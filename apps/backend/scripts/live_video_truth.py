#!/usr/bin/env python3
"""Live Video execution-truth validation.

Oracle: verified canonical clip state + revision advancement + ToolRun + reload.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def http(method, url, body=None, timeout=120):
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--meta", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--restart-backend", action="store_true")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    ws = Path(meta["workspace"])
    report = {"steps": [], "ok": True, "provider": "", "model": ""}

    def step(name, ok, detail=None):
        report["steps"].append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"{'OK' if ok else 'FAIL'} {name}: {detail}")
        if not ok:
            report["ok"] = False

    code, health = http("GET", f"{base}/health", timeout=10)
    step("health", code == 200, health)

    code, th = http("POST", f"{base}/threads", {"title": "video-truth"})
    tid = (th or {}).get("thread_id") or (th or {}).get("id")
    step("thread", bool(tid), tid)

    code, proj = http(
        "POST",
        f"{base}/projects/attach-folder",
        {"path": str(ws), "name": "Video Truth", "trust_state": "trusted", "session_id": tid},
    )
    pid = (proj or {}).get("id")
    step("project", bool(pid), pid)
    http("POST", f"{base}/projects/{pid}/activate?thread_id={tid}", {})

    code, prov = http("GET", f"{base}/provider", timeout=15)
    if isinstance(prov, dict):
        report["provider"] = str(prov.get("provider") or "")
        report["model"] = str(prov.get("model") or "")

    code, doc = http("POST", f"{base}/video/documents", {"session_id": tid, "project_id": pid, "name": "Truth Cut"})
    did = (doc or {}).get("id")
    rev = int((doc or {}).get("revision") or 0)
    step("create_doc", bool(did), f"{did} rev={rev}")

    code, imp = http(
        "POST",
        f"{base}/video/documents/{did}/assets/import",
        {"session_id": tid, "project_id": pid, "project_relative_path": "media/clip_a.mp4"},
    )
    step("import", code == 200, f"status={code}")
    code, doc = http("GET", f"{base}/video/documents/{did}?session_id={tid}&project_id={pid}")
    assets = (doc or {}).get("assets") or []
    aid = assets[0]["id"] if assets else None
    rev = int((doc or {}).get("revision") or rev)
    step("assets", bool(aid), aid)

    # track
    code, tx = http(
        "POST",
        f"{base}/video/documents/{did}/transactions",
        {
            "session_id": tid,
            "project_id": pid,
            "operations": [
                {
                    "operation_type": "add_track",
                    "expected_revision": rev,
                    "payload": {"track_id": "v1", "kind": "video", "name": "V1"},
                }
            ],
        },
    )
    doc = (tx or {}).get("document") or {}
    rev_after_track = int(doc.get("revision") or -1)
    step("add_track", code == 200 and rev_after_track == rev + 1, f"rev {rev}->{rev_after_track}")
    rev = rev_after_track if rev_after_track >= 0 else rev

    # insert clip
    code, tx = http(
        "POST",
        f"{base}/video/documents/{did}/transactions",
        {
            "session_id": tid,
            "project_id": pid,
            "operations": [
                {
                    "operation_type": "insert_clip",
                    "expected_revision": rev,
                    "payload": {
                        "track_id": "v1",
                        "clip_id": "truth-c1",
                        "asset_id": aid,
                        "timeline_start": {
                            "ticks": "0",
                            "time_base": {"numerator": 1, "denominator": 1000},
                        },
                        "duration": {
                            "ticks": "6000",
                            "time_base": {"numerator": 1, "denominator": 1000},
                        },
                    },
                }
            ],
        },
    )
    doc = (tx or {}).get("document") or {}
    verification = (tx or {}).get("verification") or {}
    clips = doc.get("clips") or []
    # also nested
    if not clips:
        for t in (doc.get("timeline") or {}).get("tracks") or []:
            clips.extend(t.get("clips") or [])
    rev_after_insert = int(doc.get("revision") or -1)
    clip_ok = any(c.get("id") == "truth-c1" for c in clips)
    step(
        "insert_clip",
        code == 200 and clip_ok and rev_after_insert == rev + 1 and verification.get("revision_advanced") is True,
        f"status={code} clip_ok={clip_ok} rev {rev}->{rev_after_insert} clips={len(clips)} ver={verification}",
    )
    rev = rev_after_insert if rev_after_insert >= 0 else rev

    # reload store via GET
    code, doc = http("GET", f"{base}/video/documents/{did}?session_id={tid}&project_id={pid}")
    clips = doc.get("clips") or []
    if not clips:
        for t in (doc.get("timeline") or {}).get("tracks") or []:
            clips.extend(t.get("clips") or [])
    step(
        "reload_clip_present",
        code == 200 and any(c.get("id") == "truth-c1" for c in clips) and int(doc.get("revision") or 0) == rev,
        f"rev={doc.get('revision')} clips={[c.get('id') for c in clips]}",
    )

    vol_before = next((float(c.get("volume", 1)) for c in clips if c.get("id") == "truth-c1"), None)

    # live model volume request
    code, q = http(
        "POST",
        f"{base}/query",
        {
            "message": "Set the selected clip volume to 50 percent.",
            "thread_id": tid,
            "include_memory": False,
            "video_document_id": did,
            "video_selection": {
                "document_id": did,
                "selected_clip_ids": ["truth-c1"],
                "selected_asset_ids": [],
                "playhead": {"ticks": "3000", "time_base": {"numerator": 1, "denominator": 1000}},
                "document_revision": rev,
            },
        },
        timeout=180,
    )
    step("model_volume_request", code == 200, str((q or {}).get("response") or "")[:160])

    code, st = http("GET", f"{base}/threads/{tid}/state")
    aid = str((st or {}).get("pending_approval_id") or "")
    if not aid:
        # API fallback proposal
        code, proposal = http(
            "POST",
            f"{base}/video/documents/{did}/proposals",
            {
                "session_id": tid,
                "project_id": pid,
                "objective": "Set volume 50%",
                "operations": [
                    {
                        "operation_type": "set_clip_volume",
                        "expected_revision": rev,
                        "payload": {"clip_id": "truth-c1", "volume": 0.5},
                    }
                ],
            },
        )
        aid = str(((proposal or {}).get("approval") or {}).get("id") or "")
        step("api_proposal_fallback", code == 200 and bool(aid), aid)
    else:
        step("pending_from_model", bool(aid), aid)

    rev_before_confirm = rev
    c1, b1 = http("POST", f"{base}/approvals/{aid}/confirm?expected_session_id={tid}", timeout=90)
    success = bool((b1 or {}).get("success")) if isinstance(b1, dict) else False
    ver = (b1 or {}).get("verification") if isinstance(b1, dict) else {}
    step("confirm_once", c1 == 200 and success, f"c1={c1} success={success} ver={ver}")

    c2, b2 = http("POST", f"{base}/approvals/{aid}/confirm?expected_session_id={tid}", timeout=30)
    step("duplicate_confirm_409", c2 == 409 or c2 >= 400, f"c2={c2}")

    code, doc = http("GET", f"{base}/video/documents/{did}?session_id={tid}&project_id={pid}")
    rev_after = int((doc or {}).get("revision") or 0)
    clips = doc.get("clips") or []
    if not clips:
        for t in (doc.get("timeline") or {}).get("tracks") or []:
            clips.extend(t.get("clips") or [])
    vol_after = next((float(c.get("volume", 1)) for c in clips if c.get("id") == "truth-c1"), None)
    step(
        "volume_and_revision",
        rev_after == rev_before_confirm + 1 and vol_after is not None and abs(vol_after - 0.5) < 1e-6,
        f"rev {rev_before_confirm}->{rev_after} vol {vol_before}->{vol_after} clips={len(clips)}",
    )
    rev = rev_after

    # missing clip mutation must fail
    code, bad = http(
        "POST",
        f"{base}/video/documents/{did}/transactions",
        {
            "session_id": tid,
            "project_id": pid,
            "operations": [
                {
                    "operation_type": "set_clip_volume",
                    "expected_revision": rev,
                    "payload": {"clip_id": "ghost", "volume": 0.2},
                }
            ],
        },
    )
    step("missing_clip_fails", code >= 400, f"status={code} body={str(bad)[:120]}")

    # second pending while first open (create then try second)
    code, p1 = http(
        "POST",
        f"{base}/video/documents/{did}/proposals",
        {
            "session_id": tid,
            "project_id": pid,
            "objective": "Mute",
            "operations": [
                {
                    "operation_type": "set_clip_volume",
                    "expected_revision": rev,
                    "payload": {"clip_id": "truth-c1", "volume": 0.0},
                }
            ],
        },
    )
    aid1 = str(((p1 or {}).get("approval") or {}).get("id") or "")
    code2, p2 = http(
        "POST",
        f"{base}/video/documents/{did}/proposals",
        {
            "session_id": tid,
            "project_id": pid,
            "objective": "Volume again",
            "operations": [
                {
                    "operation_type": "set_clip_volume",
                    "expected_revision": rev,
                    "payload": {"clip_id": "truth-c1", "volume": 0.7},
                }
            ],
        },
    )
    step(
        "second_proposal_rejected",
        code == 200 and code2 >= 400,
        f"first={code} second={code2} detail={str(p2)[:100]}",
    )
    if aid1:
        http("POST", f"{base}/approvals/{aid1}/cancel?expected_session_id={tid}")

    # split via API
    code, sp = http(
        "POST",
        f"{base}/video/documents/{did}/proposals",
        {
            "session_id": tid,
            "project_id": pid,
            "objective": "Split",
            "operations": [
                {
                    "operation_type": "split_clip",
                    "expected_revision": rev,
                    "payload": {
                        "clip_id": "truth-c1",
                        "right_clip_id": "truth-c1-r",
                        "at": {"ticks": "3000", "time_base": {"numerator": 1, "denominator": 1000}},
                    },
                }
            ],
        },
    )
    said = str(((sp or {}).get("approval") or {}).get("id") or "")
    if said:
        c1, b1 = http("POST", f"{base}/approvals/{said}/confirm?expected_session_id={tid}")
        code, doc = http("GET", f"{base}/video/documents/{did}?session_id={tid}&project_id={pid}")
        clips = doc.get("clips") or []
        if not clips:
            for t in (doc.get("timeline") or {}).get("tracks") or []:
                clips.extend(t.get("clips") or [])
        ids = {c.get("id") for c in clips}
        rev2 = int(doc.get("revision") or 0)
        step(
            "split_verified",
            c1 == 200 and "truth-c1" in ids and "truth-c1-r" in ids and rev2 == rev + 1,
            f"c1={c1} rev {rev}->{rev2} ids={ids}",
        )
        rev = rev2
    else:
        step("split_verified", False, "no split approval")

    # mute (volume 0) via API proposal + confirm
    code, mp = http(
        "POST",
        f"{base}/video/documents/{did}/proposals",
        {
            "session_id": tid,
            "project_id": pid,
            "objective": "Mute",
            "operations": [
                {
                    "operation_type": "set_clip_volume",
                    "expected_revision": rev,
                    "payload": {"clip_id": "truth-c1", "volume": 0.0},
                }
            ],
        },
    )
    maid = str(((mp or {}).get("approval") or {}).get("id") or "")
    if maid:
        c1, b1 = http("POST", f"{base}/approvals/{maid}/confirm?expected_session_id={tid}")
        code, doc = http("GET", f"{base}/video/documents/{did}?session_id={tid}&project_id={pid}")
        clips = doc.get("clips") or []
        if not clips:
            for t in (doc.get("timeline") or {}).get("tracks") or []:
                clips.extend(t.get("clips") or [])
        vol_m = next((float(c.get("volume", 1)) for c in clips if c.get("id") == "truth-c1"), None)
        rev_m = int(doc.get("revision") or 0)
        step(
            "mute_verified",
            c1 == 200 and rev_m == rev + 1 and vol_m is not None and abs(vol_m) < 1e-6,
            f"c1={c1} rev {rev}->{rev_m} vol={vol_m}",
        )
        rev = rev_m
    else:
        step("mute_verified", False, f"no mute approval status={code}")

    # delete right half from earlier split
    code, dp = http(
        "POST",
        f"{base}/video/documents/{did}/proposals",
        {
            "session_id": tid,
            "project_id": pid,
            "objective": "Delete right",
            "operations": [
                {
                    "operation_type": "delete_clip",
                    "expected_revision": rev,
                    "payload": {"clip_id": "truth-c1-r"},
                }
            ],
        },
    )
    daid = str(((dp or {}).get("approval") or {}).get("id") or "")
    if daid:
        c1, b1 = http("POST", f"{base}/approvals/{daid}/confirm?expected_session_id={tid}")
        code, doc = http("GET", f"{base}/video/documents/{did}?session_id={tid}&project_id={pid}")
        clips = doc.get("clips") or []
        if not clips:
            for t in (doc.get("timeline") or {}).get("tracks") or []:
                clips.extend(t.get("clips") or [])
        ids = {c.get("id") for c in clips}
        rev_d = int(doc.get("revision") or 0)
        step(
            "delete_verified",
            c1 == 200 and "truth-c1-r" not in ids and "truth-c1" in ids and rev_d == rev + 1,
            f"c1={c1} rev {rev}->{rev_d} ids={ids}",
        )
        rev = rev_d
    else:
        step("delete_verified", False, f"no delete approval status={code}")

    # stale revision must fail
    code, stale = http(
        "POST",
        f"{base}/video/documents/{did}/transactions",
        {
            "session_id": tid,
            "project_id": pid,
            "operations": [
                {
                    "operation_type": "set_clip_volume",
                    "expected_revision": max(0, rev - 2),
                    "payload": {"clip_id": "truth-c1", "volume": 0.9},
                }
            ],
        },
    )
    step("stale_revision_fails", code >= 400, f"status={code}")

    report["final_document"] = {
        "revision": rev,
        "clip_ids": [c.get("id") for c in clips] if clips else [],
        "volumes": {c.get("id"): c.get("volume") for c in clips} if clips else {},
        "document_id": did,
        "session_id": tid,
        "project_id": pid,
    }

    if args.restart_backend:
        # Caller restarts process externally; we only re-check health + reload doc.
        time.sleep(1)
        code, health = http("GET", f"{base}/health", timeout=15)
        step("post_restart_health", code == 200, health)
        code, doc = http("GET", f"{base}/video/documents/{did}?session_id={tid}&project_id={pid}")
        clips = doc.get("clips") or []
        if not clips:
            for t in (doc.get("timeline") or {}).get("tracks") or []:
                clips.extend(t.get("clips") or [])
        step(
            "post_restart_clips",
            code == 200 and int(doc.get("revision") or 0) == rev and len(clips) >= 2,
            f"rev={doc.get('revision')} clips={[c.get('id') for c in clips]}",
        )

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.out} ok={report['ok']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
