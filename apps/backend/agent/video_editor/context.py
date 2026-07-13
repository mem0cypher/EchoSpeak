"""Structured editor-context contract for Echo.

Echo must receive this object — never infer editor state from chat prose or
frontend layout strings.
"""

from __future__ import annotations

from typing import Any, Optional

from agent.video_editor.capabilities import build_video_capability_report
from agent.video_editor.models import (
    EditorSelectionContext,
    VideoAssetSummary,
    VideoAuthoritySnapshot,
    VideoClipSummary,
    VideoCreativeMemory,
    VideoEditorContext,
    VideoJobSummary,
    VideoPlanSummary,
    VideoProjectDocument,
    VideoTrackSummary,
)
from agent.video_editor.store import VideoEditorStore, VideoStoreError, get_video_editor_store


def _clip_speed(clip) -> float:
    try:
        return float((clip.metadata or {}).get("speed", 1.0))
    except (TypeError, ValueError):
        return 1.0


def _authority_snapshot(
    *,
    session_id: str,
    project_id: str,
    thread_state: Any = None,
    config: Any = None,
) -> VideoAuthoritySnapshot:
    permissions = dict(getattr(thread_state, "permissions", None) or {})
    system_actions = bool(permissions.get("system_actions"))
    video_edits = bool(permissions.get("video_agent_edits"))
    if config is not None:
        system_actions = system_actions or bool(getattr(config, "enable_system_actions", False))
        video_edits = video_edits or bool(getattr(config, "allow_video_agent_edits", False))
    allowed = [
        name
        for name in (getattr(thread_state, "allowed_tool_names", None) or [])
        if str(name).startswith("video_")
    ]
    if system_actions and video_edits and "video_apply_transaction" not in allowed:
        allowed = sorted(set(allowed) | {"video_apply_transaction", "video_propose_operations"})
    constraints = [str(item) for item in (getattr(thread_state, "constraints", None) or [])]
    constraint_text = "\n".join(constraints).lower()
    blocked_by_constraint = any(
        token in constraint_text
        for token in ("read_only", "read-only", "do not modify", "don't modify", "no_modify", "proposal_only")
    )
    mutation_allowed = bool(system_actions and video_edits and not blocked_by_constraint)
    return VideoAuthoritySnapshot(
        session_id=session_id,
        project_id=project_id,
        project_attached=bool(getattr(thread_state, "active_project_id", "") == project_id) if thread_state else True,
        system_actions=system_actions,
        video_agent_edits=video_edits,
        allowed_video_tools=allowed,
        constraints=constraints,
        pending_approval_id=str(getattr(thread_state, "pending_approval_id", "") or ""),
        mutation_allowed=mutation_allowed,
    )


def _summarize_document(document: VideoProjectDocument) -> tuple[
    list[VideoTrackSummary],
    list[VideoClipSummary],
    list[VideoAssetSummary],
    list[VideoJobSummary],
    list[VideoPlanSummary],
]:
    tracks: list[VideoTrackSummary] = []
    clips: list[VideoClipSummary] = []
    for track in document.timeline.tracks:
        tracks.append(
            VideoTrackSummary(
                id=track.id,
                name=track.name,
                kind=track.kind,
                order=track.order,
                locked=track.locked,
                muted=track.muted,
                clip_count=len(track.clips),
                clip_ids=[clip.id for clip in track.clips],
            )
        )
        for clip in track.clips:
            clips.append(
                VideoClipSummary(
                    id=clip.id,
                    track_id=track.id,
                    asset_id=clip.asset_id,
                    name=clip.name,
                    timeline_start=clip.timeline_start,
                    source_in=clip.source_in,
                    duration=clip.duration,
                    enabled=clip.enabled,
                    volume=clip.volume,
                    opacity=clip.opacity,
                    speed=_clip_speed(clip),
                )
            )
    assets: list[VideoAssetSummary] = []
    for asset in [*document.assets, *document.generated_assets]:
        origin = getattr(getattr(asset, "provenance", None), "origin", None)
        assets.append(
            VideoAssetSummary(
                id=asset.id,
                name=asset.name,
                kind=asset.kind,
                origin=origin or "imported",
                duration=asset.duration,
                project_relative_path=asset.project_relative_path,
                sha256=asset.sha256,
            )
        )
    active_jobs = [
        VideoJobSummary(
            id=job.id,
            kind=job.kind,
            status=job.status,
            progress=job.progress,
            adapter_id=job.adapter_id,
            capability=job.capability,
            tool_run_id=job.tool_run_id,
            execution_id=job.execution_id,
            expected_revision=job.expected_revision,
            cancel_requested=job.cancel_requested,
            error=job.error,
        )
        for job in document.jobs
        if job.status not in {"completed", "canceled", "failed"}
    ]
    pending_plans = [
        VideoPlanSummary(
            id=plan.id,
            objective=plan.objective,
            status=plan.status,
            expected_revision=plan.expected_revision,
            operation_count=len(plan.operations),
            skill_id=plan.skill_id,
            transaction_id=plan.transaction_id,
            approval_id=plan.approval_id,
        )
        for plan in document.plans
        if plan.status in {"draft", "proposed"}
    ]
    return tracks, clips, assets, active_jobs, pending_plans


def build_editor_context(
    *,
    session_id: str,
    project_id: str,
    document_id: str = "",
    selection: Optional[EditorSelectionContext] = None,
    store: Optional[VideoEditorStore] = None,
    thread_state: Any = None,
    config: Any = None,
    creative_memory: Optional[VideoCreativeMemory] = None,
) -> VideoEditorContext:
    """Build structured editor context. Opening the editor alone is not enough —
    callers must supply Session/Project identity; no auto Session creation.
    """
    session_id = str(session_id or "").strip()
    project_id = str(project_id or "").strip()
    if not session_id or not project_id:
        raise VideoStoreError("session_id and project_id are required for editor context")

    authority = _authority_snapshot(
        session_id=session_id,
        project_id=project_id,
        thread_state=thread_state,
        config=config,
    )
    capabilities = build_video_capability_report(
        authority={
            "system_actions": authority.system_actions,
            "video_agent_edits": authority.video_agent_edits,
            "mutation_allowed": authority.mutation_allowed,
        }
    )

    video_store = store or get_video_editor_store()
    document: Optional[VideoProjectDocument] = None
    doc_id = str(document_id or "").strip()
    if doc_id:
        document = video_store.get_document(project_id, doc_id)
    else:
        documents = video_store.list_documents(project_id)
        document = documents[0] if documents else None

    if document is None:
        return VideoEditorContext(
            project_id=project_id,
            session_id=session_id,
            authority=authority,
            capabilities=capabilities,
            creative_memory=creative_memory,
            selection=selection,
        )

    # Clips projected from the single owner: timeline.tracks[].clips
    from agent.video_editor.clips import project_clip_summaries

    tracks, _legacy_clips, assets, active_jobs, pending_plans = _summarize_document(document)
    clips = project_clip_summaries(document)
    unfinished = pending_plans[0] if pending_plans else None
    mem = creative_memory or document.creative_memory
    if mem is not None and not mem.unfinished_plan_ids and pending_plans:
        mem = mem.model_copy(update={"unfinished_plan_ids": [p.id for p in pending_plans]})

    # Selection is ephemeral: drop it if it targets a different revision/document.
    bound_selection = selection
    if bound_selection is not None:
        if bound_selection.document_id and bound_selection.document_id != document.id:
            bound_selection = None
        elif bound_selection.document_revision != document.revision:
            # Stale selection is not applied as truth — surface revision only.
            bound_selection = bound_selection.model_copy(update={"document_revision": document.revision})

    return VideoEditorContext(
        project_id=project_id,
        session_id=session_id,
        document_id=document.id,
        document_name=document.name,
        document_revision=document.revision,
        head_revision_id=document.head_revision_id,
        timeline_id=document.timeline.id,
        time_base=document.timeline.time_base,
        tracks=tracks,
        clips=clips,
        assets=assets,
        selection=bound_selection,
        active_jobs=active_jobs,
        pending_plans=pending_plans,
        capabilities=capabilities,
        authority=authority,
        creative_memory=mem,
        unfinished_plan=unfinished,
    )


def editor_context_for_prompt(context: VideoEditorContext, *, max_chars: int = 6000) -> str:
    """Compact machine-readable block for system/agent injection (not free-form UI dump)."""
    payload = context.model_dump(mode="json")
    # Strip heavy adapter dumps for prompt injection.
    caps = payload.get("capabilities") or {}
    if isinstance(caps, dict):
        caps.pop("adapters", None)
        payload["capabilities"] = caps
    import json

    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(text) > max_chars:
        return text[: max_chars - 20] + ',"truncated":true}'
    return text
