from pathlib import Path

import pytest

from agent.native_model_runtime import NativeLlamaCppRuntime, NativeRuntimeConfig, NativeRuntimeError


def test_native_runtime_accepts_only_explicit_qwen_gguf(tmp_path: Path):
    server = tmp_path / "llama-server.exe"
    server.write_bytes(b"stub")
    qwen = tmp_path / "Qwen3.5-approved.gguf"
    qwen.write_bytes(b"gguf")
    config = NativeRuntimeConfig(server, qwen).validate()
    assert config.approved_qwen_model == qwen.resolve()


def test_native_runtime_rejects_gemma_even_when_file_exists(tmp_path: Path):
    server = tmp_path / "llama-server.exe"
    server.write_bytes(b"stub")
    gemma = tmp_path / "gemma-4.gguf"
    gemma.write_bytes(b"gguf")
    with pytest.raises(NativeRuntimeError, match="Qwen"):
        NativeRuntimeConfig(server, gemma).validate()


def test_native_cancel_closes_active_stream(tmp_path: Path):
    server = tmp_path / "llama-server.exe"
    server.write_bytes(b"stub")
    qwen = tmp_path / "Qwen3.5-approved.gguf"
    qwen.write_bytes(b"gguf")
    runtime = NativeLlamaCppRuntime(NativeRuntimeConfig(server, qwen))

    class Response:
        closed = False

        def close(self):
            self.closed = True

    response = Response()
    runtime._active_response = response
    runtime.cancel()
    assert runtime._cancel.is_set()
    assert response.closed is True
