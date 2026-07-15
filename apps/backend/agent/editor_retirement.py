"""Fail-closed, non-destructive retirement export for the legacy Video Editor.

The legacy Editor store remains the authority while this module inventories it.
Retirement creates an immutable, byte-for-byte JSON archive plus a validated
Media Library import plan.  It never edits or removes the source tree.

Media registration is deliberately opt-in and requires an injected
``MediaLibraryStore`` whose resolved root exactly matches the caller's declared
root.  This keeps tests and rehearsals confined to disposable storage and makes
an accidental singleton/production-library write impossible.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from agent.media_library import MediaLibraryAsset, MediaLibraryStore


MIGRATION_NAME = "echospeak.video_editor.retirement"
MIGRATION_VERSION = 1
EXPORT_DIRECTORY = "video-editor-retirement-v1"
MANIFEST_NAME = "manifest.json"
RECEIPT_NAME = "media-import-receipt.json"
_SAFE_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_MEDIA_KINDS = frozenset({"image", "video", "audio", "caption", "unknown"})


class EditorRetirementError(RuntimeError):
    """A retirement precondition failed; no source data was changed."""


@dataclass(frozen=True)
class EditorRetirementResult:
    manifest_path: Path
    receipt_path: Optional[Path]
    reused_export: bool
    manifest: dict[str, Any]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _safe_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or any(character not in _SAFE_ID_CHARS for character in text):
        raise EditorRetirementError(f"Invalid {label} identity: {text!r}")
    return text


def _relative_text(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _assert_separate_roots(source: Path, destination: Path) -> None:
    if source == destination:
        raise EditorRetirementError("Retirement destination cannot be the legacy source")
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise EditorRetirementError("Retirement destination cannot be inside the legacy source")
    try:
        source.relative_to(destination)
    except ValueError:
        pass
    else:
        raise EditorRetirementError("Legacy source cannot be inside the retirement destination")


def _reject_symlink(path: Path, root: Path, label: str) -> None:
    current = path
    while current != root:
        if current.is_symlink():
            raise EditorRetirementError(f"{label} uses a symbolic link: {current}")
        parent = current.parent
        if parent == current:
            raise EditorRetirementError(f"{label} escaped its authority root: {path}")
        current = parent
    if root.is_symlink():
        raise EditorRetirementError(f"{label} authority root is a symbolic link: {root}")


def _write_bytes_fsync(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str).encode("utf-8") + b"\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    """Best-effort directory durability (directory handles are unavailable on Windows)."""

    if os.name == "nt":
        return
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _promote_directory(stage: Path, target: Path) -> None:
    """Atomically promote a completed export, tolerating brief Windows locks."""

    last_error: Optional[PermissionError] = None
    for attempt in range(6):
        try:
            os.replace(stage, target)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.02 * (attempt + 1))
    if last_error is not None:
        raise last_error


def _remove_generated_stage(stage: Path) -> None:
    """Remove only this migration's private staging directory after failure."""

    def make_writable_and_retry(function: Any, path: str, _error: Any) -> None:
        Path(path).chmod(stat.S_IREAD | stat.S_IWRITE)
        function(path)

    shutil.rmtree(stage, onerror=make_writable_and_retry)


def _load_object(data: bytes, relative_path: str) -> dict[str, Any]:
    try:
        payload = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise EditorRetirementError(f"Malformed legacy JSON at {relative_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise EditorRetirementError(f"Legacy JSON root must be an object: {relative_path}")
    return payload


def _object_field(payload: dict[str, Any], field: str, label: str) -> dict[str, Any]:
    value = payload.get(field) or {}
    if not isinstance(value, dict):
        raise EditorRetirementError(f"{label} {field} must be an object")
    return dict(value)


def _integer_field(payload: dict[str, Any], field: str, label: str) -> int:
    value = payload.get(field, 0)
    if isinstance(value, bool):
        raise EditorRetirementError(f"{label} {field} must be an integer")
    try:
        return int(value or 0)
    except (TypeError, ValueError) as exc:
        raise EditorRetirementError(f"{label} {field} must be an integer") from exc


def _float_field(payload: dict[str, Any], field: str, label: str) -> float:
    value = payload.get(field, 0.0)
    if isinstance(value, bool):
        raise EditorRetirementError(f"{label} {field} must be numeric")
    try:
        return float(value or 0.0)
    except (TypeError, ValueError) as exc:
        raise EditorRetirementError(f"{label} {field} must be numeric") from exc


def _tree_digest(files: list[dict[str, Any]]) -> str:
    return _sha256(
        _canonical([{"relative_path": row["relative_path"], "sha256": row["sha256"]} for row in files])
    )


def _snapshot_json_tree(source: Path) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    records: list[dict[str, Any]] = []
    snapshots: dict[str, bytes] = {}
    for path in sorted(source.rglob("*.json")):
        if not path.is_file():
            continue
        _reject_symlink(path, source, "Legacy JSON")
        relative = _relative_text(path, source)
        data = path.read_bytes()
        _load_object(data, relative)
        digest = _sha256(data)
        records.append({"relative_path": relative, "sha256": digest, "size_bytes": len(data)})
        snapshots[relative] = data
    if not records:
        raise EditorRetirementError("Legacy Video Editor source contains no JSON files")
    return records, snapshots


def _document_identity(payload: dict[str, Any], *, project_id: str, document_id: str, relative: str) -> None:
    if str(payload.get("project_id") or "") != project_id or str(payload.get("id") or "") != document_id:
        raise EditorRetirementError(f"Document identity does not match authority path: {relative}")


def _revision_identity(
    payload: dict[str, Any],
    *,
    project_id: str,
    document_id: str,
    revision_id: str,
    relative: str,
) -> dict[str, Any]:
    revision = payload.get("revision")
    document = payload.get("document")
    if not isinstance(revision, dict) or not isinstance(document, dict):
        raise EditorRetirementError(f"Revision snapshot requires revision and document objects: {relative}")
    if (
        str(revision.get("id") or "") != revision_id
        or str(revision.get("project_id") or "") != project_id
        or str(revision.get("document_id") or "") != document_id
        or str(document.get("project_id") or "") != project_id
        or str(document.get("id") or "") != document_id
    ):
        raise EditorRetirementError(f"Revision identity does not match authority path: {relative}")
    expected = str(revision.get("snapshot_sha256") or "").strip().lower()
    actual = _sha256(_canonical(document))
    if not expected or expected != actual:
        raise EditorRetirementError(f"Revision snapshot checksum changed or is invalid: {relative}")
    return document


def _asset_occurrences(
    document: dict[str, Any],
    *,
    project_id: str,
    document_id: str,
    relative: str,
) -> list[dict[str, Any]]:
    occurrences: list[dict[str, Any]] = []
    for collection in ("assets", "generated_assets"):
        rows = document.get(collection, [])
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise EditorRetirementError(f"{collection} must be a list: {relative}")
        for index, asset in enumerate(rows):
            if not isinstance(asset, dict):
                raise EditorRetirementError(f"Invalid asset record at {relative}#{collection}[{index}]")
            asset_id = _safe_id(asset.get("id"), "MediaAsset")
            if (
                str(asset.get("project_id") or "") != project_id
                or str(asset.get("document_id") or "") != document_id
            ):
                raise EditorRetirementError(f"MediaAsset ownership mismatch for {asset_id} at {relative}")
            occurrences.append(
                {
                    "asset": asset,
                    "collection": collection,
                    "source_file": relative,
                    "source_index": index,
                }
            )
    return occurrences


def _artifact_occurrences(
    document: dict[str, Any],
    *,
    project_id: str,
    document_id: str,
    relative: str,
) -> list[dict[str, Any]]:
    artifacts = document.get("artifacts", [])
    if artifacts is None:
        artifacts = []
    if not isinstance(artifacts, list):
        raise EditorRetirementError(f"artifacts must be a list: {relative}")
    records: list[dict[str, Any]] = []
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            raise EditorRetirementError(f"Invalid artifact at {relative}#artifacts[{index}]")
        artifact_id = _safe_id(artifact.get("id"), "artifact")
        artifact_project = str(artifact.get("project_id") or "")
        artifact_document = str(artifact.get("document_id") or "")
        if artifact_project != project_id or (artifact_document and artifact_document != document_id):
            raise EditorRetirementError(f"Artifact ownership mismatch for {artifact_id} at {relative}")
        records.append(
            {
                "id": artifact_id,
                "kind": str(artifact.get("kind") or "generic"),
                "project_id": project_id,
                "document_id": artifact_document or document_id,
                "source_file": relative,
                "source_index": index,
                "payload_sha256": _sha256(_canonical(artifact)),
            }
        )
    return records


def _normalized_relative_asset_path(value: Any, asset_id: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise EditorRetirementError(f"MediaAsset {asset_id} has no project-relative path")
    candidate = Path(text.replace("\\", "/"))
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise EditorRetirementError(f"MediaAsset {asset_id} path is outside Project authority: {text}")
    if any(part in ("", ".") for part in candidate.parts):
        raise EditorRetirementError(f"MediaAsset {asset_id} has an invalid project-relative path: {text}")
    return candidate


def _validate_asset(
    asset: dict[str, Any],
    *,
    project_roots: Mapping[str, Path],
) -> dict[str, Any]:
    asset_id = _safe_id(asset.get("id"), "MediaAsset")
    project_id = _safe_id(asset.get("project_id"), "Project")
    document_id = _safe_id(asset.get("document_id"), "video document")
    raw_relative_path = str(asset.get("project_relative_path") or "").strip()
    declared_sha = str(asset.get("sha256") or "").strip().lower()
    declared_size = asset.get("size_bytes")
    declared_mtime = asset.get("mtime_ns")
    provenance = _object_field(asset, "provenance", f"MediaAsset {asset_id}")
    created_at = _float_field(asset, "created_at", f"MediaAsset {asset_id}")
    if not raw_relative_path:
        return {
            "id": asset_id,
            "project_id": project_id,
            "document_id": document_id,
            "name": str(asset.get("name") or ""),
            "media_kind": str(asset.get("kind") or "unknown"),
            "project_relative_path": "",
            "declared_sha256": declared_sha,
            "declared_size_bytes": declared_size,
            "declared_mtime_ns": declared_mtime,
            "provenance": provenance,
            "created_at": created_at,
            "validation_status": "unverifiable",
            "validation_reason": "project_relative_path_missing",
        }
    relative_path = _normalized_relative_asset_path(raw_relative_path, asset_id)
    base = {
        "id": asset_id,
        "project_id": project_id,
        "document_id": document_id,
        "name": str(asset.get("name") or relative_path.name),
        "media_kind": str(asset.get("kind") or "unknown"),
        "project_relative_path": relative_path.as_posix(),
        "declared_sha256": declared_sha,
        "declared_size_bytes": declared_size,
        "declared_mtime_ns": declared_mtime,
        "provenance": provenance,
        "created_at": created_at,
    }
    supplied_root = project_roots.get(project_id)
    if supplied_root is None:
        return {**base, "validation_status": "unverifiable", "validation_reason": "project_root_not_supplied"}
    try:
        project_root = Path(supplied_root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise EditorRetirementError(f"Project root does not exist for {project_id}: {supplied_root}") from exc
    if not project_root.is_dir():
        raise EditorRetirementError(f"Project root is not a directory for {project_id}: {project_root}")
    candidate = project_root.joinpath(*relative_path.parts)
    unresolved = candidate.resolve(strict=False)
    try:
        unresolved.relative_to(project_root)
    except ValueError as exc:
        raise EditorRetirementError(f"MediaAsset {asset_id} path escaped Project authority") from exc
    if not candidate.exists():
        return {
            **base,
            "validation_status": "missing",
            "validation_reason": "file_not_found",
            "resolved_path": str(unresolved),
        }
    _reject_symlink(candidate, project_root, f"MediaAsset {asset_id}")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise EditorRetirementError(f"MediaAsset {asset_id} path escaped Project authority") from exc
    if not resolved.is_file():
        raise EditorRetirementError(f"MediaAsset {asset_id} does not identify a regular file")
    if len(declared_sha) != 64 or any(ch not in "0123456789abcdef" for ch in declared_sha):
        return {
            **base,
            "validation_status": "unverifiable",
            "validation_reason": "declared_sha256_missing_or_invalid",
            "resolved_path": str(resolved),
        }
    if not isinstance(declared_size, int) or isinstance(declared_size, bool) or declared_size < 0:
        return {
            **base,
            "validation_status": "unverifiable",
            "validation_reason": "declared_size_missing_or_invalid",
            "resolved_path": str(resolved),
        }
    if not isinstance(declared_mtime, int) or isinstance(declared_mtime, bool) or declared_mtime < 0:
        return {
            **base,
            "validation_status": "unverifiable",
            "validation_reason": "declared_mtime_missing_or_invalid",
            "resolved_path": str(resolved),
        }
    data = resolved.read_bytes()
    stat_result = resolved.stat()
    actual_sha = _sha256(data)
    if actual_sha != declared_sha:
        raise EditorRetirementError(f"MediaAsset {asset_id} checksum changed")
    if declared_size != len(data):
        raise EditorRetirementError(f"MediaAsset {asset_id} size/version precondition changed")
    if isinstance(declared_mtime, int) and declared_mtime > 0 and declared_mtime != stat_result.st_mtime_ns:
        raise EditorRetirementError(f"MediaAsset {asset_id} mtime/version precondition changed")
    kind = str(asset.get("kind") or "unknown")
    if kind not in _MEDIA_KINDS:
        kind = "unknown"
    return {
        **base,
        "media_kind": kind,
        "validation_status": "verified",
        "validation_reason": "checksum_size_and_scope_verified",
        "project_root": str(project_root),
        "resolved_path": str(resolved),
        "actual_sha256": actual_sha,
        "actual_size_bytes": len(data),
        "actual_mtime_ns": stat_result.st_mtime_ns,
    }


def _build_inventory(
    files: list[dict[str, Any]],
    snapshots: dict[str, bytes],
    *,
    project_roots: Mapping[str, Path],
) -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    revisions: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    other_json: list[dict[str, Any]] = []
    asset_occurrences: list[dict[str, Any]] = []
    document_ids: set[tuple[str, str]] = set()
    revision_ids: set[tuple[str, str, str]] = set()

    file_by_path = {row["relative_path"]: row for row in files}
    for relative in sorted(snapshots):
        payload = _load_object(snapshots[relative], relative)
        parts = Path(relative).parts
        is_document = len(parts) == 4 and parts[0] == "projects" and parts[2] == "documents"
        is_revision = len(parts) == 5 and parts[0] == "projects" and parts[2] == "revisions"
        if is_document:
            project_id = _safe_id(parts[1], "Project")
            document_id = _safe_id(Path(parts[3]).stem, "video document")
            key = (project_id, document_id)
            if key in document_ids:
                raise EditorRetirementError(f"Duplicate document identity: {project_id}/{document_id}")
            document_ids.add(key)
            _document_identity(payload, project_id=project_id, document_id=document_id, relative=relative)
            documents.append(
                {
                    "project_id": project_id,
                    "document_id": document_id,
                    "name": str(payload.get("name") or ""),
                    "head_revision": _integer_field(payload, "revision", f"Document {document_id}"),
                    **file_by_path[relative],
                }
            )
            asset_occurrences.extend(
                _asset_occurrences(payload, project_id=project_id, document_id=document_id, relative=relative)
            )
            artifacts.extend(
                _artifact_occurrences(payload, project_id=project_id, document_id=document_id, relative=relative)
            )
        elif is_revision:
            project_id = _safe_id(parts[1], "Project")
            document_id = _safe_id(parts[3], "video document")
            revision_id = _safe_id(Path(parts[4]).stem, "video revision")
            key = (project_id, document_id, revision_id)
            if key in revision_ids:
                raise EditorRetirementError(f"Duplicate revision identity: {project_id}/{document_id}/{revision_id}")
            revision_ids.add(key)
            snapshot_document = _revision_identity(
                payload,
                project_id=project_id,
                document_id=document_id,
                revision_id=revision_id,
                relative=relative,
            )
            revision = payload["revision"]
            revisions.append(
                {
                    "project_id": project_id,
                    "document_id": document_id,
                    "revision_id": revision_id,
                    "revision_number": _integer_field(
                        revision,
                        "revision_number",
                        f"Revision {revision_id}",
                    ),
                    **file_by_path[relative],
                }
            )
            asset_occurrences.extend(
                _asset_occurrences(
                    snapshot_document,
                    project_id=project_id,
                    document_id=document_id,
                    relative=relative,
                )
            )
            artifacts.extend(
                _artifact_occurrences(
                    snapshot_document,
                    project_id=project_id,
                    document_id=document_id,
                    relative=relative,
                )
            )
        else:
            other_json.append(dict(file_by_path[relative]))

    assets_by_id: dict[str, dict[str, Any]] = {}
    signatures: dict[str, tuple[str, str, str, str, str]] = {}
    for occurrence in asset_occurrences:
        raw = occurrence["asset"]
        asset_id = _safe_id(raw.get("id"), "MediaAsset")
        signature = (
            str(raw.get("project_id") or ""),
            str(raw.get("document_id") or ""),
            str(raw.get("project_relative_path") or "").replace("\\", "/"),
            str(raw.get("sha256") or "").lower(),
            str(raw.get("size_bytes") or ""),
        )
        if asset_id in signatures and signatures[asset_id] != signature:
            raise EditorRetirementError(f"MediaAsset identity collision: {asset_id}")
        signatures[asset_id] = signature
        occurrence_record = {
            "source_file": occurrence["source_file"],
            "collection": occurrence["collection"],
            "source_index": occurrence["source_index"],
        }
        existing = assets_by_id.get(asset_id)
        if existing is None:
            validated = _validate_asset(raw, project_roots=project_roots)
            validated["occurrences"] = [occurrence_record]
            assets_by_id[asset_id] = validated
        else:
            existing["occurrences"].append(occurrence_record)

    assets = [assets_by_id[key] for key in sorted(assets_by_id)]
    artifact_signatures: dict[str, tuple[str, str, str]] = {}
    for artifact in artifacts:
        signature = (artifact["project_id"], artifact["document_id"], artifact["kind"])
        previous = artifact_signatures.get(artifact["id"])
        if previous is not None and previous != signature:
            raise EditorRetirementError(f"Artifact identity collision: {artifact['id']}")
        artifact_signatures[artifact["id"]] = signature
    verified = [row for row in assets if row["validation_status"] == "verified"]
    missing = [row for row in assets if row["validation_status"] == "missing"]
    unverifiable = [row for row in assets if row["validation_status"] == "unverifiable"]
    return {
        "documents": documents,
        "revisions": revisions,
        "assets": assets,
        "artifacts": artifacts,
        "other_json": other_json,
        "counts": {
            "json_files": len(files),
            "documents": len(documents),
            "revisions": len(revisions),
            "unique_assets": len(assets),
            "asset_occurrences": len(asset_occurrences),
            "verified_assets": len(verified),
            "missing_assets": len(missing),
            "unverifiable_assets": len(unverifiable),
            "artifact_occurrences": len(artifacts),
            "other_json": len(other_json),
        },
    }


def _import_plan(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for asset in inventory["assets"]:
        if asset["validation_status"] != "verified":
            continue
        provenance = _object_field(asset, "provenance", f"MediaAsset {asset['id']}")
        origin = str(provenance.get("origin") or "imported")
        source_kind = "generated" if origin == "generated" else "rendered" if origin == "derived" else "imported"
        plan.append(
            {
                "id": asset["id"],
                "project_id": asset["project_id"],
                "session_id": str(provenance.get("source_session_id") or ""),
                "document_id": asset["document_id"],
                "name": asset["name"],
                "media_kind": asset["media_kind"],
                "source_kind": source_kind,
                "project_relative_path": asset["project_relative_path"],
                "sha256": asset["actual_sha256"],
                "size_bytes": asset["actual_size_bytes"],
                "provider": str(provenance.get("provider") or ""),
                "model": str(provenance.get("model") or ""),
                "settings": _object_field(provenance, "settings", f"MediaAsset {asset['id']} provenance"),
                "job_id": str(provenance.get("job_id") or ""),
                "execution_id": str(provenance.get("source_execution_id") or ""),
                "tool_run_id": str(provenance.get("source_tool_run_id") or ""),
                "created_at": _float_field(asset, "created_at", f"MediaAsset {asset['id']}"),
                "source_precondition": {
                    "project_root": asset["project_root"],
                    "resolved_path": asset["resolved_path"],
                    "sha256": asset["actual_sha256"],
                    "size_bytes": asset["actual_size_bytes"],
                    "mtime_ns": asset["actual_mtime_ns"],
                },
            }
        )
    return plan


def _verify_source_snapshot(source: Path, files: list[dict[str, Any]]) -> None:
    try:
        current_files, _current_snapshots = _snapshot_json_tree(source)
    except Exception as exc:
        if isinstance(exc, EditorRetirementError):
            raise
        raise EditorRetirementError("Legacy JSON tree changed during export") from exc
    if current_files != files:
        raise EditorRetirementError("Legacy JSON tree changed during export")


def _verify_asset_preconditions(plan: list[dict[str, Any]]) -> None:
    for row in plan:
        precondition = row["source_precondition"]
        path = Path(precondition["resolved_path"])
        project_root = Path(precondition["project_root"])
        try:
            _reject_symlink(path, project_root, f"MediaAsset {row['id']}")
            stat_result = path.stat()
            data = path.read_bytes()
        except OSError as exc:
            raise EditorRetirementError(f"MediaAsset {row['id']} changed before import") from exc
        if (
            stat_result.st_mtime_ns != precondition["mtime_ns"]
            or len(data) != precondition["size_bytes"]
            or _sha256(data) != precondition["sha256"]
        ):
            raise EditorRetirementError(f"MediaAsset {row['id']} changed before import")


def _verify_existing_export(
    export_root: Path,
    *,
    source_path: Path,
    source_tree_sha256: str,
    expected_inventory: dict[str, Any],
    expected_import_plan: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest_path = export_root / MANIFEST_NAME
    if export_root.is_symlink() or manifest_path.is_symlink():
        raise EditorRetirementError("Existing retirement export cannot use symbolic links")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise EditorRetirementError(f"Existing retirement export is malformed: {manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise EditorRetirementError(f"Existing retirement manifest root is invalid: {manifest_path}")
    source_record = manifest.get("source")
    if not isinstance(source_record, dict) or not isinstance(source_record.get("files"), list):
        raise EditorRetirementError(f"Existing retirement manifest source inventory is invalid: {manifest_path}")
    if (
        manifest.get("migration") != MIGRATION_NAME
        or manifest.get("migration_version") != MIGRATION_VERSION
        or source_record.get("path") != str(source_path)
        or source_record.get("tree_sha256") != source_tree_sha256
    ):
        raise EditorRetirementError("Existing retirement export belongs to different source content")
    if manifest.get("inventory") != expected_inventory or manifest.get("media_import_plan") != expected_import_plan:
        raise EditorRetirementError(
            "Existing retirement export was created with different Project-path validation inputs"
        )
    archive = export_root / "archive"
    for row in source_record["files"]:
        if not isinstance(row, dict):
            raise EditorRetirementError(f"Existing retirement file inventory is invalid: {manifest_path}")
        relative_text = str(row.get("relative_path") or "")
        relative = Path(relative_text)
        if not relative_text or relative.is_absolute() or ".." in relative.parts:
            raise EditorRetirementError(f"Existing retirement file path is invalid: {relative_text!r}")
        path = archive.joinpath(*relative.parts)
        try:
            _reject_symlink(path, archive, "Archived JSON")
            data = path.read_bytes()
        except OSError as exc:
            raise EditorRetirementError(f"Archived JSON is missing: {path}") from exc
        if len(data) != row.get("size_bytes") or _sha256(data) != row.get("sha256"):
            raise EditorRetirementError(f"Archived JSON checksum is invalid: {path}")
    return manifest


def create_retirement_export(
    source: Path,
    destination: Path,
    *,
    project_roots: Optional[Mapping[str, Path]] = None,
) -> tuple[Path, dict[str, Any], bool]:
    """Create or validate a versioned, immutable retirement export.

    ``destination`` is a parent directory; the versioned export directory is
    created beneath it.  Every source JSON byte is archived unchanged.
    """

    try:
        source_root = Path(source).expanduser().resolve(strict=True)
    except OSError as exc:
        raise EditorRetirementError(f"Legacy source does not exist: {source}") from exc
    destination_root = Path(destination).expanduser().resolve(strict=False)
    if not source_root.is_dir():
        raise EditorRetirementError(f"Legacy source is not a directory: {source_root}")
    _assert_separate_roots(source_root, destination_root)
    normalized_roots = {str(key): Path(value) for key, value in (project_roots or {}).items()}
    files, snapshots = _snapshot_json_tree(source_root)
    source_tree_sha256 = _tree_digest(files)
    inventory = _build_inventory(files, snapshots, project_roots=normalized_roots)
    plan = _import_plan(inventory)
    export_root = destination_root / EXPORT_DIRECTORY
    if export_root.exists():
        return (
            export_root / MANIFEST_NAME,
            _verify_existing_export(
                export_root,
                source_path=source_root,
                source_tree_sha256=source_tree_sha256,
                expected_inventory=inventory,
                expected_import_plan=plan,
            ),
            True,
        )

    destination_root.mkdir(parents=True, exist_ok=True)
    stage = destination_root / f".{EXPORT_DIRECTORY}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    if stage.exists():
        raise EditorRetirementError(f"Retirement staging collision: {stage}")
    try:
        archive = stage / "archive"
        for row in files:
            relative = row["relative_path"]
            target = archive.joinpath(*Path(relative).parts)
            _write_bytes_fsync(target, snapshots[relative])
        manifest = {
            "schema_version": 1,
            "migration": MIGRATION_NAME,
            "migration_version": MIGRATION_VERSION,
            "migration_id": f"video-editor-retirement-v1-{source_tree_sha256[:20]}",
            "created_at": time.time(),
            "source": {
                "path": str(source_root),
                "tree_sha256": source_tree_sha256,
                "files": files,
            },
            "archive": {
                "relative_root": "archive",
                "format": "byte-for-byte-json",
                "read_only": True,
            },
            "inventory": inventory,
            "media_import_plan": plan,
            "recovery": {
                "source_mutated": False,
                "instructions": (
                    "Keep this directory with its manifest. Restore any legacy JSON by copying the "
                    "matching archive/<relative_path> file back only while EchoSpeak is stopped."
                ),
            },
        }
        _atomic_json(stage / MANIFEST_NAME, manifest)
        _verify_source_snapshot(source_root, files)
        if _build_inventory(files, snapshots, project_roots=normalized_roots) != inventory:
            raise EditorRetirementError("Legacy media paths changed during export")
        _verify_asset_preconditions(plan)
        for archived in archive.rglob("*.json"):
            archived.chmod(stat.S_IREAD)
        _promote_directory(stage, export_root)
        _fsync_directory(destination_root)
    except Exception:
        if stage.exists():
            try:
                _remove_generated_stage(stage)
            except OSError:
                pass
        raise
    return export_root / MANIFEST_NAME, manifest, False


def _register_media_plan(
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    media_library_store: MediaLibraryStore,
    media_library_root: Path,
) -> Path:
    declared_root = Path(media_library_root).expanduser().resolve(strict=False)
    actual_root = Path(media_library_store.root).expanduser().resolve(strict=False)
    if actual_root != declared_root:
        raise EditorRetirementError("Injected Media Library root does not match the declared registration root")
    plan = list(manifest.get("media_import_plan") or [])
    _verify_asset_preconditions(plan)
    receipt_path = manifest_path.parent / RECEIPT_NAME
    expected_ids = sorted(str(row["id"]) for row in plan)
    if receipt_path.exists():
        try:
            current = json.loads(receipt_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise EditorRetirementError(f"Existing media import receipt is malformed: {receipt_path}") from exc
        stable_current = (
            current.get("migration_id"),
            current.get("source_tree_sha256"),
            current.get("media_library_root"),
            current.get("imported_asset_ids"),
        )
        stable_expected = (
            manifest["migration_id"],
            manifest["source"]["tree_sha256"],
            str(actual_root),
            expected_ids,
        )
        if stable_current != stable_expected:
            raise EditorRetirementError("Existing media import receipt conflicts with this migration")

    # Complete preflight before the first write so known identity collisions do
    # not leave a partial import.  Runtime I/O failures remain safely resumable.
    for row in plan:
        existing = media_library_store.get(row["id"])
        if existing is None:
            continue
        stable_existing = (existing.project_id, existing.project_relative_path, existing.sha256)
        stable_incoming = (row["project_id"], row["project_relative_path"], row["sha256"])
        if stable_existing != stable_incoming:
            raise EditorRetirementError(f"Canonical MediaAsset identity collision: {row['id']}")

    imported_ids: list[str] = []
    for row in plan:
        payload = {key: value for key, value in row.items() if key != "source_precondition"}
        asset = MediaLibraryAsset(**payload, immutable=True, status="ready")
        registered = media_library_store.register(asset)
        imported_ids.append(registered.id)

    receipt = {
        "schema_version": 1,
        "migration": MIGRATION_NAME,
        "migration_id": manifest["migration_id"],
        "source_tree_sha256": manifest["source"]["tree_sha256"],
        "media_library_root": str(actual_root),
        "imported_asset_ids": sorted(imported_ids),
        "created_at": time.time(),
    }
    if receipt_path.exists():
        return receipt_path
    _atomic_json(receipt_path, receipt)
    return receipt_path


def retire_editor_data(
    source: Path,
    destination: Path,
    *,
    project_roots: Optional[Mapping[str, Path]] = None,
    media_library_store: Optional[MediaLibraryStore] = None,
    media_library_root: Optional[Path] = None,
) -> EditorRetirementResult:
    """Export legacy Editor JSON and optionally apply its verified media plan.

    Omitting ``media_library_store`` performs no Media Library writes.  Passing
    a store requires an explicit, matching root so global/singleton state can
    never be selected implicitly.
    """

    if (media_library_store is None) != (media_library_root is None):
        raise EditorRetirementError(
            "Media registration requires both an injected store and its explicit root"
        )
    manifest_path, manifest, reused = create_retirement_export(
        source,
        destination,
        project_roots=project_roots,
    )
    receipt_path: Optional[Path] = None
    if media_library_store is not None and media_library_root is not None:
        receipt_path = _register_media_plan(
            manifest_path,
            manifest,
            media_library_store=media_library_store,
            media_library_root=media_library_root,
        )
    return EditorRetirementResult(
        manifest_path=manifest_path,
        receipt_path=receipt_path,
        reused_export=reused,
        manifest=manifest,
    )


__all__ = [
    "EditorRetirementError",
    "EditorRetirementResult",
    "MIGRATION_NAME",
    "MIGRATION_VERSION",
    "create_retirement_export",
    "retire_editor_data",
]
