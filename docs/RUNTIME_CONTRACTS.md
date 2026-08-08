# EchoSpeak Runtime Contracts

This document defines production runtime boundaries. `SYSTEM_ARCHITECTURE.md`
maps modules; `LIFECYCLE_TRUTHFULNESS.md` defines user-facing execution honesty.

## Model access

The provider/model selected for the active Session is the default for Chat,
Research, Coding, memory curation, skills, and Automations. A domain may not
silently launch a dedicated secondary model. An alternate provider is allowed
only when explicitly selected or when a visible, governed hard-capability
fallback records the reason.

`ThreadSessionState.model_binding` is the durable Session-owned selection.
Global runtime configuration is consulted only to lazily initialize a missing
binding. Updates require the current binding revision, cancel only incompatible
work in that Session, and never mutate another Session or silently fall back.

Provider readiness affects model work only. It does not hide or invalidate
Projects, Sessions, Tasks, Runs, approvals, memory, documents, or artifacts.

Every accepted ordinary message creates an Execution before semantic or
provider failure. The selected Session model first receives a bounded
`TurnUnderstandingEnvelope` and returns a validated `TurnInterpretation`.
Provider-native schema enforcement is used only when the concrete integration
declares and exposes it; otherwise the documented content channel uses bounded
JSON extraction. Both paths converge on one provider-independent canonical
decoder before strict Pydantic validation. The decoder folds only documented
property aliases and exact `TurnRelation` formatting variants, rejects alias
collisions, and never relaxes unknown-key or relationship invariants.
Turn Understanding defaults to a 2,048-token output budget. An explicit
provider length finish is never parsed as partial JSON: the same selected
Session model receives one bounded retry at up to 4,096 tokens. A second length
finish terminalizes only that interpretation attempt; it does not select a
different model or leave a resumable TaskRun.
`TurnInterpretation.relation` is the sole clarification authority;
`clarification_required` is a derived runtime projection, and a clarification
question is valid only for the `ambiguous` relation. The exact canonical relation
is carried into `ModelTurnEnvelope` alongside the legacy control projection.
Only after that boundary does the runtime derive TaskRun binding, mode, Skill
proposals, shortcuts, memory effects, and capability inventory. The same
selected model then receives freshly compiled `ModelTurnEnvelope` values and
returns runtime-validated `AgentDecision` values through one bounded production
loop for every provider. A model cannot finish required tool work through prose,
and only verified exact-scope ToolOutcomes are reinjected.

Drafted, quoted, listed, summarized, brainstormed, or discussed text is inert
response content. Command-shaped phrases inside that content do not authorize a
Skill or ToolRun. A deterministic post-validation boundary may only remove
mistaken external capabilities from such a Turn; it never adds authority. The
current instruction must explicitly promote content into an external action
(for example, save, send, or run) before mode or Skill derivation can expose
tools. An explicit empty Turn tool inventory remains empty, and a tool-backed
Skill cannot be projected as executing under `tool_use_policy=prohibited`.

Both envelopes carry the same bounded `EchoIdentityProjection` compiled from
the authoritative `SOUL.md`. Provider/model names describe the reasoning engine
and cannot replace Echo's assistant identity. Both stages also receive the same
owner-authorized memory projection; pinned account profile facts are included
deterministically and do not depend on Session similarity search.

The task-execution projection does not inject ActiveWork, legacy unfinished
workflow, pending-action prose, Session summaries, or Session semantic fields.
It is bounded to current authority/scope, the selected TaskRun and plan,
current-Turn outcomes, short recent conversation, and relevant scoped
memory/documents. These sources remain typed and independently owned.

Ordinary production Turns never construct or fall back to AgentExecutor, ReAct,
or LangGraph. Those executors are not part of the production runtime and
provider switching cannot re-enable them.

## Project, Session, and mode

- `ProjectManager` owns Project identity and trusted roots.
- `ThreadSessionState` owns Session identity, Project/workspace attachment,
  permission/source metadata, selected-model metadata, and references to the
  foreground/suspended TaskRuns and pending approvals.
- `TaskRunStore` owns cross-Turn objective, collected/missing structured inputs,
  selected Skill/version, plan, independent requirement states, the versioned
  bounded execution graph, permitted capabilities, ToolRun references,
  verified outcomes, retry identity, workflow stage, lifecycle, and revision.
- `TaskInputGap` distinguishes user-owned action inputs from runtime-discoverable
  information. Only a blocking user-owned gap may suspend work or authorize an
  `ask_for_input`; `missing_inputs` remains a compatibility projection.
- `correct_task` replaces the authoritative active requirement set. Exact
  unchanged requirements retain IDs and evidence; changed, omitted, or
  unrelated requirements move into immutable `requirement_history`.
- `suspended_waiting_for_user` is created only after a validated typed
  `ask_for_input` decision. Interpretation, provider, model-output, tool-parse,
  and policy failures are distinct terminal TaskRun states and are never
  automatic continuation candidates.
- `ExecutionRecord` owns one Turn's typed interpretation and model decisions.
- A Project is never inferred from Desktop, current working directory, or a
  previous shared-agent state.
- Mode selects a least-privilege tool inventory; it does not grant permission.
- Coding requires the Session's Project ID and root to match `ProjectManager`.
- Switching or detaching Project invalidates stale pending actions and scoped
  continuity projections.
- Recreating an agent or switching its provider/model rehydrates bounded
  conversation context from the durable Session timeline; the in-process
  conversation buffer is only a projection.

## Capability inventory

| Surface | Owner |
|---|---|
| Tool registration, provenance, and policy metadata | `ToolRegistry` |
| Skill package status and declared requirements | `SkillsRegistry` |
| Session Project/root | `ThreadSessionState` + `ProjectManager` |
| Cross-Turn semantic state | `TaskRunStore` |
| Per-Turn semantic interpretation | `ExecutionRecord` |
| Current permissions/configuration | runtime config and Session policy |
| Coding readiness | intersection report from current owners |
| Connections | `ConnectionRegistry` narrow capabilities and health |
| Media/generation | domain adapter projection; verified assets register in `MediaLibraryStore` |
| Extension package declaration | strict non-executable `PackageManifest` |

Prompts, `TOOLS.txt`, UI switches, model prose, and cached readiness reports do
not register or authorize a capability.

## Execution identity and ToolRuns

One logical operation maps to one canonical Execution identity and one
canonical ToolRun. Retries link to the original identity and may create a new
attempt record only when the lifecycle explicitly requires it. Callback queues,
stream items, activity cards, and scheduler status are projections.
Callback-to-boundary ToolRun identity is keyed by Execution, not worker thread,
so thread hops cannot mint an orphan sibling run.

Chat renders one primary live lifecycle projection. It transitions in place
through Understanding, Thinking/Planning, Tool/approval work, and Responding;
tool/task detail rows may coexist, but lifecycle placeholders do not create a
second Understanding/Thinking row.

Terminal success requires structured tool output and verification. Interrupted,
partial, missing, or prose-only evidence is never upgraded to success.

Each canonical Turn also binds one immutable `TurnExecutionAuthority` containing
its Session, Project, selected-model revision, mode, constraints, permissions,
and tool-inventory identity. Durable Session progress updates cannot overwrite
that per-Turn allowlist. Before every action, the runtime revalidates current
mutable policy, permission, Project/root, model binding, configuration, scope,
and inventory. A rejected pre-execution proposal becomes bounded
`RuntimeProposalFeedback`, never a ToolRun or ToolOutcome; the selected model
may repair the same rejection once and at most two proposal rejections per Turn.

TaskRun terminalization reloads the latest revision under the store lock and
updates only when the same Execution still owns it. Lifecycle conflicts are not
silently swallowed. Deterministically inconsistent nonterminal records become
`quarantined`, retain all evidence and history, remain visible in Work, and are
excluded from continuation candidates.

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

The ApprovalRecord also carries immutable `task_run_id`, `requirement_id`,
`attempt_id`, originating Execution, and TaskRun revision. Confirmation must
CAS-resume that exact suspended TaskRun; selecting the latest TaskRun in a
Session is forbidden. The ToolOutcome updates the same requirement and the
existing finalization gate. Pre-lineage approvals fail closed.

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

Canonical `web_search` is one requirement- and attempt-bound acquisition through
one explicitly selected provider adapter. It cannot fan out query variants,
cascade providers, fetch pages, synthesize an answer, or decide completion inside
that ToolRun. `TaskRunScheduler` owns retries and strategy changes, including an
explicit alternate-provider attempt. `SearchGrounder` remains only as a bounded
compatibility adapter for noncanonical callers while those callers are retired.
`WebEvidenceHeuristics` contains classification predicates only and cannot invoke
providers or own a retry. `LiveRetrievalRouter` classifies sports, finance,
weather, flights, schedules, and other freshness-sensitive requests for typed
providers or honest unavailable/fallback results.

`ResearchArtifactStore` persists exact Project/Session/model scope, budgets,
plan, branches, sources, evidence, claims, contradictions, gaps, freshness,
verification, and synthesis. Artifact read, reuse, and consumption revalidate
that exact scope.

Every provider query passes through a requirement- and attempt-bound
`ResearchQueryPlan`. Raw user text, TaskRun objective, requirement objective,
and model-proposed query remain separate. Internal envelope labels, JSON seams,
dangling pronouns, unexplained domain drift, and queries that lose required
entities, locations, time windows, or exact years fail before provider access.
A search-cache identity is scoped to TaskRun, requirement, normalized query,
freshness class, and tool/provider. The composite user turn is never a child
search cache key, and acquisition limits are applied per requirement.
A successful provider request is execution truth only; evidence becomes usable
only when its query-plan identity, semantic anchors, requested-field coverage,
freshness, and provenance satisfy the owning requirement.

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

`TaskRunStore` owns the objective, graph node, requirement state, and structured
semantic continuation. `ThreadSessionState` owns only the active Project and
TaskRun references. `SpecialistRunStore` owns the bounded bridge to Codex or
OpenCode; the external specialist owns its internal coding loop and thread
history. The former coding ledger and in-process coding loop are retired.

Before mutation the coding path establishes exact target/exclusions, inspected
revision, expected consequence, permission, approval need, and verification.
Resume rejects mismatched Session, Project/root, source revision, or ledger
revision.

## Automations

`RoutineManager` owns reusable definitions, `TaskStore` owns user-facing finite
work, and `AutomationRunStore` owns trigger idempotency, execution history,
leases, checkpoints, and recovery. `TaskRunStore` remains the only owner of the
semantic work lifecycle and completion verdict. AutomationRun and ProductTask
terminal labels are compatibility projections of the exact Execution's
TaskRun, never deductions from response text or callback success. The API
runtime owns one scheduler lifecycle.

`ProactiveEngine` is a retired import-only compatibility module. It cannot start
its historical daemon, and `/proactive/task` fails closed with `410 Gone` rather
than creating work in a second queue. The read endpoints return an inert retired
projection pointing callers to Project/Session-scoped Routines.

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

Installable packages are a separate taxonomy. `PackageManifest` declares
components and required Connection kinds but stores no credentials and grants
no execution authority. Tool-provider components become executable only after
their individual tools register through `ToolRegistry`. The persisted
`ConnectionKind.PLUGIN` and `PluginRegistry` names remain legacy compatibility
for existing records and the disabled pre-v8 pipeline; they are not the new
package or production orchestration mechanism.

Native, Skill-selected, Connection-backed, and MCP-discovered tools project
through the same `ToolRegistry`. Each entry carries origin/owner, input schema,
approval requirement, health, availability, and Connection or MCP identity.
Connection wrappers re-resolve current Project/Session scope and health at call
time. MCP registration rejects collisions; a disconnected server marks its
owned tools unavailable so they cannot enter a new model allowlist.

`SkillExecutionRecord` is the durable Skill participant for one Turn. It owns
the Skill/version identity, workflow stage, required and collected inputs,
permitted tools, verification rules, completion criteria, parent/child
ToolRuns, approvals, and artifacts. Completion requires successful verified
ToolOutcomes; unknown verification rules fail closed.

## Execution graph, surfaces, and bounded delegation

TaskRun schema v6 embeds one validated `TaskGraph` and `TaskGraphState`, typed
input gaps, immutable requirement history, recovery epochs, the latest
runtime-owned liveness decision, and quarantine diagnostics.
Non-retry edges must be acyclic, every node must be reachable and have a path
to the single finalization node, joins require multiple incoming branches, and
retry edges declare a finite traversal bound. State changes use TaskRun CAS and
bounded content-addressed checkpoints.

Requirement nodes can change only through the canonical requirement ledger.
The finalization node can change only as a projection of the existing answer
validation gate and TaskRun terminalization. Therefore the graph cannot create
a second completion path. Tool execution remains a durable ToolRun and a graph
Tool node may only link to its ID.

Desktop Chat and Visualizer are the only primary views. Work and Code are
Visualizer panels over the same Session, Project, and TaskRun. Settings is a
modal. View or panel navigation creates no Session or TaskRun. An explicit handoff
supersedes the source TaskRun and creates one linked replacement in the same
scope. It preserves requirement/evidence history but clears mutable capability,
retry, approval, configuration, inventory, and model authority snapshots so
the target Turn revalidates them.

Settings capability cards are a read-only projection, not another configuration
or readiness owner. `GET /settings/catalog` joins current state from
`SessionModelBinding`, model/search/voice adapters, `ConnectionRegistry`,
`CredentialBroker` presence metadata, `ToolRegistry`, `SkillsRegistry`, and MCP
tool lineage. Secret values and technical transport fields are excluded. A card
may report Connected when authorization or configuration exists; only an
authoritative successful probe/detection may report Ready. Frontend card
selection and detail expansion are presentation state only.

Bounded subagent contracts are read-only and non-executable in this phase. A
future subagent must inherit the selected Session model, be a child TaskRun of
an explicit graph node, stay within parent budgets/capabilities, and return only
durable ToolRun/evidence/artifact references. It cannot become another response
or completion authority.

## Unified media and model intelligence

GenerationJob and VoiceJob remain their durable domain owners. New jobs bind
the exact current Execution, TaskRun, requirement/attempt pair, and ToolRun;
stable idempotent replays still revalidate current authority. A unified
`MediaJobProjection` normalizes their status and assets for UI without adding a
second store. Legacy unbound job files remain readable but are labelled as such.

Direct microphone capture and reply playback use the separate
`session_transport` binding kind because a user gesture is not an agent tool
execution. A `VoiceTransportTurn` owns only capture/transcript/playback lineage.
It must match the current Session and Project, its submitted text must exactly
match its durable final transcript, and it is rebound once to the exact client
request, Execution, and TaskRun produced by the canonical query path. It cannot
interpret intent, create a TaskRun, create a ToolOutcome, evaluate completion,
or answer the user. Raw microphone audio is temporary; final transcript and
VoiceJob records are durable and malformed JSON is quarantined with a manual
recovery diagnostic.

Local speech provider replay identity includes the client Voice turn, ordered
chunk sequence, selected provider, exact text, and settings. A collision fails
closed. Current Session/Project scope is revalidated before each transcription
or synthesis request. Cloud credentials never select a Voice provider and a
recorded cloud opt-in does not make an unimplemented adapter executable.
Playback cancellation may claim the exact Session/client-turn identity before
the first synthesized clip returns, preventing an early Stop from racing into
an untracked background playback job.

`ModelIntelligenceProfile` is an advisory projection derived from exact-model
conformance cases. It may recommend exposure limits but cannot switch the
Session model, launch another model, or alter control-plane authority.

## Retrieval and startup capability

Typed `records.json` memory is canonical and remains usable when embeddings are
unavailable. FAISS memory and document indexes are disposable retrieval
projections. Newly ingested document text is retained in the canonical document
content directory so a missing/corrupt index can be rebuilt without rewriting
metadata or user content. Legacy document entries without retained canonical
text report a degraded, non-rebuildable index instead of false readiness.
Vector document retrieval may remain usable while readiness reports degraded
when a requested optional hybrid/BM25 layer is unavailable.

`/startup/readiness` separates `backend_available`/`core_ready` from
`full_ready`. Its optional capability projection includes exact provider/model,
adapter, Tool Registry origins, Skills, Connections/MCP, embeddings, document
retrieval, durable stores, and `degraded_capabilities`. Optional provider or
retrieval failure does not hide the desktop shell.

## API and desktop transport

Desktop transport binds loopback and uses the authenticated bootstrap contract.
Non-loopback API binding requires authentication; an unauthenticated remote bind
fails closed. Host-terminal execution is not the default merely because terminal
actions are enabled; terminal backends must be explicitly configured and remain
Project/approval constrained.

Stream startup is bounded by `STREAM_STARTUP_TIMEOUT_SECONDS`. HTTP stream
disconnect/cancellation propagates a request-scoped token through selected-model
understanding and the bounded model/tool loop, terminalizes the Execution as
cancelled, and releases the per-Session coordinator. Provider request timeouts
remain the hard bound when a provider yields no cancellable bytes. The canonical
control plane is the sole owner of provider retries; OpenAI-compatible clients
disable their SDK retry layer. Local streams also have a bounded meaningful-
progress deadline (`MODEL_STREAM_IDLE_TIMEOUT_SECONDS`): content, reasoning,
tool arguments/names, or a finish signal reset it, while empty keepalives do
not. A request that fails after emitting any partial stream is not replayed.
Success and failure both close the transient desktop activity state.
Turn Understanding has separate warm and local cold-start timeouts plus a small
output budget, each overridable by the selected model profile. Prompt-JSON
streams stop after the first complete decoded object; timeout/cancellation closes
the iterator when the provider transport supports it. No alternate model is used.

## Frontend projection

`GET /task-runs/{id}` is an exact Session/Project-scoped bounded read model for
the Visualizer Work panel. It projects requirements, completion, graph, stage, approvals, Executions,
ToolRuns, evidence/artifacts, media, and coding lineage without private
reasoning, prompts, secrets, or unbounded output. Work's store is keyed by
Session and Project and owns no lifecycle state. Automation-backed ProductTasks
are read-only compatibility rows.

Code preview start/stop can execute only as `code_preview_start` and
`code_preview_stop` ToolRuns with current Project, permissions, configuration,
inventory, Session model, and explicit approval. Direct preview mutation APIs
return conflict and cannot launch or stop a host process.

Chat shows conversation plus one transient lifecycle projection driven by
backend understanding/planning/model/tool/wait/terminal events, then reconciled
from durable final state. The same versioned, bounded semantic activity
projection drives Chat, the main avatar, Visualizer, and the optional desktop
companion through one frontend decoder and reducer. TaskRun snapshots expose
only display-safe requirement status, attempts, retries, source counts, gaps,
recovery, next action, and completion disposition; private reasoning, prompts,
secrets, tracebacks, and persistence IDs are excluded from that projection.
Visualizer may also rehydrate the same authoritative state from the exact-scope
TaskRun read model, but it does not own or infer completion. Chat retains
actionable approval, clarification, conflict, and recoverable failure UI. Detailed
ToolRuns, evidence, research branches, tasks/runs, provenance, and health remain
in Visualizer detail panels and Settings diagnostics.

The centered Settings modal must remain accessible under narrow windows,
maximized/full screen, and DPI scaling through controlled overflow, keyboard
navigation, and correct content offsets. Opening or switching a view does not create a
Session; only the explicit plus/new-Session action does.
That action is client single-flight and carries a durable idempotency key;
replays with the same parameters return the original Session, while key reuse
with different parameters fails with conflict.

Explicit durable memory writes require a typed memory-write operation or typed
memory payload (or an existing typed confirmation checkpoint). Memory recall
never enters the writer merely because the broad `memory` capability is present.
Typed live-sports operations expose `sports_live` as the structured primary
capability; generic research tools may remain available as secondary evidence.

## Local semantic and retrieval reliability

Turn Understanding uses the selected Session model through a bounded semantic
lane: at most six recent messages, eight candidate TaskRuns, six memory rows,
two Project summaries, and four compact verified outcomes, with a default
2,048-token output budget so bounded multi-part requirements fit the strict
schema. Qwen receives `/no_think` only in this lane. Normal
answering, research, coding, and tool-loop model behavior is unchanged.

Strict structured validation remains the normal boundary. If bounded repair is
exhausted, the runtime may preserve only two non-semantic fallbacks: inert
conversation becomes answer-only, while a conservatively recognized public
information or authorized-memory lookup becomes one read-only requirement with
the original user objective unchanged. This fallback cannot grant mutation,
communication, terminal, interactive-browser, local-file, Project/codebase, or
credential authority. If the selected provider fails before interpretation, the
same read-only objective may be materialized so a later exact Continue retains
the work; the runtime does not substitute another model or provider.

Before an LM Studio semantic request, the runtime uses `GET /api/v1/models` to
verify the exact Session-bound model. An installed but unloaded model is loaded
once through `POST /api/v1/models/load`; a missing or failed model produces a
selected-provider failure and never triggers download, model substitution, or a
hidden provider fallback. A successful load invalidates stale capability-probe
results.

LM Studio native JSON Schema is enabled only after a tiny probe of the actual
endpoint/model/load-profile combination succeeds. Positive and negative probe
results are cached by that identity and invalidated on provider/model changes.
Probe failure uses bounded single-object extraction; all paths still converge
on the canonical decoder and strict `TurnInterpretation` validation.

Desktop cancellation owns an exact request ID and, after `turn_bound`, its exact
Execution ID. Session creation/switching, provider switching, stream abort, and
window close abort the frontend stream, signal the backend token, close the
provider iterator where supported, cancel the Execution/TaskRun, and terminalize
only that Turn's open Runtime Items and ToolRuns. Late events cannot rebind a
different Session.

One logical tool call owns one runtime-generated ToolRun ID across UI events,
durable state, verification, ToolOutcome, and retry provenance. Provider fan-out
and query variants are internal attempt diagnostics. Normal completion requires
zero open ToolRuns; orphan recovery is high-severity and forces Turn failure.

Retrieval has independent `execution_status` and `result_state` axes. Transport
success with `no_data`, `unsupported_intent`, `ambiguous_entity`,
`provider_unavailable`, `stale_data`, or `insufficient_evidence` cannot satisfy
objective completion. Both axes, provider, observation time, and confidence are
preserved in ToolOutcome, ToolRun, model reinjection, research artifacts, and UI
history. `ToolOutcome.result` is the additive typed data/source/fingerprint
envelope; legacy `output` text remains a presentation compatibility field and
does not become a second execution or completion owner. Exact flight
availability/status requires a configured structured
Connection; generic web search remains labelled general research. Structured
sports exposes explicit schedule, score, standings, results, and next-event
operations with governed grounded-web fallback after a non-retryable typed
provider-unavailable/unsupported result. A transport-successful but unusable
result never exposes `answer` as a valid next action.

## Requirement-driven research

Every semantic TaskRun owns versioned independent requirements and their
status. Memory, local context, calculations, and separate retrieval objectives
remain distinct. A ToolRun binds to exactly one active requirement and one
attempt before execution. Successful execution is not evidence sufficiency;
the runtime maps verified evidence to requested fields and preserves it in a
ResearchArtifact.

`TaskRunScheduler.advance()` is the sole owner of TaskRun liveness. For each
persisted revision it chooses one next action: run an eligible capability for
one exact requirement, finalize through the existing control-plane gate, wait
for user/approval/external work, or fail an impossible lifecycle invariant.
The selected model may formulate the call but cannot switch the scheduler's
requirement or capability set. An explicit continuation opens a new bounded
recovery epoch only for incomplete work, preserves satisfied requirements and
evidence, and rejects an identical tool-plus-arguments fingerprint.
The Session's `foreground_task_id` is a reference to the exact current TaskRun.
An exact Continue command selects that valid foreground record directly even if
other resumable records exist. Without a valid foreground reference, a sole
candidate may be resumed deterministically; multiple candidates still require
semantic disambiguation rather than newest-record guessing.

Verification is explicit: generic nonempty output is execution success only.
Information is usable only when an explicit verifier, a declared
provider/extractor result contract, structured covered fields, or a concrete
execution contract such as a zero terminal exit marks it verified. Requested
fields absent from that result remain unresolved.

`verified_absence` is usable only when a verifier records an authoritative,
HTTP(S)-citable source, the covered scope, and an exact semantic match to an
existence or next-item requirement. A source label without a durable citation,
empty results, and ordinary `no_data` never satisfy a requirement.

Progressive synthesis may compact narrative passages but cannot compact
provenance. Governed search output retains a bounded append-only source ledger
with each normalized query, title, URL, and grounding acceptance status.
ResearchArtifact citation projection reads the same output, so sources from
earlier retrieval rounds survive later synthesis.

`RequirementCompletionEvaluator` is the only research-sufficiency evaluator.
Its shadow comparison is diagnostic and cannot terminalize work. The existing
ModelExecutionControlPlane decision validator remains the one response gate and
consumes the evaluator's verdict. Full answers require every mandatory
requirement; partial answers are permitted only after all remaining gaps are
blocked, unavailable, or budget-exhausted.

Once an answer leaves that gate, response finalization is presentation and
durable-record projection only. It does not recompile the model envelope or run
a second completion decision against a later TaskRun revision.

Research depth is adaptive. Fast lookups receive one primary attempt and one
recovery; standard and deep work receive larger bounded attempt, source, time,
context, and concurrency budgets. Satisfied requirements are immutable unless
the user changes them, their evidence expires, or current authority invalidates
them. Every attempt revalidates Session, Project, selected model, inventory,
permissions, constraints, approval state, provider health, and configuration.

`safe_web_fetch` is read-only public retrieval, separate from approval-gated
Playwright. It allows HTTP/HTTPS on standard ports, validates every DNS result
and redirect, rejects non-public destinations, sends no browser credentials,
and enforces content/size/time bounds. It extracts visible text, metadata,
JSON-LD, Microdata/RDFa attributes, and tables. Interactive rendering and
authenticated navigation remain owned by `browse_task`.

Tool startup is deterministic: native tools, Skills, Connection-registry
validation, MCP, then one hashed/revisioned inventory snapshot. Agents rebind
only when that authoritative registry revision changes. The shared embedding
owner is prewarmed during startup; OpenAI embedding construction is skipped when
its credential is absent.

## Specialist runtime contract

Specialist agent runtimes are not ToolRegistry functions and are not raw model
adapters. Echo delegates one `RequirementKind.SPECIALIST` requirement to one
durable `SpecialistRun`. Stable lineage contains Session, Project/root,
TaskRun, requirement, Session model-binding revision, selected specialist
runtime, and upstream runtime session/turn IDs.

Every start, continuation, interrupt, and approval revalidates current Session
attachment, Project root, TaskRun ownership, requirement kind, Session
model binding, runtime availability, and Echo's current coarse permissions.
Approvals are one-shot; session-wide specialist approval decisions are not
exposed by EchoSpeak. Codex starts read-only so writes request approval.
OpenCode is host execution and therefore requires the explicit
`ECHOSPEAK_ALLOW_UNSANDBOXED_OPENCODE=true` opt-in plus current file-write and
terminal permissions.

Runtime-specific notifications are normalized into bounded
`SpecialistEvent`s while retaining bounded source payload and identifiers for
diagnosis. The durable event journal is append-only. Backend restart converts
active child-process records to `disconnected` without discarding upstream
session IDs. Malformed authoritative state fails closed and produces a
quarantine copy plus manual recovery instructions; it is never overwritten
with defaults.

The Visualizer Code projection consumes one exact-scope NDJSON stream. The stream
wakes on `SpecialistRunStore` revision changes and carries ordered event
receipts; timeout frames are connection keepalives only. Frontend navigation,
rendering, and local state cannot advance specialist lifecycle or TaskRun
completion.

A specialist terminal event produces a `SpecialistOutcome`. Only a verified
terminal outcome may satisfy its owning specialist requirement. The
SpecialistRun cannot mark a TaskRun complete, answer the user as Echo, change
Session/Project/model authority, or synthesize ToolRuns. TaskRun evaluation and
the existing response finalization gate retain those responsibilities.

Model-family handling remains independent. Qwen and Gemma retain their bounded
adapters. GLM is detected explicitly and consumes only documented native
OpenAI-compatible `tool_calls` plus the separate reasoning channel; printed
tool-shaped prose is never promoted to execution.

## Acceptance gates

Release claims require, with disposable data:

1. Focused and full backend regression.
2. Web typecheck, component tests, and production build.
3. Desktop contract tests, Rust format/check/test/Clippy, and Tauri build.
4. Sidecar and installer builds when the required toolchains are available.
5. Native launch, startup recovery, Chat/Visualizer switching, Chat input/send,
   Session continuity, and responsive Settings-modal navigation.
6. Live configured-model checks for Research routing, memory recall/isolation,
   coding continuation, tool selection, automation planning, and honest blocks.

Environment-gated or manual checks must be reported as not run; they cannot be
inferred from compilation or earlier results.
