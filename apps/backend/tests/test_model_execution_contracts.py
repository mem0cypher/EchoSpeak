from __future__ import annotations

import time

import pytest

from agent.identity import compile_echo_identity
from agent.model_contracts import (
    AgentDecision,
    ApprovalState,
    DecisionKind,
    DecisionValidationError,
    ToolCall,
    ToolDefinition,
    ToolOutcome,
    ToolUsePolicy,
    validate_agent_decision,
)
from agent.model_control_plane import ModelTurnEnvelopeCompiler


IDENTITY = compile_echo_identity("Echo test identity", provider="lmstudio", model_id="Qwen3.5-9B-Q4_K_M.gguf")


def _tool() -> ToolDefinition:
    return ToolDefinition(
        name="calculate",
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
            "additionalProperties": False,
        },
    )


def _compile(*, outcomes=(), policy=ToolUsePolicy.REQUIRED, approval=None, missing=None):
    return ModelTurnEnvelopeCompiler().compile(
        project_id="project-1",
        session_id="session-1",
        turn_id="execution-1",
        execution_id="execution-1",
        request_id="request-1",
        provider="lmstudio",
        model_id="Qwen3.5-9B-Q4_K_M.gguf",
        assistant_identity=IDENTITY,
        objective="Calculate a value",
        task_status="in_progress",
        current_plan_step={"id": "step-1", "status": "active"},
        collected_inputs={"expression": "2+2"},
        missing_inputs=missing or [],
        latest_user_relation="continue",
        latest_user_message="continue",
        allowed_tools=[_tool()] if policy != ToolUsePolicy.PROHIBITED else [],
        tool_use_policy=policy,
        relevant_memory=[{"type": "task", "content": "scoped"}],
        approval=approval,
        tool_outcomes=outcomes,
        constraints=["disposable tools only"],
    )


def _verified_outcome(**updates) -> ToolOutcome:
    values = {
        "tool_name": "calculate",
        "execution_id": "execution-1",
        "turn_id": "execution-1",
        "session_id": "session-1",
        "project_id": "project-1",
        "success": True,
        "status": "success",
        "output": "4",
        "verification": {
            "runtime_boundary": "test",
            "verified": True,
            "verification_kind": "deterministic_fixture",
            "covered_fields": ["calculated_value"],
            "verified_at": time.time(),
        },
    }
    values.update(updates)
    return ToolOutcome(**values)


def test_compiler_preserves_exact_identity_and_safe_diagnostics():
    envelope = _compile()
    assert envelope.identity.project_id == "project-1"
    assert envelope.identity.session_id == "session-1"
    assert envelope.identity.turn_id == envelope.identity.execution_id == "execution-1"
    assert envelope.model_family == "qwen"
    assert envelope.adapter_version == "qwen-v1"
    diagnostics = envelope.safe_diagnostics()
    assert diagnostics["allowed_tool_names"] == ["calculate"]
    assert "scoped" not in str(diagnostics)
    assert len(diagnostics["user_message_sha256"]) == 64


def test_wrong_scope_outcomes_are_not_reinjected():
    wrong = _verified_outcome(session_id="another-session")
    envelope = _compile(outcomes=[wrong])
    assert envelope.verified_tool_outcomes == []


def test_mandatory_tool_work_cannot_finish_as_prose():
    envelope = _compile()
    with pytest.raises(DecisionValidationError, match="not valid|Mandatory"):
        validate_agent_decision(
            envelope,
            AgentDecision(kind=DecisionKind.ANSWER, message="The result is 4."),
        )


def test_verified_outcome_allows_grounded_answer():
    outcome = _verified_outcome()
    envelope = _compile(outcomes=[outcome])
    decision = AgentDecision(
        kind=DecisionKind.ANSWER,
        message="The verified result is 4.",
        verified_outcome_ids=[outcome.run_id],
    )
    assert validate_agent_decision(envelope, decision) == decision


def test_tool_name_and_arguments_are_revalidated_against_current_envelope():
    envelope = _compile()
    with pytest.raises(DecisionValidationError, match="Missing required"):
        validate_agent_decision(
            envelope,
            AgentDecision(
                kind=DecisionKind.CALL_TOOL,
                tool_call=ToolCall(id="call-1", name="calculate", arguments={}),
            ),
        )
    with pytest.raises(DecisionValidationError, match="allowlist"):
        validate_agent_decision(
            envelope,
            AgentDecision(
                kind=DecisionKind.CALL_TOOL,
                tool_call=ToolCall(id="call-2", name="terminal_run", arguments={}),
            ),
        )


def test_pending_approval_exposes_only_block_or_cancel():
    envelope = _compile(approval=ApprovalState(status="pending", approval_id="approval-1"))
    assert envelope.valid_next_actions == [DecisionKind.BLOCK, DecisionKind.CANCEL]
    with pytest.raises(DecisionValidationError, match="not valid"):
        validate_agent_decision(
            envelope,
            AgentDecision(
                kind=DecisionKind.CALL_TOOL,
                tool_call=ToolCall(id="call-1", name="calculate", arguments={"expression": "2+2"}),
            ),
        )


def test_missing_runtime_inputs_require_clarification_before_answer():
    envelope = _compile(missing=["location"])
    assert DecisionKind.ASK_FOR_INPUT in envelope.valid_next_actions
    assert DecisionKind.CALL_TOOL in envelope.valid_next_actions
    assert DecisionKind.ANSWER not in envelope.valid_next_actions
    with pytest.raises(DecisionValidationError, match="not valid|missing"):
        validate_agent_decision(
            envelope,
            AgentDecision(kind=DecisionKind.ANSWER, message="Done."),
        )


def test_nonusable_provider_outcome_never_enables_answer_or_reoffers_provider():
    outcome = _verified_outcome(
        output="result_state=provider_unavailable",
        result_state="provider_unavailable",
        retryable=False,
    )
    envelope = _compile(outcomes=[outcome])
    assert DecisionKind.ANSWER not in envelope.valid_next_actions
    assert DecisionKind.CALL_TOOL not in envelope.valid_next_actions
    assert envelope.allowed_tools == []
    with pytest.raises(DecisionValidationError, match="not valid|Mandatory"):
        validate_agent_decision(
            envelope,
            AgentDecision(
                kind=DecisionKind.ANSWER,
                message="The provider completed, so here is an unverified answer.",
            ),
        )
