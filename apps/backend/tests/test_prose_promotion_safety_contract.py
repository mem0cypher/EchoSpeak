"""Prose→proposal must obey a strict safety contract — never generic code-block writes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def _agent(tmp_path: Path, *, mode: str = "coding", project: bool = True, inspected: bool = True):
    from agent.core import EchoSpeakAgent

    class A:
        pass

    a = A()
    a._pending_action = None
    a._state_store = SimpleNamespace(
        get_pending_approval=lambda tid: None,
        list_tool_runs=lambda eid: [],
        get_thread_state=lambda tid: SimpleNamespace(active_project_id="proj-1" if project else ""),
        list_approvals=lambda **kw: [],
    )
    a._current_execution_id = "ex1"
    a._thread_key = lambda: "t1"
    a._active_project_id = "proj-1" if project else ""
    a._tools_succeeded_this_turn = lambda: {"file_read"} if inspected else set()
    a._partial_tool_inputs = {}
    a._content_has_unresolved_edit_markers = EchoSpeakAgent._content_has_unresolved_edit_markers.__get__(a)
    a._file_write_path_allowed_by_request = lambda user, path: "game.js" not in path
    a._action_allowed = lambda tool, kwargs: True
    a._normalize_coding_file_path = lambda p: str(tmp_path / Path(p).name)
    a._set_pending_action = lambda pending, preview, user: setattr(a, "_pending_action", pending) or pending
    a._current_mode_decision = SimpleNamespace(mode=SimpleNamespace(value=mode, name=mode.upper()))
    a._durable_pending_mutation = EchoSpeakAgent._durable_pending_mutation.__get__(a)
    a._user_requested_file_mutation = EchoSpeakAgent._user_requested_file_mutation.__get__(a)
    a._prose_promotion_coding_mode_active = EchoSpeakAgent._prose_promotion_coding_mode_active.__get__(a)
    a._prose_promotion_project_attached = EchoSpeakAgent._prose_promotion_project_attached.__get__(a)
    a._named_mutation_targets = EchoSpeakAgent._named_mutation_targets.__get__(a)
    a._extract_prose_file_body = EchoSpeakAgent._extract_prose_file_body.__get__(a)
    a._file_inspected_this_turn = (
        (lambda path: True) if inspected else (lambda path: False)
    )
    a._try_promote_prose_to_file_write_proposal = EchoSpeakAgent._try_promote_prose_to_file_write_proposal.__get__(
        a
    )
    return a


def _html_body(title: str = "New Title") -> str:
    return f"<!doctype html><html><head><title>{title}</title></head><body>x</body></html>"


def test_explicit_mutation_promotes_when_contract_holds(tmp_path):
    p = tmp_path / "index.html"
    p.write_text("<html><title>Old</title></html>\n", encoding="utf-8")
    a = _agent(tmp_path, inspected=True)
    # Simulate inspected path via list_tool_runs
    run = SimpleNamespace(
        tool_name="file_read",
        status="success",
        outcome={"success": True},
        canonical_arguments={"path": str(p)},
    )
    a._state_store = SimpleNamespace(
        get_pending_approval=lambda tid: None,
        list_tool_runs=lambda eid: [run],
        get_thread_state=lambda tid: SimpleNamespace(active_project_id="proj-1"),
        list_approvals=lambda **kw: [],
    )
    a._file_inspected_this_turn = EchoSpeakAgent_file_inspected = (
        __import__("agent.core", fromlist=["EchoSpeakAgent"]).EchoSpeakAgent._file_inspected_this_turn.__get__(a)
    )
    out = a._try_promote_prose_to_file_write_proposal(
        "Change the title in index.html to New Title.",
        f"Here is the file:\n```html\n{_html_body()}\n```",
    )
    assert out is not None
    assert "NOT been saved" in out
    assert a._pending_action is not None
    assert a._pending_action["tool"] == "file_write"


def test_example_html_not_promoted(tmp_path):
    p = tmp_path / "index.html"
    p.write_text("<html><title>Old</title></html>\n", encoding="utf-8")
    a = _agent(tmp_path)
    out = a._try_promote_prose_to_file_write_proposal(
        "Show me an example HTML page.",
        f"For example:\n```html\n{_html_body()}\n```",
    )
    assert out is None
    assert a._pending_action is None


def test_explain_only_not_promoted(tmp_path):
    p = tmp_path / "index.html"
    p.write_text("<html><title>Old</title></html>\n", encoding="utf-8")
    a = _agent(tmp_path)
    out = a._try_promote_prose_to_file_write_proposal(
        "Explain how I could change index.html. Do not edit anything; just include the corrected code.",
        f"```html\n{_html_body()}\n```",
    )
    assert out is None


def test_research_sample_code_not_promoted(tmp_path):
    p = tmp_path / "index.html"
    p.write_text("<html><title>Old</title></html>\n", encoding="utf-8")
    a = _agent(tmp_path, mode="research")
    out = a._try_promote_prose_to_file_write_proposal(
        "Research this and put sample code in the answer.",
        f"```html\n{_html_body()}\n```",
    )
    assert out is None


def test_exclusion_game_js_targets_index_only(tmp_path):
    p = tmp_path / "index.html"
    p.write_text("<html><title>Old</title></html>\n", encoding="utf-8")
    a = _agent(tmp_path)
    run = SimpleNamespace(
        tool_name="file_read",
        status="success",
        outcome={"success": True},
        canonical_arguments={"path": str(p)},
    )
    a._state_store.list_tool_runs = lambda eid: [run]
    a._file_inspected_this_turn = __import__(
        "agent.core", fromlist=["EchoSpeakAgent"]
    ).EchoSpeakAgent._file_inspected_this_turn.__get__(a)
    targets = a._named_mutation_targets("Change index.html, not game.js.")
    assert targets == ["index.html"]


def test_chat_mode_blocks_promotion(tmp_path):
    p = tmp_path / "index.html"
    p.write_text("<html><title>Old</title></html>\n", encoding="utf-8")
    a = _agent(tmp_path, mode="chat")
    out = a._try_promote_prose_to_file_write_proposal(
        "Change the title in index.html to X.",
        f"```html\n{_html_body('X')}\n```",
    )
    assert out is None


def test_no_project_blocks_promotion(tmp_path):
    p = tmp_path / "index.html"
    p.write_text("<html><title>Old</title></html>\n", encoding="utf-8")
    a = _agent(tmp_path, project=False)
    out = a._try_promote_prose_to_file_write_proposal(
        "Change the title in index.html to X.",
        f"```html\n{_html_body('X')}\n```",
    )
    assert out is None


def test_not_inspected_blocks_promotion(tmp_path):
    p = tmp_path / "index.html"
    p.write_text("<html><title>Old</title></html>\n", encoding="utf-8")
    a = _agent(tmp_path, inspected=False)
    a._file_inspected_this_turn = lambda path: False
    out = a._try_promote_prose_to_file_write_proposal(
        "Change the title in index.html to X.",
        f"```html\n{_html_body('X')}\n```",
    )
    assert out is None
