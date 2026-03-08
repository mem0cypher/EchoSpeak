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
        """Push a task_plan event with the full decomposed plan."""
        self.push(StreamEvent(
            event_type="task_plan",
            timestamp=time.time(),
            data=tasks,
        ))

    def push_task_step(
        self,
        index: int,
        status: str,
        description: str = "",
        tool: str = "",
        result_preview: str = "",
        total: int = 0,
    ) -> None:
        """Push a task_step event for a single step status change."""
        self.push(StreamEvent(
            event_type="task_step",
            timestamp=time.time(),
            data={
                "index": index,
                "status": status,
                "description": description,
                "tool": tool,
                "result_preview": result_preview[:200] if result_preview else "",
                "total": total,
            },
        ))

    def push_task_reflection(
        self,
        index: int,
        accepted: bool,
        reason: str = "",
        cycle: int = 0,
    ) -> None:
        """Push a task_reflection event when the agent reflects on a step."""
        self.push(StreamEvent(
            event_type="task_reflection",
            timestamp=time.time(),
            data={
                "index": index,
                "accepted": accepted,
                "reason": reason[:200] if reason else "",
                "cycle": cycle,
            },
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
