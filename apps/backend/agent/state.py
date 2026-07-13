from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
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
_PROCESS_LOCK_HANDLE = None
_PROCESS_LOCK_PATH: Optional[Path] = None
APPROVALS_PATH = PHASE3_DIR / "approvals.json"
EXECUTIONS_PATH = PHASE3_DIR / "executions.json"
THREAD_STATE_PATH = PHASE3_DIR / "thread_state.json"
TRACE_DIR = PHASE3_DIR / "traces"

EXECUTION_STATES = frozenset({
    "ready",
    "needs_clarification",
    "needs_permission",
    "in_progress",
    "partially_complete",
    "blocked",
    "failed",
    "retryable",
    "cancelled",
    "complete",
})

ITEM_STATUSES = frozenset({
    "pending", "started", "streaming", "awaiting_approval", "complete",
    "partial", "blocked", "failed", "cancelled", "superseded",
})


class RuntimeItem(BaseModel):
    """Typed, turn-owned activity. Payload stays structured until presentation."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""
    session_id: str = "default"
    turn_id: str
    item_type: str
    status: str = "pending"
    payload: dict[str, Any] = Field(default_factory=dict)
    tool_run_id: str = ""
    model_id: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class ToolRunRecord(BaseModel):
    """Durable identity and terminal truth for one exact tool invocation."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""
    session_id: str = "default"
    turn_id: str
    item_id: str = ""
    tool_name: str
    action_id: str = ""
    approval_id: str = ""
    status: str = "started"
    canonical_arguments: dict[str, Any] = Field(default_factory=dict)
    canonical_arguments_hash: str = ""
    outcome: dict[str, Any] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)
    retry_of: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    completed_at: Optional[float] = None


class RuntimeEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""
    session_id: str = "default"
    turn_id: str = ""
    item_id: str = ""
    tool_run_id: str = ""
    model_id: str = ""
    event_type: str
    status: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)


class ApprovalRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    thread_id: str = "default"
    session_id: str = "default"
    project_id: str = ""
    original_turn_id: str = ""
    tool_run_id: str = ""
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
    execution_context: dict[str, Any] = Field(default_factory=dict)
    action_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    plan_id: str = ""
    canonical_arguments_hash: str = ""
    required_capabilities: list[str] = Field(default_factory=list)
    permission_level: str = "modify"
    constraints: list[str] = Field(default_factory=list)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    # Optimistic-concurrency guard for filesystem mutations. This captures all
    # relevant source/destination identities and is rechecked before execution.
    source_precondition: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    decided_at: Optional[float] = None
    outcome_summary: str = ""


class ExecutionRecord(BaseModel):
    """One user Turn; the established name is retained for migration compatibility."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: str = "query"
    thread_id: str = "default"
    session_id: str = "default"
    project_id: str = ""
    source: str = "web"
    status: str = "running"
    query: str = ""
    workspace_id: str = ""
    active_project_id: str = ""
    runtime_provider: str = ""
    model_id: str = ""
    model_snapshot: dict[str, Any] = Field(default_factory=dict)
    context_budget: dict[str, Any] = Field(default_factory=dict)
    intent: str = ""
    mode: str = "chat"
    phase: str = ""
    constraints: list[str] = Field(default_factory=list)
    verification: dict[str, Any] = Field(default_factory=dict)
    terminal_status: str = "started"
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


class ToolOutcome(BaseModel):
    """Normalized result from the single runtime tool-authority boundary."""

    tool_name: str
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action_id: str = ""
    execution_id: str = ""
    project_id: str = ""
    session_id: str = "default"
    turn_id: str = ""
    success: bool = False
    status: str = "failed"
    output: str = ""
    error_code: str = ""
    error_message: str = ""
    retryable: bool = False
    policy_block: bool = False
    verification: dict[str, Any] = Field(default_factory=dict)
    started_at: float = Field(default_factory=time.time)
    completed_at: float = Field(default_factory=time.time)

    def user_text(self) -> str:
        return self.output or self.error_message or self.status.replace("_", " ")


class ProjectLedgerEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = Field(default_factory=time.time)
    thread_id: str = "default"
    session_id: str = "default"
    project_id: str = ""
    project_path: str = ""
    objective: str = ""
    category: str = "action"
    summary: str = ""
    tool: str = ""
    workflow: str = ""
    status: str = "complete"
    success: Optional[bool] = None
    verified: bool = False
    execution_id: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)
    unresolved: str = ""


class ThreadSessionState(BaseModel):
    """Authoritative durable execution context for one conversation thread."""

    thread_id: str
    session_id: str = ""
    title: str = ""
    workspace_id: str = ""
    active_project_id: str = ""
    workspace_root: str = ""
    project_path: str = ""
    objective: str = ""
    current_subject: str = ""
    mode: str = "chat"
    phase: str = ""
    required_capabilities: list[str] = Field(default_factory=list)
    available_capabilities: list[str] = Field(default_factory=list)
    allowed_tool_names: list[str] = Field(default_factory=list)
    permissions: dict[str, bool] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    completed_actions: list[dict[str, Any]] = Field(default_factory=list)
    pending_actions: list[dict[str, Any]] = Field(default_factory=list)
    failed_actions: list[dict[str, Any]] = Field(default_factory=list)
    plan_steps: list[dict[str, Any]] = Field(default_factory=list)
    retry_target: dict[str, Any] = Field(default_factory=dict)
    last_tool_outcome: dict[str, Any] = Field(default_factory=dict)
    operation_details: dict[str, Any] = Field(default_factory=dict)
    continuity_notice: str = ""
    execution_status: str = "ready"
    safest_next_action: str = ""
    current_execution_id: str = ""
    active_turn_id: str = ""
    selected_model_id: str = ""
    model_profile: dict[str, Any] = Field(default_factory=dict)
    context_budget: dict[str, Any] = Field(default_factory=dict)
    unfinished_workflow: dict[str, Any] = Field(default_factory=dict)
    # Assistant-offered next action awaiting user confirmation (not an approval gate).
    # Shape: origin_execution_id, kind, action, subject, status, assistant_text, created_at
    pending_offered_action: dict[str, Any] = Field(default_factory=dict)
    # Last assistant checkable claim for verify/double-check follow-ups (not offered actions).
    # Shape: text, subject, origin_execution_id, provisional, created_at
    last_assistant_claim: dict[str, Any] = Field(default_factory=dict)
    pending_approval_id: str = ""
    last_execution_id: str = ""
    last_trace_id: str = ""
    runtime_provider: str = ""
    intent: str = ""
    verification: dict[str, Any] = Field(default_factory=dict)
    terminal_status: str = "started"
    ledger: list[ProjectLedgerEntry] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)


def _acquire_phase3_process_lock(root: Path) -> None:
    """Enforce a single backend writer for durable phase3 JSON (desktop single-process rule).

    Atomic rename alone does not prevent two processes from last-writer-wins clobbering
    thread_state / tool_runs. Hold an exclusive lock file for the process lifetime.
    """
    global _PROCESS_LOCK_HANDLE, _PROCESS_LOCK_PATH
    if _PROCESS_LOCK_HANDLE is not None:
        return
    lock_path = Path(root) / ".echospeak_state.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            handle.write("0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\nstarted={time.time()}\n")
        handle.flush()
        _PROCESS_LOCK_HANDLE = handle
        _PROCESS_LOCK_PATH = lock_path

        def _release() -> None:
            global _PROCESS_LOCK_HANDLE
            try:
                if _PROCESS_LOCK_HANDLE is not None:
                    if os.name == "nt":
                        import msvcrt

                        _PROCESS_LOCK_HANDLE.seek(0)
                        msvcrt.locking(_PROCESS_LOCK_HANDLE.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(_PROCESS_LOCK_HANDLE.fileno(), fcntl.LOCK_UN)
                    _PROCESS_LOCK_HANDLE.close()
            except Exception:
                pass
            _PROCESS_LOCK_HANDLE = None

        atexit.register(_release)
    except Exception as exc:
        try:
            handle.close()
        except Exception:
            pass
        # Fail closed for multi-writer: second process must not share durable maps.
        raise RuntimeError(
            f"EchoSpeak durable state is already locked by another process ({lock_path}). "
            f"Run a single backend instance. ({exc})"
        ) from exc


class StateStore:
    def __init__(self, root: Optional[Path] = None) -> None:
        self._lock = RLock()
        self.root = Path(root or PHASE3_DIR)
        self.approvals_path = self.root / "approvals.json"
        self.executions_path = self.root / "executions.json"
        self.thread_state_path = self.root / "thread_state.json"
        self.trace_dir = self.root / "traces"
        self.items_path = self.root / "items.json"
        self.tool_runs_path = self.root / "tool_runs.json"
        self.events_path = self.root / "events.json"
        self.schema_path = self.root / "runtime_schema.json"
        self.quarantine_path = self.root / "quarantine.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        # Skip lock in pytest / explicit multi-writer test harnesses.
        if not os.environ.get("ECHOSPEAK_ALLOW_MULTI_WRITER"):
            try:
                _acquire_phase3_process_lock(self.root)
            except RuntimeError:
                # Re-raise clearly for operators; tests set ECHOSPEAK_ALLOW_MULTI_WRITER.
                if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
                    pass
                else:
                    raise
        self._backup_legacy_state_once()
        self._approvals: dict[str, ApprovalRecord] = {}
        self._executions: dict[str, ExecutionRecord] = {}
        self._thread_state: dict[str, ThreadSessionState] = {}
        self._items: dict[str, RuntimeItem] = {}
        self._tool_runs: dict[str, ToolRunRecord] = {}
        self._events: list[RuntimeEvent] = []
        self._quarantine: list[dict[str, Any]] = []
        self._load_all()

    def _backup_legacy_state_once(self) -> None:
        """Back up durable records before the first ownership-schema migration."""
        if self.schema_path.exists():
            return
        candidates = [self.root / name for name in ("approvals.json", "executions.json", "thread_state.json")]
        existing = [path for path in candidates if path.exists()]
        if existing:
            backup = self.root / "migration-backups" / str(int(time.time()))
            backup.mkdir(parents=True, exist_ok=True)
            for path in existing:
                shutil.copy2(path, backup / path.name)
        self.schema_path.write_text(
            json.dumps({"version": 2, "migrated_at": time.time()}, indent=2) + "\n",
            encoding="utf-8",
        )

    def _read_json(self, path: Path) -> Any:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._fail_corrupt_state(path, exc, kind="parse/read")

    def _fail_corrupt_state(self, path: Path, error: Exception, *, kind: str) -> None:
        """Preserve one bad authority file, write recovery guidance, then stop."""
        quarantine_dir = self.root / "corrupt-state" / f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        quarantine_copy = quarantine_dir / path.name
        diagnostic = quarantine_dir / "RECOVERY.txt"
        quarantine_note = "quarantine copy could not be created"
        try:
            quarantine_dir.mkdir(parents=True, exist_ok=False)
            shutil.copy2(path, quarantine_copy)
            diagnostic.write_text(
                "EchoSpeak durable-state recovery\n\n"
                f"Authoritative file: {path}\nQuarantine copy: {quarantine_copy}\n"
                f"Failure class: {kind}\nError: {error}\n\nManual recovery:\n"
                "1. Keep the backend stopped.\n"
                "2. Repair the authoritative file as valid schema-compatible JSON, or restore the matching file from migration-backups.\n"
                "3. Keep this quarantine directory until the recovered state has been inspected.\n"
                "4. Restart one backend instance.\n",
                encoding="utf-8",
            )
            quarantine_note = f"quarantine copy: {quarantine_copy}; recovery guide: {diagnostic}"
        except Exception as quarantine_exc:
            quarantine_note = f"quarantine failed: {quarantine_exc}"
        raise RuntimeError(
            f"EchoSpeak durable state is unreadable: {path}. "
            f"The authoritative file was not overwritten; {quarantine_note}. "
            f"Restore or repair it before restarting. ({error})"
        ) from error

    def _require_mapping(self, path: Path, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        self._fail_corrupt_state(path, ValueError("authoritative JSON root must be an object"), kind="schema")
        raise AssertionError("unreachable")

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass

    def _load_all(self) -> None:
        approvals_raw = self._require_mapping(self.approvals_path, self._read_json(self.approvals_path))
        for approval_id, data in approvals_raw.items():
            try:
                record = ApprovalRecord(**data)
                if record.id != approval_id:
                    raise ValueError("ApprovalRecord id does not match its authority key")
                self._approvals[approval_id] = record
            except Exception as exc:
                self._fail_corrupt_state(self.approvals_path, exc, kind=f"ApprovalRecord schema ({approval_id})")
        executions_raw = self._require_mapping(self.executions_path, self._read_json(self.executions_path))
        for execution_id, data in executions_raw.items():
            try:
                record = ExecutionRecord(**data)
                if record.id != execution_id:
                    raise ValueError("ExecutionRecord id does not match its authority key")
                self._executions[execution_id] = record
            except Exception as exc:
                self._fail_corrupt_state(self.executions_path, exc, kind=f"ExecutionRecord schema ({execution_id})")
        thread_raw = self._require_mapping(self.thread_state_path, self._read_json(self.thread_state_path))
        for thread_id, data in thread_raw.items():
            try:
                state = ThreadSessionState(**data)
                if state.thread_id != thread_id:
                    raise ValueError("ThreadSessionState id does not match its authority key")
                self._thread_state[thread_id] = state
            except Exception as exc:
                self._fail_corrupt_state(self.thread_state_path, exc, kind=f"ThreadSessionState schema ({thread_id})")
        items_raw = self._require_mapping(self.items_path, self._read_json(self.items_path))
        for key, data in items_raw.items():
            try:
                record = RuntimeItem(**data)
                if record.id != key:
                    raise ValueError("RuntimeItem id does not match its authority key")
                self._items[key] = record
            except Exception as exc:
                self._fail_corrupt_state(self.items_path, exc, kind=f"RuntimeItem schema ({key})")
        tool_runs_raw = self._require_mapping(self.tool_runs_path, self._read_json(self.tool_runs_path))
        for key, data in tool_runs_raw.items():
            try:
                record = ToolRunRecord(**data)
                if record.id != key:
                    raise ValueError("ToolRunRecord id does not match its authority key")
                self._tool_runs[key] = record
            except Exception as exc:
                self._fail_corrupt_state(self.tool_runs_path, exc, kind=f"ToolRunRecord schema ({key})")
        events_raw = self._read_json(self.events_path)
        if self.events_path.exists() and not isinstance(events_raw, list):
            self._fail_corrupt_state(
                self.events_path,
                ValueError("authoritative event JSON root must be an array"),
                kind="RuntimeEvent schema",
            )
        for data in events_raw if isinstance(events_raw, list) else []:
            try:
                self._events.append(RuntimeEvent(**data))
            except Exception as exc:
                self._fail_corrupt_state(self.events_path, exc, kind="RuntimeEvent schema")
        self._clear_legacy_implicit_self_scope()

    def _clear_legacy_implicit_self_scope(self) -> None:
        """Remove the old FILE_TOOL_ROOT-as-Project leak without touching real Projects."""
        try:
            repo_root = Path(__file__).resolve().parents[3]
        except Exception:
            return
        changed = False
        approvals_changed = False
        for state in self._thread_state.values():
            if state.active_project_id:
                continue
            raw_path = str(state.project_path or state.workspace_root or "").strip()
            if not raw_path:
                continue
            try:
                is_self_root = Path(raw_path).expanduser().resolve() == repo_root
            except Exception:
                is_self_root = False
            if not is_self_root:
                continue
            approval_id = str(state.pending_approval_id or "").strip()
            approval = self._approvals.get(approval_id) if approval_id else None
            if approval is not None and approval.status == "pending":
                approval.status = "canceled"
                approval.outcome_summary = "Canceled during migration: Session had an implicit EchoSpeak repository scope"
                approval.updated_at = approval.decided_at = time.time()
                approvals_changed = True
            state.project_path = ""
            state.workspace_root = ""
            state.pending_approval_id = ""
            state.pending_actions = []
            state.retry_target = {}
            state.unfinished_workflow = {}
            state.execution_status = "ready"
            state.safest_next_action = ""
            state.updated_at = time.time()
            changed = True
        if approvals_changed:
            self._persist_approvals()
        if changed:
            self._persist_thread_state()

    def _persist_approvals(self) -> None:
        self._write_json(self.approvals_path, {key: value.model_dump() for key, value in self._approvals.items()})

    def _persist_executions(self) -> None:
        self._write_json(self.executions_path, {key: value.model_dump() for key, value in self._executions.items()})

    def _persist_thread_state(self) -> None:
        self._write_json(self.thread_state_path, {key: value.model_dump() for key, value in self._thread_state.items()})

    def _persist_runtime_activity(self) -> None:
        self._write_json(self.items_path, {key: value.model_dump() for key, value in self._items.items()})
        self._write_json(self.tool_runs_path, {key: value.model_dump() for key, value in self._tool_runs.items()})
        self._write_json(self.events_path, [event.model_dump() for event in self._events[-2000:]])

    def add_item(self, *, turn_id: str, item_type: str, status: str = "pending",
                 payload: Optional[dict[str, Any]] = None, session_id: str = "default",
                 project_id: str = "", tool_run_id: str = "", model_id: str = "") -> RuntimeItem:
        if status not in ITEM_STATUSES:
            status = "failed"
        item = RuntimeItem(turn_id=turn_id, item_type=item_type, status=status, payload=payload or {},
                           session_id=session_id or "default", project_id=project_id,
                           tool_run_id=tool_run_id, model_id=model_id)
        with self._lock:
            self._items[item.id] = item
            self._events.append(RuntimeEvent(project_id=project_id, session_id=item.session_id,
                                             turn_id=turn_id, item_id=item.id, tool_run_id=tool_run_id,
                                             model_id=model_id, event_type=f"item.{item_type}",
                                             status=status, payload=item.payload))
            self._persist_runtime_activity()
        return RuntimeItem(**item.model_dump())

    # Terminal ToolRun statuses — once set, finish_tool_run is idempotent.
    TOOL_RUN_TERMINAL = frozenset({
        "complete", "completed", "success", "failed", "error",
        "blocked", "cancelled", "canceled", "interrupted",
        "approval_required", "policy_block",
    })

    def create_tool_run(self, *, turn_id: str, tool_name: str, session_id: str = "default",
                        project_id: str = "", run_id: str = "", item_id: str = "",
                        canonical_arguments: Optional[dict[str, Any]] = None,
                        canonical_arguments_hash: str = "", action_id: str = "",
                        approval_id: str = "", retry_of: str = "") -> ToolRunRecord:
        rid = str(run_id or "").strip() or str(uuid.uuid4())
        with self._lock:
            existing = self._tool_runs.get(rid)
            if existing is not None:
                # Never re-open a terminal ToolRun (trailing start after end).
                if str(existing.status or "").lower() in self.TOOL_RUN_TERMINAL:
                    return ToolRunRecord(**existing.model_dump())
                # Already started: keep first record identity.
                return ToolRunRecord(**existing.model_dump())
            record = ToolRunRecord(id=rid, turn_id=turn_id, tool_name=tool_name,
                                   session_id=session_id or "default", project_id=project_id, item_id=item_id,
                                   canonical_arguments=canonical_arguments or {},
                                   canonical_arguments_hash=canonical_arguments_hash, action_id=action_id,
                                   approval_id=approval_id, retry_of=retry_of)
            self._tool_runs[record.id] = record
            self._events.append(RuntimeEvent(project_id=project_id, session_id=record.session_id,
                                             turn_id=turn_id, item_id=item_id, tool_run_id=record.id,
                                             event_type="tool_run.started", status="started",
                                             payload={"tool_name": tool_name}))
            self._persist_runtime_activity()
            return ToolRunRecord(**record.model_dump())

    def finish_tool_run(self, run_id: str, outcome: ToolOutcome | dict[str, Any]) -> Optional[ToolRunRecord]:
        """Apply terminal outcome once. Trailing events are ignored (no success→failed flip)."""
        with self._lock:
            record = self._tool_runs.get(str(run_id or "").strip())
            if record is None:
                return None
            already = str(record.status or "").lower()
            if already in self.TOOL_RUN_TERMINAL:
                # Idempotent: keep first terminal truth. Never let a later callback
                # demote a successful ToolRun or open a second terminal story.
                return ToolRunRecord(**record.model_dump())
            payload = outcome.model_dump() if isinstance(outcome, ToolOutcome) else dict(outcome or {})
            new_status = str(payload.get("status") or ("complete" if payload.get("success") else "failed")).lower()
            if new_status in {"completed", "success"}:
                new_status = "complete"
            record.outcome = payload
            record.verification = dict(payload.get("verification") or {})
            record.status = new_status
            record.updated_at = record.completed_at = time.time()
            self._events.append(RuntimeEvent(project_id=record.project_id, session_id=record.session_id,
                                             turn_id=record.turn_id, item_id=record.item_id,
                                             tool_run_id=record.id, event_type="tool_run.finished",
                                             status=record.status, payload=payload))
            self._persist_runtime_activity()
            return ToolRunRecord(**record.model_dump())

    def attach_tool_verification(self, run_id: str, verification: dict[str, Any]) -> Optional[ToolRunRecord]:
        """Attach post-action verification without changing terminal outcome truth."""
        with self._lock:
            record = self._tool_runs.get(str(run_id or "").strip())
            if record is None:
                return None
            record.verification = {**dict(record.verification or {}), **dict(verification or {})}
            record.updated_at = time.time()
            self._tool_runs[record.id] = record
            self._events.append(
                RuntimeEvent(
                    project_id=record.project_id,
                    session_id=record.session_id,
                    turn_id=record.turn_id,
                    item_id=record.item_id,
                    tool_run_id=record.id,
                    event_type="tool_run.verified",
                    status="complete" if bool(record.verification.get("verified")) else "failed",
                    payload=dict(record.verification),
                )
            )
            self._persist_runtime_activity()
            return ToolRunRecord(**record.model_dump())

    def list_items(self, turn_id: str) -> list[RuntimeItem]:
        with self._lock:
            return [RuntimeItem(**item.model_dump()) for item in self._items.values() if item.turn_id == turn_id]

    def list_tool_runs(self, turn_id: str) -> list[ToolRunRecord]:
        with self._lock:
            return [ToolRunRecord(**run.model_dump()) for run in self._tool_runs.values() if run.turn_id == turn_id]

    def list_tool_runs_for_session(self, session_id: str, limit: int = 120) -> list[ToolRunRecord]:
        """ToolRuns for one Session only (never bleed across sessions)."""
        return self.query_tool_runs(session_id=session_id, limit=limit)

    def query_tool_runs(
        self,
        *,
        session_id: str = "",
        execution_id: str = "",
        project_id: str = "",
        limit: int = 120,
    ) -> list[ToolRunRecord]:
        """Canonical ToolRun query for Session / Execution / Project hydration.

        Preserves parent/child identity (retry_of, action_id), terminal status,
        errors, approvals, and verification for refresh/restart.
        """
        session_key = str(session_id or "").strip()
        exec_key = str(execution_id or "").strip()
        project_key = str(project_id or "").strip()
        with self._lock:
            turn_ids: set[str] = set()
            if session_key:
                turn_ids = {
                    ex.id
                    for ex in self._executions.values()
                    if str(ex.thread_id or "") == session_key
                    or str(getattr(ex, "session_id", "") or "") == session_key
                }
            items: list[ToolRunRecord] = []
            seen: set[str] = set()
            for run in self._tool_runs.values():
                if exec_key and str(run.turn_id or "") != exec_key:
                    continue
                if project_key:
                    run_project = str(run.project_id or "").strip()
                    if run_project and run_project != project_key:
                        continue
                    if not run_project:
                        # Resolve via execution when ToolRun lacks project pin
                        ex = self._executions.get(str(run.turn_id or ""))
                        ex_project = str(getattr(ex, "project_id", "") or getattr(ex, "active_project_id", "") or "")
                        if ex_project and ex_project != project_key:
                            continue
                if session_key:
                    run_session = str(run.session_id or "").strip()
                    if run_session and run_session != session_key and str(run.turn_id or "") not in turn_ids:
                        continue
                    if not run_session and str(run.turn_id or "") not in turn_ids:
                        continue
                if run.id in seen:
                    continue
                items.append(ToolRunRecord(**run.model_dump()))
                seen.add(run.id)
        items.sort(key=lambda r: float(r.created_at or 0), reverse=True)
        return items[: max(1, int(limit or 120))]

    def project_tool_run(self, run: ToolRunRecord) -> dict[str, Any]:
        """Safe ToolRun projection for API/history (redacted args, truncated outcomes)."""
        payload = run.model_dump()
        payload["canonical_arguments"] = self._redact_tool_arguments(dict(run.canonical_arguments or {}))
        outcome = dict(payload.get("outcome") or {})
        out_text = str(outcome.get("output") or "")
        if len(out_text) > 4000:
            outcome["output"] = out_text[:4000] + "…"
        err_text = str(outcome.get("error_message") or "")
        if len(err_text) > 1000:
            outcome["error_message"] = err_text[:1000] + "…"
        payload["outcome"] = outcome
        # Parent/child and linkage fields for UI correlation
        payload["parent_tool_run_id"] = str(run.retry_of or "")
        payload["has_children"] = any(
            str(other.retry_of or "") == run.id for other in self._tool_runs.values()
        )
        return payload

    def runtime_projection(self, session_id: str) -> dict[str, Any]:
        state = self.get_thread_state(session_id)
        current_id = state.active_turn_id or state.current_execution_id
        current = self.get_execution(current_id) if current_id else None
        historical = [turn for turn in self.list_executions(session_id, limit=50) if turn.id != current_id]
        return {
            "current_turn": ({**current.model_dump(), "items": [i.model_dump() for i in self.list_items(current.id)],
                              "tool_runs": [r.model_dump() for r in self.list_tool_runs(current.id)]} if current else None),
            "session_summary": state.model_dump(),
            "project_summary": {"project_id": state.active_project_id, "workspace_root": state.workspace_root,
                                "project_path": state.project_path},
            "model_summary": {"model_id": state.selected_model_id, "provider": state.runtime_provider,
                              "profile": state.model_profile, "context_budget": state.context_budget},
            "historical_turns": [turn.model_dump() for turn in historical],
        }

    @staticmethod
    def _redact_tool_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        """Display-safe tool args — never leak secrets into history hydration."""
        sensitive = (
            "password", "secret", "token", "api_key", "apikey", "authorization",
            "auth", "credential", "private_key", "access_key", "cookie",
        )
        out: dict[str, Any] = {}
        for key, value in dict(arguments or {}).items():
            low = str(key or "").strip().lower()
            if any(token in low for token in sensitive):
                out[key] = "[redacted]"
            elif isinstance(value, str) and len(value) > 2000:
                out[key] = value[:2000] + "…"
            else:
                out[key] = value
        return out

    def _research_from_tool_run(self, run: ToolRunRecord) -> Optional[dict[str, Any]]:
        """Best-effort research projection from durable ToolRun outcome (no prose parse of final message)."""
        name = str(run.tool_name or "").strip().lower()
        if name not in {"web_search", "sports_live", "browse_task"}:
            return None
        outcome = dict(run.outcome or {})
        output = str(outcome.get("output") or "")
        args = dict(run.canonical_arguments or {})
        query = str(args.get("q") or args.get("query") or args.get("input") or "").strip()
        evidence: list[dict[str, Any]] = []
        # Prefer structured evidence stored on the outcome when present.
        raw_evidence = outcome.get("evidence") or outcome.get("sources") or []
        if isinstance(raw_evidence, list):
            for index, item in enumerate(raw_evidence[:24]):
                if not isinstance(item, dict):
                    continue
                evidence.append({
                    "id": str(item.get("id") or f"{run.id}-ev-{index + 1}"),
                    "kind": str(item.get("kind") or "search_result"),
                    "position": int(item.get("position") or index + 1),
                    "query": str(item.get("query") or query),
                    "title": str(item.get("title") or "Source")[:240],
                    "url": str(item.get("url") or "")[:500],
                    "domain": str(item.get("domain") or "")[:120],
                    "summary": str(item.get("summary") or item.get("snippet") or "")[:500],
                    "snippet": str(item.get("snippet") or item.get("summary") or "")[:500],
                    "content": str(item.get("content") or item.get("snippet") or "")[:500],
                    "page_title": str(item.get("page_title") or "")[:240],
                    "published_raw": str(item.get("published_raw") or ""),
                    "published_at": item.get("published_at"),
                    "recency_bucket": str(item.get("recency_bucket") or "unknown"),
                })
        # Lightweight URL scrape from grounded output when structured evidence missing.
        if not evidence and output:
            import re as _re
            urls = _re.findall(r"https?://[^\s\]\)\"']+", output)[:12]
            for index, url in enumerate(urls):
                domain = ""
                try:
                    from urllib.parse import urlparse
                    domain = (urlparse(url).hostname or "").replace("www.", "")
                except Exception:
                    domain = ""
                evidence.append({
                    "id": f"{run.id}-url-{index + 1}",
                    "kind": "search_result",
                    "position": index + 1,
                    "query": query,
                    "title": domain or url[:80],
                    "url": url[:500],
                    "domain": domain,
                    "summary": "",
                    "snippet": "",
                    "content": "",
                    "page_title": "",
                    "published_raw": "",
                    "published_at": None,
                    "recency_bucket": "unknown",
                })
        if not query and not evidence:
            return None
        return {
            "id": f"research-{run.id}",
            "tool": run.tool_name,
            "at": float(run.completed_at or run.updated_at or run.created_at or 0),
            "query": query,
            "mode": "general",
            "recency_intent": False,
            "evidence_count": len(evidence),
            "evidence": evidence,
        }

    def turn_projection(self, turn_id: str) -> Optional[dict[str, Any]]:
        """Complete durable projection for one Turn (execution_id)."""
        execution = self.get_execution(turn_id)
        if execution is None:
            return None
        items = self.list_items(execution.id)
        tool_runs = self.list_tool_runs(execution.id)
        tool_runs.sort(key=lambda r: float(r.created_at or 0))
        items.sort(key=lambda i: float(i.created_at or 0))

        approvals = [
            a.model_dump()
            for a in self.list_approvals(execution.thread_id, limit=100)
            if str(a.execution_id or "") == execution.id
            or str(a.original_turn_id or "") == execution.id
            or str(a.tool_run_id or "") in {r.id for r in tool_runs}
        ]

        safe_runs: list[dict[str, Any]] = []
        research_runs: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        terminal_runs: list[dict[str, Any]] = []
        for run in tool_runs:
            payload = run.model_dump()
            payload["canonical_arguments"] = self._redact_tool_arguments(dict(run.canonical_arguments or {}))
            # Truncate large outcomes for history transport; keep status + summary.
            outcome = dict(payload.get("outcome") or {})
            out_text = str(outcome.get("output") or "")
            if len(out_text) > 4000:
                outcome["output"] = out_text[:4000] + "…"
            err_text = str(outcome.get("error_message") or "")
            if len(err_text) > 1000:
                outcome["error_message"] = err_text[:1000] + "…"
            payload["outcome"] = outcome
            safe_runs.append(payload)

            research = self._research_from_tool_run(run)
            if research:
                research_runs.append(research)

            tname = str(run.tool_name or "")
            args = dict(payload.get("canonical_arguments") or {})
            if tname in {"file_read", "file_write", "file_list", "file_mkdir", "file_delete", "file_move", "file_copy", "artifact_write"}:
                files.append({
                    "tool_run_id": run.id,
                    "tool": tname,
                    "path": str(args.get("path") or args.get("filename") or args.get("src") or ""),
                    "destination": str(args.get("dst") or ""),
                    "status": run.status,
                    "success": bool((run.outcome or {}).get("success")) if run.outcome else run.status in {"complete", "success"},
                    "at": float(run.completed_at or run.updated_at or run.created_at or 0),
                })
            if tname == "terminal_run":
                terminal_runs.append({
                    "tool_run_id": run.id,
                    "command": str(args.get("command") or "")[:500],
                    "cwd": str(args.get("cwd") or ""),
                    "status": run.status,
                    "output_preview": str((run.outcome or {}).get("output") or "")[:1500],
                    "error": str((run.outcome or {}).get("error_message") or ""),
                    "at": float(run.completed_at or run.updated_at or run.created_at or 0),
                })

        # Messages from durable items, with execution fields as fallback.
        messages: list[dict[str, Any]] = []
        for item in items:
            if item.item_type == "user_message":
                text = str((item.payload or {}).get("text") or execution.query or "").strip()
                if text:
                    messages.append({
                        "role": "user",
                        "text": text,
                        "at": float(item.created_at or execution.created_at or 0),
                        "item_id": item.id,
                        "execution_id": execution.id,
                    })
            elif item.item_type == "assistant_message":
                text = str((item.payload or {}).get("text") or "").strip()
                if text:
                    messages.append({
                        "role": "assistant",
                        "text": text,
                        "at": float(item.created_at or execution.completed_at or execution.updated_at or 0),
                        "item_id": item.id,
                        "execution_id": execution.id,
                        "backend_success": (item.payload or {}).get("backend_success"),
                        "error": str((item.payload or {}).get("error") or ""),
                    })
        if not any(m["role"] == "user" for m in messages) and execution.query:
            messages.insert(0, {
                "role": "user",
                "text": execution.query,
                "at": float(execution.created_at or 0),
                "item_id": "",
                "execution_id": execution.id,
            })
        if not any(m["role"] == "assistant" for m in messages) and str(execution.response_preview or "").strip():
            messages.append({
                "role": "assistant",
                "text": str(execution.response_preview or "").strip(),
                "at": float(execution.completed_at or execution.updated_at or 0),
                "item_id": "",
                "execution_id": execution.id,
                "backend_success": execution.success,
                "error": str(execution.error or ""),
            })

        verification_items = [i.model_dump() for i in items if i.item_type == "verification"]
        memory_records = [
            {"item_id": i.id, "status": i.status, **dict(i.payload or {})}
            for i in items if i.item_type == "memory_write"
        ]
        mutators = {"file_write", "file_delete", "file_move", "file_copy", "file_mkdir", "artifact_write", "checkpoint_undo"}
        successful_mutations = [
            run.id for run in tool_runs
            if run.tool_name in mutators and bool((run.outcome or {}).get("success"))
        ]
        blocked_mutations = [
            run.id for run in tool_runs
            if run.tool_name in mutators and not bool((run.outcome or {}).get("success"))
            and str(run.status or "") not in {"started", "pending", "running"}
        ]
        thread_state = self.get_thread_state(execution.thread_id)
        retry_target = dict(thread_state.retry_target or {})
        if str(retry_target.get("execution_id") or "") != execution.id:
            retry_target = {}
        offered_action = dict(thread_state.pending_offered_action or {})
        if str(offered_action.get("origin_execution_id") or offered_action.get("execution_id") or "") != execution.id:
            offered_action = {}
        state_owns_execution = str(thread_state.last_execution_id or thread_state.current_execution_id or "") == execution.id
        execution_projection = {
            "execution_id": execution.id,
            "status": str(execution.status or ""),
            "requested_targets": [
                str(value)
                for approval in approvals if isinstance(approval, dict)
                for value in (
                    (approval.get("kwargs") or {}).get("path")
                    or (approval.get("kwargs") or {}).get("src")
                    or (approval.get("kwargs") or {}).get("filename"),
                    (approval.get("kwargs") or {}).get("dst"),
                )
                if str(value or "").strip()
            ],
            "supporting_reads": [run.id for run in tool_runs if run.tool_name in {"file_read", "file_list"}],
            "proposed_mutations": [str(approval.get("id") or "") for approval in approvals if str(approval.get("tool") or "") in mutators],
            "successful_mutations": successful_mutations,
            "blocked_mutations": blocked_mutations,
            "files_actually_changed": [item for item in files if item.get("success") and item.get("tool") in mutators],
            "retry_target": retry_target,
            "offered_action": offered_action,
            "memory_records": memory_records,
            "unresolved_blockers": [item for item in blocked_mutations],
            "next_action": str(thread_state.safest_next_action or "") if state_owns_execution else "",
        }
        verification = dict(execution.verification or {})
        if verification_items and not verification:
            verification = dict((verification_items[-1].get("payload") or {}))

        # Normalize terminal status for interrupted in-progress turns after browser refresh.
        terminal = str(execution.terminal_status or execution.status or "")
        status = str(execution.status or "")
        open_tools = [r for r in tool_runs if str(r.status or "") in {"started", "pending", "running"}]
        if status in {"running", "in_progress", "started"} and open_tools:
            progress_status = "interrupted"
        elif status in {"running", "in_progress", "started"} and not open_tools and execution.completed_at:
            progress_status = "complete"
        else:
            progress_status = terminal or status or "complete"

        return {
            "execution": execution.model_dump(),
            "execution_id": execution.id,
            "request_id": execution.request_id,
            "status": status,
            "terminal_status": terminal,
            "progress_status": progress_status,
            "created_at": execution.created_at,
            "completed_at": execution.completed_at,
            "messages": messages,
            "items": [i.model_dump() for i in items],
            "tool_runs": safe_runs,
            "research_runs": research_runs,
            "approvals": approvals,
            "verification": verification,
            "progress": {
                "status": progress_status,
                "tools_used": list(execution.tools_used or []),
                "tool_latencies_ms": list(execution.tool_latencies_ms or []),
                "open_tool_runs": len(open_tools),
            },
            "files": files,
            "terminal_runs": terminal_runs,
            "memory_records": memory_records,
            "execution_projection": execution_projection,
            "error": str(execution.error or ""),
            "success": execution.success,
        }

    def session_timeline(self, session_id: str, limit: int = 80) -> dict[str, Any]:
        """Authoritative Session → Turns timeline for page-refresh hydration."""
        key = str(session_id or "default").strip() or "default"
        state = self.get_thread_state(key)
        # list_executions is newest-first; reverse for chronological chat order.
        executions = list(reversed(self.list_executions(key, limit=max(1, int(limit or 80)))))
        turns: list[dict[str, Any]] = []
        for execution in executions:
            projection = self.turn_projection(execution.id)
            if projection:
                turns.append(projection)
        return {
            "session": state.model_dump(),
            "session_id": key,
            "turns": turns,
            "count": len(turns),
        }

    def get_thread_state(self, thread_id: Optional[str]) -> ThreadSessionState:
        key = str(thread_id or "default").strip() or "default"
        with self._lock:
            state = self._thread_state.get(key)
            if state is None:
                state = ThreadSessionState(thread_id=key, session_id=key)
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
                if field == "execution_status" and str(value or "") not in EXECUTION_STATES:
                    value = "blocked"
                setattr(state, field, value or "" if isinstance(getattr(state, field), str) else value)
            state.required_capabilities = list(dict.fromkeys(state.required_capabilities or []))[:24]
            state.available_capabilities = list(dict.fromkeys(state.available_capabilities or []))[:64]
            state.allowed_tool_names = list(dict.fromkeys(state.allowed_tool_names or []))[:128]
            state.constraints = list(dict.fromkeys(state.constraints or []))[:24]
            state.decisions = list(dict.fromkeys(state.decisions or []))[:24]
            state.completed_actions = list(state.completed_actions or [])[-40:]
            state.pending_actions = list(state.pending_actions or [])[-40:]
            state.failed_actions = list(state.failed_actions or [])[-40:]
            state.plan_steps = list(state.plan_steps or [])[-80:]
            state.ledger = list(state.ledger or [])[-120:]
            state.updated_at = time.time()
            self._thread_state[key] = state
            self._persist_thread_state()
            return ThreadSessionState(**state.model_dump())

    def list_thread_states(self) -> list[ThreadSessionState]:
        """Return Session state snapshots for UI projections and maintenance."""
        with self._lock:
            return [ThreadSessionState(**state.model_dump()) for state in self._thread_state.values()]

    def detach_project(self, project_id: str) -> int:
        """Clear a deleted Project from every Session while preserving Session history.

        Also cancels pending approvals and clears retry targets for those Sessions.
        Callers should still clear ActiveWork + preview processes (see agent.activate_project).
        """
        target = str(project_id or "").strip()
        if not target:
            return 0
        changed = 0
        affected: list[str] = []
        with self._lock:
            for state in self._thread_state.values():
                if state.active_project_id != target:
                    continue
                if state.pending_approval_id:
                    approval = self._approvals.get(state.pending_approval_id)
                    if approval is not None and approval.status == "pending":
                        approval.status = "canceled"
                        approval.outcome_summary = "Invalidated because the Project was deleted"
                        approval.updated_at = approval.decided_at = time.time()
                        self._approvals[approval.id] = approval
                state.active_project_id = ""
                state.workspace_root = ""
                state.project_path = ""
                state.pending_approval_id = ""
                state.pending_actions = []
                state.retry_target = {}
                state.objective = ""
                state.updated_at = time.time()
                affected.append(state.thread_id)
                changed += 1
            if changed:
                self._persist_approvals()
                self._persist_thread_state()
        # Outside lock: clear ActiveWork + previews for each affected Session.
        for tid in affected:
            try:
                from agent.active_work import ActiveWorkStore

                ActiveWorkStore().clear(tid)
            except Exception:
                pass
            try:
                from agent.code_workspace import get_preview_manager

                get_preview_manager().stop(tid)
            except Exception:
                pass
        return changed

    def add_ledger_entry(self, thread_id: Optional[str], **payload: Any) -> ProjectLedgerEntry:
        key = str(thread_id or "default").strip() or "default"
        with self._lock:
            state = self._thread_state.get(key) or ThreadSessionState(thread_id=key)
            entry = ProjectLedgerEntry(thread_id=key, **payload)
            state.ledger = [*(state.ledger or []), entry][-120:]
            state.updated_at = time.time()
            self._thread_state[key] = state
            self._persist_thread_state()
            return ProjectLedgerEntry(**entry.model_dump())

    def create_execution(self, **payload: Any) -> ExecutionRecord:
        payload.setdefault("session_id", payload.get("thread_id") or "default")
        payload.setdefault("project_id", payload.get("active_project_id") or "")
        record = ExecutionRecord(**payload)
        with self._lock:
            state = self._thread_state.get(record.thread_id) or ThreadSessionState(
                thread_id=record.thread_id, session_id=record.thread_id
            )
            previous = self._executions.get(state.active_turn_id or state.current_execution_id)
            if previous and previous.status not in {"completed", "failed", "canceled", "superseded"}:
                previous.status = "superseded"
                previous.terminal_status = "superseded"
                previous.completed_at = previous.updated_at = time.time()
            self._executions[record.id] = record
            self._persist_executions()
            # Fresh Turn: wipe transient execution residue so prior blocked tools,
            # unfinished workflows, retries, and last outcomes cannot leak.
            # Explicit continue/retry/confirm re-attaches intentional state after create.
            self.update_thread_state(
                record.thread_id,
                last_execution_id=record.id,
                runtime_provider=record.runtime_provider,
                workspace_id=record.workspace_id,
                active_project_id=record.active_project_id,
                current_execution_id=record.id,
                active_turn_id=record.id,
                execution_status="in_progress",
                selected_model_id=record.model_id,
                model_profile=record.model_snapshot,
                context_budget=record.context_budget,
                completed_actions=[],
                failed_actions=[],
                operation_details={},
                plan_steps=[],
                last_tool_outcome={},
                unfinished_workflow={},
                retry_target={},
                verification={},
                continuity_notice="",
                safest_next_action="",
            )
            self.add_item(turn_id=record.id, item_type="user_message", status="complete",
                          payload={"text": record.query}, session_id=record.session_id,
                          project_id=record.project_id, model_id=record.model_id)
        return ExecutionRecord(**record.model_dump())

    def update_execution(self, execution_id: str, **updates: Any) -> Optional[ExecutionRecord]:
        project_thread = bool(updates.pop("project_thread", True))
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
            if record.status in {"completed", "failed", "canceled", "superseded"}:
                record.terminal_status = {"completed": "complete", "canceled": "cancelled"}.get(
                    record.status, record.status
                )
            self._executions[execution_id] = record
            self._persist_executions()
            if project_thread:
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
                thread_state.pending_actions = [
                    item for item in (thread_state.pending_actions or [])
                    if str(item.get("tool") or "") != record.tool
                ]
                if status in {"approved", "auto_approved"}:
                    thread_state.execution_status = "in_progress"
                    thread_state.safest_next_action = f"Execute approved {record.tool} action"
                elif status in {"canceled", "rejected"}:
                    thread_state.execution_status = "cancelled"
                    thread_state.safest_next_action = "Re-plan or choose a safe alternative"
                elif status == "blocked":
                    thread_state.execution_status = "blocked"
                    thread_state.safest_next_action = "Resolve the policy or configuration block"
                elif status == "failed":
                    thread_state.execution_status = "failed"
                    thread_state.safest_next_action = "Resolve the action failure before retrying"
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
                elif status in {"blocked", "failed"}:
                    execution.status = "failed"
                    execution.success = False
                    execution.error = outcome_summary or f"Approval {status}"
                    execution.completed_at = time.time()
                execution.updated_at = time.time()
                self._executions[execution.id] = execution
                self._persist_executions()
            return ApprovalRecord(**record.model_dump())

    def claim_pending_approval(self, approval_id: str, *, status: str = "consuming") -> Optional[ApprovalRecord]:
        """Atomically claim the Session's exact pending approval without clearing it.

        The pending pointer remains owned by this ApprovalRecord until the
        action reaches a terminal approved/failed/canceled state. Concurrent
        consumers therefore cannot both cross the mutation boundary, while a
        failure can still terminalize the owning Session truthfully.
        """
        key = str(approval_id or "").strip()
        with self._lock:
            record = self._approvals.get(key)
            if record is None or record.status != "pending":
                return None
            thread_state = self._thread_state.get(record.thread_id) or ThreadSessionState(
                thread_id=record.thread_id
            )
            if thread_state.pending_approval_id != key:
                return None
            record.status = str(status or "consuming")
            record.outcome_summary = "Approval claimed for exact action consumption"
            record.updated_at = time.time()
            record.decided_at = time.time()
            self._approvals[key] = record
            thread_state.execution_status = "in_progress"
            thread_state.safest_next_action = f"Finish claimed {record.tool} action"
            thread_state.updated_at = time.time()
            self._thread_state[record.thread_id] = thread_state
            self._persist_approvals()
            self._persist_thread_state()
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
        trace_path = self.trace_dir / f"{trace_id}.json"
        self._write_json(trace_path, payload)
        return str(trace_path)

    def read_trace(self, trace_id: str) -> Optional[dict[str, Any]]:
        trace_path = self.trace_dir / f"{trace_id}.json"
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
