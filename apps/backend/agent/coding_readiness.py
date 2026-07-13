"""Authoritative coding-readiness projection.

This module reports the intersection of existing authorities.  It does not own
Projects, Session attachment, permissions, tool registration, provider state,
or approvals; it only explains whether those owners currently permit the
bounded coding workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from agent.model_runtime import resolve_model_profile
from agent.projects import get_project_manager
from agent.tool_registry import ToolRegistry
from config import config


CODING_TOOL_NAMES = ("file_list", "file_read", "file_write", "terminal_run")
READ_TOOL_NAMES = frozenset({"file_list", "file_read"})


def _normal_path(value: str) -> str:
    try:
        return str(Path(value).expanduser().resolve())
    except Exception:
        return ""


def _same_path(left: str, right: str) -> bool:
    try:
        import os

        return os.path.normcase(_normal_path(left)) == os.path.normcase(_normal_path(right))
    except Exception:
        return False


def _model_projection(agent: Any, provider_readiness: Mapping[str, Any]) -> dict[str, Any]:
    info = dict(getattr(agent, "provider_info", {}) or {})
    provider = str(provider_readiness.get("provider") or info.get("provider") or "").strip()
    model = str(info.get("model") or "").strip()
    overrides = dict(
        (getattr(config, "model_capability_profiles", {}) or {}).get(f"{provider}:{model}")
        or (getattr(config, "model_capability_profiles", {}) or {}).get(model)
        or {}
    )
    try:
        if provider not in {"openai", "gemini"}:
            context_length = int(getattr(getattr(config, "local", None), "context_length", 0) or 0)
            if context_length > 0 and "context_limit" not in overrides:
                overrides["context_limit"] = context_length
        trim_limit = int(getattr(config, "llm_trim_max_tokens", 0) or 0)
        if trim_limit > 0 and "context_limit" not in overrides:
            overrides["context_limit"] = trim_limit
    except Exception:
        pass
    profile = resolve_model_profile(provider or "unknown", model or "default", overrides)
    provider_ready = bool(provider_readiness.get("ok"))
    native_tools = False
    try:
        native_tools = bool(agent._allow_llm_tool_calling())  # type: ignore[attr-defined]
    except Exception:
        native_tools = False
    structured_fallback = bool(
        hasattr(agent, "_parse_action_json")
        and hasattr(agent, "_infer_file_write_args")
        and getattr(agent, "llm_wrapper", None) is not None
    )
    tool_call_path = (
        "native"
        if provider_ready and native_tools
        else "structured_fallback"
        if provider_ready and structured_fallback
        else "unavailable"
    )
    return {
        "provider": provider,
        "model": model,
        "ready": provider_ready,
        "message": str(provider_readiness.get("message") or ""),
        "detail": str(provider_readiness.get("detail") or ""),
        "tool_call_path": tool_call_path,
        "native_tools": native_tools,
        "structured_fallback": structured_fallback,
        "context_limit": int(profile.context_limit or 0),
        "profile_source": str(profile.source or ""),
    }


def _tool_status(agent: Any, name: str, *, project_attached: bool) -> tuple[str, str]:
    registered = ToolRegistry.get(name) is not None
    if not registered:
        return "unregistered", f"{name} is not registered in the backend tool registry."

    loaded_names: set[str] = set()
    try:
        loaded_names = set(agent._registered_tool_names())  # type: ignore[attr-defined]
    except Exception:
        loaded_names = {
            str(getattr(tool, "name", "") or "")
            for tool in [*(getattr(agent, "tools", []) or []), *(getattr(agent, "lc_tools", []) or [])]
        }
    if name not in loaded_names:
        return "filtered", f"{name} is registered but not present in this Session's executable inventory."

    if not project_attached:
        return "disabled", "No Project is attached to this Session."

    try:
        if bool(agent._is_tool_role_blocked(name)):  # type: ignore[attr-defined]
            return "disabled", f"{name} is blocked for the current request role."
    except Exception:
        pass

    if name not in READ_TOOL_NAMES:
        try:
            if not bool(agent._action_configured(name)):  # type: ignore[attr-defined]
                if name == "file_write":
                    return "disabled", "File writing is disabled. Enable System Actions and Allow File Write in Settings."
                if name == "terminal_run":
                    return "disabled", "Terminal access is disabled. Terminal is optional for ordinary file editing."
                return "disabled", f"{name} is disabled by current system-action settings."
        except Exception:
            return "disabled", f"{name} could not be validated against current system-action settings."
    return "available", ""


def build_coding_readiness(
    agent: Any,
    thread_id: Optional[str],
    provider_readiness: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the one backend coding-readiness result used by API and preflight."""

    session_id = str(thread_id or getattr(agent, "_current_thread_id", "default") or "default").strip() or "default"
    state = agent._state_store.get_thread_state(session_id)  # type: ignore[attr-defined]
    project_id = str(state.active_project_id or "").strip()
    scope = dict(agent.project_scope_report(session_id) or {})
    project = get_project_manager().get_project(project_id) if project_id else None
    manager_root = str(getattr(project, "workspace_root", "") or "").strip() if project is not None else ""
    session_root = str(state.project_path or state.workspace_root or "").strip()
    root = manager_root or session_root
    root_path = Path(root).expanduser() if root else None
    root_exists = bool(root_path and root_path.exists() and root_path.is_dir())
    root_authorized = bool(project is not None and manager_root and session_root and _same_path(manager_root, session_root))
    attached = bool(project_id and project is not None and manager_root and session_root)

    model = _model_projection(agent, provider_readiness)
    tool_states: dict[str, str] = {}
    tool_details: dict[str, str] = {}
    for name in CODING_TOOL_NAMES:
        status, detail = _tool_status(agent, name, project_attached=attached and root_exists and root_authorized)
        tool_states[name] = status
        if detail:
            tool_details[name] = detail

    permissions = {
        "read": bool(attached and root_exists and root_authorized),
        "write": bool(
            getattr(config, "enable_system_actions", False)
            and getattr(config, "allow_file_write", False)
        ),
        "terminal": bool(
            getattr(config, "enable_system_actions", False)
            and getattr(config, "allow_terminal_commands", False)
        ),
    }
    pending = agent._state_store.get_pending_approval(session_id)  # type: ignore[attr-defined]
    approval = {
        "writes_require_confirmation": True,
        "auto_confirm_enabled": bool(getattr(config, "auto_confirm_actions", False)),
        "pending_approval_id": str(getattr(pending, "id", "") or "") or None,
        "pending_tool": str(getattr(pending, "tool", "") or "") or None,
        "pending_status": str(getattr(pending, "status", "") or "") or None,
    }

    try:
        from agent.sandbox import get_sandbox_status, normalize_execution_mode

        sandbox = get_sandbox_status().as_dict()
        terminal_mode = normalize_execution_mode(getattr(config, "terminal_execution_mode", "docker"))
    except Exception as exc:
        terminal_mode = str(getattr(config, "terminal_execution_mode", "docker") or "docker")
        sandbox = {"mode": terminal_mode, "ready": False, "message": f"Sandbox status unavailable: {exc}"}
    terminal_available = bool(permissions["terminal"] and (terminal_mode == "host" or sandbox.get("ready")))
    terminal = {
        "mode": terminal_mode,
        "enabled": permissions["terminal"],
        "available": terminal_available,
        "required_for_file_editing": False,
        "sandbox": sandbox,
    }

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def block(code: str, message: str) -> None:
        blockers.append({"code": code, "message": message})

    def warn(code: str, message: str) -> None:
        warnings.append({"code": code, "message": message})

    if not project_id:
        block("project_not_attached", "No Project is attached to this Session.")
    elif project is None:
        block("project_missing", "The Session references a Project that no longer exists.")
    elif not root_exists:
        block("project_root_missing", "The attached Project root does not exist or is not a directory.")
    elif not root_authorized:
        block("project_root_changed", "The Session Project root no longer matches the ProjectManager authority record.")
    if not model["ready"]:
        block("provider_unavailable", model["message"] or "The selected model provider is not ready.")
    elif model["tool_call_path"] == "unavailable":
        block("model_tool_path_unavailable", "The current provider has no valid native or structured tool-call path.")
    for name in ("file_list", "file_read", "file_write"):
        status = tool_states[name]
        if status != "available":
            block(f"{name}_{status}", tool_details.get(name) or f"{name} is {status}.")
    if tool_states["terminal_run"] != "available":
        warn("terminal_disabled", tool_details.get("terminal_run") or "Terminal is unavailable; ordinary file editing can still work.")
    elif not terminal_available:
        warn("terminal_runtime_unavailable", "The configured terminal runtime is unavailable; ordinary file editing can still work.")
    if approval["pending_approval_id"]:
        warn(
            "approval_pending",
            "This Session has an edit waiting for confirmation. Reply `confirm` in the same Session or cancel it.",
        )

    ready_for_reading = bool(
        attached
        and root_exists
        and root_authorized
        and model["ready"]
        and model["tool_call_path"] != "unavailable"
        and all(tool_states[name] == "available" for name in ("file_list", "file_read"))
    )
    ready_for_editing = bool(ready_for_reading and tool_states["file_write"] == "available")
    recommendations = [item["message"] for item in blockers + warnings]
    if ready_for_editing:
        recommendations.append("Coding is ready for bounded Project reads and approval-gated file edits.")

    return {
        "schema_version": 2,
        "session_id": session_id,
        "project": {
            "attached": attached,
            "project_id": project_id,
            "name": str(getattr(project, "name", "") or "") if project is not None else "",
            "root": _normal_path(root) if root else "",
            "root_exists": root_exists,
            "root_authorized": root_authorized,
            "interaction_mode": str(scope.get("interaction_mode") or state.mode or "chat"),
        },
        "model": model,
        "tools": tool_states,
        "tool_details": tool_details,
        "permissions": permissions,
        "approval": approval,
        "terminal": terminal,
        "blockers": blockers,
        "warnings": warnings,
        "ready_for_reading": ready_for_reading,
        "ready_for_editing": ready_for_editing,
        "recommended_loop": ["inspect", "plan", "implement", "confirm", "verify", "summarize"],
        "recommendations": recommendations,
        # Compatibility projection for existing Tools UI consumers.
        "ok": ready_for_editing,
        "provider": model,
        "workspace": scope,
        "file_roots": {
            "root": str(getattr(config, "file_tool_root", "") or ""),
            "extra_roots": list(getattr(config, "file_tool_extra_roots", []) or []),
            "terminal_execution_mode": terminal_mode,
            "terminal_denylist": list(getattr(config, "terminal_command_denylist", []) or []),
        },
        "tool_rows": [
            {
                "name": name,
                "status": tool_states[name],
                "loaded": tool_states[name] not in {"unregistered", "filtered"},
                "allowed": tool_states[name] == "available",
                "reason": tool_details.get(name, ""),
            }
            for name in CODING_TOOL_NAMES
        ],
        "blocked_tools": [name for name, status in tool_states.items() if status == "disabled"],
        "missing_tools": [name for name, status in tool_states.items() if status in {"unregistered", "filtered"}],
        "sandbox": sandbox,
    }


def first_coding_blocker(report: Mapping[str, Any], *, require_write: bool) -> str:
    """Return the actionable diagnostic for a coding request preflight."""

    if require_write and bool(report.get("ready_for_editing")):
        return ""
    if not require_write and bool(report.get("ready_for_reading")):
        return ""
    blockers = list(report.get("blockers") or [])
    if blockers:
        return str((blockers[0] or {}).get("message") or "Coding is not ready for this Session.")
    return "Coding is not ready for this Session. Open Tools to inspect the current Project, model, and permissions."
