"""Provider-neutral contracts for retrieval planning and outcome usefulness.

This module performs no network I/O.  It is the single deterministic boundary
between a semantic request and provider query strings/result-state projection.
"""
from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RetrievalDomain(str, Enum):
    GENERAL = "general"
    SPORTS = "sports"
    FLIGHTS = "flights"
    FINANCE = "finance"
    GAMING = "gaming"
    SOCIAL_METRIC = "social_metric"


class ResultState(str, Enum):
    DATA_FOUND = "data_found"
    VERIFIED_ABSENCE = "verified_absence"
    NO_DATA = "no_data"
    UNSUPPORTED_INTENT = "unsupported_intent"
    AMBIGUOUS_ENTITY = "ambiguous_entity"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    STALE_DATA = "stale_data"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


_INTERNAL_LABEL = re.compile(
    r"(?i)(?:task\s*objective|latest\s*user\s*message|collected\s*structured\s*fields|"
    r"requested\s*operation|completion\s*requirement|turn_understanding_envelope|"
    r"echospeak_model_turn_envelope)\s*[:=]"
)
_INTERNAL_JSON_SEAM = re.compile(
    r"(?i)[\"']?(?:latest_user_message|task_objective|requested_operation|"
    r"completion_requirements?|turn_understanding_envelope|"
    r"echospeak_model_turn_envelope)[\"']?\s*:"
)
_SPORTS_VOCAB = {"kickoff", "fixture", "matchday", "tipoff", "faceoff"}
_FLIGHT_VOCAB = {"flight", "airport", "airline", "departure", "arrival", "fare", "gate", "terminal"}
_PRONOUN_ONLY = re.compile(r"(?i)^\s*(?:him|her|them|it|that|this|he|she|they)\s*$")
_QUERY_STOPWORDS = frozenset({
    "about", "after", "again", "answer", "before", "check", "current", "details",
    "find", "from", "have", "information", "latest", "look", "next", "please",
    "requested", "search", "show", "that", "their", "this", "what", "when", "where",
    "which", "with", "would",
})


class ResearchQueryPlan(BaseModel):
    """Canonical, bounded provider-query source."""

    model_config = ConfigDict(extra="forbid")
    domain: RetrievalDomain = RetrievalDomain.GENERAL
    intent: str = "lookup"
    subject: str
    resolved_entities: list[str] = Field(default_factory=list)
    exact_identifiers: list[str] = Field(default_factory=list)
    requested_facts: list[str] = Field(default_factory=list)
    time_window: str = ""
    location: str = ""
    source_preferences: list[str] = Field(default_factory=list)
    structured_capability_preference: str = ""
    requirement_id: str = ""
    attempt_id: str = ""
    raw_user_message: str = ""
    requirement_objective: str = ""
    proposed_query: str = ""
    objective_sha256: str = ""
    query_plan_id: str = ""

    @field_validator(
        "subject", "intent", "time_window", "location", "structured_capability_preference",
        "requirement_id", "attempt_id", "raw_user_message",
        "requirement_objective", "proposed_query", "objective_sha256",
        "query_plan_id",
    )
    @classmethod
    def bound_scalar(cls, value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()[:1000]

    @field_validator(
        "resolved_entities", "exact_identifiers", "requested_facts", "source_preferences"
    )
    @classmethod
    def bound_list(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(
            re.sub(r"\s+", " ", str(item or "")).strip()[:300]
            for item in value
            if str(item or "").strip()
        ))[:16]

    @model_validator(mode="after")
    def validate_subject(self) -> "ResearchQueryPlan":
        if not self.subject or _INTERNAL_LABEL.search(self.subject):
            raise ValueError("research subject is missing or contains an internal envelope label")
        if _PRONOUN_ONLY.fullmatch(self.subject) and not self.resolved_entities:
            raise ValueError("research subject contains an unresolved pronoun")
        if (
            re.search(r"(?i)\b(?:him|her|them|it)\b", self.subject)
            and not self.resolved_entities
            and not self.exact_identifiers
        ):
            raise ValueError("research subject contains a dangling unresolved pronoun")
        identity = {
            "domain": self.domain.value,
            "intent": self.intent,
            "subject": self.subject,
            "resolved_entities": self.resolved_entities,
            "exact_identifiers": self.exact_identifiers,
            "requested_facts": self.requested_facts,
            "time_window": self.time_window,
            "location": self.location,
            "source_preferences": self.source_preferences,
            "requirement_id": self.requirement_id,
            "attempt_id": self.attempt_id,
            "objective_sha256": self.objective_sha256,
        }
        self.query_plan_id = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return self

    def provider_query(self) -> str:
        bits: list[str] = []
        for identifier in self.exact_identifiers:
            bits.append(f'"{identifier.strip(chr(34))}"')
        bits.extend([self.subject, *self.resolved_entities, *self.requested_facts])
        if self.location:
            bits.append(self.location)
        if self.time_window:
            bits.append(self.time_window)
        bits.extend(self.source_preferences)
        unique_bits: list[str] = []
        for item in bits:
            value = str(item or "").strip()
            if not value:
                continue
            current = " ".join(unique_bits).casefold()
            if value.casefold() in current:
                continue
            unique_bits.append(value)
        query = re.sub(r"\s+", " ", " ".join(unique_bits)).strip()
        validate_provider_query(query, domain=self.domain, plan=self)
        return query


def infer_retrieval_domain(text: str) -> RetrievalDomain:
    low = str(text or "").casefold()
    if re.search(r"\b(flight|airport|airline|departure|arrival|airfare|gate)\b", low):
        return RetrievalDomain.FLIGHTS
    if re.search(r"\b(score|sports?|match|fixture|fifa|world cup|nhl|nba|nfl|mlb|standings)\b", low):
        return RetrievalDomain.SPORTS
    if re.search(r"\b(stock|ticker|share price|bitcoin|crypto|exchange rate)\b", low):
        return RetrievalDomain.FINANCE
    if re.search(r"\b(followers?|subscribers?|viewers?)\b", low) and re.search(
        r"\b(twitch|youtube|instagram|tiktok|twitter|x\.com)\b", low
    ):
        return RetrievalDomain.SOCIAL_METRIC
    if re.search(r"\b(game|gaming|steam|xbox|playstation|nintendo)\b", low):
        return RetrievalDomain.GAMING
    return RetrievalDomain.GENERAL


def plan_research_query(
    text: str,
    *,
    resolved_entities: Optional[list[str]] = None,
    domain: Optional[RetrievalDomain] = None,
    requirement_id: str = "",
    attempt_id: str = "",
    objective: str = "",
    raw_user_message: str = "",
) -> ResearchQueryPlan:
    """Build a conservative plan from user-authored or already-resolved text.

    Internal envelope labels are rejected rather than converted into search
    text. The caller supplies the proposed query, current user message, and
    requirement objective as separate fields.
    """

    raw = str(text or "").strip()
    if _INTERNAL_LABEL.search(raw) or _INTERNAL_JSON_SEAM.search(raw):
        raise ValueError(
            "research input contains internal execution-envelope material"
        )
    raw = re.sub(r"\s+", " ", raw)
    chosen = domain or infer_retrieval_domain(raw)
    if chosen == RetrievalDomain.FLIGHTS:
        # Domain isolation: legacy sports enrichers cannot survive into a flight plan.
        raw = re.sub(r"(?i)\b(kickoff|fixture|matchday|tipoff|faceoff)\b", " ", raw)
        raw = re.sub(r"\s+", " ", raw).strip()
    exact = re.findall(r"(?<![\w@])@?([A-Za-z0-9_]{3,32})(?![\w])", raw)
    exact_identifiers: list[str] = []
    if re.search(r"(?i)\b(username|account|profile|twitch|github|x\.com|twitter)\b", raw):
        stop = {
            "username", "account", "profile", "twitch", "github", "twitter",
            "search", "look", "find", "up", "on", "for", "about", "him", "her",
            "how", "many", "followers", "follower", "does", "have", "has",
            "channel", "public", "social", "platform", "streaming", "user",
        }
        exact_identifiers = [item for item in exact if item.casefold() not in stop][-2:]
    requested: list[str] = []
    for pattern, fact in (
        (r"(?i)\bfollowers?\b", "follower count"),
        (r"(?i)\b(status|delayed|cancelled|gate)\b", "current status"),
        (r"(?i)\b(cheapest|price|fare|cost)\b", "price"),
        (r"(?i)\b(next|schedule|when|upcoming)\b", "schedule"),
    ):
        if re.search(pattern, raw):
            requested.append(fact)
    time_parts = re.findall(
        r"(?i)\b(?:today|tomorrow|tonight|next\s+\w+|\d{1,2}(?::\d{2})?\s*(?:am|pm))\b",
        raw,
    )
    # Structured fields own exact identifiers and time windows. Remove those
    # spans from the free-form subject so provider queries cannot duplicate or
    # mutate them through later enrichers.
    subject = raw
    for identifier in exact_identifiers:
        subject = re.sub(rf"(?<![\w@])@?{re.escape(identifier)}(?![\w])", " ", subject)
    for time_part in time_parts:
        subject = re.sub(re.escape(time_part), " ", subject, count=1, flags=re.IGNORECASE)
    subject = re.sub(r"\s+", " ", subject).strip(" ,;:-") or raw
    preference = ""
    source_preferences: list[str] = []
    if chosen == RetrievalDomain.FLIGHTS:
        preference = "configured_flight_connection"
    elif chosen == RetrievalDomain.SPORTS:
        preference = "structured_sports"
    elif chosen == RetrievalDomain.SOCIAL_METRIC:
        preference = "official_platform_metric"
        platform = next(
            (name for name in ("Twitch", "YouTube", "Instagram", "TikTok", "Twitter") if name.casefold() in raw.casefold()),
            "platform",
        )
        source_preferences = [f"official {platform} account"]
    elif exact_identifiers:
        source_preferences = ["public profile"]
    return ResearchQueryPlan(
        domain=chosen,
        intent=requested[0] if requested else "lookup",
        subject=subject,
        resolved_entities=resolved_entities or [],
        exact_identifiers=exact_identifiers,
        requested_facts=requested,
        time_window=" ".join(dict.fromkeys(time_parts)),
        source_preferences=source_preferences,
        structured_capability_preference=preference,
        requirement_id=str(requirement_id or ""),
        attempt_id=str(attempt_id or ""),
        raw_user_message=str(raw_user_message or ""),
        requirement_objective=str(objective or ""),
        proposed_query=raw,
        objective_sha256=(
            hashlib.sha256(str(objective or "").strip().encode("utf-8")).hexdigest()
            if str(objective or "").strip() else ""
        ),
    )


def validate_provider_query(
    query: str, *, domain: RetrievalDomain, plan: Optional[ResearchQueryPlan] = None
) -> None:
    low = re.sub(r"\s+", " ", str(query or "")).strip().casefold()
    if not low or _INTERNAL_LABEL.search(low):
        raise ValueError("provider query is empty or contains an internal envelope label")
    if _PRONOUN_ONLY.fullmatch(low):
        raise ValueError("provider query has no anchored subject")
    if domain == RetrievalDomain.FLIGHTS and set(low.split()) & _SPORTS_VOCAB:
        raise ValueError("flight provider query contains cross-domain sports vocabulary")
    if domain == RetrievalDomain.SPORTS and set(low.split()) & {"airfare", "departure", "arrival", "gate"}:
        raise ValueError("sports provider query contains cross-domain flight vocabulary")
    if plan is not None:
        allowed = set(re.findall(r"[a-z0-9_]+", " ".join([
            plan.subject,
            *plan.resolved_entities,
            *plan.exact_identifiers,
            *plan.requested_facts,
            plan.time_window,
            plan.location,
            *plan.source_preferences,
        ]).casefold()))
        domain_vocab = {
            RetrievalDomain.FLIGHTS: _FLIGHT_VOCAB | {"status", "price", "current", "schedule"},
            RetrievalDomain.SPORTS: _SPORTS_VOCAB | {"sports", "score", "schedule", "standings", "results"},
            RetrievalDomain.SOCIAL_METRIC: {"account", "profile", "followers", "follower", "count", "official"},
        }.get(domain, set())
        unexplained = set(re.findall(r"[a-z0-9_]+", low)) - allowed - domain_vocab
        # Function words are harmless; named/domain words are not silently added.
        unexplained -= {"the", "a", "an", "of", "for", "to", "from", "on", "at", "in", "and", "or"}
        if len(unexplained) > 8:
            raise ValueError("provider query contains unexplained vocabulary")


def query_plan_covers_requirement(plan: ResearchQueryPlan, requirement: Any) -> bool:
    """Return whether a provider query retains the requirement's stable anchors."""

    provider_text = plan.provider_query().casefold()
    objective = str(getattr(requirement, "objective", "") or "")
    entities = [str(item) for item in list(getattr(requirement, "entities", []) or [])]
    location = str(getattr(requirement, "location", "") or "").strip()
    time_window = str(getattr(requirement, "time_window", "") or "").strip()
    for anchor in [*entities, *([location] if location else []), *([time_window] if time_window else [])]:
        tokens = [item for item in re.findall(r"[a-z0-9]+", anchor.casefold()) if len(item) >= 3]
        if tokens and not any(item in provider_text for item in tokens):
            return False
    required_years = set(re.findall(r"\b(?:19|20)\d{2}\b", objective))
    provider_years = set(re.findall(r"\b(?:19|20)\d{2}\b", provider_text))
    if required_years and not required_years.issubset(provider_years):
        return False
    objective_terms = {
        item for item in re.findall(r"[a-z0-9]+", objective.casefold())
        if len(item) >= 4 and item not in _QUERY_STOPWORDS and not item.isdigit()
    }
    provider_terms = set(re.findall(r"[a-z0-9]+", provider_text))
    if objective_terms and not objective_terms.intersection(provider_terms):
        return False
    return True


def infer_result_state(tool_name: str, output: Any, *, success: bool) -> ResultState:
    text = str(output or "").strip()
    low = text.casefold()
    if not success:
        if "cancel" in low:
            return ResultState.NO_DATA
        if "not configured" in low or "unavailable" in low or "cannot connect" in low:
            return ResultState.PROVIDER_UNAVAILABLE
        if "unsupported" in low or "not available" in low or "not a" in low:
            return ResultState.UNSUPPORTED_INTENT
        return ResultState.NO_DATA
    if not text:
        return ResultState.NO_DATA
    if "result_state=verified_absence" in low or '"result_state":"verified_absence"' in low.replace(" ", ""):
        return ResultState.VERIFIED_ABSENCE
    if "result_state=unsupported_intent" in low or "unsupported_intent" in low:
        return ResultState.UNSUPPORTED_INTENT
    if "result_state=provider_unavailable" in low:
        return ResultState.PROVIDER_UNAVAILABLE
    if "result_state=ambiguous_entity" in low:
        return ResultState.AMBIGUOUS_ENTITY
    if "result_state=stale_data" in low:
        return ResultState.STALE_DATA
    if "result_state=insufficient_evidence" in low:
        return ResultState.INSUFFICIENT_EVIDENCE
    if "result_state=no_data" in low:
        return ResultState.NO_DATA
    if "accepted=false" in low or "search_evidence_insufficient" in low:
        return ResultState.INSUFFICIENT_EVIDENCE
    if re.search(r"(?i)\bok\s*=\s*false\b", text):
        return ResultState.NO_DATA
    if any(marker in low for marker in ("no data", "no matching", "no score events", "no odds events")):
        return ResultState.NO_DATA
    return ResultState.DATA_FOUND


USABLE_RESULT_STATES = frozenset({
    ResultState.DATA_FOUND.value,
    ResultState.VERIFIED_ABSENCE.value,
})


__all__ = [
    "ExecutionStatus",
    "ResearchQueryPlan",
    "ResultState",
    "RetrievalDomain",
    "USABLE_RESULT_STATES",
    "infer_result_state",
    "infer_retrieval_domain",
    "plan_research_query",
    "query_plan_covers_requirement",
    "validate_provider_query",
]
