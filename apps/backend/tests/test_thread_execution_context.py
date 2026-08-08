from pathlib import Path
from types import SimpleNamespace

import agent.state as state_module
import agent.checkpoints as checkpoint_module
from agent.core import ConversationMemory, EchoSpeakAgent
from agent.mode_controller import ModeDecision, TurnMode
from agent.memory import AgentMemory
from agent.state import StateStore, ThreadSessionState
from agent.tools import (
    bind_tool_execution_context,
    file_list,
    file_read,
    get_active_project_root,
    reset_tool_execution_context,
)


def test_file_scope_is_isolated_and_restored_between_thread_contexts(tmp_path):
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    (project_a / "a.txt").write_text("thread-a", encoding="utf-8")
    (project_b / "b.txt").write_text("thread-b", encoding="utf-8")

    token_a = bind_tool_execution_context({
        "thread_id": "thread-a",
        "project_root": str(project_a),
        "workspace_root": str(project_a),
        "allowed_tool_names": ["file_read"],
    })
    try:
        assert "thread-a" in file_read.invoke({"path": "a.txt"})
        assert "Path not allowed" in file_read.invoke({"path": str(project_b / "b.txt")})
        assert "not allowed by thread context 'thread-a'" in file_list.invoke({"path": "."})

        token_b = bind_tool_execution_context({
            "thread_id": "thread-b",
            "project_root": str(project_b),
            "workspace_root": str(project_b),
            "allowed_tool_names": ["file_read"],
        })
        try:
            assert "thread-b" in file_read.invoke({"path": "b.txt"})
            assert get_active_project_root() == project_b.resolve()
        finally:
            reset_tool_execution_context(token_b)

        assert get_active_project_root() == project_a.resolve()
    finally:
        reset_tool_execution_context(token_a)


def test_state_store_keeps_execution_context_and_ledger_thread_isolated(tmp_path, monkeypatch):
    phase_dir = tmp_path / "phase3"
    monkeypatch.setattr(state_module, "PHASE3_DIR", phase_dir)
    monkeypatch.setattr(state_module, "APPROVALS_PATH", phase_dir / "approvals.json")
    monkeypatch.setattr(state_module, "EXECUTIONS_PATH", phase_dir / "executions.json")
    monkeypatch.setattr(state_module, "THREAD_STATE_PATH", phase_dir / "thread_state.json")
    monkeypatch.setattr(state_module, "TRACE_DIR", phase_dir / "traces")
    store = StateStore()

    store.update_thread_state(
        "thread-a",
        project_path=str(tmp_path / "a"),
        objective="fix routing",
        allowed_tool_names=["file_read"],
    )
    store.add_ledger_entry(
        "thread-a",
        project_path=str(tmp_path / "a"),
        objective="fix routing",
        category="inspection",
        summary="Inspected routing.py",
        tool="file_read",
        success=True,
    )
    approval = store.create_approval(
        thread_id="thread-a",
        tool="file_write",
        execution_context={"thread_id": "thread-a", "project_path": str(tmp_path / "a")},
    )

    state_a = store.get_thread_state("thread-a")
    state_b = store.get_thread_state("thread-b")
    assert state_a.objective == "fix routing"
    assert state_a.ledger[0].thread_id == "thread-a"
    assert state_a.pending_approval_id == approval.id
    assert store.get_pending_approval("thread-b") is None
    assert store.get_approval(approval.id).execution_context["thread_id"] == "thread-a"
    assert state_b.objective == ""
    assert state_b.ledger == []


def test_memory_selection_allows_stable_facts_but_filters_other_projects(monkeypatch):
    import threading

    memory = AgentMemory.__new__(AgentMemory)
    memory._records_lock = threading.RLock()
    memory._load_records = lambda: None
    memory.use_faiss = False
    memory.simple_memory = [
        {"text": "Stable preference", "mode": "general", "thread_id": "t1", "metadata": {"type": "preference", "project_path": ""}},
        {"text": "Project A fact", "mode": "general", "thread_id": "t1", "metadata": {"type": "project", "project_path": "C:/a"}},
        {"text": "Project B fact", "mode": "general", "thread_id": "t1", "metadata": {"type": "project", "project_path": "C:/b"}},
    ]

    context = memory.get_conversation_context("routing", thread_id="t1", project_path="C:/a")

    assert "Stable preference" in context
    assert "Project A fact" in context
    assert "Project B fact" not in context


def test_pending_approval_snapshot_rejects_changed_project_scope():
    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent._execution_context = ThreadSessionState(
        thread_id="t1",
        workspace_root="C:/projects/b",
        project_path="C:/projects/b",
    )
    pending = {
        "execution_context": {
            "thread_id": "t1",
            "workspace_root": "C:/projects/a",
            "project_path": "C:/projects/a",
        }
    }

    assert agent._pending_action_matches_execution_context(pending) is False


def test_capability_registry_uses_installed_inventory_without_granting_authority():
    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent.tools = [SimpleNamespace(name="web_search"), SimpleNamespace(name="file_read")]
    agent.lc_tools = list(agent.tools)

    registry = agent._capability_registry()

    assert registry["research"]["status"] == "tool_supported"
    assert registry["filesystem_read"]["status"] == "unsupported"  # file_list is missing
    assert registry["filesystem_write"]["status"] == "unsupported"


def test_checkpoints_are_selected_by_thread_and_project(tmp_path, monkeypatch):
    checkpoint_dir = tmp_path / "checkpoints"
    monkeypatch.setattr(checkpoint_module, "CHECKPOINTS_DIR", checkpoint_dir)
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    file_a = project_a / "state.txt"
    file_b = project_b / "state.txt"
    file_a.write_text("a-before", encoding="utf-8")
    file_b.write_text("b-before", encoding="utf-8")

    token_a = bind_tool_execution_context({
        "thread_id": "thread-a",
        "project_root": str(project_a),
        "allowed_tool_names": ["file_write"],
    })
    try:
        checkpoint_module.create_checkpoint(str(file_a))
    finally:
        reset_tool_execution_context(token_a)
    token_b = bind_tool_execution_context({
        "thread_id": "thread-b",
        "project_root": str(project_b),
        "allowed_tool_names": ["file_write"],
    })
    try:
        checkpoint_module.create_checkpoint(str(file_b))
    finally:
        reset_tool_execution_context(token_b)

    file_a.write_text("a-after", encoding="utf-8")
    file_b.write_text("b-after", encoding="utf-8")
    result = checkpoint_module.undo_last_change("thread-a", str(project_a))

    assert result.startswith("Successfully reverted")
    assert file_a.read_text(encoding="utf-8") == "a-before"
    assert file_b.read_text(encoding="utf-8") == "b-after"


def test_ledger_records_provenance_without_persisting_write_body_or_secret(tmp_path, monkeypatch):
    phase_dir = tmp_path / "phase3-ledger"
    monkeypatch.setattr(state_module, "PHASE3_DIR", phase_dir)
    monkeypatch.setattr(state_module, "APPROVALS_PATH", phase_dir / "approvals.json")
    monkeypatch.setattr(state_module, "EXECUTIONS_PATH", phase_dir / "executions.json")
    monkeypatch.setattr(state_module, "THREAD_STATE_PATH", phase_dir / "thread_state.json")
    monkeypatch.setattr(state_module, "TRACE_DIR", phase_dir / "traces")
    store = StateStore()
    context = store.update_thread_state(
        "t-ledger",
        project_path=str(tmp_path / "project"),
        objective="update config",
    )
    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent._state_store = store
    agent._current_thread_id = "t-ledger"
    agent._current_execution_id = "exec-1"
    agent._execution_context = context
    agent._current_mode_profile = SimpleNamespace(executor_name="coding_implement_executor")

    agent._record_tool_execution_outcome(
        tool_name="file_write",
        tool_input="{'path': 'config.py', 'content': 'password=super-secret'}",
        output=(
            "Wrote 32 chars to config.py\n"
            "<<<ECHO_FILE action=write path=config.py chars=32>>>\n"
            "password=super-secret\n<<<END_ECHO_FILE>>>"
        ),
        success=True,
    )

    persisted = store.get_thread_state("t-ledger")
    payload = persisted.ledger[-1].model_dump_json()
    assert "super-secret" not in payload
    assert "write arguments omitted" in payload
    assert persisted.completed_actions[-1]["success"] is True


def test_single_agent_runtime_buffers_are_selected_per_thread():
    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent._thread_conversation_memories = {}
    agent._thread_summaries = {"a": "summary-a", "b": "summary-b"}
    agent._state_store = SimpleNamespace(
        get_thread_state=lambda thread_id: ThreadSessionState(
            thread_id=thread_id,
            current_subject=f"subject-{thread_id}",
            project_path=f"C:/projects/{thread_id}",
        )
    )
    agent.conversation_memory = ConversationMemory()

    agent.select_thread_runtime("a")
    agent.conversation_memory.save_context({"input": "from-a"}, {"output": "answer-a"})
    agent.select_thread_runtime("b")
    agent.conversation_memory.save_context({"input": "from-b"}, {"output": "answer-b"})
    agent.select_thread_runtime("a")

    history = " ".join(item["content"] for item in agent.conversation_memory.messages)
    assert "from-a" in history
    assert "from-b" not in history
    assert agent._summary == "summary-a"
    assert agent._current_subject_text == "subject-a"


def test_local_first_constraint_blocks_web_until_local_inspection():
    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent._current_mode_decision = None
    agent._active_approved_action = None
    agent._active_retry_action = None
    agent._current_source = "web"
    agent._workspace_id = "coding"
    agent._tool_allowlist_override = None
    agent._execution_context = ThreadSessionState(
        thread_id="local-first",
        allowed_tool_names=["file_list", "file_read", "web_search"],
        constraints=["local_first"],
    )

    assert agent._tool_allowed("web_search") is False
    agent._execution_context = agent._execution_context.model_copy(
        update={"operation_details": {"tools_used": ["file_read"]}}
    )
    assert agent._tool_allowed("web_search") is True


def test_state_store_round_trips_unicode_without_mojibake(tmp_path, monkeypatch):
    phase_dir = tmp_path / "phase3-unicode"
    monkeypatch.setattr(state_module, "PHASE3_DIR", phase_dir)
    monkeypatch.setattr(state_module, "APPROVALS_PATH", phase_dir / "approvals.json")
    monkeypatch.setattr(state_module, "EXECUTIONS_PATH", phase_dir / "executions.json")
    monkeypatch.setattr(state_module, "THREAD_STATE_PATH", phase_dir / "thread_state.json")
    monkeypatch.setattr(state_module, "TRACE_DIR", phase_dir / "traces")
    text = "Inspect → propose — verify… 📁 café"

    store = StateStore()
    store.update_thread_state("unicode", objective=text, current_subject=text)
    reloaded = StateStore().get_thread_state("unicode")

    assert reloaded.objective == text
    assert reloaded.current_subject == text
    assert "â" not in (phase_dir / "thread_state.json").read_text(encoding="utf-8")


def test_internal_policy_block_is_distinct_from_windows_elevation_error():
    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent._current_execution_id = "exec"
    policy = agent._normalize_tool_outcome(
        tool_name="file_write",
        output="System actions are disabled by EchoSpeak configuration",
    )
    elevated = OSError("The requested operation requires elevation")
    elevated.winerror = 740
    os_error = agent._normalize_tool_outcome(tool_name="terminal_run", error=elevated)

    assert policy.policy_block is True
    assert policy.error_code == "configuration_or_scope_block"
    assert os_error.policy_block is False
    assert os_error.error_code == "os_elevation_required"
