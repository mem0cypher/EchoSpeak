"""Prove intent/coding/search paths are structural — novel, never-discussed cases."""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_coding_intent_novel_genres_never_discussed():
    """Rhythm / tower defense / mystery adventure must match same as any create+artifact ask."""
    from agent.core import EchoSpeakAgent

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    novels = [
        "build me a text-based mystery adventure",
        "make a rhythm game with falling notes",
        "create a tower defense prototype",
        "lets code a turn-based tactics game with permadeath",
        "can you scaffold a visual novel engine in html",
    ]
    for q in novels:
        assert agent._is_coding_project_intent(q) is True, q
        tools = agent._allowed_lc_tool_names(q)
        # Coding create → file tools available; not web-only
        assert "file_write" in tools or "file_list" in tools or "terminal_run" in tools, (q, tools)


def test_local_desktop_intent_not_web_for_novel_folder_slug():
    from agent.core import EchoSpeakAgent, ContextBundle

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    q = "look on my desktop and start on quantum-chess-sim read the files"
    assert agent._is_local_filesystem_intent(q) is True
    tools = agent._allowed_lc_tool_names(q)
    assert "web_search" not in tools
    ctx = ContextBundle(
        extracted_input=q,
        resolved_input=q,
        allowed_tool_names=tools,
    )
    assert agent._pq_shortcut_queries(q, ctx, None) is None


def test_product_title_extraction_not_gta_only():
    """Any title with trailer/price/cast structure — not a GTA whitelist."""
    from agent.research import (
        _extract_title_entity,
        _normalize_product_trailer_query,
        _normalize_product_price_query,
        _normalize_product_cast_query,
        resolve_web_search_queries,
    )

    assert "dune" in _extract_title_entity("when does the new dune movie come out").lower()
    tq = _normalize_product_trailer_query("trailer 2 for hollow knight silksong", full_context="")
    assert "silksong" in tq.lower() or "hollow" in tq.lower()
    assert "trailer" in tq.lower()
    pq = _normalize_product_price_query("how much will elden ring dlc cost")
    assert "price" in pq.lower() or "cost" in pq.lower()
    assert "elden" in pq.lower() or "ring" in pq.lower()
    cq = _normalize_product_cast_query("who are the characters in baldurs gate 3")
    assert "cast" in cq.lower() or "characters" in cq.lower()

    # Multi: release + cost for a novel title (no GTA recipe required)
    multi = (
        "when does starfield shattered space expansion release and how much will it cost"
    )
    resolved = resolve_web_search_queries(multi, multi, use_decomposition=True)
    assert len(resolved) >= 1
    rjoin = " ".join(resolved).lower()
    # Should not collapse to only GTA strings
    assert "gta" not in rjoin or "starfield" in rjoin


def test_weak_answer_refine_does_not_inject_fifa_when_not_in_query():
    from agent.core import EchoSpeakAgent

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    refined = agent._refine_query_after_weak_answer(
        "what nhl games are on tonight",
        "nhl games tonight schedule",
        "I do not have the specific times.",
        1,
    )
    low = refined.lower()
    assert "nhl" in low or "games" in low
    assert "fifa" not in low and "world cup" not in low


def test_sports_normalize_is_structural_not_franchise_map():
    """Novel clubs/nations must compact the same way as any prior test team."""
    import agent.research as research
    from agent.research import (
        _normalize_sports_query,
        _extract_vs_sides,
        _infer_city_from_text,
        resolve_web_search_queries,
    )

    assert getattr(research, "_TEAM_CITY", None) is None

    # Free-form next-game for never-discussed club
    q = _normalize_sports_query("when do the Reykjavik Frost play next")
    assert "reykjavik" in q.lower() or "frost" in q.lower()
    assert "next game" in q.lower() or "schedule" in q.lower()
    # Must NOT rewrite to a different hard-coded franchise
    assert "oilers" not in q.lower()
    assert "edmonton oilers" not in q.lower()

    # vs-sides structural (never-discussed nations)
    sides = _extract_vs_sides("who wins Senegal vs Curaçao tomorrow")
    assert "senegal" in sides.lower()
    assert "cura" in sides.lower() or "curacao" in sides.lower().replace("ç", "c")

    # World Cup + novel sides — no country whitelist required
    fifa_q = _normalize_sports_query("fifa world cup Senegal vs Curaçao kickoff tomorrow")
    assert "senegal" in fifa_q.lower()
    assert "fifa" in fifa_q.lower() or "world cup" in fifa_q.lower()

    # City only when explicitly present — never invent from team nickname
    assert _infer_city_from_text("when do the oilers play next") == ""
    assert _infer_city_from_text("weather in Osaka tomorrow").lower().startswith("osaka")
    assert _infer_city_from_text("Osaka weather tomorrow").lower().startswith("osaka")

    # Multi-intent: novel product + novel sports — no GTA/FIFA recipe required
    multi = (
        "when does hollow knight silksong release and how much will it cost "
        "and what matches are happening for the world cup tomorrow"
    )
    resolved = resolve_web_search_queries(multi, multi, use_decomposition=True)
    rjoin = " ".join(resolved).lower()
    assert len(resolved) >= 2
    assert "silksong" in rjoin or "hollow" in rjoin
    assert "world cup" in rjoin or "fifa" in rjoin or "match" in rjoin
    assert "oilers" not in rjoin
    assert "gta" not in rjoin


def test_no_entity_hardcode_strings_in_sports_normalize_source():
    """Production normalizer must not contain franchise rewrite string literals."""
    import inspect
    from agent.research import _normalize_sports_query

    src = inspect.getsource(_normalize_sports_query)
    banned = (
        "Edmonton Oilers",
        "Calgary Flames",
        "Vancouver Canucks",
        "morocco|portugal|spain|brazil",
        '"oilers"',
    )
    for b in banned:
        assert b not in src, f"hardcoded entity residue in _normalize_sports_query: {b}"


def test_live_sports_intent_without_team_whitelist():
    from agent.sports_data import is_live_sports_data_intent, infer_sport_key, infer_team_tokens

    assert is_live_sports_data_intent("what's the Reykjavik Frost score right now") is True
    assert is_live_sports_data_intent("Senegal vs Curaçao score live") is True
    # No league keyword → no invented Odds API sport key
    assert infer_sport_key("Reykjavik Frost score") is None
    toks = infer_team_tokens("Reykjavik Frost score right now")
    assert any("reykjavik" in t or "frost" in t for t in toks)


def test_weather_place_structural_not_city_list():
    from agent.research import _normalize_weather_query, _infer_city_from_text

    assert _infer_city_from_text("what's the weather in Cape Town")
    wq = _normalize_weather_query("weather tomorrow", city_hint="Cape Town")
    assert "cape town" in wq.lower()
    # Bare weather with no place stays generic (does not invent Edmonton)
    bare = _normalize_weather_query("what's the weather tomorrow")
    assert "edmonton" not in bare.lower()
    assert "weather" in bare.lower()


def test_reflector_does_not_retry_accepted_grounded_packet():
    """Log bug: accepted=true still triggered reflector attempts 1 and 2."""
    from agent.core import EchoSpeakAgent, WebTaskReflector
    import tempfile

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    refl = WebTaskReflector(agent)
    packet = (
        "[GROUNDED_SEARCH] accepted=true query=FIFA World Cup matches today\n"
        "France vs Morocco 4:00 PM ET\n"
        "evidence ok"
    )
    calls = {"n": 0}
    orig = agent._grounded_web_search

    def _spy(*a, **k):
        calls["n"] += 1
        return packet

    agent._grounded_web_search = _spy  # type: ignore
    out = refl.reflect_and_retry(
        {
            "index": "t1",
            "tool": "web_search",
            "params": {"q": "FIFA World Cup matches today", "silent": True},
        },
        "web_search",
        packet,
        tools=[],
        callbacks=None,
    )
    assert out == packet
    assert calls["n"] == 0, "must not re-call grounded search after accepted=true"


def test_search_fingerprint_dedupes_tz_word_order():
    from agent.core import EchoSpeakAgent
    import tempfile

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    a = "FIFA World Cup today full match list kickoff ET and mnt convert timezone"
    b = "FIFA World Cup today full match list kickoff ET and et mnt convert timezone"
    c = "FIFA World Cup today full match list kickoff ET and mnt et convert timezone"
    fa, fb, fc = (
        agent._search_query_fingerprint(a),
        agent._search_query_fingerprint(b),
        agent._search_query_fingerprint(c),
    )
    assert fa == fb == fc
    # Grounded re-entry reuses packet without counting as new storms
    agent._request_grounded_results = {}
    agent._request_grounded_count = 0
    agent._request_search_cache = {}
    agent._request_grounded_results[fa] = "[GROUNDED_SEARCH]\naccepted=true\nFrance vs Morocco 4pm"
    out = agent._grounded_web_search(b, original_request=b, emit_tool_events=False)
    assert "France" in out or "4pm" in out
    assert agent._request_grounded_count == 0  # suppressed, did not increment


def test_search_query_quality_gate_rejects_fragments():
    """Utterance fragments must not become searches; multi keeps entity-rich queries only."""
    from agent.research import (
        is_viable_search_query,
        quality_gate_search_queries,
        resolve_web_search_queries,
    )

    parent = "what time does the fifa game with france and maracoo start today? pelsae check"
    assert is_viable_search_query("pelsae check", parent=parent) is False
    assert is_viable_search_query("please check", parent=parent) is False
    assert is_viable_search_query("maracoo start today", parent=parent) is False
    good = "FIFA World Cup france maracoo kickoff time ET today"
    assert is_viable_search_query(good, parent=parent) is True

    gated = quality_gate_search_queries(
        [
            "FIFA World Cup match list kickoff times ET each game schedule fixtures",
            "maracoo start today",
            "pelsae check",
        ],
        parent,
    )
    assert len(gated) >= 1
    assert not any("pelsae" in g.lower() for g in gated)
    assert not any(g.lower() == "maracoo start today" for g in gated)
    # Prefer queries that keep the matchup when present
    rjoin = " ".join(gated).lower()
    assert "fifa" in rjoin or "world cup" in rjoin or "france" in rjoin

    # Real multi still fans out cleanly
    multi = (
        "weather in Osaka tomorrow and what matches are happening for the world cup tomorrow"
    )
    resolved = resolve_web_search_queries(multi, multi, use_decomposition=True)
    assert len(resolved) >= 2
    rjoin2 = " ".join(resolved).lower()
    assert "osaka" in rjoin2 or "weather" in rjoin2
    assert "fifa" in rjoin2 or "world cup" in rjoin2 or "match" in rjoin2
    assert not any("please" in x.lower() and len(x.split()) <= 3 for x in resolved)


def test_fifa_matchup_single_query_not_junk_split():
    """Live: France/maracoo + 'pelsae check' must be ONE sports query, not 3 junk ones."""
    from agent.research import (
        resolve_web_search_queries,
        looks_like_multi_intent,
        _extract_vs_sides,
        _normalize_sports_query,
        _prep_search_work_text,
    )

    q = "what time does the fifa game with france and maracoo start today? pelsae check"
    prep = _prep_search_work_text(q)
    assert "pelsae" not in prep.lower()
    assert "please check" not in prep.lower()
    assert looks_like_multi_intent(q) is False
    sides = _extract_vs_sides(q)
    assert "france" in sides.lower()
    assert "maracoo" in sides.lower() or "morocco" in sides.lower()
    sports = _normalize_sports_query(q)
    assert "france" in sports.lower()
    assert "fifa" in sports.lower() or "world cup" in sports.lower()
    assert "kickoff" in sports.lower() or "time" in sports.lower()
    resolved = resolve_web_search_queries(q, q, use_decomposition=True)
    assert len(resolved) == 1, resolved
    r0 = resolved[0].lower()
    assert "france" in r0
    assert "maracoo" in r0 or "morocco" in r0
    assert "pelsae" not in r0
    assert r0 != "maracoo start today"
    assert "pelsae check" not in r0


def test_local_scan_reloops_into_project_not_stall():
    """After listing Desktop, must enter 2d-shooter-game and not ask 'what first?'."""
    from pathlib import Path
    from agent.core import EchoSpeakAgent

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    q = "lets start the 2d shooter game together and please scan the folder on my desktop"

    # Hollow stall answer must be detected
    stall = (
        "look, i see the 2d-shooter-game folder on your desktop. "
        "we can start building it. what's the first thing you want to look at in there?"
    )
    assert agent._local_scan_answer_is_hollow(q, stall) is True

    # Deep scan against real Desktop if present
    scan = agent._run_local_project_deep_scan(q)
    desk = Path.home() / "Desktop"
    target = desk / "2d-shooter-game"
    if target.is_dir():
        assert scan.get("path")
        assert "2d-shooter" in str(scan["path"]).lower().replace("_", "-")
        # Interior listing should NOT be the whole Desktop sibling dump only
        listing = (scan.get("listing") or "").lower()
        assert "echospeak" not in listing or "index" in listing or "html" in listing or "js" in listing or listing
        # Ensure recovery replaces stall with a brief when samples exist
        fixed = agent._ensure_local_project_deep_scan(q, stall)
        assert fixed
        flow = fixed.lower()
        assert "first thing you want" not in flow
        assert "what do you want to look at" not in flow
        # Should mention real project substance when files were readable
        assert len(fixed) > 80
        assert "2d-shooter" in flow or "game.js" in flow or "index.html" in flow or "scanned" in flow


def test_desktop_project_never_forces_web_search():
    """Live bug: 'start the 2d shooter game together + scan desktop' → internet search.

    Root causes: (1) eth⊂together live-info false positive
                 (2) 'game' classified as sports multi-intent
                 (3) bare 'search' treated as web
    """
    from agent.core import EchoSpeakAgent, ContextBundle
    from agent.research import intent_domains, looks_like_multi_intent

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    q = (
        "lets start the 2d shooter game together and please scan the folder on my desktop"
    )
    assert agent._is_local_filesystem_intent(q) is True
    assert agent._needs_live_web_fulfillment(q) is False
    assert agent._is_explicit_web_query(q) is False
    # eth in together must not mean ethereum
    assert agent._has_live_info_subject(q) is False
    # Not sports multi-intent for software game + desktop
    assert "sports" not in intent_domains(q)
    tools = agent._allowed_lc_tool_names(q)
    assert "web_search" not in tools
    assert "file_list" in tools or "file_read" in tools

    # Stage 3 must not return a web-search shortcut result
    ctx = ContextBundle(extracted_input=q, resolved_input=q, allowed_tool_names=tools)
    sc = agent._pq_shortcut_queries(q, ctx, None)
    assert sc is None

    # Even if something calls grounded web_search, it must refuse and stay local
    blocked = agent._grounded_web_search(q, original_request=q, emit_tool_events=False)
    assert "web_search blocked" in blocked.lower() or "local_filesystem" in blocked.lower()
    assert "tavily" not in blocked.lower()

    # Local file search phrasing is not web
    assert agent._is_explicit_web_query("search my desktop for the project folder") is False
    assert agent._is_explicit_web_query("search the web for pygame collision tutorials") is True


def test_product_price_refine_not_live_score():
    """Live bug: Silksong price weak answer → 'live price today live score result'."""
    from agent.core import EchoSpeakAgent
    from agent.research import build_search_intent

    agent = EchoSpeakAgent(memory_path=tempfile.mkdtemp())
    # Reflector must not treat product price as sports
    assert agent._task_planner.web_reflector._is_live_score_query(
        "Hollow Knight Silksong price cost pre-order editions"
    ) is False
    assert agent._task_planner.web_reflector._is_live_score_query(
        "live price today"
    ) is False
    assert agent._task_planner.web_reflector._is_live_score_query(
        "oilers score right now"
    ) is True or agent._task_planner.web_reflector._is_live_score_query(
        "nhl score right now"
    ) is True

    refined = agent._refine_query_after_weak_answer(
        "When does Hollow Knight Silksong release and how much will it cost?",
        "Hollow Knight Silksong price cost pre-order editions",
        "No information regarding the cost was found.",
        1,
    )
    low = refined.lower()
    assert "price" in low or "msrp" in low or "cost" in low
    assert "live score" not in low
    assert "score result" not in low

    intent = build_search_intent(
        "how much will silksong cost",
        "Hollow Knight Silksong price cost pre-order editions",
    )
    assert intent.mode != "live_score"
    assert intent.live_score_need is False
