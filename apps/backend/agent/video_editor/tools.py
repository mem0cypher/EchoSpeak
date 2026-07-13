"""Video tools registered into EchoSpeak's ToolRegistry.

Handlers call the authoritative video domain (store/context/planning/jobs).
Models never rewrite timeline JSON or emit FFmpeg/shell commands.
Mutation tools prepare proposals only — apply remains approval-bound.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import BaseModel, Field
from langchain_core.tools import tool

from agent.tool_registry import ToolRegistry


class VideoSessionProjectArgs(BaseModel):
    session_id: str
    project_id: str


class VideoDocumentArgs(VideoSessionProjectArgs):
    document_id: str = ""


class VideoInspectMediaArgs(VideoSessionProjectArgs):
    document_id: str
    asset_id: str


class VideoPlanRequestArgs(VideoSessionProjectArgs):
    document_id: str
    objective: str
    skill_id: str = ""
    selection: Optional[dict[str, Any]] = None
    operations: list[dict[str, Any]] = Field(default_factory=list)


class VideoProposeOperationsArgs(VideoSessionProjectArgs):
    document_id: str
    objective: str
    operations: list[dict[str, Any]] = Field(min_length=1)
    expected_revision: int = Field(ge=0)


class VideoApplyTransactionArgs(BaseModel):
    session_id: str
    project_id: str
    document_id: str
    transaction_id: str
    plan_id: str
    expected_revision: int
    operation_hash: str


class VideoSubmitJobArgs(VideoSessionProjectArgs):
    document_id: str
    kind: str
    idempotency_key: str
    adapter_id: str = ""
    capability: str = ""
    input_asset_ids: list[str] = Field(default_factory=list)
    expected_revision: Optional[int] = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class VideoJobArgs(VideoSessionProjectArgs):
    document_id: str
    job_id: str


class VideoCreativeMemoryArgs(VideoSessionProjectArgs):
    document_id: str = ""
    preferred_style: str = ""
    output_format: str = ""
    creative_objective: str = ""
    approved_workflow_choices: list[str] = Field(default_factory=list)
    project_conventions: list[str] = Field(default_factory=list)


def _json_ok(payload: dict[str, Any]) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, default=str)


def _json_err(code: str, message: str, **extra: Any) -> str:
    return json.dumps(
        {"ok": False, "error_code": code, "error": message, **extra},
        ensure_ascii=False,
        default=str,
    )


def _thread_state(session_id: str):
    try:
        from agent.state import get_state_store

        return get_state_store().get_thread_state(str(session_id or "").strip())
    except Exception:
        return None


def _authority_check(session_id: str, project_id: str) -> Optional[str]:
    session_id = str(session_id or "").strip()
    project_id = str(project_id or "").strip()
    if not session_id or not project_id:
        return "session_id and project_id are required"
    state = _thread_state(session_id)
    if state is not None and str(getattr(state, "active_project_id", "") or "") not in {"", project_id}:
        if str(getattr(state, "active_project_id", "") or "") != project_id:
            return "Project is not attached to this Session"
    return None


@ToolRegistry.register(
    name="video_get_editor_context",
    description="Return structured editor context for the active Project Session document.",
    category="video_editor",
    risk_level="safe",
    keyword_hints=["video", "timeline", "editor", "clip"],
)
@tool(args_schema=VideoDocumentArgs, description="Return structured editor context for the active Project Session document.")
def video_get_editor_context(session_id: str, project_id: str, document_id: str = "") -> str:
    """Return structured editor context."""
    try:
        err = _authority_check(session_id, project_id)
        if err:
            return _json_err("missing_authority", err)
        from agent.video_editor.context import build_editor_context
        from config import config

        ctx = build_editor_context(
            session_id=session_id,
            project_id=project_id,
            document_id=document_id,
            thread_state=_thread_state(session_id),
            config=config,
        )
        return _json_ok({"tool": "video_get_editor_context", "context": ctx.model_dump(mode="json")})
    except Exception as exc:
        return _json_err("tool_failed", str(exc), tool="video_get_editor_context")


@ToolRegistry.register(
    name="video_inspect_media",
    description="Inspect one media asset (streams, duration, provenance).",
    category="video_editor",
    risk_level="safe",
    keyword_hints=["media", "asset", "probe", "video"],
)
@tool(args_schema=VideoInspectMediaArgs, description="Inspect one media asset (streams, duration, provenance).")
def video_inspect_media(session_id: str, project_id: str, document_id: str, asset_id: str) -> str:
    """Inspect one media asset."""
    try:
        err = _authority_check(session_id, project_id)
        if err:
            return _json_err("missing_authority", err)
        from agent.video_editor.store import get_video_editor_store

        doc = get_video_editor_store().get_document(project_id, document_id)
        asset = next(
            (a for a in [*doc.assets, *doc.generated_assets] if a.id == asset_id),
            None,
        )
        if asset is None:
            return _json_err("not_found", f"asset not found: {asset_id}")
        return _json_ok({"tool": "video_inspect_media", "asset": asset.model_dump(mode="json")})
    except Exception as exc:
        return _json_err("tool_failed", str(exc), tool="video_inspect_media")


@ToolRegistry.register(
    name="video_inspect_timeline",
    description="Inspect timeline tracks, clips, revision, and pending plans.",
    category="video_editor",
    risk_level="safe",
    keyword_hints=["timeline", "tracks", "clips"],
)
@tool(args_schema=VideoDocumentArgs, description="Inspect timeline tracks, clips, revision, and pending plans.")
def video_inspect_timeline(session_id: str, project_id: str, document_id: str = "") -> str:
    """Inspect timeline."""
    try:
        err = _authority_check(session_id, project_id)
        if err:
            return _json_err("missing_authority", err)
        from agent.video_editor.store import get_video_editor_store

        store = get_video_editor_store()
        if document_id:
            doc = store.get_document(project_id, document_id)
        else:
            docs = store.list_documents(project_id)
            if not docs:
                return _json_err("not_found", "No video document on Project")
            doc = docs[0]
        pending = [p.model_dump(mode="json") for p in doc.plans if p.status in {"draft", "proposed"}]
        return _json_ok(
            {
                "tool": "video_inspect_timeline",
                "document_id": doc.id,
                "revision": doc.revision,
                "timeline": doc.timeline.model_dump(mode="json"),
                "pending_plans": pending,
                "job_count": len(doc.jobs),
            }
        )
    except Exception as exc:
        return _json_err("tool_failed", str(exc), tool="video_inspect_timeline")


@ToolRegistry.register(
    name="video_list_capabilities",
    description="Report deterministic and model video capabilities.",
    category="video_editor",
    risk_level="safe",
)
@tool(args_schema=VideoSessionProjectArgs, description="Report deterministic and model video capabilities.")
def video_list_capabilities(session_id: str, project_id: str) -> str:
    """List video capabilities."""
    try:
        err = _authority_check(session_id, project_id)
        if err:
            return _json_err("missing_authority", err)
        from agent.video_editor.capabilities import build_video_capability_report
        from config import config

        state = _thread_state(session_id)
        perms = dict(getattr(state, "permissions", None) or {}) if state else {}
        report = build_video_capability_report(
            authority={
                "system_actions": bool(perms.get("system_actions") or getattr(config, "enable_system_actions", False)),
                "video_agent_edits": bool(perms.get("video_agent_edits") or getattr(config, "allow_video_agent_edits", False)),
                "mutation_allowed": bool(
                    (perms.get("system_actions") or getattr(config, "enable_system_actions", False))
                    and (perms.get("video_agent_edits") or getattr(config, "allow_video_agent_edits", False))
                ),
            }
        )
        return _json_ok({"tool": "video_list_capabilities", "capabilities": report.model_dump(mode="json")})
    except Exception as exc:
        return _json_err("tool_failed", str(exc), tool="video_list_capabilities")


@ToolRegistry.register(
    name="video_list_skills",
    description="List registered VideoSkills.",
    category="video_editor",
    risk_level="safe",
)
@tool(description="List registered VideoSkills.")
def video_list_skills() -> str:
    """List VideoSkills from canonical SkillsRegistry."""
    try:
        from agent.skills_registry import SkillsRegistry

        SkillsRegistry.refresh()
        video = [
            m.model_dump(mode="json")
            for m in SkillsRegistry.list_manifests()
            if m.id.startswith("video_") or "video" in (m.supported_modes or [])
        ]
        return _json_ok({"tool": "video_list_skills", "skills": video, "count": len(video)})
    except Exception as exc:
        return _json_err("tool_failed", str(exc), tool="video_list_skills")


@ToolRegistry.register(
    name="video_plan_request",
    description="Build a structured VideoAgentPlan without mutating the editor.",
    category="video_editor",
    risk_level="safe",
    keyword_hints=["plan", "edit", "video", "cut"],
)
@tool(args_schema=VideoPlanRequestArgs, description="Build a structured VideoAgentPlan without mutating the editor.")
def video_plan_request(
    session_id: str,
    project_id: str,
    document_id: str,
    objective: str,
    skill_id: str = "",
    selection: Optional[dict[str, Any]] = None,
    operations: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Plan a video request without mutation."""
    try:
        err = _authority_check(session_id, project_id)
        if err:
            return _json_err("missing_authority", err)
        from agent.video_editor.context import build_editor_context
        from agent.video_editor.models import EditOperation, EditorSelectionContext
        from agent.video_editor.planning import plan_video_request
        from config import config

        sel = None
        if selection:
            try:
                sel = EditorSelectionContext.model_validate(selection)
            except Exception as exc:
                return _json_err("validation_failed", f"invalid selection: {exc}")
        ops = []
        for raw in operations or []:
            ops.append(EditOperation.model_validate(raw) if not isinstance(raw, EditOperation) else raw)
        ctx = build_editor_context(
            session_id=session_id,
            project_id=project_id,
            document_id=document_id,
            selection=sel,
            thread_state=_thread_state(session_id),
            config=config,
        )
        plan = plan_video_request(
            context=ctx,
            objective=objective,
            skill_id=skill_id,
            operations=ops,
        )
        return _json_ok(
            {
                "tool": "video_plan_request",
                "plan": plan.model_dump(mode="json"),
                "mutates": False,
                "note": "Plan only — propose + approval required before mutation",
            }
        )
    except Exception as exc:
        return _json_err("tool_failed", str(exc), tool="video_plan_request")


def _normalize_model_operations(
    operations: list[dict[str, Any]],
    *,
    expected_revision: int,
) -> list[dict[str, Any]]:
    """Coerce common model shapes into canonical EditOperation dicts.

    Accepts e.g. ``{clip_id, operation: set_volume, parameters: {value: 0.5}}``
    and emits ``{operation_type: set_clip_volume, expected_revision, payload}``.
    """
    out: list[dict[str, Any]] = []
    aliases = {
        "set_volume": "set_clip_volume",
        "volume": "set_clip_volume",
        "set_clip_volume": "set_clip_volume",
        "mute": "set_clip_volume",
        "split": "split_clip",
        "split_clip": "split_clip",
        "delete": "delete_clip",
        "delete_clip": "delete_clip",
        "remove": "delete_clip",
        "remove_clip": "delete_clip",
        "trim": "trim_clip",
        "trim_clip": "trim_clip",
        "move": "move_clip",
        "move_clip": "move_clip",
        "insert": "insert_clip",
        "insert_clip": "insert_clip",
        "add_track": "add_track",
    }
    for raw in operations or []:
        if not isinstance(raw, dict):
            continue
        # Already canonical
        if raw.get("operation_type") or raw.get("payload") is not None:
            item = dict(raw)
            if "expected_revision" not in item:
                item["expected_revision"] = expected_revision
            out.append(item)
            continue
        op_name = str(
            raw.get("operation")
            or raw.get("op")
            or raw.get("type")
            or raw.get("action")
            or ""
        ).strip().lower()
        op_type = aliases.get(op_name, op_name.replace("-", "_"))
        params = dict(raw.get("parameters") or raw.get("args") or raw.get("payload") or {})
        clip_id = str(raw.get("clip_id") or params.get("clip_id") or "").strip()
        track_id = str(raw.get("track_id") or params.get("track_id") or "").strip()
        payload: dict[str, Any] = {}
        if clip_id:
            payload["clip_id"] = clip_id
        if track_id:
            payload["track_id"] = track_id
        if op_type == "set_clip_volume":
            vol = params.get("volume", params.get("value", raw.get("volume", raw.get("value"))))
            if vol is None and op_name == "mute":
                vol = 0.0
            if vol is not None:
                try:
                    v = float(vol)
                    if v > 1.5:
                        v = v / 100.0
                    payload["volume"] = max(0.0, min(4.0, v))
                except (TypeError, ValueError):
                    pass
        elif op_type == "split_clip":
            if params.get("at") is not None:
                payload["at"] = params.get("at")
            if params.get("right_clip_id"):
                payload["right_clip_id"] = params.get("right_clip_id")
            elif raw.get("right_clip_id"):
                payload["right_clip_id"] = raw.get("right_clip_id")
        else:
            # Pass remaining known keys into payload
            for k, v in params.items():
                if k not in payload and k != "clip_id":
                    payload[k] = v
            for k in ("asset_id", "timeline_start", "duration", "source_in"):
                if raw.get(k) is not None and k not in payload:
                    payload[k] = raw.get(k)
        if not op_type:
            continue
        out.append(
            {
                "operation_type": op_type,
                "expected_revision": int(raw.get("expected_revision", expected_revision)),
                "payload": payload,
            }
        )
    return out


@ToolRegistry.register(
    name="video_propose_operations",
    description="Propose validated timeline operations and bind an ApprovalRecord.",
    category="video_editor",
    # Proposal-only: does not mutate timeline. Mutation is gated on
    # video_apply_transaction / consume_video_approval after user confirm.
    is_action=False,
    risk_level="low",
    policy_flags=["ENABLE_SYSTEM_ACTIONS", "ALLOW_VIDEO_AGENT_EDITS"],
    keyword_hints=["propose", "edit", "timeline"],
)
@tool(args_schema=VideoProposeOperationsArgs, description="Propose validated timeline operations and bind an ApprovalRecord.")
def video_propose_operations(
    session_id: str,
    project_id: str,
    document_id: str,
    objective: str,
    operations: list[dict[str, Any]],
    expected_revision: int,
) -> str:
    """Propose timeline operations via the video API service path (approval-bound)."""
    try:
        err = _authority_check(session_id, project_id)
        if err:
            return _json_err("missing_authority", err)
        from agent.video_editor.models import EditOperation
        from agent.video_editor.store import get_video_editor_store, VideoStoreError

        store = get_video_editor_store()
        doc = store.get_document(project_id, document_id)
        if int(expected_revision) != int(doc.revision):
            return _json_err(
                "stale_revision",
                f"expected_revision {expected_revision} != current {doc.revision}",
                current_revision=doc.revision,
            )
        normalized = _normalize_model_operations(list(operations or []), expected_revision=int(doc.revision))
        if not normalized:
            return _json_err("validation_failed", "No valid operations after normalization")
        try:
            ops = [EditOperation.model_validate(o) for o in normalized]
        except Exception as val_exc:
            return _json_err("validation_failed", f"Invalid operations: {val_exc}")
        for op in ops:
            if op.expected_revision != doc.revision:
                return _json_err("stale_revision", "operation expected_revision mismatch")
        # Full production proposal path: transaction + ApprovalRecord + ToolRun.
        from api.video_editor import ProposalRequest, propose_video_transaction_sync

        request = ProposalRequest(
            session_id=session_id,
            project_id=project_id,
            objective=objective,
            operations=ops,
        )
        proposed = propose_video_transaction_sync(document_id, request)
        return _json_ok(
            {
                "tool": "video_propose_operations",
                "transaction_id": (proposed.get("transaction") or {}).get("id"),
                "approval_id": (proposed.get("approval") or {}).get("id"),
                "plan_id": (proposed.get("plan") or {}).get("id"),
                "execution_id": proposed.get("execution_id"),
                "tool_run_id": proposed.get("tool_run_id"),
                "expected_revision": (proposed.get("transaction") or {}).get("expected_revision"),
                "operation_hash": (proposed.get("transaction") or {}).get("operation_hash"),
                "preview": proposed.get("preview"),
                "status": "pending_approval",
                "requires_approval": True,
                "applied": False,
                "note": "ApprovalRecord bound. Confirm via approvals API; no mutation until approved.",
            }
        )
    except Exception as exc:
        return _json_err("tool_failed", str(exc), tool="video_propose_operations")


@ToolRegistry.register(
    name="video_apply_transaction",
    description="Apply one exact approved video timeline transaction.",
    category="video_editor",
    is_action=True,
    risk_level="destructive",
    policy_flags=["ENABLE_SYSTEM_ACTIONS", "ALLOW_VIDEO_AGENT_EDITS"],
)
@tool(args_schema=VideoApplyTransactionArgs, description="Apply one exact approved video timeline transaction.")
def video_apply_transaction(
    session_id: str,
    project_id: str,
    document_id: str,
    transaction_id: str,
    plan_id: str,
    expected_revision: int,
    operation_hash: str,
) -> str:
    """Describe apply boundary — runtime approval consumer owns mutation."""
    return _json_err(
        "approval_required",
        (
            "video_apply_transaction is consumed by the authoritative approval service "
            "after exact ApprovalRecord validation. Models must not force-apply. "
            f"transaction_id={transaction_id} plan_id={plan_id} "
            f"expected_revision={expected_revision} operation_hash={operation_hash[:16]}…"
        ),
        tool="video_apply_transaction",
        session_id=session_id,
        project_id=project_id,
        document_id=document_id,
        requires_approval=True,
        applied=False,
    )


@ToolRegistry.register(
    name="video_submit_job",
    description="Submit a durable video analysis/render/export/generation job.",
    category="video_editor",
    is_action=True,
    risk_level="moderate",
    policy_flags=["ENABLE_SYSTEM_ACTIONS"],
    keyword_hints=["render", "export", "generate", "transcribe", "analyze"],
)
@tool(args_schema=VideoSubmitJobArgs, description="Submit a durable video analysis/render/export/generation job.")
def video_submit_job(
    session_id: str,
    project_id: str,
    document_id: str,
    kind: str,
    idempotency_key: str,
    adapter_id: str = "",
    capability: str = "",
    input_asset_ids: Optional[list[str]] = None,
    expected_revision: Optional[int] = None,
    parameters: Optional[dict[str, Any]] = None,
) -> str:
    """Submit a durable video job."""
    try:
        err = _authority_check(session_id, project_id)
        if err:
            return _json_err("missing_authority", err)
        from agent.video_editor.jobs import prepare_job, project_job_for_frontend

        _doc, job = prepare_job(
            project_id=project_id,
            document_id=document_id,
            session_id=session_id,
            kind=kind,
            idempotency_key=idempotency_key,
            adapter_id=adapter_id,
            capability=capability,
            input_asset_ids=list(input_asset_ids or []),
            parameters=dict(parameters or {}),
            expected_revision=expected_revision,
        )
        projection = project_job_for_frontend(job)
        return _json_ok(
            {
                "tool": "video_submit_job",
                "job": projection,
                "completed": bool(projection.get("completed")),
                "note": "Job recorded; blocked/queued is not completion",
            }
        )
    except Exception as exc:
        return _json_err("tool_failed", str(exc), tool="video_submit_job")


@ToolRegistry.register(
    name="video_get_job",
    description="Read durable video job status and ToolRun linkage.",
    category="video_editor",
    risk_level="safe",
)
@tool(args_schema=VideoJobArgs, description="Read durable video job status and ToolRun linkage.")
def video_get_job(session_id: str, project_id: str, document_id: str, job_id: str) -> str:
    """Get video job status."""
    try:
        err = _authority_check(session_id, project_id)
        if err:
            return _json_err("missing_authority", err)
        from agent.video_editor.jobs import project_job_for_frontend
        from agent.video_editor.store import get_video_editor_store

        job = get_video_editor_store().get_job(project_id, document_id, job_id)
        return _json_ok({"tool": "video_get_job", "job": project_job_for_frontend(job)})
    except Exception as exc:
        return _json_err("tool_failed", str(exc), tool="video_get_job")


@ToolRegistry.register(
    name="video_cancel_job",
    description="Request cancellation of a durable video job.",
    category="video_editor",
    is_action=True,
    risk_level="moderate",
    policy_flags=["ENABLE_SYSTEM_ACTIONS"],
)
@tool(args_schema=VideoJobArgs, description="Request cancellation of a durable video job.")
def video_cancel_job(session_id: str, project_id: str, document_id: str, job_id: str) -> str:
    """Cancel a video job."""
    try:
        err = _authority_check(session_id, project_id)
        if err:
            return _json_err("missing_authority", err)
        from agent.video_editor.jobs import project_job_for_frontend, request_cancel
        from agent.video_editor.store import get_video_editor_store

        job = request_cancel(get_video_editor_store(), project_id, document_id, job_id)
        return _json_ok({"tool": "video_cancel_job", "job": project_job_for_frontend(job)})
    except Exception as exc:
        return _json_err("tool_failed", str(exc), tool="video_cancel_job")


@ToolRegistry.register(
    name="video_retry_job",
    description="Retry a failed or retryable video job.",
    category="video_editor",
    is_action=True,
    risk_level="moderate",
    policy_flags=["ENABLE_SYSTEM_ACTIONS"],
)
@tool(args_schema=VideoJobArgs, description="Retry a failed or retryable video job.")
def video_retry_job(session_id: str, project_id: str, document_id: str, job_id: str) -> str:
    """Retry a video job."""
    try:
        err = _authority_check(session_id, project_id)
        if err:
            return _json_err("missing_authority", err)
        from agent.video_editor.jobs import project_job_for_frontend, retry_job
        from agent.video_editor.store import get_video_editor_store

        _doc, job = retry_job(get_video_editor_store(), project_id, document_id, job_id)
        return _json_ok({"tool": "video_retry_job", "job": project_job_for_frontend(job)})
    except Exception as exc:
        return _json_err("tool_failed", str(exc), tool="video_retry_job")


@ToolRegistry.register(
    name="video_update_creative_memory",
    description="Persist durable creative preferences (not playhead/selection).",
    category="video_editor",
    is_action=True,
    risk_level="moderate",
    policy_flags=["ENABLE_SYSTEM_ACTIONS"],
)
@tool(args_schema=VideoCreativeMemoryArgs, description="Persist durable creative preferences (not playhead/selection).")
def video_update_creative_memory(
    session_id: str,
    project_id: str,
    document_id: str = "",
    preferred_style: str = "",
    output_format: str = "",
    creative_objective: str = "",
    approved_workflow_choices: Optional[list[str]] = None,
    project_conventions: Optional[list[str]] = None,
) -> str:
    """Update durable creative memory only."""
    try:
        err = _authority_check(session_id, project_id)
        if err:
            return _json_err("missing_authority", err)
        from agent.video_editor.memory import update_creative_memory

        mem = update_creative_memory(
            project_id=project_id,
            session_id=session_id,
            document_id=document_id,
            preferred_style=preferred_style,
            output_format=output_format,
            creative_objective=creative_objective,
            approved_workflow_choices=approved_workflow_choices,
            project_conventions=project_conventions,
        )
        return _json_ok({"tool": "video_update_creative_memory", "memory": mem.model_dump(mode="json")})
    except Exception as exc:
        return _json_err("tool_failed", str(exc), tool="video_update_creative_memory")
