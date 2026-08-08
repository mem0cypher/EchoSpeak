"""Successful weather_live results must satisfy requirements and accumulate multi-city coverage."""
from __future__ import annotations

import json

from agent.identity import EchoIdentityProjection
from agent.model_control_plane import (
    ModelTurnEnvelopeCompiler,
    synthesize_structured_evidence_answer,
)
from agent.model_contracts import ToolDefinition, ToolUsePolicy
from agent.research_runtime import (
    RequirementCompletionEvaluator,
    RequirementKind,
    RequirementStatus,
    ResearchDepth,
    TurnRequirement,
    apply_evidence_to_state,
    begin_requirement_attempt,
    budget_for_depth,
    build_capability_snapshot,
    compile_turn_requirements,
    evidence_from_tool_outcome,
    extract_weather_locations,
    format_weather_live_summary,
    initial_requirement_states,
    next_recovery_strategy,
    recommended_tools_for_recovery,
    verify_tool_result_semantics,
)
from agent.state import ToolOutcome
from agent.turn_understanding import (
    TurnInterpretation,
    TurnRelation,
    enrich_multi_location_weather_interpretation,
)


def _weather_output(city: str, *, temperature_c: float = 21.0) -> str:
    payload = {
        "ok": True,
        "source": "open-meteo",
        "location": {
            "name": city,
            "admin1": "Alberta",
            "country": "Canada",
            "latitude": 53.5,
            "longitude": -113.5,
        },
        "observed_at": "2026-07-18T12:00",
        "timezone": "America/Edmonton",
        "temperature_c": temperature_c,
        "apparent_temperature_c": temperature_c - 1,
        "relative_humidity_percent": 40,
        "precipitation_mm": 0,
        "weather_code": 1,
        "wind_speed_kmh": 12.0,
    }
    return "[WEATHER_LIVE] " + json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _outcome(output: str, *, run_id: str = "run-1") -> ToolOutcome:
    return ToolOutcome(
        tool_name="weather_live",
        run_id=run_id,
        execution_id="turn-1",
        session_id="session-1",
        turn_id="turn-1",
        success=True,
        status="success",
        execution_status="success",
        result_state="data_found",
        output=output,
        verification={
            "verified": True,
            "verifier_id": "fixture",
            "covered_fields": [
                "ok", "source", "location", "temperature_c", "weather_code", "observed_at",
            ],
        },
        provider="open-meteo",
    )


def test_extract_weather_locations_from_multi_city_objective() -> None:
    places = extract_weather_locations("Get the weather for Edmonton and Calgary")
    assert places == ["Edmonton", "Calgary"]


def test_compile_splits_multi_city_weather_into_independent_requirements() -> None:
    rows = compile_turn_requirements(
        [{
            "kind": "retrieval",
            "objective": "Get the weather for Edmonton and Calgary",
            "entities": ["Edmonton", "Calgary"],
        }],
        objective="Get the weather for Edmonton and Calgary",
        capabilities=["research", "live_weather"],
    )
    assert len(rows) == 2
    locations = sorted(item.location for item in rows)
    assert locations == ["Calgary", "Edmonton"]
    assert all(item.requested_fields == ["weather_conditions"] for item in rows)
    assert all(item.entities == [item.location] for item in rows)


def test_weather_live_schema_covers_weather_conditions_field() -> None:
    requirement = TurnRequirement(
        requirement_id="req-weather",
        kind=RequirementKind.RETRIEVAL,
        objective="Weather for Edmonton",
        entities=["Edmonton"],
        location="Edmonton",
        requested_fields=["weather_conditions"],
    )
    output = _weather_output("Edmonton")
    relevant, covered = verify_tool_result_semantics(
        output,
        requirement,
        {"covered_fields": ["temperature_c", "location", "weather_code"]},
        tool_name="weather_live",
    )
    assert relevant is True
    assert "weather_conditions" in covered

    evidence = evidence_from_tool_outcome(
        _outcome(output),
        requirement=requirement,
        attempt_id="attempt-1",
    )
    assert evidence.usable is True
    assert evidence.diagnostic_code == "verified_information_found"
    assert "weather_conditions" in evidence.covered_fields
    assert evidence.matched_entities == ["Edmonton"]


def test_single_city_weather_success_satisfies_requirement() -> None:
    requirement = TurnRequirement(
        requirement_id="req-weather",
        kind=RequirementKind.RETRIEVAL,
        objective="Weather for Edmonton",
        entities=["Edmonton"],
        location="Edmonton",
        requested_fields=["weather_conditions"],
    )
    state, attempt_id = begin_requirement_attempt(
        requirement,
        initial_requirement_states([requirement])[requirement.requirement_id],
        budget_for_depth(ResearchDepth.FAST),
    )
    evidence = evidence_from_tool_outcome(
        _outcome(_weather_output("Edmonton")),
        requirement=requirement,
        attempt_id=attempt_id,
    )
    updated = apply_evidence_to_state(
        requirement,
        state,
        evidence,
        budget=budget_for_depth(ResearchDepth.FAST),
    )
    assert updated.status == RequirementStatus.SATISFIED
    assert updated.terminal_reason == "verified_evidence_covered_requirement"
    verdict = RequirementCompletionEvaluator.evaluate([requirement], {requirement.requirement_id: updated})
    assert verdict.finalizable is True
    assert verdict.disposition.value == "complete"


def test_multi_city_accumulation_without_split() -> None:
    """If a combined multi-entity requirement remains, accumulate both ToolRuns."""
    requirement = TurnRequirement(
        requirement_id="req-both",
        kind=RequirementKind.RETRIEVAL,
        objective="Weather for Edmonton and Calgary",
        entities=["Edmonton", "Calgary"],
        requested_fields=["weather_conditions"],
    )
    budget = budget_for_depth(ResearchDepth.STANDARD)
    state, attempt_1 = begin_requirement_attempt(
        requirement,
        initial_requirement_states([requirement])[requirement.requirement_id],
        budget,
    )
    ev1 = evidence_from_tool_outcome(
        _outcome(_weather_output("Edmonton", temperature_c=22), run_id="run-edm"),
        requirement=requirement,
        attempt_id=attempt_1,
    )
    assert ev1.usable is True
    assert ev1.matched_entities == ["Edmonton"]
    state = apply_evidence_to_state(requirement, state, ev1, budget=budget)
    assert state.status == RequirementStatus.WEAK
    assert "Edmonton" in state.covered_entities
    assert state.terminal_reason == "entities_incomplete"

    state, attempt_2 = begin_requirement_attempt(requirement, state, budget)
    ev2 = evidence_from_tool_outcome(
        _outcome(_weather_output("Calgary", temperature_c=18), run_id="run-cal"),
        requirement=requirement,
        attempt_id=attempt_2,
    )
    assert ev2.usable is True
    assert ev2.matched_entities == ["Calgary"]
    state = apply_evidence_to_state(requirement, state, ev2, budget=budget)
    assert state.status == RequirementStatus.SATISFIED
    assert set(state.covered_entities) == {"Edmonton", "Calgary"}
    assert set(state.tool_run_ids) == {"run-edm", "run-cal"}


def test_split_requirements_each_satisfy_from_matching_city_only() -> None:
    rows = compile_turn_requirements(
        [{
            "kind": "retrieval",
            "objective": "Get the weather for Edmonton and Calgary",
        }],
        objective="Get the weather for Edmonton and Calgary",
        capabilities=["live_weather", "research"],
    )
    states = initial_requirement_states(rows)
    budget = budget_for_depth(ResearchDepth.FAST)
    for requirement in rows:
        state, attempt_id = begin_requirement_attempt(
            requirement, states[requirement.requirement_id], budget
        )
        city = requirement.location
        evidence = evidence_from_tool_outcome(
            _outcome(_weather_output(city), run_id=f"run-{city.casefold()}"),
            requirement=requirement,
            attempt_id=attempt_id,
        )
        # Wrong city must not satisfy a place-scoped requirement.
        if city == "Edmonton":
            wrong = evidence_from_tool_outcome(
                _outcome(_weather_output("Calgary"), run_id="run-wrong"),
                requirement=requirement,
                attempt_id=attempt_id,
            )
            assert wrong.usable is False
            assert wrong.diagnostic_code == "requirement_mismatch"
        states[requirement.requirement_id] = apply_evidence_to_state(
            requirement, state, evidence, budget=budget
        )
        assert states[requirement.requirement_id].status == RequirementStatus.SATISFIED

    verdict = RequirementCompletionEvaluator.evaluate(rows, states)
    assert verdict.finalizable is True
    assert verdict.disposition.value == "complete"
    assert not verdict.unresolved_ids


def test_weather_capability_declares_real_output_fields() -> None:
    snapshot = build_capability_snapshot(
        [ToolDefinition(name="weather_live")],
        inventory_revision=1,
        project_id="",
        session_id="s1",
    )
    fields = set(snapshot.capabilities[0].result_fields)
    assert "weather_conditions" in fields
    assert "temperature_c" in fields
    assert "location" in fields
    assert "observed_at" in fields


def test_recovery_adapts_after_partial_entity_coverage() -> None:
    requirement = TurnRequirement(
        requirement_id="req-both",
        kind=RequirementKind.RETRIEVAL,
        objective="Weather for Edmonton and Calgary",
        entities=["Edmonton", "Calgary"],
        requested_fields=["weather_conditions"],
    )
    budget = budget_for_depth(ResearchDepth.STANDARD)
    state, attempt_1 = begin_requirement_attempt(
        requirement,
        initial_requirement_states([requirement])[requirement.requirement_id],
        budget,
        available_tools=["weather_live", "web_search", "safe_web_fetch"],
    )
    assert state.last_strategy == "primary_capability"
    ev1 = evidence_from_tool_outcome(
        _outcome(_weather_output("Edmonton"), run_id="run-edm"),
        requirement=requirement,
        attempt_id=attempt_1,
    )
    state = apply_evidence_to_state(
        requirement,
        state,
        ev1,
        budget=budget,
        available_tools=["weather_live", "web_search", "safe_web_fetch"],
    )
    assert state.status == RequirementStatus.WEAK
    assert state.missing_entities == ["Calgary"]
    assert "Edmonton" in state.covered_entities
    assert state.evidence_passages
    # Next recovery must target the missing city, not blindly re-loop without guidance.
    strategy = next_recovery_strategy(state, requirement)
    assert strategy == "entity_argument_correction"
    tools = recommended_tools_for_recovery(
        strategy,
        requirement=requirement,
        available_tools=["weather_live", "web_search", "safe_web_fetch"],
    )
    assert tools[0] == "weather_live"


def test_partial_budget_exhaustion_preserves_successful_weather_in_answer() -> None:
    requirement = TurnRequirement(
        requirement_id="req-weather",
        kind=RequirementKind.RETRIEVAL,
        objective="Weather for Edmonton",
        entities=["Edmonton"],
        location="Edmonton",
        requested_fields=["weather_conditions", "price"],  # price cannot come from weather
    )
    budget = budget_for_depth(ResearchDepth.FAST)  # max 2 attempts
    states = initial_requirement_states([requirement])
    state = states[requirement.requirement_id]
    for index in range(2):
        state, attempt_id = begin_requirement_attempt(requirement, state, budget)
        evidence = evidence_from_tool_outcome(
            _outcome(_weather_output("Edmonton", temperature_c=19.5), run_id=f"run-{index}"),
            requirement=requirement,
            attempt_id=attempt_id,
        )
        # weather covers weather_conditions but not price → weak then partial exhaust
        state = apply_evidence_to_state(requirement, state, evidence, budget=budget)
    assert "weather_conditions" in state.covered_fields
    assert "price" in state.missing_fields
    assert state.evidence_passages
    assert state.status == RequirementStatus.EXHAUSTED
    assert state.terminal_reason == "partial_verified_evidence_budget_exhausted"

    outcome = _outcome(_weather_output("Edmonton", temperature_c=19.5), run_id="run-0")
    compiler = ModelTurnEnvelopeCompiler()
    identity = EchoIdentityProjection(
        assistant_name="Echo", product_name="EchoSpeak", soul_sha256="a" * 64, soul_rules="t",
    )
    envelope = compiler.compile(
        project_id="",
        session_id="s1",
        turn_id="t1",
        execution_id="t1",
        request_id="r1",
        provider="lmstudio",
        model_id="test",
        assistant_identity=identity,
        objective=requirement.objective,
        task_status="running",
        current_plan_step=None,
        collected_inputs={},
        missing_inputs=[],
        latest_user_relation="new_work",
        latest_user_message=requirement.objective,
        allowed_tools=[ToolDefinition(name="weather_live")],
        tool_use_policy=ToolUsePolicy.REQUIRED,
        relevant_memory=[],
        approval=None,
        tool_outcomes=[outcome],
        task_requirements=[requirement],
        requirement_states={requirement.requirement_id: state},
        task_run_id="task-1",
        execution_profile="work",
    )
    answer = synthesize_structured_evidence_answer(envelope)
    assert "Edmonton" in answer
    assert "19.5" in answer or "19.5°C" in format_weather_live_summary(_weather_output("Edmonton", temperature_c=19.5))
    assert "19.5" in answer
    # Must not claim total failure when structured weather succeeded.
    assert "could not complete a reliable" not in answer.casefold() or "19.5" in answer
    assert "missing fields" in answer.casefold() or "price" in answer.casefold()


def test_turn_understanding_enrichment_splits_multi_city_weather() -> None:
    coarse = TurnInterpretation(
        relation=TurnRelation.NEW_TASK,
        proposed_objective="Get the weather for Edmonton and Calgary",
        requested_capabilities=["live_weather", "research"],
        requirements=[
            TurnRequirement(
                kind=RequirementKind.RETRIEVAL,
                objective="Get the weather for Edmonton and Calgary",
            )
        ],
        confidence=0.8,
    )
    enriched = enrich_multi_location_weather_interpretation(
        coarse,
        latest_user_message="Get the weather for Edmonton and Calgary",
    )
    assert len(enriched.requirements) == 2
    places = sorted(item.location for item in enriched.requirements)
    assert places == ["Calgary", "Edmonton"]
