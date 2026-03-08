"""
Proactive Engine for EchoSpeak v6.1.0 — Autonomous Agent Mode.

Manages a task queue of things the agent should do when idle:
  - Memory consolidation & review
  - Follow-up tasks from conversations
  - Ambient monitoring (Discord, email digests)
  - Routine preparation (pre-gather data for upcoming routines)

Architecture: runs alongside HeartbeatManager as a daemon thread.
Uses the same route_message() infrastructure for output delivery.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class ProactiveTask:
    """A single task in the proactive queue."""

    def __init__(
        self,
        prompt: str,
        priority: int = 5,
        cooldown_minutes: int = 60,
        label: str = "Proactive",
        task_id: Optional[str] = None,
        source: str = "system",
        max_runs: int = 0,
    ) -> None:
        self.id = task_id or str(uuid.uuid4())[:8]
        self.prompt = prompt
        self.priority = priority  # 1 = highest, 10 = lowest
        self.cooldown_minutes = cooldown_minutes
        self.label = label
        self.source = source  # "system", "user", "conversation"
        self.max_runs = max_runs  # 0 = unlimited
        self.run_count = 0
        self.last_run: Optional[str] = None
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.enabled = True

    def is_due(self) -> bool:
        """Check if enough time has passed since last run."""
        if not self.enabled:
            return False
        if self.max_runs > 0 and self.run_count >= self.max_runs:
            return False
        if self.last_run is None:
            return True
        try:
            last = datetime.fromisoformat(self.last_run)
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            return elapsed >= self.cooldown_minutes * 60
        except Exception:
            return True

    def mark_run(self) -> None:
        self.last_run = datetime.now(timezone.utc).isoformat()
        self.run_count += 1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt[:120],
            "priority": self.priority,
            "cooldown_minutes": self.cooldown_minutes,
            "label": self.label,
            "source": self.source,
            "max_runs": self.max_runs,
            "run_count": self.run_count,
            "last_run": self.last_run,
            "created_at": self.created_at,
            "enabled": self.enabled,
        }


# Sentinel for "nothing to act on"
_NO_ACTION_SENTINEL = "NO_ACTION"


class ProactiveEngine:
    """
    Manages autonomous agent behavior — things EchoSpeak does on its own.

    Usage:
        engine = ProactiveEngine(agent=echo_agent)
        engine.seed_default_tasks()    # Add built-in tasks
        engine.start()                 # Non-blocking daemon thread
    """

    def __init__(
        self,
        agent: Any,
        check_interval_minutes: int = 15,
        channels: Optional[List[str]] = None,
    ) -> None:
        from config import config

        self._agent = agent
        self._check_interval = check_interval_minutes
        self._channels = channels or list(
            getattr(config, "notification_channels", None) or
            getattr(config, "heartbeat_channels", ["web"])
        )

        self._queue: List[ProactiveTask] = []
        self._queue_lock = threading.RLock()

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False

        # History of proactive actions taken
        self._history: List[Dict[str, Any]] = []
        self._history_lock = threading.Lock()
        self._history_max = 30

    # ------------------------------------------------------------------
    # Task queue management
    # ------------------------------------------------------------------

    def set_agent(self, agent: Any) -> None:
        """Rebind the proactive engine to a new agent instance."""
        self._agent = agent

    def add_task(
        self,
        prompt: str,
        priority: int = 5,
        cooldown_minutes: int = 60,
        label: str = "Proactive",
        source: str = "system",
        max_runs: int = 0,
        task_id: Optional[str] = None,
    ) -> ProactiveTask:
        """Add a task to the proactive queue."""
        task = ProactiveTask(
            prompt=prompt,
            priority=priority,
            cooldown_minutes=cooldown_minutes,
            label=label,
            source=source,
            max_runs=max_runs,
            task_id=task_id,
        )
        with self._queue_lock:
            self._queue.append(task)
        logger.info(f"ProactiveEngine: added task '{label}' (id={task.id}, priority={priority})")
        return task

    def remove_task(self, task_id: str) -> bool:
        """Remove a task from the queue."""
        with self._queue_lock:
            before = len(self._queue)
            self._queue = [t for t in self._queue if t.id != task_id]
            return len(self._queue) < before

    def list_tasks(self) -> List[dict]:
        """List all tasks in the queue."""
        with self._queue_lock:
            return [t.to_dict() for t in self._queue]

    def seed_default_tasks(self) -> None:
        """Add built-in proactive tasks for autonomous behavior."""
        defaults = [
            {
                "task_id": "memory_review",
                "prompt": (
                    "Review your recent conversation memories. Look for patterns, "
                    "contradictions, or things that should be consolidated. If you find "
                    "something worth noting, create a brief insight. Otherwise reply NO_ACTION."
                ),
                "priority": 7,
                "cooldown_minutes": 120,
                "label": "Memory Review",
            },
            {
                "task_id": "follow_up_check",
                "prompt": (
                    "Check your memory for any pending follow-ups, reminders, or tasks "
                    "the user mentioned they'd get back to. If something is overdue or "
                    "coming up, prepare a brief notification. Otherwise reply NO_ACTION."
                ),
                "priority": 3,
                "cooldown_minutes": 60,
                "label": "Follow-Up Check",
            },
            {
                "task_id": "daily_insight",
                "prompt": (
                    "Based on everything you know about the user, generate one brief "
                    "interesting insight, tip, or suggestion that might be helpful today. "
                    "Make it personal and relevant. If nothing comes to mind, reply NO_ACTION."
                ),
                "priority": 8,
                "cooldown_minutes": 480,  # 8 hours
                "label": "Daily Insight",
            },
        ]
        with self._queue_lock:
            existing_ids = {t.id for t in self._queue}
            for d in defaults:
                if d["task_id"] not in existing_ids:
                    self.add_task(**d)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the proactive engine background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            name="echospeak-proactive",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"ProactiveEngine started — interval={self._check_interval}m, "
            f"tasks={len(self._queue)}, channels={self._channels}"
        )

    def stop(self) -> None:
        """Stop the proactive engine."""
        self._stop_event.set()
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("ProactiveEngine stopped")

    @property
    def is_running(self) -> bool:
        return self._running and bool(self._thread) and self._thread.is_alive()

    def get_history(self, limit: int = 20) -> List[dict]:
        """Return recent proactive actions (newest first)."""
        with self._history_lock:
            return list(reversed(self._history))[:limit]

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        """Main proactive loop — checks queue periodically."""
        # Let agent fully initialize
        time.sleep(30)

        while not self._stop_event.is_set():
            try:
                self._process_next_task()
            except Exception as exc:
                logger.warning(f"ProactiveEngine: error — {exc}")

            # Sleep in small increments for responsive stop
            interval_seconds = self._check_interval * 60
            elapsed = 0
            while elapsed < interval_seconds and not self._stop_event.is_set():
                time.sleep(min(10, interval_seconds - elapsed))
                elapsed += 10

    def _process_next_task(self) -> None:
        """Pick the highest-priority due task and execute it."""
        with self._queue_lock:
            due_tasks = [t for t in self._queue if t.is_due()]
            if not due_tasks:
                return
            # Sort by priority (lower number = higher priority)
            due_tasks.sort(key=lambda t: t.priority)
            task = due_tasks[0]

        logger.debug(f"ProactiveEngine: executing task '{task.label}' (id={task.id})")

        request_lock = getattr(self._agent, "_request_lock", None)
        lock_acquired = False
        if request_lock is not None:
            try:
                lock_acquired = bool(request_lock.acquire(blocking=False))
            except TypeError:
                lock_acquired = bool(request_lock.acquire(False))
            if not lock_acquired:
                logger.debug(f"ProactiveEngine: skipped task '{task.label}' because agent is busy")
                return

        try:
            response, _ = self._agent.process_query(
                task.prompt,
                source="proactive",
                thread_id=f"proactive_{task.id}",
            )
        except Exception as exc:
            logger.warning(f"ProactiveEngine: task '{task.label}' query failed — {exc}")
            task.mark_run()
            return
        finally:
            if request_lock is not None and lock_acquired:
                try:
                    request_lock.release()
                except RuntimeError:
                    pass

        response_stripped = (response or "").strip()
        is_silent = (
            not response_stripped
            or _NO_ACTION_SENTINEL in response_stripped.upper()
        )

        # Record in history
        entry = {
            "task_id": task.id,
            "task_label": task.label,
            "response": response_stripped[:300] if response_stripped else "",
            "was_silent": is_silent,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self._history_lock:
            self._history.append(entry)
            if len(self._history) > self._history_max:
                self._history = self._history[-self._history_max:]

        task.mark_run()

        # Remove completed one-shot tasks
        if task.max_runs > 0 and task.run_count >= task.max_runs:
            self.remove_task(task.id)

        if is_silent:
            logger.debug(f"ProactiveEngine: task '{task.label}' — nothing to act on")
            return

        # Route output to notification channels
        logger.info(f"ProactiveEngine: task '{task.label}' produced output — routing to {self._channels}")
        try:
            from agent.heartbeat import route_message
            route_message(response_stripped, self._channels, label=task.label)
        except Exception as exc:
            logger.warning(f"ProactiveEngine: routing failed — {exc}")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_proactive_engine: Optional[ProactiveEngine] = None


def get_proactive_engine() -> Optional[ProactiveEngine]:
    return _proactive_engine


def set_proactive_engine(engine: ProactiveEngine) -> None:
    global _proactive_engine
    _proactive_engine = engine
