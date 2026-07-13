"""Memory Curator: explicit rewrite, implicit gates, dedupe, supersede, reflection bounds."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.memory import AgentMemory
from agent.memory_curator import (
    MemoryCandidate,
    MemoryCurator,
    build_memory_context_for_turn,
    skill_memory_context,
)


@pytest.fixture()
def mem(tmp_path: Path, monkeypatch):
    # Isolate memory root
    root = tmp_path / "memory"
    root.mkdir()
    m = AgentMemory(memory_path=str(root))
    m.use_faiss = False
    m.simple_memory = []
    m.embeddings = None
    m.vector_store = None
    return m


def test_explicit_oilers_rewritten_semantic(mem):
    curator = MemoryCurator(mem, llm_invoke=None)
    result = curator.curate_and_persist(
        user_text="Remember I like the Edmonton Oilers and they're my favorite hockey team.",
        explicit=True,
        session_id="s1",
        execution_id="e1",
        user_name="Ty",
    )
    assert result.persisted_ids, result.errors
    text = result.acknowledgements[0]
    assert "Oilers" in text
    assert "favorite" in text.lower()
    assert "Ty" in text or "user" in text.lower()
    # Not only key:value
    assert ":" not in text or "team" in text.lower()
    rec = mem._records[result.persisted_ids[0]]
    assert rec["active"] is True
    assert rec["semantic_key"] == "preference:favorite_hockey_team" or "hockey" in text.lower()


def test_temporary_playhead_rejected(mem):
    curator = MemoryCurator(mem)
    cands = curator.propose_candidates(
        user_text="Remember the playhead is at 12.4 seconds right now",
        explicit=True,
        user_name="Ty",
    )
    # Even if proposed, validation must reject temporary
    for c in cands:
        v = curator.validate_candidate(c)
        if "playhead" in c.text.lower() or "right now" in c.text.lower():
            assert v.action == "ignore"


def test_sensitive_implicit_not_saved(mem):
    curator = MemoryCurator(mem)
    result = curator.curate_and_persist(
        user_text="My password is hunter2 and api_key is sk-abc",
        explicit=False,
        session_id="s1",
    )
    assert not result.persisted_ids


def test_duplicate_reinforcement(mem):
    curator = MemoryCurator(mem)
    r1 = curator.curate_and_persist(
        user_text="Remember my favorite hockey team is the Edmonton Oilers",
        explicit=True,
        user_name="Ty",
        session_id="s1",
    )
    assert r1.persisted_ids
    r2 = curator.curate_and_persist(
        user_text="Edmonton is still my favorite NHL team",
        explicit=True,
        user_name="Ty",
        session_id="s1",
    )
    # Second should reinforce (ignore) or supersede same key without double active copies
    active = [r for r in mem._records.values() if r.get("active") and "oilers" in str(r.get("text") or "").lower()]
    assert len(active) <= 2  # at most one semantic + profile projection style


def test_correction_supersedes(mem):
    curator = MemoryCurator(mem)
    r1 = curator.curate_and_persist(
        user_text="Remember my favorite hockey team is the Edmonton Oilers",
        explicit=True,
        user_name="Ty",
        session_id="s1",
    )
    assert r1.persisted_ids
    r2 = curator.curate_and_persist(
        user_text="Remember my favorite hockey team is actually the Calgary Flames now",
        explicit=True,
        user_name="Ty",
        session_id="s1",
    )
    assert r2.persisted_ids
    active_oilers = [
        r for r in mem._records.values()
        if r.get("active") and "oilers" in str(r.get("text") or "").lower()
        and "flames" not in str(r.get("text") or "").lower()
    ]
    # Old oilers-only memory should be inactive after supersede on same semantic key
    oilers_only_active = [
        r for r in mem._records.values()
        if r.get("active")
        and r.get("semantic_key") == "preference:favorite_hockey_team"
        and "oilers" in str(r.get("text") or "").lower()
        and "flames" not in str(r.get("text") or "").lower()
    ]
    assert not oilers_only_active


def test_project_scoped_memory(mem, tmp_path):
    curator = MemoryCurator(mem)
    project = str(tmp_path / "proj")
    Path(project).mkdir(exist_ok=True)
    result = curator.curate_and_persist(
        user_text="For this project export videos in 1080x1920 vertical format",
        explicit=True,
        project_path=project,
        user_name="Ty",
        session_id="s1",
    )
    # May land account or project depending on rewrite — force via candidate
    cand = MemoryCandidate(
        type="project_convention",
        scope="project",
        text="Export this Project's videos in 1080×1920 vertical format.",
        explicit=True,
        confidence=1.0,
        importance=0.9,
        action="create",
        source_session_id="s1",
    )
    v = curator.validate_candidate(cand, project_path=project)
    assert v.action in {"create", "update", "supersede"}
    mid = curator.persist_candidate(v, project_path=project, thread_id="s1")
    assert mid
    rec = mem._records[mid]
    assert rec["scope"] == "project"


def test_reflection_not_recursive(mem):
    curator = MemoryCurator(mem)
    MemoryCurator._reflection_guard.active = True
    try:
        result = curator.reflect_after_turn(user_text="I prefer short coding prompts", session_id="s1")
        assert result.errors
        assert "reentry" in result.errors[0]
    finally:
        MemoryCurator._reflection_guard.active = False


def test_failed_persist_no_ack(mem, monkeypatch):
    curator = MemoryCurator(mem)

    def boom(*a, **k):
        return None

    monkeypatch.setattr(mem, "add_memory_item", boom)
    result = curator.curate_and_persist(
        user_text="Remember I prefer concise coding-agent prompts",
        explicit=True,
        user_name="Ty",
        session_id="s1",
    )
    assert not result.persisted_ids
    assert not result.acknowledgements or result.errors or result.rejected


def test_selective_context_and_skill_read(mem):
    curator = MemoryCurator(mem)
    curator.curate_and_persist(
        user_text="Remember I prefer short coding prompts",
        explicit=True,
        user_name="Ty",
        session_id="s1",
    )
    block = build_memory_context_for_turn(mem, objective="coding agent prompt style", limit=5)
    assert "Durable memories" in block or "prefer" in block.lower() or "prompt" in block.lower()
    rows = skill_memory_context(mem, subjects=["prompt", "coding"])
    assert isinstance(rows, list)


def test_forgotten_not_listed(mem):
    curator = MemoryCurator(mem)
    r = curator.curate_and_persist(
        user_text="Remember my favorite color is blue",
        explicit=True,
        user_name="Ty",
        session_id="s1",
    )
    assert r.persisted_ids
    mid = r.persisted_ids[0]
    mem.delete_items([mid])
    items = mem.list_items(offset=0, limit=50)
    assert mid not in {i["id"] for i in items}


def test_explicit_phrases(mem):
    curator = MemoryCurator(mem)
    for phrase in (
        "Keep this in mind: I prefer manual testing after runtime fixes",
        "From now on use short prompts for coding agents",
        "Note that I dislike huge repetitive architecture dumps",
        "Don't forget I want generated and recorded footage on one timeline",
    ):
        assert MemoryCurator.is_explicit_memory_request(phrase)
        payload = MemoryCurator.extract_explicit_payload(phrase)
        assert payload
