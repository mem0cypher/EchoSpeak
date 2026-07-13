"""Regression: conversational 'Forget X for a moment' must not hit memory delete."""

from __future__ import annotations

import re


def _is_memory_forget_payload(query: str) -> bool:
    """Mirror the guard in agent.core process_query forget routing."""
    forget_match = re.match(r"(?i)^\s*(?:please\s+)?forget\s+(.+)$", query.strip())
    if not forget_match:
        return False
    forget_payload = str(forget_match.group(1) or "").strip()
    topic_switch = bool(
        re.search(
            r"(?i)\bfor\s+(a\s+)?moment\b|\bfor\s+now\b|\babout\s+that\b|"
            r"\band\s+(recommend|tell|explain|help|show|give)\b|"
            r"\b—\b|\s+-\s+|\.\s+\S",
            forget_payload,
        )
    )
    memory_like = bool(
        re.search(
            r"(?i)\b(my|that|the|this)\b|"
            r"\b(preference|memory|memories|note|notes|fact)\b|"
            r"\b(i\s+(like|prefer|said|told)|that\s+i)\b",
            forget_payload,
        )
    )
    if topic_switch or not memory_like:
        return False
    return True


def test_topic_switch_not_memory_forget():
    assert not _is_memory_forget_payload(
        "Forget coding for a moment — recommend a good stretch after desk work."
    )
    assert not _is_memory_forget_payload("Forget about that for now and help me plan dinner.")
    assert not _is_memory_forget_payload("forget it")
    assert not _is_memory_forget_payload("Please forget coding and explain recursion.")


def test_explicit_memory_forget_still_matches():
    assert _is_memory_forget_payload("Forget my hockey-team preference.")
    assert _is_memory_forget_payload("Please forget my preference about concise prompts.")
    assert _is_memory_forget_payload("forget that I like Calgary")
    assert _is_memory_forget_payload("Forget the memory about Edmonton.")
