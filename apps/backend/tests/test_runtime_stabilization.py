from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from agent.core import EchoSpeakAgent, Tool
import agent.checkpoints as checkpoint_module
from agent.mode_controller import ModeDecision, TurnMode
from agent.project_notes import update_project_notes
from agent.state import StateStore, ThreadSessionState, ToolOutcome
from agent.tool_registry import PipelinePlugin, PluginRegistry, ToolRegistry
from agent.tools import TOOL_METADATA, get_available_tools
from agent.tools import bind_tool_execution_context, reset_tool_execution_context, update_tool_execution_context
from config import BASE_DIR, DATA_DIR, config


@pytest.fixture(autouse=True)
def _current_action_configuration(monkeypatch):
    token = bind_tool_execution_context({})
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_file_write", True)
    yield
    reset_tool_execution_context(token)


def _agent(tmp_path: Path) -> EchoSpeakAgent:
    import agent.projects as projects_module
    from agent.projects import ProjectManager

    ToolRegistry.register_from_metadata(get_available_tools(), TOOL_METADATA)
    project_root = tmp_path / "project-a"
    project_root.mkdir(parents=True, exist_ok=True)
    manager = ProjectManager(tmp_path / "projects")
    project = manager.attach_folder(str(project_root), name="Project A", trust_state="trusted")
    projects_module._project_manager = manager
    store = StateStore(tmp_path / "phase3")
    context = store.update_thread_state(
        "thread-a",
        workspace_id="coding",
        active_project_id=project.id,
        workspace_root=str(project_root),
        project_path=str(project_root),
        objective="fix project",
        mode="coding",
        phase="implement",
        required_capabilities=["coding"],
        allowed_tool_names=["file_write", "file_read", "calculate", "checkpoint_undo"],
        constraints=["no_delete"],
    )
    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent._state_store = store
    agent._execution_context = context
    agent._current_thread_id = "thread-a"
    agent._current_execution_id = "exec-a"
    agent._workspace_id = "coding"
    agent._active_project_id = project.id
    agent._active_approved_action = None
    agent._active_retry_action = None
    agent._pending_action = None
    agent._last_boundary_outcome = None
    agent._last_boundary_record = None
    agent._boundary_record_in_progress = False
    agent._tool_outcomes_by_run_id = {}
    agent._registered_tool_runs = {}
    agent._current_source = "web"
    agent._current_user_role = "owner"
    agent._active_user_query = "write file"
    agent._current_mode_decision = None
    agent._tool_allowlist_override = None
    agent._coding_loop_note_tool = lambda *_args, **_kwargs: None
    agent.llm_provider = SimpleNamespace(value="test")
    agent.tools = [
        SimpleNamespace(name=name)
        for name in ("file_write", "file_read", "calculate", "checkpoint_undo")
    ]
    agent.lc_tools = []
    update_tool_execution_context(
        thread_id="thread-a",
        project_root=str(project_root),
        workspace_root=str(project_root),
        active_project_id=project.id,
        allowed_tool_names=list(context.allowed_tool_names or []),
    )
    return agent


def _pending(agent: EchoSpeakAgent, **changes):
    kwargs = {"path": str(Path(agent._execution_context.project_path) / "hello.py"), "content": "print('hello')\n"}
    kwargs = agent._canonicalize_tool_arguments("file_write", kwargs)
    digest = hashlib.sha256(json.dumps(kwargs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    risk, flags = agent._approval_risk_metadata("file_write")
    approval = agent._state_store.create_approval(
        thread_id="thread-a",
        session_id="thread-a",
        project_id=str(agent._execution_context.active_project_id or ""),
        execution_id="exec-a",
        tool="file_write",
        kwargs=kwargs,
        workspace_id="coding",
        active_project_id=str(agent._execution_context.active_project_id or ""),
        risk_level=risk,
        policy_flags=flags,
        session_permissions=agent._session_permissions_snapshot(),
        permission_level="modify",
        constraints=["no_delete"],
        required_capabilities=["coding"],
        action_id="action-a",
        plan_id="plan-a",
        canonical_arguments_hash=digest,
        execution_context={
            "thread_id": "thread-a",
            "workspace_id": "coding",
            "active_project_id": str(agent._execution_context.active_project_id or ""),
            "workspace_root": agent._execution_context.workspace_root,
            "project_path": agent._execution_context.project_path,
            "objective": "fix project",
            "constraints": ["no_delete"],
            "tool": "file_write",
            "arguments_hash": digest,
        },
    )
    pending = {
        "approval_id": approval.id,
        "action_id": "action-a",
        "plan_id": "plan-a",
        "tool": "file_write",
        "kwargs": kwargs,
        "execution_context": dict(approval.execution_context),
    }
    pending.update(changes)
    return pending, approval


@pytest.mark.parametrize(
    "mutation",
    ["thread", "project", "workspace", "tool", "path", "content", "action", "plan"],
)
def test_strict_approval_invalidates_every_material_identity_field(tmp_path, mutation):
    agent = _agent(tmp_path)
    pending, _approval = _pending(agent)
    assert agent._pending_action_matches_execution_context(pending) is True
    if mutation == "thread":
        agent._execution_context = agent._execution_context.model_copy(update={"thread_id": "thread-b"})
    elif mutation == "project":
        agent._execution_context = agent._execution_context.model_copy(update={"project_path": str(tmp_path / "other")})
    elif mutation == "workspace":
        agent._execution_context = agent._execution_context.model_copy(update={"workspace_root": str(tmp_path / "other")})
    elif mutation == "tool":
        pending["tool"] = "file_delete"
    elif mutation == "path":
        pending["kwargs"] = {**pending["kwargs"], "path": str(tmp_path / "other.py")}
    elif mutation == "content":
        pending["kwargs"] = {**pending["kwargs"], "content": "changed"}
    elif mutation == "action":
        pending["action_id"] = "other-action"
    elif mutation == "plan":
        pending["plan_id"] = "other-plan"
    assert agent._pending_action_matches_execution_context(pending) is False


def test_settings_change_invalidates_pending_approval(tmp_path, monkeypatch):
    agent = _agent(tmp_path)
    pending, _approval = _pending(agent)
    assert agent._pending_action_matches_execution_context(pending) is True
    monkeypatch.setattr(config, "allow_file_write", not bool(config.allow_file_write))
    assert agent._pending_action_matches_execution_context(pending) is False


def test_current_read_only_constraint_blocks_stable_approval(tmp_path):
    agent = _agent(tmp_path)
    pending, _approval = _pending(agent)
    assert agent._pending_action_matches_execution_context(pending) is True
    agent._execution_context = agent._execution_context.model_copy(
        update={"constraints": ["read_only"]}
    )
    assert agent._pending_action_matches_execution_context(pending) is False


def test_decision_authorized_approval_rechecks_then_executes_once(tmp_path):
    agent = _agent(tmp_path)
    pending, approval = _pending(agent)
    agent._state_store.update_approval(approval.id, status="approved", outcome_summary="Approved")
    assert agent._pending_action_matches_execution_context(pending) is False
    assert agent._pending_action_matches_execution_context({**pending, "_decision_authorized": True}) is True


def test_claimed_approval_is_usable_only_by_the_authorized_consumer(tmp_path):
    agent = _agent(tmp_path)
    pending, approval = _pending(agent)
    assert agent._state_store.claim_pending_approval(approval.id) is not None
    assert agent._pending_action_matches_execution_context(pending) is False
    assert agent._pending_action_matches_execution_context({**pending, "_decision_authorized": True}) is True
    assert agent._state_store.claim_pending_approval(approval.id) is None


def test_approval_required_outcome_is_persisted_and_run_correlated(tmp_path):
    agent = _agent(tmp_path)
    agent._is_action_tool = lambda _name: True
    agent._tool_allowed = lambda _name: True
    agent._action_allowed = lambda *_args, **_kwargs: True
    agent._should_auto_confirm = lambda _name: False
    agent._set_pending_action = lambda pending, *_args: (setattr(agent, "_pending_action", {**pending, "action_id": "action-x"}) or agent._pending_action)
    raw = Tool("file_write", lambda **_kwargs: pytest.fail("write executed before approval"), "write")
    agent._register_tool_run("file_write", "run-approval")
    outcome = agent._invoke_authorized_raw_tool(raw, {"path": str(tmp_path / "never.txt"), "content": "x"})
    assert outcome.status == "approval_required"
    assert outcome.run_id == "run-approval"
    assert agent.get_tool_outcome("run-approval") == outcome
    assert agent._state_store.get_thread_state("thread-a").last_tool_outcome["status"] == "approval_required"
    assert not (tmp_path / "never.txt").exists()


def test_two_same_named_tool_calls_keep_distinct_run_outcomes(tmp_path):
    agent = _agent(tmp_path)
    agent._is_action_tool = lambda _name: False
    agent._tool_allowed = lambda _name: True
    raw = Tool("calculate", lambda expression: str(eval(expression, {"__builtins__": {}}, {})), "math")
    agent._register_tool_run("calculate", "run-one")
    first = agent._invoke_authorized_raw_tool(raw, {"expression": "1+1"})
    agent._register_tool_run("calculate", "run-two")
    second = agent._invoke_authorized_raw_tool(raw, {"expression": "2+2"})
    assert (first.run_id, first.output) == ("run-one", "2")
    assert (second.run_id, second.output) == ("run-two", "4")
    assert agent.get_tool_outcome("run-one").output == "2"
    assert agent.get_tool_outcome("run-two").output == "4"


def test_undo_runs_through_authority_outcome_verification_and_ledger(tmp_path, monkeypatch):
    project = tmp_path / "project-a"
    project.mkdir()
    target = project / "state.txt"
    target.write_text("before", encoding="utf-8")
    monkeypatch.setattr(checkpoint_module, "CHECKPOINTS_DIR", tmp_path / "checkpoints")
    token = bind_tool_execution_context({
        "thread_id": "thread-a",
        "project_root": str(project),
        "workspace_root": str(project),
        "allowed_tool_names": ["checkpoint_undo"],
    })
    try:
        checkpoint_module.create_checkpoint(str(target))
        target.write_text("after", encoding="utf-8")
        agent = _agent(tmp_path)
        agent._execution_context = agent._execution_context.model_copy(update={"project_path": str(project), "workspace_root": str(project)})
        agent._tool_allowed = lambda _name: True
        agent._is_action_tool = lambda _name: True
        agent._action_allowed = lambda *_args, **_kwargs: True
        agent._approved_action_matches = lambda *_args, **_kwargs: True
        precondition = agent._capture_source_precondition("checkpoint_undo", {})
        approval = agent._state_store.create_approval(
            thread_id="thread-a",
            session_id="thread-a",
            project_id=str(agent._execution_context.active_project_id or ""),
            active_project_id=str(agent._execution_context.active_project_id or ""),
            tool="checkpoint_undo",
            kwargs={},
            action_id="undo-action",
            plan_id="undo-plan",
            canonical_arguments_hash=hashlib.sha256(b"{}").hexdigest(),
            source_precondition=precondition,
        )
        agent._active_approved_action = {
            "tool": "checkpoint_undo",
            "action_id": "undo-action",
            "approval_id": approval.id,
        }
        raw = ToolRegistry.get("checkpoint_undo").func
        agent._register_tool_run("checkpoint_undo", "undo-run")
        outcome = agent._invoke_authorized_raw_tool(raw, {})
        assert outcome.success is True
        assert outcome.verification == {"checkpoint_restored": True}
        assert target.read_text(encoding="utf-8") == "before"
        ledger = agent._state_store.get_thread_state("thread-a").ledger
        assert ledger[-1].tool == "checkpoint_undo"
        assert ledger[-1].verified is True
    finally:
        reset_tool_execution_context(token)


def test_new_objective_supersedes_approval_and_retry(tmp_path):
    agent = _agent(tmp_path)
    pending, approval = _pending(agent)
    agent._pending_action = pending
    agent._state_store.update_thread_state("thread-a", retry_target={"tool": "file_read"})
    decision = ModeDecision(
        mode=TurnMode.TASK_RESEARCH,
        confidence=1.0,
        reason="new research",
        user_text="research another subject",
        intent_relation="new_objective",
    )
    agent._supersede_stale_pending_action(decision)
    state = agent._state_store.get_thread_state("thread-a")
    assert agent._state_store.get_approval(approval.id).status == "canceled"
    assert state.pending_approval_id == ""
    assert state.retry_target == {}


def _retry_target(agent: EchoSpeakAgent, tool: str, kwargs: dict, *, action: bool = False) -> dict:
    canonical_kwargs = agent._canonicalize_tool_arguments(tool, kwargs)
    arguments_hash = hashlib.sha256(
        json.dumps(canonical_kwargs, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    run_id = f"original-run-{tool}"
    agent._state_store.create_tool_run(
        turn_id="original-exec",
        tool_name=tool,
        session_id="thread-a",
        project_id=str(agent._execution_context.active_project_id or ""),
        run_id=run_id,
        canonical_arguments=canonical_kwargs,
        canonical_arguments_hash=arguments_hash,
        action_id="original-action",
    )
    return {
        "thread_id": "thread-a",
        "execution_id": "original-exec",
        "tool_run_id": run_id,
        "action_id": "original-action",
        "project_path": agent._execution_context.project_path,
        "workspace_root": agent._execution_context.workspace_root,
        "workspace_id": agent._execution_context.workspace_id,
        "active_project_id": agent._execution_context.active_project_id,
        "objective": agent._execution_context.objective,
        "tool": tool,
        "kwargs": canonical_kwargs,
        "arguments_hash": arguments_hash,
        "constraints": list(agent._execution_context.constraints),
        "permissions": agent._session_permissions_snapshot(),
        "policy_flags": list(agent._approval_risk_metadata(tool)[1]),
        "failure_reason": "temporary failure",
        "failure_status": "tool_failure",
        "retryable": True,
        "approval_valid": False,
        "partial_side_effect_possible": action,
    }


def test_exact_read_retry_uses_same_tool_and_arguments(tmp_path):
    agent = _agent(tmp_path)
    kwargs = {"path": "README.md"}
    agent._state_store.update_thread_state("thread-a", retry_target=_retry_target(agent, "file_read", kwargs))
    seen = []
    tool = SimpleNamespace(
        name="file_read",
        invoke_outcome=lambda **actual: (seen.append(actual) or ToolOutcome(tool_name="file_read", success=True, status="success", output="ok")),
    )
    agent.tools = [tool]
    agent._is_action_tool = lambda _name: False
    agent._emit_tool_start = lambda *_args, **_kwargs: None
    agent._emit_tool_end = lambda *_args, **_kwargs: None
    response, success = agent._retry_last_action()
    assert success is True
    assert response == "ok"
    assert seen == [agent._canonicalize_tool_arguments("file_read", kwargs)]


def test_modifying_retry_requires_fresh_exact_approval(tmp_path):
    agent = _agent(tmp_path)
    kwargs = {"path": str(tmp_path / "project-a" / "x.txt"), "content": "x"}
    agent._state_store.update_thread_state("thread-a", retry_target=_retry_target(agent, "file_write", kwargs, action=True))
    agent.tools = [SimpleNamespace(name="file_write")]
    agent._is_action_tool = lambda _name: True
    agent._action_configured = lambda _name: True
    response, success = agent._retry_last_action()
    assert success is True
    assert "approve this exact retry" in response
    assert agent._pending_action["tool"] == "file_write"
    assert agent._pending_action["action_id"]


def test_retry_rejects_project_or_argument_change(tmp_path):
    agent = _agent(tmp_path)
    kwargs = {"path": "README.md"}
    target = _retry_target(agent, "file_read", kwargs)
    agent._state_store.update_thread_state("thread-a", retry_target=target, project_path=str(tmp_path / "other"))
    response, success = agent._retry_last_action()
    assert success is False and "project scope changed" in response
    agent._state_store.update_thread_state("thread-a", project_path=target["project_path"], retry_target={**target, "kwargs": {"path": "OTHER.md"}})
    response, success = agent._retry_last_action()
    assert success is False and "arguments changed" in response


def test_mutating_plugin_hooks_are_rejected():
    class MutatingPlugin(PipelinePlugin):
        name = "mutating-test-plugin"
        mutates_external_state = True

    with pytest.raises(ValueError, match="action tool"):
        PluginRegistry.register(MutatingPlugin())


def test_project_notes_ignore_unrelated_conversation(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    state = SimpleNamespace(
        objective="Fix rendering",
        goal="Wrong fallback",
        phase="implement",
        next_step="Run verification",
        files_known=["index.html"],
        last_user_message="okay goodnight lol",
        last_verified_action="Inspected index.html",
    )
    update_project_notes(str(project), state)
    text = (project / "PROJECT_NOTES.md").read_text(encoding="utf-8")
    assert "Fix rendering" in text
    assert "Inspected index.html" in text
    assert "goodnight" not in text


def test_pytest_data_root_is_not_production_state():
    assert os.getenv("ECHOSPEAK_TESTING") == "1"
    assert DATA_DIR.resolve() != (BASE_DIR / "data").resolve()


def test_soul_cache_invalidates_after_mtime_change(tmp_path, monkeypatch):
    soul = tmp_path / "SOUL.md"
    soul.write_text("first", encoding="utf-8")
    monkeypatch.setattr(config.soul, "path", str(soul))
    monkeypatch.setattr(config.soul, "enabled", True)
    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent._soul_cache = {"path": "", "mtime_ns": -1, "max_chars": 0, "content": ""}
    assert agent._load_soul() == "first"
    soul.write_text("second", encoding="utf-8")
    stat = soul.stat()
    os.utime(soul, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert agent._load_soul() == "second"
