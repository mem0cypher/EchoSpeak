"""Read-only UI projections for governed Voice and generation runtimes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from agent.generation_runtime import generation_provider_statuses, get_generation_job_store
from agent.state import get_state_store
from agent.threads import get_thread_manager
from agent.voice_runtime import get_voice_job_store, voice_provider_statuses


router = APIRouter(prefix="/media-runtime", tags=["voice-generation"])


def _session_scope(session_id: str, project_id: str = ""):
    session = str(session_id or "").strip()
    if not session or get_thread_manager().get_thread(session) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    state = get_state_store().get_thread_state(session)
    if project_id and state.active_project_id != project_id:
        raise HTTPException(status_code=409, detail="Project does not match the active Session Project")
    return state


@router.get("/capabilities")
async def media_runtime_capabilities():
    return {
        "voice": [item.model_dump(mode="json") for item in voice_provider_statuses()],
        "generation": [item.model_dump(mode="json") for item in generation_provider_statuses()],
        "authority": {
            "owner": "python",
            "voice_submit_tool": "voice_synthesize_speech",
            "generation_submit_tool": "generation_submit",
            "direct_submit_api": False,
        },
    }


@router.get("/voice/jobs")
async def list_voice_jobs(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
):
    state = _session_scope(session_id, project_id)
    jobs = get_voice_job_store().list(session_id=session_id, limit=limit)
    jobs = [job for job in jobs if not project_id or job.project_id == state.active_project_id]
    return {"items": [job.model_dump(mode="json") for job in jobs], "count": len(jobs)}


@router.get("/generation/jobs")
async def list_generation_jobs(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
):
    state = _session_scope(session_id, project_id)
    jobs = get_generation_job_store().list(session_id=session_id, limit=limit)
    jobs = [job for job in jobs if not project_id or job.project_id == state.active_project_id]
    return {"items": [job.model_dump(mode="json") for job in jobs], "count": len(jobs)}
