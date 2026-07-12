"""Tests for search deduplication Jaccard similarity upgrades."""

import pytest
from agent.research import _dedupe_queries, _query_token_set, _jaccard_similarity


def test_jaccard_similarity():
    """Verify Jaccard token-set similarity works correctly."""
    set_a = {"fifa", "matches", "today"}
    set_b = {"fifa", "games", "today"}
    
    # Intersection = {"fifa", "today"} (size 2), Union = {"fifa", "matches", "games", "today"} (size 4)
    # Sim = 2 / 4 = 0.5
    assert _jaccard_similarity(set_a, set_b) == 0.5
    
    set_c = {"fifa", "matches", "today", "schedule"}
    set_d = {"fifa", "matches", "today", "fixtures"}
    # Intersection = 3, Union = 5 -> Sim = 3 / 5 = 0.6
    assert _jaccard_similarity(set_c, set_d) == 0.6


def test_dedupe_queries_jaccard():
    """Verify deduplication removes near-duplicate queries using Jaccard threshold."""
    queries = [
        "FIFA matches today",
        "FIFA games today",          # near duplicate, should be filtered
        "FIFA schedule today",       # near duplicate, should be filtered
        "weather in Vancouver today", # distinct, should keep
    ]
    
    result = _dedupe_queries(queries)
    
    assert "FIFA matches today" in result
    assert "weather in Vancouver today" in result
    assert "FIFA games today" not in result
    assert "FIFA schedule today" not in result
    assert len(result) == 2
