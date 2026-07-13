"""Video creative memory boundaries.

Durable:
  style preferences, output format, creative objective, approved workflows,
  project conventions, unfinished plan IDs.

Never durable as personal memory:
  playhead, selection ranges, ephemeral UI focus.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from agent.video_editor.models import VideoCreativeMemory
from agent.video_editor.store import VideoEditorStore, VideoStoreError, get_video_editor_store


EPHEMERAL_SELECTION_KEYS = frozenset(
    {
        "playhead",
        "selected_clip_ids",
        "selected_track_ids",
        "selected_asset_ids",
        "selected_range_start",
        "selected_range_end",
        "visible_range_start",
        "visible_range_end",
    }
)


def sanitize_memory_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip ephemeral selection/playhead fields if a caller accidentally includes them."""
    cleaned = dict(payload or {})
    for key in EPHEMERAL_SELECTION_KEYS:
        cleaned.pop(key, None)
    # Nested selection objects are never memory.
    cleaned.pop("selection", None)
    cleaned.pop("playhead_ticks", None)
    return cleaned


def update_creative_memory(
    *,
    project_id: str,
    session_id: str,
    document_id: str = "",
    preferred_style: str = "",
    output_format: str = "",
    creative_objective: str = "",
    approved_workflow_choices: Optional[list[str]] = None,
    project_conventions: Optional[list[str]] = None,
    unfinished_plan_ids: Optional[list[str]] = None,
    store: Optional[VideoEditorStore] = None,
) -> VideoCreativeMemory:
    video_store = store or get_video_editor_store()
    project_id = str(project_id or "").strip()
    session_id = str(session_id or "").strip()
    if not project_id:
        raise VideoStoreError("project_id is required for creative memory")

    existing: Optional[VideoCreativeMemory] = None
    doc_id = str(document_id or "").strip()
    if doc_id:
        document = video_store.get_document(project_id, doc_id)
        existing = document.creative_memory

    base = existing or VideoCreativeMemory(project_id=project_id, session_id=session_id)
    updates: dict[str, Any] = {
        "project_id": project_id,
        "session_id": session_id or base.session_id,
        "updated_at": time.time(),
    }
    if preferred_style:
        updates["preferred_style"] = str(preferred_style).strip()[:240]
    if output_format:
        updates["output_format"] = str(output_format).strip()[:120]
    if creative_objective:
        updates["creative_objective"] = str(creative_objective).strip()[:480]
    if approved_workflow_choices is not None:
        updates["approved_workflow_choices"] = [
            str(item).strip()[:200] for item in approved_workflow_choices if str(item).strip()
        ][:24]
    if project_conventions is not None:
        updates["project_conventions"] = [
            str(item).strip()[:200] for item in project_conventions if str(item).strip()
        ][:24]
    if unfinished_plan_ids is not None:
        updates["unfinished_plan_ids"] = [str(item).strip() for item in unfinished_plan_ids if str(item).strip()][:32]

    memory = base.model_copy(update=updates)
    if doc_id:
        video_store.update_creative_memory(project_id, doc_id, memory)
    return memory
