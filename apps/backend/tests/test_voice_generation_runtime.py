from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.generation_runtime import (
    GenerationJob,
    GenerationJobStore,
    GenerationProviderStatus,
    GenerationRuntimeError,
    submit_generation_job,
)
from agent.media_library import MediaLibraryStore
from agent.voice_runtime import VoiceJob, VoiceJobStore, submit_voice_job, voice_provider_statuses


class _Threads:
    def get_thread(self, session_id: str):
        return SimpleNamespace(thread_id=session_id) if session_id == "session-1" else None


class _StateStore:
    def __init__(self, project_id: str, tools: list[str], permission: str):
        self.state = SimpleNamespace(
            active_project_id=project_id,
            allowed_tool_names=tools,
            permissions={"system_actions": True, permission: True},
        )

    def get_thread_state(self, session_id: str):
        assert session_id == "session-1"
        return self.state


class _Projects:
    def __init__(self, project_id: str, root: Path):
        self.project = SimpleNamespace(id=project_id, workspace_root=str(root), archived=False)

    def get_project(self, project_id: str):
        return self.project if project_id == self.project.id else None


def _patch_authority(monkeypatch: pytest.MonkeyPatch, module, tmp_path: Path, *, project_id: str, tool: str, permission: str):
    import agent.tools as runtime_tools

    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(module, "get_thread_manager", lambda: _Threads())
    monkeypatch.setattr(module, "get_state_store", lambda: _StateStore(project_id, [tool], permission))
    monkeypatch.setattr(module, "get_project_manager", lambda: _Projects(project_id, root))
    monkeypatch.setattr(module.config, "enable_system_actions", True)
    monkeypatch.setattr(module.config, f"allow_{permission}", True)
    monkeypatch.setattr(runtime_tools, "get_tool_execution_context", lambda: {
        "execution_id": "execution-1",
        "task_run_id": "task-run-1",
        "tool_run_id": "tool-run-1",
        "thread_id": "session-1",
        "project_root": str(root),
        "allowed_tool_names": [tool],
    })
    return root


@pytest.mark.skipif(sys.platform != "win32", reason="real local path uses Windows SAPI")
def test_windows_sapi_produces_verified_project_audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import agent.voice_runtime as voice

    root = _patch_authority(
        monkeypatch,
        voice,
        tmp_path,
        project_id="project-1",
        tool="voice_synthesize_speech",
        permission="voice_actions",
    )
    media = MediaLibraryStore(tmp_path / "media-library")
    monkeypatch.setattr(voice, "get_media_library_store", lambda: media)
    job = submit_voice_job(
        VoiceJob(
            idempotency_key="sapi-fixture-1",
            session_id="session-1",
            project_id="project-1",
            operation="text_to_speech",
            provider_id="windows-sapi",
            text="EchoSpeak local voice validation.",
        ),
        store=VoiceJobStore(tmp_path / "voice"),
    )

    assert job.status == "completed", job.error
    asset = media.get(job.output_asset_id)
    assert asset is not None and asset.media_kind == "audio"
    output = root / asset.project_relative_path
    assert output.is_file() and output.stat().st_size > 44
    # Stable idempotency never reuses the old permission snapshot.
    monkeypatch.setattr(voice.config, "allow_voice_actions", False)
    with pytest.raises(Exception, match="configuration blocks"):
        submit_voice_job(job.model_copy(deep=True), store=VoiceJobStore(tmp_path / "voice"))


def test_voice_capabilities_do_not_open_microphone_or_claim_missing_stt():
    providers = {item.id: item for item in voice_provider_statuses()}
    assert providers["windows-sapi"].operations == ["text_to_speech"]
    assert providers["faster-whisper-local"].execution_ready is False
    assert providers["openai-audio"].execution_ready is False


def test_generation_job_blocks_honestly_and_keeps_stable_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import agent.generation_runtime as generation

    _patch_authority(
        monkeypatch,
        generation,
        tmp_path,
        project_id="project-1",
        tool="generation_submit",
        permission="generation_actions",
    )
    monkeypatch.setattr(
        generation,
        "generation_provider_statuses",
        lambda: [
            GenerationProviderStatus(
                id="comfyui-local",
                locality="local",
                kinds=["image", "video"],
                detected=False,
                configured=False,
                execution_ready=False,
                detail="Disposable test provider is unavailable.",
            )
        ],
    )
    store = GenerationJobStore(tmp_path / "generation")
    request = GenerationJob(
        idempotency_key="image-fixture-1",
        session_id="session-1",
        project_id="project-1",
        kind="image",
        provider_id="comfyui-local",
        model="fixture-model",
        prompt="A monochrome EchoSpeak test card",
    )
    blocked = submit_generation_job(request, store=store)
    replay = submit_generation_job(request.model_copy(deep=True), store=store)

    assert blocked.status == "blocked"
    assert blocked.error_code == "provider_unavailable"
    assert replay.id == blocked.id
    monkeypatch.setattr(generation.config, "allow_generation_actions", False)
    with pytest.raises(GenerationRuntimeError, match="configuration blocks"):
        submit_generation_job(request.model_copy(deep=True), store=store)
    monkeypatch.setattr(generation.config, "allow_generation_actions", True)
    with pytest.raises(GenerationRuntimeError, match="stable request"):
        submit_generation_job(request.model_copy(update={"prompt": "different"}), store=store)


def test_voice_and_generation_malformed_jobs_have_manual_recovery(tmp_path: Path):
    for store in (VoiceJobStore(tmp_path / "voice"), GenerationJobStore(tmp_path / "generation")):
        record = store.jobs_root / "bad.json"
        record.write_text("{not-json", encoding="utf-8")
        with pytest.raises(Exception, match="quarantined"):
            store.get("bad")
        assert list(store.corrupt_root.glob("*.json"))
        diagnostic = next(store.corrupt_root.glob("*.diagnostic.txt"))
        assert "Recovery:" in diagnostic.read_text(encoding="utf-8")
