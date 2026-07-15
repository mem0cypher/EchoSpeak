# EchoSpeak System Architecture

**Status:** canonical implemented architecture for the current worktree.  
**Related contracts:** `ECHO_CORE_CONTRACT_MATRIX.md`, `RUNTIME_CONTRACTS.md`,
`LIFECYCLE_TRUTHFULNESS.md`, `DESKTOP_ARCHITECTURE.md`, and
`SIX_SYSTEM_IMPLEMENTATION_LEDGER.md`.

## Product layers

| Layer | Location | Responsibility |
|---|---|---|
| Windows desktop | `apps/desktop` | Tauri 2 host, authenticated loopback bootstrap, sidecar lifecycle, native window behavior |
| Web projection | `apps/web` | Chat and workspace UI; never owns runtime truth |
| Runtime/API | `apps/backend/api/server.py` | Session-scoped HTTP/streaming boundary and background-service lifecycle |
| Agent control plane | `apps/backend/agent/core.py` | Canonical interactive Turn entry, mode binding, planning, tool invocation, synthesis |
| Durable authority | `agent/state.py`, `projects.py`, domain stores | Projects, Sessions, Executions, ToolRuns, approvals, tasks/runs, artifacts, memory |

The governing chain is:

```text
Project
  -> Session
    -> Turn / Execution
      -> Item
        -> ToolRun
          -> verification and durable domain state
            -> frontend projection
              -> final response
```

The model may plan and synthesize. It cannot grant authority, approve itself,
expand Project scope, or establish completion through prose.

## Canonical ownership

| Concept | Single authoritative owner | Rebuildable projections |
|---|---|---|
| Project identity/root | `ProjectManager` | Sidebar and Studio Project views |
| Session execution context | `ThreadSessionState` / `StateStore` | thread list, workspace chrome |
| Execution, ToolRun, approval | `StateStore` | Chat transient state, Viewer, Studio |
| Registered capability | `ToolRegistry` and `SkillsRegistry` | Tools/Skills screens, prompts |
| Personal semantic memory | `MemoryCurator` writing `AgentMemory` records | indexes, profile/Markdown mirrors, Memory Studio |
| Documents | `DocumentStore` | chunks, embeddings, graph and UI results |
| Research | `SearchGrounder` plus `ResearchArtifactStore` | Research workspace and citations |
| Coding continuation | `ThreadSessionState` plus `CodingExecutionLedger` | `ActiveWorkStore` hints and Code workspace |
| Tasks and definitions | `TaskStore` and `RoutineManager` | Automations cards and schedule views |
| Automation execution | `AutomationRunStore` | callback queues, service health, Automations history |
| Connections | `ConnectionRegistry` | Connections cards and routine capability choices |
| Media | `MediaLibraryStore` | Media workspace and generated-output cards |

Malformed authoritative JSON fails closed, is preserved in quarantine, and
includes a manual recovery diagnostic. It is never treated as a successful
empty store.

## Research and retrieval

`SearchGrounder` is the sole grounded web-search orchestrator. Provider adapters
live in `agent/web_search_providers.py`; `LiveRetrievalRouter` classifies domains
that require exact structured current data. Deep results are saved as typed,
versioned `ResearchArtifact` records carrying exact Project/Session scope,
active model binding, budgets, plans, branches, claims, evidence, contradictions,
gaps, freshness, and verification.

The former Echo Search v1 workflow was removed. SearXNG support now lives in the
canonical provider adapter, so there is no second planner/evidence owner.

## Memory and context

Durable semantic records carry owner, Project, Session, type, lifecycle,
provenance, revision, checksum, freshness, sensitivity, and supersession links.
Scope filtering precedes keyword/semantic ranking. Hybrid retrieval combines
exact metadata, lexical and semantic candidates with reciprocal-rank fusion;
inactive, forgotten, superseded, and mismatched records are excluded.

`ContextAssembler` selects bounded typed context. FAISS, summaries, profiles,
and Obsidian Markdown are projections. Obsidian synchronization is optional,
explicit, plan-driven, conflict-aware, and cannot overwrite canonical memory.

## Coding

Coding requires an attached Session Project whose `ProjectManager` root matches
the current thread state. `CodingExecutionLedger` persists objective, phase,
revision, checkpoints, evidence, and resume state. `ActiveWorkStore` remains a
non-authoritative continuity projection.

Consequential filesystem actions use the normal approval and ToolRun boundary.
Approval identity is stable, while current policy, permission, Project/root,
path/source preconditions, tool inventory, and configuration are revalidated at
consumption time.

## Automations and Connections

The API runtime owns one Routine scheduler. Heartbeat evaluates triggers,
claims/reclaims work, and reports health; it never sends external messages or
mutates user resources directly. A trigger creates or resumes one exact-scope
Task and `AutomationRun`; the Run binds the active Session model and enters the
same Turn, approval, ToolRun, checkpoint, and verification path as interactive
work.

`ConnectionRegistry` stores secret-free capability, scope, auth-health, and
error metadata. Connections expose narrow capabilities but do not own Tasks,
Routines, Runs, or completion.

## Media and retired Editor

The built-in image/video Editor, its stores, workers, tools, APIs, frontend
workspaces, and permission gate are removed. Media Library, voice, image/video
generation adapters, artifacts, and verification remain independent.

Legacy Editor state is not deleted or rewritten. The explicit
`scripts/retire_editor_data.py` path inventories and archives source JSON
byte-for-byte, records checksums and unsupported references, and can create an
idempotent Media import plan. Source data remains untouched.

## Frontend behavior

Chat projects user and assistant messages plus one transient operation state;
pending approvals and actionable failures remain visible. Durable ToolRuns,
evidence, plans, and verification remain available in Studio and Viewer.

Studio navigation uses accessible, horizontally scrollable tabs with arrow,
Home, and End keys. Research, Memory, Automations, Connections, Tools, Settings,
and other surfaces query backend owners using the active Project/Session.

## Extension rule

New capabilities must define one durable owner, explicit identity/scope,
registered tools, approval/revision behavior for mutations, structured
verification, restart recovery, and a projection-only UI. No integration may
invent a parallel completion, permission, retry, or ToolRun system.
