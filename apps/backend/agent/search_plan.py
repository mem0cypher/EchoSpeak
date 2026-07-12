"""Structured Echo Search planning.

The search workflow consumes a SearchPlan, never a raw user message.  In
production the plan should come from the selected model as JSON; tests can pass
a stub model that returns the same schema.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Literal


class SearchMode(str, Enum):
    QUICK_SEARCH = "QUICK_SEARCH"
    COMPARE_SEARCH = "COMPARE_SEARCH"
    DEEP_SEARCH = "DEEP_SEARCH"
    LOCAL_FIRST_SEARCH = "LOCAL_FIRST_SEARCH"


SourceType = Literal["docs", "github", "news", "pricing", "forum", "official", "academic", "general"]


@dataclass(frozen=True)
class PlannedQuery:
    query: str
    purpose: str = ""
    expected_source_type: SourceType = "general"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlannedQuery":
        source = str(data.get("expected_source_type") or "general").strip().lower()
        allowed = {"docs", "github", "news", "pricing", "forum", "official", "academic", "general"}
        if source not in allowed:
            source = "general"
        return cls(
            query=normalize_query(data.get("query", "")),
            purpose=str(data.get("purpose") or "").strip(),
            expected_source_type=source,  # type: ignore[arg-type]
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "purpose": self.purpose,
            "expected_source_type": self.expected_source_type,
        }


@dataclass(frozen=True)
class SearchPlan:
    mode: SearchMode
    user_intent: str
    must_answer: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    recency_required: bool = False
    queries: list[PlannedQuery] = field(default_factory=list)
    stop_condition: str = ""
    risk_notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchPlan":
        mode = SearchMode(str(data.get("mode") or SearchMode.QUICK_SEARCH.value).strip().upper())
        queries = [PlannedQuery.from_dict(q) for q in data.get("queries") or [] if isinstance(q, dict)]
        plan = cls(
            mode=mode,
            user_intent=str(data.get("user_intent") or "").strip(),
            must_answer=[str(x).strip() for x in data.get("must_answer") or [] if str(x).strip()],
            entities=[str(x).strip() for x in data.get("entities") or [] if str(x).strip()],
            recency_required=bool(data.get("recency_required")),
            queries=[q for q in queries if q.query],
            stop_condition=str(data.get("stop_condition") or "").strip(),
            risk_notes=[str(x).strip() for x in data.get("risk_notes") or [] if str(x).strip()],
        )
        return clamp_plan(plan)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "user_intent": self.user_intent,
            "must_answer": list(self.must_answer),
            "entities": list(self.entities),
            "recency_required": self.recency_required,
            "queries": [q.as_dict() for q in self.queries],
            "stop_condition": self.stop_condition,
            "risk_notes": list(self.risk_notes),
        }


def normalize_query(value: Any) -> str:
    raw = str(value or "").strip()
    try:
        from agent.research import normalize_web_search_query

        text = normalize_web_search_query(raw) or raw
    except Exception:
        text = raw
    text = re.sub(r"\s+", " ", text.strip())
    return text[:240]


def dedupe_planned_queries(queries: Iterable[PlannedQuery]) -> list[PlannedQuery]:
    out: list[PlannedQuery] = []
    seen = set()
    for query in queries:
        key = query.query.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(query)
    return out


def clamp_plan(plan: SearchPlan) -> SearchPlan:
    limits = {
        SearchMode.QUICK_SEARCH: 2,
        SearchMode.COMPARE_SEARCH: 8,
        SearchMode.DEEP_SEARCH: 8,
        SearchMode.LOCAL_FIRST_SEARCH: 4,
    }
    queries = dedupe_planned_queries(plan.queries)[: limits[plan.mode]]
    if plan.mode == SearchMode.COMPARE_SEARCH and len(queries) < 4 and queries:
        base = queries[0].query
        additions = [
            PlannedQuery(f"{base} official", "Find official source", "official"),
            PlannedQuery(f"{base} comparison", "Find comparison evidence", "general"),
            PlannedQuery(f"{base} recent", "Find recent source", "news" if plan.recency_required else "general"),
        ]
        queries = dedupe_planned_queries([*queries, *additions])[:8]
    return SearchPlan(
        mode=plan.mode,
        user_intent=plan.user_intent,
        must_answer=plan.must_answer,
        entities=plan.entities,
        recency_required=plan.recency_required,
        queries=queries,
        stop_condition=plan.stop_condition,
        risk_notes=plan.risk_notes,
    )


def extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("SearchPlan JSON must be an object")
    return data


SEARCH_PLAN_PROMPT = """Return only valid JSON matching this SearchPlan schema:
{
  "mode": "QUICK_SEARCH | COMPARE_SEARCH | DEEP_SEARCH | LOCAL_FIRST_SEARCH",
  "user_intent": "string",
  "must_answer": ["string"],
  "entities": ["string"],
  "recency_required": true,
  "queries": [
    {
      "query": "string",
      "purpose": "string",
      "expected_source_type": "docs | github | news | pricing | forum | official | academic | general"
    }
  ],
  "stop_condition": "string",
  "risk_notes": ["string"]
}

Rules:
- Do not copy the user request as a single raw query.
- QUICK_SEARCH uses 1-2 focused queries.
- COMPARE_SEARCH uses 4-8 queries with different source angles.
- DEEP_SEARCH uses up to 8 queries for the first round.
- LOCAL_FIRST_SEARCH starts with docs/code queries.
- Prefer official sources for API/tool claims.
- Prefer recent sources for pricing, current APIs, frameworks, and news.
"""


def build_search_plan(user_request: str, llm: Callable[[str], str] | Any) -> SearchPlan:
    prompt = f"{SEARCH_PLAN_PROMPT}\nUser request:\n{user_request}"
    if hasattr(llm, "invoke"):
        response = llm.invoke(prompt)
    else:
        response = llm(prompt)
    data = extract_json_object(str(response))
    return SearchPlan.from_dict(data)
