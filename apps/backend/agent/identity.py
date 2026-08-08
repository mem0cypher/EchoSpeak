"""Canonical bounded projection of EchoSpeak's authoritative Soul identity."""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field


IDENTITY_PROJECTION_VERSION = "8.0.0"
_MAX_SOUL_CHARS = 3200
_PREFERRED_SECTIONS = (
    "identity",
    "personality",
    "voice",
    "thinking and judgment",
    "memory and continuity",
    "capabilities and tools",
    "response standard",
)


class EchoIdentityProjection(BaseModel):
    """Model-facing identity facts compiled from, but not replacing, SOUL.md."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    projection_version: str = IDENTITY_PROJECTION_VERSION
    assistant_name: str = "Echo"
    product_name: str = "EchoSpeak"
    provider: str = ""
    model_id: str = ""
    model_role: str = "reasoning_engine"
    soul_sha256: str
    soul_rules: str = Field(max_length=_MAX_SOUL_CHARS)

    def render(self) -> str:
        engine = "/".join(part for part in (self.provider, self.model_id) if part) or "the selected model"
        return (
            "[ECHOSPEAK_ASSISTANT_IDENTITY]\n"
            "Your personal name is Echo. You are the assistant in the EchoSpeak system.\n"
            f"{engine} is only the current reasoning engine; its provider or model name is not your personal identity.\n"
            "If directly asked about the underlying engine, distinguish it from Echo (for example: "
            "'My name is Echo. I am currently running through Gemma.').\n"
            "Runtime authority, verified outcomes, and safety policy remain controlling.\n"
            f"Authoritative Soul projection (sha256={self.soul_sha256}):\n{self.soul_rules}\n"
            "[/ECHOSPEAK_ASSISTANT_IDENTITY]"
        )


def compile_echo_identity(soul_text: str, *, provider: str, model_id: str) -> EchoIdentityProjection:
    """Compile a deterministic, bounded projection while retaining Soul provenance."""

    authoritative = str(soul_text or "").strip()
    soul_hash = hashlib.sha256(authoritative.encode("utf-8")).hexdigest()
    return _compile_cached(authoritative, soul_hash, str(provider or ""), str(model_id or ""))


@lru_cache(maxsize=32)
def _compile_cached(
    authoritative: str,
    soul_hash: str,
    provider: str,
    model_id: str,
) -> EchoIdentityProjection:
    rules = _bounded_soul_rules(authoritative)
    if not rules:
        rules = (
            "Echo is the assistant identity. Be concise, direct, technically honest, and never claim "
            "memory, access, tool use, or completion without runtime-provided evidence."
        )
    return EchoIdentityProjection(
        provider=provider,
        model_id=model_id,
        soul_sha256=soul_hash,
        soul_rules=rules,
    )


def _bounded_soul_rules(text: str) -> str:
    if not text:
        return ""
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            current = match.group(1).strip().casefold()
            sections.setdefault(current, [])
            continue
        if current:
            sections.setdefault(current, []).append(line.rstrip())
    selected: list[tuple[str, str]] = []
    for name in _PREFERRED_SECTIONS:
        body = "\n".join(sections.get(name, [])).strip()
        if body:
            selected.append((name, body))
    if not selected:
        return _truncate_section_body(text.strip(), _MAX_SOUL_CHARS)

    separators = max(0, len(selected) - 1) * 2
    header_chars = sum(len(f"## {name.title()}\n") for name, _ in selected)
    body_budget = max(0, _MAX_SOUL_CHARS - separators - header_chars)
    minimum = 220
    allocations = [min(len(body), minimum) for _, body in selected]
    remaining = max(0, body_budget - sum(allocations))
    weights = {
        "identity": 2,
        "personality": 1,
        "voice": 1,
        "thinking and judgment": 2,
        "memory and continuity": 3,
        "capabilities and tools": 3,
        "response standard": 3,
    }
    while remaining > 0:
        eligible = [
            index for index, (_, body) in enumerate(selected)
            if allocations[index] < len(body)
        ]
        if not eligible:
            break
        total_weight = sum(weights.get(selected[index][0], 1) for index in eligible)
        progressed = False
        for index in eligible:
            share = max(
                1,
                remaining * weights.get(selected[index][0], 1) // total_weight,
            )
            addition = min(
                share,
                len(selected[index][1]) - allocations[index],
                remaining,
            )
            if addition:
                allocations[index] += addition
                remaining -= addition
                progressed = True
            if remaining <= 0:
                break
        if not progressed:
            break

    rendered = [
        f"## {name.title()}\n{_truncate_section_body(body, allocations[index])}"
        for index, (name, body) in enumerate(selected)
    ]
    return "\n\n".join(rendered).strip()


def _truncate_section_body(text: str, budget: int) -> str:
    """Bound one Soul section at a readable line/sentence boundary."""

    value = str(text or "").strip()
    if len(value) <= budget:
        return value
    if budget <= 1:
        return value[:budget]
    candidate = value[: budget - 1].rstrip()
    floor = max(1, int((budget - 1) * 0.6))
    boundaries = [
        candidate.rfind("\n", floor),
        candidate.rfind(". ", floor),
        candidate.rfind("; ", floor),
        candidate.rfind(" ", floor),
    ]
    boundary = max(boundaries)
    if boundary >= floor:
        candidate = candidate[: boundary + (1 if candidate[boundary:boundary + 1] == "." else 0)]
    return candidate.rstrip() + "…"


__all__ = ["EchoIdentityProjection", "compile_echo_identity"]
