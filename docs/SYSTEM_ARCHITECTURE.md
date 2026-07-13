# EchoSpeak System Architecture

**Status:** describes the **code as of branch `feature/v7.6.10-runtime-lifecycle-honesty`** after the production-coherence pass (video tools real handlers, memory curator, skills registry, runtime contracts).  
**Canonical contracts:** `RUNTIME_CONTRACTS.md`, `LIFECYCLE_TRUTHFULNESS.md`, `UNIFIED_COORDINATION.md`.  
Do not treat this document as ahead of implementation.

---

## 1. What EchoSpeak is

EchoSpeak is a **local-first agentic runtime** with a web UI, optional platform bots, and domain capabilities (chat, research, coding, video, memory, skills).  

The **model reasons and proposes**. The **runtime owns authority**: Project scope, Session identity, permissions, approvals, ToolRuns, jobs, mutation, verification, persistence, retries, continuation, and frontend truth.

---

## 2. Main components

| Layer | Location | Role |
|-------|----------|------|
| Web UI | `apps/web` | Projection of backend state; chat, Studio, Code, Research, Video, settings |
| API | `apps/backend/api/server.py` | HTTP + streaming; routes for chat, memory, approvals, capabilities, video |
| Agent core | `apps/backend/agent/core.py` | `EchoSpeakAgent.process_query` — primary Turn entry |
| Mode controller | `agent/mode_controller.py` | Deterministic CHAT / TASK_RESEARCH / CODING |
| State store | `agent/state.py` | Executions, Items, ToolRuns, Approvals, ThreadSessionState |
| Projects | `agent/projects.py` | Project identity + workspace roots |
| Tools | `agent/tools.py` + `tool_registry.py` + skill `tools.py` | Registered executable tools |
| Skills | `agent/skills_registry.py` + `skill_*` + `skills/*` | Package + bridged domain skills |
| Memory | `agent/memory.py` + `memory_curator.py` | `records.json` + FAISS index + curator |
| Video | `agent/video_editor/*` + `api/video_editor.py` | Document/timeline/jobs domain |
| Research | `agent/research.py`, search grounder in core | Web/local research |
| Coding | coding loop, file tools, checkpoints | Project-bound edits |
| Config | `config.py` | Policy flags, models, paths |
| Bots | `discord_bot.py`, etc. | Alternate surfaces into same agent |

---

## 3. Governing chain

```
Project (ProjectManager + workspace_root)
  → Session / thread (ThreadSessionState: active_project_id, permissions, pending approvals)
    → Turn / Execution (StateStore ExecutionRecord)
      → Item (user_message, tool_run, memory_write, …)
        → ToolRun (canonical action identity + outcome)
          → Verification + durable domain state
            → Frontend projection
              → Final response (projection-only completion language)
```

---

## 4. Canonical ownership table

| Concept | Owner | Durable store | Projections / caches |
|---------|--------|---------------|----------------------|
| Project | `ProjectManager` | `apps/backend/projects/*.json` | Session `active_project_id` |
| Session / thread | `StateStore` | runtime JSON under state dir | Frontend thread list |
| Execution / Turn | `StateStore` | execution records | Activity UI |
| ToolRun | `StateStore` | tool run records | Tool cards |
| Approval | `StateStore` | approval records | Approval UI |
| Permissions / tools allowed | Session + config + ToolRegistry | Session fields + env/config | Capabilities API |
| Memory | `AgentMemory` | `data/memory/…/records.json` | FAISS, profile.json, Studio |
| Memory proposals | `MemoryCurator` | pending under `data/pending_memory_confirmations/` | Chat confirm prompt |
| Session-only memory | `MemoryCurator` | `data/session_only_memory/` | Prompt (labeled non-durable) |
| Skills | `SkillsRegistry` | package dirs + video bridge | Skill prompts |
| Video document/timeline | `VideoEditorStore` | `data/video_editor/projects/…` | Video UI |
| Video jobs | `VideoEditorStore` + job helpers | document `jobs[]` | Job projection |
| Active coding work | `ActiveWorkStore` | `data/active_work/` | Prompt continuity |
| Session summary | `SessionMemoryDistiller` | `data/session_memory/` | Prompt (not durable personal memory) |

**Rule:** UI, FAISS, and profile never outrank their durable owners.

---

## 5. Request lifecycle (happy path)

1. Client sends message with `thread_id` (Session).
2. API binds agent + thread scope (`_apply_thread_scope`).
3. `process_query` → `_bind_turn_mode` (mode + tool inventory).
4. Video-aware Turns: `_bind_video_turn_to_decision` filters `video_*` tools and injects structured editor context when relevant.
5. Memory context: selective durable + session-only (labeled).
6. Model / planner proposes tool calls.
7. Each tool invoke: ToolRun start → policy/authority → execute → finish ToolRun.
8. Mutations: ApprovalRecord claim → revalidate → apply → terminalize.
9. Memory explicit: curator → validate → persist or ask_confirmation → `memory_write` Item only on success.
10. Final response reads ToolRuns / approvals / memory write status — not free-form “I saved it” without evidence.

---

## 6. Mode selection

`classify_turn_mode` (`mode_controller.py`):

- **CHAT** — default; utility clock/calc stays non-verification-gated.
- **TASK_RESEARCH** — checkable / live / research intent.
- **CODING** — local Project/file/implement intent.

Mode **does not** hard-empty the tool inventory (`allowed_tools_for_mode` returns full registered set). Real gates: registration, config flags, Project path roots, role denylists, and Turn-specific filters (e.g. video tools only on video Turns).

---

## 7. Tools

- **Registry:** `ToolRegistry` (`tool_registry.py`).
- **Legacy bridge:** `get_available_tools` + metadata still register bulk tools.
- **Video tools:** real handlers in `video_editor/tools.py` (context, inspect, plan, propose, jobs, creative memory). Apply remains approval-service owned (`approval_required` structured error if forced).
- **Skill tools:** loaded via `load_skill_tools` when workspace skills activate.

Structured tool errors should prefer JSON `{ok:false,error_code,...}` over success-looking prose.

---

## 8. Skills

- **Canonical owner:** `SkillsRegistry` (`skills_registry.py`).
- **Filesystem packages:** `apps/backend/skills/*` with `skill.json` + `SKILL.md`.
- **Video domain skills:** bridged from `VideoSkillRegistry` into the same registry.
- **Selection:** `skill_selection.py` (direct tool vs skill vs blocked).
- **Execution identity:** `skill_execution.py` records under `data/skill_executions/`.
- **Creation:** `skill_create` writes experimental + **disabled**; not executable until enable/review.

---

## 9. Memory

- **Canonical:** `AgentMemory` → `records.json`.
- **Curator:** `MemoryCurator` — LLM primary when available; deterministic fallback; schema fail-closed.
- **Explicit remember:** chat path in `core.py` → curator → `memory_write` Item.
- **Confirmation:** pending files + yes/confirm fullmatch.
- **Forgetting:** tombstone active=false; selective retrieval skips inactive.

---

## 10. Video

- **Store:** `VideoEditorStore` — documents, revisions, transactions, jobs, artifacts.
- **API:** `/video/*` under `api/video_editor.py`.
- **Chat:** context + tools when video intent or open document (not utility small-talk).
- **Manual + agent:** same operation engine; agent path needs approval + revision bind.
- **Workers:** analysis/render/generation may be `blocked` shells — honest non-completion.

---

## 11. Research & coding

- Research: mode TASK_RESEARCH + web tools + evidence paths; local-first when Project constraints say so.
- Coding: CODING mode + file tools + approvals for writes + coding loop / active_work continuity.

---

## 12. Frontend

- **Primary:** `apps/web/src/index.tsx` — shell, chat stream, hydration of executions/ToolRuns/approvals.
- **Video UI:** `features/video-editor/*` — document projection only.
- Opening `/app/video` must not create Session/document (empty states only).

---

## 13. Extension process

To add a capability:

1. Define durable owner + schema under Project/Session/Turn chain.
2. Register tools with policy flags + ToolRun outcomes.
3. Optionally add SkillsRegistry manifest + selection intents.
4. Wire approvals if mutating.
5. Project frontend from backend only.
6. Add focused tests for lifecycle + failure.

---

## 14. Production execution ownership

| Path | Role |
|------|------|
| **`EchoSpeakAgent.process_query`** (`agent/core.py`) | **Primary production owner** for Web UI / API chat Turns |
| **`api/video_editor.py`** propose/consume | Domain-owned video mutation lifecycle (ToolRun + ApprovalRecord) |
| **`agent/orchestrator.py`** | **Non-primary.** Reachable only when `config.orchestration_enabled` is true (default **false**) via explicit `/orchestrate` routes — not normal chat. Sub-tasks must still use the shared agent; no second mutation authority. |

---

## 15. Production-closure contracts (2026-07)

### Approval identity (mutations)

Bound on every mutation ApprovalRecord:

- owner Session (`session_id` / `thread_id`) + Project (`project_id` / `active_project_id`)
- Turn / Execution (`execution_id`, `original_turn_id`)
- tool ID + **canonical arguments hash**
- target resource (path or video document/transaction)
- source version: filesystem `source_precondition.entries[]` (sha256/size) or video `document_revision` + `operation_hash`
- expiration/consumed state via ApprovalRecord `status` (`pending` → `consuming` → terminal)

**Freeze metadata** (`path_basename`, `original_input_sha256`, `tool`) may live on `source_precondition` for audit identity but **must not** invalidate an unchanged source. Mutation compare uses **entries only** (`tools._mutation_precondition_denial`, `core._source_precondition_matches`).

**Video consume** always re-loads the durable ApprovalRecord before claim so a stale client snapshot cannot re-apply after terminalization.

### Coding named-file pin

Named basenames and explicit exclusions flow request → coding plan → pending kwargs → approval → write. A later plan step must not silently retarget another file when the user named one (and “do not edit X” is honored).

### ToolRun correlation

- One logical chat mutation → one confirm-path write ToolRun (+ verification read).
- Video propose → `video_propose_operations` ToolRun; apply → `video_apply_transaction` (+ child op ToolRuns).
- Jobs attach `execution_id` / `tool_run_id`.
- **Canonical hydration API:** `GET /tool-runs?session_id=&execution_id=&project_id=` and `GET /executions/{id}/tool-runs` (parent/child via `retry_of`, `approval_id`, verification). History turns still embed `tool_runs`; the web client merges `/tool-runs` when a turn projection is empty after refresh/restart.

### Research artifact handoff

Completed `web_search` ToolRuns persist a `ResearchArtifact` (`agent/research_artifacts.py`) with Project/Session ownership and citations. Skills look up compatible artifacts by Project/objective — not unstructured prose alone.

### Skill executable status

`agent/skill_status_audit.py` classifies each registered skill as:

`executable` | `blocked_missing_tool` | `blocked_missing_model` | `blocked_missing_artifact` | `prompt_only` | `invalid` | `disabled` | `deprecated`

Only `executable` has `executable=true`. Prompt packages without production tools are not “ready.”

---

## 16. Known limitations (honest)

- **Full browser UI acceptance** (multi-tab, live WebSocket reorder, UI-only projection bugs) still required for a 10/10 claim; backend process_query / API / restart soak are exercised.
- Some skill packages remain `prompt_only` (integrations without registered tools).
- Video render/export/analysis workers may be `blocked` shells — honest non-completion.
- Repository self-edit/rollback still lacks a full HEAD/index precondition (see RUNTIME_CONTRACTS).
- Frontend TypeScript typecheck / Playwright matrix not closed in this pass unless separately run.

---

## 17. Representative flows (summary)

| Example | Path |
|---------|------|
| A. Normal chat | CHAT → no tools required → response |
| B. Inspect Project | CODING/read tools → file_list/read → ToolRuns → summary |
| C. Code edit | propose → ApprovalRecord → file_write ToolRun → verify |
| D. Memory | curator → records.json → memory_write Item → later selective inject |
| E. Research | TASK_RESEARCH → web_search ToolRun → ResearchArtifact → skill lookup |
| F. Video plan | video Turn → propose ToolRun → approval → apply ToolRun → revision |
| G. Retry | same ToolRun identity / intent_relation=retry |
| H. Skill continue | skill_execution + plan IDs on Session |

---

## 18. Testing

Focused suites (examples):

```text
pytest tests/test_production_closure.py tests/test_production_closure_lifecycle.py \
  tests/test_coding_fixture_workflow.py tests/test_video_tools_handlers.py \
  tests/test_skill_system_and_video_chat.py tests/test_memory_curator*.py -q
python scripts/_restart_soak_once.py
python scripts/_audit_skills_once.py
```

Use disposable Projects and synthetic media only.
