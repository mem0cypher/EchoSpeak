"""Read-only UI projections for governed Voice and generation runtimes."""

from __future__ import annotations

import base64
import binascii
import hashlib
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.generation_runtime import generation_provider_statuses, get_generation_job_store
from agent.state import get_state_store
from agent.threads import get_thread_manager
from agent.voice_runtime import default_voice_provider, get_voice_job_store, voice_provider_statuses
from agent.voice_transport import (
    VoiceTransportError,
    cancel_voice_playback,
    cancel_voice_playback_for_client,
    complete_voice_playback,
    get_voice_transport_store,
    synthesize_voice_chunk,
    transcribe_voice_turn,
)


router = APIRouter(prefix="/media-runtime", tags=["voice-generation"])


class VoiceTranscriptionRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(default="", max_length=200)
    client_turn_id: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    audio_base64: str = Field(min_length=4, max_length=24_000_000)
    mime_type: str = Field(default="audio/wav", max_length=80)
    provider_id: str = Field(default="", max_length=100)
    language: str = Field(default="", max_length=32)


class VoiceSynthesisRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(default="", max_length=200)
    client_turn_id: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    text: str = Field(min_length=1, max_length=1200)
    sequence: int = Field(default=0, ge=0, le=1000)
    provider_id: str = Field(default="", max_length=100)
    request_id: str = Field(default="", max_length=200)
    execution_id: str = Field(default="", max_length=200)
    task_run_id: str = Field(default="", max_length=200)
    settings: dict = Field(default_factory=dict)


class VoiceTurnDecisionRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)


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
        "voice_defaults": {
            "speech_to_text": default_voice_provider("speech_to_text"),
            "text_to_speech": default_voice_provider("text_to_speech"),
        },
        "generation": [item.model_dump(mode="json") for item in generation_provider_statuses()],
        "authority": {
            "owner": "python",
            "voice_submit_tool": "voice_synthesize_speech",
            "generation_submit_tool": "generation_submit",
            "direct_submit_api": False,
            "voice_transport": (
                "User-gesture microphone and playback transport only; final transcripts "
                "enter the canonical query runtime before any semantic work begins."
            ),
        },
    }


@router.post("/voice/transcribe")
def transcribe_voice_input(request: VoiceTranscriptionRequest):
    if request.mime_type.lower().split(";", 1)[0].strip() not in {
        "audio/wav", "audio/wave", "audio/x-wav"
    }:
        raise HTTPException(status_code=415, detail="Local Voice currently accepts PCM WAV audio")
    try:
        audio = base64.b64decode(request.audio_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="Voice audio is not valid base64") from exc
    try:
        turn, job = transcribe_voice_turn(
            session_id=request.session_id,
            project_id=request.project_id,
            client_turn_id=request.client_turn_id,
            audio_bytes=audio,
            provider_id=request.provider_id,
            language=request.language,
        )
    except VoiceTransportError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "turn": turn.model_dump(mode="json"),
        "job": job.model_dump(mode="json"),
        "event": {
            "type": "voice.transcript.final",
            "voice_turn_id": turn.id,
            "transcript": turn.transcript,
            "language": turn.transcript_language,
            "control_hint": turn.control_hint,
            "provider_id": turn.input_provider_id,
            "at": turn.updated_at,
        },
    }


@router.post("/voice/synthesize")
def synthesize_voice_output(request: VoiceSynthesisRequest):
    try:
        turn, job, clip = synthesize_voice_chunk(
            session_id=request.session_id,
            project_id=request.project_id,
            client_turn_id=request.client_turn_id,
            text=request.text,
            sequence=request.sequence,
            provider_id=request.provider_id,
            request_id=request.request_id,
            execution_id=request.execution_id,
            task_run_id=request.task_run_id,
            settings=request.settings,
        )
    except VoiceTransportError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "turn": turn.model_dump(mode="json"),
        "job": job.model_dump(mode="json"),
        "clip": clip.model_dump(mode="json"),
        "audio_url": (
            f"/media-runtime/voice/clips/{quote(clip.id, safe='')}/content"
            f"?session_id={quote(request.session_id, safe='')}"
        ),
        "event": {
            "type": "voice.playback.chunk_ready",
            "voice_turn_id": turn.id,
            "clip_id": clip.id,
            "sequence": clip.sequence,
            "provider_id": clip.provider_id,
            "at": turn.updated_at,
        },
    }


@router.get("/voice/clips/{clip_id}/content")
async def voice_clip_content(clip_id: str, session_id: str = Query(...)):
    _session_scope(session_id)
    store = get_voice_transport_store()
    try:
        clip = store.get_clip(clip_id)
    except VoiceTransportError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if clip is None or clip.session_id != session_id:
        raise HTTPException(status_code=404, detail="Voice clip not found")
    try:
        path = store.clip_path(clip)
    except (FileNotFoundError, OSError, ValueError):
        raise HTTPException(status_code=404, detail="Voice clip content is unavailable")
    if path.stat().st_size != clip.size_bytes or hashlib.sha256(path.read_bytes()).hexdigest() != clip.sha256:
        raise HTTPException(status_code=409, detail="Voice clip failed integrity validation")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"{clip.id}.wav",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/voice/turns")
async def list_voice_transport_turns(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
):
    state = _session_scope(session_id, project_id)
    rows = get_voice_transport_store().list_turns(session_id, limit=limit)
    rows = [row for row in rows if not project_id or row.project_id == state.active_project_id]
    return {"items": [row.model_dump(mode="json") for row in rows], "count": len(rows)}


@router.post("/voice/turns/{turn_id}/cancel")
async def cancel_voice_turn(turn_id: str, request: VoiceTurnDecisionRequest):
    try:
        turn = cancel_voice_playback(turn_id, session_id=request.session_id)
    except VoiceTransportError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"turn": turn.model_dump(mode="json")}


@router.post("/voice/client-turns/{client_turn_id}/cancel")
async def cancel_voice_client_turn(client_turn_id: str, request: VoiceTurnDecisionRequest):
    try:
        turn = cancel_voice_playback_for_client(
            client_turn_id,
            session_id=request.session_id,
        )
    except VoiceTransportError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"turn": turn.model_dump(mode="json")}


@router.post("/voice/turns/{turn_id}/complete")
async def complete_voice_turn(turn_id: str, request: VoiceTurnDecisionRequest):
    try:
        turn = complete_voice_playback(turn_id, session_id=request.session_id)
    except VoiceTransportError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"turn": turn.model_dump(mode="json")}


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
