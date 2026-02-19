"""
Configuration module for Echo Speak.
Loads environment variables and provides centralized configuration management.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from enum import Enum
from typing import Optional
from pydantic import BaseModel

_DOTENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_DOTENV_PATH)

BASE_DIR = Path(__file__).resolve().parent
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


class ModelProvider(str, Enum):
    """Supported model providers."""
    OPENAI = "openai"
    OLLAMA = "ollama"
    LM_STUDIO = "lmstudio"
    LOCALAI = "localai"
    LLAMA_CPP = "llama_cpp"
    VLLM = "vllm"


class LocalModelConfig(BaseModel):
    """Configuration for local model providers."""
    provider: ModelProvider = ModelProvider.OLLAMA
    base_url: str = "http://localhost:11434"
    model_name: str = "llama3"
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
    model: str = "gpt-3.5-turbo"
    temperature: float = 0.7
    max_tokens: int = 4096


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


class APIConfig(BaseModel):
    """Configuration for API server."""
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1


class Config:
    """Main configuration class for Echo Speak."""

    def __init__(self):
        self._load_env_vars()

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

        self.openai = OpenAIConfig(
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL", "gpt-3.5-turbo"),
            temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("OPENAI_MAX_TOKENS", "4096"))
        )

        self.local = LocalModelConfig(
            provider=local_provider,
            base_url=os.getenv("LOCAL_MODEL_URL", "http://localhost:11434"),
            model_name=os.getenv("LOCAL_MODEL_NAME", "llama3"),
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
            model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
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

        self.web_search_timeout = int(os.getenv("WEB_SEARCH_TIMEOUT", "10"))
        self.searxng_url = os.getenv("SEARXNG_URL", "").strip()
        self.searxng_timeout = int(os.getenv("SEARXNG_TIMEOUT", str(self.web_search_timeout)))

        self.web_search_use_scrapling = os.getenv("WEB_SEARCH_USE_SCRAPLING", "false").lower() == "true"
        self.web_search_scrapling_top_k = int(os.getenv("WEB_SEARCH_SCRAPLING_TOP_K", "2"))
        self.web_search_scrapling_timeout = int(os.getenv("WEB_SEARCH_SCRAPLING_TIMEOUT", "20"))
        self.web_search_scrapling_max_chars = int(os.getenv("WEB_SEARCH_SCRAPLING_MAX_CHARS", "1500"))
        self.web_search_scrapling_stealthy_headers = os.getenv("WEB_SEARCH_SCRAPLING_STEALTHY_HEADERS", "true").lower() == "true"
        self.web_search_scrapling_impersonate = os.getenv("WEB_SEARCH_SCRAPLING_IMPERSONATE", "chrome")
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

        self.memory_default_mode = os.getenv("MEMORY_DEFAULT_MODE", "general").strip() or "general"

        self.memory_partition_enabled = os.getenv("MEMORY_PARTITION_ENABLED", "false").lower() == "true"

        self.file_memory_enabled = os.getenv("FILE_MEMORY_ENABLED", "false").lower() == "true"
        self.file_memory_dir = os.getenv("FILE_MEMORY_DIR", str(FILE_MEMORY_DIR)).strip() or str(FILE_MEMORY_DIR)
        self.file_memory_log_conversations = os.getenv("FILE_MEMORY_LOG_CONVERSATIONS", "true").lower() == "true"
        self.file_memory_max_chars = int(os.getenv("FILE_MEMORY_MAX_CHARS", "2000") or 2000)
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

        self.local_stt_enabled = os.getenv("LOCAL_STT_ENABLED", "false").lower() == "true"
        self.local_stt_model = os.getenv("LOCAL_STT_MODEL", "base").strip() or "base"
        self.local_stt_device = os.getenv("LOCAL_STT_DEVICE", "cpu").strip() or "cpu"
        self.local_stt_compute_type = os.getenv("LOCAL_STT_COMPUTE_TYPE", "int8").strip() or "int8"

        self.use_pocket_tts = os.getenv("USE_POCKET_TTS", "false").lower() == "true"
        self.pocket_tts_default_voice = os.getenv("POCKET_TTS_DEFAULT_VOICE", "").strip()
        self.pocket_tts_default_voice_prompt = os.getenv("POCKET_TTS_DEFAULT_VOICE_PROMPT", "").strip()
        self.pocket_tts_variant = os.getenv("POCKET_TTS_VARIANT", "b6369a24").strip() or "b6369a24"
        self.pocket_tts_temp = float(os.getenv("POCKET_TTS_TEMP", "0.7"))
        self.pocket_tts_lsd_decode_steps = int(os.getenv("POCKET_TTS_LSD_DECODE_STEPS", "1"))
        self.pocket_tts_eos_threshold = float(os.getenv("POCKET_TTS_EOS_THRESHOLD", "-4.0"))
        self.pocket_tts_max_chars = int(os.getenv("POCKET_TTS_MAX_CHARS", "8000"))
        self.tts_response_mode = os.getenv("TTS_RESPONSE_MODE", "brief").strip().lower() or "brief"
        self.tts_response_max_chars = int(os.getenv("TTS_RESPONSE_MAX_CHARS", "0") or 0)
        self.tts_brief_words = int(os.getenv("TTS_BRIEF_WORDS", "20") or 20)

        self.enable_system_actions = os.getenv("ENABLE_SYSTEM_ACTIONS", "false").lower() == "true"
        self.allow_open_chrome = os.getenv("ALLOW_OPEN_CHROME", "false").lower() == "true"
        self.allow_playwright = os.getenv("ALLOW_PLAYWRIGHT", "false").lower() == "true"
        self.allow_desktop_automation = os.getenv("ALLOW_DESKTOP_AUTOMATION", "false").lower() == "true"
        self.allow_file_write = os.getenv("ALLOW_FILE_WRITE", "false").lower() == "true"
        self.allow_terminal_commands = os.getenv("ALLOW_TERMINAL_COMMANDS", "false").lower() == "true"
        raw_terminal_allow = os.getenv("TERMINAL_COMMAND_ALLOWLIST", "")
        self.terminal_command_allowlist = [
            a.strip().lower()
            for a in raw_terminal_allow.replace("\n", ",").split(",")
            if a.strip()
        ]
        self.terminal_command_timeout = int(os.getenv("TERMINAL_COMMAND_TIMEOUT", "20") or 20)
        self.terminal_max_output_chars = int(os.getenv("TERMINAL_MAX_OUTPUT_CHARS", "8000") or 8000)
        self.allow_open_application = os.getenv("ALLOW_OPEN_APPLICATION", "false").lower() == "true"
        raw_apps = os.getenv("OPEN_APPLICATION_ALLOWLIST", "")
        self.open_application_allowlist = [
            a.strip().lower()
            for a in raw_apps.replace("\n", ",").split(",")
            if a.strip()
        ]
        self.file_tool_root = os.getenv("FILE_TOOL_ROOT", str(BASE_DIR)).strip() or str(BASE_DIR)
        self.artifacts_dir = os.getenv("ARTIFACTS_DIR", str(ARTIFACTS_DIR)).strip() or str(ARTIFACTS_DIR)

        self.skills_dir = os.getenv("SKILLS_DIR", str(SKILLS_DIR)).strip() or str(SKILLS_DIR)
        self.workspaces_dir = os.getenv("WORKSPACES_DIR", str(WORKSPACES_DIR)).strip() or str(WORKSPACES_DIR)
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
    return config.openai


def get_embedding_config():
    """Get embedding configuration based on provider selection."""
    return config.embedding
