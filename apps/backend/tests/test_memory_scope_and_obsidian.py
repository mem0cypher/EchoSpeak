from __future__ import annotations

from pathlib import Path

import pytest

from agent.memory import AgentMemory
from agent.obsidian_sync import ObsidianMemorySync


def _memory(tmp_path: Path) -> AgentMemory:
    memory = AgentMemory(memory_path=str(tmp_path / "memory"))
    memory.use_faiss = False
    return memory


def test_project_memory_identity_precedes_semantic_deduplication(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    root_a = tmp_path / "project-a"
    root_b = tmp_path / "project-b"
    root_a.mkdir()
    root_b.mkdir()

    id_a = memory.add_memory_item(
        "The release channel is stable.",
        project_id="project-a",
        project_path=str(root_a),
        thread_id="session-a",
        scope="project",
        semantic_key="release-channel",
    )
    id_b = memory.add_memory_item(
        "The release channel is beta.",
        project_id="project-b",
        project_path=str(root_b),
        thread_id="session-b",
        scope="project",
        semantic_key="release-channel",
    )

    assert id_a and id_b and id_a != id_b
    rows_a = memory.list_items(
        project_id="project-a",
        project_path=str(root_a),
        thread_id="session-a",
        include_global=False,
    )
    rows_b = memory.list_items(
        project_id="project-b",
        project_path=str(root_b),
        thread_id="session-b",
        include_global=False,
    )
    assert [row["id"] for row in rows_a] == [id_a]
    assert [row["id"] for row in rows_b] == [id_b]

    assert not memory.update_item(
        id_a,
        text="cross-project mutation",
        project_id="project-b",
        thread_id="session-b",
        include_global=False,
    )
    with pytest.raises(PermissionError, match="outside the requested scope"):
        memory.delete_items(
            [id_a],
            project_id="project-b",
            thread_id="session-b",
            include_global=False,
        )


def test_supersession_is_limited_to_the_exact_project(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    root = tmp_path / "project-a"
    root.mkdir()
    first = memory.add_memory_item(
        "Use the blue theme.",
        project_id="project-a",
        project_path=str(root),
        thread_id="session-a",
        scope="project",
        semantic_key="theme",
    )
    second = memory.add_memory_item(
        "Use the monochrome theme.",
        project_id="project-a",
        project_path=str(root),
        thread_id="session-a",
        scope="project",
        semantic_key="theme",
    )
    assert first and second and first != second
    rows = memory.list_items(
        project_id="project-a",
        project_path=str(root),
        thread_id="session-a",
        include_global=False,
    )
    assert [row["id"] for row in rows] == [second]
    assert memory._records[first]["status"] == "superseded"
    assert memory._records[first]["superseded_by"] == second


def test_obsidian_is_an_explicit_projection_with_conflict_detection(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    project_root = tmp_path / "project"
    project_root.mkdir()
    memory_id = memory.add_memory_item(
        "Prefer concise release notes.",
        project_id="project-a",
        project_path=str(project_root),
        thread_id="session-a",
        scope="project",
        semantic_key="release-notes",
    )
    assert memory_id
    records = memory.list_items(
        project_id="project-a",
        project_path=str(project_root),
        thread_id="session-a",
        include_global=False,
    )

    sync = ObsidianMemorySync(tmp_path / "vault")
    plan = sync.plan(records, project_id="project-a", session_id="session-a")
    export = next(action for action in plan.actions if action.kind == "export_new")
    manifest = sync.apply_exports(plan, [export.id])
    assert memory_id in manifest.entries
    assert not sync.plan(records, project_id="project-a", session_id="session-a").actions

    note_path = Path(export.note_path)
    raw = note_path.read_text(encoding="utf-8")
    note_path.write_text(raw.replace("Prefer concise release notes.", "Prefer verified release notes."), encoding="utf-8")
    import_plan = sync.plan(records, project_id="project-a", session_id="session-a")
    update = next(action for action in import_plan.actions if action.kind == "import_update")
    sync.apply_imports(import_plan, [update.id], memory=memory, project_path=str(project_root))
    refreshed = memory.list_items(
        project_id="project-a",
        project_path=str(project_root),
        thread_id="session-a",
        include_global=False,
    )
    assert refreshed[0]["text"] == "Prefer verified release notes."


def test_obsidian_malformed_manifest_fails_closed_with_recovery_guide(tmp_path: Path) -> None:
    sync = ObsidianMemorySync(tmp_path / "vault")
    manifest = sync._manifest_path("project-a", "session-a")
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{not-json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="manifest is unreadable"):
        sync.plan([], project_id="project-a", session_id="session-a")
    assert manifest.read_text(encoding="utf-8") == "{not-json"
    assert list(manifest.parent.glob("corrupt-state/*/RECOVERY.txt"))
