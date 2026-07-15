"""Production-closure regressions: named-file pin, approval identity, skills truth, research artifacts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest


def test_explicit_file_excludes_and_named_pin():
    from agent.core import EchoSpeakAgent
    from config import ModelProvider

    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    files = ["proj/index.html", "proj/game.js", "proj/style.css"]
    named = agent._explicit_files_named_in_request(
        "Change the button text in index.html. Do not edit game.js.",
        files,
    )
    assert [Path(f).name for f in named] == ["index.html"]
    assert agent._file_write_path_allowed_by_request(
        "Change the button text in index.html.", "proj/index.html", files
    )
    assert not agent._file_write_path_allowed_by_request(
        "Change the button text in index.html. Do not edit game.js.",
        "proj/game.js",
        files,
    )
    assert not agent._file_write_path_allowed_by_request(
        "Change the button text in index.html.",
        "proj/game.js",
        files,
    )


def test_named_edit_never_targets_game_js(tmp_path, monkeypatch):
    from agent.active_work import ActiveWorkStore
    from agent.core import EchoSpeakAgent
    from agent.projects import ProjectManager
    from agent.session_memory import SessionMemoryDistiller
    from agent.state import StateStore
    from config import ModelProvider, config
    import agent.projects as projects_mod
    import agent.state as state_mod

    fixture = Path(__file__).parent / "fixtures" / "coding_project"
    project_root = tmp_path / "proj"
    shutil.copytree(fixture, project_root)
    manager = ProjectManager(tmp_path / "projects")
    project = manager.attach_folder(str(project_root), name="Fix", trust_state="trusted")
    runtime = StateStore(tmp_path / "phase3")
    monkeypatch.setattr(projects_mod, "_project_manager", manager)
    monkeypatch.setattr(state_mod, "_state_store", runtime)
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_file_write", True)
    monkeypatch.setattr(config, "allow_terminal_commands", False)
    monkeypatch.setattr(config, "disable_native_tool_calling", True)
    monkeypatch.setattr(config, "verification_telemetry_enabled", False)
    monkeypatch.setattr(config, "file_tool_root", str(project_root))

    agent = EchoSpeakAgent(
        memory_path=str(tmp_path / "memory"),
        llm_provider=ModelProvider.OPENAI,
        manage_background_services=False,
    )
    agent._session_memory = SessionMemoryDistiller(tmp_path / "sessions")
    agent._active_work_store = ActiveWorkStore(tmp_path / "active-work")
    agent._allow_llm_tool_calling = lambda: False
    agent.graph_agent = None
    agent.agent_executor = None
    agent.fallback_executor = None
    agent.select_thread_runtime("s1")
    agent.activate_project(project.id)

    class LLM:
        def invoke(self, prompt: str) -> str:
            if "SEARCH/REPLACE" in prompt or "FILE:" in prompt:
                # Maliciously try to retarget game.js
                return (
                    "### FILE: game.js\n<<<<<<< SEARCH\nconst x = 1;\n=======\nconst x = 2;\n>>>>>>> REPLACE\n"
                    "### FILE: index.html\n<<<<<<< SEARCH\n<title>EchoSpeak Coding Fixture</title>\n"
                    "=======\n<title>Pinned Title</title>\n>>>>>>> REPLACE\n"
                )
            return "ok"

        def invoke_with_reasoning(self, prompt: str):
            return self.invoke(prompt), ""

    agent.llm_wrapper = LLM()
    game_before = (project_root / "game.js").read_bytes()
    agent.process_query(
        "Change the title in index.html only. Do not edit game.js.",
        include_memory=False,
        thread_id="s1",
    )
    approval = runtime.get_pending_approval("s1")
    assert approval is not None
    assert Path(approval.kwargs["path"]).name == "index.html"
    assert Path(approval.kwargs["path"]).name != "game.js"
    assert (project_root / "game.js").read_bytes() == game_before


def test_skill_status_audit_classifies():
    from agent.skill_status_audit import audit_all_skills

    rows = audit_all_skills(available_capabilities=set(), available_artifacts=set())
    assert rows
    # Disabled packages if any must not be executable
    for r in rows:
        if r["status"] == "disabled":
            assert r["executable"] is False


def test_research_artifact_ownership_and_lookup(tmp_path, monkeypatch):
    import agent.research_artifacts as ra

    monkeypatch.setattr(ra, "_ROOT", tmp_path / "arts")
    art = ra.build_research_artifact_from_tool_output(
        output="Findings https://example.com/a and https://example.com/b about oilers.",
        query="edmonton oilers",
        project_id="p1",
        session_id="s1",
        execution_id="e1",
        tool_run_id="tr1",
        objective="research oilers",
    )
    saved = ra.save_research_artifact(art)
    assert saved.status == "ready"
    assert saved.citations
    found = ra.find_compatible_research_artifact(
        project_id="p1", session_id="s1", objective="research oilers highlights"
    )
    assert found is not None and found.id == saved.id
    # Wrong project must not match when project_id set on artifact
    assert ra.find_compatible_research_artifact(project_id="p2", objective="oilers") is None


def test_orchestrator_disabled_by_default():
    from config import config

    assert bool(getattr(config, "orchestration_enabled", False)) is False
