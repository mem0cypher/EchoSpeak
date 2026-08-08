"""Canonical contracts for specialist agent runtimes.

Raw model adapters and specialist runtimes are deliberately separate:

* model adapters translate one inference provider's message/tool syntax;
* specialist runtimes own a domain-specific agent session and its internal loop.

EchoSpeak owns the user Session, Project, TaskRun, high-level delegation policy,
and overall completion.  A SpecialistRun owns only the execution truth of the
delegated specialist subtask.  UI state is a projection of these records.
"""
from __future__ import annotations

import re
import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


SPECIALIST_SCHEMA_VERSION = 2
SPECIALIST_EVENT_TAIL_LIMIT = 400


class SpecialistRuntimeKind(str, Enum):
    CODEX_APP_SERVER = "codex_app_server"
    OPENCODE = "opencode"


class SpecialistRuntimeState(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    MISCONFIGURED = "misconfigured"


class SpecialistRunStatus(str, Enum):
    REQUESTED = "requested"
    STARTING = "starting"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_INPUT = "waiting_for_input"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    DISCONNECTED = "disconnected"


TERMINAL_SPECIALIST_STATUSES = frozenset({
    SpecialistRunStatus.COMPLETED,
    SpecialistRunStatus.FAILED,
    SpecialistRunStatus.INTERRUPTED,
})


class SpecialistEventKind(str, Enum):
    RUNTIME_STARTED = "runtime.started"
    RUNTIME_READY = "runtime.ready"
    RUNTIME_DISCONNECTED = "runtime.disconnected"
    SESSION_STARTED = "session.started"
    TURN_STARTED = "turn.started"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    PLAN_UPDATED = "plan.updated"
    ACTION_STARTED = "action.started"
    ACTION_COMPLETED = "action.completed"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    FILE_CHANGED = "file.changed"
    COMMAND_OUTPUT = "command.output"
    DIFF_UPDATED = "diff.updated"
    ARTIFACT_PRODUCED = "artifact.produced"
    TURN_COMPLETED = "turn.completed"
    TURN_INTERRUPTED = "turn.interrupted"
    RUNTIME_WARNING = "runtime.warning"
    RUNTIME_FAILED = "runtime.failed"
    UNKNOWN = "runtime.unknown"


class SpecialistFailureLayer(str, Enum):
    DISCOVERY = "runtime_discovery"
    TRANSPORT = "runtime_transport"
    PROTOCOL = "runtime_protocol"
    AUTHENTICATION = "runtime_authentication"
    AUTHORITY = "echo_authority"
    SPECIALIST = "specialist_execution"
    PERSISTENCE = "persistence"
    UNKNOWN = "unknown"


class SpecialistRuntimeDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SPECIALIST_SCHEMA_VERSION
    runtime_id: str
    kind: SpecialistRuntimeKind
    display_name: str
    state: SpecialistRuntimeState
    executable: str = ""
    version: str = ""
    reason: str = ""
    supports_resume: bool = True
    supports_streaming: bool = True
    supports_interrupt: bool = True
    supports_approvals: bool = True
    supports_diffs: bool = False
    supports_local_models: bool = False
    protocol: str = ""
    configuration_keys: list[str] = Field(default_factory=list)


class SpecialistAuthoritySnapshot(BaseModel):
    """Stable delegation scope; current authority is still revalidated on use."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    project_id: str
    project_root: str
    task_run_id: str
    requirement_id: str
    graph_node_id: str = ""
    model_binding_revision: int = 0
    permission_revision: int = 0
    capability_revision: int = 0
    approval_policy: str = "on_request"
    sandbox_mode: str = "workspace_write"
    created_at: float = Field(default_factory=time.time)


class SpecialistEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SPECIALIST_SCHEMA_VERSION
    event_id: str = Field(default_factory=lambda: f"spev-{uuid.uuid4()}")
    run_id: str
    sequence: int = Field(ge=1)
    kind: SpecialistEventKind
    runtime_id: str
    runtime_session_id: str = ""
    runtime_turn_id: str = ""
    runtime_item_id: str = ""
    runtime_request_id: str = ""
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    raw_source: str = ""
    created_at: float = Field(default_factory=time.time)

    @model_validator(mode="after")
    def bound_untrusted_fields(self) -> "SpecialistEvent":
        self.summary = re.sub(r"\s+", " ", str(self.summary or "")).strip()[:1000]
        self.raw_source = str(self.raw_source or "").strip()[:120]
        # Payloads are product projections, never an unbounded transcript dump.
        self.payload = _bounded_json(self.payload, depth=0)
        return self


class SpecialistOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome_id: str = Field(default_factory=lambda: f"spout-{uuid.uuid4()}")
    run_id: str
    status: SpecialistRunStatus
    verified: bool = False
    verifier_id: str = "specialist_terminal_event_v1"
    summary: str = ""
    final_message: str = ""
    changed_files: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    runtime_metadata: dict[str, Any] = Field(default_factory=dict)
    failure_layer: Optional[SpecialistFailureLayer] = None
    failure_code: str = ""
    failure_message: str = ""
    completed_at: float = Field(default_factory=time.time)


class SpecialistRun(BaseModel):
    """Durable specialist-subtask execution record.

    It is not a TaskRun and cannot finalize one.  The owning TaskRun stores only
    this record's id and evaluates the resulting requirement state.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SPECIALIST_SCHEMA_VERSION
    id: str = Field(default_factory=lambda: f"sprun-{uuid.uuid4()}")
    runtime_id: str
    runtime_kind: SpecialistRuntimeKind
    session_id: str
    project_id: str
    project_root: str
    task_run_id: str
    requirement_id: str
    graph_node_id: str = ""
    objective: str
    authority: SpecialistAuthoritySnapshot
    model_provider: str = ""
    model_id: str = ""
    local_base_url: str = ""
    runtime_session_id: str = ""
    runtime_turn_id: str = ""
    status: SpecialistRunStatus = SpecialistRunStatus.REQUESTED
    revision: int = 1
    # Echo retains only a bounded normalized UI/approval tail. Codex/OpenCode
    # remain the owner of their complete thread, turn, item, and transcript
    # history.
    events: list[SpecialistEvent] = Field(default_factory=list)
    next_event_sequence: int = 1
    active_turn_event_start: int = Field(default=1, ge=1)
    pending_approval_ids: list[str] = Field(default_factory=list)
    event_count: int = 0
    outcome: Optional[SpecialistOutcome] = None
    failure_layer: Optional[SpecialistFailureLayer] = None
    failure_code: str = ""
    failure_message: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    @model_validator(mode="after")
    def validate_scope(self) -> "SpecialistRun":
        for name in ("runtime_id", "session_id", "project_id", "project_root",
                     "task_run_id", "requirement_id", "objective"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"SpecialistRun {name} is required")
            setattr(self, name, value)
        self.graph_node_id = str(
            self.graph_node_id or self.authority.graph_node_id or ""
        ).strip()[:160]
        if self.authority.graph_node_id and (
            self.graph_node_id != self.authority.graph_node_id
        ):
            raise ValueError("SpecialistRun graph node does not match authority lineage")
        self.objective = re.sub(r"\s+", " ", self.objective).strip()[:8000]
        self.pending_approval_ids = list(dict.fromkeys(
            str(item).strip() for item in self.pending_approval_ids if str(item).strip()
        ))[:64]
        self.events = sorted(
            [
                item if isinstance(item, SpecialistEvent)
                else SpecialistEvent.model_validate(item)
                for item in self.events
                if str(getattr(item, "run_id", "") or (item.get("run_id") if isinstance(item, dict) else ""))
                == self.id
            ],
            key=lambda item: item.sequence,
        )[-SPECIALIST_EVENT_TAIL_LIMIT:]
        if self.events:
            sequences = [item.sequence for item in self.events]
            if len(sequences) != len(set(sequences)):
                raise ValueError("SpecialistRun event tail contains duplicate sequences")
            self.next_event_sequence = max(
                int(self.next_event_sequence or 1),
                sequences[-1] + 1,
            )
            self.event_count = max(int(self.event_count or 0), sequences[-1])
        self.schema_version = SPECIALIST_SCHEMA_VERSION
        return self


class SpecialistRunProjection(BaseModel):
    run: SpecialistRun
    events: list[SpecialistEvent] = Field(default_factory=list)


def _bounded_json(value: Any, *, depth: int) -> Any:
    if depth >= 5:
        return "[bounded]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:8000]
    if isinstance(value, list):
        return [_bounded_json(item, depth=depth + 1) for item in value[:80]]
    if isinstance(value, dict):
        return {
            str(key)[:160]: _bounded_json(item, depth=depth + 1)
            for key, item in list(value.items())[:100]
        }
    return str(value)[:2000]


__all__ = [
    "SPECIALIST_SCHEMA_VERSION",
    "SPECIALIST_EVENT_TAIL_LIMIT",
    "TERMINAL_SPECIALIST_STATUSES",
    "SpecialistAuthoritySnapshot",
    "SpecialistEvent",
    "SpecialistEventKind",
    "SpecialistFailureLayer",
    "SpecialistOutcome",
    "SpecialistRun",
    "SpecialistRunProjection",
    "SpecialistRunStatus",
    "SpecialistRuntimeDescriptor",
    "SpecialistRuntimeKind",
    "SpecialistRuntimeState",
]
