"""Capability report for deterministic editing + model adapters.

Echo asks "what can this runtime do?" rather than hardcoding model names into
planning or mutation paths.
"""

from __future__ import annotations

from typing import Any, Optional

from agent.video_editor.adapters import VideoAdapterRegistry
from agent.video_editor.models import VideoCapabilityReport, VideoModelCapability
from agent.video_editor.skills import list_video_skill_ids
from agent.video_editor.tool_catalog import list_video_tool_names


# Canonical capability tokens used by planners and skills (not model names).
CANONICAL_MODEL_CAPABILITIES = (
    "video_understanding",
    "transcription",
    "scene_detection",
    "tracking",
    "video_to_video",
    "text_to_video",
    "image_to_video",
    "video_extension",
    "inpainting",
    "interpolation",
    "upscaling",
    "audio_to_video",
)


def _adapter_capability_map(adapters: list[dict[str, Any]]) -> dict[str, VideoModelCapability]:
    rows: dict[str, VideoModelCapability] = {
        cap: VideoModelCapability(capability=cap, available=False) for cap in CANONICAL_MODEL_CAPABILITIES
    }
    for adapter in adapters:
        adapter_id = str(adapter.get("adapter_id") or "")
        location = str(adapter.get("location") or "")
        available = bool(adapter.get("available"))
        notes = [str(n) for n in (adapter.get("notes") or []) if str(n)]
        for op in adapter.get("operations") or ():
            key = str(op or "").strip().replace("-", "_")
            # Normalize common aliases onto canonical tokens.
            alias = {
                "text_to_video": "text_to_video",
                "image_to_video": "image_to_video",
                "video_to_video": "video_to_video",
                "audio_to_video": "audio_to_video",
                "extend": "video_extension",
                "retake": "video_to_video",
                "understand": "video_understanding",
                "transcribe": "transcription",
                "scene_detect": "scene_detection",
                "track": "tracking",
                "inpaint": "inpainting",
                "interpolate": "interpolation",
                "upscale": "upscaling",
            }.get(key, key)
            if alias not in rows:
                rows[alias] = VideoModelCapability(capability=alias, available=False)
            entry = rows[alias]
            if adapter_id and adapter_id not in entry.adapter_ids:
                entry.adapter_ids.append(adapter_id)
            if location and location not in entry.locations:
                entry.locations.append(location)
            if available:
                entry.available = True
            for note in notes:
                if note not in entry.notes:
                    entry.notes.append(note)
    return rows


def build_video_capability_report(
    *,
    authority: Optional[dict[str, Any]] = None,
    media_probe_available: Optional[bool] = None,
) -> VideoCapabilityReport:
    """Return a structured capability report for planning and frontend projection."""
    adapters = VideoAdapterRegistry.capabilities()
    model_caps = list(_adapter_capability_map(adapters).values())
    generative_available = any(
        cap.available and cap.capability in {"text_to_video", "image_to_video", "video_to_video", "audio_to_video"}
        for cap in model_caps
    )
    analysis_available = any(
        cap.available and cap.capability in {"video_understanding", "scene_detection", "tracking", "transcription"}
        for cap in model_caps
    )
    transcription_available = any(cap.available and cap.capability == "transcription" for cap in model_caps)

    # Probe availability is environment-dependent; default false until checked.
    probe_ok = bool(media_probe_available) if media_probe_available is not None else _probe_ffprobe()

    authority = dict(authority or {})
    mutation_allowed = bool(authority.get("mutation_allowed"))
    blocked: list[str] = []
    if not authority.get("system_actions", True):
        blocked.append("ENABLE_SYSTEM_ACTIONS is disabled for agent mutations")
    if not authority.get("video_agent_edits", True):
        blocked.append("ALLOW_VIDEO_AGENT_EDITS is disabled for agent mutations")
    if not generative_available:
        blocked.append("No generative video adapter is currently available")
    if not analysis_available:
        blocked.append("No analysis/transcription adapter is currently available")
    if not probe_ok:
        blocked.append("ffprobe is not available for media inspect")

    return VideoCapabilityReport(
        deterministic_editing=True,
        media_probe=probe_ok,
        timeline_mutation=True,
        agent_proposals=True,
        approvals=True,
        undo_redo=True,
        # Workers are shells until real render/export pipelines ship.
        render_preview=False,
        export=False,
        analysis=analysis_available,
        transcription=transcription_available,
        generative_video=generative_available,
        research=True,
        model_capabilities=model_caps,
        adapters=adapters,
        available_tools=list_video_tool_names(),
        available_skills=list_video_skill_ids(),
        blocked_reasons=blocked,
    )


def _probe_ffprobe() -> bool:
    try:
        import shutil

        return bool(shutil.which("ffprobe"))
    except Exception:
        return False


def capability_available(report: VideoCapabilityReport, capability: str) -> bool:
    token = str(capability or "").strip()
    if not token:
        return False
    # Deterministic / runtime features.
    runtime = {
        "deterministic_editing": report.deterministic_editing,
        "media_probe": report.media_probe,
        "timeline_mutation": report.timeline_mutation,
        "agent_proposals": report.agent_proposals,
        "approvals": report.approvals,
        "undo_redo": report.undo_redo,
        "render_preview": report.render_preview,
        "export": report.export,
        "analysis": report.analysis,
        "transcription": report.transcription,
        "generative_video": report.generative_video,
        "research": report.research,
    }
    if token in runtime:
        return bool(runtime[token])
    return any(item.capability == token and item.available for item in report.model_capabilities)
