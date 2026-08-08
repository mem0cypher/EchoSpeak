from __future__ import annotations

from pathlib import Path
import queue
import sys
from types import SimpleNamespace

import pytest


def _mock_mcp_config():
    fixture = Path(__file__).resolve().parent / "fixtures" / "mock_mcp_server.py"
    return {
        "mock": {
            "command": sys.executable,
            "args": [str(fixture)],
            "transport": "stdio",
            "trust": "trusted",
            "enabled": True,
            "timeout_s": 10,
        }
    }


@pytest.fixture(autouse=True)
def _mcp_isolation():
    from agent.mcp_client import reset_mcp_manager
    from agent.tool_registry import ToolRegistry

    reset_mcp_manager()
    for name in list(ToolRegistry.get_names()):
        if name.startswith("mcp__"):
            ToolRegistry._entries.pop(name, None)
    yield
    reset_mcp_manager()


def test_connection_tool_uses_registry_and_revalidates_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import agent.connections as connections
    from agent.connections import (
        ConnectionAuthentication,
        ConnectionCapability,
        ConnectionCapabilityKind,
        ConnectionHealth,
        ConnectionKind,
        ConnectionRecord,
        ConnectionRegistry,
        ConnectionScope,
        ConnectionScopeError,
        register_connection_tool,
    )
    from agent.tool_registry import ToolRegistry
    from agent.tools import bind_tool_execution_context, reset_tool_execution_context

    registry = ConnectionRegistry(tmp_path / "connections.json")
    registry.register(ConnectionRecord(
        id="fixture-connection",
        kind=ConnectionKind.API,
        display_name="Fixture Connection",
        enabled=True,
        health=ConnectionHealth.HEALTHY,
        authentication=ConnectionAuthentication.CONFIGURED,
        scope=ConnectionScope(project_ids=["project-a"], session_ids=["session-a"]),
        capabilities=[ConnectionCapability(
            id="fixture.echo",
            kind=ConnectionCapabilityKind.TOOL,
            name="Echo",
            tool_names=["connection_echo"],
            available=True,
        )],
    ))
    monkeypatch.setattr(connections, "_REGISTRY", registry)

    class FixtureTool:
        name = "connection_echo"
        description = "Echo through a governed Connection"

        def invoke(self, payload, config=None):
            return f"echo:{payload['text']}"

    wrapped = register_connection_tool(
        connection_id="fixture-connection",
        capability_id="fixture.echo",
        tool=FixtureTool(),
        project_id="project-a",
        session_id="session-a",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    )
    try:
        entry = ToolRegistry.get("connection_echo")
        assert entry is not None
        assert entry.origin == "connection"
        assert entry.connection_id == "fixture-connection"
        assert ToolRegistry.available_in_scope("connection_echo", project_id="project-a", session_id="session-a")
        assert not ToolRegistry.available_in_scope("connection_echo", project_id="project-b", session_id="session-a")

        token = bind_tool_execution_context({"active_project_id": "project-a", "session_id": "session-a"})
        try:
            assert wrapped.invoke({"text": "ok"}) == "echo:ok"
        finally:
            reset_tool_execution_context(token)

        token = bind_tool_execution_context({"active_project_id": "project-b", "session_id": "session-a"})
        try:
            with pytest.raises(ConnectionScopeError):
                wrapped.invoke({"text": "blocked"})
        finally:
            reset_tool_execution_context(token)
    finally:
        ToolRegistry.remove_owned("connection_echo", "connection:fixture-connection")


def test_mcp_disconnect_marks_tools_unavailable_and_reconnects():
    from agent.mcp_client import get_mcp_manager
    from agent.tool_registry import ToolRegistry
    manager = get_mcp_manager()
    manager.initialize_servers(_mock_mcp_config())
    entry = ToolRegistry.get("mcp__mock__echo")
    assert entry is not None and entry.origin == "mcp" and entry.mcp_server == "mock"
    assert entry.input_schema.get("type") == "object"

    process = manager.sessions["mock"].state.process
    process.kill()
    process.wait(timeout=5)
    status = manager.status()
    assert status["running_count"] == 0
    assert ToolRegistry.get("mcp__mock__echo").available is False
    assert "mcp__mock__echo" not in {
        getattr(tool, "name", "") for tool in ToolRegistry.get_funcs()
    }

    status = manager.initialize_servers(_mock_mcp_config())
    assert status["running_count"] == 1
    assert ToolRegistry.get("mcp__mock__echo").available is True


def test_mcp_duplicate_capability_name_cannot_overwrite():
    from agent.mcp_client import get_mcp_manager

    manager = get_mcp_manager()
    manager.initialize_servers(_mock_mcp_config())
    session = manager.sessions["mock"]
    with pytest.raises(ValueError, match="collision"):
        manager._register_tool("mock", {"name": "echo", "inputSchema": {"type": "object"}}, session)


@pytest.mark.parametrize(
    ("opening", "capability", "missing"),
    [
        ("Remind me", "reminder", "when"),
        ("Create a file", "file_write", "path"),
        ("Open the application", "open_app", "application"),
        ("Send an email", "email", "recipient"),
        ("Research this", "research", "query"),
    ],
)
def test_startup_readiness_separates_core_from_degraded_optional(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import agent.startup_readiness as readiness

    monkeypatch.setattr(readiness, "DATA_DIR", tmp_path)
    for name in (
        "_projects", "_sessions", "_active_scope", "_tools", "_skills", "_memory",
        "_runtime_state", "_jobs", "_media", "_tasks", "_routines", "_heartbeat", "_schema",
    ):
        monkeypatch.setattr(readiness, name, lambda: {"detail": "Ready"})
    monkeypatch.setattr(readiness, "_model", lambda: {"ready": False, "degraded": True, "detail": "offline"})
    monkeypatch.setattr(readiness, "_adapter", lambda: {"detail": "Ready"})
    monkeypatch.setattr(readiness, "_connections_mcp", lambda: {"detail": "Ready"})
    monkeypatch.setattr(readiness, "_embeddings", lambda: {"ready": False, "degraded": True, "detail": "missing"})
    monkeypatch.setattr(readiness, "_document_retrieval", lambda: {"ready": False, "degraded": True, "detail": "missing"})

    payload = readiness.build_startup_readiness()
    assert payload["backend_available"] is True
    assert payload["core_ready"] is True
    assert payload["full_ready"] is False
    assert payload["degraded"] is True
    assert set(payload["degraded_capabilities"]) >= {"model", "embeddings", "document_retrieval"}


def test_typed_memory_remains_writable_without_embeddings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import agent.memory as memory_module

    monkeypatch.setattr(memory_module, "OpenAIEmbeddings", None)
    monkeypatch.setattr(
        memory_module,
        "_require_complete_torch",
        lambda: (_ for _ in ()).throw(RuntimeError("torch.autograd unavailable")),
    )
    memory = memory_module.AgentMemory(str(tmp_path / "memory"))
    memory_id = memory.add_memory_item(
        "Use concise summaries",
        memory_type="preference",
        source="explicit_user_request",
        scope="account",
    )
    status = memory.capability_status()
    assert memory_id
    assert status["typed_memory_ready"] is True
    assert status["semantic_retrieval_ready"] is False
    assert status["canonical_record_count"] == 1


def test_document_index_rebuilds_from_canonical_text(tmp_path: Path):
    from langchain_core.embeddings import Embeddings
    from agent.document_store import DocumentStore

    class FixtureEmbeddings(Embeddings):
        def embed_documents(self, texts):
            return [self.embed_query(text) for text in texts]

        def embed_query(self, text):
            value = float(sum(ord(char) for char in str(text)) % 97) / 97.0
            return [value, 1.0 - value, 0.5, 0.25]

    index = tmp_path / "index"
    meta = tmp_path / "documents.json"
    store = DocumentStore(FixtureEmbeddings(), str(index), str(meta))
    record = store.add_document(
        "fixture.txt",
        "Edmonton is the capital city of Alberta.",
        project_id="project-a",
        session_id="session-a",
    )
    assert (tmp_path / "document_content" / f"{record['id']}.txt").exists()

    (index / "index.faiss").write_bytes(b"corrupt disposable index")
    rebuilt = DocumentStore(FixtureEmbeddings(), str(index), str(meta))
    status = rebuilt.capability_status()
    assert status["ready"] is True
    assert status["index_rebuildable"] is True
    if status["hybrid_requested"] and not status["hybrid_ready"]:
        assert status["degraded"] is True
        assert "BM25" in status["detail"]
    context, sources = rebuilt.query("capital Alberta", project_id="project-a", session_id="session-a")
    assert "Edmonton" in context
    assert sources


def test_late_execution_event_cannot_project_newer_session_state(monkeypatch: pytest.MonkeyPatch):
    import api.server as server

    class ThreadState:
        def __init__(self, execution_id: str):
            self.execution_id = execution_id

        def model_dump(self):
            return {"last_execution_id": self.execution_id, "current_execution_id": self.execution_id}

    class StateStore:
        latest_execution_id = "execution-old"

        def get_thread_state(self, _thread_id):
            return ThreadState(self.latest_execution_id)

        def get_execution(self, execution_id):
            return SimpleNamespace(id=execution_id, trace_id=f"trace-{execution_id}", metadata={})

        def turn_projection(self, execution_id):
            return {"execution_projection": {"execution_id": execution_id}}

    store = StateStore()

    class Memory:
        @staticmethod
        def count_items(**_kwargs):
            return 0

    class Agent:
        memory = Memory()

        @staticmethod
        def process_query(*_args, **_kwargs):
            store.latest_execution_id = "execution-new"
            return "older response", True

        @staticmethod
        def completed_execution_id_for_current_worker():
            return "execution-old"

        @staticmethod
        def get_last_doc_sources():
            return []

        @staticmethod
        def get_last_tts_text():
            return ""

    monkeypatch.setattr(server, "get_state_store", lambda: store)
    events: queue.Queue = queue.Queue()
    server._start_agent_thread(
        agent=Agent(), message="first request", include_memory=False,
        thread_id="session-a", workspace=None, request_id="request-old", q=events,
    )

    emitted = []
    while True:
        event = events.get(timeout=5)
        if event is None:
            break
        emitted.append(event)
    final = next(event for event in emitted if event.get("type") == "final")
    assert final["execution_id"] == "execution-old"
    assert final["execution_projection"] == {"execution_id": "execution-old"}
    assert final["thread_state"] == {}
