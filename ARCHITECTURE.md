# EchoSpeak Architecture

This document explains how EchoSpeak works internally.

---

## The Three Layers

EchoSpeak has three conceptual layers that work together:

```
┌─────────────────────────────────────────────────────────────┐
│                     6. USER MESSAGE                         │
│                   "Send a message to oxi on Discord"         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  LAYER 1: AGENT (Router + Safety Coordinator)               │
│  File: apps/backend/agent/core.py                           │
│                                                             │
│  • Reads user message                                       │
│  • Decides: conversational reply OR system action           │
│  • Chooses tool + arguments                                 │
│  • Enforces guardrails (allowlists, permissions, confirm)   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  LAYER 2: TOOLS (Executable Capabilities)                   │
│  File: apps/backend/agent/tools.py                          │
│                                                             │
│  • Real Python functions that do side effects               │
│  • Examples: discord_send_channel, discord_web_send, file_write, terminal_run     │
│  • Gated by permission flags                                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  LAYER 3: POLICY (Skills + Workspaces + Env Flags)          │
│                                                             │
│ 4. SKILLS                                                   │
│   Behavior guidance from active skills                      │
│                                                             │
│ 5. SKILL INVENTORY (dynamic)                                │
│   A short list of loaded skills + their intent/tool focus   │
│   (helps the agent notice new/updated skills)               │
│                                                             │
│ 6. CAPABILITIES (dynamic)                                   │
│   A categorized list of currently available tools +         │
│   descriptions (helps the agent notice new/updated tools)   │
│                                                             │
│  Skills (behavior guidance):                                │
│  • apps/backend/skills/<skill>/SKILL.md                     │
│  • "How to behave" instructions                             │
│  • Teach trigger phrases, tool choice, safety norms         │
│                                                             │
│  Workspaces (hard allowlists):                              │
│  • apps/backend/workspaces/<workspace>/TOOLS.txt             │
│  • Limit which tools are available                          │
│                                                             │
│  Environment Flags (runtime switches):                      │
│  • ENABLE_SYSTEM_ACTIONS, ALLOW_FILE_WRITE, etc.            │
│  • Final "on/off" gates                                     │
└─────────────────────────────────────────────────────────────┘
```

---

## Query Pipeline (v5.4+)

The `process_query()` method is decomposed into a 5-stage pipeline.
Input sources: Web UI, Discord bot, Telegram bot (v5.4.0), Heartbeat scheduler (v5.4.0).

```
User input
  │
  ▼
Stage 1: _pq_parse_and_preempt
  • Setup, multi-task planning, approval hydration, slash commands
  • Discord routing, pre-tool heuristics
  • Can short-circuit (return early)
  │
  ▼
Stage 2: _pq_build_context
  • Memory retrieval, document context, time context
  • Builds ContextBundle dataclass
  │
  ▼
Stage 3: _pq_shortcut_queries
  • Multi-web fan-out, schedule lookups
  • Can short-circuit
  │
  ▼
Stage 4: _pq_invoke_llm_agents
  • LangGraph ReAct → AgentExecutor → Fallback
  │
  ▼
Stage 5: _pq_finalize_response
  • Direct LLM fallback, TTS selection, memory recording
```

Each stage is a separate method, testable in isolation.

### Update Context Layer (v6.7.0)

EchoSpeak now has a shared update-awareness layer that detects update-intent queries ("what changed?", "what's new?", "any updates?") and injects deterministic, repo-backed update context into the pipeline.

```
User asks "what changed?"
  │
  ▼
Stage 2: _pq_build_context
  │
  ├── UpdateContextPlugin.on_context()
  │     ├── Detects update intent via UpdateContextService
  │     ├── Builds context from git log + CHANGES.md + optional diff
  │     └── Injects into ContextBundle.update_context
  │
  ▼
_allowed_lc_tool_names()
  │
  ├── Update intent detected → routes to project_update_context (safe, read-only)
  ├── NOT routed to self_git_status (requires ALLOW_SELF_MODIFICATION)
  │
  ▼
LLM receives grounded update context + safe tool
```

**Key design decisions:**
- Update context is **deterministic** (git/changelog), not solely memory-dependent
- `project_update_context` is **read-only** and has no policy flag requirements
- Public sources (Twitter mentions, Twitch, Discord public) get a high-level safe rendering
- Owner sources (Web UI, twitter_autonomous) get full detail including diffs
- The same service powers autonomous tweet prompt enrichment

**Source → Role mapping (v6.7.0):**

| Source | Resolved Role | Update Context Detail |
|--------|--------------|----------------------|
| `web` | OWNER | Full (commits + diffs + changelog) |
| `discord_bot` (server) | PUBLIC | High-level (commits + changelog) |
| `discord_bot_dm` | Role-based | Depends on user role |
| `twitter` (mentions) | PUBLIC | High-level |
| `twitch` | PUBLIC | High-level |
| `twitter_autonomous` | OWNER | Full (used for tweet grounding) |
| `heartbeat` / `proactive` | OWNER | Full |

### Inline Code Diff & Efficient Editing (v7.1.0)

The Code panel now provides a Cursor/Windsurf-style inline diff experience for file edits.

```
User asks "edit soul.md — make it shorter"
  │
  ▼
File-edit pipeline detects edit intent
  │
  ├── Step 1: file_read → original content
  │     └── Frontend: creates CodeDiffSession (status: "read")
  │
  ├── Step 2: LLM generates SEARCH/REPLACE blocks (not full file)
  │     ├── _parse_search_replace_blocks() extracts edit blocks
  │     ├── _apply_search_replace() patches original content
  │     │     ├── Exact match first
  │     │     └── Fuzzy whitespace-normalized fallback
  │     ├── Fallback: if no blocks or all skip → full-file rewrite prompt
  │     └── Frontend: updates CodeDiffSession (status: "draft", shows diff)
  │
  ├── Step 3: Confirmation gate
  │     ├── Frontend: shows Accept / Decline buttons in diff header
  │     ├── User clicks Accept → sendText("confirm")
  │     └── User clicks Decline → sendText("cancel")
  │
  └── Step 4: file_write executes
        └── Frontend: updates CodeDiffSession (status: "saved")
```

**Key design decisions:**
- Per-file session model (`codeSessions`) tracks `originalContent` + `currentContent` for diffing
- SEARCH/REPLACE blocks save 80–95% of LLM output tokens vs. full-file rewrites
- Full file always visible in diff view — no context collapsing
- Accept/Decline buttons wired directly to the existing approval pipeline

### Workspace Explorer (v7.1.0)

The Code panel now includes a permanent "📂 Files" tab with a visual file tree showing the agent's working directory.

```
Code Panel (effectiveMode === "coding")
  │
  ├── Tab bar
  │     ├── "📂 Files" tab (always present, index -1)
  │     └── Per-file session tabs (index 0, 1, 2...)
  │
  ├── If "📂 Files" selected (or no sessions):
  │     └── WorkspaceExplorer component
  │           ├── Header: display_name, root path, WRITE/TERM badges
  │           ├── "cd" button → input → POST /workspace → updates FILE_TOOL_ROOT
  │           ├── Refresh button → GET /workspace → re-fetches file tree
  │           └── Recursive file tree with folder expansion
  │
  └── If file session tab selected:
        └── InlineCodeDiff component (existing)
```

**API endpoints:**
- `GET /workspace` — returns `root`, `display_name`, `files[]` (recursive tree, max depth 2), `writable`, `terminal`
- `POST /workspace` — changes `FILE_TOOL_ROOT` at runtime; validates path exists + is directory
- `GET /workspace/browse?path=subdir` — browses a subdirectory within the workspace (shallow listing)

**Key design decisions:**
- `_build_file_tree()` limits recursion (max depth 3, max 200 items) to avoid scanning huge directories
- Hidden files (`.` prefix) are excluded except `.env` and `.env.example`
- The "cd" button mutates `config.file_tool_root` at runtime — no server restart needed
- Permission badges surface `ENABLE_SYSTEM_ACTIONS` + `ALLOW_FILE_WRITE` / `ALLOW_TERMINAL_COMMANDS` state

### Reflection Loop & Live Task Checklist (v7.0.0)

EchoSpeak now has a general-purpose reflection engine that evaluates tool results during multi-step task execution and a live task checklist streamed to the frontend.

```
User asks "Find a cat meme and post it in #general"
  │
  ▼
TaskPlanner.needs_planning() → True (multi-step)
  │
  ▼
TaskPlanner.decompose_tasks() → LLM generates plan:
  [0] web_search("cat meme")
  [1] discord_send_channel("general", {{prev_result}}) ← depends_on: 0
  │
  ▼
_emit_task_plan() → NDJSON: { type: "task_plan", tasks: [...] }
  │                  Frontend renders TaskChecklist
  ▼
execute_next_task(task 0):
  │
  ├── _emit_task_step(0, "running")
  ├── tool.invoke(q="cat meme") → result
  ├── WebTaskReflector (legacy fast-path for date/market queries)
  ├── ReflectionEngine.should_reflect() → True if result < 50 chars
  │     │
  │     ├── reflect_on_step() → LLM: "ACCEPT" or "RETRY: refined query"
  │     │     │
  │     │     ├── ACCEPT → continue
  │     │     └── RETRY → get_retry_params() → re-invoke tool
  │     │
  │     └── _emit_task_reflection(0, accepted, reason, cycle)
  │
  ├── _resolve_dependent_params(task 1) → injects task 0 result
  ├── _emit_task_step(0, "done", result_preview)
  │
  ▼
execute_next_task(task 1):
  │
  ├── Action tool → confirmation gate (existing)
  ├── _emit_task_step(1, "awaiting_confirmation")
  │
  ▼
reflect_on_plan() → post-plan: "ACCOMPLISHED" / "PARTIAL" / "FAILED"
```

**Anti-loop guards:**
- Max 2 reflection cycles per step (`MAX_REFLECTION_CYCLES`)
- Skip trivial tools: `get_system_time`, `calculate`, `project_update_context`
- Skip plans with fewer than 2 tasks
- Skip results longer than 200 characters (substantial = probably fine)
- Reflection LLM calls use low temperature (0.1) for deterministic evaluation

**NDJSON task event types:**

| Event | Payload | When |
|-------|---------|------|
| `task_plan` | `{ tasks: [{ index, description, tool, status }] }` | Plan decomposed |
| `task_step` | `{ index, status, description, tool, result_preview, total }` | Step status change |
| `task_reflection` | `{ index, accepted, reason, cycle }` | Reflection evaluation |

**Key files:**

| File | Purpose |
|------|---------|
| `agent/reflection.py` | `ReflectionEngine` class — should_reflect, reflect_on_step, reflect_on_plan, get_retry_params |
| `agent/core.py` | `TaskPlanner` integration — _emit_task_plan/step/reflection, _resolve_dependent_params |
| `agent/stream_events.py` | `StreamBuffer.push_task_plan/step/reflection` |
| `apps/web/src/components/TaskChecklist.tsx` | Frontend checklist component + taskPlanReducer |
| `tests/test_reflection.py` | 25+ tests for reflection engine, task planner, stream events |

### Routing & latency hardening (post-v6.5.0)

Recent backend hardening changed the default behavior for simple prompts:

- Ordinary chat now defaults to **no tools** unless there is explicit tool intent or a concrete tool match.
- Capability/help prompts like `what can you do right now?` short-circuit to a deterministic response instead of drifting into a tool-enabled LangGraph path.
- Explicit `remember ...` prompts short-circuit to a deterministic memory-save path and skip the extra typed-memory extraction LLM pass.
- The agent now serializes `process_query()` so background proactive work cannot mutate shared request state during a live user turn.
- Discord channel recap reads still depend on Discord history fetch health, but the tool now fails fast with a short timeout instead of appearing hung for ~30 seconds.

---

## Tool Calling Modes

EchoSpeak has two modes for mapping natural language to tool invocations.

### Mode 1: Tool Calling ON (Recommended)

When enabled, the LLM directly selects a tool and provides arguments.

```
User: "What's 25 * 47?"
    │
    ▼
LLM: selects tool "calculate" with args {"expression": "25 * 47"}
    │
    ▼
Agent: checks workspace allowlist + permission flags
    │
    ▼
Tool: executes calculate("25 * 47") → "1175"
    │
    ▼
Response: "25 * 47 = 1175"
```

**Benefits:**
- Faster (one LLM call instead of two)
- More reliable with cloud models (Gemini, OpenAI)
- Direct path from intent to action

Note: the tool-calling prompt uses the composed system prompt (base + SOUL + workspace + skills + inventories), so new tools/skills are visible to the model without manual reminders.

### Mode 2: Tool Calling OFF (Action Parser)

When disabled, the LLM outputs a JSON "form" that gets validated.

```
User: "Add oxi to Discord contacts"
    │
    ▼
LLM: returns JSON form
    {
      "action": "discord_contacts_add",
      "confidence": 0.85,
      "key": "oxi",
      "message_link": "https://discord.com/..."
    }
    │
    ▼
Agent: validates form (confidence > 0.35, required fields present)
    │
    ▼
Agent: creates ApprovalRecord + links it to the current ExecutionRecord and ThreadSessionState
    │
    ▼
Agent: "I'm about to [action]. Reply 'confirm' or 'cancel'."
    │
    ▼
User: "confirm" or "cancel"
    │
    ├─ confirm ──▶ Tool executes
    │
    └─ cancel ──▶ Action discarded
```

**Benefits:**
- More control over validation
- Works with models that don't support tool calling
- Extra safety layer

---

## Multi-step task execution

EchoSpeak can handle multi-part requests in a single message by creating a small task plan and executing tools step-by-step (for example: read Discord messages, then do multiple web searches, then send a summary).

Important safety rule: any tool that causes side effects (Discord send, file writes, terminal commands, browser/desktop automation, etc.) must be confirmation-gated. If a multi-step plan reaches an action tool, the agent will pause and ask the user to reply `confirm` or `cancel` before continuing the remaining tasks.

---

## Safety Model

### Confirmation Flow

System actions are **never executed immediately**.

```
Agent proposes action
    │
    ▼
Agent stores ApprovalRecord + links it to the current ExecutionRecord and ThreadSessionState
    │
    ▼
Agent: "I'm about to [action]. Reply 'confirm' or 'cancel'."
    │
    ▼
User: "confirm" or "cancel"
    │
    ├─ confirm ──▶ Tool executes
    │
    └─ cancel ──▶ Action discarded
```

### Permission Hierarchy (v6.2+)

```
Environment Flags (hardest limit)
    │
    ▼
Discord User Role (OWNER / TRUSTED / PUBLIC)   ← v6.2
    │
    ▼
Workspace Allowlist (ceiling)
    │
    ▼
Skill Allowlist (can only restrict further)
    │
    ▼
Prompt Injection Guard (blocks suspicious input) ← v6.2
    │
    ▼
Rate Limiter (per-user by role tier)             ← v6.2
    │
    ▼
User Confirmation (final gate)
```

### Discord User Roles (v6.2+)

| Role | Resolved By | Tool Access | Auto-Confirm | Memory Write |
|------|------------|-------------|--------------|-------------|
| **OWNER** | `DISCORD_BOT_OWNER_ID` match | All tools | safe + moderate | Full |
| **TRUSTED** | `DISCORD_BOT_TRUSTED_USERS` match | Most tools (no terminal, self-edit, desktop, email send) | safe only | Full |
| **PUBLIC** | Everyone else | Minimal (web search, calculate, time, youtube) | Never | Ephemeral only |

Rate limits: OWNER 60/min, TRUSTED 20/min, PUBLIC 5/min.

Non-Discord sources (Web UI, API, routines) always resolve to OWNER.

### Configuration Governance (v6.3+)

EchoSpeak now treats configuration as layered control-plane state instead of a single mutable `.env` file.

```
Layer 1: `.env` / process environment
    • Static defaults and deploy-time secrets
    • Example: API keys, local model defaults, file paths

Layer 2: `data/settings.json`
    • Runtime override patch persisted by the API and onboarding wizard
    • Example: selected provider, selected model, safe permission toggles

Layer 2b: `data/settings.secrets.json`
    • Secret-bearing runtime overrides persisted separately from the public settings patch
    • Example: OpenAI/Gemini/Tavily keys, webhook secrets, bot tokens

Layer 3: in-process provider/session state
    • Active agent cache + runtime provider switch
    • Reset whenever settings/provider changes are committed
```

Phase 1 onboarding now writes non-secret runtime state to `apps/backend/data/settings.json`, stores secret-bearing overrides in `apps/backend/data/settings.secrets.json`, persists `default_cloud_provider`, and keeps all system-action flags off by default.

### Research Evidence Flow (v6.4+)

Phase 2 promotes research from a UI-side parsing trick into a first-class backend/web contract.

```
Research tool execution (`web_search`)
    │
    ▼
`agent/research.py`
    • Parses tool output into structured research runs
    • Produces explicit evidence objects (query, URL, domain, summary/content, recency)
    │
    ▼
`api/server.py`
    • Streams research payloads on `tool_end`
    • Includes full research runs in final query responses
    │
    ▼
`apps/web/src/features/research/`
    • Normalizes backend payloads
    • Stores research runs separately from assistant synthesis
    • Renders the Research tab from evidence records, not raw strings
```

### Phase 3 Control Plane (v6.5+)

Phase 3 moves approval, execution, trace, and thread-session lifecycle out of ad hoc agent fields and into a shared persisted state layer.

```
User query / orchestration run
    │
    ▼
`agent/core.py` / `agent/orchestrator.py`
    • create execution records
    • attach approval IDs and trace IDs
    • sync workspace/project/provider to thread state
    │
    ▼
`agent/state.py`
    • ApprovalRecord
    • ExecutionRecord
    • ThreadSessionState
    • JSON trace persistence under data/phase3/
    │
    ▼
`api/server.py`
    • exposes thread-aware `/query`, `/query/stream`, `/history`
    • exposes `/pending-action`, `/approvals`, `/executions`, `/threads/{thread_id}/state`, `/traces/{trace_id}`
    │
    ▼
Web UI / messaging surfaces
    • Approval Center
    • Executions / Trace viewer
    • confirm/cancel continuity after reload
```

### Action Tools

Tools that perform side effects require confirmation:

| Tool | Permission Required |
|------|---------------------|
| `file_write` | `ALLOW_FILE_WRITE` |
| `terminal_run` | `ALLOW_TERMINAL_COMMANDS` |
| `browse_task` | `ALLOW_PLAYWRIGHT` |
| `desktop_*` | `ALLOW_DESKTOP_AUTOMATION` |
| `discord_web_send` | `ALLOW_PLAYWRIGHT` |
| `discord_send_channel` | `ALLOW_DISCORD_BOT` |
| `open_chrome` | `ALLOW_OPEN_CHROME` |
| `email_send` | `ALLOW_EMAIL` |
| `email_reply` | `ALLOW_EMAIL` |

---

## Memory System

EchoSpeak has two long-term memory layers:

### Profile Memory
- **Storage**: `profile.json`
- **Purpose**: Deterministic facts (user name, relations, common preferences)
- **Retrieval**: Direct key lookup
- **Example**: `What's my name?` or `What is my favorite color?` → direct profile/preference lookup

### Vector Memory
- **Storage**: FAISS index
- **Purpose**: Semantic retrieval of conversations + notes
- **Retrieval**: Similarity search
- **Example**: "What did I say about cats?" → semantic search

### Memory Types

| Type | Purpose |
|------|---------|
| `preference` | User preferences |
| `profile` | Profile facts |
| `project` | Project-related info |
| `contacts` | Contact mappings |
| `credentials_hint` | Credential reminders (not actual credentials) |
| `note` | General notes |

### Pinned Memories

Important memories can be **pinned** to always inject into context:

```
System Prompt
    │
    ▼
Pinned Memories (always included, small budget)
    │
    ▼
Retrieved Memories (semantic search)
    │
    ▼
User Message
```

---

## Soul System (v5.1+)

The **Soul** defines the agent's core identity, values, and communication style.

```
System Prompt Composition:
    │
    ▼
1. BASE PROMPT (hardcoded)
    │
    ▼
2. SOUL.md (personality) ← NEW
    │
    ▼
3. Workspace Context
    │
    ▼
4. Skills
    │
    ▼
5. MEMORY
    │
    ▼
6. User Message
```

**File**: `apps/backend/SOUL.md`

**Configuration**:
- `SOUL_ENABLED=true`
- `SOUL_PATH=./SOUL.md`
- `SOUL_MAX_CHARS=8000`

**GUI**: Edit live via the Soul tab in the web UI.

---

## Adding New Capabilities

### Adding a Tool (v5.3+ — Registry Pattern)

1. **Define**: Add to `apps/backend/agent/tools.py`
   ```python
   @tool(args_schema=MyArgs, description="What it does")
   def my_tool(arg1: str) -> str:
       # implementation
       return "result"
   ```

2. **Register**: Add to `get_available_tools()` and `TOOL_METADATA`.
   The Tool Registry auto-populates from these on agent init.
   - Set `requires_confirmation: True` in TOOL_METADATA for action tools.
   - Set `policy_flags` for env-gated tools.

3. **Allowlist**: Add to workspace `TOOLS.txt`

The Tool Registry (`agent/tool_registry.py`) is the single source of truth for:
- Which tools are action tools (`ToolRegistry.is_action(name)`)
- Which tools are safe for LLM tool-calling (`ToolRegistry.get_safe_funcs()`)
- Permission flags (`ToolRegistry.get_permission_flags(name)`)

### Adding a Skill

1. **Create folder**: `apps/backend/skills/my_skill/`

2. **Create manifest**: `skill.json`
   ```json
   {
     "name": "My Skill",
     "description": "What it handles",
     "prompt_file": "SKILL.md",
     "tools": ["tool1", "tool2"]
   }
   ```

3. **Write instructions**: `SKILL.md`
   - Trigger phrases
   - Preferred workflow
   - Safety rules
   - Examples

4. **Optional**: Add `tools.py` for custom tools, `plugin.py` for pipeline hooks

5. **Allowlist**: Add to workspace `SKILLS.txt`

### Routines (v5.3.0)

Routines fire through `process_query()` on schedule, webhook, or manual trigger:
- `action_type="query"` → message goes through full 5-stage pipeline
- `action_type="tool"` → synthetic NL query (keeps safety gating)
- Scheduler checks every 60s, connected in agent `__init__`

### Projects (v5.3.0 → v6.5.0 thread-scoped)

Activating a project injects its `context_prompt` into the system prompt:
- `POST /projects/{id}/activate?thread_id=...` to activate for a specific thread
- Context appears between workspace and skills in prompt stack
- `POST /projects/deactivate?thread_id=...` to clear for a specific thread

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `agent/core.py` | Main agent logic, 5-stage pipeline, safety |
| `agent/tools.py` | All tool definitions + TOOL_METADATA |
| `agent/tool_registry.py` | Tool Registry + Plugin Registry |
| `agent/heartbeat.py` | Heartbeat scheduler — proactive mode (v5.4.0) |
| `agent/memory.py` | FAISS memory, profile memory |
| `agent/skills_registry.py` | Skill/workspace/tool/plugin loading |
| `agent/router.py` | Intent router + routing decisions |
| `agent/security.py` | Prompt injection guard, rate limiter, audit log, owner notifications (v6.2) |
| `agent/update_context.py` | Shared update-context service + pipeline plugin (v6.7.0) |
| `agent/git_changelog.py` | Git commit watcher, changelog parsing, update tweet prompt builder |
| `twitter_bot.py` | Twitter/X bot: autonomous tweets, changelog tweets, mention replies |
| `twitch_bot.py` | Twitch chat bot integration |
| `agent/routines.py` | RoutineManager — cron/webhook/manual scheduling |
| `agent/projects.py` | ProjectManager — project-scoped context |
| `agent/state.py` | Phase 3 approval, execution, trace, and thread-session persistence |
| `api/server.py` | FastAPI control plane, settings validation, provider/session lifecycle, thread-aware approvals/executions/traces |
| `config.py` | Layered configuration management, runtime overrides, public settings export |
| `discord_bot.py` | Discord bot integration |
| `telegram_bot.py` | Telegram bot integration (v5.4.0) |
| `apps/onboard-tui/src/index.tsx` | Onboarding control plane writer — runtime settings + health-checked startup |
| `apps/web/src/index.tsx` | Main Web UI shell (still monolithic, now consuming shared modules + Phase 3 approval/execution surfaces) |
| `apps/web/src/components/SquareAvatarVisual.tsx` | Shared avatar visual used by dashboard and marketing surfaces |
| `apps/web/src/features/research/types.ts` | Research run and evidence contracts used by the Web UI |
| `apps/backend/agent/research.py` | Research run/evidence normalization and recency parsing |
| `apps/web/src/features/research/store.ts` | Dedicated research state store |
| `apps/web/src/features/research/buildResearchRun.ts` | Backend payload normalization + legacy fallback builder |
| `apps/web/src/features/research/ResearchPanel.tsx` | Research evidence rendering surface |
| `SOUL.md` | Agent personality |
| `.env` | Environment configuration |

---

## Debugging Checklist

| Problem | Check |
|---------|-------|
| Tool not being chosen | Workspace `TOOLS.txt` allowlist |
| Update query returns generic answer | Check `project_update_context` is in workspace allowlist and `_find_tool()` routes update phrases correctly |
| Autonomous tweets hallucinate | Check `UpdateContextService` is providing grounded commit/diff context to the tweet prompt |
| Simple chat/help prompt feels too slow | Confirm `_allowed_lc_tool_names()` is returning no tools for normal chat and that the query did not accidentally trigger a Discord/time/tool heuristic |
| "Actions disabled" | `ENABLE_SYSTEM_ACTIONS` + specific `ALLOW_*` flags |
| Wrong tool proposed | Skill triggers, routing heuristics in `core.py` |
| Tool arguments wrong | Args schema in `tools.py`, skill examples |
| Memory not recalled | Check memory type, try pinning important facts |
| Discord channel recap hangs | Check bot readiness, Discord loop health, and whether `discord_read_channel()` is timing out quickly instead of blocking for a long period |
| Soul not applied | `SOUL_ENABLED=true`, check `SOUL_PATH` |
