"""Referential search retry routing + false search-claim rejection."""
from __future__ import annotations

from agent.mode_controller import (
    TurnMode,
    allowed_tools_for_mode,
    classify_turn_mode,
    is_search_retry_utterance,
    _intent_relation,
)


def test_search_retry_utterances_are_detected():
    assert is_search_retry_utterance("can you try again with that search!")
    assert is_search_retry_utterance("retry the search please")
    assert is_search_retry_utterance("search it again")
    assert is_search_retry_utterance("sorry im from edmonton remember that! and retry the search...")
    assert not is_search_retry_utterance("what time is it")


def test_intent_relation_retry_for_search_phrases():
    assert (
        _intent_relation(
            "can you try again with that search! im using a different model",
            continues=False,
            explicit_new=False,
        )
        == "retry"
    )


def test_classify_search_retry_as_task_research_with_tools():
    text = "can you try again with that search! im using a different model lets see if it will work now..."
    decision = classify_turn_mode(text)
    assert decision.mode == TurnMode.TASK_RESEARCH
    assert decision.intent_relation == "retry"
    names = ["web_search", "sports_live", "calculate", "get_system_time", "system_info", "file_read"]
    allowed = allowed_tools_for_mode(decision, names)
    assert "web_search" in allowed
    assert "file_read" not in allowed


def test_response_claims_performed_search_detection():
    from agent.core import EchoSpeakAgent

    agent = object.__new__(EchoSpeakAgent)
    assert agent._response_claims_performed_search("I just searched and found the score.")
    assert agent._response_claims_performed_search("Here's what I found online.")
    assert not agent._response_claims_performed_search("I can look that up if you want.")


def test_enforce_search_truth_rejects_claim_without_toolrun():
    from agent.core import EchoSpeakAgent
    from agent.mode_controller import ModeDecision, TurnMode

    agent = object.__new__(EchoSpeakAgent)
    agent._current_mode_decision = ModeDecision(
        mode=TurnMode.TASK_RESEARCH,
        confidence=0.9,
        reason="referential search retry",
        user_text="weather",
        evidence_required=True,
        verification_required=True,
        required_capabilities=frozenset({"research"}),
    )
    agent._current_allowed_tools = set()
    agent._current_execution_id = ""
    agent._tools_succeeded_this_turn = lambda: set()  # type: ignore
    agent._tool_available_in_current_context = lambda _n: False  # type: ignore
    agent._state_store = type("S", (), {"list_tool_runs": lambda self, _e: []})()

    text, ok = agent._enforce_search_execution_truth(
        "try that search again",
        "I searched the web and found that Edmonton won.",
    )
    assert ok is False
    assert "could not perform a verified search" in text.lower()
