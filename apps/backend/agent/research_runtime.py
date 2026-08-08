"""Requirement-driven research contracts owned by the canonical runtime.

This module is deliberately orchestration-framework agnostic.  It contains
typed state, deterministic capability projection, evidence normalization, and
the single research-completion evaluator.  It never invokes a model, executes
a tool, or terminalizes a TaskRun.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.retrieval_contracts import ResultState, USABLE_RESULT_STATES


REQUIREMENT_SCHEMA_VERSION = 1
CAPABILITY_SCHEMA_VERSION = 1
EVIDENCE_SCHEMA_VERSION = 1


class RequirementKind(str, Enum):
    RETRIEVAL = "retrieval"
    MEMORY = "memory"
    LOCAL_CONTEXT = "local_context"
    CALCULATION = "calculation"
    SPECIALIST = "specialist"
    ANSWER_ONLY = "answer_only"


class RequirementStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    SATISFIED = "satisfied"
    WEAK = "weak"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    EXHAUSTED = "exhausted"


class ResearchDepth(str, Enum):
    FAST = "fast"
    STANDARD = "standard"
    DEEP = "deep"


class CompletionDisposition(str, Enum):
    PENDING = "pending"
    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class TaskRunNextAction(str, Enum):
    """The runtime-owned liveness decision for one TaskRun revision."""

    RUN_TOOL = "run_tool"
    FINALIZE = "finalize"
    WAIT_FOR_USER = "wait_for_user"
    WAIT_FOR_APPROVAL = "wait_for_approval"
    WAIT_FOR_EXTERNAL_RESULT = "wait_for_external_result"
    HARD_FAILURE = "hard_failure"


class TurnRequirement(BaseModel):
    """One independent, user-requested unit of work."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int = REQUIREMENT_SCHEMA_VERSION
    requirement_id: str = ""
    kind: RequirementKind = RequirementKind.RETRIEVAL
    objective: str
    entities: list[str] = Field(default_factory=list)
    requested_fields: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    time_window: str = ""
    freshness_class: str = "unspecified"
    location: str = ""
    comparison_group: str = ""
    dependencies: list[str] = Field(default_factory=list)
    required: bool = True
    acceptance_criteria: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_bounded_fields(self) -> "TurnRequirement":
        self.requirement_id = str(self.requirement_id or "").strip()[:100]
        self.objective = re.sub(r"\s+", " ", str(self.objective or "")).strip()[:1200]
        if not self.objective:
            raise ValueError("TurnRequirement objective is required")
        for name, limit in (
            ("entities", 24),
            ("requested_fields", 32),
            ("constraints", 24),
            ("dependencies", 24),
            ("acceptance_criteria", 24),
        ):
            values = list(dict.fromkeys(
                re.sub(r"\s+", " ", str(item or "")).strip()[:300]
                for item in getattr(self, name)
                if str(item or "").strip()
            ))[:limit]
            setattr(self, name, values)
        self.time_window = str(self.time_window or "").strip()[:200]
        self.freshness_class = str(self.freshness_class or "unspecified").strip()[:80]
        self.location = str(self.location or "").strip()[:300]
        self.comparison_group = str(self.comparison_group or "").strip()[:100]
        return self


class RequirementState(BaseModel):
    schema_version: int = REQUIREMENT_SCHEMA_VERSION
    requirement_id: str
    status: RequirementStatus = RequirementStatus.PENDING
    covered_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    covered_entities: list[str] = Field(default_factory=list)
    # entity.casefold() -> covered field names for that place/subject
    entity_field_coverage: dict[str, list[str]] = Field(default_factory=dict)
    missing_entities: list[str] = Field(default_factory=list)
    attempt_ids: list[str] = Field(default_factory=list)
    tool_run_ids: list[str] = Field(default_factory=list)
    specialist_run_ids: list[str] = Field(default_factory=list)
    specialist_outcome_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    evidence_passages: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0)
    external_call_count: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    last_strategy: str = ""
    recommended_tools: list[str] = Field(default_factory=list)
    terminal_reason: str = ""
    recovery_epoch: int = Field(default=0, ge=0)
    epoch_attempt_ids: list[str] = Field(default_factory=list)
    epoch_external_call_count: int = Field(default=0, ge=0)
    attempt_fingerprints: list[str] = Field(default_factory=list)
    strategy_history: list[str] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)


class ResearchBudgetPolicy(BaseModel):
    depth: ResearchDepth = ResearchDepth.FAST
    max_time_seconds: float = Field(default=8.0, ge=0)
    max_attempts_per_requirement: int = Field(default=2, ge=1)
    max_external_calls: int = Field(default=2, ge=1)
    max_sources_per_requirement: int = Field(default=1, ge=1)
    max_concurrency: int = Field(default=1, ge=1, le=4)
    max_context_tokens: int = Field(default=4000, ge=256)


class CapabilityDescriptor(BaseModel):
    schema_version: int = CAPABILITY_SCHEMA_VERSION
    capability_id: str
    tool_name: str
    owner: str = "builtin"
    origin: str = "native"
    supported_operations: list[str] = Field(default_factory=list)
    result_fields: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    result_schema: dict[str, Any] = Field(default_factory=dict)
    structured_level: str = "text"
    freshness: str = "unknown"
    authority_class: str = "general"
    health: str = "healthy"
    available: bool = True
    authenticated: bool = False
    cost_class: str = "low"
    latency_class: str = "low"
    read_only: bool = True
    interactive: bool = False
    mutating: bool = False
    approval_required: bool = False
    fallback_classes: list[str] = Field(default_factory=list)


class CapabilitySnapshot(BaseModel):
    schema_version: int = CAPABILITY_SCHEMA_VERSION
    inventory_revision: int = 0
    inventory_sha256: str = ""
    project_id: str = ""
    session_id: str
    capabilities: list[CapabilityDescriptor] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)


class EvidenceEnvelope(BaseModel):
    schema_version: int = EVIDENCE_SCHEMA_VERSION
    evidence_id: str
    requirement_id: str
    attempt_id: str
    tool_run_id: str
    tool_name: str
    provider: str = ""
    source_urls: list[str] = Field(default_factory=list)
    source_identifiers: list[str] = Field(default_factory=list)
    structured_values: dict[str, Any] = Field(default_factory=dict)
    passage: str = ""
    locators: list[str] = Field(default_factory=list)
    covered_fields: list[str] = Field(default_factory=list)
    matched_entities: list[str] = Field(default_factory=list)
    unavailable_fields: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    execution_status: str = ""
    result_state: str = ""
    observed_at: Optional[float] = None
    published_at: Optional[float] = None
    retrieved_at: float = Field(default_factory=time.time)
    freshness: str = "unknown"
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    cache_identity: str = ""
    cache_revalidated: bool = False
    usable: bool = False
    diagnostic_code: str = ""


class CompletionVerdict(BaseModel):
    schema_version: int = REQUIREMENT_SCHEMA_VERSION
    disposition: CompletionDisposition = CompletionDisposition.PENDING
    finalizable: bool = False
    required_ids: list[str] = Field(default_factory=list)
    satisfied_ids: list[str] = Field(default_factory=list)
    unresolved_ids: list[str] = Field(default_factory=list)
    terminal_incomplete_ids: list[str] = Field(default_factory=list)
    missing_input_fields: list[str] = Field(default_factory=list)
    pending_approval: bool = False
    reason_code: str = "requirements_pending"
    evaluated_at: float = Field(default_factory=time.time)


class TaskRunAdvanceDecision(BaseModel):
    """One deterministic answer to what the TaskRun must do next.

    This is a persisted projection of TaskRun requirement state. It cannot
    execute tools or finalize a response; the existing authority and
    control-plane boundaries retain those responsibilities.
    """

    next_action: TaskRunNextAction
    reason_code: str
    active_requirement_id: str = ""
    recovery_strategy: str = ""
    eligible_tool_names: list[str] = Field(default_factory=list)
    preferred_tool_name: str = ""
    completion: CompletionVerdict = Field(default_factory=CompletionVerdict)
    requirement_states: dict[str, RequirementState] = Field(default_factory=dict)
    evaluated_at: float = Field(default_factory=time.time)


class ResearchBudgetExceeded(RuntimeError):
    pass


class RepeatedRecoveryStrategy(RuntimeError):
    pass


def _stable_requirement_id(objective: str, kind: RequirementKind, index: int) -> str:
    digest = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"echospeak:turn-requirement:{index}:{kind.value}:{objective.casefold().strip()}",
    )
    return f"req-{digest}"


def _fallback_kind(capabilities: Iterable[str]) -> RequirementKind:
    caps = {str(item or "").strip() for item in capabilities if str(item or "").strip()}
    # "chat" is a mode capability alias for pure conversation (no tools/evidence).
    non_conversation = caps - {"conversation", "chat"}
    if not non_conversation:
        return RequirementKind.ANSWER_ONLY
    if non_conversation <= {"memory"}:
        return RequirementKind.MEMORY
    if non_conversation <= {"calculate", "time"}:
        return RequirementKind.CALCULATION
    if non_conversation & {
        "coding_read", "coding_write", "terminal", "code_execution", "specialist_code",
    }:
        return RequirementKind.SPECIALIST
    if non_conversation & {"coding_read", "project", "local_context"} and not non_conversation & {
        "research", "live_weather", "live_sports"
    }:
        return RequirementKind.LOCAL_CONTEXT
    return RequirementKind.RETRIEVAL


def infer_requested_fields(objective: str) -> list[str]:
    """Infer a small reusable field contract from an informational objective."""

    low = str(objective or "").casefold()
    inferred: list[str] = []
    rules = (
        (r"\b(?:when|date|day|schedule|upcoming|next event|next match|next game)\b", "event_date"),
        (r"\b(?:time|kickoff|tipoff|start time|departure time|arrival time)\b", "event_time"),
        (r"\b(?:price|cost|fare|cheapest|amount)\b", "price"),
        (r"\b(?:status|available|availability|in stock|open now)\b", "status"),
        (r"\b(?:score|result|standings)\b", "result"),
        (r"\b(?:weather|temperature|forecast|conditions)\b", "weather_conditions"),
        (r"\b(?:where|location|address)\b", "location"),
        (r"\b(?:version|release|sdk|package|library|framework)\b", "version"),
        (r"\b(?:citation|citations|source|sources)\b", "source"),
    )
    for pattern, field_name in rules:
        if re.search(pattern, low):
            inferred.append(field_name)
    return inferred


_WEATHER_OBJECTIVE_RE = re.compile(
    r"\b(?:weather|temperature|forecast|humidity|conditions)\b",
    flags=re.IGNORECASE,
)
_PLACE_PRIMARY_SPLIT_RE = re.compile(r"\s*(?:/|&|\band\b)\s*", flags=re.IGNORECASE)
_PLACE_JUNK_RE = re.compile(
    r"(?i)\b(?:the|current|weather|forecast|temperature|temps?|conditions|please|"
    r"today|tomorrow|tonight|now|lookup|check|get|for|in)\b"
)
_ADMIN_REGION_NAMES = frozenset({
    # Common country/administrative qualifiers.  They are syntax hints, not
    # retrieval routing: their only purpose is to preserve "city, region" as
    # one location rather than inventing a second weather requirement.
    "alberta", "british columbia", "manitoba", "new brunswick",
    "newfoundland and labrador", "northwest territories", "nova scotia",
    "nunavut", "ontario", "prince edward island", "quebec",
    "saskatchewan", "yukon",
    "united states", "united states of america", "usa", "us",
    "canada", "mexico", "united kingdom", "uk", "england", "scotland",
    "wales", "northern ireland", "australia", "new zealand",
})


def _looks_like_place_name(value: str) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" .,;:!?")
    if len(text) < 2 or len(text) > 80:
        return False
    if not re.match(r"^[A-Za-z]", text):
        return False
    if _PLACE_JUNK_RE.fullmatch(text.strip()):
        return False
    if re.search(r"(?i)\b(?:weather|forecast|temperature|humidity|conditions)\b", text):
        return False
    return bool(re.search(r"[A-Za-z]{2,}", text))


def _normalize_place_name(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" .,;:!?")
    text = _PLACE_JUNK_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .,;:!?")
    if not text:
        return ""
    return text.title() if text.islower() else text


def _looks_like_admin_region(value: str) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" .,;:!?")
    if not text:
        return False
    if re.fullmatch(r"[A-Z]{2,3}", text):
        return True
    return text.casefold() in _ADMIN_REGION_NAMES


def _split_place_sequence(value: str) -> list[str]:
    """Split independent places while retaining city-region comma pairs."""

    results: list[str] = []
    protected = str(value or "")
    sentinel = "__ECHOSPEAK_PLACE_AND__"
    for region in sorted(
        (item for item in _ADMIN_REGION_NAMES if " and " in item),
        key=len,
        reverse=True,
    ):
        protected = re.sub(
            re.escape(region),
            region.replace(" and ", sentinel),
            protected,
            flags=re.IGNORECASE,
        )
    for primary in _PLACE_PRIMARY_SPLIT_RE.split(protected):
        primary = primary.replace(sentinel, " and ")
        comma_parts = [
            part.strip() for part in primary.split(",") if part.strip()
        ]
        index = 0
        while index < len(comma_parts):
            current = comma_parts[index]
            if (
                index + 1 < len(comma_parts)
                and _looks_like_admin_region(comma_parts[index + 1])
            ):
                results.append(f"{current}, {comma_parts[index + 1]}")
                index += 2
            else:
                results.append(current)
                index += 1
    return results


def extract_weather_locations(requirement: TurnRequirement | Mapping[str, Any] | str) -> list[str]:
    """Extract independent place names for weather-style multi-location requirements."""

    if isinstance(requirement, str):
        objective = requirement
        entities: list[str] = []
        location = ""
    elif isinstance(requirement, Mapping):
        objective = str(requirement.get("objective") or "")
        entities = [str(item) for item in list(requirement.get("entities") or []) if str(item).strip()]
        location = str(requirement.get("location") or "")
    else:
        objective = str(requirement.objective or "")
        entities = list(requirement.entities or [])
        location = str(requirement.location or "")

    places: list[str] = []
    for item in entities:
        normalized = _normalize_place_name(item)
        if _looks_like_place_name(normalized):
            places.append(normalized)
    if location:
        for part in _split_place_sequence(location):
            normalized = _normalize_place_name(part)
            if _looks_like_place_name(normalized):
                places.append(normalized)
    # Structural "weather for/in A and B" / "A and B weather"
    if _WEATHER_OBJECTIVE_RE.search(objective):
        match = re.search(
            r"(?i)\b(?:for|in|near|around|at)\s+(.+?)(?:\s*[?.!]|$)",
            objective,
        )
        candidate = match.group(1) if match else ""
        if not candidate:
            leading = re.search(
                r"(?i)^\s*(.+?)\s+(?:weather|forecast|temperature|temps?)\b",
                objective.strip(),
            )
            candidate = leading.group(1) if leading else ""
        for part in _split_place_sequence(candidate):
            normalized = _normalize_place_name(part)
            if _looks_like_place_name(normalized):
                places.append(normalized)
    # A model may have emitted ["Edmonton", "Alberta"] while its objective
    # still contains "Edmonton, Alberta".  Prefer the structurally qualified
    # location and discard its accidental component requirements.
    qualified_components: set[str] = set()
    for place in places:
        if "," in place:
            qualified_components.update(
                item.strip().casefold() for item in place.split(",") if item.strip()
            )
    if qualified_components:
        places = [
            place for place in places
            if "," in place or place.casefold() not in qualified_components
        ]
    # Preserve order, de-dupe case-insensitively.
    seen: set[str] = set()
    ordered: list[str] = []
    for place in places:
        key = place.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(place)
    return ordered[:8]


def expand_multi_location_weather_requirements(
    requirements: Iterable[TurnRequirement],
) -> list[TurnRequirement]:
    """Split multi-city weather objectives into one requirement per place.

    weather_live is one location per ToolRun. A combined "Edmonton and Calgary"
    requirement can never be satisfied by a single outcome under all-entity
    matching, and must not complete from one city alone either.
    """

    expanded: list[TurnRequirement] = []
    for row in requirements:
        if row.kind != RequirementKind.RETRIEVAL:
            expanded.append(row)
            continue
        objective_low = row.objective.casefold()
        if not _WEATHER_OBJECTIVE_RE.search(objective_low):
            expanded.append(row)
            continue
        places = extract_weather_locations(row)
        if len(places) <= 1:
            if places and not row.entities and not row.location:
                place = places[0]
                expanded.append(row.model_copy(update={
                    "entities": [place],
                    "location": place,
                }))
            else:
                expanded.append(row)
            continue
        fields = list(row.requested_fields) or infer_requested_fields(row.objective)
        if "weather_conditions" not in {item.casefold() for item in fields}:
            fields = [*fields, "weather_conditions"]
        for place in places:
            expanded.append(row.model_copy(update={
                "requirement_id": "",
                "objective": f"Weather for {place}",
                "entities": [place],
                "location": place,
                "requested_fields": fields,
                "acceptance_criteria": list(dict.fromkeys([
                    *row.acceptance_criteria,
                    f"Verified weather evidence must cover {place}.",
                ])),
            }))
    return expanded


def compile_turn_requirements(
    proposed: Iterable[TurnRequirement | Mapping[str, Any]],
    *,
    objective: str,
    capabilities: Iterable[str],
    requested_operation: str = "",
    missing_fields: Iterable[str] = (),
) -> list[TurnRequirement]:
    """Validate model proposals and provide one deterministic compatibility requirement."""

    fallback_kind = _fallback_kind(capabilities)
    rows: list[TurnRequirement] = []
    for raw in list(proposed or [])[:16]:
        row = raw if isinstance(raw, TurnRequirement) else TurnRequirement.model_validate(raw)
        rows.append(row.model_copy(deep=True))
    if not rows:
        requested = [str(item) for item in missing_fields if str(item).strip()]
        criteria = ["Return information that directly answers the stated objective."]
        if fallback_kind == RequirementKind.RETRIEVAL:
            criteria = ["Runtime-verified evidence must contain the requested information."]
        rows = [TurnRequirement(
            kind=fallback_kind,
            objective=str(objective or requested_operation or "Complete the requested work"),
            requested_fields=requested if fallback_kind != RequirementKind.ANSWER_ONLY else [],
            acceptance_criteria=criteria,
        )]
    # Expand multi-city weather before ids are assigned so each place is independent.
    if fallback_kind != RequirementKind.ANSWER_ONLY:
        rows = expand_multi_location_weather_requirements(rows)
        rows = rekind_misclassified_live_requirements(rows)
    seen: set[str] = set()
    normalized: list[TurnRequirement] = []
    for index, row in enumerate(rows):
        if (
            fallback_kind == RequirementKind.SPECIALIST
            and (
                len(rows) == 1
                or row.kind == RequirementKind.LOCAL_CONTEXT
                or re.search(
                    r"\b(?:code|coding|implement|refactor|debug|fix|file|terminal|"
                    r"repository|repo|build|test|compile|patch)\b",
                    row.objective,
                    flags=re.IGNORECASE,
                )
            )
        ):
            row = row.model_copy(update={
                "kind": RequirementKind.SPECIALIST,
                "acceptance_criteria": list(dict.fromkeys([
                    *row.acceptance_criteria,
                    "A configured specialist runtime must return a verified terminal outcome.",
                ])),
            })
        # Conversational capability sets must not invent retrieval/evidence requirements.
        # Never demote clear live weather/search objectives into answer_only — that
        # falsely completes research turns with zero ToolRuns.
        if fallback_kind == RequirementKind.ANSWER_ONLY and row.kind in {
            RequirementKind.RETRIEVAL, RequirementKind.LOCAL_CONTEXT, RequirementKind.SPECIALIST
        }:
            if requirement_requires_verified_tool_evidence(row):
                # Keep retrieval even under conversational capability projection so
                # the runtime can fail closed or wait for tools rather than lie.
                pass
            else:
                row = row.model_copy(update={
                    "kind": RequirementKind.ANSWER_ONLY,
                    "requested_fields": [],
                    "acceptance_criteria": [
                        "A truthful conversational response satisfies this requirement."
                    ],
                })
        if row.kind == RequirementKind.ANSWER_ONLY and requirement_requires_verified_tool_evidence(row):
            row = row.model_copy(update={
                "kind": RequirementKind.RETRIEVAL,
                "requested_fields": list(row.requested_fields) or infer_requested_fields(row.objective),
            })
        if row.kind == RequirementKind.RETRIEVAL and not row.requested_fields:
            row = row.model_copy(update={
                "requested_fields": infer_requested_fields(row.objective)
            })
        requirement_id = row.requirement_id or _stable_requirement_id(row.objective, row.kind, index)
        if requirement_id in seen:
            raise ValueError(f"Duplicate TurnRequirement id: {requirement_id}")
        seen.add(requirement_id)
        normalized.append(row.model_copy(update={"requirement_id": requirement_id}))
    valid_ids = {item.requirement_id for item in normalized}
    for row in normalized:
        unknown = [item for item in row.dependencies if item not in valid_ids]
        if unknown:
            raise ValueError(f"Requirement {row.requirement_id} has unknown dependencies: {unknown}")
    return normalized


def canonicalize_proposed_requirements(
    proposed: Iterable[TurnRequirement | Mapping[str, Any]],
    *,
    objective: str,
    capabilities: Iterable[str],
    requested_operation: str = "",
    missing_fields: Iterable[str] = (),
) -> list[TurnRequirement]:
    """Assign runtime-owned stable identities to model-proposed requirements."""

    raw_rows = [
        item if isinstance(item, TurnRequirement) else TurnRequirement.model_validate(item)
        for item in list(proposed or [])[:16]
    ]
    if not raw_rows:
        return compile_turn_requirements(
            [],
            objective=objective,
            capabilities=capabilities,
            requested_operation=requested_operation,
            missing_fields=missing_fields,
        )
    id_map: dict[str, str] = {}
    assigned: list[TurnRequirement] = []
    for index, row in enumerate(raw_rows):
        stable_id = _stable_requirement_id(row.objective, row.kind, index)
        if row.requirement_id:
            id_map[row.requirement_id] = stable_id
        assigned.append(row.model_copy(update={"requirement_id": stable_id}))
    assigned = [
        row.model_copy(update={
            "dependencies": [id_map.get(item, item) for item in row.dependencies]
        })
        for row in assigned
    ]
    return compile_turn_requirements(
        assigned,
        objective=objective,
        capabilities=capabilities,
        requested_operation=requested_operation,
        missing_fields=missing_fields,
    )


def _requirement_semantic_key(requirement: TurnRequirement) -> tuple[str, str]:
    return (
        requirement.kind.value,
        re.sub(r"\W+", " ", requirement.objective.casefold()).strip(),
    )


def _requirement_contract_fingerprint(requirement: TurnRequirement) -> str:
    payload = requirement.model_dump(mode="json", exclude={"requirement_id"})
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def replace_turn_requirements(
    existing: Iterable[TurnRequirement],
    existing_states: Mapping[str, RequirementState | Mapping[str, Any]],
    proposed: Iterable[TurnRequirement | Mapping[str, Any]],
    *,
    objective: str,
    capabilities: Iterable[str],
    requested_operation: str = "",
    missing_fields: Iterable[str] = (),
) -> tuple[list[TurnRequirement], dict[str, RequirementState], list[TurnRequirement], dict[str, RequirementState]]:
    """Replace a corrected active set while preserving only unchanged work."""

    previous = [item.model_copy(deep=True) for item in existing]
    previous_states = {
        key: value if isinstance(value, RequirementState) else RequirementState.model_validate(value)
        for key, value in dict(existing_states or {}).items()
    }
    incoming = canonicalize_proposed_requirements(
        proposed,
        objective=objective,
        capabilities=capabilities,
        requested_operation=requested_operation,
        missing_fields=missing_fields,
    )
    previous_by_key = {_requirement_semantic_key(item): item for item in previous}
    used_previous_ids: set[str] = set()
    changed_previous_ids: set[str] = set()
    active: list[TurnRequirement] = []
    states: dict[str, RequirementState] = {}
    defaults = initial_requirement_states(incoming)
    for item in incoming:
        matched = previous_by_key.get(_requirement_semantic_key(item))
        if matched is None:
            active.append(item)
            states[item.requirement_id] = defaults[item.requirement_id]
            continue
        used_previous_ids.add(matched.requirement_id)
        stable = item.model_copy(update={"requirement_id": matched.requirement_id})
        active.append(stable)
        previous_state = previous_states.get(matched.requirement_id)
        if (
            previous_state is not None
            and _requirement_contract_fingerprint(stable) == _requirement_contract_fingerprint(matched)
        ):
            states[stable.requirement_id] = previous_state.model_copy(deep=True)
        else:
            changed_previous_ids.add(matched.requirement_id)
            states[stable.requirement_id] = initial_requirement_states([stable])[stable.requirement_id]
    archived = [
        item for item in previous
        if item.requirement_id not in used_previous_ids or item.requirement_id in changed_previous_ids
    ]
    archived_states = {
        item.requirement_id: previous_states[item.requirement_id]
        for item in archived if item.requirement_id in previous_states
    }
    return active, states, archived, archived_states


def initial_requirement_states(requirements: Iterable[TurnRequirement]) -> dict[str, RequirementState]:
    states: dict[str, RequirementState] = {}
    for requirement in requirements:
        answer_only = requirement.kind == RequirementKind.ANSWER_ONLY
        states[requirement.requirement_id] = RequirementState(
            requirement_id=requirement.requirement_id,
            status=RequirementStatus.SATISFIED if answer_only else RequirementStatus.PENDING,
            covered_fields=list(requirement.requested_fields) if answer_only else [],
            missing_fields=[] if answer_only else list(requirement.requested_fields),
            terminal_reason="response_generation_only" if answer_only else "",
        )
    return states


def merge_turn_requirements(
    existing: Iterable[TurnRequirement],
    proposed: Iterable[TurnRequirement | Mapping[str, Any]],
    *,
    objective: str,
    capabilities: Iterable[str],
    requested_operation: str = "",
    missing_fields: Iterable[str] = (),
) -> list[TurnRequirement]:
    """Preserve completed work while adding or refining independently identified parts."""
    current = [item.model_copy(deep=True) for item in existing]
    proposed_rows = list(proposed or [])
    if not proposed_rows:
        return current or compile_turn_requirements(
            [], objective=objective, capabilities=capabilities,
            requested_operation=requested_operation, missing_fields=missing_fields,
        )
    incoming = canonicalize_proposed_requirements(
        proposed_rows, objective=objective, capabilities=capabilities,
        requested_operation=requested_operation, missing_fields=missing_fields,
    )
    by_id = {item.requirement_id: index for index, item in enumerate(current)}
    by_semantic = {
        (item.kind.value, re.sub(r"\W+", " ", item.objective.casefold()).strip()): index
        for index, item in enumerate(current)
    }
    for item in incoming:
        index = by_id.get(item.requirement_id)
        if index is None:
            index = by_semantic.get((
                item.kind.value,
                re.sub(r"\W+", " ", item.objective.casefold()).strip(),
            ))
        if index is None:
            current.append(item)
            by_id[item.requirement_id] = len(current) - 1
        else:
            stable_id = current[index].requirement_id
            current[index] = item.model_copy(update={"requirement_id": stable_id})
    return current


def reconcile_requirement_states(
    requirements: Iterable[TurnRequirement],
    existing: Mapping[str, RequirementState | Mapping[str, Any]],
) -> dict[str, RequirementState]:
    defaults = initial_requirement_states(requirements)
    reconciled: dict[str, RequirementState] = {}
    for requirement in requirements:
        raw = existing.get(requirement.requirement_id)
        reconciled[requirement.requirement_id] = (
            raw if isinstance(raw, RequirementState)
            else RequirementState.model_validate(raw)
            if raw is not None
            else defaults[requirement.requirement_id]
        )
    return reconciled


def select_research_depth(requirements: Iterable[TurnRequirement], objective: str) -> ResearchDepth:
    rows = list(requirements)
    low = str(objective or "").casefold()
    if re.search(r"\b(deep|comprehensive|investigate|systematic|multi[- ]source|detailed research)\b", low):
        return ResearchDepth.DEEP
    if len(rows) > 1 or any(item.comparison_group or item.dependencies for item in rows):
        return ResearchDepth.STANDARD
    return ResearchDepth.FAST


def budget_for_depth(depth: ResearchDepth | str) -> ResearchBudgetPolicy:
    value = ResearchDepth(str(getattr(depth, "value", depth)))
    if value == ResearchDepth.DEEP:
        return ResearchBudgetPolicy(
            depth=value, max_time_seconds=120, max_attempts_per_requirement=5,
            max_external_calls=24, max_sources_per_requirement=8, max_concurrency=4,
            max_context_tokens=12000,
        )
    if value == ResearchDepth.STANDARD:
        return ResearchBudgetPolicy(
            depth=value, max_time_seconds=30, max_attempts_per_requirement=3,
            max_external_calls=8, max_sources_per_requirement=3, max_concurrency=3,
            max_context_tokens=8000,
        )
    return ResearchBudgetPolicy(depth=value)


def capability_descriptor_from_tool(definition: Any) -> CapabilityDescriptor:
    name = str(getattr(definition, "name", "") or "")
    origin = str(getattr(definition, "origin", "native") or "native")
    approval = bool(getattr(definition, "approval_required", False))
    mutating = bool(getattr(definition, "mutating", False))
    category = str(getattr(definition, "category", "") or "")
    operations: list[str] = []
    fields: list[str] = []
    structured = "text"
    freshness = "unknown"
    authority = "general"
    fallbacks: list[str] = []
    interactive = name in {"browse_task"} or name.startswith("desktop_")
    if name == "web_search":
        operations = ["discovery", "current_lookup", "authority_search"]
        fields = ["search_hits", "source_urls", "snippets"]
        freshness, fallbacks = "provider_observed", ["safe_web_fetch", "browse_task"]
    elif name == "safe_web_fetch":
        operations = ["page_fetch", "structured_extraction", "visible_text_extraction"]
        fields = ["page_text", "json_ld", "microdata", "rdfa", "tables", "metadata"]
        structured, fallbacks = "page_structured", ["browse_task"]
    elif name == "weather_live":
        operations = ["structured_live_lookup"]
        # Declare the real weather_live JSON contract (open-meteo projection).
        fields = [
            "weather_conditions", "location", "temperature_c", "apparent_temperature_c",
            "relative_humidity_percent", "precipitation_mm", "weather_code",
            "wind_speed_kmh", "observed_at", "timezone", "source", "provenance",
            "exact_values",
        ]
        structured, freshness, authority = "structured", "live", "specialized"
        fallbacks = ["web_search", "safe_web_fetch"]
    elif name == "sports_live":
        operations = ["structured_live_lookup"]
        fields = ["result", "status", "exact_values", "observed_at", "provenance"]
        structured, freshness, authority = "structured", "live", "specialized"
        fallbacks = ["web_search"]
    elif name == "calculate":
        operations, fields, structured = ["calculation"], ["calculated_value"], "structured"
    elif name == "youtube_transcript":
        operations, fields = ["transcript_lookup"], ["transcript", "source_url"]
    elif name == "browse_task":
        operations, fields = ["interactive_browse"], ["rendered_page_text"]
    else:
        operations = [category or name]
    return CapabilityDescriptor(
        capability_id=f"tool:{origin}:{name}",
        tool_name=name,
        owner=str(getattr(definition, "owner", "builtin") or "builtin"),
        origin=origin,
        supported_operations=operations,
        result_fields=fields,
        input_schema=dict(getattr(definition, "parameters", {}) or {}),
        result_schema=(
            dict(getattr(definition, "result_schema", {}) or {})
            or (
                {
                    "type": "object",
                    "properties": {
                        field_name: {} for field_name in fields
                    },
                    "additionalProperties": True,
                }
                if fields
                else {}
            )
        ),
        structured_level=structured,
        freshness=freshness,
        authority_class=authority,
        health=str(getattr(definition, "health", "unknown") or "unknown"),
        available=bool(getattr(definition, "available", True)),
        authenticated=bool(getattr(definition, "connection_id", "") or getattr(definition, "mcp_server", "")),
        latency_class="high" if interactive else "medium" if name in {"web_search", "safe_web_fetch"} else "low",
        read_only=not mutating,
        interactive=interactive,
        mutating=mutating,
        approval_required=approval,
        fallback_classes=fallbacks,
    )


def build_capability_snapshot(
    definitions: Iterable[Any], *, inventory_revision: int, project_id: str, session_id: str
) -> CapabilitySnapshot:
    capabilities = sorted(
        (capability_descriptor_from_tool(item) for item in definitions),
        key=lambda item: item.capability_id,
    )
    canonical = json.dumps(
        [item.model_dump(mode="json") for item in capabilities],
        sort_keys=True, separators=(",", ":"),
    )
    return CapabilitySnapshot(
        inventory_revision=int(inventory_revision or 0),
        inventory_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        project_id=str(project_id or ""),
        session_id=str(session_id or ""),
        capabilities=capabilities,
    )


def missing_requirement_entities(
    requirement: TurnRequirement,
    covered_entities: Iterable[str],
) -> list[str]:
    """Return place/subject anchors still uncovered by accumulated evidence."""

    required = [str(item) for item in requirement.entities if str(item).strip()]
    split_locations = _split_place_sequence(requirement.location)
    if not required and len(split_locations) > 1:
        required = [
            _normalize_place_name(part)
            for part in split_locations
            if _normalize_place_name(part)
        ]
    if not required:
        return []
    covered_keys = {
        item.casefold() for item in covered_entities if str(item or "").strip()
    }
    missing: list[str] = []
    for entity in required:
        terms = _anchor_terms(entity)
        if not terms:
            continue
        if entity.casefold() in covered_keys:
            continue
        if any(terms.intersection(_anchor_terms(item)) for item in covered_entities):
            continue
        missing.append(entity)
    return missing


def recommended_tools_for_recovery(
    strategy: str,
    *,
    requirement: Optional[TurnRequirement] = None,
    available_tools: Optional[Iterable[str]] = None,
) -> list[str]:
    """Map recovery strategy to concrete authorized tool preferences."""

    available = {
        str(item or "").strip()
        for item in list(available_tools or [])
        if str(item or "").strip()
    }
    objective = str(getattr(requirement, "objective", "") or "").casefold()
    weatherish = bool(_WEATHER_OBJECTIVE_RE.search(objective))
    order: list[str]
    if strategy in {"primary_capability", "query_reformulation", "entity_argument_correction"}:
        order = (
            ["weather_live", "web_search", "safe_web_fetch"]
            if weatherish
            else ["web_search", "safe_web_fetch", "browse_task"]
        )
    elif strategy in {"alternate_provider", "authority_targeted_search"}:
        order = ["web_search", "safe_web_fetch", "weather_live", "browse_task"]
    elif strategy in {"safe_page_fetch", "structured_page_extraction"}:
        order = ["safe_web_fetch", "web_search", "browse_task"]
    elif strategy == "alternate_source":
        order = ["web_search", "safe_web_fetch", "browse_task", "weather_live"]
    else:
        order = ["web_search", "safe_web_fetch", "browse_task", "weather_live"]
    if available:
        order = [item for item in order if item in available]
    return order


def next_recovery_strategy(
    state: RequirementState,
    requirement: Optional[TurnRequirement] = None,
) -> str:
    """Choose the next recovery class from missing coverage, not just attempt count.

    Attempt index still advances through the ladder, but entity gaps and field
    gaps force meaningful strategy changes before repeating primary weather.
    """

    strategies = (
        "primary_capability",
        "entity_argument_correction",
        "query_reformulation",
        "alternate_provider",
        "authority_targeted_search",
        "safe_page_fetch",
        "structured_page_extraction",
        "alternate_source",
        "contradiction_resolution",
    )
    attempts = len(
        state.epoch_attempt_ids
        or (state.attempt_ids if int(state.recovery_epoch) == 0 else [])
    )
    missing_entities = list(state.missing_entities or [])
    if requirement is not None and not missing_entities:
        missing_entities = missing_requirement_entities(requirement, state.covered_entities)
    missing_fields = list(state.missing_fields or [])
    # First retry after a partial hit: fix the missing city/args before searching.
    if attempts >= 1 and missing_entities:
        selected = "entity_argument_correction"
    elif attempts >= 1 and missing_fields and not missing_entities:
        # Fields incomplete on a matched entity → reformulate or change provider.
        selected = strategies[min(max(attempts, 2), len(strategies) - 1)]
    elif attempts >= 2 and state.covered_fields:
        # Successful partial evidence already exists — do not loop primary forever.
        selected = strategies[min(max(attempts, 3), len(strategies) - 1)]
    else:
        selected = strategies[min(attempts, len(strategies) - 1)]
    if attempts == 0 and state.strategy_history:
        prior = str(state.strategy_history[-1] or "")
        try:
            selected = strategies[min(strategies.index(prior) + 1, len(strategies) - 1)]
        except ValueError:
            pass
    return selected


def choose_active_requirement(
    requirements: Iterable[TurnRequirement],
    states: Mapping[str, RequirementState],
    *,
    tool_name: str = "",
    capability: Optional[CapabilityDescriptor] = None,
) -> Optional[TurnRequirement]:
    """Pick the next dependency-ready requirement, preferring tool fit when given."""
    satisfied = {key for key, value in states.items() if value.status == RequirementStatus.SATISFIED}
    candidates: list[TurnRequirement] = []
    for requirement in requirements:
        state = states.get(requirement.requirement_id)
        if state is None or state.status not in {
            RequirementStatus.PENDING, RequirementStatus.ACTIVE, RequirementStatus.WEAK
        }:
            continue
        if all(item in satisfied for item in requirement.dependencies):
            candidates.append(requirement)
    if not candidates:
        return None
    tool = str(tool_name or "").strip()
    if not tool:
        # Prefer retrieval / tool-backed work before pure conversation leftovers.
        ordered = sorted(
            candidates,
            key=lambda item: (
                0 if item.kind in {RequirementKind.RETRIEVAL, RequirementKind.SPECIALIST} else
                1 if item.kind in {RequirementKind.LOCAL_CONTEXT, RequirementKind.CALCULATION, RequirementKind.MEMORY} else
                2,
                item.requirement_id,
            ),
        )
        return ordered[0]
    scored = [
        (capability_fit_score(tool, item, capability), item)
        for item in candidates
    ]
    scored = [row for row in scored if row[0] > 0]
    if not scored:
        return None
    scored.sort(key=lambda row: (-row[0], row[1].requirement_id))
    return scored[0][1]


def tool_matches_requirement(
    tool_name: str,
    requirement: TurnRequirement,
    capability: Optional[CapabilityDescriptor] = None,
) -> bool:
    return capability_fit_score(tool_name, requirement, capability) > 0


def capability_fit_score(
    tool_name: str,
    requirement: TurnRequirement,
    capability: Optional[CapabilityDescriptor] = None,
) -> int:
    """Return bounded routing fit without granting execution authority.

    The current Turn allowlist, registry snapshot, permissions, approval policy,
    and Project scope remain the execution boundary. This score only prevents
    unrelated tools from being treated as evidence-capable and gives the model
    a stable least-complex ordering among already-authorized capabilities.
    """
    name = str(tool_name or "").strip()
    if not name:
        return 0
    if capability is not None:
        if capability.tool_name != name or not capability.available:
            return 0
        if capability.health.casefold() in {"unavailable", "failed", "offline", "disabled"}:
            return 0
    if requirement.kind == RequirementKind.ANSWER_ONLY:
        return 0
    if requirement.kind == RequirementKind.MEMORY:
        return 100 if name in {"memory_search", "memory_list"} else 0
    if requirement.kind == RequirementKind.CALCULATION:
        return 100 if name in {"calculate", "get_system_time"} else 0
    if requirement.kind == RequirementKind.SPECIALIST:
        # A specialist runtime is not a ToolRegistry entry. Individual file and
        # terminal tools must not become an alternate coding-agent loop.
        return 0

    descriptor = capability or CapabilityDescriptor(
        capability_id=f"compat:{name}", tool_name=name,
        supported_operations=[name], health="unknown", available=True,
    )
    objective = " ".join([
        requirement.objective,
        *requirement.entities,
        *requirement.requested_fields,
        *requirement.acceptance_criteria,
    ]).casefold()
    operations = {str(item or "").casefold() for item in descriptor.supported_operations}
    fields = {str(item or "").casefold() for item in descriptor.result_fields}
    requested = {str(item or "").casefold() for item in requirement.requested_fields}
    field_fit = bool(requested and requested & fields)

    if requirement.kind == RequirementKind.LOCAL_CONTEXT:
        local_prefixes = (
            "file_", "project_", "terminal_", "code_preview_", "desktop_",
            "self_", "image_", "generation_", "voice_",
        )
        if name.startswith(local_prefixes) or name in {"system_info", "calculate", "get_system_time"}:
            return 95 if field_fit else 75
        return 0

    # Retrieval may cover live data, safe discovery/fetch, communications reads,
    # and other provider-backed information. Mutating tools are eligible only
    # when the requirement itself clearly requests the corresponding action.
    if descriptor.mutating:
        action_requested = bool(re.search(
            r"\b(send|post|reply|create|write|save|delete|remove|move|copy|start|stop|generate|synthesize)\b",
            objective,
        ))
        if not action_requested:
            return 0
    if field_fit:
        return 100
    # Specialized live tools beat generic search when the objective matches.
    if name in {"weather_live", "sports_live"}:
        if name == "weather_live" and re.search(r"\bweather|temperature|forecast|conditions\b", objective):
            return 98
        if name == "sports_live" and re.search(r"\bscore|match|game|standings|fixture\b", objective):
            return 98
        return 80
    if descriptor.authority_class.casefold() == "specialized":
        return 95
    if descriptor.structured_level.casefold() in {"structured", "page_structured"}:
        return 85
    if "discovery" in operations or name == "web_search":
        return 75
    if "page_fetch" in operations or name == "safe_web_fetch":
        return 65
    if descriptor.interactive:
        return 35
    return 50


def begin_requirement_attempt(
    requirement: TurnRequirement,
    state: RequirementState,
    budget: ResearchBudgetPolicy,
    *,
    available_tools: Optional[Iterable[str]] = None,
    recovery_epoch: int = 0,
    attempt_fingerprint: str = "",
) -> tuple[RequirementState, str]:
    if state.status == RequirementStatus.SATISFIED:
        raise ResearchBudgetExceeded("Satisfied requirements cannot be rerun")
    epoch_attempt_ids = (
        list(
            state.epoch_attempt_ids
            or (state.attempt_ids if int(recovery_epoch) == 0 else [])
        )
        if int(state.recovery_epoch) == int(recovery_epoch)
        else []
    )
    if len(epoch_attempt_ids) >= budget.max_attempts_per_requirement:
        raise ResearchBudgetExceeded("Requirement attempt budget is exhausted")
    fingerprint = str(attempt_fingerprint or "").strip()
    if fingerprint and fingerprint in set(state.attempt_fingerprints):
        raise RepeatedRecoveryStrategy(
            "The same tool strategy and canonical arguments were already attempted"
        )
    attempt_id = f"attempt-{uuid.uuid4()}"
    epoch_state = state.model_copy(update={
        "recovery_epoch": int(recovery_epoch),
        "epoch_attempt_ids": epoch_attempt_ids,
    })
    strategy = next_recovery_strategy(epoch_state, requirement)
    recommended = recommended_tools_for_recovery(
        strategy, requirement=requirement, available_tools=available_tools
    )
    updated = state.model_copy(update={
        "status": RequirementStatus.ACTIVE,
        "attempt_ids": [*state.attempt_ids, attempt_id],
        "epoch_attempt_ids": [*epoch_attempt_ids, attempt_id],
        "retry_count": max(0, len(epoch_attempt_ids)),
        "external_call_count": state.external_call_count + 1,
        "epoch_external_call_count": (
            (
                state.epoch_external_call_count
                or (state.external_call_count if int(recovery_epoch) == 0 else 0)
            ) + 1
            if int(state.recovery_epoch) == int(recovery_epoch)
            else 1
        ),
        "recovery_epoch": int(recovery_epoch),
        "attempt_fingerprints": list(dict.fromkeys([
            *state.attempt_fingerprints,
            *([fingerprint] if fingerprint else []),
        ]))[-64:],
        "strategy_history": list(dict.fromkeys([
            *state.strategy_history,
            strategy,
        ]))[-32:],
        "last_strategy": strategy,
        "recommended_tools": recommended,
        "missing_entities": missing_requirement_entities(requirement, state.covered_entities),
        "terminal_reason": "",
        "updated_at": time.time(),
    })
    return updated, attempt_id


def format_weather_live_summary(output: str) -> str:
    """Render a successful weather_live payload into a short user-facing fact line."""

    text = str(output or "").strip()
    first_object, last_object = text.find("{"), text.rfind("}")
    if not (0 <= first_object < last_object):
        return re.sub(r"\s+", " ", text)[:400]
    try:
        payload = json.loads(text[first_object:last_object + 1])
    except (TypeError, ValueError):
        return re.sub(r"\s+", " ", text)[:400]
    if not isinstance(payload, dict) or payload.get("ok") is False:
        return re.sub(r"\s+", " ", text)[:400]
    location = payload.get("location")
    if isinstance(location, Mapping):
        place = str(location.get("name") or "Unknown place").strip()
    else:
        place = str(location or "Unknown place").strip() or "Unknown place"
    parts = [place + ":"]
    temp = payload.get("temperature_c")
    if temp is not None:
        parts.append(f"{temp}°C")
    apparent = payload.get("apparent_temperature_c")
    if apparent is not None and apparent != temp:
        parts.append(f"(feels like {apparent}°C)")
    humidity = payload.get("relative_humidity_percent")
    if humidity is not None:
        parts.append(f"humidity {humidity}%")
    wind = payload.get("wind_speed_kmh")
    if wind is not None:
        parts.append(f"wind {wind} km/h")
    code = payload.get("weather_code")
    if code is not None:
        parts.append(f"code {code}")
    observed = payload.get("observed_at")
    if observed:
        parts.append(f"observed {observed}")
    source = payload.get("source")
    if source:
        parts.append(f"via {source}")
    return " ".join(str(item) for item in parts if str(item).strip())


def summarize_tool_evidence_passage(tool_name: str, output: str) -> str:
    name = str(tool_name or "").strip()
    if name == "weather_live":
        return format_weather_live_summary(output)
    text = re.sub(r"\s+", " ", str(output or "")).strip()
    return text[:500]


def _extract_urls(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"https?://[^\s\]\)\"']+", text)))[:24]


def _inferred_covered_fields(output: str, requested_fields: Iterable[str]) -> list[str]:
    text = str(output or "")
    low = text.casefold()
    covered: list[str] = []
    patterns = {
        "event_date": r"\b(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}\b|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        "event_time": r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\b|\b(?:[01]?\d|2[0-3]):[0-5]\d\b|\b(?:utc|gmt|est|edt|cst|cdt|mst|mdt|pst|pdt)\b",
        "price": r"(?:[$€£]\s?\d|\b\d+(?:\.\d{2})?\s?(?:usd|cad|eur|gbp)\b)",
        "status": r"\b(?:available|unavailable|in stock|out of stock|open|closed|delayed|cancelled|canceled|scheduled)\b",
        "result": r"\b\d{1,3}\s*[-:]\s*\d{1,3}\b|\b(?:won|lost|draw|standings|final score)\b",
        # Include Open-Meteo style keys (temperature_c) where \btemperature\b alone fails.
        "weather_conditions": (
            r"(?:temperature(?:_[a-z0-9]+)?|apparent_temperature(?:_[a-z0-9]+)?|"
            r"(?:relative_)?humidity(?:_[a-z0-9]+)?|wind(?:_speed)?(?:_[a-z0-9]+)?|"
            r"weather_code|precipitation(?:_[a-z0-9]+)?|forecast|°\s*[cf])"
        ),
        "location": r"\b(?:address|located|location|venue|airport|store|latitude|longitude|admin1)\b",
    }
    for field_name in requested_fields:
        pattern = patterns.get(str(field_name or "").casefold())
        if pattern and re.search(pattern, low, flags=re.IGNORECASE):
            covered.append(str(field_name))
    return covered


def _structured_alias_fields(
    tool_name: str,
    structured_values: Mapping[str, Any],
    output: str,
) -> set[str]:
    """Map provider-native keys onto requirement field names.

    weather_live returns temperature_c / weather_code / humidity keys, not a
    literal weather_conditions property. Without this alias, successful live
    weather is marked requested_fields_not_covered and exhausted.
    """

    name = str(tool_name or "").strip()
    keys = {
        str(item or "").strip().casefold()
        for item in structured_values.keys()
        if str(item or "").strip()
    }
    text = str(output or "").casefold()
    aliases: set[str] = set()
    weather_keys = {
        "temperature_c", "apparent_temperature_c", "relative_humidity_percent",
        "precipitation_mm", "weather_code", "wind_speed_kmh", "temperature",
        "humidity", "wind", "forecast",
    }
    if name == "weather_live" or keys & weather_keys or re.search(
        r"temperature(?:_[a-z0-9]+)?|weather_code|relative_humidity|wind_speed|°\s*[cf]",
        text,
    ):
        if keys & weather_keys or re.search(
            r"temperature(?:_[a-z0-9]+)?|weather_code|relative_humidity|wind_speed|°\s*[cf]",
            text,
        ):
            aliases.add("weather_conditions")
    if "location" in keys or re.search(r"\b(?:location|latitude|longitude|admin1)\b", text):
        aliases.add("location")
    if "observed_at" in keys or re.search(r"\bobserved_at\b", text):
        aliases.add("event_time")
    if name == "sports_live":
        if keys & {"score", "home_score", "away_score", "status", "events"} or re.search(
            r"\b(?:score|final|standings|fixture)\b", text
        ):
            aliases.add("result")
            aliases.add("status")
    return aliases


def _evidence_text_blob(
    output: str,
    verification: Mapping[str, Any],
    structured_values: Optional[Mapping[str, Any]] = None,
) -> str:
    query_plan = dict(verification.get("query_plan") or {})
    location_blob = ""
    if structured_values:
        loc = structured_values.get("location")
        if isinstance(loc, Mapping):
            location_blob = " ".join(
                str(loc.get(key) or "")
                for key in ("name", "admin1", "country")
            )
        elif loc:
            location_blob = str(loc)
    return " ".join([
        str(output or ""),
        location_blob,
        str(query_plan.get("subject") or ""),
        " ".join(str(item) for item in list(query_plan.get("resolved_entities") or [])),
    ]).casefold()


def _anchor_terms(anchor: str) -> set[str]:
    return {
        item for item in re.findall(r"[a-z0-9]+", str(anchor or "").casefold())
        if len(item) >= 3
    }


def _location_identity_terms(structured_values: Optional[Mapping[str, Any]]) -> set[str]:
    """Place identity from structured weather/geo payloads only.

    Full weather JSON includes timezone ids like America/Edmonton which must not
    count as covering an Edmonton place requirement when the observed city is
    Calgary.
    """

    if not structured_values:
        return set()
    loc = structured_values.get("location")
    if isinstance(loc, Mapping):
        blob = " ".join(
            str(loc.get(key) or "")
            for key in ("name", "admin1", "country")
        )
    elif loc:
        blob = str(loc)
    else:
        return set()
    return set(re.findall(r"[a-z0-9]+", blob.casefold()))


def matched_requirement_entities(
    output: str,
    requirement: TurnRequirement,
    verification: Mapping[str, Any] | None = None,
    structured_values: Optional[Mapping[str, Any]] = None,
) -> list[str]:
    """Return requirement entities/places present in this single ToolOutcome."""

    identity_terms = _location_identity_terms(structured_values)
    # Prefer structured place identity when the tool returned one; fall back to
    # free text only when there is no structured location object.
    if identity_terms:
        evidence_terms = identity_terms
    else:
        evidence_terms = set(re.findall(
            r"[a-z0-9]+",
            _evidence_text_blob(output, dict(verification or {}), structured_values),
        ))
    matched: list[str] = []
    anchors = list(requirement.entities or [])
    if requirement.location and requirement.location not in anchors:
        # Multi-place location strings are handled via entity expansion; single
        # location still counts as an anchor for coverage tracking.
        if len(_split_place_sequence(requirement.location)) <= 1:
            anchors.append(requirement.location)
    for anchor in anchors:
        terms = _anchor_terms(anchor)
        if terms and terms.intersection(evidence_terms):
            matched.append(str(anchor))
    return list(dict.fromkeys(matched))


def _evidence_matches_requirement(
    output: str,
    requirement: TurnRequirement,
    verification: Mapping[str, Any],
    *,
    tool_name: str,
    structured_values: Optional[Mapping[str, Any]] = None,
) -> bool:
    retrieval_tools = {
        "web_search", "safe_web_fetch", "browse_task", "youtube_transcript",
        "weather_live", "sports_live",
    }
    if tool_name not in retrieval_tools:
        return True
    query_plan = dict(verification.get("query_plan") or {})
    expected_hash = hashlib.sha256(requirement.objective.encode("utf-8")).hexdigest()
    observed_hash = str(query_plan.get("objective_sha256") or "")
    if observed_hash and observed_hash != expected_hash:
        return False
    objective_terms = {
        item for item in re.findall(r"[a-z0-9]+", requirement.objective.casefold())
        if len(item) >= 4 and item not in {
            "about", "current", "date", "details", "event", "find", "from", "game",
            "information", "latest", "match", "next", "result", "search", "time", "when",
            "where", "which", "with", "weather", "forecast", "temperature", "humidity",
            "conditions",
        }
    }
    evidence_text = _evidence_text_blob(output, verification, structured_values)
    evidence_terms = set(re.findall(r"[a-z0-9]+", evidence_text))
    identity_terms = _location_identity_terms(structured_values)
    place_terms = identity_terms or evidence_terms
    # Multi-entity requirements may be covered by multiple ToolRuns. A single
    # outcome only needs to match at least one entity (or the sole location).
    entity_anchors = [str(item) for item in requirement.entities if str(item).strip()]
    if entity_anchors:
        if not matched_requirement_entities(
            output, requirement, verification, structured_values
        ):
            return False
    elif requirement.location:
        location_terms = _anchor_terms(requirement.location)
        # Multi-place location strings: partial match is enough for one outcome.
        if location_terms and not location_terms.intersection(place_terms):
            # If location is "Edmonton and Calgary", either place counts.
            parts = [
                _normalize_place_name(part)
                for part in _split_place_sequence(requirement.location)
                if _normalize_place_name(part)
            ]
            if len(parts) > 1:
                if not any(_anchor_terms(part).intersection(place_terms) for part in parts):
                    return False
            else:
                return False
    if requirement.time_window:
        window_terms = _anchor_terms(requirement.time_window)
        if window_terms and not window_terms.intersection(evidence_terms):
            return False
    required_years = set(re.findall(r"\b(?:19|20)\d{2}\b", requirement.objective))
    if required_years and not required_years.intersection(
        set(re.findall(r"\b(?:19|20)\d{2}\b", evidence_text))
    ):
        return False
    # Entity/location match already anchors specialized tools; skip loose objective OR
    # that would accept Edmonton evidence for a Calgary-only objective via shared words.
    if entity_anchors or requirement.location:
        return True
    return not objective_terms or bool(objective_terms.intersection(evidence_terms))


def _resolve_covered_fields(
    *,
    output: str,
    requirement: TurnRequirement,
    verification: Mapping[str, Any],
    tool_name: str,
    structured_values: Mapping[str, Any],
) -> list[str]:
    declared = {
        str(item).strip().casefold(): str(item).strip()
        for item in list(verification.get("covered_fields") or [])
        if str(item).strip()
    }
    structured_keys = {
        str(item).strip().casefold()
        for item in structured_values.keys()
        if str(item).strip()
    }
    inferred = {
        item.casefold(): item
        for item in _inferred_covered_fields(output, requirement.requested_fields)
    }
    aliases = {
        item.casefold()
        for item in _structured_alias_fields(tool_name, structured_values, output)
    }
    covered: list[str] = []
    for field in requirement.requested_fields:
        canonical = str(field or "").strip().casefold()
        if (
            canonical in declared
            or canonical in structured_keys
            or canonical in inferred
            or canonical in aliases
        ):
            covered.append(field)
    return covered


def verify_tool_result_semantics(
    output: str,
    requirement: TurnRequirement,
    verification: Mapping[str, Any],
    *,
    tool_name: str,
) -> tuple[bool, list[str]]:
    """Return bounded semantic relevance and covered fields for one result.

    This does not replace exact post-action verifiers. It prevents a provider
    request that merely returned data from being treated as information success
    for an unrelated or field-incomplete requirement.
    """

    structured_values: dict[str, Any] = {}
    text = str(output or "")
    first_object, last_object = text.find("{"), text.rfind("}")
    if 0 <= first_object < last_object:
        try:
            payload = json.loads(text[first_object:last_object + 1])
            if isinstance(payload, dict):
                structured_values = dict(payload)
        except (TypeError, ValueError):
            structured_values = {}
    covered = _resolve_covered_fields(
        output=text,
        requirement=requirement,
        verification=verification,
        tool_name=tool_name,
        structured_values=structured_values,
    )
    semantically_relevant = _evidence_matches_requirement(
        text,
        requirement,
        verification,
        tool_name=tool_name,
        structured_values=structured_values,
    )
    if requirement.requested_fields and len(covered) < len(requirement.requested_fields):
        semantically_relevant = False
    return semantically_relevant, covered


def evidence_from_tool_outcome(
    outcome: Any, *, requirement: TurnRequirement, attempt_id: str
) -> EvidenceEnvelope:
    output = str(getattr(outcome, "output", "") or "").strip()
    execution_status = str(getattr(outcome, "execution_status", "") or "")
    result_state = str(getattr(outcome, "result_state", "") or "")
    verification = dict(getattr(outcome, "verification", None) or {})
    verified = verification.get("verified") is True
    tool_name = str(getattr(outcome, "tool_name", "") or "")
    non_information_markers = {
        "(search expanded)", "search expanded", "queued", "started", "complete", "completed",
    }
    substantive_output = bool(
        output and output.casefold().strip(". ") not in non_information_markers
    )
    if tool_name in {
        "web_search", "safe_web_fetch", "browse_task", "youtube_transcript",
        "weather_live", "sports_live",
    }:
        substantive_output = substantive_output and len(output) >= 40
    verified_absence = result_state == ResultState.VERIFIED_ABSENCE.value
    absence_scope = str(verification.get("absence_scope") or "").strip()
    absence_contract_valid = bool(
        verified_absence
        and verified_absence_contract_is_valid(verification)
    )
    usable = bool(
        verified
        and execution_status == "success"
        and result_state in USABLE_RESULT_STATES
        and substantive_output
        and (not verified_absence or absence_contract_valid)
    )
    structured_values: dict[str, Any] = {}
    contradictions: list[str] = []
    unavailable_fields: list[str] = []
    cache_identity = ""
    cache_revalidated = False
    freshness = "unknown"
    first_object = output.find("{")
    last_object = output.rfind("}")
    if 0 <= first_object < last_object:
        try:
            payload = json.loads(output[first_object:last_object + 1])
            if isinstance(payload, dict):
                structured_values = dict(payload)
                contradictions = [
                    str(item)[:500] for item in list(payload.get("contradictions") or [])[:20]
                ]
                unavailable_fields = [
                    str(item)[:200] for item in list(payload.get("unavailable_fields") or [])[:40]
                ]
                cache_identity = str(payload.get("cache_identity") or "")[:200]
                cache_revalidated = bool(payload.get("cache_revalidated"))
                freshness = str(payload.get("freshness") or "unknown")[:80]
        except (TypeError, ValueError):
            structured_values = {}
    covered = _resolve_covered_fields(
        output=output,
        requirement=requirement,
        verification=verification,
        tool_name=tool_name,
        structured_values=structured_values,
    )
    matched_entities = matched_requirement_entities(
        output, requirement, verification, structured_values
    )
    semantic_match = _evidence_matches_requirement(
        output,
        requirement,
        verification,
        tool_name=tool_name,
        structured_values=structured_values,
    )
    if verified_absence and absence_contract_valid:
        scope_terms = _anchor_terms(absence_scope)
        requirement_terms = _anchor_terms(" ".join([
            requirement.objective,
            *requirement.entities,
            requirement.location,
            requirement.time_window,
        ]))
        semantic_match = bool(
            scope_terms
            and requirement_terms
            and scope_terms.intersection(requirement_terms)
        )
    if not semantic_match:
        usable = False
    if (
        requirement.requested_fields
        and len(covered) < len(requirement.requested_fields)
        and not verified_absence
    ):
        usable = False
    diagnostic = "verified_information_found" if usable else (
        "verified_absence_contract_incomplete" if verified_absence and not absence_contract_valid
        else "tool_execution_failed" if execution_status != "success"
        else "no_usable_information" if result_state not in USABLE_RESULT_STATES or not substantive_output
        else "requirement_mismatch" if not semantic_match
        else "requested_fields_not_covered"
    )
    run_id = str(getattr(outcome, "run_id", "") or "")
    evidence_id = f"evidence-{uuid.uuid5(uuid.NAMESPACE_URL, f'echospeak:{run_id}:{requirement.requirement_id}') }"
    return EvidenceEnvelope(
        evidence_id=evidence_id,
        requirement_id=requirement.requirement_id,
        attempt_id=attempt_id,
        tool_run_id=run_id,
        tool_name=tool_name,
        provider=str(getattr(outcome, "provider", "") or ""),
        source_urls=_extract_urls(output),
        structured_values=structured_values,
        passage=re.sub(r"\s+", " ", output)[:12000],
        covered_fields=covered,
        matched_entities=matched_entities,
        unavailable_fields=list(dict.fromkeys([
            *[item for item in requirement.requested_fields if item not in covered],
            *unavailable_fields,
        ])),
        contradictions=contradictions,
        execution_status=execution_status,
        result_state=result_state,
        observed_at=getattr(outcome, "observed_at", None),
        confidence=getattr(outcome, "confidence", None),
        freshness=freshness,
        cache_identity=cache_identity,
        cache_revalidated=cache_revalidated,
        usable=usable,
        diagnostic_code=diagnostic,
    )


def _entity_coverage_complete(
    requirement: TurnRequirement,
    covered_entities: Iterable[str],
) -> bool:
    required = [str(item) for item in requirement.entities if str(item).strip()]
    if not required:
        # Fall back to multi-place location strings when entities were not set.
        split_locations = _split_place_sequence(requirement.location)
        if len(split_locations) > 1:
            required = [
                _normalize_place_name(part)
                for part in split_locations
                if _normalize_place_name(part)
            ]
        else:
            return True
    covered_keys = {
        item.casefold() for item in covered_entities if str(item or "").strip()
    }
    for entity in required:
        terms = _anchor_terms(entity)
        if not terms:
            continue
        if entity.casefold() in covered_keys:
            continue
        # Accept evidence that matched the same place under a slightly different label.
        if any(terms.intersection(_anchor_terms(item)) for item in covered_entities):
            continue
        return False
    return True


def verified_absence_contract_is_valid(verification: Mapping[str, Any]) -> bool:
    """Require explicit scope plus a durable citation for authoritative absence."""

    if verification.get("verified_absence") is not True:
        return False
    if not str(verification.get("absence_scope") or "").strip():
        return False
    candidates: list[str] = []
    raw_urls = verification.get("source_urls") or []
    if isinstance(raw_urls, str):
        candidates.append(raw_urls)
    else:
        candidates.extend(str(item or "") for item in list(raw_urls))
    for source in list(verification.get("sources") or []):
        if isinstance(source, Mapping):
            candidates.append(str(source.get("url") or ""))
        else:
            candidates.append(str(getattr(source, "url", "") or ""))
    candidates.append(str(verification.get("authoritative_source") or ""))
    return any(
        re.match(r"^https?://", candidate.strip(), flags=re.IGNORECASE)
        for candidate in candidates
    )


def requirement_accepts_verified_absence(requirement: TurnRequirement) -> bool:
    """Return whether an authoritative absence can answer this requirement."""

    text = " ".join([
        requirement.objective,
        *requirement.requested_fields,
        *requirement.acceptance_criteria,
    ])
    return bool(re.search(
        r"(?i)\b(?:next|upcoming|scheduled|available|availability|exists?|any|"
        r"latest\s+scheduled|future)\b",
        text,
    ))


def apply_evidence_to_state(
    requirement: TurnRequirement,
    state: RequirementState,
    evidence: EvidenceEnvelope,
    *,
    artifact_id: str = "",
    budget: Optional[ResearchBudgetPolicy] = None,
    available_tools: Optional[Iterable[str]] = None,
) -> RequirementState:
    if state.status == RequirementStatus.SATISFIED:
        passage = summarize_tool_evidence_passage(evidence.tool_name, evidence.passage)
        return state.model_copy(update={
            "tool_run_ids": list(dict.fromkeys([*state.tool_run_ids, evidence.tool_run_id])),
            "evidence_ids": list(dict.fromkeys([*state.evidence_ids, evidence.evidence_id])),
            "artifact_ids": list(dict.fromkeys(
                [*state.artifact_ids, artifact_id] if artifact_id else state.artifact_ids
            )),
            "evidence_passages": list(dict.fromkeys([
                *state.evidence_passages,
                *([passage] if passage else []),
            ]))[:12],
            "updated_at": time.time(),
        })
    verified_absence_satisfies = bool(
        evidence.usable
        and evidence.result_state == ResultState.VERIFIED_ABSENCE.value
        and requirement_accepts_verified_absence(requirement)
    )
    covered = list(dict.fromkeys([
        *state.covered_fields,
        *evidence.covered_fields,
        *(requirement.requested_fields if verified_absence_satisfies else []),
    ]))
    missing = [item for item in requirement.requested_fields if item not in covered]
    covered_entities = list(dict.fromkeys([
        *state.covered_entities,
        *list(evidence.matched_entities or []),
    ]))
    missing_entities = missing_requirement_entities(requirement, covered_entities)
    entities_complete = not missing_entities
    entity_field_coverage = {
        str(key): list(value)
        for key, value in dict(state.entity_field_coverage or {}).items()
    }
    field_chunk = list(evidence.covered_fields or [])
    for entity in list(evidence.matched_entities or []) or (
        [requirement.location]
        if requirement.location
        and len(_split_place_sequence(requirement.location)) <= 1
        else []
    ):
        key = str(entity or "").strip()
        if not key:
            continue
        prior = list(entity_field_coverage.get(key.casefold()) or entity_field_coverage.get(key) or [])
        merged = list(dict.fromkeys([*prior, *field_chunk]))
        entity_field_coverage[key.casefold()] = merged
    # Successful structured hits are never discarded from the passage ledger.
    success_chunk = (
        str(evidence.execution_status or "") == "success"
        and str(evidence.result_state or "") in USABLE_RESULT_STATES
        and bool(str(evidence.passage or "").strip())
    )
    passage = summarize_tool_evidence_passage(evidence.tool_name, evidence.passage) if success_chunk else ""
    passages = list(dict.fromkeys([
        *state.evidence_passages,
        *([passage] if passage else []),
    ]))[:12]
    # Multi-ToolRun accumulation: once fields + all entities are covered by usable
    # chunks, satisfy even if the latest chunk alone does not list every entity.
    has_successful_evidence = bool(
        evidence.usable
        or evidence.covered_fields
        or evidence.matched_entities
        or state.covered_fields
        or state.covered_entities
        or state.evidence_passages
        or passages
    ) and (
        str(evidence.execution_status or "") == "success"
        or bool(state.tool_run_ids)
        or bool(evidence.tool_run_id)
    )
    accumulated_usable = bool(evidence.usable) or (
        bool(state.evidence_ids or state.tool_run_ids)
        and not missing
        and entities_complete
        and bool(evidence.covered_fields or evidence.matched_entities or covered)
        and str(evidence.execution_status or "") == "success"
        and str(evidence.result_state or "") in USABLE_RESULT_STATES
    )
    exhausted = bool(
        budget
        and len(
            state.epoch_attempt_ids
            or (state.attempt_ids if int(state.recovery_epoch) == 0 else [])
        ) >= budget.max_attempts_per_requirement
    )
    recovery_strategy = next_recovery_strategy(
        state.model_copy(update={
            "missing_fields": missing,
            "missing_entities": missing_entities,
            "covered_fields": covered,
            "covered_entities": covered_entities,
        }),
        requirement,
    )
    recommended = recommended_tools_for_recovery(
        recovery_strategy, requirement=requirement, available_tools=available_tools
    )
    if verified_absence_satisfies and not evidence.contradictions:
        status = RequirementStatus.SATISFIED
        terminal_reason = "authoritative_verified_absence"
    elif accumulated_usable and not missing and entities_complete and not evidence.contradictions:
        # Retrieval satisfaction requires accepted evidence linked to this ToolRun.
        if requirement_requires_verified_tool_evidence(requirement) and not (
            str(evidence.tool_run_id or "").strip()
            or str(evidence.evidence_id or "").strip()
            or state.tool_run_ids
            or state.evidence_ids
        ):
            status, terminal_reason = RequirementStatus.WEAK, "satisfaction_blocked_without_tool_linkage"
        else:
            status, terminal_reason = RequirementStatus.SATISFIED, "verified_evidence_covered_requirement"
    elif evidence.contradictions and not exhausted:
        status, terminal_reason = RequirementStatus.WEAK, "contradictory_evidence_requires_resolution"
    elif evidence.execution_status == "blocked":
        status, terminal_reason = RequirementStatus.BLOCKED, evidence.diagnostic_code
    elif evidence.result_state in {"provider_unavailable", "unsupported_intent"} and exhausted:
        status, terminal_reason = RequirementStatus.UNAVAILABLE, evidence.diagnostic_code
    elif exhausted and has_successful_evidence and (covered or covered_entities or passages):
        # Budget ended, but successful ToolOutcomes exist — terminal partial, not "no evidence".
        status = RequirementStatus.EXHAUSTED
        terminal_reason = (
            "partial_verified_evidence_budget_exhausted"
            if (missing or missing_entities)
            else "verified_evidence_covered_requirement"
        )
        if not missing and entities_complete and not evidence.contradictions:
            status, terminal_reason = (
                RequirementStatus.SATISFIED,
                "verified_evidence_covered_requirement",
            )
    elif exhausted and not has_successful_evidence:
        status, terminal_reason = RequirementStatus.EXHAUSTED, evidence.diagnostic_code
    elif exhausted:
        # Successful runs exist but produced no field/entity credit — still preserve as partial.
        status, terminal_reason = (
            RequirementStatus.EXHAUSTED,
            "partial_verified_evidence_budget_exhausted"
            if success_chunk else evidence.diagnostic_code,
        )
    else:
        if not entities_complete and (evidence.usable or evidence.matched_entities or success_chunk):
            status, terminal_reason = RequirementStatus.WEAK, "entities_incomplete"
        elif missing and (evidence.usable or evidence.covered_fields or success_chunk):
            status, terminal_reason = RequirementStatus.WEAK, "requested_fields_not_covered"
        else:
            status, terminal_reason = RequirementStatus.WEAK, evidence.diagnostic_code
    updated_state = state.model_copy(update={
        "status": status,
        "covered_fields": covered,
        "missing_fields": missing,
        "covered_entities": covered_entities,
        "missing_entities": missing_entities,
        "entity_field_coverage": entity_field_coverage,
        "tool_run_ids": list(dict.fromkeys([*state.tool_run_ids, evidence.tool_run_id])),
        "evidence_ids": list(dict.fromkeys([*state.evidence_ids, evidence.evidence_id])),
        "artifact_ids": list(dict.fromkeys([*state.artifact_ids, artifact_id] if artifact_id else state.artifact_ids)),
        "evidence_passages": passages,
        "source_count": max(state.source_count, len(evidence.source_urls)),
        "contradictions": list(dict.fromkeys([*state.contradictions, *evidence.contradictions])),
        "last_strategy": recovery_strategy if status == RequirementStatus.WEAK else state.last_strategy,
        "recommended_tools": recommended if status == RequirementStatus.WEAK else state.recommended_tools,
        "terminal_reason": terminal_reason,
        "updated_at": time.time(),
    })
    log_requirement_status_transition(
        requirement,
        state,
        updated_state,
        source="apply_evidence_to_state",
        reason_code=terminal_reason,
    )
    return enforce_tool_backed_satisfaction(
        requirement, updated_state, source="apply_evidence_to_state"
    )


def requirement_has_verified_evidence(state: RequirementState) -> bool:
    """Tool-backed work requires one durable execution/evidence owner."""
    return any(
        str(item or "").strip()
        for item in [
            *list(state.evidence_ids or []),
            *list(state.tool_run_ids or []),
            *list(state.specialist_outcome_ids or []),
        ]
    )


def apply_specialist_outcome_to_state(
    requirement: TurnRequirement,
    state: RequirementState,
    *,
    specialist_run_id: str,
    specialist_outcome_id: str = "",
    completed: bool,
    verified: bool,
    failure_code: str = "",
    failure_message: str = "",
) -> RequirementState:
    """Project specialist execution truth into the owning requirement.

    The specialist does not finalize the TaskRun. It only supplies one durable
    outcome for the runtime's existing RequirementCompletionEvaluator.
    """

    if requirement.kind != RequirementKind.SPECIALIST:
        raise ValueError("Specialist outcome cannot satisfy a non-specialist requirement")
    run_ids = list(dict.fromkeys([
        *list(state.specialist_run_ids or []),
        str(specialist_run_id or "").strip(),
    ]))
    run_ids = [item for item in run_ids if item]
    outcome_ids = list(dict.fromkeys([
        *list(state.specialist_outcome_ids or []),
        str(specialist_outcome_id or "").strip(),
    ]))
    outcome_ids = [item for item in outcome_ids if item]
    if completed and verified and outcome_ids:
        status = RequirementStatus.SATISFIED
        terminal_reason = "verified_specialist_outcome"
        covered_fields = list(requirement.requested_fields)
        missing_fields: list[str] = []
    else:
        status = RequirementStatus.EXHAUSTED
        terminal_reason = str(failure_code or "specialist_execution_failed")[:160]
        covered_fields = list(state.covered_fields)
        missing_fields = list(requirement.requested_fields)
    updated = state.model_copy(update={
        "status": status,
        "specialist_run_ids": run_ids,
        "specialist_outcome_ids": outcome_ids,
        "covered_fields": covered_fields,
        "missing_fields": missing_fields,
        "evidence_passages": [
            item for item in list(dict.fromkeys([
                *list(state.evidence_passages or []),
                str(failure_message or "").strip()[:1200],
            ])) if item
        ][-12:],
        "terminal_reason": terminal_reason,
        "updated_at": time.time(),
    })
    log_requirement_status_transition(
        requirement,
        state,
        updated,
        source="apply_specialist_outcome_to_state",
        reason_code=terminal_reason,
    )
    return updated


def log_requirement_status_transition(
    requirement: TurnRequirement,
    previous: RequirementState | None,
    updated: RequirementState,
    *,
    source: str,
    reason_code: str = "",
) -> None:
    """Structured audit log for every requirement status change."""

    prev_status = str(getattr(previous, "status", None) or "missing")
    new_status = str(updated.status or "")
    if previous is not None and prev_status == new_status:
        return
    logger.info(
        "requirement_status_transition req={} kind={} {} -> {} reason={} source={} "
        "evidence_ids={} tool_run_ids={} covered_fields={} missing_fields={} "
        "covered_entities={} missing_entities={}",
        requirement.requirement_id,
        requirement.kind.value if hasattr(requirement.kind, "value") else requirement.kind,
        prev_status,
        new_status,
        reason_code or updated.terminal_reason or "",
        source,
        list(updated.evidence_ids or [])[:8],
        list(updated.tool_run_ids or [])[:8],
        list(updated.covered_fields or [])[:12],
        list(updated.missing_fields or [])[:12],
        list(updated.covered_entities or [])[:8],
        list(getattr(updated, "missing_entities", None) or [])[:8],
    )


def enforce_tool_backed_satisfaction(
    requirement: TurnRequirement,
    state: RequirementState,
    *,
    source: str,
) -> RequirementState:
    """Hard reject SATISFIED on tool-backed requirements without accepted evidence."""

    if state.status != RequirementStatus.SATISFIED:
        return state
    if not requirement_requires_verified_tool_evidence(requirement):
        return state
    if requirement_has_verified_evidence(state):
        return state
    demoted = state.model_copy(update={
        "status": RequirementStatus.PENDING,
        "covered_fields": [],
        "missing_fields": list(requirement.requested_fields),
        "covered_entities": [],
        "missing_entities": list(requirement.entities),
        "tool_run_ids": [],
        "evidence_ids": [],
        "terminal_reason": "rejected_satisfied_without_tool_evidence",
        "updated_at": time.time(),
    })
    log_requirement_status_transition(
        requirement,
        state,
        demoted,
        source=source,
        reason_code="rejected_satisfied_without_tool_evidence",
    )
    logger.error(
        "Rejected illegal retrieval satisfaction without evidence: req={} source={}",
        requirement.requirement_id,
        source,
    )
    return demoted


_LIVE_TOOL_EVIDENCE_RE = re.compile(
    r"(?i)\b(?:"
    r"weather|forecast|temperature|humidity|conditions|"
    r"score|scores|standings|fixture|fixtures|match\b|game\b|"
    r"search|look\s*up|lookup|research|news|headline|"
    r"price|stock|bitcoin|flight|schedule"
    r")\b"
)
_EXPLICIT_SOURCE_REQUEST_RE = re.compile(
    r"(?i)\b(?:cite|citation|citations|with\s+sources?|provide\s+sources?|"
    r"include\s+sources?|show\s+sources?|find\s+sources?)\b"
)
_FRESH_SOFTWARE_EVIDENCE_RE = re.compile(
    r"(?i)\b(?:latest|current|newest|most\s+recent|stable)\b.{0,80}"
    r"\b(?:version|release|sdk|package|library|framework|software|documentation)\b|"
    r"\b(?:version|release|sdk|package|library|framework|software|documentation)\b"
    r".{0,80}\b(?:latest|current|newest|most\s+recent|stable)\b"
)


def requirement_requires_verified_tool_evidence(requirement: TurnRequirement) -> bool:
    """True when this requirement cannot complete from conversation/memory alone.

    Kind is authoritative when correct. Objectives that clearly request live
    weather/search data are also treated as tool-backed so a coarse
    answer_only mislabel cannot mark them complete before any ToolRun.
    """

    if requirement.kind in {
        RequirementKind.RETRIEVAL,
        RequirementKind.CALCULATION,
        RequirementKind.LOCAL_CONTEXT,
        RequirementKind.SPECIALIST,
    }:
        return True
    if requirement.kind == RequirementKind.MEMORY:
        return False
    if requirement.kind == RequirementKind.ANSWER_ONLY:
        blob = " ".join([
            requirement.objective,
            *requirement.entities,
            *requirement.requested_fields,
            *requirement.acceptance_criteria,
            requirement.location,
        ])
        if (
            _LIVE_TOOL_EVIDENCE_RE.search(blob)
            or _FRESH_SOFTWARE_EVIDENCE_RE.search(blob)
            or _EXPLICIT_SOURCE_REQUEST_RE.search(blob)
        ):
            return True
    return False


def rekind_misclassified_live_requirements(
    requirements: Iterable[TurnRequirement],
) -> list[TurnRequirement]:
    """Upgrade mislabeled live objectives to retrieval before seed/completion."""

    fixed: list[TurnRequirement] = []
    for requirement in requirements:
        if (
            requirement.kind == RequirementKind.ANSWER_ONLY
            and requirement_requires_verified_tool_evidence(requirement)
        ):
            fields = list(requirement.requested_fields) or infer_requested_fields(
                requirement.objective
            )
            fixed.append(requirement.model_copy(update={
                "kind": RequirementKind.RETRIEVAL,
                "requested_fields": fields,
                "acceptance_criteria": list(dict.fromkeys([
                    *requirement.acceptance_criteria,
                    "Runtime-verified evidence must contain the requested information.",
                ])),
            }))
        else:
            fixed.append(requirement)
    return fixed


def demote_unverified_retrieval_states(
    requirements: Iterable[TurnRequirement],
    states: Mapping[str, RequirementState],
) -> dict[str, RequirementState]:
    """Safety net: never keep tool-backed work satisfied without verified evidence."""
    updated = {key: value.model_copy(deep=True) for key, value in states.items()}
    for requirement in requirements:
        state = updated.get(requirement.requirement_id)
        if state is None:
            continue
        if not requirement_requires_verified_tool_evidence(requirement):
            continue
        if state.status != RequirementStatus.SATISFIED:
            continue
        if requirement_has_verified_evidence(state):
            continue
        updated[requirement.requirement_id] = state.model_copy(update={
            "status": RequirementStatus.PENDING,
            "covered_fields": [],
            "missing_fields": list(requirement.requested_fields),
            "covered_entities": [],
            "missing_entities": list(requirement.entities),
            "tool_run_ids": [],
            "evidence_ids": [],
            "terminal_reason": "demoted_unverified_retrieval_satisfaction",
            "updated_at": time.time(),
        })
    return updated


def seed_context_requirements(
    requirements: Iterable[TurnRequirement],
    states: Mapping[str, RequirementState],
    *,
    relevant_memory: Iterable[Mapping[str, Any]] = (),
    local_context_available: bool = False,
    local_context_text: str = "",
    available_tool_names: Iterable[str] = (),
) -> dict[str, RequirementState]:
    """Seed non-tool context only. Retrieval stays pending until ToolRun evidence exists."""
    updated = demote_unverified_retrieval_states(requirements, states)
    memory_rows = [
        item for item in list(relevant_memory or [])
        if str(item.get("type") or "").casefold() != "scoped_compiled_context"
    ]
    available_names = {
        str(item or "").strip() for item in available_tool_names if str(item or "").strip()
    }
    research_tools_present = any(
        name in available_names
        or name.startswith(("web_", "safe_web", "weather_", "sports_", "browse_"))
        for name in available_names
    )
    for requirement in requirements:
        state = updated.get(requirement.requirement_id) or RequirementState(
            requirement_id=requirement.requirement_id
        )
        # Answer-only is model-response-backed. Leave as satisfied so mixed turns can
        # still complete the conversational branches while retrieval stays open.
        # Live weather/search objectives mislabeled as answer_only must NOT auto-satisfy.
        if requirement.kind == RequirementKind.ANSWER_ONLY:
            if requirement_requires_verified_tool_evidence(requirement):
                updated[requirement.requirement_id] = state.model_copy(update={
                    "status": RequirementStatus.PENDING,
                    "covered_fields": [],
                    "missing_fields": list(requirement.requested_fields)
                    or infer_requested_fields(requirement.objective),
                    "terminal_reason": "live_objective_misclassified_as_answer_only",
                    "updated_at": time.time(),
                })
                continue
            updated[requirement.requirement_id] = state.model_copy(update={
                "status": RequirementStatus.SATISFIED,
                "covered_fields": list(requirement.requested_fields),
                "missing_fields": [],
                "terminal_reason": "response_generation_only",
                "updated_at": time.time(),
            })
            continue
        # Retrieval is never satisfied by seed alone.
        if requirement.kind == RequirementKind.RETRIEVAL:
            if state.status == RequirementStatus.SATISFIED and not requirement_has_verified_evidence(state):
                updated[requirement.requirement_id] = state.model_copy(update={
                    "status": RequirementStatus.PENDING,
                    "covered_fields": [],
                    "missing_fields": list(requirement.requested_fields),
                    "terminal_reason": "",
                    "updated_at": time.time(),
                })
                state = updated[requirement.requirement_id]
            if state.status in {
                RequirementStatus.SATISFIED,
                RequirementStatus.UNAVAILABLE,
                RequirementStatus.BLOCKED,
                RequirementStatus.EXHAUSTED,
            }:
                continue
            compatible_tool_available = any(
                tool_matches_requirement(name, requirement) for name in available_names
            )
            if not compatible_tool_available and not research_tools_present:
                updated[requirement.requirement_id] = state.model_copy(update={
                    "status": RequirementStatus.UNAVAILABLE,
                    "terminal_reason": "no_authorized_capability_available",
                    "updated_at": time.time(),
                })
            # Otherwise leave pending/active/weak so call_tool remains legal.
            continue
        if requirement.kind == RequirementKind.SPECIALIST:
            if state.status == RequirementStatus.SATISFIED and not state.specialist_outcome_ids:
                updated[requirement.requirement_id] = state.model_copy(update={
                    "status": RequirementStatus.PENDING,
                    "covered_fields": [],
                    "missing_fields": list(requirement.requested_fields),
                    "terminal_reason": "specialist_outcome_missing",
                    "updated_at": time.time(),
                })
            # Availability is owned by the specialist runtime catalog. Context
            # seeding and individual ToolRegistry entries cannot satisfy it.
            continue
        if state.status == RequirementStatus.SATISFIED:
            continue
        available = False
        if requirement.kind == RequirementKind.MEMORY and memory_rows:
            # Require at least one non-empty memory row; profile/list rows are enough
            # for name/preference style questions. Do not invent coverage without rows.
            available = any(str(item.get("content") or item.get("text") or "").strip() for item in memory_rows)
        if requirement.kind == RequirementKind.LOCAL_CONTEXT and local_context_available:
            context_low = str(local_context_text or "").casefold()
            if not requirement.requested_fields:
                # Empty field contracts are not a free pass — require some local text.
                available = bool(context_low.strip())
            else:
                context_fields = [
                    field for field in requirement.requested_fields
                    if any(
                        token in context_low
                        for token in re.findall(r"[a-z0-9]+", field.casefold())
                        if len(token) > 2
                    )
                ]
                available = len(context_fields) == len(requirement.requested_fields)
        if available:
            updated[requirement.requirement_id] = state.model_copy(update={
                "status": RequirementStatus.SATISFIED,
                "covered_fields": list(requirement.requested_fields),
                "missing_fields": [],
                "terminal_reason": "authorized_runtime_context_available",
                "updated_at": time.time(),
            })
            continue
        if requirement.kind == RequirementKind.CALCULATION:
            compatible_tool_available = any(
                tool_matches_requirement(name, requirement) for name in available_names
            )
            if not compatible_tool_available:
                updated[requirement.requirement_id] = state.model_copy(update={
                    "status": RequirementStatus.UNAVAILABLE,
                    "terminal_reason": "no_authorized_capability_available",
                    "updated_at": time.time(),
                })
            continue
        if requirement.kind == RequirementKind.MEMORY and not memory_rows:
            updated[requirement.requirement_id] = state.model_copy(update={
                "status": RequirementStatus.UNAVAILABLE,
                "terminal_reason": "authorized_memory_projection_empty",
                "updated_at": time.time(),
            })
    return demote_unverified_retrieval_states(requirements, updated)


def reopen_incomplete_requirements(
    requirements: Iterable[TurnRequirement],
    states: Mapping[str, RequirementState],
    *,
    recovery_epoch: int,
    include_open: bool = False,
) -> tuple[dict[str, RequirementState], list[str]]:
    """Open a new bounded recovery epoch without discarding prior evidence."""

    rows = list(requirements)
    updated = reconcile_requirement_states(rows, states)
    reopened: list[str] = []
    reopenable = {
        RequirementStatus.EXHAUSTED,
        RequirementStatus.UNAVAILABLE,
    }
    if include_open:
        reopenable.update({
            RequirementStatus.PENDING,
            RequirementStatus.ACTIVE,
            RequirementStatus.WEAK,
        })
    for requirement in rows:
        state = updated[requirement.requirement_id]
        if not requirement.required or state.status not in reopenable:
            continue
        reopened.append(requirement.requirement_id)
        updated[requirement.requirement_id] = state.model_copy(update={
            "status": (
                RequirementStatus.WEAK
                if state.evidence_ids or state.tool_run_ids or state.covered_fields
                else RequirementStatus.PENDING
            ),
            "recovery_epoch": int(recovery_epoch),
            "epoch_attempt_ids": [],
            "epoch_external_call_count": 0,
            "retry_count": 0,
            "terminal_reason": "",
            "updated_at": time.time(),
        })
    return updated, reopened


class TaskRunScheduler:
    """Sole deterministic owner of TaskRun liveness and next work selection."""

    @staticmethod
    def advance(
        requirements: Iterable[TurnRequirement],
        states: Mapping[str, RequirementState],
        *,
        budget: ResearchBudgetPolicy,
        capabilities: Iterable[CapabilityDescriptor] = (),
        missing_inputs: Iterable[str] = (),
        pending_approval: bool = False,
        waiting_external: bool = False,
        recovery_epoch: int = 0,
        epoch_started_at: float = 0.0,
        now: Optional[float] = None,
    ) -> TaskRunAdvanceDecision:
        rows = list(requirements)
        missing = list(dict.fromkeys(
            str(item) for item in missing_inputs if str(item).strip()
        ))
        current = demote_unverified_retrieval_states(
            rows,
            reconcile_requirement_states(rows, states),
        )
        clock = float(now if now is not None else time.time())
        elapsed_exhausted = bool(
            epoch_started_at
            and clock - float(epoch_started_at) >= budget.max_time_seconds
        )
        epoch_calls = sum(
            (
                state.epoch_external_call_count
                or (state.external_call_count if int(recovery_epoch) == 0 else 0)
            )
            for state in current.values()
            if int(state.recovery_epoch) == int(recovery_epoch)
        )
        global_exhausted = elapsed_exhausted or epoch_calls >= budget.max_external_calls
        for requirement in rows:
            state = current[requirement.requirement_id]
            if state.status not in {
                RequirementStatus.PENDING,
                RequirementStatus.ACTIVE,
                RequirementStatus.WEAK,
            }:
                continue
            epoch_attempts = (
                len(
                    state.epoch_attempt_ids
                    or (state.attempt_ids if int(recovery_epoch) == 0 else [])
                )
                if int(state.recovery_epoch) == int(recovery_epoch)
                else 0
            )
            if global_exhausted or epoch_attempts >= budget.max_attempts_per_requirement:
                current[requirement.requirement_id] = state.model_copy(update={
                    "status": RequirementStatus.EXHAUSTED,
                    "terminal_reason": (
                        "research_time_budget_exhausted"
                        if elapsed_exhausted
                        else "external_call_budget_exhausted"
                        if epoch_calls >= budget.max_external_calls
                        else "requirement_attempt_budget_exhausted"
                    ),
                    "updated_at": clock,
                })

        completion = RequirementCompletionEvaluator.evaluate(
            rows,
            current,
            missing_inputs=missing,
            pending_approval=pending_approval,
        )
        if pending_approval:
            return TaskRunAdvanceDecision(
                next_action=TaskRunNextAction.WAIT_FOR_APPROVAL,
                reason_code="approval_pending",
                completion=completion,
                requirement_states=current,
                evaluated_at=clock,
            )
        if missing:
            return TaskRunAdvanceDecision(
                next_action=TaskRunNextAction.WAIT_FOR_USER,
                reason_code="user_input_required",
                completion=completion,
                requirement_states=current,
                evaluated_at=clock,
            )
        if completion.finalizable:
            return TaskRunAdvanceDecision(
                next_action=TaskRunNextAction.FINALIZE,
                reason_code=completion.reason_code,
                completion=completion,
                requirement_states=current,
                evaluated_at=clock,
            )
        if waiting_external:
            return TaskRunAdvanceDecision(
                next_action=TaskRunNextAction.WAIT_FOR_EXTERNAL_RESULT,
                reason_code="external_result_pending",
                completion=completion,
                requirement_states=current,
                evaluated_at=clock,
            )

        descriptors = [
            item for item in capabilities
            if item.available
            and item.health.casefold() not in {"unavailable", "failed", "offline", "disabled"}
        ]
        # If a requirement has no eligible current capability, terminalize only
        # that requirement and continue selecting independent work.
        for _ in range(max(1, len(rows) + 1)):
            active = choose_active_requirement(rows, current)
            if active is None:
                break
            ranked = sorted(
                (
                    (capability_fit_score(item.tool_name, active, item), item)
                    for item in descriptors
                ),
                key=lambda row: (-row[0], row[1].tool_name),
            )
            ranked = [row for row in ranked if row[0] > 0]
            best_score = ranked[0][0] if ranked else 0
            eligible = [
                item for score, item in ranked
                if score >= max(60, best_score - 25)
            ]
            if eligible:
                state = current[active.requirement_id]
                strategy = next_recovery_strategy(state, active)
                recommended = recommended_tools_for_recovery(
                    strategy,
                    requirement=active,
                    available_tools=[item.tool_name for item in eligible],
                )
                names = [item.tool_name for item in eligible]
                names = list(dict.fromkeys([*recommended, *names]))
                return TaskRunAdvanceDecision(
                    next_action=TaskRunNextAction.RUN_TOOL,
                    reason_code="actionable_requirement_selected",
                    active_requirement_id=active.requirement_id,
                    recovery_strategy=strategy,
                    eligible_tool_names=names,
                    preferred_tool_name=names[0] if names else "",
                    completion=completion,
                    requirement_states=current,
                    evaluated_at=clock,
                )
            state = current[active.requirement_id]
            if active.kind == RequirementKind.SPECIALIST:
                return TaskRunAdvanceDecision(
                    next_action=TaskRunNextAction.WAIT_FOR_EXTERNAL_RESULT,
                    reason_code="specialist_runtime_not_settled",
                    active_requirement_id=active.requirement_id,
                    completion=completion,
                    requirement_states=current,
                    evaluated_at=clock,
                )
            current[active.requirement_id] = state.model_copy(update={
                "status": RequirementStatus.UNAVAILABLE,
                "terminal_reason": "no_eligible_current_capability",
                "updated_at": clock,
            })
            completion = RequirementCompletionEvaluator.evaluate(
                rows,
                current,
                missing_inputs=missing,
                pending_approval=False,
            )
            if completion.finalizable:
                return TaskRunAdvanceDecision(
                    next_action=TaskRunNextAction.FINALIZE,
                    reason_code=completion.reason_code,
                    completion=completion,
                    requirement_states=current,
                    evaluated_at=clock,
                )

        return TaskRunAdvanceDecision(
            next_action=TaskRunNextAction.HARD_FAILURE,
            reason_code="no_actionable_requirement_in_nonfinalizable_state",
            completion=completion,
            requirement_states=current,
            evaluated_at=clock,
        )


class RequirementCompletionEvaluator:
    """The sole research-sufficiency evaluator; it never writes TaskRun state."""

    @staticmethod
    def evaluate(
        requirements: Iterable[TurnRequirement],
        states: Mapping[str, RequirementState],
        *,
        missing_inputs: Iterable[str] = (),
        pending_approval: bool = False,
    ) -> CompletionVerdict:
        required = [item for item in requirements if item.required]
        required_ids = [item.requirement_id for item in required]
        # Integrity: retrieval without evidence is never treated as satisfied.
        effective_states = demote_unverified_retrieval_states(requirements, states)
        satisfied = [
            item.requirement_id for item in required
            if effective_states.get(item.requirement_id)
            and effective_states[item.requirement_id].status == RequirementStatus.SATISFIED
        ]
        unresolved = [item for item in required_ids if item not in satisfied]
        states = effective_states
        terminal_statuses = {
            RequirementStatus.UNAVAILABLE, RequirementStatus.BLOCKED, RequirementStatus.EXHAUSTED
        }
        terminal_incomplete = [
            item for item in unresolved
            if states.get(item) and states[item].status in terminal_statuses
        ]
        missing = list(dict.fromkeys(str(item) for item in missing_inputs if str(item).strip()))
        if pending_approval:
            return CompletionVerdict(
                disposition=CompletionDisposition.BLOCKED,
                required_ids=required_ids, satisfied_ids=satisfied, unresolved_ids=unresolved,
                terminal_incomplete_ids=terminal_incomplete, missing_input_fields=missing,
                pending_approval=True, reason_code="approval_pending",
            )
        if missing:
            return CompletionVerdict(
                required_ids=required_ids, satisfied_ids=satisfied, unresolved_ids=unresolved,
                terminal_incomplete_ids=terminal_incomplete, missing_input_fields=missing,
                reason_code="user_input_required",
            )
        if not unresolved:
            return CompletionVerdict(
                disposition=CompletionDisposition.COMPLETE, finalizable=True,
                required_ids=required_ids, satisfied_ids=satisfied,
                reason_code="all_required_requirements_satisfied",
            )
        if len(terminal_incomplete) == len(unresolved):
            return CompletionVerdict(
                disposition=CompletionDisposition.PARTIAL, finalizable=True,
                required_ids=required_ids, satisfied_ids=satisfied, unresolved_ids=unresolved,
                terminal_incomplete_ids=terminal_incomplete,
                reason_code="recovery_exhausted_with_partial_result",
            )
        return CompletionVerdict(
            required_ids=required_ids, satisfied_ids=satisfied, unresolved_ids=unresolved,
            terminal_incomplete_ids=terminal_incomplete, reason_code="requirements_pending",
        )


def legacy_completion_would_allow(*, tool_required: bool, usable_outcome_count: int, missing_inputs: int) -> bool:
    return not missing_inputs and (not tool_required or usable_outcome_count > 0)


__all__ = [
    "CAPABILITY_SCHEMA_VERSION", "EVIDENCE_SCHEMA_VERSION", "REQUIREMENT_SCHEMA_VERSION",
    "CapabilityDescriptor", "CapabilitySnapshot", "CompletionDisposition", "CompletionVerdict",
    "EvidenceEnvelope", "RequirementCompletionEvaluator", "RequirementKind", "RequirementState",
    "RequirementStatus", "RepeatedRecoveryStrategy", "ResearchBudgetExceeded",
    "ResearchBudgetPolicy", "ResearchDepth",
    "TaskRunAdvanceDecision", "TaskRunNextAction", "TaskRunScheduler",
    "TurnRequirement", "apply_evidence_to_state", "apply_specialist_outcome_to_state",
    "begin_requirement_attempt",
    "budget_for_depth", "build_capability_snapshot", "capability_descriptor_from_tool",
    "choose_active_requirement", "compile_turn_requirements", "evidence_from_tool_outcome",
    "canonicalize_proposed_requirements", "replace_turn_requirements",
    "expand_multi_location_weather_requirements", "extract_weather_locations",
    "format_weather_live_summary", "infer_requested_fields", "matched_requirement_entities",
    "missing_requirement_entities", "recommended_tools_for_recovery",
    "rekind_misclassified_live_requirements",
    "reopen_incomplete_requirements", "requirement_accepts_verified_absence",
    "requirement_requires_verified_tool_evidence",
    "summarize_tool_evidence_passage", "verify_tool_result_semantics",
    "verified_absence_contract_is_valid",
    "initial_requirement_states", "legacy_completion_would_allow", "next_recovery_strategy",
    "demote_unverified_retrieval_states",
    "enforce_tool_backed_satisfaction",
    "log_requirement_status_transition",
    "merge_turn_requirements", "reconcile_requirement_states", "requirement_has_verified_evidence",
    "seed_context_requirements", "select_research_depth",
    "capability_fit_score", "tool_matches_requirement",
]
