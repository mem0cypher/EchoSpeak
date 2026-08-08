"""Bounded child-work contracts; no standalone agent runtime.

Subagents may be introduced only as child TaskRuns linked to an explicit
SUBAGENT graph node. They inherit the selected Session model and a subset of
parent capabilities. This module intentionally contains no executor.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.execution_graph import ExecutionProfile


class SubagentBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_turns: int = Field(default=2, ge=1, le=8)
    max_tool_runs: int = Field(default=4, ge=0, le=16)
    max_wall_time_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    max_context_tokens: int = Field(default=4000, ge=256, le=32000)


class SubagentSpecification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_id: str
    parent_task_run_id: str
    graph_node_id: str
    session_id: str
    project_id: str = ""
    objective: str
    execution_profile: ExecutionProfile = ExecutionProfile.WORK
    selected_provider: str
    selected_model_id: str
    permitted_capabilities: list[str] = Field(default_factory=list)
    read_only: bool = True
    budget: SubagentBudget = Field(default_factory=SubagentBudget)

    @model_validator(mode="after")
    def preserve_parent_authority(self) -> "SubagentSpecification":
        required = {
            "subagent_id": self.subagent_id,
            "parent_task_run_id": self.parent_task_run_id,
            "graph_node_id": self.graph_node_id,
            "session_id": self.session_id,
            "objective": self.objective,
            "selected_provider": self.selected_provider,
            "selected_model_id": self.selected_model_id,
        }
        missing = [key for key, value in required.items() if not str(value or "").strip()]
        if missing:
            raise ValueError(f"Subagent specification is missing: {missing}")
        self.permitted_capabilities = list(dict.fromkeys(
            str(item or "").strip()
            for item in self.permitted_capabilities
            if str(item or "").strip()
        ))[:32]
        if not self.read_only:
            raise ValueError("Bounded subagent foundation is read-only until delegated approvals are implemented")
        if set(self.permitted_capabilities) & {"coding_write", "terminal", "communications", "desktop"}:
            raise ValueError("Read-only subagent cannot receive mutating or interactive capabilities")
        return self


class SubagentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent_id: str
    child_task_run_id: str
    status: str
    artifact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    tool_run_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    completion_finalizable: bool = False
    diagnostics: dict[str, Any] = Field(default_factory=dict)
