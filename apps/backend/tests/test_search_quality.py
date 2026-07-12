"""Tests for Bug 3: Conversational Framing Stripping & Fuzzy Typo Fixes."""

import pytest
from agent.research import apply_spelling_fixes, _prep_search_work_text


def test_apply_spelling_fixes_fuzzy():
    """Verify Levenshtein typo correction against high-frequency terms."""
    # Exact spelling fixes
    assert apply_spelling_fixes("weahter") == "weather"
    assert apply_spelling_fixes("schedul") == "schedule"
    assert apply_spelling_fixes("bitconi") == "bitcoin"
    
    # Fuzzy Levenshtein (edit distance <= 2)
    assert apply_spelling_fixes("fifaa") == "fifa"
    assert apply_spelling_fixes("championss") == "champions"
    assert apply_spelling_fixes("playofs") == "playoffs"
    assert apply_spelling_fixes("leagur") == "league"
    assert apply_spelling_fixes("weathrr") == "weather"

    # Common stop words should NOT be fuzzy corrected (edit distance matches shouldn't hijack them)
    assert apply_spelling_fixes("the") == "the"
    assert apply_spelling_fixes("get") == "get"


def test_prep_search_work_text_framing():
    """Verify conversational framing is stripped from search queries."""
    # Greetings / names
    assert _prep_search_work_text("hey echo when are the fifa matches tomorrow") == "when are the fifa matches tomorrow"
    assert _prep_search_work_text("echo can you check the weather forecast") == "weather forecast"

    # Social fillers
    assert _prep_search_work_text("so like when is the next match") == "when is the next match"
    assert _prep_search_work_text("I was wondering who won the playoffs") == "who won the playoffs"
    assert _prep_search_work_text("just curious who won the match") == "who won the match"
    assert _prep_search_work_text("real quick tell me the temperature") == "temperature"



def test_probability_query_reformulates_typo_and_keeps_domain():
    from agent.research import normalize_web_search_query, intent_domains

    query = normalize_web_search_query("chances edmonton oilers win the standly cup")
    low = query.lower()
    assert "standly" not in low
    assert "stanley" in low
    assert "edmonton" in low and "oilers" in low
    domains = intent_domains(query)
    assert "sports" in domains
    assert "odds" in domains


def test_deep_research_domain_is_genuine_multi_hop_only():
    from agent.research import intent_domains, is_deep_research_intent

    ordinary = "look up the oilers score tonight"
    deep = "deep research the evidence, compare sources, and trace the timeline for the battery recall root cause"
    assert not is_deep_research_intent(ordinary)
    assert "deep_research" not in intent_domains(ordinary)
    assert is_deep_research_intent(deep)
    assert "deep_research" in intent_domains(deep)
