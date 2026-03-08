# EchoSpeak Advanced Tooling (All Integrations — v5.4.0)

This document explains the **advanced tooling stack** that was implemented in EchoSpeak, how each integration works, where the code lives, how to enable it, and how it improves the assistant.

---

## Recent Updates
- **Inline code diff (v7.1.0)**: Unified one-pane diff view in the Code panel with green additions, red deletions, and Accept/Decline buttons for pending `file_write` actions. Per-file session model replaces snapshot array.
- **Efficient SEARCH/REPLACE editing (v7.1.0)**: File-edit pipeline uses targeted SEARCH/REPLACE blocks instead of full-file rewrites, saving 80–95% of LLM output tokens. Automatic fallback to full-file if parsing fails.
- **Context Ring (v7.1.0)**: Token-usage gauge in chat input with color-coded thresholds and hover tooltip.
- **Workspace Explorer (v7.1.0)**: Visual file tree of the agent's `FILE_TOOL_ROOT` in the Code panel with "cd" button, permission badges, and `GET/POST /workspace` API endpoints.
- **Reflection loop (v7.0.0)**: `ReflectionEngine` evaluates tool results between multi-task plan steps, retries with adjusted params on failure. Anti-loop guards prevent runaway reflection.
- **Live task checklist (v7.0.0)**: New NDJSON stream events (`task_plan`, `task_step`, `task_reflection`) power a real-time `TaskChecklist` component in the Web UI chat.
- **Result passing (v7.0.0)**: Dependent tasks auto-inject previous task results via `{{prev_result}}` placeholders.
- **Unified update awareness (v6.7.0)**: Shared update-context layer detects update-intent queries and injects deterministic repo-backed context across all sources. New read-only `project_update_context` tool decoupled from self-modification.
- **Twitter/Twitch presence (v6.7.0)**: Twitter mentions and Twitch chat resolve to PUBLIC role. Autonomous tweet generation grounded via shared update-context service.
- **Bot Interactions**: Telegram and Discord bot settings instances now have strictly independent "Auto-Confirm Bot Actions" UI configuration points.
- **Core LLM**: Defaults now to `gemini-3.1-flash-lite-preview` for Google integrations.

---

## TL;DR (What you have now)

EchoSpeak is now a **local-first** assistant with:

- **Tavily-only web search** via `web_search`
- **YouTube transcript extraction + summarization**
- **Browser-native voice** in the Web UI (speech recognition + speech synthesis)
- **Local LLM providers**: Ollama, **LM Studio (GGUF direct)**, LocalAI, llama.cpp, vLLM (provider switcher)
- **Cloud LLM providers**: OpenAI, Google Gemini (recommended for best tool reliability)
- **Model listing** via `GET /provider/models` (Ollama tags + OpenAI-compatible `/v1/models` for LM Studio/LocalAI/vLLM)
- **Local browser automation** via **Playwright** (`browse_task`) with safety gating
- **Windows desktop automation** via **pywinauto** (with **PyAutoGUI fallback**) with safety gating
- **Document RAG** (upload + FAISS index + doc sources)
- **Go TUI** (Bubble Tea + Lipgloss) that consumes the same `/query/stream` events
- **More reliable tool calling for Ollama** via optional `tool_calling_llm` (opt-in; local tool-calling is OFF by default)
- **Safer tools** using **Pydantic args schemas** (started with `calculate`)
- A strict **per-action confirmation** safety flow so the model cannot execute system actions without your approval
 - Multi-step tool plans for multi-part user messages, with streamed tool events in the UI
- A `GET /capabilities` endpoint to inspect tool availability and why a tool is blocked (workspace vs policy)
- **Ops tooling**: doctor diagnostics (`/doctor`) plus cron/webhook triggers (`/trigger/cron`, `/trigger/webhook`)
- **Multi-agent session routing** keyed by `thread_id`
- **Memory v2**: typed durable memories + pinned injection + compaction to merge duplicates

Web UI (current):

- Split layout: **left visualizer** + **right panel**
- Right panel tabs: Chat, Research, Memory, Documents
- Header toggle to hide/show the visualizer (full-width mode)

---

## The safety foundation (most important)

### What it does
EchoSpeak supports **action tools** (browser actions, desktop automation, app launching). These are **never executed immediately**.

Instead, Echo:

1. Proposes the action.
2. Stores it as a persisted **approval record** tied to the current execution and thread.
3. Waits for you to reply **`confirm`** or **`cancel`**.

This also applies during multi-step plans: the agent can run multiple read-only tools in sequence, but it will pause at the first action tool and require confirmation before continuing.

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
  - `_set_pending_action()`
  - `_hydrate_pending_action_from_state()`
  - `_is_action_tool()` → delegates to `ToolRegistry.is_action()` (v5.3.0)
  - `_action_allowed()`
  - `_format_pending_action()`
  - `process_query()` 5-stage pipeline with approval/confirmation flow
  - filtering action tools via `ToolRegistry.get_safe_funcs()` (v5.3.0)
- `apps/backend/agent/state.py`
  - `ApprovalRecord`
  - `ExecutionRecord`
  - `ThreadSessionState`

### Workspaces + skills allowlist semantics

- Workspaces define the tool allowlist ceiling.
- Skills can only further restrict tool access; skills must not expand tool access beyond the workspace.

Web UI mode selector:

- `auto | chat | coding | research`
- The UI sends this as `workspace` in `POST /query/stream`
- The backend applies it per request in `apps/backend/api/server.py`

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
- Discord web automation:
  - `discord_contacts_add` (confirmation-gated; writes to `DISCORD_CONTACTS_PATH`)
  - `discord_web_send` (confirmation-gated; Playwright; uses a persistent profile)
- Discord bot channel posting:
  - `discord_send_channel` (confirmation-gated; posts to a server channel as the bot)
- `artifact_write`
- `open_application` (allowlisted apps)
- `notepad_write`
- Email (v5.4.0):
  - `email_send` (confirmation-gated; sends via SMTP)
  - `email_reply` (confirmation-gated; replies via SMTP)

Read-only tools (search, time, transcript, list windows, find control, file list/read, `email_read_inbox`, `email_search`, `email_get_thread`, etc.) run immediately.

---

## Local LLM providers + model listing

---

## Memory v2 (profile + vector memory)

EchoSpeak uses two complementary long-term memory mechanisms:

- **Profile memory** (`profile.json`): deterministic facts like your name/relations.
- **Vector memory** (FAISS): semantic retrieval of prior conversation chunks and durable memory items.

Memory v2 upgrades:

- **Typed memories**: `preference | profile | project | contacts | credentials_hint | note`.
- **Pinned memories**: always injected into context with a tight budget.
- **Update endpoint**: `POST /memory/update` can edit `text`, `type`, and `pinned`.
- **Compaction endpoint**: `POST /memory/compact` merges near-duplicates.

Thread scoping:

- The UI passes `thread_id` to `/memory` endpoints so you see the memory for the current session.

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

- `LM_STUDIO_ONLY` in `apps/backend/api/server.py` (can also be enabled via the `LM_STUDIO_ONLY` environment variable)

When enabled:

- Forces provider to `lmstudio` regardless of request
- `/provider/switch` returns 403 (disabled)
- `/provider` only advertises LM Studio
- UI provider dropdown shows only LM Studio and disables switching

This is an optional mode and is not always enabled by default.

---
## Go TUI (Bubble Tea) + streaming

### What it does
EchoSpeak includes a Go terminal UI under `apps/tui/` that connects to the backend via:

- `POST /query/stream` for newline-delimited JSON events

### Where it lives
- `apps/tui/main.go`
- Backend stream endpoint: `apps/backend/api/server.py`

### Key stream detail: `spoken_text`
The backend stream `final` event includes:

- `response`: full assistant response text (shown in the UI)
- `spoken_text`: a brief voice-oriented summary field retained for compatible clients

The Web UI handles voice directly in the browser instead of requesting backend TTS/STT services.

---

## 1) Tavily web search

### What it does
The `web_search` tool uses **Tavily** as the only active web-search provider.

### Where it lives
- `apps/backend/agent/tools.py`
  - `web_search(query: str) -> str`
- `apps/backend/config.py`
  - `config.tavily_api_key`
  - `config.tavily_search_depth`
  - `config.tavily_max_results`
- `apps/backend/.env`
  - `TAVILY_API_KEY`
  - `TAVILY_SEARCH_DEPTH`
  - `TAVILY_MAX_RESULTS`

### How it works
- Calls Tavily search with the configured API key.
- Reranks and compresses result content toward query-relevant snippets.
- Returns a normalized formatted result set for the agent research pipeline.

### How it improves EchoSpeak
- One supported provider path with less fallback complexity.
- Better result quality for recent/news-sensitive queries.
- Cleaner frontend and backend search semantics.

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
- `apps/backend/config.py` / `.env`
  - `ALLOW_PLAYWRIGHT`
- `apps/backend/requirements.txt`
  - `playwright`

### Enable it
In `.env`:

- `ENABLE_SYSTEM_ACTIONS=true`
- `ALLOW_PLAYWRIGHT=true`

One-time install (required by Playwright):

- `python -m playwright install chromium`

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
- `apps/backend/config.py` / `.env`
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

### Voice
- Browser voice is handled in the Web UI; no backend TTS/STT env flags are required.

### Search
- `WEB_SEARCH_TIMEOUT`
- `TAVILY_API_KEY`
- `TAVILY_SEARCH_DEPTH`
- `TAVILY_MAX_RESULTS`

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

### Discord (Playwright)

- `DISCORD_PLAYWRIGHT_PROFILE_DIR` (persistent profile; log in once with `headless=false`)
- `DISCORD_CONTACTS_PATH` (contacts JSON file)
- `DISCORD_CONTACTS_JSON` (optional JSON string override)

Notes:

- `discord_web_send` clears the composer draft before typing to avoid sending corrupted messages when Discord drafts are present.

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

### Heartbeat (v5.4.0)
- `HEARTBEAT_ENABLED`
- `HEARTBEAT_INTERVAL`
- `HEARTBEAT_PROMPT`
- `HEARTBEAT_CHANNELS`

### Email (v5.4.0)
- `ALLOW_EMAIL`
- `EMAIL_IMAP_HOST`
- `EMAIL_IMAP_PORT`
- `EMAIL_SMTP_HOST`
- `EMAIL_SMTP_PORT`
- `EMAIL_USERNAME`
- `EMAIL_PASSWORD`
- `EMAIL_USE_TLS`

### Telegram (v5.4.0)
- `ALLOW_TELEGRAM_BOT`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USERS`
- `TELEGRAM_AUTO_CONFIRM`

---

## File map (important files)

- `apps/backend/agent/core.py`
  - 5-stage pipeline + tool-calling agent + safety
  - **Multi-step plans are supported, and action tools are confirmation-gated even during multi-step execution.**
  - Plugin dispatch hooks at each pipeline stage (v5.3.0)
- `apps/backend/agent/tools.py`
  - all tools (search, vision, browser, desktop, email) + `TOOL_METADATA`
- `apps/backend/agent/tool_registry.py`
  - Tool Registry + Plugin Registry (v5.3.0)
- `apps/backend/agent/heartbeat.py`
  - HeartbeatManager — proactive heartbeat scheduler (v5.4.0)
- `apps/backend/agent/skills_registry.py`
  - skill/workspace loading + `load_skill_tools()` + `load_skill_plugin()` (v5.3.0)
- `apps/backend/agent/router.py`
  - Intent router + routing decisions
- `apps/backend/config.py`
  - env config / flags
- `apps/backend/.env`
  - example env file

---

## Discord Bot (server + DM)

EchoSpeak can run as a Discord bot that responds to @mentions in servers and to direct messages.

Shared server channels and Discord DMs now intentionally behave differently:

- server channel mentions = limited smart-assistant mode
- Discord DMs = role-aware mode (owner/trusted/public)

It can also expose Discord server channel access to the Web UI using bot-based tools:

- `discord_read_channel` (read recent messages from a server channel)
- `discord_send_channel` (post an announcement/message to a server channel as the bot)

This is distinct from Discord Web (Playwright) tools, which use your personal Discord web session.

### Intent-based Discord Routing

The agent now applies source-aware Discord routing:

| Intent Pattern | Routed Tool |
|----------------|-------------|
| Shared server mention asking normal questions | No special Discord tool; stays in smart assistant mode |
| Shared server mention asking for live/current info | `web_search` |
| Shared server mention asking time/math | `get_system_time` / `calculate` |
| Owner/trusted/public Discord DM asking for broader actions | Routed through normal role-aware tool gating |
| Web UI asking about Discord server channels | `discord_read_channel` / `discord_send_channel` (bot) |
| DM/personal messaging | `discord_web_send` / `discord_web_read_recent` (Playwright) |

Notes:

- Shared Discord server messages are intentionally restricted to natural chat, web search, time, and calculations only.
- Advanced/admin actions are not available in shared server channels, even for the owner.
- If you want richer Discord control, use the Web UI or a direct message with the bot.
- Ordinary chat/help/memory-save prompts now stay on a no-tool fast path by default unless there is explicit tool intent or a concrete tool match.
- Bot-based channel read/send tools run their async work on the Discord client's running event loop to avoid cross-loop `asyncio/aiohttp` failures.
- Channel recap reads now fail fast with a short timeout when Discord history fetch health is degraded, instead of blocking the full request path for a long time.
- Background Discord delivery for routines / heartbeat / proactive output is opt-in by channel config and now prefers `DISCORD_BOT_OWNER_ID`.

Discord bot context wrapping:

- The Discord bot injects optional context blocks like `Recent conversation context:` and always prefixes the latest message with `User request:`.
- The agent extracts the actual user request before any routing/tool selection to prevent context from triggering false tool calls.

Discord confirmation UX:

- For confirmation-gated actions in Discord DM, the bot returns a minimal `confirm`/`cancel` prompt and does not expose internal action planning text.
- Shared server-channel mode does not auto-confirm actions and does not expose the broader admin-style tool surface.

This routing is implemented in both `_allowed_lc_tool_names` and `_should_use_tool` in `core.py`.

Enable in `apps/backend/.env` (or via Web UI Settings → Automation & Webhooks):

- `ALLOW_DISCORD_BOT=true`
- `DISCORD_BOT_TOKEN=...`
- `DISCORD_BOT_ALLOWED_USERS=...` (optional whitelist)

Discord Developer Portal requirement:

- Enable **Message Content Intent** under Bot → Privileged Gateway Intents.
- `apps/backend/requirements.txt`
  - dependencies

### Discord diagnostics: bot online but silent

If the bot shows as online in Discord but never responds and you see **no `Discord on_message` logs** in the backend:

- **1) Confirm intents in the Discord Developer Portal**
  - Under *Bot → Privileged Gateway Intents*, ensure **Message Content Intent** is enabled.
  - Save changes and restart the backend so the new intents are applied.
- **2) Check startup logs**
  - Look for `Discord bot logged in as ...` and a subsequent `Discord intents: ...` line from `apps/backend/discord_bot.py`.
  - If you only see `Discord bot startup initiated` and nothing else, the token or intents are likely misconfigured.
- **3) Verify message delivery**
  - With the current code, every incoming message that reaches the gateway is logged as `Discord on_message: ...` along with `Discord message flags: ...`.
  - If you see these logs but no reply, the issue is in the **filtering or processing** logic.
- **4) Check the whitelist**
  - If `DISCORD_BOT_ALLOWED_USERS` is set, logs will include `Discord whitelist active: allowed_ids=..., incoming_user_id=...`.
  - Make sure your user ID appears in `allowed_ids` or clear the whitelist to allow all users while testing.
- **5) Isolate connectivity with a simple command**
  - The bot is created with `command_prefix=\"!\"`. Add a simple `!ping` command in `discord_bot.py` if needed and confirm it replies with \"pong\".
  - If `!ping` works but mentions/DMs do not, focus on the `on_message` handler filters.
- **6) When in doubt, increase logging**
  - Temporarily add more `logger.info(...)` lines to `on_message` and restart the backend.
  - Once fixed, revert any extra logging to keep noise low.

---

## Current status
All requested integrations are implemented, including LM Studio (GGUF direct) provider support with model listing, tool-calling defaulted OFF for local providers to avoid JSON tool-call loops, langchain-huggingface embeddings, UI auto-retry for backend connectivity, post-v6.5.1 backend stability hardening for fast chat/help/memory routing, and the v6.6.0 Tavily-only/browser-only cleanup.

v5.3.0 additions: 5-stage query pipeline, Tool Registry, Skill → Tool Bridge, Plugin Pipeline Stages.

v5.4.0 additions: Heartbeat Scheduler (proactive mode), 5 native Email tools (IMAP/SMTP), Telegram Bot integration.

v6.7.0 additions: Shared update-context layer (`UpdateContextService` + `UpdateContextPlugin`), read-only `project_update_context` tool, Twitter/Twitch as PUBLIC sources, grounded autonomous tweet generation via shared update context, source-parity regression tests.

Latest backend follow-up: ordinary chat/help/memory prompts now avoid accidental slow tool-enabled runs, Discord channel recap failures degrade to a short timeout instead of a long apparent hang, active search/voice behavior is limited to Tavily web search plus browser-native Web UI speech, and update-intent queries are now handled deterministically across all sources.
