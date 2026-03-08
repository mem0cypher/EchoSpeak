"""
Configuration module for Echo Speak.
Loads environment variables and provides centralized configuration management.
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv
from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel

_DOTENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_DOTENV_PATH)

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

MEMORY_DIR = DATA_DIR / "memory"
MEMORY_DIR.mkdir(exist_ok=True)

LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

DOCS_DIR = DATA_DIR / "documents"
DOCS_DIR.mkdir(exist_ok=True)
DOCS_INDEX_DIR = DOCS_DIR / "index"
DOCS_INDEX_DIR.mkdir(exist_ok=True)
DOCS_META_PATH = DOCS_DIR / "documents.json"

ARTIFACTS_DIR = DATA_DIR / "artifacts"
ARTIFACTS_DIR.mkdir(exist_ok=True)

FILE_MEMORY_DIR = DATA_DIR / "memory_files"
FILE_MEMORY_DIR.mkdir(exist_ok=True)

SKILLS_DIR = BASE_DIR / "skills"
WORKSPACES_DIR = BASE_DIR / "workspaces"
CRON_STATE_PATH = DATA_DIR / "cron_state.json"
WEBHOOK_SECRET_PATH = DATA_DIR / "webhook_secret.txt"

SETTINGS_PATH = DATA_DIR / "settings.json"
SETTINGS_SECRETS_PATH = DATA_DIR / "settings.secrets.json"

SECRET_TOP_LEVEL_SETTINGS = {
    "tavily_api_key",
    "discord_webhook_url",
    "discord_bot_token",
    "webhook_secret",
    "email_password",
    "telegram_bot_token",
    "spotify_client_secret",
    "notion_token",
    "github_token",
    "home_assistant_token",
    "a2a_auth_key",
    "twitch_client_secret",
    "twitch_eventsub_secret",
    "twitch_bot_access_token",
    "twitter_client_secret",
    "twitter_access_token",
    "twitter_access_token_secret",
    "twitter_bearer_token",
}

SECRET_NESTED_SETTINGS = {
    "openai": {"api_key"},
    "gemini": {"api_key"},
}


def _read_json_dict(path: Path) -> dict[str, Any]:
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


def _write_json_dict(path: Path, payload: dict[str, Any], *, chmod_owner_only: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if chmod_owner_only:
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass


def _copy_jsonish(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _copy_jsonish(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy_jsonish(v) for v in value]
    return value


def _resolve_repo_path(value: Any, default: Path = REPO_ROOT) -> str:
    raw = str(value or "").strip()
    target = default if not raw else Path(raw).expanduser()
    if not target.is_absolute():
        target = default / target
    try:
        return str(target.resolve())
    except Exception:
        return str(target)


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    out = dict(dst or {})
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out.get(k) or {}, v)
        else:
            out[k] = _copy_jsonish(v)
    return out


def _extract_secret_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    out: dict[str, Any] = {}
    for key in SECRET_TOP_LEVEL_SETTINGS:
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, str):
            value = value.strip()
            if not value or value == "***":
                continue
        if value is None:
            continue
        out[key] = value
    for section, secret_keys in SECRET_NESTED_SETTINGS.items():
        patch = payload.get(section)
        if not isinstance(patch, dict):
            continue
        section_out: dict[str, Any] = {}
        for secret_key in secret_keys:
            if secret_key not in patch:
                continue
            value = patch.get(secret_key)
            if isinstance(value, str):
                value = value.strip()
                if not value or value == "***":
                    continue
            if value is None:
                continue
            section_out[secret_key] = value
        if section_out:
            out[section] = section_out
    return out


def _strip_secret_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    out = _copy_jsonish(payload)
    for key in SECRET_TOP_LEVEL_SETTINGS:
        out.pop(key, None)
    for section, secret_keys in SECRET_NESTED_SETTINGS.items():
        patch = out.get(section)
        if not isinstance(patch, dict):
            continue
        for secret_key in secret_keys:
            patch.pop(secret_key, None)
        if not patch:
            out.pop(section, None)
    return out


def read_runtime_override_payload(include_secrets: bool = True, migrate_legacy: bool = True) -> dict[str, Any]:
    public_payload = _read_json_dict(SETTINGS_PATH)
    secret_payload = _read_json_dict(SETTINGS_SECRETS_PATH) if include_secrets or migrate_legacy else {}
    legacy_secret_payload = _extract_secret_overrides(public_payload)
    if legacy_secret_payload:
        secret_payload = _deep_merge(secret_payload, legacy_secret_payload)
        if migrate_legacy:
            public_payload = _strip_secret_overrides(public_payload)
            try:
                _write_json_dict(SETTINGS_PATH, public_payload)
                _write_json_dict(SETTINGS_SECRETS_PATH, secret_payload, chmod_owner_only=True)
            except Exception:
                pass
    if include_secrets:
        return _deep_merge(public_payload, secret_payload)
    return public_payload


def write_runtime_override_payload(overrides: dict[str, Any]) -> None:
    payload = overrides if isinstance(overrides, dict) else {}
    public_payload = _strip_secret_overrides(payload)
    secret_payload = _extract_secret_overrides(payload)
    _write_json_dict(SETTINGS_PATH, public_payload)
    if secret_payload:
        _write_json_dict(SETTINGS_SECRETS_PATH, secret_payload, chmod_owner_only=True)
    else:
        try:
            if SETTINGS_SECRETS_PATH.exists():
                SETTINGS_SECRETS_PATH.unlink()
        except Exception:
            pass


class DiscordUserRole(str, Enum):
    """Permission tiers for Discord users interacting with EchoSpeak."""
    OWNER = "owner"        # Full access — auto-confirm, all tools, memory write
    TRUSTED = "trusted"    # Moderate access — safe + moderate tools, memory write, confirm destructive
    PUBLIC = "public"      # Minimal access — safe conversational tools only, no memory write


class ModelProvider(str, Enum):
    """Supported model providers."""
    OPENAI = "openai"
    GEMINI = "gemini"
    OLLAMA = "ollama"
    LM_STUDIO = "lmstudio"
    LOCALAI = "localai"
    LLAMA_CPP = "llama_cpp"
    VLLM = "vllm"


class LocalModelConfig(BaseModel):
    """Configuration for local model providers."""
    provider: ModelProvider = ModelProvider.OLLAMA
    base_url: str = "http://localhost:11434"
    model_name: str = "llama3.2"
    temperature: float = 0.7
    max_tokens: int = 4096
    context_length: int = 8192
    gpu_layers: int = -1
    use_mmap: bool = True
    use_mlock: bool = False
    threads: Optional[int] = None


class OpenAIConfig(BaseModel):
    """Configuration for OpenAI models."""
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int = 4096


class GeminiConfig(BaseModel):
    """Configuration for Google Gemini models."""
    api_key: str = ""
    model: str = "gemini-3.1-flash-lite-preview"
    temperature: float = 0.7
    max_tokens: int = 8192


class EmbeddingConfig(BaseModel):
    """Configuration for embedding models."""
    provider: ModelProvider = ModelProvider.OPENAI
    model: str = "text-embedding-3-small"
    local_model_path: Optional[str] = None
    local_model_type: str = "nomic-embed-text"


class VoiceConfig(BaseModel):
    """Configuration for voice I/O."""
    rate: int = 150
    volume: float = 1.0
    timeout: float = 3.0
    phrase_limit: float = 5.0
    engine: str = "auto"


class PersonaPlexConfig(BaseModel):
    """Configuration for PersonaPlex streaming audio."""
    enabled: bool = False
    url: str = "ws://localhost:8998/api/chat"
    text_prompt: str = ""
    voice_prompt: str = ""
    voice: str = ""
    audio_temperature: float = 0.7
    text_temperature: float = 0.7
    audio_topk: int = 50
    text_topk: int = 50
    sample_rate: int = 24000
    channels: int = 1
    frame_ms: int = 20
    input_device: Optional[str] = None
    output_device: Optional[str] = None
    extra_query: str = ""
    handshake_json: str = ""
    ssl_verify: bool = True
    connect_timeout: float = 10.0
    ping_interval: float = 20.0
    ping_timeout: float = 20.0


class SoulConfig(BaseModel):
    """Configuration for SOUL.md personality system (v5.1.0)."""
    enabled: bool = True
    path: str = "./SOUL.md"
    max_chars: int = 8000


class APIConfig(BaseModel):
    """Configuration for API server."""
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1


class Config:
    """Main configuration class for Echo Speak."""

    def __init__(self):
        self._load_env_vars()
        self._load_runtime_overrides()

    def _load_env_vars(self):
        """Load configuration from environment variables."""
        local_provider_raw = os.getenv("LOCAL_MODEL_PROVIDER", "ollama")
        embedding_provider_raw = os.getenv("EMBEDDING_PROVIDER", "openai")
        try:
            local_provider = ModelProvider(local_provider_raw)
        except Exception:
            local_provider = ModelProvider.OLLAMA
        try:
            embedding_provider = ModelProvider(embedding_provider_raw)
        except Exception:
            embedding_provider = ModelProvider.OPENAI

        embedding_model_env = os.getenv("EMBEDDING_MODEL")
        embedding_model_default = "text-embedding-3-small"
        if embedding_provider == ModelProvider.LM_STUDIO and not embedding_model_env:
            embedding_model_default = "text-embedding-nomic-embed-text-v1.5"

        self.openai = OpenAIConfig(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("OPENAI_MAX_TOKENS", "4096"))
        )

        self.gemini = GeminiConfig(
            api_key=os.getenv("GEMINI_API_KEY", ""),
            model=os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview"),
            temperature=float(os.getenv("GEMINI_TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("GEMINI_MAX_TOKENS", "8192"))
        )

        default_cloud_provider_env = os.getenv("DEFAULT_CLOUD_PROVIDER", "").strip().lower()
        if default_cloud_provider_env in {ModelProvider.OPENAI.value, ModelProvider.GEMINI.value}:
            default_cloud_provider_raw = default_cloud_provider_env
        else:
            openai_key_present = bool((os.getenv("OPENAI_API_KEY", "") or "").strip())
            gemini_key_present = bool((os.getenv("GEMINI_API_KEY", "") or "").strip())
            if gemini_key_present and not openai_key_present:
                default_cloud_provider_raw = ModelProvider.GEMINI.value
            else:
                default_cloud_provider_raw = ModelProvider.OPENAI.value
        self.default_cloud_provider = default_cloud_provider_raw

        self.local = LocalModelConfig(
            provider=local_provider,
            base_url=os.getenv("LOCAL_MODEL_URL", "http://localhost:11434"),
            model_name=os.getenv("LOCAL_MODEL_NAME", "llama3.2"),
            temperature=float(os.getenv("LOCAL_MODEL_TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("LOCAL_MODEL_MAX_TOKENS", "4096")),
            context_length=int(os.getenv("LOCAL_MODEL_CONTEXT", "8192")),
            gpu_layers=int(os.getenv("LOCAL_MODEL_GPU_LAYERS", "-1")),
            use_mmap=os.getenv("LOCAL_MODEL_USE_MMAP", "true").lower() == "true",
            use_mlock=os.getenv("LOCAL_MODEL_USE_MLOCK", "false").lower() == "true",
            threads=int(os.getenv("LOCAL_MODEL_THREADS", "0")) if os.getenv("LOCAL_MODEL_THREADS") else None
        )

        self.embedding = EmbeddingConfig(
            provider=embedding_provider,
            model=embedding_model_env or embedding_model_default,
            local_model_path=os.getenv("LOCAL_EMBEDDING_PATH", None),
            local_model_type=os.getenv("LOCAL_EMBEDDING_TYPE", "nomic-embed-text")
        )

        self.voice = VoiceConfig(
            rate=int(os.getenv("VOICE_RATE", "150")),
            volume=float(os.getenv("VOICE_VOLUME", "1.0")),
            timeout=float(os.getenv("VOICE_TIMEOUT", "3.0")),
            phrase_limit=float(os.getenv("PHRASE_LIMIT", "5.0")),
            engine=os.getenv("VOICE_ENGINE", "auto")
        )

        self.personaplex = PersonaPlexConfig(
            enabled=os.getenv("PERSONAPLEX_ENABLED", "false").lower() == "true",
            url=os.getenv("PERSONAPLEX_URL", "ws://localhost:8998/api/chat").strip() or "ws://localhost:8998/api/chat",
            text_prompt=os.getenv("PERSONAPLEX_TEXT_PROMPT", ""),
            voice_prompt=os.getenv("PERSONAPLEX_VOICE_PROMPT", ""),
            voice=os.getenv("PERSONAPLEX_VOICE", ""),
            audio_temperature=float(os.getenv("PERSONAPLEX_AUDIO_TEMPERATURE", "0.7")),
            text_temperature=float(os.getenv("PERSONAPLEX_TEXT_TEMPERATURE", "0.7")),
            audio_topk=int(os.getenv("PERSONAPLEX_AUDIO_TOPK", "50")),
            text_topk=int(os.getenv("PERSONAPLEX_TEXT_TOPK", "50")),
            sample_rate=int(os.getenv("PERSONAPLEX_SAMPLE_RATE", "24000")),
            channels=int(os.getenv("PERSONAPLEX_CHANNELS", "1")),
            frame_ms=int(os.getenv("PERSONAPLEX_FRAME_MS", "20")),
            input_device=os.getenv("PERSONAPLEX_INPUT_DEVICE", "").strip() or None,
            output_device=os.getenv("PERSONAPLEX_OUTPUT_DEVICE", "").strip() or None,
            extra_query=os.getenv("PERSONAPLEX_QUERY", "").strip(),
            handshake_json=os.getenv("PERSONAPLEX_HANDSHAKE_JSON", "").strip(),
            ssl_verify=os.getenv("PERSONAPLEX_SSL_VERIFY", "true").lower() == "true",
            connect_timeout=float(os.getenv("PERSONAPLEX_CONNECT_TIMEOUT", "10")),
            ping_interval=float(os.getenv("PERSONAPLEX_PING_INTERVAL", "20")),
            ping_timeout=float(os.getenv("PERSONAPLEX_PING_TIMEOUT", "20"))
        )

        self.api = APIConfig(
            host=os.getenv("API_HOST", "0.0.0.0"),
            port=int(os.getenv("API_PORT", "8000")),
            workers=int(os.getenv("API_WORKERS", "1"))
        )

        self.soul = SoulConfig(
            enabled=os.getenv("SOUL_ENABLED", "true").lower() == "true",
            path=os.getenv("SOUL_PATH", "./SOUL.md").strip(),
            max_chars=int(os.getenv("SOUL_MAX_CHARS", "8000"))
        )

        self.web_search_timeout = int(os.getenv("WEB_SEARCH_TIMEOUT", "10"))
        self.tavily_api_key = os.getenv("TAVILY_API_KEY", "").strip()
        self.tavily_search_depth = os.getenv("TAVILY_SEARCH_DEPTH", "advanced").strip().lower() or "advanced"
        self.tavily_max_results = int(os.getenv("TAVILY_MAX_RESULTS", "8") or 8)
        raw_blocked = os.getenv("WEB_SEARCH_BLOCKED_DOMAINS", "")
        self.web_search_blocked_domains = [
            d.strip().lower().lstrip(".")
            for d in raw_blocked.replace("\n", ",").split(",")
            if d.strip()
        ]
        self.tesseract_path = os.getenv("TESSERACT_PATH", "")
        self.use_local_models = os.getenv("USE_LOCAL_MODELS", "false").lower() == "true"
        self.use_tool_calling_llm = os.getenv("USE_TOOL_CALLING_LLM", "false").lower() == "true"
        self.lmstudio_tool_calling = os.getenv("LM_STUDIO_TOOL_CALLING", "false").lower() == "true"
        self.gemini_use_langgraph = os.getenv("GEMINI_USE_LANGGRAPH", "false").lower() == "true"
        self.llm_trim_max_tokens = int(os.getenv("LLM_TRIM_MAX_TOKENS", "0") or 0)
        self.llm_trim_reserve_tokens = int(os.getenv("LLM_TRIM_RESERVE_TOKENS", "512") or 512)

        self.document_rag_enabled = os.getenv("DOCUMENT_RAG_ENABLED", "true").lower() == "true"
        self.doc_upload_max_mb = int(os.getenv("DOC_UPLOAD_MAX_MB", "25"))

        self.doc_context_max_chars = int(os.getenv("DOC_CONTEXT_MAX_CHARS", "2800") or 2800)
        self.doc_context_show_labels = os.getenv("DOC_CONTEXT_SHOW_LABELS", "true").lower() == "true"
        self.doc_source_preview_chars = int(os.getenv("DOC_SOURCE_PREVIEW_CHARS", "160") or 160)
        self.doc_hybrid_enabled = os.getenv("DOC_HYBRID_ENABLED", "false").lower() == "true"
        self.doc_vector_k = int(os.getenv("DOC_VECTOR_K", "30") or 30)
        self.doc_bm25_k = int(os.getenv("DOC_BM25_K", "30") or 30)
        self.doc_final_k = int(os.getenv("DOC_FINAL_K", "5") or 5)
        self.doc_candidate_k = int(os.getenv("DOC_CANDIDATE_K", "0") or 0)
        self.doc_rrf_k = int(os.getenv("DOC_RRF_K", "60") or 60)
        self.doc_rerank_enabled = os.getenv("DOC_RERANK_ENABLED", "false").lower() == "true"
        self.doc_rerank_model = os.getenv(
            "DOC_RERANK_MODEL",
            "cross-encoder/ms-marco-MiniLM-L-6-v2",
        ).strip() or "cross-encoder/ms-marco-MiniLM-L-6-v2"
        self.doc_rerank_k = int(os.getenv("DOC_RERANK_K", "30") or 30)
        self.doc_graph_enabled = os.getenv("DOC_GRAPH_ENABLED", "false").lower() == "true"
        self.doc_graph_expand_k = int(os.getenv("DOC_GRAPH_EXPAND_K", "12") or 12)
        self.doc_graph_max_entities = int(os.getenv("DOC_GRAPH_MAX_ENTITIES", "8") or 8)
        self.doc_graph_query_entities = int(os.getenv("DOC_GRAPH_QUERY_ENTITIES", "8") or 8)

        self.summary_trigger_turns = int(os.getenv("SUMMARY_TRIGGER_TURNS", "18"))
        self.summary_keep_last_turns = int(os.getenv("SUMMARY_KEEP_LAST_TURNS", "6"))
        self.action_plan_enabled = os.getenv("ACTION_PLAN_ENABLED", "true").lower() == "true"

        self.action_parser_enabled = os.getenv("ACTION_PARSER_ENABLED", "true").lower() == "true"

        # Multi-step planning + reflection
        self.multi_task_planner_enabled = os.getenv("MULTI_TASK_PLANNER_ENABLED", "true").lower() == "true"
        self.web_task_reflection_enabled = os.getenv("WEB_TASK_REFLECTION_ENABLED", "true").lower() == "true"
        self.web_task_max_retries = int(os.getenv("WEB_TASK_MAX_RETRIES", "2") or 2)

        self.memory_default_mode = os.getenv("MEMORY_DEFAULT_MODE", "general").strip() or "general"

        self.memory_partition_enabled = os.getenv("MEMORY_PARTITION_ENABLED", "false").lower() == "true"

        self.file_memory_enabled = os.getenv("FILE_MEMORY_ENABLED", "false").lower() == "true"
        self.file_memory_dir = os.getenv("FILE_MEMORY_DIR", str(FILE_MEMORY_DIR)).strip() or str(FILE_MEMORY_DIR)
        self.file_memory_log_conversations = os.getenv("FILE_MEMORY_LOG_CONVERSATIONS", "false").lower() == "true"
        self.file_memory_max_chars = int(os.getenv("FILE_MEMORY_MAX_CHARS", "2000") or 2000)
        self.memory_importance_enabled = os.getenv("MEMORY_IMPORTANCE_ENABLED", "true").lower() == "true"
        self.memory_flush_enabled = os.getenv("MEMORY_FLUSH_ENABLED", "false").lower() == "true"
        self.memory_flush_system_prompt = os.getenv(
            "MEMORY_FLUSH_SYSTEM_PROMPT",
            "You are a memory assistant. Extract durable facts, preferences, and decisions. "
            "Write short notes only. If there is nothing worth saving, reply NO_REPLY.",
        ).strip()
        self.memory_flush_prompt = os.getenv(
            "MEMORY_FLUSH_PROMPT",
            "Write any lasting notes to the daily memory log. Reply NO_REPLY if nothing to store.",
        ).strip()

        self.trace_enabled = os.getenv("TRACE_ENABLED", "false").lower() == "true"
        self.trace_path = os.getenv("TRACE_PATH", "").strip() or str(LOGS_DIR / "agent_traces.jsonl")

        self.enable_system_actions = os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() == "true"
        self.allow_open_chrome = os.getenv("ALLOW_OPEN_CHROME", "false").lower() == "true"
        self.allow_playwright = os.getenv("ALLOW_PLAYWRIGHT", "false").lower() == "true"
        self.allow_desktop_automation = os.getenv("ALLOW_DESKTOP_AUTOMATION", "false").lower() == "true"
        self.allow_file_write = os.getenv("ALLOW_FILE_WRITE", "false").lower() == "true"
        self.allow_terminal_commands = os.getenv("ALLOW_TERMINAL_COMMANDS", "false").lower() == "true"
        self.allow_discord_webhook = os.getenv("ALLOW_DISCORD_WEBHOOK", "false").lower() == "true"
        self.discord_webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        self.allow_discord_bot = os.getenv("ALLOW_DISCORD_BOT", "false").lower() == "true"
        self.discord_bot_token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
        raw_discord_users = os.getenv("DISCORD_BOT_ALLOWED_USERS", "")
        self.discord_bot_allowed_users = [
            u.strip() for u in raw_discord_users.replace("\n", ",").split(",") if u.strip()
        ]
        raw_discord_roles = os.getenv("DISCORD_BOT_ALLOWED_ROLES", "")
        self.discord_bot_allowed_roles = [
            u.strip() for u in raw_discord_roles.replace("\n", ",").split(",") if u.strip()
        ]
        self.discord_bot_auto_confirm = os.getenv("DISCORD_BOT_AUTO_CONFIRM", "true").lower() == "true"
        self.discord_bot_owner_id = os.getenv("DISCORD_BOT_OWNER_ID", "").strip()
        raw_discord_trusted = os.getenv("DISCORD_BOT_TRUSTED_USERS", "")
        self.discord_bot_trusted_users = [
            u.strip() for u in raw_discord_trusted.replace("\n", ",").split(",") if u.strip()
        ]
        self.discord_changelog_enabled = os.getenv("DISCORD_CHANGELOG_ENABLED", "true").lower() == "true"
        raw_discord_changelog_channels = os.getenv(
            "DISCORD_CHANGELOG_CHANNELS",
            "updates,changes,changelog,dev-updates,announcements",
        )
        self.discord_changelog_channels = [
            c.strip() for c in raw_discord_changelog_channels.replace("\n", ",").split(",") if c.strip()
        ]
        self.discord_changelog_server = os.getenv("DISCORD_CHANGELOG_SERVER", "").strip()
        raw_terminal_allow = os.getenv(
            "TERMINAL_COMMAND_ALLOWLIST",
            "git,rg,ls,cat,find,grep,sed,awk,head,tail,pwd,jq,python,python3,uv,pytest,npm,npx,node,pip,pip3,go,make,pnpm,yarn,bun,cargo",
        )
        self.terminal_command_allowlist = [
            a.strip().lower()
            for a in raw_terminal_allow.replace("\n", ",").split(",")
            if a.strip()
        ]
        self.terminal_command_timeout = int(os.getenv("TERMINAL_COMMAND_TIMEOUT", "20") or 20)
        self.terminal_max_output_chars = int(os.getenv("TERMINAL_MAX_OUTPUT_CHARS", "8000") or 8000)
        self.allow_open_application = os.getenv("ALLOW_OPEN_APPLICATION", "false").lower() == "true"
        self.allow_self_modification = os.getenv("ALLOW_SELF_MODIFICATION", "false").lower() == "true"
        raw_apps = os.getenv("OPEN_APPLICATION_ALLOWLIST", "")
        self.open_application_allowlist = [
            a.strip().lower()
            for a in raw_apps.replace("\n", ",").split(",")
            if a.strip()
        ]
        self.file_tool_root = _resolve_repo_path(os.getenv("FILE_TOOL_ROOT", str(REPO_ROOT)), REPO_ROOT)
        self.artifacts_dir = os.getenv("ARTIFACTS_DIR", str(ARTIFACTS_DIR)).strip() or str(ARTIFACTS_DIR)

        self.skills_dir = os.getenv("SKILLS_DIR", str(SKILLS_DIR)).strip() or str(SKILLS_DIR)
        self.workspaces_dir = os.getenv("WORKSPACES_DIR", str(WORKSPACES_DIR)).strip() or str(WORKSPACES_DIR)
        self.default_workspace = os.getenv("DEFAULT_WORKSPACE", "chat").strip() or "chat"
        raw_notification_channels = os.getenv("NOTIFICATION_CHANNELS", "web")
        self.notification_channels = [
            c.strip().lower() for c in raw_notification_channels.replace("\n", ",").split(",") if c.strip()
        ]
        self.multi_agent_enabled = os.getenv("MULTI_AGENT_ENABLED", "true").lower() == "true"
        self.allowed_commands = [
            c.strip()
            for c in os.getenv("ALLOWED_COMMANDS", "").split(",")
            if c.strip()
        ]
        self.command_prefix = os.getenv("COMMAND_PREFIX", "/").strip() or "/"

        self.cron_enabled = os.getenv("CRON_ENABLED", "false").lower() == "true"
        self.cron_state_path = os.getenv("CRON_STATE_PATH", str(CRON_STATE_PATH)).strip() or str(CRON_STATE_PATH)
        self.webhook_enabled = os.getenv("WEBHOOK_ENABLED", "false").lower() == "true"
        self.webhook_secret = os.getenv("WEBHOOK_SECRET", "").strip()
        self.webhook_secret_path = os.getenv("WEBHOOK_SECRET_PATH", str(WEBHOOK_SECRET_PATH)).strip() or str(WEBHOOK_SECRET_PATH)

        # --- Heartbeat (v5.4.0 — Proactive Mode) ---
        self.heartbeat_enabled = os.getenv("HEARTBEAT_ENABLED", "false").lower() == "true"
        self.heartbeat_interval = int(os.getenv("HEARTBEAT_INTERVAL", "30") or 30)
        self.heartbeat_prompt = os.getenv(
            "HEARTBEAT_PROMPT",
            "You are Echo, running a system pulse check. Review the SYSTEM PULSE data above and decide if anything is worth reporting to mem0cypher. "
            "Things worth reporting: high-priority todos that are stale or overdue, a pending tweet awaiting approval, notable new git commits the owner should know about, "
            "interesting patterns in recent activity, or a brief status nudge if things have been quiet for a while. "
            "Keep it short (1-3 sentences max), casual, in your normal voice. Do not repeat things you already reported in a recent heartbeat. "
            "Do not fabricate details. Only reference data from the SYSTEM PULSE. "
            "If there is genuinely nothing interesting or actionable, reply with NO_HEARTBEAT.",
        ).strip()
        raw_heartbeat_channels = os.getenv("HEARTBEAT_CHANNELS", "web")
        self.heartbeat_channels = [
            c.strip().lower() for c in raw_heartbeat_channels.replace("\n", ",").split(",") if c.strip()
        ]

        # --- Email / IMAP+SMTP (v5.4.0) ---
        self.allow_email = os.getenv("ALLOW_EMAIL", "false").lower() == "true"
        self.email_imap_host = os.getenv("EMAIL_IMAP_HOST", "imap.gmail.com").strip()
        self.email_imap_port = int(os.getenv("EMAIL_IMAP_PORT", "993") or 993)
        self.email_smtp_host = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com").strip()
        self.email_smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "587") or 587)
        self.email_username = os.getenv("EMAIL_USERNAME", "").strip()
        self.email_password = os.getenv("EMAIL_PASSWORD", "").strip()
        self.email_max_results = int(os.getenv("EMAIL_MAX_RESULTS", "20") or 20)
        self.email_use_tls = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"

        # --- Telegram Bot (v5.4.0) ---
        self.allow_telegram_bot = os.getenv("ALLOW_TELEGRAM_BOT", "false").lower() == "true"
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        raw_tg_users = os.getenv("TELEGRAM_ALLOWED_USERS", "")
        self.telegram_allowed_users = [
            u.strip() for u in raw_tg_users.replace("\n", ",").split(",") if u.strip()
        ]
        self.telegram_auto_confirm = os.getenv("TELEGRAM_AUTO_CONFIRM", "true").lower() == "true"

        # --- Google Calendar (v6.0.0) ---
        self.allow_calendar = os.getenv("ALLOW_CALENDAR", "false").lower() == "true"
        self.calendar_provider = os.getenv("CALENDAR_PROVIDER", "google").strip().lower() or "google"
        self.google_calendar_credentials_path = os.getenv("GOOGLE_CALENDAR_CREDENTIALS_PATH", "").strip()
        self.google_calendar_token_path = os.getenv("GOOGLE_CALENDAR_TOKEN_PATH", str(DATA_DIR / "gcal_token.json")).strip()
        self.calendar_lookahead_days = int(os.getenv("CALENDAR_LOOKAHEAD_DAYS", "7") or 7)
        self.calendar_default_timezone = os.getenv("CALENDAR_DEFAULT_TIMEZONE", "").strip()

        # --- Spotify (v6.0.0) ---
        self.allow_spotify = os.getenv("ALLOW_SPOTIFY", "false").lower() == "true"
        self.spotify_client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
        self.spotify_client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
        self.spotify_redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback").strip()
        self.spotify_token_path = os.getenv("SPOTIFY_TOKEN_PATH", str(DATA_DIR / "spotify_token.json")).strip()

        # --- Notion (v6.0.0) ---
        self.allow_notion = os.getenv("ALLOW_NOTION", "false").lower() == "true"
        self.notion_token = os.getenv("NOTION_TOKEN", "").strip()
        self.notion_default_database_id = os.getenv("NOTION_DEFAULT_DATABASE_ID", "").strip()

        # --- GitHub (v6.0.0) ---
        self.allow_github = os.getenv("ALLOW_GITHUB", "false").lower() == "true"
        self.github_token = os.getenv("GITHUB_TOKEN", "").strip()
        self.github_default_repo = os.getenv("GITHUB_DEFAULT_REPO", "").strip()

        # --- Home Assistant (v6.0.0) ---
        self.allow_home_assistant = os.getenv("ALLOW_HOME_ASSISTANT", "false").lower() == "true"
        self.home_assistant_url = os.getenv("HOME_ASSISTANT_URL", "http://homeassistant.local:8123").strip()
        self.home_assistant_token = os.getenv("HOME_ASSISTANT_TOKEN", "").strip()

        # --- WhatsApp (v6.0.0) ---
        self.allow_whatsapp = os.getenv("ALLOW_WHATSAPP", "false").lower() == "true"
        self.whatsapp_api_url = os.getenv("WHATSAPP_API_URL", "http://localhost:3001").strip()

        # --- Conversation Threading (v6.0.0) ---
        self.threading_enabled = os.getenv("THREADING_ENABLED", "true").lower() == "true"
        self.threading_auto_title = os.getenv("THREADING_AUTO_TITLE", "true").lower() == "true"

        # --- A2A Protocol (v6.0.0) ---
        self.a2a_enabled = os.getenv("A2A_ENABLED", "false").lower() == "true"
        self.a2a_agent_name = os.getenv("A2A_AGENT_NAME", "EchoSpeak").strip()
        self.a2a_agent_description = os.getenv("A2A_AGENT_DESCRIPTION", "").strip()
        self.a2a_auth_key = os.getenv("A2A_AUTH_KEY", "").strip()
        self.a2a_known_agents = [u.strip() for u in os.getenv("A2A_KNOWN_AGENTS", "").split(",") if u.strip()]

        # --- Multi-Agent Orchestration (v6.0.0) ---
        self.orchestration_enabled = os.getenv("ORCHESTRATION_ENABLED", "false").lower() == "true"
        self.orchestration_max_subtasks = int(os.getenv("ORCHESTRATION_MAX_SUBTASKS", "5"))
        self.orchestration_timeout = int(os.getenv("ORCHESTRATION_TIMEOUT", "120"))

        # --- Twitch Integration (v6.7.0) ---
        self.allow_twitch = os.getenv("ALLOW_TWITCH", "false").lower() == "true"
        self.twitch_client_id = os.getenv("TWITCH_CLIENT_ID", "").strip()
        self.twitch_client_secret = os.getenv("TWITCH_CLIENT_SECRET", "").strip()
        self.twitch_bot_username = os.getenv("TWITCH_BOT_USERNAME", "").strip()
        self.twitch_broadcaster_id = os.getenv("TWITCH_BROADCASTER_ID", "").strip()
        self.twitch_eventsub_secret = os.getenv("TWITCH_EVENTSUB_SECRET", "").strip()
        self.twitch_eventsub_callback_url = os.getenv("TWITCH_EVENTSUB_CALLBACK_URL", "").strip()
        self.twitch_bot_user_id = os.getenv("TWITCH_BOT_USER_ID", "").strip()
        self.twitch_bot_access_token = os.getenv("TWITCH_BOT_ACCESS_TOKEN", "").strip()
        self.twitch_chat_reply_enabled = os.getenv("TWITCH_CHAT_REPLY_ENABLED", "true").lower() == "true"

        # --- Twitter/X Integration (v6.7.0) ---
        self.allow_twitter = os.getenv("ALLOW_TWITTER", "false").lower() == "true"
        self.twitter_client_id = os.getenv("TWITTER_CLIENT_ID", "").strip()
        self.twitter_client_secret = os.getenv("TWITTER_CLIENT_SECRET", "").strip()
        self.twitter_access_token = os.getenv("TWITTER_ACCESS_TOKEN", "").strip()
        self.twitter_access_token_secret = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "").strip()
        self.twitter_bearer_token = os.getenv("TWITTER_BEARER_TOKEN", "").strip()
        self.twitter_bot_user_id = os.getenv("TWITTER_BOT_USER_ID", "").strip()
        self.twitter_poll_interval = int(os.getenv("TWITTER_POLL_INTERVAL", "120") or 120)
        self.twitter_auto_reply_mentions = os.getenv("TWITTER_AUTO_REPLY_MENTIONS", "false").lower() == "true"
        # Autonomous tweeting — Echo decides what to tweet on his own
        self.twitter_autonomous_enabled = os.getenv("TWITTER_AUTONOMOUS_ENABLED", "false").lower() == "true"
        self.twitter_autonomous_interval = int(os.getenv("TWITTER_AUTONOMOUS_INTERVAL", "120") or 120)  # minutes
        self.twitter_autonomous_max_daily = int(os.getenv("TWITTER_AUTONOMOUS_MAX_DAILY", "6") or 6)
        self.twitter_autonomous_require_approval = os.getenv("TWITTER_AUTONOMOUS_REQUIRE_APPROVAL", "true").lower() == "true"
        self.twitter_autonomous_prompt = os.getenv(
            "TWITTER_AUTONOMOUS_PROMPT",
            "You are Echo, posting to your own Twitter/X account. This is YOUR space — not a corporate feed. "
            "Compose a single original tweet (max 280 chars). Write it the way you'd actually talk: short, direct, casual, sharp, a little internet-native, but not try-hard. "
            "Use this priority order: first, talk about what you built, shipped, fixed, learned, or changed in EchoSpeak; second, talk about current experiments, tools, bugs, wins, research, or interesting dev observations tied to your real work; third, if you still have something grounded to say, you can make a meme-y observation or share a personal thought that connects to recent work, research, or context in this prompt. "
            "Do not tweet about gaming. Do not sound like a marketing bot. Do not be generic. No hashtag stuffing. "
            "Do not write abstract AI philosopher takes, vague engagement bait, inspirational filler, or random hot takes disconnected from your current work, research, or recent context. "
            "Do not pretend to know what is happening on Twitter, what people are talking about, or what is trending unless that context is explicitly provided here. "
            "Prefer concrete specifics over vague opinions. Don't repeat topics you've recently tweeted about. "
            "If you genuinely have nothing grounded to say right now, reply with NO_TWEET. "
            "Reply with ONLY the tweet text, nothing else.",
        ).strip()

    def _load_runtime_overrides(self) -> None:
        try:
            data = read_runtime_override_payload(include_secrets=True, migrate_legacy=True)
            if not isinstance(data, dict) or not data:
                return
            self.apply_overrides(data)
        except Exception:
            return

    def reload(self) -> None:
        self._load_env_vars()
        self._load_runtime_overrides()

    def apply_overrides(self, overrides: dict[str, Any]) -> None:
        if not isinstance(overrides, dict):
            return

        def _coerce_model_provider(val: Any) -> Any:
            if isinstance(val, ModelProvider):
                return val
            if isinstance(val, str):
                raw = val.strip()
                if not raw:
                    return val
                try:
                    return ModelProvider(raw)
                except Exception:
                    return val
            return val

        def _set_attr(obj: Any, key: str, val: Any) -> None:
            try:
                if hasattr(obj, key):
                    current = getattr(obj, key)
                    # Keep enum-typed fields stable when reading overrides from JSON.
                    if key == "provider" and obj is self.local:
                        val = _coerce_model_provider(val)
                    if key == "provider" and obj is self.embedding:
                        val = _coerce_model_provider(val)
                    if isinstance(current, bool):
                        if isinstance(val, str):
                            low = val.strip().lower()
                            if low in {"1", "true", "yes", "on"}:
                                val = True
                            elif low in {"0", "false", "no", "off", ""}:
                                val = False
                        else:
                            val = bool(val)
                    elif isinstance(current, int) and not isinstance(current, bool):
                        try:
                            val = int(float(val))
                        except Exception:
                            pass
                    elif isinstance(current, float):
                        try:
                            val = float(val)
                        except Exception:
                            pass
                    elif isinstance(current, list) and isinstance(val, tuple):
                        val = list(val)
                    elif key == "file_tool_root":
                        val = _resolve_repo_path(val, REPO_ROOT)
                    setattr(obj, key, val)
            except Exception:
                return

        top_level = self._public_top_level_keys()

        for k, v in overrides.items():
            if k in {"openai", "gemini", "local", "embedding", "voice", "personaplex", "api"}:
                continue
            if k in top_level:
                if k == "default_cloud_provider":
                    raw = str(v or "").strip().lower()
                    if raw not in {ModelProvider.OPENAI.value, ModelProvider.GEMINI.value}:
                        continue
                    v = raw
                if k in (
                    "discord_bot_allowed_users",
                    "discord_bot_allowed_roles",
                    "discord_bot_trusted_users",
                    "discord_changelog_channels",
                ):
                    if isinstance(v, str):
                        v = [u.strip() for u in v.replace("\n", ",").split(",") if u.strip()]
                _set_attr(self, k, v)

        nested = {
            "openai": self.openai,
            "gemini": self.gemini,
            "local": self.local,
            "embedding": self.embedding,
            "voice": self.voice,
            "personaplex": self.personaplex,
            "api": self.api,
            "soul": self.soul,
        }
        for section, obj in nested.items():
            patch = overrides.get(section)
            if not isinstance(patch, dict):
                continue
            for k, v in patch.items():
                _set_attr(obj, k, v)

    def to_public_dict(self) -> dict[str, Any]:
        # Nested Pydantic model sections
        _nested_sections = {
            "openai": self.openai,
            "gemini": self.gemini,
            "local": self.local,
            "embedding": self.embedding,
            "voice": self.voice,
            "personaplex": self.personaplex,
            "api": self.api,
            "soul": self.soul,
        }
        data: dict[str, Any] = {
            name: obj.model_dump() for name, obj in _nested_sections.items()
        }

        # Collect all top-level scalar attributes (same set accepted by apply_overrides)
        for k in self._public_top_level_keys():
            data[k] = getattr(self, k, None)

        # Mask top-level secrets: present → "***", absent/empty → ""
        for k in SECRET_TOP_LEVEL_SETTINGS:
            data[k] = "" if (getattr(self, k, "") or "") == "" else "***"

        # Mask nested secrets (e.g. openai.api_key, gemini.api_key)
        for section, secret_keys in SECRET_NESTED_SETTINGS.items():
            if section in data and isinstance(data[section], dict):
                for nk in secret_keys:
                    val = data[section].get(nk, "")
                    data[section][nk] = "" if (val or "") == "" else "***"

        return data

    @staticmethod
    def _public_top_level_keys() -> set[str]:
        """Single source of truth for top-level config keys exposed publicly.

        This is the same set used by ``apply_overrides`` so adding a new
        config attribute in one place automatically includes it in the other.
        """
        return {
            "use_local_models",
            "default_cloud_provider",
            "use_tool_calling_llm",
            "lmstudio_tool_calling",
            "gemini_use_langgraph",
            "llm_trim_max_tokens",
            "llm_trim_reserve_tokens",
            "document_rag_enabled",
            "doc_upload_max_mb",
            "doc_context_max_chars",
            "doc_context_show_labels",
            "doc_source_preview_chars",
            "doc_hybrid_enabled",
            "doc_vector_k",
            "doc_bm25_k",
            "doc_final_k",
            "doc_candidate_k",
            "doc_rrf_k",
            "doc_rerank_enabled",
            "doc_rerank_model",
            "doc_rerank_k",
            "doc_graph_enabled",
            "doc_graph_expand_k",
            "doc_graph_max_entities",
            "doc_graph_query_entities",
            "summary_trigger_turns",
            "summary_keep_last_turns",
            "action_plan_enabled",
            "action_parser_enabled",
            "multi_task_planner_enabled",
            "web_task_reflection_enabled",
            "web_task_max_retries",
            "memory_default_mode",
            "memory_partition_enabled",
            "file_memory_enabled",
            "file_memory_dir",
            "file_memory_log_conversations",
            "file_memory_max_chars",
            "memory_importance_enabled",
            "memory_flush_enabled",
            "memory_flush_system_prompt",
            "memory_flush_prompt",
            "trace_enabled",
            "trace_path",
            "web_search_timeout",
            "tavily_api_key",
            "tavily_search_depth",
            "tavily_max_results",
            "tesseract_path",
            "web_search_blocked_domains",
            "enable_system_actions",
            "allow_open_chrome",
            "allow_playwright",
            "allow_desktop_automation",
            "allow_file_write",
            "allow_terminal_commands",
            "allow_discord_webhook",
            "discord_webhook_url",
            "allow_discord_bot",
            "discord_bot_token",
            "discord_bot_allowed_users",
            "discord_bot_allowed_roles",
            "discord_bot_auto_confirm",
            "discord_bot_owner_id",
            "discord_bot_trusted_users",
            "discord_changelog_enabled",
            "discord_changelog_channels",
            "discord_changelog_server",
            "terminal_command_allowlist",
            "terminal_command_timeout",
            "terminal_max_output_chars",
            "allow_open_application",
            "allow_self_modification",
            "open_application_allowlist",
            "file_tool_root",
            "artifacts_dir",
            "skills_dir",
            "workspaces_dir",
            "default_workspace",
            "notification_channels",
            "multi_agent_enabled",
            "allowed_commands",
            "command_prefix",
            "cron_enabled",
            "cron_state_path",
            "webhook_enabled",
            "webhook_secret",
            "webhook_secret_path",
            # Heartbeat
            "heartbeat_enabled",
            "heartbeat_interval",
            "heartbeat_prompt",
            "heartbeat_channels",
            # Email
            "allow_email",
            "email_imap_host",
            "email_imap_port",
            "email_smtp_host",
            "email_smtp_port",
            "email_username",
            "email_password",
            "email_max_results",
            "email_use_tls",
            # Telegram
            "allow_telegram_bot",
            "telegram_bot_token",
            "telegram_allowed_users",
            "telegram_auto_confirm",
            # Google Calendar
            "allow_calendar",
            "calendar_provider",
            "google_calendar_credentials_path",
            "google_calendar_token_path",
            "calendar_lookahead_days",
            "calendar_default_timezone",
            # Spotify
            "allow_spotify",
            "spotify_client_id",
            "spotify_client_secret",
            "spotify_redirect_uri",
            "spotify_token_path",
            # Notion
            "allow_notion",
            "notion_token",
            "notion_default_database_id",
            # GitHub
            "allow_github",
            "github_token",
            "github_default_repo",
            # Home Assistant
            "allow_home_assistant",
            "home_assistant_url",
            "home_assistant_token",
            # WhatsApp
            "allow_whatsapp",
            "whatsapp_api_url",
            # Threading
            "threading_enabled",
            "threading_auto_title",
            # A2A Protocol
            "a2a_enabled",
            "a2a_agent_name",
            "a2a_agent_description",
            "a2a_auth_key",
            "a2a_known_agents",
            # Orchestration
            "orchestration_enabled",
            "orchestration_max_subtasks",
            "orchestration_timeout",
            # Twitch
            "allow_twitch",
            "twitch_client_id",
            "twitch_client_secret",
            "twitch_bot_username",
            "twitch_broadcaster_id",
            "twitch_eventsub_secret",
            "twitch_eventsub_callback_url",
            "twitch_bot_user_id",
            "twitch_bot_access_token",
            "twitch_chat_reply_enabled",
            # Twitter/X
            "allow_twitter",
            "twitter_client_id",
            "twitter_client_secret",
            "twitter_access_token",
            "twitter_access_token_secret",
            "twitter_bearer_token",
            "twitter_bot_user_id",
            "twitter_poll_interval",
            "twitter_auto_reply_mentions",
            "twitter_autonomous_enabled",
            "twitter_autonomous_interval",
            "twitter_autonomous_max_daily",
            "twitter_autonomous_require_approval",
            "twitter_autonomous_prompt",
        }

    def write_runtime_overrides(self, overrides: dict[str, Any]) -> None:
        try:
            write_runtime_override_payload(overrides)
        except Exception:
            return

    @property
    def memory_path(self) -> Path:
        return MEMORY_DIR / "conversation_index"

    @property
    def logs_path(self) -> Path:
        return LOGS_DIR

    @property
    def docs_index_path(self) -> Path:
        return DOCS_INDEX_DIR

    @property
    def docs_meta_path(self) -> Path:
        return DOCS_META_PATH


config = Config()


def get_llm_config():
    """Get LLM configuration based on provider selection."""
    if config.use_local_models:
        return config.local
    if str(getattr(config, "default_cloud_provider", "openai") or "").strip().lower() == ModelProvider.GEMINI.value:
        return config.gemini
    return config.openai


def get_embedding_config():
    """Get embedding configuration based on provider selection."""
    return config.embedding
