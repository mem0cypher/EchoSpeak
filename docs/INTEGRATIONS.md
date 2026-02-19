# EchoSpeak Advanced Tooling (All Integrations)

This document explains the **advanced tooling stack** that was implemented in EchoSpeak, how each integration works, where the code lives, how to enable it, and how it improves the assistant.

---

## TL;DR (What you have now)

EchoSpeak is now a **local-first** assistant with:

- **Local search (SearxNG)** with a **DDG fallback**
- **YouTube transcript extraction + summarization**
- **High-quality CPU TTS (Pocket-TTS)** exposed via backend `POST /tts` and used by the web UI
- **Local LLM providers**: Ollama, **LM Studio (GGUF direct)**, LocalAI, llama.cpp, vLLM (provider switcher)
- **Model listing** via `GET /provider/models` (Ollama tags + OpenAI-compatible `/v1/models` for LM Studio/LocalAI/vLLM)
- **Local browser automation** via **Playwright** (`browse_task`) with safety gating
- **Windows desktop automation** via **pywinauto** (with **PyAutoGUI fallback**) with safety gating
- **Document RAG** (upload + FAISS index + doc sources)
- **Offline local STT** via `/stt` (optional)
- **Go TUI** (Bubble Tea + Lipgloss) that consumes the same `/query/stream` events
- **Summary-only TTS playback in the TUI** via the streaming `spoken_text` field
- **More reliable tool calling for Ollama** via optional `tool_calling_llm` (opt-in; local tool-calling is OFF by default)
- **Safer tools** using **Pydantic args schemas** (started with `calculate`)
- A strict **per-action confirmation** safety flow so the model cannot execute system actions without your approval
- **Ops tooling**: doctor diagnostics (`/doctor`) plus cron/webhook triggers (`/trigger/cron`, `/trigger/webhook`)
- **Multi-agent session routing** keyed by `thread_id`

---

## The safety foundation (most important)

### What it does
EchoSpeak supports **action tools** (browser actions, desktop automation, app launching). These are **never executed immediately**.

Instead, Echo:

1. Proposes the action.
2. Stores it as a **pending action**.
3. Waits for you to reply **`confirm`** or **`cancel`**.

### Why this matters
Local LLMs can occasionally:

- call tools at the wrong time
- hallucinate tool parameters
- misroute intent

This safety layer prevents accidental actions.

### Action Parser pass (LLM-driven)

EchoSpeak also runs an Action Parser pass before heuristic tool routing. The Action Parser returns a single structured JSON action (or “none”), then the agent validates that proposed action against:

- env hard gates (`ENABLE_SYSTEM_ACTIONS`, tool-specific `ALLOW_*` flags)
- workspace tool allowlist (ceiling)
- file root and terminal allowlist enforcement (tool-level safety)

If valid and confirmation-gated, Echo proposes the action and waits for you to reply `confirm` or `cancel`.

Config:

- `ACTION_PARSER_ENABLED=true` (default)

### Where it lives
- `apps/backend/agent/core.py`
  - `_pending_action`
  - `_is_action_tool()`
  - `_action_allowed()`
  - `_format_pending_action()`
  - `process_query()` pending-action flow
  - filtering action tools out of the tool-calling agent (`lc_tools`)

### Workspaces + skills allowlist semantics

- Workspaces define the tool allowlist ceiling.
- Skills can only further restrict tool access; skills must not expand tool access beyond the workspace.

### What counts as an action tool
Current action tools include:

- `open_chrome`
- `browse_task` (Playwright)
- Desktop automation actions:
  - `desktop_click`
  - `desktop_type_text`
  - `desktop_activate_window`
  - `desktop_send_hotkey`
- `file_write`
- File mutation tools:
  - `file_move`
  - `file_copy`
  - `file_delete`
  - `file_mkdir`
- `terminal_run` (PowerShell; allowlisted)

Read-only tools (search, time, transcript, list windows, find control, file list/read, etc.) run immediately.

---

## Local LLM providers + model listing

### What it does
EchoSpeak can run local models through multiple providers. **LM Studio runs GGUF directly** (llama.cpp/MLX under the hood) and exposes an **OpenAI-compatible API**. LocalAI and vLLM are also OpenAI-compatible; llama.cpp runs GGUF directly via the Python bindings.

### Where it lives
- `apps/backend/config.py` - `ModelProvider`, `LocalModelConfig`
- `apps/backend/agent/core.py` - `LLMWrapper` provider setup
- `apps/backend/api/server.py` - `/provider`, `/provider/switch`, `/provider/models`
- `apps/web/src/index.tsx` - provider dropdown + model list UI

### Enable it
In `.env`:

- `USE_LOCAL_MODELS=true`
- `LOCAL_MODEL_PROVIDER=lmstudio | ollama | localai | llama_cpp | vllm`
- `LOCAL_MODEL_URL=http://localhost:1234` (LM Studio default)
- `LOCAL_MODEL_NAME=qwen/qwen3-coder-30b` (use the model id from `/v1/models`)

### Model listing
- `GET /provider/models?provider=ollama` uses Ollama `/api/tags`
- `GET /provider/models?provider=lmstudio|localai|vllm` uses OpenAI-compatible `/v1/models`
- If empty, ensure the local server is running and reachable at `LOCAL_MODEL_URL`

### Tool-calling note
Local providers **default to non-tool-calling** to avoid JSON tool-call loops. Enable tool-calling only if you explicitly want it (see tool_calling section below).

### LM Studio-only lock
The backend can be locked to **LM Studio only** (UI and API) via a code constant in:

- `LM_STUDIO_ONLY = True` in `apps/backend/api/server.py`

When enabled:

- Forces provider to `lmstudio` regardless of request
- `/provider/switch` returns 403 (disabled)
- `/provider` only advertises LM Studio
- UI provider dropdown shows only LM Studio and disables switching

---

## Pocket-TTS voice (backend `/tts` + web UI playback)

### What it does
Adds a **high-quality CPU TTS** engine (Kyutai Pocket-TTS) and exposes it as:

- a backend API endpoint `POST /tts` returning `audio/wav`
- web UI playback that uses backend audio from `/tts`

Supports:

- **fixed voice selection** (e.g., `eponine`, `alba`, `marius`, etc.)
- **voice cloning** via a `voice_prompt` (local path, `http(s)://...wav`, or `hf://...wav`)

Note: the **Go TUI** uses the same backend `/tts` endpoint, but plays only the brief spoken summary sent as `spoken_text` in the stream `final` event.

---

## Go TUI (Bubble Tea) + streaming

### What it does
EchoSpeak includes a Go terminal UI under `apps/tui/` that connects to the backend via:

- `POST /query/stream` for newline-delimited JSON events
- `POST /tts` for speech playback

### Where it lives
- `apps/tui/main.go`
- Backend stream endpoint: `apps/backend/api/server.py`

### Key stream detail: `spoken_text`
The backend stream `final` event includes:

- `response`: full assistant response text (shown in the UI)
- `spoken_text`: a brief TTS summary (used for TTS playback)

This keeps voice playback short without changing the visible chat output.

Note: fixed catalog voices are the recommended default because they avoid requiring gated voice-cloning weights.

### Where it lives
- `apps/backend/io_module/pocket_tts_engine.py`
  - `PocketTTSEngine` (model + voice-state caching)
  - `get_pocket_tts_engine()` singleton
- `apps/backend/api/server.py`
  - `POST /tts`
- `apps/web/src/index.tsx`
  - `speakText()` fetches `/tts` and plays the WAV in the browser

### Enable it
In `.env`:

- `USE_POCKET_TTS=true`

Optional defaults/tuning:

- `POCKET_TTS_DEFAULT_VOICE=EPONINE`
- `POCKET_TTS_DEFAULT_VOICE_PROMPT=`
- `POCKET_TTS_VARIANT=b6369a24`
- `POCKET_TTS_TEMP=0.7`
- `POCKET_TTS_LSD_DECODE_STEPS=1`
- `POCKET_TTS_EOS_THRESHOLD=-4.0`

### How it improves EchoSpeak
- Local-first (CPU), no external TTS API
- Unified voice across UI and backend via `/tts`

---

## 1) Local web search (SearxNG + fallback)

### What it does
The `web_search` tool prefers **SearxNG** if configured, and otherwise falls back to DuckDuckGo.

### Where it lives
- `apps/backend/agent/tools.py`
  - `web_search(query: str) -> str`
- `apps/backend/config.py`
  - `config.searxng_url`
  - `config.searxng_timeout`
- `apps/backend/.env.example`
  - `SEARXNG_URL`
  - `SEARXNG_TIMEOUT`

### How it works
- If `SEARXNG_URL` is set:
  - Calls `GET {SEARXNG_URL}/search?format=json&q=...`
  - Formats top results
- If SearxNG fails/unset:
  - Uses `ddgs` / `duckduckgo-search`

### How it improves EchoSpeak
- Local-first search routing
- Better control over sources
- Works offline on a LAN when SearxNG is hosted locally

---

## 2) YouTube transcript tool (video understanding)

### What it does
Fetches transcripts for YouTube videos and returns text; the agent then summarizes it.

### Where it lives
- `apps/backend/agent/tools.py`
  - `_extract_youtube_video_id(url)`
  - `youtube_transcript(url, language=None)`
- `apps/backend/agent/core.py`
  - routing + transcript summarization
- `apps/backend/requirements.txt`
  - `youtube-transcript-api`

### How it works
- Detects `youtube.com` / `youtu.be` link
- Extracts video id
- Calls `youtube-transcript-api`
- Truncates long transcripts to a safe max
- Agent prompts the LLM to summarize

### Example
User:
- "Get the transcript for https://www.youtube.com/watch?v=... and summarize"

---

## 3) Playwright browser automation (local-first)

### What it does
`browse_task(url, task=None)` loads a page headlessly and extracts the body text so Echo can summarize it.

### Where it lives
- `apps/backend/agent/tools.py`
  - `browse_task(url, task=None)`
- `apps/backend/agent/core.py`
  - confirmation gating + summarization
- `apps/backend/config.py` / `.env.example`
  - `ALLOW_PLAYWRIGHT`
- `apps/backend/requirements.txt`
  - `playwright`

### Enable it
In `.env`:

- `ENABLE_SYSTEM_ACTIONS=true`
- `ALLOW_PLAYWRIGHT=true`

One-time install (required by Playwright):

- `playwright install`

### How it improves EchoSpeak
- Allows “browse and read this page” tasks
- Still local-first and confirmation-gated

---

## 4) Windows desktop automation (pywinauto + fallback)

### What it does
Adds safe Windows automation tools. Read-only inspection tools run immediately; actions require confirmation.

### Where it lives
- `apps/backend/agent/tools.py`
  - `desktop_list_windows`
  - `desktop_find_control`
  - `desktop_click` (action)
  - `desktop_type_text` (action)
  - `desktop_activate_window` (action)
  - `desktop_send_hotkey` (action)
- `apps/backend/agent/core.py`
  - confirmation gating, preview, and argument extraction
- `apps/backend/config.py` / `.env.example`
  - `ALLOW_DESKTOP_AUTOMATION`

### Enable it
In `.env`:

- `ENABLE_SYSTEM_ACTIONS=true`
- `ALLOW_DESKTOP_AUTOMATION=true`

### How it works
- Uses `pywinauto` with UIA backend to find windows/controls
- Uses `click_input()` / `set_edit_text()` when possible
- Falls back to `pyautogui` for click/type if pywinauto fails
- `core.py` provides:
  - dry-run preview
  - inference helpers (window/control hints)
  - confirmation gating

### Examples
- List windows:
  - `list windows`
  - `desktop_list_windows filter="chrome"`

- Find controls:
  - `find control window_title="Notepad" control_type="Edit"`

- Click (preview + confirm):
  - `click OK in window "Installer"`

- Type (preview + confirm):
  - `type "hello" into window "Notepad"`

- Hotkey (preview + confirm):
  - `press ctrl+l in window "Chrome"`

---

## 5) Optional tool calling reliability (tool_calling_llm)

### What it does
Improves the ability of local Ollama models to emit structured tool calls.

### Where it lives
- `apps/backend/agent/core.py`
  - `LLMWrapper._create_llm()` Ollama branch
- `apps/backend/config.py`
  - `USE_TOOL_CALLING_LLM`
- `apps/backend/requirements.txt`
  - `tool-calling-llm`

### Enable it
In `.env`:

- `USE_TOOL_CALLING_LLM=true`

Tool calling for local providers is **opt-in** and only used for Ollama. If the package isn’t installed or is incompatible, EchoSpeak falls back automatically.

---

## 6) Pydantic args schemas / structured tools (started with calculate)

### What it does
Tools become safer and easier for LLMs to call correctly.

### Where it lives
- `apps/backend/agent/tools.py`
  - `CalculateArgs` + `@tool(args_schema=CalculateArgs)`

### Improvement
- The model is less likely to send malformed tool arguments.

---

## Embeddings + memory (langchain-huggingface)

### What it does
Conversation memory uses FAISS for vector search. Embeddings now **prefer `langchain-huggingface`** to avoid LangChain deprecation warnings, with a safe fallback to `langchain-community` if needed.

### Where it lives
- `apps/backend/agent/memory.py` (HuggingFaceEmbeddings import + fallback)
- `apps/backend/requirements.txt` (`langchain-huggingface`)

---

## 7) Notes on larger frameworks (Skyvern / browser_agent)

We intentionally did **not** integrate Skyvern because it requires external services / API keys.

The current architecture makes adding such systems later straightforward (behind flags) while keeping the local-first default.

---

## Environment variables (quick reference)

### Local model
- `USE_LOCAL_MODELS`
- `LOCAL_MODEL_PROVIDER`
- `LOCAL_MODEL_URL`
- `LOCAL_MODEL_NAME`
  - LM Studio default: `http://localhost:1234`
  - LocalAI default: `http://localhost:8080`
  - vLLM default: `http://localhost:8000`
  - Ollama default: `http://localhost:11434`

### Tool calling reliability
- `USE_TOOL_CALLING_LLM`

### Voice (Pocket-TTS)
- `USE_POCKET_TTS`
- `POCKET_TTS_DEFAULT_VOICE`
- `POCKET_TTS_DEFAULT_VOICE_PROMPT`
- `POCKET_TTS_VARIANT`
- `POCKET_TTS_TEMP`
- `POCKET_TTS_LSD_DECODE_STEPS`
- `POCKET_TTS_EOS_THRESHOLD`
- `POCKET_TTS_MAX_CHARS`

### Search
- `WEB_SEARCH_TIMEOUT`
- `SEARXNG_URL`
- `SEARXNG_TIMEOUT`

### Vision OCR
- `TESSERACT_PATH`

### System actions safety
- `ENABLE_SYSTEM_ACTIONS`
- `ALLOW_OPEN_CHROME`
- `ALLOW_PLAYWRIGHT`
- `ALLOW_DESKTOP_AUTOMATION`
- `ALLOW_FILE_WRITE`
- `ALLOW_TERMINAL_COMMANDS`
- `TERMINAL_COMMAND_ALLOWLIST`
- `TERMINAL_COMMAND_TIMEOUT`
- `TERMINAL_MAX_OUTPUT_CHARS`
- `FILE_TOOL_ROOT`

### Action Parser
- `ACTION_PARSER_ENABLED`

### Multi-session + ops
- `MULTI_AGENT_ENABLED`
- `ALLOWED_COMMANDS`
- `COMMAND_PREFIX`
- `CRON_ENABLED`
- `CRON_STATE_PATH`
- `WEBHOOK_ENABLED`
- `WEBHOOK_SECRET`
- `WEBHOOK_SECRET_PATH`

---

## File map (important files)

- `apps/backend/agent/core.py`
  - routing + tool-calling agent + safety confirmation layer
- `apps/backend/agent/tools.py`
  - all tools (search, vision, browser, desktop)
- `apps/backend/config.py`
  - env config / flags
- `apps/backend/.env.example`
  - example env file
- `apps/backend/requirements.txt`
  - dependencies

---

## Current status
All requested integrations are implemented, including LM Studio (GGUF direct) provider support with model listing, tool-calling defaulted OFF for local providers to avoid JSON tool-call loops, langchain-huggingface embeddings, and UI auto-retry for backend connectivity.
