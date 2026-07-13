"""Truthful skill availability classification for production."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.skill_contract import SkillManifest, SkillStatus
from agent.skills_registry import SkillsRegistry
from agent.tool_registry import ToolRegistry


def _ensure_tools_registered() -> None:
    """Best-effort load of production tool modules so availability is not empty."""
    try:
        import agent.video_editor.tools  # noqa: F401
    except Exception:
        pass
    try:
        from agent.tools import TOOL_METADATA, get_available_tools

        ToolRegistry.register_from_metadata(get_available_tools(), TOOL_METADATA)
    except Exception:
        pass


def classify_skill(
    manifest: SkillManifest,
    *,
    available_capabilities: Optional[set[str]] = None,
    available_artifacts: Optional[set[str]] = None,
) -> Dict[str, Any]:
    caps = set(available_capabilities or set())
    arts = set(available_artifacts or set())
    _ensure_tools_registered()
    registered = set(ToolRegistry.get_names())
    status = "executable"
    reasons: List[str] = []

    if manifest.status == SkillStatus.DISABLED:
        return _row(manifest, "disabled", ["status:disabled"])
    if manifest.status in {SkillStatus.INVALID, SkillStatus.DRAFT, SkillStatus.PROPOSED}:
        return _row(manifest, "invalid", [f"status:{manifest.status.value}"])
    if manifest.status == SkillStatus.DEPRECATED:
        return _row(manifest, "deprecated", ["status:deprecated"])
    if not manifest.executable and not manifest.prompt:
        return _row(manifest, "invalid", ["not_executable"])
    if not manifest.implementation_entry and not manifest.prompt and not manifest.required_tools:
        return _row(manifest, "prompt_only", ["no_implementation_entry"])

    missing_tools = [t for t in manifest.required_tools if t and t not in registered]
    is_video_domain = str(manifest.implementation_entry or "").startswith("video_domain:")
    if missing_tools and not manifest.prompt and not is_video_domain:
        return _row(manifest, "blocked_missing_tool", [f"tool:{t}" for t in missing_tools])
    if missing_tools and not is_video_domain:
        # Any required tool absent → not fully executable (prompt packaging is not authority).
        if not any(t in registered for t in (manifest.required_tools or [])):
            return _row(manifest, "prompt_only", [f"tool:{t}" for t in missing_tools])
        return _row(manifest, "blocked_missing_tool", [f"tool:{t}" for t in missing_tools])

    for cap in manifest.required_models or []:
        if caps and cap not in caps:
            # Generative/analysis caps only when inventory provided
            if cap in {"text_to_video", "transcription", "analysis", "video_understanding", "scene_detection"}:
                return _row(manifest, "blocked_missing_model", [f"capability:{cap}"])
    for art in manifest.required_artifacts or []:
        if arts is not None and art and art not in arts and art not in {"silence_detection", "transcription"}:
            # Only hard-block when artifacts inventory is non-empty and required kinds missing
            pass
    if arts is not None and manifest.required_artifacts:
        missing_art = [a for a in manifest.required_artifacts if a and a not in arts]
        # Skills that need analysis before mutate: blocked until artifact exists
        if missing_art and any(x in missing_art for x in ("silence", "transcript", "silence_detection", "transcription")):
            if any(m in ("silence_detection", "transcription", "silence", "transcript") for m in missing_art):
                return _row(manifest, "blocked_missing_artifact", [f"artifact:{a}" for a in missing_art])

    # Prompt-only packages (no tools, no video_domain impl)
    if (
        manifest.prompt
        and not manifest.required_tools
        and not str(manifest.implementation_entry or "").startswith("video_domain:")
        and not (manifest.package_path and Path_has_tools(manifest.package_path))
    ):
        return _row(manifest, "prompt_only", ["prompt_package_without_tools"])

    return _row(manifest, "executable", reasons)


def Path_has_tools(package_path: str) -> bool:
    try:
        from pathlib import Path

        return (Path(package_path) / "tools.py").exists()
    except Exception:
        return False


def _row(manifest: SkillManifest, status: str, reasons: List[str]) -> Dict[str, Any]:
    # Operator classification for prompt-only / dead packages
    disposition = "production"
    if status == "prompt_only":
        if "prompt_package_without_tools" in reasons or any(str(r).startswith("tool:") for r in reasons):
            disposition = "intentionally_prompt_only_or_missing_tools"
        else:
            disposition = "documentation_or_helper"
    elif status == "disabled":
        disposition = "disabled"
    elif status in {"invalid", "deprecated"}:
        disposition = status
    elif str(status).startswith("blocked"):
        disposition = "blocked_until_capability"
    return {
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "origin": manifest.origin.value if hasattr(manifest.origin, "value") else str(manifest.origin),
        "status": status,
        "manifest_status": manifest.status.value if hasattr(manifest.status, "value") else str(manifest.status),
        "executable": status == "executable",
        "reasons": reasons,
        "disposition": disposition,
        "required_tools": list(manifest.required_tools or []),
        "required_models": list(manifest.required_models or []),
        "required_artifacts": list(manifest.required_artifacts or []),
    }


def audit_all_skills(
    *,
    available_capabilities: Optional[set[str]] = None,
    available_artifacts: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    SkillsRegistry.refresh()
    rows = [
        classify_skill(
            m,
            available_capabilities=available_capabilities,
            available_artifacts=available_artifacts,
        )
        for m in SkillsRegistry.list_manifests(include_disabled=True)
    ]
    return sorted(rows, key=lambda r: r["id"])
