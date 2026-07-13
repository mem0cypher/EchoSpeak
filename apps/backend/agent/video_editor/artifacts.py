"""Structured artifacts exchanged by video (and related) skills.

Skills exchange artifacts — not copied prose. Compatibility is checked before reuse.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def new_artifact_id() -> str:
    return str(uuid.uuid4())


class ArtifactKind(str, Enum):
    TRANSCRIPT = "transcript"
    SILENCE = "silence"
    SCENE = "scene"
    RESEARCH = "research"
    SCRIPT = "script"
    HIGHLIGHT = "highlight"
    CAPTION = "caption"
    GENERATED_VIDEO_CANDIDATE = "generated_video_candidate"
    RENDER = "render"
    ANALYSIS = "analysis"
    GENERIC = "generic"


class ArtifactValidity(str, Enum):
    VALID = "valid"
    STALE = "stale"
    INVALID = "invalid"
    PENDING = "pending"


class VideoArtifact(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_artifact_id)
    kind: ArtifactKind
    project_id: str
    document_id: str = ""
    source_asset_ids: list[str] = Field(default_factory=list)
    source_revision: Optional[int] = None
    producer_skill_id: str = ""
    producer_tool: str = ""
    producer_model: str = ""
    producer_job_id: str = ""
    configuration: dict[str, Any] = Field(default_factory=dict)
    validity: ArtifactValidity = ArtifactValidity.PENDING
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_path: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    invalidation_rules: list[str] = Field(default_factory=list)
    session_id: str = ""
    execution_id: str = ""
    tool_run_id: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


def artifact_compatible(
    artifact: VideoArtifact,
    *,
    document_id: str = "",
    document_revision: Optional[int] = None,
    required_kind: ArtifactKind | str | None = None,
) -> tuple[bool, str]:
    if artifact.validity == ArtifactValidity.INVALID:
        return False, "artifact marked invalid"
    if artifact.validity == ArtifactValidity.STALE:
        return False, "artifact marked stale"
    if required_kind is not None:
        kind = required_kind if isinstance(required_kind, ArtifactKind) else ArtifactKind(str(required_kind))
        if artifact.kind != kind:
            return False, f"kind mismatch: {artifact.kind.value} != {kind.value}"
    if document_id and artifact.document_id and artifact.document_id != document_id:
        return False, "document mismatch"
    if document_revision is not None and artifact.source_revision is not None:
        if "invalidate_on_revision_change" in (artifact.invalidation_rules or []):
            if artifact.source_revision != document_revision:
                return False, "source revision changed"
    return True, "ok"


def find_reusable_artifact(
    artifacts: list[VideoArtifact],
    *,
    kind: ArtifactKind | str,
    document_id: str = "",
    document_revision: Optional[int] = None,
    source_asset_ids: list[str] | None = None,
) -> Optional[VideoArtifact]:
    want = kind if isinstance(kind, ArtifactKind) else ArtifactKind(str(kind))
    sources = set(source_asset_ids or [])
    for art in reversed(artifacts):
        ok, _ = artifact_compatible(
            art,
            document_id=document_id,
            document_revision=document_revision,
            required_kind=want,
        )
        if not ok:
            continue
        if sources and sources - set(art.source_asset_ids):
            continue
        if art.validity != ArtifactValidity.VALID:
            continue
        return art
    return None
