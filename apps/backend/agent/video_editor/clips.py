"""Canonical timeline clip ownership.

Clips live only under ``document.timeline.tracks[].clips``.
There is no separate durable top-level clip collection.

API responses may *project* a flat ``clips`` list for clients, but that list is
always derived from tracks — never a second store.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from agent.video_editor.models import Clip, Track, VideoClipSummary, VideoProjectDocument


class ClipLookupError(LookupError):
    """Clip identity could not be resolved from durable timeline state."""


def iter_track_clips(document: VideoProjectDocument) -> Iterable[tuple[Track, Clip]]:
    """Yield (track, clip) from the single canonical timeline owner."""
    timeline = getattr(document, "timeline", None)
    if timeline is None:
        return
    for track in list(getattr(timeline, "tracks", None) or []):
        for clip in list(getattr(track, "clips", None) or []):
            yield track, clip


def list_clips(document: VideoProjectDocument) -> list[Clip]:
    return [clip for _track, clip in iter_track_clips(document)]


def clip_count(document: VideoProjectDocument) -> int:
    return sum(1 for _ in iter_track_clips(document))


def find_clip(document: VideoProjectDocument, clip_id: str) -> tuple[Track, Clip]:
    cid = str(clip_id or "").strip()
    if not cid:
        raise ClipLookupError("clip_id is required")
    for track, clip in iter_track_clips(document):
        if str(clip.id) == cid:
            return track, clip
    raise ClipLookupError(f"clip not found: {cid}")


def clip_exists(document: VideoProjectDocument, clip_id: str) -> bool:
    try:
        find_clip(document, clip_id)
        return True
    except ClipLookupError:
        return False


def project_clip_summaries(document: VideoProjectDocument) -> list[VideoClipSummary]:
    """API/editor projection — derived only from tracks."""
    items: list[VideoClipSummary] = []
    for track, clip in iter_track_clips(document):
        speed = 1.0
        try:
            speed = float((clip.metadata or {}).get("speed") or 1.0)
        except (TypeError, ValueError):
            speed = 1.0
        items.append(
            VideoClipSummary(
                id=clip.id,
                track_id=track.id,
                asset_id=clip.asset_id,
                name=clip.name,
                timeline_start=clip.timeline_start,
                source_in=clip.source_in,
                duration=clip.duration,
                enabled=clip.enabled,
                volume=float(clip.volume),
                opacity=float(clip.opacity),
                speed=speed,
            )
        )
    return items


def project_clips_json(document: VideoProjectDocument) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in project_clip_summaries(document)]


def document_api_dict(document: VideoProjectDocument) -> dict[str, Any]:
    """Serialize document with a derived flat clips projection for clients.

    Does not create a second durable owner — ``clips`` is always projected from
    ``timeline.tracks[].clips``.
    """
    data = document.model_dump(mode="json")
    projected = project_clips_json(document)
    data["clips"] = projected
    data["clip_count"] = len(projected)
    return data


def require_clips(document: VideoProjectDocument, clip_ids: list[str]) -> list[tuple[Track, Clip]]:
    found: list[tuple[Track, Clip]] = []
    for cid in clip_ids:
        found.append(find_clip(document, cid))
    return found


def clip_volume(document: VideoProjectDocument, clip_id: str) -> float:
    _track, clip = find_clip(document, clip_id)
    return float(clip.volume)
