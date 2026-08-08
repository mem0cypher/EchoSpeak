"""Bounded Turn Understanding repair for correctable model structured-output errors."""
from __future__ import annotations

import json

import pytest

from agent.identity import EchoIdentityProjection
from agent.turn_understanding import (
    TURN_UNDERSTANDING_FALLBACK,
    TURN_UNDERSTANDING_NORMALIZED,
    TURN_UNDERSTANDING_REPAIRED,
    TurnInterpretation,
    TurnInterpreter,
    TurnRelation,
    TurnUnderstandingEnvelope,
    TurnUnderstandingError,
    message_looks_like_retrieval_or_mutation,
    minimal_safe_fallback_interpretation,
    normalize_extraneous_clarification_question,
    turn_interpretation_model_schema,
    validate_turn_interpretation_payload,
)


def _identity() -> EchoIdentityProjection:
    return EchoIdentityProjection(
        assistant_name="Echo",
        product_name="EchoSpeak",
        soul_sha256="a" * 64,
        soul_rules="t",
    )


def _envelope(message: str = "How are you?") -> TurnUnderstandingEnvelope:
    return TurnUnderstandingEnvelope(
        assistant_identity=_identity(),
        latest_user_message=message,
        recent_conversation=[],
        reply_relationship={},
        project_id="",
        session_id="s1",
        suspended_tasks=[],
        active_approvals=[],
        relevant_memory=[],
        project_context=[],
        recent_verified_outcomes=[],
        entity_candidates=[],
        source="test",
    )


def test_normalize_strips_extraneous_clarification_on_new_task() -> None:
    payload = {
        "relation": "new_task",
        "proposed_objective": "Summarize my notes",
        "requested_capabilities": ["conversation"],
        "clarification_question": "Which notes?",
        "confidence": 0.9,
        "candidate_alternatives": [],
        "missing_fields": [],
    }
    fixed, notes = normalize_extraneous_clarification_question(payload)
    assert fixed["clarification_question"] is None
    assert any("stripped_extraneous_clarification_question" in item for item in notes)
    interpretation, diagnostics = validate_turn_interpretation_payload(fixed)
    assert interpretation.relation == TurnRelation.NEW_TASK
    assert interpretation.clarification_question is None
    assert diagnostics.get("lifecycle") == TURN_UNDERSTANDING_NORMALIZED or notes


def test_normalize_keeps_question_when_ambiguous() -> None:
    payload = {
        "relation": "ambiguous",
        "clarification_question": "Which project?",
        "confidence": 0.7,
    }
    fixed, notes = normalize_extraneous_clarification_question(payload)
    assert fixed["clarification_question"] == "Which project?"
    assert notes == []


def test_normalize_keeps_question_when_alternatives_exist() -> None:
    payload = {
        "relation": "new_task",
        "proposed_objective": "Continue the work",
        "clarification_question": "Which one?",
        "confidence": 0.6,
        "candidate_alternatives": [{"id": "a"}, {"id": "b"}],
    }
    fixed, notes = normalize_extraneous_clarification_question(payload)
    assert fixed["clarification_question"] == "Which one?"
    assert notes == []


def test_schema_documents_clarification_discriminator() -> None:
    schema = turn_interpretation_model_schema()
    assert "allOf" in schema
    desc = str(schema["properties"]["clarification_question"].get("description") or "")
    assert "ambiguous" in desc.casefold()


def test_interpreter_repairs_extraneous_clarification_via_model_retry() -> None:
    invalid = {
        "relation": "new_task",
        "proposed_objective": "Tell me a joke",
        "requested_capabilities": ["conversation"],
        "clarification_question": "What kind of joke?",
        "confidence": 0.8,
        "requirements": [],
        "missing_fields": [],
        "candidate_alternatives": [],
    }
    # First response has both new_task and a question (the incident shape).
    # Normalization should fix it without needing a second model call.
    calls: list[dict] = []

    def invoke(messages, schema, temperature=None, **kwargs):
        calls.append({"temperature": temperature, "messages": len(messages)})
        return invalid

    interpretation = TurnInterpreter().interpret(
        _envelope("Tell me a joke"),
        invoke_selected_model=invoke,
    )
    assert interpretation.relation == TurnRelation.NEW_TASK
    assert interpretation.clarification_question is None
    assert interpretation.safe_decode_diagnostics().get("lifecycle") in {
        TURN_UNDERSTANDING_NORMALIZED,
        TURN_UNDERSTANDING_REPAIRED,
        "turn_understanding_ok",
    }
    # Deterministic normalize happens before retry — one model call is enough.
    assert len(calls) == 1


def test_interpreter_retries_then_accepts_corrected_json() -> None:
    bad = {
        "relation": "casual_conversation",
        "clarification_question": "What do you mean?",
        "confidence": 0.5,
        "candidate_alternatives": [{"x": 1}],  # blocks normalize
        "missing_fields": [],
    }
    good = {
        "relation": "casual_conversation",
        "clarification_question": None,
        "confidence": 0.9,
        "requested_capabilities": ["conversation"],
        "candidate_alternatives": [],
        "missing_fields": [],
    }
    payloads = [bad, good]
    temps: list[float | None] = []

    def invoke(messages, schema, temperature=None, **kwargs):
        temps.append(temperature)
        return payloads.pop(0)

    interpretation = TurnInterpreter().interpret(
        _envelope("hey"),
        invoke_selected_model=invoke,
    )
    assert interpretation.relation == TurnRelation.CASUAL_CONVERSATION
    assert interpretation.clarification_question is None
    assert interpretation.safe_decode_diagnostics().get("lifecycle") == TURN_UNDERSTANDING_REPAIRED
    assert temps[0] is None
    assert temps[1] == pytest.approx(0.1)


def test_interpreter_fallback_for_conversational_exhaustion() -> None:
    bad = {
        "relation": "casual_conversation",
        "clarification_question": "huh?",
        "confidence": 0.4,
        "candidate_alternatives": [{"a": 1}],
    }

    def invoke(messages, schema, temperature=None, **kwargs):
        return bad

    interpretation = TurnInterpreter().interpret(
        _envelope("How is your day going?"),
        invoke_selected_model=invoke,
    )
    assert interpretation.relation == TurnRelation.NEW_TASK
    assert interpretation.safe_decode_diagnostics().get("lifecycle") == TURN_UNDERSTANDING_FALLBACK


def test_interpreter_does_not_fallback_for_weather_exhaustion() -> None:
    bad = {
        "relation": "new_task",
        "proposed_objective": "weather in Edmonton",
        "clarification_question": "Which city?",  # invalid combo; alternatives block normalize
        "confidence": 0.5,
        "candidate_alternatives": [{"x": 1}],
        "requested_capabilities": ["live_weather"],
    }

    def invoke(messages, schema, temperature=None, **kwargs):
        return bad

    with pytest.raises(TurnUnderstandingError) as exc:
        TurnInterpreter().interpret(
            _envelope("What is the weather in Edmonton tomorrow?"),
            invoke_selected_model=invoke,
        )
    assert exc.value.diagnostics.get("lifecycle") == "turn_understanding_exhausted"
    assert message_looks_like_retrieval_or_mutation("What is the weather in Edmonton tomorrow?")


def test_minimal_fallback_is_answer_only() -> None:
    row = minimal_safe_fallback_interpretation("Hello there")
    assert row.relation == TurnRelation.NEW_TASK
    assert row.requirements[0].kind.value == "answer_only"
