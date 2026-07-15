from __future__ import annotations

from pathlib import Path

import pytest

from agent.coding_ledger import CodingEvidence, CodingLedgerStore


def test_coding_ledger_resumes_only_exact_session_project_objective(tmp_path: Path) -> None:
    store = CodingLedgerStore(tmp_path / "ledgers")
    root = tmp_path / "project"
    root.mkdir()
    first = store.start_or_resume(
        session_id="session-a",
        project_id="project-a",
        project_root=str(root),
        objective="Implement durable resume",
    )
    resumed = store.start_or_resume(
        session_id="session-a",
        project_id="project-a",
        project_root=str(root),
        objective="  implement   durable resume ",
    )
    other_project = store.start_or_resume(
        session_id="session-a",
        project_id="project-b",
        project_root=str(root),
        objective="Implement durable resume",
    )

    assert resumed.id == first.id
    assert other_project.id != first.id
    assert store.active_for("session-a", "project-a").id == first.id
    assert store.active_for("session-a", "project-b").id == other_project.id


def test_coding_ledger_revisions_evidence_and_restart(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    ledger_root = tmp_path / "ledgers"
    store = CodingLedgerStore(ledger_root)
    record = store.start_or_resume(
        session_id="session-a",
        project_id="project-a",
        project_root=str(root),
        objective="Trace authority",
    )
    updated = store.update(record.id, expected_revision=record.revision, status="implementing")
    with pytest.raises(ValueError, match="stale"):
        store.update(record.id, expected_revision=record.revision, status="completed")
    with_evidence = store.append_evidence(
        record.id,
        CodingEvidence(kind="inspection", summary="Project root verified", path=str(root)),
    )
    assert with_evidence.revision == updated.revision + 1
    assert with_evidence.completion_evidence[-1].summary == "Project root verified"

    restored = CodingLedgerStore(ledger_root).get(record.id)
    assert restored is not None
    assert restored.status == "implementing"
    assert restored.completion_evidence[-1].kind == "inspection"


def test_coding_ledger_corruption_is_preserved_and_quarantined(tmp_path: Path) -> None:
    root = tmp_path / "ledgers"
    root.mkdir()
    bad = root / "bad.json"
    bad.write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Coding ledger is unreadable"):
        CodingLedgerStore(root)
    assert bad.read_text(encoding="utf-8") == "{broken"
    assert list(root.glob("corrupt-state/*/bad.json"))
    assert list(root.glob("corrupt-state/*/RECOVERY.txt"))
