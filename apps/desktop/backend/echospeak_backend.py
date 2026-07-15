"""Packaged EchoSpeak API sidecar entrypoint.

The desktop host is authoritative for port, data directory, authentication key,
and process lifetime. This entrypoint refuses to weaken those constraints.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys
import threading
import time


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EchoSpeak desktop backend")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--parent-pid", required=True, type=int)
    return parser


def _validate_desktop_contract(args: argparse.Namespace) -> tuple[Path, Path]:
    if args.host != "127.0.0.1":
        raise SystemExit("Desktop backend only accepts the 127.0.0.1 loopback host")
    if not 1 <= args.port <= 65535:
        raise SystemExit("Desktop backend port is outside the valid range")
    if args.parent_pid <= 0:
        raise SystemExit("Desktop backend requires a valid desktop parent PID")
    session_key = os.getenv("API_AUTH_KEY", "").strip()
    if os.getenv("API_AUTH_ENABLED", "").lower() != "true" or not session_key:
        raise SystemExit("Desktop backend requires a per-launch API authentication key")
    if os.getenv("API_AUTH_LOCALHOST_BYPASS", "").lower() != "false":
        raise SystemExit("Desktop backend requires localhost authentication")
    data_value = os.getenv("ECHOSPEAK_DATA_DIR", "").strip()
    if not data_value:
        raise SystemExit("Desktop backend requires an application data directory")
    data_dir = Path(data_value).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_value = os.getenv("ECHOSPEAK_LOGS_DIR", "").strip()
    if not logs_value:
        raise SystemExit("Desktop backend requires an application log directory")
    logs_dir = Path(logs_value).expanduser().resolve()
    logs_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, logs_dir


def _seed_mutable_defaults(data_dir: Path) -> None:
    """Copy packaged defaults once; all later edits belong to app data."""
    bundled_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2] / "backend"))
    source_soul = bundled_root / "SOUL.md"
    target_soul = data_dir / "SOUL.md"
    if not target_soul.exists() and source_soul.is_file():
        shutil.copyfile(source_soul, target_soul)
    os.environ["SOUL_PATH"] = str(target_soul)


def _watch_windows_parent(parent_pid: int) -> None:
    import ctypes

    synchronize = 0x00100000
    infinite = 0xFFFFFFFF
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, parent_pid)
    if not handle:
        os._exit(0)
    try:
        ctypes.windll.kernel32.WaitForSingleObject(handle, infinite)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)
    os._exit(0)


def _watch_posix_parent(parent_pid: int) -> None:
    while True:
        try:
            os.kill(parent_pid, 0)
        except OSError:
            os._exit(0)
        time.sleep(1.0)


def _start_parent_watchdog(parent_pid: int, name: str) -> None:
    target = _watch_windows_parent if os.name == "nt" else _watch_posix_parent
    threading.Thread(target=target, args=(parent_pid,), name=name, daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data_dir, logs_dir = _validate_desktop_contract(args)
    os.environ["ECHOSPEAK_RUNTIME_KIND"] = "desktop"
    os.environ["API_HOST"] = "127.0.0.1"
    os.environ["API_PORT"] = str(args.port)
    os.environ["API_AUTH_ENABLED"] = "true"
    os.environ["API_AUTH_LOCALHOST_BYPASS"] = "false"
    os.environ["API_TRUST_PROXY_HEADERS"] = "false"
    os.environ["ECHOSPEAK_LOGS_DIR"] = str(logs_dir)
    _seed_mutable_defaults(data_dir)
    os.chdir(data_dir)
    _start_parent_watchdog(args.parent_pid, "desktop-parent-watchdog")
    # PyInstaller one-file mode runs Python in a worker below a bootloader.
    # Watching both owners prevents an early bootloader crash from leaving a
    # worker that has not yet opened its authenticated admin endpoint.
    bootloader_pid = os.getppid()
    if bootloader_pid > 0 and bootloader_pid != args.parent_pid:
        _start_parent_watchdog(bootloader_pid, "desktop-bootloader-watchdog")

    # PyInstaller resolves these modules from the Analysis pathex. Source runs
    # resolve them from apps/backend for packaging diagnostics.
    backend_root = Path(__file__).resolve().parents[2] / "backend"
    if backend_root.exists() and str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    from api.server import start_server

    start_server(host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
