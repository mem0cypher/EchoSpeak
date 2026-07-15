"""Full process restart validation: kill backend PID, restart, verify durable state.

Usage:
  python scripts/live_process_restart.py --base http://127.0.0.1:8000 --meta .../live_meta.json --pid-file .../backend.pid
If --pid-file missing, discovers listener on port from --base.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional


def http_json(method: str, url: str, body: Optional[dict] = None, timeout: float = 60.0):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw) if raw else {"error": str(exc)}
        except Exception:
            return exc.code, {"error": raw}


def wait_health(base: str, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            code, body = http_json("GET", f"{base}/health", timeout=3)
            if code == 200:
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def find_pid_on_port(port: int) -> Optional[int]:
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            errors="ignore",
        )
    except Exception:
        return None
    needle = f":{port}"
    for line in out.splitlines():
        if needle in line and "LISTENING" in line.upper():
            parts = line.split()
            try:
                return int(parts[-1])
            except Exception:
                continue
    return None


def kill_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, capture_output=True)
    else:
        os.kill(pid, signal.SIGTERM)


def start_backend(data_dir: str, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["ECHOSPEAK_DATA_DIR"] = data_dir
    env["ENABLE_SYSTEM_ACTIONS"] = "true"
    env["ALLOW_FILE_WRITE"] = "true"
    env["USE_LOCAL_MODELS"] = "true"
    env["LOCAL_MODEL_PROVIDER"] = "lmstudio"
    env["LOCAL_MODEL_URL"] = "http://localhost:1234"
    env["LOCAL_MODEL_NAME"] = "google/gemma-4-e2b"
    env["API_PORT"] = str(port)
    env["ORCHESTRATION_ENABLED"] = "false"
    env["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    backend = Path(__file__).resolve().parents[1]
    # Use a fresh python process
    return subprocess.Popen(
        [sys.executable, "-c", f"from api.server import start_server; start_server(host='127.0.0.1', port={port})"],
        cwd=str(backend),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--meta", required=True)
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    base = args.base.rstrip("/")
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    data_dir = meta["data"]
    rows = []

    def rec(name: str, ok: bool, detail: str = "") -> None:
        rows.append({"name": name, "ok": ok, "detail": detail})
        print(("PASS" if ok else "FAIL"), name, detail)

    # Create pending approval state before kill
    code, th = http_json("POST", f"{base}/threads", {"title": "restart-soak"}, timeout=20)
    tid = (th or {}).get("thread_id") or (th or {}).get("id")
    if not tid:
        rec("pre_restart_thread", False, str(th))
        Path(meta["base"], "live_restart_report.json").write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")
        return 1
    rec("pre_restart_thread", True, tid)

    # Attach project for coding approval pending
    code, proj = http_json(
        "POST",
        f"{base}/projects/attach-folder",
        {
            "path": meta["workspace"],
            "name": "Restart Project",
            "trust_state": "trusted",
            "session_id": tid,
        },
        timeout=30,
    )
    pid = (proj or {}).get("id")
    rec("pre_restart_project", code < 400 and bool(pid), str(pid))
    http_json("POST", f"{base}/projects/{pid}/activate?thread_id={tid}", {}, timeout=20)

    code, q = http_json(
        "POST",
        f"{base}/query",
        {
            "message": "Change the title in index.html only. Do not edit game.js.",
            "thread_id": tid,
            "include_memory": False,
        },
        timeout=300,
    )
    rec("pre_restart_propose", code == 200, str((q or {}).get("response") or "")[:100])

    code, approvals = http_json("GET", f"{base}/approvals?thread_id={tid}", timeout=20)
    items = approvals if isinstance(approvals, list) else (approvals or {}).get("items") or []
    pending = [a for a in items if a.get("status") == "pending"]
    approval_id = pending[0]["id"] if pending else ""
    rec("pre_restart_pending_approval", bool(approval_id), approval_id)

    # Snapshot index.html before kill (should be unchanged if approval pending)
    idx = Path(meta["workspace"]) / "index.html"
    before_bytes = idx.read_bytes()

    # Kill real backend process
    backend_pid = find_pid_on_port(args.port)
    if not backend_pid:
        rec("find_backend_pid", False, "no listener")
        Path(meta["base"], "live_restart_report.json").write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")
        return 1
    rec("find_backend_pid", True, str(backend_pid))
    kill_pid(backend_pid)
    # Ensure the old PID is gone (port may still flap during respawn).
    dead = False
    for _ in range(20):
        time.sleep(0.5)
        if os.name == "nt":
            probe = subprocess.run(
                ["tasklist", "/FI", f"PID eq {backend_pid}"],
                capture_output=True,
                text=True,
            )
            if str(backend_pid) not in (probe.stdout or ""):
                dead = True
                break
        else:
            try:
                os.kill(backend_pid, 0)
            except OSError:
                dead = True
                break
    rec("backend_terminated", dead, f"killed={backend_pid}")

    # Restart process
    proc = start_backend(data_dir, args.port)
    rec("backend_restart_spawned", proc.pid is not None, f"new_pid={proc.pid}")
    up = wait_health(base, timeout=120)
    rec("backend_health_after_restart", up, "")

    if not up:
        Path(meta["base"], "live_restart_report.json").write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")
        return 1

    # Pending approval should still exist and not auto-apply
    code, approvals2 = http_json("GET", f"{base}/approvals?thread_id={tid}", timeout=20)
    items2 = approvals2 if isinstance(approvals2, list) else (approvals2 or {}).get("items") or []
    pending2 = [a for a in items2 if a.get("id") == approval_id]
    still_pending = bool(pending2) and pending2[0].get("status") == "pending"
    rec("approval_survives_restart", still_pending, str((pending2[0] if pending2 else {}) .get("status")))
    rec("no_mutation_before_confirm", idx.read_bytes() == before_bytes, "")

    # Confirm once after restart
    if approval_id:
        c1, b1 = http_json("POST", f"{base}/approvals/{approval_id}/confirm?expected_session_id={tid}", timeout=180)
        rec("confirm_after_restart", c1 == 200 and bool((b1 or {}).get("success")), f"{c1}")
        c2, b2 = http_json("POST", f"{base}/approvals/{approval_id}/confirm?expected_session_id={tid}", timeout=60)
        rec(
            "duplicate_confirm_after_restart_fail_closed",
            c2 >= 400 or not (b2 or {}).get("success"),
            f"{c2}",
        )

    # Memory tombstone path: request forget after save if possible
    code, mem = http_json(
        "POST",
        f"{base}/query",
        {
            "message": "Remember that restart-soak marker is Aurora Gate.",
            "thread_id": tid,
            "include_memory": True,
        },
        timeout=240,
    )
    rec("memory_after_restart", code == 200, str((mem or {}).get("response") or "")[:80])

    out = Path(meta["base"]) / "live_restart_report.json"
    out.write_text(json.dumps({"rows": rows, "backend_new_pid": proc.pid}, indent=2), encoding="utf-8")
    n_ok = sum(1 for r in rows if r["ok"])
    n_fail = sum(1 for r in rows if not r["ok"])
    print(f"\n=== RESTART SUMMARY: {n_ok} passed, {n_fail} failed / {len(rows)} ===")
    print(f"Wrote {out}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
