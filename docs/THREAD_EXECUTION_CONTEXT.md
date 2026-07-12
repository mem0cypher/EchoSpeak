# Thread Execution Context

EchoSpeak uses `agent.state.ThreadSessionState` as the durable execution context
for one conversation thread. It is not a second state store. `ModeDecision`,
session memory, and `ActiveWorkState` feed the context at turn start; the state
store persists the reconciled result.

## Canonical runtime / lifecycle rules

→ **`docs/RUNTIME_CONTRACTS.md`** (Project scope, hydration, approval identity, Known limitations)  
→ **`docs/LIFECYCLE_TRUTHFULNESS.md`** (recovery, confirm, ToolRun truth, projection, corruption)

v7.6.10: **implemented (partial); pending live validation**.

This file covers **schema and isolation** only — not a parallel contracts manual.

## Lifecycle (bind → execute → finalize)

1. Restore thread state, session subject, active work, pending approval, and
   `pending_offered_action`.
2. Bind request-local tool context (thread id + filesystem roots).
3. Classify once (`ModeDecision` + `intent_relation`). Resolve confirm/cancel
   against durable targets per lifecycle §4 (not free-form “yes = write”).
4. Create the Turn/execution; re-attach unfinished work only for
   `continue` / `confirm` / `retry` as specified in the lifecycle doc.
5. Compile model context in trust order (system → authority → objective →
   memory/ledger → untrusted content → request).
6. Execute tools; path roots and mutator approvals apply.
7. Record durable ToolRuns with stable ids (lifecycle §5).
8. Finalize from ToolRuns + projection (lifecycle §6); honesty gates
   (lifecycle §8); release request-local context.

## Durable schema (fields)

The context owns:

- thread, workspace, project id, roots/paths
- objective, subject, mode, coding phase
- capabilities, allowed tools, permission snapshot
- decisions, constraints, completed/pending/failed actions
- pending approval, current execution id, execution status, safest next action
- `pending_offered_action` (type A prepare go-ahead — see lifecycle §4)
- unfinished workflow / retry target
- last assistant factual claim (referential research)
- capped project ledger (verified events, provenance, unresolved)

**Status values and work-state mapping** (inspection vs awaiting confirmation vs
needs_permission vs complete): **lifecycle §6 only** — do not invent a second
table here.

## Isolation rules

- Relative paths resolve against the request-local project root.
- Bound project/workspace roots do not grant parent/sibling projects.
- Pending approvals carry a scope snapshot; cancel if project/workspace changes
  before type B confirmation.
- Checkpoints, conversation, session memory, active work, executions, and ledger
  are keyed by thread (and project where applicable).

## Authority ownership

`ThreadSessionState` is operational authority for project, objective, subject,
mode, phase, constraints, pending approval, retry target, and execution status.
Execution records own immutable per-request history; the ledger owns verified
events; active-work is resumable progress only; frontend mirrors backend.

Mutating/external work must enter `EchoSpeakAgent._invoke_authorized_raw_tool`
(including MCP/plugin actions and checkpoint undo). Pipeline plugins that
declare external mutation must be rejected at registration.

Internal search-provider calls stay read-only evidence under research mode.
Admin API routes are not agent tools.

Modifying approvals use strict snapshot semantics (action id, plan id, canonical
args, scope, permissions). Material change requires a new approval.
