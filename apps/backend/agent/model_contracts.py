"""Canonical runtime-to-model execution contracts.

The runtime owns every field in :class:`ModelTurnEnvelope`.  Models receive a
projection of this state and may propose an :class:`AgentDecision`; they never
become authoritative for Projects, Sessions, approvals, tools, or completion.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from enum import Enum
from typing import Any, Iterable, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from agent.state import ToolOutcome
from agent.identity import EchoIdentityProjection
from agent.retrieval_contracts import USABLE_RESULT_STATES
from agent.research_runtime import (
    CapabilityDescriptor,
    CapabilitySnapshot,
    CompletionDisposition,
    CompletionVerdict,
    RequirementCompletionEvaluator,
    RequirementKind,
    RequirementState,
    RequirementStatus,
    TaskRunNextAction,
    TurnRequirement,
    build_capability_snapshot,
    compile_turn_requirements,
    reconcile_requirement_states,
)


CONTRACT_VERSION = "8.0.0"


def is_usable_verified_outcome(outcome: ToolOutcome) -> bool:
    """Return whether runtime evidence may satisfy a requirement.

    Verification metadata is diagnostic unless the runtime explicitly records
    ``verified: true``.  Treating a non-empty metadata object as truth would
    allow ``{"verified": false}`` to enter the authoritative evidence channel.
    """
    return bool(
        outcome.execution_status == "success"
        and outcome.result_state in USABLE_RESULT_STATES
        and dict(outcome.verification or {}).get("verified") is True
    )


class ToolUsePolicy(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    PROHIBITED = "prohibited"


class DecisionKind(str, Enum):
    ASK_FOR_INPUT = "ask_for_input"
    CALL_TOOL = "call_tool"
    UPDATE_PLAN = "update_plan"
    ANSWER = "answer"
    CANCEL = "cancel"
    BLOCK = "block"


class RuntimeIdentity(BaseModel):
    project_id: str = ""
    session_id: str
    turn_id: str
    execution_id: str
    request_id: str = ""

    @model_validator(mode="after")
    def require_runtime_ids(self) -> "RuntimeIdentity":
        if not self.session_id or not self.turn_id or not self.execution_id:
            raise ValueError("session_id, turn_id, and execution_id are required")
        if self.turn_id != self.execution_id:
            raise ValueError("EchoSpeak Turn identity must equal Execution identity")
        return self


class TaskState(BaseModel):
    task_run_id: str = ""
    objective: str
    status: str = "in_progress"
    execution_profile: Literal["chat", "work", "code"] = "chat"
    graph_id: str = ""
    active_graph_node_ids: list[str] = Field(default_factory=list)
    current_plan_step: Optional[dict[str, Any]] = None
    collected_inputs: dict[str, Any] = Field(default_factory=dict)
    missing_inputs: list[str] = Field(default_factory=list)
    latest_user_relation: Literal["new_work", "continue", "confirm", "retry", "cancel", "other"] = "other"
    # Exact TurnInterpretation relation is authoritative. latest_user_relation
    # is a derived compatibility/telemetry projection only.
    canonical_turn_relation: str = ""
    requirements: list[TurnRequirement] = Field(default_factory=list)
    requirement_states: dict[str, RequirementState] = Field(default_factory=dict)
    active_requirement_id: str = ""
    completion: CompletionVerdict = Field(default_factory=CompletionVerdict)
    next_runtime_action: TaskRunNextAction = TaskRunNextAction.FINALIZE
    preferred_tool_name: str = ""
    eligible_tool_names: list[str] = Field(default_factory=list)
    recovery_strategy: str = ""


class SkillRuntimeState(BaseModel):
    execution_record_id: str = ""
    skill_id: str = ""
    skill_version: str = ""
    workflow_stage: str = ""
    permitted_tool_ids: list[str] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    verification_rules: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)


class ToolDefinition(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    result_schema: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: list[str] = Field(default_factory=list)
    mutating: bool = False
    approval_required: bool = False
    origin: str = "native"
    owner: str = "builtin"
    connection_id: str = ""
    mcp_server: str = ""
    health: str = "healthy"
    available: bool = True
    category: str = "general"
    capability: Optional[CapabilityDescriptor] = None

    @model_validator(mode="after")
    def require_name_and_object_schema(self) -> "ToolDefinition":
        if not self.name.strip():
            raise ValueError("tool name is required")
        if self.parameters.get("type", "object") != "object":
            raise ValueError("tool parameters must use an object schema")
        return self


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    index: int = 0


class StreamedToolCallFragment(BaseModel):
    index: int = 0
    call_id: str = ""
    name_fragment: str = ""
    arguments_fragment: str = ""


class ApprovalState(BaseModel):
    status: Literal["none", "required", "pending", "approved", "rejected", "expired"] = "none"
    approval_id: str = ""
    action_id: str = ""
    tool_name: str = ""


class ModelTurnEnvelope(BaseModel):
    contract_version: str = CONTRACT_VERSION
    compiled_at: float = Field(default_factory=time.time)
    identity: RuntimeIdentity
    assistant_identity: EchoIdentityProjection
    provider: str
    model_id: str
    model_family: str
    adapter_version: str
    task: TaskState
    skill: SkillRuntimeState = Field(default_factory=SkillRuntimeState)
    latest_user_message: str
    allowed_tools: list[ToolDefinition] = Field(default_factory=list)
    tool_use_policy: ToolUsePolicy = ToolUsePolicy.OPTIONAL
    relevant_memory: list[dict[str, Any]] = Field(default_factory=list)
    approval: ApprovalState = Field(default_factory=ApprovalState)
    verified_tool_outcomes: list[ToolOutcome] = Field(default_factory=list)
    valid_next_actions: list[DecisionKind] = Field(default_factory=list)
    completion_requirements: list[str] = Field(default_factory=list)
    capability_snapshot: Optional[CapabilitySnapshot] = None
    completion_evaluation: CompletionVerdict = Field(default_factory=CompletionVerdict)
    constraints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_authority_projection(self) -> "ModelTurnEnvelope":
        names = [item.name for item in self.allowed_tools]
        if len(names) != len(set(names)):
            raise ValueError("allowed tool names must be unique")
        unavailable = [item.name for item in self.allowed_tools if not item.available]
        if unavailable:
            raise ValueError(f"unavailable tools cannot enter the model allowlist: {unavailable}")
        if self.tool_use_policy == ToolUsePolicy.PROHIBITED and self.allowed_tools:
            raise ValueError("prohibited tool policy cannot expose tools")
        # A required policy with an empty allowlist is a valid blocked state. It
        # must never be coerced to optional merely to make a model call possible.
        for outcome in self.verified_tool_outcomes:
            if outcome.execution_id != self.identity.execution_id:
                raise ValueError("ToolOutcome belongs to a different Execution")
            if outcome.session_id != self.identity.session_id:
                raise ValueError("ToolOutcome belongs to a different Session")
            if (outcome.project_id or "") != (self.identity.project_id or ""):
                raise ValueError("ToolOutcome belongs to a different Project")
            if not is_usable_verified_outcome(outcome):
                raise ValueError("ToolOutcome must carry explicit usable runtime verification")
        return self

    def safe_diagnostics(self) -> dict[str, Any]:
        """Return structure-only diagnostics; never include prompts or result bodies."""
        memory_types = sorted({str(item.get("type") or "unknown") for item in self.relevant_memory})
        return {
            "contract_version": self.contract_version,
            "project_id": self.identity.project_id,
            "session_id": self.identity.session_id,
            "turn_id": self.identity.turn_id,
            "execution_id": self.identity.execution_id,
            "request_id": self.identity.request_id,
            "provider": self.provider,
            "model_id": self.model_id,
            "model_family": self.model_family,
            "adapter_version": self.adapter_version,
            "assistant_name": self.assistant_identity.assistant_name,
            "product_name": self.assistant_identity.product_name,
            "soul_sha256": self.assistant_identity.soul_sha256,
            "task_status": self.task.status,
            "task_run_id": self.task.task_run_id,
            "execution_profile": self.task.execution_profile,
            "graph_id": self.task.graph_id,
            "active_graph_node_ids": list(self.task.active_graph_node_ids),
            "skill_execution_record_id": self.skill.execution_record_id,
            "skill_id": self.skill.skill_id,
            "skill_version": self.skill.skill_version,
            "skill_workflow_stage": self.skill.workflow_stage,
            "skill_permitted_tools": list(self.skill.permitted_tool_ids),
            "latest_user_relation": self.task.latest_user_relation,
            "canonical_turn_relation": self.task.canonical_turn_relation,
            "tool_policy": self.tool_use_policy.value,
            "allowed_tool_names": [tool.name for tool in self.allowed_tools],
            "allowed_tool_origins": {
                tool.name: {
                    "origin": tool.origin,
                    "connection_id": tool.connection_id,
                    "mcp_server": tool.mcp_server,
                    "health": tool.health,
                }
                for tool in self.allowed_tools
            },
            "memory_count": len(self.relevant_memory),
            "memory_types": memory_types,
            "approval_status": self.approval.status,
            "verified_outcome_ids": [item.run_id for item in self.verified_tool_outcomes],
            "valid_next_actions": [item.value for item in self.valid_next_actions],
            # Authoritative TaskRun requirement count — both fields must match evaluator.required_ids.
            "requirement_count": len(self.task.requirements),
            "completion_requirement_count": len(self.completion_evaluation.required_ids or self.task.requirements),
            "completion_instruction_count": len(self.completion_requirements),
            "requirement_projection_aligned": (
                {item.requirement_id for item in self.task.requirements if item.required}
                == set(self.completion_evaluation.required_ids or [])
            ),
            "active_requirement_id": self.task.active_requirement_id,
            "next_runtime_action": self.task.next_runtime_action.value,
            "preferred_tool_name": self.task.preferred_tool_name,
            "eligible_tool_names": list(self.task.eligible_tool_names),
            "recovery_strategy": self.task.recovery_strategy,
            "requirement_states": {
                key: value.status.value for key, value in self.task.requirement_states.items()
            },
            "completion_disposition": self.completion_evaluation.disposition.value,
            "completion_finalizable": self.completion_evaluation.finalizable,
            "capability_snapshot_sha256": (
                self.capability_snapshot.inventory_sha256 if self.capability_snapshot else ""
            ),
            "user_message_sha256": hashlib.sha256(self.latest_user_message.encode("utf-8")).hexdigest(),
            "estimated_input_tokens": estimate_envelope_tokens(self),
        }


class AgentDecision(BaseModel):
    kind: DecisionKind
    message: str = ""
    tool_call: Optional[ToolCall] = None
    # Canonical batch emitted by providers that support parallel native tool
    # calls. ``tool_call`` remains a read-compatible first-call projection.
    tool_calls: list[ToolCall] = Field(default_factory=list)
    plan: list[dict[str, Any]] = Field(default_factory=list)
    reason_code: str = ""
    verified_outcome_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def match_payload_to_kind(self) -> "AgentDecision":
        calls = list(self.tool_calls or [])
        if not calls and self.tool_call is not None:
            calls = [self.tool_call]
        if self.kind == DecisionKind.CALL_TOOL and not calls:
            raise ValueError("call_tool requires at least one tool call")
        if self.kind != DecisionKind.CALL_TOOL and (self.tool_call is not None or calls):
            raise ValueError("tool calls are only valid for call_tool")
        if calls:
            call_ids = [item.id for item in calls]
            if len(call_ids) != len(set(call_ids)):
                raise ValueError("tool call ids must be unique within one model decision")
            self.tool_calls = calls
            self.tool_call = calls[0]
        if self.kind == DecisionKind.UPDATE_PLAN and not self.plan:
            raise ValueError("update_plan requires at least one step")
        if self.kind in {DecisionKind.ASK_FOR_INPUT, DecisionKind.ANSWER, DecisionKind.BLOCK} and not self.message.strip():
            raise ValueError(f"{self.kind.value} requires a message")
        return self


class DecisionValidationError(ValueError):
    """A model proposal conflicts with current runtime authority."""


def validate_agent_decision(envelope: ModelTurnEnvelope, decision: AgentDecision) -> AgentDecision:
    if decision.kind not in envelope.valid_next_actions:
        raise DecisionValidationError(f"Decision {decision.kind.value} is not valid for the current runtime state")
    if decision.kind == DecisionKind.CALL_TOOL:
        if envelope.tool_use_policy == ToolUsePolicy.PROHIBITED:
            raise DecisionValidationError("Tool use is prohibited for this turn")
        normalized_calls: list[ToolCall] = []
        matched_definitions: list[ToolDefinition] = []
        for call in list(decision.tool_calls or ([decision.tool_call] if decision.tool_call else [])):
            definition = next((item for item in envelope.allowed_tools if item.name == call.name), None)
            if definition is None:
                key = _normalized_tool_name(call.name)
                matches = [
                    item for item in envelope.allowed_tools
                    if _normalized_tool_name(item.name) == key
                ]
                if len(matches) != 1:
                    raise DecisionValidationError(
                        f"Tool {call.name!r} is not an unambiguous current allowlist name"
                    )
                definition = matches[0]
                call = call.model_copy(update={"name": definition.name})
            _validate_json_arguments(definition.parameters, call.arguments)
            normalized_calls.append(call)
            matched_definitions.append(definition)
        if len(normalized_calls) > 1 and any(
            item.mutating or item.approval_required for item in matched_definitions
        ):
            raise DecisionValidationError(
                "Mutating or approval-bound tools must be proposed one at a time"
            )
        decision = decision.model_copy(update={
            "tool_calls": normalized_calls,
            "tool_call": normalized_calls[0],
        })
    if decision.kind == DecisionKind.ASK_FOR_INPUT and not envelope.task.missing_inputs:
        raise DecisionValidationError("The runtime has not identified any missing required input")
    if decision.kind == DecisionKind.ANSWER:
        if envelope.approval.status in {"required", "pending"}:
            raise DecisionValidationError("A pending approval prevents completion")
        if envelope.task.missing_inputs:
            raise DecisionValidationError("Required inputs are still missing")
        successful = {
            item.run_id
            for item in envelope.verified_tool_outcomes
            if item.execution_status == "success"
            and item.result_state in USABLE_RESULT_STATES
            and is_usable_verified_outcome(item)
        }
        if not envelope.completion_evaluation.finalizable:
            unresolved = ", ".join(envelope.completion_evaluation.unresolved_ids[:6])
            raise DecisionValidationError(
                "Runtime requirements are not finalizable"
                + (f": {unresolved}" if unresolved else "")
            )
        if envelope.completion_evaluation.disposition not in {
            CompletionDisposition.COMPLETE, CompletionDisposition.PARTIAL
        }:
            raise DecisionValidationError("Runtime completion disposition does not permit an answer")
        if decision.verified_outcome_ids and not set(decision.verified_outcome_ids).issubset(successful):
            raise DecisionValidationError("Answer cites an unverified or failed ToolOutcome")
    return decision


def estimate_envelope_tokens(envelope: ModelTurnEnvelope) -> int:
    payload = envelope.model_dump_json(exclude_none=True)
    return max(1, (len(payload) + 3) // 4)


def tool_definition_from_runtime(tool: Any, *, approval_required: bool = False) -> ToolDefinition:
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None:
        try:
            schema = args_schema.model_json_schema()
        except Exception:
            try:
                schema = args_schema.schema()
            except Exception:
                pass
    elif isinstance(getattr(tool, "args", None), dict):
        schema = {"type": "object", "properties": dict(getattr(tool, "args"))}
    name = str(getattr(tool, "name", "") or "").strip()
    entry = None
    try:
        from agent.tool_registry import ToolRegistry

        entry = ToolRegistry.get(name)
    except Exception:
        entry = None
    if entry is not None and entry.input_schema:
        schema = dict(entry.input_schema)
    mutating_prefixes = ("file_write", "file_delete", "file_move", "file_copy", "terminal_run", "desktop_")
    definition = ToolDefinition(
        name=name,
        description=str(getattr(tool, "description", "") or "").strip(),
        parameters=schema,
        result_schema=dict(getattr(entry, "output_schema", {}) or {}) if entry is not None else {},
        mutating=bool(
            name.startswith(mutating_prefixes)
            or (entry is not None and entry.is_action)
        ),
        approval_required=bool(
            approval_required
            or (entry is not None and (entry.approval_required or entry.is_action))
        ),
        origin=str(getattr(entry, "origin", "native") or "native"),
        owner=str(getattr(entry, "owner", "builtin") or "builtin"),
        connection_id=str(getattr(entry, "connection_id", "") or ""),
        mcp_server=str(getattr(entry, "mcp_server", "") or ""),
        health=str(getattr(entry, "health", "healthy") or "unknown"),
        available=bool(getattr(entry, "available", True)),
        category=str(getattr(entry, "category", "general") or "general"),
    )
    from agent.research_runtime import capability_descriptor_from_tool

    return definition.model_copy(update={
        "capability": capability_descriptor_from_tool(definition)
    })


def verified_outcomes_for_scope(
    outcomes: Iterable[ToolOutcome], *, execution_id: str, session_id: str, project_id: str
) -> list[ToolOutcome]:
    return [
        item
        for item in outcomes
        if item.execution_id == execution_id
        and item.session_id == session_id
        and (item.project_id or "") == (project_id or "")
        and is_usable_verified_outcome(item)
    ]


def _validate_json_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    required = [str(item) for item in schema.get("required", [])]
    missing = [name for name in required if name not in arguments]
    if missing:
        raise DecisionValidationError(f"Missing required tool arguments: {', '.join(missing)}")
    properties = schema.get("properties") or {}
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            raise DecisionValidationError(f"Unknown tool arguments: {', '.join(unknown)}")
    for name, value in arguments.items():
        property_schema = properties.get(name)
        if isinstance(property_schema, dict):
            _validate_schema_value(property_schema, value, path=name, root=schema)


def _normalized_tool_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _validate_schema_value(schema: dict[str, Any], value: Any, *, path: str, root: dict[str, Any]) -> None:
    reference = str(schema.get("$ref") or "")
    if reference.startswith("#/$defs/"):
        target = (root.get("$defs") or {}).get(reference.rsplit("/", 1)[-1])
        if isinstance(target, dict):
            _validate_schema_value(target, value, path=path, root=root)
            return
    variants = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(variants, list) and variants:
        failures = 0
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            try:
                _validate_schema_value(variant, value, path=path, root=root)
                return
            except DecisionValidationError:
                failures += 1
        if failures:
            raise DecisionValidationError(f"Tool argument {path!r} does not match an allowed schema variant")
    if "enum" in schema and value not in list(schema.get("enum") or []):
        raise DecisionValidationError(f"Tool argument {path!r} is outside its allowed values")
    expected = schema.get("type")
    if isinstance(expected, list):
        expected_types = set(expected)
    elif expected:
        expected_types = {str(expected)}
    else:
        expected_types = set()
    type_checks = {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }
    if expected_types and not any(type_checks.get(item, True) for item in expected_types):
        raise DecisionValidationError(f"Tool argument {path!r} has the wrong type")
    if isinstance(value, dict) and (expected == "object" or "properties" in schema):
        _validate_json_arguments(schema, value)
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _validate_schema_value(schema["items"], item, path=f"{path}[{index}]", root=root)


def envelope_json_for_model(envelope: ModelTurnEnvelope) -> str:
    """Stable canonical JSON used by every adapter; never used for diagnostics."""
    return json.dumps(
        envelope.model_dump(mode="json", exclude_none=True, exclude={"assistant_identity"}),
        ensure_ascii=False,
        sort_keys=True,
    )


__all__ = [
    "AgentDecision",
    "ApprovalState",
    "CONTRACT_VERSION",
    "DecisionKind",
    "DecisionValidationError",
    "ModelTurnEnvelope",
    "RuntimeIdentity",
    "SkillRuntimeState",
    "StreamedToolCallFragment",
    "TaskState",
    "ToolCall",
    "ToolDefinition",
    "ToolOutcome",
    "ToolUsePolicy",
    "envelope_json_for_model",
    "estimate_envelope_tokens",
    "is_usable_verified_outcome",
    "tool_definition_from_runtime",
    "validate_agent_decision",
    "verified_outcomes_for_scope",
]
