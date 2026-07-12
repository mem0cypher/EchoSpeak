"""Evidence extraction and page cache for Echo Search."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urldefrag


@dataclass(frozen=True)
class Evidence:
    claim: str
    source_url: str
    source_title: str
    quote_or_summary: str
    confidence: str
    supports_answer_part: str

    def as_dict(self) -> dict:
        return {
            "claim": self.claim,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "quote_or_summary": self.quote_or_summary,
            "confidence": self.confidence,
            "supports_answer_part": self.supports_answer_part,
        }


def canonical_url(url: str) -> str:
    clean, _frag = urldefrag(str(url or "").strip())
    return clean.rstrip("/")


class PageCache:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS page_cache (url TEXT PRIMARY KEY, content TEXT NOT NULL, fetched_at REAL NOT NULL)"
            )

    def get(self, url: str) -> str:
        key = canonical_url(url)
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT content FROM page_cache WHERE url=?", (key,)).fetchone()
        return str(row[0]) if row else ""

    def set(self, url: str, content: str) -> None:
        key = canonical_url(url)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO page_cache(url, content, fetched_at) VALUES (?, ?, ?)",
                (key, str(content or ""), time.time()),
            )


def html_to_readable_text(html: str) -> str:
    raw = str(html or "")
    if not raw:
        return ""
    try:
        import trafilatura  # type: ignore

        extracted = trafilatura.extract(raw)
        if extracted:
            return re.sub(r"\s+", " ", extracted).strip()
    except Exception:
        pass
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_page_text(url: str, cache: PageCache, *, timeout_s: float = 12.0, use_playwright_fallback: bool = False) -> str:
    cached = cache.get(url)
    if cached:
        return cached
    text = ""
    try:
        import httpx

        with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": "EchoSearch/1.0"})
            response.raise_for_status()
            text = html_to_readable_text(response.text)
    except Exception:
        text = ""
    if not text and use_playwright_fallback:
        text = fetch_page_text_playwright(url, timeout_s=timeout_s)
    if text:
        cache.set(url, text[:50000])
    return text


def fetch_page_text_playwright(url: str, *, timeout_s: float = 12.0) -> str:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=int(timeout_s * 1000))
            text = page.locator("body").inner_text(timeout=int(timeout_s * 1000))
            browser.close()
            return re.sub(r"\s+", " ", text).strip()
    except Exception:
        return ""


def evidence_key_terms(parts: Iterable[str]) -> set[str]:
    terms: set[str] = set()
    for part in parts:
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{3,}", str(part or "").lower()):
            if token not in {"what", "when", "where", "with", "from", "this", "that", "have", "about", "official"}:
                terms.add(token)
    return terms


def extract_evidence_from_text(
    *,
    text: str,
    source_url: str,
    source_title: str,
    must_answer: list[str],
    entities: list[str],
    max_items: int = 3,
) -> list[Evidence]:
    terms = evidence_key_terms([*must_answer, *entities])
    sentences = re.split(r"(?<=[.!?])\s+", str(text or ""))
    out: list[Evidence] = []
    seen = set()
    for sentence in sentences:
        clean = re.sub(r"\s+", " ", sentence).strip()
        if len(clean) < 40:
            continue
        low = clean.lower()
        score = sum(1 for term in terms if term in low)
        if score <= 0 and terms:
            continue
        key = hashlib.sha1(clean.lower().encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        conf = "high" if score >= 3 else "medium" if score >= 1 else "low"
        part = next((m for m in must_answer if any(t in m.lower() for t in terms if t in low)), must_answer[0] if must_answer else "answer")
        out.append(
            Evidence(
                claim=clean[:180],
                source_url=source_url,
                source_title=source_title,
                quote_or_summary=clean[:500],
                confidence=conf,
                supports_answer_part=part,
            )
        )
        if len(out) >= max_items:
            break
    return out
