# Lifecycle Truthfulness and Execution Honesty

This is the canonical contract for recovery evidence, confirmation versus
approval, ToolRun identity, projection-based completion, corrupt-state handling,
and truthful final responses. `RUNTIME_CONTRACTS.md` owns scope, capability,
and subsystem-specific boundaries.

## Outcome taxonomy

Every evaluated operation uses one of these outcomes:

- `completed_successfully`
- `clarified_correctly`
- `blocked_honestly`
- `failed_safely`
- `failed_incorrectly`

Safe refusal or missing authority is not success. A completed HTTP request is
not necessarily a completed user objective.

## Recovery evidence

Before a consequential mutation, record the target, inspected revision,
expected consequence, exclusions, required capabilities, approval policy, and
verification plan. The runtime may claim that recovery is available only when a
real backup, revision, checkpoint, or reversible domain operation has been
verified.

`not_checked`, `unavailable`, and `unsupported` are truthful states. They must
not be rendered as protected, backed up, or reversible.

## Confirmation and approval

Conversational confirmation is not mutation authority. A durable
`ApprovalRecord` binds stable identity to one action. At consumption time the
runtime reloads that record and freshly validates policy, permissions, Project,
root/path, source and destination versions, tool inventory, configuration,
expiry, and consumed state before atomic claim.

An approval can terminate as confirmed/consumed, cancelled, expired, stale,
blocked, or failed. Repeated confirmation cannot replay a completed mutation.

## ToolRun identity

One logical operation has one canonical ToolRun identity. Planning cards,
callback queues, streaming events, domain records, and UI rows link to it; they
do not create alternate success state.

Retries are explicit attempts linked to the original action. Stable identity is
preserved while mutable authority is revalidated. A retry does not inherit old
permission or policy snapshots.

ToolRun terminal status comes from structured execution and verification:

- `completed`: the action ran and required verification passed.
- `failed`: execution or verification failed.
- `blocked`: required authority/capability was unavailable.
- `cancelled`: the user or runtime cancelled before completion.
- `interrupted`: recovery must reconcile; success is not inferred.

## Completion projection

The final response and frontend status are projections of recorded state. They
must not infer success from model prose, an HTTP 200, a stopped spinner, a
progress card, or optimistic local state.

When evidence is incomplete, the response states what completed, what did not,
why, and the safest next action. It does not collapse partial work into “done.”

Normal Chat intentionally hides persistent internal-operation cards after
completion, but this does not delete evidence. Studio and Viewer continue to
project Executions, ToolRuns, approvals, research sources, tasks/runs,
checkpoints, failures, and verification.

## Corrupt authoritative state

Malformed canonical JSON fails closed. The store must:

1. Preserve the malformed bytes.
2. Copy or move them into a clearly named quarantine/recovery location.
3. Record the parse/storage diagnostic.
4. Provide concise manual recovery instructions.
5. Refuse mutation until authority is restored.

It must never silently initialize an empty successful store over malformed
authority. Rebuildable projections may be discarded and rebuilt only after the
canonical source is valid.

## Background and automation honesty

Heartbeat and schedulers report evaluation, claim, lease, and recovery state;
they do not report external delivery or user-data mutation as complete. Those
effects require a bound Turn, ToolRun, approval when required, and verification.

Exactly-once language is permitted only for the canonical Task/Run identity and
completed mutation record. Provider notification failure is recorded separately
from the work result.

## Research honesty

Search snippets are discovery data, not proof of an exact current fact. Research
answers distinguish structured current values, extracted evidence, synthesis,
contradictions, coverage gaps, freshness, and unavailable fields. A failed live
provider produces an explicit incomplete/blocked result, not a vague confident
answer.

## Memory honesty

Only canonical active records may be described as remembered. Temporary,
pending-confirmation, superseded, disputed, forgotten, or projection-only items
must retain their status. Retrieval explanations may expose source, scope,
freshness, and selection reason without hidden chain-of-thought.

## Coding honesty

Planning, proposing, writing, and verifying are distinct states. A model-created
patch is not an applied change. An applied change is not complete until required
verification runs and its result is recorded. If tests were prohibited or not
run, the final response says so explicitly.

## Final response checklist

Before claiming completion, verify:

1. The requested objective, not merely an intermediate command, completed.
2. The current Project/Session and target identity still match.
3. Each consequential action has a terminal ToolRun and verification evidence.
4. Required approvals were freshly revalidated and consumed once.
5. Partial, blocked, cancelled, interrupted, and notification states are named.
6. Tests, builds, live model checks, and manual/native checks are reported only
   when actually run in the current work.

Architecture text describes coded contracts. It is not a substitute for
focused regression, full regression, native launch, or live-provider evidence.
