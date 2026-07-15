"""Canonical memory indexing and skill-selection coverage."""

from __future__ import annotations


def test_faiss_forget_and_rebuild(tmp_path, monkeypatch):
    from agent.memory import AgentMemory

    mem = AgentMemory(memory_path=str(tmp_path / "memory"))
    owner = mem._owner_id()
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
    result = mem.rebuild_faiss_from_canonical()
    assert result.get("ok") is True
    mem.delete_items([rid])
    assert mem._records[rid]["active"] is False
    result2 = mem.rebuild_faiss_from_canonical()
    assert result2.get("ok") is True
    ctx_after = mem.get_conversation_context("rambutan sunrise fruit", k=5)
    assert "rambutan" not in (ctx_after or "").lower()
    docs = mem.retrieve_relevant("rambutan sunrise", k=5)
    for doc in docs:
        assert "rambutan" not in (getattr(doc, "page_content", "") or "").lower()


def test_prompt_only_not_selected():
    from agent.skill_contract import SkillManifest, SkillOrigin, SkillSelectionOutcome, SkillStatus
    from agent.skill_selection import select_skill

    manifest = SkillManifest(
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
        manifests=[manifest],
        available_tools=set(),
        available_capabilities=set(),
    )
    assert result.outcome in {
        SkillSelectionOutcome.UNAVAILABLE,
        SkillSelectionOutcome.NO_MATCHING_SKILL,
        SkillSelectionOutcome.DISABLED,
    } or result.skill_id == ""
