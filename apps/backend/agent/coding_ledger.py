"""Durable coding objective ledger.

The in-memory CodingLoop is a transition helper. This store owns resumable
engineering-objective state across context compaction and process restart.
It records operational facts and evidence, never hidden reasoning.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from config import DATA_DIR


LedgerStatus = Literal[
    "discovering", "planning", "implementing", "verifying", "waiting_for_approval",
    "paused", "blocked", "completed", "failed", "cancelled",
]


class CodingEvidence(BaseModel):
    kind: Literal["inspection", "source_revision", "command", "test", "tool_run", "approval", "artifact", "diagnosis"]
    summary: str
    reference_id: str = ""
    path: str = ""
    revision: str = ""
    status: str = ""
    created_at: float = Field(default_factory=time.time)


class CodingExecutionLedger(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    project_id: str
    project_root: str
    objective: str
    objective_hash: str
    requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    repository_instructions: list[dict[str, str]] = Field(default_factory=list)
    architecture_findings: list[str] = Field(default_factory=list)
    affected_areas: list[str] = Field(default_factory=list)
    phases: list[dict[str, Any]] = Field(default_factory=list)
    inspected_files: list[str] = Field(default_factory=list)
    source_revisions: dict[str, str] = Field(default_factory=dict)
    changed_files: list[str] = Field(default_factory=list)
    commands: list[dict[str, Any]] = Field(default_factory=list)
    tests: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    diagnoses: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    approval_ids: list[str] = Field(default_factory=list)
    tool_run_ids: list[str] = Field(default_factory=list)
    remaining_work: list[str] = Field(default_factory=list)
    completion_evidence: list[CodingEvidence] = Field(default_factory=list)
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    status: LedgerStatus = "discovering"
    cancellation_requested: bool = False
    revision: int = 1
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class CodingLedgerStore:
    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root or (Path(DATA_DIR) / "coding_ledgers"))
        self._lock = threading.RLock()
        self._items: dict[str, CodingExecutionLedger] = {}
        self._load()

    @staticmethod
    def objective_hash(session_id: str, project_id: str, objective: str) -> str:
        payload = "\0".join([str(session_id), str(project_id), " ".join(str(objective).split()).casefold()])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _path(self, ledger_id: str) -> Path:
        return self.root / f"{ledger_id}.json"

    def _load(self) -> None:
        if not self.root.exists():
            return
        for path in sorted(self.root.glob("*.json")):
            try:
                record = CodingExecutionLedger.model_validate_json(path.read_text(encoding="utf-8"))
                if record.id in self._items:
                    raise ValueError(f"duplicate coding ledger id: {record.id}")
                self._items[record.id] = record
            except Exception as exc:
                self._fail_corrupt(path, exc)

    def _fail_corrupt(self, path: Path, error: Exception) -> None:
        quarantine = self.root / "corrupt-state" / f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        note = "quarantine copy could not be created"
        try:
            quarantine.mkdir(parents=True, exist_ok=False)
            copy = quarantine / path.name
            shutil.copy2(path, copy)
            guide = quarantine / "RECOVERY.txt"
            guide.write_text(
                "EchoSpeak coding-ledger recovery\n\n"
                f"Authoritative file: {path}\nQuarantine copy: {copy}\nError: {error}\n\n"
                "Keep EchoSpeak stopped, repair or restore the authoritative JSON, then restart. "
                "The original file was not modified.\n",
                encoding="utf-8",
            )
            note = f"quarantine copy: {copy}; recovery guide: {guide}"
        except Exception as quarantine_error:
            note = f"quarantine failed: {quarantine_error}"
        raise RuntimeError(f"Coding ledger is unreadable at {path}; {note}. ({error})") from error

    def _save(self, record: CodingExecutionLedger) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(record.id)
        temp = path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(record.model_dump_json(indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)

    def start_or_resume(
        self,
        *,
        session_id: str,
        project_id: str,
        project_root: str,
        objective: str,
        **initial: Any,
    ) -> CodingExecutionLedger:
        if not session_id or not project_id or not project_root or not str(objective).strip():
            raise ValueError("Coding ledger requires Session, Project, root, and objective")
        digest = self.objective_hash(session_id, project_id, objective)
        with self._lock:
            existing = next(
                (
                    item for item in self._items.values()
                    if item.objective_hash == digest and item.status not in {"completed", "failed", "cancelled"}
                ),
                None,
            )
            if existing:
                return existing.model_copy(deep=True)
            record = CodingExecutionLedger(
                session_id=session_id,
                project_id=project_id,
                project_root=str(Path(project_root).resolve(strict=False)),
                objective=str(objective).strip(),
                objective_hash=digest,
                **initial,
            )
            self._items[record.id] = record
            self._save(record)
            return record.model_copy(deep=True)

    def get(self, ledger_id: str) -> Optional[CodingExecutionLedger]:
        with self._lock:
            item = self._items.get(str(ledger_id))
            return item.model_copy(deep=True) if item else None

    def active_for(self, session_id: str, project_id: str) -> Optional[CodingExecutionLedger]:
        with self._lock:
            rows = [
                item for item in self._items.values()
                if item.session_id == session_id and item.project_id == project_id
                and item.status not in {"completed", "failed", "cancelled"}
            ]
            rows.sort(key=lambda item: item.updated_at, reverse=True)
            return rows[0].model_copy(deep=True) if rows else None

    def update(self, ledger_id: str, *, expected_revision: int, **changes: Any) -> CodingExecutionLedger:
        with self._lock:
            current = self._items.get(str(ledger_id))
            if current is None:
                raise KeyError("Coding ledger not found")
            if current.revision != int(expected_revision):
                raise ValueError("stale coding-ledger revision")
            allowed = set(CodingExecutionLedger.model_fields) - {
                "id", "schema_version", "session_id", "project_id", "project_root",
                "objective_hash", "created_at", "revision", "updated_at",
            }
            payload = {key: value for key, value in changes.items() if key in allowed}
            payload.update({"revision": current.revision + 1, "updated_at": time.time()})
            updated = CodingExecutionLedger.model_validate(current.model_copy(update=payload).model_dump())
            self._items[current.id] = updated
            self._save(updated)
            return updated.model_copy(deep=True)

    def append_evidence(self, ledger_id: str, evidence: CodingEvidence) -> CodingExecutionLedger:
        with self._lock:
            current = self._items.get(str(ledger_id))
            if current is None:
                raise KeyError("Coding ledger not found")
            return self.update(
                current.id,
                expected_revision=current.revision,
                completion_evidence=[*current.completion_evidence, evidence][-300:],
            )

    def list_for_session(self, session_id: str, *, project_id: str = "") -> list[CodingExecutionLedger]:
        with self._lock:
            rows = [item for item in self._items.values() if item.session_id == session_id]
            if project_id:
                rows = [item for item in rows if item.project_id == project_id]
            rows.sort(key=lambda item: item.updated_at, reverse=True)
            return [item.model_copy(deep=True) for item in rows]


_STORE: Optional[CodingLedgerStore] = None


def get_coding_ledger_store() -> CodingLedgerStore:
    global _STORE
    if _STORE is None:
        _STORE = CodingLedgerStore()
    return _STORE
