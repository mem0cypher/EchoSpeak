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
    name="skill_enable",
    description="Enable or disable an installed skill by ID. Disabling creates a .disabled marker; enabling removes it.",
    category="self_mod",
    is_action=True,
    risk_level="moderate",
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
            # Enable: remove the .disabled marker
            if marker.exists():
                marker.unlink()
                logger.info(f"Skill enabled: {skill_id}")
                return f"✅ Skill **{skill_id}** is now enabled. It will be active on the next query."
            else:
                return f"ℹ️ Skill **{skill_id}** is already enabled."
        else:
            # Disable: create the .disabled marker
            if not marker.exists():
                marker.write_text("disabled\n", encoding="utf-8")
                logger.info(f"Skill disabled: {skill_id}")
                return f"✅ Skill **{skill_id}** is now disabled. It will be inactive on the next query."
            else:
                return f"ℹ️ Skill **{skill_id}** is already disabled."
    except Exception as exc:
        logger.error(f"skill_enable failed: {exc}")
        return f"❌ Failed to update skill: {exc}"
