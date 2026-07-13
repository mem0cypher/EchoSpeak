"""Canonical Skill contract shared by video, research, coding, and future skills.

Filesystem manifests, VideoSkill definitions, and runtime selection all project
into this contract. Prompt-only packages without a validated reachable
implementation are marked invalid/unavailable — not silently “active.”
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class SkillStatus(str, Enum):
    BUILT_IN = "built_in"
    INSTALLED = "installed"
    EXPERIMENTAL = "experimental"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"
    NEEDS_DEPENDENCY = "needs_dependency"
    NEEDS_MODEL = "needs_model"
    NEEDS_PERMISSION = "needs_permission"
    DEPRECATED = "deprecated"
    DRAFT = "draft"
    PROPOSED = "proposed"


class SkillOrigin(str, Enum):
    BUILT_IN = "built_in"
    PACKAGE = "package"
    GENERATED = "generated"
    PROJECT = "project"
    VIDEO_DOMAIN = "video_domain"


class SkillManifest(BaseModel):
    """Authoritative skill declaration. A prompt file alone is not enough."""

    schema_version: Literal[1] = 1
    id: str
    version: str = "1.0.0"
    status: SkillStatus = SkillStatus.INSTALLED
    owner: str = "echospeak"
    origin: SkillOrigin = SkillOrigin.PACKAGE
    name: str
    description: str = ""
    accepted_intents: list[str] = Field(default_factory=list)
    supported_modes: list[str] = Field(default_factory=list)  # chat | coding | research | video
    required_project_state: list[str] = Field(default_factory=list)
    required_context_fields: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    optional_tools: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    required_models: list[str] = Field(default_factory=list)
    required_artifacts: list[str] = Field(default_factory=list)
    produced_artifacts: list[str] = Field(default_factory=list)
    job_types: list[str] = Field(default_factory=list)
    operation_templates: list[dict[str, Any]] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    verification_rules: list[str] = Field(default_factory=list)
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    dependency_metadata: dict[str, Any] = Field(default_factory=dict)
    license: str = ""
    compatibility_version: str = "1"
    implementation_entry: str = ""  # e.g. package path, video_domain:video_rough_cut
    prompt: str = ""
    package_path: str = ""
    project_id: str = ""  # Project-scoped skills only
    tools_reachable: list[str] = Field(default_factory=list)
    tools_missing: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    executable: bool = False
    updated_at: float = Field(default_factory=time.time)

    def tool_allowlist(self) -> list[str]:
        return list(dict.fromkeys([*self.required_tools, *self.optional_tools, *self.tools_reachable]))


class SkillSelectionOutcome(str, Enum):
    SELECTED = "selected"
    DIRECT_TOOL_BETTER = "direct_tool_better"
    BLOCKED_MISSING_TOOL = "blocked_missing_tool"
    BLOCKED_MISSING_MODEL = "blocked_missing_model"
    BLOCKED_MISSING_ARTIFACT = "blocked_missing_artifact"
    BLOCKED_PERMISSION = "blocked_permission"
    UNAVAILABLE = "unavailable"
    AMBIGUOUS = "ambiguous"
    NO_MATCHING_SKILL = "no_matching_skill"
    DISABLED = "disabled"
    STALE_CONTEXT = "stale_context"


class SkillSelectionResult(BaseModel):
    schema_version: Literal[1] = 1
    outcome: SkillSelectionOutcome
    skill_id: str = ""
    skill_version: str = ""
    direct_tool: str = ""
    reason: str = ""
    candidates: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class SkillExecutionStatus(str, Enum):
    PLANNED = "planned"
    BLOCKED = "blocked"
    PENDING_APPROVAL = "pending_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    PARTIAL = "partial"


class SkillExecutionRecord(BaseModel):
    """Durable skill execution identity — never store completion as prose only."""

    schema_version: Literal[1] = 1
    id: str
    execution_id: str
    skill_id: str
    skill_version: str
    project_id: str = ""
    session_id: str = ""
    turn_id: str = ""
    parent_execution_id: str = ""  # composition parent
    child_skill_ids: list[str] = Field(default_factory=list)
    input_context_identity: dict[str, Any] = Field(default_factory=dict)
    selected_tool_ids: list[str] = Field(default_factory=list)
    operation_ids: list[str] = Field(default_factory=list)
    job_ids: list[str] = Field(default_factory=list)
    approval_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    tool_run_ids: list[str] = Field(default_factory=list)
    status: SkillExecutionStatus = SkillExecutionStatus.PLANNED
    verification: dict[str, Any] = Field(default_factory=dict)
    failure_reason: str = ""
    retry_of: str = ""
    continue_from: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class SkillProposal(BaseModel):
    """Governed skill-creation proposal. Not executable until registration approval."""

    schema_version: Literal[1] = 1
    id: str
    name: str
    description: str
    reason_created: str = ""
    insufficient_existing_skills: list[str] = Field(default_factory=list)
    accepted_intents: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_models: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    verification_rules: list[str] = Field(default_factory=list)
    files_created: list[str] = Field(default_factory=list)
    version: str = "0.1.0-draft"
    project_id: str = ""
    session_id: str = ""
    status: Literal["proposed", "reviewed", "registered_disabled", "rejected", "canceled"] = "proposed"
    registration_approval_id: str = ""
    created_at: float = Field(default_factory=time.time)


# Executable statuses — selectable for execution planning.
EXECUTABLE_STATUSES = frozenset(
    {
        SkillStatus.BUILT_IN,
        SkillStatus.INSTALLED,
        SkillStatus.EXPERIMENTAL,
    }
)
