"""EchoSpeak ownership layer for delegated specialist agent sessions."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from agent.research_runtime import (
    RequirementCompletionEvaluator,
    RequirementKind,
    RequirementStatus,
    apply_specialist_outcome_to_state,
)
from agent.specialist_adapters import (
    SpecialistAdapter,
    create_specialist_adapter,
    discover_specialist_runtimes,
)
from agent.specialist_contracts import (
    SpecialistAuthoritySnapshot,
    SpecialistEvent,
    SpecialistEventKind,
    SpecialistFailureLayer,
    SpecialistOutcome,
    SpecialistRun,
    SpecialistRunProjection,
    SpecialistRunStatus,
    SpecialistRuntimeDescriptor,
    SpecialistRuntimeState,
)
from agent.specialist_store import (
    SpecialistRunConflictError,
    SpecialistRunStore,
    get_specialist_run_store,
)
from agent.task_runs import (
    TERMINAL_TASK_STATUSES,
    TaskRun,
    TaskRunContinuation,
    TaskRunContinuationStatus,
    TaskRunConflictError,
    get_task_run_store,
)


AuthorityValidator = Callable[[SpecialistRun, str], None]
ContinuationScheduler = Callable[[SpecialistRun], None]


class SpecialistRuntimeManager:
    """Coordinates specialist transports without becoming a TaskRun owner."""

    def __init__(self, store: Optional[SpecialistRunStore] = None) -> None:
        self.store = store or get_specialist_run_store()
        self._handles: dict[str, SpecialistAdapter] = {}
        self._event_locks: dict[str, threading.RLock] = {}
        self._continuation_schedulers: dict[str, ContinuationScheduler] = {}
        self._handles_lock = threading.RLock()

    @staticmethod
    def catalog() -> list[SpecialistRuntimeDescriptor]:
        return discover_specialist_runtimes()

    @staticmethod
    def descriptor(runtime_id: str) -> SpecialistRuntimeDescriptor:
        normalized = str(runtime_id or "").strip().casefold()
        for item in discover_specialist_runtimes():
            if item.runtime_id.casefold() == normalized:
                return item
        raise KeyError(f"Unknown specialist runtime: {runtime_id}")

    def create_and_start(
        self,
        *,
        runtime_id: str,
        task: TaskRun,
        requirement_id: str,
        project_root: str,
        objective: str,
        authority: SpecialistAuthoritySnapshot,
        model_provider: str = "",
        model_id: str = "",
        local_base_url: str = "",
        authority_validator: Optional[AuthorityValidator] = None,
        continuation_scheduler: Optional[ContinuationScheduler] = None,
    ) -> SpecialistRun:
        descriptor = self.descriptor(runtime_id)
        if descriptor.state != SpecialistRuntimeState.AVAILABLE:
            raise RuntimeError(descriptor.reason or f"{descriptor.display_name} is unavailable")
        requirement = next(
            (item for item in task.requirements if item.requirement_id == requirement_id),
            None,
        )
        if requirement is None:
            raise ValueError("Specialist requirement does not belong to TaskRun")
        if requirement.kind != RequirementKind.SPECIALIST:
            raise ValueError("Only specialist requirements may delegate to a coding runtime")
        if task.status in TERMINAL_TASK_STATUSES:
            raise ValueError("Terminal TaskRun cannot start specialist work")
        resolved_root = str(Path(project_root).resolve())
        if authority.task_run_id != task.id or authority.requirement_id != requirement_id:
            raise ValueError("Specialist authority lineage does not match TaskRun")
        graph_node = next(
            (
                item
                for item in list(getattr(task.execution_graph, "nodes", []) or [])
                if item.requirement_id == requirement_id
            ),
            None,
        )
        if graph_node is None or authority.graph_node_id != graph_node.node_id:
            raise ValueError("Specialist authority does not match the TaskRun graph node")
        run = self.store.create(SpecialistRun(
            runtime_id=descriptor.runtime_id,
            runtime_kind=descriptor.kind,
            session_id=task.session_id,
            project_id=task.project_id,
            project_root=resolved_root,
            task_run_id=task.id,
            requirement_id=requirement_id,
            graph_node_id=authority.graph_node_id,
            objective=str(objective or requirement.objective),
            authority=authority,
            model_provider=str(model_provider or ""),
            model_id=str(model_id or ""),
            local_base_url=str(local_base_url or ""),
        ))
        try:
            self._bind_run_to_task(run)
        except Exception as exc:
            self.store.finish(run.id, SpecialistOutcome(
                run_id=run.id,
                status=SpecialistRunStatus.FAILED,
                verified=False,
                summary="Specialist delegation could not bind to TaskRun.",
                failure_layer=SpecialistFailureLayer.PERSISTENCE,
                failure_code="taskrun_binding_failed",
                failure_message=str(exc)[:2000],
            ))
            raise
        if continuation_scheduler is not None:
            with self._handles_lock:
                self._continuation_schedulers[run.id] = continuation_scheduler
        threading.Thread(
            target=self._launch,
            kwargs={
                "run_id": run.id,
                "prompt": run.objective,
                "authority_validator": authority_validator,
            },
            daemon=True,
            name=f"specialist-{descriptor.runtime_id}-{run.id[-8:]}",
        ).start()
        return self.store.get_unscoped(run.id) or run

    def continue_run(
        self,
        run_id: str,
        *,
        prompt: str,
        authority_validator: Optional[AuthorityValidator] = None,
        continuation_scheduler: Optional[ContinuationScheduler] = None,
    ) -> SpecialistRun:
        run = self.store.get_unscoped(run_id)
        if run is None:
            raise KeyError("SpecialistRun not found")
        if authority_validator:
            authority_validator(run, "continue")
        with self._handles_lock:
            adapter = self._handles.get(run.id)
        if run.status in {
            SpecialistRunStatus.STARTING,
            SpecialistRunStatus.WAITING_FOR_APPROVAL,
            SpecialistRunStatus.WAITING_FOR_INPUT,
        }:
            raise SpecialistRunConflictError(
                f"SpecialistRun cannot accept another turn while {run.status.value}"
            )
        if adapter is not None and run.status == SpecialistRunStatus.RUNNING:
            adapter.send_turn(str(prompt))
            return self.store.get_unscoped(run.id) or run
        reactivated = self.store.reactivate(run.id)
        if continuation_scheduler is not None:
            with self._handles_lock:
                self._continuation_schedulers[run.id] = continuation_scheduler
        try:
            self._mark_requirement_active(reactivated)
        except Exception as exc:
            failed = self.store.finish(reactivated.id, SpecialistOutcome(
                run_id=reactivated.id,
                status=SpecialistRunStatus.FAILED,
                verified=False,
                summary="Specialist follow-up could not reopen its TaskRun requirement.",
                failure_layer=SpecialistFailureLayer.PERSISTENCE,
                failure_code="taskrun_followup_binding_failed",
                failure_message=str(exc)[:2000],
            ))
            projected = self._project_outcome(failed)
            self._notify_continuation(failed, projected)
            raise
        threading.Thread(
            target=self._launch,
            kwargs={
                "run_id": reactivated.id,
                "prompt": str(prompt),
                "authority_validator": authority_validator,
            },
            daemon=True,
            name=f"specialist-resume-{reactivated.id[-8:]}",
        ).start()
        return self.store.get_unscoped(run.id) or reactivated

    def interrupt(
        self,
        run_id: str,
        *,
        authority_validator: Optional[AuthorityValidator] = None,
    ) -> SpecialistRun:
        with self._event_lock(run_id):
            run = self.store.get_unscoped(run_id)
            if run is None:
                raise KeyError("SpecialistRun not found")
            if authority_validator:
                authority_validator(run, "interrupt")
            with self._handles_lock:
                adapter = self._handles.get(run.id)
            if adapter is None:
                raise RuntimeError("Specialist runtime is not connected")
            adapter.interrupt()
            outcome = SpecialistOutcome(
                run_id=run.id,
                status=SpecialistRunStatus.INTERRUPTED,
                verified=True,
                summary="Specialist work was interrupted by the user.",
                failure_layer=SpecialistFailureLayer.SPECIALIST,
                failure_code="user_interrupted",
                failure_message="The user interrupted the delegated specialist turn.",
            )
            finished = self.store.finish(run.id, outcome)
            projected = self._project_outcome(finished)
            self._notify_continuation(finished, projected)
            return finished

    def resolve_approval(
        self,
        run_id: str,
        request_id: str,
        decision: str,
        *,
        authority_validator: Optional[AuthorityValidator] = None,
    ) -> SpecialistRun:
        with self._event_lock(run_id):
            run = self.store.get_unscoped(run_id)
            if run is None:
                raise KeyError("SpecialistRun not found")
            if str(request_id) not in run.pending_approval_ids:
                raise SpecialistRunConflictError(
                    "Specialist approval is stale or already resolved"
                )
            if authority_validator:
                authority_validator(run, "approval")
            with self._handles_lock:
                adapter = self._handles.get(run.id)
            if adapter is None:
                raise RuntimeError("Specialist runtime is not connected")
            adapter.resolve_approval(str(request_id), str(decision))
            current = self.store.resolve_pending_approval(
                run.id, str(request_id)
            )
            self._append_event(
                run.id,
                kind=SpecialistEventKind.APPROVAL_RESOLVED,
                summary=f"Specialist approval {str(decision).casefold()}",
                payload={"decision": str(decision).casefold()},
                runtime_request_id=str(request_id),
                raw_source=f"{run.runtime_id}.approval",
            )
            return current

    def projection(
        self,
        run_id: str,
        *,
        session_id: str,
        project_id: str,
        after: int = 0,
        limit: int = 500,
    ) -> Optional[SpecialistRunProjection]:
        run = self.store.get(
            run_id, session_id=session_id, project_id=project_id
        )
        if run is None:
            return None
        return SpecialistRunProjection(
            run=run,
            events=self.store.list_events(run.id, after=after, limit=limit),
        )

    def _launch(
        self,
        *,
        run_id: str,
        prompt: str,
        authority_validator: Optional[AuthorityValidator],
    ) -> None:
        run = self.store.get_unscoped(run_id)
        if run is None:
            return
        adapter: Optional[SpecialistAdapter] = None
        try:
            if authority_validator:
                authority_validator(run, "start")
            current = self.store.get_unscoped(run.id)
            if current is None:
                return
            self.store.update(
                current.id,
                expected_revision=current.revision,
                status=SpecialistRunStatus.STARTING,
                started_at=current.started_at or time.time(),
                active_turn_event_start=current.next_event_sequence,
                failure_layer=None,
                failure_code="",
                failure_message="",
            )
            adapter = create_specialist_adapter(run.runtime_id)
            with self._handles_lock:
                self._handles[run.id] = adapter
            adapter.start(
                self.store.get_unscoped(run.id) or run,
                prompt=prompt,
                emit=lambda **values: self._on_event(run.id, **values),
                terminal=lambda success, values: self._on_terminal(
                    run.id, success=success, values=values
                ),
            )
        except Exception as exc:
            logger.exception("Specialist runtime {} failed to start", run.runtime_id)
            self._append_event(
                run.id,
                kind=SpecialistEventKind.RUNTIME_FAILED,
                summary=f"{run.runtime_id} could not start",
                payload={"error": str(exc)[:2000]},
                raw_source=f"{run.runtime_id}.launch",
            )
            self._on_terminal(run.id, success=False, values={
                "failure_layer": SpecialistFailureLayer.TRANSPORT.value,
                "failure_code": "specialist_start_failed",
                "failure_message": str(exc)[:2000],
            })

    def _on_event(
        self,
        run_id: str,
        *,
        kind: SpecialistEventKind,
        summary: str = "",
        payload: Optional[dict[str, Any]] = None,
        runtime_session_id: str = "",
        runtime_turn_id: str = "",
        runtime_item_id: str = "",
        runtime_request_id: str = "",
        raw_source: str = "",
    ) -> SpecialistEvent:
        with self._event_lock(run_id):
            current = self.store.get_unscoped(run_id)
            if current is None:
                raise KeyError("SpecialistRun not found")
            changes: dict[str, Any] = {}
            if runtime_session_id and runtime_session_id != current.runtime_session_id:
                changes["runtime_session_id"] = runtime_session_id
            if runtime_turn_id and runtime_turn_id != current.runtime_turn_id:
                changes["runtime_turn_id"] = runtime_turn_id
            if kind in {
                SpecialistEventKind.RUNTIME_READY,
                SpecialistEventKind.SESSION_STARTED,
                SpecialistEventKind.TURN_STARTED,
                SpecialistEventKind.ACTION_STARTED,
                SpecialistEventKind.MESSAGE_DELTA,
            }:
                changes["status"] = SpecialistRunStatus.RUNNING
            if kind == SpecialistEventKind.APPROVAL_REQUESTED:
                ids = list(dict.fromkeys([
                    *current.pending_approval_ids,
                    str(runtime_request_id or ""),
                ]))
                changes.update({
                    "pending_approval_ids": [item for item in ids if item],
                    "status": SpecialistRunStatus.WAITING_FOR_APPROVAL,
                })
            if changes:
                current = self.store.update(
                    current.id, expected_revision=current.revision, **changes
                )
            _, event = self.store.append_event(
                run_id,
                kind=kind,
                summary=summary,
                payload=payload,
                raw_source=raw_source,
                runtime_session_id=runtime_session_id,
                runtime_turn_id=runtime_turn_id,
                runtime_item_id=runtime_item_id,
                runtime_request_id=runtime_request_id,
            )
            return event

    def _append_event(self, run_id: str, **values: Any) -> SpecialistEvent:
        with self._event_lock(run_id):
            _, event = self.store.append_event(run_id, **values)
            return event

    def _event_lock(self, run_id: str) -> threading.RLock:
        with self._handles_lock:
            return self._event_locks.setdefault(str(run_id), threading.RLock())

    def _on_terminal(
        self, run_id: str, *, success: bool, values: dict[str, Any]
    ) -> None:
        finished: Optional[SpecialistRun] = None
        continuation: Optional[ContinuationScheduler] = None
        with self._event_lock(run_id):
            current = self.store.get_unscoped(run_id)
            if current is None or current.outcome is not None:
                return
            events = self.store.list_events(
                run_id,
                after=max(0, int(current.active_turn_event_start) - 1),
                limit=2000,
            )
            final_message = self._final_message(events)
            changed_files = self._changed_files(events)
            layer_raw = values.get("failure_layer")
            failure_layer = None
            if layer_raw:
                try:
                    failure_layer = SpecialistFailureLayer(str(layer_raw))
                except ValueError:
                    failure_layer = SpecialistFailureLayer.UNKNOWN
            verified = bool(success and any(
                item.kind == SpecialistEventKind.TURN_COMPLETED for item in events
            ))
            outcome = SpecialistOutcome(
                run_id=run_id,
                status=(
                    SpecialistRunStatus.COMPLETED
                    if success else SpecialistRunStatus.FAILED
                ),
                verified=verified,
                summary=(
                    "Specialist turn completed."
                    if success else "Specialist turn failed."
                ),
                final_message=final_message,
                changed_files=changed_files,
                runtime_metadata={
                    "runtime_id": current.runtime_id,
                    "runtime_session_id": (
                        str(values.get("runtime_session_id") or "")
                        or current.runtime_session_id
                    ),
                    "runtime_turn_id": (
                        str(values.get("runtime_turn_id") or "")
                        or current.runtime_turn_id
                    ),
                    "event_count": len(events),
                },
                failure_layer=failure_layer,
                failure_code=str(values.get("failure_code") or ""),
                failure_message=str(values.get("failure_message") or "")[:2000],
            )
            finished = self.store.finish(run_id, outcome)
            task = self._project_outcome(finished)
            with self._handles_lock:
                adapter = self._handles.pop(run_id, None)
                if task is not None:
                    continuation = self._continuation_schedulers.pop(run_id, None)
                else:
                    self._continuation_schedulers.pop(run_id, None)
            if adapter is not None:
                # A completed OpenCode/Codex runtime session is durable upstream
                # and can be resumed by id; EchoSpeak does not keep orphan
                # processes.
                adapter.close()
        if finished is not None and continuation is not None:
            try:
                continuation(finished)
            except Exception:
                logger.exception(
                    "Specialist continuation scheduling failed run_id={}", run_id
                )

    def _notify_continuation(
        self,
        run: SpecialistRun,
        projected_task: Optional[TaskRun],
    ) -> None:
        with self._handles_lock:
            continuation = self._continuation_schedulers.pop(run.id, None)
        if projected_task is None or continuation is None:
            return
        try:
            continuation(run)
        except Exception:
            logger.exception(
                "Specialist continuation scheduling failed run_id={}", run.id
            )

    @staticmethod
    def _final_message(events: list[SpecialistEvent]) -> str:
        final_answers: list[str] = []
        completed_messages: list[str] = []
        snapshots: dict[str, str] = {}
        delta_chunks: list[str] = []
        for event in events:
            if event.kind not in {
                SpecialistEventKind.MESSAGE_DELTA,
                SpecialistEventKind.MESSAGE_COMPLETED,
            }:
                continue
            item = event.payload.get("item")
            if isinstance(item, dict):
                value = item.get("text") or item.get("content")
                if isinstance(value, str) and value:
                    if str(item.get("phase") or "").casefold() == "final_answer":
                        final_answers.append(value)
                    elif str(item.get("type") or "").casefold() in {
                        "agentmessage", "assistant", "message",
                    }:
                        completed_messages.append(value)
            part = event.payload.get("part")
            if isinstance(part, dict) and str(part.get("type") or "").casefold() == "text":
                value = part.get("text")
                if isinstance(value, str) and value:
                    snapshots[str(part.get("id") or len(snapshots))] = value
            delta = event.payload.get("delta")
            if isinstance(delta, str) and delta:
                delta_chunks.append(delta)
            elif not isinstance(item, dict) and not isinstance(part, dict):
                for key in ("text", "message", "content"):
                    value = event.payload.get(key)
                    if isinstance(value, str) and value:
                        delta_chunks.append(value)
                        break
        if final_answers:
            return final_answers[-1][-32000:]
        if completed_messages:
            return "\n\n".join(dict.fromkeys(completed_messages))[-32000:]
        if snapshots:
            return "\n\n".join(dict.fromkeys(snapshots.values()))[-32000:]
        return "".join(delta_chunks)[-32000:]

    @staticmethod
    def _changed_files(events: list[SpecialistEvent]) -> list[str]:
        paths: list[str] = []
        for event in events:
            if event.kind != SpecialistEventKind.FILE_CHANGED:
                continue
            payload = event.payload
            item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            candidates = [
                payload.get("path"), payload.get("filePath"),
                item.get("path"), item.get("filePath"),
            ]
            changes = item.get("changes") if isinstance(item.get("changes"), list) else []
            candidates.extend(
                row.get("path") for row in changes if isinstance(row, dict)
            )
            for value in candidates:
                text = str(value or "").strip()
                if text:
                    paths.append(text)
        return list(dict.fromkeys(paths))[:500]

    def _bind_run_to_task(self, run: SpecialistRun) -> None:
        store = get_task_run_store()
        for _ in range(3):
            task = store.get(
                run.task_run_id,
                session_id=run.session_id,
                project_id=run.project_id,
            )
            if task is None:
                raise KeyError("Owning TaskRun no longer exists")
            requirement = next(
                (item for item in task.requirements if item.requirement_id == run.requirement_id),
                None,
            )
            state = task.requirement_states.get(run.requirement_id)
            if requirement is None or state is None:
                raise ValueError("Owning specialist requirement no longer exists")
            states = dict(task.requirement_states)
            states[run.requirement_id] = state.model_copy(update={
                "status": RequirementStatus.ACTIVE,
                "specialist_run_ids": list(dict.fromkeys([
                    *state.specialist_run_ids, run.id,
                ])),
                "terminal_reason": "",
                "updated_at": time.time(),
            })
            try:
                store.update(
                    task.id,
                    session_id=task.session_id,
                    project_id=task.project_id,
                    expected_revision=task.revision,
                    specialist_run_ids=list(dict.fromkeys([
                        *task.specialist_run_ids, run.id,
                    ])),
                    requirement_states=states,
                    workflow_stage="specialist_delegated",
                )
                return
            except TaskRunConflictError:
                continue
        raise TaskRunConflictError("TaskRun changed while binding specialist work")

    def _mark_requirement_active(self, run: SpecialistRun) -> None:
        """Reopen only the bound specialist requirement for an explicit follow-up."""

        store = get_task_run_store()
        for _ in range(4):
            task = store.get(
                run.task_run_id,
                session_id=run.session_id,
                project_id=run.project_id,
            )
            if task is None or task.status in TERMINAL_TASK_STATUSES:
                raise SpecialistRunConflictError(
                    "Owning TaskRun cannot accept a specialist follow-up"
                )
            requirement = next(
                (
                    item for item in task.requirements
                    if item.requirement_id == run.requirement_id
                ),
                None,
            )
            state = task.requirement_states.get(run.requirement_id)
            if (
                requirement is None
                or requirement.kind != RequirementKind.SPECIALIST
                or state is None
            ):
                raise SpecialistRunConflictError(
                    "Owning specialist requirement is unavailable"
                )
            states = dict(task.requirement_states)
            states[run.requirement_id] = state.model_copy(update={
                "status": RequirementStatus.ACTIVE,
                "covered_fields": [],
                "missing_fields": list(requirement.requested_fields),
                "terminal_reason": "",
                "updated_at": time.time(),
            })
            verdict = RequirementCompletionEvaluator.evaluate(
                task.requirements,
                states,
                missing_inputs=task.missing_inputs,
                pending_approval=False,
            )
            try:
                store.update(
                    task.id,
                    session_id=task.session_id,
                    project_id=task.project_id,
                    expected_revision=task.revision,
                    requirement_states=states,
                    completion_evaluation=verdict,
                    workflow_stage="specialist_followup_requested",
                )
                return
            except TaskRunConflictError:
                continue
        raise TaskRunConflictError(
            "TaskRun changed while reopening specialist work"
        )

    def _project_outcome(self, run: SpecialistRun) -> Optional[TaskRun]:
        if run.outcome is None:
            return None
        store = get_task_run_store()
        for _ in range(4):
            task = store.get(
                run.task_run_id,
                session_id=run.session_id,
                project_id=run.project_id,
            )
            if task is None:
                return None
            requirement = next(
                (item for item in task.requirements if item.requirement_id == run.requirement_id),
                None,
            )
            state = task.requirement_states.get(run.requirement_id)
            if requirement is None or state is None:
                return None
            updated_state = apply_specialist_outcome_to_state(
                requirement,
                state,
                specialist_run_id=run.id,
                specialist_outcome_id=run.outcome.outcome_id,
                completed=run.outcome.status == SpecialistRunStatus.COMPLETED,
                verified=run.outcome.verified,
                failure_code=run.outcome.failure_code,
                failure_message=run.outcome.failure_message or run.outcome.final_message,
            )
            states = dict(task.requirement_states)
            states[run.requirement_id] = updated_state
            verdict = RequirementCompletionEvaluator.evaluate(
                task.requirements,
                states,
                missing_inputs=task.missing_inputs,
                pending_approval=False,
            )
            stage = (
                "specialist_completed_awaiting_finalization"
                if verdict.finalizable
                else "specialist_outcome_recorded"
            )
            prior = task.continuation
            continuation = prior
            if (
                prior is None
                or prior.trigger_id != run.outcome.outcome_id
                or prior.status == TaskRunContinuationStatus.FAILED
            ):
                continuation = TaskRunContinuation(
                    trigger_id=run.outcome.outcome_id,
                    specialist_run_id=run.id,
                )
            try:
                return store.update(
                    task.id,
                    session_id=task.session_id,
                    project_id=task.project_id,
                    expected_revision=task.revision,
                    requirement_states=states,
                    completion_evaluation=verdict,
                    continuation=continuation,
                    workflow_stage=stage,
                )
            except TaskRunConflictError:
                continue
        logger.error(
            "Specialist outcome {} could not be projected after TaskRun CAS retries",
            run.outcome.outcome_id,
        )
        return None


_MANAGER: Optional[SpecialistRuntimeManager] = None
_MANAGER_LOCK = threading.Lock()


def get_specialist_runtime_manager() -> SpecialistRuntimeManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = SpecialistRuntimeManager()
        return _MANAGER


__all__ = [
    "AuthorityValidator",
    "ContinuationScheduler",
    "SpecialistRuntimeManager",
    "get_specialist_runtime_manager",
]
