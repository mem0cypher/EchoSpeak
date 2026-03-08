# Getting Started with EchoSpeak

This guide will get you running in 5 minutes.

---

## Prerequisites

- Python 3.11-3.12
- Node.js 18+ (for web UI)
- Go 1.21+ (optional, for TUI)

---

## Quick Start

### Option A: Onboarding Wizard (Recommended)

The onboarding wizard runs in the terminal (OpenClaw-style) and will open the Web UI at the end.

```bash
# Backend venv (Python 3.11-3.12 required). On Arch/CachyOS,
# using uv is the easiest way to avoid system Python (PEP 668) issues.
cd apps/backend

uv python install 3.12
uv venv --python 3.12 .venv

# Activate (Linux/Mac bash/zsh)
source .venv/bin/activate

# Activate (Linux/Mac fish)
source .venv/bin/activate.fish

# Activate (Windows)
.venv\Scripts\activate

# Install dependencies
uv pip install -r requirements.txt

# Run the TypeScript onboarding wizard
cd ../onboard-tui
npm install
npm run start
```

The wizard will:
1. Let you choose your LLM provider (Gemini, OpenAI, Ollama, LM Studio)
2. Enter your API key (if needed)
3. Select a model
4. Choose a `Safe` or `Advanced` setup profile
5. Save non-secret runtime config to `apps/backend/data/settings.json` and secret-bearing overrides to `apps/backend/data/settings.secrets.json`
6. Start the backend, validate `http://localhost:8000/health`, then open your browser

### Option B: Manual Setup

#### 1. Backend Setup

```bash
cd apps/backend
python -m venv .venv
source .venv/bin/activate  # Linux/Mac bash/zsh
# or: source .venv/bin/activate.fish  # Linux/Mac fish
# or: .venv\Scripts\activate  # Windows

python -m pip install -r requirements.txt
```

If you're on Arch Linux / CachyOS and see `externally-managed-environment`, use the venv's Python explicitly:

```bash
cd apps/backend
./.venv/bin/python -m pip install -r requirements.txt
```

#### 2. Configure Environment

Edit `apps/backend/.env` for static defaults and deploy-time secrets, or use `apps/backend/data/settings.json` for persisted non-secret runtime overrides plus `apps/backend/data/settings.secrets.json` for secret-bearing runtime overrides:

```env
# Cloud provider (default)
# If you want local models instead, set USE_LOCAL_MODELS=true (see below).
USE_LOCAL_MODELS=false

# Required for cloud providers
OPENAI_API_KEY=your-key-here      # if using OpenAI
OPENAI_MODEL=gpt-4o-mini

GEMINI_API_KEY=your-key-here      # if using Gemini
GEMINI_MODEL=gemini-2.5-pro

# Optional: embeddings provider/model
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small

# Local model settings (only used when USE_LOCAL_MODELS=true)
LOCAL_MODEL_PROVIDER=ollama
LOCAL_MODEL_URL=http://localhost:11434
LOCAL_MODEL_NAME=llama3.2

# Optional: Enable features
ENABLE_SYSTEM_ACTIONS=false
ALLOW_FILE_WRITE=false
ALLOW_TERMINAL_COMMANDS=false

# Multi-step planning + web retry (optional)
MULTI_TASK_PLANNER_ENABLED=true
WEB_TASK_REFLECTION_ENABLED=true
WEB_TASK_MAX_RETRIES=2

# Discord bot (optional)
ALLOW_DISCORD_BOT=false
DISCORD_BOT_TOKEN=
DISCORD_BOT_ALLOWED_USERS=
```

### 3. Start the Server

```bash
cd apps/backend
python app.py --mode api
```

Server runs at `http://localhost:8000`

### 4. Start the Web UI

```bash
cd apps/web
npm install
npm run dev
```

Web UI runs at `http://localhost:5173`

### 5. Run quality rails

```bash
cd apps/web
npm run typecheck
npm run test:run
```

---

## What You Can Do

### Chat

Just type messages. EchoSpeak will respond conversationally.

Latest behavior notes:

- Ordinary chat/help prompts now stay on a fast no-tool path by default.
- Explicit `remember ...` prompts now save quickly and deterministic profile/preference questions are answered directly when possible.

### Use Tools

EchoSpeak can:
- **Search the web**: "What's the latest news about AI?"
- **Read files**: "Read the file config.py"
- **Edit files**: "Edit soul.md and make it shorter" — the Code panel shows an inline diff with green additions and red deletions, plus Accept/Decline buttons to approve changes (v7.1.0)
- **Run commands**: "Run git status"
- **Take screenshots**: "Take a screenshot and describe it"
- **Browse websites**: "Go to github.com and find trending repos"

Discord examples:

- **Ask in a shared server**: "@EchoSpeak what's the latest AI news today?"
- **Owner DM with broader access**: "search for the latest NVIDIA news and summarize it"
- **Read a server channel from the Web UI**: "what are people saying in #general on Discord?"
- **Personal Discord (Playwright)**: "send a message to oxi on Discord saying I'm on my way"

**Note**: Shared Discord server messages now stay in a limited smart-assistant mode (chat, web search, time, calculations). Broader Discord bot capabilities are meant for owner DMs or the Web UI. DM/personal messaging routes to Playwright web tools. If Discord history fetch is unhealthy, channel recap reads now fail fast with a short timeout instead of appearing hung for a long time.

The Code panel now includes a permanent **"📂 Files" tab** (v7.1.0) showing the agent's current working directory as a visual file tree. You can see what files the agent has access to, check permission status (WRITE/TERM badges), and use the "cd" button to change the working directory at runtime without restarting the server.

Useful fast-path examples:

- `what can you do right now?`
- `remember that my favorite color is blue`
- `what is my favorite color?`
- `what my name?`
- `what changed recently?` (uses the shared update-context layer to return grounded repo info)
- `what's new with EchoSpeak?` (same — deterministic, not memory-dependent)

EchoSpeak can also handle multi-part requests in a single message by executing a short multi-step plan. You'll see a **live task checklist** in the chat showing each step's progress (○ pending, ● running, ✓ done, ✗ failed). For any tool that causes side effects (file writes, terminal commands, Discord sends, browser/desktop automation), EchoSpeak will pause and ask you to reply `confirm` or `cancel`. The agent also **reflects** on each tool result — if a search comes back empty, it automatically retries with a refined query (up to 2 retries max).

Multi-step examples:
- `search for a cat meme and post it in Discord general`
- `check what time it is in Tokyo and write it to notes.txt`
- `search for Python news and email me a summary`

The Web UI now persists that pause state inside an Approval Center per thread, and recent runs appear in an Executions view with trace IDs and persisted run metadata.

### Manage Memory

- **View memories**: Click the Memory tab
- **Pin important memories**: Click the pin icon
- **Clear memories**: Use the clear button

### Upload Documents

- Click the Documents tab
- Upload PDFs or text files
- Ask questions about them

### Customize the Soul

- Click the Soul tab
- Edit the personality definition
- Save to apply to new conversations

---

## Configuration Options

### Model Providers

| Provider | Setting | Notes |
|----------|---------|-------|
| **Gemini** | `USE_LOCAL_MODELS=false` + `GEMINI_API_KEY` + `GEMINI_MODEL` | Recommended for best tool reliability |
| **OpenAI** | `USE_LOCAL_MODELS=false` + `OPENAI_API_KEY` + `OPENAI_MODEL` | Also excellent |
| **Ollama** | `USE_LOCAL_MODELS=true` + `LOCAL_MODEL_PROVIDER=ollama` | Local, requires Ollama running |
| **LM Studio** | `USE_LOCAL_MODELS=true` + `LOCAL_MODEL_PROVIDER=lmstudio` | Local, requires LM Studio running |

### Safety Flags

| Flag | Default | Purpose |
|------|---------|---------| 
| `ENABLE_SYSTEM_ACTIONS` | `false` | Master switch for action tools |
| `ALLOW_FILE_WRITE` | `false` | Allow file modifications |
| `ALLOW_TERMINAL_COMMANDS` | `false` | Allow command execution |
| `ALLOW_PLAYWRIGHT` | `false` | Allow browser automation |
| `DEFAULT_CLOUD_PROVIDER` | `openai` | Persist the active cloud provider when local models are off |
| `FILE_TOOL_ROOT` | `.` | Restrict file operations to this directory |

### Integration Flags (v5.4.0)

| Flag | Default | Purpose |
|------|---------|---------| 
| `ALLOW_EMAIL` | `false` | Enable email tools (IMAP/SMTP) |
| `EMAIL_USERNAME` / `EMAIL_PASSWORD` | empty | IMAP/SMTP credentials |
| `ALLOW_TELEGRAM_BOT` | `false` | Enable Telegram bot |
| `TELEGRAM_BOT_TOKEN` | empty | BotFather token |
| `HEARTBEAT_ENABLED` | `false` | Enable proactive heartbeat scheduler |
| `HEARTBEAT_INTERVAL` | `30` | Minutes between heartbeat ticks |

### Tool Calling

| Flag | Default | Purpose |
|------|---------|---------|
| `USE_TOOL_CALLING_LLM` | `false` | Optional Ollama wrapper to improve tool-calling reliability |
| `LM_STUDIO_TOOL_CALLING` | `false` | Enable OpenAI-style tool calling for LM Studio (if supported by your setup) |
| `ACTION_PARSER_ENABLED` | `true` | Enable the Action Parser pass (single-action JSON) |

---

## Interfaces

### Web UI (Recommended)

Full-featured interface with:
- Streaming chat
- Activity timeline
- Structured Research tab with explicit evidence cards and recency-aware search results
- Approval Center for pending confirmations and approval history
- Executions / Traces view for persisted run metadata and trace inspection
- Memory management
- Document upload
- Settings editor
- Soul editor

Project activation, pending approvals, execution history, and provider/workspace state now follow the currently selected session instead of one global UI state.

```bash
cd apps/web && npm run dev
```

### Go TUI

Terminal interface with:
- Streaming chat
- Session management
- Provider controls

```bash
cd apps/tui && go run .
```

### Python CLI

Direct agent access:

```bash
cd apps/backend && python app.py --mode text
```

---

## Troubleshooting

### "Tool not allowed"

Check:
1. Workspace `TOOLS.txt` includes the tool
2. `ENABLE_SYSTEM_ACTIONS=true`
3. Specific `ALLOW_*` flag is enabled

### "Actions disabled"

Set `ENABLE_SYSTEM_ACTIONS=true` in `.env`

### "Model not found"

Check:
1. `USE_LOCAL_MODELS` is correct for the provider you're trying to use
2. For cloud: `OPENAI_MODEL`/`GEMINI_MODEL` is valid
3. For local: `LOCAL_MODEL_PROVIDER`, `LOCAL_MODEL_URL`, and `LOCAL_MODEL_NAME` are valid
3. API key is set (for cloud providers)

### Embeddings not working

Check:
1. `EMBEDDING_PROVIDER` is set correctly
2. If using `EMBEDDING_PROVIDER=openai`, ensure `OPENAI_API_KEY` is set

### Virtual environment broken

If you see errors like `The file specified the interpreter '...', which is not an executable command`:

```bash
# Delete and recreate the venv
cd apps/backend
rm -rf .venv
python -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

### Python version too new

If you see errors mentioning Pydantic/LangChain compatibility (often on Python 3.13+), use Python 3.11 or 3.12 for the backend.

---

## Next Steps

- Read [ARCHITECTURE.md](../ARCHITECTURE.md) to understand how it works
- Read [AUDIT.md](../AUDIT.md) for full system details
- Read [docs/AGENT.md](AGENT.md) for developer guide
- Read [docs/INTEGRATIONS.md](INTEGRATIONS.md) for tool details
- Read [CHANGES.md](../CHANGES.md) for the full version history
