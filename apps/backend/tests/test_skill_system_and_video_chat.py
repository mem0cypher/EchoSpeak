"""Canonical skill system + video chat-turn integration tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.projects import ProjectManager
from agent.skill_contract import SkillSelectionOutcome, SkillStatus
from agent.skill_execution import create_skill_execution, create_skill_proposal, get_skill_execution
from agent.skill_contract import SkillExecutionStatus, SkillProposal
from agent.skill_selection import detect_direct_tool, select_composition, select_skill
from agent.skills_registry import SkillsRegistry, load_skills, package_to_manifest
from agent.video_editor.chat_integration import (
    build_video_turn_package,
    filter_tools_for_turn,
    is_video_edit_intent,
)
from agent.video_editor.models import MediaAsset, MediaKind, RationalTime
from agent.video_editor.store import VideoEditorStore
from agent.video_editor import tools as _video_tools  # noqa: F401 — register tools


@pytest.fixture()
def skills_env(tmp_path: Path, monkeypatch):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    # Minimal package skill
    pkg = skills_dir / "demo_skill"
    pkg.mkdir()
    (pkg / "skill.json").write_text(
        '{"name":"Demo","description":"Demo skill","tools":["get_system_time"],"version":"1.0.0"}',
        encoding="utf-8",
    )
    (pkg / "SKILL.md").write_text("# Demo\nUse get_system_time.\n", encoding="utf-8")
    # Disabled skill
    dis = skills_dir / "disabled_skill"
    dis.mkdir()
    (dis / "skill.json").write_text('{"name":"Disabled","description":"Off"}', encoding="utf-8")
    (dis / "SKILL.md").write_text("# Off\n", encoding="utf-8")
    (dis / ".disabled").write_text("1\n", encoding="utf-8")
    # Invalid: no prompt
    bad = skills_dir / "broken_skill"
    bad.mkdir()
    (bad / "skill.json").write_text('{"name":"Broken"}', encoding="utf-8")

    SkillsRegistry.clear()
    SkillsRegistry.refresh(skills_dir)
    return skills_dir


def test_canonical_registry_loads_and_skips_disabled(skills_env):
    manifests = {m.id: m for m in SkillsRegistry.list_manifests(include_disabled=False)}
    assert "demo_skill" in manifests
    assert "disabled_skill" not in manifests
    disabled = {m.id: m for m in SkillsRegistry.list_manifests(include_disabled=True)}
    assert disabled["disabled_skill"].status == SkillStatus.DISABLED
    assert "broken_skill" in disabled
    assert disabled["broken_skill"].status == SkillStatus.INVALID
    # Video domain skills bridged into same registry
    assert any(k.startswith("video_") for k in SkillsRegistry.refresh(skills_env))


def test_disabled_skill_not_in_load_skills(skills_env):
    loaded = load_skills(skills_env)
    assert "demo_skill" in loaded
    assert "disabled_skill" not in loaded


def test_direct_tool_vs_skill_selection(skills_env):
    SkillsRegistry.refresh(skills_env)
    manifests = SkillsRegistry.list_manifests()
    tools = {"video_propose_operations", "video_apply_transaction", "video_get_editor_context", "get_system_time"}
    direct = select_skill(
        user_text="Split the selected clip at the playhead",
        manifests=manifests,
        available_tools=tools,
        available_capabilities={"deterministic_editing", "timeline_mutation", "approvals"},
        permissions={"system_actions", "video_agent_edits"},
        domain_hint="video",
    )
    assert direct.outcome == SkillSelectionOutcome.DIRECT_TOOL_BETTER
    assert direct.direct_tool == "video_propose_operations"

    skill = select_skill(
        user_text="Remove the silent parts from my video",
        manifests=manifests,
        available_tools={
            "video_get_editor_context",
            "video_submit_job",
            "video_propose_operations",
            "video_apply_transaction",
        },
        available_capabilities={"analysis", "deterministic_editing", "approvals"},
        available_artifacts=set(),  # missing silence artifact / analysis may block model
        permissions={"system_actions", "video_agent_edits"},
        domain_hint="video",
    )
    assert skill.skill_id == "video_remove_silence"
    # Without analysis capability token properly, may be blocked_missing_model or selected
    assert skill.outcome in {
        SkillSelectionOutcome.SELECTED,
        SkillSelectionOutcome.BLOCKED_MISSING_MODEL,
        SkillSelectionOutcome.BLOCKED_MISSING_ARTIFACT,
    }

    none = select_skill(
        user_text="What is the capital of France?",
        manifests=manifests,
        available_tools=tools,
    )
    assert none.outcome == SkillSelectionOutcome.NO_MATCHING_SKILL


def test_explicit_skill_and_no_stale_prior(skills_env):
    SkillsRegistry.refresh(skills_env)
    manifests = SkillsRegistry.list_manifests()
    # Stale prior must not auto-select without continue language
    result = select_skill(
        user_text="hello there",
        manifests=manifests,
        prior_unfinished_skill_id="video_rough_cut",
        allow_stale_prior=False,
    )
    assert result.outcome == SkillSelectionOutcome.NO_MATCHING_SKILL
    assert result.skill_id == ""


def test_composition_orders_multiple_skills(skills_env):
    SkillsRegistry.refresh(skills_env)
    manifests = SkillsRegistry.list_manifests()
    parts = select_composition(
        user_text="Research this topic, remove silence, and add captions",
        manifests=manifests,
        available_tools={
            "video_plan_request",
            "video_get_editor_context",
            "video_submit_job",
            "video_propose_operations",
            "video_apply_transaction",
            "web_search",
            "video_inspect_media",
        },
        available_capabilities={"research", "analysis", "transcription", "approvals"},
        permissions={"system_actions", "video_agent_edits"},
    )
    ids = [p.skill_id for p in parts if p.skill_id]
    assert "video_script_research" in ids or any("research" in (p.reason or "").lower() for p in parts)
    assert any(p.skill_id == "video_remove_silence" for p in parts) or any(
        p.outcome != SkillSelectionOutcome.NO_MATCHING_SKILL for p in parts
    )


def test_filter_tools_strips_video_on_non_video_turns():
    names = {"web_search", "video_apply_transaction", "file_read", "video_plan_request"}
    assert "video_apply_transaction" not in filter_tools_for_turn(names, video_turn=False)
    assert "video_apply_transaction" in filter_tools_for_turn(names, video_turn=True)


def test_video_chat_package_and_utility_exclusion(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    manager = ProjectManager(tmp_path / "projects")
    project = manager.attach_folder(str(project_root), name="V", trust_state="trusted")
    store = VideoEditorStore(tmp_path / "video", project_manager=manager)
    document = store.create_document(project.id, "Cut")
    store.add_asset(
        project.id,
        document.id,
        MediaAsset(
            project_id=project.id,
            document_id=document.id,
            name="a.mp4",
            kind=MediaKind.VIDEO,
            project_relative_path="a.mp4",
            sha256="e" * 64,
            size_bytes=1,
            mtime_ns=1,
            duration=RationalTime(ticks="1000"),
        ),
    )
    SkillsRegistry.clear()
    SkillsRegistry.refresh(tmp_path / "empty_skills")
    (tmp_path / "empty_skills").mkdir(exist_ok=True)

    assert is_video_edit_intent("Remove silence from the timeline")
    assert not is_video_edit_intent("what time is it")

    thread = SimpleNamespace(
        active_project_id=project.id,
        permissions={"system_actions": True, "video_agent_edits": True},
        allowed_tool_names=[],
        constraints=[],
        pending_approval_id="",
    )
    # Utility with open document → inactive package
    util = build_video_turn_package(
        session_id="s1",
        project_id=project.id,
        user_text="what time is it",
        store=store,
        thread_state=thread,
        skill_manifests=SkillsRegistry.list_manifests(),
    )
    assert util.active is False

    edit = build_video_turn_package(
        session_id="s1",
        project_id=project.id,
        user_text="Remove the silent parts",
        document_id=document.id,
        store=store,
        thread_state=thread,
        skill_manifests=SkillsRegistry.list_manifests(),
    )
    assert edit.active is True
    assert edit.context is not None
    assert edit.context.document_id == document.id
    assert edit.context_prompt_block
    assert "video_get_editor_context" in edit.allowed_video_tools
    assert edit.skill_selection is not None
    assert edit.skill_selection.skill_id == "video_remove_silence"


def test_skill_execution_record_and_proposal(tmp_path, monkeypatch):
    import agent.skill_execution as se

    monkeypatch.setattr(se, "_EXEC_DIR", tmp_path / "exec")
    monkeypatch.setattr(se, "_PROPOSAL_DIR", tmp_path / "prop")
    rec = create_skill_execution(
        execution_id="exec-1",
        skill_id="video_rough_cut",
        skill_version="1.0.0",
        project_id="p1",
        session_id="s1",
        status=SkillExecutionStatus.PLANNED,
    )
    loaded = get_skill_execution(rec.id)
    assert loaded is not None
    assert loaded.skill_id == "video_rough_cut"
    assert loaded.status == SkillExecutionStatus.PLANNED

    prop = create_skill_proposal(
        SkillProposal(
            id="prop-1",
            name="Podcast Cut",
            description="Reusable podcast workflow",
            reason_created="missing workflow",
            status="proposed",
        )
    )
    assert prop.status == "proposed"


def test_skill_create_writes_disabled_package(tmp_path, monkeypatch):
    import skills.skill_writer.tools as sw

    monkeypatch.setattr(sw, "_skills_dir", lambda: tmp_path / "skills")
    (tmp_path / "skills").mkdir()
    # skill_create is a langchain tool — invoke via .invoke if available
    fn = sw.skill_create
    invoke = getattr(fn, "invoke", None)
    if callable(invoke):
        out = invoke(
            {
                "name": "Podcast Episodes",
                "description": "Edit podcasts",
                "prompt": "# Podcast\nCut intros.",
                "tool_names": ["video_propose_operations"],
            }
        )
    else:
        out = fn(
            name="Podcast Episodes",
            description="Edit podcasts",
            prompt="# Podcast\nCut intros.",
            tool_names=["video_propose_operations"],
        )
    assert "DISABLED" in str(out) or "disabled" in str(out).lower()
    skill_path = tmp_path / "skills" / "podcast_episodes"
    assert skill_path.exists()
    assert (skill_path / ".disabled").exists()
    assert (skill_path / ".experimental").exists()
    # Not loaded by load_skills
    loaded = load_skills(tmp_path / "skills")
    assert "podcast_episodes" not in loaded


def test_core_tool_allowed_gates_video(monkeypatch):
    """video_* tools require an active video turn package."""
    from agent.core import EchoSpeakAgent

    class Stub:
        _video_turn_package = None
        _current_mode_decision = None
        _execution_context = None
        _tool_allowlist_override = None
        _current_source = None
        _active_retry_action = None

        def _approved_action_matches(self, name):
            return False

        _tool_allowed = EchoSpeakAgent._tool_allowed

    stub = Stub()
    assert EchoSpeakAgent._tool_allowed(stub, "video_apply_transaction") is False
    stub._video_turn_package = SimpleNamespace(
        active=True,
        allowed_video_tools=["video_apply_transaction", "video_plan_request"],
    )
    assert EchoSpeakAgent._tool_allowed(stub, "video_apply_transaction") is True
    assert EchoSpeakAgent._tool_allowed(stub, "video_submit_job") is False
    assert EchoSpeakAgent._tool_allowed(stub, "web_search") is True
