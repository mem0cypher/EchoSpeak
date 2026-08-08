from __future__ import annotations

import time

import pytest

from agent.generation_runtime import GenerationJob
from agent.media_jobs import (
    MediaJobBinding,
    MediaOperation,
    bind_media_job,
    project_generation_job,
)
from agent.model_conformance import ConformanceCaseResult, ModelConformanceReport
from agent.model_intelligence import profile_from_conformance
from agent.subagent_contracts import SubagentSpecification


def test_generation_job_projects_into_unified_media_contract() -> None:
    job = GenerationJob(
        id="generation-1",
        idempotency_key="stable-request",
        session_id="session-1",
        project_id="project-1",
        kind="image",
        provider_id="provider-1",
        model="model-1",
        prompt="A monochrome Echo portrait",
    )
    bound = bind_media_job(job, MediaJobBinding(
        execution_id="execution-1",
        task_run_id="task-1",
        requirement_id="req-1",
        attempt_id="attempt-1",
        tool_run_id="tool-run-1",
    ))
    projection = project_generation_job(bound)
    assert projection.operation == MediaOperation.IMAGE_GENERATION
    assert projection.binding.task_run_id == "task-1"
    assert projection.binding.tool_run_id == "tool-run-1"


def test_media_job_binding_cannot_be_reassigned() -> None:
    job = GenerationJob(
        idempotency_key="stable-request",
        session_id="session-1",
        project_id="project-1",
        kind="image",
        provider_id="provider-1",
        model="model-1",
        prompt="Echo",
        task_run_id="task-original",
    )
    with pytest.raises(RuntimeError, match="another runtime identity"):
        bind_media_job(job, MediaJobBinding(
            execution_id="execution-1",
            task_run_id="task-other",
            tool_run_id="tool-run-1",
        ))


def test_subagent_foundation_is_read_only_and_uses_one_selected_model() -> None:
    spec = SubagentSpecification(
        subagent_id="subagent-1",
        parent_task_run_id="task-1",
        graph_node_id="delegate-research",
        session_id="session-1",
        project_id="project-1",
        objective="Read two sources",
        selected_provider="lmstudio",
        selected_model_id="selected-session-model",
        permitted_capabilities=["research"],
    )
    assert spec.read_only is True
    assert spec.selected_model_id == "selected-session-model"
    with pytest.raises(ValueError, match="mutating or interactive"):
        SubagentSpecification(**{
            **spec.model_dump(),
            "permitted_capabilities": ["terminal"],
        })


def test_model_intelligence_is_advisory_and_does_not_select_a_fallback() -> None:
    report = ModelConformanceReport(
        provider="lmstudio",
        model_id="exact-model",
        family="qwen",
        template="chatml",
        adapter_version="1",
        capabilities={},
        cases=[
            ConformanceCaseResult(
                scenario_id="single_calculator", passed=True, decision="answer"
            ),
            ConformanceCaseResult(
                scenario_id="truthful_failed_tool", passed=False, decision="block"
            ),
        ],
        recommended_max_exposed_tools=1,
        created_at=time.time(),
    )
    profile = profile_from_conformance(report)
    assert profile.advisory_only is True
    assert profile.selected_model_unchanged is True
    assert profile.model_id == "exact-model"
    assert {item.capability: item.score for item in profile.observations}["single_tool_call"] == pytest.approx(1.0)
