"""Echo Search providers.

SearXNG is the primary provider for Echo Search v1.  The provider returns a
stable result shape independent of SearXNG engine quirks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin


@dataclass(frozen=True)
class NormalizedSearchResult:
    title: str
    url: str
    snippet: str
    source: str = "searxng"
    engine: str = ""
    rank: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "engine": self.engine,
            "rank": self.rank,
        }


class SearXNGSearchProvider:
    name = "searxng"

    def __init__(self, base_url: str, *, timeout_s: float = 12.0):
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout_s = timeout_s

    @property
    def available(self) -> bool:
        return bool(self.base_url)

    def search(self, query: str, *, limit: int = 5, categories: str | None = None) -> list[NormalizedSearchResult]:
        if not self.available:
            raise RuntimeError("SEARXNG_BASE_URL is not configured")
        import httpx

        params: dict[str, Any] = {
            "q": str(query or "").strip(),
            "format": "json",
        }
        if categories:
            params["categories"] = categories
        url = urljoin(self.base_url + "/", "search")
        with httpx.Client(timeout=self.timeout_s, follow_redirects=True) as client:
            response = client.get(url, params=params)
            if response.status_code in {400, 415}:
                response = client.post(url, json=params)
            response.raise_for_status()
            payload = response.json()
        return normalize_searxng_results(payload, limit=limit)


def normalize_searxng_results(payload: dict[str, Any], *, limit: int = 5) -> list[NormalizedSearchResult]:
    rows = payload.get("results") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        rows = []
    out: list[NormalizedSearchResult] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("content") or "").strip()
        url = str(row.get("url") or "").strip()
        snippet = str(row.get("content") or row.get("snippet") or "").strip()
        engine = row.get("engine") or row.get("engines") or ""
        if isinstance(engine, list):
            engine = ",".join(str(x) for x in engine if str(x).strip())
        engine = str(engine or "").strip()
        if not url and not title:
            continue
        out.append(
            NormalizedSearchResult(
                title=title or url,
                url=url,
                snippet=snippet,
                source="searxng",
                engine=engine,
                rank=len(out) + 1,
            )
        )
        if len(out) >= limit:
            break
    return out
