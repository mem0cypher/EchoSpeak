"""v7.5.0 — Terminal execution mode + Docker sandbox skeleton."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.sandbox import (
    STATUS_DENIED,
    STATUS_SANDBOX_UNAVAILABLE,
    build_mount_plan,
    get_sandbox_status,
    map_cwd_to_container,
    normalize_execution_mode,
    run_sandboxed_terminal,
)


def test_normalize_execution_mode():
    assert normalize_execution_mode("host") == "host"
    assert normalize_execution_mode("HOST") == "host"
    assert normalize_execution_mode("docker") == "docker"
    assert normalize_execution_mode("sandbox") == "docker"
    assert normalize_execution_mode("container") == "docker"
    assert normalize_execution_mode("") == "host"
    assert normalize_execution_mode(None) == "host"


def test_mount_plan_only_file_tool_roots(monkeypatch, tmp_path):
    from config import config

    root = tmp_path / "proj"
    extra = tmp_path / "extra"
    root.mkdir()
    extra.mkdir()
    monkeypatch.setattr(config, "file_tool_root", str(root), raising=False)
    monkeypatch.setattr(config, "file_tool_extra_roots", [str(extra)], raising=False)

    plan = build_mount_plan()
    hosts = {p["host"] for p in plan}
    assert str(root.resolve()) in hosts
    assert str(extra.resolve()) in hosts
    # Never docker.sock / home secrets
    joined = " ".join(hosts).lower().replace("\\", "/")
    assert "docker.sock" not in joined
    assert "/.ssh" not in joined


def test_map_cwd_to_container(tmp_path):
    root = (tmp_path / "proj").resolve()
    root.mkdir()
    sub = root / "src"
    sub.mkdir()
    mounts = [{"host": str(root), "container": "/sandbox/root0", "mode": "rw"}]
    assert map_cwd_to_container(root, mounts) == "/sandbox/root0"
    assert map_cwd_to_container(sub, mounts) == "/sandbox/root0/src"
    outside = (tmp_path / "other").resolve()
    outside.mkdir()
    assert map_cwd_to_container(outside, mounts) is None


def test_sandbox_unavailable_when_docker_missing(monkeypatch, tmp_path):
    from config import config

    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(config, "file_tool_root", str(root), raising=False)
    monkeypatch.setattr(config, "file_tool_extra_roots", [], raising=False)
    monkeypatch.setattr("agent.sandbox._docker_bin", lambda: None)

    result = run_sandboxed_terminal("echo hi", cwd=root, timeout_s=5)
    assert result.status == STATUS_SANDBOX_UNAVAILABLE
    assert result.exit_code == 127
    assert "sandbox_unavailable" in result.reason.lower()
    text = result.format()
    assert "Status=sandbox_unavailable" in text
    # Must not look like a successful host run
    assert "Mode=docker" in text


def test_sandbox_denied_outside_mount(monkeypatch, tmp_path):
    from config import config

    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(config, "file_tool_root", str(root), raising=False)
    monkeypatch.setattr(config, "file_tool_extra_roots", [], raising=False)
    monkeypatch.setattr("agent.sandbox._docker_bin", lambda: "/usr/bin/docker")
    monkeypatch.setattr("agent.sandbox.probe_docker", lambda timeout_s=4.0: (True, "ok"))

    result = run_sandboxed_terminal("echo hi", cwd=outside, timeout_s=5)
    assert result.status == STATUS_DENIED
    low = result.reason.lower()
    assert "not inside sandbox" in low or "escapes sandbox" in low or "cwd rejected" in low


def test_sandbox_rechecks_denylist(monkeypatch, tmp_path):
    from config import config

    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(config, "file_tool_root", str(root), raising=False)
    monkeypatch.setattr(config, "file_tool_extra_roots", [], raising=False)
    monkeypatch.setattr("agent.sandbox._docker_bin", lambda: "/usr/bin/docker")
    monkeypatch.setattr("agent.sandbox.probe_docker", lambda timeout_s=4.0: (True, "ok"))

    result = run_sandboxed_terminal(
        "rm -rf /",
        cwd=root,
        timeout_s=5,
        denylist_check=lambda c: "Command blocked by terminal denylist: rm",
    )
    assert result.status == STATUS_DENIED
    assert "denylist" in result.reason.lower()


def test_host_mode_status_ready_without_docker(monkeypatch):
    from config import config

    monkeypatch.setattr(config, "terminal_execution_mode", "host", raising=False)
    st = get_sandbox_status()
    assert st.mode == "host"
    assert st.ready is True
    assert st.docker_available is False


def test_docker_mode_status_not_ready_without_cli(monkeypatch):
    from config import config

    monkeypatch.setattr(config, "terminal_execution_mode", "docker", raising=False)
    monkeypatch.setattr("agent.sandbox._docker_bin", lambda: None)
    st = get_sandbox_status()
    assert st.mode == "docker"
    assert st.ready is False
    assert "not found" in st.message.lower() or "unavailable" in st.message.lower()


def test_terminal_run_docker_mode_no_host_fallback(monkeypatch, tmp_path):
    """When mode=docker and sandbox fails, must NOT execute on host."""
    from config import config
    from agent import tools as tools_mod

    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(config, "enable_system_actions", True, raising=False)
    monkeypatch.setattr(config, "allow_terminal_commands", True, raising=False)
    monkeypatch.setattr(config, "terminal_execution_mode", "docker", raising=False)
    monkeypatch.setattr(config, "file_tool_root", str(root), raising=False)
    monkeypatch.setattr(config, "file_tool_extra_roots", [], raising=False)
    monkeypatch.setattr(config, "terminal_command_denylist", ["rm"], raising=False)

    # Force sandbox path to report unavailable
    from agent.sandbox import TerminalRunResult, STATUS_SANDBOX_UNAVAILABLE

    monkeypatch.setattr(
        "agent.sandbox.run_sandboxed_terminal",
        lambda *a, **k: TerminalRunResult(
            status=STATUS_SANDBOX_UNAVAILABLE,
            exit_code=127,
            mode="docker",
            reason="sandbox_unavailable: test",
        ),
    )
    # Simple command (no shell redirection — host safety rejects chaining operators first)
    token = tools_mod.bind_tool_execution_context({
        "thread_id": "sandbox-test",
        "project_root": str(root),
        "workspace_root": str(root),
        "allowed_tool_names": ["terminal_run"],
    })
    try:
        out = tools_mod.terminal_run.invoke({"command": "echo should-not-run-on-host", "cwd": str(root)})
    finally:
        tools_mod.reset_tool_execution_context(token)
    assert "sandbox_unavailable" in out.lower()
    assert "Status=sandbox_unavailable" in out or "status=sandbox_unavailable" in out.lower()
    assert "Mode=docker" in out


def test_coding_readiness_includes_sandbox(monkeypatch):
    import asyncio
    from api import server as server_mod
    from config import config, ModelProvider

    class FakeAgent:
        tools = []
        _tool_allowlist_override = None
        llm_provider = ModelProvider.OPENAI

        def _is_action_tool(self, name):
            return name in {"file_write", "terminal_run"}

        def _action_allowed(self, name):
            return True

        def get_doctor_report(self):
            return {"workspace": {"id": "coding"}}

    monkeypatch.setattr(server_mod, "get_agent", lambda thread_id=None: FakeAgent(), raising=True)
    monkeypatch.setattr(server_mod, "_apply_thread_scope", lambda agent, thread_id=None: None, raising=True)
    monkeypatch.setattr(
        server_mod,
        "_check_provider_readiness",
        lambda provider=None: {"ok": True, "provider": "openai", "message": "", "detail": ""},
        raising=True,
    )
    monkeypatch.setattr(config, "terminal_execution_mode", "host", raising=False)
    monkeypatch.setattr(config, "allow_terminal_commands", True, raising=False)
    monkeypatch.setattr(config, "file_tool_root", "C:/tmp", raising=False)

    resp = asyncio.run(server_mod.coding_readiness(thread_id=None))
    payload = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    assert "sandbox" in payload
    assert payload["sandbox"].get("mode") == "host"
    assert payload["sandbox"].get("ready") is True
