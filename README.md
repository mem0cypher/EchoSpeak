# EchoSpeak

EchoSpeak is a local-first, low-latency voice and chat assistant designed for privacy and speed. It integrates advanced LLM orchestration with a high-performance streaming audio pipeline.

## 🚀 Key Features

- **Interfaces**:
  - **Modern Web UI**: React/Vite with streaming chat, research panel, OCR monitor, doc sources, memory/docs management, and provider controls.
  - **Go TUI (v0.2.0)**: Bubble Tea client with `/session` management, `/doctor`, `/model`, and streaming UI.
  - **Python CLI**: optional console text mode via `app.py`.
- **Retrieval + Memory**:
  - **Document RAG**: PDF/text upload, chunk-level citations, hybrid retrieval (BM25 + FAISS), optional reranking, GraphRAG-lite expansion.
  - **Long-term Memory**: FAISS vector store with optional file-backed logs, auto-summaries, optional memory-flush notes, and partitioning by mode/thread_id.
- **Tools + Automation**:
  - Web search (SearxNG or DDG, optional Scrapling extraction), live_web_search/browse_task (Playwright).
  - YouTube transcript ingestion, OCR + vision Q&A (Ollama-only), screenshots.
  - Desktop automation (pywinauto + pyautogui fallback), file tools gated by `FILE_TOOL_ROOT` (including safe mutations), `terminal_run` (PowerShell; allowlisted), and `open_chrome`.
- **Speech + Audio**:
  - Pocket-TTS with voice prompts (`hf://` supported), optional PersonaPlex low-latency streaming.
  - Optional local STT (faster-whisper) for offline transcription.
- **Safety + Control**:
  - Confirmation-gated system actions with optional action-plan previews.
  - Workspace/skill tool allowlists and slash-command allowlisting (`/skills`, `/workspaces`, `/workspace`).

## 🤖 Agent Routing + Self-Check

- **Action Parser pass (LLM-driven)**: Echo first interprets the user’s request into a single structured action (or “none”), then applies safety/policy checks and (when needed) asks for `confirm` before executing.
- **Query heuristics / tool pre-filtering (fallback)**: Echo narrows available tools per query (math → `calculate`, YouTube URL → `youtube_transcript`, live/current questions → `live_web_search`).
- **Live web search for “current” queries**: When Playwright is enabled, Echo prefers `live_web_search` and falls back to `web_search` if needed.
- **Reflection step after web tool calls**: After `web_search` / `live_web_search`, Echo checks whether the result answered the question and retries or asks a clarifying question when needed.

## Ops + Sessions

- **Multi-agent session routing**: isolated agent state per `thread_id` (session/workspace).
- **Memory partitioning**: optional per-session memory stores keyed by mode/thread_id.
- **TUI session commands**: `/session`, `/session new`, `/session use <id>`, `/sessions`.
- **Provider controls**: `/provider`, `/provider/models`, `/provider/switch` (switching disabled when LM Studio only).
- **Diagnostics + metrics**: `GET /doctor`, `GET /metrics`, `GET /health`.
- **Streaming**: `/query/stream` NDJSON plus `/gateway/ws` WebSocket gateway.
- **Triggers**: cron + webhook triggers (`/trigger/cron`, `/trigger/webhook`).
- **History + memory**: `/history`, `/history/clear`, `/memory`, `/memory/clear`.

## 🧱 Stack + Data

- **Backend**: FastAPI + LangChain/LangGraph, FAISS, Pocket-TTS, faster-whisper, PersonaPlex (optional).
- **Frontend**: React/Vite streaming UI with OCR monitor + doc/memory panels.
- **TUI**: Go (Bubble Tea + Lipgloss) streaming client.
- **Data**: `apps/backend/data/` stores document index + metadata, memory indexes, and optional memory logs.

## Repository Structure

- `apps/backend/` - Core Python backend (FastAPI + LangChain/LangGraph)
- `apps/tui/` - High-performance Go-based Terminal User Interface
- `apps/web/` - Modern React/Vite Web application
- `docs/` - Comprehensive architecture and developer documentation

## 📖 Essential Documentation

- **[Installation & Setup](docs/OVERVIEW.md)**: Full guide to get everything running.
- **[Integration Details](docs/INTEGRATIONS.md)**: Deep dive into the tool stack and external services.
- **[Agent Developer Guide](docs/AGENT.md)**: Logic behind the `EchoSpeakAgent`, safety models, and tool design.

## ⚡ Quick Start

### 1. Install Backend
```bash
cd apps/backend
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Fish shell:

```fish
cd apps/backend
python -m venv .venv
source .venv/bin/activate.fish
python -m pip install -r requirements.txt
```

### 2. Configure Environment
Edit `apps/backend/.env` to set your desired model provider and enable optional features like PersonaPlex.

### 3. Launch
- **Backend** (run from `apps/backend/`): `python app.py --mode api`
- **Go TUI** (run from `apps/tui/`): `go run .`
- **Web UI** (run from `apps/web/`): `npm run dev`

### Safety + policy notes

- System actions are confirmation-gated (reply `confirm` / `cancel`).
- Workspaces define the tool allowlist ceiling; skills can only further restrict tool access.
- `ACTION_PARSER_ENABLED=true` enables the LLM-driven action parser pass (on by default).

---
MIT License
