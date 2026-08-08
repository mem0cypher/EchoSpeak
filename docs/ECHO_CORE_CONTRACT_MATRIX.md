# Echo Core Contract Matrix

This matrix is the canonical ownership map for EchoSpeak 8.0. Chat, Visualizer,
and Settings are projections or controls over these owners; none is an
execution engine or completion authority.

| Domain | Sole durable owner | Runtime truth | Projection |
|---|---|---|---|
| Project | `ProjectManager` | Project identity, trusted root, resources, and Project configuration | Sidebar, Settings, Visualizer |
| Session | `ThreadSessionState` plus `ThreadManager` metadata | Conversation identity, attached Project, exact model binding, contextual bindings, and preferences | Sidebar and Chat |
| Turn / Execution | `StateStore` `ExecutionRecord` | One request attempt and its durable lifecycle | Chat activity and Visualizer history |
| Actionable objective | `TaskRunStore` `TaskRun` | Objective, requirements, execution graph, attempts, waits, evidence references, specialist bindings, and completion state | Compact Chat card and Visualizer |
| Execution graph | The owning `TaskRun` | Runtime-authored nodes, dependencies, joins, retry eligibility, and the single finalization node | Adaptive Visualizer graph |
| Tool execution | Durable `ToolRun` / `ToolOutcome` records in `StateStore` | One authorized bounded capability attempt and its verified outcome | Chat status and Visualizer detail |
| Approval | `ApprovalRecord` in `StateStore` | Stable action identity plus immutable TaskRun/requirement/attempt lineage | Exact approval card |
| Specialist delegation | `SpecialistRunStore` `SpecialistRun` | Echo-level correlation to one Codex or OpenCode runtime/session/turn | Specialist panel in Visualizer |
| Research evidence | `ResearchArtifactStore`, referenced by `TaskRun` | Provenance, timestamps, field coverage, contradictions, and evidence state | Structured Chat results and Visualizer research |
| Research sufficiency | `RequirementCompletionEvaluator` updating the owning `TaskRun` | Whether every required field is satisfied or has an allowed terminal partial state | Completion summary |
| Final response | Existing `ModelExecutionControlPlane` validation gate | The only production gate that may accept an actionable TaskRun answer | Chat response |
| Tool inventory | `ToolRegistry` plus current Session/Project/policy reduction | Exact current capability snapshot; visibility never grants authority | Settings and Visualizer capabilities |
| Memory | `MemoryCurator` canonical records; indexes are rebuildable projections | Personal facts, preferences, decisions, and scoped relevant history | Chat context and Settings |
| Connection | `ConnectionRegistry` and external MCP/provider owners | Narrow availability, health, scope, and credential-free metadata | Settings and Visualizer |
| Routine occurrence | `AutomationRunStore` | Lease, occurrence identity, linked Execution/TaskRun, and delivery state | Visualizer runs/schedules |
| Media asset/job | Existing generation, voice, and media stores | Verified job lineage and registered artifacts | Visualizer media panel |
| UI | Backend read models keyed by Session and Project | No independent lifecycle or completion state | Chat, Visualizer, Settings |

## Cross-domain invariants

1. Casual conversation creates an `ExecutionRecord`, but no `TaskRun`, graph,
   requirement set, or specialist session.
2. An actionable request receives at most one owning `TaskRun`; its graph is
   embedded state, not a second scheduler or authority.
3. A successful tool process is not automatically a successful semantic
   requirement. Explicit verification and field coverage are required.
4. A specialist terminal event updates only its exact TaskRun requirement and
   graph node, then schedules canonical Echo continuation. It never writes a
   final user response directly.
5. Approval identity is matched first. Consumption then revalidates the current
   policy, permissions, Session, Project/root, model binding, path
   preconditions, tool inventory, configuration, and TaskRun lineage before an
   atomic claim.
6. External content and MCP results are untrusted evidence. They may influence
   reasoning but cannot expand permissions or authorize side effects.
7. Failed provider/model/parse work remains recoverable only through explicit
   TaskRun statuses; impossible or corrupt lifecycle state is quarantined and
   excluded from continuation candidates.
8. Session selection, Project navigation, view switching, streaming replies,
   and model responses never create Sessions. Only explicit `+` controls do.
9. Exact Session-selected model/provider identity is revalidated for each Turn;
   there is no hidden provider, model, Project, capability, or specialist
   fallback.

## Compatibility boundary

Historical persisted records are read or migrated into these contracts without
deleting user data. Legacy `ActiveWork`, pending-action, callback, and old plan
records may be read for noncanonical migration/diagnostics, but the canonical
semantic runtime does not permit them to restore authority, execute work, or
decide completion.

The detailed production flow is in
[`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md); execution and approval
invariants are in [`RUNTIME_CONTRACTS.md`](RUNTIME_CONTRACTS.md).
