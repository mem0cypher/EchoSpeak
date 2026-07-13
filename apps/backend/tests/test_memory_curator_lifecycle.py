"""Production lifecycle validation for Memory Curator: LLM path, confirm, session-only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.memory import AgentMemory
from agent.memory_curator import MemoryCandidate, MemoryCurator, build_memory_context_for_turn


@pytest.fixture()
def mem(tmp_path: Path):
    root = tmp_path / "memory"
    root.mkdir()
    m = AgentMemory(memory_path=str(root))
    m.use_faiss = False
    m.simple_memory = []
    m.embeddings = None
    m.vector_store = None
    return m


@pytest.fixture()
def curator_factory(mem, tmp_path):
    def make(llm=None):
        return MemoryCurator(mem, llm_invoke=llm, session_store_root=tmp_path / "data")

    return make


def _llm_nuanced_coding(_prompt: str) -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "type": "workflow_preference",
                    "scope": "account",
                    "subject": "coding prompts vs architecture",
                    "text": (
                        "Ty prefers short, focused prompts for coding agents, "
                        "but detailed explanations when discussing architecture."
                    ),
                    "structured_attributes": {
                        "coding_prompts": "short",
                        "architecture_explanations": "detailed",
                    },
                    "confidence": 0.95,
                    "importance": 0.9,
                    "expected_lifetime": "long_term",
                    "sensitivity": "normal",
                    "action": "create",
                    "reason": "Nuanced dual preference stated by user",
                    "semantic_key": "preference:prompt_style_coding_vs_architecture",
                }
            ]
        }
    )


def test_llm_primary_for_nuanced_explicit(curator_factory, mem):
    cur = curator_factory(_llm_nuanced_coding)
    result = cur.curate_and_persist(
        user_text=(
            "Remember: for coding agents, keep prompts short, but give me detailed "
            "explanations when we're discussing architecture."
        ),
        explicit=True,
        user_name="Ty",
        session_id="s-llm",
        execution_id="e1",
    )
    assert result.llm_invoked is True
    assert result.used_deterministic_fallback is False
    assert result.persisted_ids
    text = result.acknowledgements[0]
    assert "coding" in text.lower()
    assert "architecture" in text.lower()
    rec = mem._records[result.persisted_ids[0]]
    assert rec["active"] is True
    assert rec["metadata"].get("semantic_text") or rec["text"]


def test_malformed_llm_fails_closed_uses_fallback(curator_factory):
    def bad_llm(_p: str) -> str:
        return "not json at all, just prose"

    cur = curator_factory(bad_llm)
    result = cur.curate_and_persist(
        user_text="Remember my favorite hockey team is the Edmonton Oilers",
        explicit=True,
        user_name="Ty",
        session_id="s-bad",
    )
    assert result.llm_invoked is True
    assert result.llm_failed is True
    assert result.used_deterministic_fallback is True
    assert result.persisted_ids  # deterministic still saved


def test_unavailable_llm_fallback(curator_factory):
    cur = curator_factory(None)
    result = cur.curate_and_persist(
        user_text="Remember my favorite hockey team is the Edmonton Oilers",
        explicit=True,
        user_name="Ty",
        session_id="s-no-llm",
    )
    assert result.llm_invoked is False
    assert result.used_deterministic_fallback is True
    assert result.persisted_ids


def test_sensitive_explicit_requires_confirmation_then_save(curator_factory, mem):
    def sens_llm(_p: str) -> str:
        return json.dumps(
            {
                "candidates": [
                    {
                        "type": "fact",
                        "scope": "account",
                        "subject": "medical",
                        "text": "Ty asked to remember a private medical condition note.",
                        "confidence": 1.0,
                        "importance": 0.8,
                        "expected_lifetime": "long_term",
                        "sensitivity": "sensitive",
                        "action": "ask_confirmation",
                        "reason": "sensitive content",
                    }
                ]
            }
        )

    cur = curator_factory(sens_llm)
    # Force sensitive via password-like also
    result = cur.curate_and_persist(
        user_text="Remember my recovery code is SECRET-TOKEN-ABC",
        explicit=True,
        user_name="Ty",
        session_id="s-sens",
        execution_id="e-sens",
    )
    # Either blocked as secret or needs confirmation — never auto durable save for secrets
    if result.persisted_ids:
        # if password pattern blocked at persist
        pass
    assert not result.persisted_ids or result.needs_confirmation

    # Explicit sensitive path via candidate ask_confirmation
    cand = MemoryCandidate(
        type="note",
        scope="account",
        text="Ty's private recovery note (confirmed).",
        explicit=True,
        confidence=1.0,
        importance=0.9,
        sensitivity="sensitive",
        action="ask_confirmation",
        reason="sensitive",
        source_session_id="s-sens2",
    )
    cur2 = curator_factory(None)
    v = cur2.validate_candidate(cand)
    assert v.action == "ask_confirmation"
    pid = cur2.store_pending_confirmation("s-sens2", [v], execution_id="e2")
    assert pid
    assert cur2.get_pending_confirmation("s-sens2")
    # Reject leaves no record
    assert cur2.reject_pending("s-sens2") is True
    assert cur2.get_pending_confirmation("s-sens2") is None
    assert not any("recovery note" in str(r.get("text")) for r in mem._records.values() if r.get("active"))

    # Confirm saves with durable id
    cur2.store_pending_confirmation("s-sens3", [v], execution_id="e3")
    confirmed = cur2.confirm_pending("s-sens3")
    assert confirmed.persisted_ids
    assert mem._records[confirmed.persisted_ids[0]]["active"] is True


def test_session_only_not_in_records_or_studio_list(curator_factory, mem):
    cur = curator_factory(None)
    cand = MemoryCandidate(
        type="note",
        scope="session",
        text="Working on the video export dialog this Session.",
        explicit=False,
        confidence=0.8,
        importance=0.6,
        expected_lifetime="temporary",
        action="create",
        reason="session context",
    )
    sid = cur.add_session_only("sess-a", cand)
    assert sid
    assert sid not in mem._records
    studio_ids = {i["id"] for i in mem.list_items(offset=0, limit=100)}
    assert sid not in studio_ids
    items = cur.list_session_only("sess-a")
    assert any(i["id"] == sid for i in items)
    block = build_memory_context_for_turn(
        mem, session_id="sess-a", curator=cur, objective="export dialog"
    )
    assert "session-only" in block.lower()
    assert "not durable" in block.lower()


def test_project_only_scope(curator_factory, mem, tmp_path):
    project = str(tmp_path / "proj")
    Path(project).mkdir()

    def proj_llm(_p: str) -> str:
        return json.dumps(
            {
                "candidates": [
                    {
                        "type": "project_convention",
                        "scope": "project",
                        "subject": "video export",
                        "text": "For this Project only, videos should be vertical with minimal captions.",
                        "confidence": 0.95,
                        "importance": 0.9,
                        "expected_lifetime": "long_term",
                        "sensitivity": "normal",
                        "action": "create",
                        "reason": "Project convention",
                    }
                ]
            }
        )

    cur = curator_factory(proj_llm)
    result = cur.curate_and_persist(
        user_text="For this Project only, videos should be vertical and use minimal captions.",
        explicit=True,
        user_name="Ty",
        session_id="s-proj",
        project_path=project,
    )
    assert result.persisted_ids
    rec = mem._records[result.persisted_ids[0]]
    assert rec["scope"] == "project"
    # Other project must not retrieve
    block = build_memory_context_for_turn(
        mem, project_path=str(tmp_path / "other"), objective="video export"
    )
    assert "vertical" not in block.lower()
    block_ok = build_memory_context_for_turn(
        mem, project_path=project, objective="video export"
    )
    assert "vertical" in block_ok.lower()


def test_correction_and_forget_suppresses_list(curator_factory, mem):
    cur = curator_factory(None)
    r1 = cur.curate_and_persist(
        user_text="Remember my favorite hockey team is the Edmonton Oilers",
        explicit=True,
        user_name="Ty",
        session_id="s-corr",
    )
    assert r1.persisted_ids
    r2 = cur.curate_and_persist(
        user_text="Remember my favorite hockey team is actually the Calgary Flames now",
        explicit=True,
        user_name="Ty",
        session_id="s-corr",
    )
    assert r2.persisted_ids
    active_team = [
        r for r in mem._records.values()
        if r.get("active") and r.get("semantic_key") == "preference:favorite_hockey_team"
    ]
    assert len(active_team) == 1
    assert "flames" in active_team[0]["text"].lower()

    mid = active_team[0]["id"]
    mem.delete_items([mid])
    listed = {i["id"] for i in mem.list_items(offset=0, limit=50)}
    assert mid not in listed
    block = build_memory_context_for_turn(mem, objective="favorite team")
    assert "flames" not in block.lower()


def test_llm_inference_rejected(curator_factory):
    def infer_llm(_p: str) -> str:
        return json.dumps(
            {
                "candidates": [
                    {
                        "type": "preference",
                        "scope": "account",
                        "subject": "personality",
                        "text": "You seem like someone who prefers chaos.",
                        "confidence": 0.9,
                        "importance": 0.8,
                        "expected_lifetime": "long_term",
                        "sensitivity": "normal",
                        "action": "create",
                        "reason": "guess",
                    }
                ]
            }
        )

    cur = curator_factory(infer_llm)
    result = cur.curate_and_persist(
        user_text="I fixed a bug today",
        explicit=False,
        user_name="Ty",
        session_id="s-inf",
    )
    # Fail closed: no durable save of inference
    assert not result.persisted_ids


def test_confirm_is_confirm_helpers():
    assert MemoryCurator.is_memory_confirm("yes")
    assert MemoryCurator.is_memory_confirm("confirm")
    assert MemoryCurator.is_memory_reject("no")
    assert MemoryCurator.is_memory_reject("cancel")
    assert not MemoryCurator.is_memory_confirm("yes please rewrite the file")


def test_playhead_never_saved(curator_factory):
    cur = curator_factory(None)
    result = cur.curate_and_persist(
        user_text="Remember the playhead is at 12.4 seconds right now",
        explicit=True,
        user_name="Ty",
        session_id="s-ph",
    )
    assert not result.persisted_ids
    assert not result.session_only_ids
