"""
Skill Writer tools — let the agent create, list, and manage skills at runtime.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional, List

from loguru import logger
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from agent.tool_registry import ToolRegistry

# ── Helpers ──────────────────────────────────────────────────────────

def _skills_dir() -> Path:
    """Resolve the configured skills directory."""
    try:
        from config import config
        return Path(getattr(config, "skills_dir", "") or "").expanduser()
    except Exception:
        return Path("skills")


def _slugify(name: str) -> str:
    """Convert a skill name to a filesystem-safe snake_case ID."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "unnamed_skill"


# ── Pydantic schemas ────────────────────────────────────────────────

class SkillCreateArgs(BaseModel):
    name: str = Field(description="Human-readable skill name (e.g. 'Weather Reporter')")
    description: str = Field(description="One-line description of what the skill does")
    prompt: str = Field(description="Full SKILL.md prompt content — instructions for the agent when this skill is active")
    tool_names: Optional[List[str]] = Field(
        default=None,
        description="Optional list of existing tool names this skill needs access to",
    )


class SkillListArgs(BaseModel):
    pass


class SkillEnableArgs(BaseModel):
    skill_id: str = Field(description="The skill directory name (ID) to enable or disable")
    enabled: bool = Field(default=True, description="True to enable, False to disable")


class SkillReviewArgs(BaseModel):
    skill_id: str = Field(description="Candidate skill directory name")
    disposable_tests_passed: bool = Field(description="True only after isolated validation completed")
    validation_evidence: str = Field(description="Short review/test evidence recorded with the proposal")


# ── skill_create ────────────────────────────────────────────────────

@ToolRegistry.register(
    name="skill_create",
    description=(
        "Create a new EchoSpeak skill package as experimental and DISABLED. "
        "Does not execute the skill. Requires separate skill_enable after review/registration approval."
    ),
    category="self_mod",
    is_action=True,
    risk_level="moderate",
    policy_flags=["ENABLE_SYSTEM_ACTIONS"],
)
@tool(args_schema=SkillCreateArgs)
def skill_create(
    name: str,
    description: str,
    prompt: str,
    tool_names: Optional[List[str]] = None,
) -> str:
    """Create a skill package as experimental+disabled. Not executable until skill_enable."""
    try:
        import uuid
        from agent.skill_contract import SkillProposal
        from agent.skill_execution import create_skill_proposal

        skills_dir = _skills_dir()
        skill_id = _slugify(name)
        skill_path = skills_dir / skill_id

        if skill_path.exists():
            return (
                f"Skill '{skill_id}' already exists at {skill_path}. "
                "Use a different name, or skill_enable after review — do not re-create."
            )

        skill_path.mkdir(parents=True, exist_ok=True)

        meta: dict = {
            "name": name.strip(),
            "description": description.strip(),
            "prompt_file": "SKILL.md",
            "version": "0.1.0",
            "status": "experimental",
            "experimental": True,
            "origin": "generated",
            "owner": "generated",
        }
        if tool_names:
            meta["tools"] = [t.strip() for t in tool_names if t.strip()]
            meta["required_tools"] = list(meta["tools"])

        (skill_path / "skill.json").write_text(
            json.dumps(meta, indent=4) + "\n", encoding="utf-8"
        )
        (skill_path / "SKILL.md").write_text(prompt.strip() + "\n", encoding="utf-8")
        # Governed: not selectable/executable until explicit enable after review.
        (skill_path / ".disabled").write_text(
            "Created disabled. Enable only after registration review.\n",
            encoding="utf-8",
        )
        (skill_path / ".experimental").write_text("true\n", encoding="utf-8")
        (skill_path / ".draft").write_text("awaiting_registration_approval\n", encoding="utf-8")

        proposal = SkillProposal(
            id=str(uuid.uuid4()),
            name=name.strip(),
            description=description.strip(),
            reason_created="Agent skill_create proposal",
            insufficient_existing_skills=[],
            accepted_intents=[name.strip().lower()],
            required_tools=list(meta.get("tools") or []),
            risks=["generated_skill", "not_reviewed"],
            verification_rules=["skill_registered_before_execution", "skill_enable_separate_turn"],
            files_created=[
                str(skill_path / "skill.json"),
                str(skill_path / "SKILL.md"),
                str(skill_path / ".disabled"),
            ],
            version="0.1.0-draft",
            status="registered_disabled",
        )
        create_skill_proposal(proposal)

        logger.info(f"Skill proposed/created disabled: {skill_id} at {skill_path}")
        tools_list = ", ".join(meta.get("tools", [])) or "none"
        return (
            f"Skill package **{name}** created as experimental and DISABLED.\n"
            f"- ID: `{skill_id}`\n"
            f"- Path: `{skill_path}`\n"
            f"- Proposal ID: `{proposal.id}`\n"
            f"- Tool allowlist: {tools_list}\n"
            f"- NOT executable in this Turn.\n"
            f"- Separate review + `skill_enable` required before use.\n"
            f"- skill_create never grants permissions or installs dependencies."
        )
    except Exception as exc:
        logger.error(f"skill_create failed: {exc}")
        return f"Failed to create skill proposal: {exc}"


# ── skill_list ──────────────────────────────────────────────────────

@ToolRegistry.register(
    name="skill_list",
    description="List all installed EchoSpeak skills with their ID, name, description, and file inventory.",
    category="self_mod",
    risk_level="safe",
)
@tool(args_schema=SkillListArgs)
def skill_list() -> str:
    """List all installed skills."""
    try:
        skills_dir = _skills_dir()
        if not skills_dir.exists():
            return "No skills directory found."

        lines = ["**Installed Skills:**\n"]
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue

            # Check disabled status
            disabled = (entry / ".disabled").exists()
            status = "🔴 disabled" if disabled else "🟢 active"

            # Read metadata
            meta_file = entry / "skill.json"
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            else:
                meta = {}

            name = meta.get("name", entry.name)
            desc = meta.get("description", "No description")
            has_tools = (entry / "tools.py").exists()
            has_plugin = (entry / "plugin.py").exists()
            has_skill_md = (entry / "SKILL.md").exists()

            indicators = []
            if has_skill_md:
                indicators.append("📝prompt")
            if has_tools:
                indicators.append("🔧tools")
            if has_plugin:
                indicators.append("🔌plugin")

            lines.append(
                f"- **{name}** (`{entry.name}`) — {status}\n"
                f"  {desc}\n"
                f"  Files: {', '.join(indicators) or 'none'}"
            )

        if len(lines) == 1:
            return "No skills installed."

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"skill_list failed: {exc}")
        return f"❌ Failed to list skills: {exc}"


# ── skill_enable ────────────────────────────────────────────────────

@ToolRegistry.register(
    name="skill_review",
    description="Record an approved isolated review and promote a generated skill to installed-but-disabled.",
    category="self_mod",
    is_action=True,
    risk_level="moderate",
    policy_flags=["ENABLE_SYSTEM_ACTIONS"],
)
@tool(args_schema=SkillReviewArgs)
def skill_review(
    skill_id: str,
    disposable_tests_passed: bool,
    validation_evidence: str,
) -> str:
    """Promote a validated proposal without enabling or importing its code."""
    try:
        from agent.skill_execution import list_skill_proposals, update_skill_proposal
        from agent.skills_registry import SkillsRegistry, package_to_manifest
        from agent.tools import get_tool_execution_context

        safe_id = _slugify(skill_id)
        skills_dir = _skills_dir().resolve()
        skill_path = (skills_dir / safe_id).resolve()
        if not skill_path.is_relative_to(skills_dir) or not skill_path.is_dir():
            return f"Skill '{safe_id}' was not found."
        evidence = str(validation_evidence or "").strip()
        if not disposable_tests_passed or not evidence:
            return "Skill review blocked: disposable validation evidence is required."

        meta_path = skill_path / "skill.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        prompt_file = str(meta.get("prompt_file") or "SKILL.md")
        prompt = (skill_path / prompt_file).read_text(encoding="utf-8").strip()
        manifest = package_to_manifest(skill_path, meta, prompt)
        if manifest.validation_errors:
            return f"Skill review blocked by manifest errors: {manifest.validation_errors}"

        proposal = next(
            (
                item
                for item in list_skill_proposals()
                if any(Path(path).parent.resolve() == skill_path for path in item.files_created)
            ),
            None,
        )
        if proposal is None or proposal.status not in {"registered_disabled", "reviewed"}:
            return "Skill review blocked: matching registered-disabled proposal was not found."

        approval_id = str(get_tool_execution_context().get("approval_id") or "")
        if not approval_id:
            return "Skill review blocked: an active registration approval is required."

        meta["status"] = "installed"
        meta["experimental"] = False
        meta["review_evidence"] = evidence[:1000]
        meta_path.write_text(json.dumps(meta, indent=4) + "\n", encoding="utf-8")
        for marker_name in (".draft", ".experimental"):
            marker = skill_path / marker_name
            if marker.exists():
                marker.unlink()
        update_skill_proposal(
            proposal.id,
            status="reviewed",
            registration_approval_id=approval_id,
            verification_rules=list(dict.fromkeys([*proposal.verification_rules, evidence[:240]])),
        )
        SkillsRegistry.refresh(skills_dir)
        return (
            f"Skill **{safe_id}** passed review and remains DISABLED. "
            "Use a separate approved skill_enable action to activate it."
        )
    except Exception as exc:
        logger.error(f"skill_review failed: {exc}")
        return f"Failed to review skill: {exc}"


@ToolRegistry.register(
    name="skill_enable",
    description="Enable or disable an installed skill by ID. Disabling creates a .disabled marker; enabling removes it.",
    category="self_mod",
    is_action=True,
    risk_level="moderate",
    policy_flags=["ENABLE_SYSTEM_ACTIONS"],
)
@tool(args_schema=SkillEnableArgs)
def skill_enable(skill_id: str, enabled: bool = True) -> str:
    """Enable or disable a skill by adding/removing a .disabled marker."""
    try:
        skills_dir = _skills_dir()
        # Sanitize skill_id to prevent path traversal (../../)
        safe_id = _slugify(skill_id)
        skill_path = skills_dir / safe_id

        # Double-check the resolved path is inside skills_dir
        if not skill_path.resolve().is_relative_to(skills_dir.resolve()):
            return f"❌ Invalid skill ID: '{skill_id}'"

        if not skill_path.exists():
            return f"❌ Skill '{safe_id}' not found in {skills_dir}"

        marker = skill_path / ".disabled"

        if enabled:
            from agent.skills_registry import SkillsRegistry, package_to_manifest

            if (skill_path / ".draft").exists() or (skill_path / ".experimental").exists():
                return (
                    f"Skill **{safe_id}** is still experimental/draft. "
                    "Complete an approved skill_review first."
                )
            meta = json.loads((skill_path / "skill.json").read_text(encoding="utf-8"))
            prompt_file = str(meta.get("prompt_file") or "SKILL.md")
            prompt = (skill_path / prompt_file).read_text(encoding="utf-8").strip()
            manifest = package_to_manifest(skill_path, meta, prompt)
            if manifest.status.value not in {"built_in", "installed", "disabled"} or manifest.validation_errors:
                return f"Skill **{safe_id}** failed activation validation: {manifest.validation_errors}"
            # Enable: remove the .disabled marker
            if marker.exists():
                marker.unlink()
                SkillsRegistry.refresh(skills_dir)
                logger.info(f"Skill enabled: {skill_id}")
                return f"✅ Skill **{skill_id}** is now enabled. It will be active on the next query."
            else:
                return f"ℹ️ Skill **{skill_id}** is already enabled."
        else:
            # Disable: create the .disabled marker
            if not marker.exists():
                marker.write_text("disabled\n", encoding="utf-8")
                from agent.skills_registry import SkillsRegistry
                SkillsRegistry.refresh(skills_dir)
                logger.info(f"Skill disabled: {skill_id}")
                return f"✅ Skill **{skill_id}** is now disabled. It will be inactive on the next query."
            else:
                return f"ℹ️ Skill **{skill_id}** is already disabled."
    except Exception as exc:
        logger.error(f"skill_enable failed: {exc}")
        return f"❌ Failed to update skill: {exc}"
