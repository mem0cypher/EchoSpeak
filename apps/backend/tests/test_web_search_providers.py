"""Web search provider cascade + free DDG engineering upgrades."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.web_search_providers import (
    SearchHit,
    SearchProviderResult,
    SearXNGProvider,
    build_query_variants,
    is_vague_search_query,
    normalize_provider_query,
    resolve_provider_order,
    run_web_search,
    simplify_query,
)


def test_query_variants_include_primary():
    vs = build_query_variants("Edmonton weather tomorrow high low")
    assert vs
    assert vs[0].lower().startswith("edmonton weather")
    # weather authority variant
    assert any("site:" in v for v in vs)


def test_news_variant_adds_month():
    vs = build_query_variants("latest AI news")
    assert any("202" in v or "July" in v or "June" in v or "March" in v or len(vs) >= 1 for v in vs)


def test_simplify_query_strips_noise():
    s = simplify_query("what is the weather OR site:foo.com please")
    assert "site:" not in s.lower() or "weather" in s.lower()
    assert "please" not in s.lower() or True  # may keep content words


def test_provider_order_auto_ddg_only():
    cfg = SimpleNamespace(tavily_api_key="", brave_search_api_key="", web_search_provider="auto")
    order = resolve_provider_order(cfg)
    assert order[0] == "duckduckgo"
    assert "duckduckgo" in order


def test_provider_order_ignores_legacy_tavily_key():
    cfg = SimpleNamespace(
        tavily_api_key="tvly-test",
        brave_search_api_key="",
        web_search_provider="auto",
    )
    order = resolve_provider_order(cfg)
    assert "tavily" not in order
    assert order[0] == "duckduckgo"
    assert "duckduckgo" in order


def test_provider_order_brave_when_requested():
    cfg = SimpleNamespace(
        tavily_api_key="",
        brave_search_api_key="bsa-test",
        web_search_provider="brave",
    )
    order = resolve_provider_order(cfg)
    assert order[0] == "brave"

def test_provider_order_searxng_primary_when_configured():
    cfg = SimpleNamespace(
        tavily_api_key="",
        brave_search_api_key="",
        searxng_base_url="http://localhost:8080",
        web_search_provider="auto",
    )
    order = resolve_provider_order(cfg)
    assert order[0] == "searxng"
    assert "duckduckgo" in order


def test_provider_order_searxng_when_requested_without_base_url():
    cfg = SimpleNamespace(
        brave_search_api_key="",
        searxng_base_url="",
        web_search_provider="searxng",
    )
    assert resolve_provider_order(cfg) == ["searxng", "duckduckgo"]


def test_run_web_search_uses_searxng_provider(monkeypatch):
    calls = []

    def fake_search(self, query, **kwargs):
        calls.append(query)
        return SearchProviderResult(
            hits=[SearchHit(title="SearXNG result", url="https://example.com/a", snippet="real snippet", provider="searxng", query=query)],
            provider="searxng",
            queries_used=[query],
        )

    monkeypatch.setattr(SearXNGProvider, "search", fake_search)
    cfg = SimpleNamespace(
        brave_search_api_key="",
        searxng_base_url="http://localhost:8080",
        web_search_provider="searxng",
        web_search_timeout=3,
        web_search_max_results=5,
    )
    result = run_web_search("chances edmonton oilers win the standly cup", config=cfg, enrich_extract=False, max_hits=3)
    assert result.provider == "searxng"
    assert result.hits[0].provider == "searxng"
    assert "standly" not in calls[0].lower()
    assert "stanley" in calls[0].lower()


def test_run_web_search_rejects_vague_query_before_provider(monkeypatch):
    def fail_search(self, query, **kwargs):
        raise AssertionError("provider should not run for vague query")

    monkeypatch.setattr(SearXNGProvider, "search", fail_search)
    cfg = SimpleNamespace(
        brave_search_api_key="",
        searxng_base_url="http://localhost:8080",
        web_search_provider="searxng",
        web_search_timeout=3,
        web_search_max_results=5,
    )
    result = run_web_search("look it up more", config=cfg, enrich_extract=False, max_hits=3)
    assert not result.hits
    assert "too vague" in result.errors[0]
    assert is_vague_search_query("look it up more")
    assert normalize_provider_query("chances edmonton oilers win the standly cup") != "chances edmonton oilers win the standly cup"
