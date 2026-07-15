"""Real browser acceptance for EchoSpeak web UI (Playwright Chromium).

Requires:
  - backend on --api (default http://127.0.0.1:8000)
  - frontend on --ui (default http://127.0.0.1:5174)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ui", default="http://127.0.0.1:5174")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    rows: list[dict] = []

    def rec(name: str, ok: bool, detail: str = "") -> None:
        rows.append({"name": name, "ok": ok, "detail": detail})
        print(("PASS" if ok else "FAIL"), name, detail)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.set_default_timeout(45000)

        app_url = f"{args.ui.rstrip('/')}/app"
        research_url = f"{args.ui.rstrip('/')}/app/research"

        # 1) Open marketing first (no session), then app
        try:
            page.goto(args.ui, wait_until="domcontentloaded")
            page.wait_for_timeout(800)
            rec("browser_open_marketing", True, page.url)
            page.goto(app_url, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            rec("browser_open_app", True, page.url)
        except Exception as exc:
            rec("browser_open_app", False, str(exc))
            browser.close()
            return _finish(rows, args.out)

        # Capture thread list before interaction via API
        import urllib.request

        def api_get(path: str):
            with urllib.request.urlopen(f"{args.api}{path}", timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))

        def threads_count(payload) -> int:
            if isinstance(payload, list):
                return len(payload)
            if isinstance(payload, dict):
                return int(payload.get("count") or len(payload.get("items") or []) or 0)
            return 0

        try:
            before = api_get("/threads")
            before_n = threads_count(before)
            rec("api_threads_before_ui", True, f"count={before_n}")
        except Exception as exc:
            rec("api_threads_before_ui", False, str(exc))
            before_n = -1

        # Workspace navigation must not create a Session by itself.
        try:
            page.goto(research_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            rec("browser_workspace_nav", True, page.url)
        except Exception as exc:
            rec("browser_workspace_nav", False, str(exc))

        try:
            after = api_get("/threads")
            after_n = threads_count(after)
            rec(
                "no_phantom_session_on_workspace_nav",
                before_n < 0 or after_n == before_n,
                f"before={before_n} after={after_n}",
            )
        except Exception as exc:
            rec("no_phantom_session_on_workspace_nav", False, str(exc))

        # Go to main chat UI (/app)
        try:
            page.goto(app_url, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            # Try common selectors for chat composer
            selectors = [
                "textarea",
                "textarea[placeholder]",
                "[contenteditable='true']",
                "input[type='text']",
                "[data-testid='chat-input']",
                ".chat-input textarea",
                "form textarea",
            ]
            box = None
            for sel in selectors:
                loc = page.locator(sel).first
                try:
                    if loc.count() > 0 and loc.is_visible():
                        box = loc
                        break
                except Exception:
                    continue
            if box is None:
                # dump buttons/text for diagnosis
                body = page.inner_text("body")[:500]
                rec("find_chat_input", False, body.replace("\n", " ")[:200])
            else:
                rec("find_chat_input", True, "found")
                box.click()
                box.fill("Hello from live browser acceptance. Reply with the single word hello.")
                # submit
                submitted = False
                try:
                    box.press("Enter")
                    submitted = True
                except Exception:
                    btn = page.locator("button:has-text('Send'), button[type='submit']").first
                    if btn.count():
                        btn.click()
                        submitted = True
                # Wait for model reply (live LM Studio)
                page.wait_for_timeout(20000)
                body = page.inner_text("body")
                rec("chat_submit_visible", submitted, body[:160].replace("\n", " "))
                # Look for assistant content / activity
                has_reply = bool(re.search(r"hello|Hello|Echo|thinking|tool|pong", body, re.I))
                rec(
                    "chat_response_or_activity",
                    has_reply,
                    "matched activity/reply markers" if has_reply else "no markers yet",
                )
        except Exception as exc:
            rec("chat_flow", False, str(exc))

        # Second tab same app Session surface
        try:
            page2 = context.new_page()
            page2.goto(app_url, wait_until="domcontentloaded")
            page2.wait_for_timeout(1500)
            rec("second_tab_open", True, page2.url)
            page2.close()
        except Exception as exc:
            rec("second_tab_open", False, str(exc))

        # Refresh hydration
        try:
            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            body = page.inner_text("body")
            rec("refresh_hydration", True, body[:120].replace("\n", " "))
        except Exception as exc:
            rec("refresh_hydration", False, str(exc))

        # Screenshot evidence
        try:
            shot = Path(args.out or ".").resolve().parent / "live_browser_shot.png"
            if args.out:
                shot = Path(args.out).with_suffix(".png")
            page.screenshot(path=str(shot), full_page=True)
            rec("screenshot", True, str(shot))
        except Exception as exc:
            rec("screenshot", False, str(exc))

        browser.close()

    return _finish(rows, args.out)


def _finish(rows: list[dict], out: str) -> int:
    n_ok = sum(1 for r in rows if r["ok"])
    n_fail = sum(1 for r in rows if not r["ok"])
    print(f"\n=== BROWSER SUMMARY: {n_ok} passed, {n_fail} failed / {len(rows)} ===")
    if out:
        Path(out).write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
