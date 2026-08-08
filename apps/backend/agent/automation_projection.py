"""Read-only projections from canonical TaskRun state into automation history.

AutomationRun coordinates idempotency, claims, and leases. ProductTask is a
user-facing record. Neither may decide whether work completed. This module is
the only compatibility boundary that projects the canonical TaskRun verdict
into those historical surfaces.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from agent.task_runs import TaskRun, TaskRunStatus, TaskRunStore, get_task_run_store


class AutomationTaskProjection(BaseModel):
    task_run_id: str
    execution_id: str
    canonical_status: TaskRunStatus
    product_task_status: str
    automation_status: str
    finalizable: bool = False
    completion_disposition: str = "pending"
    tool_run_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    verified: bool = False
    reason_code: str = ""


def find_task_run_for_execution(
    *,
    project_id: str,
    session_id: str,
    execution_id: str,
    store: Optional[TaskRunStore] = None,
) -> Optional[TaskRun]:
    """Find the one TaskRun owned by a background Turn's Execution."""

    if not execution_id:
        return None
    return (store or get_task_run_store()).find_for_execution(
        session_id=session_id,
        project_id=project_id,
        execution_id=execution_id,
    )


def project_task_run(task: TaskRun) -> AutomationTaskProjection:
    """Map TaskRun truth to compatibility status strings without re-evaluation."""

    verdict = task.completion_evaluation
    finalizable = bool(verdict and verdict.finalizable)
    disposition = str(getattr(getattr(verdict, "disposition", None), "value", "") or "pending")
    reason_code = str(getattr(verdict, "reason_code", "") or task.workflow_stage or task.status.value)

    if task.status == TaskRunStatus.COMPLETED and finalizable:
        product_status = "complete"
        automation_status = "completed"
        verified = True
    elif task.status == TaskRunStatus.SUSPENDED_WAITING_FOR_APPROVAL:
        product_status = "needs_permission"
        automation_status = "waiting_for_approval"
        verified = False
    elif task.status in {
        TaskRunStatus.SUSPENDED_WAITING_FOR_USER,
        TaskRunStatus.SUSPENDED_WAITING_FOR_EXTERNAL_RESULT,
        TaskRunStatus.LEGACY_UNTRUSTED,
        TaskRunStatus.BLOCKED_POLICY,
        TaskRunStatus.RUNTIME_AUTHORITY_CONFLICT,
        TaskRunStatus.COMPLETION_PROJECTION_CONFLICT,
    }:
        # Distinct from model-output failure: runtime state blocked a legal action.
        if task.status == TaskRunStatus.RUNTIME_AUTHORITY_CONFLICT:
            product_status = "runtime_authority_conflict"
            automation_status = "runtime_authority_conflict"
        elif task.status == TaskRunStatus.COMPLETION_PROJECTION_CONFLICT:
            product_status = "completion_projection_conflict"
            automation_status = "completion_projection_conflict"
        else:
            product_status = "blocked"
            automation_status = "blocked"
        verified = False
    elif task.status == TaskRunStatus.CANCELLED:
        product_status = "cancelled"
        automation_status = "cancelled"
        verified = False
    elif task.status in {
        TaskRunStatus.FAILED,
        TaskRunStatus.FAILED_INTERPRETATION,
        TaskRunStatus.FAILED_MODEL_OUTPUT,
        TaskRunStatus.FAILED_TOOL_PARSE,
        TaskRunStatus.FAILED_PROVIDER,
    } or task.status == TaskRunStatus.COMPLETED:
        # A completed status without a finalizable canonical verdict is invalid
        # for compatibility consumers and remains a visible failure.
        product_status = "failed"
        automation_status = "failed"
        verified = False
    else:
        product_status = "in_progress"
        # The synchronous trigger callback has returned. A non-terminal
        # canonical TaskRun remains resumable work, but its compatibility lease
        # record must not be left running without a coordinator.
        automation_status = "paused"
        verified = False

    return AutomationTaskProjection(
        task_run_id=task.id,
        execution_id=task.last_execution_id or task.created_by_execution_id,
        canonical_status=task.status,
        product_task_status=product_status,
        automation_status=automation_status,
        finalizable=finalizable,
        completion_disposition=disposition,
        tool_run_ids=list(task.tool_run_ids),
        artifact_ids=list(task.research_artifact_ids),
        verified=verified,
        reason_code=reason_code,
    )


def project_execution(
    *,
    project_id: str,
    session_id: str,
    execution_id: str,
    store: Optional[TaskRunStore] = None,
    occurrence_id: str = "",
) -> Optional[AutomationTaskProjection]:
    owner = store or get_task_run_store()
    task = find_task_run_for_execution(
        project_id=project_id,
        session_id=session_id,
        execution_id=execution_id,
        store=owner,
    )
    occurrence = str(occurrence_id or "").strip()
    if task is not None and occurrence:
        if task.trigger_occurrence_id and task.trigger_occurrence_id != occurrence:
            raise RuntimeError("TaskRun is already bound to another automation occurrence")
        if not task.trigger_occurrence_id:
            task = owner.update(
                task.id,
                session_id=task.session_id,
                project_id=task.project_id,
                expected_revision=task.revision,
                trigger_occurrence_id=occurrence,
            )
    return project_task_run(task) if task is not None else None
