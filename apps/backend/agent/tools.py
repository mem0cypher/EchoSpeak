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
from datetime import datetime, timezone, timedelta
from io import BytesIO
from typing import Optional
from pathlib import Path
from urllib.parse import quote_plus, urlparse
from loguru import logger
from pydantic import BaseModel, Field, AliasChoices

from langchain_core.tools import tool
from pytesseract import pytesseract

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config, ModelProvider

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
    root = Path(getattr(config, "file_tool_root", "") or ".").expanduser()
    try:
        return root.resolve()
    except Exception:
        return Path(".").resolve()


def _safe_file_path(path: str) -> Optional[Path]:
    if not path:
        return None
    root = _file_tool_root()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
    except Exception:
        return None
    try:
        common = os.path.commonpath([str(root), str(resolved)])
    except Exception:
        return None
    if common != str(root):
        return None
    return resolved


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
    query: str = Field(
        ..., validation_alias=AliasChoices("query", "q", "search", "keywords"), description="Search query text."
    )


class LiveWebSearchArgs(BaseModel):
    query: str = Field(
        ..., validation_alias=AliasChoices("query", "q", "search", "keywords"), description="Search query text."
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
    path: str = Field(..., validation_alias=AliasChoices("path", "file", "filepath", "filename"))
    max_chars: int = Field(default=4000, ge=200, le=20000)


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


@tool(args_schema=FileListArgs, description="List files/folders within a directory (restricted to FILE_TOOL_ROOT).")
def file_list(path: Optional[str] = ".", limit: int = 50) -> str:
    root = _file_tool_root()
    target = _safe_file_path(path or ".")
    if target is None:
        return f"Path not allowed. Allowed root: {root}"
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


@tool(args_schema=FileReadArgs, description="Read a text file (restricted to FILE_TOOL_ROOT).")
def file_read(path: str, max_chars: int = 4000) -> str:
    target = _safe_file_path(path)
    if target is None:
        return "Path not allowed."
    if not target.exists():
        return "File not found."
    if target.is_dir():
        return "Path is a directory."
    try:
        data = target.read_bytes()
        if b"\x00" in data[:2000]:
            return "Binary file detected; text read skipped."
        text = data.decode("utf-8", errors="ignore")
        if max_chars and len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        return text if text.strip() else "(empty file)"
    except Exception as e:
        return f"Failed to read file: {str(e)}"


@tool(args_schema=FileWriteArgs, description="Write text to a file (restricted; opt-in system action).")
def file_write(path: str, content: str, append: bool = False) -> str:
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_file_write", False):
        return "File write is disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_FILE_WRITE=true, then restart the API."
    target = _safe_file_path(path)
    if target is None:
        return "Path not allowed."
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(target, mode, encoding="utf-8") as f:
            f.write(content or "")
        action = "Appended" if append else "Wrote"
        return f"{action} {len(content or '')} chars to {target}"
    except Exception as e:
        return f"Failed to write file: {str(e)}"


@tool(args_schema=FileMoveArgs, description="Move/rename a file or folder (restricted to FILE_TOOL_ROOT; opt-in system action).")
def file_move(src: str, dst: str, overwrite: bool = False) -> str:
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_file_write", False):
        return "File operations are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_FILE_WRITE=true, then restart the API."
    src_p = _safe_file_path(src)
    dst_p = _safe_file_path(dst)
    if src_p is None or dst_p is None:
        return "Path not allowed."
    if not src_p.exists():
        return "Source path not found."
    try:
        dst_p.parent.mkdir(parents=True, exist_ok=True)
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


@tool(args_schema=FileCopyArgs, description="Copy a file or folder (restricted to FILE_TOOL_ROOT; opt-in system action).")
def file_copy(src: str, dst: str, overwrite: bool = False) -> str:
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_file_write", False):
        return "File operations are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_FILE_WRITE=true, then restart the API."
    src_p = _safe_file_path(src)
    dst_p = _safe_file_path(dst)
    if src_p is None or dst_p is None:
        return "Path not allowed."
    if not src_p.exists():
        return "Source path not found."
    try:
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        if dst_p.exists():
            if not overwrite:
                return "Destination already exists. Set overwrite=true to replace."
            if dst_p.is_dir():
                shutil.rmtree(dst_p)
            else:
                dst_p.unlink()
        if src_p.is_dir():
            shutil.copytree(src_p, dst_p)
        else:
            shutil.copy2(src_p, dst_p)
        return f"Copied {src_p} -> {dst_p}"
    except Exception as e:
        return f"Failed to copy: {str(e)}"


@tool(args_schema=FileDeleteArgs, description="Delete a file or folder (restricted to FILE_TOOL_ROOT; opt-in system action).")
def file_delete(path: str, recursive: bool = False) -> str:
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_file_write", False):
        return "File operations are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_FILE_WRITE=true, then restart the API."
    target = _safe_file_path(path)
    if target is None:
        return "Path not allowed."
    if not target.exists():
        return "Path not found."
    try:
        if target.is_dir():
            if not recursive:
                return "Path is a directory. Set recursive=true to delete folders."
            shutil.rmtree(target)
        else:
            target.unlink()
        return f"Deleted {target}"
    except Exception as e:
        return f"Failed to delete: {str(e)}"


@tool(args_schema=FileMkdirArgs, description="Create a folder (restricted to FILE_TOOL_ROOT; opt-in system action).")
def file_mkdir(path: str, parents: bool = True, exist_ok: bool = True) -> str:
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_file_write", False):
        return "File operations are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_FILE_WRITE=true, then restart the API."
    target = _safe_file_path(path)
    if target is None:
        return "Path not allowed."
    try:
        target.mkdir(parents=bool(parents), exist_ok=bool(exist_ok))
        return f"Created folder: {target}"
    except Exception as e:
        return f"Failed to create folder: {str(e)}"


def _terminal_first_token(command: str) -> str:
    s = (command or "").strip()
    if not s:
        return ""
    try:
        import shlex

        parts = shlex.split(s, posix=False)
    except Exception:
        parts = s.split()
    if not parts:
        return ""
    token = str(parts[0]).strip().lower()
    token = token.rsplit("\\", 1)[-1]
    token = token.rsplit("/", 1)[-1]
    token = token.removesuffix(".exe") if token.endswith(".exe") else token
    return token


@tool(args_schema=TerminalRunArgs, description="Run a PowerShell command (Windows; allowlisted; opt-in system action).")
def terminal_run(command: str, cwd: Optional[str] = ".", timeout: Optional[int] = None) -> str:
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_terminal_commands", False):
        return "Terminal commands are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_TERMINAL_COMMANDS=true, then restart the API."
    if os.name != "nt":
        return "terminal_run is only supported on Windows."

    root = _file_tool_root()
    cwd_p = _safe_file_path(cwd or ".")
    if cwd_p is None:
        return f"CWD not allowed. Allowed root: {root}"
    if not cwd_p.exists() or not cwd_p.is_dir():
        return "CWD not found or is not a directory."

    allow = [str(x).strip().lower() for x in (getattr(config, "terminal_command_allowlist", None) or []) if str(x).strip()]
    if not allow:
        return "No terminal commands are allowlisted. Set TERMINAL_COMMAND_ALLOWLIST and restart the API."
    token = _terminal_first_token(command)
    allow_set = set(allow)
    if "*" not in allow_set and "powershell" not in allow_set and "ps" not in allow_set and token not in allow_set:
        return f"Command not allowlisted: {token or '(unknown)'}"

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

    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]
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
        header = f"ExitCode={proc.returncode}"
        if out:
            return header + "\n" + out
        return header
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout_s}s."
    except Exception as e:
        return f"Failed to run command: {str(e)}"


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
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_file_write", False):
        return "Artifact write is disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_FILE_WRITE=true, then restart the API."
    data = str(content or "")
    if len(data) > 200000:
        data = data[:200000].rstrip() + "…"
    root = _artifacts_root()
    fname = _safe_artifact_filename(filename)
    target = root / fname
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
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
        return f"Opened application: {app}"
    except Exception as e:
        return f"Failed to open application: {str(e)}"


@tool(args_schema=NotepadWriteArgs, description="Open Notepad, type text, and save a copy to the artifacts folder (opt-in system action).")
def notepad_write(content: str, filename: Optional[str] = None) -> str:
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
    try:
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


@tool(args_schema=WebSearchArgs, description="Search the web for current information.")
def web_search(query: str) -> str:
    """
    Search the web for current information using DuckDuckGo.

    Args:
        query: The search query.

    Returns:
        Search results as a formatted string.
    """
    try:
        query_low = (query or "").lower()

        def _is_schedule_like(q: str) -> bool:
            low = (q or "").lower().strip()
            if not low:
                return False
            schedule_terms = [
                "game",
                "match",
                "fixture",
                "schedule",
                "event",
                "concert",
                "show",
                "episode",
                "season",
                "flight",
                "departure",
                "arrival",
                "release",
                "launch",
                "plays",
                "play",
            ]
            if any(t in low for t in ["next", "upcoming"]) and any(term in low for term in schedule_terms):
                return True
            if any(t in low for t in ["when is", "when's", "when does", "start time", "starts at", "kickoff", "tipoff"]):
                if any(term in low for term in schedule_terms):
                    return True
            return False

        def _split_queries(q: str) -> list[str]:
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
            out = []
            seen = set()
            for p in parts:
                if p.lower() in seen:
                    continue
                seen.add(p.lower())
                out.append(p)
                if len(out) >= 4:
                    break
            return out

        def _extract_keywords(q: str) -> list[str]:
            text = re.sub(r"[^a-zA-Z0-9\s]", " ", (q or "").lower())
            tokens = [t.strip() for t in text.split() if t.strip()]
            stop = {
                "a",
                "an",
                "and",
                "are",
                "as",
                "at",
                "be",
                "by",
                "for",
                "from",
                "how",
                "i",
                "in",
                "is",
                "it",
                "of",
                "on",
                "or",
                "that",
                "the",
                "this",
                "to",
                "what",
                "when",
                "where",
                "who",
                "why",
                "with",
                "you",
                "your",
            }
            out = []
            seen = set()
            for t in tokens:
                if len(t) < 3:
                    continue
                if t in stop:
                    continue
                if t in seen:
                    continue
                seen.add(t)
                out.append(t)
                if len(out) >= 8:
                    break
            return out

        def _compress_extract(extract: str, q: str, max_chars: int = 900) -> str:
            s = re.sub(r"\s+", " ", (extract or "")).strip()
            if not s:
                return ""
            if len(s) <= max_chars:
                return s
            kws = _extract_keywords(q)
            if not kws:
                return s[:max_chars].rstrip() + "…"
            sents = re.split(r"(?<=[\.!\?])\s+", s)
            scored = []
            for sent in sents:
                low = sent.lower()
                score = 0
                for kw in kws:
                    if kw in low:
                        score += 1
                if score > 0:
                    scored.append((score, sent.strip()))
            scored.sort(key=lambda x: (-x[0], len(x[1])))
            picked = []
            used = set()
            total = 0
            for _, sent in scored:
                key = sent.lower()
                if key in used:
                    continue
                used.add(key)
                if not sent:
                    continue
                add_len = len(sent) + (1 if picked else 0)
                if total+add_len > max_chars:
                    continue
                picked.append(sent)
                total += add_len
                if len(picked) >= 5:
                    break
            if not picked:
                return s[:max_chars].rstrip() + "…"
            out = " ".join(picked).strip()
            if len(out) > max_chars:
                out = out[:max_chars].rstrip() + "…"
            return out

        def _enrich_with_scrapling(items: list[dict]) -> list[dict]:
            if not bool(getattr(config, "web_search_use_scrapling", False)):
                return items
            if _is_schedule_like(query_low):
                return items
            try:
                from scrapling.fetchers import Fetcher  # type: ignore
            except Exception as exc:
                logger.warning(f"Scrapling unavailable; skipping enrichment: {exc}")
                return items

            try:
                top_k = int(getattr(config, "web_search_scrapling_top_k", 2) or 2)
            except Exception:
                top_k = 2
            try:
                timeout_s = int(getattr(config, "web_search_scrapling_timeout", 20) or 20)
            except Exception:
                timeout_s = 20
            try:
                max_chars = int(getattr(config, "web_search_scrapling_max_chars", 1500) or 1500)
            except Exception:
                max_chars = 1500

            stealthy_headers = bool(getattr(config, "web_search_scrapling_stealthy_headers", True))
            impersonate = (getattr(config, "web_search_scrapling_impersonate", "chrome") or "chrome").strip() or "chrome"

            for item in items[: max(0, top_k)]:
                url = (item.get("url") or "").strip()
                if not url:
                    continue
                try:
                    page = Fetcher.get(
                        url,
                        timeout=timeout_s,
                        stealthy_headers=stealthy_headers,
                        impersonate=impersonate,
                        follow_redirects=True,
                    )
                    if getattr(page, "status", 0) != 200:
                        continue

                    title = ""
                    try:
                        title = str(page.css_first("title::text") or "").strip()
                    except Exception:
                        title = ""

                    extracted = ""
                    try:
                        extracted = str(page.get_all_text(strip=True, valid_values=True)).strip()
                    except Exception:
                        extracted = ""

                    if extracted and max_chars > 0 and len(extracted) > max_chars:
                        extracted = extracted[:max_chars].rstrip() + "…"

                    if title:
                        item["page_title"] = title
                    if extracted:
                        item["extract"] = extracted
                except Exception as exc:
                    logger.debug(f"Scrapling fetch failed for {url}: {exc}")
                    continue
            return items

        def _search_searxng(q: str) -> list[dict]:
            searx_url = (getattr(config, "searxng_url", "") or "").strip().rstrip("/")
            if not searx_url:
                return []
            try:
                import requests

                endpoint = searx_url
                if not endpoint.lower().endswith("/search"):
                    endpoint = endpoint + "/search"

                timeout_s = int(getattr(config, "searxng_timeout", getattr(config, "web_search_timeout", 10)) or 10)
                resp = requests.get(
                    endpoint,
                    params={
                        "q": q,
                        "format": "json",
                        "language": "en",
                        "safesearch": 1,
                    },
                    timeout=timeout_s,
                )
                resp.raise_for_status()
                data = resp.json() or {}
                results = data.get("results") or []
                items = []
                for result in results[:5]:
                    title = (result.get("title") or "No title").strip()
                    link = (result.get("url") or "").strip()
                    snippet = (result.get("content") or "").strip()
                    date = (
                        result.get("publishedDate")
                        or result.get("published_date")
                        or result.get("published")
                        or result.get("date")
                        or ""
                    )
                    items.append({"title": title, "url": link, "snippet": snippet, "date": str(date).strip(), "_query": q})
                return items
            except Exception as e:
                logger.warning(f"SearxNG search failed, falling back to DuckDuckGo: {e}")
                return []

        def _search_ddg(q: str) -> list[dict]:
            DDGS = None
            try:
                from ddgs import DDGS  # type: ignore
            except Exception:
                from duckduckgo_search import DDGS  # type: ignore

            results = []
            with DDGS() as ddgs:
                try:
                    results = list(ddgs.news(q, max_results=5))
                except Exception:
                    results = list(ddgs.text(q, max_results=5))

            if not results:
                refined = f"{q} latest news"
                with DDGS() as ddgs:
                    try:
                        results = list(ddgs.news(refined, max_results=5))
                    except Exception:
                        results = list(ddgs.text(refined, max_results=5))

            items = []
            for result in results[:5]:
                title = result.get("title") or "No title"
                link = result.get("url") or result.get("href") or ""
                snippet = result.get("body") or result.get("description") or ""
                date = result.get("date") or result.get("published") or result.get("published_date") or ""
                items.append(
                    {
                        "title": str(title).strip(),
                        "url": str(link).strip(),
                        "snippet": str(snippet).strip(),
                        "date": str(date).strip(),
                        "_query": q,
                    }
                )
            return items

        queries = _split_queries(query)
        if not queries:
            return "No search results found."

        def _score_item(item: dict, q: str) -> int:
            blob = " ".join(
                [
                    str(item.get("title") or ""),
                    str(item.get("snippet") or ""),
                    str(item.get("page_title") or ""),
                    str(item.get("extract") or ""),
                ]
            ).lower()
            if not blob.strip():
                return 0
            score = 0
            for kw in _extract_keywords(q):
                if kw and kw in blob:
                    score += 1
            return score

        def _parse_date_value(val: str) -> Optional[datetime]:
            s = (val or "").strip()
            if not s:
                return None
            low = s.lower()
            m = re.match(r"^(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago\b", low)
            if m:
                n = int(m.group(1))
                unit = m.group(2)
                if unit == "minute":
                    return datetime.now(timezone.utc) - timedelta(minutes=n)
                if unit == "hour":
                    return datetime.now(timezone.utc) - timedelta(hours=n)
                if unit == "day":
                    return datetime.now(timezone.utc) - timedelta(days=n)
                if unit == "week":
                    return datetime.now(timezone.utc) - timedelta(weeks=n)
                if unit == "month":
                    return datetime.now(timezone.utc) - timedelta(days=30 * n)
                if unit == "year":
                    return datetime.now(timezone.utc) - timedelta(days=365 * n)

            iso = s.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                pass
            try:
                dt = datetime.strptime(s, "%Y-%m-%d")
                return dt.replace(tzinfo=timezone.utc)
            except Exception:
                return None

        def _recency_ts(item: dict) -> float:
            dt = _parse_date_value(str(item.get("date") or ""))
            if dt is None:
                return 0.0
            try:
                return float(dt.timestamp())
            except Exception:
                return 0.0

        blocked_domains = set(
            [
                str(d).strip().lower().lstrip(".")
                for d in (getattr(config, "web_search_blocked_domains", None) or [])
                if str(d).strip()
            ]
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

        merged = []
        seen_urls = set()
        for q in queries:
            items = _search_searxng(q)
            if not items:
                items = _search_ddg(q)
            for it in items:
                url = (it.get("url") or "").strip()
                if not url:
                    continue
                if _url_is_blocked(url):
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                merged.append(it)
                if len(merged) >= 20:
                    break
            if len(merged) >= 20:
                break

        if not merged:
            return "No search results found."

        merged = _enrich_with_scrapling(merged)

        base_q = " ".join(queries)
        merged.sort(
            key=lambda it: (
                _score_item(it, f"{base_q} {str(it.get('_query') or '')}"),
                _recency_ts(it),
                len(str(it.get("extract") or "")),
                len(str(it.get("snippet") or "")),
            ),
            reverse=True,
        )

        formatted_results = []
        multi = len(queries) > 1
        for i, item in enumerate(merged[:10], 1):
            title = (item.get("title") or "No title").strip()
            link = (item.get("url") or "").strip()
            snippet = (item.get("snippet") or "").strip()
            date = (item.get("date") or "").strip()
            page_title = (item.get("page_title") or "").strip()
            raw_extract = (item.get("extract") or "").strip()
            src_q = (item.get("_query") or "").strip()
            extract = _compress_extract(raw_extract, src_q or query, max_chars=900)

            block = [f"{i}. {title}", f"   URL: {link}"]
            if multi and src_q:
                block.append(f"   Query: {src_q}")
            if date:
                block.append(f"   Date: {date}")
            if snippet:
                block.append(f"   Snippet: {snippet[:200]}...")
            if page_title and page_title != title:
                block.append(f"   Page: {page_title}")
            if extract:
                block.append(f"   Extract: {extract}")
            formatted_results.append("\n".join(block))

        return "\n\n".join(formatted_results) if formatted_results else "No search results found."

    except ImportError:
        logger.error("ddgs/duckduckgo-search not installed")
        return "Web search is not available. Please install ddgs (preferred) or duckduckgo-search."
    except Exception as e:
        logger.error(f"Web search failed: {e}")
        return f"Search failed: {str(e)}"


@tool(args_schema=LiveWebSearchArgs, description="Browse live/dynamic pages with Playwright for real-time info (scores, weather, stocks).")
def live_web_search(query: str) -> str:
    if not getattr(config, "enable_system_actions", False) or not getattr(config, "allow_playwright", False):
        return "System actions are disabled. To enable: set ENABLE_SYSTEM_ACTIONS=true and ALLOW_PLAYWRIGHT=true, then restart the API."

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return "Playwright is not available. Please install playwright and run 'playwright install'."

    q = (query or "").strip()
    if not q:
        return "No search query provided."

    urls: list[str] = []
    try:
        try:
            from ddgs import DDGS  # type: ignore
        except Exception:
            from duckduckgo_search import DDGS  # type: ignore

        with DDGS() as ddgs:
            results = list(ddgs.text(q, max_results=5))
        for r in results:
            link = (r.get("href") or r.get("url") or "").strip()
            if link and link not in urls:
                urls.append(link)
            if len(urls) >= 2:
                break
    except Exception as exc:
        logger.warning(f"live_web_search query results failed; falling back to DuckDuckGo HTML: {exc}")

    if not urls:
        urls = [f"https://duckduckgo.com/?q={quote_plus(q)}"]

    extracts: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            for u in urls[:2]:
                try:
                    page.goto(u, wait_until="domcontentloaded", timeout=45000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    try:
                        page.wait_for_timeout(1200)
                    except Exception:
                        pass

                    title = ""
                    try:
                        title = page.title() or ""
                    except Exception:
                        title = ""

                    try:
                        body_text = page.inner_text("body")
                    except Exception:
                        body_text = page.content() or ""

                    body_text = re.sub(r"\s+", " ", (body_text or "")).strip()
                    if len(body_text) > 8000:
                        body_text = body_text[:8000].rstrip() + "…"
                    extracts.append(f"Title: {title}\nURL: {u}\n\nContent:\n{body_text}")
                except Exception as exc:
                    extracts.append(f"URL: {u}\n\nContent:\nFailed to load page: {exc}")
            browser.close()
    except Exception as exc:
        return f"Live browse failed: {exc}"

    return "\n\n---\n\n".join([x for x in extracts if x.strip()]) or "No results found."


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
        description="Website URL to open in an automated browser.",
    )
    task: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("task", "instruction", "instructions", "goal"),
        description="What to look for or extract from the page.",
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
            data_dir = Path(__file__).resolve().parent.parent / "data"
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


def get_available_tools() -> list:
    """
    Get list of available tools based on dependencies.

    Returns:
        List of tool functions.
    """
    tools = [
        live_web_search,
        web_search,
        get_system_time,
        calculate,
        youtube_transcript,
        browse_task,
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
        system_info,
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
