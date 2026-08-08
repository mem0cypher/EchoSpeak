from __future__ import annotations

import time

import pytest

from agent.identity import compile_echo_identity
from agent.model_adapters import AssembledModelResponse, QwenAdapter
from agent.model_contracts import DecisionKind, ToolDefinition, ToolOutcome, ToolUsePolicy
from agent.model_control_plane import (
    LangChainStreamingTransport,
    ModelExecutionControlPlane,
    ModelProviderError,
    ModelStreamIdleTimeout,
    ModelTurnEnvelopeCompiler,
)


TOOL = ToolDefinition(
    name="calculate",
    parameters={
        "type": "object",
        "properties": {"expression": {"type": "string"}},
        "required": ["expression"],
    },
)

SPORTS_TOOL = ToolDefinition(
    name="sports_live",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
)

WEB_TOOL = ToolDefinition(
    name="web_search",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
)

IDENTITY = compile_echo_identity("Echo test identity", provider="lmstudio", model_id="Qwen3.5-9B")


class SequencedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeChunk:
    def __init__(self, *, content="", reasoning="", calls=None, finish=""):
        self.content = content
        self.additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}
        self.tool_call_chunks = list(calls or [])
        self.response_metadata = {"finish_reason": finish} if finish else {}

    def __add__(self, other):
        return other


class FakeStreamingChatModel:
    def __init__(self, chunks):
        self.chunks = chunks
        self.bound = None
        self.messages = None

    def bind_tools(self, tools, tool_choice):
        self.bound = (tools, tool_choice)
        return self

    def stream(self, messages):
        self.messages = messages
        yield from self.chunks


class StallingStreamingChatModel:
    def stream(self, _messages):
        time.sleep(0.2)
        yield FakeChunk(content="too late", finish="stop")


class PartialFailureTransport:
    def __init__(self):
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        kwargs["on_event"]({
            "type": "stream_delta",
            "reasoning_chars": 302,
        })
        raise TimeoutError("stream stopped after partial reasoning")


def _factory(outcomes):
    return ModelTurnEnvelopeCompiler().compile(
        project_id="",
        session_id="session-1",
        turn_id="execution-1",
        execution_id="execution-1",
        request_id="request-1",
        provider="lmstudio",
        model_id="Qwen3.5-9B",
        assistant_identity=IDENTITY,
        objective="calculate",
        task_status="in_progress",
        current_plan_step=None,
        collected_inputs={},
        missing_inputs=[],
        latest_user_relation="new_work",
        latest_user_message="calculate 2+2",
        allowed_tools=[TOOL],
        tool_use_policy=ToolUsePolicy.REQUIRED,
        relevant_memory=[],
        approval=None,
        tool_outcomes=outcomes,
    )


def _execute(name, args):
    return ToolOutcome(
        tool_name=name,
        execution_id="execution-1",
        turn_id="execution-1",
        session_id="session-1",
        success=True,
        status="success",
        output="4",
        verification={"verified": True, "runtime_boundary": "test", "verified_at": time.time()},
    )


def test_sequential_loop_reinjects_verified_outcome_before_answer():
    transport = SequencedTransport([
        AssembledModelResponse(tool_calls=[{
            "id": "call-1", "name": "calculate", "arguments": {"expression": "2+2"}, "index": 0,
        }], finish_reason="tool_calls"),
        AssembledModelResponse(content="The verified result is 4.", finish_reason="stop"),
    ])
    decision, trace = ModelExecutionControlPlane().run(
        envelope_factory=_factory,
        transport=transport,
        execute_tool=_execute,
    )
    assert decision.kind == DecisionKind.ANSWER
    assert trace.tool_runs
    assert len(transport.calls) == 2
    second_messages = transport.calls[1]["messages"]
    assert any(item["role"] == "tool" and "runtime_boundary" in item["content"] for item in second_messages)


def test_elapsed_budget_preserves_one_post_tool_synthesis_call(monkeypatch):
    transport = SequencedTransport([
        AssembledModelResponse(tool_calls=[{
            "id": "call-1", "name": "calculate",
            "arguments": {"expression": "2+2"}, "index": 0,
        }], finish_reason="tool_calls"),
        AssembledModelResponse(content="The verified result is 4.", finish_reason="stop"),
    ])
    ticks = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(
        "agent.model_control_plane.time.monotonic",
        lambda: next(ticks, 2.0),
    )
    diagnostics = []
    decision, _trace = ModelExecutionControlPlane(
        max_elapsed_seconds=1.0
    ).run(
        envelope_factory=_factory,
        transport=transport,
        execute_tool=_execute,
        diagnostic_sink=diagnostics.append,
    )
    assert decision.kind == DecisionKind.ANSWER
    assert len(transport.calls) == 2
    assert any(
        item.get("event") == "post_tool_synthesis_grace"
        for item in diagnostics
    )


def test_mandatory_tool_work_replans_after_prose_only_completion():
    transport = SequencedTransport([
        AssembledModelResponse(content="The answer is probably four.", finish_reason="stop"),
        AssembledModelResponse(tool_calls=[{
            "id": "call-1", "name": "calculate",
            "arguments": {"expression": "2+2"}, "index": 0,
        }], finish_reason="tool_calls"),
        AssembledModelResponse(content="The verified result is 4.", finish_reason="stop"),
    ])
    decision, trace = ModelExecutionControlPlane().run(
        envelope_factory=_factory,
        transport=transport,
        execute_tool=_execute,
    )
    assert decision.kind == DecisionKind.ANSWER
    assert trace.terminal_status == "answer"
    assert trace.proposal_feedback[0]["reason_code"] == "tool_required_before_answer"


def test_parallel_read_tool_calls_are_all_executed_before_next_model_step():
    transport = SequencedTransport([
        AssembledModelResponse(tool_calls=[
            {
                "id": "call-1", "name": "calculate",
                "arguments": {"expression": "2+2"}, "index": 0,
            },
            {
                "id": "call-2", "name": "calculate",
                "arguments": {"expression": "3+3"}, "index": 1,
            },
        ], finish_reason="tool_calls"),
        AssembledModelResponse(content="Both results are verified.", finish_reason="stop"),
    ])
    executed = []

    def execute(name, args):
        executed.append((name, args["expression"]))
        return _execute(name, args)

    decision, trace = ModelExecutionControlPlane().run(
        envelope_factory=_factory,
        transport=transport,
        execute_tool=execute,
    )
    assert decision.kind == DecisionKind.ANSWER
    assert executed == [("calculate", "2+2"), ("calculate", "3+3")]
    assert len(trace.tool_runs) == 2


def test_nonretryable_provider_failure_uses_remaining_grounded_fallback():
    transport = SequencedTransport([
        AssembledModelResponse(tool_calls=[{
            "id": "sports-call", "name": "sports_live", "arguments": {"query": "FIFA next match"}, "index": 0,
        }], finish_reason="tool_calls"),
        AssembledModelResponse(tool_calls=[{
            "id": "web-call", "name": "web_search", "arguments": {"query": "FIFA World Cup next match"}, "index": 0,
        }], finish_reason="tool_calls"),
        AssembledModelResponse(content="The grounded result is available.", finish_reason="stop"),
    ])

    def factory(outcomes):
        return ModelTurnEnvelopeCompiler().compile(
            project_id="",
            session_id="session-1",
            turn_id="execution-1",
            execution_id="execution-1",
            request_id="request-1",
            provider="lmstudio",
            model_id="Qwen3.5-9B",
            assistant_identity=IDENTITY,
            objective="Find the next FIFA match",
            task_status="in_progress",
            current_plan_step=None,
            collected_inputs={},
            missing_inputs=[],
            latest_user_relation="new_work",
            latest_user_message="Find the next FIFA match",
            allowed_tools=[SPORTS_TOOL, WEB_TOOL],
            tool_use_policy=ToolUsePolicy.REQUIRED,
            relevant_memory=[],
            approval=None,
            tool_outcomes=outcomes,
        )

    def execute(name, _args):
        return ToolOutcome(
            tool_name=name,
            execution_id="execution-1",
            turn_id="execution-1",
            session_id="session-1",
            success=True,
            status="success",
            result_state="provider_unavailable" if name == "sports_live" else "data_found",
            output="provider unavailable" if name == "sports_live" else "grounded search result",
            retryable=False,
            verification={"verified": True, "runtime_boundary": "test", "verified_at": time.time()},
        )

    decision, trace = ModelExecutionControlPlane().run(
        envelope_factory=factory,
        transport=transport,
        execute_tool=execute,
    )
    assert decision.kind == DecisionKind.ANSWER
    assert [item["tool"] for item in trace.decisions[:2]] == ["sports_live", "web_search"]
    assert transport.calls[0]["tool_choice"] == "required"
    assert transport.calls[1]["tool_choice"] == "required"
    second_tool_names = {
        item["function"]["name"] for item in transport.calls[1]["tools"]
    }
    assert second_tool_names == {"web_search"}


def test_truncated_answer_is_blocked_even_after_verified_tool_work():
    transport = SequencedTransport([
        AssembledModelResponse(tool_calls=[{
            "id": "call-1", "name": "calculate", "arguments": {"expression": "2+2"}, "index": 0,
        }], finish_reason="tool_calls"),
        AssembledModelResponse(content="The verified result is", finish_reason="length"),
        AssembledModelResponse(content="The verified result is still incomplete", finish_reason="length"),
        AssembledModelResponse(content="The verified result remains incomplete", finish_reason="length"),
    ])
    decision, trace = ModelExecutionControlPlane().run(
        envelope_factory=_factory,
        transport=transport,
        execute_tool=_execute,
    )
    assert decision.kind == DecisionKind.BLOCK
    assert decision.reason_code == "model_finish_incomplete"
    assert trace.terminal_status == "block"


def test_cancel_stops_before_transport_call():
    transport = SequencedTransport([])
    decision, trace = ModelExecutionControlPlane().run(
        envelope_factory=_factory,
        transport=transport,
        execute_tool=_execute,
        cancel=lambda: True,
    )
    assert decision.kind == DecisionKind.CANCEL
    assert trace.terminal_status == "cancelled"
    assert transport.calls == []


def test_transport_cancellation_becomes_typed_cancel_decision():
    transport = SequencedTransport([
        AssembledModelResponse(finish_reason="cancelled"),
    ])
    decision, trace = ModelExecutionControlPlane().run(
        envelope_factory=_factory,
        transport=transport,
        execute_tool=_execute,
    )
    assert decision.kind == DecisionKind.CANCEL
    assert decision.reason_code == "model_request_cancelled"
    assert trace.terminal_status == "cancel"


def test_langchain_stream_transport_uses_family_argument_assembler():
    model = FakeStreamingChatModel([
        FakeChunk(reasoning="private", calls=[{
            "index": 0, "id": "call-1", "name": "calculate", "args": '{"exp',
        }]),
        FakeChunk(calls=[{
            "index": 0, "id": None, "name": None, "args": 'ression":"2+2"}',
        }], finish="tool_calls"),
    ])
    parsed = LangChainStreamingTransport(model).complete(
        messages=[
            {"role": "system", "content": "contract"},
            {"role": "user", "content": "calculate"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "prior-call",
                    "type": "function",
                    "function": {"name": "calculate", "arguments": '{"expression":"1+1"}'},
                }],
            },
            {"role": "tool", "tool_call_id": "prior-call", "name": "calculate", "content": "verified 2"},
        ],
        tools=[{"type": "function", "function": {"name": "calculate", "parameters": {"type": "object"}}}],
        tool_choice="required",
        adapter=QwenAdapter(),
    )
    assert model.bound[1] == "required"
    assert [item.type for item in model.messages] == ["system", "human", "ai", "tool"]
    assert parsed.reasoning == "private"
    assert parsed.tool_calls[0].arguments == {"expression": "2+2"}
    assert parsed.finish_reason == "tool_calls"


def test_langchain_stream_transport_fails_on_meaningful_progress_idle_timeout():
    started = time.monotonic()
    with pytest.raises(ModelStreamIdleTimeout) as caught:
        LangChainStreamingTransport(
            StallingStreamingChatModel(),
            stream_idle_timeout_seconds=0.05,
            poll_interval_seconds=0.01,
        ).complete(
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            tool_choice="auto",
            adapter=QwenAdapter(),
        )
    assert time.monotonic() - started < 0.5
    assert caught.value.event_count == 0
    assert caught.value.progress_chars == 0


def test_partial_provider_stream_is_not_retried():
    transport = PartialFailureTransport()
    diagnostics = []
    with pytest.raises(ModelProviderError):
        ModelExecutionControlPlane(provider_retries=1).run(
            envelope_factory=_factory,
            transport=transport,
            execute_tool=_execute,
            diagnostic_sink=diagnostics.append,
        )
    assert transport.calls == 1
    retry_event = next(
        item for item in diagnostics if item.get("event") == "provider_retry"
    )
    assert retry_event["retrying"] is False
    assert retry_event["partial_stream"] is True
    assert retry_event["reason_code"] == "partial_stream_failed"
