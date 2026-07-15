"""
Canonical skills and workspace registry for EchoSpeak.

This module is the single package-skill owner. Video domain skills are bridged
in via SkillsRegistry.refresh() — they do not form a competing production
selection path. Prompt-only packages without reachable tools are marked invalid.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional, Dict, List

from loguru import logger

from agent.skill_contract import (
    SkillManifest,
    SkillOrigin,
    SkillStatus,
)


@dataclass
class SkillDefinition:
    """Backward-compatible prompt skill projection used by core.py prompts."""

    id: str
    name: str
    description: str
    prompt: str
    tool_allowlist: List[str] = field(default_factory=list)
    version: str = "1.0.0"
    status: str = "installed"
    executable: bool = True
    validation_errors: List[str] = field(default_factory=list)


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


def _registered_tool_names() -> set[str]:
    try:
        from agent.tool_registry import ToolRegistry

        return set(ToolRegistry.get_names())
    except Exception:
        return set()


def package_to_manifest(entry: Path, meta: Dict[str, object], prompt: str) -> SkillManifest:
    """Build a validated SkillManifest from a filesystem skill package."""
    skill_id = entry.name
    disabled = (entry / ".disabled").exists()
    experimental = (entry / ".experimental").exists() or bool(meta.get("experimental"))
    draft = (entry / ".draft").exists() or str(meta.get("status") or "") == "draft"
    tool_allowlist = [str(x).strip() for x in (meta.get("tools") or meta.get("required_tools") or []) if str(x).strip()]
    if not tool_allowlist:
        tool_allowlist = _read_list(entry / "TOOLS.txt")
    optional_tools = [str(x).strip() for x in (meta.get("optional_tools") or []) if str(x).strip()]
    accepted = [str(x).strip() for x in (meta.get("accepted_intents") or meta.get("intents") or []) if str(x).strip()]
    modes = [str(x).strip() for x in (meta.get("supported_modes") or meta.get("modes") or []) if str(x).strip()]
    if not modes:
        # Infer domain from id / tools
        if skill_id in {"web_search"} or "web_search" in tool_allowlist:
            modes = ["chat", "research"]
        else:
            modes = ["chat"]

    registered = _registered_tool_names()
    missing = [t for t in tool_allowlist if t and t not in registered]
    reachable = [t for t in tool_allowlist if t in registered]
    has_impl = bool(prompt) or (entry / "tools.py").exists() or (entry / "plugin.py").exists()
    errors: list[str] = []
    if not prompt and not (entry / "tools.py").exists():
        errors.append("missing_prompt_and_tools_module")
    if tool_allowlist and not reachable:
        errors.append("no_required_tools_registered")
    if (entry / "tools.py").exists() and errors == ["no_required_tools_registered"]:
        # Reviewed package code is the bootstrap source for its declared tools.
        # The bridge revalidates the complete declaration after import.
        errors = []

    if disabled:
        status = SkillStatus.DISABLED
    elif draft:
        status = SkillStatus.DRAFT
    elif experimental:
        status = SkillStatus.EXPERIMENTAL
    elif meta.get("origin") == "built_in" or skill_id in {
        "web_search", "soul", "skill_writer",
    }:
        status = SkillStatus.BUILT_IN
    elif errors and "no_required_tools_registered" in errors:
        status = SkillStatus.NEEDS_DEPENDENCY
    elif errors:
        status = SkillStatus.INVALID
    else:
        status = SkillStatus.INSTALLED

    executable = status in {
        SkillStatus.BUILT_IN,
        SkillStatus.INSTALLED,
        SkillStatus.EXPERIMENTAL,
    } and not errors and has_impl

    # Skill packages that only provide prompts still guide the agent when tools
    # are from the global inventory (e.g. web_search). Treat as executable if
    # prompt exists and not disabled/draft.
    if prompt and status in {SkillStatus.BUILT_IN, SkillStatus.INSTALLED, SkillStatus.EXPERIMENTAL}:
        if not tool_allowlist or reachable or skill_id.startswith("video"):
            executable = not disabled and not draft
            if executable and "no_required_tools_registered" in errors:
                errors = [e for e in errors if e != "no_required_tools_registered"]
                if not errors and status == SkillStatus.NEEDS_DEPENDENCY:
                    status = SkillStatus.INSTALLED

    name = str(meta.get("name") or skill_id).strip() or skill_id
    description = str(meta.get("description") or "").strip() or _derive_description(prompt)
    return SkillManifest(
        id=skill_id,
        version=str(meta.get("version") or "1.0.0"),
        status=status,
        owner=str(meta.get("owner") or "echospeak"),
        origin=SkillOrigin.PACKAGE,
        name=name,
        description=description,
        accepted_intents=accepted or [name.lower()],
        supported_modes=modes,
        required_project_state=[str(x) for x in (meta.get("required_project_state") or [])],
        required_context_fields=[str(x) for x in (meta.get("required_context_fields") or [])],
        required_tools=tool_allowlist,
        optional_tools=optional_tools,
        required_capabilities=[str(x) for x in (meta.get("required_capabilities") or [])],
        required_models=[str(x) for x in (meta.get("required_models") or [])],
        required_artifacts=[str(x) for x in (meta.get("required_artifacts") or [])],
        produced_artifacts=[str(x) for x in (meta.get("produced_artifacts") or [])],
        job_types=[str(x) for x in (meta.get("job_types") or [])],
        operation_templates=list(meta.get("operation_templates") or []),
        permissions=[str(x) for x in (meta.get("permissions") or [])],
        approval_policy=dict(meta.get("approval_policy") or meta.get("approval_rules") or {}),
        verification_rules=[str(x) for x in (meta.get("verification_rules") or [])],
        retry_policy=dict(meta.get("retry_policy") or {}),
        resource_limits=dict(meta.get("resource_limits") or {}),
        dependency_metadata=dict(meta.get("dependency_metadata") or {}),
        license=str(meta.get("license") or ""),
        compatibility_version=str(meta.get("compatibility_version") or "1"),
        implementation_entry=str(meta.get("implementation_entry") or f"package:{skill_id}"),
        prompt=prompt,
        package_path=str(entry),
        project_id=str(meta.get("project_id") or ""),
        tools_reachable=reachable,
        tools_missing=missing,
        validation_errors=errors,
        executable=executable,
    )


def executable_package_manifest(skill_dir: Path) -> Optional[SkillManifest]:
    """Return the import-authorizing manifest, or fail closed before code import."""
    meta = _load_json(skill_dir / "skill.json")
    prompt_file = str(meta.get("prompt_file") or "SKILL.md")
    prompt = _read_text(skill_dir / prompt_file)
    if not prompt:
        logger.warning("Skill '{}' cannot load code: missing prompt/manifest content", skill_dir.name)
        return None
    manifest = package_to_manifest(skill_dir, meta, prompt)
    if (
        (skill_dir / ".disabled").exists()
        or (skill_dir / ".draft").exists()
        or (skill_dir / ".experimental").exists()
        or manifest.status not in {SkillStatus.BUILT_IN, SkillStatus.INSTALLED}
        or not manifest.executable
        or bool(manifest.validation_errors)
    ):
        logger.warning(
            "Skill '{}' code import blocked by lifecycle status={} executable={} errors={}",
            skill_dir.name,
            manifest.status.value,
            manifest.executable,
            manifest.validation_errors,
        )
        return None
    return manifest


def load_skills(skills_dir: Path, *, include_disabled: bool = False) -> Dict[str, SkillDefinition]:
    """Load package skills as SkillDefinition (prompt projection).

    Disabled skills are excluded unless include_disabled=True (for admin listing).
    """
    skills: Dict[str, SkillDefinition] = {}
    if not skills_dir.exists():
        return skills
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        if (entry / ".disabled").exists() and not include_disabled:
            logger.debug(f"Skipping disabled skill: {entry.name}")
            continue
        meta = _load_json(entry / "skill.json")
        prompt_file = str(meta.get("prompt_file") or "SKILL.md")
        prompt = _read_text(entry / prompt_file)
        # restart/ has SKILL.md only — still load if prompt exists
        if not prompt and not meta:
            continue
        if not prompt:
            continue
        manifest = package_to_manifest(entry, meta, prompt)
        if not include_disabled and (
            manifest.status not in {SkillStatus.BUILT_IN, SkillStatus.INSTALLED}
            or not manifest.executable
            or bool(manifest.validation_errors)
        ):
            continue
        skills[manifest.id] = SkillDefinition(
            id=manifest.id,
            name=manifest.name,
            description=manifest.description,
            prompt=manifest.prompt,
            tool_allowlist=manifest.tool_allowlist(),
            version=manifest.version,
            status=manifest.status.value,
            executable=manifest.executable,
            validation_errors=list(manifest.validation_errors),
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

    # Workspace allowlists are the runtime policy ceiling. Skill allowlists describe
    # what each loaded skill may use, but they must not shrink the workspace's own
    # safe defaults. The old intersection behavior hid tools like web_search and
    # project_update_context whenever active skills did not all list them.
    if not base:
        skill_union = {name for allowlist in (skill_allowlists or []) for name in (allowlist or []) if name}
        return skill_union or None

    return base or None


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
    manifest = executable_package_manifest(skill_dir)
    if manifest is None:
        return []

    module_key = str(tools_file.resolve())
    if module_key in _loaded_skill_tool_modules:
        # Already loaded — return names from registry that match this skill
        logger.debug(f"Skill tools already loaded: {skill_dir.name}")
        return []

    # Capture registry state before import to detect new registrations
    try:
        from agent.tool_registry import ToolRegistry
        before_entries = ToolRegistry.get_all()
        before_names = set(before_entries)
    except ImportError:
        logger.warning("ToolRegistry not available — skipping skill tool loading")
        return []

    # Dynamically import the skill's tools module
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location(f"skill_tools_{skill_dir.name}", str(tools_file))
        if spec is None or spec.loader is None:
            logger.warning(f"Could not load spec for {tools_file}")
            return []
        module = importlib.util.module_from_spec(spec)
        with ToolRegistry.registration_scope(f"skill:{manifest.id}", reject_conflicts=True):
            spec.loader.exec_module(module)
        _loaded_skill_tool_modules.add(module_key)

        # Detect newly registered tools
        after_names = set(ToolRegistry.get_names())
        new_tools = sorted(after_names - before_names)

        declared = set(manifest.required_tools) | set(manifest.optional_tools)
        undeclared = sorted(set(new_tools) - declared)
        missing = sorted(set(manifest.required_tools) - after_names)
        if undeclared or missing:
            ToolRegistry._entries.clear()
            ToolRegistry._entries.update(before_entries)
            logger.warning(
                "Skill '{}' registration rejected: undeclared={} missing={}",
                skill_dir.name,
                undeclared,
                missing,
            )
            return []

        # Package code is always treated as an action-capable authority surface.
        # Runtime policy and approval gates may narrow it further, never widen it.
        for tool_name in new_tools:
            entry = ToolRegistry.get(tool_name)
            if entry is not None:
                ToolRegistry._entries[tool_name] = replace(
                    entry,
                    is_action=True,
                    risk_level="destructive" if entry.risk_level == "destructive" else "moderate",
                    owner=f"skill:{manifest.id}",
                )

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
        try:
            ToolRegistry._entries.clear()
            ToolRegistry._entries.update(before_entries)
        except Exception:
            pass
        logger.warning(f"Failed to load skill tools from {tools_file}: {exc}")
        return []


_loaded_skill_plugin_modules: set[str] = set()


class SkillsRegistry:
    """Canonical in-process registry of SkillManifest rows.

    Package skills (apps/backend/skills) + bridged video domain skills.
    There is one selection owner: this registry + agent.skill_selection.
    """

    _lock = threading.RLock()
    _manifests: Dict[str, SkillManifest] = {}
    _loaded_root: str = ""

    @classmethod
    def clear(cls) -> None:
        with cls._lock:
            cls._manifests = {}
            cls._loaded_root = ""

    @classmethod
    def refresh(cls, skills_dir: Optional[Path] = None) -> Dict[str, SkillManifest]:
        with cls._lock:
            if skills_dir is None:
                try:
                    from config import config

                    skills_dir = Path(getattr(config, "skills_dir", "") or "").expanduser()
                except Exception:
                    skills_dir = Path("skills")
            root = str(skills_dir.resolve()) if skills_dir.exists() else str(skills_dir)
            manifests: Dict[str, SkillManifest] = {}
            if skills_dir.exists():
                for entry in sorted(skills_dir.iterdir()):
                    if not entry.is_dir():
                        continue
                    meta = _load_json(entry / "skill.json")
                    prompt_file = str(meta.get("prompt_file") or "SKILL.md")
                    prompt = _read_text(entry / prompt_file)
                    if not prompt and not meta:
                        continue
                    if not prompt:
                        # Invalid package without prompt
                        manifests[entry.name] = SkillManifest(
                            id=entry.name,
                            name=entry.name,
                            description="Invalid skill package (missing SKILL.md)",
                            status=SkillStatus.INVALID,
                            origin=SkillOrigin.PACKAGE,
                            validation_errors=["missing_prompt"],
                            executable=False,
                            package_path=str(entry),
                        )
                        continue
                    manifests[entry.name] = package_to_manifest(entry, meta, prompt)


            cls._manifests = manifests
            cls._loaded_root = root
            return dict(manifests)

    @classmethod
    def get(cls, skill_id: str) -> Optional[SkillManifest]:
        with cls._lock:
            if not cls._manifests:
                cls.refresh()
            return cls._manifests.get(str(skill_id or "").strip())

    @classmethod
    def list_manifests(cls, *, include_disabled: bool = False) -> List[SkillManifest]:
        with cls._lock:
            if not cls._manifests:
                cls.refresh()
            rows = list(cls._manifests.values())
        if not include_disabled:
            rows = [m for m in rows if m.status != SkillStatus.DISABLED]
        return sorted(rows, key=lambda m: m.id)

    @classmethod
    def list_skills(cls) -> List[dict[str, Any]]:
        """A2A / API projection."""
        return [
            {
                "name": m.name,
                "description": m.description,
                "id": m.id,
                "version": m.version,
                "status": m.status.value,
                "tags": list(m.supported_modes),
                "executable": m.executable,
            }
            for m in cls.list_manifests()
        ]

    @classmethod
    def executable_manifests(cls, *, mode: str = "") -> List[SkillManifest]:
        rows = [m for m in cls.list_manifests() if m.executable]
        if mode:
            rows = [m for m in rows if not m.supported_modes or mode in m.supported_modes]
        return rows

    def __init__(self, skills_dir: Optional[Path] = None):
        # Instance API for a2a.py compatibility
        self.refresh(skills_dir)


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
    manifest = executable_package_manifest(skill_dir)
    if manifest is None:
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

