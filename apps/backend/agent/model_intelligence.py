"""Advisory model-intelligence profiles derived from conformance evidence.

Profiles inform UI and capacity planning only. They never change the selected
Session model, invoke a fallback model, or bypass the canonical control plane.
"""
from __future__ import annotations

import time
from pydantic import BaseModel, ConfigDict, Field

from agent.model_conformance import ModelConformanceReport


class ModelCapabilityObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str
    score: float = Field(ge=0.0, le=1.0)
    passed_cases: list[str] = Field(default_factory=list)
    failed_cases: list[str] = Field(default_factory=list)
    evidence_count: int = Field(ge=0)


class ModelIntelligenceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    provider: str
    model_id: str
    family: str
    adapter_version: str
    advisory_only: bool = True
    selected_model_unchanged: bool = True
    recommended_max_exposed_tools: int = Field(ge=0)
    observations: list[ModelCapabilityObservation] = Field(default_factory=list)
    source_report_created_at: float
    created_at: float = Field(default_factory=time.time)


_CAPABILITY_CASES = {
    "single_tool_call": {"single_calculator", "multiple_arguments", "returned_outcome"},
    "sequential_tool_use": {"two_sequential_tools"},
    "truthful_failure": {"truthful_failed_tool"},
}


def profile_from_conformance(report: ModelConformanceReport) -> ModelIntelligenceProfile:
    by_id = {item.scenario_id: item for item in report.cases}
    observations: list[ModelCapabilityObservation] = []
    for capability, case_ids in _CAPABILITY_CASES.items():
        available = [by_id[item] for item in sorted(case_ids) if item in by_id]
        passed = [item.scenario_id for item in available if item.passed]
        failed = [item.scenario_id for item in available if not item.passed]
        observations.append(ModelCapabilityObservation(
            capability=capability,
            score=(len(passed) / len(available)) if available else 0.0,
            passed_cases=passed,
            failed_cases=failed,
            evidence_count=len(available),
        ))
    return ModelIntelligenceProfile(
        provider=report.provider,
        model_id=report.model_id,
        family=report.family,
        adapter_version=report.adapter_version,
        recommended_max_exposed_tools=report.recommended_max_exposed_tools,
        observations=observations,
        source_report_created_at=report.created_at,
    )
