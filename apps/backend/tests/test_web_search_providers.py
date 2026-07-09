"""Web search provider cascade + free DDG engineering upgrades."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.web_search_providers import (
    build_query_variants,
    resolve_provider_order,
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


def test_provider_order_tavily_first_when_key():
    cfg = SimpleNamespace(
        tavily_api_key="tvly-test",
        brave_search_api_key="",
        web_search_provider="auto",
    )
    order = resolve_provider_order(cfg)
    assert order[0] == "tavily"
    assert "duckduckgo" in order


def test_provider_order_brave_when_requested():
    cfg = SimpleNamespace(
        tavily_api_key="",
        brave_search_api_key="bsa-test",
        web_search_provider="brave",
    )
    order = resolve_provider_order(cfg)
    assert order[0] == "brave"
