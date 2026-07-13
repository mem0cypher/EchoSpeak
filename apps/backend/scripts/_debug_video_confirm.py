#!/usr/bin/env python3
import json
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
        raw = ""
        code = getattr(e, "code", 0) or 0
        if hasattr(e, "read"):
            raw = e.read().decode("utf-8", errors="replace")
            try:
                return code, json.loads(raw)
            except Exception:
                return code, raw
        return code, str(e)


def main() -> None:
    base = "http://127.0.0.1:8000"
    meta = json.loads(Path(r"C:\Users\ty0x7\Desktop\EchoSpeak\.live-acceptance\live_meta.json").read_text())
    ws = meta["workspace"]
    _, th = http("POST", f"{base}/threads", {"title": "vid-debug2"})
    tid = th.get("thread_id") or th.get("id")
    print("thread", tid)
    _, proj = http(
        "POST",
        f"{base}/projects/attach-folder",
        {"path": ws, "name": "VidDbg2", "trust_state": "trusted", "session_id": tid},
    )
    pid = proj.get("id")
    print("project", pid)
    http("POST", f"{base}/projects/{pid}/activate?thread_id={tid}", {})
    _, doc = http("POST", f"{base}/video/documents", {"session_id": tid, "project_id": pid, "name": "DbgCut2"})
    did = doc.get("id")
    rev = int(doc.get("revision") or 0)
    print("doc", did, "rev", rev)
    code, imp = http(
        "POST",
        f"{base}/video/documents/{did}/assets/import",
        {"session_id": tid, "project_id": pid, "project_relative_path": "media/clip_a.mp4"},
    )
    print("import", code)
    _, doc2 = http("GET", f"{base}/video/documents/{did}?session_id={tid}&project_id={pid}")
    assets = (doc2 or {}).get("assets") or []
    aid = assets[0]["id"] if assets else None
    rev = int((doc2 or {}).get("revision") or rev)
    print("asset", aid, "rev", rev)
    http(
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
    _, doc2 = http("GET", f"{base}/video/documents/{did}?session_id={tid}&project_id={pid}")
    rev = int((doc2 or {}).get("revision") or rev)
    code, _ = http(
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
                        "clip_id": "c1",
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
    print("insert", code)
    _, doc2 = http("GET", f"{base}/video/documents/{did}?session_id={tid}&project_id={pid}")
    rev = int((doc2 or {}).get("revision") or rev)
    print("rev_seed", rev)
    code, q = http(
        "POST",
        f"{base}/query",
        {
            "message": "Split the selected clip at the playhead.",
            "thread_id": tid,
            "include_memory": False,
            "video_document_id": did,
            "video_selection": {
                "document_id": did,
                "selected_clip_ids": ["c1"],
                "selected_asset_ids": [],
                "playhead": {"ticks": "3000", "time_base": {"numerator": 1, "denominator": 1000}},
                "document_revision": rev,
            },
        },
    )
    print("query", code, str((q or {}).get("response") or "")[:180])
    _, apps = http("GET", f"{base}/approvals?thread_id={tid}")
    items = apps if isinstance(apps, list) else (apps or {}).get("items") or []
    pending = [a for a in items if str(a.get("status")) == "pending"]
    print("pending", [(a.get("id"), a.get("tool")) for a in pending])
    _, st = http("GET", f"{base}/threads/{tid}/state")
    print("pending_approval_id", (st or {}).get("pending_approval_id"))
    if pending:
        # Prefer thread's current pending id
        cur = str((st or {}).get("pending_approval_id") or "")
        aid = cur if any(a.get("id") == cur for a in pending) else pending[-1]["id"]
        print("confirming", aid)
        c1, b1 = http("POST", f"{base}/approvals/{aid}/confirm?expected_session_id={tid}")
        print("c1", c1, str(b1)[:220])
        c2, b2 = http("POST", f"{base}/approvals/{aid}/confirm?expected_session_id={tid}")
        print("c2", c2, str(b2)[:120])
        _, doc3 = http("GET", f"{base}/video/documents/{did}?session_id={tid}&project_id={pid}")
        print("rev_after", (doc3 or {}).get("revision"))


if __name__ == "__main__":
    main()
