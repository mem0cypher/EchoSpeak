# Lifecycle Truthfulness & Execution Honesty

**Sister index:** **`docs/RUNTIME_CONTRACTS.md`** owns equal model access, Mode vs
Project vs permissions, Project/Code lifecycle, refresh hydration, search/utility
references, coding target selection, streaming/concurrency, approval-scope
identity, and **Known limitations**. Do not bury those topics under “general
truthfulness.”

**This file is the canonical source of truth only for:**

| Topic | Section | Contract id |
|-------|---------|-------------|
| Recovery evidence | §3 | **I.1** (also indexed in Runtime Contracts) |
| Confirmation vs write approval | §4 | **I.2** proposal / types A–D |
| ToolRun identity (user-facing truth) | §5 | — |
| Projection-based completion & status language | §6 | **I.3** incomplete evidence |
| Corrupted-file handling | §7 | — |
| Truthful final responses | §8 | — |

**Do not restate these tables in full elsewhere.** Other docs summarize and link.
Future edits to these six topics land **here first**.

---

## Status of this wave (v7.6.10 + production-closure)

| Layer | State |
|-------|--------|
| Design (this document + Runtime Contracts) | **Authoritative for contracts** |
| Code paths in worktree | **Implemented for high-risk backend paths** — named-file pin, approval freeze metadata vs content-identity compare, video propose→apply ToolRuns, durable approval re-load, research artifacts, skill executable audit, orchestrator gated off by default |
| Backend regression + disposable restart soak | **Exercised** (`test_production_closure*`, `test_coding_fixture_workflow`, `scripts/_restart_soak_once.py`) |
| Live API + live model (LM Studio) | **Exercised** against disposable `ECHOSPEAK_DATA_DIR` (`scripts/live_api_acceptance.py`, `scripts/live_gap_closure.py`) — chat stream, coding, ToolRuns list, video selection fail-closed, video propose/apply, memory save/correct/forget, research artifact consume, skills truth |
| Live Playwright browser | **Exercised** Chromium against `/app` and `/app/video` (`scripts/live_browser_acceptance.py`, `scripts/live_browser_concurrency.py`) — no phantom Session, dual tabs, simultaneous send, refresh |
| Full process kill/restart | **Exercised** (`scripts/live_process_restart.py`) — taskkill backend PID, respawn, pending approval survives, confirm once, duplicate 409, no pre-confirm mutation |
| Canonical ToolRuns API | **`GET /tool-runs`**, **`GET /executions/{id}/tool-runs`** — Session/Execution/Project hydration with parent/child (`retry_of`), approval_id, verification |
| Deterministic video proposals | **`agent/video_editor/deterministic_ops.py`** — proposal-only for unambiguous split/delete/volume when selection+playhead present; never fakes model tool calls |
| Stream reorder guards | Stream events carry monotonic **`seq`**; frontend ignores `seq <= maxSeen` for the active request |
| FAISS forget/rebuild | **`POST /memory/rebuild-index`** rebuilds from **active** canonical records only; retrieval drops inactive vectors |

**Release language rule:** Do **not** describe the product as fully “closed” until
live browser multi-tab/refresh gates pass. Prefer:

> **High-risk production paths hardened and regression-tested; browser UI acceptance still pending where not explicitly run.**

Architecture text describes **intended + coded** behavior. Backend suite pass is
not a substitute for browser multi-tab proof.

---

## 1. Problem class (why this exists)

Live sessions showed **prose and UI status drifting from execution reality**:

| Symptom | What tools actually did | False claim |
|---------|-------------------------|-------------|
| Recovery | Only Project `file_list` + `file_read` | “Searched checkpoints, backups, autosaves, Previous Versions — none exist” |
| Size | Read ≈ 3379 characters | “exactly 3016 bytes” |
| Confirm | User said `yes` / `yes proceed…` | “What about it?” then hollow “Proceeding.” with no write ToolRun |
| Duplicates | Preflight + planner both listed/read | Twin `file_list` / `file_read` rows |
| Corruption | File already had SEARCH/REPLACE chrome | Further “feature work” on dirty content |
| Completion | Placeholder reads failed | “Six of six complete; full understanding” |
| Search | Multi wrapper + thinking chrome | Multiple “Search done” rows for one intent |

**Non-goals of the repair wave:** UI redesign; loosening Project authority;
changing model capability product surface; editing the user’s
`Desktop/2d-shooter-game` during EchoSpeak repairs.

---

## 2. Design principles

1. **Evidence before claim.** Final prose may assert a tool or recovery source
   only when the **current Turn** has matching ToolRun evidence (or an explicit
   `unavailable` / `permission_required` status for that source).
2. **`not_checked` is never “not found”.** Silence is not absence.
3. **One user-facing ToolRun row** per canonical tool identity (stream start/end
   id = durable ToolRun id = hydrate id).
4. **Silent preflight** is internal context only — not a second UI event.
5. **Approvals are hard gates.** An exact mutation needs a durable pending
   approval (or authorized approved action), not chat prose alone.
6. **A plain “yes” is never universal write approval.** It only binds to an
   existing durable target (see §4).
7. **Projection owns completion.** Checklist and execution status come from
   ToolRuns + plan projection, not free-form “all done”.
8. **Equal functional access across models.** Profiles are observability;
   authority is path, policy, and approval.
9. **Memory acknowledgements require durable identity.** Recent conversation,
   Session summaries, profile prompts, and vector hits cannot justify “saved.”
   Durable writes go through **MemoryCurator** (LLM semantic rewrite + runtime
   validate + confirm). Raw `curated_lines_from_text` / post-curator
   `add_memory_item` bypasses are **forbidden**.
10. **Failed mutations are attempts, not changes.** Only successful mutating
    ToolRuns populate changed-file projections or saved-content UI.

---

## 3. Recovery evidence (canonical contract I.1)

**Separate contract** — not a soft “be honest” guideline. Live logs still
showed recovery claims without corresponding ToolRuns; treat as regression-prone
until §11 L1 passes.

### 3.1 Per-source status values (preserve exactly)

Every recovery source on a Turn is one of:

| Status | Meaning |
|--------|---------|
| `checked_found` | ToolRun ran for this source and a candidate/copy was found |
| `checked_not_found` | ToolRun ran for this source and no candidate was found |
| `not_checked` | No ToolRun for this source this Turn — **do not report as not found** |
| `unavailable` | Source exists in product intent but is not available (e.g. not installed) |
| `permission_required` | Source could be checked but policy/OS permission blocked the ToolRun |

**Forbidden:** converting `not_checked` → “no recovery copy exists” / “no
candidates found”.

### 3.2 Source keys

| Source key | Meaning | Becomes checked only via |
|------------|---------|---------------------------|
| `echospeak_checkpoints` | EchoSpeak checkpoint store | checkpoint / related ToolRun |
| `echospeak_undo` | Undo records | same family |
| `pre_write_snapshots` | Pre-write snapshots | same family |
| `project_local_backups` | `*.bak` / local copies | dedicated search, not mere source file_list |
| `editor_autosaves` | Editor autosave | dedicated tool evidence |
| `temporary_files` | OS temp copies | e.g. terminal search ToolRun |
| `windows_previous_versions` | Shadow Copy / File History | dedicated recovery tool only — **never** inferred from Project list/read |
| `user_provided_backups` | User-supplied backup | user content this Session |
| `reconstruction_from_current_source` | Rebuild from live file | successful `file_read` of that file |
| `project_file_read` | Live body observed | successful `file_read` |

### 3.3 Project-only inspection (truthful template)

If Echo only lists/reads the Project folder:

> I inspected the Project files (…exact sizes from `file_read`…), but I did not
> have evidence from EchoSpeak checkpoints, Windows recovery history, editor
> autosaves, or external backups, so I **cannot conclude that no recovery copy
> exists**.

### 3.4 Size and provenance

From successful `file_read` ToolRuns this Turn only:

- character count (body or declared `Read N chars`)
- `provenance: current_project_file_read`
- `corrupted_markers` when marker chrome is present

Invented exact-byte claims are rewritten to the observed size. No size claim
without a matching observation.

### 3.5 Implementation note

Code: `_ensure_recovery_claim_honesty`, `_file_read_observations_this_turn` in
`agent/core.py`. **Implemented; pending live validation** of full recovery
phrasing on Web UI.

---

## 4. Confirmation versus write approval (canonical contract I.2)

**Separate contract** for weak proposal confirmation. Live logs still showed
hollow “Proceeding.” / failed resume — unit helpers do not close type A→B.

These are **four different user intents**. Docs and code must not collapse them.

| Intent | Meaning | Durable target required | Typical phrases (examples) |
|--------|---------|-------------------------|----------------------------|
| **A. Prepare / offer go-ahead** | User agrees Echo may **prepare** or continue planning/implementing a **proposal** (diff, plan) — not yet execute a write | `pending_offered_action` (`coding_edit` / `web_research`) with subject | “Do you want me to proceed with this change?” → `yes` / `yes proceed with the changes` |
| **B. Approve exact mutation** | User authorizes **this** tool call with schema-canonical kwargs | `ApprovalRecord` + `_pending_action` (path, content, tool) | `confirm` after “Reply confirm to save…”; Approve in Approval Center |
| **C. Continue unfinished work** | Resume preserved plan/workflow without new objective | `unfinished_workflow` / active work + `intent_relation=continue` | `continue`, `keep going`, `where were we` |
| **D. Retry failed ToolRun** | Re-attempt the same failed step | `retry_target` / failed step + `intent_relation=retry` | `retry`, `try again` |

### 4.1 Plain “yes” is not universal write approval

- **Yes alone never means “write whatever you want.”**
- **Yes** only resolves if there is a still-valid durable target:
  - (B) pending approval with exact kwargs, **or**
  - (A) awaiting offered action with a stored subject.
- If **neither** exists, the honest response is: nothing durable to apply /
  prepare; restate file + intent. **Never** hollow “Proceeding.”
- Accepting (A) may **start prepare/implement toward a proposal**. It still
  does **not** skip (B) for web/UI mutators: the eventual `file_write` must
  create or hit a pending approval unless auto-confirm policy explicitly allows.
- A type-B filesystem approval captures every relevant source and destination
  version (write/patch, delete, move, copy, mkdir, artifact replacement, and
  checkpoint undo). If one changes before confirmation, the stale action is
  blocked and must be prepared again from current state.

### 4.2 What each store does

| Store | Field | On valid confirm |
|-------|-------|------------------|
| Write approval | `ApprovalRecord`, `_pending_action` | Execute **that** tool with stored kwargs |
| Offered action | `pending_offered_action` | Resume stored **subject** (research or coding plan), status → consuming → consumed |
| Unfinished plan | `unfinished_workflow` | Resume remaining tasks (`continue` / sometimes `confirm` when relation matches) |
| Retry | `retry_target` | Re-run failed step |

### 4.3 Offered coding action shape

```json
{
  "kind": "offered_action",
  "action": "coding_edit",
  "subject": "<user goal + optional file>",
  "target_file": "game.js",
  "status": "awaiting_user_confirmation",
  "origin_execution_id": "<prior turn>"
}
```

### 4.4 Intent relation (routing)

`new_objective` | `continue` | `confirm` | `retry` | `cancel`

- `confirm` phrases are matched by `_intent_relation` / `_is_confirm_text`.
- On an **active project**, `confirm` may select coding phase **IMPLEMENT** so
  routing does not stall as inspect-only — that still requires a durable target
  before any write.
- `new_objective` supersedes stale approvals and unfinished work; unrelated pins
  must not resume (todo vs shooter).

### 4.5 Mutation prose gate

Without a successful mutator ToolRun this Turn (and without a pending approval
that is only “proposed, not applied”), rewrite:

- hollow `Proceeding.` / `On it.`
- “I’ve updated / fixed / wrote…”

### 4.6 Implementation note

**Implemented** paths: offered-action extract/resolve, pure-confirm refusal,
mutation honesty, Stage 1 approval confirm, and filesystem version preconditions.
**Pending live validation** of the full A→prepare→B→write sequence and stale-source
blocking on Web UI.

---

## 5. ToolRun identity (canonical)

### 5.1 One id

For user-facing tools (`web_search`, `file_list`, `file_read`, …):

- LangChain / stream `tool_start` id  
- `_emit_tool_start` / `_emit_tool_end`  
- durable `ToolRun` in `StateStore`  
- chat timeline row  
- history hydration  

must be the **same** id. Do not invent a second row by query-text dedupe alone.

### 5.2 Search

One logical search intent → one canonical visible ToolRun (+ evidence + one
final answer). Provider fan-out stays under that id or diagnostic-only.
Provisional `thinking_step` chrome must not complete or replace real ToolRun
UUIDs.

### 5.3 Files: silent preflight

`_preflight_list_local_project` / `_preflight_sample_read_local_project` default
to `emit_tool_events=False` and tag partials `silent_preflight=True`.
User-facing list/read rows belong to planner / force-read / implement only.

### 5.4 Implementation note

**Implemented** in stream/store/UI paths; **pending live validation** that
duplicate rows no longer appear after full-folder scans in production UI.

---

## 6. Projection-based completion & status language (canonical contract I.3)

**Incomplete evidence tracking** is its own contract: every class of claim
requires matching ToolRun evidence this Turn (see also Runtime Contracts §I.3).

### 6.1 Projection rules

`TaskPlanner.build_execution_projection`:

- Only **successful** `file_read` counts as “file contents inspected”.
- Listing alone ≠ understanding the codebase.
- Unresolved templates (`{{first_relevant_file}}`, `${…}`) fail or are dropped.
- Final prose must not say “all tasks complete” when projection disagrees.

### 6.2 Consistent status mapping (document this mapping only here)

Use one vocabulary for docs, UI copy, and execution finalize:

| Work state | Documented status | Notes |
|------------|-------------------|--------|
| Inspection only (list/read, no mutation pending or done) | `in_progress` or `partially_complete` | Prefer `partially_complete` if user asked for full understand/scan and required reads remain |
| Proposal / offer awaiting user go-ahead (type A) | `awaiting_confirmation` | Offered action or plan approval language — **not** write yet |
| Exact mutation awaiting approval (type B) | `needs_permission` | Durable `ApprovalRecord` |
| Write complete, verification not done | `verifying` or `partially_complete` | Prefer `partially_complete` if `verifying` is not yet a first-class store enum |
| All required work ToolRun-verified | `complete` | Only when projection + verification agree |

Other durable statuses remain: `ready`, `needs_clarification`, `blocked`,
`failed`, `cancelled` / `retryable` as already used by thread state.

**Hard rule:** coding implement intent with only reads and no write **and** no
pending approval → **not** `complete`.

### 6.3 Honesty gates on final text

Run on Stage 3 **and** Stage 4 exits:

- capability / mutation / recovery / research evidence honesty  
- grounding guard (unsupported numbers/odds)

### 6.4 Implementation note

Projection + finalize gates **implemented**. Mapping of every UI label to the
table above is **pending live validation** (some surfaces still use older
wording).

---

## 7. Corrupted-file handling (canonical)

### 7.1 What counts as corruption

Unresolved edit / conflict chrome in the file body:

- `<<<<<<< SEARCH` / `>>>>>>> REPLACE`
- generic conflict triples `<<<<<<<` / `=======` / `>>>>>>>`

### 7.2 Effect on development (not only “don’t write markers back”)

Detecting corruption means the file is **not a safe baseline for normal feature
development** until cleaned or restored:

1. **Do not treat the file as a healthy source of truth** for “add move/shoot”,
   large features, or confident “I understand this file” claims.
2. **Block writes** that re-introduce or leave unresolved marker chrome
   (`corrupted_write_content`).
3. **Block destructive “fix” paths** that shrink or further scramble an
   already-corrupted original (suspicious full replace / shrink guards).
4. Prefer: restore from a real recovery source (§3), or explicit user-directed
   clean rewrite after acknowledging corruption — not silent incremental feature
   work on top of conflict markers.
5. Observations set `corrupted_markers=true` and recovery/honesty text must warn.

**Not sufficient:** only refusing to write the same marker text while still
claiming feature work completed on the dirty file.

### 7.3 Implementation note

Marker write block + suspicious shrink **implemented**. Full “pause feature
work and force recovery path” product UX is **implemented in parts; pending
live validation** as an end-to-end operator experience.

---

## 8. Truthful final responses (canonical)

Final assistant text must:

1. Match ToolRuns and projection for this Turn.
2. Use recovery statuses from §3 without inventing checks.
3. Use confirm semantics from §4 (no universal “yes” write).
4. Use status language from §6.
5. Surface corruption from §7 before claiming implementation success.
6. Prefer exact observed sizes and provenance.
7. For research: no confident unsupported numbers; referential “double check
   that” targets the last factual claim when present.

---

## 9. Pipeline placement (reference only)

```
process_query
  ├─ bind mode + resolve offered_action (type A)
  ├─ create execution (re-attach offer / unfinished / retry carefully)
  ├─ Stage 1  pending approval confirm/cancel (type B)
  ├─ Stage 2  context
  ├─ Stage 3  shortcuts (double-check, pure-confirm honesty, coding offer, force read)
  ├─ Stage 4  LLM agents
  ├─ honesty gates (§8)
  └─ Stage 5  finalize + projection status (§6)
```

---

## 10. Automated tests (not a substitute for live)

| Suite | Focus |
|-------|--------|
| `tests/test_recovery_confirm_resume.py` | Confirm phrases, recovery rewrite, Proceeding. honesty, offer extract, markers, size |
| `tests/test_runtime_ownership.py` | Ownership / equal access |
| `tests/test_search_dedup.py` | Search emission |
| `tests/test_grounding_guard.py` | Ungrounded numbers |
| `tests/test_intent_guard.py` | Intent isolation |
| `tests/test_thread_execution_context.py` | Thread / approvals |

```powershell
cd apps/backend
python -m pytest tests/test_recovery_confirm_resume.py tests/test_runtime_ownership.py tests/test_search_dedup.py -q
```

Pass of unit tests = **implemented signal only**, not live acceptance.

---

## 11. Live acceptance checklist (required before “complete”)

Run in **Web UI** with coding flags on and a real Project attached. Mark each
PASS/FAIL; all must PASS before release docs say v7.6.10 is closed.

| # | Scenario | Pass criteria |
|---|----------|---------------|
| L1 | Recovery: list+read only, ask for backups/checkpoints | No claim that external recovery sources were all searched empty; `not_checked` respected; sizes match observations if cited |
| L2 | Type A: offer proceed → user pure `yes` / `yes proceed with the changes` | Resumes offer subject **or** honest “no durable target”; never “What about it?” / empty “Proceeding.”; no write without type B if policy requires it |
| L3 | Type B: pending file_write → `confirm` | Exact kwargs execute once; ToolRun success matches prose |
| L4 | Type C: unfinished plan → `continue` | Resumes remaining steps, not a new objective |
| L5 | Type D: failed required read → `retry` | Retries failed step; status not falsely complete |
| L6 | Full-folder scan | No duplicate preflight+plan list/read rows for same logical work |
| L7 | Corrupted file with SEARCH markers | Feature work does not claim success on dirty baseline; markers not left as “done” content; recovery/clean path surfaced |
| L8 | Failed/unread required reads | Status partial/failed; no “full understanding / all complete” |

---

## 12. Code map

| Area | Location |
|------|----------|
| Confirm phrase routing | `agent/mode_controller.py` |
| Offered actions | `core.py` `_extract_offered_action_*`, `_resolve_offered_action_*` |
| Recovery / mutation honesty | `core.py` `_ensure_recovery_*`, `_ensure_mutation_*` |
| Size observations | `core.py` `_file_read_observations_this_turn` |
| Silent preflight | `core.py` `_preflight_list_*`, `_preflight_sample_*` |
| Projection | `core.py` `TaskPlanner.build_execution_projection` |
| Marker write block | `core.py` `_content_has_unresolved_edit_markers` |
| Pending approval | `core.py` `_set_pending_action`, Stage 1 |
| Thread fields | `agent/state.py` `pending_offered_action` |
| Grounding | `agent/grounding_guard.py` |
| UI provisional rows | `apps/web/src/index.tsx`, `agentActivity.ts` |

---

## 13. How other documents should refer here

| Document role | Allowed content |
|---------------|-----------------|
| This file | Full rules for §3–§8 only |
| `RUNTIME_CONTRACTS.md` | Index + all other contracts; links here for I.1–I.3 |
| CHANGES / ROADMAP / AUDIT | One-line status + link; **no “complete” until §11 + Runtime §K** |
| MODE_SYSTEM, THREAD_*, AGENT, UI map, SEARCH, GETTING_STARTED, coding WORKSPACE, SOUL, TEST_RUNDOWN, INFRASTRUCTURE, ARCHITECTURE | Short pointer + link |
| Skills SKILL.md | Unchanged unless skill-specific |

If a truthfulness rule changes, **edit this file first**. If equal-access,
Project lifecycle, hydration, explicit-file targets, or approval-scope rules
change, **edit `RUNTIME_CONTRACTS.md` first**.
