"""Disposable restart/soak: mutate durable state, recreate stores, verify terminal truth."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure backend root is importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from agent.projects import ProjectManager
    from agent.state import StateStore
    from agent.video_editor.models import EditOperation, MediaAsset, MediaKind, RationalTime
    from agent.video_editor.store import VideoEditorStore
    from agent.research_artifacts import (
        build_research_artifact_from_tool_output,
        find_compatible_research_artifact,
        save_research_artifact,
    )
    import agent.research_artifacts as ra
    from api.video_editor import ProposalRequest, consume_video_approval, propose_video_transaction_sync
    from config import config
    import agent.projects as projects_mod
    import agent.state as state_mod
    import agent.video_editor.store as store_mod

    base = Path(tempfile.mkdtemp(prefix="echospeak-soak-"))
    try:
        proj_root = base / "project"
        proj_root.mkdir()
        (proj_root / "notes.txt").write_text("hello soak\n", encoding="utf-8")
        records = base / "records"
        video_dir = base / "video"
        runtime_dir = base / "runtime"
        arts = base / "arts"
        ra._ROOT = arts

        manager = ProjectManager(records)
        project = manager.attach_folder(str(proj_root), name="Soak", trust_state="trusted")
        store = VideoEditorStore(video_dir, project_manager=manager)
        runtime = StateStore(runtime_dir)
        projects_mod._project_manager = manager
        state_mod._state_store = runtime
        store_mod._STORE = store
        config.enable_system_actions = True
        config.allow_video_agent_edits = True

        runtime.update_thread_state(
            "soak-sess",
            active_project_id=project.id,
            project_path=str(proj_root),
            workspace_root=str(proj_root),
            permissions={"system_actions": True, "video_agent_edits": True},
            allowed_tool_names=["video_apply_transaction", "video_propose_operations"],
        )
        doc = store.create_document(project.id, "SoakDoc")
        asset = MediaAsset(
            project_id=project.id,
            document_id=doc.id,
            name="a.mp4",
            kind=MediaKind.VIDEO,
            project_relative_path="a.mp4",
            sha256="c" * 64,
            size_bytes=1,
            mtime_ns=1,
            duration=RationalTime(ticks="8000"),
        )
        doc = store.add_asset(project.id, doc.id, asset)
        tx, _ = store.prepare_transaction(
            project.id,
            doc.id,
            "soak-sess",
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
                        "duration": {"ticks": "5000", "time_base": {"numerator": 1, "denominator": 1000}},
                    },
                ),
            ],
            source="manual",
        )
        doc = store.apply_transaction(tx)
        proposal = propose_video_transaction_sync(
            doc.id,
            ProposalRequest(
                session_id="soak-sess",
                project_id=project.id,
                objective="trim soak clip",
                operations=[
                    EditOperation(
                        operation_type="trim_clip",
                        expected_revision=doc.revision,
                        payload={
                            "clip_id": "c1",
                            "duration": {"ticks": "3000", "time_base": {"numerator": 1, "denominator": 1000}},
                        },
                    )
                ],
            ),
        )
        approval_id = proposal["approval"]["id"]
        exec_id = proposal["execution_id"]
        art = build_research_artifact_from_tool_output(
            output="Findings https://example.com/soak",
            query="soak research",
            project_id=project.id,
            session_id="soak-sess",
            execution_id=exec_id,
            objective="soak research",
        )
        save_research_artifact(art)

        # --- "restart": drop in-memory singletons and reopen from disk ---
        projects_mod._project_manager = None
        state_mod._state_store = None
        store_mod._STORE = None

        manager2 = ProjectManager(records)
        store2 = VideoEditorStore(video_dir, project_manager=manager2)
        runtime2 = StateStore(runtime_dir)
        projects_mod._project_manager = manager2
        state_mod._state_store = runtime2
        store_mod._STORE = store2

        ts = runtime2.get_thread_state("soak-sess")
        assert ts.active_project_id == project.id, "session project lost after restart"
        pending = runtime2.get_pending_approval("soak-sess")
        assert pending is not None and pending.id == approval_id, "pending approval lost"
        assert pending.status == "pending"
        runs = runtime2.list_tool_runs(exec_id)
        assert any(r.tool_name == "video_propose_operations" for r in runs)
        found = find_compatible_research_artifact(
            project_id=project.id, session_id="soak-sess", objective="soak research"
        )
        assert found is not None and found.id == art.id

        # Consume after restart
        result = consume_video_approval(pending)
        assert result.get("success") is True
        doc2 = store2.get_document(project.id, doc.id)
        assert doc2.revision == doc.revision + 1
        # Terminal approval cannot re-execute
        try:
            consume_video_approval(pending)
            raise AssertionError("stale approval re-executed after restart")
        except Exception as exc:
            assert "already" in str(exc).lower() or "pending" in str(exc).lower() or "consumed" in str(exc).lower() or "not a pending" in str(exc).lower()

        pending_after = runtime2.get_pending_approval("soak-sess")
        assert pending_after is None or pending_after.id != approval_id or pending_after.status != "pending"

        print("SOAK_OK", json.dumps({
            "base": str(base),
            "project_id": project.id,
            "approval_id": approval_id,
            "execution_id": exec_id,
            "revision_after": doc2.revision,
            "artifact_id": art.id,
        }))
        return 0
    finally:
        try:
            shutil.rmtree(base, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
