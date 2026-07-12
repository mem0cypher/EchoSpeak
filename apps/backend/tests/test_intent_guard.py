"""Tests for Bug 1: Workspace Promotion & Coding Intent Guards."""

import pytest
from unittest.mock import MagicMock
from agent.core import EchoSpeakAgent
from agent.router import RoutingDecision


class DummyAgent(EchoSpeakAgent):
    """Subclass of EchoSpeakAgent bypassing network/LLM init for tests."""
    def __init__(self):
        self._workspace_id = "chat"
        self._router = MagicMock()
        # Mocking memory classes to prevent loading actual database files
        self.conversation_memory = MagicMock()
        self.conversation_memory.messages = []


def test_is_coding_project_intent_negatives():
    """Verify that general queries do not trigger coding intent."""
    agent = DummyAgent()
    
    # Non-coding intents
    assert not agent._is_coding_project_intent("when are the fifa matches tomorrow")
    assert not agent._is_coding_project_intent("what is the weather like in Vancouver")
    assert not agent._is_coding_project_intent("can you check my SOUL.md file contents")
    assert not agent._is_coding_project_intent("read the project guidelines document")

    # Real coding intent should still pass
    assert agent._is_coding_project_intent("create a new python game for me")
    assert agent._is_coding_project_intent("scaffold a react todo app")


def test_ensure_workspace_for_intent_respects_router():
    """Verify _ensure_workspace_for_intent doesn't promote workspace for general intents."""
    agent = DummyAgent()
    agent.configure_workspace = MagicMock()

    # Configure router to mock web search intent
    agent._router.route.return_value = RoutingDecision(intent="web_search", tool_name="web_search", tool_args={})
    agent._ensure_workspace_for_intent("when are the fifa matches tomorrow")
    agent.configure_workspace.assert_not_called()

    # Configure router to mock chat intent
    agent._router.route.return_value = RoutingDecision(intent="chat", tool_name=None, tool_args={})
    agent._ensure_workspace_for_intent("weather forecast for tomorrow")
    agent.configure_workspace.assert_not_called()

    # If it's a genuine coding intent, it should trigger configure_workspace
    agent._router.route.return_value = None  # fallback
    agent._ensure_workspace_for_intent("build me a 2d shooter game")
    agent.configure_workspace.assert_called_once_with("coding")


def test_project_materialization_guard_rejects_novel_information_phrasings():
    """Novel non-build phrasings must not be allowed to allocate/scaffold projects."""
    from agent.intent_guard import may_materialize_project, is_explicit_new_project_request

    negatives = [
        "which clubs are on the pitch later tonight",
        "can you explain what your personality file says",
        "look over the setup notes and summarize them",
        "how likely are the oilers to win the stanley cup",
        "show me the current bitcoin price in cad",
    ]
    for phrase in negatives:
        assert not is_explicit_new_project_request(phrase), phrase
        assert not may_materialize_project(phrase), phrase

    assert not may_materialize_project("create a python script that prints hello world")

    assert may_materialize_project("build a habit tracker app")
    assert may_materialize_project("write a small weather dashboard")
