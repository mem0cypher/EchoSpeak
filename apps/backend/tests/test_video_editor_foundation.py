from __future__ import annotations

import json
import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.projects import ProjectManager
from agent.video_editor.models import EditOperation, MediaAsset, MediaKind, RationalTime, VideoJob
from agent.video_editor.store import VideoEditorStore, VideoStoreCorruption, VideoStoreError


@pytest.fixture()
def video_store(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    manager = ProjectManager(tmp_path / "project-records")
    project = manager.attach_folder(str(project_root), name="Fixture Video", trust_state="trusted")
    store = VideoEditorStore(tmp_path / "video-state", project_manager=manager)
    return store, project, project_root


def op(operation_type: str, revision: int, **payload):
    return EditOperation(operation_type=operation_type, expected_revision=revision, payload=payload)


def test_manual_operations_revisions_and_exactly_once(video_store):
    store, project, _root = video_store
    document = store.create_document(project.id, "Demo")
    asset = MediaAsset(
        project_id=project.id,
        document_id=document.id,
        name="source.mp4",
        kind=MediaKind.VIDEO,
        project_relative_path="source.mp4",
        sha256="a" * 64,
        size_bytes=100,
        mtime_ns=1,
        duration=RationalTime(ticks="10000"),
    )
    document = store.add_asset(project.id, document.id, asset)
    first, _ = store.prepare_transaction(
        project.id,
        document.id,
        "session-1",
        [
            op("add_track", 0, track_id="v1", kind="video", name="Video 1"),
            op(
                "insert_clip",
                0,
                track_id="v1",
                clip_id="clip-1",
                asset_id=asset.id,
                timeline_start={"ticks": "0", "time_base": {"numerator": 1, "denominator": 1000}},
                duration={"ticks": "6000", "time_base": {"numerator": 1, "denominator": 1000}},
            ),
        ],
        source="manual",
    )
    document = store.apply_transaction(first)
    assert document.revision == 1
    assert document.timeline.tracks[0].clips[0].id == "clip-1"
    # Exact retry is idempotent and cannot create a second revision.
    assert store.apply_transaction(first).revision == 1

    second, _ = store.prepare_transaction(
        project.id,
        document.id,
        "session-1",
        [
            op("add_track", 1, track_id="v2", kind="video", name="Video 2"),
            op("split_clip", 1, clip_id="clip-1", right_clip_id="clip-2", at={"ticks": "3000"}),
        ],
        source="manual",
    )
    document = store.apply_transaction(second)
    assert document.revision == 2
    assert [clip.id for clip in document.timeline.tracks[0].clips] == ["clip-1", "clip-2"]

    third, _ = store.prepare_transaction(
        project.id,
        document.id,
        "session-1",
        [
            op("trim_clip", 2, clip_id="clip-2", duration={"ticks": "2000"}),
            op("move_clip", 2, clip_id="clip-2", track_id="v2", timeline_start={"ticks": "7000"}),
            op("delete_clip", 2, clip_id="clip-1"),
        ],
        source="manual",
    )
    document = store.apply_transaction(third)
    assert document.revision == 3
    assert document.timeline.tracks[0].clips == []
    assert document.timeline.tracks[1].clips[0].id == "clip-2"
    assert document.timeline.tracks[1].clips[0].duration.ticks == "2000"


def test_stale_transaction_fails_without_changing_head(video_store):
    store, project, _root = video_store
    document = store.create_document(project.id, "Demo")
    transaction, _ = store.prepare_transaction(
        project.id,
        document.id,
        "session-1",
        [op("add_track", 0, track_id="v1", kind="video")],
        source="manual",
    )
    document = store.apply_transaction(transaction)
    stale = transaction.model_copy(update={"id": "stale-transaction", "status": "prepared"})
    with pytest.raises(VideoStoreError, match="changed|expected"):
        store.apply_transaction(stale)
    assert store.get_document(project.id, document.id).revision == 1


def test_undo_redo_create_new_revisions(video_store):
    store, project, _root = video_store
    document = store.create_document(project.id, "Demo")
    transaction, _ = store.prepare_transaction(
        project.id,
        document.id,
        "session-1",
        [op("add_track", 0, track_id="v1", kind="video")],
        source="manual",
    )
    document = store.apply_transaction(transaction)
    undone = store.undo(project.id, document.id)
    assert undone.revision == 2
    assert undone.timeline.tracks == []
    redone = store.redo(project.id, document.id)
    assert redone.revision == 3
    assert redone.timeline.tracks[0].id == "v1"


def test_malformed_authoritative_json_fails_closed_with_recovery(video_store):
    store, project, _root = video_store
    document = store.create_document(project.id, "Demo")
    document_path = store._document_path(project.id, document.id)
    original = document_path.read_bytes()
    document_path.write_bytes(b"{not-json")
    with pytest.raises(VideoStoreCorruption, match="recovery copy"):
        store.get_document(project.id, document.id)
    assert document_path.read_bytes() == b"{not-json"
    quarantines = list(store.corrupt_root.iterdir())
    assert quarantines
    assert (quarantines[0] / "RECOVERY.txt").exists()
    assert any(path.read_bytes() == b"{not-json" for path in quarantines[0].glob("*.json"))
    # Test cleanup may restore the disposable fixture, but the store never did.
    document_path.write_bytes(original)


def test_schema_invalid_json_and_external_ids_fail_closed(video_store):
    store, project, _root = video_store
    document = store.create_document(project.id, "Demo")
    document_path = store._document_path(project.id, document.id)
    document_path.write_text(json.dumps({"schema_version": 1, "id": document.id}), encoding="utf-8")
    with pytest.raises(VideoStoreCorruption, match="Invalid authoritative video schema"):
        store.get_document(project.id, document.id)
    assert any((folder / "RECOVERY.txt").exists() for folder in store.corrupt_root.iterdir())
    with pytest.raises(VideoStoreError, match="invalid video document id"):
        store.get_document(project.id, "..\\escaped")


def test_snapshot_integrity_and_import_then_undo_preserves_assets(video_store):
    store, project, _root = video_store
    document = store.create_document(project.id, "Current Name")
    transaction, _ = store.prepare_transaction(
        project.id,
        document.id,
        "session-1",
        [op("add_track", 0, track_id="v1", kind="video")],
        source="manual",
    )
    document = store.apply_transaction(transaction)
    asset = MediaAsset(
        project_id=project.id,
        document_id=document.id,
        name="later.mp4",
        kind=MediaKind.VIDEO,
        project_relative_path="later.mp4",
        sha256="b" * 64,
        size_bytes=5,
        mtime_ns=2,
        duration=RationalTime(ticks="1000"),
    )
    store.add_asset(project.id, document.id, asset)
    undone = store.undo(project.id, document.id)
    assert undone.timeline.tracks == []
    assert [item.id for item in undone.assets] == [asset.id]
    assert undone.name == "Current Name"

    # A schema-valid, digest-adjusted ownership tamper is still quarantined.
    target_revision_id = undone.redo_revision_ids[-1]
    snapshot_path = store._revision_path(project.id, document.id, target_revision_id)
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["document"]["project_id"] = "different-project"
    payload["revision"]["snapshot_sha256"] = hashlib.sha256(
        store._canonical(payload["document"])
    ).hexdigest()
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(VideoStoreCorruption, match="Invalid authoritative revision snapshot"):
        store.redo(project.id, document.id)


def test_operation_kind_and_split_id_guards(video_store):
    store, project, _root = video_store
    document = store.create_document(project.id, "Demo")
    audio = MediaAsset(
        project_id=project.id,
        document_id=document.id,
        name="audio.wav",
        kind=MediaKind.AUDIO,
        project_relative_path="audio.wav",
        sha256="c" * 64,
        size_bytes=10,
        mtime_ns=1,
        duration=RationalTime(ticks="5000"),
    )
    document = store.add_asset(project.id, document.id, audio)
    with pytest.raises(ValueError, match="incompatible"):
        store.prepare_transaction(
            project.id,
            document.id,
            "session-1",
            [
                op("add_track", 0, track_id="v1", kind="video"),
                op(
                    "insert_clip",
                    0,
                    track_id="v1",
                    asset_id=audio.id,
                    timeline_start={"ticks": "0"},
                    duration={"ticks": "1000"},
                ),
            ],
            source="manual",
        )


def test_job_idempotency_returns_authoritative_record_and_rejects_alias(video_store):
    store, project, _root = video_store
    document = store.create_document(project.id, "Demo")
    job = VideoJob(
        project_id=project.id,
        document_id=document.id,
        session_id="session-1",
        kind="analysis",
        idempotency_key="stable-key",
        parameters={"quality": "draft"},
    )
    document, persisted = store.create_job(project.id, document.id, job)
    _document, repeated = store.create_job(project.id, document.id, job.model_copy(update={"id": "other"}))
    assert repeated.id == persisted.id
    with pytest.raises(VideoStoreError, match="different inputs"):
        store.create_job(
            project.id,
            document.id,
            job.model_copy(update={"id": "third", "parameters": {"quality": "final"}}),
        )


def test_media_probe_preserves_exact_time_base(video_store, monkeypatch):
    from agent.video_editor import media as media_mod

    store, project, root = video_store
    document = store.create_document(project.id, "Demo")
    source = root / "source.mp4"
    source.write_bytes(b"fixture-media")
    probe = {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "time_base": "1/24000",
                "duration_ts": "48000",
                "avg_frame_rate": "24000/1001",
                "r_frame_rate": "24000/1001",
                "width": 1920,
                "height": 1080,
            }
        ],
        "format": {"format_name": "mov,mp4"},
        "chapters": [],
    }
    monkeypatch.setattr(
        media_mod.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=json.dumps(probe).encode(), stderr=b""),
    )
    asset = media_mod.build_asset_from_probe(
        project.id,
        document.id,
        "source.mp4",
        project_manager=store.project_manager,
        session_id="session-1",
    )
    assert asset.duration.ticks == "48000"
    assert asset.duration.time_base.denominator == 24000
    assert asset.streams[0].average_frame_rate.numerator == 24000
    assert asset.sha256
    source.write_bytes(b"replacement-media")
    with pytest.raises(media_mod.MediaProbeError, match="changed after import"):
        media_mod.validate_asset_source(root, asset)


def test_video_approval_revalidates_authority_and_terminalizes_session(tmp_path, monkeypatch):
    import agent.projects as projects_mod
    import agent.state as state_mod
    import agent.video_editor.store as video_store_mod
    from agent.state import StateStore
    from api.video_editor import ProposalRequest, consume_video_approval, propose_transaction
    from config import config

    project_root = tmp_path / "project"
    project_root.mkdir()
    manager = ProjectManager(tmp_path / "project-records")
    project = manager.attach_folder(str(project_root), name="Fixture Video", trust_state="trusted")
    runtime = StateStore(tmp_path / "runtime")
    store = VideoEditorStore(tmp_path / "video-state", project_manager=manager)
    monkeypatch.setattr(projects_mod, "_project_manager", manager)
    monkeypatch.setattr(state_mod, "_state_store", runtime)
    monkeypatch.setattr(video_store_mod, "_STORE", store)
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_video_agent_edits", True)
    runtime.update_thread_state(
        "video-session",
        active_project_id=project.id,
        project_path=str(project_root),
        workspace_root=str(project_root),
    )
    document = store.create_document(project.id, "Demo")
    request = ProposalRequest(
        session_id="video-session",
        project_id=project.id,
        objective="Add a video track",
        operations=[op("add_track", 0, track_id="v1", kind="video")],
    )
    proposed = asyncio.run(propose_transaction(document.id, request))
    approval = runtime.get_approval(proposed["approval"]["id"])
    assert approval is not None
    state = runtime.get_thread_state("video-session")
    runtime.update_thread_state(
        "video-session",
        permissions={**state.permissions, "video_agent_edits": False},
    )
    with pytest.raises(VideoStoreError, match="permissions"):
        consume_video_approval(approval)
    assert store.get_document(project.id, document.id).revision == 0

    runtime.update_thread_state(
        "video-session",
        permissions={"system_actions": True, "video_agent_edits": True},
        allowed_tool_names=["video_apply_transaction"],
    )
    result = consume_video_approval(approval)
    assert result["success"] is True
    assert result["document"]["revision"] == 1
    assert runtime.get_approval(approval.id).status == "approved"
    assert runtime.get_thread_state("video-session").execution_status == "complete"
    runs = runtime.list_tool_runs(result["execution_id"])
    assert any(run.tool_name == "video_apply_transaction" and run.status == "complete" for run in runs)
