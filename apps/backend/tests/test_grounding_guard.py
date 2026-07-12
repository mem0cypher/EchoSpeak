"""Tests for Bug 4: Post-Response Grounding Guard."""

import pytest
from agent.grounding_guard import extract_factual_claims, check_grounding, apply_grounding_guard


def test_extract_factual_claims():
    """Verify regex-based claim extraction for dates, scores, stats, and events."""
    text = (
        "The match was scheduled for July 15. "
        "They ended up winning 3-2 at 8pm. "
        "The player recorded 25 points, and they attended the training camp."
    )
    claims = extract_factual_claims(text)
    
    assert any("July 15" in c for c in claims)
    assert any("winning 3-2" in c or "3-2" in c for c in claims)
    assert any("at 8pm" in c for c in claims)
    assert any("25 points" in c for c in claims)
    assert any("training camp" in c for c in claims)


def test_check_grounding_success():
    """Verify that grounded claims check succeeds when facts exist in sources."""
    sources = [
        "The Lakers beat the Celtics 105-98 on October 24.",
        "AccuWeather forecast for Vancouver shows high of 22C."
    ]

    # Exactly matching claims
    res1 = check_grounding("The final score was 105-98.", sources)
    assert res1.is_grounded

    # Fuzzy matching claims
    res2 = check_grounding("It will be 22C in Vancouver on October 24.", sources)
    assert res2.is_grounded


def test_check_grounding_violation():
    """Verify that ungrounded claims are caught."""
    sources = [
        "The game is scheduled for Friday night.",
    ]

    # Hallucinated date and score not in sources
    res = check_grounding("The game is scheduled for July 15 and they will win 3-2.", sources)
    assert not res.is_grounded
    assert any("July 15" in c for c in res.ungrounded_claims)
    assert any("3-2" in c for c in res.ungrounded_claims)


def test_apply_grounding_guard():
    """Verify apply_grounding_guard appends caveat when violations are high."""
    sources = ["Friday night game."]
    
    # Under 3 ungrounded claims -> logs only, no modification
    clean = "They will win 3-2."
    assert apply_grounding_guard(clean, sources) == clean

    # 3 or more ungrounded claims -> appends warning caveat
    hallucinated = "The game is on July 15 at 8pm, final score 3-2."
    guarded = apply_grounding_guard(hallucinated, sources)
    assert guarded != hallucinated
    assert "accuracy" in guarded.lower() or "double-check" in guarded.lower()


def test_unsupported_event_drift_is_removed_from_weather_answer():
    sources = [
        "User asked: what's the weather in Calgary today?",
        "Weather result: Calgary high 24C, low 13C, partly cloudy.",
    ]
    response = "Calgary is partly cloudy today. We're talking about training camps, right?"
    guarded = apply_grounding_guard(response, sources)
    assert "training camp" not in guarded.lower()
    assert "partly cloudy" in guarded.lower()
