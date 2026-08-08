"""Shared compiler and bounded execution loop for model-facing work."""
from __future__ import annotations

import hashlib
import json
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Protocol

from loguru import logger

from agent.model_adapters import AssembledModelResponse, ModelFamilyAdapter, get_family_adapter
from agent.identity import EchoIdentityProjection
from agent.model_contracts import (
    AgentDecision,
    ApprovalState,
    DecisionKind,
    DecisionValidationError,
    ModelTurnEnvelope,
    RuntimeIdentity,
    SkillRuntimeState,
    TaskState,
    ToolDefinition,
    ToolOutcome,
    ToolUsePolicy,
    is_usable_verified_outcome,
    validate_agent_decision,
    verified_outcomes_for_scope,
)
from agent.research_runtime import (
    CapabilitySnapshot,
    CompletionDisposition,
    CompletionVerdict,
    RequirementCompletionEvaluator,
    RequirementKind,
    RequirementState,
    RequirementStatus,
    ResearchBudgetExceeded,
    TaskRunAdvanceDecision,
    TaskRunNextAction,
    TurnRequirement,
    build_capability_snapshot,
    choose_active_requirement,
    compile_turn_requirements,
    demote_unverified_retrieval_states,
    format_weather_live_summary,
    reconcile_requirement_states,
    rekind_misclassified_live_requirements,
    requirement_has_verified_evidence,
    requirement_requires_verified_tool_evidence,
    summarize_tool_evidence_passage,
)
from agent.execution_graph import build_task_graph, reconcile_graph_state
from agent.retrieval_contracts import USABLE_RESULT_STATES


CONTROL_PLANE_VERSION = "8.0.0"
ENVELOPE_MARKER = "[ECHOSPEAK_MODEL_TURN_ENVELOPE]"


def safe_decision_rejection_message(
    envelope: ModelTurnEnvelope,
    *,
    decision: Optional[AgentDecision] = None,
) -> str:
    """Translate an authority rejection into honest user-facing language.

    The detailed contract exception remains in structured diagnostics. It must
    not be copied into chat, where internal class names and validation wording
    are neither useful nor actionable.
    """
    if envelope.approval.status in {"required", "pending"}:
        return "That action is waiting for your approval. Confirm or cancel it to continue."
    if envelope.task.missing_inputs:
        return "I still need a little more information before I can continue safely."
    # Never erase successful structured ToolOutcomes behind a generic refusal.
    evidence_lines = collect_structured_evidence_lines(envelope)
    structured = synthesize_structured_evidence_answer(envelope)
    if evidence_lines and structured:
        return structured
    # Mixed multi-part turns: never erase satisfied memory/answer-only branches.
    # But never surface raw requirement objective labels as the chat answer when
    # tools were required and no ToolRun landed (that looked like "model talking
    # to itself" / instruction echo).
    tools_required = envelope.tool_use_policy == ToolUsePolicy.REQUIRED
    has_outcomes = bool(envelope.verified_tool_outcomes)
    if not (tools_required and not has_outcomes):
        mixed_partial = synthesize_mixed_requirement_partial(envelope)
        if mixed_partial and not _looks_like_requirement_objective_echo(mixed_partial, envelope):
            return mixed_partial
    if envelope.completion_evaluation.unresolved_ids:
        return (
            "I haven't verified every requested part yet. "
            "I stopped before presenting an incomplete result as finished."
        )
    if tools_required:
        states = {item.result_state for item in envelope.verified_tool_outcomes}
        if "provider_unavailable" in states:
            return (
                "I couldn't reach a working source for that lookup, and I wasn't able to "
                "verify it another way. I won't guess."
            )
        if states & {"no_data", "insufficient_evidence", "stale_data", "ambiguous_entity"}:
            return (
                "The available sources didn't return enough reliable information to answer "
                "that confidently, so I haven't guessed."
            )
        if decision is not None and decision.kind == DecisionKind.CALL_TOOL:
            return (
                "I still need to run a verified lookup for the remaining public-source part, "
                "but the tool call was not accepted under the current requirement binding. "
                "Please retry that part."
            )
        return "I need to verify that with an available source before I answer, so I won't guess."
    if decision is not None and decision.kind == DecisionKind.CALL_TOOL:
        return "I couldn't use that action safely with the current permissions and inputs."
    return "I couldn't complete that request safely in the current state."


def _looks_like_requirement_objective_echo(text: str, envelope: ModelTurnEnvelope) -> bool:
    """Detect finalizer text that is only a restatement of requirement labels."""

    low = re.sub(r"\s+", " ", str(text or "")).strip().casefold()
    if not low:
        return False
    objectives = [
        re.sub(r"\s+", " ", str(item.objective or "")).strip().casefold()
        for item in list(envelope.task.requirements or [])
        if str(item.objective or "").strip()
    ]
    if not objectives:
        return False
    # If most requirement objectives appear verbatim, this is instruction leak.
    hits = sum(1 for item in objectives if item and item in low)
    return hits >= max(1, min(2, len(objectives))) and len(low) < 800


def _outcome_success_output(outcome: ToolOutcome) -> str:
    if str(getattr(outcome, "execution_status", "") or "") != "success":
        return ""
    if str(getattr(outcome, "result_state", "") or "") not in USABLE_RESULT_STATES:
        return ""
    return str(getattr(outcome, "output", "") or "").strip()


def _summarize_outcome_for_user(outcome: ToolOutcome) -> str:
    output = _outcome_success_output(outcome)
    if not output:
        return ""
    tool_name = str(getattr(outcome, "tool_name", "") or "")
    if tool_name == "weather_live":
        return format_weather_live_summary(output)
    return summarize_tool_evidence_passage(tool_name, output)


def collect_structured_evidence_lines(
    envelope: ModelTurnEnvelope,
    *,
    include_open: bool = True,
) -> list[str]:
    """Preserve successful ToolOutcome facts for partial or complete synthesis."""

    rows = list(envelope.task.requirements or [])
    states = dict(envelope.task.requirement_states or {})
    outcomes = list(envelope.verified_tool_outcomes or [])
    lines: list[str] = []
    seen: set[str] = set()

    def _add(line: str) -> None:
        text = str(line or "").strip()
        if not text or text.casefold() in seen:
            return
        seen.add(text.casefold())
        lines.append(text)

    for req in rows:
        state = states.get(req.requirement_id)
        if state is None:
            continue
        if req.kind != RequirementKind.RETRIEVAL:
            continue
        if state.status in {
            RequirementStatus.PENDING,
            RequirementStatus.ACTIVE,
        } and not include_open:
            continue
        for passage in list(getattr(state, "evidence_passages", None) or []):
            _add(passage)
        bound_ids = set(state.tool_run_ids or [])
        for outcome in outcomes:
            if bound_ids and str(getattr(outcome, "run_id", "") or "") not in bound_ids:
                # Also accept requirement_id binding when run ids are missing on legacy rows.
                if str(getattr(outcome, "requirement_id", "") or "") != req.requirement_id:
                    continue
            summary = _summarize_outcome_for_user(outcome)
            _add(summary)
    # Unbound but successful weather/search outcomes still belong in the answer.
    for outcome in outcomes:
        summary = _summarize_outcome_for_user(outcome)
        _add(summary)
    return lines[:16]


def synthesize_structured_evidence_answer(envelope: ModelTurnEnvelope) -> str:
    """Build an honest answer that never discards successful structured ToolRuns.

    Successful weather_live (and other) results are stated first. Uncertainty is
    scoped only to remaining missing fields/entities — never a total lookup refusal.
    """

    rows = list(envelope.task.requirements or [])
    states = dict(envelope.task.requirement_states or {})
    evidence_lines = collect_structured_evidence_lines(envelope)
    missing_bits: list[str] = []
    open_bits: list[str] = []
    failed_without_evidence: list[str] = []
    for req in rows:
        if req.kind != RequirementKind.RETRIEVAL:
            continue
        state = states.get(req.requirement_id)
        if state is None:
            continue
        label = str(req.objective or "one requested part").strip()
        has_passages = bool(list(getattr(state, "evidence_passages", None) or []) or state.tool_run_ids)
        if state.status == RequirementStatus.SATISFIED:
            continue
        if state.missing_fields:
            missing_bits.append(
                f"{label}: missing fields {', '.join(state.missing_fields[:6])}"
            )
        if list(getattr(state, "missing_entities", None) or []):
            missing_bits.append(
                f"{label}: missing places {', '.join(state.missing_entities[:6])}"
            )
        if state.status in {
            RequirementStatus.PENDING, RequirementStatus.ACTIVE, RequirementStatus.WEAK,
        }:
            open_bits.append(label)
        elif state.status in {
            RequirementStatus.EXHAUSTED, RequirementStatus.UNAVAILABLE, RequirementStatus.BLOCKED,
        } and not has_passages and not evidence_lines:
            failed_without_evidence.append(label)
        elif state.status == RequirementStatus.EXHAUSTED and state.terminal_reason == (
            "partial_verified_evidence_budget_exhausted"
        ):
            if state.missing_fields or list(getattr(state, "missing_entities", None) or []):
                missing_bits.append(label + " (partial verified evidence; budget ended)")
    chunks: list[str] = []
    if evidence_lines:
        if len(evidence_lines) == 1:
            chunks.append(evidence_lines[0])
        else:
            chunks.append("Here is what the verified lookups returned:\n- " + "\n- ".join(evidence_lines))
    # Memory branches may contribute stored facts — never dump raw requirement
    # objective labels (those are work items, not answers).
    for item in list(envelope.relevant_memory or [])[:8]:
        content = str(item.get("content") or item.get("text") or "").strip()
        kind = str(item.get("type") or "").casefold()
        if content and kind in {"profile", "preference", "identity", "fact"}:
            chunks.append(f"From stored context: {content[:400]}")
            break
    if evidence_lines and missing_bits:
        chunks.append(
            "I retrieved the structured results above, but could not fully verify: "
            + "; ".join(list(dict.fromkeys(missing_bits))[:6])
            + "."
        )
    elif evidence_lines and open_bits and not missing_bits:
        chunks.append(
            "I still have open follow-up work for: "
            + "; ".join(open_bits[:6])
            + "."
        )
    elif not evidence_lines and failed_without_evidence:
        chunks.append(
            "I could not complete a reliable lookup for: "
            + "; ".join(failed_without_evidence[:6])
            + ". I will not invent those results."
        )
    elif not evidence_lines and open_bits:
        chunks.append(
            "I still need a verified lookup for: "
            + "; ".join(open_bits[:6])
            + "."
        )
    return " ".join(str(item).strip() for item in chunks if str(item).strip()).strip()


def synthesize_mixed_requirement_partial(envelope: ModelTurnEnvelope) -> str:
    """Preserve satisfied branches and successful ToolOutcomes when work is incomplete."""
    rows = list(envelope.task.requirements or [])
    states = dict(envelope.task.requirement_states or {})
    evidence_answer = synthesize_structured_evidence_answer(envelope)
    # Prefer evidence-preserving synthesis whenever successful structured results exist.
    evidence_lines = collect_structured_evidence_lines(envelope)
    if evidence_lines:
        return evidence_answer
    if len(rows) < 2:
        # Single retrieval with no usable evidence still falls through to honesty wording.
        return evidence_answer
    satisfied_parts: list[str] = []
    failed_parts: list[str] = []
    open_retrieval: list[str] = []
    for req in rows:
        state = states.get(req.requirement_id)
        if state is None:
            continue
        objective = str(req.objective or "").strip()
        if state.status == RequirementStatus.SATISFIED and req.kind in {
            RequirementKind.ANSWER_ONLY, RequirementKind.MEMORY, RequirementKind.CALCULATION,
        }:
            satisfied_parts.append(objective or "one requested part")
        elif req.kind == RequirementKind.RETRIEVAL:
            if state.status in {
                RequirementStatus.UNAVAILABLE,
                RequirementStatus.EXHAUSTED,
                RequirementStatus.BLOCKED,
            }:
                failed_parts.append(objective or "one requested part")
            elif state.status in {
                RequirementStatus.PENDING, RequirementStatus.ACTIVE, RequirementStatus.WEAK,
            }:
                open_retrieval.append(objective or "one requested part")
            elif state.status == RequirementStatus.SATISFIED and not (
                state.evidence_ids or state.tool_run_ids
            ):
                open_retrieval.append(objective or "one requested part")
    if not satisfied_parts and not failed_parts and not open_retrieval:
        return evidence_answer
    if not (failed_parts or open_retrieval):
        return evidence_answer
    chunks: list[str] = []
    if satisfied_parts:
        chunks.append(
            "Here is what I can answer from conversation and local memory: "
            + "; ".join(satisfied_parts[:6])
            + "."
        )
    # Prefer memory-backed name/identity hints already in relevant_memory.
    for item in list(envelope.relevant_memory or [])[:8]:
        content = str(item.get("content") or item.get("text") or "").strip()
        kind = str(item.get("type") or "").casefold()
        if content and kind in {"profile", "preference", "identity", "fact"}:
            chunks.append(f"From stored context: {content[:400]}")
            break
    if open_retrieval:
        chunks.append(
            "I still need a verified public-source lookup for: "
            + "; ".join(open_retrieval[:6])
            + ". Those parts are not finished yet."
        )
    if failed_parts:
        chunks.append(
            "I could not complete a reliable public-source lookup for: "
            + "; ".join(failed_parts[:6])
            + ". I will not invent those results."
        )
    return " ".join(chunks).strip()


class ModelProviderError(RuntimeError):
    """The selected provider failed before producing a canonical decision."""


class ModelStreamIdleTimeout(ModelProviderError):
    """A provider stream stopped making meaningful progress."""

    def __init__(
        self,
        idle_seconds: float,
        *,
        progress_chars: int = 0,
        event_count: int = 0,
    ) -> None:
        self.idle_seconds = float(idle_seconds)
        self.progress_chars = int(progress_chars)
        self.event_count = int(event_count)
        super().__init__(
            "selected provider stream made no meaningful progress for "
            f"{self.idle_seconds:.1f}s"
        )


def _provider_error_retryability(
    exc: Exception,
    *,
    partial_stream: bool,
) -> tuple[bool, str]:
    """Classify provider failures without creating a second retry authority."""

    if isinstance(exc, ModelStreamIdleTimeout):
        return False, "stream_idle_timeout"
    if partial_stream:
        return False, "partial_stream_failed"

    error_type = type(exc).__name__.casefold()
    message = str(exc or "").casefold()
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
    try:
        status_code = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status_code = None

    non_retryable_types = (
        "badrequest",
        "authentication",
        "permission",
        "notfound",
        "unprocessable",
    )
    non_retryable_messages = (
        "engine protocol startup was aborted",
        "failed to load model",
        "context length",
        "maximum context",
        "invalid request",
        "invalid_request",
    )
    if any(item in error_type for item in non_retryable_types):
        return False, "non_retryable_provider_error"
    if any(item in message for item in non_retryable_messages):
        return False, "non_retryable_provider_error"
    if status_code is not None:
        retryable_status = (
            status_code in {408, 409, 425, 429} or status_code >= 500
        )
        return (
            retryable_status,
            "transient_provider_error"
            if retryable_status
            else "non_retryable_provider_error",
        )

    transient_types = (
        "apierror",
        "apiconnection",
        "ratelimit",
        "timeout",
        "connection",
    )
    if any(item in error_type for item in transient_types):
        return True, "transient_provider_error"
    return False, "unclassified_provider_error"


class RuntimeProposalFeedback(RuntimeError):
    """Trusted, non-executing feedback for a rejected model proposal.

    This is deliberately not a ToolOutcome: no tool crossed the execution
    boundary.  The control plane may give the selected model a bounded chance
    to choose another currently valid action, while the runtime remains the
    sole authority for tools, requirements, approvals, and completion.
    """

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        retryable: bool = True,
        task_run_id: str = "",
        requirement_id: str = "",
        attempt_id: str = "",
        allowed_actions: Optional[Iterable[str]] = None,
        allowed_tools: Optional[Iterable[str]] = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = str(reason_code or "proposal_rejected")[:120]
        self.safe_message = str(message or "The proposed action is not currently valid.")[:500]
        self.retryable = bool(retryable)
        self.task_run_id = str(task_run_id or "")[:120]
        self.requirement_id = str(requirement_id or "")[:120]
        self.attempt_id = str(attempt_id or "")[:120]
        self.allowed_actions = sorted({str(item) for item in (allowed_actions or []) if str(item)})[:16]
        self.allowed_tools = sorted({str(item) for item in (allowed_tools or []) if str(item)})[:128]
        digest_source = json.dumps(
            {
                "reason_code": self.reason_code,
                "task_run_id": self.task_run_id,
                "requirement_id": self.requirement_id,
                "attempt_id": self.attempt_id,
                "allowed_actions": self.allowed_actions,
                "allowed_tools": self.allowed_tools,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self.diagnostic_id = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]

    def safe_dict(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "retryable": self.retryable,
            "task_run_id": self.task_run_id,
            "requirement_id": self.requirement_id,
            "attempt_id": self.attempt_id,
            "allowed_actions": list(self.allowed_actions),
            "allowed_tools": list(self.allowed_tools),
            "diagnostic_id": self.diagnostic_id,
        }

    def model_feedback(self) -> str:
        actions = ", ".join(self.allowed_actions) or "use the refreshed envelope"
        tools = ", ".join(self.allowed_tools) or "none"
        return (
            "The runtime rejected the previous proposal before execution. "
            f"Reason code: {self.reason_code}. {self.safe_message} "
            f"Current valid actions: {actions}. Current allowed tools: {tools}. "
            "Choose one valid next action from the refreshed envelope; do not repeat the rejected proposal."
        )


class ModelTransport(Protocol):
    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str,
        adapter: ModelFamilyAdapter,
        on_event: Optional[Callable[[dict[str, Any]], None]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> AssembledModelResponse: ...


class LangChainStreamingTransport:
    """Adapt one LangChain chat model to the canonical streaming boundary.

    This transport performs syntax conversion only. The control plane still
    owns loop bounds and decisions, while EchoSpeak's authority-wrapped tools
    own execution and persistence.
    """

    def __init__(
        self,
        chat_model: Any,
        *,
        stream_idle_timeout_seconds: float = 45.0,
        poll_interval_seconds: float = 0.1,
        generation_parameters: Optional[dict[str, Any]] = None,
        callbacks: Optional[list[Any]] = None,
    ) -> None:
        self.chat_model = chat_model
        self.stream_idle_timeout_seconds = max(
            0.05, float(stream_idle_timeout_seconds)
        )
        self.poll_interval_seconds = max(0.01, float(poll_interval_seconds))
        self.generation_parameters = dict(generation_parameters or {})
        self.callbacks = list(callbacks or [])

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str,
        adapter: ModelFamilyAdapter,
        on_event: Optional[Callable[[dict[str, Any]], None]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> AssembledModelResponse:
        runnable = self.chat_model
        if tools:
            if not hasattr(runnable, "bind_tools"):
                raise RuntimeError("Selected chat model does not support bound tool definitions")
            runnable = runnable.bind_tools(tools, tool_choice=tool_choice)
        if self.generation_parameters:
            runnable = runnable.bind(**self.generation_parameters)
        outbound = _to_langchain_messages(messages)
        events: list[dict[str, Any]] = []
        aggregate: Any = None
        stream_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        stop_event = threading.Event()

        def produce() -> None:
            iterator: Any = None
            try:
                if self.callbacks:
                    iterator = runnable.stream(
                        outbound,
                        config={"callbacks": self.callbacks},
                    )
                else:
                    iterator = runnable.stream(outbound)
                for chunk in iterator:
                    if stop_event.is_set():
                        break
                    stream_queue.put(("chunk", chunk))
                stream_queue.put(("done", None))
            except BaseException as exc:
                stream_queue.put(("error", exc))
            finally:
                close = getattr(iterator, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass

        worker = threading.Thread(
            target=produce,
            name="echospeak-model-stream",
            daemon=True,
        )
        worker.start()
        last_progress_at = time.monotonic()
        progress_chars = 0
        try:
            while True:
                if cancel and cancel():
                    stop_event.set()
                    return AssembledModelResponse(finish_reason="cancelled")
                remaining = self.stream_idle_timeout_seconds - (
                    time.monotonic() - last_progress_at
                )
                if remaining <= 0:
                    stop_event.set()
                    raise ModelStreamIdleTimeout(
                        self.stream_idle_timeout_seconds,
                        progress_chars=progress_chars,
                        event_count=len(events),
                    )
                try:
                    event_kind, value = stream_queue.get(
                        timeout=min(self.poll_interval_seconds, remaining)
                    )
                except queue.Empty:
                    continue
                if event_kind == "done":
                    break
                if event_kind == "error":
                    raise value
                chunk = value
                aggregate = chunk if aggregate is None else aggregate + chunk
                event = _langchain_chunk_event(chunk)
                events.append(event)
                choice = (event.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                fragments = delta.get("tool_calls") or []
                content_chars = len(str(delta.get("content") or ""))
                reasoning_chars = len(str(delta.get("reasoning_content") or ""))
                argument_chars = sum(
                    len(str((item.get("function") or {}).get("arguments") or ""))
                    for item in fragments if isinstance(item, dict)
                )
                tool_names = ",".join(
                    str((item.get("function") or {}).get("name") or "")
                    for item in fragments if isinstance(item, dict)
                )
                finish_reason = str(choice.get("finish_reason") or "")
                meaningful_chars = content_chars + reasoning_chars + argument_chars
                if meaningful_chars or tool_names or finish_reason:
                    progress_chars += meaningful_chars
                    last_progress_at = time.monotonic()
                if on_event:
                    on_event({
                        "type": "stream_delta",
                        "content_chars": content_chars,
                        "reasoning_chars": reasoning_chars,
                        "argument_chars": argument_chars,
                        "tool": tool_names,
                        "finish_reason": finish_reason,
                    })
        finally:
            stop_event.set()
            worker.join(timeout=0.25)
        parsed = adapter.parse_stream(events)
        # A provider may expose only normalized tool_calls on its aggregate
        # chunk. Preserve family parsing while accepting that representation.
        if aggregate is not None and not parsed.tool_calls and not parsed.content and not parsed.malformed_calls:
            return adapter.parse_response(aggregate)
        return parsed


@dataclass
class ControlPlaneTrace:
    execution_id: str
    provider: str
    model_id: str
    model_family: str
    template: str
    adapter_version: str
    loop_count: int = 0
    decisions: list[dict[str, Any]] = field(default_factory=list)
    tool_runs: list[str] = field(default_factory=list)
    parser_events: list[dict[str, Any]] = field(default_factory=list)
    proposal_feedback: list[dict[str, Any]] = field(default_factory=list)
    terminal_status: str = "started"
    started_at: float = field(default_factory=time.time)
    completed_at: float = 0.0

    def safe_dict(self) -> dict[str, Any]:
        parser_tools = sorted({str(item.get("tool") or "") for item in self.parser_events if item.get("tool")})
        return {
            "execution_id": self.execution_id,
            "provider": self.provider,
            "model_id": self.model_id,
            "model_family": self.model_family,
            "template": self.template,
            "adapter_version": self.adapter_version,
            "loop_count": self.loop_count,
            "decisions": list(self.decisions),
            "tool_runs": list(self.tool_runs),
            "parser_event_count": len(self.parser_events),
            "parser_content_chars": sum(int(item.get("content_chars") or 0) for item in self.parser_events),
            "parser_reasoning_chars": sum(int(item.get("reasoning_chars") or 0) for item in self.parser_events),
            "parser_argument_chars": sum(int(item.get("argument_chars") or 0) for item in self.parser_events),
            "parser_tool_names": parser_tools,
            "proposal_feedback": list(self.proposal_feedback),
            "terminal_status": self.terminal_status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class ModelTurnEnvelopeCompiler:
    """The single authoritative compiler from runtime state to model input."""

    def compile(
        self,
        *,
        project_id: str,
        session_id: str,
        turn_id: str,
        execution_id: str,
        request_id: str,
        provider: str,
        model_id: str,
        assistant_identity: EchoIdentityProjection,
        objective: str,
        task_status: str,
        current_plan_step: Optional[dict[str, Any]],
        collected_inputs: Optional[dict[str, Any]],
        missing_inputs: Optional[list[str]],
        latest_user_relation: str,
        latest_user_message: str,
        allowed_tools: Iterable[ToolDefinition],
        tool_use_policy: ToolUsePolicy,
        relevant_memory: Optional[list[dict[str, Any]]],
        approval: Optional[ApprovalState],
        tool_outcomes: Iterable[ToolOutcome],
        skill: Optional[SkillRuntimeState] = None,
        constraints: Optional[list[str]] = None,
        canonical_turn_relation: str = "",
        task_requirements: Optional[list[TurnRequirement]] = None,
        requirement_states: Optional[dict[str, RequirementState]] = None,
        capability_snapshot: Optional[CapabilitySnapshot] = None,
        task_run_id: str = "",
        execution_profile: str = "chat",
        graph_id: str = "",
        active_graph_node_ids: Optional[list[str]] = None,
        liveness_decision: Optional[TaskRunAdvanceDecision] = None,
    ) -> ModelTurnEnvelope:
        adapter = get_family_adapter(model_id, provider)
        all_outcomes = list(tool_outcomes)
        scoped_attempt_outcomes = [
            item
            for item in all_outcomes
            if item.execution_id == execution_id
            and item.session_id == session_id
            and (item.project_id or "") == (project_id or "")
        ]
        scoped_outcomes = verified_outcomes_for_scope(
            all_outcomes,
            execution_id=execution_id,
            session_id=session_id,
            project_id=project_id,
        )
        remaining_chars = 32000
        bounded_outcomes: list[ToolOutcome] = []
        for item in reversed(scoped_outcomes):
            allowed_chars = max(0, min(12000, remaining_chars))
            output = str(item.output or "")
            bounded = output[:allowed_chars]
            bounded_outcomes.append(item.model_copy(update={
                "output": bounded + ("\n[bounded evidence projection]" if len(output) > len(bounded) else "")
            }))
            remaining_chars -= len(bounded)
        scoped_outcomes = list(reversed(bounded_outcomes))
        approval_state = approval or ApprovalState()
        missing = list(missing_inputs or [])
        tool_list = list(allowed_tools)
        # A deterministic, non-retryable provider failure must not be offered
        # again in the same Execution. Other currently allowed tools remain
        # available as governed fallbacks.
        unavailable_tools = {
            item.tool_name
            for item in scoped_attempt_outcomes
            if not item.retryable
            and item.result_state in {"provider_unavailable", "unsupported_intent"}
        }
        if unavailable_tools:
            tool_list = [item for item in tool_list if item.name not in unavailable_tools]
        # Prefer the TaskRun ledger as-is. Synthetic capability recompilation used to
        # coerce retrieval requirements into answer_only under OPTIONAL/PROHIBITED
        # policies and falsely complete mixed multi-part turns.
        if task_requirements:
            requirements = [
                item if isinstance(item, TurnRequirement) else TurnRequirement.model_validate(item)
                for item in list(task_requirements)[:16]
            ]
            # Repair answer_only weather/search mislabels before seed/completion.
            requirements = rekind_misclassified_live_requirements(requirements)
            states = reconcile_requirement_states(requirements, requirement_states or {})
        else:
            requirements = compile_turn_requirements(
                [],
                objective=objective or latest_user_message,
                capabilities=(
                    ["research"] if tool_use_policy == ToolUsePolicy.REQUIRED else ["conversation"]
                ),
                missing_fields=missing,
            )
            states = reconcile_requirement_states(requirements, requirement_states or {})
            # Compatibility callers without a TaskRun ledger: project successful
            # tool boundary results into the single synthetic requirement.
            def _evidence_outcome(item: ToolOutcome) -> bool:
                return is_usable_verified_outcome(item)

            successful = [item for item in scoped_outcomes if _evidence_outcome(item)]
            if successful and requirements:
                only = requirements[0]
                run_ids = [item.run_id for item in successful if str(item.run_id or "").strip()]
                states[only.requirement_id] = states[only.requirement_id].model_copy(update={
                    "status": RequirementStatus.SATISFIED,
                    "covered_fields": list(only.requested_fields),
                    "missing_fields": [],
                    "tool_run_ids": run_ids,
                    "evidence_ids": list(run_ids),
                    "terminal_reason": "legacy_outcome_projected_into_requirement",
                })
        states = demote_unverified_retrieval_states(requirements, states)
        completion = RequirementCompletionEvaluator.evaluate(
            requirements,
            states,
            missing_inputs=missing,
            pending_approval=approval_state.status in {"required", "pending"},
        )
        # Hard invariant: tool-required turns with tool-backed requirements and zero
        # verified outcomes are never complete/finalizable.
        open_tool_backed_ids = [
            item.requirement_id
            for item in requirements
            if requirement_requires_verified_tool_evidence(item)
            and states.get(item.requirement_id)
            and states[item.requirement_id].status in {
                RequirementStatus.PENDING,
                RequirementStatus.ACTIVE,
                RequirementStatus.WEAK,
            }
        ]
        if (
            tool_use_policy == ToolUsePolicy.REQUIRED
            and not scoped_outcomes
            and any(requirement_requires_verified_tool_evidence(item) for item in requirements)
            and completion.finalizable
            and not missing
            and approval_state.status not in {"required", "pending"}
        ):
            # Re-open any falsely satisfied live requirements and recompute.
            for item in requirements:
                if not requirement_requires_verified_tool_evidence(item):
                    continue
                state = states.get(item.requirement_id)
                if state is None:
                    continue
                if requirement_has_verified_evidence(state):
                    continue
                states[item.requirement_id] = state.model_copy(update={
                    "status": RequirementStatus.PENDING,
                    "covered_fields": [],
                    "missing_fields": list(item.requested_fields),
                    "tool_run_ids": [],
                    "evidence_ids": [],
                    "terminal_reason": "demoted_complete_without_tool_evidence",
                    "updated_at": time.time(),
                })
            completion = RequirementCompletionEvaluator.evaluate(
                requirements,
                states,
                missing_inputs=missing,
                pending_approval=False,
            )
            open_tool_backed_ids = [
                item.requirement_id
                for item in requirements
                if requirement_requires_verified_tool_evidence(item)
                and states.get(item.requirement_id)
                and states[item.requirement_id].status in {
                    RequirementStatus.PENDING,
                    RequirementStatus.ACTIVE,
                    RequirementStatus.WEAK,
                }
            ]
        if liveness_decision is not None:
            live_ids = {
                item.requirement_id for item in requirements if item.required
            }
            if set(liveness_decision.completion.required_ids) != live_ids:
                raise ValueError(
                    "TaskRun liveness decision does not match the current requirement ledger"
                )
            if not set(liveness_decision.requirement_states).issubset({
                item.requirement_id for item in requirements
            }):
                raise ValueError(
                    "TaskRun liveness decision references an unknown requirement"
                )
            states = dict(liveness_decision.requirement_states)
            completion = liveness_decision.completion
            if liveness_decision.next_action == TaskRunNextAction.RUN_TOOL:
                eligible_names = set(liveness_decision.eligible_tool_names)
                tool_list = [
                    item for item in tool_list if item.name in eligible_names
                ]
                tool_use_policy = ToolUsePolicy.REQUIRED
            open_tool_backed_ids = [
                item.requirement_id
                for item in requirements
                if requirement_requires_verified_tool_evidence(item)
                and states.get(item.requirement_id)
                and states[item.requirement_id].status in {
                    RequirementStatus.PENDING,
                    RequirementStatus.ACTIVE,
                    RequirementStatus.WEAK,
                }
            ]
        # Projection disagreement: TaskRun requirement set vs evaluator required_ids.
        ledger_ids = [item.requirement_id for item in requirements if item.required]
        evaluator_ids = list(completion.required_ids or [])
        projection_aligned = ledger_ids == evaluator_ids or set(ledger_ids) == set(evaluator_ids)
        if not projection_aligned:
            completion = CompletionVerdict(
                disposition=CompletionDisposition.BLOCKED,
                finalizable=False,
                required_ids=ledger_ids,
                satisfied_ids=[
                    item_id for item_id in ledger_ids
                    if states.get(item_id) and states[item_id].status == RequirementStatus.SATISFIED
                ],
                unresolved_ids=[
                    item_id for item_id in ledger_ids
                    if not (
                        states.get(item_id) and states[item_id].status == RequirementStatus.SATISFIED
                    )
                ],
                reason_code="requirement_projection_disagreement",
            )
        active_requirement = (
            next(
                (
                    item for item in requirements
                    if liveness_decision is not None
                    and item.requirement_id == liveness_decision.active_requirement_id
                ),
                None,
            )
            if liveness_decision is not None
            else choose_active_requirement(requirements, states)
        )
        unresolved_tool_backed = bool(open_tool_backed_ids) or any(
            item.kind in {
                RequirementKind.RETRIEVAL,
                RequirementKind.LOCAL_CONTEXT,
                RequirementKind.CALCULATION,
            }
            and states.get(item.requirement_id)
            and states[item.requirement_id].status in {
                RequirementStatus.PENDING,
                RequirementStatus.ACTIVE,
                RequirementStatus.WEAK,
            }
            for item in requirements
        )
        # Never trust a stale finalize-only graph while tool-backed work is open.
        computed_active_nodes = list(active_graph_node_ids or [])
        try:
            graph = build_task_graph(
                task_run_id=str(task_run_id or "envelope"),
                requirements=requirements,
                budget=None,
            )
            gstate = reconcile_graph_state(
                graph,
                None,
                requirement_states=states,
                completion=completion,
                task_status=task_status or "running",
            )
            computed_active_nodes = list(gstate.active_node_ids or [])
        except Exception as exc:
            logger.warning("Envelope graph reconcile failed closed to requirement nodes: {}", exc)
            if unresolved_tool_backed:
                computed_active_nodes = [
                    f"requirement:{item_id}" for item_id in open_tool_backed_ids[:8]
                ] or computed_active_nodes
        if unresolved_tool_backed and computed_active_nodes == ["finalize"]:
            computed_active_nodes = [
                f"requirement:{item_id}" for item_id in open_tool_backed_ids[:8]
            ] or [item.requirement_id for item in requirements[:4]]
        active_graph_node_ids = computed_active_nodes
        actions = self._valid_next_actions(
            tool_policy=tool_use_policy,
            has_tools=bool(tool_list),
            has_actionable_requirement=active_requirement is not None or unresolved_tool_backed,
            missing_inputs=missing,
            approval=approval_state,
            outcomes=scoped_outcomes,
            completion=completion,
            unresolved_tool_backed=unresolved_tool_backed,
            liveness_decision=liveness_decision,
        )
        completion_requirements = self._completion_requirements(
            tool_policy=tool_use_policy,
            missing_inputs=missing,
            approval=approval_state,
            completion=completion,
            requirements=requirements,
            states=states,
        )
        if active_requirement is not None:
            active_state = states[active_requirement.requirement_id]
            missing_fields = list(getattr(active_state, "missing_fields", None) or [])
            missing_entities = list(getattr(active_state, "missing_entities", None) or [])
            recommended = list(getattr(active_state, "recommended_tools", None) or [])
            covered_entities = list(getattr(active_state, "covered_entities", None) or [])
            strategy = (
                liveness_decision.recovery_strategy
                if liveness_decision is not None and liveness_decision.recovery_strategy
                else active_state.last_strategy or "primary_capability"
            )
            guidance = (
                "Work only on active_requirement_id=" + active_requirement.requirement_id
                + f". recovery_strategy={strategy}."
            )
            if missing_fields:
                guidance += " missing_fields=[" + ", ".join(missing_fields[:8]) + "]."
            if missing_entities:
                guidance += (
                    " missing_entities=[" + ", ".join(missing_entities[:8]) + "]"
                    + " — call the structured tool with the missing place/argument;"
                    + " do not repeat a city already covered."
                )
            if covered_entities:
                guidance += " already_covered_entities=[" + ", ".join(covered_entities[:8]) + "]."
            if recommended:
                guidance += " preferred_tools=[" + ", ".join(recommended[:6]) + "]."
            if liveness_decision is not None and liveness_decision.preferred_tool_name:
                guidance += (
                    " runtime_preferred_tool="
                    + liveness_decision.preferred_tool_name
                    + "."
                )
            if strategy in {
                "alternate_provider", "authority_targeted_search",
                "safe_page_fetch", "structured_page_extraction", "alternate_source",
            }:
                guidance += (
                    " Do not repeat the same primary tool with the same arguments;"
                    " change location/query, switch provider, or use search/fetch."
                )
            guidance += " Do not rerun satisfied requirements."
            completion_requirements.append(guidance)
        constraint_rows = list(constraints or [])
        if not projection_aligned:
            constraint_rows.append("requirement_projection_disagreement")
            logger.error(
                "Requirement projection disagreement ledger={} evaluator={}",
                ledger_ids,
                evaluator_ids,
            )
        canonical = str(canonical_turn_relation or "").strip()
        derived_relation = {
            "new_task": "new_work",
            "continue_task": "continue",
            "provide_task_input": "continue",
            "correct_task": "continue",
            "switch_task": "continue",
            "cancel_task": "cancel",
            "resume_approval": "confirm",
            "casual_conversation": "other",
        }.get(canonical)
        relation_candidate = derived_relation if derived_relation is not None else latest_user_relation
        relation = relation_candidate if relation_candidate in {
            "new_work", "continue", "confirm", "retry", "cancel", "other"
        } else "other"
        return ModelTurnEnvelope(
            identity=RuntimeIdentity(
                project_id=project_id,
                session_id=session_id,
                turn_id=turn_id,
                execution_id=execution_id,
                request_id=request_id,
            ),
            assistant_identity=assistant_identity,
            provider=provider,
            model_id=model_id,
            model_family=adapter.family.value,
            adapter_version=adapter.version,
            task=TaskState(
                task_run_id=str(task_run_id or ""),
                objective=objective or latest_user_message,
                status=task_status or "in_progress",
                execution_profile=(
                    execution_profile
                    if execution_profile in {"chat", "work", "code"}
                    else "chat"
                ),
                graph_id=str(graph_id or ""),
                active_graph_node_ids=list(active_graph_node_ids or []),
                current_plan_step=current_plan_step,
                collected_inputs=dict(collected_inputs or {}),
                missing_inputs=missing,
                latest_user_relation=relation,  # type: ignore[arg-type]
                canonical_turn_relation=canonical,
                requirements=requirements,
                requirement_states=states,
                active_requirement_id=(active_requirement.requirement_id if active_requirement else ""),
                completion=completion,
                next_runtime_action=(
                    liveness_decision.next_action
                    if liveness_decision is not None
                    else (
                        TaskRunNextAction.FINALIZE
                        if completion.finalizable
                        else TaskRunNextAction.RUN_TOOL
                        if active_requirement is not None
                        else TaskRunNextAction.HARD_FAILURE
                    )
                ),
                preferred_tool_name=(
                    liveness_decision.preferred_tool_name
                    if liveness_decision is not None else ""
                ),
                eligible_tool_names=(
                    list(liveness_decision.eligible_tool_names)
                    if liveness_decision is not None else [item.name for item in tool_list]
                ),
                recovery_strategy=(
                    liveness_decision.recovery_strategy
                    if liveness_decision is not None else ""
                ),
            ),
            skill=skill or SkillRuntimeState(),
            latest_user_message=latest_user_message,
            allowed_tools=tool_list,
            tool_use_policy=tool_use_policy,
            relevant_memory=list(relevant_memory or []),
            approval=approval_state,
            verified_tool_outcomes=scoped_outcomes,
            valid_next_actions=actions,
            completion_requirements=completion_requirements,
            capability_snapshot=(capability_snapshot or build_capability_snapshot(
                tool_list,
                inventory_revision=0,
                project_id=project_id,
                session_id=session_id,
            )),
            completion_evaluation=completion,
            constraints=constraint_rows,
        )

    @staticmethod
    def _valid_next_actions(
        *, tool_policy: ToolUsePolicy, has_tools: bool, has_actionable_requirement: bool,
        missing_inputs: list[str], approval: ApprovalState,
        outcomes: list[ToolOutcome], completion: CompletionVerdict,
        unresolved_tool_backed: bool = False,
        liveness_decision: Optional[TaskRunAdvanceDecision] = None,
    ) -> list[DecisionKind]:
        if liveness_decision is not None:
            action = liveness_decision.next_action
            if action == TaskRunNextAction.RUN_TOOL:
                return [
                    DecisionKind.CALL_TOOL,
                    DecisionKind.UPDATE_PLAN,
                    DecisionKind.CANCEL,
                    DecisionKind.BLOCK,
                ]
            if action == TaskRunNextAction.FINALIZE:
                return [
                    DecisionKind.ANSWER,
                    DecisionKind.UPDATE_PLAN,
                    DecisionKind.CANCEL,
                    DecisionKind.BLOCK,
                ]
            if action == TaskRunNextAction.WAIT_FOR_USER:
                return [
                    DecisionKind.ASK_FOR_INPUT,
                    DecisionKind.CANCEL,
                    DecisionKind.BLOCK,
                ]
            return [DecisionKind.BLOCK, DecisionKind.CANCEL]
        if approval.status in {"required", "pending"}:
            return [DecisionKind.BLOCK, DecisionKind.CANCEL]
        actions = [DecisionKind.UPDATE_PLAN, DecisionKind.CANCEL, DecisionKind.BLOCK]
        # A tool call requires an actual runtime-selected actionable
        # requirement. An unresolved diagnostic flag alone cannot authorize a
        # call; if selection is empty, completion/recovery must reconcile state.
        if (
            tool_policy != ToolUsePolicy.PROHIBITED
            and has_tools
            and has_actionable_requirement
        ):
            actions.insert(0, DecisionKind.CALL_TOOL)
        if missing_inputs:
            actions.insert(0, DecisionKind.ASK_FOR_INPUT)
        # Never answer while required tool-backed work is still open.
        if (
            not missing_inputs
            and completion.finalizable
            and not unresolved_tool_backed
        ):
            actions.insert(0, DecisionKind.ANSWER)
        elif (
            not missing_inputs
            and completion.disposition == CompletionDisposition.PARTIAL
            and completion.finalizable
            and unresolved_tool_backed is False
        ):
            actions.insert(0, DecisionKind.ANSWER)
        return actions

    @staticmethod
    def _completion_requirements(
        *, tool_policy: ToolUsePolicy, missing_inputs: list[str], approval: ApprovalState,
        completion: CompletionVerdict,
        requirements: Optional[list[TurnRequirement]] = None,
        states: Optional[dict[str, RequirementState]] = None,
    ) -> list[str]:
        # Instruction strings for the model. Count is diagnostic only; authoritative
        # requirement_count comes from the TaskRun ledger (see safe_diagnostics).
        rows = list(requirements or [])
        instructions = ["The final answer must be truthful and grounded in runtime-verified state."]
        instructions.append(
            "Keep the user response clean: cite supporting sources compactly and never dump internal evidence diagnostics."
        )
        if rows:
            instructions.append(
                "There are exactly "
                + str(len([item for item in rows if item.required]))
                + " required independent parts. Preserve every satisfied part in the final answer; "
                "scope failures only to unresolved requirement ids."
            )
        if tool_policy == ToolUsePolicy.REQUIRED:
            instructions.append(
                "Every retrieval requirement needs verified ToolRun evidence or a terminal recovery state "
                "before full completion. Do not mark retrieval complete from conversation alone."
            )
        if completion.unresolved_ids:
            instructions.append(
                "Resolve these independent requirement ids before full completion: "
                + ", ".join(completion.unresolved_ids[:12])
            )
            if states:
                pending_retrieval = [
                    item.requirement_id for item in rows
                    if item.requirement_id in set(completion.unresolved_ids)
                    and item.kind == RequirementKind.RETRIEVAL
                ]
                if pending_retrieval:
                    instructions.append(
                        "call_tool is required for retrieval requirement ids: "
                        + ", ".join(pending_retrieval[:12])
                    )
        if missing_inputs:
            instructions.append("Collect all runtime-declared missing inputs before answering.")
        if approval.status in {"required", "pending"}:
            instructions.append("Do not execute or claim completion while current approval is unresolved.")
        return instructions


class ModelExecutionControlPlane:
    """A bounded sequential tool loop shared by LM Studio conformance and native proof."""

    def __init__(
        self,
        *,
        max_loops: int = 12,
        max_tool_calls: int = 16,
        malformed_repair_attempts: int = 2,
        provider_retries: int = 1,
        provider_backoff_seconds: float = 0.35,
        no_progress_limit: int = 2,
        max_elapsed_seconds: float = 600.0,
    ) -> None:
        self.max_loops = max(1, min(int(max_loops), 26))
        self.max_tool_calls = max(0, min(int(max_tool_calls), 128))
        self.malformed_repair_attempts = max(
            0, min(int(malformed_repair_attempts), 6)
        )
        self.provider_retries = max(0, min(int(provider_retries), 4))
        self.provider_backoff_seconds = max(
            0.0, min(float(provider_backoff_seconds), 10.0)
        )
        self.no_progress_limit = max(1, min(int(no_progress_limit), 6))
        self.max_elapsed_seconds = max(
            1.0, min(float(max_elapsed_seconds), 3600.0)
        )

    def run(
        self,
        *,
        envelope_factory: Callable[[list[ToolOutcome]], ModelTurnEnvelope],
        transport: ModelTransport,
        execute_tool: Callable[[str, dict[str, Any]], ToolOutcome],
        apply_plan: Optional[Callable[[list[dict[str, Any]]], None]] = None,
        validate_answer: Optional[
            Callable[[ModelTurnEnvelope, AgentDecision], Optional[RuntimeProposalFeedback]]
        ] = None,
        cancel: Optional[Callable[[], bool]] = None,
        diagnostic_sink: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> tuple[AgentDecision, ControlPlaneTrace]:
        outcomes: list[ToolOutcome] = []
        first = envelope_factory(outcomes)
        adapter = get_family_adapter(first.model_id, first.provider)
        trace = ControlPlaneTrace(
            execution_id=first.identity.execution_id,
            provider=first.provider,
            model_id=first.model_id,
            model_family=adapter.family.value,
            template=adapter.template,
            adapter_version=adapter.version,
        )
        messages: list[dict[str, Any]] = []
        rejection_counts: dict[str, int] = {}
        rejection_total = 0
        malformed_repairs = 0
        tool_call_count = 0
        decision_repeats: dict[str, int] = {}
        started_monotonic = time.monotonic()
        post_tool_synthesis_pending = False
        post_tool_synthesis_grace_used = False

        def recover_from_feedback(feedback: RuntimeProposalFeedback) -> bool:
            nonlocal rejection_total
            key = feedback.reason_code
            rejection_counts[key] = rejection_counts.get(key, 0) + 1
            rejection_total += 1
            trace.proposal_feedback.append(feedback.safe_dict())
            self._emit(
                diagnostic_sink,
                {"event": "runtime_proposal_feedback", **feedback.safe_dict()},
            )
            if (
                not feedback.retryable
                or rejection_counts[key] > 1
                or rejection_total > 2
            ):
                return False
            messages.append({"role": "system", "content": feedback.model_feedback()})
            return True

        for loop_index in range(self.max_loops):
            if cancel and cancel():
                decision = AgentDecision(kind=DecisionKind.CANCEL, reason_code="cancelled_by_runtime")
                return self._finish(decision, trace, "cancelled", diagnostic_sink)
            if time.monotonic() - started_monotonic >= self.max_elapsed_seconds:
                # A tool result is not a user answer.  A slow local model must
                # receive one bounded opportunity to inspect newly persisted
                # ToolOutcomes and synthesize/terminalize them even when the
                # earlier provider call consumed the nominal turn budget.
                if post_tool_synthesis_pending and not post_tool_synthesis_grace_used:
                    post_tool_synthesis_pending = False
                    post_tool_synthesis_grace_used = True
                    self._emit(
                        diagnostic_sink,
                        {
                            "event": "post_tool_synthesis_grace",
                            "elapsed_seconds": round(
                                time.monotonic() - started_monotonic, 3
                            ),
                            "tool_outcome_count": len(outcomes),
                        },
                    )
                else:
                    blocked = AgentDecision(
                        kind=DecisionKind.BLOCK,
                        message=(
                            "I reached the bounded time budget for this work. "
                            "I preserved the progress already recorded."
                        ),
                        reason_code="model_loop_elapsed_budget",
                    )
                    return self._finish(blocked, trace, "blocked", diagnostic_sink)
            envelope = envelope_factory(outcomes)
            if envelope.identity.execution_id != trace.execution_id:
                raise RuntimeError("Envelope factory changed Execution identity during a tool loop")
            contract = adapter.render_system_contract(envelope)
            if not messages:
                messages = [
                    {"role": "system", "content": contract},
                    {"role": "user", "content": envelope.latest_user_message},
                ]
            else:
                messages[0] = {"role": "system", "content": contract}
            trace.loop_count = loop_index + 1
            self._emit(
                diagnostic_sink,
                {"event": "model_call", **envelope.safe_diagnostics(), "loop": trace.loop_count, "template": adapter.template},
            )
            # Crossing into this provider call consumes the pending synthesis
            # opportunity, regardless of whether the model then answers,
            # repairs its proposal, or requests another tool.
            post_tool_synthesis_pending = False
            response: Optional[AssembledModelResponse] = None
            provider_error: Optional[Exception] = None
            provider_attempts_used = 0
            for provider_attempt in range(self.provider_retries + 1):
                provider_attempts_used = provider_attempt + 1
                parser_event_start = len(trace.parser_events)
                try:
                    response = transport.complete(
                        messages=messages,
                        tools=adapter.render_tool_definitions(envelope.allowed_tools),
                        tool_choice=(
                            "required"
                            if envelope.tool_use_policy == ToolUsePolicy.REQUIRED
                            and not envelope.task.missing_inputs
                            and DecisionKind.CALL_TOOL in envelope.valid_next_actions
                            else "auto"
                        ),
                        adapter=adapter,
                        on_event=lambda event: trace.parser_events.append(_safe_parser_event(event)),
                        cancel=cancel,
                    )
                    provider_error = None
                    break
                except Exception as exc:
                    provider_error = exc
                    attempt_events = trace.parser_events[parser_event_start:]
                    partial_stream = any(
                        int(item.get("content_chars") or 0)
                        + int(item.get("reasoning_chars") or 0)
                        + int(item.get("argument_chars") or 0)
                        > 0
                        or bool(item.get("tool"))
                        for item in attempt_events
                    )
                    retryable, reason_code = _provider_error_retryability(
                        exc,
                        partial_stream=partial_stream,
                    )
                    will_retry = (
                        retryable and provider_attempt < self.provider_retries
                    )
                    self._emit(
                        diagnostic_sink,
                        {
                            "event": "provider_retry",
                            "attempt": provider_attempt + 1,
                            "retrying": will_retry,
                            "reason_code": reason_code,
                            "partial_stream": partial_stream,
                            "error_type": type(exc).__name__,
                            "provider": envelope.provider,
                            "model_id": envelope.model_id,
                            "stream_idle_seconds": (
                                exc.idle_seconds
                                if isinstance(exc, ModelStreamIdleTimeout)
                                else 0.0
                            ),
                            "stream_progress_chars": (
                                exc.progress_chars
                                if isinstance(exc, ModelStreamIdleTimeout)
                                else 0
                            ),
                        },
                    )
                    if not will_retry:
                        break
                    delay = self.provider_backoff_seconds * (2 ** provider_attempt)
                    deadline = time.monotonic() + delay
                    while time.monotonic() < deadline:
                        if cancel and cancel():
                            decision = AgentDecision(
                                kind=DecisionKind.CANCEL,
                                reason_code="cancelled_during_provider_retry",
                            )
                            return self._finish(
                                decision, trace, "cancelled", diagnostic_sink
                            )
                        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            if response is None:
                raise ModelProviderError(
                    f"selected provider {envelope.provider}/{envelope.model_id} "
                    f"failed after {provider_attempts_used} bounded attempt(s): "
                    f"{provider_error}"
                ) from provider_error
            decision = adapter.decision_from_response(response)
            finish_status = adapter.interpret_finish_reason(
                response.finish_reason, has_tool_calls=bool(response.tool_calls)
            )
            if finish_status == "cancelled":
                decision = AgentDecision(
                    kind=DecisionKind.CANCEL,
                    message="The model request was cancelled by the runtime.",
                    reason_code="model_request_cancelled",
                )
            elif finish_status in {"incomplete", "blocked"} and decision.kind == DecisionKind.ANSWER:
                decision = AgentDecision(
                    kind=DecisionKind.BLOCK,
                    message="The model response did not reach a complete terminal state.",
                    reason_code=f"model_finish_{finish_status}",
                )
            repairable_model_output = decision.reason_code in {
                "malformed_agent_decision",
                "malformed_tool_call",
                "empty_model_response",
                "model_finish_incomplete",
            }
            if (
                decision.kind == DecisionKind.BLOCK
                and repairable_model_output
                and malformed_repairs < self.malformed_repair_attempts
            ):
                malformed_repairs += 1
                self._emit(
                    diagnostic_sink,
                    {
                        "event": "model_output_repair",
                        "attempt": malformed_repairs,
                        "reason_code": decision.reason_code,
                        "loop": trace.loop_count,
                    },
                )
                messages.append({
                    "role": "system",
                    "content": (
                        "The previous response could not be decoded into one valid current action. "
                        "Do not repeat or explain it. Use native tool calling for a tool action; "
                        "otherwise emit one complete valid AgentDecision for a non-tool action, "
                        "or provide final answer text only when ANSWER is currently allowed."
                    ),
                })
                continue
            trace.decisions.append({
                "kind": decision.kind.value,
                "tool": decision.tool_call.name if decision.tool_call else "",
                "tools": [item.name for item in decision.tool_calls],
                "reason_code": decision.reason_code,
                "finish": finish_status,
            })
            signature_payload = {
                "kind": decision.kind.value,
                "reason": decision.reason_code,
                "tools": [
                    {"name": item.name, "arguments": item.arguments}
                    for item in decision.tool_calls
                ],
                "active_requirement": envelope.task.active_requirement_id,
            }
            decision_signature = hashlib.sha256(
                json.dumps(
                    signature_payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            decision_repeats[decision_signature] = (
                decision_repeats.get(decision_signature, 0) + 1
            )
            if decision_repeats[decision_signature] > self.no_progress_limit:
                blocked = AgentDecision(
                    kind=DecisionKind.BLOCK,
                    message=(
                        "I stopped after repeating the same action without new progress. "
                        "The work already recorded has been preserved."
                    ),
                    reason_code="model_loop_no_progress",
                )
                return self._finish(blocked, trace, "blocked", diagnostic_sink)
            try:
                decision = validate_agent_decision(envelope, decision)
            except DecisionValidationError as exc:
                logger.warning(
                    "Model decision rejected by authority boundary: kind={} error_type={}",
                    decision.kind.value,
                    type(exc).__name__,
                )
                # Case B: model proposed an allowed tool, but the envelope projection
                # was contradictory (false complete / finalize-only). Repair state and
                # revalidate the SAME proposal before spending another model loop.
                repaired_ok = False
                if decision.kind == DecisionKind.CALL_TOOL and decision.tool_calls:
                    proposed_names = [
                        str(item.name or "").strip() for item in decision.tool_calls
                    ]
                    allow_names = {str(item.name or "").strip() for item in envelope.allowed_tools}
                    if proposed_names and all(name in allow_names for name in proposed_names):
                        repaired = envelope_factory(outcomes)
                        self._emit(
                            diagnostic_sink,
                            {
                                "event": "projection_repair_and_revalidate",
                                "tools": proposed_names,
                                "prior_actions": [a.value for a in envelope.valid_next_actions],
                                "repaired_actions": [a.value for a in repaired.valid_next_actions],
                                "prior_finalizable": envelope.completion_evaluation.finalizable,
                                "repaired_finalizable": repaired.completion_evaluation.finalizable,
                                "loop": trace.loop_count,
                            },
                        )
                        if DecisionKind.CALL_TOOL in repaired.valid_next_actions:
                            try:
                                decision = validate_agent_decision(repaired, decision)
                                envelope = repaired
                                repaired_ok = True
                                logger.info(
                                    "Revalidated tool proposal after projection repair: tools={}",
                                    proposed_names,
                                )
                            except DecisionValidationError:
                                repaired_ok = False
                if repaired_ok:
                    # Fall through to execute the original tool call.
                    pass
                else:
                    # An early prose answer still fails the evidence boundary, but
                    # receives one bounded edge back into the same selected-model loop.
                    open_tool_work = DecisionKind.CALL_TOOL in envelope.valid_next_actions
                    prose_only_under_tools = (
                        decision.kind == DecisionKind.ANSWER
                        and envelope.tool_use_policy == ToolUsePolicy.REQUIRED
                        and open_tool_work
                        and not outcomes
                    )
                    # Valid allowed-tool rejection after failed repair is authority conflict,
                    # not "failed model output".
                    reason = (
                        "runtime_authority_conflict"
                        if decision.kind == DecisionKind.CALL_TOOL
                        else "runtime_decision_rejected"
                    )
                    feedback = RuntimeProposalFeedback(
                        (
                            "tool_required_before_answer"
                            if prose_only_under_tools
                            else reason
                        ),
                        safe_decision_rejection_message(envelope, decision=decision),
                        retryable=(
                            prose_only_under_tools
                            or reason != "runtime_authority_conflict"
                        ),
                        task_run_id=envelope.task.task_run_id,
                        requirement_id=envelope.task.active_requirement_id,
                        allowed_actions=[item.value for item in envelope.valid_next_actions],
                        allowed_tools=[item.name for item in envelope.allowed_tools],
                    )
                    if recover_from_feedback(feedback):
                        continue
                    blocked = AgentDecision(
                        kind=DecisionKind.BLOCK,
                        message=safe_decision_rejection_message(envelope, decision=decision),
                        reason_code=feedback.reason_code,
                    )
                    return self._finish(blocked, trace, "blocked", diagnostic_sink)
            if decision.kind == DecisionKind.ANSWER and validate_answer is not None:
                answer_feedback = validate_answer(envelope, decision)
                if answer_feedback is not None:
                    if recover_from_feedback(answer_feedback):
                        continue
                    grounded_fallback = synthesize_structured_evidence_answer(
                        envelope
                    )
                    if grounded_fallback and envelope.completion_evaluation.finalizable:
                        repaired = AgentDecision(
                            kind=DecisionKind.ANSWER,
                            message=grounded_fallback,
                            reason_code="runtime_grounded_answer_fallback",
                            verified_outcome_ids=[
                                item.run_id
                                for item in envelope.verified_tool_outcomes
                            ],
                        )
                        return self._finish(
                            repaired,
                            trace,
                            "answer",
                            diagnostic_sink,
                        )
                    blocked = AgentDecision(
                        kind=DecisionKind.BLOCK,
                        message=answer_feedback.safe_message,
                        reason_code=answer_feedback.reason_code,
                    )
                    return self._finish(
                        blocked, trace, "blocked", diagnostic_sink
                    )
            if decision.kind == DecisionKind.UPDATE_PLAN and apply_plan is not None:
                apply_plan([dict(item) for item in decision.plan])
                messages.append({
                    "role": "assistant",
                    "content": decision.message or "I proposed a structured plan.",
                })
                messages.append({
                    "role": "system",
                    "content": (
                        "The runtime accepted and persisted the structured plan. "
                        "Continue with the next valid action under the refreshed envelope."
                    ),
                })
                continue
            if decision.kind != DecisionKind.CALL_TOOL:
                return self._finish(decision, trace, decision.kind.value, diagnostic_sink)
            calls = list(decision.tool_calls)
            if not calls and decision.tool_call is not None:
                calls = [decision.tool_call]
            remaining_calls = self.max_tool_calls - tool_call_count
            if remaining_calls <= 0:
                messages.append({
                    "role": "system",
                    "content": (
                        "The runtime tool-call budget is exhausted. Refresh the envelope and "
                        "answer only if the completion verdict permits a complete or honest partial result."
                    ),
                })
                continue
            executable_calls = calls[:remaining_calls]
            if len(executable_calls) < len(calls):
                messages.append({
                    "role": "system",
                    "content": (
                        "The runtime accepted only the calls that fit the current bounded "
                        "tool budget. Re-evaluate remaining requirements after their outcomes."
                    ),
                })
            recover_batch = False
            for batch_index, call in enumerate(executable_calls):
                tool_call_count += 1
                try:
                    outcome = execute_tool(call.name, call.arguments)
                except ResearchBudgetExceeded:
                    messages.append({
                        "role": "system",
                        "content": (
                            "The runtime exhausted the bounded acquisition budget before this "
                            "tool could run. Refresh the envelope and answer only if its completion "
                            "verdict permits an honest partial result."
                        ),
                    })
                    recover_batch = True
                    break
                except RuntimeProposalFeedback as feedback:
                    if recover_from_feedback(feedback):
                        recover_batch = True
                        break
                    blocked = AgentDecision(
                        kind=DecisionKind.BLOCK,
                        message=feedback.safe_message,
                        reason_code=feedback.reason_code,
                    )
                    return self._finish(blocked, trace, "blocked", diagnostic_sink)
                except Exception as exc:
                    self._emit(
                        diagnostic_sink,
                        {
                            "event": "tool_execution_error",
                            "tool": call.name,
                            "error_type": type(exc).__name__,
                            "loop": trace.loop_count,
                        },
                    )
                    feedback = RuntimeProposalFeedback(
                        "tool_execution_error",
                        (
                            "The proposed tool action did not return a durable outcome. "
                            "Choose another valid action or a different allowed tool."
                        ),
                        retryable=True,
                        task_run_id=envelope.task.task_run_id,
                        requirement_id=envelope.task.active_requirement_id,
                        allowed_actions=[item.value for item in envelope.valid_next_actions],
                        allowed_tools=[item.name for item in envelope.allowed_tools],
                    )
                    if recover_from_feedback(feedback):
                        recover_batch = True
                        break
                    blocked = AgentDecision(
                        kind=DecisionKind.BLOCK,
                        message=feedback.safe_message,
                        reason_code=feedback.reason_code,
                    )
                    return self._finish(blocked, trace, "blocked", diagnostic_sink)
                if outcome.execution_id != envelope.identity.execution_id:
                    raise RuntimeError(
                        "Tool executor returned an outcome for a different Execution"
                    )
                if not outcome.verification:
                    raise RuntimeError(
                        "Tool executor returned a ToolOutcome without runtime verification metadata"
                    )
                outcomes.append(outcome)
                post_tool_synthesis_pending = True
                trace.tool_runs.append(outcome.run_id)
                messages.append({
                    "role": "assistant",
                    "content": response.content if batch_index == 0 else "",
                    "tool_calls": [{
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, sort_keys=True),
                        },
                    }],
                })
                projected = outcome
                if len(str(outcome.output or "")) > 12000:
                    projected = outcome.model_copy(update={
                        "output": (
                            str(outcome.output or "")[:12000]
                            + "\n[bounded evidence projection]"
                        )
                    })
                messages.append(adapter.format_tool_outcome(projected, call.id))
            if recover_batch:
                continue
        blocked = AgentDecision(
            kind=DecisionKind.BLOCK,
            message="The model exceeded the bounded sequential tool-loop limit.",
            reason_code="tool_loop_limit",
        )
        return self._finish(blocked, trace, "blocked", diagnostic_sink)

    @staticmethod
    def _emit(sink: Optional[Callable[[dict[str, Any]], None]], event: dict[str, Any]) -> None:
        if sink:
            sink(event)
        logger.info("Model control plane: {}", json.dumps(event, sort_keys=True, default=str))

    def _finish(
        self, decision: AgentDecision, trace: ControlPlaneTrace, status: str,
        sink: Optional[Callable[[dict[str, Any]], None]],
    ) -> tuple[AgentDecision, ControlPlaneTrace]:
        trace.terminal_status = status
        trace.completed_at = time.time()
        self._emit(sink, {"event": "model_loop_finished", **trace.safe_dict()})
        return decision, trace


def merge_contract_into_system_messages(
    messages: list[Any], *, envelope: ModelTurnEnvelope, adapter: ModelFamilyAdapter
) -> list[Any]:
    """Inject or refresh the envelope without duplicating it across tool loops."""
    contract = adapter.render_system_contract(envelope)
    result = list(messages)
    for index, message in enumerate(result):
        role = str(getattr(message, "type", "") or getattr(message, "role", "") or "").lower()
        content = str(getattr(message, "content", "") or "")
        if role in {"system", "systemmessage"}:
            clean = _remove_existing_contract(content)
            try:
                result[index] = message.__class__(content=f"{contract}\n\n{clean}".strip())
            except Exception:
                result[index] = {"role": "system", "content": f"{contract}\n\n{clean}".strip()}
            return result
    try:
        from langchain_core.messages import SystemMessage

        return [SystemMessage(content=contract), *result]
    except Exception:
        return [{"role": "system", "content": contract}, *result]


def _remove_existing_contract(content: str) -> str:
    return re_sub_contract(content).strip()


def re_sub_contract(content: str) -> str:
    start = content.find(ENVELOPE_MARKER)
    if start < 0:
        return content
    close = "[/ECHOSPEAK_MODEL_TURN_ENVELOPE]"
    end = content.find(close, start)
    if end < 0:
        return content[:start]
    return content[:start] + content[end + len(close):]


def _safe_parser_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": str(event.get("type") or "parser"),
        "index": int(event.get("index") or 0),
        "tool": str(event.get("tool") or ""),
        "argument_chars": int(event.get("argument_chars") or 0),
        "content_chars": int(event.get("content_chars") or 0),
        "reasoning_chars": int(event.get("reasoning_chars") or 0),
        "finish_reason": str(event.get("finish_reason") or ""),
        "sha256": hashlib.sha256(json.dumps(event, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
    }


def _to_langchain_messages(messages: list[dict[str, Any]]) -> list[Any]:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    result: list[Any] = []
    for item in messages:
        role = str(item.get("role") or "user").lower()
        content = item.get("content") or ""
        if role == "system":
            result.append(SystemMessage(content=content))
        elif role == "assistant":
            calls = []
            for call in item.get("tool_calls") or []:
                function = call.get("function") or {}
                arguments = function.get("arguments") or "{}"
                try:
                    arguments = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
                except (json.JSONDecodeError, TypeError, ValueError):
                    arguments = {}
                calls.append({
                    "id": str(call.get("id") or ""),
                    "name": str(function.get("name") or ""),
                    "args": arguments,
                    "type": "tool_call",
                })
            result.append(AIMessage(content=content, tool_calls=calls))
        elif role == "tool":
            result.append(ToolMessage(
                content=content,
                tool_call_id=str(item.get("tool_call_id") or ""),
                name=str(item.get("name") or ""),
            ))
        else:
            result.append(HumanMessage(content=content))
    return result


def _langchain_chunk_event(chunk: Any) -> dict[str, Any]:
    content = getattr(chunk, "content", "") or ""
    if isinstance(content, list):
        content = "".join(
            str(item.get("text") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    additional = dict(getattr(chunk, "additional_kwargs", None) or {})
    metadata = dict(getattr(chunk, "response_metadata", None) or {})
    fragments = []
    for index, item in enumerate(getattr(chunk, "tool_call_chunks", None) or []):
        fragments.append({
            "index": int(item.get("index", index) or 0),
            "id": str(item.get("id") or ""),
            "function": {
                "name": str(item.get("name") or ""),
                "arguments": str(item.get("args") or ""),
            },
        })
    delta: dict[str, Any] = {"content": str(content)}
    reasoning = additional.get("reasoning_content") or additional.get("reasoning")
    if reasoning:
        delta["reasoning_content"] = str(reasoning)
    if fragments:
        delta["tool_calls"] = fragments
    function_call = additional.get("function_call")
    if isinstance(function_call, dict):
        function_arguments = function_call.get("arguments") or ""
        if isinstance(function_arguments, dict):
            function_arguments = json.dumps(function_arguments, ensure_ascii=False, sort_keys=True)
        delta["function_call"] = {
            "id": str(function_call.get("id") or ""),
            "name": str(function_call.get("name") or ""),
            "arguments": str(function_arguments),
        }
    return {
        "choices": [{
            "delta": delta,
            "finish_reason": str(metadata.get("finish_reason") or metadata.get("stop_reason") or ""),
        }]
    }


__all__ = [
    "CONTROL_PLANE_VERSION",
    "ControlPlaneTrace",
    "ENVELOPE_MARKER",
    "LangChainStreamingTransport",
    "ModelExecutionControlPlane",
    "ModelProviderError",
    "ModelStreamIdleTimeout",
    "ModelTransport",
    "ModelTurnEnvelopeCompiler",
    "merge_contract_into_system_messages",
]
