from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from agent.core import EchoSpeakAgent
from agent.grounding_guard import check_grounding
from agent.model_runtime import (
    StructuredOutputCapability,
    clear_structured_output_probe_cache,
    resolve_model_profile,
    resolve_structured_output_capability,
)
from agent.semantic_runtime import CanonicalSemanticRuntime
from agent.retrieval_contracts import plan_research_query
from agent.sports_data import SportsDataClient
from agent.state import StateStore, ToolOutcome
from agent.tool_registry import ToolRegistry
from agent.turn_understanding import (
    TurnCancelledError,
    TurnInterpretation,
    TurnRelation,
    decode_turn_interpretation_payload,
)


class _StructuredRunnable:
    def with_structured_output(self, *_args, **_kwargs):
        return self


def test_lmstudio_capability_probe_is_cached_and_falls_back(monkeypatch) -> None:
    calls: list[str] = []

    def fake_probe(*, endpoint: str, model_id: str, timeout: float):
        calls.append(model_id)
        if model_id == "supported":
            return StructuredOutputCapability(
                "native_json_schema", "json_schema", "model_host_probe_succeeded", probed=True
            )
        return StructuredOutputCapability(
            "prompt_json", "bounded_json_extraction", "model_host_probe_failed:ValueError", probed=True
        )

    monkeypatch.setattr("agent.model_runtime._probe_openai_compatible_json_schema", fake_probe)
    clear_structured_output_probe_cache()
    runnable = _StructuredRunnable()
    supported = resolve_model_profile("lmstudio", "supported", {"base_url": "http://127.0.0.1:1234"})
    first = resolve_structured_output_capability(
        "lmstudio", "supported", llm=runnable, profile=supported
    )
    second = resolve_structured_output_capability(
        "lmstudio", "supported", llm=runnable, profile=supported
    )
    unsupported = resolve_structured_output_capability(
        "lmstudio",
        "unsupported",
        llm=runnable,
        profile=resolve_model_profile(
            "lmstudio", "unsupported", {"base_url": "http://127.0.0.1:1234"}
        ),
    )
    assert first.mode == second.mode == "native_json_schema"
    assert unsupported.mode == "prompt_json"
    assert calls == ["supported", "unsupported"]


def test_ten_repeated_canonical_classifications_validate() -> None:
    for index in range(10):
        payload = (
            {"relation": "casual_conversation", "confidence": 0.99}
            if index % 2 == 0
            else {
                "relation": "new_task",
                "proposed_objective": f"Objective {index}",
                "confidence": 0.95,
            }
        )
        canonical, _diagnostics = decode_turn_interpretation_payload(payload)
        interpretation = TurnInterpretation.model_validate(canonical)
        assert interpretation.relation in {
            TurnRelation.CASUAL_CONVERSATION,
            TurnRelation.NEW_TASK,
        }


def test_query_plan_rejects_labels_quotes_username_and_isolates_flights() -> None:
    with pytest.raises(ValueError, match="internal execution-envelope"):
        plan_research_query(
            "Task objective: about him Latest user message: search up ty0x7 on Twitch"
        )
    username = plan_research_query("search up ty0x7 on Twitch").provider_query()
    assert '"ty0x7"' in username

    flight = plan_research_query(
        "Find latest flight information from Edmonton to Vegas tomorrow at 2pm kickoff"
    ).provider_query()
    assert "kickoff" not in flight.casefold()
    assert flight.casefold().count("tomorrow") == 1
    assert flight.casefold().count("2pm") == 1


def test_dangling_pronoun_query_fails_closed() -> None:
    with pytest.raises(ValueError, match="pronoun"):
        plan_research_query("search about him").provider_query()


def test_sports_schedule_operation_and_no_data_state() -> None:
    client = SportsDataClient(api_key="test")
    client._get = lambda _path, _params=None: ([], "")  # type: ignore[method-assign]
    result = client.query("next FIFA World Cup game", operation="competition_next_event")
    assert result.mode == "schedule"
    assert result.ok is False
    assert result.result_state == "no_data"
    assert "execution_status=success" in result.as_tool_text()


def test_no_data_transport_does_not_count_as_successful_tool_work() -> None:
    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent._partial_tool_results = [{
        "tool": "sports_live",
        "success": True,
        "execution_status": "success",
        "result_state": "no_data",
    }]
    agent._current_execution_id = ""
    assert agent._tools_succeeded_this_turn() == set()


def test_user_time_constraint_has_user_input_provenance() -> None:
    result = check_grounding(
        "I will look for a departure at 2 pm.",
        [],
        user_constraints=["Find a flight tomorrow at 2 pm"],
    )
    assert result.is_grounded is True
    assert result.claim_provenance.get("at 2 pm") == "user_input"


def test_exact_tool_run_terminalizes_same_identity(tmp_path) -> None:
    store = StateStore(tmp_path / "state")
    run = store.create_tool_run(
        turn_id="turn-1", tool_name="web_search", run_id="run-1", session_id="session-1"
    )
    finished = store.finish_tool_run(
        run.id,
        ToolOutcome(
            tool_name="web_search",
            run_id=run.id,
            execution_id="turn-1",
            turn_id="turn-1",
            session_id="session-1",
            success=True,
            status="success",
            execution_status="success",
            result_state="data_found",
            output="verified evidence",
            verification={"runtime_boundary": "test"},
        ),
    )
    assert finished is not None
    assert finished.id == run.id == "run-1"
    assert [item for item in store.list_tool_runs("turn-1") if item.status == "started"] == []


def test_registry_snapshot_is_stable_without_revision_change() -> None:
    first = ToolRegistry.inventory_snapshot()
    second = ToolRegistry.inventory_snapshot()
    assert (first["revision"], first["count"], first["sha256"]) == (
        second["revision"], second["count"], second["sha256"]
    )


def test_turn_understanding_cancellation_closes_provider_stream(monkeypatch) -> None:
    cancel_event = threading.Event()

    class SlowStreamingRunnable:
        closed = False

        def bind(self, **_kwargs):
            return self

        def stream(self, _messages):
            try:
                while True:
                    time.sleep(0.025)
                    yield SimpleNamespace(content="", additional_kwargs={}, response_metadata={})
            finally:
                self.closed = True

    runnable = SlowStreamingRunnable()
    monkeypatch.setattr(
        "agent.semantic_runtime.resolve_structured_output_capability",
        lambda *_args, **_kwargs: StructuredOutputCapability(
            "prompt_json", "bounded_json_extraction", "test_fallback", probed=True
        ),
    )
    agent = SimpleNamespace(
        model_runtime=SimpleNamespace(llm=runnable, model_id="test-model"),
        llm_provider=SimpleNamespace(value="test-provider"),
        _active_model_profile=SimpleNamespace(local=False, model_id="test-model", metadata={}),
    )
    timer = threading.Timer(0.1, cancel_event.set)
    timer.start()
    try:
        with pytest.raises(TurnCancelledError, match="cancelled"):
            CanonicalSemanticRuntime._invoke_understanding_model(
                agent,
                [{"role": "user", "content": "cancel this request"}],
                schema={"type": "object", "properties": {}},
                cancel_event=cancel_event,
            )
    finally:
        timer.cancel()
    deadline = time.monotonic() + 1.0
    while not runnable.closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runnable.closed is True


def test_missing_openai_key_never_constructs_openai_embeddings(monkeypatch, tmp_path) -> None:
    from agent import memory as memory_module
    from config import ModelProvider, config

    calls: list[dict] = []

    class ForbiddenOpenAIEmbeddings:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError("OpenAI embeddings must not be constructed without a key")

    monkeypatch.setattr(memory_module, "OpenAIEmbeddings", ForbiddenOpenAIEmbeddings)
    monkeypatch.setattr(memory_module, "_require_complete_torch", lambda: (_ for _ in ()).throw(RuntimeError("disabled in test")))
    monkeypatch.setattr(config.embedding, "provider", ModelProvider.OPENAI)
    monkeypatch.setattr(config.openai, "api_key", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    memory = memory_module.AgentMemory(str(tmp_path / "memory"))

    assert calls == []
    assert memory.embedding_health["provider"] == "huggingface_local"
    assert memory.use_faiss is False
