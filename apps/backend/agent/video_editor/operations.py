"""Pure, validated timeline operation engine."""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from agent.video_editor.models import (
    Clip,
    EditOperation,
    EditOperationType,
    MediaKind,
    Rational,
    RationalTime,
    Track,
    VideoProjectDocument,
    new_id,
)


class VideoOperationError(ValueError):
    pass


def as_fraction(value: RationalTime) -> Fraction:
    return Fraction(int(value.ticks) * value.time_base.numerator, value.time_base.denominator)


def from_fraction(value: Fraction, time_base: Rational) -> RationalTime:
    ticks = value / Fraction(time_base.numerator, time_base.denominator)
    if ticks.denominator != 1:
        raise VideoOperationError("time is not exactly representable in the target time base")
    return RationalTime(ticks=str(ticks.numerator), time_base=time_base)


def _time(payload: dict[str, Any], key: str, *, required: bool = True) -> RationalTime | None:
    raw = payload.get(key)
    if raw is None and not required:
        return None
    if raw is None:
        raise VideoOperationError(f"missing {key}")
    try:
        value = raw if isinstance(raw, RationalTime) else RationalTime.model_validate(raw)
    except Exception as exc:
        raise VideoOperationError(f"invalid {key}: {exc}") from exc
    return value


def _track(document: VideoProjectDocument, track_id: str) -> Track:
    match = next((item for item in document.timeline.tracks if item.id == track_id), None)
    if match is None:
        raise VideoOperationError(f"track not found: {track_id}")
    return match


def _clip(document: VideoProjectDocument, clip_id: str) -> tuple[Track, Clip]:
    # Canonical owner: document.timeline.tracks[].clips (see agent.video_editor.clips).
    from agent.video_editor.clips import ClipLookupError, find_clip

    try:
        return find_clip(document, clip_id)
    except ClipLookupError as exc:
        raise VideoOperationError(str(exc)) from exc


def _assert_nonnegative(value: RationalTime, label: str, *, positive: bool = False) -> None:
    actual = as_fraction(value)
    if actual < 0 or (positive and actual == 0):
        raise VideoOperationError(f"{label} must be {'positive' if positive else 'nonnegative'}")


def _asset_duration(document: VideoProjectDocument, asset_id: str) -> Fraction | None:
    asset = _asset(document, asset_id)
    return as_fraction(asset.duration) if asset.duration else None


def _asset(document: VideoProjectDocument, asset_id: str):
    asset = next((item for item in [*document.assets, *document.generated_assets] if item.id == asset_id), None)
    if asset is None:
        raise VideoOperationError(f"asset not found: {asset_id}")
    return asset


def _assert_track_accepts_asset(track: Track, asset_kind: MediaKind) -> None:
    compatible = {
        MediaKind.VIDEO: {MediaKind.VIDEO, MediaKind.IMAGE},
        MediaKind.AUDIO: {MediaKind.AUDIO},
        MediaKind.IMAGE: {MediaKind.IMAGE},
        MediaKind.CAPTION: {MediaKind.CAPTION},
    }
    if asset_kind not in compatible.get(track.kind, {track.kind}):
        raise VideoOperationError(
            f"{asset_kind.value} asset is incompatible with {track.kind.value} track"
        )


def _assert_source_bounds(document: VideoProjectDocument, clip: Clip) -> None:
    _assert_nonnegative(clip.timeline_start, "timeline_start")
    _assert_nonnegative(clip.source_in, "source_in")
    _assert_nonnegative(clip.duration, "duration", positive=True)
    duration = _asset_duration(document, clip.asset_id)
    if duration is not None and as_fraction(clip.source_in) + as_fraction(clip.duration) > duration:
        raise VideoOperationError("clip source range exceeds immutable asset duration")


def _assert_no_overlap(track: Track, candidate: Clip, *, ignore_clip_id: str = "") -> None:
    start = as_fraction(candidate.timeline_start)
    end = start + as_fraction(candidate.duration)
    for existing in track.clips:
        if existing.id == ignore_clip_id or not existing.enabled:
            continue
        other_start = as_fraction(existing.timeline_start)
        other_end = other_start + as_fraction(existing.duration)
        if start < other_end and other_start < end:
            raise VideoOperationError(f"clip overlaps {existing.id} on track {track.id}")


def _sort_tracks(document: VideoProjectDocument) -> None:
    document.timeline.tracks.sort(key=lambda item: (item.order, item.id))
    for track in document.timeline.tracks:
        track.clips.sort(key=lambda item: (as_fraction(item.timeline_start), item.id))


def apply_operation(document: VideoProjectDocument, operation: EditOperation) -> VideoProjectDocument:
    if operation.expected_revision != document.revision:
        raise VideoOperationError(
            f"stale document revision: expected {operation.expected_revision}, current {document.revision}"
        )
    next_document = document.model_copy(deep=True)
    payload = dict(operation.payload or {})
    kind = operation.operation_type

    if kind == EditOperationType.ADD_TRACK:
        raw_kind = str(payload.get("kind") or "video")
        try:
            media_kind = MediaKind(raw_kind)
        except Exception as exc:
            raise VideoOperationError(f"invalid track kind: {raw_kind}") from exc
        track_id = str(payload.get("track_id") or new_id())
        if any(track.id == track_id for track in next_document.timeline.tracks):
            raise VideoOperationError(f"duplicate track id: {track_id}")
        order = int(payload.get("order", len(next_document.timeline.tracks)))
        next_document.timeline.tracks.append(
            Track(id=track_id, kind=media_kind, name=str(payload.get("name") or f"{media_kind.value.title()} Track"), order=order)
        )

    elif kind == EditOperationType.INSERT_CLIP:
        track = _track(next_document, str(payload.get("track_id") or ""))
        if track.locked:
            raise VideoOperationError("track is locked")
        asset_id = str(payload.get("asset_id") or "")
        asset = _asset(next_document, asset_id)
        _assert_track_accepts_asset(track, asset.kind)
        duration = _time(payload, "duration", required=False)
        if duration is None:
            duration = asset.duration
        if duration is None:
            raise VideoOperationError("duration is required when asset duration is unknown")
        clip = Clip(
            id=str(payload.get("clip_id") or new_id()),
            asset_id=asset_id,
            name=str(payload.get("name") or ""),
            timeline_start=_time(payload, "timeline_start"),
            source_in=_time(payload, "source_in", required=False) or RationalTime(),
            duration=duration,
        )
        if any(existing.id == clip.id for item in next_document.timeline.tracks for existing in item.clips):
            raise VideoOperationError(f"duplicate clip id: {clip.id}")
        _assert_source_bounds(next_document, clip)
        _assert_no_overlap(track, clip)
        track.clips.append(clip)

    elif kind == EditOperationType.SPLIT_CLIP:
        track, clip = _clip(next_document, str(payload.get("clip_id") or ""))
        if track.locked:
            raise VideoOperationError("track is locked")
        at = _time(payload, "at")
        offset = as_fraction(at) - as_fraction(clip.timeline_start)
        total = as_fraction(clip.duration)
        if offset <= 0 or offset >= total:
            raise VideoOperationError("split point must be strictly inside the clip")
        left = clip.model_copy(deep=True)
        right = clip.model_copy(deep=True)
        left.duration = from_fraction(offset, clip.duration.time_base)
        right.id = str(payload.get("right_clip_id") or new_id())
        if any(
            existing.id == right.id
            for item in next_document.timeline.tracks
            for existing in item.clips
        ):
            raise VideoOperationError(f"duplicate clip id: {right.id}")
        right.timeline_start = at
        right.source_in = from_fraction(as_fraction(clip.source_in) + offset, clip.source_in.time_base)
        right.duration = from_fraction(total - offset, clip.duration.time_base)
        index = next(i for i, item in enumerate(track.clips) if item.id == clip.id)
        track.clips[index:index + 1] = [left, right]

    elif kind == EditOperationType.TRIM_CLIP:
        track, clip = _clip(next_document, str(payload.get("clip_id") or ""))
        if track.locked:
            raise VideoOperationError("track is locked")
        source_in = _time(payload, "source_in", required=False) or clip.source_in
        duration = _time(payload, "duration", required=False) or clip.duration
        timeline_start = _time(payload, "timeline_start", required=False) or clip.timeline_start
        candidate = clip.model_copy(update={"source_in": source_in, "duration": duration, "timeline_start": timeline_start})
        _assert_source_bounds(next_document, candidate)
        _assert_no_overlap(track, candidate, ignore_clip_id=clip.id)
        index = next(i for i, item in enumerate(track.clips) if item.id == clip.id)
        track.clips[index] = candidate

    elif kind == EditOperationType.MOVE_CLIP:
        source_track, clip = _clip(next_document, str(payload.get("clip_id") or ""))
        target_track = _track(next_document, str(payload.get("track_id") or source_track.id))
        if source_track.locked or target_track.locked:
            raise VideoOperationError("source or destination track is locked")
        _assert_track_accepts_asset(target_track, _asset(next_document, clip.asset_id).kind)
        candidate = clip.model_copy(update={"timeline_start": _time(payload, "timeline_start")})
        _assert_nonnegative(candidate.timeline_start, "timeline_start")
        _assert_no_overlap(target_track, candidate, ignore_clip_id=clip.id)
        source_track.clips = [item for item in source_track.clips if item.id != clip.id]
        target_track.clips.append(candidate)

    elif kind == EditOperationType.DELETE_CLIP:
        track, clip = _clip(next_document, str(payload.get("clip_id") or ""))
        if track.locked:
            raise VideoOperationError("track is locked")
        track.clips = [item for item in track.clips if item.id != clip.id]

    elif kind == EditOperationType.SET_CLIP_VOLUME:
        track, clip = _clip(next_document, str(payload.get("clip_id") or ""))
        if track.locked:
            raise VideoOperationError("track is locked")
        try:
            volume = float(payload.get("volume"))
        except (TypeError, ValueError) as exc:
            raise VideoOperationError("volume must be a number") from exc
        if volume < 0.0 or volume > 4.0:
            raise VideoOperationError("volume must be between 0 and 4")
        index = next(i for i, item in enumerate(track.clips) if item.id == clip.id)
        track.clips[index] = clip.model_copy(update={"volume": volume})

    elif kind == EditOperationType.SET_CLIP_OPACITY:
        track, clip = _clip(next_document, str(payload.get("clip_id") or ""))
        if track.locked:
            raise VideoOperationError("track is locked")
        try:
            opacity = float(payload.get("opacity"))
        except (TypeError, ValueError) as exc:
            raise VideoOperationError("opacity must be a number") from exc
        if opacity < 0.0 or opacity > 1.0:
            raise VideoOperationError("opacity must be between 0 and 1")
        index = next(i for i, item in enumerate(track.clips) if item.id == clip.id)
        track.clips[index] = clip.model_copy(update={"opacity": opacity})

    elif kind == EditOperationType.SET_CLIP_TRANSFORM:
        track, clip = _clip(next_document, str(payload.get("clip_id") or ""))
        if track.locked:
            raise VideoOperationError("track is locked")
        transform = payload.get("transform")
        if not isinstance(transform, dict):
            raise VideoOperationError("transform must be an object")
        # Allowlist framing keys only — never accept executable filter strings.
        allowed = {"x", "y", "scale", "scale_x", "scale_y", "rotation", "crop"}
        cleaned = {str(k): transform[k] for k in transform if str(k) in allowed}
        if not cleaned:
            raise VideoOperationError("transform must include at least one allowlisted framing key")
        index = next(i for i, item in enumerate(track.clips) if item.id == clip.id)
        merged = {**(clip.transform or {}), **cleaned}
        track.clips[index] = clip.model_copy(update={"transform": merged})

    elif kind == EditOperationType.SET_CLIP_SPEED:
        track, clip = _clip(next_document, str(payload.get("clip_id") or ""))
        if track.locked:
            raise VideoOperationError("track is locked")
        try:
            speed = float(payload.get("speed"))
        except (TypeError, ValueError) as exc:
            raise VideoOperationError("speed must be a number") from exc
        if speed <= 0.0 or speed > 16.0:
            raise VideoOperationError("speed must be in (0, 16]")
        index = next(i for i, item in enumerate(track.clips) if item.id == clip.id)
        meta = dict(clip.metadata or {})
        meta["speed"] = speed
        track.clips[index] = clip.model_copy(update={"metadata": meta})

    elif kind == EditOperationType.SET_CLIP_ENABLED:
        track, clip = _clip(next_document, str(payload.get("clip_id") or ""))
        if track.locked:
            raise VideoOperationError("track is locked")
        if "enabled" not in payload:
            raise VideoOperationError("enabled is required")
        enabled = bool(payload.get("enabled"))
        index = next(i for i, item in enumerate(track.clips) if item.id == clip.id)
        track.clips[index] = clip.model_copy(update={"enabled": enabled})

    else:  # pragma: no cover - Enum validation prevents this
        raise VideoOperationError(f"unsupported operation: {kind}")

    _sort_tracks(next_document)
    return next_document


def stage_transaction(document: VideoProjectDocument, operations: list[EditOperation]) -> VideoProjectDocument:
    """Validate all operations on a copy; caller commits only the final result."""
    staged = document.model_copy(deep=True)
    for operation in operations:
        operation = operation.model_copy(update={"expected_revision": document.revision})
        staged = apply_operation(staged, operation)
    return staged


def operation_preview(operation: EditOperation) -> dict[str, Any]:
    return {
        "operation_id": operation.id,
        "type": operation.operation_type.value,
        "expected_revision": operation.expected_revision,
        "affected_ids": [
            str(operation.payload.get(key) or "")
            for key in ("track_id", "clip_id", "asset_id")
            if str(operation.payload.get(key) or "")
        ],
        "payload": dict(operation.payload),
    }
