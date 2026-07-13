"""Versioned video-editor schemas.

External media engines and models are adapters. These records are the domain
truth consumed by the operation/revision store.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def new_id() -> str:
    return str(uuid.uuid4())


class Rational(BaseModel):
    schema_version: Literal[1] = 1
    numerator: int = 1
    denominator: int = Field(default=1, gt=0)


class RationalTime(BaseModel):
    """Exact integer ticks in an explicit time base.

    Ticks serialize as a decimal string so browser clients never lose integer
    precision. Seconds = ticks * numerator / denominator.
    """

    schema_version: Literal[1] = 1
    ticks: str = "0"
    time_base: Rational = Field(default_factory=lambda: Rational(numerator=1, denominator=1000))

    @field_validator("ticks")
    @classmethod
    def validate_ticks(cls, value: str) -> str:
        text = str(value).strip()
        if not text or text.startswith("+") or (text.startswith("-") and not text[1:].isdigit()) or (not text.startswith("-") and not text.isdigit()):
            raise ValueError("ticks must be a base-10 integer string")
        return str(int(text))


class MediaKind(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    CAPTION = "caption"
    UNKNOWN = "unknown"


class MediaOrigin(str, Enum):
    IMPORTED = "imported"
    GENERATED = "generated"
    DERIVED = "derived"


class MediaStream(BaseModel):
    schema_version: Literal[1] = 1
    index: int = 0
    kind: MediaKind = MediaKind.UNKNOWN
    codec: str = ""
    time_base: Optional[Rational] = None
    duration_ticks: Optional[str] = None
    average_frame_rate: Optional[Rational] = None
    nominal_frame_rate: Optional[Rational] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    pixel_format: str = ""
    color: dict[str, Any] = Field(default_factory=dict)
    rotation_degrees: Optional[int] = None
    disposition: dict[str, Any] = Field(default_factory=dict)


class MediaProvenance(BaseModel):
    schema_version: Literal[1] = 1
    origin: MediaOrigin = MediaOrigin.IMPORTED
    source_sha256: str = ""
    parent_asset_ids: list[str] = Field(default_factory=list)
    adapter_id: str = ""
    provider: str = ""
    model: str = ""
    model_version: str = ""
    prompt_sha256: str = ""
    seed: Optional[int] = None
    settings: dict[str, Any] = Field(default_factory=dict)
    job_id: str = ""
    source_session_id: str = ""
    source_execution_id: str = ""
    source_tool_run_id: str = ""
    license_expression: str = ""
    license_url: str = ""
    license_text_sha256: str = ""
    commercial_use: str = "unknown"
    remote_retention_expires_at: Optional[float] = None
    generated_content_disclosed: bool = False


class MediaAsset(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    project_id: str
    document_id: str
    name: str
    kind: MediaKind = MediaKind.UNKNOWN
    project_relative_path: str
    immutable: bool = True
    sha256: str
    size_bytes: int = Field(ge=0)
    mtime_ns: int = Field(ge=0)
    duration: Optional[RationalTime] = None
    streams: list[MediaStream] = Field(default_factory=list)
    container: dict[str, Any] = Field(default_factory=dict)
    provenance: MediaProvenance = Field(default_factory=MediaProvenance)
    created_at: float = Field(default_factory=time.time)


class GeneratedAsset(MediaAsset):
    candidate_id: str = ""
    selected_at: Optional[float] = None


class Clip(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    asset_id: str
    name: str = ""
    timeline_start: RationalTime
    source_in: RationalTime = Field(default_factory=RationalTime)
    duration: RationalTime
    enabled: bool = True
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    volume: float = Field(default=1.0, ge=0.0, le=4.0)
    transform: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Track(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    kind: MediaKind
    name: str
    order: int = Field(default=0, ge=0)
    locked: bool = False
    muted: bool = False
    hidden: bool = False
    clips: list[Clip] = Field(default_factory=list)


class Timeline(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    name: str = "Main Timeline"
    time_base: Rational = Field(default_factory=lambda: Rational(numerator=1, denominator=1000))
    tracks: list[Track] = Field(default_factory=list)


class EditOperationType(str, Enum):
    ADD_TRACK = "add_track"
    INSERT_CLIP = "insert_clip"
    SPLIT_CLIP = "split_clip"
    TRIM_CLIP = "trim_clip"
    MOVE_CLIP = "move_clip"
    DELETE_CLIP = "delete_clip"
    # Deterministic clip property mutations (no generative side effects).
    SET_CLIP_VOLUME = "set_clip_volume"
    SET_CLIP_OPACITY = "set_clip_opacity"
    SET_CLIP_TRANSFORM = "set_clip_transform"
    SET_CLIP_SPEED = "set_clip_speed"
    SET_CLIP_ENABLED = "set_clip_enabled"


class EditOperation(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    operation_type: EditOperationType
    payload: dict[str, Any] = Field(default_factory=dict)
    expected_revision: int = Field(ge=0)
    source: Literal["manual", "agent", "undo", "redo"] = "manual"
    created_at: float = Field(default_factory=time.time)


class VideoEditPlan(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    project_id: str
    document_id: str
    session_id: str
    objective: str
    expected_revision: int = Field(ge=0)
    operations: list[EditOperation] = Field(default_factory=list)
    status: Literal["draft", "proposed", "approved", "rejected", "applied", "failed"] = "draft"
    transaction_id: str = ""
    approval_id: str = ""
    skill_id: str = ""
    required_capabilities: list[str] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    job_specs: list[dict[str, Any]] = Field(default_factory=list)
    research_inputs: list[dict[str, Any]] = Field(default_factory=list)
    verification_rules: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)


class VideoEditTransaction(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    project_id: str
    document_id: str
    session_id: str
    expected_revision: int = Field(ge=0)
    operation_hash: str = ""
    operations: list[EditOperation] = Field(min_length=1)
    source: Literal["manual", "agent", "undo", "redo"] = "manual"
    status: Literal["prepared", "pending_approval", "applied", "rejected", "failed"] = "prepared"
    approval_id: str = ""
    plan_id: str = ""
    resulting_revision_id: str = ""
    created_at: float = Field(default_factory=time.time)
    applied_at: Optional[float] = None

    @model_validator(mode="after")
    def revisions_match(self):
        if any(op.expected_revision != self.expected_revision for op in self.operations):
            raise ValueError("every operation must target the transaction expected_revision")
        return self


class VideoRevision(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    project_id: str
    document_id: str
    revision_number: int = Field(ge=0)
    parent_revision_id: str = ""
    transaction_id: str = ""
    operation_ids: list[str] = Field(default_factory=list)
    snapshot_sha256: str = ""
    created_at: float = Field(default_factory=time.time)
    source: Literal["create", "manual", "agent", "undo", "redo"] = "manual"


class JobKind(str, Enum):
    ANALYSIS = "analysis"
    GENERATION = "generation"
    RENDER = "render"
    PROXY = "proxy"
    TRANSCRIPTION = "transcription"
    EXPORT = "export"
    PREVIEW = "preview"


class VideoJob(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    project_id: str
    document_id: str
    session_id: str
    kind: JobKind
    adapter_id: str = ""
    tool_name: str = ""
    capability: str = ""
    idempotency_key: str
    status: Literal["queued", "preparing", "running", "interrupted", "blocked", "retryable", "failed", "completed", "canceled"] = "queued"
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    input_asset_ids: list[str] = Field(default_factory=list)
    input_hashes: list[str] = Field(default_factory=list)
    expected_revision: Optional[int] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    provider_job_id: str = ""
    outputs: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    error: str = ""
    cancel_requested: bool = False
    retry_count: int = Field(default=0, ge=0)
    # Canonical runtime linkage (Project → Session → Turn/Execution → ToolRun).
    execution_id: str = ""
    tool_run_id: str = ""
    approval_id: str = ""
    plan_id: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class GeneratedCandidate(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    project_id: str
    document_id: str
    job_id: str
    asset_id: str = ""
    name: str
    status: Literal["pending", "ready", "selected", "rejected", "failed"] = "pending"
    preview_url: str = ""
    provenance: MediaProvenance
    created_at: float = Field(default_factory=time.time)


class EditorSelectionContext(BaseModel):
    """Ephemeral UI selection. Never written to durable personal memory."""

    schema_version: Literal[1] = 1
    document_id: str
    timeline_id: str = ""
    selected_track_ids: list[str] = Field(default_factory=list)
    selected_clip_ids: list[str] = Field(default_factory=list)
    selected_asset_ids: list[str] = Field(default_factory=list)
    playhead: RationalTime = Field(default_factory=RationalTime)
    selected_range_start: Optional[RationalTime] = None
    selected_range_end: Optional[RationalTime] = None
    visible_range_start: Optional[RationalTime] = None
    visible_range_end: Optional[RationalTime] = None
    document_revision: int = Field(ge=0)


class VideoAssetSummary(BaseModel):
    schema_version: Literal[1] = 1
    id: str
    name: str
    kind: MediaKind
    origin: MediaOrigin = MediaOrigin.IMPORTED
    duration: Optional[RationalTime] = None
    project_relative_path: str = ""
    sha256: str = ""


class VideoTrackSummary(BaseModel):
    schema_version: Literal[1] = 1
    id: str
    name: str
    kind: MediaKind
    order: int = 0
    locked: bool = False
    muted: bool = False
    clip_count: int = 0
    clip_ids: list[str] = Field(default_factory=list)


class VideoClipSummary(BaseModel):
    schema_version: Literal[1] = 1
    id: str
    track_id: str
    asset_id: str
    name: str = ""
    timeline_start: RationalTime
    source_in: RationalTime
    duration: RationalTime
    enabled: bool = True
    volume: float = 1.0
    opacity: float = 1.0
    speed: float = 1.0


class VideoJobSummary(BaseModel):
    schema_version: Literal[1] = 1
    id: str
    kind: JobKind
    status: str
    progress: float = 0.0
    adapter_id: str = ""
    capability: str = ""
    tool_run_id: str = ""
    execution_id: str = ""
    expected_revision: Optional[int] = None
    cancel_requested: bool = False
    error: str = ""


class VideoPlanSummary(BaseModel):
    schema_version: Literal[1] = 1
    id: str
    objective: str
    status: str
    expected_revision: int
    operation_count: int = 0
    skill_id: str = ""
    transaction_id: str = ""
    approval_id: str = ""


class VideoAuthoritySnapshot(BaseModel):
    schema_version: Literal[1] = 1
    session_id: str
    project_id: str
    project_attached: bool = False
    system_actions: bool = False
    video_agent_edits: bool = False
    allowed_video_tools: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    pending_approval_id: str = ""
    mutation_allowed: bool = False


class VideoModelCapability(BaseModel):
    """Capability-first model surface. Never hardcode model names into edit logic."""

    schema_version: Literal[1] = 1
    capability: str
    available: bool = False
    adapter_ids: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class VideoCapabilityReport(BaseModel):
    schema_version: Literal[1] = 1
    deterministic_editing: bool = True
    media_probe: bool = False
    timeline_mutation: bool = True
    agent_proposals: bool = True
    approvals: bool = True
    undo_redo: bool = True
    render_preview: bool = False
    export: bool = False
    analysis: bool = False
    transcription: bool = False
    generative_video: bool = False
    research: bool = True
    model_capabilities: list[VideoModelCapability] = Field(default_factory=list)
    adapters: list[dict[str, Any]] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)
    available_skills: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)


class VideoCreativeMemory(BaseModel):
    """Durable creative preferences only — never playhead/selection."""

    schema_version: Literal[1] = 1
    project_id: str
    session_id: str = ""
    preferred_style: str = ""
    output_format: str = ""
    creative_objective: str = ""
    approved_workflow_choices: list[str] = Field(default_factory=list)
    project_conventions: list[str] = Field(default_factory=list)
    unfinished_plan_ids: list[str] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)


class VideoEditorContext(BaseModel):
    """Authoritative structured context for Echo — not inferred from chat prose."""

    schema_version: Literal[1] = 1
    project_id: str
    session_id: str
    document_id: str = ""
    document_name: str = ""
    document_revision: int = 0
    head_revision_id: str = ""
    timeline_id: str = ""
    time_base: Rational = Field(default_factory=lambda: Rational(numerator=1, denominator=1000))
    tracks: list[VideoTrackSummary] = Field(default_factory=list)
    clips: list[VideoClipSummary] = Field(default_factory=list)
    assets: list[VideoAssetSummary] = Field(default_factory=list)
    selection: Optional[EditorSelectionContext] = None
    active_jobs: list[VideoJobSummary] = Field(default_factory=list)
    pending_plans: list[VideoPlanSummary] = Field(default_factory=list)
    capabilities: VideoCapabilityReport = Field(default_factory=VideoCapabilityReport)
    authority: VideoAuthoritySnapshot
    creative_memory: Optional[VideoCreativeMemory] = None
    unfinished_plan: Optional[VideoPlanSummary] = None
    built_at: float = Field(default_factory=time.time)


class VideoAgentPlanStep(BaseModel):
    schema_version: Literal[1] = 1
    step_id: str = Field(default_factory=new_id)
    kind: Literal["inspect", "tool", "operation", "job", "research", "approval", "verify", "skill"]
    tool_name: str = ""
    description: str = ""
    requires_approval: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)


class VideoAgentPlan(BaseModel):
    """Structured agent plan. Does not mutate the editor."""

    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    project_id: str
    session_id: str
    document_id: str = ""
    objective: str
    expected_revision: int = Field(ge=0)
    skill_id: str = ""
    status: Literal["draft", "ready", "blocked", "proposed", "applied", "failed", "canceled"] = "draft"
    steps: list[VideoAgentPlanStep] = Field(default_factory=list)
    operations: list[EditOperation] = Field(default_factory=list)
    job_specs: list[dict[str, Any]] = Field(default_factory=list)
    research_queries: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    verification_rules: list[str] = Field(default_factory=list)
    resumable: bool = True
    created_at: float = Field(default_factory=time.time)


class VideoProjectDocument(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    project_id: str
    name: str
    archived: bool = False
    revision: int = Field(default=0, ge=0)
    head_revision_id: str = ""
    timeline: Timeline = Field(default_factory=Timeline)
    assets: list[MediaAsset] = Field(default_factory=list)
    generated_assets: list[GeneratedAsset] = Field(default_factory=list)
    plans: list[VideoEditPlan] = Field(default_factory=list)
    transactions: list[VideoEditTransaction] = Field(default_factory=list)
    revisions: list[VideoRevision] = Field(default_factory=list)
    jobs: list[VideoJob] = Field(default_factory=list)
    candidates: list[GeneratedCandidate] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    undo_revision_ids: list[str] = Field(default_factory=list)
    redo_revision_ids: list[str] = Field(default_factory=list)
    creative_memory: Optional[VideoCreativeMemory] = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
