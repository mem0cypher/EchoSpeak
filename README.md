# EchoSpeak

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Go 1.21+](https://img.shields.io/badge/go-1.21+-00ADD8.svg)](https://go.dev/dl/)
[![Node 18+](https://img.shields.io/badge/node-18+-339933.svg)](https://nodejs.org/)

**A local-first agent OS for research, automation, and governed execution.** (v7.1.0)

EchoSpeak is a privacy-focused local-first agent platform that runs on your machine. It combines LLM orchestration, persistent memory, governed tool use, and multi-surface delivery so you can research, automate, and manage information without handing your operating context to a hosted assistant.

## Latest Update

- **Inline code diff** — file edits are now shown in a single-file unified diff view with green-highlighted additions and red-highlighted deletions, replacing the old two-tab snapshot approach. Full file content is always visible in the Code panel.
- **Accept / Decline buttons** — when a `file_write` is pending confirmation, Accept and Decline buttons appear directly in the diff view header, wired to the existing approval flow.
- **Efficient SEARCH/REPLACE editing** — the file-edit pipeline now asks the LLM for targeted SEARCH/REPLACE blocks instead of full-file rewrites, saving 80–95% of output tokens on typical edits. Automatic fallback to full-file if parsing fails.
- **Context Ring** — circular SVG gauge in the chat input bar showing estimated token usage vs. context window, with color-coded thresholds and hover tooltip.
- **Per-file session model** — code visualizer tracks original/current content per file for proper diffing, with status pills (Read, Draft, Awaiting save, Saved, Output).
- **Workspace explorer** — the Code panel now has a permanent "📂 Files" tab showing the agent's current working directory as a visual file tree with folder expansion, file icons, permission badges (WRITE/TERM), and a "cd" button to change the working directory at runtime.

## Why EchoSpeak?

- **Privacy First** — Your conversations and data stay on your machine
- **Flexible Deployment** — Run with cloud models (Gemini, OpenAI) or local models (Ollama, LM Studio)
- **Extensible** — Add new skills and tools to customize behavior
- **Safe Automation** — All actions require explicit confirmation
- **Persistent Memory** — Remembers facts across sessions
- **Customizable Personality** — Define how the agent communicates via `SOUL.md`
- **Governed Defaults** — Onboarding now writes backend runtime settings with safe action defaults instead of enabling dangerous capabilities automatically

## Features

### Interfaces
| Interface | Description |
|-----------|-------------|
| **Web UI** | React/Vite with streaming chat, memory management, document RAG, Soul editor, and the **interactive Meet Echo avatar** |
| **Discord Bot** | Server channels + DMs via bot account |
| **Telegram Bot** | Native bot with full agent pipeline (v5.4.0) |
| **Go TUI** | Terminal client with session management and streaming responses |
| **Python CLI** | Direct agent access for scripting and development |

### LLM Providers
| Cloud (Recommended for Tool Reliability) | Local (Privacy) |
|------------------------------------------|-----------------|
| Google Gemini | Ollama |
| OpenAI | LM Studio |
| | LocalAI, vLLM, llama.cpp |

### Capabilities
- **Pipeline Architecture** — 5-stage modular query pipeline for testability and extensibility
- **Skill → Tool Bridge** — skills bundle custom tools via `tools.py`, auto-registered at load
- **Plugin Pipeline** — skills intercept pipeline stages via `plugin.py` (instant responses, context injection)
- **Projects** — activate a project to inject domain context into every AI response
- **Routines** — scheduled (cron), webhook-triggered, or manual agent actions via the pipeline
- **Tools** — Web search, file operations, terminal commands, browser automation, desktop automation, email
- **Integrations** — Discord, Telegram, Email, Slack
- **Heartbeat Scheduler** — Proactive mode: agent wakes every N minutes and reports to configured channels
- **Memory** — Deterministic profile facts + curated durable memories + FAISS vector retrieval + document RAG (v6.0.1)
- **Soul** — Customize agent personality via `SOUL.md`
- **Safety** — Confirmation-gated actions with workspace allowlists
- **Voice Integration** — Browser-native speech recognition and browser speech synthesis in the Web UI
- **Multi-Agent Orchestrator** — Decomposes complex queries into parallel sub-tasks with dependency ordering (v6.0.1)
- **A2A Protocol** — Google Agent-to-Agent protocol for inter-agent communication (v6.0.1)
- **Observability Dashboard** — Real-time tool metrics, latency tracking, error aggregation (v6.0.1)
- **Streaming Events** — NDJSON event streaming for real-time tool execution visibility (v6.0.1)
- **Platform Integrity (v6.3.0)** — shared Web UI modules, duplicate API route cleanup, persisted cloud-provider selection, onboarding health checks, and first-pass regression rails
- **Research Evidence Model (v6.4.0)** — first-class structured research runs, explicit evidence objects, and recency-aware search rendering across backend and web
- **Phase 3 Control Plane (v6.5.0)** — explicit approval records, execution objects, persisted traces, thread-scoped session state, and dedicated Approval/Execution views in the Web UI
- **Routing Hardening (v6.5.1)** — fast no-tool chat/help/memory paths, deterministic preference recall, request-level concurrency protection, and shorter Discord recap timeouts
- **Tavily/Browser Cleanup (v6.6.0)** — Tavily-only search, browser-only voice, stale settings removal, and doc/test cleanup
- **Unified Update Awareness (v6.7.0)** — shared update-context layer across all sources, read-only update introspection tool, Twitter/Twitch as PUBLIC sources, grounded autonomous tweets

Twitter/Twitch highlights:

- **Twitter/X Bot** — autonomous tweet generation grounded by real git commits and code diffs via `UpdateContextService`; changelog tweets auto-generated on new commits; mention replies routed through `process_query(source="twitter")`
- **Twitch Bot** — Twitch chat messages routed through `process_query(source="twitch")` with PUBLIC role restrictions
- **Git Changelog Watcher** — `agent/git_changelog.py` detects new commits, builds update tweet prompts, and persists a watermark to avoid duplicate announcements

Discord highlights:

- **Discord Bot (server channels)** — Read recent channel messages and post announcements *as the bot account* from the Web UI using `discord_read_channel` / `discord_send_channel` (requires `ALLOW_DISCORD_BOT=true` and a valid bot token).
- **Discord Web (Playwright)** — Read/send messages via your *personal Discord web session* using `discord_web_read_recent` / `discord_web_send` (requires `ENABLE_SYSTEM_ACTIONS=true` + `ALLOW_PLAYWRIGHT=true`).

**Intent-based routing**: EchoSpeak automatically detects server channel intent (e.g., `#general`, `#updates`, or channel names like `general`/`updates`) and routes to bot tools.

- Recap/read intents include natural phrasing like `read general chat`, `check updates`, or `search general chat chase is there`.
- Post intents include patterns like `send a message in updates saying that we are live`.
- If Discord channel history is unhealthy or the bot loop is stalled, EchoSpeak now returns a short timeout response quickly instead of hanging the full request path.

DM/personal messaging queries route to Playwright web tools. If a DM recipient matches a saved Discord contact key, DM routing can work even if the user doesn’t explicitly type the word “discord”. This routing happens in both `_allowed_lc_tool_names` and `_should_use_tool`.

**Context extraction (v5.2.0)**: The Discord bot injects conversation context into queries. The agent uses `_extract_user_request_text()` to parse out the actual user request, preventing false tool routing when context mentions Discord but the user's message is conversational.

### Multi-step task execution

EchoSpeak can handle multi-part requests in a single message by creating a small task plan and executing tools step-by-step. The Web UI and TUI stream tool events so you can see each tool being used.

For any tool that causes side effects (Discord send, file writes, terminal commands, browser/desktop automation), EchoSpeak will pause and ask you to reply `confirm` or `cancel` before continuing the rest of the plan.

Phase 3 now persists that pause state as a first-class approval record instead of leaving it trapped inside a live agent instance. The same thread also carries explicit execution IDs, trace IDs, workspace state, project activation, and provider state across `/query`, `/query/stream`, the Web UI, and messaging surfaces.

## Documentation

| Document | Description |
|----------|-------------|
| [Getting Started](docs/GETTING_STARTED.md) | 5-minute setup guide |
| [Architecture](ARCHITECTURE.md) | How EchoSpeak works internally |
| [Audit](AUDIT.md) | Full system architecture reference |
| [Roadmap](ROADMAP.md) | Development plans |
| [Agent Guide](docs/AGENT.md) | Extending the agent |
| [Integrations](docs/INTEGRATIONS.md) | Tool and service details |

## Prerequisites

- **Python** 3.11-3.12
- **Node.js** 18 or higher (for Web UI)
- **Go** 1.21 or higher (optional, for TUI)
- **API Key** for Gemini or OpenAI (if using cloud providers)

## Installation

### Quick Start with Wizard (Recommended)

```bash
# 1) Backend venv (Python 3.11-3.12 required)
cd apps/backend

# Create virtual environment
python -m venv .venv

# Activate (Linux/Mac bash/zsh)
source .venv/bin/activate

# Activate (Linux/Mac fish)
source .venv/bin/activate.fish

# Activate (Windows)
.venv\Scripts\activate

# Install dependencies
python -m pip install -r requirements.txt

  # 2) Run the TypeScript terminal onboarding (OpenClaw-style)
  cd ../onboard-tui
  npm install
  npm run start
```

The wizard now writes non-secret runtime configuration to `apps/backend/data/settings.json`, stores secret-bearing overrides in `apps/backend/data/settings.secrets.json`, keeps all action permissions disabled by default, validates backend health, and then opens the Web UI.

### Manual Setup

#### Backend

```bash
cd apps/backend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS bash/zsh
# or: source .venv/bin/activate.fish  # Linux/macOS fish
# or: .venv\Scripts\activate  # Windows

# Install dependencies
python -m pip install -r requirements.txt

# Configure environment
# Edit apps/backend/.env and add your API keys / settings

# Run server
python app.py --mode api
```

#### Arch Linux / CachyOS note (PEP 668)

On Arch-based distros, `pip` may be blocked when it points to the system Python (`externally-managed-environment`).

If you see that error, use the venv's Python explicitly:

```bash
cd apps/backend
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python app.py --mode api
```

### Web UI

```bash
cd apps/web
npm install
npm run dev
```

The Web UI will be available at `http://localhost:5173` (or the next available port, e.g. `5174`).

The Web UI now includes:

- **Approval Center** — inspect pending approvals, policy flags, and confirmation requirements per thread
- **Executions / Traces** — inspect recent runs, persisted execution metadata, and trace payloads
- **Thread-scoped project state** — project activation/deactivation follows the currently selected session instead of mutating one global agent state

Quality rails:

```bash
cd apps/web
npm run typecheck
npm run test:run
```

### Go TUI (Optional)

```bash
cd apps/tui
go run .
```

## Usage Examples

| Task | Command |
|------|---------|
| Chat | "Explain quantum computing in simple terms" |
| Web Search | "What's the latest news about AI?" |
| File Operations | "Read config.py and explain what it does" |
| Terminal | "Run git status and summarize the changes" |
| Discord | "Send 'Meeting in 5 minutes' to oxi on Discord" |
| Documents | Upload a PDF and ask questions about its contents |
| Memory | "Remember that I prefer dark mode in all applications" |
| Soul | Edit the Soul tab to change how the agent communicates |

Most runtime settings (providers, planning toggles, web reflection retries, Discord bot settings) can be changed from the Web UI Settings tab and saved without editing `.env`.

EchoSpeak also dynamically injects its current tool and skill inventory into the system prompt, so when you add/update skills/tools, the agent is less likely to “forget” what it can do.

## Architecture

```
┌─────────────────┐
│  User Interfaces │  Web UI │ Discord │ Telegram │ Go TUI │ A2A API
└────────┬────────┘
         ▼
┌─────────────────┐     ┌─────────────────┐
│ process_query() │────▶│  LLM Provider   │
│ 5-Stage Pipeline│     │  (Gemini/etc)   │
│                 │     └─────────────────┘
│ S1: Parse/Preempt      ┌───────────────┐
│ S2: Build Context ────▶│ Memory Store  │
│ S3: Shortcuts          │ FAISS+Profile │
│ S4: LLM Agents         │ +Curated+RAG  │
│ S5: Finalize           └───────────────┘
└────────┬────────┘
         ▼
┌─────────────────┐     ┌───────────────┐
│ Phase 3 State   │────▶│ Observability │
│ Store + Control │     │ Dashboard     │
│ Plane           │     └───────────────┘
└────────┬────────┘
         ▼
┌─────────────────┐
│ Tool Registry + │
│ Plugin Pipeline │
└────────┬────────┘     └───────────────┘
         ▼
┌─────────────────┐     ┌───────────────┐
│  Intent Router  │     │ Orchestrator  │
│ + Policy Layer  │     │ (Multi-Agent) │
│ Soul/Skills/Env │     └───────────────┘
└─────────────────┘
```

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | FastAPI, LangChain, LangGraph, FAISS |
| Frontend | React, Vite, Framer Motion |
| TUI | Go, Bubble Tea, Lipgloss |
| Voice | Browser SpeechRecognition, browser speech synthesis |
| LLM | OpenAI, Gemini, Ollama, LM Studio |

## Project Structure

```
EchoSpeak/
├── apps/
│   ├── backend/
│   │   ├── agent/                # 16 modules — core pipeline
│   │   │   ├── core.py           # 5-stage query pipeline + EchoSpeakAgent
│   │   │   ├── memory.py         # FAISS vector store + profile facts
│   │   │   ├── router.py         # Intent classification + routing
│   │   │   ├── tools.py          # 30+ tool implementations
│   │   │   ├── tool_registry.py  # Tool + plugin registry
│   │   │   ├── skills_registry.py # Skill/workspace loader
│   │   │   ├── document_store.py # RAG pipeline (FAISS+BM25+reranking)
│   │   │   ├── heartbeat.py      # Proactive heartbeat scheduler
│   │   │   ├── routines.py       # Cron/webhook routines
│   │   │   ├── projects.py       # Project-scoped memory
│   │   │   ├── observability.py  # Metrics + latency tracking
│   │   │   ├── stream_events.py  # NDJSON event streaming
│   │   │   ├── orchestrator.py   # Multi-agent task decomposition + execution tracking
│   │   │   ├── state.py          # Phase 3 approvals, executions, traces, thread session state
│   │   │   ├── a2a.py            # Google A2A protocol
│   │   │   └── threads.py        # Thread persistence manager
│   │   ├── api/              # FastAPI server
│   │   ├── skills/           # Behavior guidance modules
│   │   ├── workspaces/       # Tool allowlists
│   │   ├── discord_bot.py    # Discord bot integration
│   │   ├── telegram_bot.py   # Telegram bot integration
│   │   ├── twitter_bot.py    # Twitter/X bot (autonomous + mentions)
│   │   ├── twitch_bot.py     # Twitch chat bot
│   │   └── SOUL.md           # Agent personality config
│   ├── web/                  # React frontend + Meet Echo avatar
│   └── tui/                  # Go terminal UI
├── docs/                     # Documentation
├── ARCHITECTURE.md           # System architecture
├── AUDIT.md                  # Full system reference
└── ROADMAP.md                # Development roadmap
```

## Safety Model

EchoSpeak uses a layered safety model:

1. **Environment Flags** — Master switches (`ENABLE_SYSTEM_ACTIONS`, `ALLOW_FILE_WRITE`, etc.)
2. **Workspace Allowlists** — Define which tools are available per context
3. **Skill Restrictions** — Skills can only narrow tool access
4. **Approval Records** — Side effects pause behind persisted approval objects tied to a thread and execution
5. **User Confirmation** — All action tools still require explicit `confirm`/`cancel`

### File Safety

```
FILE_TOOL_ROOT=/path/to/allowed/directory
```

File operations are restricted to this directory.

### Terminal Safety

```
TERMINAL_COMMAND_ALLOWLIST=git,ls,cat,python,pytest
```

Only allowlisted commands can be executed.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

See [Agent Guide](docs/AGENT.md) for details on extending the agent.

## Changelog

### v6.7.0 (2026-03-07)

**Unified Update Awareness + Twitter/Twitch Presence:**
- Shared `UpdateContextService` + `UpdateContextPlugin` detect update-intent queries and inject repo-backed context across all sources
- New read-only `project_update_context` tool decoupled from self-modification permissions
- Twitter/Twitch sources resolve to PUBLIC role; autonomous tweets grounded via shared update context
- Source-parity regression tests for Web UI, Discord, Twitter, and autonomous Twitter

### v6.6.0 (2026-03-06)

**Tavily-Only Search + Browser-Only Voice:**
- Removed stale non-Tavily search surfaces from active runtime, UI settings, and documentation
- Removed backend Pocket-TTS and local STT runtime paths and replaced them with explicit failure stubs
- Simplified Web UI voice behavior to browser-native speech only
- Added cleanup-focused verification guidance and a lightweight regression script

### v6.5.0 (2026-03-06)

**Phase 3 Control Plane:**
- Added persistent approval, execution, and thread-session state records in `apps/backend/agent/state.py`
- Made `/query`, `/query/stream`, `/history`, and project activation thread-aware and execution-aware
- Added backend APIs for `/approvals`, `/executions`, `/threads/{thread_id}/state`, and `/traces/{trace_id}`
- Added Approval and Executions tabs to the Web UI and surfaced persisted trace metadata in stream final events

### v6.0.1 (2026-03-05)

**Memory & Profile Fixes:**
- Deterministic profile fact retrieval via `answer_profile_question()` — no more "I don't know your name" on successive queries
- `update_profile_from_text()` now runs every turn to capture facts like "my sister Emily" via regex
- `curated_lines_from_text()` connected to pipeline — "remember my birthday" now saves as searchable FAISS memory
- Fixed noise-word bug where generic regex overwrote specific profile values

**Discord Integration Fixes:**
- Removed all 4 hardcoded channel name lists — now uses dynamic context-phrase regex
- `_parse_discord_send_intent` supports arbitrary channel names for sending
- `router.py` and `core.py` Discord detection now consistent with dynamic fallback

**Pipeline Integration:**
- Profile context injected into every LLM call via `_build_profile_context()`
- All 12 `memory.py` public methods verified connected to pipeline
- PluginRegistry properly dispatched at all 5 pipeline stages

### v5.4.0
- Telegram bot integration
- Email tooling
- Heartbeat scheduler with multi-channel routing

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [LangChain](https://github.com/langchain-ai/langchain) — LLM orchestration
- [FastAPI](https://fastapi.tiangolo.com/) — Backend framework
- [FAISS](https://github.com/facebookresearch/faiss) — Vector similarity search
- [Bubble Tea](https://github.com/charmbracelet/bubbletea) — Go TUI framework
