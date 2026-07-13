# EchoSpeak Runtime Contracts

**Master index of product contracts** for the 2026 lifecycle / reliability wave.

**Platform north-star:** optimize for the **next subsystem being easy to plug
in**, not for the next demo. Prefer one authoritative source of truth over
guesses. See **`docs/UNIFIED_COORDINATION.md`**.

| Document | Owns |
|----------|------|
| **`UNIFIED_COORDINATION.md`** | Unified lifecycle; capability system; ownership table; skills/jobs/integrations; frontend projection; how to extend |
| **This file** | Equal models; Mode/Project/permissions; Project + Code workspace; refresh hydration; search/utility/references; coding execution targets; streaming/concurrency; explicit-file + approval-scope identity; **Known limitations** |
| **`LIFECYCLE_TRUTHFULNESS.md`** | Recovery evidence statuses; confirmation types A–D; ToolRun identity (truthfulness); projection status language; corrupted-file policy; truthful final responses |

**Rule:** Do not restate full tables from either file in satellite docs. Summarize and link.

**Wave status (v7.6.10):** **Implemented in code (partial); pending live validation.** Unit tests and architecture text are not acceptance. See `LIFECYCLE_TRUTHFULNESS.md` §11 and **this doc §K** for live gates.

---

## Status legend

| Label | Meaning |
|-------|---------|
| **Implemented** | Code path exists in the worktree |
| **Pending live validation** | Not closed by unit tests alone; needs Web UI / multi-session checks |
| **Contract required / gap** | Intended product rule; incomplete, over-strict, or recently broken in live use |
| **Known debt** | Accepted unfinished work; must not be silently marked “done” |

---

## A. Equal model capabilities

**Canonical owner: this section.**  
**Code:** `agent/model_runtime.py`, `core.py` (`_allow_llm_tool_calling`, plan depth, context injection).

### Contract

1. **No local/small-model functional restrictions.** Capability profiles are **observability and operator metadata**, not runtime gates for tools, planner depth, autonomy, history inclusion, recovery, or search packaging.
2. **Equal planner depth and autonomy defaults** for every configured model (full plan depth / autonomous step defaults; profile `recommended_*` fields must not shrink execution).
3. **Provider-independent tool-path attempts.** Native tool-calling is allowed for all providers unless the operator sets an explicit global opt-out (`disable_native_tool_calling`). Do not disable tools only because the provider is “local” or “small”.
4. **Real context-window resolution.** Physical window comes from:
   - explicit profile override / discovery metadata, or  
   - configured `llm_trim_max_tokens` / `local.context_length` injected at turn start, or  
   - a **single universal fallback** when unknown.  
   **Forbidden:** assuming local = 32k and hosted = 128k as tiered runtime limits.
5. Failures must come from **real** limits (OOM, provider error, path policy, approval, verification) — not silent task downgrades.

### Status

**Implemented** for defaults and turn injection (`runtime_gates: disabled_equal_full_access`).
Hosted providers no longer inherit `config.local.context_length`; explicit global
trim/profile limits and local-provider physical limits remain distinct.
**Pending live validation** across Gemma-local vs hosted Gemini/OpenAI sessions
for identical tool reach, plan depth, and correct context budgets.

---

## B. Mode, Project, and permission separation

**Canonical owner: this section.**  
**Related:** `MODE_SYSTEM.md` (routing summary), `THREAD_EXECUTION_CONTEXT.md` (schema).

### Contract

| Concept | Is | Is not |
|---------|----|--------|
| **Chat / Research / Coding** | Interaction **mode** (routing + verification expectations) | A filesystem workspace that empties Project tools |
| **Skill workspace** (`chat` / `coding` / `research` folders) | Soft prompt + skill metadata | Project root or hard tool ceiling |
| **Attached Project** | Session-scoped folder authority | Optional only when mode is CODING |
| **Project scope** | Allowed **paths** for tools | Write permission by itself |
| **Permissions** | Flags for writes, terminal, etc. | Path roots |

1. **Attached Projects remain available during normal chat** for read-oriented work and capability reporting when scope is bound.
2. **`GET /capabilities` must bind the exact Session** (`thread_id`) via `_apply_thread_scope` before reporting tool readiness. Never report the shared agent’s stale skill-workspace as if no Project were attached.
3. **`project_status` is a registered safe read tool** when Project scope is active.
4. **`TOOLS.txt` is not a hard allowlist.** Availability = registration + policy + Project/session scope (+ role denylists). Skill lists guide behavior only.

### Capability inventory authority (closure)

| Surface | Single owner |
|---------|----------------|
| Tool registration / `is_action` / policy flags | **`ToolRegistry`** |
| `GET /capabilities` tool list | **Full `ToolRegistry` names** (not `agent.tools` alone) |
| Coding readiness | `coding_readiness.build_coding_readiness` (intersection of registry + project + flags) |
| Video domain adapters/jobs | `video_editor.capabilities` (domain projection; mutations still go through ToolRun/approval) |
| Skill selection / executable status | **`SkillsRegistry` + `skill_selection`** |
| Workspace skill prompts | `_active_skill_defs` (prompt only; not a second permission owner) |
| Turn tool shrink (research/video) | `_bind_research_tool_inventory` / `_bind_video_turn_to_decision` on ModeDecision |

Future Photo / Voice / Calendar: register tools in `ToolRegistry`, optional skill manifests in `SkillsRegistry`, domain stores for durable media — **no parallel approval or ToolRun system**.

### Status

**Implemented** in `/capabilities` and tool inventory wiring (registry-first list + SkillsRegistry projection). **Pending live validation** that CHAT mode + attached Project still shows path-bound file tools correctly.

---

## C. Project lifecycle and Code workspace

**Canonical owner: this section.**  
**Code:** `EchoSpeakAgent.activate_project`, `_clear_session_project_scope`, `code_workspace`, `ActiveWorkStore`, StateStore `detach_project`.

### Contract

1. **Attach / switch / detach / delete is one scope transaction** for the affected Session(s). On clear or switch:
   - ThreadSessionState: `active_project_id`, `project_path`, `workspace_root`
   - Pending approvals and in-memory pending action
   - Retry targets
   - ActiveWork for that thread
   - Request-local tool root / project root
   - Preview process (Code workspace preview)
   - Soft path-only “project” identity (no durable project id pretending to be attach)
2. **Remove path-only soft Projects** as durable authority. Filesystem tools need an explicit Project/session root, not “we saw a Desktop path once.”
3. **Code workspace UI** (Files, Preview, Terminal, Changes) must be backed by **real Project state** for the active Session — not orphan paths from a previous pin.

### Status

**Implemented** clear/switch transaction helpers. **Pending live validation** of Studio Files/Preview/Terminal/Changes after attach/switch/detach/delete; soft-project removal across all entry points.

---

## D. Refresh hydration

**Canonical owner: this section.**  
**Code:** `StateStore.session_timeline` / `turn_projection`, `GET /history`, frontend history consumer.

### Contract

After page refresh (or Session reload), restore **complete Turns**, not only chat text:

| Must restore | Notes |
|--------------|--------|
| Messages | User + assistant |
| ToolRuns | Same ids as live stream |
| Research / sources | Evidence objects when present |
| Approvals | Pending + history linkage |
| Verification | Per-ToolRun / projection |
| Files / terminal results | From durable turn projection |
| Execution status | From finalize, not invented “complete” |

Rules:

1. **Group by exact `execution_id` / Turn id.**
2. **Idempotent hydration** — re-fetching history must not duplicate rows or restart tools.
3. **Historical activity must not restart as live animation** (no re-running tools; no provisional “running” chrome for finished ToolRuns).

### Status

**Implemented** backend timeline + `/history` turns payload. Historical
nonterminal ToolRuns hydrate as interrupted/error rather than live or successful.
**Pending live validation** of full UI restore (research panel, approvals, files,
terminal, no zombie animations).

---

## E. Search, utility tools, and conversational references

**Canonical owner: this section** (search packaging also `SEARCH_ENGINEERING.md` at high level).  
**Truthfulness of “search ran / evidence enough”:** also `LIFECYCLE_TRUTHFULNESS.md`.

### Contract

1. **Utility tools are not research.** Clock/date/calc → CHAT / utility reason (`utility tool request (clock/date/calc)`), no research verification gate, no “partial research” finalize.
2. **One canonical search row** per logical search intent (ToolRun id identity — lifecycle §5).
3. **Current-Turn evidence only** for claims about what was searched or found this Turn.
4. **Sports query isolation** — sports enrich / live paths must not bleed subject or ToolRuns into unrelated intents (or vice versa).
5. **Structured offered actions** for “okay, do that” / “yes, look that up” → `pending_offered_action` (lifecycle §4 type A).
6. **Durable factual claims** for “double check that” → `last_assistant_claim` / claim memory with `origin_execution_id`; research target = claim text, not “what do you want me to check?”

### Status

**Implemented** utility classification, offered actions, claim memory, search id hardening. **Pending live validation** of single Search-done row and sports isolation under multi-intent load.

---

## F. Coding execution truth

**Canonical owner: this section** for planner/targets; mutation/corruption/projection cross-link lifecycle.

### Contract

`agent/coding_readiness.py` is the sole readiness projection for the endpoint,
Tools diagnostics, and coding request preflight. It intersects ProjectManager,
Session attachment, provider/model tool path, registered versus loaded tool
inventory, current permissions/configuration, pending approval, and terminal
runtime. Reading and editing readiness are separate; terminal is optional for
ordinary file editing. See `SETTINGS_REQUIRED_FOR_ECHOSPEAK_CODING.md`.

1. **Reject unresolved planner placeholders** (`{{first_relevant_file}}`, `${…}`, symbolic paths) at validation — never invoke tools with them.
2. **Convert `file_list` results into exact `file_read` tasks** with real basenames under Project root when the user required scan/read-all or required files.
3. **Derive required actions from user wording** (scan intent, named files, implement verbs) — not from optimistic “six tasks complete.”
4. **No “I understand the codebase”** without successful required `file_read` ToolRuns (lifecycle projection §6).
5. **No mutation claims** without successful write ToolRun (lifecycle §4 / §8).
6. **Suspicious full-file replacement guards** and **corrupted-file blocking** (lifecycle §7) — corruption blocks normal feature development on that baseline.

### Explicit file as mutation target (critical)

| Rule | Detail |
|------|--------|
| **User-named file wins** | If the user names `index.html` (or any basename), that file is the **mutation target** unless they also name others. |
| **Supporting reads ≠ write targets** | Reading `game.js` / `style.css` for context must not retarget the pending write or implement plan to those files. |
| **Implement gaps** | Feature heuristics must not override an explicit filename from the current request. |

**Reported live defect (post-doc-pass):** user asked for work on **`index.html`** but **`game.js`** became the edit target after supporting reads. The implement path now applies a final current-request basename pin after gap analysis, so supporting reads and ActiveWork heuristics cannot select another mutation target. A disposable Project fixture now proves the backend read/propose/confirm/cancel/stale-source/verification lifecycle and that `game.js` remains unchanged; the full browser flow remains pending live validation.

### Status

Placeholder reject, list→read inject, projection honesty, marker/suspicious guards: **Implemented**. Explicit-file mutation pinning and removal of the untracked raw-read fallback: **implemented and covered by a disposable backend Project fixture; browser live validation remains pending**.

---

## G. Streaming and concurrency

**Canonical owner: this section.**

### Contract

1. **Client `request_id` ↔ durable Turn / `execution_id` binding** (`turn_bound` stream event) so UI debug and ToolRun rows correlate.
2. **Exact ToolRun identity** from `tool_start` through durable store through history hydrate (lifecycle §5).
3. **Synchronous Session-switch abort** — switching Session must stop applying stream events / tools for the previous Session’s in-flight request (no cross-Session animation or tool completion).
4. **Single-writer process lock** for durable state (`StateStore` phase3 process lock) so two processes do not corrupt phase3 JSON.
5. **Capability / status refreshes must not change another active Session’s** tool scope, pending approval, or project pin. Refresh always keys by `thread_id`.
6. **Corrupt durable JSON fails closed.** Unreadable authoritative state is not
   converted to an empty map and overwritten on the next persistence call. The
   original remains untouched; startup creates `phase3/corrupt-state/<id>/`
   with a byte-for-byte copy and `RECOVERY.txt` before reporting the failure.
7. **One owner per durable fact.** ProjectManager owns Project metadata/root;
   ThreadSessionState owns only thread attachment and its root projection.
   ActiveWork owns resumable coding digest/goal, not Project scope. ApprovalRecord
   owns approval identity/status; `_pending_action` is a cache. Durable ToolRuns
   own execution lifecycle; callback/event queues are delivery projections only.

### Status

**Implemented:** turn_bound, ToolRun ids, process lock, corrupt-state fail-closed
loading, capabilities `thread_id` bind. **Pending live validation:** Session-switch
abort under concurrent streams; capability refresh never canceling another
Session’s pending approval (see §H).

---

## H. Approval scope identity (and Studio / capabilities)

**Canonical owner: this section.**  
**Related:** lifecycle §4 type B (exact mutation approval).

### Contract

A pending write approval is valid only when these identities still match:

| Identity | Role |
|----------|------|
| Session / `thread_id` | Which conversation |
| Project id + project path | Which folder |
| Tool name | e.g. `file_write` |
| Canonical arguments hash | Exact path + content (+ other kwargs) |
| Action id / plan id | Durable approval record |

**Must NOT invalidate a pending approval by itself:**

- Opening Studio / Code workspace UI  
- Refreshing `/capabilities` or doctor/status for the **same** Session  
- Cosmetic mode or layout changes  
- Re-reading supporting files for display  

**Must invalidate:**

- Project attach/switch/detach/delete for that Session  
- Path/args change (new hash)  
- Thread/Session change  
- Explicit cancel or superseding `new_objective` that cancels approvals by design  

### Gap / live risk

`_pending_action_matches_execution_context` now compares stable Session/Project/path,
origin execution, action/plan, tool, and canonical-arguments identities. Mutable
objective/mode/capability/permission snapshots remain audit evidence; current
constraints and action configuration are revalidated directly at execution.
All filesystem mutations carry source/destination version preconditions. This
includes write/patch-via-write, delete, move, copy, mkdir, artifact replacement,
and checkpoint undo. The same precondition is checked once during approval
consumption and again inside the tool immediately before the filesystem call.
Directory identities are deterministic and bounded; reparse points, inaccessible
entries, excessive size, or incomplete scans make the approval non-consumable.
Approval consumption then freshly validates current policy, permissions,
Project/root, canonical paths, registered tool inventory, and configuration.
Approvals created by older versions without a frozen execution identity are
readable but cannot be consumed; the user must request a fresh proposal.

**Implemented in code; pending live validation** that Studio open + capabilities
refresh leave pending approvals intact and that a concurrent source edit blocks
the stale write.

### H.0.1 Production-closure approval identity (2026-07)

Every mutation approval also binds:

| Field | Notes |
|-------|--------|
| `canonical_arguments_hash` | Exact kwargs identity |
| `source_precondition.entries[]` | Filesystem content identity (v2) |
| Freeze fields (`path_basename`, `original_input_sha256`) | Audit aids only — **not** compared for stale-source denial |
| Video: `document_revision` + `operation_hash` | Timeline identity |
| Atomic claim | `claim_pending_approval` → `consuming`; second claim fails closed |
| Video durable re-load | `consume_video_approval` re-fetches ApprovalRecord before status checks so stale client snapshots cannot re-apply |

**Coding named-file pin:** `_explicit_files_named_in_request` + `_file_write_path_allowed_by_request` bind named basenames and exclusions through proposal and pending write; silent retarget to another project file is refused.

**Orchestrator:** `ORCHESTRATION_ENABLED` defaults false. Normal production path is `process_query` only.

**Research handoff:** durable `ResearchArtifact` records (not prose alone) with Project/Session ownership for skill consumption.

**Skill truth:** `skill_status_audit.classify_skill` — prompt-only / blocked skills report `executable=false`.

Regression suites: `tests/test_production_closure.py`, `tests/test_production_closure_lifecycle.py`, `tests/test_coding_fixture_workflow.py`. Disposable restart soak: `scripts/_restart_soak_once.py`.

---

## H.1 Authoritative personal memory

`AgentMemory.records.json` is the durable owner for explicit long-term memory.
FAISS is a rebuildable retrieval index, `profile.json` is a legacy compatibility
projection, SessionMemory is a per-Session context cache, and Studio is an API
projection. None may independently prove that a memory was saved.

An explicit save is acknowledged only after an active record exists with a
stable ID, owner, scope, normalized content, type, source Session/Turn/Item,
timestamps, and index state. Account memories remain visible across Projects.
Index failure leaves the durable record visible as `failed`/`unavailable` rather
than deleting or hiding it. Corrections supersede the prior semantic preference;
forgetting tombstones the record, removes or invalidates its index entry, clears
its profile projection, and removes matching derived Session-cache facts.

Legacy FAISS/profile items are imported once with explicit legacy provenance.
They are not treated as newly user-confirmed facts.

Thread-pooled agents sharing one memory path use one in-process `AgentMemory`
owner, serialize canonical record access with a per-store lock, and refresh
`records.json` before reads and mutations, so one Session cannot persist a stale
record or vector-index snapshot over another. Account
memory recall and explicit save/forget are owner-only; public/community adapter
requests receive neither the owner's durable memory prompt context nor write
authority. A completed memory-write Item triggers Studio refresh even when a
correction keeps the active record count unchanged.

---

## I. Dedicated evidence contracts (not “general honesty”)

These are first-class contracts. Full status tables live here and in lifecycle where noted.

### I.1 Recovery evidence contract

→ Full table: **`LIFECYCLE_TRUTHFULNESS.md` §3**

Must record per source: `checked_found` | `checked_not_found` | `not_checked` | `unavailable` | `permission_required`.  
**Live logs still showed recovery claims without ToolRuns** — treat as **regression-prone; live gate L1 required**. Implementation of the honesty rewriter is not the same as “recovery tools exist for Windows VSS.”

### I.2 Proposal / weak confirmation contract

→ Types A–D: **`LIFECYCLE_TRUTHFULNESS.md` §4**

- Type A (offer): prepare work only.  
- Type B (exact mutation): only path to write on web/UI without auto-confirm.  
- Weak “yes” with no durable target → refuse; no hollow “Proceeding.”  
**Live logs showed weak proposal confirmation** — **pending live validation** of A→B chain.

### I.3 Incomplete evidence tracking contract

For every Turn that claims work:

| Claim class | Required evidence this Turn |
|-------------|----------------------------|
| File understood | Successful `file_read` ToolRun(s) for named/required files |
| Mutation applied | Successful mutator ToolRun or honest “awaiting type B” |
| Search done | Canonical `web_search` ToolRun id + outcome |
| Recovery source checked | ToolRun for **that** source only |
| Tasks complete | Projection status `complete` + required ToolRuns |

Incomplete evidence → `partially_complete` / `in_progress` / `needs_permission` / `awaiting_confirmation` per lifecycle §6 — never silent `complete`.

---

## J. Known limitations (unresolved debt)

Do not mark these “done” because contracts exist.

| Debt | Why it matters | Direction |
|------|----------------|-----------|
| **Structured `ToolOutcome` becomes text** on some planner paths | Planner/UI lose structured success/verification; harder projection | Keep structured outcomes through TaskPlanner → stream → hydrate |
| **Search-stack consolidation** | Production wrappers converge on `_grounded_web_search`; `EchoSearchWorkflow` remains experimental/test-only dead code | Remove the experimental stack or deliberately replace the canonical owner before production use |
| **Large `core.py` control-plane overlap** | Mode, planner, honesty, search, coding in one module → regressions | Split only after live gates green |
| **Unbound or direct tool bypasses** | Calls outside `_invoke_authorized_raw_tool` skip approval/path policy | Audit remaining invoke sites; plugins must not mutate externally |
| **Exact approval-policy revalidation** | Stable identity + current policy recheck are implemented; filesystem and video-timeline actions have version preconditions, while repository self-edit/rollback still lack one coherent HEAD/index/worktree precondition | Add a repository-aware frozen identity before treating self-edit/rollback as covered |
| **Missing transition tests** | Mode/project/approval transitions not fully covered | Add matrix tests: attach/switch/detach, A/B confirm, Session switch mid-stream |
| **Windows VSS / File History ToolRun** | Recovery correctly stays `not_checked` but product cannot check yet | Optional adapter later |
| **Explicit-file write retarget browser validation** | Disposable backend fixture passes; the reported browser interaction has not been rerun | §F live browser test |
| **Equal-access + live multi-provider** | Defaults equal; not fully live-proven | §A live matrix |
| **Video Editor is foundation-only** | Timeline ops + approvals + store work in unit tests; no real playback, proxies, FFmpeg export, or generation | Finish K-video browser pass first; then playback/proxies/export; generation last |
| **`self_edit` / `self_rollback` Git preconditions** | Still lack one coherent HEAD/index/worktree freeze | Same as approval revalidation row for repo tools |
| **Recursive directory mutations** | Cannot be perfectly atomic | Document fail modes; prefer file-level ops |
| **Generic plugin / direct-tool authority debt** | Paths outside `_invoke_authorized_raw_tool` remain broader | Continue audit |
| **Authenticated remote media preview tokens** | Secure token-delivery design not finalized | Design before remote asset preview |
| **Historical zero-message Sessions** | Cannot always distinguish old phantoms from real empty Sessions | Prefer prevent new phantoms; migration optional |
| **Real VFR / real provider generation / full browser lifecycle** | Not exercised | Product validation later |

---

## K. Live acceptance (broader than lifecycle §11)

Until these pass, release docs stay **pending live validation**:

1. Lifecycle §11 L1–L8  
2. Equal tools/plan depth on local vs hosted model  
3. `/capabilities?thread_id=` matches attached Project for that Session only  
4. Attach → Studio Files/Preview → detach clears scope and approvals  
5. Refresh restores ToolRuns/research/approvals without live re-animation  
6. Utility time/calc never research-partial  
7. One Search-done row for single-intent FIFA/Pokémon style query  
8. “okay do that” / “double check that” bind durable offer/claim  
9. Explicit `index.html` edit does **not** write `game.js`  
10. Pending approval survives Studio open + capabilities refresh on same Session  
11. Session switch aborts previous stream application  

### K-video — Video Editor foundation browser pass (before any new video feature)

**Do not implement playback/proxies/export/generation until this is green.**

1. Sidebar does **not** create phantom Sessions on ordinary navigation  
2. Create one real Session; attach a **disposable** Project only  
3. Open `/app/video` — no silent Session or video document creation  
4. Explicitly create a video document  
5. Import a tiny synthetic MP4 under the Project; metadata appears in media bin  
6. Add a track; insert the clip manually  
7. Split, trim, move, delete, undo, redo each advance revision correctly  
8. Ask Echo / use proposal path for one of those ops; **confirm** → exactly one new revision + parent `video_apply_transaction` ToolRun (+ child op runs)  
9. Refresh: document, timeline, revisions, history hydrate; no live re-animation of tools  
10. No real user footage / unrelated Project is touched  

**Automated proof today (not a substitute for K-video):**  
`tests/test_video_editor_foundation.py` (store/ops/approvals path), frontend
`features/video-editor/types.test.ts` (rational time helpers). Generation
adapters remain declarations only (`agent/video_editor/adapters.py`).

---

## L. Code map (pointer)

| Area | Primary location |
|------|------------------|
| Equal model profile | `agent/model_runtime.py` |
| Tool calling allow | `core.py` `_allow_llm_tool_calling` |
| Capabilities Session bind | `api/server.py` `/capabilities`, `_apply_thread_scope` |
| Project clear/activate | `core.py` `_clear_session_project_scope`, `activate_project` |
| Timeline hydrate | `state.py` `session_timeline`, `turn_projection`; `GET /history` |
| Utility mode | `mode_controller.py` utility branch |
| Offered action / claims | `core.py` offered-action + `_remember_assistant_factual_claim` |
| Placeholders / list→read | `TaskPlanner` inject/validate in `core.py` |
| Explicit files / gaps | bounded named-file path in `_pq_parse_and_preempt`; `_files_relevant_to_request`, gap analysis |
| Coding readiness | `agent/coding_readiness.py`; `/coding/readiness`; query preflight |
| Approval match / source precondition | `_pending_action_matches_execution_context`, `_capture_source_precondition` |
| Process lock | `state.py` `_acquire_phase3_process_lock` |
| turn_bound | `process_query` stream event |

---

## M. How other docs should refer here

| Doc | Allowed |
|-----|---------|
| This file + `LIFECYCLE_TRUTHFULNESS.md` | Full contracts |
| CHANGES / ROADMAP / AUDIT | Status line + link; no “closed” until §K |
| MODE_SYSTEM, THREAD_*, AGENT, UI map, SEARCH, GETTING_STARTED, coding WORKSPACE | Short pointer |
| Skills | Unchanged unless skill-specific |
