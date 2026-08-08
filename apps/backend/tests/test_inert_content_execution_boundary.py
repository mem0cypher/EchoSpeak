from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.mode_controller import TurnMode
from agent.semantic_runtime import TurnModeDeriver
from agent.skill_contract import (
    SkillExecutionStatus,
    SkillManifest,
    SkillOrigin,
    SkillSelectionOutcome,
    SkillStatus,
)
from agent.skill_execution import activate_skill_execution, create_skill_execution
from agent.skill_selection import select_skill
from agent.skills_registry import SkillsRegistry
from agent.state import StateStore
from agent.tool_registry import ToolRegistry
from agent.turn_understanding import (
    TurnInterpretation,
    TurnRelation,
    is_inert_conversational_content_request,
    scope_interpretation_to_current_instruction,
)


def _interpretation(*, capabilities: list[str], operation: str) -> TurnInterpretation:
    return TurnInterpretation(
        relation=TurnRelation.NEW_TASK,
        proposed_objective="Handle the latest request",
        requested_capabilities=capabilities,
        requested_operation=operation,
        confidence=0.95,
    )


@pytest.mark.parametrize(
    "message",
    [
        "Create a list:\n1. Git full update\n2. Delete old files\n3. Email Sarah",
        'Add "send an email to Sarah" to the list.',
        "Add “send an email to Sarah” to the list.",
        'Add "delete unused files" to that list.',
        "Add print my resume to the list.",
        "Add email Sarah to the list.",
        "Draft the email for Sarah.",
        "Make a grocery list.",
        "Add another item.",
        "Add this.",
        "Change number 2.",
        "Remove the last one.",
        "Reorder these.",
    ],
)
def test_command_shaped_content_is_inert(message: str) -> None:
    assert is_inert_conversational_content_request(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Send that email now.",
        "Save that grocery list to groceries.txt.",
        "Run the Git update.",
        "Draft the email in Gmail.",
        "Create a list and save it to groceries.txt.",
        "Add email Sarah to the list and send it now.",
    ],
)
def test_explicit_external_promotion_is_not_inert(message: str) -> None:
    assert is_inert_conversational_content_request(message) is False


def test_inert_email_list_is_scoped_to_answer_only() -> None:
    original = _interpretation(capabilities=["communications"], operation="email_send")
    scoped = scope_interpretation_to_current_instruction(
        original,
        "Create a list:\n1. Email Sarah\n2. Send the report",
    )
    assert scoped.requested_capabilities == ["conversation"]
    assert scoped.requested_operation == "compose_response"
    assert "response_only_content" in scoped.constraints

    mode = TurnModeDeriver.derive(
        scoped,
        None,
        provider="test",
        model_id="test-model",
        available_tools=set(ToolRegistry.get_names()),
    )
    assert mode.mode == TurnMode.CHAT
    assert mode.allowed_tool_names == frozenset()
    assert mode.evidence_required is False


def test_send_instruction_preserves_external_capability() -> None:
    original = _interpretation(capabilities=["communications"], operation="email_send")
    assert scope_interpretation_to_current_instruction(original, "Send that email now.") is original


def test_registered_tool_is_not_turn_authority_for_skill_selection() -> None:
    manifest = SkillManifest(
        id="email_comms",
        name="Email Communications",
        origin=SkillOrigin.BUILT_IN,
        status=SkillStatus.BUILT_IN,
        executable=True,
        accepted_intents=["email"],
        required_tools=["email_send"],
        tools_reachable=["email_send"],
    )
    selected = select_skill(
        user_text="email Sarah",
        manifests=[manifest],
        available_tools=set(),
    )
    assert selected.outcome == SkillSelectionOutcome.BLOCKED_MISSING_TOOL


def test_empty_turn_inventory_cannot_activate_tool_skill(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.skill_execution as execution_module

    monkeypatch.setattr(execution_module, "_EXEC_DIR", tmp_path / "skill-executions")
    manifest = SkillManifest(
        id="email_comms",
        name="Email Communications",
        origin=SkillOrigin.BUILT_IN,
        status=SkillStatus.BUILT_IN,
        executable=True,
        required_tools=["email_send"],
    )
    monkeypatch.setattr(SkillsRegistry, "_manifests", {manifest.id: manifest})
    runtime = StateStore(tmp_path / "runtime")
    turn = runtime.create_execution(thread_id="session-1", source="test", status="running")
    record = create_skill_execution(execution_id=turn.id, session_id="session-1", skill_id=manifest.id)
    blocked = activate_skill_execution(record.id, state_store=runtime, allowed_tool_names=set())
    assert blocked.status == SkillExecutionStatus.BLOCKED
    assert blocked.workflow_stage.value == "blocked"
    assert blocked.permitted_tool_ids == []
    assert runtime.list_tool_runs(turn.id) == []


def test_answer_only_turn_preserves_draft_without_mutation_disclaimer() -> None:
    from agent.core import EchoSpeakAgent

    agent = SimpleNamespace(
        _active_turn_interpretation=scope_interpretation_to_current_instruction(
            _interpretation(capabilities=["communications"], operation="email_send"),
            'Add "email Sarah" to the list.',
        ),
        _current_mode_decision=SimpleNamespace(
            allowed_tool_names=frozenset(),
            evidence_required=False,
            verification_required=False,
        ),
    )
    agent._turn_contract_requires_file_mutation = (
        EchoSpeakAgent._turn_contract_requires_file_mutation.__get__(agent)
    )
    agent._ensure_mutation_claim_honesty = EchoSpeakAgent._ensure_mutation_claim_honesty.__get__(agent)
    response = "1. Git full update\n2. Email Sarah\n3. Delete unused files"
    assert agent._ensure_mutation_claim_honesty("Add those items.", response) == response
