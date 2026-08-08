"""Local-first speech transport for the canonical Echo Session runtime.

This module owns microphone-transcript and playback projections only. It never
interprets user intent, creates TaskRuns, executes model tools, or decides when
work is complete. A final transcript is submitted through the existing query
endpoint and is then rebound to the exact resulting Execution and TaskRun.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from loguru import logger

from agent.projects import get_project_manager
from agent.state import get_state_store
from agent.threads import get_thread_manager
from agent.voice_runtime import (
    VoiceJob,
    default_voice_provider,
    get_voice_job_store,
    synthesize_voice_audio,
    transcribe_voice_audio,
)
from config import DATA_DIR, config


VoiceTransportStatus = Literal[
    "transcribing",
    "transcript_ready",
    "submitted",
    "responding",
    "speaking",
    "completed",
    "cancelled",
    "failed",
]
VoiceControlHint = Literal[
    "message",
    "cancel_active",
    "canonical_continue",
    "canonical_steer",
    "canonical_inspect",
]


class VoiceTransportTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str = Field(default_factory=lambda: f"voice-turn-{uuid.uuid4().hex}")
    client_turn_id: str
    session_id: str
    project_id: str = ""
    transcript: str = ""
    transcript_language: str = ""
    input_provider_id: str = ""
    output_provider_id: str = ""
    input_job_id: str = ""
    output_job_ids: list[str] = Field(default_factory=list)
    control_hint: VoiceControlHint = "message"
    request_id: str = ""
    execution_id: str = ""
    task_run_id: str = ""
    query_completed: bool = False
    status: VoiceTransportStatus = "transcribing"
    playback_cancelled: bool = False
    error_code: str = ""
    user_message: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class VoiceTransportClip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str = Field(default_factory=lambda: f"voice-clip-{uuid.uuid4().hex}")
    turn_id: str
    session_id: str
    provider_id: str
    job_id: str
    sequence: int = Field(ge=0)
    relative_path: str
    sha256: str
    size_bytes: int = Field(ge=1)
    created_at: float = Field(default_factory=time.time)


class VoiceTransportError(RuntimeError):
    pass


def _bounded_failure(operation: str, error: Exception) -> tuple[str, str]:
    diagnostic_id = hashlib.sha256(
        f"{operation}:{type(error).__name__}:{error}".encode("utf-8", errors="ignore")
    ).hexdigest()[:12]
    logger.exception("Voice transport {} failed diagnostic_id={}", operation, diagnostic_id)
    message = (
        "Local transcription could not finish. Check Voice & Speech in Settings."
        if operation == "transcription"
        else "Local speech playback could not be prepared. Check Voice & Speech in Settings."
    )
    return diagnostic_id, message


def _safe_id(value: str, label: str) -> str:
    key = str(value or "").strip()
    if not key or not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", key):
        raise VoiceTransportError(f"Invalid {label}")
    return key


def validate_voice_scope(session_id: str, project_id: str = "") -> tuple[Any, Any]:
    """Validate transport scope without pretending a user microphone is a tool action."""

    session = _safe_id(session_id, "Session id")
    if get_thread_manager().get_thread(session) is None:
        raise VoiceTransportError("Session not found")
    state = get_state_store().get_thread_state(session)
    requested_project = str(project_id or "").strip()
    active_project = str(state.active_project_id or "").strip()
    if requested_project != active_project:
        raise VoiceTransportError("Voice Project does not match the active Session Project")
    project = None
    if requested_project:
        project = get_project_manager().get_project(requested_project)
        if project is None or project.archived:
            raise VoiceTransportError("Voice Project is unavailable")
    return state, project


def classify_voice_control(text: str) -> VoiceControlHint:
    """Recognize only bounded transport commands; all other semantics stay model-led."""

    normalized = re.sub(r"[^a-z0-9']+", " ", str(text or "").lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if normalized in {"stop", "stop that", "cancel", "cancel that", "never mind", "nevermind"}:
        return "cancel_active"
    if normalized in {"continue", "keep going", "resume", "go on"}:
        return "canonical_continue"
    if normalized in {"use another source", "try another source", "use a different source"}:
        return "canonical_steer"
    if normalized in {
        "what are you working on",
        "what are you doing",
        "what is the current task",
        "where are you at",
    }:
        return "canonical_inspect"
    return "message"


class VoiceTransportStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or (Path(DATA_DIR) / "voice_transport")).expanduser().resolve()
        self.turns_root = self.root / "turns"
        self.clips_root = self.root / "clips"
        self.corrupt_root = self.root / "corrupt-state"
        for path in (self.turns_root, self.clips_root, self.corrupt_root):
            path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _record_path(self, kind: Literal["turn", "clip"], record_id: str) -> Path:
        key = _safe_id(record_id, f"Voice {kind} id")
        root = self.turns_root if kind == "turn" else self.clips_root
        return root / f"{key}.json"

    def _quarantine(self, path: Path, error: Exception) -> None:
        stamp = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        target = self.corrupt_root / f"{path.stem}-{stamp}{path.suffix}"
        try:
            shutil.copy2(path, target)
            target.with_suffix(".diagnostic.txt").write_text(
                f"Malformed Voice transport record: {error}\nSource: {path}\n"
                "Recovery: repair the quarantine copy and restore it under the original id.\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def _read_turn(self, path: Path) -> VoiceTransportTurn:
        try:
            return VoiceTransportTurn.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._quarantine(path, exc)
            raise VoiceTransportError("Malformed Voice turn was quarantined") from exc

    def _read_clip(self, path: Path) -> VoiceTransportClip:
        try:
            return VoiceTransportClip.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._quarantine(path, exc)
            raise VoiceTransportError("Malformed Voice clip was quarantined") from exc

    @staticmethod
    def _write(path: Path, model: BaseModel) -> None:
        tmp = path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(model.model_dump_json(indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    def save_turn(self, turn: VoiceTransportTurn) -> VoiceTransportTurn:
        turn.updated_at = time.time()
        with self._lock:
            self._write(self._record_path("turn", turn.id), turn)
        return turn

    def save_clip(self, clip: VoiceTransportClip) -> VoiceTransportClip:
        with self._lock:
            self._write(self._record_path("clip", clip.id), clip)
        return clip

    def get_turn(self, turn_id: str) -> Optional[VoiceTransportTurn]:
        path = self._record_path("turn", turn_id)
        if not path.is_file():
            return None
        with self._lock:
            return self._read_turn(path)

    def get_clip(self, clip_id: str) -> Optional[VoiceTransportClip]:
        path = self._record_path("clip", clip_id)
        if not path.is_file():
            return None
        with self._lock:
            return self._read_clip(path)

    def find_client_turn(self, session_id: str, client_turn_id: str) -> Optional[VoiceTransportTurn]:
        session = str(session_id or "")
        client = str(client_turn_id or "")
        with self._lock:
            for path in self.turns_root.glob("*.json"):
                try:
                    turn = self._read_turn(path)
                except VoiceTransportError:
                    continue
                if turn.session_id == session and turn.client_turn_id == client:
                    return turn
        return None

    def claim_client_turn(self, candidate: VoiceTransportTurn) -> tuple[VoiceTransportTurn, bool]:
        """Atomically claim one Session/client transport identity."""

        with self._lock:
            existing = self.find_client_turn(candidate.session_id, candidate.client_turn_id)
            if existing is not None:
                if existing.project_id != candidate.project_id:
                    raise VoiceTransportError("Voice client turn already belongs to another Project scope")
                return existing, False
            self.save_turn(candidate)
            return candidate, True

    def list_turns(self, session_id: str, limit: int = 100) -> list[VoiceTransportTurn]:
        rows: list[VoiceTransportTurn] = []
        paths = sorted(self.turns_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        for path in paths:
            try:
                turn = self._read_turn(path)
            except VoiceTransportError:
                continue
            if turn.session_id == session_id:
                rows.append(turn)
            if len(rows) >= max(1, min(int(limit or 100), 500)):
                break
        return rows

    def clip_path(self, clip: VoiceTransportClip) -> Path:
        path = (self.clips_root / clip.relative_path).resolve(strict=True)
        path.relative_to(self.clips_root)
        return path


_STORE: Optional[VoiceTransportStore] = None


def get_voice_transport_store() -> VoiceTransportStore:
    global _STORE
    if _STORE is None:
        _STORE = VoiceTransportStore()
    return _STORE


def transcribe_voice_turn(
    *,
    session_id: str,
    project_id: str,
    client_turn_id: str,
    audio_bytes: bytes,
    provider_id: str = "",
    language: str = "",
) -> tuple[VoiceTransportTurn, VoiceJob]:
    validate_voice_scope(session_id, project_id)
    client_key = _safe_id(client_turn_id, "Voice client turn id")
    store = get_voice_transport_store()
    selected = str(provider_id or default_voice_provider("speech_to_text")).strip()
    requested_language = str(
        language or getattr(config, "voice_stt_language", "") or ""
    ).strip()
    input_identity = {
        "requested_language": requested_language,
        "audio_sha256": hashlib.sha256(audio_bytes).hexdigest(),
        "audio_size_bytes": len(audio_bytes),
        "audio_retained": False,
    }
    turn, claimed = store.claim_client_turn(
        VoiceTransportTurn(
            client_turn_id=client_key,
            session_id=session_id,
            project_id=project_id,
            input_provider_id=selected,
            status="transcribing",
        )
    )
    if not claimed:
        job = get_voice_job_store().get(turn.input_job_id) if turn.input_job_id else None
        if job is None:
            raise VoiceTransportError("Voice turn exists without its transcription job")
        if (
            turn.input_provider_id != selected
            or job.project_id != project_id
            or job.provider_id != selected
            or any(job.settings.get(key) != value for key, value in input_identity.items())
        ):
            raise VoiceTransportError("Voice transcription identity conflicts with an existing transport turn")
        if job.status in {"queued", "running"}:
            raise VoiceTransportError("Voice transcription is already being prepared")
        if job.status != "completed" or not turn.transcript:
            raise VoiceTransportError("The previous Voice transcription attempt did not produce a transcript")
        return turn, job
    job = VoiceJob(
        idempotency_key=f"voice-input:{client_key}",
        session_id=session_id,
        project_id=project_id,
        operation="speech_to_text",
        provider_id=selected,
        origin="voice_transport",
        transport_turn_id=turn.id,
        settings=input_identity,
        status="running",
        progress=0.1,
    )
    get_voice_job_store().save(job)
    turn.input_job_id = job.id
    store.save_turn(turn)
    try:
        result = transcribe_voice_audio(audio_bytes, provider_id=selected, language=requested_language)
        job.status = "completed"
        job.progress = 1.0
        job.transcript = result.text
        job.model = result.model
        job.settings = {
            **input_identity,
            "detected_language": result.language,
            "duration_seconds": result.duration_seconds,
        }
        get_voice_job_store().save(job)
        turn.transcript = result.text
        turn.transcript_language = result.language
        turn.control_hint = classify_voice_control(result.text)
        turn.status = "transcript_ready"
        return store.save_turn(turn), job
    except Exception as exc:
        diagnostic_id, message = _bounded_failure("transcription", exc)
        job.status = "failed"
        job.error_code = "transcription_failed"
        job.error = f"diagnostic:{diagnostic_id}"
        get_voice_job_store().save(job)
        turn.status = "failed"
        turn.error_code = job.error_code
        turn.user_message = message
        store.save_turn(turn)
        raise VoiceTransportError(message) from exc


def bind_voice_turn_submission(
    turn_id: str,
    *,
    session_id: str,
    request_id: str,
    execution_id: str = "",
    task_run_id: str = "",
    query_completed: bool = False,
) -> VoiceTransportTurn:
    store = get_voice_transport_store()
    turn = store.get_turn(turn_id)
    if turn is None or turn.session_id != session_id:
        raise VoiceTransportError("Voice turn does not belong to this Session")
    validate_voice_scope(session_id, turn.project_id)
    if turn.status in {"cancelled", "failed"}:
        raise VoiceTransportError("Voice turn is not eligible for submission")
    if turn.request_id and turn.request_id != request_id:
        raise VoiceTransportError("Voice turn is already bound to another request")
    turn.request_id = _safe_id(request_id, "Voice request id")
    if execution_id:
        execution = get_state_store().get_execution(execution_id)
        if execution is None or execution.session_id != session_id:
            raise VoiceTransportError("Voice Execution does not belong to this Session")
        if str(execution.project_id or "") != str(turn.project_id or ""):
            raise VoiceTransportError("Voice Execution Project does not match the transport turn")
        if task_run_id and str(execution.task_run_id or "") != str(task_run_id):
            raise VoiceTransportError("Voice TaskRun does not match the owning Execution")
        turn.execution_id = execution.id
        turn.task_run_id = str(task_run_id or execution.task_run_id or "")
        turn.query_completed = bool(turn.query_completed or query_completed)
        turn.status = "completed" if turn.query_completed and not turn.output_job_ids else "responding"
        for job_id in [turn.input_job_id, *turn.output_job_ids]:
            job = get_voice_job_store().get(job_id) if job_id else None
            if job is None or job.origin != "voice_transport":
                continue
            if job.execution_id and job.execution_id != execution.id:
                raise VoiceTransportError("Voice job is already bound to another Execution")
            job.execution_id = execution.id
            job.task_run_id = turn.task_run_id
            get_voice_job_store().save(job)
    elif turn.status == "transcript_ready":
        turn.status = "submitted"
    return store.save_turn(turn)


def prepare_voice_turn_submission(
    turn_id: str,
    *,
    session_id: str,
    request_id: str,
    transcript: str,
) -> VoiceTransportTurn:
    """Bind a final transcript once and prevent client-side text substitution."""

    turn = get_voice_transport_store().get_turn(turn_id)
    if turn is None or turn.session_id != session_id:
        raise VoiceTransportError("Voice turn does not belong to this Session")
    if turn.status != "transcript_ready" and not (
        turn.status == "submitted" and turn.request_id == request_id
    ):
        raise VoiceTransportError("Voice transcript is not ready for submission")
    if str(turn.transcript or "").strip() != str(transcript or "").strip():
        raise VoiceTransportError("Voice transcript does not match the durable transport turn")
    return bind_voice_turn_submission(
        turn.id,
        session_id=session_id,
        request_id=request_id,
    )


def fail_voice_turn(turn_id: str, *, session_id: str, error_code: str) -> None:
    store = get_voice_transport_store()
    turn = store.get_turn(turn_id)
    if turn is None or turn.session_id != session_id or turn.status in {"completed", "cancelled"}:
        return
    turn.status = "failed"
    turn.error_code = str(error_code or "voice_query_failed")[:100]
    turn.user_message = "Echo could not complete this spoken turn. The transcript remains in this Session."
    store.save_turn(turn)


def synthesize_voice_chunk(
    *,
    session_id: str,
    project_id: str,
    client_turn_id: str,
    text: str,
    sequence: int,
    provider_id: str = "",
    request_id: str = "",
    execution_id: str = "",
    task_run_id: str = "",
    settings: Optional[dict[str, Any]] = None,
) -> tuple[VoiceTransportTurn, VoiceJob, VoiceTransportClip]:
    validate_voice_scope(session_id, project_id)
    store = get_voice_transport_store()
    client_key = _safe_id(client_turn_id, "Voice client turn id")
    turn, _claimed = store.claim_client_turn(
        VoiceTransportTurn(
            client_turn_id=client_key,
            session_id=session_id,
            project_id=project_id,
            request_id=str(request_id or ""),
            status="responding",
        )
    )
    if request_id:
        turn = bind_voice_turn_submission(
            turn.id,
            session_id=session_id,
            request_id=request_id,
            execution_id=execution_id,
            task_run_id=task_run_id,
        )
    if turn.playback_cancelled:
        raise VoiceTransportError("Voice playback was cancelled")
    selected = str(provider_id or default_voice_provider("text_to_speech")).strip()
    stable_settings = dict(settings or {})
    # Sequence is the stable playback action identity. Provider, text, and
    # settings are rechecked below so a replay cannot smuggle changed content
    # into the same logical sentence slot.
    idempotency_key = f"voice-output:{client_key}:{int(sequence)}"
    candidate = VoiceJob(
        idempotency_key=idempotency_key,
        session_id=session_id,
        project_id=project_id,
        operation="text_to_speech",
        provider_id=selected,
        text=str(text or "").strip(),
        settings=stable_settings,
        origin="voice_transport",
        transport_turn_id=turn.id,
        execution_id=turn.execution_id,
        task_run_id=turn.task_run_id,
        status="running",
        progress=0.1,
    )
    job_store = get_voice_job_store()
    job, claimed = job_store.claim_idempotent(candidate)
    if not claimed:
        if (
            job.project_id != project_id
            or job.transport_turn_id != turn.id
            or job.provider_id != selected
            or job.text != str(text or "").strip()
            or dict(job.settings or {}) != stable_settings
        ):
            raise VoiceTransportError("Voice playback identity conflicts with an existing transport job")
        if job.output_asset_id:
            clip = store.get_clip(job.output_asset_id)
            if clip is not None:
                return turn, job, clip
            raise VoiceTransportError("Voice playback result exists without its durable clip")
        if job.status in {"queued", "running"}:
            raise VoiceTransportError("Voice playback is already being prepared")
        raise VoiceTransportError("The previous Voice playback attempt did not produce a clip")
    clip_id = f"voice-clip-{uuid.uuid4().hex}"
    relative_path = f"{clip_id}.wav"
    output = (store.clips_root / relative_path).resolve()
    output.relative_to(store.clips_root)
    try:
        used_provider = synthesize_voice_audio(
            job.text,
            output,
            provider_id=selected,
            settings=job.settings,
        )
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        clip = store.save_clip(
            VoiceTransportClip(
                id=clip_id,
                turn_id=turn.id,
                session_id=session_id,
                provider_id=used_provider,
                job_id=job.id,
                sequence=int(sequence),
                relative_path=relative_path,
                sha256=digest,
                size_bytes=output.stat().st_size,
            )
        )
        job.status = "completed"
        job.progress = 1.0
        job.output_asset_id = clip.id
        get_voice_job_store().save(job)
        turn.output_provider_id = used_provider
        if job.id not in turn.output_job_ids:
            turn.output_job_ids.append(job.id)
        turn.status = "speaking"
        return store.save_turn(turn), job, clip
    except Exception as exc:
        diagnostic_id, message = _bounded_failure("synthesis", exc)
        job.status = "failed"
        job.error_code = "synthesis_failed"
        job.error = f"diagnostic:{diagnostic_id}"
        get_voice_job_store().save(job)
        turn.status = "failed"
        turn.error_code = job.error_code
        turn.user_message = message
        store.save_turn(turn)
        raise VoiceTransportError(message) from exc


def cancel_voice_playback(turn_id: str, *, session_id: str) -> VoiceTransportTurn:
    store = get_voice_transport_store()
    turn = store.get_turn(turn_id)
    if turn is None or turn.session_id != session_id:
        raise VoiceTransportError("Voice turn does not belong to this Session")
    turn.playback_cancelled = True
    turn.status = "cancelled"
    for job_id in turn.output_job_ids:
        job = get_voice_job_store().get(job_id)
        if job is not None and job.status in {"queued", "running"}:
            job.cancellation_requested = True
            job.status = "cancelled"
            get_voice_job_store().save(job)
    return store.save_turn(turn)


def cancel_voice_playback_for_client(
    client_turn_id: str,
    *,
    session_id: str,
) -> VoiceTransportTurn:
    """Cancel the exact Session/client transport before its generated id is known."""

    client_key = _safe_id(client_turn_id, "Voice client turn id")
    state = get_state_store().get_thread_state(session_id)
    project_id = str(state.active_project_id or "")
    validate_voice_scope(session_id, project_id)
    store = get_voice_transport_store()
    turn, claimed = store.claim_client_turn(
        VoiceTransportTurn(
            client_turn_id=client_key,
            session_id=session_id,
            project_id=project_id,
            status="cancelled",
            playback_cancelled=True,
        )
    )
    if claimed:
        return turn
    return cancel_voice_playback(turn.id, session_id=session_id)


def complete_voice_playback(turn_id: str, *, session_id: str) -> VoiceTransportTurn:
    store = get_voice_transport_store()
    turn = store.get_turn(turn_id)
    if turn is None or turn.session_id != session_id:
        raise VoiceTransportError("Voice turn does not belong to this Session")
    if turn.transcript and not turn.query_completed:
        raise VoiceTransportError("Voice playback cannot complete before its canonical query is terminal")
    if not turn.playback_cancelled and turn.status not in {"failed", "cancelled"}:
        turn.status = "completed"
    return store.save_turn(turn)
