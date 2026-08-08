"""
Streaming tool-result events for real-time UI updates.

Provides NDJSON (Newline-Delimited JSON) event streaming for tool execution,
allowing frontends to show partial results as tools execute.

Event types:
  - tool_start   : Tool execution began
  - tool_chunk   : Partial result (for long-running tools)
  - tool_end     : Tool completed with final result
  - tool_error   : Tool failed with error details
  - agent_token  : LLM token streamed
  - status       : General status update
"""

from __future__ import annotations

import json
import time
import asyncio
from dataclasses import dataclass, asdict
from typing import Optional, Any, AsyncIterator
from threading import Lock

from loguru import logger


SEMANTIC_ACTIVITY_SCHEMA_VERSION = 1


def _activity_text(value: Any, limit: int = 360) -> str:
    """Return one bounded display string for the public activity projection."""

    text = " ".join(str(value or "").split()).strip()
    return text[:limit]


def _activity_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _enum_value(value: Any) -> str:
    return _activity_text(getattr(value, "value", value), 80).lower()


def _bounded_strings(values: Any, *, limit: int = 8, item_limit: int = 160) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    result: list[str] = []
    for value in values:
        text = _activity_text(value, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _activity_sources(value: Any, *, limit: int = 6) -> list[dict[str, str]]:
    rows = value if isinstance(value, list) else []
    sources: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _activity_text(row.get("url"), 600)
        if url and not url.lower().startswith(("http://", "https://")):
            url = ""
        label = _activity_text(row.get("title") or row.get("source") or url, 180)
        if not label and not url:
            continue
        key = (label.casefold(), url.casefold())
        if key in seen:
            continue
        seen.add(key)
        sources.append({"label": label or "Source", "url": url})
        if len(sources) >= limit:
            break
    return sources


def build_task_activity_event(task: Any) -> dict[str, Any]:
    """Build the one public TaskRun activity snapshot.

    Durable IDs remain on the compatibility event, while the nested activity
    projection contains only bounded user-facing semantics.  Chat, Visualizer,
    the avatar, and the desktop companion can therefore share the same live
    truth without learning private prompts, reasoning, or persistence IDs.
    """

    requirements = list(getattr(task, "requirements", None) or [])
    states = dict(getattr(task, "requirement_states", None) or {})
    liveness = getattr(task, "liveness_decision", None)
    active_id = str(getattr(liveness, "active_requirement_id", "") or "")
    active_requirement = next(
        (item for item in requirements if str(getattr(item, "requirement_id", "")) == active_id),
        None,
    )
    if active_requirement is None:
        active_requirement = next(
            (
                item
                for item in requirements
                if _enum_value(
                    getattr(states.get(str(getattr(item, "requirement_id", ""))), "status", "")
                ) in {"active", "pending", "weak"}
            ),
            None,
        )

    requirement_rows: list[dict[str, Any]] = []
    total_attempts = 0
    total_retries = 0
    total_sources = 0
    all_missing: list[str] = []
    for requirement in requirements[:24]:
        requirement_id = str(getattr(requirement, "requirement_id", "") or "")
        state = states.get(requirement_id)
        attempts = len(list(getattr(state, "attempt_ids", None) or []))
        retries = _activity_int(getattr(state, "retry_count", 0))
        sources = _activity_int(getattr(state, "source_count", 0))
        missing = _bounded_strings(getattr(state, "missing_fields", None) or [], limit=8)
        total_attempts += attempts
        total_retries += retries
        total_sources += sources
        for field in missing:
            if field not in all_missing and len(all_missing) < 12:
                all_missing.append(field)
        requirement_rows.append({
            "label": _activity_text(
                getattr(requirement, "objective", "")
                or getattr(requirement, "requested_operation", ""),
                260,
            ),
            "kind": _enum_value(getattr(requirement, "kind", "")),
            "status": _enum_value(getattr(state, "status", "pending")) or "pending",
            "missing_fields": missing,
            "attempt_count": attempts,
            "retry_count": retries,
            "source_count": sources,
            "required": bool(getattr(requirement, "required", True)),
        })

    completion = (
        getattr(liveness, "completion", None)
        or getattr(task, "completion_evaluation", None)
    )
    next_action = _enum_value(getattr(liveness, "next_action", ""))
    recovery_strategy = _activity_text(getattr(liveness, "recovery_strategy", ""), 200)
    preferred_tool = _activity_text(getattr(liveness, "preferred_tool_name", ""), 120)
    active_label = _activity_text(
        getattr(active_requirement, "objective", "")
        or getattr(active_requirement, "requested_operation", ""),
        260,
    )
    activity = {
        "schema_version": SEMANTIC_ACTIVITY_SCHEMA_VERSION,
        "kind": "task",
        "stage": _activity_text(getattr(task, "workflow_stage", ""), 100).lower(),
        "status": _enum_value(getattr(task, "status", "running")) or "running",
        "label": active_label or "Working on the current objective",
        "objective": _activity_text(getattr(task, "objective", ""), 600),
        "active_requirement": active_label,
        "requirements": requirement_rows,
        "attempt_count": total_attempts,
        "retry_count": total_retries,
        "source_count": total_sources,
        "missing_fields": all_missing,
        "next_action": next_action,
        "recovery_reason": recovery_strategy,
        "recovery_epoch": _activity_int(getattr(task, "recovery_epoch", 0)),
        "tool_name": preferred_tool,
        "completion_disposition": _enum_value(getattr(completion, "disposition", "pending")) or "pending",
        "finalizable": bool(getattr(completion, "finalizable", False)),
    }
    return {
        "type": "task_bound",
        "task_run_id": str(getattr(task, "id", "") or ""),
        "task_revision": _activity_int(getattr(task, "revision", 0)),
        "objective": activity["objective"],
        "active_requirement": active_label,
        "status": activity["status"],
        "requirements": requirement_rows,
        "next_action": next_action,
        "recovery_reason": recovery_strategy,
        "recovery_epoch": activity["recovery_epoch"],
        "completion_disposition": activity["completion_disposition"],
        "finalizable": activity["finalizable"],
        "activity": activity,
    }


def semantic_activity_from_stream_payload(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Project one existing stream packet into the public semantic contract."""

    supplied = payload.get("activity")
    if isinstance(supplied, dict):
        activity: dict[str, Any] = {
            "schema_version": SEMANTIC_ACTIVITY_SCHEMA_VERSION,
            "kind": _activity_text(supplied.get("kind"), 80).lower() or "status",
        }
        text_fields = {
            "stage": 100,
            "status": 100,
            "label": 360,
            "objective": 600,
            "active_requirement": 260,
            "model": 180,
            "tool_name": 120,
            "next_action": 100,
            "recovery_reason": 300,
            "completion_disposition": 100,
        }
        for field_name, field_limit in text_fields.items():
            if field_name in supplied:
                activity[field_name] = _activity_text(supplied.get(field_name), field_limit)
        for field_name in (
            "attempt_count", "retry_count", "source_count", "recovery_epoch", "iteration",
        ):
            if field_name in supplied:
                activity[field_name] = _activity_int(supplied.get(field_name))
        if "finalizable" in supplied:
            activity["finalizable"] = bool(supplied.get("finalizable"))
        if "missing_fields" in supplied:
            activity["missing_fields"] = _bounded_strings(supplied.get("missing_fields"), limit=12)
        if "sources" in supplied:
            activity["sources"] = _activity_sources(supplied.get("sources"))
        requirement_rows: list[dict[str, Any]] = []
        for row in list(supplied.get("requirements") or [])[:24]:
            if not isinstance(row, dict):
                continue
            requirement_rows.append({
                "label": _activity_text(row.get("label"), 260),
                "kind": _activity_text(row.get("kind"), 80).lower(),
                "status": _activity_text(row.get("status"), 80).lower() or "pending",
                "missing_fields": _bounded_strings(row.get("missing_fields"), limit=8),
                "attempt_count": _activity_int(row.get("attempt_count")),
                "retry_count": _activity_int(row.get("retry_count")),
                "source_count": _activity_int(row.get("source_count")),
                "required": bool(row.get("required", True)),
            })
        if "requirements" in supplied:
            activity["requirements"] = requirement_rows
        return activity

    event_type = _activity_text(payload.get("type"), 80).lower()
    if event_type in {"agent_token", "memory_saved", "task_plan", "thinking_step"}:
        return None

    activity: dict[str, Any] = {
        "schema_version": SEMANTIC_ACTIVITY_SCHEMA_VERSION,
        "kind": event_type or "status",
    }
    if event_type == "turn_bound":
        activity.update({
            "stage": "understanding",
            "status": "running",
            "label": "Understanding the request",
            "model": _activity_text(payload.get("model"), 180),
        })
    elif event_type == "iteration_boundary":
        iteration = _activity_int(payload.get("iteration"))
        activity.update({
            "kind": "model",
            "stage": "waiting_for_model",
            "status": "running",
            "label": f"Model pass {iteration}" if iteration else "Waiting for the selected model",
            "model": _activity_text(payload.get("model"), 180),
            "iteration": iteration,
        })
    elif event_type == "reasoning_summary":
        activity.update({
            "kind": "reasoning",
            "stage": "thinking",
            "status": "running",
            "label": _activity_text(payload.get("content"), 360) or "Reviewing the next step",
        })
    elif event_type == "recovery":
        activity.update({
            "stage": "thinking",
            "status": "retrying",
            "label": _activity_text(payload.get("message"), 300) or "Trying another approach",
            "recovery_reason": _activity_text(payload.get("message"), 300),
        })
    elif event_type == "lifecycle":
        phase = _activity_text(payload.get("phase"), 100).lower()
        labels = {
            "understanding": "Understanding the request",
            "planning": "Organizing the work",
            "waiting_for_model": "Waiting for the selected model",
            "thinking": "Working through the next step",
            "responding": "Preparing the response",
            "waiting_for_approval": "Waiting for approval",
            "waiting_for_user": "Waiting for your input",
            "blocked": "Blocked by the current authority",
            "failed": "This run stopped safely",
            "cancelled": "Stopped by Ty",
            "completed": "Completed",
        }
        activity.update({
            "stage": phase,
            "status": "failed" if phase == "failed" else phase,
            "label": labels.get(phase, _activity_text(phase.replace("_", " ").title(), 160)),
        })
    elif event_type == "tool_start":
        tool_name = _activity_text(payload.get("name"), 120)
        activity.update({
            "kind": "tool",
            "stage": "tool",
            "status": "running",
            "label": f"Using {tool_name.replace('_', ' ')}" if tool_name else "Using a tool",
            "tool_name": tool_name,
        })
    elif event_type in {"tool_end", "tool_error"}:
        tool_name = _activity_text(payload.get("name"), 120)
        outcome = payload.get("outcome")
        outcome_success = outcome.get("success", True) if isinstance(outcome, dict) else True
        failed = event_type == "tool_error" or not bool(outcome_success)
        activity.update({
            "kind": "tool",
            "stage": "tool",
            "status": "failed" if failed else "succeeded",
            "label": (
                f"{tool_name.replace('_', ' ')} failed"
                if failed and tool_name
                else f"{tool_name.replace('_', ' ')} finished"
                if tool_name
                else "Tool failed" if failed else "Tool finished"
            ),
            "tool_name": tool_name,
        })
        research = payload.get("research")
        if isinstance(research, dict):
            sources = _activity_sources(research.get("evidence"))
            if sources:
                activity["sources"] = sources
                activity["source_count"] = max(
                    len(sources), _activity_int(research.get("evidence_count"))
                )
        if "sources" not in activity and isinstance(outcome, dict):
            provider = _activity_text(outcome.get("provider"), 180)
            if provider:
                activity["sources"] = [{"label": provider, "url": ""}]
                activity["source_count"] = max(1, _activity_int(outcome.get("source_count")))
    elif event_type == "partial_reply":
        activity.update({"kind": "response", "stage": "responding", "status": "running", "label": "Responding"})
    elif event_type == "final":
        thread_state = payload.get("thread_state")
        if not isinstance(thread_state, dict):
            thread_state = {}
        execution_status = _activity_text(
            thread_state.get("execution_status"), 100
        ).lower()
        final_status = execution_status or ("succeeded" if payload.get("success") else "failed")
        final_label = {
            "needs_permission": "Waiting for approval",
            "needs_approval": "Waiting for approval",
            "needs_clarification": "Waiting for your input",
            "blocked": "Blocked by the current authority",
            "retryable": "Trying another approach is required",
            "failed": "This run stopped safely",
        }.get(final_status, "Response ready" if payload.get("success") else "This run stopped safely")
        activity.update({
            "kind": "response",
            "stage": "completed",
            "status": final_status,
            "label": final_label,
            "completion_disposition": execution_status or ("complete" if payload.get("success") else "blocked"),
        })
    elif event_type == "error":
        activity.update({"stage": "failed", "status": "failed", "label": _activity_text(payload.get("message"), 300) or "This run stopped safely"})
    elif event_type == "status":
        mode = _activity_text(payload.get("agent_mode"), 80).lower()
        activity.update({
            "stage": mode,
            "status": "idle" if mode == "idle" else "running",
            "label": _activity_text(mode.replace("_", " ").title(), 160) or "Working",
            "tool_name": _activity_text(payload.get("tool"), 120),
        })
    else:
        return None
    return activity


@dataclass
class StreamEvent:
    """A single event in the NDJSON stream."""
    event_type: str  # tool_start, tool_chunk, tool_end, tool_error, agent_token, status
    timestamp: float
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None
    data: Optional[Any] = None
    error: Optional[str] = None
    progress: Optional[float] = None  # 0.0 – 1.0 for progress bars
    metadata: Optional[dict] = None

    def to_json(self) -> str:
        """Serialize to a single JSON line."""
        d = {k: v for k, v in asdict(self).items() if v is not None}
        return json.dumps(d, ensure_ascii=False, default=str)


class StreamBuffer:
    """Thread-safe buffer for streaming events.

    Tools push events into the buffer. The SSE/NDJSON endpoint
    drains the buffer asynchronously.
    """

    def __init__(self, max_events: int = 1000):
        self._lock = Lock()
        self._events: list[StreamEvent] = []
        self._max = max_events
        self._closed = False
        self._async_event: Optional[asyncio.Event] = None

    def _get_async_event(self) -> asyncio.Event:
        """Lazy-create the asyncio event (must be in async context)."""
        if self._async_event is None:
            self._async_event = asyncio.Event()
        return self._async_event

    def push(self, event: StreamEvent) -> None:
        """Push an event into the buffer (thread-safe, callable from sync code)."""
        with self._lock:
            if self._closed:
                return
            self._events.append(event)
            if len(self._events) > self._max:
                self._events = self._events[-self._max:]

        # Signal async consumer
        if self._async_event is not None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.call_soon_threadsafe(self._async_event.set)
            except RuntimeError:
                pass

    def push_tool_start(self, tool_name: str, tool_call_id: str = "", metadata: Optional[dict] = None) -> None:
        """Push a tool_start event."""
        self.push(StreamEvent(
            event_type="tool_start",
            timestamp=time.time(),
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            metadata=metadata,
        ))

    def push_tool_chunk(self, tool_name: str, data: Any, progress: Optional[float] = None, tool_call_id: str = "") -> None:
        """Push a partial-result chunk."""
        self.push(StreamEvent(
            event_type="tool_chunk",
            timestamp=time.time(),
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            data=data,
            progress=progress,
        ))

    def push_tool_end(self, tool_name: str, data: Any, tool_call_id: str = "") -> None:
        """Push a tool_end event with the final result."""
        self.push(StreamEvent(
            event_type="tool_end",
            timestamp=time.time(),
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            data=data,
        ))

    def push_tool_error(self, tool_name: str, error: str, tool_call_id: str = "") -> None:
        """Push a tool_error event."""
        self.push(StreamEvent(
            event_type="tool_error",
            timestamp=time.time(),
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            error=error,
        ))

    def push_token(self, token: str) -> None:
        """Push an LLM token event."""
        self.push(StreamEvent(
            event_type="agent_token",
            timestamp=time.time(),
            data=token,
        ))

    def push_status(self, message: str, metadata: Optional[dict] = None) -> None:
        """Push a status update."""
        self.push(StreamEvent(
            event_type="status",
            timestamp=time.time(),
            data=message,
            metadata=metadata,
        ))

    def push_task_plan(self, tasks: list[dict]) -> None:
        """Push a read-only model plan projection for compact Chat status."""
        self.push(StreamEvent(
            event_type="task_plan",
            timestamp=time.time(),
            data=tasks,
        ))

    def drain(self) -> list[StreamEvent]:
        """Drain all events from the buffer."""
        with self._lock:
            events = self._events
            self._events = []
        return events

    def close(self) -> None:
        """Close the buffer (no more events will be accepted)."""
        with self._lock:
            self._closed = True
        if self._async_event is not None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.call_soon_threadsafe(self._async_event.set)
            except RuntimeError:
                pass

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def stream(self, poll_interval: float = 0.1, timeout: float = 300.0) -> AsyncIterator[str]:
        """Async generator yielding NDJSON lines.

        Use in a FastAPI StreamingResponse:
            return StreamingResponse(buffer.stream(), media_type="application/x-ndjson")
        """
        start = time.time()
        ae = self._get_async_event()

        while True:
            events = self.drain()
            for event in events:
                yield event.to_json() + "\n"

            if self._closed:
                break

            if (time.time() - start) > timeout:
                yield StreamEvent(
                    event_type="status",
                    timestamp=time.time(),
                    data="Stream timeout reached",
                ).to_json() + "\n"
                break

            ae.clear()
            try:
                await asyncio.wait_for(ae.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                pass  # Normal — just poll again


# ── Singleton per request ───────────────────────────────────────────

_active_buffers: dict[str, StreamBuffer] = {}
_buffers_lock = Lock()


def get_stream_buffer(request_id: str) -> StreamBuffer:
    """Get or create a stream buffer for a request."""
    with _buffers_lock:
        if request_id not in _active_buffers:
            _active_buffers[request_id] = StreamBuffer()
        return _active_buffers[request_id]


def cleanup_buffer(request_id: str) -> None:
    """Remove a buffer after streaming is complete."""
    with _buffers_lock:
        buf = _active_buffers.pop(request_id, None)
        if buf:
            buf.close()
