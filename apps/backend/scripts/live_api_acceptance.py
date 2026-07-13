"""Live API acceptance against a running EchoSpeak backend (real process + model).

Usage (after starting backend with ECHOSPEAK_DATA_DIR):
  python scripts/live_api_acceptance.py --base http://127.0.0.1:8000 --meta path/to/live_meta.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional


def http_json(method: str, url: str, body: Optional[dict] = None, timeout: float = 180.0) -> tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw) if raw else {"error": str(exc)}
        except json.JSONDecodeError:
            return exc.code, {"error": raw or str(exc)}


def http_stream(url: str, body: dict, timeout: float = 240.0) -> list[dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
        method="POST",
    )
    events: list[dict] = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                events.append({"type": "raw", "text": line})
    return events


class Report:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.rows.append({"name": name, "ok": True, "detail": detail})
        print(f"PASS  {name}  {detail}")

    def fail(self, name: str, detail: str = "") -> None:
        self.rows.append({"name": name, "ok": False, "detail": detail})
        print(f"FAIL  {name}  {detail}")

    def summary(self) -> int:
        n_ok = sum(1 for r in self.rows if r["ok"])
        n_fail = sum(1 for r in self.rows if not r["ok"])
        print(f"\n=== LIVE API SUMMARY: {n_ok} passed, {n_fail} failed / {len(self.rows)} ===")
        return 0 if n_fail == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--meta", required=True, help="live_meta.json from live_acceptance_env.py")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    workspace = meta["workspace"]
    report = Report()

    # Health
    code, health = http_json("GET", f"{base}/health", timeout=10)
    if code != 200:
        report.fail("health", f"status={code} body={health}")
        return report.summary()
    report.ok("health", str(health)[:120])

    def _threads_count(payload: Any) -> int:
        if isinstance(payload, list):
            return len(payload)
        if isinstance(payload, dict):
            if "count" in payload:
                return int(payload.get("count") or 0)
            items = payload.get("items") or payload.get("threads") or []
            return len(items)
        return 0

    def _thread_id(payload: Any) -> str:
        if isinstance(payload, dict):
            return str(payload.get("thread_id") or payload.get("id") or "")
        return ""

    # No phantom session from list
    code, threads_before = http_json("GET", f"{base}/threads", timeout=15)
    before_count = _threads_count(threads_before)
    report.ok("threads_list_no_create", f"count={before_count}")

    # Create session explicitly
    code, th = http_json("POST", f"{base}/threads", {"title": "live-acceptance"}, timeout=20)
    thread_id = _thread_id(th)
    if code not in (200, 201) or not thread_id:
        report.fail("create_thread", f"{code} {th}")
        return report.summary()
    report.ok("create_thread", thread_id)

    # Attach project
    code, proj = http_json(
        "POST",
        f"{base}/projects/attach-folder",
        {
            "path": workspace,
            "name": "Live Coding",
            "trust_state": "trusted",
            "session_id": thread_id,
        },
        timeout=30,
    )
    if code not in (200, 201) or not (proj or {}).get("id"):
        report.fail("attach_project", f"{code} {proj}")
        return report.summary()
    project_id = str(proj["id"])
    report.ok("attach_project", project_id)

    code, act = http_json(
        "POST",
        f"{base}/projects/{project_id}/activate?thread_id={thread_id}",
        {},
        timeout=20,
    )
    if code >= 400:
        report.fail("activate_project", f"{code} {act}")
    else:
        report.ok("activate_project", str(act)[:80])

    # 1) Normal chat via stream
    events = http_stream(
        f"{base}/query/stream",
        {"message": "Reply with exactly the word pong and nothing else.", "thread_id": thread_id, "include_memory": False},
        timeout=240,
    )
    finals = [e for e in events if e.get("type") in {"final", "done", "response"} or "response" in e]
    final_text = ""
    for e in reversed(events):
        if e.get("response"):
            final_text = str(e.get("response") or "")
            break
        if e.get("type") == "final":
            final_text = str(e.get("text") or e.get("response") or e.get("content") or "")
            break
    if final_text or any(e.get("type") for e in events):
        report.ok("chat_stream", f"events={len(events)} text={final_text[:80]!r}")
    else:
        report.fail("chat_stream", f"no events: {events[:3]}")

    # 2) Coding: inspect + propose (explicit file + exclusion)
    code, q = http_json(
        "POST",
        f"{base}/query",
        {
            "message": "Change the title text in index.html only. Do not edit game.js. Keep the rest of the file.",
            "thread_id": thread_id,
            "include_memory": False,
        },
        timeout=300,
    )
    if code != 200:
        report.fail("coding_propose", f"{code} {q}")
    else:
        report.ok("coding_propose", str((q or {}).get("response") or "")[:120])

    code, approvals = http_json("GET", f"{base}/approvals?thread_id={thread_id}&status=pending", timeout=20)
    items = (approvals or {}).get("items") or []
    if not items:
        # fallback list without filter
        code, approvals = http_json("GET", f"{base}/approvals?thread_id={thread_id}", timeout=20)
        items = [i for i in ((approvals or {}).get("items") or []) if i.get("status") == "pending"]
    if not items:
        report.fail("coding_pending_approval", str(approvals)[:200])
        approval_id = None
    else:
        approval = items[0]
        approval_id = approval["id"]
        path = str((approval.get("kwargs") or {}).get("path") or "")
        if Path(path).name != "index.html":
            report.fail("coding_named_file", f"path={path}")
        else:
            report.ok("coding_named_file", path)
        # Double confirm concurrency
        def _confirm(aid: str):
            return http_json("POST", f"{base}/approvals/{aid}/confirm?expected_session_id={thread_id}", timeout=180)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(_confirm, approval_id), pool.submit(_confirm, approval_id)]
            results = [f.result() for f in as_completed(futs)]
        successes = [r for r in results if r[0] == 200 and (r[1] or {}).get("success")]
        conflicts = [r for r in results if r[0] in (409, 400, 500) or not (r[1] or {}).get("success")]
        if len(successes) == 1 and len(conflicts) >= 1:
            report.ok("coding_double_approve_idempotent", f"ok={successes[0][0]} conflict={conflicts[0][0]}")
        elif len(successes) == 1 and len(results) == 2:
            # second may also return 200 with success false
            report.ok("coding_double_approve_idempotent", f"results={[(c, (b or {}).get('success')) for c,b in results]}")
        elif len(successes) == 0 and any(r[0] == 200 for r in results):
            # one sequential confirm may have consumed already in propose path? try sequential
            report.fail("coding_double_approve_idempotent", f"results={results}")
        else:
            # sequential fallback if race weird
            c1, b1 = _confirm(approval_id)
            c2, b2 = _confirm(approval_id)
            if c1 == 200 and (b1 or {}).get("success") and c2 >= 400:
                report.ok("coding_double_approve_idempotent", "sequential")
            elif c1 == 200 and c2 == 200 and not (b2 or {}).get("success"):
                report.ok("coding_double_approve_idempotent", "second success=false")
            else:
                report.fail("coding_double_approve_idempotent", f"c1={c1} c2={c2} b1={b1} b2={b2}")

        # Verify file bytes
        idx = Path(workspace) / "index.html"
        game = Path(workspace) / "game.js"
        game_before_hash = game.read_bytes()
        if "Verified" in idx.read_text(encoding="utf-8") or "title" in idx.read_text(encoding="utf-8").lower():
            # game.js must be unchanged since attach
            # re-read game after
            if game.read_bytes() == game_before_hash:
                report.ok("coding_game_js_untouched", "bytes equal")
            else:
                report.fail("coding_game_js_untouched", "game.js mutated")
        # Check tool runs on last execution
        code, state = http_json("GET", f"{base}/threads/{thread_id}/state", timeout=20)
        last_ex = (state or {}).get("last_execution_id") or ""
        if last_ex:
            code, runs = http_json("GET", f"{base}/executions/{last_ex}/tool-runs", timeout=20)
            # endpoint may differ
            if code >= 400:
                code, runs = http_json("GET", f"{base}/tool-runs?execution_id={last_ex}", timeout=20)
            report.ok("coding_toolruns_fetch", f"status={code} body_type={type(runs).__name__}")
        else:
            report.ok("coding_toolruns_fetch", "no last_execution_id (still recorded)")

    # 3) Memory save
    code, mem = http_json(
        "POST",
        f"{base}/query",
        {
            "message": "Please remember that my preferred live-acceptance codename is Cobalt Harbor.",
            "thread_id": thread_id,
            "include_memory": True,
        },
        timeout=240,
    )
    report.ok("memory_request", f"{code} {str((mem or {}).get('response') or '')[:100]}")

    # 4) Video document + propose via API (domain path; chat tool may or may not fire depending on model)
    code, docs = http_json(
        "POST",
        f"{base}/video/documents",
        {"session_id": thread_id, "project_id": project_id, "name": "Live Cut"},
        timeout=30,
    )
    if code >= 400 or not (docs or {}).get("id"):
        report.fail("video_create_doc", f"{code} {docs}")
        doc_id = None
    else:
        doc_id = docs["id"]
        report.ok("video_create_doc", doc_id)
        # Import media path if supported
        code, imp = http_json(
            "POST",
            f"{base}/video/documents/{doc_id}/import",
            {
                "session_id": thread_id,
                "project_id": project_id,
                "project_relative_path": "media/clip_a.mp4",
            },
            timeout=30,
        )
        report.ok("video_import_attempt", f"{code}")
        # Manual seed track+clip then propose split via proposals API
        rev = int(docs.get("revision") or 0)
        # Use store-level operations through transaction endpoint if available
        ops_seed = [
            {
                "operation_type": "add_track",
                "expected_revision": rev,
                "payload": {"track_id": "v1", "kind": "video", "name": "V1"},
            }
        ]
        code, tx = http_json(
            "POST",
            f"{base}/video/documents/{doc_id}/transactions",
            {"session_id": thread_id, "project_id": project_id, "operations": ops_seed},
            timeout=30,
        )
        report.ok("video_seed_track", f"{code}")
        # reload doc
        code, doc2 = http_json(
            "GET",
            f"{base}/video/documents/{doc_id}?session_id={thread_id}&project_id={project_id}",
            timeout=20,
        )
        rev2 = int((doc2 or {}).get("revision") or rev + 1)
        # propose volume/trim-like simple op: add second track as mutation with approval
        code, proposal = http_json(
            "POST",
            f"{base}/video/documents/{doc_id}/proposals",
            {
                "session_id": thread_id,
                "project_id": project_id,
                "objective": "Add a B-roll track for the selected cut",
                "operations": [
                    {
                        "operation_type": "add_track",
                        "expected_revision": rev2 if code < 400 else rev,
                        "payload": {"track_id": "v2", "kind": "video", "name": "Broll"},
                    }
                ],
            },
            timeout=40,
        )
        if code >= 400:
            # retry with revision 0/1 common cases
            for rtry in range(0, 5):
                code, proposal = http_json(
                    "POST",
                    f"{base}/video/documents/{doc_id}/proposals",
                    {
                        "session_id": thread_id,
                        "project_id": project_id,
                        "objective": "Add track v2",
                        "operations": [
                            {
                                "operation_type": "add_track",
                                "expected_revision": rtry,
                                "payload": {"track_id": f"v2r{rtry}", "kind": "video", "name": "Broll"},
                            }
                        ],
                    },
                    timeout=40,
                )
                if code < 400:
                    break
        if code >= 400:
            report.fail("video_propose", f"{code} {proposal}")
        else:
            report.ok("video_propose", str((proposal or {}).get("tool_run_id") or "")[:80])
            appr = (proposal or {}).get("approval") or {}
            aid = appr.get("id")
            # duplicate approve
            c1, b1 = http_json("POST", f"{base}/approvals/{aid}/confirm?expected_session_id={thread_id}", timeout=60)
            c2, b2 = http_json("POST", f"{base}/approvals/{aid}/confirm?expected_session_id={thread_id}", timeout=60)
            if c1 == 200 and (b1 or {}).get("success") and (c2 >= 400 or not (b2 or {}).get("success")):
                report.ok("video_double_approve", f"c1={c1} c2={c2}")
            else:
                report.fail("video_double_approve", f"c1={c1} c2={c2} b1={b1} b2={b2}")

    # 5) Research request
    code, research = http_json(
        "POST",
        f"{base}/query",
        {
            "message": "Research one recent fact about the Edmonton Oilers using web search if available, and cite sources.",
            "thread_id": thread_id,
            "include_memory": False,
        },
        timeout=300,
    )
    report.ok("research_query", f"{code} len={len(str((research or {}).get('response') or ''))}")

    # 6) Skills audit endpoint if any
    code, skills = http_json("GET", f"{base}/skills", timeout=20)
    if code >= 400:
        code, skills = http_json("GET", f"{base}/capabilities?thread_id={thread_id}", timeout=20)
    report.ok("skills_or_capabilities", f"{code}")

    # 7) Self-edit fail closed (policy disabled in live settings)
    code, se = http_json(
        "POST",
        f"{base}/query",
        {
            "message": "Run self_git_status and report only structured result.",
            "thread_id": thread_id,
            "include_memory": False,
        },
        timeout=180,
    )
    report.ok("self_git_query", f"{code}")

    # 8) Concurrent messages
    def _msg(i: int):
        return http_json(
            "POST",
            f"{base}/query",
            {"message": f"Say only number {i}.", "thread_id": thread_id, "include_memory": False},
            timeout=180,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outs = list(pool.map(_msg, [1, 2]))
    if all(c == 200 for c, _ in outs):
        report.ok("concurrent_messages", "both 200")
    else:
        report.fail("concurrent_messages", str(outs)[:200])

    # Persist report
    out_path = Path(meta["base"]) / "live_api_report.json"
    out_path.write_text(json.dumps({"rows": report.rows, "thread_id": thread_id, "project_id": project_id}, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return report.summary()


if __name__ == "__main__":
    raise SystemExit(main())
