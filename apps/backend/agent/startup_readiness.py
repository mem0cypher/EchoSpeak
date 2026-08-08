"""Typed product-readiness projection for the desktop hydration gate.

Health proves that HTTP is alive. This module separately reports foundational
runtime readiness and optional full-agent capabilities.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from config import DATA_DIR


DESKTOP_PROTOCOL_VERSION = "1"
RUNTIME_SCHEMA_VERSION = 2


def _component(
    key: str,
    label: str,
    loader: Callable[[], dict[str, Any] | None],
    *,
    critical: bool = True,
) -> dict[str, Any]:
    try:
        detail = dict(loader() or {})
        ready = bool(detail.pop("ready", True))
        degraded = bool(detail.pop("degraded", not ready))
        return {
            "key": key,
            "label": label,
            "ready": ready,
            "degraded": degraded,
            "critical": critical,
            "detail": str(detail.pop("detail", "Ready")),
            "metadata": detail,
        }
    except Exception as exc:
        return {
            "key": key,
            "label": label,
            "ready": False,
            "degraded": not critical,
            "critical": critical,
            "detail": str(exc),
            "metadata": {},
        }


def _data_root() -> dict[str, Any]:
    root = Path(DATA_DIR).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".echospeak-readiness-", dir=root, delete=True):
        pass
    return {"path": str(root), "writable": True, "detail": "Local data is available"}


def _projects() -> dict[str, Any]:
    from agent.projects import get_project_manager

    manager = get_project_manager()
    return {
        "count": len(manager.list_projects()),
        "root": str(manager.projects_dir.resolve()),
        "detail": "Projects loaded",
    }


def _sessions() -> dict[str, Any]:
    from agent.threads import get_thread_manager

    manager = get_thread_manager()
    rows = manager.list_threads(include_archived=True, limit=100000)
    return {
        "count": len(rows),
        "active_session_id": rows[0].thread_id if rows else "",
        "detail": "Sessions restored",
    }


def _active_scope() -> dict[str, Any]:
    from agent.state import get_state_store
    from agent.threads import get_thread_manager

    rows = get_thread_manager().list_threads(include_archived=False, limit=1)
    session_id = rows[0].thread_id if rows else ""
    state = get_state_store().get_thread_state(session_id) if session_id else None
    return {
        "active_session_id": session_id,
        "active_project_id": str(getattr(state, "active_project_id", "") or ""),
        "detail": "Active scope resolved",
    }


def _tools() -> dict[str, Any]:
    from agent.tool_registry import ToolRegistry
    from agent.tools import TOOL_METADATA, get_available_tools

    ToolRegistry.register_from_metadata(get_available_tools(), TOOL_METADATA)
    entries = ToolRegistry.get_all()
    names = set(entries)
    if not names:
        raise RuntimeError("ToolRegistry loaded no tools")
    available = [entry for entry in entries.values() if entry.available]
    origins: dict[str, int] = {}
    for entry in entries.values():
        origins[entry.origin] = origins.get(entry.origin, 0) + 1
    return {
        "count": len(names),
        "available_count": len(available),
        "unavailable_count": len(names) - len(available),
        "origins": origins,
        "ready": bool(available),
        "detail": "Canonical Tool Registry loaded",
    }


def _skills() -> dict[str, Any]:
    from agent.skills_registry import SkillsRegistry

    SkillsRegistry.refresh()
    return {"count": len(SkillsRegistry.list_manifests(include_disabled=True)), "detail": "Skills loaded"}


def _runtime_state() -> dict[str, Any]:
    from agent.state import get_state_store

    store = get_state_store()
    approvals = store.list_approvals(status="pending", limit=200)
    executions = store.list_executions(limit=500)
    active = [row for row in executions if row.status not in {"complete", "failed", "cancelled"}]
    return {
        "pending_approvals": len(approvals),
        "active_executions": len(active),
        "root": str(store.root.resolve()),
        "detail": "Approvals and active work restored",
    }


def _memory() -> dict[str, Any]:
    root = Path(DATA_DIR) / "memory"
    root.mkdir(parents=True, exist_ok=True)
    from agent import memory as memory_module
    from config import config

    instance = None
    configured = os.path.normcase(str(Path(config.memory_path).resolve(strict=False)))
    with memory_module._MEMORY_INSTANCES_GUARD:
        instance = memory_module._MEMORY_INSTANCES.get(configured)
    if instance is not None:
        status = instance.capability_status()
        return {**status, "detail": "Typed memory is available"}
    return {
        "root": str(root.resolve()),
        "typed_memory_ready": True,
        "semantic_retrieval_ready": False,
        "detail": "Typed memory store is available; semantic retrieval has not initialized",
    }


def _model() -> dict[str, Any]:
    from api.server import _check_provider_readiness, _resolve_runtime_provider
    from config import config

    provider = _resolve_runtime_provider()
    result = _check_provider_readiness(provider, timeout=0.5)
    model_id = str(getattr(getattr(config, "local", None), "model_name", "") or "")
    return {
        "provider": provider.value,
        "model_id": model_id,
        "ready": bool(result.get("ok")),
        "degraded": not bool(result.get("ok")),
        "detail": str(result.get("detail") or ("Model provider is ready" if result.get("ok") else "Model provider unavailable")),
    }


def _adapter() -> dict[str, Any]:
    from agent.model_runtime import get_model_adapter
    from api.server import _resolve_runtime_provider
    from config import config

    provider = _resolve_runtime_provider().value
    model_id = str(getattr(getattr(config, "local", None), "model_name", "") or "")
    adapter = get_model_adapter(provider, model_id)
    caps = adapter.capabilities
    return {
        "provider": provider,
        "model_id": model_id,
        "family": caps.family.value,
        "adapter_version": caps.adapter_version,
        "detail": "Model-family adapter resolved",
    }


def _connections_mcp() -> dict[str, Any]:
    from agent.connections import get_connection_registry
    from agent.mcp_client import get_mcp_manager

    connections = get_connection_registry().readiness_status()
    mcp = get_mcp_manager().status()
    configured = int(mcp.get("configured_count") or 0)
    running = int(mcp.get("running_count") or 0)
    degraded = bool(connections.get("unavailable_count")) or bool(configured and running < configured)
    return {
        "connections": connections,
        "mcp": mcp,
        "ready": not degraded,
        "degraded": degraded,
        "detail": "Connections and MCP capabilities inspected" if not degraded else "One or more Connections/MCP servers are unavailable",
    }


def _embeddings() -> dict[str, Any]:
    from agent import memory as memory_module

    with memory_module._MEMORY_INSTANCES_GUARD:
        instances = list(memory_module._MEMORY_INSTANCES.values())
    if not instances:
        return {"ready": False, "degraded": True, "detail": "Embedding runtime has not initialized"}
    status = instances[0].capability_status()
    ready = bool(status.get("semantic_retrieval_ready"))
    return {**status, "ready": ready, "degraded": not ready, "detail": "Embeddings ready" if ready else "Typed memory active without semantic retrieval"}


def _document_retrieval() -> dict[str, Any]:
    from api.server import get_existing_agent
    from config import config

    enabled = bool(getattr(config, "document_rag_enabled", False))
    if not enabled:
        return {"enabled": False, "ready": True, "degraded": False, "detail": "Document retrieval is disabled by configuration"}
    agent = get_existing_agent("default") or get_existing_agent(None)
    store = getattr(agent, "document_store", None) if agent is not None else None
    if store is None:
        return {"enabled": True, "ready": False, "degraded": True, "detail": "Document retrieval requested but unavailable"}
    status = store.capability_status()
    return {
        **status,
        "degraded": bool(status.get("degraded", not bool(status.get("ready")))),
        "detail": str(status.get("detail") or "Document retrieval inspected"),
    }


def _jobs() -> dict[str, Any]:
    from agent.generation_runtime import get_generation_job_store
    from agent.voice_runtime import get_voice_job_store

    generation = get_generation_job_store().list(limit=10000)
    voice = get_voice_job_store().list(limit=10000)
    return {"generation": len(generation), "voice": len(voice), "detail": "Jobs restored"}


def _media() -> dict[str, Any]:
    from agent.media_library import get_media_library_store

    store = get_media_library_store()
    return {"count": len(store.list(limit=10000)), "root": str(store.root), "detail": "Media catalog loaded"}


def _tasks() -> dict[str, Any]:
    from agent.task_store import get_task_store

    store = get_task_store()
    return {"count": len(store.list()), "root": str(store.path), "detail": "Tasks restored"}


def _routines() -> dict[str, Any]:
    from agent.routines import get_routine_manager

    manager = get_routine_manager()
    return {
        "count": len(manager.list_routines()),
        "root": str(manager.routines_dir.resolve()),
        "detail": "Routines restored",
    }


def _heartbeat() -> dict[str, Any]:
    from agent.heartbeat import get_heartbeat_manager
    from config import config

    manager = get_heartbeat_manager()
    return {
        "enabled": bool(getattr(config, "heartbeat_enabled", False)),
        "running": bool(manager and manager.is_running),
        "detail": "Heartbeat state restored",
    }


def _schema() -> dict[str, Any]:
    from agent.state import get_state_store

    store = get_state_store()
    version = RUNTIME_SCHEMA_VERSION
    if store.schema_path.exists():
        import json

        payload = json.loads(store.schema_path.read_text(encoding="utf-8"))
        version = int(payload.get("version", 0)) if isinstance(payload, dict) else 0
    if version != RUNTIME_SCHEMA_VERSION:
        raise RuntimeError(f"Runtime schema {version} is incompatible with {RUNTIME_SCHEMA_VERSION}")
    return {"version": version, "detail": "Schema is compatible"}


def build_startup_readiness() -> dict[str, Any]:
    components = [
        _component("backend", "Starting local runtime", lambda: {"healthy": True}),
        _component("protocol", "Checking desktop protocol", lambda: {"version": DESKTOP_PROTOCOL_VERSION}),
        _component("data_root", "Preparing local data", _data_root),
        _component("projects", "Loading Projects", _projects),
        _component("sessions", "Restoring Sessions", _sessions),
        _component("active_scope", "Restoring workspace", _active_scope),
        _component("tools", "Loading tools", _tools),
        _component("skills", "Loading skills", _skills),
        _component("memory", "Restoring memory", _memory),
        _component("model", "Checking model provider", _model, critical=False),
        _component("adapter", "Resolving model adapter", _adapter, critical=False),
        _component("connections_mcp", "Checking Connections and MCP", _connections_mcp, critical=False),
        _component("embeddings", "Checking semantic retrieval", _embeddings, critical=False),
        _component("document_retrieval", "Checking document retrieval", _document_retrieval, critical=False),
        _component("runtime_state", "Restoring work", _runtime_state),
        _component("jobs", "Restoring jobs", _jobs),
        _component("media", "Loading Media", _media),
        _component("tasks", "Restoring Tasks", _tasks),
        _component("routines", "Restoring Routines", _routines),
        _component("heartbeat", "Restoring Heartbeat", _heartbeat),
        _component("schema", "Checking data compatibility", _schema),
    ]
    critical = [item for item in components if item["critical"]]
    completed = sum(1 for item in critical if item["ready"])
    core_ready = completed == len(critical)
    full_ready = all(item["ready"] for item in components)
    degraded_capabilities = [
        item["key"] for item in components
        if item.get("degraded") or (not item["ready"] and not item["critical"])
    ]
    current = next((item for item in critical if not item["ready"]), critical[-1])
    scope = next((item for item in components if item["key"] == "active_scope"), {"metadata": {}})
    return {
        "protocol_version": DESKTOP_PROTOCOL_VERSION,
        "runtime_schema_version": RUNTIME_SCHEMA_VERSION,
        "core_ready": core_ready,
        "backend_available": True,
        "full_ready": full_ready,
        "degraded": bool(degraded_capabilities),
        "degraded_capabilities": degraded_capabilities,
        "status": "Ready" if full_ready else "Ready with degraded capabilities" if core_ready else current["label"],
        "completed_steps": completed,
        "total_steps": len(critical),
        "data_root": str(Path(DATA_DIR).expanduser().resolve()),
        "active_project_id": str(scope["metadata"].get("active_project_id") or ""),
        "active_session_id": str(scope["metadata"].get("active_session_id") or ""),
        "components": components,
        "checked_at": time.time(),
        "runtime_kind": os.getenv("ECHOSPEAK_RUNTIME_KIND", "browser") or "browser",
        "instance_id": os.getenv("ECHOSPEAK_DESKTOP_INSTANCE_ID", ""),
    }
