from agent.active_work import ActiveWorkState
from agent.mode_controller import (
    CodingPhaseName,
    TurnMode,
    allowed_tools_for_mode,
    classify_turn_mode,
)
from agent.mode_executor import execution_profile_for
from agent.web_search_providers import resolve_provider_order


ALL_TOOLS = {
    "web_search",
    "sports_live",
    "get_system_time",
    "calculate",
    "file_list",
    "file_read",
    "file_write",
    "file_mkdir",
    "file_delete",
    "terminal_run",
    "artifact_write",
    "browse_task",
    "project_status",
}


def test_chat_mode_has_no_tools_for_novel_non_build_phrasings():
    examples = [
        "look into your soul and tell me what you notice",
        "can you riff on why this feels weird",
        "tell me a tiny bedtime story about debugging",
        "what do you think about making software less annoying",
        "explain your vibe in one sentence",
    ]
    for text in examples:
        decision = classify_turn_mode(text)
        assert decision.mode == TurnMode.CHAT
        assert allowed_tools_for_mode(decision, ALL_TOOLS) == frozenset()
        profile = execution_profile_for(decision)
        assert profile.executor_name == "chat_executor"
        assert not profile.may_search
        assert not profile.may_write_files
        assert not profile.may_create_projects


def test_live_information_is_research_not_coding():
    decision = classify_turn_mode("which clubs are on the pitch later tonight")
    assert decision.mode == TurnMode.TASK_RESEARCH
    tools = allowed_tools_for_mode(decision, ALL_TOOLS)
    assert "web_search" in tools
    assert "file_write" not in tools
    profile = execution_profile_for(decision)
    assert profile.executor_name == "research_executor"
    assert profile.may_search
    assert not profile.may_write_files
    assert not profile.may_create_projects


def test_new_project_enters_implement_phase_with_confirmation_gated_tools():
    decision = classify_turn_mode("build a tiny invoice tracker app")
    assert decision.mode == TurnMode.CODING
    assert decision.coding_phase == CodingPhaseName.IMPLEMENT
    tools = allowed_tools_for_mode(decision, ALL_TOOLS)
    assert {"file_list", "file_read"} & tools
    assert "file_write" in tools
    assert "terminal_run" in tools
    profile = execution_profile_for(decision)
    assert profile.executor_name == "coding_implement_executor"
    assert profile.may_read_files
    assert profile.may_create_projects
    assert profile.may_write_files


def test_active_project_approval_enters_implement_phase():
    active = ActiveWorkState(
        thread_id="t1",
        kind="coding_project",
        phase="plan",
        project_path="/tmp/projects/todo-app",
        project_name="todo-app",
        goal="make a todo app",
    )
    decision = classify_turn_mode("okay lets make the app", active_work=active)
    assert decision.mode == TurnMode.CODING
    assert decision.coding_phase == CodingPhaseName.IMPLEMENT
    tools = allowed_tools_for_mode(decision, ALL_TOOLS)
    assert "file_write" in tools
    assert "terminal_run" in tools
    profile = execution_profile_for(decision)
    assert profile.executor_name == "coding_implement_executor"
    assert profile.may_write_files


def test_mode_decision_serializes_unified_turn_continuity_fields():
    decision = classify_turn_mode("look up the latest Python release")
    enriched = decision.__class__(
        **{
            **decision.__dict__,
            "objective": "verify the latest Python release",
            "current_subject": "Python releases",
            "continuation_context": "Follow-up to: Python releases",
        }
    )

    payload = enriched.as_dict()

    assert payload["objective"] == "verify the latest Python release"
    assert payload["current_subject"] == "Python releases"
    assert payload["continuation_context"] == "Follow-up to: Python releases"


def test_active_project_continuation_handles_vague_followups():
    active = ActiveWorkState(
        thread_id="t1",
        kind="coding_project",
        phase="plan",
        project_path="/tmp/projects/todo-app",
        project_name="todo-app",
        goal="make a todo app",
    )
    for text in ["sounds good, go ahead", "yes start building it", "okay do it"]:
        decision = classify_turn_mode(text, active_work=active)
        assert decision.mode == TurnMode.CODING
        assert decision.coding_phase == CodingPhaseName.IMPLEMENT


def test_search_only_allowed_in_research_mode():
    chat = classify_turn_mode("tell me what your voice feels like")
    coding = classify_turn_mode("build a small notes app")
    research = classify_turn_mode("look up the latest oilers score")

    assert "web_search" not in allowed_tools_for_mode(chat, ALL_TOOLS)
    assert "web_search" not in allowed_tools_for_mode(coding, ALL_TOOLS)
    assert "web_search" in allowed_tools_for_mode(research, ALL_TOOLS)


def test_research_never_gets_project_or_write_tools():
    decision = classify_turn_mode("research the current Python release notes and compare sources")
    tools = allowed_tools_for_mode(decision, ALL_TOOLS)
    assert decision.mode == TurnMode.TASK_RESEARCH
    assert "file_write" not in tools
    assert "file_mkdir" not in tools
    assert "terminal_run" not in tools
    assert not execution_profile_for(decision).may_create_projects


def test_explicit_research_then_code_keeps_coding_authority_and_adds_read_only_search():
    decision = classify_turn_mode(
        "research the official API docs, then update the project code to use the supported method"
    )
    tools = allowed_tools_for_mode(decision, ALL_TOOLS)

    assert decision.mode == TurnMode.CODING
    assert decision.required_capabilities == frozenset({"coding", "research"})
    assert "web_search" in tools
    assert "file_read" in tools
    assert "file_write" in tools  # execution still pauses at the exact-action approval boundary
    assert execution_profile_for(decision).may_search


def test_explicit_read_only_intent_overrides_stale_implement_phase():
    active = ActiveWorkState(
        thread_id="t1",
        kind="coding_project",
        phase="implement",
        project_path="/tmp/projects/EchoSpeak",
        project_name="EchoSpeak",
        goal="repair runtime",
    )
    cases = {
        "Check this project and understand it": CodingPhaseName.INSPECT,
        "Tell me the current objective and unfinished work": CodingPhaseName.INSPECT,
        "Research the official docs for this project": CodingPhaseName.INSPECT,
        "Inspect, propose a fix, and wait for approval": CodingPhaseName.PLAN,
    }
    for text, phase in cases.items():
        decision = classify_turn_mode(text, active_work=active)
        tools = allowed_tools_for_mode(decision, ALL_TOOLS)
        assert decision.mode == TurnMode.CODING
        assert decision.coding_phase == phase
        assert "file_write" not in tools
        assert "file_delete" not in tools


def test_turn_constraints_are_structured_and_reduce_authority():
    decision = classify_turn_mode(
        "Inspect this project, use primary sources, do not modify or delete anything, and wait for approval"
    )
    tools = allowed_tools_for_mode(decision, ALL_TOOLS)
    assert {"read_only", "primary_sources", "no_delete", "wait_for_approval"} <= set(decision.constraints)
    assert "file_read" in tools
    assert "file_write" not in tools


def test_deep_research_routes_to_research_model():
    decision = classify_turn_mode(
        "deep research the evidence, compare sources, and trace the timeline for the recall"
    )
    assert decision.mode == TurnMode.TASK_RESEARCH
    assert decision.model_name == "mradermacher/Marco-DeepResearch-8B-i1-GGUF"
    assert decision.evidence_required is True


def test_provider_order_excludes_tavily_even_when_legacy_key_exists():
    class Cfg:
        web_search_provider = "auto"
        brave_search_api_key = ""
        tavily_api_key = "tvly-legacy"

    assert "tavily" not in resolve_provider_order(Cfg())
