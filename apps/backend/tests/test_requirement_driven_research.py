from __future__ import annotations

import json

import pytest

from agent.model_contracts import (
    AgentDecision,
    ApprovalState,
    DecisionKind,
    DecisionValidationError,
    ToolDefinition,
    ToolUsePolicy,
    validate_agent_decision,
)
from agent.model_control_plane import ModelTurnEnvelopeCompiler
from agent.research_runtime import (
    CompletionDisposition,
    RequirementCompletionEvaluator,
    RequirementKind,
    RequirementState,
    RequirementStatus,
    ResearchDepth,
    TurnRequirement,
    apply_evidence_to_state,
    begin_requirement_attempt,
    budget_for_depth,
    build_capability_snapshot,
    capability_fit_score,
    choose_active_requirement,
    compile_turn_requirements,
    evidence_from_tool_outcome,
    initial_requirement_states,
    seed_context_requirements,
)
from agent.safe_web_retrieval import SafeWebRetrievalError, _extract_page, fetch_public_page
from agent.state import ToolOutcome
from agent.task_runs import TaskRun
from agent.identity import EchoIdentityProjection


def _requirement(name: str, *, fields: list[str] | None = None) -> TurnRequirement:
    return TurnRequirement(
        requirement_id=f"req-{name}",
        kind=RequirementKind.RETRIEVAL,
        objective=name,
        requested_fields=fields or [],
    )


def _outcome(
    *, output: str, result_state: str = "data_found", covered_fields: list[str] | None = None
) -> ToolOutcome:
    return ToolOutcome(
        tool_name="web_search",
        run_id="run-1",
        execution_id="turn-1",
        session_id="session-1",
        turn_id="turn-1",
        success=True,
        status="success",
        execution_status="success",
        result_state=result_state,
        output=output,
        verification={
            "verified": True,
            "verifier_id": "fixture",
            "covered_fields": covered_fields or [],
        },
        provider="fixture",
    )


def test_independent_requirements_do_not_complete_from_one_success() -> None:
    first = _requirement("next match", fields=["match time"])
    second = _requirement("retailer price", fields=["price"])
    states = initial_requirement_states([first, second])
    state, attempt_id = begin_requirement_attempt(first, states[first.requirement_id], budget_for_depth("standard"))
    evidence = evidence_from_tool_outcome(
        _outcome(
            output="The authoritative schedule lists the next match time as 19:00 tomorrow.",
            covered_fields=["match time"],
        ),
        requirement=first,
        attempt_id=attempt_id,
    )
    states[first.requirement_id] = apply_evidence_to_state(first, state, evidence)
    verdict = RequirementCompletionEvaluator.evaluate([first, second], states)
    assert states[first.requirement_id].status == RequirementStatus.SATISFIED
    assert states[second.requirement_id].status == RequirementStatus.PENDING
    assert verdict.finalizable is False
    assert verdict.unresolved_ids == [second.requirement_id]


def test_successful_execution_without_requested_information_is_weak() -> None:
    requirement = _requirement("retailer price", fields=["price", "availability"])
    state, attempt_id = begin_requirement_attempt(
        requirement,
        initial_requirement_states([requirement])[requirement.requirement_id],
        budget_for_depth(ResearchDepth.STANDARD),
    )
    evidence = evidence_from_tool_outcome(
        _outcome(output="The search provider returned a retailer category page."),
        requirement=requirement,
        attempt_id=attempt_id,
    )
    updated = apply_evidence_to_state(requirement, state, evidence)
    assert evidence.usable is False
    assert evidence.diagnostic_code == "requested_fields_not_covered"
    assert updated.status == RequirementStatus.WEAK


def test_search_orchestration_placeholder_is_not_information_success() -> None:
    requirement = _requirement("current result")
    evidence = evidence_from_tool_outcome(
        _outcome(output="(search expanded)"),
        requirement=requirement,
        attempt_id="attempt-1",
    )
    assert evidence.usable is False
    assert evidence.diagnostic_code == "no_usable_information"


def test_exhausted_requirements_allow_only_honest_partial_finalization() -> None:
    complete = _requirement("complete")
    failed = _requirement("failed")
    states = initial_requirement_states([complete, failed])
    states[complete.requirement_id] = RequirementState(
        requirement_id=complete.requirement_id,
        status=RequirementStatus.SATISFIED,
        # Retrieval satisfaction requires ToolRun/evidence identity.
        tool_run_ids=["tool-run-complete"],
        evidence_ids=["evidence-complete"],
    )
    states[failed.requirement_id] = RequirementState(
        requirement_id=failed.requirement_id,
        status=RequirementStatus.EXHAUSTED,
        terminal_reason="bounded_research_budget_exhausted",
    )
    verdict = RequirementCompletionEvaluator.evaluate([complete, failed], states)
    assert verdict.disposition == CompletionDisposition.PARTIAL
    assert verdict.finalizable is True
    assert verdict.terminal_incomplete_ids == [failed.requirement_id]


def test_satisfied_requirement_is_not_selected_or_rerun() -> None:
    complete = _requirement("complete")
    pending = _requirement("pending")
    states = initial_requirement_states([complete, pending])
    states[complete.requirement_id] = states[complete.requirement_id].model_copy(
        update={"status": RequirementStatus.SATISFIED}
    )
    assert choose_active_requirement([complete, pending], states) == pending
    with pytest.raises(Exception, match="Satisfied requirements cannot be rerun"):
        begin_requirement_attempt(
            complete, states[complete.requirement_id], budget_for_depth("standard")
        )


def test_empty_authorized_memory_projection_is_terminally_unavailable() -> None:
    requirement = TurnRequirement(
        requirement_id="req-memory",
        kind=RequirementKind.MEMORY,
        objective="Recall my preferred airline",
    )
    states = seed_context_requirements(
        [requirement],
        initial_requirement_states([requirement]),
        relevant_memory=[],
        available_tool_names=[],
    )
    assert states[requirement.requirement_id].status == RequirementStatus.UNAVAILABLE
    verdict = RequirementCompletionEvaluator.evaluate([requirement], states)
    assert verdict.disposition == CompletionDisposition.PARTIAL
    assert verdict.finalizable is True


def test_legacy_taskrun_is_read_compatibly_but_owns_new_requirement_state() -> None:
    task = TaskRun.model_validate({
        "schema_version": 1,
        "id": "task-1",
        "session_id": "session-1",
        "objective": "Find a current price",
        "permitted_capabilities": ["research"],
    })
    assert task.schema_version >= 3
    assert len(task.requirements) == 1
    requirement_id = task.requirements[0].requirement_id
    assert task.requirement_states[requirement_id].status == RequirementStatus.PENDING


def test_existing_answer_gate_consumes_requirement_completion_verdict() -> None:
    requirement = _requirement("one")
    states = initial_requirement_states([requirement])
    compiler = ModelTurnEnvelopeCompiler()
    identity = EchoIdentityProjection(
        assistant_name="Echo",
        product_name="EchoSpeak",
        soul_sha256="a" * 64,
        soul_rules="test identity",
    )
    envelope = compiler.compile(
        project_id="",
        session_id="session-1",
        turn_id="turn-1",
        execution_id="turn-1",
        request_id="request-1",
        provider="lmstudio",
        model_id="test-model",
        assistant_identity=identity,
        objective="one",
        task_status="running",
        current_plan_step=None,
        collected_inputs={},
        missing_inputs=[],
        latest_user_relation="new_work",
        latest_user_message="one",
        allowed_tools=[ToolDefinition(name="web_search")],
        tool_use_policy=ToolUsePolicy.REQUIRED,
        relevant_memory=[],
        approval=ApprovalState(),
        tool_outcomes=[],
        task_requirements=[requirement],
        requirement_states=states,
    )
    assert DecisionKind.ANSWER not in envelope.valid_next_actions
    with pytest.raises(DecisionValidationError, match="not valid|not finalizable"):
        validate_agent_decision(
            envelope,
            AgentDecision(kind=DecisionKind.ANSWER, message="premature"),
        )


def test_capability_snapshot_distinguishes_safe_fetch_from_browser() -> None:
    snapshot = build_capability_snapshot(
        [
            ToolDefinition(name="safe_web_fetch"),
            ToolDefinition(name="browse_task", approval_required=True),
        ],
        inventory_revision=7,
        project_id="",
        session_id="session-1",
    )
    rows = {item.tool_name: item for item in snapshot.capabilities}
    assert rows["safe_web_fetch"].read_only is True
    assert rows["safe_web_fetch"].interactive is False
    assert rows["browse_task"].interactive is True
    assert rows["browse_task"].approval_required is True


def test_capability_fit_rejects_unrelated_mutation_and_prefers_specialized_data() -> None:
    requirement = _requirement("Find the next match")
    snapshot = build_capability_snapshot(
        [
            ToolDefinition(name="sports_live"),
            ToolDefinition(name="web_search"),
            ToolDefinition(name="file_delete", mutating=True, approval_required=True),
        ],
        inventory_revision=8,
        project_id="",
        session_id="session-1",
    )
    rows = {item.tool_name: item for item in snapshot.capabilities}
    assert capability_fit_score("sports_live", requirement, rows["sports_live"]) > capability_fit_score(
        "web_search", requirement, rows["web_search"]
    )
    assert capability_fit_score("file_delete", requirement, rows["file_delete"]) == 0


def test_safe_fetch_blocks_loopback_before_transport() -> None:
    with pytest.raises(SafeWebRetrievalError) as exc:
        fetch_public_page("http://127.0.0.1/private")
    assert exc.value.code == "destination_not_public"


def test_structured_page_extraction_keeps_jsonld_semantics_and_tables() -> None:
    html = b"""
    <html><head><title>Store</title>
    <meta property="og:updated_time" content="2026-07-18" />
    <script type="application/ld+json">{"@type":"Product","name":"Paint","offers":{"price":"4.99"}}</script>
    </head><body><div itemscope itemtype="https://schema.org/Product"><span itemprop="name">Paint</span></div>
    <table><tr><th>Price</th><td>$4.99</td></tr></table></body></html>
    """
    title, text, metadata, json_ld, semantic, tables = _extract_page(
        html, content_type="text/html; charset=utf-8", max_text_chars=5000
    )
    assert title == "Store"
    assert "Paint" in text
    assert metadata["og:updated_time"] == "2026-07-18"
    assert json_ld[0]["offers"]["price"] == "4.99"
    assert any(item.get("itemprop") == "name" for item in semantic)
    assert tables[0][0] == ["Price", "$4.99"]


def test_model_proposed_requirements_receive_stable_runtime_ids() -> None:
    rows = compile_turn_requirements(
        [
            {"kind": "memory", "objective": "Recall my home city"},
            {"kind": "retrieval", "objective": "Find a flight from that city"},
        ],
        objective="Recall my city and find a flight",
        capabilities=["memory", "research"],
    )
    assert len({item.requirement_id for item in rows}) == 2
    assert all(item.requirement_id.startswith("req-") for item in rows)
