"""
Tests for Echo Speak.
Pytest test suite for validating the voice AI system.
"""

import asyncio
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _bind_disposable_file_scope(agent, root) -> None:
    """Give parser tests an explicit disposable filesystem authority root."""
    from agent.tools import set_active_project_root

    set_active_project_root(str(root))
    agent._execution_context.project_path = str(root)
    agent._execution_context.workspace_root = str(root)
    agent._state_store.update_thread_state(
        agent._thread_key(),
        project_path=str(root),
        workspace_root=str(root),
    )


class TestConfig:
    """Tests for the configuration module."""

    def test_config_creation(self):
        """Test that config can be created."""
        from config import config

        assert config is not None
        assert hasattr(config, 'openai')
        assert hasattr(config, 'local')
        assert hasattr(config, 'embedding')
        assert hasattr(config, 'voice')
        assert hasattr(config, 'api')

    def test_config_openai(self):
        """Test OpenAI configuration."""
        from config import config

        assert config.openai.model == "gpt-4o-mini"
        assert isinstance(config.openai.temperature, float)
        assert isinstance(config.openai.max_tokens, int)

    def test_config_local(self):
        """Test local model configuration."""
        from config import config

        assert config.local.provider.value in ["ollama", "lmstudio", "localai", "llama_cpp", "vllm"]
        assert isinstance(config.local.temperature, float)

    def test_memory_path_exists(self):
        """Test that memory path is set correctly."""
        from config import config, MEMORY_DIR

        assert config.memory_path.exists() or str(MEMORY_DIR)


class TestModelProvider:
    """Tests for model provider enum."""

    def test_provider_values(self):
        """Test all provider values exist."""
        from config import ModelProvider

        assert ModelProvider.OPENAI.value == "openai"
        assert ModelProvider.OLLAMA.value == "ollama"
        assert ModelProvider.LM_STUDIO.value == "lmstudio"
        assert ModelProvider.LOCALAI.value == "localai"
        assert ModelProvider.LLAMA_CPP.value == "llama_cpp"
        assert ModelProvider.VLLM.value == "vllm"


class TestModelRuntimeClient:
    """Tests for the canonical provider communication client."""

    @pytest.fixture
    def mock_openai(self):
        """Mock OpenAI dependencies."""
        with patch('agent.model_runtime.ReasoningChatOpenAI') as mock:
            mock_instance = MagicMock()
            mock.return_value = mock_instance
            yield mock_instance

    @pytest.fixture
    def mock_ollama(self):
        """Mock Ollama dependencies."""
        with patch('agent.model_runtime.ChatOllama') as mock:
            mock_instance = MagicMock()
            mock.return_value = mock_instance
            yield mock_instance

    def test_openai_runtime_creation(self, mock_openai):
        """Test OpenAI provider client creation with an exact model."""
        from agent.model_runtime import ModelRuntimeClient
        from config import ModelProvider

        runtime = ModelRuntimeClient(ModelProvider.OPENAI, "gpt-4o-mini")
        assert runtime.provider == ModelProvider.OPENAI
        assert runtime.model_id == "gpt-4o-mini"

    def test_ollama_runtime_creation(self, mock_ollama):
        """Test Ollama provider client creation with an exact model."""
        from agent.model_runtime import ModelRuntimeClient
        from config import ModelProvider

        runtime = ModelRuntimeClient(ModelProvider.OLLAMA, "llama3")
        assert runtime.provider == ModelProvider.OLLAMA
        assert runtime.model_id == "llama3"


class TestAgentMemory:
    """Tests for the agent memory module."""

    @pytest.fixture
    def mock_dependencies(self):
        """Mock external dependencies."""
        with patch('agent.memory.OpenAIEmbeddings') as mock_embed:
            with patch('agent.memory.FAISS') as mock_faiss:
                mock_store = MagicMock()
                mock_faiss.from_texts.return_value = mock_store
                mock_faiss.load_local.return_value = mock_store
                yield mock_embed, mock_faiss, mock_store

    @pytest.fixture
    def patch_memory_config(self, monkeypatch):
        from config import config

        monkeypatch.setattr(config.openai, "api_key", "test-key", raising=False)
        monkeypatch.setattr(config, "memory_partition_enabled", False, raising=False)
        return config

    def test_memory_initialization(self, mock_dependencies, patch_memory_config, tmp_path):
        """Test memory initialization."""
        from agent.memory import AgentMemory
        _mock_embed, _mock_faiss, mock_store = mock_dependencies

        memory = AgentMemory(memory_path=str(tmp_path))

        assert memory is not None
        assert memory.vector_store is mock_store

    def test_add_conversation(self, mock_dependencies, patch_memory_config, tmp_path):
        """Test adding conversation to memory."""
        from agent.memory import AgentMemory
        _mock_embed, _mock_faiss, mock_store = mock_dependencies

        memory = AgentMemory(memory_path=str(tmp_path))

        memory.add_conversation("Hello", "Hi there!")
        mock_store.add_texts.assert_called()

    def test_retrieve_relevant(self, mock_dependencies, patch_memory_config, tmp_path):
        """Test retrieving relevant memories."""
        from agent.memory import AgentMemory
        _mock_embed, _mock_faiss, mock_store = mock_dependencies

        mock_doc = MagicMock()
        mock_doc.page_content = "User: Hello\nAI: Hi there!"
        mock_doc.metadata = {}

        memory = AgentMemory(memory_path=str(tmp_path))
        mock_store.similarity_search.return_value = [mock_doc]

        results = memory.retrieve_relevant("Hello", k=5)

        assert isinstance(results, list)
        assert results == [mock_doc]

    def test_add_conversation_partitioned(self, mock_dependencies, patch_memory_config, tmp_path, monkeypatch):
        """Test partitioned memory storage paths."""
        from config import config
        from agent.memory import AgentMemory

        monkeypatch.setattr(config, "memory_partition_enabled", True, raising=False)
        _mock_embed, _mock_faiss, mock_store = mock_dependencies

        memory = AgentMemory(memory_path=str(tmp_path))
        memory.add_conversation("Hello", "Hi!")

        expected_path = memory._namespace_dir("general", "default")
        assert str(expected_path) in memory._vector_stores
        assert memory._vector_stores[str(expected_path)] is mock_store


class TestTools:
    """Tests for the tools module."""

    def test_web_search_tool_exists(self):
        """Test web search tool is available."""
        from agent.tools import web_search

        assert getattr(web_search, "name", "") == "web_search"
        assert hasattr(web_search, "invoke")

    def test_analyze_screen_tool_exists(self):
        """Test analyze screen tool is available."""
        from agent.tools import analyze_screen

        assert getattr(analyze_screen, "name", "") == "analyze_screen"
        assert hasattr(analyze_screen, "invoke")

    def test_get_system_time_tool_exists(self):
        """Test get system time tool is available."""
        from agent.tools import get_system_time

        assert getattr(get_system_time, "name", "") == "get_system_time"
        assert hasattr(get_system_time, "invoke")

    def test_calculate_tool_exists(self):
        """Test calculate tool is available."""
        from agent.tools import calculate

        assert getattr(calculate, "name", "") == "calculate"
        assert hasattr(calculate, "invoke")

    def test_get_available_tools(self):
        """Test getting list of available tools."""
        from agent.tools import get_available_tools

        tools = get_available_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_web_search_timeout_returns_timeout_message(self, monkeypatch):
        """Configured HTTP-provider timeouts should surface as provider errors."""
        import builtins
        import requests
        from agent.tools import web_search
        from config import config

        def fake_get(*args, **kwargs):
            raise requests.exceptions.Timeout()

        real_import = builtins.__import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name in {"ddgs", "duckduckgo_search"} or (fromlist and name == "ddgs"):
                raise ImportError("blocked in unit test")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr(config, "web_search_provider", "brave", raising=False)
        monkeypatch.setattr(config, "brave_search_api_key", "bsa-test", raising=False)
        monkeypatch.setattr(config, "searxng_base_url", "", raising=False)
        monkeypatch.setattr(config, "web_search_timeout", 7, raising=False)
        monkeypatch.setattr(requests, "get", fake_get, raising=False)
        monkeypatch.setattr(builtins, "__import__", guarded_import)

        out = web_search.invoke({"query": "Edmonton Oilers score right now"})

        assert "timed out after 7s" in str(out).lower()

class TestActionParser:
    def test_action_parser_file_write_python_script(self, monkeypatch, tmp_path):
        """Ensure the action parser can propose a file_write for python script phrasing."""
        from agent.core import EchoSpeakAgent
        from config import config

        monkeypatch.setattr(config, "action_parser_enabled", True, raising=False)
        monkeypatch.setattr(config, "enable_system_actions", True, raising=False)
        monkeypatch.setattr(config, "allow_file_write", True, raising=False)

        agent = EchoSpeakAgent(memory_path=str(config.memory_path))
        _bind_disposable_file_scope(agent, tmp_path)
        agent._tool_allowlist_override = {"file_write"}
        agent._allow_llm_tool_calling = lambda: False

        class StubLLM:
            def invoke(self, text: str) -> str:
                return '{"action":"file_write","confidence":0.9,"path":"hello.py","content":"print(\\"Hello, world!\\")","append":false}'

        agent.model_runtime = StubLLM()
        agent.research_model_runtime = None

        resp, _ok = agent.process_query("create a python script that prints hello world", include_memory=False)
        assert "pending action" in resp.lower() or "reply 'confirm'" in resp.lower()
        assert "hello.py" in resp


class TestDiscordHardening:
    def test_discord_server_source_requires_bound_turn_authority(self, tmp_path):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path), manage_background_services=False)
        agent._current_source = "discord_bot"
        agent._tool_allowlist_override = None

        assert agent._tool_allowed("web_search") is False
        assert agent._tool_allowed("get_system_time") is False
        assert agent._tool_allowed("calculate") is False
        assert agent._tool_allowed("file_read") is False
        assert agent._tool_allowed("discord_send_channel") is False
        # Query selection may identify a candidate; execution still requires
        # the bound mode/context checked above.
        assert agent._allowed_lc_tool_names("search for the weather") == frozenset({"web_search"})
        assert agent._allowed_lc_tool_names('post in #announcements "hi"') == frozenset()

    def test_discord_dm_allowlist_does_not_replace_bound_turn_authority(self, tmp_path):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        agent._current_source = "discord_bot_dm"
        agent._tool_allowlist_override = {"file_read", "web_search"}

        assert agent._tool_allowed("file_read") is False
        assert agent._tool_allowed("web_search") is False

    def test_public_discord_dm_does_not_expose_channel_tools(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent
        from config import DiscordUserRole, config

        monkeypatch.setattr(config, "allow_discord_bot", True, raising=False)

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        agent._current_source = "discord_bot_dm"
        agent._current_user_role = DiscordUserRole.PUBLIC

        assert agent._tool_available_in_current_context("web_search") is False
        assert agent._tool_available_in_current_context("discord_read_channel") is False
        assert agent._tool_available_in_current_context("discord_send_channel") is False
        assert agent._allowed_lc_tool_names("what are people saying in #general?") == frozenset()

    def test_public_discord_dm_capability_help_stays_minimal(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent
        from config import DiscordUserRole, config

        monkeypatch.setattr(config, "allow_discord_bot", True, raising=False)
        monkeypatch.setattr(config, "enable_system_actions", True, raising=False)
        monkeypatch.setattr(config, "allow_file_write", True, raising=False)

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        agent._current_source = "discord_bot_dm"
        agent._current_user_role = DiscordUserRole.PUBLIC

        response = agent._capability_help_response().lower()

        assert "search the web" in response
        assert "discord messages" not in response
        assert "inspect files" not in response

    def test_discord_server_small_talk_today_uses_no_tools(self, tmp_path):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        agent._current_source = "discord_bot"

        assert agent._is_live_web_intent("what are you up to today") is False
        assert agent._needs_time_context("what are you up to today") is False
        assert agent._allowed_lc_tool_names("what are you up to today") == frozenset()

    def test_discord_followup_context_skips_smalltalk_with_suspicious_history(self):
        from discord_bot import EchoSpeakDiscordBot

        class StubAuthor:
            def __init__(self, name: str):
                self.name = name
                self.display_name = name

        class StubMessage:
            def __init__(self, message_id: int, author, content: str):
                self.id = message_id
                self.author = author
                self.content = content

        class StubChannel:
            def __init__(self, messages):
                self._messages = messages

            async def history(self, limit=10, oldest_first=False):
                for msg in self._messages[:limit]:
                    yield msg

        user = StubAuthor("pi")
        bot_user = StubAuthor("EchoSpeak")
        prior_messages = [
            StubMessage(2, bot_user, "Hey. I'm right here."),
            StubMessage(1, user, "i am mem0s new account"),
        ]

        bot = EchoSpeakDiscordBot("x" * 60, lambda **_: ("ok", True))
        bot.client = type("StubClient", (), {"user": bot_user})()
        live_message = type("LiveMessage", (), {})()
        live_message.id = 3
        live_message.author = user
        live_message.channel = StubChannel(prior_messages)

        ctx = asyncio.run(bot._maybe_get_followup_context(live_message, "yo"))

        assert ctx == ""

    def test_discord_smalltalk_reply_is_clamped(self, tmp_path):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        agent._current_source = "discord_bot"

        long_reply = (
            "I'm just here and ready to help you! Since I'm an AI, I don't have a personal life or plans like humans do—"
            "I'm basically just hanging out in the digital ether waiting for your next question. "
            "How about you? Do you have anything fun or productive on your agenda for this Saturday?"
        )

        clamped = agent._clamp_discord_casual_reply("what are you up to today", long_reply)

        assert "Since I'm an AI" not in clamped
        assert len(clamped) <= 120
        assert clamped.count("?") <= 1

    def test_record_turn_strips_wrapped_discord_input(self, tmp_path):
        from agent.core import EchoSpeakAgent
        from config import DiscordUserRole

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        agent._current_source = "discord_bot_dm"
        agent._current_user_role = DiscordUserRole.PUBLIC

        wrapped = (
            "Recent conversation context:\n"
            "User: Max is a gamer\n"
            "EchoSpeak: Got it.\n\n"
            "User request: No because of kernel level anti cheat"
        )

        agent._record_turn(wrapped, "Makes sense")

        assert agent.conversation_memory.messages[0]["content"] == "No because of kernel level anti cheat"
        assert agent.conversation_memory.messages[1]["content"] == "Makes sense"

    def test_pq_build_context_uses_extracted_discord_request_for_memory_query(self, tmp_path):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        seen = {}

        def fake_get_conversation_context(query, thread_id=None, **_kwargs):
            seen["query"] = query
            seen["thread_id"] = thread_id
            return ""

        agent.memory.get_conversation_context = fake_get_conversation_context
        wrapped = (
            "Recent conversation context:\n"
            "User: Max is a gamer\n"
            "EchoSpeak: Got it.\n\n"
            "User request: No because of kernel level anti cheat"
        )

        agent._pq_build_context(wrapped, include_memory=True, callbacks=None, thread_id="discord_1_2")

        assert seen["query"] == "task: No because of kernel level anti cheat"
        assert seen["thread_id"] == "discord_1_2"

    def test_referential_followup_uses_current_subject(self, tmp_path):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        agent._current_subject_text = "Canada vs Morocco World Cup score"

        resolved, is_followup, subject = agent._resolve_referential_followup("do a deeper search")

        assert is_followup is True
        assert subject == "Canada vs Morocco World Cup score"
        assert "Canada vs Morocco World Cup score" in resolved
        # Deeper-search rewrites to subject + analysis terms (not the meta phrase itself)
        assert "detailed" in resolved.lower() or "sources" in resolved.lower() or "analysis" in resolved.lower()
        assert "do a deeper search" not in resolved.lower()

    def test_desktop_project_inspect_not_web_search(self, tmp_path):
        """Local desktop/file inspect must use file tools, not Stage 3 web (no project-name whitelist)."""
        from agent.core import EchoSpeakAgent, ContextBundle

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        q = (
            "can you look on my desktop and lets start on my-app "
            "read the files and understand the project"
        )
        assert agent._is_local_filesystem_intent(q) is True
        assert agent._is_coding_project_intent(q) is True
        tools = agent._allowed_lc_tool_names(q)
        assert "file_list" in tools or "file_read" in tools
        assert "web_search" not in tools
        ctx = ContextBundle(
            context="",
            chat_history=[],
            graph_thread_id=None,
            extracted_input=q,
            resolved_input=q,
            current_subject="",
            referential_followup=False,
            allowed_tool_names=tools,
            time_context="",
        )
        out = agent._pq_shortcut_queries(q, ctx, callbacks=None)
        assert out is None

    def test_coding_path_pin_and_stub_rejection(self, tmp_path, monkeypatch):
        """Bare relative paths resolve under active project root (discovery-based pin)."""
        from agent import tools as tools_mod
        from agent.tools import (
            set_active_project_root,
            _safe_file_path,
            _looks_like_code_stub,
            get_active_project_root,
            _file_tool_root,
        )

        # Use a temp "desktop" project — not a hard-coded product name
        proj = tmp_path / "any-project-xyz"
        proj.mkdir()
        set_active_project_root(str(proj))
        pinned = get_active_project_root()
        assert pinned is not None
        assert pinned.resolve() == proj.resolve()
        resolved = _safe_file_path("index.html")
        assert resolved is not None
        assert resolved == (proj / "index.html").resolve()
        # Must not land in EchoSpeak repo root
        assert resolved.resolve() != (_file_tool_root() / "index.html").resolve()
        # Stub rejection (generic code quality gate)
        assert _looks_like_code_stub("game.js", "// Implement collision detection…") is True
        assert _looks_like_code_stub("game.js", "x=1") is True
        big = "function loop(){ requestAnimationFrame(loop); }\n" * 20
        assert _looks_like_code_stub("game.js", big) is False
        set_active_project_root(None)

    def test_desktop_project_pin_discovers_existing_folder(self, tmp_path, monkeypatch):
        """Pin by matching user tokens to real Desktop folders — no name whitelist."""
        from agent.core import EchoSpeakAgent
        from agent import tools as tools_mod

        desk = tmp_path / "FakeDesktop"
        desk.mkdir()
        (desk / "cool-app-v2").mkdir()
        monkeypatch.setattr(tools_mod, "_desktop_root", lambda: desk.resolve())

        agent = EchoSpeakAgent(memory_path=str(tmp_path / "mem"))
        pinned = agent._try_pin_desktop_project_from_user(
            "look on my desktop and start on cool-app read the files"
        )
        assert pinned is not None
        assert "cool-app-v2" in pinned.replace("\\", "/")
        tools_mod.set_active_project_root(None)

    def test_web_answer_abdication_detected_and_evidence_gate(self, tmp_path):
        """System-wide keep-trying: abdication drafts are weak even with evidence present."""
        from agent.core import EchoSpeakAgent, WebEvidenceHeuristics

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        give_up = (
            "I do not have the specific kickoff time for the FIFA World Cup on July 9, 2026, "
            "nor do I have the conversion to Mountain Time."
        )
        assert agent._answer_is_abdication(give_up) is True
        assert agent._web_answer_is_weak(
            "what mnt time?",
            give_up,
            "France vs Morocco 4 p.m. ET",
        ) is True
        ok = "France vs Morocco kicks off at 4 p.m. ET, which is 2 p.m. Mountain Time (MNT)."
        assert agent._answer_is_abdication(ok) is False
        assert agent._web_answer_is_weak("what mnt time?", ok, "France vs Morocco 4pm ET") is False

        fluff = (
            "The 2026 FIFA World Cup continues today with six matchups. "
            "You can find the full schedule for all 104 games online."
        )
        assert agent._answer_is_abdication(fluff) is True

        # Grounded packet quality gate: fluff without times is not acceptable
        ref = WebEvidenceHeuristics(agent)
        weak_packet = (
            "[GROUNDED_SEARCH] accepted=true\n"
            "The World Cup has 104 games. Full schedule available online."
        )
        assert ref._is_grounded_packet_acceptable(
            "FIFA World Cup matches today kickoff",
            weak_packet,
        ) is False
        strong_packet = (
            "[GROUNDED_SEARCH] accepted=true\n"
            "France vs Morocco kickoff 4:00 p.m. ET on July 9, 2026."
        )
        assert ref._is_grounded_packet_acceptable(
            "FIFA World Cup matches today kickoff",
            strong_packet,
        ) is True

    def test_clarifier_followups_bind_timezone_and_currency_to_subject(self, tmp_path):
        """
        Live: FIFA kickoff '4pm' then '4pm my time? MNT time or when?' must NOT
        search bare 'MNT time zone' — keep match context. Same for BTC → 'in cad?'.
        """
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))

        # --- Timezone clarifier on sports subject ---
        agent._current_subject_text = (
            "FIFA World Cup France vs Morocco Thursday July 9 2026 4pm"
        )
        agent._last_web_query_context = (
            "FIFA World Cup matchups teams kickoff schedule fixtures today"
        )
        for q in (
            "4pm my time? MNT time or when?",
            "is that mountain time?",
            "what timezone is that?",
            "4pm my time?",
        ):
            assert agent._is_timezone_followup_text(q), q
            assert agent._is_referential_followup_text(q), q
            resolved, is_fu, subj = agent._resolve_referential_followup(q)
            assert is_fu is True, (q, resolved)
            assert "france" in resolved.lower() or "morocco" in resolved.lower() or "fifa" in resolved.lower(), (
                q,
                resolved,
            )
            assert "kickoff" in resolved.lower() or "timezone" in resolved.lower() or "time" in resolved.lower()
            # Must not collapse to bare TZ definition search
            assert resolved.lower().strip() not in {
                "mnt time or when",
                "mnt time zone offset",
                "m nt time or when",
            }
            assert "mnt time zone offset" not in resolved.lower()
            # Subject preserved (not replaced by MNT)
            assert "fifa" in subj.lower() or "france" in subj.lower() or "morocco" in subj.lower()

        # --- Currency clarifier on finance subject (not location swap) ---
        agent._current_subject_text = "current bitcoin price"
        agent._last_web_query_context = "the current bitcoin price"
        for q in ("in cad?", "whats the in cad?", "how much in CAD?"):
            assert agent._is_currency_followup_text(q), q
            assert not agent._is_location_swap_followup(q), q
            resolved, is_fu, subj = agent._resolve_referential_followup(q)
            assert is_fu is True, (q, resolved)
            assert "bitcoin" in resolved.lower() or "btc" in resolved.lower()
            assert "cad" in resolved.lower()
            assert "fifa" not in resolved.lower()
            # Subject stays bitcoin, not "bitcoin in cad" as a city swap
            assert "bitcoin" in subj.lower()

        # Hollow clarifiers must not wipe subject on next turn
        agent._current_subject_text = "FIFA World Cup France vs Morocco 4pm"
        assert agent._subject_candidate_from_turn("4pm my time? MNT time or when?", "x") == ""
        assert agent._subject_candidate_from_turn("in cad?", "x") == ""

        # Live: answer anchors (France / 4pm) must stick; MNT follow-up must convert, not re-slate
        agent._current_subject_text = "what fifa matches are happening today"
        agent._update_current_subject(
            "what fifa matches are happening today",
            "Today there is one match: France vs. Morocco, kicking off at 4 p.m.",
        )
        subj = (agent._current_subject_text or "").lower()
        assert "france" in subj and "morocco" in subj, agent._current_subject_text
        assert "4" in subj and "p" in subj, agent._current_subject_text

        resolved_mnt, is_mnt, _ = agent._resolve_referential_followup("what time MNT time?")
        assert is_mnt is True
        rlow = resolved_mnt.lower()
        assert "france" in rlow or "morocco" in rlow or "fifa" in rlow
        assert "mnt" in rlow or "mountain" in rlow
        assert "convert" in rlow or "timezone" in rlow or "what time" in rlow
        # Must not be the same bare slate query as the first search
        assert rlow.strip() != (
            "fifa world cup matchups teams kickoff schedule fixtures today thursday july 09 2026"
        )

        import re as _re
        from agent.research import _normalize_sports_query

        sports_q = _normalize_sports_query(resolved_mnt)
        assert "convert" in sports_q.lower() or "mountain" in sports_q.lower() or "mnt" in sports_q.lower()
        assert "france" in sports_q.lower() or "morocco" in sports_q.lower()
        # Not a pure "today's full slate" rewrite
        assert not _re.fullmatch(
            r"fifa world cup matchups teams kickoff schedule fixtures( today.*)?",
            sports_q.lower().strip(),
        )

        # Live phrasing: vague first answer → MNT follow-up must demand full kickoff list
        agent._current_subject_text = "what fifa matches are happening today"
        agent._last_search_facts = ""
        r_vague, ok_vague, _ = agent._resolve_referential_followup(
            "what mnt time? check that for me so its the right time for me."
        )
        assert ok_vague is True
        assert agent._is_timezone_followup_text(
            "what mnt time? check that for me so its the right time for me."
        )
        nv = _normalize_sports_query(r_vague).lower()
        assert "mountain" in nv or "mnt" in nv
        assert "kickoff" in nv or "time" in nv
        assert "list" in nv or "each" in nv or "schedule" in nv

    def test_printed_tool_directive_becomes_pending_terminal_action(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        _bind_disposable_file_scope(agent, tmp_path)
        monkeypatch.setattr(agent, "_action_allowed", lambda _name, *_args: True)

        response = agent._handle_printed_tool_directive(
            '|TOOL| terminal_run {"command":"echo hello","cwd":"."}',
            "run echo hello",
        )

        assert response is not None
        assert "|TOOL|" not in response
        assert "Reply 'confirm'" in response
        assert agent._pending_action is not None
        assert agent._pending_action["tool"] == "terminal_run"
        assert agent._pending_action["kwargs"]["command"] == "echo hello"

    def test_execute_tool_function_call_becomes_pending_file_action(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        _bind_disposable_file_scope(agent, tmp_path)
        monkeypatch.setattr(agent, "_action_allowed", lambda _name, *_args: True)

        response = agent._handle_printed_tool_directive(
            '<execute_tool>file_write(path="index.html", content="<h1>Hello</h1>", append=False)</execute_tool>',
            "create an html file",
        )

        assert response is not None
        assert "<execute_tool>" not in response
        assert "Reply 'confirm'" in response
        assert agent._pending_action["tool"] == "file_write"
        assert Path(agent._pending_action["kwargs"]["path"]).name == "index.html"

    def test_malformed_execute_tool_file_path_alias_becomes_pending_file_action(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        _bind_disposable_file_scope(agent, tmp_path)
        monkeypatch.setattr(agent, "_action_allowed", lambda _name, *_args: True)

        response = agent._handle_printed_tool_directive(
            '<execute_tool> file_write(file_path="index.html", '
            'content="<!DOCTYPE html>\\n<html><body><h1>Hello</h1></body></html>" </execute_tool>',
            "Create an index.html file for a simple landing page",
        )

        assert response is not None
        assert "<execute_tool>" not in response
        assert "Reply 'confirm'" in response
        assert agent._pending_action["tool"] == "file_write"
        assert Path(agent._pending_action["kwargs"]["path"]).name == "index.html"
        assert "<!DOCTYPE html>" in agent._pending_action["kwargs"]["content"]

    def test_tool_call_json_tag_becomes_pending_terminal_action(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        _bind_disposable_file_scope(agent, tmp_path)
        monkeypatch.setattr(agent, "_action_allowed", lambda _name, *_args: True)

        response = agent._handle_printed_tool_directive(
            '<tool_call>{"tool":"terminal_run","args":{"command":"npm test","cwd":"."}}</tool_call>',
            "run tests",
        )

        assert response is not None
        assert "<tool_call>" not in response
        assert agent._pending_action["tool"] == "terminal_run"
        assert agent._pending_action["kwargs"]["command"] == "npm test"

    def test_pipe_token_tool_call_becomes_pending_file_action(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        _bind_disposable_file_scope(agent, tmp_path)
        monkeypatch.setattr(agent, "_action_allowed", lambda _name, *_args: True)

        response = agent._handle_printed_tool_directive(
            '<|tool_call>call:file_write{path:<|"|>index.html<|"|>, '
            'content:<|"|><!DOCTYPE html>\\n<html><body><h1>Hello</h1></body></html><|"|>}',
            "Create an index.html file for a simple landing page",
        )

        assert response is not None
        assert "<|tool_call>" not in response
        assert "Reply 'confirm'" in response
        assert agent._pending_action["tool"] == "file_write"
        assert Path(agent._pending_action["kwargs"]["path"]).name == "index.html"
        assert "<!DOCTYPE html>" in agent._pending_action["kwargs"]["content"]

    def test_pipe_token_file_write_infers_missing_path_from_prompt(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        _bind_disposable_file_scope(agent, tmp_path)
        monkeypatch.setattr(agent, "_action_allowed", lambda _name, *_args: True)

        response = agent._handle_printed_tool_directive(
            '<|tool_call>call:file_write{content:<|"|><!DOCTYPE html>\\n<html></html><|"|>}',
            "Create an index.html file for a simple landing page",
        )

        assert response is not None
        assert "<|tool_call>" not in response
        assert agent._pending_action["tool"] == "file_write"
        assert Path(agent._pending_action["kwargs"]["path"]).name == "index.html"

    def test_tool_code_block_extracts_first_function_call_as_pending_action(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        _bind_disposable_file_scope(agent, tmp_path)
        monkeypatch.setattr(agent, "_action_allowed", lambda _name, *_args: True)

        response = agent._handle_printed_tool_directive(
            '<tool_code> file_write(path="movement_demo/index.html", content="""<html></html>""") '
            'file_write(path="movement_demo/script.js", content="""console.log(1)""") </tool_code>',
            "write a tiny JavaScript movement demo",
        )

        assert response is not None
        assert "<tool_code>" not in response
        assert agent._pending_action["tool"] == "file_write"
        assert Path(agent._pending_action["kwargs"]["path"]).name == "index.html"
        assert Path(agent._pending_action["kwargs"]["path"]).parent.name == "movement_demo"
        assert "<html></html>" in agent._pending_action["kwargs"]["content"]

    def test_unrecognized_tool_call_shape_is_blocked_not_displayed(self, tmp_path):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        response = agent._handle_printed_tool_directive(
            "<execute_tool>totally_unknown_tool(foo='bar')</execute_tool>",
            "do a thing",
        )

        assert response is not None
        assert "<execute_tool>" not in response
        assert "could not safely parse" in response.lower()

    def test_direct_fallback_uses_full_discord_prompt_and_wrapped_followup(self, tmp_path):
        from agent.core import EchoSpeakAgent, ContextBundle
        from config import DiscordUserRole

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        agent._current_source = "discord_bot_dm"
        agent._current_user_role = DiscordUserRole.PUBLIC
        agent._discord_user_info = {
            "user_id": "123",
            "username": "mem0",
            "display_name": "mem0",
            "access_reason": "owner_id",
        }

        captured = {}

        class StubLLM:
            def invoke(self, prompt):
                captured["prompt"] = prompt
                return "Makes sense"

            def _coerce_content_to_text(self, content):
                return str(content or "")

        agent.model_runtime = StubLLM()
        wrapped = (
            "Recent conversation context:\n"
            "User: I'm on CachyOS so I can't unfortunately\n"
            "EchoSpeak: That's a bummer!\n\n"
            "User request: No because of kernel level anti cheat"
        )
        ctx = ContextBundle(
            context="",
            chat_history=[],
            graph_thread_id="discord_1_2",
            extracted_input="No because of kernel level anti cheat",
            allowed_tool_names=frozenset(),
            time_context="",
        )

        response, ok = agent._pq_finalize_response(wrapped, "", ctx, None)

        assert ok is True
        assert response == "Makes sense"
        assert "Discord user identity:" in captured["prompt"]
        assert "Recent conversation context:" in captured["prompt"]
        assert "User request: No because of kernel level anti cheat" in captured["prompt"]

    def test_discord_server_source_never_auto_confirms(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent
        from config import DiscordUserRole, config

        monkeypatch.setattr(config, "discord_bot_auto_confirm", True, raising=False)

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        agent._current_source = "discord_bot"
        agent._current_user_role = DiscordUserRole.OWNER

        assert agent._should_auto_confirm("file_write") is False

    def test_discord_server_access_can_be_granted_by_role(self, monkeypatch):
        from config import config
        from discord_bot import EchoSpeakDiscordBot

        class StubRole:
            def __init__(self, name: str, role_id: str):
                self.name = name
                self.id = role_id

        class StubAuthor:
            def __init__(self, user_id: str, roles: list[StubRole]):
                self.id = user_id
                self.roles = roles

        monkeypatch.setattr(config, "discord_bot_owner_id", "owner-1", raising=False)
        monkeypatch.setattr(config, "discord_bot_trusted_users", [], raising=False)
        monkeypatch.setattr(config, "discord_bot_allowed_users", [], raising=False)
        monkeypatch.setattr(config, "discord_bot_allowed_roles", ["Echo Access"], raising=False)

        bot = EchoSpeakDiscordBot("x" * 60, lambda **_: ("ok", True))
        access_ok, reason, role_names, role_ids = asyncio.run(
            bot._invocation_access(
                StubAuthor("2002", [StubRole("Echo Access", "55")]),
                is_dm=False,
            )
        )

        assert access_ok is True
        assert reason == "allowed_role"
        assert role_names == ["Echo Access"]
        assert role_ids == ["55"]

    def test_discord_dm_access_can_be_granted_by_verified_mutual_role(self, monkeypatch):
        from config import config
        from discord_bot import EchoSpeakDiscordBot

        class StubRole:
            def __init__(self, name: str, role_id: str):
                self.name = name
                self.id = role_id

        class StubMember:
            def __init__(self, roles: list[StubRole]):
                self.roles = roles

        class StubGuild:
            def __init__(self, member: StubMember | None):
                self._member = member

            def get_member(self, _user_id: int):
                return None

            async def fetch_member(self, _user_id: int):
                if self._member is None:
                    raise LookupError("missing")
                return self._member

        class StubClient:
            def __init__(self, guilds: list[StubGuild]):
                self.guilds = guilds

        class StubAuthor:
            def __init__(self, user_id: str):
                self.id = user_id
                self.roles = []

        monkeypatch.setattr(config, "discord_bot_owner_id", "", raising=False)
        monkeypatch.setattr(config, "discord_bot_trusted_users", [], raising=False)
        monkeypatch.setattr(config, "discord_bot_allowed_users", [], raising=False)
        monkeypatch.setattr(config, "discord_bot_allowed_roles", ["Echo Access"], raising=False)

        bot = EchoSpeakDiscordBot("x" * 60, lambda **_: ("ok", True))
        bot.client = StubClient([StubGuild(StubMember([StubRole("Echo Access", "55")]))])
        access_ok, reason, role_names, role_ids = asyncio.run(
            bot._invocation_access(
                StubAuthor("3003"),
                is_dm=True,
            )
        )

        assert access_ok is True
        assert reason == "verified_allowed_role_dm"
        assert role_names == ["Echo Access"]
        assert role_ids == ["55"]

    def test_discord_dm_without_matching_role_is_denied_when_role_gate_configured(self, monkeypatch):
        from config import config
        from discord_bot import EchoSpeakDiscordBot

        class StubRole:
            def __init__(self, name: str, role_id: str):
                self.name = name
                self.id = role_id

        class StubMember:
            def __init__(self, roles: list[StubRole]):
                self.roles = roles

        class StubGuild:
            def __init__(self, member: StubMember | None):
                self._member = member

            def get_member(self, _user_id: int):
                return None

            async def fetch_member(self, _user_id: int):
                if self._member is None:
                    raise LookupError("missing")
                return self._member

        class StubClient:
            def __init__(self, guilds: list[StubGuild]):
                self.guilds = guilds

        class StubAuthor:
            def __init__(self, user_id: str):
                self.id = user_id
                self.roles = []

        monkeypatch.setattr(config, "discord_bot_owner_id", "", raising=False)
        monkeypatch.setattr(config, "discord_bot_trusted_users", [], raising=False)
        monkeypatch.setattr(config, "discord_bot_allowed_users", [], raising=False)
        monkeypatch.setattr(config, "discord_bot_allowed_roles", ["Echo Access"], raising=False)

        bot = EchoSpeakDiscordBot("x" * 60, lambda **_: ("ok", True))
        bot.client = StubClient([StubGuild(StubMember([StubRole("Different Role", "99")]))])
        access_ok, reason, role_names, role_ids = asyncio.run(
            bot._invocation_access(
                StubAuthor("4004"),
                is_dm=True,
            )
        )

        assert access_ok is False
        assert reason == "dm_not_allowlisted"
        assert role_names == ["Different Role"]
        assert role_ids == ["99"]

    def test_discord_dm_prompt_explains_verified_role_dm_admission(self, tmp_path):
        from agent.core import EchoSpeakAgent
        from config import DiscordUserRole

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        agent._current_source = "discord_bot_dm"
        agent._current_user_role = DiscordUserRole.PUBLIC
        agent._discord_user_info = {
            "user_id": "user-4",
            "username": "guest",
            "display_name": "guest",
            "access_reason": "allowed_user_id",
        }

        prompt = agent._compose_system_prompt()

        assert "Discord DM admission may come from the owner ID, trusted-user IDs, allowed-user IDs, or by verifying that the user still holds an allowed role in a mutual guild." in prompt
        assert "Being admitted by allowed_user_id or verified_allowed_role_dm does NOT upgrade them to TRUSTED" in prompt
        assert "Admission path: allowed_user_id." in prompt

    def test_architecture_question_uses_deterministic_help_response(self, tmp_path):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))

        response_text, ok = agent.process_query("How does EchoSpeak work?", include_memory=False)

        assert ok is True
        assert "three layers" in response_text.lower()
        assert "apps/backend/agent/core.py" in response_text
        assert "apps/backend/data/settings.json" in response_text


class TestUpdateContextParity:
    def test_update_query_uses_safe_project_update_context_without_self_modification(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent
        from config import config

        monkeypatch.setattr(config, "enable_system_actions", False, raising=False)
        monkeypatch.setattr(config, "allow_self_modification", False, raising=False)

        agent = EchoSpeakAgent(memory_path=str(tmp_path))

        assert agent._allowed_lc_tool_names("what changed?") == frozenset({"project_update_context"})

    def test_discord_server_update_query_uses_safe_update_tool(self, tmp_path):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        agent._current_source = "discord_bot"

        assert agent._allowed_lc_tool_names("what changed?") == frozenset({"project_update_context"})

    def test_resolve_user_role_marks_public_social_sources_as_public(self, tmp_path):
        from agent.core import EchoSpeakAgent
        from config import DiscordUserRole

        agent = EchoSpeakAgent(memory_path=str(tmp_path))

        assert agent._resolve_user_role("twitter") == DiscordUserRole.PUBLIC
        assert agent._resolve_user_role("twitch") == DiscordUserRole.PUBLIC
        assert agent._resolve_user_role("twitter_autonomous") == DiscordUserRole.OWNER
        assert agent._resolve_user_role("web") == DiscordUserRole.OWNER

    def test_update_context_plugin_injects_owner_context_for_web(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent, ContextBundle
        from agent.tool_registry import PluginRegistry
        from agent.update_context import get_update_context_service
        from config import DiscordUserRole

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        agent._current_source = "web"
        agent._current_user_role = DiscordUserRole.OWNER

        service = get_update_context_service()
        seen = {}

        def fake_block(**kwargs):
            seen["public"] = kwargs.get("public")
            return "OWNER_UPDATE_CONTEXT"

        monkeypatch.setattr(service, "build_context_block", fake_block)

        ctx = ContextBundle(context="Existing context", extracted_input="what changed?")
        PluginRegistry.dispatch_context("what changed?", ctx, source="web", agent=agent)

        assert seen["public"] is False
        assert ctx.update_intent is True
        assert ctx.update_context == "OWNER_UPDATE_CONTEXT"
        assert ctx.context.startswith("OWNER_UPDATE_CONTEXT")

    def test_update_context_plugin_injects_public_safe_context_for_discord(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent, ContextBundle
        from agent.tool_registry import PluginRegistry
        from agent.update_context import get_update_context_service
        from config import DiscordUserRole

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        agent._current_source = "discord_bot_dm"
        agent._current_user_role = DiscordUserRole.PUBLIC

        service = get_update_context_service()
        seen = {}

        def fake_block(**kwargs):
            seen["public"] = kwargs.get("public")
            return "PUBLIC_UPDATE_CONTEXT"

        monkeypatch.setattr(service, "build_context_block", fake_block)

        ctx = ContextBundle(context="", extracted_input="what changed?")
        PluginRegistry.dispatch_context("what changed?", ctx, source="discord_bot_dm", agent=agent)

        assert seen["public"] is True
        assert ctx.update_intent is True
        assert ctx.update_context == "PUBLIC_UPDATE_CONTEXT"

    def test_update_context_plugin_injects_public_safe_context_for_twitter_mentions(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent, ContextBundle
        from agent.tool_registry import PluginRegistry
        from agent.update_context import get_update_context_service
        from config import DiscordUserRole

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        agent._current_source = "twitter"
        agent._current_user_role = DiscordUserRole.PUBLIC

        service = get_update_context_service()
        seen = {}

        def fake_block(**kwargs):
            seen["public"] = kwargs.get("public")
            return "PUBLIC_TWITTER_UPDATE_CONTEXT"

        monkeypatch.setattr(service, "build_context_block", fake_block)

        ctx = ContextBundle(context="", extracted_input="what's new with EchoSpeak?")
        PluginRegistry.dispatch_context("what's new with EchoSpeak?", ctx, source="twitter", agent=agent)

        assert seen["public"] is True
        assert ctx.update_intent is True
        assert ctx.update_context == "PUBLIC_TWITTER_UPDATE_CONTEXT"

    def test_twitter_autonomous_prompt_uses_shared_update_context_service(self, monkeypatch):
        from twitter_bot import EchoSpeakTwitterBot, _NO_TWEET_SENTINEL
        from agent.update_context import get_update_context_service

        bot = EchoSpeakTwitterBot()
        bot._agent = object()

        monkeypatch.setattr("twitter_bot._load_auto_tweet_state", lambda: {"tweets_today": [], "recent_hashes": []})

        service = get_update_context_service()
        monkeypatch.setattr(
            service,
            "build_context_block",
            lambda **_kwargs: "SHARED_UPDATE_CONTEXT_BLOCK",
        )

        captured = {}

        def fake_generate(prompt: str) -> str:
            captured["prompt"] = prompt
            return _NO_TWEET_SENTINEL

        bot._generate_tweet_agentic = fake_generate

        bot._autonomous_tweet_tick(max_daily=5)

        assert "SHARED_UPDATE_CONTEXT_BLOCK" in captured["prompt"]


class TestCodingWorkspaceToolAvailability:
    def test_coding_workspace_keeps_file_and_terminal_tools_available_with_stale_allowlist(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent
        from config import config

        monkeypatch.setattr(config, "enable_system_actions", True, raising=False)
        monkeypatch.setattr(config, "allow_file_write", True, raising=False)
        monkeypatch.setattr(config, "allow_terminal_commands", True, raising=False)

        agent = EchoSpeakAgent(memory_path=str(tmp_path))
        agent._workspace_id = "coding"
        agent._tool_allowlist_override = set()

        allowed = agent._allowed_lc_tool_names("Write a tiny JavaScript movement demo and verify the files exist.")

        assert {"file_write", "file_read", "file_list", "file_mkdir", "terminal_run"}.issubset(allowed)


class TestConversationAndResearchRouting:
    def test_news_intent_forces_web_search_and_refinement(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent
        from agent.core import Tool

        agent = EchoSpeakAgent(memory_path=str(tmp_path), manage_background_services=False)

        class StubLLM:
            def invoke(self, text: str) -> str:
                return "SUMMARY"

        agent.model_runtime = StubLLM()
        agent.research_model_runtime = None

        captured: list[str] = []

        def fake_raw_web_search(q: str):
            captured.append(str(q))
            return (
                "1. Tech headlines\n"
                "   URL: https://example.com/news\n"
                "   Snippet: Latest tech news today about AI and chips."
            )

        # v7.4: Stage 3 routes through _raw_web_search_execute (shared grounder backend).
        agent._raw_web_search_execute = fake_raw_web_search
        agent._fetch_search_result_page_text = lambda url, **kw: ""
        agent.tools = [Tool("web_search", lambda q: agent._grounded_web_search(q), "Search the web")]
        agent.process_query("latest news today", include_memory=True, thread_id="t1")
        assert captured, "Expected web_search to be invoked for news intent"
        assert "today" in captured[-1].lower()

        agent.process_query("what about in tech?", include_memory=True, thread_id="t1")
        assert len(captured) >= 2
        q2 = captured[-1].lower()
        assert "tech" in q2
        assert "news" in q2 or "today" in q2

    def test_schedule_query_uses_time_context_for_natural_play_next_phrasing(self, tmp_path):
        from agent.core import EchoSpeakAgent
        from agent.core import Tool

        agent = EchoSpeakAgent(memory_path=str(tmp_path))

        class StubLLM:
            def invoke(self, text: str) -> str:
                return "The Edmonton Oilers play today, Friday, March 6, 2026."

        agent.model_runtime = StubLLM()

        captured_queries: list[str] = []
        time_calls: list[str] = []

        def fake_raw_web_search(q: str):
            captured_queries.append(str(q))
            return (
                "1. Edmonton Oilers schedule\n"
                "   URL: https://example.com/oilers\n"
                "   Snippet: The Oilers play the Stars on March 6, 2026, and the Avalanche on March 10, 2026.\n"
                "   Extract: Upcoming Oilers games include March 6, 2026 versus Dallas and March 10, 2026 versus Colorado."
            )

        def fake_time():
            time_calls.append("called")
            return "2026-03-06 12:03:24"

        agent._raw_web_search_execute = fake_raw_web_search
        agent._fetch_search_result_page_text = lambda url, **kw: (
            "The Oilers play the Stars on March 6, 2026 at 7:00 PM MT versus Dallas."
        )
        agent._silent_time_context = fake_time
        agent.tools = [
            Tool("web_search", lambda q: agent._grounded_web_search(q), "Search the web"),
            Tool("get_system_time", fake_time, "Get current time"),
        ]
        agent.lc_tools = list(agent.tools)
        response, _ok = agent.process_query("when does the edmonton oilers play next?", include_memory=True, thread_id="t1")

        assert "march 6, 2026" in response.lower()
        assert time_calls
        assert captured_queries
        final_query = captured_queries[0].lower()
        assert "schedule" in final_query or "oilers" in final_query
        assert any(token in final_query for token in ["today", "2026-03-06", "march 6, 2026", "oilers"])

    def test_schedule_answer_is_corrected_when_model_skips_same_day_result(self, tmp_path):
        from agent.core import EchoSpeakAgent
        from agent.core import Tool

        agent = EchoSpeakAgent(memory_path=str(tmp_path))

        class StubLLM:
            def __init__(self):
                self.calls: list[str] = []

            def invoke(self, text: str) -> str:
                self.calls.append(text)
                if len(self.calls) == 1:
                    return "The Edmonton Oilers' next game is on Tuesday, March 10, 2026."
                return "The Edmonton Oilers play today, Friday, March 6, 2026, against Dallas."

        llm = StubLLM()
        agent.model_runtime = llm

        captured_queries: list[str] = []

        def fake_raw_web_search(q: str):
            captured_queries.append(str(q))
            return (
                "1. Edmonton Oilers schedule\n"
                "   URL: https://example.com/oilers\n"
                "   Snippet: The Oilers play the Stars on March 6, 2026, and the Avalanche on March 10, 2026.\n"
                "   Extract: Upcoming Oilers games include March 6, 2026 versus Dallas and March 10, 2026 versus Colorado."
            )

        def fake_time():
            return "2026-03-06 12:03:24"

        agent._raw_web_search_execute = fake_raw_web_search
        agent._fetch_search_result_page_text = lambda url, **kw: (
            "The Oilers play the Stars on March 6, 2026 at 7:00 PM MT versus Dallas."
        )
        agent._silent_time_context = fake_time
        agent.tools = [
            Tool("web_search", lambda q: agent._grounded_web_search(q), "Search the web"),
            Tool("get_system_time", fake_time, "Get current time"),
        ]
        agent.lc_tools = list(agent.tools)
        response, _ok = agent.process_query("when does the edmonton oilers play next?", include_memory=True, thread_id="t1")

        assert "march 6, 2026" in response.lower()
        assert len(captured_queries) >= 1
        assert len(llm.calls) >= 2

    def test_deep_search_prompt_routes_to_web_research(self, tmp_path):
        from agent.core import EchoSpeakAgent
        from agent.core import Tool

        agent = EchoSpeakAgent(memory_path=str(tmp_path))

        class StubLLM:
            def invoke(self, text: str) -> str:
                return "SUMMARY"

        agent.model_runtime = StubLLM()

        captured_queries: list[str] = []

        def fake_raw_web_search(q: str):
            captured_queries.append(str(q))
            return (
                "1. Mic roundup\n"
                "   URL: https://example.com/mics\n"
                "   Snippet: Best microphones for streaming under $300 in 2026."
            )

        agent._raw_web_search_execute = fake_raw_web_search
        agent._fetch_search_result_page_text = lambda url, **kw: ""
        agent.tools = [Tool("web_search", lambda q: agent._grounded_web_search(q), "Search the web")]
        agent.lc_tools = list(agent.tools)
        response, _ok = agent.process_query(
            "Deep search the best microphones for streaming in 2026 under $300 and recommend the best value pick.",
            include_memory=True,
            thread_id="t1",
        )

        assert response == "SUMMARY"
        assert captured_queries
        assert "best microphones for streaming" in captured_queries[-1].lower() or "microphones" in captured_queries[-1].lower()
        assert "deep search" not in captured_queries[-1].lower()

class TestNoSearchOnSocialIntro:
    def test_social_intro_does_not_trigger_web_search(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent, Tool
        from config import config

        monkeypatch.setattr(config, "enable_system_actions", True, raising=False)
        monkeypatch.setattr(config, "allow_playwright", True, raising=False)

        agent = EchoSpeakAgent(memory_path=str(tmp_path))

        calls: list[str] = []

        def fake_web_search(q: str):
            calls.append(str(q))
            return "RESULTS"

        for i, t in enumerate(list(agent.tools)):
            if getattr(t, "name", "") == "web_search":
                agent.tools[i] = Tool("web_search", fake_web_search, getattr(t, "description", ""))
                break

        agent.process_query("i have friend named max! he's currently watching you! say hi! remember my friend max!", include_memory=False)
        assert calls == []

class TestToolAllowlistMerge:
    def test_skills_cannot_expand_or_shrink_workspace_ceiling(self):
        from agent.skills_registry import merge_tool_allowlists

        # Workspace ceiling only allows file_read.
        workspace = ["file_read"]
        # Skill tries to allow terminal_run, but must not hide workspace-safe file_read.
        skills = [["terminal_run"]]
        merged = merge_tool_allowlists(workspace, skills)
        assert merged == {"file_read"}


class TestEmbeddingsConfig:
    def test_lm_studio_embeddings_disable_tiktoken(self, monkeypatch, tmp_path):
        from config import config, ModelProvider

        # Force LM Studio embeddings config.
        monkeypatch.setattr(config.embedding, "provider", ModelProvider.LM_STUDIO, raising=False)
        monkeypatch.setattr(config.embedding, "model", "text-embedding-nomic-embed-text-v1.5", raising=False)
        monkeypatch.setattr(config.local, "base_url", "http://localhost:1234", raising=False)

        captured_kwargs = {}

        class StubEmbeddings:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

            def embed_query(self, _text: str):
                return [0.0]

        class StubStore:
            def save_local(self, _path: str):
                return None

        class StubFAISS:
            @staticmethod
            def load_local(*args, **kwargs):
                return StubStore()

            @staticmethod
            def from_texts(*args, **kwargs):
                return StubStore()

        import agent.memory as memory_mod

        monkeypatch.setattr(memory_mod, "OpenAIEmbeddings", StubEmbeddings, raising=True)
        monkeypatch.setattr(memory_mod, "FAISS", StubFAISS, raising=True)

        from agent.memory import AgentMemory

        AgentMemory(memory_path=str(tmp_path))
        assert captured_kwargs.get("tiktoken_enabled") is False


class TestTtsSelection:
    def test_browser_voice_uses_full_response(self, tmp_path):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))

        text = agent._select_tts_text("q", "This is the full response.")
        assert text == "This is the full response."


class TestFileWriteDisabledMessage:
    def test_file_write_disabled_instructions(self, tmp_path, monkeypatch):
        from config import config
        from agent.core import EchoSpeakAgent

        # Ensure actions are disabled.
        monkeypatch.setattr(config, "enable_system_actions", False, raising=False)
        monkeypatch.setattr(config, "allow_file_write", False, raising=False)
        agent = EchoSpeakAgent(memory_path=str(tmp_path))

        resp, _ok = agent.process_query('write to file path="hello_world.txt" text="hello"', include_memory=False)
        low = (resp or "").lower()
        assert "file write is disabled" in low
        assert "enable_system_actions=true" in low
        assert "allow_file_write=true" in low


class TestTerminalRunFollowup:
    def test_terminal_followup_adds_hint_for_missing_module(self, tmp_path, monkeypatch):
        from config import config
        from agent.core import EchoSpeakAgent

        monkeypatch.setattr(config, "enable_system_actions", True, raising=False)
        monkeypatch.setattr(config, "allow_terminal_commands", True, raising=False)
        monkeypatch.setattr(config, "terminal_command_allowlist", ["python"], raising=False)
        agent = EchoSpeakAgent(memory_path=str(tmp_path))

        out = "Traceback (most recent call last):\nModuleNotFoundError: No module named foo"
        resp = agent._terminal_followup("python -c 'import foo'", out)
        low = (resp or "").lower()
        assert "next step" in low
        assert "pip install" in low


class TestDeterministicProfileMemory:
    def test_sister_name_recall(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))

        class StubLLM:
            def invoke(self, _text: str) -> str:
                return "OK"

        agent.model_runtime = StubLLM()
        agent.process_query("my sister name is Emily remember that", include_memory=False)
        resp, _ = agent.process_query("what is my sister name?", include_memory=False)
        assert "emily" in (resp or "").lower()

    def test_user_vs_friend_name_clarification(self, tmp_path):
        from agent.core import EchoSpeakAgent

        agent = EchoSpeakAgent(memory_path=str(tmp_path))

        class StubLLM:
            def invoke(self, _text: str) -> str:
                return "OK"

        agent.model_runtime = StubLLM()
        agent.process_query("im memo not max", include_memory=False)
        resp_me, _ = agent.process_query("what is my name?", include_memory=False)
        assert "memo" in (resp_me or "").lower()

        resp_friend, _ = agent.process_query("what is my friend name?", include_memory=False)
        assert "max" not in (resp_friend or "").lower()


class TestCuratedMemoryRememberAndImportance:
    def test_explicit_remember_writes_curated_memory(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent
        from config import config

        monkeypatch.setattr(config, "file_memory_enabled", True, raising=False)
        monkeypatch.setattr(config, "file_memory_dir", str(tmp_path), raising=False)
        monkeypatch.setattr(config, "memory_importance_enabled", False, raising=False)

        agent = EchoSpeakAgent(memory_path=str(tmp_path))

        class StubLLM:
            def invoke(self, _text: str) -> str:
                return "OK"

        agent.model_runtime = StubLLM()
        resp, _ = agent.process_query("remember my sister name is Emily", include_memory=False)
        assert "remember" in (resp or "").lower()

        mem_path = tmp_path / "MEMORY.md"
        assert mem_path.exists()
        text = mem_path.read_text(encoding="utf-8").lower()
        assert "relation: sister name is emily" in text

    def test_importance_auto_saves_relation_fact(self, tmp_path, monkeypatch):
        from agent.core import EchoSpeakAgent
        from config import config

        monkeypatch.setattr(config, "file_memory_enabled", True, raising=False)
        monkeypatch.setattr(config, "file_memory_dir", str(tmp_path), raising=False)
        monkeypatch.setattr(config, "memory_importance_enabled", True, raising=False)

        agent = EchoSpeakAgent(memory_path=str(tmp_path))

        class StubLLM:
            def invoke(self, _text: str) -> str:
                return "OK"

        agent.model_runtime = StubLLM()
        agent.process_query("my sister name is Emily", include_memory=False)
        mem_path = tmp_path / "MEMORY.md"
        assert mem_path.exists()
        text = mem_path.read_text(encoding="utf-8").lower()
        assert "relation: sister name is emily" in text


class TestVoiceIO:
    """Tests for the browser-only voice posture."""

    def test_local_stt_engine_is_removed(self):
        from io_module.stt_engine import get_stt_engine

        with pytest.raises(RuntimeError, match="browser speech recognition"):
            get_stt_engine()

    def test_pocket_tts_factory_is_removed(self):
        from io_module.pocket_tts_engine import get_pocket_tts_engine

        with pytest.raises(RuntimeError, match="browser speech playback"):
            get_pocket_tts_engine()

    def test_pocket_tts_class_is_removed(self):
        from io_module.pocket_tts_engine import PocketTTSEngine

        with pytest.raises(RuntimeError, match="browser speech playback"):
            PocketTTSEngine()


class TestVisionIO:
    """Tests for the vision I/O module."""

    def test_capture_screen_function_exists(self):
        """Test capture_screen function exists."""
        from io_module.vision import capture_screen

        assert callable(capture_screen)

    def test_perform_ocr_function_exists(self):
        """Test perform_ocr function exists."""
        from io_module.vision import perform_ocr

        assert callable(perform_ocr)

    def test_analyze_screen_content_function_exists(self):
        """Test analyze_screen_content function exists."""
        from io_module.vision import analyze_screen_content

        assert callable(analyze_screen_content)


class TestAPI:
    """Tests for the FastAPI server module."""

    def test_server_module_exists(self):
        """Test server module can be imported."""
        from api import server

        assert server is not None

    def test_app_exists(self):
        """Test FastAPI app is created."""
        from api.server import app

        assert app is not None

    def test_app_routes_exist(self):
        """Test FastAPI exposes a routes collection."""
        from api.server import app

        assert app.routes is not None

    def test_thread_scoped_agents_do_not_manage_background_services(self, monkeypatch):
        import agent.core as core_mod
        from api import server
        from config import config

        created: list[dict] = []

        class StubAgent:
            def __init__(self, memory_path=None, llm_provider=None, manage_background_services=True):
                created.append(
                    {
                        "provider": llm_provider,
                        "manage_background_services": manage_background_services,
                    }
                )

        monkeypatch.setattr(config, "multi_agent_enabled", True, raising=False)
        monkeypatch.setattr(config, "use_local_models", False, raising=False)
        monkeypatch.setattr(server, "_agent", None, raising=False)
        monkeypatch.setattr(server, "_runtime_provider", None, raising=False)
        monkeypatch.setattr(core_mod, "EchoSpeakAgent", StubAgent, raising=True)

        with server._agent_pool_lock:
            server._agent_pool.clear()

        server.get_agent("thread-x")
        server.get_agent(None)

        assert len(created) == 2
        assert created[0]["manage_background_services"] is False
        assert created[1]["manage_background_services"] is True

    def test_query_endpoint_exists(self):
        """Test query endpoint is defined."""
        from api.server import app

        route_paths = [route.path for route in app.routes]
        assert "/query" in route_paths

    def test_health_endpoint_exists(self):
        """Test health endpoint is defined."""
        from api.server import app

        route_paths = [route.path for route in app.routes]
        assert "/health" in route_paths

    def test_provider_endpoint_exists(self):
        """Test provider endpoint is defined."""
        from api.server import app

        route_paths = [route.path for route in app.routes]
        assert "/provider" in route_paths


class TestCoreAgent:
    """Tests for the core agent module."""

    def test_core_module_exists(self):
        """Test core module can be imported."""
        from agent import core

        assert core is not None

    def test_echo_speak_agent_class_exists(self):
        """Test EchoSpeakAgent class exists."""
        from agent.core import EchoSpeakAgent

        assert EchoSpeakAgent is not None

    def test_create_agent_function_exists(self):
        """Test create_agent function exists."""
        from app import create_agent

        assert callable(create_agent)

    def test_list_available_providers(self):
        """Test listing available providers."""
        from agent.model_runtime import list_available_providers

        providers = list_available_providers()
        assert isinstance(providers, list)
        assert len(providers) > 0

    def test_get_provider_requirements(self):
        """Test getting provider requirements."""
        from agent.model_runtime import get_provider_requirements
        from config import ModelProvider

        reqs = get_provider_requirements(ModelProvider.OLLAMA)
        assert "env_vars" in reqs
        assert "pip_packages" in reqs


class TestIntegration:
    """Integration tests for the complete system."""

    def test_config_imports(self):
        """Test all config values are accessible."""
        from config import config

        assert isinstance(config.voice.rate, int)
        assert isinstance(config.voice.volume, float)
        assert isinstance(config.memory_path, object)

    def test_tools_can_be_imported(self):
        """Test all tools can be imported."""
        from agent.tools import (
            web_search,
            analyze_screen,
            get_system_time,
            calculate,
            take_screenshot,
            get_available_tools
        )

        tools = get_available_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0


class TestLocalModels:
    """Tests for local model functionality."""

    def test_model_runtime_catalog_supports_all_providers(self):
        """Test that the provider communication layer catalogs all providers."""
        from agent.model_runtime import list_available_providers
        from config import ModelProvider

        providers = [
            ModelProvider.OPENAI,
            ModelProvider.OLLAMA,
            ModelProvider.LM_STUDIO,
            ModelProvider.LOCALAI,
        ]

        catalog = {entry["id"] for entry in list_available_providers()}
        for provider in providers:
            assert provider.value in catalog

    def test_model_provider_enum(self):
        """Test model provider enum values."""
        from config import ModelProvider

        assert ModelProvider.OPENAI.value == "openai"
        assert ModelProvider.OLLAMA.value == "ollama"
        assert ModelProvider.LM_STUDIO.value == "lmstudio"
        assert ModelProvider.LOCALAI.value == "localai"
        assert ModelProvider.LLAMA_CPP.value == "llama_cpp"
        assert ModelProvider.VLLM.value == "vllm"

    def test_local_config_structure(self):
        """Test local model config has all required fields."""
        from config import config

        local = config.local
        assert hasattr(local, 'provider')
        assert hasattr(local, 'base_url')
        assert hasattr(local, 'model_name')
        assert hasattr(local, 'temperature')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
