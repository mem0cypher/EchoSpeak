"""Formal registry of structured video tools.

Models propose tool calls. The deterministic editor runtime validates and
applies them. Models never rewrite timeline JSON or emit FFmpeg/shell commands.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional


VideoToolKind = Literal[
    "inspect",
    "timeline_op",
    "proposal",
    "job",
    "capability",
    "skill",
    "research",
    "memory",
]


@dataclass(frozen=True)
class VideoToolSpec:
    name: str
    kind: VideoToolKind
    description: str
    mutates_timeline: bool = False
    requires_approval: bool = False
    creates_job: bool = False
    is_action: bool = False
    risk_level: str = "safe"  # safe | moderate | destructive
    policy_flags: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    operation_types: tuple[str, ...] = ()
    args_schema: dict[str, Any] = field(default_factory=dict)


# Canonical tool inventory. Runtime handlers live in tools.py / API / jobs.py.
VIDEO_TOOL_SPECS: tuple[VideoToolSpec, ...] = (
    VideoToolSpec(
        name="video_get_editor_context",
        kind="inspect",
        description="Return structured editor context (document, timeline, selection, jobs, capabilities, authority).",
        args_schema={"session_id": "str", "project_id": "str", "document_id": "str?", "selection": "EditorSelectionContext?"},
    ),
    VideoToolSpec(
        name="video_inspect_media",
        kind="inspect",
        description="Inspect one imported or generated media asset by id (streams, duration, provenance).",
        required_capabilities=("media_probe",),
        args_schema={"session_id": "str", "project_id": "str", "document_id": "str", "asset_id": "str"},
    ),
    VideoToolSpec(
        name="video_inspect_timeline",
        kind="inspect",
        description="Inspect the current timeline head: tracks, clips, revision, pending plans.",
        args_schema={"session_id": "str", "project_id": "str", "document_id": "str"},
    ),
    VideoToolSpec(
        name="video_list_capabilities",
        kind="capability",
        description="Report deterministic editing features and local/cloud model capabilities.",
        args_schema={"session_id": "str", "project_id": "str"},
    ),
    VideoToolSpec(
        name="video_list_skills",
        kind="skill",
        description="List registered VideoSkills with intentions, tools, models, and approval rules.",
        args_schema={},
    ),
    VideoToolSpec(
        name="video_plan_request",
        kind="proposal",
        description="Build a structured VideoAgentPlan from a user request and editor context. Does not mutate.",
        args_schema={
            "session_id": "str",
            "project_id": "str",
            "document_id": "str",
            "objective": "str",
            "skill_id": "str?",
            "selection": "EditorSelectionContext?",
            "operations": "EditOperation[]?",
        },
    ),
    VideoToolSpec(
        name="video_propose_operations",
        kind="proposal",
        description=(
            "Validate operations against the current revision and create an "
            "ApprovalRecord-bound proposal. Proposal-only: does not mutate the timeline; "
            "runtime consumes approval via video_apply_transaction / consume_video_approval."
        ),
        # Proposal creates a pending ApprovalRecord; it is not itself a timeline mutation.
        # Authority owner is ToolRegistry (is_action=False). Catalog mirrors registry.
        requires_approval=False,
        is_action=False,
        risk_level="moderate",
        policy_flags=("ENABLE_SYSTEM_ACTIONS", "ALLOW_VIDEO_AGENT_EDITS"),
        required_capabilities=("agent_proposals", "approvals"),
        operation_types=(
            "add_track",
            "insert_clip",
            "split_clip",
            "trim_clip",
            "move_clip",
            "delete_clip",
            "set_clip_volume",
            "set_clip_opacity",
            "set_clip_transform",
            "set_clip_speed",
            "set_clip_enabled",
        ),
        args_schema={
            "session_id": "str",
            "project_id": "str",
            "document_id": "str",
            "objective": "str",
            "operations": "EditOperation[]",
            "expected_revision": "int",
        },
    ),
    VideoToolSpec(
        name="video_apply_transaction",
        kind="timeline_op",
        description="Apply one exact approved video timeline transaction.",
        mutates_timeline=True,
        requires_approval=True,
        is_action=True,
        risk_level="destructive",
        policy_flags=("ENABLE_SYSTEM_ACTIONS", "ALLOW_VIDEO_AGENT_EDITS"),
        required_capabilities=("timeline_mutation", "approvals"),
        args_schema={
            "session_id": "str",
            "project_id": "str",
            "document_id": "str",
            "transaction_id": "str",
            "plan_id": "str",
            "expected_revision": "int",
            "operation_hash": "str",
        },
    ),
    VideoToolSpec(
        name="video_submit_job",
        kind="job",
        description="Submit a durable analysis/render/export/generation job bound to Session, Project, and revision.",
        creates_job=True,
        is_action=True,
        risk_level="moderate",
        policy_flags=("ENABLE_SYSTEM_ACTIONS",),
        args_schema={
            "session_id": "str",
            "project_id": "str",
            "document_id": "str",
            "kind": "JobKind",
            "capability": "str?",
            "adapter_id": "str?",
            "idempotency_key": "str",
            "input_asset_ids": "str[]",
            "expected_revision": "int?",
            "parameters": "object",
        },
    ),
    VideoToolSpec(
        name="video_get_job",
        kind="job",
        description="Read durable job status, progress, outputs, and ToolRun linkage.",
        args_schema={"session_id": "str", "project_id": "str", "document_id": "str", "job_id": "str"},
    ),
    VideoToolSpec(
        name="video_cancel_job",
        kind="job",
        description="Request cancellation of a durable video job.",
        is_action=True,
        risk_level="moderate",
        policy_flags=("ENABLE_SYSTEM_ACTIONS",),
        args_schema={"session_id": "str", "project_id": "str", "document_id": "str", "job_id": "str"},
    ),
    VideoToolSpec(
        name="video_retry_job",
        kind="job",
        description="Retry a failed or retryable job with the same idempotency identity when allowed.",
        creates_job=True,
        is_action=True,
        risk_level="moderate",
        policy_flags=("ENABLE_SYSTEM_ACTIONS",),
        args_schema={"session_id": "str", "project_id": "str", "document_id": "str", "job_id": "str"},
    ),
    VideoToolSpec(
        name="video_update_creative_memory",
        kind="memory",
        description="Persist durable creative preferences only (style, format, objective, conventions). Never playhead/selection.",
        is_action=True,
        risk_level="moderate",
        policy_flags=("ENABLE_SYSTEM_ACTIONS",),
        args_schema={
            "session_id": "str",
            "project_id": "str",
            "document_id": "str?",
            "preferred_style": "str?",
            "output_format": "str?",
            "creative_objective": "str?",
            "approved_workflow_choices": "str[]?",
            "project_conventions": "str[]?",
        },
    ),
)


_BY_NAME: dict[str, VideoToolSpec] = {spec.name: spec for spec in VIDEO_TOOL_SPECS}


def get_video_tool(name: str) -> Optional[VideoToolSpec]:
    return _BY_NAME.get(str(name or "").strip())


def list_video_tools() -> list[VideoToolSpec]:
    return list(VIDEO_TOOL_SPECS)


def list_video_tool_names() -> list[str]:
    return [spec.name for spec in VIDEO_TOOL_SPECS]


def video_tools_as_dicts() -> list[dict[str, Any]]:
    return [asdict(spec) for spec in VIDEO_TOOL_SPECS]


def mutation_tools() -> list[VideoToolSpec]:
    return [spec for spec in VIDEO_TOOL_SPECS if spec.mutates_timeline or spec.requires_approval]


def allowed_operation_types() -> tuple[str, ...]:
    propose = get_video_tool("video_propose_operations")
    if propose is None:
        return ()
    return propose.operation_types
