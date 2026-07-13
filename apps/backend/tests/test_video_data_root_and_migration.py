"""Video Editor data root must follow ECHOSPEAK_DATA_DIR; legacy migration is explicit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_resolve_video_data_dir_under_configured_root(tmp_path):
    from agent.video_editor.store import legacy_repo_video_data_dir, resolve_video_data_dir, reset_video_editor_store

    reset_video_editor_store()
    root = resolve_video_data_dir(data_dir=tmp_path / "dataA")
    assert root == (tmp_path / "dataA" / "video_editor").resolve()
    assert root != legacy_repo_video_data_dir()


def test_two_isolated_data_roots(tmp_path):
    from agent.video_editor.store import VideoEditorStore, reset_video_editor_store
    from agent.projects import ProjectManager

    reset_video_editor_store()
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    pm_a = ProjectManager(tmp_path / "prec_a")
    pm_b = ProjectManager(tmp_path / "prec_b")
    proj_a = pm_a.attach_folder(str(root_a), name="A", trust_state="trusted")
    proj_b = pm_b.attach_folder(str(root_b), name="B", trust_state="trusted")

    store_a = VideoEditorStore(tmp_path / "vstore_a", project_manager=pm_a)
    store_b = VideoEditorStore(tmp_path / "vstore_b", project_manager=pm_b)
    doc_a = store_a.create_document(proj_a.id, "DocA")
    doc_b = store_b.create_document(proj_b.id, "DocB")
    assert doc_a.id != doc_b.id
    # Cross-store isolation: B cannot open A's document path
    with pytest.raises(Exception):
        store_b.get_document(proj_a.id, doc_a.id)
    # A store writes only under its root
    paths_a = list((tmp_path / "vstore_a").rglob("*.json"))
    paths_b = list((tmp_path / "vstore_b").rglob("*.json"))
    assert paths_a and paths_b
    assert all(str(tmp_path / "vstore_a") in str(p) for p in paths_a)
    assert all(str(tmp_path / "vstore_b") in str(p) for p in paths_b)


def test_singleton_follows_resolve_and_reset(tmp_path, monkeypatch):
    from agent.video_editor import store as store_mod

    store_mod.reset_video_editor_store()
    roots = []

    def fake_resolve(data_dir=None):
        return (tmp_path / f"ve{len(roots)}").resolve() if roots else (tmp_path / "ve0").resolve()

    # First bind
    monkeypatch.setattr(store_mod, "resolve_video_data_dir", lambda data_dir=None: (tmp_path / "ve0").resolve())
    s1 = store_mod.get_video_editor_store()
    assert s1.root == (tmp_path / "ve0").resolve()
    # Same root reuses singleton
    s2 = store_mod.get_video_editor_store()
    assert s1 is s2
    # Root change rebinds
    monkeypatch.setattr(store_mod, "resolve_video_data_dir", lambda data_dir=None: (tmp_path / "ve1").resolve())
    s3 = store_mod.get_video_editor_store()
    assert s3 is not s1
    assert s3.root == (tmp_path / "ve1").resolve()
    store_mod.reset_video_editor_store()


def test_no_writes_to_repository_legacy_path(tmp_path, monkeypatch):
    from agent.video_editor.store import (
        VideoEditorStore,
        legacy_repo_video_data_dir,
        reset_video_editor_store,
    )
    from agent.projects import ProjectManager

    reset_video_editor_store()
    legacy = legacy_repo_video_data_dir()
    before = set()
    if legacy.exists():
        before = {p.relative_to(legacy) for p in legacy.rglob("*") if p.is_file()}

    pm = ProjectManager(tmp_path / "prec")
    root = tmp_path / "ws"
    root.mkdir()
    project = pm.attach_folder(str(root), name="Iso", trust_state="trusted")
    store = VideoEditorStore(tmp_path / "isolated_video", project_manager=pm)
    store.create_document(project.id, "OnlyHere")

    after = set()
    if legacy.exists():
        after = {p.relative_to(legacy) for p in legacy.rglob("*") if p.is_file()}
    # No new files under legacy from this store
    assert after == before or not (after - before)
    assert list((tmp_path / "isolated_video").rglob("*.json"))


def test_unwritable_root_fails_closed(tmp_path, monkeypatch):
    from agent.video_editor.store import VideoEditorStore, VideoStorePersistenceError

    # Use a path that cannot be created as a directory (file in the way)
    blocker = tmp_path / "blocked"
    blocker.write_text("not a dir", encoding="utf-8")
    with pytest.raises(VideoStorePersistenceError):
        VideoEditorStore(blocker / "nested" / "video")


def test_legacy_detection_and_migration(tmp_path):
    from agent.video_editor.migrate import detect_legacy_video_data, migrate_legacy_video_data

    src = tmp_path / "legacy_video"
    dest = tmp_path / "canonical_video"
    (src / "projects" / "p1" / "documents").mkdir(parents=True)
    doc = {
        "schema_version": 1,
        "id": "doc-1",
        "project_id": "p1",
        "name": "Legacy Cut",
        "revision": 2,
        "timeline": {"tracks": [{"id": "v1", "clips": [{"id": "c1", "volume": 0.5}]}]},
    }
    doc_path = src / "projects" / "p1" / "documents" / "doc-1.json"
    doc_path.write_text(json.dumps(doc), encoding="utf-8")

    det = detect_legacy_video_data(source=src, destination=dest)
    assert det["source_file_count"] == 1
    assert det["destination_file_count"] == 0

    dry = migrate_legacy_video_data(source=src, destination=dest, dry_run=True)
    assert dry.ok
    assert dry.files_scanned == 1
    assert not (dest / "projects").exists()  # dry-run must not write

    report = migrate_legacy_video_data(source=src, destination=dest, dry_run=False)
    assert report.ok
    assert report.files_copied == 1
    dest_doc = dest / "projects" / "p1" / "documents" / "doc-1.json"
    assert dest_doc.exists()
    loaded = json.loads(dest_doc.read_text(encoding="utf-8"))
    assert loaded["id"] == "doc-1"
    assert loaded["revision"] == 2
    assert loaded["timeline"]["tracks"][0]["clips"][0]["id"] == "c1"
    # Source untouched
    assert doc_path.exists()
    assert Path(report.audit_path).exists()


def test_migration_collision_fails_closed(tmp_path):
    from agent.video_editor.migrate import migrate_legacy_video_data

    src = tmp_path / "legacy"
    dest = tmp_path / "dest"
    (src / "x").mkdir(parents=True)
    (dest / "x").mkdir(parents=True)
    (src / "x" / "a.json").write_text('{"v":1}', encoding="utf-8")
    (dest / "x" / "a.json").write_text('{"v":2}', encoding="utf-8")
    report = migrate_legacy_video_data(source=src, destination=dest, dry_run=False)
    assert not report.ok
    assert report.collisions
    # Source and dest unchanged
    assert (src / "x" / "a.json").read_text(encoding="utf-8") == '{"v":1}'
    assert (dest / "x" / "a.json").read_text(encoding="utf-8") == '{"v":2}'


def test_get_store_writes_under_config_data_dir(tmp_path, monkeypatch):
    """get_video_editor_store must use resolve path under DATA_DIR, not repo path."""
    import config as config_mod
    from agent.video_editor import store as store_mod

    store_mod.reset_video_editor_store()
    data = tmp_path / "echodata"
    data.mkdir()
    monkeypatch.setattr(config_mod, "DATA_DIR", data)
    # resolve_video_data_dir reads config.DATA_DIR
    root = store_mod.resolve_video_data_dir()
    assert root == (data / "video_editor").resolve()
    store = store_mod.get_video_editor_store()
    assert store.root == root
    # Create a marker file via store internals
    (store.root / "marker.txt").write_text("ok", encoding="utf-8")
    assert (data / "video_editor" / "marker.txt").exists()
    assert not str(store.root).endswith(str(Path("apps") / "backend" / "data" / "video_editor"))
    store_mod.reset_video_editor_store()
