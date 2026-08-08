from __future__ import annotations

import pytest
from types import SimpleNamespace

from agent.research_runtime import RequirementState, RequirementStatus, TurnRequirement
from agent.semantic_runtime import CanonicalSemanticRuntime
from agent.state import StateStore
from agent.task_runs import TaskRunStatus, TaskRunStore
from agent.turn_understanding import ApprovalDecision


def test_session_model_binding_is_scoped_and_revision_checked(tmp_path) -> None:
    store = StateStore(tmp_path / "state")
    first = store.ensure_session_model_binding(
        "session-a", provider_id="lmstudio", model_id="model-a"
    )
    other = store.ensure_session_model_binding(
        "session-b", provider_id="openai", model_id="model-b"
    )

    changed = store.update_session_model_binding(
        "session-a",
        provider_id="openai",
        model_id="model-c",
        expected_revision=first.binding_revision,
    )

    assert changed.binding_revision == first.binding_revision + 1
    assert store.get_thread_state("session-b").model_binding == other
    with pytest.raises(RuntimeError, match="binding changed"):
        store.update_session_model_binding(
            "session-a",
            provider_id="lmstudio",
            model_id="stale",
            expected_revision=first.binding_revision,
        )


def test_approval_resume_requires_exact_requirement_attempt_and_revision(tmp_path) -> None:
    store = TaskRunStore(tmp_path / "task-runs.json")
    requirement = TurnRequirement(
        requirement_id="req-preview",
        objective="Start the Project preview",
    )
    state = RequirementState(
        requirement_id=requirement.requirement_id,
        status=RequirementStatus.ACTIVE,
        attempt_ids=["attempt-preview"],
    )
    task = store.create(
        id="task-preview",
        project_id="project-a",
        session_id="session-a",
        objective=requirement.objective,
        requirements=[requirement],
        requirement_states={requirement.requirement_id: state},
        status=TaskRunStatus.SUSPENDED_WAITING_FOR_APPROVAL,
        last_execution_id="execution-origin",
    )

    resumed = store.resume_for_approval(
        task.id,
        session_id=task.session_id,
        project_id=task.project_id,
        expected_revision=task.revision,
        execution_id="execution-confirm",
        requirement_id=requirement.requirement_id,
        attempt_id="attempt-preview",
    )
    assert resumed.status == TaskRunStatus.RUNNING
    assert resumed.last_execution_id == "execution-confirm"

    with pytest.raises(Exception):
        store.resume_for_approval(
            task.id,
            session_id=task.session_id,
            project_id=task.project_id,
            expected_revision=task.revision,
            execution_id="execution-stale",
            requirement_id=requirement.requirement_id,
            attempt_id="attempt-other",
        )


def test_project_scope_preview_cleanup_records_a_verified_toolrun(tmp_path, monkeypatch) -> None:
    import agent.project_preview as workspace

    class Preview:
        running = True

        def status(self, _session_id: str):
            return {"running": self.running, "project_root": "C:/old-project"}

        def stop(self, _session_id: str):
            self.running = False
            return {"ok": True, "stopped": True}

    store = StateStore(tmp_path / "state")
    store.ensure_session_model_binding(
        "session-a", provider_id="lmstudio", model_id="model-a"
    )
    monkeypatch.setattr(workspace, "_PREVIEW_MANAGER", Preview())

    result = workspace.stop_preview_for_scope_change(
        "session-a",
        reason="Project detached",
        detached_project_id="project-a",
        state_store=store,
    )

    run = store.get_tool_run(result["tool_run_id"])
    assert run is not None
    assert run.tool_name == "code_preview_stop"
    assert run.project_id == "project-a"
    assert run.verification["verified"] is True
    execution = store.get_execution(result["execution_id"])
    assert execution is not None and execution.status == "completed"


def test_interpreted_approval_uses_the_same_exact_consumption_handler() -> None:
    runtime = object.__new__(CanonicalSemanticRuntime)
    calls: list[tuple[str, str]] = []
    runtime._handle_exact_control = lambda agent, text, callbacks: (
        calls.append((agent._requested_approval_id, text)) or ("approved", True)
    )
    approval = SimpleNamespace(id="approval-a", status="pending")
    agent = SimpleNamespace(
        _requested_approval_id=None,
        _state_store=SimpleNamespace(get_approval=lambda _approval_id: approval),
    )
    interpretation = SimpleNamespace(
        selected_approval_id=approval.id,
        approval_decision=ApprovalDecision.APPROVE,
    )

    result = runtime._apply_interpreted_approval(agent, interpretation, [])

    assert result == ("approved", True)
    assert calls == [(approval.id, "confirm")]
