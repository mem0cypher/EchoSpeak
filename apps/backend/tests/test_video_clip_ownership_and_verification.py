"""Canonical clip ownership under timeline.tracks[].clips + mutation verification."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def video_env(tmp_path, monkeypatch):
    from agent.projects import ProjectManager
    from agent.state import StateStore
    from agent.video_editor.models import (
        EditOperation,
        MediaAsset,
        MediaKind,
        RationalTime,
    )
    from agent.video_editor.store import VideoEditorStore
    from config import config
    import agent.projects as projects_mod
    import agent.state as state_mod
    import agent.video_editor.store as store_mod

    root = tmp_path / "vproj"
    root.mkdir()
    manager = ProjectManager(tmp_path / "prec")
    project = manager.attach_folder(str(root), name="Vid", trust_state="trusted")
    store = VideoEditorStore(tmp_path / "vstore", project_manager=manager)
    runtime = StateStore(tmp_path / "vrt")
    monkeypatch.setattr(projects_mod, "_project_manager", manager)
    monkeypatch.setattr(state_mod, "_state_store", runtime)
    monkeypatch.setattr(store_mod, "_STORE", store)
    monkeypatch.setattr(store_mod, "_STORE_ROOT", Path(store.root).resolve())
    monkeypatch.setattr(store_mod, "resolve_video_data_dir", lambda data_dir=None: Path(store.root).resolve())
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_video_agent_edits", True)
    runtime.update_thread_state(
        "vsess",
        active_project_id=project.id,
        project_path=str(root),
        workspace_root=str(root),
        permissions={"system_actions": True, "video_agent_edits": True},
        allowed_tool_names=["video_propose_operations", "video_apply_transaction"],
    )
    doc = store.create_document(project.id, "Cut")
    asset = MediaAsset(
        project_id=project.id,
        document_id=doc.id,
        name="cam.mp4",
        kind=MediaKind.VIDEO,
        project_relative_path="cam.mp4",
        sha256="b" * 64,
        size_bytes=10,
        mtime_ns=1,
        duration=RationalTime(ticks="10000"),
    )
    doc = store.add_asset(project.id, doc.id, asset)
    return {
        "store": store,
        "runtime": runtime,
        "project": project,
        "doc": doc,
        "asset": asset,
        "EditOperation": EditOperation,
    }


def test_canonical_clips_live_under_tracks(video_env):
    from agent.video_editor.clips import clip_count, document_api_dict, list_clips

    store = video_env["store"]
    project = video_env["project"]
    doc = video_env["doc"]
    asset = video_env["asset"]
    EO = video_env["EditOperation"]

    assert clip_count(doc) == 0
    assert list_clips(doc) == []
    # Top-level document has no durable clips field
    raw = doc.model_dump(mode="json")
    assert "clips" not in raw or raw.get("clips") in (None, [])

    tx, _ = store.prepare_transaction(
        project.id,
        doc.id,
        "vsess",
        [
            EO(
                operation_type="add_track",
                expected_revision=doc.revision,
                payload={"track_id": "v1", "kind": "video", "name": "V1"},
            ),
        ],
        source="manual",
    )
    doc = store.apply_transaction(tx)
    assert doc.revision == 1

    tx2, _ = store.prepare_transaction(
        project.id,
        doc.id,
        "vsess",
        [
            EO(
                operation_type="insert_clip",
                expected_revision=doc.revision,
                payload={
                    "track_id": "v1",
                    "clip_id": "c1",
                    "asset_id": asset.id,
                    "timeline_start": {"ticks": "0", "time_base": {"numerator": 1, "denominator": 1000}},
                    "duration": {"ticks": "6000", "time_base": {"numerator": 1, "denominator": 1000}},
                },
            ),
        ],
        source="manual",
    )
    doc = store.apply_transaction(tx2)
    assert doc.revision == 2
    assert clip_count(doc) == 1
    assert list_clips(doc)[0].id == "c1"
    assert any(c.id == "c1" for t in doc.timeline.tracks for c in t.clips)

    # API projection exposes flat clips derived from tracks
    view = document_api_dict(doc)
    assert view["clip_count"] == 1
    assert view["clips"][0]["id"] == "c1"
    assert view["clips"][0]["track_id"] == "v1"

    # Reload from store
    reloaded = store.get_document(project.id, doc.id)
    assert clip_count(reloaded) == 1
    assert reloaded.revision == 2


def test_insert_missing_track_fails(video_env):
    from agent.video_editor.store import VideoStoreError

    store = video_env["store"]
    project = video_env["project"]
    doc = video_env["doc"]
    asset = video_env["asset"]
    EO = video_env["EditOperation"]

    with pytest.raises((VideoStoreError, Exception)):
        tx, _ = store.prepare_transaction(
            project.id,
            doc.id,
            "vsess",
            [
                EO(
                    operation_type="insert_clip",
                    expected_revision=doc.revision,
                    payload={
                        "track_id": "missing",
                        "clip_id": "c1",
                        "asset_id": asset.id,
                        "timeline_start": {"ticks": "0", "time_base": {"numerator": 1, "denominator": 1000}},
                        "duration": {"ticks": "1000", "time_base": {"numerator": 1, "denominator": 1000}},
                    },
                ),
            ],
            source="manual",
        )
        store.apply_transaction(tx)


def test_volume_requires_existing_clip_and_advances_revision(video_env):
    from agent.video_editor.clips import clip_volume, find_clip
    from agent.video_editor.store import VideoStoreError

    store = video_env["store"]
    project = video_env["project"]
    doc = video_env["doc"]
    asset = video_env["asset"]
    EO = video_env["EditOperation"]

    tx, _ = store.prepare_transaction(
        project.id,
        doc.id,
        "vsess",
        [
            EO(operation_type="add_track", expected_revision=0, payload={"track_id": "v1", "kind": "video", "name": "V1"}),
            EO(
                operation_type="insert_clip",
                expected_revision=0,
                payload={
                    "track_id": "v1",
                    "clip_id": "c1",
                    "asset_id": asset.id,
                    "timeline_start": {"ticks": "0", "time_base": {"numerator": 1, "denominator": 1000}},
                    "duration": {"ticks": "6000", "time_base": {"numerator": 1, "denominator": 1000}},
                },
            ),
        ],
        source="manual",
    )
    # stage both with expected_revision 0 - prepare checks all match current
    # Actually prepare requires all ops expected_revision == document.revision
    # Multi-op with same revision works in stage_transaction
    doc = store.apply_transaction(tx)
    rev = doc.revision
    assert clip_volume(doc, "c1") == 1.0

    # Missing clip must fail at apply prep
    with pytest.raises(VideoStoreError, match="missing|not found|verification"):
        tx_bad, _ = store.prepare_transaction(
            project.id,
            doc.id,
            "vsess",
            [
                EO(
                    operation_type="set_clip_volume",
                    expected_revision=rev,
                    payload={"clip_id": "nope", "volume": 0.5},
                )
            ],
            source="manual",
        )
        store.apply_transaction(tx_bad)

    tx2, _ = store.prepare_transaction(
        project.id,
        doc.id,
        "vsess",
        [
            EO(
                operation_type="set_clip_volume",
                expected_revision=rev,
                payload={"clip_id": "c1", "volume": 0.5},
            )
        ],
        source="manual",
    )
    doc2 = store.apply_transaction(tx2)
    assert doc2.revision == rev + 1
    assert abs(clip_volume(doc2, "c1") - 0.5) < 1e-6
    # Idempotent re-apply refuses false success
    with pytest.raises(VideoStoreError, match="already applied"):
        store.apply_transaction(tx2, allow_idempotent=False)


def test_double_apply_not_false_success(video_env):
    from agent.video_editor.store import VideoStoreError

    store = video_env["store"]
    project = video_env["project"]
    doc = video_env["doc"]
    EO = video_env["EditOperation"]

    tx, _ = store.prepare_transaction(
        project.id,
        doc.id,
        "vsess",
        [EO(operation_type="add_track", expected_revision=0, payload={"track_id": "v1", "kind": "video", "name": "V1"})],
        source="manual",
    )
    doc = store.apply_transaction(tx)
    with pytest.raises(VideoStoreError, match="already applied"):
        store.apply_transaction(tx)


def _seed_clip(video_env):
    store = video_env["store"]
    project = video_env["project"]
    doc = video_env["doc"]
    asset = video_env["asset"]
    EO = video_env["EditOperation"]
    tx, _ = store.prepare_transaction(
        project.id,
        doc.id,
        "vsess",
        [
            EO(operation_type="add_track", expected_revision=0, payload={"track_id": "v1", "kind": "video", "name": "V1"}),
            EO(
                operation_type="insert_clip",
                expected_revision=0,
                payload={
                    "track_id": "v1",
                    "clip_id": "c1",
                    "asset_id": asset.id,
                    "timeline_start": {"ticks": "0", "time_base": {"numerator": 1, "denominator": 1000}},
                    "duration": {"ticks": "6000", "time_base": {"numerator": 1, "denominator": 1000}},
                },
            ),
        ],
        source="manual",
    )
    return store.apply_transaction(tx)


def test_split_and_delete_verification(video_env):
    from agent.video_editor.clips import clip_count, clip_exists

    store = video_env["store"]
    project = video_env["project"]
    EO = video_env["EditOperation"]
    doc = _seed_clip(video_env)
    rev = doc.revision
    assert clip_count(doc) == 1

    tx_split, _ = store.prepare_transaction(
        project.id,
        doc.id,
        "vsess",
        [
            EO(
                operation_type="split_clip",
                expected_revision=rev,
                payload={
                    "clip_id": "c1",
                    "right_clip_id": "c1-r",
                    "at": {"ticks": "3000", "time_base": {"numerator": 1, "denominator": 1000}},
                },
            )
        ],
        source="manual",
    )
    doc = store.apply_transaction(tx_split)
    assert doc.revision == rev + 1
    assert clip_exists(doc, "c1")
    assert clip_exists(doc, "c1-r")
    assert clip_count(doc) == 2
    rev = doc.revision

    tx_del, _ = store.prepare_transaction(
        project.id,
        doc.id,
        "vsess",
        [EO(operation_type="delete_clip", expected_revision=rev, payload={"clip_id": "c1-r"})],
        source="manual",
    )
    doc = store.apply_transaction(tx_del)
    assert doc.revision == rev + 1
    assert clip_exists(doc, "c1")
    assert not clip_exists(doc, "c1-r")
    reloaded = store.get_document(project.id, doc.id)
    assert reloaded.revision == doc.revision
    assert not clip_exists(reloaded, "c1-r")


def test_mute_is_volume_zero_with_revision_advance(video_env):
    from agent.video_editor.clips import clip_volume

    store = video_env["store"]
    project = video_env["project"]
    EO = video_env["EditOperation"]
    doc = _seed_clip(video_env)
    rev = doc.revision
    tx, _ = store.prepare_transaction(
        project.id,
        doc.id,
        "vsess",
        [EO(operation_type="set_clip_volume", expected_revision=rev, payload={"clip_id": "c1", "volume": 0.0})],
        source="manual",
    )
    doc = store.apply_transaction(tx)
    assert doc.revision == rev + 1
    assert abs(clip_volume(doc, "c1")) < 1e-9
