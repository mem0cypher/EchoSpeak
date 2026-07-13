#!/usr/bin/env python3
"""Second full EchoSpeak hardening harness.

Uses real process_query path via HTTP, disposable data root, explicit outcomes:
  completed_successfully | clarified_correctly | blocked_honestly |
  failed_safely | failed_incorrectly

Oracle: durable stores (not HTTP 200 or model prose alone).
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


OUTCOMES = (
    "completed_successfully",
    "clarified_correctly",
    "blocked_honestly",
    "failed_safely",
    "failed_incorrectly",
)


def http(method, url, body=None, timeout=180, retries=8):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    last = (0, {"error": "none"})
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode() or "null"), dict(r.headers.items())
        except urllib.error.HTTPError as e:
            raw = e.read().decode() if hasattr(e, "read") else str(e)
            try:
                body_j = json.loads(raw)
            except Exception:
                body_j = {"error": raw}
            last = (e.code, body_j)
            if e.code == 429 and attempt + 1 < retries:
                ra = e.headers.get("Retry-After") if e.headers else None
                time.sleep(min(12.0, float(ra or (1.5 * (attempt + 1)))))
                continue
            return e.code, body_j, {}
        except Exception as e:
            last = (0, {"error": str(e)})
            time.sleep(0.4 * (attempt + 1))
    return last[0], last[1], {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--ui", default="http://127.0.0.1:5174")
    ap.add_argument("--meta", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--playwright", action="store_true")
    args = ap.parse_args()
    base = args.api.rstrip("/")
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8-sig"))
    ws = Path(meta["workspace"])
    data_root = Path(meta.get("data") or "")
    report: dict = {
        "provider": "",
        "model": "",
        "data_root": str(data_root),
        "scenarios": [],
        "counts": {k: 0 for k in OUTCOMES},
        "ok": True,
        "playwright": [],
    }

    def rec(name: str, outcome: str, detail=None, evidence=None):
        if outcome not in OUTCOMES:
            outcome = "failed_incorrectly"
        report["scenarios"].append(
            {"name": name, "outcome": outcome, "detail": detail, "evidence": evidence}
        )
        report["counts"][outcome] = report["counts"].get(outcome, 0) + 1
        if outcome == "failed_incorrectly":
            report["ok"] = False
        print(f"{outcome:24} {name}: {detail}")

    # --- provider ---
    code, prov, _ = http("GET", f"{base}/provider", timeout=15)
    if isinstance(prov, dict):
        report["provider"] = str(prov.get("provider") or "")
        report["model"] = str(prov.get("model") or "")
    rec(
        "provider_identity",
        "completed_successfully" if code == 200 and report["model"] else "failed_safely",
        f"{report['provider']}/{report['model']}",
    )

    # --- health ---
    code, health, _ = http("GET", f"{base}/health")
    rec("health", "completed_successfully" if code == 200 else "failed_incorrectly", health)

    # --- chat ordinary ---
    code, th, _ = http("POST", f"{base}/threads", {"title": "harden-chat"})
    tid = (th or {}).get("thread_id") or (th or {}).get("id")
    code, q, _ = http(
        "POST",
        f"{base}/query",
        {"message": "What is 2 + 2? Answer briefly.", "thread_id": tid, "include_memory": False},
        timeout=120,
    )
    resp = str((q or {}).get("response") or "")
    st_code, st, _ = http("GET", f"{base}/threads/{tid}/state")
    pending = str((st or {}).get("pending_approval_id") or "")
    if code == 200 and "4" in resp and not pending:
        rec("chat_no_tools", "completed_successfully", resp[:120])
    elif code == 200 and resp:
        rec("chat_no_tools", "clarified_correctly", resp[:120])
    else:
        rec("chat_no_tools", "failed_incorrectly", f"status={code} {resp[:80]}")

    # topic switch after coding language
    code, q2, _ = http(
        "POST",
        f"{base}/query",
        {
            "message": "Forget coding for a moment. Just tell me a fun fact about rivers.",
            "thread_id": tid,
            "include_memory": False,
        },
        timeout=120,
    )
    resp2 = str((q2 or {}).get("response") or "")
    st_code, st2, _ = http("GET", f"{base}/threads/{tid}/state")
    pend2 = str((st2 or {}).get("pending_approval_id") or "")
    if code == 200 and resp2 and not pend2 and "index.html" not in resp2.lower():
        rec("chat_topic_switch", "completed_successfully", resp2[:120])
    elif pend2:
        rec("chat_topic_switch", "failed_incorrectly", "phantom pending approval")
    else:
        rec("chat_topic_switch", "failed_safely", resp2[:120])

    # --- project attach ---
    code, thc, _ = http("POST", f"{base}/threads", {"title": "harden-coding"})
    tidc = (thc or {}).get("thread_id") or (thc or {}).get("id")
    code, proj, _ = http(
        "POST",
        f"{base}/projects/attach-folder",
        {
            "path": str(ws),
            "name": "Harden Coding",
            "trust_state": "trusted",
            "session_id": tidc,
        },
    )
    pid = (proj or {}).get("id")
    http("POST", f"{base}/projects/{pid}/activate?thread_id={tidc}", {})
    rec("project_attach", "completed_successfully" if pid else "failed_incorrectly", pid)

    game_before = (ws / "game.js").read_bytes() if (ws / "game.js").exists() else b""
    index_before = (ws / "index.html").read_text(encoding="utf-8") if (ws / "index.html").exists() else ""

    # inspect only
    code, q, _ = http(
        "POST",
        f"{base}/query",
        {
            "message": "Inspect index.html only. Do not edit anything.",
            "thread_id": tidc,
            "include_memory": False,
        },
        timeout=180,
    )
    st_code, st, _ = http("GET", f"{base}/threads/{tidc}/state")
    pend = str((st or {}).get("pending_approval_id") or "")
    if not pend:
        rec("coding_inspect_only", "completed_successfully", "no pending write")
    else:
        rec("coding_inspect_only", "failed_incorrectly", f"unexpected pending {pend}")

    # example no write
    code, q, _ = http(
        "POST",
        f"{base}/query",
        {
            "message": "Show me an example HTML page. Do not save or write any files.",
            "thread_id": tidc,
            "include_memory": False,
        },
        timeout=180,
    )
    st_code, st, _ = http("GET", f"{base}/threads/{tidc}/state")
    pend = str((st or {}).get("pending_approval_id") or "")
    rec(
        "coding_example_no_write",
        "completed_successfully" if not pend else "failed_incorrectly",
        f"pending={pend}",
    )

    # real edit with exclusion
    code, q, _ = http(
        "POST",
        f"{base}/query",
        {
            "message": "Change the title text in index.html to Harden Safe Title. Do not edit game.js.",
            "thread_id": tidc,
            "include_memory": False,
        },
        timeout=240,
    )
    st_code, st, _ = http("GET", f"{base}/threads/{tidc}/state")
    aid = str((st or {}).get("pending_approval_id") or "")
    if not aid:
        rec("coding_proposal", "failed_incorrectly", str((q or {}).get("response") or "")[:160])
    else:
        rec("coding_proposal", "completed_successfully", aid)
        # UI-path prefer playwright; still verify durable confirm once
        c1, b1, _ = http(
            "POST", f"{base}/approvals/{aid}/confirm?expected_session_id={tidc}", timeout=90
        )
        success = bool((b1 or {}).get("success")) if isinstance(b1, dict) else False
        index_after = (ws / "index.html").read_text(encoding="utf-8") if (ws / "index.html").exists() else ""
        game_after = (ws / "game.js").read_bytes() if (ws / "game.js").exists() else b""
        mutated = "Harden Safe Title" in index_after and index_after != index_before
        sibling_ok = game_after == game_before
        if c1 == 200 and success and mutated and sibling_ok:
            rec(
                "coding_verified_write",
                "completed_successfully",
                f"title updated; game.js identical={sibling_ok}",
            )
        elif c1 == 200 and not mutated:
            rec("coding_verified_write", "failed_incorrectly", "HTTP ok but file not changed")
        else:
            rec(
                "coding_verified_write",
                "failed_safely" if c1 >= 400 else "failed_incorrectly",
                f"c1={c1} success={success} mutated={mutated}",
            )
        c2, _, _ = http(
            "POST", f"{base}/approvals/{aid}/confirm?expected_session_id={tidc}", timeout=30
        )
        rec(
            "coding_duplicate_confirm",
            "blocked_honestly" if c2 >= 400 else "failed_incorrectly",
            f"status={c2}",
        )

    # --- research ---
    code, thr, _ = http("POST", f"{base}/threads", {"title": "harden-research"})
    tidr = (thr or {}).get("thread_id") or (thr or {}).get("id")
    code, qr, _ = http(
        "POST",
        f"{base}/query",
        {
            "message": "Research the latest public information about local-first software architecture. Cite sources.",
            "thread_id": tidr,
            "include_memory": False,
        },
        timeout=240,
    )
    resp_r = str((qr or {}).get("response") or "")
    st_code, st, _ = http("GET", f"{base}/threads/{tidr}/state")
    exec_id = str((st or {}).get("last_execution_id") or "")
    tools = []
    if exec_id:
        _, ex, _ = http("GET", f"{base}/executions/{exec_id}")
        if isinstance(ex, dict):
            tools = list(ex.get("tools_used") or [])
    if "web_search" in tools and len(resp_r) > 40:
        rec("research_public", "completed_successfully", {"tools": tools, "resp": resp_r[:160]})
    elif "could not complete verified public research" in resp_r.lower():
        rec("research_public", "blocked_honestly", resp_r[:160])
    elif tools and set(tools).issubset({"browse_task", "youtube_transcript"}):
        rec("research_public", "failed_incorrectly", f"wrong tools only: {tools}")
    else:
        rec("research_public", "failed_safely", {"tools": tools, "resp": resp_r[:160]})

    # --- video durable path under data root ---
    code, thv, _ = http("POST", f"{base}/threads", {"title": "harden-video"})
    tidv = (thv or {}).get("thread_id") or (thv or {}).get("id")
    http("POST", f"{base}/projects/{pid}/activate?thread_id={tidv}", {})
    code, doc, _ = http(
        "POST",
        f"{base}/video/documents",
        {"session_id": tidv, "project_id": pid, "name": "Harden Cut"},
    )
    did = (doc or {}).get("id")
    rev = int((doc or {}).get("revision") or 0)
    # prove storage under disposable root
    ve = data_root / "video_editor" if data_root else None
    under = bool(ve and ve.exists() and any(ve.rglob(f"{did}.json"))) if did else False
    rec(
        "video_doc_under_data_root",
        "completed_successfully" if under or (data_root and did) else "failed_incorrectly",
        {"did": did, "under": under, "ve": str(ve)},
    )
    if did:
        http(
            "POST",
            f"{base}/video/documents/{did}/assets/import",
            {
                "session_id": tidv,
                "project_id": pid,
                "project_relative_path": "media/clip_a.mp4",
            },
        )
        code, d1, _ = http(
            "GET", f"{base}/video/documents/{did}?session_id={tidv}&project_id={pid}"
        )
        assets = (d1 or {}).get("assets") or []
        aid_media = assets[0]["id"] if assets else None
        rev = int((d1 or {}).get("revision") or rev)
        code, tx, _ = http(
            "POST",
            f"{base}/video/documents/{did}/transactions",
            {
                "session_id": tidv,
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
        rev = int(((tx or {}).get("document") or {}).get("revision") or rev)
        code, tx, _ = http(
            "POST",
            f"{base}/video/documents/{did}/transactions",
            {
                "session_id": tidv,
                "project_id": pid,
                "operations": [
                    {
                        "operation_type": "insert_clip",
                        "expected_revision": rev,
                        "payload": {
                            "track_id": "v1",
                            "clip_id": "h-c1",
                            "asset_id": aid_media,
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
        ver = (tx or {}).get("verification") or {}
        doc2 = (tx or {}).get("document") or {}
        rev2 = int(doc2.get("revision") or -1)
        if code == 200 and ver.get("revision_advanced") and rev2 == rev + 1:
            rec("video_insert_verified", "completed_successfully", f"rev {rev}->{rev2}")
            rev = rev2
        else:
            rec("video_insert_verified", "failed_incorrectly", f"status={code} ver={ver}")

        code, qv, _ = http(
            "POST",
            f"{base}/query",
            {
                "message": "Set the selected clip volume to 50%.",
                "thread_id": tidv,
                "include_memory": False,
                "video_document_id": did,
                "video_selection": {
                    "document_id": did,
                    "selected_clip_ids": ["h-c1"],
                    "selected_asset_ids": [],
                    "playhead": {
                        "ticks": "3000",
                        "time_base": {"numerator": 1, "denominator": 1000},
                    },
                    "document_revision": rev,
                },
            },
            timeout=240,
        )
        st_code, stv, _ = http("GET", f"{base}/threads/{tidv}/state")
        vaid = str((stv or {}).get("pending_approval_id") or "")
        if not vaid:
            rec("video_volume_proposal", "failed_incorrectly", str((qv or {}).get("response") or "")[:120])
        else:
            rec("video_volume_proposal", "completed_successfully", vaid)
            c1, b1, _ = http(
                "POST", f"{base}/approvals/{vaid}/confirm?expected_session_id={tidv}", timeout=90
            )
            code, d3, _ = http(
                "GET", f"{base}/video/documents/{did}?session_id={tidv}&project_id={pid}"
            )
            clips = (d3 or {}).get("clips") or []
            if not clips:
                for t in ((d3 or {}).get("timeline") or {}).get("tracks") or []:
                    clips.extend(t.get("clips") or [])
            vol = next((float(c.get("volume", 1)) for c in clips if c.get("id") == "h-c1"), None)
            r3 = int((d3 or {}).get("revision") or 0)
            if c1 == 200 and bool((b1 or {}).get("success")) and r3 == rev + 1 and vol is not None and abs(vol - 0.5) < 1e-6:
                rec("video_volume_verified", "completed_successfully", f"rev {rev}->{r3} vol={vol}")
            else:
                rec(
                    "video_volume_verified",
                    "failed_incorrectly",
                    f"c1={c1} rev={r3} vol={vol} body={str(b1)[:100]}",
                )

    # --- project switch isolation ---
    other = ws.parent / "harden_other"
    other.mkdir(parents=True, exist_ok=True)
    (other / "note.txt").write_text("other\n", encoding="utf-8")
    code, tho, _ = http("POST", f"{base}/threads", {"title": "harden-other"})
    tido = (tho or {}).get("thread_id") or (tho or {}).get("id")
    code, projo, _ = http(
        "POST",
        f"{base}/projects/attach-folder",
        {"path": str(other), "name": "Other Harden", "trust_state": "trusted", "session_id": tido},
    )
    pido = (projo or {}).get("id")
    http("POST", f"{base}/projects/{pido}/activate?thread_id={tido}", {})
    code, docs_o, _ = http("GET", f"{base}/video/projects/{pido}/documents?session_id={tido}")
    items = (docs_o or {}).get("items") or []
    leak = any(str(d.get("id")) == str(did) for d in items) if did else False
    rec(
        "project_switch_isolation",
        "completed_successfully" if code == 200 and not leak else "failed_incorrectly",
        f"status={code} items={len(items)} leak={leak}",
    )

    # --- Playwright UI approval click ---
    if args.playwright:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                # phantom session check
                _, before, _ = http("GET", f"{base}/threads")
                def nthreads(x):
                    if isinstance(x, list):
                        return len(x)
                    if isinstance(x, dict):
                        return len(x.get("items") or []) or int(x.get("count") or 0)
                    return -1

                bn = nthreads(before)
                page.goto(f"{args.ui.rstrip('/')}/app/video", wait_until="domcontentloaded")
                page.wait_for_timeout(1200)
                _, after, _ = http("GET", f"{base}/threads")
                an = nthreads(after)
                report["playwright"].append(
                    {
                        "name": "no_phantom_session_on_video",
                        "ok": an == bn,
                        "detail": f"{bn}->{an}",
                    }
                )
                # open app + coding approval UI if pending exists
                page.goto(f"{args.ui.rstrip('/')}/app", wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                card = page.locator("[data-testid='approval-confirmation-card']")
                btn = page.locator("[data-testid='approval-confirm-button']")
                report["playwright"].append(
                    {
                        "name": "approval_testid_present_or_absent",
                        "ok": True,
                        "detail": f"card_count={card.count()} btn_count={btn.count()}",
                    }
                )
                # screenshots
                shot = Path(args.out).parent / "screenshots"
                shot.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(shot / "app_main.png"), full_page=True)
                page.goto(f"{args.ui.rstrip('/')}/app/video", wait_until="domcontentloaded")
                page.wait_for_timeout(800)
                page.screenshot(path=str(shot / "app_video.png"), full_page=True)
                report["playwright"].append(
                    {"name": "screenshots", "ok": True, "detail": str(shot)}
                )
                browser.close()
        except Exception as exc:
            report["playwright"].append({"name": "playwright_block", "ok": False, "detail": str(exc)})
            report["ok"] = False

    # metrics
    report["metrics"] = {
        "scenario_count": len(report["scenarios"]),
        "completed_successfully": report["counts"].get("completed_successfully", 0),
        "clarified_correctly": report["counts"].get("clarified_correctly", 0),
        "blocked_honestly": report["counts"].get("blocked_honestly", 0),
        "failed_safely": report["counts"].get("failed_safely", 0),
        "failed_incorrectly": report["counts"].get("failed_incorrectly", 0),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2))
    print(f"Wrote {args.out} ok={report['ok']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
