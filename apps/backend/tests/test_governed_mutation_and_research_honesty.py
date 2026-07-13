"""Regression: prose must not claim mutation/research without durable authority."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace


class _FakeStore:
    def __init__(self):
        self._pending = None
        self._runs = []

    def get_pending_approval(self, thread_id: str):
        return self._pending

    def list_tool_runs(self, execution_id: str):
        return list(self._runs)


def _agent_stub(tmp_path: Path):
    from agent.core import EchoSpeakAgent

    # Minimal stand-in without full init — bind methods only
    class A:
        pass

    a = A()
    a._pending_action = None
    a._state_store = _FakeStore()
    a._current_execution_id = "ex1"
    a._thread_key = lambda: "t1"
    a._tools_succeeded_this_turn = lambda: set()
    a._content_has_unresolved_edit_markers = EchoSpeakAgent._content_has_unresolved_edit_markers.__get__(a)
    a._file_write_path_allowed_by_request = lambda user, path: True
    a._action_allowed = lambda tool, kwargs: True
    a._normalize_coding_file_path = lambda p: str(tmp_path / Path(p).name)
    a._set_pending_action = lambda pending, preview, user: setattr(a, "_pending_action", pending) or pending
    a._durable_pending_mutation = EchoSpeakAgent._durable_pending_mutation.__get__(a)
    a._user_requested_file_mutation = EchoSpeakAgent._user_requested_file_mutation.__get__(a)
    a._extract_prose_file_body = EchoSpeakAgent._extract_prose_file_body.__get__(a)
    a._prose_promotion_coding_mode_active = EchoSpeakAgent._prose_promotion_coding_mode_active.__get__(a)
    a._prose_promotion_project_attached = EchoSpeakAgent._prose_promotion_project_attached.__get__(a)
    a._named_mutation_targets = EchoSpeakAgent._named_mutation_targets.__get__(a)
    a._file_inspected_this_turn = EchoSpeakAgent._file_inspected_this_turn.__get__(a)
    a._try_promote_prose_to_file_write_proposal = EchoSpeakAgent._try_promote_prose_to_file_write_proposal.__get__(a)
    a._ensure_mutation_claim_honesty = EchoSpeakAgent._ensure_mutation_claim_honesty.__get__(a)
    a._ensure_research_evidence_honesty = EchoSpeakAgent._ensure_research_evidence_honesty.__get__(a)
    a._current_mode_decision = SimpleNamespace(mode=SimpleNamespace(value="coding", name="CODING"))
    a._active_project_id = "proj-1"
    a._current_execution_id = "ex1"
    return a


def test_prose_html_becomes_pending_not_applied(tmp_path):
    from types import SimpleNamespace as SN

    html_path = tmp_path / "index.html"
    html_path.write_text("<html><title>Old</title></html>\n", encoding="utf-8")
    a = _agent_stub(tmp_path)
    run = SN(
        tool_name="file_read",
        status="success",
        outcome={"success": True},
        canonical_arguments={"path": str(html_path)},
    )
    a._state_store = SN(
        get_pending_approval=lambda tid: None,
        list_tool_runs=lambda eid: [run],
        get_thread_state=lambda tid: SN(active_project_id="proj-1"),
        list_approvals=lambda **kw: [],
    )
    a._tools_succeeded_this_turn = lambda: {"file_read"}
    prose = (
        "I have updated index.html.\n\n```html\n"
        "<!doctype html><html><head><title>Game Application Live</title></head><body>x</body></html>\n"
        "```"
    )
    out = a._ensure_mutation_claim_honesty(
        "Change the title in index.html to Game Application Live.",
        prose,
    )
    assert a._pending_action is not None
    assert a._pending_action["tool"] == "file_write"
    assert "Game Application Live" in a._pending_action["kwargs"]["content"]
    assert "NOT been saved" in out or "prepared" in out.lower()
    # File still old until confirm
    assert "Old" in html_path.read_text(encoding="utf-8")


def test_false_updated_claim_without_body_is_honest():
    a = _agent_stub(Path("."))
    out = a._ensure_mutation_claim_honesty(
        "Change the title in index.html to X.",
        "I have updated index.html successfully.",
    )
    assert "not" in out.lower() or "will not claim" in out.lower() or "did not create" in out.lower()
    assert a._pending_action is None


def test_research_without_toolrun_blocked():
    a = _agent_stub(Path("."))
    decision = SimpleNamespace(mode=SimpleNamespace(value="chat"), reason="")
    out = a._ensure_research_evidence_honesty(
        "Research one short fact about the Edmonton Oilers and mention sources.",
        "The Oilers were founded in 1972 and are a great team.",
        mode_decision=decision,
    )
    assert "web_search" in out.lower() or "could not complete" in out.lower()
    assert "founded in 1972" not in out


def test_local_first_research_not_blocked_by_public_gate():
    a = _agent_stub(Path("."))
    decision = SimpleNamespace(mode=SimpleNamespace(value="chat"), reason="")
    out = a._ensure_research_evidence_honesty(
        "Use local project material first. Summarize what this project is without searching the web.",
        "This is a local coding fixture project.",
        mode_decision=decision,
    )
    assert out == "This is a local coding fixture project."
