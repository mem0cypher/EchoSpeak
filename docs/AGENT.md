# AGENT.md (Developer Guide)

This file is for developers extending EchoSpeak’s agent.

---

## Recent Updates
- **Unified coordination (north-star)**: One request lifecycle, one owner per concept, capability inventory, no prose-as-truth — **`docs/UNIFIED_COORDINATION.md`**. Prefer foundation cleanup over demo-only features.
- **Runtime contracts / lifecycle (v7.6.10)**: **Implemented (partial); pending live validation.**  
  - Full wave index: **`docs/RUNTIME_CONTRACTS.md`** (equal models, Project lifecycle, hydration, search/utility, coding targets, streaming, approval scope, Known limitations).  
  - Truthfulness contracts: **`docs/LIFECYCLE_TRUTHFULNESS.md`** (recovery I.1, confirm I.2, evidence I.3, ToolRuns, projection, corruption).  
  - Do not restate full rules here. Live gates: Lifecycle §11 + Runtime §K.
- **Deterministic mode system**: `mode_controller.py` classifies each turn once (Chat / Task Research / Coding + `intent_relation`); execution gated by path roots, permissions, and approvals. See `docs/MODE_SYSTEM.md`.
- **Continuity + verifier-first reflection (v7.3.0)**: `ContextBundle` now carries explicit current-subject state for referential follow-ups, execution metadata records the Stage 4 tool-calling branch, and `ReflectionEngine` uses deterministic checks for terminal/file/JSON results before asking the LLM to self-grade.
- **Coding loop + trust surfaces (v7.3.0)**: Added `GET /coding/readiness`, surfaced Memory Doctor in the Web UI, and extended the Tools panel with coding readiness plus tool-origin/trust metadata. The coding workspace prompt now guides Echo through inspect -> plan -> implement -> verify -> summarize.
- **Provider readiness + memory doctor (v7.2.0)**: `/query` and `/query/stream` now preflight the configured model provider before starting the agent loop, returning a clear `provider_unavailable` response when LM Studio/Ollama/LocalAI/vLLM/API keys are not ready. `GET /provider` includes readiness metadata, and `GET /memory/doctor` reports duplicate memory groups, type coverage, pinned/profile counts, and raw conversation auto-store status.
- **Agentic baseline (v7.1.3 docs)**: Added `docs/AGENTIC_BASELINE_2026.md`, comparing EchoSpeak against current Claude Code, Letta, LangGraph, OpenAI Agents SDK, OpenHands, CrewAI, and MCP patterns. The next roadmap target is reliability: provider readiness, coding lifecycle, memory doctor, MCP trust center, and evaluation scenarios.
- **Agentic tool loop hardening (v7.1.2)**: Terminal execution now uses a denylist, coding/project prompts auto-promote into the coding workspace, `Desktop/...` resolves through configured extra file roots, and operational lessons can be injected into the system prompt without saving every conversation as memory.
- **Transparent reasoning trace (v7.1.2)**: Thinking/reasoning and task-plan UI render as transparent chat timeline activity with clearer live tool text, while internal five-stage pipeline logging stays out of the user-facing reasoning stream.
- **Inline code diff (v7.1.0)**: New `InlineCodeDiff.tsx` component renders a unified one-pane diff in the Code panel with green additions and red deletions. Per-file session model (`codeSessions`) replaces the old `codeBlocks` snapshot array. Accept/Decline buttons appear in the diff header when a `file_write` is pending confirmation.
- **Efficient SEARCH/REPLACE editing (v7.1.0)**: Targeted SEARCH/REPLACE blocks vs full-file rewrites. Corruption / marker rules: **`docs/LIFECYCLE_TRUTHFULNESS.md` §7**.
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
  - binds deterministic turn mode and enforces mode-scoped tool access
- `apps/backend/agent/mode_controller.py`
  - deterministic Chat / Task Research / Coding classifier and tool mask source of truth
- `apps/backend/agent/mode_executor.py`
  - executor profiles for allowed behavior, failure handling, and logging scope
  - `EchoSpeakAgent`: routing + tool usage + memory + safety gating
  - `ContextBundle`: explicit turn context including `current_subject` and resolved follow-up input for chat continuity
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
- `apps/backend/agent/reflection.py`
  - Verifier-first step reflection for concrete tool outcomes, with LLM reflection reserved for ambiguous results
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

Action tools are never free-form “yes = write.” Separate intents (prepare offer,
exact mutation approval, continue unfinished work, retry) are defined only in:

→ **`docs/LIFECYCLE_TRUTHFULNESS.md` §4**

Minimum implementation map:

- `core.py`: `_set_pending_action`, `_hydrate_pending_action_from_state`,
  offered-action extract/resolve, honesty gates, `_finalize_execution_record`,
  Stage 1 confirm/cancel
- `mode_controller.py`: `_intent_relation`
- `state.py`: `ApprovalRecord`, `ExecutionRecord`, `ThreadSessionState`

### Recovery, ToolRuns, completion, corruption, equal access, Project scope

- Truthfulness: **`docs/LIFECYCLE_TRUTHFULNESS.md`**  
- Equal models, Project/Code lifecycle, hydration, explicit-file targets, approval identity, Known limitations: **`docs/RUNTIME_CONTRACTS.md`**

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

## Memory v3 (authoritative records + retrieval index)

EchoSpeak memory has one durable owner and derived layers:

- **Authoritative records**: `records.json` owns stable memory IDs, owner,
  scope, provenance, active/deleted state, supersession, and index state.
- **Profile projection**: `profile.json` is a backward-compatible legacy mirror;
  deterministic recall resolves active authoritative records.
- **Retrieval index**: FAISS is rebuildable and never proves persistence.
- **Session memory**: per-Session summary/context cache; never account memory.
- **Studio**: reads authoritative records through `GET /memory`.

### Typed memories

Durable memory records use `memory_type` plus explicit account/session/project scope.

### Pinned memories

If a memory has `metadata.pinned=true`, it is always injected into the agent context with a tight budget. This avoids relying on semantic retrieval to remember critical facts.

### Memory write policy (LLM-driven)

After each turn, the agent may run a memory curator pass which extracts 0-2 durable items as strict JSON and saves them via `AgentMemory.add_memory_item(...)`.

Exception: explicit `remember ...` requests synchronously write an authoritative
record and acknowledge success only after a durable ID exists. Index failure is
reported separately and does not hide the source record.

Hard rules:

- Save durable facts only.
- Never store secrets (API keys, passwords, tokens).
- Dedupe near-identical items.
- Corrections supersede prior semantic preferences without erasing provenance.
- Forget/delete tombstones the record and removes or invalidates derived retrieval/profile/Session projections.

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

- `TERMINAL_COMMAND_DENYLIST` (comma-separated denylist of blocked command first-tokens)
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

### Workspaces + skills + Project scope (v7.6.10)

- Skill workspaces (`chat` / `coding` / `research`) provide prompts and soft `TOOLS.txt` preference lists — **not** a hard tool ceiling.
- Execution gates: registration + Project path scope + env `ALLOW_*` / role policy + confirmations.
- Attached Projects stay available even in chat interaction mode; `/capabilities?thread_id=` must bind that Session.
- Full rules: **`docs/RUNTIME_CONTRACTS.md` §B**. Historical notes that said “workspace is the ceiling” are superseded by this contract.

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

The `web_search` tool routes through `agent/web_search_providers.py`.

Behavior:

- Provider cascade:
  - `WEB_SEARCH_PROVIDER=searxng` uses the configured self-hosted SearXNG instance first, then DuckDuckGo fallback.
  - `WEB_SEARCH_PROVIDER=auto` prefers SearXNG only when `SEARXNG_BASE_URL` is explicitly set, then Brave when configured, then DuckDuckGo.
  - `WEB_SEARCH_PROVIDER=duckduckgo` forces the free DuckDuckGo path.
- Query safety:
  - Queries are normalized with the same typo/framing cleanup used by research planning.
  - Vague searches with no concrete subject are rejected before providers run.
- Multi-query support:
  - Separate queries with newlines (recommended) OR use `OR`.
- Aggregation + dedupe:
  - Results are merged across queries and duplicate URLs are removed.
- Free-path enrichment:
  - DuckDuckGo uses text/news variants, simplified retry, authority `site:` variants, and thin-snippet extraction.
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

The backend exposes a capabilities endpoint that reports tool availability and why a tool might be blocked (policy, Project scope, flags — not a hard `TOOLS.txt` ceiling). Always pass the Session id so Project scope is bound:

- `GET /capabilities?thread_id=...`

Location:

- `apps/backend/api/server.py` (`_apply_thread_scope` before report)

See **`docs/RUNTIME_CONTRACTS.md` §B**.

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
