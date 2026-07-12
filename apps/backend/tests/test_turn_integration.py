from types import SimpleNamespace

from agent.core import EchoSpeakAgent
from agent.mode_controller import ModeDecision, TurnMode


def _agent_with_tools(*names: str) -> EchoSpeakAgent:
    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent.tools = [SimpleNamespace(name=name) for name in names]
    agent.lc_tools = list(agent.tools)
    agent._tool_allowlist_override = None
    agent._current_source = "web"
    agent._current_mode_decision = ModeDecision(
        mode=TurnMode.CHAT,
        confidence=1.0,
        reason="capability question",
        user_text="what can you do",
        allowed_tool_names=frozenset(),
    )
    agent._is_tool_role_blocked = lambda _name: False
    agent._tool_policy_flags_satisfied = lambda _name: True
    return agent


def test_capability_help_checks_registered_tools_outside_chat_turn_mask():
    agent = _agent_with_tools("web_search", "file_read", "file_write")

    response = agent._capability_help_response()

    assert "search the web" in response
    assert "inspect files" in response


def test_capability_help_does_not_claim_tools_that_are_not_registered():
    agent = _agent_with_tools("calculate")

    response = agent._capability_help_response()

    assert "search the web" not in response
    assert "inspect files" not in response


def test_executor_miss_is_not_reported_as_unsupported_capability():
    agent = _agent_with_tools("file_read")
    agent._current_mode_decision = ModeDecision(
        mode=TurnMode.CODING,
        confidence=1.0,
        reason="inspect project",
        user_text="read the project file",
        allowed_tool_names=frozenset({"file_read"}),
    )
    agent._partial_tool_results = []

    response = agent._ensure_capability_claim_honesty(
        "read the project file",
        "I can't access files.",
    )

    assert "execution failure" in response
    assert "unsupported capability" in response


def test_durable_session_subject_restores_research_followup_after_agent_restart():
    agent = _agent_with_tools("web_search")
    agent._current_thread_id = "thread-weather"
    agent._current_subject_text = ""
    agent._load_active_work = lambda: None
    agent._session_memory = SimpleNamespace(
        load=lambda _thread: SimpleNamespace(current_subject="Edmonton weather")
    )

    decision = agent._bind_turn_mode("what about Calgary?", source="web")

    assert decision.mode == TurnMode.TASK_RESEARCH
    assert decision.current_subject == "Edmonton weather"
    assert decision.continuation_context == "Follow-up to: Edmonton weather"
    assert "web_search" in decision.allowed_tool_names
