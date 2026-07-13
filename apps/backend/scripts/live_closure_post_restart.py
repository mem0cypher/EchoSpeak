#!/usr/bin/env python3
"""Post-restart video durability + coding exclusion + research tool selection."""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path


def http(method, url, body=None, timeout=180):
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
        raw = e.read().decode() if hasattr(e, "read") else str(e)
        try:
            return code, json.loads(raw)
        except Exception:
            return code, {"error": raw}


def main() -> int:
    base = "http://127.0.0.1:8000"
    ws = Path(r"C:\Users\ty0x7\Desktop\EchoSpeak\.live-acceptance\workspace\live_coding_project")
    out = Path(r"C:\Users\ty0x7\Desktop\EchoSpeak\.live-acceptance\live_closure_post_restart.json")
    report: dict = {"steps": [], "ok": True}

    def step(name, ok, detail=None):
        report["steps"].append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"{'OK' if ok else 'FAIL'} {name}: {detail}")
        if not ok:
            report["ok"] = False

    ok = False
    health = None
    for _ in range(45):
        code, health = http("GET", f"{base}/health", timeout=3)
        if code == 200:
            ok = True
            break
        time.sleep(1)
    step("post_restart_health", ok, health)
    if not ok:
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return 1

    pre = json.loads(
        Path(r"C:\Users\ty0x7\Desktop\EchoSpeak\.live-acceptance\video_pre_restart.json").read_text(
            encoding="utf-8"
        )
    )
    did, tid, proj = pre["document_id"], pre["session_id"], pre["project_id"]
    http("POST", f"{base}/projects/{proj}/activate?thread_id={tid}", {})
    code, doc = http("GET", f"{base}/video/documents/{did}?session_id={tid}&project_id={proj}")
    clips = (doc or {}).get("clips") or []
    if not clips:
        for t in ((doc or {}).get("timeline") or {}).get("tracks") or []:
            clips.extend(t.get("clips") or [])
    vol = next((float(x.get("volume", 1)) for x in clips if x.get("id") == "truth-c1"), None)
    step(
        "post_restart_video",
        code == 200
        and int((doc or {}).get("revision") or 0) == int(pre.get("revision") or -1)
        and any(x.get("id") == "truth-c1" for x in clips)
        and vol is not None
        and abs(vol) < 1e-6,
        {
            "revision": (doc or {}).get("revision"),
            "clip_ids": [x.get("id") for x in clips],
            "volume": vol,
            "expected_revision": pre.get("revision"),
        },
    )

    # Coding sibling exclusion after real backend reload
    game_path = ws / "game.js"
    index_path = ws / "index.html"
    game_before = game_path.read_bytes()
    index_before = index_path.read_text(encoding="utf-8")
    code, th = http("POST", f"{base}/threads", {"title": "coding-reload"})
    tid2 = (th or {}).get("thread_id") or (th or {}).get("id")
    code, projb = http(
        "POST",
        f"{base}/projects/attach-folder",
        {
            "path": str(ws),
            "name": "Coding Reload",
            "trust_state": "trusted",
            "session_id": tid2,
        },
    )
    pid2 = (projb or {}).get("id")
    http("POST", f"{base}/projects/{pid2}/activate?thread_id={tid2}", {})
    step("coding_thread", bool(tid2 and pid2), f"{tid2}/{pid2}")

    code, q = http(
        "POST",
        f"{base}/query",
        {
            "message": "Change the title text in index.html to Reload Safe Title. Do not edit game.js.",
            "thread_id": tid2,
            "include_memory": False,
        },
        timeout=240,
    )
    step("coding_edit_request", code == 200, str((q or {}).get("response") or "")[:200])
    code, st = http("GET", f"{base}/threads/{tid2}/state")
    aid = str((st or {}).get("pending_approval_id") or "")
    step("coding_pending", bool(aid), aid)
    if aid:
        c1, b1 = http("POST", f"{base}/approvals/{aid}/confirm?expected_session_id={tid2}", timeout=90)
        step(
            "coding_confirm",
            c1 == 200 and bool((b1 or {}).get("success")),
            f"c1={c1} success={(b1 or {}).get('success')} resp={str((b1 or {}).get('response') or '')[:120]}",
        )
        c2, _b2 = http("POST", f"{base}/approvals/{aid}/confirm?expected_session_id={tid2}", timeout=30)
        step("coding_dup_409", c2 >= 400, f"c2={c2}")
    else:
        step("coding_confirm", False, "no approval")
        step("coding_dup_409", False, "skipped")

    game_after = game_path.read_bytes()
    index_after = index_path.read_text(encoding="utf-8")
    step(
        "game_js_byte_identical",
        game_before == game_after,
        f"len {len(game_before)}->{len(game_after)}",
    )
    step(
        "index_html_changed",
        index_before != index_after and "Reload Safe Title" in index_after,
        index_after[:160],
    )

    for label, message in [
        ("explain_no_pending", "Do not edit anything; just show corrected HTML for index.html."),
        ("explain_how_no_pending", "Explain how I could change index.html."),
        ("example_no_pending", "Show me an example HTML page."),
    ]:
        _c, _q = http(
            "POST",
            f"{base}/query",
            {"message": message, "thread_id": tid2, "include_memory": False},
            timeout=180,
        )
        _c, stn = http("GET", f"{base}/threads/{tid2}/state")
        aidn = str((stn or {}).get("pending_approval_id") or "")
        step(label, not aidn, f"aid={aidn} resp={str((_q or {}).get('response') or '')[:100]}")

    # Research ordinary public — should complete or fail honestly, not wrong-tool-only
    code, thr = http("POST", f"{base}/threads", {"title": "research-wrong-tool"})
    tidr = (thr or {}).get("thread_id") or (thr or {}).get("id")
    code, qr = http(
        "POST",
        f"{base}/query",
        {
            "message": (
                "Research the latest public information about local-first AI agents. "
                "Use web search and cite sources."
            ),
            "thread_id": tidr,
            "include_memory": False,
        },
        timeout=240,
    )
    resp = str((qr or {}).get("response") or "")
    code, st = http("GET", f"{base}/threads/{tidr}/state")
    exec_id = str((st or {}).get("last_execution_id") or "")
    tools: list[str] = []
    if exec_id:
        _c, tr = http("GET", f"{base}/executions/{exec_id}")
        if isinstance(tr, dict):
            tools = list(tr.get("tools_used") or [])
    step(
        "research_public",
        code is not None and len(resp) > 20,
        {"tools": tools, "exec": exec_id, "resp": resp[:240]},
    )
    bad_only = bool(tools) and set(tools).issubset({"browse_task", "youtube_transcript"}) and "web_search" not in set(
        tools
    )
    step("research_not_wrong_tool_only", not bad_only, f"tools={tools}")

    # YouTube-specific should still allow youtube when requested (inventory test via unit; live soft check)
    code, thy = http("POST", f"{base}/threads", {"title": "research-youtube"})
    tidy = (thy or {}).get("thread_id") or (thy or {}).get("id")
    code, qy = http(
        "POST",
        f"{base}/query",
        {
            "message": "Get the YouTube transcript for https://www.youtube.com/watch?v=dQw4w9WgXcQ if available.",
            "thread_id": tidy,
            "include_memory": False,
        },
        timeout=180,
    )
    step(
        "youtube_request_handled",
        code == 200,
        str((qy or {}).get("response") or "")[:160],
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out} ok={report['ok']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
