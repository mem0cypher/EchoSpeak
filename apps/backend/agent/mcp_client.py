"""Official-SDK Model Context Protocol client for EchoSpeak.

The MCP SDK owns framing, negotiation, pagination, cancellation and transport
lifecycle. EchoSpeak owns Connection scope, capability policy, ToolRuns and
approvals. Server-provided annotations are descriptive hints, never execution
authority unless a reviewed configuration explicitly accepts them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

import httpx
from loguru import logger

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    _SDK_AVAILABLE = True
    _SDK_ERROR = ""
except Exception as exc:  # pragma: no cover - dependency/readiness boundary
    ClientSession = Any  # type: ignore[assignment,misc]
    StdioServerParameters = Any  # type: ignore[assignment,misc]
    stdio_client = streamable_http_client = sse_client = None  # type: ignore[assignment]
    _SDK_AVAILABLE = False
    _SDK_ERROR = str(exc)


def re_sub_safe(name: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", str(name or "").strip())
    return value.strip("_") or "unnamed"


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="json", by_alias=True, exclude_none=True))
    if isinstance(value, dict):
        return dict(value)
    return {}


def _safe_error(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)\bbearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"(?i)\b(token|secret|password|authorization|api[_-]?key)\s*[=:]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    return text[:1000]


def _validate_http_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("MCP HTTP transport requires an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("MCP URL userinfo must be stored as a credential, not embedded in the URL")
    hostname = parsed.hostname.casefold()
    local = hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not local:
        raise ValueError("Remote MCP HTTP transport requires HTTPS")
    return parsed.geturl()


_SHELL_COMMANDS = {
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
    "bash",
    "sh",
    "zsh",
}


@dataclass
class MCPServerState:
    name: str
    command: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict, repr=False)
    cwd: str = ""
    transport: str = "stdio"
    url: str = ""
    headers: Dict[str, str] = field(default_factory=dict, repr=False)
    enabled: bool = True
    timeout_s: float = 15.0
    capability_policies: Dict[str, str] = field(default_factory=dict)
    accept_server_read_only_hints: bool = False
    connection_id: str = ""
    project_ids: List[str] = field(default_factory=list)
    session_ids: List[str] = field(default_factory=list)
    allow_global: bool = True
    running: bool = False
    last_error: str = ""
    tools: List[Dict[str, Any]] = field(default_factory=list)
    resources: List[Dict[str, Any]] = field(default_factory=list)
    resource_templates: List[Dict[str, Any]] = field(default_factory=list)
    prompts: List[Dict[str, Any]] = field(default_factory=list)
    server_capabilities: Dict[str, Any] = field(default_factory=dict)
    protocol_version: str = ""
    inventory_changed: bool = False
    last_progress: Dict[str, Any] = field(default_factory=dict)
    started_at: Optional[float] = None


class MCPSession:
    """Persistent official-SDK session hosted on one private asyncio thread."""

    def __init__(
        self,
        state: MCPServerState,
        inventory_callback: Optional[Callable[["MCPSession"], None]] = None,
    ):
        self.state = state
        self._inventory_callback = inventory_callback
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._session: Any = None
        self._stop_event: Optional[asyncio.Event] = None
        self._ready = threading.Event()
        self._closed = False

    def start(self) -> bool:
        if not _SDK_AVAILABLE:
            self.state.last_error = f"Official MCP SDK unavailable: {_safe_error(_SDK_ERROR)}"
            return False
        try:
            self._validate_configuration()
        except Exception as exc:
            self.state.last_error = _safe_error(exc)
            return False
        self._closed = False
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._thread_main,
            name=f"mcp-sdk-{re_sub_safe(self.state.name)}",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=max(2.0, self.state.timeout_s + 2.0)):
            self.state.last_error = "Timed out initializing the MCP SDK session"
            self.stop()
            return False
        return bool(self.state.running)

    def _validate_configuration(self) -> None:
        transport = self.state.transport.casefold().replace("-", "_")
        if transport in {"http", "streamablehttp"}:
            transport = "streamable_http"
        if transport not in {"stdio", "streamable_http", "sse"}:
            raise ValueError(f"Unsupported MCP transport: {self.state.transport}")
        self.state.transport = transport
        self.state.timeout_s = min(max(float(self.state.timeout_s), 1.0), 300.0)
        if transport == "stdio":
            if not self.state.command:
                raise ValueError("MCP stdio transport requires a command")
            command_name = Path(self.state.command).name.casefold()
            if command_name in _SHELL_COMMANDS:
                raise ValueError("MCP stdio may not launch an unrestricted shell")
            if any(
                marker in str(argument).casefold()
                for argument in self.state.args
                for marker in ("token", "secret", "password", "api-key", "api_key", "bearer")
            ):
                raise ValueError(
                    "MCP credentials may not be embedded in process arguments; use brokered environment values"
                )
            if self.state.cwd and not Path(self.state.cwd).expanduser().is_dir():
                raise ValueError("MCP stdio working directory does not exist")
        else:
            self.state.url = _validate_http_url(self.state.url)

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run())
        except Exception as exc:
            self.state.last_error = _safe_error(exc)
            self.state.running = False
            self._ready.set()
        finally:
            self.state.running = False
            self._session = None
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    async def _message_handler(self, message: Any) -> None:
        representation = repr(message).casefold()
        if "listchanged" in representation or "list_changed" in representation:
            self.state.inventory_changed = True
            asyncio.create_task(self._refresh_after_notification())

    async def _refresh_after_notification(self) -> None:
        await asyncio.sleep(0)
        try:
            await self._refresh_inventory()
            if self._inventory_callback is not None:
                self._inventory_callback(self)
        except Exception as exc:
            self.state.last_error = _safe_error(exc)

    async def _run(self) -> None:
        self._stop_event = asyncio.Event()
        async with AsyncExitStack() as stack:
            if self.state.transport == "stdio":
                environment = os.environ.copy()
                environment.update({str(key): str(value) for key, value in self.state.env.items()})
                parameters = StdioServerParameters(
                    command=self.state.command,
                    args=list(self.state.args),
                    env=environment,
                    cwd=self.state.cwd or None,
                )
                read_stream, write_stream = await stack.enter_async_context(stdio_client(parameters))
            else:
                client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=dict(self.state.headers),
                        timeout=httpx.Timeout(self.state.timeout_s),
                        follow_redirects=True,
                    )
                )
                if self.state.transport == "streamable_http":
                    read_stream, write_stream, _session_id = await stack.enter_async_context(
                        streamable_http_client(self.state.url, http_client=client)
                    )
                else:
                    read_stream, write_stream = await stack.enter_async_context(
                        sse_client(
                            self.state.url,
                            headers=dict(self.state.headers),
                            timeout=self.state.timeout_s,
                            sse_read_timeout=max(60.0, self.state.timeout_s),
                        )
                    )
            session = await stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=self.state.timeout_s),
                    message_handler=self._message_handler,
                )
            )
            result = await session.initialize()
            self._session = session
            initialized = _model_dump(result)
            self.state.protocol_version = str(
                initialized.get("protocolVersion")
                or initialized.get("protocol_version")
                or ""
            )
            self.state.server_capabilities = dict(initialized.get("capabilities") or {})
            await self._refresh_inventory()
            self.state.running = True
            self.state.started_at = time.time()
            self.state.last_error = ""
            self._ready.set()
            await self._stop_event.wait()

    async def _paginate(self, method_name: str, field_name: str) -> list[dict[str, Any]]:
        if self._session is None:
            return []
        rows: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            result = await getattr(self._session, method_name)(cursor=cursor)
            payload = _model_dump(result)
            raw_rows = payload.get(field_name) or []
            rows.extend(_model_dump(item) for item in raw_rows)
            cursor = str(payload.get("nextCursor") or payload.get("next_cursor") or "").strip() or None
            if not cursor:
                return rows

    async def _refresh_inventory(self) -> None:
        self.state.tools = await self._paginate("list_tools", "tools")
        capabilities = dict(self.state.server_capabilities or {})
        if capabilities.get("resources") is not None:
            self.state.resources = await self._paginate("list_resources", "resources")
            self.state.resource_templates = await self._paginate(
                "list_resource_templates", "resourceTemplates"
            )
        if capabilities.get("prompts") is not None:
            self.state.prompts = await self._paginate("list_prompts", "prompts")
        self.state.inventory_changed = False

    def _submit(self, coroutine: Any, *, timeout: Optional[float] = None) -> Any:
        if not self.state.running or self._loop is None or self._session is None:
            raise RuntimeError(self.state.last_error or "MCP session is not running")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=timeout or self.state.timeout_s + 1.0)
        except Exception:
            future.cancel()
            raise

    def refresh_inventory(self) -> List[Dict[str, Any]]:
        self._submit(self._refresh_inventory())
        return list(self.state.tools)

    def list_tools(self) -> List[Dict[str, Any]]:
        if self.state.inventory_changed:
            return self.refresh_inventory()
        return list(self.state.tools)

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async def progress(progress: float, total: Optional[float], message: Optional[str]) -> None:
            self.state.last_progress = {
                "progress": progress,
                "total": total,
                "message": str(message or "")[:300],
                "at": time.time(),
            }

        result = await self._session.call_tool(
            name,
            arguments,
            read_timeout_seconds=timedelta(seconds=self.state.timeout_s),
            progress_callback=progress,
        )
        return _model_dump(result)

    def call_tool(self, name: str, arguments: Optional[dict] = None) -> str:
        try:
            result = self._submit(
                self._call_tool(str(name), dict(arguments or {})),
                timeout=self.state.timeout_s + 2.0,
            )
            # Preserve structuredContent, rich content, resource links and
            # error state. The governed ToolOutcome boundary owns semantics.
            return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        except Exception as exc:
            self.state.last_error = _safe_error(exc)
            return json.dumps(
                {
                    "isError": True,
                    "error": {
                        "code": "mcp_tool_error",
                        "message": self.state.last_error,
                        "retryable": True,
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )

    def read_resource(self, uri: str) -> dict[str, Any]:
        async def _read() -> dict[str, Any]:
            from pydantic import AnyUrl

            return _model_dump(await self._session.read_resource(AnyUrl(uri)))

        return self._submit(_read())

    def get_prompt(self, name: str, arguments: Optional[dict[str, str]] = None) -> dict[str, Any]:
        async def _get() -> dict[str, Any]:
            return _model_dump(await self._session.get_prompt(name, arguments or {}))

        return self._submit(_get())

    def stop(self) -> None:
        self._closed = True
        loop = self._loop
        event = self._stop_event
        if loop is not None and event is not None and loop.is_running():
            loop.call_soon_threadsafe(event.set)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self.state.running = False


class MCPManager:
    """Manage SDK sessions and project capability-level policy into ToolRegistry."""

    def __init__(self) -> None:
        self.servers: Dict[str, MCPServerState] = {}
        self.sessions: Dict[str, MCPSession] = {}
        self.initialized = False
        self.last_error = ""
        self._registered_names: List[str] = []
        self._lock = threading.RLock()

    @property
    def loaded_tool_count(self) -> int:
        return len(self._registered_names)

    def status(self) -> Dict[str, Any]:
        try:
            from agent.tool_registry import ToolRegistry

            for name, session in list(self.sessions.items()):
                if not session.state.running:
                    ToolRegistry.set_owner_availability(
                        f"mcp:{name}",
                        available=False,
                        health="unhealthy",
                        reason=session.state.last_error or "MCP session stopped",
                    )
        except Exception:
            pass
        servers = [
            {
                "name": name,
                "connection_id": state.connection_id,
                "running": state.running,
                "tool_count": len(state.tools),
                "resource_count": len(state.resources),
                "prompt_count": len(state.prompts),
                "last_error": _safe_error(state.last_error),
                "transport": state.transport,
                "enabled": state.enabled,
                "protocol_version": state.protocol_version,
                "inventory_changed": state.inventory_changed,
                "last_progress": dict(state.last_progress),
            }
            for name, state in self.servers.items()
        ]
        running = sum(1 for state in self.servers.values() if state.running)
        return {
            "configured_count": len(self.servers),
            "running_count": running,
            "initialized": self.initialized,
            "loaded_tool_count": self.loaded_tool_count,
            "last_error": _safe_error(self.last_error),
            "servers": servers,
            "failed_servers": [
                row for row in servers if row["last_error"] and not row["running"]
            ],
            "client_present": _SDK_AVAILABLE,
            "client_version": "official-python-sdk-1.x",
            "supported_transports": ["stdio", "streamable_http", "sse"],
        }

    @staticmethod
    def _state_from_config(name: str, config: dict[str, Any]) -> MCPServerState:
        transport = str(config.get("transport") or "stdio").strip().casefold()
        return MCPServerState(
            name=str(name),
            command=str(config.get("command") or config.get("cmd") or "").strip(),
            args=[str(item) for item in list(config.get("args") or [])],
            env={
                str(key): str(value)
                for key, value in dict(config.get("env") or {}).items()
            },
            cwd=str(config.get("cwd") or "").strip(),
            transport=transport,
            url=str(config.get("url") or config.get("endpoint") or "").strip(),
            headers={
                str(key): str(value)
                for key, value in dict(config.get("headers") or {}).items()
            },
            enabled=bool(config.get("enabled", True)),
            timeout_s=float(config.get("timeout_s") or config.get("timeout") or 15),
            capability_policies={
                str(key): str(value).strip().casefold()
                for key, value in dict(config.get("capability_policies") or {}).items()
            },
            accept_server_read_only_hints=bool(config.get("accept_server_read_only_hints", False)),
            connection_id=str(config.get("connection_id") or f"mcp-{re_sub_safe(name)}"),
            project_ids=[str(item) for item in list(config.get("project_ids") or []) if str(item).strip()],
            session_ids=[str(item) for item in list(config.get("session_ids") or []) if str(item).strip()],
            allow_global=bool(config.get("allow_global", not bool(config.get("project_ids")))),
        )

    def initialize_servers(self, servers: Any) -> Dict[str, Any]:
        with self._lock:
            self.shutdown()
            self.last_error = ""
            if not isinstance(servers, dict) or not servers:
                return self.status()
            for name, raw in servers.items():
                if not isinstance(raw, dict):
                    continue
                state = self._state_from_config(str(name), raw)
                self.servers[state.name] = state
                if not state.enabled:
                    state.last_error = "disabled in Connection configuration"
                    self._sync_connection(state, raw)
                    continue
                session = MCPSession(state, self._inventory_changed)
                self.sessions[state.name] = session
                if not session.start():
                    self.last_error = state.last_error or self.last_error
                    self._sync_connection(state, raw)
                    continue
                for definition in session.list_tools():
                    try:
                        self._register_tool(state.name, definition, session)
                    except Exception as exc:
                        state.last_error = _safe_error(exc)
                        self.last_error = state.last_error
                        logger.warning("MCP capability registration rejected for '{}': {}", state.name, state.last_error)
                self._sync_connection(state, raw)
            self.initialized = bool(self.servers)
            return self.status()

    def _inventory_changed(self, session: MCPSession) -> None:
        """Atomically reconcile one server's changed capability list."""
        state = session.state
        with self._lock:
            from agent.tool_registry import ToolRegistry

            owner = f"mcp:{state.name}"
            for name in list(self._registered_names):
                entry = ToolRegistry.get(name)
                if entry is None or entry.owner != owner:
                    continue
                ToolRegistry.remove_owned(name, owner)
                self._registered_names.remove(name)
            for definition in session.list_tools():
                try:
                    self._register_tool(state.name, definition, session)
                except Exception as exc:
                    state.last_error = _safe_error(exc)
                    logger.warning(
                        "MCP changed capability registration rejected for '{}': {}",
                        state.name,
                        state.last_error,
                    )
            self._sync_connection(state, {"display_name": state.name})

    def _capability_risk(
        self,
        state: MCPServerState,
        definition: dict[str, Any],
    ) -> tuple[str, bool]:
        name = str(definition.get("name") or "")
        policy = str(state.capability_policies.get(name) or "").casefold()
        if policy in {"read", "safe", "read_only"}:
            return "safe", False
        if policy in {"destructive", "delete"}:
            return "destructive", True
        if policy in {"write", "action", "moderate"}:
            return "moderate", True
        annotations = dict(definition.get("annotations") or {})
        if bool(annotations.get("destructiveHint") or annotations.get("destructive_hint")):
            return "destructive", True
        if state.accept_server_read_only_hints and bool(
            annotations.get("readOnlyHint") or annotations.get("read_only_hint")
        ):
            return "safe", False
        # Unknown remote code is action-capable until the user reviews this
        # exact capability. No server-wide "trusted" bypass exists.
        return "moderate", True

    def _register_tool(
        self,
        server_name: str,
        definition: dict[str, Any],
        session: MCPSession,
    ) -> None:
        from agent.tool_registry import ToolEntry, ToolRegistry

        raw_name = str(definition.get("name") or "").strip()
        if not raw_name:
            return
        registered_name = f"mcp__{re_sub_safe(server_name)}__{re_sub_safe(raw_name)}"
        description = str(
            definition.get("description")
            or f"MCP capability {raw_name} from {server_name}"
        )[:1000]
        input_schema = (
            dict(definition.get("inputSchema"))
            if isinstance(definition.get("inputSchema"), dict)
            else {}
        )
        output_schema = (
            dict(definition.get("outputSchema"))
            if isinstance(definition.get("outputSchema"), dict)
            else {}
        )
        risk, is_action = self._capability_risk(session.state, definition)
        tool = self._make_langchain_tool(
            registered_name,
            description,
            session,
            raw_name,
            input_schema,
        )
        ToolRegistry.register_entry(
            ToolEntry(
                name=registered_name,
                func=tool,
                description=description,
                category="mcp",
                is_action=is_action,
                risk_level=risk,
                owner=f"mcp:{server_name}",
                origin="mcp",
                connection_id=session.state.connection_id,
                mcp_server=server_name,
                health="healthy",
                input_schema=input_schema,
                output_schema=output_schema,
                approval_required=is_action,
                available=True,
                project_ids=tuple(session.state.project_ids),
                session_ids=tuple(session.state.session_ids),
            ),
            reject_conflicts=True,
        )
        self._registered_names.append(registered_name)

    @staticmethod
    def _make_langchain_tool(
        registered_name: str,
        description: str,
        session: MCPSession,
        raw_name: str,
        input_schema: dict[str, Any],
    ) -> Any:
        def invoke(**kwargs: Any) -> str:
            if len(kwargs) == 1 and isinstance(kwargs.get("kwargs"), dict):
                kwargs = kwargs["kwargs"]
            return session.call_tool(
                raw_name,
                {key: value for key, value in kwargs.items() if value is not None},
            )

        try:
            from langchain_core.tools import StructuredTool
            from pydantic import Field, create_model
            from typing import Any as TypingAny
            from typing import Optional as TypingOptional

            properties = dict(input_schema.get("properties") or {})
            required = set(input_schema.get("required") or [])
            fields: Dict[str, Any] = {}
            for key in properties:
                fields[str(key)] = (
                    TypingAny if key in required else TypingOptional[TypingAny],
                    Field(...) if key in required else Field(default=None),
                )
            if not fields:
                fields["input"] = (TypingOptional[TypingAny], Field(default=None))
            args_model = create_model(
                f"MCPArgs_{re_sub_safe(registered_name)}"[:80],
                **fields,
            )
            return StructuredTool.from_function(
                func=invoke,
                name=registered_name,
                description=description,
                args_schema=args_model,
            )
        except Exception as exc:
            logger.debug("MCP StructuredTool build failed for {}: {}", registered_name, exc)

            class MCPToolShim:
                name = registered_name
                description = description

                def invoke(self, input: Any = None, **kwargs: Any) -> str:  # noqa: A002
                    payload = input if isinstance(input, dict) else kwargs
                    return invoke(**dict(payload or {}))

                def __call__(self, **kwargs: Any) -> str:
                    return invoke(**kwargs)

            return MCPToolShim()

    def _sync_connection(self, state: MCPServerState, raw: dict[str, Any]) -> None:
        from agent.connections import (
            ConnectionAuthentication,
            ConnectionCapability,
            ConnectionCapabilityKind,
            ConnectionCapabilityRisk,
            ConnectionHealth,
            ConnectionKind,
            ConnectionLifecycle,
            ConnectionRecord,
            ConnectionScope,
            get_connection_registry,
        )

        credential_refs: list[str] = []
        secret_bundle = {
            key: value
            for key, value in {
                "env": dict(state.env),
                "headers": dict(state.headers),
            }.items()
            if value
        }
        existing = get_connection_registry().get_unscoped(state.connection_id)
        if secret_bundle:
            from agent.credential_broker import get_credential_broker

            prior_ref = existing.credential_refs[0] if existing and existing.credential_refs else ""
            credential_refs = [
                get_credential_broker().put(
                    secret_bundle,
                    label=f"EchoSpeak MCP {state.name}",
                    reference=prior_ref,
                )
            ]
        elif existing:
            credential_refs = list(existing.credential_refs)
        capabilities = []
        for definition in state.tools:
            risk_name, requires_approval = self._capability_risk(state, definition)
            capabilities.append(
                ConnectionCapability(
                    id=f"tool:{definition.get('name')}",
                    kind=ConnectionCapabilityKind.TOOL,
                    name=str(definition.get("title") or definition.get("name") or "MCP tool"),
                    description=str(definition.get("description") or "")[:1000],
                    enabled=True,
                    available=state.running,
                    requires_approval=requires_approval,
                    risk=(
                        ConnectionCapabilityRisk.DESTRUCTIVE
                        if risk_name == "destructive"
                        else ConnectionCapabilityRisk.WRITE
                        if requires_approval
                        else ConnectionCapabilityRisk.READ
                    ),
                    tool_names=[
                        f"mcp__{re_sub_safe(state.name)}__{re_sub_safe(str(definition.get('name') or ''))}"
                    ],
                    permissions=[f"mcp.{state.name}.{definition.get('name')}"],
                    metadata={
                        "input_schema_present": bool(definition.get("inputSchema")),
                        "output_schema_present": bool(definition.get("outputSchema")),
                    },
                )
            )
        for resource in state.resources:
            uri = str(resource.get("uri") or "")
            capabilities.append(
                ConnectionCapability(
                    id=f"resource:{hashlib.sha256(uri.encode('utf-8')).hexdigest()[:24]}",
                    kind=ConnectionCapabilityKind.RESOURCE,
                    name=str(resource.get("title") or resource.get("name") or uri or "MCP resource")[:200],
                    description=str(resource.get("description") or "")[:1000],
                    risk=ConnectionCapabilityRisk.READ,
                    resource_types=[str(resource.get("mimeType") or "mcp_resource")],
                    permissions=[f"mcp.{state.name}.resources.read"],
                    metadata={
                        "uri_sha256": hashlib.sha256(uri.encode("utf-8")).hexdigest()
                    },
                )
            )
        for prompt in state.prompts:
            prompt_name = str(prompt.get("name") or "")
            capabilities.append(
                ConnectionCapability(
                    id=f"prompt:{prompt_name}"[:200],
                    kind=ConnectionCapabilityKind.RESOURCE,
                    name=str(prompt.get("title") or prompt_name or "MCP prompt")[:200],
                    description=str(prompt.get("description") or "")[:1000],
                    risk=ConnectionCapabilityRisk.READ,
                    resource_types=["mcp_prompt"],
                    permissions=[f"mcp.{state.name}.prompts.read"],
                    metadata={"prompt_name": prompt_name},
                )
            )
        scope = ConnectionScope(
            allow_global=state.allow_global,
            project_ids=list(state.project_ids),
            session_ids=list(state.session_ids),
            network_hosts=(
                [str(urlsplit(state.url).hostname or "")]
                if state.url
                else []
            ),
            permissions=[
                permission
                for capability in capabilities
                for permission in capability.permissions
            ],
        )
        health = (
            ConnectionHealth.HEALTHY
            if state.running
            else ConnectionHealth.DISABLED
            if not state.enabled
            else ConnectionHealth.UNHEALTHY
        )
        lifecycle = (
            ConnectionLifecycle.CONNECTED
            if state.running
            else ConnectionLifecycle.DISABLED
            if not state.enabled
            else ConnectionLifecycle.DEGRADED
        )
        authentication = (
            ConnectionAuthentication.CONFIGURED
            if credential_refs
            else ConnectionAuthentication.NONE
        )
        metadata = {
            "transport": state.transport,
            "url": state.url,
            "protocol_version": state.protocol_version,
            "resource_count": len(state.resources),
            "prompt_count": len(state.prompts),
        }
        registry = get_connection_registry()
        if existing is None:
            registry.register(
                ConnectionRecord(
                    id=state.connection_id,
                    kind=ConnectionKind.MCP_SERVER,
                    display_name=str(raw.get("display_name") or state.name),
                    provider="custom_mcp",
                    source_ref=f"mcp:{state.name}",
                    enabled=state.enabled,
                    health=health,
                    authentication=authentication,
                    lifecycle=lifecycle,
                    credential_refs=credential_refs,
                    scope=scope,
                    capabilities=capabilities,
                    errors=[] if state.running else [state.last_error],
                    provenance={"owner": "MCPManager", "sdk": "official-python-sdk"},
                    metadata=metadata,
                    last_checked_at=time.time(),
                )
            )
        else:
            registry.update(
                existing.id,
                expected_revision=existing.revision,
                enabled=state.enabled,
                health=health,
                authentication=authentication,
                lifecycle=lifecycle,
                credential_refs=credential_refs,
                capabilities=capabilities,
                metadata=metadata,
                errors=[] if state.running else [state.last_error],
                last_checked_at=time.time(),
            )

    def probe(self, connection_id: str) -> dict[str, Any]:
        for state in self.servers.values():
            if state.connection_id != connection_id:
                continue
            session = self.sessions.get(state.name)
            if session is None or not state.running:
                return {"ok": False, "error": state.last_error or "MCP session is not running"}
            try:
                session.refresh_inventory()
                return {
                    "ok": True,
                    "tool_count": len(state.tools),
                    "resource_count": len(state.resources),
                    "prompt_count": len(state.prompts),
                    "protocol_version": state.protocol_version,
                }
            except Exception as exc:
                state.last_error = _safe_error(exc)
                return {"ok": False, "error": state.last_error}
        return {"ok": False, "error": "MCP Connection is not configured in this runtime"}

    def list_resources(self, server_name: str) -> list[dict[str, Any]]:
        session = self.sessions.get(str(server_name or ""))
        return list(session.state.resources) if session and session.state.running else []

    def read_resource(self, server_name: str, uri: str) -> dict[str, Any]:
        session = self.sessions.get(str(server_name or ""))
        if session is None:
            raise RuntimeError("MCP server is unavailable")
        return session.read_resource(uri)

    def list_prompts(self, server_name: str) -> list[dict[str, Any]]:
        session = self.sessions.get(str(server_name or ""))
        return list(session.state.prompts) if session and session.state.running else []

    def get_prompt(
        self,
        server_name: str,
        prompt_name: str,
        arguments: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        session = self.sessions.get(str(server_name or ""))
        if session is None:
            raise RuntimeError("MCP server is unavailable")
        return session.get_prompt(prompt_name, arguments)

    def shutdown(self) -> None:
        for session in list(self.sessions.values()):
            try:
                session.stop()
            except Exception:
                pass
        try:
            from agent.tool_registry import ToolRegistry

            for name in list(self._registered_names):
                entry = ToolRegistry.get(name)
                if entry is not None and entry.owner.startswith("mcp:"):
                    ToolRegistry.remove_owned(name, entry.owner)
        except Exception:
            pass
        self._registered_names = []
        self.sessions = {}
        self.servers = {}
        self.initialized = False

    def call(self, registered_name: str, arguments: Optional[dict] = None) -> str:
        parts = str(registered_name or "").split("__", 2)
        if len(parts) != 3 or parts[0] != "mcp":
            return json.dumps({"isError": True, "error": {"code": "invalid_mcp_tool_name"}})
        server_key, tool_key = parts[1], parts[2]
        for server_name, session in self.sessions.items():
            if re_sub_safe(server_name) != server_key:
                continue
            raw_name = next(
                (
                    str(item.get("name"))
                    for item in session.state.tools
                    if re_sub_safe(str(item.get("name") or "")) == tool_key
                ),
                tool_key,
            )
            return session.call_tool(raw_name, arguments or {})
        return json.dumps(
            {"isError": True, "error": {"code": "mcp_server_unavailable", "retryable": True}}
        )


_MANAGER: Optional[MCPManager] = None
_MANAGER_LOCK = threading.Lock()


def get_mcp_manager() -> MCPManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = MCPManager()
        return _MANAGER


def reset_mcp_manager() -> None:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is not None:
            _MANAGER.shutdown()
        _MANAGER = None


def is_mcp_client_present() -> bool:
    return _SDK_AVAILABLE
