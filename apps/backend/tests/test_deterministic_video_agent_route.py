"""Agent deterministic video proposal route (no live model required)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


def test_try_deterministic_video_proposal_missing_selection():
    from agent.core import EchoSpeakAgent
    from agent.video_editor.chat_integration import VideoTurnPackage
    from agent.video_editor.models import VideoEditorContext, EditorSelectionContext, RationalTime

    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent._thread_key = lambda: "s1"
    agent._record_turn = lambda *a, **k: None
    agent._clamp_tts_text = lambda t: t
    agent._last_tts_text = ""
    agent._add_pipeline_reasoning = lambda *a, **k: None
    sel = EditorSelectionContext(
        document_id="d1",
        selected_clip_ids=[],
        playhead=RationalTime(ticks="1000"),
        document_revision=1,
    )
    ctx = SimpleNamespace(
        document_id="d1",
        document_revision=1,
        selection=sel,
    )
    agent._video_turn_package = VideoTurnPackage(
        active=True,
        project_id="p1",
        session_id="s1",
        document_id="d1",
        context=ctx,  # type: ignore
        direct_tool="video_propose_operations",
    )
    out = agent._try_deterministic_video_proposal("Split the selected clip at the playhead.")
    assert out is not None
    text, ok = out
    assert ok is False
    assert "selected clip" in text.lower()


def test_try_deterministic_video_proposal_creates_approval(monkeypatch):
    from agent.core import EchoSpeakAgent
    from agent.video_editor.chat_integration import VideoTurnPackage
    from agent.video_editor.models import EditorSelectionContext, RationalTime
    from types import SimpleNamespace

    agent = EchoSpeakAgent.__new__(EchoSpeakAgent)
    agent._thread_key = lambda: "s1"
    agent._record_turn = lambda *a, **k: None
    agent._clamp_tts_text = lambda t: t
    agent._last_tts_text = ""
    calls = []

    def _reason(*a, **k):
        calls.append(a)

    agent._add_pipeline_reasoning = _reason
    sel = EditorSelectionContext(
        document_id="d1",
        selected_clip_ids=["c1"],
        playhead=RationalTime(ticks="3000"),
        document_revision=2,
    )
    ctx = SimpleNamespace(
        document_id="d1",
        document_revision=2,
        selection=sel,
    )
    agent._video_turn_package = VideoTurnPackage(
        active=True,
        project_id="p1",
        session_id="s1",
        document_id="d1",
        context=ctx,  # type: ignore
        direct_tool="video_propose_operations",
    )

    def fake_propose(document_id, request):
        return {
            "approval": {"id": "appr-12345678"},
            "tool_run_id": "video-propose-x",
            "transaction": {"id": "tx1"},
        }

    with patch("api.video_editor.propose_video_transaction_sync", fake_propose):
        out = agent._try_deterministic_video_proposal("Split the selected clip at the playhead.")
    assert out is not None
    text, ok = out
    assert ok is True
    assert "proposal" in text.lower()
    assert "approve" in text.lower()
    assert any("Deterministic" in str(c) for c in calls)
