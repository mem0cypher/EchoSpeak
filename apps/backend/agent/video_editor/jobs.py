"""Canonical video job integration with ToolRun / Execution linkage.

Video analysis, render, export, and generation use durable VideoJob records
owned by VideoEditorStore — not a private spinner lifecycle.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional

from agent.video_editor.adapters import VideoAdapterRegistry
from agent.video_editor.models import JobKind, VideoJob, VideoProjectDocument
from agent.video_editor.store import VideoEditorStore, VideoStoreError, get_video_editor_store


TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "canceled"})
RETRYABLE_STATUSES = frozenset({"failed", "retryable", "interrupted", "blocked"})


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def job_identity_hash(job: VideoJob) -> str:
    payload = {
        "project_id": job.project_id,
        "document_id": job.document_id,
        "session_id": job.session_id,
        "kind": job.kind.value if hasattr(job.kind, "value") else job.kind,
        "adapter_id": job.adapter_id,
        "capability": job.capability,
        "input_asset_ids": job.input_asset_ids,
        "input_hashes": job.input_hashes,
        "parameters": job.parameters,
        "expected_revision": job.expected_revision,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def resolve_adapter_for_capability(capability: str) -> tuple[str, dict[str, Any] | None]:
    """Pick a registered adapter that claims the capability. Prefer available ones."""
    capability = str(capability or "").strip()
    rows = VideoAdapterRegistry.capabilities()
    available_match = None
    declared_match = None
    for row in rows:
        ops = {str(op) for op in (row.get("operations") or ())}
        # Map capability tokens onto adapter operation names.
        aliases = {
            "text_to_video": {"text_to_video"},
            "image_to_video": {"image_to_video"},
            "video_to_video": {"video_to_video", "retake"},
            "audio_to_video": {"audio_to_video"},
            "video_extension": {"extend"},
            "video_understanding": {"understand", "video_understanding"},
            "transcription": {"transcribe", "transcription"},
            "scene_detection": {"scene_detect", "scene_detection"},
            "generation": {"text_to_video", "image_to_video", "video_to_video"},
            "analysis": {"understand", "scene_detect", "transcribe"},
        }
        wanted = aliases.get(capability, {capability})
        if ops & wanted:
            if row.get("available"):
                available_match = row
                break
            if declared_match is None:
                declared_match = row
    chosen = available_match or declared_match
    if chosen is None:
        return "", None
    return str(chosen.get("adapter_id") or ""), chosen


def prepare_job(
    *,
    project_id: str,
    document_id: str,
    session_id: str,
    kind: JobKind | str,
    idempotency_key: str,
    adapter_id: str = "",
    capability: str = "",
    input_asset_ids: list[str] | None = None,
    parameters: dict[str, Any] | None = None,
    expected_revision: int | None = None,
    plan_id: str = "",
    store: Optional[VideoEditorStore] = None,
) -> tuple[VideoProjectDocument, VideoJob]:
    """Create or return an idempotent durable job. Never claims completion."""
    video_store = store or get_video_editor_store()
    document = video_store.get_document(project_id, document_id)
    kind_value = JobKind(kind) if not isinstance(kind, JobKind) else kind
    capability = str(capability or "").strip()
    adapter_id = str(adapter_id or "").strip()
    adapter_row = None
    if not adapter_id and capability:
        adapter_id, adapter_row = resolve_adapter_for_capability(capability)
    elif adapter_id:
        adapter_row = next(
            (row for row in VideoAdapterRegistry.capabilities() if row.get("adapter_id") == adapter_id),
            None,
        )
        if adapter_row is None:
            raise VideoStoreError(f"Unknown video adapter: {adapter_id}")

    status = "queued"
    diagnostics: list[str] = []
    # Deterministic job kinds without adapters stay queued as shells.
    needs_adapter = kind_value in {
        JobKind.GENERATION,
        JobKind.ANALYSIS,
        JobKind.TRANSCRIPTION,
        JobKind.RENDER,
        JobKind.EXPORT,
    }
    if needs_adapter and adapter_id and adapter_row is not None and not bool(adapter_row.get("available")):
        status = "blocked"
        diagnostics.append(
            "Adapter is declared but unavailable; install/configure it before execution. "
            "No completion or artifact claim is made."
        )
    if needs_adapter and not adapter_id and kind_value in {JobKind.GENERATION, JobKind.TRANSCRIPTION, JobKind.ANALYSIS}:
        status = "blocked"
        diagnostics.append(f"No adapter available for capability `{capability or kind_value.value}`.")
    # Render/export workers are not shipped — fail closed as blocked shells.
    if kind_value in {JobKind.RENDER, JobKind.EXPORT, JobKind.PREVIEW, JobKind.PROXY}:
        status = "blocked"
        diagnostics.append(
            f"{kind_value.value} worker is not implemented; job recorded for continuity only."
        )

    if expected_revision is not None and expected_revision != document.revision:
        raise VideoStoreError(
            f"Job expected_revision {expected_revision} does not match document revision {document.revision}"
        )

    job = VideoJob(
        project_id=project_id,
        document_id=document_id,
        session_id=session_id,
        kind=kind_value,
        adapter_id=adapter_id,
        capability=capability,
        tool_name="video_submit_job",
        idempotency_key=str(idempotency_key or "").strip() or f"{kind_value.value}-{int(time.time() * 1000)}",
        input_asset_ids=list(input_asset_ids or []),
        parameters=dict(parameters or {}),
        expected_revision=document.revision if expected_revision is None else expected_revision,
        status=status,
        diagnostics=diagnostics,
        plan_id=plan_id,
    )
    return video_store.create_job(project_id, document_id, job)


def attach_runtime_identity(
    store: VideoEditorStore,
    project_id: str,
    document_id: str,
    job_id: str,
    *,
    execution_id: str = "",
    tool_run_id: str = "",
    approval_id: str = "",
) -> VideoJob:
    return store.update_job(
        project_id,
        document_id,
        job_id,
        execution_id=execution_id,
        tool_run_id=tool_run_id,
        approval_id=approval_id,
        updated_at=time.time(),
    )


def request_cancel(
    store: VideoEditorStore,
    project_id: str,
    document_id: str,
    job_id: str,
) -> VideoJob:
    document = store.get_document(project_id, document_id)
    job = next((item for item in document.jobs if item.id == job_id), None)
    if job is None:
        raise VideoStoreError("Video job not found")
    if job.status in TERMINAL_JOB_STATUSES:
        return job
    return store.update_job(
        project_id,
        document_id,
        job_id,
        cancel_requested=True,
        status="canceled" if job.status in {"queued", "blocked", "preparing"} else job.status,
        diagnostics=[*job.diagnostics, "Cancellation requested"],
        updated_at=time.time(),
    )


def retry_job(
    store: VideoEditorStore,
    project_id: str,
    document_id: str,
    job_id: str,
) -> tuple[VideoProjectDocument, VideoJob]:
    document = store.get_document(project_id, document_id)
    job = next((item for item in document.jobs if item.id == job_id), None)
    if job is None:
        raise VideoStoreError("Video job not found")
    if job.status not in RETRYABLE_STATUSES:
        raise VideoStoreError(f"Job status `{job.status}` is not retryable")
    # New durable attempt with related idempotency key; original remains history.
    retry_key = f"{job.idempotency_key}:retry:{job.retry_count + 1}"
    return prepare_job(
        project_id=project_id,
        document_id=document_id,
        session_id=job.session_id,
        kind=job.kind,
        idempotency_key=retry_key,
        adapter_id=job.adapter_id,
        capability=job.capability,
        input_asset_ids=list(job.input_asset_ids),
        parameters={**dict(job.parameters), "retry_of": job.id},
        expected_revision=document.revision,
        plan_id=job.plan_id,
        store=store,
    )


def project_job_for_frontend(job: VideoJob) -> dict[str, Any]:
    """Frontend projection of durable job truth — never invents completion."""
    return {
        "id": job.id,
        "kind": job.kind.value if hasattr(job.kind, "value") else job.kind,
        "status": job.status,
        "progress": job.progress,
        "adapter_id": job.adapter_id,
        "capability": job.capability,
        "tool_name": job.tool_name,
        "execution_id": job.execution_id,
        "tool_run_id": job.tool_run_id,
        "approval_id": job.approval_id,
        "plan_id": job.plan_id,
        "expected_revision": job.expected_revision,
        "input_asset_ids": list(job.input_asset_ids),
        "outputs": list(job.outputs),
        "artifact_ids": list(job.artifact_ids),
        "diagnostics": list(job.diagnostics),
        "error": job.error,
        "cancel_requested": job.cancel_requested,
        "retry_count": job.retry_count,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "terminal": job.status in TERMINAL_JOB_STATUSES,
        "completed": job.status == "completed",
    }
