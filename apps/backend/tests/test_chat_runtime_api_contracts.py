from __future__ import annotations

import queue
import threading
import asyncio
from types import SimpleNamespace

import pytest


def test_query_request_accepts_exact_chat_controls():
    from api.server import QueryRequest

    request = QueryRequest(
        message="hello",
        thread_id="session-a",
        client_request_id="request-1234",
        thinking_enabled=False,
        reasoning_effort="ultra",
    )

    assert request.thinking_enabled is False
    assert request.reasoning_effort == "ultra"


def test_chat_runtime_routes_have_one_owner():
    from api.server import app

    route_pairs = [
        (method, route.path)
        for route in app.routes
        for method in list(getattr(route, "methods", None) or [])
    ]
    for method, path in (
        ("POST", "/query"),
        ("POST", "/query/stream"),
        ("POST", "/query/cancel"),
        ("POST", "/query/steer"),
        ("POST", "/query/queue"),
        ("GET", "/query/queue"),
        ("POST", "/query/queue/claim"),
        ("GET", "/provider"),
        ("POST", "/provider/switch"),
        ("GET", "/studio/overview"),
        ("GET", "/startup/readiness"),
    ):
        assert route_pairs.count((method, path)) == 1


def test_stream_handler_emits_real_iteration_usage_and_bounded_summary():
    from api.server import _StreamingHandler

    events: queue.Queue = queue.Queue()
    handler = _StreamingHandler(events, "request-1234")
    handler._agent_ref = SimpleNamespace(
        _turn_thinking_enabled=True,
        _selected_model_id=lambda: "selected-model",
    )
    response = SimpleNamespace(
        llm_output={
            "token_usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            "reasoning_summary": "Checked the current requirement and selected the next safe action.",
        },
        generations=[],
    )

    handler.on_llm_start({}, [], "model-run-1")
    handler.on_llm_end(response, "model-run-1")
    emitted = [events.get_nowait() for _ in range(events.qsize())]

    assert [item["type"] for item in emitted].count("iteration_boundary") == 1
    assert next(item for item in emitted if item["type"] == "token_usage")["total"] == 14
    assert next(item for item in emitted if item["type"] == "reasoning_summary")["content"].startswith("Checked")


def test_stream_handler_suppresses_summary_when_thinking_is_off():
    from api.server import _StreamingHandler

    events: queue.Queue = queue.Queue()
    handler = _StreamingHandler(events, "request-1234")
    handler._agent_ref = SimpleNamespace(
        _turn_thinking_enabled=False,
        _selected_model_id=lambda: "selected-model",
    )
    handler.on_llm_end(
        SimpleNamespace(
            llm_output={"reasoning_summary": "provider summary"},
            generations=[],
        ),
        "model-run-1",
    )

    assert not any(
        item.get("type") == "reasoning_summary"
        for item in list(events.queue)
    )


def test_cancel_matches_exact_session_and_execution(monkeypatch: pytest.MonkeyPatch):
    from api import server

    event = threading.Event()
    monkeypatch.setattr(
        server,
        "_ACTIVE_QUERY_CANCELLATIONS",
        {"request-1234": ("session-a", event, "execution-a")},
    )
    result = asyncio.run(
        server.cancel_query(
            server.QueryCancelRequest(
                request_id="request-1234",
                thread_id="session-a",
                execution_id="execution-a",
            )
        )
    )

    assert result["cancelled"] is True
    assert event.is_set()


def test_steer_requires_exact_active_task_identity(monkeypatch: pytest.MonkeyPatch):
    from agent import task_runs as task_module
    from api import server

    class FakeTask:
        id = "task-a"
        project_id = "project-a"
        status = task_module.TaskRunStatus.RUNNING
        last_execution_id = "execution-a"
        created_by_execution_id = "execution-a"
        revision = 3
        steering_instructions: list[str] = []

        def steer(self, instruction: str) -> None:
            self.steering_instructions.append(instruction)
            self.revision += 1

    task = FakeTask()

    class FakeTaskStore:
        def get(self, task_id: str, *, session_id: str):
            return task if task_id == task.id and session_id == "session-a" else None

        def update(self, task_id: str, **changes):
            return SimpleNamespace(id=task_id, revision=changes["expected_revision"] + 1)

    class FakeStateStore:
        @staticmethod
        def get_execution(execution_id: str):
            return SimpleNamespace(task_run_id="task-a") if execution_id == "execution-a" else None

    monkeypatch.setattr(task_module, "get_task_run_store", lambda: FakeTaskStore())
    monkeypatch.setattr(server, "get_state_store", lambda: FakeStateStore())
    monkeypatch.setattr(
        server,
        "_ACTIVE_QUERY_CANCELLATIONS",
        {"request-1234": ("session-a", threading.Event(), "execution-a")},
    )

    result = asyncio.run(
        server.steer_query(
            server.QuerySteerRequest(
                thread_id="session-a",
                task_run_id="task-a",
                client_request_id="request-1234",
                instruction="Use the verified source first.",
            )
        )
    )

    assert result["steered"] is True
    assert result["applies_at"] == "next_model_boundary"
    assert task.steering_instructions == ["Use the verified source first."]


def test_session_queue_is_durable_and_claimed_fifo(tmp_path):
    from agent.state import StateStore

    store = StateStore(tmp_path)
    store.enqueue_turn(
        "session-a",
        message="first",
        client_request_id="queue-request-1",
    )
    store.enqueue_turn(
        "session-a",
        message="second",
        client_request_id="queue-request-2",
    )

    reloaded = StateStore(tmp_path)
    assert [item["message"] for item in reloaded.list_queued_turns("session-a")] == ["first", "second"]
    assert reloaded.claim_queued_turn("session-a")["message"] == "first"
    assert reloaded.claim_queued_turn("session-a")["message"] == "second"
    assert reloaded.claim_queued_turn("session-a") is None
