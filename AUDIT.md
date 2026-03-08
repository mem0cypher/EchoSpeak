# EchoSpeak Architecture Audit

**Generated:** 2025-01-20  
**Updated:** 2026-03-07 (v7.1.0 inline code diff, accept/decline flow, efficient editing)  
**Version:** 7.1.0  
**Auditor:** Cascade AI

---

## Executive Summary

EchoSpeak is a local-first agent platform with a strong backend foundation and an increasingly governed control plane. Phase 1 fixed foundational control-plane and onboarding issues, Phase 2 promoted research into a first-class backend/web contract with explicit evidence objects and recency-aware rendering, and Phase 3 matured runtime state into a persisted control plane for approvals, executions, traces, and thread-scoped session state. The latest post-v6.5.0 hardening pass also corrected a major latency/routing regression by pushing ordinary chat/help/memory-save prompts back onto deterministic fast paths instead of defaulting them into broad tool-enabled LangGraph runs. The Web UI shell is still large, but the critical approval and trace lifecycle is no longer trapped inside ad hoc local state.

**Key Features:**
- Multi-provider LLM support (OpenAI, Gemini, Ollama, LM Studio, LocalAI, vLLM)
- **5-stage query pipeline** (v5.3.0) — decomposed `process_query()` into testable stages
- **Tool Registry** (v5.3.0) — centralized tool metadata, replaces hardcoded lists
- **Skill → Tool Bridge** (v5.3.0) — skills can bundle custom tools via `tools.py`
- **Plugin Pipeline** (v5.3.0) — skills can intercept pipeline stages via `plugin.py`
- Tool-based automation with confirmation gating
- Persistent memory (profile + vector storage)
- Skills and workspaces for behavior customization
- Soul system for personality definition
- Document RAG with hybrid retrieval
- Browser-native Web UI voice
- Web UI, Go TUI, and Python CLI interfaces
- **Heartbeat Scheduler** (v5.4.0) — proactive mode, periodic agent queries
- **Native Email Tools** (v5.4.0) — 5 IMAP/SMTP tools (read, search, send, reply)
- **Telegram Bot** (v5.4.0) — native bot with full agent pipeline routing
- **Phase 1 Platform Integrity (v6.3.0)** — duplicate `/query` route removal, persisted cloud-provider governance, safe onboarding defaults, backend route/config regressions, and first-pass Web UI modular extraction
- **Phase 2 Research Evidence Model (v6.4.0)** — structured research runs, explicit evidence payloads, recency metadata, and extracted frontend research modules
- **Phase 3 Control Plane (v6.5.0)** — persisted approval records, execution objects, trace storage, thread-scoped session state, and backend/web approval + execution surfaces
- **Routing Hardening (v6.5.1)** — fast no-tool chat/help/memory paths, deterministic preference recall, request-level concurrency protection, and shorter Discord recap timeouts
- **Tavily/Browser Cleanup (v6.6.0)** — Tavily-only web search, browser-only voice, stale settings removal, and doc/test cleanup
- **Discord Context Hardening (v6.6.1)** — shared server channels now stay in limited public-assistant mode while owner DMs retain broader gated access; background Discord delivery prefers the owner and the Web UI now shows live Discord activity over `/gateway/ws`
- **Unified Update Awareness (v6.7.0)** — shared `UpdateContextService` + `UpdateContextPlugin` detect update-intent queries and inject deterministic repo-backed context (git commits, changelog, diffs) across all sources; new read-only `project_update_context` tool decoupled from self-modification; Twitter/Twitch resolved as PUBLIC; autonomous tweet generation grounded via shared update context
- **Reflection Loop (v7.0.0)** — general-purpose `ReflectionEngine` (`agent/reflection.py`) evaluates tool results between steps in multi-task plans via per-step and post-plan LLM reflection; anti-loop guards (max 2 cycles, trivial-tool skip, substantial-result bypass); integrated into `TaskPlanner.execute_next_task()` with retry-on-reject
- **Live Task Checklist (v7.0.0)** — three new NDJSON stream event types (`task_plan`, `task_step`, `task_reflection`) in `StreamBuffer`; `TaskPlanner` emits plan/step/reflection events during execution; new `TaskChecklist.tsx` React component renders inline checklist with animated status icons in the Web UI chat
- **Result Passing (v7.0.0)** — `_resolve_dependent_params()` enables `{{prev_result}}` placeholders and auto-injection of dependency results into downstream task parameters
- **Reflection Test Suite (v7.0.0)** — 25+ tests in `tests/test_reflection.py` covering reflection heuristics, step/plan evaluation, retry params, stream events, and dependent result passing
- **Inline Code Diff (v7.1.0)** — new `InlineCodeDiff.tsx` component renders a unified one-pane diff in the Code panel with green additions and red deletions (strikethrough); per-file session model (`codeSessions`) replaces the old `codeBlocks` snapshot array; full file always visible; status pills (Read, Draft, Awaiting save, Saved, Output)
- **Accept/Decline Flow (v7.1.0)** — Accept and Decline buttons appear directly in the diff view when a `file_write` is pending confirmation, wired to the existing approval pipeline
- **Efficient SEARCH/REPLACE Editing (v7.1.0)** — file-edit pipeline prompts LLM for targeted SEARCH/REPLACE blocks instead of full-file rewrites; `_parse_search_replace_blocks()` + `_apply_search_replace()` with exact-match and fuzzy fallback; automatic fallback to full-file if parsing fails
- **Context Ring (v7.1.0)** — circular SVG token-usage gauge in the chat input bar with color-coded thresholds and hover tooltip
- **Workspace Explorer (v7.1.0)** — new `WorkspaceExplorer.tsx` component renders a visual file tree of the agent's `FILE_TOOL_ROOT` in the Code panel with recursive folder expansion, file icons, size labels, permission badges (WRITE/TERM), and a "cd" button to change the working directory at runtime; permanent "📂 Files" tab always accessible alongside code sessions; new `GET/POST /workspace` and `GET /workspace/browse` API endpoints

Discord integration highlights:

- Shared Discord server channels are now intentionally limited to a smart public-bot profile: natural chat, web search, time, basic calculations, and light recap of the current channel only.
- Discord DMs keep the role-aware model: owner DM is the broad privileged path, trusted/public DMs remain restricted by tool and action policy.
- Richer Discord server channel read/post actions remain available from the Web UI and can still be used in owner DM contexts when explicitly requested.
- Background Discord notifications are opt-in by delivery channel and now prefer `DISCORD_BOT_OWNER_ID` for delivery.
- The Web UI consumes `/gateway/ws` to show live Discord activity and gateway connection state in the Services tab.

---

## Table of Contents

1. [Core Architecture Overview](#1-core-architecture-overview)
2. [Agent System](#2-agent-system)
3. [Tools & Automation](#3-tools--automation)
4. [Memory System](#4-memory-system)
5. [LLM Provider Integration](#5-llm-provider-integration)
6. [Skills & Workspaces](#6-skills--workspaces)
7. [Soul System (v5.1.0)](#7-soul-system-v510)
8. [API Layer](#8-api-layer)
9. [Frontend Architecture](#9-frontend-architecture)
10. [Projects & Routines](#10-projects--routines)
11. [Configuration & Settings](#11-configuration--settings)
12. [Safety & Security Model](#12-safety--security-model)
13. [Data Flow Diagrams](#13-data-flow-diagrams)

---

## 1. Core Architecture Overview

### 1.1 System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend Layer                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │  Web UI      │  │  Go TUI      │  │  Python CLI         │   │
│  │  (React)     │  │  (Terminal)  │  │  (Direct Agent)      │   │
│  └──────────────┘  └──────────────┘  └──────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         API Layer                               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  FastAPI Server (apps/backend/api/server.py)             │   │
│  │  - REST endpoints (/query, /memory, /documents, etc.)    │   │
│  │  - WebSocket gateway (/gateway/ws)                       │   │
│  │  - Streaming responses (NDJSON)                          │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Agent Layer                             │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  EchoSpeakAgent (apps/backend/agent/core.py)             │   │
│  │  - LLM orchestration (tool calling + action parser)      │   │
│  │  - Memory management (profile + vector)                   │   │
│  │  - Tool routing + safety gating                           │   │
│  │  - Soul loading + system prompt composition               │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Capability Layer                           │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐   │
│  │   Tools    │ │   Skills   │ │ Workspaces │ │   Soul     │   │
│  │ (tools.py) │ │(skills/)   │ │(workspaces)│ │ (SOUL.md)  │   │
│  └────────────┘ └────────────┘ └────────────┘ └────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Directory Structure

```
EchoSpeak/
├── apps/
│   ├── backend/                 # Python backend
│   │   ├── agent/               # Core agent logic
│   │   │   ├── core.py          # EchoSpeakAgent (5-stage pipeline)
│   │   │   ├── tools.py         # Tool definitions + TOOL_METADATA
│   │   │   ├── tool_registry.py # Tool Registry + Plugin Registry
│   │   │   ├── memory.py        # Memory management
│   │   │   ├── document_store.py # Document RAG
│   │   │   ├── skills_registry.py # Skill/workspace/tool/plugin loading
│   │   │   ├── router.py        # Intent router + routing decisions
│   │   │   ├── projects.py      # Project management
│   │   │   └── routines.py      # Scheduled routines
│   │   ├── api/                 # FastAPI server
│   │   │   └── server.py        # REST + WebSocket endpoints
│   │   ├── io_module/           # I/O handlers
│   │   │   ├── personaplex_client.py # Low-latency voice
│   │   │   ├── stt_engine.py    # Removed local STT stub (browser speech only)
│   │   │   ├── pocket_tts_engine.py # Removed Pocket-TTS stub (browser playback only)
│   │   │   └── vision.py        # Screen capture + OCR
│   │   ├── skills/              # Skill definitions
│   │   │   ├── discord/           # Discord bot + DM tools
│   │   │   ├── discord_contacts/  # Contact lookups
│   │   │   ├── email_comms/       # Email integration
│   │   │   ├── slack_comms/       # Slack integration
│   │   │   ├── soul/              # SOUL.md management tools
│   │   │   ├── web_search/        # Tavily-backed web search
│   │   │   ├── self_modify/       # Code self-edit tools
│   │   │   ├── system_monitor/    # ← NEW: Plugin Pipeline showcase
│   │   │   │   ├── skill.json
│   │   │   │   ├── SKILL.md
│   │   │   │   └── plugin.py      # Intercepts "system status" at Stage 1
│   │   │   ├── daily_briefing/    # ← NEW: Skill→Tool Bridge showcase
│   │   │   │   ├── skill.json
│   │   │   │   ├── SKILL.md
│   │   │   │   └── tools.py       # Auto-registers daily_briefing tool
│   │   │   └── restart/
│   │   ├── workspaces/          # Workspace configurations
│   │   ├── projects/            # Project data
│   │   ├── routines/            # Routine definitions
│   │   ├── data/                # Persistent data
│   │   ├── SOUL.md              # Agent personality
│   │   ├── config.py            # Configuration management
│   │   ├── .env                 # Environment variables
│   │   └── app.py               # Entry point
│   ├── web/                     # React/Vite frontend
│   │   └── src/index.tsx        # Main UI component
│   └── tui/                      # Go terminal UI
├── docs/                        # Documentation
│   ├── AGENT.md                 # Developer guide
│   └── INTEGRATIONS.md          # Tool integrations
├── README.md                    # Project overview
├── ARCHITECTURE.md              # How it works
├── AUDIT.md                     # This file
├── ROADMAP.md                   # Future plans
└── .gitignore
```

---

## 2. Agent System

### 2.1 EchoSpeakAgent

**File:** `apps/backend/agent/core.py`

The central orchestrator that handles:
- Query processing via a **5-stage pipeline** (v5.3.0)
- Tool selection and execution via **Tool Registry**
- Memory management
- Safety gating and confirmation flow
- **Plugin dispatch** at each pipeline stage

### 2.2 Query Processing Pipeline (v5.3.0)

```
User Message
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│ Stage 1: _pq_parse_and_preempt                           │
│ • Setup, multi-task planning, approval hydration          │
│ • Slash commands, Discord routing, pre-tool heuristics   │
│ • Can short-circuit (return early)                       │
│ 🔌 Plugin hook: on_preempt                               │
└──────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│ Stage 2: _pq_build_context                               │
│ • Memory retrieval, document context, time context       │
│ • Builds ContextBundle dataclass                         │
│ 🔌 Plugin hook: on_context                               │
└──────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│ Stage 3: _pq_shortcut_queries                            │
│ • Multi-web fan-out, schedule lookups                    │
│ • Can short-circuit                                      │
│ 🔌 Plugin hook: on_shortcut                              │
└──────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│ Stage 4: _pq_invoke_llm_agents                           │
│ • LangGraph ReAct → AgentExecutor → Fallback            │
│ 🔌 Plugin hook: on_response                              │
└──────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│ Stage 5: _pq_finalize_response                           │
│ • Direct LLM fallback, TTS selection, memory recording  │
│ 🔌 Plugin hook: on_finalize                              │
└──────────────────────────────────────────────────────────┘
    │
    ▼
Return response
```

Each stage is a separate method, testable in isolation.

### 2.3 System Prompt Composition

```
1. BASE PROMPT (hardcoded in core.py)
   "You are Echo Speak, a conversational AI companion..."
   
2. SOUL (from SOUL.md) ← v5.1.0
   "Identity: I am EchoSpeak..."
   "Values: Honesty over politeness..."
   "Communication Style: Direct and concise..."
   
3. SOURCE AWARENESS
   Context about where the conversation is happening (web UI, Discord, etc.)
   
4. WORKSPACE CONTEXT
   Current workspace mode and available tools

5. PROJECT CONTEXT ← v5.3.0, thread-scoped in v6.5.0
   Injected when a project is activated via `POST /projects/{id}/activate?thread_id=...`
   Example: "You are helping develop EchoSpeak. Focus on typed Python."
   
6. SKILLS
   Behavior guidance from active skills

7. SKILL INVENTORY
   Auto-generated list of active skills with their tool allowlists
   
8. CAPABILITIES ← v5.3.0 (dynamic self-discovery)
   Auto-generated categorized list of all available tools
   Agent reads its own capabilities before every response
   
9. MEMORY
   - Pinned memories (always included)
   - Retrieved memories (semantic search)
   
10. USER MESSAGE
    The actual query
```

---

## 3. Tools & Automation

### 3.1 Tool Categories

| Category | Tools | Purpose |
|----------|-------|---------|
| **Search** | `web_search` | Web information retrieval |
| **Documents** | `youtube_transcript` | Content extraction |
| **Files** | `file_list`, `file_read`, `file_write` | File system operations |
| **Terminal** | `terminal_run` | Command execution |
| **Browser** | `browse_task`, `open_chrome` | Web automation |
| **Desktop** | `desktop_click`, `desktop_type_text`, etc. | UI automation |
| **Discord (Web)** | `discord_web_send`, `discord_web_read_recent`, `discord_contacts_*` | Discord via Playwright (personal web session; DMs) |
| **Discord (Bot)** | `discord_read_channel`, `discord_send_channel` | Discord server channel read/post via bot account |
| **Communication** | `slack_send_message` | Slack |
| **Vision** | `take_screenshot`, `vision_qa`, `analyze_screen` | Screen analysis |
| **Utility** | `calculate`, `get_system_time` | Helpers |

### 3.2 Action Tools (Require Confirmation)

These tools perform side effects and require user confirmation:

```
file_write, terminal_run, browse_task,
desktop_*, discord_web_send, discord_contacts_*, 
slack_send_message, open_chrome

Additionally:

- `discord_send_channel` (posts to a server channel as the bot account; gated by `ALLOW_DISCORD_BOT`)
```

Note: EchoSpeak supports multi-step plans for multi-part messages. Read-only tools can run immediately, but if a plan reaches an action tool it will pause and require `confirm` before continuing.

### 3.3 Tool Registry (v5.3.0)

**File:** `apps/backend/agent/tool_registry.py`

The Tool Registry is the single source of truth for tool metadata:

| API | Purpose |
|-----|--------|
| `ToolRegistry.is_action(name)` | Check if tool requires confirmation |
| `ToolRegistry.get_safe_funcs()` | Get non-action tools for LLM tool-calling |
| `ToolRegistry.get_permission_flags(name)` | Get env flags required for a tool |
| `ToolRegistry.get_by_category(category)` | Filter tools by category |
| `ToolRegistry.get_all()` | All registered ToolEntry objects |

The registry auto-populates from `TOOL_METADATA` in `tools.py` via `register_from_metadata()` at agent init.

### 3.4 Permission Gates

Each action tool has associated permission flags (stored in `TOOL_METADATA` and queryable via `ToolRegistry.get_permission_flags()`):

```python
# Example from core.py
def _action_allowed(self, tool_name: str) -> bool:
    if tool_name == "file_write":
        return self._allow_file_write
    if tool_name == "terminal_run":
        return self._allow_terminal_commands
    if tool_name == "browse_task":
        return self._allow_playwright
    # ... etc
```

Note: Discord bot tools have their own gate (`ALLOW_DISCORD_BOT`) and are treated as system actions when sending messages.

---

## 4. Memory System

### 4.1 Memory Types

| Type | Storage | Retrieval | Use Case |
|------|---------|-----------|----------|
| **Profile** | `profile.json` | Key lookup | Deterministic facts |
| **Vector** | FAISS index | Semantic search | Conversations, notes |
| **Document** | Document store | RAG retrieval | Uploaded documents |

Recent hardening added deterministic preference capture/recall for explicit facts such as favorite-color style prompts, and explicit `remember ...` requests now avoid the slow extra typed-memory extraction pass.

### 4.2 Memory Item Structure

```python
{
    "id": "uuid",
    "text": "User prefers dark mode",
    "memory_type": "preference",  # preference|profile|project|contacts|credentials_hint|note
    "pinned": false,
    "timestamp": "2026-03-01T12:00:00Z",
    "metadata": {}
}
```

### 4.3 Memory Operations

| Endpoint | Operation |
|----------|-----------|
| `GET /memory` | List memories |
| `POST /memory/clear` | Clear all memories |
| `POST /memory/update` | Update memory item |
| `POST /memory/compact` | Merge near-duplicates |

---

## 5. LLM Provider Integration

### 5.1 Supported Providers

| Provider | Type | Tool Calling | Notes |
|----------|------|--------------|-------|
| **OpenAI** | Cloud | ✅ Full support | Recommended for reliability |
| **Gemini** | Cloud | ✅ Full support | Recommended for reliability |
| **Ollama** | Local | ⚠️ Optional wrapper | Requires `tool_calling_llm` |
| **LM Studio** | Local | ⚠️ Via OpenAI compat | GGUF direct also supported |
| **LocalAI** | Local | ⚠️ Via OpenAI compat | |
| **vLLM** | Local | ⚠️ Via OpenAI compat | |
| **llama.cpp** | Local | ❌ No | Action parser fallback |

### 5.2 Provider Selection

```python
# From config.py
class ModelProvider(str, Enum):
    OPENAI = "openai"
    GEMINI = "gemini"
    OLLAMA = "ollama"
    LM_STUDIO = "lm_studio"
    LOCALAI = "localai"
    VLLM = "vllm"
    LLAMA_CPP = "llama_cpp"
```

### 5.3 Model Selection

- **Cloud**: Configured via `OPENAI_MODEL` / `GEMINI_MODEL` env vars
- **Ollama**: Uses `/api/tags` to list available models
- **LM Studio/LocalAI/vLLM**: Uses OpenAI-compatible `/v1/models`

---

## 6. Skills & Workspaces

### 6.1 Skills

Skills provide behavior guidance and can now bring their own tools and pipeline plugins (v5.3.0).

**Structure:**
```
apps/backend/skills/<skill_name>/
├── SKILL.md      # Behavior instructions
├── skill.json    # Metadata (name, description, tools)
├── TOOLS.txt     # Tool allowlist (optional)
├── tools.py      # Custom tools (optional, v5.3.0)
└── plugin.py     # Pipeline plugin (optional, v5.3.0)
```

**Skill → Tool Bridge (v5.3.0):**

If a skill includes `tools.py`, functions decorated with `@ToolRegistry.register(...)` are auto-loaded when the workspace is configured. New tools appear in the agent's capabilities and allowlists automatically.

**Plugin Pipeline (v5.3.0):**

If a skill includes `plugin.py`, `PipelinePlugin` subclasses registered via `PluginRegistry.register()` can intercept pipeline stages:

| Hook | Stage | Behavior |
|------|-------|----------|
| `on_preempt` | After Stage 1 | Short-circuit with custom response |
| `on_context` | After Stage 2 | Enrich the context bundle |
| `on_shortcut` | Before Stage 3 | Short-circuit before native shortcuts |
| `on_response` | After Stage 4 | Transform the LLM response |
| `on_finalize` | In Stage 5 | Transform the final output |

**Example skill.json:**
```json
{
    "name": "Discord Contacts",
    "description": "Manage Discord recipient mappings",
    "prompt_file": "SKILL.md",
    "tools": ["discord_contacts_add", "discord_contacts_discover"]
}
```

### 6.2 Workspaces

Workspaces define hard limits on available tools and skills.

**Structure:**
```
apps/backend/workspaces/<workspace_name>/
├── TOOLS.txt     # Allowed tools (one per line)
└── SKILLS.txt    # Allowed skills (one per line)
```

### 6.3 Workspace Modes

| Workspace | Purpose | Typical Tools |
|-----------|---------|---------------|
| `auto` | General purpose | All tools |
| `chat` | Conversation focus | Minimal tools |
| `coding` | Development | Terminal, file tools |
| `research` | Information gathering | Search, browse tools |

---

## 7. Soul System (v5.1.0)

### 7.1 Overview

The Soul system defines the agent's core identity, values, communication style, and boundaries via a `SOUL.md` file.

### 7.2 Configuration

```env
SOUL_ENABLED=true
SOUL_PATH=./SOUL.md
SOUL_MAX_CHARS=8000
```

### 7.3 SOUL.md Structure

```markdown
# EchoSpeak Soul

## Identity
I am EchoSpeak, a personal AI assistant.

## Communication Style
- Direct and concise
- No corporate pleasantries
- Technical accuracy over politeness

## Values
- Honesty over politeness
- Getting things done
- User autonomy

## Boundaries
- I won't reveal API keys or secrets
- I won't sugarcoat technical realities
- I won't pretend to have emotions

## Memory Behavior
- Remember user preferences
- Track project context
- Maintain contact mappings

## Tool Usage Philosophy
- Prefer read-only operations first
- Always explain what I'm about to do
- Ask for confirmation on side effects
```

### 7.4 API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/soul` | GET | Get current soul content |
| `/soul` | PUT | Update soul content |

### 7.5 GUI Integration

The Soul tab in the web UI allows live editing of `SOUL.md`. Changes apply to new conversations.

---

## 8. API Layer

### 8.1 FastAPI Server

**File:** `apps/backend/api/server.py`

### 8.2 Key Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/query` | POST | Single query (non-streaming) |
| `/query/stream` | POST | Streaming query (NDJSON) |
| `/pending-action` | GET | Current pending approval for a thread |
| `/approvals` | GET | Approval-center history for a thread |
| `/executions` | GET | Execution ledger for a thread |
| `/threads/{thread_id}/state` | GET | Thread-scoped workspace/project/provider/approval state |
| `/traces/{trace_id}` | GET | Persisted per-run trace payload |
| `/memory` | GET | List memories |
| `/memory/clear` | POST | Clear memories |
| `/documents` | GET/POST/DELETE | Document management |
| `/provider` | GET | Current provider info |
| `/provider/models` | GET | Available models |
| `/settings` | GET/PUT | Runtime settings |
| `/soul` | GET/PUT | Soul management |
| `/capabilities` | GET | Tool availability |
| `/projects` | GET/POST/PUT/DELETE | Project management |
| `/routines` | GET/POST/PUT/DELETE | Routine management |
| `/admin/restart` | POST | Schedule graceful restart (requires `X-Admin-Key`) |
| `/doctor` | GET | System diagnostics |
| `/metrics` | GET | Usage metrics |
| `/health` | GET | Health check |

### 8.3 Streaming Response Format

```
data: {"type": "partial", "text": "..."}
data: {"type": "tool_start", "id": "...", "name": "..."}
data: {"type": "tool_end", "id": "...", "output": "...", "research": {...}}
data: {"type": "memory_saved", "memory_count": 42}
data: {"type": "final", "response": "...", "success": true, "execution_id": "...", "trace_id": "...", "thread_state": {...}}
```

---

## 9. Frontend Architecture

### 9.1 Web UI

**File:** `apps/web/src/index.tsx`

**Stack:** React + Vite + Framer Motion

**Features:**
- Split layout (visualizer + panel)
- Tabs: Chat, Research, Memory, Documents, Settings, Capabilities, Approvals, Executions, Projects, Routines, Soul
- Streaming chat with activity timeline
- Document upload and management
- Memory management with pinning
- Provider switching
- Settings editor with validation
- Approval Center backed by persisted approval records
- Execution / trace viewer backed by the Phase 3 execution ledger

**Phase 1 / 2 / 3 / v7 status:**
- `index.tsx` is still the dominant shell and remains the main frontend bottleneck
- `SquareAvatarVisual` was extracted to `apps/web/src/components/SquareAvatarVisual.tsx`
- `TaskChecklist` extracted to `apps/web/src/components/TaskChecklist.tsx` (v7.0.0) — renders live task plan progress inline in chat with animated status icons, result previews, and reflection notes
- Research state was extracted to `apps/web/src/features/research/store.ts`
- Research normalization moved to `apps/web/src/features/research/buildResearchRun.ts`
- Research rendering moved to `apps/web/src/features/research/ResearchPanel.tsx`
- `marketing.tsx` now depends on shared UI instead of importing dashboard internals
- Frontend quality rails now include `npm run typecheck`, `npm run check`, and research unit coverage

**Research contract status:**
- The backend now emits structured `research` payloads on tool events and final query responses
- Research evidence is distinct from assistant synthesis in both transport and UI state
- Recent/news queries carry explicit recency intent and per-source freshness buckets

### 9.2 Go TUI

**Directory:** `apps/tui/`

**Stack:** Go + Bubble Tea + Lipgloss

**Features:**
- Terminal-based interface
- Session management (`/session` commands)
- Streaming responses
- Provider controls

---

## 10. Projects & Routines

### 10.1 Projects (v5.3.0 — now thread-scoped in v6.5.0)

Projects organize related context and inject `context_prompt` into the agent's system prompt when activated.

**Structure:**
```json
{
    "id": "uuid",
    "name": "Project Name",
    "description": "Description",
    "context_prompt": "You are working on X. Focus on Y.",
    "tags": ["tag1"],
    "created_at": "2026-03-01T12:00:00Z"
}
```

**API:**
- `POST /projects/{id}/activate?thread_id=...` — activate project for a specific thread
- `POST /projects/deactivate?thread_id=...` — deactivate the current project for a specific thread
- Standard CRUD: `GET/POST/PUT/DELETE /projects`

**Pipeline integration:** `_compose_system_prompt()` calls `_get_active_project()` and injects the project's context between workspace and skills blocks. Phase 3 additionally persists the selected project in thread session state so different threads can carry different project context.

### 10.2 Routines (v5.3.0 — now wired to pipeline)

Routines enable scheduled, webhook-triggered, or manual actions that fire through `process_query()`.

**Structure:**
```json
{
    "id": "uuid",
    "name": "Daily Summary",
    "description": "Generate daily summary",
    "trigger_type": "schedule",
    "schedule": "0 9 * * *",
    "action_type": "query",
    "action_config": {"message": "Summarize yesterday's activity"},
    "enabled": true
}
```

**Pipeline integration:** `_execute_routine()` routes actions through the full 5-stage pipeline:
- `action_type="query"` → `process_query(message, source="routine")`
- `action_type="tool"` → synthetic NL query through pipeline (keeps safety gating)
- `action_type="skill"` → message through pipeline

**Scheduler:** Connected in `__init__` via `RoutineManager.set_run_callback()`. Checks every 60s. Clean shutdown in server lifespan.

---

## 11. Configuration & Settings

### 11.1 Environment Variables

**File:** `apps/backend/.env`

Key configuration areas:
- Model provider settings
- Embedding model selection
- Browser voice and Tavily search configuration
- Document RAG settings
- Memory flags
- System action safety gates
- Discord/Email/Slack integration
- Multi-agent pool settings

### 11.2 Runtime Settings

Settings can be modified at runtime via:
- `GET /settings` - View current settings
- `PUT /settings` - Update settings

Non-secret overrides are persisted to `data/settings.json`. Secret-bearing overrides are persisted separately to `data/settings.secrets.json`.

**Governance model (Phase 1):**
- `apps/backend/.env` remains the deploy-time/static configuration layer
- `apps/backend/data/settings.json` is the persisted non-secret runtime override layer
- `apps/backend/data/settings.secrets.json` stores secret-bearing runtime overrides separately from the public settings patch
- In-process provider/session state is rebuilt from these layers whenever provider/settings changes are committed
- The onboarding TUI now writes non-secret runtime state to `data/settings.json` and secret-bearing overrides to `data/settings.secrets.json` instead of maintaining a separate `~/.echospeak` source of truth

### 11.4 Research Transport Contract (Phase 2)

- `POST /query` now returns `research` in addition to the assistant response and document citations
- `POST /query/stream` emits structured research payloads during `tool_end` and `final` events
- Research records contain explicit evidence items with URL/domain/summary/content boundaries and recency metadata

### 11.5 Phase 3 Session-State Contract

- `POST /query` now returns `execution_id`, `trace_id`, and `thread_state` alongside the assistant response
- `POST /query/stream` final events now carry `execution_id`, `trace_id`, and `thread_state`
- `GET /history` and project activation endpoints are thread-aware instead of mutating one global live agent
- Approval continuity survives reload because pending approvals are hydrated from persisted thread state

### 11.3 Safety Flags

```env
ENABLE_SYSTEM_ACTIONS=false
ALLOW_FILE_WRITE=false
ALLOW_TERMINAL_COMMANDS=false
ALLOW_PLAYWRIGHT=false
ALLOW_DESKTOP_AUTOMATION=false
ALLOW_OPEN_CHROME=false
FILE_TOOL_ROOT=/path/to/allowed/directory
TERMINAL_COMMAND_ALLOWLIST=git,ls,cat,python
```

---

## 12. Safety & Security Model

### 12.1 Permission Hierarchy

```
1. Environment Flags (hardest limit)
   ↓
2. Workspace Allowlist (ceiling)
   ↓
3. Skill Allowlist (can only restrict)
   ↓
4. User Confirmation (final gate)
```

### 12.2 Confirmation Flow

```
Agent proposes action
    ↓
Agent: "I'm about to [action]. Reply 'confirm' or 'cancel'."
    ↓
User responds
    ↓
    ├─ "confirm" → Execute action
    └─ "cancel" → Discard action
```

### 12.3 File System Safety

- `FILE_TOOL_ROOT` restricts file operations to a specific directory
- Paths outside this root are rejected
- Symlinks are resolved and checked

### 12.4 Terminal Safety

- `TERMINAL_COMMAND_ALLOWLIST` restricts which commands can run
- Commands not in the allowlist are rejected
- Timeout and output limits apply

---

## 13. Data Flow Diagrams

### 13.1 Query Flow

```
┌──────────────┐
│ User Message │
└──────────────┘
      │
      ▼
┌──────────────────────────────────────────────────────┐
│ FastAPI Server                                        │
│ /query/stream endpoint                                │
└──────────────────────────────────────────────────────┘
      │
      ▼
┌──────────────────────────────────────────────────────┐
│ EchoSpeakAgent.process_query()                        │
│ 1. Hydrate thread state + pending approval            │
│ 2. Compose system prompt (base + soul + skills)       │
│ 3. Route to tool or conversation                      │
│ 4. Update execution + trace lifecycle                 │
└──────────────────────────────────────────────────────┘
      │
      ▼
┌──────────────┐
│ LLM Response │
└──────────────┘
      │
      ▼
┌──────────────┐
│ Memory Store │
└──────────────┘
      │
      ▼
┌──────────────┐
│ Return to    │
│ Client       │
└──────────────┘
```

### 13.2 Tool Execution Flow

```
LLM selects tool
    │
    ▼
┌─────────────────────────────┐
│ Check tool in allowlist     │──No──▶ Return "Tool not allowed"
└─────────────────────────────┘
    │ Yes
    ▼
┌─────────────────────────────┐
│ Check if action tool        │──No──▶ Execute immediately
└─────────────────────────────┘
    │ Yes
    ▼
┌─────────────────────────────┐
│ Check policy flags          │──Disabled──▶ Return "Action disabled"
└─────────────────────────────┘
    │ Enabled
    ▼
┌─────────────────────────────┐
│ Create ApprovalRecord +      │
│ link ExecutionRecord         │
└─────────────────────────────┘
    │
    ▼
┌─────────────────────────────┐
│ Return approval state to UI  │
└─────────────────────────────┘
    │
    ▼
User confirms/rejects
    │
    ├─Reject──▶ Update approval/execution, return cancelled
    │
    ▼ Confirm
┌─────────────────────────────┐
│ Execute tool                 │
└─────────────────────────────┘
    │
    ▼
Return result
```

---

## Appendix A: File Reference

### Core Files

| File | Lines | Purpose |
|------|-------|---------|
| `apps/backend/agent/core.py` | ~5800 | Main agent orchestration (5-stage pipeline) |
| `apps/backend/agent/tools.py` | ~4100 | Tool definitions + TOOL_METADATA |
| `apps/backend/agent/tool_registry.py` | ~380 | Tool Registry + Plugin Registry |
| `apps/backend/agent/memory.py` | ~1304 | Memory management |
| `apps/backend/agent/skills_registry.py` | ~285 | Skills/workspaces/tool/plugin loading |
| `apps/backend/agent/router.py` | ~550 | Intent router + routing decisions |
| `apps/backend/agent/document_store.py` | ~580 | Document RAG |
| `apps/backend/agent/projects.py` | ~164 | Project management |
| `apps/backend/agent/state.py` | ~293 | Phase 3 approval, execution, trace, and thread-state persistence |
| `apps/backend/agent/update_context.py` | ~260 | Shared update-context service + pipeline plugin (v6.7.0) |
| `apps/backend/agent/git_changelog.py` | ~390 | Git commit watcher, changelog parsing, diff summary, tweet prompts |
| `apps/backend/twitter_bot.py` | ~940 | Twitter/X bot: autonomous tweets, changelog tweets, mention replies |
| `apps/backend/twitch_bot.py` | ~510 | Twitch chat bot integration |
| `apps/backend/agent/routines.py` | ~310 | Routine scheduling |
| `apps/backend/api/server.py` | ~3400 | FastAPI endpoints + control plane |
| `apps/backend/config.py` | ~676 | Configuration management |
| `apps/web/src/index.tsx` | ~5900 | React web UI + approval/execution surfaces |

---

## Appendix B: Skill Reference

| Skill | Purpose | Tools |
|-------|---------|-------|
| `discord` | Discord desktop automation | `desktop_*` tools |
| `discord_bot` | Discord bot management | Bot tools |
| `discord_contacts` | Discord contact management | `discord_contacts_*` |
| `email_comms` | Email communication | `email_*` |
| `slack_comms` | Slack communication | `slack_*` |
| `self_modify` | Self-modification guidance | `self_*` |
| `soul` | Soul management | None (guidance only) |
| `web_search` | Web search guidance | `web_search` |
| `restart` | Server restart | None (uses API) |
| `system_monitor` | System status shortcuts | Plugin pipeline showcase |
| `daily_briefing` | Daily briefing tool | `daily_briefing` |

---

*End of Audit Document*
