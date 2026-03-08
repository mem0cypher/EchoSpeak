# AGENT.md (Developer Guide)

This file is for developers extending EchoSpeak’s agent.

---

## Recent Updates
- **Inline code diff (v7.1.0)**: New `InlineCodeDiff.tsx` component renders a unified one-pane diff in the Code panel with green additions and red deletions. Per-file session model (`codeSessions`) replaces the old `codeBlocks` snapshot array. Accept/Decline buttons appear in the diff header when a `file_write` is pending confirmation.
- **Efficient SEARCH/REPLACE editing (v7.1.0)**: File-edit pipeline now prompts the LLM for targeted `<<<<<<< SEARCH / ======= / >>>>>>> REPLACE` blocks instead of full-file rewrites, saving 80–95% of output tokens. Automatic fallback to full-file if parsing fails. Exact-match + fuzzy whitespace fallback matching.
- **Context Ring (v7.1.0)**: Circular SVG token-usage gauge in the chat input bar with color-coded thresholds (blue/amber/red) and hover tooltip.
- **Workspace Explorer (v7.1.0)**: New `WorkspaceExplorer.tsx` component renders a file tree of `FILE_TOOL_ROOT` in the Code panel. Permanent "📂 Files" tab with folder expansion, file icons, permission badges (WRITE/TERM), "cd" button to change working directory at runtime, and refresh. New `GET/POST /workspace` + `GET /workspace/browse` API endpoints.
- **Reflection loop (v7.0.0)**: General-purpose `ReflectionEngine` (`agent/reflection.py`) evaluates tool results between steps in multi-task plans. Per-step reflection ("Does this result satisfy what we need?") and post-plan reflection ("Did the overall execution match user intent?"). Anti-loop guards: max 2 cycles, trivial-tool skip, substantial-result bypass. Integrated into `TaskPlanner.execute_next_task()` with retry-on-reject via `get_retry_params()`.
- **Live task checklist (v7.0.0)**: Three new NDJSON stream events (`task_plan`, `task_step`, `task_reflection`) emitted by `TaskPlanner` during execution. Frontend `TaskChecklist.tsx` renders inline in chat with animated status icons.
- **Result passing (v7.0.0)**: `_resolve_dependent_params()` enables `{{prev_result}}` placeholders and auto-injection of dependency results into downstream tasks.
- **Unified update awareness (v6.7.0)**: Shared `UpdateContextService` + `UpdateContextPlugin` detect update-intent queries and inject deterministic repo-backed context (git commits, changelog, diffs) into the pipeline. New read-only `project_update_context` tool decoupled from `ALLOW_SELF_MODIFICATION`.
- **Twitter/Twitch presence (v6.7.0)**: Twitter mentions and Twitch chat resolve to PUBLIC role. Autonomous tweet generation uses the shared update-context service and routes through `process_query(source="twitter_autonomous")` with full tool access.
- **Source role hardening (v6.7.0)**: Plugin dispatch passes `source` and `agent` into context plugins for source-aware context injection.
- **Avatar & UI**: Overhauled Settings into bento-styled blocks, added 3-minute idle sleep logic, and transformed the sub-navigation menu into a liquid metal aesthetic with inverted white icons.
- **LLM Defaults**: Setup `gemini-3.1-flash-lite-preview` as the core fallback endpoint API model.
- **Safety / Tool Tracking**: Segregated Auto-Confirm actions for Discord and Telegram bots independently in web UI.
- **Backend stability hardening**: Ordinary chat/help/memory-save prompts now stay on fast deterministic paths instead of defaulting into broad tool-enabled LangGraph runs.
- **Concurrency protection**: `process_query()` now serializes request handling so proactive/background work cannot trample shared live-request state.
- **Discord recap fail-fast**: `discord_read_channel` now returns quickly on bot/loop/history-fetch problems instead of hanging the request path for a long time.

---

## Key design goals

- **Local-first by default** (local models, local search, local automation).
- **Per-action confirmation** for any system action.
- **Action tools must never be executed directly by the tool-calling agent.**

---

## Core architecture

### Main components

- `apps/backend/agent/core.py`
  - `LLMWrapper`: provider abstraction (OpenAI, Google Gemini, Ollama, **LM Studio (GGUF direct)**, LocalAI, llama.cpp, vLLM)
  - `EchoSpeakAgent`: routing + tool usage + memory + safety gating
- `apps/backend/io_module/personaplex_client.py`
  - `PersonaPlexClient`: Async WebSocket client (Opus/sphn)
  - `PersonaPlexOrchestrator`: High-level lifecycle + tool routing (mic pause/resume)
- `apps/backend/agent/document_store.py`
  - Document RAG store (FAISS + metadata)
- `apps/backend/agent/tools.py`
  - All tools (read-only and action tools), including email tools (v5.4.0)
  - `get_available_tools()` defines which tools exist
- `apps/backend/agent/heartbeat.py`
  - HeartbeatManager — proactive mode scheduler (v5.4.0)
- `apps/backend/agent/router.py`
  - Intent router + routing decisions
- `apps/backend/agent/update_context.py`
  - Shared update-context service + pipeline plugin (v6.7.0)
- `apps/backend/agent/git_changelog.py`
  - Git commit watcher, changelog parsing, diff summary, tweet prompts
- `apps/backend/agent/memory.py`
  - FAISS-based memory (local embeddings fallback if OpenAI key absent)
- `apps/backend/twitter_bot.py`
  - Twitter/X bot: autonomous tweets, changelog tweets, mention replies
- `apps/backend/twitch_bot.py`
  - Twitch chat bot integration
- `apps/backend/telegram_bot.py`
  - TelegramBotManager — Telegram bot integration (v5.4.0)
- `apps/backend/config.py`
  - env config / flags
- `apps/backend/.env`
  - example env file

---

## Safety model (mandatory)

### Approval / confirmation flow

Action tools must follow:

1. Agent proposes action
2. Agent persists an approval record linked to the current execution and thread state
3. User must reply `confirm` or `cancel`
4. Only then do we execute

This logic lives in:

- `apps/backend/agent/core.py`
  - `_set_pending_action()`
  - `_hydrate_pending_action_from_state()`
  - `_begin_execution_record()` / `_finalize_execution_record()`
  - `_is_action_tool()`
  - `_action_allowed()`
  - `_format_pending_action()`
  - `process_query()` confirmation handling
- `apps/backend/agent/state.py`
  - `ApprovalRecord`
  - `ExecutionRecord`
  - `ThreadSessionState`

### Multi-step task plans + approvals

EchoSpeak can execute multi-part requests by decomposing a message into a short task plan and running tools sequentially.

If a plan reaches an action tool, it must enter the same approval flow:

1. Execute read-only tasks immediately (search/time/read, etc.)
2. When an action tool is reached, create an approval record and ask for `confirm`/`cancel`
3. On `confirm`, execute the action tool and resume the remaining tasks in the plan

### Action Parser pass (LLM-driven)

EchoSpeak runs an LLM-driven Action Parser pass before heuristic tool routing. The Action Parser interprets the user’s request and returns a single JSON action (or “none”), which is then validated against the current policy and routed into the existing approval confirmation flow.

Location:

- `apps/backend/agent/core.py`
  - `_action_parser_candidate()`
  - `_normalize_candidate_action()`
  - `_candidate_to_pending_action()`

Config:

- `ACTION_PARSER_ENABLED=true` (default)

### Preventing bypass via tool-calling agent

LangChain tool-calling agent (`create_tool_calling_agent`) must not receive action tools.

Implementation:

- In `EchoSpeakAgent.__init__`:
  - `self.lc_tools = [t for t in get_available_tools() if t.name not in {<action tools>}]`

If you add a new action tool, you must:

- Add it to `TOOL_METADATA` in `tools.py` with `requires_confirmation: True` and the appropriate `policy_flags`
- Add permission check to `_action_allowed()` in `core.py`
- The Tool Registry auto-excludes action tools from `lc_tools` based on `TOOL_METADATA`

---

## Conversational fallback (no-tool path)

EchoSpeak always needs a usable response path even when:

- tool-calling is disabled for the current provider
- the Action Parser returns `none`
- no heuristic tool route matches

Implementation:

- `apps/backend/agent/core.py`
  - `EchoSpeakAgent.process_query()` includes a **direct LLM fallback** that generates a normal conversational response when `response_text` is still empty at the end of routing.

This prevents silent “(no response)” failures for simple inputs like greetings.

Latest hardening changed the default tool policy for simple prompts:

- Ordinary chat now defaults to **no tools** unless there is explicit tool intent or a concrete tool match.
- Capability/help prompts like `what can you do right now?` short-circuit to a deterministic response.
- Explicit `remember ...` prompts short-circuit to a deterministic memory-save path.

---

## Memory v2 (typed + pinned)

EchoSpeak memory has two complementary layers:

- **Profile memory**: deterministic facts stored in `profile.json` (user name, relations, common structured preferences). Used to answer simple questions reliably.
- **Vector memory**: FAISS semantic store for conversation chunks and durable memory items.

### Typed memories

Durable memory items use a `metadata.type` field (ex: `preference`, `project`, `contacts`).

### Pinned memories

If a memory has `metadata.pinned=true`, it is always injected into the agent context with a tight budget. This avoids relying on semantic retrieval to remember critical facts.

### Memory write policy (LLM-driven)

After each turn, the agent may run a memory curator pass which extracts 0-2 durable items as strict JSON and saves them via `AgentMemory.add_memory_item(...)`.

Exception: explicit `remember ...` requests now bypass the extra typed-memory extraction pass so they complete quickly and deterministically.

Hard rules:

- Save durable facts only.
- Never store secrets (API keys, passwords, tokens).
- Dedupe near-identical items.

### Memory API endpoints (thread-scoped)

- `GET /memory?thread_id=...`
- `POST /memory/update` (edit text/type/pinned)
- `POST /memory/compact` (merge near-duplicates)

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
- `ALLOW_OPEN_APPLICATION`

Multi-step planning + web reflection:

- `MULTI_TASK_PLANNER_ENABLED` (default: true)
- `WEB_TASK_REFLECTION_ENABLED` (default: true)
- `WEB_TASK_MAX_RETRIES` (default: 2)

Discord bot:

- `ALLOW_DISCORD_BOT` (default: false)
- `DISCORD_BOT_TOKEN`
- `DISCORD_BOT_ALLOWED_USERS` (comma-separated user IDs; empty = everyone)

Discord bot tools:

- `discord_read_channel` (read recent server channel messages via bot)
- `discord_send_channel` (post to a server channel via bot; confirmation-gated)

**Intent-based Discord Routing**: The agent automatically detects server channel intent (e.g., `#general`, `#updates`, "what are people saying in #general") and routes to bot tools. DM/personal messaging queries route to Playwright web tools. `#channel` patterns trigger routing even if the user does not include the word "discord".

Discord read-path note:

- `discord_read_channel` now checks bot/client readiness and fails fast on stalled history fetches.
- This improves perceived latency, but a timeout still means the Discord history read itself is unhealthy and needs separate debugging.

Discord web automation:

- `DISCORD_PLAYWRIGHT_PROFILE_DIR` (persistent Playwright profile used by `discord_web_send`)
- `DISCORD_CONTACTS_PATH` (JSON file mapping recipient keys to Discord DM/channel URLs; used by `discord_web_send` and written by `discord_contacts_add`)
- `DISCORD_CONTACTS_JSON` (optional JSON string override for contacts mapping)

### Dynamic tool/skill awareness

The agent composes its system prompt from:

- base prompt
- `SOUL.md`
- workspace context
- active project `context_prompt` (if a project is activated)
- skill prompts

It also injects:

- a dynamic **Skill inventory** section (loaded skills + descriptions/tool focus)
- a dynamic **Capabilities** section (available tools + descriptions)

Skills/workspaces are fingerprinted and can be reloaded automatically when files change, so updates take effect on the next request without manual reminders.

---

## v5.3.0 Extensibility — Skill→Tool Bridge

Skills can bundle their own **custom tools** by adding a `tools.py` file. Any function decorated with `@ToolRegistry.register` in that file auto-registers when the skill loads.

### How it works

```
skills/my_skill/
  skill.json
  SKILL.md
  tools.py    ← functions decorated @ToolRegistry.register auto-register as tools
```

### Example `tools.py` structure

```python
from agent.tool_registry import ToolRegistry

@ToolRegistry.register(
    name="my_tool",
    description="Does something useful",
    risk_level="safe",
)
def my_tool(query: str) -> str:
    return f"Result for {query}"
```

**Showcase:** `skills/daily_briefing/tools.py` registers `daily_briefing` — can be called manually or run as a cron routine.

---

## v5.3.0 Extensibility — Plugin Pipeline

Skills can intercept pipeline stages by adding a `plugin.py` file. The plugin class registers hooks that run at each stage.

### Pipeline stages you can intercept

| Hook | Stage | Use case |
|------|-------|----------|
| `on_preempt` | Stage 1 — before any LLM | Return instant response, skip LLM entirely |
| `on_context` | Stage 2 — context building | Inject extra context into system prompt |
| `on_response` | Stage 4 — after LLM | Post-process or augment responses |
| `on_finalize` | Stage 5 — finalization | Side effects after response is sent |

### Example `plugin.py` structure

```python
from agent.tool_registry import PipelinePlugin, PluginRegistry

class MyPlugin(PipelinePlugin):
    def on_preempt(self, bundle):
        if "trigger phrase" in bundle.user_input.lower():
            return "Instant response — no LLM needed"
        return None

    def on_context(self, bundle):
        bundle.extra_context += "\nExtra info here."

PluginRegistry.register(MyPlugin())
```

**Showcase:** `skills/system_monitor/plugin.py` handles "system status" / "cpu usage" / "how's the system" instantly at Stage 1 with real CPU/RAM/disk/uptime data (zero LLM calls).

---

## v5.3.0 Projects — Activating Project Context

Projects inject a `context_prompt` into the system prompt when active. This shifts the agent's focus for that domain.

**API:**
- `POST /projects` — create (include `name`, `description`, `context_prompt`)
- `POST /projects/{id}/activate?thread_id=...` — make active for a specific thread
- `POST /projects/deactivate?thread_id=...` — clear the active project for a specific thread

**UI:** Click a project card in the Projects tab → automatically calls the thread-scoped backend API for the selected session.

---

## v5.3.0 Routines — Scheduled Agent Actions

Routines fire through `process_query()` — same tool access, safety gating, and memory recording as regular messages.

**Types:**
- `cron` — schedule with cron expression (e.g. `0 8 * * *` for 8am daily)
- `webhook` — trigger via `POST /routines/{id}/run` from external services
- `manual` — run on demand from UI

**Showcase:** Create a routine with action `Give me a daily briefing` of type `cron`, schedule `0 8 * * *`. The `daily_briefing` tool will fire automatically every morning.

Terminal command safety:

- `TERMINAL_COMMAND_ALLOWLIST` (comma-separated allowlist of command first-tokens)
- `TERMINAL_COMMAND_TIMEOUT` (seconds)
- `TERMINAL_MAX_OUTPUT_CHARS`

File tool root:

- `FILE_TOOL_ROOT` (restricts file tools to a safe base directory)

Optional reliability flag:

- `USE_TOOL_CALLING_LLM` (wraps Ollama model with `tool_calling_llm`)
- `LM_STUDIO_TOOL_CALLING` (enable OpenAI-style tool calling for LM Studio)

### LM Studio-only lock (server-side)

The API server can be hard-locked to LM Studio regardless of `.env` provider selection:

- `apps/backend/api/server.py`
  - `LM_STUDIO_ONLY = True`

When enabled:

- the runtime provider is forced to `lmstudio`
- `POST /provider/switch` returns 403

Local providers default to **non-tool-calling** to avoid JSON tool-call loops; only enable tool-calling when you explicitly need it.

Multi-session + ops:

- `MULTI_AGENT_ENABLED=true` enables an agent pool keyed by `thread_id` (each session/workspace gets isolated state).
- `ALLOWED_COMMANDS` and `COMMAND_PREFIX` control which slash commands are accepted by the agent.
- `CRON_ENABLED` + `CRON_STATE_PATH` enable cron-style trigger handling.
- `WEBHOOK_ENABLED` + `WEBHOOK_SECRET` / `WEBHOOK_SECRET_PATH` enable signed webhook trigger handling.

LangChain compatibility note:

- `tool-calling-llm` expects the LangChain `0.3.x` ecosystem (keep `langchain*` packages pinned to `<0.4` in `apps/backend/requirements.txt`).

Voice (browser-only):

- Browser speech recognition and browser speech synthesis are the supported voice path in the Web UI.
- Backend voice engines were removed; any legacy backend voice imports now fail clearly.

Document RAG + context:

- `DOCUMENT_RAG_ENABLED`
- `DOC_UPLOAD_MAX_MB`
- `SUMMARY_TRIGGER_TURNS`
- `SUMMARY_KEEP_LAST_TURNS`
- `ACTION_PLAN_ENABLED`

Action Parser:

- `ACTION_PARSER_ENABLED`

### Workspaces + skills allowlist semantics

- Workspaces define the tool allowlist ceiling for the session.
- Skills can only further restrict tool access; skills must not expand tools beyond what the workspace allows.

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
- Tavily-only retrieval:
  - `web_search` is the only active web-search tool.
  - Tavily response content is compressed toward the most query-relevant sentences so the agent gets “signal”, not walls of text.

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

## Web UI session behavior (thread_id)

The web UI persists a stable `thread_id` in `localStorage` under `echospeak.thread_id` and sends it with each request. When `MULTI_AGENT_ENABLED=true`, this maps to an isolated agent instance in the backend agent pool.

The web UI also sends an optional workspace/mode override (`auto | chat | coding | research`) which the backend applies per request.

Locations:

- `apps/web/src/index.tsx` (mode selector and request payload)
- `apps/backend/api/server.py` (applies `workspace` in `/query/stream`)

## Diagnostics: capabilities report

The backend exposes a capabilities endpoint that reports tool availability and why a tool might be blocked (workspace allowlist vs system action policy).

- `GET /capabilities?thread_id=...`

Location:

- `apps/backend/api/server.py`

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

## Embeddings + memory (langchain-huggingface)

Embeddings now prefer `langchain-huggingface` to avoid LangChain deprecation warnings, with a fallback to `langchain-community` if needed.

- `apps/backend/agent/memory.py` handles the import fallback.
- `apps/backend/requirements.txt` includes `langchain-huggingface`.

---

## Adding a new tool (checklist)

### 1) Implement tool in `apps/backend/agent/tools.py`

- Prefer `@tool(args_schema=...)` with Pydantic models.
- Keep the tool return value a simple string.

### 2) Register in `get_available_tools()` and `TOOL_METADATA`

- Add the tool function to the `get_available_tools()` list.
- Add an entry to `TOOL_METADATA` with `risk_level`, `requires_confirmation`, and `policy_flags`.
- The Tool Registry auto-populates from these on agent init.

### 3) If action tool:

- Set `requires_confirmation: True` in `TOOL_METADATA`
- Set appropriate `policy_flags` (e.g., `["ENABLE_SYSTEM_ACTIONS", "ALLOW_FILE_WRITE"]`)
- Add permission gating in `_action_allowed()` in `core.py`
- Add dry-run preview if possible

### 4) Update docs

- Update `README.md`
- Update `docs/INTEGRATIONS.md`

---

## Tool Registry (`agent/tool_registry.py`)

The Tool Registry provides a single source of truth for tool metadata.

Key APIs:

- `ToolRegistry.is_action(name)` — replaces the hardcoded `_is_action_tool()` set
- `ToolRegistry.get_safe_funcs()` — returns non-action tools for LLM tool-calling
- `ToolRegistry.get_permission_flags(name)` — returns env flags required
- `ToolRegistry.get_by_category(category)` — filter tools by category
- `ToolRegistry.get_all()` — all registered `ToolEntry` objects

The registry is populated via `register_from_metadata(get_available_tools(), TOOL_METADATA)` during agent init.

---

## Tool-calling reliability mode (Ollama)

EchoSpeak can optionally wrap `ChatOllama` with `tool_calling_llm`:

- `USE_TOOL_CALLING_LLM=true`

Location:

- `apps/backend/agent/core.py` in `LLMWrapper._create_llm()` (Ollama branch)

If the dependency is missing or incompatible, it falls back to normal `ChatOllama`.
