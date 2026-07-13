"""Project-bound immutable media ingest and ffprobe metadata extraction."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from agent.projects import ProjectManager, get_project_manager
from agent.video_editor.models import (
    MediaAsset,
    MediaKind,
    MediaProvenance,
    MediaStream,
    Rational,
    RationalTime,
)
from config import config


class MediaProbeError(RuntimeError):
    pass


def _fraction(value: Any) -> Rational | None:
    text = str(value or "").strip()
    if not text or text in {"0/0", "N/A"}:
        return None
    try:
        result = Fraction(text)
        if result.denominator <= 0:
            return None
        return Rational(numerator=result.numerator, denominator=result.denominator)
    except Exception:
        return None


def _kind(codec_type: str) -> MediaKind:
    try:
        return MediaKind(str(codec_type or "unknown"))
    except Exception:
        return MediaKind.UNKNOWN


def _assert_project_file(project_root: Path, relative_path: str) -> Path:
    raw = str(relative_path or "").strip().replace("\\", "/")
    if not raw or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        raise MediaProbeError("Only Project-relative local file paths are accepted")
    candidate = (project_root / raw).resolve(strict=True)
    try:
        candidate.relative_to(project_root)
    except ValueError as exc:
        raise MediaProbeError("Media path is outside the Project root") from exc
    if not candidate.is_file():
        raise MediaProbeError("Media path is not a file")
    # Reject symlink/junction/reparse chains. On Windows st_file_attributes
    # exposes FILE_ATTRIBUTE_REPARSE_POINT (0x400).
    current = project_root
    for part in candidate.relative_to(project_root).parts:
        current = current / part
        try:
            stat = current.lstat()
        except OSError as exc:
            raise MediaProbeError(f"Media path is inaccessible: {current}") from exc
        if current.is_symlink() or bool(int(getattr(stat, "st_file_attributes", 0) or 0) & 0x400):
            raise MediaProbeError("Media paths may not traverse symlinks, junctions, or reparse points")
    return candidate


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def validate_asset_source(project_root: Path, asset: MediaAsset) -> Path:
    """Revalidate an imported asset's immutable identity before serving/using it."""
    source = _assert_project_file(project_root, asset.project_relative_path)
    stat = source.stat()
    if stat.st_size != asset.size_bytes or stat.st_mtime_ns != asset.mtime_ns:
        raise MediaProbeError("Media source changed after import; re-import it before use")
    if sha256_file(source) != asset.sha256:
        raise MediaProbeError("Media source digest changed after import; re-import it before use")
    return source


def probe_media(path: Path) -> dict[str, Any]:
    executable = str(getattr(config, "ffprobe_path", "ffprobe") or "ffprobe").strip() or "ffprobe"
    timeout = max(1, min(60, int(getattr(config, "video_ffprobe_timeout_seconds", 15) or 15)))
    command = [
        executable,
        "-v",
        "error",
        "-show_error",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MediaProbeError("ffprobe is not installed or VIDEO_FFPROBE_PATH is incorrect") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaProbeError(f"ffprobe timed out after {timeout} seconds") from exc
    stdout = bytes(result.stdout or b"")
    stderr = bytes(result.stderr or b"")
    if len(stdout) > 4_000_000 or len(stderr) > 1_000_000:
        raise MediaProbeError("ffprobe diagnostic output exceeded the safety limit")
    if result.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace")[-4000:]
        raise MediaProbeError(f"ffprobe rejected the media (exit {result.returncode}): {detail}")
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except Exception as exc:
        raise MediaProbeError(f"ffprobe returned malformed JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
        raise MediaProbeError("ffprobe returned no stream inventory")
    if payload.get("error"):
        raise MediaProbeError(f"ffprobe reported an authoritative error: {payload['error']}")
    return payload


def build_asset_from_probe(
    project_id: str,
    document_id: str,
    relative_path: str,
    *,
    project_manager: ProjectManager | None = None,
    session_id: str = "",
) -> MediaAsset:
    manager = project_manager or get_project_manager()
    project = manager.get_project(project_id)
    if project is None or project.archived:
        raise MediaProbeError("Project does not exist or is archived")
    project_root = Path(str(project.workspace_root or "")).expanduser().resolve(strict=True)
    source = _assert_project_file(project_root, relative_path)
    initial_stat = source.stat()
    probe = probe_media(source)
    streams: list[MediaStream] = []
    duration: RationalTime | None = None
    primary_kind = MediaKind.UNKNOWN
    for row in probe.get("streams") or []:
        if not isinstance(row, dict):
            continue
        stream_kind = _kind(row.get("codec_type"))
        if primary_kind == MediaKind.UNKNOWN and stream_kind != MediaKind.UNKNOWN:
            primary_kind = stream_kind
        time_base = _fraction(row.get("time_base"))
        duration_ticks = str(row.get("duration_ts") or "").strip() or None
        if duration is None and time_base is not None and duration_ticks and duration_ticks.lstrip("-").isdigit():
            duration = RationalTime(ticks=duration_ticks, time_base=time_base)
        tags = row.get("tags") if isinstance(row.get("tags"), dict) else {}
        side_data = row.get("side_data_list") if isinstance(row.get("side_data_list"), list) else []
        rotation = tags.get("rotate")
        for side in side_data:
            if isinstance(side, dict) and side.get("rotation") is not None:
                rotation = side.get("rotation")
        streams.append(
            MediaStream(
                index=int(row.get("index") or 0),
                kind=stream_kind,
                codec=str(row.get("codec_name") or ""),
                time_base=time_base,
                duration_ticks=duration_ticks,
                average_frame_rate=_fraction(row.get("avg_frame_rate")),
                nominal_frame_rate=_fraction(row.get("r_frame_rate")),
                sample_rate=int(row["sample_rate"]) if str(row.get("sample_rate") or "").isdigit() else None,
                channels=int(row["channels"]) if str(row.get("channels") or "").isdigit() else None,
                width=int(row["width"]) if str(row.get("width") or "").isdigit() else None,
                height=int(row["height"]) if str(row.get("height") or "").isdigit() else None,
                pixel_format=str(row.get("pix_fmt") or ""),
                color={
                    key: row.get(key)
                    for key in ("color_range", "color_space", "color_transfer", "color_primaries")
                    if row.get(key) is not None
                },
                rotation_degrees=int(rotation) if str(rotation or "").lstrip("-").isdigit() else None,
                disposition=dict(row.get("disposition") or {}),
            )
        )
    if not streams:
        raise MediaProbeError("ffprobe found no usable streams")
    try:
        relative = source.relative_to(project_root).as_posix()
    except ValueError as exc:  # belt-and-suspenders after canonical validation
        raise MediaProbeError("Media path escaped the Project root") from exc
    digest = sha256_file(source)
    final_stat = source.stat()
    initial_identity = (
        initial_stat.st_size,
        initial_stat.st_mtime_ns,
        getattr(initial_stat, "st_dev", None),
        getattr(initial_stat, "st_ino", None),
    )
    final_identity = (
        final_stat.st_size,
        final_stat.st_mtime_ns,
        getattr(final_stat, "st_dev", None),
        getattr(final_stat, "st_ino", None),
    )
    if initial_identity != final_identity:
        raise MediaProbeError("Media source changed while it was being probed; retry the import")
    # Re-run path/reparse validation after the slow probe/hash boundary.
    if _assert_project_file(project_root, relative_path) != source:
        raise MediaProbeError("Media source identity changed while it was being probed")
    return MediaAsset(
        project_id=project_id,
        document_id=document_id,
        name=source.name,
        kind=primary_kind,
        project_relative_path=relative,
        sha256=digest,
        size_bytes=final_stat.st_size,
        mtime_ns=final_stat.st_mtime_ns,
        duration=duration,
        streams=streams,
        container={
            "format": dict(probe.get("format") or {}),
            "chapters": list(probe.get("chapters") or []),
            "probe_program": "ffprobe",
        },
        provenance=MediaProvenance(
            origin="imported",
            source_sha256=digest,
            source_session_id=session_id,
        ),
    )
