"""Bounded typed context selection without creating another state owner."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent.context_budget import compress_text, estimate_tokens


ContextScope = Literal["account", "project", "session", "turn", "resource"]
ContextLifecycle = Literal[
    "active", "pending", "completed", "cancelled", "superseded", "forgotten", "expired", "stale"
]
ContextTrust = Literal["authoritative", "verified", "user", "untrusted", "model"]


class ContextItem(BaseModel):
    id: str
    source_type: str
    text: str
    project_id: str = ""
    session_id: str = ""
    turn_id: str = ""
    source_id: str = ""
    source_revision: str = ""
    timestamp: float = Field(default_factory=time.time)
    relevance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0.5, ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    scope: ContextScope = "session"
    lifetime: str = "turn"
    lifecycle: ContextLifecycle = "active"
    trust: ContextTrust = "untrusted"
    verified: bool = False
    explicit_reference: bool = False
    token_estimate: int = 0
    provenance: dict[str, str] = Field(default_factory=dict)


class ContextSelection(BaseModel):
    selected: list[ContextItem] = Field(default_factory=list)
    excluded: list[dict[str, str]] = Field(default_factory=list)
    used_tokens: int = 0
    token_budget: int = 0

    def redacted_manifest(self) -> dict[str, Any]:
        return {
            "used_tokens": self.used_tokens,
            "token_budget": self.token_budget,
            "selected": [
                {
                    "id": item.id,
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "scope": item.scope,
                    "lifecycle": item.lifecycle,
                    "trust": item.trust,
                    "verified": item.verified,
                    "tokens": item.token_estimate or estimate_tokens(item.text),
                    "sha256": hashlib.sha256(item.text.encode("utf-8", errors="ignore")).hexdigest(),
                    "provenance": dict(item.provenance),
                }
                for item in self.selected
            ],
            "excluded": list(self.excluded),
            "content_omitted": True,
        }


_BUCKET_ORDER = {
    "current_turn": 0,
    "session_state": 1,
    "pending_work": 2,
    "tool_outcome": 3,
    "approval": 4,
    "job": 5,
    "artifact": 6,
    "document": 7,
    "memory": 8,
    "recent_turn": 9,
    "conversation_summary": 10,
    "skill": 11,
    "capability": 12,
    "authority": 13,
}


class ContextAssembler:
    """Pure selector. It reads candidates but owns no durable state."""

    def __init__(self, *, project_id: str, session_id: str, turn_id: str = "") -> None:
        self.project_id = str(project_id or "")
        self.session_id = str(session_id or "")
        self.turn_id = str(turn_id or "")

    def _exclusion_reason(self, item: ContextItem) -> str:
        if item.lifecycle in {"cancelled", "superseded", "forgotten", "expired", "stale"}:
            return f"lifecycle:{item.lifecycle}"
        if item.scope == "project" and (not self.project_id or item.project_id != self.project_id):
            return "project_scope"
        if item.scope in {"session", "turn"} and item.session_id and item.session_id != self.session_id:
            return "session_scope"
        if item.scope == "turn" and item.turn_id and self.turn_id and item.turn_id != self.turn_id:
            return "turn_scope"
        if not str(item.text or "").strip():
            return "empty"
        return ""

    @staticmethod
    def _score(item: ContextItem) -> float:
        age_hours = max(0.0, (time.time() - float(item.timestamp or 0)) / 3600.0)
        recency = 1.0 / (1.0 + age_hours / 24.0)
        authority = 1.0 if item.trust == "authoritative" else 0.8 if item.verified else 0.0
        return (
            (2.0 if item.explicit_reference else 0.0)
            + item.relevance * 1.4
            + item.importance
            + item.confidence * 0.6
            + recency * 0.5
            + authority
        )

    def select(self, candidates: list[ContextItem], *, token_budget: int) -> ContextSelection:
        budget = max(0, int(token_budget or 0))
        eligible: list[ContextItem] = []
        excluded: list[dict[str, str]] = []
        seen_content: set[str] = set()
        for item in candidates:
            reason = self._exclusion_reason(item)
            digest = hashlib.sha256(str(item.text or "").strip().encode("utf-8", errors="ignore")).hexdigest()
            if not reason and digest in seen_content:
                reason = "duplicate_content"
            if reason:
                excluded.append({"id": item.id, "reason": reason})
            else:
                eligible.append(item)
                seen_content.add(digest)
        eligible.sort(key=lambda item: (_BUCKET_ORDER.get(item.source_type, 50), -self._score(item), -item.timestamp))

        selected: list[ContextItem] = []
        used = 0
        for item in eligible:
            need = item.token_estimate or estimate_tokens(item.text)
            remaining = budget - used
            if remaining <= 0:
                excluded.append({"id": item.id, "reason": "budget"})
                continue
            chosen = item
            if need > remaining:
                if remaining < 24:
                    excluded.append({"id": item.id, "reason": "budget"})
                    continue
                clipped = compress_text(item.text, remaining * 4, label=item.source_type)
                chosen = item.model_copy(update={"text": clipped, "token_estimate": estimate_tokens(clipped)})
                need = chosen.token_estimate
            else:
                chosen = item.model_copy(update={"token_estimate": need})
            selected.append(chosen)
            used += need
        return ContextSelection(selected=selected, excluded=excluded, used_tokens=used, token_budget=budget)
