#!/usr/bin/env python3
"""Official-SDK stdio MCP fixture for EchoSpeak connection tests."""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP


server = FastMCP("echospeak-mcp-fixture")


@server.tool(structured_output=True)
def echo(text: str) -> dict[str, str]:
    """Echo back the provided text."""
    return {"text": f"echo:{text}"}


@server.tool(structured_output=True)
def add(a: float, b: float) -> dict[str, float | int]:
    """Add two numbers."""
    total = float(a) + float(b)
    return {"value": int(total) if total.is_integer() else total}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--http-port", type=int, default=0)
    args = parser.parse_args()
    if args.http_port:
        server.settings.host = "127.0.0.1"
        server.settings.port = args.http_port
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")
