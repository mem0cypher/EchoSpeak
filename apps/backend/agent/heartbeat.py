"""
Heartbeat module for EchoSpeak — System Pulse.

Runs a background loop that wakes every N minutes, gathers real system
state (todos, git activity, twitter queue, time context), and calls
process_query() with an enriched prompt so Echo can make informed
decisions about what to report.

Architecture mirrors RoutineManager._scheduler_thread — same daemon thread
pattern, same _on_run callback system.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any

from loguru import logger
from config import DATA_DIR

_DATA_DIR = Path(DATA_DIR)

# Sentinel returned by the agent when there's nothing to report.
_NO_HEARTBEAT_SENTINEL = "NO_HEARTBEAT"


class HeartbeatResult:
    """Result from a heartbeat tick."""

    def __init__(
        self,
        response: str,
        timestamp: str,
        channels: List[str],
        was_silent: bool = False,
        pulse_context: str = "",
        task_id: str = "",
        status: str = "",
    ) -> None:
        self.response = response
        self.timestamp = timestamp
        self.channels = channels
        self.was_silent = was_silent
        self.pulse_context = pulse_context
        self.task_id = task_id
        self.status = status

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {
            "response": self.response,
            "timestamp": self.timestamp,
            "channels": self.channels,
            "was_silent": self.was_silent,
        }
        if self.pulse_context:
            d["pulse_context"] = self.pulse_context
        if self.task_id:
            d["task_id"] = self.task_id
        if self.status:
            d["status"] = self.status
        return d


class HeartbeatManager:
    """
    Manages the proactive heartbeat loop.

    Usage:
        manager = HeartbeatManager(agent=echo_agent)
        manager.start()          # Non-blocking — runs in daemon thread
        manager.stop()           # Signals thread to exit cleanly
    """

    def __init__(
        self,
        agent: Any,
        interval_minutes: Optional[int] = None,
        prompt: Optional[str] = None,
        channels: Optional[List[str]] = None,
        on_result: Optional[Callable[[HeartbeatResult], None]] = None,
        cron_expression: Optional[str] = None,
        project_id: str = "",
        session_id: str = "",
    ) -> None:
        """
        Args:
            agent:            EchoSpeakAgent instance.
            interval_minutes: Minutes between heartbeat ticks. Reads from
                              config if None.
            prompt:           The check-in prompt. Reads from config if None.
            channels:         Output channels list. Reads from config if None.
            on_result:        Optional callback for each non-silent heartbeat.
                              Called with HeartbeatResult from the tick thread.
            cron_expression:  Optional cron syntax (e.g. "0 9 * * 1-5" for weekdays at 9am).
                              When set, overrides interval_minutes.
        """
        from config import config

        self._agent = agent
        self._interval_minutes = interval_minutes or getattr(config, "heartbeat_interval", 30)
        self._cron_expression = cron_expression or getattr(config, "heartbeat_cron", None)
        self._prompt = prompt or getattr(
            config,
            "heartbeat_prompt",
            "Check if there is anything proactive you should report, remind, or "
            "act on right now. If there is nothing relevant, reply with NO_HEARTBEAT.",
        )
        self._channels = channels or list(getattr(config, "heartbeat_channels", ["web"]))
        self._project_id = str(project_id or getattr(config, "heartbeat_project_id", "") or "").strip()
        self._session_id = str(session_id or getattr(config, "heartbeat_session_id", "") or "").strip()
        self._on_result = on_result

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Ring buffer of recent heartbeat results (last 50)
        self._history: List[HeartbeatResult] = []
        self._history_lock = threading.Lock()
        self._history_max = 50

        # Last tick timestamp (ISO UTC)
        self.last_tick: Optional[str] = None
        self.next_tick: Optional[str] = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_agent(self, agent: Any) -> None:
        """Rebind the heartbeat loop to a new agent instance."""
        self._agent = agent

    def start(self) -> None:
        """Start the heartbeat background thread."""
        if self._thread and self._thread.is_alive():
            logger.debug("HeartbeatManager: already running, ignoring start()")
            return

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            name="echospeak-heartbeat",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"HeartbeatManager started — interval={self._interval_minutes}m, "
            f"channels={self._channels}"
        )

    def stop(self) -> None:
        """Signal the heartbeat thread to stop cleanly."""
        self._stop_event.set()
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("HeartbeatManager stopped")

    @property
    def is_running(self) -> bool:
        return self._running and bool(self._thread) and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Configuration (hot-updateable at runtime)
    # ------------------------------------------------------------------

    def update_config(
        self,
        interval_minutes: Optional[int] = None,
        prompt: Optional[str] = None,
        channels: Optional[List[str]] = None,
        cron_expression: Optional[str] = None,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        """Update heartbeat parameters without restarting the thread."""
        if interval_minutes is not None:
            self._interval_minutes = interval_minutes
        if prompt is not None:
            self._prompt = prompt
        if channels is not None:
            self._channels = list(channels)
        if cron_expression is not None:
            self._cron_expression = cron_expression if cron_expression else None
        if project_id is not None:
            self._project_id = str(project_id).strip()
        if session_id is not None:
            self._session_id = str(session_id).strip()
        logger.info(
            f"HeartbeatManager config updated: interval={self._interval_minutes}m, "
            f"cron={self._cron_expression or 'none'}, channels={self._channels}"
        )

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_history(self, limit: int = 20) -> List[dict]:
        """Return recent heartbeat results (newest first)."""
        with self._history_lock:
            results = list(reversed(self._history))
        return [r.to_dict() for r in results[:limit]]

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    def _get_sleep_seconds(self) -> float:
        """Calculate seconds to sleep before the next tick.

        Uses croniter if a cron expression is set, otherwise falls back
        to the fixed interval_minutes.
        """
        if self._cron_expression:
            try:
                from croniter import croniter
                cron = croniter(self._cron_expression, datetime.now(timezone.utc))
                next_dt = cron.get_next(datetime)
                delta = (next_dt - datetime.now(timezone.utc)).total_seconds()
                self.next_tick = next_dt.isoformat()
                return max(10, delta)  # min 10s to avoid tight loops
            except Exception as exc:
                logger.warning(f"HeartbeatManager: cron parse error ({self._cron_expression}), "
                               f"falling back to interval: {exc}")
        return self._interval_minutes * 60

    def _loop(self) -> None:
        """Main heartbeat loop — sleeps between ticks, wakes to check in."""
        # Small sleep at start to let agent fully initialize
        time.sleep(10)

        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.warning(f"HeartbeatManager: tick error — {exc}")

            # Calculate sleep duration (cron or fixed interval)
            interval_seconds = self._get_sleep_seconds()
            if not self._cron_expression:
                self.next_tick = (datetime.now(timezone.utc) + timedelta(seconds=interval_seconds)).isoformat()

            # Sleep in small increments so stop() is responsive
            elapsed = 0
            while elapsed < interval_seconds and not self._stop_event.is_set():
                time.sleep(min(10, interval_seconds - elapsed))
                elapsed += 10

    # ------------------------------------------------------------------
    # System pulse — deterministic context gathering
    # ------------------------------------------------------------------

    def _gather_system_pulse(self) -> str:
        """Gather real system state to enrich the heartbeat prompt.

        Reads from data files and git log to build a factual snapshot
        of what's happening in EchoSpeak right now. Each section is
        best-effort — failures are silently skipped.
        """
        sections: List[str] = []

        # -- Current time --
        now = datetime.now(timezone.utc)
        try:
            import locale
            local_now = datetime.now()
            sections.append(f"Current time: {local_now.strftime('%A, %B %d %Y %I:%M %p')} (local) / {now.strftime('%H:%M UTC')}")
        except Exception:
            sections.append(f"Current time: {now.strftime('%A, %B %d %Y %H:%M UTC')}")

        # -- Todos --
        try:
            todos_path = _DATA_DIR / "todos.json"
            if todos_path.exists():
                todos_data = json.loads(todos_path.read_text(encoding="utf-8"))
                todos = todos_data.get("todos", []) if isinstance(todos_data, dict) else []
                if todos:
                    pending = [t for t in todos if t.get("status") == "pending"]
                    in_progress = [t for t in todos if t.get("status") == "in_progress"]
                    done = [t for t in todos if t.get("status") == "done"]
                    high_priority = [t for t in (pending + in_progress) if t.get("priority") == "high"]
                    todo_lines = [f"Todos: {len(todos)} total ({len(pending)} pending, {len(in_progress)} in progress, {len(done)} done)"]
                    if high_priority:
                        todo_lines.append(f"  High priority: {', '.join(t.get('title', '?')[:60] for t in high_priority[:3])}")
                    if in_progress:
                        todo_lines.append(f"  Active: {', '.join(t.get('title', '?')[:60] for t in in_progress[:3])}")
                    sections.append("\n".join(todo_lines))
                else:
                    sections.append("Todos: none")
        except Exception:
            pass

        # -- Git activity --
        try:
            from agent.git_changelog import get_recent_commits
            commits = get_recent_commits(limit=5)
            if commits:
                commit_lines = [f"Recent git activity: {len(commits)} recent commits"]
                for c in commits[:3]:
                    commit_lines.append(f"  - {c.get('short_sha', '?')} {c.get('message', '?')[:80]}")
                sections.append("\n".join(commit_lines))
            else:
                sections.append("Git activity: no recent commits")
        except Exception:
            pass

        # -- Twitter autonomous state --
        try:
            from config import config as _cfg
            if getattr(_cfg, "allow_twitter", False) and getattr(_cfg, "twitter_autonomous_enabled", False):
                tw_state_path = _DATA_DIR / "twitter_auto_tweet_state.json"
                if tw_state_path.exists():
                    tw_state = json.loads(tw_state_path.read_text(encoding="utf-8"))
                    pending = tw_state.get("pending_approval")
                    tweets_today = tw_state.get("tweets_today", [])
                    tw_lines = [f"Twitter: {len(tweets_today)} tweets posted today"]
                    if pending and isinstance(pending, dict):
                        tw_lines.append(f"  Pending approval: \"{pending.get('text', '?')[:80]}...\"")
                    sections.append("\n".join(tw_lines))
        except Exception:
            pass

        # -- Spotify (if playing) --
        try:
            from config import config as _cfg
            if getattr(_cfg, "allow_spotify", False):
                sp_state_path = _DATA_DIR / "spotify_state.json"
                if sp_state_path.exists():
                    sp = json.loads(sp_state_path.read_text(encoding="utf-8"))
                    if sp.get("is_playing"):
                        track = sp.get("track_name", "Unknown")
                        artist = sp.get("artist_name", "Unknown")
                        sections.append(f"Spotify: Playing \"{track}\" by {artist}")
        except Exception:
            pass

        # -- Backend uptime --
        try:
            import os
            pid = os.getpid()
            sections.append(f"Backend: running (PID {pid})")
        except Exception:
            pass

        if not sections:
            return ""
        return "\n".join(sections)

    # ------------------------------------------------------------------
    # Response sanitization
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_response(text: str) -> str:
        """Strip plan noise, confirmation prompts, and tool chatter from the LLM response.

        The LLM is called without tools, but it may still hallucinate
        tool-call syntax, plan bullet lists, or confirmation prompts
        from its training data.  This method strips all of that so
        only the clean message text reaches Discord / Telegram / etc.
        """
        import re

        if not text:
            return ""

        out = text.strip()

        # Remove "Plan:" / "Planned action:" blocks (bullet lists that follow)
        out = re.sub(
            r"(?:^|\n)\s*(?:Plan|Planned action|Actions?|Steps?):\s*\n(?:\s*[-*•]\s*.+\n?)+",
            "\n", out, flags=re.IGNORECASE,
        )

        # Remove "I can do this: …" / "Reply 'confirm' …" confirmation prompts
        out = re.sub(
            r"(?:I can do this|I'll do this|Shall I|Want me to|Reply\s+['\"]?confirm).*$",
            "", out, flags=re.IGNORECASE | re.MULTILINE,
        )

        # Remove "Post to Discord channel #…" action lines
        out = re.sub(
            r"(?:^|\n)\s*Post to (?:Discord|Telegram|email|WhatsApp).*$",
            "", out, flags=re.IGNORECASE | re.MULTILINE,
        )

        # Collapse multiple blank lines
        out = re.sub(r"\n{3,}", "\n\n", out)

        return out.strip()

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """Materialize one Product Task and execute one governed heartbeat Turn."""
        now = datetime.now(timezone.utc)
        self.last_tick = now.isoformat()
        logger.debug(f"HeartbeatManager: tick at {self.last_tick}")

        # Gather real system state
        pulse = ""
        try:
            pulse = self._gather_system_pulse()
        except Exception as exc:
            logger.debug(f"HeartbeatManager: pulse gather error — {exc}")

        # Build enriched prompt with system context
        if pulse:
            enriched_prompt = (
                f"=== SYSTEM PULSE ===\n{pulse}\n=== END PULSE ===\n\n"
                f"{self._prompt}"
            )
        else:
            enriched_prompt = self._prompt

        # Stable task identity is followed by the normal, freshly validated
        # Turn/Approval/ToolRun authority path.
        from agent.automation_runtime import (
            AutomationModelBinding,
            AutomationRunStatus,
            ModelBindingPolicy,
            get_automation_run_store,
        )
        from agent.projects import get_project_manager
        from agent.state import get_state_store
        from agent.task_store import get_task_store

        project_id = str(self._project_id or "").strip()
        session_id = str(self._session_id or "").strip()
        state_store = get_state_store()
        session_state = state_store.get_thread_state(session_id) if session_id else None
        if (
            not project_id
            or not session_id
            or get_project_manager().get_project(project_id) is None
            or session_state is None
            or str(session_state.active_project_id or "") != project_id
        ):
            logger.error("Heartbeat blocked: configured Project/Session scope is missing or stale")
            return

        interval_seconds = max(60, int(self._interval_minutes) * 60)
        schedule_bucket = int(now.timestamp()) // interval_seconds
        task_store = get_task_store()
        task = task_store.create(
            title="Heartbeat check-in",
            description=self._prompt,
            objective=self._prompt,
            project_id=project_id,
            session_id=session_id,
            source="heartbeat",
            source_id="heartbeat",
            scheduled_for=str(schedule_bucket),
            idempotency_key=f"heartbeat:{project_id}:{session_id}:{schedule_bucket}",
        )
        if task.status in {"complete", "done"}:
            logger.debug("HeartbeatManager: stable schedule bucket already completed")
            return
        run_store = get_automation_run_store()
        run = run_store.create_run(
            idempotency_key=f"heartbeat:{project_id}:{session_id}:{schedule_bucket}",
            project_id=project_id,
            session_id=session_id,
            task_id=task.id,
            trigger_id=str(schedule_bucket),
            source="heartbeat",
            source_id="heartbeat",
            objective=self._prompt,
            model_binding=AutomationModelBinding(
                policy=ModelBindingPolicy.SESSION_DEFAULT,
                source_session_id=session_id,
            ),
        )
        if run.status == AutomationRunStatus.COMPLETED:
            return
        claimed = run_store.claim(
            run.id,
            project_id=project_id,
            session_id=session_id,
            claimant_id="heartbeat-coordinator",
            expected_revision=run.revision,
            lease_seconds=300,
        )
        if claimed is None or claimed.lease is None:
            logger.debug("Heartbeat occurrence already claimed or no longer queued")
            return
        lease_token = claimed.lease.token
        provider = str(self._agent.llm_provider.value)
        model_id = str(self._agent.provider_info.get("model") or "default")
        run_store.bind_model(
            run.id,
            AutomationModelBinding(
                policy=ModelBindingPolicy.SESSION_DEFAULT,
                source_session_id=session_id,
                resolved_provider=provider,
                resolved_model_id=model_id,
            ),
            project_id=project_id,
            session_id=session_id,
            claimant_id="heartbeat-coordinator",
            lease_token=lease_token,
        )
        run_store.transition(
            run.id,
            AutomationRunStatus.RUNNING,
            project_id=project_id,
            session_id=session_id,
            claimant_id="heartbeat-coordinator",
            lease_token=lease_token,
        )
        task_store.update(
            task.id,
            status="in_progress",
            automation_run_ids=list(dict.fromkeys([*task.automation_run_ids, run.id])),
        )

        try:
            response_text, success = self._agent.process_query(
                enriched_prompt,
                include_memory=False,
                callbacks=[],
                thread_id=session_id,
                source="heartbeat",
            )
        except Exception as exc:
            try:
                run_store.transition(
                    run.id,
                    AutomationRunStatus.FAILED,
                    project_id=project_id,
                    session_id=session_id,
                    claimant_id="heartbeat-coordinator",
                    lease_token=lease_token,
                    error=str(exc),
                )
            except Exception as transition_exc:
                logger.error("Heartbeat Run failure transition failed: {}", transition_exc)
            task_store.update(
                task.id,
                status="failed",
                verification={"verified": False, "error": str(exc)},
            )
            logger.warning(f"HeartbeatManager: governed Turn failed — {exc}")
            return

        # Sanitize: strip plan noise, confirmation prompts, tool output
        response_stripped = self._sanitize_response(response_text)

        # Detect silence sentinel
        is_silent = (
            not response_stripped
            or _NO_HEARTBEAT_SENTINEL in response_stripped.upper()
        )

        blocked_channels = (
            [str(channel).lower() for channel in self._channels if str(channel).lower() != "web"]
            if not is_silent
            else []
        )
        verified = bool(success and (is_silent or response_stripped) and not blocked_channels)
        status = "complete" if verified else "needs_permission" if blocked_channels else "failed"
        state = state_store.get_thread_state(session_id)
        execution_id = str(state.last_execution_id or state.current_execution_id or "")
        tool_run_ids = [item.id for item in state_store.list_tool_runs(execution_id)] if execution_id else []
        approval_ids = [str(state.pending_approval_id)] if state.pending_approval_id else []
        if verified:
            run_status = AutomationRunStatus.COMPLETED
        elif state.pending_approval_id:
            run_status = AutomationRunStatus.WAITING_FOR_APPROVAL
        elif blocked_channels:
            run_status = AutomationRunStatus.BLOCKED
        else:
            run_status = AutomationRunStatus.FAILED
        run_store.transition(
            run.id,
            run_status,
            project_id=project_id,
            session_id=session_id,
            claimant_id="heartbeat-coordinator",
            lease_token=lease_token,
            execution_id=execution_id,
            tool_run_ids=tool_run_ids,
            approval_ids=approval_ids,
            outcome={
                "verified": verified,
                "silent": is_silent,
                "response_present": bool(response_stripped),
                "blocked_delivery_channels": blocked_channels,
            },
            error="" if verified else "Heartbeat Turn did not reach a verified terminal outcome",
        )
        task_store.update(
            task.id,
            status=status,
            execution_ids=[execution_id] if execution_id else [],
            tool_run_ids=tool_run_ids,
            approval_ids=approval_ids,
            verification={
                "verified": verified,
                "silent": is_silent,
                "response_present": bool(response_stripped),
                "blocked_delivery_channels": blocked_channels,
                "reason": (
                    "External heartbeat delivery requires a governed communication ToolRun"
                    if blocked_channels
                    else "Heartbeat Turn completed"
                    if verified
                    else "Heartbeat Turn failed"
                ),
            },
        )

        result = HeartbeatResult(
            response=response_stripped,
            timestamp=self.last_tick,
            channels=list(self._channels),
            was_silent=is_silent,
            pulse_context=pulse,
            task_id=task.id,
            status=status,
        )

        # Store in history regardless (so UI can show "last check: nothing to report")
        with self._history_lock:
            self._history.append(result)
            if len(self._history) > self._history_max:
                self._history = self._history[-self._history_max :]

        if is_silent:
            logger.debug("HeartbeatManager: silent tick — nothing to report")
            return

        if blocked_channels:
            # External delivery is an action, not a callback side effect. It
            # must be requested and completed through the governed Turn,
            # Approval, ToolRun, and current-authority boundary.
            logger.info(
                "HeartbeatManager: external delivery blocked pending governed ToolRun: {}",
                blocked_channels,
            )
            return

        logger.info(f"HeartbeatManager: active tick — routing to {self._channels}")

        # Route to channels
        self._route(result)

        # Fire callback if registered
        if self._on_result:
            try:
                self._on_result(result)
            except Exception as exc:
                logger.warning(f"HeartbeatManager: on_result callback error — {exc}")

    def _route(self, result: HeartbeatResult) -> None:
        """Route a non-silent heartbeat result to configured channels."""
        route_message(result.response, result.channels, label="Heartbeat")


# ---------------------------------------------------------------------------
# Shared channel routing — usable by heartbeat, routines, proactive engine
# ---------------------------------------------------------------------------

def route_message(
    text: str,
    channels: List[str],
    label: str = "Notification",
) -> None:
    """Route a message to one or more output channels.

    The web projection is side-effect free. External delivery must be prepared
    and executed as a governed communication ToolRun by the agent pipeline.
    """
    for channel in channels:
        try:
            if channel == "web":
                # Web channel: caller is responsible for storing/broadcasting
                pass
            elif channel in {"discord", "telegram", "email", "whatsapp"}:
                logger.warning(
                    "route_message: blocked background %s delivery for %s; "
                    "prepare it through a Turn/Approval/ToolRun instead",
                    channel,
                    label,
                )
            else:
                logger.warning(f"route_message: unknown channel '{channel}'")
        except Exception as exc:
            logger.warning(f"route_message: routing to '{channel}' failed — {exc}")


# ---------------------------------------------------------------------------
# Module-level singleton (created and managed by core.py)
# ---------------------------------------------------------------------------

_heartbeat_manager: Optional[HeartbeatManager] = None


def get_heartbeat_manager() -> Optional[HeartbeatManager]:
    return _heartbeat_manager


def set_heartbeat_manager(manager: HeartbeatManager) -> None:
    global _heartbeat_manager
    _heartbeat_manager = manager
