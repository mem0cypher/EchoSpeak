"""Canonical durable Product Task owner.

Turn plans remain Turn projections. Routines, Heartbeat, proactive adapters,
and the legacy Todo API may point at these records but do not own task truth.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from config import DATA_DIR


TaskStatus = Literal[
    "pending", "in_progress", "needs_permission", "blocked", "failed", "cancelled", "complete", "done"
]


class ProductTask(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str = ""
    status: TaskStatus = "pending"
    priority: Literal["low", "medium", "high"] = "medium"
    project_id: str = ""
    session_id: str = ""
    objective: str = ""
    dependencies: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    execution_ids: list[str] = Field(default_factory=list)
    tool_run_ids: list[str] = Field(default_factory=list)
    job_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    automation_run_ids: list[str] = Field(default_factory=list)
    task_run_ids: list[str] = Field(default_factory=list)
    approval_ids: list[str] = Field(default_factory=list)
    connection_references: list[dict[str, Any]] = Field(default_factory=list)
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    trigger_provenance: dict[str, Any] = Field(default_factory=dict)
    cancellation_requested: bool = False
    verification: dict[str, Any] = Field(default_factory=dict)
    source: str = "user"
    source_id: str = ""
    idempotency_key: str = ""
    scheduled_for: str = ""
    revision: int = 1
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TaskStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        # Keep the legacy filename as a compatible projection while replacing
        # its fail-open/non-atomic implementation with this single owner.
        self.path = Path(path or (Path(DATA_DIR) / "todos.json"))
        self._lock = threading.RLock()
        self._tasks: dict[str, ProductTask] = {}
        self._order: list[str] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            rows = payload.get("tasks", []) if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                raise ValueError("Task root must be a list or {tasks: [...]} object")
            for raw in rows:
                task = ProductTask.model_validate(raw)
                if task.id in self._tasks:
                    raise ValueError(f"Duplicate Task id: {task.id}")
                self._tasks[task.id] = task
                self._order.append(task.id)
        except Exception as exc:
            self._fail_corrupt(exc)

    def _fail_corrupt(self, error: Exception) -> None:
        root = self.path.parent / "corrupt-state" / f"tasks-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        note = "quarantine copy could not be created"
        try:
            root.mkdir(parents=True, exist_ok=False)
            copy = root / self.path.name
            shutil.copy2(self.path, copy)
            guide = root / "RECOVERY.txt"
            guide.write_text(
                "EchoSpeak Product Task recovery\n\n"
                f"Authoritative file: {self.path}\nQuarantine copy: {copy}\nError: {error}\n\n"
                "Keep EchoSpeak stopped, repair or restore the authoritative JSON, then restart. "
                "The original file was not changed.\n",
                encoding="utf-8",
            )
            note = f"quarantine copy: {copy}; recovery guide: {guide}"
        except Exception as quarantine_error:
            note = f"quarantine failed: {quarantine_error}"
        raise RuntimeError(
            f"Product Task state is unreadable at {self.path}; the authoritative file was not overwritten; {note}. ({error})"
        ) from error

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rows = [self._tasks[item].model_dump(mode="json") for item in self._order if item in self._tasks]
        temp = self.path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)

    def list(self, *, project_id: str = "", session_id: str = "") -> list[ProductTask]:
        with self._lock:
            rows = [self._tasks[item] for item in self._order if item in self._tasks]
            if project_id:
                rows = [task for task in rows if task.project_id == project_id]
            if session_id:
                rows = [task for task in rows if task.session_id == session_id]
            return [task.model_copy(deep=True) for task in rows]

    def get(self, task_id: str) -> Optional[ProductTask]:
        with self._lock:
            task = self._tasks.get(str(task_id or ""))
            return task.model_copy(deep=True) if task else None

    def create(self, **values: Any) -> ProductTask:
        with self._lock:
            candidate = ProductTask.model_validate(values)
            if not candidate.title.strip():
                raise ValueError("Task title is required")
            key = str(values.get("idempotency_key") or "")
            if key:
                existing = next((task for task in self._tasks.values() if task.idempotency_key == key), None)
                if existing:
                    stable_fields = (
                        "project_id", "session_id", "objective", "source", "source_id", "scheduled_for"
                    )
                    if any(
                        str(getattr(existing, field, "") or "")
                        != str(getattr(candidate, field, "") or "")
                        for field in stable_fields
                    ):
                        raise ValueError("Task idempotency key is bound to another action identity or scope")
                    return existing.model_copy(deep=True)
            task = candidate
            if task.id in self._tasks:
                raise ValueError("Task id already exists")
            self._tasks[task.id] = task
            self._order.append(task.id)
            self._save()
            return task.model_copy(deep=True)

    def update(
        self,
        task_id: str,
        *,
        expected_revision: Optional[int] = None,
        **changes: Any,
    ) -> Optional[ProductTask]:
        with self._lock:
            current = self._tasks.get(str(task_id or ""))
            if current is None:
                return None
            if expected_revision is not None and current.revision != int(expected_revision):
                raise ValueError("Task revision changed")
            allowed = set(ProductTask.model_fields) - {"id", "schema_version", "created_at"}
            update = {key: value for key, value in changes.items() if key in allowed and value is not None}
            update["updated_at"] = datetime.now(timezone.utc).isoformat()
            update["revision"] = current.revision + 1
            updated = current.model_copy(update=update)
            self._tasks[current.id] = ProductTask.model_validate(updated.model_dump())
            self._save()
            return self._tasks[current.id].model_copy(deep=True)

    def delete(self, task_id: str) -> bool:
        with self._lock:
            key = str(task_id or "")
            if key not in self._tasks:
                return False
            del self._tasks[key]
            self._order = [item for item in self._order if item != key]
            self._save()
            return True

    def reorder(self, order: list[str]) -> list[ProductTask]:
        with self._lock:
            requested = [item for item in order if item in self._tasks]
            self._order = requested + [item for item in self._order if item not in requested]
            self._save()
            return self.list()

    def reorder_scope(
        self,
        order: list[str],
        *,
        project_id: str,
        session_id: str,
    ) -> list[ProductTask]:
        """Reorder only positions already owned by one Project/Session scope."""

        with self._lock:
            scoped_positions = [
                index for index, task_id in enumerate(self._order)
                if task_id in self._tasks
                and self._tasks[task_id].project_id == str(project_id or "")
                and self._tasks[task_id].session_id == str(session_id or "")
            ]
            existing = [self._order[index] for index in scoped_positions]
            requested = [item for item in order if item in existing]
            replacement = requested + [item for item in existing if item not in requested]
            for index, task_id in zip(scoped_positions, replacement):
                self._order[index] = task_id
            self._save()
            return [self._tasks[item].model_copy(deep=True) for item in replacement]


_task_store: Optional[TaskStore] = None


def get_task_store() -> TaskStore:
    global _task_store
    if _task_store is None:
        _task_store = TaskStore()
    return _task_store
