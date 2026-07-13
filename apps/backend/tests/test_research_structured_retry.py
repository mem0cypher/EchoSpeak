"""Research malformed tool calls get one bounded structured retry."""

from __future__ import annotations

from types import SimpleNamespace


def test_web_search_args_accept_aliases():
    from agent.tools import WebSearchArgs

    m = WebSearchArgs.model_validate({"q": "Edmonton Oilers founded"})
    assert m.query == "Edmonton Oilers founded"
    m2 = WebSearchArgs.model_validate(
        {"query": "local-first AI", "objective": "summary", "local_first": False, "freshness": "latest"}
    )
    assert m2.objective == "summary"
    assert m2.freshness == "latest"


def test_bounded_research_retry_runs_web_search(monkeypatch):
    from agent.core import EchoSpeakAgent

    class A:
        pass

    a = A()
    a._tools_succeeded_this_turn = lambda: set()
    a._research_structured_retry_used = False
    calls = []

    def fake_grounded(q, original_request="", emit_tool_events=True, **kwargs):
        calls.append(q)
        a._tools_succeeded_this_turn = lambda: {"web_search"}
        return "SEARCH_OK: fact with sources about local-first agents"

    a._grounded_web_search = fake_grounded
    a._extract_search_query = lambda u: "local-first AI agents"
    a._user_wants_public_research = EchoSpeakAgent._user_wants_public_research.__get__(a)
    a._bounded_research_structured_retry = EchoSpeakAgent._bounded_research_structured_retry.__get__(a)

    out = a._bounded_research_structured_retry(
        "Look up the latest public information about local-first AI agents",
        "malformed tool markup without recoverable query field",
    )
    assert out is not None
    assert "SEARCH_OK" in out
    assert calls
    assert "local-first" in calls[0]


def test_local_first_skips_research_retry():
    from agent.core import EchoSpeakAgent

    class A:
        pass

    a = A()
    a._user_wants_public_research = EchoSpeakAgent._user_wants_public_research.__get__(a)
    a._bounded_research_structured_retry = EchoSpeakAgent._bounded_research_structured_retry.__get__(a)
    a._tools_succeeded_this_turn = lambda: set()
    out = a._bounded_research_structured_retry(
        "Use local project material first and do not search the web.",
        "web_search(query='x')",
    )
    assert out is None


def test_research_inventory_excludes_unrelated_specialized_tools():
    """Ordinary public research must not expose browse/youtube by default."""
    from agent.core import EchoSpeakAgent
    from agent.mode_controller import ModeDecision, TurnMode

    class A:
        def _all_lc_tool_names(self):
            return {
                "web_search",
                "browse_task",
                "youtube_transcript",
                "sports_live",
                "file_list",
                "get_system_time",
            }

    a = A()
    a._bind_research_tool_inventory = EchoSpeakAgent._bind_research_tool_inventory.__get__(a)
    decision = ModeDecision(
        mode=TurnMode.TASK_RESEARCH,
        confidence=0.9,
        reason="research request",
        user_text="Research the latest public information about local-first AI agents",
        allowed_tool_names=frozenset(a._all_lc_tool_names()),
    )
    bound = a._bind_research_tool_inventory(decision.user_text, decision)
    names = set(bound.allowed_tool_names or [])
    assert "web_search" in names
    assert "youtube_transcript" not in names
    assert "browse_task" not in names

    yt = a._bind_research_tool_inventory(
        "Get the YouTube transcript for https://youtu.be/abc123",
        decision.with_allowed_tools(frozenset(a._all_lc_tool_names())),
    )
    assert "youtube_transcript" in set(yt.allowed_tool_names or [])
