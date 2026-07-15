"""Provider-neutral image/video generation contracts and durable job authority."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

from agent.media_library import MediaLibraryAsset, get_media_library_store
from agent.projects import get_project_manager
from agent.state import get_state_store
from agent.threads import get_thread_manager
from agent.tool_registry import ToolRegistry
from config import DATA_DIR, config


GenerationKind = Literal["image", "video"]
GenerationJobStatus = Literal["queued", "running", "completed", "blocked", "failed", "cancelled"]


class GenerationSettings(BaseModel):
    width: int = Field(default=1024, ge=256, le=4096)
    height: int = Field(default=1024, ge=256, le=4096)
    duration_seconds: int = Field(default=4, ge=1, le=60)
    seed: Optional[int] = Field(default=None, ge=0)
    quality: Literal["draft", "standard", "high"] = "standard"
    negative_prompt: str = Field(default="", max_length=2000)


class GenerationProviderStatus(BaseModel):
    id: str
    locality: Literal["local", "cloud"]
    kinds: list[GenerationKind]
    detected: bool
    configured: bool
    execution_ready: bool
    supports_progress: bool = False
    supports_cancel: bool = False
    requires_cost_approval: bool = False
    detail: str = ""


class GenerationJob(BaseModel):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=lambda: f"generation-{uuid.uuid4().hex}")
    idempotency_key: str
    session_id: str
    project_id: str
    kind: GenerationKind
    provider_id: str
    model: str
    prompt: str
    settings: GenerationSettings = Field(default_factory=GenerationSettings)
    input_asset_ids: list[str] = Field(default_factory=list)
    status: GenerationJobStatus = "queued"
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    provider_job_id: str = ""
    output_asset_ids: list[str] = Field(default_factory=list)
    retry_of: str = ""
    cancellation_requested: bool = False
    error_code: str = ""
    error: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    @model_validator(mode="after")
    def validate_kind_settings(self) -> "GenerationJob":
        if self.kind == "image" and self.settings.duration_seconds != 4:
            # Duration is provider-neutral Video input, not an image option.
            self.settings.duration_seconds = 4
        if not self.prompt.strip() or len(self.prompt) > 8000:
            raise ValueError("prompt must contain 1 to 8000 characters")
        return self


class GenerationRuntimeError(RuntimeError):
    pass


class GenerationJobStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or (Path(DATA_DIR) / "generation_jobs")).expanduser().resolve()
        self.jobs_root = self.root / "jobs"
        self.corrupt_root = self.root / "corrupt-state"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.corrupt_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, job_id: str) -> Path:
        value = str(job_id or "").strip()
        if not value or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in value):
            raise GenerationRuntimeError("Invalid GenerationJob id")
        return self.jobs_root / f"{value}.json"

    def _read(self, path: Path) -> GenerationJob:
        try:
            return GenerationJob.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            target = self.corrupt_root / f"{path.stem}-{int(time.time())}-{time.time_ns()}.json"
            try:
                shutil.copy2(path, target)
                target.with_suffix(".diagnostic.txt").write_text(
                    f"Malformed GenerationJob: {exc}\nSource: {path}\n"
                    "Recovery: repair the quarantined JSON and restore it to jobs/<id>.json.\n",
                    encoding="utf-8",
                )
            except OSError:
                pass
            raise GenerationRuntimeError(f"Malformed GenerationJob quarantined: {path.name}") from exc

    def save(self, job: GenerationJob) -> GenerationJob:
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

    def get(self, job_id: str) -> Optional[GenerationJob]:
        path = self._path(job_id)
        return self._read(path) if path.exists() else None

    def list(self, *, session_id: str = "", limit: int = 100) -> list[GenerationJob]:
        rows: list[GenerationJob] = []
        for path in sorted(self.jobs_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                job = self._read(path)
            except GenerationRuntimeError:
                continue
            if session_id and job.session_id != session_id:
                continue
            rows.append(job)
            if len(rows) >= max(1, min(int(limit or 100), 500)):
                break
        return rows

    def find_idempotent(self, session_id: str, key: str) -> Optional[GenerationJob]:
        with self._lock:
            for path in self.jobs_root.glob("*.json"):
                try:
                    job = self._read(path)
                except GenerationRuntimeError:
                    continue
                if job.session_id == session_id and job.idempotency_key == key:
                    return job
        return None

    def recover_incomplete(self) -> int:
        recovered = 0
        jobs: list[GenerationJob] = []
        for path in self.jobs_root.glob("*.json"):
            try:
                jobs.append(self._read(path))
            except GenerationRuntimeError:
                continue
        for job in jobs:
            if job.status in {"queued", "running"}:
                job.status = "failed"
                job.error_code = "process_interrupted"
                job.error = "Generation worker stopped before a terminal result; submit a new approved job to retry."
                self.save(job)
                recovered += 1
        return recovered


def _comfyui_detected() -> tuple[bool, str]:
    base = str(getattr(config, "comfyui_base_url", "") or "").strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
        return False, "COMFYUI_BASE_URL must be loopback for the local adapter."
    try:
        request = Request(f"{base}/system_stats", headers={"Accept": "application/json"})
        with urlopen(request, timeout=0.35) as response:
            if response.status != 200:
                return False, f"ComfyUI returned HTTP {response.status}."
            json.loads(response.read(1024 * 1024).decode("utf-8"))
        return True, "Local ComfyUI server is reachable."
    except Exception as exc:
        return False, f"Local ComfyUI is not reachable: {type(exc).__name__}."


def generation_provider_statuses() -> list[GenerationProviderStatus]:
    comfy, comfy_detail = _comfyui_detected()
    workflow = str(getattr(config, "comfyui_workflow_path", "") or "").strip()
    workflow_ready = bool(workflow and Path(workflow).expanduser().is_file())
    openai_key = bool(str(getattr(config.openai, "api_key", "") or "").strip())
    vertex = bool(str(getattr(config, "vertex_project_id", "") or "").strip())
    runway = bool(str(getattr(config, "runway_api_key", "") or "").strip())
    return [
        GenerationProviderStatus(
            id="comfyui-local",
            locality="local",
            kinds=["image", "video"],
            detected=comfy,
            configured=comfy and workflow_ready,
            execution_ready=False,
            supports_progress=True,
            supports_cancel=True,
            detail=(
                "Server and workflow are configured; the governed workflow compiler/output verifier is not implemented."
                if comfy and workflow_ready
                else f"{comfy_detail} Configure an explicit API-format workflow template to continue."
            ),
        ),
        GenerationProviderStatus(
            id="openai-images",
            locality="cloud",
            kinds=["image"],
            detected=openai_key,
            configured=openai_key and getattr(config, "generation_cloud_provider", "") == "openai-images",
            execution_ready=False,
            supports_progress=True,
            supports_cancel=True,
            requires_cost_approval=True,
            detail="Credentials detected; adapter is held behind explicit cost/upload approval." if openai_key else "OPENAI_API_KEY is not configured.",
        ),
        GenerationProviderStatus(
            id="openai-video",
            locality="cloud",
            kinds=["video"],
            detected=openai_key,
            configured=openai_key and getattr(config, "generation_cloud_provider", "") == "openai-video",
            execution_ready=False,
            supports_progress=True,
            supports_cancel=True,
            requires_cost_approval=True,
            detail="Credentials detected; adapter is held behind explicit cost/upload approval." if openai_key else "OPENAI_API_KEY is not configured.",
        ),
        GenerationProviderStatus(
            id="vertex-media",
            locality="cloud",
            kinds=["image", "video"],
            detected=vertex,
            configured=vertex and getattr(config, "generation_cloud_provider", "") == "vertex-media",
            execution_ready=False,
            supports_progress=True,
            supports_cancel=True,
            requires_cost_approval=True,
            detail="Vertex Project detected; credentials, region, quota, and approval adapter remain gated." if vertex else "VERTEX_PROJECT_ID is not configured.",
        ),
        GenerationProviderStatus(
            id="runway",
            locality="cloud",
            kinds=["image", "video"],
            detected=runway,
            configured=runway and getattr(config, "generation_cloud_provider", "") == "runway",
            execution_ready=False,
            supports_progress=True,
            supports_cancel=True,
            requires_cost_approval=True,
            detail="Runway credentials detected; cost/upload approval adapter remains gated." if runway else "RUNWAY_API_KEY is not configured.",
        ),
    ]


def _authority(session_id: str, project_id: str, tool_name: str) -> tuple[Any, Any, Path]:
    session = str(session_id or "").strip()
    if not session or get_thread_manager().get_thread(session) is None:
        raise GenerationRuntimeError("Session not found")
    state = get_state_store().get_thread_state(session)
    if state.active_project_id != project_id:
        raise GenerationRuntimeError("Generation Project is not attached to this Session")
    project = get_project_manager().get_project(project_id)
    if project is None or project.archived:
        raise GenerationRuntimeError("Generation Project is unavailable")
    root = Path(project.workspace_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise GenerationRuntimeError("Generation Project root is unavailable")
    if not bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_generation_actions", False)):
        raise GenerationRuntimeError("Current configuration blocks generation actions")
    permissions = dict(state.permissions or {})
    if not bool(permissions.get("system_actions") and permissions.get("generation_actions")):
        raise GenerationRuntimeError("Current Session permissions block generation actions")
    if tool_name not in set(state.allowed_tool_names or []):
        raise GenerationRuntimeError("Current Session tool inventory blocks this generation action")
    entry = ToolRegistry.get(tool_name)
    if entry is None or not entry.is_action:
        raise GenerationRuntimeError("Generation action is absent from the canonical ToolRegistry")
    return state, project, root


def submit_generation_job(job: GenerationJob, *, store: Optional[GenerationJobStore] = None) -> GenerationJob:
    store = store or get_generation_job_store()
    # Current authority and current provider inventory are checked on every
    # replay; mutable snapshots never become identity.
    _authority(job.session_id, job.project_id, "generation_submit")
    provider = next((item for item in generation_provider_statuses() if item.id == job.provider_id), None)
    if provider is None:
        raise GenerationRuntimeError(f"Unknown generation provider: {job.provider_id}")
    with store._lock:
        existing = store.find_idempotent(job.session_id, job.idempotency_key)
        if existing is not None:
            stable = (
                existing.project_id,
                existing.kind,
                existing.provider_id,
                existing.model,
                existing.prompt,
                existing.settings.model_dump(),
                existing.input_asset_ids,
            )
            incoming = (job.project_id, job.kind, job.provider_id, job.model, job.prompt, job.settings.model_dump(), job.input_asset_ids)
            if stable != incoming:
                raise GenerationRuntimeError("Generation idempotency key already belongs to another stable request")
            return existing
        store.save(job)
    if job.kind not in provider.kinds:
        job.status, job.error_code, job.error = "blocked", "unsupported_kind", "Provider does not support this generation kind"
    elif not provider.execution_ready:
        job.status, job.error_code, job.error = "blocked", "provider_unavailable", provider.detail
    return store.save(job)


def register_generated_output(job: GenerationJob, output: Path, *, project_relative_path: str) -> MediaLibraryAsset:
    """Verify and register provider output as the one canonical immutable asset."""
    _, _, root = _authority(job.session_id, job.project_id, "generation_submit")
    target = Path(output).expanduser().resolve(strict=True)
    target.relative_to(root)
    expected = (root / project_relative_path).resolve(strict=True)
    if os.path.normcase(str(target)) != os.path.normcase(str(expected)) or not target.is_file():
        raise GenerationRuntimeError("Generated output path does not match its Project-relative identity")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    asset = MediaLibraryAsset(
        id=f"asset-{job.id}-{len(job.output_asset_ids) + 1}",
        project_id=job.project_id,
        session_id=job.session_id,
        name=target.name,
        media_kind=job.kind,
        source_kind="generated",
        project_relative_path=project_relative_path,
        sha256=digest,
        size_bytes=target.stat().st_size,
        prompt=job.prompt,
        provider=job.provider_id,
        model=job.model,
        settings=job.settings.model_dump(mode="json"),
        job_id=job.id,
    )
    return get_media_library_store().register(asset)


_STORE: Optional[GenerationJobStore] = None


def get_generation_job_store() -> GenerationJobStore:
    global _STORE
    if _STORE is None:
        _STORE = GenerationJobStore()
        _STORE.recover_incomplete()
    return _STORE


class GenerationSubmitArgs(BaseModel):
    session_id: str
    project_id: str
    kind: GenerationKind
    provider_id: str
    model: str
    prompt: str = Field(min_length=1, max_length=8000)
    idempotency_key: str = Field(min_length=1, max_length=200)
    settings: GenerationSettings = Field(default_factory=GenerationSettings)
    input_asset_ids: list[str] = Field(default_factory=list, max_length=8)


@ToolRegistry.register(
    name="generation_submit",
    description="Submit an approved provider-neutral image or video generation job.",
    category="generation",
    is_action=True,
    risk_level="moderate",
    policy_flags=["ENABLE_SYSTEM_ACTIONS", "ALLOW_GENERATION_ACTIONS"],
    keyword_hints=["generate image", "generate video", "create image", "create video"],
)
@tool(args_schema=GenerationSubmitArgs, description="Submit an approved provider-neutral image or video generation job.")
def generation_submit(
    session_id: str,
    project_id: str,
    kind: GenerationKind,
    provider_id: str,
    model: str,
    prompt: str,
    idempotency_key: str,
    settings: Optional[dict[str, Any]] = None,
    input_asset_ids: Optional[list[str]] = None,
) -> str:
    try:
        job = submit_generation_job(
            GenerationJob(
                session_id=session_id,
                project_id=project_id,
                kind=kind,
                provider_id=provider_id,
                model=model,
                prompt=prompt,
                idempotency_key=idempotency_key,
                settings=GenerationSettings.model_validate(settings or {}),
                input_asset_ids=list(input_asset_ids or []),
            )
        )
        return json.dumps({"ok": job.status == "completed", "job": job.model_dump(mode="json")}, default=str)
    except Exception as exc:
        return json.dumps({"ok": False, "error_code": "generation_job_failed", "error": str(exc)})


@ToolRegistry.register(
    name="generation_list_capabilities",
    description="Report local and cloud generation provider readiness without generating or uploading media.",
    category="generation",
    risk_level="safe",
)
@tool(description="Report local and cloud generation provider readiness without generating or uploading media.")
def generation_list_capabilities() -> str:
    return json.dumps({"ok": True, "providers": [item.model_dump(mode="json") for item in generation_provider_statuses()]})
