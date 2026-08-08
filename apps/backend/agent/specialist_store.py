"""Durable Echo-owned projection for delegated specialist-runtime work."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from config import DATA_DIR
from agent.specialist_contracts import (
    SPECIALIST_EVENT_TAIL_LIMIT,
    SPECIALIST_SCHEMA_VERSION,
    TERMINAL_SPECIALIST_STATUSES,
    SpecialistEvent,
    SpecialistEventKind,
    SpecialistFailureLayer,
    SpecialistOutcome,
    SpecialistRun,
    SpecialistRunStatus,
)


class SpecialistRunConflictError(RuntimeError):
    pass


class SpecialistRunScopeError(RuntimeError):
    pass


class SpecialistRunStore:
    """One authoritative specialist execution ledger.

    TaskRun remains the semantic owner.  This store owns runtime-session,
    runtime-turn, event, approval, and outcome truth for delegated work only.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or (DATA_DIR / "specialist_runs.json"))
        # Schema v1 wrote a parallel JSONL journal. It is read once for
        # compatibility and then left untouched; schema v2 stores only the
        # bounded Echo projection in the SpecialistRun record.
        self.legacy_events_dir = self.path.parent / "specialist_run_events"
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._runs: dict[str, SpecialistRun] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("specialist run root must be an object")
            version = int(payload.get("schema_version") or 1)
            if version > SPECIALIST_SCHEMA_VERSION:
                raise ValueError(f"unsupported future specialist schema version {version}")
            rows = payload.get("runs") or []
            if not isinstance(rows, list):
                raise ValueError("specialist runs must be an array")
            loaded: dict[str, SpecialistRun] = {}
            reconciled = False
            for item in rows:
                raw = dict(item or {})
                if version < 2 and not raw.get("events"):
                    raw["events"] = [
                        event.model_dump(mode="json")
                        for event in self._load_legacy_event_tail(
                            str(raw.get("id") or ""),
                            pending_approval_ids=list(raw.get("pending_approval_ids") or []),
                        )
                    ]
                run = SpecialistRun.model_validate(raw)
                if run.id in loaded:
                    raise ValueError(f"duplicate SpecialistRun id {run.id}")
                changes: dict[str, Any] = {}
                if version < 2:
                    changes["schema_version"] = SPECIALIST_SCHEMA_VERSION
                    reconciled = True
                # A child process cannot remain authoritatively running across a
                # backend restart.  The stored runtime session id is preserved so
                # a later turn may explicitly resume it.
                if run.status in {
                    SpecialistRunStatus.STARTING,
                    SpecialistRunStatus.RUNNING,
                    SpecialistRunStatus.WAITING_FOR_APPROVAL,
                    SpecialistRunStatus.WAITING_FOR_INPUT,
                }:
                    changes.update({
                        "status": SpecialistRunStatus.DISCONNECTED,
                        "failure_layer": SpecialistFailureLayer.TRANSPORT,
                        "failure_code": "backend_restart",
                        "failure_message": (
                            "The EchoSpeak backend restarted while the specialist "
                            "runtime was active. The specialist session may be resumed."
                        ),
                    })
                    reconciled = True
                if changes:
                    run = run.model_copy(update={
                        **changes,
                        "revision": run.revision + 1,
                        "updated_at": time.time(),
                    })
                loaded[run.id] = run
            self._runs = loaded
            if reconciled:
                self._save()
        except Exception as exc:
            self._fail_closed(exc)

    def _load_legacy_event_tail(
        self,
        run_id: str,
        *,
        pending_approval_ids: list[str],
    ) -> list[SpecialistEvent]:
        """Read a v1 journal once without making it a second active store."""

        path = self.legacy_events_dir / f"{str(run_id)}.jsonl"
        if not path.exists():
            return []
        rows: list[SpecialistEvent] = []
        expected = 1
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = SpecialistEvent.model_validate_json(line)
                if event.run_id != str(run_id):
                    raise ValueError(
                        f"Specialist event journal {path} contains another run id"
                    )
                if event.sequence != expected:
                    raise ValueError(
                        f"Specialist event journal {path} expected sequence "
                        f"{expected}, found {event.sequence}"
                    )
                rows.append(event)
                expected += 1
        if len(rows) <= SPECIALIST_EVENT_TAIL_LIMIT:
            return rows
        pending = set(str(item) for item in pending_approval_ids)
        required = [
            item for item in rows
            if item.runtime_request_id in pending
            and item.kind == SpecialistEventKind.APPROVAL_REQUESTED
        ]
        tail = rows[-SPECIALIST_EVENT_TAIL_LIMIT:]
        by_id = {item.event_id: item for item in [*required, *tail]}
        return sorted(by_id.values(), key=lambda item: item.sequence)[-SPECIALIST_EVENT_TAIL_LIMIT:]

    def _fail_closed(self, error: Exception) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        quarantine = self.path.parent / "quarantine" / f"specialist-runs-{stamp}"
        note = "quarantine was not created"
        try:
            quarantine.mkdir(parents=True, exist_ok=True)
            copy = quarantine / self.path.name
            shutil.copy2(self.path, copy)
            event_copy = ""
            if self.legacy_events_dir.exists():
                copied_events = quarantine / self.legacy_events_dir.name
                shutil.copytree(self.legacy_events_dir, copied_events)
                event_copy = f"\nEvent journals: {copied_events}"
            guide = quarantine / "RECOVERY.txt"
            guide.write_text(
                "EchoSpeak SpecialistRun recovery\n\n"
                f"Authoritative file: {self.path}\n"
                f"Quarantine copy: {copy}\n"
                f"{event_copy}\n"
                f"Error: {error}\n\n"
                "Keep EchoSpeak stopped. Repair or restore the authoritative JSON, "
                "then restart. The original was not overwritten.\n",
                encoding="utf-8",
            )
            note = f"quarantine copy: {copy}; recovery guide: {guide}"
        except Exception as quarantine_error:
            note = f"quarantine failed: {quarantine_error}"
        raise RuntimeError(
            f"SpecialistRun state is unreadable at {self.path}; it was not "
            f"overwritten; {note}. ({error})"
        ) from error

    def _save(self) -> None:
        payload = {
            "schema_version": SPECIALIST_SCHEMA_VERSION,
            "runs": [
                item.model_dump(mode="json")
                for item in sorted(self._runs.values(), key=lambda row: (row.created_at, row.id))
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
            f"Failed to persist SpecialistRun store after retries: {last_error}"
        ) from last_error

    @staticmethod
    def _copy(run: SpecialistRun) -> SpecialistRun:
        return run.model_copy(deep=True)

    @staticmethod
    def _require_scope(
        run: SpecialistRun, *, session_id: str, project_id: str = ""
    ) -> None:
        if run.session_id != str(session_id or ""):
            raise SpecialistRunScopeError("SpecialistRun belongs to another Session")
        if run.project_id != str(project_id or ""):
            raise SpecialistRunScopeError("SpecialistRun belongs to another Project")

    def create(self, run: SpecialistRun) -> SpecialistRun:
        with self._lock:
            if run.id in self._runs:
                raise ValueError("SpecialistRun id already exists")
            self._runs[run.id] = self._copy(run)
            self._save()
            self._changed.notify_all()
            return self._copy(run)

    def get(
        self, run_id: str, *, session_id: str, project_id: str = ""
    ) -> Optional[SpecialistRun]:
        with self._lock:
            run = self._runs.get(str(run_id or ""))
            if run is None:
                return None
            self._require_scope(run, session_id=session_id, project_id=project_id)
            return self._copy(run)

    def get_unscoped(self, run_id: str) -> Optional[SpecialistRun]:
        with self._lock:
            run = self._runs.get(str(run_id or ""))
            return self._copy(run) if run is not None else None

    def list(
        self,
        *,
        session_id: str,
        project_id: str = "",
        task_run_id: str = "",
        limit: int = 100,
    ) -> list[SpecialistRun]:
        with self._lock:
            rows = [
                item for item in self._runs.values()
                if item.session_id == str(session_id or "")
                and item.project_id == str(project_id or "")
                and (not task_run_id or item.task_run_id == str(task_run_id))
            ]
            rows.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
            return [self._copy(item) for item in rows[:max(1, min(int(limit), 500))]]

    def update(
        self,
        run_id: str,
        *,
        expected_revision: Optional[int] = None,
        **changes: Any,
    ) -> SpecialistRun:
        with self._lock:
            current = self._runs.get(str(run_id or ""))
            if current is None:
                raise KeyError("SpecialistRun not found")
            if expected_revision is not None and current.revision != int(expected_revision):
                raise SpecialistRunConflictError(
                    f"SpecialistRun revision changed: expected {expected_revision}, "
                    f"current {current.revision}"
                )
            if current.status in TERMINAL_SPECIALIST_STATUSES and changes.get("status") not in {
                None, current.status,
            }:
                # A deliberate follow-up turn uses reactivate(), never a generic
                # update that silently reopens terminal execution truth.
                raise SpecialistRunConflictError("Terminal SpecialistRun cannot be reopened")
            updated = current.model_copy(update={
                **changes,
                "revision": current.revision + 1,
                "updated_at": time.time(),
            })
            updated = SpecialistRun.model_validate(updated.model_dump(mode="json"))
            self._runs[updated.id] = updated
            self._save()
            self._changed.notify_all()
            return self._copy(updated)

    def reactivate(self, run_id: str) -> SpecialistRun:
        with self._lock:
            current = self._runs.get(str(run_id or ""))
            if current is None:
                raise KeyError("SpecialistRun not found")
            if current.status not in TERMINAL_SPECIALIST_STATUSES | {
                SpecialistRunStatus.DISCONNECTED
            }:
                raise SpecialistRunConflictError("SpecialistRun is already active")
            updated = current.model_copy(update={
                "status": SpecialistRunStatus.STARTING,
                "outcome": None,
                "failure_layer": None,
                "failure_code": "",
                "failure_message": "",
                "runtime_turn_id": "",
                "pending_approval_ids": [],
                "completed_at": None,
                "revision": current.revision + 1,
                "updated_at": time.time(),
            })
            self._runs[current.id] = updated
            self._save()
            self._changed.notify_all()
            return self._copy(updated)

    def append_event(
        self,
        run_id: str,
        *,
        kind: SpecialistEventKind,
        summary: str = "",
        payload: Optional[dict[str, Any]] = None,
        raw_source: str = "",
        runtime_session_id: str = "",
        runtime_turn_id: str = "",
        runtime_item_id: str = "",
        runtime_request_id: str = "",
    ) -> tuple[SpecialistRun, SpecialistEvent]:
        with self._lock:
            current = self._runs.get(str(run_id or ""))
            if current is None:
                raise KeyError("SpecialistRun not found")
            event = SpecialistEvent(
                run_id=current.id,
                sequence=current.next_event_sequence,
                kind=kind,
                runtime_id=current.runtime_id,
                runtime_session_id=runtime_session_id or current.runtime_session_id,
                runtime_turn_id=runtime_turn_id or current.runtime_turn_id,
                runtime_item_id=runtime_item_id,
                runtime_request_id=runtime_request_id,
                summary=summary,
                payload=dict(payload or {}),
                raw_source=raw_source,
            )
            rows = [*current.events, event]
            if len(rows) > SPECIALIST_EVENT_TAIL_LIMIT:
                pending = set(current.pending_approval_ids)
                required = [
                    item for item in rows
                    if item.runtime_request_id in pending
                    and item.kind == SpecialistEventKind.APPROVAL_REQUESTED
                ]
                tail = rows[-SPECIALIST_EVENT_TAIL_LIMIT:]
                by_id = {item.event_id: item for item in [*required, *tail]}
                rows = sorted(
                    by_id.values(), key=lambda item: item.sequence
                )[-SPECIALIST_EVENT_TAIL_LIMIT:]
            updated = current.model_copy(update={
                "events": rows,
                "next_event_sequence": current.next_event_sequence + 1,
                "event_count": current.event_count + 1,
                "revision": current.revision + 1,
                "updated_at": time.time(),
            })
            self._runs[current.id] = updated
            self._save()
            self._changed.notify_all()
            return self._copy(updated), event.model_copy(deep=True)

    def list_events(
        self, run_id: str, *, after: int = 0, limit: int = 500
    ) -> list[SpecialistEvent]:
        with self._lock:
            run = self._runs.get(str(run_id or ""))
            if run is None:
                return []
            rows = [
                item.model_copy(deep=True)
                for item in run.events
                if item.sequence > int(after)
            ]
            return rows[:max(1, min(int(limit), SPECIALIST_EVENT_TAIL_LIMIT))]

    def wait_for_revision(
        self,
        run_id: str,
        *,
        after_revision: int,
        timeout: float = 15.0,
    ) -> Optional[SpecialistRun]:
        """Wait for authoritative state change without polling the JSON file."""

        key = str(run_id or "")
        with self._changed:
            self._changed.wait_for(
                lambda: (
                    key not in self._runs
                    or self._runs[key].revision > int(after_revision)
                ),
                timeout=max(0.1, min(float(timeout), 30.0)),
            )
            current = self._runs.get(key)
            return self._copy(current) if current is not None else None

    def add_pending_approval(self, run_id: str, request_id: str) -> SpecialistRun:
        current = self.get_unscoped(run_id)
        if current is None:
            raise KeyError("SpecialistRun not found")
        ids = list(dict.fromkeys([*current.pending_approval_ids, str(request_id)]))
        return self.update(
            run_id,
            expected_revision=current.revision,
            pending_approval_ids=ids,
            status=SpecialistRunStatus.WAITING_FOR_APPROVAL,
        )

    def resolve_pending_approval(self, run_id: str, request_id: str) -> SpecialistRun:
        current = self.get_unscoped(run_id)
        if current is None:
            raise KeyError("SpecialistRun not found")
        ids = [item for item in current.pending_approval_ids if item != str(request_id)]
        return self.update(
            run_id,
            expected_revision=current.revision,
            pending_approval_ids=ids,
            status=(
                SpecialistRunStatus.WAITING_FOR_APPROVAL
                if ids else SpecialistRunStatus.RUNNING
            ),
        )

    def finish(self, run_id: str, outcome: SpecialistOutcome) -> SpecialistRun:
        with self._lock:
            current = self._runs.get(str(run_id or ""))
            if current is None:
                raise KeyError("SpecialistRun not found")
            if current.status in TERMINAL_SPECIALIST_STATUSES and current.outcome is not None:
                return self._copy(current)
            updated = current.model_copy(update={
                "status": outcome.status,
                "outcome": outcome,
                "failure_layer": outcome.failure_layer,
                "failure_code": outcome.failure_code,
                "failure_message": outcome.failure_message,
                "pending_approval_ids": [],
                "completed_at": outcome.completed_at,
                "revision": current.revision + 1,
                "updated_at": time.time(),
            })
            self._runs[current.id] = updated
            self._save()
            self._changed.notify_all()
            return self._copy(updated)


_STORE: Optional[SpecialistRunStore] = None
_STORE_LOCK = threading.Lock()


def get_specialist_run_store() -> SpecialistRunStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = SpecialistRunStore()
        return _STORE


__all__ = [
    "SpecialistRunConflictError",
    "SpecialistRunScopeError",
    "SpecialistRunStore",
    "get_specialist_run_store",
]
