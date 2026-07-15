"""Deterministic skill selection — model may rank; runtime validates executability."""

from __future__ import annotations

import re
from typing import Any, Optional

from agent.skill_contract import (
    EXECUTABLE_STATUSES,
    SkillManifest,
    SkillSelectionOutcome,
    SkillSelectionResult,
    SkillStatus,
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


# Direct structured video ops that should not force a multi-step skill.
_DIRECT_VIDEO_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(split|cut)\b.{0,40}\b(clip|playhead|here|at)\b"), "video_propose_operations"),
    (re.compile(r"\b(trim|shorten)\b.{0,30}\bclip\b"), "video_propose_operations"),
    (re.compile(r"\b(move|nudge)\b.{0,30}\bclip\b"), "video_propose_operations"),
    (re.compile(r"\b(delete|remove)\b.{0,20}\b(this |the |selected )?clip\b"), "video_propose_operations"),
    # volume/mute either before or after "clip"/"selected" (e.g. "selected clip volume to 50%")
    (re.compile(r"\b(volume|gain|mute)\b.{0,40}\b(clip|audio|selected)\b"), "video_propose_operations"),
    (re.compile(r"\b(selected\s+)?clip\b.{0,40}\b(volume|gain|mute)\b"), "video_propose_operations"),
    (re.compile(r"\bset\s+(?:the\s+)?(?:selected\s+)?(?:clip\s+)?volume\b"), "video_propose_operations"),
    (re.compile(r"\b(reframe|crop|scale|zoom)\b.{0,20}\b(clip|selected)?\b"), "video_propose_operations"),
]


def detect_direct_tool(user_text: str, *, domain: str = "") -> Optional[str]:
    text = _normalize(user_text)
    if not text:
        return None
    # Multi-step verbs force skill path.
    if re.search(r"\b(and then|then |also |after that|workflow|pipeline)\b", text):
        return None
    if re.search(
        r"\b(remove silence|silence|silent|captions?|subtitles?|b-?roll|highlight|rough cut|research)\b",
        text,
    ):
        return None
    for pattern, tool in _DIRECT_VIDEO_PATTERNS:
        if pattern.search(text):
            return tool
    return None


def _intent_score(manifest: SkillManifest, text: str) -> float:
    if not text:
        return 0.0
    score = 0.0
    for intent in manifest.accepted_intents:
        token = _normalize(intent)
        if not token:
            continue
        if token in text:
            score = max(score, 1.0 if len(token) > 8 else 0.85)
        else:
            # Token overlap for multi-word intents
            parts = [p for p in token.split() if len(p) > 2]
            if parts and all(p in text for p in parts):
                score = max(score, 0.75)
            # Stem-ish: "silent" matches intent containing "silence"
            for part in parts:
                if len(part) >= 5 and part[:5] in text:
                    score = max(score, 0.7)
    # Name/id soft match
    for label in (manifest.id, manifest.name):
        low = _normalize(label).replace("_", " ")
        if low and low in text:
            score = max(score, 0.55)
        # video_remove_silence ↔ "remove" + "silence/silent"
        tokens = [t for t in low.replace("-", " ").split() if len(t) > 3]
        if len(tokens) >= 2 and sum(1 for t in tokens if t in text or t[:5] in text) >= 2:
            score = max(score, 0.72)
    return score


def _validate_executable(
    manifest: SkillManifest,
    *,
    available_tools: set[str],
    available_capabilities: set[str],
    available_artifacts: set[str],
    permissions: set[str],
) -> tuple[SkillSelectionOutcome, list[str]]:
    missing: list[str] = []
    if manifest.status == SkillStatus.DISABLED:
        return SkillSelectionOutcome.DISABLED, ["status:disabled"]
    if manifest.status in {SkillStatus.INVALID, SkillStatus.DRAFT, SkillStatus.PROPOSED}:
        return SkillSelectionOutcome.UNAVAILABLE, [f"status:{manifest.status.value}"]
    if manifest.status == SkillStatus.UNAVAILABLE:
        return SkillSelectionOutcome.UNAVAILABLE, ["status:unavailable"]
    if not manifest.executable and manifest.status not in EXECUTABLE_STATUSES:
        return SkillSelectionOutcome.UNAVAILABLE, ["not_executable"]
    # Prompt-only packages must never be selected as if they run tools.
    if not (manifest.required_tools or []) and not str(manifest.implementation_entry or "").startswith("video_domain:"):
        from pathlib import Path

        has_tools_py = False
        try:
            has_tools_py = bool(manifest.package_path and (Path(manifest.package_path) / "tools.py").exists())
        except Exception:
            has_tools_py = False
        if not has_tools_py and manifest.prompt:
            return SkillSelectionOutcome.UNAVAILABLE, ["prompt_only"]

    for tool in manifest.required_tools:
        if tool not in available_tools and tool not in set(manifest.tools_reachable or []):
            # Service-owned video tools may be registered but filtered from turn inventory.
            if tool.startswith("video_") and tool in available_tools:
                continue
            if tool not in available_tools:
                missing.append(f"tool:{tool}")
    if any(m.startswith("tool:") for m in missing):
        return SkillSelectionOutcome.BLOCKED_MISSING_TOOL, missing

    for cap in [*manifest.required_capabilities, *manifest.required_models]:
        # Capability tokens; if inventory is empty, do not block on unknown map.
        if available_capabilities and cap not in available_capabilities:
            if cap in {"approvals", "research"}:
                continue
            missing.append(f"capability:{cap}")
    if any(m.startswith("capability:") for m in missing):
        # Model-backed skills block honestly when generative/analysis missing.
        modelish = [m for m in missing if m.startswith("capability:")]
        if modelish:
            return SkillSelectionOutcome.BLOCKED_MISSING_MODEL, missing

    for art in manifest.required_artifacts:
        if art and art not in available_artifacts:
            missing.append(f"artifact:{art}")
    if any(m.startswith("artifact:") for m in missing):
        return SkillSelectionOutcome.BLOCKED_MISSING_ARTIFACT, missing

    for perm in manifest.permissions:
        if perm and permissions and perm not in permissions:
            # Common aliases
            aliases = {"system_actions": {"system_actions", "ENABLE_SYSTEM_ACTIONS"}}
            ok = perm in permissions or any(a in permissions for a in aliases.get(perm, ()))
            if not ok:
                missing.append(f"permission:{perm}")
    if any(m.startswith("permission:") for m in missing):
        return SkillSelectionOutcome.BLOCKED_PERMISSION, missing

    if manifest.validation_errors:
        return SkillSelectionOutcome.UNAVAILABLE, [f"validation:{e}" for e in manifest.validation_errors[:4]]

    return SkillSelectionOutcome.SELECTED, []


def select_skill(
    *,
    user_text: str,
    manifests: list[SkillManifest],
    available_tools: set[str] | None = None,
    available_capabilities: set[str] | None = None,
    available_artifacts: set[str] | None = None,
    permissions: set[str] | None = None,
    explicit_skill_id: str = "",
    prior_unfinished_skill_id: str = "",
    domain_hint: str = "",
    allow_stale_prior: bool = False,
) -> SkillSelectionResult:
    """Select one skill or direct tool. Never silently reuses a stale prior skill."""
    text = _normalize(user_text)
    tools = set(available_tools or set())
    caps = set(available_capabilities or set())
    arts = set(available_artifacts or set())
    perms = set(permissions or set())

    # Explicit selection wins if valid.
    if explicit_skill_id:
        match = next((m for m in manifests if m.id == explicit_skill_id), None)
        if match is None:
            return SkillSelectionResult(
                outcome=SkillSelectionOutcome.NO_MATCHING_SKILL,
                reason=f"Explicit skill `{explicit_skill_id}` is not registered",
            )
        outcome, missing = _validate_executable(
            match,
            available_tools=tools,
            available_capabilities=caps,
            available_artifacts=arts,
            permissions=perms,
        )
        return SkillSelectionResult(
            outcome=outcome,
            skill_id=match.id if outcome == SkillSelectionOutcome.SELECTED else match.id,
            skill_version=match.version,
            reason=f"Explicit skill selection: {outcome.value}",
            missing_requirements=missing,
            confidence=1.0 if outcome == SkillSelectionOutcome.SELECTED else 0.4,
        )

    # Direct tool for simple deterministic edits.
    direct = detect_direct_tool(user_text, domain=domain_hint)
    if direct:
        return SkillSelectionResult(
            outcome=SkillSelectionOutcome.DIRECT_TOOL_BETTER,
            direct_tool=direct,
            reason="Simple deterministic operation — direct structured tool preferred over skill",
            confidence=0.92,
        )

    scored: list[tuple[float, SkillManifest]] = []
    for manifest in manifests:
        # Domain filter soft
        if domain_hint == "video" and manifest.supported_modes and "video" not in manifest.supported_modes:
            if not manifest.id.startswith("video_"):
                continue
        score = _intent_score(manifest, text)
        if score > 0:
            scored.append((score, manifest))
    scored.sort(key=lambda item: (-item[0], item[1].id))

    if not scored:
        # Prior unfinished only when user clearly continues AND allow_stale_prior.
        if prior_unfinished_skill_id and allow_stale_prior and re.search(
            r"\b(continue|resume|keep going|yes|proceed|retry)\b", text
        ):
            prior = next((m for m in manifests if m.id == prior_unfinished_skill_id), None)
            if prior is not None:
                outcome, missing = _validate_executable(
                    prior, available_tools=tools, available_capabilities=caps,
                    available_artifacts=arts, permissions=perms,
                )
                return SkillSelectionResult(
                    outcome=outcome if outcome != SkillSelectionOutcome.SELECTED else SkillSelectionOutcome.SELECTED,
                    skill_id=prior.id,
                    skill_version=prior.version,
                    reason="Continuation of unfinished skill (explicit continue language)",
                    missing_requirements=missing,
                    confidence=0.7,
                )
        return SkillSelectionResult(
            outcome=SkillSelectionOutcome.NO_MATCHING_SKILL,
            reason="No skill intent matched; use direct tools or propose a new skill",
            confidence=0.2,
        )

    # Ambiguous: two high-scoring different skills
    top_score, top = scored[0]
    near = [m for s, m in scored if s >= top_score - 0.05 and m.id != top.id]
    if near and top_score >= 0.75:
        return SkillSelectionResult(
            outcome=SkillSelectionOutcome.AMBIGUOUS,
            candidates=[top.id, *[m.id for m in near[:4]]],
            reason="Multiple skills match at similar confidence",
            confidence=top_score,
        )

    outcome, missing = _validate_executable(
        top,
        available_tools=tools,
        available_capabilities=caps,
        available_artifacts=arts,
        permissions=perms,
    )
    return SkillSelectionResult(
        outcome=outcome,
        skill_id=top.id,
        skill_version=top.version,
        reason=f"Matched intent for `{top.id}` ({outcome.value})",
        missing_requirements=missing,
        candidates=[m.id for _, m in scored[:5]],
        confidence=top_score if outcome == SkillSelectionOutcome.SELECTED else max(0.3, top_score * 0.5),
    )


def select_composition(
    *,
    user_text: str,
    manifests: list[SkillManifest],
    available_tools: set[str] | None = None,
    available_capabilities: set[str] | None = None,
    available_artifacts: set[str] | None = None,
    permissions: set[str] | None = None,
) -> list[SkillSelectionResult]:
    """Decompose multi-clause requests into ordered child skill selections."""
    text = _normalize(user_text)
    # Split on "and" / commas for composition when multiple skill intents appear.
    clauses = re.split(r"\b(?:and then|then|also|,\s*and|;\s*)\b|,", text)
    clauses = [c.strip() for c in clauses if c and len(c.strip()) > 3]
    if len(clauses) < 2:
        return [select_skill(
            user_text=user_text,
            manifests=manifests,
            available_tools=available_tools,
            available_capabilities=available_capabilities,
            available_artifacts=available_artifacts,
            permissions=permissions,
        )]

    results: list[SkillSelectionResult] = []
    seen: set[str] = set()
    for clause in clauses:
        result = select_skill(
            user_text=clause,
            manifests=manifests,
            available_tools=available_tools,
            available_capabilities=available_capabilities,
            available_artifacts=available_artifacts,
            permissions=permissions,
        )
        key = result.skill_id or result.direct_tool or result.outcome.value
        if key in seen:
            continue
        seen.add(key)
        results.append(result)
    return results or [SkillSelectionResult(outcome=SkillSelectionOutcome.NO_MATCHING_SKILL, reason="empty composition")]
