# EchoSpeak Runtime Contracts

This document defines production runtime boundaries. `SYSTEM_ARCHITECTURE.md`
maps modules; `LIFECYCLE_TRUTHFULNESS.md` defines user-facing execution honesty.

## Model access

The provider/model selected for the active Session is the default for Chat,
Research, Coding, memory curation, skills, and Automations. A domain may not
silently launch a dedicated secondary model. An alternate provider is allowed
only when explicitly selected or when a visible, governed hard-capability
fallback records the reason.

Provider readiness affects model work only. It does not hide or invalidate
Projects, Sessions, Tasks, Runs, approvals, memory, documents, or artifacts.

## Project, Session, and mode

- `ProjectManager` owns Project identity and trusted roots.
- `ThreadSessionState` owns the current Session attachment and execution context.
- A Project is never inferred from Desktop, current working directory, or a
  previous shared-agent state.
- Mode selects a least-privilege tool inventory; it does not grant permission.
- Coding requires the Session's Project ID and root to match `ProjectManager`.
- Switching or detaching Project invalidates stale pending actions and scoped
  continuity projections.

## Capability inventory

| Surface | Owner |
|---|---|
| Tool registration, provenance, and policy metadata | `ToolRegistry` |
| Skill package status and declared requirements | `SkillsRegistry` |
| Session Project/root | `ThreadSessionState` + `ProjectManager` |
| Current permissions/configuration | runtime config and Session policy |
| Coding readiness | intersection report from current owners |
| Connections | `ConnectionRegistry` narrow capabilities and health |
| Media/generation | domain adapter projection; verified assets register in `MediaLibraryStore` |

Prompts, `TOOLS.txt`, UI switches, model prose, and cached readiness reports do
not register or authorize a capability.

## Execution identity and ToolRuns

One logical operation maps to one canonical Execution identity and one
canonical ToolRun. Retries link to the original identity and may create a new
attempt record only when the lifecycle explicitly requires it. Callback queues,
stream items, activity cards, and scheduler status are projections.

Terminal success requires structured tool output and verification. Interrupted,
partial, missing, or prose-only evidence is never upgraded to success.

## Approval consumption

Approval is a consent record, not durable permission. Every consumption entry
point follows this order:

1. Match stable action identity: tool, normalized arguments, target, owner,
   Project, Session, and originating Execution.
2. Load the current authoritative ApprovalRecord.
3. Revalidate current policy and Session permissions.
4. Revalidate current Project identity, trusted root, and path constraints.
5. Revalidate current source/destination mutation preconditions and revisions.
6. Revalidate current ToolRegistry executable inventory and configuration.
7. Revalidate expiry, cancellation, and consumed state.
8. Atomically claim, execute one ToolRun, verify, and terminalize.

Delete, move, rename, copy destinations, patching, repository changes, and other
destructive filesystem operations require appropriate source and destination
preconditions, not only `file_write`.

Retry identity excludes mutable policy/permission snapshots. Those are always
read fresh after the stable identity match.

## Authoritative JSON and recovery

Canonical stores write atomically and carry schema/version metadata where
applicable. Malformed state fails closed. The original bytes are preserved in a
quarantine/recovery directory with a clear diagnostic and manual recovery
instructions. The runtime must never silently replace malformed authority with
an empty successful store.

## Research and live retrieval

`SearchGrounder` is the sole web-search orchestrator. Provider adapters in
`web_search_providers.py` return a normalized discovery shape; snippets are not
final evidence for exact current facts. `LiveRetrievalRouter` classifies sports,
finance, weather, flights, schedules, and other freshness-sensitive requests
for typed providers or honest unavailable/fallback results.

`ResearchArtifactStore` persists exact Project/Session/model scope, budgets,
plan, branches, sources, evidence, claims, contradictions, gaps, freshness,
verification, and synthesis. Artifact read, reuse, and consumption revalidate
that exact scope.

## Memory and documents

`MemoryCurator` is the only durable semantic-memory writer. Records carry owner,
Project, Session, class, provenance, lifecycle, timestamps, confidence,
sensitivity, revision, and checksum. Scope filtering occurs before deduplication,
supersession, lexical/semantic ranking, compaction, edit, or deletion.

Document chunks and embeddings carry Project/Session, source revision, checksum,
embedding model/version, domain, sensitivity, and freshness. Graph expansion
cannot reintroduce a chunk excluded by scope.

FAISS, keyword indexes, profiles, summaries, and Obsidian Markdown are
rebuildable projections. Obsidian changes require an explicit deterministic plan
and conflict-aware apply; the vault is never canonical memory.

## Coding

`ThreadSessionState` owns the objective and active Project. The
`CodingExecutionLedger` owns durable phase/checkpoint/evidence/revision history.
`ActiveWorkStore` is a scoped continuity hint only and cannot attach or restore a
Project.

Before mutation the coding path establishes exact target/exclusions, inspected
revision, expected consequence, permission, approval need, and verification.
Resume rejects mismatched Session, Project/root, source revision, or ledger
revision.

## Automations

`RoutineManager` owns reusable definitions, `TaskStore` owns finite work, and
`AutomationRunStore` owns execution history, leases, checkpoints, and recovery.
The API runtime owns one scheduler lifecycle.

Heartbeat may evaluate triggers, claim/reclaim Runs, detect stuck work, and
report health. It cannot send email, WhatsApp, Discord, or other external
messages directly and cannot mutate user resources. External delivery is a
governed tool action inside a bound Turn/Execution.

One exact-scope trigger/idempotency key creates at most one Run. Run planning
uses the active Session model. Approval pause/resume, retries, cancellation,
lease expiry, and restart recovery preserve the same canonical identity without
replaying completed mutations.

## Connections

`ConnectionRegistry` persists secret-free identity, scope, capabilities,
authentication health, last failure, and revocation/enable state. Secrets stay
in the provider's secure credential mechanism. A Connection cannot own Tasks,
Routines, Runs, ToolRuns, or completion and may not expose unrestricted shell or
silently broaden filesystem/network scope.

## API and desktop transport

Desktop transport binds loopback and uses the authenticated bootstrap contract.
Non-loopback API binding requires authentication; an unauthenticated remote bind
fails closed. Host-terminal execution is not the default merely because terminal
actions are enabled; terminal backends must be explicitly configured and remain
Project/approval constrained.

## Frontend projection

Chat shows conversation plus one transient active status. It retains actionable
approval, clarification, conflict, and recoverable failure UI. Detailed
ToolRuns, evidence, research branches, tasks/runs, provenance, and health remain
in Studio and Viewer.

Studio navigation must remain accessible under narrow windows, maximized/full
screen, and DPI scaling through controlled overflow, keyboard navigation, and
correct content offsets. Opening or switching a workspace does not create a
Session; only the explicit plus/new-Session action does.

## Acceptance gates

Release claims require, with disposable data:

1. Focused and full backend regression.
2. Web typecheck, component tests, and production build.
3. Desktop contract tests, Rust format/check/test/Clippy, and Tauri build.
4. Sidecar and installer builds when the required toolchains are available.
5. Native launch, startup recovery, workspace switching, Chat input/send,
   Session continuity, and responsive Studio navigation.
6. Live configured-model checks for Research routing, memory recall/isolation,
   coding continuation, tool selection, automation planning, and honest blocks.

Environment-gated or manual checks must be reported as not run; they cannot be
inferred from compilation or earlier results.
