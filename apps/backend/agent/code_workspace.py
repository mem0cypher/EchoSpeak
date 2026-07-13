"""Project-aware Code workspace: detection, file access, preview processes.

Used by the web Code view. Never invents project activity — reads attached
Project / Session state and filesystem under the resolved project root only.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

try:
    from config import config
except Exception:  # pragma: no cover
    config = None  # type: ignore


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

_SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
    ".cache",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "target",
}


def _norm(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve()))
    except Exception:
        return os.path.normcase(str(path))


def resolve_under_root(root: Path, rel: str) -> Optional[Path]:
    """Resolve rel under root; reject escapes."""
    try:
        base = root.resolve()
    except Exception:
        return None
    raw = str(rel or "").strip().replace("\\", "/").lstrip("/")
    if not raw or raw in {".", "./"}:
        return base
    if ".." in raw.split("/"):
        return None
    try:
        target = (base / raw).resolve()
    except Exception:
        return None
    try:
        if os.path.commonpath([_norm(base), _norm(target)]) != _norm(base):
            return None
    except Exception:
        return None
    return target


def build_file_tree(
    root: Path,
    *,
    max_depth: int = 4,
    max_items: int = 400,
    relative_to: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    base = relative_to or root
    items: List[Dict[str, Any]] = []
    count = 0

    def walk(current: Path, depth: int) -> List[Dict[str, Any]]:
        nonlocal count
        nodes: List[Dict[str, Any]] = []
        if depth > max_depth or count >= max_items:
            return nodes
        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except (PermissionError, OSError):
            return nodes
        for entry in entries:
            if count >= max_items:
                break
            name = entry.name
            if name in _SKIP_DIR_NAMES:
                continue
            if name.startswith(".") and name not in {".env", ".env.example", ".gitignore"}:
                continue
            try:
                rel = str(entry.relative_to(base)).replace("\\", "/")
            except Exception:
                rel = name
            is_dir = entry.is_dir()
            node: Dict[str, Any] = {
                "name": name,
                "path": rel,
                "type": "directory" if is_dir else "file",
            }
            if not is_dir:
                try:
                    node["size"] = int(entry.stat().st_size)
                except Exception:
                    node["size"] = 0
            count += 1
            if is_dir and depth < max_depth:
                children = walk(entry, depth + 1)
                node["children"] = children
                node["item_count"] = len(children)
            elif is_dir:
                node["children"] = []
                try:
                    node["item_count"] = sum(1 for _ in entry.iterdir())
                except Exception:
                    node["item_count"] = 0
            nodes.append(node)
        return nodes

    try:
        if root.is_dir():
            items = walk(root, 0)
    except Exception as exc:
        logger.warning("[CodeWorkspace] tree build failed: {}", exc)
    return items


def read_text_file(path: Path, *, max_chars: int = 400_000) -> Tuple[str, Dict[str, Any]]:
    meta: Dict[str, Any] = {
        "path": str(path),
        "size": 0,
        "mtime": 0.0,
        "truncated": False,
        "binary": False,
        "encoding": "utf-8",
    }
    try:
        st = path.stat()
        meta["size"] = int(st.st_size)
        meta["mtime"] = float(st.st_mtime)
    except Exception:
        pass
    try:
        raw = path.read_bytes()
    except Exception as exc:
        raise OSError(f"Failed to read file: {exc}") from exc
    if b"\x00" in raw[:8192]:
        meta["binary"] = True
        return "", meta
    text = raw.decode("utf-8", errors="replace")
    if max_chars > 0 and len(text) > max_chars:
        meta["truncated"] = True
        text = text[:max_chars]
    return text, meta


# ---------------------------------------------------------------------------
# Project detection (plug-and-play, not framework-hardcoded for EchoSpeak)
# ---------------------------------------------------------------------------

@dataclass
class ProjectDetection:
    kind: str = "unknown"
    label: str = "Unknown project"
    entrypoints: List[str] = field(default_factory=list)
    preview_available: bool = False
    preview_strategy: str = ""  # static_http | node_script | none
    preview_command: str = ""
    preview_cwd_rel: str = "."
    preview_entry: str = ""
    run_command_hint: str = ""
    reason: str = ""
    signals: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _exists(root: Path, *parts: str) -> bool:
    return (root.joinpath(*parts)).exists()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def detect_project(root: Path) -> ProjectDetection:
    """Inspect project root and decide how (if) it can be previewed/run."""
    if not root.exists() or not root.is_dir():
        return ProjectDetection(
            kind="missing",
            label="Missing project folder",
            reason="Project root does not exist on disk.",
        )

    signals: List[str] = []
    names = {p.name.lower() for p in root.iterdir()} if root.is_dir() else set()

    # --- Static / game HTML ---
    html_candidates: List[str] = []
    seen_html: set[str] = set()
    for candidate in ("index.html", "game.html", "main.html", "app.html"):
        p = root / candidate
        if not p.is_file():
            continue
        key = candidate.lower()
        if key in seen_html:
            continue
        seen_html.add(key)
        # Preserve on-disk name
        html_candidates.append(p.name)
    if not html_candidates:
        for sub in ("public", "dist", "build", "www", "docs"):
            idx = root / sub / "index.html"
            if idx.is_file():
                html_candidates.append(f"{sub}/index.html")
                break

    package = _read_json(root / "package.json") if (root / "package.json").is_file() else None
    has_py = any(
        (root / n).is_file()
        for n in ("requirements.txt", "pyproject.toml", "setup.py", "Pipfile")
    )
    has_cargo = (root / "Cargo.toml").is_file()
    has_go = (root / "go.mod").is_file()
    has_cs = any(root.glob("*.csproj")) or (root / "Program.cs").is_file()

    # Prefer node when package.json has a real preview/start/dev and no simple index at root
    if isinstance(package, dict):
        signals.append("package.json")
        scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
        script_name = ""
        for key in ("preview", "start", "dev", "serve"):
            if key in scripts and str(scripts.get(key) or "").strip():
                script_name = key
                break
        # Pure static: package.json present but root index.html is the app (common games)
        if html_candidates and html_candidates[0].lower() == "index.html" and not script_name:
            return ProjectDetection(
                kind="static_web",
                label="Static web / HTML project",
                entrypoints=html_candidates[:5],
                preview_available=True,
                preview_strategy="static_http",
                preview_command="python -m http.server",
                preview_entry=html_candidates[0],
                run_command_hint="Open index.html via local static server",
                reason="Found index.html — can serve the project folder as a local site.",
                signals=signals + ["index.html"],
            )
        if script_name:
            return ProjectDetection(
                kind="node",
                label=f"Node project (npm run {script_name})",
                entrypoints=["package.json"] + html_candidates[:3],
                preview_available=True,
                preview_strategy="node_script",
                preview_command=f"npm run {script_name}",
                preview_entry=html_candidates[0] if html_candidates else "",
                run_command_hint=f"npm run {script_name}",
                reason=f"Detected package.json script '{script_name}'. Preview launches that script.",
                signals=signals + [f"script:{script_name}"],
            )
        if html_candidates:
            return ProjectDetection(
                kind="static_web",
                label="Static web project",
                entrypoints=html_candidates[:5],
                preview_available=True,
                preview_strategy="static_http",
                preview_command="python -m http.server",
                preview_entry=html_candidates[0],
                run_command_hint="Serve project folder with a static HTTP server",
                reason="HTML entry found; serving project root.",
                signals=signals + html_candidates[:1],
            )
        return ProjectDetection(
            kind="node",
            label="Node project",
            entrypoints=["package.json"],
            preview_available=False,
            preview_strategy="none",
            run_command_hint="npm install && npm start",
            reason="package.json found but no preview/start/dev script and no HTML entry to serve.",
            signals=signals,
        )

    if html_candidates:
        entry = html_candidates[0]
        serve_cwd = "."
        serve_entry = entry
        if "/" in entry:
            # e.g. public/index.html → serve from public
            parts = entry.split("/", 1)
            serve_cwd = parts[0]
            serve_entry = "index.html"
        return ProjectDetection(
            kind="static_web",
            label="Static web / HTML project",
            entrypoints=html_candidates[:5],
            preview_available=True,
            preview_strategy="static_http",
            preview_command="python -m http.server",
            preview_cwd_rel=serve_cwd,
            preview_entry=serve_entry if serve_cwd == "." else entry,
            run_command_hint="Local static HTTP server",
            reason=f"Found {entry} — can serve and embed a live preview.",
            signals=["html"] + html_candidates[:2],
        )

    if has_py or any(n.endswith(".py") for n in names):
        signals.append("python")
        entry = ""
        for n in ("app.py", "main.py", "manage.py", "server.py", "wsgi.py", "asgi.py"):
            if (root / n).is_file():
                entry = n
                break
        return ProjectDetection(
            kind="python",
            label="Python project",
            entrypoints=[entry] if entry else [n for n in names if n.endswith(".py")][:5],
            preview_available=False,
            preview_strategy="none",
            run_command_hint=f"python {entry}" if entry else "python main.py",
            reason=(
                "Python project detected, but no automatic visual preview is available "
                "(web frameworks need an explicit run command and may require deps). "
                "Use Terminal and Files to inspect and run."
            ),
            signals=signals,
        )

    if has_cargo:
        return ProjectDetection(
            kind="rust",
            label="Rust project",
            entrypoints=["Cargo.toml"],
            preview_available=False,
            preview_strategy="none",
            run_command_hint="cargo run",
            reason="Rust project detected. No built-in visual preview — use Terminal for cargo commands.",
            signals=["Cargo.toml"],
        )

    if has_go:
        return ProjectDetection(
            kind="go",
            label="Go project",
            entrypoints=["go.mod"],
            preview_available=False,
            preview_strategy="none",
            run_command_hint="go run .",
            reason="Go project detected. No built-in visual preview — use Terminal for go commands.",
            signals=["go.mod"],
        )

    if has_cs:
        return ProjectDetection(
            kind="dotnet",
            label=".NET project",
            entrypoints=[p.name for p in root.glob("*.csproj")][:3],
            preview_available=False,
            preview_strategy="none",
            run_command_hint="dotnet run",
            reason=".NET project detected. No automatic visual preview.",
            signals=["csproj"],
        )

    # Generic source tree
    codeish = [n for n in sorted(names) if Path(n).suffix.lower() in {
        ".js", ".ts", ".tsx", ".jsx", ".py", ".css", ".html", ".rs", ".go", ".java", ".c", ".cpp", ".h"
    }]
    return ProjectDetection(
        kind="source",
        label="Source project",
        entrypoints=codeish[:8],
        preview_available=False,
        preview_strategy="none",
        reason=(
            "No known launch/preview entry (index.html, package.json scripts, etc.). "
            "Files, Terminal, and Changes still work against this folder."
        ),
        signals=["generic"],
    )


# ---------------------------------------------------------------------------
# Preview process manager (session-scoped, project-bound)
# ---------------------------------------------------------------------------

@dataclass
class PreviewProcess:
    session_id: str
    project_root: str
    strategy: str
    command: str
    port: int
    url: str
    pid: int
    started_at: float
    status: str = "running"  # running | stopped | failed
    error: str = ""
    entry: str = ""
    _proc: Any = field(default=None, repr=False)


class PreviewManager:
    """One preview process per session. Never launches outside project root."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_session: Dict[str, PreviewProcess] = {}

    def status(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            rec = self._by_session.get(session_id)
            if not rec:
                return {"running": False, "status": "idle", "url": "", "port": 0, "error": "", "command": "", "entry": ""}
            alive = False
            try:
                alive = rec._proc is not None and rec._proc.poll() is None
            except Exception:
                alive = False
            if rec.status == "running" and not alive:
                rec.status = "stopped"
            return {
                "running": bool(alive and rec.status == "running"),
                "status": rec.status if alive else ("stopped" if rec.status == "running" else rec.status),
                "url": rec.url if alive else "",
                "port": rec.port if alive else 0,
                "error": rec.error,
                "command": rec.command,
                "entry": rec.entry,
                "pid": rec.pid if alive else 0,
                "project_root": rec.project_root,
                "started_at": rec.started_at,
            }

    def stop(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            rec = self._by_session.pop(session_id, None)
            if not rec:
                return {"ok": True, "stopped": False, "message": "No preview process for this session."}
            try:
                if rec._proc and rec._proc.poll() is None:
                    rec._proc.terminate()
                    try:
                        rec._proc.wait(timeout=3)
                    except Exception:
                        rec._proc.kill()
            except Exception as exc:
                logger.warning("[Preview] stop failed: {}", exc)
            return {"ok": True, "stopped": True, "message": "Preview stopped."}

    def _free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    def start(self, session_id: str, root: Path, detection: ProjectDetection) -> Dict[str, Any]:
        session_id = str(session_id or "default").strip() or "default"
        if not detection.preview_available:
            return {
                "ok": False,
                "running": False,
                "status": "unavailable",
                "error": detection.reason or "This project cannot be previewed automatically.",
                "url": "",
                "port": 0,
                "command": "",
            }
        if detection.preview_strategy != "static_http":
            return {
                "ok": False,
                "running": False,
                "status": "approval_required",
                "error": (
                    "Automatic preview does not execute package scripts on the host. "
                    "Run the suggested command through the approval-gated terminal sandbox."
                ),
                "url": "",
                "port": 0,
                "command": detection.run_command_hint or detection.preview_command,
            }
        with self._lock:
            # Restart if already running for this session
            existing = self._by_session.get(session_id)
            if existing:
                try:
                    if existing._proc and existing._proc.poll() is None:
                        existing._proc.terminate()
                        try:
                            existing._proc.wait(timeout=2)
                        except Exception:
                            existing._proc.kill()
                except Exception:
                    pass
                self._by_session.pop(session_id, None)

            port = self._free_port()
            cwd = root
            rel_cwd = str(detection.preview_cwd_rel or ".").strip() or "."
            if rel_cwd not in {".", "./"}:
                candidate = resolve_under_root(root, rel_cwd)
                if candidate and candidate.is_dir():
                    cwd = candidate

            cmd: List[str]
            url = f"http://127.0.0.1:{port}/"
            entry = detection.preview_entry or ""
            if entry and not entry.lower().endswith("index.html") and "/" not in entry.replace("\\", "/"):
                # e.g. game.html
                url = f"http://127.0.0.1:{port}/{entry}"

            strategy = detection.preview_strategy
            if strategy == "static_http":
                cmd = [
                    os.environ.get("PYTHON", "") or ("py" if os.name == "nt" else "python3"),
                    "-m",
                    "http.server",
                    str(port),
                    "--bind",
                    "127.0.0.1",
                ]
                # Prefer sys.executable
                import sys

                cmd[0] = sys.executable
                display_cmd = f"{cmd[0]} -m http.server {port} --bind 127.0.0.1"
            elif strategy == "node_script":
                # npm run X — inject PORT when possible
                script = detection.preview_command.replace("npm run ", "").strip()
                env_port = str(port)
                if os.name == "nt":
                    cmd = ["cmd", "/c", f"set PORT={env_port}&& set HOST=127.0.0.1&& npm run {script}"]
                    display_cmd = f"npm run {script} (PORT={port})"
                else:
                    cmd = ["npm", "run", script]
                    display_cmd = f"PORT={port} npm run {script}"
            else:
                return {
                    "ok": False,
                    "running": False,
                    "status": "unavailable",
                    "error": f"Unknown preview strategy: {strategy}",
                    "url": "",
                    "port": 0,
                    "command": "",
                }

            env = os.environ.copy()
            env["PORT"] = str(port)
            env["HOST"] = "127.0.0.1"
            env["BROWSER"] = "none"

            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(cwd),
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
            except Exception as exc:
                return {
                    "ok": False,
                    "running": False,
                    "status": "failed",
                    "error": f"Failed to start preview: {exc}",
                    "url": "",
                    "port": 0,
                    "command": display_cmd,
                }

            # Brief readiness wait
            time.sleep(0.35)
            if proc.poll() is not None:
                err = ""
                try:
                    err = (proc.stderr.read() or "")[:500] if proc.stderr else ""
                except Exception:
                    err = ""
                return {
                    "ok": False,
                    "running": False,
                    "status": "failed",
                    "error": err or f"Preview process exited immediately (code {proc.returncode}).",
                    "url": "",
                    "port": 0,
                    "command": display_cmd,
                }

            rec = PreviewProcess(
                session_id=session_id,
                project_root=_norm(root),
                strategy=strategy,
                command=display_cmd,
                port=port,
                url=url,
                pid=int(proc.pid or 0),
                started_at=time.time(),
                status="running",
                entry=entry,
                _proc=proc,
            )
            self._by_session[session_id] = rec
            logger.info("[Preview] session={} strategy={} port={} cwd={}", session_id, strategy, port, cwd)
            return {
                "ok": True,
                "running": True,
                "status": "running",
                "url": url,
                "port": port,
                "command": display_cmd,
                "entry": entry,
                "error": "",
                "pid": rec.pid,
                "project_root": rec.project_root,
            }


_PREVIEW_MANAGER: Optional[PreviewManager] = None
_PREVIEW_LOCK = threading.Lock()


def get_preview_manager() -> PreviewManager:
    global _PREVIEW_MANAGER
    with _PREVIEW_LOCK:
        if _PREVIEW_MANAGER is None:
            _PREVIEW_MANAGER = PreviewManager()
        return _PREVIEW_MANAGER


# ---------------------------------------------------------------------------
# Session resolution helpers
# ---------------------------------------------------------------------------

def resolve_project_context(thread_id: str) -> Dict[str, Any]:
    """Resolve attached project root from Session + Project store."""
    from agent.state import get_state_store
    from agent.projects import get_project_manager

    tid = str(thread_id or "default").strip() or "default"
    store = get_state_store()
    state = store.get_thread_state(tid)
    pm = get_project_manager()

    project_id = str(state.active_project_id or "").strip()
    project = pm.get_project(project_id) if project_id else None

    root_str = ""
    # Authority: only an attached Project (active_project_id) grants Code filesystem scope.
    # Path-only "soft projects" without a Project id must not keep FS/preview authority.
    if project_id and project and project.workspace_root:
        root_str = str(project.workspace_root).strip()

    root: Optional[Path] = None
    if root_str:
        try:
            root = Path(root_str).expanduser().resolve()
        except Exception:
            root = Path(root_str)

    project_name = ""
    if project:
        project_name = project.name
    elif root is not None:
        project_name = root.name

    return {
        "thread_id": tid,
        "session_id": tid,
        "project_id": project_id,
        "project_name": project_name,
        "project": project.model_dump() if project else None,
        "root": str(root) if root else "",
        "root_exists": bool(root and root.exists() and root.is_dir()),
        "mode": str(state.mode or ""),
        "phase": str(state.phase or ""),
        "objective": str(state.objective or ""),
        "current_subject": str(state.current_subject or ""),
        "execution_status": str(state.execution_status or ""),
        "active_turn_id": str(state.active_turn_id or state.current_execution_id or ""),
        "last_execution_id": str(state.last_execution_id or ""),
        "thread_state": state.model_dump(),
    }


def parse_terminal_output(output: str) -> Dict[str, Any]:
    text = str(output or "")
    exit_code = None
    status = ""
    mode = ""
    reason = ""
    m = re.search(r"ExitCode\s*=\s*(-?\d+)", text, re.I)
    if m:
        try:
            exit_code = int(m.group(1))
        except Exception:
            exit_code = None
    m = re.search(r"Status\s*=\s*(\S+)", text, re.I)
    if m:
        status = m.group(1).strip()
    m = re.search(r"Mode\s*=\s*(\S+)", text, re.I)
    if m:
        mode = m.group(1).strip()
    m = re.search(r"Reason\s*=\s*(.+)", text, re.I)
    if m:
        reason = m.group(1).strip()
    # Body after header lines
    body_lines = []
    header_done = False
    for line in text.splitlines():
        if not header_done and re.match(r"^(ExitCode|Status|Mode|Reason|DurationMs)\s*=", line, re.I):
            continue
        header_done = True
        body_lines.append(line)
    body = "\n".join(body_lines).strip("\n")
    return {
        "exit_code": exit_code,
        "status": status or ("pass" if exit_code == 0 else ("fail" if exit_code is not None else "")),
        "mode": mode,
        "reason": reason,
        "body": body,
        "raw": text,
    }


def extract_command_from_args(args: Any) -> str:
    if isinstance(args, dict):
        for key in ("command", "cmd", "input"):
            val = args.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
    if isinstance(args, str) and args.strip():
        # try JSON
        try:
            data = json.loads(args)
            if isinstance(data, dict):
                return extract_command_from_args(data)
        except Exception:
            pass
        m = re.search(r"['\"]command['\"]\s*:\s*['\"]([^'\"]+)['\"]", args)
        if m:
            return m.group(1)
        return args.strip()[:500]
    return ""


def extract_path_from_args(args: Any) -> str:
    if isinstance(args, dict):
        for key in ("path", "file", "src", "dst"):
            val = args.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
    if isinstance(args, str) and args.strip():
        try:
            data = json.loads(args)
            if isinstance(data, dict):
                return extract_path_from_args(data)
        except Exception:
            pass
        m = re.search(r"['\"]path['\"]\s*:\s*['\"]([^'\"]+)['\"]", args)
        if m:
            return m.group(1)
    return ""


FILE_CHANGE_TOOLS = frozenset({
    "file_write",
    "file_read",
    "file_delete",
    "file_move",
    "file_copy",
    "file_mkdir",
    "artifact_write",
    "notepad_write",
    "checkpoint_undo",
})

TERMINAL_TOOLS = frozenset({"terminal_run"})


def build_session_activity(thread_id: str, *, limit: int = 80) -> Dict[str, Any]:
    """Terminal runs + file changes from ToolRuns / ledger for this session only."""
    from agent.state import get_state_store

    tid = str(thread_id or "default").strip() or "default"
    store = get_state_store()
    runs = store.list_tool_runs_for_session(tid, limit=max(limit * 3, 120))
    state = store.get_thread_state(tid)

    terminal: List[Dict[str, Any]] = []
    changes: List[Dict[str, Any]] = []

    for run in runs:
        name = str(run.tool_name or "")
        outcome = run.outcome if isinstance(run.outcome, dict) else {}
        output = str(outcome.get("output") or "")
        success = bool(outcome.get("success")) if "success" in outcome else (run.status in {"complete", "completed", "pass"})
        args = run.canonical_arguments if isinstance(run.canonical_arguments, dict) else {}
        verification = run.verification if isinstance(run.verification, dict) else (outcome.get("verification") or {})

        if name in TERMINAL_TOOLS:
            parsed = parse_terminal_output(output)
            terminal.append({
                "id": run.id,
                "tool_run_id": run.id,
                "turn_id": run.turn_id,
                "tool_name": name,
                "command": extract_command_from_args(args) or extract_command_from_args(output),
                "cwd": str(args.get("cwd") or ".") if isinstance(args, dict) else ".",
                "status": parsed.get("status") or run.status,
                "exit_code": parsed.get("exit_code"),
                "mode": parsed.get("mode") or "",
                "reason": parsed.get("reason") or "",
                "output": parsed.get("body") or output,
                "raw_output": output,
                "running": False,  # durable records are finished
                "success": success,
                "verification": verification,
                "created_at": run.created_at,
                "completed_at": run.completed_at,
            })
        elif name in FILE_CHANGE_TOOLS and name != "file_read" and success:
            path = extract_path_from_args(args)
            if not path and output:
                m = re.search(r"(?:Wrote|Appended|Read|Deleted|Moved|Copied).+?(?:to|from)\s+(.+?)(?:\n|$)", output, re.I)
                if m:
                    path = m.group(1).strip()
            action = "inspect" if name == "file_read" else "modify"
            if name == "file_delete":
                action = "delete"
            elif name in {"file_move", "file_copy"}:
                action = name.replace("file_", "")
            elif name == "file_mkdir":
                action = "mkdir"
            changes.append({
                "id": run.id,
                "tool_run_id": run.id,
                "turn_id": run.turn_id,
                "tool_name": name,
                "path": path,
                "action": action,
                "status": run.status,
                "success": success,
                "summary": (output.split("\n")[0] if output else name)[:200],
                "output_preview": output[:400],
                "verification": verification,
                "verified": bool(verification.get("verified") or verification.get("ok")),
                "created_at": run.created_at,
                "completed_at": run.completed_at,
            })

    # Checkpoints as change provenance (scoped)
    checkpoints: List[Dict[str, Any]] = []
    try:
        from agent.checkpoints import _load_index

        for entry in reversed(_load_index()):
            if str(entry.get("thread_id") or "legacy") not in {tid, "legacy"}:
                continue
            checkpoints.append({
                "timestamp": entry.get("timestamp"),
                "original_path": entry.get("original_path"),
                "filename": entry.get("filename"),
                "reason": entry.get("reason"),
                "execution_id": entry.get("execution_id"),
                "project_root": entry.get("project_root"),
            })
            if len(checkpoints) >= 30:
                break
    except Exception:
        pass

    ledger = []
    for entry in reversed(list(state.ledger or [])):
        if hasattr(entry, "model_dump"):
            ledger.append(entry.model_dump())
        elif isinstance(entry, dict):
            ledger.append(entry)
        if len(ledger) >= 40:
            break

    return {
        "thread_id": tid,
        "terminal": terminal[:limit],
        "changes": changes[:limit],
        "checkpoints": checkpoints,
        "ledger": ledger,
        "active_turn_id": str(state.active_turn_id or state.current_execution_id or ""),
    }
