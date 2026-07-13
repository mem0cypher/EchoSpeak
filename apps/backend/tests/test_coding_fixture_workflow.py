from __future__ import annotations

import shutil
from pathlib import Path


def test_disposable_fixture_named_edit_lifecycle(tmp_path, monkeypatch):
    """Controlled A-G workflow using real Project/Approval/ToolRun/filesystem owners."""
    from agent.active_work import ActiveWorkStore
    from agent.core import EchoSpeakAgent
    from agent.projects import ProjectManager
    from agent.session_memory import SessionMemoryDistiller
    from agent.state import StateStore
    from config import ModelProvider, config
    import agent.projects as projects_mod
    import agent.state as state_mod

    fixture = Path(__file__).parent / "fixtures" / "coding_project"
    project_root = tmp_path / "controlled-project"
    shutil.copytree(fixture, project_root)
    manager = ProjectManager(tmp_path / "projects")
    project = manager.attach_folder(str(project_root), name="Controlled Fixture", trust_state="trusted")
    runtime = StateStore(tmp_path / "phase3")
    monkeypatch.setattr(projects_mod, "_project_manager", manager)
    monkeypatch.setattr(state_mod, "_state_store", runtime)
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_file_write", True)
    monkeypatch.setattr(config, "allow_terminal_commands", False)
    monkeypatch.setattr(config, "disable_native_tool_calling", True)
    monkeypatch.setattr(config, "verification_telemetry_enabled", False)
    monkeypatch.setattr(config, "file_tool_root", str(project_root))

    agent = EchoSpeakAgent(memory_path=str(tmp_path / "memory"), llm_provider=ModelProvider.OPENAI, manage_background_services=False)
    agent._session_memory = SessionMemoryDistiller(tmp_path / "sessions")
    agent._active_work_store = ActiveWorkStore(tmp_path / "active-work")
    agent._allow_llm_tool_calling = lambda: False
    agent.graph_agent = None
    agent.agent_executor = None
    agent.fallback_executor = None
    agent.select_thread_runtime("fixture-session")
    assert agent.activate_project(project.id)

    class EditLLM:
        mode = "normal"

        def invoke(self, prompt: str) -> str:
            if "SEARCH/REPLACE blocks" in prompt:
                if self.mode == "marker":
                    return "<<<<<<< SEARCH\n<title>Verified Title</title>\n=======\n<<<<<<< SEARCH\n>>>>>>> REPLACE"
                if "Verified Title" in prompt:
                    replacement = "Canceled Title" if self.mode == "cancel" else "Stale Title"
                    return f"<<<<<<< SEARCH\n<title>Verified Title</title>\n=======\n<title>{replacement}</title>\n>>>>>>> REPLACE"
                return "<<<<<<< SEARCH\n<title>EchoSpeak Coding Fixture</title>\n=======\n<title>Verified Title</title>\n>>>>>>> REPLACE"
            return "The exact operation completed."

        def invoke_with_reasoning(self, prompt: str):
            return self.invoke(prompt), ""

    llm = EditLLM()
    agent.llm_wrapper = llm
    source = project_root / "index.html"
    original = source.read_text(encoding="utf-8")

    # A/B: exact read + proposal; no write before confirm.
    response, ok = agent.process_query("Change the title in index.html.", include_memory=False, thread_id="fixture-session")
    assert ok is True
    assert "not been saved" in response.lower()
    assert source.read_text(encoding="utf-8") == original
    approval = runtime.get_pending_approval("fixture-session")
    assert approval is not None and approval.tool == "file_write"
    assert "--- a/index.html" in approval.preview

    confirmed, confirmed_ok = agent.process_query("confirm", include_memory=False, thread_id="fixture-session")
    assert confirmed_ok is True
    assert "Verified Title" in source.read_text(encoding="utf-8")
    confirm_execution = runtime.get_thread_state("fixture-session").last_execution_id
    runs = runtime.list_tool_runs(confirm_execution)
    writes = [run for run in runs if run.tool_name == "file_write"]
    reads = [run for run in runs if run.tool_name == "file_read"]
    assert len(writes) == 1 and writes[0].status == "complete"
    assert writes[0].verification.get("verified") is True
    assert reads and reads[-1].status == "complete"
    projection = runtime.turn_projection(confirm_execution)
    assert len(projection["execution_projection"]["files_actually_changed"]) == 1

    # C: cancellation leaves bytes and changed-file projection untouched.
    llm.mode = "cancel"
    before_cancel = source.read_bytes()
    agent.process_query("Change the title in index.html.", include_memory=False, thread_id="fixture-session")
    pending_cancel = runtime.get_pending_approval("fixture-session")
    assert pending_cancel is not None
    canceled, canceled_ok = agent.process_query("cancel", include_memory=False, thread_id="fixture-session")
    assert canceled_ok is True and "canceled" in canceled.lower()
    assert source.read_bytes() == before_cancel

    # D/E/G: stale source is blocked; exact index target never becomes game.js;
    # terminal remains disabled throughout ordinary editing.
    llm.mode = "stale"
    game_before = (project_root / "game.js").read_bytes()
    agent.process_query("Change the title in index.html after reading game.js for context.", include_memory=False, thread_id="fixture-session")
    stale_approval = runtime.get_pending_approval("fixture-session")
    assert stale_approval is not None
    assert Path(stale_approval.kwargs["path"]).name == "index.html"
    source.write_text(source.read_text(encoding="utf-8") + "\n<!-- external change -->\n", encoding="utf-8")
    stale_response, stale_ok = agent.process_query("confirm", include_memory=False, thread_id="fixture-session")
    assert stale_ok is False
    assert "changed" in stale_response.lower()
    assert "Stale Title" not in source.read_text(encoding="utf-8")
    assert (project_root / "game.js").read_bytes() == game_before
    assert config.allow_terminal_commands is False

    # F: marker-bearing proposal is rejected before ApprovalRecord/write.
    llm.mode = "marker"
    marker_before = source.read_bytes()
    marker_response, marker_ok = agent.process_query("Change the title in index.html.", include_memory=False, thread_id="fixture-session")
    assert marker_ok is False
    assert "marker" in marker_response.lower()
    assert source.read_bytes() == marker_before
