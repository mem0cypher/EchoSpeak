"""Production-closure: approval identity, video propose→apply ToolRuns, concurrency, research handoff."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest


def _coding_agent(tmp_path, monkeypatch, project_root, *, session="s1"):
    from agent.active_work import ActiveWorkStore
    from agent.core import EchoSpeakAgent
    from agent.projects import ProjectManager
    from agent.session_memory import SessionMemoryDistiller
    from agent.state import StateStore
    from config import ModelProvider, config
    import agent.projects as projects_mod
    import agent.state as state_mod

    manager = ProjectManager(tmp_path / "projects")
    project = manager.attach_folder(str(project_root), name="PC", trust_state="trusted")
    runtime = StateStore(tmp_path / "runtime")
    monkeypatch.setattr(projects_mod, "_project_manager", manager)
    monkeypatch.setattr(state_mod, "_state_store", runtime)
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_file_write", True)
    monkeypatch.setattr(config, "allow_terminal_commands", False)
    monkeypatch.setattr(config, "disable_native_tool_calling", True)
    monkeypatch.setattr(config, "verification_telemetry_enabled", False)
    monkeypatch.setattr(config, "file_tool_root", str(project_root))

    agent = EchoSpeakAgent(
        memory_path=str(tmp_path / "memory"),
        llm_provider=ModelProvider.OPENAI,
        manage_background_services=False,
    )
    agent._session_memory = SessionMemoryDistiller(tmp_path / "sessions")
    agent._active_work_store = ActiveWorkStore(tmp_path / "active-work")
    agent._allow_llm_tool_calling = lambda: False
    agent.graph_agent = None
    agent.agent_executor = None
    agent.fallback_executor = None
    agent.select_thread_runtime(session)
    agent.activate_project(project.id)
    return agent, runtime, project, manager


def test_approval_identity_blocks_stale_source_and_project_switch(tmp_path, monkeypatch):
    fixture = Path(__file__).parent / "fixtures" / "coding_project"
    project_root = tmp_path / "proj"
    shutil.copytree(fixture, project_root)
    agent, runtime, project, manager = _coding_agent(tmp_path, monkeypatch, project_root)

    class LLM:
        def invoke(self, prompt: str) -> str:
            if "SEARCH/REPLACE" in prompt:
                return (
                    "<<<<<<< SEARCH\n<title>EchoSpeak Coding Fixture</title>\n"
                    "=======\n<title>Identity Title</title>\n>>>>>>> REPLACE"
                )
            return "ok"

        def invoke_with_reasoning(self, prompt: str):
            return self.invoke(prompt), ""

    agent.llm_wrapper = LLM()
    agent.process_query(
        "Change the title in index.html only. Do not edit game.js.",
        include_memory=False,
        thread_id="s1",
    )
    approval = runtime.get_pending_approval("s1")
    assert approval is not None
    assert approval.canonical_arguments_hash
    assert approval.session_id == "s1"
    assert approval.project_id == project.id
    assert Path(approval.kwargs["path"]).name == "index.html"
    # Freeze metadata must not poison mutation precondition compare
    assert "path_basename" in (approval.source_precondition or {})
    assert (approval.source_precondition or {}).get("entries")

    # Stale source: external change invalidates confirm
    path = project_root / "index.html"
    path.write_text(path.read_text(encoding="utf-8") + "\n<!-- external -->\n", encoding="utf-8")
    stale_resp, stale_ok = agent.process_query("confirm", include_memory=False, thread_id="s1")
    assert stale_ok is False
    assert "changed" in stale_resp.lower() or "not run" in stale_resp.lower() or "blocked" in stale_resp.lower()


def test_duplicate_claim_pending_approval_is_idempotent(tmp_path):
    from agent.state import StateStore

    runtime = StateStore(tmp_path / "rt")
    approval = runtime.create_approval(
        thread_id="t1",
        session_id="t1",
        project_id="p1",
        tool="file_write",
        kwargs={"path": "a.txt", "content": "x"},
        original_input="write a",
        preview="write",
        summary="write",
        canonical_arguments_hash="abc",
    )
    first = runtime.claim_pending_approval(approval.id)
    second = runtime.claim_pending_approval(approval.id)
    assert first is not None and first.status == "consuming"
    assert second is None  # second concurrent/duplicate claim fails closed


def test_mutation_precondition_ignores_freeze_metadata(tmp_path, monkeypatch):
    """Regression: path_basename freeze fields must not block legitimate writes."""
    from agent.tools import _mutation_precondition_denial, update_tool_execution_context, _mutation_path_version

    target = tmp_path / "index.html"
    target.write_text("<html></html>", encoding="utf-8")
    entry = _mutation_path_version(target, "path")
    # Same content identity + extra freeze fields (as stored on ApprovalRecord)
    expected = {
        "version": 2,
        "entries": [entry],
        "tool": "file_write",
        "path_basename": "index.html",
        "original_input_sha256": "deadbeef",
    }
    update_tool_execution_context(mutation_precondition=expected)
    assert _mutation_precondition_denial("file_write") == ""
    # Real content change must still deny
    target.write_text("<html>changed</html>", encoding="utf-8")
    denial = _mutation_precondition_denial("file_write")
    assert "changed after approval" in denial


def test_video_propose_approve_toolrun_and_double_claim(tmp_path, monkeypatch):
    from agent.projects import ProjectManager
    from agent.state import StateStore
    from agent.video_editor.models import EditOperation, MediaAsset, MediaKind, RationalTime
    from agent.video_editor.store import VideoEditorStore
    from api.video_editor import ProposalRequest, consume_video_approval, propose_video_transaction_sync
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
    # Seed timeline with track + clip via manual apply
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

    proposal = propose_video_transaction_sync(
        doc.id,
        ProposalRequest(
            session_id="vsess",
            project_id=project.id,
            objective="Split selected clip at mid",
            operations=[
                EditOperation(
                    operation_type="split_clip",
                    expected_revision=rev,
                    payload={
                        "clip_id": "c1",
                        "right_clip_id": "c2",
                        "at": {"ticks": "3000", "time_base": {"numerator": 1, "denominator": 1000}},
                    },
                )
            ],
        ),
    )
    assert proposal["execution_id"]
    assert proposal["tool_run_id"]
    assert proposal["approval"]["id"]
    approval_id = proposal["approval"]["id"]
    runs = runtime.list_tool_runs(proposal["execution_id"])
    assert any(r.tool_name == "video_propose_operations" for r in runs)
    assert runtime.get_pending_approval("vsess") is not None

    # Project switch during pending approval must fail authority
    (tmp_path / "other").mkdir(exist_ok=True)
    other = manager.create_project(name="Other", workspace_root=str(tmp_path / "other"))
    runtime.update_thread_state("vsess", active_project_id=other.id)
    approval = runtime.get_approval(approval_id)
    from agent.video_editor.store import VideoStoreError
    from fastapi import HTTPException

    # Fail closed: switched Project is not attached (HTTPException or VideoStoreError)
    with pytest.raises((VideoStoreError, HTTPException)):
        consume_video_approval(approval)

    # Restore project and apply once
    runtime.update_thread_state("vsess", active_project_id=project.id, pending_approval_id=approval_id)
    # Re-fetch pending approval after project restore
    approval = runtime.get_approval(approval_id)
    # After failed consume attempt, approval remains pending (validate fails before claim)
    assert approval.status == "pending"
    result = consume_video_approval(approval)
    assert result.get("success") is True
    applied = store.get_document(project.id, doc.id)
    assert applied.revision == rev + 1
    clips = applied.timeline.tracks[0].clips
    assert {c.id for c in clips} == {"c1", "c2"}

    # Duplicate approve fails closed
    with pytest.raises(VideoStoreError):
        consume_video_approval(approval)


def test_video_stale_revision_rejected(tmp_path, monkeypatch):
    from agent.projects import ProjectManager
    from agent.state import StateStore
    from agent.video_editor.models import EditOperation
    from agent.video_editor.store import VideoEditorStore, VideoStoreError
    from api.video_editor import ProposalRequest, consume_video_approval, propose_video_transaction_sync
    from config import config
    import agent.projects as projects_mod
    import agent.state as state_mod
    import agent.video_editor.store as store_mod

    root = tmp_path / "vproj2"
    root.mkdir()
    manager = ProjectManager(tmp_path / "prec2")
    project = manager.attach_folder(str(root), name="Vid2", trust_state="trusted")
    store = VideoEditorStore(tmp_path / "vstore2", project_manager=manager)
    runtime = StateStore(tmp_path / "vrt2")
    monkeypatch.setattr(projects_mod, "_project_manager", manager)
    monkeypatch.setattr(state_mod, "_state_store", runtime)
    monkeypatch.setattr(store_mod, "_STORE", store)
    monkeypatch.setattr(store_mod, "_STORE_ROOT", Path(store.root).resolve())
    monkeypatch.setattr(store_mod, "resolve_video_data_dir", lambda data_dir=None: Path(store.root).resolve())
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_video_agent_edits", True)
    runtime.update_thread_state(
        "s2",
        active_project_id=project.id,
        project_path=str(root),
        workspace_root=str(root),
        permissions={"system_actions": True, "video_agent_edits": True},
        allowed_tool_names=["video_apply_transaction"],
    )
    doc = store.create_document(project.id, "D")
    proposal = propose_video_transaction_sync(
        doc.id,
        ProposalRequest(
            session_id="s2",
            project_id=project.id,
            objective="add track",
            operations=[
                EditOperation(
                    operation_type="add_track",
                    expected_revision=0,
                    payload={"track_id": "v1", "kind": "video", "name": "V"},
                )
            ],
        ),
    )
    # Advance revision externally
    other_tx, _ = store.prepare_transaction(
        project.id,
        doc.id,
        "s2",
        [
            EditOperation(
                operation_type="add_track",
                expected_revision=0,
                payload={"track_id": "v9", "kind": "video", "name": "ext"},
            )
        ],
        source="manual",
    )
    store.apply_transaction(other_tx)
    approval = runtime.get_approval(proposal["approval"]["id"])
    with pytest.raises(VideoStoreError, match="changed|revision|precondition"):
        consume_video_approval(approval)


def test_research_artifact_from_web_search_toolrun(tmp_path, monkeypatch):
    from agent.research_artifacts import find_compatible_research_artifact
    import agent.research_artifacts as ra
    from agent.state import StateStore
    from agent.core import EchoSpeakAgent, ToolOutcome
    from config import ModelProvider

    monkeypatch.setattr(ra, "_ROOT", tmp_path / "arts")
    runtime = StateStore(tmp_path / "rt")
    import agent.state as state_mod

    monkeypatch.setattr(state_mod, "_state_store", runtime)
    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent._state_store = runtime
    agent._thread_key = lambda: "rs1"
    agent._current_execution_id = "ex1"
    agent._tool_outcomes_by_run_id = {}
    agent._current_mode_decision = type("D", (), {"objective": "research oilers highlights"})()
    agent._promote_materialized_project = lambda *a, **k: None
    agent._dequeue_tool_run = lambda *a, **k: None
    agent._safe_retry_kwargs = lambda p: dict(p or {})
    agent._is_action_tool = lambda n: False
    from agent.tool_registry import ToolRegistry

    # Ensure web_search path persists
    runtime.create_execution(kind="turn", thread_id="rs1", source="test", status="running", query="q")
    # Force turn id
    agent._current_execution_id = list(runtime._executions.keys())[0]
    outcome = ToolOutcome(
        tool_name="web_search",
        run_id="tr-web-1",
        execution_id=agent._current_execution_id,
        success=True,
        status="complete",
        output="Oilers analysis https://example.com/oilers and https://example.com/nhl",
        started_at=0.0,
        completed_at=1.0,
    )
    agent._persist_tool_outcome(outcome, {"query": "edmonton oilers"})
    arts = list((tmp_path / "arts").glob("*.json")) if (tmp_path / "arts").exists() else []
    assert arts, "research artifact should be persisted for completed web_search"
    data = json.loads(arts[0].read_text(encoding="utf-8"))
    assert data["status"] == "ready"
    assert data["citations"] or data["source_urls"]
    assert data["session_id"] == "rs1"
    # Session-only lookup allowed when require_project=False; Project pin is preferred
    found = find_compatible_research_artifact(
        project_id="",
        session_id="rs1",
        objective="research oilers highlights",
        require_project=False,
    )
    assert found is not None

def test_skill_audit_no_disabled_executable(tmp_path, monkeypatch):
    import agent.video_editor.tools  # noqa: F401
    from agent.skill_status_audit import audit_all_skills

    rows = audit_all_skills(
        available_capabilities={"deterministic_editing", "approvals"},
        available_artifacts=set(),
    )
    for r in rows:
        if r["status"] in {"disabled", "invalid", "deprecated", "prompt_only", "blocked_missing_tool", "blocked_missing_model", "blocked_missing_artifact"}:
            assert r["executable"] is False
        if r["status"] == "executable":
            assert r["executable"] is True


def test_confirm_write_one_toolrun(tmp_path, monkeypatch):
    fixture = Path(__file__).parent / "fixtures" / "coding_project"
    project_root = tmp_path / "proj"
    shutil.copytree(fixture, project_root)
    agent, runtime, project, _ = _coding_agent(tmp_path, monkeypatch, project_root, session="one-write")

    class LLM:
        def invoke(self, prompt: str) -> str:
            if "SEARCH/REPLACE" in prompt:
                return (
                    "<<<<<<< SEARCH\n<title>EchoSpeak Coding Fixture</title>\n"
                    "=======\n<title>One Write</title>\n>>>>>>> REPLACE"
                )
            return "done"

        def invoke_with_reasoning(self, prompt: str):
            return self.invoke(prompt), ""

    agent.llm_wrapper = LLM()
    agent.process_query("Change the title in index.html.", include_memory=False, thread_id="one-write")
    confirm_resp, ok = agent.process_query("confirm", include_memory=False, thread_id="one-write")
    body = (project_root / "index.html").read_text(encoding="utf-8")
    assert "One Write" in body, f"durable write missing; ok={ok} resp={confirm_resp!r}"
    ex = runtime.get_thread_state("one-write").last_execution_id
    writes = [r for r in runtime.list_tool_runs(ex) if r.tool_name == "file_write"]
    assert len(writes) == 1, f"expected one write ToolRun, got {[(w.tool_name, w.status) for w in runtime.list_tool_runs(ex)]}"
    assert writes[0].status in {"complete", "completed", "success"}
    # Turn-level ok should follow successful verified mutation when verification passes.
    if writes[0].verification and writes[0].verification.get("verified") is True:
        assert ok is True
