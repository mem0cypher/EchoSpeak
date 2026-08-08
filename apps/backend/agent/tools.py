"""
Tools module for Echo Speak.
Provides custom tools for web search, screen analysis, and other utilities.
"""

import os
import sys
import base64
import json
import subprocess
import shutil
import platform
import re
import time
import hashlib
import uuid
from contextvars import ContextVar, Token
from datetime import datetime, timezone, timedelta
from io import BytesIO
from typing import Optional, Dict, Any, Literal
from pathlib import Path
from urllib.parse import urlparse
from loguru import logger
from pydantic import BaseModel, Field, AliasChoices

from langchain_core.tools import tool
from pytesseract import pytesseract

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config, ModelProvider, DATA_DIR


_tool_execution_context: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "echospeak_tool_execution_context",
    default=None,
)


def bind_tool_execution_context(context: Dict[str, Any]) -> Token:
    """Bind one request's authority and filesystem scope to tool execution."""
    payload = dict(context or {})
    payload["allowed_tool_names"] = list(dict.fromkeys(payload.get("allowed_tool_names") or []))
    payload["permissions"] = dict(payload.get("permissions") or {})
    payload["enforce_tools"] = True
    payload["strict_scope"] = True
    return _tool_execution_context.set(payload)


def update_tool_execution_context(**updates: Any) -> Dict[str, Any]:
    payload = dict(_tool_execution_context.get() or {})
    payload.update(updates)
    _tool_execution_context.set(payload)
    return dict(payload)


def reset_tool_execution_context(token: Optional[Token]) -> None:
    if token is not None:
        _tool_execution_context.reset(token)


def get_tool_execution_context() -> Dict[str, Any]:
    return dict(_tool_execution_context.get() or {})


def _tool_scope_denial(tool_name: str) -> str:
    context = _tool_execution_context.get() or {}
    if not context or not context.get("enforce_tools"):
        return f"Tool '{tool_name}' is blocked outside a bound thread execution context."
    allowed = {str(name) for name in (context.get("allowed_tool_names") or []) if str(name)}
    if str(tool_name or "") not in allowed:
        thread_id = str(context.get("thread_id") or "default")
        return f"Tool '{tool_name}' is not allowed by thread context '{thread_id}'."
    return ""


def _ensure_playwright_browsers() -> bool:
    """Ensure Playwright browsers are installed. Returns True if browsers are available."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            # Check if chromium executable exists
            exec_path = p.chromium.executable_path
            if exec_path and Path(exec_path).exists():
                return True
    except Exception:
        pass
    
    # Try to install browsers
    logger.info("Playwright browsers missing, attempting auto-install...")
    try:
        venv_python = Path(__file__).parent.parent / ".venv" / "bin" / "python"
        if venv_python.exists():
            result = subprocess.run([str(venv_python), "-m", "playwright", "install", "chromium"], 
                                    capture_output=True, timeout=300)
        else:
            result = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                                    capture_output=True, timeout=300)
        if result.returncode == 0:
            logger.info("Playwright browsers installed successfully")
            return True
    except Exception as e:
        logger.warning(f"Failed to auto-install Playwright browsers: {e}")
    return False


def _with_playwright_retry(func, *args, **kwargs):
    """Execute a Playwright function with auto-retry if browsers are missing."""
    try:
        return func(*args, **kwargs)
    except Exception as e:
        error_msg = str(e)
        if "Executable doesn't exist" in error_msg or "playwright install" in error_msg:
            logger.info("Playwright browser missing, attempting auto-install and retry...")
            if _ensure_playwright_browsers():
                # Retry after installing
                try:
                    return func(*args, **kwargs)
                except Exception as retry_e:
                    return f"Playwright error after retry: {retry_e}"
            else:
                return f"Playwright browsers not available. Run: playwright install chromium"
        raise

try:
    from io_module.vision import capture_screen, perform_ocr
    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False
    logger.warning("Vision module not available")


try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("OpenCV not available")


def _desktop_automation_enabled() -> bool:
    return bool(
        os.name == "nt"
        and getattr(config, "enable_system_actions", False)
        and getattr(config, "allow_desktop_automation", False)
    )


def _require_desktop_automation() -> Optional[str]:
    if os.name != "nt":
        return "Desktop automation is only supported on Windows."
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_desktop_automation", False):
        return "System actions are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_DESKTOP_AUTOMATION=true, then restart the API."
    return None


def _safe_rect_str(rect: object) -> str:
    try:
        left = getattr(rect, "left", None)
        top = getattr(rect, "top", None)
        right = getattr(rect, "right", None)
        bottom = getattr(rect, "bottom", None)
        return f"({left},{top})-({right},{bottom})"
    except Exception:
        return ""


def _rect_center(rect: object) -> tuple[int, int]:
    try:
        left = int(getattr(rect, "left", 0) or 0)
        top = int(getattr(rect, "top", 0) or 0)
        right = int(getattr(rect, "right", 0) or 0)
        bottom = int(getattr(rect, "bottom", 0) or 0)
        return int((left + right) / 2), int((top + bottom) / 2)
    except Exception:
        return 0, 0


def _get_desktop_backend():
    try:
        from pywinauto import Desktop  # type: ignore

        return Desktop
    except Exception:
        return None


def _file_tool_root() -> Path:
    configured = str(getattr(config, "file_tool_root", "") or "").strip()
    if configured:
        root = Path(configured).expanduser()
    else:
        root = Path(__file__).resolve().parents[3]
    try:
        return root.resolve()
    except Exception:
        return Path(".").resolve()


def _file_tool_extra_roots() -> list[Path]:
    roots: list[Path] = []
    raw = getattr(config, "file_tool_extra_roots", []) or []
    if isinstance(raw, str):
        raw = raw.replace("\n", ";").replace(",", ";").split(";")
    for item in raw:
        try:
            val = str(item or "").strip()
            if not val:
                continue
            p = Path(val).expanduser().resolve()
            if p not in roots:
                roots.append(p)
        except Exception:
            continue
    return roots


def _file_tool_roots() -> list[Path]:
    context = _tool_execution_context.get() or {}
    if context.get("strict_scope"):
        scoped = str(context.get("project_root") or context.get("workspace_root") or "").strip()
        if scoped:
            try:
                return [Path(scoped).expanduser().resolve()]
            except Exception:
                return []
        # FILE_TOOL_ROOT is an authority ceiling, not an implicitly attached
        # Project. A Session without an explicit root has no filesystem scope.
        return []
    roots = [_file_tool_root()]
    # Always allow user Desktop (common coding destination)
    try:
        desk = _desktop_root()
        if desk not in roots:
            roots.append(desk)
    except Exception:
        pass
    for root in _file_tool_extra_roots():
        if root not in roots:
            roots.append(root)
    # Active coding project (may be under Desktop or a discovered path)
    try:
        active_project_root = get_active_project_root()
        if active_project_root is not None:
            ap = active_project_root.resolve()
            # Allow the project dir itself and its parent (for relative resolves)
            if ap not in roots:
                roots.append(ap)
            parent = ap.parent
            if parent not in roots:
                roots.append(parent)
    except Exception:
        pass
    return roots


def _desktop_root() -> Path:
    return (Path.home() / "Desktop").expanduser().resolve()


# Active coding project root — relative paths like "index.html" resolve here
# instead of the EchoSpeak repo (live bug: white-screen write to EchoSpeak/index.html).
def set_active_project_root(path: Optional[str]) -> None:
    """Set a request-local project root without mutating process-wide scope.

    Never opens legacy broad roots (strict_scope=False) when a real thread is bound.
    Unbound pins only set project_root and keep enforcement defaults from the active context.
    """
    raw = str(path or "").strip()
    context = dict(_tool_execution_context.get() or {})
    if not raw:
        context["project_root"] = ""
        _tool_execution_context.set(context)
        return
    try:
        p = _candidate_file_path(raw, _file_tool_root()).resolve()
        context["project_root"] = str(p)
    except Exception:
        try:
            context["project_root"] = str(Path(raw).expanduser().resolve())
        except Exception:
            context["project_root"] = ""
    # Do not disable strict_scope / enforce_tools — that reopened Desktop/parent roots.
    if not context.get("thread_id"):
        context["thread_id"] = str(context.get("session_id") or "default")
    if "strict_scope" not in context:
        context["strict_scope"] = True
    if "enforce_tools" not in context:
        context["enforce_tools"] = True
    _tool_execution_context.set(context)


def get_active_project_root() -> Optional[Path]:
    raw = str((_tool_execution_context.get() or {}).get("project_root") or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except Exception:
        return None


def _candidate_file_path(path: str, root: Path) -> Path:
    raw = str(path or "").strip().strip("\"'")
    low = raw.replace("\\", "/").lower()
    if low in {"desktop", "~/desktop", "%userprofile%/desktop"}:
        return _desktop_root()
    for prefix in ("desktop/", "~/desktop/", "%userprofile%/desktop/"):
        if low.startswith(prefix):
            suffix = raw.replace("\\", "/")[len(prefix) :]
            return _desktop_root() / suffix
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate
    # Bare relative paths during coding → project folder, not EchoSpeak repo root
    proj = get_active_project_root()
    if proj is not None and not low.startswith(("desktop/", "apps/", "src/")):
        try:
            if proj.exists() or True:
                return proj / raw
        except Exception:
            pass
    candidate = root / raw
    return candidate


def _safe_file_path(path: str) -> Optional[Path]:
    if not path:
        return None
    root = _file_tool_root()
    candidate = _candidate_file_path(path, root)
    try:
        resolved = candidate.expanduser().resolve()
    except Exception:
        return None
    # Windows: compare case-insensitively and with normalized separators so
    # C:/Users/.../game.js and C:\Users\...\game.js under the same project root both pass.
    for allowed_root in _file_tool_roots():
        try:
            allowed_resolved = Path(allowed_root).expanduser().resolve()
        except Exception:
            continue
        try:
            resolved.relative_to(allowed_resolved)
            return resolved
        except ValueError:
            pass
        # Case-insensitive containment (Windows paths)
        try:
            r_parts = [p.casefold() for p in resolved.parts]
            a_parts = [p.casefold() for p in allowed_resolved.parts]
            if len(r_parts) >= len(a_parts) and r_parts[: len(a_parts)] == a_parts:
                return resolved
        except Exception:
            continue
        # Fallback: normalized commonpath with casefold string equality
        try:
            common = os.path.commonpath([str(allowed_resolved), str(resolved)])
            if common.casefold().replace("\\", "/") == str(allowed_resolved).casefold().replace("\\", "/"):
                return resolved
        except Exception:
            continue
    return None


def _mutation_path_version(target: Path, argument: str) -> Dict[str, Any]:
    """Bounded, deterministic identity used at the actual mutation boundary."""
    target = target.expanduser().resolve(strict=False)
    item: Dict[str, Any] = {"argument": argument, "path": str(target), "exists": target.exists()}
    if not item["exists"]:
        item.update({"kind": "missing", "sha256": "", "size": 0})
        return item
    st = target.lstat()
    if int(getattr(st, "st_file_attributes", 0) or 0) & 0x400:
        raise ValueError(f"Reparse-point paths cannot be approval-versioned safely: {target}")
    if target.is_symlink():
        raise ValueError(f"Symlink paths cannot be approval-versioned safely: {target}")
    if target.is_file():
        digest = hashlib.sha256()
        size = 0
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
                if size > 1024 * 1024 * 1024:
                    raise ValueError(f"File exceeds approval-version bound: {target}")
        item.update({"kind": "file", "sha256": digest.hexdigest(), "size": size})
        return item
    if target.is_dir():
        digest = hashlib.sha256()
        count = 0
        total = 0
        for root, dirs, files in os.walk(target, followlinks=False):
            dirs.sort(key=str.casefold)
            files.sort(key=str.casefold)
            root_path = Path(root)
            for name in [*dirs, *files]:
                child = root_path / name
                child_st = child.lstat()
                if int(getattr(child_st, "st_file_attributes", 0) or 0) & 0x400 or child.is_symlink():
                    raise ValueError(f"Directory contains a reparse point: {child}")
                rel = child.relative_to(target).as_posix().casefold()
                digest.update(rel.encode("utf-8", errors="surrogatepass"))
                if child.is_file():
                    digest.update(b"F")
                    with child.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                            total += len(chunk)
                            if total > 1024 * 1024 * 1024:
                                raise ValueError(f"Directory exceeds approval-version byte bound: {target}")
                else:
                    digest.update(b"D")
                count += 1
                if count > 20000:
                    raise ValueError(f"Directory exceeds approval-version entry bound: {target}")
        item.update({"kind": "directory", "sha256": digest.hexdigest(), "size": count})
        return item
    raise ValueError(f"Unsupported filesystem object for approval: {target}")


def _mutation_precondition_denial(tool_name: str) -> str:
    governed = {
        "file_write", "file_delete", "file_move", "file_copy", "file_mkdir",
        "artifact_write", "notepad_write", "checkpoint_undo",
    }
    if tool_name not in governed:
        return ""
    context = _tool_execution_context.get() or {}
    expected = dict(context.get("mutation_precondition") or {})
    if int(expected.get("version") or 0) < 2:
        return "Mutation blocked: no versioned approval precondition reached the tool boundary."
    try:
        current_entries = [
            _mutation_path_version(Path(str(entry.get("path") or "")), str(entry.get("argument") or "path"))
            for entry in list(expected.get("entries") or [])
        ]
        # Content identity only. Approval freeze metadata (tool, path_basename,
        # original_input_sha256, …) may sit on the same dict for audit identity
        # but must not invalidate an unchanged filesystem source.
        current: Dict[str, Any] = {"version": 2, "entries": current_entries}
        expected_identity: Dict[str, Any] = {
            "version": 2,
            "entries": list(expected.get("entries") or []),
        }
        if "checkpoint" in expected:
            from agent.checkpoints import get_last_checkpoint
            checkpoint = get_last_checkpoint(
                str(context.get("thread_id") or "default"), str(context.get("project_root") or "")
            )
            current["checkpoint"] = None if checkpoint is None else {
                key: checkpoint.get(key) for key in ("timestamp", "original_path", "backup_path")
            }
            expected_identity["checkpoint"] = expected.get("checkpoint")
        if json.dumps(current, sort_keys=True, separators=(",", ":")) != json.dumps(
            expected_identity, sort_keys=True, separators=(",", ":")
        ):
            return "Mutation blocked: source or destination changed after approval."
    except Exception as exc:
        return f"Mutation blocked: precondition could not be checked completely ({exc})."
    return ""


def _format_file_tool_roots() -> str:
    return "; ".join(str(p) for p in _file_tool_roots())


def _run_wmic(args: list[str]) -> list[str]:
    try:
        output = subprocess.check_output(
            ["wmic", *args],
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except Exception:
        return []
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return []
    header = lines[0].lower()
    if header.startswith("name") or header.startswith("totalphysicalmemory"):
        lines = lines[1:]
    return [line for line in lines if line]


def _format_gb(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value:.1f} GB"


def _collect_system_info() -> dict[str, str]:
    info: dict[str, str] = {}
    info["os"] = platform.platform()

    cpu = ""
    ram_gb: Optional[float] = None
    gpus: list[str] = []

    if os.name == "nt":
        cpu_lines = _run_wmic(["cpu", "get", "Name"])
        if cpu_lines:
            cpu = cpu_lines[0]

        gpu_lines = _run_wmic(["path", "win32_videocontroller", "get", "Name"])
        gpus = [line for line in gpu_lines if line]

        mem_lines = _run_wmic(["computersystem", "get", "TotalPhysicalMemory"])
        if mem_lines:
            try:
                mem_bytes = float(mem_lines[0])
                ram_gb = mem_bytes / (1024 ** 3)
            except Exception:
                ram_gb = None
    else:
        cpu = platform.processor() or platform.machine()
        try:
            import psutil  # type: ignore

            mem = psutil.virtual_memory()
            ram_gb = float(mem.total) / (1024 ** 3)
        except Exception:
            ram_gb = None

    if not cpu:
        cpu = os.environ.get("PROCESSOR_IDENTIFIER", "")

    if cpu:
        info["cpu"] = cpu
    if gpus:
        info["gpu"] = ", ".join(gpus)
    if ram_gb is not None:
        info["ram"] = _format_gb(ram_gb)
    return info


class WebSearchArgs(BaseModel):
    """Object-shaped schema for provider tool calling and structured-action recovery.

    Runtime owns Project/Session identity; the model only supplies search fields.
    """

    query: str = Field(
        ...,
        validation_alias=AliasChoices(
            "query", "q", "search", "keywords", "text", "prompt", "topic", "subject"
        ),
        description="Public search query text (compact factual string).",
    )
    objective: str = Field(
        default="",
        validation_alias=AliasChoices("objective", "goal", "intent", "purpose"),
        description="Optional research objective for logging (not required for execution).",
    )
    local_first: bool = Field(
        default=False,
        validation_alias=AliasChoices("local_first", "localFirst", "prefer_local"),
        description="When true, caller prefers local sources; runtime may skip public search.",
    )
    freshness: str = Field(
        default="",
        validation_alias=AliasChoices("freshness", "recency", "time_range"),
        description="Optional freshness hint (e.g. latest, today). Informational only.",
    )



class AnalyzeScreenArgs(BaseModel):
    context: str = Field(
        default="",
        validation_alias=AliasChoices("context", "query", "prompt", "question"),
        description="Optional context about what to look for on screen.",
    )


class VisionQaArgs(BaseModel):
    question: str = Field(
        ..., validation_alias=AliasChoices("question", "query", "prompt"), description="Question about the screen."
    )


class TakeScreenshotArgs(BaseModel):
    path: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("path", "file", "filepath"),
        description="Optional file path to save the screenshot.",
    )


class OpenChromeArgs(BaseModel):
    url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("url", "website", "site", "link"),
        description="Optional URL/website to open in Chrome.",
    )


class DesktopListWindowsArgs(BaseModel):
    filter: Optional[str] = Field(default=None, validation_alias=AliasChoices("filter", "title", "query"))
    limit: int = Field(default=15, ge=1, le=50)


class FileListArgs(BaseModel):
    path: Optional[str] = Field(default=".", validation_alias=AliasChoices("path", "dir", "folder"))
    limit: int = Field(default=50, ge=1, le=200)


class FileReadArgs(BaseModel):
    path: str = Field(
        ...,
        validation_alias=AliasChoices("path", "file", "filepath", "filename"),
        description="Path to read; use Desktop/<project>/file for user projects",
    )
    max_chars: int = Field(default=100000, ge=200, le=200000)


class FileWriteArgs(BaseModel):
    path: str = Field(..., validation_alias=AliasChoices("path", "file", "filepath", "filename"))
    content: str = Field(..., validation_alias=AliasChoices("content", "text", "data"))
    append: bool = Field(default=False)


class FileMoveArgs(BaseModel):
    src: str = Field(..., validation_alias=AliasChoices("src", "source", "from"))
    dst: str = Field(..., validation_alias=AliasChoices("dst", "dest", "destination", "to"))
    overwrite: bool = Field(default=False)


class FileCopyArgs(BaseModel):
    src: str = Field(..., validation_alias=AliasChoices("src", "source", "from"))
    dst: str = Field(..., validation_alias=AliasChoices("dst", "dest", "destination", "to"))
    overwrite: bool = Field(default=False)


class FileDeleteArgs(BaseModel):
    path: str = Field(..., validation_alias=AliasChoices("path", "file", "filepath", "dir", "folder"))
    recursive: bool = Field(default=False)


class FileMkdirArgs(BaseModel):
    path: str = Field(..., validation_alias=AliasChoices("path", "dir", "folder"))
    parents: bool = Field(default=True)
    exist_ok: bool = Field(default=True)


class TerminalRunArgs(BaseModel):
    command: str = Field(..., validation_alias=AliasChoices("command", "cmd", "powershell", "ps"))
    cwd: Optional[str] = Field(default=".", validation_alias=AliasChoices("cwd", "dir", "path", "workdir"))
    timeout: Optional[int] = Field(default=None, ge=1, le=120)


class CodePreviewArgs(BaseModel):
    """Preview controls derive all identity and scope from the active Turn."""

    pass


class ArtifactWriteArgs(BaseModel):
    filename: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("filename", "name", "file"),
        description="File name to write into the artifacts folder (e.g., poem.txt).",
    )
    content: str = Field(
        ...,
        validation_alias=AliasChoices("content", "text", "data"),
        description="Text content to write.",
    )


class OpenApplicationArgs(BaseModel):
    app: str = Field(
        ...,
        validation_alias=AliasChoices("app", "application", "name", "program"),
        description="Application name or executable (must be allowlisted).",
    )
    args: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("args", "arguments", "cmd", "command"),
        description="Optional arguments string.",
    )


class NotepadWriteArgs(BaseModel):
    content: str = Field(
        ...,
        validation_alias=AliasChoices("content", "text", "data"),
        description="Text content to type into Notepad.",
    )
    filename: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("filename", "name", "file"),
        description="Optional artifact file name to save (e.g., poem.txt).",
    )


@tool(args_schema=DesktopListWindowsArgs, description="List open desktop windows (Windows UI Automation).")
def desktop_list_windows(filter: Optional[str] = None, limit: int = 15) -> str:
    err = _require_desktop_automation()
    if err:
        return err

    Desktop = _get_desktop_backend()
    if Desktop is None:
        return "pywinauto is not available. Please install pywinauto."

    try:
        desk = Desktop(backend="uia")
        wins = desk.windows() or []
        q = (filter or "").strip().lower()
        out = []
        for w in wins:
            try:
                title = (w.window_text() or "").strip()
                if not title:
                    continue
                if q and q not in title.lower():
                    continue
                handle = getattr(w, "handle", None)
                out.append((title, handle))
            except Exception:
                continue
        if not out:
            return "No windows found."
        out = out[: max(1, int(limit))]
        lines = []
        for i, (title, handle) in enumerate(out, 1):
            lines.append(f"{i}. {title} (handle={handle})")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to list windows: {str(e)}"


@tool(args_schema=FileListArgs, description="List files/folders within an allowed workspace directory.")
def file_list(path: Optional[str] = ".", limit: int = 50) -> str:
    if denied := _tool_scope_denial("file_list"):
        return denied
    target = _safe_file_path(path or ".")
    if target is None:
        return f"Path not allowed. Allowed roots: {_format_file_tool_roots()}"
    if not target.exists():
        return "Path not found."
    if not target.is_dir():
        return "Path is not a directory."
    try:
        items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = []
        for item in items[: max(1, int(limit))]:
            name = item.name + ("/" if item.is_dir() else "")
            lines.append(name)
        return "\n".join(lines) if lines else "No files found."
    except Exception as e:
        return f"Failed to list files: {str(e)}"


def _echo_file_payload(
    path: Path | str,
    content: str,
    *,
    action: str = "read",
    source_chars: Optional[int] = None,
    source_truncated: bool = False,
) -> str:
    """Structured payload so the Code visualizer can show real file text (not just summaries)."""
    p = str(path or "").replace("\\", "/")
    body = content if content is not None else ""
    # Cap stream payload size while keeping enough for real source files
    max_body = 120_000
    truncated = bool(source_truncated)
    if len(body) > max_body:
        body = body[:max_body]
        truncated = True
    original_chars = int(source_chars) if source_chars is not None else len(content or "")
    header = f"<<<ECHO_FILE action={action} path={p} chars={original_chars}{' truncated=1' if truncated else ''}>>>"
    return f"{header}\n{body}\n<<<END_ECHO_FILE>>>"


def strip_echo_file_wrapper(text: str) -> str:
    """Extract pure file body from tool output. Never write wrappers into source files.

    Handles:
      Read N chars from path
      <<<ECHO_FILE ...>>>
      <body>
      <<<END_ECHO_FILE>>>
    """
    raw = str(text or "")
    if not raw:
        return ""
    # Prefer ECHO_FILE body
    m = re.search(
        r"<<<ECHO_FILE\b[^>]*>>>\r?\n(.*)\r?\n<<<END_ECHO_FILE>>>",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if m:
        return m.group(1)
    # Drop leading "Read N chars…" / "Wrote N chars…" summary lines
    lines = raw.splitlines()
    if lines and re.match(r"^(Read|Wrote|Appended)\s+\d+\s+chars\b", lines[0], flags=re.I):
        body = "\n".join(lines[1:])
        # If residual markers, strip them
        body = re.sub(r"^<<<ECHO_FILE\b[^>]*>>>\s*\n?", "", body, flags=re.I)
        body = re.sub(r"\n?<<<END_ECHO_FILE>>>\s*$", "", body, flags=re.I)
        return body
    # Bare markers without full pair
    if "<<<ECHO_FILE" in raw or "<<<END_ECHO_FILE>>>" in raw:
        raw = re.sub(r"^.*?<<<ECHO_FILE\b[^>]*>>>\s*\n?", "", raw, count=1, flags=re.I | re.DOTALL)
        raw = re.sub(r"\n?<<<END_ECHO_FILE>>>.*$", "", raw, flags=re.I | re.DOTALL)
        return raw
    return raw


@tool(args_schema=FileReadArgs, description="Read a text file from an allowed workspace directory.")
def file_read(path: str, max_chars: int = 100000) -> str:
    if denied := _tool_scope_denial("file_read"):
        return denied
    raw_path = str(path or "").strip()
    # Planner template residue must never become a filesystem probe.
    if re.search(r"\{\{[^{}]+\}\}", raw_path) or re.fullmatch(
        r"(?i)(first_relevant_file|remaining_relevant_files|relevant_files?|"
        r"target_file|file_to_read|chosen_file)",
        raw_path,
    ):
        return (
            f"Rejected unresolved planner template path: {raw_path!r}. "
            "Use a concrete path from file_list or prior tool output."
        )
    target = _safe_file_path(path)
    if target is None:
        roots = _format_file_tool_roots()
        return (
            f"Path not allowed: {path!r}. Allowed roots: {roots}. "
            f"Use Desktop/<project>/filename or an absolute path under an allowed root."
        )
    if not target.exists():
        # Helpful hint when relative path missed the active project
        proj = get_active_project_root()
        hint = f" Active project is {proj}." if proj else " No active project pin — pass Desktop/<project>/file."
        return f"File not found: {target}.{hint}"
    if target.is_dir():
        return f"Path is a directory: {target}. Use file_list to list children."
    try:
        data = target.read_bytes()
        if b"\x00" in data[:2000]:
            return "Binary file detected; text read skipped."
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return "Unsupported text encoding; UTF-8 read required for safe editing."
        source_chars = len(text)
        limit = int(max_chars) if max_chars else 100000
        truncated = bool(limit and len(text) > limit)
        if limit and len(text) > limit:
            text = text[:limit].rstrip() + "\n…(truncated)"
        if not text:
            return (
                f"Read 0 of 0 chars from {target}\n"
                f"{_echo_file_payload(target, '', action='read', source_chars=0, source_truncated=False)}"
            )
        # Lead with human line + structured body for UI + model
        return (
            f"Read {len(text)} of {source_chars} chars from {target}"
            f"{' (truncated)' if truncated else ''}\n"
            f"{_echo_file_payload(target, text, action='read', source_chars=source_chars, source_truncated=truncated)}"
        )
    except Exception as e:
        return f"Failed to read file: {str(e)}"


def _looks_like_code_stub(path: str, content: str) -> bool:
    """Reject comment-only / tiny placeholder writes that leave a white-screen game."""
    p = str(path or "").lower().replace("\\", "/")
    if not any(p.endswith(ext) for ext in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html", ".htm", ".css", ".py")):
        return False
    c = str(content or "").strip()
    if not c:
        return True
    # Tiny stubs (live: game.js became "// Implement collision…" only)
    if len(c) < 100:
        lines = [ln.strip() for ln in c.splitlines() if ln.strip()]
        if not lines:
            return True
        commentish = all(
            ln.startswith(("//", "/*", "*", "#", "<!--", "pass", "..."))
            or ln in {"{}", "[]", ";"}
            or re.match(r"^//.*", ln)
            for ln in lines
        )
        if commentish or len(c) < 60:
            return True
    # Entire file is only comments / TODO placeholders
    code_lines = [
        ln
        for ln in c.splitlines()
        if ln.strip()
        and not ln.strip().startswith(("//", "/*", "*", "#", "<!--"))
        and "todo" not in ln.lower()
        and "implement " not in ln.lower()
    ]
    if len(c) < 400 and not code_lines:
        return True
    return False


@tool(args_schema=FileWriteArgs, description="Write text to a file (restricted; opt-in system action).")
def file_write(path: str, content: str, append: bool = False) -> str:
    if denied := _tool_scope_denial("file_write"):
        return denied
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_file_write", False):
        return "File write is disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_FILE_WRITE=true, then restart the API."
    target = _safe_file_path(path)
    if target is None:
        return f"Path not allowed. Allowed roots: {_format_file_tool_roots()}. Prefer Desktop/<project>/filename for user projects."
    # Never persist tool wrapper / "Read N chars" pollution into real source files
    body = strip_echo_file_wrapper(content or "")
    if not append and _looks_like_code_stub(str(path), body):
        return (
            "Rejected stub write: content is too small or comment-only for a code file. "
            "Write the FULL working file content (not '// Implement …' placeholders). "
            f"Path was: {target}"
        )
    if denial := _mutation_precondition_denial("file_write"):
        return denial
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # If writing into a new project dir, pin active project for later relative paths
        try:
            scope = _tool_execution_context.get() or {}
            if (
                not str(scope.get("project_root") or "").strip()
                and target.parent.is_dir()
                and target.parent.name
                and target.parent != _file_tool_root()
            ):
                # Pin parent when it looks like a project folder under Desktop
                desk = _desktop_root()
                active_project_root = get_active_project_root()
                if str(target.parent).startswith(str(desk)) or (
                    active_project_root and str(target).startswith(str(active_project_root))
                ):
                    set_active_project_root(str(target.parent))
        except Exception:
            pass
        # Create checkpoint backup before modifying existing file
        if denial := _mutation_precondition_denial("file_write"):
            return denial
        try:
            from agent.checkpoints import create_checkpoint
            create_checkpoint(str(target), reason="file_write")
        except Exception as exc:
            logger.warning("Checkpoint creation failed: {}", exc)

        final_body = body
        if append and target.exists():
            try:
                final_body = target.read_text(encoding="utf-8", errors="strict") + body
            except UnicodeDecodeError:
                return "Unsupported text encoding; UTF-8 append required for safe editing."
        temp_target = target.with_name(f".{target.name}.echospeak-{uuid.uuid4().hex}.tmp")
        try:
            with open(temp_target, "x", encoding="utf-8", newline="") as f:
                f.write(final_body)
                f.flush()
                os.fsync(f.fileno())
            # Recheck the exact approval precondition at the final replacement
            # boundary, after all potentially slow preparation work.
            if denial := _mutation_precondition_denial("file_write"):
                try:
                    temp_target.unlink(missing_ok=True)
                except Exception:
                    pass
                return denial
            os.replace(temp_target, target)
        finally:
            try:
                temp_target.unlink(missing_ok=True)
            except Exception:
                pass
        action = "Appended" if append else "Wrote"
        # Include file body so Code visualizer can show real source (not "Wrote N chars")
        return f"{action} {len(body)} chars to {target}\n{_echo_file_payload(target, body, action='write')}"
    except Exception as e:
        return f"Failed to write file: {str(e)}"


@tool(args_schema=FileMoveArgs, description="Move/rename a file or folder inside allowed workspace directories (opt-in system action).")
def file_move(src: str, dst: str, overwrite: bool = False) -> str:
    if denied := _tool_scope_denial("file_move"):
        return denied
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_file_write", False):
        return "File operations are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_FILE_WRITE=true, then restart the API."
    src_p = _safe_file_path(src)
    dst_p = _safe_file_path(dst)
    if src_p is None or dst_p is None:
        return "Path not allowed."
    if not src_p.exists():
        return "Source path not found."
    if denial := _mutation_precondition_denial("file_move"):
        return denial
    try:
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        # Parent preparation may be slow or externally observable. Revalidate
        # the approved source and destination immediately before replacement.
        if denial := _mutation_precondition_denial("file_move"):
            return denial
        if dst_p.exists():
            if not overwrite:
                return "Destination already exists. Set overwrite=true to replace."
            if dst_p.is_dir():
                shutil.rmtree(dst_p)
            else:
                dst_p.unlink()
        shutil.move(str(src_p), str(dst_p))
        return f"Moved {src_p} -> {dst_p}"
    except Exception as e:
        return f"Failed to move: {str(e)}"


@tool(args_schema=FileCopyArgs, description="Copy a file or folder inside allowed workspace directories (opt-in system action).")
def file_copy(src: str, dst: str, overwrite: bool = False) -> str:
    if denied := _tool_scope_denial("file_copy"):
        return denied
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_file_write", False):
        return "File operations are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_FILE_WRITE=true, then restart the API."
    src_p = _safe_file_path(src)
    dst_p = _safe_file_path(dst)
    if src_p is None or dst_p is None:
        return "Path not allowed."
    if not src_p.exists():
        return "Source path not found."
    if denial := _mutation_precondition_denial("file_copy"):
        return denial
    try:
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        # Copying can overwrite a destination and traverse a large source.
        # Revalidate after preparation and before the first destructive step.
        if denial := _mutation_precondition_denial("file_copy"):
            return denial
        if dst_p.exists():
            if not overwrite:
                return "Destination already exists. Set overwrite=true to replace."
        temp_dst = dst_p.with_name(f".{dst_p.name}.echospeak-copy-{uuid.uuid4().hex}.tmp")
        try:
            if src_p.is_dir():
                shutil.copytree(src_p, temp_dst)
            else:
                shutil.copy2(src_p, temp_dst)
            # A large source may change while it is copied. Validate the exact
            # approved source/destination again before publishing the temp copy.
            if denial := _mutation_precondition_denial("file_copy"):
                return denial
            if dst_p.exists():
                if dst_p.is_dir():
                    shutil.rmtree(dst_p)
                else:
                    dst_p.unlink()
            os.replace(temp_dst, dst_p)
        finally:
            try:
                if temp_dst.is_dir():
                    shutil.rmtree(temp_dst)
                else:
                    temp_dst.unlink(missing_ok=True)
            except Exception:
                pass
        return f"Copied {src_p} -> {dst_p}"
    except Exception as e:
        return f"Failed to copy: {str(e)}"


@tool(args_schema=FileDeleteArgs, description="Delete a file or folder inside allowed workspace directories (opt-in system action).")
def file_delete(path: str, recursive: bool = False) -> str:
    if denied := _tool_scope_denial("file_delete"):
        return denied
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_file_write", False):
        return "File operations are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_FILE_WRITE=true, then restart the API."
    target = _safe_file_path(path)
    if target is None:
        return "Path not allowed."
    if not target.exists():
        return "Path not found."
    if denial := _mutation_precondition_denial("file_delete"):
        return denial
    try:
        if denial := _mutation_precondition_denial("file_delete"):
            return denial
        if target.is_dir():
            if not recursive:
                return "Path is a directory. Set recursive=true to delete folders."
            shutil.rmtree(target)
        else:
            # Create checkpoint backup before deleting existing file
            try:
                from agent.checkpoints import create_checkpoint
                create_checkpoint(str(target), reason="file_delete")
            except Exception as exc:
                logger.warning("Checkpoint creation failed: {}", exc)
            if denial := _mutation_precondition_denial("file_delete"):
                return denial
            target.unlink()
        return f"Deleted {target}"
    except Exception as e:
        return f"Failed to delete: {str(e)}"


@tool(args_schema=FileMkdirArgs, description="Create a folder inside an allowed workspace directory (opt-in system action).")
def file_mkdir(path: str, parents: bool = True, exist_ok: bool = True) -> str:
    if denied := _tool_scope_denial("file_mkdir"):
        return denied
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_file_write", False):
        return "File operations are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_FILE_WRITE=true, then restart the API."
    target = _safe_file_path(path)
    if target is None:
        return f"Path not allowed. Allowed roots: {_format_file_tool_roots()}"
    if denial := _mutation_precondition_denial("file_mkdir"):
        return denial
    try:
        if denial := _mutation_precondition_denial("file_mkdir"):
            return denial
        target.mkdir(parents=bool(parents), exist_ok=bool(exist_ok))
        # Pin coding project so later bare "index.html" / "game.js" resolve here
        try:
            scope = _tool_execution_context.get() or {}
            if not str(scope.get("project_root") or "").strip():
                set_active_project_root(str(target))
        except Exception:
            pass
        return f"Created folder: {target}"
    except Exception as e:
        return f"Failed to create folder: {str(e)}"


def _terminal_first_token(command: str) -> str:
    s = (command or "").strip()
    if not s:
        return ""
    try:
        import shlex

        parts = shlex.split(s, posix=(os.name != "nt"))
    except Exception:
        parts = s.split()
    if not parts:
        return ""
    token = str(parts[0]).strip().lower()
    token = token.rsplit("\\", 1)[-1]
    token = token.rsplit("/", 1)[-1]
    token = token.removesuffix(".exe") if token.endswith(".exe") else token
    return token


def _is_command_safe(command: str) -> bool:
    """Check if a command is free of shell chaining, statement separators, or redirection
    operators outside of quotes. Prevents command injection and denylist bypass.
    """
    in_single_quote = False
    in_double_quote = False
    escape = False

    for i, char in enumerate(command):
        if escape:
            escape = False
            continue
        if char == '\\':
            escape = True
            continue
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            continue
        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            continue

        if not in_single_quote and not in_double_quote:
            # Block statement separators, command chaining, pipelines, redirection,
            # newlines, subexpressions, and environment variables outside quotes.
            if char in (';', '&', '|', '<', '>', '\n', '\r', '`', '$'):
                return False

    return True


def _terminal_command_denied(command: str) -> Optional[str]:
    token = _terminal_first_token(command)
    deny = [
        str(x).strip().lower()
        for x in (getattr(config, "terminal_command_denylist", None) or [])
        if str(x).strip()
    ]
    if not token:
        return "Command rejected: command is empty."
    if token in set(deny):
        return f"Command blocked by terminal denylist: {token}"
    return None


@tool(args_schema=TerminalRunArgs, description="Run a PowerShell command (Windows; denylisted dangerous commands; opt-in system action).")
def terminal_run(command: str, cwd: Optional[str] = ".", timeout: Optional[int] = None) -> str:
    if denied := _tool_scope_denial("terminal_run"):
        return denied
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_terminal_commands", False):
        return "Terminal commands are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_TERMINAL_COMMANDS=true, then restart the API."

    # Prevent command injection / denylist bypass via command chaining.
    if not _is_command_safe(command):
        return "Command rejected: Contains statement separators or chaining operators outside of quotes."

    cwd_p = _safe_file_path(cwd or ".")
    if cwd_p is None:
        return f"CWD not allowed. Allowed roots: {_format_file_tool_roots()}"
    if not cwd_p.exists() or not cwd_p.is_dir():
        return "CWD not found or is not a directory."

    denied = _terminal_command_denied(command)
    if denied:
        return denied

    try:
        default_timeout = int(getattr(config, "terminal_command_timeout", 20) or 20)
    except Exception:
        default_timeout = 20
    if timeout is None:
        timeout_s = default_timeout
    else:
        timeout_s = int(timeout)
    if timeout_s <= 0:
        timeout_s = default_timeout
    timeout_s = max(1, min(120, timeout_s))

    # Docker/sandbox is default; host requires explicit configuration. Never fall back.
    try:
        from agent.sandbox import normalize_execution_mode, run_sandboxed_terminal
    except Exception:
        normalize_execution_mode = None  # type: ignore
        run_sandboxed_terminal = None  # type: ignore

    mode = "docker"
    if callable(normalize_execution_mode):
        mode = normalize_execution_mode(getattr(config, "terminal_execution_mode", "docker"))
    else:
        raw_mode = str(getattr(config, "terminal_execution_mode", "docker") or "docker").strip().lower()
        mode = "host" if raw_mode == "host" else "docker"

    if mode == "docker":
        if not callable(run_sandboxed_terminal):
            return (
                "ExitCode=127\n"
                "Status=sandbox_unavailable\n"
                "Mode=docker\n"
                "Reason=sandbox_unavailable: agent.sandbox module failed to import. "
                "Fix the install or set TERMINAL_EXECUTION_MODE=host (unsandboxed)."
            )
        result = run_sandboxed_terminal(
            command,
            cwd=cwd_p,
            timeout_s=timeout_s,
            denylist_check=_terminal_command_denied,
        )
        return result.format()

    # ---- explicitly selected host path ----
    cmd: list[str]
    if os.name == "nt":
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        try:
            import shlex

            cmd = shlex.split(command or "", posix=True)
        except Exception:
            cmd = (command or "").split()
        if not cmd:
            return "Command is empty."
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd_p),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=timeout_s,
        )
        out = (proc.stdout or "") + ("\n" if (proc.stdout and proc.stderr) else "") + (proc.stderr or "")
        out = out.strip("\n")
        try:
            max_chars = int(getattr(config, "terminal_max_output_chars", 8000) or 8000)
        except Exception:
            max_chars = 8000
        if max_chars > 0 and len(out) > max_chars:
            out = out[:max_chars].rstrip() + "…"
        status = "pass" if int(proc.returncode) == 0 else "fail"
        header = f"ExitCode={proc.returncode}\nStatus={status}\nMode=host"
        if out:
            return header + "\n" + out
        return header
    except subprocess.TimeoutExpired:
        return (
            f"ExitCode=124\nStatus=timeout\nMode=host\n"
            f"Reason=Command timed out after {timeout_s}s."
        )
    except Exception as e:
        return f"ExitCode=1\nStatus=fail\nMode=host\nReason=Failed to run command: {str(e)}"


@tool(
    args_schema=CodePreviewArgs,
    description="Start the detected preview for the active Session's attached Project. Requires explicit approval.",
)
def code_preview_start() -> str:
    if denied := _tool_scope_denial("code_preview_start"):
        return denied
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_terminal_commands", False):
        return "Code preview is disabled. Enable system actions and terminal commands first."
    context = _tool_execution_context.get() or {}
    thread_id = str(context.get("thread_id") or "").strip()
    project_root = str(context.get("project_root") or context.get("project_path") or "").strip()
    if not thread_id or not project_root:
        return "Code preview blocked: the active Session has no attached Project root."
    try:
        from agent.project_preview import detect_project, get_preview_manager

        root = Path(project_root).resolve()
        if not root.exists() or not root.is_dir():
            return "Code preview blocked: the attached Project root is unavailable."
        detection = detect_project(root)
        result = get_preview_manager().start(thread_id, root, detection)
        return json.dumps({
            **dict(result or {}),
            "thread_id": thread_id,
            "project_root": str(root),
            "detection": detection.as_dict(),
        }, ensure_ascii=False)
    except Exception as exc:
        return f"Code preview failed to start: {exc}"


@tool(
    args_schema=CodePreviewArgs,
    description="Stop the running preview owned by the active Session. Requires explicit approval.",
)
def code_preview_stop() -> str:
    if denied := _tool_scope_denial("code_preview_stop"):
        return denied
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_terminal_commands", False):
        return "Code preview is disabled. Enable system actions and terminal commands first."
    context = _tool_execution_context.get() or {}
    thread_id = str(context.get("thread_id") or "").strip()
    if not thread_id:
        return "Code preview blocked: no active Session is bound."
    try:
        from agent.project_preview import get_preview_manager

        result = get_preview_manager().stop(thread_id)
        return json.dumps({
            **dict(result or {}),
            "thread_id": thread_id,
            "preview": get_preview_manager().status(thread_id),
        }, ensure_ascii=False)
    except Exception as exc:
        return f"Code preview failed to stop: {exc}"


def _artifacts_root() -> Path:
    root = Path(getattr(config, "artifacts_dir", "") or "").expanduser()
    if not str(root).strip():
        root = Path.cwd() / "data" / "artifacts"
    try:
        root = root.resolve()
    except Exception:
        root = Path.cwd() / "data" / "artifacts"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return root


def _safe_artifact_filename(name: Optional[str]) -> str:
    raw = (name or "").strip().strip("\"' ")
    raw = raw.replace("\\", "/")
    raw = raw.split("/")[-1]
    raw = re.sub(r"[^a-zA-Z0-9._-]", "_", raw)
    raw = raw.strip("._-")
    if not raw:
        raw = f"artifact_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    if "." not in raw:
        raw = raw + ".txt"
    return raw


@tool(args_schema=ArtifactWriteArgs, description="Write text to a safe artifacts folder and return the file path.")
def artifact_write(filename: Optional[str] = None, content: str = "") -> str:
    if denied := _tool_scope_denial("artifact_write"):
        return denied
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_file_write", False):
        return "Artifact write is disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_FILE_WRITE=true, then restart the API."
    data = str(content or "")
    if len(data) > 200000:
        data = data[:200000].rstrip() + "…"
    root = _artifacts_root()
    fname = _safe_artifact_filename(filename)
    target = root / fname
    if denial := _mutation_precondition_denial("artifact_write"):
        return denial
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if denial := _mutation_precondition_denial("artifact_write"):
            return denial
        target.write_text(data, encoding="utf-8")
        return str(target)
    except Exception as e:
        return f"Failed to write artifact: {str(e)}"


def _normalize_app_token(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _resolve_app_command(app: str, args: Optional[str]) -> Optional[list[str]]:
    a = _normalize_app_token(app)
    mapping: dict[str, list[str]] = {
        "notepad": ["notepad.exe"],
        "calc": ["calc.exe"],
        "calculator": ["calc.exe"],
        "paint": ["mspaint.exe"],
        "explorer": ["explorer.exe"],
        "cmd": ["cmd.exe"],
        "powershell": ["powershell.exe"],
        "terminal": ["wt.exe"],
    }
    cmd = mapping.get(a)
    if cmd is None:
        cmd = [app]
    extra: list[str] = []
    raw = (args or "").strip()
    if raw:
        try:
            import shlex

            extra = shlex.split(raw, posix=False)
        except Exception:
            extra = [raw]
    return [*cmd, *extra]


@tool(args_schema=OpenApplicationArgs, description="Open/launch an application on Windows (allowlisted; opt-in system action).")
def open_application(app: str, args: Optional[str] = None) -> str:
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_open_application", False):
        return "System actions are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_OPEN_APPLICATION=true, then restart the API."
    if os.name != "nt":
        return "open_application is only supported on Windows."

    allow = [
        _normalize_app_token(x)
        for x in (getattr(config, "open_application_allowlist", None) or [])
        if str(x).strip()
    ]
    token = _normalize_app_token(app)
    if not allow:
        return "No applications are allowlisted. Set OPEN_APPLICATION_ALLOWLIST and restart the API."
    if token not in set(allow):
        return f"Application not allowlisted: {app}"

    cmd = _resolve_app_command(app, args)
    if not cmd:
        return "Failed to resolve application command."
    try:
        subprocess.Popen(cmd)
        return "Opened application."
    except Exception as e:
        return f"Failed to open application: {str(e)}"


@tool(args_schema=NotepadWriteArgs, description="Open Notepad, type text, and save a copy to the artifacts folder (opt-in system action).")
def notepad_write(content: str, filename: Optional[str] = None) -> str:
    if denied := _tool_scope_denial("notepad_write"):
        return denied
    if not getattr(config, "enable_system_actions", False):
        return "System actions are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true, then restart the API."
    if not getattr(config, "allow_open_application", False):
        return "Application launching is disabled. To enable: set ALLOW_OPEN_APPLICATION=true, then restart the API."
    if not getattr(config, "allow_desktop_automation", False):
        return "Desktop automation is disabled. To enable: set ALLOW_DESKTOP_AUTOMATION=true, then restart the API."
    if not getattr(config, "allow_file_write", False):
        return "File write is disabled. To enable: set ALLOW_FILE_WRITE=true, then restart the API."
    if os.name != "nt":
        return "notepad_write is only supported on Windows."

    allow = {
        _normalize_app_token(x)
        for x in (getattr(config, "open_application_allowlist", None) or [])
        if str(x).strip()
    }
    if allow and ("notepad" not in allow and "notepad.exe" not in allow):
        return "Notepad is not allowlisted. Add 'notepad' to OPEN_APPLICATION_ALLOWLIST and restart the API."

    data = str(content or "")
    if data == "":
        return "No content provided."
    if len(data) > 200000:
        data = data[:200000].rstrip() + "…"

    root = _artifacts_root()
    fname = _safe_artifact_filename(filename)
    artifact_path = root / fname
    if denial := _mutation_precondition_denial("notepad_write"):
        return denial
    try:
        if denial := _mutation_precondition_denial("notepad_write"):
            return denial
        artifact_path.write_text(data, encoding="utf-8")
    except Exception as e:
        return f"Failed to write artifact: {str(e)}"

    try:
        subprocess.Popen(["notepad.exe"])
    except Exception as e:
        return f"Failed to open Notepad: {str(e)}"

    focused = False
    try:
        Desktop = _get_desktop_backend()
        if Desktop is not None:
            deadline = time.time() + 5.0
            while time.time() < deadline:
                try:
                    desk = Desktop(backend="uia")
                    win = _find_window_by_title_substring(desk, "Notepad")
                    if win is not None:
                        try:
                            win.set_focus()
                        except Exception:
                            pass
                        focused = True
                        break
                except Exception:
                    pass
                time.sleep(0.2)
    except Exception:
        focused = False

    try:
        import pyautogui  # type: ignore

        if not focused:
            time.sleep(0.6)
        pyautogui.typewrite(data)
    except Exception as e:
        return f"Notepad opened and artifact saved to {artifact_path}, but typing failed: {str(e)}"

    return f"Opened Notepad, typed {len(data)} chars, and saved artifact: {artifact_path}"


@tool(description="Return basic system hardware info (OS, CPU, GPU, RAM).")
def system_info() -> str:
    info = _collect_system_info()
    if not info:
        return "System info unavailable."
    lines = []
    for key in ("os", "cpu", "gpu", "ram"):
        val = info.get(key)
        if val:
            label = key.upper() if key != "os" else "OS"
            lines.append(f"{label}: {val}")
    return "\n".join(lines) if lines else "System info unavailable."


class DesktopFindControlArgs(BaseModel):
    window_title: str = Field(..., validation_alias=AliasChoices("window_title", "window", "app", "title"))
    control_name: Optional[str] = Field(default=None, validation_alias=AliasChoices("control_name", "control", "name"))
    control_type: Optional[str] = Field(default=None, validation_alias=AliasChoices("control_type", "type"))
    automation_id: Optional[str] = Field(default=None, validation_alias=AliasChoices("automation_id", "auto_id", "id"))
    limit: int = Field(default=8, ge=1, le=25)


def _find_window_by_title_substring(desktop, window_title: str):
    title_q = (window_title or "").strip().lower()
    if not title_q:
        return None
    wins = desktop.windows() or []
    candidates = []
    for w in wins:
        try:
            title = (w.window_text() or "").strip()
            if not title:
                continue
            if title_q in title.lower():
                candidates.append((len(title), w))
        except Exception:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _find_controls(window, control_name: Optional[str], control_type: Optional[str], automation_id: Optional[str], limit: int):
    kwargs = {}
    if control_name:
        kwargs["title"] = control_name
    if control_type:
        kwargs["control_type"] = control_type
    if automation_id:
        kwargs["automation_id"] = automation_id

    ctrls = []
    try:
        if kwargs:
            ctrls = window.descendants(**kwargs) or []
        else:
            ctrls = window.descendants() or []
    except Exception:
        ctrls = []

    return (ctrls or [])[: max(1, int(limit))]


@tool(args_schema=DesktopFindControlArgs, description="Find UI controls in a target window using Windows UI Automation.")
def desktop_find_control(
    window_title: str,
    control_name: Optional[str] = None,
    control_type: Optional[str] = None,
    automation_id: Optional[str] = None,
    limit: int = 8,
) -> str:
    err = _require_desktop_automation()
    if err:
        return err

    Desktop = _get_desktop_backend()
    if Desktop is None:
        return "pywinauto is not available. Please install pywinauto."

    try:
        desk = Desktop(backend="uia")
        win = _find_window_by_title_substring(desk, window_title)
        if win is None:
            return "Window not found."

        ctrls = _find_controls(win, control_name, control_type, automation_id, limit)
        if not ctrls:
            return "No matching controls found."

        lines = []
        for i, c in enumerate(ctrls, 1):
            try:
                name = (c.window_text() or "").strip()
                ctype = getattr(c, "control_type", lambda: "")()
                aid = getattr(getattr(c, "element_info", None), "automation_id", None)
                rect = ""
                try:
                    rect = _safe_rect_str(c.rectangle())
                except Exception:
                    rect = ""
                lines.append(f"{i}. name={name!r} type={ctype!r} automation_id={aid!r} rect={rect}")
            except Exception:
                continue
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to find controls: {str(e)}"


class DesktopClickArgs(BaseModel):
    window_title: str = Field(..., validation_alias=AliasChoices("window_title", "window", "app", "title"))
    control_name: Optional[str] = Field(default=None, validation_alias=AliasChoices("control_name", "control", "name"))
    control_type: Optional[str] = Field(default=None, validation_alias=AliasChoices("control_type", "type"))
    automation_id: Optional[str] = Field(default=None, validation_alias=AliasChoices("automation_id", "auto_id", "id"))
    dry_run: bool = Field(default=False, validation_alias=AliasChoices("dry_run", "preview"))


@tool(args_schema=DesktopClickArgs, description="Click a UI control inside a window (supports dry_run preview).")
def desktop_click(
    window_title: str,
    control_name: Optional[str] = None,
    control_type: Optional[str] = None,
    automation_id: Optional[str] = None,
    dry_run: bool = False,
) -> str:
    err = _require_desktop_automation()
    if err:
        return err

    Desktop = _get_desktop_backend()
    if Desktop is None:
        return "pywinauto is not available. Please install pywinauto."

    try:
        desk = Desktop(backend="uia")
        win = _find_window_by_title_substring(desk, window_title)
        if win is None:
            return "Window not found."
        ctrls = _find_controls(win, control_name, control_type, automation_id, limit=1)
        if not ctrls:
            return "Control not found."
        c = ctrls[0]
        name = (c.window_text() or "").strip()
        ctype = getattr(c, "control_type", lambda: "")()
        aid = getattr(getattr(c, "element_info", None), "automation_id", None)
        rect_obj = None
        rect_str = ""
        try:
            rect_obj = c.rectangle()
            rect_str = _safe_rect_str(rect_obj)
        except Exception:
            rect_obj = None
            rect_str = ""

        if dry_run:
            return f"Preview: click control name={name!r} type={ctype!r} automation_id={aid!r} rect={rect_str} in window={window_title!r}"

        try:
            win.set_focus()
        except Exception:
            pass

        try:
            c.click_input()
            return "Clicked."
        except Exception:
            try:
                import pyautogui  # type: ignore

                if rect_obj is None:
                    return "Click failed."
                x, y = _rect_center(rect_obj)
                if x <= 0 and y <= 0:
                    return "Click failed."
                pyautogui.click(x, y)
                return "Clicked."
            except Exception as e:
                return f"Click failed: {str(e)}"
    except Exception as e:
        return f"Click failed: {str(e)}"


class DesktopTypeArgs(BaseModel):
    window_title: str = Field(..., validation_alias=AliasChoices("window_title", "window", "app", "title"))
    text: str = Field(..., validation_alias=AliasChoices("text", "value", "input"))
    control_name: Optional[str] = Field(default=None, validation_alias=AliasChoices("control_name", "control", "name"))
    control_type: Optional[str] = Field(default=None, validation_alias=AliasChoices("control_type", "type"))
    automation_id: Optional[str] = Field(default=None, validation_alias=AliasChoices("automation_id", "auto_id", "id"))
    append: bool = Field(default=False, validation_alias=AliasChoices("append", "add"))
    dry_run: bool = Field(default=False, validation_alias=AliasChoices("dry_run", "preview"))


@tool(args_schema=DesktopTypeArgs, description="Type text into a UI control inside a window (supports dry_run preview).")
def desktop_type_text(
    window_title: str,
    text: str,
    control_name: Optional[str] = None,
    control_type: Optional[str] = None,
    automation_id: Optional[str] = None,
    append: bool = False,
    dry_run: bool = False,
) -> str:
    err = _require_desktop_automation()
    if err:
        return err

    Desktop = _get_desktop_backend()
    if Desktop is None:
        return "pywinauto is not available. Please install pywinauto."

    payload = (text or "")
    if payload == "":
        return "No text provided."

    try:
        desk = Desktop(backend="uia")
        win = _find_window_by_title_substring(desk, window_title)
        if win is None:
            return "Window not found."
        ctrls = _find_controls(win, control_name, control_type, automation_id, limit=1)
        if not ctrls:
            return "Control not found."
        c = ctrls[0]
        name = (c.window_text() or "").strip()
        ctype = getattr(c, "control_type", lambda: "")()
        aid = getattr(getattr(c, "element_info", None), "automation_id", None)
        rect_str = ""
        try:
            rect_str = _safe_rect_str(c.rectangle())
        except Exception:
            rect_str = ""

        if dry_run:
            preview_text = payload
            if len(preview_text) > 120:
                preview_text = preview_text[:120].rstrip() + "…"
            return f"Preview: type text={preview_text!r} append={append} into control name={name!r} type={ctype!r} automation_id={aid!r} rect={rect_str} in window={window_title!r}"

        try:
            win.set_focus()
        except Exception:
            pass

        try:
            c.set_focus()
        except Exception:
            pass

        try:
            if append:
                try:
                    from pywinauto.keyboard import send_keys  # type: ignore

                    send_keys("{END}")
                except Exception:
                    pass

            if hasattr(c, "set_edit_text"):
                if append:
                    try:
                        existing = ""
                        try:
                            existing = c.window_text() or ""
                        except Exception:
                            existing = ""
                        c.set_edit_text(existing + payload)
                        return "Typed."
                    except Exception:
                        pass
                c.set_edit_text(payload)
                return "Typed."

            try:
                c.click_input()
            except Exception:
                pass

            from pywinauto.keyboard import send_keys  # type: ignore

            send_keys(payload, with_spaces=True)
            return "Typed."
        except Exception:
            try:
                import pyautogui  # type: ignore

                pyautogui.typewrite(payload)
                return "Typed."
            except Exception as e:
                return f"Type failed: {str(e)}"
    except Exception as e:
        return f"Type failed: {str(e)}"


class DesktopActivateWindowArgs(BaseModel):
    window_title: str = Field(..., validation_alias=AliasChoices("window_title", "window", "app", "title"))
    dry_run: bool = Field(default=False, validation_alias=AliasChoices("dry_run", "preview"))


@tool(args_schema=DesktopActivateWindowArgs, description="Activate/focus a window by title (supports dry_run preview).")
def desktop_activate_window(window_title: str, dry_run: bool = False) -> str:
    err = _require_desktop_automation()
    if err:
        return err

    Desktop = _get_desktop_backend()
    if Desktop is None:
        return "pywinauto is not available. Please install pywinauto."

    try:
        desk = Desktop(backend="uia")
        win = _find_window_by_title_substring(desk, window_title)
        if win is None:
            return "Window not found."
        title = (win.window_text() or "").strip() or window_title

        if dry_run:
            return f"Preview: activate window title={title!r}"

        try:
            if hasattr(win, "restore"):
                try:
                    win.restore()
                except Exception:
                    pass
            win.set_focus()
        except Exception:
            pass
        return f"Activated window: {title}"
    except Exception as e:
        return f"Activate failed: {str(e)}"


class DesktopSendHotkeyArgs(BaseModel):
    hotkey: str = Field(..., validation_alias=AliasChoices("hotkey", "keys", "combo"))
    window_title: Optional[str] = Field(default=None, validation_alias=AliasChoices("window_title", "window", "app", "title"))
    dry_run: bool = Field(default=False, validation_alias=AliasChoices("dry_run", "preview"))


def _parse_hotkey_combo(combo: str) -> list[str]:
    raw = (combo or "").strip().lower()
    if not raw:
        return []
    raw = raw.replace(" ", "")
    parts = [p for p in raw.split("+") if p]
    mapped = []
    for p in parts:
        if p in {"control", "ctl"}:
            p = "ctrl"
        if p in {"windows", "win"}:
            p = "win"
        if p in {"escape"}:
            p = "esc"
        mapped.append(p)
    return mapped


@tool(args_schema=DesktopSendHotkeyArgs, description="Send a hotkey combo like ctrl+l or alt+f4 (supports dry_run preview).")
def desktop_send_hotkey(hotkey: str, window_title: Optional[str] = None, dry_run: bool = False) -> str:
    err = _require_desktop_automation()
    if err:
        return err

    keys = _parse_hotkey_combo(hotkey)
    if not keys:
        return "Invalid hotkey. Use a format like ctrl+l or alt+f4."

    display = "+".join(keys)
    if dry_run:
        if window_title:
            return f"Preview: activate window={window_title!r}, then send hotkey={display}"
        return f"Preview: send hotkey={display}"

    if window_title:
        try:
            _ = desktop_activate_window(window_title=window_title, dry_run=False)
        except Exception:
            pass

    try:
        import pyautogui  # type: ignore

        pyautogui.hotkey(*keys)
        return f"Sent hotkey: {display}"
    except Exception as e:
        return f"Hotkey failed: {str(e)}"


def _clamp_vision_summary(text: str, max_chars: int = 700) -> str:
    s = (text or "").strip()
    if not s:
        return ""

    s = re.sub(r"^the image depicts\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^in summary[:,\s-]*", "", s, flags=re.IGNORECASE)

    # Remove common markdown formatting produced by VLMs.
    s = re.sub(r"\*\*(.*?)\*\*", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)

    raw_lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    lines = []
    for ln in raw_lines:
        ln = re.sub(r"^#{1,6}\s*", "", ln)
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            lines.append(ln)

    bullet_re = re.compile(r"^(?:[-*]|\d+[\.)])\s+")
    bullets = [ln for ln in lines if bullet_re.match(ln)]
    bullets = bullets[:3]

    intro = ""
    for ln in lines:
        if not bullet_re.match(ln):
            intro = ln
            break

    out_lines = []
    if intro:
        out_lines.append(intro)

    for b in bullets:
        b = re.sub(r"^\d+[\.)]\s+", "- ", b)
        out_lines.append(b)

    out = "\n".join(out_lines) if out_lines else " ".join(lines)
    out = out.strip()

    if len(out) <= max_chars:
        return out

    head = out[:max_chars].rstrip()
    for sep in (". ", "; ", ": ", ", ", " "):
        cut = head.rfind(sep)
        if cut >= max(40, max_chars // 3):
            return head[:cut].rstrip(" ,;:") + "…"
    return head.rstrip(" ,;:") + "…"


def _split_live_desktop_context(t: str) -> tuple[str, str]:
    s = (t or "").strip()
    if not s:
        return "", ""

    marker = "live desktop context:"
    low = s.lower()
    idx = low.find(marker)
    if idx == -1:
        return s, ""

    q = s[:idx].strip()
    ctx = s[idx + len(marker):].strip()
    return q, ctx


def _resize_for_vision(img: "np.ndarray", max_dim: int = 1024) -> "np.ndarray":
    try:
        h, w = img.shape[:2]
        if h <= 0 or w <= 0:
            return img
        m = max(h, w)
        if m <= max_dim:
            return img
        scale = float(max_dim) / float(m)
        nh = max(1, int(round(h * scale)))
        nw = max(1, int(round(w * scale)))
        return cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    except Exception:
        return img


def _encode_image_b64(img: "np.ndarray") -> str:
    """Encode an image to a compact JPEG base64 string for vision models."""
    try:
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            ok, buf = cv2.imencode(".png", img)
        if not ok:
            return ""
        return base64.b64encode(buf.tobytes()).decode("utf-8")
    except Exception:
        return ""


class SportsLiveArgs(BaseModel):
    query: str = Field(description="Sports subject, team, or competition request")
    operation: Literal[
        "schedule", "live_scores", "standings", "results",
        "team_next_event", "competition_next_event", "odds",
    ] = Field(default="live_scores", description="Exact structured sports operation")


class WeatherLiveArgs(BaseModel):
    location: str = Field(description="City or location whose current weather should be retrieved")


@tool(
    args_schema=SportsLiveArgs,
    description=(
        "Live sports scores and betting odds from a structured sports-data API "
        "(not web crawl). Supports schedule, live_scores, standings, results, "
        "team_next_event, competition_next_event, and odds. Unsupported provider coverage "
        "returns a typed no-data/unsupported state so grounded web fallback may run."
    ),
)
def sports_live(query: str, operation: str = "live_scores") -> str:
    """Structured live sports data (The Odds API). Not a web search crawl."""
    try:
        from agent.sports_data import get_sports_data_client

        client = get_sports_data_client()
        result = client.query(query or "", operation=operation)
        return result.as_tool_text()
    except Exception as exc:
        logger.exception("sports_live provider request failed")
        return (
            "[SPORTS_LIVE] ok=false execution_status=error "
            "result_state=provider_unavailable provider=sports_live retryable=false\n"
            "DETAIL: the structured sports provider request failed safely\n"
            "NEXT_ACTION: use an allowed grounded web_search fallback when available."
        )


@tool(
    args_schema=WeatherLiveArgs,
    description=(
        "Current weather from the structured Open-Meteo geocoding and forecast APIs. "
        "Prefer this over web_search when a location is known."
    ),
)
def weather_live(location: str) -> str:
    """Keyless structured current weather with bounded network responses."""
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    place = str(location or "").strip()
    if not place:
        return "[WEATHER_LIVE] ok=false error=missing_location"

    def _read_json(url: str) -> dict[str, Any]:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "EchoSpeak/8.0"})
        with urlopen(request, timeout=8) as response:
            body = response.read(512_001)
        if len(body) > 512_000:
            raise ValueError("weather response exceeded 512 KB")
        payload = json.loads(body.decode("utf-8")) if body else {}
        if not isinstance(payload, dict):
            raise ValueError("weather response was not an object")
        return payload

    try:
        geo = _read_json(
            "https://geocoding-api.open-meteo.com/v1/search?"
            + urlencode({"name": place, "count": 1, "language": "en", "format": "json"})
        )
        rows = geo.get("results") or []
        if not rows or not isinstance(rows[0], dict):
            return f"[WEATHER_LIVE] ok=false error=location_not_found location={place}"
        match = rows[0]
        latitude = float(match["latitude"])
        longitude = float(match["longitude"])
        forecast = _read_json(
            "https://api.open-meteo.com/v1/forecast?"
            + urlencode({
                "latitude": latitude,
                "longitude": longitude,
                "current": (
                    "temperature_2m,apparent_temperature,relative_humidity_2m,"
                    "precipitation,weather_code,wind_speed_10m"
                ),
                "temperature_unit": "celsius",
                "wind_speed_unit": "kmh",
                "timezone": "auto",
            })
        )
        current = forecast.get("current")
        if not isinstance(current, dict):
            raise ValueError("forecast did not include current conditions")
        result = {
            "ok": True,
            "source": "open-meteo",
            "location": {
                "name": match.get("name") or place,
                "admin1": match.get("admin1") or "",
                "country": match.get("country") or "",
                "latitude": latitude,
                "longitude": longitude,
            },
            "observed_at": current.get("time"),
            "timezone": forecast.get("timezone"),
            "temperature_c": current.get("temperature_2m"),
            "apparent_temperature_c": current.get("apparent_temperature"),
            "relative_humidity_percent": current.get("relative_humidity_2m"),
            "precipitation_mm": current.get("precipitation"),
            "weather_code": current.get("weather_code"),
            "wind_speed_kmh": current.get("wind_speed_10m"),
        }
        return "[WEATHER_LIVE] " + json.dumps(result, ensure_ascii=False, sort_keys=True)
    except Exception as exc:
        return f"[WEATHER_LIVE] ok=false error={type(exc).__name__}: {exc}"


@tool(args_schema=WebSearchArgs, description="Search the web for current information.")
def web_search(
    query: str,
    objective: str = "",
    local_first: bool = False,
    freshness: str = "",
) -> str:
    """Search the web via pluggable providers (DuckDuckGo default; optional Tavily/Brave).

    Free-path engineering: news channel, query variants, empty-result retry,
    thin-snippet URL extract. Cascade controlled by WEB_SEARCH_PROVIDER.
    """
    try:
        from urllib.parse import urlparse
        from agent.web_search_providers import (
            SearchProviderResult,
            format_hits_for_tool,
            run_web_search,
        )

        def _split_queries(q: str) -> list:
            raw = (q or "").strip()
            if not raw:
                return []
            if "\n" in raw:
                parts = [p.strip() for p in raw.splitlines() if p.strip()]
            elif re.search(r"\sOR\s", raw, flags=re.IGNORECASE):
                parts = [p.strip() for p in re.split(r"\s+OR\s+", raw, flags=re.IGNORECASE) if p.strip()]
                if len(parts) <= 1:
                    parts = [raw]
            else:
                parts = [raw]
            out, seen = [], set()
            for p in parts:
                if p.lower() in seen:
                    continue
                seen.add(p.lower())
                out.append(p)
                if len(out) >= 4:
                    break
            return out

        blocked_domains = set(
            str(d).strip().lower().lstrip(".")
            for d in (getattr(config, "web_search_blocked_domains", None) or [])
            if str(d).strip()
        )

        def _url_is_blocked(url: str) -> bool:
            if not blocked_domains:
                return False
            try:
                host = (urlparse(url).netloc or "").lower()
            except Exception:
                return False
            if not host:
                return False
            if ":" in host:
                host = host.split(":", 1)[0]
            host = host.lstrip(".")
            if host.startswith("www."):
                host = host[4:]
            for d in blocked_domains:
                if host == d or host.endswith("." + d):
                    return True
            return False

        queries = _split_queries(query)
        if not queries:
            return "No search results found."

        merged_hits = []
        errors = []
        providers_used = []

        # Search discovery is read-only and has no shared TaskRun/ToolRun
        # authority.  Independent query variants may therefore run in
        # parallel inside this one governed ToolRun, while their results are
        # consumed in original query order for deterministic evidence.
        search_results = []
        if len(queries) == 1:
            search_results = [run_web_search(queries[0], config=config, enrich_extract=True, max_hits=10)]
        else:
            from concurrent.futures import ThreadPoolExecutor

            configured_limit = max(1, int(getattr(config, "research_max_concurrency", 4) or 4))
            with ThreadPoolExecutor(max_workers=min(configured_limit, len(queries))) as executor:
                futures = [
                    executor.submit(run_web_search, q, config=config, enrich_extract=True, max_hits=10)
                    for q in queries
                ]
                search_results = [future.result() for future in futures]

        for res in search_results:
            if res.provider:
                providers_used.append(res.provider)
            errors.extend(res.errors or [])
            for h in res.hits:
                if _url_is_blocked(h.url):
                    continue
                merged_hits.append(h)

        seen = set()
        deduped = []
        for h in merged_hits:
            key = (h.url or h.title).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(h)
            if len(deduped) >= 12:
                break

        if not deduped:
            if errors:
                return errors[0]
            return "No search results found."

        result = SearchProviderResult(
            hits=deduped,
            provider=",".join(dict.fromkeys(providers_used)) or "unknown",
            errors=errors[:5],
            queries_used=queries,
        )
        return format_hits_for_tool(result, multi_query=len(queries) > 1)
    except Exception as e:
        logger.error(f"Web search failed: {e}")
        return f"Search failed: {str(e)}"


@tool(args_schema=AnalyzeScreenArgs, description="Capture the screen, run OCR, and return relevant text.")
def analyze_screen(context: str = "") -> str:
    """
    Capture the screen, perform OCR, and analyze the content.

    Args:
        context: Additional context about what to look for.

    Returns:
        OCR text content from the screen.
    """
    if not VISION_AVAILABLE or not CV2_AVAILABLE:
        return "Screen analysis is not available. Please ensure OpenCV and Pillow are installed."

    try:
        logger.info("Capturing screen for analysis")

        screen_image = capture_screen()
        if screen_image is None:
            return "Failed to capture screen."

        # Desktop screenshots can be very large; downscale to improve VLM reliability.
        screen_image = _resize_for_vision(screen_image, max_dim=1024)

        ocr_text = perform_ocr(screen_image)

        if not ocr_text or not ocr_text.strip():
            return "No text detected on screen."

        if context:
            relevant_text = f"Context: {context}\n\nScreen Text:\n{ocr_text}"
        else:
            relevant_text = ocr_text

        logger.info(f"Screen analysis complete: {len(ocr_text)} characters found")
        return relevant_text

    except Exception as e:
        logger.error(f"Screen analysis failed: {e}")
        return f"Screen analysis failed: {str(e)}"


@tool(args_schema=VisionQaArgs, description="Answer questions about the current screen using a vision model.")
def vision_qa(question: str) -> str:
    """Answer a question about what's currently visible on the screen using a vision-language model (Ollama)."""
    if not VISION_AVAILABLE or not CV2_AVAILABLE:
        return "Vision Q&A is not available. Please ensure OpenCV and Pillow are installed."

    if getattr(config, "local", None) is None or getattr(config.local, "provider", None) != ModelProvider.OLLAMA:
        return "Vision Q&A is only supported for local provider=ollama."

    try:
        import requests

        screen_image = capture_screen()
        if screen_image is None:
            return "Failed to capture screen."

        # Desktop screenshots can be very large; downscale to improve VLM reliability.
        screen_image = _resize_for_vision(screen_image, max_dim=1024)

        img_b64 = _encode_image_b64(screen_image)
        if not img_b64:
            return "Failed to encode screenshot."

        base = (getattr(config.local, "base_url", "") or "").rstrip("/")
        if not base:
            base = "http://localhost:11434"

        model = getattr(config.local, "model_name", "") or ""
        if not model:
            return "No LOCAL_MODEL_NAME configured for Ollama."

        q_raw = (question or "").strip()
        q, ctx = _split_live_desktop_context(q_raw)
        q = q.strip() or "Describe what is on the screen."

        # Some VLM chat templates require an explicit image token.
        if "<image>" not in q.lower():
            q_for_model = f"<image>\n{q}"
        else:
            q_for_model = q

        # If monitor OCR was appended, keep only a tiny excerpt as optional aid.
        ctx_excerpt = ""
        if ctx:
            ctx_excerpt = re.sub(r"\s+", " ", ctx).strip()
            if len(ctx_excerpt) > 400:
                ctx_excerpt = ctx_excerpt[:400].rstrip() + "…"

        system_prompt = (
            "You are a vision assistant looking at the user's desktop screenshot. "
            "Respond like a human: give a short summary focused on the main active window and what's important. "
            "Do NOT enumerate every icon or UI element. "
            "Keep it under 2 short sentences, and optionally up to 3 bullets for key details. "
            "No headings, no section numbers. "
            "Always respond with at least one sentence."
        )

        num_predict = 250
        try:
            max_tokens = int(getattr(config.local, "max_tokens", 0) or 0)
            if max_tokens > 0:
                num_predict = min(max_tokens, 350)
        except Exception:
            pass

        payload = {
            "model": model,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                *(
                    [
                        {
                            "role": "user",
                            "content": f"OCR excerpt (may be incomplete): {ctx_excerpt}",
                        }
                    ]
                    if ctx_excerpt
                    else []
                ),
                {
                    "role": "user",
                    "content": q_for_model,
                    "images": [img_b64],
                }
            ],
            "options": {
                "temperature": float(getattr(config.local, "temperature", 0.2) or 0.2),
                "num_predict": num_predict,
            },
        }

        resp = requests.post(f"{base}/api/chat", json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json() or {}
        if isinstance(data, dict) and data.get("error"):
            return f"Vision Q&A failed: {str(data.get('error'))}"

        out_raw = ((data.get("message") or {}).get("content") or data.get("response") or "").strip()
        out = _clamp_vision_summary(out_raw, max_chars=700)
        if out:
            return out

        logger.warning(
            "Vision model returned empty content (first attempt). Keys={} ",
            list(data.keys()) if isinstance(data, dict) else str(type(data)),
        )
        try:
            dump = ""
            if isinstance(data, dict):
                dump = json.dumps(data, ensure_ascii=False)[:1200]
            else:
                dump = str(data)[:1200]
            if dump:
                logger.warning("Ollama /api/chat empty response payload (truncated): {}", dump)
        except Exception:
            pass

        # Retry once with a simpler prompt and without OCR context.
        retry_payload = {
            "model": model,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": "<image>\nIn one short sentence, describe the main active window and what is happening on the screen.",
                    "images": [img_b64],
                },
            ],
            "options": {
                "temperature": float(getattr(config.local, "temperature", 0.2) or 0.2),
                "num_predict": num_predict,
            },
        }

        resp2 = requests.post(f"{base}/api/chat", json=retry_payload, timeout=120)
        resp2.raise_for_status()
        data2 = resp2.json() or {}
        if isinstance(data2, dict) and data2.get("error"):
            return f"Vision Q&A failed: {str(data2.get('error'))}"

        out2_raw = ((data2.get("message") or {}).get("content") or data2.get("response") or "").strip()
        out2 = _clamp_vision_summary(out2_raw, max_chars=700)
        if out2:
            return out2

        logger.warning(
            "Vision model returned empty content (second attempt). Keys={} ",
            list(data2.keys()) if isinstance(data2, dict) else str(type(data2)),
        )
        try:
            dump2 = ""
            if isinstance(data2, dict):
                dump2 = json.dumps(data2, ensure_ascii=False)[:1200]
            else:
                dump2 = str(data2)[:1200]
            if dump2:
                logger.warning("Ollama /api/chat empty response payload (truncated): {}", dump2)
        except Exception:
            pass

        # Compatibility fallback: some models respond better to /api/generate for multimodal.
        try:
            gen_prompt = f"<image>\n{q}"
            gen_payload = {
                "model": model,
                "prompt": gen_prompt,
                "images": [img_b64],
                "stream": False,
                "options": {
                    "temperature": float(getattr(config.local, "temperature", 0.2) or 0.2),
                    "num_predict": num_predict,
                },
            }
            gen_resp = requests.post(f"{base}/api/generate", json=gen_payload, timeout=120)
            gen_resp.raise_for_status()
            gen_data = gen_resp.json() or {}
            if isinstance(gen_data, dict) and gen_data.get("error"):
                return f"Vision Q&A failed: {str(gen_data.get('error'))}"
            gen_text = (gen_data.get("response") or gen_data.get("message") or "").strip()
            gen_out = _clamp_vision_summary(gen_text, max_chars=700)
            if gen_out:
                return gen_out
            if gen_text:
                return gen_text[:700].strip() or "(no response)"
        except Exception as e:
            logger.warning("Vision /api/generate fallback failed: {}", str(e))

        # Last-resort fallback: use OCR so the user doesn't get an empty response.
        try:
            ocr = perform_ocr(screen_image)
            ocr = re.sub(r"\s+", " ", (ocr or "")).strip()
            if ocr:
                if len(ocr) > 500:
                    ocr = ocr[:500].rstrip() + "…"
                return f"I couldn't get a vision-model response, but OCR sees: {ocr}"
        except Exception:
            pass
        if out_raw:
            return out_raw[:700].strip() or "(no response)"
        if out2_raw:
            return out2_raw[:700].strip() or "(no response)"
        return "Vision model returned an empty response. Try asking again."
    except Exception as e:
        logger.error(f"Vision Q&A failed: {e}")
        return f"Vision Q&A failed: {str(e)}"


@tool
def get_system_time() -> str:
    """
    Get the current system time and date.

    Returns:
        Formatted current date and time.
    """
    from datetime import datetime

    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")


class CalculateArgs(BaseModel):
    expression: str = Field(
        ...,
        validation_alias=AliasChoices("expression", "expr", "equation", "input"),
        description="Mathematical expression to evaluate.",
    )


@tool(args_schema=CalculateArgs)
def calculate(expression: str) -> str:
    """
    Perform a mathematical calculation.

    Args:
        expression: Mathematical expression to evaluate.

    Returns:
        Result of the calculation.
    """
    try:
        import math

        safe_dict = {
            'abs': abs, 'max': max, 'min': min, 'pow': pow,
            'round': round, 'sum': sum, 'len': len,
            'sqrt': math.sqrt, 'sin': math.sin, 'cos': math.cos,
            'tan': math.tan, 'log': math.log, 'log10': math.log10,
            'pi': math.pi, 'e': math.e
        }

        result = eval(expression, {"__builtins__": {}}, safe_dict)
        return str(result)
    except Exception as e:
        return f"Calculation error: {str(e)}"


class YouTubeTranscriptArgs(BaseModel):
    url: str = Field(
        ...,
        validation_alias=AliasChoices("url", "video_url", "link"),
        description="YouTube video URL.",
    )
    language: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("language", "lang"),
        description="Optional language code (e.g., 'en').",
    )


def _extract_youtube_video_id(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""

    m = re.search(r"(?:youtu\.be/)([A-Za-z0-9_-]{6,})", u)
    if m:
        return m.group(1)
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", u)
    if m:
        return m.group(1)
    m = re.search(r"/shorts/([A-Za-z0-9_-]{6,})", u)
    if m:
        return m.group(1)
    m = re.search(r"/embed/([A-Za-z0-9_-]{6,})", u)
    if m:
        return m.group(1)
    return ""


@tool(args_schema=YouTubeTranscriptArgs, description="Fetch a YouTube video transcript as plain text.")
def youtube_transcript(url: str, language: Optional[str] = None) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
    except Exception:
        return "YouTube transcript is not available. Please install youtube-transcript-api."

    video_id = _extract_youtube_video_id(url)
    if not video_id:
        return "Invalid YouTube URL."

    try:
        langs = [language] if (language or "").strip() else None
        if langs is None:
            items = YouTubeTranscriptApi.get_transcript(video_id)
        else:
            items = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
        text = " ".join([(x.get("text") or "").strip() for x in (items or []) if (x.get("text") or "").strip()])
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return "No transcript text was returned."
        if len(text) > 20000:
            return text[:20000].rstrip() + "…"
        return text
    except Exception as e:
        return f"Failed to fetch transcript: {str(e)}"


class BrowseTaskArgs(BaseModel):
    url: str = Field(
        ...,
        validation_alias=AliasChoices("url", "link", "website"),
        description="The URL to browse.",
    )
    task: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("task", "goal", "instructions"),
        description="Optional task/instructions for the browsing operation.",
    )


class SafeWebFetchArgs(BaseModel):
    url: str = Field(
        ...,
        validation_alias=AliasChoices("url", "link", "source_url"),
        description="A public HTTP or HTTPS source URL discovered during research.",
    )
    objective: str = Field(
        default="",
        validation_alias=AliasChoices("objective", "goal", "question"),
        description="The unresolved information field this page should help answer.",
    )
    max_text_chars: int = Field(default=24000, ge=1000, le=40000)


@tool(
    args_schema=SafeWebFetchArgs,
    description=(
        "Safely fetch and extract a public webpage without browser cookies or interaction. "
        "Use after search discovery when snippets do not contain the requested information. "
        "Returns visible text plus bounded JSON-LD, microdata/RDFa attributes, metadata, and tables."
    ),
)
def safe_web_fetch(url: str, objective: str = "", max_text_chars: int = 24000) -> str:
    del objective  # Runtime requirement state owns the objective and evidence binding.
    try:
        from agent.safe_web_retrieval import SafeWebRetrievalError, fetch_public_page

        result = fetch_public_page(url, max_text_chars=max_text_chars)
        return result.tool_text()
    except SafeWebRetrievalError as exc:
        return (
            "execution_status=error\nresult_state=insufficient_evidence\n"
            f"safe_fetch_error={exc.code}: {exc}"
        )
    except Exception as exc:
        return (
            "execution_status=error\nresult_state=insufficient_evidence\n"
            f"safe_fetch_error={type(exc).__name__}: {exc}"
        )


@tool(args_schema=BrowseTaskArgs, description="Browse a URL with Playwright and return extracted page text.")
def browse_task(url: str, task: Optional[str] = None) -> str:
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_playwright", False):
        return "System actions are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_PLAYWRIGHT=true, then restart the API."

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return "Playwright is not available. Please install playwright and run 'playwright install'."

    target = (url or "").strip().strip("\"' ")
    if not target:
        return "Invalid URL."
    if target.startswith("www."):
        target = "https://" + target
    if not re.match(r"^https?://", target, flags=re.IGNORECASE):
        if re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:/\S*)?$", target, flags=re.IGNORECASE):
            target = "https://" + target
        else:
            return "Invalid URL."

    goal = (task or "").strip()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(target, wait_until="domcontentloaded", timeout=45000)
            title = ""
            try:
                title = page.title() or ""
            except Exception:
                title = ""
            try:
                body_text = page.inner_text("body")
            except Exception:
                body_text = page.content() or ""
            browser.close()

        body_text = re.sub(r"\s+", " ", (body_text or "")).strip()
        if len(body_text) > 12000:
            body_text = body_text[:12000].rstrip() + "…"

        if goal:
            return f"Title: {title}\nURL: {target}\nTask: {goal}\n\nContent:\n{body_text}"
        return f"Title: {title}\nURL: {target}\n\nContent:\n{body_text}"
    except Exception as e:
        return f"Browse failed: {str(e)}"


class DiscordWebSendArgs(BaseModel):
    url: str = Field(
        default="",
        validation_alias=AliasChoices("url", "channel_url", "dm_url", "link"),
        description="Discord channel/DM URL (ex: https://discord.com/channels/<guild>/<channel>).",
    )
    recipient: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("recipient", "to", "user", "person", "name"),
        description="Optional recipient key to resolve via DISCORD_CONTACTS_JSON / DISCORD_CONTACTS_PATH (ex: mayo).",
    )
    message: str = Field(
        ...,
        validation_alias=AliasChoices("message", "content", "text", "msg"),
        description="Message content to send.",
    )
    headless: bool = Field(
        default=False,
        validation_alias=AliasChoices("headless", "hidden"),
        description="Run browser headless. If false, the window may appear and can be used for login.",
    )


class DiscordWebReadRecentArgs(BaseModel):
    url: str = Field(
        default="",
        validation_alias=AliasChoices("url", "channel_url", "dm_url", "link"),
        description="Discord channel/DM URL (ex: https://discord.com/channels/<guild>/<channel>).",
    )
    recipient: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("recipient", "to", "user", "person", "name"),
        description="Optional recipient key to resolve via DISCORD_CONTACTS_JSON / DISCORD_CONTACTS_PATH (ex: mayo).",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=50,
        validation_alias=AliasChoices("limit", "n", "count"),
        description="Number of most recent messages to return (max 50).",
    )
    headless: bool = Field(
        default=True,
        validation_alias=AliasChoices("headless", "hidden"),
        description="Run browser headless. If false, the window may appear and can be used for login.",
    )


class DiscordContactsAddArgs(BaseModel):
    key: str = Field(
        ...,
        validation_alias=AliasChoices("key", "name", "recipient", "user", "to"),
        description="Contact key to add/update (ex: mayo).",
    )
    url: str = Field(
        default="",
        validation_alias=AliasChoices("url", "link", "dm_url", "channel_url"),
        description="Discord DM/channel URL to store for this key (ex: https://discord.com/channels/@me/<dm_id>).",
    )
    message_link: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("message_link", "message_url", "copy_link", "message"),
        description="Optional Discord 'Copy Message Link' URL. If provided, the tool will derive the channel/DM URL automatically.",
    )


class DiscordContactsDiscoverArgs(BaseModel):
    key: str = Field(
        ...,
        validation_alias=AliasChoices("key", "name", "recipient", "user", "to"),
        description="Contact name to discover (ex: mayo).",
    )
    headless: bool = Field(
        default=False,
        validation_alias=AliasChoices("headless", "hidden"),
        description="Run browser headless. If false, the window may appear and can be used for login.",
    )


@tool(args_schema=DiscordContactsAddArgs, description="Add/update a Discord contact mapping (recipient -> DM/channel URL).")
def discord_contacts_add(key: str, url: str = "", message_link: Optional[str] = None) -> str:
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_file_write", False):
        return "Discord contacts update is disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_FILE_WRITE=true, then restart the API."

    k = (key or "").strip()
    v = (url or "").strip().strip('"\' ')
    if not k:
        return "Missing contact key."

    if (message_link or "").strip() and not v:
        ml = str(message_link or "").strip().strip('"\' ')
        m = re.match(r"^https?://(?:www\.)?discord\.com/channels/([^/]+)/([^/]+)(?:/([^/?#]+))?", ml, flags=re.IGNORECASE)
        if not m:
            return "Invalid Discord message link. Expected a discord.com/channels/... URL."
        guild = (m.group(1) or "").strip()
        channel = (m.group(2) or "").strip()
        if not guild or not channel:
            return "Invalid Discord message link."
        v = f"https://discord.com/channels/{guild}/{channel}"

    if not v:
        return "Missing Discord URL (or message_link)."
    if not re.match(r"^https?://(?:www\.)?discord\.com/", v, flags=re.IGNORECASE):
        return "Invalid Discord URL. Expected a discord.com URL."

    contacts_path = (os.getenv("DISCORD_CONTACTS_PATH", "") or "").strip()
    if not contacts_path:
        root = Path(getattr(config, "artifacts_dir", "") or "").expanduser()
        if not str(root).strip():
            root = Path(DATA_DIR) / "artifacts"
        contacts_path = str(root.parent / "discord_contacts.json")

    p = Path(contacts_path).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    data: dict = {}
    try:
        if p.exists():
            loaded = json.loads(p.read_text(encoding="utf-8") or "{}")
            if isinstance(loaded, dict):
                data = loaded
    except Exception:
        data = {}

    data[k] = v
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return f"Saved Discord contact '{k}' -> {v}"
    except Exception as e:
        return f"Failed to write contacts: {str(e)}"


@tool(args_schema=DiscordContactsDiscoverArgs, description="Discover a Discord contact automatically via Playwright by searching for the user and extracting a DM message link.")
def discord_contacts_discover(key: str, headless: bool = False) -> str:
    """Automatically discover a Discord contact by navigating Discord Web UI.

    Flow:
    1. Open Discord Web with persistent profile
    2. Check if logged in
    3. Use search (Ctrl+K) to find user
    4. Open DM channel
    5. Find a message, right-click, and extract "Copy Message Link"
    6. Derive channel URL and save to contacts

    Args:
        key: Contact name to search for.
        headless: Run browser headless.

    Returns:
        Status message with discovered URL or error.
    """
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_playwright", False):
        return "System actions are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_PLAYWRIGHT=true, then restart the API."

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return "Playwright is not available. Please install playwright and run 'playwright install'."

    # Ensure Playwright browsers are installed
    _ensure_playwright_browsers()

    k = (key or "").strip()
    if not k:
        return "Missing contact name."

    root = Path(getattr(config, "artifacts_dir", "") or "").expanduser()
    if not str(root).strip():
        root = Path(DATA_DIR) / "artifacts"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    profile_dir = Path(os.getenv("DISCORD_PLAYWRIGHT_PROFILE_DIR", str(root.parent / "discord_profile"))).expanduser()
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=bool(headless),
            )
            page = context.new_page()

            # Navigate to Discord
            page.goto("https://discord.com/app", wait_until="domcontentloaded", timeout=60000)

            # Check for login page
            try:
                if page.locator('input[name="email"]').count() > 0 or page.locator('input[type="email"]').count() > 0:
                    context.close()
                    return (
                        "Discord Web is not logged in. Re-run with headless=false, log in once in the opened browser, then retry. "
                        f"Profile dir: {profile_dir}"
                    )
            except Exception:
                pass

            # Wait for Discord to load
            try:
                page.wait_for_selector('div[class*="sidebar"]', timeout=30000)
            except Exception:
                context.close()
                return "Discord UI did not load properly. Try again or run with headless=false."

            # Open quick switcher (Ctrl+K)
            page.keyboard.press("Control+K")
            page.wait_for_timeout(500)  # Wait for quick switcher to open

            # Type the username to search directly (focus is automatically on the input)
            page.keyboard.type(k, delay=50)
            page.wait_for_timeout(1500)  # Wait for results

            # Look for user in results - try to find a DM or user entry
            # Discord's quick switcher shows users with @ prefix or DM icon
            found = False
            dm_url = None

            # Try clicking on the first result that looks like a user/DM
            try:
                # Results appear in a list - look for clickable items
                results = page.locator('div[role="option"], div[class*="result"]').all()
                for i, result in enumerate(results[:5]):  # Check first 5 results
                    text = result.inner_text(timeout=500) or ""
                    # Look for the username in the result text
                    if k.lower() in text.lower():
                        result.click()
                        found = True
                        page.wait_for_timeout(1000)
                        break
            except Exception:
                pass

            if not found:
                # Try pressing Enter to select first result
                try:
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(1000)
                    found = True
                except Exception:
                    pass

            if not found:
                context.close()
                return f"Could not find user '{k}' in Discord search results."

            # Now we should be in the DM - wait for the page to navigate/render
            page.wait_for_timeout(2000)

            current_url = page.url
            if "discord.com/channels/@me/" in current_url:
                dm_url = current_url.split("?")[0].split("#")[0]  # Clean URL
            else:
                # Try to extract from the page
                try:
                    # Look for message elements and wait for at least one to be visible
                    try:
                        page.wait_for_selector('div[class*="message"]', timeout=5000)
                    except Exception:
                        pass
                        
                    messages = page.locator('div[class*="message"]').all()
                    if len(messages) == 0:
                        context.close()
                        return f"Found user '{k}' but no messages in DM. Send a message first, then retry."

                    # Right-click on the first message
                    first_msg = messages[0]
                    first_msg.click(button="right")
                    page.wait_for_timeout(1000)

                    # Look for "Copy Message Link" option in context menu
                    context_menu = page.locator('div[id*="menu"], div[class*="menu"]').first
                    try:
                        context_menu.wait_for(timeout=2000)
                    except Exception:
                        context.close()
                        return "Failed to open message context menu."

                    # Find and click "Copy Message Link"
                    copy_link_option = None
                    menu_items = context_menu.locator('div[role="menuitem"]').all()
                    for item in menu_items:
                        text = item.inner_text(timeout=200) or ""
                        if "copy message link" in text.lower():
                            copy_link_option = item
                            break

                    if copy_link_option:
                        copy_link_option.click()
                        page.wait_for_timeout(500)

                        # Read clipboard - this requires special handling
                        # Discord copies the link to clipboard, we need to read it
                        # Use a workaround: paste into a text area
                        page.evaluate("navigator.clipboard.readText().then(t => window.__discord_clipboard = t)")
                        page.wait_for_timeout(300)
                        clipboard_text = page.evaluate("window.__discord_clipboard") or ""

                        if clipboard_text and "discord.com/channels/" in clipboard_text:
                            # Parse the message link to get DM URL
                            m = re.match(r"^https?://(?:www\.)?discord\.com/channels/([^/]+)/([^/]+)", clipboard_text, flags=re.IGNORECASE)
                            if m:
                                guild = (m.group(1) or "").strip()
                                channel = (m.group(2) or "").strip()
                                dm_url = f"https://discord.com/channels/{guild}/{channel}"
                        else:
                            # Alternative: get URL from address bar after clicking message
                            dm_url = page.url.split("?")[0].split("#")[0]
                    else:
                        # Fallback: just use current URL
                        dm_url = page.url.split("?")[0].split("#")[0]
                except Exception as e:
                    context.close()
                    return f"Failed to extract message link: {str(e)}"

            context.close()

            if not dm_url or "discord.com/channels" not in dm_url:
                return f"Failed to discover DM URL for '{k}'."

            # Now save to contacts using the same logic as discord_contacts_add
            contacts_path = (os.getenv("DISCORD_CONTACTS_PATH", "") or "").strip()
            if not contacts_path:
                contacts_path = str(root.parent / "discord_contacts.json")

            p = Path(contacts_path).expanduser()
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

            data: dict = {}
            try:
                if p.exists():
                    loaded = json.loads(p.read_text(encoding="utf-8") or "{}")
                    if isinstance(loaded, dict):
                        data = loaded
            except Exception:
                data = {}

            data[k] = dm_url
            try:
                p.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                return f"Discovered and saved Discord contact '{k}' -> {dm_url}"
            except Exception as e:
                return f"Discovered DM URL {dm_url} but failed to save: {str(e)}"

    except Exception as e:
        return f"Discord contact discovery failed: {str(e)}"


class DiscordReadChannelArgs(BaseModel):
    channel: str = Field(
        ...,
        validation_alias=AliasChoices("channel", "channel_name", "name", "channel_id"),
        description="Discord channel name (e.g., 'general') or channel ID to read messages from.",
    )
    server: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("server", "guild", "guild_id", "server_name"),
        description="Optional server/guild name or ID. If not provided, uses the first available server.",
    )
    limit: int = Field(
        default=25,
        ge=1,
        le=100,
        validation_alias=AliasChoices("limit", "n", "count"),
        description="Number of most recent messages to return (max 100).",
    )


@tool(args_schema=DiscordReadChannelArgs, description="Read recent messages from a Discord server channel. CALL THIS TOOL whenever the user asks about Discord messages, what people are saying on Discord, or to check/read a Discord channel. The bot must be running. Parameters: channel (e.g., 'general' or 'updates'), optional server name, limit (default 25). Returns recent human messages with author names.")
def discord_read_channel(channel: str, server: Optional[str] = None, limit: int = 25) -> str:
    """
    Read recent messages from a Discord channel via the bot's direct API access.
    This works from the Web UI without needing browser automation.
    """
    # Check if Discord bot is enabled
    if not getattr(config, "allow_discord_bot", False):
        return "Discord bot is not enabled. To enable: set ALLOW_DISCORD_BOT=true and configure DISCORD_BOT_TOKEN, then restart the API."

    # Get the bot instance
    try:
        from discord_bot import get_bot
        bot = get_bot()
    except ImportError:
        return "Discord bot module not available."
    
    if bot is None or not bot.is_running():
        return "Discord bot is not running. Make sure the bot is started and connected to Discord."
    
    client = getattr(bot, "client", None)
    if client is None:
        return "Discord bot client not available."
    try:
        if hasattr(client, "is_ready") and not client.is_ready():
            return "Discord bot is still connecting. Try again in a few seconds."
    except Exception:
        pass

    try:
        import asyncio
        import discord
        import concurrent.futures

        read_timeout_seconds = 6.0

        # Find the target guild (server)
        guild = None
        if server:
            # Try to find by ID first
            if server.isdigit():
                guild = client.get_guild(int(server))
            # Then by name
            if guild is None:
                guild = next((g for g in client.guilds if (g.name or "").lower() == server.lower()), None)
        else:
            # Use first available guild
            guild = next(iter(client.guilds), None) if client.guilds else None

        if guild is None:
            available = [g.name for g in client.guilds] if client.guilds else []
            if available:
                return f"Server '{server}' not found. Available servers: {', '.join(available)}"
            return "The bot is not connected to any Discord servers."

        # Find the target channel
        target_channel = None
        channel_lower = (channel or "").lower().strip()
        
        # Remove # prefix if present
        if channel_lower.startswith("#"):
            channel_lower = channel_lower[1:]
        
        # Try to find by ID first
        if channel.isdigit():
            target_channel = guild.get_channel(int(channel))
        
        # Helper to strip emojis and special chars for fuzzy matching
        def strip_emojis(name: str) -> str:
            # Discord channel names may start with emojis/symbols like "💬-general".
            # For fuzzy matching, drop leading non-alphanumeric characters and normalize separators.
            cleaned = (name or "").lower().strip()
            cleaned = re.sub(r"^[^a-z0-9]+", "", cleaned)
            cleaned = re.sub(r"[-_]", " ", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            return cleaned
        
        # Then by exact name match
        if target_channel is None:
            for ch in guild.text_channels:
                if (ch.name or "").lower() == channel_lower:
                    target_channel = ch
                    break
        
        # Fuzzy match: channel name contains the search term
        if target_channel is None:
            for ch in guild.text_channels:
                ch_name_lower = (ch.name or "").lower()
                if channel_lower in ch_name_lower:
                    target_channel = ch
                    break
        
        # Fuzzy match: search term contains the channel name (after stripping emojis)
        if target_channel is None:
            for ch in guild.text_channels:
                stripped = strip_emojis(ch.name or "")
                if stripped and (channel_lower in stripped or stripped in channel_lower):
                    target_channel = ch
                    break
        
        # Fuzzy match: stripped channel name equals search term
        if target_channel is None:
            for ch in guild.text_channels:
                stripped = strip_emojis(ch.name or "")
                if stripped == channel_lower:
                    target_channel = ch
                    break

        if target_channel is None:
            available = [f"#{ch.name}" for ch in guild.text_channels]
            return f"Channel '{channel}' not found in server '{guild.name}'. Available channels: {', '.join(available[:10])}"

        if not isinstance(target_channel, discord.TextChannel):
            return f"'{channel}' is not a text channel."

        async def _collect_messages():
            lines = []
            max_chars = 3000
            total_chars = 0
            async for msg in target_channel.history(limit=limit, oldest_first=False):
                if getattr(msg, "author", None) is None:
                    continue
                if getattr(msg.author, "bot", False):
                    continue
                text = (msg.content or "").strip()
                if not text:
                    continue
                author = getattr(msg.author, "display_name", None) or getattr(msg.author, "name", "unknown")
                # Basic cleanup
                text_clean = re.sub(r"\s+", " ", text)
                line = f"- {author}: {text_clean}"
                lines.append(line)
                total_chars += len(line)
                if total_chars > max_chars:
                    break
            return lines

        async def _fetch_messages():
            return await asyncio.wait_for(_collect_messages(), timeout=read_timeout_seconds)

        # Run the coroutine on the Discord client's event loop.
        # The agent runs in a worker thread (via run_in_executor), so we need to
        # schedule the coroutine on the Discord client's loop using run_coroutine_threadsafe.
        # Note: client.loop is a sentinel in discord.py 2.0+, so we use bot.get_loop() instead.
        bot_loop = bot.get_loop() if bot else None
        
        if bot_loop is not None:
            try:
                if hasattr(bot_loop, "is_closed") and bot_loop.is_closed():
                    return "Discord bot event loop is unavailable right now."
                if hasattr(bot_loop, "is_running") and not bot_loop.is_running():
                    return "Discord bot loop is not running right now."
            except Exception:
                pass
            # We're in a worker thread, schedule on the bot's loop
            try:
                fut = asyncio.run_coroutine_threadsafe(_fetch_messages(), bot_loop)
                lines = fut.result(timeout=read_timeout_seconds + 1.0)
            except concurrent.futures.TimeoutError:
                try:
                    fut.cancel()
                except Exception:
                    pass
                return f"Timed out reading messages from #{target_channel.name} on '{guild.name}' after {int(read_timeout_seconds)} seconds."
            except Exception as e:
                if isinstance(e, asyncio.TimeoutError):
                    try:
                        fut.cancel()
                    except Exception:
                        pass
                    return f"Timed out reading messages from #{target_channel.name} on '{guild.name}' after {int(read_timeout_seconds)} seconds."
                logger.error(f"Discord read channel coroutine error: {e}")
                return f"Failed to read Discord channel: {str(e)}"
        else:
            # Fallback: try to get the running loop
            try:
                loop = asyncio.get_running_loop()
                fut = asyncio.run_coroutine_threadsafe(_fetch_messages(), loop)
                lines = fut.result(timeout=read_timeout_seconds + 1.0)
            except RuntimeError:
                # No running loop, create one
                lines = asyncio.run(_fetch_messages())
            except concurrent.futures.TimeoutError:
                try:
                    fut.cancel()
                except Exception:
                    pass
                return f"Timed out reading messages from #{target_channel.name} on '{guild.name}' after {int(read_timeout_seconds)} seconds."
            except Exception as e:
                if isinstance(e, asyncio.TimeoutError):
                    try:
                        fut.cancel()
                    except Exception:
                        pass
                    return f"Timed out reading messages from #{target_channel.name} on '{guild.name}' after {int(read_timeout_seconds)} seconds."
                logger.error(f"Discord read channel fallback error: {e}")
                return f"Failed to read Discord channel: {str(e)}"

        if not lines:
            return f"No recent human messages found in #{target_channel.name} on '{guild.name}'."

        return f"Recent messages in #{target_channel.name} on '{guild.name}' (most recent first):\n" + "\n".join(lines)

    except Exception as e:
        logger.error(f"Discord read channel error: {e}")
        return f"Failed to read Discord channel: {str(e)}"


class DiscordSendChannelArgs(BaseModel):
    channel: str = Field(
        ...,
        validation_alias=AliasChoices("channel", "channel_name", "name", "channel_id"),
        description="Discord channel name (e.g., 'general') or channel ID to send message to.",
    )
    message: str = Field(
        ...,
        validation_alias=AliasChoices("message", "content", "text", "msg"),
        description="Message content to send to the channel.",
    )
    server: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("server", "guild", "guild_id", "server_name"),
        description="Optional server/guild name or ID. If not provided, uses the first available server.",
    )


@tool(args_schema=DiscordSendChannelArgs, description="Send a message to a Discord server channel. CALL THIS TOOL whenever the user asks to post, send, announce, or say something on Discord. The message is sent as the bot account. Parameters: channel (e.g., 'general'), message content, optional server name.")
def discord_send_channel(channel: str, message: str, server: Optional[str] = None) -> str:
    """
    Send a message to a Discord channel via the bot's direct API access.
    This works from the Web UI without needing browser automation.
    The message is sent as the bot, not as the user's personal account.
    """
    # Check if Discord bot is enabled
    if not getattr(config, "allow_discord_bot", False):
        return "Discord bot is not enabled. To enable: set ALLOW_DISCORD_BOT=true and configure DISCORD_BOT_TOKEN, then restart the API."

    # Get the bot instance
    try:
        from discord_bot import get_bot
        bot = get_bot()
    except ImportError:
        return "Discord bot module not available."
    
    if bot is None or not bot.is_running():
        return "Discord bot is not running. Make sure the bot is started and connected to Discord."
    
    client = getattr(bot, "client", None)
    if client is None:
        return "Discord bot client not available."

    # Validate message
    msg = (message or "").strip()
    if not msg:
        return "Message content is required."
    
    if len(msg) > 2000:
        msg = msg[:1997] + "..."

    try:
        import asyncio
        import discord

        # Find the target guild (server)
        guild = None
        if server:
            # Try to find by ID first
            if server.isdigit():
                guild = client.get_guild(int(server))
            # Then by name
            if guild is None:
                guild = next((g for g in client.guilds if (g.name or "").lower() == server.lower()), None)
        else:
            # Use first available guild
            guild = next(iter(client.guilds), None) if client.guilds else None

        if guild is None:
            available = [g.name for g in client.guilds] if client.guilds else []
            if available:
                return f"Server '{server}' not found. Available servers: {', '.join(available)}"
            return "The bot is not connected to any Discord servers."

        # Find the target channel
        target_channel = None
        channel_lower = (channel or "").lower().strip()
        
        # Remove # prefix if present
        if channel_lower.startswith("#"):
            channel_lower = channel_lower[1:]
        
        # Try to find by ID first
        if channel.isdigit():
            target_channel = guild.get_channel(int(channel))
        
        # Helper to strip emojis and special chars for fuzzy matching
        def strip_emojis(name: str) -> str:
            # Discord channel names may start with emojis/symbols like "💬-general".
            # For fuzzy matching, drop leading non-alphanumeric characters and normalize separators.
            cleaned = (name or "").lower().strip()
            cleaned = re.sub(r"^[^a-z0-9]+", "", cleaned)
            cleaned = re.sub(r"[-_]", " ", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            return cleaned
        
        # Then by exact name match
        if target_channel is None:
            for ch in guild.text_channels:
                if (ch.name or "").lower() == channel_lower:
                    target_channel = ch
                    break
        
        # Fuzzy match: channel name contains the search term
        if target_channel is None:
            for ch in guild.text_channels:
                ch_name_lower = (ch.name or "").lower()
                if channel_lower in ch_name_lower:
                    target_channel = ch
                    break
        
        # Fuzzy match: search term contains the channel name (after stripping emojis)
        if target_channel is None:
            for ch in guild.text_channels:
                stripped = strip_emojis(ch.name or "")
                if stripped and (channel_lower in stripped or stripped in channel_lower):
                    target_channel = ch
                    break
        
        # Fuzzy match: stripped channel name equals search term
        if target_channel is None:
            for ch in guild.text_channels:
                stripped = strip_emojis(ch.name or "")
                if stripped == channel_lower:
                    target_channel = ch
                    break

        if target_channel is None:
            available = [f"#{ch.name}" for ch in guild.text_channels]
            return f"Channel '{channel}' not found in server '{guild.name}'. Available channels: {', '.join(available[:10])}"

        if not isinstance(target_channel, discord.TextChannel):
            return f"'{channel}' is not a text channel."

        async def _send_message():
            await target_channel.send(msg)
            return True

        # Run the coroutine on the Discord client's event loop.
        # The agent runs in a worker thread (via run_in_executor), so we need to
        # schedule the coroutine on the Discord client's loop using run_coroutine_threadsafe.
        # Note: client.loop is a sentinel in discord.py 2.0+, so we use bot.get_loop() instead.
        bot_loop = bot.get_loop() if bot else None
        
        if bot_loop is not None:
            # We're in a worker thread, schedule on the bot's loop
            import concurrent.futures
            try:
                fut = asyncio.run_coroutine_threadsafe(_send_message(), bot_loop)
                fut.result(timeout=30)
            except concurrent.futures.TimeoutError:
                fut.cancel()
                return f"Timed out sending message to #{target_channel.name} on '{guild.name}'."
            except Exception as e:
                logger.error(f"Discord send channel coroutine error: {e}")
                err = str(e) or ""
                if "403" in err and "Missing Permissions" in err:
                    return (
                        "Failed to send Discord message: 403 Forbidden (Missing Permissions). "
                        "This happens in the Web UI too because it uses the same Discord bot API. "
                        "Fix: in your Discord server, open #"
                        f"{target_channel.name} -> Edit Channel -> Permissions, and ensure the bot role has: "
                        "View Channel, Send Messages, and Read Message History. Also check role hierarchy: the bot's role must be above any roles it needs to interact with."
                    )
                return f"Failed to send Discord message: {err}"
        else:
            # Fallback: try to get the running loop
            try:
                loop = asyncio.get_running_loop()
                import concurrent.futures
                fut = asyncio.run_coroutine_threadsafe(_send_message(), loop)
                fut.result(timeout=30)
            except RuntimeError:
                # No running loop, create one
                asyncio.run(_send_message())
            except Exception as e:
                logger.error(f"Discord send channel fallback error: {e}")
                err = str(e) or ""
                if "403" in err and "Missing Permissions" in err:
                    return (
                        "Failed to send Discord message: 403 Forbidden (Missing Permissions). "
                        "This happens in the Web UI too because it uses the same Discord bot API. "
                        "Fix: in your Discord server, open #"
                        f"{target_channel.name} -> Edit Channel -> Permissions, and ensure the bot role has: "
                        "View Channel, Send Messages, and Read Message History. Also check role hierarchy: the bot's role must be above any roles it needs to interact with."
                    )
                return f"Failed to send Discord message: {err}"

        return f"Message sent to #{target_channel.name} on '{guild.name}' via bot account."

    except Exception as e:
        logger.error(f"Discord send channel error: {e}")
        return f"Failed to send message to Discord channel: {str(e)}"


@tool(args_schema=DiscordWebReadRecentArgs, description="Read recent Discord messages from a DM or channel using Playwright browser automation. USE THIS TOOL when the user asks to read/check/view Discord messages and discord_read_channel is not available. Provide either a Discord channel/DM URL or a recipient key from saved contacts. Returns the last N messages with author and content.")
def discord_web_read_recent(url: str = "", recipient: Optional[str] = None, limit: int = 20, headless: bool = True) -> str:
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_playwright", False):
        return "System actions are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_PLAYWRIGHT=true, then restart the API."

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return "Playwright is not available. Please install playwright and run 'playwright install'."

    # Ensure Playwright browsers are installed
    _ensure_playwright_browsers()

    root = Path(getattr(config, "artifacts_dir", "") or "").expanduser()
    if not str(root).strip():
        root = Path(DATA_DIR) / "artifacts"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    def _load_contacts() -> dict:
        raw_json = (os.getenv("DISCORD_CONTACTS_JSON", "") or "").strip()
        if raw_json:
            try:
                data = json.loads(raw_json)
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

        contacts_path = (os.getenv("DISCORD_CONTACTS_PATH", "") or "").strip()
        if not contacts_path:
            contacts_path = str(root.parent / "discord_contacts.json")
        try:
            p = Path(contacts_path).expanduser()
            if not p.exists():
                return {}
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    target = (url or "").strip().strip("\"' ")
    if not target:
        key = (recipient or "").strip()
        if not key:
            return (
                "Missing Discord target. Provide a channel/DM url=... or set a recipient mapping via "
                "DISCORD_CONTACTS_PATH / DISCORD_CONTACTS_JSON and call with recipient=..."
            )
        contacts = _load_contacts()
        raw = contacts.get(key)
        if raw is None:
            raw = contacts.get(key.lower())
        target = str(raw or "").strip()
        if raw is None:
            return (
                f"Unknown Discord recipient '{key}'. Add it via discord_contacts_add(key=\"{key}\", url=\"https://discord.com/channels/...\") "
                "or pass url=... directly."
            )
        if not target:
            return (
                f"Discord recipient '{key}' exists but has a blank URL in DISCORD_CONTACTS_PATH. "
                f"Update it via discord_contacts_add(key=\"{key}\", url=\"https://discord.com/channels/...\")."
            )

    try:
        limit_n = int(limit)
    except Exception:
        limit_n = 20
    limit_n = max(1, min(50, limit_n))

    profile_dir = Path(os.getenv("DISCORD_PLAYWRIGHT_PROFILE_DIR", str(root.parent / "discord_profile"))).expanduser()
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=bool(headless),
            )
            page = context.new_page()
            page.goto(target, wait_until="domcontentloaded", timeout=60000)

            try:
                if page.locator('input[name="email"]').count() > 0 or page.locator('input[type="email"]').count() > 0:
                    context.close()
                    return (
                        "Discord Web is not logged in. Re-run with headless=false, log in once in the opened browser, then retry. "
                        f"Profile dir: {profile_dir}"
                    )
            except Exception:
                pass

            page.wait_for_timeout(1200)

            selectors = [
                'li[id^="chat-messages-"]',
                'div[class*="messageListItem"]',
                'div[class*="message"]',
            ]
            msg_locator = None
            for sel in selectors:
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0:
                        msg_locator = loc
                        break
                except Exception:
                    continue

            if msg_locator is None:
                context.close()
                return "No messages found (Discord UI not loaded or selectors changed)."

            total = msg_locator.count()
            if total <= 0:
                context.close()
                return "No messages found."

            start = max(0, total - limit_n)
            lines: list[str] = []
            for i in range(start, total):
                msg = msg_locator.nth(i)
                author = ""
                content = ""
                timestamp = ""

                for sel in ['span[class*="username"]', 'h3 span', 'strong']:
                    try:
                        a = (msg.locator(sel).first.inner_text(timeout=200) or "").strip()
                        if a:
                            author = a
                            break
                    except Exception:
                        continue

                try:
                    t = msg.locator("time").first
                    timestamp = (t.get_attribute("datetime") or "").strip()
                except Exception:
                    timestamp = ""

                for sel in ['div[id^="message-content-"]', 'div[class*="markup"]']:
                    try:
                        c = (msg.locator(sel).first.inner_text(timeout=200) or "").strip()
                        if c:
                            content = c
                            break
                    except Exception:
                        continue

                if not author and not content:
                    continue

                # Just collect author and content, no timestamps
                if author and content:
                    lines.append(f"{author}: {content}")
                elif content:
                    lines.append(content)

            context.close()

            if not lines:
                return "No messages found."
            
            # Return simple format for agent to summarize naturally
            return "\n".join(lines[-limit_n:])
    except Exception as e:
        return f"Discord web read failed: {str(e)}"


@tool(args_schema=DiscordWebSendArgs, description="Send a Discord message via Playwright (requires a logged-in browser profile).")
def discord_web_send(url: str = "", recipient: Optional[str] = None, message: str = "", headless: bool = False) -> str:
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_playwright", False):
        return "System actions are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_PLAYWRIGHT=true, then restart the API."

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return "Playwright is not available. Please install playwright and run 'playwright install'."

    # Ensure Playwright browsers are installed
    _ensure_playwright_browsers()

    root = Path(getattr(config, "artifacts_dir", "") or "").expanduser()
    if not str(root).strip():
        root = Path(DATA_DIR) / "artifacts"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    def _load_contacts() -> dict:
        raw_json = (os.getenv("DISCORD_CONTACTS_JSON", "") or "").strip()
        if raw_json:
            try:
                data = json.loads(raw_json)
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

        contacts_path = (os.getenv("DISCORD_CONTACTS_PATH", "") or "").strip()
        if not contacts_path:
            contacts_path = str(root.parent / "discord_contacts.json")
        try:
            p = Path(contacts_path).expanduser()
            if not p.exists():
                return {}
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    target = (url or "").strip().strip("\"' ")
    if not target:
        key = (recipient or "").strip()
        if not key:
            return (
                "Missing Discord target. Provide a channel/DM url=... or set a recipient mapping via "
                "DISCORD_CONTACTS_PATH / DISCORD_CONTACTS_JSON and call with recipient=..."
            )
        contacts = _load_contacts()
        raw = contacts.get(key)
        if raw is None:
            raw = contacts.get(key.lower())
        target = str(raw or "").strip()
        if raw is None:
            return (
                f"Unknown Discord recipient '{key}'. Add it via discord_contacts_add(key=\"{key}\", url=\"https://discord.com/channels/...\") "
                "or pass url=... directly."
            )
        if not target:
            return (
                f"Discord recipient '{key}' exists but has a blank URL in DISCORD_CONTACTS_PATH. "
                f"Update it via discord_contacts_add(key=\"{key}\", url=\"https://discord.com/channels/...\")."
            )
    msg = (message or "").strip()
    if not msg:
        return "Message is empty."
    profile_dir = Path(os.getenv("DISCORD_PLAYWRIGHT_PROFILE_DIR", str(root.parent / "discord_profile"))).expanduser()
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=bool(headless),
            )
            page = context.new_page()
            page.goto(target, wait_until="domcontentloaded", timeout=60000)

            try:
                if page.locator('input[name="email"]').count() > 0 or page.locator('input[type="email"]').count() > 0:
                    context.close()
                    return (
                        "Discord Web is not logged in. Re-run with headless=false, log in once in the opened browser, then retry. "
                        f"Profile dir: {profile_dir}"
                    )
            except Exception:
                pass

            page.wait_for_timeout(750)
            box = page.locator('div[role="textbox"][contenteditable="true"][aria-label*="Message"]')
            if box.count() <= 0:
                box = page.locator('div[role="textbox"][contenteditable="true"]')
            if box.count() <= 0:
                box = page.locator('div[role="textbox"]')
            box = box.first
            box.wait_for(timeout=45000)
            box.scroll_into_view_if_needed()
            box.click(force=True)
            page.wait_for_timeout(100)
            try:
                box.press("Control+A")
                box.press("Backspace")
            except Exception:
                try:
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                except Exception:
                    pass
            try:
                page.evaluate(
                    """(el) => {
                      try {
                        if (!el) return;
                        el.focus();
                        el.textContent = '';
                      } catch (e) {}
                    }""",
                    box,
                )
            except Exception:
                pass
            page.wait_for_timeout(100)
            box.type(msg, delay=15)
            page.wait_for_timeout(100)
            page.keyboard.press("Enter")
            context.close()
            return f"Sent Discord message ({len(msg)} chars) via Playwright."
    except Exception as e:
        return f"Discord web send failed: {str(e)}"


@tool(args_schema=TakeScreenshotArgs, description="Take a screenshot and save it to disk.")
def take_screenshot(path: Optional[str] = None) -> str:
    """
    Take a screenshot and save it to disk.

    Args:
        path: Optional path to save the screenshot. Defaults to data/screenshot.png.

    Returns:
        Path to the saved screenshot or error message.
    """
    if not CV2_AVAILABLE or not VISION_AVAILABLE:
        return "Screenshot functionality is not available."

    try:
        from pathlib import Path
        import numpy as np

        if path is None:
            data_dir = Path(DATA_DIR)
            data_dir.mkdir(exist_ok=True)
            path = str(data_dir / "screenshot.png")

        screen_image = capture_screen()
        if screen_image is None:
            return "Failed to capture screen."

        if isinstance(screen_image, np.ndarray):
            ok = cv2.imwrite(path, screen_image)
            if not ok:
                return "Failed to save screenshot."
        else:
            return "Failed to save screenshot."
        logger.info(f"Screenshot saved to: {path}")
        return f"Screenshot saved to: {path}"

    except Exception as e:
        logger.error(f"Screenshot failed: {e}")
        return f"Screenshot failed: {str(e)}"


def _find_chrome_exe() -> Optional[str]:
    candidates = []
    pf = os.environ.get("ProgramFiles", "")
    pfx = os.environ.get("ProgramFiles(x86)", "")
    la = os.environ.get("LOCALAPPDATA", "")
    for root in (pf, pfx, la):
        if not root:
            continue
        candidates.append(os.path.join(root, "Google", "Chrome", "Application", "chrome.exe"))
    for p in candidates:
        try:
            if p and os.path.exists(p):
                return p
        except Exception:
            continue
    return None


@tool(args_schema=OpenChromeArgs, description="Open Google Chrome (opt-in system action).")
def open_chrome(url: Optional[str] = None) -> str:
    """Open Google Chrome on Windows (optionally to a website).

    This is an opt-in system action. It is disabled unless ENABLE_SYSTEM_ACTIONS=true
    and ALLOW_OPEN_CHROME=true are set in the environment and the API is restarted.

    Args:
        url: Optional URL/website to open (supports https://..., www..., example.com/path, or a single-word site like "reddit").

    Returns:
        Status message.
    """
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_open_chrome", False):
        return "System actions are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_OPEN_CHROME=true, then restart the API."

    if os.name != "nt":
        return "open_chrome is only supported on Windows."

    raw = (url or "").strip().strip("\"' ")
    target = ""
    if raw:
        candidate = raw
        if candidate.startswith("www."):
            candidate = "https://" + candidate

        if re.match(r"^https?://", candidate, flags=re.IGNORECASE):
            target = candidate
        else:
            domain_or_path = candidate
            if re.match(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:/\S*)?$", domain_or_path, flags=re.IGNORECASE):
                target = "https://" + domain_or_path
            elif re.match(r"^[a-z0-9][a-z0-9-]{1,62}$", domain_or_path, flags=re.IGNORECASE):
                target = f"https://{domain_or_path}.com"
            else:
                target = ""

    exe = _find_chrome_exe()
    try:
        if exe:
            args = [exe]
            if target:
                args.append(target)
            subprocess.Popen(args)
            return "Opened Google Chrome."

        cmd = ["cmd", "/c", "start", "", "chrome"]
        if target:
            cmd.append(target)
        subprocess.Popen(cmd)
        return "Opened Google Chrome."
    except Exception as e:
        return f"Failed to open Google Chrome: {str(e)}"


# Tool metadata for capabilities panel and safety UI
# risk_level: "safe" (read-only), "moderate" (writes/changes), "destructive" (deletes/irreversible)
# requires_confirmation: True for action tools that need user approval
# policy_flags: env flags required to enable this tool (for action tools)
# ============================================================================
# EMAIL TOOLS (v5.4.0 — IMAP / SMTP)
# ============================================================================

class EmailReadInboxArgs(BaseModel):
    count: int = Field(default=10, ge=1, le=50, description="Number of most recent emails to return")
    unread_only: bool = Field(default=False, description="If true, only return unread emails")


class EmailSearchArgs(BaseModel):
    query: str = Field(..., description="Search term to look for in emails (sender, subject, or body)")
    folder: str = Field(default="INBOX", description="Mail folder to search")
    count: int = Field(default=10, ge=1, le=50, description="Max results")


class EmailGetThreadArgs(BaseModel):
    message_id: str = Field(..., description="Message-ID to fetch the full thread for")


class EmailSendArgs(BaseModel):
    to: str = Field(..., description="Recipient email address")
    subject: str = Field(..., description="Email subject line")
    body: str = Field(..., description="Email body text")


class EmailReplyArgs(BaseModel):
    message_id: str = Field(..., description="Message-ID or UID of the email to reply to")
    body: str = Field(..., description="Reply body text")


def _retry_email(max_retries: int = 3, base_delay: float = 1.0):
    """Decorator that retries email operations with exponential backoff."""
    import functools
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (ConnectionError, TimeoutError, OSError) as exc:
                    last_exc = exc
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(f"Email retry {attempt + 1}/{max_retries} for {func.__name__}: {exc} (wait {delay}s)")
                        time.sleep(delay)
                except Exception as exc:
                    # Check for IMAP/SMTP specific transient errors
                    exc_str = str(type(exc).__name__).lower()
                    if any(k in exc_str for k in ("imap", "smtp", "connection", "timeout")):
                        last_exc = exc
                        if attempt < max_retries:
                            delay = base_delay * (2 ** attempt)
                            logger.warning(f"Email retry {attempt + 1}/{max_retries} for {func.__name__}: {exc} (wait {delay}s)")
                            time.sleep(delay)
                    else:
                        raise
            raise last_exc  # type: ignore[misc]  # always set after loop
        return wrapper
    return decorator


@_retry_email(max_retries=3, base_delay=1.0)
def _get_imap_connection():
    """Connect to IMAP server using config credentials (with automatic retry)."""
    import imaplib
    host = getattr(config, "email_imap_host", "imap.gmail.com")
    port = int(getattr(config, "email_imap_port", 993))
    username = getattr(config, "email_username", "")
    password = getattr(config, "email_password", "")
    if not username or not password:
        raise ValueError("EMAIL_USERNAME and EMAIL_PASSWORD must be set")
    use_tls = getattr(config, "email_use_tls", True)
    if use_tls:
        conn = imaplib.IMAP4_SSL(host, port)
    else:
        conn = imaplib.IMAP4(host, port)
    conn.login(username, password)
    return conn


def _parse_email_message(raw_msg_bytes):
    """Parse raw email bytes into a summary dict."""
    import email
    from email.header import decode_header
    msg = email.message_from_bytes(raw_msg_bytes)

    def _decode_hdr(hdr):
        parts = decode_header(hdr or "")
        result = []
        for part, charset in parts:
            if isinstance(part, bytes):
                result.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                result.append(str(part))
        return " ".join(result)

    subject = _decode_hdr(msg.get("Subject", ""))
    sender = _decode_hdr(msg.get("From", ""))
    date_str = msg.get("Date", "")
    message_id = msg.get("Message-ID", "")
    in_reply_to = msg.get("In-Reply-To", "")

    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")
                    break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode("utf-8", errors="replace")

    # Truncate long bodies
    if len(body) > 1500:
        body = body[:1500] + "..."

    return {
        "subject": subject,
        "from": sender,
        "date": date_str,
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "body_preview": body.strip(),
    }


@tool(args_schema=EmailReadInboxArgs, description="Read recent emails from your inbox. Returns subject, sender, date, and a body preview for each email.")
def email_read_inbox(count: int = 10, unread_only: bool = False) -> str:
    """Read recent emails from the inbox."""
    if not getattr(config, "allow_email", False):
        return "Email integration is not enabled. Set ALLOW_EMAIL=true and configure EMAIL_* settings."
    try:
        conn = _get_imap_connection()
        conn.select("INBOX")
        search_criteria = "UNSEEN" if unread_only else "ALL"
        _, data = conn.search(None, search_criteria)
        msg_ids = data[0].split()
        if not msg_ids:
            return "No emails found." if not unread_only else "No unread emails."
        # Get most recent N
        recent_ids = msg_ids[-count:]
        recent_ids.reverse()
        results = []
        for mid in recent_ids:
            _, raw = conn.fetch(mid, "(RFC822)")
            if raw and raw[0] and isinstance(raw[0], tuple):
                parsed = _parse_email_message(raw[0][1])
                results.append(parsed)
        conn.close()
        conn.logout()
        if not results:
            return "No emails found."
        lines = []
        for i, e in enumerate(results, 1):
            lines.append(f"--- Email {i} ---")
            lines.append(f"From: {e['from']}")
            lines.append(f"Subject: {e['subject']}")
            lines.append(f"Date: {e['date']}")
            lines.append(f"ID: {e['message_id']}")
            lines.append(f"Preview: {e['body_preview'][:300]}")
            lines.append("")
        return "\n".join(lines)
    except Exception as exc:
        return f"Email read failed: {exc}"


@tool(args_schema=EmailSearchArgs, description="Search emails by keyword in sender, subject, or body.")
def email_search(query: str, folder: str = "INBOX", count: int = 10) -> str:
    """Search emails by keyword."""
    if not getattr(config, "allow_email", False):
        return "Email integration is not enabled. Set ALLOW_EMAIL=true."
    try:
        conn = _get_imap_connection()
        conn.select(folder)
        # IMAP search — OR across subject, from, body
        _, data = conn.search(None, f'(OR OR SUBJECT "{query}" FROM "{query}" BODY "{query}")')
        msg_ids = data[0].split()
        if not msg_ids:
            conn.close()
            conn.logout()
            return f"No emails matching '{query}' found."
        recent_ids = msg_ids[-count:]
        recent_ids.reverse()
        results = []
        for mid in recent_ids:
            _, raw = conn.fetch(mid, "(RFC822)")
            if raw and raw[0] and isinstance(raw[0], tuple):
                parsed = _parse_email_message(raw[0][1])
                results.append(parsed)
        conn.close()
        conn.logout()
        lines = [f"Found {len(results)} email(s) matching '{query}':"]
        for i, e in enumerate(results, 1):
            lines.append(f"{i}. {e['from']} — {e['subject']} ({e['date']})")
            lines.append(f"   ID: {e['message_id']}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Email search failed: {exc}"


@tool(args_schema=EmailGetThreadArgs, description="Get the full email thread by Message-ID.")
def email_get_thread(message_id: str) -> str:
    """Get full email thread."""
    if not getattr(config, "allow_email", False):
        return "Email integration is not enabled."
    try:
        conn = _get_imap_connection()
        conn.select("INBOX")
        # Search for the specific message and any replies
        _, data = conn.search(None, f'(OR HEADER Message-ID "{message_id}" HEADER In-Reply-To "{message_id}")')
        msg_ids = data[0].split()
        if not msg_ids:
            conn.close()
            conn.logout()
            return f"No emails found for thread {message_id}"
        results = []
        for mid in msg_ids:
            _, raw = conn.fetch(mid, "(RFC822)")
            if raw and raw[0] and isinstance(raw[0], tuple):
                parsed = _parse_email_message(raw[0][1])
                results.append(parsed)
        conn.close()
        conn.logout()
        lines = [f"Thread ({len(results)} message(s)):"]
        for e in results:
            lines.append(f"\n--- {e['from']} ({e['date']}) ---")
            lines.append(f"Subject: {e['subject']}")
            lines.append(e['body_preview'])
        return "\n".join(lines)
    except Exception as exc:
        return f"Email get_thread failed: {exc}"


@tool(args_schema=EmailSendArgs, description="Send a new email. Requires recipient, subject, and body.")
def email_send(to: str, subject: str, body: str) -> str:
    """Send a new email via SMTP."""
    if not getattr(config, "allow_email", False):
        return "Email integration is not enabled."
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        host = getattr(config, "email_smtp_host", "smtp.gmail.com")
        port = int(getattr(config, "email_smtp_port", 587))
        username = getattr(config, "email_username", "")
        password = getattr(config, "email_password", "")

        msg = MIMEMultipart()
        msg["From"] = username
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            if getattr(config, "email_use_tls", True):
                server.starttls()
            server.login(username, password)
            server.send_message(msg)

        return f"Email sent to {to}: '{subject}'"
    except Exception as exc:
        return f"Email send failed: {exc}"


@tool(args_schema=EmailReplyArgs, description="Reply to an email by its Message-ID.")
def email_reply(message_id: str, body: str) -> str:
    """Reply to an existing email."""
    if not getattr(config, "allow_email", False):
        return "Email integration is not enabled."
    try:
        # First, fetch the original message to get sender and subject
        conn = _get_imap_connection()
        conn.select("INBOX")
        _, data = conn.search(None, f'(HEADER Message-ID "{message_id}")')
        msg_ids = data[0].split()
        if not msg_ids:
            conn.close()
            conn.logout()
            return f"Original email {message_id} not found."
        _, raw = conn.fetch(msg_ids[0], "(RFC822)")
        original = _parse_email_message(raw[0][1])
        conn.close()
        conn.logout()

        # Now send the reply via SMTP
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        host = getattr(config, "email_smtp_host", "smtp.gmail.com")
        port = int(getattr(config, "email_smtp_port", 587))
        username = getattr(config, "email_username", "")
        password = getattr(config, "email_password", "")

        reply_to = original.get("from", "")
        subject = original.get("subject", "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        msg = MIMEMultipart()
        msg["From"] = username
        msg["To"] = reply_to
        msg["Subject"] = subject
        msg["In-Reply-To"] = message_id
        msg["References"] = message_id
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            if getattr(config, "email_use_tls", True):
                server.starttls()
            server.login(username, password)
            server.send_message(msg)

        return f"Reply sent to {reply_to}: '{subject}'"
    except Exception as exc:
        return f"Email reply failed: {exc}"


class ProjectUpdateContextArgs(BaseModel):
    limit: int = Field(default=5, ge=1, le=10, description="How many recent commits to summarize")


@tool(args_schema=ProjectUpdateContextArgs, description="Read a grounded, public-safe summary of recent EchoSpeak project updates.")
def project_update_context(limit: int = 5) -> str:
    try:
        from agent.update_context import get_update_context_service

        block = get_update_context_service().build_context_block(
            limit=max(1, min(int(limit or 5), 10)),
            public=True,
            include_diff=False,
            heading="Grounded EchoSpeak project updates",
        )
        return block or "No recent grounded project updates were found."
    except Exception as exc:
        return f"Failed to read project updates: {exc}"


class TodoManageArgs(BaseModel):
    action: str = Field(..., description="Action: 'list', 'add', 'update', 'delete'")
    title: str = Field(default="", description="Title for add/update")
    description: str = Field(default="", description="Description for add/update")
    todo_id: str = Field(default="", description="ID of todo for update/delete")
    status: str = Field(default="pending", description="Status: 'pending', 'in_progress', 'done'")
    priority: str = Field(default="medium", description="Priority: 'low', 'medium', 'high'")


@tool(args_schema=TodoManageArgs, description="Manage the shared todo list. Actions: list (show all), add (create new), update (change status/title/description), delete (remove by id).")
def todo_manage(action: str, title: str = "", description: str = "", todo_id: str = "", status: str = "pending", priority: str = "medium") -> str:
    """Manage the shared todo list that is visible in the Web UI."""
    import json as _json
    import uuid as _uuid
    from datetime import datetime as _dt
    from pathlib import Path as _Path

    todo_file = _Path(DATA_DIR) / "todos.json"

    def _load() -> list:
        if todo_file.exists():
            try:
                return _json.loads(todo_file.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def _save(items: list) -> None:
        todo_file.parent.mkdir(parents=True, exist_ok=True)
        todo_file.write_text(_json.dumps(items, indent=2, default=str), encoding="utf-8")

    action = (action or "list").lower().strip()

    if action == "list":
        todos = _load()
        if not todos:
            return "Todo list is empty."
        lines = []
        for t in todos:
            mark = "✅" if t.get("status") == "done" else "🔄" if t.get("status") == "in_progress" else "⬜"
            pri = t.get("priority", "medium")
            lines.append(f"{mark} [{pri.upper()}] {t.get('title', '?')} (id: {t.get('id', '?')}, status: {t.get('status', '?')})")
            if t.get("description"):
                lines.append(f"   {t['description']}")
        return "\n".join(lines)

    elif action == "add":
        if not title:
            return "Error: 'title' is required to add a todo."
        todos = _load()
        now = _dt.utcnow().isoformat()
        entry = {
            "id": str(_uuid.uuid4())[:8],
            "title": title,
            "description": description,
            "status": status,
            "priority": priority,
            "created_at": now,
            "updated_at": now,
        }
        todos.append(entry)
        _save(todos)
        return f"Added todo '{title}' (id: {entry['id']}, priority: {priority}, status: {status})"

    elif action == "update":
        if not todo_id:
            return "Error: 'todo_id' is required to update a todo."
        todos = _load()
        for t in todos:
            if t.get("id") == todo_id:
                if title:
                    t["title"] = title
                if description:
                    t["description"] = description
                t["status"] = status
                t["priority"] = priority
                t["updated_at"] = _dt.utcnow().isoformat()
                _save(todos)
                return f"Updated todo '{t['title']}' (id: {todo_id}) -> status: {status}, priority: {priority}"
        return f"Error: Todo with id '{todo_id}' not found."

    elif action == "delete":
        if not todo_id:
            return "Error: 'todo_id' is required to delete a todo."
        todos = _load()
        filtered = [t for t in todos if t.get("id") != todo_id]
        if len(filtered) == len(todos):
            return f"Error: Todo with id '{todo_id}' not found."
        _save(filtered)
        return f"Deleted todo with id '{todo_id}'."

    return f"Unknown action '{action}'. Use: list, add, update, delete."


TOOL_METADATA: Dict[str, Dict[str, Any]] = {
    # Read-only / safe tools
    "web_search": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
    "safe_web_fetch": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
    "weather_live": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
    "sports_live": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
    "get_system_time": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
    "calculate": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
    "project_update_context": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
    "todo_manage": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
    "youtube_transcript": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
    "desktop_list_windows": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_DESKTOP_AUTOMATION"]},
    "desktop_find_control": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_DESKTOP_AUTOMATION"]},
    "file_list": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
    "file_read": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
    "system_info": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
    "project_status": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
    "analyze_screen": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
     "vision_qa": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
     "take_screenshot": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": []},
     "discord_read_channel": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": ["ALLOW_DISCORD_BOT"]},
     "discord_send_channel": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ALLOW_DISCORD_BOT"]},
     "discord_web_read_recent": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_PLAYWRIGHT"]},
     # Email tools (v5.4.0)
     "email_read_inbox": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": ["ALLOW_EMAIL"]},
     "email_search": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": ["ALLOW_EMAIL"]},
    "email_get_thread": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": ["ALLOW_EMAIL"]},
    "email_send": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ALLOW_EMAIL"]},
    "email_reply": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ALLOW_EMAIL"]},
    # Moderate risk tools (write/create)
    "browse_task": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_PLAYWRIGHT"]},
    "file_write": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_FILE_WRITE"]},
    "file_move": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_FILE_WRITE"]},
    "file_copy": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_FILE_WRITE"]},
    "file_mkdir": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_FILE_WRITE"]},
    "artifact_write": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_FILE_WRITE"]},
    "notepad_write": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_OPEN_APPLICATION", "ALLOW_DESKTOP_AUTOMATION", "ALLOW_FILE_WRITE"]},
    "open_chrome": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_OPEN_CHROME"]},
    "open_application": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_OPEN_APPLICATION"]},
    "desktop_click": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_DESKTOP_AUTOMATION"]},
    "desktop_type_text": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_DESKTOP_AUTOMATION"]},
    "desktop_activate_window": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_DESKTOP_AUTOMATION"]},
    "desktop_send_hotkey": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_DESKTOP_AUTOMATION"]},
    "discord_web_send": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_PLAYWRIGHT"]},
    "discord_contacts_add": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_PLAYWRIGHT"]},
    "discord_contacts_discover": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_PLAYWRIGHT"]},
    # Destructive risk tools (delete/terminal)
    "file_delete": {"risk_level": "destructive", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_FILE_WRITE"]},
    "terminal_run": {"risk_level": "destructive", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_TERMINAL_COMMANDS"]},
    "code_preview_start": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_TERMINAL_COMMANDS"]},
    "code_preview_stop": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_TERMINAL_COMMANDS"]},
    "checkpoint_undo": {"risk_level": "destructive", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_FILE_WRITE"]},
    # Self-modification tools
    "self_edit": {"risk_level": "destructive", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_SELF_MODIFICATION"]},
    "self_rollback": {"risk_level": "moderate", "requires_confirmation": True, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_SELF_MODIFICATION"]},
    "self_git_status": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_SELF_MODIFICATION"]},
    "self_read": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_SELF_MODIFICATION"]},
    "self_grep": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_SELF_MODIFICATION"]},
    "self_list": {"risk_level": "safe", "requires_confirmation": False, "policy_flags": ["ENABLE_SYSTEM_ACTIONS", "ALLOW_SELF_MODIFICATION"]},
}


# ============================================================================
# SELF-MODIFICATION TOOLS
# ============================================================================

PROJECT_ROOT = Path(__file__).parent.parent.parent  # apps/backend -> apps -> project root


class SelfEditArgs(BaseModel):
    file_path: str = Field(..., description="Relative path from project root, e.g., 'apps/backend/agent/core.py'")
    old_content: str = Field(..., description="Exact content to replace (must match exactly)")
    new_content: str = Field(..., description="New content to write")
    commit_message: str = Field(default="self_edit: automated change", description="Git commit message for rollback")


class SelfRollbackArgs(BaseModel):
    steps: int = Field(default=1, ge=1, le=10, description="Number of commits to roll back")


def _git_repo_precondition(cwd: Path) -> Optional[dict]:
    """Return None when git identity is usable; else structured fail-closed payload.

    Self-edit/rollback must never invent commit history, restore claims, or
    mutate when HEAD/index/worktree identity cannot be established.
    """
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if top.returncode != 0:
            return {
                "ok": False,
                "error_code": "missing_precondition",
                "error": "Not a git repository or git is unavailable.",
                "retryable": False,
            }
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if head.returncode != 0 or not str(head.stdout or "").strip():
            return {
                "ok": False,
                "error_code": "missing_precondition",
                "error": "Git HEAD is unavailable; cannot bind self-edit/rollback identity.",
                "retryable": False,
            }
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "-b"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if status.returncode != 0:
            return {
                "ok": False,
                "error_code": "missing_precondition",
                "error": "Git status could not be read; refusing self-edit/rollback.",
                "retryable": False,
            }
        return None
    except FileNotFoundError:
        return {
            "ok": False,
            "error_code": "unavailable_adapter",
            "error": "git executable not found.",
            "retryable": False,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "missing_precondition",
            "error": f"Git precondition check failed: {exc}",
            "retryable": False,
        }


def _self_mod_json_err(code: str, message: str, **extra: Any) -> str:
    return json.dumps(
        {"ok": False, "error_code": code, "error": message, "applied": False, **extra},
        ensure_ascii=False,
        default=str,
    )


@tool(args_schema=SelfEditArgs, description="Edit EchoSpeak's own code with automatic git commit for rollback. USE WITH CAUTION.")
def self_edit(file_path: str, old_content: str, new_content: str, commit_message: str = "self_edit: automated change") -> str:
    """
    Edit a file in EchoSpeak's codebase with automatic git commit.
    This allows the agent to modify itself with rollback capability.
    
    Safety:
    - Creates a git commit before each change
    - Only works if ALLOW_SELF_MODIFICATION is enabled
    - File must be within project root
    - Requires resolvable git HEAD/index precondition (fail closed otherwise)
    """
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_self_modification", False):
        return _self_mod_json_err(
            "permission_denied",
            "Self-modification is disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_SELF_MODIFICATION=true",
        )

    pre = _git_repo_precondition(PROJECT_ROOT)
    if pre is not None:
        return json.dumps(pre, ensure_ascii=False)

    # Resolve and validate path
    target = (PROJECT_ROOT / file_path).resolve()
    try:
        target.relative_to(PROJECT_ROOT)
    except ValueError:
        return _self_mod_json_err("validation_failed", f"Path must be within project root. Got: {file_path}")
    
    if not target.exists():
        return _self_mod_json_err("validation_failed", f"File not found: {file_path}")
    
    # Read current content
    try:
        current = target.read_text(encoding="utf-8")
    except Exception as e:
        return _self_mod_json_err("tool_failed", f"Failed to read file: {str(e)}")
    
    # Verify old_content matches
    if old_content not in current:
        # Show a snippet to help debug
        snippet = current[:500] if len(current) > 500 else current
        return _self_mod_json_err(
            "stale_source",
            f"old_content not found in file (source may have changed). File starts with:\n{snippet}",
        )
    
    # Create git commit for rollback
    try:
        # Check if file is tracked
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(target)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            # File not tracked, add it
            subprocess.run(["git", "add", str(target)], cwd=PROJECT_ROOT, check=True)
        
        # Commit current state
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", f"[self_edit backup] {commit_message}"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=True
        )
    except subprocess.CalledProcessError as e:
        return _self_mod_json_err(
            "missing_precondition",
            f"Failed to create backup commit: {e.stderr or str(e)}",
        )
    except Exception as e:
        return _self_mod_json_err("missing_precondition", f"Git error: {str(e)}")
    
    # Apply the edit
    try:
        new_text = current.replace(old_content, new_content, 1)
        target.write_text(new_text, encoding="utf-8")
    except Exception as e:
        return _self_mod_json_err("persistence_failed", f"Failed to write file: {str(e)}")
    
    return json.dumps(
        {
            "ok": True,
            "applied": True,
            "path": str(file_path),
            "message": "Edited with backup commit. Use self_rollback to undo if needed.",
        },
        ensure_ascii=False,
    )


@tool(args_schema=SelfRollbackArgs, description="Roll back recent self-modification commits. Restores previous code state.")
def self_rollback(steps: int = 1) -> str:
    """
    Roll back the last N commits made by self_edit.
    This restores the codebase to a previous state.
    
    Args:
        steps: Number of commits to roll back (1-10)
    """
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_self_modification", False):
        return _self_mod_json_err("permission_denied", "Self-modification is disabled.")

    pre = _git_repo_precondition(PROJECT_ROOT)
    if pre is not None:
        return json.dumps(pre, ensure_ascii=False)
    
    try:
        # Get list of recent commits
        result = subprocess.run(
            ["git", "log", "--oneline", "-n", str(steps + 1)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True
        )
        commits = result.stdout.strip().split("\n")
        
        if len(commits) <= steps:
            return _self_mod_json_err(
                "missing_precondition",
                f"Not enough commits to roll back {steps} steps.",
            )

        # Only roll back commits that look like self_edit backups — never invent history.
        head_msg = subprocess.run(
            ["git", "log", "-1", "--pretty=%s"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        subject = str(head_msg.stdout or "").strip()
        if "[self_edit backup]" not in subject and "self_edit" not in subject.casefold():
            return _self_mod_json_err(
                "validation_failed",
                f"HEAD is not a self_edit backup commit ({subject!r}); refusing hard reset.",
            )
        
        # Perform rollback
        result = subprocess.run(
            ["git", "reset", "--hard", f"HEAD~{steps}"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True
        )
        
        return json.dumps(
            {
                "ok": True,
                "applied": True,
                "steps": steps,
                "message": f"Rolled back {steps} commit(s). Restart server to apply runtime code.",
            },
            ensure_ascii=False,
        )
    except subprocess.CalledProcessError as e:
        return _self_mod_json_err("tool_failed", f"Git rollback failed: {e.stderr or str(e)}")
    except Exception as e:
        return _self_mod_json_err("tool_failed", f"Rollback error: {str(e)}")


@tool(description="Show git status and recent commits for self-modification tracking.")
def self_git_status() -> str:
    """
    Show git status and recent commits.
    Useful for seeing what changes have been made and can be rolled back.
    """
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_self_modification", False):
        return _self_mod_json_err("permission_denied", "Self-modification is disabled.")

    pre = _git_repo_precondition(PROJECT_ROOT)
    if pre is not None:
        return json.dumps(pre, ensure_ascii=False)
    
    try:
        # Get status
        status_result = subprocess.run(
            ["git", "status", "--short"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True
        )
        
        # Get recent commits
        log_result = subprocess.run(
            ["git", "log", "--oneline", "-n", "10"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True
        )
        
        return json.dumps(
            {
                "ok": True,
                "status": status_result.stdout,
                "log": log_result.stdout,
            },
            ensure_ascii=False,
        )
    except subprocess.CalledProcessError as e:
        return _self_mod_json_err("tool_failed", f"Git error: {e.stderr or str(e)}")
    except Exception as e:
        return _self_mod_json_err("tool_failed", f"Error: {str(e)}")


class SelfReadArgs(BaseModel):
    file_path: str = Field(..., description="Relative path from project root, e.g., 'apps/backend/agent/core.py'")
    start_line: int = Field(default=1, ge=1, description="Start line number (1-indexed)")
    end_line: int = Field(default=100, ge=1, description="End line number (1-indexed)")


class SelfGrepArgs(BaseModel):
    pattern: str = Field(..., description="Search pattern (regex supported)")
    path: str = Field(default="", description="Relative path to search in (empty = whole project)")


class SelfListArgs(BaseModel):
    path: str = Field(default="", description="Relative path to list (empty = project root)")


@tool(args_schema=SelfReadArgs, description="Read a file from EchoSpeak's own codebase. Use this to understand code before editing.")
def self_read(file_path: str, start_line: int = 1, end_line: int = 100) -> str:
    """
    Read a file from EchoSpeak's codebase.
    Useful for understanding code before making edits.
    """
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_self_modification", False):
        return "Self-modification is disabled."
    
    target = (PROJECT_ROOT / file_path).resolve()
    try:
        target.relative_to(PROJECT_ROOT)
    except ValueError:
        return f"Path must be within project root. Got: {file_path}"
    
    if not target.exists():
        return f"File not found: {file_path}"
    
    if not target.is_file():
        return f"Not a file: {file_path}"
    
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
        total_lines = len(lines)
        
        # Clamp line numbers
        start = max(1, start_line)
        end = min(total_lines, end_line)
        
        if start > total_lines:
            return f"File has {total_lines} lines. Start line {start_line} is out of range."
        
        # Format with line numbers
        result_lines = []
        for i in range(start - 1, end):
            result_lines.append(f"{i+1:4d}: {lines[i]}")
        
        header = f"=== {file_path} (lines {start}-{end} of {total_lines}) ===\n"
        return header + "\n".join(result_lines)
    except Exception as e:
        return f"Failed to read file: {str(e)}"


@tool(args_schema=SelfGrepArgs, description="Search for patterns in EchoSpeak's codebase. Use this to find code before editing.")
def self_grep(pattern: str, path: str = "") -> str:
    """
    Search for a pattern in EchoSpeak's codebase.
    Useful for finding code locations before making edits.
    """
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_self_modification", False):
        return "Self-modification is disabled."
    
    search_path = (PROJECT_ROOT / path).resolve() if path else PROJECT_ROOT
    try:
        search_path.relative_to(PROJECT_ROOT)
    except ValueError:
        return f"Path must be within project root. Got: {path}"
    
    if not search_path.exists():
        return f"Path not found: {path or 'project root'}"
    
    try:
        # Use ripgrep if available, fallback to grep
        result = subprocess.run(
            ["rg", "-n", "--no-heading", "-C", "2", pattern, str(search_path)],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().split("\n")
            # Limit output
            if len(lines) > 50:
                return f"Found {len(lines)} matches (showing first 50):\n" + "\n".join(lines[:50]) + f"\n... ({len(lines) - 50} more)"
            return f"Found {len(lines)} matches:\n" + result.stdout
        elif result.returncode == 1:
            return f"No matches found for pattern: {pattern}"
        else:
            # Fallback to Python grep
            import re as regex
            matches = []
            for py_file in search_path.rglob("*.py"):
                try:
                    for i, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), 1):
                        if regex.search(pattern, line):
                            rel_path = py_file.relative_to(PROJECT_ROOT)
                            matches.append(f"{rel_path}:{i}: {line.strip()}")
                            if len(matches) >= 50:
                                break
                except:
                    continue
                if len(matches) >= 50:
                    break
            
            if matches:
                return f"Found {len(matches)} matches:\n" + "\n".join(matches)
            return f"No matches found for pattern: {pattern}"
    except subprocess.TimeoutExpired:
        return "Search timed out. Try a more specific path or pattern."
    except FileNotFoundError:
        # rg not found, use Python fallback
        import re as regex
        matches = []
        for py_file in search_path.rglob("*.py"):
            try:
                for i, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), 1):
                    if regex.search(pattern, line):
                        rel_path = py_file.relative_to(PROJECT_ROOT)
                        matches.append(f"{rel_path}:{i}: {line.strip()}")
                        if len(matches) >= 50:
                            break
            except:
                continue
            if len(matches) >= 50:
                break
        
        if matches:
            return f"Found {len(matches)} matches:\n" + "\n".join(matches)
        return f"No matches found for pattern: {pattern}"
    except Exception as e:
        return f"Search error: {str(e)}"


@tool(args_schema=SelfListArgs, description="List files in EchoSpeak's codebase. Use this to explore the project structure.")
def self_list(path: str = "") -> str:
    """
    List files and directories in EchoSpeak's codebase.
    Useful for exploring project structure.
    """
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_self_modification", False):
        return "Self-modification is disabled."
    
    list_path = (PROJECT_ROOT / path).resolve() if path else PROJECT_ROOT
    try:
        list_path.relative_to(PROJECT_ROOT)
    except ValueError:
        return f"Path must be within project root. Got: {path}"
    
    if not list_path.exists():
        return f"Path not found: {path or 'project root'}"
    
    if not list_path.is_dir():
        return f"Not a directory: {path}"
    
    try:
        items = sorted(list_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        lines = []
        for item in items:
            # Skip hidden and common ignore patterns
            if item.name.startswith(".") or item.name in ["__pycache__", "node_modules", ".git", ".venv", "venv", "*.pyc"]:
                continue
            if item.is_dir():
                lines.append(f"📁 {item.name}/")
            else:
                size = item.stat().st_size
                size_str = f"{size}B" if size < 1024 else f"{size//1024}KB"
                lines.append(f"📄 {item.name} ({size_str})")
        
        header = f"=== {path or 'project root'} ({len(lines)} items) ===\n"
        return header + "\n".join(lines) if lines else f"Empty directory: {path or 'project root'}"
    except Exception as e:
        return f"Failed to list directory: {str(e)}"


# ============================================================================
# PROJECT STATUS / SHOWCASE TOOL (v8.0.0)
# ============================================================================

@tool(description="Undo the latest checkpointed file change in the current thread and project. Requires exact approval.")
def checkpoint_undo() -> str:
    if denied := _tool_scope_denial("checkpoint_undo"):
        return denied
    context = get_tool_execution_context()
    if not context or not context.get("strict_scope"):
        return "Undo is blocked outside a bound thread execution context."
    if denial := _mutation_precondition_denial("checkpoint_undo"):
        return denial
    from agent.checkpoints import undo_last_change

    result = undo_last_change(
        thread_id=str(context.get("thread_id") or "default"),
        project_root=str(context.get("project_root") or ""),
    )
    return result if str(result).startswith("Successfully reverted") else f"Error: {result}"

class ProjectStatusArgs(BaseModel):
    workspace_path: str = Field(
        default="",
        description="Optional relative path inside the attached Project. Defaults to the attached Project root.",
    )


@tool(args_schema=ProjectStatusArgs, description="Inspect attached-Project metadata, git working-tree state, todos, and available build scripts without executing project code.")
def project_status(workspace_path: str = "") -> str:
    """Inspect Project metadata and status without executing Project code."""
    if denied := _tool_scope_denial("project_status"):
        return denied
    try:
        # Project/session scope is authority. FILE_TOOL_ROOT is not an implicit
        # attachment and arbitrary absolute paths are never accepted here.
        ws_root = _safe_file_path(workspace_path or ".")
        if ws_root is None:
            return "Path not allowed by the attached Project scope."

        if not ws_root.exists():
            return f"Workspace path not found: {ws_root}"

        sections = []
        sections.append(f"# Project Status Report\n**Workspace:** `{ws_root}`\n**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

        # ── 1. Git Status ──
        try:
            git_result = subprocess.run(
                ["git", "status", "--porcelain", "-b"],
                cwd=str(ws_root), capture_output=True, text=True, timeout=10
            )
            if git_result.returncode == 0:
                git_lines = git_result.stdout.strip().split("\n")
                branch_line = git_lines[0] if git_lines else "unknown"
                changes = [l for l in git_lines[1:] if l.strip()]
                modified = [l for l in changes if l.startswith(" M") or l.startswith("M ")]
                added = [l for l in changes if l.startswith("A ") or l.startswith("??")]
                deleted = [l for l in changes if l.startswith("D ") or l.startswith(" D")]

                git_section = f"## Git Status\n**Branch:** `{branch_line.replace('## ', '')}`\n"
                git_section += f"- Modified: {len(modified)} files\n"
                git_section += f"- New/Untracked: {len(added)} files\n"
                git_section += f"- Deleted: {len(deleted)} files\n"
                if changes:
                    git_section += f"\n**Changed files:**\n"
                    for c in changes[:20]:
                        git_section += f"  `{c.strip()}`\n"
                    if len(changes) > 20:
                        git_section += f"  ... and {len(changes) - 20} more\n"
                else:
                    git_section += "\nWorking tree clean.\n"
                sections.append(git_section)
            else:
                sections.append("## Git Status\nNot a git repository or git not available.\n")
        except FileNotFoundError:
            sections.append("## Git Status\nGit is not installed.\n")
        except Exception as e:
            sections.append(f"## Git Status\nError: {e}\n")

        # ── 2. Test Results ──
        # project_status is a safe read tool. Tests/builds execute Project code
        # and therefore belong exclusively to approval-gated terminal_run.
        sections.append(
            "## Tests\nNot executed. Use the approval-gated terminal tool when verification is authorized.\n"
        )

        # ── 3. Todo Status ──
        todo_paths = [ws_root / "data" / "todos.json", ws_root / "apps" / "backend" / "data" / "todos.json"]
        for tp in todo_paths:
            if tp.exists():
                try:
                    todos = json.loads(tp.read_text(encoding="utf-8"))
                    if isinstance(todos, list):
                        total = len(todos)
                        done = sum(1 for t in todos if t.get("done") or t.get("completed"))
                        pending = total - done
                        sections.append(f"## Todos\n- Total: {total}\n- Done: {done} ✅\n- Pending: {pending} 📋\n")
                        if pending > 0:
                            pending_items = [t for t in todos if not (t.get("done") or t.get("completed"))][:5]
                            sections.append("**Top pending:**\n" + "\n".join(f"  - {t.get('text', t.get('title', 'Untitled'))}" for t in pending_items) + "\n")
                except Exception:
                    pass
                break

        # ── 4. Build Health Check ──
        # Check for common build indicators
        pkg_json = ws_root / "package.json"
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
                scripts = pkg.get("scripts", {})
                sections.append(f"## Build System\n**package.json** found.\nAvailable scripts: {', '.join(f'`{s}`' for s in sorted(scripts.keys())[:10])}\n")
            except Exception:
                pass

        # Check for Python setup
        req_txt = ws_root / "requirements.txt"
        setup_py = ws_root / "setup.py"
        pyproject = ws_root / "pyproject.toml"
        if req_txt.exists() or setup_py.exists() or pyproject.exists():
            sections.append("## Python Project\nPython project files detected.\n")

        # ── 5. File Summary ──
        try:
            py_count = len(list(ws_root.rglob("*.py")))
            ts_count = len(list(ws_root.rglob("*.ts"))) + len(list(ws_root.rglob("*.tsx")))
            md_count = len(list(ws_root.rglob("*.md")))
            sections.append(f"## File Summary\n- Python files: {py_count}\n- TypeScript files: {ts_count}\n- Markdown docs: {md_count}\n")
        except Exception:
            pass

        return "\n---\n\n".join(sections)
    except Exception as exc:
        return f"Failed to generate project status: {exc}"


def get_available_tools() -> list:
    """
    Get list of available tools based on dependencies.

    Returns:
        List of tool functions.
    """
    tools = [
        web_search,
        safe_web_fetch,
        weather_live,
        sports_live,
        get_system_time,
        calculate,
        project_update_context,
        youtube_transcript,
        browse_task,
        discord_contacts_add,
        discord_contacts_discover,
        discord_read_channel,  # Bot-based channel reader (works from Web UI)
        discord_send_channel,  # Bot-based channel sender (works from Web UI)
        discord_web_read_recent,
        discord_web_send,
        desktop_list_windows,
        desktop_find_control,
        desktop_click,
        desktop_type_text,
        desktop_activate_window,
        desktop_send_hotkey,
        file_list,
        file_read,
        file_write,
        file_move,
        file_copy,
        file_delete,
        file_mkdir,
        artifact_write,
        notepad_write,
        terminal_run,
        code_preview_start,
        code_preview_stop,
        system_info,
        project_status,
        checkpoint_undo,
        # Self-modification tools
        self_edit,
        self_rollback,
        self_git_status,
        self_read,
        self_grep,
        self_list,
        # Email tools (v5.4.0)
        email_read_inbox,
        email_search,
        email_get_thread,
        email_send,
        email_reply,
    ]

    tools.append(open_chrome)

    tools.append(open_application)

    if VISION_AVAILABLE and CV2_AVAILABLE:
        tools.extend([analyze_screen, vision_qa, take_screenshot])

    return tools


def get_tool_descriptions() -> str:
    """
    Get descriptions of all available tools for the agent.

    Returns:
        Formatted tool descriptions.
    """
    tool_descriptions = []

    for tool_func in get_available_tools():
        description = f"- {tool_func.name}: {tool_func.description}"
        tool_descriptions.append(description)

    return "\n".join(tool_descriptions)
