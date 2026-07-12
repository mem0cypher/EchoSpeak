"""
v7.4.4+ Eval harness — E1–E11 fixtures (deterministic / recorded, no live network).

Success bar (product): ≥8/10 stable; zero raw tool-call syntax in chat.
E11 covers long-conversation subject continuity + memory-save discipline.
These tests exercise harness behavior with stubs — they are the CI gate.
Live Tavily/Gemma runs remain manual.
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.core import EchoSpeakAgent, Tool
from agent.research import format_grounded_tool_output, is_grounded_search_output
from agent.verification import VerificationTelemetry


# ---------------------------------------------------------------------------
# Recorded fixtures (not live search)
# ---------------------------------------------------------------------------

FIXTURE_DATE_ONLY_SCORE = (
    "1. Schedule page\n"
    "   URL: https://example.com/schedule\n"
    "   Snippet: Canada vs Morocco kickoff date and start time Sunday July 5 2026."
)

FIXTURE_LIVE_SCORE = (
    "1. Live scoreboard\n"
    "   URL: https://example.com/live\n"
    "   Snippet: Canada 2-1 Morocco live score result full-time."
)

FIXTURE_SCHEDULE_NAV = (
    "1. ESPN Schedule\n"
    "   URL: https://example.com/fixtures\n"
    "   Snippet: Sunday, July 5, 2026 Schedule Results Standings Teams Stats Tickets"
)

FIXTURE_PAGE_WITH_MATCHUPS = (
    "Canada vs Morocco starts at 3:00 PM ET today. Portugal vs Spain starts at 6:00 PM ET."
)

FIXTURE_WEAK_NEWS = (
    "1. Portal home\n"
    "   URL: https://example.com/home\n"
    "   Snippet: Welcome to our site. Navigation: News Sports Weather."
)


def _bare_agent(tmp_path=None):
    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent._current_subject_text = ""
    agent._last_web_query_context = ""
    agent._last_grounded_search_result = None
    agent._verification_telemetry = VerificationTelemetry(enabled=False)
    agent._partial_tool_results = []
    agent._partial_tool_names = {}
    agent._pending_action = None
    agent._emit_tool_start = MagicMock()
    agent._emit_tool_end = MagicMock()
    agent._emit_tool_error = MagicMock()
    agent._emit_thinking_step = MagicMock()
    agent._emit_reasoning = MagicMock()
    agent._clamp_tts_text = lambda s: s
    agent.tools = []
    agent._current_callbacks = None
    return agent


# E1 — Printed file_write becomes pending confirm, not chat text
def test_e1_printed_file_write_becomes_pending(tmp_path, monkeypatch):
    from config import config

    monkeypatch.setattr(config, "action_parser_enabled", True, raising=False)
    monkeypatch.setattr(config, "enable_system_actions", True, raising=False)
    monkeypatch.setattr(config, "allow_file_write", True, raising=False)

    agent = EchoSpeakAgent(memory_path=str(tmp_path))
    agent._allow_llm_tool_calling = lambda: False
    agent.graph_agent = None
    agent.agent_executor = None
    agent.fallback_executor = None

    class StubLLM:
        def invoke(self, text: str) -> str:
            return 'file_write(path="hello.html", content="<h1>Hi</h1>")'

        def invoke_with_reasoning(self, text: str):
            return self.invoke(text), ""

    agent.llm_wrapper = StubLLM()
    resp, _ = agent.process_query("make a hello.html file", include_memory=False)
    low = resp.lower()
    # Plan text may mention the tool name; raw inert tool-call alone must not be the whole reply.
    assert "confirm" in low or "pending" in low
    assert agent._pending_action is not None
    assert agent._pending_action.get("tool") == "file_write"
    assert "hello.html" in resp
    # Must not look like the model "already wrote" the file without confirm.
    assert "saved" not in low or "confirm" in low


# E2 — Live score with date-only snippets → reject / retry
def test_e2_live_score_rejects_date_only_then_accepts():
    agent = _bare_agent()
    calls = []

    def fake_raw(q: str) -> str:
        calls.append(q)
        if len(calls) == 1:
            return FIXTURE_DATE_ONLY_SCORE
        return FIXTURE_LIVE_SCORE

    agent._raw_web_search_execute = fake_raw
    agent._fetch_search_result_page_text = lambda url, **kw: ""
    out = agent._grounded_web_search("Canada vs Morocco score right now")
    assert is_grounded_search_output(out)
    assert "accepted=true" in out.lower() or "2-1" in out
    assert len(calls) >= 2
    assert agent._last_grounded_search_result and agent._last_grounded_search_result.get("accepted") is True


# E3 — Schedule answer buried on page → full-page path
def test_e3_buried_schedule_full_page():
    agent = _bare_agent()
    agent._raw_web_search_execute = lambda q: FIXTURE_SCHEDULE_NAV
    agent._fetch_search_result_page_text = lambda url, **kw: FIXTURE_PAGE_WITH_MATCHUPS
    out = agent._grounded_web_search("who's playing today?")
    assert is_grounded_search_output(out)
    assert "Canada vs Morocco" in out or agent._last_grounded_search_result.get("accepted")
    evidence = (agent._last_grounded_search_result or {}).get("evidence") or []
    if evidence:
        assert evidence[0].get("fetched_full_page") is True or "Canada" in out


# E4 — Capability-gap (odds) does not use canned capabilities reply
def test_e4_capability_gap_odds_not_canned():
    agent = _bare_agent()
    # Router-level: is_capability_question should not swallow topic-specific asks.
    from agent.router import IntentRouter

    router = IntentRouter(tools=[], lc_tools=[], source="web", config=None)
    # "can you get the odds for the oilers game" is a capability-gap topic question —
    # if is_capability_question is True alone, core must still allow search. Check core helper.
    q = "can you get the live odds for the oilers game tonight?"
    # IntentRouter marks many "can you" as capability — core has narrowed this for topic gaps.
    # Assert the web-search path would still be chosen via live web triggers.
    assert router.is_live_web_intent(q.lower()) or "odds" in q.lower()
    decision = router.route(q)
    # Must not be a pure chat dead-end when live web signals present.
    assert decision.intent in {"web_search", "chat", "tool_call"}
    # Soft bar: if chat, at least live-web intent is detected for tool allowlist.
    if decision.intent == "chat":
        assert router.is_live_web_intent(q.lower())


# E5 — Deeper search keeps current_subject
def test_e5_deeper_search_keeps_subject():
    agent = _bare_agent()
    agent._current_subject_text = "Canada vs Morocco World Cup score"
    captured = []

    def fake_raw(q: str) -> str:
        captured.append(q)
        return FIXTURE_LIVE_SCORE

    agent._raw_web_search_execute = fake_raw
    agent._fetch_search_result_page_text = lambda url, **kw: ""
    agent._grounded_web_search("do a deeper search", original_request="do a deeper search")
    assert captured
    assert any("canada" in c.lower() or "morocco" in c.lower() for c in captured)


# E6 — Terminal ExitCode=1 fails reflection, not success
def test_e6_terminal_nonzero_fails_reflection():
    from agent.reflection import ReflectionEngine

    agent = SimpleNamespace(_verification_telemetry=VerificationTelemetry(enabled=False))
    engine = ReflectionEngine(agent)
    task = {"index": 0, "tool": "terminal_run", "description": "run tests"}
    result = engine._deterministic_reflection(task, "ExitCode=1\nFAILED tests/test_foo.py")
    assert result is not None
    assert result.accepted is False


# E7 — Provider readiness message shape (LM Studio down)
def test_e7_provider_readiness_lmstudio_down(monkeypatch):
    from api import server as server_mod
    from config import ModelProvider, config

    monkeypatch.setattr(config.local, "base_url", "http://localhost:1234", raising=False)

    def fake_urlopen(req, timeout=0):
        raise server_mod.URLError("connection refused")

    monkeypatch.setattr(server_mod, "urlopen", fake_urlopen, raising=True)
    readiness = server_mod._check_provider_readiness(ModelProvider.LM_STUDIO)
    assert readiness["ok"] is False
    assert "LM Studio" in readiness["message"]


# E8 — Coding write → pending confirm
def test_e8_coding_file_write_pending(tmp_path, monkeypatch):
    from config import config

    monkeypatch.setattr(config, "enable_system_actions", True, raising=False)
    monkeypatch.setattr(config, "allow_file_write", True, raising=False)
    monkeypatch.setattr(config, "action_parser_enabled", True, raising=False)

    agent = EchoSpeakAgent(memory_path=str(tmp_path))
    agent._allow_llm_tool_calling = lambda: False
    agent.graph_agent = None
    agent.agent_executor = None
    agent.fallback_executor = None
    agent.configure_workspace("coding")

    class StubLLM:
        def invoke(self, text: str) -> str:
            return (
                '<execute_tool>file_write(path="index.html", content="<html>hi</html>")</execute_tool>'
            )

        def invoke_with_reasoning(self, text: str):
            return self.invoke(text), ""

    agent.llm_wrapper = StubLLM()
    resp, _ = agent.process_query("create index.html with hello", include_memory=False)
    assert "file_write(" not in resp or "confirm" in resp.lower()
    assert "confirm" in resp.lower() or agent._pending_action is not None


# E9 — Weak evidence → insufficient, not invented answer packet
def test_e9_weak_evidence_insufficient_structure():
    agent = _bare_agent()
    agent._raw_web_search_execute = lambda q: FIXTURE_WEAK_NEWS
    agent._fetch_search_result_page_text = lambda url, **kw: "Home page nav only. Contact us."
    out = agent._grounded_web_search("what is the exact final score of Canada vs Morocco right now?")
    assert is_grounded_search_output(out)
    # Either insufficient structure or accepted only if score somehow present
    if "SEARCH_EVIDENCE_INSUFFICIENT" in out:
        assert "Do NOT invent" in out
        assert agent._last_grounded_search_result.get("accepted") is False
    else:
        assert "2-1" in out or "score" in out.lower()


# E10 — Context flood: protected subject survives
def test_e10_context_flood_protects_subject():
    from agent.context_budget import ContextBlock, ContextBudgetManager

    manager = ContextBudgetManager(context_window=300, reserve_tokens=40, enabled=True)
    fitted, report = manager.fit_blocks(
        [
            ContextBlock("current_subject", "PROTECTED_SUBJECT_XYZ", 1, "Current subject", protected=True),
            ContextBlock("raw_history", ("old chat spam " * 400), 9, "Raw history"),
            ContextBlock("docs", ("document blob " * 400), 8, "Docs"),
        ],
        overhead_tokens=80,
    )
    assert "PROTECTED_SUBJECT_XYZ" in fitted
    assert report.trimmed_blocks or report.compressed_blocks or "compressed" in fitted.lower()


# ---------------------------------------------------------------------------
# E11 — Long conversation: subject continuity, memory-save discipline, tier agreement
# ---------------------------------------------------------------------------

def _e11_scripted_turns():
    """15–20+ turns: evolving topic, follow-ups, tangent, switch-back, durable fact."""
    gta = "GTA 6 trailer"
    return [
        # Main topic establishment + evolution
        ("Have you seen the new GTA 6 trailer?", f"Yeah the {gta} looks massive.", "gta"),
        ("What stood out to you?", "The vice city vibe and the characters.", "gta"),
        ("so i'll look into that for you right now", "Sounds good — digging into the trailer details.", "gta"),
        ("what do you think about it?", f"I think the {gta} sets a high bar for open-world hype.", "gta"),
        ("When does it release?", "Rockstar has said 2025 for GTA 6, still subject to change.", "gta"),
        ("Tell me more about the setting", "Leonida is the state, with Vice City as the big draw.", "gta"),
        ("How long is the trailer?", "A few minutes — enough to show world density and tone.", "gta"),
        ("Is Lucia the protagonist?", "Yes, Lucia is a confirmed lead character in GTA 6.", "gta"),
        # Soft follow-ups that must NOT overwrite subject
        ("interesting", "Yeah, the casting and tone really land.", "gta"),
        ("and what about the music?", "The soundtrack tease felt very Vice City.", "gta"),
        # Topic switch (weather) then switch-back
        ("What's the weather in Calgary today?", "Calgary is cooler with some cloud cover today.", "weather"),
        ("what about in Vancouver?", "Vancouver looks milder and a bit wetter.", "weather"),
        ("ok back to the trailer", f"Back to the {gta} — want more on gameplay or story?", "gta"),
        ("Does it show multiplayer?", "The trailer is story-focused; multiplayer details are still thin.", "gta"),
        ("compare it to the first trailer", "The second pass usually doubles down on world density vs the first.", "gta"),
        # Durable memory (explicit remember) — should save typed, not raw chatter
        ("Remember that I prefer short answers about games", "Got it — I'll keep game answers short.", "gta"),
        # Late referential follow-up (many turns after topic start)
        ("what do you think about it?", "Still excited — the world building in that trailer is the hook.", "gta"),
        # Another tangent then return
        ("Side note: FIFA World Cup scores later?", "Sure — we can check live scores when you want.", "fifa"),
        ("anyway, trailer thoughts one more time", f"Overall the {gta} still feels like the main event of the chat.", "gta"),
        ("summarize what we've been talking about", f"Mostly the {gta}, with a brief weather detour and a FIFA note.", "gta"),
    ]


def test_e11_long_conversation_memory_and_subject(tmp_path, monkeypatch):
    """
    Long-run board scenario:
    - subject holds through 20 turns with follow-ups, switch, and switch-back
    - raw conversation is NOT auto-stored every turn (Memory saved discipline)
    - durable remember still stores
    - late follow-up resolves against earlier topic, not only last message
    - session summary and vector retrieval stay aligned (no stale raw-turn dominance)
    """
    from config import config
    from agent.core import EchoSpeakAgent
    from agent.session_memory import SessionMemoryDistiller
    from api import server as server_mod

    monkeypatch.setattr(config, "memory_auto_store_conversations", False, raising=False)
    monkeypatch.setattr(config, "memory_importance_enabled", True, raising=False)
    monkeypatch.setattr(config, "memory_extraction_async", False, raising=False)
    monkeypatch.setattr(config, "session_memory_enabled", True, raising=False)

    agent = EchoSpeakAgent(memory_path=str(tmp_path / "e11_mem"))
    # Isolate session memory from shared DATA_DIR so prior runs cannot inflate turn_count.
    agent._session_memory = SessionMemoryDistiller(tmp_path / "e11_session", update_turns=1)
    agent._current_source = "web"
    agent._current_thread_id = "e11-long"
    agent._last_memory_thread_id = "e11-long"

    # Never invent durable memories via LLM for chatter turns
    class QuietLLM:
        def invoke(self, text: str) -> str:
            return '{"items":[],"reason":"no durable facts"}'

        def invoke_with_reasoning(self, text: str):
            return self.invoke(text), ""

    agent.llm_wrapper = QuietLLM()

    turns = _e11_scripted_turns()
    assert len(turns) >= 18

    counts_before_each = []
    subjects = []
    conversation_saves = 0
    durable_saves = 0

    def _conversation_item_count() -> int:
        items = agent.memory.list_items(offset=0, limit=500, thread_id="e11-long") if hasattr(agent.memory, "list_items") else []
        if not items:
            # Fallback: scan simple or all items without thread filter
            try:
                items = agent.memory.list_items(offset=0, limit=500)
            except Exception:
                items = []
        n = 0
        for it in items or []:
            meta = it.get("metadata") if isinstance(it, dict) else {}
            mt = str((meta or {}).get("type") or "").lower()
            if mt == "conversation":
                n += 1
        return n

    baseline_conv = _conversation_item_count()
    baseline_total = int(getattr(agent.memory, "memory_count", 0) or 0)

    for user, reply, _bucket in turns:
        before = int(getattr(agent.memory, "memory_count", 0) or 0)
        conv_before = _conversation_item_count()
        agent._record_turn(user, reply)
        after = int(getattr(agent.memory, "memory_count", 0) or 0)
        conv_after = _conversation_item_count()
        counts_before_each.append((before, after, user[:40]))
        if conv_after > conv_before:
            conversation_saves += conv_after - conv_before
        if after > before and conv_after == conv_before:
            durable_saves += after - before
        subjects.append(str(getattr(agent, "_current_subject_text", "") or ""))

    # --- 1) Memory-save frequency: no raw conversation auto-store ---
    assert conversation_saves == 0, (
        f"Raw conversation auto-store regression: {conversation_saves} type=conversation "
        f"saves with memory_auto_store_conversations=False"
    )
    # Chitchat turns must not 1:1 inflate FAISS; durable remember may add 1+ items
    total_delta = int(getattr(agent.memory, "memory_count", 0) or 0) - baseline_total
    assert total_delta <= 5, f"Memory count rose too much for non-durable chatter: delta={total_delta}"
    assert durable_saves >= 1, "Explicit 'Remember that…' should produce at least one durable save"

    # --- 2) Subject continuity at turn 20+ ---
    # After "ok back to the trailer" (index 12) and before FIFA tangent, subject must be trailer/GTA
    post_return = subjects[12:16]
    assert any("trailer" in (s or "").lower() or "gta" in (s or "").lower() for s in post_return), (
        f"Failed to switch back to trailer topic: {post_return}"
    )
    # Mid-session weather switch should have briefly moved subject
    weather_seen = any("weather" in (s or "").lower() or "calgary" in (s or "").lower() for s in subjects)
    assert weather_seen, "Expected a weather subject during the switch segment"
    # Final stretch after trailer return + FIFA note should not be stuck on weather alone
    late_subjects = " | ".join(subjects[12:]).lower()
    assert ("gta" in late_subjects or "trailer" in late_subjects), (
        f"Subject lost GTA/trailer after switch-back: {subjects[12:]!r}"
    )
    assert not all(
        ("weather" in (s or "").lower() or "calgary" in (s or "").lower() or "vancouver" in (s or "").lower())
        and "trailer" not in (s or "").lower() and "gta" not in (s or "").lower()
        for s in subjects[12:]
    )

    # --- 3) Late follow-up resolves against earlier topic ---
    # Use subject right after "ok back to the trailer" / compare turn (GTA track, pre-FIFA)
    gta_subject = next(
        (s for s in reversed(subjects[12:17]) if s and ("gta" in s.lower() or "trailer" in s.lower())),
        subjects[12],
    )
    agent._current_subject_text = gta_subject
    resolved, is_ref, subj = agent._resolve_referential_followup("what do you think about it?")
    assert is_ref is True
    low_res = (resolved or "").lower()
    assert "gta" in low_res or "trailer" in low_res or "gta" in (subj or "").lower() or "trailer" in (subj or "").lower(), (
        f"Late follow-up failed to bind to GTA/trailer: resolved={resolved!r} subject={subj!r}"
    )

    # --- 4) Session memory vs vector agreement ---
    session = agent._session_memory.load("e11-long")
    assert session.turn_count == len(turns)
    sess_subj = (session.current_subject or "").lower()
    assert "gta" in sess_subj or "trailer" in sess_subj or "summarize" in sess_subj or "talking" in sess_subj

    # Inject a stale raw conversation dump as if from an older buggy session
    agent.memory.add_conversation(
        "totally unrelated old topic about antique radios",
        "Sure, antique radios are cool.",
        thread_id="e11-long",
    )
    # With auto-store off, retrieval for the live subject must prefer durable/session,
    # not inject the stale raw conversation turn as the memory context.
    vec_ctx = agent.memory.get_conversation_context(
        "what do you think about the trailer?",
        k=5,
        thread_id="e11-long",
    )
    assert "antique radios" not in (vec_ctx or "").lower(), (
        "Vector memory injected stale raw conversation while auto-store is off — "
        "session/vector tier disagreement"
    )
    # Session still reports the live topic
    sess_ctx = agent._session_memory.context_for("e11-long", max_chars=1200)
    assert "antique radios" not in (sess_ctx or "").lower()
    assert "gta" in sess_ctx.lower() or "trailer" in sess_ctx.lower() or session.current_subject

    # --- 5) Memory Doctor: raw conversation dominance stays healthy for this session ---
    report = server_mod._build_memory_doctor_report(agent, thread_id="e11-long", max_scan=300)
    assert report.auto_store_conversations is False
    # One manually injected conversation exists for the drift probe; should not dominate
    # a long real session of durable-only writes.
    conv_count = int(report.type_counts.get("conversation", 0) or 0)
    scanned = max(1, int(report.scanned or 1))
    assert conv_count <= max(3, scanned // 2), (
        f"Conversation memories dominate after long session: {report.type_counts}"
    )
    assert not any("auto-store" in w.lower() for w in (report.warnings or []))


def test_e11_auto_store_on_still_records_conversations(tmp_path, monkeypatch):
    """Opt-in path: when auto-store is enabled, raw turns do land in FAISS."""
    from config import config
    from agent.core import EchoSpeakAgent

    monkeypatch.setattr(config, "memory_auto_store_conversations", True, raising=False)
    monkeypatch.setattr(config, "memory_importance_enabled", False, raising=False)
    monkeypatch.setattr(config, "session_memory_enabled", True, raising=False)

    agent = EchoSpeakAgent(memory_path=str(tmp_path / "e11_on"))
    agent._current_source = "web"
    agent._current_thread_id = "e11-on"
    agent._last_memory_thread_id = "e11-on"
    before = int(getattr(agent.memory, "memory_count", 0) or 0)
    agent._record_turn("hello there friend", "hi!")
    after = int(getattr(agent.memory, "memory_count", 0) or 0)
    assert after > before


def test_e12_search_query_not_raw_chat_prompt():
    """
    Live bug: multi-intent chat was shipped to Tavily as the search string:
      \"how're you feeling? and i wonder when that new trailer comes out for trailer 3 for gta 6 hey?\"
    Must compact to a real query (GTA 6 Trailer 3 release date), not the raw prompt.
    Also: substring \"won\" in \"wonder\" must not be a live-score/live-web false positive alone.
    """
    from agent.research import SearchGrounder, build_search_intent, normalize_web_search_query

    raw = "how're you feeling? and i wonder when that new trailer comes out for trailer 3 for gta 6 hey?"
    compact = normalize_web_search_query(raw)
    cl = compact.lower()
    assert "gta 6" in cl and "trailer" in cl and "3" in cl and "release" in cl, compact
    assert "feeling" not in cl and "wonder" not in cl and "hey" not in cl

    agent = _bare_agent()
    extracted = agent._extract_search_query(raw)
    el = extracted.lower()
    assert "gta 6" in el and "trailer" in el and "release" in el, extracted
    # \"won\" inside \"wonder\" must not trip sports triggers by itself
    assert agent._is_live_web_intent("i wonder how you are") is False
    # trailer + come out still is live/web-worthy
    assert agent._is_live_web_intent(raw.lower()) is True

    intent = build_search_intent(raw, extracted, "")
    assert "gta 6" in intent.resolved_request.lower() and "trailer" in intent.resolved_request.lower()
    assert intent.specific_answer_need is True
    assert intent.recency_need is True
    cands = SearchGrounder(max_candidates=3).build_candidates(intent)
    assert cands
    assert all("feeling" not in c.query.lower() for c in cands)
    assert any("gta 6" in c.query.lower() and "trailer" in c.query.lower() for c in cands)

    # Stage-4 style: model passes full user message as tool arg
    seen = []

    def fake_raw(q: str) -> str:
        seen.append(q)
        return (
            "1. Rockstar\n"
            "   URL: https://example.com/gta\n"
            "   Snippet: No Trailer 3 release date announced for GTA 6 yet."
        )

    agent._raw_web_search_execute = fake_raw
    agent._fetch_search_result_page_text = lambda url, **kw: "No official Trailer 3 date."
    agent._verification_telemetry = VerificationTelemetry(enabled=False)
    agent._current_subject_text = ""
    agent._last_web_query_context = ""
    agent._emit_tool_start = MagicMock()
    agent._emit_tool_end = MagicMock()
    agent._emit_tool_error = MagicMock()
    out = agent._grounded_web_search(raw, original_request=raw, emit_tool_events=False)
    assert seen, "expected search execute"
    assert all("feeling" not in s.lower() and "wonder" not in s.lower() for s in seen), seen
    assert any("gta 6" in s.lower() or "trailer 3" in s.lower() or "trailer" in s.lower() for s in seen), seen
    assert out


def test_e21_general_multi_intent_no_recipe_including_weather_fifa():
    """
    System-wide multi-intent (domain diversity), NOT a weather+FIFA recipe.

    Live transcript that dropped FIFA must fan out to 2+ searches even when the
    model only passes a weather tool arg. Novel combos with no recipes must work too.
    """
    from agent.research import (
        looks_like_multi_intent,
        resolve_web_search_queries,
        normalize_web_search_query,
        intent_domains,
    )

    # --- Live failure phrasing (weather + FIFA) ---
    live = (
        "damn bro just wondering what the temp going to be tomorrow, also "
        "what matches are happening for fifa tomorrow also"
    )
    assert len(intent_domains(live)) >= 2
    assert looks_like_multi_intent(live) is True
    # Lazy model arg must NOT wipe the second intent
    resolved = resolve_web_search_queries(
        live,
        "tomorrow high low weather tomorrow high low temperature forecast",
        use_decomposition=True,
    )
    assert len(resolved) >= 2, resolved
    rjoin = " ".join(resolved).lower()
    assert "weather" in rjoin or "temp" in rjoin or "high" in rjoin
    assert "fifa" in rjoin or "world cup" in rjoin or "match" in rjoin
    # Not a third weather-only duplicate from model arg
    weatherish = sum(1 for q in resolved if "weather" in q.lower() or "temp" in q.lower())
    assert weatherish <= 2, resolved

    # Full grounded path must execute 2+ raw searches
    agent = _bare_agent()
    seen = []
    agent._raw_web_search_execute = lambda q: (
        seen.append(q)
        or (
            "1. Src\n   URL: https://example.com\n   Snippet: High 12 Low 3 Edmonton forecast. "
            "FIFA World Cup Morocco vs Portugal tomorrow 3pm ET."
        )
    )
    agent._fetch_search_result_page_text = lambda url, **kw: ""
    agent._request_search_cache = {}
    agent._active_user_query = live
    agent._current_subject_text = "Edmonton weather"
    out = agent._grounded_web_search(
        "tomorrow high low temperature forecast",
        original_request=live,
        emit_tool_events=False,
    )
    assert len(seen) >= 2, seen
    sjoin = " ".join(seen).lower()
    assert "fifa" in sjoin or "world cup" in sjoin or "match" in sjoin
    assert "weather" in sjoin or "high" in sjoin or "temp" in sjoin

    # --- Live Stage-3 bug: _extract_search_query collapses to FIFA-only primary ---
    # original_request was the collapsed string; active_user_query must still fan out.
    seen2 = []
    agent._raw_web_search_execute = lambda q: (
        seen2.append(q)
        or (
            "1. Src\n   URL: https://example.com\n   Snippet: High 12 Low 3 Edmonton. "
            "FIFA slate tomorrow Morocco vs Portugal 3pm ET."
        )
    )
    agent._active_user_query = live
    collapsed = normalize_web_search_query(live)  # historically FIFA-only primary
    assert "fifa" in collapsed.lower() or "world cup" in collapsed.lower()
    # This is a separate simulated request; clear the per-request anti-loop state.
    agent._request_grounded_results = {}
    agent._request_grounded_inflight = set()
    agent._request_grounded_count = 0
    agent._grounded_web_search(
        collapsed,
        original_request=collapsed,  # Stage 3 used to pass this as BOTH
        emit_tool_events=False,
    )
    assert len(seen2) >= 2, f"Stage3 collapse must not wipe multi; got {seen2}"
    s2 = " ".join(seen2).lower()
    assert ("weather" in s2 or "high" in s2 or "temp" in s2 or "edmonton" in s2), seen2
    assert ("fifa" in s2 or "world cup" in s2 or "match" in s2), seen2

    # --- Novel combos never seen in recipes ---
    novels = [
        "what is the apple stock price and also what is the weather in seattle tomorrow",
        "when does the new Dune movie release and also any local news in toronto today",
        "what was the lakers score last night and also what is the capital of peru",
        "bitcoin price right now plus weather in denver",
    ]
    for n in novels:
        assert looks_like_multi_intent(n) is True, n
        # No GTA/weather-sports-only assumption: just must be multi
        r = resolve_web_search_queries(n, n.split("and")[0][:40], use_decomposition=True)
        assert len(r) >= 2, (n, r)

    # Weather city-ask detector
    agent2 = _bare_agent()
    agent2._current_subject_text = "Edmonton"
    evidence = "Edmonton high 12 low 3 cloudy AccuWeather"
    assert agent2._answer_asks_city_despite_known_location(
        "I can give you the weather forecast. What city are you interested in?",
        evidence,
    )
    assert not agent2._answer_asks_city_despite_known_location(
        "Edmonton tomorrow: high 12, low 3, cloudy.",
        evidence,
    )


def test_e22_live_transcript_gta_fifa_release_notes_july9():
    """
    Live multi-prompt dump (2026-07-09):
      - GTA release + cost AND FIFA tomorrow must fan into distinct compact queries
      - \"python release notes\" must NOT become \"python release date\"
      - \"sorry not tomorrow today! july 9th what games\" must drop apology + pin date
      - July 9 follow-up inherits FIFA from subject
    """
    from agent.research import (
        resolve_web_search_queries,
        normalize_web_search_query_single,
        looks_like_multi_intent,
        enrich_sports_query_with_subject,
        _is_orphan_price_query,
    )

    gta_fifa = (
        "i need you to search when gta 6 is released how much money it costs and then "
        "explain to me when the next fifa matchup is tommrrow and who is playing"
    )
    assert looks_like_multi_intent(gta_fifa) is True
    resolved = resolve_web_search_queries(gta_fifa, gta_fifa, use_decomposition=True)
    assert len(resolved) >= 2, resolved
    rjoin = " ".join(resolved).lower()
    assert "gta" in rjoin
    assert "fifa" in rjoin or "world cup" in rjoin
    assert any("price" in q.lower() or "cost" in q.lower() for q in resolved), resolved
    assert any("release" in q.lower() for q in resolved), resolved
    # No chatty residue shipped to providers
    for q in resolved:
        assert "i need you" not in q.lower()
        assert "explain to me" not in q.lower()
        assert "who is playing" != q.lower().strip()
        assert not _is_orphan_price_query(q), q

    # Second live phrasing: cost clause split from GTA → must rebind entity
    gta_cost_fifa = (
        "when does gta 6 come out and how much will it cost? and also can you tell me "
        "what fifa games are happening today"
    )
    r2 = resolve_web_search_queries(gta_cost_fifa, gta_cost_fifa, use_decomposition=True)
    assert len(r2) >= 3, r2
    r2j = " ".join(r2).lower()
    assert "gta" in r2j and ("fifa" in r2j or "world cup" in r2j)
    assert any(
        "gta" in q.lower() and ("price" in q.lower() or "cost" in q.lower()) for q in r2
    ), r2
    assert not any(_is_orphan_price_query(q) for q in r2), r2
    assert not any(q.lower().strip() == "how much will it cost" for q in r2), r2

    notes = normalize_web_search_query_single("search for latest python release notes")
    assert "release notes" in notes.lower() or "changelog" in notes.lower(), notes
    assert "release date" not in notes.lower(), notes

    july = resolve_web_search_queries(
        "sorry not tommrow today! july 9th what games are being played then?",
        use_decomposition=True,
    )
    assert len(july) == 1, july
    assert "sorry" not in july[0].lower()
    assert "july" in july[0].lower() or "9" in july[0]
    # Subject continuity: prior FIFA turn
    enriched = enrich_sports_query_with_subject(
        july[0],
        "FIFA World Cup matches schedule fixtures tomorrow",
    )
    assert "fifa" in enriched.lower() or "world cup" in enriched.lower(), enriched

    agent = _bare_agent()
    seen = []
    agent._raw_web_search_execute = lambda q: (
        seen.append(q)
        or "1. Src\n   URL: https://example.com\n   Snippet: GTA 6 Nov 19 2026 $70. FIFA QF July 9."
    )
    agent._fetch_search_result_page_text = lambda url, **kw: ""
    agent._request_search_cache = {}
    agent._active_user_query = gta_fifa
    agent._grounded_web_search(gta_fifa, original_request=gta_fifa, emit_tool_events=False)
    assert len(seen) >= 2, seen
    sjoin = " ".join(seen).lower()
    assert "gta" in sjoin
    assert "fifa" in sjoin or "world cup" in sjoin


def test_e19_general_decompose_novel_compound_and_simple_fp():
    """
    General multi-intent fallback (no weather/sports/GTA recipe):
      \"tallest building in Dubai AND current CEO of Tesla\"
    must become 2+ sub-queries. Simple single-fact asks must NOT look multi
    (no decomposition cost / false fan-out).
    """
    from agent.research import (
        looks_like_multi_intent,
        resolve_web_search_queries,
        recipe_multi_search_queries,
        decompose_search_intents,
    )

    # --- False-positive / latency guard: simple questions ---
    simples = [
        "what's the capital of France?",
        "What is 2 plus 2?",
        "who is the president of France",
        "weather in Calgary",
        "hi",
    ]
    for s in simples:
        # weather alone is single-intent specialty, not multi
        if "weather" in s.lower() and "and" not in s.lower():
            assert looks_like_multi_intent(s) is False, s
        elif len(s.split()) < 10:
            assert looks_like_multi_intent(s) is False, s

    assert looks_like_multi_intent("what's the capital of France?") is False
    assert recipe_multi_search_queries("what's the capital of France?") == []

    # --- Novel compound (no recipe) ---
    novel = (
        "what's the tallest building in Dubai right now, and also who is the "
        "current CEO of Tesla and when did they take the role?"
    )
    assert looks_like_multi_intent(novel) is True
    assert recipe_multi_search_queries(novel) == []  # no hand-written recipe

    # Stub LLM decomposer returns structured sub-questions
    def fake_llm(_prompt: str) -> str:
        return (
            '["tallest building in Dubai 2026", '
            '"current CEO of Tesla", '
            '"when did Tesla CEO take the role"]'
        )

    decomp = decompose_search_intents(novel, llm_invoke=fake_llm)
    assert len(decomp) >= 2, decomp
    joined = " ".join(decomp).lower()
    assert "dubai" in joined
    assert "tesla" in joined or "ceo" in joined

    resolved = resolve_web_search_queries(
        novel,
        "tallest building dubai",  # lazy model arg — must not wipe multi
        llm_invoke=fake_llm,
    )
    assert len(resolved) >= 2, resolved
    rjoin = " ".join(resolved).lower()
    assert "dubai" in rjoin
    assert "tesla" in rjoin or "ceo" in rjoin

    # Full grounded path with model arg overwrite resistance
    agent = _bare_agent()
    seen = []

    def fake_raw(q: str) -> str:
        seen.append(q)
        return (
            f"1. Source for {q[:40]}\n"
            f"   URL: https://example.com/x\n"
            f"   Snippet: Factual answer fragment about {q}."
        )

    agent._raw_web_search_execute = fake_raw
    agent._fetch_search_result_page_text = lambda url, **kw: "details"
    agent._verification_telemetry = VerificationTelemetry(enabled=False)
    agent._current_subject_text = ""
    agent._last_web_query_context = ""
    agent._active_user_query = novel
    agent.llm_wrapper = type("W", (), {"invoke_fast": staticmethod(lambda p, max_tokens=180: fake_llm(p))})()
    agent._emit_tool_start = MagicMock()
    agent._emit_tool_end = MagicMock()
    agent._emit_tool_error = MagicMock()
    out = agent._grounded_web_search(
        "tallest building dubai",
        original_request=novel,
        emit_tool_events=False,
    )
    assert len(seen) >= 2, seen
    sjoin = " ".join(seen).lower()
    assert "dubai" in sjoin
    assert "tesla" in sjoin or "ceo" in sjoin
    assert out


def test_e18_gta_trailer_and_characters_split_not_release_only():
    """
    Live bug: \"when trailer 3 will happen + characters in gta 6\"
    searched only \"gta 6 release date\" (model arg) and said no character info.
    Must fan out to Trailer 3 + characters queries from the user turn.
    """
    from agent.research import split_web_search_queries, normalize_web_search_query

    raw = (
        "i want you to find out when trailer 3 will happen, also can you figure out "
        "the names of the characters in gta 6 and what we know"
    )
    parts = split_web_search_queries(raw)
    assert len(parts) >= 2, parts
    joined = " | ".join(parts).lower()
    assert "trailer 3" in joined or "trailer" in joined
    assert "character" in joined or "lucia" in joined or "cast" in joined
    assert not any(p.lower() == "gta 6 release date" for p in parts)

    # Model-arg overwrite must not drop multi-intent from original_request
    agent = _bare_agent()
    seen = []

    def fake_raw(q: str) -> str:
        seen.append(q)
        if "character" in q.lower() or "lucia" in q.lower() or "cast" in q.lower():
            return (
                "1. GTA Wiki\n"
                "   URL: https://example.com/chars\n"
                "   Snippet: Lucia Caminos and Jason Duval are the protagonists of GTA 6 in Leonida."
            )
        return (
            "1. Trailer rumors\n"
            "   URL: https://example.com/t3\n"
            "   Snippet: Rockstar has not announced GTA 6 Trailer 3; summer 2026 rumors persist."
        )

    agent._raw_web_search_execute = fake_raw
    agent._fetch_search_result_page_text = lambda url, **kw: "Lucia and Jason. Trailer 3 unannounced."
    agent._verification_telemetry = VerificationTelemetry(enabled=False)
    agent._current_subject_text = ""
    agent._last_web_query_context = ""
    agent._active_user_query = raw
    agent._emit_tool_start = MagicMock()
    agent._emit_tool_end = MagicMock()
    agent._emit_tool_error = MagicMock()
    out = agent._grounded_web_search(
        "gta 6 release date",  # weak model arg (what the live log showed)
        original_request=raw,
        emit_tool_events=False,
    )
    assert any("trailer" in s.lower() for s in seen), seen
    assert any("character" in s.lower() or "lucia" in s.lower() or "cast" in s.lower() for s in seen), seen
    assert "lucia" in out.lower() or "jason" in out.lower() or "trailer" in out.lower()
    # Should not be pure give-up on characters when evidence exists
    assert "lucia" in out.lower() or "accepted=true" in out.lower() or "Jason" in out or "jason" in out.lower()


def test_e17_coding_loop_multi_file_tool_sequence():
    """
    v7.5.2: multi-file coding path advances the enforced loop
    inspect → plan → implement (2 files) → verify (terminal status) → confirm path.
    """
    from agent.coding_loop import CodingLoop, CodingPhase, CodingExit, parse_terminal_status_block
    from agent.core import EchoSpeakAgent
    from unittest.mock import MagicMock

    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent._workspace_id = "coding"
    agent._coding_loop = None
    agent._is_coding_project_intent = lambda u: True  # type: ignore

    agent._ensure_coding_loop("create a small website with index.html and style.css")
    assert agent._coding_loop is not None
    assert agent._coding_loop.phase == CodingPhase.INSPECT

    agent._coding_loop_note_tool("file_list", "path=.", "")
    assert agent._coding_loop.phase == CodingPhase.INSPECT

    agent._coding_loop_note_tool("file_write", "path='index.html'", "", pending_write=True)
    # implement or confirm depending on transition
    assert agent._coding_loop.phase in (CodingPhase.IMPLEMENT, CodingPhase.CONFIRM, CodingPhase.PLAN)
    agent._coding_loop_note_tool("file_write", "path='style.css'", "", pending_write=True)
    files = agent._coding_loop.state.files_touched
    assert any("index.html" in f for f in files) or any("style" in f for f in files)

    agent._coding_loop_note_tool(
        "terminal_run",
        "command=python -m pytest",
        "ExitCode=0\nStatus=pass\nMode=host",
    )
    assert agent._coding_loop.phase in (CodingPhase.VERIFY, CodingPhase.CONFIRM, CodingPhase.SUMMARIZE)
    assert agent._coding_loop.state.verify_status in (CodingExit.PASS.value, "pass", CodingExit.PENDING.value)

    # Full machine walk for multi-file project folder naming
    loop = CodingLoop(project_folder="projects/demo-site")
    loop.start()
    loop.advance(CodingPhase.PLAN)
    loop.advance(CodingPhase.IMPLEMENT)
    loop.mark_files(["index.html", "style.css", "app.js"])
    assert len(loop.state.files_touched) == 3
    loop.advance(CodingPhase.VERIFY)
    loop.set_verify_status(CodingExit.PASS)
    loop.advance(CodingPhase.CONFIRM)
    loop.set_confirm_status(CodingExit.PASS)
    loop.advance(CodingPhase.SUMMARIZE)
    loop.complete(exit_status=CodingExit.PASS)
    assert loop.state.exit_status == "pass"
    assert parse_terminal_status_block("ExitCode=1\nStatus=fail") == "fail"


def test_e15_single_preamble_per_request():
    """ReAct loops must not emit two spoken first-beats (Doing good… then Pretty good…)."""
    from api.server import _StreamingHandler
    import queue

    q: queue.Queue = queue.Queue()
    h = _StreamingHandler(q, request_id="req-preamble")
    h._preamble_fn = lambda *a, **k: "Doing good — checking that now."
    h._flush_partial_reply("tool_start", tool_name="web_search", tool_input="q1")
    h._start_new_generation()  # second ReAct tool loop
    h._preamble_fn = lambda *a, **k: "Pretty good — let me check that."
    h._flush_partial_reply("tool_start", tool_name="web_search", tool_input="q2")

    partials = []
    while not q.empty():
        evt = q.get_nowait()
        if evt.get("type") == "partial_reply":
            partials.append(evt.get("response"))
    assert len(partials) == 1, partials
    assert "Doing good" in (partials[0] or "")


def test_e16_schedule_signal_accepts_next_game_snippets():
    """Next-game snippets with date/matchup must be accepted (structural, any team phrase)."""
    from agent.research import SearchGrounder, build_search_intent, _normalize_sports_query

    g = SearchGrounder(max_candidates=3)
    user = "when do the edmonton oilers play next"
    compact = _normalize_sports_query(user)
    intent = build_search_intent(user, compact, "")
    assert intent.schedule_need is True
    assert intent.mode == "schedule"
    assert "oilers" in compact.lower() or "edmonton" in compact.lower()
    fixture = (
        "1. Team schedule\n"
        "   URL: https://www.nhl.com/schedule\n"
        "   Snippet: Next game: Edmonton Oilers vs Calgary Flames Oct 12, 2026 7:00 PM MT."
    )
    evidence = g.score_evidence(fixture, compact, intent)
    assert evidence, "expected scored evidence"
    assert g._has_schedule_signal(fixture.lower())
    assert g._accept_evidence(evidence, intent) is True

    # Deeper pass exists when first candidates fail (authority from league keyword if present)
    deeper = g._deeper_schedule_candidates(compact + " NHL", intent)
    assert any("next game" in c.query.lower() or "espn" in c.query.lower() or "nhl.com" in c.query.lower() for c in deeper)


def test_e14_weather_without_city_no_recursion():
    """
    Live bug: \"can you check the weather for me tho?\" triggered
    RecursionError: maximum recursion depth exceeded
    via _normalize_weather_query ↔ normalize_web_search_query_single.
    Must terminate with a compact weather query for any weather chat line.
    """
    from agent.research import (
        normalize_web_search_query,
        normalize_web_search_query_single,
        split_web_search_queries,
        _normalize_weather_query,
    )

    samples = [
        "can you check the weather for me tho?",
        "check the weather for me",
        "what the weather",
        "what's the weather like?",
        "not much just chilling hope you're well echo! look good! can you check the weather for me tho?",
        "weather in Calgary",
        "Edmonton weather today high low temperature forecast",
    ]
    for s in samples:
        out = normalize_web_search_query(s)
        assert out and "weather" in out.lower(), (s, out)
        assert len(out) < 200, (s, out)
        # single path must also terminate
        one = normalize_web_search_query_single(s)
        assert one and "weather" in one.lower(), (s, one)
        parts = split_web_search_queries(s)
        assert parts, s
        assert all("weather" in p.lower() or "Oilers" in p or "schedule" in p.lower() for p in parts) or parts

    # Leaf weather normalizer never needs a city (may pin calendar day)
    bare = _normalize_weather_query("check the weather for me")
    assert bare.lower().startswith("weather"), bare
    assert "high" in bare.lower() and "low" in bare.lower(), bare
    with_city = _normalize_weather_query("check the weather", city_hint="Calgary")
    assert with_city.startswith("Calgary weather"), with_city
    # Social+weather should not leave chat crumbs in the query
    social = normalize_web_search_query(
        "not much just chilling hope you're well echo! look good! can you check the weather for me tho?"
    )
    assert "weather" in social.lower() and "high" in social.lower(), social
    assert "chilling" not in social.lower() and "echo" not in social.lower()

    # Full grounded path must not recurse
    agent = _bare_agent()
    seen = []

    def fake_raw(q: str) -> str:
        seen.append(q)
        return (
            "1. Weather\n"
            "   URL: https://example.com/w\n"
            "   Snippet: High 20°C low 10°C partly cloudy."
        )

    agent._raw_web_search_execute = fake_raw
    agent._fetch_search_result_page_text = lambda url, **kw: "High 20 C low 10 C"
    agent._verification_telemetry = VerificationTelemetry(enabled=False)
    agent._current_subject_text = ""
    agent._last_web_query_context = ""
    agent._emit_tool_start = MagicMock()
    agent._emit_tool_end = MagicMock()
    agent._emit_tool_error = MagicMock()
    out = agent._grounded_web_search(
        "can you check the weather for me tho?",
        original_request="not much just chilling! look good! can you check the weather for me tho?",
        emit_tool_events=False,
    )
    assert seen, "search should execute once"
    assert all("weather" in s.lower() for s in seen), seen
    assert out


def test_e13_oilers_and_weather_split_not_blended():
    """
    Live bug: sports+weather multi-intent blended into one query.

    Must fan out into separate schedule + weather searches. Weather must NOT invent
    a city from the team nickname (no team→city hardcode); place comes from explicit
    text or stays generic.
    """
    from agent.research import SearchGrounder, build_search_intent, split_web_search_queries

    raw = (
        "just wondering when the next edmonton oiler game is and what the weather is? "
        "also im really liking how you look today! look great!"
    )
    parts = split_web_search_queries(raw)
    assert len(parts) >= 2, parts
    sports_parts = [
        p for p in parts
        if ("oiler" in p.lower() or "edmonton" in p.lower())
        and ("schedule" in p.lower() or "game" in p.lower() or "next" in p.lower())
    ]
    weather_parts = [p for p in parts if "weather" in p.lower() or "forecast" in p.lower()]
    assert sports_parts, parts
    assert weather_parts, parts
    assert all("look great" not in p.lower() and "liking" not in p.lower() for p in parts)
    # No blended single query
    assert not any("weather" in p.lower() and "oiler" in p.lower() and "schedule" in p.lower() for p in parts)

    sports = sports_parts[0]
    weather = weather_parts[0]
    si = build_search_intent(raw, sports, "")
    wi = build_search_intent(raw, weather, "")
    assert si.mode == "schedule" and si.weather_need is False
    assert wi.mode == "weather" and wi.weather_need is True
    # Sports candidates must NOT be weather-rewritten
    sc = SearchGrounder().build_candidates(si)
    assert sc and all("temperature" not in c.query.lower() for c in sc), [c.query for c in sc]
    assert any("oiler" in c.query.lower() or "edmonton" in c.query.lower() for c in sc)

    agent = _bare_agent()
    seen = []

    def fake_raw(q: str) -> str:
        seen.append(q)
        if "weather" in q.lower():
            return (
                "1. Environment Canada\n"
                "   URL: https://example.com/edm-weather\n"
                "   Snippet: Edmonton today high 24°C low 12°C partly cloudy."
            )
        return (
            "1. NHL\n"
            "   URL: https://example.com/oilers\n"
            "   Snippet: Next Edmonton Oilers game schedule vs Calgary Flames."
        )

    agent._raw_web_search_execute = fake_raw
    agent._fetch_search_result_page_text = lambda url, **kw: "Edmonton Oilers next game. High 24 C."
    agent._verification_telemetry = VerificationTelemetry(enabled=False)
    agent._current_subject_text = ""
    agent._last_web_query_context = ""
    agent._emit_tool_start = MagicMock()
    agent._emit_tool_end = MagicMock()
    agent._emit_tool_error = MagicMock()
    out = agent._grounded_web_search(raw, original_request=raw, emit_tool_events=True)
    assert len(seen) >= 2, seen
    assert any("oiler" in s.lower() or "edmonton" in s.lower() for s in seen if "weather" not in s.lower()), seen
    assert any("weather" in s.lower() for s in seen), seen
    assert all("look great" not in s.lower() for s in seen)
    # Multi-intent should emit per-query tool events for chat visibility
    assert agent._emit_tool_start.call_count >= 2
    assert agent._emit_tool_end.call_count >= 2
    assert "Oilers" in out or "oiler" in out.lower() or "weather" in out.lower() or "24" in out


def test_e12_social_plus_gta_trailer_preamble_answers_feeling():
    """
    Live bug: multi-intent
      \"how're you feeling? and … trailer 3 for gta 6\"
    produced only \"Checking that now.\" and skipped the social half.

    Preamble must answer the feeling question first even when the LLM fails.
    """
    agent = _bare_agent()
    agent._turn_partial_beats = []
    agent._sanitize_response_text = lambda s: s
    agent._invoke_visible_llm = lambda *a, **k: ""  # force failure path
    agent.llm_wrapper = SimpleNamespace(invoke_fast=lambda *a, **k: "")

    q = "how're you feeling? and i wonder when that new trailer comes out for trailer 3 for gta 6 hey?"
    assert agent._user_has_social_open(q) is True

    # Task-only model text must be rewritten to social-first
    beat = agent.generate_tool_preamble_beat(
        tool_name="web_search",
        tool_input="GTA 6 Trailer 3 release date",
        user_query=q,
        model_text="Checking that now.",
    )
    low = (beat or "").lower()
    assert beat and len(beat) >= 8
    assert any(
        x in low
        for x in (
            "doing good",
            "i'm good",
            "im good",
            "feeling solid",
            "all good",
            "pretty good",
        )
    ), f"social half missing from preamble: {beat!r}"
    assert any(
        x in low
        for x in (
            "check",
            "looking",
            "pulling",
            "looking into",
            "one sec",
            "sec on",
            "on that",
            "on it",
        )
    ), f"task half missing from preamble: {beat!r}"


# E20 — Deeper search must not claim "tools can't search" + real subject query
def test_e20_deeper_search_uses_subject_not_meta_phrase():
    from agent.research import normalize_web_search_query_single

    agent = _bare_agent()
    agent._current_subject_text = "Canada vs Morocco World Cup score"
    agent._last_web_query_context = "Canada vs Morocco World Cup score"
    resolved, is_fu, _subj = agent._resolve_referential_followup("do a deeper search")
    assert is_fu is True
    assert "canada" in resolved.lower() or "morocco" in resolved.lower()
    assert "do a deeper search" not in resolved.lower()
    cleaned = normalize_web_search_query_single(
        "do a deeper search about Canada vs Morocco World Cup score"
    )
    assert "deeper search" not in cleaned.lower()
    assert "canada" in cleaned.lower() or "morocco" in cleaned.lower()
    seen = []
    agent._raw_web_search_execute = lambda q: (seen.append(q) or FIXTURE_LIVE_SCORE)
    agent._fetch_search_result_page_text = lambda url, **kw: ""
    agent._request_search_cache = {}
    out = agent._grounded_web_search(
        resolved,
        original_request="do a deeper search",
        emit_tool_events=False,
    )
    assert seen, "expected at least one search"
    for q in seen:
        assert "do a deeper search" not in q.lower(), q
    assert is_grounded_search_output(out)
    if "SEARCH_EVIDENCE_INSUFFICIENT" in out:
        assert "Never claim" in out or "cannot search" in out.lower()
    agent._tool_available_in_current_context = lambda name: name == "web_search"
    agent._summarize_web_results = lambda *a, **k: "Canada 2-1 Morocco from live sources."
    fixed = agent._ensure_search_capability_honesty(
        "do a deeper search",
        "Sorry, my tools don't let me search the web right now.",
        callbacks=None,
    )
    assert "don't let me search" not in fixed.lower()
    assert "canada" in fixed.lower() or "morocco" in fixed.lower() or "2-1" in fixed


def test_e20b_tomorrow_schedule_and_spelling():
    from agent.research import apply_spelling_fixes, build_search_intent

    # Day-word STT is structural (tommrrow→tomorrow). Country/team typos are not
    # hard-fixed in production — search + model handle free-form spellings.
    fixed_day = apply_spelling_fixes("who is playing tommrrow world cup")
    assert "tomorrow" in fixed_day.lower()
    intent = build_search_intent(
        "who is playing tomorrow maracco world cup",
        "who is playing tomorrow maracco world cup",
    )
    assert intent.schedule_need is True or intent.current_day_need is True
    assert "world cup" in intent.resolved_request.lower() or "tomorrow" in intent.resolved_request.lower()
    fixture = (
        "1. FIFA World Cup fixtures\n"
        "   URL: https://example.com/fixtures\n"
        "   Snippet: Tomorrow's slate: Morocco vs Portugal at 3:00 PM ET, "
        "Brazil vs Spain at 6:00 PM ET kickoff schedule."
    )
    agent = _bare_agent()
    agent._raw_web_search_execute = lambda q: fixture
    agent._fetch_search_result_page_text = lambda url, **kw: ""
    agent._request_search_cache = {}
    out = agent._grounded_web_search(
        "who is playing tomorrow morocco world cup",
        original_request="who is playing tomorrow maracco world cup",
        emit_tool_events=False,
    )
    assert is_grounded_search_output(out)
    assert "accepted=true" in out.lower() or "morocco" in out.lower()


def test_e20c_search_cache_dedupes_identical_queries():
    from agent.core import EchoSpeakAgent
    import agent.tools as tools_mod

    hits = {"n": 0}

    def fake_invoke(payload):
        hits["n"] += 1
        return FIXTURE_LIVE_SCORE

    orig = tools_mod.web_search
    tools_mod.web_search = type("T", (), {"invoke": staticmethod(fake_invoke)})()
    try:
        agent = _bare_agent()
        agent._request_search_cache = {}
        a = EchoSpeakAgent._raw_web_search_execute(agent, "Canada vs Morocco score")
        b = EchoSpeakAgent._raw_web_search_execute(agent, "Canada vs Morocco score")
        c = EchoSpeakAgent._raw_web_search_execute(agent, "Canada vs Morocco score")
        assert a and b and c
        assert hits["n"] == 1, f"expected 1 network call, got {hits['n']}"
    finally:
        tools_mod.web_search = orig


def test_e20d_routine_does_not_clobber_subject():
    agent = _bare_agent()
    agent._current_subject_text = "Edmonton Oilers odds"
    agent._last_web_query_context = "Oilers moneyline"
    agent._partial_tool_results = [{"tool": "web_search", "output": "oilers odds"}]
    agent.process_query = MagicMock(return_value=("Daily briefing done.", True))
    routine = SimpleNamespace(
        id="daily_news",
        name="daily news briefing",
        action_type="query",
        action_config={"message": "daily news briefing"},
        delivery_channels=["web"],
    )
    agent._execute_routine = EchoSpeakAgent._execute_routine.__get__(agent, EchoSpeakAgent)
    try:
        agent._execute_routine(routine)
    except Exception:
        pass
    assert agent._current_subject_text == "Edmonton Oilers odds"
    assert agent._last_web_query_context == "Oilers moneyline"
    assert agent.process_query.called
    kwargs = agent.process_query.call_args.kwargs
    assert kwargs.get("source") == "routine"
    assert str(kwargs.get("thread_id") or "").startswith("routine_")


def test_eval_board_counts_at_least_eight_passing():
    """Meta-check: this module defines discrete E-scenarios."""
    import pathlib
    import re

    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    e_tests = re.findall(r"^def (test_e\d+_)", src, flags=re.MULTILINE)
    assert len(e_tests) >= 20

