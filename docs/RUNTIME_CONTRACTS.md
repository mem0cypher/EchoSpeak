# EchoSpeak Runtime Contracts

**Master index of product contracts** for the 2026 lifecycle / reliability wave.

| Document | Owns |
|----------|------|
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

**Implemented** for defaults and turn injection (`runtime_gates: disabled_equal_full_access`). **Pending live validation** across Gemma-local vs hosted Gemini/OpenAI sessions for identical tool reach and plan depth.

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

### Status

**Implemented** in `/capabilities` and tool inventory wiring. **Pending live validation** that CHAT mode + attached Project still shows path-bound file tools correctly.

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

**Implemented** backend timeline + `/history` turns payload. **Pending live validation** of full UI restore (research panel, approvals, files, terminal, no zombie animations).

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

**Live defect (post-doc-pass; contract required):** user asked for work on **`index.html`** but **`game.js`** became the edit target after supporting reads. Treat as **gap until live-fixed** — do not document as shipped-complete. Code has basename preference in `_files_relevant_to_request` / gap analysis for “add a comment to game.js” style asks; multi-file / implement paths still need live proof that supporting reads cannot steal the write target.

### Status

Placeholder reject, list→read inject, projection honesty, marker/suspicious guards: **Implemented**. Explicit-file mutation pinning end-to-end: **Contract required / gap (live bug reported)**.

---

## G. Streaming and concurrency

**Canonical owner: this section.**

### Contract

1. **Client `request_id` ↔ durable Turn / `execution_id` binding** (`turn_bound` stream event) so UI debug and ToolRun rows correlate.
2. **Exact ToolRun identity** from `tool_start` through durable store through history hydrate (lifecycle §5).
3. **Synchronous Session-switch abort** — switching Session must stop applying stream events / tools for the previous Session’s in-flight request (no cross-Session animation or tool completion).
4. **Single-writer process lock** for durable state (`StateStore` phase3 process lock) so two processes do not corrupt phase3 JSON.
5. **Capability / status refreshes must not change another active Session’s** tool scope, pending approval, or project pin. Refresh always keys by `thread_id`.

### Status

**Implemented:** turn_bound, ToolRun ids, process lock, capabilities `thread_id` bind. **Pending live validation:** Session-switch abort under concurrent streams; capability refresh never canceling another Session’s pending approval (see §H).

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

Current `_pending_action_matches_execution_context` also compares **objective**, **constraints**, **session_permissions**, and policy flags. Over-strict comparison can cancel a pending write when unrelated Session metadata or permission snapshot drift occurs (e.g. status refresh side effects).  

**Contract required:** narrow match to stable Project/session/path/args-hash identity; document any intentional invalidation. **Pending live validation** that Studio open + capabilities refresh leave pending approvals intact.

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
| **Duplicate search-stack architecture** | Grounder + Stage 3 + LC tool + thinking chrome can still multi-fire | One initiation owner; lifecycle §5 |
| **Large `core.py` control-plane overlap** | Mode, planner, honesty, search, coding in one module → regressions | Split only after live gates green |
| **Unbound or direct tool bypasses** | Calls outside `_invoke_authorized_raw_tool` skip approval/path policy | Audit remaining invoke sites; plugins must not mutate externally |
| **Exact approval-policy revalidation** | Over-strict snapshots cancel valid approvals; under-strict allows scope drift | §H stable identity; revalidate only material fields |
| **Missing transition tests** | Mode/project/approval transitions not fully covered | Add matrix tests: attach/switch/detach, A/B confirm, Session switch mid-stream |
| **Windows VSS / File History ToolRun** | Recovery correctly stays `not_checked` but product cannot check yet | Optional adapter later |
| **Explicit-file write retarget** | `index.html` request → `game.js` write | §F contract; fix + live test |
| **Equal-access + live multi-provider** | Defaults equal; not fully live-proven | §A live matrix |

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
| Explicit files / gaps | `_files_relevant_to_request`, gap analysis |
| Approval match | `_pending_action_matches_execution_context` |
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
