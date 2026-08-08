"""Canonical durable semantic TaskRun owner for EchoSpeak 8.0.

TaskRuns are distinct from Product Tasks.  A Product Task is user-visible work;
a TaskRun is the semantic execution state that may span multiple Turns.  Session
state stores only references to TaskRuns and never owns their objectives or
missing inputs.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import threading
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field, model_validator

from config import DATA_DIR
from agent.execution_graph import (
    ExecutionProfile,
    GraphSource,
    TaskGraph,
    TaskGraphState,
    GraphTransition,
    apply_graph_transition,
    build_task_graph,
    checkpoint_graph_state,
    execution_profile_for,
    reconcile_graph_state,
)
from agent.research_runtime import (
    CapabilitySnapshot,
    CompletionVerdict,
    RequirementState,
    ResearchBudgetPolicy,
    ResearchDepth,
    TaskRunAdvanceDecision,
    TurnRequirement,
    budget_for_depth,
    choose_active_requirement,
    compile_turn_requirements,
    reconcile_requirement_states,
    select_research_depth,
)


class TaskRunStatus(str, Enum):
    RUNNING = "running"
    SUSPENDED_WAITING_FOR_USER = "suspended_waiting_for_user"
    SUSPENDED_WAITING_FOR_APPROVAL = "suspended_waiting_for_approval"
    SUSPENDED_WAITING_FOR_EXTERNAL_RESULT = "suspended_waiting_for_external_result"
    BACKGROUND = "background"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    FAILED_INTERPRETATION = "failed_interpretation"
    FAILED_MODEL_OUTPUT = "failed_model_output"
    FAILED_TOOL_PARSE = "failed_tool_parse"
    FAILED_PROVIDER = "failed_provider"
    BLOCKED_POLICY = "blocked_policy"
    # Valid model action rejected because TaskRun/evaluator/graph projection was contradictory.
    RUNTIME_AUTHORITY_CONFLICT = "runtime_authority_conflict"
    # TaskRun ledger and completion evaluator disagree about required work.
    COMPLETION_PROJECTION_CONFLICT = "completion_projection_conflict"
    QUARANTINED = "quarantined"
    LEGACY_UNTRUSTED = "legacy_untrusted"


SUSPENDED_STATUSES = frozenset({
    TaskRunStatus.SUSPENDED_WAITING_FOR_USER,
    TaskRunStatus.SUSPENDED_WAITING_FOR_APPROVAL,
    TaskRunStatus.SUSPENDED_WAITING_FOR_EXTERNAL_RESULT,
    # These statuses preserve the failed attempt while keeping the owning
    # objective resumable.  They are serialized legacy names, not terminal
    # lifecycle decisions.
    TaskRunStatus.FAILED_MODEL_OUTPUT,
    TaskRunStatus.FAILED_TOOL_PARSE,
    TaskRunStatus.FAILED_PROVIDER,
    TaskRunStatus.LEGACY_UNTRUSTED,
})

TERMINAL_TASK_STATUSES = frozenset({
    TaskRunStatus.COMPLETED,
    TaskRunStatus.CANCELLED,
    TaskRunStatus.SUPERSEDED,
    TaskRunStatus.FAILED,
    TaskRunStatus.FAILED_INTERPRETATION,
    TaskRunStatus.BLOCKED_POLICY,
    TaskRunStatus.RUNTIME_AUTHORITY_CONFLICT,
    TaskRunStatus.COMPLETION_PROJECTION_CONFLICT,
    TaskRunStatus.QUARANTINED,
})

RECOVERABLE_TASK_STATUSES = frozenset({
    TaskRunStatus.FAILED_PROVIDER,
    TaskRunStatus.FAILED_MODEL_OUTPUT,
    TaskRunStatus.FAILED_TOOL_PARSE,
})


class TaskInputOwner(str, Enum):
    USER = "user"
    RUNTIME = "runtime"


class TaskInputGap(BaseModel):
    field: str
    owner: TaskInputOwner = TaskInputOwner.USER
    requirement_id: str = ""
    reason: str = ""
    blocking: bool = True

    @model_validator(mode="after")
    def normalize(self) -> "TaskInputGap":
        self.field = str(self.field or "").strip()[:240]
        if not self.field:
            raise ValueError("TaskInputGap field is required")
        self.requirement_id = str(self.requirement_id or "").strip()[:120]
        self.reason = str(self.reason or "").strip()[:500]
        if self.owner == TaskInputOwner.RUNTIME:
            self.blocking = False
        return self


class RequirementHistoryEntry(BaseModel):
    archived_at: float = Field(default_factory=time.time)
    reason: str
    task_revision: int = 0
    requirements: list[TurnRequirement] = Field(default_factory=list)
    requirement_states: dict[str, RequirementState] = Field(default_factory=dict)


class TaskRunContinuationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskRunContinuation(BaseModel):
    """Runtime-owned continuation receipt for an asynchronous TaskRun result."""

    trigger_kind: str = "specialist_outcome"
    trigger_id: str
    specialist_run_id: str = ""
    status: TaskRunContinuationStatus = TaskRunContinuationStatus.PENDING
    execution_id: str = ""
    response: str = ""
    error: str = ""
    requested_at: float = Field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0
    updated_at: float = Field(default_factory=time.time)

    @model_validator(mode="after")
    def require_trigger(self) -> "TaskRunContinuation":
        self.trigger_kind = str(self.trigger_kind or "").strip()[:80]
        self.trigger_id = str(self.trigger_id or "").strip()[:160]
        self.specialist_run_id = str(self.specialist_run_id or "").strip()[:160]
        self.execution_id = str(self.execution_id or "").strip()[:160]
        self.response = str(self.response or "")[:32000]
        self.error = str(self.error or "")[:2000]
        if not self.trigger_kind or not self.trigger_id:
            raise ValueError("TaskRun continuation trigger identity is required")
        return self


_INFORMATIONAL_CAPABILITIES = frozenset({
    "conversation", "research", "live_weather", "live_sports", "time", "calculate", "memory",
})
_EXPLICIT_USER_INPUT_MARKERS = frozenset({
    "confirmation", "memory_confirmation", "recipient", "email_address", "phone_number",
    "account", "credential", "password", "path", "file_path", "project_path", "choice",
    "preference", "content", "message_body",
})


def classify_task_input_gaps(
    fields: list[str],
    capabilities: list[str],
    *,
    objective: str = "",
    requirements: Optional[list[TurnRequirement]] = None,
) -> list[TaskInputGap]:
    """Separate genuine user inputs from information the runtime can acquire."""

    normalized = list(dict.fromkeys(
        str(item).strip() for item in fields if str(item).strip()
    ))
    caps = {str(item).strip() for item in capabilities if str(item).strip()}
    informational = bool(caps) and caps.issubset(_INFORMATIONAL_CAPABILITIES)
    requirement_rows = list(requirements or [])
    known_locations = {
        str(item.location or "").strip().casefold() for item in requirement_rows if item.location
    }
    objective_low = str(objective or "").casefold()
    gaps: list[TaskInputGap] = []
    for field_name in normalized:
        canonical = re.sub(r"[^a-z0-9]+", "_", field_name.casefold()).strip("_")
        owning_requirement_id = next(
            (
                item.requirement_id
                for item in requirement_rows
                if canonical in {
                    re.sub(r"[^a-z0-9]+", "_", str(field).casefold()).strip("_")
                    for field in item.requested_fields
                }
            ),
            "",
        )
        marker_match = canonical in _EXPLICIT_USER_INPUT_MARKERS or any(
            canonical.endswith("_" + marker) for marker in _EXPLICIT_USER_INPUT_MARKERS
        )
        location_required = bool(
            informational
            and "live_weather" in caps
            and canonical in {"location", "city", "weather_location"}
            and not known_locations
            and not re.search(r"\b(?:in|for|at)\s+[a-z][a-z .'-]{1,80}\b", objective_low)
        )
        user_owned = bool(not informational or marker_match or location_required)
        gaps.append(TaskInputGap(
            field=field_name,
            owner=TaskInputOwner.USER if user_owned else TaskInputOwner.RUNTIME,
            requirement_id=owning_requirement_id,
            reason=(
                "required_user_supplied_action_input"
                if user_owned else "runtime_discoverable_information_gap"
            ),
            blocking=user_owned,
        ))
    return gaps


class TaskRun(BaseModel):
    schema_version: int = 6
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""
    session_id: str
    objective: str
    requested_operation: str = ""
    collected_inputs: dict[str, Any] = Field(default_factory=dict)
    missing_inputs: list[str] = Field(default_factory=list)
    input_gaps: list[TaskInputGap] = Field(default_factory=list)
    selected_skill_id: str = ""
    selected_skill_version: str = ""
    workflow_stage: str = "understanding"
    plan: list[dict[str, Any]] = Field(default_factory=list)
    completion_requirements: list[str] = Field(default_factory=list)
    requirements: list[TurnRequirement] = Field(default_factory=list)
    requirement_states: dict[str, RequirementState] = Field(default_factory=dict)
    requirement_history: list[RequirementHistoryEntry] = Field(default_factory=list)
    research_depth: Optional[ResearchDepth] = None
    research_budget: Optional[ResearchBudgetPolicy] = None
    research_started_at: float = 0.0
    research_artifact_ids: list[str] = Field(default_factory=list)
    capability_snapshot: Optional[CapabilitySnapshot] = None
    completion_evaluation: Optional[CompletionVerdict] = None
    liveness_decision: Optional[TaskRunAdvanceDecision] = None
    shadow_completion_evaluation: dict[str, Any] = Field(default_factory=dict)
    recovery_epoch: int = Field(default=0, ge=0)
    recovery_epoch_started_at: float = 0.0
    recovery_history: list[dict[str, Any]] = Field(default_factory=list)
    execution_profile: Optional[ExecutionProfile] = None
    parent_task_run_id: str = ""
    handoff_context_id: str = ""
    trigger_occurrence_id: str = ""
    execution_graph: Optional[TaskGraph] = None
    execution_graph_state: Optional[TaskGraphState] = None
    model_binding_events: list[dict[str, Any]] = Field(default_factory=list)
    permitted_capabilities: list[str] = Field(default_factory=list)
    tool_run_ids: list[str] = Field(default_factory=list)
    specialist_run_ids: list[str] = Field(default_factory=list)
    continuation: Optional[TaskRunContinuation] = None
    verified_tool_outcomes: list[dict[str, Any]] = Field(default_factory=list)
    steering_instructions: list[str] = Field(default_factory=list)
    retry_identity: dict[str, Any] = Field(default_factory=dict)
    status: TaskRunStatus = TaskRunStatus.RUNNING
    revision: int = 1
    created_by_execution_id: str = ""
    last_execution_id: str = ""
    source: str = "web"
    legacy_provenance: dict[str, Any] = Field(default_factory=dict)
    quarantine_diagnostics: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    @model_validator(mode="before")
    @classmethod
    def reject_future_schema(cls, value: Any) -> Any:
        if isinstance(value, dict) and int(value.get("schema_version") or 1) > 6:
            raise ValueError("unsupported future TaskRun schema version")
        return value

    @model_validator(mode="after")
    def require_scope_and_objective(self) -> "TaskRun":
        if not self.session_id.strip():
            raise ValueError("TaskRun session_id is required")
        if not self.objective.strip():
            raise ValueError("TaskRun objective is required")
        self.objective = self.objective.strip()[:4000]
        self.requested_operation = self.requested_operation.strip()[:500]
        raw_missing_inputs = list(dict.fromkeys(
            str(item).strip() for item in self.missing_inputs if str(item).strip()
        ))
        self.permitted_capabilities = list(dict.fromkeys(
            str(item).strip() for item in self.permitted_capabilities if str(item).strip()
        ))
        self.specialist_run_ids = list(dict.fromkeys(
            str(item).strip() for item in self.specialist_run_ids if str(item).strip()
        ))[-128:]
        self.requirements = compile_turn_requirements(
            self.requirements,
            objective=self.objective,
            capabilities=self.permitted_capabilities,
            requested_operation=self.requested_operation,
            missing_fields=raw_missing_inputs,
        )
        existing_gaps = [
            item if isinstance(item, TaskInputGap) else TaskInputGap.model_validate(item)
            for item in self.input_gaps
        ]
        existing_fields = {item.field for item in existing_gaps}
        existing_gaps.extend(classify_task_input_gaps(
            [item for item in raw_missing_inputs if item not in existing_fields],
            self.permitted_capabilities,
            objective=self.objective,
            requirements=self.requirements,
        ))
        self.input_gaps = list({item.field: item for item in existing_gaps}.values())[:64]
        self.missing_inputs = [
            item.field for item in self.input_gaps
            if item.owner == TaskInputOwner.USER and item.blocking
        ]
        self.requirement_states = reconcile_requirement_states(
            self.requirements, self.requirement_states
        )
        requirement_ids = {item.requirement_id for item in self.requirements}
        if (
            self.liveness_decision is not None
            and (
                self.liveness_decision.active_requirement_id
                and self.liveness_decision.active_requirement_id not in requirement_ids
                or not set(self.liveness_decision.requirement_states).issubset(
                    requirement_ids
                )
            )
        ):
            self.liveness_decision = None
        selected_depth = self.research_depth or select_research_depth(
            self.requirements, self.objective
        )
        self.research_depth = selected_depth
        self.research_budget = self.research_budget or budget_for_depth(selected_depth)
        self.research_artifact_ids = list(dict.fromkeys(
            str(item).strip() for item in self.research_artifact_ids if str(item).strip()
        ))
        self.parent_task_run_id = str(self.parent_task_run_id or "").strip()[:100]
        self.handoff_context_id = str(self.handoff_context_id or "").strip()[:100]
        self.trigger_occurrence_id = str(self.trigger_occurrence_id or "").strip()[:100]
        self.model_binding_events = [
            dict(item) for item in list(self.model_binding_events or [])[-64:] if isinstance(item, dict)
        ]
        self.requirement_history = list(self.requirement_history or [])[-64:]
        self.recovery_history = [
            dict(item) for item in list(self.recovery_history or [])[-64:]
            if isinstance(item, dict)
        ]
        self.quarantine_diagnostics = dict(self.quarantine_diagnostics or {})
        self.execution_profile = self.execution_profile or execution_profile_for(
            self.permitted_capabilities,
            source=self.source,
            requested_operation=self.requested_operation,
        )
        if self.execution_graph is None:
            self.execution_graph = build_task_graph(
                task_run_id=self.id,
                requirements=self.requirements,
                budget=self.research_budget,
            )
        elif (
            self.execution_graph.source == GraphSource.COMPATIBILITY
            or self.execution_graph.compatibility_revision < 2
            or {
                item.requirement_id
                for item in self.execution_graph.nodes
                if item.requirement_id
            } != {item.requirement_id for item in self.requirements}
        ):
            self.execution_graph = build_task_graph(
                task_run_id=self.id,
                requirements=self.requirements,
                budget=self.research_budget,
            )
        self.execution_graph_state = reconcile_graph_state(
            self.execution_graph,
            self.execution_graph_state,
            requirement_states=self.requirement_states,
            completion=self.completion_evaluation,
            task_status=self.status.value,
        )
        graph_node_ids = {item.node_id for item in self.execution_graph.nodes}
        if set(self.execution_graph_state.node_states) != graph_node_ids:
            raise ValueError("TaskRun graph state must cover every graph node exactly once")
        if not set(self.execution_graph_state.active_node_ids).issubset(graph_node_ids):
            raise ValueError("TaskRun graph state references an unknown active node")
        if self.execution_graph_state.transition_count > self.execution_graph.budget.max_transitions:
            raise ValueError("TaskRun graph transition budget exceeded")
        self.schema_version = 6
        return self

    def suspended(self) -> bool:
        return self.status in SUSPENDED_STATUSES

    def unresolved_wait_fields(self) -> list[str]:
        collected = set(str(key) for key in (self.collected_inputs or {}))
        supplied = set(collected)
        if self.requested_operation:
            supplied.add("requested_operation")
        if self.objective:
            supplied.update({"objective", "proposed_objective"})
        return [item for item in self.missing_inputs if item not in supplied]

    def steer(self, instruction: str) -> None:
        """Inject a steering instruction into the active TaskRun.

        The instruction is consumed at the next canonical model-envelope boundary.
        It does not rewrite the objective or requirement ledger, preserving
        completed work, verified evidence, prior ToolRuns, and recovery history.
        """
        text = str(instruction or "").strip()
        if not text:
            return
        if text not in self.steering_instructions:
            self.steering_instructions.append(text[:10000])
        self.revision += 1
        self.updated_at = time.time()
        if self.status in {TaskRunStatus.SUSPENDED_WAITING_FOR_USER, TaskRunStatus.SUSPENDED_WAITING_FOR_APPROVAL}:
            self.status = TaskRunStatus.RUNNING



class TaskRunConflictError(RuntimeError):
    """A compare-and-swap update observed a different TaskRun revision."""


class TaskRunScopeError(RuntimeError):
    """A caller attempted to read or mutate a TaskRun outside its scope."""


class TaskRunStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or (Path(DATA_DIR) / "task_runs.json"))
        self._lock = threading.RLock()
        self._tasks: dict[str, TaskRun] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            rows = payload.get("task_runs", []) if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("TaskRun root must be {task_runs: [...]} ")
            if int(payload.get("schema_version") or 1) > 5:
                raise ValueError("unsupported future TaskRun store schema version")
            for raw in rows:
                task = TaskRun.model_validate(raw)
                if task.id in self._tasks:
                    raise ValueError(f"Duplicate TaskRun id: {task.id}")
                self._tasks[task.id] = task
        except Exception as exc:
            self._fail_corrupt(exc)

    def _fail_corrupt(self, error: Exception) -> None:
        root = self.path.parent / "corrupt-state" / f"task-runs-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        note = "quarantine copy could not be created"
        try:
            root.mkdir(parents=True, exist_ok=False)
            copy = root / self.path.name
            shutil.copy2(self.path, copy)
            guide = root / "RECOVERY.txt"
            guide.write_text(
                "EchoSpeak TaskRun recovery\n\n"
                f"Authoritative file: {self.path}\nQuarantine copy: {copy}\nError: {error}\n\n"
                "Keep EchoSpeak stopped. Repair or restore the authoritative JSON, then restart. "
                "The original file was not modified.\n",
                encoding="utf-8",
            )
            note = f"quarantine copy: {copy}; recovery guide: {guide}"
        except Exception as quarantine_error:
            note = f"quarantine failed: {quarantine_error}"
        raise RuntimeError(
            f"TaskRun state is unreadable at {self.path}; it was not overwritten; {note}. ({error})"
        ) from error

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 5,
            "task_runs": [
                item.model_dump(mode="json")
                for item in sorted(self._tasks.values(), key=lambda row: (row.created_at, row.id))
            ],
        }
        serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        last_error: Optional[Exception] = None
        for attempt in range(1, 9):
            temp = self.path.with_name(
                f".{self.path.name}.{os.getpid()}.{time.time_ns()}.{uuid.uuid4().hex[:8]}.tmp"
            )
            try:
                with temp.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(serialized)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, self.path)
                return
            except (PermissionError, OSError) as exc:
                last_error = exc
                winerr = getattr(exc, "winerror", None)
                if winerr not in {5, 32, 33} and not isinstance(exc, PermissionError):
                    raise
                time.sleep(min(0.05 * (2 ** (attempt - 1)), 0.8))
            finally:
                if temp.exists():
                    try:
                        temp.unlink()
                    except OSError:
                        pass
        raise RuntimeError(
            f"Failed to persist TaskRun store to {self.path} after retries: {last_error}"
        ) from last_error

    @staticmethod
    def _copy(task: TaskRun) -> TaskRun:
        return task.model_copy(deep=True)

    def create(self, **values: Any) -> TaskRun:
        with self._lock:
            task = TaskRun.model_validate(values)
            if task.id in self._tasks:
                raise ValueError("TaskRun id already exists")
            if task.execution_graph_state is not None:
                task = task.model_copy(update={
                    "execution_graph_state": checkpoint_graph_state(
                        task.execution_graph_state,
                        task_revision=task.revision,
                        reason_code="taskrun_created",
                    )
                })
            self._tasks[task.id] = task
            self._save()
            return self._copy(task)

    def get(
        self,
        task_id: str,
        *,
        session_id: str,
        project_id: Optional[str] = None,
    ) -> Optional[TaskRun]:
        with self._lock:
            task = self._tasks.get(str(task_id or ""))
            if task is None:
                return None
            self._require_scope(task, session_id=session_id, project_id=project_id)
            return self._copy(task)

    def list_for_session(
        self,
        session_id: str,
        *,
        project_id: Optional[str] = None,
        include_terminal: bool = False,
    ) -> list[TaskRun]:
        with self._lock:
            rows = [item for item in self._tasks.values() if item.session_id == str(session_id or "")]
            if project_id is not None:
                rows = [item for item in rows if item.project_id == str(project_id or "")]
            if not include_terminal:
                rows = [item for item in rows if item.status not in TERMINAL_TASK_STATUSES]
            rows.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
            return [self._copy(item) for item in rows]

    def find_for_execution(
        self,
        *,
        session_id: str,
        project_id: str,
        execution_id: str,
    ) -> Optional[TaskRun]:
        """Return the unique TaskRun owned by an Execution, failing ambiguity."""

        identity = str(execution_id or "").strip()
        if not identity:
            return None
        rows = self.list_for_session(
            session_id, project_id=project_id, include_terminal=True
        )
        matches = [
            item for item in rows
            if identity in {item.created_by_execution_id, item.last_execution_id}
        ]
        if len(matches) > 1:
            raise TaskRunConflictError(
                f"Execution {identity} is bound to multiple TaskRuns"
            )
        return matches[0] if matches else None

    def resume_for_approval(
        self,
        task_id: str,
        *,
        session_id: str,
        project_id: str,
        expected_revision: int,
        execution_id: str,
        requirement_id: str,
        attempt_id: str,
    ) -> TaskRun:
        """CAS-resume the exact suspended TaskRun before approval consumption."""

        current = self.get(task_id, session_id=session_id, project_id=project_id)
        if current is None:
            raise KeyError(f"Unknown TaskRun: {task_id}")
        if current.revision != int(expected_revision):
            raise TaskRunConflictError(
                f"TaskRun {current.id} changed from revision {expected_revision} to {current.revision}"
            )
        if current.status != TaskRunStatus.SUSPENDED_WAITING_FOR_APPROVAL:
            raise ValueError("Approval TaskRun is not suspended waiting for approval")
        state = current.requirement_states.get(str(requirement_id or ""))
        if state is None or str(attempt_id or "") not in state.attempt_ids:
            raise ValueError("Approval requirement attempt does not belong to the TaskRun")
        return self.update(
            current.id,
            session_id=session_id,
            project_id=project_id,
            expected_revision=current.revision,
            status=TaskRunStatus.RUNNING,
            workflow_stage="approval_consuming",
            last_execution_id=execution_id,
        )

    def suspended_candidates(self, session_id: str, *, project_id: str = "") -> list[TaskRun]:
        return [
            item for item in self.list_for_session(session_id, project_id=project_id, include_terminal=False)
            if (
                item.status != TaskRunStatus.SUSPENDED_WAITING_FOR_USER
                or bool(item.unresolved_wait_fields())
            )
            and (item.suspended() or item.status in {TaskRunStatus.RUNNING, TaskRunStatus.BACKGROUND})
        ]

    def continuation_candidates(
        self,
        session_id: str,
        *,
        project_id: str = "",
        limit: int = 12,
    ) -> list[TaskRun]:
        """Return work the selected model may explicitly continue or retry.

        Recoverable failed work is visible to Turn Understanding, but it is
        never selected automatically and never becomes ordinary chat context.
        """

        rows = self.list_for_session(
            session_id,
            project_id=project_id,
            include_terminal=True,
        )
        candidates = [
            item
            for item in rows
            if (
                item.status in RECOVERABLE_TASK_STATUSES
                or (
                    item.status not in TERMINAL_TASK_STATUSES
                    and item.status != TaskRunStatus.LEGACY_UNTRUSTED
                    and (
                        item.status != TaskRunStatus.SUSPENDED_WAITING_FOR_USER
                        or bool(item.unresolved_wait_fields())
                    )
                )
            )
        ]
        return candidates[: max(1, min(int(limit), 24))]

    def quarantine_invalid_waiting_tasks(
        self,
        session_id: str,
        *,
        project_id: str = "",
        active_execution_task_ids: Optional[set[str]] = None,
        pending_approval_task_ids: Optional[set[str]] = None,
        terminal_execution_ids: Optional[set[str]] = None,
    ) -> list[str]:
        """Quarantine structurally impossible resumable work without deleting history."""
        changed: list[str] = []
        active_execution_task_ids = set(active_execution_task_ids or set())
        pending_approval_task_ids = set(pending_approval_task_ids or set())
        terminal_execution_ids = set(terminal_execution_ids or set())
        with self._lock:
            originals: dict[str, TaskRun] = {}
            for current in list(self._tasks.values()):
                if current.session_id != str(session_id or "") or current.project_id != str(project_id or ""):
                    continue
                reason = ""
                if (
                    current.status == TaskRunStatus.SUSPENDED_WAITING_FOR_USER
                    and not current.unresolved_wait_fields()
                ):
                    reason = "declared missing inputs were already present or empty"
                elif current.status == TaskRunStatus.RUNNING:
                    active_requirement = choose_active_requirement(
                        current.requirements, current.requirement_states
                    )
                    user_gaps = [
                        item for item in current.input_gaps
                        if item.owner == TaskInputOwner.USER and item.blocking
                    ]
                    if (
                        str(current.last_execution_id or "") in terminal_execution_ids
                        and current.id not in active_execution_task_ids
                        and current.id not in pending_approval_task_ids
                    ):
                        reason = "running TaskRun is owned only by a terminal Execution"
                    elif (
                        active_requirement is None
                        and not user_gaps
                        and current.id not in active_execution_task_ids
                        and current.id not in pending_approval_task_ids
                    ):
                        reason = "running TaskRun has no actionable requirement or user-owned input gap"
                if not reason:
                    continue
                provenance = dict(current.legacy_provenance or {})
                quarantined_at = time.time()
                provenance["invalid_wait_quarantined_at"] = quarantined_at
                provenance["invalid_wait_reason"] = reason
                updated = TaskRun.model_validate(current.model_copy(update={
                    "status": TaskRunStatus.QUARANTINED,
                    "workflow_stage": "quarantined_inconsistent_lifecycle",
                    "legacy_provenance": provenance,
                    "quarantine_diagnostics": {
                        "reason_code": "non_resumable_lifecycle_invariant",
                        "reason": reason,
                        "previous_status": current.status.value,
                        "task_revision": current.revision,
                        "quarantined_at": quarantined_at,
                    },
                    "revision": current.revision + 1,
                    "updated_at": quarantined_at,
                }).model_dump())
                originals[current.id] = current
                self._tasks[current.id] = updated
                changed.append(current.id)
            if changed:
                try:
                    self._save()
                except Exception:
                    self._tasks.update(originals)
                    raise
        return changed

    def update(
        self,
        task_id: str,
        *,
        session_id: str,
        project_id: Optional[str] = None,
        expected_revision: int,
        clear_fields: Iterable[str] = (),
        **changes: Any,
    ) -> TaskRun:
        with self._lock:
            current = self._tasks.get(str(task_id or ""))
            if current is None:
                raise KeyError(f"Unknown TaskRun: {task_id}")
            self._require_scope(current, session_id=session_id, project_id=project_id)
            if current.revision != int(expected_revision):
                raise TaskRunConflictError(
                    f"TaskRun {current.id} changed from revision {expected_revision} to {current.revision}"
                )
            allowed = set(TaskRun.model_fields) - {
                "id", "schema_version", "session_id", "project_id", "created_at", "revision", "updated_at"
            }
            update = {key: value for key, value in changes.items() if key in allowed and value is not None}
            for key in clear_fields:
                if key not in allowed:
                    raise ValueError(f"TaskRun field cannot be cleared: {key}")
                if not TaskRun.model_fields[key].is_required():
                    update[key] = None
                else:
                    raise ValueError(f"Required TaskRun field cannot be cleared: {key}")
            update["revision"] = current.revision + 1
            update["updated_at"] = time.time()
            updated = TaskRun.model_validate(current.model_copy(update=update).model_dump())
            if updated.execution_graph_state is not None:
                updated = updated.model_copy(update={
                    "execution_graph_state": checkpoint_graph_state(
                        updated.execution_graph_state,
                        task_revision=updated.revision,
                        reason_code=str(update.get("workflow_stage") or "taskrun_updated"),
                    )
                })
            self._tasks[current.id] = updated
            try:
                self._save()
            except Exception:
                self._tasks[current.id] = current
                raise
            return self._copy(updated)

    def update_latest_owned(
        self,
        task_id: str,
        *,
        session_id: str,
        project_id: str,
        execution_id: str,
        **changes: Any,
    ) -> TaskRun:
        """Update the latest revision only when the same Execution still owns it."""

        with self._lock:
            current = self._tasks.get(str(task_id or ""))
            if current is None:
                raise KeyError(f"Unknown TaskRun: {task_id}")
            self._require_scope(current, session_id=session_id, project_id=project_id)
            owner = str(execution_id or "").strip()
            if owner and current.last_execution_id not in {"", owner} and current.created_by_execution_id != owner:
                raise TaskRunConflictError(
                    f"TaskRun {current.id} is now owned by another Execution"
                )
            return self.update(
                current.id,
                session_id=session_id,
                project_id=project_id,
                expected_revision=current.revision,
                **changes,
            )

    def checkpoint_waiting_for_user(
        self,
        task_id: str,
        *,
        session_id: str,
        project_id: str,
        expected_revision: int,
        execution_id: str,
        selected_skill_id: str = "",
        selected_skill_version: str = "",
        workflow_stage: str,
        collected_inputs: dict[str, Any],
        missing_inputs: list[str],
        completion_requirements: list[str],
        permitted_capabilities: list[str],
    ) -> TaskRun:
        """Atomically persist the full clarification checkpoint before UI emission."""
        current = self.get(task_id, session_id=session_id, project_id=project_id)
        if current is None:
            raise KeyError(f"Unknown TaskRun: {task_id}")
        gaps = classify_task_input_gaps(
            list(missing_inputs or []),
            list(permitted_capabilities or []),
            objective=current.objective,
            requirements=current.requirements,
        )
        return self.update(
            task_id,
            session_id=session_id,
            project_id=project_id,
            expected_revision=expected_revision,
            status=TaskRunStatus.SUSPENDED_WAITING_FOR_USER,
            last_execution_id=execution_id,
            selected_skill_id=selected_skill_id,
            selected_skill_version=selected_skill_version,
            workflow_stage=workflow_stage,
            collected_inputs=dict(collected_inputs or {}),
            missing_inputs=[
                item.field for item in gaps
                if item.owner == TaskInputOwner.USER and item.blocking
            ],
            input_gaps=gaps,
            completion_requirements=list(completion_requirements or []),
            permitted_capabilities=list(permitted_capabilities or []),
        )

    def transition_graph(
        self,
        task_id: str,
        *,
        session_id: str,
        project_id: str,
        expected_revision: int,
        transition: GraphTransition,
    ) -> TaskRun:
        """CAS-apply one legal graph transition without changing TaskRun completion."""

        with self._lock:
            current = self._tasks.get(str(task_id or ""))
            if current is None:
                raise KeyError(f"Unknown TaskRun: {task_id}")
            self._require_scope(current, session_id=session_id, project_id=project_id)
            if current.revision != int(expected_revision):
                raise TaskRunConflictError(
                    f"TaskRun {current.id} changed from revision {expected_revision} to {current.revision}"
                )
            if current.execution_graph is None or current.execution_graph_state is None:
                raise RuntimeError("TaskRun execution graph is unavailable")
            graph_state = apply_graph_transition(
                current.execution_graph,
                current.execution_graph_state,
                transition,
            )
            graph_state = checkpoint_graph_state(
                graph_state,
                task_revision=current.revision + 1,
                reason_code=transition.reason_code,
            )
            updated = TaskRun.model_validate(current.model_copy(update={
                "execution_graph_state": graph_state,
                "revision": current.revision + 1,
                "updated_at": time.time(),
            }).model_dump())
            self._tasks[current.id] = updated
            self._save()
            return self._copy(updated)

    def supersede_and_create(
        self,
        task_id: str,
        *,
        session_id: str,
        project_id: str,
        expected_revision: int,
        execution_id: str,
        replacement: dict[str, Any],
    ) -> tuple[TaskRun, TaskRun]:
        """Atomically supersede one scoped TaskRun and create its replacement."""
        with self._lock:
            current = self._tasks.get(str(task_id or ""))
            if current is None:
                raise KeyError(f"Unknown TaskRun: {task_id}")
            self._require_scope(current, session_id=session_id, project_id=project_id)
            if current.revision != int(expected_revision):
                raise TaskRunConflictError(
                    f"TaskRun {current.id} changed from revision {expected_revision} to {current.revision}"
                )
            now = time.time()
            superseded = TaskRun.model_validate(current.model_copy(update={
                "status": TaskRunStatus.SUPERSEDED,
                "workflow_stage": "superseded_by_model_arbitration",
                "last_execution_id": execution_id,
                "revision": current.revision + 1,
                "updated_at": now,
            }).model_dump())
            values = {
                **dict(replacement or {}),
                "session_id": session_id,
                "project_id": project_id,
                "created_by_execution_id": execution_id,
                "last_execution_id": execution_id,
            }
            replacement_task = TaskRun.model_validate(values)
            if replacement_task.id in self._tasks:
                raise ValueError("Replacement TaskRun id already exists")
            self._tasks[current.id] = superseded
            self._tasks[replacement_task.id] = replacement_task
            try:
                self._save()
            except Exception:
                self._tasks[current.id] = current
                self._tasks.pop(replacement_task.id, None)
                raise
            return self._copy(superseded), self._copy(replacement_task)

    def handoff_to_profile(
        self,
        task_id: str,
        *,
        session_id: str,
        project_id: str,
        expected_revision: int,
        execution_id: str,
        target_profile: ExecutionProfile | str,
        objective: str = "",
    ) -> tuple[TaskRun, TaskRun]:
        """Atomically replace a TaskRun for an explicit same-Session surface handoff."""

        profile = ExecutionProfile(target_profile)
        with self._lock:
            current = self._tasks.get(str(task_id or ""))
            if current is None:
                raise KeyError(f"Unknown TaskRun: {task_id}")
            self._require_scope(current, session_id=session_id, project_id=project_id)
            if current.revision != int(expected_revision):
                raise TaskRunConflictError(
                    f"TaskRun {current.id} changed from revision {expected_revision} to {current.revision}"
                )
            if current.status in TERMINAL_TASK_STATUSES or current.status == TaskRunStatus.LEGACY_UNTRUSTED:
                raise ValueError("Only current non-terminal work can be handed off")
            if current.execution_profile == profile:
                raise ValueError("TaskRun is already assigned to the requested execution profile")

            now = time.time()
            handoff_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"echospeak:task-handoff:{current.id}:{current.revision}:{profile.value}:{execution_id}",
            ))
            superseded = TaskRun.model_validate(current.model_copy(update={
                "status": TaskRunStatus.SUPERSEDED,
                "workflow_stage": f"handed_off_to_{profile.value}",
                "last_execution_id": execution_id,
                "revision": current.revision + 1,
                "updated_at": now,
            }).model_dump())
            if superseded.execution_graph_state is not None:
                superseded = superseded.model_copy(update={
                    "execution_graph_state": checkpoint_graph_state(
                        superseded.execution_graph_state,
                        task_revision=superseded.revision,
                        reason_code=f"handed_off_to_{profile.value}",
                    )
                })

            replacement = TaskRun.model_validate({
                "project_id": current.project_id,
                "session_id": current.session_id,
                "objective": str(objective or current.objective),
                "requested_operation": current.requested_operation,
                "collected_inputs": dict(current.collected_inputs),
                "missing_inputs": list(current.missing_inputs),
                "workflow_stage": "handoff_ready",
                "plan": [dict(item) for item in current.plan],
                "completion_requirements": list(current.completion_requirements),
                "requirements": [item.model_copy(deep=True) for item in current.requirements],
                "requirement_states": {
                    key: value.model_copy(deep=True)
                    for key, value in current.requirement_states.items()
                },
                "research_depth": current.research_depth,
                "research_budget": current.research_budget.model_copy(deep=True) if current.research_budget else None,
                "research_started_at": current.research_started_at,
                "research_artifact_ids": list(current.research_artifact_ids),
                # Authority snapshots are intentionally not copied. The target
                # Turn must revalidate Session, Project, policy, permissions,
                # inventory, configuration, and selected model before work.
                "capability_snapshot": None,
                "completion_evaluation": None,
                "permitted_capabilities": list(current.permitted_capabilities),
                # ToolRuns remain owned by the TaskRun that authorized them.
                # Requirement evidence and ResearchArtifact references preserve
                # reusable results without assigning those ToolRuns to a second
                # TaskRun.
                "tool_run_ids": [],
                "verified_tool_outcomes": [],
                "retry_identity": {},
                "status": TaskRunStatus.RUNNING,
                "created_by_execution_id": execution_id,
                "last_execution_id": execution_id,
                "source": "surface_handoff",
                "execution_profile": profile,
                "parent_task_run_id": current.id,
                "handoff_context_id": handoff_id,
                "model_binding_events": [
                    *current.model_binding_events,
                    {
                        "event": "surface_handoff",
                        "source_profile": current.execution_profile.value,
                        "target_profile": profile.value,
                        "execution_id": execution_id,
                        "created_at": now,
                        "requires_fresh_authority_validation": True,
                    },
                ],
                "legacy_provenance": {
                    "handoff_from_task_run_id": current.id,
                    "handoff_context_id": handoff_id,
                    "inherited_tool_run_ids": list(current.tool_run_ids),
                },
            })
            if replacement.execution_graph_state is not None:
                replacement = replacement.model_copy(update={
                    "execution_graph_state": checkpoint_graph_state(
                        replacement.execution_graph_state,
                        task_revision=replacement.revision,
                        reason_code="surface_handoff_created",
                    )
                })
            self._tasks[current.id] = superseded
            self._tasks[replacement.id] = replacement
            try:
                self._save()
            except Exception:
                self._tasks[current.id] = current
                self._tasks.pop(replacement.id, None)
                raise
            return self._copy(superseded), self._copy(replacement)

    @staticmethod
    def _require_scope(task: TaskRun, *, session_id: str, project_id: Optional[str]) -> None:
        if task.session_id != str(session_id or ""):
            raise TaskRunScopeError("TaskRun belongs to another Session")
        if project_id is not None and task.project_id != str(project_id or ""):
            raise TaskRunScopeError("TaskRun belongs to another Project")

    def migrate_legacy_session_state(self, state_store: Any, session_id: str) -> dict[str, Any]:
        """One-way, non-destructive semantic-state migration.

        Old semantic fields are preserved in ``legacy_semantic_state`` and then
        cleared from their sticky Session locations. Only a structurally complete
        waiting continuation becomes a resumable suspended candidate. Everything
        else is retained as ``legacy_untrusted`` and cannot be auto-selected.
        """
        state = state_store.get_thread_state(session_id)
        if int(getattr(state, "semantic_schema_version", 0) or 0) >= 1:
            return {"migrated": False, "created_task_ids": []}
        legacy = {
            "objective": str(getattr(state, "objective", "") or ""),
            "current_subject": str(getattr(state, "current_subject", "") or ""),
            "mode": str(getattr(state, "mode", "") or ""),
            "phase": str(getattr(state, "phase", "") or ""),
            "active_continuation": dict(getattr(state, "active_continuation", None) or {}),
            "unfinished_workflow": dict(getattr(state, "unfinished_workflow", None) or {}),
            "retry_target": dict(getattr(state, "retry_target", None) or {}),
            "required_capabilities": list(getattr(state, "required_capabilities", None) or []),
            "available_capabilities": list(getattr(state, "available_capabilities", None) or []),
            "allowed_tool_names": list(getattr(state, "allowed_tool_names", None) or []),
            "constraints": list(getattr(state, "constraints", None) or []),
            "decisions": list(getattr(state, "decisions", None) or []),
            "plan_steps": list(getattr(state, "plan_steps", None) or []),
            "pending_offered_action": dict(getattr(state, "pending_offered_action", None) or {}),
            "last_assistant_claim": dict(getattr(state, "last_assistant_claim", None) or {}),
            "execution_status": str(getattr(state, "execution_status", "") or ""),
            "safest_next_action": str(getattr(state, "safest_next_action", "") or ""),
            "migrated_at": time.time(),
        }
        created: list[TaskRun] = []
        continuation = legacy["active_continuation"]
        objective = str(continuation.get("objective") or legacy["objective"] or "").strip()
        if objective:
            collected = dict(continuation.get("collected_args") or continuation.get("collected_inputs") or {})
            missing = list(continuation.get("missing_fields") or continuation.get("missing_inputs") or [])
            has_trusted_identity = bool(
                continuation.get("id")
                and str(continuation.get("session_id") or session_id) == session_id
                and str(continuation.get("project_id") or state.active_project_id or "")
                == str(state.active_project_id or "")
            )
            status = (
                TaskRunStatus.SUSPENDED_WAITING_FOR_USER
                if has_trusted_identity and missing
                else TaskRunStatus.LEGACY_UNTRUSTED
            )
            legacy_task_id = str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                "echospeak:legacy-task-run:"
                f"{session_id}:{state.active_project_id or ''}:"
                f"{continuation.get('id') or hashlib.sha256(objective.encode('utf-8')).hexdigest()}",
            ))
            existing = self.get(
                legacy_task_id,
                session_id=session_id,
                project_id=str(state.active_project_id or ""),
            )
            created.append(existing or self.create(
                id=legacy_task_id,
                project_id=str(state.active_project_id or ""),
                session_id=session_id,
                objective=objective,
                requested_operation=str(
                    continuation.get("requested_operation")
                    or continuation.get("capability")
                    or continuation.get("tool_family")
                    or ""
                ),
                collected_inputs=collected,
                missing_inputs=missing,
                selected_skill_id=str(continuation.get("skill_id") or ""),
                workflow_stage=str(continuation.get("status") or legacy["phase"] or "legacy"),
                completion_requirements=list(continuation.get("completion_requirements") or []),
                permitted_capabilities=list(continuation.get("required_capabilities") or state.required_capabilities or []),
                retry_identity=legacy["retry_target"],
                status=status,
                source="legacy_migration",
                legacy_provenance={"continuation_id": str(continuation.get("id") or ""), "trusted": has_trusted_identity},
            ))
        suspended_ids = [item.id for item in created if item.status != TaskRunStatus.LEGACY_UNTRUSTED]
        state_store.update_thread_state(
            session_id,
            foreground_task_id="",
            suspended_task_ids=suspended_ids,
            semantic_schema_version=1,
            semantic_state_migrated_at=time.time(),
            legacy_semantic_state=legacy,
            objective="",
            current_subject="",
            mode="chat",
            phase="",
            required_capabilities=[],
            available_capabilities=[],
            allowed_tool_names=[],
            constraints=[],
            decisions=[],
            plan_steps=[],
            retry_target={},
            active_continuation={},
            pending_offered_action={},
            last_assistant_claim={},
            unfinished_workflow={},
            execution_status="ready",
            safest_next_action="",
        )
        return {
            "migrated": True,
            "created_task_ids": [item.id for item in created],
            "legacy_untrusted": [item.id for item in created if item.status == TaskRunStatus.LEGACY_UNTRUSTED],
        }


_task_run_store: Optional[TaskRunStore] = None
_task_run_store_lock = threading.Lock()


def get_task_run_store() -> TaskRunStore:
    global _task_run_store
    if _task_run_store is None:
        with _task_run_store_lock:
            if _task_run_store is None:
                _task_run_store = TaskRunStore()
    return _task_run_store
