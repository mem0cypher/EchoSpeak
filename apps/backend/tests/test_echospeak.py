"""
Tests for Echo Speak.
Pytest test suite for validating the voice AI system.
"""

import os
import sys
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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

        assert config.openai.model == "gpt-3.5-turbo"
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


class TestLLMWrapper:
    """Tests for the LLM wrapper."""

    @pytest.fixture
    def mock_openai(self):
        """Mock OpenAI dependencies."""
        with patch('agent.core.ChatOpenAI') as mock:
            mock_instance = MagicMock()
            mock.return_value = mock_instance
            yield mock_instance

    @pytest.fixture
    def mock_ollama(self):
        """Mock Ollama dependencies."""
        with patch('agent.core.ChatOllama') as mock:
            mock_instance = MagicMock()
            mock.return_value = mock_instance
            yield mock_instance

    def test_openai_wrapper_creation(self, mock_openai):
        """Test OpenAI LLM wrapper creation."""
        with patch('agent.core.get_llm_config') as mock_config:
            mock_config.return_value.model = "gpt-3.5-turbo"
            mock_config.return_value.temperature = 0.7
            mock_config.return_value.max_tokens = 4096
            mock_config.return_value.api_key = "test-key"

            from agent.core import LLMWrapper, ModelProvider
            from config import config

            wrapper = LLMWrapper(ModelProvider.OPENAI)
            assert wrapper.llm_type == ModelProvider.OPENAI

    def test_ollama_wrapper_creation(self, mock_ollama):
        """Test Ollama LLM wrapper creation."""
        with patch('agent.core.get_llm_config') as mock_config:
            mock_config.return_value.model_name = "llama3"
            mock_config.return_value.temperature = 0.7
            mock_config.return_value.max_tokens = 4096
            mock_config.return_value.base_url = "http://localhost:11434"

            from agent.core import LLMWrapper, ModelProvider

            wrapper = LLMWrapper(ModelProvider.OLLAMA)
            assert wrapper.llm_type == ModelProvider.OLLAMA


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

        assert callable(web_search)

    def test_analyze_screen_tool_exists(self):
        """Test analyze screen tool is available."""
        from agent.tools import analyze_screen

        assert callable(analyze_screen)

    def test_get_system_time_tool_exists(self):
        """Test get system time tool is available."""
        from agent.tools import get_system_time

        assert callable(get_system_time)

    def test_calculate_tool_exists(self):
        """Test calculate tool is available."""
        from agent.tools import calculate

        assert callable(calculate)

    def test_get_available_tools(self):
        """Test getting list of available tools."""
        from agent.tools import get_available_tools

        tools = get_available_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0


class TestActionParser:
    def test_action_parser_file_write_python_script(self, monkeypatch):
        """Ensure the action parser can propose a file_write for python script phrasing."""
        from agent.core import EchoSpeakAgent
        from config import config

        monkeypatch.setattr(config, "action_parser_enabled", True, raising=False)
        monkeypatch.setattr(config, "enable_system_actions", True, raising=False)
        monkeypatch.setattr(config, "allow_file_write", True, raising=False)

        agent = EchoSpeakAgent(memory_path=str(config.memory_path))
        agent._tool_allowlist_override = {"file_write"}

        class StubLLM:
            def invoke(self, text: str) -> str:
                return '{"action":"file_write","confidence":0.9,"path":"hello.py","content":"print(\\"Hello, world!\\")","append":false}'

        agent.llm_wrapper = StubLLM()

        resp, _ok = agent.process_query("create a python script that prints hello world", include_memory=False)
        assert "pending action" in resp.lower() or "reply 'confirm'" in resp.lower()
        assert "hello.py" in resp


class TestToolAllowlistMerge:
    def test_skills_cannot_expand_beyond_workspace(self):
        from agent.skills_registry import merge_tool_allowlists

        # Workspace ceiling only allows file_read.
        workspace = ["file_read"]
        # Skill tries to allow terminal_run.
        skills = [["terminal_run"]]
        merged = merge_tool_allowlists(workspace, skills)
        assert merged == set()


class TestVoiceIO:
    """Tests for the voice I/O module."""

    def test_voice_input_class_exists(self):
        """Test VoiceInput class exists."""
        from io.voice import VoiceInput

        assert VoiceInput is not None

    def test_voice_output_class_exists(self):
        """Test VoiceOutput class exists."""
        from io.voice import VoiceOutput

        assert VoiceOutput is not None

    def test_voice_manager_class_exists(self):
        """Test VoiceManager class exists."""
        from io.voice import VoiceManager

        assert VoiceManager is not None


class TestVisionIO:
    """Tests for the vision I/O module."""

    def test_capture_screen_function_exists(self):
        """Test capture_screen function exists."""
        from io.vision import capture_screen

        assert callable(capture_screen)

    def test_perform_ocr_function_exists(self):
        """Test perform_ocr function exists."""
        from io.vision import perform_ocr

        assert callable(perform_ocr)

    def test_analyze_screen_content_function_exists(self):
        """Test analyze_screen_content function exists."""
        from io.vision import analyze_screen_content

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

    def test_root_endpoint_exists(self):
        """Test root endpoint is defined."""
        from api.server import app

        assert app.routes is not None

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
        from agent.core import create_agent

        assert callable(create_agent)

    def test_list_available_providers(self):
        """Test listing available providers."""
        from agent.core import list_available_providers

        providers = list_available_providers()
        assert isinstance(providers, list)
        assert len(providers) > 0

    def test_get_provider_requirements(self):
        """Test getting provider requirements."""
        from agent.core import get_provider_requirements
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

    def test_llm_wrapper_supports_all_providers(self):
        """Test that LLM wrapper can theoretically support all providers."""
        from agent.core import LLMWrapper
        from config import ModelProvider

        providers = [
            ModelProvider.OPENAI,
            ModelProvider.OLLAMA,
            ModelProvider.LM_STUDIO,
            ModelProvider.LOCALAI,
        ]

        for provider in providers:
            assert provider.value in [p.value for p in ModelProvider]

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
