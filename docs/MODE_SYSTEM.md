# Deterministic Mode System

EchoSpeak routes every turn through a deterministic mode decision before any
model or tool loop can act. The model may reason inside the selected lane, but
it does not invent authority (writes, path roots, or approvals).

## Canonical runtime / lifecycle rules

**Do not maintain a second copy of product contracts here.**

→ **`docs/RUNTIME_CONTRACTS.md`** — equal models; Mode vs Project vs
permissions; Project lifecycle; hydration; search/utility; coding targets;
streaming; approval scope; Known limitations.  
→ **`docs/LIFECYCLE_TRUTHFULNESS.md`** — recovery evidence; confirm types;
ToolRun truth; projection status; corruption; finals.

v7.6.10: **implemented (partial); pending live validation**.

## Source of Truth (mode routing)

- `apps/backend/agent/mode_controller.py` — normalize text; `ModeDecision` with
  `intent_relation` (`new_objective` | `continue` | `confirm` | `retry` |
  `cancel`); coding phase
- `apps/backend/agent/mode_executor.py` — executor profile / logging scope
- `apps/backend/agent/core.py` — bind once per `process_query()`; resolve
  offered actions; enforce path/policy/approval at execution time

### Mode vs tool inventory

Conversation mode is a **routing and verification hint**. Hard stops remain
project/workspace roots, permission flags, approval-gated mutators, and
denylists. Do not empty inventory solely because the turn classified as CHAT.

Compound prompts (research then code) stay coding for write authority while
declaring both capabilities. New intent rules belong in the classifier layer.

## Modes (summary)

| Mode | Purpose | Failure policy (short) |
|------|---------|------------------------|
| **Chat** | Conversation | Concise fallback; no invented tool success |
| **Task Research** | Live/checkable facts | Say what evidence is missing |
| **Coding** | Project work | Phases: inspect → plan → implement → verify → confirm → summarize |

Coding **inspect/plan** are read-only for writes. Advance to implement only with
a real project objective and the confirm semantics in the lifecycle doc (type A
offer vs type B exact mutation approval — a plain `yes` is never universal
write approval).

## Active project continuation

`ActiveWorkStore` (per thread) holds path, phase, goal, known files. Resume only
when the request is about that project; explicit new-project language starts a
new plan. Unrelated pins must not resume.

## Research routing

`intent_domains()` / deep-research flags route to the research model when
appropriate. Referential “double check that” → lifecycle doc + last factual claim.

## Git-backed undo/redo plan

Prefer checkpoint commits + `git revert`, never default `git reset --hard` over
dirty user work. (Orthogonal to recovery-source evidence in the lifecycle doc.)

## Known failure cases (pointers)

Full recovery/confirm/ToolRun failure modes: **`LIFECYCLE_TRUTHFULNESS.md`**.  
Equal models, Project attach, hydration, approval scope: **`RUNTIME_CONTRACTS.md`**.

Also watch: make/build phrasing as chat vs create; LM Studio unload stalls;
settings UI keys drifting from backend config; explicit-file write retarget
(`RUNTIME_CONTRACTS.md` §F).
