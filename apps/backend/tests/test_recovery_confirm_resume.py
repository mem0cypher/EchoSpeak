"""Recovery-evidence honesty, confirmation-resume, and corrupted-write safety."""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.mode_controller import (
    CodingPhaseName,
    TurnMode,
    _intent_relation,
    classify_turn_mode,
)
from agent.active_work import ActiveWorkState


# ---------------------------------------------------------------------------
# Confirm phrase matching
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "yes proceed with the changes",
        "yes proceed",
        "proceed with the changes",
        "okay do it",
        "yes, please proceed with the changes",
    ],
)
def test_intent_relation_confirm_phrases(text):
    assert _intent_relation(text, continues=True, explicit_new=False) == "confirm"


def test_confirm_on_active_project_enters_implement_phase():
    active = ActiveWorkState(
        thread_id="t1",
        kind="coding_project",
        phase="inspect",
        project_path=r"C:\Users\ty0x7\Desktop\2d-shooter-game",
        project_name="2d-shooter-game",
        goal="make player move and shoot",
    )
    decision = classify_turn_mode("yes proceed with the changes", active_work=active)
    assert decision.mode == TurnMode.CODING
    assert decision.intent_relation == "confirm"
    assert decision.coding_phase == CodingPhaseName.IMPLEMENT


# ---------------------------------------------------------------------------
# Recovery claim honesty
# ---------------------------------------------------------------------------

def _minimal_agent_for_honesty():
    """Build a thin object that only needs recovery/mutation honesty methods."""
    from agent.core import EchoSpeakAgent

    # Avoid full __init__ if possible — use unbound methods on a SimpleNamespace
    # with the required attributes.
    agent = object.__new__(EchoSpeakAgent)
    agent._current_execution_id = "exec-test-1"
    agent._partial_tool_results = []
    agent._pending_action = None
    agent._state_store = MagicMock()
    agent._state_store.list_tool_runs.return_value = []
    return agent


def test_recovery_claims_without_checkpoint_tools_are_rewritten():
    agent = _minimal_agent_for_honesty()
    agent._partial_tool_results = [
        {
            "tool": "file_read",
            "path": r"C:\Users\ty0x7\Desktop\2d-shooter-game\game.js",
            "output": "x" * 3379,
            "success": True,
        },
        {
            "tool": "file_list",
            "output": "game.js\nindex.html\nstyle.css",
            "success": True,
        },
    ]
    # Simulate tools succeeded
    agent._tools_succeeded_this_turn = lambda: {"file_read", "file_list"}  # type: ignore

    lied = (
        "I systematically searched checkpoints, backups, autosaves, temporary copies, "
        "and previous versions. No recovery candidates exist. game.js is exactly 3016 bytes. "
        "Reconstruction is required."
    )
    out = agent._ensure_recovery_claim_honesty(
        "recover game.js from any backup or checkpoint",
        lied,
    )
    low = out.lower()
    assert "cannot conclude that no recovery copy exists" in low or "not checked" in low
    assert "3016" not in out
    assert "3379" in out or "characters" in low
    # not_checked must not be phrased as not_found for external sources
    assert "systematically searched" not in low


def test_mutation_honesty_rewrites_empty_proceeding():
    agent = _minimal_agent_for_honesty()
    agent._tools_succeeded_this_turn = lambda: set()  # type: ignore
    out = agent._ensure_mutation_claim_honesty("yes proceed with the changes", "Proceeding.")
    low = out.lower()
    assert "proceeding" not in low or "no durable" in low
    assert "no durable write" in low or "not modified" in low or "restate" in low


def test_coding_offer_extracted_from_proceed_question():
    agent = _minimal_agent_for_honesty()
    text = (
        "I can update game.js so the player moves and shoots enemies. "
        "Do you want me to proceed with this change?"
    )
    offer = agent._extract_offered_action_from_response(
        text,
        user_input="upgrade the project so the player can move and shoot enemies",
        execution_id="exec-1",
    )
    assert offer is not None
    assert offer["action"] == "coding_edit"
    assert offer["status"] == "awaiting_user_confirmation"
    assert "game.js" in (offer.get("target_file") or offer.get("subject") or "")


def test_content_has_unresolved_edit_markers():
    agent = _minimal_agent_for_honesty()
    clean = "function update() { player.x += 1; }"
    dirty = (
        "<<<<<<< SEARCH\nfunction update() {}\n=======\n"
        "function update() { player.x += 1; }\n>>>>>>> REPLACE\n"
    )
    assert agent._content_has_unresolved_edit_markers(clean) is False
    assert agent._content_has_unresolved_edit_markers(dirty) is True


def test_file_read_observations_exact_size_and_corruption():
    agent = _minimal_agent_for_honesty()
    body = "a" * 3379 + "\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\n"
    agent._partial_tool_results = [
        {
            "tool": "file_read",
            "path": r"C:\proj\game.js",
            "output": body,
            "success": True,
        }
    ]
    obs = agent._file_read_observations_this_turn()
    assert "game.js" in obs
    assert obs["game.js"]["chars"] == len(body)
    assert obs["game.js"]["corrupted_markers"] is True
    assert obs["game.js"]["provenance"] == "current_project_file_read"


def test_confirm_text_helper_matches_yes_proceed():
    agent = _minimal_agent_for_honesty()
    assert agent._is_confirm_text("yes") is True
    assert agent._is_confirm_text("yes proceed with the changes") is True
    assert agent._is_confirm_text("what about the weather") is False
