"""Protocol adapters for external specialist agent runtimes.

These adapters translate transport-specific events only. They do not own
EchoSpeak Sessions, Projects, TaskRuns, permissions, or finalization.
"""
from __future__ import annotations

import base64
import json
import os
import queue
import shlex
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from agent.specialist_contracts import (
    SpecialistEventKind,
    SpecialistFailureLayer,
    SpecialistRun,
    SpecialistRuntimeDescriptor,
    SpecialistRuntimeKind,
    SpecialistRuntimeState,
)


EventCallback = Callable[..., None]
TerminalCallback = Callable[[bool, dict[str, Any]], None]


def _command_from_env(name: str, fallback: str) -> list[str]:
    configured = str(os.getenv(name, "") or "").strip()
    if configured:
        # The command is administrator/user configuration, not model output.
        parts = shlex.split(configured, posix=os.name != "nt")
        # Windows command shims such as npx.cmd are not resolved by
        # CreateProcess when shell=False. Resolve only the configured executable
        # token; arguments remain an argv vector and are never shell-evaluated.
        resolved = shutil.which(parts[0]) if parts else None
        if resolved:
            parts[0] = resolved
        return parts
    resolved = shutil.which(fallback)
    return [resolved] if resolved else []


def _descriptor(
    *,
    runtime_id: str,
    kind: SpecialistRuntimeKind,
    display_name: str,
    command: list[str],
    protocol: str,
    reason: str = "",
    supports_diffs: bool = True,
    supports_local_models: bool = False,
    configuration_keys: list[str],
) -> SpecialistRuntimeDescriptor:
    available = bool(command)
    return SpecialistRuntimeDescriptor(
        runtime_id=runtime_id,
        kind=kind,
        display_name=display_name,
        state=(
            SpecialistRuntimeState.AVAILABLE
            if available else SpecialistRuntimeState.UNAVAILABLE
        ),
        executable=command[0] if command else "",
        reason=reason or (
            "" if available else f"{display_name} executable was not found"
        ),
        protocol=protocol,
        supports_diffs=supports_diffs,
        supports_local_models=supports_local_models,
        configuration_keys=configuration_keys,
    )


class SpecialistAdapter:
    descriptor: SpecialistRuntimeDescriptor

    def start(
        self,
        run: SpecialistRun,
        *,
        prompt: str,
        emit: EventCallback,
        terminal: TerminalCallback,
    ) -> None:
        raise NotImplementedError

    def send_turn(self, prompt: str) -> str:
        raise NotImplementedError

    def interrupt(self) -> None:
        raise NotImplementedError

    def resolve_approval(self, request_id: str, decision: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class _JsonRpcStdioClient:
    """Small JSONL JSON-RPC client with one reader and no hidden retries."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str,
        on_notification: Callable[[dict[str, Any]], None],
        on_server_request: Callable[[dict[str, Any]], None],
        on_disconnect: Callable[[str], None],
    ) -> None:
        self.command = list(command)
        self.cwd = cwd
        self.on_notification = on_notification
        self.on_server_request = on_server_request
        self.on_disconnect = on_disconnect
        self.process: Optional[subprocess.Popen[str]] = None
        self._pending: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._next_id = 0
        self._closed = threading.Event()

    def start(self) -> None:
        self.process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def request(
        self, method: str, params: Optional[dict[str, Any]] = None, *, timeout: float = 30.0
    ) -> dict[str, Any]:
        with self._pending_lock:
            self._next_id += 1
            request_id = str(self._next_id)
            result_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = result_queue
        self._write({
            "jsonrpc": "2.0",
            "id": int(request_id),
            "method": method,
            "params": dict(params or {}),
        })
        try:
            message = result_queue.get(timeout=timeout)
        except queue.Empty as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise TimeoutError(f"{method} did not respond within {timeout:.0f}s") from exc
        if "error" in message:
            raise RuntimeError(f"{method} failed: {message['error']}")
        result = message.get("result")
        return result if isinstance(result, dict) else {"value": result}

    def notify(self, method: str, params: Optional[dict[str, Any]] = None) -> None:
        self._write({
            "jsonrpc": "2.0",
            "method": method,
            "params": dict(params or {}),
        })

    def respond(self, request_id: Any, result: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _write(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise RuntimeError("specialist runtime transport is not connected")
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_lock:
            process.stdin.write(line)
            process.stdin.flush()

    def _read_stdout(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        callback_error = ""
        try:
            for line in process.stdout:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self.on_notification({
                        "method": "transport/unparseableLine",
                        "params": {"preview": line.strip()[:1000]},
                    })
                    continue
                if not isinstance(message, dict):
                    continue
                if "id" in message and ("result" in message or "error" in message):
                    with self._pending_lock:
                        pending = self._pending.pop(str(message["id"]), None)
                    if pending is not None:
                        pending.put(message)
                    continue
                if "id" in message and "method" in message:
                    self.on_server_request(message)
                elif "method" in message:
                    self.on_notification(message)
        except Exception as exc:
            callback_error = (
                "specialist event ingestion failed: "
                f"{exc.__class__.__name__}: {exc}"
            )
        finally:
            if not self._closed.is_set():
                code = process.poll()
                self.on_disconnect(
                    callback_error or f"app-server stdout closed (exit={code})"
                )

    def _read_stderr(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            text = line.strip()
            if text:
                self.on_notification({
                    "method": "transport/stderr",
                    "params": {"message": text[:2000]},
                })

    def close(self) -> None:
        self._closed.set()
        process = self.process
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()


def _codex_event(method: str, params: dict[str, Any]) -> tuple[SpecialistEventKind, str]:
    low = method.casefold()
    item = params.get("item") if isinstance(params.get("item"), dict) else {}
    item_type = str(item.get("type") or params.get("type") or "").casefold()
    if low == "thread/started":
        return SpecialistEventKind.SESSION_STARTED, "Codex thread started"
    if low == "turn/started":
        return SpecialistEventKind.TURN_STARTED, "Codex turn started"
    if low == "turn/completed":
        return SpecialistEventKind.TURN_COMPLETED, "Codex turn completed"
    if low == "turn/aborted":
        return SpecialistEventKind.TURN_INTERRUPTED, "Codex turn interrupted"
    if "plan" in low or item_type == "plan":
        return SpecialistEventKind.PLAN_UPDATED, "Codex updated its plan"
    if "delta" in low:
        if "command" in low:
            return SpecialistEventKind.COMMAND_OUTPUT, "Command output"
        return SpecialistEventKind.MESSAGE_DELTA, "Codex response"
    if low == "item/started":
        if "command" in item_type:
            return SpecialistEventKind.ACTION_STARTED, "Command started"
        return SpecialistEventKind.ACTION_STARTED, f"{item_type or 'work item'} started"
    if low == "item/completed":
        if "file" in item_type or "change" in item_type:
            return SpecialistEventKind.FILE_CHANGED, "File change completed"
        if "command" in item_type:
            return SpecialistEventKind.ACTION_COMPLETED, "Command completed"
        if "message" in item_type:
            return SpecialistEventKind.MESSAGE_COMPLETED, "Codex message completed"
        return SpecialistEventKind.ACTION_COMPLETED, f"{item_type or 'work item'} completed"
    if low == "transport/stderr":
        return SpecialistEventKind.RUNTIME_WARNING, str(params.get("message") or "Codex warning")
    if low == "transport/unparseableline":
        return SpecialistEventKind.RUNTIME_WARNING, "Codex emitted a non-protocol stdout line"
    return SpecialistEventKind.UNKNOWN, method


class CodexAppServerAdapter(SpecialistAdapter):
    def __init__(self) -> None:
        command = _command_from_env("ECHOSPEAK_CODEX_COMMAND", "codex")
        self._command = [*command, "app-server"] if command else []
        self.descriptor = _descriptor(
            runtime_id="codex",
            kind=SpecialistRuntimeKind.CODEX_APP_SERVER,
            display_name="Codex",
            command=command,
            protocol="codex-app-server-jsonrpc-stdio",
            configuration_keys=["ECHOSPEAK_CODEX_COMMAND"],
        )
        self._client: Optional[_JsonRpcStdioClient] = None
        self._run: Optional[SpecialistRun] = None
        self._emit: Optional[EventCallback] = None
        self._terminal: Optional[TerminalCallback] = None
        self._pending_requests: dict[str, Any] = {}
        self._thread_id = ""
        self._turn_id = ""
        self._terminal_sent = False

    def start(
        self,
        run: SpecialistRun,
        *,
        prompt: str,
        emit: EventCallback,
        terminal: TerminalCallback,
    ) -> None:
        if not self._command:
            raise FileNotFoundError(self.descriptor.reason)
        self._run, self._emit, self._terminal = run, emit, terminal
        self._client = _JsonRpcStdioClient(
            self._command,
            cwd=run.project_root,
            on_notification=self._on_notification,
            on_server_request=self._on_server_request,
            on_disconnect=self._on_disconnect,
        )
        self._client.start()
        emit(
            kind=SpecialistEventKind.RUNTIME_STARTED,
            summary="Codex App Server process started",
            payload={"protocol": self.descriptor.protocol},
            raw_source="codex.process",
        )
        self._client.request("initialize", {
            "clientInfo": {"name": "EchoSpeak", "version": "8.0.0"},
            "capabilities": {"experimentalApi": False},
        })
        self._client.notify("initialized")
        emit(
            kind=SpecialistEventKind.RUNTIME_READY,
            summary="Codex App Server initialized",
            payload={},
            raw_source="codex.initialize",
        )
        thread_result: dict[str, Any]
        if run.runtime_session_id:
            thread_result = self._client.request(
                "thread/resume", {"threadId": run.runtime_session_id}
            )
        else:
            thread_result = self._client.request("thread/start", {
                "cwd": run.project_root,
                "model": run.model_id or None,
                "approvalPolicy": "on-request",
                # Echo intentionally starts Codex read-only. Workspace writes
                # return as one-shot App Server approvals instead of bypassing
                # Echo's current file-mutation permission.
                "sandbox": "read-only",
            })
        thread = thread_result.get("thread")
        self._thread_id = str(
            (thread.get("id") if isinstance(thread, dict) else "")
            or thread_result.get("threadId")
            or run.runtime_session_id
        )
        if not self._thread_id:
            raise RuntimeError("Codex App Server did not return a thread id")
        emit(
            kind=SpecialistEventKind.SESSION_STARTED,
            summary="Codex coding session is ready",
            payload={},
            runtime_session_id=self._thread_id,
            raw_source="codex.thread",
        )
        self.send_turn(prompt)

    def send_turn(self, prompt: str) -> str:
        if self._client is None or not self._thread_id:
            raise RuntimeError("Codex session is not ready")
        result = self._client.request("turn/start", {
            "threadId": self._thread_id,
            "input": [{"type": "text", "text": str(prompt)}],
        })
        turn = result.get("turn")
        self._turn_id = str(
            (turn.get("id") if isinstance(turn, dict) else "")
            or result.get("turnId")
        )
        if self._emit:
            self._emit(
                kind=SpecialistEventKind.TURN_STARTED,
                summary="Codex received the delegated objective",
                payload={},
                runtime_session_id=self._thread_id,
                runtime_turn_id=self._turn_id,
                raw_source="codex.turn",
            )
        return self._turn_id

    def _on_notification(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        kind, summary = _codex_event(method, params)
        thread_id = str(
            params.get("threadId")
            or (params.get("thread") or {}).get("id")
            if isinstance(params.get("thread"), dict) else params.get("threadId") or ""
        )
        turn_id = str(
            params.get("turnId")
            or (params.get("turn") or {}).get("id")
            if isinstance(params.get("turn"), dict) else params.get("turnId") or ""
        )
        item = params.get("item") if isinstance(params.get("item"), dict) else {}
        if self._emit:
            self._emit(
                kind=kind,
                summary=summary,
                payload=params,
                runtime_session_id=thread_id or self._thread_id,
                runtime_turn_id=turn_id or self._turn_id,
                runtime_item_id=str(item.get("id") or params.get("itemId") or ""),
                raw_source=f"codex.{method}"[:120],
            )
        if method.casefold() == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            status = str(turn.get("status") or params.get("status") or "completed").casefold()
            success = status in {"completed", "complete", "success", "succeeded"}
            self._send_terminal(success, {
                "failure_layer": (
                    None if success else SpecialistFailureLayer.SPECIALIST.value
                ),
                "failure_code": "" if success else f"codex_turn_{status or 'failed'}",
                "failure_message": str(
                    turn.get("error") or params.get("error") or ""
                )[:2000],
                "runtime_session_id": self._thread_id,
                "runtime_turn_id": turn_id or self._turn_id,
            })

    def _on_server_request(self, message: dict[str, Any]) -> None:
        request_id = str(message.get("id"))
        method = str(message.get("method") or "")
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        self._pending_requests[request_id] = message.get("id")
        if self._emit:
            self._emit(
                kind=SpecialistEventKind.APPROVAL_REQUESTED,
                summary=f"Codex requests approval: {method}",
                payload={"method": method, "request": params},
                runtime_session_id=self._thread_id,
                runtime_turn_id=self._turn_id,
                runtime_request_id=request_id,
                raw_source=f"codex.{method}"[:120],
            )

    def resolve_approval(self, request_id: str, decision: str) -> None:
        if self._client is None:
            raise RuntimeError("Codex transport is not connected")
        original_id = self._pending_requests.pop(str(request_id), None)
        if original_id is None:
            raise KeyError("Codex approval request is not pending")
        normalized = str(decision or "").casefold()
        codex_decision = {
            "approve": "accept",
            "accept": "accept",
            "deny": "decline",
            "decline": "decline",
        }.get(normalized)
        if not codex_decision:
            raise ValueError("Unsupported Codex approval decision")
        self._client.respond(original_id, {"decision": codex_decision})

    def interrupt(self) -> None:
        if self._client is None or not self._thread_id or not self._turn_id:
            raise RuntimeError("No active Codex turn")
        self._client.request("turn/interrupt", {
            "threadId": self._thread_id,
            "turnId": self._turn_id,
        })

    def _on_disconnect(self, message: str) -> None:
        if self._emit:
            self._emit(
                kind=SpecialistEventKind.RUNTIME_DISCONNECTED,
                summary=message,
                payload={},
                runtime_session_id=self._thread_id,
                runtime_turn_id=self._turn_id,
                raw_source="codex.transport",
            )
        ingestion_failure = str(message).startswith(
            "specialist event ingestion failed:"
        )
        self._send_terminal(False, {
            "failure_layer": (
                SpecialistFailureLayer.PERSISTENCE.value
                if ingestion_failure
                else SpecialistFailureLayer.TRANSPORT.value
            ),
            "failure_code": (
                "specialist_event_ingestion_failed"
                if ingestion_failure else "codex_disconnected"
            ),
            "failure_message": message,
        })

    def _send_terminal(self, success: bool, payload: dict[str, Any]) -> None:
        if self._terminal_sent:
            return
        self._terminal_sent = True
        if self._terminal:
            self._terminal(success, payload)

    def close(self) -> None:
        if self._client:
            self._client.close()


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _opencode_event(event_type: str, properties: dict[str, Any]) -> tuple[SpecialistEventKind, str]:
    low = event_type.casefold()
    if "permission" in low and ("asked" in low or "request" in low):
        return SpecialistEventKind.APPROVAL_REQUESTED, "OpenCode requests approval"
    if "session.status" in low:
        status = str((properties.get("status") or {}).get("type") or properties.get("status") or "")
        return SpecialistEventKind.PLAN_UPDATED, f"OpenCode session is {status or 'active'}"
    if "message.part.delta" in low:
        return SpecialistEventKind.MESSAGE_DELTA, "OpenCode response"
    if "message.part.updated" in low:
        part = properties.get("part") if isinstance(properties.get("part"), dict) else {}
        if str(part.get("type") or "").casefold() == "text":
            return SpecialistEventKind.MESSAGE_DELTA, "OpenCode response"
        if str(part.get("type") or "").casefold() == "tool":
            return SpecialistEventKind.ACTION_STARTED, "OpenCode tool activity"
    if "file" in low:
        return SpecialistEventKind.FILE_CHANGED, "OpenCode file activity"
    if "session.idle" in low:
        return SpecialistEventKind.TURN_COMPLETED, "OpenCode turn completed"
    if "session.error" in low:
        return SpecialistEventKind.RUNTIME_FAILED, "OpenCode turn failed"
    return SpecialistEventKind.UNKNOWN, event_type


class OpenCodeAdapter(SpecialistAdapter):
    def __init__(self) -> None:
        command = _command_from_env("ECHOSPEAK_OPENCODE_COMMAND", "opencode")
        self._command = command
        self.descriptor = _descriptor(
            runtime_id="opencode",
            kind=SpecialistRuntimeKind.OPENCODE,
            display_name="OpenCode",
            command=command,
            protocol="opencode-http-sse",
            supports_local_models=True,
            configuration_keys=[
                "ECHOSPEAK_OPENCODE_COMMAND",
                "ECHOSPEAK_LM_STUDIO_BASE_URL",
            ],
        )
        if (
            command
            and str(os.getenv("ECHOSPEAK_ALLOW_UNSANDBOXED_OPENCODE", ""))
            .strip().casefold() not in {"1", "true", "yes", "on"}
        ):
            self.descriptor = self.descriptor.model_copy(update={
                "state": SpecialistRuntimeState.MISCONFIGURED,
                "reason": (
                    "OpenCode runs on the host. Set "
                    "ECHOSPEAK_ALLOW_UNSANDBOXED_OPENCODE=true explicitly "
                    "after reviewing its permission policy."
                ),
            })
        self._process: Optional[subprocess.Popen[str]] = None
        self._client: Optional[httpx.Client] = None
        self._base_url = ""
        self._directory = ""
        self._session_id = ""
        self._emit: Optional[EventCallback] = None
        self._terminal: Optional[TerminalCallback] = None
        self._closed = threading.Event()
        self._terminal_sent = False
        self._message_roles: dict[str, str] = {}
        self._part_types: dict[str, str] = {}

    def start(
        self,
        run: SpecialistRun,
        *,
        prompt: str,
        emit: EventCallback,
        terminal: TerminalCallback,
    ) -> None:
        if not self._command:
            raise FileNotFoundError(self.descriptor.reason)
        self._emit, self._terminal, self._directory = emit, terminal, run.project_root
        port = _free_loopback_port()
        password = base64.urlsafe_b64encode(os.urandom(24)).decode("ascii").rstrip("=")
        environment = dict(os.environ)
        environment["OPENCODE_SERVER_PASSWORD"] = password
        environment["OPENCODE_SERVER_USERNAME"] = "echospeak"
        if run.local_base_url and run.model_id:
            environment["OPENCODE_CONFIG_CONTENT"] = json.dumps({
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    "lmstudio": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "LM Studio",
                        "options": {"baseURL": run.local_base_url.rstrip("/")},
                        "models": {run.model_id: {"name": run.model_id}},
                    }
                },
                "model": f"lmstudio/{run.model_id}",
            })
        command = [
            *self._command,
            "serve",
            "--hostname=127.0.0.1",
            f"--port={port}",
        ]
        self._process = subprocess.Popen(
            command,
            cwd=run.project_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
        self._base_url = f"http://127.0.0.1:{port}"
        self._client = httpx.Client(
            base_url=self._base_url,
            auth=("echospeak", password),
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
        if self._process.stdout is not None:
            threading.Thread(
                target=self._drain_process_stream,
                args=(self._process.stdout, False),
                daemon=True,
            ).start()
        if self._process.stderr is not None:
            threading.Thread(
                target=self._drain_process_stream,
                args=(self._process.stderr, True),
                daemon=True,
            ).start()
        emit(
            kind=SpecialistEventKind.RUNTIME_STARTED,
            summary="OpenCode server process started",
            payload={"address": "authenticated loopback"},
            raw_source="opencode.process",
        )
        deadline = time.monotonic() + 15.0
        last_error = ""
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"OpenCode exited during startup ({self._process.returncode})")
            try:
                response = self._client.get("/global/health")
                if response.status_code < 500:
                    break
                last_error = f"HTTP {response.status_code}"
            except Exception as exc:
                last_error = str(exc)
            time.sleep(0.15)
        else:
            raise TimeoutError(f"OpenCode health check failed: {last_error}")
        emit(
            kind=SpecialistEventKind.RUNTIME_READY,
            summary="OpenCode authenticated loopback server is ready",
            payload={},
            raw_source="opencode.health",
        )
        if run.runtime_session_id:
            self._session_id = run.runtime_session_id
        else:
            response = self._client.post(
                "/session",
                params={"directory": self._directory},
                json={"title": run.objective[:120]},
            )
            response.raise_for_status()
            body = response.json()
            self._session_id = str(body.get("id") or "")
        if not self._session_id:
            raise RuntimeError("OpenCode did not return a session id")
        emit(
            kind=SpecialistEventKind.SESSION_STARTED,
            summary="OpenCode coding session is ready",
            payload={},
            runtime_session_id=self._session_id,
            raw_source="opencode.session",
        )
        threading.Thread(target=self._event_loop, daemon=True).start()
        self.send_turn(prompt)

    def _drain_process_stream(self, stream: Any, warning: bool) -> None:
        for line in stream:
            text = str(line or "").strip()
            if text and warning and self._emit and not self._closed.is_set():
                self._emit(
                    kind=SpecialistEventKind.RUNTIME_WARNING,
                    summary="OpenCode runtime warning",
                    payload={"message": text[:2000]},
                    runtime_session_id=self._session_id,
                    raw_source="opencode.stderr",
                )

    def send_turn(self, prompt: str) -> str:
        if self._client is None or not self._session_id:
            raise RuntimeError("OpenCode session is not ready")
        response = self._client.post(
            f"/session/{self._session_id}/prompt_async",
            params={"directory": self._directory},
            json={"parts": [{"type": "text", "text": str(prompt)}]},
        )
        response.raise_for_status()
        if self._emit:
            self._emit(
                kind=SpecialistEventKind.TURN_STARTED,
                summary="OpenCode received the delegated objective",
                payload={},
                runtime_session_id=self._session_id,
                raw_source="opencode.prompt_async",
            )
        return ""

    def _event_loop(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            with client.stream(
                "GET", "/event", params={"directory": self._directory}, timeout=None
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if self._closed.is_set():
                        return
                    if not line.startswith("data:"):
                        continue
                    try:
                        envelope = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    payload = (
                        envelope.get("payload")
                        if isinstance(envelope, dict) and isinstance(envelope.get("payload"), dict)
                        else envelope
                    )
                    if not isinstance(payload, dict):
                        continue
                    event_type = str(payload.get("type") or "")
                    properties = (
                        payload.get("properties")
                        if isinstance(payload.get("properties"), dict) else {}
                    )
                    projected_properties = dict(properties)
                    low_type = event_type.casefold()
                    if low_type == "message.updated":
                        info = (
                            properties.get("info")
                            if isinstance(properties.get("info"), dict) else {}
                        )
                        message_id = str(info.get("id") or "")
                        role = str(info.get("role") or "").casefold()
                        if message_id and role:
                            self._message_roles[message_id] = role
                    part = (
                        properties.get("part")
                        if isinstance(properties.get("part"), dict) else {}
                    )
                    if low_type == "message.part.updated" and part:
                        part_id = str(part.get("id") or "")
                        part_type = str(part.get("type") or "").casefold()
                        if part_id and part_type:
                            self._part_types[part_id] = part_type
                        role = self._message_roles.get(
                            str(part.get("messageID") or ""), ""
                        )
                        if part_type == "reasoning" or role == "user":
                            projected_part = dict(part)
                            projected_part.pop("text", None)
                            projected_part.pop("content", None)
                            projected_properties["part"] = projected_part
                    if low_type == "message.part.delta":
                        part_type = self._part_types.get(
                            str(properties.get("partID") or ""), ""
                        )
                        role = self._message_roles.get(
                            str(properties.get("messageID") or ""), ""
                        )
                        if part_type != "text" or role != "assistant":
                            projected_properties.pop("delta", None)
                            projected_properties["redacted"] = True
                    session_id = str(
                        properties.get("sessionID")
                        or properties.get("sessionId")
                        or (properties.get("info") or {}).get("sessionID")
                        if isinstance(properties.get("info"), dict)
                        else properties.get("sessionID") or properties.get("sessionId") or ""
                    )
                    if session_id and session_id != self._session_id:
                        continue
                    kind, summary = _opencode_event(event_type, properties)
                    if low_type == "message.part.updated" and part:
                        role = self._message_roles.get(
                            str(part.get("messageID") or ""), ""
                        )
                        if str(part.get("type") or "").casefold() == "reasoning":
                            kind = SpecialistEventKind.UNKNOWN
                            summary = "OpenCode reasoning activity"
                        elif role == "user":
                            kind = SpecialistEventKind.UNKNOWN
                            summary = "OpenCode user message recorded"
                    if low_type == "message.part.delta":
                        part_type = self._part_types.get(
                            str(properties.get("partID") or ""), ""
                        )
                        role = self._message_roles.get(
                            str(properties.get("messageID") or ""), ""
                        )
                        if part_type != "text" or role != "assistant":
                            kind = SpecialistEventKind.UNKNOWN
                            summary = "OpenCode private runtime activity"
                    request_id = str(
                        properties.get("id")
                        or properties.get("permissionID")
                        or ""
                    )
                    if self._emit:
                        self._emit(
                            kind=kind,
                            summary=summary,
                            payload=projected_properties,
                            runtime_session_id=self._session_id,
                            runtime_request_id=request_id,
                            raw_source=f"opencode.{event_type}"[:120],
                        )
                    if kind == SpecialistEventKind.TURN_COMPLETED:
                        self._send_terminal(True, {
                            "runtime_session_id": self._session_id,
                        })
                    elif kind == SpecialistEventKind.RUNTIME_FAILED:
                        self._send_terminal(False, {
                            "failure_layer": SpecialistFailureLayer.SPECIALIST.value,
                            "failure_code": "opencode_session_error",
                            "failure_message": str(properties.get("error") or "")[:2000],
                        })
        except Exception as exc:
            if not self._closed.is_set():
                self._send_terminal(False, {
                    "failure_layer": SpecialistFailureLayer.TRANSPORT.value,
                    "failure_code": "opencode_event_stream_closed",
                    "failure_message": str(exc)[:2000],
                })

    def resolve_approval(self, request_id: str, decision: str) -> None:
        if self._client is None:
            raise RuntimeError("OpenCode transport is not connected")
        normalized = str(decision or "").casefold()
        reply = {
            "approve": "once",
            "accept": "once",
            "deny": "reject",
            "decline": "reject",
        }.get(normalized)
        if not reply:
            raise ValueError("Unsupported OpenCode approval decision")
        response = self._client.post(
            f"/permission/{request_id}/reply",
            params={"directory": self._directory},
            json={"reply": reply},
        )
        response.raise_for_status()

    def interrupt(self) -> None:
        if self._client is None or not self._session_id:
            raise RuntimeError("No active OpenCode session")
        response = self._client.post(
            f"/session/{self._session_id}/abort",
            params={"directory": self._directory},
        )
        response.raise_for_status()

    def _send_terminal(self, success: bool, payload: dict[str, Any]) -> None:
        if self._terminal_sent:
            return
        self._terminal_sent = True
        if self._terminal:
            self._terminal(success, payload)

    def close(self) -> None:
        self._closed.set()
        if self._client:
            self._client.close()
        if self._process and self._process.poll() is None:
            self._process.terminate()


def discover_specialist_runtimes() -> list[SpecialistRuntimeDescriptor]:
    codex = CodexAppServerAdapter().descriptor
    opencode = OpenCodeAdapter().descriptor
    return [codex, opencode]


def create_specialist_adapter(runtime_id: str) -> SpecialistAdapter:
    normalized = str(runtime_id or "").strip().casefold()
    if normalized == "codex":
        return CodexAppServerAdapter()
    if normalized == "opencode":
        return OpenCodeAdapter()
    raise KeyError(f"Unknown specialist runtime: {runtime_id}")


__all__ = [
    "CodexAppServerAdapter",
    "OpenCodeAdapter",
    "SpecialistAdapter",
    "create_specialist_adapter",
    "discover_specialist_runtimes",
]
