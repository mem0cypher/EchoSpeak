# Echo Speak 3.0 (Low-Latency Edition)

A standalone voice AI assistant (Jarvis-like) with a modern dark UI. Built with a Python backend (FastAPI + LangChain/LangGraph) and a React/Vite web front-end.

EchoSpeak works end-to-end like this:

- The React/Vite web UI (`apps/web/`) captures text input and mic speech using the browser’s `SpeechRecognition`, or local STT via `/stt` when `/stt/info` reports enabled.
- The UI sends requests to the FastAPI backend (`apps/backend/api/server.py`) using `POST /query/stream` (newline-delimited JSON events).
- The backend runs `EchoSpeakAgent` (`apps/backend/agent/core.py`) which can:
  - use LangGraph tool-calling (when enabled)
  - run an **LLM-driven Action Parser pass** (single-action JSON) to interpret “do something on my machine” requests into a structured action before any tool routing
  - call local-first tools from `apps/backend/agent/tools.py` (search, YouTube transcript, Playwright browse, vision/OCR, Windows desktop automation)
  - store/retrieve long-term memory via FAISS (`apps/backend/agent/memory.py`), optionally partitioned by mode/thread_id
  - maintain conversation summaries + optional memory-flush notes for durable logging
  - write durable memory logs (`FILE_MEMORY_DIR/MEMORY.md` + `FILE_MEMORY_DIR/memory/YYYY-MM-DD.md`) when enabled
- Any **system action** is previewed and requires an explicit `confirm` (with an optional action plan summary). The Action Parser pass proposes an action; the existing confirmation gate executes it.
- For ultra-low latency voice, the system uses **PersonaPlex** (`apps/backend/io_module/personaplex_client.py`), a full-duplex WebSocket connection with Opus encoding.
- For standard voice output, the backend uses **Pocket-TTS** (`apps/backend/io_module/pocket_tts_engine.py`) via `POST /tts`.

This repo now includes an **advanced local-first tooling stack**:

- **Local search** via optional **SearxNG** (with DDG fallback)
- **YouTube transcript extraction + summarization**
- **High-quality CPU TTS (Pocket-TTS)** via backend `POST /tts`
- **Local browser automation** via **Playwright** (`browse_task`) (confirmation-gated)
- **Windows desktop automation** via **pywinauto** (PyAutoGUI fallback) (confirmation-gated)
- **Document RAG** with chunk-level citations, hybrid retrieval (BM25 + FAISS), optional reranking, and GraphRAG-lite expansion (`/documents`)
- **File tools + screenshots** with a restricted root (`file_list`, `file_read`, `file_write`, `take_screenshot`)
- **Offline local STT** (optional, `/stt`)
- **LangGraph ReAct agent** with:
  - **context window trimming** (`pre_model_hook` + `trim_messages`)
  - **thread-level persistence** via `thread_id`
- **Optional tool-calling reliability wrapper** for Ollama via `tool_calling_llm`
- **Safer tool inputs** using Pydantic args schemas (e.g., `calculate`, `web_search`, `analyze_screen`, `vision_qa`, `take_screenshot`, `open_chrome`)
- **Provider model lists** (Ollama tags + OpenAI-compatible `/v1/models` for LM Studio/LocalAI/vLLM)

---

## Safety: Action Confirmation Flow

Some tools perform system actions (browser automation, desktop automation, app launching). These are **never executed immediately**. Echo will also summarize a short plan (when `ACTION_PLAN_ENABLED=true`).

Flow:

1. Echo proposes the action.
2. Echo asks you to reply `confirm` or `cancel`.
3. Only after `confirm`, the action is executed.

This prevents accidental automation caused by misroutes or tool-call hallucinations.

### Action Parser pass (LLM-driven)

EchoSpeak runs an Action Parser pass before heuristic tool routing. The Action Parser returns a single JSON action (or “none”) and is then validated against:

- env hard gates (e.g. `ENABLE_SYSTEM_ACTIONS`, `ALLOW_FILE_WRITE`, `ALLOW_TERMINAL_COMMANDS`)
- workspace tool allowlist (ceiling)
- file root + terminal allowlist enforcement (tool-level safety)

Config:

- `ACTION_PARSER_ENABLED=true` (default)

## Architecture Overview

```
EchoSpeak/
├── apps/web/                 # React/Vite web front-end
├── apps/tui/                 # Go TUI (Bubble Tea)
└── apps/backend/             # Backend core
    ├── app.py               # Entry point (text/api modes)
    ├── config.py            # Pydantic configuration
    ├── api/server.py        # FastAPI REST API
    ├── agent/
    │   ├── core.py          # EchoSpeakAgent with LLMWrapper
    │   ├── document_store.py # Document RAG store
    │   ├── memory.py        # FAISS vector memory
    │   └── tools.py         # Tools: search, vision, youtube, browser, desktop automation
    ├── io_module/
    │   ├── personaplex_client.py # Low-latency WebSocket Client (Opus/sphn)
    │   ├── stt_engine.py     # Local STT (faster-whisper)
    │   └── vision.py        # Screen capture & OCR
    └── requirements.txt     # Python dependencies
```

---

## How It Works

### Query lifecycle (from prompt to action)

When you send a message (web UI, TUI, or API client), the backend runs a consistent flow:

```text
User message
  |
  v
FastAPI (/query or /query/stream)
  |
  v
EchoSpeakAgent.process_query()
  |
  +--> Pending action?
  |      - Only accept: confirm / cancel
  |      - If confirm: execute tool
  |
  +--> Slash command?
  |      - /onboard, /workspace, /doctor, ...
  |
  +--> Action Parser pass (LLM-driven)
  |      - Return: single JSON action (or none)
  |      - Validate against policy and safety gates
  |      - If system action: create pending action and ask for confirm
  |
  +--> Heuristic tool routing (fallback)
  |
  +--> Normal assistant response (LLM)
```

The Action Parser pass is what makes the system feel like it “reasons first, then asks”. It proposes an action, but the existing confirmation gate controls execution.

### What goes into the model context

EchoSpeak composes prompts using:

- system prompt
- active workspace prompt
- active skills prompt(s)
- optional retrieved memory
- optional document context (RAG)
- recent conversation turns

Separately, the Action Parser pass also includes a policy summary (workspace/tool allowlist, `FILE_TOOL_ROOT`, terminal allowlist) so the model proposes actions that match the current permissions.

### 1. Web UI Layer (`apps/web/`)

The React/Vite web application provides:

- **Chat UI** with animated message lines
- **Mic input** using the browser’s built-in `SpeechRecognition` or optional local STT (`/stt`)
- **Text input + streaming** to `POST http://localhost:8000/query/stream` (the UI consumes newline-delimited JSON events)
- **Voice playback** using backend `POST /tts` (Pocket-TTS)

### 2. Backend Layer (`apps/backend/`)

#### `app.py` - Entry Point
Two modes of operation:
```bash
python app.py --mode text      # Console text chat
python app.py --mode api       # REST API server (default, what UI uses)
```

#### `api/server.py` - FastAPI Server

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | API info |
| `/health` | GET | Health check |
| `/query` | POST | Process user message |
| `/query/stream` | POST | Stream agent events (UI endpoint) |
| `/history` | GET | Get conversation history |
| `/history/clear` | POST | Clear short-term conversation history |
| `/memory` | GET | List saved long-term memory items (FAISS) |
| `/memory/delete` | POST | Delete memory items by id |
| `/memory/clear` | POST | Clear long-term memory (FAISS) |
| `/documents` | GET | List uploaded documents |
| `/documents/upload` | POST | Upload a document (txt/md/pdf) |
| `/documents/delete` | POST | Delete documents by id |
| `/documents/clear` | POST | Clear all uploaded documents |
| `/doctor` | GET | Environment and toolchain diagnostics |
| `/sessions` | GET | List active sessions (thread_id pool) |
| `/agents` | GET | Alias for `/sessions` |
| `/trigger/cron` | POST | Cron-style trigger runner (when enabled) |
| `/trigger/webhook` | POST | Signed webhook trigger runner (when enabled) |
| `/provider` | GET | Current model info |
| `/provider/models` | GET | List models for current/selected provider |
| `/provider/switch` | POST | Switch LLM provider (disabled when the backend is locked to LM Studio) |
| `/vision/analyze` | POST | OCR screen analysis |
| `/vision/capture` | POST | Get screenshot |
| `/vision/info` | GET | Screen/monitor info |
| `/tts` | POST | Text-to-speech (Pocket-TTS) returning `audio/wav` |
| `/stt/info` | GET | Local STT status |
| `/stt` | POST | Local STT transcription (multipart audio) |
| `/metrics` | GET | Lightweight request/tool latency metrics |

#### `agent/core.py` - Core Agent

- **LLMWrapper** - Unified interface for:
  - OpenAI (cloud)
  - Ollama (local)
  - LM Studio (GGUF direct via OpenAI-compatible API)
  - LocalAI (OpenAI-compatible)
  - llama.cpp (direct GGUF path)
  - vLLM (OpenAI-compatible)

- **EchoSpeakAgent** - Main agent class:
  - Processes queries via LLM
  - Manages conversation memory
  - Provides tools (search, vision, YouTube transcript, browser automation, desktop automation)
  - Uses per-query tool routing (query classification) to expose only the most relevant tools
  - Adds a reflection step after web tool calls to decide: accept, retry with alternate tool, or ask a clarifying question
  - Enforces **per-action confirmation** for system actions
  - Maintains conversation summaries + optional memory-flush notes for long-term logs
  - Supports skills/workspaces to inject prompts and tool allowlists

#### `agent/memory.py` - Vector Memory

- Uses **FAISS** for semantic search.
- Embeddings are selected by `EMBEDDING_PROVIDER`:
  - `lmstudio`: uses LM Studio's OpenAI-compatible embeddings endpoint (no OpenAI key required)
  - `openai`: uses OpenAI embeddings (requires `OPENAI_API_KEY`)
  - otherwise: falls back to local HuggingFace embeddings when available
- Stores conversation history + metadata; retrieves relevant context for queries
- Optional per-session partitioning by mode/thread_id (`MEMORY_PARTITION_ENABLED=true`)
- Rolling summaries (`SUMMARY_TRIGGER_TURNS`) plus optional memory-flush notes to daily/curated logs
- File-based memory logs (daily + curated) with pre-compaction flush

#### `agent/document_store.py` - Document RAG

- Chunks documents with `RecursiveCharacterTextSplitter` (chunk_size=950, overlap=160)
- Stores chunks in FAISS under `apps/backend/data/documents/index`
- Document metadata saved in `apps/backend/data/documents/documents.json`
- Optional graph expansion index at `apps/backend/data/documents/index/doc_graph.json`
- Hybrid retrieval (BM25 + FAISS), optional CrossEncoder rerank, GraphRAG-lite expansion
- Returns `doc_sources` with chunk metadata + preview text and builds labeled context blocks

#### `agent/tools.py` - Agent Tools

| Tool | Description |
|------|-------------|
| `web_search` | Web search (SearxNG if configured, otherwise DDG). Supports multi-query aggregation + dedupe + lightweight reranking and optional page extraction. |
| `live_web_search` | Live/dynamic browsing with Playwright for current info (scores, weather, stocks). Requires `ENABLE_SYSTEM_ACTIONS=true` and `ALLOW_PLAYWRIGHT=true`. |
| `get_system_time` | Current system time |
| `calculate` | Safe math expression evaluation (Pydantic args schema) |
| `analyze_screen` | OCR of screen content |
| `vision_qa` | Vision Q&A using local Ollama VLM (when available) |
| `youtube_transcript` | Fetch YouTube transcripts |
| `browse_task` | Browse a page headlessly via Playwright (confirmation-gated) |
| `desktop_list_windows` | List open Windows desktop windows |
| `desktop_find_control` | Find UI controls in a window |
| `desktop_click` | Click a UI control (confirmation-gated) |
| `desktop_type_text` | Type into a UI control (confirmation-gated) |
| `desktop_activate_window` | Focus/activate a window (confirmation-gated) |
| `desktop_send_hotkey` | Send hotkey combos (confirmation-gated) |
| `open_chrome` | Open Chrome (confirmation-gated) |
| `file_list` | List files in a directory (restricted to `FILE_TOOL_ROOT`) |
| `file_read` | Read a text file (restricted to `FILE_TOOL_ROOT`) |
| `file_write` | Write text to a file (confirmation-gated) |
| `file_move` | Move/rename a file/folder (confirmation-gated; restricted to `FILE_TOOL_ROOT`) |
| `file_copy` | Copy a file/folder (confirmation-gated; restricted to `FILE_TOOL_ROOT`) |
| `file_delete` | Delete a file/folder (confirmation-gated; restricted to `FILE_TOOL_ROOT`) |
| `file_mkdir` | Create a folder (confirmation-gated; restricted to `FILE_TOOL_ROOT`) |
| `terminal_run` | Run a PowerShell command (confirmation-gated; allowlisted; `cwd` restricted to `FILE_TOOL_ROOT`) |
| `take_screenshot` | Capture a screenshot to disk |

System actions (`browse_task`, `open_chrome`, `desktop_*`, `file_write`, `file_move`, `file_copy`, `file_delete`, `file_mkdir`, `terminal_run`) require `ENABLE_SYSTEM_ACTIONS=true` plus the matching `ALLOW_*` flag, and must be confirmed by the user.

Additional safety controls:

- File tools are restricted to `FILE_TOOL_ROOT`.
- `terminal_run` is restricted to `FILE_TOOL_ROOT` for `cwd`, and the command must match `TERMINAL_COMMAND_ALLOWLIST`.

`vision_qa` is only supported when the local provider is Ollama.

#### `io_module/vision.py` - Vision Module

- **Screen capture** - Uses `mss` library
- **OCR** - Uses `pytesseract` (requires Tesseract installed)
- Requires OpenCV (`cv2`) and NumPy

---

## Setup & Installation

### 1. Create a virtual environment

```bash
cd apps/backend
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

Fish shell:

```fish
cd apps/backend
python -m venv .venv
source .venv/bin/activate.fish
python -m pip install -U pip
python -m pip install -r requirements.txt
```

Optional (Playwright browser automation):
```powershell
playwright install
```

To enable Playwright-based tools at runtime, set in `apps/backend/.env`:

- `ENABLE_SYSTEM_ACTIONS=true`
- `ALLOW_PLAYWRIGHT=true`

**Optional (for vision/OCR):**
```
pytesseract>=0.3.10
tesseract (Windows installer)
```

### 3. Configure Local LLM

Edit `.env` file:
```env
# Use local models (true/false)
USE_LOCAL_MODELS=true

# Tool calling for local providers is OFF by default to avoid JSON tool-call loops
# (Enable only if you want tool_calling_llm for Ollama)
USE_TOOL_CALLING_LLM=false

# Enable OpenAI-compatible tool-calling for LM Studio
LM_STUDIO_TOOL_CALLING=true

# Optional: trim chat history sent to the LLM (LangGraph)
# If LLM_TRIM_MAX_TOKENS=0, EchoSpeak derives a budget from LOCAL_MODEL_CONTEXT minus LLM_TRIM_RESERVE_TOKENS.
LLM_TRIM_MAX_TOKENS=0
LLM_TRIM_RESERVE_TOKENS=512

# Optional: durable memory logs (Clawdbot-style)
FILE_MEMORY_ENABLED=false
FILE_MEMORY_DIR=
FILE_MEMORY_LOG_CONVERSATIONS=true
FILE_MEMORY_MAX_CHARS=2000
MEMORY_FLUSH_ENABLED=false
# MEMORY_FLUSH_SYSTEM_PROMPT=You are a memory assistant. Extract durable facts and preferences. Reply NO_REPLY if nothing to store.
# MEMORY_FLUSH_PROMPT=Write any lasting notes to the daily memory log. Reply NO_REPLY if nothing to store.

# Document RAG + memory tuning
DOC_CONTEXT_MAX_CHARS=2800
DOC_SOURCE_PREVIEW_CHARS=160
DOC_HYBRID_ENABLED=false
DOC_RERANK_ENABLED=false
DOC_GRAPH_ENABLED=false
MEMORY_PARTITION_ENABLED=false

# LM Studio-only mode is enabled via a code constant (backend + UI are locked to LM Studio)
# To unlock provider switching later, set LM_STUDIO_ONLY = False in apps/backend/api/server.py

# LM Studio (GGUF direct, OpenAI-compatible)
LOCAL_MODEL_PROVIDER=lmstudio
LOCAL_MODEL_NAME=qwen/qwen3-coder-30b
LOCAL_MODEL_URL=http://localhost:1234

# Ollama (alternative)
# LOCAL_MODEL_PROVIDER=ollama
# LOCAL_MODEL_NAME=qwen3:8b
# LOCAL_MODEL_URL=http://localhost:11434

# Temperature (creativity)
LOCAL_MODEL_TEMPERATURE=0.7
LOCAL_MODEL_MAX_TOKENS=4096

# Pocket-TTS (backend /tts + web UI playback)
USE_POCKET_TTS=true
POCKET_TTS_DEFAULT_VOICE=alba
# Optional voice cloning prompt (local path, http(s) URL, or hf://...wav)
POCKET_TTS_DEFAULT_VOICE_PROMPT=
POCKET_TTS_VARIANT=b6369a24
POCKET_TTS_TEMP=0.7
POCKET_TTS_LSD_DECODE_STEPS=1
POCKET_TTS_EOS_THRESHOLD=-4.0
POCKET_TTS_MAX_CHARS=8000

# Safety gates (system actions)
ENABLE_SYSTEM_ACTIONS=false
ALLOW_OPEN_CHROME=false
ALLOW_PLAYWRIGHT=false
ALLOW_DESKTOP_AUTOMATION=false
ALLOW_FILE_WRITE=false
ALLOW_TERMINAL_COMMANDS=false
TERMINAL_COMMAND_ALLOWLIST=
TERMINAL_COMMAND_TIMEOUT=20
TERMINAL_MAX_OUTPUT_CHARS=8000
FILE_TOOL_ROOT=.

# Optional: local search via SearxNG
SEARXNG_URL=
SEARXNG_TIMEOUT=10
```

Pocket-TTS endpoint usage (examples):

```bash
curl -X POST http://localhost:8000/tts \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello from Pocket-TTS","voice":"eponine"}' --output tts.wav

curl -X POST http://localhost:8000/tts \
  -H "Content-Type: application/json" \
  -d '{"text":"Cloned voice example","voice_prompt":"C:/path/to/voice.wav"}' --output tts.wav
```

### 4. Start Backend

```powershell
cd apps/backend
python app.py --mode api
```

### 5. Start Web UI

```powershell
cd apps/web
npm install
npm run dev
```

The dev server prints a URL like `http://localhost:5173`.

If you are running a built UI (for example from `apps/web/dist`), you must rebuild it after changing `apps/web/src/index.tsx`:

```powershell
npm run build
```

---

EchoSpeak includes a high-performance terminal UI (Bubble Tea + Lipgloss) under `apps/tui/`.

### Latest Features (v0.2.0)
- **Refined Aesthetics**: Pure black background (`"0"`) and white borders (`"255"`) for a high-contrast, premium feel.
- **Sleek Branding**: Updated logo with a modern ASCII font and reduced visual weight (non-bold).
- **In-Box Info**: Real-time display of active **LLM Provider** and **Model** on the splash screen.
- **Session Commands**: `/session`, `/session new`, `/session use <id>`, and `/sessions` manage thread contexts.

### Start the TUI
```powershell
cd apps/tui
.\echospeak-tui.exe
```

### Required environment variables

- `ECHOSPEAK_API_BASE`
  - Backend base URL for the TUI.
  - Default: `http://127.0.0.1:8000`

### TUI commands

- `/session` (current), `/session new`, `/session use <id>`, `/sessions`
- `/model` (show provider/model)
- `/doctor` or `/status` (diagnostics)
- `/help`, `/commands`, `/exit`

### TTS behavior (summary-only)

The backend stream `final` event includes a `spoken_text` field. The TUI uses `spoken_text` for TTS playback so only the brief spoken summary is read aloud, while the full assistant response remains visible in the chat UI.

---

## Usage

### Web UI Mic Input
1. Click mic button (🎤)
2. Speak your query (browser `SpeechRecognition`)
3. Click mic again to submit the captured transcript
4. Agent responds with voice + text

### Text Mode
1. Type in bottom input field
2. Press Enter or click Send (→)
3. Agent response appears in chat

### Notes
- The web UI uses a stable `thread_id` (generated per page load) for LangGraph thread persistence.
- API clients can also pass `thread_id` in `POST /query` or `POST /query/stream` to keep a consistent multi-turn thread.
- When `MULTI_AGENT_ENABLED=true`, the backend keeps an isolated agent instance per `thread_id` (session/workspace routing).
- When `MEMORY_PARTITION_ENABLED=true`, long-term memory searches are scoped to the active mode/thread_id partition.
- The Go TUI switches thread contexts with `/session` and lists them via `/sessions`.

---

## What should be working now (and how to test it)

Use this as a verification checklist.

### 0) Baseline: backend + web UI are alive

- Start backend:
  - `python app.py --mode api`
- Start web UI:
  - `cd apps/web`
  - `npm run dev`
- Quick checks:
  - `http://127.0.0.1:8000/health`
  - `http://127.0.0.1:8000/provider`

Expected:

- `/health` returns healthy JSON
- `/provider` returns provider info
- `/provider/models` lists available models for Ollama/LM Studio/LocalAI/vLLM
- Web UI shows “API Online”

### 0B) Sessions (thread_id) in the TUI

- `/sessions` to list active session IDs
- `/session new` to create a fresh session
- `/session use <id>` to hop back to an existing session

### 1) Pocket-TTS voice (backend `/tts` + web UI playback)

#### 1A) Backend `/tts` basic (fixed voice)

- In `.env`:
  - `USE_POCKET_TTS=true`
- Test:

```bash
curl -X POST http://127.0.0.1:8000/tts \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello from Pocket TTS","voice":"eponine"}' --output tts.wav
```

Expected:

- HTTP 200
- `tts.wav` is valid speech audio

#### 1B) Voice cloning (prompt wav/url/hf://)

```bash
curl -X POST http://127.0.0.1:8000/tts \
  -H "Content-Type: application/json" \
  -d '{"text":"Cloned voice test","voice_prompt":"C:/path/to/voice.wav"}' --output clone.wav
```

Expected:

- Returns WAV audio
- If you get an error about voice-cloning weights, prefer fixed voices (e.g. `eponine`) or switch to a compatible Pocket-TTS model.

#### 1C) Web UI speaking uses backend audio

- In the web UI, send a chat message and listen.

Expected:

- Ring animates during audio playback
- Backend logs show `POST /tts ... 200`

### 2) Web search (SearxNG optional + DDG fallback)

- Without SearxNG configured, ask:
  - `search best budget microphone 2026`
- With SearxNG:
  - set `SEARXNG_URL=http://localhost:xxxx`
  - ask the same query again

Expected:

- The agent calls `web_search`
- The web UI Research panel shows captured sources (tab: Research) and the activity feed shows the tool call
- With SearxNG configured it should pull from SearxNG; otherwise DDG

Quality improvements in `web_search`:

- Multi-query support: you can pass multiple queries by separating lines or using `OR`.
- Results are aggregated across queries and deduped by URL.
- The tool collects more candidates (up to ~20 unique URLs) and then applies a lightweight keyword-based rerank.
- Optional page fetch/extraction (Scrapling) is still controlled by `WEB_SEARCH_USE_SCRAPLING=true`, but extracts are now compressed down to the most query-relevant sentences.

### 3) YouTube transcript tool

- Ask:
  - `Get transcript for <youtube url> and summarize`

Expected:

- Tool call `youtube_transcript`
- Transcript or summary returned (videos with transcripts disabled should return a clear error)

### 4) Playwright browsing (`browse_task`) + confirmation safety

Prereq:

- `.env`:
  - `ENABLE_SYSTEM_ACTIONS=true`
  - `ALLOW_PLAYWRIGHT=true`
- Install once:
  - `playwright install`

Test:

- Ask:
  - `browse https://example.com and summarize`
- You should receive a preview asking you to `confirm`.

Expected:

- It does not browse until you confirm
- After `confirm`, it returns extracted content + a summary

### 5) Desktop automation + confirmation safety

Prereq:

- `.env`:
  - `ENABLE_SYSTEM_ACTIONS=true`
  - `ALLOW_DESKTOP_AUTOMATION=true`

Read-only tests (no confirm needed):

- `list windows`
- `list windows chrome`
- `find control in window "Notepad" control_type=Edit`

Action tests (require preview + confirm):

- `activate window "Notepad"`
- `type "hello" into window "Notepad"`

Expected:

- You see a Preview message first
- After `confirm`, the action occurs

### 6) Structured tool: `calculate`

- `calculate 19 * (4 + 3)`
- `what is 999/7`

Expected:

- Numeric answer (verifies structured-tool call path)

### 7) Tool-calling reliability mode (Ollama + `tool_calling_llm`) (optional)

- `.env`:
  - `USE_TOOL_CALLING_LLM=true`
- Restart backend

Expected:

- More consistent tool usage for queries like `search ...` or `calculate ...`
- If it can’t load, it should warn and fall back automatically

### 8) Streaming + tool timeline (UI verification)

- Ask something that triggers tools (search/browse/desktop)

Expected:

- You see `tool_start`, `tool_end`, and a final response event in the UI activity feed

### 9) Vision / monitor mode

- In web UI click Monitor
- Ask: `what do you see on my screen?`

Expected:

- It attaches “Live desktop context”
- You get a summary of screen OCR

### 10) Provider model lists (LM Studio / Ollama / LocalAI / vLLM)

- With LM Studio running (local server enabled), call:
  - `http://127.0.0.1:8000/provider/models?provider=lmstudio`

Expected:

- Returns a list of model IDs from the OpenAI-compatible `/v1/models` endpoint

---

## Model Configuration

### Default (recommended): LM Studio GGUF direct

1. Start LM Studio and enable the local server.
2. Use the LM Studio base URL (no `/v1`; the backend adds it when needed):

```
http://localhost:1234
```

3. Set `.env`:

```
LOCAL_MODEL_PROVIDER=lmstudio
LOCAL_MODEL_URL=http://localhost:1234
LOCAL_MODEL_NAME=qwen/qwen3-coder-30b
```

Because provider switching is disabled in LM Studio-only mode, change models by editing
`LOCAL_MODEL_NAME` and restarting the backend.

### Alternative: Ollama + Qwen3:8b

 Ollama must be running:
```powershell
ollama serve
ollama pull qwen3:8b
```

### Other Local Providers

| Provider | Base URL | Notes |
|----------|----------|-------|
| LM Studio (GGUF direct) | http://localhost:1234 | OpenAI-compatible endpoint (model list via `/v1/models`) |
| LocalAI | http://localhost:8080/v1 | OpenAI-compatible |
| llama.cpp | Direct model path | Requires model file (.gguf) |
| vLLM | http://localhost:8000/v1 | High throughput |

---

## File Structure

```
EchoSpeak/
├── apps/web/                    # 🌐 React/Vite web front-end
├── apps/tui/                    # 💻 Go TUI
└── apps/backend/
    ├── app.py                   # 🚀 Main entry point
    ├── config.py                # ⚙️ Configuration
    ├── .env                     # 🔑 API keys & settings
    ├── requirements.txt         # 📦 Dependencies
    ├── api/
    │   └── server.py            # 🌐 FastAPI server
    ├── agent/
    │   ├── core.py              # 🤖 Agent logic
    │   ├── memory.py            # 🧠 Vector memory
    │   └── tools.py             # 🔧 Tools: search, vision, youtube, browser, desktop
    ├── io_module/
    │   ├── voice.py             # 🎤 Voice I/O
    │   └── vision.py            # 👁️ Vision/OCR
    ├── data/memory/             # 💾 Persisted memory
    ├── data/memory_files/       # 📝 Durable memory logs (optional)
    └── logs/                    # 📋 Application logs
```

---

## API Reference

### POST /query

```json
// Request
{
  "message": "What is the weather?",
  "include_memory": true,
  "thread_id": "optional-thread-id"
}

// Response
{
  "response": "I checked and the weather is...",
  "success": true,
  "memory_count": 5,
  "request_id": "optional-request-id",
  "doc_sources": [
    {
      "id": "doc-id",
      "chunk_id": "doc-id:3",
      "chunk": 3,
      "filename": "handbook.pdf",
      "source": "upload",
      "timestamp": "2026-01-27T21:44:00",
      "preview": "Snippet of the cited chunk..."
    }
  ]
}
```

### Response Codes
- `200` - Success
- `400` - Invalid request
- `500` - Server error

### POST /query/stream

The web UI uses this endpoint. It returns a streaming response of newline-delimited JSON events.

Common event types:

- `tool_start`
- `tool_end`
- `tool_error`
- `memory_saved`
- `final`

Notes:

- `final` includes `spoken_text` (used by the TUI for TTS summary-only playback).
- `final` includes `doc_sources` when Document RAG is enabled (chunk-level citations + previews).

### WS /gateway/ws

Gateway WebSocket control plane for multi-client streaming. The server sends a `gateway_ready` message on connect.

Client -> server (query):
```json
{
  "type": "query",
  "message": "Summarize today's agenda",
  "include_memory": true,
  "thread_id": "optional-thread-id",
  "request_id": "optional-client-id"
}
```

Server -> client events (same shape as `/query/stream`):
- `tool_start`
- `tool_end`
- `tool_error`
- `memory_saved`
- `final`

---

## Troubleshooting

### Backend Connection Failed
```powershell
# Ensure API server is running
curl http://localhost:8000/health

# Should return: {"status":"healthy"}
```

### Voice Input Not Working
Web UI mic uses your browser’s `SpeechRecognition`:

- Use Chrome/Edge.
- Ensure mic permission is granted.
- If you see a “network” mic error, your browser can’t reach its speech service.

### TTS Audio Issues
- The web UI speaks using backend `/tts`. If `/tts` fails or Pocket-TTS is disabled, the UI will not have audio.
- Confirm the backend is running and Pocket-TTS is enabled:
  - `USE_POCKET_TTS=true`
  - Try `curl http://localhost:8000/health`
  - Look for `POST /tts ... 200` in backend logs when the assistant speaks
- If the backend never receives `POST /tts`, you are likely running an old/built frontend bundle. Use `npm run dev` or rebuild via `npm run build` and hard refresh.

### Vision/OCR Not Working
```powershell
# Install Tesseract OCR
# Download from: https://github.com/UB-Mannheim/tesseract/wiki
# Add to PATH or set:
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
```

### Playwright Browse Not Working
If `browse_task` says Playwright is unavailable:

1. Ensure dependency installed: `pip install -r requirements.txt`
2. Install browser binaries: `playwright install`

### Desktop Automation Not Working
Desktop automation requires:

- Windows
- `ENABLE_SYSTEM_ACTIONS=true`
- `ALLOW_DESKTOP_AUTOMATION=true`

If actions are refused, check these flags and restart the API.

### Provider model list is empty (LM Studio / LocalAI / vLLM)
- Ensure the local server is running and reachable at `LOCAL_MODEL_URL`.
- Verify the OpenAI-compatible endpoint responds:
  - `http://localhost:1234/v1/models` (LM Studio) (note: `LOCAL_MODEL_URL` should be `http://localhost:1234`)

---

## Development

### Running Tests

Install dev dependencies into the same venv you run the backend with:

```bash
cd apps/backend
python -m pip install -r requirements-dev.txt
python -m pytest
```

### Adding New Tools
1. Add a tool in `apps/backend/agent/tools.py`
2. Register it in `get_available_tools()`
3. If it is a **system action**, also:
   - gate it behind config flags
   - ensure it is treated as an action tool in `apps/backend/agent/core.py`
   - require confirmation (`confirm`/`cancel`)
   - filter it out of `lc_tools` so the tool-calling agent cannot bypass confirmation
4. Restart backend

For a deeper explanation of the integrations and their locations, see:

- `docs/INTEGRATIONS.md`

---

## Credits

Built with:
- **LangChain** - LLM orchestration
- **LangGraph** - Optional agent routing for tool calls
- **FastAPI** - REST API
- **FAISS** - Vector search
- **langchain-huggingface** - Local embeddings fallback

---

## License

MIT License
