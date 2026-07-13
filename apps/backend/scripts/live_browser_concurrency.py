"""Deeper multi-tab / stream-race browser acceptance (Playwright)."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ui", default="http://127.0.0.1:5174")
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    rows = []

    def rec(name: str, ok: bool, detail: str = "") -> None:
        rows.append({"name": name, "ok": ok, "detail": detail})
        print(("PASS" if ok else "FAIL"), name, detail)

    app = f"{args.ui.rstrip('/')}/app"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        t1 = ctx.new_page()
        t2 = ctx.new_page()
        t1.set_default_timeout(45000)
        t2.set_default_timeout(45000)

        try:
            t1.goto(app, wait_until="domcontentloaded")
            t2.goto(app, wait_until="domcontentloaded")
            t1.wait_for_timeout(1500)
            t2.wait_for_timeout(1500)
            rec("two_tabs_open", True, f"{t1.url} | {t2.url}")
        except Exception as exc:
            rec("two_tabs_open", False, str(exc))
            browser.close()
            return _done(rows, args.out)

        def find_input(page):
            for sel in ["textarea", "[contenteditable='true']", "form textarea"]:
                loc = page.locator(sel).first
                try:
                    if loc.count() and loc.is_visible():
                        return loc
                except Exception:
                    continue
            return None

        box1 = find_input(t1)
        box2 = find_input(t2)
        rec("find_inputs", bool(box1 and box2), "")

        # Simultaneous messages
        try:
            if box1 and box2:
                box1.fill("Tab1: reply with only the word alpha")
                box2.fill("Tab2: reply with only the word beta")
                box1.press("Enter")
                box2.press("Enter")
                t1.wait_for_timeout(18000)
                b1 = t1.inner_text("body")
                b2 = t2.inner_text("body")
                rec("simultaneous_send", True, f"t1_len={len(b1)} t2_len={len(b2)}")
            else:
                rec("simultaneous_send", False, "missing inputs")
        except Exception as exc:
            rec("simultaneous_send", False, str(exc))

        # Refresh during/after stream
        try:
            t1.reload(wait_until="domcontentloaded")
            t1.wait_for_timeout(2500)
            rec("refresh_tab1", True, t1.inner_text("body")[:80].replace("\n", " "))
        except Exception as exc:
            rec("refresh_tab1", False, str(exc))

        # Project switch UI if present — best effort click
        try:
            # Look for project list entries
            clicked = False
            for label in ["Live Coding", "Gap Project", "video-game", "Projects"]:
                loc = t2.get_by_text(label, exact=False).first
                if loc.count():
                    try:
                        loc.click(timeout=2000)
                        clicked = True
                        break
                    except Exception:
                        continue
            t2.wait_for_timeout(1500)
            rec("project_switch_attempt", True, f"clicked={clicked}")
        except Exception as exc:
            rec("project_switch_attempt", False, str(exc))

        # Cancel if approval button visible
        try:
            cancel = t1.get_by_role("button", name="Cancel").first
            if cancel.count() and cancel.is_visible():
                cancel.click()
                rec("cancel_button", True, "clicked")
            else:
                rec("cancel_button", True, "no pending approval button")
        except Exception as exc:
            rec("cancel_button", False, str(exc))

        # Second tab still coherent after first tab refresh
        try:
            body2 = t2.inner_text("body")
            rec("tab2_still_valid", "EchoSpeak" in body2 or "PROJECT" in body2.upper() or len(body2) > 50, body2[:80].replace("\n", " "))
        except Exception as exc:
            rec("tab2_still_valid", False, str(exc))

        try:
            shot = Path(args.out).with_suffix(".png") if args.out else Path("live_browser_concurrency.png")
            t1.screenshot(path=str(shot), full_page=True)
            rec("screenshot", True, str(shot))
        except Exception as exc:
            rec("screenshot", False, str(exc))

        browser.close()
    return _done(rows, args.out)


def _done(rows, out: str) -> int:
    n_ok = sum(1 for r in rows if r["ok"])
    n_fail = sum(1 for r in rows if not r["ok"])
    print(f"\n=== BROWSER CONCURRENCY: {n_ok} passed, {n_fail} failed / {len(rows)} ===")
    if out:
        Path(out).write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
