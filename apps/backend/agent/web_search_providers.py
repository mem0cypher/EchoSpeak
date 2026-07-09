"""Pluggable web search providers + free-tier engineering upgrades.

Scope
-----
Paid APIs (Brave/Tavily/Exa/Firecrawl) sell two things:
  1) A web index + crawl ops  → hard to DIY
  2) Agent packaging (clean JSON, extract, recency) → *engineerable*

Echo already owns (2) via multi-intent + SearchGrounder. This module owns
**retrieval adapters** so we can:
  - squeeze more quality out of DuckDuckGo (default free path)
  - optionally plug Tavily / Brave when keys exist
  - cascade: preferred → fallback without changing the grounder

Engineering upgrades on free DDG (no paid key required)
-------------------------------------------------------
  - Dual channel: ``text`` + ``news`` for recency-ish queries
  - Empty-result retry with simplified query
  - Light authority query variants (site: for weather/sports)
  - Optional URL extract via ddgs.extract when snippets are thin
  - Unified result shape for the grounder / formatters
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from loguru import logger


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    extract: str = ""
    date: str = ""
    page_title: str = ""
    provider: str = ""
    query: str = ""
    score_hint: float = 0.0

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "extract": self.extract,
            "date": self.date,
            "page_title": self.page_title,
            "_query": self.query,
            "_provider": self.provider,
            "_score_hint": self.score_hint,
        }


@dataclass
class SearchProviderResult:
    hits: List[SearchHit] = field(default_factory=list)
    provider: str = ""
    errors: List[str] = field(default_factory=list)
    queries_used: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Query engineering (free quality boosts)
# ---------------------------------------------------------------------------


def _is_newsish(q: str) -> bool:
    low = (q or "").lower()
    return any(
        t in low
        for t in (
            "news", "latest", "breaking", "headline", "today", "yesterday",
            "this week", "update", "updates",
        )
    )


def _is_weatherish(q: str) -> bool:
    low = (q or "").lower()
    return any(t in low for t in ("weather", "forecast", "temperature", "high low", "°"))


def _is_sportsish(q: str) -> bool:
    low = (q or "").lower()
    return any(
        t in low
        for t in (
            "schedule", "fixture", "nhl", "nba", "nfl", "mlb", "fifa", "world cup",
            "score", "match", "kickoff", "vs", "versus", "hockey", "basketball",
        )
    )


def build_query_variants(query: str, *, max_variants: int = 3) -> List[str]:
    """Extra free retrieval angles when the primary query is thin."""
    q = re.sub(r"\s+", " ", str(query or "").strip())
    if not q:
        return []
    out: List[str] = [q]
    low = q.lower()

    # Recency nudge without requiring paid "freshness" filters
    if _is_newsish(q) or re.search(r"\b(today|tomorrow|tonight)\b", low):
        from datetime import datetime

        month_year = datetime.now().strftime("%B %Y")
        if month_year.lower() not in low:
            out.append(f"{q} {month_year}")

    # Authority site variants by *league/domain keyword* — never franchise nicknames
    if _is_weatherish(q) and "site:" not in low:
        out.append(f"{q} site:accuweather.com OR site:weather.gc.ca OR site:weather.com")
    if _is_sportsish(q) and "site:" not in low and re.search(r"\b(fifa|world cup)\b", low):
        out.append(f"{q} site:fifa.com OR site:espn.com OR site:foxsports.com")
        # Kickoff-oriented variant when user/follow-up needs times
        if re.search(r"\b(kickoff|time|timezone|mnt|mountain|schedule)\b", low):
            out.append(f"{q} kickoff times ET list")
    elif _is_sportsish(q) and "site:" not in low and re.search(r"\b(nhl|hockey)\b", low):
        out.append(f"{q} site:nhl.com OR site:espn.com")
    elif _is_sportsish(q) and "site:" not in low and re.search(r"\b(nba|basketball)\b", low):
        out.append(f"{q} site:nba.com OR site:espn.com")
    elif _is_sportsish(q) and "site:" not in low and re.search(r"\b(nfl)\b", low):
        out.append(f"{q} site:nfl.com OR site:espn.com")
    elif _is_sportsish(q) and "site:" not in low:
        # Generic sports authority when no league keyword
        out.append(f"{q} site:espn.com OR site:cbssports.com")

    # Simplified retry form: drop filler words
    simple = re.sub(
        r"(?i)\b(please|what is|what's|who is|find|search|check|look up|the|a|an)\b",
        " ",
        q,
    )
    simple = re.sub(r"\s+", " ", simple).strip()
    if simple and simple.lower() != q.lower() and len(simple) >= 6:
        out.append(simple)

    # Dedupe preserve order
    seen = set()
    deduped = []
    for v in out:
        k = v.lower()
        if k in seen:
            continue
        seen.add(k)
        deduped.append(v)
        if len(deduped) >= max_variants:
            break
    return deduped


def simplify_query(query: str) -> str:
    q = re.sub(r"\s+", " ", str(query or "").strip())
    q = re.sub(r"(?i)\b(OR|site:[^\s]+)\b", " ", q)
    q = re.sub(r"[^\w\s\-]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


def _domain(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


class DuckDuckGoProvider:
    """Free default. Uses ddgs text + news + optional extract."""

    name = "duckduckgo"

    def __init__(self, max_results: int = 8, timeout_s: float = 12.0):
        self.max_results = max(3, min(int(max_results), 12))
        self.timeout_s = timeout_s

    def search(self, query: str, *, news: bool = False) -> SearchProviderResult:
        q = str(query or "").strip()
        if not q:
            return SearchProviderResult(provider=self.name, errors=["empty query"])
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS  # type: ignore
        except ImportError:
            return SearchProviderResult(
                provider=self.name,
                errors=["DuckDuckGo package not installed (ddgs / duckduckgo_search)"],
            )

        hits: List[SearchHit] = []
        errors: List[str] = []
        queries_used = [q]
        try:
            with DDGS() as ddgs:
                if news or _is_newsish(q):
                    try:
                        news_rows = list(ddgs.news(q, max_results=min(self.max_results, 8)))
                        for r in news_rows:
                            title = str(r.get("title") or "").strip()
                            url = str(r.get("url") or r.get("href") or "").strip()
                            snippet = str(r.get("body") or r.get("excerpt") or "").strip()
                            date = str(r.get("date") or "").strip()
                            if not url and not title:
                                continue
                            hits.append(
                                SearchHit(
                                    title=title or "News",
                                    url=url,
                                    snippet=snippet,
                                    date=date,
                                    provider=self.name,
                                    query=q,
                                    score_hint=0.15 if date else 0.05,
                                )
                            )
                    except Exception as exc:
                        errors.append(f"ddg news: {exc}")

                try:
                    rows = list(ddgs.text(q, max_results=self.max_results))
                except TypeError:
                    rows = list(ddgs.text(q))
                for r in rows:
                    title = str(r.get("title") or "").strip()
                    url = str(r.get("href") or r.get("url") or "").strip()
                    snippet = str(r.get("body") or r.get("snippet") or "").strip()
                    if not url and not title:
                        continue
                    hits.append(
                        SearchHit(
                            title=title or "Result",
                            url=url,
                            snippet=snippet,
                            provider=self.name,
                            query=q,
                        )
                    )
        except Exception as exc:
            errors.append(f"ddg search failed: {exc}")
            return SearchProviderResult(provider=self.name, errors=errors, queries_used=queries_used)

        # Empty → simplified retry
        if not hits:
            simple = simplify_query(q)
            if simple and simple.lower() != q.lower():
                queries_used.append(simple)
                try:
                    with DDGS() as ddgs:
                        rows = list(ddgs.text(simple, max_results=self.max_results))
                        for r in rows:
                            title = str(r.get("title") or "").strip()
                            url = str(r.get("href") or r.get("url") or "").strip()
                            snippet = str(r.get("body") or "").strip()
                            if url or title:
                                hits.append(
                                    SearchHit(
                                        title=title or "Result",
                                        url=url,
                                        snippet=snippet,
                                        provider=self.name,
                                        query=simple,
                                    )
                                )
                except Exception as exc:
                    errors.append(f"ddg simplified retry failed: {exc}")

        return SearchProviderResult(
            hits=_dedupe_hits(hits),
            provider=self.name,
            errors=errors,
            queries_used=queries_used,
        )

    def extract_url(self, url: str) -> str:
        """Optional full-page extract (Firecrawl-like, free via ddgs)."""
        url = str(url or "").strip()
        if not url.startswith("http"):
            return ""
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS  # type: ignore
            with DDGS() as ddgs:
                data = ddgs.extract(url, fmt="text_markdown")
            if isinstance(data, dict):
                for k in ("text", "markdown", "content", "body"):
                    if data.get(k):
                        return str(data.get(k))[:8000]
                # any string value
                for v in data.values():
                    if isinstance(v, str) and len(v) > 40:
                        return v[:8000]
            if isinstance(data, str):
                return data[:8000]
        except Exception as exc:
            logger.debug("ddg extract failed for {}: {}", url[:80], exc)
        return ""


class TavilyProvider:
    name = "tavily"

    def __init__(self, api_key: str, max_results: int = 8, timeout_s: float = 10.0, depth: str = "advanced"):
        self.api_key = (api_key or "").strip()
        self.max_results = max_results
        self.timeout_s = timeout_s
        self.depth = depth

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, **_kwargs) -> SearchProviderResult:
        if not self.available:
            return SearchProviderResult(provider=self.name, errors=["TAVILY_API_KEY not set"])
        q = str(query or "").strip()
        try:
            import requests

            resp = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.api_key,
                    "query": q,
                    "search_depth": self.depth,
                    "max_results": max(1, min(self.max_results, 10)),
                    "include_answer": False,
                    "include_raw_content": True,
                },
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            data = resp.json() or {}
            hits = []
            for r in data.get("results") or []:
                hits.append(
                    SearchHit(
                        title=str(r.get("title") or "No title").strip(),
                        url=str(r.get("url") or "").strip(),
                        snippet=str(r.get("content") or "").strip(),
                        extract=str(r.get("raw_content") or "").strip()[:4000],
                        date=str(r.get("published_date") or r.get("published_at") or "").strip(),
                        provider=self.name,
                        query=q,
                    )
                )
            return SearchProviderResult(hits=_dedupe_hits(hits), provider=self.name, queries_used=[q])
        except Exception as exc:
            return SearchProviderResult(
                provider=self.name,
                errors=[_format_provider_error("Tavily", exc, self.timeout_s)],
                queries_used=[q],
            )


class BraveProvider:
    """Brave Search API — optional; independent index."""

    name = "brave"

    def __init__(self, api_key: str, max_results: int = 8, timeout_s: float = 10.0):
        self.api_key = (api_key or "").strip()
        self.max_results = max_results
        self.timeout_s = timeout_s

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, **_kwargs) -> SearchProviderResult:
        if not self.available:
            return SearchProviderResult(provider=self.name, errors=["BRAVE_SEARCH_API_KEY not set"])
        q = str(query or "").strip()
        try:
            import requests

            resp = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": q, "count": max(1, min(self.max_results, 20))},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self.api_key,
                },
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            data = resp.json() or {}
            web = (data.get("web") or {}).get("results") or []
            hits = []
            for r in web:
                hits.append(
                    SearchHit(
                        title=str(r.get("title") or "").strip(),
                        url=str(r.get("url") or "").strip(),
                        snippet=str(r.get("description") or r.get("snippet") or "").strip(),
                        date=str(r.get("age") or r.get("page_age") or "").strip(),
                        provider=self.name,
                        query=q,
                    )
                )
            return SearchProviderResult(hits=_dedupe_hits(hits), provider=self.name, queries_used=[q])
        except Exception as exc:
            return SearchProviderResult(
                provider=self.name,
                errors=[_format_provider_error("Brave", exc, self.timeout_s)],
                queries_used=[q],
            )


def _format_provider_error(label: str, exc: BaseException, timeout_s: float) -> str:
    """Stable user/test-facing provider errors (timeout must mention duration)."""
    name = type(exc).__name__
    msg = str(exc or "").strip()
    is_timeout = name in {"Timeout", "ReadTimeout", "ConnectTimeout", "TimeoutError"} or (
        "timeout" in msg.lower() and "timed out" in msg.lower()
    )
    # requests.exceptions.Timeout → empty or short message; always surface duration
    try:
        import requests

        if isinstance(exc, requests.exceptions.Timeout):
            is_timeout = True
    except Exception:
        pass
    if is_timeout or name.endswith("Timeout"):
        secs = int(timeout_s) if timeout_s else 10
        return f"{label} timed out after {secs}s"
    return f"{label}: {msg or name}"


def _dedupe_hits(hits: Sequence[SearchHit]) -> List[SearchHit]:
    out: List[SearchHit] = []
    seen = set()
    for h in hits:
        key = (h.url or h.title).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def resolve_provider_order(config: Any) -> List[str]:
    """Return ordered provider names based on config + available keys."""
    pref = str(getattr(config, "web_search_provider", "auto") or "auto").strip().lower()
    tavily = bool(str(getattr(config, "tavily_api_key", "") or "").strip())
    brave = bool(str(getattr(config, "brave_search_api_key", "") or "").strip())

    if pref == "tavily":
        order = ["tavily", "duckduckgo"]
    elif pref == "brave":
        order = ["brave", "duckduckgo"]
    elif pref == "duckduckgo" or pref == "ddg":
        order = ["duckduckgo"]
    else:  # auto
        order = []
        if tavily:
            order.append("tavily")
        if brave:
            order.append("brave")
        order.append("duckduckgo")
    # Always ensure ddg last fallback unless exclusive
    if "duckduckgo" not in order:
        order.append("duckduckgo")
    return order


def run_web_search(
    query: str,
    *,
    config: Any = None,
    enrich_extract: bool = True,
    max_hits: int = 10,
) -> SearchProviderResult:
    """
    Provider cascade + free engineering upgrades.

    1) Build query variants
    2) Try providers in order until we have enough hits
    3) Optionally extract top URLs when snippets are thin (DDG extract)
    """
    if config is None:
        from config import config as config  # noqa: A001

    timeout = float(getattr(config, "web_search_timeout", 10) or 10)
    max_results = int(getattr(config, "tavily_max_results", 8) or 8)

    providers: Dict[str, Any] = {
        "duckduckgo": DuckDuckGoProvider(max_results=max_results, timeout_s=timeout),
        "tavily": TavilyProvider(
            api_key=str(getattr(config, "tavily_api_key", "") or ""),
            max_results=max_results,
            timeout_s=timeout,
            depth=str(getattr(config, "tavily_search_depth", "advanced") or "advanced"),
        ),
        "brave": BraveProvider(
            api_key=str(getattr(config, "brave_search_api_key", "") or ""),
            max_results=max_results,
            timeout_s=timeout,
        ),
    }

    order = resolve_provider_order(config)
    variants = build_query_variants(query, max_variants=3)
    all_hits: List[SearchHit] = []
    all_errors: List[str] = []
    queries_used: List[str] = []
    used_provider = ""

    for pname in order:
        prov = providers.get(pname)
        if prov is None:
            continue
        if pname in {"tavily", "brave"} and not getattr(prov, "available", False):
            continue
        for vq in variants:
            res = prov.search(vq, news=_is_newsish(vq))
            queries_used.extend(res.queries_used or [vq])
            all_errors.extend(res.errors or [])
            if res.hits:
                used_provider = used_provider or pname
                all_hits.extend(res.hits)
            if len(_dedupe_hits(all_hits)) >= max_hits:
                break
        if len(_dedupe_hits(all_hits)) >= max(4, max_hits // 2):
            # Good enough — stop cascading
            break
        # else try next provider

    hits = _dedupe_hits(all_hits)[:max_hits]

    # Thin-snippet extract upgrade (paid Firecrawl equivalent on free path)
    if enrich_extract and hits:
        ddg = providers["duckduckgo"]
        enriched = 0
        for h in hits[:4]:
            snip = (h.snippet or "") + (h.extract or "")
            needs = len(snip) < 120 or (
                _is_weatherish(query) and not re.search(r"\d+\s*°|\bhigh\b|\blow\b", snip, re.I)
            )
            if needs and h.url and hasattr(ddg, "extract_url"):
                text = ddg.extract_url(h.url)
                if text and len(text) > len(h.extract or ""):
                    h.extract = text[:3500]
                    enriched += 1
            if enriched >= 2:
                break

    return SearchProviderResult(
        hits=hits,
        provider=used_provider or (order[0] if order else "none"),
        errors=all_errors[:8],
        queries_used=list(dict.fromkeys(queries_used))[:8],
    )


def format_hits_for_tool(result: SearchProviderResult, *, multi_query: bool = False) -> str:
    if not result.hits:
        if result.errors:
            return result.errors[0]
        return "No search results found."
    blocks = []
    for i, h in enumerate(result.hits[:10], 1):
        lines = [f"{i}. {h.title or 'No title'}", f"   URL: {h.url}"]
        if multi_query and h.query:
            lines.append(f"   Query: {h.query}")
        if h.provider:
            lines.append(f"   Provider: {h.provider}")
        if h.date:
            lines.append(f"   Date: {h.date}")
        if h.snippet:
            sn = h.snippet if len(h.snippet) <= 280 else h.snippet[:280] + "..."
            lines.append(f"   Snippet: {sn}")
        if h.extract:
            ex = h.extract if len(h.extract) <= 900 else h.extract[:900] + "…"
            lines.append(f"   Extract: {ex}")
        blocks.append("\n".join(lines))
    header = f"Search provider: {result.provider}"
    if result.queries_used and len(result.queries_used) > 1:
        header += f" | variants: {len(result.queries_used)}"
    return header + "\n\n" + "\n\n".join(blocks)
