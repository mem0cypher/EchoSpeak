from __future__ import annotations

from agent.automation_projection import project_execution, project_task_run
from agent.research_runtime import CompletionDisposition, CompletionVerdict
from agent.task_runs import TaskRun, TaskRunStatus, TaskRunStore


def _verdict(*, finalizable: bool) -> CompletionVerdict:
    return CompletionVerdict(
        disposition=CompletionDisposition.COMPLETE if finalizable else CompletionDisposition.PENDING,
        finalizable=finalizable,
        reason_code="all_satisfied" if finalizable else "requirements_pending",
    )


def test_automation_completion_is_projected_from_taskrun_verdict() -> None:
    complete = TaskRun(
        id="task-1",
        session_id="session-1",
        objective="Run routine",
        status=TaskRunStatus.COMPLETED,
        completion_evaluation=_verdict(finalizable=True),
        last_execution_id="execution-1",
        source="routine",
    )
    projection = project_task_run(complete)
    assert projection.verified is True
    assert projection.product_task_status == "complete"
    assert projection.automation_status == "completed"


def test_completed_label_without_finalizable_verdict_fails_closed() -> None:
    malformed = TaskRun(
        id="task-1",
        session_id="session-1",
        objective="Run routine",
        status=TaskRunStatus.COMPLETED,
        completion_evaluation=_verdict(finalizable=False),
        last_execution_id="execution-1",
        source="routine",
    )
    projection = project_task_run(malformed)
    assert projection.verified is False
    assert projection.product_task_status == "failed"
    assert projection.automation_status == "failed"


def test_projection_finds_exact_execution_in_scope(tmp_path) -> None:
    store = TaskRunStore(tmp_path / "task-runs.json")
    task = store.create(
        id="task-1",
        project_id="project-1",
        session_id="session-1",
        objective="Background work",
        last_execution_id="execution-1",
        source="heartbeat",
    )
    projection = project_execution(
        project_id="project-1",
        session_id="session-1",
        execution_id="execution-1",
        store=store,
    )
    assert projection is not None
    assert projection.task_run_id == task.id
    assert project_execution(
        project_id="project-1",
        session_id="session-1",
        execution_id="other-execution",
        store=store,
    ) is None


def test_non_terminal_taskrun_projects_to_a_releasable_automation_lease() -> None:
    running = TaskRun(
        id="task-1",
        session_id="session-1",
        objective="Continue bounded work",
        status=TaskRunStatus.RUNNING,
        last_execution_id="execution-1",
        source="routine",
    )
    projection = project_task_run(running)
    assert projection.product_task_status == "in_progress"
    assert projection.automation_status == "paused"
    assert projection.verified is False
