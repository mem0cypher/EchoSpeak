"""Permanent regressions for adversarial stabilization (s01/s13/s19 class defects).

Covers:
- arithmetic must not route as web research
- multi-intent remember + convert/calculate residual split
- residual command text never enters durable memory text
- calc expression extraction for pure math and F→C conversion
"""
from __future__ import annotations

import re

import pytest

from agent.memory_curator import MemoryCurator, is_search_retry_like
from agent.mode_controller import (
    TurnMode,
    allowed_tools_for_mode,
    classify_turn_mode,
    _is_utility_tool_request,
    _is_checkable_task,
)


# ── Mode / routing (s19 class) ───────────────────────────────────────────────


def test_pure_arithmetic_is_utility_not_research():
    text = "What is 17 * 19? Reply with the number only if possible."
    assert _is_utility_tool_request(text)
    assert not _is_checkable_task(text)
    decision = classify_turn_mode(text)
    assert decision.mode == TurnMode.CHAT
    assert "utility" in (decision.reason or "")
    names = ["web_search", "calculate", "get_system_time", "system_info", "sports_live"]
    allowed = allowed_tools_for_mode(decision, names)
    assert "calculate" in allowed
    assert "web_search" not in allowed


def test_bare_product_expression_is_utility():
    decision = classify_turn_mode("17 * 19")
    assert decision.mode == TurnMode.CHAT
    assert "calculate" in allowed_tools_for_mode(
        decision, ["web_search", "calculate", "get_system_time"]
    )


def test_live_price_still_research_not_utility():
    text = "What is the current bitcoin price USD?"
    assert not _is_utility_tool_request(text)
    decision = classify_turn_mode(text)
    assert decision.mode == TurnMode.TASK_RESEARCH


# ── Multi-intent memory split (s01 / s13 class) ──────────────────────────────


def test_remember_plus_convert_splits_residual():
    text = (
        "Please remember that I prefer temperatures in Celsius, and also "
        "convert 98.6 Fahrenheit to Celsius for me right now."
    )
    payload = MemoryCurator.extract_explicit_payload(text)
    span, residual = MemoryCurator.split_memory_and_residual(text)
    assert "celsius" in payload.casefold()
    assert "convert" not in payload.casefold()
    assert "98.6" not in payload
    assert "convert" in residual.casefold()
    assert "98.6" in residual


def test_winnipeg_remember_excludes_retry_command():
    text = "I'm from Winnipeg, remember that, and retry that search"
    payload = MemoryCurator.extract_explicit_payload(text)
    span, residual = MemoryCurator.split_memory_and_residual(text)
    assert "winnipeg" in payload.casefold()
    assert "retry" not in payload.casefold()
    assert "search" not in payload.casefold()
    assert is_search_retry_like(residual)
    assert "winnipeg" not in residual.casefold()


def test_curate_winnipeg_does_not_store_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOSPEAK_DATA_DIR", str(tmp_path / "mem"))
    from agent.memory import AgentMemory

    memory = AgentMemory(memory_path=str(tmp_path / "memory"))
    memory.use_faiss = False
    memory.embeddings = None
    memory.vector_store = None
    memory.simple_memory = []
    curator = MemoryCurator(memory, llm_invoke=None)
    result = curator.curate_and_persist(
        user_text="I'm from Winnipeg, remember that, and retry that search",
        explicit=True,
        session_id="s",
        execution_id="e",
        allow_implicit_auto=True,
    )
    assert result.persisted_ids, result.errors
    rec = memory._records[result.persisted_ids[0]]
    text = str(rec.get("text") or "")
    meta = dict(rec.get("metadata") or {})
    source = str(meta.get("source_text") or "")
    assert "Winnipeg" in text or "winnipeg" in text.casefold()
    assert "retry" not in text.casefold()
    assert "retry that search" not in source.casefold()


def test_validate_rejects_pure_command_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHOSPEAK_DATA_DIR", str(tmp_path / "mem2"))
    from agent.memory import AgentMemory
    from agent.memory_curator import MemoryCandidate

    memory = AgentMemory(memory_path=str(tmp_path / "memory"))
    memory.use_faiss = False
    memory.embeddings = None
    curator = MemoryCurator(memory, llm_invoke=None)
    cand = MemoryCandidate(
        text="retry that search",
        type="note",
        scope="account",
        explicit=True,
        confidence=1.0,
        importance=0.9,
    )
    out = curator.validate_candidate(cand, allow_implicit_auto=True)
    assert out.action == "ignore"


# ── Calc expression extraction (s01 / s19 class) ─────────────────────────────


def test_extract_calc_expression_product_and_fahrenheit():
    from agent.core import EchoSpeakAgent

    agent = object.__new__(EchoSpeakAgent)
    assert agent._extract_calc_expression("What is 17 * 19? Reply with the number only.") in {
        "17*19",
        "17 * 19",
    }
    # Normalize spaces
    expr = re.sub(r"\s+", "", agent._extract_calc_expression("What is 17 * 19?"))
    assert expr == "17*19"
    f_expr = agent._extract_calc_expression(
        "convert 98.6 Fahrenheit to Celsius for me right now"
    )
    assert "98.6" in f_expr and "32" in f_expr and "5" in f_expr
    from agent.core import _is_valid_math_expression
    from agent.tools import calculate

    assert _is_valid_math_expression(re.sub(r"\s+", "", expr) if " " in expr else expr)
    out = calculate.invoke({"expression": "17*19"})
    assert str(out).strip() == "323"
    c_out = calculate.invoke({"expression": f_expr})
    val = float(str(c_out).strip())
    assert 36.5 <= val <= 37.5


def test_celsius_preference_payload_excludes_conversion_command():
    payload = MemoryCurator.extract_explicit_payload(
        "remember that I prefer temperatures in Celsius, and also convert 98.6 F to C"
    )
    assert "prefer" in payload.casefold() or "celsius" in payload.casefold()
    assert "98.6" not in payload
    assert "convert" not in payload.casefold()


def test_search_retry_utterance_not_failed_action_retry_gate():
    """Search retries must not be swallowed by failed-ToolRun retry_target path."""
    from agent.mode_controller import is_search_retry_utterance

    text = "I'm from Winnipeg, remember that, and retry that search"
    decision = classify_turn_mode(text)
    assert decision.intent_relation == "retry" or is_search_retry_utterance(text)
    # Memory multi-intent must still extract the city
    assert "winnipeg" in MemoryCurator.extract_explicit_payload(text).casefold()
    # Bare residual remains a search retry, not a calc utility
    residual = MemoryCurator.split_memory_and_residual(text)[1]
    assert is_search_retry_utterance(residual)
