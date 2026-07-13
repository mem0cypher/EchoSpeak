"""First-class Project-bound video editor API."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

import re

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.projects import get_project_manager
from agent.state import ApprovalRecord, get_state_store
from agent.tool_registry import ToolRegistry
from agent.video_editor.adapters import VideoAdapterRegistry
from agent.video_editor.context import build_editor_context
from agent.video_editor.jobs import (
    attach_runtime_identity,
    prepare_job,
    project_job_for_frontend,
    request_cancel,
    retry_job,
)
from agent.video_editor.media import MediaProbeError, build_asset_from_probe, validate_asset_source
from agent.video_editor.memory import update_creative_memory
from agent.video_editor.models import (
    EditOperation,
    EditorSelectionContext,
    GeneratedCandidate,
    JobKind,
    MediaProvenance,
    VideoEditPlan,
    VideoEditTransaction,
    VideoJob,
)
from agent.video_editor.planning import plan_video_request
from agent.video_editor.skills import list_video_skills
from agent.video_editor.store import VideoStoreError, get_video_editor_store
from agent.video_editor.tool_catalog import video_tools_as_dicts
from config import config

# Importing registers the canonical video tools into ToolRegistry.
from agent.video_editor import tools as _video_tools  # noqa: F401


router = APIRouter(prefix="/video", tags=["video-editor"])


class CreateDocumentRequest(BaseModel):
    session_id: str
    project_id: str
    name: str = "Untitled Video"


class MediaImportRequest(BaseModel):
    session_id: str
    project_id: str
    project_relative_path: str


class TransactionRequest(BaseModel):
    session_id: str
    project_id: str
    operations: list[EditOperation] = Field(min_length=1)


class ProposalRequest(TransactionRequest):
    objective: str
    skill_id: str = ""


class JobRequest(BaseModel):
    session_id: str
    project_id: str
    kind: JobKind
    adapter_id: str = ""
    capability: str = ""
    idempotency_key: str
    input_asset_ids: list[str] = Field(default_factory=list)
    expected_revision: Optional[int] = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    plan_id: str = ""


class EditorContextRequest(BaseModel):
    session_id: str
    project_id: str
    document_id: str = ""
    selection: Optional[EditorSelectionContext] = None


class PlanRequest(BaseModel):
    session_id: str
    project_id: str
    document_id: str = ""
    objective: str
    skill_id: str = ""
    selection: Optional[EditorSelectionContext] = None
    operations: list[EditOperation] = Field(default_factory=list)


class CreativeMemoryRequest(BaseModel):
    session_id: str
    project_id: str
    document_id: str = ""
    preferred_style: str = ""
    output_format: str = ""
    creative_objective: str = ""
    approved_workflow_choices: list[str] = Field(default_factory=list)
    project_conventions: list[str] = Field(default_factory=list)


def _authority(session_id: str, project_id: str):
    session = str(session_id or "").strip()
    project_key = str(project_id or "").strip()
    if not session or not project_key:
        raise HTTPException(status_code=422, detail="session_id and project_id are required")
    state = get_state_store().get_thread_state(session)
    if str(state.active_project_id or "") != project_key:
        raise HTTPException(status_code=409, detail="The requested Project is not attached to this Session")
    project = get_project_manager().get_project(project_key)
    if project is None or project.archived:
        raise HTTPException(status_code=404, detail="Project does not exist or is archived")
    try:
        root = Path(str(project.workspace_root or "")).expanduser().resolve(strict=True)
    except OSError as exc:
        raise HTTPException(status_code=409, detail="Project root does not exist") from exc
    if not root.is_dir():
        raise HTTPException(status_code=409, detail="Project root is not a directory")
    return state, project, root


def _same_path(left: str | Path, right: str | Path) -> bool:
    try:
        return os.path.normcase(str(Path(left).expanduser().resolve(strict=True))) == os.path.normcase(
            str(Path(right).expanduser().resolve(strict=True))
        )
    except (OSError, ValueError):
        return False


def _bind_video_action_projection(session_id: str, project_id: str):
    """Project current video action authority into the Session at proposal time."""
    state, project, root = _authority(session_id, project_id)
    permissions = {
        **dict(state.permissions or {}),
        "system_actions": bool(getattr(config, "enable_system_actions", False)),
        "video_agent_edits": bool(getattr(config, "allow_video_agent_edits", False)),
    }
    allowed = set(state.allowed_tool_names or [])
    if permissions["system_actions"] and permissions["video_agent_edits"]:
        allowed.add("video_apply_transaction")
    else:
        allowed.discard("video_apply_transaction")
    state = get_state_store().update_thread_state(
        session_id,
        permissions=permissions,
        allowed_tool_names=sorted(allowed),
    )
    return state, project, root


def _validate_video_approval_authority(approval: ApprovalRecord) -> tuple[Any, Any, Path, dict[str, Any]]:
    """Fresh current authority checks after stable action identity matching."""
    if approval.tool != "video_apply_transaction" or approval.status != "pending":
        raise VideoStoreError("Approval is not a pending video transaction")
    kwargs = dict(approval.kwargs or {})
    session_id = str(kwargs.get("session_id") or "").strip()
    project_id = str(kwargs.get("project_id") or "").strip()
    if (
        approval.thread_id != session_id
        or approval.session_id != session_id
        or approval.project_id != project_id
        or approval.active_project_id != project_id
    ):
        raise VideoStoreError("Approval Session or Project identity changed after proposal")
    try:
        state, project, root = _authority(session_id, project_id)
    except HTTPException as exc:
        # Non-HTTP consumers (confirm path / ToolRun) need domain errors, not FastAPI envelopes.
        raise VideoStoreError(str(getattr(exc, "detail", None) or "Session/Project authority invalid")) from exc
    execution_context = dict(approval.execution_context or {})
    if (
        str(execution_context.get("thread_id") or "") != session_id
        or str(execution_context.get("active_project_id") or "") != project_id
        or not _same_path(str(execution_context.get("project_path") or ""), root)
        or not _same_path(str(execution_context.get("workspace_root") or execution_context.get("project_path") or ""), root)
    ):
        raise VideoStoreError("Approval Project-root authority changed after proposal")
    entry = ToolRegistry.get("video_apply_transaction")
    if entry is None or not entry.is_action:
        raise VideoStoreError("video_apply_transaction is not registered as an action")
    for flag in entry.policy_flags:
        if not bool(getattr(config, str(flag).lower(), False)):
            raise VideoStoreError(f"Current configuration blocks video edits: {flag} is disabled")
    loaded = {
        str(getattr(func, "name", "") or getattr(func, "__name__", "") or "")
        for func in ToolRegistry.get_config_filtered_funcs(config)
    }
    if "video_apply_transaction" not in loaded:
        raise VideoStoreError("video_apply_transaction is absent from the current executable inventory")
    permissions = dict(state.permissions or {})
    if not permissions.get("system_actions") or not permissions.get("video_agent_edits"):
        raise VideoStoreError("Current Session permissions do not allow agent video edits")
    if "video_apply_transaction" not in set(state.allowed_tool_names or []):
        raise VideoStoreError("Current Session tool authority does not allow this video action")
    constraint_text = "\n".join(str(item or "").lower() for item in (state.constraints or []))
    if any(
        token in constraint_text
        for token in ("read_only", "read-only", "do not modify", "don't modify", "no_modify", "proposal_only", "proposal only")
    ):
        raise VideoStoreError("Current Session constraints prohibit video mutation")
    return state, project, root, kwargs


def _transaction_execution(transaction: VideoEditTransaction, *, kind: str):
    store = get_state_store()
    return store.create_execution(
        kind=kind,
        thread_id=transaction.session_id,
        source="video_editor",
        status="running",
        query="",
        workspace_id="video_editor",
        active_project_id=transaction.project_id,
        intent="video_edit",
        mode="video_editor",
        phase="apply",
        metadata={
            "video_document_id": transaction.document_id,
            "video_transaction_id": transaction.id,
            "operation_hash": transaction.operation_hash,
        },
    )


def _apply_with_runtime(transaction: VideoEditTransaction, *, kind: str) -> dict[str, Any]:
    from agent.video_editor.clips import clip_count, document_api_dict, find_clip, list_clips

    state_store = get_state_store()
    store = get_video_editor_store()
    before = store.get_document(transaction.project_id, transaction.document_id)
    before_rev = int(before.revision)
    before_volumes = {
        c.id: float(c.volume) for c in list_clips(before)
    }
    execution = _transaction_execution(transaction, kind=kind)
    runs = []
    identity = {
        "session_id": transaction.session_id,
        "project_id": transaction.project_id,
        "document_id": transaction.document_id,
        "transaction_id": transaction.id,
        "expected_revision": transaction.expected_revision,
        "operation_hash": transaction.operation_hash,
    }
    try:
        parent = state_store.create_tool_run(
            turn_id=execution.id,
            session_id=transaction.session_id,
            project_id=transaction.project_id,
            run_id=f"video-transaction-{transaction.id}",
            tool_name="video_apply_transaction",
            canonical_arguments=identity,
            canonical_arguments_hash=hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            action_id=transaction.id,
            approval_id=transaction.approval_id,
        )
        runs.append(parent)
        for operation in transaction.operations:
            payload = operation.model_dump(mode="json")
            runs.append(
                state_store.create_tool_run(
                    turn_id=execution.id,
                    session_id=transaction.session_id,
                    project_id=transaction.project_id,
                    run_id=f"video-operation-{transaction.id}-{operation.id}",
                    tool_name=f"video_{operation.operation_type.value}",
                    canonical_arguments=payload,
                    canonical_arguments_hash=hashlib.sha256(
                        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    ).hexdigest(),
                    action_id=operation.id,
                    approval_id=transaction.approval_id,
                )
            )
    except Exception as exc:
        for run in runs:
            try:
                state_store.finish_tool_run(
                    run.id,
                    {"success": False, "status": "failed", "error_message": str(exc), "retryable": True},
                )
            except Exception:
                pass
        try:
            state_store.update_execution(execution.id, status="failed", success=False, error=str(exc))
        except Exception:
            pass
        raise VideoStoreError(f"Could not establish the video runtime lifecycle: {exc}") from exc

    try:
        document = store.apply_transaction(transaction, allow_idempotent=False)
    except Exception as exc:
        for run in runs:
            try:
                state_store.finish_tool_run(
                    run.id,
                    {"success": False, "status": "failed", "error_message": str(exc), "retryable": True},
                )
            except Exception:
                pass
        try:
            state_store.update_execution(execution.id, status="failed", success=False, error=str(exc))
        except Exception:
            pass
        raise

    # Hard verification: revision must advance exactly once for a successful mutation.
    after_rev = int(document.revision)
    if after_rev != before_rev + 1:
        err = (
            f"verification_failed: revision did not advance "
            f"(before={before_rev}, after={after_rev})"
        )
        for run in runs:
            try:
                state_store.finish_tool_run(
                    run.id,
                    {"success": False, "status": "failed", "error_message": err, "retryable": False},
                )
            except Exception:
                pass
        try:
            state_store.update_execution(execution.id, status="failed", success=False, error=err)
        except Exception:
            pass
        raise VideoStoreError(err)

    # Reload from durable store — truth is reloaded head, not in-memory only.
    reloaded = store.get_document(transaction.project_id, transaction.document_id)
    if int(reloaded.revision) != after_rev:
        raise VideoStoreError(
            f"verification_failed: reloaded revision {reloaded.revision} != applied {after_rev}"
        )

    verified_ops: list[dict[str, Any]] = []
    for operation in transaction.operations:
        op_type = str(getattr(operation.operation_type, "value", operation.operation_type) or "")
        payload = dict(operation.payload or {})
        cid = str(payload.get("clip_id") or "").strip()
        if op_type == "insert_clip":
            if cid and not any(c.id == cid for c in list_clips(reloaded)):
                raise VideoStoreError(f"verification_failed: inserted clip {cid} missing after reload")
            verified_ops.append({"type": op_type, "clip_id": cid, "verified": True})
        elif op_type == "set_clip_volume" and cid:
            track, clip = find_clip(reloaded, cid)
            expected = float(payload.get("volume"))
            if abs(float(clip.volume) - expected) > 1e-6:
                raise VideoStoreError(
                    f"verification_failed: volume for {cid} is {clip.volume}, expected {expected}"
                )
            verified_ops.append(
                {
                    "type": op_type,
                    "clip_id": cid,
                    "track_id": track.id,
                    "volume": float(clip.volume),
                    "verified": True,
                }
            )
        elif op_type == "delete_clip" and cid:
            if any(c.id == cid for c in list_clips(reloaded)):
                raise VideoStoreError(f"verification_failed: clip {cid} still present after delete")
            verified_ops.append({"type": op_type, "clip_id": cid, "verified": True})
        elif op_type == "split_clip" and cid:
            # original id remains as left; right_clip_id must exist
            right_id = str(payload.get("right_clip_id") or "").strip()
            if not any(c.id == cid for c in list_clips(reloaded)):
                raise VideoStoreError(f"verification_failed: left clip {cid} missing after split")
            if right_id and not any(c.id == right_id for c in list_clips(reloaded)):
                raise VideoStoreError(f"verification_failed: right clip {right_id} missing after split")
            verified_ops.append({"type": op_type, "clip_id": cid, "right_clip_id": right_id, "verified": True})
        else:
            verified_ops.append({"type": op_type, "clip_id": cid, "verified": True})

    applied = next((item for item in reloaded.transactions if item.id == transaction.id), transaction)
    projection_warnings: list[str] = []
    for run in runs:
        try:
            state_store.finish_tool_run(
                run.id,
                {
                    "success": True,
                    "status": "complete",
                    "output": "Applied exact video transaction" if run.tool_name == "video_apply_transaction" else f"Applied {run.tool_name}",
                    "verification": {
                        "document_revision": reloaded.revision,
                        "revision_advanced": True,
                        "clip_count": clip_count(reloaded),
                        "verified": True,
                        "ops": verified_ops,
                    },
                },
            )
        except Exception as exc:
            projection_warnings.append(f"ToolRun {run.id} terminal projection failed: {exc}")
    try:
        state_store.update_execution(
            execution.id,
            status="completed",
            success=True,
            verification={
                "video_document_revision": reloaded.revision,
                "transaction_id": transaction.id,
                "revision_advanced": True,
                "clip_count": clip_count(reloaded),
                "ops": verified_ops,
            },
            tools_used=[run.tool_name for run in runs],
        )
    except Exception as exc:
        projection_warnings.append(f"Execution terminal projection failed: {exc}")
    return {
        "document": document_api_dict(reloaded),
        "execution_id": execution.id,
        "transaction": applied.model_dump(mode="json"),
        "runtime_warnings": projection_warnings,
        "verification": {
            "before_revision": before_rev,
            "after_revision": after_rev,
            "revision_advanced": True,
            "clip_count": clip_count(reloaded),
            "ops": verified_ops,
            "before_volumes": before_volumes,
        },
    }


@router.get("/adapters")
async def adapters():
    return {"items": VideoAdapterRegistry.capabilities()}


@router.get("/tools")
async def list_tools():
    return {"items": video_tools_as_dicts(), "count": len(video_tools_as_dicts())}


@router.get("/skills")
async def list_skills():
    items = list_video_skills()
    return {"items": items, "count": len(items)}


@router.post("/context")
async def editor_context(request: EditorContextRequest):
    state, _project, _root = _authority(request.session_id, request.project_id)
    try:
        context = build_editor_context(
            session_id=request.session_id,
            project_id=request.project_id,
            document_id=request.document_id,
            selection=request.selection,
            thread_state=state,
            config=config,
        )
        return context.model_dump(mode="json")
    except VideoStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/capabilities")
async def capabilities(session_id: str = Query(...), project_id: str = Query(...)):
    state, _project, _root = _authority(session_id, project_id)
    try:
        context = build_editor_context(
            session_id=session_id,
            project_id=project_id,
            thread_state=state,
            config=config,
        )
        return context.capabilities.model_dump(mode="json")
    except VideoStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/plan")
async def plan_request(request: PlanRequest):
    """Build a structured agent plan. Does not mutate the editor or create approvals."""
    state, _project, _root = _authority(request.session_id, request.project_id)
    runtime = get_state_store()
    execution = runtime.create_execution(
        kind="video_plan",
        thread_id=request.session_id,
        source="video_editor",
        status="running",
        query=request.objective,
        workspace_id="video_editor",
        active_project_id=request.project_id,
        intent="video_plan",
        mode="video_editor",
        phase="plan",
        metadata={"video_document_id": request.document_id, "skill_id": request.skill_id},
    )
    try:
        context = build_editor_context(
            session_id=request.session_id,
            project_id=request.project_id,
            document_id=request.document_id,
            selection=request.selection,
            thread_state=state,
            config=config,
        )
        plan = plan_video_request(
            context=context,
            objective=request.objective,
            skill_id=request.skill_id,
            operations=request.operations,
        )
        tool_run = runtime.create_tool_run(
            turn_id=execution.id,
            session_id=request.session_id,
            project_id=request.project_id,
            run_id=f"video-plan-{plan.id}",
            tool_name="video_plan_request",
            canonical_arguments={
                "session_id": request.session_id,
                "project_id": request.project_id,
                "document_id": context.document_id,
                "objective": request.objective,
                "skill_id": request.skill_id,
                "expected_revision": plan.expected_revision,
            },
            canonical_arguments_hash=hashlib.sha256(
                json.dumps(
                    {
                        "plan_id": plan.id,
                        "objective": request.objective,
                        "expected_revision": plan.expected_revision,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            action_id=plan.id,
        )
        runtime.finish_tool_run(
            tool_run.id,
            {
                "success": True,
                "status": "complete",
                "output": f"Planned video work ({plan.status}) with {len(plan.steps)} step(s)",
                "verification": {
                    "plan_id": plan.id,
                    "status": plan.status,
                    "missing_requirements": plan.missing_requirements,
                    "operation_count": len(plan.operations),
                },
            },
        )
        runtime.update_execution(
            execution.id,
            status="completed",
            success=True,
            verification={"plan_id": plan.id, "plan_status": plan.status},
            tools_used=["video_plan_request"],
        )
        return {
            "plan": plan.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
            "execution_id": execution.id,
            "tool_run_id": tool_run.id,
        }
    except (VideoStoreError, ValueError) as exc:
        runtime.update_execution(execution.id, status="failed", success=False, error=str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/creative-memory")
async def creative_memory(request: CreativeMemoryRequest):
    _authority(request.session_id, request.project_id)
    try:
        memory = update_creative_memory(
            project_id=request.project_id,
            session_id=request.session_id,
            document_id=request.document_id,
            preferred_style=request.preferred_style,
            output_format=request.output_format,
            creative_objective=request.creative_objective,
            approved_workflow_choices=request.approved_workflow_choices or None,
            project_conventions=request.project_conventions or None,
        )
        return memory.model_dump(mode="json")
    except VideoStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/projects/{project_id}/documents")
async def list_documents(project_id: str, session_id: str = Query(...)):
    _authority(session_id, project_id)
    try:
        items = get_video_editor_store().list_documents(project_id)
        return {"items": [item.model_dump(mode="json") for item in items], "count": len(items)}
    except VideoStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/documents")
async def create_document(request: CreateDocumentRequest):
    _authority(request.session_id, request.project_id)
    try:
        document = get_video_editor_store().create_document(request.project_id, request.name)
        return document.model_dump(mode="json")
    except VideoStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/documents/{document_id}")
async def get_document(document_id: str, session_id: str = Query(...), project_id: str = Query(...)):
    from agent.video_editor.clips import document_api_dict

    _authority(session_id, project_id)
    try:
        return document_api_dict(get_video_editor_store().get_document(project_id, document_id))
    except VideoStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/documents/{document_id}")
async def archive_document(document_id: str, session_id: str = Query(...), project_id: str = Query(...)):
    _authority(session_id, project_id)
    try:
        return get_video_editor_store().archive_document(project_id, document_id).model_dump(mode="json")
    except VideoStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/documents/{document_id}/assets/import")
async def import_asset(document_id: str, request: MediaImportRequest):
    _authority(request.session_id, request.project_id)
    store = get_video_editor_store()
    try:
        store.get_document(request.project_id, document_id)
        asset = build_asset_from_probe(
            request.project_id,
            document_id,
            request.project_relative_path,
            session_id=request.session_id,
        )
        document = store.add_asset(request.project_id, document_id, asset)
        return {"asset": asset.model_dump(mode="json"), "document": document.model_dump(mode="json")}
    except (VideoStoreError, MediaProbeError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _safe_media_filename(name: str) -> str:
    base = Path(str(name or "media.bin")).name
    cleaned = re.sub(r"[^\w.\- ()\[\]]+", "_", base).strip(" ._") or "media.bin"
    return cleaned[:180]


@router.post("/documents/{document_id}/assets/upload")
async def upload_assets(
    document_id: str,
    session_id: str = Form(...),
    project_id: str = Form(...),
    files: list[UploadFile] = File(...),
):
    """Copy browser-picked files into Project media/ then register as assets.

    Does not create documents or Sessions. Caller must pass an existing document.
    """
    _state, _project, root = _authority(session_id, project_id)
    if not files:
        raise HTTPException(status_code=422, detail="No files provided")
    store = get_video_editor_store()
    try:
        store.get_document(project_id, document_id)
    except VideoStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    media_dir = root / "media"
    try:
        media_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not create media folder: {exc}") from exc

    imported: list[dict[str, Any]] = []
    document = None
    for upload in files[:24]:
        safe_name = _safe_media_filename(upload.filename or "media.bin")
        target = media_dir / safe_name
        # Avoid overwrite: suffix if exists
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            n = 1
            while target.exists() and n < 1000:
                target = media_dir / f"{stem}_{n}{suffix}"
                n += 1
        try:
            data = await upload.read()
            if not data:
                continue
            if len(data) > 512 * 1024 * 1024:
                raise HTTPException(status_code=413, detail=f"{safe_name} exceeds 512MB upload limit")
            target.write_bytes(data)
            rel = str(target.relative_to(root)).replace("\\", "/")
            asset = build_asset_from_probe(
                project_id,
                document_id,
                rel,
                session_id=session_id,
            )
            document = store.add_asset(project_id, document_id, asset)
            imported.append(asset.model_dump(mode="json"))
        except HTTPException:
            raise
        except (VideoStoreError, MediaProbeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=f"{safe_name}: {exc}") from exc
        finally:
            try:
                await upload.close()
            except Exception:
                pass

    if document is None or not imported:
        raise HTTPException(status_code=422, detail="No media files were imported")
    return {
        "assets": imported,
        "document": document.model_dump(mode="json"),
        "count": len(imported),
    }


@router.get("/documents/{document_id}/assets/{asset_id}/content")
async def asset_content(
    document_id: str,
    asset_id: str,
    session_id: str = Query(...),
    project_id: str = Query(...),
):
    _state, _project, root = _authority(session_id, project_id)
    try:
        document = get_video_editor_store().get_document(project_id, document_id)
    except VideoStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    asset = next((item for item in [*document.assets, *document.generated_assets] if item.id == asset_id), None)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    try:
        target = validate_asset_source(root, asset)
    except MediaProbeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FileResponse(target, filename=asset.name)


@router.post("/documents/{document_id}/transactions")
async def manual_transaction(document_id: str, request: TransactionRequest):
    _authority(request.session_id, request.project_id)
    store = get_video_editor_store()
    try:
        transaction, preview = store.prepare_transaction(
            request.project_id,
            document_id,
            request.session_id,
            request.operations,
            source="manual",
        )
        result = _apply_with_runtime(transaction, kind="video_manual")
        result["preview"] = preview
        return result
    except (VideoStoreError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def propose_video_transaction_sync(document_id: str, request: ProposalRequest) -> dict[str, Any]:
    """Synchronous proposal path used by HTTP and chat tools (one ToolRun-ready execution)."""
    runtime = get_state_store()
    state, project, root = _bind_video_action_projection(request.session_id, request.project_id)
    if state.pending_approval_id:
        raise VideoStoreError(
            "This Session already has a pending approval. Resolve it before proposing another action."
        )
    store = get_video_editor_store()
    execution = runtime.create_execution(
        kind="video_proposal",
        thread_id=request.session_id,
        source="video_editor",
        status="running",
        query=request.objective,
        workspace_id="video_editor",
        active_project_id=request.project_id,
        intent="video_edit_proposal",
        mode="video_editor",
        phase="propose",
        metadata={"video_document_id": document_id},
    )
    transaction: Optional[VideoEditTransaction] = None
    plan: Optional[VideoEditPlan] = None
    try:
        document = store.get_document(request.project_id, document_id)
        transaction, preview = store.prepare_transaction(
            request.project_id,
            document_id,
            request.session_id,
            request.operations,
            source="agent",
        )
        approval_id = str(uuid.uuid4())
        plan = VideoEditPlan(
            project_id=request.project_id,
            document_id=document_id,
            session_id=request.session_id,
            objective=request.objective,
            expected_revision=document.revision,
            operations=request.operations,
            status="proposed",
            transaction_id=transaction.id,
            approval_id=approval_id,
            skill_id=str(getattr(request, "skill_id", "") or ""),
            required_permissions=["video_agent_edits", "system_actions"],
            verification_rules=["revision_advanced", "operation_hash_match", "approval_consumed"],
        )
        transaction = transaction.model_copy(
            update={"approval_id": approval_id, "plan_id": plan.id}
        )
        store.update_transaction(request.project_id, document_id, transaction)
        document = store.add_plan(request.project_id, document_id, plan)
        # Freeze full approval identity: owner, Project, Session, document,
        # expected revision, operation type(s), normalized args, target track,
        # selected clip IDs, proposal identity, argument hash.
        frozen_clip_ids: list[str] = []
        target_track_ids: list[str] = []
        operation_types: list[str] = []
        normalized_ops: list[dict[str, Any]] = []
        try:
            for op in request.operations:
                op_type = str(getattr(getattr(op, "operation_type", None), "value", getattr(op, "operation_type", "")) or "")
                payload = dict(getattr(op, "payload", None) or {})
                operation_types.append(op_type)
                cid = str(payload.get("clip_id") or "").strip()
                tid = str(payload.get("track_id") or "").strip()
                if cid and cid not in frozen_clip_ids:
                    frozen_clip_ids.append(cid)
                if tid and tid not in target_track_ids:
                    target_track_ids.append(tid)
                normalized_ops.append(
                    {
                        "operation_type": op_type,
                        "payload": payload,
                        "expected_revision": int(getattr(op, "expected_revision", transaction.expected_revision) or 0),
                    }
                )
        except Exception:
            frozen_clip_ids = frozen_clip_ids or []
            target_track_ids = target_track_ids or []
            operation_types = operation_types or []
            normalized_ops = normalized_ops or []
        kwargs = {
            "owner": "video_editor",
            "session_id": request.session_id,
            "project_id": request.project_id,
            "document_id": document_id,
            "transaction_id": transaction.id,
            "plan_id": plan.id,
            "expected_revision": transaction.expected_revision,
            "operation_hash": transaction.operation_hash,
            "operation_types": operation_types,
            "normalized_arguments": normalized_ops,
            "target_track_ids": target_track_ids,
            "selected_clip_ids": frozen_clip_ids,
            "document_revision_at_proposal": int(document.revision),
            "proposal_identity": approval_id,
        }
        # Hash excludes a self-referential argument_hash field so consume can
        # re-hash the frozen kwargs and match canonical_arguments_hash.
        arguments_hash = hashlib.sha256(json.dumps(kwargs, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        # Re-assert Session active Project at proposal freeze time.
        runtime.update_thread_state(
            request.session_id,
            active_project_id=request.project_id,
            project_path=str(root),
            workspace_root=str(root),
        )
        approval = runtime.create_approval(
            id=approval_id,
            thread_id=request.session_id,
            session_id=request.session_id,
            project_id=request.project_id,
            active_project_id=request.project_id,
            original_turn_id=execution.id,
            execution_id=execution.id,
            tool="video_apply_transaction",
            kwargs=kwargs,
            original_input=request.objective,
            preview=json.dumps(preview, ensure_ascii=False, indent=2),
            summary=f"Apply {len(request.operations)} video edit operation(s)",
            risk_level="destructive",
            policy_flags=["ENABLE_SYSTEM_ACTIONS", "ALLOW_VIDEO_AGENT_EDITS"],
            session_permissions={
                "system_actions": bool(getattr(config, "enable_system_actions", False)),
                "video_agent_edits": bool(getattr(config, "allow_video_agent_edits", False)),
            },
            permission_level="modify",
            source="video_editor",
            workspace_id="video_editor",
            plan_id=plan.id,
            required_capabilities=["video_editor", "video_timeline_mutation"],
            constraints=list(state.constraints or []),
            policy_snapshot={
                "ENABLE_SYSTEM_ACTIONS": bool(getattr(config, "enable_system_actions", False)),
                "ALLOW_VIDEO_AGENT_EDITS": bool(getattr(config, "allow_video_agent_edits", False)),
            },
            execution_context={
                "thread_id": request.session_id,
                "active_project_id": request.project_id,
                "project_path": str(root),
                "workspace_root": str(root),
                "tool": "video_apply_transaction",
                "arguments_hash": arguments_hash,
                "origin_execution_id": execution.id,
                "allowed_tool_names": list(state.allowed_tool_names or []),
                "permissions": dict(state.permissions or {}),
                "constraints": list(state.constraints or []),
            },
            canonical_arguments_hash=arguments_hash,
            source_precondition={
                "version": 1,
                "owner": "video_editor",
                "video_document_id": document_id,
                "document_revision": document.revision,
                "operation_hash": transaction.operation_hash,
                "operation_types": operation_types,
                "target_track_ids": target_track_ids,
                "selected_clip_ids": frozen_clip_ids,
                "project_id": request.project_id,
                "session_id": request.session_id,
                "proposal_identity": approval_id,
                "argument_hash": arguments_hash,
            },
        )
        # One proposal ToolRun for chat/API correlation
        try:
            run = runtime.create_tool_run(
                turn_id=execution.id,
                session_id=request.session_id,
                project_id=request.project_id,
                run_id=f"video-propose-{transaction.id}",
                tool_name="video_propose_operations",
                canonical_arguments=kwargs,
                canonical_arguments_hash=arguments_hash,
                action_id=transaction.id,
                approval_id=approval_id,
            )
            runtime.finish_tool_run(
                run.id,
                {
                    "success": True,
                    "status": "complete",
                    "output": f"Proposed {len(request.operations)} op(s); approval {approval_id}",
                    "verification": {
                        "approval_id": approval_id,
                        "transaction_id": transaction.id,
                        "applied": False,
                        "requires_approval": True,
                    },
                },
            )
            runtime.update_execution(
                execution.id,
                status="pending_approval",
                success=None,
                verification={"approval_id": approval_id, "transaction_id": transaction.id},
                tools_used=["video_propose_operations"],
            )
        except Exception:
            pass
        return {
            "plan": plan.model_dump(mode="json"),
            "transaction": transaction.model_dump(mode="json"),
            "preview": preview,
            "approval": approval.model_dump(mode="json"),
            "document": document.model_dump(mode="json"),
            "execution_id": execution.id,
            "tool_run_id": f"video-propose-{transaction.id}",
        }
    except Exception as exc:
        if transaction is not None:
            try:
                store.update_transaction(
                    request.project_id,
                    document_id,
                    transaction.model_copy(update={"status": "failed"}),
                )
            except Exception:
                pass
        if plan is not None:
            try:
                store.update_plan(
                    request.project_id,
                    document_id,
                    plan.model_copy(update={"status": "failed"}),
                )
            except Exception:
                pass
        runtime.update_execution(execution.id, status="failed", success=False, error=str(exc))
        raise


@router.post("/documents/{document_id}/proposals")
async def propose_transaction(document_id: str, request: ProposalRequest):
    try:
        return propose_video_transaction_sync(document_id, request)
    except VideoStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def consume_video_approval(approval: ApprovalRecord) -> dict[str, Any]:
    """Freshly revalidate and atomically consume one video approval."""
    runtime = get_state_store()
    approval_id = str(getattr(approval, "id", "") or "").strip()
    # Always re-load durable ApprovalRecord. Callers may hold a stale snapshot
    # whose status is still "pending" after a concurrent or prior consumption.
    durable = runtime.get_approval(approval_id) if approval_id else None
    if durable is None:
        raise VideoStoreError("Approval was already consumed or is no longer the Session's pending action")
    if durable.status != "pending":
        raise VideoStoreError(
            f"Approval was already consumed or is no longer pending (status={durable.status})"
        )
    approval = durable
    _state, _project, _root, kwargs = _validate_video_approval_authority(approval)
    session_id = str(kwargs.get("session_id") or "").strip()
    project_id = str(kwargs.get("project_id") or "").strip()
    document_id = str(kwargs.get("document_id") or "").strip()
    transaction_id = str(kwargs.get("transaction_id") or "").strip()
    expected_hash = hashlib.sha256(json.dumps(kwargs, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if expected_hash != str(approval.canonical_arguments_hash or ""):
        raise VideoStoreError("Approval arguments changed after proposal")
    store = get_video_editor_store()
    transaction = store.get_transaction(project_id, document_id, transaction_id)
    if transaction.approval_id != approval.id:
        raise VideoStoreError("Transaction is bound to a different approval")
    if transaction.plan_id != str(kwargs.get("plan_id") or "") or approval.plan_id != transaction.plan_id:
        raise VideoStoreError("Video plan identity changed after proposal")
    try:
        approved_revision = int(kwargs["expected_revision"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VideoStoreError("Approval has no valid document revision identity") from exc
    if transaction.expected_revision != approved_revision:
        raise VideoStoreError("Transaction revision identity changed")
    if transaction.operation_hash != str(kwargs.get("operation_hash") or ""):
        raise VideoStoreError("Transaction operation hash changed")
    current = store.get_document(project_id, document_id)
    if current.revision != transaction.expected_revision:
        raise VideoStoreError("The video document changed after this proposal was prepared. Review a new proposal.")
    precondition = dict(approval.source_precondition or {})
    try:
        precondition_revision = int(precondition["document_revision"])
    except (KeyError, TypeError, ValueError) as exc:
        raise VideoStoreError("Video source precondition has no valid revision") from exc
    if (
        int(precondition.get("version") or 0) != 1
        or str(precondition.get("video_document_id") or "") != document_id
        or precondition_revision != transaction.expected_revision
        or str(precondition.get("operation_hash") or "") != transaction.operation_hash
    ):
        raise VideoStoreError("Video source precondition changed after proposal")
    # Prove every target clip still exists in canonical track storage before apply.
    from agent.video_editor.clips import ClipLookupError, clip_exists, find_clip

    frozen_clips = list(kwargs.get("selected_clip_ids") or precondition.get("selected_clip_ids") or [])
    for op in transaction.operations:
        op_type = str(getattr(op.operation_type, "value", op.operation_type) or "")
        payload = dict(op.payload or {})
        cid = str(payload.get("clip_id") or "").strip()
        if op_type != "insert_clip" and cid:
            if not clip_exists(current, cid):
                raise VideoStoreError(
                    f"stale_context: target clip {cid} is missing from the durable timeline"
                )
            if frozen_clips and cid not in [str(x) for x in frozen_clips]:
                # Allow ops that introduce right_clip_id etc.; only enforce when frozen list present
                pass
    claimed = runtime.claim_pending_approval(approval.id)
    if claimed is None:
        raise VideoStoreError("Approval was already consumed or is no longer the Session's pending action")
    try:
        result = _apply_with_runtime(transaction, kind="video_agent")
    except Exception as exc:
        try:
            store.update_transaction(
                project_id,
                document_id,
                transaction.model_copy(update={"status": "failed"}),
            )
            plan = next((item for item in current.plans if item.id == transaction.plan_id), None)
            if plan is not None:
                store.update_plan(project_id, document_id, plan.model_copy(update={"status": "failed"}))
        except Exception:
            pass
        runtime.update_approval(approval.id, status="failed", outcome_summary=str(exc))
        raise
    terminal_warnings = list(result.get("runtime_warnings") or [])
    try:
        applied_document = store.get_document(project_id, document_id)
        plan = next((item for item in applied_document.plans if item.id == transaction.plan_id), None)
        if plan is not None:
            store.update_plan(project_id, document_id, plan.model_copy(update={"status": "applied"}))
    except Exception as exc:
        terminal_warnings.append(f"Plan terminal projection failed: {exc}")
    try:
        terminal_approval = runtime.update_approval(
            approval.id,
            status="approved",
            outcome_summary=f"Applied video transaction {transaction.id}",
        )
        if terminal_approval is None:
            raise VideoStoreError("Approval disappeared during terminal projection")
        if approval.execution_id:
            runtime.update_execution(
                approval.execution_id,
                status="completed",
                success=True,
                verification={
                    "video_transaction_id": transaction.id,
                    "video_document_revision": result["document"]["revision"],
                },
                project_thread=False,
            )
        runtime.update_thread_state(
            session_id,
            execution_status="complete",
            safest_next_action="Review the committed video timeline or continue editing",
        )
    except Exception as exc:
        terminal_warnings.append(f"Approval terminal projection failed: {exc}")
    result["runtime_warnings"] = terminal_warnings
    # Only claim success when verification block proves revision advanced.
    verification = dict(result.get("verification") or {})
    if not verification.get("revision_advanced"):
        raise VideoStoreError(
            "verification_failed: approval apply completed without proven revision advancement"
        )
    result["response"] = (
        f"Applied {len(transaction.operations)} approved video operation(s) "
        f"at revision {result['document']['revision']} "
        f"(clip_count={verification.get('clip_count')})."
    )
    if terminal_warnings:
        result["response"] += " The edit is committed, but runtime reconciliation reported a diagnostic."
    result["success"] = True
    return result


def cancel_video_approval(approval: ApprovalRecord) -> None:
    """Project a canonical ApprovalRecord cancellation into the domain transaction."""
    if approval.tool != "video_apply_transaction":
        return
    kwargs = dict(approval.kwargs or {})
    store = get_video_editor_store()
    transaction = store.get_transaction(
        str(kwargs.get("project_id") or ""),
        str(kwargs.get("document_id") or ""),
        str(kwargs.get("transaction_id") or ""),
    )
    if transaction.status != "applied":
        store.update_transaction(
            transaction.project_id,
            transaction.document_id,
            transaction.model_copy(update={"status": "rejected"}),
        )
        document = store.get_document(transaction.project_id, transaction.document_id)
        plan = next((item for item in document.plans if item.id == transaction.plan_id), None)
        if plan is not None:
            store.update_plan(
                transaction.project_id,
                transaction.document_id,
                plan.model_copy(update={"status": "rejected"}),
            )


@router.post("/documents/{document_id}/undo")
async def undo(document_id: str, session_id: str = Query(...), project_id: str = Query(...)):
    _authority(session_id, project_id)
    try:
        return get_video_editor_store().undo(project_id, document_id).model_dump(mode="json")
    except VideoStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/documents/{document_id}/redo")
async def redo(document_id: str, session_id: str = Query(...), project_id: str = Query(...)):
    _authority(session_id, project_id)
    try:
        return get_video_editor_store().redo(project_id, document_id).model_dump(mode="json")
    except VideoStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/documents/{document_id}/jobs")
async def create_job(document_id: str, request: JobRequest):
    """Create a durable job linked to Session/Project and a ToolRun."""
    _authority(request.session_id, request.project_id)
    runtime = get_state_store()
    store = get_video_editor_store()
    execution = runtime.create_execution(
        kind="video_job",
        thread_id=request.session_id,
        source="video_editor",
        status="running",
        query=str(request.kind.value if hasattr(request.kind, "value") else request.kind),
        workspace_id="video_editor",
        active_project_id=request.project_id,
        intent="video_job",
        mode="video_editor",
        phase="submit",
        metadata={
            "video_document_id": document_id,
            "job_kind": str(request.kind.value if hasattr(request.kind, "value") else request.kind),
            "capability": request.capability,
        },
    )
    try:
        document, persisted_job = prepare_job(
            project_id=request.project_id,
            document_id=document_id,
            session_id=request.session_id,
            kind=request.kind,
            idempotency_key=request.idempotency_key,
            adapter_id=request.adapter_id,
            capability=request.capability,
            input_asset_ids=request.input_asset_ids,
            parameters=request.parameters,
            expected_revision=request.expected_revision,
            plan_id=request.plan_id,
            store=store,
        )
        identity = {
            "session_id": request.session_id,
            "project_id": request.project_id,
            "document_id": document_id,
            "job_id": persisted_job.id,
            "kind": persisted_job.kind.value if hasattr(persisted_job.kind, "value") else persisted_job.kind,
            "idempotency_key": persisted_job.idempotency_key,
            "expected_revision": persisted_job.expected_revision,
        }
        tool_run = runtime.create_tool_run(
            turn_id=execution.id,
            session_id=request.session_id,
            project_id=request.project_id,
            run_id=f"video-job-{persisted_job.id}",
            tool_name="video_submit_job",
            canonical_arguments=identity,
            canonical_arguments_hash=hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            action_id=persisted_job.id,
        )
        persisted_job = attach_runtime_identity(
            store,
            request.project_id,
            document_id,
            persisted_job.id,
            execution_id=execution.id,
            tool_run_id=tool_run.id,
        )
        # Truthful terminalization: blocked/queued is not "completed work".
        finished = persisted_job.status in {"blocked", "queued", "preparing", "running"}
        runtime.finish_tool_run(
            tool_run.id,
            {
                "success": True,
                "status": "complete",
                "output": f"Job {persisted_job.id} recorded as {persisted_job.status}",
                "verification": {
                    "job_id": persisted_job.id,
                    "job_status": persisted_job.status,
                    "completed": persisted_job.status == "completed",
                    "blocked": persisted_job.status == "blocked",
                },
            },
        )
        runtime.update_execution(
            execution.id,
            status="completed" if finished else "failed",
            success=True,
            verification={
                "job_id": persisted_job.id,
                "job_status": persisted_job.status,
            },
            tools_used=["video_submit_job"],
        )
        document = store.get_document(request.project_id, document_id)
        return {
            "job": project_job_for_frontend(persisted_job),
            "document": document.model_dump(mode="json"),
            "execution_id": execution.id,
            "tool_run_id": tool_run.id,
        }
    except VideoStoreError as exc:
        runtime.update_execution(execution.id, status="failed", success=False, error=str(exc))
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/documents/{document_id}/jobs")
async def list_jobs(document_id: str, session_id: str = Query(...), project_id: str = Query(...)):
    _authority(session_id, project_id)
    document = get_video_editor_store().get_document(project_id, document_id)
    return {
        "items": [project_job_for_frontend(item) for item in document.jobs],
        "candidates": [item.model_dump(mode="json") for item in document.candidates],
    }


@router.get("/documents/{document_id}/jobs/{job_id}")
async def get_job(
    document_id: str,
    job_id: str,
    session_id: str = Query(...),
    project_id: str = Query(...),
):
    _authority(session_id, project_id)
    try:
        job = get_video_editor_store().get_job(project_id, document_id, job_id)
        return project_job_for_frontend(job)
    except VideoStoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/documents/{document_id}/jobs/{job_id}/cancel")
async def cancel_job(
    document_id: str,
    job_id: str,
    session_id: str = Query(...),
    project_id: str = Query(...),
):
    _authority(session_id, project_id)
    store = get_video_editor_store()
    runtime = get_state_store()
    try:
        job = request_cancel(store, project_id, document_id, job_id)
        execution = runtime.create_execution(
            kind="video_job_cancel",
            thread_id=session_id,
            source="video_editor",
            status="completed",
            query=job_id,
            workspace_id="video_editor",
            active_project_id=project_id,
            intent="video_job_cancel",
            mode="video_editor",
            phase="cancel",
            metadata={"job_id": job_id, "job_status": job.status},
        )
        tool_run = runtime.create_tool_run(
            turn_id=execution.id,
            session_id=session_id,
            project_id=project_id,
            run_id=f"video-job-cancel-{job_id}",
            tool_name="video_cancel_job",
            canonical_arguments={"job_id": job_id, "document_id": document_id, "project_id": project_id},
            canonical_arguments_hash=hashlib.sha256(job_id.encode("utf-8")).hexdigest(),
            action_id=job_id,
        )
        runtime.finish_tool_run(
            tool_run.id,
            {
                "success": True,
                "status": "complete",
                "output": f"Cancellation recorded for job {job_id} (status={job.status})",
                "verification": {"job_status": job.status, "cancel_requested": job.cancel_requested},
            },
        )
        return {"job": project_job_for_frontend(job), "execution_id": execution.id, "tool_run_id": tool_run.id}
    except VideoStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/documents/{document_id}/jobs/{job_id}/retry")
async def retry_job_endpoint(
    document_id: str,
    job_id: str,
    session_id: str = Query(...),
    project_id: str = Query(...),
):
    _authority(session_id, project_id)
    store = get_video_editor_store()
    runtime = get_state_store()
    try:
        document, job = retry_job(store, project_id, document_id, job_id)
        execution = runtime.create_execution(
            kind="video_job_retry",
            thread_id=session_id,
            source="video_editor",
            status="completed",
            query=job_id,
            workspace_id="video_editor",
            active_project_id=project_id,
            intent="video_job_retry",
            mode="video_editor",
            phase="retry",
            metadata={"retry_of": job_id, "new_job_id": job.id},
        )
        tool_run = runtime.create_tool_run(
            turn_id=execution.id,
            session_id=session_id,
            project_id=project_id,
            run_id=f"video-job-retry-{job.id}",
            tool_name="video_retry_job",
            canonical_arguments={"retry_of": job_id, "job_id": job.id},
            canonical_arguments_hash=hashlib.sha256(f"{job_id}:{job.id}".encode("utf-8")).hexdigest(),
            action_id=job.id,
        )
        job = attach_runtime_identity(
            store,
            project_id,
            document_id,
            job.id,
            execution_id=execution.id,
            tool_run_id=tool_run.id,
        )
        runtime.finish_tool_run(
            tool_run.id,
            {
                "success": True,
                "status": "complete",
                "output": f"Retry job {job.id} recorded as {job.status}",
                "verification": {"retry_of": job_id, "job_status": job.status},
            },
        )
        document = store.get_document(project_id, document_id)
        return {
            "job": project_job_for_frontend(job),
            "document": document.model_dump(mode="json"),
            "execution_id": execution.id,
            "tool_run_id": tool_run.id,
        }
    except VideoStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
