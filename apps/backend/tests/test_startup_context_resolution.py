import json
from pathlib import Path

import pytest


def test_desktop_project_and_routine_roots_follow_configured_data(monkeypatch, tmp_path: Path):
    import config
    from agent.projects import ProjectManager
    from agent.routines import RoutineManager

    monkeypatch.setenv("ECHOSPEAK_RUNTIME_KIND", "desktop")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert ProjectManager().projects_dir == tmp_path / "projects"
    assert RoutineManager().routines_dir == tmp_path / "routines"


def test_corrupt_session_registry_is_preserved_and_quarantined(tmp_path: Path):
    from agent.threads import ThreadManager

    path = tmp_path / "threads.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="authoritative file was not overwritten"):
        ThreadManager(path)
    assert path.read_text(encoding="utf-8") == "{not-json"
    guides = list((tmp_path / "corrupt-state").glob("*/RECOVERY.txt"))
    assert guides and "repair or restore" in guides[0].read_text(encoding="utf-8")


def test_typed_context_filters_scope_lifecycle_and_budget():
    from agent.context_chain import ContextAssembler, ContextItem

    items = [
        ContextItem(id="turn", source_type="current_turn", text="current", session_id="A", scope="session"),
        ContextItem(id="wrong", source_type="memory", text="secret", project_id="P2", scope="project"),
        ContextItem(id="forgotten", source_type="memory", text="old", session_id="A", lifecycle="forgotten"),
        ContextItem(id="verified", source_type="tool_outcome", text="verified result", session_id="A", verified=True, trust="verified"),
    ]
    selected = ContextAssembler(project_id="P1", session_id="A").select(items, token_budget=8)
    ids = [item.id for item in selected.selected]
    assert "turn" in ids
    assert "wrong" not in ids
    assert "forgotten" not in ids
    assert selected.used_tokens <= selected.token_budget
    assert all("text" not in row for row in selected.redacted_manifest()["selected"])


def test_resolution_is_bounded_advisory_and_cannot_expand_scope():
    from agent.mode_controller import ModeDecision, TurnMode
    from agent.resolution import EchoResolutionEngine, ResolutionRecommendation

    decision = ModeDecision(
        mode=TurnMode.CODING,
        confidence=0.5,
        reason="ambiguous",
        user_text="delete that file",
        ambiguous=True,
        allowed_tool_names=frozenset({"file_delete"}),
    )
    engine = EchoResolutionEngine()
    calls = 0

    def adviser(_request):
        nonlocal calls
        calls += 1
        return json.dumps({
            "recommended_mode": "coding",
            "interpreted_objective": "delete that file",
            "project_id": "OTHER",
            "session_id": "A",
            "recommended_tools": ["file_delete", "unregistered"],
            "recommendation": "proceed",
        })

    result = engine.resolve(
        user_text="delete that file",
        mode_decision=decision,
        project_id="P1",
        session_id="A",
        available_tools={"file_delete"},
        available_skills=set(),
        adviser=adviser,
    )
    assert calls == 1
    assert result.advice is not None
    assert result.advice.project_id == "P1"
    assert result.advice.recommendation == ResolutionRecommendation.CLARIFY
    assert result.parse_error


def test_startup_readiness_reports_real_steps_without_provider_gate(monkeypatch, tmp_path: Path):
    import agent.startup_readiness as readiness

    monkeypatch.setattr(readiness, "DATA_DIR", tmp_path)
    monkeypatch.setenv("ECHOSPEAK_DESKTOP_INSTANCE_ID", "instance-test")
    for name in (
        "_projects", "_sessions", "_active_scope", "_tools", "_skills", "_runtime_state",
        "_memory", "_jobs", "_media", "_tasks", "_routines", "_heartbeat", "_schema",
    ):
        monkeypatch.setattr(readiness, name, lambda: {"detail": "Ready"})
    payload = readiness.build_startup_readiness()
    assert payload["core_ready"] is True
    assert payload["instance_id"] == "instance-test"
    assert payload["completed_steps"] == payload["total_steps"]
    assert not any(item["key"] == "provider" for item in payload["components"])
