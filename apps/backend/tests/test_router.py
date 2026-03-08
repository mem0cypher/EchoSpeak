"""Integration tests for the IntentRouter.

Tests cover specific input → output routing paths end-to-end to verify
that the extracted router logic reproduces the same decisions that were
previously buried inside core.py's process_query().

No LLM calls are made; these tests are fast and deterministic.
"""

import pytest
from agent.router import IntentRouter, RoutingDecision


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class FakeTool:
    """Minimal tool stub for routing tests."""

    def __init__(self, name: str):
        self.name = name

    def invoke(self, **kwargs):
        return f"invoked {self.name}"


@pytest.fixture
def make_router():
    """Factory that builds a router with optional source and tools."""
    def _factory(source=None, tool_names=None):
        tool_names = tool_names or [
            "web_search", "discord_read_channel",
            "discord_send_channel", "discord_web_read_recent",
            "discord_web_send", "vision_qa", "get_system_time",
            "calculate", "terminal_run", "file_read", "file_write",
        ]
        tools = [FakeTool(n) for n in tool_names]
        lc_tools = [FakeTool(n) for n in tool_names]
        return IntentRouter(tools=tools, lc_tools=lc_tools, source=source)
    return _factory


# ---------------------------------------------------------------------------
# Conversational detection
# ---------------------------------------------------------------------------

class TestConversationalRouting:
    """Messages that should route to 'chat' (no tools)."""

    @pytest.mark.parametrize("msg", [
        "hello",
        "hey",
        "thanks",
        "lol",
        "im going to play some games",
        "sounds good",
        "brb",
        "goodbye",
        "yeah",
    ])
    def test_conversational_returns_chat(self, make_router, msg):
        router = make_router()
        decision = router.route(msg)
        assert decision.intent == "chat", f"Expected 'chat' for '{msg}', got '{decision.intent}'"

    @pytest.mark.parametrize("msg", [
        "hello, can you search for the latest news?",
        "hey, what time is it?",
    ])
    def test_conversational_with_tool_intent_routes(self, make_router, msg):
        """Conversational greetings with tool keywords should still route."""
        router = make_router()
        decision = router.route(msg)
        assert decision.intent != "chat", f"Expected tool routing for '{msg}', got '{decision.intent}'"


# ---------------------------------------------------------------------------
# Discord channel intent
# ---------------------------------------------------------------------------

class TestDiscordChannelRouting:
    """Discord server channel intents (recap / post)."""

    def test_recap_with_hash_channel(self, make_router):
        router = make_router()
        decision = router.route("what are people saying in #general?")
        assert decision.intent == "discord_read"
        assert decision.tool_name == "discord_read_channel"
        assert decision.tool_args["channel"] == "general"

    def test_recap_common_channel_name(self, make_router):
        router = make_router()
        decision = router.route("catch me up on general")
        assert decision.intent == "discord_read"
        assert decision.tool_args["channel"] == "general"

    def test_post_to_channel(self, make_router):
        router = make_router()
        decision = router.route('post in #announcements "Server maintenance tonight"')
        assert decision.intent == "discord_send"
        assert decision.tool_name == "discord_send_channel"
        assert decision.tool_args["channel"] == "announcements"
        assert decision.tool_args["message"] == "Server maintenance tonight"

    def test_about_tools_does_not_route(self, make_router):
        """Discussing tool names shouldn't trigger Discord routing."""
        router = make_router()
        decision = router.route("I added fuzzy channel matching to discord_send_channel")
        assert decision.intent == "chat"

    def test_dm_source_requires_hash(self, make_router):
        """In Discord DM context, bare channel names shouldn't trigger server routing."""
        router = make_router(source="discord_bot_dm")
        decision = router.route("what's general discussing?")
        # Without #, DM source should NOT route to server channel
        assert decision.intent != "discord_read" or decision.tool_args.get("channel") != "general"

    def test_server_source_channel_intent_stays_chat(self, make_router):
        router = make_router(source="discord_bot")
        decision = router.route("recap #general")
        assert decision.intent == "chat"


# ---------------------------------------------------------------------------
# Live web search
# ---------------------------------------------------------------------------

class TestWebSearchRouting:
    """Live web search intent detection."""

    def test_weather_question(self, make_router):
        router = make_router()
        decision = router.route("what's the weather like today?")
        assert decision.intent == "web_search"

    def test_small_talk_today_stays_chat(self, make_router):
        router = make_router()
        decision = router.route("what are you up to today")
        assert decision.intent == "chat"

    def test_score_question(self, make_router):
        router = make_router()
        decision = router.route("what's the score of the Lakers game right now?")
        assert decision.intent == "web_search"

    def test_conversational_no_false_positive(self, make_router):
        """'right now' in conversational context shouldn't trigger web search."""
        router = make_router()
        decision = router.route("im talking to you right now")
        assert decision.intent == "chat"

    def test_capability_question_no_tool(self, make_router):
        """Capability questions should not trigger web search."""
        router = make_router()
        decision = router.route("can you search for the latest news?")
        assert decision.intent == "chat"


# ---------------------------------------------------------------------------
# Time query
# ---------------------------------------------------------------------------

class TestTimeRouting:
    """Direct time/date questions."""

    def test_what_time_is_it(self, make_router):
        router = make_router()
        decision = router.route("what time is it?")
        assert decision.intent == "time_query"

    def test_current_date(self, make_router):
        router = make_router()
        decision = router.route("what's the date today?")
        # This could be either "time_query" or "web_search" depending on priority
        assert decision.intent in ("time_query", "web_search")

    def test_schedule_question_not_time(self, make_router):
        """'What time does the game start' is a schedule Q, not a direct time Q."""
        router = make_router()
        decision = router.route("what time does the game start?")
        assert decision.intent != "time_query"


# ---------------------------------------------------------------------------
# Tool-set filtering
# ---------------------------------------------------------------------------

class TestToolSetFiltering:
    """allowed_tool_names returns correct subsets."""

    def test_conversational_no_tools(self, make_router):
        router = make_router()
        names = router.allowed_tool_names("hey what's up")
        assert names == frozenset()

    def test_discord_intent_limits_tools(self, make_router):
        router = make_router()
        names = router.allowed_tool_names("recap #general")
        assert names == frozenset({"discord_read_channel", "discord_send_channel"})

    def test_discord_bot_source_filters_playwright(self, make_router):
        router = make_router(source="discord_bot")
        names = router.allowed_tool_names("search for the weather")
        assert names == frozenset({"web_search"})
        assert not any(n.startswith("discord_web_") for n in names)

    def test_discord_bot_no_channel_tools_without_intent(self, make_router):
        router = make_router(source="discord_bot")
        names = router.allowed_tool_names("search for the weather")
        assert "discord_send_channel" not in names
        assert "discord_read_channel" not in names

    def test_discord_bot_small_talk_today_has_no_tools(self, make_router):
        router = make_router(source="discord_bot")
        names = router.allowed_tool_names("what are you up to today")
        assert names == frozenset()

    def test_discord_bot_explicit_channel_post_has_no_tools(self, make_router):
        router = make_router(source="discord_bot")
        names = router.allowed_tool_names('post in #announcements "hi"')
        assert names == frozenset()


# ---------------------------------------------------------------------------
# Text preprocessing
# ---------------------------------------------------------------------------

class TestTextPreprocessing:
    """extract_user_request_text and strip_live_desktop_context."""

    def test_extract_from_user_request_marker(self, make_router):
        router = make_router()
        text = "Recent conversation context:\nSome context\n\nUser request: what time is it"
        assert router.extract_user_request_text(text) == "what time is it"

    def test_extract_from_user_message_line(self, make_router):
        router = make_router()
        text = "Recent conversation context:\nUser: hello\nEchoSpeak: hi\nUser: what's up"
        assert router.extract_user_request_text(text) == "what's up"

    def test_strip_desktop_context(self, make_router):
        router = make_router()
        text = "search for the weather Live desktop context: some OCR text"
        assert router.strip_live_desktop_context(text) == "search for the weather"

    def test_plain_text_unchanged(self, make_router):
        router = make_router()
        text = "hello there"
        assert router.extract_user_request_text(text) == "hello there"


# ---------------------------------------------------------------------------
# Vision intent
# ---------------------------------------------------------------------------

class TestVisionRouting:
    """Vision/screen analysis intent."""

    def test_look_at_screen(self, make_router):
        router = make_router()
        decision = router.route("what do you see on my screen?")
        assert decision.intent == "tool_call"
        assert decision.tool_name == "vision_qa"

    def test_file_operation_not_vision(self, make_router):
        """'Show files' should NOT trigger vision."""
        router = make_router()
        assert not router.has_vision_intent("show files in the directory")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases and empty inputs."""

    def test_empty_string(self, make_router):
        router = make_router()
        decision = router.route("")
        assert decision.intent == "chat"

    def test_none_like_input(self, make_router):
        router = make_router()
        decision = router.route("   ")
        assert decision.intent == "chat"

    def test_capability_question(self, make_router):
        router = make_router()
        decision = router.route("can you read my Discord messages?")
        assert decision.intent == "chat"
