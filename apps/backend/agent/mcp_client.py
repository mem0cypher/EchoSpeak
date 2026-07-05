"""Minimal MCP manager bridge.

EchoSpeak's Trust Center and core agent expect an MCP manager module to exist.
This bridge records configured servers and leaves actual transport/client
binding to a future MCP implementation, without pretending unloaded tools work.
"""

from __future__ import annotations

from typing import Any, Dict


class MCPManager:
    def __init__(self) -> None:
        self.servers: Dict[str, Any] = {}
        self.initialized: bool = False
        self.last_error: str = ""

    def initialize_servers(self, servers: Any) -> Dict[str, Any]:
        if not isinstance(servers, dict):
            self.servers = {}
            self.initialized = False
            return self.status()
        self.servers = dict(servers)
        self.initialized = bool(self.servers)
        return self.status()

    def status(self) -> Dict[str, Any]:
        return {
            "configured_count": len(self.servers),
            "initialized": self.initialized,
            "loaded_tool_count": 0,
            "last_error": self.last_error,
        }
