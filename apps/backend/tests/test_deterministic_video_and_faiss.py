"""Deterministic video proposals + FAISS forget/rebuild."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


def test_deterministic_split_requires_selection():
    from agent.video_editor.deterministic_ops import build_deterministic_operations

    built = build_deterministic_operations(
        user_text="Split the selected clip at the playhead",
        document_revision=1,
        selected_clip_ids=[],
        playhead_ticks="3000",
    )
    assert built["ok"] is False
    assert built["error_code"] == "missing_selection"


def test_deterministic_split_ok():
    from agent.video_editor.deterministic_ops import build_deterministic_operations

    built = build_deterministic_operations(
        user_text="Split the selected clip at the playhead",
        document_revision=2,
        selected_clip_ids=["c1"],
        playhead_ticks="3000",
    )
    assert built["ok"] is True
    assert built["operations"][0]["operation_type"] == "split_clip"
    assert built["operations"][0]["payload"]["clip_id"] == "c1"


def test_creative_intent_not_deterministic():
    from agent.video_editor.deterministic_ops import is_deterministic_video_intent

    assert not is_deterministic_video_intent("make a cinematic cut of the best moments")
    assert is_deterministic_video_intent("delete the selected clip")


def test_faiss_forget_and_rebuild(tmp_path, monkeypatch):
    from agent.memory import AgentMemory

    mem = AgentMemory(memory_path=str(tmp_path / "memory"))
    owner = mem._owner_id()
    # Direct canonical write
    rid = "mem-rambutan-1"
    mem._records[rid] = {
        "id": rid,
        "text": "My preferred test fruit is rambutan sunrise.",
        "normalized_content": "my preferred test fruit is rambutan sunrise",
        "owner_id": owner,
        "active": True,
        "scope": "account",
        "type": "preference",
        "index_state": "pending",
    }
    mem._save_records()
    # Index via rebuild
    result = mem.rebuild_faiss_from_canonical()
    assert result.get("ok") is True
    # Forget / tombstone
    mem.delete_items([rid])
    assert mem._records[rid]["active"] is False
    # Rebuild excludes inactive
    result2 = mem.rebuild_faiss_from_canonical()
    assert result2.get("ok") is True
    ctx_after = mem.get_conversation_context("rambutan sunrise fruit", k=5)
    assert "rambutan" not in (ctx_after or "").lower()
    docs = mem.retrieve_relevant("rambutan sunrise", k=5)
    for d in docs:
        assert "rambutan" not in (getattr(d, "page_content", "") or "").lower()


def test_prompt_only_not_selected():
    from agent.skill_contract import SkillManifest, SkillOrigin, SkillStatus, SkillSelectionOutcome
    from agent.skill_selection import select_skill

    m = SkillManifest(
        id="fake_prompt",
        name="Fake",
        description="d",
        version="1.0.0",
        origin=SkillOrigin.PACKAGE,
        status=SkillStatus.INSTALLED,
        executable=True,
        prompt="Only a prompt",
        required_tools=[],
        package_path="",
        accepted_intents=["do something fake"],
    )
    result = select_skill(
        user_text="do something fake please",
        manifests=[m],
        available_tools=set(),
        available_capabilities=set(),
    )
    assert result.outcome in {
        SkillSelectionOutcome.UNAVAILABLE,
        SkillSelectionOutcome.NO_MATCHING_SKILL,
        SkillSelectionOutcome.DISABLED,
    } or result.skill_id == ""
