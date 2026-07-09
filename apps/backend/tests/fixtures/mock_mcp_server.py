#!/usr/bin/env python3
"""Minimal stdio MCP server for EchoSpeak v7.6 tests.

Protocol: JSON-RPC 2.0 with Content-Length framing (MCP 2024-11-05 subset).
Tools:
  - echo: returns "echo:<text>"
  - add: returns sum of a + b
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional


def _read_message() -> Optional[dict]:
    headers: Dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        try:
            text = line.decode("utf-8", errors="ignore").strip()
        except Exception:
            continue
        if ":" in text:
            k, v = text.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length") or "0")
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _write_message(payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    sys.stdout.buffer.write(header + body)
    sys.stdout.buffer.flush()


def _result(req_id: Any, result: Any) -> None:
    _write_message({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id: Any, code: int, message: str) -> None:
    _write_message(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    )


TOOLS = [
    {
        "name": "echo",
        "description": "Echo back the provided text",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "Add two numbers",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    },
]


def main() -> int:
    while True:
        msg = _read_message()
        if msg is None:
            break
        method = msg.get("method")
        req_id = msg.get("id")
        params = msg.get("params") or {}

        # Notifications have no id
        if req_id is None:
            if method in {"notifications/initialized", "initialized"}:
                continue
            continue

        if method == "initialize":
            _result(
                req_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "echospeak-mock-mcp", "version": "1.0.0"},
                },
            )
        elif method == "tools/list":
            _result(req_id, {"tools": TOOLS})
        elif method == "tools/call":
            name = str(params.get("name") or "")
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                args = {}
            if name == "echo":
                text = str(args.get("text") or args.get("input") or "")
                _result(
                    req_id,
                    {"content": [{"type": "text", "text": f"echo:{text}"}], "isError": False},
                )
            elif name == "add":
                try:
                    a = float(args.get("a", 0))
                    b = float(args.get("b", 0))
                    total = a + b
                    if total == int(total):
                        total = int(total)
                    _result(
                        req_id,
                        {
                            "content": [{"type": "text", "text": str(total)}],
                            "isError": False,
                        },
                    )
                except Exception as exc:
                    _result(
                        req_id,
                        {
                            "content": [{"type": "text", "text": f"bad args: {exc}"}],
                            "isError": True,
                        },
                    )
            else:
                _error(req_id, -32601, f"Unknown tool: {name}")
        elif method == "ping":
            _result(req_id, {})
        else:
            _error(req_id, -32601, f"Method not found: {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
