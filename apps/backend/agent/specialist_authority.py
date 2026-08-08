"""Single Echo-level authority validator for specialist runtime actions."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from config import config
from agent.projects import get_project_manager
from agent.research_runtime import RequirementKind
from agent.specialist_contracts import SpecialistEventKind, SpecialistRun
from agent.specialist_store import get_specialist_run_store
from agent.state import get_state_store
from agent.task_runs import TERMINAL_TASK_STATUSES, get_task_run_store
from agent.threads import get_thread_manager


class SpecialistAuthorityError(RuntimeError):
    pass


def validate_specialist_delegation_policy(runtime_id: str) -> None:
    """Validate coarse Echo authority before an autonomous specialist loop."""

    runtime = str(runtime_id or "").strip().casefold()
    if not bool(getattr(config, "allow_terminal_commands", False)):
        raise SpecialistAuthorityError(
            "Specialist coding runtimes require EchoSpeak terminal permission"
        )
    if runtime == "opencode":
        opted_in = str(
            os.getenv("ECHOSPEAK_ALLOW_UNSANDBOXED_OPENCODE", "")
        ).strip().casefold() in {"1", "true", "yes", "on"}
        if not opted_in:
            raise SpecialistAuthorityError(
                "OpenCode host execution requires explicit "
                "ECHOSPEAK_ALLOW_UNSANDBOXED_OPENCODE=true configuration"
            )
        if not bool(getattr(config, "allow_file_write", False)):
            raise SpecialistAuthorityError(
                "OpenCode requires EchoSpeak file-write permission because "
                "EchoSpeak does not provide its process sandbox"
            )


def resolve_specialist_scope(session_id: str, project_id: str) -> tuple[Any, Any, Path]:
    session_key = str(session_id or "").strip()
    project_key = str(project_id or "").strip()
    if not session_key or get_thread_manager().get_thread(session_key) is None:
        raise SpecialistAuthorityError("Session not found")
    state = get_state_store().get_thread_state(session_key)
    if str(state.active_project_id or "") != project_key:
        raise SpecialistAuthorityError(
            "Session is not bound to the requested Project"
        )
    project = get_project_manager().get_project(project_key)
    if project is None:
        raise SpecialistAuthorityError("Project not found")
    root_text = str(project.workspace_root or "").strip()
    if not root_text:
        raise SpecialistAuthorityError("Project has no attached workspace root")
    root = Path(root_text).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise SpecialistAuthorityError("Project workspace root is unavailable")
    return state, project, root


def validate_specialist_run_authority(run: SpecialistRun, operation: str) -> None:
    """Revalidate current ownership before an external runtime action."""

    state, _project, root = resolve_specialist_scope(
        run.session_id, run.project_id
    )
    if os.path.normcase(str(root)) != os.path.normcase(
        str(Path(run.project_root).resolve())
    ):
        raise SpecialistAuthorityError(
            "Project workspace root changed after specialist delegation"
        )
    # Stopping an already delegated process is always a reducing action. A
    # Session model change or TaskRun terminalization must not make an orphaned
    # specialist process impossible to interrupt.
    if str(operation or "") == "interrupt":
        return
    binding = state.model_binding
    if binding is None:
        raise SpecialistAuthorityError("Session model binding is unavailable")
    if binding.binding_revision != run.authority.model_binding_revision:
        raise SpecialistAuthorityError(
            "Session model binding changed after specialist delegation"
        )
    task = get_task_run_store().get(
        run.task_run_id,
        session_id=run.session_id,
        project_id=run.project_id,
    )
    if task is None:
        raise SpecialistAuthorityError("Owning TaskRun no longer exists")
    if task.status in TERMINAL_TASK_STATUSES:
        raise SpecialistAuthorityError("Owning TaskRun is terminal")
    if run.id not in task.specialist_run_ids:
        raise SpecialistAuthorityError(
            "SpecialistRun is not bound to the owning TaskRun"
        )
    requirement = next(
        (
            item for item in task.requirements
            if item.requirement_id == run.requirement_id
        ),
        None,
    )
    if requirement is None or requirement.kind != RequirementKind.SPECIALIST:
        raise SpecialistAuthorityError(
            "Owning specialist requirement changed or was removed"
        )
    graph_node = next(
        (
            item
            for item in list(getattr(task.execution_graph, "nodes", []) or [])
            if item.requirement_id == run.requirement_id
        ),
        None,
    )
    if (
        graph_node is None
        or graph_node.node_id != run.graph_node_id
        or graph_node.node_id != run.authority.graph_node_id
    ):
        raise SpecialistAuthorityError(
            "Owning TaskRun graph node changed after specialist delegation"
        )
    if str(operation or "") in {"start", "continue"}:
        from agent.specialist_runtime import get_specialist_runtime_manager

        validate_specialist_delegation_policy(run.runtime_id)
        descriptor = get_specialist_runtime_manager().descriptor(run.runtime_id)
        if descriptor.state.value != "available":
            raise SpecialistAuthorityError(
                descriptor.reason or "Specialist runtime is unavailable"
            )


def validate_specialist_approval_authority(
    run: SpecialistRun, request_id: str
) -> None:
    """Apply current Echo policy to one exact pending specialist request."""

    validate_specialist_run_authority(run, "approval")
    pending = next(
        (
            item for item in reversed(
                get_specialist_run_store().list_events(run.id, limit=2000)
            )
            if item.runtime_request_id == str(request_id)
            and item.kind == SpecialistEventKind.APPROVAL_REQUESTED
        ),
        None,
    )
    if pending is None:
        raise SpecialistAuthorityError("Specialist approval is stale")
    category = " ".join([
        pending.raw_source,
        str(pending.payload.get("method") or ""),
        json.dumps(pending.payload, ensure_ascii=False)[:4000],
    ]).casefold()
    needs_terminal = any(
        token in category for token in ("command", "exec", "shell", "terminal")
    )
    needs_file_write = any(
        token in category for token in ("file", "patch", "write", "change")
    )
    if needs_terminal and not bool(getattr(config, "allow_terminal_commands", False)):
        raise SpecialistAuthorityError(
            "Terminal actions are disabled by EchoSpeak policy"
        )
    if needs_file_write and not bool(getattr(config, "allow_file_write", False)):
        raise SpecialistAuthorityError(
            "File mutations are disabled by EchoSpeak policy"
        )
    if not needs_terminal and not needs_file_write:
        raise SpecialistAuthorityError(
            "Unknown specialist action category cannot be approved"
        )


__all__ = [
    "SpecialistAuthorityError",
    "resolve_specialist_scope",
    "validate_specialist_delegation_policy",
    "validate_specialist_approval_authority",
    "validate_specialist_run_authority",
]
