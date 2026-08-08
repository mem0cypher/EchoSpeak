"""v7.6.0 — real MCP stdio client: start / list / call + Trust Center honesty."""

from __future__ import annotations

import os
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.mcp_client import (
    get_mcp_manager,
    is_mcp_client_present,
    re_sub_safe,
    reset_mcp_manager,
)
from agent.tool_registry import ToolRegistry

# Import server helper after path setup
import api.server as server_mod  # noqa: E402

FIXTURE_SERVER = Path(__file__).resolve().parent / "fixtures" / "mock_mcp_server.py"
PY = sys.executable


@pytest.fixture(autouse=True)
def _clean_mcp():
    """Isolate each test from the singleton + registry MCP entries."""
    reset_mcp_manager()
    # Drop any leftover mcp__ entries from prior tests/agent loads
    for name in list(ToolRegistry._entries.keys()):
        if name.startswith("mcp__"):
            ToolRegistry._entries.pop(name, None)
    yield
    reset_mcp_manager()
    for name in list(ToolRegistry._entries.keys()):
        if name.startswith("mcp__"):
            ToolRegistry._entries.pop(name, None)


def _mock_server_cfg(name: str = "mock", trust: str = "trusted", **extra):
    cfg = {
        "command": PY,
        "args": [str(FIXTURE_SERVER)],
        "transport": "stdio",
        "capability_policies": (
            {"echo": "read", "add": "read"} if trust == "trusted" else {}
        ),
        "enabled": True,
        "timeout_s": 10,
    }
    cfg.update(extra)
    return {name: cfg}


def test_mcp_client_present():
    assert is_mcp_client_present() is True


def test_mock_server_list_and_call():
    mgr = get_mcp_manager()
    status = mgr.initialize_servers(_mock_server_cfg())
    assert status["client_present"] is True
    assert status["configured_count"] == 1
    assert status["loaded_tool_count"] == 2
    assert status["running_count"] == 1
    names = sorted(mgr._registered_names)
    assert "mcp__mock__echo" in names
    assert "mcp__mock__add" in names

    # Registry entries
    entry = ToolRegistry.get("mcp__mock__echo")
    assert entry is not None
    assert entry.category == "mcp"
    assert entry.is_action is False  # trust=trusted

    # Direct call path
    out = json.loads(mgr.call("mcp__mock__echo", {"text": "hello"}))
    assert out["structuredContent"]["text"] == "echo:hello"
    out2 = json.loads(mgr.call("mcp__mock__add", {"a": 2, "b": 3}))
    assert out2["structuredContent"]["value"] == 5

    # Tool invoke path
    tool = entry.func
    if hasattr(tool, "invoke"):
        result = tool.invoke({"text": "via-invoke"})
    else:
        result = tool(text="via-invoke")
    assert "echo:via-invoke" in str(result)


def test_untrusted_server_marks_tools_as_action():
    mgr = get_mcp_manager()
    mgr.initialize_servers(_mock_server_cfg(trust="configured"))
    entry = ToolRegistry.get("mcp__mock__echo")
    assert entry is not None
    assert entry.is_action is True
    assert entry.risk_level == "moderate"


def test_bad_command_fails_loud_not_available():
    mgr = get_mcp_manager()
    status = mgr.initialize_servers(
        {
            "broken": {
                "command": "this-command-definitely-does-not-exist-xyzzy-echo",
                "args": [],
                "transport": "stdio",
                "trust": "trusted",
                "enabled": True,
                "timeout_s": 5,
            }
        }
    )
    assert status["configured_count"] == 1
    assert status["loaded_tool_count"] == 0
    assert status["running_count"] == 0
    servers = status["servers"]
    assert len(servers) == 1
    assert servers[0]["running"] is False
    assert servers[0]["last_error"]  # loud failure reason
    assert "Failed to start" in servers[0]["last_error"] or servers[0]["last_error"]


def test_configured_not_same_as_available_trust_summary():
    """Trust Center: configured servers without loaded tools ≠ available."""
    summary = server_mod._mcp_trust_summary(
        {"filesystem": {"transport": "stdio", "trust": "trusted"}},
        mcp_client_present=True,
        mcp_tool_count=0,
    )
    assert summary["mcp_configured_count"] == 1
    assert summary["mcp_client_present"] is True
    assert summary["mcp_available"] is False
    assert summary["mcp_available_tool_count"] == 0
    assert summary["mcp_status"] == "configured_no_tools"
    assert summary["warnings"]


def test_available_when_tools_loaded():
    summary = server_mod._mcp_trust_summary(
        {"mock": {"transport": "stdio"}},
        mcp_client_present=True,
        mcp_tool_count=2,
    )
    assert summary["mcp_available"] is True
    assert summary["mcp_status"] == "available"
    assert summary["mcp_available_tool_count"] == 2
    assert summary["warnings"] == []


def test_disabled_server_recorded_not_started():
    mgr = get_mcp_manager()
    status = mgr.initialize_servers(
        {
            "off": {
                "command": PY,
                "args": [str(FIXTURE_SERVER)],
                "enabled": False,
                "trust": "trusted",
            }
        }
    )
    assert status["configured_count"] == 1
    assert status["loaded_tool_count"] == 0
    assert status["running_count"] == 0
    assert status["servers"][0]["enabled"] is False


def test_unsupported_transport_fails_loud():
    mgr = get_mcp_manager()
    status = mgr.initialize_servers(
        {
            "httpish": {
                "command": PY,
                "args": [str(FIXTURE_SERVER)],
                "transport": "sse",
                "trust": "trusted",
            }
        }
    )
    assert status["loaded_tool_count"] == 0
    err = status["servers"][0]["last_error"]
    assert "url" in err.lower() or "http" in err.lower()


def test_re_sub_safe():
    assert re_sub_safe("my-server!") == "my_server"
    assert re_sub_safe("") == "unnamed"


def test_trust_summary_includes_manager_detail():
    """Optional manager_status enriches warnings without flipping available falsely."""
    mgr = get_mcp_manager()
    mgr.initialize_servers(
        {
            "broken": {
                "command": "nope-not-a-real-binary-echo-speak",
                "args": [],
                "transport": "stdio",
                "enabled": True,
            }
        }
    )
    st = mgr.status()
    summary = server_mod._mcp_trust_summary(
        {"broken": {"transport": "stdio"}},
        mcp_client_present=True,
        mcp_tool_count=st["loaded_tool_count"],
        manager_status=st,
    )
    assert summary["mcp_available"] is False
    assert any("failed" in w.lower() or "no MCP tools" in w for w in summary["warnings"])
