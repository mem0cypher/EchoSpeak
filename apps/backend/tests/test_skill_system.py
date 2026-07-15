"""Canonical skill registry and durable skill lifecycle coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.skill_contract import SkillExecutionStatus, SkillProposal, SkillSelectionOutcome, SkillStatus
from agent.skill_execution import create_skill_execution, create_skill_proposal, get_skill_execution
from agent.skill_selection import select_skill
from agent.skills_registry import SkillsRegistry, load_skills


@pytest.fixture()
def skills_env(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    package = skills_dir / "demo_skill"
    package.mkdir()
    (package / "skill.json").write_text(
        '{"name":"Demo","description":"Demo skill","tools":["get_system_time"],"version":"1.0.0"}',
        encoding="utf-8",
    )
    (package / "SKILL.md").write_text("# Demo\nUse get_system_time.\n", encoding="utf-8")
    disabled = skills_dir / "disabled_skill"
    disabled.mkdir()
    (disabled / "skill.json").write_text('{"name":"Disabled","description":"Off"}', encoding="utf-8")
    (disabled / "SKILL.md").write_text("# Off\n", encoding="utf-8")
    (disabled / ".disabled").write_text("1\n", encoding="utf-8")
    invalid = skills_dir / "broken_skill"
    invalid.mkdir()
    (invalid / "skill.json").write_text('{"name":"Broken"}', encoding="utf-8")
    SkillsRegistry.clear()
    SkillsRegistry.refresh(skills_dir)
    return skills_dir


def test_canonical_registry_loads_and_skips_disabled(skills_env: Path):
    manifests = {item.id: item for item in SkillsRegistry.list_manifests(include_disabled=False)}
    assert "demo_skill" in manifests
    assert "disabled_skill" not in manifests
    complete = {item.id: item for item in SkillsRegistry.list_manifests(include_disabled=True)}
    assert complete["disabled_skill"].status == SkillStatus.DISABLED
    assert complete["broken_skill"].status == SkillStatus.INVALID


def test_disabled_skill_not_in_load_skills(skills_env: Path):
    loaded = load_skills(skills_env)
    assert "demo_skill" in loaded
    assert "disabled_skill" not in loaded


def test_stale_prior_skill_does_not_auto_select(skills_env: Path):
    result = select_skill(
        user_text="hello there",
        manifests=SkillsRegistry.list_manifests(),
        prior_unfinished_skill_id="demo_skill",
        allow_stale_prior=False,
    )
    assert result.outcome == SkillSelectionOutcome.NO_MATCHING_SKILL
    assert result.skill_id == ""


def test_skill_execution_record_and_proposal(tmp_path: Path, monkeypatch):
    import agent.skill_execution as skill_execution

    monkeypatch.setattr(skill_execution, "_EXEC_DIR", tmp_path / "exec")
    monkeypatch.setattr(skill_execution, "_PROPOSAL_DIR", tmp_path / "prop")
    record = create_skill_execution(
        execution_id="exec-1",
        skill_id="demo_skill",
        skill_version="1.0.0",
        project_id="p1",
        session_id="s1",
        status=SkillExecutionStatus.PLANNED,
    )
    loaded = get_skill_execution(record.id)
    assert loaded is not None
    assert loaded.skill_id == "demo_skill"
    assert loaded.status == SkillExecutionStatus.PLANNED

    proposal = create_skill_proposal(
        SkillProposal(
            id="prop-1",
            name="Reusable workflow",
            description="A governed reusable workflow",
            reason_created="missing workflow",
            status="proposed",
        )
    )
    assert proposal.status == "proposed"


def test_skill_create_writes_disabled_package(tmp_path: Path, monkeypatch):
    import skills.skill_writer.tools as skill_writer

    monkeypatch.setattr(skill_writer, "_skills_dir", lambda: tmp_path / "skills")
    (tmp_path / "skills").mkdir()
    invoke = getattr(skill_writer.skill_create, "invoke", None)
    payload = {
        "name": "Project Notes",
        "description": "Organize project notes",
        "prompt": "# Project notes\nOrganize notes.",
        "tool_names": ["file_read"],
    }
    output = invoke(payload) if callable(invoke) else skill_writer.skill_create(**payload)
    assert "disabled" in str(output).lower()
    skill_path = tmp_path / "skills" / "project_notes"
    assert (skill_path / ".disabled").exists()
    assert (skill_path / ".experimental").exists()
    assert "project_notes" not in load_skills(tmp_path / "skills")
