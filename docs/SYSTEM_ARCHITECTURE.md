# EchoSpeak System Architecture

**Status:** canonical source-present architecture for the dirty current worktree;
provider and packaged-runtime behavior remains live-unverified unless explicitly
recorded otherwise.
**Related contracts:** `ECHO_CORE_CONTRACT_MATRIX.md`, `RUNTIME_CONTRACTS.md`,
`LIFECYCLE_TRUTHFULNESS.md`, `DESKTOP_ARCHITECTURE.md`, and
`SIX_SYSTEM_IMPLEMENTATION_LEDGER.md`. The canonical runtime-to-model boundary
is documented in `MODEL_EXECUTION_CONTROL_PLANE.md`.

## Product layers

| Layer | Location | Responsibility |
|---|---|---|
| Windows desktop | `apps/desktop` | Tauri 2 host, authenticated loopback bootstrap, sidecar lifecycle, native window behavior |
| Web projection | `apps/web` | Chat and workspace UI; never owns runtime truth |
| Runtime/API | `apps/backend/api/server.py` | Session-scoped HTTP/streaming boundary and background-service lifecycle |
| Agent control plane | `agent/semantic_runtime.py`, `turn_understanding.py`, `core.py` | Selected-model understanding, TaskRun arbitration, post-understanding policy, canonical model/tool loop |
| Durable authority | `agent/state.py`, `projects.py`, domain stores | Projects, Sessions, Executions, ToolRuns, approvals, tasks/runs, artifacts, memory |

The governing chain is:

```text
Project
  -> Session
    -> Turn / Execution
      -> selected-model Turn Understanding
        -> TaskRun create/select/checkpoint (CAS)
          -> post-understanding mode and least-privilege inventory
            -> AgentDecision
              -> ToolRun
                -> verification and durable domain state
                  -> frontend projection
                    -> final response
```

The model may plan and synthesize. It cannot grant authority, approve itself,
expand Project scope, or establish completion through prose.

## Canonical ownership

| Concept | Single authoritative owner | Rebuildable projections |
|---|---|---|
| Echo assistant identity and behavior | `SOUL.md` via bounded `EchoIdentityProjection` | Turn-understanding and execution prompts |
| Project identity/root | `ProjectManager` | Sidebar and Settings Project views |
| Session identity/bindings/references | `ThreadSessionState` / `StateStore` | thread list, workspace chrome |
| Cross-Turn semantic objective, requirements, and graph | `TaskRunStore` | Session references, compact Chat status, Visualizer panels |
| Per-Turn interpretation | `ExecutionRecord.turn_interpretation` | lifecycle/activity UI |
| Execution, ToolRun, approval | `StateStore` | Chat transient state, Visualizer, Settings diagnostics |
| Registered capability | `ToolRegistry` and `SkillsRegistry` | Tools/Skills screens, prompts |
| Personal semantic memory | `MemoryCurator` writing `AgentMemory` records | indexes, profile/Markdown mirrors, Settings memory view |
| Documents | `DocumentStore` | chunks, embeddings, graph and UI results |
| Research requirements and sufficiency | `TaskRunStore` plus `RequirementCompletionEvaluator`; `ResearchArtifactStore` owns evidence artifacts | Visualizer Research panel and citations |
| Coding delegation and continuation | `TaskRunStore` plus `SpecialistRunStore` | Visualizer Code panel |
| Tasks and definitions | `TaskStore` and `RoutineManager` | Automations cards and schedule views |
| Automation work completion | `TaskRunStore` through the existing completion gate | `AutomationRunStore` lease/history projection, Product Task cards |
| Connections | `ConnectionRegistry` | Connections cards and routine capability choices |
| Extension packages | Contract-only `PackageManifest`; no installation owner exists | hidden/unavailable package presentation |
| Media jobs and assets | `GenerationJobStore` / `VoiceJobStore`; `MediaLibraryStore` for verified assets | unified `MediaJobProjection`, Visualizer Media panel |
| User Voice transport | `VoiceTransportStore` for transcript/playback lineage; canonical runtime for semantic work | Chat microphone, playback, and Voice status |

Malformed authoritative JSON fails closed, is preserved in quarantine, and
includes a manual recovery diagnostic. It is never treated as a successful
empty store.

## TaskRun v4 execution graph and recovery

Every new or loaded TaskRun has one versioned, bounded `TaskGraph` and one
`TaskGraphState`. Older TaskRuns migrate deterministically in memory to a
runtime-owned graph containing start, independent requirement or specialist
nodes, an optional join, and the existing finalization gate. Schema v4 adds
typed input gaps, immutable requirement history, and
quarantine diagnostics. The persisted store envelope is version 4; future
versions fail closed.

The graph is orchestration state, not an executor. Requirement nodes are
projected only from the TaskRun requirement ledger. Tool nodes may reference
durable ToolRun IDs but cannot manufacture execution success. The finalization
node is projected only from the canonical TaskRun status and
`RequirementCompletionEvaluator` verdict and cannot be mutated through the
generic transition API. Graph cycles require explicit bounded retry edges,
unreachable nodes and impossible joins are rejected, transitions are CAS
updated, and content-addressed checkpoints are bounded.

`correct_task` reconciles an authoritative replacement set instead of appending
to earlier work. Exact unchanged requirements retain stable IDs and verified
evidence; unrelated or changed requirements leave the active set and are kept
in `requirement_history`. A nonterminal TaskRun whose only owning Execution is
already terminal, or whose lifecycle checkpoint is structurally impossible, is
quarantined without deleting data and cannot be selected for continuation.

The desktop exposes exactly two primary views: Chat and Visualizer. Work,
Research, Code, Checklist, and Media are internal Visualizer panels. Settings
is a centered modal, not another primary workspace. Switching views or panels is
navigation only and never creates a Session, TaskRun, Execution, or specialist
handoff. An explicit profile handoff
atomically supersedes the prior TaskRun and creates one linked replacement in
the same Session and Project. Requirement/evidence state is retained, while
capability, permission, policy, configuration, inventory, retry, approval, and
selected-model authority must be resolved again on the target Turn.

## Research and retrieval

The canonical TaskRun scheduler owns query revision, provider/source changes,
extraction, retry budget, evidence gaps, and completion. One bound acquisition
attempt calls one provider adapter through the governed `web_search` capability.
`SearchGrounder` remains a noncanonical compatibility orchestrator for callers
without canonical TaskRun requirement/attempt binding; it is not a second
TaskRun scheduler or completion authority. `WebEvidenceHeuristics` contains pure
predicates only and cannot call a provider, retry acquisition, create a ToolRun,
or change requirement state. Provider adapters live in
`agent/web_search_providers.py`; `LiveRetrievalRouter` classifies domains that
require exact structured current data. Deep results are saved as typed,
versioned `ResearchArtifact` records carrying exact Project/Session scope,
active model binding, budgets, plans, branches, claims, evidence, contradictions,
gaps, freshness, and verification.

Every acquisition attempt first creates a bounded `ResearchQueryPlan` tied to
one TaskRun requirement and attempt. Provider queries cannot be assembled from
serialized model/runtime envelopes. Results retain the query-plan identity and
must match the requirement's entity, temporal, and requested-field contract;
returning nonempty search data is not itself evidence sufficiency.

The former Echo Search v1 workflow was removed. SearXNG support now lives in the
canonical provider adapter, so there is no second planner/evidence owner.

## Memory and context

Durable semantic records carry owner, Project, Session, type, lifecycle,
provenance, revision, checksum, freshness, sensitivity, and supersession links.
Scope filtering precedes keyword/semantic ranking. Hybrid retrieval combines
exact metadata, lexical and semantic candidates with reciprocal-rank fusion;
inactive, forgotten, superseded, and mismatched records are excluded.

The model-facing memory projection has one deterministic order: pinned account
profile facts, applicable pinned Project facts, relevant durable semantic
memory, then relevant Session-only memory. The same authorized projection is
compiled once per Turn and supplied to Turn Understanding and execution.

`ContextAssembler` selects bounded typed context. FAISS, summaries, profiles,
and Obsidian Markdown are projections. Obsidian synchronization is optional,
explicit, plan-driven, conflict-aware, and cannot overwrite canonical memory.

After TaskRun arbitration, the canonical execution projection excludes legacy
ActiveWork, unfinished-workflow, pending-action, and Session-summary semantic
state. The normal model envelope receives the selected TaskRun directly plus
current scope, its plan, current-Turn outcomes, bounded recent conversation,
and relevant scoped memory/documents. Approval state is a separate typed field.

Every ordinary message first creates an `ExecutionRecord`, then the selected
Session model receives one bounded `TurnUnderstandingEnvelope`. Only the typed,
validated `TurnInterpretation` may select a TaskRun, extract structured values,
or request capability categories. Suspended TaskRuns are candidates and never
auto-resume. Only a validated `ask_for_input` execution decision may move a
TaskRun to `suspended_waiting_for_user`; the full checkpoint is persisted with
revision compare-and-swap before the question is emitted. Pre-task ambiguity
may ask a question without inventing a resumable TaskRun. Interpretation,
policy, authority-conflict, and quarantined failures are terminal history.
Provider, model-output, and tool-parse failures retain the TaskRun objective and
are eligible only when a later selected-model interpretation explicitly chooses
to retry or continue them.

After arbitration, the runtime captures immutable per-Turn execution authority.
Durable Session progress updates cannot erase or expand its allowlist. Every
proposed action is still checked against fresh current policy, permissions,
Project/root, selected-model revision, configuration, constraints, and tool
inventory. Pre-execution rejection is typed feedback inside the existing
bounded control loop and never creates a fake ToolRun.

## Coding

Coding requires an attached Session Project whose `ProjectManager` root matches
the current Session. The selected TaskRun owns the semantic objective and graph
node; `SpecialistRunStore` owns only Echo's bridge to a Codex or OpenCode
runtime. The specialist runtime owns its own coding plan, commands, edits, and
thread history. The retired coding ledger and in-process coding loop are not
part of the production architecture.

Consequential filesystem actions use the normal approval and ToolRun boundary.
Approval identity is stable, while current policy, permission, Project/root,
path/source preconditions, tool inventory, and configuration are revalidated at
consumption time. ApprovalRecords retain immutable TaskRun, requirement,
attempt, originating Execution, and TaskRun revision lineage. Confirmation
CAS-resumes that exact suspended TaskRun before execution, binds the resulting
ToolRun/ToolOutcome to the same attempt, recomputes sufficiency, and returns
through the existing finalization gate. Pre-lineage approvals fail closed.

Project preview start and stop are registered, approval-gated ToolRegistry
actions. The Visualizer Code panel requests them through the canonical Turn
path; direct host-process endpoints are removed. Preview status remains a
read-only projection.

## Automations and Connections

The API runtime owns one Routine scheduler. Heartbeat evaluates triggers,
claims/reclaims work, and reports health; it never sends external messages or
mutates user resources directly. A trigger creates or resumes one exact-scope
Product Task and lease/history `AutomationRun`, then enters the same canonical
Turn, TaskRun, approval, ToolRun, checkpoint, and verification path as
interactive work. Automation and Product Task terminal labels are read-only
projections of the exact Execution's TaskRun verdict; response text and callback
success are not completion authorities.

`ConnectionRegistry` stores secret-free transport/auth capability, scope,
health, and error metadata. Connections expose narrow capabilities but do not
own Tasks, Routines, Runs, or completion. `PackageManifest` separately
describes installable Skill, tool-provider, model-adapter, media-provider, and
UI components. A package cannot inherit credentials or execution authority
from a Connection; executable operations still register in `ToolRegistry`.
The old `plugin` Connection kind and pipeline-hook registry are read-only
compatibility taxonomy for existing data and the disabled pre-v8 pipeline.

All executable tool origins converge on `ToolRegistry`; Connection and MCP
transports remain providers, not execution authorities. Skill workflows use
`SkillExecutionRecord` as a typed projection over canonical Turn, Approval, and
ToolRun truth. Structured weather uses `weather_live` first and falls back to
governed web research only when the dedicated provider cannot produce current
conditions.

Startup reports foundational readiness separately from full capability.
Embeddings and Document RAG are optional/degraded capabilities; canonical typed
memory remains available, and document vector indexes rebuild from retained
canonical extracted text.

## Media and retired Editor

The built-in image/video Editor, its stores, workers, tools, APIs, frontend
workspaces, and permission gate are removed. Media Library, voice, image/video
generation adapters, artifacts, and verification remain independent.

Legacy Editor state is not deleted or rewritten. The explicit
`scripts/retire_editor_data.py` path inventories and archives source JSON
byte-for-byte, records checksums and unsupported references, and can create an
idempotent Media import plan. Source data remains untouched.

Generation and Voice jobs now carry exact Execution, TaskRun, requirement,
attempt, and ToolRun bindings when created through the governed action wrapper.
`MediaJobProjection` provides one typed image/video/audio job surface without
adding another store or provider executor. Pre-binding legacy job files remain
readable and are explicitly labelled legacy-unbound.

User-gesture Voice input is transport, not a model tool. `voiceTransport.ts`
captures bounded PCM locally and sends it to one explicitly selected local STT
adapter. `VoiceTransportStore` persists the final transcript and its exact
Session/request/Execution/TaskRun lineage; raw microphone audio is discarded.
The transcript then enters the existing `/query` or `/query/stream` path with
`source=voice`, so Turn Understanding, TaskRun arbitration, tools, evidence, and
finalization remain unchanged. Transport VoiceJobs use `session_transport`
bindings and never impersonate ToolRuns. Model-invoked speech remains a
`canonical_tool` VoiceJob with full ToolRun identity.

Assistant text is synthesized in sentence-sized local chunks and played through
one ordered, interruptible queue. A spoken preamble is not replayed with the
final response. Stop cancels local playback and records cancellation on the
exact Voice transport turn. Cloud Voice is an explicit Settings opt-in only;
credentials do not enable upload, and its execution adapter remains unavailable.
Provider readiness is reported independently for speech input and playback, so
an installed Windows voice cannot falsely make dictation look ready. Current
batch STT adapters emit a final transcript only; fabricated interim words are
not projected as partial transcripts. Wake word and native speech-to-speech are
not active capabilities.

Bounded subagent and model-intelligence contracts are foundations only.
Subagents have no standalone executor: a future child must be a read-only child
TaskRun linked to an explicit SUBAGENT graph node, inherit the exact selected
Session provider/model, and receive only a subset of parent capabilities and
budgets. Model-intelligence profiles are derived from conformance evidence for
UI/capacity advice and cannot select or silently fall back to another model.

## Specialist coding runtimes

Raw inference adapters and specialist agents are separate system types.
`ModelFamilyAdapter` translates message, reasoning, structured-output, and
tool-call syntax for the one Session-selected Echo model. It never owns an
agent session. `SpecialistRuntimeManager` instead delegates a Project-scoped
coding subtask to a runtime that owns its own coding loop and context.

The active implementations are Codex App Server over JSON-RPC stdio and
OpenCode over authenticated loopback HTTP plus SSE. Claude Code is not exposed
as an available specialist until a real Agent SDK permission and event bridge
exists. OpenCode host execution is unavailable unless the operator
explicitly opts into it; Codex starts in read-only sandbox mode, so file
mutations return through one-shot specialist approval requests.

`SpecialistRunStore` is the sole owner of specialist runtime session, turn,
event, pending-request, and terminal-outcome truth. TaskRun remains the sole
owner of the delegated requirement and overall completion. A verified
`SpecialistOutcome` is projected into `RequirementState`; it cannot terminalize
the TaskRun or produce Echo's final response. The existing
`RequirementCompletionEvaluator` and model-control-plane finalization gate
remain authoritative.

Code is an internal Visualizer projection and control surface for those durable
specialist sessions. Work reads the same SpecialistRuns through TaskRun detail.
Opening Chat, Visualizer, Work, or Code is side-effect free; only an explicit
Delegate, Continue, Interrupt, or one-shot approval action mutates specialist
state. The former file/terminal/inline-diff workspace and its direct APIs are
physically retired.

Runtime receipts wake a scoped NDJSON projection stream from the durable store's
revision condition. Code does not poll on a timer or infer completion from UI
silence. A terminal specialist receipt updates the SpecialistRun first, then its
owning TaskRun requirement; the UI only renders those records.

Simple `casual_conversation` Turns use the selected Echo model directly with
Echo identity, authorized context, and recent conversation. They create no
TaskRun, requirement, plan, ToolRun, or AgentDecision envelope. Work-bearing
Turns still enter the canonical bounded model/tool control plane.

For LM Studio, Echo checks the exact Session-bound model in the native model
catalog before Turn Understanding. If it is installed but unloaded, Echo loads
that model explicitly. It never downloads or substitutes another model. The
structured-output capability probe runs only after readiness is established.

## Frontend behavior

Chat projects user and assistant messages plus one transient operation state;
the backend streams understanding, planning, waiting-for-model, thinking, tool,
waiting-for-user/approval, responding, and terminal lifecycle events. Final UI
state reconciles from durable Execution/Session truth. Pending approvals and
actionable failures remain visible. Durable ToolRuns, evidence, plans, and
verification remain available in Visualizer detail panels.

HTTP and gateway streams have a bounded startup deadline. Client cancellation
sets a request-scoped cancellation token consumed by Turn Understanding and the
model execution loop; cancellation terminalizes the Execution and releases only
that Session's coordinator slot. Private provider reasoning is never streamed.

Settings is a centered modal organized into Models, Search & Research, Voice &
Speech, Connections, Local Tools, Skills, MCP, Privacy & Permissions, and
Advanced. `GET /settings/catalog` is a read-only, secret-free projection over
the Session model binding, model runtime, search configuration, Voice runtime,
ConnectionRegistry/CredentialBroker, ToolRegistry, SkillsRegistry, and MCP
inventory. Settings owns no lifecycle or execution state. Connected means that
configuration or authorization exists; Ready requires the authoritative owner
to report executable readiness. Raw credentials and transport/runtime fields
remain behind the explicit Advanced compatibility editor.
Connection management is rendered inside the same selected-card detail panel;
its controls mutate only the revision-checked `ConnectionRegistry` APIs and do
not copy connection or capability state into the frontend.

The Visualizer Work panel consumes one Session/Project-keyed store and exact
TaskRun projections. It shows overview, the read-only TaskRun graph,
requirements, evidence, artifacts, occurrences, media lineage, approvals,
Executions, and ToolRuns. The store holds backend IDs and revisions only and
cannot terminalize work. Runtime-backed checklist rows are read-only
compatibility projections.

Each Session lazily receives one durable `SessionModelBinding`. Global provider
configuration supplies only its initial default. Switching uses binding-revision
compare-and-swap, evicts only that Session's cached agent, and cancels only
incompatible work in that Session. No hidden model fallback is introduced.

## Extension rule

New capabilities must define one durable owner, explicit identity/scope,
registered tools, approval/revision behavior for mutations, structured
verification, restart recovery, and a projection-only UI. No integration may
invent a parallel completion, permission, retry, or ToolRun system.

## Research execution path

`CanonicalSemanticRuntime -> TurnInterpretation.requirements -> TaskRun
requirement ledger -> ModelExecutionControlPlane -> governed ToolRun ->
ResearchArtifact evidence -> RequirementCompletionEvaluator -> existing answer
validation gate` is the only production research path.

ToolRegistry capability descriptors expose structure, freshness, authority,
health, scope, interaction, approval, cost, latency, and fallbacks in one
per-turn snapshot. Specialized weather, sports, and other providers participate
through that catalog; generic discovery uses the configured search cascade,
then safe page retrieval and structured extraction when snippets are weak.
No capability adapter owns a task or completion decision.
