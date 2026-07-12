"""Tests for Bug 2: Project Memory Continuity & Plan Approval."""

import pytest
from pathlib import Path
from agent.active_work import ActiveWorkState, request_continues_project, request_approves_plan


def test_request_continues_project_implicit():
    """Verify implicit continuity detection on pronouns and articles."""
    state = ActiveWorkState(
        project_path="C:/Users/ty0x7/Desktop/2d-shooter-game",
        project_name="2d-shooter-game",
        kind="coding_project",
        phase="implement"
    )

    # Definite articles referring to active work
    assert request_continues_project("make the app wider", state)
    assert request_continues_project("fix the game logic", state)
    
    # Pronoun continuations
    assert request_continues_project("build it", state)
    assert request_continues_project("finish it please", state)

    # Continuation phrases
    assert request_continues_project("let's keep going", state)
    assert request_continues_project("okay go ahead", state)
    assert request_continues_project("where were we?", state)


def test_request_continues_project_new_conflict():
    """Verify that conflict queries or explicit new project calls return False."""
    state = ActiveWorkState(
        project_path="C:/Users/ty0x7/Desktop/2d-shooter-game",
        project_name="2d-shooter-game",
        kind="coding_project",
        phase="implement"
    )

    # Explicit new project intent
    assert not request_continues_project("build me a new project", state)
    assert not request_continues_project("start a different project from scratch", state)

    # Indefinite article with a different product noun (Todo vs Game)
    assert not request_continues_project("build a todo app", state)
    assert not request_continues_project("make a calculator", state)


def test_request_approves_plan():
    """Verify request_approves_plan helper matches user approvals."""
    assert request_approves_plan("looks good")
    assert request_approves_plan("go ahead")
    assert request_approves_plan("proceed")
    assert request_approves_plan("let's start building")
    assert request_approves_plan("yes, implement it")

    assert not request_approves_plan("why are we doing this?")
    assert not request_approves_plan("not yet")
