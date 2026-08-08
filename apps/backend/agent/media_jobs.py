"""Unified media-job contract and compatibility projections.

GenerationJob and VoiceJob remain the durable domain owners during migration.
This module supplies one typed job surface and exact TaskRun/ToolRun bindings;
it does not introduce another job store or provider executor.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MediaOperation(str, Enum):
    IMAGE_GENERATION = "image_generation"
    VIDEO_GENERATION = "video_generation"
    TEXT_TO_SPEECH = "text_to_speech"
    SPEECH_TO_TEXT = "speech_to_text"
    REALTIME_VOICE = "realtime_voice"


class MediaJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MediaJobBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_kind: Literal["canonical_tool", "session_transport", "legacy_unbound"] = "canonical_tool"
    execution_id: str = ""
    task_run_id: str = ""
    requirement_id: str = ""
    attempt_id: str = ""
    tool_run_id: str = ""
    legacy_unbound: bool = False

    @model_validator(mode="after")
    def require_canonical_binding(self) -> "MediaJobBinding":
        if self.binding_kind == "canonical_tool" and not self.legacy_unbound and (
            not self.execution_id or not self.task_run_id or not self.tool_run_id
        ):
            raise ValueError("MediaJobBinding requires Execution, TaskRun, and ToolRun identity")
        if self.binding_kind == "session_transport" and self.tool_run_id:
            raise ValueError("Session Voice transport must not impersonate a ToolRun")
        if self.binding_kind == "legacy_unbound":
            self.legacy_unbound = True
        if bool(self.requirement_id) != bool(self.attempt_id):
            raise ValueError("Media requirement and attempt identity must be bound together")
        return self


class MediaJobProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    job_id: str
    owner: str
    session_id: str
    project_id: str
    operation: MediaOperation
    provider_id: str
    model_id: str = ""
    status: MediaJobStatus
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    input_asset_ids: list[str] = Field(default_factory=list)
    output_asset_ids: list[str] = Field(default_factory=list)
    binding: MediaJobBinding
    error_code: str = ""
    created_at: float
    updated_at: float


def current_media_job_binding() -> MediaJobBinding:
    """Read exact current execution identity after the authority wrapper binds it."""

    from agent.tools import get_tool_execution_context

    context = dict(get_tool_execution_context() or {})
    if not all(str(context.get(key) or "") for key in ("execution_id", "task_run_id", "tool_run_id")):
        raise RuntimeError("Media job is not bound to a current TaskRun and ToolRun")
    if bool(context.get("requirement_id")) != bool(context.get("attempt_id")):
        raise RuntimeError("Media requirement and attempt identity must be bound together")
    binding = MediaJobBinding(
        execution_id=str(context.get("execution_id") or ""),
        task_run_id=str(context.get("task_run_id") or ""),
        requirement_id=str(context.get("requirement_id") or ""),
        attempt_id=str(context.get("attempt_id") or ""),
        tool_run_id=str(context.get("tool_run_id") or ""),
    )
    return binding


def bind_media_job(job: Any, binding: MediaJobBinding) -> Any:
    """Bind a new job once; a stable replay keeps its original execution lineage."""

    updates = {
        "execution_id": binding.execution_id,
        "task_run_id": binding.task_run_id,
        "requirement_id": binding.requirement_id,
        "attempt_id": binding.attempt_id,
        "tool_run_id": binding.tool_run_id,
    }
    for key, value in updates.items():
        current = str(getattr(job, key, "") or "")
        if current and current != value:
            raise RuntimeError(f"Media job {key} is already bound to another runtime identity")
    return job.model_copy(update=updates)


def project_generation_job(job: Any) -> MediaJobProjection:
    return MediaJobProjection(
        job_id=job.id,
        owner="generation_job",
        session_id=job.session_id,
        project_id=job.project_id,
        operation=(
            MediaOperation.IMAGE_GENERATION if job.kind == "image"
            else MediaOperation.VIDEO_GENERATION
        ),
        provider_id=job.provider_id,
        model_id=job.model,
        status=MediaJobStatus(job.status),
        progress=job.progress,
        input_asset_ids=list(job.input_asset_ids),
        output_asset_ids=list(job.output_asset_ids),
        binding=_binding_from_job(job),
        error_code=job.error_code,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def project_voice_job(job: Any) -> MediaJobProjection:
    operation = {
        "text_to_speech": MediaOperation.TEXT_TO_SPEECH,
        "speech_to_text": MediaOperation.SPEECH_TO_TEXT,
        "realtime": MediaOperation.REALTIME_VOICE,
    }[job.operation]
    return MediaJobProjection(
        job_id=job.id,
        owner="voice_job",
        session_id=job.session_id,
        project_id=job.project_id,
        operation=operation,
        provider_id=job.provider_id,
        model_id=job.model,
        status=MediaJobStatus(job.status),
        progress=job.progress,
        input_asset_ids=[job.input_asset_id] if job.input_asset_id else [],
        output_asset_ids=[job.output_asset_id] if job.output_asset_id else [],
        binding=_binding_from_job(job),
        error_code=job.error_code,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _binding_from_job(job: Any) -> MediaJobBinding:
    transport = str(getattr(job, "origin", "") or "") == "voice_transport"
    unbound = not transport and not bool(job.execution_id and job.task_run_id and job.tool_run_id)
    return MediaJobBinding(
        binding_kind=(
            "session_transport" if transport
            else "legacy_unbound" if unbound
            else "canonical_tool"
        ),
        execution_id=str(job.execution_id or ""),
        task_run_id=str(job.task_run_id or ""),
        requirement_id=str(job.requirement_id or ""),
        attempt_id=str(job.attempt_id or ""),
        tool_run_id=str(job.tool_run_id or ""),
        legacy_unbound=unbound,
    )
