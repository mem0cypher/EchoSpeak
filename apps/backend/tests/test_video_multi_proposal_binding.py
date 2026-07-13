"""Second video proposal must fail closed when a pending approval already exists."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_second_video_proposal_rejected_while_pending(tmp_path, monkeypatch):
    from agent.projects import ProjectManager
    from agent.state import StateStore
    from agent.video_editor.models import EditOperation, MediaAsset, MediaKind, RationalTime
    from agent.video_editor.store import VideoEditorStore
    from api.video_editor import ProposalRequest, propose_video_transaction_sync
    from agent.video_editor.store import VideoStoreError
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
    tx0, _ = store.prepare_transaction(
        project.id,
        doc.id,
        "vsess",
        [
            EditOperation(
                operation_type="add_track",
                expected_revision=0,
                payload={"track_id": "v1", "kind": "video", "name": "V1"},
            ),
            EditOperation(
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
    doc = store.apply_transaction(tx0)
    rev = doc.revision

    first = propose_video_transaction_sync(
        doc.id,
        ProposalRequest(
            session_id="vsess",
            project_id=project.id,
            objective="Mute clip",
            operations=[
                EditOperation(
                    operation_type="set_clip_volume",
                    expected_revision=rev,
                    payload={"clip_id": "c1", "volume": 0.0},
                )
            ],
        ),
    )
    aid1 = first["approval"]["id"]
    assert runtime.get_thread_state("vsess").pending_approval_id == aid1
    # Frozen selection identity on kwargs
    kw = first["approval"]["kwargs"]
    assert kw.get("selected_clip_ids") == ["c1"]
    assert kw.get("proposal_identity") == aid1
    assert kw.get("owner") == "video_editor"
    assert kw.get("project_id") == project.id
    assert kw.get("session_id") == "vsess"
    assert kw.get("document_id") == doc.id
    assert kw.get("expected_revision") == rev
    assert "set_clip_volume" in (kw.get("operation_types") or [])
    assert first["approval"].get("canonical_arguments_hash")
    pre = first["approval"].get("source_precondition") or {}
    assert pre.get("selected_clip_ids") == ["c1"]
    assert pre.get("argument_hash") or pre.get("operation_hash")

    with pytest.raises(VideoStoreError, match="pending approval"):
        propose_video_transaction_sync(
            doc.id,
            ProposalRequest(
                session_id="vsess",
                project_id=project.id,
                objective="Volume 50%",
                operations=[
                    EditOperation(
                        operation_type="set_clip_volume",
                        expected_revision=rev,
                        payload={"clip_id": "c1", "volume": 0.5},
                    )
                ],
            ),
        )
    # First approval still current
    assert runtime.get_thread_state("vsess").pending_approval_id == aid1
    assert runtime.get_approval(aid1).status == "pending"
