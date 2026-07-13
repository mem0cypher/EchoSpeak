"""Agentic foundation for the Video Editor: context, tools, skills, plan, jobs."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.projects import ProjectManager
from agent.video_editor.context import build_editor_context, editor_context_for_prompt
from agent.video_editor.jobs import prepare_job, project_job_for_frontend, request_cancel, retry_job
from agent.video_editor.memory import sanitize_memory_payload, update_creative_memory
from agent.video_editor.models import (
    EditOperation,
    EditorSelectionContext,
    MediaAsset,
    MediaKind,
    RationalTime,
)
from agent.video_editor.planning import plan_video_request
from agent.video_editor.skills import VideoSkillRegistry, list_video_skills, propose_skill_draft
from agent.video_editor.store import VideoEditorStore, VideoStoreError
from agent.video_editor.tool_catalog import get_video_tool, list_video_tool_names


@pytest.fixture()
def video_env(tmp_path: Path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    manager = ProjectManager(tmp_path / "project-records")
    project = manager.attach_folder(str(project_root), name="Agentic Video", trust_state="trusted")
    store = VideoEditorStore(tmp_path / "video-state", project_manager=manager)
    document = store.create_document(project.id, "Cut 1")
    asset = MediaAsset(
        project_id=project.id,
        document_id=document.id,
        name="cam.mp4",
        kind=MediaKind.VIDEO,
        project_relative_path="cam.mp4",
        sha256="d" * 64,
        size_bytes=40,
        mtime_ns=3,
        duration=RationalTime(ticks="12000"),
    )
    document = store.add_asset(project.id, document.id, asset)
    return store, project, document, asset


def _op(operation_type: str, revision: int, **payload):
    return EditOperation(operation_type=operation_type, expected_revision=revision, payload=payload)


def test_tool_catalog_and_skills_registered():
    names = list_video_tool_names()
    assert "video_get_editor_context" in names
    assert "video_apply_transaction" in names
    assert "video_submit_job" in names
    apply = get_video_tool("video_apply_transaction")
    assert apply is not None
    assert apply.mutates_timeline and apply.requires_approval
    skills = list_video_skills()
    assert any(item["id"] == "video_rough_cut" for item in skills)
    assert any(item["id"] == "video_generate_broll" for item in skills)
    draft = propose_skill_draft(
        name="Custom Silence",
        description="Draft only",
        accepted_intentions=["custom silence"],
        required_tools=["video_propose_operations"],
    )
    assert draft.status == "draft"
    assert VideoSkillRegistry.get(draft.id) is None


def test_editor_context_is_structured_not_prose(video_env):
    store, project, document, asset = video_env
    selection = EditorSelectionContext(
        document_id=document.id,
        selected_clip_ids=[],
        selected_asset_ids=[asset.id],
        playhead=RationalTime(ticks="1000"),
        document_revision=document.revision,
    )
    thread = SimpleNamespace(
        active_project_id=project.id,
        permissions={"system_actions": True, "video_agent_edits": True},
        allowed_tool_names=["video_apply_transaction"],
        constraints=[],
        pending_approval_id="",
    )
    context = build_editor_context(
        session_id="sess-1",
        project_id=project.id,
        document_id=document.id,
        selection=selection,
        store=store,
        thread_state=thread,
        config=SimpleNamespace(enable_system_actions=True, allow_video_agent_edits=True),
    )
    assert context.document_id == document.id
    assert context.document_revision == 0
    assert len(context.assets) == 1
    assert context.assets[0].id == asset.id
    assert context.authority.mutation_allowed is True
    assert "video_plan_request" in context.capabilities.available_tools
    assert context.selection is not None
    assert context.selection.playhead.ticks == "1000"
    prompt = editor_context_for_prompt(context)
    assert document.id in prompt
    assert "schema_version" in prompt


def test_plan_ready_with_ops_and_blocked_without_permissions(video_env):
    store, project, document, asset = video_env
    thread_ok = SimpleNamespace(
        active_project_id=project.id,
        permissions={"system_actions": True, "video_agent_edits": True},
        allowed_tool_names=["video_apply_transaction"],
        constraints=[],
        pending_approval_id="",
    )
    context = build_editor_context(
        session_id="sess-1",
        project_id=project.id,
        document_id=document.id,
        store=store,
        thread_state=thread_ok,
        config=SimpleNamespace(enable_system_actions=True, allow_video_agent_edits=True),
    )
    ops = [
        _op("add_track", 0, track_id="v1", kind="video", name="V1"),
        _op(
            "insert_clip",
            0,
            track_id="v1",
            clip_id="c1",
            asset_id=asset.id,
            timeline_start={"ticks": "0"},
            duration={"ticks": "4000"},
        ),
    ]
    plan = plan_video_request(
        context=context,
        objective="Assemble a rough cut of the imported camera clip",
        operations=ops,
        store=store,
    )
    assert plan.status == "ready"
    assert plan.skill_id == "video_rough_cut"
    assert len(plan.operations) == 2
    assert any(step.tool_name == "video_propose_operations" for step in plan.steps)
    assert plan.resumable is True

    thread_blocked = SimpleNamespace(
        active_project_id=project.id,
        permissions={"system_actions": False, "video_agent_edits": False},
        allowed_tool_names=[],
        constraints=["read_only"],
        pending_approval_id="",
    )
    blocked_ctx = build_editor_context(
        session_id="sess-1",
        project_id=project.id,
        document_id=document.id,
        store=store,
        thread_state=thread_blocked,
        config=SimpleNamespace(enable_system_actions=False, allow_video_agent_edits=False),
    )
    blocked = plan_video_request(
        context=blocked_ctx,
        objective="Assemble a rough cut",
        operations=ops,
        store=store,
    )
    assert blocked.status == "blocked"
    assert any(item.startswith("permission:") or item.startswith("authority:") for item in blocked.missing_requirements)


def test_stale_ops_rejected_by_planner(video_env):
    store, project, document, asset = video_env
    # Advance revision.
    tx, _ = store.prepare_transaction(
        project.id,
        document.id,
        "sess-1",
        [_op("add_track", 0, track_id="v1", kind="video")],
        source="manual",
    )
    document = store.apply_transaction(tx)
    context = build_editor_context(
        session_id="sess-1",
        project_id=project.id,
        document_id=document.id,
        store=store,
        thread_state=SimpleNamespace(
            active_project_id=project.id,
            permissions={"system_actions": True, "video_agent_edits": True},
            allowed_tool_names=[],
            constraints=[],
            pending_approval_id="",
        ),
        config=SimpleNamespace(enable_system_actions=True, allow_video_agent_edits=True),
    )
    with pytest.raises(VideoStoreError, match="Stale"):
        plan_video_request(
            context=context,
            objective="Insert",
            operations=[
                _op(
                    "insert_clip",
                    0,  # stale
                    track_id="v1",
                    asset_id=asset.id,
                    timeline_start={"ticks": "0"},
                    duration={"ticks": "1000"},
                )
            ],
            store=store,
        )


def test_clip_property_ops_and_creative_memory(video_env):
    store, project, document, asset = video_env
    tx, _ = store.prepare_transaction(
        project.id,
        document.id,
        "sess-1",
        [
            _op("add_track", 0, track_id="v1", kind="video"),
            _op(
                "insert_clip",
                0,
                track_id="v1",
                clip_id="c1",
                asset_id=asset.id,
                timeline_start={"ticks": "0"},
                duration={"ticks": "5000"},
            ),
        ],
        source="manual",
    )
    document = store.apply_transaction(tx)
    props, _ = store.prepare_transaction(
        project.id,
        document.id,
        "sess-1",
        [
            _op("set_clip_volume", 1, clip_id="c1", volume=0.5),
            _op("set_clip_speed", 1, clip_id="c1", speed=1.5),
            _op("set_clip_transform", 1, clip_id="c1", transform={"scale": 1.1, "x": 10}),
        ],
        source="manual",
    )
    document = store.apply_transaction(props)
    clip = document.timeline.tracks[0].clips[0]
    assert clip.volume == 0.5
    assert clip.metadata.get("speed") == 1.5
    assert clip.transform.get("scale") == 1.1

    dirty = sanitize_memory_payload(
        {"preferred_style": "cinematic", "playhead": {"ticks": "9"}, "selection": {"clip": "c1"}}
    )
    assert "playhead" not in dirty and "selection" not in dirty
    memory = update_creative_memory(
        project_id=project.id,
        session_id="sess-1",
        document_id=document.id,
        preferred_style="cinematic",
        output_format="1080p30",
        creative_objective="Trailer cut",
        store=store,
    )
    assert memory.preferred_style == "cinematic"
    reloaded = store.get_document(project.id, document.id)
    assert reloaded.creative_memory is not None
    assert reloaded.creative_memory.creative_objective == "Trailer cut"


def test_job_toolrun_projection_blocked_truth_and_retry(video_env):
    store, project, document, _asset = video_env
    _doc, job = prepare_job(
        project_id=project.id,
        document_id=document.id,
        session_id="sess-1",
        kind="generation",
        capability="text_to_video",
        idempotency_key="gen-1",
        parameters={"prompt": "city night"},
        store=store,
    )
    # No generative adapter available → blocked, not completed.
    assert job.status == "blocked"
    projection = project_job_for_frontend(job)
    assert projection["completed"] is False
    assert projection["terminal"] is False or job.status == "blocked"

    with pytest.raises(VideoStoreError, match="without outputs"):
        store.update_job(project.id, document.id, job.id, status="completed")

    canceled = request_cancel(store, project.id, document.id, job.id)
    assert canceled.cancel_requested is True

    # Mark retryable and retry.
    store.update_job(project.id, document.id, job.id, status="retryable", error="adapter offline")
    _doc2, retried = retry_job(store, project.id, document.id, job.id)
    assert retried.id != job.id
    assert retried.parameters.get("retry_of") == job.id
    assert retried.status == "blocked"


def test_api_plan_and_context_endpoints(tmp_path, monkeypatch):
    import agent.projects as projects_mod
    import agent.state as state_mod
    import agent.video_editor.store as video_store_mod
    from agent.state import StateStore
    from api.video_editor import EditorContextRequest, PlanRequest, editor_context, plan_request
    from config import config

    project_root = tmp_path / "project"
    project_root.mkdir()
    manager = ProjectManager(tmp_path / "project-records")
    project = manager.attach_folder(str(project_root), name="API Video", trust_state="trusted")
    runtime = StateStore(tmp_path / "runtime")
    store = VideoEditorStore(tmp_path / "video-state", project_manager=manager)
    monkeypatch.setattr(projects_mod, "_project_manager", manager)
    monkeypatch.setattr(state_mod, "_state_store", runtime)
    monkeypatch.setattr(video_store_mod, "_STORE", store)
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_video_agent_edits", True)
    runtime.update_thread_state(
        "agentic-session",
        active_project_id=project.id,
        project_path=str(project_root),
        workspace_root=str(project_root),
        permissions={"system_actions": True, "video_agent_edits": True},
    )
    document = store.create_document(project.id, "API Cut")

    ctx = asyncio.run(
        editor_context(
            EditorContextRequest(
                session_id="agentic-session",
                project_id=project.id,
                document_id=document.id,
            )
        )
    )
    assert ctx["document_id"] == document.id
    assert ctx["capabilities"]["deterministic_editing"] is True

    planned = asyncio.run(
        plan_request(
            PlanRequest(
                session_id="agentic-session",
                project_id=project.id,
                document_id=document.id,
                objective="Research a script outline for a product demo",
            )
        )
    )
    assert planned["plan"]["skill_id"] == "video_script_research"
    assert planned["tool_run_id"]
    assert planned["execution_id"]
    runs = runtime.list_tool_runs(planned["execution_id"])
    assert runs
    assert runs[0].id == planned["tool_run_id"]
    assert runs[0].status == "complete"
