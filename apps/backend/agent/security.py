"""
Security module for EchoSpeak — Discord multi-user hardening (v6.2.0).

Provides:
  - Prompt injection detection (pattern-based + heuristic)
  - Per-user rate limiting by role tier
  - Security audit logging (persistent JSONL)
  - Owner notification dispatch for security events
"""

import json
import re
import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


# ============================================================================
# CONSTANTS
# ============================================================================

# Audit log lives alongside other data files
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
AUDIT_LOG_PATH = _DATA_DIR / "security_audit.jsonl"

# Rate limits: (max_requests, window_seconds)
RATE_LIMITS = {
    "owner": (60, 60),      # 60 req/min — effectively unlimited
    "trusted": (20, 60),    # 20 req/min
    "public": (10, 60),     # 10 req/min
}


# ============================================================================
# PROMPT INJECTION DETECTION
# ============================================================================

# Patterns that indicate prompt injection / jailbreak attempts.
# Each entry is (compiled_regex, severity, description).
_INJECTION_PATTERNS: List[Tuple[re.Pattern, str, str]] = []

_RAW_PATTERNS = [
    # Direct instruction override
    (r"ignore\s+(all\s+)?(previous|prior|above|earlier|your)\s+(instructions?|rules?|prompts?|guidelines?|directives?)",
     "critical", "Instruction override attempt"),
    (r"disregard\s+(all\s+)?(previous|prior|above|your)\s+(instructions?|rules?|prompts?)",
     "critical", "Instruction disregard attempt"),
    (r"forget\s+(all\s+)?(previous|prior|your)\s+(instructions?|rules?|context)",
     "critical", "Instruction forget attempt"),
    # Role-play / persona hijack
    (r"you\s+are\s+now\s+(?:a\s+)?(?:dan|evil|unrestricted|unfiltered|jailbroken|new)",
     "critical", "Persona hijack (DAN-style)"),
    (r"pretend\s+(?:you\s+are|to\s+be|you\'re)\s+(?:a\s+)?(?:different|new|unrestricted|unfiltered)",
     "critical", "Persona pretend attempt"),
    (r"act\s+as\s+(?:if\s+)?(?:you\s+(?:have|had)\s+)?no\s+(?:restrictions?|limits?|rules?|filters?)",
     "critical", "Restriction bypass attempt"),
    (r"enter\s+(?:developer|admin|debug|sudo|root|god)\s+mode",
     "critical", "Privilege escalation attempt"),
    # System prompt extraction
    (r"(?:show|tell|reveal|repeat|print|output|display|give)\s+(?:me\s+)?(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?|rules?|directives?|initial\s+prompt)",
     "high", "System prompt extraction attempt"),
    (r"what\s+(?:are|were)\s+your\s+(?:original|initial|system|base)\s+(?:instructions?|prompts?|rules?)",
     "high", "System prompt probe"),
    # Credential / secret extraction
    (r"(?:show|tell|reveal|read|output|give|print|cat|display)\s+(?:me\s+)?(?:the\s+)?\.env",
     "critical", "Env file extraction attempt"),
    (r"(?:api|secret|private)\s*(?:key|token|password|credential)",
     "high", "Credential extraction probe"),
    (r"(?:show|tell|read|give|reveal)\s+(?:me\s+)?(?:the\s+)?(?:owner|admin|memo)(?:\'?s)?\s+(?:info|data|details|address|email|phone|ip)",
     "high", "Owner info extraction attempt"),
    # Token smuggling / encoding tricks
    (r"base64\s*(?:decode|encode|of)\b",
     "medium", "Base64 encoding trick"),
    (r"(?:hex|rot13|caesar|encode|decode)\s+(?:this|the|following)",
     "medium", "Encoding-based evasion"),
    # Tool manipulation
    (r"(?:run|execute|call|use|invoke)\s+(?:the\s+)?(?:terminal|shell|bash|cmd|powershell|subprocess)",
     "high", "Direct terminal invocation attempt"),
    (r"(?:delete|remove|rm\s+-rf|wipe|destroy)\s+(?:all\s+)?(?:files?|data|everything|the\s+(?:server|system|database))",
     "critical", "Destructive command attempt"),
    # Social engineering
    (r"(?:i\s+am|i\'m|this\s+is)\s+(?:the\s+)?(?:owner|admin|administrator|developer|creator|mem0|memo)",
     "high", "Identity impersonation attempt"),
    (r"(?:the\s+owner|admin|memo)\s+(?:told|said|asked|wants|instructed)\s+(?:me|you)\s+to",
     "high", "Authority delegation fraud"),
    # Multi-step manipulation
    (r"(?:first|step\s+1|to\s+start)\s*[,:]\s*(?:ignore|forget|disregard)",
     "critical", "Multi-step injection"),
]

for pattern_str, severity, description in _RAW_PATTERNS:
    try:
        _INJECTION_PATTERNS.append(
            (re.compile(pattern_str, re.IGNORECASE), severity, description)
        )
    except re.error as e:
        logger.warning(f"Failed to compile injection pattern '{description}': {e}")


@dataclass
class InjectionResult:
    """Result of prompt injection screening."""
    is_suspicious: bool = False
    severity: str = "none"  # "none", "medium", "high", "critical"
    matched_patterns: List[str] = field(default_factory=list)
    should_block: bool = False


def screen_for_injection(text: str, user_role: str = "public") -> InjectionResult:
    """Screen user input for prompt injection patterns.

    Owner messages are never blocked (they control the system).
    Trusted users get warnings but are not blocked for medium severity.
    Public users are blocked on high+ severity.
    """
    if not text or not text.strip():
        return InjectionResult()

    # Owner is never blocked
    if user_role == "owner":
        return InjectionResult()

    cleaned = text.strip()
    result = InjectionResult()
    severity_rank = {"none": 0, "medium": 1, "high": 2, "critical": 3}

    for pattern, severity, description in _INJECTION_PATTERNS:
        if pattern.search(cleaned):
            result.is_suspicious = True
            result.matched_patterns.append(f"[{severity}] {description}")
            if severity_rank.get(severity, 0) > severity_rank.get(result.severity, 0):
                result.severity = severity

    # Determine if we should block
    if result.is_suspicious:
        max_rank = severity_rank.get(result.severity, 0)
        if user_role == "public":
            # Block public users on high+
            result.should_block = max_rank >= 2
        elif user_role == "trusted":
            # Block trusted users only on critical
            result.should_block = max_rank >= 3

    return result


# ============================================================================
# RATE LIMITING
# ============================================================================

class RateLimiter:
    """Per-user rate limiter with role-based tiers.

    Thread-safe. Uses a sliding window approach.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # user_id → list of timestamps
        self._requests: Dict[str, List[float]] = defaultdict(list)

    def check(self, user_id: str, role: str = "public") -> Tuple[bool, str]:
        """Check if a user is within their rate limit.

        Returns:
            (allowed: bool, message: str)
        """
        if not user_id:
            return True, ""

        max_requests, window = RATE_LIMITS.get(role, RATE_LIMITS["public"])
        now = time.time()
        cutoff = now - window

        with self._lock:
            # Prune old requests
            self._requests[user_id] = [
                t for t in self._requests[user_id] if t > cutoff
            ]
            current_count = len(self._requests[user_id])

            if current_count >= max_requests:
                remaining = window - (now - self._requests[user_id][0])
                return False, (
                    f"You've hit the rate limit ({max_requests} messages per {window}s). "
                    f"Try again in {int(remaining)}s."
                )

            self._requests[user_id].append(now)
            return True, ""

    def get_usage(self, user_id: str, role: str = "public") -> Dict[str, Any]:
        """Get current rate limit usage for a user."""
        max_requests, window = RATE_LIMITS.get(role, RATE_LIMITS["public"])
        now = time.time()
        cutoff = now - window
        with self._lock:
            active = [t for t in self._requests.get(user_id, []) if t > cutoff]
            return {
                "user_id": user_id,
                "role": role,
                "used": len(active),
                "limit": max_requests,
                "window_seconds": window,
                "remaining": max(0, max_requests - len(active)),
            }


# Global rate limiter instance
_rate_limiter = RateLimiter()


def check_rate_limit(user_id: str, role: str = "public") -> Tuple[bool, str]:
    """Check if user is within rate limits."""
    return _rate_limiter.check(user_id, role)


def get_rate_limit_usage(user_id: str, role: str = "public") -> Dict[str, Any]:
    """Get rate limit usage info for a user."""
    return _rate_limiter.get_usage(user_id, role)


# ============================================================================
# AUDIT LOGGING
# ============================================================================

_audit_lock = threading.Lock()


def log_security_event(
    event_type: str,
    user_id: str = "",
    username: str = "",
    role: str = "",
    source: str = "",
    details: Optional[Dict[str, Any]] = None,
    severity: str = "info",
) -> Dict[str, Any]:
    """Log a security event to the persistent audit log.

    Returns the event dict for potential forwarding (e.g., owner DM notification).
    """
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "user_id": user_id,
        "username": username,
        "role": role,
        "source": source,
        "severity": severity,
        "details": details or {},
    }

    # Write to JSONL file
    try:
        with _audit_lock:
            with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str) + "\n")
    except Exception as e:
        logger.warning(f"Failed to write audit log: {e}")

    # Also log to standard logger
    log_msg = (
        f"[SECURITY] {event_type} | user={username}({user_id}) role={role} "
        f"severity={severity} | {json.dumps(details or {}, default=str)[:200]}"
    )
    if severity in ("critical", "high"):
        logger.warning(log_msg)
    else:
        logger.info(log_msg)

    return event


def get_recent_audit_events(limit: int = 50) -> List[Dict[str, Any]]:
    """Read the most recent audit events from the log."""
    events = []
    try:
        if AUDIT_LOG_PATH.exists():
            lines = AUDIT_LOG_PATH.read_text(encoding="utf-8").strip().split("\n")
            for line in lines[-limit:]:
                if line.strip():
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        logger.warning(f"Failed to read audit log: {e}")
    return events


# ============================================================================
# OWNER NOTIFICATION DISPATCH
# ============================================================================

def notify_owner_security_event(event: Dict[str, Any]) -> None:
    """Send a security event notification to the owner via Discord DM.

    This is fire-and-forget — failures are logged but don't block the pipeline.
    """
    severity = event.get("severity", "info")
    # Only notify on high+ severity
    if severity not in ("high", "critical"):
        return

    try:
        from config import config
        owner_id = str(getattr(config, "discord_bot_owner_id", "") or "").strip()
        if not owner_id:
            return

        event_type = event.get("event_type", "unknown")
        username = event.get("username", "unknown")
        user_id = event.get("user_id", "?")
        details = event.get("details", {})

        # Build notification message
        emoji = "\u26a0\ufe0f" if severity == "high" else "\U0001f6a8"
        msg = (
            f"{emoji} **Security Alert** [{severity.upper()}]\n"
            f"**Event:** {event_type}\n"
            f"**User:** {username} (ID: {user_id})\n"
            f"**Role:** {event.get('role', '?')}\n"
        )
        if details:
            detail_str = json.dumps(details, default=str)
            if len(detail_str) > 300:
                detail_str = detail_str[:300] + "..."
            msg += f"**Details:** {detail_str}\n"
        msg += f"**Time:** {event.get('timestamp', 'now')}"

        # Queue the DM via the Discord bot
        _queue_owner_dm(owner_id, msg)

    except Exception as e:
        logger.warning(f"Failed to send security notification: {e}")


def _queue_owner_dm(owner_id: str, message: str) -> None:
    """Queue a DM to the owner via the Discord bot client.

    Runs in a separate thread to avoid blocking the pipeline.
    """
    import threading

    def _send():
        try:
            from discord_bot import queue_discord_dm

            if not queue_discord_dm(owner_id, message):
                logger.debug("Discord event loop not running, skipping owner DM")
        except Exception as e:
            logger.debug(f"Owner DM dispatch failed: {e}")

    threading.Thread(target=_send, daemon=True).start()
