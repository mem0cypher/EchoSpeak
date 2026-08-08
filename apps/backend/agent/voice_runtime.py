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
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave
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
    operation_readiness: dict[VoiceOperation, bool] = Field(default_factory=dict)
    detected: bool
    configured: bool
    execution_ready: bool
    supports_streaming: bool = False
    supports_barge_in: bool = False
    supports_cancel: bool = False
    requires_microphone_permission: bool = False
    detail: str = ""


class VoiceTranscriptResult(BaseModel):
    """Provider-neutral final transcript produced from one bounded audio clip."""

    text: str
    provider_id: str
    model: str = ""
    language: str = ""
    duration_seconds: float = 0.0
    confidence: Optional[float] = None


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
    origin: Literal["canonical_tool", "voice_transport"] = "canonical_tool"
    transport_turn_id: str = ""
    transcript: str = ""
    execution_id: str = ""
    task_run_id: str = ""
    requirement_id: str = ""
    attempt_id: str = ""
    tool_run_id: str = ""
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

    def claim_idempotent(self, candidate: VoiceJob) -> tuple[VoiceJob, bool]:
        """Atomically claim one Session/idempotency identity."""

        with self._lock:
            existing = self.find_idempotent(candidate.session_id, candidate.idempotency_key)
            if existing is not None:
                return existing, False
            self.save(candidate)
            return candidate, True

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
    sapi_tts = is_windows and bool(shutil.which("powershell.exe"))
    sapi_stt = sapi_tts and _windows_sapi_recognizer_installed()
    faster_whisper = importlib.util.find_spec("faster_whisper") is not None
    whisper_cpp = bool(shutil.which("whisper-cli") or shutil.which("whisper-cpp"))
    piper = bool(shutil.which("piper"))
    faster_whisper_model = _existing_path(getattr(config, "voice_faster_whisper_model_path", ""))
    whisper_cpp_model = _existing_file(getattr(config, "voice_whisper_cpp_model_path", ""))
    piper_model = _existing_file(getattr(config, "voice_piper_model_path", ""))
    openai_key = bool(str(getattr(config.openai, "api_key", "") or "").strip())
    persona_enabled = bool(getattr(config.personaplex, "enabled", False))
    return [
        VoiceProviderStatus(
            id="windows-sapi",
            locality="local",
            operations=["speech_to_text", "text_to_speech"],
            operation_readiness={"speech_to_text": sapi_stt, "text_to_speech": sapi_tts},
            detected=sapi_tts or sapi_stt,
            configured=sapi_tts or sapi_stt,
            execution_ready=sapi_tts or sapi_stt,
            supports_cancel=True,
            requires_microphone_permission=sapi_stt,
            detail=(
                "Local Windows speech recognition and WAV synthesis are available."
                if sapi_tts and sapi_stt
                else "Local Windows speech recognition is available; text-to-speech is unavailable."
                if sapi_stt
                else "Local Windows text-to-speech is available; speech recognition is unavailable."
                if sapi_tts
                else "Windows speech services are unavailable."
            ),
        ),
        VoiceProviderStatus(
            id="faster-whisper-local",
            locality="local",
            operations=["speech_to_text"],
            operation_readiness={"speech_to_text": bool(faster_whisper and faster_whisper_model)},
            detected=faster_whisper,
            configured=bool(faster_whisper and faster_whisper_model),
            execution_ready=bool(faster_whisper and faster_whisper_model),
            requires_microphone_permission=True,
            detail=(
                f"Ready with the explicitly configured local model {faster_whisper_model.name}."
                if faster_whisper and faster_whisper_model
                else "Runtime detected; configure an existing local model path. EchoSpeak will not download one implicitly."
                if faster_whisper
                else "faster-whisper is not installed."
            ),
        ),
        VoiceProviderStatus(
            id="whisper-cpp-local",
            locality="local",
            operations=["speech_to_text"],
            operation_readiness={"speech_to_text": bool(whisper_cpp and whisper_cpp_model)},
            detected=whisper_cpp,
            configured=bool(whisper_cpp and whisper_cpp_model),
            execution_ready=bool(whisper_cpp and whisper_cpp_model),
            requires_microphone_permission=True,
            detail=(
                f"Ready with the explicitly configured local model {whisper_cpp_model.name}."
                if whisper_cpp and whisper_cpp_model
                else "CLI detected; configure an existing local model path."
                if whisper_cpp
                else "whisper.cpp CLI is not installed."
            ),
        ),
        VoiceProviderStatus(
            id="piper-local",
            locality="local",
            operations=["text_to_speech"],
            operation_readiness={"text_to_speech": bool(piper and piper_model)},
            detected=piper,
            configured=bool(piper and piper_model),
            execution_ready=bool(piper and piper_model),
            supports_cancel=True,
            detail=(
                f"Ready with the explicitly configured local voice {piper_model.name}."
                if piper and piper_model
                else "Piper detected; configure an existing local voice model path."
                if piper
                else "Piper is not installed."
            ),
        ),
        VoiceProviderStatus(
            id="personaplex",
            locality="local",
            operations=["realtime"],
            operation_readiness={"realtime": False},
            detected=persona_enabled,
            configured=persona_enabled,
            execution_ready=False,
            supports_streaming=False,
            supports_barge_in=False,
            supports_cancel=False,
            requires_microphone_permission=True,
            detail=(
                "The legacy experiment is configured but hard-disabled because PersonaPlex would become a second conversational model authority."
                if persona_enabled
                else "PersonaPlex is disabled and is not part of the canonical Voice transport."
            ),
        ),
        VoiceProviderStatus(
            id="openai-audio",
            locality="cloud",
            operations=["speech_to_text", "text_to_speech", "realtime"],
            operation_readiness={
                "speech_to_text": False,
                "text_to_speech": False,
                "realtime": False,
            },
            detected=openai_key,
            configured=openai_key and getattr(config, "voice_cloud_provider", "") == "openai-audio",
            execution_ready=False,
            supports_streaming=False,
            supports_barge_in=False,
            supports_cancel=False,
            requires_microphone_permission=True,
            detail="Credentials are present, but cloud audio remains disabled until explicit upload/cost approval is implemented." if openai_key else "OPENAI_API_KEY is not configured.",
        ),
    ]


def _existing_file(value: Any) -> Optional[Path]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        path = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return path if path.is_file() else None


def _windows_sapi_recognizer_installed() -> bool:
    """Conservatively detect a legacy System.Speech recognizer without launching it."""

    if sys.platform != "win32" or not shutil.which("powershell.exe"):
        return False
    try:
        import winreg

        access = winreg.KEY_READ
        views = [getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0)]
        keys = [
            r"SOFTWARE\Microsoft\Speech\Recognizers\Tokens",
            r"SOFTWARE\WOW6432Node\Microsoft\Speech\Recognizers\Tokens",
        ]
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key_name in keys:
                for view in views:
                    try:
                        with winreg.OpenKey(root, key_name, 0, access | view) as key:
                            if winreg.QueryInfoKey(key)[0] > 0:
                                return True
                    except OSError:
                        continue
    except (ImportError, OSError):
        return False
    return False


def _existing_path(value: Any) -> Optional[Path]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        path = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return path if path.is_file() or path.is_dir() else None


def default_voice_provider(operation: VoiceOperation) -> str:
    """Resolve one explicitly configured provider; credentials never select it."""

    if operation == "speech_to_text":
        return str(getattr(config, "voice_local_stt_provider", "windows-sapi") or "windows-sapi")
    if operation == "text_to_speech":
        return str(getattr(config, "voice_local_tts_provider", "windows-sapi") or "windows-sapi")
    return ""


def _validate_pcm_wav(audio_path: Path) -> float:
    try:
        with wave.open(str(audio_path), "rb") as source:
            channels = int(source.getnchannels())
            width = int(source.getsampwidth())
            rate = int(source.getframerate())
            frames = int(source.getnframes())
    except (OSError, wave.Error) as exc:
        raise VoiceRuntimeError("Local speech input must be a valid PCM WAV recording") from exc
    if channels not in {1, 2} or width not in {1, 2, 3, 4} or rate < 8_000 or rate > 96_000:
        raise VoiceRuntimeError("Speech input uses an unsupported WAV format")
    duration = frames / float(rate or 1)
    if duration <= 0.05 or duration > 180.0:
        raise VoiceRuntimeError("Speech input must be between 0.05 and 180 seconds")
    return duration


def _windows_sapi_transcribe(audio_path: Path, language: str = "") -> tuple[str, str]:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        raise VoiceRuntimeError("Windows speech recognition is unavailable")
    script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$path = $env:ECHOSPEAK_VOICE_AUDIO
$requested = $env:ECHOSPEAK_VOICE_LANGUAGE
$available = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
if (-not $available -or $available.Count -eq 0) { throw 'No Windows speech recognition language is installed.' }
$selected = $available | Where-Object { -not $requested -or $_.Culture.Name -eq $requested } | Select-Object -First 1
if ($null -eq $selected) { throw "The requested Windows speech language is not installed." }
$engine = New-Object System.Speech.Recognition.SpeechRecognitionEngine($selected)
$grammar = New-Object System.Speech.Recognition.DictationGrammar
$engine.LoadGrammar($grammar)
$engine.SetInputToWaveFile($path)
$parts = New-Object System.Collections.Generic.List[string]
while ($true) {
  $result = $engine.Recognize()
  if ($null -eq $result) { break }
  if ($result.Text) { $parts.Add($result.Text) }
}
$engine.Dispose()
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
@{ text = ($parts -join ' ').Trim(); language = $selected.Culture.Name } | ConvertTo-Json -Compress
"""
    env = os.environ.copy()
    env["ECHOSPEAK_VOICE_AUDIO"] = str(audio_path)
    env["ECHOSPEAK_VOICE_LANGUAGE"] = str(language or "").strip()
    completed = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=90,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise VoiceRuntimeError("Windows could not transcribe this recording")
    try:
        payload = json.loads((completed.stdout or "").strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise VoiceRuntimeError("Windows speech recognition returned an invalid result") from exc
    return str(payload.get("text") or "").strip(), str(payload.get("language") or "").strip()


def _faster_whisper_transcribe(audio_path: Path, language: str = "") -> tuple[str, str]:
    model_path = _existing_path(getattr(config, "voice_faster_whisper_model_path", ""))
    if model_path is None:
        raise VoiceRuntimeError("A local faster-whisper model path is not configured")
    from faster_whisper import WhisperModel

    model = WhisperModel(str(model_path), device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(audio_path),
        language=str(language or "").split("-", 1)[0] or None,
        vad_filter=True,
    )
    text = " ".join(str(item.text or "").strip() for item in segments).strip()
    return text, str(getattr(info, "language", "") or language or "")


def _whisper_cpp_transcribe(audio_path: Path, language: str = "") -> tuple[str, str]:
    executable = shutil.which("whisper-cli") or shutil.which("whisper-cpp")
    model_path = _existing_file(getattr(config, "voice_whisper_cpp_model_path", ""))
    if not executable or model_path is None:
        raise VoiceRuntimeError("whisper.cpp and an explicit local model path are required")
    with tempfile.TemporaryDirectory(prefix="echospeak-whisper-") as folder:
        output_stem = Path(folder) / "transcript"
        command = [executable, "-m", str(model_path), "-f", str(audio_path), "-otxt", "-of", str(output_stem), "-nt"]
        short_language = str(language or "").split("-", 1)[0].strip()
        if short_language:
            command.extend(["-l", short_language])
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            raise VoiceRuntimeError("whisper.cpp could not transcribe this recording")
        transcript_path = output_stem.with_suffix(".txt")
        if not transcript_path.is_file():
            raise VoiceRuntimeError("whisper.cpp did not produce a transcript")
        return transcript_path.read_text(encoding="utf-8", errors="replace").strip(), short_language


def transcribe_voice_audio(
    audio_bytes: bytes,
    *,
    provider_id: str = "",
    language: str = "",
) -> VoiceTranscriptResult:
    """Transcribe one local PCM WAV without opening a microphone or downloading a model."""

    maximum = int(getattr(config, "voice_max_audio_bytes", 16_777_216) or 16_777_216)
    if not audio_bytes or len(audio_bytes) > maximum:
        raise VoiceRuntimeError(f"Speech input must contain between 1 and {maximum} bytes")
    selected = str(provider_id or default_voice_provider("speech_to_text")).strip()
    provider = _provider(selected)
    if provider.locality != "local":
        raise VoiceRuntimeError("Cloud speech input requires an explicit connected cloud-voice adapter")
    if not provider.operation_readiness.get("speech_to_text", False):
        raise VoiceRuntimeError(provider.detail or "The selected local speech provider is not ready")
    with tempfile.TemporaryDirectory(prefix="echospeak-voice-") as folder:
        audio_path = Path(folder) / "input.wav"
        audio_path.write_bytes(audio_bytes)
        duration = _validate_pcm_wav(audio_path)
        if selected == "windows-sapi":
            text, detected_language = _windows_sapi_transcribe(audio_path, language)
            model = "windows-installed-recognizer"
        elif selected == "faster-whisper-local":
            text, detected_language = _faster_whisper_transcribe(audio_path, language)
            model = Path(str(getattr(config, "voice_faster_whisper_model_path", ""))).name
        elif selected == "whisper-cpp-local":
            text, detected_language = _whisper_cpp_transcribe(audio_path, language)
            model = Path(str(getattr(config, "voice_whisper_cpp_model_path", ""))).name
        else:
            raise VoiceRuntimeError("The selected local speech provider has no transcription adapter")
    if not text:
        raise VoiceRuntimeError("No speech was recognized in this recording")
    return VoiceTranscriptResult(
        text=text,
        provider_id=selected,
        model=model,
        language=detected_language,
        duration_seconds=duration,
    )


def synthesize_voice_audio(
    text: str,
    output: Path,
    *,
    provider_id: str = "",
    settings: Optional[dict[str, Any]] = None,
) -> str:
    """Synthesize one bounded local speech chunk to a WAV file."""

    selected = str(provider_id or default_voice_provider("text_to_speech")).strip()
    provider = _provider(selected)
    if provider.locality != "local":
        raise VoiceRuntimeError("Cloud speech output requires an explicit connected cloud-voice adapter")
    if not provider.operation_readiness.get("text_to_speech", False):
        raise VoiceRuntimeError(provider.detail or "The selected local speech provider is not ready")
    value = str(text or "").strip()
    if not value or len(value) > 1200:
        raise VoiceRuntimeError("Each speech chunk must contain 1 to 1200 characters")
    output.parent.mkdir(parents=True, exist_ok=True)
    options = dict(settings or {})
    if selected == "windows-sapi":
        _sapi_synthesize(value, output, options)
    elif selected == "piper-local":
        executable = shutil.which("piper")
        model_path = _existing_file(getattr(config, "voice_piper_model_path", ""))
        if not executable or model_path is None:
            raise VoiceRuntimeError("Piper and an explicit local voice model are required")
        completed = subprocess.run(
            [executable, "--model", str(model_path), "--output_file", str(output)],
            input=value,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            raise VoiceRuntimeError("Piper could not synthesize this speech chunk")
    else:
        raise VoiceRuntimeError("The selected local speech provider has no synthesis adapter")
    if not output.is_file() or output.stat().st_size <= 44:
        raise VoiceRuntimeError("The speech provider did not produce a valid WAV clip")
    return selected


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
    from agent.tools import get_tool_execution_context
    turn = dict(get_tool_execution_context() or {})
    if not str(turn.get("execution_id") or ""):
        raise VoiceRuntimeError("Voice action is not bound to a current Turn Execution")
    if str(turn.get("thread_id") or "") != session:
        raise VoiceRuntimeError("Voice action is bound to a different Session")
    if tool_name not in set(turn.get("allowed_tool_names") or []):
        raise VoiceRuntimeError("Current Turn tool inventory blocks this Voice action")
    turn_root = str(turn.get("project_root") or "").strip()
    if not turn_root or Path(turn_root).expanduser().resolve(strict=True) != root:
        raise VoiceRuntimeError("Current Turn Project root does not match the authoritative Project")
    entry = ToolRegistry.get(tool_name)
    if entry is None or not entry.is_action:
        raise VoiceRuntimeError("Voice action is absent from the canonical ToolRegistry")
    return state, project, root


def _sapi_synthesize(text: str, output: Path, settings: dict[str, Any]) -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        raise VoiceRuntimeError("Windows speech synthesis is unavailable")
    env = os.environ.copy()
    env["ECHOSPEAK_VOICE_TEXT"] = text
    env["ECHOSPEAK_VOICE_OUTPUT"] = str(output)
    env["ECHOSPEAK_VOICE_RATE"] = str(max(-10, min(10, int(settings.get("rate", 0) or 0))))
    env["ECHOSPEAK_VOICE_VOLUME"] = str(max(0, min(100, int(settings.get("volume", 100) or 100))))
    script = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $speaker.Rate = [int]$env:ECHOSPEAK_VOICE_RATE
  $speaker.Volume = [int]$env:ECHOSPEAK_VOICE_VOLUME
  $speaker.SetOutputToWaveFile($env:ECHOSPEAK_VOICE_OUTPUT)
  $speaker.Speak($env:ECHOSPEAK_VOICE_TEXT)
} finally {
  $speaker.Dispose()
}
"""
    completed = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=90,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise VoiceRuntimeError("Windows could not synthesize this speech chunk")


def submit_voice_job(request: VoiceJob, *, store: Optional[VoiceJobStore] = None) -> VoiceJob:
    store = store or get_voice_job_store()
    # Replays match stable request identity only after fresh current Session,
    # Project, configuration, permission, and ToolRegistry validation.
    _authority(request.session_id, request.project_id, "voice_synthesize_speech")
    from agent.media_jobs import bind_media_job, current_media_job_binding
    try:
        request = bind_media_job(request, current_media_job_binding())
    except RuntimeError as exc:
        raise VoiceRuntimeError(str(exc)) from exc
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
