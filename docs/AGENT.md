# AGENT.md (Developer Guide)

This file is for developers extending EchoSpeak’s agent.

---

## Key design goals

- **Local-first by default** (local models, local search, local automation).
- **Per-action confirmation** for any system action.
- **Action tools must never be executed directly by the tool-calling agent.**

---

## Core architecture

### Main components

- `apps/backend/agent/core.py`
  - `LLMWrapper`: provider abstraction (OpenAI, Ollama, **LM Studio (GGUF direct)**, LocalAI, llama.cpp, vLLM)
  - `EchoSpeakAgent`: routing + tool usage + memory + safety gating
- `apps/backend/io_module/personaplex_client.py`
  - `PersonaPlexClient`: Async WebSocket client (Opus/sphn)
  - `PersonaPlexOrchestrator`: High-level lifecycle + tool routing (mic pause/resume)
- `apps/backend/agent/document_store.py`
  - Document RAG store (FAISS + metadata)
- `apps/backend/agent/tools.py`
  - All tools (read-only and action tools)
  - `get_available_tools()` defines which tools exist
- `apps/backend/agent/memory.py`
  - FAISS-based memory (local embeddings fallback if OpenAI key absent)

---

## Safety model (mandatory)

### Pending action / confirmation flow

Action tools must follow:

1. Agent proposes action
2. Agent stores `_pending_action`
3. User must reply `confirm` or `cancel`
4. Only then do we execute

This logic lives in:

- `apps/backend/agent/core.py`
  - `_pending_action`
  - `_is_action_tool()`
  - `_action_allowed()`
  - `_format_pending_action()`
  - `process_query()` confirmation handling

### Preventing bypass via tool-calling agent

LangChain tool-calling agent (`create_tool_calling_agent`) must not receive action tools.

Implementation:

- In `EchoSpeakAgent.__init__`:
  - `self.lc_tools = [t for t in get_available_tools() if t.name not in {<action tools>}]`

If you add a new action tool, you must:

- Add it to `_is_action_tool()`
- Add gating to `_action_allowed()`
- Add a human-readable entry in `_format_pending_action()`
- Exclude it from `lc_tools`

---

## Environment flags

All system actions should be guarded by BOTH:

- `ENABLE_SYSTEM_ACTIONS=true`
- a tool-specific allow flag

Current allow flags:

- `ALLOW_OPEN_CHROME`
- `ALLOW_PLAYWRIGHT`
- `ALLOW_DESKTOP_AUTOMATION`
- `ALLOW_FILE_WRITE`
- `ALLOW_TERMINAL_COMMANDS`

Terminal command safety:

- `TERMINAL_COMMAND_ALLOWLIST` (comma-separated allowlist of command first-tokens)
- `TERMINAL_COMMAND_TIMEOUT` (seconds)
- `TERMINAL_MAX_OUTPUT_CHARS`

File tool root:

- `FILE_TOOL_ROOT` (restricts file tools to a safe base directory)

Optional reliability flag:

- `USE_TOOL_CALLING_LLM` (wraps Ollama model with `tool_calling_llm`)
- `LM_STUDIO_TOOL_CALLING` (enable OpenAI-style tool calling for LM Studio)

Local providers default to **non-tool-calling** to avoid JSON tool-call loops; only enable tool-calling when you explicitly need it.

Multi-session + ops:

- `MULTI_AGENT_ENABLED=true` enables an agent pool keyed by `thread_id` (each session/workspace gets isolated state).
- `ALLOWED_COMMANDS` and `COMMAND_PREFIX` control which slash commands are accepted by the agent.
- `CRON_ENABLED` + `CRON_STATE_PATH` enable cron-style trigger handling.
- `WEBHOOK_ENABLED` + `WEBHOOK_SECRET` / `WEBHOOK_SECRET_PATH` enable signed webhook trigger handling.

LangChain compatibility note:

- `tool-calling-llm` expects the LangChain `0.3.x` ecosystem (keep `langchain*` packages pinned to `<0.4` in `apps/backend/requirements.txt`).

Voice (Pocket-TTS):

- `USE_POCKET_TTS` (enables backend `/tts` endpoint)
- `POCKET_TTS_DEFAULT_VOICE` (voice id like `eponine` or a full prompt URL/path)
- `POCKET_TTS_DEFAULT_VOICE_PROMPT` (voice cloning prompt: local path/http/hf)
- `POCKET_TTS_VARIANT`, `POCKET_TTS_TEMP`, `POCKET_TTS_LSD_DECODE_STEPS`, `POCKET_TTS_EOS_THRESHOLD`

Document RAG + context:

- `DOCUMENT_RAG_ENABLED`
- `DOC_UPLOAD_MAX_MB`
- `SUMMARY_TRIGGER_TURNS`
- `SUMMARY_KEEP_LAST_TURNS`
- `ACTION_PLAN_ENABLED`

Local STT:

- `LOCAL_STT_ENABLED`
- `LOCAL_STT_MODEL`
- `LOCAL_STT_DEVICE`
- `LOCAL_STT_COMPUTE_TYPE`

---

## Tool design guidelines

### Read-only tools

Read-only tools are allowed to execute immediately.

Examples:

- `web_search`
- `youtube_transcript`
- `desktop_list_windows`
- `desktop_find_control`

### web_search quality upgrades

The `web_search` tool was upgraded to return more useful context and higher-quality results without adding heavy dependencies.

Behavior:

- Multi-query support:
  - Separate queries with newlines (recommended) OR use `OR`.
  - Example:
    - `best budget mic 2026\nusb mic podcast`
    - `best budget mic 2026 OR usb mic podcast`
- Aggregation + dedupe:
  - Results are merged across queries.
  - URLs are deduped so you don’t get the same page repeated.
- Retrieve-more then rerank:
  - The tool collects more candidates (up to ~20 unique URLs) and then applies a lightweight keyword-based rerank.
  - Scoring uses title/snippet/page_title/extract text.
- Optional page extraction (Scrapling):
  - Still controlled by `WEB_SEARCH_USE_SCRAPLING=true`.
  - Extract text is compressed down to the most query-relevant sentences so the agent gets “signal”, not walls of text.

---

## Action tools

Action tools must be:

- gated by env flags
- confirmation-gated
- excluded from tool-calling agent tools list
- ideally offer a **dry-run preview** path

Examples:

- `browse_task`
- `desktop_click`
- `desktop_type_text`
- `desktop_send_hotkey`

---

## Pocket-TTS (/tts)

Pocket-TTS is integrated as a backend TTS engine and exposed through:

- `POST /tts` in `apps/backend/api/server.py`

Implementation details:

- `apps/backend/io_module/pocket_tts_engine.py` holds the cached singleton model and voice states.
- The web UI (`apps/web/src/index.tsx`) calls `/tts` and plays the returned WAV.

`/tts` is intentionally not confirmation-gated because it is non-destructive (audio synthesis only).

---

## PersonaPlex WebSocket Protocol

The low-latency voice mode (`io_module/personaplex_client.py`) implements a full-duplex WebSocket bridge:

- **Frame Types**:
  - `0x00`: Handshake (Client -> Server) - Send JSON config
  - `0x01`: Audio (Bi-directional) - Opus encoded frames
  - `0x02`: Text (Server -> Client) - Token events
  - `0x03`: Control (Client -> Server) - Interrupt/Signal
- **Tool Routing**:
  - When `orchestrator` detects tool-intent, it calls `pause_mic()`.
  - The client stops sending frames to the WebSocket.
  - Local tool processing occurs (confirm/execute).
  - After tool completion, `resume_mic()` is called to restart the stream.

## TUI Styling (Bubble Tea)

The Go TUI (`apps/tui/main.go`) uses `lipgloss` for styling. 

Key Style Modifications (v0.2.0):
- **Colors**:
  - `colorBg = "0"` (Pure Black)
  - `colorBorder = "255"` (Pure White)
- **Centering**: Uses `lipgloss.Place` and `lipgloss.JoinVertical(lipgloss.Center, ...)` for the splash screen layout.
- **Microphone Stack**:
  - Logic in `windows_mic.go` uses `wca` to resolve default audio endpoints.
  - `main.go` parses FFmpeg `dshow` output with fallback to lines containing `(audio)`.

## Embeddings + memory (langchain-huggingface)

Embeddings now prefer `langchain-huggingface` to avoid LangChain deprecation warnings, with a fallback to `langchain-community` if needed.

- `apps/backend/agent/memory.py` handles the import fallback.
- `apps/backend/requirements.txt` includes `langchain-huggingface`.

---

## Adding a new tool (checklist)

### 1) Implement tool in `apps/backend/agent/tools.py`

- Prefer `@tool(args_schema=...)` with Pydantic models.
- Keep the tool return value a simple string.

### 2) Register tool in `get_available_tools()`

### 3) If action tool:

- Implement env gating in the tool itself
- Add routing + confirmation behavior in `apps/backend/agent/core.py`
- Add dry-run preview if possible

### 4) Update docs

- Update `README.md`
- Update `docs/INTEGRATIONS.md`

---

## Tool-calling reliability mode (Ollama)

EchoSpeak can optionally wrap `ChatOllama` with `tool_calling_llm`:

- `USE_TOOL_CALLING_LLM=true`

Location:

- `apps/backend/agent/core.py` in `LLMWrapper._create_llm()` (Ollama branch)

If the dependency is missing or incompatible, it falls back to normal `ChatOllama`.
