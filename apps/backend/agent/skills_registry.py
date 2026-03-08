"""
Skills and workspace registry helpers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, List

from loguru import logger


@dataclass
class SkillDefinition:
    id: str
    name: str
    description: str
    prompt: str
    tool_allowlist: List[str] = field(default_factory=list)


@dataclass
class WorkspaceDefinition:
    id: str
    name: str
    prompt: str
    skill_ids: List[str] = field(default_factory=list)
    tool_allowlist: List[str] = field(default_factory=list)


def _read_text(path: Path) -> str:
    try:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.warning(f"Failed to read {path}: {exc}")
        return ""


def _load_json(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"Failed to parse {path}: {exc}")
        return {}


def _read_list(path: Path) -> List[str]:
    raw = _read_text(path)
    if not raw:
        return []
    items: List[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or stripped.startswith("//"):
            continue
        items.append(stripped)
    return items


def _derive_description(prompt: str) -> str:
    for line in (prompt or "").splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned
    return ""


def load_skills(skills_dir: Path) -> Dict[str, SkillDefinition]:
    skills: Dict[str, SkillDefinition] = {}
    if not skills_dir.exists():
        return skills
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        # Skip disabled skills (created by skill_enable tool)
        if (entry / ".disabled").exists():
            logger.debug(f"Skipping disabled skill: {entry.name}")
            continue
        meta = _load_json(entry / "skill.json")
        prompt_file = str(meta.get("prompt_file") or "SKILL.md")
        prompt = _read_text(entry / prompt_file)
        if not prompt:
            continue
        skill_id = entry.name
        name = str(meta.get("name") or skill_id).strip() or skill_id
        description = str(meta.get("description") or "").strip()
        if not description:
            description = _derive_description(prompt)
        tool_allowlist = [str(x).strip() for x in (meta.get("tools") or []) if str(x).strip()]
        if not tool_allowlist:
            tool_allowlist = _read_list(entry / "TOOLS.txt")
        skills[skill_id] = SkillDefinition(
            id=skill_id,
            name=name,
            description=description,
            prompt=prompt,
            tool_allowlist=tool_allowlist,
        )
    return skills


def load_workspace(workspaces_dir: Path, workspace_id: str) -> Optional[WorkspaceDefinition]:
    if not workspace_id:
        return None
    path = workspaces_dir / workspace_id
    if not path.exists() or not path.is_dir():
        return None
    meta = _load_json(path / "workspace.json")
    prompt_file = str(meta.get("prompt_file") or "WORKSPACE.md")
    prompt = _read_text(path / prompt_file)
    name = str(meta.get("name") or workspace_id).strip() or workspace_id
    skill_ids = [str(x).strip() for x in (meta.get("skills") or []) if str(x).strip()]
    if not skill_ids:
        skill_ids = _read_list(path / "SKILLS.txt")
    tool_allowlist = [str(x).strip() for x in (meta.get("tools") or []) if str(x).strip()]
    if not tool_allowlist:
        tool_allowlist = _read_list(path / "TOOLS.txt")
    return WorkspaceDefinition(
        id=workspace_id,
        name=name,
        prompt=prompt,
        skill_ids=skill_ids,
        tool_allowlist=tool_allowlist,
    )


def list_workspaces(workspaces_dir: Path) -> List[str]:
    if not workspaces_dir.exists():
        return []
    return [p.name for p in workspaces_dir.iterdir() if p.is_dir()]


def list_skills(skills_dir: Path) -> List[str]:
    if not skills_dir.exists():
        return []
    return [p.name for p in skills_dir.iterdir() if p.is_dir()]


def build_skills_prompt(skills: List[SkillDefinition]) -> str:
    if not skills:
        return ""
    blocks: List[str] = []
    for skill in skills:
        title = f"Skill: {skill.name}"
        detail = (skill.prompt or "").strip()
        if detail:
            blocks.append(f"{title}\n{detail}")
        else:
            blocks.append(title)
    return "\n\n".join(blocks).strip()


def merge_tool_allowlists(
    workspace_allowlist: List[str],
    skill_allowlists: List[List[str]],
) -> Optional[set[str]]:
    base = {name for name in (workspace_allowlist or []) if name}

    # If the workspace doesn't define an allowlist, treat as unrestricted.
    # Skills can still restrict in that case, but cannot "expand" beyond an explicit workspace ceiling.
    if not base:
        skill_union = {name for allowlist in (skill_allowlists or []) for name in (allowlist or []) if name}
        return skill_union or None

    non_empty_skills = [a for a in (skill_allowlists or []) if a]
    if not non_empty_skills:
        return base or None

    skill_union = {name for allowlist in non_empty_skills for name in allowlist if name}
    restricted = base.intersection(skill_union)
    return restricted


# ── Skill → Tool Bridge ────────────────────────────────────────────

_loaded_skill_tool_modules: set[str] = set()


def load_skill_tools(skill_dir: Path) -> List[str]:
    """Load custom tools from a skill's ``tools.py`` file.

    If ``<skill_dir>/tools.py`` exists, it is dynamically imported.
    Any functions decorated with ``@ToolRegistry.register(...)`` inside
    that module will auto-register into the global Tool Registry.

    Args:
        skill_dir: Path to the skill directory (e.g. ``skills/weather/``).

    Returns:
        List of tool names that were registered by this skill's tools module.
        Empty list if no ``tools.py`` exists or on import error.
    """
    tools_file = skill_dir / "tools.py"
    if not tools_file.exists():
        return []

    module_key = str(tools_file.resolve())
    if module_key in _loaded_skill_tool_modules:
        # Already loaded — return names from registry that match this skill
        logger.debug(f"Skill tools already loaded: {skill_dir.name}")
        return []

    # Capture registry state before import to detect new registrations
    try:
        from agent.tool_registry import ToolRegistry
        before_names = set(ToolRegistry.get_names())
    except ImportError:
        logger.warning("ToolRegistry not available — skipping skill tool loading")
        return []

    # Dynamically import the skill's tools module
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location(
            f"skill_tools_{skill_dir.name}",
            str(tools_file),
        )
        if spec is None or spec.loader is None:
            logger.warning(f"Could not load spec for {tools_file}")
            return []
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _loaded_skill_tool_modules.add(module_key)

        # Detect newly registered tools
        after_names = set(ToolRegistry.get_names())
        new_tools = sorted(after_names - before_names)

        # Enforce policy_flags — remove tools whose config flags aren't enabled
        if new_tools:
            try:
                from config import config as _cfg
            except ImportError:
                _cfg = None
            approved: list[str] = []
            for tname in new_tools:
                flags = ToolRegistry.get_permission_flags(tname)
                if flags and _cfg:
                    missing = [f for f in flags if not getattr(_cfg, f.lower(), False)]
                    if missing:
                        logger.debug(
                            f"Skill tool '{tname}' blocked: missing config flags {missing}"
                        )
                        # Remove from registry so LLM can't access it
                        ToolRegistry._entries.pop(tname, None)
                        continue
                approved.append(tname)
            if approved:
                logger.info(f"Skill '{skill_dir.name}' registered tools: {approved}")
            return approved
        return new_tools  # empty list — no new tools registered

    except Exception as exc:
        logger.warning(f"Failed to load skill tools from {tools_file}: {exc}")
        return []


_loaded_skill_plugin_modules: set[str] = set()


def load_skill_plugin(skill_dir: Path) -> bool:
    """Load a pipeline plugin from a skill's ``plugin.py`` file.

    If ``<skill_dir>/plugin.py`` exists, it is dynamically imported.
    The module should register plugins via ``PluginRegistry.register(MyPlugin())``.

    Args:
        skill_dir: Path to the skill directory.

    Returns:
        True if a plugin module was loaded, False otherwise.
    """
    plugin_file = skill_dir / "plugin.py"
    if not plugin_file.exists():
        return False

    module_key = str(plugin_file.resolve())
    if module_key in _loaded_skill_plugin_modules:
        logger.debug(f"Skill plugin already loaded: {skill_dir.name}")
        return False

    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location(
            f"skill_plugin_{skill_dir.name}",
            str(plugin_file),
        )
        if spec is None or spec.loader is None:
            logger.warning(f"Could not load spec for {plugin_file}")
            return False
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _loaded_skill_plugin_modules.add(module_key)
        logger.info(f"Loaded pipeline plugin from skill '{skill_dir.name}'")
        return True

    except Exception as exc:
        logger.warning(f"Failed to load skill plugin from {plugin_file}: {exc}")
        return False

