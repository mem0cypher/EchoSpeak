from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.model_runtime import get_model_adapter, repair_tool_call_once, resolve_model_profile
from agent.projects import ProjectManager
from agent.state import StateStore, ToolOutcome


def test_new_turn_isolates_current_activity_and_histories(tmp_path: Path):
    store = StateStore(tmp_path / "state")
    first = store.create_execution(thread_id="session-a", query="research Pokemon", model_id="small")
    store.add_item(turn_id=first.id, session_id="session-a", item_type="research_source", status="complete", payload={"subject": "Pokemon"})
    store.update_execution(first.id, status="completed", success=True)
    second = store.create_execution(thread_id="session-a", query="goodnight", model_id="small")

    projection = store.runtime_projection("session-a")
    assert projection["current_turn"]["id"] == second.id
    assert [item["item_type"] for item in projection["current_turn"]["items"]] == ["user_message"]
    assert projection["historical_turns"][0]["id"] == first.id


def test_new_turn_supersedes_unfinished_turn(tmp_path: Path):
    store = StateStore(tmp_path / "state")
    first = store.create_execution(thread_id="s", query="first")
    store.create_execution(thread_id="s", query="second")
    assert store.get_execution(first.id).terminal_status == "superseded"


def test_tool_runs_never_overwrite_same_tool(tmp_path: Path):
    store = StateStore(tmp_path / "state")
    turn = store.create_execution(thread_id="s", query="inspect")
    one = store.create_tool_run(turn_id=turn.id, session_id="s", tool_name="file_read", canonical_arguments={"path": "a"})
    two = store.create_tool_run(turn_id=turn.id, session_id="s", tool_name="file_read", canonical_arguments={"path": "b"})
    store.finish_tool_run(one.id, ToolOutcome(tool_name="file_read", run_id=one.id, success=True, status="success", output="a"))
    assert one.id != two.id
    assert len(store.list_tool_runs(turn.id)) == 2


def test_finish_tool_run_is_idempotent_and_never_demotes_success(tmp_path: Path):
    store = StateStore(tmp_path / "state")
    turn = store.create_execution(thread_id="s", query="search")
    store.create_tool_run(
        turn_id=turn.id, session_id="s", tool_name="web_search", run_id="canon",
        canonical_arguments={"q": "x"},
    )
    store.finish_tool_run(
        "canon",
        ToolOutcome(tool_name="web_search", run_id="canon", success=True, status="complete", output="evidence"),
    )
    store.finish_tool_run(
        "canon",
        ToolOutcome(tool_name="web_search", run_id="canon", success=False, status="failed", error_message="late"),
    )
    run = next(r for r in store.list_tool_runs(turn.id) if r.id == "canon")
    assert run.status == "complete"
    assert (run.outcome or {}).get("success") is True
    # create after terminal must not re-open
    store.create_tool_run(
        turn_id=turn.id, session_id="s", tool_name="web_search", run_id="canon",
        canonical_arguments={"q": "x"},
    )
    run2 = next(r for r in store.list_tool_runs(turn.id) if r.id == "canon")
    assert run2.status == "complete"


def test_session_timeline_hydrates_tools_and_redacts_secrets(tmp_path: Path):
    """Page refresh must restore ToolRuns under the owning execution_id."""
    store = StateStore(tmp_path / "state")
    turn = store.create_execution(thread_id="sess-h", query="search weather", model_id="m")
    store.create_tool_run(
        turn_id=turn.id,
        session_id="sess-h",
        tool_name="web_search",
        run_id="run-ws-h",
        canonical_arguments={"q": "weather", "api_key": "SECRET_KEY"},
    )
    store.finish_tool_run(
        "run-ws-h",
        ToolOutcome(
            tool_name="web_search",
            run_id="run-ws-h",
            success=True,
            status="success",
            output="https://example.com/weather",
        ),
    )
    store.add_item(
        turn_id=turn.id,
        session_id="sess-h",
        item_type="assistant_message",
        status="complete",
        payload={"text": "Sunny.", "backend_success": True},
    )
    store.update_execution(
        turn.id,
        status="completed",
        success=True,
        response_preview="Sunny.",
        tools_used=["web_search"],
        terminal_status="complete",
    )
    timeline = store.session_timeline("sess-h")
    assert timeline["count"] == 1
    proj = timeline["turns"][0]
    assert proj["execution_id"] == turn.id
    assert len(proj["tool_runs"]) == 1
    assert proj["tool_runs"][0]["id"] == "run-ws-h"
    assert proj["tool_runs"][0]["canonical_arguments"]["api_key"] == "[redacted]"
    assert proj["research_runs"]
    roles = [m["role"] for m in proj["messages"]]
    assert "user" in roles and "assistant" in roles
    other = store.session_timeline("other-session")
    assert other["count"] == 0


def test_migration_backs_up_legacy_state_without_deleting_it(tmp_path: Path):
    root = tmp_path / "state"
    root.mkdir()
    legacy = {"s": {"thread_id": "s", "objective": "keep me"}}
    (root / "thread_state.json").write_text(json.dumps(legacy), encoding="utf-8")
    StateStore(root)
    assert (root / "thread_state.json").exists()
    backups = list((root / "migration-backups").glob("*/thread_state.json"))
    assert len(backups) == 1


def test_all_models_receive_equal_full_functional_defaults():
    """Local and hosted profiles share full runtime defaults; no weak-model gates."""
    local = resolve_model_profile("ollama", "custom-local-model")
    hosted = resolve_model_profile("openai", "gpt-4o")
    assert local.one_tool_at_a_time is False
    assert hosted.one_tool_at_a_time is False
    assert local.recommended_plan_depth == hosted.recommended_plan_depth
    assert local.maximum_autonomous_steps == hosted.maximum_autonomous_steps
    assert local.recommended_parallelism == hosted.recommended_parallelism
    assert local.recommended_budget == local.context_limit
    # No local-vs-hosted context tier: same universal fallback without overrides.
    assert local.context_limit == hosted.context_limit
    # Explicit real window wins (e.g. context_length=65536 must not clamp to 32k).
    wide = resolve_model_profile("ollama", "phi", {"context_limit": 65536})
    assert wide.context_limit == 65536
    # Explicit config still wins for observability / operator overrides.
    configured = resolve_model_profile(
        "ollama",
        "custom-local-model",
        {"maximum_autonomous_steps": 9, "one_tool_at_a_time": True, "recommended_plan_depth": 2},
    )
    assert configured.maximum_autonomous_steps == 9
    assert configured.one_tool_at_a_time is True
    assert configured.recommended_plan_depth == 2
    assert configured.source == "configured"


def test_allow_llm_tool_calling_is_equal_across_providers(monkeypatch):
    """No provider or model-name allowlist may deny the tool-capable path."""
    from agent.core import EchoSpeakAgent
    from config import ModelProvider, config

    monkeypatch.setattr(config, "disable_native_tool_calling", False, raising=False)
    agent = object.__new__(EchoSpeakAgent)
    for provider in (
        ModelProvider.OPENAI,
        ModelProvider.GEMINI,
        ModelProvider.OLLAMA,
        ModelProvider.LM_STUDIO,
        ModelProvider.LOCALAI,
        ModelProvider.VLLM,
    ):
        agent.llm_provider = provider
        assert agent._allow_llm_tool_calling() is True, provider

    monkeypatch.setattr(config, "disable_native_tool_calling", True, raising=False)
    agent.llm_provider = ModelProvider.OLLAMA
    assert agent._allow_llm_tool_calling() is False
    monkeypatch.setattr(config, "disable_native_tool_calling", False, raising=False)


def test_effective_context_window_prefers_explicit_profile_then_trim(monkeypatch):
    from agent.core import EchoSpeakAgent
    from config import ModelProvider, config

    agent = object.__new__(EchoSpeakAgent)
    agent.llm_provider = ModelProvider.OLLAMA
    agent._active_model_profile = resolve_model_profile("ollama", "x", {"context_limit": 8192})
    monkeypatch.setattr(config, "llm_trim_max_tokens", 0, raising=False)
    monkeypatch.setattr(config.local, "context_length", 65536, raising=False)
    assert agent._resolve_effective_context_window() == 8192
    monkeypatch.setattr(config, "llm_trim_max_tokens", 100000, raising=False)
    assert agent._resolve_effective_context_window() == 100000


def test_tool_repair_is_bounded_and_rejects_unknown_tools():
    assert repair_tool_call_once("prefix {'tool':'file_read','arguments':{'path':'x'},}", {"file_read"})["arguments"] == {"path": "x"}
    with pytest.raises(ValueError, match="Unknown tool"):
        repair_tool_call_once('{"tool":"delete_everything","arguments":{}}', {"file_read"})
    with pytest.raises(ValueError, match="No JSON"):
        repair_tool_call_once("please run a tool", {"file_read"})


def test_provider_logic_isolated_in_model_adapters():
    assert get_model_adapter("gemini").tool_call_format == "gemini-function-calling"
    assert get_model_adapter("lmstudio").tool_call_format == "openai-tools"


def test_projects_bind_exact_folders_and_read_only_git_scope(tmp_path: Path):
    folder_a = tmp_path / "a"
    folder_b = tmp_path / "b"
    folder_a.mkdir(); folder_b.mkdir()
    manager = ProjectManager(tmp_path / "projects")
    a = manager.attach_folder(str(folder_a))
    b = manager.attach_folder(str(folder_b))
    assert a.workspace_root != b.workspace_root
    assert isinstance(a.git_metadata.get("is_repository"), bool)
    if a.git_metadata.get("is_repository"):
        assert a.git_metadata.get("root")
    assert b.workspace_root.endswith("b")


def test_query_message_recording_never_creates_a_session(tmp_path: Path, monkeypatch):
    import agent.threads as threads_mod
    from agent.threads import ThreadManager
    from api.server import _record_session_message

    manager = ThreadManager(tmp_path / "threads.json")
    monkeypatch.setattr(threads_mod, "_thread_manager", manager)
    _record_session_message("missing-session", "hello")
    assert manager.get_thread("missing-session") is None

    manager.create_thread(thread_id="explicit-session", title="New Session", source="web")
    _record_session_message("explicit-session", "real first message")
    recorded = manager.get_thread("explicit-session")
    assert recorded is not None
    assert recorded.message_count == 1
    assert recorded.title == "real first message"


def test_schema_invalid_runtime_authority_is_quarantined_and_fails_closed(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    source = root / "approvals.json"
    source.write_text(json.dumps({"approval-1": {"id": "approval-1"}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="recovery guide"):
        StateStore(root)
    assert json.loads(source.read_text(encoding="utf-8")) == {"approval-1": {"id": "approval-1"}}
    recovery_dirs = list((root / "corrupt-state").iterdir())
    assert recovery_dirs
    assert (recovery_dirs[0] / "approvals.json").exists()
    assert (recovery_dirs[0] / "RECOVERY.txt").exists()
