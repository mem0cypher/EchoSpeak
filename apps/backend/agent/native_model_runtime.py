"""Crash-isolated Echo-managed llama.cpp proof runtime.

The proof launches an approved Qwen GGUF in a dedicated ``llama-server``
process bound to a random loopback port. LM Studio remains a separate provider.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from agent.model_adapters import (
    AssembledModelResponse,
    ModelFamily,
    ModelFamilyAdapter,
    detect_model_family,
)


class NativeRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class NativeRuntimeConfig:
    llama_server: Path
    approved_qwen_model: Path
    context_size: int = 32768
    gpu_layers: int = -1
    startup_timeout: float = 180.0

    def validate(self) -> "NativeRuntimeConfig":
        server = self.llama_server.expanduser().resolve(strict=True)
        model = self.approved_qwen_model.expanduser().resolve(strict=True)
        if not server.is_file():
            raise NativeRuntimeError(f"llama-server is not a file: {server}")
        if not model.is_file() or model.suffix.lower() != ".gguf":
            raise NativeRuntimeError(f"approved Qwen model must be a GGUF file: {model}")
        if detect_model_family(model.name) != ModelFamily.QWEN:
            raise NativeRuntimeError("Echo Native Runtime proof permits one explicitly approved Qwen GGUF only")
        object.__setattr__(self, "llama_server", server)
        object.__setattr__(self, "approved_qwen_model", model)
        return self


@dataclass
class NativeRuntimeTelemetry:
    status: str = "unloaded"
    pid: int = 0
    port: int = 0
    model_id: str = ""
    template: str = ""
    loaded_at: float = 0.0
    requests: int = 0
    cancelled: int = 0
    failures: int = 0
    last_latency_ms: float = 0.0
    last_error_code: str = ""
    log_path: str = ""

    def safe_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class NativeLlamaCppRuntime:
    """Own one llama-server child process and OpenAI-compatible stream."""

    def __init__(self, config: NativeRuntimeConfig) -> None:
        self.config = config.validate()
        self.telemetry = NativeRuntimeTelemetry(
            model_id=self.config.approved_qwen_model.name,
            template="qwen-model-metadata",
        )
        self._process: Optional[subprocess.Popen[Any]] = None
        self._log_handle: Any = None
        self._active_response: Any = None
        self._lock = threading.RLock()
        self._cancel = threading.Event()

    @property
    def base_url(self) -> str:
        if not self.telemetry.port:
            raise NativeRuntimeError("Native runtime is not loaded")
        return f"http://127.0.0.1:{self.telemetry.port}"

    def load(self) -> dict[str, Any]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self.health()
            port = _reserve_loopback_port()
            log_dir = Path(tempfile.mkdtemp(prefix="echospeak-native-qwen-"))
            log_path = log_dir / "llama-server.log"
            self._log_handle = log_path.open("ab", buffering=0)
            command = [
                str(self.config.llama_server),
                "--model", str(self.config.approved_qwen_model),
                "--host", "127.0.0.1",
                "--port", str(port),
                "--ctx-size", str(max(2048, int(self.config.context_size))),
                "--n-gpu-layers", str(int(self.config.gpu_layers)),
                "--jinja",
                "--chat-template-kwargs", '{"enable_thinking":false}',
                "--reasoning", "off",
                "--reasoning-budget", "0",
            ]
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.telemetry.status = "loading"
            self.telemetry.port = port
            self.telemetry.log_path = str(log_path)
            self._process = subprocess.Popen(
                command,
                cwd=str(self.config.llama_server.parent),
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                shell=False,
                creationflags=creationflags,
            )
            self.telemetry.pid = int(self._process.pid)
        deadline = time.monotonic() + max(5.0, self.config.startup_timeout)
        last_error = ""
        while time.monotonic() < deadline:
            if self._process is None or self._process.poll() is not None:
                self.telemetry.status = "crashed"
                error = NativeRuntimeError(
                    f"llama-server exited during load (code={getattr(self._process, 'returncode', None)}; log={log_path})"
                )
                self._cleanup_process(final_status="crashed")
                raise error
            try:
                health = self.health(timeout=2.0)
                if health.get("ready"):
                    self.telemetry.status = "ready"
                    self.telemetry.loaded_at = time.time()
                    return self.health(timeout=2.0)
                last_error = str(health)
            except Exception as exc:
                last_error = str(exc)
            time.sleep(0.25)
        self.unload()
        raise NativeRuntimeError(f"llama-server did not become ready: {last_error}; log={log_path}")

    def unload(self) -> None:
        self._cleanup_process(final_status="unloaded")

    def _cleanup_process(self, *, final_status: str) -> None:
        with self._lock:
            process = self._process
            self._process = None
            self._cancel.set()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None
        self.telemetry.status = final_status
        self.telemetry.pid = 0
        self.telemetry.port = 0

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            response = self._active_response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def health(self, *, timeout: float = 5.0) -> dict[str, Any]:
        process_alive = self._process is not None and self._process.poll() is None
        ready = False
        endpoint_status = "unavailable"
        if process_alive and self.telemetry.port:
            try:
                response = httpx.get(f"{self.base_url}/health", timeout=timeout)
                endpoint_status = str(response.status_code)
                payload = response.json() if response.content else {}
                ready = response.status_code == 200 and str(payload.get("status") or "ok").lower() in {"ok", "ready"}
            except Exception:
                ready = False
        return {
            "process_alive": process_alive,
            "ready": ready,
            "endpoint_status": endpoint_status,
            **self.telemetry.safe_dict(),
        }

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str,
        adapter: ModelFamilyAdapter,
        on_event: Optional[Callable[[dict[str, Any]], None]] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> AssembledModelResponse:
        if adapter.family != ModelFamily.QWEN or adapter.template != "qwen-model-metadata":
            raise NativeRuntimeError("Native proof runtime only accepts the selected Qwen adapter/template")
        if not self.health(timeout=2.0).get("ready"):
            raise NativeRuntimeError("Native runtime is not ready")
        self._cancel.clear()
        payload: dict[str, Any] = {
            "model": self.config.approved_qwen_model.name,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "stream": True,
            "temperature": 0.0,
            "max_tokens": 128,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        events: list[dict[str, Any]] = []
        started = time.perf_counter()
        self.telemetry.requests += 1
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=httpx.Timeout(300.0, connect=10.0),
            ) as response:
                with self._lock:
                    self._active_response = response
                response.raise_for_status()
                for line in response.iter_lines():
                    if self._cancel.is_set() or (cancel and cancel()):
                        self.telemetry.cancelled += 1
                        return AssembledModelResponse(finish_reason="cancelled")
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if not body or body == "[DONE]":
                        continue
                    event = json.loads(body)
                    events.append(event)
                    if on_event:
                        choice = (event.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        fragments = delta.get("tool_calls") or []
                        on_event({
                            "type": "stream_delta",
                            "content_chars": len(str(delta.get("content") or "")),
                            "argument_chars": sum(
                                len(str((item.get("function") or {}).get("arguments") or ""))
                                for item in fragments if isinstance(item, dict)
                            ),
                            "tool": ",".join(
                                str((item.get("function") or {}).get("name") or "")
                                for item in fragments if isinstance(item, dict)
                            ),
                        })
            return adapter.parse_stream(events)
        except Exception as exc:
            if self._cancel.is_set() or (cancel and cancel()):
                self.telemetry.cancelled += 1
                return AssembledModelResponse(finish_reason="cancelled")
            self.telemetry.failures += 1
            self.telemetry.last_error_code = exc.__class__.__name__
            raise
        finally:
            with self._lock:
                self._active_response = None
            self.telemetry.last_latency_ms = round((time.perf_counter() - started) * 1000.0, 2)

    def __enter__(self) -> "NativeLlamaCppRuntime":
        self.load()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.unload()


def discover_llama_server() -> Optional[Path]:
    explicit = str(os.getenv("ECHOSPEAK_LLAMA_SERVER", "") or "").strip()
    if explicit and Path(explicit).is_file():
        return Path(explicit).resolve()
    lmstudio = Path.home() / ".lmstudio" / "extensions" / "backends"
    if lmstudio.is_dir():
        candidates = list(lmstudio.glob("llama.cpp-win-x86_64-*/llama-server.exe"))
        # Prefer the dependency-light CPU AVX2 backend for automatic discovery.
        # GPU backends remain available through the explicit environment path,
        # where their matching CUDA/Vulkan runtime can be deliberately selected.
        candidates = sorted(
            candidates,
            key=lambda item: (
                not any(
                    marker in item.parent.name.lower()
                    for marker in ("cuda", "vulkan", "rocm", "sycl")
                ),
                item.parent.name,
            ),
            reverse=True,
        )
        if candidates:
            return candidates[0].resolve()
    return None


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


__all__ = [
    "NativeLlamaCppRuntime",
    "NativeRuntimeConfig",
    "NativeRuntimeError",
    "NativeRuntimeTelemetry",
    "discover_llama_server",
]
