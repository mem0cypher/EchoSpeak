from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest


FIXTURE = Path(__file__).parent / "fixtures" / "coding_project"


class _FakeStateStore:
    def __init__(self, state, pending=None):
        self.state = state
        self.pending = pending

    def get_thread_state(self, _thread_id):
        return self.state

    def get_pending_approval(self, _thread_id):
        return self.pending


class _FakeAgent:
    def __init__(self, state, loaded):
        self._current_thread_id = state.thread_id
        self._state_store = _FakeStateStore(state)
        self._loaded = frozenset(loaded)
        self.llm_wrapper = object()
        self.provider_info = {"provider": "openai", "model": "gpt-test"}

    def project_scope_report(self, _thread_id=None):
        return {
            "project_attached": bool(self._state_store.state.active_project_id),
            "interaction_mode": self._state_store.state.mode,
            "permissions": {},
        }

    def _registered_tool_names(self):
        return self._loaded

    def _is_tool_role_blocked(self, _name):
        return False

    def _action_configured(self, name):
        from config import config

        if name == "file_write":
            return bool(config.enable_system_actions and config.allow_file_write)
        if name == "terminal_run":
            return bool(config.enable_system_actions and config.allow_terminal_commands)
        return True

    def _allow_llm_tool_calling(self):
        return True

    def _parse_action_json(self):  # pragma: no cover - capability marker
        return None

    def _infer_file_write_args(self):  # pragma: no cover - capability marker
        return None


@pytest.fixture()
def copied_project(tmp_path: Path) -> Path:
    target = tmp_path / "fixture-project"
    shutil.copytree(FIXTURE, target)
    (target / "binary.bin").write_bytes(b"\x00\x01\x02fixture")
    (target / "large.txt").write_text("x" * 210_000, encoding="utf-8")
    return target


def _state(project_id="", root="", *, mode="chat", allowed=None):
    return SimpleNamespace(
        thread_id="fixture-session",
        active_project_id=project_id,
        project_path=str(root),
        workspace_root=str(root),
        mode=mode,
        allowed_tool_names=list(allowed or []),
        constraints=[],
    )


def test_readiness_no_project_reports_exact_blocker(monkeypatch):
    from agent import coding_readiness as readiness_mod

    monkeypatch.setattr(readiness_mod, "get_project_manager", lambda: SimpleNamespace(get_project=lambda _id: None))
    monkeypatch.setattr(readiness_mod.ToolRegistry, "get", lambda name: SimpleNamespace(name=name))
    report = readiness_mod.build_coding_readiness(
        _FakeAgent(_state(), {"file_list", "file_read", "file_write", "terminal_run"}),
        "fixture-session",
        {"ok": True, "provider": "openai"},
    )
    assert report["ready_for_reading"] is False
    assert report["ready_for_editing"] is False
    assert report["blockers"][0]["code"] == "project_not_attached"


def test_readiness_editing_does_not_require_terminal(copied_project, monkeypatch):
    from agent import coding_readiness as readiness_mod
    from config import config

    project = SimpleNamespace(id="project-1", name="Fixture", workspace_root=str(copied_project))
    monkeypatch.setattr(readiness_mod, "get_project_manager", lambda: SimpleNamespace(get_project=lambda _id: project))
    monkeypatch.setattr(readiness_mod.ToolRegistry, "get", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_file_write", True)
    monkeypatch.setattr(config, "allow_terminal_commands", False)
    report = readiness_mod.build_coding_readiness(
        _FakeAgent(_state("project-1", copied_project), {"file_list", "file_read", "file_write", "terminal_run"}),
        "fixture-session",
        {"ok": True, "provider": "openai"},
    )
    assert report["ready_for_reading"] is True
    assert report["ready_for_editing"] is True
    assert report["tools"]["terminal_run"] == "disabled"
    assert report["terminal"]["required_for_file_editing"] is False


def test_readiness_distinguishes_registered_but_filtered(copied_project, monkeypatch):
    from agent import coding_readiness as readiness_mod
    from config import config

    project = SimpleNamespace(id="project-1", name="Fixture", workspace_root=str(copied_project))
    monkeypatch.setattr(readiness_mod, "get_project_manager", lambda: SimpleNamespace(get_project=lambda _id: project))
    monkeypatch.setattr(readiness_mod.ToolRegistry, "get", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_file_write", True)
    report = readiness_mod.build_coding_readiness(
        _FakeAgent(_state("project-1", copied_project), {"file_list", "file_read", "terminal_run"}),
        "fixture-session",
        {"ok": True, "provider": "openai"},
    )
    assert report["tools"]["file_write"] == "filtered"
    assert report["ready_for_editing"] is False


@pytest.mark.parametrize(
    ("tool_name", "raw", "error_code"),
    [
        ("file_write", "Mutation blocked: source changed", "mutation_precondition_failed"),
        ("file_move", "Destination already exists. Set overwrite=true to replace.", "tool_returned_error"),
        ("file_copy", "Source path not found.", "tool_returned_error"),
        ("terminal_run", "ExitCode=1\nStatus=fail\nMode=docker", "terminal_command_failed"),
        ("terminal_run", "ExitCode=127\nStatus=sandbox_unavailable\nMode=docker", "terminal_sandbox_unavailable"),
    ],
)
def test_raw_tool_failures_never_project_as_success(tool_name, raw, error_code):
    from agent.core import EchoSpeakAgent

    agent = object.__new__(EchoSpeakAgent)
    agent._current_execution_id = "turn-1"
    outcome = agent._normalize_tool_outcome(tool_name=tool_name, output=raw)
    assert outcome.success is False
    assert outcome.error_code == error_code


def test_file_read_reports_binary_truncation_and_invalid_utf8(copied_project, monkeypatch):
    from agent.tools import bind_tool_execution_context, file_read, reset_tool_execution_context

    token = bind_tool_execution_context({
        "thread_id": "fixture-session",
        "project_root": str(copied_project),
        "allowed_tool_names": ["file_read"],
    })
    try:
        assert "Binary file detected" in file_read.invoke({"path": "binary.bin"})
        large = file_read.invoke({"path": "large.txt", "max_chars": 1000})
        assert "truncated=1" in large
        (copied_project / "invalid.txt").write_bytes(b"\xff\xfebroken")
        assert "Unsupported text encoding" in file_read.invoke({"path": "invalid.txt"})
    finally:
        reset_tool_execution_context(token)


def test_project_status_never_runs_tests(copied_project, monkeypatch):
    import subprocess
    from agent.tools import bind_tool_execution_context, project_status, reset_tool_execution_context

    commands = []

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        return SimpleNamespace(returncode=0, stdout="## main\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    token = bind_tool_execution_context({
        "thread_id": "fixture-session",
        "project_root": str(copied_project),
        "allowed_tool_names": ["project_status"],
    })
    try:
        output = project_status.invoke({"workspace_path": "."})
    finally:
        reset_tool_execution_context(token)
    assert output
    assert commands and commands[0][:2] == ["git", "status"]
    assert not any("pytest" in " ".join(command) for command in commands)


def test_node_preview_never_spawns_host_package_script(copied_project, monkeypatch):
    from agent.code_workspace import PreviewManager, ProjectDetection

    monkeypatch.setattr("agent.code_workspace.subprocess.Popen", lambda *_a, **_k: pytest.fail("host process spawned"))
    result = PreviewManager().start(
        "fixture-session",
        copied_project,
        ProjectDetection(
            kind="node",
            label="Node",
            preview_available=True,
            preview_strategy="node_script",
            preview_command="npm run dev",
            run_command_hint="npm run dev",
        ),
    )
    assert result["ok"] is False
    assert result["status"] == "approval_required"


def test_echo_file_wrapper_round_trip_preserves_exact_whitespace():
    from agent.tools import _echo_file_payload, strip_echo_file_wrapper

    body = "\n  first line\nsecond line  \n"
    wrapped = _echo_file_payload("index.html", body, action="read")
    assert strip_echo_file_wrapper(wrapped) == body


def test_empty_file_read_remains_zero_bytes(copied_project):
    from agent.tools import bind_tool_execution_context, file_read, reset_tool_execution_context, strip_echo_file_wrapper

    (copied_project / "empty.txt").write_bytes(b"")
    token = bind_tool_execution_context({
        "thread_id": "fixture-session",
        "project_root": str(copied_project),
        "allowed_tool_names": ["file_read"],
    })
    try:
        output = file_read.invoke({"path": "empty.txt"})
    finally:
        reset_tool_execution_context(token)
    assert "Read 0 of 0 chars" in output
    assert strip_echo_file_wrapper(output) == ""


def test_copy_revalidates_source_after_staging(copied_project, monkeypatch):
    import shutil
    from agent.tools import (
        _mutation_path_version,
        bind_tool_execution_context,
        file_copy,
        reset_tool_execution_context,
    )
    from config import config

    source = copied_project / "source.txt"
    destination = copied_project / "destination.txt"
    source.write_text("approved", encoding="utf-8")
    precondition = {
        "version": 2,
        "entries": [
            _mutation_path_version(source, "src"),
            _mutation_path_version(destination, "dst"),
        ],
    }
    original_copy2 = shutil.copy2

    def racing_copy(src, dst, *args, **kwargs):
        result = original_copy2(src, dst, *args, **kwargs)
        source.write_text("changed during copy", encoding="utf-8")
        return result

    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_file_write", True)
    monkeypatch.setattr(shutil, "copy2", racing_copy)
    token = bind_tool_execution_context({
        "thread_id": "fixture-session",
        "project_root": str(copied_project),
        "allowed_tool_names": ["file_copy"],
        "mutation_precondition": precondition,
    })
    try:
        output = file_copy.invoke({"src": "source.txt", "dst": "destination.txt"})
    finally:
        reset_tool_execution_context(token)
    assert "Mutation blocked" in output
    assert not destination.exists()
