"""Durable automation Run identity, coordination, and recovery.

Routine definitions and Product Tasks remain separate domain records.  This
module owns the historical execution identity that makes trigger evaluation
idempotent and gives Heartbeat a single atomic claim/lease boundary.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

try:
    from config import DATA_DIR
except Exception:  # pragma: no cover - import fallback for isolated tooling
    DATA_DIR = Path("data")


AUTOMATION_RUN_SCHEMA_VERSION = 1


class AutomationRuntimeError(RuntimeError):
    """Base error for durable automation state."""


class AutomationStateError(AutomationRuntimeError):
    """Canonical state could not be loaded or persisted safely."""


class AutomationConflictError(AutomationRuntimeError):
    """An identity, revision, or idempotency contract conflicted."""


class AutomationScopeError(AutomationRuntimeError):
    """A caller attempted to cross the Run's Project/Session boundary."""


class AutomationLeaseError(AutomationRuntimeError):
    """A mutation did not hold the current, unexpired lease."""


class AutomationTransitionError(AutomationRuntimeError):
    """A requested Run lifecycle transition is invalid."""


class AutomationRunStatus(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    RUNNING = "running"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ModelBindingPolicy(str, Enum):
    SESSION_DEFAULT = "session_default"
    CONFIGURED_DEFAULT = "configured_default"
    EXPLICIT_OVERRIDE = "explicit_override"


TERMINAL_RUN_STATUSES = frozenset(
    {
        AutomationRunStatus.COMPLETED,
        AutomationRunStatus.FAILED,
        AutomationRunStatus.CANCELLED,
    }
)


_TRANSITIONS: dict[AutomationRunStatus, frozenset[AutomationRunStatus]] = {
    AutomationRunStatus.QUEUED: frozenset(
        {AutomationRunStatus.PREPARING, AutomationRunStatus.CANCELLED}
    ),
    AutomationRunStatus.PREPARING: frozenset(
        {
            AutomationRunStatus.QUEUED,
            AutomationRunStatus.RUNNING,
            AutomationRunStatus.WAITING_FOR_APPROVAL,
            AutomationRunStatus.PAUSED,
            AutomationRunStatus.BLOCKED,
            AutomationRunStatus.FAILED,
            AutomationRunStatus.CANCELLED,
        }
    ),
    AutomationRunStatus.WAITING_FOR_APPROVAL: frozenset(
        {
            AutomationRunStatus.QUEUED,
            AutomationRunStatus.BLOCKED,
            AutomationRunStatus.FAILED,
            AutomationRunStatus.CANCELLED,
        }
    ),
    AutomationRunStatus.RUNNING: frozenset(
        {
            AutomationRunStatus.QUEUED,
            AutomationRunStatus.WAITING_FOR_APPROVAL,
            AutomationRunStatus.PAUSED,
            AutomationRunStatus.BLOCKED,
            AutomationRunStatus.COMPLETED,
            AutomationRunStatus.FAILED,
            AutomationRunStatus.CANCELLED,
        }
    ),
    AutomationRunStatus.PAUSED: frozenset(
        {AutomationRunStatus.QUEUED, AutomationRunStatus.CANCELLED}
    ),
    AutomationRunStatus.BLOCKED: frozenset(
        {
            AutomationRunStatus.QUEUED,
            AutomationRunStatus.FAILED,
            AutomationRunStatus.CANCELLED,
        }
    ),
    AutomationRunStatus.FAILED: frozenset(
        {AutomationRunStatus.QUEUED, AutomationRunStatus.CANCELLED}
    ),
    AutomationRunStatus.COMPLETED: frozenset(),
    AutomationRunStatus.CANCELLED: frozenset(),
}


class AutomationModelBinding(BaseModel):
    """Visible model policy plus the exact provider/model resolved for a Run."""

    policy: ModelBindingPolicy = ModelBindingPolicy.SESSION_DEFAULT
    source_session_id: str = ""
    requested_provider: str = ""
    requested_model_id: str = ""
    resolved_provider: str = ""
    resolved_model_id: str = ""
    model_snapshot: dict[str, Any] = Field(default_factory=dict)
    fallback_used: bool = False
    fallback_reason: str = ""
    resolved_at: Optional[float] = None

    @model_validator(mode="after")
    def validate_binding(self) -> "AutomationModelBinding":
        if self.policy == ModelBindingPolicy.SESSION_DEFAULT and not self.source_session_id.strip():
            raise ValueError("session_default model binding requires source_session_id")
        if self.policy == ModelBindingPolicy.EXPLICIT_OVERRIDE and (
            not self.requested_provider.strip() or not self.requested_model_id.strip()
        ):
            raise ValueError("explicit_override requires requested provider and model")
        if self.fallback_used and not self.fallback_reason.strip():
            raise ValueError("a visible fallback reason is required when fallback_used is true")
        if bool(self.resolved_provider) != bool(self.resolved_model_id):
            raise ValueError("resolved provider and model must be recorded together")
        return self


class AutomationLease(BaseModel):
    claimant_id: str = Field(min_length=1)
    token: str = Field(min_length=1)
    generation: int = Field(ge=1)
    acquired_at: float
    renewed_at: float
    expires_at: float

    @model_validator(mode="after")
    def validate_expiry(self) -> "AutomationLease":
        if self.expires_at <= self.renewed_at:
            raise ValueError("lease expiry must be later than its renewal timestamp")
        return self


class AutomationCheckpoint(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sequence: int = Field(ge=1)
    kind: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    execution_id: str = ""
    tool_run_ids: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)


class AutomationRun(BaseModel):
    """One historical attempt to execute a finite Task or Routine occurrence."""

    schema_version: Literal[1] = AUTOMATION_RUN_SCHEMA_VERSION
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: str = Field(min_length=1, max_length=500)
    project_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str = ""
    routine_id: str = ""
    trigger_id: str = ""
    source: str = "manual"
    source_id: str = ""
    objective: str = Field(min_length=1)
    status: AutomationRunStatus = AutomationRunStatus.QUEUED
    model_binding: AutomationModelBinding
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=100)
    recovery_count: int = Field(default=0, ge=0)
    lease_generation: int = Field(default=0, ge=0)
    lease: Optional[AutomationLease] = None
    execution_id: str = ""
    tool_run_ids: list[str] = Field(default_factory=list)
    approval_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    checkpoints: list[AutomationCheckpoint] = Field(default_factory=list)
    outcome: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    revision: int = Field(default=1, ge=1)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    completed_at: Optional[float] = None

    @field_validator("idempotency_key", "project_id", "session_id", "objective")
    @classmethod
    def strip_required_identity(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("required automation identity fields cannot be empty")
        return cleaned

    @model_validator(mode="after")
    def validate_identity_and_lifecycle(self) -> "AutomationRun":
        if (
            self.model_binding.policy == ModelBindingPolicy.SESSION_DEFAULT
            and self.model_binding.source_session_id != self.session_id
        ):
            raise ValueError("session_default model binding must reference the Run session")
        sequences = [checkpoint.sequence for checkpoint in self.checkpoints]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("checkpoint sequences must be contiguous and ordered")
        if self.status in TERMINAL_RUN_STATUSES and self.lease is not None:
            raise ValueError("terminal Runs cannot retain a lease")
        return self


class _AutomationRunEnvelope(BaseModel):
    schema_version: Literal[1] = AUTOMATION_RUN_SCHEMA_VERSION
    revision: int = Field(default=0, ge=0)
    runs: dict[str, AutomationRun] = Field(default_factory=dict)


def resolve_automation_run_path(data_dir: Optional[Path] = None) -> Path:
    root = Path(data_dir if data_dir is not None else DATA_DIR).expanduser().resolve()
    return root / "automations" / "runs.json"


class AutomationRunStore:
    """Single-process transactional owner for durable automation Runs.

    EchoSpeak's runtime process lock prevents multiple backend writers.  This
    store adds the thread-safe compare-and-set boundary needed by concurrent
    trigger evaluation inside that process.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or resolve_automation_run_path()).expanduser().resolve()
        self._lock = threading.RLock()
        self._envelope = _AutomationRunEnvelope()
        self._idempotency_index: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
            if not raw.strip():
                raise ValueError("authoritative automation state is empty")
            payload = json.loads(raw)
            envelope = _AutomationRunEnvelope.model_validate(payload)
            index: dict[str, str] = {}
            for key, run in envelope.runs.items():
                if key != run.id:
                    raise ValueError(f"Run map key {key!r} does not match record id {run.id!r}")
                if run.idempotency_key in index:
                    raise ValueError(f"duplicate idempotency key {run.idempotency_key!r}")
                index[run.idempotency_key] = run.id
            self._envelope = envelope
            self._idempotency_index = index
        except Exception as exc:
            self._fail_corrupt(exc)

    def _fail_corrupt(self, error: Exception) -> None:
        quarantine = self.path.parent / "corrupt-state" / f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        note = "quarantine copy could not be created"
        try:
            quarantine.mkdir(parents=True, exist_ok=False)
            copy = quarantine / self.path.name
            shutil.copy2(self.path, copy)
            guide = quarantine / "RECOVERY.txt"
            guide.write_text(
                "EchoSpeak automation Run recovery\n\n"
                f"Authoritative file: {self.path}\nQuarantine copy: {copy}\nError: {error}\n\n"
                "Keep the backend stopped. Repair the authoritative JSON or restore a reviewed backup, "
                "then restart one backend instance. The original file was not changed.\n",
                encoding="utf-8",
            )
            note = f"quarantine copy: {copy}; recovery guide: {guide}"
        except Exception as quarantine_error:  # pragma: no cover - exceptional filesystem failure
            note = f"quarantine failed: {quarantine_error}"
        raise AutomationStateError(
            f"Automation Run state is unreadable at {self.path}; the authoritative file was not "
            f"overwritten; {note}. ({error})"
        ) from error

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
        payload = self._envelope.model_dump(mode="json")
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except Exception as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise AutomationStateError(f"Failed to persist Automation Run state: {exc}") from exc

    @staticmethod
    def _copy(run: AutomationRun) -> AutomationRun:
        return run.model_copy(deep=True)

    def _touch(self, run: AutomationRun, *, now: Optional[float] = None) -> None:
        run.updated_at = float(time.time() if now is None else now)
        run.revision += 1
        self._envelope.revision += 1

    def _require_scope(self, run_id: str, project_id: str, session_id: str) -> AutomationRun:
        run = self._envelope.runs.get(str(run_id or ""))
        if run is None:
            raise AutomationRuntimeError("Automation Run not found")
        if run.project_id != str(project_id or "").strip() or run.session_id != str(session_id or "").strip():
            raise AutomationScopeError("Automation Run belongs to another Project or Session")
        return run

    @staticmethod
    def _check_revision(run: AutomationRun, expected_revision: Optional[int]) -> None:
        if expected_revision is not None and run.revision != int(expected_revision):
            raise AutomationConflictError(
                f"Automation Run revision changed (expected {expected_revision}, current {run.revision})"
            )

    @staticmethod
    def _require_lease(
        run: AutomationRun,
        *,
        claimant_id: str,
        lease_token: str,
        now: Optional[float] = None,
    ) -> AutomationLease:
        lease = run.lease
        current = float(time.time() if now is None else now)
        if lease is None:
            raise AutomationLeaseError("Automation Run has no active lease")
        if lease.claimant_id != str(claimant_id or "") or lease.token != str(lease_token or ""):
            raise AutomationLeaseError("Automation Run lease identity does not match")
        if lease.expires_at <= current:
            raise AutomationLeaseError("Automation Run lease has expired")
        return lease

    def create_run(self, **values: Any) -> AutomationRun:
        candidate = AutomationRun.model_validate(values)
        with self._lock:
            existing_id = self._idempotency_index.get(candidate.idempotency_key)
            if existing_id:
                existing = self._envelope.runs[existing_id]
                stable_existing = (
                    existing.project_id,
                    existing.session_id,
                    existing.task_id,
                    existing.routine_id,
                    existing.trigger_id,
                    existing.source,
                    existing.source_id,
                    existing.objective,
                    existing.model_binding.policy,
                    existing.model_binding.source_session_id,
                    existing.model_binding.requested_provider,
                    existing.model_binding.requested_model_id,
                )
                stable_candidate = (
                    candidate.project_id,
                    candidate.session_id,
                    candidate.task_id,
                    candidate.routine_id,
                    candidate.trigger_id,
                    candidate.source,
                    candidate.source_id,
                    candidate.objective,
                    candidate.model_binding.policy,
                    candidate.model_binding.source_session_id,
                    candidate.model_binding.requested_provider,
                    candidate.model_binding.requested_model_id,
                )
                if stable_existing != stable_candidate:
                    raise AutomationConflictError(
                        "Idempotency key is already bound to another automation identity or scope"
                    )
                return self._copy(existing)
            if candidate.id in self._envelope.runs:
                raise AutomationConflictError("Automation Run id already exists")
            self._envelope.runs[candidate.id] = candidate
            self._idempotency_index[candidate.idempotency_key] = candidate.id
            self._envelope.revision += 1
            self._persist()
            return self._copy(candidate)

    def get_run(self, run_id: str, *, project_id: str, session_id: str) -> Optional[AutomationRun]:
        with self._lock:
            run = self._envelope.runs.get(str(run_id or ""))
            if run is None or run.project_id != project_id or run.session_id != session_id:
                return None
            return self._copy(run)

    def list_runs(self, *, project_id: str, session_id: str = "") -> list[AutomationRun]:
        scope = str(project_id or "").strip()
        if not scope:
            raise AutomationScopeError("Project scope is required to list Automation Runs")
        with self._lock:
            rows = [
                self._copy(run)
                for run in self._envelope.runs.values()
                if run.project_id == scope and (not session_id or run.session_id == session_id)
            ]
        return sorted(rows, key=lambda item: (item.created_at, item.id), reverse=True)

    def claim(
        self,
        run_id: str,
        *,
        project_id: str,
        session_id: str,
        claimant_id: str,
        lease_seconds: float = 60.0,
        now: Optional[float] = None,
        expected_revision: Optional[int] = None,
    ) -> Optional[AutomationRun]:
        current = float(time.time() if now is None else now)
        duration = float(lease_seconds)
        if not str(claimant_id or "").strip():
            raise AutomationLeaseError("claimant_id is required")
        if duration <= 0:
            raise AutomationLeaseError("lease_seconds must be positive")
        with self._lock:
            run = self._require_scope(run_id, project_id, session_id)
            self._check_revision(run, expected_revision)
            if run.status != AutomationRunStatus.QUEUED:
                return None
            if run.lease is not None and run.lease.expires_at > current:
                return None
            if run.attempt >= run.max_attempts:
                raise AutomationTransitionError("Automation Run retry budget is exhausted")
            run.attempt += 1
            run.lease_generation += 1
            run.lease = AutomationLease(
                claimant_id=str(claimant_id).strip(),
                token=uuid.uuid4().hex,
                generation=run.lease_generation,
                acquired_at=current,
                renewed_at=current,
                expires_at=current + duration,
            )
            run.status = AutomationRunStatus.PREPARING
            self._touch(run, now=current)
            self._persist()
            return self._copy(run)

    def renew_lease(
        self,
        run_id: str,
        *,
        project_id: str,
        session_id: str,
        claimant_id: str,
        lease_token: str,
        lease_seconds: float = 60.0,
        now: Optional[float] = None,
        expected_revision: Optional[int] = None,
    ) -> AutomationRun:
        current = float(time.time() if now is None else now)
        duration = float(lease_seconds)
        if duration <= 0:
            raise AutomationLeaseError("lease_seconds must be positive")
        with self._lock:
            run = self._require_scope(run_id, project_id, session_id)
            self._check_revision(run, expected_revision)
            lease = self._require_lease(
                run, claimant_id=claimant_id, lease_token=lease_token, now=current
            )
            lease.renewed_at = current
            lease.expires_at = current + duration
            self._touch(run, now=current)
            self._persist()
            return self._copy(run)

    def bind_model(
        self,
        run_id: str,
        binding: AutomationModelBinding,
        *,
        project_id: str,
        session_id: str,
        claimant_id: str,
        lease_token: str,
        now: Optional[float] = None,
        expected_revision: Optional[int] = None,
    ) -> AutomationRun:
        current = float(time.time() if now is None else now)
        resolved = AutomationModelBinding.model_validate(binding)
        if not resolved.resolved_provider or not resolved.resolved_model_id:
            raise AutomationConflictError("resolved provider and model are required before execution")
        if resolved.policy == ModelBindingPolicy.SESSION_DEFAULT and resolved.source_session_id != session_id:
            raise AutomationScopeError("model binding source Session does not match the Run")
        with self._lock:
            run = self._require_scope(run_id, project_id, session_id)
            self._check_revision(run, expected_revision)
            self._require_lease(run, claimant_id=claimant_id, lease_token=lease_token, now=current)
            if run.status != AutomationRunStatus.PREPARING:
                raise AutomationTransitionError("model binding is only mutable while preparing")
            resolved.resolved_at = current
            run.model_binding = resolved.model_copy(deep=True)
            self._touch(run, now=current)
            self._persist()
            return self._copy(run)

    def append_checkpoint(
        self,
        run_id: str,
        *,
        project_id: str,
        session_id: str,
        kind: str,
        payload: Optional[dict[str, Any]] = None,
        execution_id: str = "",
        tool_run_ids: Optional[list[str]] = None,
        claimant_id: str,
        lease_token: str,
        now: Optional[float] = None,
        expected_revision: Optional[int] = None,
    ) -> AutomationRun:
        current = float(time.time() if now is None else now)
        with self._lock:
            run = self._require_scope(run_id, project_id, session_id)
            self._check_revision(run, expected_revision)
            self._require_lease(run, claimant_id=claimant_id, lease_token=lease_token, now=current)
            checkpoint = AutomationCheckpoint(
                sequence=len(run.checkpoints) + 1,
                kind=str(kind or "").strip(),
                payload=dict(payload or {}),
                execution_id=str(execution_id or ""),
                tool_run_ids=list(dict.fromkeys(str(item) for item in (tool_run_ids or []) if str(item))),
                created_at=current,
            )
            run.checkpoints.append(checkpoint)
            if checkpoint.execution_id:
                run.execution_id = checkpoint.execution_id
            run.tool_run_ids = list(dict.fromkeys([*run.tool_run_ids, *checkpoint.tool_run_ids]))
            self._touch(run, now=current)
            self._persist()
            return self._copy(run)

    def transition(
        self,
        run_id: str,
        target: AutomationRunStatus | str,
        *,
        project_id: str,
        session_id: str,
        claimant_id: str = "",
        lease_token: str = "",
        now: Optional[float] = None,
        expected_revision: Optional[int] = None,
        execution_id: Optional[str] = None,
        tool_run_ids: Optional[list[str]] = None,
        approval_ids: Optional[list[str]] = None,
        artifact_ids: Optional[list[str]] = None,
        outcome: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> AutomationRun:
        current = float(time.time() if now is None else now)
        next_status = AutomationRunStatus(target)
        with self._lock:
            run = self._require_scope(run_id, project_id, session_id)
            self._check_revision(run, expected_revision)
            if next_status == run.status:
                return self._copy(run)
            if next_status not in _TRANSITIONS.get(run.status, frozenset()):
                raise AutomationTransitionError(
                    f"Invalid Automation Run transition: {run.status.value} -> {next_status.value}"
                )
            if run.lease is not None:
                self._require_lease(
                    run, claimant_id=claimant_id, lease_token=lease_token, now=current
                )
            elif next_status in {
                AutomationRunStatus.PREPARING,
                AutomationRunStatus.RUNNING,
                AutomationRunStatus.COMPLETED,
            }:
                raise AutomationLeaseError(f"{next_status.value} requires an active lease")

            run.status = next_status
            if execution_id is not None:
                run.execution_id = str(execution_id or "")
            if tool_run_ids is not None:
                run.tool_run_ids = list(
                    dict.fromkeys([*run.tool_run_ids, *(str(item) for item in tool_run_ids if str(item))])
                )
            if approval_ids is not None:
                run.approval_ids = list(
                    dict.fromkeys([*run.approval_ids, *(str(item) for item in approval_ids if str(item))])
                )
            if artifact_ids is not None:
                run.artifact_ids = list(
                    dict.fromkeys([*run.artifact_ids, *(str(item) for item in artifact_ids if str(item))])
                )
            if outcome is not None:
                run.outcome = dict(outcome)
            if error is not None:
                run.error = str(error)[:4000]

            if next_status != AutomationRunStatus.RUNNING:
                run.lease = None
            if next_status in TERMINAL_RUN_STATUSES:
                run.completed_at = current
            else:
                run.completed_at = None
            self._touch(run, now=current)
            self._persist()
            return self._copy(run)

    def recover_expired(self, *, now: Optional[float] = None) -> list[AutomationRun]:
        """Requeue or fail every expired claimed Run exactly once per persisted lease."""

        current = float(time.time() if now is None else now)
        recovered: list[AutomationRun] = []
        with self._lock:
            for run in self._envelope.runs.values():
                lease = run.lease
                if lease is None or lease.expires_at > current or run.status in TERMINAL_RUN_STATUSES:
                    continue
                run.recovery_count += 1
                exhausted = run.attempt >= run.max_attempts
                run.status = AutomationRunStatus.FAILED if exhausted else AutomationRunStatus.QUEUED
                run.error = "Automation Run lease expired; retry budget exhausted" if exhausted else ""
                run.lease = None
                run.checkpoints.append(
                    AutomationCheckpoint(
                        sequence=len(run.checkpoints) + 1,
                        kind="lease_expired",
                        payload={
                            "claimant_id": lease.claimant_id,
                            "lease_generation": lease.generation,
                            "requeued": not exhausted,
                        },
                        created_at=current,
                    )
                )
                run.completed_at = current if exhausted else None
                self._touch(run, now=current)
                recovered.append(self._copy(run))
            if recovered:
                self._persist()
        return recovered


_STORE: Optional[AutomationRunStore] = None


def get_automation_run_store() -> AutomationRunStore:
    global _STORE
    if _STORE is None:
        _STORE = AutomationRunStore()
    return _STORE
