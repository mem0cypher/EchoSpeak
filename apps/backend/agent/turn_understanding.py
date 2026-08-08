"""Selected-model semantic boundary for every ordinary EchoSpeak Turn.

This module performs no effects and owns no durable state.  It compiles a
bounded semantic projection and validates the selected model's structural
interpretation.  The runtime remains the only authority that may apply it.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Optional

from loguru import logger
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)

from agent.identity import EchoIdentityProjection
from agent.model_runtime import extract_json_value_once
from agent.research_runtime import (
    RequirementKind,
    TurnRequirement,
    expand_multi_location_weather_requirements,
    extract_weather_locations,
    infer_requested_fields,
)


TURN_UNDERSTANDING_VERSION = "8.0.0"


class TurnRelation(str, Enum):
    CASUAL_CONVERSATION = "casual_conversation"
    NEW_TASK = "new_task"
    CONTINUE_TASK = "continue_task"
    PROVIDE_TASK_INPUT = "provide_task_input"
    CORRECT_TASK = "correct_task"
    CANCEL_TASK = "cancel_task"
    SWITCH_TASK = "switch_task"
    RESUME_APPROVAL = "resume_approval"
    AMBIGUOUS = "ambiguous"
    BLOCKED = "blocked"


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    CANCEL = "cancel"


CAPABILITY_CATEGORIES = (
    "conversation",
    "research",
    "live_weather",
    "live_sports",
    "time",
    "calculate",
    "coding_read",
    "coding_write",
    "terminal",
    "desktop",
    "communications",
    "memory",
    "task_management",
    "voice",
    "media_generation",
)


class SuspendedTaskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    objective: str
    lifecycle_status: str
    waiting_for: list[str] = Field(default_factory=list)
    workflow_stage: str = ""
    updated_at: float = 0.0
    revision: int = 0
    legacy_untrusted: bool = False


class ApprovalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: str
    tool_name: str
    summary: str = ""
    risk_level: str = ""


class TurnUnderstandingEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: str = TURN_UNDERSTANDING_VERSION
    compiled_at: float = Field(default_factory=time.time)
    assistant_identity: EchoIdentityProjection
    latest_user_message: str
    recent_conversation: list[dict[str, str]] = Field(default_factory=list)
    reply_relationship: dict[str, str] = Field(default_factory=dict)
    project_id: str = ""
    session_id: str
    suspended_tasks: list[SuspendedTaskSummary] = Field(default_factory=list)
    active_approvals: list[ApprovalSummary] = Field(default_factory=list)
    relevant_memory: list[dict[str, Any]] = Field(default_factory=list)
    project_context: list[dict[str, Any]] = Field(default_factory=list)
    recent_verified_outcomes: list[dict[str, Any]] = Field(default_factory=list)
    entity_candidates: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "web"
    channel: str = ""
    available_capability_categories: list[str] = Field(default_factory=lambda: list(CAPABILITY_CATEGORIES))

    @field_validator("latest_user_message")
    @classmethod
    def require_message(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("latest_user_message is required")
        return text[:12000]

    @model_validator(mode="after")
    def bound_projection(self) -> "TurnUnderstandingEnvelope":
        self.recent_conversation = self.recent_conversation[-6:]
        self.suspended_tasks = self.suspended_tasks[:8]
        self.active_approvals = self.active_approvals[:8]
        self.relevant_memory = self.relevant_memory[:6]
        self.project_context = self.project_context[:2]
        self.recent_verified_outcomes = self.recent_verified_outcomes[:4]
        self.entity_candidates = self.entity_candidates[:12]
        self.available_capability_categories = [
            item for item in dict.fromkeys(self.available_capability_categories)
            if item in CAPABILITY_CATEGORIES
        ]
        return self

    def safe_diagnostics(self) -> dict[str, Any]:
        projection = self.model_dump_json(exclude={"assistant_identity"}, exclude_none=True)
        return {
            "contract_version": self.contract_version,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "source": self.source,
            "assistant_name": self.assistant_identity.assistant_name,
            "soul_sha256": self.assistant_identity.soul_sha256,
            "conversation_messages": len(self.recent_conversation),
            "candidate_task_ids": [item.task_id for item in self.suspended_tasks],
            "approval_ids": [item.approval_id for item in self.active_approvals],
            "memory_count": len(self.relevant_memory),
            "project_context_count": len(self.project_context),
            "verified_outcome_count": len(self.recent_verified_outcomes),
            "entity_candidate_count": len(self.entity_candidates),
            "block_counts": {
                "conversation": len(self.recent_conversation),
                "tasks": len(self.suspended_tasks),
                "approvals": len(self.active_approvals),
                "memory": len(self.relevant_memory),
                "project": len(self.project_context),
                "verified_outcomes": len(self.recent_verified_outcomes),
                "entities": len(self.entity_candidates),
            },
            "estimated_input_tokens": max(1, (len(projection) + 3) // 4),
            "latest_message_sha256": hashlib.sha256(self.latest_user_message.encode("utf-8")).hexdigest(),
        }


_INFORMATIONAL_CAPABILITIES = frozenset({
    "conversation",
    "research",
    "live_weather",
    "live_sports",
    "time",
    "calculate",
    "memory",
})
_TOOL_DISCOVERABLE_INFORMATION_FIELDS = frozenset({
    "specific_time",
    "exact_time",
    "event_time",
    "start_time",
    "kickoff_time",
    "tipoff_time",
    "match_time",
})


def blocking_missing_fields(fields: Iterable[str], capabilities: Iterable[str]) -> list[str]:
    """Keep user-owned missing inputs while dropping tool-discoverable facts.

    This is deliberately narrow. It applies only to purely informational work
    and exact event-time fields. Locations, recipients, paths, approval facts,
    and all inputs for mutating capabilities remain blocking.
    """
    normalized = list(dict.fromkeys(
        str(item).strip() for item in fields if str(item).strip()
    ))
    capability_set = {
        str(item).strip() for item in capabilities if str(item).strip()
    }
    if not capability_set or not capability_set.issubset(_INFORMATIONAL_CAPABILITIES):
        return normalized
    if not capability_set.intersection({"research", "live_sports"}):
        return normalized
    return [
        item for item in normalized
        if item.casefold() not in _TOOL_DISCOVERABLE_INFORMATION_FIELDS
    ]


_CONTENT_NOUNS = (
    r"list|checklist|notes?|draft|email|message|letter|paragraph|summary|outline|"
    r"brainstorm|ideas?|copy|example|plan"
)
_QUOTED_CONTENT = re.compile(r"(['\"`]).*?\1|“.*?”|‘.*?’")


def is_inert_conversational_content_request(message: str) -> bool:
    """Return True when the current instruction asks only for response content.

    The first instruction frame owns action scope. Command-shaped text inside a
    list, quotation, or draft is payload and cannot grant execution authority.
    This boundary only removes authority; it never infers or adds a capability.
    """
    lines = [line.strip() for line in str(message or "").splitlines() if line.strip()]
    if not lines:
        return False
    frame = lines[0]
    unquoted_frame = _QUOTED_CONTENT.sub(" ", frame).casefold()
    unquoted_frame = re.sub(r"\s+", " ", unquoted_frame).strip()
    add_to_content = re.search(
        rf"^(?:please\s+)?(?:add|append|insert)\b.*\b(?:to|onto)\s+(?:my|the|that|this)\s+"
        rf"(?:{_CONTENT_NOUNS})\b",
        unquoted_frame,
    )
    content_request = bool(
        re.search(
            rf"^(?:please\s+)?(?:create|make|generate|write|draft|rewrite|summari[sz]e|"
            rf"organize|brainstorm)(?:\s+me)?\s+(?:a|an|the|my|this|that)?\s*"
            rf"(?:[\w-]+\s+){{0,3}}(?:{_CONTENT_NOUNS})\b",
            unquoted_frame,
        )
        or add_to_content
        or re.search(
            r"^(?:please\s+)?(?:change|edit|update|remove|reorder|move)\s+"
            r"(?:item|number|#|the\s+last\s+one|the\s+first\s+one|these\b)",
            unquoted_frame,
        )
        or re.search(
            r"^(?:please\s+)?(?:add|append|insert)\s+"
            r"(?:this|that|another|one\s+more)(?:\s+item)?[.!?]?$",
            unquoted_frame,
        )
    )
    if not content_request:
        return False
    # Explicit promotion in the instruction frame wins. Payload on later list
    # lines is intentionally excluded from this scan.
    promotion_frame = unquoted_frame
    if add_to_content is not None:
        # In "add X to the list", X is payload even when it is not quoted.
        promotion_frame = unquoted_frame[add_to_content.end():]
    elif ":" in promotion_frame:
        # A colon after a draft/list instruction introduces inert payload.
        promotion_frame = promotion_frame.split(":", 1)[0]
    external_promotion = bool(
        re.search(
            r"\b(save|send|post|publish|print|run|execute|launch|open|upload)\b|"
            r"\bemail\s+(?:it|this|that|the)\b",
            promotion_frame,
        )
        or re.search(r"\b[\w.-]+\.(?:txt|md|json|csv|html?|css|js|ts|py|docx?|pdf)\b", promotion_frame)
        or re.search(r"\b(?:in|using|through)\s+(?:gmail|outlook|discord|telegram|whatsapp)\b", promotion_frame)
    )
    return not external_promotion


def scope_interpretation_to_current_instruction(
    interpretation: "TurnInterpretation", message: str
) -> "TurnInterpretation":
    """Fail-safe mention-versus-use projection for the validated current Turn."""
    if not is_inert_conversational_content_request(message):
        return interpretation
    constraints = list(dict.fromkeys([*interpretation.constraints, "response_only_content"]))
    return interpretation.model_copy(update={
        "requested_capabilities": ["conversation"],
        "requested_operation": "compose_response",
        "missing_fields": [],
        "constraints": constraints,
        "requirements": [TurnRequirement(
            kind=RequirementKind.ANSWER_ONLY,
            objective=str(message or "Respond to the user").strip()[:1200],
            acceptance_criteria=["Produce the requested response without external execution."],
        )],
    })


class TurnInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    _decode_diagnostics: dict[str, Any] = PrivateAttr(default_factory=dict)
    relation: TurnRelation
    selected_task_id: Optional[str] = None
    selected_approval_id: Optional[str] = None
    approval_decision: Optional[ApprovalDecision] = None
    proposed_objective: Optional[str] = None
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    referenced_entities: list[dict[str, Any]] = Field(default_factory=list)
    requested_capabilities: list[str] = Field(default_factory=list)
    requested_operation: Optional[str] = None
    constraints: list[str] = Field(default_factory=list)
    requirements: list[TurnRequirement] = Field(default_factory=list)
    clarification_question: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    candidate_alternatives: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_relationship(self) -> "TurnInterpretation":
        task_relations = {
            TurnRelation.CONTINUE_TASK,
            TurnRelation.PROVIDE_TASK_INPUT,
            TurnRelation.CORRECT_TASK,
            TurnRelation.CANCEL_TASK,
            TurnRelation.SWITCH_TASK,
        }
        selected_task_id = (self.selected_task_id or "").strip()
        if self.relation in task_relations and not selected_task_id:
            raise ValueError(f"{self.relation.value} requires selected_task_id")
        if self.relation in {
            TurnRelation.CASUAL_CONVERSATION,
            TurnRelation.NEW_TASK,
            TurnRelation.AMBIGUOUS,
            TurnRelation.BLOCKED,
            TurnRelation.RESUME_APPROVAL,
        } and selected_task_id:
            raise ValueError(f"{self.relation.value} must not select a TaskRun")
        self.selected_task_id = selected_task_id or None
        if self.relation == TurnRelation.NEW_TASK and not (self.proposed_objective or "").strip():
            raise ValueError("new_task requires proposed_objective")
        if self.relation == TurnRelation.SWITCH_TASK and not (self.proposed_objective or "").strip():
            raise ValueError("switch_task requires the replacement proposed_objective")
        selected_approval_id = (self.selected_approval_id or "").strip()
        self.selected_approval_id = selected_approval_id or None
        if self.relation == TurnRelation.RESUME_APPROVAL:
            if not self.selected_approval_id:
                raise ValueError("resume_approval requires selected_approval_id")
            if self.approval_decision not in {ApprovalDecision.APPROVE, ApprovalDecision.CANCEL}:
                raise ValueError("resume_approval requires approval_decision approve or cancel")
        elif self.selected_approval_id is not None or self.approval_decision is not None:
            raise ValueError("approval fields are only valid for resume_approval")
        self.requested_capabilities = [
            item for item in dict.fromkeys(str(value).strip() for value in self.requested_capabilities)
            if item in CAPABILITY_CATEGORIES
        ]
        self.requested_capabilities = list(dict.fromkeys([
            *self.requested_capabilities,
            *_capabilities_for_typed_operation(self.requested_operation),
        ]))
        self.missing_fields = list(dict.fromkeys(
            str(item).strip() for item in self.missing_fields if str(item).strip()
        ))
        supplied = {str(key).strip() for key in self.extracted_fields if str(key).strip()}
        if (self.requested_operation or "").strip():
            supplied.add("requested_operation")
        if (self.proposed_objective or "").strip():
            supplied.update({"objective", "proposed_objective"})
        self.missing_fields = [item for item in self.missing_fields if item not in supplied]
        self.missing_fields = blocking_missing_fields(
            self.missing_fields,
            self.requested_capabilities,
        )
        question = (self.clarification_question or "").strip()
        if self.relation == TurnRelation.AMBIGUOUS and not question:
            raise ValueError("ambiguous interpretation requires clarification_question")
        if self.relation != TurnRelation.AMBIGUOUS and question:
            raise ValueError("clarification_question is only valid for ambiguous")
        self.clarification_question = question or None
        self.candidate_alternatives = self.candidate_alternatives[:6]
        self.referenced_entities = self.referenced_entities[:16]
        self.requirements = self.requirements[:16]
        return self

    @computed_field(return_type=bool)
    @property
    def clarification_required(self) -> bool:
        """Runtime projection derived from the authoritative semantic relation."""

        return self.relation == TurnRelation.AMBIGUOUS

    def persisted_projection(self, *, message: str) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["message_sha256"] = hashlib.sha256(str(message or "").encode("utf-8")).hexdigest()
        return payload

    def safe_decode_diagnostics(self) -> dict[str, Any]:
        return dict(self._decode_diagnostics)


# Explicitly documented property-name drift only. Unknown properties deliberately
# survive this boundary so ``extra="forbid"`` rejects them during model validation.
TURN_INTERPRETATION_FIELD_ALIASES: dict[str, str] = {
    "TurnRelation": "relation",
    "turnRelation": "relation",
    "turn_relation": "relation",
    "selectedTaskId": "selected_task_id",
    "SelectedTaskId": "selected_task_id",
    "selectedApprovalId": "selected_approval_id",
    "SelectedApprovalId": "selected_approval_id",
    "approvalDecision": "approval_decision",
    "ApprovalDecision": "approval_decision",
    "proposedObjective": "proposed_objective",
    "ProposedObjective": "proposed_objective",
    "extractedFields": "extracted_fields",
    "ExtractedFields": "extracted_fields",
    "missingFields": "missing_fields",
    "MissingFields": "missing_fields",
    "missingInputs": "missing_fields",
    "requiredInputs": "missing_fields",
    "referencedEntities": "referenced_entities",
    "requestedCapabilities": "requested_capabilities",
    "capabilityHints": "requested_capabilities",
    "requestedOperation": "requested_operation",
    "turnRequirements": "requirements",
    "TurnRequirements": "requirements",
    "clarificationRequired": "clarification_required",
    "clarificationQuestion": "clarification_question",
    "candidateAlternatives": "candidate_alternatives",
}

# Accepted as typed legacy input and then discarded. Relation is the sole
# clarification owner; retaining this model-authored boolean would restore a
# contradictory second source of truth.
DERIVED_INPUT_FIELDS = frozenset({"clarification_required"})

OPTIONAL_IDENTIFIER_FIELDS = frozenset({"selected_task_id", "selected_approval_id"})


class TurnInterpretationNormalizationError(ValueError):
    """A bounded canonicalization failure with non-sensitive diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        canonical_field: str = "",
        original_keys: Optional[list[str]] = None,
    ) -> None:
        super().__init__(message)
        self.diagnostics = {
            "code": code,
            "canonical_field": canonical_field,
            "original_keys": list(original_keys or []),
        }


def _normalize_relation_value(value: Any) -> str:
    """Normalize formatting only when it resolves to an authoritative enum value."""

    if isinstance(value, TurnRelation):
        return value.value
    if not isinstance(value, str):
        raise TurnInterpretationNormalizationError(
            "relation must be a string",
            code="invalid_relation_type",
            canonical_field="relation",
        )
    candidate = value.strip()
    candidate = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", candidate)
    candidate = re.sub(r"[-\s]+", "_", candidate)
    candidate = re.sub(r"_+", "_", candidate).strip("_").casefold()
    allowed = {item.value for item in TurnRelation}
    if candidate not in allowed:
        raise TurnInterpretationNormalizationError(
            "relation does not resolve to an authoritative TurnRelation value",
            code="invalid_relation_value",
            canonical_field="relation",
        )
    return candidate


def _collision_value(value: Any) -> Any:
    """Produce a comparison-only primitive projection without changing payload meaning."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_collision_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _collision_value(item) for key, item in value.items()}
    return value


def decode_turn_interpretation_payload(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Canonical provider-independent decoder before strict Pydantic validation."""

    if not isinstance(payload, dict):
        raise TurnInterpretationNormalizationError(
            "TurnInterpretation payload must be an object",
            code="invalid_payload_type",
        )
    if len(payload) > 64:
        raise TurnInterpretationNormalizationError(
            "TurnInterpretation payload has too many properties",
            code="property_limit_exceeded",
        )

    grouped: dict[str, list[tuple[str, Any]]] = {}
    identifier_normalizations: list[str] = []
    derived_fields_ignored: list[str] = []
    for original_key, original_value in payload.items():
        if not isinstance(original_key, str) or not original_key or len(original_key) > 128:
            raise TurnInterpretationNormalizationError(
                "TurnInterpretation property names must be bounded strings",
                code="invalid_property_name",
            )
        canonical_key = TURN_INTERPRETATION_FIELD_ALIASES.get(original_key, original_key)
        if canonical_key in DERIVED_INPUT_FIELDS:
            if original_value is not None and type(original_value) is not bool:
                raise TurnInterpretationNormalizationError(
                    "derived clarification input must be a boolean or null",
                    code="invalid_derived_field_type",
                    canonical_field=canonical_key,
                    original_keys=[original_key],
                )
            derived_fields_ignored.append(original_key)
            continue
        if canonical_key == "relation":
            value = _normalize_relation_value(original_value)
        elif canonical_key in OPTIONAL_IDENTIFIER_FIELDS and isinstance(original_value, str):
            value = original_value.strip() or None
            if value != original_value:
                identifier_normalizations.append(canonical_key)
        else:
            value = original_value
        grouped.setdefault(canonical_key, []).append((original_key, value))

    canonical: dict[str, Any] = {}
    folds: list[dict[str, str]] = []
    relation_format_normalized = False
    for canonical_key, entries in grouped.items():
        # Prefer an explicitly emitted canonical property after proving all values agree.
        selected = next((entry for entry in entries if entry[0] == canonical_key), entries[0])
        selected_comparison = _collision_value(selected[1])
        if any(
            type(_collision_value(value)) is not type(selected_comparison)
            or _collision_value(value) != selected_comparison
            for _key, value in entries
        ):
            keys = [key for key, _value in entries]
            error = TurnInterpretationNormalizationError(
                f"conflicting properties map to {canonical_key}",
                code="alias_collision",
                canonical_field=canonical_key,
                original_keys=keys,
            )
            logger.warning("TurnInterpretation normalization rejected ambiguity: {}", error.diagnostics)
            raise error
        canonical[canonical_key] = selected[1]
        for original_key, _value in entries:
            if original_key != canonical_key:
                folds.append({"original_key": original_key, "canonical_key": canonical_key})
        if canonical_key == "relation":
            relation_format_normalized = any(
                not isinstance(payload[key], TurnRelation) and str(payload[key]) != str(value)
                for key, value in entries
            )

    return canonical, {
        "decoder_version": TURN_UNDERSTANDING_VERSION,
        "alias_folds": folds,
        "relation_format_normalized": relation_format_normalized,
        "optional_identifier_normalized": sorted(set(identifier_normalizations)),
        "derived_fields_ignored": sorted(set(derived_fields_ignored)),
        "input_property_count": len(payload),
    }


def extract_turn_interpretation_payload(raw: Any) -> dict[str, Any]:
    """Read only the documented structured/message content channel from a model result."""

    if isinstance(raw, dict):
        # LangChain ``with_structured_output(..., include_raw=True)`` envelope.
        if "parsed" in raw and ("raw" in raw or "parsing_error" in raw):
            parsed = raw.get("parsed")
            if isinstance(parsed, BaseModel):
                return parsed.model_dump(mode="python")
            if isinstance(parsed, dict):
                return parsed
            raw = raw.get("raw")
        else:
            return raw
    if hasattr(raw, "content"):
        content = getattr(raw, "content")
    elif isinstance(raw, BaseModel):
        return raw.model_dump(mode="python")
    else:
        content = raw
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        json_objects: list[dict[str, Any]] = []
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
                continue
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").casefold()
            if block_type in {"json", "output_json"} and isinstance(block.get("json"), dict):
                json_objects.append(block["json"])
            elif block_type in {"text", "output_text"} and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
        if len(json_objects) == 1 and not text_parts:
            return json_objects[0]
        if json_objects:
            raise ValueError("multiple or mixed structured response channels are ambiguous")
        content = "\n".join(text_parts)
    return extract_json_value_once(content, expected=dict)


def classify_extraction_failure(raw: Any, error: BaseException) -> str:
    """Return a safe structural code without persisting model content/reasoning."""
    content: Any = getattr(raw, "content", raw)
    reasoning = ""
    try:
        additional = getattr(raw, "additional_kwargs", {}) or {}
        reasoning = str(additional.get("reasoning_content") or "")
    except Exception:
        reasoning = ""
    text = content if isinstance(content, str) else ""
    if not text.strip():
        return "reasoning_only_response" if reasoning.strip() else "empty_response"
    if "Malformed JSON" in str(error) or (
        "{" in text and text.count("{") > text.count("}")
    ):
        return "truncated_json"
    if "No JSON" in str(error):
        return "no_json_object"
    return "response_extraction_failed"


def turn_interpretation_model_schema() -> dict[str, Any]:
    """Derive the compact model-facing schema from the authoritative Pydantic contract."""

    source = TurnInterpretation.model_json_schema()
    definitions = dict(source.get("$defs") or {})

    def clean(node: Any) -> Any:
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        if isinstance(node.get("$ref"), str) and node["$ref"].startswith("#/$defs/"):
            name = node["$ref"].rsplit("/", 1)[-1]
            resolved = dict(definitions.get(name) or {})
            resolved.update({key: value for key, value in node.items() if key != "$ref"})
            return clean(resolved)
        return {
            key: clean(value)
            for key, value in node.items()
            if key not in {"title", "$defs", "definitions"}
        }

    schema = clean(source)
    schema["additionalProperties"] = False
    schema["properties"]["relation"] = {
        "type": "string",
        "enum": [item.value for item in TurnRelation],
        "description": "Authoritative semantic relation for this Turn.",
    }
    schema["properties"]["proposed_objective"]["description"] = (
        "Required non-null concise objective copied from the user's request when relation is new_task "
        "or switch_task; otherwise null or omitted."
    )
    schema["properties"]["selected_task_id"]["description"] = (
        "Required exact supplied TaskRun id for continue_task, provide_task_input, correct_task, "
        "cancel_task, or switch_task; otherwise null or omitted."
    )
    schema["properties"]["selected_approval_id"]["description"] = (
        "Required exact supplied approval id only for resume_approval; otherwise null or omitted."
    )
    schema["properties"]["requirements"]["description"] = (
        "Independent user-requested parts. Preserve memory, local context, calculations, and each search "
        "as separate objects. Use an empty list only for controls or genuine casual conversation."
    )
    # Discriminated clarification contract (documented for models; enforced in validators
    # and repair). Ambiguous requires a non-empty clarification_question; every other
    # relation must omit it or set null.
    props = dict(schema.get("properties") or {})
    if "clarification_question" in props:
        props["clarification_question"] = {
            **dict(props.get("clarification_question") or {}),
            "description": (
                "Required non-empty string only when relation=ambiguous. "
                "For every other relation this MUST be null or omitted. "
                "Never set a clarification question on new_task, continue_task, or casual_conversation."
            ),
        }
        schema["properties"] = props
    schema["allOf"] = [
        {
            "if": {"properties": {"relation": {"const": "ambiguous"}}, "required": ["relation"]},
            "then": {
                "required": ["clarification_question"],
                "properties": {
                    "clarification_question": {
                        "type": "string",
                        "minLength": 1,
                        "description": "User-facing clarification for an ambiguous relation.",
                    }
                },
            },
        },
        {
            "if": {
                "properties": {
                    "relation": {
                        "enum": [
                            item.value for item in TurnRelation if item != TurnRelation.AMBIGUOUS
                        ]
                    }
                },
                "required": ["relation"],
            },
            "then": {
                "properties": {
                    "clarification_question": {
                        "anyOf": [{"type": "null"}, {"type": "string", "maxLength": 0}],
                    }
                },
            },
        },
    ]
    schema["examples"] = [TurnInterpretation(
        relation=TurnRelation.CASUAL_CONVERSATION,
        requested_capabilities=["conversation"],
        requirements=[TurnRequirement(
            kind=RequirementKind.ANSWER_ONLY,
            objective="Respond conversationally to the user.",
        )],
        confidence=0.99,
    ).model_dump(mode="json", exclude={"clarification_required"})]
    return schema


class TurnUnderstandingError(RuntimeError):
    def __init__(self, message: str, *, diagnostics: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.diagnostics = dict(diagnostics or {})


class TurnUnderstandingProviderError(TurnUnderstandingError):
    """The selected provider failed before returning an interpretation payload."""


class TurnCancelledError(RuntimeError):
    """The owning client cancelled this Turn."""


# Lifecycle statuses for bounded repair of model-authored TurnInterpretation objects.
TURN_UNDERSTANDING_RETRYING = "turn_understanding_retrying"
TURN_UNDERSTANDING_REPAIRED = "turn_understanding_repaired"
TURN_UNDERSTANDING_NORMALIZED = "turn_understanding_normalized"
TURN_UNDERSTANDING_FALLBACK = "turn_understanding_fallback"
TURN_UNDERSTANDING_EXHAUSTED = "turn_understanding_exhausted"
TURN_UNDERSTANDING_OK = "turn_understanding_ok"

_RETRIEVAL_OR_MUTATION_RE = re.compile(
    r"(?i)\b(?:"
    r"weather|forecast|temperature|humidity|search|look\s*up|lookup|research|"
    r"score|scores|standings|price|stock|news|browse|fetch|"
    r"delete|remove|send|email|post|write\s+file|save\s+to|run\s+command|install|"
    r"commit|push|deploy|open\s+app"
    r")\b"
)

_READ_ONLY_INFORMATION_RE = re.compile(
    r"(?i)(?:"
    r"^\s*(?:what|when|where|who|which|why|how|is|are|was|were|do|does|did|"
    r"can\s+you\s+(?:find|check|compare|explain)|"
    r"tell\s+me\s+(?:about|what|when|where|who|which|whether)|"
    r"show\s+me\s+(?:what|when|where|who|which|how|the\s+(?:current|latest))|"
    r"find|search|look\s*up|research|compare|explain)\b|"
    r"\b(?:weather|forecast|temperature|humidity|scores?|standings|fixtures?|"
    r"prices?|stocks?|markets?|news|current|latest|upcoming|availability|schedule|"
    r"flights?|travel|retailers?|documentation)\b"
    r")"
)
_CONVERSATIONAL_QUESTION_RE = re.compile(
    r"(?i)^\s*(?:what(?:'s|\s+is)\s+up|how\s+are\s+you|who\s+are\s+you|"
    r"what(?:'s|\s+is)\s+your\s+name|what\s+can\s+you\s+do|"
    r"what\s+do\s+you\s+think|tell\s+me\s+(?:a|another)\s+joke)\b"
)
_EXPLICIT_EFFECT_REQUEST_RE = re.compile(
    r"(?i)(?:"
    r"^\s*(?:please\s+)?(?:delete|remove|send|email|post|publish|write|save|"
    r"run|execute|install|commit|push|deploy|launch|open|upload|rename|move|copy|"
    r"create|update|modify)\b|"
    r"\b(?:please|then|and\s+then|go\s+ahead\s+and|can\s+you|could\s+you|"
    r"would\s+you|i\s+want\s+you\s+to)\s+(?:delete|remove|send|email|post|"
    r"publish|write|save|run|execute|install|commit|push|deploy|launch|open|"
    r"upload|rename|move|copy|create|update|modify)\b"
    r")"
)
_LOCAL_OR_SENSITIVE_INPUT_RE = re.compile(
    r"(?i)(?:[a-z]:[\\/]|\\\\[^\s]+[\\/]|(?:^|\s)\.{0,2}[\\/][^\s]+|"
    r"\b(?:this|my)\s+(?:project|repo(?:sitory)?|codebase|file|folder|directory|"
    r"computer|desktop)\b|\bthe\s+(?:current|active|attached|local)\s+"
    r"(?:project|repo(?:sitory)?|codebase|file|folder|directory)\b|"
    r"\b(?:password|passphrase|api[_ -]?key|access[_ -]?token|secret)\b)"
)
_READ_ONLY_MEMORY_RE = re.compile(
    r"(?i)(?:\bdo\s+you\s+remember\b|\bwhat\s+do\s+you\s+(?:remember|know)\s+about\s+me\b|"
    r"\bwhat(?:'s|\s+is|\s+was)\s+my\s+(?:name|preference|favorite|favourite)\b|"
    r"\b(?:from|in|using)\s+(?:your\s+)?memory\b)"
)


def normalize_extraneous_clarification_question(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Drop a non-ambiguous clarification_question when the rest of the contract is complete.

    Logged as a normalization, never applied to genuine ambiguous relations.
    """

    canonical = dict(payload or {})
    notes: list[str] = []
    relation = str(canonical.get("relation") or "").strip().casefold()
    question = str(canonical.get("clarification_question") or "").strip()
    if not relation or relation == TurnRelation.AMBIGUOUS.value:
        return canonical, notes
    if not question:
        # Already correct for resolved relations.
        if canonical.get("clarification_question") is not None and not question:
            canonical["clarification_question"] = None
            notes.append("normalized_empty_clarification_question_to_null")
        return canonical, notes
    # Narrow safety: only strip when the resolved relation is otherwise complete.
    resolved = {
        TurnRelation.NEW_TASK.value,
        TurnRelation.CONTINUE_TASK.value,
        TurnRelation.PROVIDE_TASK_INPUT.value,
        TurnRelation.CORRECT_TASK.value,
        TurnRelation.CANCEL_TASK.value,
        TurnRelation.SWITCH_TASK.value,
        TurnRelation.CASUAL_CONVERSATION.value,
        TurnRelation.RESUME_APPROVAL.value,
    }
    if relation not in resolved:
        return canonical, notes
    alternatives = list(canonical.get("candidate_alternatives") or [])
    if alternatives:
        return canonical, notes
    missing = [str(item).strip() for item in list(canonical.get("missing_fields") or []) if str(item).strip()]
    if missing:
        return canonical, notes
    if relation in {TurnRelation.NEW_TASK.value, TurnRelation.SWITCH_TASK.value}:
        if not str(canonical.get("proposed_objective") or "").strip():
            return canonical, notes
    task_relations = {
        TurnRelation.CONTINUE_TASK.value,
        TurnRelation.PROVIDE_TASK_INPUT.value,
        TurnRelation.CORRECT_TASK.value,
        TurnRelation.CANCEL_TASK.value,
        TurnRelation.SWITCH_TASK.value,
    }
    if relation in task_relations and not str(canonical.get("selected_task_id") or "").strip():
        return canonical, notes
    if relation == TurnRelation.RESUME_APPROVAL.value and not str(
        canonical.get("selected_approval_id") or ""
    ).strip():
        return canonical, notes
    canonical["clarification_question"] = None
    notes.append(
        "stripped_extraneous_clarification_question:"
        f"relation={relation}:question={question[:120]}"
    )
    logger.info(
        "TurnInterpretation normalization: stripped clarification_question on non-ambiguous relation={}",
        relation,
    )
    return canonical, notes


def normalize_missing_objective_from_requirements(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Recover a missing new-task objective from explicit requirement objectives.

    This is a bounded structural fold, not semantic inference: every source
    string already exists in the model-authored canonical requirements.
    """

    canonical = dict(payload or {})
    relation = str(canonical.get("relation") or "").strip().casefold()
    if relation not in {
        TurnRelation.NEW_TASK.value,
        TurnRelation.SWITCH_TASK.value,
    }:
        return canonical, []
    if str(canonical.get("proposed_objective") or "").strip():
        return canonical, []
    requirements = canonical.get("requirements")
    if not isinstance(requirements, list):
        return canonical, []
    objectives: list[str] = []
    for item in requirements:
        if not isinstance(item, Mapping):
            return canonical, []
        objective = re.sub(r"\s+", " ", str(item.get("objective") or "")).strip()
        if not objective:
            return canonical, []
        if objective not in objectives:
            objectives.append(objective)
    if not objectives:
        return canonical, []
    canonical["proposed_objective"] = "; ".join(objectives)[:1200]
    return canonical, ["derived_proposed_objective_from_requirement_objectives"]


def _validation_issues_from_error(exc: ValidationError) -> list[dict[str, Any]]:
    return [
        {
            "location": ".".join(str(item) for item in error.get("loc") or ()) or "object",
            "type": str(error.get("type") or "validation_error"),
            "message": str(error.get("msg") or "invalid value")[:240],
        }
        for error in exc.errors(include_url=False, include_input=False, include_context=False)[:12]
    ]


def _is_semantic_invariant_failure(issues: list[dict[str, Any]]) -> bool:
    tokens = (
        "requires selected_task_id", "must not select", "requires proposed_objective",
        "only valid for", "requires clarification_question", "only when relation",
        "clarification_question is only valid",
    )
    return any(
        any(token in str(issue.get("message") or "").casefold() for token in tokens)
        for issue in issues
    )


def validate_turn_interpretation_payload(
    extracted: Any,
) -> tuple[TurnInterpretation, dict[str, Any]]:
    """Decode, normalize, and validate one model payload into TurnInterpretation."""

    canonical, diagnostics = decode_turn_interpretation_payload(extracted)
    canonical, objective_notes = normalize_missing_objective_from_requirements(canonical)
    canonical, clarification_notes = normalize_extraneous_clarification_question(canonical)
    notes = [*objective_notes, *clarification_notes]
    if notes:
        diagnostics = {
            **dict(diagnostics or {}),
            "normalizations": list(dict.fromkeys([
                *list((diagnostics or {}).get("normalizations") or []),
                *notes,
            ])),
            "lifecycle": TURN_UNDERSTANDING_NORMALIZED,
        }
    interpretation = TurnInterpretation.model_validate(canonical)
    interpretation._decode_diagnostics = diagnostics
    return interpretation, diagnostics


def message_looks_like_retrieval_or_mutation(message: str) -> bool:
    return bool(_RETRIEVAL_OR_MUTATION_RE.search(str(message or "")))


def message_looks_like_safe_read_only_information_request(message: str) -> bool:
    """Conservatively recognize a recovery-safe information request.

    This classifier exists only after selected-model Turn Understanding has
    failed.  It can grant the general read-only research capability, never a
    mutation, local-file, credential, terminal, browser-interaction, or
    communication capability.
    """

    text = re.sub(r"\s+", " ", str(message or "")).strip()
    if not text or is_inert_conversational_content_request(text):
        return False
    if _CONVERSATIONAL_QUESTION_RE.search(text):
        return False
    if _EXPLICIT_EFFECT_REQUEST_RE.search(text):
        return False
    if _LOCAL_OR_SENSITIVE_INPUT_RE.search(text):
        return False
    return bool(_READ_ONLY_INFORMATION_RE.search(text))


def minimal_safe_fallback_interpretation(
    message: str,
    *,
    reason: str = "turn_understanding_exhausted_safe_fallback",
) -> TurnInterpretation:
    """Conservative answer-only new_task when repair is exhausted and guessing is safe."""

    text = re.sub(r"\s+", " ", str(message or "")).strip()[:1200] or "Respond to the user"
    interpretation = TurnInterpretation(
        relation=TurnRelation.NEW_TASK,
        proposed_objective=text,
        requested_capabilities=["conversation"],
        requested_operation="compose_response",
        requirements=[TurnRequirement(
            kind=RequirementKind.ANSWER_ONLY,
            objective=text,
            acceptance_criteria=[
                "Produce a truthful conversational response without external execution.",
            ],
        )],
        confidence=0.35,
    )
    interpretation._decode_diagnostics = {
        "lifecycle": TURN_UNDERSTANDING_FALLBACK,
        "fallback_reason": reason,
    }
    return interpretation


def minimal_safe_read_only_fallback_interpretation(
    message: str,
    *,
    reason: str = "turn_understanding_exhausted_read_only_fallback",
) -> TurnInterpretation:
    """Preserve one information objective without granting effect authority."""

    text = re.sub(r"\s+", " ", str(message or "")).strip()[:1200]
    if not text or not message_looks_like_safe_read_only_information_request(text):
        raise ValueError("read-only fallback requires a bounded information request")
    freshness = (
        "current"
        if re.search(
            r"(?i)\b(?:current|currently|latest|live|recent|today|tonight|tomorrow|"
            r"upcoming|next|now|weather|forecast|price|score|news|availability)\b",
            text,
        )
        else "unspecified"
    )
    memory_lookup = bool(_READ_ONLY_MEMORY_RE.search(text))
    capability = "memory" if memory_lookup else "research"
    requirement_kind = RequirementKind.MEMORY if memory_lookup else RequirementKind.RETRIEVAL
    operation = "memory_lookup" if memory_lookup else "research"
    interpretation = TurnInterpretation(
        relation=TurnRelation.NEW_TASK,
        proposed_objective=text,
        requested_capabilities=[capability],
        requested_operation=operation,
        constraints=["runtime_read_only_recovery"],
        requirements=[TurnRequirement(
            kind=requirement_kind,
            objective=text,
            freshness_class=freshness,
            acceptance_criteria=[
                (
                    "Answer only from the authorized memory projection."
                    if memory_lookup
                    else "Return relevant evidence for the user's preserved information request."
                ),
            ],
        )],
        confidence=0.2,
    )
    interpretation._decode_diagnostics = {
        "lifecycle": TURN_UNDERSTANDING_FALLBACK,
        "fallback_reason": reason,
        "authority_ceiling": "read_only_memory" if memory_lookup else "read_only_research",
    }
    return interpretation


class TurnUnderstandingCompiler:
    def compile(
        self,
        *,
        latest_user_message: str,
        assistant_identity: EchoIdentityProjection,
        recent_conversation: list[dict[str, str]],
        reply_relationship: Optional[dict[str, str]],
        project_id: str,
        session_id: str,
        suspended_tasks: list[Any],
        active_approvals: list[Any],
        relevant_memory: list[dict[str, Any]],
        project_context: list[dict[str, Any]],
        recent_verified_outcomes: list[dict[str, Any]],
        entity_candidates: list[dict[str, Any]],
        source: str,
        channel: str = "",
        capability_categories: Optional[list[str]] = None,
    ) -> TurnUnderstandingEnvelope:
        return TurnUnderstandingEnvelope(
            assistant_identity=assistant_identity,
            latest_user_message=latest_user_message,
            recent_conversation=recent_conversation,
            reply_relationship=dict(reply_relationship or {}),
            project_id=project_id,
            session_id=session_id,
            suspended_tasks=[
                SuspendedTaskSummary(
                    task_id=str(item.id),
                    objective=str(item.objective)[:600],
                    lifecycle_status=str(getattr(item.status, "value", item.status)),
                    waiting_for=list(item.missing_inputs or []),
                    workflow_stage=str(item.workflow_stage or ""),
                    updated_at=float(item.updated_at or 0.0),
                    revision=int(getattr(item, "revision", 0) or 0),
                    legacy_untrusted=str(getattr(item.status, "value", item.status)) == "legacy_untrusted",
                )
                for item in suspended_tasks[:12]
            ],
            active_approvals=[
                ApprovalSummary(
                    approval_id=str(item.id),
                    tool_name=str(item.tool),
                    summary=str(item.summary or item.preview or "")[:500],
                    risk_level=str(item.risk_level or ""),
                )
                for item in active_approvals[:8]
            ],
            relevant_memory=relevant_memory,
            project_context=project_context,
            recent_verified_outcomes=recent_verified_outcomes,
            entity_candidates=entity_candidates,
            source=source,
            channel=channel,
            available_capability_categories=capability_categories or list(CAPABILITY_CATEGORIES),
        )


class TurnInterpreter:
    """Invoke the selected model for typed Turn understanding with bounded repair."""

    MAX_REPAIR_ATTEMPTS = 2
    REPAIR_TEMPERATURE = 0.1

    def interpret(
        self,
        envelope: TurnUnderstandingEnvelope,
        *,
        invoke_selected_model: Callable[..., Any],
    ) -> TurnInterpretation:
        schema = turn_interpretation_model_schema()
        property_names = list(TurnInterpretation.model_fields)
        system = self._system_prompt(envelope, schema, property_names)
        user = "TURN_UNDERSTANDING_ENVELOPE=" + json.dumps(
            envelope.model_dump(mode="json", exclude={"assistant_identity"}),
            ensure_ascii=False,
            sort_keys=True,
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_failure: Optional[dict[str, Any]] = None
        last_raw: Any = None
        last_extracted: Any = None
        repair_attempts = 0
        lifecycle = TURN_UNDERSTANDING_OK

        for attempt in range(self.MAX_REPAIR_ATTEMPTS + 1):
            temperature = None if attempt == 0 else self.REPAIR_TEMPERATURE
            try:
                raw = invoke_selected_model(
                    messages,
                    schema,
                    temperature=temperature,
                )
            except TurnCancelledError:
                raise
            except TurnUnderstandingProviderError:
                raise
            except TypeError:
                # Older callables may not accept temperature=; retry without it.
                try:
                    raw = invoke_selected_model(messages, schema)
                except TurnCancelledError:
                    raise
                except TurnUnderstandingProviderError:
                    raise
                except Exception as exc:
                    raise TurnUnderstandingProviderError(
                        f"selected model invocation failed before TurnInterpretation: {exc}"
                    ) from exc
            except Exception as exc:
                raise TurnUnderstandingProviderError(
                    f"selected model invocation failed before TurnInterpretation: {exc}"
                ) from exc
            last_raw = raw
            try:
                extracted = extract_turn_interpretation_payload(raw)
            except Exception as exc:
                failure_code = classify_extraction_failure(raw, exc)
                last_failure = {
                    "code": failure_code,
                    "error_type": type(exc).__name__,
                    "attempt": attempt,
                }
                logger.warning(
                    "TurnInterpretation response extraction failed attempt={}: {}",
                    attempt,
                    last_failure,
                )
                if attempt >= self.MAX_REPAIR_ATTEMPTS:
                    break
                lifecycle = TURN_UNDERSTANDING_RETRYING
                repair_attempts += 1
                messages = self._repair_messages(
                    system=system,
                    user=user,
                    failed_payload=str(raw)[:4000],
                    issues=[{"message": failure_code, "location": "payload"}],
                    attempt=repair_attempts,
                )
                continue
            last_extracted = extracted
            try:
                interpretation, diagnostics = validate_turn_interpretation_payload(extracted)
            except TurnInterpretationNormalizationError as exc:
                last_failure = {
                    "code": str(exc.diagnostics.get("code") or "normalization_failed"),
                    "diagnostics": dict(exc.diagnostics),
                    "attempt": attempt,
                }
                logger.warning("TurnInterpretation canonical decode failed: {}", last_failure)
                if attempt >= self.MAX_REPAIR_ATTEMPTS:
                    break
                lifecycle = TURN_UNDERSTANDING_RETRYING
                repair_attempts += 1
                messages = self._repair_messages(
                    system=system,
                    user=user,
                    failed_payload=extracted if isinstance(extracted, (dict, list)) else str(extracted)[:4000],
                    issues=[{"message": str(exc), "location": "payload"}],
                    attempt=repair_attempts,
                )
                continue
            except ValidationError as exc:
                issues = _validation_issues_from_error(exc)
                validation_code = (
                    "semantic_invariant_failed"
                    if _is_semantic_invariant_failure(issues)
                    else "strict_validation_failed"
                )
                last_failure = {
                    "code": validation_code,
                    "issues": issues,
                    "attempt": attempt,
                    "preserved_payload": extracted if isinstance(extracted, dict) else None,
                }
                logger.warning(
                    "TurnInterpretation strict validation failed attempt={}: {}",
                    attempt,
                    {"code": validation_code, "issues": issues},
                )
                if attempt >= self.MAX_REPAIR_ATTEMPTS:
                    break
                lifecycle = TURN_UNDERSTANDING_RETRYING
                repair_attempts += 1
                messages = self._repair_messages(
                    system=system,
                    user=user,
                    failed_payload=extracted if isinstance(extracted, (dict, list)) else str(extracted)[:4000],
                    issues=issues,
                    attempt=repair_attempts,
                )
                continue

            # Post-validate identity scope against the supplied candidate sets.
            try:
                interpretation = self._check_candidate_scope(envelope, interpretation)
            except TurnUnderstandingError as scope_exc:
                last_failure = dict(scope_exc.diagnostics or {})
                last_failure["attempt"] = attempt
                if attempt >= self.MAX_REPAIR_ATTEMPTS:
                    break
                lifecycle = TURN_UNDERSTANDING_RETRYING
                repair_attempts += 1
                messages = self._repair_messages(
                    system=system,
                    user=user,
                    failed_payload=interpretation.model_dump(mode="json"),
                    issues=[{"message": str(scope_exc), "location": "selected_task_id"}],
                    attempt=repair_attempts,
                )
                continue

            if repair_attempts > 0:
                lifecycle = TURN_UNDERSTANDING_REPAIRED
            elif (diagnostics or {}).get("normalizations"):
                lifecycle = TURN_UNDERSTANDING_NORMALIZED
            else:
                lifecycle = TURN_UNDERSTANDING_OK
            merged = {
                **dict(diagnostics or {}),
                "lifecycle": lifecycle,
                "repair_attempts": repair_attempts,
            }
            interpretation._decode_diagnostics = merged
            logger.info(
                "Turn Understanding accepted lifecycle={} repairs={}",
                lifecycle,
                repair_attempts,
            )
            return enrich_multi_location_weather_interpretation(
                interpretation,
                latest_user_message=envelope.latest_user_message,
            )

        # Exhausted repair. Preserve inert conversation or a bounded read-only
        # information objective without granting any effect authority.
        lifecycle = TURN_UNDERSTANDING_EXHAUSTED
        message = str(envelope.latest_user_message or "")
        if (
            is_inert_conversational_content_request(message)
            or (
                not message_looks_like_retrieval_or_mutation(message)
                and not message_looks_like_safe_read_only_information_request(message)
                and not _EXPLICIT_EFFECT_REQUEST_RE.search(message)
                and not _LOCAL_OR_SENSITIVE_INPUT_RE.search(message)
            )
        ):
            fallback = minimal_safe_fallback_interpretation(
                message,
                reason="repair_exhausted_conversational_fallback",
            )
            fallback._decode_diagnostics = {
                **dict(fallback._decode_diagnostics or {}),
                "lifecycle": TURN_UNDERSTANDING_FALLBACK,
                "repair_attempts": repair_attempts,
                "prior_failure": last_failure,
                "preserved_raw_chars": len(str(last_raw or "")),
            }
            logger.warning(
                "Turn Understanding exhausted repairs; using safe conversational fallback"
            )
            return fallback
        if message_looks_like_safe_read_only_information_request(message):
            fallback = minimal_safe_read_only_fallback_interpretation(
                message,
                reason="repair_exhausted_read_only_fallback",
            )
            fallback._decode_diagnostics = {
                **dict(fallback._decode_diagnostics or {}),
                "lifecycle": TURN_UNDERSTANDING_FALLBACK,
                "repair_attempts": repair_attempts,
                "prior_failure": last_failure,
                "preserved_raw_chars": len(str(last_raw or "")),
            }
            logger.warning(
                "Turn Understanding exhausted repairs; preserving a bounded read-only objective"
            )
            return fallback

        diagnostics = {
            "turn_interpretation_decode_error": last_failure or {
                "code": "repair_exhausted",
            },
            "lifecycle": lifecycle,
            "repair_attempts": repair_attempts,
            "preserved_payload": last_extracted if isinstance(last_extracted, dict) else None,
        }
        logger.error("Turn Understanding exhausted repairs without a valid contract: {}", diagnostics)
        raise TurnUnderstandingError(
            f"selected model returned an invalid TurnInterpretation after {repair_attempts} repair(s) "
            f"({(last_failure or {}).get('code') or 'repair_exhausted'})",
            diagnostics=diagnostics,
        )

    @staticmethod
    def _system_prompt(
        envelope: TurnUnderstandingEnvelope,
        schema: dict[str, Any],
        property_names: list[str],
    ) -> str:
        return (
            envelope.assistant_identity.render()
            + "\n\nYou are EchoSpeak's semantic controller for one Turn. Understand the latest user message. "
            "You may select a suspended task only when the latest message actually refers to it. "
            "A foreground or recent task is a candidate, never an automatic owner. Preserve every explicit "
            "location, date, time, filename, path, recipient, application, quantity, correction, referenced "
            "object, and requested action in extracted_fields or referenced_entities. If a complete message "
            "already supplies a field, do not mark it missing. Separate explicit memory from a requested action. "
            "A missing field is only a user-supplied value without which no safe, progressive allowed tool action "
            "can begin. Facts that an allowed research or live-data tool can discover (including an exact event "
            "time) are not missing user inputs. Do not require optional precision that the request itself does not "
            "need. "
            "The relation is the only clarification authority: use relation=ambiguous and supply "
            "clarification_question only when a question is required; never emit clarification_required. "
            "If relation is new_task, continue_task, casual_conversation, or any non-ambiguous value, "
            "clarification_question MUST be null or omitted — never both a resolved relation and a question. "
            "Requests for live scores, upcoming games, matches, fixtures, or sports schedules must include "
            "live_sports and set requested_operation to exactly one of schedule, live_scores, standings, "
            "results, team_next_event, or competition_next_event; research may also be included as a "
            "secondary fallback. "
            "When a selected memory TaskRun is waiting for memory_confirmation, convert an explicit confirmation "
            "or rejection into extracted_fields.memory_confirmation as the boolean true or false. "
            "A new explicit objective is normally new_task, not an answer to an unrelated suspended task. "
            "Decompose every new or continued objective into requirements. Each unrelated requested result must "
            "be a separate requirement, including memory retrieval, Project/local context, calculations, and "
            "each distinct search entity or objective. Do not combine unrelated searches. "
            "For multi-location live data (for example weather in Edmonton and Calgary, or forecasts for two "
            "cities), emit one retrieval requirement per location with entities=[that place] and location set, "
            "or at minimum list every place in entities on a single requirement. Never omit secondary cities. "
            "Use kind=answer_only "
            "only when no tool or stored context is needed; memory, local_context, calculation, and retrieval "
            "must retain their exact kinds. requirement_id may be empty because the runtime assigns stable IDs. "
            "Text the user asks Echo to draft, quote, summarize, list, brainstorm, organize, or discuss is inert "
            "response content, even when that content contains command-shaped phrases such as send, delete, email, "
            "Git, or print. Use requested_capabilities=[\"conversation\"] and requested_operation=\"compose_response\" "
            "for those answer-only requests. Drafting an email is not sending it. A list item is not an action. "
            "Only classify external capability use when the latest user instruction itself explicitly promotes the "
            "content into an action such as save this list, send that email, or run that update. "
            "When relation is new_task or switch_task, proposed_objective MUST be a non-null concise copy "
            "of the user's objective. When relation continues/corrects/cancels/switches a task, "
            "selected_task_id MUST be one exact supplied candidate ID. "
            "Use cancel_task for explicit cancellation and switch_task with the selected old TaskRun plus a "
            "replacement proposed_objective when the user explicitly replaces that work. "
            "legacy_untrusted tasks may never be selected automatically. Do not perform tools, grant permission, "
            "or claim completion. Do not output reasoning or chain-of-thought. Return exactly one JSON object "
            "matching the supplied schema and no prose. Use these exact lower_snake_case property names: "
            f"{json.dumps(property_names)}. Type and class names such as TurnRelation or ApprovalDecision are never property names. "
            "Do not add, rename, capitalize, or omit required properties. Use JSON null for optional scalar fields "
            "that do not apply. Never fabricate a TaskRun or approval ID; select only an ID supplied in this "
            "Turn Understanding boundary. selected_approval_id and approval_decision apply only when relation is "
            "resume_approval and must otherwise be null or omitted.\n\n"
            f"TURN_INTERPRETATION_SCHEMA={json.dumps(schema, ensure_ascii=False, sort_keys=True)}"
        )

    @staticmethod
    def _repair_messages(
        *,
        system: str,
        user: str,
        failed_payload: Any,
        issues: list[dict[str, Any]],
        attempt: int,
    ) -> list[dict[str, str]]:
        issue_text = json.dumps(issues[:12], ensure_ascii=False, sort_keys=True)
        if isinstance(failed_payload, (dict, list)):
            payload_text = json.dumps(failed_payload, ensure_ascii=False, sort_keys=True)[:6000]
        else:
            payload_text = str(failed_payload or "")[:6000]
        repair = (
            f"REPAIR_ATTEMPT={attempt}. Your previous TurnInterpretation was rejected by runtime "
            f"semantic validation. Failed invariants:\n{issue_text}\n\n"
            "Preserve the original intent. Return ONLY one corrected JSON object matching the schema. "
            "Hard rules: if relation is not ambiguous, clarification_question must be null or omitted; "
            "if relation is ambiguous, clarification_question must be a non-empty user-facing question; "
            "new_task requires proposed_objective; do not invent TaskRun or approval IDs.\n\n"
            f"PREVIOUS_INVALID_PAYLOAD={payload_text}"
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "user", "content": repair},
        ]

    @staticmethod
    def _check_candidate_scope(
        envelope: TurnUnderstandingEnvelope,
        interpretation: TurnInterpretation,
    ) -> TurnInterpretation:
        candidate_ids = {
            item.task_id for item in envelope.suspended_tasks if not item.legacy_untrusted
        }
        if interpretation.selected_task_id and interpretation.selected_task_id not in candidate_ids:
            raise TurnUnderstandingError(
                "selected model referenced a TaskRun outside the supplied candidate set",
                diagnostics={
                    "turn_interpretation_decode_error": {"code": "semantic_invariant_failed"},
                },
            )
        approval_ids = {item.approval_id for item in envelope.active_approvals}
        if (
            interpretation.selected_approval_id
            and interpretation.selected_approval_id not in approval_ids
        ):
            raise TurnUnderstandingError(
                "selected model referenced an approval outside the current Session",
                diagnostics={
                    "turn_interpretation_decode_error": {"code": "semantic_invariant_failed"},
                },
            )
        return interpretation


def enrich_multi_location_weather_interpretation(
    interpretation: TurnInterpretation,
    *,
    latest_user_message: str = "",
) -> TurnInterpretation:
    """Ensure multi-city weather objectives yield distinct entities/requirements.

    Safe for native structured output and prompt_json fallback alike: when the
    model already decomposed correctly, expansion is a no-op.
    """

    message = str(latest_user_message or interpretation.proposed_objective or "").strip()
    rows = list(interpretation.requirements or [])
    if not rows and message and re.search(r"(?i)\b(weather|forecast|temperature|humidity)\b", message):
        places = extract_weather_locations(message)
        if places:
            rows = [
                TurnRequirement(
                    kind=RequirementKind.RETRIEVAL,
                    objective=f"Weather for {place}",
                    entities=[place],
                    location=place,
                    requested_fields=infer_requested_fields(f"weather for {place}") or ["weather_conditions"],
                )
                for place in places
            ]
            caps = list(dict.fromkeys([*interpretation.requested_capabilities, "live_weather", "research"]))
            return interpretation.model_copy(update={
                "requirements": rows,
                "requested_capabilities": caps,
            })
    if not rows:
        return interpretation
    # Fill missing entities on coarse single weather requirements before expand.
    enriched: list[TurnRequirement] = []
    for row in rows:
        if row.kind != RequirementKind.RETRIEVAL:
            enriched.append(row)
            continue
        places = extract_weather_locations(row)
        if len(places) < 2 and message:
            from_message = extract_weather_locations(message)
            if len(from_message) >= 2 and re.search(
                r"(?i)\b(weather|forecast|temperature|humidity)\b",
                row.objective + " " + message,
            ):
                places = from_message
        if len(places) >= 2 and len(row.entities) < 2:
            enriched.append(row.model_copy(update={
                "entities": places,
                "location": row.location or ", ".join(places),
                "requested_fields": list(row.requested_fields)
                or infer_requested_fields(row.objective)
                or ["weather_conditions"],
            }))
        else:
            enriched.append(row)
    expanded = expand_multi_location_weather_requirements(enriched)
    before = [item.model_dump(mode="json", exclude={"requirement_id"}) for item in rows]
    after = [item.model_dump(mode="json", exclude={"requirement_id"}) for item in expanded]
    if before == after:
        return interpretation
    caps = list(interpretation.requested_capabilities or [])
    if any(
        re.search(r"(?i)\bweather\b", item.objective)
        for item in expanded
    ) and "live_weather" not in caps:
        caps = list(dict.fromkeys([*caps, "live_weather", "research"]))
    return interpretation.model_copy(update={
        "requirements": expanded,
        "requested_capabilities": caps,
    })


def _capabilities_for_typed_operation(operation: Optional[str]) -> list[str]:
    """Normalize the model's typed operation; this never inspects raw user prose."""

    value = re.sub(r"[^a-z0-9_]+", "_", str(operation or "").casefold()).strip("_")
    if not value:
        return []
    rules = (
        (("research", "web_search", "browse", "internet_search", "search_web"), "research"),
        (("weather",), "live_weather"),
        ((
            "sports", "score", "fixture", "fifa", "world_cup",
            "schedule", "match_schedule", "game_schedule", "live_game", "live_scores",
            "standings", "results", "team_next_event", "competition_next_event",
            "next_match", "next_game",
        ), "live_sports"),
        (("file_write", "file_delete", "file_move", "file_copy", "patch", "edit_code", "coding_write"), "coding_write"),
        (("file_read", "inspect_code", "coding_read"), "coding_read"),
        (("terminal", "shell", "command"), "terminal"),
        (("calculate", "math"), "calculate"),
        (("time", "date"), "time"),
        (("memory", "remember", "forget"), "memory"),
    )
    return [capability for needles, capability in rules if any(needle in value for needle in needles)]
