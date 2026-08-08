"""Canonical model-led Turn coordinator.

Production ``process_query`` delegates here.  The selected model owns semantic
interpretation; EchoSpeak applies the interpretation, derives policy, executes
tools, verifies outcomes, and persists lifecycle truth.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from config import config
from agent.mode_controller import CodingPhaseName, ModeDecision, TurnMode
from agent.model_contracts import is_usable_verified_outcome
from agent.model_control_plane import ModelProviderError
from agent.state import ToolOutcome
from agent.model_runtime import (
    SelectedModelReadinessError,
    clear_structured_output_probe_cache,
    ensure_selected_model_ready,
    extract_json_value_once,
    resolve_model_profile,
    resolve_structured_output_capability,
)
from agent.execution_graph import ExecutionProfile
from agent.research_runtime import (
    RequirementCompletionEvaluator,
    RequirementKind,
    TurnRequirement,
    canonicalize_proposed_requirements,
    initial_requirement_states,
    merge_turn_requirements,
    reconcile_requirement_states,
    reopen_incomplete_requirements,
    replace_turn_requirements,
)
from agent.task_runs import (
    RECOVERABLE_TASK_STATUSES,
    RequirementHistoryEntry,
    TERMINAL_TASK_STATUSES,
    TaskInputGap,
    TaskInputOwner,
    TaskRun,
    TaskRunContinuation,
    TaskRunContinuationStatus,
    TaskRunStatus,
    TaskRunStore,
    classify_task_input_gaps,
    get_task_run_store,
)
from agent.tool_registry import ToolRegistry
from agent.turn_understanding import (
    ApprovalDecision,
    CAPABILITY_CATEGORIES,
    TurnInterpretation,
    TurnInterpreter,
    TurnCancelledError,
    TurnRelation,
    TurnUnderstandingCompiler,
    TurnUnderstandingError,
    TurnUnderstandingProviderError,
    blocking_missing_fields,
    message_looks_like_safe_read_only_information_request,
    minimal_safe_read_only_fallback_interpretation,
    scope_interpretation_to_current_instruction,
)


def parse_deterministic_continuation_command(text: str) -> tuple[bool, str]:
    """Return whether text begins with a bounded resume command and its modifier."""

    normalized = re.sub(
        r"[\s.!?,;:]+", " ", str(text or "").casefold()
    ).strip()
    match = re.match(
        r"^(?:continue(?:\s+(?:it|that))?|keep\s+(?:going|working)|go\s+on|"
        r"resume(?:\s+it)?|retry(?:\s+it)?|try\s+again|finish(?:\s+(?:it|that))?)"
        r"(?:\s+(.*))?$",
        normalized,
        flags=re.IGNORECASE,
    )
    if match is None:
        return False, ""
    return True, str(match.group(1) or "").strip()


class SessionTurnCoordinator:
    """One ordered foreground mutation slot per Session; unrelated Sessions proceed."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    def lock_for(self, session_id: str) -> threading.RLock:
        key = str(session_id or "default").strip() or "default"
        with self._guard:
            return self._locks.setdefault(key, threading.RLock())


_SESSION_TURNS = SessionTurnCoordinator()


@dataclass
class _SafeProviderChannelMarker:
    """Preserve channel presence without retaining private reasoning text."""

    content: str = ""
    additional_kwargs: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.additional_kwargs is None:
            self.additional_kwargs = {}


class _TurnUnderstandingLengthError(RuntimeError):
    """The selected model ended a structured response at its output limit."""


CAPABILITY_TOOLS: dict[str, set[str]] = {
    "conversation": set(),
    "research": {"web_search", "safe_web_fetch", "browse_task", "youtube_transcript"},
    # Structured tools are preferred by the model-facing descriptions. A
    # governed web lookup remains available when a provider returns a typed
    # unavailable/unsupported outcome; it is never an unverified shortcut.
    "live_weather": {"weather_live", "web_search", "safe_web_fetch"},
    "live_sports": {"sports_live", "web_search", "safe_web_fetch"},
    "time": {"get_system_time"},
    "calculate": {"calculate"},
    "coding_read": {"file_list", "file_read", "project_status", "project_update_context", "system_info"},
    "coding_write": {
        "file_list", "file_read", "project_status", "file_write", "file_mkdir",
        "file_move", "file_copy", "file_delete", "artifact_write", "checkpoint_undo",
        "code_preview_start", "code_preview_stop",
    },
    "terminal": {"terminal_run", "file_list", "file_read", "project_status", "system_info"},
    "desktop": {
        "desktop_list_windows", "desktop_find_control", "desktop_click", "desktop_type_text",
        "desktop_activate_window", "desktop_send_hotkey", "analyze_screen", "vision_qa",
        "take_screenshot", "open_application", "open_chrome",
    },
    "communications": {
        "email_read_inbox", "email_search", "email_get_thread", "email_send", "email_reply",
        "discord_read_channel", "discord_send_channel", "discord_web_read_recent", "discord_web_send",
        "telegram_send", "whatsapp_send", "calendar_list", "calendar_create",
    },
    "task_management": {"todo_manage"},
    "voice": {"voice_synthesize_speech", "voice_list_capabilities"},
    "media_generation": {"generation_submit", "generation_list_capabilities"},
    "memory": set(),
}


@dataclass
class TaskBinding:
    task: Optional[TaskRun]
    terminal_response: str = ""
    terminal_success: bool = True


class TaskArbitrator:
    def __init__(self, store: TaskRunStore) -> None:
        self.store = store

    def apply(
        self,
        interpretation: TurnInterpretation,
        *,
        session_id: str,
        project_id: str,
        execution_id: str,
        source: str,
        eligible_task_revisions: Optional[dict[str, int]] = None,
    ) -> TaskBinding:
        relation = interpretation.relation
        if relation in {TurnRelation.CASUAL_CONVERSATION, TurnRelation.AMBIGUOUS, TurnRelation.BLOCKED, TurnRelation.RESUME_APPROVAL}:
            return TaskBinding(task=None)

        if relation == TurnRelation.NEW_TASK:
            capabilities = list(interpretation.requested_capabilities or [])
            requirements = canonicalize_proposed_requirements(
                interpretation.requirements,
                objective=str(interpretation.proposed_objective or ""),
                capabilities=capabilities,
                requested_operation=str(interpretation.requested_operation or ""),
                missing_fields=list(interpretation.missing_fields or []),
            )
            input_gaps = classify_task_input_gaps(
                list(interpretation.missing_fields or []),
                capabilities,
                objective=str(interpretation.proposed_objective or ""),
                requirements=requirements,
            )
            task = self.store.create(
                project_id=project_id,
                session_id=session_id,
                objective=str(interpretation.proposed_objective or ""),
                requested_operation=str(interpretation.requested_operation or ""),
                collected_inputs=dict(interpretation.extracted_fields or {}),
                missing_inputs=[
                    item.field for item in input_gaps
                    if item.owner == TaskInputOwner.USER and item.blocking
                ],
                input_gaps=input_gaps,
                permitted_capabilities=capabilities,
                completion_requirements=self._completion_requirements(interpretation),
                requirements=requirements,
                workflow_stage="clarification" if interpretation.clarification_required else "ready",
                status=TaskRunStatus.RUNNING,
                created_by_execution_id=execution_id,
                last_execution_id=execution_id,
                source=source,
            )
            return TaskBinding(task=task)

        task = self.store.get(
            interpretation.selected_task_id or "",
            session_id=session_id,
            project_id=project_id,
        )
        if task is None:
            raise TurnUnderstandingError("selected TaskRun no longer exists")
        eligible_task_revisions = dict(eligible_task_revisions or {})
        expected_candidate_revision = eligible_task_revisions.get(task.id)
        if expected_candidate_revision is None:
            raise TurnUnderstandingError("selected TaskRun was not an eligible continuation candidate")
        if (
            task.status in TERMINAL_TASK_STATUSES
            and task.status not in RECOVERABLE_TASK_STATUSES
        ) or task.status == TaskRunStatus.LEGACY_UNTRUSTED:
            raise TurnUnderstandingError("selected TaskRun is terminal or quarantined")
        if int(task.revision) != int(expected_candidate_revision):
            raise TurnUnderstandingError("selected TaskRun changed after Turn Understanding was compiled")
        self._supersede_resolved_arbitrations(task, execution_id=execution_id)

        if relation == TurnRelation.CANCEL_TASK:
            task = self.store.update(
                task.id,
                session_id=session_id,
                project_id=project_id,
                expected_revision=task.revision,
                status=TaskRunStatus.CANCELLED,
                workflow_stage="cancelled",
                last_execution_id=execution_id,
            )
            return TaskBinding(task=task, terminal_response=f"Canceled: {task.objective}.")

        if relation == TurnRelation.SWITCH_TASK:
            replacement_capabilities = list(interpretation.requested_capabilities or [])
            replacement_requirements = canonicalize_proposed_requirements(
                interpretation.requirements,
                objective=str(interpretation.proposed_objective or ""),
                capabilities=replacement_capabilities,
                requested_operation=str(interpretation.requested_operation or ""),
                missing_fields=list(interpretation.missing_fields or []),
            )
            replacement_gaps = classify_task_input_gaps(
                list(interpretation.missing_fields or []),
                replacement_capabilities,
                objective=str(interpretation.proposed_objective or ""),
                requirements=replacement_requirements,
            )
            _previous, replacement = self.store.supersede_and_create(
                task.id,
                session_id=session_id,
                project_id=project_id,
                expected_revision=task.revision,
                execution_id=execution_id,
                replacement={
                    "objective": str(interpretation.proposed_objective or ""),
                    "requested_operation": str(interpretation.requested_operation or ""),
                    "collected_inputs": dict(interpretation.extracted_fields or {}),
                    "missing_inputs": [
                        item.field for item in replacement_gaps
                        if item.owner == TaskInputOwner.USER and item.blocking
                    ],
                    "input_gaps": replacement_gaps,
                    "permitted_capabilities": replacement_capabilities,
                    "completion_requirements": self._completion_requirements(interpretation),
                    "requirements": replacement_requirements,
                    "workflow_stage": "ready",
                    "status": TaskRunStatus.RUNNING,
                    "source": source,
                    "legacy_provenance": {"supersedes_task_id": task.id},
                },
            )
            return TaskBinding(task=replacement)

        collected = (
            {}
            if relation == TurnRelation.CORRECT_TASK
            else dict(task.collected_inputs or {})
        )
        collected.update(dict(interpretation.extracted_fields or {}))
        effective_capabilities = (
            list(interpretation.requested_capabilities)
            if interpretation.requested_capabilities
            else list(task.permitted_capabilities)
        )
        raw_gap_candidates = list(interpretation.missing_fields or [])
        if relation != TurnRelation.CORRECT_TASK:
            raw_gap_candidates = [
                *(gap.field for gap in task.input_gaps),
                *raw_gap_candidates,
            ]
        raw_missing = [
            item for item in raw_gap_candidates
            if item not in collected
        ]
        objective = str(interpretation.proposed_objective or task.objective).strip()
        proposed_requirements = list(interpretation.requirements or [])
        input_gaps = classify_task_input_gaps(
            raw_missing,
            effective_capabilities,
            objective=objective,
            requirements=proposed_requirements or task.requirements,
        )
        missing = [
            item.field for item in input_gaps
            if item.owner == TaskInputOwner.USER and item.blocking
        ]
        requirement_states = dict(task.requirement_states)
        requirement_history = list(task.requirement_history)
        if relation == TurnRelation.CORRECT_TASK:
            requirements, requirement_states, archived, archived_states = replace_turn_requirements(
                task.requirements,
                task.requirement_states,
                proposed_requirements,
                objective=objective,
                capabilities=effective_capabilities,
                requested_operation=str(interpretation.requested_operation or task.requested_operation),
                missing_fields=missing,
            )
            if archived:
                requirement_history.append(RequirementHistoryEntry(
                    reason="correct_task_replaced_active_requirement_set",
                    task_revision=task.revision,
                    requirements=archived,
                    requirement_states=archived_states,
                ))
        else:
            requirements = merge_turn_requirements(
                task.requirements,
                proposed_requirements,
                objective=objective,
                capabilities=effective_capabilities,
                requested_operation=str(interpretation.requested_operation or task.requested_operation),
                missing_fields=missing,
            )
        requirement_states = reconcile_requirement_states(
            requirements,
            requirement_states,
        )
        recovery_epoch = int(task.recovery_epoch or 0)
        recovery_epoch_started_at = float(task.recovery_epoch_started_at or 0.0)
        recovery_history = list(task.recovery_history or [])
        if relation == TurnRelation.CONTINUE_TASK:
            next_epoch = recovery_epoch + 1
            requirement_states, reopened_ids = reopen_incomplete_requirements(
                requirements,
                requirement_states,
                recovery_epoch=next_epoch,
                include_open=task.status in RECOVERABLE_TASK_STATUSES,
            )
            if reopened_ids:
                recovery_history.append({
                    "epoch": next_epoch,
                    "trigger": "explicit_continue",
                    "execution_id": execution_id,
                    "reopened_requirement_ids": reopened_ids,
                    "preserved_satisfied_requirement_ids": [
                        requirement.requirement_id
                        for requirement in requirements
                        if requirement_states[requirement.requirement_id].status.value == "satisfied"
                    ],
                    "started_at": time.time(),
                })
                recovery_epoch = next_epoch
                recovery_epoch_started_at = time.time()
        status = TaskRunStatus.RUNNING
        task = self.store.update(
            task.id,
            session_id=session_id,
            project_id=project_id,
            expected_revision=task.revision,
            objective=objective,
            requested_operation=str(interpretation.requested_operation or task.requested_operation),
            collected_inputs=collected,
            missing_inputs=missing,
            input_gaps=input_gaps,
            permitted_capabilities=(
                effective_capabilities
            ),
            completion_requirements=(
                self._completion_requirements(interpretation)
                or list(task.completion_requirements)
            ),
            requirements=requirements,
            requirement_states=requirement_states,
            requirement_history=requirement_history,
            recovery_epoch=recovery_epoch,
            recovery_epoch_started_at=recovery_epoch_started_at,
            recovery_history=recovery_history[-64:],
            clear_fields=("liveness_decision", "completion_evaluation"),
            workflow_stage="ready",
            status=status,
            last_execution_id=execution_id,
        )
        return TaskBinding(task=task)

    def _supersede_resolved_arbitrations(
        self, selected: TaskRun, *, execution_id: str
    ) -> None:
        for candidate in self.store.list_for_session(
            selected.session_id,
            project_id=selected.project_id,
            include_terminal=False,
        ):
            if candidate.id == selected.id:
                continue
            if candidate.requested_operation != "resolve_task_relationship":
                continue
            ids = list((candidate.collected_inputs or {}).get("candidate_task_ids") or [])
            if selected.id not in {str(item) for item in ids}:
                continue
            self.store.update(
                candidate.id,
                session_id=candidate.session_id,
                project_id=candidate.project_id,
                expected_revision=candidate.revision,
                status=TaskRunStatus.SUPERSEDED,
                workflow_stage="ambiguity_resolved",
                last_execution_id=execution_id,
            )

    @staticmethod
    def _completion_requirements(interpretation: TurnInterpretation) -> list[str]:
        capabilities = set(interpretation.requested_capabilities or [])
        requirements: list[str] = []
        if capabilities - {"conversation", "memory"}:
            requirements.append(
                "Each required capability requirement must have sufficient runtime-verified evidence "
                "or an allowed terminal recovery state."
            )
        if interpretation.missing_fields:
            requirements.append("All declared missing inputs must be collected before completion.")
        return requirements


class TurnModeDeriver:
    """Post-understanding policy derivation; never reads raw user text semantically."""

    @staticmethod
    def derive(
        interpretation: TurnInterpretation,
        task: Optional[TaskRun],
        *,
        provider: str,
        model_id: str,
        available_tools: set[str],
    ) -> ModeDecision:
        capabilities = set(
            interpretation.requested_capabilities
            or (task.permitted_capabilities if task is not None else [])
            or ["conversation"]
        )
        if capabilities & {"coding_read", "coding_write", "terminal"}:
            mode = TurnMode.CODING
            operation = str(interpretation.requested_operation or "").lower()
            if "verify" in operation or "test" in operation:
                phase = CodingPhaseName.VERIFY
            elif "coding_write" in capabilities or "terminal" in capabilities:
                phase = CodingPhaseName.IMPLEMENT
            else:
                phase = CodingPhaseName.INSPECT
        elif capabilities & {"research", "live_weather", "live_sports"}:
            mode = TurnMode.TASK_RESEARCH
            phase = None
        else:
            mode = TurnMode.CHAT
            phase = None

        requested_tools = TurnModeDeriver._tools_for_interpretation(
            interpretation,
            capabilities,
            fallback_operation=str(getattr(task, "requested_operation", "") or ""),
        )
        allowed = frozenset(requested_tools & available_tools)
        objective = str(
            (task.objective if task is not None else "")
            or interpretation.proposed_objective
        ).strip()
        if capabilities <= {"time", "calculate", "conversation"} and capabilities & {"time", "calculate"}:
            reason = "utility tool request (selected-model Turn Understanding)"
        else:
            reason = "turn_understanding:" + ",".join(sorted(capabilities))
        tool_capabilities = capabilities - {"conversation", "memory"}
        relation_map = {
            TurnRelation.NEW_TASK: "new_objective",
            TurnRelation.CONTINUE_TASK: "continue",
            TurnRelation.PROVIDE_TASK_INPUT: "continue",
            TurnRelation.CORRECT_TASK: "continue",
            TurnRelation.SWITCH_TASK: "continue",
            TurnRelation.CANCEL_TASK: "cancel",
            TurnRelation.RESUME_APPROVAL: "confirm",
        }
        return ModeDecision(
            mode=mode,
            confidence=float(interpretation.confidence),
            reason=reason,
            user_text=objective,
            coding_phase=phase,
            model_provider=provider,
            model_name=model_id,
            allowed_tool_names=allowed,
            verification_required=bool(tool_capabilities),
            evidence_required=bool(tool_capabilities),
            ambiguous=bool(interpretation.clarification_required),
            objective=objective,
            required_capabilities=frozenset(capabilities),
            constraints=frozenset(interpretation.constraints or []),
            intent_relation=relation_map.get(interpretation.relation, "other"),
        )

    @staticmethod
    def _tools_for_interpretation(
        interpretation: TurnInterpretation,
        capabilities: set[str],
        fallback_operation: str = "",
    ) -> set[str]:
        requested: set[str] = set()
        for capability in capabilities:
            requested.update(CAPABILITY_TOOLS.get(capability, set()))
        operation = re.sub(
            r"[^a-z0-9_]+",
            " ",
            str(interpretation.requested_operation or fallback_operation or "").lower(),
        )
        exact = {
            name for name in requested
            if re.search(rf"(?<![a-z0-9_]){re.escape(name)}(?![a-z0-9_])", operation)
        }

        if "coding_write" in capabilities:
            support = {"file_list", "file_read", "project_status", "project_update_context"}
            if exact:
                coding = exact | support
            elif re.search(r"\b(delete|remove|unlink)\b", operation):
                coding = support | {"file_delete"}
            elif re.search(r"\b(move|rename)\b", operation):
                coding = support | {"file_move"}
            elif re.search(r"\bcopy\b", operation):
                coding = support | {"file_copy"}
            elif re.search(r"\b(directory|folder|mkdir)\b", operation):
                coding = support | {"file_mkdir"}
            elif re.search(r"\b(undo|restore|rollback|checkpoint)\b", operation):
                coding = support | {"checkpoint_undo"}
            elif re.search(r"\b(preview|dev server|development server)\b", operation):
                coding = support | (
                    {"code_preview_stop"}
                    if re.search(r"\b(stop|close|end|terminate)\b", operation)
                    else {"code_preview_start"}
                )
            elif re.search(r"\bartifact\b", operation):
                coding = support | {"artifact_write"}
            else:
                # Edit/create/patch defaults to the least destructive mutator.
                coding = support | {"file_write"}
            requested -= CAPABILITY_TOOLS["coding_write"]
            requested.update(coding)

        if "communications" in capabilities:
            family = CAPABILITY_TOOLS["communications"]
            if exact & family:
                communication = exact & family
            elif "whatsapp" in operation:
                communication = {"whatsapp_send"}
            elif "telegram" in operation:
                communication = {"telegram_send"}
            elif "discord" in operation:
                communication = (
                    {"discord_send_channel", "discord_web_send"}
                    if re.search(r"\b(send|post|write)\b", operation)
                    else {"discord_read_channel", "discord_web_read_recent"}
                )
            elif "calendar" in operation:
                communication = (
                    {"calendar_create"}
                    if re.search(r"\b(create|add|schedule|book)\b", operation)
                    else {"calendar_list"}
                )
            elif re.search(r"\b(reply|respond)\b", operation):
                communication = {"email_reply", "email_get_thread"}
            elif re.search(r"\b(send|compose|write)\b", operation):
                communication = {"email_send"}
            else:
                communication = {"email_read_inbox", "email_search", "email_get_thread"}
            requested -= family
            requested.update(communication)

        if "desktop" in capabilities:
            family = CAPABILITY_TOOLS["desktop"]
            if exact & family:
                desktop = exact & family
            elif "chrome" in operation:
                desktop = {"open_chrome"}
            elif re.search(r"\b(open|launch|start)\b", operation):
                desktop = {"open_application"}
            elif re.search(r"\b(screenshot|screen|vision|analy[sz]e)\b", operation):
                desktop = {"take_screenshot", "analyze_screen", "vision_qa"}
            else:
                desktop = {"desktop_list_windows", "desktop_find_control"}
            requested -= family
            requested.update(desktop)
        return requested


class CanonicalSemanticRuntime:
    def __init__(self) -> None:
        self.task_store = get_task_run_store()
        self.compiler = TurnUnderstandingCompiler()
        self.interpreter = TurnInterpreter()
        self.arbitrator = TaskArbitrator(self.task_store)

    def run(
        self,
        agent: Any,
        *,
        user_input: str,
        include_memory: bool,
        callbacks: Optional[list],
        thread_id: Optional[str],
        source: Optional[str],
        discord_user_info: Optional[dict[str, Any]],
        requested_approval_id: Optional[str] = None,
        cancel_event: Optional[threading.Event] = None,
        request_id: str = "",
        thinking_enabled: bool = True,
        reasoning_effort: str = "medium",
    ) -> tuple[str, bool]:
        session_id = str(thread_id or "default").strip() or "default"
        lock = _SESSION_TURNS.lock_for(session_id)
        with lock:
            return self._run_locked(
                agent,
                user_input=str(user_input or "").strip(),
                include_memory=include_memory,
                callbacks=list(callbacks or []),
                session_id=session_id,
                source=str(source or "web").strip() or "web",
                discord_user_info=discord_user_info,
                requested_approval_id=str(requested_approval_id or "").strip(),
                cancel_event=cancel_event or threading.Event(),
                request_id=str(request_id or "").strip(),
                thinking_enabled=bool(thinking_enabled),
                reasoning_effort=str(reasoning_effort or "medium"),
            )

    def schedule_specialist_continuation(
        self,
        agent: Any,
        specialist_run: Any,
    ) -> None:
        """Schedule one canonical Echo Turn for a projected specialist outcome."""

        task = self.task_store.get(
            str(specialist_run.task_run_id),
            session_id=str(specialist_run.session_id),
            project_id=str(specialist_run.project_id),
        )
        continuation = getattr(task, "continuation", None) if task is not None else None
        outcome = getattr(specialist_run, "outcome", None)
        if (
            task is None
            or outcome is None
            or continuation is None
            or continuation.trigger_id != outcome.outcome_id
            or continuation.status != TaskRunContinuationStatus.PENDING
        ):
            return

        def _continue() -> None:
            self.continue_task_after_specialist(
                agent,
                task_run_id=task.id,
                trigger_id=continuation.trigger_id,
                session_id=task.session_id,
                project_id=task.project_id,
            )

        threading.Thread(
            target=_continue,
            daemon=True,
            name=f"echo-specialist-continuation-{task.id[-8:]}",
        ).start()

    def continue_task_after_specialist(
        self,
        agent: Any,
        *,
        task_run_id: str,
        trigger_id: str,
        session_id: str,
        project_id: str,
    ) -> tuple[str, bool]:
        """Enter the ordinary Echo control plane without another user Turn."""

        task = self.task_store.get(
            task_run_id,
            session_id=session_id,
            project_id=project_id,
        )
        if task is None:
            return "", False
        lock = _SESSION_TURNS.lock_for(task.session_id)
        with lock:
            current = self.task_store.get(
                task.id,
                session_id=task.session_id,
                project_id=task.project_id,
            )
            continuation = current.continuation if current is not None else None
            if (
                current is None
                or continuation is None
                or continuation.trigger_id != str(trigger_id)
                or continuation.status != TaskRunContinuationStatus.PENDING
            ):
                return "", False
            return self._run_locked(
                agent,
                user_input=(
                    "Continue the owning objective after the delegated specialist "
                    "outcome. Use the TaskRun requirement evidence, continue any "
                    "remaining work, or synthesize the final response."
                ),
                include_memory=True,
                callbacks=[],
                session_id=current.session_id,
                source="specialist_continuation",
                discord_user_info=None,
                requested_approval_id="",
                cancel_event=threading.Event(),
                request_id=f"specialist-continuation:{trigger_id}",
                internal_task_id=current.id,
                internal_trigger_id=str(trigger_id),
            )

    def _run_locked(
        self,
        agent: Any,
        *,
        user_input: str,
        include_memory: bool,
        callbacks: list,
        session_id: str,
        source: str,
        discord_user_info: Optional[dict[str, Any]],
        requested_approval_id: str,
        cancel_event: threading.Event,
        request_id: str,
        thinking_enabled: bool = True,
        reasoning_effort: str = "medium",
        internal_task_id: str = "",
        internal_trigger_id: str = "",
    ) -> tuple[str, bool]:
        started = time.time()
        request_id = str(request_id or "").strip() or str(uuid.uuid4())
        internal_continuation = bool(internal_task_id and internal_trigger_id)
        execution = None
        understanding = None
        trace: Optional[dict[str, Any]] = None
        task: Optional[TaskRun] = None
        agent._canonical_semantic_flow = True
        agent._turn_cancel_event = cancel_event
        agent._requested_approval_id = requested_approval_id or None
        agent._current_request_id = request_id
        agent._current_thread_id = session_id
        agent._current_source = source
        agent._turn_understanding_output_mode = None
        agent._current_callbacks = callbacks
        agent._current_mode_decision = None
        agent._active_task_run = None
        agent._active_turn_interpretation = None
        agent._raw_turn_user_message = user_input
        agent._model_latest_user_message = user_input
        agent._model_context_snapshot = ""
        agent._partial_tool_results = []
        agent._partial_tool_names = {}
        agent._partial_tool_inputs = {}
        agent._last_boundary_outcome = None
        agent._tool_outcomes_by_run_id = {}
        agent._registered_tool_runs = {}
        agent._last_boundary_record = None
        agent._active_research_binding = {}
        agent._request_search_cache = {}
        agent._request_grounded_results = {}
        agent._request_grounded_count = 0
        agent._request_grounded_inflight = set()
        agent._request_read_cache = {}
        agent._request_mutation_generation = 0
        agent._last_compiled_context_manifest = {}
        agent._pending_memory_ack = ""
        agent._pending_memory_confirmation_prompt = ""
        agent._memory_terminal_response = ""
        agent._pipeline_reasoning_steps = []
        agent._emitted_reasoning_hashes = set()
        agent._current_allowed_tools = frozenset()
        agent._last_agent_decision_kind = ""
        agent._last_agent_decision_reason_code = ""
        agent._last_research_query_plan = {}
        agent._last_research_query_plans = []
        agent._turn_identity_projection = None
        agent._turn_relevant_memory = []
        agent._turn_thinking_enabled = bool(thinking_enabled)
        agent._turn_reasoning_effort = str(reasoning_effort or "medium")
        agent._turn_reasoning_control = {}

        try:
            if not user_input:
                return "Please enter a message.", False
            agent.select_thread_runtime(session_id)
            self.task_store.migrate_legacy_session_state(agent._state_store, session_id)
            agent._current_subject_text = ""
            agent._sync_thread_state(session_id)
            agent._hydrate_pending_action_from_state()
            agent._execution_context = agent._restore_execution_context(session_id)
            state = agent._state_store.get_thread_state(session_id)
            project_id = str(state.active_project_id or "")
            active_execution_task_ids = {
                str(item.task_run_id)
                for item in agent._state_store.list_executions(thread_id=session_id, limit=200)
                if str(item.status or "") in {"running", "pending_approval"}
                and str(item.task_run_id or "")
            }
            terminal_execution_ids = {
                str(item.id)
                for item in agent._state_store.list_executions(thread_id=session_id, limit=200)
                if str(item.status or "") in {
                    "completed", "failed", "blocked", "canceled", "cancelled"
                }
            }
            pending_approval_task_ids = {
                str(item.task_run_id)
                for item in agent._state_store.list_approvals(
                    thread_id=session_id, status="pending", limit=100
                )
                if str(item.task_run_id or "")
            }
            quarantined = self.task_store.quarantine_invalid_waiting_tasks(
                session_id,
                project_id=project_id,
                active_execution_task_ids=active_execution_task_ids,
                pending_approval_task_ids=pending_approval_task_ids,
                terminal_execution_ids=terminal_execution_ids,
            )
            if quarantined:
                logger.warning("Terminalized invalid clarification checkpoints: {}", quarantined)
            agent._discord_user_info = discord_user_info
            agent._current_user_role = agent._resolve_user_role(source, discord_user_info)

            active_model_id = str(agent.provider_info.get("model") or agent._selected_model_id() or "default")
            model_binding = getattr(state, "model_binding", None)
            if model_binding is None:
                raise RuntimeError("Session model binding was not initialized before the Turn")
            if (
                str(model_binding.provider_id) != agent.llm_provider.value
                or str(model_binding.model_id) != active_model_id
            ):
                raise RuntimeError("Session model binding changed before the Turn started")
            profile_overrides = self._model_profile_overrides(project_id, agent, active_model_id)
            model_profile = resolve_model_profile(agent.llm_provider.value, active_model_id, profile_overrides)
            agent._active_model_profile = model_profile

            execution = agent._state_store.create_execution(
                request_id=request_id,
                kind="query",
                thread_id=session_id,
                source=source,
                status="running",
                query="" if internal_continuation else user_input[:2000],
                record_user_message=not internal_continuation,
                workspace_id=str(state.workspace_id or ""),
                active_project_id=project_id,
                runtime_provider=agent.llm_provider.value,
                model_id=active_model_id,
                model_snapshot=model_profile.as_dict(),
                context_budget={"semantic_boundary": "turn_understanding", "context_limit": model_profile.context_limit},
                intent="understanding",
                mode="unbound",
                phase="understanding",
                constraints=[],
                metadata={
                    "include_memory": bool(include_memory),
                    "semantic_runtime": "canonical_v8",
                    "internal_continuation": internal_continuation,
                    "continuation_trigger_id": (
                        internal_trigger_id if internal_continuation else ""
                    ),
                    "model_binding_revision": int(model_binding.binding_revision),
                    "provider_configuration_id": str(model_binding.provider_configuration_id),
                    "thinking_enabled": bool(thinking_enabled),
                    "reasoning_effort": str(reasoning_effort or "medium"),
                },
            )
            agent._current_execution_id = execution.id
            self._bind_turn(agent, callbacks, request_id, execution.id, session_id, project_id, source)
            trace = self._start_trace(agent, execution, request_id, user_input, model_profile)
            self._emit_lifecycle(agent, callbacks, "understanding")
            self._raise_if_cancelled(cancel_event)

            # Only exact controls may bypass selected-model Turn Understanding.
            exact = (
                None
                if internal_continuation
                else self._handle_exact_control(agent, user_input, callbacks)
            )
            if exact is not None:
                response, ok = exact
                actual = agent._finalize_execution_record(success=ok, response_text=response, trace=trace)
                return response, actual

            agent._turn_identity_projection = agent._canonical_identity_projection()
            if internal_continuation:
                task, interpretation = self._prepare_internal_continuation(
                    session_id=session_id,
                    project_id=project_id,
                    task_run_id=internal_task_id,
                    trigger_id=internal_trigger_id,
                    execution_id=execution.id,
                )
                eligible_tasks = [task]
                understanding = None
                agent._turn_relevant_memory = self._understanding_memory(
                    agent, task.objective, include_memory, session_id, state
                )
            else:
                (
                    understanding,
                    interpretation,
                    eligible_tasks,
                ) = self._interpret_user_turn(
                    agent,
                    user_input=user_input,
                    include_memory=include_memory,
                    session_id=session_id,
                    project_id=project_id,
                    source=source,
                    discord_user_info=discord_user_info,
                    state=state,
                    cancel_event=cancel_event,
                )
            agent._active_turn_interpretation = interpretation
            decode_diag = interpretation.safe_decode_diagnostics()
            understanding_diagnostics = (
                understanding.safe_diagnostics()
                if understanding is not None
                else {
                    "contract_version": "runtime_specialist_continuation_v1",
                    "session_id": session_id,
                    "project_id": project_id,
                    "trigger_id": internal_trigger_id,
                }
            )
            agent._state_store.update_execution(
                execution.id,
                turn_interpretation=interpretation.persisted_projection(message=user_input),
                intent=interpretation.relation.value,
                phase="understood",
                metadata={
                    **dict((agent._state_store.get_execution(execution.id) or execution).metadata or {}),
                    "turn_understanding": understanding_diagnostics,
                    "turn_understanding_output": dict(agent._turn_understanding_output_mode or {}),
                    "turn_interpretation_decode": decode_diag,
                    "turn_understanding_lifecycle": str(
                        decode_diag.get("lifecycle") or "turn_understanding_ok"
                    ),
                    "interpretation_confidence": interpretation.confidence,
                },
            )

            if interpretation.relation == TurnRelation.AMBIGUOUS:
                response = interpretation.clarification_question
                # Ambiguity before task selection is a successful semantic
                # question, not ownership of a new resumable TaskRun.
                self._project_task_references(agent, session_id, project_id, None)
                agent._state_store.update_thread_state(
                    session_id,
                    execution_status="needs_clarification",
                    safest_next_action=response[:240],
                )
                self._emit_lifecycle(agent, callbacks, "waiting_for_user")
                agent._record_turn(user_input, response)
                actual = agent._finalize_execution_record(
                    success=True, response_text=response, trace=trace
                )
                return response, actual
            if interpretation.relation == TurnRelation.BLOCKED:
                response = interpretation.clarification_question or "The selected model could not safely resolve this request."
                self._emit_lifecycle(agent, callbacks, "blocked")
                agent._record_turn(user_input, response)
                agent._finalize_execution_record(success=False, response_text=response, error=response, trace=trace)
                agent._state_store.update_execution(
                    execution.id,
                    status="blocked",
                    terminal_status=TaskRunStatus.BLOCKED_POLICY.value,
                    success=False,
                    phase="blocked",
                    error=response[:1000],
                    response_preview=response[:500],
                )
                self._project_task_references(agent, session_id, project_id, None)
                return response, False

            if internal_continuation:
                binding = TaskBinding(task=task)
            else:
                binding = self.arbitrator.apply(
                    interpretation,
                    session_id=session_id,
                    project_id=project_id,
                    execution_id=execution.id,
                    source=source,
                    eligible_task_revisions={
                        item.id: int(item.revision) for item in eligible_tasks
                    },
                )
                task = binding.task
            agent._active_task_run = task
            if task is not None:
                agent._state_store.update_execution(execution.id, task_run_id=task.id)
                self._emit_task_bound(agent, callbacks, task)
            self._project_task_references(agent, session_id, project_id, task)

            if interpretation.relation == TurnRelation.RESUME_APPROVAL:
                response, ok = self._apply_interpreted_approval(agent, interpretation, callbacks)
                if interpretation.approval_decision == ApprovalDecision.CANCEL:
                    agent._record_turn(user_input, response)
                actual = agent._finalize_execution_record(success=ok, response_text=response, trace=trace)
                return response, actual
            if binding.terminal_response:
                self._emit_lifecycle(agent, callbacks, "cancelled")
                agent._record_turn(user_input, binding.terminal_response)
                agent._finalize_execution_record(success=True, response_text=binding.terminal_response, trace=trace)
                self._project_task_references(agent, session_id, project_id, task)
                return binding.terminal_response, True

            mode = TurnModeDeriver.derive(
                interpretation,
                task,
                provider=agent.llm_provider.value,
                model_id=active_model_id,
                available_tools=set(ToolRegistry.get_names()),
            )
            mode = mode.with_allowed_tools(
                agent._filter_tool_names_for_current_context(
                    mode.allowed_tool_names,
                    respect_turn_mode=False,
                )
            )
            # Chat / tool-free turns must not retain retrieval requirements that
            # seed as UNAVAILABLE and force partial research-exhaustion answers.
            if task is not None and mode.mode.value == "chat" and not mode.allowed_tool_names:
                task = self._coerce_task_to_answer_only(agent, task)
                agent._active_task_run = task
            agent._current_mode_decision = mode
            agent._current_allowed_tools = frozenset(mode.allowed_tool_names)
            agent._execution_context = agent._bind_execution_context(mode)
            agent._state_store.update_execution(
                execution.id,
                mode=mode.mode.value,
                phase=mode.coding_phase.value if mode.coding_phase else "planning",
                constraints=list(mode.constraints),
                metadata={
                    **dict((agent._state_store.get_execution(execution.id) or execution).metadata or {}),
                    "mode": mode.as_dict(),
                },
            )

            memory_ack = self._persist_explicit_memory(agent, interpretation, user_input, task)
            if memory_ack:
                agent._pending_memory_ack = memory_ack
                agent._satisfy_non_tool_requirements(
                    {"memory"}, "authorized_memory_operation_completed"
                )
            if task is not None and getattr(agent, "_active_task_run", None) is not None:
                task = agent._active_task_run

            if agent._memory_terminal_response:
                response = str(agent._memory_terminal_response)
                agent._record_turn(user_input, response)
                actual = agent._finalize_execution_record(
                    success=True, response_text=response, trace=trace
                )
                self._finish_task(agent, task, actual)
                self._emit_lifecycle(agent, callbacks, "completed")
                return response, actual

            specialist_response = self._delegate_specialist_requirement(
                agent,
                task=task,
                model_binding=model_binding,
            )
            if task is not None:
                refreshed_task = self.task_store.get(
                    task.id,
                    session_id=session_id,
                    project_id=project_id,
                )
                if refreshed_task is not None:
                    task = refreshed_task
                    agent._active_task_run = task
                    self._project_task_references(
                        agent, session_id, project_id, task
                    )
            if specialist_response:
                agent._record_turn(user_input, specialist_response)
                actual = agent._finalize_execution_record(
                    success=True,
                    response_text=specialist_response,
                    trace=trace,
                )
                # This user Turn is complete, but its durable objective is
                # explicitly waiting on the correlated SpecialistRun.
                self._emit_lifecycle(agent, callbacks, "waiting_for_specialist")
                return specialist_response, actual

            self._materialize_skill_after_understanding(agent, task, interpretation, user_input)
            if task is not None:
                task = self._refresh_task_skill(agent, task, interpretation)
                agent._active_task_run = task

            if task is not None:
                self._emit_lifecycle(agent, callbacks, "planning")
            self._raise_if_cancelled(cancel_event)
            effective_input = self._effective_execution_input(user_input, interpretation, task)
            # Search acquisition must receive only user-authored text. The
            # composite execution input is model context, not a provider query.
            agent._active_user_query = user_input
            agent._last_user_input_for_plan = user_input
            agent._request_grounded_results = {}
            agent._request_grounded_inflight = set()
            agent._request_grounded_count = 0
            agent._request_grounded_count_by_requirement = {}
            ctx = agent._pq_build_context(effective_input, include_memory, callbacks, session_id)

            self._emit_lifecycle(agent, callbacks, "waiting_for_model")
            self._emit_lifecycle(agent, callbacks, "thinking")
            if (
                interpretation.relation == TurnRelation.CASUAL_CONVERSATION
                and task is None
                and mode.mode == TurnMode.CHAT
                and not mode.allowed_tool_names
            ):
                # Ordinary conversation is not agent work. It uses the selected
                # Session model directly with Echo identity/context, without an
                # AgentDecision envelope, TaskRun, requirements, plan, or tools.
                prompt = self._casual_conversation_prompt(
                    agent, user_input=user_input, context_bundle=ctx
                )
                response = agent._invoke_conversation_llm(prompt)
                self._raise_if_cancelled(cancel_event)
                self._emit_lifecycle(agent, callbacks, "responding")
                final_text, final_ok = agent._pq_finalize_response(
                    user_input, response, ctx, callbacks
                )
                actual = agent._finalize_execution_record(
                    success=bool(final_ok),
                    response_text=str(final_text or ""),
                    trace=trace,
                )
                self._project_task_references(
                    agent, session_id, project_id, None
                )
                self._emit_lifecycle(
                    agent, callbacks, "completed" if actual else "failed"
                )
                return str(final_text or ""), actual
            response = agent._pq_invoke_llm_agents(effective_input, ctx, callbacks)
            self._raise_if_cancelled(cancel_event)
            decision_kind = str(getattr(agent, "_last_agent_decision_kind", "") or "")
            decision_reason = str(getattr(agent, "_last_agent_decision_reason_code", "") or "")
            if decision_kind == "ask_for_input":
                if task is None:
                    raise RuntimeError("A typed ask_for_input decision requires an active TaskRun")
                question = str(response or "").strip()
                if not question:
                    raise RuntimeError("A typed ask_for_input decision did not include a question")
                task = self.task_store.checkpoint_waiting_for_user(
                    task.id,
                    session_id=session_id,
                    project_id=project_id,
                    expected_revision=task.revision,
                    execution_id=execution.id,
                    selected_skill_id=task.selected_skill_id,
                    selected_skill_version=task.selected_skill_version,
                    workflow_stage="clarification",
                    collected_inputs=task.collected_inputs,
                    missing_inputs=task.missing_inputs,
                    completion_requirements=task.completion_requirements,
                    permitted_capabilities=task.permitted_capabilities,
                )
                agent._active_task_run = task
                self._project_task_references(agent, session_id, project_id, task)
                agent._state_store.update_thread_state(
                    session_id,
                    execution_status="needs_clarification",
                    safest_next_action=question[:240],
                )
                self._emit_lifecycle(agent, callbacks, "waiting_for_user")
                agent._record_turn(user_input, question)
                actual = agent._finalize_execution_record(
                    success=True, response_text=question, trace=trace
                )
                return question, actual
            ok = decision_kind != "block"

            # The selected model's ANSWER has already passed the canonical
            # control-plane authority, completion, and grounding gate. Do not run
            # the former post-loop capability/mutation/recovery/research repair
            # stack here: it could silently replace an accepted answer and become
            # a competing completion path. Presentation-only notices remain safe.
            response = agent._enforce_volatile_retrieval_contract(effective_input, response or "")
            if agent._pending_memory_ack:
                response = f"{agent._pending_memory_ack}\n\n{response}".strip()
                agent._pending_memory_ack = ""

            self._emit_lifecycle(agent, callbacks, "responding")
            final_text, final_ok = agent._pq_finalize_response(effective_input, response, ctx, callbacks)
            actual = agent._finalize_execution_record(
                success=bool(ok and final_ok),
                response_text=str(final_text or ""),
                trace=trace,
            )
            failure_status = self._failure_task_status(decision_kind, decision_reason)
            self._finish_task(agent, task, actual, failure_status=failure_status)
            if decision_kind == "cancel":
                agent._state_store.update_execution(
                    execution.id,
                    status="canceled",
                    terminal_status="cancelled",
                    success=True,
                )
                self._emit_lifecycle(agent, callbacks, "cancelled")
                return str(final_text or ""), True
            if decision_kind == "block":
                terminal_status = failure_status.value if failure_status is not None else "blocked_policy"
                agent._state_store.update_execution(
                    execution.id,
                    status="blocked",
                    terminal_status=terminal_status,
                    success=False,
                )
                self._emit_lifecycle(agent, callbacks, "blocked")
                return str(final_text or ""), False
            terminal = agent._state_store.get_execution(execution.id)
            if terminal is not None and terminal.status == "pending_approval":
                self._emit_lifecycle(agent, callbacks, "waiting_for_approval")
            else:
                self._emit_lifecycle(agent, callbacks, "completed" if actual else "failed")
            return str(final_text or ""), actual
        except TurnCancelledError as exc:
            detail = str(exc) or "Turn cancelled by client"
            response = "Stopped by Ty."
            if execution is not None:
                cancelled_runs = agent._state_store.cancel_open_tool_runs(execution.id, detail)
                for cancelled_run in cancelled_runs:
                    try:
                        agent._dequeue_tool_run(cancelled_run.id, cancelled_run.tool_name)
                    except Exception:
                        pass
                agent._state_store.update_execution(
                    execution.id,
                    status="canceled",
                    terminal_status="cancelled",
                    success=False,
                    phase="cancelled",
                    error=detail,
                    response_preview=response,
                )
                try:
                    agent._finalize_execution_record(
                        success=False,
                        response_text=response,
                        error=detail,
                        trace=trace,
                    )
                except Exception:
                    pass
                # Generic finalization records evidence; exact cancellation has
                # already terminalized every owned ToolRun, so it is not used as
                # normal lifecycle cleanup.
                agent._state_store.update_execution(
                    execution.id,
                    status="canceled",
                    terminal_status="cancelled",
                    success=False,
                    phase="cancelled",
                    error=detail,
                    response_preview=response,
                )
            if task is not None and task.status == TaskRunStatus.RUNNING:
                try:
                    task = self.task_store.update_latest_owned(
                        task.id,
                        session_id=session_id,
                        project_id=str(task.project_id or ""),
                        execution_id=str(getattr(execution, "id", "") or ""),
                        status=TaskRunStatus.CANCELLED,
                        workflow_stage="turn_cancelled_by_client",
                        last_execution_id=str(getattr(execution, "id", "") or ""),
                    )
                    self._project_task_references(
                        agent, session_id, str(task.project_id or ""), task
                    )
                except Exception as lifecycle_exc:
                    logger.exception(
                        "TaskRun cancellation terminalization failed task_run_id={}", task.id
                    )
                    task = self.task_store.update_latest_owned(
                        task.id,
                        session_id=session_id,
                        project_id=str(task.project_id or ""),
                        execution_id=str(getattr(execution, "id", "") or ""),
                        status=TaskRunStatus.QUARANTINED,
                        workflow_stage="quarantined_cancellation_conflict",
                        quarantine_diagnostics={
                            "reason_code": "cancellation_terminalization_conflict",
                            "error_type": type(lifecycle_exc).__name__,
                            "execution_id": str(getattr(execution, "id", "") or ""),
                            "quarantined_at": time.time(),
                        },
                        last_execution_id=str(getattr(execution, "id", "") or ""),
                    )
                    response = (
                        "This Turn was stopped, and its inconsistent work record was quarantined "
                        "so it cannot affect later chat."
                    )
            agent._state_store.update_thread_state(
                session_id,
                execution_status="cancelled",
                safest_next_action="Send another message to start or resume work",
            )
            self._emit_lifecycle(agent, callbacks, "cancelled")
            return response, False

        except Exception as exc:
            provider = str(getattr(getattr(agent, "llm_provider", None), "value", agent.llm_provider) or "unknown")
            model = str(getattr(agent, "_selected_model_id", lambda: "unknown")() or "unknown")
            detail = str(exc).strip() or exc.__class__.__name__
            understanding_output = dict(
                getattr(agent, "_turn_understanding_output_mode", None) or {}
            )
            understanding_failure = str(
                understanding_output.get("failure_code") or ""
            )
            if (
                isinstance(exc, TurnUnderstandingProviderError)
                and understanding_failure == "provider_output_truncated"
            ):
                response = (
                    "The selected model hit its output limit twice while understanding this request. "
                    "Nothing ran. Please shorten the request or raise its output limit."
                )
                failure_status = TaskRunStatus.FAILED_INTERPRETATION
            elif isinstance(exc, TurnUnderstandingProviderError):
                response = (
                    "The selected model could not finish understanding that request. "
                    "This attempt is isolated; please try again."
                )
                failure_status = TaskRunStatus.FAILED_PROVIDER
            elif isinstance(exc, (TurnUnderstandingError, TimeoutError, concurrent.futures.TimeoutError)):
                tu_lifecycle = ""
                if isinstance(exc, TurnUnderstandingError):
                    tu_lifecycle = str(
                        (exc.diagnostics or {}).get("lifecycle")
                        or ((exc.diagnostics or {}).get("turn_interpretation_decode_error") or {}).get("code")
                        or ""
                    )
                if "exhausted" in tu_lifecycle.casefold() or tu_lifecycle == "turn_understanding_exhausted":
                    response = (
                        "The selected model returned an invalid request interpretation twice. "
                        "Nothing ran. Please simplify or rephrase the request."
                    )
                else:
                    response = (
                        "The selected model returned an invalid request interpretation. "
                        "Nothing ran. Please rephrase the request."
                    )
                failure_status = TaskRunStatus.FAILED_INTERPRETATION
            elif isinstance(exc, ModelProviderError):
                response = (
                    "The selected provider stopped responding. Your completed work is preserved; "
                    "try Continue when it is available."
                )
                failure_status = TaskRunStatus.FAILED_PROVIDER
            else:
                diagnostic_id = hashlib.sha256(
                    f"{session_id}:{getattr(execution, 'id', '')}:{type(exc).__name__}:{detail}".encode(
                        "utf-8", errors="ignore"
                    )
                ).hexdigest()[:12]
                response = (
                    "Echo stopped this run safely after an internal problem. "
                    f"Diagnostic ID: {diagnostic_id}."
                )
                failure_status = TaskRunStatus.FAILED_MODEL_OUTPUT
            logger.exception("Canonical semantic Turn failed session={} execution={}", session_id, getattr(execution, "id", ""))
            if execution is not None:
                current_execution = agent._state_store.get_execution(execution.id) or execution
                diagnostic_metadata = {
                    "turn_understanding_output": dict(
                        getattr(agent, "_turn_understanding_output_mode", None) or {}
                    )
                }
                if understanding is not None:
                    diagnostic_metadata["turn_understanding"] = understanding.safe_diagnostics()
                if isinstance(exc, TurnUnderstandingError) and exc.diagnostics:
                    diagnostic_metadata.update(exc.diagnostics)
                failure_metadata = dict(current_execution.metadata or {})
                failure_metadata.update(diagnostic_metadata)
                agent._state_store.update_execution(
                    execution.id,
                    status="failed",
                    terminal_status=failure_status.value,
                    success=False,
                    phase="failed",
                    error=detail[:1000],
                    response_preview=response[:500],
                    metadata=failure_metadata,
                )
                try:
                    agent._finalize_execution_record(success=False, response_text=response, error=detail, trace=trace)
                except Exception:
                    pass
                finalized_execution = agent._state_store.get_execution(execution.id) or execution
                finalized_metadata = dict(finalized_execution.metadata or {})
                finalized_metadata.update(diagnostic_metadata)
                agent._state_store.update_execution(
                    execution.id,
                    status="failed",
                    terminal_status=failure_status.value,
                    success=False,
                    phase="failed",
                    error=detail[:1000],
                    response_preview=response[:500],
                    metadata=finalized_metadata,
                )
            if task is not None:
                try:
                    self._finish_task(
                        agent,
                        task,
                        False,
                        failure_status=failure_status,
                    )
                except Exception as lifecycle_exc:
                    logger.exception(
                        "TaskRun failure terminalization failed; quarantining task_run_id={}",
                        task.id,
                    )
                    self.task_store.update_latest_owned(
                        task.id,
                        session_id=session_id,
                        project_id=str(task.project_id or ""),
                        execution_id=str(getattr(execution, "id", "") or ""),
                        status=TaskRunStatus.QUARANTINED,
                        workflow_stage="quarantined_terminalization_failure",
                        quarantine_diagnostics={
                            "reason_code": "failure_terminalization_conflict",
                            "error_type": type(lifecycle_exc).__name__,
                            "execution_id": str(getattr(execution, "id", "") or ""),
                            "quarantined_at": time.time(),
                        },
                        last_execution_id=str(getattr(execution, "id", "") or ""),
                    )
            resumable_failure = (
                task is not None
                and failure_status in RECOVERABLE_TASK_STATUSES
            )
            current_task = (
                self.task_store.get(
                    task.id,
                    session_id=session_id,
                    project_id=project_id,
                )
                if task is not None and resumable_failure
                else None
            )
            self._project_task_references(
                agent, session_id, project_id, current_task
            )
            agent._state_store.update_thread_state(
                session_id,
                execution_status="retryable" if resumable_failure else "failed",
                safest_next_action=(
                    "Continue the preserved TaskRun when the selected provider is available"
                    if resumable_failure and failure_status == TaskRunStatus.FAILED_PROVIDER
                    else "Continue the preserved TaskRun"
                    if resumable_failure
                    else "Rephrase or shorten the request and retry"
                    if failure_status == TaskRunStatus.FAILED_INTERPRETATION
                    else "Start a clean Turn and reference the diagnostic ID if the issue repeats"
                ),
            )
            self._emit_lifecycle(agent, callbacks, "failed", error=detail)
            return response, False
        finally:
            if internal_continuation and internal_task_id and internal_trigger_id:
                try:
                    self._settle_internal_continuation(
                        task_run_id=internal_task_id,
                        trigger_id=internal_trigger_id,
                        session_id=session_id,
                        project_id=project_id if "project_id" in locals() else "",
                        execution=execution,
                        agent=agent,
                    )
                except Exception:
                    logger.exception(
                        "Specialist continuation receipt could not be settled task_run_id={}",
                        internal_task_id,
                    )
            try:
                agent._record_request_metric(
                    request_id, started, source, session_id,
                    bool(execution and (agent._state_store.get_execution(execution.id) or execution).success),
                )
            except Exception:
                pass
            if getattr(agent, "_stream_buffer", None):
                try:
                    agent._stream_buffer.push_status("done")
                except Exception:
                    pass
            agent._stream_buffer = None
            agent._current_callbacks = []
            agent._current_execution_id = None
            agent._current_request_id = None
            agent._current_mode_decision = None
            agent._active_model_profile = None
            agent._turn_understanding_output_mode = None
            agent._model_latest_user_message = ""
            agent._raw_turn_user_message = ""
            agent._model_context_snapshot = ""
            agent._requested_approval_id = None
            if getattr(agent, "_turn_cancel_event", None) is cancel_event:
                agent._turn_cancel_event = None
            agent._active_approved_action = None
            agent._active_retry_action = None
            agent._active_task_run = None
            agent._active_turn_interpretation = None
            agent._turn_execution_authority = None
            agent._last_agent_decision_kind = ""
            agent._last_agent_decision_reason_code = ""
            agent._turn_identity_projection = None
            agent._turn_relevant_memory = []
            agent._pending_memory_confirmation_prompt = ""
            agent._memory_terminal_response = ""
            try:
                from agent.tools import reset_tool_execution_context
                reset_tool_execution_context(getattr(agent, "_tool_context_token", None))
            except Exception:
                pass
            agent._tool_context_token = None
            agent._canonical_semantic_flow = False

    def _prepare_internal_continuation(
        self,
        *,
        session_id: str,
        project_id: str,
        task_run_id: str,
        trigger_id: str,
        execution_id: str,
    ) -> tuple[TaskRun, TurnInterpretation]:
        """Claim one pending specialist outcome for the canonical Echo loop."""

        task = self.task_store.get(
            task_run_id,
            session_id=session_id,
            project_id=project_id,
        )
        if task is None:
            raise RuntimeError("The owning TaskRun no longer exists")
        if task.status in TERMINAL_TASK_STATUSES:
            raise RuntimeError("A terminal TaskRun cannot consume a specialist continuation")
        continuation = task.continuation
        if (
            continuation is None
            or continuation.trigger_kind != "specialist_outcome"
            or continuation.trigger_id != str(trigger_id or "")
            or continuation.status != TaskRunContinuationStatus.PENDING
        ):
            raise RuntimeError("The specialist continuation receipt is stale or already consumed")
        from agent.specialist_store import get_specialist_run_store

        specialist = get_specialist_run_store().get(
            continuation.specialist_run_id,
            session_id=session_id,
            project_id=project_id,
        )
        if (
            specialist is None
            or specialist.task_run_id != task.id
            or specialist.outcome is None
            or specialist.outcome.outcome_id != continuation.trigger_id
        ):
            raise RuntimeError("The specialist outcome does not match its TaskRun continuation")
        running = continuation.model_copy(update={
            "status": TaskRunContinuationStatus.RUNNING,
            "execution_id": execution_id,
            "started_at": time.time(),
            "updated_at": time.time(),
            "error": "",
        })
        task = self.task_store.update(
            task.id,
            session_id=session_id,
            project_id=project_id,
            expected_revision=task.revision,
            continuation=running,
            status=TaskRunStatus.RUNNING,
            workflow_stage="specialist_continuation_running",
            last_execution_id=execution_id,
        )
        interpretation = TurnInterpretation(
            relation=TurnRelation.CONTINUE_TASK,
            selected_task_id=task.id,
            proposed_objective=task.objective,
            extracted_fields={},
            missing_fields=list(task.missing_inputs),
            requested_capabilities=list(task.permitted_capabilities),
            requested_operation=task.requested_operation or "continue_task",
            constraints=["runtime_specialist_continuation"],
            requirements=list(task.requirements),
            confidence=1.0,
        )
        interpretation._decode_diagnostics = {
            "lifecycle": "runtime_specialist_continuation",
            "trigger_kind": continuation.trigger_kind,
        }
        return task, interpretation

    def _interpret_user_turn(
        self,
        agent: Any,
        *,
        user_input: str,
        include_memory: bool,
        session_id: str,
        project_id: str,
        source: str,
        discord_user_info: Optional[dict[str, Any]],
        state: Any,
        cancel_event: threading.Event,
    ) -> tuple[Any, TurnInterpretation, list[TaskRun]]:
        """Compile and validate one selected-model semantic interpretation."""

        eligible_tasks = self.task_store.continuation_candidates(
            session_id,
            project_id=project_id,
        )
        active_approvals = agent._state_store.list_approvals(
            thread_id=session_id,
            status="pending",
            limit=8,
        )
        relevant_memory = self._understanding_memory(
            agent,
            user_input,
            include_memory,
            session_id,
            state,
        )
        agent._turn_relevant_memory = relevant_memory
        envelope = self.compiler.compile(
            latest_user_message=user_input,
            assistant_identity=agent._turn_identity_projection,
            recent_conversation=self._recent_conversation(agent),
            reply_relationship=self._reply_relationship(discord_user_info),
            project_id=project_id,
            session_id=session_id,
            suspended_tasks=eligible_tasks,
            active_approvals=active_approvals,
            relevant_memory=relevant_memory,
            project_context=self._project_context(project_id),
            recent_verified_outcomes=self._recent_verified_outcomes(agent, session_id),
            entity_candidates=self._entity_candidates(user_input),
            source=source,
            channel=str((discord_user_info or {}).get("channel_id") or ""),
            capability_categories=list(CAPABILITY_CATEGORIES),
        )
        is_deterministic_continuation, continuation_modifier = (
            parse_deterministic_continuation_command(user_input)
        )
        deterministic_candidates = [
            item for item in eligible_tasks
            if item.status in {
                TaskRunStatus.RUNNING,
                TaskRunStatus.BACKGROUND,
                *RECOVERABLE_TASK_STATUSES,
            }
        ]
        foreground_id = str(getattr(state, "foreground_task_id", "") or "").strip()
        selected = next(
            (item for item in deterministic_candidates if item.id == foreground_id),
            None,
        )
        selection_reason = "runtime_deterministic_foreground_continuation"
        if selected is None and len(deterministic_candidates) == 1:
            selected = deterministic_candidates[0]
            selection_reason = "runtime_deterministic_single_task_continuation"
        if is_deterministic_continuation and selected is not None:
            interpretation = TurnInterpretation(
                relation=TurnRelation.CONTINUE_TASK,
                selected_task_id=selected.id,
                proposed_objective=selected.objective,
                extracted_fields={},
                missing_fields=list(selected.missing_inputs),
                requested_capabilities=list(selected.permitted_capabilities),
                requested_operation=selected.requested_operation or "continue_task",
                constraints=[
                    selection_reason,
                    *(
                        [f"continuation_modifier:{continuation_modifier[:500]}"]
                        if continuation_modifier
                        else []
                    ),
                ],
                requirements=list(selected.requirements),
                confidence=1.0,
            )
            interpretation._decode_diagnostics = {
                "lifecycle": selection_reason,
                "candidate_count": len(deterministic_candidates),
                "selected_task_id": selected.id,
            }
            return envelope, interpretation, eligible_tasks
        try:
            interpretation = self.interpreter.interpret(
                envelope,
                invoke_selected_model=lambda messages, schema, temperature=None: (
                    self._invoke_understanding_model(
                        agent,
                        messages,
                        schema=schema,
                        cancel_event=cancel_event,
                        temperature=temperature,
                    )
                ),
            )
        except TurnUnderstandingProviderError as exc:
            if not message_looks_like_safe_read_only_information_request(user_input):
                raise
            interpretation = minimal_safe_read_only_fallback_interpretation(
                user_input,
                reason="provider_failed_read_only_fallback",
            )
            interpretation._decode_diagnostics = {
                **dict(interpretation._decode_diagnostics or {}),
                "lifecycle": "turn_understanding_provider_read_only_fallback",
                "provider_failure": dict(exc.diagnostics or {}),
            }
            logger.warning(
                "Turn Understanding provider failed; preserving a read-only objective for bounded recovery"
            )
        interpretation = scope_interpretation_to_current_instruction(
            interpretation,
            user_input,
        )
        filtered_missing = blocking_missing_fields(
            interpretation.missing_fields,
            interpretation.requested_capabilities,
        )
        if filtered_missing != interpretation.missing_fields:
            interpretation = interpretation.model_copy(update={
                "missing_fields": filtered_missing,
            })
        return envelope, interpretation, eligible_tasks

    def _settle_internal_continuation(
        self,
        *,
        task_run_id: str,
        trigger_id: str,
        session_id: str,
        project_id: str,
        execution: Any,
        agent: Any,
    ) -> None:
        """Persist the outcome of the internal Echo Turn without finalizing twice."""

        for _ in range(4):
            task = self.task_store.get(
                task_run_id,
                session_id=session_id,
                project_id=project_id,
            )
            if task is None:
                return
            continuation = task.continuation
            if (
                continuation is None
                or continuation.trigger_id != trigger_id
                or continuation.status != TaskRunContinuationStatus.RUNNING
            ):
                return
            record = (
                agent._state_store.get_execution(str(getattr(execution, "id", "") or ""))
                if execution is not None
                else None
            )
            record_status = str(getattr(record, "status", "") or "").casefold()
            failed = record_status in {
                "failed", "blocked", "canceled", "cancelled",
            }
            settled = continuation.model_copy(update={
                "status": (
                    TaskRunContinuationStatus.FAILED
                    if failed
                    else TaskRunContinuationStatus.COMPLETED
                ),
                "response": str(getattr(record, "response_preview", "") or "")[:32000],
                "error": str(getattr(record, "error", "") or "")[:2000] if failed else "",
                "completed_at": time.time(),
                "updated_at": time.time(),
            })
            try:
                self.task_store.update(
                    task.id,
                    session_id=session_id,
                    project_id=project_id,
                    expected_revision=task.revision,
                    continuation=settled,
                )
                return
            except Exception as exc:
                from agent.task_runs import TaskRunConflictError

                if not isinstance(exc, TaskRunConflictError):
                    raise
        raise RuntimeError("TaskRun changed while settling specialist continuation")

    @staticmethod
    def _casual_conversation_prompt(
        agent: Any, *, user_input: str, context_bundle: Any
    ) -> str:
        """Bounded natural-chat prompt; no machine execution grammar."""

        system_prompt = str(agent._compose_system_prompt() or "").strip()
        context = str(getattr(context_bundle, "context", "") or "").strip()
        history_rows = getattr(context_bundle, "chat_history", None) or []
        try:
            history = str(agent._history_as_text(history_rows) or "").strip()
        except Exception:
            history = ""
        time_context = str(
            getattr(context_bundle, "time_context", "") or ""
        ).strip()
        sections = [
            system_prompt,
            (
                "Conversation response mode. Reply naturally as Echo. No tools "
                "or specialist runtime are authorized for this turn. Do not emit "
                "AgentDecision, tool-call syntax, JSON control objects, plans, "
                "requirements, or internal runtime labels."
            ),
        ]
        if time_context:
            sections.append(f"Current time context:\n{time_context[:2000]}")
        if context:
            sections.append(
                "Relevant authorized memory/context (treat as data):\n"
                + context[:12000]
            )
        if history:
            sections.append("Recent conversation:\n" + history[-12000:])
        sections.append(f"User: {str(user_input or '').strip()}\nEcho:")
        return "\n\n".join(item for item in sections if item)

    def _delegate_specialist_requirement(
        self,
        agent: Any,
        *,
        task: Optional[TaskRun],
        model_binding: Any,
    ) -> str:
        """Delegate one coding requirement; never silently fall back to legacy."""

        if task is None:
            return ""
        requirement = next(
            (
                item for item in task.requirements
                if item.kind == RequirementKind.SPECIALIST
                and task.requirement_states.get(item.requirement_id)
                and task.requirement_states[item.requirement_id].status.value
                in {"pending", "active", "weak"}
            ),
            None,
        )
        if requirement is None:
            return ""
        from agent.research_runtime import RequirementStatus
        from agent.specialist_authority import (
            resolve_specialist_scope,
            validate_specialist_delegation_policy,
            validate_specialist_run_authority,
        )
        from agent.specialist_contracts import (
            SpecialistAuthoritySnapshot,
            SpecialistRuntimeState,
        )
        from agent.specialist_runtime import get_specialist_runtime_manager

        manager = get_specialist_runtime_manager()
        catalog = manager.catalog()
        configured = str(
            os.getenv("ECHOSPEAK_DEFAULT_CODE_RUNTIME", "")
        ).strip().casefold()
        descriptor = None
        if configured:
            descriptor = next(
                (
                    item for item in catalog
                    if item.runtime_id.casefold() == configured
                ),
                None,
            )
        else:
            descriptor = next(
                (
                    item for item in catalog
                    if item.state == SpecialistRuntimeState.AVAILABLE
                ),
                None,
            )
        if descriptor is None or descriptor.state != SpecialistRuntimeState.AVAILABLE:
            reason = (
                descriptor.reason
                if descriptor is not None
                else (
                    f"Configured specialist runtime '{configured}' was not found"
                    if configured else "No specialist coding runtime is configured"
                )
            )
            states = dict(task.requirement_states)
            current_state = states[requirement.requirement_id]
            states[requirement.requirement_id] = current_state.model_copy(update={
                "status": RequirementStatus.UNAVAILABLE,
                "terminal_reason": "specialist_runtime_unavailable",
                "evidence_passages": list(dict.fromkeys([
                    *current_state.evidence_passages,
                    str(reason or "Specialist runtime unavailable")[:1200],
                ]))[-12:],
                "updated_at": time.time(),
            })
            verdict = RequirementCompletionEvaluator.evaluate(
                task.requirements,
                states,
                missing_inputs=task.missing_inputs,
                pending_approval=False,
            )
            updated = self.task_store.update(
                task.id,
                session_id=task.session_id,
                project_id=task.project_id,
                expected_revision=task.revision,
                requirement_states=states,
                completion_evaluation=verdict,
                workflow_stage="specialist_runtime_unavailable",
            )
            agent._active_task_run = updated
            mode = agent._current_mode_decision
            if mode is not None:
                mode = mode.with_allowed_tools([])
                agent._current_mode_decision = mode
                agent._current_allowed_tools = frozenset()
                agent._execution_context = agent._bind_execution_context(mode)
            return ""
        try:
            validate_specialist_delegation_policy(descriptor.runtime_id)
        except Exception as exc:
            states = dict(task.requirement_states)
            current_state = states[requirement.requirement_id]
            states[requirement.requirement_id] = current_state.model_copy(update={
                "status": RequirementStatus.UNAVAILABLE,
                "terminal_reason": "specialist_authority_unavailable",
                "evidence_passages": list(dict.fromkeys([
                    *current_state.evidence_passages,
                    str(exc)[:1200],
                ]))[-12:],
                "updated_at": time.time(),
            })
            verdict = RequirementCompletionEvaluator.evaluate(
                task.requirements,
                states,
                missing_inputs=task.missing_inputs,
                pending_approval=False,
            )
            updated = self.task_store.update(
                task.id,
                session_id=task.session_id,
                project_id=task.project_id,
                expected_revision=task.revision,
                requirement_states=states,
                completion_evaluation=verdict,
                workflow_stage="specialist_authority_unavailable",
            )
            agent._active_task_run = updated
            mode = agent._current_mode_decision
            if mode is not None:
                mode = mode.with_allowed_tools([])
                agent._current_mode_decision = mode
                agent._current_allowed_tools = frozenset()
                agent._execution_context = agent._bind_execution_context(mode)
            return ""
        _state, _project, root = resolve_specialist_scope(
            task.session_id, task.project_id
        )
        graph_node = next(
            (
                item
                for item in list(getattr(task.execution_graph, "nodes", []) or [])
                if item.requirement_id == requirement.requirement_id
            ),
            None,
        )
        if graph_node is None:
            raise RuntimeError("Specialist requirement has no owning TaskRun graph node")
        authority = SpecialistAuthoritySnapshot(
            session_id=task.session_id,
            project_id=task.project_id,
            project_root=str(root),
            task_run_id=task.id,
            requirement_id=requirement.requirement_id,
            graph_node_id=graph_node.node_id,
            model_binding_revision=int(model_binding.binding_revision),
            approval_policy="on_request",
            sandbox_mode="read_only",
        )
        local_provider = str(model_binding.provider_id or "")
        use_local = (
            descriptor.runtime_id == "opencode"
            and local_provider.casefold() in {"lmstudio", "lm_studio"}
        )
        run = manager.create_and_start(
            runtime_id=descriptor.runtime_id,
            task=task,
            requirement_id=requirement.requirement_id,
            project_root=str(root),
            objective=requirement.objective,
            authority=authority,
            model_provider=local_provider if use_local else "",
            model_id=str(model_binding.model_id or "") if use_local else "",
            local_base_url=str(getattr(config.local, "base_url", "") or "")
            if use_local else "",
            authority_validator=validate_specialist_run_authority,
            continuation_scheduler=lambda finished: self.schedule_specialist_continuation(
                agent,
                finished,
            ),
        )
        return (
            f"I handed the coding step to {descriptor.display_name} in the attached "
            "Project. You can follow its progress in Visualizer. Echo still owns the "
            "overall objective and will continue automatically when the specialist "
            "returns."
        )

    @staticmethod
    def _model_profile_overrides(project_id: str, agent: Any, model_id: str) -> dict[str, Any]:
        registry = dict(getattr(config, "model_capability_profiles", {}) or {})
        overrides = dict(registry.get(f"{agent.llm_provider.value}:{model_id}") or registry.get(model_id) or {})
        if project_id:
            try:
                from agent.projects import get_project_manager
                project = get_project_manager().get_project(project_id)
                if project and project.preferred_model_profile:
                    overrides.update(dict(project.preferred_model_profile or {}))
            except Exception:
                pass
        try:
            trim = int(getattr(config, "llm_trim_max_tokens", 0) or 0)
            context = int(getattr(getattr(config, "local", None), "context_length", 0) or 0)
            if (trim or context) and "context_limit" not in overrides:
                overrides["context_limit"] = trim or context
        except Exception:
            pass
        return overrides

    @staticmethod
    def _bind_turn(agent: Any, callbacks: list, request_id: str, execution_id: str, session_id: str, project_id: str, source: str) -> None:
        try:
            reasoning_control = agent.model_runtime.resolve_turn_reasoning_control(
                thinking_enabled=bool(getattr(agent, "_turn_thinking_enabled", True)),
                reasoning_effort=str(getattr(agent, "_turn_reasoning_effort", "medium") or "medium"),
            )
            safe_reasoning_control = {
                key: value
                for key, value in reasoning_control.items()
                if key != "bind_parameters"
            }
            agent._turn_reasoning_control = safe_reasoning_control
        except Exception:
            safe_reasoning_control = {
                "thinking_enabled": bool(getattr(agent, "_turn_thinking_enabled", True)),
                "effort_level": str(getattr(agent, "_turn_reasoning_effort", "medium") or "medium"),
                "native_support": False,
                "applied": False,
            }
        try:
            from agent.stream_events import get_stream_buffer
            background = source.lower() in {"routine", "heartbeat", "proactive", "cron"}
            agent._stream_buffer = None if background else get_stream_buffer(request_id)
            if agent._stream_buffer:
                agent._stream_buffer.push_status("understanding")
        except Exception:
            agent._stream_buffer = None
        for cb in callbacks:
            put = getattr(cb, "_put", None)
            if callable(put):
                put({
                    "type": "turn_bound", "request_id": request_id,
                    "execution_id": execution_id, "turn_id": execution_id,
                    "thread_id": session_id, "active_project_id": project_id,
                    "model": str(agent._selected_model_id() or ""),
                    "reasoning_control": safe_reasoning_control,
                })

    @staticmethod
    def _start_trace(agent: Any, execution: Any, request_id: str, user_input: str, model_profile: Any) -> Optional[dict[str, Any]]:
        if not bool(getattr(agent, "_trace_enabled", False)):
            return None
        return {
            "trace_id": str(uuid.uuid4()),
            "request_id": request_id,
            "execution_id": execution.id,
            "thread_id": execution.thread_id,
            "source": execution.source,
            "query_sha256": hashlib.sha256(user_input.encode("utf-8")).hexdigest(),
            "started_at": time.time(),
            "model_id": execution.model_id,
            "model_profile_source": model_profile.source,
            "tools_used": set(),
            "tool_latencies_ms": [],
        }

    def _handle_exact_control(self, agent: Any, user_input: str, callbacks: list) -> Optional[tuple[str, bool]]:
        text = str(user_input or "").strip()
        requested_approval = str(getattr(agent, "_requested_approval_id", None) or "").strip()
        if requested_approval:
            # The typed approval endpoint supplied exact identity; no semantic inference is needed.
            from agent.mode_controller import ModeDecision, TurnMode
            approval = agent._state_store.get_approval(requested_approval)
            if approval is None or approval.status != "pending":
                return "That approval is no longer pending. Nothing was executed.", False
            # Restore the one semantic owner before crossing the tool boundary.
            # Approval identity never authorizes execution by itself; the
            # pending-action consumer still performs all fresh policy, Project,
            # path, permission, inventory, and configuration checks.
            if not all((approval.task_run_id, approval.requirement_id, approval.attempt_id)):
                agent._state_store.update_approval(
                    approval.id,
                    status="blocked",
                    outcome_summary="Legacy approval lacks canonical TaskRun lineage",
                )
                return (
                    "That approval predates canonical TaskRun lineage and cannot be resumed safely. "
                    "Nothing was executed; request the action again.",
                    False,
                )
            current_model_binding = getattr(
                agent._state_store.get_thread_state(approval.session_id),
                "model_binding",
                None,
            )
            if (
                current_model_binding is None
                or int(approval.model_binding_revision or 0) <= 0
                or int(current_model_binding.binding_revision)
                != int(approval.model_binding_revision)
            ):
                agent._state_store.update_approval(
                    approval.id,
                    status="canceled",
                    outcome_summary="Session model binding changed before approval consumption",
                )
                try:
                    stale_task = self.task_store.get(
                        approval.task_run_id,
                        session_id=approval.session_id,
                        project_id=approval.project_id,
                    )
                    if (
                        stale_task is not None
                        and stale_task.revision == approval.task_run_revision
                    ):
                        self.task_store.update(
                            stale_task.id,
                            session_id=stale_task.session_id,
                            project_id=stale_task.project_id,
                            expected_revision=stale_task.revision,
                            status=TaskRunStatus.CANCELLED,
                            workflow_stage="cancelled:model_binding_changed",
                        )
                except Exception as exc:
                    logger.warning("Stale approval TaskRun cancellation raced: {}", exc)
                return (
                    "The Session model binding changed after this approval was requested. "
                    "Nothing was executed; request the action again.",
                    False,
                )
            try:
                task = self.task_store.resume_for_approval(
                    approval.task_run_id,
                    session_id=approval.session_id,
                    project_id=approval.project_id,
                    expected_revision=approval.task_run_revision,
                    execution_id=str(agent._current_execution_id or ""),
                    requirement_id=approval.requirement_id,
                    attempt_id=approval.attempt_id,
                )
            except Exception as exc:
                logger.warning("Approval TaskRun resume failed closed: {}", exc)
                agent._state_store.update_approval(
                    approval.id,
                    status="blocked",
                    outcome_summary="TaskRun lineage changed before approval consumption",
                )
                return (
                    "The work attached to that approval changed before confirmation. "
                    "Nothing was executed; request the action again.",
                    False,
                )
            agent._active_task_run = task
            agent._active_research_binding = {
                "requirement_id": approval.requirement_id,
                "attempt_id": approval.attempt_id,
                "strategy": "approval_resume",
                "tool_name": approval.tool,
            }
            agent._state_store.update_execution(
                str(agent._current_execution_id or ""), task_run_id=task.id
            )
            self._project_task_references(
                agent, approval.session_id, approval.project_id, task
            )
            agent._current_mode_decision = ModeDecision(
                mode=TurnMode.CHAT,
                confidence=1.0,
                reason="exact approval identifier",
                user_text="confirm",
                intent_relation="confirm",
                allowed_tool_names=frozenset({approval.tool}),
            )
            agent._current_allowed_tools = frozenset({approval.tool})
            agent._execution_context = agent._bind_execution_context(agent._current_mode_decision)
            response, ok = agent._consume_pending_approval(
                approval.id,
                callbacks,
            )
            current = self.task_store.get(
                task.id, session_id=task.session_id, project_id=task.project_id
            )
            if current is not None:
                from agent.research_runtime import RequirementCompletionEvaluator

                verdict = RequirementCompletionEvaluator.evaluate(
                    current.requirements,
                    current.requirement_states,
                    missing_inputs=current.missing_inputs,
                    pending_approval=False,
                )
                current = self.task_store.update(
                    current.id,
                    session_id=current.session_id,
                    project_id=current.project_id,
                    expected_revision=current.revision,
                    completion_evaluation=verdict,
                    workflow_stage="approval_outcome_evaluated",
                    last_execution_id=str(agent._current_execution_id or ""),
                )
                agent._active_task_run = current
                self._finish_task(agent, current, bool(ok))
            return response, ok
        if text.startswith("/"):
            response = agent._handle_slash_command(text)
            if response is not None:
                response_text = str(response)
                agent._record_turn(text, response_text)
                return response_text, True
        return None

    @staticmethod
    def _recent_conversation(agent: Any) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for item in list(getattr(agent.conversation_memory, "messages", []) or [])[-12:]:
            if isinstance(item, dict):
                role = str(item.get("role") or item.get("type") or "")
                content = str(item.get("content") or "")
            else:
                role = str(getattr(item, "type", "") or getattr(item, "role", ""))
                content = str(getattr(item, "content", "") or "")
            if content.strip():
                rows.append({"role": role[:24] or "unknown", "content": content[:1200]})
        return rows

    @staticmethod
    def _reply_relationship(source_info: Optional[dict[str, Any]]) -> dict[str, str]:
        info = dict(source_info or {})
        result: dict[str, str] = {}
        aliases = {
            "reply_to_message_id": ("reply_to_message_id", "referenced_message_id", "message_reference"),
            "reply_to_author_id": ("reply_to_author_id", "referenced_author_id"),
            "quoted_text": ("quoted_text", "referenced_message_text", "reply_to_text"),
        }
        for target, keys in aliases.items():
            value = next((str(info.get(key) or "").strip() for key in keys if info.get(key)), "")
            if value:
                result[target] = value[:1200] if target == "quoted_text" else value[:200]
        return result

    @staticmethod
    def _understanding_memory(agent: Any, message: str, include: bool, session_id: str, state: Any) -> list[dict[str, Any]]:
        if not include or not agent._owner_memory_access_allowed():
            return []
        try:
            return agent.memory.runtime_memory_projection(
                message,
                session_id=session_id,
                project_id=str(state.active_project_id or ""),
                project_path=str(state.project_path or "") or None,
                limit=8,
                max_chars=3200,
            )
        except Exception as exc:
            logger.warning("Canonical runtime memory projection failed closed: {}", exc)
        return []

    @staticmethod
    def _project_context(project_id: str) -> list[dict[str, Any]]:
        if not project_id:
            return []
        try:
            from agent.projects import get_project_manager
            project = get_project_manager().get_project(project_id)
            if project is None:
                return []
            return [{
                "project_id": project.id,
                "name": str(project.name or "")[:200],
                "description": str(project.description or "")[:1200],
                "context": str(project.context_prompt or "")[:1600],
                "workspace_attached": bool(project.workspace_root),
            }]
        except Exception:
            return []

    @staticmethod
    def _recent_verified_outcomes(agent: Any, session_id: str) -> list[dict[str, Any]]:
        rows = []
        try:
            for run in agent._state_store.list_tool_runs_for_session(session_id, limit=20):
                outcome = dict(run.outcome or {})
                try:
                    persisted_outcome = ToolOutcome.model_validate(outcome)
                except (TypeError, ValueError):
                    continue
                if not is_usable_verified_outcome(persisted_outcome):
                    continue
                rows.append({
                    "tool_run_id": run.id,
                    "tool_name": run.tool_name,
                    "status": run.status,
                    "completed_at": run.completed_at,
                    "output_summary": str(outcome.get("output") or "")[:300],
                })
                if len(rows) >= 8:
                    break
        except Exception:
            pass
        return rows

    @staticmethod
    def _entity_candidates(message: str) -> list[dict[str, Any]]:
        text = str(message or "")
        found: list[dict[str, Any]] = []
        patterns = (
            ("quoted", r"[\"']([^\"']{1,180})[\"']"),
            ("email", r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
            ("path", r"(?:[A-Za-z]:[\\/][^\s\"']+|(?:\.?\.?[\\/])[^\s\"']+)"),
            ("date", r"\b(?:20\d{2}-\d{1,2}-\d{1,2}|(?:mon|tues|wednes|thurs|fri|satur|sun)day|today|tomorrow|tonight)\b"),
            ("time", r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?:\s*[ap]m)?\b|\b\d{1,2}\s*[ap]m\b"),
            ("number", r"(?<!\w)-?\d+(?:\.\d+)?(?!\w)"),
            ("named_phrase", r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b"),
        )
        for kind, pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE if kind in {"email", "date", "time"} else 0):
                value = match.group(1) if match.lastindex else match.group(0)
                value = str(value or "").strip().rstrip(".,?!")
                if value and not any(item["value"].casefold() == value.casefold() for item in found):
                    found.append({"kind": kind, "value": value, "start": match.start(), "end": match.end()})
                if len(found) >= 16:
                    return found
        return found

    @staticmethod
    def _invoke_understanding_model(
        agent: Any,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any],
        cancel_event: threading.Event,
        _retry_count: int = 0,
        _forced_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Any:
        llm = agent.model_runtime.llm
        profile = getattr(agent, "_active_model_profile", None)
        provider = str(getattr(getattr(agent, "llm_provider", None), "value", "unknown") or "unknown")
        model_id = str(getattr(profile, "model_id", "") or getattr(agent.model_runtime, "model_id", "default"))
        metadata = dict(getattr(profile, "metadata", {}) or {})
        model_key = f"{provider}:{model_id}"
        warmed = getattr(agent, "_turn_understanding_warmed_models", None)
        if not isinstance(warmed, set):
            warmed = set()
            agent._turn_understanding_warmed_models = warmed
        cold_start = bool(getattr(profile, "local", False) and model_key not in warmed)
        configured_timeout = float(
            metadata.get("turn_understanding_timeout_seconds")
            or getattr(config, "turn_understanding_timeout_seconds", 45.0)
            or 45.0
        )
        if cold_start:
            configured_timeout = max(
                configured_timeout,
                float(
                    metadata.get("turn_understanding_cold_start_timeout_seconds")
                    or getattr(config, "turn_understanding_cold_start_timeout_seconds", 120.0)
                    or 120.0
                ),
            )
        timeout_max = max(
            10.0,
            float(getattr(config, "turn_understanding_timeout_max_seconds", 120.0) or 120.0),
        )
        latency_history = getattr(agent, "_turn_understanding_latency_by_model", None)
        if not isinstance(latency_history, dict):
            latency_history = {}
            agent._turn_understanding_latency_by_model = latency_history
        recent_latency = [
            float(item) for item in list(latency_history.get(model_key, []) or [])[-8:]
            if isinstance(item, (int, float)) and item > 0
        ]
        adaptive_floor = max(recent_latency) * 1.75 if recent_latency else 0.0
        timeout = min(timeout_max, max(5.0, configured_timeout, adaptive_floor))
        output_tokens = max(
            128,
            min(
                4096,
                int(
                    _forced_output_tokens
                    or metadata.get("turn_understanding_max_output_tokens")
                    or getattr(config, "turn_understanding_max_output_tokens", 2048)
                    or 2048
                ),
            ),
        )
        try:
            readiness = ensure_selected_model_ready(
                provider,
                model_id,
                llm=llm,
                profile=profile,
                timeout=timeout,
            )
        except SelectedModelReadinessError as exc:
            failure_output = {
                "provider": provider,
                "model_id": model_id,
                "failure_code": exc.code,
                "model_load_state": "unavailable",
                "retry_count": int(_retry_count),
            }
            agent._turn_understanding_output_mode = failure_output
            raise TurnUnderstandingProviderError(
                "The selected Session model is unavailable",
                diagnostics={
                    "turn_understanding_failure": failure_output,
                    "lifecycle": "turn_understanding_provider_unavailable",
                },
            ) from exc
        if readiness.action == "loaded":
            # A failed capability probe for an unloaded model must not remain
            # authoritative after Echo explicitly loads that exact model.
            clear_structured_output_probe_cache()
        probe_timeout = float(
            getattr(config, "turn_understanding_probe_timeout_seconds", 8.0)
            or 8.0
        )
        if provider == "lmstudio" and cold_start:
            # Local first-token latency commonly exceeds the network-oriented
            # default. Capability detection is cached, so prove support once
            # rather than permanently misclassifying a healthy loaded model.
            probe_timeout = min(timeout, max(30.0, probe_timeout))
        capability = resolve_structured_output_capability(
            provider,
            model_id,
            llm=llm,
            profile=profile,
            probe_timeout=probe_timeout,
        )
        output_parameter = "num_predict" if provider == "ollama" else (
            "max_output_tokens" if provider == "gemini" else "max_tokens"
        )
        invocation_messages = [dict(item) for item in messages]
        if capability.mode == "native_json_schema" and invocation_messages:
            # The provider already receives the authoritative schema. Avoid
            # duplicating its full JSON text in the prompt, where local models
            # can mistake type titles or schema metadata for output properties.
            system_text = str(invocation_messages[0].get("content") or "")
            schema_marker = "\n\nTURN_INTERPRETATION_SCHEMA="
            if schema_marker in system_text:
                system_text = system_text.split(schema_marker, 1)[0]
            invocation_messages[0]["content"] = (
                system_text
                + "\n\nThe provider-enforced JSON Schema is authoritative. Return exactly its one object; "
                  "use only the canonical lower_snake_case property names."
            )
        qwen_non_thinking = "qwen" in model_id.casefold()
        if qwen_non_thinking and invocation_messages:
            # Qwen3's documented soft switch is confined to the semantic lane.
            # Final answering uses the untouched Session model configuration.
            invocation_messages[0]["content"] = (
                str(invocation_messages[0].get("content") or "") + "\n/no_think"
            )
        semantic_reasoning_disabled = bool(
            provider == "lmstudio"
            and any(family in model_id.casefold() for family in ("qwen", "gemma"))
        )
        bind_parameters: dict[str, Any] = {output_parameter: output_tokens}
        if temperature is not None:
            try:
                bind_parameters["temperature"] = float(temperature)
            except (TypeError, ValueError):
                pass
        if semantic_reasoning_disabled:
            bind_parameters["reasoning_effort"] = "none"
        if capability.mode == "native_json_schema" and provider == "lmstudio":
            bind_parameters["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "echospeak_turn_interpretation",
                    "strict": True,
                    "schema": dict(schema),
                },
            }
        try:
            # Bind model-generation parameters before composing the structured
            # parser pipeline. Binding a RunnableSequence would incorrectly
            # forward max_tokens into RunnableParallel.transform().
            runnable = llm.bind(**bind_parameters)
        except Exception as exc:
            runnable = llm
            logger.warning(
                "Turn Understanding output budget could not be bound for {}: {}",
                provider,
                type(exc).__name__,
            )
        output_mode = capability.as_dict()
        if capability.mode == "native_json_schema" and provider == "lmstudio":
            # LM Studio's documented structured channel is message.content.
            # Streaming the raw bound chat model avoids LangChain's fragmented
            # include_raw parser envelope while preserving cancellation.
            output_mode["method"] = "json_schema_response_format"
        elif capability.mode == "native_json_schema":
            # LangChain's OpenAI-schema converter requires a top-level title.
            # Keep it transport-only: canonical property names/schema metadata
            # supplied in the prompt remain stripped of confusing type titles.
            transport_schema = {
                "title": "echospeak_turn_interpretation",
                "description": "One strict EchoSpeak TurnInterpretation object",
                **dict(schema),
            }
            try:
                runnable = runnable.with_structured_output(
                    transport_schema,
                    include_raw=True,
                    method=capability.method,
                )
            except TypeError:
                try:
                    runnable = runnable.with_structured_output(transport_schema, include_raw=True)
                    output_mode["method"] = "provider_default_structured"
                except Exception as exc:
                    runnable = llm.bind(**bind_parameters)
                    output_mode = {
                        "mode": "prompt_json",
                        "method": "bounded_json_extraction",
                        "reason": f"native_configuration_rejected:{type(exc).__name__}",
                    }
            except Exception as exc:
                runnable = llm.bind(**bind_parameters)
                output_mode = {
                    "mode": "prompt_json",
                    "method": "bounded_json_extraction",
                    "reason": f"native_configuration_rejected:{type(exc).__name__}",
                }
        output_mode.update({
            "provider": provider,
            "model_id": model_id,
            "profile_revision": str(metadata.get("revision") or metadata.get("profile_revision") or ""),
            "timeout_seconds": timeout,
            "max_output_tokens": output_tokens,
            "output_parameter": output_parameter,
            "cold_start_allowance": cold_start,
            "adaptive_latency_samples": len(recent_latency),
            "qwen_non_thinking": qwen_non_thinking,
            "semantic_reasoning_disabled": semantic_reasoning_disabled,
            "retry_count": int(_retry_count),
            "model_load_state": readiness.state,
            "model_load_action": readiness.action,
            "model_instance_id": readiness.instance_id,
            "model_load_time_seconds": readiness.load_time_seconds,
            "input_token_estimate": max(
                1,
                (sum(len(str(item.get("content") or "")) for item in invocation_messages) + 3) // 4,
            ),
        })
        agent._turn_understanding_output_mode = output_mode
        logger.info("Turn Understanding structured-output mode: {}", json.dumps(output_mode, sort_keys=True))
        provider_stop = threading.Event()
        provider_stream_ref: dict[str, Any] = {"iterator": None}

        def close_provider_stream() -> None:
            iterator = provider_stream_ref.get("iterator")
            close = getattr(iterator, "close", None)
            if callable(close):
                try:
                    close()
                except (ValueError, RuntimeError):
                    # A generator currently executing in its provider thread
                    # will observe provider_stop at its next fragment and close
                    # itself in that thread's finally block.
                    pass

        call_started = time.monotonic()
        stream_stats: dict[str, Any] = {
            "normal_content_chars": 0,
            "reasoning_content_chars": 0,
            "stream_fragment_count": 0,
            "structured_fragment_count": 0,
            "generated_token_count": None,
            "ttft_ms": None,
            "finish_reason": "",
            "response_hash": "",
            "provider_request_id": "",
        }

        def invoke() -> Any:
            # Streaming is the cancellation boundary for local HTTP providers.
            # Closing the iterator terminates the underlying prediction instead
            # of merely abandoning a worker Future.
            if callable(getattr(runnable, "stream", None)):
                chunks: list[str] = []
                last_structured: Any = None
                iterator = runnable.stream(invocation_messages)
                provider_stream_ref["iterator"] = iterator
                try:
                    for chunk in iterator:
                        if cancel_event.is_set() or provider_stop.is_set():
                            raise TurnCancelledError("Turn Understanding provider stream cancelled")
                        stream_stats["stream_fragment_count"] += 1
                        if stream_stats["ttft_ms"] is None:
                            stream_stats["ttft_ms"] = round((time.monotonic() - call_started) * 1000, 2)
                        if isinstance(chunk, dict):
                            parsed = chunk.get("parsed") if "parsed" in chunk else chunk
                            if isinstance(parsed, dict):
                                last_structured = parsed
                                stream_stats["structured_fragment_count"] += 1
                        content = getattr(chunk, "content", chunk if isinstance(chunk, str) else "")
                        additional = getattr(chunk, "additional_kwargs", {}) or {}
                        response_metadata = getattr(chunk, "response_metadata", {}) or {}
                        if isinstance(response_metadata, dict) and not stream_stats["provider_request_id"]:
                            stream_stats["provider_request_id"] = str(
                                response_metadata.get("request_id")
                                or response_metadata.get("id")
                                or response_metadata.get("x-request-id")
                                or ""
                            )[:160]
                        if isinstance(response_metadata, dict):
                            finish = response_metadata.get("finish_reason") or response_metadata.get("stop_reason")
                            if finish:
                                stream_stats["finish_reason"] = str(finish)[:120]
                            usage = response_metadata.get("token_usage") or response_metadata.get("usage") or {}
                            if isinstance(usage, dict):
                                stream_stats["generated_token_count"] = (
                                    usage.get("completion_tokens") or usage.get("output_tokens")
                                )
                        reasoning = str(additional.get("reasoning_content") or "")
                        stream_stats["reasoning_content_chars"] += len(reasoning)
                        if isinstance(content, str) and content:
                            chunks.append(content)
                            stream_stats["normal_content_chars"] += len(content)
                            joined = "".join(chunks)
                            if len(joined) > 256_000:
                                raise ValueError("Turn Understanding response exceeded the bounded stream size")
                            try:
                                decoded = extract_json_value_once(joined, expected=dict)
                                stream_stats["response_hash"] = hashlib.sha256(
                                    joined.encode("utf-8", errors="ignore")
                                ).hexdigest()
                                stream_stats["finish_reason"] = "complete_root_json"
                                return decoded
                            except ValueError:
                                pass
                    if isinstance(last_structured, dict):
                        stream_stats["response_hash"] = hashlib.sha256(
                            json.dumps(last_structured, sort_keys=True, default=str).encode("utf-8")
                        ).hexdigest()
                        stream_stats["finish_reason"] = "structured_stream_complete"
                        return last_structured
                    joined = "".join(chunks)
                    stream_stats["response_hash"] = hashlib.sha256(
                        joined.encode("utf-8", errors="ignore")
                    ).hexdigest() if joined else ""
                    if not stream_stats["finish_reason"]:
                        stream_stats["finish_reason"] = "stream_eof"
                    if not joined and stream_stats["reasoning_content_chars"]:
                        return _SafeProviderChannelMarker(
                            content="",
                            additional_kwargs={"reasoning_content": "[present]"},
                        )
                    return joined
                finally:
                    close = getattr(iterator, "close", None)
                    if callable(close):
                        close()
                    provider_stream_ref["iterator"] = None
            result = runnable.invoke(invocation_messages)
            content = getattr(result, "content", result if isinstance(result, str) else "")
            normal = content if isinstance(content, str) else ""
            additional = getattr(result, "additional_kwargs", {}) or {}
            reasoning = str(additional.get("reasoning_content") or "")
            stream_stats["normal_content_chars"] = len(normal)
            stream_stats["reasoning_content_chars"] = len(reasoning)
            stream_stats["response_hash"] = hashlib.sha256(
                normal.encode("utf-8", errors="ignore")
            ).hexdigest() if normal else ""
            response_metadata = getattr(result, "response_metadata", {}) or {}
            stream_stats["provider_request_id"] = str(
                response_metadata.get("request_id")
                or response_metadata.get("id")
                or response_metadata.get("x-request-id")
                or ""
            )[:160]
            stream_stats["finish_reason"] = str(
                response_metadata.get("finish_reason") or response_metadata.get("stop_reason") or "invoke_complete"
            )[:120]
            usage = response_metadata.get("token_usage") or response_metadata.get("usage") or {}
            if isinstance(usage, dict):
                stream_stats["generated_token_count"] = usage.get("completion_tokens") or usage.get("output_tokens")
            return result

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="echo-turn-understanding")
        future = executor.submit(invoke)
        try:
            deadline = time.monotonic() + timeout
            while True:
                if cancel_event.is_set():
                    provider_stop.set()
                    close_provider_stream()
                    future.cancel()
                    output_mode.update(stream_stats)
                    output_mode.update({
                        "total_latency_ms": round((time.monotonic() - call_started) * 1000, 2),
                        "failure_code": "provider_cancelled",
                        "decode_stage": "provider_invocation",
                    })
                    agent._turn_understanding_output_mode = output_mode
                    raise TurnCancelledError("Turn cancelled during understanding")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    provider_stop.set()
                    close_provider_stream()
                    future.cancel()
                    output_mode.update(stream_stats)
                    output_mode.update({
                        "total_latency_ms": round((time.monotonic() - call_started) * 1000, 2),
                        "failure_code": "provider_timeout",
                        "decode_stage": "provider_invocation",
                    })
                    agent._turn_understanding_output_mode = output_mode
                    raise TimeoutError(
                        f"selected model did not return TurnUnderstanding within {timeout:.0f}s"
                    )
                try:
                    result = future.result(timeout=min(0.25, remaining))
                    total_latency = time.monotonic() - call_started
                    latency_history[model_key] = [
                        *recent_latency[-7:], total_latency
                    ]
                    output_mode.update(stream_stats)
                    output_mode["total_latency_ms"] = round(total_latency * 1000, 2)
                    output_mode["decode_stage"] = "provider_response_extracted"
                    finish_reason = str(output_mode.get("finish_reason") or "").casefold()
                    if finish_reason in {"length", "max_tokens", "max_output_tokens"}:
                        raise _TurnUnderstandingLengthError(
                            "selected model reached the Turn Understanding output limit"
                        )
                    if bool(getattr(config, "turn_understanding_diagnostic_preview", False)):
                        preview = result if isinstance(result, str) else json.dumps(
                            result, ensure_ascii=False, sort_keys=True, default=str
                        )
                        preview = re.sub(
                            r"(?i)(api[_ -]?key|password|token|secret)\s*[:=]\s*[^,}\s]+",
                            r"\1=[redacted]",
                            str(preview),
                        )
                        output_mode["sanitized_response_preview"] = re.sub(r"\s+", " ", preview).strip()[:240]
                    agent._turn_understanding_output_mode = output_mode
                    warmed.add(model_key)
                    return result
                except concurrent.futures.TimeoutError:
                    continue
                except BaseException as exc:
                    total_latency = time.monotonic() - call_started
                    length_limited = bool(
                        isinstance(exc, _TurnUnderstandingLengthError)
                        or "lengthfinishreason" in type(exc).__name__.casefold()
                        or "max_tokens" in str(exc).casefold()
                        or "maximum output" in str(exc).casefold()
                        or "output limit" in str(exc).casefold()
                    )
                    output_mode.update(stream_stats)
                    output_mode.update({
                        "total_latency_ms": round(total_latency * 1000, 2),
                        "failure_code": "provider_cancelled" if isinstance(exc, TurnCancelledError)
                        else "provider_output_truncated" if length_limited
                        else "provider_error",
                        "provider_error_type": type(exc).__name__,
                        "decode_stage": "provider_invocation",
                    })
                    agent._turn_understanding_output_mode = output_mode
                    if length_limited and _retry_count == 0:
                        retry_ceiling = max(
                            output_tokens,
                            min(
                                4096,
                                int(
                                    metadata.get("turn_understanding_retry_max_output_tokens")
                                    or getattr(config, "turn_understanding_retry_max_output_tokens", 4096)
                                    or 4096
                                ),
                            ),
                        )
                        logger.warning(
                            "Turn Understanding reached the output limit for {}:{}; retrying once at {} tokens",
                            provider,
                            model_id,
                            retry_ceiling,
                        )
                        return CanonicalSemanticRuntime._invoke_understanding_model(
                            agent,
                            messages,
                            schema=schema,
                            cancel_event=cancel_event,
                            _retry_count=1,
                            _forced_output_tokens=retry_ceiling,
                        )
                    raise
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _raise_if_cancelled(cancel_event: threading.Event) -> None:
        if cancel_event.is_set():
            raise TurnCancelledError("Turn cancelled by client")

    @staticmethod
    def _project_task_references(agent: Any, session_id: str, project_id: str, selected: Optional[TaskRun]) -> None:
        candidates = get_task_run_store().list_for_session(session_id, project_id=project_id, include_terminal=False)
        suspended = [item.id for item in candidates if item.suspended()]
        foreground = (
            selected.id
            if selected is not None
            and selected.status in {TaskRunStatus.RUNNING, *RECOVERABLE_TASK_STATUSES}
            else ""
        )
        approvals = [item.id for item in agent._state_store.list_approvals(thread_id=session_id, status="pending", limit=50)]
        agent._state_store.update_thread_state(
            session_id,
            foreground_task_id=foreground,
            suspended_task_ids=suspended,
            pending_approval_ids=approvals,
            source_metadata={"last_source": agent._current_source, "updated_at": time.time()},
            objective="",
            current_subject="",
            mode="chat",
            phase="",
        )

    def _apply_interpreted_approval(
        self, agent: Any, interpretation: TurnInterpretation, callbacks: list
    ) -> tuple[str, bool]:
        approval_id = interpretation.selected_approval_id
        approval = agent._state_store.get_approval(approval_id)
        if approval is None or approval.status != "pending":
            return "That approval is no longer pending. Nothing was executed.", False
        if interpretation.approval_decision == ApprovalDecision.CANCEL:
            updated = agent._state_store.update_approval(
                approval_id, status="canceled", outcome_summary="Canceled by selected-model Turn interpretation"
            )
            if approval.task_run_id:
                try:
                    task = self.task_store.get(
                        approval.task_run_id,
                        session_id=approval.session_id,
                        project_id=approval.project_id,
                    )
                    if task is not None and task.revision == approval.task_run_revision:
                        task = self.task_store.update(
                            task.id,
                            session_id=task.session_id,
                            project_id=task.project_id,
                            expected_revision=task.revision,
                            status=TaskRunStatus.CANCELLED,
                            workflow_stage="approval_cancelled",
                            last_execution_id=str(agent._current_execution_id or ""),
                        )
                        self._project_task_references(
                            agent, task.session_id, task.project_id, task
                        )
                except Exception as exc:
                    logger.warning("Interpreted approval cancellation raced: {}", exc)
            return f"Canceled: {updated.summary or updated.tool}.", True
        agent._requested_approval_id = approval_id
        result = self._handle_exact_control(agent, "confirm", callbacks)
        if result is None:
            return "The approval could not be consumed by the current runtime.", False
        return str(result[0] or ""), bool(result[1])

    @staticmethod
    def _persist_explicit_memory(agent: Any, interpretation: TurnInterpretation, message: str, task: Optional[TaskRun]) -> str:
        capabilities = set(interpretation.requested_capabilities or []) | set(
            getattr(task, "permitted_capabilities", []) or []
        )
        if "memory" not in capabilities:
            return ""
        fields = dict(interpretation.extracted_fields or {})
        collected = dict(getattr(task, "collected_inputs", {}) or {})
        payload = str(fields.get("memory") or fields.get("memory_text") or fields.get("fact_to_remember") or "").strip()
        operation = re.sub(
            r"[^a-z0-9_]+", "_", str(interpretation.requested_operation or "").casefold()
        ).strip("_")
        explicit_write = bool(payload) or operation in {
            "memory_write", "write_memory", "save_memory", "store_memory",
            "remember", "remember_fact", "add_memory",
        }
        pending_id = str(collected.get("pending_memory_confirmation_id") or "").strip()
        if not pending_id and not explicit_write:
            return ""
        if not agent._owner_memory_access_allowed():
            return "I could not save account memory from this source."
        try:
            from agent.memory_curator import MemoryCurator
            curator = MemoryCurator(agent.memory, llm_invoke=None)
            if pending_id:
                pending_record = curator.get_pending_confirmation(agent._thread_key()) or {}
                if str(pending_record.get("id") or "") != pending_id:
                    agent._memory_terminal_response = (
                        "That memory confirmation is stale or no longer exists. Nothing was saved."
                    )
                    return ""
                decision = fields.get("memory_confirmation")
                approved = decision is True or str(decision or "").strip().lower() in {"approve", "confirm", "yes"}
                rejected = decision is False or str(decision or "").strip().lower() in {"cancel", "reject", "no"}
                if not approved and not rejected:
                    prompt = str(
                        collected.get("memory_confirmation_prompt")
                        or "Please confirm or reject the pending memory save."
                    )
                    agent._pending_memory_confirmation_prompt = prompt
                    return ""
                if approved:
                    confirmed = curator.confirm_pending(
                        agent._thread_key(),
                        mode=(agent._current_mode_decision.mode.value if agent._current_mode_decision else None),
                        current_project_path=str(agent._execution_context.project_path or ""),
                    )
                    if not confirmed.persisted_ids:
                        agent._memory_terminal_response = (
                            "I could not save that pending memory, so I will not claim it was saved."
                        )
                    else:
                        text = confirmed.acknowledgements[0] if confirmed.acknowledgements else "the confirmed item"
                        agent._memory_terminal_response = f"Saved to durable memory: {text}"
                else:
                    curator.reject_pending(agent._thread_key())
                    agent._memory_terminal_response = "Canceled the pending memory save."
                if task is not None:
                    clean = dict(task.collected_inputs or {})
                    clean.pop("pending_memory_confirmation_id", None)
                    clean.pop("memory_confirmation_prompt", None)
                    clean["memory_confirmation"] = bool(approved)
                    updated = get_task_run_store().update(
                        task.id,
                        session_id=task.session_id,
                        project_id=task.project_id,
                        expected_revision=task.revision,
                        collected_inputs=clean,
                        missing_inputs=[item for item in task.missing_inputs if item != "memory_confirmation"],
                        input_gaps=[
                            item for item in task.input_gaps if item.field != "memory_confirmation"
                        ],
                        status=TaskRunStatus.RUNNING,
                        workflow_stage="memory_confirmation_resolved",
                        last_execution_id=str(agent._current_execution_id or ""),
                    )
                    agent._active_task_run = updated
                return ""
            # Memory capability can be read-only. Mutation requires a typed write
            # operation or typed payload; raw-message heuristics are not authority.
            if not payload:
                return "I could not identify a durable fact to remember."
            result = curator.curate_and_persist(
                user_text=payload,
                response_text="",
                explicit=True,
                session_id=agent._thread_key(),
                execution_id=str(agent._current_execution_id or ""),
                project_path=str(agent._execution_context.project_path or ""),
                mode=(agent._current_mode_decision.mode.value if agent._current_mode_decision else None),
                allow_implicit_auto=True,
                max_candidates=3,
            )
            if result.persisted_ids:
                text = result.acknowledgements[0] if result.acknowledgements else payload
                return f"I'll remember: {text}"
            if result.needs_confirmation and result.confirmation_prompt:
                if task is None:
                    return "I could not checkpoint the required memory confirmation, so nothing was saved."
                pending = dict(task.collected_inputs or {})
                pending["pending_memory_confirmation_id"] = result.pending_confirmation_id
                pending["memory_confirmation_prompt"] = result.confirmation_prompt
                updated = get_task_run_store().update(
                    task.id,
                    session_id=task.session_id,
                    project_id=task.project_id,
                    expected_revision=task.revision,
                    collected_inputs=pending,
                    missing_inputs=list(dict.fromkeys([*task.missing_inputs, "memory_confirmation"])),
                    input_gaps=[
                        *[item for item in task.input_gaps if item.field != "memory_confirmation"],
                        TaskInputGap(
                            field="memory_confirmation",
                            owner=TaskInputOwner.USER,
                            reason="explicit_memory_write_requires_user_confirmation",
                            blocking=True,
                        ),
                    ],
                    status=TaskRunStatus.SUSPENDED_WAITING_FOR_USER,
                    workflow_stage="memory_confirmation",
                    last_execution_id=str(agent._current_execution_id or ""),
                )
                agent._active_task_run = updated
                agent._pending_memory_confirmation_prompt = result.confirmation_prompt
                return ""
            return "I could not save that to durable memory, so I will not claim it was saved."
        except Exception as exc:
            logger.warning("Canonical explicit memory persistence failed: {}", exc)
            return "I could not save that to durable memory, so I will not claim it was saved."

    @staticmethod
    def _materialize_skill_after_understanding(
        agent: Any,
        task: Optional[TaskRun],
        interpretation: TurnInterpretation,
        user_input: str,
    ) -> None:
        if task is None:
            return
        if "response_only_content" in set(interpretation.constraints or []):
            return
        decision = getattr(agent, "_current_mode_decision", None)
        allowed = set(getattr(decision, "allowed_tool_names", None) or [])
        if not allowed:
            return
        try:
            # Match only the current instruction after the canonical semantic
            # boundary, never a prior Task objective or an isolated list item.
            agent._materialize_general_skill_executions(user_input)
        except Exception as exc:
            logger.debug("Post-understanding Skill proposal unavailable: {}", exc)

    def _refresh_task_skill(self, agent: Any, task: TaskRun, interpretation: TurnInterpretation) -> TaskRun:
        try:
            from agent.skill_execution import list_skill_executions_for_turn
            rows = list_skill_executions_for_turn(str(agent._current_execution_id or ""))
            skill = next((row for row in rows if not row.parent_skill_execution_id), rows[0] if rows else None)
        except Exception:
            skill = None
        return self.task_store.update(
            task.id,
            session_id=task.session_id,
            project_id=task.project_id,
            expected_revision=task.revision,
            selected_skill_id=str(getattr(skill, "skill_id", "") or ""),
            selected_skill_version=str(getattr(skill, "skill_version", "") or ""),
            workflow_stage=str(getattr(getattr(skill, "workflow_stage", None), "value", "") or task.workflow_stage),
            permitted_capabilities=list(interpretation.requested_capabilities or task.permitted_capabilities),
            last_execution_id=str(agent._current_execution_id or ""),
        )

    @staticmethod
    def _effective_execution_input(user_input: str, interpretation: TurnInterpretation, task: Optional[TaskRun]) -> str:
        if task is None:
            return user_input
        fields = json.dumps(task.collected_inputs or {}, ensure_ascii=False, sort_keys=True)
        return (
            f"Task objective: {task.objective}\n"
            f"Latest user message: {user_input}\n"
            f"Collected structured fields: {fields}\n"
            f"Requested operation: {interpretation.requested_operation or task.requested_operation or 'complete the objective'}"
        )

    @staticmethod
    def _failure_task_status(decision_kind: str, reason_code: str) -> Optional[TaskRunStatus]:
        if decision_kind != "block":
            return None
        if reason_code == "malformed_tool_call":
            return TaskRunStatus.FAILED_TOOL_PARSE
        if reason_code in {
            "turn_authority_unbound",
            "tool_outside_turn_allowlist",
            "capability_inventory_changed",
            "permission_snapshot_changed",
            "session_authority_changed",
            "project_authority_changed",
            "project_root_changed",
            "model_binding_changed",
            "task_scope_changed",
            "task_project_changed",
            "tool_not_currently_permitted",
            "tool_blocked_by_constraints",
            "tool_scope_unavailable",
            "tool_inventory_missing",
        }:
            return TaskRunStatus.BLOCKED_POLICY
        if reason_code in {
            "runtime_authority_conflict",
            "tool_requirement_mismatch",
            "no_actionable_requirement",
        }:
            # Valid tool proposal rejected by contradictory TaskRun/evaluator/graph state.
            return TaskRunStatus.RUNTIME_AUTHORITY_CONFLICT
        if reason_code in {
            "requirement_projection_disagreement",
            "completion_projection_conflict",
        }:
            return TaskRunStatus.COMPLETION_PROJECTION_CONFLICT
        if reason_code in {
            "runtime_decision_rejected",
        }:
            # Generic authority rejection — not malformed model output.
            return TaskRunStatus.BLOCKED_POLICY
        return TaskRunStatus.FAILED_MODEL_OUTPUT

    def _coerce_task_to_answer_only(self, agent: Any, task: TaskRun) -> TaskRun:
        """Rewrite tool-free Chat TaskRuns onto answer_only requirements.

        When Turn Understanding over-proposes retrieval under a Chat mode with
        an empty tool allowlist, seed_context would mark requirements UNAVAILABLE
        and force partial research footnotes onto pure conversation.
        """
        if task is None:
            return task
        if task.requirements and all(
            item.kind == RequirementKind.ANSWER_ONLY for item in task.requirements
        ):
            if task.execution_profile == ExecutionProfile.CHAT:
                return task
        answer_rows = [
            item.model_copy(update={
                "kind": RequirementKind.ANSWER_ONLY,
                "requested_fields": [],
                "acceptance_criteria": [
                    "A truthful conversational response satisfies this requirement."
                ],
            })
            for item in list(task.requirements or [])
        ]
        if not answer_rows:
            answer_rows = [
                TurnRequirement(
                    kind=RequirementKind.ANSWER_ONLY,
                    objective=str(task.objective or "Respond conversationally"),
                    acceptance_criteria=[
                        "A truthful conversational response satisfies this requirement."
                    ],
                )
            ]
        states = initial_requirement_states(answer_rows)
        verdict = RequirementCompletionEvaluator.evaluate(
            answer_rows, states, missing_inputs=task.missing_inputs, pending_approval=False
        )
        updated = self.task_store.update(
            task.id,
            session_id=task.session_id,
            project_id=task.project_id,
            expected_revision=task.revision,
            requirements=answer_rows,
            requirement_states=states,
            permitted_capabilities=["conversation"],
            execution_profile=ExecutionProfile.CHAT,
            completion_evaluation=verdict,
            workflow_stage="chat_answer_only",
            last_execution_id=str(
                getattr(agent, "_current_execution_id", "") or task.last_execution_id
            ),
        )
        logger.info(
            "Coerced TaskRun {} onto answer_only for tool-free Chat path",
            updated.id,
        )
        return updated

    def _finish_task(
        self,
        agent: Any,
        task: Optional[TaskRun],
        success: bool,
        *,
        failure_status: Optional[TaskRunStatus] = None,
    ) -> None:
        if task is None:
            self._project_task_references(
                agent, agent._thread_key(), str(agent._execution_context.active_project_id or ""), None
            )
            return
        current = self.task_store.get(task.id, session_id=task.session_id, project_id=task.project_id)
        if current is None:
            return
        if current.status in TERMINAL_TASK_STATUSES or current.status == TaskRunStatus.LEGACY_UNTRUSTED:
            self._project_task_references(agent, current.session_id, current.project_id, current)
            return
        state = agent._state_store.get_thread_state(task.session_id)
        runs = agent._state_store.list_tool_runs(str(agent._current_execution_id or ""))
        if state.pending_approval_id:
            status = TaskRunStatus.SUSPENDED_WAITING_FOR_APPROVAL
            stage = "waiting_for_approval"
        elif success:
            verdict = current.completion_evaluation
            if verdict is None or not verdict.finalizable:
                status = TaskRunStatus.FAILED_MODEL_OUTPUT
                stage = "completion_gate_rejected"
                success = False
            else:
                status = TaskRunStatus.COMPLETED
                stage = (
                    "completed_partial"
                    if str(getattr(verdict.disposition, "value", verdict.disposition)) == "partial"
                    else "completed"
                )
        else:
            status = failure_status or TaskRunStatus.FAILED
            stage = status.value
        updated = self.task_store.update_latest_owned(
            current.id,
            session_id=current.session_id,
            project_id=current.project_id,
            execution_id=str(agent._current_execution_id or ""),
            status=status,
            workflow_stage=stage,
            tool_run_ids=list(dict.fromkeys([*current.tool_run_ids, *(run.id for run in runs)])),
            verified_tool_outcomes=[
                {
                    "tool_run_id": run.id,
                    "tool_name": run.tool_name,
                    "requirement_id": str(getattr(run, "requirement_id", "") or ""),
                    "attempt_id": str(getattr(run, "attempt_id", "") or ""),
                    "evidence_ids": list(getattr(run, "evidence_ids", None) or []),
                    "verification": dict(run.verification or {}),
                }
                for run in runs if run.verification
            ],
            last_execution_id=str(agent._current_execution_id or ""),
        )
        if status == TaskRunStatus.SUSPENDED_WAITING_FOR_APPROVAL:
            approval = agent._state_store.get_pending_approval(current.session_id)
            if approval is None or approval.task_run_id != updated.id:
                raise RuntimeError("Suspended TaskRun has no exact pending ApprovalRecord")
            agent._state_store.bind_approval_task_checkpoint(
                approval.id,
                task_run_id=updated.id,
                requirement_id=approval.requirement_id,
                attempt_id=approval.attempt_id,
                task_run_revision=updated.revision,
            )
        self._project_task_references(agent, current.session_id, current.project_id, updated)

    @staticmethod
    def _emit_lifecycle(agent: Any, callbacks: list, phase: str, *, error: str = "") -> None:
        execution_id = str(getattr(agent, "_current_execution_id", "") or "")
        if execution_id:
            status_map = {
                "waiting_for_user": "needs_clarification",
                "waiting_for_approval": "needs_permission",
                "blocked": "blocked",
                "failed": "failed",
                "cancelled": "cancelled",
                "completed": "complete",
            }
            agent._state_store.update_execution(execution_id, phase=phase)
            if phase in status_map:
                agent._state_store.update_thread_state(agent._thread_key(), execution_status=status_map[phase])
            try:
                agent._state_store.add_item(
                    turn_id=execution_id,
                    item_type="lifecycle",
                    status="failed" if phase == "failed" else "complete",
                    payload={"phase": phase, "error": error[:500]},
                    session_id=agent._thread_key(),
                    project_id=str(agent._execution_context.active_project_id or ""),
                    model_id=agent._selected_model_id(),
                )
            except Exception:
                pass
        stream_error = ""
        if phase == "failed" and error:
            stream_error = "Echo stopped this run safely after an internal problem."
        elif phase == "blocked" and error:
            stream_error = "This run is blocked by its current authority or required input."
        event = {
            "type": "lifecycle",
            "phase": phase,
            "execution_id": execution_id,
            "error": stream_error,
        }
        for cb in callbacks:
            put = getattr(cb, "_put", None)
            if callable(put):
                put(event)
    @staticmethod
    def _emit_task_bound(agent: Any, callbacks: list, task: TaskRun) -> None:
        """Project the exact durable TaskRun without exposing internal reasoning."""
        from agent.stream_events import build_task_activity_event

        event = build_task_activity_event(task)
        for cb in callbacks:
            put = getattr(cb, "_put", None)
            if callable(put):
                put(event)


_runtime: Optional[CanonicalSemanticRuntime] = None
_runtime_lock = threading.Lock()


def get_canonical_semantic_runtime() -> CanonicalSemanticRuntime:
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = CanonicalSemanticRuntime()
    return _runtime
