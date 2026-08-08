"""Skill execution records bound to Turn / Execution / ToolRun.

Completion is durable structured state — never prose-only.
"""

from __future__ import annotations

import json
import hashlib
import importlib.util
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from agent.skill_contract import (
    SkillExecutionRecord,
    SkillExecutionStatus,
    SkillProposal,
    SkillWorkflowStage,
)


class SkillExecutionError(RuntimeError):
    pass

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
    serialized = json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n"
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


_TRANSITIONS: dict[SkillExecutionStatus, frozenset[SkillExecutionStatus]] = {
    SkillExecutionStatus.SELECTED: frozenset({SkillExecutionStatus.PLANNED, SkillExecutionStatus.BLOCKED, SkillExecutionStatus.CANCELED}),
    SkillExecutionStatus.PLANNED: frozenset({SkillExecutionStatus.RUNNING, SkillExecutionStatus.BLOCKED, SkillExecutionStatus.CANCELED}),
    SkillExecutionStatus.RUNNING: frozenset({
        SkillExecutionStatus.PENDING_APPROVAL, SkillExecutionStatus.COMPLETED, SkillExecutionStatus.BLOCKED,
        SkillExecutionStatus.FAILED, SkillExecutionStatus.CANCELED, SkillExecutionStatus.PARTIAL,
    }),
    SkillExecutionStatus.PENDING_APPROVAL: frozenset({
        SkillExecutionStatus.RUNNING, SkillExecutionStatus.COMPLETED, SkillExecutionStatus.BLOCKED,
        SkillExecutionStatus.FAILED, SkillExecutionStatus.CANCELED, SkillExecutionStatus.PARTIAL,
    }),
    SkillExecutionStatus.PARTIAL: frozenset({
        SkillExecutionStatus.PLANNED, SkillExecutionStatus.RUNNING, SkillExecutionStatus.COMPLETED,
        SkillExecutionStatus.BLOCKED, SkillExecutionStatus.FAILED, SkillExecutionStatus.CANCELED,
    }),
    SkillExecutionStatus.BLOCKED: frozenset({SkillExecutionStatus.PLANNED, SkillExecutionStatus.CANCELED}),
    SkillExecutionStatus.FAILED: frozenset({SkillExecutionStatus.PLANNED, SkillExecutionStatus.CANCELED}),
    SkillExecutionStatus.COMPLETED: frozenset(),
    SkillExecutionStatus.CANCELED: frozenset(),
}


def _validate_transition(current: SkillExecutionStatus, target: SkillExecutionStatus) -> None:
    if current == target:
        return
    if target not in _TRANSITIONS.get(current, frozenset()):
        raise SkillExecutionError(f"Invalid SkillExecution transition: {current.value} -> {target.value}")


def create_skill_execution(
    *,
    execution_id: str,
    skill_id: str,
    skill_version: str = "1.0.0",
    project_id: str = "",
    session_id: str = "",
    turn_id: str = "",
    parent_execution_id: str = "",
    parent_skill_execution_id: str = "",
    child_skill_ids: list[str] | None = None,
    child_execution_ids: list[str] | None = None,
    input_context_identity: dict[str, Any] | None = None,
    status: SkillExecutionStatus = SkillExecutionStatus.SELECTED,
) -> SkillExecutionRecord:
    # Stable replay: one SkillExecution per Turn + Skill + composition parent.
    with _LOCK:
        if _EXEC_DIR.exists():
            for path in _EXEC_DIR.glob("*.json"):
                try:
                    existing = SkillExecutionRecord.model_validate(_read_json(path))
                except Exception:
                    continue
                if (
                    existing.execution_id == execution_id
                    and existing.skill_id == skill_id
                    and existing.parent_skill_execution_id == parent_skill_execution_id
                ):
                    return existing
    record = SkillExecutionRecord(
        id=str(uuid.uuid4()),
        execution_id=execution_id,
        skill_id=skill_id,
        skill_version=skill_version,
        project_id=project_id,
        session_id=session_id,
        turn_id=turn_id or execution_id,
        parent_execution_id=parent_execution_id,
        parent_skill_execution_id=parent_skill_execution_id,
        child_skill_ids=list(child_skill_ids or []),
        child_execution_ids=list(child_execution_ids or []),
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
        if "status" in updates:
            updates["status"] = SkillExecutionStatus(updates["status"])
            _validate_transition(record.status, updates["status"])
            if "workflow_stage" not in updates:
                updates["workflow_stage"] = {
                    SkillExecutionStatus.SELECTED: SkillWorkflowStage.SELECTING,
                    SkillExecutionStatus.PLANNED: SkillWorkflowStage.AUTHORIZING,
                    SkillExecutionStatus.RUNNING: SkillWorkflowStage.EXECUTING,
                    SkillExecutionStatus.PENDING_APPROVAL: SkillWorkflowStage.AWAITING_APPROVAL,
                    SkillExecutionStatus.PARTIAL: SkillWorkflowStage.VERIFYING,
                    SkillExecutionStatus.COMPLETED: SkillWorkflowStage.COMPLETE,
                    SkillExecutionStatus.BLOCKED: SkillWorkflowStage.BLOCKED,
                    SkillExecutionStatus.FAILED: SkillWorkflowStage.FAILED,
                    SkillExecutionStatus.CANCELED: SkillWorkflowStage.CANCELED,
                }[updates["status"]]
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


def list_skill_executions_for_turn(execution_id: str) -> list[SkillExecutionRecord]:
    key = str(execution_id or "").strip()
    if not key or not _EXEC_DIR.exists():
        return []
    rows: list[SkillExecutionRecord] = []
    for path in _EXEC_DIR.glob("*.json"):
        try:
            record = SkillExecutionRecord.model_validate(_read_json(path))
        except Exception:
            continue
        if record.execution_id == key:
            rows.append(record)
    return sorted(rows, key=lambda item: item.created_at)


def _dependency_blocks(manifest: Any) -> list[str]:
    metadata = dict(getattr(manifest, "dependency_metadata", None) or {})
    blocked: list[str] = []
    for module in metadata.get("required_python_modules") or []:
        token = str(module or "").strip()
        if token and importlib.util.find_spec(token) is None:
            blocked.append(f"python_module:{token}")
    for binary in metadata.get("required_binaries") or []:
        token = str(binary or "").strip()
        if token and not shutil.which(token):
            blocked.append(f"binary:{token}")
    for variable in metadata.get("required_environment_variables") or []:
        token = str(variable or "").strip()
        if token and not os.environ.get(token):
            blocked.append(f"configuration:{token}")
    return blocked


def activate_skill_execution(
    execution_record_id: str,
    *,
    state_store: Any,
    allowed_tool_names: set[str] | None = None,
    available_capabilities: set[str] | None = None,
    permissions: set[str] | None = None,
) -> SkillExecutionRecord:
    """Re-resolve a manifest and start one governed skill wrapper ToolRun."""
    from agent.skills_registry import SkillsRegistry
    from agent.tool_registry import ToolRegistry

    record = get_skill_execution(execution_record_id)
    if record is None:
        raise SkillExecutionError("SkillExecutionRecord not found")
    manifest = SkillsRegistry.get(record.skill_id)
    blocks: list[str] = []
    if manifest is None:
        blocks.append("manifest:not_registered")
    else:
        if manifest.version != record.skill_version:
            blocks.append(f"manifest_version:{record.skill_version}->{manifest.version}")
        if not manifest.executable:
            blocks.append("manifest:prompt_only_or_unavailable")
        registered = set(ToolRegistry.get_names())
        # None means the caller did not supply a Turn-scoped inventory.  An
        # explicit empty set means tools are prohibited and must stay empty.
        inventory = registered if allowed_tool_names is None else set(allowed_tool_names)
        permitted = set(manifest.tool_allowlist())
        authorized_permitted = permitted & inventory
        for tool in manifest.required_tools:
            entry = ToolRegistry.get(tool)
            if tool not in registered or tool not in inventory or entry is None or not entry.available:
                blocks.append(f"tool:{tool}")
        if permitted and not authorized_permitted:
            blocks.append("tool_policy:no_authorized_skill_tools")
        caps = set(available_capabilities or set())
        for capability in [*manifest.required_capabilities, *manifest.required_models]:
            if capability not in caps:
                blocks.append(f"capability:{capability}")
        granted = set(permissions or set())
        for permission in manifest.permissions:
            aliases = {
                "system_actions": {"system_actions", "enable_system_actions"},
            }
            if not (aliases.get(permission, {permission}) & {item.lower() for item in granted}):
                blocks.append(f"permission:{permission}")
        if manifest.required_project_state and not record.project_id:
            blocks.append("project:not_attached")
        collected = {
            key: value
            for key, value in dict(record.input_context_identity or {}).items()
            if key in set(manifest.required_context_fields) and value not in {None, ""}
        }
        missing_inputs = [
            key for key in manifest.required_context_fields
            if key not in collected
        ]
        if missing_inputs:
            blocks.extend(f"input:{key}" for key in missing_inputs)
        blocks.extend(_dependency_blocks(manifest))
    if blocks:
        updated = update_skill_execution(
            record.id,
            status=SkillExecutionStatus.BLOCKED,
            failure_reason="; ".join(blocks),
            prompt_only=bool(manifest is not None and not manifest.executable),
            verification={"passed": False, "blocks": blocks},
            workflow_stage=SkillWorkflowStage.BLOCKED,
            permitted_tool_ids=sorted(authorized_permitted) if manifest is not None else [],
            required_inputs=list(manifest.required_context_fields) if manifest is not None else [],
            collected_inputs=collected if manifest is not None else {},
            missing_inputs=missing_inputs if manifest is not None else [],
            verification_rules=list(manifest.verification_rules) if manifest is not None else [],
            completion_criteria=list(manifest.completion_criteria) if manifest is not None else [],
        )
        if updated is None:
            raise SkillExecutionError("SkillExecutionRecord disappeared")
        return updated

    planned = update_skill_execution(
        record.id,
        status=SkillExecutionStatus.PLANNED,
        selected_tool_ids=list(manifest.required_tools),
        workflow_stage=SkillWorkflowStage.AUTHORIZING,
        permitted_tool_ids=sorted(authorized_permitted),
        required_inputs=list(manifest.required_context_fields),
        collected_inputs=collected,
        missing_inputs=[],
        verification_rules=list(manifest.verification_rules),
        completion_criteria=list(manifest.completion_criteria or manifest.verification_rules),
        input_context_identity={
            **dict(record.input_context_identity or {}),
            "manifest_id": manifest.id,
            "manifest_version": manifest.version,
            "implementation_entry": manifest.implementation_entry,
        },
    )
    if planned is None:
        raise SkillExecutionError("SkillExecutionRecord disappeared")
    arguments = {
        "skill_id": manifest.id,
        "skill_version": manifest.version,
        "project_id": record.project_id,
        "session_id": record.session_id,
        "child_skill_ids": list(record.child_skill_ids),
    }
    arguments_hash = hashlib.sha256(
        json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    parent_run_id = f"skill-{record.id}"
    state_store.create_tool_run(
        turn_id=record.turn_id or record.execution_id,
        session_id=record.session_id,
        project_id=record.project_id,
        run_id=parent_run_id,
        tool_name="skill_execute",
        canonical_arguments=arguments,
        canonical_arguments_hash=arguments_hash,
        action_id=record.id,
    )
    running = update_skill_execution(
        record.id,
        status=SkillExecutionStatus.RUNNING,
        parent_tool_run_id=parent_run_id,
        tool_run_ids=list(dict.fromkeys([*record.tool_run_ids, parent_run_id])),
    )
    if running is None:
        raise SkillExecutionError("SkillExecutionRecord disappeared")
    return running


def _evaluate_verification_rules(
    rules: list[str],
    *,
    successes: list[Any],
    failures: list[Any],
    missing_required_tools: list[str],
    artifact_ids: list[str],
) -> tuple[bool, list[str]]:
    """Evaluate the small canonical rule vocabulary; unknown rules fail closed."""
    unmet: list[str] = []
    for raw in rules:
        rule = str(raw or "").strip().lower()
        if not rule:
            continue
        if rule in {"child_tool_succeeded", "tool_succeeded", "verified_tool_outcome"}:
            if not successes or not any(bool(getattr(run, "verification", None)) for run in successes):
                unmet.append(raw)
        elif rule in {"required_tool_success", "required_tools_succeeded"}:
            if missing_required_tools:
                unmet.append(raw)
        elif rule in {"no_tool_failures", "all_tool_runs_succeeded"}:
            if failures:
                unmet.append(raw)
        elif rule in {"artifact_required", "produced_artifact"}:
            if not artifact_ids:
                unmet.append(raw)
        else:
            unmet.append(f"unsupported:{raw}")
    return not unmet, unmet


def record_skill_tool_outcome(state_store: Any, tool_run: Any, *, owner_execution_id: str = "") -> None:
    """Link canonical child ToolRun truth; never execute or reinterpret the tool."""
    turn_id = str(owner_execution_id or getattr(tool_run, "turn_id", "") or "")
    tool_name = str(getattr(tool_run, "tool_name", "") or "")
    if not turn_id or not tool_name or tool_name == "skill_execute":
        return
    from agent.skills_registry import SkillsRegistry

    for record in list_skill_executions_for_turn(turn_id):
        if record.status not in {SkillExecutionStatus.RUNNING, SkillExecutionStatus.PENDING_APPROVAL, SkillExecutionStatus.PARTIAL}:
            continue
        manifest = SkillsRegistry.get(record.skill_id)
        if manifest is None or tool_name not in set(manifest.tool_allowlist()):
            continue
        outcome = dict(getattr(tool_run, "outcome", None) or {})
        verification = dict(getattr(tool_run, "verification", None) or {})
        artifact_ids = [
            str(value) for value in [*(verification.get("artifact_ids") or []), *(outcome.get("artifact_ids") or [])]
            if str(value or "").strip()
        ]
        approval_id = str(getattr(tool_run, "approval_id", "") or "")
        update_skill_execution(
            record.id,
            status=SkillExecutionStatus.RUNNING if record.status != SkillExecutionStatus.PENDING_APPROVAL else record.status,
            selected_tool_ids=list(dict.fromkeys([*record.selected_tool_ids, tool_name])),
            tool_run_ids=list(dict.fromkeys([*record.tool_run_ids, str(tool_run.id)])),
            approval_ids=list(dict.fromkeys([*record.approval_ids, *([approval_id] if approval_id else [])])),
            artifact_ids=list(dict.fromkeys([*record.artifact_ids, *artifact_ids])),
        )


def resume_skill_executions_for_approval(
    original_execution_id: str,
    *,
    continuation_execution_id: str,
    approval_id: str,
    state_store: Any,
) -> list[SkillExecutionRecord]:
    """Create a continuation parent ToolRun; terminal approval rows never reopen."""
    resumed: list[SkillExecutionRecord] = []
    for record in list_skill_executions_for_turn(original_execution_id):
        if record.status != SkillExecutionStatus.PENDING_APPROVAL:
            continue
        run_id = f"skill-resume-{record.id}-{approval_id}"
        state_store.create_tool_run(
            turn_id=continuation_execution_id,
            session_id=record.session_id,
            project_id=record.project_id,
            run_id=run_id,
            tool_name="skill_execute",
            canonical_arguments={"skill_id": record.skill_id, "approval_id": approval_id, "continuation_of": record.parent_tool_run_id},
            canonical_arguments_hash=hashlib.sha256(f"{record.id}:{approval_id}".encode("utf-8")).hexdigest(),
            action_id=record.id,
            approval_id=approval_id,
            retry_of=record.parent_tool_run_id,
        )
        updated = update_skill_execution(
            record.id,
            status=SkillExecutionStatus.RUNNING,
            parent_tool_run_id=run_id,
            approval_ids=list(dict.fromkeys([*record.approval_ids, approval_id])),
            tool_run_ids=list(dict.fromkeys([*record.tool_run_ids, run_id])),
        )
        if updated is not None:
            resumed.append(updated)
    return resumed


def finalize_skill_executions_for_turn(execution_id: str, *, state_store: Any, turn_success: bool) -> list[SkillExecutionRecord]:
    """Project child ToolRun/Approval truth into terminal SkillExecution state."""
    from agent.skills_registry import SkillsRegistry

    finalized: list[SkillExecutionRecord] = []
    approvals = [
        item for item in state_store.list_approvals(limit=200)
        if str(getattr(item, "execution_id", "") or getattr(item, "original_turn_id", "") or "") == execution_id
    ]
    for record in list_skill_executions_for_turn(execution_id):
        if record.status in {SkillExecutionStatus.COMPLETED, SkillExecutionStatus.CANCELED, SkillExecutionStatus.BLOCKED, SkillExecutionStatus.FAILED}:
            finalized.append(record)
            continue
        manifest = SkillsRegistry.get(record.skill_id)
        child_runs = [
            run for run_id in record.tool_run_ids if run_id != record.parent_tool_run_id
            for run in [state_store.get_tool_run(run_id)]
            if run is not None and str(getattr(run, "tool_name", "")) != "skill_execute"
        ]
        pending = [item for item in approvals if str(getattr(item, "status", "")) == "pending"]
        allowed = set(manifest.tool_allowlist()) if manifest is not None else set()
        pending = [item for item in pending if str(getattr(item, "tool", "") or "") in allowed]
        successes = [run for run in child_runs if str(run.status).lower() in {"complete", "completed", "success"} or (run.outcome or {}).get("success") is True]
        failures = [run for run in child_runs if str(run.status).lower() in {"failed", "error", "blocked", "policy_block"} or (run.outcome or {}).get("success") is False]
        required = set(manifest.required_tools if manifest is not None else [])
        successful_names = {str(run.tool_name) for run in successes}
        missing = sorted(required - successful_names)
        verification_rules = list(manifest.verification_rules if manifest is not None else [])
        completion_criteria = list(manifest.completion_criteria if manifest is not None else [])
        enforced_rules = list(dict.fromkeys([*verification_rules, *completion_criteria]))
        rules_passed, unmet_rules = _evaluate_verification_rules(
            enforced_rules,
            successes=successes,
            failures=failures,
            missing_required_tools=missing,
            artifact_ids=list(record.artifact_ids),
        )
        if pending:
            target = SkillExecutionStatus.PENDING_APPROVAL
        elif failures:
            target = SkillExecutionStatus.FAILED
        elif turn_success and not missing and rules_passed:
            target = SkillExecutionStatus.COMPLETED
        else:
            target = SkillExecutionStatus.PARTIAL
        verification = {
            "passed": target == SkillExecutionStatus.COMPLETED,
            "required_rules": verification_rules,
            "completion_criteria": completion_criteria,
            "unmet_rules": unmet_rules,
            "successful_tool_runs": [run.id for run in successes],
            "failed_tool_runs": [run.id for run in failures],
            "missing_required_tools": missing,
            "artifact_ids": list(record.artifact_ids),
        }
        updated = update_skill_execution(
            record.id,
            status=target,
            approval_ids=list(dict.fromkeys([*record.approval_ids, *(str(item.id) for item in pending)])),
            verification=verification,
            failure_reason=(str((failures[0].outcome or {}).get("error_message") or "required ToolRun failed") if failures else ""),
        )
        if updated is None:
            continue
        if updated.parent_tool_run_id:
            state_store.finish_tool_run(
                updated.parent_tool_run_id,
                {
                    "success": target == SkillExecutionStatus.COMPLETED,
                    "status": "complete" if target == SkillExecutionStatus.COMPLETED else "approval_required" if target == SkillExecutionStatus.PENDING_APPROVAL else target.value,
                    "output": f"Skill {updated.skill_id} {target.value}",
                    "verification": verification,
                },
            )
        finalized.append(updated)
    return finalized


def cancel_skill_execution(execution_record_id: str, *, state_store: Any) -> SkillExecutionRecord:
    record = get_skill_execution(execution_record_id)
    if record is None:
        raise SkillExecutionError("SkillExecutionRecord not found")
    if record.status in {SkillExecutionStatus.COMPLETED, SkillExecutionStatus.CANCELED}:
        return record
    updated = update_skill_execution(
        record.id,
        status=SkillExecutionStatus.CANCELED,
        cancellation_requested=True,
    )
    if updated is None:
        raise SkillExecutionError("SkillExecutionRecord disappeared")
    if updated.parent_tool_run_id:
        state_store.finish_tool_run(
            updated.parent_tool_run_id,
            {"success": False, "status": "canceled", "output": f"Skill {updated.skill_id} canceled"},
        )
    return updated


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


def list_skill_proposals() -> list[SkillProposal]:
    if not _PROPOSAL_DIR.exists():
        return []
    rows: list[SkillProposal] = []
    for path in _PROPOSAL_DIR.glob("*.json"):
        try:
            rows.append(SkillProposal.model_validate(_read_json(path)))
        except Exception:
            continue
    return sorted(rows, key=lambda item: item.created_at, reverse=True)


def update_skill_proposal(proposal_id: str, **changes: Any) -> Optional[SkillProposal]:
    with _LOCK:
        current = get_skill_proposal(proposal_id)
        if current is None:
            return None
        allowed = set(SkillProposal.model_fields) - {"id", "schema_version", "created_at"}
        update = {key: value for key, value in changes.items() if key in allowed and value is not None}
        updated = SkillProposal.model_validate(current.model_copy(update=update).model_dump())
        _write_json(_PROPOSAL_DIR / f"{proposal_id}.json", updated.model_dump(mode="json"))
        return updated
