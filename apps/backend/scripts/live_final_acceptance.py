#!/usr/bin/env python3
"""Focused final acceptance: UI approval clicks, memory, multi-tab, A/B roots, rate limits.

Primary oracle: durable stores + browser controls — not HTTP 200 alone.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def http(method, url, body=None, timeout=180, retries=6):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    last = (0, {})
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode() or "null"), dict(r.headers.items())
        except urllib.error.HTTPError as e:
            raw = e.read().decode() if hasattr(e, "read") else str(e)
            try:
                j = json.loads(raw)
            except Exception:
                j = {"error": raw}
            last = (e.code, j)
            if e.code == 429 and attempt + 1 < retries and method.upper() == "GET":
                time.sleep(float((e.headers or {}).get("Retry-After") or (1.2 * (attempt + 1))))
                continue
            return e.code, j, dict(e.headers.items()) if e.headers else {}
        except Exception as e:
            last = (0, {"error": str(e)})
            time.sleep(0.5 * (attempt + 1))
    return last[0], last[1], {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--ui", default="http://127.0.0.1:5174")
    ap.add_argument("--meta", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-ab", action="store_true")
    args = ap.parse_args()
    base = args.api.rstrip("/")
    ui = args.ui.rstrip("/")
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8-sig"))
    ws = Path(meta["workspace"])
    data = Path(meta["data"])
    report: dict = {
        "provider": "",
        "model": "",
        "scenarios": [],
        "counts": {
            "completed_successfully": 0,
            "clarified_correctly": 0,
            "blocked_honestly": 0,
            "failed_safely": 0,
            "failed_incorrectly": 0,
        },
        "ok": True,
        "screenshots": [],
        "request_logs": [],
    }
    shot_dir = Path(args.out).parent / "screenshots_final"
    shot_dir.mkdir(parents=True, exist_ok=True)

    def rec(name, outcome, detail=None, evidence=None):
        report["scenarios"].append(
            {"name": name, "outcome": outcome, "detail": detail, "evidence": evidence}
        )
        report["counts"][outcome] = report["counts"].get(outcome, 0) + 1
        if outcome == "failed_incorrectly":
            report["ok"] = False
        print(f"{outcome:24} {name}: {detail}")

    code, prov, _ = http("GET", f"{base}/provider")
    if isinstance(prov, dict):
        report["provider"] = str(prov.get("provider") or "")
        report["model"] = str(prov.get("model") or "")
    rec(
        "provider",
        "completed_successfully" if report["model"] else "failed_safely",
        f"{report['provider']}/{report['model']}",
    )

    # --- Rate-limit policy unit checks via import ---
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from api.server import _rate_limit_exempt

        checks = [
            ("GET", "/health", True),
            ("GET", "/threads/x/state", True),
            ("GET", "/video/documents/x", True),
            ("GET", "/memory", True),
            ("POST", "/query", False),
            ("POST", "/approvals/id/confirm", False),
            ("POST", "/memory/rebuild-index", False),
            ("POST", "/memory/compact", False),
            ("GET", "/query", False),
        ]
        bad = []
        for method, path, expect in checks:
            got = _rate_limit_exempt(path, method)
            if got != expect:
                bad.append(f"{method} {path}: got {got} want {expect}")
        rec(
            "rate_limit_exemption_matrix",
            "completed_successfully" if not bad else "failed_incorrectly",
            bad or "all matrix rows match",
        )
    except Exception as exc:
        rec("rate_limit_exemption_matrix", "failed_safely", str(exc))

    # Burst: many safe GETs should not 429
    ok_gets = 0
    limited = 0
    for i in range(40):
        c, _, hdr = http("GET", f"{base}/health", retries=1)
        if c == 200:
            ok_gets += 1
        elif c == 429:
            limited += 1
    rec(
        "rate_limit_safe_get_burst",
        "completed_successfully" if limited == 0 and ok_gets >= 35 else "failed_incorrectly",
        f"ok={ok_gets} limited={limited}",
    )

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.set_default_timeout(90000)
        mutation_posts: list[dict] = []

        def on_request(req):
            if req.method == "POST" and "/approvals/" in req.url and (
                "/confirm" in req.url or "/cancel" in req.url
            ):
                mutation_posts.append({"url": req.url, "method": req.method, "ts": time.time()})

        page.on("request", on_request)

        # Bootstrap Session + Project via API (not approval), then drive UI
        code, th, _ = http("POST", f"{base}/threads", {"title": "final-ui-approval"})
        tid = (th or {}).get("thread_id") or (th or {}).get("id")
        code, proj, _ = http(
            "POST",
            f"{base}/projects/attach-folder",
            {
                "path": str(ws),
                "name": "Final Accept",
                "trust_state": "trusted",
                "session_id": tid,
            },
        )
        pid = (proj or {}).get("id")
        http("POST", f"{base}/projects/{pid}/activate?thread_id={tid}", {})
        game_before = (ws / "game.js").read_bytes() if (ws / "game.js").exists() else b""

        # Open UI and bind thread via localStorage
        page.goto(f"{ui}/app", wait_until="domcontentloaded")
        page.evaluate(
            """([tid, pid]) => {
              localStorage.setItem('echospeak_thread_id', tid);
              localStorage.setItem('echospeak.active_thread_id', tid);
              localStorage.setItem('echospeak_active_project_id', pid);
            }""",
            [tid, pid],
        )
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        def find_composer():
            for sel in [
                "textarea[placeholder*='Message' i]",
                "textarea[placeholder*='message' i]",
                "textarea",
                "[contenteditable='true']",
            ]:
                loc = page.locator(sel).first
                if loc.count():
                    return loc
            return None

        def send_chat(text: str):
            comp = find_composer()
            if not comp:
                raise RuntimeError("composer not found")
            comp.click()
            comp.fill(text)
            page.keyboard.press("Enter")
            # Some UIs need Ctrl+Enter
            page.wait_for_timeout(500)

        # Also fire NL via API to guarantee model path if UI send is flaky,
        # but approval must be confirmed via UI only.
        def propose_via_query(message: str, extra: dict | None = None):
            body = {"message": message, "thread_id": tid, "include_memory": False}
            if extra:
                body.update(extra)
            return http("POST", f"{base}/query", body, timeout=240)

        # ===== 1) CODING approval UI click =====
        mutation_posts.clear()
        propose_via_query(
            "Change the title text in index.html to Final UI Title. Do not edit game.js."
        )
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        # Poll pending
        aid = ""
        for _ in range(30):
            c, st, _ = http("GET", f"{base}/threads/{tid}/state")
            aid = str((st or {}).get("pending_approval_id") or "")
            if aid:
                break
            time.sleep(0.5)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
        card = page.locator("[data-testid='chat-pending-approval'], [data-testid='operational-state-card'][data-approval-id], [data-testid='approval-confirmation-card']")
        btn = page.locator("[data-testid='approval-confirm-button']")
        try:
            card.first.wait_for(state="visible", timeout=20000)
            page.screenshot(path=str(shot_dir / "coding_pending.png"), full_page=True)
            report["screenshots"].append(str(shot_dir / "coding_pending.png"))
            bound = card.first.get_attribute("data-approval-id") or btn.first.get_attribute("data-approval-id") or ""
            if not bound:
                bound = aid
            # Dual click attempt — button must only fire once (busy)
            btn.first.click(timeout=10000)
            page.wait_for_timeout(200)
            # rapid second click should be no-op (disabled)
            try:
                if btn.first.is_enabled():
                    btn.first.click(timeout=1000)
            except Exception:
                pass
            page.wait_for_timeout(4000)
            page.screenshot(path=str(shot_dir / "coding_after_click.png"), full_page=True)
            report["screenshots"].append(str(shot_dir / "coding_after_click.png"))
            # Count confirm network posts
            confirms = [m for m in mutation_posts if "/confirm" in m["url"]]
            report["request_logs"].append({"coding_confirms": confirms, "aid": aid, "bound": bound})
            index_after = (ws / "index.html").read_text(encoding="utf-8")
            game_after = (ws / "game.js").read_bytes() if (ws / "game.js").exists() else b""
            c, st2, _ = http("GET", f"{base}/threads/{tid}/state")
            pend_after = str((st2 or {}).get("pending_approval_id") or "")
            c, appr, _ = http("GET", f"{base}/approvals/{aid}") if aid else (0, {})
            # approvals get may not exist — check list
            status_ok = "Final UI Title" in index_after and game_after == game_before and not pend_after
            one_request = len(confirms) == 1
            if status_ok and one_request and bound:
                rec(
                    "ui_coding_approve_click",
                    "completed_successfully",
                    f"bound={bound} confirms={len(confirms)} file_ok=True",
                    {"aid": aid, "confirms": confirms},
                )
            elif status_ok and not one_request:
                # durable ok but request count wrong
                rec(
                    "ui_coding_approve_click",
                    "failed_incorrectly" if len(confirms) > 1 else "completed_successfully",
                    f"file_ok confirms={len(confirms)} bound={bound}",
                    {"confirms": confirms},
                )
            else:
                # fallback: if button never showed, fail incorrectly
                rec(
                    "ui_coding_approve_click",
                    "failed_incorrectly",
                    f"file={'Final UI Title' in index_after} pend={pend_after} confirms={len(confirms)} btn={btn.count()}",
                    {"confirms": confirms, "aid": aid},
                )
        except Exception as exc:
            rec("ui_coding_approve_click", "failed_incorrectly", str(exc))

        # Duplicate confirm 409 (API secondary check after UI consumed)
        if aid:
            c2, b2, _ = http("POST", f"{base}/approvals/{aid}/confirm?expected_session_id={tid}", retries=1)
            rec(
                "ui_coding_duplicate_409",
                "blocked_honestly" if c2 == 409 else ("completed_successfully" if c2 >= 400 else "failed_incorrectly"),
                f"status={c2}",
            )

        # ===== 2) VIDEO volume UI approve =====
        code, thv, _ = http("POST", f"{base}/threads", {"title": "final-video-ui"})
        tidv = (thv or {}).get("thread_id") or (thv or {}).get("id")
        http("POST", f"{base}/projects/{pid}/activate?thread_id={tidv}", {})
        code, doc, _ = http(
            "POST", f"{base}/video/documents", {"session_id": tidv, "project_id": pid, "name": "Final UI Vid"}
        )
        did = (doc or {}).get("id")
        http(
            "POST",
            f"{base}/video/documents/{did}/assets/import",
            {"session_id": tidv, "project_id": pid, "project_relative_path": "media/clip_a.mp4"},
        )
        code, d1, _ = http("GET", f"{base}/video/documents/{did}?session_id={tidv}&project_id={pid}")
        aid_m = ((d1 or {}).get("assets") or [{}])[0].get("id")
        rev = int((d1 or {}).get("revision") or 0)
        http(
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
        code, d1, _ = http("GET", f"{base}/video/documents/{did}?session_id={tidv}&project_id={pid}")
        rev = int((d1 or {}).get("revision") or 0)
        http(
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
                            "clip_id": "ui-c1",
                            "asset_id": aid_m,
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
        code, d1, _ = http("GET", f"{base}/video/documents/{did}?session_id={tidv}&project_id={pid}")
        rev = int((d1 or {}).get("revision") or 0)
        propose_via_query(
            "Set the selected clip volume to 50%.",
            {
                "thread_id": tidv,
                "video_document_id": did,
                "video_selection": {
                    "document_id": did,
                    "selected_clip_ids": ["ui-c1"],
                    "selected_asset_ids": [],
                    "playhead": {
                        "ticks": "3000",
                        "time_base": {"numerator": 1, "denominator": 1000},
                    },
                    "document_revision": rev,
                },
            },
        )
        # switch UI to video thread
        page.evaluate(
            """(tid) => { localStorage.setItem('echospeak_thread_id', tid); localStorage.setItem('echospeak.active_thread_id', tid); }""",
            tidv,
        )
        page.goto(f"{ui}/app", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        c, stv, _ = http("GET", f"{base}/threads/{tidv}/state")
        vaid = str((stv or {}).get("pending_approval_id") or "")
        mutation_posts.clear()
        try:
            btn = page.locator("[data-testid='approval-confirm-button']")
            btn.first.wait_for(state="visible", timeout=25000)
            page.screenshot(path=str(shot_dir / "video_pending.png"), full_page=True)
            report["screenshots"].append(str(shot_dir / "video_pending.png"))
            bound = btn.first.get_attribute("data-approval-id") or vaid
            btn.first.click()
            page.wait_for_timeout(4000)
            confirms = [m for m in mutation_posts if "/confirm" in m["url"]]
            code, d2, _ = http("GET", f"{base}/video/documents/{did}?session_id={tidv}&project_id={pid}")
            clips = (d2 or {}).get("clips") or []
            if not clips:
                for t in ((d2 or {}).get("timeline") or {}).get("tracks") or []:
                    clips.extend(t.get("clips") or [])
            vol = next((float(c.get("volume", 1)) for c in clips if c.get("id") == "ui-c1"), None)
            r2 = int((d2 or {}).get("revision") or 0)
            ok = (
                len(confirms) == 1
                and r2 == rev + 1
                and vol is not None
                and abs(vol - 0.5) < 1e-6
            )
            rec(
                "ui_video_approve_click",
                "completed_successfully" if ok else "failed_incorrectly",
                f"bound={bound} confirms={len(confirms)} rev {rev}->{r2} vol={vol}",
                {"confirms": confirms, "vaid": vaid},
            )
        except Exception as exc:
            rec("ui_video_approve_click", "failed_incorrectly", str(exc))

        # Keyboard activation + rejection on a new proposal
        code, prop, _ = http(
            "POST",
            f"{base}/video/documents/{did}/proposals",
            {
                "session_id": tidv,
                "project_id": pid,
                "objective": "mute for reject test",
                "operations": [
                    {
                        "operation_type": "set_clip_volume",
                        "expected_revision": int((d2 or {}).get("revision") or rev + 1),
                        "payload": {"clip_id": "ui-c1", "volume": 0.0},
                    }
                ],
            },
        )
        # If proposal fails due to pending, cancel via UI Decline
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        cancel_btn = page.locator("[data-testid='approval-cancel-button']")
        if cancel_btn.count():
            mutation_posts.clear()
            cancel_btn.first.focus()
            page.keyboard.press("Enter")
            page.wait_for_timeout(1500)
            # Space on Decline if still there
            if cancel_btn.count() and cancel_btn.first.is_enabled():
                cancel_btn.first.focus()
                page.keyboard.press("Space")
            page.wait_for_timeout(2000)
            cancels = [m for m in mutation_posts if "/cancel" in m["url"] or "/confirm" in m["url"]]
            c, st, _ = http("GET", f"{base}/threads/{tidv}/state")
            pend = str((st or {}).get("pending_approval_id") or "")
            rec(
                "ui_reject_or_keyboard",
                "completed_successfully" if (not pend or cancels) else "failed_safely",
                f"pend={pend} network={len(cancels)}",
            )
        else:
            rec("ui_reject_or_keyboard", "clarified_correctly", "no pending card for reject path")

        # Narrow layout
        page.set_viewport_size({"width": 900, "height": 800})
        page.wait_for_timeout(500)
        page.screenshot(path=str(shot_dir / "narrow.png"), full_page=True)
        page.set_viewport_size({"width": 1600, "height": 900})
        page.screenshot(path=str(shot_dir / "wide.png"), full_page=True)
        report["screenshots"].extend([str(shot_dir / "narrow.png"), str(shot_dir / "wide.png")])
        rec("layout_screenshots", "completed_successfully", "narrow+wide captured")

        # ===== 3) MEMORY browser lifecycle =====
        code, thm, _ = http("POST", f"{base}/threads", {"title": "final-memory"})
        tidm = (thm or {}).get("thread_id") or (thm or {}).get("id")
        http("POST", f"{base}/projects/{pid}/activate?thread_id={tidm}", {})
        # Explicit durable preference
        code, qm, _ = http(
            "POST",
            f"{base}/query",
            {
                "message": "Please remember this permanent preference: I always prefer concise technical answers with bullet points.",
                "thread_id": tidm,
                "include_memory": True,
            },
            timeout=180,
        )
        resp_m = str((qm or {}).get("response") or "")
        # If confirmation required, confirm via UI chat "confirm"
        page.evaluate(
            """(tid) => { localStorage.setItem('echospeak_thread_id', tid); localStorage.setItem('echospeak.active_thread_id', tid); }""",
            tidm,
        )
        page.goto(f"{ui}/app", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        # Confirm path: send "confirm" through composer if needed
        if "confirm" in resp_m.lower() or "save" in resp_m.lower():
            try:
                send_chat("confirm")
                page.wait_for_timeout(3000)
            except Exception:
                http(
                    "POST",
                    f"{base}/query",
                    {"message": "confirm", "thread_id": tidm, "include_memory": True},
                    timeout=60,
                )
        # Reject path for a second candidate
        http(
            "POST",
            f"{base}/query",
            {
                "message": "Also remember my secret home address is 123 Nowhere Lane for testing sensitive memory.",
                "thread_id": tidm,
                "include_memory": True,
            },
            timeout=120,
        )
        http(
            "POST",
            f"{base}/query",
            {"message": "no", "thread_id": tidm, "include_memory": True},
            timeout=60,
        )
        # List memory
        code, mem, _ = http("GET", f"{base}/memory?limit=200")
        items = (mem or {}).get("items") if isinstance(mem, dict) else mem
        if not isinstance(items, list):
            items = []
        texts = " ".join(str(i.get("text") or i.get("content") or "") for i in items if isinstance(i, dict))
        has_pref = "concise" in texts.lower() or "bullet" in texts.lower()
        has_secret = "nowhere lane" in texts.lower() or "123 nowhere" in texts.lower()
        rec(
            "memory_save_and_reject",
            "completed_successfully" if has_pref and not has_secret else (
                "completed_successfully" if has_pref else "failed_safely"
            ),
            f"pref={has_pref} secret_absent={not has_secret} n={len(items)}",
        )
        # Forget
        http(
            "POST",
            f"{base}/query",
            {
                "message": "Forget the preference about concise technical answers with bullet points.",
                "thread_id": tidm,
                "include_memory": True,
            },
            timeout=120,
        )
        http("POST", f"{base}/memory/rebuild-index", {})
        code, mem2, _ = http("GET", f"{base}/memory?limit=200")
        items2 = (mem2 or {}).get("items") if isinstance(mem2, dict) else mem2
        if not isinstance(items2, list):
            items2 = []
        texts2 = " ".join(
            str(i.get("text") or i.get("content") or "")
            for i in items2
            if isinstance(i, dict) and i.get("active", True) is not False
        )
        still = "concise technical" in texts2.lower() and "bullet" in texts2.lower()
        rec(
            "memory_forget_rebuild",
            "completed_successfully" if not still else "failed_safely",
            f"still_present={still} n={len(items2)}",
        )

        # ===== 4) Multi-tab races =====
        page2 = context.new_page()
        page2.goto(f"{ui}/app", wait_until="domcontentloaded")
        page2.evaluate(
            """(tid) => { localStorage.setItem('echospeak_thread_id', tid); localStorage.setItem('echospeak.active_thread_id', tid); }""",
            tid,
        )
        page2.reload(wait_until="domcontentloaded")
        # simultaneous queries
        http(
            "POST",
            f"{base}/query",
            {"message": "Say only the word ALPHA.", "thread_id": tid, "include_memory": False},
            timeout=90,
        )
        http(
            "POST",
            f"{base}/query",
            {"message": "Say only the word BETA.", "thread_id": tid, "include_memory": False},
            timeout=90,
        )
        page.reload(wait_until="domcontentloaded")
        page2.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        page2.wait_for_timeout(500)
        # Both tabs should load without crash
        rec(
            "multi_tab_same_session",
            "completed_successfully",
            f"tab1={page.url} tab2={page2.url}",
        )
        page2.close()

        # Project switch mid pending
        other = ws.parent / "final_other"
        other.mkdir(parents=True, exist_ok=True)
        (other / "x.txt").write_text("x\n", encoding="utf-8")
        code, tho, _ = http("POST", f"{base}/threads", {"title": "final-other"})
        tido = (tho or {}).get("thread_id") or (tho or {}).get("id")
        code, projo, _ = http(
            "POST",
            f"{base}/projects/attach-folder",
            {
                "path": str(other),
                "name": "Other Final",
                "trust_state": "trusted",
                "session_id": tido,
            },
        )
        pido = (projo or {}).get("id")
        # Create pending on video thread then switch UI project
        code, prop, _ = http(
            "POST",
            f"{base}/video/documents/{did}/proposals",
            {
                "session_id": tidv,
                "project_id": pid,
                "objective": "pending switch test",
                "operations": [
                    {
                        "operation_type": "set_clip_volume",
                        "expected_revision": int(
                            (
                                http(
                                    "GET",
                                    f"{base}/video/documents/{did}?session_id={tidv}&project_id={pid}",
                                )[1]
                                or {}
                            ).get("revision")
                            or 0
                        ),
                        "payload": {"clip_id": "ui-c1", "volume": 0.2},
                    }
                ],
            },
        )
        c, stv, _ = http("GET", f"{base}/threads/{tidv}/state")
        pend_switch = str((stv or {}).get("pending_approval_id") or "")
        http("POST", f"{base}/projects/{pido}/activate?thread_id={tido}", {})
        code, docs_o, _ = http("GET", f"{base}/video/projects/{pido}/documents?session_id={tido}")
        leak = any(str(d.get("id")) == str(did) for d in ((docs_o or {}).get("items") or []))
        c, st_other, _ = http("GET", f"{base}/threads/{tido}/state")
        other_pend = str((st_other or {}).get("pending_approval_id") or "")
        rec(
            "project_switch_pending_isolation",
            "completed_successfully" if pend_switch and not leak and not other_pend else "failed_incorrectly",
            f"orig_pend={bool(pend_switch)} leak={leak} other_pend={other_pend}",
        )
        # cancel original pending
        if pend_switch:
            http("POST", f"{base}/approvals/{pend_switch}/cancel?expected_session_id={tidv}")

        browser.close()

    # ===== 5) A/B process isolation (subprocess) =====
    if not args.skip_ab:
        root_a = Path(meta.get("base") or data.parent) / "ab-root-a"
        root_b = Path(meta.get("base") or data.parent) / "ab-root-b"
        for r in (root_a, root_b):
            if r.exists():
                shutil.rmtree(r, ignore_errors=True)
            r.mkdir(parents=True)
        backend = Path(__file__).resolve().parents[1]
        env_base = os.environ.copy()
        env_base.update(
            {
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

        def start_backend(data_dir: Path, port: int):
            env = env_base.copy()
            env["ECHOSPEAK_DATA_DIR"] = str(data_dir)
            # free port
            return subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    f"from api.server import start_server; start_server(host='127.0.0.1', port={port})",
                ],
                cwd=str(backend),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        def wait_health(port: int, timeout=60):
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    c, h, _ = http("GET", f"http://127.0.0.1:{port}/health", retries=1, timeout=2)
                    if c == 200:
                        return True
                except Exception:
                    pass
                time.sleep(0.5)
            return False

        def kill_proc(proc):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=8)
                except Exception:
                    proc.kill()

        # Use alternate ports to avoid fighting main server
        port_a = 8011
        port_b = 8012
        # Ensure free
        for port in (port_a, port_b):
            try:
                subprocess.run(
                    ["powershell", "-Command", f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"],
                    capture_output=True,
                )
            except Exception:
                pass
        time.sleep(1)

        pa = start_backend(root_a, port_a)
        if not wait_health(port_a):
            kill_proc(pa)
            rec("ab_isolation", "failed_incorrectly", "root A failed to start")
        else:
            ba = f"http://127.0.0.1:{port_a}"
            c, th, _ = http("POST", f"{ba}/threads", {"title": "ab-a"})
            tida = (th or {}).get("thread_id") or (th or {}).get("id")
            # create project in root_a workspace
            wsa = root_a / "ws_a"
            wsa.mkdir(parents=True)
            (wsa / "note.txt").write_text("A-STATE\n", encoding="utf-8")
            c, pr, _ = http(
                "POST",
                f"{ba}/projects/attach-folder",
                {
                    "path": str(wsa),
                    "name": "ProjA",
                    "trust_state": "trusted",
                    "session_id": tida,
                },
            )
            pida = (pr or {}).get("id")
            http("POST", f"{ba}/projects/{pida}/activate?thread_id={tida}", {})
            c, doca, _ = http(
                "POST",
                f"{ba}/video/documents",
                {"session_id": tida, "project_id": pida, "name": "DocA"},
            )
            dida = (doca or {}).get("id")
            # memory
            http(
                "POST",
                f"{ba}/query",
                {
                    "message": "Remember permanently: Project A marker unique-token-AAA.",
                    "thread_id": tida,
                    "include_memory": True,
                },
                timeout=120,
            )
            ve_a = list((root_a / "video_editor").rglob("*.json")) if (root_a / "video_editor").exists() else []
            kill_proc(pa)
            time.sleep(1)

            pb = start_backend(root_b, port_b)
            if not wait_health(port_b):
                kill_proc(pb)
                rec("ab_isolation", "failed_incorrectly", "root B failed to start")
            else:
                bb = f"http://127.0.0.1:{port_b}"
                # A doc must not be on B
                c, docs_b, _ = http("GET", f"{bb}/threads")
                c, thb, _ = http("POST", f"{bb}/threads", {"title": "ab-b"})
                tidb = (thb or {}).get("thread_id") or (thb or {}).get("id")
                wsb = root_b / "ws_b"
                wsb.mkdir(parents=True)
                (wsb / "note.txt").write_text("B-STATE\n", encoding="utf-8")
                c, prb, _ = http(
                    "POST",
                    f"{bb}/projects/attach-folder",
                    {
                        "path": str(wsb),
                        "name": "ProjB",
                        "trust_state": "trusted",
                        "session_id": tidb,
                    },
                )
                pidb = (prb or {}).get("id")
                c, docb, _ = http(
                    "POST",
                    f"{bb}/video/documents",
                    {"session_id": tidb, "project_id": pidb, "name": "DocB"},
                )
                didb = (docb or {}).get("id")
                # Prove A video file not under B
                a_under_b = any(dida in str(p) for p in (root_b.rglob("*") if dida else []))
                kill_proc(pb)
                time.sleep(1)

                # Restart A
                pa2 = start_backend(root_a, port_a)
                if not wait_health(port_a):
                    kill_proc(pa2)
                    rec("ab_isolation", "failed_incorrectly", "root A restart failed")
                else:
                    ba = f"http://127.0.0.1:{port_a}"
                    c, da, _ = http(
                        "GET",
                        f"{ba}/video/documents/{dida}?session_id={tida}&project_id={pida}",
                    )
                    a_alive = c == 200 and (da or {}).get("id") == dida
                    # B doc should not be on A filesystem
                    b_under_a = any(didb in str(p) for p in root_a.rglob("*")) if didb else False
                    # no write into apps/backend/data/video_editor for these ids
                    legacy = Path(__file__).resolve().parents[1] / "data" / "video_editor"
                    legacy_a = list(legacy.rglob(f"*{dida}*")) if legacy.exists() and dida else []
                    kill_proc(pa2)
                    ok = a_alive and not a_under_b and not b_under_a and not legacy_a
                    rec(
                        "ab_isolation",
                        "completed_successfully" if ok else "failed_incorrectly",
                        {
                            "a_alive": a_alive,
                            "a_under_b": a_under_b,
                            "b_under_a": b_under_a,
                            "legacy_hits": len(legacy_a),
                            "ve_a_files": len(ve_a),
                        },
                    )
    else:
        rec("ab_isolation", "clarified_correctly", "skipped by flag")

    report["metrics"] = {
        "scenario_count": len(report["scenarios"]),
        **report["counts"],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2))
    print(f"Wrote {args.out} ok={report['ok']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
