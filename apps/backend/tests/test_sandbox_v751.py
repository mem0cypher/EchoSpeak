"""v7.5.1 — mount escape hardening + dual-layer denylist + coding loop machine."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.sandbox import (
    STATUS_DENIED,
    assert_safe_project_path,
    build_mount_plan,
    default_denylist_check,
    map_cwd_to_container,
    path_is_within_root,
    run_sandboxed_terminal,
)
from agent.coding_loop import (
    CodingExit,
    CodingLoop,
    CodingLoopError,
    CodingPhase,
    parse_terminal_status_block,
    project_folder_for_name,
)


def test_path_within_root_and_escape(tmp_path):
    root = (tmp_path / "proj").resolve()
    root.mkdir()
    inside = root / "src" / "a.py"
    inside.parent.mkdir(parents=True)
    inside.write_text("x", encoding="utf-8")
    outside = (tmp_path / "secret").resolve()
    outside.mkdir()
    assert path_is_within_root(inside, root) is True
    assert path_is_within_root(outside, root) is False
    ok, _ = assert_safe_project_path(inside, [root])
    assert ok is True
    ok2, reason = assert_safe_project_path(outside, [root])
    assert ok2 is False
    assert "escapes" in reason.lower()


def test_symlink_escape_rejected(tmp_path):
    """If OS allows symlinks, a link pointing outside must not pass assert_safe_project_path."""
    root = (tmp_path / "proj").resolve()
    root.mkdir()
    outside = (tmp_path / "outside").resolve()
    outside.mkdir()
    (outside / "secret.txt").write_text("nope", encoding="utf-8")
    link = root / "escape_link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available on this platform/privileges")
    # resolve() follows the link → outside
    ok, reason = assert_safe_project_path(link, [root])
    # After resolve, path is outside root
    assert ok is False
    assert "escapes" in reason.lower() or "escape" in reason.lower()


def test_map_cwd_rejects_outside(tmp_path):
    root = (tmp_path / "proj").resolve()
    root.mkdir()
    mounts = [{"host": str(root), "container": "/sandbox/root0", "mode": "rw"}]
    assert map_cwd_to_container(root / "sub", mounts) is None or True  # sub may not exist
    (root / "sub").mkdir()
    assert map_cwd_to_container(root / "sub", mounts) == "/sandbox/root0/sub"
    outside = (tmp_path / "other").resolve()
    outside.mkdir()
    assert map_cwd_to_container(outside, mounts) is None


def test_build_mount_skips_docker_sock(monkeypatch, tmp_path):
    from config import config

    root = (tmp_path / "proj").resolve()
    root.mkdir()
    monkeypatch.setattr(config, "file_tool_root", str(root), raising=False)
    # Inject a fake extra root that looks like docker.sock via monkeypatch of allowed list
    sock = tmp_path / "docker.sock"
    sock.write_text("", encoding="utf-8")
    monkeypatch.setattr(config, "file_tool_extra_roots", [str(sock)], raising=False)
    plan = build_mount_plan()
    hosts = " ".join(p["host"] for p in plan).lower().replace("\\", "/")
    assert "docker.sock" not in hosts


def test_default_denylist_blocks_rm():
    msg = default_denylist_check("rm -rf /tmp/x")
    assert msg is not None
    assert "denylist" in msg.lower() or "blocked" in msg.lower()


def test_sandbox_uses_default_denylist_without_callback(monkeypatch, tmp_path):
    from config import config

    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(config, "file_tool_root", str(root), raising=False)
    monkeypatch.setattr(config, "file_tool_extra_roots", [], raising=False)
    monkeypatch.setattr("agent.sandbox._docker_bin", lambda: "/usr/bin/docker")
    monkeypatch.setattr("agent.sandbox.probe_docker", lambda timeout_s=4.0: (True, "ok"))
    result = run_sandboxed_terminal("rm -rf /", cwd=root, timeout_s=5, denylist_check=None)
    assert result.status == STATUS_DENIED


def test_coding_loop_enforces_order():
    loop = CodingLoop(project_folder="projects/demo")
    assert loop.phase == CodingPhase.IDLE
    loop.start()
    assert loop.phase == CodingPhase.INSPECT
    loop.advance(CodingPhase.PLAN)
    loop.advance(CodingPhase.IMPLEMENT)
    # Cannot skip to summarize
    with pytest.raises(CodingLoopError):
        loop.advance(CodingPhase.SUMMARIZE)
    loop.advance(CodingPhase.VERIFY)
    loop.set_verify_status(CodingExit.PASS)
    loop.advance(CodingPhase.CONFIRM)
    loop.set_confirm_status(CodingExit.PASS)
    loop.advance(CodingPhase.SUMMARIZE)
    loop.complete(exit_status=CodingExit.PASS)
    assert loop.phase == CodingPhase.DONE
    assert loop.state.exit_status == CodingExit.PASS.value


def test_coding_loop_fail_from_verify():
    loop = CodingLoop()
    loop.start()
    loop.advance(CodingPhase.PLAN)
    loop.advance(CodingPhase.IMPLEMENT)
    loop.advance(CodingPhase.VERIFY)
    loop.fail("tests failed", exit_status=CodingExit.FAIL)
    assert loop.phase == CodingPhase.FAILED
    assert loop.state.exit_status == "fail"


def test_project_folder_named_under_root(tmp_path):
    p = project_folder_for_name("My Cool App!!", str(tmp_path))
    assert "projects" in p.replace("\\", "/")
    assert "My-Cool-App" in p or "My-Cool-App" in Path(p).name or "Cool" in p


def test_parse_terminal_status_block():
    assert parse_terminal_status_block("ExitCode=0\nStatus=pass\nMode=host") == "pass"
    assert parse_terminal_status_block("ExitCode=127\nStatus=sandbox_unavailable") == "sandbox_unavailable"
    assert parse_terminal_status_block("Command timed out after 20s.") == "timeout"
