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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any

from loguru import logger

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

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
    ) -> None:
        self.response = response
        self.timestamp = timestamp
        self.channels = channels
        self.was_silent = was_silent
        self.pulse_context = pulse_context

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {
            "response": self.response,
            "timestamp": self.timestamp,
            "channels": self.channels,
            "was_silent": self.was_silent,
        }
        if self.pulse_context:
            d["pulse_context"] = self.pulse_context
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
                self.next_tick = datetime.now(timezone.utc).isoformat()

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
    # Tick
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        """Fire one heartbeat: gather system state, query the agent, route the result."""
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

        try:
            response_text, _ = self._agent.process_query(
                enriched_prompt,
                source="heartbeat",
                thread_id="heartbeat",
            )
        except Exception as exc:
            logger.warning(f"HeartbeatManager: agent query failed — {exc}")
            return

        # Detect silence sentinel
        response_stripped = (response_text or "").strip()
        is_silent = (
            not response_stripped
            or _NO_HEARTBEAT_SENTINEL in response_stripped.upper()
        )

        result = HeartbeatResult(
            response=response_stripped,
            timestamp=self.last_tick,
            channels=list(self._channels),
            was_silent=is_silent,
            pulse_context=pulse,
        )

        # Store in history regardless (so UI can show "last check: nothing to report")
        with self._history_lock:
            self._history.append(result)
            if len(self._history) > self._history_max:
                self._history = self._history[-self._history_max :]

        if is_silent:
            logger.debug("HeartbeatManager: silent tick — nothing to report")
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

    Used by HeartbeatManager, RoutineManager, and ProactiveEngine to send
    outbound notifications through Discord, Telegram, WhatsApp, email, or web.
    """
    for channel in channels:
        try:
            if channel == "discord":
                _route_discord(text, label=label)
            elif channel == "telegram":
                _route_telegram(text, label=label)
            elif channel == "email":
                _route_email(text, label=label)
            elif channel == "whatsapp":
                _route_whatsapp(text, label=label)
            elif channel == "web":
                # Web channel: caller is responsible for storing/broadcasting
                pass
            else:
                logger.warning(f"route_message: unknown channel '{channel}'")
        except Exception as exc:
            logger.warning(f"route_message: routing to '{channel}' failed — {exc}")


def _route_discord(text: str, label: str = "Notification") -> None:
    """Send a message as a Discord DM, preferring the configured owner."""
    try:
        from config import config
        from discord_bot import queue_discord_dm

        owner_id = str(getattr(config, "discord_bot_owner_id", "") or "").strip()
        allowed = getattr(config, "discord_bot_allowed_users", [])
        target_user_id = owner_id or (str(allowed[0]).strip() if allowed else "")
        if not target_user_id:
            return

        queue_discord_dm(target_user_id, f"🫀 **EchoSpeak {label}**\n{text}")
    except Exception as exc:
        logger.debug(f"route_message: discord route error — {exc}")


def _route_telegram(text: str, label: str = "Notification") -> None:
    """Send a message via Telegram bot."""
    try:
        from telegram_bot import get_telegram_bot
        tg = get_telegram_bot()
        if tg is None:
            return
        tg.send_heartbeat(f"[{label}] {text}")
    except Exception as exc:
        logger.debug(f"route_message: telegram route error — {exc}")


def _route_email(text: str, label: str = "Notification") -> None:
    """Send a message via email."""
    try:
        from config import config
        if not getattr(config, "allow_email", False):
            return
        from agent.skills_registry import get_skills_registry
        registry = get_skills_registry()
        email_tool = registry.get_tool_by_name("email_send")
        if email_tool:
            email_tool.invoke(
                subject=f"🫀 EchoSpeak {label}",
                body=text,
            )
    except Exception as exc:
        logger.debug(f"route_message: email route error — {exc}")


def _route_whatsapp(text: str, label: str = "Notification") -> None:
    """Send a message via WhatsApp."""
    try:
        from config import config
        if not getattr(config, "allow_whatsapp", False):
            return
        from agent.skills_registry import get_skills_registry
        registry = get_skills_registry()
        wa_tool = registry.get_tool_by_name("whatsapp_send")
        if wa_tool:
            wa_tool.invoke(
                message=f"🫀 EchoSpeak {label}\n{text}",
            )
    except Exception as exc:
        logger.debug(f"route_message: whatsapp route error — {exc}")


# ---------------------------------------------------------------------------
# Module-level singleton (created and managed by core.py)
# ---------------------------------------------------------------------------

_heartbeat_manager: Optional[HeartbeatManager] = None


def get_heartbeat_manager() -> Optional[HeartbeatManager]:
    return _heartbeat_manager


def set_heartbeat_manager(manager: HeartbeatManager) -> None:
    global _heartbeat_manager
    _heartbeat_manager = manager
