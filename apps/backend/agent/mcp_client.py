"""MCP client for EchoSpeak (v7.6.0).

Real stdio JSON-RPC client (MCP framing). Replaces the old stub that only
recorded server configs with loaded_tool_count always 0.

Honest status:
  - configured servers are listed even when start fails
  - mcp_available / loaded tools only when tools/list succeeded
  - never pretends tools work when the process is dead

Config shape (config.mcp_servers dict):
  {
    "demo": {
      "command": "python",
      "args": ["path/to/server.py"],
      "env": {},
      "transport": "stdio",
      "trust": "trusted",
      "enabled": true,
      "timeout_s": 15
    }
  }
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import config  # noqa: E402


# ---------------------------------------------------------------------------
# JSON-RPC / MCP framing (LSP-style Content-Length)
# ---------------------------------------------------------------------------


def _encode_message(payload: dict) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


def _read_message(stdout) -> Optional[dict]:
    """Read one framed MCP message from a binary/text stream."""

    def _readline():
        line = stdout.readline()
        if isinstance(line, bytes):
            return line
        if line is None or line == "":
            return b""
        return line.encode("utf-8")

    headers: Dict[str, str] = {}
    while True:
        line = _readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        try:
            text = line.decode("utf-8", errors="ignore").strip()
        except Exception:
            text = str(line)
        if ":" in text:
            k, v = text.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    length = int(headers.get("content-length") or "0")
    if length <= 0:
        return None
    raw = b""
    remaining = length
    while remaining > 0:
        chunk = stdout.read(remaining)
        if not chunk:
            break
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        raw += chunk
        remaining -= len(chunk)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        logger.warning("MCP decode failed: {}", exc)
        return None


def re_sub_safe(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", str(name or "").strip())
    return s.strip("_") or "unnamed"


@dataclass
class MCPServerState:
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"
    trust: str = "configured"
    enabled: bool = True
    timeout_s: float = 15.0
    running: bool = False
    last_error: str = ""
    tools: List[Dict[str, Any]] = field(default_factory=list)
    process: Any = None
    _lock: Any = field(default=None, repr=False)
    _next_id: int = 0

    def __post_init__(self):
        self._lock = threading.RLock()


class MCPSession:
    """One stdio MCP server process with a background reader thread.

    Windows cannot select() on pipes, so we always drain stdout on a daemon
    thread into a queue and match responses by JSON-RPC id.
    """

    def __init__(self, state: MCPServerState):
        self.state = state
        self._inbox: queue.Queue = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._closed = False

    def start(self) -> bool:
        st = self.state
        if st.transport not in {"stdio", "", "STDIO"}:
            st.last_error = f"Unsupported transport: {st.transport} (only stdio in v7.6.0)"
            st.running = False
            return False
        if not st.command:
            st.last_error = "Missing command"
            st.running = False
            return False
        cmd = [st.command, *list(st.args or [])]
        env = os.environ.copy()
        for k, v in (st.env or {}).items():
            env[str(k)] = str(v)
        try:
            st.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0,
            )
        except Exception as exc:
            st.last_error = f"Failed to start: {exc}"
            st.running = False
            st.process = None
            return False

        self._closed = False
        self._inbox = queue.Queue()
        self._reader = threading.Thread(
            target=self._read_loop,
            name=f"mcp-reader-{st.name}",
            daemon=True,
        )
        self._reader.start()

        try:
            init_result = self.request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "echospeak", "version": "7.6.0"},
                },
            )
            if init_result is None:
                if not st.last_error:
                    st.last_error = "initialize returned no result"
                self.stop()
                return False
            self.notify("notifications/initialized", {})
            st.running = True
            st.last_error = ""
            return True
        except Exception as exc:
            st.last_error = f"initialize failed: {exc}"
            self.stop()
            return False

    def _read_loop(self) -> None:
        st = self.state
        proc = st.process
        if proc is None or proc.stdout is None:
            return
        try:
            while not self._closed:
                if proc.poll() is not None:
                    # Drain any final framed messages then exit
                    try:
                        msg = _read_message(proc.stdout)
                    except Exception:
                        break
                    if msg is None:
                        break
                    self._inbox.put(msg)
                    continue
                msg = _read_message(proc.stdout)
                if msg is None:
                    break
                self._inbox.put(msg)
        except Exception as exc:
            logger.debug("MCP reader exit for {}: {}", st.name, exc)
        finally:
            try:
                self._inbox.put({"_eof": True})
            except Exception:
                pass

    def stop(self) -> None:
        st = self.state
        self._closed = True
        proc = st.process
        st.process = None
        st.running = False
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _next_id(self) -> int:
        self.state._next_id += 1
        return self.state._next_id

    def notify(self, method: str, params: Optional[dict] = None) -> None:
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)

    def request(
        self,
        method: str,
        params: Optional[dict] = None,
        timeout_s: Optional[float] = None,
    ) -> Optional[Any]:
        st = self.state
        with st._lock:
            req_id = self._next_id()
            msg: Dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
            if params is not None:
                msg["params"] = params
            if not self._write(msg):
                return None
            deadline = time.time() + float(timeout_s if timeout_s is not None else st.timeout_s)
            while time.time() < deadline:
                remaining = max(0.05, deadline - time.time())
                try:
                    resp = self._inbox.get(timeout=min(0.5, remaining))
                except queue.Empty:
                    if st.process is None or st.process.poll() is not None:
                        # One more drain attempt after process exit
                        try:
                            resp = self._inbox.get_nowait()
                        except queue.Empty:
                            st.last_error = "MCP process exited"
                            st.running = False
                            return None
                    else:
                        continue
                if not isinstance(resp, dict):
                    continue
                if resp.get("_eof"):
                    st.last_error = "MCP process closed stdout"
                    st.running = False
                    return None
                # Ignore notifications / unmatched ids (server may push events)
                if "id" not in resp or resp.get("id") != req_id:
                    continue
                if "error" in resp:
                    err = resp.get("error") or {}
                    st.last_error = str(err.get("message") or err)[:300]
                    return None
                return resp.get("result")
            st.last_error = f"timeout waiting for {method}"
            return None

    def _write(self, msg: dict) -> bool:
        st = self.state
        proc = st.process
        if proc is None or proc.stdin is None:
            st.last_error = "process not running"
            return False
        try:
            proc.stdin.write(_encode_message(msg))
            proc.stdin.flush()
            return True
        except Exception as exc:
            st.last_error = f"write failed: {exc}"
            st.running = False
            return False

    def list_tools(self) -> List[Dict[str, Any]]:
        result = self.request("tools/list", {})
        if not isinstance(result, dict):
            return []
        tools = result.get("tools")
        if not isinstance(tools, list):
            return []
        out = []
        for t in tools:
            if isinstance(t, dict) and t.get("name"):
                out.append(t)
        self.state.tools = out
        return out

    def call_tool(self, name: str, arguments: Optional[dict] = None) -> str:
        result = self.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout_s=self.state.timeout_s,
        )
        if result is None:
            return f"MCP tool error ({self.state.name}/{name}): {self.state.last_error or 'no result'}"
        if isinstance(result, dict):
            if result.get("isError"):
                parts = result.get("content") or []
                text = " ".join(
                    str(p.get("text") or "") for p in parts if isinstance(p, dict)
                ).strip()
                return f"MCP tool error: {text or result}"
            parts = result.get("content") or []
            texts = []
            for p in parts:
                if isinstance(p, dict) and p.get("type") == "text":
                    texts.append(str(p.get("text") or ""))
            if texts:
                return "\n".join(texts).strip()
            return json.dumps(result, ensure_ascii=False)[:4000]
        return str(result)[:4000]


class MCPManager:
    """Manage configured MCP servers and register tools into ToolRegistry."""

    def __init__(self) -> None:
        self.servers: Dict[str, MCPServerState] = {}
        self.sessions: Dict[str, MCPSession] = {}
        self.initialized: bool = False
        self.last_error: str = ""
        self._registered_names: List[str] = []
        self._lock = threading.RLock()

    @property
    def loaded_tool_count(self) -> int:
        return len(self._registered_names)

    def status(self) -> Dict[str, Any]:
        server_rows = []
        for name, st in self.servers.items():
            server_rows.append(
                {
                    "name": name,
                    "command": st.command,
                    "running": st.running,
                    "tool_count": len(st.tools),
                    "last_error": st.last_error,
                    "trust": st.trust,
                    "transport": st.transport,
                    "enabled": st.enabled,
                }
            )
        running_count = sum(1 for s in self.servers.values() if s.running)
        failed = [s for s in server_rows if s.get("last_error") and not s.get("running")]
        return {
            "configured_count": len(self.servers),
            "running_count": running_count,
            "initialized": self.initialized,
            "loaded_tool_count": self.loaded_tool_count,
            "last_error": self.last_error,
            "servers": server_rows,
            "failed_servers": failed,
            "client_present": True,
            "client_version": "7.6.0",
        }

    def initialize_servers(self, servers: Any) -> Dict[str, Any]:
        """Start enabled stdio servers, list tools, register into ToolRegistry."""
        with self._lock:
            self.shutdown()
            self.servers = {}
            self.sessions = {}
            self._registered_names = []
            self.last_error = ""

            if not isinstance(servers, dict) or not servers:
                self.initialized = False
                return self.status()

            for name, cfg in servers.items():
                if not isinstance(cfg, dict):
                    continue
                enabled = cfg.get("enabled", True)
                if enabled is False or str(enabled).lower() in {"0", "false", "no"}:
                    # Still record as configured-but-disabled for honesty
                    st = MCPServerState(
                        name=str(name),
                        command=str(cfg.get("command") or cfg.get("cmd") or "").strip(),
                        args=[str(a) for a in (cfg.get("args") or [])],
                        transport=str(cfg.get("transport") or "stdio").strip().lower() or "stdio",
                        trust=str(cfg.get("trust") or cfg.get("trust_state") or "configured").strip()
                        or "configured",
                        enabled=False,
                    )
                    st.last_error = "disabled in config"
                    self.servers[st.name] = st
                    continue
                st = MCPServerState(
                    name=str(name),
                    command=str(cfg.get("command") or cfg.get("cmd") or "").strip(),
                    args=[str(a) for a in (cfg.get("args") or [])],
                    env={str(k): str(v) for k, v in (cfg.get("env") or {}).items()}
                    if isinstance(cfg.get("env"), dict)
                    else {},
                    transport=str(cfg.get("transport") or "stdio").strip().lower() or "stdio",
                    trust=str(cfg.get("trust") or cfg.get("trust_state") or "configured").strip()
                    or "configured",
                    enabled=True,
                    timeout_s=float(cfg.get("timeout_s") or cfg.get("timeout") or 15),
                )
                self.servers[st.name] = st
                session = MCPSession(st)
                self.sessions[st.name] = session
                ok = session.start()
                if not ok:
                    logger.warning("MCP server '{}' failed to start: {}", st.name, st.last_error)
                    self.last_error = st.last_error or self.last_error
                    continue
                tools = session.list_tools()
                if not tools:
                    logger.warning(
                        "MCP server '{}' started but tools/list returned 0 tools ({})",
                        st.name,
                        st.last_error or "empty",
                    )
                    if not st.last_error:
                        st.last_error = "tools/list returned 0 tools"
                    continue
                for tdef in tools:
                    self._register_tool(st.name, tdef, session)

            self.initialized = bool(self.servers)
            return self.status()

    def _register_tool(self, server_name: str, tdef: dict, session: MCPSession) -> None:
        from agent.tool_registry import ToolEntry, ToolRegistry

        raw_name = str(tdef.get("name") or "").strip()
        if not raw_name:
            return
        safe_server = re_sub_safe(server_name)
        safe_tool = re_sub_safe(raw_name)
        reg_name = f"mcp__{safe_server}__{safe_tool}"
        description = str(tdef.get("description") or f"MCP tool {raw_name} from {server_name}")[:500]
        input_schema = tdef.get("inputSchema") if isinstance(tdef.get("inputSchema"), dict) else {}

        tool_obj = self._make_langchain_tool(reg_name, description, session, raw_name, input_schema)

        trust = (session.state.trust or "").lower()
        is_action = trust not in {"trusted", "owner", "builtin"}
        risk = "moderate" if is_action else "safe"

        ToolRegistry._entries[reg_name] = ToolEntry(
            name=reg_name,
            func=tool_obj,
            description=description,
            category="mcp",
            is_action=is_action,
            risk_level=risk,
            policy_flags=(),
            keyword_hints=(),
        )
        self._registered_names.append(reg_name)
        logger.info("Registered MCP tool {}", reg_name)

    def _make_langchain_tool(
        self,
        reg_name: str,
        description: str,
        session: MCPSession,
        raw_name: str,
        input_schema: dict,
    ) -> Any:
        """Build a LangChain-compatible tool when possible; else a minimal shim."""

        def _invoke(**kwargs: Any) -> str:
            if len(kwargs) == 1 and "kwargs" in kwargs and isinstance(kwargs["kwargs"], dict):
                kwargs = kwargs["kwargs"]
            # Drop nulls for cleaner MCP args
            args = {k: v for k, v in kwargs.items() if v is not None}
            return session.call_tool(raw_name, args)

        try:
            from langchain_core.tools import StructuredTool
            from pydantic import Field, create_model
            from typing import Any as TypingAny
            from typing import Optional

            props = (input_schema or {}).get("properties") or {}
            fields: Dict[str, Any] = {}
            for key, schema in props.items():
                if not isinstance(key, str) or not key:
                    continue
                fields[key] = (Optional[TypingAny], Field(default=None))  # type: ignore[valid-type]
            if not fields:
                fields["text"] = (Optional[str], Field(default=None))
                fields["input"] = (Optional[str], Field(default=None))
                fields["query"] = (Optional[str], Field(default=None))

            # Model name must be a valid Python identifier
            model_name = f"MCPArgs_{re_sub_safe(reg_name)}"[:80]
            ArgsModel = create_model(model_name, **fields)  # type: ignore[call-overload]

            return StructuredTool.from_function(
                func=_invoke,
                name=reg_name,
                description=description,
                args_schema=ArgsModel,
            )
        except Exception as exc:
            logger.debug("StructuredTool build failed for {}: {}; using shim", reg_name, exc)

            class _MCPTool:
                name = reg_name
                description = description  # noqa: A003

                def invoke(self, input=None, **kwargs):  # noqa: A002
                    if isinstance(input, dict):
                        return _invoke(**input)
                    if input is not None and not kwargs:
                        return _invoke(input=str(input))
                    return _invoke(**kwargs)

                def __call__(self, *args, **kwargs):
                    if args and isinstance(args[0], dict):
                        return _invoke(**args[0])
                    return _invoke(**kwargs)

            return _MCPTool()

    def shutdown(self) -> None:
        for _name, session in list(self.sessions.items()):
            try:
                session.stop()
            except Exception:
                pass
        try:
            from agent.tool_registry import ToolRegistry

            for n in list(self._registered_names):
                ToolRegistry._entries.pop(n, None)
        except Exception:
            pass
        self._registered_names = []
        self.sessions = {}
        # Keep server rows only if we are mid-reinit; full clear here
        self.servers = {}
        self.initialized = False

    def call(self, registered_name: str, arguments: Optional[dict] = None) -> str:
        """Call by registered mcp__server__tool name."""
        parts = registered_name.split("__", 2)
        if len(parts) < 3 or parts[0] != "mcp":
            return f"Invalid MCP tool name: {registered_name}"
        server_key = parts[1]
        tool_suffix = parts[2]
        session = self.sessions.get(server_key)
        if session is None:
            for sn, sess in self.sessions.items():
                if re_sub_safe(sn) == server_key:
                    session = sess
                    break
        if session is None:
            return f"MCP server not running: {server_key}"
        raw_name = tool_suffix
        for t in session.state.tools:
            if re_sub_safe(str(t.get("name") or "")) == tool_suffix:
                raw_name = str(t.get("name"))
                break
        return session.call_tool(raw_name, arguments or {})


_MANAGER: Optional[MCPManager] = None
_MANAGER_LOCK = threading.Lock()


def get_mcp_manager() -> MCPManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = MCPManager()
        return _MANAGER


def reset_mcp_manager() -> None:
    """Test helper: tear down singleton and clear registered MCP tools."""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is not None:
            try:
                _MANAGER.shutdown()
            except Exception:
                pass
        _MANAGER = None


def is_mcp_client_present() -> bool:
    """True when this real client module is loaded (not the old stub semantics)."""
    return True
