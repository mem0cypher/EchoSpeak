#!/usr/bin/env python3
import json
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

base = "http://127.0.0.1:8000"
ui = "http://127.0.0.1:5174"
pre = json.loads(
    Path(r"C:\Users\ty0x7\Desktop\EchoSpeak\.live-acceptance\video_storage_pre_restart.json").read_text(
        encoding="utf-8"
    )
)
rep = json.loads(
    Path(r"C:\Users\ty0x7\Desktop\EchoSpeak\.live-acceptance\live_playwright_video_report.json").read_text(
        encoding="utf-8"
    )
)
fd = rep["final"]
did, tid, pid = fd["document_id"], fd["session_id"], fd["project_id"]
rev = fd["revision"]


def http(method, url, body=None, timeout=60):
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    for attempt in range(8):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode() or "null")
        except Exception as e:
            code = getattr(e, "code", 0) or 0
            raw = e.read().decode() if hasattr(e, "read") else str(e)
            if code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            try:
                return code, json.loads(raw)
            except Exception:
                return code, {"error": raw}
    return 0, {"error": "retries"}


http("POST", f"{base}/projects/{pid}/activate?thread_id={tid}", {})
code, doc = http("GET", f"{base}/video/documents/{did}?session_id={tid}&project_id={pid}")
clips = doc.get("clips") or []
if not clips:
    for t in (doc.get("timeline") or {}).get("tracks") or []:
        clips.extend(t.get("clips") or [])
vol = next((float(c.get("volume", 1)) for c in clips if c.get("id") == "pw-c1"), None)
print("POST_RESTART_API", code, "rev", doc.get("revision"), "vol", vol, "clips", [c.get("id") for c in clips])

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(f"{ui}/app/video", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    print("BROWSER_URL", page.url)
    browser.close()

p = Path(pre["path"])
print("DURABLE_EXISTS", p.exists(), p)
d = json.loads(p.read_text(encoding="utf-8"))
print("DURABLE_REV", d.get("revision"))
legacy = Path(r"C:\Users\ty0x7\Desktop\EchoSpeak\apps\backend\data\video_editor")
legacy_hit = list(legacy.rglob(f"*{did}*")) if legacy.exists() else []
print("LEGACY_HIT_THIS_DOC", len(legacy_hit))

out = {
    "post_restart_api_rev": doc.get("revision"),
    "expected_rev": rev,
    "volume": vol,
    "durable_path": str(p),
    "legacy_hits": len(legacy_hit),
    "ok": code == 200
    and int(doc.get("revision") or 0) == int(rev)
    and vol is not None
    and abs(vol) < 1e-6
    and p.exists()
    and str(p).replace("\\", "/").find("data-storage-pass") >= 0,
}
Path(r"C:\Users\ty0x7\Desktop\EchoSpeak\.live-acceptance\video_storage_post_restart.json").write_text(
    json.dumps(out, indent=2) + "\n", encoding="utf-8"
)
print("RESULT", out)
raise SystemExit(0 if out["ok"] else 1)
