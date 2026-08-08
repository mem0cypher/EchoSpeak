from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from agent.core import ConversationMemory, EchoSpeakAgent
from agent.semantic_runtime import CanonicalSemanticRuntime
from agent.model_runtime import resolve_model_profile
from agent.threads import ThreadManager
from agent.turn_understanding import (
    TurnInterpretation,
    TurnInterpretationNormalizationError,
    TurnRelation,
    decode_turn_interpretation_payload,
    turn_interpretation_model_schema,
)


def _interpret(payload: dict) -> TurnInterpretation:
    canonical, _diagnostics = decode_turn_interpretation_payload(payload)
    return TurnInterpretation.model_validate(canonical)


def test_clarification_is_relation_owned_and_legacy_boolean_is_ignored() -> None:
    interpretation = _interpret({
        "relation": "casual_conversation",
        "clarification_required": True,
        "confidence": 0.9,
    })
    assert interpretation.clarification_required is False
    assert "clarification_required" not in turn_interpretation_model_schema()["properties"]

    ambiguous = _interpret({
        "relation": "ambiguous",
        "clarificationRequired": False,
        "clarification_question": "Which project?",
        "confidence": 0.8,
    })
    assert ambiguous.clarification_required is True


def test_invalid_legacy_clarification_type_and_non_ambiguous_question_fail() -> None:
    with pytest.raises(TurnInterpretationNormalizationError, match="boolean or null"):
        decode_turn_interpretation_payload({
            "relation": "casual_conversation",
            "clarification_required": {"value": True},
            "confidence": 0.9,
        })
    with pytest.raises(ValueError, match="only valid for ambiguous"):
        _interpret({
            "relation": "casual_conversation",
            "clarification_question": "Why?",
            "confidence": 0.9,
        })


def test_typed_sports_operation_adds_live_sports_without_dropping_research() -> None:
    interpretation = _interpret({
        "relation": "new_task",
        "proposed_objective": "Find the next match",
        "requested_capabilities": ["research"],
        "requested_operation": "fifa_match_schedule",
        "confidence": 0.95,
    })
    assert interpretation.requested_capabilities == ["research", "live_sports"]


def test_informational_sports_time_is_tool_discoverable_not_user_blocking() -> None:
    interpretation = _interpret({
        "relation": "new_task",
        "proposed_objective": "Find opinions about Argentina vs Spain this Saturday",
        "requested_capabilities": ["research", "live_sports"],
        "requested_operation": "schedule",
        "extracted_fields": {"teams": ["Argentina", "Spain"], "date": "this Saturday"},
        "missing_fields": ["specific_time", "location"],
        "confidence": 0.95,
    })
    assert interpretation.missing_fields == ["location"]


def test_memory_recall_never_enters_the_durable_writer() -> None:
    interpretation = _interpret({
        "relation": "casual_conversation",
        "requested_capabilities": ["memory", "conversation"],
        "requested_operation": "memory_recall",
        "confidence": 0.99,
    })
    agent = SimpleNamespace(_owner_memory_access_allowed=lambda: True)
    assert CanonicalSemanticRuntime._persist_explicit_memory(
        agent, interpretation, "what is my name?", None
    ) == ""


def test_local_understanding_uses_cold_start_budget_and_stops_at_complete_json(monkeypatch) -> None:
    class FakeRunnable:
        def __init__(self) -> None:
            self.chunks_read = 0
            self.last_messages = []

        def bind(self, **_kwargs):
            return self

        def stream(self, _messages):
            self.last_messages = list(_messages)
            self.chunks_read += 1
            yield '{"relation":"casual_conversation"} trailing prose'
            self.chunks_read += 1
            yield "must not be consumed"

    runnable = FakeRunnable()
    monkeypatch.setattr(
        "agent.semantic_runtime.ensure_selected_model_ready",
        lambda *_args, **_kwargs: SimpleNamespace(
            action="none",
            state="ready",
            instance_id="",
            load_time_seconds=0.0,
        ),
    )
    agent = SimpleNamespace(
        model_runtime=SimpleNamespace(llm=runnable, model_id="qwen-local"),
        llm_provider=SimpleNamespace(value="lmstudio"),
        _active_model_profile=resolve_model_profile("lmstudio", "qwen-local", {"local": True}),
    )
    result = CanonicalSemanticRuntime._invoke_understanding_model(
        agent,
        [{"role": "user", "content": "hello"}],
        schema={"type": "object"},
        cancel_event=threading.Event(),
    )
    assert result == {"relation": "casual_conversation"}
    assert runnable.chunks_read == 1
    assert agent._turn_understanding_output_mode["cold_start_allowance"] is True
    assert agent._turn_understanding_output_mode["timeout_seconds"] >= 120
    assert agent._turn_understanding_output_mode["max_output_tokens"] == 2048
    assert "/no_think" in runnable.last_messages[0]["content"]


def test_session_creation_key_is_durable_idempotency_identity(tmp_path) -> None:
    manager = ThreadManager(tmp_path / "threads.json")
    first = manager.create_thread(
        title="One",
        source="web",
        idempotency_key="request-1",
        idempotency_context="project-a",
    )
    replay = manager.create_thread(
        title="One",
        source="web",
        idempotency_key="request-1",
        idempotency_context="project-a",
    )
    assert replay.thread_id == first.thread_id
    assert len(manager.list_threads()) == 1
    with pytest.raises(ValueError, match="different Session creation parameters"):
        manager.create_thread(
            title="Different",
            source="web",
            idempotency_key="request-1",
            idempotency_context="project-a",
        )


def test_tool_run_registration_survives_worker_thread_hop() -> None:
    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent._registered_tool_runs = {}
    agent._current_execution_id = "execution-1"
    agent._current_request_id = "request-1"
    agent._current_thread_id = "session-1"

    thread = threading.Thread(target=agent._register_tool_run, args=("web_search", "run-1"))
    thread.start()
    thread.join()

    assert agent._claim_tool_run("web_search") == "run-1"
    assert agent._registered_tool_runs == {}


def test_conversation_projection_rehydrates_from_durable_session_timeline() -> None:
    target = ConversationMemory()
    fake = SimpleNamespace(
        _state_store=SimpleNamespace(
            session_timeline=lambda _session, limit: {
                "turns": [{
                    "messages": [
                        {"role": "user", "text": "hello"},
                        {"role": "assistant", "text": "Hi, I'm Echo."},
                    ]
                }]
            }
        )
    )
    EchoSpeakAgent._rehydrate_conversation_memory(fake, "session-1", target)
    assert target.messages == [
        {"role": "human", "content": "hello"},
        {"role": "ai", "content": "Hi, I'm Echo."},
    ]
