"""
Intent Router for Echo Speak.

Centralizes all intent classification and routing logic that was previously
scattered across core.py.  The router is stateless — it reads the query,
examines available tools, and returns a structured RoutingDecision.

The EchoSpeakAgent delegates to IntentRouter.route() instead of inline
heuristic checks, keeping process_query() clean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RoutingDecision:
    """Result of intent classification and routing."""

    intent: str  # "chat", "discord_read", "discord_send", "web_search",
                 # "tool_call", "time_query", "slash_command", "multi_task",
                 # "pending_confirm", "pending_cancel", "pending_detail"

    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None
    query_stripped: str = ""
    confidence: float = 1.0
    extra: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Intent Router
# ---------------------------------------------------------------------------

class IntentRouter:
    """Stateless intent classifier for incoming user messages.

    Parameters
    ----------
    tools : list
        Legacy ``Tool`` instances (non‑LangChain wrappers).
    lc_tools : list
        LangChain tool instances.
    source : str or None
        Originating client — ``"discord_bot"``, ``"discord_bot_dm"``, or None.
    config : object
        The global ``config`` singleton (read‑only access).
    """

    # -- Shared pattern lists (class-level, immutable) ----------------------

    CONVERSATIONAL_PATTERNS: List[str] = [
        "im going to", "i'm going to", "i am going to",
        "im taking a", "i'm taking a", "i am taking a",
        "im playing", "i'm playing", "i am playing",
        "im watching", "i'm watching", "i am watching",
        "im having", "i'm having", "i am having",
        "sounds good", "have fun", "cool", "nice", "awesome",
        "thanks", "thank you", "ok", "okay", "sure", "yeah", "yes", "nope", "no",
        "hello", "hi", "hey", "how are you", "what's up", "whats up",
        "how's it going", "how is it going", "good morning", "good night",
        "what are you up to", "what're you up to", "whatre you up to",
        "what are you doing", "what're you doing", "whatre you doing", "wyd",
        "see you", "later", "bye", "goodbye",
        "lol", "haha", "lmao", "rofl",
        "brb", "afk", "gtg", "gotta go",
    ]

    TOOL_INTENT_KEYWORDS: List[str] = [
        "search", "look up", "find", "calculate", "read", "write", "open",
        "run", "execute", "send", "post", "announce", "message",
        "what time", "what's the time", "current time", "get time",
        "list files", "show files",
        "weather", "news", "headlines", "remind me", "set reminder",
        "schedule", "calendar", "alarm", "timer",
    ]

    CAPABILITY_PHRASES: List[str] = [
        "is that in ur power", "is that in your power", "can you", "are you able",
        "do you have", "what can you", "your power", "your ability", "are you about to",
        "you about to", "will you be able", "could you be able",
    ]

    QUESTION_SIGNAL_WORDS: List[str] = [
        "?", "what", "how", "when", "where", "who", "which",
        "is there", "show me", "tell me", "find", "search",
        "look up", "check", "get me", "give me",
    ]

    LIVE_WEB_TRIGGERS: List[str] = [
        "right now", "currently", "today", "live",
        "score", "scores", "weather", "forecast",
        "price", "stock", "stocks", "bitcoin", "btc",
        "ethereum", "eth", "exchange rate",
        "flight status", "traffic",
        "is it open", "availability", "released",
        # Sports results / recency triggers
        "last night", "yesterday", "last game",
        "won", "lost", "beat", "defeated",
        "standings", "playoff", "playoffs",
    ]

    DIRECT_TIME_PHRASES: List[str] = [
        "what time is it", "time is it", "current time",
        "what date is it", "what date", "current date",
        "today's date", "todays date", "date today",
    ]

    SCHEDULE_MARKERS: List[str] = [
        "what time does", "start time", "starts at", "kickoff", "tipoff",
        "game", "match", "fixture", "schedule", "event", "concert", "show",
        "flight", "departure", "arrival", "release", "launch",
    ]

    RECAP_PHRASES: List[str] = [
        "what are people saying", "what's everyone saying",
        "what are they saying", "catch me up", "recap",
        "summarize", "read the channel", "talking about",
        "what's being discussed", "whats being discussed",
        "what is being discussed", "going on in",
        "happening in", "latest in",
    ]

    POST_PHRASES: List[str] = [
        "post", "announce", "send in", "say in",
        "send a message in", "message in", "saying that",
    ]

    COMMON_CHANNELS: List[str] = [
        "general", "random", "announcements", "updates", "chat",
        "off-topic", "music", "gaming", "memes", "bot", "testing",
    ]

    ABOUT_TOOLS_PHRASES: List[str] = [
        "discord_read_channel", "discord_send_channel", "discord_web_send",
        "discord_web_read", "discord_contacts", "fuzzy channel matching",
        "added", "fixed", "made fixes", "do you know what", "what did you fix",
    ]

    ACTION_INTENT_WORDS: List[str] = [
        "read", "check", "messages", "last", "send", "say", "sent", "post",
        "what are people", "what's everyone", "catch me up", "recap",
        "summarize", "happening", "what's happening", "going on",
    ]

    # -- Constructor ---------------------------------------------------------

    def __init__(
        self,
        tools: list,
        lc_tools: list,
        source: Optional[str] = None,
        config: Any = None,
    ):
        self.tools = tools or []
        self.lc_tools = lc_tools or []
        self.source = source
        self.config = config
        self.role_blocked_tools: frozenset = frozenset()  # Set by agent per-request

    def _discord_server_assistant_tools(self) -> frozenset[str]:
        return frozenset({"web_search", "get_system_time", "calculate"})

    def _limited_discord_server_tool_names(self, query_lower: str) -> frozenset[str]:
        low = (query_lower or "").strip().lower()
        if not low:
            return frozenset()
        if self.is_small_talk(low):
            return frozenset()
        if self.is_direct_time_question(low):
            return frozenset({"get_system_time"})
        has_calc_keyword = any(ind in low for ind in ["calculate", "compute", "evaluate", "solve", "times", "equals"])
        has_math_operator = bool(re.search(r"\d\s*[+\-*/^]\s*\d", low))
        if has_calc_keyword or has_math_operator:
            return frozenset({"calculate"})
        if self.is_live_web_intent(low):
            return frozenset({"web_search"})
        if any(x in low for x in ["search", "look up", "find out", "news", "headlines", "current events", "weather", "latest"]):
            return frozenset({"web_search"})
        return frozenset()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def route(self, user_input: str) -> RoutingDecision:
        """Single entry point: classify intent and return routing decision.

        The caller handles multi-task planning, pending actions, and slash
        commands *before* calling this method.  ``route()`` focuses on
        tool selection for a single user utterance.
        """
        query = self.extract_user_request_text(user_input)
        query_lower = (query or "").lower().strip()

        if not query_lower:
            return RoutingDecision(intent="chat", query_stripped=query)

        has_tool_intent = self._has_tool_intent_keywords(query_lower)
        has_greeting_preface = any(
            query_lower.startswith(prefix) for prefix in ("hello,", "hello ", "hi,", "hi ", "hey,", "hey ")
        )

        # 1. Capability question → pure chat
        if self.is_capability_question(query_lower) and not (has_greeting_preface and has_tool_intent):
            return RoutingDecision(intent="chat", query_stripped=query)

        # 2. Conversational check (gate for everything below)
        is_conversational = self.is_conversational(query_lower)

        # Pure conversational with no tool keywords → chat
        if is_conversational and not has_tool_intent:
            return RoutingDecision(intent="chat", query_stripped=query)

        # 3. Discord channel intent (highest priority for structured routing)
        dc_intent = self.detect_discord_channel_intent(query)
        dc_kind = dc_intent.get("kind")
        if self.source == "discord_bot" and dc_kind in {"recap", "post"}:
            return RoutingDecision(intent="chat", query_stripped=query, extra=dc_intent)
        if dc_kind == "recap":
            channel = dc_intent.get("channel") or "general"
            return RoutingDecision(
                intent="discord_read",
                tool_name="discord_read_channel",
                tool_args={"channel": channel},
                query_stripped=query,
                extra=dc_intent,
            )
        if dc_kind == "post":
            channel = dc_intent.get("channel")
            message = dc_intent.get("message")
            return RoutingDecision(
                intent="discord_send",
                tool_name="discord_send_channel",
                tool_args={"channel": channel, "message": message},
                query_stripped=query,
                extra=dc_intent,
            )

        # 4. Direct time question → no LLM needed
        if self.is_direct_time_question(query_lower):
            return RoutingDecision(intent="time_query", query_stripped=query)

        # 5. Live web search intent (guarded by question signal)
        if self.is_live_web_intent(query_lower):
            return RoutingDecision(intent="web_search", tool_name="web_search", query_stripped=query)

        # 6. Vision intent
        query_main = self.strip_live_desktop_context(query)
        has_monitor_ctx = "live desktop context" in (user_input or "").lower()
        if self.source != "discord_bot" and self.has_vision_intent(query_main.lower(), has_monitor_ctx=has_monitor_ctx):
            return RoutingDecision(intent="tool_call", tool_name="vision_qa", query_stripped=query)

        # 7. Heuristic tool match via find_tool
        tool_match = self.find_tool(query)
        if tool_match is not None:
            tool_name = tool_match.get("name") if isinstance(tool_match, dict) else getattr(tool_match, "name", None)
            if self.source == "discord_bot" and str(tool_name or "") not in self._discord_server_assistant_tools():
                return RoutingDecision(intent="chat", query_stripped=query)
            return RoutingDecision(
                intent="tool_call",
                tool_name=tool_name,
                query_stripped=query,
            )

        # 8. Default → LLM chat (LangGraph may internally use tool-calling)
        return RoutingDecision(intent="chat", query_stripped=query)

    # -----------------------------------------------------------------------
    # Intent detection methods
    # -----------------------------------------------------------------------

    def is_capability_question(self, query_lower: str) -> bool:
        return any(phrase in query_lower for phrase in self.CAPABILITY_PHRASES)

    def is_small_talk(self, query_lower: str) -> bool:
        q = re.sub(r"\s+", " ", str(query_lower or "").strip().lower())
        if not q:
            return False
        patterns = [
            r"^(?:yo|hi|hey|hello|sup|what(?:'s|s) up|good morning|good night|later|bye|goodbye|cya|gn|night)\s*[!.?]*$",
            r"^what(?:\s+are|\s*'re|\s*re)?\s+you\s+up\s+to(?:\s+today)?\s*[!.?]*$",
            r"^what(?:\s+are|\s*'re|\s*re)?\s+you\s+doing(?:\s+today)?\s*[!.?]*$",
            r"^wyd(?:\s+today)?\s*[!.?]*$",
        ]
        return any(re.fullmatch(pattern, q) is not None for pattern in patterns)

    def has_live_info_subject(self, query_lower: str) -> bool:
        q = re.sub(r"\s+", " ", str(query_lower or "").strip().lower())
        if not q:
            return False
        return any(term in q for term in [
            "weather",
            "forecast",
            "score",
            "scores",
            "price",
            "stock",
            "stocks",
            "bitcoin",
            "btc",
            "ethereum",
            "eth",
            "exchange rate",
            "flight status",
            "traffic",
            "availability",
            "released",
            "is it open",
            "news",
            "headlines",
            "current events",
            "top stories",
            "breaking news",
            "latest news",
            "recent news",
        ])

    def is_conversational(self, query_lower: str) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False
        for pattern in self.CONVERSATIONAL_PATTERNS:
            if " " in pattern or "'" in pattern:
                if pattern in q:
                    return True
                continue
            if re.search(rf"\b{re.escape(pattern)}\b", q):
                return True
        return False

    def _has_tool_intent_keywords(self, query_lower: str) -> bool:
        return any(x in query_lower for x in self.TOOL_INTENT_KEYWORDS)

    def is_direct_time_question(self, query_lower: str) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False
        if not any(p in q for p in self.DIRECT_TIME_PHRASES):
            return False
        if any(m in q for m in self.SCHEDULE_MARKERS):
            return False
        return True

    def is_live_web_intent(self, query_lower: str) -> bool:
        q = re.sub(r"\s+", " ", str(query_lower or "").strip().lower())
        if not q:
            return False
        if self.is_small_talk(q):
            return False
        has_question_signal = any(w in q for w in self.QUESTION_SIGNAL_WORDS)
        if not has_question_signal:
            return False
        triggers = [t for t in self.LIVE_WEB_TRIGGERS if t != "today"]
        if any(t in q for t in triggers):
            return True
        if "today" in q and self.has_live_info_subject(q):
            return True
        if "latest" in q or "breaking" in q:
            return True
        return False

    def needs_time_context(self, query_lower: str) -> bool:
        q = re.sub(r"\s+", " ", str(query_lower or "").strip().lower())
        if not q:
            return False
        if self.is_small_talk(q):
            return False
        if self.is_direct_time_question(q):
            return True
        fast_triggers = [
            "right now", "currently", "today", "tonight", "tomorrow",
            "this week", "this weekend", "this month", "as of",
        ]
        if any(t in q for t in [t for t in fast_triggers if t != "today"]):
            return True
        schedule_terms = [
            "game", "match", "fixture", "schedule", "event", "concert",
            "show", "episode", "season", "flight", "departure", "arrival",
            "release", "launch",
        ]
        if "today" in q and (self.has_live_info_subject(q) or any(term in q for term in schedule_terms)):
            return True
        if any(t in q for t in ["next", "upcoming"]) and any(term in q for term in schedule_terms):
            return True
        if any(t in q for t in ["when is", "when's", "when does", "start time", "starts at", "kickoff", "tipoff"]):
            if any(term in q for term in schedule_terms):
                return True
        return False

    def has_vision_intent(self, query_lower: str, has_monitor_ctx: bool = False) -> bool:
        q = (query_lower or "").strip()
        if not q:
            return False

        file_nouns = ["file", "files", "folder", "folders", "directory", "directories"]
        file_verbs = ["create", "make", "new", "mkdir", "list", "show", "move", "copy", "delete", "remove", "rename"]
        if any(n in q for n in file_nouns) and any(v in q for v in file_verbs):
            return False

        strong_phrases = [
            "what do you see", "what am i looking at", "look at my screen",
            "on my screen", "describe the screen", "describe what's on",
        ]
        if any(p in q for p in strong_phrases):
            return True

        visual_nouns = [
            "video", "clip", "screen", "desktop", "monitor", "window",
            "tab", "page", "image", "picture", "photo", "screenshot",
        ]
        has_visual_noun = any(n in q for n in visual_nouns)

        deictic = ["this", "that", "here", "right here", "there"]
        has_deictic = any(d in q for d in deictic)

        visual_verbs = ["look", "see", "watch", "check", "show", "identify", "describe"]
        has_visual_verb = any(v in q for v in visual_verbs)

        if "check this out" in q and (has_visual_noun or has_monitor_ctx):
            return True
        if "look at this" in q and (has_visual_noun or has_monitor_ctx):
            return True
        if "watch this" in q and ("video" in q or "clip" in q or has_monitor_ctx):
            return True

        if ("what is this" in q or "what's this" in q or "what is that" in q or "what's that" in q) and (
            "video" in q or "clip" in q or "screen" in q or "desktop" in q or has_monitor_ctx
        ):
            return True

        if ("in this video" in q or "in the video" in q or "in this clip" in q or "in the clip" in q) and (
            has_deictic or has_visual_verb
        ):
            return True

        if has_visual_noun and (has_visual_verb or has_deictic):
            return True

        return False

    # -----------------------------------------------------------------------
    # Discord intent detection
    # -----------------------------------------------------------------------

    def detect_discord_channel_intent(self, user_input: str) -> Dict[str, Any]:
        """Single source of truth for Discord *server channel* intent.

        Returns:
            {"kind": "post"|"recap"|None, "channel": str|None, "message": str|None}
        """
        try:
            text = self.strip_live_desktop_context(user_input)
            text = self.extract_user_request_text(text)
            low = (text or "").lower().strip()
            if not low:
                return {"kind": None, "channel": None, "message": None}

            # Avoid false positives when discussing tools.
            if any(p in low for p in [
                "discord_read_channel", "discord_send_channel",
                "discord_web_send", "discord_web_read",
                "discord_contacts", "fuzzy channel matching",
            ]):
                return {"kind": None, "channel": None, "message": None}

            wants_recap = any(p in low for p in self.RECAP_PHRASES)
            wants_post = any(p in low for p in self.POST_PHRASES)

            channel_match = re.search(r"#([a-z0-9_-]{1,80})", low)
            channel = channel_match.group(1) if channel_match else None

            # In Discord DMs, only treat explicit #channel mentions as server-channel intent.
            if channel is None and self.source == "discord_bot_dm":
                return {"kind": None, "channel": None, "message": None}

            if channel is None:
                for ch in self.COMMON_CHANNELS:
                    if re.search(rf"\b{ch}\b", low):
                        channel = ch
                        break

            # If no common channel matched, try extracting an arbitrary channel
            # name from context phrases like "read #development", "send in dev".
            if channel is None:
                ctx_match = re.search(
                    r"\b(?:read|check|send\s+in|post\s+in|say\s+in|message\s+in|announce\s+in)\s+#?([a-z0-9][a-z0-9_-]{0,79})\b",
                    low,
                )
                if ctx_match:
                    channel = ctx_match.group(1)

            if not channel:
                return {"kind": None, "channel": None, "message": None}

            if not wants_post and not wants_recap:
                if re.search(r"\b(read|check|see|show)\b", low):
                    wants_recap = True

            if not wants_post and not wants_recap:
                if re.search(r"\b(search|find|look\s*up|lookup)\b", low):
                    wants_recap = True

            if wants_post:
                # Extract message from the text
                msg = self._extract_post_message(text, channel)
                return {"kind": "post", "channel": channel, "message": msg}

            if wants_recap:
                return {"kind": "recap", "channel": channel, "message": None}

            return {"kind": None, "channel": None, "message": None}
        except Exception:
            return {"kind": None, "channel": None, "message": None}

    def _extract_post_message(self, text: str, channel: str) -> Optional[str]:
        """Best-effort extraction of the message body from a post intent."""
        # Quoted message — try smart quotes first, then straight quotes.
        # Smart double quotes (U+201C/U+201D) — apostrophes inside are OK.
        m = re.search(r'\u201c([^\u201c\u201d]+)\u201d', text)
        if m:
            return m.group(1).strip()
        m = re.search(r'"([^"]+)"', text)
        if m:
            return m.group(1).strip()
        # Smart single quotes (U+2018/U+2019)
        m = re.search(r'\u2018([^\u2018\u2019]+)\u2019', text)
        if m:
            return m.group(1).strip()
        m = re.search(r"'([^']+)'", text)
        if m:
            return m.group(1).strip()
        # "saying <message>"
        m = re.search(r"\bsaying\s+(?:that\s+)?(.+?)(?:\s+please|\s+thank|\s*$)", text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return None

    # -----------------------------------------------------------------------
    # Text preprocessing
    # -----------------------------------------------------------------------

    def extract_user_request_text(self, text: str) -> str:
        """Extract the actual user request from Discord bot wrapped inputs."""
        try:
            raw = (text or "").strip()
            if not raw:
                return raw
            low = raw.lower()
            marker = "user request:"
            idx = low.rfind(marker)
            if idx != -1:
                return (raw[idx + len(marker):] or "").strip()
            if "recent conversation context:" in low and "user:" in low:
                matches = re.findall(r"(?im)^\s*user\s*:\s*(.+?)\s*$", raw)
                if matches:
                    return (matches[-1] or "").strip()
            if "recent conversation context:" in low and "user request:" not in low and "user:" not in low:
                return ""
            return raw
        except Exception:
            return (text or "").strip()

    def strip_live_desktop_context(self, query: str) -> str:
        s = (query or "").strip()
        if not s:
            return ""
        low = s.lower()
        marker = "live desktop context:"
        idx = low.find(marker)
        if idx == -1:
            return s
        return s[:idx].strip()

    # -----------------------------------------------------------------------
    # Tool-set filtering for LangGraph
    # -----------------------------------------------------------------------

    def allowed_tool_names(self, user_input: str) -> frozenset:
        """Determine which tool names the LLM should see for this query.

        Applies several filters:
        1. Conversational suppression (no tools for greetings)
        2. Discord channel intent (only Discord tools)
        3. Source filtering (Discord bot can't use Playwright tools)
        """
        text = self.strip_live_desktop_context(user_input)
        text = self.extract_user_request_text(text)
        low = (text or "").lower().strip()
        if not low:
            return frozenset()

        # Pure conversational → no tools
        is_conv = self.is_conversational(low)
        has_intent = self._has_tool_intent_keywords(low)
        if is_conv and not has_intent:
            return frozenset()

        if self.source == "discord_bot":
            return self._limited_discord_server_tool_names(low)

        # Discord channel intent → only channel tools
        try:
            dc_intent = self.detect_discord_channel_intent(text)
        except Exception:
            dc_intent = {"kind": None}
        if dc_intent and dc_intent.get("kind") in {"post", "recap"}:
            return frozenset({"discord_read_channel", "discord_send_channel"})

        # Start with all available tools
        try:
            all_names = frozenset(
                str(getattr(t, "name", "")).strip()
                for t in (self.lc_tools or [])
                if str(getattr(t, "name", "")).strip()
            )
        except Exception:
            all_names = frozenset()

        # Role-based tool filtering — remove tools blocked for the current user's permission tier
        if self.role_blocked_tools and all_names:
            all_names = frozenset(n for n in all_names if n not in self.role_blocked_tools)

        # Source filtering: Discord bot shouldn't drive Playwright/contacts
        if self.source in {"discord_bot", "discord_bot_dm"} and all_names:
            all_names = frozenset(
                n for n in all_names
                if not (str(n).startswith("discord_web_") or str(n).startswith("discord_contacts_"))
            )
            # Only expose server-channel tools when there's an explicit channel intent
            if not (dc_intent and dc_intent.get("kind") in {"post", "recap"}):
                all_names = frozenset(
                    n for n in all_names
                    if str(n) not in {"discord_send_channel", "discord_read_channel"}
                )

        return all_names

    # -----------------------------------------------------------------------
    # Heuristic tool matching (simplified find_tool)
    # -----------------------------------------------------------------------

    def find_tool(self, query: str) -> Optional[Any]:
        """Lightweight heuristic tool matcher.

        Returns a matching Tool instance if keywords match, else None.
        This is the simplified version — the full ``_find_tool`` in
        ``core.py`` contains the complete indicator map and is delegated
        to by ``EchoSpeakAgent._find_tool`` which calls into us.
        """
        # This method is a thin bridge — the actual find_tool logic remains
        # in core.py's _find_tool() for now, since it depends on self.tools
        # and many tool-specific arg extraction methods.  The router provides
        # the *intent classification* layer above it.
        #
        # In a future iteration, the tool_indicators dict and
        # per-tool arg extraction will move here.
        return None
