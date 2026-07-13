"""VideoSkill schema and registration.

Skills declare intentions, tools, models, templates, and verification rules.
They return structured plans only — they never mutate the editor directly.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from agent.video_editor.models import new_id


class VideoSkillDefinition(BaseModel):
    schema_version: Literal[1] = 1
    id: str
    name: str
    description: str
    accepted_intentions: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_models: list[str] = Field(default_factory=list)  # capability tokens, not product names
    required_analysis: list[str] = Field(default_factory=list)
    operation_templates: list[dict[str, Any]] = Field(default_factory=list)
    job_requirements: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    approval_rules: dict[str, Any] = Field(default_factory=dict)
    verification_rules: list[str] = Field(default_factory=list)
    resource_limits: dict[str, Any] = Field(default_factory=dict)
    research_enabled: bool = False
    mutates_timeline: bool = True
    status: Literal["registered", "draft", "disabled"] = "registered"


class VideoSkillRegistry:
    """In-process registry of VideoSkills. Creation and execution remain separate."""

    _skills: dict[str, VideoSkillDefinition] = {}

    @classmethod
    def register(cls, skill: VideoSkillDefinition) -> None:
        if skill.status == "disabled":
            cls._skills.pop(skill.id, None)
            return
        cls._skills[skill.id] = skill

    @classmethod
    def get(cls, skill_id: str) -> Optional[VideoSkillDefinition]:
        return cls._skills.get(str(skill_id or "").strip())

    @classmethod
    def list(cls) -> list[VideoSkillDefinition]:
        return sorted(cls._skills.values(), key=lambda item: item.id)

    @classmethod
    def match_intention(cls, text: str) -> list[VideoSkillDefinition]:
        hay = str(text or "").strip().lower()
        if not hay:
            return []
        hits: list[VideoSkillDefinition] = []
        for skill in cls._skills.values():
            if any(token.lower() in hay for token in skill.accepted_intentions if token):
                hits.append(skill)
        return hits


def _builtin_skills() -> list[VideoSkillDefinition]:
    return [
        VideoSkillDefinition(
            id="video_rough_cut",
            name="Rough Cut",
            description="Assemble selected or all clips into a linear rough cut on a video track.",
            accepted_intentions=["rough cut", "assemble", "sequence clips", "first cut"],
            required_tools=["video_get_editor_context", "video_inspect_timeline", "video_propose_operations", "video_apply_transaction"],
            required_models=[],
            operation_templates=[
                {"operation_type": "add_track", "payload": {"kind": "video"}},
                {"operation_type": "insert_clip", "payload": {"track_id": "$track_id", "asset_id": "$asset_id"}},
            ],
            permissions=["video_agent_edits", "system_actions"],
            approval_rules={"requires_approval": True, "stale_revision_invalidates": True},
            verification_rules=["revision_advanced", "clip_count_matches_plan", "no_overlap"],
            resource_limits={"max_operations": 64},
            mutates_timeline=True,
        ),
        VideoSkillDefinition(
            id="video_remove_silence",
            name="Remove Silence",
            description="Plan silence removal using analysis jobs and subsequent trim/delete operations.",
            accepted_intentions=[
                "remove silence",
                "cut silence",
                "tighten audio",
                "silence removal",
                "silent parts",
                "remove the silent",
                "silence",
            ],
            required_tools=["video_get_editor_context", "video_submit_job", "video_propose_operations", "video_apply_transaction"],
            required_models=["analysis"],
            required_analysis=["silence_detection"],
            job_requirements=["analysis"],
            permissions=["video_agent_edits", "system_actions"],
            approval_rules={
                "requires_approval": True,
                "analysis_before_mutate": True,
                "required_artifacts_before_mutate": ["silence"],
            },
            verification_rules=["analysis_job_completed", "revision_advanced", "duration_changed"],
            resource_limits={"max_operations": 128},
            mutates_timeline=True,
        ),
        VideoSkillDefinition(
            id="video_captions",
            name="Captions",
            description="Transcribe selected footage and plan caption-track insertions.",
            accepted_intentions=["captions", "subtitles", "transcribe", "add captions"],
            required_tools=["video_inspect_media", "video_submit_job", "video_propose_operations", "video_apply_transaction"],
            required_models=["transcription"],
            required_analysis=["transcription"],
            job_requirements=["transcription"],
            permissions=["video_agent_edits", "system_actions"],
            approval_rules={
                "requires_approval": True,
                "required_artifacts_before_mutate": ["transcript"],
            },
            verification_rules=["transcription_job_completed", "caption_track_present"],
            resource_limits={"max_caption_seconds": 7200},
            mutates_timeline=True,
        ),
        VideoSkillDefinition(
            id="video_reframe",
            name="Reframe",
            description="Apply deterministic framing transforms to selected clips.",
            accepted_intentions=["reframe", "crop", "scale", "zoom", "framing"],
            required_tools=["video_get_editor_context", "video_propose_operations", "video_apply_transaction"],
            required_models=[],
            operation_templates=[{"operation_type": "set_clip_transform", "payload": {"clip_id": "$clip_id", "transform": {}}}],
            permissions=["video_agent_edits", "system_actions"],
            approval_rules={"requires_approval": True},
            verification_rules=["revision_advanced", "transform_keys_allowlisted"],
            mutates_timeline=True,
        ),
        VideoSkillDefinition(
            id="video_audio_cleanup",
            name="Audio Cleanup",
            description="Adjust volume and plan optional analysis-backed audio cleanup jobs.",
            accepted_intentions=["audio cleanup", "normalize audio", "volume", "gain", "mute"],
            required_tools=["video_inspect_timeline", "video_propose_operations", "video_apply_transaction"],
            required_models=[],
            operation_templates=[{"operation_type": "set_clip_volume", "payload": {"clip_id": "$clip_id", "volume": 1.0}}],
            permissions=["video_agent_edits", "system_actions"],
            approval_rules={"requires_approval": True},
            verification_rules=["revision_advanced", "volume_in_range"],
            mutates_timeline=True,
        ),
        VideoSkillDefinition(
            id="video_highlights",
            name="Highlights",
            description="Analyze footage and plan a highlight reel from detected peaks.",
            accepted_intentions=["highlights", "highlight reel", "best moments"],
            required_tools=["video_submit_job", "video_plan_request", "video_propose_operations"],
            required_models=["video_understanding", "scene_detection"],
            required_analysis=["scene_detection"],
            job_requirements=["analysis"],
            permissions=["video_agent_edits", "system_actions"],
            approval_rules={"requires_approval": True, "analysis_before_mutate": True},
            verification_rules=["analysis_job_completed", "revision_advanced"],
            mutates_timeline=True,
        ),
        VideoSkillDefinition(
            id="video_script_research",
            name="Script Research",
            description="Use Echo Research to gather structured inputs for a video plan (not a parallel workflow).",
            accepted_intentions=["research script", "research topic", "script outline", "fact check video"],
            required_tools=["video_plan_request", "video_get_editor_context"],
            required_models=[],
            job_requirements=[],
            permissions=[],
            approval_rules={"requires_approval": False},
            verification_rules=["research_inputs_attached"],
            research_enabled=True,
            mutates_timeline=False,
        ),
        VideoSkillDefinition(
            id="video_generate_broll",
            name="Generated B-roll",
            description="Submit a generative-video job for B-roll candidates. Never auto-inserts into the timeline.",
            accepted_intentions=["b-roll", "broll", "generate clip", "generate video", "text to video"],
            required_tools=["video_list_capabilities", "video_submit_job", "video_get_job"],
            required_models=["text_to_video", "image_to_video"],
            job_requirements=["generation"],
            permissions=["system_actions"],
            approval_rules={"requires_approval": True, "cloud_cost_approval": True, "no_auto_timeline_insert": True},
            verification_rules=["job_completed", "candidate_artifact_verified", "not_on_timeline_until_selected"],
            resource_limits={"max_candidates": 4},
            mutates_timeline=False,
        ),
    ]


def ensure_builtin_video_skills() -> None:
    for skill in _builtin_skills():
        if VideoSkillRegistry.get(skill.id) is None:
            VideoSkillRegistry.register(skill)


def list_video_skill_ids() -> list[str]:
    ensure_builtin_video_skills()
    return [skill.id for skill in VideoSkillRegistry.list()]


def list_video_skills() -> list[dict[str, Any]]:
    ensure_builtin_video_skills()
    return [skill.model_dump(mode="json") for skill in VideoSkillRegistry.list()]


def propose_skill_draft(
    *,
    name: str,
    description: str,
    accepted_intentions: list[str],
    required_tools: list[str],
    required_models: list[str] | None = None,
) -> VideoSkillDefinition:
    """Create an unregistered draft skill proposal. Registration is a separate step."""
    skill_id = f"draft_{new_id().replace('-', '')[:12]}"
    return VideoSkillDefinition(
        id=skill_id,
        name=str(name or "Untitled Video Skill").strip() or "Untitled Video Skill",
        description=str(description or "").strip(),
        accepted_intentions=[str(x).strip() for x in accepted_intentions if str(x).strip()],
        required_tools=[str(x).strip() for x in required_tools if str(x).strip()],
        required_models=[str(x).strip() for x in (required_models or []) if str(x).strip()],
        status="draft",
        approval_rules={"requires_approval": True, "registration_separate_from_execution": True},
        verification_rules=["skill_registered_before_execution"],
    )


# Register builtins on import so catalog/capability reports stay consistent.
ensure_builtin_video_skills()
