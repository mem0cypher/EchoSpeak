"""Video tools must return real structured domain results, not empty service stubs."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.projects import ProjectManager
from agent.video_editor.store import VideoEditorStore
from agent.video_editor import tools as video_tools
import agent.state as state_mod
from agent.state import StateStore


@pytest.fixture()
def env(tmp_path: Path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    manager = ProjectManager(tmp_path / "records")
    project = manager.attach_folder(str(root), name="VT", trust_state="trusted")
    store = VideoEditorStore(tmp_path / "video", project_manager=manager)
    runtime = StateStore(tmp_path / "runtime")
    monkeypatch.setattr(state_mod, "_state_store", runtime)
    import agent.video_editor.store as store_mod
    import agent.projects as projects_mod

    monkeypatch.setattr(store_mod, "_STORE", store)
    monkeypatch.setattr(store_mod, "_STORE_ROOT", Path(store.root).resolve())
    monkeypatch.setattr(store_mod, "resolve_video_data_dir", lambda data_dir=None: Path(store.root).resolve())
    monkeypatch.setattr(projects_mod, "_project_manager", manager)
    runtime.update_thread_state("sess", active_project_id=project.id, project_path=str(root))
    doc = store.create_document(project.id, "Cut")
    return project, doc, store


def test_video_get_editor_context_returns_ok_json(env):
    project, doc, _store = env
    raw = video_tools.video_get_editor_context.invoke(
        {"session_id": "sess", "project_id": project.id, "document_id": doc.id}
    )
    import json

    data = json.loads(raw)
    assert data["ok"] is True
    assert data["context"]["document_id"] == doc.id


def test_video_inspect_timeline_and_plan(env):
    project, doc, _store = env
    import json

    timeline = json.loads(
        video_tools.video_inspect_timeline.invoke(
            {"session_id": "sess", "project_id": project.id, "document_id": doc.id}
        )
    )
    assert timeline["ok"] is True
    assert timeline["revision"] == 0

    plan = json.loads(
        video_tools.video_plan_request.invoke(
            {
                "session_id": "sess",
                "project_id": project.id,
                "document_id": doc.id,
                "objective": "Rough cut the footage",
            }
        )
    )
    assert plan["ok"] is True
    assert plan["mutates"] is False
    assert plan["plan"]["objective"]


def test_video_apply_requires_approval_boundary(env):
    project, doc, _store = env
    import json

    out = json.loads(
        video_tools.video_apply_transaction.invoke(
            {
                "session_id": "sess",
                "project_id": project.id,
                "document_id": doc.id,
                "transaction_id": "t1",
                "plan_id": "p1",
                "expected_revision": 0,
                "operation_hash": "abc",
            }
        )
    )
    assert out["ok"] is False
    assert out["error_code"] == "approval_required"
    assert out.get("applied") is False
