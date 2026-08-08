from __future__ import annotations

from pathlib import Path

import pytest

from agent.skill_contract import SkillExecutionStatus, SkillManifest, SkillOrigin, SkillStatus
from agent.skill_execution import (
    SkillExecutionError,
    activate_skill_execution,
    create_skill_execution,
    finalize_skill_executions_for_turn,
    get_skill_execution,
    record_skill_tool_outcome,
    update_skill_execution,
)
from agent.skills_registry import SkillsRegistry
from agent.state import StateStore
from agent.tool_registry import ToolRegistry


@ToolRegistry.register(name="fixture_skill_tool", description="Disposable skill lifecycle tool")
def fixture_skill_tool() -> str:
    return "ok"


def test_skill_lifecycle_links_parent_and_child_toolruns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import agent.skill_execution as execution_module

    monkeypatch.setattr(execution_module, "_EXEC_DIR", tmp_path / "skill-executions")
    manifest = SkillManifest(
        id="fixture_skill",
        name="Fixture Skill",
        origin=SkillOrigin.BUILT_IN,
        status=SkillStatus.BUILT_IN,
        executable=True,
        implementation_entry="fixture:run",
        required_tools=["fixture_skill_tool"],
        verification_rules=["child_tool_succeeded"],
    )
    monkeypatch.setattr(SkillsRegistry, "_manifests", {manifest.id: manifest})
    runtime = StateStore(tmp_path / "runtime")
    turn = runtime.create_execution(thread_id="session-1", source="test", status="running")
    record = create_skill_execution(
        execution_id=turn.id,
        turn_id=turn.id,
        session_id="session-1",
        project_id="project-1",
        skill_id=manifest.id,
        status=SkillExecutionStatus.SELECTED,
    )
    running = activate_skill_execution(
        record.id,
        state_store=runtime,
        allowed_tool_names={"fixture_skill_tool"},
    )
    assert running.status == SkillExecutionStatus.RUNNING
    assert running.parent_tool_run_id.startswith("skill-")

    child = runtime.create_tool_run(
        turn_id=turn.id,
        session_id="session-1",
        project_id="project-1",
        run_id="fixture-child-run",
        tool_name="fixture_skill_tool",
    )
    child = runtime.finish_tool_run(child.id, {"success": True, "status": "complete", "verification": {"verified": True}})
    record_skill_tool_outcome(runtime, child)
    completed = finalize_skill_executions_for_turn(turn.id, state_store=runtime, turn_success=True)

    assert completed[0].status == SkillExecutionStatus.COMPLETED
    assert "fixture-child-run" in completed[0].tool_run_ids
    assert completed[0].verification["passed"] is True
    parent_run = next(run for run in runtime.list_tool_runs(turn.id) if run.id == running.parent_tool_run_id)
    assert parent_run.status == "complete"


def test_prompt_only_skill_blocks_and_terminal_transition_cannot_reopen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import agent.skill_execution as execution_module

    monkeypatch.setattr(execution_module, "_EXEC_DIR", tmp_path / "skill-executions")
    manifest = SkillManifest(
        id="prompt_only",
        name="Prompt only",
        origin=SkillOrigin.PACKAGE,
        status=SkillStatus.INSTALLED,
        executable=False,
        prompt="Instructions only",
    )
    monkeypatch.setattr(SkillsRegistry, "_manifests", {manifest.id: manifest})
    runtime = StateStore(tmp_path / "runtime")
    turn = runtime.create_execution(thread_id="session-1", source="test", status="running")
    record = create_skill_execution(execution_id=turn.id, session_id="session-1", skill_id=manifest.id)
    blocked = activate_skill_execution(record.id, state_store=runtime)
    assert blocked.status == SkillExecutionStatus.BLOCKED
    assert blocked.prompt_only is True

    update_skill_execution(blocked.id, status=SkillExecutionStatus.PLANNED)
    update_skill_execution(blocked.id, status=SkillExecutionStatus.RUNNING)
    update_skill_execution(blocked.id, status=SkillExecutionStatus.COMPLETED)
    with pytest.raises(SkillExecutionError, match="Invalid SkillExecution transition"):
        update_skill_execution(blocked.id, status=SkillExecutionStatus.RUNNING)
    assert get_skill_execution(blocked.id).status == SkillExecutionStatus.COMPLETED


def test_explicit_empty_turn_inventory_blocks_registered_skill_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import agent.skill_execution as execution_module

    monkeypatch.setattr(execution_module, "_EXEC_DIR", tmp_path / "skill-executions")
    manifest = SkillManifest(
        id="prohibited_fixture_skill",
        name="Prohibited fixture",
        origin=SkillOrigin.BUILT_IN,
        status=SkillStatus.BUILT_IN,
        executable=True,
        implementation_entry="fixture:run",
        required_tools=["fixture_skill_tool"],
    )
    monkeypatch.setattr(SkillsRegistry, "_manifests", {manifest.id: manifest})
    runtime = StateStore(tmp_path / "runtime")
    turn = runtime.create_execution(thread_id="session-1", source="test", status="running")
    record = create_skill_execution(execution_id=turn.id, session_id="session-1", skill_id=manifest.id)

    blocked = activate_skill_execution(record.id, state_store=runtime, allowed_tool_names=set())

    assert blocked.status == SkillExecutionStatus.BLOCKED
    assert blocked.permitted_tool_ids == []
    assert runtime.list_tool_runs(turn.id) == []


def test_composed_skill_records_keep_bounded_parent_child_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import agent.skill_execution as execution_module

    monkeypatch.setattr(execution_module, "_EXEC_DIR", tmp_path / "skill-executions")
    parent = create_skill_execution(
        execution_id="turn-1",
        skill_id="parent-skill",
        child_skill_ids=["child-a", "child-b"],
    )
    child = create_skill_execution(
        execution_id="turn-1",
        skill_id="child-a",
        parent_skill_execution_id=parent.id,
    )
    update_skill_execution(parent.id, child_execution_ids=[child.id])
    loaded = get_skill_execution(parent.id)
    assert loaded.child_skill_ids == ["child-a", "child-b"]
    assert loaded.child_execution_ids == [child.id]


def test_skill_completion_fails_closed_without_verified_tool_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import agent.skill_execution as execution_module

    monkeypatch.setattr(execution_module, "_EXEC_DIR", tmp_path / "skill-executions")
    manifest = SkillManifest(
        id="verified_fixture_skill",
        name="Verified fixture",
        origin=SkillOrigin.BUILT_IN,
        status=SkillStatus.BUILT_IN,
        executable=True,
        implementation_entry="fixture:run",
        required_tools=["fixture_skill_tool"],
        verification_rules=["child_tool_succeeded"],
        completion_criteria=["required_tools_succeeded"],
    )
    monkeypatch.setattr(SkillsRegistry, "_manifests", {manifest.id: manifest})
    runtime = StateStore(tmp_path / "runtime")
    turn = runtime.create_execution(thread_id="session-1", source="test", status="running")
    record = create_skill_execution(execution_id=turn.id, session_id="session-1", skill_id=manifest.id)
    running = activate_skill_execution(record.id, state_store=runtime, allowed_tool_names={"fixture_skill_tool"})
    child = runtime.create_tool_run(
        turn_id=turn.id,
        session_id="session-1",
        run_id="unverified-child",
        tool_name="fixture_skill_tool",
    )
    child = runtime.finish_tool_run(child.id, {"success": True, "status": "complete"})
    record_skill_tool_outcome(runtime, child)

    finalized = finalize_skill_executions_for_turn(turn.id, state_store=runtime, turn_success=True)[0]
    assert finalized.status == SkillExecutionStatus.PARTIAL
    assert finalized.verification["passed"] is False
    assert "child_tool_succeeded" in finalized.verification["unmet_rules"]
    assert finalized.workflow_stage.value == "verifying"
    assert running.permitted_tool_ids == ["fixture_skill_tool"]
