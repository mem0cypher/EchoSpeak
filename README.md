<p align="center">
  <img src="assets/echospeak-real-logo.png" alt="EchoSpeak" width="120" />
</p>

<h1 align="center">EchoSpeak</h1>

<p align="center">
  <strong>Your own local-first AI agent. Any model. Any channel. Your data stays yours.</strong>
</p>

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT License" /></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+" /></a>
  <a href="https://nodejs.org/"><img src="https://img.shields.io/badge/node-18+-339933.svg" alt="Node 18+" /></a>
  <a href="https://go.dev/dl/"><img src="https://img.shields.io/badge/go-1.21+-00ADD8.svg" alt="Go 1.21+" /></a>
</p>

<p align="center">
  <a href="docs/GETTING_STARTED.md">Getting Started</a> ·
  <a href="ARCHITECTURE.md">Architecture</a> ·
  <a href="CHANGES.md">Changelog</a> ·
  <a href="ROADMAP.md">Roadmap</a> ·
  <a href="docs/AGENT.md">Agent Guide</a> ·
  <a href="docs/INTEGRATIONS.md">Integrations</a>
</p>

---

EchoSpeak is a privacy-focused agent platform that runs on your machine. It combines LLM orchestration, persistent memory, governed tool use, and multi-channel delivery so you can research, automate, and manage information without handing your data to a hosted assistant.

If you want a personal, single-user AI assistant that feels local, fast, and always-on — this is it.

## Install (recommended)

```bash
# 1. Backend (Python 3.11–3.12)
cd apps/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Onboarding wizard
cd ../onboard-tui
npm install && npm run start
```

The wizard writes runtime config, validates backend health, and opens the Web UI. All action permissions are disabled by default.

<details>
<summary><strong>Manual setup / Arch Linux note</strong></summary>

```bash
# Backend
cd apps/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py --mode api

# Web UI
cd apps/web
npm install && npm run dev
# → http://localhost:5173

# Go TUI (optional)
cd apps/tui
go run .
```

On Arch/CachyOS with PEP 668, use `./.venv/bin/python -m pip install -r requirements.txt`.

</details>

## Highlights

- **Local-first** — conversations, memory, and data stay on your machine. No telemetry, no cloud dependency.
- **Any model** — Google Gemini, OpenAI, Ollama, LM Studio, LocalAI, vLLM, llama.cpp.
- **Multi-channel** — Web UI, Discord, Telegram, Twitter/X, Twitch, Go TUI, Python CLI, A2A protocol.
- **Persistent memory** — deterministic profile facts, curated durable memories, FAISS vector search, document RAG.
- **Governed tools** — 30+ tools with confirmation gates, workspace allowlists, and layered safety.
- **Skills + plugins** — drop-in skill bundles with custom tools and pipeline hooks.
- **Customizable soul** — define personality, voice, and boundaries via `SOUL.md`.
- **Proactive agent** — heartbeat system pulse, scheduled routines, autonomous Twitter presence.

## Everything we built

### Core platform

- **5-stage query pipeline** — parse → context → shortcuts → LLM agents → finalize.
- **Reflection engine** — per-step evaluation with retry and post-plan reflection.
- **Multi-step task planner** — decomposes complex queries into tool chains with dependency ordering.
- **Multi-agent orchestrator** — parallel sub-task execution with result passing.
- **Intent router** — deterministic fast paths for chat, help, memory, and tool routing.
- **Streaming events** — NDJSON event stream with live task checklist in the Web UI.

### Memory + knowledge

- **Profile facts** — deterministic recall for name, relations, preferences.
- **Curated memories** — long-term facts stored via "remember …" and auto-extraction.
- **FAISS vector store** — semantic similarity search over conversation history.
- **Document RAG** — upload PDFs/docs, chunked and indexed for Q&A.
- **Projects** — activate a project to inject domain context into every response.

### Channels

| Channel | How it connects |
|---------|----------------|
| **Web UI** | React/Vite — streaming chat, inline code diffs, research panel, todos, avatar editor, workspace explorer |
| **Discord** | Bot account for server channels + DMs; Playwright bridge for personal sessions |
| **Telegram** | Native bot via grammY-style integration |
| **Twitter/X** | Autonomous tweets (grounded by git diffs), changelog tweets, mention replies |
| **Twitch** | Chat messages routed through the agent pipeline |
| **Go TUI** | Terminal client with session management and streaming |
| **A2A** | Google Agent-to-Agent protocol for inter-agent communication |

### Tools + automation

- **Web search** — Tavily-powered with reflection and recency filtering.
- **File operations** — read, write (SEARCH/REPLACE blocks), grep, workspace browsing.
- **Terminal** — allowlisted shell commands with confirmation.
- **Browser** — Playwright-driven page control, screenshots, form filling.
- **Email** — send and compose via SMTP integration.
- **Routines** — cron-scheduled, webhook-triggered, or manual agent actions.
- **Heartbeat** — system pulse: gathers todos, git activity, twitter state, then decides if anything is worth reporting.
- **Git changelog** — detects new commits and auto-announces updates.

### Runtime + safety

- **Layered config** — `.env` (static) → `settings.json` (runtime) → `settings.secrets.json` (credentials).
- **Confirmation gates** — all side-effect tools pause for `confirm`/`cancel`.
- **Workspace allowlists** — restrict which tools are available per context.
- **Approval records** — persisted approval state tied to threads and executions.
- **Role-based access** — Discord users resolve to OWNER / TRUSTED / PUBLIC with scoped permissions.
- **Observability** — real-time tool metrics, latency tracking, error aggregation.

## How it works

```
Web UI / Discord / Telegram / Twitter / Twitch / TUI / A2A
                        │
                        ▼
              ┌──────────────────┐
              │  process_query() │
              │  5-stage pipeline │
              └────────┬─────────┘
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
   ┌──────────┐ ┌────────────┐ ┌──────────┐
   │  Memory  │ │    LLM     │ │  Tools   │
   │  FAISS + │ │ Gemini /   │ │ 30+ with │
   │  Profile │ │ OpenAI /   │ │ confirm  │
   │  + RAG   │ │ Ollama     │ │ gates    │
   └──────────┘ └────────────┘ └──────────┘
```

## Tech stack

| Layer | Technology |
|-------|------------|
| Backend | Python · FastAPI · LangChain · LangGraph · FAISS |
| Frontend | React · Vite · Framer Motion · TailwindCSS |
| TUI | Go · Bubble Tea · Lipgloss |
| Voice | Browser SpeechRecognition + speech synthesis |
| LLM | Gemini · OpenAI · Ollama · LM Studio |

## Project structure

```
EchoSpeak/
├── apps/
│   ├── backend/
│   │   ├── agent/           # Core pipeline (16 modules)
│   │   ├── api/             # FastAPI server
│   │   ├── skills/          # Drop-in skill bundles
│   │   ├── workspaces/      # Tool allowlists
│   │   ├── discord_bot.py   # Discord integration
│   │   ├── telegram_bot.py  # Telegram integration
│   │   ├── twitter_bot.py   # Twitter/X (autonomous + mentions)
│   │   ├── twitch_bot.py    # Twitch chat bot
│   │   └── SOUL.md          # Agent personality
│   ├── web/                 # React frontend
│   ├── tui/                 # Go terminal UI
│   └── onboard-tui/         # Setup wizard
├── docs/                    # Documentation
├── ARCHITECTURE.md
├── CHANGES.md               # Full changelog
├── ROADMAP.md
└── AUDIT.md                 # System reference
```

## Configuration

Most settings can be changed from the **Web UI Settings tab** without editing files.

| Layer | File | Purpose |
|-------|------|---------|
| Static | `apps/backend/.env` | API keys, master switches |
| Runtime | `data/settings.json` | Persisted overrides (provider, toggles) |
| Secrets | `data/settings.secrets.json` | Credentials written by onboarding |
| Personality | `apps/backend/SOUL.md` | Agent voice and boundaries |

## Safety model

1. **Environment flags** — master switches (`ENABLE_SYSTEM_ACTIONS`, `ALLOW_FILE_WRITE`, etc.)
2. **Workspace allowlists** — define which tools are available per context
3. **Skill restrictions** — skills can only narrow tool access, never widen
4. **Approval records** — side effects persist as approval objects tied to thread + execution
5. **User confirmation** — all action tools require explicit `confirm` / `cancel`

## Documentation

| Document | Description |
|----------|-------------|
| [Getting Started](docs/GETTING_STARTED.md) | 5-minute setup guide |
| [Architecture](ARCHITECTURE.md) | System internals |
| [Changelog](CHANGES.md) | Full version history |
| [Roadmap](ROADMAP.md) | What's next |
| [Agent Guide](docs/AGENT.md) | Extending the agent |
| [Integrations](docs/INTEGRATIONS.md) | Tool and service details |
| [Audit](AUDIT.md) | Full system reference |

## Contributing

Contributions welcome.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

See [Agent Guide](docs/AGENT.md) for details on extending the agent.

## License

MIT — see [LICENSE](LICENSE) for details.

## Acknowledgments

- [LangChain](https://github.com/langchain-ai/langchain) — LLM orchestration
- [FastAPI](https://fastapi.tiangolo.com/) — Backend framework
- [FAISS](https://github.com/facebookresearch/faiss) — Vector similarity search
- [Bubble Tea](https://github.com/charmbracelet/bubbletea) — Go TUI framework
