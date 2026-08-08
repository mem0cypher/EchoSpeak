from __future__ import annotations

import json

from agent.model_adapters import GemmaAdapter, QwenAdapter, detect_model_family, get_family_adapter
from agent.model_contracts import DecisionKind, ToolOutcome


def test_family_detection_and_explicit_templates():
    assert detect_model_family("Qwen3.5-9B-GGUF").value == "qwen"
    assert detect_model_family("gemma-4-E2B-it").value == "gemma"
    assert get_family_adapter("Qwen3.5-9B").template == "qwen-model-metadata"
    assert get_family_adapter("gemma-4-E2B").template == "gemma"


def test_qwen_native_tool_call_and_reasoning_are_separated():
    adapter = QwenAdapter()
    parsed = adapter.parse_response({
        "message": {
            "content": "<think>private scratch</think>",
            "tool_calls": [{
                "id": "call-1",
                "function": {"name": "calculate", "arguments": '{"expression":"17*19"}'},
            }],
        },
        "finish_reason": "tool_calls",
    })
    assert parsed.content == ""
    assert parsed.reasoning == "private scratch"
    assert parsed.tool_calls[0].arguments == {"expression": "17*19"}
    assert adapter.decision_from_response(parsed).kind == DecisionKind.CALL_TOOL


def test_native_parallel_tool_calls_survive_the_adapter_boundary():
    adapter = QwenAdapter()
    parsed = adapter.parse_response({
        "message": {
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "function": {
                        "name": "calculate",
                        "arguments": '{"expression":"2+2"}',
                    },
                },
                {
                    "id": "call-2",
                    "function": {
                        "name": "calculate",
                        "arguments": '{"expression":"3+3"}',
                    },
                },
            ],
        },
        "finish_reason": "tool_calls",
    })
    decision = adapter.decision_from_response(parsed)
    assert decision.kind == DecisionKind.CALL_TOOL
    assert [item.id for item in decision.tool_calls] == ["call-1", "call-2"]
    assert decision.tool_call == decision.tool_calls[0]


def test_streamed_argument_fragments_reconstruct_without_reasoning_corruption():
    adapter = QwenAdapter()
    events = [
        {"choices": [{"delta": {"reasoning_content": "considering {not arguments}"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "function": {"name": "calculate", "arguments": '{"exp'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ression":"2+2"}'}}]}, "finish_reason": "tool_calls"}]},
    ]
    parsed = adapter.parse_stream(events)
    assert parsed.tool_calls[0].name == "calculate"
    assert parsed.tool_calls[0].arguments == {"expression": "2+2"}
    assert "not arguments" in parsed.reasoning


def test_malformed_streamed_arguments_fail_closed():
    parsed = QwenAdapter().parse_stream([
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "calculate", "arguments": "{"}}]}}]},
    ])
    decision = QwenAdapter().decision_from_response(parsed)
    assert decision.kind == DecisionKind.BLOCK
    assert decision.reason_code == "malformed_tool_call"


def test_streamed_typed_non_answer_decision_survives_wrapper_stripping():
    parsed = QwenAdapter().parse_stream([
        {"choices": [{"delta": {"content": '<agent_decision>{"kind":"block","message":"No authority","reason_code":"not_authorized"}'}}]},
        {"choices": [{"delta": {"content": "</agent_decision>"}, "finish_reason": "stop"}]},
    ])
    decision = QwenAdapter().decision_from_response(parsed)
    assert parsed.content == ""
    assert decision.kind == DecisionKind.BLOCK
    assert decision.message == "No authority"
    assert decision.reason_code == "not_authorized"


def test_malformed_typed_decision_has_specific_fail_closed_diagnostic():
    parsed = QwenAdapter().parse_response(
        '<agent_decision>{"kind":"block","message":}</agent_decision>'
    )
    decision = QwenAdapter().decision_from_response(parsed)
    assert decision.kind == DecisionKind.BLOCK
    assert decision.reason_code == "malformed_agent_decision"


def test_gemma_function_sentinel_is_normalized():
    parsed = GemmaAdapter().parse_response(
        '<think>scratch</think><start_of_function_call>{"name":"combine_values","arguments":{"left":"a","right":"b"}}</end_of_function_call>'
    )
    assert parsed.content == ""
    assert parsed.reasoning == "scratch"
    assert parsed.tool_calls[0].name == "combine_values"
    assert parsed.tool_calls[0].arguments == {"left": "a", "right": "b"}


def test_tool_outcome_reinjection_preserves_verified_structure():
    outcome = ToolOutcome(
        tool_name="calculate",
        run_id="run-1",
        execution_id="execution-1",
        success=True,
        status="success",
        output="4",
        verification={"runtime_boundary": "test"},
    )
    message = QwenAdapter().format_tool_outcome(outcome, "call-1")
    payload = json.loads(message["content"])
    assert message["role"] == "tool"
    assert message["tool_call_id"] == "call-1"
    assert payload["run_id"] == "run-1"
    assert payload["verification"] == {"runtime_boundary": "test"}


def test_resolve_reasoning_effort_mapping_and_honesty():
    from agent.model_adapters import resolve_reasoning_effort

    # OpenAI o1/o3 reasoning model
    result_o1 = resolve_reasoning_effort("high", "openai", "o1-mini")
    assert result_o1["reasoning_effort"] == "high"
    assert result_o1["native_support"] is True

    # Generic model without native effort parameter
    result_gemma = resolve_reasoning_effort("max", "gemma", "gemma-2-9b")
    assert result_gemma["native_support"] is False
    assert result_gemma["reasoning_effort"] is None
    assert result_gemma["budget_tokens"] == 32768
