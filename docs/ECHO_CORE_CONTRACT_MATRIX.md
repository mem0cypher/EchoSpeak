# Echo Core Contract Matrix

This matrix is the canonical ownership map for the desktop and browser product. UI surfaces are projections; they do not become alternate registries or execution engines.

| Domain | Canonical owner | Identity and scope | Lifecycle / terminal truth | Recovery rule | Projection surfaces |
|---|---|---|---|---|---|
| Project | `ProjectManager` | Project ID; optional trusted workspace root | Project JSON, including archived state | Malformed authoritative JSON fails closed and is copied to `corrupt-state` with `RECOVERY.txt` | Sidebar, Studio, Viewer |
| Session | `ThreadSessionState` in `StateStore` plus `ThreadManager` metadata | Session ID; attached Project ID is Session-owned | Thread state and explicit Session metadata | Malformed registry fails closed; no empty overwrite | Sidebar, Chat, Studio, Viewer |
| Turn / Execution | `StateStore` | Execution ID, request ID, Session ID, Project ID | `completed`, `failed`, `pending_approval`, or explicit interruption | Durable record is reconciled after restart | Chat, Viewer, traces |
| Approval | `ApprovalRecord` in `StateStore` | Stable action identity and approved arguments | Exact identity is matched, then current policy, permission, Project/root, path version, registry/executable inventory, constraints, and configuration are revalidated before atomic claim | Stale/invalid authority blocks; approval is not permission; confirm exposes only the exact action tool and required deterministic verification reads | Chat approval UI, Studio, Viewer |
| Tool | `ToolRegistry` plus the agent execution boundary | Registered tool name and provenance owner | Per-Turn mode inventories are least-privilege reductions of the current registry; an empty Chat inventory never expands to the registry | Dynamic collisions fail closed; skill tools cannot replace registry entries | Studio Tools, Viewer |
| ToolRun | `StateStore` | ToolRun ID linked to Execution and Approval | Durable terminal status and structured verification | Interrupted or partial actions never infer success | Chat activity, Viewer |
| Skill | `SkillsRegistry` | Skill ID and version; optional Project scope | Draft → experimental/disabled → reviewed/disabled → explicitly enabled | Draft/disabled code is never imported; imports validate declarations and collisions | Studio Skills, Viewer |
| Skill execution | `SkillExecutionRecord` store | Skill execution ID linked to Execution, Session, Project, ToolRuns, jobs, artifacts | Completed only from structured verification | Interrupted records remain non-terminal until reconciled | Studio Skills, Viewer |
| Product Task | `TaskStore` | Task ID plus stable idempotency key and explicit owner/Project/Session scope | `queued`, `preparing`, `waiting_for_approval`, `running`, `paused`, `blocked`, `completed`, `failed`, or `cancelled` | Atomic persistence; malformed state fails closed with recovery copy | Automations, Studio, Viewer |
| Routine | `RoutineManager` | Routine ID and revision; trigger creates a stable Task/Run identity | Definition state and next trigger only | Scheduler never owns execution truth; triggers enter the canonical Run boundary | Automations, Viewer |
| Automation Run | `AutomationRunStore` | Run ID, exact Task/Routine scope, idempotency key, lease, active Session model | Durable transition, checkpoint, ToolRun, approval, and verification links | Atomic claim, lease expiry/reclaim, restart recovery, no completed replay | Automations, Studio, Viewer |
| Heartbeat | `HeartbeatManager` | Schedule bucket resolves one stable Task/Run identity | Claim/recovery health only; completion comes from the governed Turn/ToolRuns | Duplicate bucket returns existing Run; Heartbeat never performs external delivery directly | Automations health, Services, Viewer |
| Connection | `ConnectionRegistry` | Connection ID, owner/Project scope, narrow typed capabilities and auth-health metadata | Enabled/disabled/revoked/blocked health projection; no secrets persisted | Missing scope, capability, health, or auth blocks use; no unrestricted shell | Studio Connections, Automations |
| Memory | `MemoryCurator` writes canonical memory records; vector/simple stores are retrieval projections | Memory ID with owner and Session/Project/account scope | Active/superseded/forgotten lifecycle; opt-in raw conversation storage also persists canonical records first | No Project memory is injected into a detached Session; projection entries without a canonical owner are excluded once records exist | Chat context, Studio Memory |
| Context assembly | `ContextAssembler` followed by `ContextBudget` | Typed item identity, scope, freshness, lifecycle, source | Selected manifest is redacted and budgeted | Scope mismatch, stale lifecycle, and over-budget items are excluded | Turn metadata, Viewer diagnostics |
| Coding active work | `ThreadSessionState` owns objective/Project attachment; `ActiveWorkStore` is a scoped coding projection | Session ID and an exact root matching the current Session | Phase, file digest, and next-step hints only | Root mismatch clears ActiveWork; it cannot restore or override Session/Project authority | Code workspace, contextual prompt |
| Echo Resolution | `EchoResolutionEngine` | At most one advisory envelope per ambiguous Turn | Advisory only; deterministic policy remains authoritative | Parse/model failure falls back safely without expanding tools or scope | Execution metadata, Viewer indicator |
| Grounded search | `SearchGrounder` through `_grounded_web_search`; `LiveRetrievalRouter` classifies structured-live domains | One request-scoped search budget, exact Project/Session, active Session model, and canonical ToolRun lineage | Accepted/insufficient evidence plus durable typed `ResearchArtifact` | Generic reflection cannot retry raw search; the duplicate Echo Search stack was removed | Research, Chat, Viewer |
| Media | `MediaLibraryStore` | Media asset ID, content hash, Project/Session scope | Registered artifact metadata; generation outputs register only after verification | Missing output or hash mismatch is not success; retired Editor data is readable only by explicit non-destructive export | Media, Studio |
| Provider / model | runtime provider configuration | Provider and model ID | Readiness is observable, not product-storage authority | Provider loss does not hide Projects, Sessions, Tasks, Skills, or documents | Startup diagnostics, Studio Providers |
| Studio | Backend projection APIs | Current Session and Project query scope | No independent durable state | Refresh from canonical owners | Studio |
| Viewer | Backend projection APIs | Current Session and Project query scope | No independent registry or completion state | Refresh during executions, approvals, jobs, and provider transitions | Viewer |

## Cross-domain invariants

1. A visible capability is not automatically available, selected, authorized, running, or complete.
2. Stable identity matching is followed by fresh policy, permission, Project, path, tool-inventory, and configuration validation at execution time.
3. A background trigger creates or resumes a Product Task and then enters the same Turn/Approval/ToolRun boundary as interactive work.
4. External side effects are never performed by Routine or Heartbeat schedulers directly.
5. Studio and Viewer may inspect and request governed changes through backend APIs, but neither owns canonical state.
6. Desktop storage follows `ECHOSPEAK_DATA_DIR`; browser development retains its existing compatible paths unless explicitly migrated.
7. Malformed authoritative JSON is preserved, quarantined by copy, diagnosed, and repaired manually; it is never silently replaced with empty state.
8. `pending_actions`, callback queues, ActiveWork, Studio, and Viewer are projections; `ApprovalRecord`, durable ToolRuns, ThreadSessionState, and ProjectManager remain their respective authorities.

## Context-engineering basis

The typed pipeline follows a small-context, explicit-lifecycle approach: select only relevant scoped items, keep durable memory separate from the active working set, budget before model invocation, and make optional resolution advisory rather than authoritative. See [Anthropic’s context-engineering guidance](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents), [LangChain context engineering](https://docs.langchain.com/oss/python/langchain/context-engineering), [MemGPT](https://arxiv.org/abs/2310.08560), and [Titans](https://research.google/pubs/titans-learning-to-memorize-at-test-time/).
