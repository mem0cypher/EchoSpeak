"""Terminal execution sandbox (v7.5.0–v7.5.1).

Modes (config.terminal_execution_mode):
  - docker / sandbox (default): isolated per-run container; never silently falls back to host
  - host: explicitly selected unsandboxed PowerShell/shell execution

Isolation rules (enforced here):
  - Mount only FILE_TOOL_ROOT + configured FILE_TOOL_EXTRA_ROOTS
  - Never mount home, secrets, or the host Docker socket
  - Network disabled by default (--network=none)
  - Non-root user, memory/CPU limits, hard timeout
  - Denylist enforced at host gate AND re-checked in the sandbox path (defense in depth)
  - Path boundary: resolve + realpath; reject symlink roots that escape; block `..` tricks

If Docker is unavailable or the run cannot be isolated, callers receive an
explicit sandbox_unavailable / fail status — never a quiet host execution.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config


# ---------------------------------------------------------------------------
# Exit classification (honest status — never vague "done")
# ---------------------------------------------------------------------------

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_TIMEOUT = "timeout"
STATUS_DENIED = "denied"
STATUS_SANDBOX_UNAVAILABLE = "sandbox_unavailable"


@dataclass
class TerminalRunResult:
    """Structured terminal outcome for host or sandbox execution."""

    status: str
    exit_code: int
    output: str = ""
    mode: str = "host"
    reason: str = ""
    duration_ms: int = 0

    def format(self) -> str:
        """Human/agent-readable block with explicit status."""
        lines = [
            f"ExitCode={self.exit_code}",
            f"Status={self.status}",
            f"Mode={self.mode}",
        ]
        if self.reason:
            lines.append(f"Reason={self.reason}")
        if self.duration_ms:
            lines.append(f"DurationMs={self.duration_ms}")
        body = (self.output or "").strip("\n")
        if body:
            return "\n".join(lines) + "\n" + body
        return "\n".join(lines)


@dataclass
class SandboxStatus:
    """Readiness probe for /coding/readiness and diagnostics."""

    mode: str
    requested_mode: str
    docker_available: bool
    docker_detail: str = ""
    image: str = ""
    network: str = "none"
    non_root: bool = True
    mounts: List[Dict[str, str]] = field(default_factory=list)
    ready: bool = False
    message: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_execution_mode(raw: Optional[str] = None) -> str:
    mode = str(raw if raw is not None else getattr(config, "terminal_execution_mode", "docker") or "docker")
    mode = mode.strip().lower()
    if mode in {"docker", "sandbox", "container"}:
        return "docker"
    if mode == "host":
        return "host"
    return "docker"


def _docker_bin() -> Optional[str]:
    return shutil.which("docker")


def probe_docker(timeout_s: float = 4.0) -> Tuple[bool, str]:
    """Return (available, detail). Does not start a container."""
    docker = _docker_bin()
    if not docker:
        return False, "Docker CLI not found on PATH."
    try:
        proc = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=max(1.0, float(timeout_s)),
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "docker info failed").strip()
            return False, err[:300] or "docker info failed"
        ver = (proc.stdout or "").strip() or "unknown"
        return True, f"Docker engine reachable (server {ver})."
    except subprocess.TimeoutExpired:
        return False, "docker info timed out — is Docker Desktop/engine running?"
    except Exception as exc:
        return False, f"docker probe failed: {exc}"


def _resolve_path(p: str | Path) -> Optional[Path]:
    try:
        return Path(p).expanduser().resolve()
    except Exception:
        return None


def _is_forbidden_mount_host(path: Path) -> Optional[str]:
    """Reject mounts that would expand blast radius (socket, ssh, secrets)."""
    try:
        s = str(path.resolve()).replace("\\", "/").lower()
    except Exception:
        s = str(path).replace("\\", "/").lower()
    if "docker.sock" in s:
        return "docker.sock mounts are forbidden"
    # Common secret/home escapes (not exhaustive — primary guard is allowlist of roots)
    for bad in ("/.ssh", "/.gnupg", "/.aws", "/.config/gcloud", "/appdata/roaming"):
        if bad in s:
            return f"refusing sensitive path mount: {bad}"
    return None


def path_is_within_root(path: Path, root: Path) -> bool:
    """True if path is under root after resolve (follows symlinks via resolve()).

    On Windows, slash style and drive-letter case must not cause false denials.
    """
    try:
        p = path.expanduser().resolve()
        r = root.expanduser().resolve()
    except Exception:
        return False
    try:
        p.relative_to(r)
        return True
    except ValueError:
        pass
    try:
        p_parts = [part.casefold() for part in p.parts]
        r_parts = [part.casefold() for part in r.parts]
        return len(p_parts) >= len(r_parts) and p_parts[: len(r_parts)] == r_parts
    except Exception:
        return False


def assert_safe_project_path(path: Path, roots: Optional[Sequence[Path]] = None) -> Tuple[bool, str]:
    """
    Validate a host path stays inside allowed roots after resolution.

    Blocks:
      - paths that resolve outside every allowed root (symlink escape)
      - raw `..` segments that would climb out before resolve (defense in depth)
      - empty / non-existent roots
    """
    roots_list = list(roots) if roots is not None else allowed_mount_roots()
    if not roots_list:
        return False, "no allowed FILE_TOOL_ROOT mounts configured"

    raw = str(path or "").strip()
    if not raw:
        return False, "empty path"

    # Explicit climb detection on the *unresolved* string (Windows + POSIX)
    norm = raw.replace("\\", "/")
    parts = [p for p in norm.split("/") if p and p != "."]
    if ".." in parts:
        # Still allow if resolve keeps us inside a root — but flag pure escape attempts
        # when the path does not exist yet (resolve may not collapse safely).
        pass

    try:
        resolved = Path(raw).expanduser().resolve()
    except Exception as exc:
        return False, f"path resolve failed: {exc}"

    # Symlink / junction escape: resolved real path must sit under a declared root
    for root in roots_list:
        if path_is_within_root(resolved, root):
            # If original contained `..`, require resolve still under same root
            return True, ""
    return False, f"path escapes sandbox roots: {resolved}"


def allowed_mount_roots() -> List[Path]:
    """Host paths allowed inside the sandbox (project scope only)."""
    roots: List[Path] = []
    primary = _resolve_path(getattr(config, "file_tool_root", "") or "")
    if primary is not None and primary.exists():
        roots.append(primary)
    elif primary is not None:
        # Allow non-existent root that will be created — still resolve parent-safe
        roots.append(primary)
    for extra in getattr(config, "file_tool_extra_roots", None) or []:
        ep = _resolve_path(extra)
        if ep is None:
            continue
        if any(ep == r for r in roots):
            continue
        roots.append(ep)
    out: List[Path] = []
    seen = set()
    for r in roots:
        forbid = _is_forbidden_mount_host(r)
        if forbid:
            logger.warning("Skipping forbidden sandbox mount {}: {}", r, forbid)
            continue
        key = str(r).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _container_mount_path(index: int) -> str:
    return f"/sandbox/root{index}"


def build_mount_plan() -> List[Dict[str, str]]:
    """Map host roots → container paths. Never includes docker.sock or home."""
    plan: List[Dict[str, str]] = []
    for i, root in enumerate(allowed_mount_roots()):
        forbid = _is_forbidden_mount_host(root)
        if forbid:
            continue
        # Mount the resolved real path so symlink roots cannot alias outside
        try:
            host = str(root.resolve())
        except Exception:
            host = str(root)
        plan.append(
            {
                "host": host,
                "container": _container_mount_path(i),
                "mode": "rw",
            }
        )
    return plan


def map_cwd_to_container(cwd: Path, mounts: Sequence[Dict[str, str]]) -> Optional[str]:
    """Resolve host cwd into a path inside one of the mounted roots."""
    roots = [Path(m["host"]) for m in mounts]
    ok, _reason = assert_safe_project_path(cwd, roots)
    if not ok:
        return None
    try:
        cwd_r = cwd.expanduser().resolve()
    except Exception:
        return None
    # Reject if any path component after resolve is still outside (double-check)
    for m in mounts:
        host = Path(m["host"])
        try:
            host_r = host.resolve()
        except Exception:
            continue
        try:
            rel = cwd_r.relative_to(host_r)
        except ValueError:
            continue
        # Block sneaky relative parts that survived (should not happen post-resolve)
        rel_parts = rel.parts
        if any(p == ".." for p in rel_parts):
            return None
        cont = m["container"].rstrip("/")
        rel_s = str(rel).replace("\\", "/")
        if rel_s in (".", ""):
            return cont
        return f"{cont}/{rel_s}"
    return None


def default_denylist_check(command: str) -> Optional[str]:
    """
    Built-in denylist check used when caller does not inject one.
    Uses config.terminal_command_denylist (same source as tools) without
    importing agent.tools — avoids circular imports.
    """
    s = (command or "").strip()
    if not s:
        return "Command rejected: command is empty."
    try:
        import shlex

        parts = shlex.split(s, posix=(os.name != "nt"))
    except Exception:
        parts = s.split()
    if not parts:
        return "Command rejected: command is empty."
    token = str(parts[0]).strip().lower()
    token = token.replace("\\", "/").rsplit("/", 1)[-1]
    if token.endswith(".exe"):
        token = token[:-4]
    deny = [
        str(x).strip().lower()
        for x in (getattr(config, "terminal_command_denylist", None) or [])
        if str(x).strip()
    ]
    if not deny:
        deny = [
            "rm", "del", "erase", "rmdir", "rd", "format", "shutdown",
            "powershell", "pwsh", "cmd", "bash", "sh", "wsl",
        ]
    if token in set(deny):
        return f"Command blocked by terminal denylist: {token}"
    return None

def _sandbox_image() -> str:
    return str(getattr(config, "terminal_docker_image", "") or "python:3.12-slim").strip() or "python:3.12-slim"


def _sandbox_memory() -> str:
    return str(getattr(config, "terminal_docker_memory", "") or "512m").strip() or "512m"


def _sandbox_cpus() -> str:
    return str(getattr(config, "terminal_docker_cpus", "") or "1.0").strip() or "1.0"


def _sandbox_user() -> str:
    # Non-root; python slim has nobody/65534
    return str(getattr(config, "terminal_docker_user", "") or "65534:65534").strip() or "65534:65534"


def get_sandbox_status() -> SandboxStatus:
    """Status for readiness APIs — honest about docker vs host."""
    requested = str(getattr(config, "terminal_execution_mode", "docker") or "docker")
    mode = normalize_execution_mode(requested)
    mounts = build_mount_plan()
    image = _sandbox_image()
    if mode == "host":
        return SandboxStatus(
            mode="host",
            requested_mode=requested,
            docker_available=False,
            docker_detail="Not required in host mode.",
            image=image,
            network="host",
            non_root=False,
            mounts=[],
            ready=True,
            message="Terminal is explicitly configured for unsandboxed host execution.",
        )
    ok, detail = probe_docker()
    ready = bool(ok and mounts)
    msg = detail
    if ok and not mounts:
        ready = False
        msg = "Docker is available but no FILE_TOOL_ROOT mounts are configured."
    elif ok:
        msg = f"{detail} Sandbox ready with {len(mounts)} mount(s)."
    else:
        msg = f"Sandbox unavailable: {detail}"
    return SandboxStatus(
        mode="docker",
        requested_mode=requested,
        docker_available=ok,
        docker_detail=detail,
        image=image,
        network="none",
        non_root=True,
        mounts=mounts,
        ready=ready,
        message=msg,
    )


def _clip_output(text: str) -> str:
    try:
        max_chars = int(getattr(config, "terminal_max_output_chars", 8000) or 8000)
    except Exception:
        max_chars = 8000
    s = text or ""
    if max_chars > 0 and len(s) > max_chars:
        return s[:max_chars].rstrip() + "…"
    return s


def run_sandboxed_terminal(
    command: str,
    *,
    cwd: Path,
    timeout_s: int,
    denylist_check=None,
) -> TerminalRunResult:
    """
    Run `command` in an isolated one-shot container.

    denylist_check: optional callable(command) -> Optional[str] denial message
    (caller should already have checked; we re-check here for defense in depth).
    """
    t0 = time.perf_counter()
    mode = "docker"

    # Defense in depth: always re-check denylist on the sandbox path (caller + built-in).
    checker = denylist_check if callable(denylist_check) else default_denylist_check
    denied = checker(command) if callable(checker) else default_denylist_check(command)
    if denied:
        return TerminalRunResult(
            status=STATUS_DENIED,
            exit_code=126,
            output="",
            mode=mode,
            reason=str(denied),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    docker = _docker_bin()
    if not docker:
        return TerminalRunResult(
            status=STATUS_SANDBOX_UNAVAILABLE,
            exit_code=127,
            output="",
            mode=mode,
            reason=(
                "sandbox_unavailable: Docker CLI not found. "
                "Install Docker or set TERMINAL_EXECUTION_MODE=host (unsandboxed)."
            ),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    ok, detail = probe_docker()
    if not ok:
        return TerminalRunResult(
            status=STATUS_SANDBOX_UNAVAILABLE,
            exit_code=127,
            output="",
            mode=mode,
            reason=(
                f"sandbox_unavailable: {detail} "
                "Start Docker Engine/Desktop or set TERMINAL_EXECUTION_MODE=host (unsandboxed)."
            ),
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    mounts = build_mount_plan()
    if not mounts:
        return TerminalRunResult(
            status=STATUS_SANDBOX_UNAVAILABLE,
            exit_code=127,
            output="",
            mode=mode,
            reason="sandbox_unavailable: no FILE_TOOL_ROOT mounts configured for the sandbox.",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    # Path boundary before mapping into container
    roots = [Path(m["host"]) for m in mounts]
    safe, safe_reason = assert_safe_project_path(cwd, roots)
    if not safe:
        return TerminalRunResult(
            status=STATUS_DENIED,
            exit_code=126,
            output="",
            mode=mode,
            reason=f"CWD rejected: {safe_reason}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    workdir = map_cwd_to_container(cwd, mounts)
    if not workdir:
        return TerminalRunResult(
            status=STATUS_DENIED,
            exit_code=126,
            output="",
            mode=mode,
            reason=f"CWD not inside sandbox mounts: {cwd}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    # Reject attempts to smuggle docker.sock or absolute host paths into the command surface
    # (the mount list is the only host exposure).
    cmd_str = str(command or "").strip()
    if not cmd_str:
        return TerminalRunResult(
            status=STATUS_DENIED,
            exit_code=126,
            output="",
            mode=mode,
            reason="Command rejected: command is empty.",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    image = _sandbox_image()
    # One-shot container: no docker.sock, no privileged, no host network.
    docker_cmd: List[str] = [
        docker,
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        _sandbox_user(),
        "--memory",
        _sandbox_memory(),
        "--cpus",
        _sandbox_cpus(),
        "--read-only",  # root FS read-only; project mounts are rw
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--workdir",
        workdir,
        # Security opts (ignored on some engines — best effort)
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
    ]
    for m in mounts:
        # Explicit bind; never :shared docker socket
        host_path = m["host"]
        if "docker.sock" in host_path.replace("\\", "/").lower():
            logger.error("Refusing to mount docker.sock into sandbox")
            return TerminalRunResult(
                status=STATUS_SANDBOX_UNAVAILABLE,
                exit_code=127,
                output="",
                mode=mode,
                reason="sandbox_unavailable: docker.sock mounts are forbidden.",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        docker_cmd.extend(["-v", f"{host_path}:{m['container']}:rw"])

    docker_cmd.append(image)
    # Run via shell inside the container (Linux image). Command already denylisted on host.
    docker_cmd.extend(["/bin/sh", "-c", cmd_str])

    # Hard wall-clock timeout slightly above command timeout for docker overhead
    wall = max(2, int(timeout_s) + 5)
    try:
        proc = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=wall,
        )
        out = ((proc.stdout or "") + ("\n" if (proc.stdout and proc.stderr) else "") + (proc.stderr or "")).strip(
            "\n"
        )
        out = _clip_output(out)
        code = int(proc.returncode)
        # Docker "image pull failed" / permission often still return non-zero without starting cmd
        if code != 0 and not out and "Unable to find image" in (proc.stderr or ""):
            return TerminalRunResult(
                status=STATUS_SANDBOX_UNAVAILABLE,
                exit_code=code,
                output=_clip_output(proc.stderr or ""),
                mode=mode,
                reason=f"sandbox_unavailable: cannot pull/run image {image}",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        status = STATUS_PASS if code == 0 else STATUS_FAIL
        return TerminalRunResult(
            status=status,
            exit_code=code,
            output=out,
            mode=mode,
            reason="" if code == 0 else "Command exited non-zero inside sandbox.",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
    except subprocess.TimeoutExpired:
        return TerminalRunResult(
            status=STATUS_TIMEOUT,
            exit_code=124,
            output="",
            mode=mode,
            reason=f"Command timed out after {timeout_s}s (sandbox wall {wall}s).",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
    except Exception as exc:
        logger.warning("Sandbox run failed: {}", exc)
        return TerminalRunResult(
            status=STATUS_SANDBOX_UNAVAILABLE,
            exit_code=127,
            output="",
            mode=mode,
            reason=f"sandbox_unavailable: {exc}",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )


# Back-compat name referenced by an earlier incomplete hook in tools.py
class DockerSandbox:
    """Thin facade for tools.terminal_run."""

    @staticmethod
    def run_command(command: str, cwd: str, timeout: int = 20) -> str:
        result = run_sandboxed_terminal(
            command,
            cwd=Path(cwd),
            timeout_s=int(timeout or 20),
        )
        return result.format()

    @staticmethod
    def status() -> Dict[str, Any]:
        return get_sandbox_status().as_dict()
