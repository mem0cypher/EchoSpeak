"""Sports live-data path vs crawl search (category mismatch workstream)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.sports_data import (
    SportsDataClient,
    SportsLiveResult,
    is_live_sports_data_intent,
    live_sports_mode,
    infer_sport_key,
)


def test_live_intent_score_not_schedule():
    assert is_live_sports_data_intent("what's the oilers score right now") is True
    assert is_live_sports_data_intent("who won the lakers game") is True
    assert is_live_sports_data_intent("oilers moneyline odds") is True
    # Schedule / slate stays on web search
    assert is_live_sports_data_intent("who is playing tomorrow for fifa") is False
    assert is_live_sports_data_intent("when do the oilers play next") is False
    assert is_live_sports_data_intent("weather in edmonton") is False


def test_live_mode_classification():
    assert live_sports_mode("lakers moneyline") == "odds"
    assert live_sports_mode("oilers score right now") == "scores"
    assert live_sports_mode("nba standings") == "standings"
    assert live_sports_mode("who plays tomorrow") == "none"


def test_infer_sport_key():
    assert infer_sport_key("edmonton oilers score") == "icehockey_nhl"
    assert infer_sport_key("lakers vs celtics score") == "basketball_nba"


def test_missing_key_falls_back():
    client = SportsDataClient(api_key="")
    r = client.query("oilers score right now")
    assert r.ok is False
    assert r.fallback_to_web is True
    assert "ODDS_API_KEY" in r.error or "not configured" in r.error.lower() or "not set" in r.error.lower()
    text = r.as_tool_text()
    assert "SPORTS_LIVE" in text
    assert "ok=false" in text


def test_tool_text_success_shape():
    r = SportsLiveResult(
        ok=True,
        mode="scores",
        provider="the_odds_api",
        sport_key="icehockey_nhl",
        summary="Live/recent scores:\n- A @ B (final) — A: 2 | B: 1",
    )
    t = r.as_tool_text()
    assert "ok=true" in t
    assert "Do not invent" in t
    assert "2" in t
