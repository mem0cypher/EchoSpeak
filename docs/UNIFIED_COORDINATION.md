# Unified Coordination Layer

**Status:** implemented platform contract.  
**Related:** `SYSTEM_ARCHITECTURE.md`, `ECHO_CORE_CONTRACT_MATRIX.md`,
`RUNTIME_CONTRACTS.md`, and `LIFECYCLE_TRUTHFULNESS.md`.

## Principle

Every subsystem plugs into the same identity, authority, execution,
verification, recovery, and projection spine. Specialized planners and domain
adapters are allowed; separate definitions of completion, permission, retry,
or truth are not.

```text
User or trigger
  -> exact Session and Project
  -> objective and capability resolution
  -> structured plan
  -> current policy / permission / revision checks
  -> one Execution and canonical ToolRuns
  -> verification and durable domain state
  -> Studio/Viewer/Chat projection
  -> final response from recorded truth
```

## One owner per concept

| Concept | Authority | Projections only |
|---|---|---|
| Project attachment/root | `ThreadSessionState` + `ProjectManager` | sidebar, workspace chrome |
| Session identity/current execution | `StateStore` | frontend stores and streams |
| Pending mutation | `ApprovalRecord` | pending-action response and approval card |
| ToolRun outcome | durable `StateStore` ToolRun | callback queues, activity cards |
| Personal semantic memory | `MemoryCurator` / `AgentMemory` | indexes, profiles, Obsidian, Memory Studio |
| Coding objective and checkpoints | `ThreadSessionState` + `CodingExecutionLedger` | ActiveWork and Code UI |
| Research evidence | `ResearchArtifactStore` | Research workspace |
| Task/Routine definition | `TaskStore` / `RoutineManager` | Automations cards |
| Automation execution | `AutomationRunStore` | scheduler callbacks and health |
| Connection capability | `ConnectionRegistry` | Connections UI |
| Media asset | `MediaLibraryStore` | Media cards and generation results |

If two modules can change the meaning or lifecycle of one row, that is an
ownership defect.

## Capability and skill rules

`ToolRegistry` owns executable tool registration and policy metadata.
`SkillsRegistry` owns skill package status and declared requirements. Session
scope, Project roots, configuration, and current policy reduce those
inventories; UI visibility or model prose cannot expand them.

A skill may select, plan, and structure inputs. It must declare required tools,
permissions, artifacts, verification, and failure behavior. Draft or disabled
skills are never imported as executable code. Generated or installed skills
cannot replace a canonical registered tool.

## Mutation and approval rules

Stable action identity matches the approved action without freezing mutable
authority. Every approval-consumption path then revalidates current policy,
permissions, Project/root, source revision, path constraints, tool inventory,
configuration, expiry, and consumed state before atomic claim.

Retry preserves stable action identity but obtains fresh authority. It does not
reuse an old permission or policy snapshot as authorization.

## Background work

Heartbeat and the Routine scheduler may discover, claim, recover, and report
work. They cannot perform external delivery or user-data mutation directly.
One trigger maps to at most one Task and AutomationRun by exact-scope
idempotency. The Run binds the active Session model and enters the normal Turn,
approval, ToolRun, checkpoint, and verification boundary.

Callback queues, progress cards, and scheduler status are projections of the
Run. They never own completion.

## Models and retrieval

All model-driven domains use the active Session provider/model by default.
Explicit alternate-model selection must be visible and governed.

Retrieval resolves domain and scope first. Exact live data uses structured
providers; Project files, documents, memory, and artifacts use filtered hybrid
retrieval. Embeddings are rebuildable indexes, not authority for current
prices, scores, flights, weather, schedules, or other fast-changing facts.

## Frontend truth

Chat stays conversational: normal completion shows the user message and Echo's
synthesis, with one transient status while active. Approval, clarification,
conflict, and recoverable failure remain actionable. Studio and Viewer expose
durable evidence, ToolRuns, tasks/runs, research, memory provenance,
connections, failures, health, and verification.

The frontend must not infer completion from prose, a stopped spinner, an
optimistic flag, or a nearby card. It refreshes from canonical backend owners.

## Extension checklist

A new subsystem must define:

1. Exact identity and Project/Session scope.
2. One durable owner and versioned schema.
3. Registered tools and narrow Connection capabilities.
4. Approval and source-revision behavior for mutations.
5. Structured outcomes and independent verification.
6. Checkpoint, retry, cancellation, and restart recovery.
7. Corrupt-state quarantine and manual recovery diagnostics.
8. A projection-only UI and disposable-data tests.

No extension may add unrestricted shell, silent authority expansion, a second
ToolRun system, or a prose-based completion path.
