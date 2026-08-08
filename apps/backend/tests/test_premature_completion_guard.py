"""False completion before ToolRuns must not ban call_tool or leak objectives."""
from __future__ import annotations

from agent.identity import EchoIdentityProjection
from agent.model_contracts import DecisionKind, ToolDefinition, ToolUsePolicy
from agent.model_control_plane import (
    ModelTurnEnvelopeCompiler,
    safe_decision_rejection_message,
)
from agent.research_runtime import (
    RequirementKind,
    RequirementStatus,
    TurnRequirement,
    demote_unverified_retrieval_states,
    initial_requirement_states,
    rekind_misclassified_live_requirements,
    requirement_requires_verified_tool_evidence,
)


def _identity() -> EchoIdentityProjection:
    return EchoIdentityProjection(
        assistant_name="Echo",
        product_name="EchoSpeak",
        soul_sha256="a" * 64,
        soul_rules="t",
    )


def _mixed_poisoned_requirements() -> list[TurnRequirement]:
    return [
        TurnRequirement(
            requirement_id="req-edm",
            kind=RequirementKind.ANSWER_ONLY,  # mislabeled live work
            objective="Get the weather forecast for Edmonton tomorrow",
        ),
        TurnRequirement(
            requirement_id="req-cal",
            kind=RequirementKind.ANSWER_ONLY,
            objective="Get the weather forecast for Calgary tomorrow",
        ),
        TurnRequirement(
            requirement_id="req-name",
            kind=RequirementKind.MEMORY,
            objective="Retrieve the user's name from memory",
        ),
        TurnRequirement(
            requirement_id="req-use",
            kind=RequirementKind.ANSWER_ONLY,
            objective="Explain Echo's usefulness",
        ),
    ]


def test_rekind_promotes_weather_answer_only_to_retrieval() -> None:
    rows = rekind_misclassified_live_requirements(_mixed_poisoned_requirements())
    kinds = {item.requirement_id: item.kind for item in rows}
    assert kinds["req-edm"] == RequirementKind.RETRIEVAL
    assert kinds["req-cal"] == RequirementKind.RETRIEVAL
    assert kinds["req-name"] == RequirementKind.MEMORY
    assert kinds["req-use"] == RequirementKind.ANSWER_ONLY
    assert requirement_requires_verified_tool_evidence(rows[0]) is True


def test_demote_reopens_mislabeled_weather_without_tool_evidence() -> None:
    rows = rekind_misclassified_live_requirements(_mixed_poisoned_requirements())
    states = initial_requirement_states(rows)
    for rid in states:
        states[rid] = states[rid].model_copy(
            update={"status": RequirementStatus.SATISFIED, "terminal_reason": "poison"}
        )
    demoted = demote_unverified_retrieval_states(rows, states)
    assert demoted["req-edm"].status == RequirementStatus.PENDING
    assert demoted["req-cal"].status == RequirementStatus.PENDING
    assert demoted["req-use"].status == RequirementStatus.SATISFIED


def test_envelope_keeps_call_tool_when_falsely_complete_with_zero_outcomes() -> None:
    rows = _mixed_poisoned_requirements()
    states = initial_requirement_states(rows)
    for rid in states:
        states[rid] = states[rid].model_copy(
            update={"status": RequirementStatus.SATISFIED, "terminal_reason": "poison"}
        )
    compiler = ModelTurnEnvelopeCompiler()
    envelope = compiler.compile(
        project_id="",
        session_id="s1",
        turn_id="t1",
        execution_id="t1",
        request_id="r1",
        provider="lmstudio",
        model_id="test",
        assistant_identity=_identity(),
        objective="mixed multi-part weather request",
        task_status="running",
        current_plan_step=None,
        collected_inputs={},
        missing_inputs=[],
        latest_user_relation="new_work",
        latest_user_message=(
            "Get Edmonton and Calgary weather tomorrow, my name, and is Echo useful?"
        ),
        allowed_tools=[
            ToolDefinition(name="weather_live"),
            ToolDefinition(name="web_search"),
        ],
        tool_use_policy=ToolUsePolicy.REQUIRED,
        relevant_memory=[{"type": "profile", "content": "User name is Ty"}],
        approval=None,
        tool_outcomes=[],
        task_requirements=rows,
        requirement_states=states,
        task_run_id="task-1",
        execution_profile="work",
        active_graph_node_ids=["finalize"],
    )
    diag = envelope.safe_diagnostics()
    assert diag["completion_finalizable"] is False
    assert diag["completion_disposition"] == "pending"
    assert DecisionKind.CALL_TOOL in envelope.valid_next_actions
    assert DecisionKind.ANSWER not in envelope.valid_next_actions
    assert diag["requirement_states"]["req-edm"] == "pending"
    assert diag["requirement_states"]["req-cal"] == "pending"
    assert diag["active_graph_node_ids"] != ["finalize"]
    assert envelope.verified_tool_outcomes == []


def test_exact_incident_four_requirements_zero_evidence_allows_call_tool() -> None:
    """Regression for the packaged-build false-complete incident.

    Two retrieval weather parts + memory + answer_only, tools required, no evidence.
    Expected: pending completion, call_tool legal, finalize not sole active node.
    """
    rows = [
        TurnRequirement(
            requirement_id="req-edm",
            kind=RequirementKind.RETRIEVAL,
            objective="Get the weather forecast for Edmonton tomorrow",
            entities=["Edmonton"],
            location="Edmonton",
            requested_fields=["weather_conditions"],
        ),
        TurnRequirement(
            requirement_id="req-cal",
            kind=RequirementKind.RETRIEVAL,
            objective="Get the weather forecast for Calgary tomorrow",
            entities=["Calgary"],
            location="Calgary",
            requested_fields=["weather_conditions"],
        ),
        TurnRequirement(
            requirement_id="req-name",
            kind=RequirementKind.MEMORY,
            objective="Retrieve the user's name from memory",
        ),
        TurnRequirement(
            requirement_id="req-use",
            kind=RequirementKind.ANSWER_ONLY,
            objective="Explain Echo's usefulness",
        ),
    ]
    # Poison: all satisfied with zero evidence (the packaged bug shape).
    states = initial_requirement_states(rows)
    for rid in states:
        states[rid] = states[rid].model_copy(
            update={
                "status": RequirementStatus.SATISFIED,
                "terminal_reason": "poison_false_complete",
                "tool_run_ids": [],
                "evidence_ids": [],
            }
        )
    compiler = ModelTurnEnvelopeCompiler()
    envelope = compiler.compile(
        project_id="",
        session_id="s1",
        turn_id="t1",
        execution_id="t1",
        request_id="r1",
        provider="lmstudio",
        model_id="gemma",
        assistant_identity=_identity(),
        objective="Edmonton and Calgary weather, my name, is Echo useful?",
        task_status="running",
        current_plan_step=None,
        collected_inputs={},
        missing_inputs=[],
        latest_user_relation="new_work",
        latest_user_message="Edmonton and Calgary weather tomorrow; what's my name; is Echo useful?",
        allowed_tools=[
            ToolDefinition(name="weather_live"),
            ToolDefinition(name="web_search"),
            ToolDefinition(name="safe_web_fetch"),
        ],
        tool_use_policy=ToolUsePolicy.REQUIRED,
        relevant_memory=[{"type": "profile", "content": "User name is Ty"}],
        approval=None,
        tool_outcomes=[],
        task_requirements=rows,
        requirement_states=states,
        task_run_id="task-incident",
        execution_profile="work",
        active_graph_node_ids=["finalize"],
    )
    diag = envelope.safe_diagnostics()
    assert diag["requirement_count"] == 4
    assert diag["verified_outcome_ids"] == []
    assert diag["tool_policy"] == "required"
    assert diag["completion_disposition"] != "complete"
    assert diag["completion_finalizable"] is False
    assert DecisionKind.CALL_TOOL in envelope.valid_next_actions
    assert DecisionKind.ANSWER not in envelope.valid_next_actions
    assert diag["requirement_states"]["req-edm"] == "pending"
    assert diag["requirement_states"]["req-cal"] == "pending"
    # Graph must not advertise finalize as the only active node.
    assert "finalize" not in diag["active_graph_node_ids"] or len(diag["active_graph_node_ids"]) > 1
    active = list(diag["active_graph_node_ids"] or [])
    assert active != ["finalize"]
    # Weather requirement nodes (or any requirement-*) should be active.
    assert any("requirement" in str(node) for node in active) or active


def test_rejection_message_does_not_echo_requirement_objectives() -> None:
    rows = _mixed_poisoned_requirements()
    states = initial_requirement_states(rows)
    for rid in states:
        states[rid] = states[rid].model_copy(
            update={"status": RequirementStatus.SATISFIED, "terminal_reason": "poison"}
        )
    compiler = ModelTurnEnvelopeCompiler()
    envelope = compiler.compile(
        project_id="",
        session_id="s1",
        turn_id="t1",
        execution_id="t1",
        request_id="r1",
        provider="lmstudio",
        model_id="test",
        assistant_identity=_identity(),
        objective="mixed",
        task_status="running",
        current_plan_step=None,
        collected_inputs={},
        missing_inputs=[],
        latest_user_relation="new_work",
        latest_user_message="mixed",
        allowed_tools=[ToolDefinition(name="weather_live")],
        tool_use_policy=ToolUsePolicy.REQUIRED,
        relevant_memory=[],
        approval=None,
        tool_outcomes=[],
        task_requirements=rows,
        requirement_states=states,
        task_run_id="task-1",
        execution_profile="work",
        active_graph_node_ids=["finalize"],
    )
    message = safe_decision_rejection_message(envelope)
    assert "Get the weather forecast for Edmonton tomorrow" not in message
    assert "Get the weather forecast for Calgary tomorrow" not in message
    assert "haven't verified" in message.casefold() or "need to verify" in message.casefold()
