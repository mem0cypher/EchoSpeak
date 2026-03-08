from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from threading import RLock
from typing import Any, Optional

from pydantic import BaseModel, Field

try:
    from config import DATA_DIR
except Exception:
    DATA_DIR = Path("data")


PHASE3_DIR = DATA_DIR / "phase3"
APPROVALS_PATH = PHASE3_DIR / "approvals.json"
EXECUTIONS_PATH = PHASE3_DIR / "executions.json"
THREAD_STATE_PATH = PHASE3_DIR / "thread_state.json"
TRACE_DIR = PHASE3_DIR / "traces"


class ApprovalRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    thread_id: str = "default"
    execution_id: Optional[str] = None
    status: str = "pending"
    tool: str
    kwargs: dict[str, Any] = Field(default_factory=dict)
    original_input: str = ""
    preview: str = ""
    summary: str = ""
    risk_level: str = "safe"
    policy_flags: list[str] = Field(default_factory=list)
    session_permissions: dict[str, bool] = Field(default_factory=dict)
    dry_run_available: bool = False
    source: str = "web"
    workspace_id: str = ""
    active_project_id: str = ""
    plan_state: Optional[dict[str, Any]] = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    decided_at: Optional[float] = None
    outcome_summary: str = ""


class ExecutionRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: str = "query"
    thread_id: str = "default"
    source: str = "web"
    status: str = "running"
    query: str = ""
    workspace_id: str = ""
    active_project_id: str = ""
    runtime_provider: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    completed_at: Optional[float] = None
    success: Optional[bool] = None
    response_preview: str = ""
    error: str = ""
    approvals: list[str] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    tool_latencies_ms: list[dict[str, Any]] = Field(default_factory=list)
    trace_id: Optional[str] = None
    evaluation: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ThreadSessionState(BaseModel):
    thread_id: str
    workspace_id: str = ""
    active_project_id: str = ""
    pending_approval_id: str = ""
    last_execution_id: str = ""
    last_trace_id: str = ""
    runtime_provider: str = ""
    updated_at: float = Field(default_factory=time.time)


class StateStore:
    def __init__(self) -> None:
        self._lock = RLock()
        PHASE3_DIR.mkdir(parents=True, exist_ok=True)
        TRACE_DIR.mkdir(parents=True, exist_ok=True)
        self._approvals: dict[str, ApprovalRecord] = {}
        self._executions: dict[str, ExecutionRecord] = {}
        self._thread_state: dict[str, ThreadSessionState] = {}
        self._load_all()

    def _read_json(self, path: Path) -> Any:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _load_all(self) -> None:
        approvals_raw = self._read_json(APPROVALS_PATH)
        if isinstance(approvals_raw, dict):
            for approval_id, data in approvals_raw.items():
                try:
                    record = ApprovalRecord(**data)
                    self._approvals[approval_id] = record
                except Exception:
                    continue
        executions_raw = self._read_json(EXECUTIONS_PATH)
        if isinstance(executions_raw, dict):
            for execution_id, data in executions_raw.items():
                try:
                    record = ExecutionRecord(**data)
                    self._executions[execution_id] = record
                except Exception:
                    continue
        thread_raw = self._read_json(THREAD_STATE_PATH)
        if isinstance(thread_raw, dict):
            for thread_id, data in thread_raw.items():
                try:
                    state = ThreadSessionState(**data)
                    self._thread_state[thread_id] = state
                except Exception:
                    continue

    def _persist_approvals(self) -> None:
        self._write_json(APPROVALS_PATH, {key: value.model_dump() for key, value in self._approvals.items()})

    def _persist_executions(self) -> None:
        self._write_json(EXECUTIONS_PATH, {key: value.model_dump() for key, value in self._executions.items()})

    def _persist_thread_state(self) -> None:
        self._write_json(THREAD_STATE_PATH, {key: value.model_dump() for key, value in self._thread_state.items()})

    def get_thread_state(self, thread_id: Optional[str]) -> ThreadSessionState:
        key = str(thread_id or "default").strip() or "default"
        with self._lock:
            state = self._thread_state.get(key)
            if state is None:
                state = ThreadSessionState(thread_id=key)
                self._thread_state[key] = state
                self._persist_thread_state()
            return ThreadSessionState(**state.model_dump())

    def update_thread_state(self, thread_id: Optional[str], **updates: Any) -> ThreadSessionState:
        key = str(thread_id or "default").strip() or "default"
        with self._lock:
            state = self._thread_state.get(key) or ThreadSessionState(thread_id=key)
            for field, value in updates.items():
                if not hasattr(state, field):
                    continue
                setattr(state, field, value or "" if isinstance(getattr(state, field), str) else value)
            state.updated_at = time.time()
            self._thread_state[key] = state
            self._persist_thread_state()
            return ThreadSessionState(**state.model_dump())

    def create_execution(self, **payload: Any) -> ExecutionRecord:
        record = ExecutionRecord(**payload)
        with self._lock:
            self._executions[record.id] = record
            self._persist_executions()
            self.update_thread_state(
                record.thread_id,
                last_execution_id=record.id,
                runtime_provider=record.runtime_provider,
                workspace_id=record.workspace_id,
                active_project_id=record.active_project_id,
            )
        return ExecutionRecord(**record.model_dump())

    def update_execution(self, execution_id: str, **updates: Any) -> Optional[ExecutionRecord]:
        with self._lock:
            record = self._executions.get(execution_id)
            if record is None:
                return None
            for field, value in updates.items():
                if hasattr(record, field):
                    setattr(record, field, value)
            record.updated_at = time.time()
            if record.status in {"completed", "failed", "canceled"} and record.completed_at is None:
                record.completed_at = time.time()
            self._executions[execution_id] = record
            self._persist_executions()
            self.update_thread_state(
                record.thread_id,
                last_execution_id=record.id,
                last_trace_id=record.trace_id or "",
                pending_approval_id=updates.get("clear_pending_approval", "") if "clear_pending_approval" in updates else self._thread_state.get(record.thread_id, ThreadSessionState(thread_id=record.thread_id)).pending_approval_id,
            )
            return ExecutionRecord(**record.model_dump())

    def get_execution(self, execution_id: str) -> Optional[ExecutionRecord]:
        with self._lock:
            record = self._executions.get(execution_id)
            return ExecutionRecord(**record.model_dump()) if record else None

    def list_executions(self, thread_id: Optional[str] = None, limit: int = 50) -> list[ExecutionRecord]:
        key = str(thread_id or "").strip()
        with self._lock:
            items = list(self._executions.values())
        if key:
            items = [item for item in items if item.thread_id == key]
        items.sort(key=lambda item: item.created_at, reverse=True)
        return [ExecutionRecord(**item.model_dump()) for item in items[:limit]]

    def create_approval(self, **payload: Any) -> ApprovalRecord:
        record = ApprovalRecord(**payload)
        with self._lock:
            self._approvals[record.id] = record
            self._persist_approvals()
            self.update_thread_state(record.thread_id, pending_approval_id=record.id)
            if record.execution_id and record.execution_id in self._executions:
                execution = self._executions[record.execution_id]
                execution.status = "pending_approval"
                execution.approvals = [*execution.approvals, record.id]
                execution.updated_at = time.time()
                self._executions[execution.id] = execution
                self._persist_executions()
        return ApprovalRecord(**record.model_dump())

    def update_approval(self, approval_id: str, *, status: str, outcome_summary: str = "") -> Optional[ApprovalRecord]:
        with self._lock:
            record = self._approvals.get(approval_id)
            if record is None:
                return None
            record.status = status
            record.outcome_summary = outcome_summary
            record.updated_at = time.time()
            record.decided_at = time.time()
            self._approvals[approval_id] = record
            self._persist_approvals()
            thread_state = self._thread_state.get(record.thread_id) or ThreadSessionState(thread_id=record.thread_id)
            if thread_state.pending_approval_id == approval_id:
                thread_state.pending_approval_id = ""
                thread_state.updated_at = time.time()
                self._thread_state[record.thread_id] = thread_state
                self._persist_thread_state()
            if record.execution_id and record.execution_id in self._executions:
                execution = self._executions[record.execution_id]
                if status in {"approved", "auto_approved"}:
                    execution.status = "running"
                elif status in {"canceled", "rejected"}:
                    execution.status = "canceled"
                    execution.success = False
                    execution.error = outcome_summary or "Approval canceled"
                    execution.completed_at = time.time()
                execution.updated_at = time.time()
                self._executions[execution.id] = execution
                self._persist_executions()
            return ApprovalRecord(**record.model_dump())

    def get_approval(self, approval_id: str) -> Optional[ApprovalRecord]:
        with self._lock:
            record = self._approvals.get(approval_id)
            return ApprovalRecord(**record.model_dump()) if record else None

    def get_pending_approval(self, thread_id: Optional[str]) -> Optional[ApprovalRecord]:
        state = self.get_thread_state(thread_id)
        if not state.pending_approval_id:
            return None
        return self.get_approval(state.pending_approval_id)

    def list_approvals(self, thread_id: Optional[str] = None, status: Optional[str] = None, limit: int = 50) -> list[ApprovalRecord]:
        key = str(thread_id or "").strip()
        with self._lock:
            items = list(self._approvals.values())
        if key:
            items = [item for item in items if item.thread_id == key]
        if status:
            items = [item for item in items if item.status == status]
        items.sort(key=lambda item: item.created_at, reverse=True)
        return [ApprovalRecord(**item.model_dump()) for item in items[:limit]]

    def write_trace(self, trace_id: str, payload: dict[str, Any]) -> str:
        trace_path = TRACE_DIR / f"{trace_id}.json"
        trace_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return str(trace_path)

    def read_trace(self, trace_id: str) -> Optional[dict[str, Any]]:
        trace_path = TRACE_DIR / f"{trace_id}.json"
        if not trace_path.exists():
            return None
        try:
            return json.loads(trace_path.read_text(encoding="utf-8"))
        except Exception:
            return None


_state_store: Optional[StateStore] = None


def get_state_store() -> StateStore:
    global _state_store
    if _state_store is None:
        _state_store = StateStore()
    return _state_store
