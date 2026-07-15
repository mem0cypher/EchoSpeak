from __future__ import annotations

from pathlib import Path

import pytest

from agent.media_library import MediaLibraryAsset, MediaLibraryError, MediaLibraryStore


def source_asset(
    *,
    asset_id: str = "asset-1",
    digest: str = "a" * 64,
    session_id: str = "session-1",
) -> MediaLibraryAsset:
    return MediaLibraryAsset(
        id=asset_id,
        project_id="project-1",
        session_id=session_id,
        document_id="document-1",
        name="source.mp4",
        media_kind="video",
        source_kind="imported",
        project_relative_path="media/source.mp4",
        sha256=digest,
        size_bytes=1024,
        provider="fixture-provider",
        model="fixture-model",
    )


def test_media_catalog_owns_stable_immutable_source_identity(tmp_path: Path):
    store = MediaLibraryStore(tmp_path / "media-library")
    registered = store.register(source_asset())

    assert registered.immutable is True
    assert registered.session_id == "session-1"
    assert registered.media_kind == "video"
    assert registered.provider == "fixture-provider"
    assert store.get("asset-1") == registered
    assert store.list(project_id="project-1") == [registered]

    # Exact replay is idempotent; an existing id cannot be rebound to new bytes.
    assert store.register(source_asset()) == registered
    with pytest.raises(MediaLibraryError, match="another source"):
        store.register(source_asset(digest="b" * 64))


def test_malformed_media_record_is_preserved_with_recovery_diagnostic(tmp_path: Path):
    store = MediaLibraryStore(tmp_path / "media-library")
    record = store.assets_root / "asset-1.json"
    record.write_bytes(b"{not-json")

    with pytest.raises(MediaLibraryError, match="quarantined"):
        store.get("asset-1")

    assert record.read_bytes() == b"{not-json"
    quarantined = list(store.corrupt_root.glob("*.json"))
    diagnostics = list(store.corrupt_root.glob("*.diagnostic.txt"))
    assert quarantined and quarantined[0].read_bytes() == b"{not-json"
    assert diagnostics
    assert "Recovery:" in diagnostics[0].read_text(encoding="utf-8")


def test_session_projection_does_not_leak_other_unbound_sessions(tmp_path: Path):
    store = MediaLibraryStore(tmp_path / "media-library")
    store.register(source_asset(asset_id="asset-1", session_id="session-1"))
    store.register(source_asset(asset_id="asset-2", session_id="session-2"))

    assert [asset.id for asset in store.list(session_id="session-1")] == ["asset-1"]
