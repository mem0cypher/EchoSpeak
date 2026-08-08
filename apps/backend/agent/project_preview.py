"""Project preview detection and governed preview-process lifecycle.

Preview start/stop is exposed only through governed tools. This small module
owns the process adapter and the mandatory safety stop when Session/Project
scope changes; it is not a coding runtime or UI workspace.
"""

from __future__ import annotations

import json
import hashlib
import os
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    from config import config
except Exception:  # pragma: no cover
    config = None  # type: ignore


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

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


def stop_preview_for_scope_change(
    session_id: str,
    *,
    reason: str,
    detached_project_id: str = "",
    state_store: Any = None,
) -> Dict[str, Any]:
    """Safety teardown with durable Execution/ToolRun truth.

    Project detach/delete is itself authoritative cancellation and cannot wait
    for user approval, but it still must not mutate a host process invisibly.
    """

    tid = str(session_id or "default").strip() or "default"
    manager = get_preview_manager()
    before = manager.status(tid)
    if not before.get("running"):
        return {"ok": True, "stopped": False, "reason": "preview_not_running"}
    from agent.state import ToolOutcome, get_state_store

    store = state_store or get_state_store()
    session_state = store.get_thread_state(tid)
    binding = getattr(session_state, "model_binding", None)
    execution = store.create_execution(
        kind="runtime_safety_control",
        thread_id=tid,
        source="project_scope_lifecycle",
        status="running",
        query="",
        active_project_id="",
        runtime_provider=str(getattr(binding, "provider_id", "") or ""),
        model_id=str(getattr(binding, "model_id", "") or ""),
        intent="scope_invalidation",
        mode="control",
        phase="preview_safety_stop",
        metadata={
            "reason": str(reason or "project_scope_changed")[:300],
            "detached_project_id": str(detached_project_id or ""),
            "model_binding_revision": int(getattr(binding, "binding_revision", 0) or 0),
        },
    )
    arguments = {"reason": str(reason or "project_scope_changed")[:300]}
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    run_id = str(uuid.uuid4())
    item = store.add_item(
        turn_id=execution.id,
        item_type="tool_run",
        status="started",
        payload={"tool_name": "code_preview_stop", "safety_control": True},
        session_id=tid,
        project_id=str(detached_project_id or ""),
        tool_run_id=run_id,
        model_id=execution.model_id,
    )
    store.create_tool_run(
        turn_id=execution.id,
        tool_name="code_preview_stop",
        session_id=tid,
        project_id=str(detached_project_id or ""),
        run_id=run_id,
        item_id=item.id,
        canonical_arguments=arguments,
        canonical_arguments_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        action_id=f"scope-safety:{execution.id}",
    )
    started_at = time.time()
    result = manager.stop(tid)
    success = bool(result.get("ok") and not manager.status(tid).get("running"))
    outcome = ToolOutcome(
        tool_name="code_preview_stop",
        run_id=run_id,
        action_id=f"scope-safety:{execution.id}",
        execution_id=execution.id,
        project_id=str(detached_project_id or ""),
        session_id=tid,
        turn_id=execution.id,
        success=success,
        status="success" if success else "failed",
        execution_status="success" if success else "error",
        result_state="data_found" if success else "insufficient_evidence",
        output=json.dumps(result, ensure_ascii=False),
        error_message="" if success else "Preview safety teardown did not stop the process",
        verification={
            "verified": success,
            "execution_verified": success,
            "verifier_id": "project_scope_lifecycle_v1",
            "verification_kind": "lifecycle_safety_teardown",
            "reason": str(reason or "project_scope_changed")[:300],
        },
        provider="runtime_safety_control",
        observed_at=time.time(),
        started_at=started_at,
        completed_at=time.time(),
    )
    store.finish_tool_run(run_id, outcome)
    store.update_execution(
        execution.id,
        status="completed" if success else "failed",
        success=success,
        phase="preview_safety_stopped" if success else "preview_safety_stop_failed",
        response_preview="Preview stopped because Project scope changed" if success else "Preview safety stop failed",
        error="" if success else outcome.error_message,
        verification=dict(outcome.verification),
    )
    store.update_thread_state(
        tid,
        execution_status="complete" if success else "failed",
        safest_next_action="" if success else "Close the preview process manually",
    )
    return {**result, "tool_run_id": run_id, "execution_id": execution.id}
