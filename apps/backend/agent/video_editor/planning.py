"""Agent planning path for video editing.

user request → understand context → capabilities → tools/skills → structured plan
→ missing requirements → propose ops/jobs → (approval outside this module).

Plans never mutate the editor. Apply is a separate governed step.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from agent.video_editor.capabilities import capability_available
from agent.video_editor.models import (
    EditOperation,
    VideoAgentPlan,
    VideoAgentPlanStep,
    VideoEditorContext,
)
from agent.video_editor.operations import stage_transaction, VideoOperationError
from agent.video_editor.skills import VideoSkillDefinition, VideoSkillRegistry, ensure_builtin_video_skills
from agent.video_editor.store import VideoEditorStore, VideoStoreError, get_video_editor_store
from agent.video_editor.tool_catalog import allowed_operation_types


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def select_skill(objective: str, skill_id: str = "") -> Optional[VideoSkillDefinition]:
    ensure_builtin_video_skills()
    if skill_id:
        return VideoSkillRegistry.get(skill_id)
    matches = VideoSkillRegistry.match_intention(objective)
    return matches[0] if matches else None


def _research_queries(objective: str, skill: Optional[VideoSkillDefinition]) -> list[str]:
    if skill is not None and skill.research_enabled:
        return [objective]
    text = _normalize(objective)
    if any(token in text for token in ("research", "script", "fact check", "sources", "outline")):
        return [objective]
    return []


def _detect_missing(
    context: VideoEditorContext,
    skill: Optional[VideoSkillDefinition],
    *,
    operations: list[EditOperation],
    job_specs: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    required_caps: list[str] = []
    required_perms: list[str] = []
    missing: list[str] = []

    if skill is not None:
        required_caps.extend(skill.required_models)
        required_caps.extend(skill.required_analysis)
        required_perms.extend(skill.permissions)
        for job_kind in skill.job_requirements:
            required_caps.append(job_kind if job_kind in {"analysis", "transcription", "generation", "export", "render"} else job_kind)

    if operations:
        required_caps.append("timeline_mutation")
        required_perms.extend(["video_agent_edits", "system_actions"])
        required_caps.append("approvals")

    for spec in job_specs:
        cap = str(spec.get("capability") or spec.get("kind") or "").strip()
        if cap:
            required_caps.append(cap)

    # Deduplicate while preserving order.
    def _uniq(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            key = str(item or "").strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out

    required_caps = _uniq(required_caps)
    required_perms = _uniq(required_perms)

    for cap in required_caps:
        if not capability_available(context.capabilities, cap):
            missing.append(f"capability:{cap}")

    if "video_agent_edits" in required_perms and not context.authority.video_agent_edits:
        missing.append("permission:video_agent_edits")
    if "system_actions" in required_perms and not context.authority.system_actions:
        missing.append("permission:system_actions")
    if operations and not context.authority.mutation_allowed:
        missing.append("authority:mutation_not_allowed")
    if context.authority.pending_approval_id and operations:
        missing.append("authority:pending_approval_blocks_new_proposal")
    if not context.document_id:
        missing.append("document:none_active")
    if not context.assets and any(op.operation_type.value in {"insert_clip"} for op in operations):
        missing.append("media:no_assets")

    return required_caps, required_perms, missing


def _validate_operations_against_document(
    store: VideoEditorStore,
    context: VideoEditorContext,
    operations: list[EditOperation],
) -> list[EditOperation]:
    if not operations:
        return []
    if not context.document_id:
        raise VideoStoreError("Cannot validate operations without an active video document")
    document = store.get_document(context.project_id, context.document_id)
    allowed = set(allowed_operation_types())
    cleaned: list[EditOperation] = []
    for op in operations:
        if op.operation_type.value not in allowed:
            raise VideoStoreError(f"Operation type is not in the formal video tool registry: {op.operation_type.value}")
        if op.expected_revision != document.revision:
            raise VideoStoreError(
                f"Stale operation revision: expected {document.revision}, got {op.expected_revision}"
            )
        cleaned.append(op.model_copy(update={"source": "agent", "expected_revision": document.revision}))
    try:
        stage_transaction(document, cleaned)
    except VideoOperationError as exc:
        raise VideoStoreError(f"Operation plan failed validation: {exc}") from exc
    return cleaned


def _default_job_specs(skill: Optional[VideoSkillDefinition], objective: str) -> list[dict[str, Any]]:
    if skill is None:
        return []
    specs: list[dict[str, Any]] = []
    for kind in skill.job_requirements:
        capability = skill.required_models[0] if skill.required_models else kind
        specs.append(
            {
                "kind": kind if kind in {"analysis", "generation", "render", "proxy", "transcription", "export", "preview"} else "analysis",
                "capability": capability,
                "parameters": {"objective": objective, "skill_id": skill.id},
                "idempotency_hint": f"{skill.id}:{kind}",
            }
        )
    return specs


def plan_video_request(
    *,
    context: VideoEditorContext,
    objective: str,
    skill_id: str = "",
    operations: Optional[list[EditOperation]] = None,
    store: Optional[VideoEditorStore] = None,
) -> VideoAgentPlan:
    """Build a resumable structured plan. Does not create approvals or mutate state."""
    objective = str(objective or "").strip()
    if not objective:
        raise VideoStoreError("A video plan requires a non-empty objective")

    skill = select_skill(objective, skill_id)
    ops = list(operations or [])
    video_store = store or get_video_editor_store()
    if ops:
        ops = _validate_operations_against_document(video_store, context, ops)

    job_specs = _default_job_specs(skill, objective)
    research = _research_queries(objective, skill)
    required_caps, required_perms, missing = _detect_missing(
        context, skill, operations=ops, job_specs=job_specs
    )

    steps: list[VideoAgentPlanStep] = [
        VideoAgentPlanStep(
            kind="inspect",
            tool_name="video_get_editor_context",
            description="Load structured editor context (document, revision, selection, jobs, authority).",
        )
    ]
    if skill is not None:
        steps.append(
            VideoAgentPlanStep(
                kind="skill",
                tool_name="video_list_skills",
                description=f"Use VideoSkill `{skill.id}`: {skill.name}",
                payload={"skill_id": skill.id},
            )
        )
    steps.append(
        VideoAgentPlanStep(
            kind="inspect",
            tool_name="video_list_capabilities",
            description="Identify required capabilities and missing models/permissions.",
        )
    )
    for query in research:
        steps.append(
            VideoAgentPlanStep(
                kind="research",
                tool_name="web_search",
                description="Gather structured research inputs for the video plan.",
                payload={"query": query},
            )
        )
    for spec in job_specs:
        steps.append(
            VideoAgentPlanStep(
                kind="job",
                tool_name="video_submit_job",
                description=f"Submit durable {spec.get('kind')} job ({spec.get('capability')}).",
                requires_approval=bool(skill and skill.approval_rules.get("requires_approval")),
                payload=spec,
            )
        )
    if ops:
        steps.append(
            VideoAgentPlanStep(
                kind="operation",
                tool_name="video_propose_operations",
                description=f"Propose {len(ops)} validated timeline operation(s) bound to revision {context.document_revision}.",
                requires_approval=True,
                payload={"operation_count": len(ops), "expected_revision": context.document_revision},
            )
        )
        steps.append(
            VideoAgentPlanStep(
                kind="approval",
                tool_name="video_apply_transaction",
                description="Apply only after exact ApprovalRecord confirmation and revision revalidation.",
                requires_approval=True,
            )
        )
        steps.append(
            VideoAgentPlanStep(
                kind="verify",
                tool_name="video_inspect_timeline",
                description="Verify new revision, operation outcomes, and projection truth.",
            )
        )

    verification = list(skill.verification_rules if skill else [])
    if ops and "revision_advanced" not in verification:
        verification.append("revision_advanced")
    if job_specs and "job_terminal_truth" not in verification:
        verification.append("job_terminal_truth")

    status: str = "blocked" if missing else "ready"
    if not ops and not job_specs and not research:
        # Intent-only plan: still useful for continuation, but not executable yet.
        steps.append(
            VideoAgentPlanStep(
                kind="tool",
                tool_name="video_plan_request",
                description="No concrete operations or jobs were supplied; refine with inspect results or explicit ops.",
            )
        )

    return VideoAgentPlan(
        project_id=context.project_id,
        session_id=context.session_id,
        document_id=context.document_id,
        objective=objective,
        expected_revision=context.document_revision,
        skill_id=skill.id if skill else "",
        status=status,  # type: ignore[arg-type]
        steps=steps,
        operations=ops,
        job_specs=job_specs,
        research_queries=research,
        required_capabilities=required_caps,
        required_permissions=required_perms,
        missing_requirements=missing,
        verification_rules=verification,
        resumable=True,
    )
