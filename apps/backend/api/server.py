"""
API module for Echo Speak.
Provides FastAPI server for REST API access.
"""

import os
import sys
import base64
import json
import queue
import asyncio
import importlib.util
import re
import threading
import time
import uuid
import hmac
import hashlib
from datetime import datetime
from pathlib import Path
from io import BytesIO
from collections import deque, OrderedDict
from typing import Optional, List, Dict, Any, Literal
from contextlib import asynccontextmanager
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urlparse

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
# Avoid Tk/matplotlib GUI backends on worker threads (Tcl_AsyncDelete process death).
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from fastapi import FastAPI, HTTPException, Query, Response, Request, UploadFile, File, WebSocket, WebSocketDisconnect, Header, Depends, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from loguru import logger

import anyio
from collections import defaultdict


try:
    from croniter import croniter
except Exception:
    croniter = None

try:
    from langchain_core.callbacks.base import BaseCallbackHandler
except ImportError:
    from langchain.callbacks.base import BaseCallbackHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    config,
    DATA_DIR,
    ModelProvider,
    SECRET_NESTED_SETTINGS,
    SECRET_TOP_LEVEL_SETTINGS,
    _strip_mcp_secret_overrides,
    read_runtime_override_payload,
    write_runtime_override_payload,
)
from agent.research import build_research_run
from agent.state import get_state_store
from agent.stream_events import semantic_activity_from_stream_payload

# Base directory for relative path resolution
BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ENV_LM_STUDIO_ONLY = str(os.getenv("LM_STUDIO_ONLY", "")).strip().lower() in ("1", "true", "yes", "on")
LM_STUDIO_DEFAULT_URL = "http://localhost:1234"


def _is_lmstudio_only_enabled() -> bool:
    """Return whether 'LM Studio Only' mode is enabled.

    Priority:
    1) Runtime overrides from settings.json (GUI-controlled)
    2) Environment variable LM_STUDIO_ONLY (fallback default)
    """
    try:
        overrides = _read_runtime_settings()
        if isinstance(overrides, dict) and "lm_studio_only" in overrides:
            return bool(overrides.get("lm_studio_only"))
    except Exception:
        pass
    return bool(_ENV_LM_STUDIO_ONLY)


def _assert_provider_available(provider: "ModelProvider") -> None:
    if provider == ModelProvider.GEMINI:
        if importlib.util.find_spec("langchain_google_genai") is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Gemini provider requires 'langchain-google-genai' on the backend. "
                    "Install it in apps/backend venv: pip install langchain-google-genai"
                ),
            )


def _default_cloud_provider() -> "ModelProvider":
    """Choose a sensible default cloud provider when none is explicitly selected."""
    configured = str(getattr(config, "default_cloud_provider", "") or "").strip().lower()
    try:
        openai_key = str(getattr(getattr(config, "openai", None), "api_key", "") or "").strip()
        gemini_key = str(getattr(getattr(config, "gemini", None), "api_key", "") or "").strip()
        if configured == ModelProvider.OPENAI.value:
            if openai_key or not gemini_key:
                return ModelProvider.OPENAI
        elif configured == ModelProvider.GEMINI.value:
            if gemini_key or not openai_key:
                return ModelProvider.GEMINI
        if gemini_key and not openai_key:
            return ModelProvider.GEMINI
    except Exception:
        pass
    return ModelProvider.OPENAI


def _default_model_for_provider(provider: "ModelProvider") -> str:
    if provider == ModelProvider.OPENAI:
        return str(config.openai.model or "").strip()
    if provider == ModelProvider.GEMINI:
        return str(config.gemini.model or "").strip()
    return str(config.local.model_name or "").strip()


def _ensure_session_model_binding(session_id: str):
    key = _normalize_thread_id(session_id)
    if _is_lmstudio_only_enabled():
        provider = ModelProvider.LM_STUDIO
    elif _runtime_provider is not None:
        provider = _runtime_provider
    else:
        provider = config.local.provider if config.use_local_models else _default_cloud_provider()
    store = get_state_store()
    binding = store.ensure_session_model_binding(
        key,
        provider_id=provider.value,
        model_id=_default_model_for_provider(provider) or "default",
        provider_configuration_id="global-default",
    )
    if (
        _is_lmstudio_only_enabled()
        and binding.provider_id != ModelProvider.LM_STUDIO.value
    ):
        previous_revision = binding.binding_revision
        binding = store.update_session_model_binding(
            key,
            provider_id=ModelProvider.LM_STUDIO.value,
            model_id=_default_model_for_provider(ModelProvider.LM_STUDIO) or "default",
            expected_revision=binding.binding_revision,
            provider_configuration_id="global-default",
        )
        cancel_incompatible = globals().get("_cancel_incompatible_session_work")
        if callable(cancel_incompatible):
            cancel_incompatible(
                key,
                reason=(
                    "LM Studio-only configuration changed Session provider binding from revision "
                    f"{previous_revision} to {binding.binding_revision}"
                ),
            )
    return binding


def _resolve_runtime_provider(session_id: Optional[str] = None) -> "ModelProvider":
    """Resolve the provider the next query would use without creating an agent."""
    if session_id:
        return ModelProvider(_ensure_session_model_binding(session_id).provider_id)
    if _is_lmstudio_only_enabled():
        return ModelProvider.LM_STUDIO
    if _runtime_provider is not None:
        return _runtime_provider
    return config.local.provider if config.use_local_models else _default_cloud_provider()


def _provider_default_base_url(provider: "ModelProvider") -> str:
    from agent.model_runtime import resolve_local_provider_base_url

    return resolve_local_provider_base_url(provider)


def _provider_configured_base_url(provider: "ModelProvider") -> str:
    from agent.model_runtime import resolve_local_provider_base_url

    configured = str(getattr(getattr(config, "local", None), "base_url", "") or "")
    return resolve_local_provider_base_url(provider, configured)


def _local_provider_models_url(provider: "ModelProvider", base_url: str) -> str:
    base = (base_url or _provider_default_base_url(provider)).rstrip("/")
    if provider == ModelProvider.OLLAMA:
        return f"{base}/api/tags"
    if base.endswith("/v1"):
        return f"{base}/models"
    return f"{base}/v1/models"


def _provider_recovery_message(provider: "ModelProvider", detail: str = "") -> str:
    name = provider.value
    if provider == ModelProvider.LM_STUDIO:
        return (
            "LM Studio is selected, but I cannot reach its local server. "
            f"Start LM Studio, load a model, enable the local server at {LM_STUDIO_DEFAULT_URL}, "
            "then try again. You can also switch EchoSpeak to another provider."
        )
    if provider == ModelProvider.OLLAMA:
        return (
            "Ollama is selected, but I cannot reach the Ollama server. "
            "Start Ollama, make sure a model is installed, then try again."
        )
    if provider in (ModelProvider.LOCALAI, ModelProvider.VLLM):
        return (
            f"{name} is selected, but I cannot reach its local model server. "
            "Start the server or switch EchoSpeak to another provider, then try again."
        )
    if provider == ModelProvider.OPENAI:
        return "OpenAI is selected, but no OpenAI API key is configured. Add an API key or switch provider."
    if provider == ModelProvider.GEMINI:
        return "Gemini is selected, but Gemini is not ready. Add a Gemini API key/dependency or switch provider."
    if provider == ModelProvider.LLAMA_CPP:
        return "llama.cpp is selected, but its local model path is not ready. Check the model path or switch provider."
    return f"Model provider {name} is not ready. {detail}".strip()


def _check_provider_readiness(
    provider: Optional["ModelProvider"] = None,
    timeout: float = 1.5,
    *,
    model_id: str = "",
) -> dict[str, Any]:
    """Fast query preflight so provider outages become clear user-facing failures."""
    p = provider or _resolve_runtime_provider()
    try:
        _assert_provider_available(p)
    except HTTPException as exc:
        return {
            "ok": False,
            "provider": p.value,
            "message": _provider_recovery_message(p, str(exc.detail)),
            "detail": str(exc.detail),
        }

    if p == ModelProvider.OPENAI:
        key = str(getattr(getattr(config, "openai", None), "api_key", "") or "").strip()
        return {
            "ok": bool(key),
            "provider": p.value,
            "message": "" if key else _provider_recovery_message(p),
            "detail": "" if key else "Missing OPENAI_API_KEY",
        }

    if p == ModelProvider.GEMINI:
        key = str(getattr(getattr(config, "gemini", None), "api_key", "") or "").strip()
        return {
            "ok": bool(key),
            "provider": p.value,
            "message": "" if key else _provider_recovery_message(p),
            "detail": "" if key else "Missing GEMINI_API_KEY",
        }

    if p == ModelProvider.LLAMA_CPP:
        model_path = str(model_id or getattr(getattr(config, "local", None), "model_name", "") or "").strip()
        ok = bool(model_path and Path(model_path).exists())
        return {
            "ok": ok,
            "provider": p.value,
            "message": "" if ok else _provider_recovery_message(p),
            "detail": "" if ok else f"Model path not found: {model_path or '(empty)'}",
        }

    if p in (ModelProvider.OLLAMA, ModelProvider.LM_STUDIO, ModelProvider.LOCALAI, ModelProvider.VLLM):
        base_url = _provider_configured_base_url(p)
        url = _local_provider_models_url(p, base_url)
        try:
            req = UrlRequest(url, headers={"Accept": "application/json"})
            with urlopen(req, timeout=timeout) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                if 200 <= status < 300:
                    if not hasattr(resp, "read"):
                        return {"ok": True, "provider": p.value, "message": "", "detail": ""}
                    payload_bytes = resp.read(512_001)
                    if len(payload_bytes) > 512_000:
                        raise ValueError("Provider model inventory exceeded 512 KB")
                    payload = json.loads(payload_bytes.decode("utf-8")) if payload_bytes else {}
                    rows = payload.get("models") if p == ModelProvider.OLLAMA else payload.get("data")
                    if not isinstance(rows, list):
                        rows = []
                    available_ids = {
                        str((item or {}).get("id") or (item or {}).get("name") or (item or {}).get("model") or "").strip().lower()
                        for item in rows
                        if isinstance(item, dict)
                    }
                    available_ids.discard("")
                    configured_model = str(model_id or getattr(getattr(config, "local", None), "model_name", "") or "").strip().lower()
                    model_loaded = bool(
                        configured_model
                        and any(
                            candidate == configured_model
                            or candidate.endswith("/" + configured_model)
                            or configured_model.endswith("/" + candidate)
                            for candidate in available_ids
                        )
                    )
                    if configured_model and not model_loaded:
                        detail = (
                            f"Configured model is not loaded: {configured_model}. "
                            f"Provider reported {len(available_ids)} model(s)."
                        )
                        return {
                            "ok": False,
                            "provider": p.value,
                            "message": _provider_recovery_message(p, detail),
                            "detail": detail,
                        }
                    return {"ok": True, "provider": p.value, "message": "", "detail": ""}
                detail = f"HTTP {status} from {url}"
        except HTTPError as exc:
            detail = f"HTTP {getattr(exc, 'code', '')} from {url}".strip()
        except URLError as exc:
            detail = f"Cannot connect to {url}: {getattr(exc, 'reason', exc)}"
        except TimeoutError:
            detail = f"Timed out connecting to {url}"
        except Exception as exc:
            detail = f"Cannot connect to {url}: {exc}"
        return {
            "ok": False,
            "provider": p.value,
            "message": _provider_recovery_message(p, detail),
            "detail": detail,
        }

    return {"ok": True, "provider": p.value, "message": "", "detail": ""}


def _should_preflight_provider(message: str) -> bool:
    """Skip preflight for lightweight confirmation/control replies."""
    low = str(message or "").strip().lower()
    if low in {"confirm", "cancel", "yes", "no", "approve", "reject"}:
        return False
    return True


def _provider_unavailable_payload(request_id: str, readiness: dict[str, Any]) -> dict[str, Any]:
    message = str(readiness.get("message") or "The selected model provider is not ready.")
    detail = str(readiness.get("detail") or "").strip()
    if detail and detail not in message:
        message = f"{message}\n\nDetails: {detail}"
    return {
        "type": "final",
        "response": message,
        "success": False,
        "memory_count": 0,
        "doc_sources": [],
        "research": [],
        "spoken_text": message,
        "execution_id": None,
        "trace_id": None,
        "thread_state": None,
        "request_id": request_id,
        "at": time.time(),
        "error_code": "provider_unavailable",
        "provider": readiness.get("provider"),
    }


_agent = None
_agent_pool: "OrderedDict[str, Any]" = OrderedDict()
_agent_pool_lock = threading.Lock()
_agent_pool_max = 8
_vision_manager = None
_runtime_provider: Optional[ModelProvider] = None
_discord_bot_task: Optional[asyncio.Task] = None
_discord_bot_token_value: str = ""

_metrics_lock = threading.Lock()
_metrics = {
    "requests": 0,
    "errors": 0,
    "tool_calls": 0,
    "tool_errors": 0,
}
_tool_latency_ms: deque[float] = deque(maxlen=200)


def _read_runtime_settings() -> dict:
    try:
        data = read_runtime_override_payload(include_secrets=True, migrate_legacy=True)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _copy_jsonish(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _copy_jsonish(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy_jsonish(v) for v in value]
    return value


def _redact_settings_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    out = _copy_jsonish(payload)
    for key in SECRET_TOP_LEVEL_SETTINGS:
        if key in out:
            out[key] = "" if str(out.get(key) or "").strip() == "" else "***"
    for section, secret_keys in SECRET_NESTED_SETTINGS.items():
        patch = out.get(section)
        if not isinstance(patch, dict):
            continue
        for secret_key in secret_keys:
            if secret_key in patch:
                patch[secret_key] = "" if str(patch.get(secret_key) or "").strip() == "" else "***"
    if isinstance(out.get("mcp_servers"), dict):
        out["mcp_servers"] = _strip_mcp_secret_overrides(out["mcp_servers"])
    return out


def _deep_merge(dst: dict, src: dict) -> dict:
    out = dict(dst)
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out.get(k) or {}, v)
        else:
            out[k] = v
    return out


def _validate_settings_effective(effective: dict) -> list[dict]:
    """Return a list of validation issues.

    Each issue is {"key": "path.like.this", "message": "...", "severity": "error"|"warning"}.
    """
    issues: list[dict] = []
    s = effective or {}

    use_local = bool(s.get("use_local_models"))
    openai_api_key = ((s.get("openai") or {}).get("api_key") or "").strip()
    gemini_api_key = ((s.get("gemini") or {}).get("api_key") or "").strip()
    local_provider = ((s.get("local") or {}).get("provider") or "").strip()
    local_base_url = ((s.get("local") or {}).get("base_url") or "").strip()
    local_model = ((s.get("local") or {}).get("model_name") or "").strip()

    if use_local:
        if not local_provider:
            issues.append({"key": "local.provider", "message": "Local provider is required when Use Local Models is enabled.", "severity": "error"})
        if not local_base_url:
            issues.append({"key": "local.base_url", "message": "Local base URL is required when Use Local Models is enabled.", "severity": "error"})
        if not local_model:
            issues.append({"key": "local.model_name", "message": "Local model name is required when Use Local Models is enabled.", "severity": "error"})
    else:
        # Cloud provider: need either OpenAI or Gemini API key
        if not openai_api_key and not gemini_api_key:
            issues.append({"key": "cloud.api_key", "message": "An API key is required for cloud providers. Add either an OpenAI or Gemini API key.", "severity": "error"})

    embedding_provider = ((s.get("embedding") or {}).get("provider") or "").strip()
    if embedding_provider == "openai" and not openai_api_key:
        issues.append({"key": "embedding.provider", "message": "Embedding provider=openai has no OpenAI API key configured. EchoSpeak will fall back to local embeddings when available.", "severity": "warning"})

    enable_system_actions = bool(s.get("enable_system_actions"))
    allow_flags = [
        "allow_open_chrome",
        "allow_playwright",
        "allow_desktop_automation",
        "allow_file_write",
        "allow_terminal_commands",
        "allow_open_application",
        "allow_self_modification",
        "allow_discord_webhook",
    ]
    if not enable_system_actions:
        for k in allow_flags:
            if bool(s.get(k)):
                issues.append({"key": k, "message": "Enable System Actions must be ON to enable this permission.", "severity": "error"})

    if bool(s.get("allow_terminal_commands")):
        denylist = s.get("terminal_command_denylist")
        if not isinstance(denylist, list):
            issues.append({"key": "terminal_command_denylist", "message": "Terminal commands are enabled but TERMINAL_COMMAND_DENYLIST is missing.", "severity": "warning"})
        root = str(s.get("file_tool_root") or "").strip()
        if not root:
            issues.append({"key": "file_tool_root", "message": "Set FILE_TOOL_ROOT to restrict terminal/file operations.", "severity": "warning"})

    if bool(s.get("allow_file_write")):
        root = str(s.get("file_tool_root") or "").strip()
        if not root:
            issues.append({"key": "file_tool_root", "message": "Set FILE_TOOL_ROOT to restrict file writes.", "severity": "warning"})

    api_host = str(((s.get("api") or {}).get("host") or "")).strip().lower()
    api_auth_enabled = bool(s.get("api_auth_enabled"))
    api_auth_key = str(s.get("api_auth_key") or "").strip()
    if api_auth_enabled and not api_auth_key:
        issues.append({"key": "api_auth_key", "message": "API auth is enabled but API_AUTH_KEY is empty.", "severity": "error"})
    if api_host in {"0.0.0.0", "::", "[::]"} and (not api_auth_enabled or not api_auth_key):
        issues.append({"key": "api_auth_enabled", "message": "API_HOST is network-facing. API authentication and a non-empty key are required.", "severity": "error"})

    if bool(s.get("webhook_enabled")):
        secret = str(s.get("webhook_secret") or "").strip()
        secret_path = str(s.get("webhook_secret_path") or "").strip()
        if not secret and not secret_path:
            issues.append({"key": "webhook_secret", "message": "Webhooks enabled but WEBHOOK_SECRET / WEBHOOK_SECRET_PATH is not set.", "severity": "error"})

    if bool(s.get("allow_discord_webhook")):
        url = str(s.get("discord_webhook_url") or "").strip()
        if not url:
            issues.append({"key": "discord_webhook_url", "message": "Allow Discord Webhook is enabled but DISCORD_WEBHOOK_URL is empty.", "severity": "error"})

    if bool(s.get("cron_enabled")):
        try:
            from croniter import croniter as _ci  # type: ignore
        except Exception:
            _ci = None
        if _ci is None:
            issues.append({"key": "cron_enabled", "message": "Cron enabled but croniter is not installed on the backend.", "severity": "warning"})

    if bool(s.get("allow_open_application")):
        from config import _normalize_open_application_allowlist

        allowlist = _normalize_open_application_allowlist(s.get("open_application_allowlist"))
        if not allowlist:
            issues.append({"key": "open_application_allowlist", "message": "Application launching is enabled but OPEN_APPLICATION_ALLOWLIST is empty.", "severity": "error"})

    if bool(s.get("allow_self_modification")):
        issues.append({"key": "allow_self_modification", "message": "Self-modification is enabled. This is high-risk and should stay off outside controlled development sessions.", "severity": "warning"})

    if bool(s.get("allow_discord_bot")):
        token = str(s.get("discord_bot_token") or "").strip()
        owner_id = str(s.get("discord_bot_owner_id") or "").strip()
        allowed_users = s.get("discord_bot_allowed_users")
        allowed_roles = s.get("discord_bot_allowed_roles")
        if not token:
            issues.append({"key": "discord_bot_token", "message": "Discord bot is enabled but DISCORD_BOT_TOKEN is empty.", "severity": "error"})
        if not owner_id:
            issues.append({"key": "discord_bot_owner_id", "message": "Set DISCORD_BOT_OWNER_ID to enable owner-level Discord bot protections.", "severity": "warning"})
        has_allowed_users = isinstance(allowed_users, list) and any(str(x).strip() for x in allowed_users)
        has_allowed_roles = isinstance(allowed_roles, list) and any(str(x).strip() for x in allowed_roles)
        if not has_allowed_users and not has_allowed_roles:
            issues.append({"key": "discord_bot_allowed_roles", "message": "Discord bot server access is open. Set DISCORD_BOT_ALLOWED_ROLES for role-based server gating, or DISCORD_BOT_ALLOWED_USERS for explicit user allowlisting.", "severity": "warning"})

    if bool(s.get("allow_telegram_bot")):
        token = str(s.get("telegram_bot_token") or "").strip()
        allowed_users = s.get("telegram_allowed_users")
        if not token:
            issues.append({"key": "telegram_bot_token", "message": "Telegram bot is enabled but TELEGRAM_BOT_TOKEN is empty.", "severity": "error"})
        if not isinstance(allowed_users, list) or not any(str(x).strip() for x in allowed_users):
            issues.append({"key": "telegram_allowed_users", "message": "Telegram bot allowed users list is empty. Consider restricting access explicitly.", "severity": "warning"})

    if bool(s.get("allow_twitch")):
        if not str(s.get("twitch_client_id") or "").strip() or not str(s.get("twitch_client_secret") or "").strip():
            issues.append({"key": "twitch_client_secret", "message": "Twitch is enabled but TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET is incomplete.", "severity": "error"})
        if bool(s.get("twitch_chat_reply_enabled")) and not str(s.get("twitch_bot_access_token") or "").strip():
            issues.append({"key": "twitch_bot_access_token", "message": "Twitch chat replies are enabled but TWITCH_BOT_ACCESS_TOKEN is empty.", "severity": "error"})
        if str(s.get("twitch_eventsub_callback_url") or "").strip() and not str(s.get("twitch_eventsub_secret") or "").strip():
            issues.append({"key": "twitch_eventsub_secret", "message": "Twitch EventSub callback is configured but TWITCH_EVENTSUB_SECRET is empty.", "severity": "warning"})

    if bool(s.get("allow_twitter")):
        bearer = str(s.get("twitter_bearer_token") or "").strip()
        access = str(s.get("twitter_access_token") or "").strip()
        access_secret = str(s.get("twitter_access_token_secret") or "").strip()
        if not bearer and not access:
            issues.append({"key": "twitter_bearer_token", "message": "Twitter/X is enabled but no bearer or access token is configured.", "severity": "error"})
        if access and not access_secret:
            issues.append({"key": "twitter_access_token_secret", "message": "Twitter/X access token is set but TWITTER_ACCESS_TOKEN_SECRET is empty.", "severity": "error"})

    if bool(s.get("allow_calendar")) and not str(s.get("google_calendar_credentials_path") or "").strip():
        issues.append({"key": "google_calendar_credentials_path", "message": "Calendar integration is enabled but GOOGLE_CALENDAR_CREDENTIALS_PATH is empty.", "severity": "error"})

    if bool(s.get("allow_spotify")):
        if not str(s.get("spotify_client_id") or "").strip() or not str(s.get("spotify_client_secret") or "").strip():
            issues.append({"key": "spotify_client_secret", "message": "Spotify integration is enabled but SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET is incomplete.", "severity": "error"})

    if bool(s.get("allow_notion")) and not str(s.get("notion_token") or "").strip():
        issues.append({"key": "notion_token", "message": "Notion integration is enabled but NOTION_TOKEN is empty.", "severity": "error"})

    if bool(s.get("allow_github")) and not str(s.get("github_token") or "").strip():
        issues.append({"key": "github_token", "message": "GitHub integration is enabled but GITHUB_TOKEN is empty.", "severity": "error"})

    if bool(s.get("allow_home_assistant")):
        if not str(s.get("home_assistant_url") or "").strip() or not str(s.get("home_assistant_token") or "").strip():
            issues.append({"key": "home_assistant_token", "message": "Home Assistant integration is enabled but HOME_ASSISTANT_URL / HOME_ASSISTANT_TOKEN is incomplete.", "severity": "error"})

    if bool(s.get("allow_whatsapp")) and not str(s.get("whatsapp_api_url") or "").strip():
        issues.append({"key": "whatsapp_api_url", "message": "WhatsApp integration is enabled but WHATSAPP_API_URL is empty.", "severity": "error"})

    if bool(s.get("a2a_enabled")) and not str(s.get("a2a_auth_key") or "").strip():
        issues.append({"key": "a2a_auth_key", "message": "A2A is enabled without A2A_AUTH_KEY. Add an auth key before using this outside a trusted local environment.", "severity": "warning"})

    return issues


def _sanitize_incoming_settings(patch: dict) -> dict:
    if not isinstance(patch, dict):
        return {}

    nested_sections = {
        "openai": set(getattr(config.openai, "model_dump")().keys()),
        "gemini": set(getattr(config.gemini, "model_dump")().keys()),
        "local": set(getattr(config.local, "model_dump")().keys()),
        "patches": set(getattr(config.patches, "model_dump")().keys()),
        "embedding": set(getattr(config.embedding, "model_dump")().keys()),
        "voice": set(getattr(config.voice, "model_dump")().keys()),
        "personaplex": set(getattr(config.personaplex, "model_dump")().keys()),
        "api": set(getattr(config.api, "model_dump")().keys()),
        "soul": set(getattr(config.soul, "model_dump")().keys()),
    }
    known_top_level = set(config.to_public_dict().keys()) | {"lm_studio_only"}
    out: dict[str, Any] = {}
    for key, value in patch.items():
        if key in nested_sections:
            if not isinstance(value, dict):
                continue
            allowed_fields = nested_sections[key]
            section_patch = {k: v for k, v in value.items() if k in allowed_fields}
            if section_patch:
                out[key] = section_patch
            continue
        if key in known_top_level:
            out[key] = value

    # If the UI sends redacted placeholders, ignore them.
    openai_patch = out.get("openai")
    if isinstance(openai_patch, dict):
        val = openai_patch.get("api_key")
        if isinstance(val, str) and val.strip() == "***":
            openai_patch = dict(openai_patch)
            openai_patch.pop("api_key", None)
            out["openai"] = openai_patch

    gemini_patch = out.get("gemini")
    if isinstance(gemini_patch, dict):
        val = gemini_patch.get("api_key")
        if isinstance(val, str) and val.strip() == "***":
            gemini_patch = dict(gemini_patch)
            gemini_patch.pop("api_key", None)
            out["gemini"] = gemini_patch

    for secret_key in SECRET_TOP_LEVEL_SETTINGS:
        val = out.get(secret_key)
        if isinstance(val, str) and val.strip() == "***":
            out.pop(secret_key, None)

    if "open_application_allowlist" in out:
        from config import _normalize_open_application_allowlist

        out["open_application_allowlist"] = _normalize_open_application_allowlist(
            out.get("open_application_allowlist")
        )

    return out


def _force_lmstudio_config() -> None:
    config.use_local_models = True
    config.local.provider = ModelProvider.LM_STUDIO
    if not (config.local.base_url or "").strip():
        config.local.base_url = LM_STUDIO_DEFAULT_URL


def _normalize_thread_id(thread_id: Optional[str]) -> str:
    if thread_id is None:
        return "default"
    val = str(thread_id).strip()
    return val or "default"


def get_agent(thread_id: Optional[str] = None):
    """Get or create the agent instance.

    Canonical Turns always use a Session-keyed agent actor. The pre-8.0 shared
    mutable agent switch is ignored because it cannot safely run unrelated
    Sessions concurrently under separate Session locks.
    """
    global _agent
    global _runtime_provider
    from agent.core import EchoSpeakAgent

    key = _normalize_thread_id(thread_id)
    with _agent_pool_lock:
        binding = _ensure_session_model_binding(key)
        existing = _agent_pool.pop(key, None)
        if existing is not None:
            existing_provider = str(getattr(getattr(existing, "llm_provider", None), "value", "") or "")
            existing_model = str(getattr(getattr(existing, "model_runtime", None), "model_id", "") or "")
            if existing_provider == binding.provider_id and existing_model == binding.model_id:
                _agent_pool[key] = existing
                return existing
            logger.info("Recreating Session agent because its model binding changed: {}", key)
        provider = ModelProvider(binding.provider_id)

        agent = EchoSpeakAgent(
            llm_provider=provider,
            manage_background_services=(key == "default"),
            model_id=binding.model_id,
        )
        _agent_pool[key] = agent
        while len(_agent_pool) > _agent_pool_max:
            _agent_pool.popitem(last=False)
        return agent


def get_existing_agent(thread_id: Optional[str] = None):
    """Get an already-initialized agent without creating a new one."""
    key = _normalize_thread_id(thread_id)
    with _agent_pool_lock:
        existing = _agent_pool.get(key)
        if existing is not None:
            _agent_pool.move_to_end(key)
        return existing


def _discord_process_query(
    user_input: str,
    include_memory: bool = True,
    callbacks: list | None = None,
    thread_id: str | None = None,
    source: str | None = None,
    discord_user_info: dict | None = None,
):
    agent = get_agent(thread_id)
    return agent.process_query(
        user_input=user_input,
        include_memory=include_memory,
        callbacks=callbacks,
        thread_id=thread_id,
        source=source or "discord_bot",
        discord_user_info=discord_user_info,
    )


async def _discord_startup_health_check() -> None:
    try:
        await asyncio.sleep(6)
        from discord_bot import get_bot

        bot = get_bot()
        if bot is None:
            logger.error("Discord bot health-check: bot instance is None (startup failed or never created)")
            return
        running = False
        try:
            running = bool(bot.is_running())
        except Exception:
            running = False
        logger.info(
            f"Discord bot health-check: running={running} has_loop={bool(getattr(bot, '_loop', None))}"
        )
        if not running:
            logger.error(
                "Discord bot does not appear to be connected. "
                "If the bot shows as online in Discord, check privileged Gateway Intents (Message Content) in the Developer Portal. "
                "Otherwise verify DISCORD_BOT_TOKEN/ALLOW_DISCORD_BOT and look for 'Discord bot background task crashed' logs."
            )
    except Exception as exc:
        logger.warning(f"Discord bot health-check failed: {exc}")


async def _reconcile_discord_bot_runtime() -> None:
    global _discord_bot_task, _discord_bot_token_value

    try:
        from discord_bot import get_bot, start_discord_bot, stop_discord_bot
    except Exception as exc:
        logger.warning(f"Discord bot module unavailable: {exc}")
        return

    desired_token = str(getattr(config, "discord_bot_token", "") or "").strip()
    desired_enabled = bool(getattr(config, "allow_discord_bot", False) and desired_token)

    bot = get_bot()
    running = bool(bot and bot.is_running())
    token_changed = bool(_discord_bot_token_value and desired_token and _discord_bot_token_value != desired_token)

    if (not desired_enabled) or token_changed:
        if bot is not None:
            try:
                await stop_discord_bot()
            except Exception as exc:
                logger.warning(f"Failed to stop Discord bot: {exc}")
        _discord_bot_task = None
        if not desired_enabled:
            _discord_bot_token_value = ""
            return
        bot = get_bot()
        running = bool(bot and bot.is_running())

    if desired_enabled and not running:
        try:
            started_bot = await start_discord_bot(
                token=desired_token,
                process_query_func=_discord_process_query,
                agent_name="EchoSpeak",
            )
            _discord_bot_task = getattr(started_bot, "_task", None)
            _discord_bot_token_value = desired_token
            logger.info("Discord bot startup initiated")
            asyncio.create_task(_discord_startup_health_check())
        except Exception as exc:
            _discord_bot_task = None
            logger.warning(f"Failed to start Discord bot: {exc}")
    elif desired_enabled:
        _discord_bot_task = getattr(bot, "_task", None) if bot is not None else None
        _discord_bot_token_value = desired_token


_heartbeat_runtime_lock = threading.Lock()


async def _reconcile_heartbeat_runtime() -> None:
    from agent.heartbeat import HeartbeatManager, get_heartbeat_manager, set_heartbeat_manager

    desired_enabled = bool(getattr(config, "heartbeat_enabled", False))
    heartbeat_project_id = str(getattr(config, "heartbeat_project_id", "") or "").strip()
    heartbeat_session_id = str(getattr(config, "heartbeat_session_id", "") or "").strip()
    if desired_enabled and (not heartbeat_project_id or not heartbeat_session_id):
        logger.error("Heartbeat requires HEARTBEAT_PROJECT_ID and HEARTBEAT_SESSION_ID")
        desired_enabled = False

    with _heartbeat_runtime_lock:
        hb = get_heartbeat_manager()
        if not desired_enabled:
            if hb is not None and hb.is_running:
                hb.stop()
            return

        agent = get_agent()
        hb = get_heartbeat_manager()
        if hb is None:
            hb = HeartbeatManager(
                agent=agent,
                project_id=heartbeat_project_id,
                session_id=heartbeat_session_id,
            )
            set_heartbeat_manager(hb)
        else:
            hb.set_agent(agent)
            hb.update_config(
                interval_minutes=getattr(config, "heartbeat_interval", 30),
                prompt=getattr(config, "heartbeat_prompt", ""),
                channels=list(getattr(config, "heartbeat_channels", ["web"])),
                project_id=heartbeat_project_id,
                session_id=heartbeat_session_id,
            )

        if not hb.is_running:
            hb.start()


def get_document_store():
    agent = get_agent()
    if not bool(getattr(config, "document_rag_enabled", False)):
        return None
    return getattr(agent, "document_store", None)


def _apply_thread_scope(agent, thread_id: Optional[str], workspace_override: Optional[str] = None) -> dict[str, Any]:
    store = get_state_store()
    normalized_thread_id = _normalize_thread_id(thread_id)
    if hasattr(agent, "select_thread_runtime"):
        agent.select_thread_runtime(normalized_thread_id)
    else:
        setattr(agent, "_current_thread_id", normalized_thread_id)
    state = store.get_thread_state(normalized_thread_id)

    workspace_value = str(workspace_override or "").strip()
    if workspace_value:
        if workspace_value.lower() in {"auto", "default", "none", "clear"}:
            agent.configure_workspace(None)
            workspace_id = ""
        else:
            agent.configure_workspace(workspace_value)
            workspace_id = workspace_value
    else:
        workspace_id = str(state.workspace_id or "").strip()
        agent.configure_workspace(workspace_id or None)

    project_id = str(state.active_project_id or "").strip()
    # The agent instance is shared, but Project scope is Session-owned. Never
    # copy the previously selected Session's Project into a fresh Session.
    if project_id:
        if project_id != str(getattr(agent, "_active_project_id", None) or ""):
            agent.activate_project(project_id)
    else:
        # Full detach transaction — do not leave soft path / ActiveWork / preview.
        if str(getattr(agent, "_active_project_id", None) or "") or str(state.project_path or ""):
            if hasattr(agent, "_clear_session_project_scope"):
                agent._clear_session_project_scope(
                    thread_id=normalized_thread_id,
                    reason="Session has no attached Project",
                )
            else:
                setattr(agent, "_active_project_id", None)
        else:
            setattr(agent, "_active_project_id", None)
    updated = store.get_thread_state(normalized_thread_id)
    # Keep workspace_id / provider stamps without re-writing a cleared project id.
    updated = store.update_thread_state(
        normalized_thread_id,
        workspace_id=str(getattr(agent, "_workspace_id", None) or updated.workspace_id or ""),
        runtime_provider=str(getattr(getattr(agent, "llm_provider", None), "value", getattr(agent, "llm_provider", "")) or ""),
    )
    return updated.model_dump()


def _load_webhook_secret() -> str:
    secret = str(getattr(config, "webhook_secret", "") or "").strip()
    if secret:
        return secret
    path_val = str(getattr(config, "webhook_secret_path", "") or "").strip()
    if not path_val:
        return ""
    path = Path(path_val).expanduser()
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return ""


def _parse_signature(header_val: str) -> Optional[str]:
    raw = str(header_val or "").strip()
    if not raw:
        return None
    if raw.lower().startswith("sha256="):
        raw = raw.split("=", 1)[1].strip()
    raw = raw.strip()
    if not raw:
        return None
    return raw


def _verify_webhook_signature(secret: str, body: bytes, signature_header: Optional[str]) -> bool:
    if not secret:
        return False
    sig = _parse_signature(signature_header or "")
    if not sig:
        return False
    expected = hmac.new(secret.encode("utf-8"), body or b"", hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


_cron_state_lock = threading.Lock()


def _load_cron_state() -> dict:
    path_val = str(getattr(config, "cron_state_path", "") or "").strip()
    if not path_val:
        return {}
    path = Path(path_val).expanduser()
    try:
        if not path.exists():
            return {}
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cron_state(state: dict) -> None:
    path_val = str(getattr(config, "cron_state_path", "") or "").strip()
    if not path_val:
        return
    path = Path(path_val).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return


def get_vision_manager():
    """Get or create the vision manager instance."""
    global _vision_manager
    if _vision_manager is None:
        from io_module.vision import create_vision_manager
        _vision_manager = create_vision_manager()
    return _vision_manager


def _metric_inc(key: str, amount: int = 1) -> None:
    with _metrics_lock:
        if key not in _metrics:
            _metrics[key] = 0
        _metrics[key] += amount


def _record_tool_latency(ms: float) -> None:
    with _metrics_lock:
        _tool_latency_ms.append(ms)


# Tool name → agent_mode classification for visualizer
_RESEARCH_TOOLS = frozenset({"web_search", "browse_task"})
_CODING_TOOLS = frozenset({"file_write", "file_read", "file_list", "file_move", "file_copy", "file_delete", "file_mkdir", "artifact_write", "terminal_run", "notepad_write"})


def _classify_agent_mode(tool_name: str) -> str:
    if tool_name in _RESEARCH_TOOLS:
        return "research"
    if tool_name in _CODING_TOOLS:
        return "coding"
    return "working"


_STREAM_SECRET_KEY = re.compile(
    r"(?i)(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|secret|cookie)"
)


def _redact_stream_text(value: str, limit: int = 280) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(
        r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+",
        r"\1[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]\s*[^,\s}\]]+",
        r"\1=[redacted]",
        text,
    )
    return text if len(text) <= limit else text[:limit].rstrip() + "â€¦"


def _safe_tool_input_preview(tool_name: str, raw_input: str) -> str:
    """Return bounded user-useful arguments without file bodies or secrets."""
    raw = str(raw_input or "")
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        parsed = None
    if isinstance(parsed, dict):
        projected: dict[str, Any] = {}
        visible_keys = {
            "query", "url", "path", "source", "destination", "target",
            "operation", "location", "city", "league", "team", "ticker",
            "date", "date_from", "date_to", "provider", "model",
        }
        for key, value in parsed.items():
            key_text = str(key)
            if _STREAM_SECRET_KEY.search(key_text):
                projected[key_text] = "[redacted]"
            elif key_text.casefold() in {"content", "text", "body", "data", "patch"}:
                projected[key_text] = f"<{len(str(value or ''))} chars>"
            elif key_text.casefold() in visible_keys and isinstance(value, (str, int, float, bool)):
                projected[key_text] = _redact_stream_text(str(value), 140)
        if projected:
            return _redact_stream_text(json.dumps(projected, ensure_ascii=False), 320)
    name = str(tool_name or "").casefold()
    if name in {"file_write", "artifact_write", "notepad_write"}:
        return "Writing governed content"
    if name == "terminal_run":
        return _redact_stream_text(raw, 180)
    return _redact_stream_text(raw, 260)


def _safe_tool_result_summary(tool_name: str, output: Any, *, success: bool = True) -> str:
    name = str(tool_name or "tool").casefold()
    if not success:
        return "Tool failed â€” trying another approach."
    if name == "web_search":
        return "Search results received."
    if name == "terminal_run":
        return "Terminal action completed."
    if name in {
        "file_write", "file_read", "file_list", "file_move", "file_copy",
        "file_delete", "file_mkdir", "artifact_write", "notepad_write",
    }:
        return f"{name.replace('_', ' ').capitalize()} completed."
    text = _redact_stream_text(str(output or ""), 280)
    return text or f"{name.replace('_', ' ').capitalize()} completed."


def _safe_stream_failure(exc: BaseException) -> str:
    name = type(exc).__name__.casefold()
    detail = str(exc or "").casefold()
    if "cancel" in name or "cancel" in detail:
        return "Stopped by Ty."
    if "timeout" in name or "stall" in name or "timed out" in detail:
        return "The selected provider stalled. This run stopped cleanly."
    if "connect" in name or "unavailable" in detail:
        return "The selected model is unavailable right now."
    return "Echo stopped this run safely after an internal problem."


class _StreamingHandler(BaseCallbackHandler):
    def __init__(self, q: queue.Queue, request_id: str):
        self._q = q
        self._request_id = request_id
        self._event_seq = 0
        self._tool_run_map: dict = {}
        self._tool_started_at: dict = {}
        self._tool_input_map: dict = {}
        self._research_runs: list[dict[str, Any]] = []
        self._in_think_block = False
        self._loop_blocks: list[str] = []
        self._current_reasoning = ""
        # Visible answer text for the *current* LLM generation (pre-tool beat).
        self._visible_gen = ""
        self._partial_count = 0
        self.partial_replies: list[str] = []
        # One guaranteed preamble beat per LLM generation that invokes tools.
        self._preamble_done_this_gen = False
        # One spoken pre-tool beat per request; bounded model-loop iterations
        # must never produce duplicate greetings.
        self._preamble_done_this_request = False
        # Optional: agent generates free-form wording when the model produced none.
        self._preamble_fn = None  # type: ignore[assignment]
        self._on_partial = None  # type: ignore[assignment]
        # No-progress detection: track repeated tool call signatures
        self._tool_call_signatures: dict[str, int] = {}  # hash -> count
        self._visible_tool_keys: dict[str, str] = {}
        self._hidden_duplicate_tool_ids: set[str] = set()
        self._loop_warning_sent = False
        # Private model reasoning is not part of the frontend stream contract.
        self._expose_model_reasoning = False
        # Durable lifecycle and tool events replace synthetic spoken preambles
        # in the canonical runtime, avoiding an extra free-form model call.
        self._emit_synthetic_preamble = False
        self._iteration_count = 0
        self._seen_llm_run_ids: set[str] = set()
        self._token_usage = {"prompt": 0, "completion": 0, "total": 0}

    def _put(self, event: dict) -> None:
        """Emit a stream event with monotonic seq for reconnect/reorder guards."""
        self._event_seq += 1
        payload = dict(event or {})
        payload.setdefault("request_id", self._request_id)
        if str(payload.get("type") or "") == "turn_bound":
            _bind_query_execution(
                self._request_id,
                str(payload.get("execution_id") or payload.get("turn_id") or ""),
            )
        payload["seq"] = self._event_seq
        payload.setdefault("at", time.time())
        activity = semantic_activity_from_stream_payload(payload)
        if activity is not None:
            payload["activity"] = activity
        self._q.put(payload)

    @property
    def research_runs(self) -> list[dict[str, Any]]:
        return list(self._research_runs)

    def set_preamble_fn(self, fn) -> None:
        """Wire agent-side free-form beat generator (decision stays in code)."""
        self._preamble_fn = fn

    def set_on_partial(self, fn) -> None:
        """Notify agent when a mid-turn spoken beat is sealed."""
        self._on_partial = fn

    # Internal / silent tools must NOT trigger a spoken beat (time inject, calc, memory…).
    # User-facing tools (web_search, files, browser, etc.) always do.
    _PREAMBLE_SKIP_TOOLS = frozenset({
        "get_system_time",
        "calculate",
        "system_info",
        "project_update_context",
        "store_memory",
        "save_memory",
        "recall_memory",
        "search_memory",
        "query_memory",
        "memory_store",
        "memory_recall",
    })

    def _tool_requires_preamble(self, tool_name: str) -> bool:
        n = re.sub(r"[^a-z0-9_]+", "", str(tool_name or "").strip().lower())
        if not n:
            return False
        if n in self._PREAMBLE_SKIP_TOOLS:
            return False
        if "memory" in n and n not in {"memory_store", "memory_recall"}:
            # catch-all for other memory helpers
            if n.startswith("memory") or n.endswith("memory"):
                return False
        return True

    def _looks_like_tool_payload(self, text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return True
        # Pure tool/function JSON — not something we should speak mid-turn.
        if t.startswith("{") and ("\"name\"" in t or "\"tool\"" in t or "arguments" in t):
            return True
        if t.startswith("```") and "function" in t.lower():
            return True
        return False

    def _is_usable_preamble(self, text: str) -> bool:
        t = (text or "").strip()
        if len(t) < 3:
            return False
        if self._looks_like_tool_payload(t):
            return False
        # Reject pure reasoning dumps / markdown thought headers
        low = t.lower()
        if low.startswith("### ") or "model thoughts" in low or low.startswith("<think"):
            return False
        return True

    def _emit_partial(self, text: str, reason: str) -> None:
        text = (text or "").strip()
        if not self._is_usable_preamble(text):
            return
        if self.partial_replies and self.partial_replies[-1].strip() == text:
            return
        # Hard stop: never emit two spoken first-beats in one request.
        if self._preamble_done_this_request:
            return
        self._partial_count += 1
        self.partial_replies.append(text)
        self._preamble_done_this_gen = True
        self._preamble_done_this_request = True
        # Keep agent state in sync so finalize can forbid re-greetings.
        try:
            if callable(self._on_partial):
                self._on_partial(text)
        except Exception:
            pass
        self._put(
            {
                "type": "partial_reply",
                "response": text,
                "speak": True,
                "segment": self._partial_count,
                "reason": reason,
                "at": time.time(),
                "request_id": self._request_id,
            }
        )

    def _flush_partial_reply(
        self,
        reason: str = "tool",
        *,
        tool_name: str = "",
        tool_input: str = "",
        force: bool = True,
    ) -> None:
        """
        Code-level guarantee: before tools run, emit exactly one spoken beat.

        Wording preference order:
          1) Model-streamed visible text for this generation (if any)
          2) Fresh free-form line from agent preamble generator
          3) Soft varied fallback (only if generation fails)

        Decision to emit is never left to the model remembering a prompt.
        """
        # Already sealed this generation or this full request — never double first-beat.
        if self._preamble_done_this_gen or self._preamble_done_this_request:
            self._visible_gen = ""
            return

        text = (self._visible_gen or "").strip()
        self._visible_gen = ""
        if not self._is_usable_preamble(text):
            text = ""

        # Always consult generator when available: it enforces social-first order
        # when the user greeted / asked how Echo is (model often skips that).
        if callable(self._preamble_fn):
            try:
                generated = str(
                    self._preamble_fn(tool_name or "tool", tool_input or "", text) or ""
                ).strip()
                if self._is_usable_preamble(generated):
                    text = generated
            except TypeError:
                # Older 2-arg callback
                try:
                    if not text:
                        text = str(self._preamble_fn(tool_name or "tool", tool_input or "") or "").strip()
                except Exception as exc:
                    logger.warning("Preamble generator failed: {}", exc)
            except Exception as exc:
                logger.warning("Preamble generator failed: {}", exc)

        if not self._is_usable_preamble(text):
            # Last-resort variety — never say the raw tool id ("web search").
            # Prefer agent social-aware fallback when the user opened socially.
            import random
            tool = re.sub(r"[_\-]+", " ", str(tool_name or "")).strip().lower()
            if tool in {"web search", "websearch", "search"}:
                task = "that"
            elif tool:
                task = tool
            else:
                task = "that"
            social_fallback = ""
            try:
                agent = getattr(self, "_agent_ref", None)
                if agent is not None and hasattr(agent, "_user_has_social_open"):
                    uq = str(getattr(agent, "_active_user_query", "") or "")
                    if agent._user_has_social_open(uq) and hasattr(agent, "_social_task_preamble_fallback"):
                        social_fallback = str(agent._social_task_preamble_fallback(task) or "").strip()
            except Exception:
                social_fallback = ""
            if social_fallback:
                options = [social_fallback]
            else:
                options = [
                    f"On it — pulling {task} up.",
                    f"One sec, checking {task}.",
                    f"Alright, looking into {task}.",
                    "Checking that now.",
                    "Let me pull that up.",
                    "Hang on, grabbing it.",
                ]
            text = random.choice(options)

        self._emit_partial(text, reason)

    def _start_new_generation(self, run_id: Any = None):
        run_key = str(run_id or "").strip()
        if run_key and run_key in self._seen_llm_run_ids:
            return
        if run_key:
            self._seen_llm_run_ids.add(run_key)
        self._iteration_count += 1
        # Save previous loop's reasoning before starting a new one
        if self._current_reasoning.strip():
            loop_idx = len(self._loop_blocks) + 1
            header = f"### Model Thoughts (Loop {loop_idx})"
            self._loop_blocks.append(f"{header}\n{self._current_reasoning.strip()}")
        self._current_reasoning = ""
        # New LLM generation after tools — start a fresh visible buffer
        # (prior preamble should already have been flushed on tool_start).
        self._visible_gen = ""
        self._in_think_block = False
        self._preamble_done_this_gen = False
        # Do not reset _preamble_done_this_request: later canonical model-loop
        # iterations must not emit another spoken pre-tool beat.
        # Reliable phase signal for avatar/chat (was only set on tool_start before).
        self._put({
            "type": "iteration_boundary",
            "iteration": self._iteration_count,
            "phase": "model_call",
            "model": str(
                getattr(getattr(self, "_agent_ref", None), "_selected_model_id", lambda: "")()
                or ""
            ),
            "at": time.time(),
            "request_id": self._request_id,
        })
        self._put({
            "type": "status",
            "agent_mode": "thinking",
            "at": time.time(),
            "request_id": self._request_id,
        })

    def on_llm_start(self, serialized: dict, prompts: Any, run_id: str, parent_run_id: Optional[str] = None, **kwargs):
        self._start_new_generation(run_id)

    def on_chat_model_start(self, serialized: dict, messages: Any, run_id: str, parent_run_id: Optional[str] = None, **kwargs):
        self._start_new_generation(run_id)

    @staticmethod
    def _usage_from_response(response: Any) -> dict[str, int]:
        def as_non_negative_int(value: Any) -> int:
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError, OverflowError):
                return 0

        candidates: list[Any] = []
        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, dict):
            candidates.extend([
                llm_output.get("token_usage"),
                llm_output.get("usage"),
                llm_output.get("usage_metadata"),
            ])
        for generation_group in list(getattr(response, "generations", None) or []):
            for generation in list(generation_group or []):
                message = getattr(generation, "message", None)
                candidates.extend([
                    getattr(message, "usage_metadata", None),
                    getattr(message, "response_metadata", None),
                    getattr(generation, "generation_info", None),
                ])
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            nested = candidate.get("token_usage") or candidate.get("usage")
            if isinstance(nested, dict):
                candidate = nested
            prompt = as_non_negative_int(
                candidate.get("prompt_tokens")
                or candidate.get("input_tokens")
                or candidate.get("prompt_token_count")
                or 0
            )
            completion = as_non_negative_int(
                candidate.get("completion_tokens")
                or candidate.get("output_tokens")
                or candidate.get("candidates_token_count")
                or 0
            )
            total = as_non_negative_int(
                candidate.get("total_tokens") or candidate.get("total_token_count") or 0
            )
            if prompt or completion or total:
                return {
                    "prompt": prompt,
                    "completion": completion,
                    "total": total or prompt + completion,
                }
        return {}

    @staticmethod
    def _reasoning_summary_from_response(response: Any) -> str:
        """Read only explicit provider summary fields, never reasoning_content."""
        candidates: list[Any] = []
        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, dict):
            candidates.extend([
                llm_output.get("reasoning_summary"),
                llm_output.get("summary"),
            ])
        for generation_group in list(getattr(response, "generations", None) or []):
            for generation in list(generation_group or []):
                message = getattr(generation, "message", None)
                additional = getattr(message, "additional_kwargs", None)
                if isinstance(additional, dict):
                    candidates.append(additional.get("reasoning_summary"))
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return _redact_stream_text(candidate, 600)
            if isinstance(candidate, list):
                text = " ".join(
                    str(item.get("text") or item.get("summary") or "")
                    if isinstance(item, dict) else str(item)
                    for item in candidate
                ).strip()
                if text:
                    return _redact_stream_text(text, 600)
        return ""

    def on_llm_end(self, response: Any, run_id: str, parent_run_id: Optional[str] = None, **kwargs):
        usage = self._usage_from_response(response)
        if usage:
            for key in self._token_usage:
                self._token_usage[key] += int(usage.get(key) or 0)
            self._put({"type": "token_usage", **self._token_usage})
        expose_summary = bool(
            getattr(getattr(self, "_agent_ref", None), "_turn_thinking_enabled", True)
        )
        summary = self._reasoning_summary_from_response(response) if expose_summary else ""
        if summary:
            self._put({
                "type": "reasoning_summary",
                "content": summary,
                "iteration": self._iteration_count,
            })

    def _process_token_or_reasoning(self, token: str, reasoning: str):
        # 1. Extract inline <think> tags from main content stream if no native reasoning is provided
        visible_token = token
        if not reasoning and token:
            t_low = token.lower()
            if "<think>" in t_low:
                self._in_think_block = True
                parts = token.split("<think>", 1)
                visible_token = parts[0]
                if len(parts) > 1:
                    reasoning = parts[1]
            elif "</think>" in t_low:
                self._in_think_block = False
                parts = token.split("</think>", 1)
                reasoning = parts[0]
                visible_token = parts[1] if len(parts) > 1 else ""
            elif self._in_think_block:
                reasoning = token
                visible_token = ""

        # 2. Push accumulated loops + current reasoning to UI
        if reasoning and self._expose_model_reasoning:
            self._current_reasoning += reasoning
            blocks = list(self._loop_blocks)
            loop_idx = len(self._loop_blocks) + 1
            if loop_idx > 1 or len(self._loop_blocks) > 0:
                current_header = f"### Model Thoughts (Loop {loop_idx})"
            else:
                current_header = "### Model Thoughts"
            blocks.append(f"{current_header}\n{self._current_reasoning}")
            
            self._put({
                "type": "thinking",
                "content": "\n\n".join(blocks),
                "at": time.time(),
                "request_id": self._request_id
            })

        # 3. Stream non-reasoning answer tokens so the chat can show live text.
        # Buffer per generation so we can seal a partial spoken beat before tools.
        if visible_token and not self._in_think_block and not reasoning:
            self._visible_gen += visible_token
            self._put({
                "type": "agent_token",
                "data": visible_token,
                "at": time.time(),
                "request_id": self._request_id,
            })

    def on_llm_new_token(self, token: str, **kwargs):
        chunk = kwargs.get("chunk")
        reasoning = ""
        if chunk:
            if hasattr(chunk, "message") and chunk.message:
                msg = chunk.message
                if hasattr(msg, "additional_kwargs") and msg.additional_kwargs:
                    reasoning = msg.additional_kwargs.get("reasoning_content") or ""
            if not reasoning and hasattr(chunk, "additional_kwargs") and chunk.additional_kwargs:
                reasoning = chunk.additional_kwargs.get("reasoning_content") or ""
        self._process_token_or_reasoning(token, reasoning)

    def on_llm_chunk(self, chunk: Any, **kwargs: Any) -> Any:
        token = ""
        reasoning = ""
        if hasattr(chunk, "message") and chunk.message:
            msg = chunk.message
            if hasattr(msg, "content"):
                token = str(msg.content or "")
            if hasattr(msg, "additional_kwargs") and msg.additional_kwargs:
                reasoning = msg.additional_kwargs.get("reasoning_content") or ""
        elif hasattr(chunk, "text"):
            token = str(chunk.text or "")
        
        if not reasoning and hasattr(chunk, "additional_kwargs") and chunk.additional_kwargs:
            reasoning = chunk.additional_kwargs.get("reasoning_content") or ""
        
        self._process_token_or_reasoning(token, reasoning)

    def on_tool_start(self, serialized: dict, input_str: str, run_id: str, parent_run_id: Optional[str] = None, **kwargs):
        tool_name = (serialized or {}).get("name") or (serialized or {}).get("id") or "tool"
        call_id = str(run_id)
        raw_input = input_str if isinstance(input_str, str) else str(input_str)
        normalized_input = re.sub(r"\s+", " ", raw_input).strip().casefold()
        if str(tool_name) == "file_list":
            normalized_input = re.sub(r"['\"]?limit['\"]?\s*[:=]\s*\d+\s*,?", "", normalized_input)
            normalized_input = re.sub(r"\s+", " ", normalized_input).strip(" {},")
        visible_key = str(tool_name) if str(tool_name) == "web_search" else f"{tool_name}:{normalized_input}"
        if str(tool_name) in {"web_search", "file_list"} and visible_key in self._visible_tool_keys:
            self._hidden_duplicate_tool_ids.add(call_id)
        else:
            self._visible_tool_keys[visible_key] = call_id
        try:
            agent = getattr(self, "_agent_ref", None)
            if agent is not None:
                if str(tool_name or "") == "web_search":
                    if hasattr(agent, "_set_outer_web_search_id"):
                        agent._set_outer_web_search_id(call_id)
                    else:
                        agent._lc_outer_web_search_id = call_id
                        agent._grounded_fanout_count = 0
                if hasattr(agent, "_register_tool_run"):
                    agent._register_tool_run(str(tool_name or ""), call_id)
                if hasattr(agent, "_ensure_durable_tool_run_started"):
                    agent._ensure_durable_tool_run_started(str(tool_name or ""), call_id, str(input_str or ""))
        except Exception:
            pass
        # Deterministic beat only for user-facing tools — never for silent injects
        # like get_system_time (that was firing "Checking that now." then skipping weather).
        if (
            self._emit_synthetic_preamble
            and call_id not in self._hidden_duplicate_tool_ids
            and self._tool_requires_preamble(str(tool_name or ""))
        ):
            self._flush_partial_reply(
                "tool_start",
                tool_name=str(tool_name or ""),
                tool_input=raw_input,
                force=True,
            )
        self._tool_run_map[call_id] = tool_name
        self._tool_started_at[call_id] = time.perf_counter()
        self._tool_input_map[call_id] = raw_input
        _metric_inc("tool_calls", 1)

        # No-progress detection: hash tool name + input to detect repeated identical calls
        import hashlib as _hl
        sig = _hl.md5(f"{tool_name}:{raw_input}".encode("utf-8", errors="ignore")).hexdigest()
        self._tool_call_signatures[sig] = self._tool_call_signatures.get(sig, 0) + 1
        repeat_count = self._tool_call_signatures[sig]
        if repeat_count >= 3 and not self._loop_warning_sent:
            self._loop_warning_sent = True
            self._put({
                "type": "thinking",
                "content": f"### ⚠️ Loop Detected\nThe model has called `{tool_name}` with the same arguments {repeat_count} times. The agent will be stopped after the current iteration to prevent infinite looping.",
                "at": time.time(),
                "request_id": self._request_id,
            })
            logger.warning("No-progress detected: tool '{}' called {} times with identical input", tool_name, repeat_count)

        # Chat receives only a bounded preview. Full arguments stay on the
        # governed ToolRun projection for authorized inspectors.
        inp = _safe_tool_input_preview(str(tool_name or ""), raw_input)
        if call_id not in self._hidden_duplicate_tool_ids:
            self._put(
            {
                "type": "tool_start",
                "id": call_id,
                "name": tool_name,
                "input": inp,
                "at": time.time(),
                "request_id": self._request_id,
            }
        )
        # Emit agent_mode status for visualizer
        mode = _classify_agent_mode(tool_name)
        self._put({"type": "status", "agent_mode": mode, "tool": tool_name, "at": time.time(), "request_id": self._request_id})

    def on_tool_end(self, output: str, run_id: str, parent_run_id: Optional[str] = None, **kwargs):
        call_id = str(run_id)
        out = output if isinstance(output, str) else str(output)
        tool_name = self._tool_run_map.get(call_id, "")
        raw_input = self._tool_input_map.pop(call_id, "")
        if call_id in self._hidden_duplicate_tool_ids:
            self._hidden_duplicate_tool_ids.discard(call_id)
            self._tool_started_at.pop(call_id, None)
            return
        started = self._tool_started_at.pop(call_id, None)
        if started is not None:
            _record_tool_latency((time.perf_counter() - started) * 1000.0)
        # Multi-intent fan-out already closed the outer LC web_search row (UI + durable)
        # and emitted per-intent children — drop the outer tool_end to avoid a third row.
        try:
            agent = getattr(self, "_agent_ref", None)
            fanout = int(getattr(agent, "_grounded_fanout_count", 0) or 0) if agent is not None else 0
            if agent is not None and hasattr(agent, "_get_outer_web_search_id"):
                outer = str(agent._get_outer_web_search_id() or "")
            else:
                outer = str(getattr(agent, "_lc_outer_web_search_id", "") or "") if agent is not None else ""
            # Also skip if outer was already cleared after _close_outer_web_search_tool_run
            # but this LC end still fires with the original outer call_id.
            if tool_name == "web_search" and fanout > 1 and (call_id == outer or (
                agent is not None
                and call_id
                and not outer
                and hasattr(agent, "get_tool_outcome")
                and agent.get_tool_outcome(call_id) is not None
                and str((agent.get_tool_outcome(call_id).output or "")).startswith("(expanded")
            )):
                if agent is not None and hasattr(agent, "_dequeue_tool_run"):
                    agent._dequeue_tool_run(call_id, "web_search")
                if agent is not None:
                    agent._grounded_fanout_count = 0
                    if hasattr(agent, "_clear_outer_web_search_id"):
                        agent._clear_outer_web_search_id(call_id)
                    else:
                        agent._lc_outer_web_search_id = ""
                self._put({
                    "type": "status",
                    "agent_mode": "thinking",
                    "at": time.time(),
                    "request_id": self._request_id,
                })
                return
        except Exception:
            pass
        event = {
            "type": "tool_end",
            "id": call_id,
            "name": tool_name,
            "output": _safe_tool_result_summary(tool_name, out),
            "at": time.time(),
            "request_id": self._request_id,
        }
        try:
            agent = getattr(self, "_agent_ref", None)
            outcome = agent.get_tool_outcome(call_id) if agent is not None and hasattr(agent, "get_tool_outcome") else None
            if outcome is not None:
                event["outcome"] = {
                    "success": bool(outcome.success),
                    "status": str(outcome.status or ""),
                    "error_message": (
                        "" if outcome.success else "Tool failed — trying another approach."
                    ),
                }
        except Exception:
            pass
        research_run = build_research_run(run_id=call_id, tool_name=tool_name, tool_input=raw_input, output=output if isinstance(output, str) else str(output), at=event["at"])
        if research_run is not None:
            event["research"] = research_run
            self._research_runs.append(research_run)
        self._put(event)
        # After a tool completes, return to thinking so UI does not stick on last tool mode.
        self._put({
            "type": "status",
            "agent_mode": "thinking",
            "at": time.time(),
            "request_id": self._request_id,
        })

    def on_tool_error(self, error: BaseException, run_id: str, parent_run_id: Optional[str] = None, **kwargs):
        call_id = str(run_id)
        _metric_inc("tool_errors", 1)
        started = self._tool_started_at.pop(call_id, None)
        if started is not None:
            _record_tool_latency((time.perf_counter() - started) * 1000.0)
        tool_name = self._tool_run_map.get(call_id, "")
        self._put({
            "type": "tool_error",
            "id": call_id,
            "name": tool_name,
            "error": "Tool failed — trying another approach.",
            "at": time.time(),
            "request_id": self._request_id,
        })
        self._put({
            "type": "status",
            "agent_mode": "thinking",
            "at": time.time(),
            "request_id": self._request_id,
        })


def _start_agent_thread(
    *,
    agent,
    message: str,
    include_memory: bool,
    thread_id: Optional[str],
    workspace: Optional[str],
    request_id: str,
    q: queue.Queue,
    cancel_event: threading.Event,
    thinking_enabled: bool = True,
    reasoning_effort: str = "medium",
    source: str = "web",
    voice_turn_id: str = "",
) -> None:
    def run_agent():
        handler: Optional[_StreamingHandler] = None
        try:
            handler = _StreamingHandler(q, request_id)
            handler._agent_ref = agent  # social-aware last-resort preambles
            # Decision = code (on tool_start). Wording = free model generation when needed.
            try:
                # Fresh multi-beat state for this turn
                agent._turn_partial_beats = []
                agent._active_user_query = message
                handler.set_on_partial(lambda text: agent.record_turn_partial_beat(text))
            except Exception:
                pass
            # Scope was persisted before this worker started; process_query restores
            # it once under the agent request lock.
            thread_state = get_state_store().get_thread_state(thread_id).model_dump()
            memory_before = int(agent.memory.count_items(thread_id=thread_id) or 0)
            response, success = agent.process_query(
                message,
                include_memory=include_memory,
                callbacks=[handler],
                thread_id=thread_id,
                source=source,
                cancel_event=cancel_event,
                request_id=request_id,
                thinking_enabled=thinking_enabled,
                reasoning_effort=reasoning_effort,
            )
            doc_sources = agent.get_last_doc_sources() if include_memory else []
            state_store = get_state_store()
            latest_state = state_store.get_thread_state(thread_id).model_dump()
            worker_execution_id = str(
                getattr(agent, "completed_execution_id_for_current_worker", lambda: "")() or ""
            )
            if not worker_execution_id and not voice_turn_id:
                worker_execution_id = str(latest_state.get("last_execution_id") or "")
            if voice_turn_id and not worker_execution_id:
                raise RuntimeError("Voice query completed without an exact worker Execution identity")
            execution = state_store.get_execution(worker_execution_id) if worker_execution_id else None
            if voice_turn_id and execution is None:
                raise RuntimeError("Voice query completed without its durable Execution")
            if voice_turn_id and execution is not None:
                from agent.voice_transport import bind_voice_turn_submission

                bind_voice_turn_submission(
                    voice_turn_id,
                    session_id=str(thread_id or ""),
                    request_id=request_id,
                    execution_id=execution.id,
                    task_run_id=str(execution.task_run_id or ""),
                    query_completed=True,
                )
            turn_projection = state_store.turn_projection(execution.id) if execution is not None else None
            execution_projection = dict((turn_projection or {}).get("execution_projection") or {})
            response_render = None
            try:
                exec_meta = execution.metadata if execution is not None else {}
                if isinstance(exec_meta, dict):
                    response_render = exec_meta.get("response_render")
            except Exception:
                response_render = None
            spoken_text = ""
            try:
                spoken_text = str(agent.get_last_tts_text() or "")
            except Exception:
                spoken_text = ""
            # Prefer the last generation's visible text when partials already covered the preamble.
            final_response = str(response or "")
            if handler.partial_replies:
                # Strip already-spoken beats from final so we don't re-say "I'm great" after weather.
                trimmed = final_response
                for part in handler.partial_replies:
                    p = (part or "").strip()
                    if not p:
                        continue
                    if trimmed.startswith(p):
                        trimmed = trimmed[len(p):].lstrip(" \n\t-–—")
                    elif p in trimmed:
                        # Soft fallback: drop first occurrence only
                        trimmed = trimmed.replace(p, "", 1).strip()
                # If stripping wiped everything, keep last non-empty partial out and use leftover live gen
                leftover_gen = (handler._visible_gen or "").strip()
                if trimmed.strip():
                    final_response = trimmed.strip()
                elif leftover_gen:
                    final_response = leftover_gen
                # else keep original response (better than empty)
            memory_after = int(agent.memory.count_items(thread_id=thread_id) or 0)
            if memory_after > memory_before or bool(execution_projection.get("memory_records")):
                handler._put({"type": "memory_saved", "memory_count": memory_after, "at": time.time()})
            handler._put(
                {
                    "type": "final",
                    "response": final_response,
                    "success": success,
                    "memory_count": memory_after,
                    "doc_sources": doc_sources,
                    "research": handler.research_runs,
                    "response_render": response_render,
                    "spoken_text": spoken_text if not handler.partial_replies else (final_response or spoken_text),
                    "partial_replies": list(handler.partial_replies),
                    "execution_id": execution.id if execution else None,
                    "trace_id": execution.trace_id if execution else None,
                    # A newer Turn may already own Session state. In that case,
                    # this older stream receives its own Turn projection but no
                    # stale Session projection capable of overwriting the UI.
                    "thread_state": (
                        latest_state
                        if execution is not None
                        and str(latest_state.get("last_execution_id") or latest_state.get("current_execution_id") or "") == execution.id
                        else {}
                    ),
                    "execution_projection": execution_projection,
                    "voice_turn_id": voice_turn_id or None,
                    "at": time.time(),
                }
            )
        except Exception as e:
            if voice_turn_id:
                try:
                    from agent.voice_transport import fail_voice_turn

                    fail_voice_turn(
                        voice_turn_id,
                        session_id=str(thread_id or ""),
                        error_code="voice_query_failed",
                    )
                except Exception:
                    pass
            _metric_inc("errors", 1)
            diagnostic_id = hashlib.sha256(
                f"{request_id}:{type(e).__name__}:{e}".encode("utf-8", errors="ignore")
            ).hexdigest()[:12]
            logger.exception(
                "Query stream worker failed request_id={} diagnostic_id={}",
                request_id,
                diagnostic_id,
            )
            event = {
                "type": "error",
                "message": _safe_stream_failure(e),
                "diagnostic_id": diagnostic_id,
                "at": time.time(),
                "request_id": request_id,
            }
            if handler is not None:
                handler._put(event)
            else:
                q.put(event)
        finally:
            # Provider, parser, and lifecycle failures must close the same UI
            # activity state as successful turns.
            idle_event = {"type": "status", "agent_mode": "idle", "at": time.time(), "request_id": request_id}
            if handler is not None:
                handler._put(idle_event)
            else:
                q.put(idle_event)
            q.put(None)

    threading.Thread(target=run_agent, daemon=True).start()


def _extract_text_from_upload(filename: str, content_type: Optional[str], data: bytes) -> str:
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    if name.endswith(".pdf") or ctype == "application/pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception as exc:
            raise HTTPException(status_code=503, detail="pypdf is required to parse PDF files") from exc
        try:
            reader = PdfReader(BytesIO(data))
            parts = []
            for page in reader.pages:
                text = page.extract_text() or ""
                if text.strip():
                    parts.append(text.strip())
            return "\n\n".join(parts).strip()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {exc}") from exc

    try:
        return data.decode("utf-8", errors="ignore").strip()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unsupported text encoding: {exc}") from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan."""
    # The API lifespan is the sole scheduler/coordinator owner in server and
    # desktop processes. Agent instances must not start competing daemons.
    os.environ["ECHOSPEAK_API_RUNTIME"] = "1"
    build_id = (
        os.environ.get("ECHOSPEAK_BUILD_ID")
        or os.environ.get("ECHOSPEAK_DESKTOP_INSTANCE_ID")
        or "dev"
    )
    logger.info(
        "Starting Echo Speak API server build_id={} pid={} data_dir={}",
        build_id,
        os.getpid(),
        os.environ.get("ECHOSPEAK_DATA_DIR") or "",
    )
    global _gateway_loop
    _gateway_loop = asyncio.get_running_loop()
    # Build the shared memory/embedding owner during readiness so the first
    # user Turn never pays model-load or vector-store initialization latency.
    # Note: prewarm owns the "default" Session agent only. Other Sessions still
    # construct Session-scoped agents on first use; they share StateStore via
    # get_state_store() singleton + phase3 process lock (not duplicate writers).
    if bool(getattr(config, "enable_prewarm", False)):
        try:
            prewarmed_agent = await asyncio.to_thread(get_agent, "default")
            if prewarmed_agent.llm_provider == ModelProvider.LM_STUDIO:
                from agent.model_runtime import (
                    ensure_selected_model_ready,
                    resolve_model_profile,
                    resolve_structured_output_capability,
                )
                model_id = str(prewarmed_agent._selected_model_id() or "default")
                profile = resolve_model_profile(
                    ModelProvider.LM_STUDIO.value,
                    model_id,
                    {"context_limit": int(getattr(config.local, "context_length", 0) or 32768)},
                )
                await asyncio.to_thread(
                    ensure_selected_model_ready,
                    ModelProvider.LM_STUDIO.value,
                    model_id,
                    llm=prewarmed_agent.model_runtime.llm,
                    profile=profile,
                    timeout=float(
                        getattr(config, "turn_understanding_cold_start_timeout_seconds", 120.0)
                        or 120.0
                    ),
                )
                capability = await asyncio.to_thread(
                    resolve_structured_output_capability,
                    ModelProvider.LM_STUDIO.value,
                    model_id,
                    llm=prewarmed_agent.model_runtime.llm,
                    profile=profile,
                    probe_timeout=float(getattr(config, "turn_understanding_probe_timeout_seconds", 8.0) or 8.0),
                )
                if capability.probed and capability.mode == "native_json_schema":
                    prewarmed_agent._turn_understanding_warmed_models = {
                        f"{ModelProvider.LM_STUDIO.value}:{model_id}"
                    }
            logger.info("Default Session runtime and embeddings prewarmed")
        except Exception as exc:
            logger.warning("Runtime prewarm degraded; startup continues honestly: {}", exc)
    else:
        logger.info("Model prewarming disabled on startup; model loads on demand.")
    await _reconcile_discord_bot_runtime()
    await _reconcile_heartbeat_runtime()
    
    # --- Telegram Bot startup (v5.4.0) ---
    if bool(getattr(config, "allow_telegram_bot", False)):
        try:
            from telegram_bot import TelegramBotManager, set_telegram_bot
            tg_agent = get_agent()
            tg_bot = TelegramBotManager(agent=tg_agent)
            set_telegram_bot(tg_bot)
            tg_bot.start()
            logger.info("Telegram bot startup initiated")
        except Exception as e:
            logger.warning(f"Failed to start Telegram bot: {e}")
    
    # --- Twitch Bot startup (v6.7.0) ---
    if bool(getattr(config, "allow_twitch", False)):
        try:
            from twitch_bot import get_twitch_bot
            twitch = get_twitch_bot()
            twitch.set_agent(get_agent())
            await twitch.start()
            logger.info("Twitch bot startup initiated")
        except Exception as e:
            logger.warning(f"Failed to start Twitch bot: {e}")

    # --- Twitter/X Bot startup (v6.7.0) ---
    if bool(getattr(config, "allow_twitter", False)):
        try:
            from twitter_bot import get_twitter_bot
            twitter = get_twitter_bot()
            twitter.set_agent(get_agent())
            await twitter.start()
            logger.info("Twitter/X bot startup initiated")
        except Exception as e:
            logger.warning(f"Failed to start Twitter/X bot: {e}")

    # --- Routine Scheduler startup ---
    try:
        from agent.routines import get_routine_manager

        def _routine_callback(routine):
            """Claim one durable Run, then execute one governed Turn."""
            from agent.automation_runtime import (
                AutomationModelBinding,
                AutomationRunStatus,
                ModelBindingPolicy,
                get_automation_run_store,
            )
            from agent.projects import get_project_manager
            from agent.task_store import get_task_store

            scheduled_for = str(getattr(routine, "next_run", "") or f"run:{int(getattr(routine, 'run_count', 0)) + 1}")
            session_id = str(getattr(routine, "session_id", "") or "").strip()
            project_id = str(getattr(routine, "project_id", "") or "").strip()
            query = str(routine.action_config.get("query") or routine.action_config.get("message") or routine.name).strip()
            if not project_id or not session_id:
                return {
                    "success": False,
                    "error": "Routine requires an explicit Project and Session",
                }
            project = get_project_manager().get_project(project_id)
            session = get_state_store().get_thread_state(session_id)
            if project is None or str(session.active_project_id or "") != project_id:
                return {
                    "success": False,
                    "error": "Routine Project/Session binding is missing or stale",
                }
            task = get_task_store().create(
                title=f"Routine: {routine.name}",
                description=query,
                objective=query,
                project_id=project_id,
                session_id=session_id,
                source="routine",
                source_id=routine.id,
                scheduled_for=scheduled_for,
                idempotency_key=f"routine:{routine.id}:{scheduled_for}",
            )
            if task.status in {"complete", "done"}:
                return {"success": True, "task_id": task.id}
            run_store = get_automation_run_store()
            run = run_store.create_run(
                idempotency_key=f"routine:{routine.id}:{scheduled_for}",
                project_id=project_id,
                session_id=session_id,
                task_id=task.id,
                routine_id=str(routine.id),
                trigger_id=scheduled_for,
                source="routine",
                source_id=str(routine.id),
                objective=query,
                model_binding=AutomationModelBinding(
                    policy=ModelBindingPolicy.SESSION_DEFAULT,
                    source_session_id=session_id,
                ),
            )
            from agent.automation_runtime import (
                AutomationModelBinding,
                AutomationRunStatus,
                ModelBindingPolicy,
                get_automation_run_store,
            )
            from agent.projects import get_project_manager
            from agent.task_store import get_task_store

            scheduled_for = str(getattr(routine, "next_run", "") or f"run:{int(getattr(routine, 'run_count', 0)) + 1}")
            session_id = str(getattr(routine, "session_id", "") or "").strip()
            project_id = str(getattr(routine, "project_id", "") or "").strip()
            query = str(routine.action_config.get("query") or routine.action_config.get("message") or routine.name).strip()
            if not project_id or not session_id:
                return {
                    "success": False,
                    "error": "Routine requires an explicit Project and Session",
                }
            project = get_project_manager().get_project(project_id)
            session = get_state_store().get_thread_state(session_id)
            if project is None or str(session.active_project_id or "") != project_id:
                return {
                    "success": False,
                    "error": "Routine Project/Session binding is missing or stale",
                }
            task = get_task_store().create(
                title=f"Routine: {routine.name}",
                description=query,
                objective=query,
                project_id=project_id,
                session_id=session_id,
                source="routine",
                source_id=routine.id,
                scheduled_for=scheduled_for,
                idempotency_key=f"routine:{routine.id}:{scheduled_for}",
            )
            if task.status in {"complete", "done"}:
                return {"success": True, "task_id": task.id}
            run_store = get_automation_run_store()
            run = run_store.create_run(
                idempotency_key=f"routine:{routine.id}:{scheduled_for}",
                project_id=project_id,
                session_id=session_id,
                task_id=task.id,
                routine_id=str(routine.id),
                trigger_id=scheduled_for,
                source="routine",
                source_id=str(routine.id),
                objective=query,
                model_binding=AutomationModelBinding(
                    policy=ModelBindingPolicy.SESSION_DEFAULT,
                    source_session_id=session_id,
                ),
            )
            if run.status == AutomationRunStatus.COMPLETED:
                return {"success": True, "task_id": task.id, "run_id": run.id}
            claimed = run_store.claim(
                run.id,
                project_id=project_id,
                session_id=session_id,
                claimant_id="api-routine-coordinator",
                expected_revision=run.revision,
                lease_seconds=300,
            )
            if claimed is None or claimed.lease is None:
                return {
                    "success": False,
                    "task_id": task.id,
                    "run_id": run.id,
                    "error": "Routine occurrence is already claimed or no longer queued",
                }
            lease_token = claimed.lease.token
            get_task_store().update(
                task.id,
                status="in_progress",
                automation_run_ids=list(dict.fromkeys([*task.automation_run_ids, run.id])),
            )
            try:
                r_agent = get_agent(session_id)
                provider = str(r_agent.llm_provider.value)
                model_id = str(r_agent.provider_info.get("model") or "default")
                claimed = run_store.bind_model(
                    run.id,
                    AutomationModelBinding(
                        policy=ModelBindingPolicy.SESSION_DEFAULT,
                        source_session_id=session_id,
                        resolved_provider=provider,
                        resolved_model_id=model_id,
                    ),
                    project_id=project_id,
                    session_id=session_id,
                    claimant_id="api-routine-coordinator",
                    lease_token=lease_token,
                )
                claimed = run_store.transition(
                    run.id,
                    AutomationRunStatus.RUNNING,
                    project_id=project_id,
                    session_id=session_id,
                    claimant_id="api-routine-coordinator",
                    lease_token=lease_token,
                )
                response, success = r_agent.process_query(
                    query, source="routine", thread_id=session_id, callbacks=[],
                )
                state = get_state_store().get_thread_state(session_id)
                from agent.automation_projection import project_execution
                channels = list(getattr(routine, "delivery_channels", None) or ["web"])
                blocked_channels = [channel for channel in channels if str(channel).lower() != "web"]
                execution_id = str(state.last_execution_id or state.current_execution_id or "")
                canonical = project_execution(
                    project_id=project_id,
                    session_id=session_id,
                    execution_id=execution_id,
                    occurrence_id=run.id,
                )
                tool_run_ids = list(canonical.tool_run_ids) if canonical else []
                approval_ids = [str(state.pending_approval_id)] if state.pending_approval_id else []
                verified = bool(canonical and canonical.verified and response and not blocked_channels)
                status = (
                    "complete" if verified
                    else "needs_permission" if canonical and canonical.automation_status == "waiting_for_approval"
                    else "blocked" if blocked_channels or (canonical and canonical.product_task_status == "blocked")
                    else "cancelled" if canonical and canonical.product_task_status == "cancelled"
                    else "failed"
                )
                run_status = AutomationRunStatus(
                    "completed" if verified
                    else "blocked" if blocked_channels
                    else canonical.automation_status if canonical
                    else "failed"
                )
                run_store.transition(
                    run.id,
                    run_status,
                    project_id=project_id,
                    session_id=session_id,
                    claimant_id="api-routine-coordinator",
                    lease_token=lease_token,
                    execution_id=execution_id,
                    tool_run_ids=tool_run_ids,
                    approval_ids=approval_ids,
                    artifact_ids=list(canonical.artifact_ids) if canonical else [],
                    task_run_id=str(canonical.task_run_id) if canonical else "",
                    outcome={
                        "verified": verified,
                        "completion_authority": "task_run",
                        "canonical_task_status": canonical.canonical_status.value if canonical else "missing",
                        "canonical_completion_disposition": canonical.completion_disposition if canonical else "pending",
                        "response_present": bool(response),
                        "blocked_delivery_channels": blocked_channels,
                    },
                    error="" if verified else "Governed Turn did not reach a verified terminal outcome",
                )
                get_task_store().update(
                    task.id,
                    status=status,
                    execution_ids=[execution_id] if execution_id else [],
                    task_run_ids=[canonical.task_run_id] if canonical else [],
                    tool_run_ids=tool_run_ids,
                    approval_ids=approval_ids,
                    verification={
                        "verified": verified,
                        "completion_authority": "task_run",
                        "canonical_task_status": canonical.canonical_status.value if canonical else "missing",
                        "response_present": bool(response),
                        "blocked_delivery_channels": blocked_channels,
                        "reason": (
                            "External routine delivery requires a governed communication ToolRun"
                            if blocked_channels else "Turn completed" if verified else "Turn failed"
                        ),
                    },
                )
                return {
                    "success": verified,
                    "task_id": task.id,
                    "run_id": run.id,
                    "error": "External delivery awaits governed approval" if blocked_channels else "" if verified else str(response or "Routine failed"),
                }
            except Exception as exc:
                try:
                    current_run = run_store.get_run(run.id, project_id=project_id, session_id=session_id)
                    if current_run and current_run.status in {
                        AutomationRunStatus.PREPARING,
                        AutomationRunStatus.RUNNING,
                    }:
                        run_store.transition(
                            run.id,
                            AutomationRunStatus.FAILED,
                            project_id=project_id,
                            session_id=session_id,
                            claimant_id="api-routine-coordinator",
                            lease_token=lease_token,
                            error=str(exc),
                        )
                except Exception as transition_exc:
                    logger.error(f"Routine Run failure transition failed: {transition_exc}")
                get_task_store().update(task.id, status="failed", verification={"verified": False, "error": str(exc)})
                logger.warning(f"Routine callback error ({routine.name}): {exc}")
                return {"success": False, "task_id": task.id, "run_id": run.id, "error": str(exc)}

        rm = get_routine_manager()
        from agent.automation_runtime import get_automation_run_store

        recovered_runs = get_automation_run_store().recover_expired()
        if recovered_runs:
            logger.info("Recovered {} expired Automation Runs", len(recovered_runs))
        rm.set_run_callback(_routine_callback)
        rm.start_scheduler()
        logger.info("Routine scheduler started")
    except Exception as exc:
        logger.warning(f"Failed to start routine scheduler: {exc}")

    # Warn if A2A is enabled without authentication
    if getattr(config, "a2a_enabled", False) and not getattr(config, "a2a_auth_key", ""):
        logger.warning("⚠️ A2A protocol enabled WITHOUT authentication key! Set A2A_AUTH_KEY for production.")

    # --- Spotify Playback Monitor startup ---
    global _spotify_monitor_task
    if bool(getattr(config, "allow_spotify", False)):
        _spotify_monitor_task = asyncio.create_task(_spotify_playback_monitor())
        logger.info("Spotify playback monitor started")

    yield
    
    # Shutdown heartbeat scheduler
    try:
        from agent.heartbeat import get_heartbeat_manager
        hb = get_heartbeat_manager()
        if hb:
            hb.stop()
    except Exception:
        pass

    # Shutdown Telegram bot
    try:
        from telegram_bot import get_telegram_bot
        tg = get_telegram_bot()
        if tg:
            tg.stop()
    except Exception:
        pass

    # Shutdown Twitch bot
    try:
        from twitch_bot import get_twitch_bot
        twitch = get_twitch_bot()
        if twitch and twitch.is_running:
            await twitch.stop()
    except Exception:
        pass

    # Shutdown Twitter/X bot
    try:
        from twitter_bot import get_twitter_bot
        twitter = get_twitter_bot()
        if twitter and twitter.is_running:
            await twitter.stop()
    except Exception:
        pass

    # Shutdown routine scheduler
    try:
        from agent.routines import get_routine_manager
        get_routine_manager().stop_scheduler()
    except Exception:
        pass

    # Shutdown Spotify playback monitor
    if _spotify_monitor_task and not _spotify_monitor_task.done():
        _spotify_monitor_task.cancel()
        try:
            await _spotify_monitor_task
        except (asyncio.CancelledError, Exception):
            pass

    # Shutdown Discord bot
    try:
        from discord_bot import get_bot, stop_discord_bot

        if get_bot() is not None:
            await stop_discord_bot()
    except Exception:
        pass
    global _discord_bot_task, _discord_bot_token_value
    _discord_bot_task = None
    _discord_bot_token_value = ""
    _gateway_loop = None
    
    logger.info("Shutting down Echo Speak API server...")


app = FastAPI(
    title="Echo Speak API",
    description="Voice AI Assistant API with support for local models",
    version="1.0.0",
    lifespan=lifespan
)

from api.media import router as media_router
from api.media_runtime import router as media_runtime_router

app.include_router(media_router)
app.include_router(media_runtime_router)
# Ensure domain ToolRegistry entries load independently of agent import order.
for _domain_module in ("agent.voice_runtime", "agent.generation_runtime"):
    try:
        __import__(_domain_module)
    except Exception:
        pass

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:5176",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
        "http://127.0.0.1:5176",
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_PUBLIC_AUTH_PATHS = {
    "/",
    "/health",
    "/metrics",
    "/favicon.ico",
    "/.well-known/agent.json",
}


def _is_local_client(host: str) -> bool:
    h = str(host or "").strip().lower()
    return h in {"127.0.0.1", "::1", "localhost"} or h.startswith("127.")


def _configured_api_auth_key() -> str:
    return str(getattr(config, "api_auth_key", "") or os.getenv("API_AUTH_KEY", "") or "").strip()


def _extract_api_auth_key_from_headers(headers: Any) -> str:
    for key in ("x-echospeak-key", "x-api-key", "x-admin-key"):
        val = str(headers.get(key) or "").strip()
        if val:
            return val
    auth = str(headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # Browsers cannot add arbitrary headers to WebSocket handshakes. The
    # desktop bridge therefore supplies the per-launch key as a constrained
    # subprotocol token; it is not placed in a URL or persisted to storage.
    for protocol in str(headers.get("sec-websocket-protocol") or "").split(","):
        value = protocol.strip()
        if value.startswith("echospeak-auth-"):
            return value[len("echospeak-auth-"):].strip()
    return ""


def _api_auth_required_for_host(host: str) -> bool:
    local = _is_local_client(host)
    if not local:
        # Defense in depth for alternate ASGI launchers/proxies that bypass
        # start_server's bind-time check.
        return True
    return bool(
        getattr(config, "api_auth_enabled", False)
        and not bool(getattr(config, "api_auth_localhost_bypass", True))
    )


def _api_auth_ok(headers: Any, host: str) -> bool:
    if not _api_auth_required_for_host(host):
        return True
    if not bool(getattr(config, "api_auth_enabled", False)):
        return False
    expected = _configured_api_auth_key()
    if not expected:
        return False
    provided = _extract_api_auth_key_from_headers(headers)
    return bool(provided and hmac.compare_digest(provided, expected))


def _mcp_trust_summary(
    mcp_servers: Any,
    mcp_client_present: bool,
    mcp_tool_count: int,
    manager_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Summarize MCP availability without overstating configured-only capability.

    Configured ≠ available. Loaded tools come from a live tools/list, not from
    merely having MCP_SERVERS set. Optional manager_status adds per-server errors.
    """
    configured_count = len(mcp_servers) if isinstance(mcp_servers, dict) else 0
    if isinstance(manager_status, dict) and manager_status.get("configured_count") is not None:
        # Prefer live manager count (includes disabled/failed rows from init)
        try:
            configured_count = max(configured_count, int(manager_status.get("configured_count") or 0))
        except Exception:
            pass
    loaded_tools = int(mcp_tool_count or 0)
    if isinstance(manager_status, dict) and manager_status.get("loaded_tool_count") is not None:
        try:
            loaded_tools = max(loaded_tools, int(manager_status.get("loaded_tool_count") or 0))
        except Exception:
            pass
    mcp_available = bool(configured_count and mcp_client_present and loaded_tools > 0)
    warnings: List[str] = []
    if mcp_available:
        status = "available"
    elif configured_count and mcp_client_present and loaded_tools <= 0:
        status = "configured_no_tools"
        warnings.append(
            "MCP servers are configured and the MCP bridge is present, but no MCP tools are loaded yet."
        )
    elif configured_count and not mcp_client_present:
        status = "client_missing"
        warnings.append(
            "MCP servers are configured, but agent.mcp_client.py is missing, so MCP tools cannot load."
        )
    elif mcp_client_present:
        status = "not_configured"
    else:
        status = "not_configured"

    # Surface loud start failures (config ≠ available)
    if isinstance(manager_status, dict):
        for row in manager_status.get("servers") or []:
            if not isinstance(row, dict):
                continue
            err = str(row.get("last_error") or "").strip()
            running = bool(row.get("running"))
            if err and not running:
                name = str(row.get("name") or "server")
                warnings.append(f"MCP server '{name}' failed to load: {err}")
        if manager_status.get("last_error") and loaded_tools <= 0:
            le = str(manager_status.get("last_error") or "").strip()
            if le and not any(le in w for w in warnings):
                warnings.append(f"MCP last error: {le}")

    out: Dict[str, Any] = {
        "mcp_configured_count": configured_count,
        "mcp_tool_count": loaded_tools,
        "mcp_available_tool_count": loaded_tools if mcp_available else 0,
        "mcp_client_present": bool(mcp_client_present),
        "mcp_available": mcp_available,
        "mcp_status": status,
        "warnings": warnings,
        "client_version": (
            str(manager_status.get("client_version") or "official-python-sdk")
            if mcp_client_present and isinstance(manager_status, dict)
            else "official-python-sdk"
            if mcp_client_present
            else ""
        ),
    }
    if isinstance(manager_status, dict):
        out["mcp_running_count"] = int(manager_status.get("running_count") or 0)
        out["mcp_servers_detail"] = manager_status.get("servers") or []
    return out


@app.middleware("http")
async def api_auth_middleware(request: Request, call_next):
    """Optional shared-key auth for network/remote EchoSpeak access."""
    if request.method.upper() == "OPTIONS" or request.url.path in _PUBLIC_AUTH_PATHS:
        return await call_next(request)
    client_ip = _get_client_ip(request)
    if not _api_auth_ok(request.headers, client_ip):
        return Response(
            content='{"detail":"EchoSpeak API auth required."}',
            status_code=401,
            media_type="application/json",
        )
    return await call_next(request)

# Graceful restart support
_restart_requested = False
_restart_lock = threading.Lock()

# Rate limiting
# Interactive desktop use + hydration + dense live harnesses need more headroom
# than 100/min when every poll, state refresh, and mutation shares one IP.
_rate_limit_lock = threading.Lock()
_rate_limits: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_REQUESTS = int(os.getenv("ECHOSPEAK_RATE_LIMIT_REQUESTS", "240") or 240)
RATE_LIMIT_WINDOW = float(os.getenv("ECHOSPEAK_RATE_LIMIT_WINDOW", "60") or 60.0)
# Safe reads / hydration / diagnostics do not consume the mutation budget.
# Mutations and expensive model routes still count.
_RATE_LIMIT_EXEMPT_PREFIXES = (
    "/health",
    "/metrics",
    "/favicon.ico",
    "/gateway/ws",
)
_RATE_LIMIT_SAFE_GET_PREFIXES = (
    "/threads",
    "/pending-action",
    "/approvals",
    "/executions",
    "/traces",
    "/provider",
    "/settings",
    "/memory",
    "/projects",
    "/history",
    "/soul",
)


def _get_client_ip(request: Request) -> str:
    """Extract client IP, trusting proxy headers only when explicitly configured."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded and bool(getattr(config, "api_trust_proxy_headers", False)):
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_exempt(path: str, method: str) -> bool:
    """Return True only for inexpensive, read-only, non-model routes.

    Never exempt:
    - POST/PUT/PATCH/DELETE (mutations, model calls, workers)
    - /query, /approvals/*/confirm
    - rebuild/compact endpoints
    """
    p = str(path or "")
    m = str(method or "GET").upper()
    if p in {"/health", "/metrics", "/favicon.ico"} or p.startswith("/gateway/"):
        return True
    if m != "GET":
        return False
    if p.startswith("/query") or "/confirm" in p or p.endswith("/rebuild-index") or p.endswith("/compact"):
        return False
    if any(p == pref.rstrip("/") or p.startswith(pref) for pref in _RATE_LIMIT_SAFE_GET_PREFIXES):
        return True
    return False


def _check_rate_limit(client_ip: str) -> tuple[bool, int]:
    """Check if client is within rate limit. Returns (allowed, remaining)."""
    now = time.time()
    with _rate_limit_lock:
        # Clean old entries
        _rate_limits[client_ip] = [
            t for t in _rate_limits[client_ip] if now - t < RATE_LIMIT_WINDOW
        ]
        current_count = len(_rate_limits[client_ip])
        if current_count >= RATE_LIMIT_REQUESTS:
            return False, 0
        _rate_limits[client_ip].append(now)
        return True, RATE_LIMIT_REQUESTS - current_count - 1


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limit requests per client IP.

    Safe GET hydration paths are exempt so a completed mutation is never
    misreported as failed solely because a follow-up state read was limited.
    Mutations still count and must not be blindly retried by clients.
    """
    path = request.url.path
    method = request.method
    if _rate_limit_exempt(path, method):
        response = await call_next(request)
        response.headers["X-RateLimit-Policy"] = "safe-get-exempt"
        return response

    client_ip = _get_client_ip(request)
    allowed, remaining = _check_rate_limit(client_ip)

    if not allowed:
        retry_after = max(1, int(RATE_LIMIT_WINDOW))
        return Response(
            content='{"detail":"Rate limit exceeded. Try again later.","code":"rate_limited"}',
            status_code=429,
            media_type="application/json",
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Remaining": "0",
            },
        )

    response = await call_next(request)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


@app.middleware("http")
async def check_graceful_restart(request: Request, call_next):
    """Middleware to handle graceful restart after request completes."""
    global _restart_requested
    response = await call_next(request)
    
    with _restart_lock:
        if _restart_requested:
            logger.info("Graceful restart requested - exiting after response")
            # Use os._exit for immediate termination
            # External process manager (systemd, docker, uvicorn --reload) will restart
            # Small delay to ensure response is sent
            def _do_exit():
                time.sleep(0.5)
                logger.info("Exiting for restart...")
                os._exit(0)
            threading.Thread(target=_do_exit, daemon=True).start()
    
    return response


class RestartRequest(BaseModel):
    """Request model for restart endpoint."""
    delay_seconds: int = Field(default=1, description="Seconds to wait before restart")


class RestartResponse(BaseModel):
    """Response model for restart endpoint."""
    message: str
    restart_scheduled: bool


def _get_admin_api_key() -> str:
    """Get admin API key from environment or generate one."""
    key = os.getenv("ADMIN_API_KEY", "").strip()
    if not key:
        # Generate a random key if not set
        import secrets
        key = secrets.token_hex(16)
        logger.warning("ADMIN_API_KEY not set. A random key has been generated. Set ADMIN_API_KEY in .env for production.")
        logger.debug(f"Generated admin key: {key}")
    return key


_ADMIN_API_KEY = None


def _verify_admin_key(api_key: str = Header(None, alias="X-Admin-Key")) -> str:
    """Dependency to verify admin API key."""
    global _ADMIN_API_KEY
    if _ADMIN_API_KEY is None:
        _ADMIN_API_KEY = _get_admin_api_key()
    
    if not api_key or api_key != _ADMIN_API_KEY:
        raise HTTPException(
            status_code=403,
            detail="Admin access required. Provide X-Admin-Key header."
        )
    return api_key


@app.post("/admin/restart", response_model=RestartResponse)
async def request_restart(
    req: RestartRequest = RestartRequest(),
    _: str = Depends(_verify_admin_key)
):
    """Schedule a graceful restart after current request completes.
    
    Requires X-Admin-Key header with ADMIN_API_KEY from environment.
    The server will exit after completing the current response,
    and an external process manager (systemd, docker, uvicorn) will restart it.
    """
    global _restart_requested
    
    with _restart_lock:
        if _restart_requested:
            return RestartResponse(message="Restart already scheduled", restart_scheduled=True)
        _restart_requested = True
    
    logger.info(f"Restart scheduled in {req.delay_seconds}s")
    return RestartResponse(
        message=f"Restart scheduled. Server will restart after current request completes.",
        restart_scheduled=True
    )


@app.get("/admin/restart/status", response_model=RestartResponse)
async def get_restart_status(_: str = Depends(_verify_admin_key)):
    """Check if a restart is pending. Requires admin auth."""
    global _restart_requested
    with _restart_lock:
        return RestartResponse(
            message="Restart pending" if _restart_requested else "No restart scheduled",
            restart_scheduled=_restart_requested
        )


def _cancel_incompatible_session_work(session_id: str, *, reason: str) -> dict[str, int]:
    """Terminalize only work bound to the Session's previous model revision."""
    from agent.task_runs import TERMINAL_TASK_STATUSES, TaskRunStatus, get_task_run_store

    key = _normalize_thread_id(session_id)
    approval_count = 0
    for approval in get_state_store().list_approvals(thread_id=key, limit=1000):
        if approval.status not in {"pending", "consuming"}:
            continue
        get_state_store().update_approval(
            approval.id, status="canceled", outcome_summary=reason
        )
        approval_count += 1
    task_count = 0
    store = get_task_run_store()
    for task in store.list_for_session(key, include_terminal=False):
        if task.status in TERMINAL_TASK_STATUSES:
            continue
        try:
            store.update(
                task.id,
                session_id=task.session_id,
                project_id=task.project_id,
                expected_revision=task.revision,
                status=TaskRunStatus.CANCELLED,
                workflow_stage="cancelled:model_binding_changed",
                last_execution_id=task.last_execution_id,
            )
            task_count += 1
        except Exception as exc:
            logger.warning("Model-binding cancellation raced for TaskRun {}: {}", task.id, exc)
    state = get_state_store().get_thread_state(key)
    get_state_store().update_thread_state(
        key,
        foreground_task_id="",
        suspended_task_ids=[],
        pending_approval_id="",
        pending_actions=[],
        execution_status="cancelled",
        safest_next_action="Start new work under the selected Session model",
        source_metadata={
            **dict(state.source_metadata or {}),
            "model_binding_cancellation_reason": reason,
            "model_binding_cancelled_at": time.time(),
        },
    )
    return {"approvals": approval_count, "task_runs": task_count}


class QueryRequest(BaseModel):
    """Request model for query endpoint."""
    message: str = Field(..., description="User message to process", max_length=50000)
    include_memory: bool = Field(default=True, description="Include conversation memory")
    thread_id: Optional[str] = Field(default=None, description="Conversation Session id")
    workspace: Optional[str] = Field(default=None, description="Optional workspace/mode override (ex: auto|chat|coding|research)")
    thinking_enabled: bool = Field(
        default=True,
        description="Request provider-native reasoning controls when the selected provider supports them",
    )
    reasoning_effort: Literal[
        "minimal", "low", "medium", "high", "extra_high", "max", "ultra"
    ] = Field(default="medium")
    client_request_id: Optional[str] = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="Client-owned cancellation identity for this exact Turn",
    )
    transport: Literal["chat", "voice"] = Field(
        default="chat",
        description="User-input transport only; it never changes semantic or execution authority",
    )
    voice_turn_id: Optional[str] = Field(
        default=None,
        max_length=200,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="Durable local Voice transport turn whose final transcript equals message",
    )


class QueryResponse(BaseModel):
    """Response model for query endpoint."""
    response: str
    success: bool
    memory_count: int
    request_id: Optional[str] = None
    doc_sources: Optional[list] = None
    research: Optional[list[dict[str, Any]]] = None
    execution_id: Optional[str] = None
    trace_id: Optional[str] = None
    thread_state: Optional[dict[str, Any]] = None
    voice_turn_id: Optional[str] = None


def _prepare_query_transport(request: QueryRequest, request_id: str) -> str:
    """Validate user-input transport without creating another intent router."""

    voice_turn_id = str(request.voice_turn_id or "").strip()
    if request.transport != "voice":
        if voice_turn_id:
            raise HTTPException(status_code=400, detail="voice_turn_id requires the Voice transport")
        return "web"
    if not voice_turn_id:
        raise HTTPException(status_code=400, detail="Voice transport requires a durable voice_turn_id")
    try:
        from agent.voice_transport import prepare_voice_turn_submission

        prepare_voice_turn_submission(
            voice_turn_id,
            session_id=_normalize_thread_id(request.thread_id),
            request_id=request_id,
            transcript=request.message,
        )
    except Exception as exc:
        from agent.voice_transport import VoiceTransportError

        if isinstance(exc, VoiceTransportError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise
    return "voice"


class QueryCancelRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    thread_id: str = Field(min_length=1, max_length=200)
    execution_id: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9._:-]*$")
    voice_turn_id: Optional[str] = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    voice_transcript: Optional[str] = Field(default=None, max_length=10000)


class QuerySteerRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=10000)
    task_run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    client_request_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    voice_turn_id: Optional[str] = Field(
        default=None,
        max_length=200,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="Optional exact Voice transport turn containing this steering instruction",
    )


class QueryQueueRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=50000)
    client_request_id: Optional[str] = Field(default=None)


_ACTIVE_QUERY_CANCEL_LOCK = threading.RLock()
_ACTIVE_QUERY_CANCELLATIONS: Dict[str, tuple[str, threading.Event, str]] = {}


def _register_query_cancellation(request_id: str, thread_id: str, event: threading.Event) -> None:
    with _ACTIVE_QUERY_CANCEL_LOCK:
        _ACTIVE_QUERY_CANCELLATIONS[str(request_id)] = (_normalize_thread_id(thread_id), event, "")


def _bind_query_execution(request_id: str, execution_id: str) -> None:
    rid = str(request_id or "").strip()
    eid = str(execution_id or "").strip()
    if not rid or not eid:
        return
    with _ACTIVE_QUERY_CANCEL_LOCK:
        current = _ACTIVE_QUERY_CANCELLATIONS.get(rid)
        if current is not None:
            _ACTIVE_QUERY_CANCELLATIONS[rid] = (current[0], current[1], eid)


def _release_query_cancellation(request_id: str, event: threading.Event) -> None:
    with _ACTIVE_QUERY_CANCEL_LOCK:
        current = _ACTIVE_QUERY_CANCELLATIONS.get(str(request_id))
        if current is not None and current[1] is event:
            _ACTIVE_QUERY_CANCELLATIONS.pop(str(request_id), None)


def _cancel_all_active_queries() -> int:
    """Signal every registered query without changing its ownership record."""
    with _ACTIVE_QUERY_CANCEL_LOCK:
        active = list(_ACTIVE_QUERY_CANCELLATIONS.values())
    for _session_id, event, _execution_id in active:
        event.set()
    return len(active)


def _cancel_active_queries_for_session(session_id: str) -> int:
    """Signal only queries owned by one exact Session."""
    key = _normalize_thread_id(session_id)
    with _ACTIVE_QUERY_CANCEL_LOCK:
        active = [
            row
            for row in _ACTIVE_QUERY_CANCELLATIONS.values()
            if row[0] == key
        ]
    for _owner, event, _execution_id in active:
        event.set()
    return len(active)


@app.post("/query/steer")
async def steer_query(request: QuerySteerRequest):
    """Steer an ongoing TaskRun with a new instruction without losing progress."""
    from agent.task_runs import get_task_run_store, TaskRunStatus

    thread_id = _normalize_thread_id(request.thread_id)
    with _ACTIVE_QUERY_CANCEL_LOCK:
        active = _ACTIVE_QUERY_CANCELLATIONS.get(request.client_request_id)
    if active is None or active[0] != thread_id:
        raise HTTPException(status_code=409, detail="The requested Turn is no longer active in this Session.")
    execution_id = str(active[2] or "")
    if not execution_id:
        raise HTTPException(status_code=409, detail="The active Turn has not bound a durable Execution yet.")
    execution = get_state_store().get_execution(execution_id)
    if execution is None or str(execution.task_run_id or "") != request.task_run_id:
        raise HTTPException(status_code=409, detail="Steering identity does not match the active TaskRun.")

    store = get_task_run_store()
    task = store.get(request.task_run_id, session_id=thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="The active TaskRun no longer exists.")
    if task.status != TaskRunStatus.RUNNING or str(task.last_execution_id or task.created_by_execution_id) != execution_id:
        raise HTTPException(status_code=409, detail="The TaskRun is not owned by this active Execution.")

    if request.voice_turn_id:
        try:
            from agent.voice_transport import prepare_voice_turn_submission

            prepare_voice_turn_submission(
                request.voice_turn_id,
                session_id=thread_id,
                request_id=request.client_request_id,
                transcript=request.instruction,
            )
        except Exception as exc:
            from agent.voice_transport import VoiceTransportError

            if isinstance(exc, VoiceTransportError):
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            raise

    task.steer(request.instruction)
    updated = store.update(
        task.id,
        session_id=thread_id,
        project_id=task.project_id,
        expected_revision=task.revision - 1,
        steering_instructions=task.steering_instructions,
        status=task.status,
        workflow_stage="steered_at_next_model_boundary",
    )

    if request.voice_turn_id:
        from agent.voice_transport import bind_voice_turn_submission

        bind_voice_turn_submission(
            request.voice_turn_id,
            session_id=thread_id,
            request_id=request.client_request_id,
            execution_id=execution_id,
            task_run_id=updated.id,
            query_completed=True,
        )

    logger.info("Steered TaskRun id={} session={} revision={}", updated.id, thread_id, updated.revision)
    return {
        "steered": True,
        "task_run_id": updated.id,
        "revision": updated.revision,
        "applies_at": "next_model_boundary",
    }


@app.post("/query/queue")
async def queue_query(request: QueryQueueRequest):
    """Queue a follow-up prompt to run after the active turn finishes."""
    thread_id = _normalize_thread_id(request.thread_id)
    store = get_state_store()
    item = store.enqueue_turn(
        thread_id,
        message=request.message,
        client_request_id=request.client_request_id or str(uuid.uuid4()),
    )
    queue_length = len(store.list_queued_turns(thread_id))
    return {
        "queued": True,
        "thread_id": thread_id,
        "queue_length": queue_length,
        "client_request_id": item["client_request_id"],
    }


@app.get("/query/queue")
async def get_queued_queries(thread_id: str):
    """List pending queued turns for a Session."""
    key = _normalize_thread_id(thread_id)
    items = get_state_store().list_queued_turns(key)
    return {"thread_id": key, "queue": items}


@app.post("/query/queue/claim")
async def claim_queued_query(thread_id: str):
    """Claim one durable follow-up only after the Session has no active Turn."""
    key = _normalize_thread_id(thread_id)
    with _ACTIVE_QUERY_CANCEL_LOCK:
        session_active = any(owner == key for owner, _event, _execution in _ACTIVE_QUERY_CANCELLATIONS.values())
    if session_active:
        raise HTTPException(status_code=409, detail="This Session still has an active Turn.")
    item = get_state_store().claim_queued_turn(key)
    return {
        "thread_id": key,
        "claimed": item is not None,
        "item": item,
        "queue_length": len(get_state_store().list_queued_turns(key)),
    }


@app.post("/query/cancel")
async def cancel_query(request: QueryCancelRequest):
    """Cancel one exact request; never infer by newest Session activity."""
    with _ACTIVE_QUERY_CANCEL_LOCK:
        active = _ACTIVE_QUERY_CANCELLATIONS.get(request.request_id)
    if active is None:
        return {"cancelled": False, "request_id": request.request_id, "reason": "not_active"}
    owner_session, event, execution_id = active
    if owner_session != _normalize_thread_id(request.thread_id):
        raise HTTPException(status_code=409, detail="Cancellation request belongs to a different Session")
    if request.execution_id and execution_id and request.execution_id != execution_id:
        raise HTTPException(status_code=409, detail="Cancellation request belongs to a different Execution")
    if request.voice_turn_id:
        if not str(request.voice_transcript or "").strip():
            raise HTTPException(status_code=400, detail="Voice cancellation requires its exact final transcript")
        try:
            from agent.voice_transport import (
                bind_voice_turn_submission,
                cancel_voice_playback,
                prepare_voice_turn_submission,
            )

            prepare_voice_turn_submission(
                request.voice_turn_id,
                session_id=owner_session,
                request_id=request.request_id,
                transcript=str(request.voice_transcript or ""),
            )
            if execution_id:
                execution = get_state_store().get_execution(execution_id)
                if execution is None or execution.session_id != owner_session:
                    raise HTTPException(status_code=409, detail="Voice cancellation Execution is unavailable")
                bind_voice_turn_submission(
                    request.voice_turn_id,
                    session_id=owner_session,
                    request_id=request.request_id,
                    execution_id=execution.id,
                    task_run_id=str(execution.task_run_id or ""),
                    query_completed=True,
                )
            cancel_voice_playback(request.voice_turn_id, session_id=owner_session)
        except HTTPException:
            raise
        except Exception as exc:
            from agent.voice_transport import VoiceTransportError

            if isinstance(exc, VoiceTransportError):
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            raise
    event.set()
    return {
        "cancelled": True,
        "request_id": request.request_id,
        "execution_id": execution_id,
        "thread_id": owner_session,
    }

class ThreadSessionStateResponse(BaseModel):
    thread_id: str
    session_id: str = ""
    title: str = ""
    workspace_id: str = ""
    active_project_id: str = ""
    workspace_root: str = ""
    project_path: str = ""
    foreground_task_id: str = ""
    suspended_task_ids: List[str] = Field(default_factory=list)
    pending_approval_ids: List[str] = Field(default_factory=list)
    source_metadata: Dict[str, Any] = Field(default_factory=dict)
    semantic_schema_version: int = 0
    semantic_state_migrated_at: float = 0.0
    objective: str = ""
    current_subject: str = ""
    mode: str = "chat"
    phase: str = ""
    required_capabilities: List[str] = Field(default_factory=list)
    available_capabilities: List[str] = Field(default_factory=list)
    allowed_tool_names: List[str] = Field(default_factory=list)
    permissions: Dict[str, bool] = Field(default_factory=dict)
    constraints: List[str] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)
    completed_actions: List[Dict[str, Any]] = Field(default_factory=list)
    pending_actions: List[Dict[str, Any]] = Field(default_factory=list)
    failed_actions: List[Dict[str, Any]] = Field(default_factory=list)
    plan_steps: List[Dict[str, Any]] = Field(default_factory=list)
    retry_target: Dict[str, Any] = Field(default_factory=dict)
    last_tool_outcome: Dict[str, Any] = Field(default_factory=dict)
    operation_details: Dict[str, Any] = Field(default_factory=dict)
    continuity_notice: str = ""
    execution_status: str = "ready"
    safest_next_action: str = ""
    current_execution_id: str = ""
    active_turn_id: str = ""
    selected_model_id: str = ""
    model_binding: Optional[Dict[str, Any]] = None
    model_profile: Dict[str, Any] = Field(default_factory=dict)
    context_budget: Dict[str, Any] = Field(default_factory=dict)
    unfinished_workflow: Dict[str, Any] = Field(default_factory=dict)
    pending_approval_id: str = ""
    last_execution_id: str = ""
    last_trace_id: str = ""
    runtime_provider: str = ""
    ledger: List[Dict[str, Any]] = Field(default_factory=list)
    updated_at: float = 0.0


class TaskRunSummaryResponse(BaseModel):
    id: str
    project_id: str = ""
    session_id: str
    objective: str
    status: str
    workflow_stage: str
    execution_profile: str
    parent_task_run_id: str = ""
    handoff_context_id: str = ""
    requirement_statuses: Dict[str, str] = Field(default_factory=dict)
    active_graph_node_ids: List[str] = Field(default_factory=list)
    completion_finalizable: bool = False
    completion_disposition: str = "pending"
    next_runtime_action: str = ""
    active_requirement_id: str = ""
    preferred_tool_name: str = ""
    recovery_epoch: int = 0
    revision: int
    updated_at: float


class TaskRunHandoffRequest(BaseModel):
    session_id: str
    project_id: str = ""
    execution_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    target_profile: Literal["chat", "work", "code"]
    objective: str = ""
    expected_model_binding_revision: int = Field(ge=1)


class TaskRunDetailResponse(BaseModel):
    task: Dict[str, Any]
    requirements: List[Dict[str, Any]] = Field(default_factory=list)
    requirement_states: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    completion: Optional[Dict[str, Any]] = None
    stage: Dict[str, Any] = Field(default_factory=dict)
    approvals: List[Dict[str, Any]] = Field(default_factory=list)
    executions: List[Dict[str, Any]] = Field(default_factory=list)
    tool_runs: List[Dict[str, Any]] = Field(default_factory=list)
    research_artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    media_jobs: List[Dict[str, Any]] = Field(default_factory=list)
    specialist_runs: List[Dict[str, Any]] = Field(default_factory=list)


def _task_run_summary(task: Any) -> TaskRunSummaryResponse:
    verdict = getattr(task, "completion_evaluation", None)
    liveness = getattr(task, "liveness_decision", None)
    graph_state = getattr(task, "execution_graph_state", None)
    return TaskRunSummaryResponse(
        id=task.id,
        project_id=str(task.project_id or ""),
        session_id=task.session_id,
        objective=task.objective,
        status=str(getattr(task.status, "value", task.status)),
        workflow_stage=task.workflow_stage,
        execution_profile=str(getattr(task.execution_profile, "value", task.execution_profile)),
        parent_task_run_id=str(task.parent_task_run_id or ""),
        handoff_context_id=str(task.handoff_context_id or ""),
        requirement_statuses={
            key: str(getattr(value.status, "value", value.status))
            for key, value in task.requirement_states.items()
        },
        active_graph_node_ids=list(getattr(graph_state, "active_node_ids", None) or []),
        completion_finalizable=bool(verdict and verdict.finalizable),
        completion_disposition=str(
            getattr(getattr(verdict, "disposition", None), "value", "") or "pending"
        ),
        next_runtime_action=str(
            getattr(getattr(liveness, "next_action", None), "value", "") or ""
        ),
        active_requirement_id=str(
            getattr(liveness, "active_requirement_id", "") or ""
        ),
        preferred_tool_name=str(
            getattr(liveness, "preferred_tool_name", "") or ""
        ),
        recovery_epoch=int(getattr(task, "recovery_epoch", 0) or 0),
        revision=task.revision,
        updated_at=task.updated_at,
    )


@app.get("/task-runs", response_model=List[TaskRunSummaryResponse])
async def list_task_runs(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
    include_terminal: bool = Query(default=False),
):
    """List canonical TaskRuns in one exact Session/Project scope."""

    from agent.task_runs import get_task_run_store
    from agent.threads import get_thread_manager

    if get_thread_manager().get_thread(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    state = get_state_store().get_thread_state(session_id)
    if str(state.active_project_id or "") != str(project_id or ""):
        raise HTTPException(status_code=409, detail="Session is not bound to the requested Project")
    rows = get_task_run_store().list_for_session(
        session_id,
        project_id=project_id,
        include_terminal=include_terminal,
    )
    return [_task_run_summary(item) for item in rows]


@app.get("/task-runs/{task_run_id}", response_model=TaskRunDetailResponse)
async def get_task_run_detail(
    task_run_id: str,
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Bounded, read-only projection of one canonical TaskRun owner."""

    from agent.task_runs import TaskRunScopeError, get_task_run_store

    state = get_state_store().get_thread_state(session_id)
    if str(state.active_project_id or "") != str(project_id or ""):
        raise HTTPException(status_code=409, detail="Session is not bound to the requested Project")
    try:
        task = get_task_run_store().get(
            task_run_id, session_id=session_id, project_id=project_id
        )
    except TaskRunScopeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="TaskRun not found")

    executions = [
        row for row in get_state_store().list_executions(session_id, limit=200)
        if row.task_run_id == task.id
    ]
    execution_ids = {row.id for row in executions}
    tool_runs = [
        row for row in get_state_store().list_tool_runs_for_session(session_id, limit=240)
        if row.id in set(task.tool_run_ids) or row.turn_id in execution_ids
    ]
    approvals = [
        row for row in get_state_store().list_approvals(thread_id=session_id, limit=200)
        if row.task_run_id == task.id
    ]
    artifacts: list[dict[str, Any]] = []
    try:
        from agent.research_artifacts import get_research_artifact_for_scope
        for artifact_id in task.research_artifact_ids[:80]:
            artifact = get_research_artifact_for_scope(
                artifact_id, project_id=project_id, session_id=session_id
            )
            if artifact is not None:
                artifacts.append(artifact.model_dump(mode="json"))
    except Exception as exc:
        logger.warning("TaskRun research projection unavailable: {}", exc)
    media_jobs: list[dict[str, Any]] = []
    try:
        from agent.generation_runtime import get_generation_job_store
        from agent.media_jobs import project_generation_job, project_voice_job
        from agent.voice_runtime import get_voice_job_store
        projected = [
            *(project_generation_job(row) for row in get_generation_job_store().list(session_id=session_id, limit=100)),
            *(project_voice_job(row) for row in get_voice_job_store().list(session_id=session_id, limit=100)),
        ]
        media_jobs = [
            row.model_dump(mode="json") for row in projected
            if row.task_run_id == task.id
        ][:80]
    except Exception:
        media_jobs = []
    specialist_runs: list[dict[str, Any]] = []
    try:
        from agent.specialist_store import get_specialist_run_store
        specialist_runs = [
            row.model_dump(mode="json")
            for row in get_specialist_run_store().list(
                session_id=session_id,
                project_id=project_id,
                task_run_id=task.id,
                limit=100,
            )
        ]
    except Exception as exc:
        logger.warning("TaskRun specialist projection unavailable: {}", exc)
    graph_state = task.execution_graph_state
    graph = task.execution_graph
    node_states = dict(getattr(graph_state, "node_states", None) or {})
    return TaskRunDetailResponse(
        task={
            **_task_run_summary(task).model_dump(mode="json"),
            "requested_operation": task.requested_operation,
            "missing_inputs": list(task.missing_inputs),
            "created_at": task.created_at,
            "created_by_execution_id": task.created_by_execution_id,
            "last_execution_id": task.last_execution_id,
            "trigger_occurrence_id": task.trigger_occurrence_id,
            "research_depth": str(getattr(task.research_depth, "value", task.research_depth or "")),
            "recovery_epoch_started_at": float(
                task.recovery_epoch_started_at or 0.0
            ),
            "recovery_history": list(task.recovery_history or []),
            "liveness": (
                task.liveness_decision.model_dump(mode="json")
                if task.liveness_decision else None
            ),
        },
        requirements=[item.model_dump(mode="json") for item in task.requirements],
        requirement_states={
            key: value.model_dump(mode="json")
            for key, value in task.requirement_states.items()
        },
        completion=(task.completion_evaluation.model_dump(mode="json") if task.completion_evaluation else None),
        stage={
            "workflow_stage": task.workflow_stage,
            "graph_id": str(getattr(graph, "graph_id", "") or ""),
            "source": str(getattr(getattr(graph, "source", None), "value", getattr(graph, "source", "")) or ""),
            "active_node_ids": list(getattr(graph_state, "active_node_ids", None) or []),
            "checkpoint_count": len(list(getattr(graph_state, "checkpoints", None) or [])),
            "nodes": [
                {
                    "node_id": node.node_id,
                    "kind": str(getattr(node.kind, "value", node.kind)),
                    "label": node.label,
                    "requirement_id": node.requirement_id,
                    "depends_on": list(node.depends_on),
                    "status": str(
                        getattr(
                            getattr(node_states.get(node.node_id), "status", "pending"),
                            "value",
                            getattr(node_states.get(node.node_id), "status", "pending"),
                        )
                    ),
                    "attempt_count": int(
                        getattr(node_states.get(node.node_id), "attempt_count", 0) or 0
                    ),
                    "outcome_code": str(
                        getattr(node_states.get(node.node_id), "outcome_code", "") or ""
                    ),
                }
                for node in list(getattr(graph, "nodes", None) or [])[:128]
            ],
            "edges": [
                {
                    "source_node_id": edge.source_node_id,
                    "target_node_id": edge.target_node_id,
                    "kind": str(getattr(edge.kind, "value", edge.kind)),
                }
                for edge in list(getattr(graph, "edges", None) or [])[:512]
            ],
        },
        approvals=[row.model_dump(mode="json") for row in approvals],
        executions=[row.model_dump(mode="json") for row in executions],
        tool_runs=[row.model_dump(mode="json") for row in tool_runs],
        research_artifacts=artifacts,
        media_jobs=media_jobs,
        specialist_runs=specialist_runs,
    )


@app.post("/task-runs/{task_run_id}/handoff", response_model=TaskRunSummaryResponse)
async def handoff_task_run(task_run_id: str, request: TaskRunHandoffRequest):
    """Explicitly hand current work to another surface without creating a Session."""

    from agent.execution_graph import ExecutionProfile
    from agent.task_runs import TaskRunConflictError, get_task_run_store
    from agent.threads import get_thread_manager

    if get_thread_manager().get_thread(request.session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    state = get_state_store().get_thread_state(request.session_id)
    if str(state.active_project_id or "") != str(request.project_id or ""):
        raise HTTPException(status_code=409, detail="Session Project changed before handoff")
    binding = _ensure_session_model_binding(request.session_id)
    if binding.binding_revision != request.expected_model_binding_revision:
        raise HTTPException(status_code=409, detail="Session model binding changed before handoff")
    execution = get_state_store().get_execution(request.execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Handoff Execution not found")
    if execution.session_id != request.session_id or execution.thread_id != request.session_id:
        raise HTTPException(status_code=409, detail="Handoff Execution belongs to another Session")
    if str(execution.project_id or execution.active_project_id or "") != str(request.project_id or ""):
        raise HTTPException(status_code=409, detail="Handoff Execution belongs to another Project")
    execution_id = execution.id
    try:
        _previous, replacement = get_task_run_store().handoff_to_profile(
            task_run_id,
            session_id=request.session_id,
            project_id=request.project_id,
            expected_revision=request.expected_revision,
            execution_id=execution_id,
            target_profile=ExecutionProfile(request.target_profile),
            objective=request.objective,
        )
    except TaskRunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    get_state_store().update_thread_state(
        request.session_id,
        foreground_task_id=replacement.id,
        source_metadata={
            **dict(state.source_metadata or {}),
            "last_surface_handoff": request.target_profile,
            "handoff_task_run_id": replacement.id,
            "updated_at": time.time(),
        },
    )
    return _task_run_summary(replacement)


class ApprovalResponse(BaseModel):
    id: str
    thread_id: str
    session_id: str = "default"
    project_id: str = ""
    original_turn_id: str = ""
    tool_run_id: str = ""
    execution_id: Optional[str] = None
    task_run_id: str = ""
    requirement_id: str = ""
    attempt_id: str = ""
    task_run_revision: int = 0
    model_binding_revision: int = 0
    status: str
    tool: str
    kwargs: Dict[str, Any] = Field(default_factory=dict)
    original_input: str = ""
    preview: str = ""
    summary: str = ""
    risk_level: str = "safe"
    policy_flags: List[str] = Field(default_factory=list)
    permission_level: str = "modify"
    constraints: List[str] = Field(default_factory=list)
    policy_snapshot: Dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0
    execution_context: Dict[str, Any] = Field(default_factory=dict)
    action_id: str = ""
    plan_id: str = ""
    canonical_arguments_hash: str = ""
    required_capabilities: List[str] = Field(default_factory=list)
    session_permissions: Dict[str, bool] = Field(default_factory=dict)
    dry_run_available: bool = False
    source: str = "web"
    workspace_id: str = ""
    active_project_id: str = ""
    created_at: float
    updated_at: float
    decided_at: Optional[float] = None
    outcome_summary: str = ""


class ApprovalListResponse(BaseModel):
    items: List[ApprovalResponse]
    count: int


class ApprovalDecisionResponse(BaseModel):
    approval: ApprovalResponse
    success: bool
    response: str = ""
    execution_id: Optional[str] = None
    thread_state: Dict[str, Any] = Field(default_factory=dict)
    task_run: Optional[TaskRunSummaryResponse] = None
    tool_run_id: str = ""


class ExecutionResponse(BaseModel):
    id: str
    request_id: str
    kind: str
    thread_id: str
    session_id: str = "default"
    project_id: str = ""
    source: str
    status: str
    query: str
    workspace_id: str = ""
    active_project_id: str = ""
    runtime_provider: str = ""
    model_id: str = ""
    model_snapshot: Dict[str, Any] = Field(default_factory=dict)
    context_budget: Dict[str, Any] = Field(default_factory=dict)
    intent: str = ""
    mode: str = "chat"
    phase: str = ""
    constraints: List[str] = Field(default_factory=list)
    verification: Dict[str, Any] = Field(default_factory=dict)
    terminal_status: str = "started"
    created_at: float
    updated_at: float
    completed_at: Optional[float] = None
    success: Optional[bool] = None
    response_preview: str = ""
    error: str = ""
    approvals: List[str] = Field(default_factory=list)
    tools_used: List[str] = Field(default_factory=list)
    tool_latencies_ms: List[Dict[str, Any]] = Field(default_factory=list)
    trace_id: Optional[str] = None
    evaluation: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExecutionListResponse(BaseModel):
    items: List[ExecutionResponse]
    count: int


class SettingsResponse(BaseModel):
    settings: Dict[str, Any]
    overrides: Dict[str, Any]
    issues: List[Dict[str, Any]] = Field(default_factory=list)


class SettingsTestRequest(BaseModel):
    target: str = Field(..., description="openai | gemini | local | ollama | openai_compat")
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class SettingsTestResponse(BaseModel):
    ok: bool
    target: str
    message: str
    latency_ms: Optional[float] = None


@app.get("/settings", response_model=SettingsResponse)
async def get_settings():
    """Return the effective settings (redacted) and the current override patch."""
    s = config.to_public_dict()
    overrides = _redact_settings_payload(_sanitize_incoming_settings(_read_runtime_settings()))
    return SettingsResponse(settings=s, overrides=overrides, issues=_validate_settings_effective(s))


def _http_get_json(url: str, headers: Optional[dict] = None, timeout_s: float = 6.0) -> tuple[int, Any]:
    req = UrlRequest(url, headers=headers or {}, method="GET")
    with urlopen(req, timeout=timeout_s) as resp:
        code = int(getattr(resp, "status", 200) or 200)
        raw = resp.read().decode("utf-8", errors="ignore")
        try:
            return code, json.loads(raw) if raw.strip() else {}
        except Exception:
            return code, {"raw": raw[:2000]}


def _http_post_json(url: str, payload: dict, headers: Optional[dict] = None, timeout_s: float = 6.0) -> tuple[int, Any]:
    body = json.dumps(payload or {}).encode("utf-8")
    req_headers = {"Content-Type": "application/json", **(headers or {})}
    req = UrlRequest(url, headers=req_headers, data=body, method="POST")
    with urlopen(req, timeout=timeout_s) as resp:
        code = int(getattr(resp, "status", 200) or 200)
        raw = resp.read().decode("utf-8", errors="ignore")
        try:
            return code, json.loads(raw) if raw.strip() else {}
        except Exception:
            return code, {"raw": raw[:2000]}


def _normalize_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    return u


@app.post("/settings/test", response_model=SettingsTestResponse)
def settings_test(request: SettingsTestRequest):
    target = (request.target or "").strip().lower()
    base_url = (request.base_url or "").strip() or None
    api_key = (request.api_key or "").strip() or None

    started = time.perf_counter()
    try:
        if target == "openai":
            key = api_key or (getattr(getattr(config, "openai", None), "api_key", "") or "").strip()
            if not key or key == "***":
                return SettingsTestResponse(ok=False, target=target, message="Missing OpenAI API key.")
            url = "https://api.openai.com/v1/models"
            code, _ = _http_get_json(url, headers={"Authorization": f"Bearer {key}"}, timeout_s=6.0)
            ok = 200 <= code < 300
            ms = (time.perf_counter() - started) * 1000.0
            return SettingsTestResponse(ok=ok, target=target, message=f"HTTP {code}", latency_ms=ms)

        if target == "gemini":
            key = api_key or (getattr(getattr(config, "gemini", None), "api_key", "") or "").strip()
            if not key or key == "***":
                return SettingsTestResponse(ok=False, target=target, message="Missing Gemini API key.")
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
            code, data = _http_get_json(url, timeout_s=6.0)
            ok = 200 <= code < 300
            ms = (time.perf_counter() - started) * 1000.0
            if ok:
                count = 0
                try:
                    count = len((data or {}).get("models") or [])
                except Exception:
                    count = 0
                return SettingsTestResponse(ok=True, target=target, message=f"OK (models={count})", latency_ms=ms)
            return SettingsTestResponse(ok=False, target=target, message=f"HTTP {code}", latency_ms=ms)

        if target in {"local", "openai_compat"}:
            url0 = base_url or (getattr(getattr(config, "local", None), "base_url", "") or "").strip()
            url0 = _normalize_base_url(url0)
            if not url0:
                return SettingsTestResponse(ok=False, target=target, message="Missing local base URL.")
            url = f"{url0}/v1/models"
            code, data = _http_get_json(url, timeout_s=5.0)
            ok = 200 <= code < 300
            ms = (time.perf_counter() - started) * 1000.0
            if ok:
                count = 0
                try:
                    count = len((data or {}).get("data") or [])
                except Exception:
                    count = 0
                return SettingsTestResponse(ok=True, target=target, message=f"OK (models={count})", latency_ms=ms)
            return SettingsTestResponse(ok=False, target=target, message=f"HTTP {code}", latency_ms=ms)

        if target == "ollama":
            url0 = base_url or (getattr(getattr(config, "local", None), "base_url", "") or "").strip()
            url0 = _normalize_base_url(url0)
            if not url0:
                return SettingsTestResponse(ok=False, target=target, message="Missing Ollama base URL.")
            url = f"{url0}/api/tags"
            code, data = _http_get_json(url, timeout_s=5.0)
            ok = 200 <= code < 300
            ms = (time.perf_counter() - started) * 1000.0
            if ok:
                models = 0
                try:
                    models = len((data or {}).get("models") or [])
                except Exception:
                    models = 0
                return SettingsTestResponse(ok=True, target=target, message=f"OK (models={models})", latency_ms=ms)
            return SettingsTestResponse(ok=False, target=target, message=f"HTTP {code}", latency_ms=ms)

        return SettingsTestResponse(ok=False, target=target, message="Unknown target. Use: openai | gemini | local | ollama | openai_compat")
    except HTTPError as e:
        ms = (time.perf_counter() - started) * 1000.0
        return SettingsTestResponse(ok=False, target=target, message=f"HTTP {getattr(e, 'code', 'error')}: {str(e)}", latency_ms=ms)
    except URLError as e:
        ms = (time.perf_counter() - started) * 1000.0
        return SettingsTestResponse(ok=False, target=target, message=f"Network error: {str(e)}", latency_ms=ms)
    except Exception as e:
        ms = (time.perf_counter() - started) * 1000.0
        return SettingsTestResponse(ok=False, target=target, message=str(e), latency_ms=ms)


@app.put("/settings", response_model=SettingsResponse)
async def put_settings(req: Request):
    """Merge and persist runtime settings overrides.

    Secrets are accepted here, but they are stored separately from settings.json.
    """
    try:
        patch = await req.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

    patch = _sanitize_incoming_settings(patch if isinstance(patch, dict) else {})
    existing = _sanitize_incoming_settings(_read_runtime_settings())
    merged = _deep_merge(existing, patch)

    try:
        write_runtime_override_payload(merged)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write settings: {exc}")

    try:
        config.reload()
    except Exception:
        # Config reload failure shouldn't brick the API; keep serving.
        pass

    # IMPORTANT: the agent/LLM objects are cached in-process.
    # When settings change (model/provider/base_url/tool-calling flags), we must
    # rebuild agents so the new config is actually used.
    global _agent, _runtime_provider
    _agent = None
    with _agent_pool_lock:
        _agent_pool.clear()

    try:
        await _reconcile_discord_bot_runtime()
    except Exception as exc:
        logger.warning(f"Discord bot reconcile after settings save failed: {exc}")

    try:
        await _reconcile_heartbeat_runtime()
    except Exception as exc:
        logger.warning(f"Heartbeat reconcile after settings save failed: {exc}")

    s = config.to_public_dict()
    overrides = _redact_settings_payload(_sanitize_incoming_settings(_read_runtime_settings()))
    return SettingsResponse(settings=s, overrides=overrides, issues=_validate_settings_effective(s))


class SoulResponse(BaseModel):
    """Response model for soul endpoint."""
    enabled: bool
    path: str
    content: str
    max_chars: int
    exists: bool


class SoulUpdateRequest(BaseModel):
    """Request model for updating soul content."""
    content: str = Field(..., description="New soul content (markdown)")


@app.get("/soul", response_model=SoulResponse)
async def get_soul():
    """Get current SOUL.md content and configuration."""
    soul_config = getattr(config, "soul", None)
    if soul_config is None:
        return SoulResponse(
            enabled=False,
            path="./SOUL.md",
            content="",
            max_chars=8000,
            exists=False
        )
    
    soul_path_str = getattr(soul_config, "path", "./SOUL.md")
    max_chars = getattr(soul_config, "max_chars", 8000)
    enabled = getattr(soul_config, "enabled", True)
    
    # Resolve path
    soul_path = Path(soul_path_str).expanduser()
    if not soul_path.is_absolute():
        soul_root = DATA_DIR if os.getenv("ECHOSPEAK_RUNTIME_KIND", "").strip().lower() == "desktop" else BASE_DIR
        soul_path = soul_root / soul_path
    
    content = ""
    exists = soul_path.exists()
    
    if exists:
        try:
            content = soul_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"Failed to read SOUL.md: {e}")
    
    return SoulResponse(
        enabled=enabled,
        path=soul_path_str,
        content=content,
        max_chars=max_chars,
        exists=exists
    )


@app.put("/soul", response_model=SoulResponse)
async def update_soul(request: SoulUpdateRequest):
    """Update SOUL.md content."""
    soul_config = getattr(config, "soul", None)
    if soul_config is None:
        raise HTTPException(status_code=500, detail="Soul configuration not initialized")
    
    soul_path_str = getattr(soul_config, "path", "./SOUL.md")
    max_chars = getattr(soul_config, "max_chars", 8000)
    
    # Resolve path
    soul_path = Path(soul_path_str).expanduser()
    if not soul_path.is_absolute():
        soul_root = DATA_DIR if os.getenv("ECHOSPEAK_RUNTIME_KIND", "").strip().lower() == "desktop" else BASE_DIR
        soul_path = soul_root / soul_path
    
    # Validate content length
    content = request.content.strip()
    if len(content) > max_chars:
        raise HTTPException(
            status_code=422,
            detail=f"Soul content exceeds max_chars limit ({len(content)} > {max_chars})"
        )
    
    # Write to file
    try:
        soul_path.parent.mkdir(parents=True, exist_ok=True)
        soul_path.write_text(content, encoding="utf-8")
        logger.info(f"Updated SOUL.md at {soul_path} ({len(content)} chars)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write SOUL.md: {e}")
    
    # Clear agent cache so new soul is loaded on next query
    global _agent
    _agent = None
    with _agent_pool_lock:
        _agent_pool.clear()
    
    return SoulResponse(
        enabled=getattr(soul_config, "enabled", True),
        path=soul_path_str,
        content=content,
        max_chars=max_chars,
        exists=True
    )


class DoctorResponse(BaseModel):
    ok: bool
    report: Dict[str, Any]
    text: str


class SessionsResponse(BaseModel):
    multi_agent_enabled: bool
    pool_max: int
    pool_size: int
    thread_ids: List[str]
    lm_studio_only: bool
    runtime_provider: Optional[str] = None


class CronTickRequest(BaseModel):
    job_id: str = Field(..., description="Job identifier")
    cron: str = Field(..., description="Cron schedule (5-field)")
    message: str = Field(..., description="Message to run when due")
    thread_id: Optional[str] = Field(default=None, description="Session/thread id")
    include_memory: bool = Field(default=True, description="Include memory")


class ScreenAnalysisResponse(BaseModel):
    """Response model for screen analysis."""
    text: str
    text_length: int
    has_text: bool
    image_size: dict


class ScreenCaptureResponse(BaseModel):
    """Response model for screen capture."""
    success: bool
    image_base64: Optional[str] = None
    error: Optional[str] = None


class HistoryResponse(BaseModel):
    """Conversation history + durable Turn timeline for page-refresh hydration."""
    history: list = Field(default_factory=list)
    count: int = 0
    session: Dict[str, Any] = Field(default_factory=dict)
    session_id: str = ""
    turns: List[Dict[str, Any]] = Field(default_factory=list)


class MemoryItem(BaseModel):
    id: str
    text: str
    timestamp: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    memory_type: Optional[str] = None
    pinned: Optional[bool] = None
    owner_id: str = ""
    scope: str = "account"
    project_id: str = ""
    source_session_id: str = ""
    source_execution_id: str = ""
    source_item_id: str = ""
    updated_at: Optional[str] = None
    index_state: str = "pending"
    supersedes: str = ""
    superseded_by: str = ""
    status: str = "active"
    checksum: str = ""
    version: int = 1
    # Curated Studio projection fields (same canonical records.json source)
    subject: Optional[str] = None
    confidence: Optional[float] = None
    explicit: Optional[bool] = None
    structured_attributes: Optional[dict] = None
    source_text: Optional[str] = None
    active: bool = True


class MemoryUpdateRequest(BaseModel):
    id: str
    text: Optional[str] = None
    memory_type: Optional[str] = None
    pinned: Optional[bool] = None
    thread_id: Optional[str] = None
    project_id: str = ""


class MemoryCompactRequest(BaseModel):
    thread_id: Optional[str] = None
    project_id: str = ""
    similarity: float = Field(default=0.94, ge=0.5, le=1.0)
    max_scan: int = Field(default=250, ge=10, le=1000)


class MemoryListResponse(BaseModel):
    items: List[MemoryItem]
    count: int
    use_faiss: bool


class MemoryDoctorResponse(BaseModel):
    ok: bool
    memory_count: int
    scanned: int
    use_faiss: bool
    auto_store_conversations: bool
    session_memory: Dict[str, Any] = Field(default_factory=dict)
    type_counts: Dict[str, int]
    pinned_count: int
    profile_fact_count: int
    missing_type_count: int
    duplicate_groups: List[Dict[str, Any]]
    warnings: List[str]
    recommendations: List[str]
    # Canonical projection audit (same records.json as Studio)
    active_semantic_samples: List[Dict[str, Any]] = Field(default_factory=list)
    superseded_count: int = 0
    session_only_count: int = 0
    pending_confirmation: Optional[Dict[str, Any]] = None


class MemoryDeleteRequest(BaseModel):
    ids: List[str]
    thread_id: Optional[str] = None
    project_id: str = ""


class DocumentItem(BaseModel):
    id: str
    filename: str
    chunks: int
    source: Optional[str] = None
    mime: Optional[str] = None
    timestamp: Optional[str] = None
    project_id: str = ""
    session_id: str = ""


class DocumentListResponse(BaseModel):
    items: List[DocumentItem]
    count: int
    enabled: bool


class DocumentDeleteRequest(BaseModel):
    ids: List[str]
    session_id: str
    project_id: str = ""


class ProviderInfoResponse(BaseModel):
    """Response model for provider information."""
    provider: str
    model: str
    local: bool
    base_url: Optional[str] = None
    available_providers: list
    context_window: int = 0
    max_output_tokens: int = 0
    ready: bool = True
    readiness_message: str = ""
    readiness_detail: str = ""
    model_profile: Dict[str, Any] = Field(default_factory=dict)
    session_id: str = "default"
    binding_revision: int = 1


class SwitchProviderRequest(BaseModel):
    """Request model for switching provider."""
    provider: str = Field(..., description="Provider ID (openai, gemini, ollama, lmstudio, localai, llama_cpp, vllm)")
    session_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=1)
    model: Optional[str] = Field(default=None, description="Model name (or path for llama.cpp)")
    base_url: Optional[str] = Field(default=None, description="Base URL for local servers (Ollama/LM Studio/LocalAI/vLLM)")
    openai_model: Optional[str] = Field(default=None, description="OpenAI model override when provider=openai")
    gemini_model: Optional[str] = Field(default=None, description="Gemini model override when provider=gemini")


class CapabilitiesResponse(BaseModel):
    ok: bool
    provider: str
    workspace: Dict[str, Any]
    tools: Dict[str, Any]
    features: Dict[str, Any]
    skills: List[Dict[str, Any]] = []
    trust: Dict[str, Any] = Field(default_factory=dict)
    capability_registry: Dict[str, Any] = Field(default_factory=dict)
    thread_context: Dict[str, Any] = Field(default_factory=dict)


class PendingActionResponse(BaseModel):
    has_pending: bool
    action: Optional[Dict[str, Any]] = None
    approval_id: Optional[str] = None
    risk_level: Optional[str] = None
    risk_color: Optional[str] = None
    policy_flags: List[str] = []
    session_permissions: Dict[str, bool] = {}
    dry_run_available: bool = False


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Echo Speak API",
        "version": "1.0.0",
        "status": "running",
        "local_models_enabled": config.use_local_models
    }


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    """
    Process a user query through the agent.

    Args:
        request: Query request with message.

    Returns:
        Agent response.
    """
    request_id = str(request.client_request_id or "").strip() or str(uuid.uuid4())
    cancel_event = threading.Event()
    _register_query_cancellation(
        request_id, request.thread_id or "default", cancel_event
    )
    _metric_inc("requests", 1)
    try:
        source = _prepare_query_transport(request, request_id)
        logger.debug(
            "Query request_id={} thread_id={} include_memory={} msg_len={}",
            request_id,
            _normalize_thread_id(request.thread_id),
            bool(request.include_memory),
            len((request.message or "")),
        )
        # The explicit Session exists independently of model readiness. Every
        # accepted message proceeds to process_query so the canonical runtime
        # creates an Execution before provider/understanding failure is recorded.
        _record_session_message(request.thread_id, request.message)
        agent = get_agent(request.thread_id)
        # process_query restores Session scope under its request lock. Query
        # payloads do not override backend-selected workspace/mode.
        thread_state = get_state_store().get_thread_state(request.thread_id).model_dump()
        q: queue.Queue = queue.Queue()
        handler = _StreamingHandler(q, request_id)
        response, success = agent.process_query(
            request.message,
            include_memory=request.include_memory,
            callbacks=[handler],
            thread_id=request.thread_id,
            source=source,
            cancel_event=cancel_event,
            request_id=request_id,
            thinking_enabled=request.thinking_enabled,
            reasoning_effort=request.reasoning_effort,
        )
        doc_sources = agent.get_last_doc_sources() if request.include_memory else []
        store = get_state_store()
        latest_state = store.get_thread_state(request.thread_id).model_dump()
        worker_execution_id = str(
            getattr(agent, "completed_execution_id_for_current_worker", lambda: "")() or ""
        )
        if not worker_execution_id and not request.voice_turn_id:
            worker_execution_id = str(latest_state.get("last_execution_id") or "")
        if request.voice_turn_id and not worker_execution_id:
            raise RuntimeError("Voice query completed without an exact worker Execution identity")
        execution = store.get_execution(worker_execution_id) if worker_execution_id else None
        if request.voice_turn_id and execution is None:
            raise RuntimeError("Voice query completed without its durable Execution")
        if request.voice_turn_id and execution is not None:
            from agent.voice_transport import bind_voice_turn_submission

            bind_voice_turn_submission(
                request.voice_turn_id,
                session_id=_normalize_thread_id(request.thread_id),
                request_id=request_id,
                execution_id=execution.id,
                task_run_id=str(execution.task_run_id or ""),
                query_completed=True,
            )

        return QueryResponse(
            response=response,
            success=success,
            memory_count=agent.memory.memory_count,
            request_id=request_id,
            doc_sources=doc_sources,
            research=handler.research_runs,
            execution_id=execution.id if execution else None,
            trace_id=execution.trace_id if execution else None,
            thread_state=latest_state or thread_state,
            voice_turn_id=request.voice_turn_id,
        )
    except Exception as e:
        if request.voice_turn_id:
            try:
                from agent.voice_transport import fail_voice_turn

                fail_voice_turn(
                    request.voice_turn_id,
                    session_id=_normalize_thread_id(request.thread_id),
                    error_code="voice_query_failed",
                )
            except Exception:
                pass
        _metric_inc("errors", 1)
        diagnostic_id = hashlib.sha256(
            f"{request_id}:{type(e).__name__}:{e}".encode("utf-8", errors="ignore")
        ).hexdigest()[:12]
        logger.exception(
            "Query failed request_id={} diagnostic_id={}", request_id, diagnostic_id
        )
        raise HTTPException(
            status_code=500,
            detail={"message": _safe_stream_failure(e), "diagnostic_id": diagnostic_id},
        )
    finally:
        _release_query_cancellation(request_id, cancel_event)


@app.post("/memory/compact")
async def compact_memory(
    request: Optional[MemoryCompactRequest] = Body(default=None),
    thread_id: Optional[str] = Query(default=None),
    project_id: str = Query(default=""),
    similarity: float = Query(default=0.94, ge=0.5, le=1.0),
    max_scan: int = Query(default=250, ge=10, le=1000),
):
    """Merge near-duplicate memory items within a thread by deleting redundant items.

    This is a lightweight compaction pass to reduce spam/duplicates.
    """
    try:
        import difflib

        direct_project_id = project_id if isinstance(project_id, str) else ""
        req = request or MemoryCompactRequest(
            thread_id=thread_id,
            project_id=direct_project_id,
            similarity=similarity,
            max_scan=max_scan,
        )
        session_id = str(req.thread_id or "").strip()
        scoped_project_id = _require_automation_project_scope(session_id, req.project_id)
        state = get_state_store().get_thread_state(session_id)
        agent = get_agent(req.thread_id)
        items = agent.memory.list_items(
            offset=0,
            limit=int(req.max_scan or 250),
            thread_id=session_id,
            project_id=scoped_project_id,
            project_path=str(state.project_path or ""),
            include_global=False,
        )
        if not items:
            return {"success": True, "deleted": 0, "kept": 0, "memory_count": 0}

        # Group by type.
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for it in items:
            meta = (it or {}).get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
            mt = str(meta.get("type") or "note").strip().lower() or "note"
            groups.setdefault(mt, []).append(it)

        deleted_ids: List[str] = []
        kept_ids: set[str] = set()

        for mt, gitems in groups.items():
            # Newest first so we prefer keeping the newest canonical.
            gitems.sort(key=lambda x: (x.get("timestamp") or ""), reverse=True)
            canon: List[Dict[str, Any]] = []
            for it in gitems:
                iid = str((it or {}).get("id") or "").strip()
                txt = str((it or {}).get("text") or "").strip()
                if not iid or not txt:
                    continue
                meta = (it or {}).get("metadata") or {}
                if not isinstance(meta, dict):
                    meta = {}
                is_pinned = meta.get("pinned") is True

                merged = False
                for c in canon:
                    cid = str((c or {}).get("id") or "").strip()
                    ctxt = str((c or {}).get("text") or "").strip()
                    if not cid or not ctxt:
                        continue
                    ratio = difflib.SequenceMatcher(a=txt.lower(), b=ctxt.lower()).ratio()
                    if ratio >= float(req.similarity or 0.94):
                        deleted_ids.append(iid)
                        kept_ids.add(cid)
                        if is_pinned:
                            try:
                                agent.memory.update_item(
                                    cid,
                                    pinned=True,
                                    thread_id=session_id,
                                    project_id=scoped_project_id,
                                    include_global=False,
                                )
                            except Exception:
                                pass
                        merged = True
                        break
                if not merged:
                    canon.append(it)
                    kept_ids.add(iid)

        deleted = agent.memory.delete_items(
            deleted_ids,
            thread_id=session_id,
            project_id=scoped_project_id,
            include_global=False,
        )
        return {
            "success": True,
            "deleted": int(deleted),
            "kept": int(len(kept_ids)),
            "memory_count": agent.memory.count_items(
                thread_id=session_id,
                project_id=scoped_project_id,
                include_global=False,
            ),
        }
    except Exception as e:
        logger.error(f"Compact memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(thread_id: Optional[str] = Query(default=None)):
    try:
        agent = get_agent(thread_id)
        # Bind the exact active Session Project before reporting tool readiness.
        # Without this, the shared agent keeps a stale skill-workspace ("chat")
        # and never activates the folder attached to this thread_id.
        _apply_thread_scope(agent, thread_id)
        report = agent.get_doctor_report() or {}
        # Skill-workspace TOOLS.txt is not a hard allowlist. Availability uses
        # registration + policy + Project scope (see coding readiness).
        allowlist = None
        allowset = None

        # Import tool metadata
        from agent.tools import TOOL_METADATA
        from agent.tool_registry import ToolRegistry

        items = []
        risk_counts: Dict[str, int] = {}
        origin_counts: Dict[str, int] = {}
        mcp_tool_count = 0
        mcp_servers = getattr(config, "mcp_servers", None) or {}
        mcp_client_present = bool((BASE_DIR / "agent" / "mcp_client.py").exists())
        manager_status: Dict[str, Any] = {}
        try:
            from agent.mcp_client import get_mcp_manager, is_mcp_client_present as _mcp_present

            mcp_client_present = bool(_mcp_present())
            manager_status = get_mcp_manager().status()
            mcp_tool_count = max(mcp_tool_count, int(manager_status.get("loaded_tool_count") or 0))
        except Exception:
            manager_status = {}

        # Canonical inventory: ToolRegistry is the single tool owner for capability
        # reporting. agent.tools / lc_tools may be subsets; never under-report
        # video/skill tools that are registered and executable via the invoke path.
        seen_tool_names: set = set()
        tool_names: list[str] = []
        try:
            tool_names = sorted(ToolRegistry.get_names())
        except Exception:
            tool_names = []
        for name in tool_names:
            if not name or name in seen_tool_names:
                continue
            seen_tool_names.add(name)
            allowed_by_workspace = True
            if allowset is not None:
                allowed_by_workspace = name in allowset
            entry = ToolRegistry.get(name)
            is_action = False
            try:
                is_action = bool(agent._is_action_tool(name))  # type: ignore[attr-defined]
            except Exception:
                is_action = bool(getattr(entry, "is_action", False)) if entry else False
            if entry is not None and getattr(entry, "is_action", False):
                is_action = True
            allowed_by_policy = True
            blocked_reason = ""
            blocked_by_policy_flags: List[str] = []
            # Prefer ToolRegistry policy_flags; fall back to TOOL_METADATA.
            meta = TOOL_METADATA.get(name, {})
            policy_flags = list(getattr(entry, "policy_flags", None) or meta.get("policy_flags") or [])
            if is_action:
                try:
                    allowed_by_policy = bool(agent._action_configured(name))  # type: ignore[attr-defined]
                except Exception:
                    allowed_by_policy = False
                if not allowed_by_policy:
                    blocked_reason = "Blocked by EchoSpeak role or configuration"
                    for flag in policy_flags:
                        if not bool(getattr(config, str(flag).lower(), False)):
                            blocked_by_policy_flags.append(flag)

            risk_level = str(
                (getattr(entry, "risk_level", None) if entry else None)
                or meta.get("risk_level")
                or "safe"
            )
            requires_confirmation = bool(
                is_action
                or meta.get("requires_confirmation", False)
            )
            category = str(getattr(entry, "category", "") or "")
            origin = "mcp" if category == "mcp" or name.startswith("mcp__") else "local"
            mcp_server = ""
            if origin == "mcp":
                mcp_tool_count += 1
                parts = name.split("__", 2)
                if len(parts) >= 3:
                    mcp_server = parts[1]
            if origin == "mcp":
                # Resolve config by sanitized or raw server key
                server_cfg = None
                if isinstance(mcp_servers, dict):
                    server_cfg = mcp_servers.get(mcp_server)
                    if server_cfg is None:
                        for sk, sv in mcp_servers.items():
                            safe = "".join(c if c.isalnum() or c == "_" else "_" for c in str(sk)).strip("_")
                            if safe == mcp_server:
                                server_cfg = sv
                                break
                if isinstance(server_cfg, dict):
                    trust_state = (
                        "capability_destructive"
                        if risk_level == "destructive"
                        else "capability_governed"
                        if is_action
                        else "capability_read"
                    )
                    transport = str(server_cfg.get("transport") or "stdio")
                else:
                    # Loaded tool without matching config key — infer from registry risk
                    if entry is not None and not entry.is_action:
                        trust_state = "capability_read"
                    else:
                        trust_state = (
                            "capability_destructive"
                            if risk_level == "destructive"
                            else "capability_governed"
                        )
                    transport = "stdio"
                if not mcp_client_present:
                    allowed_by_policy = False
                    trust_state = "client_missing"
                    blocked_reason = blocked_reason or "MCP client is missing; configured MCP servers are not available."
            else:
                trust_state = "built_in"
                transport = ""
            risk_counts[str(risk_level)] = risk_counts.get(str(risk_level), 0) + 1
            origin_counts[origin] = origin_counts.get(origin, 0) + 1

            # Get usage statistics
            try:
                from agent.tool_registry import ToolUsageStats
                usage = ToolUsageStats.get_stats(name)
            except Exception:
                usage = {"usage_count": 0, "error_count": 0, "last_used_at": None, "success_rate": None}

            allowed = bool(allowed_by_workspace and allowed_by_policy)
            items.append(
                {
                    "name": name,
                    "allowed": allowed,
                    "allowed_by_workspace": allowed_by_workspace,
                    "allowed_by_policy": allowed_by_policy,
                    "is_action": is_action,
                    "blocked_reason": blocked_reason,
                    "blocked_by_policy_flags": blocked_by_policy_flags,
                    "risk_level": risk_level,
                    "requires_confirmation": requires_confirmation,
                    "policy_flags": policy_flags,
                    "origin": origin,
                    "category": category or ("mcp" if origin == "mcp" else "local"),
                    "trust_state": trust_state,
                    "mcp_server": mcp_server or None,
                    "transport": transport or None,
                    "usage_count": usage["usage_count"],
                    "error_count": usage["error_count"],
                    "last_used_at": usage["last_used_at"],
                    "success_rate": usage["success_rate"],
                }
            )

        # Prefer live manager count over double-count from list walk
        if isinstance(manager_status, dict) and manager_status.get("loaded_tool_count") is not None:
            try:
                mcp_tool_count = max(
                    int(manager_status.get("loaded_tool_count") or 0),
                    sum(1 for it in items if it.get("origin") == "mcp"),
                )
            except Exception:
                pass

        tools = (report.get("tools") or {}) if isinstance(report.get("tools"), dict) else {}
        features = (report.get("features") or {}) if isinstance(report.get("features"), dict) else {}
        provider = str(report.get("provider") or {}).strip() if isinstance(report.get("provider"), str) else ""
        if not provider:
            provider = str(getattr(agent, "llm_provider", "") or "")

        scope = agent.project_scope_report(thread_id)
        # Annotate project-scoped tools with scope/policy readiness for the panel.
        project_read = {"project_status", "file_list", "file_read"}
        project_write = {"file_write", "file_mkdir", "file_move", "file_copy", "file_delete", "artifact_write", "notepad_write"}
        project_term = {"terminal_run"}
        for it in items:
            name = str(it.get("name") or "")
            if name in project_read:
                if not scope.get("project_attached"):
                    it["allowed"] = False
                    it["allowed_by_workspace"] = False
                    it["blocked_reason"] = it.get("blocked_reason") or "No Project attached to this Session."
                else:
                    it["allowed_by_workspace"] = True
                    it["allowed"] = bool(it.get("allowed_by_policy", True))
            elif name in project_write:
                if not scope.get("project_attached"):
                    it["allowed"] = False
                    it["allowed_by_workspace"] = False
                    it["blocked_reason"] = it.get("blocked_reason") or "No Project attached to this Session."
                elif not (scope.get("permissions") or {}).get("filesystem_write"):
                    it["allowed"] = False
                    it["allowed_by_policy"] = False
                    it["blocked_reason"] = it.get("blocked_reason") or "Write permission is disabled."
                else:
                    it["allowed_by_workspace"] = True
            elif name in project_term:
                if not scope.get("project_attached"):
                    it["allowed"] = False
                    it["allowed_by_workspace"] = False
                    it["blocked_reason"] = it.get("blocked_reason") or "No Project attached to this Session."
                elif not (scope.get("permissions") or {}).get("terminal"):
                    it["allowed"] = False
                    it["allowed_by_policy"] = False
                    it["blocked_reason"] = it.get("blocked_reason") or "Terminal permission is disabled."
                else:
                    it["allowed_by_workspace"] = True

        # Skills list: SkillsRegistry is the selection/executable owner; workspace
        # _active_skill_defs remain prompt projection. Surface both without forking.
        skills_list = []
        skills_dir = Path(getattr(config, "skills_dir", "") or "").expanduser()
        seen_skill_ids: set = set()
        try:
            from agent.skills_registry import SkillsRegistry

            SkillsRegistry.refresh()
            for man in SkillsRegistry.list_manifests() or []:
                sid = str(getattr(man, "id", "") or "").strip()
                if not sid or sid in seen_skill_ids:
                    continue
                seen_skill_ids.add(sid)
                skills_list.append(
                    {
                        "id": sid,
                        "name": str(getattr(man, "name", "") or sid),
                        "description": str(getattr(man, "description", "") or "")[:100],
                        "status": str(getattr(getattr(man, "status", None), "value", getattr(man, "status", "")) or ""),
                        "executable": bool(getattr(man, "executable", False)),
                        "origin": str(getattr(getattr(man, "origin", None), "value", getattr(man, "origin", "")) or ""),
                        "has_tools": bool(getattr(man, "required_tools", None) or getattr(man, "tools_reachable", None)),
                        "has_plugin": False,
                    }
                )
        except Exception:
            pass
        for skill_def in getattr(agent, "_active_skill_defs", []):
            sid = str(getattr(skill_def, "id", "") or "").strip()
            if not sid or sid in seen_skill_ids:
                continue
            seen_skill_ids.add(sid)
            skill_path = skills_dir / sid
            skills_list.append(
                {
                    "id": sid,
                    "name": skill_def.name,
                    "description": skill_def.description[:100] if skill_def.description else "",
                    "status": "workspace_active",
                    "executable": False,
                    "origin": "workspace",
                    "has_tools": (skill_path / "tools.py").exists() if skill_path.exists() else False,
                    "has_plugin": (skill_path / "plugin.py").exists() if skill_path.exists() else False,
                }
            )

        mcp_summary = _mcp_trust_summary(
            mcp_servers,
            mcp_client_present,
            mcp_tool_count,
            manager_status=manager_status or None,
        )

        return CapabilitiesResponse(
            ok=bool(report.get("ok", True)),
            provider=str(getattr(agent, "llm_provider", "") or ""),
            workspace=scope,
            tools={"count": len(items), "items": items, "allowlist": tools.get("allowlist")},
            features=features,
            skills=skills_list,
            capability_registry=agent._capability_registry(),
            thread_context=get_state_store().get_thread_state(thread_id).model_dump(),
            trust={
                "risk_counts": risk_counts,
                "origin_counts": origin_counts,
                **mcp_summary,
                "recommendations": [
                    "Treat local/MCP tools as executable capability. Keep exact commands, risk, and confirmation visible before use.",
                    "Prefer built-in read-only tools for inspection; require explicit approval for writes, terminal commands, desktop actions, and MCP actions.",
                    "Project scope and permissions are independent of chat/research/coding interaction mode.",
                ],
            },
        )
    except Exception as e:
        logger.error(f"Capabilities error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/pending-action", response_model=PendingActionResponse)
async def get_pending_action(thread_id: Optional[str] = Query(default=None)):
    """Get structured pending action info for confirmation UI with risk levels and session permissions."""
    try:
        store = get_state_store()
        pending_record = store.get_pending_approval(thread_id)
        pending = pending_record.model_dump() if pending_record else None

        if not pending:
            return PendingActionResponse(
                has_pending=False,
                action=None,
                approval_id=None,
                risk_level=None,
                risk_color=None,
                policy_flags=[],
                session_permissions={},
                dry_run_available=False,
            )

        tool_name = str(pending.get("tool") or "").strip()

        # Import tool metadata
        from agent.tools import TOOL_METADATA

        meta = TOOL_METADATA.get(tool_name, {})
        risk_level = meta.get("risk_level", "safe")
        policy_flags = meta.get("policy_flags", [])

        # Risk color mapping for UI
        risk_colors = {
            "safe": "#22c55e",      # green
            "moderate": "#f59e0b",  # amber
            "destructive": "#ef4444",  # red
        }
        risk_color = risk_colors.get(risk_level, "#6b7280")

        # Check dry-run availability (desktop automation tools support dry_run)
        dry_run_tools = {"desktop_click", "desktop_type_text", "desktop_activate_window", "desktop_send_hotkey"}
        dry_run_available = tool_name in dry_run_tools

        # Session permissions - what actions are allowed this session
        session_permissions = {
            "system_actions": bool(getattr(config, "enable_system_actions", False)),
            "file_write": bool(getattr(config, "allow_file_write", False)),
            "terminal": bool(getattr(config, "allow_terminal_commands", False)),
            "desktop": bool(getattr(config, "allow_desktop_automation", False)),
            "playwright": bool(getattr(config, "allow_playwright", False)),
        }

        return PendingActionResponse(
            has_pending=True,
            action=pending,
            approval_id=str(pending.get("id") or "") or None,
            risk_level=risk_level,
            risk_color=risk_color,
            policy_flags=policy_flags,
            session_permissions=session_permissions,
            dry_run_available=dry_run_available,
        )
    except Exception as e:
        logger.error(f"Pending action error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SpecialistRunCreateRequest(BaseModel):
    session_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    runtime_id: Literal["codex", "opencode"]
    objective: str = Field(min_length=1, max_length=8000)
    task_run_id: str = ""
    requirement_id: str = ""
    expected_task_revision: Optional[int] = Field(default=None, ge=1)
    expected_model_binding_revision: int = Field(ge=1)
    model_provider: str = ""
    model_id: str = ""
    local_base_url: str = ""


class SpecialistTurnRequest(BaseModel):
    session_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1, max_length=32000)


class SpecialistDecisionRequest(BaseModel):
    session_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    decision: Literal["approve", "deny"]


class SpecialistScopeRequest(BaseModel):
    session_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)


def _specialist_project_scope(session_id: str, project_id: str):
    from agent.specialist_authority import (
        SpecialistAuthorityError,
        resolve_specialist_scope,
    )

    try:
        return resolve_specialist_scope(session_id, project_id)
    except SpecialistAuthorityError as exc:
        status = 404 if str(exc) in {"Session not found", "Project not found"} else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc


def _specialist_local_base_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if (
        parsed.scheme != "http"
        or parsed.username
        or parsed.password
        or str(parsed.hostname or "").casefold() not in {
            "127.0.0.1", "localhost", "::1",
        }
    ):
        raise HTTPException(
            status_code=422,
            detail="Local model base URL must be unauthenticated HTTP loopback",
        )
    return text.rstrip("/")


def _validate_specialist_run_authority(run: Any, operation: str) -> None:
    """Fresh Echo-level validation before each specialist lifecycle action."""

    from agent.specialist_authority import (
        SpecialistAuthorityError,
        validate_specialist_run_authority,
    )

    try:
        validate_specialist_run_authority(run, operation)
    except SpecialistAuthorityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/specialist-runtimes")
async def list_specialist_runtimes_api():
    """Discover configured specialist agents; raw model providers are not listed."""

    from agent.specialist_runtime import get_specialist_runtime_manager

    items = get_specialist_runtime_manager().catalog()
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "count": len(items),
        "owner": "SpecialistRuntimeManager",
    }


@app.get("/specialist-runs")
async def list_specialist_runs_api(
    session_id: str = Query(...),
    project_id: str = Query(...),
    task_run_id: str = Query(default=""),
):
    _specialist_project_scope(session_id, project_id)
    from agent.specialist_store import get_specialist_run_store

    rows = get_specialist_run_store().list(
        session_id=session_id,
        project_id=project_id,
        task_run_id=task_run_id,
        limit=200,
    )
    return {
        "items": [row.model_dump(mode="json") for row in rows],
        "count": len(rows),
    }


@app.get("/specialist-runs/{run_id}")
async def get_specialist_run_api(
    run_id: str,
    session_id: str = Query(...),
    project_id: str = Query(...),
    after: int = Query(default=0, ge=0),
):
    _specialist_project_scope(session_id, project_id)
    from agent.specialist_runtime import get_specialist_runtime_manager

    projection = get_specialist_runtime_manager().projection(
        run_id,
        session_id=session_id,
        project_id=project_id,
        after=after,
        limit=1000,
    )
    if projection is None:
        raise HTTPException(status_code=404, detail="SpecialistRun not found")
    return projection.model_dump(mode="json")


@app.get("/specialist-runs/{run_id}/stream")
async def stream_specialist_run_api(
    run_id: str,
    request: Request,
    session_id: str = Query(...),
    project_id: str = Query(...),
    after: int = Query(default=0, ge=0),
):
    """Stream exact-scope SpecialistRun projections on durable revision changes."""

    _specialist_project_scope(session_id, project_id)
    from agent.specialist_contracts import TERMINAL_SPECIALIST_STATUSES
    from agent.specialist_store import get_specialist_run_store

    store = get_specialist_run_store()
    initial = store.get(
        run_id, session_id=session_id, project_id=project_id
    )
    if initial is None:
        raise HTTPException(status_code=404, detail="SpecialistRun not found")

    async def generate():
        last_sequence = int(after)
        current = initial
        while True:
            events = store.list_events(
                current.id, after=last_sequence, limit=2000
            )
            if events:
                last_sequence = max(item.sequence for item in events)
            yield json.dumps(
                {
                    "type": "specialist_projection",
                    "run": current.model_dump(mode="json"),
                    "events": [
                        item.model_dump(mode="json") for item in events
                    ],
                },
                ensure_ascii=False,
            ) + "\n"
            if current.status in TERMINAL_SPECIALIST_STATUSES:
                return
            revision = current.revision
            current = await asyncio.to_thread(
                store.wait_for_revision,
                current.id,
                after_revision=revision,
                timeout=15.0,
            )
            if current is None or await request.is_disconnected():
                return
            if current.revision == revision:
                yield json.dumps({
                    "type": "keepalive",
                    "run_id": current.id,
                    "revision": current.revision,
                }) + "\n"

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/specialist-runs")
async def create_specialist_run_api(request: SpecialistRunCreateRequest):
    """Explicit Code action; opening/navigating the Code view never calls this."""

    from agent.execution_graph import ExecutionProfile
    from agent.research_runtime import RequirementKind, TurnRequirement
    from agent.semantic_runtime import get_canonical_semantic_runtime
    from agent.specialist_authority import (
        SpecialistAuthorityError,
        validate_specialist_delegation_policy,
    )
    from agent.specialist_contracts import SpecialistAuthoritySnapshot
    from agent.specialist_runtime import get_specialist_runtime_manager
    from agent.task_runs import TERMINAL_TASK_STATUSES, get_task_run_store

    _state, _project, root = _specialist_project_scope(
        request.session_id, request.project_id
    )
    binding = _ensure_session_model_binding(request.session_id)
    if binding.binding_revision != request.expected_model_binding_revision:
        raise HTTPException(
            status_code=409,
            detail="Session model binding changed before specialist delegation",
        )
    manager = get_specialist_runtime_manager()
    descriptor = manager.descriptor(request.runtime_id)
    if descriptor.state.value != "available":
        raise HTTPException(
            status_code=409,
            detail=descriptor.reason or "Specialist runtime is unavailable",
        )
    try:
        validate_specialist_delegation_policy(request.runtime_id)
    except SpecialistAuthorityError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    store = get_task_run_store()
    task = None
    requirement_id = str(request.requirement_id or "").strip()
    if request.task_run_id:
        task = store.get(
            request.task_run_id,
            session_id=request.session_id,
            project_id=request.project_id,
        )
        if task is None:
            raise HTTPException(status_code=404, detail="TaskRun not found")
        if request.expected_task_revision is None:
            raise HTTPException(
                status_code=422,
                detail="expected_task_revision is required for an existing TaskRun",
            )
        if task.revision != request.expected_task_revision:
            raise HTTPException(
                status_code=409,
                detail="TaskRun changed before specialist delegation",
            )
        if task.status in TERMINAL_TASK_STATUSES:
            raise HTTPException(status_code=409, detail="TaskRun is terminal")
        requirement = next(
            (
                item for item in task.requirements
                if item.requirement_id == requirement_id
            ),
            None,
        )
        if requirement is None or requirement.kind != RequirementKind.SPECIALIST:
            raise HTTPException(
                status_code=409,
                detail="Selected TaskRun requirement is not specialist-owned",
            )
    else:
        task = store.create(
            session_id=request.session_id,
            project_id=request.project_id,
            objective=request.objective,
            requested_operation="coding_write",
            permitted_capabilities=["specialist_code"],
            requirements=[TurnRequirement(
                kind=RequirementKind.SPECIALIST,
                objective=request.objective,
                acceptance_criteria=[
                    "A configured specialist runtime must return one verified terminal outcome."
                ],
            )],
            execution_profile=ExecutionProfile.CODE,
            source="code",
            workflow_stage="specialist_requested",
        )
        requirement_id = task.requirements[0].requirement_id
    graph_node = next(
        (
            item
            for item in list(getattr(task.execution_graph, "nodes", []) or [])
            if item.requirement_id == requirement_id
        ),
        None,
    )
    if graph_node is None:
        raise HTTPException(
            status_code=409,
            detail="Specialist requirement has no owning TaskRun graph node",
        )
    authority = SpecialistAuthoritySnapshot(
        session_id=request.session_id,
        project_id=request.project_id,
        project_root=str(root),
        task_run_id=task.id,
        requirement_id=requirement_id,
        graph_node_id=graph_node.node_id,
        model_binding_revision=binding.binding_revision,
        approval_policy="on_request",
        sandbox_mode="read_only",
    )
    local_base_url = _specialist_local_base_url(request.local_base_url)
    try:
        run = manager.create_and_start(
            runtime_id=request.runtime_id,
            task=task,
            requirement_id=requirement_id,
            project_root=str(root),
            objective=request.objective,
            authority=authority,
            model_provider=request.model_provider,
            model_id=request.model_id,
            local_base_url=local_base_url,
            authority_validator=_validate_specialist_run_authority,
            continuation_scheduler=lambda finished: (
                get_canonical_semantic_runtime().schedule_specialist_continuation(
                    get_agent(request.session_id), finished
                )
            ),
        )
    except HTTPException:
        raise
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "run": run.model_dump(mode="json"),
        "task_run_id": task.id,
        "requirement_id": requirement_id,
    }


@app.post("/specialist-runs/{run_id}/turn")
async def continue_specialist_run_api(
    run_id: str, request: SpecialistTurnRequest
):
    _specialist_project_scope(request.session_id, request.project_id)
    from agent.semantic_runtime import get_canonical_semantic_runtime
    from agent.specialist_runtime import get_specialist_runtime_manager
    from agent.specialist_store import get_specialist_run_store

    run = get_specialist_run_store().get(
        run_id,
        session_id=request.session_id,
        project_id=request.project_id,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="SpecialistRun not found")
    try:
        updated = get_specialist_runtime_manager().continue_run(
            run.id,
            prompt=request.prompt,
            authority_validator=_validate_specialist_run_authority,
            continuation_scheduler=lambda finished: (
                get_canonical_semantic_runtime().schedule_specialist_continuation(
                    get_agent(request.session_id), finished
                )
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run": updated.model_dump(mode="json")}


@app.post("/specialist-runs/{run_id}/interrupt")
async def interrupt_specialist_run_api(
    run_id: str, request: SpecialistScopeRequest
):
    _specialist_project_scope(request.session_id, request.project_id)
    from agent.specialist_runtime import get_specialist_runtime_manager

    try:
        run = get_specialist_runtime_manager().interrupt(
            run_id, authority_validator=_validate_specialist_run_authority
        )
    except HTTPException:
        raise
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run": run.model_dump(mode="json")}


@app.post("/specialist-runs/{run_id}/approvals/{request_id}")
async def resolve_specialist_approval_api(
    run_id: str,
    request_id: str,
    request: SpecialistDecisionRequest,
):
    from agent.specialist_runtime import get_specialist_runtime_manager
    from agent.specialist_store import get_specialist_run_store

    _specialist_project_scope(request.session_id, request.project_id)
    run = get_specialist_run_store().get(
        run_id,
        session_id=request.session_id,
        project_id=request.project_id,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="SpecialistRun not found")
    # Denial executes no action and remains safe even when current authority was
    # reduced. Approval must revalidate both Echo ownership and the relevant
    # high-level permission before the specialist receives a one-shot decision.
    validator = None
    if request.decision == "approve":
        from agent.specialist_authority import (
            SpecialistAuthorityError,
            validate_specialist_approval_authority,
        )
        try:
            validate_specialist_approval_authority(run, request_id)
        except SpecialistAuthorityError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        validator = _validate_specialist_run_authority
    try:
        updated = get_specialist_runtime_manager().resolve_approval(
            run.id,
            request_id,
            request.decision,
            authority_validator=validator,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run": updated.model_dump(mode="json")}


@app.get("/threads/{thread_id}/state", response_model=ThreadSessionStateResponse)
async def get_thread_state(thread_id: str):
    store = get_state_store()
    return ThreadSessionStateResponse(**store.get_thread_state(thread_id).model_dump())


class SessionModelBindingResponse(BaseModel):
    session_id: str
    provider_id: str
    model_id: str
    provider_configuration_id: str
    binding_revision: int
    created_at: float
    updated_at: float


@app.get("/sessions/{session_id}/model-binding", response_model=SessionModelBindingResponse)
async def get_session_model_binding(session_id: str):
    from agent.threads import get_thread_manager

    if get_thread_manager().get_thread(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionModelBindingResponse(**_ensure_session_model_binding(session_id).model_dump())


@app.get("/sessions/{session_id}/runtime")
async def get_session_runtime(session_id: str):
    """Current activity is isolated from historical Turns by construction."""
    return get_state_store().runtime_projection(session_id)


@app.get("/approvals", response_model=ApprovalListResponse)
async def list_approvals(
    thread_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    store = get_state_store()
    items = store.list_approvals(thread_id=thread_id, status=status, limit=limit)
    return ApprovalListResponse(items=[ApprovalResponse(**item.model_dump()) for item in items], count=len(items))


@app.post("/approvals/{approval_id}/confirm", response_model=ApprovalDecisionResponse)
async def confirm_approval(
    approval_id: str,
    expected_session_id: Optional[str] = Query(default=None),
):
    store = get_state_store()
    approval = store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if expected_session_id and str(expected_session_id) != str(approval.thread_id):
        raise HTTPException(status_code=409, detail="Approval belongs to a different Session; nothing was executed")
    state = store.get_thread_state(approval.thread_id)
    if approval.status != "pending" or state.pending_approval_id != approval_id:
        raise HTTPException(status_code=409, detail="Approval is stale or is not the current pending action")
    agent = get_agent(approval.thread_id)
    response, success = agent.process_query(
        "confirm",
        include_memory=False,
        thread_id=approval.thread_id,
        requested_approval_id=approval_id,
    )
    updated = store.get_approval(approval_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Approval missing after confirm")
    thread_state = store.get_thread_state(approval.thread_id)
    task_summary = None
    if updated.task_run_id:
        try:
            from agent.task_runs import get_task_run_store
            resumed = get_task_run_store().get(
                updated.task_run_id,
                session_id=updated.session_id,
                project_id=updated.project_id,
            )
            task_summary = _task_run_summary(resumed) if resumed is not None else None
        except Exception as exc:
            logger.warning("Approval TaskRun response projection failed: {}", exc)
    try:
        from agent.skill_execution import (
            finalize_skill_executions_for_turn,
            record_skill_tool_outcome,
            resume_skill_executions_for_approval,
        )

        original_execution_id = str(approval.execution_id or approval.original_turn_id or "")
        continuation_id = str(thread_state.last_execution_id or "")
        if original_execution_id and continuation_id:
            resume_skill_executions_for_approval(
                original_execution_id,
                continuation_execution_id=continuation_id,
                approval_id=approval.id,
                state_store=store,
            )
            for run in store.list_tool_runs(continuation_id):
                record_skill_tool_outcome(store, run, owner_execution_id=original_execution_id)
            finalize_skill_executions_for_turn(
                original_execution_id,
                state_store=store,
                turn_success=bool(success),
            )
    except Exception as exc:
        logger.warning(f"Skill approval reconciliation failed: {exc}")
    return ApprovalDecisionResponse(
        approval=ApprovalResponse(**updated.model_dump()),
        success=bool(success),
        response=str(response or ""),
        execution_id=thread_state.last_execution_id or None,
        thread_state=thread_state.model_dump(),
        task_run=task_summary,
        tool_run_id=str(updated.tool_run_id or ""),
    )


@app.post("/approvals/{approval_id}/cancel", response_model=ApprovalDecisionResponse)
async def cancel_approval(
    approval_id: str,
    expected_session_id: Optional[str] = Query(default=None),
):
    store = get_state_store()
    approval = store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if expected_session_id and str(expected_session_id) != str(approval.thread_id):
        raise HTTPException(status_code=409, detail="Approval belongs to a different Session; nothing was canceled")
    state = store.get_thread_state(approval.thread_id)
    if approval.status != "pending" or state.pending_approval_id != approval_id:
        raise HTTPException(status_code=409, detail="Approval is stale or is not the current pending action")
    try:
        from agent.skill_execution import cancel_skill_execution, list_skill_executions_for_turn

        original_execution_id = str(approval.execution_id or approval.original_turn_id or "")
        for record in list_skill_executions_for_turn(original_execution_id):
            if record.status.value == "pending_approval":
                cancel_skill_execution(record.id, state_store=store)
    except Exception as exc:
        logger.warning(f"Skill cancellation projection failed: {exc}")
    updated = store.update_approval(approval_id, status="canceled", outcome_summary="Canceled by user")
    if updated is None:
        raise HTTPException(status_code=500, detail="Approval missing after cancel")
    thread_state = store.get_thread_state(approval.thread_id)
    task_summary = None
    if approval.task_run_id:
        try:
            from agent.task_runs import TaskRunStatus, get_task_run_store
            task_store = get_task_run_store()
            task = task_store.get(
                approval.task_run_id,
                session_id=approval.session_id,
                project_id=approval.project_id,
            )
            if (
                task is not None
                and task.status == TaskRunStatus.SUSPENDED_WAITING_FOR_APPROVAL
                and task.revision == approval.task_run_revision
            ):
                task = task_store.update(
                    task.id,
                    session_id=task.session_id,
                    project_id=task.project_id,
                    expected_revision=task.revision,
                    status=TaskRunStatus.CANCELLED,
                    workflow_stage="approval_cancelled",
                )
                task_summary = _task_run_summary(task)
        except Exception as exc:
            logger.warning("Approval cancellation TaskRun reconciliation failed: {}", exc)
    return ApprovalDecisionResponse(
        approval=ApprovalResponse(**updated.model_dump()),
        success=True,
        response=f"Canceled: {updated.summary or updated.tool}.",
        execution_id=thread_state.last_execution_id or None,
        thread_state=thread_state.model_dump(),
        task_run=task_summary,
    )


@app.get("/executions", response_model=ExecutionListResponse)
async def list_executions(
    thread_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    store = get_state_store()
    items = store.list_executions(thread_id=thread_id, limit=limit)
    return ExecutionListResponse(items=[ExecutionResponse(**item.model_dump()) for item in items], count=len(items))


@app.get("/executions/{execution_id}", response_model=ExecutionResponse)
async def get_execution(execution_id: str):
    store = get_state_store()
    execution = store.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return ExecutionResponse(**execution.model_dump())


class ToolRunResponse(BaseModel):
    """Canonical ToolRun projection for Session/Execution/Project hydration."""
    id: str
    project_id: str = ""
    session_id: str = "default"
    turn_id: str
    item_id: str = ""
    tool_name: str
    action_id: str = ""
    approval_id: str = ""
    status: str = "started"
    canonical_arguments: Dict[str, Any] = Field(default_factory=dict)
    canonical_arguments_hash: str = ""
    outcome: Dict[str, Any] = Field(default_factory=dict)
    verification: Dict[str, Any] = Field(default_factory=dict)
    retry_of: str = ""
    parent_tool_run_id: str = ""
    has_children: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    completed_at: Optional[float] = None


class ToolRunListResponse(BaseModel):
    items: List[ToolRunResponse]
    count: int
    session_id: str = ""
    execution_id: str = ""
    project_id: str = ""


def _project_tool_runs(
    *,
    session_id: str = "",
    execution_id: str = "",
    project_id: str = "",
    limit: int = 120,
) -> ToolRunListResponse:
    store = get_state_store()
    runs = store.query_tool_runs(
        session_id=session_id,
        execution_id=execution_id,
        project_id=project_id,
        limit=limit,
    )
    items = [ToolRunResponse(**store.project_tool_run(run)) for run in runs]
    return ToolRunListResponse(
        items=items,
        count=len(items),
        session_id=str(session_id or ""),
        execution_id=str(execution_id or ""),
        project_id=str(project_id or ""),
    )


@app.get("/tool-runs", response_model=ToolRunListResponse)
async def list_tool_runs(
    session_id: Optional[str] = Query(default=None, description="Session / thread id"),
    thread_id: Optional[str] = Query(default=None, description="Alias for session_id"),
    execution_id: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None),
    limit: int = Query(default=120, ge=1, le=500),
):
    """Canonical ToolRun list for refresh/restart hydration.

    Filter by Session, Execution (Turn), and/or Project. Returns parent/child
    identity (retry_of), terminal status, errors, verification, and approval_id.
    """
    sid = str(session_id or thread_id or "").strip()
    eid = str(execution_id or "").strip()
    pid = str(project_id or "").strip()
    if not sid and not eid and not pid:
        raise HTTPException(
            status_code=422,
            detail="Provide session_id/thread_id, execution_id, and/or project_id",
        )
    return _project_tool_runs(session_id=sid, execution_id=eid, project_id=pid, limit=limit)


@app.get("/executions/{execution_id}/tool-runs", response_model=ToolRunListResponse)
async def list_execution_tool_runs(
    execution_id: str,
    limit: int = Query(default=120, ge=1, le=500),
):
    """ToolRuns for one Execution / Turn (alias of /tool-runs?execution_id=)."""
    store = get_state_store()
    execution = store.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return _project_tool_runs(
        session_id=str(execution.thread_id or execution.session_id or ""),
        execution_id=execution_id,
        project_id=str(execution.project_id or execution.active_project_id or ""),
        limit=limit,
    )


@app.get("/traces/{trace_id}")
async def get_trace(trace_id: str):
    store = get_state_store()
    trace = store.read_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace


# === Project Management Endpoints ===

class ProjectResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = ""
    created_at: str
    updated_at: str
    memory_type: str = "project"
    context_prompt: Optional[str] = ""
    tags: List[str] = []
    metadata: Dict[str, Any] = Field(default_factory=dict)
    workspace_root: str = ""
    trust_state: str = "untrusted"
    git_root: str = ""
    git_metadata: Dict[str, Any] = Field(default_factory=dict)
    instructions: str = ""
    verified_facts: List[Dict[str, Any]] = Field(default_factory=list)
    archived: bool = False
    preferred_model_profile: Optional[Dict[str, Any]] = None


class ProjectListResponse(BaseModel):
    items: List[ProjectResponse]
    count: int


class ProjectCreateRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    context_prompt: Optional[str] = ""
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None
    workspace_root: str = ""
    trust_state: str = "untrusted"


class ProjectAttachFolderRequest(BaseModel):
    path: str
    name: str = ""
    trust_state: str = "trusted"
    session_id: str = ""


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    context_prompt: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


@app.get("/projects", response_model=ProjectListResponse)
async def list_projects():
    """List all projects."""
    from agent.projects import get_project_manager
    manager = get_project_manager()
    projects = manager.list_projects()
    return ProjectListResponse(
        items=[ProjectResponse(**p.model_dump()) for p in projects],
        count=len(projects),
    )


@app.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str):
    """Get a project by ID."""
    from agent.projects import get_project_manager
    manager = get_project_manager()
    project = manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(**project.model_dump())


@app.post("/projects", response_model=ProjectResponse)
async def create_project(request: ProjectCreateRequest):
    """Create a new project."""
    from agent.projects import get_project_manager
    manager = get_project_manager()
    project = manager.create_project(
        name=request.name,
        description=request.description,
        context_prompt=request.context_prompt,
        tags=request.tags,
        metadata=request.metadata,
        workspace_root=request.workspace_root,
        trust_state=request.trust_state,
    )
    return ProjectResponse(**project.model_dump())


@app.post("/projects/attach-folder", response_model=ProjectResponse)
async def attach_project_folder(request: ProjectAttachFolderRequest):
    from agent.projects import get_project_manager
    try:
        project = get_project_manager().attach_folder(request.path, name=request.name, trust_state=request.trust_state)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if request.session_id:
        agent = get_agent(request.session_id)
        with agent._request_lock:
            _apply_thread_scope(agent, request.session_id)
            if not agent.activate_project(project.id):
                raise HTTPException(status_code=409, detail="Folder attached but Project activation failed")
    return ProjectResponse(**project.model_dump())


@app.post("/projects/pick-folder")
async def pick_project_folder():
    """Open the native Windows folder dialog for the local desktop deployment."""
    def choose() -> str:
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            try:
                return str(filedialog.askdirectory(title="Add EchoSpeak Project folder", mustexist=True) or "")
            finally:
                root.destroy()
        except Exception as exc:
            raise RuntimeError(f"Native folder picker is unavailable: {exc}") from exc
    try:
        path = await asyncio.to_thread(choose)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"path": path, "cancelled": not bool(path)}


@app.put("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, request: ProjectUpdateRequest):
    """Update an existing project."""
    from agent.projects import get_project_manager
    manager = get_project_manager()
    project = manager.update_project(
        project_id=project_id,
        name=request.name,
        description=request.description,
        context_prompt=request.context_prompt,
        tags=request.tags,
        metadata=request.metadata,
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(**project.model_dump())


@app.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    """Delete a project and clear live agent scope for every detached Session."""
    from agent.projects import get_project_manager
    manager = get_project_manager()
    success = manager.delete_project(project_id)
    if not success:
        raise HTTPException(status_code=404, detail="Project not found")
    store = get_state_store()
    # Snapshot sessions before detach so we can clear live agent memory.
    affected = [
        st.thread_id
        for st in store.list_thread_states()
        if str(st.active_project_id or "") == str(project_id)
    ]
    detached_sessions = store.detach_project(project_id)
    for tid in affected:
        try:
            agent = get_existing_agent(tid) or get_agent(tid)
            with agent._request_lock:
                if hasattr(agent, "_clear_session_project_scope"):
                    agent._clear_session_project_scope(
                        thread_id=tid,
                        reason="Project deleted",
                    )
                else:
                    agent.activate_project(None)
        except Exception:
            pass
    return {"ok": True, "deleted": project_id, "detached_sessions": detached_sessions}


@app.post("/projects/{project_id}/activate")
async def activate_project(project_id: str, thread_id: Optional[str] = Query(default=None)):
    """Activate a project, injecting its context into the agent's system prompt."""
    agent = get_agent(thread_id)
    with agent._request_lock:
        _apply_thread_scope(agent, thread_id)
        success = agent.activate_project(project_id)
    if not success:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"ok": True, "activated": project_id, "thread_state": get_state_store().get_thread_state(thread_id).model_dump()}


@app.post("/projects/deactivate")
async def deactivate_project(thread_id: Optional[str] = Query(default=None)):
    """Deactivate the current project."""
    agent = get_agent(thread_id)
    with agent._request_lock:
        _apply_thread_scope(agent, thread_id)
        agent.activate_project(None)
    return {"ok": True, "deactivated": True, "thread_state": get_state_store().get_thread_state(thread_id).model_dump()}


# === Routine Management Endpoints ===

class RoutineResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = ""
    enabled: bool = True
    trigger_type: str = "schedule"
    schedule: Optional[str] = None
    webhook_path: Optional[str] = None
    action_type: str = "query"
    action_config: Dict[str, Any] = Field(default_factory=dict)
    last_run: Optional[str] = None
    next_run: Optional[str] = None
    run_count: int = 0
    created_at: str
    updated_at: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    delivery_channels: List[str] = Field(default_factory=lambda: ["web"])
    project_id: str = ""
    session_id: str = ""
    missed_run_policy: str = "run_next"
    last_task_id: str = ""
    last_result_status: str = ""
    last_error: str = ""


class RoutineListResponse(BaseModel):
    items: List[RoutineResponse]
    count: int


class RoutineCreateRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    enabled: Optional[bool] = True
    trigger_type: Optional[str] = "schedule"
    schedule: Optional[str] = None
    webhook_path: Optional[str] = None
    action_type: Optional[str] = "query"
    action_config: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    delivery_channels: Optional[List[str]] = None
    project_id: str = ""
    session_id: str = ""
    missed_run_policy: str = "run_next"


class RoutineUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    trigger_type: Optional[str] = None
    schedule: Optional[str] = None
    webhook_path: Optional[str] = None
    action_type: Optional[str] = None
    action_config: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    delivery_channels: Optional[List[str]] = None
    project_id: Optional[str] = None
    session_id: Optional[str] = None
    missed_run_policy: Optional[str] = None


def _require_automation_project_scope(session_id: str, project_id: str = "") -> str:
    key = str(session_id or "").strip()
    if not key:
        raise HTTPException(status_code=422, detail="Session scope is required")
    state = get_state_store().get_thread_state(key)
    active_project_id = str(state.active_project_id or "").strip()
    requested_project_id = str(project_id or active_project_id).strip()
    if not requested_project_id or active_project_id != requested_project_id:
        raise HTTPException(status_code=409, detail="Session is not bound to the requested Project")
    from agent.projects import get_project_manager

    if get_project_manager().get_project(requested_project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return requested_project_id


@app.get("/routines", response_model=RoutineListResponse)
async def list_routines(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
    enabled_only: bool = False,
):
    """List Routines in the exact active Project/Session scope."""
    from agent.routines import get_routine_manager
    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    manager = get_routine_manager()
    routines = [
        routine for routine in manager.list_routines(enabled_only=enabled_only)
        if routine.project_id == scoped_project_id and routine.session_id == session_id
    ]
    return RoutineListResponse(
        items=[RoutineResponse(**r.model_dump()) for r in routines],
        count=len(routines),
    )


@app.get("/routines/{routine_id}", response_model=RoutineResponse)
async def get_routine(
    routine_id: str,
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Get a routine by ID."""
    from agent.routines import get_routine_manager
    manager = get_routine_manager()
    routine = manager.get_routine(routine_id)
    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    if not routine or routine.project_id != scoped_project_id or routine.session_id != session_id:
        raise HTTPException(status_code=404, detail="Routine not found")
    return RoutineResponse(**routine.model_dump())


@app.post("/routines", response_model=RoutineResponse)
async def create_routine(request: RoutineCreateRequest):
    """Create a new routine."""
    from agent.routines import get_routine_manager
    scoped_project_id = _require_automation_project_scope(request.session_id, request.project_id)
    manager = get_routine_manager()
    routine = manager.create_routine(
        name=request.name,
        description=request.description,
        enabled=request.enabled,
        trigger_type=request.trigger_type,
        schedule=request.schedule,
        webhook_path=request.webhook_path,
        action_type=request.action_type,
        action_config=request.action_config,
        metadata=request.metadata,
        delivery_channels=request.delivery_channels,
        project_id=scoped_project_id,
        session_id=request.session_id,
        missed_run_policy=request.missed_run_policy,
    )
    return RoutineResponse(**routine.model_dump())


@app.put("/routines/{routine_id}", response_model=RoutineResponse)
async def update_routine(
    routine_id: str,
    request: RoutineUpdateRequest,
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Update an existing routine."""
    from agent.routines import get_routine_manager
    manager = get_routine_manager()
    existing = manager.get_routine(routine_id)
    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    if not existing or existing.project_id != scoped_project_id or existing.session_id != session_id:
        raise HTTPException(status_code=404, detail="Routine not found")
    if request.project_id is not None and str(request.project_id) != scoped_project_id:
        raise HTTPException(status_code=409, detail="Routine Project cannot change outside its scope")
    if request.session_id is not None and str(request.session_id) != session_id:
        raise HTTPException(status_code=409, detail="Routine Session cannot change outside its scope")
    routine = manager.update_routine(
        routine_id=routine_id,
        name=request.name,
        description=request.description,
        enabled=request.enabled,
        trigger_type=request.trigger_type,
        schedule=request.schedule,
        webhook_path=request.webhook_path,
        action_type=request.action_type,
        action_config=request.action_config,
        metadata=request.metadata,
        delivery_channels=request.delivery_channels,
        project_id=request.project_id,
        session_id=request.session_id,
        missed_run_policy=request.missed_run_policy,
    )
    if not routine:
        raise HTTPException(status_code=404, detail="Routine not found")
    return RoutineResponse(**routine.model_dump())


@app.delete("/routines/{routine_id}")
async def delete_routine(
    routine_id: str,
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Delete a routine."""
    from agent.routines import get_routine_manager
    manager = get_routine_manager()
    routine = manager.get_routine(routine_id)
    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    if not routine or routine.project_id != scoped_project_id or routine.session_id != session_id:
        raise HTTPException(status_code=404, detail="Routine not found")
    success = manager.delete_routine(routine_id)
    if not success:
        raise HTTPException(status_code=404, detail="Routine not found")
    return {"ok": True, "deleted": routine_id}


@app.post("/routines/{routine_id}/run")
async def run_routine(
    routine_id: str,
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Manually run a routine."""
    from agent.routines import get_routine_manager
    manager = get_routine_manager()
    routine = manager.get_routine(routine_id)
    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    if not routine or routine.project_id != scoped_project_id or routine.session_id != session_id:
        raise HTTPException(status_code=404, detail="Routine not found")
    success = manager.run_routine(routine_id)
    if not success:
        raise HTTPException(status_code=404, detail="Routine not found or run failed")
    return {"ok": True, "run": routine_id}


@app.post("/webhooks/{path:path}")
async def webhook_trigger(path: str, request: Request):
    """Trigger a routine via webhook."""
    from agent.routines import get_routine_manager
    manager = get_routine_manager()
    
    routine = manager.get_routine_by_webhook(f"/{path}")
    if not routine:
        raise HTTPException(status_code=404, detail="Webhook not found")
    raw_body = await request.body()
    secret = _load_webhook_secret()
    if secret:
        sig = request.headers.get("x-echospeak-signature") or request.headers.get("x-signature") or ""
        if not _verify_webhook_signature(secret, raw_body, sig):
            raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Get request body if any — validate type and size
    try:
        body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        if not isinstance(body, dict) or len(json.dumps(body)) > 10_000:
            raise HTTPException(status_code=400, detail="Invalid webhook body (must be JSON object under 10KB)")
    except HTTPException:
        raise
    except Exception:
        body = {}
    
    # Merge body into action config for the routine
    action_config = {**routine.action_config, "webhook_body": body}
    
    # Run the routine
    manager.run_routine(routine.id)
    
    return {"ok": True, "triggered": routine.name}


# ---------------------------------------------------------------------------
# Heartbeat API (v5.4.0 — Proactive Mode)
# ---------------------------------------------------------------------------

@app.get("/heartbeat")
async def heartbeat_status():
    """Get heartbeat scheduler status and config."""
    from agent.heartbeat import get_heartbeat_manager
    hb = get_heartbeat_manager()
    return {
        "enabled": bool(getattr(config, "heartbeat_enabled", False)),
        "running": bool(hb and hb.is_running),
        "interval_minutes": getattr(config, "heartbeat_interval", 30),
        "prompt": getattr(config, "heartbeat_prompt", ""),
        "channels": list(getattr(config, "heartbeat_channels", [])),
        "last_tick": hb.last_tick if hb else None,
        "next_tick": hb.next_tick if hb else None,
    }


@app.post("/heartbeat")
async def heartbeat_update(request: Request):
    """Update heartbeat configuration at runtime."""
    data = await request.json()
    # Persist to settings
    existing = _read_runtime_settings()
    for key in ("heartbeat_enabled", "heartbeat_interval", "heartbeat_prompt", "heartbeat_channels"):
        if key in data:
            existing[key] = data[key]
    config.apply_overrides(existing)
    config.write_runtime_overrides(existing)
    await _reconcile_heartbeat_runtime()
    return {"ok": True}


@app.post("/heartbeat/start")
async def heartbeat_start():
    """Start or restart the heartbeat scheduler."""
    from agent.heartbeat import get_heartbeat_manager
    hb = get_heartbeat_manager()
    if hb and hb.is_running:
        return {"ok": True, "message": "Already running"}
    # Also persist enabled state
    existing = _read_runtime_settings()
    existing["heartbeat_enabled"] = True
    config.apply_overrides(existing)
    config.write_runtime_overrides(existing)
    await _reconcile_heartbeat_runtime()
    return {"ok": True, "message": "Heartbeat started"}


@app.post("/heartbeat/stop")
async def heartbeat_stop():
    """Stop the heartbeat scheduler."""
    existing = _read_runtime_settings()
    existing["heartbeat_enabled"] = False
    config.apply_overrides(existing)
    config.write_runtime_overrides(existing)
    await _reconcile_heartbeat_runtime()
    return {"ok": True, "message": "Heartbeat stopped"}


@app.get("/heartbeat/history")
async def heartbeat_history(limit: int = 20):
    """Get recent heartbeat results."""
    from agent.heartbeat import get_heartbeat_manager
    hb = get_heartbeat_manager()
    history = hb.get_history(limit=limit) if hb else []
    return {"history": history}


# ---------------------------------------------------------------------------
# Retired ProactiveEngine compatibility API
# ---------------------------------------------------------------------------

@app.get("/proactive")
async def proactive_status():
    """Project the retired scheduler contract without reviving its authority."""
    return {
        "running": False,
        "retired": True,
        "tasks": [],
        "channels": [],
        "replacement": "/routines",
        "message": (
            "ProactiveEngine was retired. Routines, AutomationRuns, and TaskRuns "
            "own scheduled and background work."
        ),
    }


@app.post("/proactive/task")
async def proactive_add_task(request: Request):
    """Fail closed instead of creating work in a second scheduler."""
    raise HTTPException(
        status_code=410,
        detail=(
            "ProactiveEngine is retired. Create Project/Session-scoped work with "
            "POST /routines so AutomationRun, Execution, and TaskRun lineage remain canonical."
        ),
    )


@app.get("/proactive/history")
async def proactive_history(limit: int = 20):
    """Return an inert compatibility projection; canonical history is TaskRun-owned."""
    return {
        "retired": True,
        "history": [],
        "replacement": "/routines",
        "message": "Use routine and TaskRun projections for background-work history.",
    }

# ---------------------------------------------------------------------------
# Discord API
# ---------------------------------------------------------------------------

@app.get("/discord")
async def discord_status():
    """Get Discord bot status."""
    from discord_bot import get_bot
    bot = get_bot()
    is_running = False
    username = None
    guilds = 0
    if bot and bot.is_running() and bot.client:
        is_running = True
        username = getattr(bot.client.user, "name", None)
        try:
            guilds = len(bot.client.guilds)
        except Exception:
            pass

    return {
        "enabled": bool(getattr(config, "allow_discord_bot", False)),
        "running": is_running,
        "token_set": bool(getattr(config, "discord_bot_token", "")),
        "username": username,
        "guilds": guilds,
        "allowed_users": list(getattr(config, "discord_bot_allowed_users", [])),
        "allowed_roles": list(getattr(config, "discord_bot_allowed_roles", [])),
    }

# ---------------------------------------------------------------------------
# Telegram API (v5.4.0)
# ---------------------------------------------------------------------------

@app.get("/telegram")
async def telegram_status():
    """Get Telegram bot status."""
    from telegram_bot import get_telegram_bot
    tg = get_telegram_bot()
    return {
        "enabled": bool(getattr(config, "allow_telegram_bot", False)),
        "running": bool(tg and tg.is_running),
        "token_set": bool(getattr(config, "telegram_bot_token", "")),
        "allowed_users": list(getattr(config, "telegram_allowed_users", [])),
        "auto_confirm": getattr(config, "telegram_auto_confirm", True),
    }


@app.post("/telegram/send")
async def telegram_send(request: Request):
    """Send a message to a Telegram user."""
    data = await request.json()
    from telegram_bot import get_telegram_bot
    tg = get_telegram_bot()
    if not tg or not tg.is_running:
        return {"ok": False, "error": "Telegram bot is not running"}
    text = data.get("text", "")
    chat_id = data.get("chat_id", "")
    if not text or not chat_id:
        return {"ok": False, "error": "text and chat_id are required"}
    try:
        import asyncio
        app_instance = tg._application
        loop = tg._loop
        if app_instance and loop:
            async def _send():
                await app_instance.bot.send_message(chat_id=chat_id, text=text)
            asyncio.run_coroutine_threadsafe(_send(), loop)
        return {"ok": True, "sent_to": chat_id}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Twitch API (v6.7.0)
# ---------------------------------------------------------------------------

@app.get("/twitch")
async def twitch_status():
    """Get Twitch bot status."""
    try:
        from twitch_bot import get_twitch_bot
        bot = get_twitch_bot()
        return bot.get_status()
    except Exception:
        return {"enabled": False, "running": False}


@app.post("/twitch/eventsub")
async def twitch_eventsub_webhook(request: Request):
    """Handle Twitch EventSub webhook notifications.

    Twitch sends:
      - Verification challenges (respond with the challenge string)
      - Notifications (chat messages, stream events)
      - Revocations
    """
    try:
        from twitch_bot import get_twitch_bot
        bot = get_twitch_bot()
        if not bot or not bot.is_running:
            return JSONResponse({"error": "Twitch bot not running"}, status_code=503)

        headers = {k.lower(): v for k, v in request.headers.items()}
        body = await request.body()
        result = await bot.handle_eventsub_webhook(headers, body)

        if "error" in result:
            if result["error"] == "signature_invalid":
                return JSONResponse({"error": "Forbidden"}, status_code=403)
            return JSONResponse(result, status_code=400)

        if "challenge" in result:
            # Must return the challenge as plain text for verification
            return Response(content=result["challenge"], media_type="text/plain")

        return result
    except Exception as e:
        logger.error(f"Twitch EventSub webhook error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Twitter/X API (v6.7.0)
# ---------------------------------------------------------------------------

@app.get("/twitter")
async def twitter_status():
    """Get Twitter/X bot status."""
    try:
        from twitter_bot import get_twitter_bot
        bot = get_twitter_bot()
        return bot.get_status()
    except Exception:
        return {"enabled": False, "running": False}


@app.post("/twitter/tweet")
async def twitter_post_tweet(request: Request):
    """Post a tweet via the Twitter/X bot."""
    data = await request.json()
    text = data.get("text", "").strip()
    if not text:
        return {"ok": False, "error": "text is required"}
    try:
        from twitter_bot import get_twitter_bot
        bot = get_twitter_bot()
        if not bot or not bot.is_running:
            return {"ok": False, "error": "Twitter bot is not running"}
        result = bot.post_tweet(text)
        return {"ok": "error" not in result, **result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/twitter/mentions")
async def twitter_get_mentions():
    """Get recent mentions of the Twitter/X bot."""
    try:
        from twitter_bot import get_twitter_bot
        bot = get_twitter_bot()
        if not bot or not bot.is_running:
            return {"ok": False, "error": "Twitter bot is not running", "mentions": []}
        mentions = bot.get_mentions(max_results=10)
        return {"ok": True, "mentions": mentions}
    except Exception as e:
        return {"ok": False, "error": str(e), "mentions": []}


@app.get("/twitter/autonomous")
async def twitter_autonomous_status():
    """Get autonomous tweeting status, pending tweet, and recent history."""
    try:
        from twitter_bot import get_twitter_bot
        bot = get_twitter_bot()
        status = bot.get_status().get("autonomous", {})
        history = bot.get_auto_tweet_history(limit=10) if bot.is_running else []
        return {"ok": True, **status, "history": history}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/twitter/autonomous/approve")
async def twitter_autonomous_approve():
    """Approve and post the pending autonomous tweet."""
    try:
        from twitter_bot import get_twitter_bot
        bot = get_twitter_bot()
        if not bot or not bot.is_running:
            return {"ok": False, "error": "Twitter bot is not running"}
        return bot.approve_pending_tweet()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/twitter/autonomous/reject")
async def twitter_autonomous_reject():
    """Reject the pending autonomous tweet."""
    try:
        from twitter_bot import get_twitter_bot
        bot = get_twitter_bot()
        if not bot or not bot.is_running:
            return {"ok": False, "error": "Twitter bot is not running"}
        return bot.reject_pending_tweet()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/twitter/autonomous/history")
async def twitter_autonomous_history():
    """Get autonomous tweet history (last 20 attempts)."""
    try:
        from twitter_bot import get_twitter_bot
        bot = get_twitter_bot()
        history = bot.get_auto_tweet_history(limit=20) if bot.is_running else []
        return {"ok": True, "history": history}
    except Exception as e:
        return {"ok": False, "error": str(e), "history": []}


@app.get("/sessions", response_model=SessionsResponse)
async def list_sessions():
    runtime_provider = _runtime_provider.value if _runtime_provider is not None else None

    with _agent_pool_lock:
        thread_ids = list(_agent_pool.keys())

    return SessionsResponse(
        multi_agent_enabled=True,
        pool_max=_agent_pool_max,
        pool_size=len(thread_ids),
        thread_ids=thread_ids,
        lm_studio_only=_is_lmstudio_only_enabled(),
        runtime_provider=runtime_provider,
    )


@app.get("/agents", response_model=SessionsResponse)
async def list_agents():
    return await list_sessions()


# ── Thread Management (v6.0.0) ──────────────────────────────────────

class ThreadCreateRequest(BaseModel):
    title: str = Field(default="", description="Thread title")
    source: str = Field(default="web", description="Source: web, discord, telegram, whatsapp, api")
    workspace_id: str = Field(default="", description="Optional workspace ID")
    project_id: str = Field(default="", description="Optional containing Project")
    idempotency_key: str = Field(
        default="", max_length=128, description="Opaque key that makes creation retries idempotent"
    )

class ThreadUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, description="New title")
    pinned: Optional[bool] = Field(default=None, description="Pin/unpin thread")
    archived: Optional[bool] = Field(default=None, description="Archive/unarchive thread")

class ThreadResponse(BaseModel):
    thread_id: str
    title: str = ""
    created_at: float = 0.0
    last_active_at: float = 0.0
    message_count: int = 0
    source: str = "web"
    workspace_id: str = ""
    pinned: bool = False
    archived: bool = False
    project_id: str = ""


def _thread_response(thread) -> ThreadResponse:
    payload = thread.to_dict()
    payload["project_id"] = get_state_store().get_thread_state(thread.thread_id).active_project_id
    return ThreadResponse(**payload)


def _record_session_message(thread_id: Optional[str], message: str) -> None:
    tid = _normalize_thread_id(thread_id)
    if not tid:
        return
    from agent.threads import get_thread_manager
    manager = get_thread_manager()
    if manager.get_thread(tid) is None:
        # Session creation belongs exclusively to POST /threads (the explicit
        # New Session/+ UI). Query completion must never manufacture a sidebar
        # Session from a missing/default/stale id.
        logger.warning("Skipped message metadata for unknown Session {}; no Session was created", tid)
        return
    manager.record_user_message(tid, message)


@app.get("/threads")
async def list_threads(
    include_archived: bool = Query(default=False),
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=50),
):
    """List conversation threads."""
    from agent.threads import get_thread_manager
    tm = get_thread_manager()
    threads = tm.list_threads(include_archived=include_archived, source=source, limit=limit)
    return [_thread_response(t) for t in threads]


@app.post("/threads", response_model=ThreadResponse)
async def create_thread(request: ThreadCreateRequest):
    """Create a new conversation thread."""
    from agent.threads import get_thread_manager
    tm = get_thread_manager()
    try:
        thread = tm.create_thread(
            title=request.title,
            source=request.source,
            workspace_id=request.workspace_id,
            idempotency_key=request.idempotency_key,
            idempotency_context=request.project_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if request.project_id:
        agent = get_agent(thread.thread_id)
        with agent._request_lock:
            _apply_thread_scope(agent, thread.thread_id)
            if not agent.activate_project(request.project_id):
                tm.delete_thread(thread.thread_id)
                raise HTTPException(status_code=404, detail="Project not found")
    return _thread_response(thread)


@app.get("/threads/{thread_id}", response_model=ThreadResponse)
async def get_thread(thread_id: str):
    """Get a conversation thread by ID."""
    from agent.threads import get_thread_manager
    tm = get_thread_manager()
    thread = tm.get_thread(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    return _thread_response(thread)


@app.patch("/threads/{thread_id}", response_model=ThreadResponse)
async def update_thread(thread_id: str, request: ThreadUpdateRequest):
    """Update a conversation thread."""
    from agent.threads import get_thread_manager
    tm = get_thread_manager()
    thread = tm.update_thread(
        thread_id=thread_id,
        title=request.title,
        pinned=request.pinned,
        archived=request.archived,
    )
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    return _thread_response(thread)


@app.delete("/threads/{thread_id}")
async def delete_thread(thread_id: str):
    """Delete a conversation thread."""
    from agent.threads import get_thread_manager
    tm = get_thread_manager()
    deleted = tm.delete_thread(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    return {"deleted": True, "thread_id": thread_id}

@app.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    session_id: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None),
):
    store = get_document_store()
    if store is None:
        return DocumentListResponse(items=[], count=0, enabled=False)
    if session_id and project_id:
        state = get_state_store().get_thread_state(session_id)
        if str(state.active_project_id or "") != str(project_id or ""):
            raise HTTPException(status_code=409, detail="Document Project does not match the active Session Project")
    items = store.list_documents(project_id=str(project_id or ""), session_id=str(session_id or ""))
    return DocumentListResponse(items=[DocumentItem(**i) for i in items], count=len(items), enabled=True)


# ── Observability Dashboard (v6.0.0) ────────────────────────────────

@app.get("/observability")
async def observability_dashboard():
    """Get the observability dashboard with system metrics, tool stats, and errors."""
    from agent.observability import get_observability_collector
    collector = get_observability_collector()
    return collector.get_dashboard()


# ── NDJSON Streaming (v6.0.0) ────────────────────────────────────────

@app.get("/stream/{request_id}")
async def stream_events(request_id: str):
    """Stream tool-execution events as NDJSON for real-time UI updates.

    Returns a StreamingResponse with Content-Type: application/x-ndjson.
    Each line is a JSON object with event_type, timestamp, tool_name, data, etc.
    """
    from starlette.responses import StreamingResponse
    from agent.stream_events import get_stream_buffer

    buffer = get_stream_buffer(request_id)
    return StreamingResponse(
        buffer.stream(),
        media_type="application/x-ndjson",
        headers={
            "X-Request-Id": request_id,
            "Cache-Control": "no-cache",
        },
    )


# ── A2A Protocol Endpoints (v6.0.0) ─────────────────────────────────

def _a2a_auth_check(request):
    """Verify A2A auth key if configured."""
    auth_key = getattr(config, "a2a_auth_key", "") or ""
    if not auth_key:
        return  # No auth required
    auth_header = request.headers.get("authorization", "")
    if auth_header.replace("Bearer ", "").strip() != auth_key:
        raise HTTPException(status_code=401, detail="Invalid A2A auth key")


@app.get("/.well-known/agent.json")
async def agent_card():
    """Publish EchoSpeak's A2A Agent Card for discovery."""
    if not getattr(config, "a2a_enabled", False):
        raise HTTPException(status_code=404, detail="A2A protocol is disabled")
    from agent.a2a import build_agent_card
    base_url = str(getattr(config, "api", None) and getattr(config.api, "base_url", "") or "")
    card = build_agent_card(base_url)
    return card.to_dict()


@app.post("/a2a")
async def a2a_rpc(request: Request):
    """JSON-RPC 2.0 endpoint for A2A task operations.

    Methods: tasks/send, tasks/get, tasks/cancel
    """
    if not getattr(config, "a2a_enabled", False):
        raise HTTPException(status_code=404, detail="A2A protocol is disabled")
    _a2a_auth_check(request)

    from agent.a2a import (
        get_task_manager, A2AMessage, TextPart, TaskState,
    )

    try:
        body = await request.json()
    except Exception:
        return {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None}

    rpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    tm = get_task_manager()

    # ── tasks/send ──────────────────────────────────────
    if method == "tasks/send":
        msg_data = params.get("message", {})
        parts = msg_data.get("parts", [])
        text = " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
        if not text:
            return {"jsonrpc": "2.0", "error": {"code": -32602, "message": "No text content in message"}, "id": rpc_id}

        message = A2AMessage(role="user", parts=[TextPart(text=text)])
        task = tm.create_task(message, metadata=params.get("metadata"))
        task = tm.process_task(task)
        return {"jsonrpc": "2.0", "result": task.to_dict(), "id": rpc_id}

    # ── tasks/get ───────────────────────────────────────
    if method == "tasks/get":
        task_id = params.get("id", "")
        task = tm.get_task(task_id)
        if not task:
            return {"jsonrpc": "2.0", "error": {"code": -32001, "message": "Task not found"}, "id": rpc_id}
        return {"jsonrpc": "2.0", "result": task.to_dict(), "id": rpc_id}

    # ── tasks/cancel ────────────────────────────────────
    if method == "tasks/cancel":
        task_id = params.get("id", "")
        task = tm.update_status(task_id, TaskState.CANCELED)
        if not task:
            return {"jsonrpc": "2.0", "error": {"code": -32001, "message": "Task not found"}, "id": rpc_id}
        return {"jsonrpc": "2.0", "result": task.to_dict(), "id": rpc_id}

    return {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Method not found: {method}"}, "id": rpc_id}


@app.get("/a2a/tasks")
async def a2a_list_tasks(request: Request, limit: int = 50):
    """Admin endpoint: list active A2A tasks."""
    if not getattr(config, "a2a_enabled", False):
        raise HTTPException(status_code=404, detail="A2A protocol is disabled")
    _a2a_auth_check(request)
    from agent.a2a import get_task_manager
    tm = get_task_manager()
    tasks = tm.list_tasks(limit=limit)
    return {"tasks": [t.to_dict() for t in tasks], "count": len(tasks)}


# ── Multi-Agent Orchestration Endpoints (v6.0.0) ────────────────

@app.post("/documents/upload", response_model=DocumentItem)
async def upload_document(
    file: UploadFile = File(...),
    source: Optional[str] = None,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
):
    store = get_document_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Document RAG is disabled")
    try:
        data = await file.read()
        max_bytes = int(getattr(config, "doc_upload_max_mb", 25) or 25) * 1024 * 1024
        if len(data) > max_bytes:
            raise HTTPException(status_code=413, detail="Upload too large")
        text = _extract_text_from_upload(file.filename or "document", file.content_type, data)
        if session_id and project_id:
            state = get_state_store().get_thread_state(session_id)
            if str(state.active_project_id or "") != str(project_id or ""):
                raise HTTPException(status_code=409, detail="Document Project does not match the active Session Project")
        meta = store.add_document(
            file.filename or "document",
            text,
            source=source or "",
            mime=file.content_type or "",
            project_id=str(project_id or ""),
            session_id=str(session_id or ""),
        )
        return DocumentItem(**meta)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Document upload failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/documents/delete")
async def delete_documents(request: DocumentDeleteRequest):
    store = get_document_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Document RAG is disabled")
    state = get_state_store().get_thread_state(request.session_id)
    if request.project_id and str(state.active_project_id or "") != request.project_id:
        raise HTTPException(status_code=409, detail="Document Project does not match the active Session Project")
    if not request.project_id and state.active_project_id:
        raise HTTPException(status_code=409, detail="Project-bound Session requires a Project-scoped document mutation")
    try:
        deleted = store.delete_documents(
            request.ids,
            project_id=request.project_id,
            session_id=request.session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "deleted": deleted}


@app.post("/documents/clear")
async def clear_documents(session_id: str = Query(...), project_id: str = Query(default="")):
    store = get_document_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Document RAG is disabled")
    state = get_state_store().get_thread_state(session_id)
    if project_id and str(state.active_project_id or "") != project_id:
        raise HTTPException(status_code=409, detail="Document Project does not match the active Session Project")
    if not project_id and state.active_project_id:
        raise HTTPException(status_code=409, detail="Project-bound Session requires a Project-scoped document clear")
    try:
        deleted = store.clear_scope(project_id=project_id, session_id=session_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "deleted": deleted}


@app.post("/query/stream")
async def query_stream(request: QueryRequest):
    q: queue.Queue = queue.Queue()
    request_id = str(request.client_request_id or "").strip() or str(uuid.uuid4())
    cancel_event = threading.Event()
    _register_query_cancellation(request_id, request.thread_id or "default", cancel_event)
    _metric_inc("requests", 1)

    logger.debug(
        "QueryStream request_id={} thread_id={} include_memory={} msg_len={}",
        request_id,
        _normalize_thread_id(request.thread_id),
        bool(request.include_memory),
        len((request.message or "")),
    )
    # Unknown Session ids still fail closed in _record_session_message. Provider
    # and coding readiness are diagnosed only after the Turn owns an Execution.
    try:
        source = _prepare_query_transport(request, request_id)
        _record_session_message(request.thread_id, request.message)
        agent = get_agent(request.thread_id)
        _start_agent_thread(
            agent=agent,
            message=request.message,
            include_memory=request.include_memory,
            thread_id=request.thread_id,
            workspace=request.workspace,
            request_id=request_id,
            q=q,
            cancel_event=cancel_event,
            thinking_enabled=request.thinking_enabled,
            reasoning_effort=request.reasoning_effort,
            source=source,
            voice_turn_id=str(request.voice_turn_id or ""),
        )
    except Exception as exc:
        cancel_event.set()
        _release_query_cancellation(request_id, cancel_event)
        if request.voice_turn_id:
            try:
                from agent.voice_transport import fail_voice_turn

                fail_voice_turn(
                    str(request.voice_turn_id),
                    session_id=str(request.thread_id or "default"),
                    error_code="voice_query_start_failed",
                )
            except Exception as voice_exc:
                logger.warning(
                    "Voice transport startup failure could not be persisted voice_turn_id={} request_id={} error_type={}",
                    request.voice_turn_id,
                    request_id,
                    type(voice_exc).__name__,
                )
        raise

    async def gen():
        first = True
        startup_timeout = max(
            1.0,
            float(getattr(config, "stream_startup_timeout_seconds", 15.0) or 15.0),
        )
        try:
            while True:
                try:
                    item = await anyio.to_thread.run_sync(
                        (lambda: q.get(timeout=startup_timeout)) if first else q.get,
                        abandon_on_cancel=True,
                    )
                except queue.Empty:
                    cancel_event.set()
                    yield (
                        json.dumps(
                            {
                                "type": "error",
                                "message": "The selected model did not start responding in time. This run was cancelled.",
                                "request_id": request_id,
                                "at": time.time(),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    ).encode("utf-8")
                    break
                first = False
                if item is None:
                    break
                yield (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")
        finally:
            cancel_event.set()
            _release_query_cancellation(request_id, cancel_event)

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# Track active WebSocket connections for cross-source notifications (Fix 5)
_gateway_connections: set = set()
_gateway_loop = None


async def _broadcast_to_gateway(event: dict) -> None:
    """Push an event to all connected gateway WebSocket clients."""
    dead: list = []
    for ws in _gateway_connections:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _gateway_connections.discard(ws)


def broadcast_discord_event(event: dict) -> None:
    """Schedule a broadcast from a sync context (e.g. Discord bot callbacks)."""
    import asyncio
    try:
        loop = _gateway_loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
        if loop is None:
            return
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_broadcast_to_gateway(event), loop)
    except Exception:
        pass


# ── Spotify Playback Monitor ───────────────────────────────────────────────
# Polls Spotify current_playback() and broadcasts state changes to the
# Web UI so the avatar can vibe when music is playing.
# If Spotify returns a fatal error (403 Premium required, 401, etc.) the
# monitor logs once and stops polling permanently.

_spotify_monitor_task: Optional[asyncio.Task] = None
_spotify_last_state: dict = {"is_playing": False, "track_id": None}

_SPOTIFY_FATAL_MARKERS = (
    "403",
    "premium",
    "forbidden",
    "401",
    "unauthorized",
)


async def _spotify_playback_monitor():
    """Background loop: poll Spotify playback every ~12s, broadcast changes."""
    poll_interval = 12  # seconds
    consecutive_errors = 0
    while True:
        try:
            await asyncio.sleep(poll_interval)
            try:
                from config import config as _cfg
                if not getattr(_cfg, "allow_spotify", False):
                    continue
            except Exception:
                continue

            # Run the blocking spotipy call in a thread
            try:
                from skills.spotify.tools import _get_spotify_client
                sp = await asyncio.to_thread(_get_spotify_client)
                current = await asyncio.to_thread(sp.current_playback)
                consecutive_errors = 0
            except Exception as exc:
                msg = str(exc).strip() or exc.__class__.__name__
                msg_lower = msg.lower()
                # Detect fatal / permanent errors and stop polling
                if any(m in msg_lower for m in _SPOTIFY_FATAL_MARKERS):
                    logger.warning(
                        f"Spotify monitor disabled — account lacks required access: {msg}"
                    )
                    # Broadcast a final "stopped" so the avatar stops dancing
                    _spotify_last_state["is_playing"] = False
                    _spotify_last_state["track_id"] = None
                    await _broadcast_to_gateway({
                        "type": "spotify_playback",
                        "is_playing": False,
                        "track_id": "",
                        "track_name": "",
                        "track_artist": "",
                        "duration_ms": 0,
                        "progress_ms": 0,
                        "at": time.time(),
                    })
                    return  # exit the loop permanently
                consecutive_errors += 1
                if consecutive_errors <= 3:
                    logger.warning(f"Spotify monitor error ({consecutive_errors}/3): {msg}")
                elif consecutive_errors == 4:
                    logger.warning("Spotify monitor: suppressing further errors until success")
                continue

            is_playing = False
            track_id = None
            track_name = ""
            track_artist = ""
            track_duration_ms = 0
            progress_ms = 0

            if current and current.get("item"):
                is_playing = bool(current.get("is_playing", False))
                track = current["item"]
                track_id = track.get("id") or track.get("uri")
                track_name = track.get("name", "")
                track_artist = ", ".join(
                    a.get("name", "") for a in track.get("artists", [])
                )
                track_duration_ms = track.get("duration_ms", 0)
                progress_ms = current.get("progress_ms", 0)

            prev = _spotify_last_state
            changed = (
                prev["is_playing"] != is_playing
                or prev["track_id"] != track_id
            )

            _spotify_last_state["is_playing"] = is_playing
            _spotify_last_state["track_id"] = track_id

            # Broadcast on every poll if playing, or once on stop
            if is_playing or changed:
                await _broadcast_to_gateway({
                    "type": "spotify_playback",
                    "is_playing": is_playing,
                    "track_id": track_id or "",
                    "track_name": track_name,
                    "track_artist": track_artist,
                    "duration_ms": track_duration_ms,
                    "progress_ms": progress_ms,
                    "at": time.time(),
                })
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug(f"Spotify monitor tick error: {exc}")
            await asyncio.sleep(30)


@app.websocket("/gateway/ws")
async def gateway_ws(websocket: WebSocket):
    client_host = _get_client_ip(websocket)
    if not _api_auth_ok(websocket.headers, client_host):
        await websocket.close(code=1008, reason="EchoSpeak API auth required")
        return
    offered_protocols = {
        value.strip()
        for value in str(websocket.headers.get("sec-websocket-protocol") or "").split(",")
        if value.strip()
    }
    await websocket.accept(subprotocol="echospeak" if "echospeak" in offered_protocols else None)
    _gateway_connections.add(websocket)
    session_id = str(uuid.uuid4())
    await websocket.send_json({"type": "gateway_ready", "session_id": session_id, "at": time.time()})

    while True:
        try:
            payload = await websocket.receive_json()
        except WebSocketDisconnect:
            _gateway_connections.discard(websocket)
            break
        except Exception as exc:
            await websocket.send_json({"type": "error", "message": f"Invalid message: {exc}", "at": time.time()})
            continue

        if not isinstance(payload, dict):
            await websocket.send_json({"type": "error", "message": "Message must be a JSON object.", "at": time.time()})
            continue

        msg_type = str(payload.get("type") or "").strip().lower()
        if msg_type == "ping":
            await websocket.send_json({"type": "pong", "at": time.time()})
            continue
        if msg_type != "query":
            await websocket.send_json({"type": "error", "message": f"Unknown message type: {payload.get('type')}", "at": time.time()})
            continue

        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            await websocket.send_json({"type": "error", "message": "Missing 'message' field for query.", "at": time.time()})
            continue

        include_memory = payload.get("include_memory", True)
        if isinstance(include_memory, str):
            include_memory = include_memory.strip().lower() not in {"false", "0", "no", "off"}
        elif include_memory is None:
            include_memory = True
        else:
            include_memory = bool(include_memory)

        thread_id_val = payload.get("thread_id")
        thread_id = str(thread_id_val).strip() if thread_id_val is not None else None
        if thread_id == "":
            thread_id = None

        request_id = payload.get("request_id") or str(uuid.uuid4())
        request_id = str(request_id)
        thinking_enabled = bool(payload.get("thinking_enabled", True))
        reasoning_effort = str(payload.get("reasoning_effort") or "medium")
        if reasoning_effort not in {
            "minimal", "low", "medium", "high", "extra_high", "max", "ultra"
        }:
            reasoning_effort = "medium"

        agent = get_agent(thread_id)
        try:
            ws = str(payload.get("workspace") or "").strip()
            if ws:
                if ws.lower() in {"auto", "default", "none", "clear"}:
                    agent.configure_workspace(None)
                else:
                    agent.configure_workspace(ws)
        except Exception:
            pass

        q: queue.Queue = queue.Queue()
        cancel_event = threading.Event()
        _register_query_cancellation(request_id, thread_id or "default", cancel_event)
        _metric_inc("requests", 1)
        _start_agent_thread(
            agent=agent,
            message=message,
            include_memory=include_memory,
            thread_id=thread_id,
            workspace=payload.get("workspace"),
            request_id=request_id,
            q=q,
            cancel_event=cancel_event,
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
        )

        try:
            first = True
            startup_timeout = max(
                1.0,
                float(getattr(config, "stream_startup_timeout_seconds", 15.0) or 15.0),
            )
            while True:
                try:
                    item = await anyio.to_thread.run_sync(
                        (lambda: q.get(timeout=startup_timeout)) if first else q.get,
                        abandon_on_cancel=True,
                    )
                except queue.Empty:
                    cancel_event.set()
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "The selected model did not start responding in time. This run was cancelled.",
                            "request_id": request_id,
                            "at": time.time(),
                        }
                    )
                    break
                first = False
                if item is None:
                    break
                try:
                    await websocket.send_json(item)
                except WebSocketDisconnect:
                    return
                except Exception as exc:
                    logger.warning(f"Gateway WS send failed: {exc}")
                    break
        finally:
            cancel_event.set()
            _release_query_cancellation(request_id, cancel_event)


@app.get("/doctor", response_model=DoctorResponse)
async def doctor(thread_id: Optional[str] = Query(default=None)):
    try:
        agent = get_agent(thread_id)
        report = agent.get_doctor_report()
        text = ""
        try:
            text = str(agent.format_doctor_report(report) or "")
        except Exception:
            text = ""
        return DoctorResponse(ok=bool(report.get("ok")), report=report, text=text)
    except Exception as e:
        logger.error(f"Doctor error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class WorkspaceResponse(BaseModel):
    root: str = Field(description="Absolute path of the current FILE_TOOL_ROOT")
    display_name: str = Field(description="Short display name (last directory component)")
    files: List[Dict[str, Any]] = Field(default_factory=list, description="File listing of the root directory")
    writable: bool = Field(default=False, description="Whether file_write is enabled")
    terminal: bool = Field(default=False, description="Whether terminal_run is enabled")


class WorkspaceChangeRequest(BaseModel):
    root: str = Field(..., description="New FILE_TOOL_ROOT path (absolute)")


def _build_file_tree(root_path: Path, max_depth: int = 3, max_items: int = 200) -> List[Dict[str, Any]]:
    """Build a recursive file tree from root_path, limited by depth and item count."""
    items: List[Dict[str, Any]] = []
    count = 0

    def _walk(current: Path, depth: int, rel: str):
        nonlocal count
        if depth > max_depth or count >= max_items:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if count >= max_items:
                return
            if entry.name.startswith(".") and entry.name not in (".env", ".env.example"):
                continue
            name = entry.name
            rel_path = f"{rel}/{name}" if rel else name
            is_dir = entry.is_dir()
            node: Dict[str, Any] = {
                "name": name,
                "path": rel_path,
                "type": "directory" if is_dir else "file",
            }
            if not is_dir:
                try:
                    node["size"] = entry.stat().st_size
                except Exception:
                    node["size"] = 0
            count += 1
            if is_dir and depth < max_depth:
                children: List[Dict[str, Any]] = []
                old_count = count
                _walk_into(entry, depth + 1, rel_path, children)
                node["children"] = children
                node["item_count"] = count - old_count
            elif is_dir:
                node["children"] = []
                try:
                    node["item_count"] = sum(1 for _ in entry.iterdir())
                except Exception:
                    node["item_count"] = 0
            items.append(node)

    def _walk_into(current: Path, depth: int, rel: str, target: List[Dict[str, Any]]):
        nonlocal count
        if depth > max_depth or count >= max_items:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return
        for entry in entries:
            if count >= max_items:
                return
            if entry.name.startswith(".") and entry.name not in (".env", ".env.example"):
                continue
            name = entry.name
            rel_path = f"{rel}/{name}" if rel else name
            is_dir = entry.is_dir()
            node: Dict[str, Any] = {
                "name": name,
                "path": rel_path,
                "type": "directory" if is_dir else "file",
            }
            if not is_dir:
                try:
                    node["size"] = entry.stat().st_size
                except Exception:
                    node["size"] = 0
            count += 1
            if is_dir and depth < max_depth:
                children: List[Dict[str, Any]] = []
                old_count = count
                _walk_into(entry, depth + 1, rel_path, children)
                node["children"] = children
                node["item_count"] = count - old_count
            elif is_dir:
                node["children"] = []
                try:
                    node["item_count"] = sum(1 for _ in entry.iterdir())
                except Exception:
                    node["item_count"] = 0
            target.append(node)

    _walk(root_path, 0, "")
    return items


@app.get("/workspace", response_model=WorkspaceResponse)
async def get_workspace():
    """Return the current workspace root, file tree, and permission flags."""
    try:
        from agent.tools import _file_tool_root
        root = _file_tool_root()
        files = _build_file_tree(root, max_depth=2, max_items=150)
        return WorkspaceResponse(
            root=str(root),
            display_name=root.name or str(root),
            files=files,
            writable=bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_file_write", False)),
            terminal=bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_terminal_commands", False)),
        )
    except Exception as e:
        logger.error(f"Workspace error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/workspace", response_model=WorkspaceResponse)
async def set_workspace(request: WorkspaceChangeRequest):
    """Change the FILE_TOOL_ROOT at runtime."""
    try:
        new_root = Path(request.root).expanduser().resolve()
        if not new_root.exists():
            raise HTTPException(status_code=400, detail=f"Path does not exist: {new_root}")
        if not new_root.is_dir():
            raise HTTPException(status_code=400, detail=f"Path is not a directory: {new_root}")
        config.file_tool_root = str(new_root)
        try:
            overrides = read_runtime_override_payload(include_secrets=True, migrate_legacy=True)
            if not isinstance(overrides, dict):
                overrides = {}
            overrides["file_tool_root"] = str(new_root)
            write_runtime_override_payload(overrides)
        except Exception as persist_error:
            logger.warning(f"Failed to persist FILE_TOOL_ROOT override: {persist_error}")
        logger.info(f"FILE_TOOL_ROOT changed to: {new_root}")
        from agent.tools import _file_tool_root
        root = _file_tool_root()
        files = _build_file_tree(root, max_depth=2, max_items=150)
        return WorkspaceResponse(
            root=str(root),
            display_name=root.name or str(root),
            files=files,
            writable=bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_file_write", False)),
            terminal=bool(getattr(config, "enable_system_actions", False) and getattr(config, "allow_terminal_commands", False)),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Workspace change error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/workspace/browse")
async def browse_workspace(path: str = Query(default="", description="Relative path within FILE_TOOL_ROOT to browse")):
    """Browse a specific subdirectory within the workspace."""
    try:
        from agent.tools import _file_tool_root, _safe_file_path
        root = _file_tool_root()
        target = _safe_file_path(path or ".")
        if target is None:
            raise HTTPException(status_code=403, detail="Path not allowed")
        if not target.exists():
            raise HTTPException(status_code=404, detail="Path not found")
        if not target.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")
        files = _build_file_tree(target, max_depth=1, max_items=100)
        rel = str(target.relative_to(root)) if target != root else ""
        return {
            "root": str(root),
            "current": str(target),
            "relative": rel,
            "display_name": target.name or str(target),
            "files": files,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Workspace browse error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/trigger/cron")
async def trigger_cron(_request: CronTickRequest):
    raise HTTPException(
        status_code=410,
        detail="Legacy cron trigger retired; create a Project/Session-scoped Routine",
    )


@app.post("/trigger/webhook")
async def trigger_webhook(_request: Request):
    raise HTTPException(
        status_code=410,
        detail="Legacy generic webhook retired; use a signed Project/Session-scoped Routine webhook",
    )
@app.get("/history", response_model=HistoryResponse)
def get_history(thread_id: Optional[str] = Query(default=None)):
    """
    Complete Session history for page refresh.

    Returns:
      - turns: durable Turn projections (messages, ToolRuns, research, approvals, verification)
      - history: legacy Human/Assistant strings (backward compatible)
    """
    try:
        store = get_state_store()
        session_id = _normalize_thread_id(thread_id)
        agent = get_existing_agent(thread_id)
        if agent is not None:
            with agent._request_lock:
                _apply_thread_scope(agent, thread_id)

        timeline = store.session_timeline(session_id, limit=80)
        turns = list(timeline.get("turns") or [])

        # Legacy string history from durable Turn messages (prefer durable over ephemeral agent memory).
        normalized_history: list[str] = []
        for turn in turns:
            for msg in list(turn.get("messages") or []):
                role = str(msg.get("role") or "").strip().lower()
                text = str(msg.get("text") or "").strip()
                if not text:
                    continue
                if role == "user":
                    normalized_history.append(f"Human: {text}")
                elif role == "assistant":
                    normalized_history.append(f"Assistant: {text}")

        # Fallback: agent conversation buffer when no durable turns yet.
        if not normalized_history and agent is not None:
            try:
                history = agent.get_history()
            except Exception:
                history = []

            def _history_content(item: Any) -> str:
                if isinstance(item, dict):
                    return str(item.get("content") or "")
                return str(item or "")

            def _history_role(item: Any) -> str:
                if isinstance(item, dict):
                    return str(item.get("role") or "").strip().lower()
                text = str(item or "")
                if text.startswith("Human:"):
                    return "human"
                if text.startswith("Assistant:"):
                    return "ai"
                return ""

            def _is_internal_background_turn(item: Any) -> bool:
                role = _history_role(item)
                if role != "human":
                    return False
                low = _history_content(item).lower()
                markers = [
                    "check your memory for any pending follow-ups",
                    "review your recent conversation memories",
                    "based on everything you know about the user, generate one brief",
                    "if something is overdue or coming up, prepare a brief notification",
                    "otherwise reply no_action",
                    "reply no_action",
                ]
                return any(marker in low for marker in markers)

            skip_next_ai = False
            for item in history:
                if _is_internal_background_turn(item):
                    skip_next_ai = True
                    continue
                role = _history_role(item)
                content = _history_content(item).strip()
                if not content:
                    continue
                if skip_next_ai and role == "ai":
                    skip_next_ai = False
                    continue
                if role == "human":
                    normalized_history.append(f"Human: {content}")
                elif role == "ai":
                    normalized_history.append(f"Assistant: {content}")
                else:
                    normalized_history.append(content)

        return HistoryResponse(
            history=normalized_history,
            count=len(normalized_history),
            session=dict(timeline.get("session") or {}),
            session_id=str(timeline.get("session_id") or session_id),
            turns=turns,
        )
    except Exception as e:
        logger.error(f"History error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/history/clear")
def clear_history(thread_id: Optional[str] = Query(default=None)):
    """Clear conversation history."""
    try:
        agent = get_existing_agent(thread_id)
        if agent is None:
            return {"success": True, "message": "Conversation history cleared"}
        _apply_thread_scope(agent, thread_id)
        agent.clear_conversation()
        return {"success": True, "message": "Conversation history cleared"}
    except Exception as e:
        logger.error(f"Clear history error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/research/artifacts")
async def list_research_artifacts_api(
    project_id: str = Query(default=""),
    session_id: str = Query(...),
    limit: int = Query(default=50, ge=1, le=200),
):
    from agent.research_artifacts import list_research_artifacts_for_scope

    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    rows = list_research_artifacts_for_scope(
        project_id=scoped_project_id,
        session_id=session_id,
        limit=limit,
    )
    return {"items": [r.model_dump(mode="json") for r in rows], "count": len(rows)}


@app.get("/research/artifacts/{artifact_id}")
async def get_research_artifact_api(
    artifact_id: str,
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    from agent.research_artifacts import get_research_artifact_for_scope

    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    art = get_research_artifact_for_scope(
        artifact_id,
        project_id=scoped_project_id,
        session_id=session_id,
    )
    if art is None:
        raise HTTPException(status_code=404, detail="Research artifact not found")
    return art.model_dump(mode="json")


@app.post("/research/artifacts/lookup")
async def lookup_research_artifact_api(payload: Dict[str, Any] = Body(default_factory=dict)):
    from agent.research_artifacts import find_compatible_research_artifact

    session_id = str(payload.get("session_id") or "")
    project_id = _require_automation_project_scope(session_id, str(payload.get("project_id") or ""))
    art = find_compatible_research_artifact(
        project_id=project_id,
        session_id=session_id,
        objective=str(payload.get("objective") or ""),
        require_project=bool(payload.get("require_project", True)),
    )
    if art is None:
        return {"ok": False, "error_code": "not_found", "artifact": None}
    return {"ok": True, "artifact": art.model_dump(mode="json")}


@app.post("/research/artifacts/{artifact_id}/consume")
async def consume_research_artifact_api(artifact_id: str, payload: Dict[str, Any] = Body(default_factory=dict)):
    """Skill handoff: structured artifact only (never invent citations from prose)."""
    from agent.research_artifacts import consume_research_artifact_for_skill

    session_id = str(payload.get("session_id") or "")
    project_id = _require_automation_project_scope(session_id, str(payload.get("project_id") or ""))
    result = consume_research_artifact_for_skill(
        artifact_id,
        project_id=project_id,
        session_id=session_id,
        skill_id=str(payload.get("skill_id") or ""),
        objective=str(payload.get("objective") or ""),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return result


@app.get("/skills/status")
async def skills_status_api():
    """Truthful skill executable classification (prompt-only never marked executable)."""
    from agent.skill_status_audit import audit_all_skills
    from agent.specialist_runtime import get_specialist_runtime_manager

    rows = audit_all_skills(
        available_capabilities={"approvals", "research"},
        available_artifacts=set(),
    )
    return {
        "items": rows,
        "count": len(rows),
        "executable_ids": [r["id"] for r in rows if r.get("executable")],
        "prompt_only_ids": [r["id"] for r in rows if r.get("status") == "prompt_only"],
        "blocked_ids": [
            r["id"]
            for r in rows
            if str(r.get("status") or "").startswith("blocked") or r.get("status") in {"disabled", "invalid", "deprecated"}
        ],
    }


@app.get("/studio/overview")
async def studio_overview_api(session_id: Optional[str] = Query(default=None)):
    """Read-only Studio/Viewer projection over canonical backend owners."""
    from agent.heartbeat import get_heartbeat_manager
    from agent.automation_runtime import get_automation_run_store
    from agent.connections import get_connection_registry
    from agent.projects import get_project_manager
    from agent.routines import get_routine_manager
    from agent.skill_execution import list_skill_executions_for_session, list_skill_proposals
    from agent.skill_status_audit import audit_all_skills
    from agent.specialist_runtime import get_specialist_runtime_manager
    from agent.task_store import get_task_store
    from agent.tool_registry import ToolRegistry

    key = _normalize_thread_id(session_id)
    state_store = get_state_store()
    state = state_store.get_thread_state(key)
    enabled_funcs = {
        str(getattr(func, "name", None) or getattr(func, "__name__", ""))
        for func in ToolRegistry.get_config_filtered_funcs(config)
    }
    selected = set(state.allowed_tool_names or [])
    tools = []
    for name, entry in sorted(ToolRegistry.get_all().items()):
        missing_flags = [
            flag for flag in entry.policy_flags
            if not bool(getattr(config, str(flag).lower(), False))
        ]
        tools.append(
            {
                "name": name,
                "description": entry.description,
                "owner": entry.owner,
                "category": entry.category,
                "registered": True,
                "available": name in enabled_funcs,
                "executable": name in enabled_funcs,
                "selected": name in selected,
                "running": False,
                "risk_level": entry.risk_level,
                "is_action": entry.is_action,
                "policy_flags": list(entry.policy_flags),
                "blocked_reason": f"Missing configuration: {', '.join(missing_flags)}" if missing_flags else "",
            }
        )

    skills = audit_all_skills(
        available_capabilities={"approvals", "research"},
        available_artifacts=set(),
    )
    active_project_id = str(state.active_project_id or "")
    tasks = (
        get_task_store().list(project_id=active_project_id, session_id=key)
        if active_project_id else []
    )
    routines = [
        routine for routine in get_routine_manager().list_routines()
        if routine.project_id == active_project_id and routine.session_id == key
    ] if active_project_id else []
    automation_runs = (
        get_automation_run_store().list_runs(project_id=active_project_id, session_id=key)
        if active_project_id else []
    )
    connections = (
        get_connection_registry().list(project_id=active_project_id, session_id=key)
        if active_project_id else []
    )
    projects = get_project_manager().list_projects()
    executions = state_store.list_executions(thread_id=key, limit=30)
    heartbeat = get_heartbeat_manager()
    resolution = {}
    if executions:
        resolution = dict((executions[0].metadata or {}).get("echo_resolution") or {})

    return {
        "schema_version": 1,
        "session": state.model_dump(mode="json"),
        "active_project_id": active_project_id,
        "projects": [item.model_dump(mode="json") for item in projects],
        "tasks": [item.model_dump(mode="json") for item in tasks],
        "routines": [item.model_dump(mode="json") for item in routines],
        "automation_runs": [item.model_dump(mode="json") for item in automation_runs],
        "connections": [item.model_dump(mode="json") for item in connections],
        "heartbeat": {
            "enabled": bool(getattr(config, "heartbeat_enabled", False)),
            "running": bool(heartbeat and heartbeat.is_running),
            "last_tick": heartbeat.last_tick if heartbeat else None,
            "next_tick": heartbeat.next_tick if heartbeat else None,
            "history": heartbeat.get_history(limit=10) if heartbeat else [],
        },
        "tools": tools,
        "skills": skills,
        "specialist_runtimes": [
            item.model_dump(mode="json")
            for item in get_specialist_runtime_manager().catalog()
        ],
        "skill_proposals": [item.model_dump(mode="json") for item in list_skill_proposals()],
        "skill_executions": [
            item.model_dump(mode="json")
            for item in list_skill_executions_for_session(key, limit=30)
        ],
        "executions": [item.model_dump(mode="json") for item in executions],
        "resolution": resolution,
        "owners": {
            "projects": "ProjectManager",
            "sessions": "ThreadSessionState",
            "tasks": "TaskStore",
            "routines": "RoutineManager",
            "automation_runs": "AutomationRunStore",
            "connections": "ConnectionRegistry",
            "heartbeat": "HeartbeatManager",
            "tools": "ToolRegistry",
            "skills": "SkillsRegistry",
            "specialist_runtimes": "SpecialistRuntimeManager",
            "executions": "StateStore",
        },
    }


@app.get("/automations/runs")
async def list_automation_runs_api(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Exact-scope read model for durable Task/Routine occurrences."""
    from agent.automation_runtime import get_automation_run_store

    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    rows = get_automation_run_store().list_runs(
        project_id=scoped_project_id,
        session_id=session_id,
    )
    return {"items": [row.model_dump(mode="json") for row in rows], "count": len(rows)}


@app.get("/work/occurrences")
async def list_work_occurrences_api(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Canonical background occurrence view without a second status owner."""

    from agent.automation_runtime import get_automation_run_store
    from agent.task_runs import get_task_run_store

    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    rows = get_automation_run_store().list_runs(
        project_id=scoped_project_id, session_id=session_id
    )
    items: list[dict[str, Any]] = []
    for run in rows:
        task = None
        if run.task_run_id:
            task = get_task_run_store().get(
                run.task_run_id,
                session_id=session_id,
                project_id=scoped_project_id,
            )
        items.append({
            "occurrence": run.model_dump(mode="json"),
            "task_run": _task_run_summary(task).model_dump(mode="json") if task else None,
        })
    return {"items": items, "count": len(items)}


@app.get("/media/jobs")
async def list_media_jobs_api(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=200),
):
    """Unified projection; GenerationJob and VoiceJob remain the owners."""

    from agent.generation_runtime import get_generation_job_store
    from agent.media_jobs import project_generation_job, project_voice_job
    from agent.voice_runtime import get_voice_job_store

    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    rows = [
        *(project_generation_job(row) for row in get_generation_job_store().list(session_id=session_id, limit=limit)),
        *(project_voice_job(row) for row in get_voice_job_store().list(session_id=session_id, limit=limit)),
    ]
    rows = [row for row in rows if row.project_id == scoped_project_id]
    rows.sort(key=lambda row: (row.updated_at, row.job_id), reverse=True)
    return {
        "items": [row.model_dump(mode="json") for row in rows[:limit]],
        "count": min(len(rows), limit),
    }


@app.get("/connections")
async def list_connections_api(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Secret-free Connection capabilities in the active exact scope."""
    from agent.connection_lifecycle import get_connection_lifecycle_service
    from agent.connections import get_connection_registry

    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    get_connection_lifecycle_service().migrate_legacy_settings(config)
    rows = get_connection_registry().list(
        project_id=scoped_project_id,
        session_id=session_id,
    )
    return {"items": [row.model_dump(mode="json") for row in rows], "count": len(rows)}


class ConnectionAuthorizationRequest(BaseModel):
    provider_id: str
    session_id: str
    project_id: str = ""
    display_name: str = ""
    configuration: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] = Field(default_factory=dict)
    allow_global: bool = False


class ConnectionRevisionRequest(BaseModel):
    session_id: str
    project_id: str = ""
    expected_revision: int = Field(ge=1)


class ConnectionCapabilityUpdateRequest(ConnectionRevisionRequest):
    enabled: bool


class ConnectionAuthorizationCallbackRequest(BaseModel):
    provider_id: str
    transaction_id: str
    code: str = ""
    state: str = ""
    error: str = ""


def _connection_api_error(exc: Exception) -> HTTPException:
    from agent.connections import (
        ConnectionConflictError,
        ConnectionScopeError,
    )

    if isinstance(exc, ConnectionConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ConnectionScopeError):
        return HTTPException(status_code=403, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _require_connection_scope(
    connection_id: str,
    *,
    session_id: str,
    project_id: str,
):
    from agent.connections import ConnectionScopeError, get_connection_registry

    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    record = get_connection_registry().get(
        connection_id,
        project_id=scoped_project_id,
        session_id=session_id,
    )
    if record is None:
        raise ConnectionScopeError("Connection not found in the active Project and Session")
    return scoped_project_id, record


@app.get("/connections/catalog")
async def connection_catalog_api(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Consumer catalog joined to secret-free canonical Connection state."""
    from agent.connection_lifecycle import get_connection_lifecycle_service

    service = get_connection_lifecycle_service()
    service.migrate_legacy_settings(config)
    scoped_project_id = ""
    if str(project_id or "").strip():
        scoped_project_id = _require_automation_project_scope(session_id, project_id)
    else:
        from agent.threads import get_thread_manager

        if get_thread_manager().get_thread(str(session_id or "").strip()) is None:
            raise HTTPException(status_code=404, detail="Session not found")
    items = service.catalog(project_id=scoped_project_id, session_id=session_id)
    return {"items": items, "count": len(items)}


@app.get("/settings/catalog")
async def settings_catalog_api(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Read-only, secret-free Settings cards projected from canonical owners."""
    from agent.connection_lifecycle import get_connection_lifecycle_service
    from agent.settings_catalog import build_settings_catalog
    from agent.threads import get_thread_manager

    session_key = str(session_id or "").strip()
    if get_thread_manager().get_thread(session_key) is None:
        raise HTTPException(status_code=404, detail="Session not found")

    service = get_connection_lifecycle_service()
    scoped_project_id = (
        _require_automation_project_scope(session_key, project_id)
        if str(project_id or "").strip()
        else ""
    )
    connection_items = service.catalog(
        project_id=scoped_project_id,
        session_id=session_key,
    )
    provider_projection = await get_provider_info(session_key)
    projection = build_settings_catalog(
        runtime_config=config,
        provider_info=provider_projection.model_dump(mode="json"),
        connection_catalog=connection_items,
    )
    return projection.model_dump(mode="json")


@app.post("/connections/authorize")
async def begin_connection_authorization_api(request: ConnectionAuthorizationRequest):
    """Begin one explicit setup transaction; credentials never enter a projection."""
    from agent.connection_lifecycle import get_connection_lifecycle_service

    try:
        scoped_project_id = _require_automation_project_scope(
            request.session_id,
            request.project_id,
        )
        result = get_connection_lifecycle_service().begin(
            provider_id=request.provider_id,
            project_id=scoped_project_id,
            session_id=request.session_id,
            display_name=request.display_name,
            configuration=request.configuration,
            credentials=request.credentials,
            allow_global=request.allow_global,
        )
        return result.model_dump(mode="json")
    except Exception as exc:
        raise _connection_api_error(exc) from exc


@app.post("/connections/authorization/callback")
async def complete_connection_authorization_api(
    _request: ConnectionAuthorizationCallbackRequest,
):
    """Reserved provider callback boundary.

    No generic callback is accepted because OAuth code exchange, redirect and
    state validation belong to a concrete provider adapter. This endpoint is
    deliberately fail-closed until that adapter owns the transaction.
    """
    raise HTTPException(
        status_code=501,
        detail="This provider OAuth callback adapter is not installed; no Connection state changed",
    )


@app.post("/connections/{connection_id}/probe")
async def probe_connection_api(
    connection_id: str,
    request: ConnectionRevisionRequest,
):
    from agent.connection_lifecycle import get_connection_lifecycle_service
    from agent.connections import get_connection_registry

    try:
        _require_connection_scope(
            connection_id,
            session_id=request.session_id,
            project_id=request.project_id,
        )
        record = get_connection_lifecycle_service().probe(
            connection_id,
            expected_revision=request.expected_revision,
        )
        return {"connection": get_connection_registry()._project(record).model_dump(mode="json")}
    except Exception as exc:
        raise _connection_api_error(exc) from exc


@app.put("/connections/{connection_id}/capabilities/{capability_id}")
async def update_connection_capability_api(
    connection_id: str,
    capability_id: str,
    request: ConnectionCapabilityUpdateRequest,
):
    from agent.connection_lifecycle import get_connection_lifecycle_service
    from agent.connections import get_connection_registry

    try:
        _require_connection_scope(
            connection_id,
            session_id=request.session_id,
            project_id=request.project_id,
        )
        record = get_connection_lifecycle_service().set_capability(
            connection_id,
            capability_id,
            expected_revision=request.expected_revision,
            enabled=request.enabled,
        )
        return {"connection": get_connection_registry()._project(record).model_dump(mode="json")}
    except Exception as exc:
        raise _connection_api_error(exc) from exc


@app.post("/connections/{connection_id}/reconnect")
async def reconnect_connection_api(
    connection_id: str,
    request: ConnectionRevisionRequest,
):
    from agent.connection_lifecycle import get_connection_lifecycle_service
    from agent.connections import get_connection_registry

    try:
        _require_connection_scope(
            connection_id,
            session_id=request.session_id,
            project_id=request.project_id,
        )
        record = get_connection_lifecycle_service().reconnect(
            connection_id,
            expected_revision=request.expected_revision,
        )
        return {"connection": get_connection_registry()._project(record).model_dump(mode="json")}
    except Exception as exc:
        raise _connection_api_error(exc) from exc


@app.post("/connections/{connection_id}/disable")
async def disable_connection_api(
    connection_id: str,
    request: ConnectionRevisionRequest,
):
    from agent.connection_lifecycle import get_connection_lifecycle_service
    from agent.connections import get_connection_registry

    try:
        _require_connection_scope(
            connection_id,
            session_id=request.session_id,
            project_id=request.project_id,
        )
        record = get_connection_lifecycle_service().disable(
            connection_id,
            expected_revision=request.expected_revision,
        )
        return {"connection": get_connection_registry()._project(record).model_dump(mode="json")}
    except Exception as exc:
        raise _connection_api_error(exc) from exc


@app.delete("/connections/{connection_id}")
async def disconnect_connection_api(
    connection_id: str,
    session_id: str = Query(...),
    project_id: str = Query(default=""),
    expected_revision: int = Query(..., ge=1),
):
    from agent.connection_lifecycle import get_connection_lifecycle_service

    try:
        _require_connection_scope(
            connection_id,
            session_id=session_id,
            project_id=project_id,
        )
        removed = get_connection_lifecycle_service().disconnect(
            connection_id,
            expected_revision=expected_revision,
        )
        return {"disconnected": True, "connection_id": removed.id}
    except Exception as exc:
        raise _connection_api_error(exc) from exc


@app.get("/skills/executions")
async def skill_executions_api(session_id: str = Query(...), limit: int = Query(default=40, ge=1, le=200)):
    """Session-scoped projection of durable governed SkillExecution records."""
    from agent.skill_execution import list_skill_executions_for_session
    from agent.threads import get_thread_manager

    key = str(session_id or "").strip()
    if not key or get_thread_manager().get_thread(key) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    rows = list_skill_executions_for_session(key, limit=limit)
    return {"items": [row.model_dump(mode="json") for row in rows], "count": len(rows)}


@app.post("/skills/executions/{skill_execution_id}/cancel")
async def cancel_skill_execution_api(skill_execution_id: str, session_id: str = Query(...)):
    from agent.skill_execution import SkillExecutionError, cancel_skill_execution, get_skill_execution

    record = get_skill_execution(skill_execution_id)
    if record is None:
        raise HTTPException(status_code=404, detail="SkillExecutionRecord not found")
    if record.session_id != str(session_id or "").strip():
        raise HTTPException(status_code=403, detail="SkillExecution belongs to another Session")
    try:
        updated = cancel_skill_execution(record.id, state_store=get_state_store())
    except SkillExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return updated.model_dump(mode="json")


@app.get("/diagnostics/tool-calling")
async def tool_calling_diagnostics_api(thread_id: Optional[str] = Query(default=None)):
    """Honest provider tool-calling capability matrix for operators."""
    agent = get_existing_agent(thread_id) or get_agent(thread_id)
    diag = agent._tool_calling_diagnostics() if hasattr(agent, "_tool_calling_diagnostics") else {}
    provider = str(diag.get("provider") or getattr(agent, "llm_provider", ""))
    disabled = bool(diag.get("disable_native_tool_calling"))
    native_supported = bool(diag.get("native_tool_calling_supported"))
    native_enabled = bool(diag.get("native_tool_calling_enabled")) and not disabled
    matrix = {
        "provider": provider,
        "execution_loop": "canonical_model_control_plane",
        "native_tool_calls": native_enabled,
        "native_tool_calls_supported": native_supported,
        "strict_agent_decision_validation": True,
        "deterministic_direct_tool_fallback": False,
        "printed_tool_syntax_executable": False,
        "lmstudio_tool_calling_flag": bool(diag.get("lmstudio_tool_calling")),
        "disable_native_tool_calling": disabled,
        "notes": (
            "Native calls and bounded structured decisions enter the same "
            "ToolRun, approval, authority, and completion boundaries."
        ),
    }
    return {"diagnostics": diag, "capability_matrix": matrix, "mode_label": agent._tool_calling_mode_label()}


@app.post("/memory/rebuild-index")
async def memory_rebuild_index():
    """Rebuild FAISS from active canonical records only (forgotten stay out)."""
    agent = get_agent(None)
    if not hasattr(agent, "memory") or agent.memory is None:
        raise HTTPException(status_code=503, detail="Memory store unavailable")
    result = agent.memory.rebuild_faiss_from_canonical()
    return result


def _build_obsidian_sync_plan(session_id: str, project_id: str):
    if not bool(getattr(config, "obsidian_sync_enabled", False)):
        raise HTTPException(status_code=409, detail="Obsidian sync is disabled")
    vault = str(getattr(config, "obsidian_vault_path", "") or "").strip()
    if not vault:
        raise HTTPException(status_code=409, detail="Obsidian vault path is not configured")
    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    state = get_state_store().get_thread_state(session_id)
    agent = get_agent(session_id)
    records = agent.memory.list_items(
        offset=0,
        limit=1000,
        thread_id=session_id,
        project_id=scoped_project_id,
        project_path=str(state.project_path or ""),
        include_global=False,
    )
    from agent.obsidian_sync import ObsidianMemorySync

    adapter = ObsidianMemorySync(Path(vault))
    plan = adapter.plan(records, project_id=scoped_project_id, session_id=session_id)
    return adapter, agent, state, plan


@app.get("/memory/obsidian/plan")
async def obsidian_sync_plan_api(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Return explicit imports, exports, deletions, and conflicts; make no changes."""
    _adapter, _agent, _state, plan = _build_obsidian_sync_plan(session_id, project_id)
    return plan.model_dump(mode="json")


@app.post("/memory/obsidian/apply")
async def obsidian_sync_apply_api(payload: Dict[str, Any] = Body(default_factory=dict)):
    """Apply only selected deterministic actions after fresh scope revalidation."""
    session_id = str(payload.get("session_id") or "").strip()
    project_id = str(payload.get("project_id") or "").strip()
    direction = str(payload.get("direction") or "").strip().lower()
    action_ids = [str(item) for item in (payload.get("action_ids") or []) if str(item)]
    if direction not in {"export", "import"} or not action_ids:
        raise HTTPException(status_code=422, detail="direction and action_ids are required")
    adapter, agent, state, plan = _build_obsidian_sync_plan(session_id, project_id)
    current_ids = {action.id for action in plan.actions}
    if any(action_id not in current_ids for action_id in action_ids):
        raise HTTPException(status_code=409, detail="Obsidian sync plan changed; review it again")
    try:
        if direction == "export":
            manifest = adapter.apply_exports(plan, action_ids)
        else:
            manifest = adapter.apply_imports(
                plan,
                action_ids,
                memory=agent.memory,
                project_path=str(state.project_path or ""),
            )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "manifest": manifest.model_dump(mode="json")}


def _resolve_memory_read_scope(
    thread_id: Optional[str],
    project_id: str = "",
) -> tuple[str, str, str]:
    """Read-only Studio/list scope — never requires a bound Project.

    Returns (session_id, project_id, project_path). Empty project_id lists
    account/global memories plus any Project memories only when a Project is
    active on the Session. Does not mutate memory stores.
    """
    session_id = _normalize_thread_id(thread_id)
    state = get_state_store().get_thread_state(session_id)
    active_project_id = str(getattr(state, "active_project_id", "") or "").strip()
    requested = str(project_id or "").strip()
    if requested and active_project_id and requested != active_project_id:
        raise HTTPException(
            status_code=409,
            detail="Session is not bound to the requested Project",
        )
    scoped_project_id = requested or active_project_id
    if scoped_project_id:
        from agent.projects import get_project_manager

        if get_project_manager().get_project(scoped_project_id) is None:
            # Detached / missing Project: still allow account memory, drop project filter.
            scoped_project_id = ""
    project_path = str(getattr(state, "project_path", "") or "").strip()
    return session_id, scoped_project_id, project_path


def _memory_item_from_payload(payload: dict) -> Optional[MemoryItem]:
    """Project one canonical record to API MemoryItem; skip corrupt rows without erasing."""
    try:
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        mt = str(meta.get("type") or "").strip() if isinstance(meta, dict) else ""
        pinned = meta.get("pinned") if isinstance(meta, dict) else None
        raw_id = str(payload.get("id") or "").strip()
        raw_text = str(payload.get("text") or "")
        if not raw_id:
            return None
        base = {k: v for k, v in payload.items() if k in MemoryItem.model_fields}
        base["id"] = raw_id
        base["text"] = raw_text
        if not isinstance(base.get("metadata"), dict):
            base["metadata"] = dict(meta) if isinstance(meta, dict) else {}
        mi = MemoryItem.model_validate(base)
        mi.memory_type = mt or str(meta.get("curator_type") or "") or None
        mi.pinned = bool(pinned) if pinned is not None else None
        mi.owner_id = str(meta.get("owner_id") or "")
        mi.scope = str(meta.get("scope") or "account")
        mi.project_id = str(meta.get("project_id") or payload.get("project_id") or "")
        mi.source_session_id = str(meta.get("source_session_id") or "")
        mi.source_execution_id = str(meta.get("source_execution_id") or "")
        mi.source_item_id = str(meta.get("source_item_id") or "")
        mi.index_state = str(meta.get("index_state") or "pending")
        mi.supersedes = str(meta.get("supersedes") or "")
        mi.superseded_by = str(meta.get("superseded_by") or "")
        mi.status = str(meta.get("status") or "active")
        mi.checksum = str(meta.get("checksum") or "")
        try:
            mi.version = int(meta.get("version") or 1)
        except Exception:
            mi.version = 1
        mi.subject = str(meta.get("subject") or "") or None
        conf = meta.get("confidence")
        try:
            mi.confidence = float(conf) if conf is not None and conf != "" else None
        except Exception:
            mi.confidence = None
        mi.explicit = bool(meta.get("explicit")) if meta.get("explicit") is not None else None
        attrs = meta.get("structured_attributes")
        mi.structured_attributes = dict(attrs) if isinstance(attrs, dict) else None
        mi.source_text = str(meta.get("source_text") or "") or None
        mi.active = True
        semantic = str(meta.get("semantic_text") or "").strip()
        if semantic:
            mi.text = semantic
        return mi
    except Exception as exc:
        logger.warning("Skipping corrupt memory projection (preserved on disk): {}", exc)
        return None


@app.get("/memory", response_model=MemoryListResponse)
async def list_memory(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    thread_id: Optional[str] = Query(default=None),
    project_id: str = Query(default=""),
):
    """List memories for Studio. Read-only — never mutates canonical records or indexes."""
    try:
        session_id, scoped_project_id, project_path = _resolve_memory_read_scope(thread_id, project_id)
        agent = get_agent(thread_id)
        memory = getattr(agent, "memory", None)
        if memory is None:
            return MemoryListResponse(items=[], count=0, use_faiss=False)
        # Account/global always included; Project rows only when a Project is scoped.
        items = memory.list_items(
            offset=offset,
            limit=limit,
            thread_id=session_id,
            project_id=scoped_project_id,
            project_path=project_path,
            include_global=True,
        )
        out_items: List[MemoryItem] = []
        for i in items:
            payload = (i or {}) if isinstance(i, dict) else {}
            mi = _memory_item_from_payload(payload)
            if mi is not None:
                out_items.append(mi)
        count_fn = getattr(memory, "count_items", None)
        if callable(count_fn):
            count = int(
                count_fn(
                    thread_id=session_id,
                    project_id=scoped_project_id,
                    include_global=True,
                )
                or 0
            )
        else:
            count = len(out_items)
        return MemoryListResponse(
            items=out_items,
            count=count,
            use_faiss=bool(getattr(memory, "use_faiss", False)),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"List memory error: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_list_failed",
                "message": "Memory list failed. Canonical records were not modified.",
                "error": str(e),
            },
        ) from e


def _normalize_memory_audit_text(text: str) -> str:
    cleaned = " ".join(str(text or "").lower().split())
    return cleaned[:500]


def _build_memory_doctor_report(
    agent,
    thread_id: Optional[str],
    project_id: str = "",
    max_scan: int = 300,
) -> MemoryDoctorResponse:
    max_scan = max(10, min(int(max_scan or 300), 1000))
    memory = getattr(agent, "memory", None)
    if memory is None:
        return MemoryDoctorResponse(
            ok=False,
            memory_count=0,
            scanned=0,
            use_faiss=False,
            auto_store_conversations=bool(getattr(config, "memory_auto_store_conversations", False)),
            session_memory={},
            type_counts={},
            pinned_count=0,
            profile_fact_count=0,
            missing_type_count=0,
            duplicate_groups=[],
            warnings=["Memory manager is not initialized."],
            recommendations=["Restart the backend or check memory initialization logs."],
        )

    items = memory.list_items(
        offset=0,
        limit=max_scan,
        thread_id=thread_id,
        project_id=project_id,
        include_global=False,
    )
    type_counts: Dict[str, int] = {}
    pinned_count = 0
    missing_type_count = 0
    duplicate_map: Dict[str, list[dict[str, Any]]] = {}
    active_semantic_samples: List[Dict[str, Any]] = []

    for item in items:
        payload = item if isinstance(item, dict) else {}
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        text = str(meta.get("semantic_text") or payload.get("text") or "")
        mem_type = str(meta.get("curator_type") or meta.get("type") or "").strip() or "unknown"
        type_counts[mem_type] = type_counts.get(mem_type, 0) + 1
        if mem_type == "unknown":
            missing_type_count += 1
        if bool(meta.get("pinned")):
            pinned_count += 1
        if len(active_semantic_samples) < 12:
            active_semantic_samples.append(
                {
                    "id": str(payload.get("id") or ""),
                    "text": text[:240],
                    "type": mem_type,
                    "scope": str(meta.get("scope") or "account"),
                    "source_execution_id": str(meta.get("source_execution_id") or ""),
                    "index_state": str(meta.get("index_state") or "pending"),
                    "explicit": bool(meta.get("explicit")),
                    "confidence": meta.get("confidence"),
                    "active": True,
                    "supersedes": str(meta.get("supersedes") or ""),
                }
            )
        norm = _normalize_memory_audit_text(text)
        if len(norm) >= 24:
            duplicate_map.setdefault(norm, []).append(
                {
                    "id": str(payload.get("id") or ""),
                    "type": mem_type,
                    "preview": text[:180],
                    "timestamp": payload.get("timestamp"),
                }
            )

    superseded_count = 0
    try:
        for rec in (getattr(memory, "_records", None) or {}).values():
            if not memory._record_matches_scope(
                rec,
                project_id=project_id,
                thread_id=str(thread_id or ""),
                include_global=False,
            ):
                continue
            if not bool(rec.get("active", True)) and str(rec.get("supersedes") or rec.get("deleted_at") or ""):
                superseded_count += 1
            elif not bool(rec.get("active", True)):
                superseded_count += 1
    except Exception:
        superseded_count = 0

    session_only_count = 0
    pending_confirmation = None
    try:
        from agent.memory_curator import MemoryCurator

        cur = MemoryCurator(memory)
        sid = str(thread_id or "default")
        session_only_count = len(cur.list_session_only(sid))
        pending_confirmation = cur.get_pending_confirmation(sid)
        if pending_confirmation:
            pending_confirmation = {
                "id": pending_confirmation.get("id"),
                "status": pending_confirmation.get("status"),
                "candidate_count": len(pending_confirmation.get("candidates") or []),
            }
    except Exception:
        pass

    duplicate_groups = []
    for norm, group in duplicate_map.items():
        if len(group) > 1:
            duplicate_groups.append(
                {
                    "count": len(group),
                    "preview": group[0].get("preview", ""),
                    "items": group[:8],
                }
            )
    duplicate_groups.sort(key=lambda g: int(g.get("count") or 0), reverse=True)
    duplicate_groups = duplicate_groups[:12]

    profile_fact_count = int(type_counts.get("profile", 0))

    memory_count = memory.count_items(
        thread_id=thread_id,
        project_id=project_id,
        include_global=False,
    )
    auto_store = bool(getattr(config, "memory_auto_store_conversations", False))
    conversation_count = int(type_counts.get("conversation", 0))
    warnings: list[str] = []
    recommendations: list[str] = []

    if auto_store:
        warnings.append("Raw conversation auto-store is enabled.")
        recommendations.append("Keep raw conversation auto-store off unless explicitly debugging; prefer profile facts and curated memories.")
    if duplicate_groups:
        warnings.append(f"Found {len(duplicate_groups)} exact duplicate-looking memory group(s) in the scanned items.")
        recommendations.append("Run memory compaction or review duplicate groups before injecting more long-term memory.")
    if missing_type_count:
        warnings.append(f"{missing_type_count} scanned memory item(s) are missing a typed memory category.")
        recommendations.append("Backfill missing memory types so retrieval can distinguish profile, preference, project, contacts, and notes.")
    if conversation_count > max(20, len(items) // 2):
        warnings.append("Conversation memories dominate the scanned sample.")
        recommendations.append("Prefer searchable chat history plus curated durable memories instead of storing every conversation turn.")
    if profile_fact_count == 0:
        recommendations.append("Add deterministic profile facts for stable personal recall, such as name and core preferences.")
    if memory_count > 250:
        warnings.append("Memory count is high enough that retrieval quality and startup cost may degrade.")
        recommendations.append("Use memory doctor plus compaction to reduce stale or duplicated memories.")
    if not warnings:
        recommendations.append("Memory looks healthy in the scanned sample.")

    session_memory: Dict[str, Any] = {}
    try:
        distiller = getattr(agent, "_session_memory", None)
        if distiller is not None and bool(getattr(config, "session_memory_enabled", True)):
            session_memory = distiller.doctor(thread_id or getattr(agent, "_current_thread_id", None) or "default")
            if session_memory.get("enabled") and not session_memory.get("exists"):
                recommendations.append("Session memory is enabled and will be created after the next completed turn.")
        else:
            session_memory = {"enabled": False}
    except Exception as exc:
        session_memory = {"enabled": bool(getattr(config, "session_memory_enabled", True)), "error": str(exc)[:200]}

    return MemoryDoctorResponse(
        ok=not bool(warnings),
        memory_count=memory_count,
        scanned=len(items),
        use_faiss=bool(getattr(memory, "use_faiss", False)),
        auto_store_conversations=auto_store,
        session_memory=session_memory,
        type_counts=type_counts,
        pinned_count=pinned_count,
        profile_fact_count=profile_fact_count,
        missing_type_count=missing_type_count,
        duplicate_groups=duplicate_groups,
        warnings=warnings,
        recommendations=recommendations,
        active_semantic_samples=active_semantic_samples,
        superseded_count=superseded_count,
        session_only_count=session_only_count,
        pending_confirmation=pending_confirmation,
    )


@app.get("/memory/doctor", response_model=MemoryDoctorResponse)
async def memory_doctor(
    thread_id: Optional[str] = Query(default=None),
    project_id: str = Query(default=""),
    max_scan: int = Query(default=300, ge=10, le=1000),
):
    """Read-only memory health report for duplicate/stale/untyped memory diagnosis."""
    try:
        session_id, scoped_project_id, _path = _resolve_memory_read_scope(thread_id, project_id)
        agent = get_agent(session_id)
        return _build_memory_doctor_report(
            agent,
            session_id,
            scoped_project_id,
            max_scan=max_scan,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Memory doctor error: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_doctor_failed",
                "message": "Memory doctor failed. Canonical records were not modified.",
                "error": str(e),
            },
        ) from e


@app.post("/memory/delete")
async def delete_memory(request: MemoryDeleteRequest):
    try:
        session_id = _normalize_thread_id(request.thread_id)
        scoped_project_id = _require_automation_project_scope(session_id, request.project_id)
        agent = get_agent(session_id)
        deleted = agent.memory.delete_items(
            request.ids,
            project_id=scoped_project_id,
            thread_id=session_id,
        )
        if deleted != len(set(request.ids)):
            raise HTTPException(status_code=409, detail="One or more memories are outside the active scope")
        return {
            "success": True,
            "deleted": deleted,
            "memory_count": agent.memory.count_items(
                thread_id=session_id,
                project_id=scoped_project_id,
            ),
        }
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/update")
async def update_memory(request: MemoryUpdateRequest):
    try:
        session_id = _normalize_thread_id(request.thread_id)
        scoped_project_id = _require_automation_project_scope(session_id, request.project_id)
        agent = get_agent(session_id)
        ok = agent.memory.update_item(
            request.id,
            text=request.text,
            memory_type=request.memory_type,
            pinned=request.pinned,
            project_id=scoped_project_id,
            thread_id=session_id,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Memory not found in active scope")
        return {
            "success": True,
            "memory_count": agent.memory.count_items(
                thread_id=session_id,
                project_id=scoped_project_id,
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/clear")
async def clear_memory(
    thread_id: Optional[str] = Query(default=None),
    project_id: str = Query(default=""),
):
    try:
        session_id = _normalize_thread_id(thread_id)
        scoped_project_id = _require_automation_project_scope(session_id, project_id)
        agent = get_agent(session_id)
        deleted = agent.memory.clear_scope(
            project_id=scoped_project_id,
            thread_id=session_id,
            include_global=False,
        )
        return {
            "success": True,
            "deleted": deleted,
            "memory_count": agent.memory.count_items(
                thread_id=session_id,
                project_id=scoped_project_id,
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Clear memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/provider", response_model=ProviderInfoResponse)
async def get_provider_info(session_id: Optional[str] = Query(default=None)):
    """
    Get current provider information.

    Returns:
        Current provider details and available providers.
    """
    from agent.model_runtime import list_available_providers, resolve_model_profile

    providers = list_available_providers()
    def _profile_for(prov: ModelProvider, model_name: str) -> dict[str, Any]:
        registry = dict(getattr(config, "model_capability_profiles", {}) or {})
        overrides = dict(registry.get(f"{prov.value}:{model_name}") or registry.get(model_name) or {})
        # Real configured window for every provider — no local 32k / hosted 128k guess.
        trim = int(getattr(config, "llm_trim_max_tokens", 0) or 0)
        ctx_len = int(getattr(config.local, "context_length", 0) or 0)
        real_window = trim or ctx_len
        if real_window > 0:
            overrides.setdefault("context_limit", real_window)
        return resolve_model_profile(prov.value, model_name, overrides).as_dict()

    if _is_lmstudio_only_enabled():
        _force_lmstudio_config()
        binding = _ensure_session_model_binding(session_id or "default")
        providers = [p for p in providers if p.get("id") == ModelProvider.LM_STUDIO.value]
        model_profile = _profile_for(ModelProvider.LM_STUDIO, binding.model_id)
        ctx_w, max_out = int(model_profile["context_limit"]), int(getattr(config.local, "max_tokens", 0) or 4096)
        readiness = _check_provider_readiness(ModelProvider.LM_STUDIO)
        return ProviderInfoResponse(
            provider=ModelProvider.LM_STUDIO.value,
            model=binding.model_id,
            local=True,
            base_url=_provider_configured_base_url(ModelProvider.LM_STUDIO),
            available_providers=providers,
            context_window=ctx_w,
            max_output_tokens=max_out,
            ready=bool(readiness.get("ok")),
            readiness_message=str(readiness.get("message") or ""),
            readiness_detail=str(readiness.get("detail") or ""),
            model_profile=model_profile,
            session_id=binding.session_id,
            binding_revision=binding.binding_revision,
        )

    # Do not instantiate the agent here; provider can be misconfigured (e.g. missing deps)
    # and we still want /provider to respond.
    binding = _ensure_session_model_binding(session_id or "default")
    provider = ModelProvider(binding.provider_id)
    is_local = provider not in (ModelProvider.OPENAI, ModelProvider.GEMINI)
    if provider == ModelProvider.OPENAI:
        model = binding.model_id
    elif provider == ModelProvider.GEMINI:
        model = binding.model_id
    else:
        model = binding.model_id
    base_url = (
        None
        if provider in (ModelProvider.OPENAI, ModelProvider.GEMINI, ModelProvider.LLAMA_CPP)
        else _provider_configured_base_url(provider)
    )
    model_profile = _profile_for(provider, model)
    ctx_w = int(model_profile["context_limit"])
    max_out = int(
        getattr(config.openai, "max_tokens", 0) if provider == ModelProvider.OPENAI
        else getattr(config.gemini, "max_tokens", 0) if provider == ModelProvider.GEMINI
        else getattr(config.local, "max_tokens", 0)
        or 4096
    )
    readiness = _check_provider_readiness(provider, model_id=model)

    return ProviderInfoResponse(
        provider=provider.value,
        model=model,
        local=is_local,
        base_url=base_url,
        available_providers=providers,
        context_window=ctx_w,
        max_output_tokens=max_out,
        ready=bool(readiness.get("ok")),
        readiness_message=str(readiness.get("message") or ""),
        readiness_detail=str(readiness.get("detail") or ""),
        model_profile=model_profile,
        session_id=binding.session_id,
        binding_revision=binding.binding_revision,
    )


@app.post("/provider/switch")
async def switch_provider(request: SwitchProviderRequest):
    """
    Switch to a different model provider.

    Args:
        request: Switch provider request.

    Returns:
        Success message.
    """
    try:
        provider = ModelProvider(request.provider)
        if (
            _is_lmstudio_only_enabled()
            and provider != ModelProvider.LM_STUDIO
        ):
            raise HTTPException(
                status_code=403,
                detail="Only LM Studio models may be selected in LM Studio-only mode",
            )
        _assert_provider_available(provider)
        session_id = _normalize_thread_id(request.session_id)
        current_binding = _ensure_session_model_binding(session_id)
        if current_binding.binding_revision != request.expected_revision:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Session model binding changed from revision "
                    f"{request.expected_revision} to {current_binding.binding_revision}"
                ),
            )
        requested_model = (
            request.openai_model if provider == ModelProvider.OPENAI
            else request.gemini_model if provider == ModelProvider.GEMINI
            else request.model
        )
        selected_model = str(
            requested_model or _default_model_for_provider(provider)
        ).strip()
        if not selected_model:
            raise HTTPException(status_code=422, detail="A model id is required")
        if request.base_url:
            configured = str(
                _provider_configured_base_url(provider)
                if provider not in {ModelProvider.OPENAI, ModelProvider.GEMINI}
                else ""
            ).rstrip("/")
            if configured and request.base_url.rstrip("/") != configured:
                raise HTTPException(
                    status_code=409,
                    detail="Provider endpoints are global configuration; change the endpoint in Settings before binding this Session",
                )
        if (
            current_binding.provider_id == provider.value
            and current_binding.model_id == selected_model
        ):
            return {
                "success": True,
                "message": "Session already uses that provider and model",
                "provider": provider.value,
                "model": current_binding.model_id,
                "session_id": session_id,
                "binding_revision": current_binding.binding_revision,
                "cancelled_turns": 0,
                "cancelled_incompatible_work": {"approvals": 0, "task_runs": 0},
            }
        cancelled = _cancel_active_queries_for_session(session_id)
        if cancelled:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                with _ACTIVE_QUERY_CANCEL_LOCK:
                    if not any(
                        row[0] == session_id
                        for row in _ACTIVE_QUERY_CANCELLATIONS.values()
                    ):
                        break
                await asyncio.sleep(0.05)
        binding = get_state_store().update_session_model_binding(
            session_id,
            provider_id=provider.value,
            model_id=selected_model,
            expected_revision=request.expected_revision,
            provider_configuration_id=current_binding.provider_configuration_id,
        )
        retired = _cancel_incompatible_session_work(
            session_id,
            reason=(
                "Session model binding changed from revision "
                f"{current_binding.binding_revision} to {binding.binding_revision}"
            ),
        )

        with _agent_pool_lock:
            _agent_pool.pop(session_id, None)
        from agent.model_runtime import clear_structured_output_probe_cache
        clear_structured_output_probe_cache()

        return {
            "success": True,
            "message": f"Switched to {provider.value}",
            "provider": provider.value,
            "model": binding.model_id,
            "session_id": session_id,
            "binding_revision": binding.binding_revision,
            "cancelled_turns": cancelled,
            "cancelled_incompatible_work": retired,
        }
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid provider: {request.provider}")
    except Exception as e:
        logger.error(f"Switch provider error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/provider/models")
async def list_provider_models(provider: Optional[str] = Query(default=None)):
    p = None
    if _is_lmstudio_only_enabled():
        p = ModelProvider.LM_STUDIO
    elif provider:
        try:
            p = ModelProvider(provider)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid provider: {provider}")
    else:
        p = _runtime_provider or (config.local.provider if config.use_local_models else _default_cloud_provider())

    if p == ModelProvider.OLLAMA:
        try:
            import requests

            base = _provider_configured_base_url(ModelProvider.OLLAMA)
            resp = requests.get(f"{base}/api/tags", timeout=4)
            resp.raise_for_status()
            data = resp.json() or {}
            models = set()
            for m in data.get("models") or []:
                name = m.get("name")
                if name:
                    models.add(name)
            return {"provider": p.value, "models": sorted(models)}
        except Exception as e:
            logger.warning(f"Failed to list Ollama models: {e}")
            return {"provider": p.value, "models": []}

    if p in (ModelProvider.LM_STUDIO, ModelProvider.LOCALAI, ModelProvider.VLLM):
        try:
            import requests

            base = _provider_configured_base_url(p)
            if base.endswith("/v1"):
                url = f"{base}/models"
            else:
                url = f"{base}/v1/models"

            resp = requests.get(url, timeout=4)
            resp.raise_for_status()
            data = resp.json() or {}
            models = []
            for m in data.get("data") or []:
                model_id = m.get("id")
                if model_id:
                    models.append(model_id)
            return {"provider": p.value, "models": sorted(set(models))}
        except Exception as e:
            logger.warning(f"Failed to list {p.value} models: {e}")
            return {"provider": p.value, "models": []}

    return {"provider": p.value, "models": []}


@app.post("/vision/analyze", response_model=ScreenAnalysisResponse)
async def analyze_screen():
    """
    Capture screen and perform OCR analysis.

    Returns:
        Analysis results with extracted text.
    """
    try:
        vision = get_vision_manager()
        result = vision.capture_and_analyze()

        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])

        return ScreenAnalysisResponse(
            text=result.get("text", ""),
            text_length=result.get("text_length", 0),
            has_text=result.get("has_text", False),
            image_size=result.get("image_size", {})
        )
    except Exception as e:
        logger.error(f"Screen analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/vision/capture", response_model=ScreenCaptureResponse)
async def capture_screen():
    """
    Capture screen and return as base64 encoded image.

    Returns:
        Base64 encoded image.
    """
    try:
        import cv2
        from PIL import Image

        vision = get_vision_manager()
        image = vision.capture_and_analyze()

        if "error" in image:
            return ScreenCaptureResponse(success=False, error=image["error"])

        import numpy as np
        from io import BytesIO
        import base64

        img_array = np.array(vision.last_capture)
        img_rgb = cv2.cvtColor(img_array, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)

        buffer = BytesIO()
        pil_img.save(buffer, format="PNG")
        img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        return ScreenCaptureResponse(
            success=True,
            image_base64=f"data:image/png;base64,{img_base64}"
        )
    except Exception as e:
        logger.error(f"Capture error: {e}")
        return ScreenCaptureResponse(success=False, error=str(e))


@app.get("/vision/info")
async def get_screen_info():
    """
    Get screen/monitor information.

    Returns:
        Screen information.
    """
    try:
        vision = get_vision_manager()
        return vision.get_screen_info()
    except Exception as e:
        logger.error(f"Screen info error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/startup/readiness")
async def startup_readiness():
    """Authoritative durable-owner readiness; optional providers never block it."""
    from agent.startup_readiness import build_startup_readiness

    return build_startup_readiness()


@app.get("/metrics")
async def metrics():
    with _metrics_lock:
        counts = dict(_metrics)
        samples = list(_tool_latency_ms)

    stats = {"count": len(samples)}
    if samples:
        samples.sort()
        n = len(samples)
        avg = sum(samples) / max(1, n)
        def pick(p: float) -> float:
            idx = int(max(0, min(n - 1, round((n - 1) * p))))
            return float(samples[idx])

        stats.update(
            {
                "avg_ms": round(avg, 2),
                "p50_ms": round(pick(0.50), 2),
                "p90_ms": round(pick(0.90), 2),
                "p99_ms": round(pick(0.99), 2),
            }
        )
    return {"requests": counts.get("requests", 0), "errors": counts.get("errors", 0), "tool_calls": counts.get("tool_calls", 0), "tool_errors": counts.get("tool_errors", 0), "tool_latency_ms": stats}


# ── Todo List Endpoints ──────────────────────────────────────────────────────

class TodoItem(BaseModel):
    id: str = ""
    title: str = ""
    description: str = ""
    status: str = "pending"  # pending | in_progress | done
    priority: str = "medium"  # low | medium | high
    project_id: str = ""
    session_id: str = ""
    created_at: str = ""
    updated_at: str = ""


class TodoUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None


@app.get("/todos")
async def list_todos(
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """List Product Tasks in the exact active Project/Session scope."""
    from agent.task_store import get_task_store

    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    return {
        "todos": [
            task.model_dump(mode="json")
            for task in get_task_store().list(
                project_id=scoped_project_id,
                session_id=session_id,
            )
        ]
    }


@app.post("/todos")
async def create_todo(item: TodoItem):
    """Create a new todo item."""
    if not (item.title or "").strip():
        raise HTTPException(status_code=400, detail="Todo title is required")
    from agent.task_store import get_task_store
    scoped_project_id = _require_automation_project_scope(item.session_id, item.project_id)

    entry = get_task_store().create(
        id=item.id or str(uuid.uuid4()),
        title=item.title,
        description=item.description,
        status=item.status,
        priority=item.priority,
        project_id=scoped_project_id,
        session_id=item.session_id,
        source="user",
    )
    return {"todo": entry.model_dump(mode="json")}


@app.put("/todos/{todo_id}")
async def update_todo(
    todo_id: str,
    item: TodoUpdateRequest,
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Update a todo item by ID."""
    from agent.task_store import get_task_store

    store = get_task_store()
    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    current = store.get(todo_id)
    if current is None or current.project_id != scoped_project_id or current.session_id != session_id:
        raise HTTPException(status_code=404, detail="Todo not found")
    if current.source != "user" or current.automation_run_ids or current.task_run_ids:
        raise HTTPException(
            status_code=409,
            detail="Automation-backed Task records are read-only projections; edit the owning schedule or TaskRun",
        )
    task = store.update(todo_id, **item.model_dump())
    if task is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"todo": task.model_dump(mode="json")}


@app.delete("/todos/{todo_id}")
async def delete_todo(
    todo_id: str,
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Delete a todo item by ID."""
    from agent.task_store import get_task_store

    store = get_task_store()
    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    current = store.get(todo_id)
    if current is None or current.project_id != scoped_project_id or current.session_id != session_id:
        raise HTTPException(status_code=404, detail="Todo not found")
    if current.source != "user" or current.automation_run_ids or current.task_run_ids:
        raise HTTPException(
            status_code=409,
            detail="Automation-backed Task records cannot be deleted from the Checklist",
        )
    if not store.delete(todo_id):
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"deleted": todo_id}


@app.post("/todos/reorder")
async def reorder_todos(
    request: Request,
    session_id: str = Query(...),
    project_id: str = Query(default=""),
):
    """Reorder todos by providing a list of IDs in order."""
    data = await request.json()
    order = data.get("order", [])
    from agent.task_store import get_task_store

    store = get_task_store()
    scoped_project_id = _require_automation_project_scope(session_id, project_id)
    scoped = store.list(project_id=scoped_project_id, session_id=session_id)
    scoped_ids = {task.id for task in scoped}
    requested = [str(item) for item in order]
    if any(item not in scoped_ids for item in requested):
        raise HTTPException(status_code=409, detail="Task reorder crosses Project/Session scope")
    store.reorder_scope(
        requested,
        project_id=scoped_project_id,
        session_id=session_id,
    )
    return {
        "todos": [
            task.model_dump(mode="json")
            for task in store.list(project_id=scoped_project_id, session_id=session_id)
        ]
    }


# ── Avatar Config Endpoints ─────────────────────────────────────────────────

_AVATAR_CONFIG_FILE = DATA_DIR / "avatar_config.json"

_DEFAULT_AVATAR_CONFIG = {
    "body_color": "#ffffff",
    "eye_color": "#000000",
    "bg_color": "#0a0a0a",
    "glow_color": "#4f8eff",
    "idle_activity": "auto",
    "breathing_speed": 1.0,
    "eye_size": 1.0,
    "body_roundness": 14,
    "enable_glow": True,
    "enable_idle_activities": True,
    "custom_status_text": "",
}


def _load_avatar_config() -> dict:
    if _AVATAR_CONFIG_FILE.exists():
        try:
            return json.loads(_AVATAR_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return dict(_DEFAULT_AVATAR_CONFIG)


def _save_avatar_config(cfg: dict) -> None:
    _AVATAR_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _AVATAR_CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


@app.get("/avatar/config")
async def get_avatar_config():
    """Get avatar customization config."""
    return _load_avatar_config()


@app.put("/avatar/config")
async def update_avatar_config(request: Request):
    """Update avatar customization config."""
    data = await request.json()
    cfg = _load_avatar_config()
    cfg.update(data)
    _save_avatar_config(cfg)
    return cfg


@app.post("/avatar/config/reset")
async def reset_avatar_config():
    """Reset avatar config to defaults."""
    _save_avatar_config(dict(_DEFAULT_AVATAR_CONFIG))
    return _DEFAULT_AVATAR_CONFIG


def start_server(host: str = None, port: int = None):
    """
    Start the FastAPI server.

    Args:
        host: Host to bind to.
        port: Port to listen on.
    """
    import uvicorn
    host = host or config.api.host
    port = port or config.api.port
    import ipaddress
    normalized_host = str(host or "").strip().strip("[]").lower()
    try:
        loopback = normalized_host == "localhost" or ipaddress.ip_address(normalized_host).is_loopback
    except ValueError:
        loopback = False
    auth_ready = bool(
        getattr(config, "api_auth_enabled", False)
        and str(getattr(config, "api_auth_key", "") or "").strip()
    )
    if not loopback and not auth_ready:
        raise RuntimeError(
            f"Refusing unauthenticated non-loopback API bind to {host}. "
            "Enable API_AUTH_ENABLED and set API_AUTH_KEY, or bind to 127.0.0.1/::1."
            )
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    start_server()
