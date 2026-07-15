from pathlib import Path
from types import SimpleNamespace

import pytest


def test_product_task_identity_is_stable_and_state_is_atomic(tmp_path: Path) -> None:
    from agent.task_store import TaskStore

    path = tmp_path / "todos.json"
    store = TaskStore(path)
    first = store.create(title="Daily brief", idempotency_key="routine:r1:bucket")
    second = store.create(title="Duplicate retry", idempotency_key="routine:r1:bucket")
    assert second.id == first.id

    updated = store.update(first.id, status="complete", verification={"verified": True})
    assert updated is not None and updated.status == "complete"
    reloaded = TaskStore(path).get(first.id)
    assert reloaded is not None and reloaded.verification == {"verified": True}
    assert not list(tmp_path.glob("*.tmp.*"))


def test_corrupt_product_tasks_fail_closed_with_recovery_copy(tmp_path: Path) -> None:
    from agent.task_store import TaskStore

    path = tmp_path / "todos.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="authoritative file was not overwritten"):
        TaskStore(path)
    assert path.read_text(encoding="utf-8") == "{broken"
    assert list((tmp_path / "corrupt-state").rglob("RECOVERY.txt"))


def test_routine_requires_coordinator_and_records_callback_result(tmp_path: Path) -> None:
    from agent.routines import RoutineManager

    manager = RoutineManager(tmp_path / "routines")
    routine = manager.create_routine(name="Governed", trigger_type="manual")
    assert manager.run_routine(routine.id) is False
    blocked = manager.get_routine(routine.id)
    assert blocked is not None
    assert blocked.last_result_status == "failed"
    assert "coordinator" in blocked.last_error.lower()

    manager.set_run_callback(lambda _routine: {"success": True, "task_id": "task-1"})
    assert manager.run_routine(routine.id) is True
    completed = manager.get_routine(routine.id)
    assert completed is not None
    assert completed.last_task_id == "task-1"
    assert completed.last_result_status == "complete"


def test_heartbeat_creates_one_task_and_uses_governed_turn(tmp_path: Path, monkeypatch) -> None:
    from agent.heartbeat import HeartbeatManager
    from agent.automation_runtime import AutomationRunStore
    from agent.task_store import TaskStore
    import agent.automation_runtime as automation_module
    import agent.projects as project_module
    import agent.state as state_module
    import agent.task_store as task_module

    store = TaskStore(tmp_path / "todos.json")
    monkeypatch.setattr(task_module, "get_task_store", lambda: store)
    run_store = AutomationRunStore(tmp_path / "automation-runs")
    monkeypatch.setattr(automation_module, "get_automation_run_store", lambda: run_store)
    monkeypatch.setattr(
        project_module,
        "get_project_manager",
        lambda: SimpleNamespace(get_project=lambda project_id: SimpleNamespace(id=project_id)),
    )
    thread_state = SimpleNamespace(
        active_project_id="project-a",
        last_execution_id="execution-1",
        current_execution_id="",
        pending_approval_id="",
    )
    monkeypatch.setattr(
        state_module,
        "get_state_store",
        lambda: SimpleNamespace(
            get_thread_state=lambda _thread: thread_state,
            list_tool_runs=lambda _execution: [],
        ),
    )

    calls = []

    class FakeAgent:
        llm_provider = SimpleNamespace(value="lmstudio")
        provider_info = {"model": "test-model"}

        def process_query(self, prompt, **kwargs):
            calls.append((prompt, kwargs))
            return "A useful update", True

    manager = HeartbeatManager(
        agent=FakeAgent(),
        interval_minutes=30,
        prompt="Check now",
        channels=["web", "email"],
        project_id="project-a",
        session_id="session-a",
    )
    monkeypatch.setattr(manager, "_gather_system_pulse", lambda: "all systems nominal")
    manager._tick()

    assert len(calls) == 1
    assert calls[0][1] == {
        "include_memory": False,
        "callbacks": [],
        "thread_id": "session-a",
        "source": "heartbeat",
    }
    tasks = store.list()
    assert len(tasks) == 1
    assert tasks[0].status == "needs_permission"
    assert tasks[0].execution_ids == ["execution-1"]
    assert tasks[0].verification["blocked_delivery_channels"] == ["email"]


def test_background_channel_router_never_calls_legacy_external_senders(monkeypatch) -> None:
    import agent.heartbeat as heartbeat

    assert not hasattr(heartbeat, "_route_discord")
    assert not hasattr(heartbeat, "_route_telegram")
    assert not hasattr(heartbeat, "_route_email")
    assert not hasattr(heartbeat, "_route_whatsapp")
    heartbeat.route_message("hello", ["web", "discord", "telegram", "email", "whatsapp"])
