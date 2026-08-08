"""Mixed multi-part turns must not finalize retrieval before ToolRuns exist."""
from __future__ import annotations

from agent.identity import EchoIdentityProjection
from agent.model_contracts import (
    DecisionKind,
    ToolDefinition,
    ToolUsePolicy,
    validate_agent_decision,
    AgentDecision,
    ToolCall,
)
from agent.model_control_plane import (
    ModelTurnEnvelopeCompiler,
    synthesize_mixed_requirement_partial,
)
from agent.research_runtime import (
    RequirementCompletionEvaluator,
    RequirementKind,
    RequirementState,
    RequirementStatus,
    TurnRequirement,
    demote_unverified_retrieval_states,
    seed_context_requirements,
    initial_requirement_states,
    choose_active_requirement,
)
from agent.execution_graph import (
    build_task_graph,
    reconcile_graph_state,
    GraphNodeKind,
    GraphNodeStatus,
)


def _identity() -> EchoIdentityProjection:
    return EchoIdentityProjection(
        assistant_name="Echo",
        product_name="EchoSpeak",
        soul_sha256="a" * 64,
        soul_rules="test",
    )


def _mixed_requirements() -> list[TurnRequirement]:
    return [
        TurnRequirement(
            requirement_id="req-vibe",
            kind=RequirementKind.ANSWER_ONLY,
            objective="Answer how the user is doing",
        ),
        TurnRequirement(
            requirement_id="req-weather",
            kind=RequirementKind.RETRIEVAL,
            objective="Get Edmonton weather",
            requested_fields=["weather_conditions"],
        ),
        TurnRequirement(
            requirement_id="req-search",
            kind=RequirementKind.RETRIEVAL,
            objective="Search ty0x7 and combine with known context",
            requested_fields=["search_hits"],
        ),
        TurnRequirement(
            requirement_id="req-name",
            kind=RequirementKind.MEMORY,
            objective="State the user's name",
        ),
    ]


def test_seed_never_satisfies_retrieval_without_evidence() -> None:
    reqs = _mixed_requirements()
    states = initial_requirement_states(reqs)
    # Poison: false satisfaction without ToolRuns.
    states["req-weather"] = states["req-weather"].model_copy(
        update={"status": RequirementStatus.SATISFIED, "terminal_reason": "bad"}
    )
    seeded = seed_context_requirements(
        reqs,
        states,
        relevant_memory=[{"type": "profile", "content": "User name is Ty"}],
        available_tool_names=["weather_live", "web_search", "safe_web_fetch"],
    )
    assert seeded["req-vibe"].status == RequirementStatus.SATISFIED
    assert seeded["req-name"].status == RequirementStatus.SATISFIED
    assert seeded["req-weather"].status == RequirementStatus.PENDING
    assert seeded["req-search"].status == RequirementStatus.PENDING
    demoted = demote_unverified_retrieval_states(reqs, seeded)
    assert demoted["req-weather"].status == RequirementStatus.PENDING


def test_completion_keeps_retrieval_open_and_preserves_satisfied_branches() -> None:
    reqs = _mixed_requirements()
    states = seed_context_requirements(
        reqs,
        initial_requirement_states(reqs),
        relevant_memory=[{"type": "profile", "content": "User name is Ty"}],
        available_tool_names=["weather_live", "web_search"],
    )
    verdict = RequirementCompletionEvaluator.evaluate(reqs, states)
    assert verdict.disposition.value == "pending"
    assert verdict.finalizable is False
    assert "req-weather" in verdict.unresolved_ids
    assert "req-search" in verdict.unresolved_ids
    assert "req-vibe" in verdict.satisfied_ids
    assert "req-name" in verdict.satisfied_ids


def test_envelope_allows_call_tool_while_retrieval_open() -> None:
    reqs = _mixed_requirements()
    states = seed_context_requirements(
        reqs,
        initial_requirement_states(reqs),
        relevant_memory=[{"type": "profile", "content": "User name is Ty"}],
        available_tool_names=["weather_live", "web_search", "safe_web_fetch"],
    )
    compiler = ModelTurnEnvelopeCompiler()
    envelope = compiler.compile(
        project_id="",
        session_id="s1",
        turn_id="t1",
        execution_id="t1",
        request_id="r1",
        provider="lmstudio",
        model_id="test-model",
        assistant_identity=_identity(),
        objective="mixed multi-part request",
        task_status="running",
        current_plan_step=None,
        collected_inputs={},
        missing_inputs=[],
        latest_user_relation="new_work",
        latest_user_message="How am I doing? Weather in Edmonton? Search ty0x7. What's my name?",
        allowed_tools=[
            ToolDefinition(name="weather_live", description="weather"),
            ToolDefinition(name="web_search", description="search"),
            ToolDefinition(name="safe_web_fetch", description="fetch"),
        ],
        tool_use_policy=ToolUsePolicy.REQUIRED,
        relevant_memory=[{"type": "profile", "content": "User name is Ty"}],
        approval=None,
        tool_outcomes=[],
        task_requirements=reqs,
        requirement_states=states,
        task_run_id="task-1",
        execution_profile="work",
    )
    diag = envelope.safe_diagnostics()
    assert diag["requirement_count"] == 4
    assert diag["completion_requirement_count"] == 4
    assert diag["requirement_projection_aligned"] is True
    assert DecisionKind.CALL_TOOL in envelope.valid_next_actions
    assert DecisionKind.ANSWER not in envelope.valid_next_actions
    assert envelope.task.requirement_states["req-weather"].status == RequirementStatus.PENDING
    # Model tool call must be legal.
    validated = validate_agent_decision(
        envelope,
        AgentDecision(
            kind=DecisionKind.CALL_TOOL,
            tool_call=ToolCall(id="call-1", name="weather_live", arguments={"location": "Edmonton"}),
        ),
    )
    assert validated.tool_call is not None
    assert validated.tool_call.name == "weather_live"


def test_graph_does_not_activate_finalize_while_retrieval_open() -> None:
    reqs = _mixed_requirements()
    states = seed_context_requirements(
        reqs,
        initial_requirement_states(reqs),
        relevant_memory=[{"type": "profile", "content": "User name is Ty"}],
        available_tool_names=["weather_live", "web_search"],
    )
    verdict = RequirementCompletionEvaluator.evaluate(reqs, states)
    graph = build_task_graph(task_run_id="task-1", requirements=reqs, budget=None)
    gstate = reconcile_graph_state(graph, None, requirement_states=states, completion=verdict, task_status="running")
    assert graph.finalization_node_id not in gstate.active_node_ids
    fin = gstate.node_states[graph.finalization_node_id]
    assert fin.status == GraphNodeStatus.PENDING
    open_req = [
        node.node_id
        for node in graph.nodes
        if node.kind == GraphNodeKind.REQUIREMENT
        and gstate.node_states[node.node_id].status in {GraphNodeStatus.READY, GraphNodeStatus.RUNNING}
    ]
    assert open_req


def test_choose_active_requirement_binds_weather_tool() -> None:
    reqs = _mixed_requirements()
    states = seed_context_requirements(
        reqs,
        initial_requirement_states(reqs),
        relevant_memory=[{"type": "profile", "content": "User name is Ty"}],
        available_tool_names=["weather_live", "web_search"],
    )
    active = choose_active_requirement(reqs, states, tool_name="weather_live")
    assert active is not None
    assert active.requirement_id == "req-weather"


def test_mixed_partial_synthesis_scopes_public_failure() -> None:
    reqs = _mixed_requirements()
    states = seed_context_requirements(
        reqs,
        initial_requirement_states(reqs),
        relevant_memory=[{"type": "profile", "content": "User name is Ty / ty0x7"}],
        available_tool_names=["weather_live", "web_search"],
    )
    states["req-weather"] = states["req-weather"].model_copy(
        update={"status": RequirementStatus.EXHAUSTED, "terminal_reason": "provider_unavailable"}
    )
    states["req-search"] = states["req-search"].model_copy(
        update={"status": RequirementStatus.EXHAUSTED, "terminal_reason": "provider_unavailable"}
    )
    compiler = ModelTurnEnvelopeCompiler()
    envelope = compiler.compile(
        project_id="",
        session_id="s1",
        turn_id="t1",
        execution_id="t1",
        request_id="r1",
        provider="lmstudio",
        model_id="test-model",
        assistant_identity=_identity(),
        objective="mixed",
        task_status="running",
        current_plan_step=None,
        collected_inputs={},
        missing_inputs=[],
        latest_user_relation="new_work",
        latest_user_message="mixed",
        allowed_tools=[ToolDefinition(name="web_search", description="search")],
        tool_use_policy=ToolUsePolicy.REQUIRED,
        relevant_memory=[{"type": "profile", "content": "User name is Ty / ty0x7"}],
        approval=None,
        tool_outcomes=[],
        task_requirements=reqs,
        requirement_states=states,
        task_run_id="task-1",
        execution_profile="work",
    )
    text = synthesize_mixed_requirement_partial(envelope)
    assert text
    assert "public-source" in text.casefold() or "lookup" in text.casefold()
    # Must not be a pure generic abandonment of personal parts.
    assert "Answer how the user is doing" in text or "stored context" in text.casefold() or "Ty" in text
