from __future__ import annotations

from pathlib import Path

import pytest

from agent.core import EchoSpeakAgent
from agent.identity import compile_echo_identity
from agent.research_runtime import (
    CapabilityDescriptor,
    RequirementKind,
    RequirementState,
    RequirementStatus,
    ResearchBudgetPolicy,
    RepeatedRecoveryStrategy,
    TaskRunNextAction,
    TaskRunScheduler,
    TurnRequirement,
    begin_requirement_attempt,
    evidence_from_tool_outcome,
    extract_weather_locations,
    reopen_incomplete_requirements,
    rekind_misclassified_live_requirements,
    apply_evidence_to_state,
)
from agent.retrieval_contracts import plan_research_query
from agent.state import ToolOutcome
from agent.semantic_runtime import parse_deterministic_continuation_command
from agent.task_runs import TaskRunStore


def test_city_region_is_one_weather_requirement() -> None:
    requirement = TurnRequirement(
        kind=RequirementKind.RETRIEVAL,
        objective="Weather in Edmonton, Alberta",
        entities=["Edmonton", "Alberta"],
    )
    assert extract_weather_locations(requirement) == ["Edmonton, Alberta"]


def test_independent_city_region_pairs_remain_independent() -> None:
    requirement = TurnRequirement(
        kind=RequirementKind.RETRIEVAL,
        objective="Weather in Edmonton, Alberta and Calgary, Alberta",
    )
    assert extract_weather_locations(requirement) == [
        "Edmonton, Alberta",
        "Calgary, Alberta",
    ]


def test_latest_software_release_and_citation_require_retrieval() -> None:
    proposed = TurnRequirement(
        kind=RequirementKind.ANSWER_ONLY,
        objective="Find the latest stable OpenAI SDK release and cite the source",
    )
    repaired = rekind_misclassified_live_requirements([proposed])[0]
    assert repaired.kind == RequirementKind.RETRIEVAL
    assert {"version", "source"}.issubset(set(repaired.requested_fields))


def test_internal_execution_envelope_cannot_become_provider_query() -> None:
    with pytest.raises(ValueError, match="internal execution-envelope"):
        plan_research_query(
            "Latest user message: FIFA result Task objective: SDK release"
        )


def test_search_cache_identity_is_requirement_scoped() -> None:
    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    first = agent._scoped_search_fingerprint(
        "latest stable SDK release",
        task_run_id="task-1",
        requirement_id="req-sdk",
        freshness_class="current",
    )
    same = agent._scoped_search_fingerprint(
        "stable latest release SDK",
        task_run_id="task-1",
        requirement_id="req-sdk",
        freshness_class="current",
    )
    other = agent._scoped_search_fingerprint(
        "latest stable SDK release",
        task_run_id="task-1",
        requirement_id="req-fifa",
        freshness_class="current",
    )
    assert first == same
    assert first != other


@pytest.mark.parametrize(
    ("text", "modifier"),
    [
        ("Continue", ""),
        ("continue and use another source", "and use another source"),
        (
            "try again for the unfinished parts",
            "for the unfinished parts",
        ),
    ],
)
def test_continuation_prefix_preserves_bounded_modifier(
    text: str, modifier: str
) -> None:
    accepted, observed_modifier = parse_deterministic_continuation_command(text)
    assert accepted is True
    assert observed_modifier == modifier


def test_tool_outcome_projects_typed_result_without_replacing_legacy_text() -> None:
    outcome = ToolOutcome(
        tool_name="weather_live",
        success=True,
        status="success",
        execution_status="success",
        result_state="data_found",
        output="legacy weather rendering",
        requirement_id="req-weather",
        attempt_id="attempt-1",
        verification={
            "verified": True,
            "structured_values": {"temperature_c": 15},
            "sources": [{"url": "https://example.invalid"}],
            "query_plan": {"query_plan_id": "plan-1"},
        },
    )
    assert outcome.output == "legacy weather rendering"
    assert outcome.result is not None
    assert outcome.result.data == {"temperature_c": 15}
    assert outcome.result.requirement_id == "req-weather"
    assert outcome.result.semantic_fingerprint == "plan-1"


def test_soul_projection_reserves_every_critical_section() -> None:
    soul = (
        Path(__file__).resolve().parents[1] / "SOUL.md"
    ).read_text(encoding="utf-8")
    projection = compile_echo_identity(
        soul, provider="lmstudio", model_id="fixture"
    )
    assert len(projection.soul_rules) <= 3200
    for section in (
        "Identity",
        "Personality",
        "Voice",
        "Thinking And Judgment",
        "Memory And Continuity",
        "Capabilities And Tools",
        "Response Standard",
    ):
        assert f"## {section}" in projection.soul_rules


def test_scheduler_owns_active_requirement_and_capability() -> None:
    requirement = TurnRequirement(
        requirement_id="req-weather",
        kind=RequirementKind.RETRIEVAL,
        objective="Current Edmonton weather",
        requested_fields=["temperature_c"],
    )
    state = RequirementState(
        requirement_id=requirement.requirement_id,
        missing_fields=["temperature_c"],
    )
    decision = TaskRunScheduler.advance(
        [requirement],
        {requirement.requirement_id: state},
        budget=ResearchBudgetPolicy(),
        capabilities=[
            CapabilityDescriptor(
                capability_id="weather",
                tool_name="weather_live",
                result_fields=["temperature_c"],
                supported_operations=["structured_live_lookup"],
            ),
            CapabilityDescriptor(
                capability_id="search",
                tool_name="web_search",
                supported_operations=["discovery"],
            ),
        ],
    )
    assert decision.next_action == TaskRunNextAction.RUN_TOOL
    assert decision.active_requirement_id == requirement.requirement_id
    assert decision.preferred_tool_name == "weather_live"


def test_taskrun_update_can_clear_stale_liveness_projection(tmp_path: Path) -> None:
    requirement = TurnRequirement(
        requirement_id="req-weather",
        kind=RequirementKind.RETRIEVAL,
        objective="Current Edmonton weather",
    )
    state = RequirementState(requirement_id=requirement.requirement_id)
    decision = TaskRunScheduler.advance(
        [requirement],
        {requirement.requirement_id: state},
        budget=ResearchBudgetPolicy(),
        capabilities=[CapabilityDescriptor(
            capability_id="weather",
            tool_name="weather_live",
            supported_operations=["structured_live_lookup"],
        )],
    )
    store = TaskRunStore(tmp_path / "task-runs.json")
    task = store.create(
        session_id="session-1",
        objective=requirement.objective,
        requirements=[requirement],
        requirement_states={requirement.requirement_id: state},
        permitted_capabilities=["research"],
        liveness_decision=decision,
    )
    updated = store.update(
        task.id,
        session_id=task.session_id,
        project_id=task.project_id,
        expected_revision=task.revision,
        clear_fields=("liveness_decision",),
    )
    assert updated.liveness_decision is None


def test_continue_reopens_only_terminal_incomplete_requirement() -> None:
    completed = TurnRequirement(
        requirement_id="req-done",
        kind=RequirementKind.RETRIEVAL,
        objective="Current Edmonton weather",
    )
    unfinished = TurnRequirement(
        requirement_id="req-open",
        kind=RequirementKind.RETRIEVAL,
        objective="Next Canada match",
    )
    states = {
        "req-done": RequirementState(
            requirement_id="req-done",
            status=RequirementStatus.SATISFIED,
            evidence_ids=["evidence-weather"],
        ),
        "req-open": RequirementState(
            requirement_id="req-open",
            status=RequirementStatus.EXHAUSTED,
            attempt_ids=["attempt-old"],
            epoch_attempt_ids=["attempt-old"],
            evidence_ids=["evidence-schedule"],
        ),
    }
    reopened, ids = reopen_incomplete_requirements(
        [completed, unfinished],
        states,
        recovery_epoch=2,
    )
    assert ids == ["req-open"]
    assert reopened["req-done"].status == RequirementStatus.SATISFIED
    assert reopened["req-open"].status == RequirementStatus.WEAK
    assert reopened["req-open"].evidence_ids == ["evidence-schedule"]
    assert reopened["req-open"].epoch_attempt_ids == []


def test_recovery_epoch_rejects_exact_strategy_repetition() -> None:
    requirement = TurnRequirement(
        requirement_id="req-search",
        kind=RequirementKind.RETRIEVAL,
        objective="Latest stable SDK version",
    )
    state = RequirementState(requirement_id="req-search")
    updated, _ = begin_requirement_attempt(
        requirement,
        state,
        ResearchBudgetPolicy(),
        recovery_epoch=0,
        attempt_fingerprint="same",
    )
    with pytest.raises(RepeatedRecoveryStrategy):
        begin_requirement_attempt(
            requirement,
            updated.model_copy(update={"status": RequirementStatus.WEAK}),
            ResearchBudgetPolicy(),
            recovery_epoch=0,
            attempt_fingerprint="same",
        )


def test_authoritative_verified_absence_can_satisfy_next_item() -> None:
    requirement = TurnRequirement(
        requirement_id="req-next",
        kind=RequirementKind.RETRIEVAL,
        objective="Find the next scheduled Canada match",
        entities=["Canada"],
        requested_fields=["opponent", "event_time", "venue"],
    )
    outcome = ToolOutcome(
        tool_name="sports_live",
        success=True,
        status="success",
        execution_status="success",
        result_state="verified_absence",
        output=(
            "result_state=verified_absence Canada has no next scheduled match "
            "in the covered official competition schedule."
        ),
        requirement_id="req-next",
        attempt_id="attempt-1",
        verification={
            "verified": True,
            "verified_absence": True,
            "absence_scope": "Canada official competition schedule",
            "authoritative_source": "https://example.com/official-schedule",
            "covered_fields": [],
        },
    )
    evidence = evidence_from_tool_outcome(
        outcome,
        requirement=requirement,
        attempt_id="attempt-1",
    )
    state = RequirementState(
        requirement_id="req-next",
        status=RequirementStatus.ACTIVE,
        attempt_ids=["attempt-1"],
        epoch_attempt_ids=["attempt-1"],
    )
    updated = apply_evidence_to_state(
        requirement,
        state,
        evidence,
        budget=ResearchBudgetPolicy(),
    )
    assert evidence.usable is True
    assert updated.status == RequirementStatus.SATISFIED
    assert updated.terminal_reason == "authoritative_verified_absence"


def test_verified_absence_rejects_uncitable_source_label() -> None:
    requirement = TurnRequirement(
        requirement_id="req-next",
        kind=RequirementKind.RETRIEVAL,
        objective="Find the next scheduled Canada match",
        entities=["Canada"],
        requested_fields=["event_time"],
    )
    outcome = ToolOutcome(
        tool_name="sports_live",
        success=True,
        status="success",
        execution_status="success",
        result_state="verified_absence",
        output="result_state=verified_absence Canada has no next scheduled match.",
        requirement_id="req-next",
        attempt_id="attempt-1",
        verification={
            "verified": True,
            "verified_absence": True,
            "absence_scope": "Canada official competition schedule",
            "authoritative_source": "official schedule",
        },
    )
    evidence = evidence_from_tool_outcome(
        outcome,
        requirement=requirement,
        attempt_id="attempt-1",
    )
    assert evidence.usable is False
    assert evidence.diagnostic_code == "verified_absence_contract_incomplete"
