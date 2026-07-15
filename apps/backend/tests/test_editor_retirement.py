"""Disposable tests for the non-destructive Editor retirement export."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import Any

import pytest

from agent.editor_retirement import (
    EXPORT_DIRECTORY,
    EditorRetirementError,
    retire_editor_data,
)
from agent.media_library import MediaLibraryStore


def _canonical(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _asset(
    *,
    asset_id: str,
    document_id: str,
    relative_path: str,
    digest: str,
    size: int,
    mtime_ns: int = 0,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": asset_id,
        "project_id": "project-1",
        "document_id": document_id,
        "name": Path(relative_path).name,
        "kind": "video",
        "project_relative_path": relative_path,
        "immutable": True,
        "sha256": digest,
        "size_bytes": size,
        "mtime_ns": mtime_ns,
        "provenance": {
            "origin": "imported",
            "source_session_id": "session-1",
            "provider": "fixture",
        },
        "created_at": 100.0,
    }


def _fixture(tmp_path: Path) -> dict[str, Any]:
    source = tmp_path / "legacy-video-editor"
    destination = tmp_path / "retirement"
    project_root = tmp_path / "project"
    media = project_root / "media"
    media.mkdir(parents=True)

    verified_path = media / "verified.bin"
    verified_bytes = b"verified-media-content"
    verified_path.write_bytes(verified_bytes)
    verified_stat = verified_path.stat()
    unverified_path = media / "unverified.bin"
    unverified_path.write_bytes(b"unverified")

    verified = _asset(
        asset_id="asset-verified",
        document_id="doc-1",
        relative_path="media/verified.bin",
        digest=hashlib.sha256(verified_bytes).hexdigest(),
        size=len(verified_bytes),
        mtime_ns=verified_stat.st_mtime_ns,
    )
    missing = _asset(
        asset_id="asset-missing",
        document_id="doc-1",
        relative_path="media/missing.bin",
        digest="a" * 64,
        size=123,
    )
    unverifiable = _asset(
        asset_id="asset-unverifiable",
        document_id="doc-1",
        relative_path="media/unverified.bin",
        digest="",
        size=len(b"unverified"),
        mtime_ns=unverified_path.stat().st_mtime_ns,
    )
    artifact = {
        "schema_version": 1,
        "id": "artifact-1",
        "kind": "transcript",
        "project_id": "project-1",
        "document_id": "doc-1",
        "payload": {"text": "preserve me"},
    }
    document = {
        "schema_version": 1,
        "id": "doc-1",
        "project_id": "project-1",
        "name": "Legacy Cut",
        "revision": 1,
        "assets": [verified, missing, unverifiable],
        "generated_assets": [],
        "artifacts": [artifact],
        "timeline": {"tracks": []},
        "revisions": [],
    }
    revision = {
        "schema_version": 1,
        "id": "revision-1",
        "project_id": "project-1",
        "document_id": "doc-1",
        "revision_number": 1,
        "snapshot_sha256": hashlib.sha256(_canonical(document)).hexdigest(),
    }
    document_path = source / "projects" / "project-1" / "documents" / "doc-1.json"
    revision_path = source / "projects" / "project-1" / "revisions" / "doc-1" / "revision-1.json"
    metadata_path = source / "migration-history.json"
    _write_json(document_path, document)
    _write_json(revision_path, {"revision": revision, "document": document})
    _write_json(metadata_path, {"schema_version": 1, "note": "also archived"})
    return {
        "source": source,
        "destination": destination,
        "project_root": project_root,
        "document": document,
        "document_path": document_path,
        "revision_path": revision_path,
        "metadata_path": metadata_path,
    }


def _source_bytes(source: Path) -> dict[str, bytes]:
    return {path.relative_to(source).as_posix(): path.read_bytes() for path in sorted(source.rglob("*.json"))}


def _rewrite_fixture_document(fixture: dict[str, Any], document: dict[str, Any]) -> None:
    _write_json(fixture["document_path"], document)
    revision_wrapper = json.loads(fixture["revision_path"].read_text(encoding="utf-8"))
    revision_wrapper["document"] = document
    revision_wrapper["revision"]["snapshot_sha256"] = hashlib.sha256(_canonical(document)).hexdigest()
    _write_json(fixture["revision_path"], revision_wrapper)


def test_retirement_export_and_disposable_media_import_are_idempotent(tmp_path):
    fixture = _fixture(tmp_path)
    before = _source_bytes(fixture["source"])
    media_root = tmp_path / "disposable-media-library"
    media_store = MediaLibraryStore(media_root)

    result = retire_editor_data(
        fixture["source"],
        fixture["destination"],
        project_roots={"project-1": fixture["project_root"]},
        media_library_store=media_store,
        media_library_root=media_root,
    )

    assert not result.reused_export
    assert result.receipt_path is not None and result.receipt_path.exists()
    assert result.manifest["migration_version"] == 1
    counts = result.manifest["inventory"]["counts"]
    assert counts == {
        "json_files": 3,
        "documents": 1,
        "revisions": 1,
        "unique_assets": 3,
        "asset_occurrences": 6,
        "verified_assets": 1,
        "missing_assets": 1,
        "unverifiable_assets": 1,
        "artifact_occurrences": 2,
        "other_json": 1,
    }
    statuses = {
        row["id"]: row["validation_status"] for row in result.manifest["inventory"]["assets"]
    }
    assert statuses == {
        "asset-missing": "missing",
        "asset-unverifiable": "unverifiable",
        "asset-verified": "verified",
    }
    assert [row["id"] for row in result.manifest["media_import_plan"]] == ["asset-verified"]
    assert [row.id for row in media_store.list(project_id="project-1")] == ["asset-verified"]

    for relative, data in before.items():
        archived = result.manifest_path.parent / "archive" / Path(relative)
        assert archived.read_bytes() == data
    assert _source_bytes(fixture["source"]) == before

    receipt_before = result.receipt_path.read_bytes()
    rerun = retire_editor_data(
        fixture["source"],
        fixture["destination"],
        project_roots={"project-1": fixture["project_root"]},
        media_library_store=media_store,
        media_library_root=media_root,
    )
    assert rerun.reused_export
    assert rerun.receipt_path is not None and rerun.receipt_path.read_bytes() == receipt_before
    assert len(list((media_root / "assets").glob("*.json"))) == 1
    assert _source_bytes(fixture["source"]) == before


@pytest.mark.parametrize("failure", ["checksum", "out_of_scope", "asset_collision"])
def test_changed_hash_out_of_scope_and_id_collision_fail_without_export(tmp_path, failure):
    fixture = _fixture(tmp_path)
    document = json.loads(json.dumps(fixture["document"]))
    if failure == "checksum":
        document["assets"][0]["sha256"] = "f" * 64
    elif failure == "out_of_scope":
        document["assets"][0]["project_relative_path"] = "../outside.bin"
    else:
        revision_wrapper = json.loads(fixture["revision_path"].read_text(encoding="utf-8"))
        collision = revision_wrapper["document"]
        collision["assets"][0]["project_relative_path"] = "media/other.bin"
        revision_wrapper["revision"]["snapshot_sha256"] = hashlib.sha256(_canonical(collision)).hexdigest()
        _write_json(fixture["revision_path"], revision_wrapper)
    if failure != "asset_collision":
        _rewrite_fixture_document(fixture, document)
    before = _source_bytes(fixture["source"])

    with pytest.raises(EditorRetirementError):
        retire_editor_data(
            fixture["source"],
            fixture["destination"],
            project_roots={"project-1": fixture["project_root"]},
        )

    assert not (fixture["destination"] / EXPORT_DIRECTORY).exists()
    assert _source_bytes(fixture["source"]) == before


def test_malformed_authoritative_json_fails_without_writes(tmp_path):
    source = tmp_path / "legacy"
    malformed = source / "projects" / "project-1" / "documents" / "doc-1.json"
    malformed.parent.mkdir(parents=True)
    malformed.write_text("{not-json", encoding="utf-8")
    destination = tmp_path / "export"

    with pytest.raises(EditorRetirementError, match="Malformed legacy JSON"):
        retire_editor_data(source, destination)

    assert malformed.read_text(encoding="utf-8") == "{not-json"
    assert not (destination / EXPORT_DIRECTORY).exists()


def test_corrupted_existing_archive_fails_closed(tmp_path):
    fixture = _fixture(tmp_path)
    result = retire_editor_data(
        fixture["source"],
        fixture["destination"],
        project_roots={"project-1": fixture["project_root"]},
    )
    archived = result.manifest_path.parent / "archive" / "migration-history.json"
    archived.chmod(stat.S_IREAD | stat.S_IWRITE)
    archived.write_text('{"changed":true}\n', encoding="utf-8")

    with pytest.raises(EditorRetirementError, match="Archived JSON checksum"):
        retire_editor_data(
            fixture["source"],
            fixture["destination"],
            project_roots={"project-1": fixture["project_root"]},
        )


def test_media_registration_requires_exact_injected_root(tmp_path):
    fixture = _fixture(tmp_path)
    actual_root = tmp_path / "actual-disposable-library"
    store = MediaLibraryStore(actual_root)

    with pytest.raises(EditorRetirementError, match="does not match"):
        retire_editor_data(
            fixture["source"],
            fixture["destination"],
            project_roots={"project-1": fixture["project_root"]},
            media_library_store=store,
            media_library_root=tmp_path / "different-library",
        )

    assert store.list() == []

