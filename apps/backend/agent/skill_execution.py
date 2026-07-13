"""Skill execution records bound to Turn / Execution / ToolRun.

Completion is durable structured state — never prose-only.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from agent.skill_contract import SkillExecutionRecord, SkillExecutionStatus, SkillProposal

try:
    from config import DATA_DIR
except Exception:
    DATA_DIR = Path("data")

_EXEC_DIR = Path(DATA_DIR) / "skill_executions"
_PROPOSAL_DIR = Path(DATA_DIR) / "skill_proposals"
_LOCK = threading.RLock()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def create_skill_execution(
    *,
    execution_id: str,
    skill_id: str,
    skill_version: str = "1.0.0",
    project_id: str = "",
    session_id: str = "",
    turn_id: str = "",
    parent_execution_id: str = "",
    child_skill_ids: list[str] | None = None,
    input_context_identity: dict[str, Any] | None = None,
    status: SkillExecutionStatus = SkillExecutionStatus.PLANNED,
) -> SkillExecutionRecord:
    record = SkillExecutionRecord(
        id=str(uuid.uuid4()),
        execution_id=execution_id,
        skill_id=skill_id,
        skill_version=skill_version,
        project_id=project_id,
        session_id=session_id,
        turn_id=turn_id or execution_id,
        parent_execution_id=parent_execution_id,
        child_skill_ids=list(child_skill_ids or []),
        input_context_identity=dict(input_context_identity or {}),
        status=status,
    )
    with _LOCK:
        _EXEC_DIR.mkdir(parents=True, exist_ok=True)
        _write_json(_EXEC_DIR / f"{record.id}.json", record.model_dump(mode="json"))
    return record


def update_skill_execution(execution_record_id: str, **updates: Any) -> Optional[SkillExecutionRecord]:
    path = _EXEC_DIR / f"{execution_record_id}.json"
    with _LOCK:
        if not path.exists():
            return None
        data = _read_json(path)
        record = SkillExecutionRecord.model_validate(data)
        # Terminal completion cannot flip to running.
        if record.status == SkillExecutionStatus.COMPLETED and updates.get("status") not in {
            None,
            SkillExecutionStatus.COMPLETED,
            "completed",
        }:
            updates = {k: v for k, v in updates.items() if k != "status"}
        safe = {k: v for k, v in updates.items() if k not in {"id", "skill_id", "skill_version", "execution_id"}}
        safe["updated_at"] = time.time()
        record = record.model_copy(update=safe)
        _write_json(path, record.model_dump(mode="json"))
        return record


def get_skill_execution(execution_record_id: str) -> Optional[SkillExecutionRecord]:
    path = _EXEC_DIR / f"{execution_record_id}.json"
    if not path.exists():
        return None
    try:
        return SkillExecutionRecord.model_validate(_read_json(path))
    except Exception:
        return None


def list_skill_executions_for_session(session_id: str, limit: int = 40) -> list[SkillExecutionRecord]:
    session_id = str(session_id or "").strip()
    if not _EXEC_DIR.exists() or not session_id:
        return []
    items: list[SkillExecutionRecord] = []
    for path in sorted(_EXEC_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            record = SkillExecutionRecord.model_validate(_read_json(path))
        except Exception:
            continue
        if record.session_id == session_id:
            items.append(record)
        if len(items) >= limit:
            break
    return items


def create_skill_proposal(proposal: SkillProposal) -> SkillProposal:
    with _LOCK:
        _PROPOSAL_DIR.mkdir(parents=True, exist_ok=True)
        _write_json(_PROPOSAL_DIR / f"{proposal.id}.json", proposal.model_dump(mode="json"))
    return proposal


def get_skill_proposal(proposal_id: str) -> Optional[SkillProposal]:
    path = _PROPOSAL_DIR / f"{proposal_id}.json"
    if not path.exists():
        return None
    try:
        return SkillProposal.model_validate(_read_json(path))
    except Exception:
        return None
