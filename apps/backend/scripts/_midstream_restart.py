#!/usr/bin/env python3
"""Kill backend mid-query; restart; prove no mutation replay and state hydrates."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
BACKEND = Path(__file__).resolve().parents[1]
DATA = Path(r"C:\Users\ty0x7\Desktop\EchoSpeak\.live-acceptance\data-storage-pass")
OUT = Path(r"C:\Users\ty0x7\Desktop\EchoSpeak\.live-acceptance\midstream_restart.json")


def http(method, url, body=None, timeout=30):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except Exception as e:
        code = getattr(e, "code", 0) or 0
        return code, {"error": str(e)}


def free_port(port: int = 8000):
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | "
            f"ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}",
        ],
        capture_output=True,
    )


def main() -> int:
    code, th = http("POST", f"{BASE}/threads", {"title": "midstream-kill"})
    tid = (th or {}).get("thread_id") or (th or {}).get("id")
    result = {"done": False, "err": None, "status": None}

    def fire():
        try:
            c, body = http(
                "POST",
                f"{BASE}/query",
                {
                    "message": "Research local-first AI architecture briefly with sources.",
                    "thread_id": tid,
                    "include_memory": False,
                },
                timeout=8,
            )
            result["status"] = c
            result["body_snip"] = str(body)[:120]
        except Exception as e:
            result["err"] = str(e)
        result["done"] = True

    t = threading.Thread(target=fire, daemon=True)
    t.start()
    time.sleep(1.8)
    free_port(8000)
    time.sleep(1.5)

    env = os.environ.copy()
    env.update(
        {
            "ECHOSPEAK_DATA_DIR": str(DATA),
            "ENABLE_SYSTEM_ACTIONS": "true",
            "ALLOW_FILE_WRITE": "true",
            "ALLOW_VIDEO_AGENT_EDITS": "true",
            "USE_LOCAL_MODELS": "true",
            "LOCAL_MODEL_PROVIDER": "lmstudio",
            "LOCAL_MODEL_URL": "http://localhost:1234",
            "LOCAL_MODEL_NAME": "google/gemma-4-e2b",
            "ORCHESTRATION_ENABLED": "false",
            "API_AUTH_ENABLED": "false",
            "KMP_DUPLICATE_LIB_OK": "TRUE",
            "MPLBACKEND": "Agg",
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", "from api.server import start_server; start_server(host='127.0.0.1', port=8000)"],
        cwd=str(BACKEND),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    healthy = False
    for _ in range(45):
        c, _ = http("GET", f"{BASE}/health", timeout=2)
        if c == 200:
            healthy = True
            break
        time.sleep(1)
    code, st = http("GET", f"{BASE}/threads/{tid}/state", timeout=15)
    # index.html should still be Final UI Title from prior UI click (no replay of unrelated writes)
    idx = Path(r"C:\Users\ty0x7\Desktop\EchoSpeak\.live-acceptance\workspace\live_coding_project\index.html")
    title_ok = idx.exists() and "Final UI Title" in idx.read_text(encoding="utf-8")
    out = {
        "tid": tid,
        "healthy": healthy,
        "state_status": code,
        "pending": (st or {}).get("pending_approval_id") if isinstance(st, dict) else None,
        "execution_status": (st or {}).get("execution_status") if isinstance(st, dict) else None,
        "fire": result,
        "no_mutation_replay_file": title_ok,
        "ok": healthy and code == 200 and title_ok,
    }
    OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2))
    # leave backend running
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
