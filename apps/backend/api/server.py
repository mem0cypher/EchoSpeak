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
import threading
import time
import uuid
import hmac
import hashlib
from datetime import datetime
from pathlib import Path
from io import BytesIO
from collections import deque, OrderedDict
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import URLError, HTTPError

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from fastapi import FastAPI, HTTPException, Query, Response, Request, UploadFile, File, WebSocket, WebSocketDisconnect, Header, Depends
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
    ModelProvider,
    SECRET_NESTED_SETTINGS,
    SECRET_TOP_LEVEL_SETTINGS,
    read_runtime_override_payload,
    write_runtime_override_payload,
)
from agent.research import build_research_run
from agent.state import get_state_store

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
        allowlist = s.get("terminal_command_allowlist")
        if not isinstance(allowlist, list) or not any(str(x).strip() for x in allowlist):
            issues.append({"key": "terminal_command_allowlist", "message": "Terminal commands are enabled but TERMINAL_COMMAND_ALLOWLIST is empty.", "severity": "error"})
        elif any(str(x).strip() == "*" for x in allowlist):
            issues.append({"key": "terminal_command_allowlist", "message": "TERMINAL_COMMAND_ALLOWLIST contains '*', which effectively disables first-token command restrictions.", "severity": "warning"})
        root = str(s.get("file_tool_root") or "").strip()
        if not root:
            issues.append({"key": "file_tool_root", "message": "Set FILE_TOOL_ROOT to restrict terminal/file operations.", "severity": "warning"})

    if bool(s.get("allow_file_write")):
        root = str(s.get("file_tool_root") or "").strip()
        if not root:
            issues.append({"key": "file_tool_root", "message": "Set FILE_TOOL_ROOT to restrict file writes.", "severity": "warning"})

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
        allowlist = s.get("open_application_allowlist")
        if not isinstance(allowlist, list) or not any(str(x).strip() for x in allowlist):
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

    tavily_key = str(s.get("tavily_api_key") or "").strip()
    if not tavily_key:
        issues.append({"key": "tavily_api_key", "message": "Tavily is the active web search integration and requires TAVILY_API_KEY.", "severity": "error"})

    if str(s.get("default_cloud_provider") or "").strip().lower() == "gemini" and bool(s.get("gemini_use_langgraph")):
        issues.append({"key": "gemini_use_langgraph", "message": "Gemini LangGraph tool-calling is enabled. If tool calls fail, turn this off to use AgentExecutor instead.", "severity": "warning"})

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

    When MULTI_AGENT_ENABLED=true, agents are pooled per thread_id.
    """
    global _agent
    global _runtime_provider
    from agent.core import EchoSpeakAgent

    if not bool(getattr(config, "multi_agent_enabled", True)):
        if _agent is None:
            if _is_lmstudio_only_enabled():
                _force_lmstudio_config()
                provider = ModelProvider.LM_STUDIO
            elif _runtime_provider is not None:
                provider = _runtime_provider
            else:
                provider = config.local.provider if config.use_local_models else _default_cloud_provider()
            _agent = EchoSpeakAgent(llm_provider=provider, manage_background_services=True)
        return _agent

    key = _normalize_thread_id(thread_id)
    with _agent_pool_lock:
        existing = _agent_pool.pop(key, None)
        if existing is not None:
            _agent_pool[key] = existing
            return existing

        if _is_lmstudio_only_enabled():
            _force_lmstudio_config()
            provider = ModelProvider.LM_STUDIO
        elif _runtime_provider is not None:
            provider = _runtime_provider
        else:
            provider = config.local.provider if config.use_local_models else _default_cloud_provider()

        agent = EchoSpeakAgent(
            llm_provider=provider,
            manage_background_services=(key == "default"),
        )
        _agent_pool[key] = agent
        while len(_agent_pool) > _agent_pool_max:
            _agent_pool.popitem(last=False)
        return agent


def get_existing_agent(thread_id: Optional[str] = None):
    """Get an already-initialized agent without creating a new one."""
    global _agent

    if not bool(getattr(config, "multi_agent_enabled", True)):
        return _agent

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

    with _heartbeat_runtime_lock:
        hb = get_heartbeat_manager()
        if not desired_enabled:
            if hb is not None and hb.is_running:
                hb.stop()
            return

        agent = get_agent()
        hb = get_heartbeat_manager()
        if hb is None:
            hb = HeartbeatManager(agent=agent)
            set_heartbeat_manager(hb)
        else:
            hb.set_agent(agent)
            hb.update_config(
                interval_minutes=getattr(config, "heartbeat_interval", 30),
                prompt=getattr(config, "heartbeat_prompt", ""),
                channels=list(getattr(config, "heartbeat_channels", ["web"])),
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
    agent.activate_project(project_id or None)
    updated = store.update_thread_state(
        normalized_thread_id,
        workspace_id=str(getattr(agent, "_workspace_id", None) or ""),
        active_project_id=str(getattr(agent, "_active_project_id", None) or ""),
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


class _StreamingHandler(BaseCallbackHandler):
    def __init__(self, q: queue.Queue, request_id: str):
        self._q = q
        self._request_id = request_id
        self._tool_run_map: dict = {}
        self._tool_started_at: dict = {}
        self._tool_input_map: dict = {}
        self._research_runs: list[dict[str, Any]] = []

    @property
    def research_runs(self) -> list[dict[str, Any]]:
        return list(self._research_runs)

    def on_tool_start(self, serialized: dict, input_str: str, run_id: str, parent_run_id: Optional[str] = None, **kwargs):
        tool_name = (serialized or {}).get("name") or (serialized or {}).get("id") or "tool"
        call_id = str(run_id)
        self._tool_run_map[call_id] = tool_name
        self._tool_started_at[call_id] = time.perf_counter()
        raw_input = input_str if isinstance(input_str, str) else str(input_str)
        self._tool_input_map[call_id] = raw_input
        _metric_inc("tool_calls", 1)

        inp = raw_input
        inp = " ".join((inp or "").split())
        if len(inp) > 600:
            inp = inp[:600] + "…"
        self._q.put(
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
        self._q.put({"type": "status", "agent_mode": mode, "tool": tool_name, "at": time.time(), "request_id": self._request_id})

    def on_tool_end(self, output: str, run_id: str, parent_run_id: Optional[str] = None, **kwargs):
        call_id = str(run_id)
        out = output if isinstance(output, str) else str(output)
        tool_name = self._tool_run_map.get(call_id, "")
        raw_input = self._tool_input_map.pop(call_id, "")
        max_len = 8000 if tool_name == "web_search" else 800
        if len(out) > max_len:
            out = out[:max_len] + "…"
        started = self._tool_started_at.pop(call_id, None)
        if started is not None:
            _record_tool_latency((time.perf_counter() - started) * 1000.0)
        event = {"type": "tool_end", "id": call_id, "name": tool_name, "output": out, "at": time.time(), "request_id": self._request_id}
        research_run = build_research_run(run_id=call_id, tool_name=tool_name, tool_input=raw_input, output=output if isinstance(output, str) else str(output), at=event["at"])
        if research_run is not None:
            event["research"] = research_run
            self._research_runs.append(research_run)
        self._q.put(event)

    def on_tool_error(self, error: BaseException, run_id: str, parent_run_id: Optional[str] = None, **kwargs):
        call_id = str(run_id)
        _metric_inc("tool_errors", 1)
        started = self._tool_started_at.pop(call_id, None)
        if started is not None:
            _record_tool_latency((time.perf_counter() - started) * 1000.0)
        tool_name = self._tool_run_map.get(call_id, "")
        self._q.put({"type": "tool_error", "id": call_id, "name": tool_name, "error": str(error), "at": time.time(), "request_id": self._request_id})


def _start_agent_thread(
    *,
    agent,
    message: str,
    include_memory: bool,
    thread_id: Optional[str],
    workspace: Optional[str],
    request_id: str,
    q: queue.Queue,
) -> None:
    def run_agent():
        try:
            handler = _StreamingHandler(q, request_id)
            thread_state = _apply_thread_scope(agent, thread_id, workspace)
            response, success = agent.process_query(
                message,
                include_memory=include_memory,
                callbacks=[handler],
                thread_id=thread_id,
            )
            doc_sources = agent.get_last_doc_sources() if include_memory else []
            state_store = get_state_store()
            latest_state = state_store.get_thread_state(thread_id).model_dump()
            execution = state_store.get_execution(latest_state.get("last_execution_id") or "") if latest_state.get("last_execution_id") else None
            spoken_text = ""
            try:
                spoken_text = str(agent.get_last_tts_text() or "")
            except Exception:
                spoken_text = ""
            q.put({"type": "memory_saved", "memory_count": agent.memory.memory_count, "at": time.time(), "request_id": request_id})
            q.put(
                {
                    "type": "final",
                    "response": response,
                    "success": success,
                    "memory_count": agent.memory.memory_count,
                    "doc_sources": doc_sources,
                    "research": handler.research_runs,
                    "spoken_text": spoken_text,
                    "execution_id": execution.id if execution else None,
                    "trace_id": execution.trace_id if execution else None,
                    "thread_state": latest_state or thread_state,
                    "request_id": request_id,
                    "at": time.time(),
                }
            )
            # Reset visualizer to idle
            q.put({"type": "status", "agent_mode": "idle", "at": time.time(), "request_id": request_id})
        except Exception as e:
            _metric_inc("errors", 1)
            q.put({"type": "error", "message": str(e), "at": time.time(), "request_id": request_id})
        finally:
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
    logger.info("Starting Echo Speak API server...")
    global _gateway_loop
    _gateway_loop = asyncio.get_running_loop()
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
            """Execute a scheduled routine through the agent pipeline."""
            try:
                r_agent = get_agent("routine_" + routine.id)
                query = routine.action_config.get("query", routine.name)
                response, success = r_agent.process_query(
                    query, source="routine", thread_id="routine_" + routine.id,
                )
                if response and success:
                    _deliver_routine_result(routine, response)
            except Exception as exc:
                logger.warning(f"Routine callback error ({routine.name}): {exc}")

        def _deliver_routine_result(routine, response):
            """Push routine output to configured delivery channels."""
            channels = getattr(routine, "delivery_channels", None) or ["web"]
            try:
                from agent.heartbeat import route_message
                route_message(str(response), list(channels), label=f"Routine: {getattr(routine, 'name', 'Routine')}")
            except Exception as exc:
                logger.debug(f"Routine delivery failed: {exc}")

        rm = get_routine_manager()
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
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Graceful restart support
_restart_requested = False
_restart_lock = threading.Lock()

# Rate limiting
_rate_limit_lock = threading.Lock()
_rate_limits: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_REQUESTS = 100  # requests per window
RATE_LIMIT_WINDOW = 60.0  # seconds


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request, considering X-Forwarded-For."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
    """Rate limit requests per client IP."""
    # Skip rate limiting for health checks and static assets
    if request.url.path in ["/health", "/metrics", "/favicon.ico"]:
        return await call_next(request)
    
    client_ip = _get_client_ip(request)
    allowed, remaining = _check_rate_limit(client_ip)
    
    if not allowed:
        return Response(
            content='{"detail":"Rate limit exceeded. Try again later."}',
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": str(int(RATE_LIMIT_WINDOW))}
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


class QueryRequest(BaseModel):
    """Request model for query endpoint."""
    message: str = Field(..., description="User message to process", max_length=50000)
    include_memory: bool = Field(default=True, description="Include conversation memory")
    thread_id: Optional[str] = Field(default=None, description="Conversation thread id for LangGraph persistence")
    workspace: Optional[str] = Field(default=None, description="Optional workspace/mode override (ex: auto|chat|coding|research)")


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


class ThreadSessionStateResponse(BaseModel):
    thread_id: str
    workspace_id: str = ""
    active_project_id: str = ""
    pending_approval_id: str = ""
    last_execution_id: str = ""
    last_trace_id: str = ""
    runtime_provider: str = ""
    updated_at: float = 0.0


class ApprovalResponse(BaseModel):
    id: str
    thread_id: str
    execution_id: Optional[str] = None
    status: str
    tool: str
    kwargs: Dict[str, Any] = Field(default_factory=dict)
    original_input: str = ""
    preview: str = ""
    summary: str = ""
    risk_level: str = "safe"
    policy_flags: List[str] = Field(default_factory=list)
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


class ExecutionResponse(BaseModel):
    id: str
    request_id: str
    kind: str
    thread_id: str
    source: str
    status: str
    query: str
    workspace_id: str = ""
    active_project_id: str = ""
    runtime_provider: str = ""
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
    target: str = Field(..., description="openai | gemini | tavily | local | ollama | openai_compat")
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

        if target == "tavily":
            key = api_key or (getattr(config, "tavily_api_key", "") or "").strip()
            if not key or key == "***":
                return SettingsTestResponse(ok=False, target=target, message="Missing Tavily API key.")
            code, data = _http_post_json(
                "https://api.tavily.com/search",
                {
                    "api_key": key,
                    "query": "EchoSpeak settings connectivity test",
                    "search_depth": "basic",
                    "max_results": 1,
                    "include_answer": False,
                    "include_raw_content": False,
                },
                timeout_s=6.0,
            )
            ok = 200 <= code < 300
            ms = (time.perf_counter() - started) * 1000.0
            if ok:
                count = 0
                try:
                    count = len((data or {}).get("results") or [])
                except Exception:
                    count = 0
                return SettingsTestResponse(ok=True, target=target, message=f"OK (results={count})", latency_ms=ms)
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
        soul_path = BASE_DIR / soul_path
    
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
        soul_path = BASE_DIR / soul_path
    
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
    """Response model for conversation history."""
    history: list
    count: int


class MemoryItem(BaseModel):
    id: str
    text: str
    timestamp: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    memory_type: Optional[str] = None
    pinned: Optional[bool] = None


class MemoryUpdateRequest(BaseModel):
    id: str
    text: Optional[str] = None
    memory_type: Optional[str] = None
    pinned: Optional[bool] = None
    thread_id: Optional[str] = None


class MemoryCompactRequest(BaseModel):
    thread_id: Optional[str] = None
    similarity: float = Field(default=0.94, ge=0.5, le=1.0)
    max_scan: int = Field(default=250, ge=10, le=1000)


class MemoryListResponse(BaseModel):
    items: List[MemoryItem]
    count: int
    use_faiss: bool


class MemoryDeleteRequest(BaseModel):
    ids: List[str]
    thread_id: Optional[str] = None


class DocumentItem(BaseModel):
    id: str
    filename: str
    chunks: int
    source: Optional[str] = None
    mime: Optional[str] = None
    timestamp: Optional[str] = None


class DocumentListResponse(BaseModel):
    items: List[DocumentItem]
    count: int
    enabled: bool


class DocumentDeleteRequest(BaseModel):
    ids: List[str]


class ProviderInfoResponse(BaseModel):
    """Response model for provider information."""
    provider: str
    model: str
    local: bool
    base_url: Optional[str] = None
    available_providers: list
    context_window: int = 0
    max_output_tokens: int = 0


class SwitchProviderRequest(BaseModel):
    """Request model for switching provider."""
    provider: str = Field(..., description="Provider ID (openai, gemini, ollama, lmstudio, localai, llama_cpp, vllm)")
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
    request_id = str(uuid.uuid4())
    _metric_inc("requests", 1)
    try:
        logger.debug(
            "Query request_id=%s thread_id=%s include_memory=%s msg_len=%s",
            request_id,
            _normalize_thread_id(request.thread_id),
            bool(request.include_memory),
            len((request.message or "")),
        )
        agent = get_agent(request.thread_id)
        thread_state = _apply_thread_scope(agent, request.thread_id, request.workspace)
        q: queue.Queue = queue.Queue()
        handler = _StreamingHandler(q, request_id)
        response, success = agent.process_query(
            request.message,
            include_memory=request.include_memory,
            callbacks=[handler],
            thread_id=request.thread_id,
        )
        doc_sources = agent.get_last_doc_sources() if request.include_memory else []
        store = get_state_store()
        latest_state = store.get_thread_state(request.thread_id).model_dump()
        execution = store.get_execution(latest_state.get("last_execution_id") or "") if latest_state.get("last_execution_id") else None

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
        )
    except Exception as e:
        _metric_inc("errors", 1)
        logger.error(f"Query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/compact")
async def compact_memory(request: MemoryCompactRequest):
    """Merge near-duplicate memory items within a thread by deleting redundant items.

    This is a lightweight compaction pass to reduce spam/duplicates.
    """
    try:
        import difflib

        agent = get_agent(request.thread_id)
        items = agent.memory.list_items(offset=0, limit=int(request.max_scan or 250))
        if not items:
            return {"success": True, "deleted": 0, "kept": 0, "memory_count": agent.memory.memory_count}

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
                    if ratio >= float(request.similarity or 0.94):
                        deleted_ids.append(iid)
                        kept_ids.add(cid)
                        if is_pinned:
                            try:
                                agent.memory.update_item(cid, pinned=True)
                            except Exception:
                                pass
                        merged = True
                        break
                if not merged:
                    canon.append(it)
                    kept_ids.add(iid)

        deleted = agent.memory.delete_items(deleted_ids)
        return {
            "success": True,
            "deleted": int(deleted),
            "kept": int(len(kept_ids)),
            "memory_count": agent.memory.memory_count,
        }
    except Exception as e:
        logger.error(f"Compact memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class CapabilitiesResponse(BaseModel):
    ok: bool
    provider: str
    workspace: Dict[str, Any]
    tools: Dict[str, Any]
    features: Dict[str, Any]
    skills: List[Dict[str, Any]] = []


class PendingActionResponse(BaseModel):
    has_pending: bool
    action: Optional[Dict[str, Any]] = None
    approval_id: Optional[str] = None
    risk_level: Optional[str] = None
    risk_color: Optional[str] = None
    policy_flags: List[str] = []
    session_permissions: Dict[str, bool] = {}
    dry_run_available: bool = False


@app.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(thread_id: Optional[str] = Query(default=None)):
    try:
        agent = get_agent(thread_id)
        report = agent.get_doctor_report() or {}
        # Compute per-tool allow/deny status.
        allowlist = agent._tool_allowlist_override  # type: ignore[attr-defined]
        allowset = set(allowlist) if isinstance(allowlist, (set, frozenset)) else None

        # Import tool metadata
        from agent.tools import TOOL_METADATA

        items = []
        # NOTE: `lc_tools` intentionally excludes many action/system tools.
        # For the Capabilities & Permissions UI, we want to show the full registered tool set.
        for t in (getattr(agent, "tools", []) or []):
            name = str(getattr(t, "name", "") or "").strip()
            if not name:
                continue
            allowed_by_workspace = True
            if allowset is not None:
                allowed_by_workspace = name in allowset
            is_action = False
            try:
                is_action = bool(agent._is_action_tool(name))  # type: ignore[attr-defined]
            except Exception:
                is_action = False
            allowed_by_policy = True
            blocked_reason = ""
            blocked_by_policy_flags: List[str] = []
            if is_action:
                try:
                    allowed_by_policy = bool(agent._action_allowed(name))  # type: ignore[attr-defined]
                except Exception:
                    allowed_by_policy = False
                if not allowed_by_policy:
                    blocked_reason = "Blocked by system action permissions"
                    # Get specific policy flags that are missing
                    meta = TOOL_METADATA.get(name, {})
                    for flag in meta.get("policy_flags", []):
                        blocked_by_policy_flags.append(flag)

            # Get tool metadata
            meta = TOOL_METADATA.get(name, {})
            risk_level = meta.get("risk_level", "safe")
            requires_confirmation = meta.get("requires_confirmation", False)
            policy_flags = meta.get("policy_flags", [])

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
                    "usage_count": usage["usage_count"],
                    "error_count": usage["error_count"],
                    "last_used_at": usage["last_used_at"],
                    "success_rate": usage["success_rate"],
                }
            )

        ws = (report.get("workspace") or {}) if isinstance(report.get("workspace"), dict) else {}
        tools = (report.get("tools") or {}) if isinstance(report.get("tools"), dict) else {}
        features = (report.get("features") or {}) if isinstance(report.get("features"), dict) else {}
        provider = str(report.get("provider") or {}).strip() if isinstance(report.get("provider"), str) else ""
        if not provider:
            provider = str(getattr(agent, "llm_provider", "") or "")

        # Build skills list with type indicators
        skills_list = []
        skills_dir = Path(getattr(config, "skills_dir", "") or "").expanduser()
        for skill_def in getattr(agent, "_active_skill_defs", []):
            skill_path = skills_dir / skill_def.id
            skills_list.append({
                "id": skill_def.id,
                "name": skill_def.name,
                "description": skill_def.description[:100] if skill_def.description else "",
                "has_tools": (skill_path / "tools.py").exists() if skill_path.exists() else False,
                "has_plugin": (skill_path / "plugin.py").exists() if skill_path.exists() else False,
            })

        return CapabilitiesResponse(
            ok=bool(report.get("ok", True)),
            provider=str(getattr(agent, "llm_provider", "") or ""),
            workspace=ws,
            tools={"count": len(items), "items": items, "allowlist": tools.get("allowlist")},
            features=features,
            skills=skills_list,
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


@app.get("/threads/{thread_id}/state", response_model=ThreadSessionStateResponse)
async def get_thread_state(thread_id: str):
    store = get_state_store()
    return ThreadSessionStateResponse(**store.get_thread_state(thread_id).model_dump())


@app.get("/approvals", response_model=ApprovalListResponse)
async def list_approvals(
    thread_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    store = get_state_store()
    items = store.list_approvals(thread_id=thread_id, status=status, limit=limit)
    return ApprovalListResponse(items=[ApprovalResponse(**item.model_dump()) for item in items], count=len(items))


@app.post("/approvals/{approval_id}/confirm", response_model=ApprovalResponse)
async def confirm_approval(approval_id: str):
    store = get_state_store()
    approval = store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    agent = get_agent(approval.thread_id)
    _apply_thread_scope(agent, approval.thread_id, approval.workspace_id or None)
    agent.process_query("confirm", include_memory=False, thread_id=approval.thread_id)
    updated = store.get_approval(approval_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Approval missing after confirm")
    return ApprovalResponse(**updated.model_dump())


@app.post("/approvals/{approval_id}/cancel", response_model=ApprovalResponse)
async def cancel_approval(approval_id: str):
    store = get_state_store()
    approval = store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    agent = get_agent(approval.thread_id)
    _apply_thread_scope(agent, approval.thread_id, approval.workspace_id or None)
    agent.process_query("cancel", include_memory=False, thread_id=approval.thread_id)
    updated = store.get_approval(approval_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Approval missing after cancel")
    return ApprovalResponse(**updated.model_dump())


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


class ProjectListResponse(BaseModel):
    items: List[ProjectResponse]
    count: int


class ProjectCreateRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    context_prompt: Optional[str] = ""
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


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
    )
    return ProjectResponse(**project.model_dump())


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
    """Delete a project."""
    from agent.projects import get_project_manager
    manager = get_project_manager()
    success = manager.delete_project(project_id)
    if not success:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"ok": True, "deleted": project_id}


@app.post("/projects/{project_id}/activate")
async def activate_project(project_id: str, thread_id: Optional[str] = Query(default=None)):
    """Activate a project, injecting its context into the agent's system prompt."""
    agent = get_agent(thread_id)
    _apply_thread_scope(agent, thread_id)
    success = agent.activate_project(project_id)
    if not success:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"ok": True, "activated": project_id, "thread_state": get_state_store().get_thread_state(thread_id).model_dump()}


@app.post("/projects/deactivate")
async def deactivate_project(thread_id: Optional[str] = Query(default=None)):
    """Deactivate the current project."""
    agent = get_agent(thread_id)
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


@app.get("/routines", response_model=RoutineListResponse)
async def list_routines(enabled_only: bool = False):
    """List all routines."""
    from agent.routines import get_routine_manager
    manager = get_routine_manager()
    routines = manager.list_routines(enabled_only=enabled_only)
    return RoutineListResponse(
        items=[RoutineResponse(**r.model_dump()) for r in routines],
        count=len(routines),
    )


@app.get("/routines/{routine_id}", response_model=RoutineResponse)
async def get_routine(routine_id: str):
    """Get a routine by ID."""
    from agent.routines import get_routine_manager
    manager = get_routine_manager()
    routine = manager.get_routine(routine_id)
    if not routine:
        raise HTTPException(status_code=404, detail="Routine not found")
    return RoutineResponse(**routine.model_dump())


@app.post("/routines", response_model=RoutineResponse)
async def create_routine(request: RoutineCreateRequest):
    """Create a new routine."""
    from agent.routines import get_routine_manager
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
    )
    return RoutineResponse(**routine.model_dump())


@app.put("/routines/{routine_id}", response_model=RoutineResponse)
async def update_routine(routine_id: str, request: RoutineUpdateRequest):
    """Update an existing routine."""
    from agent.routines import get_routine_manager
    manager = get_routine_manager()
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
    )
    if not routine:
        raise HTTPException(status_code=404, detail="Routine not found")
    return RoutineResponse(**routine.model_dump())


@app.delete("/routines/{routine_id}")
async def delete_routine(routine_id: str):
    """Delete a routine."""
    from agent.routines import get_routine_manager
    manager = get_routine_manager()
    success = manager.delete_routine(routine_id)
    if not success:
        raise HTTPException(status_code=404, detail="Routine not found")
    return {"ok": True, "deleted": routine_id}


@app.post("/routines/{routine_id}/run")
async def run_routine(routine_id: str):
    """Manually run a routine."""
    from agent.routines import get_routine_manager
    manager = get_routine_manager()
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
    
    # Get request body if any — validate type and size
    try:
        body = await request.json()
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
# Proactive Engine API (v6.1.0)
# ---------------------------------------------------------------------------

@app.get("/proactive")
async def proactive_status():
    """Get proactive engine status and tasks."""
    from agent.proactive import get_proactive_engine
    pe = get_proactive_engine()
    if pe is None:
        return {"running": False, "tasks": [], "channels": []}
    return {
        "running": pe.is_running,
        "tasks": pe.list_tasks(),
        "channels": pe._channels,
    }


@app.post("/proactive/task")
async def proactive_add_task(request: Request):
    """Add or remove a proactive task."""
    data = await request.json()
    from agent.proactive import get_proactive_engine
    pe = get_proactive_engine()
    if pe is None:
        return {"ok": False, "error": "ProactiveEngine not running"}

    action = data.get("action", "add")

    if action == "remove":
        task_id = data.get("task_id", "")
        removed = pe.remove_task(task_id)
        return {"ok": removed, "message": f"Task '{task_id}' {'removed' if removed else 'not found'}"}

    # Default: add
    task = pe.add_task(
        prompt=data.get("prompt", ""),
        priority=int(data.get("priority", 5)),
        cooldown_minutes=int(data.get("cooldown_minutes", 60)),
        label=data.get("label", "Custom Task"),
        source="user",
        max_runs=int(data.get("max_runs", 0)),
    )
    return {"ok": True, "task": task.to_dict()}


@app.get("/proactive/history")
async def proactive_history(limit: int = 20):
    """Get recent proactive engine actions."""
    from agent.proactive import get_proactive_engine
    pe = get_proactive_engine()
    if pe is None:
        return {"history": []}
    return {"history": pe.get_history(limit=limit)}

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
    multi_agent_enabled = bool(getattr(config, "multi_agent_enabled", True))
    runtime_provider = _runtime_provider.value if _runtime_provider is not None else None

    if not multi_agent_enabled:
        return SessionsResponse(
            multi_agent_enabled=False,
            pool_max=_agent_pool_max,
            pool_size=1 if _agent is not None else 0,
            thread_ids=["default"],
            lm_studio_only=_is_lmstudio_only_enabled(),
            runtime_provider=runtime_provider,
        )

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
    return [ThreadResponse(**t.to_dict()) for t in threads]


@app.post("/threads", response_model=ThreadResponse)
async def create_thread(request: ThreadCreateRequest):
    """Create a new conversation thread."""
    from agent.threads import get_thread_manager
    tm = get_thread_manager()
    thread = tm.create_thread(
        title=request.title,
        source=request.source,
        workspace_id=request.workspace_id,
    )
    return ThreadResponse(**thread.to_dict())


@app.get("/threads/{thread_id}", response_model=ThreadResponse)
async def get_thread(thread_id: str):
    """Get a conversation thread by ID."""
    from agent.threads import get_thread_manager
    tm = get_thread_manager()
    thread = tm.get_thread(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
    return ThreadResponse(**thread.to_dict())


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
    return ThreadResponse(**thread.to_dict())


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
async def list_documents():
    store = get_document_store()
    if store is None:
        return DocumentListResponse(items=[], count=0, enabled=False)
    items = store.list_documents()
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

@app.post("/orchestrate")
async def orchestrate_query(request: Request):
    """Submit a complex query for multi-agent orchestration.

    Decomposes the query into sub-tasks, dispatches them in parallel
    across the agent pool, and returns a merged response.
    """
    if not getattr(config, "orchestration_enabled", False):
        raise HTTPException(status_code=404, detail="Orchestration is disabled")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    query = body.get("query", "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Missing 'query' field")

    from agent.orchestrator import get_orchestrator
    orch = get_orchestrator()

    # Run synchronously in a thread to avoid blocking the event loop
    import asyncio
    plan = await asyncio.to_thread(orch.run, query)

    return plan.to_dict()


@app.get("/orchestrate/{plan_id}")
async def get_orchestration_plan(plan_id: str):
    """Get the status and results of an orchestration plan."""
    if not getattr(config, "orchestration_enabled", False):
        raise HTTPException(status_code=404, detail="Orchestration is disabled")

    from agent.orchestrator import get_orchestrator
    orch = get_orchestrator()
    plan = orch.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan.to_dict()


@app.post("/documents/upload", response_model=DocumentItem)
async def upload_document(file: UploadFile = File(...), source: Optional[str] = None):
    store = get_document_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Document RAG is disabled")
    try:
        data = await file.read()
        max_bytes = int(getattr(config, "doc_upload_max_mb", 25) or 25) * 1024 * 1024
        if len(data) > max_bytes:
            raise HTTPException(status_code=413, detail="Upload too large")
        text = _extract_text_from_upload(file.filename or "document", file.content_type, data)
        meta = store.add_document(file.filename or "document", text, source=source or "", mime=file.content_type or "")
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
    deleted = store.delete_documents(request.ids)
    return {"success": True, "deleted": deleted}


@app.post("/documents/clear")
async def clear_documents():
    store = get_document_store()
    if store is None:
        raise HTTPException(status_code=503, detail="Document RAG is disabled")
    store.clear()
    return {"success": True}


@app.post("/query/stream")
async def query_stream(request: QueryRequest):
    agent = get_agent(request.thread_id)
    _apply_thread_scope(agent, request.thread_id, request.workspace)
    q: queue.Queue = queue.Queue()
    request_id = str(uuid.uuid4())
    _metric_inc("requests", 1)

    logger.debug(
        "QueryStream request_id=%s thread_id=%s include_memory=%s msg_len=%s",
        request_id,
        _normalize_thread_id(request.thread_id),
        bool(request.include_memory),
        len((request.message or "")),
    )

    _start_agent_thread(
        agent=agent,
        message=request.message,
        include_memory=request.include_memory,
        thread_id=request.thread_id,
        workspace=request.workspace,
        request_id=request_id,
        q=q,
    )

    async def gen():
        while True:
            item = await anyio.to_thread.run_sync(q.get)
            if item is None:
                break
            yield (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")

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
    await websocket.accept()
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
        _metric_inc("requests", 1)
        _start_agent_thread(
            agent=agent,
            message=message,
            include_memory=include_memory,
            thread_id=thread_id,
            workspace=payload.get("workspace"),
            request_id=request_id,
            q=q,
        )

        while True:
            item = await anyio.to_thread.run_sync(q.get)
            if item is None:
                break
            try:
                await websocket.send_json(item)
            except WebSocketDisconnect:
                return
            except Exception as exc:
                logger.warning(f"Gateway WS send failed: {exc}")
                break


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
async def trigger_cron(request: CronTickRequest):
    if not bool(getattr(config, "cron_enabled", False)):
        raise HTTPException(status_code=403, detail="Cron triggers disabled (CRON_ENABLED=false)")
    if croniter is None:
        raise HTTPException(status_code=503, detail="croniter is not available")

    job_id = (request.job_id or "").strip() or "default"
    cron_expr = (request.cron or "").strip()
    if not cron_expr:
        raise HTTPException(status_code=422, detail="Missing cron expression")

    now = datetime.utcnow()
    now_ts = now.timestamp()
    with _cron_state_lock:
        state = _load_cron_state()
        jobs = state.get("jobs")
        if not isinstance(jobs, dict):
            jobs = {}
        last_run = jobs.get(job_id)

        due = False
        next_run = None
        if last_run is None:
            due = True
            try:
                next_run = croniter(cron_expr, now).get_next(datetime)
            except Exception:
                next_run = None
        else:
            try:
                base = datetime.utcfromtimestamp(float(last_run))
                next_run = croniter(cron_expr, base).get_next(datetime)
                due = now >= next_run
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid cron expression: {exc}")

        if not due:
            return {
                "ran": False,
                "job_id": job_id,
                "next_run_at": next_run.isoformat() if next_run else None,
                "last_run_at": datetime.utcfromtimestamp(float(last_run)).isoformat() if last_run is not None else None,
            }

        agent = get_agent(request.thread_id)
        response, success = agent.process_query(
            request.message,
            include_memory=request.include_memory,
            thread_id=request.thread_id,
        )
        jobs[job_id] = now_ts
        state["jobs"] = jobs
        _save_cron_state(state)

    return {
        "ran": True,
        "job_id": job_id,
        "ran_at": now.isoformat(),
        "success": bool(success),
        "response": response,
    }


@app.post("/trigger/webhook")
async def trigger_webhook(req: Request):
    if not bool(getattr(config, "webhook_enabled", False)):
        raise HTTPException(status_code=403, detail="Webhook triggers disabled (WEBHOOK_ENABLED=false)")

    body = await req.body()
    secret = _load_webhook_secret()
    if not secret:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    sig = req.headers.get("x-echospeak-signature") or req.headers.get("x-signature") or ""
    if not _verify_webhook_signature(secret, body, sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    message = (str(payload.get("message") or "").strip())
    if not message:
        raise HTTPException(status_code=422, detail="Missing 'message'")
    thread_id_val = payload.get("thread_id")
    thread_id = str(thread_id_val).strip() if thread_id_val is not None else None
    if thread_id == "":
        thread_id = None
    include_memory = payload.get("include_memory", True)
    if isinstance(include_memory, str):
        include_memory = include_memory.strip().lower() not in {"false", "0", "no", "off"}
    else:
        include_memory = bool(include_memory)

    agent = get_agent(thread_id)
    _apply_thread_scope(agent, thread_id, payload.get("workspace"))
    response, success = agent.process_query(
        message,
        include_memory=include_memory,
        thread_id=thread_id,
    )

    return {"success": bool(success), "response": response}


@app.get("/history", response_model=HistoryResponse)
def get_history(thread_id: Optional[str] = Query(default=None)):
    """
    Get conversation history.

    Returns:
        List of conversation messages.
    """
    try:
        agent = get_existing_agent(thread_id)
        if agent is None:
            return HistoryResponse(history=[], count=0)
        _apply_thread_scope(agent, thread_id)
        history = agent.get_history()

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

        normalized_history: list[str] = []
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
            count=len(normalized_history)
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


@app.get("/memory", response_model=MemoryListResponse)
async def list_memory(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    thread_id: Optional[str] = Query(default=None),
):
    try:
        agent = get_agent(thread_id)
        items = agent.memory.list_items(offset=offset, limit=limit)
        out_items: List[MemoryItem] = []
        for i in items:
            payload = (i or {}) if isinstance(i, dict) else {}
            meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
            mt = str(meta.get("type") or "").strip() if isinstance(meta, dict) else ""
            pinned = meta.get("pinned") if isinstance(meta, dict) else None
            mi = MemoryItem(**payload)
            mi.memory_type = mt or None
            mi.pinned = bool(pinned) if pinned is not None else None
            out_items.append(mi)
        return MemoryListResponse(
            items=out_items,
            count=agent.memory.memory_count,
            use_faiss=bool(getattr(agent.memory, "use_faiss", False)),
        )
    except Exception as e:
        logger.error(f"List memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/delete")
async def delete_memory(request: MemoryDeleteRequest):
    try:
        agent = get_agent(request.thread_id)
        deleted = agent.memory.delete_items(request.ids)
        return {"success": True, "deleted": deleted, "memory_count": agent.memory.memory_count}
    except Exception as e:
        logger.error(f"Delete memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/update")
async def update_memory(request: MemoryUpdateRequest):
    try:
        agent = get_agent(request.thread_id)
        ok = agent.memory.update_item(
            request.id,
            text=request.text,
            memory_type=request.memory_type,
            pinned=request.pinned,
        )
        return {"success": bool(ok), "memory_count": agent.memory.memory_count}
    except Exception as e:
        logger.error(f"Update memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/memory/clear")
async def clear_memory(thread_id: Optional[str] = Query(default=None)):
    try:
        agent = get_agent(thread_id)
        agent.memory.clear_memory()
        return {"success": True, "memory_count": agent.memory.memory_count}
    except Exception as e:
        logger.error(f"Clear memory error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/provider", response_model=ProviderInfoResponse)
async def get_provider_info():
    """
    Get current provider information.

    Returns:
        Current provider details and available providers.
    """
    from agent.core import list_available_providers

    providers = list_available_providers()
    def _estimate_context_window(prov: ModelProvider, model_name: str) -> tuple[int, int]:
        """Return (context_window, max_output_tokens) for known models."""
        m = (model_name or "").lower()
        if prov == ModelProvider.OPENAI:
            if "gpt-4.1" in m:
                return (1047576, 32768)
            if "gpt-4o" in m:
                return (128000, 16384)
            if "gpt-4-turbo" in m:
                return (128000, 4096)
            if "gpt-4" in m:
                return (8192, 4096)
            if "gpt-3.5" in m:
                return (16385, 4096)
            if "o1" in m or "o3" in m or "o4" in m:
                return (200000, 100000)
            return (128000, 4096)
        if prov == ModelProvider.GEMINI:
            if "pro" in m:
                return (1048576, 8192)
            if "flash" in m:
                return (1048576, 8192)
            return (1048576, 8192)
        # Local providers: use config
        ctx = int(getattr(config.local, "context_length", 0) or 0) or 8192
        out = int(getattr(config.local, "max_tokens", 0) or 0) or 4096
        return (ctx, out)

    if _is_lmstudio_only_enabled():
        _force_lmstudio_config()
        providers = [p for p in providers if p.get("id") == ModelProvider.LM_STUDIO.value]
        ctx_w, max_out = _estimate_context_window(ModelProvider.LM_STUDIO, config.local.model_name)
        return ProviderInfoResponse(
            provider=ModelProvider.LM_STUDIO.value,
            model=config.local.model_name,
            local=True,
            base_url=config.local.base_url or LM_STUDIO_DEFAULT_URL,
            available_providers=providers,
            context_window=ctx_w,
            max_output_tokens=max_out,
        )

    # Do not instantiate the agent here; provider can be misconfigured (e.g. missing deps)
    # and we still want /provider to respond.
    provider = _runtime_provider or (config.local.provider if config.use_local_models else _default_cloud_provider())
    is_local = provider not in (ModelProvider.OPENAI, ModelProvider.GEMINI)
    if provider == ModelProvider.OPENAI:
        model = config.openai.model
    elif provider == ModelProvider.GEMINI:
        model = config.gemini.model
    else:
        model = config.local.model_name
    base_url = None if provider in (ModelProvider.OPENAI, ModelProvider.GEMINI, ModelProvider.LLAMA_CPP) else config.local.base_url
    ctx_w, max_out = _estimate_context_window(provider, model)

    return ProviderInfoResponse(
        provider=provider.value,
        model=model,
        local=is_local,
        base_url=base_url,
        available_providers=providers,
        context_window=ctx_w,
        max_output_tokens=max_out,
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
        if _is_lmstudio_only_enabled():
            raise HTTPException(status_code=403, detail="Provider switching is disabled (LM Studio only)")
        provider = ModelProvider(request.provider)
        _assert_provider_available(provider)
        global _agent, _runtime_provider

        if provider == ModelProvider.OPENAI:
            if request.openai_model:
                config.openai.model = request.openai_model
            config.use_local_models = False
            config.default_cloud_provider = ModelProvider.OPENAI.value
        elif provider == ModelProvider.GEMINI:
            if request.gemini_model:
                config.gemini.model = request.gemini_model
            config.use_local_models = False
            config.default_cloud_provider = ModelProvider.GEMINI.value
        else:
            config.local.provider = provider
            if request.model:
                config.local.model_name = request.model
            if request.base_url:
                config.local.base_url = request.base_url
            else:
                if provider == ModelProvider.OLLAMA:
                    config.local.base_url = "http://localhost:11434"
                elif provider == ModelProvider.LM_STUDIO:
                    config.local.base_url = "http://localhost:1234"
                elif provider == ModelProvider.LOCALAI:
                    config.local.base_url = "http://localhost:8080"
                elif provider == ModelProvider.VLLM:
                    config.local.base_url = "http://localhost:8000"
            config.use_local_models = True

        existing = _read_runtime_settings()
        existing["use_local_models"] = bool(config.use_local_models)
        if provider == ModelProvider.OPENAI:
            existing["default_cloud_provider"] = ModelProvider.OPENAI.value
            openai_patch = existing.get("openai") if isinstance(existing.get("openai"), dict) else {}
            openai_patch["model"] = config.openai.model
            existing["openai"] = openai_patch
        elif provider == ModelProvider.GEMINI:
            existing["default_cloud_provider"] = ModelProvider.GEMINI.value
            gemini_patch = existing.get("gemini") if isinstance(existing.get("gemini"), dict) else {}
            gemini_patch["model"] = config.gemini.model
            existing["gemini"] = gemini_patch
        else:
            local_patch = existing.get("local") if isinstance(existing.get("local"), dict) else {}
            local_patch["provider"] = provider.value
            local_patch["model_name"] = config.local.model_name
            local_patch["base_url"] = config.local.base_url
            existing["local"] = local_patch
        config.apply_overrides(existing)
        config.write_runtime_overrides(existing)

        # Only commit runtime provider after validation + config updates.
        _runtime_provider = provider

        _agent = None
        with _agent_pool_lock:
            _agent_pool.clear()

        return {
            "success": True,
            "message": f"Switched to {provider.value}",
            "provider": provider.value
        }
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

            base = (config.local.base_url or "").rstrip("/")
            if config.local.provider != ModelProvider.OLLAMA:
                base = "http://localhost:11434"
            if not base:
                base = "http://localhost:11434"
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

            base = (config.local.base_url or "").rstrip("/")
            if not base:
                if p == ModelProvider.LM_STUDIO:
                    base = "http://localhost:1234"
                elif p == ModelProvider.LOCALAI:
                    base = "http://localhost:8080"
                elif p == ModelProvider.VLLM:
                    base = "http://localhost:8000"
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

_TODO_FILE = BASE_DIR / "data" / "todos.json"
_todo_lock = threading.Lock()


def _load_todos() -> list:
    with _todo_lock:
        if _TODO_FILE.exists():
            try:
                return json.loads(_TODO_FILE.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []


def _save_todos(todos: list) -> None:
    with _todo_lock:
        _TODO_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TODO_FILE.write_text(json.dumps(todos, indent=2, default=str), encoding="utf-8")


class TodoItem(BaseModel):
    id: str = ""
    title: str = ""
    description: str = ""
    status: str = "pending"  # pending | in_progress | done
    priority: str = "medium"  # low | medium | high
    created_at: str = ""
    updated_at: str = ""


class TodoUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None


@app.get("/todos")
async def list_todos():
    """List all todo items."""
    return {"todos": _load_todos()}


@app.post("/todos")
async def create_todo(item: TodoItem):
    """Create a new todo item."""
    if not (item.title or "").strip():
        raise HTTPException(status_code=400, detail="Todo title is required")
    todos = _load_todos()
    now = datetime.utcnow().isoformat()
    entry = {
        "id": item.id or str(uuid.uuid4())[:8],
        "title": item.title,
        "description": item.description,
        "status": item.status,
        "priority": item.priority,
        "created_at": now,
        "updated_at": now,
    }
    todos.append(entry)
    _save_todos(todos)
    return {"todo": entry}


@app.put("/todos/{todo_id}")
async def update_todo(todo_id: str, item: TodoUpdateRequest):
    """Update a todo item by ID."""
    todos = _load_todos()
    for t in todos:
        if t.get("id") == todo_id:
            if item.title is not None:
                t["title"] = item.title
            if item.description is not None:
                t["description"] = item.description
            if item.status is not None:
                t["status"] = item.status
            if item.priority is not None:
                t["priority"] = item.priority
            t["updated_at"] = datetime.utcnow().isoformat()
            _save_todos(todos)
            return {"todo": t}
    raise HTTPException(status_code=404, detail="Todo not found")


@app.delete("/todos/{todo_id}")
async def delete_todo(todo_id: str):
    """Delete a todo item by ID."""
    todos = _load_todos()
    filtered = [t for t in todos if t.get("id") != todo_id]
    if len(filtered) == len(todos):
        raise HTTPException(status_code=404, detail="Todo not found")
    _save_todos(filtered)
    return {"deleted": todo_id}


@app.post("/todos/reorder")
async def reorder_todos(request: Request):
    """Reorder todos by providing a list of IDs in order."""
    data = await request.json()
    order = data.get("order", [])
    todos = _load_todos()
    by_id = {t["id"]: t for t in todos}
    reordered = [by_id[tid] for tid in order if tid in by_id]
    # append any that weren't in the order list
    seen = set(order)
    for t in todos:
        if t["id"] not in seen:
            reordered.append(t)
    _save_todos(reordered)
    return {"todos": reordered}


# ── Avatar Config Endpoints ─────────────────────────────────────────────────

_AVATAR_CONFIG_FILE = BASE_DIR / "data" / "avatar_config.json"

_DEFAULT_AVATAR_CONFIG = {
    "body_color": "#ffffff",
    "eye_color": "#000000",
    "bg_color": "#0a0a0a",
    "glow_color": "#4f8eff",
    "idle_activity": "auto",
    "breathing_speed": 1.0,
    "eye_size": 1.0,
    "body_roundness": 14,
    "enable_particles": True,
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
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    start_server()
