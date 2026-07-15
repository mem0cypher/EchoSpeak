"""Governed, provider-neutral Voice jobs.

Python owns provider readiness, durable jobs, authority validation, and audio
artifacts.  The React and Tauri layers may project these records but never
become a second voice-job authority.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent.media_library import MediaLibraryAsset, get_media_library_store
from agent.projects import get_project_manager
from agent.state import get_state_store
from agent.threads import get_thread_manager
from agent.tool_registry import ToolRegistry
from config import DATA_DIR, config


VoiceOperation = Literal["speech_to_text", "text_to_speech", "realtime"]
VoiceJobStatus = Literal["queued", "running", "completed", "blocked", "failed", "cancelled"]


class VoiceProviderStatus(BaseModel):
    id: str
    locality: Literal["local", "cloud"]
    operations: list[VoiceOperation]
    detected: bool
    configured: bool
    execution_ready: bool
    supports_streaming: bool = False
    supports_barge_in: bool = False
    supports_cancel: bool = False
    requires_microphone_permission: bool = False
    detail: str = ""


class VoiceJob(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=lambda: f"voice-{uuid.uuid4().hex}")
    idempotency_key: str
    session_id: str
    project_id: str
    operation: VoiceOperation
    provider_id: str
    model: str = ""
    text: str = ""
    input_asset_id: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)
    status: VoiceJobStatus = "queued"
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    output_asset_id: str = ""
    error_code: str = ""
    error: str = ""
    cancellation_requested: bool = False
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class VoiceRuntimeError(RuntimeError):
    pass


class VoiceJobStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or (Path(DATA_DIR) / "voice_jobs")).expanduser().resolve()
        self.jobs_root = self.root / "jobs"
        self.corrupt_root = self.root / "corrupt-state"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.corrupt_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, job_id: str) -> Path:
        value = str(job_id or "").strip()
        if not value or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in value):
            raise VoiceRuntimeError("Invalid VoiceJob id")
        return self.jobs_root / f"{value}.json"

    def _read(self, path: Path) -> VoiceJob:
        try:
            return VoiceJob.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            stamp = f"{int(time.time())}-{time.time_ns()}"
            target = self.corrupt_root / f"{path.stem}-{stamp}.json"
            try:
                shutil.copy2(path, target)
                target.with_suffix(".diagnostic.txt").write_text(
                    f"Malformed VoiceJob: {exc}\nSource: {path}\n"
                    "Recovery: repair the quarantined JSON and restore it to jobs/<id>.json.\n",
                    encoding="utf-8",
                )
            except OSError:
                pass
            raise VoiceRuntimeError(f"Malformed VoiceJob quarantined: {path.name}") from exc

    def save(self, job: VoiceJob) -> VoiceJob:
        job.updated_at = time.time()
        path = self._path(job.id)
        with self._lock:
            tmp = path.with_suffix(f".tmp.{os.getpid()}.{time.time_ns()}")
            with tmp.open("w", encoding="utf-8") as handle:
                handle.write(job.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        return job

    def get(self, job_id: str) -> Optional[VoiceJob]:
        path = self._path(job_id)
        return self._read(path) if path.exists() else None

    def list(self, *, session_id: str = "", limit: int = 100) -> list[VoiceJob]:
        rows: list[VoiceJob] = []
        for path in sorted(self.jobs_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                job = self._read(path)
            except VoiceRuntimeError:
                continue
            if session_id and job.session_id != session_id:
                continue
            rows.append(job)
            if len(rows) >= max(1, min(int(limit or 100), 500)):
                break
        return rows

    def find_idempotent(self, session_id: str, key: str) -> Optional[VoiceJob]:
        with self._lock:
            for path in self.jobs_root.glob("*.json"):
                try:
                    job = self._read(path)
                except VoiceRuntimeError:
                    continue
                if job.session_id == session_id and job.idempotency_key == key:
                    return job
        return None

    def recover_incomplete(self) -> int:
        recovered = 0
        jobs: list[VoiceJob] = []
        for path in self.jobs_root.glob("*.json"):
            try:
                jobs.append(self._read(path))
            except VoiceRuntimeError:
                continue
        for job in jobs:
            if job.status in {"queued", "running"}:
                job.status = "failed"
                job.error_code = "process_interrupted"
                job.error = "Voice worker stopped before a terminal result; submit a new approved job to retry."
                self.save(job)
                recovered += 1
        return recovered


def voice_provider_statuses() -> list[VoiceProviderStatus]:
    is_windows = sys.platform == "win32"
    sapi = is_windows and importlib.util.find_spec("win32com.client") is not None
    faster_whisper = importlib.util.find_spec("faster_whisper") is not None
    whisper_cpp = bool(shutil.which("whisper-cli") or shutil.which("whisper-cpp"))
    piper = bool(shutil.which("piper"))
    openai_key = bool(str(getattr(config.openai, "api_key", "") or "").strip())
    persona_enabled = bool(getattr(config.personaplex, "enabled", False))
    return [
        VoiceProviderStatus(
            id="windows-sapi",
            locality="local",
            operations=["text_to_speech"],
            detected=sapi,
            configured=sapi,
            execution_ready=sapi,
            detail="Windows SAPI WAV synthesis through the installed pywin32 bridge." if sapi else "Windows SAPI/pywin32 is unavailable.",
        ),
        VoiceProviderStatus(
            id="faster-whisper-local",
            locality="local",
            operations=["speech_to_text"],
            detected=faster_whisper,
            configured=faster_whisper,
            execution_ready=False,
            detail="Runtime detected; model selection/download remains an explicit environment gate." if faster_whisper else "faster-whisper is not installed.",
        ),
        VoiceProviderStatus(
            id="whisper-cpp-local",
            locality="local",
            operations=["speech_to_text"],
            detected=whisper_cpp,
            configured=whisper_cpp,
            execution_ready=False,
            detail="CLI detected; a governed model-path contract is not configured." if whisper_cpp else "whisper.cpp CLI is not installed.",
        ),
        VoiceProviderStatus(
            id="piper-local",
            locality="local",
            operations=["text_to_speech"],
            detected=piper,
            configured=piper,
            execution_ready=False,
            detail="Piper detected; voice-model selection remains an explicit gate." if piper else "Piper is not installed.",
        ),
        VoiceProviderStatus(
            id="personaplex",
            locality="local",
            operations=["realtime"],
            detected=persona_enabled,
            configured=persona_enabled,
            execution_ready=False,
            supports_streaming=True,
            supports_barge_in=True,
            supports_cancel=True,
            requires_microphone_permission=True,
            detail="Existing experimental client is configured but is not yet attached to governed VoiceJobs." if persona_enabled else "PersonaPlex is disabled.",
        ),
        VoiceProviderStatus(
            id="openai-audio",
            locality="cloud",
            operations=["speech_to_text", "text_to_speech", "realtime"],
            detected=openai_key,
            configured=openai_key and getattr(config, "voice_cloud_provider", "") == "openai-audio",
            execution_ready=False,
            supports_streaming=True,
            supports_barge_in=True,
            supports_cancel=True,
            requires_microphone_permission=True,
            detail="Credentials are present, but cloud audio remains disabled until explicit upload/cost approval is implemented." if openai_key else "OPENAI_API_KEY is not configured.",
        ),
    ]


def _provider(provider_id: str) -> VoiceProviderStatus:
    found = next((item for item in voice_provider_statuses() if item.id == provider_id), None)
    if found is None:
        raise VoiceRuntimeError(f"Unknown Voice provider: {provider_id}")
    return found


def _authority(session_id: str, project_id: str, tool_name: str) -> tuple[Any, Any, Path]:
    session = str(session_id or "").strip()
    project_key = str(project_id or "").strip()
    if not session or get_thread_manager().get_thread(session) is None:
        raise VoiceRuntimeError("Session not found")
    state = get_state_store().get_thread_state(session)
    if state.active_project_id != project_key:
        raise VoiceRuntimeError("Voice Project is not attached to this Session")
    project = get_project_manager().get_project(project_key)
    if project is None or project.archived:
        raise VoiceRuntimeError("Voice Project is unavailable")
    root = Path(project.workspace_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise VoiceRuntimeError("Voice Project root is unavailable")
    if not bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_voice_actions", False)):
        raise VoiceRuntimeError("Current configuration blocks Voice actions")
    permissions = dict(state.permissions or {})
    if not bool(permissions.get("system_actions") and permissions.get("voice_actions")):
        raise VoiceRuntimeError("Current Session permissions block Voice actions")
    if tool_name not in set(state.allowed_tool_names or []):
        raise VoiceRuntimeError("Current Session tool inventory blocks this Voice action")
    entry = ToolRegistry.get(tool_name)
    if entry is None or not entry.is_action:
        raise VoiceRuntimeError("Voice action is absent from the canonical ToolRegistry")
    return state, project, root


def _sapi_synthesize(text: str, output: Path, settings: dict[str, Any]) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        speaker.Rate = max(-10, min(10, int(settings.get("rate", 0) or 0)))
        speaker.Volume = max(0, min(100, int(settings.get("volume", 100) or 100)))
        stream.Open(str(output), 3, False)
        previous = speaker.AudioOutputStream
        try:
            speaker.AudioOutputStream = stream
            speaker.Speak(text)
        finally:
            speaker.AudioOutputStream = previous
            stream.Close()
    finally:
        pythoncom.CoUninitialize()


def submit_voice_job(request: VoiceJob, *, store: Optional[VoiceJobStore] = None) -> VoiceJob:
    store = store or get_voice_job_store()
    # Replays match stable request identity only after fresh current Session,
    # Project, configuration, permission, and ToolRegistry validation.
    _authority(request.session_id, request.project_id, "voice_synthesize_speech")
    provider = _provider(request.provider_id)
    with store._lock:
        existing = store.find_idempotent(request.session_id, request.idempotency_key)
        if existing is not None:
            stable = (existing.project_id, existing.operation, existing.provider_id, existing.text, existing.input_asset_id, existing.settings)
            incoming = (request.project_id, request.operation, request.provider_id, request.text, request.input_asset_id, request.settings)
            if stable != incoming:
                raise VoiceRuntimeError("Voice idempotency key already belongs to another stable request")
            return existing
        # Claim the idempotency key before provider execution so concurrent
        # callers observe one logical job.
        store.save(request)
    if request.operation not in provider.operations:
        request.status, request.error_code, request.error = "blocked", "unsupported_operation", "Provider does not support this Voice operation"
        return store.save(request)
    if not provider.execution_ready:
        request.status, request.error_code, request.error = "blocked", "provider_unavailable", provider.detail
        return store.save(request)
    if request.operation != "text_to_speech" or request.provider_id != "windows-sapi":
        request.status, request.error_code, request.error = "blocked", "adapter_not_implemented", "Selected provider has no governed execution adapter"
        return store.save(request)
    text_value = request.text.strip()
    if not text_value or len(text_value) > 5000:
        request.status, request.error_code, request.error = "blocked", "invalid_text", "Text must contain 1 to 5000 characters"
        return store.save(request)
    _, project, root = _authority(request.session_id, request.project_id, "voice_synthesize_speech")
    relative = Path(".echospeak") / "voice" / f"{request.id}.wav"
    output = (root / relative).resolve()
    output.relative_to(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    request.status, request.progress = "running", 0.1
    store.save(request)
    try:
        _sapi_synthesize(text_value, output, request.settings)
        if not output.is_file() or output.stat().st_size <= 44:
            raise VoiceRuntimeError("SAPI did not produce a valid non-empty WAV artifact")
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        asset_id = f"asset-{request.id}"
        get_media_library_store().register(
            MediaLibraryAsset(
                id=asset_id,
                project_id=request.project_id,
                session_id=request.session_id,
                name=output.name,
                media_kind="audio",
                source_kind="generated",
                project_relative_path=relative.as_posix(),
                sha256=digest,
                size_bytes=output.stat().st_size,
                provider=request.provider_id,
                model=request.model or "system-default-voice",
                settings=dict(request.settings),
                job_id=request.id,
            )
        )
        request.status, request.progress, request.output_asset_id = "completed", 1.0, asset_id
        return store.save(request)
    except Exception as exc:
        request.status, request.error_code, request.error = "failed", "synthesis_failed", str(exc)
        return store.save(request)


_STORE: Optional[VoiceJobStore] = None


def get_voice_job_store() -> VoiceJobStore:
    global _STORE
    if _STORE is None:
        _STORE = VoiceJobStore()
        _STORE.recover_incomplete()
    return _STORE


class VoiceSynthesisArgs(BaseModel):
    session_id: str
    project_id: str
    text: str = Field(min_length=1, max_length=5000)
    idempotency_key: str = Field(min_length=1, max_length=200)
    provider_id: str = "windows-sapi"
    model: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)


@ToolRegistry.register(
    name="voice_synthesize_speech",
    description="Create a governed Project-owned speech audio artifact.",
    category="voice",
    is_action=True,
    risk_level="moderate",
    policy_flags=["ENABLE_SYSTEM_ACTIONS", "ALLOW_VOICE_ACTIONS"],
    keyword_hints=["voice", "speak", "speech", "tts"],
)
@tool(args_schema=VoiceSynthesisArgs, description="Create a governed Project-owned speech audio artifact.")
def voice_synthesize_speech(
    session_id: str,
    project_id: str,
    text: str,
    idempotency_key: str,
    provider_id: str = "windows-sapi",
    model: str = "",
    settings: Optional[dict[str, Any]] = None,
) -> str:
    try:
        job = submit_voice_job(
            VoiceJob(
                session_id=session_id,
                project_id=project_id,
                operation="text_to_speech",
                provider_id=provider_id,
                model=model,
                text=text,
                settings=dict(settings or {}),
                idempotency_key=idempotency_key,
            )
        )
        return json.dumps({"ok": job.status == "completed", "job": job.model_dump(mode="json")}, default=str)
    except Exception as exc:
        return json.dumps({"ok": False, "error_code": "voice_job_failed", "error": str(exc)})


@ToolRegistry.register(
    name="voice_list_capabilities",
    description="Report detected Voice providers without opening a microphone or transmitting audio.",
    category="voice",
    risk_level="safe",
)
@tool(description="Report detected Voice providers without opening a microphone or transmitting audio.")
def voice_list_capabilities() -> str:
    return json.dumps({"ok": True, "providers": [item.model_dump(mode="json") for item in voice_provider_statuses()]})
