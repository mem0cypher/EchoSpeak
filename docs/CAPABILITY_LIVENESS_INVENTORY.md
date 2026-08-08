# EchoSpeak capability and liveness inventory

Snapshot: 2026-08-08
Phase: recovery baseline and canonical liveness consolidation
Method: current-source inspection only

No tests, builds, desktop launches, provider calls, OAuth flows, browser scenarios,
voice scenarios, or live validation were run for this inventory. A row marked
`coverage present` means focused deterministic test source exists; it does not mean
that test was executed against this worktree during this pass.

## Status vocabulary

- **Source-present**: the implementation and its production entry point exist in the
  current checkout.
- **Coverage present**: a focused deterministic test exists in source, but was not run
  in this pass.
- **Live-unverified**: no current real provider/UI scenario was executed in this pass.
- **Provider-ready**: the adapter can be selected when its explicit runtime dependency,
  credential, endpoint, or local process is available. It is not a claim that the
  current machine is ready.
- **UI-visible**: the user can see a projection of this capability or its status.
- **User-configurable**: the user can configure it through a current bounded product
  surface rather than only environment variables or raw files.

## Working-tree baseline

The current checkout contains a committed `echospeak-8.0` baseline plus a large,
valuable uncommitted redesign. Git HEAD is not a recovery source for those
uncommitted files, and this inventory does not imply that the redesign has been
reviewed as one unit. `.tmp/` is intentionally ignored because its disposable
validation state can contain credentials, local transcripts, logs, and encrypted
secret material. Existing `.tmp/` data remains local and was not inspected for
secret values or deleted.

Every capability below remains live-unverified unless the row says otherwise.
Source presence, a test file, credential detection, endpoint discovery, and a
successful build are not substitutes for a real acceptance scenario.

## Runtime and liveness

| Capability | Source-present | Deterministic evidence in source | Live status | UI-visible | User-configurable | Confirmed boundary or blocker |
|---|---|---|---|---|---|---|
| Canonical ordinary-turn runtime | Yes | Coverage present in semantic-boundary, runtime-integration, and model-execution contract tests | Live-unverified | Indirectly through Chat and Visualizer | No | `CanonicalSemanticRuntime` serializes a Session turn, binds its Execution, runs Turn Understanding and TaskRun arbitration, then enters the existing control plane. |
| Turn Understanding read-only recovery | Yes | Source-present; current tests were not run in this pass | Live-unverified | Through normal lifecycle/activity projection | No | After strict decode and bounded same-model repair fail, inert conversation remains answer-only. A conservatively recognized public-information or authorized-memory request preserves the exact user objective in a read-only TaskRun path. Effects, communications, terminal/browser interaction, local paths, Project/codebase access, and credential-bearing input still fail closed. |
| TaskRun liveness | Yes | Coverage present in `test_agent_liveness_contracts.py` and mixed-requirement tests | Live-unverified | Partially | No | `TaskRunScheduler.advance()` exists and is consulted from the runtime. It selects the active requirement and next action; it does not finalize responses. |
| Research sufficiency | Yes | Coverage present in requirement-driven research, weather evidence, and premature-completion tests | Live-unverified | Partially | No | `RequirementCompletionEvaluator` demotes retrieval states without verified evidence and is the runtime sufficiency decision. Legacy completion remains telemetry only. |
| Recovery epochs and Continue | Yes | Coverage present for reopening only incomplete requirements and rejecting repeated recovery strategies; current tests were not run | Live-unverified | Partial status only | No | Recovery state is durable on TaskRun. An exact Continue command selects the Session's valid foreground TaskRun directly, even when other resumable candidates exist; with no valid foreground it may select a sole candidate, otherwise semantic disambiguation remains required. Current live behavior with the historical weather/FIFA/SDK scenario remains unproved. |
| Session-owned model selection | Yes | Coverage present in work-lifecycle and runtime ownership tests | Live-unverified | Yes | Yes | `SessionModelBinding` is persisted in Session state with a revision. Provider switching requires the expected revision and cancels only incompatible work for that Session. |
| Bounded selected-model loop | Yes | Coverage present in model-control-plane and adapter tests | Live-unverified | Yes, through activity events | Thinking and effort are configurable | Same-provider retry, malformed-output repair, proposal feedback, tool execution, and answer validation remain inside one `ModelExecutionControlPlane`. No hidden alternate provider is introduced. |
| Stop, Steer, Queue | Yes | API-contract coverage present | Live-unverified | Yes | Yes | `/query/cancel` targets an exact request; `/query/steer` binds the active TaskRun; queued turns are durable in backend state. |
| Unified semantic activity | Mostly | Reducer and stream-event coverage present | Live-unverified | Yes in Chat; Visualizer consumes related projections | No | Backend emits iteration, reasoning summary, usage, tool, lifecycle, recovery, and final events. `agentActivityReducer` is shared by Chat/avatar behavior, but not every Visualizer field is yet a direct projection of this reducer. |
| Background routines | Yes | Automation projection/lease coverage present | Live-unverified | Yes in existing automation surfaces | Yes | Canonical Routine/AutomationRun/Execution/TaskRun ownership remains active. `ProactiveEngine` is import-only compatibility: its daemon cannot start, its mutation endpoint returns `410 Gone`, and its reads are inert retired projections. |

## Models and providers

| Provider/path | Source-present | Provider-ready contract | UI-visible | User-configurable | Current conclusion |
|---|---|---|---|---|---|
| OpenAI | Yes | API key plus installed client | Yes | Yes | Ordinary selected-model provider. No live call made. |
| Google Gemini | Yes | API key plus installed client | Yes | Yes | Ordinary selected-model provider. No live call made. |
| LM Studio | Yes | Reachable OpenAI-compatible endpoint and selected loaded model | Yes | Yes | Model discovery, structured output, streaming, cancellation, context metadata, and idle-timeout logic are source-present. Live reliability remains unverified. |
| Ollama | Yes | Reachable Ollama endpoint and installed integration | Yes | Yes | Model discovery and ordinary model path are source-present. |
| LocalAI | Yes | Reachable OpenAI-compatible endpoint | Yes | Yes | Adapter is source-present; no current liveness proof. |
| llama.cpp | Yes | Configured native model path/runtime | Yes | Yes | Native adapter and cancellation contracts are source-present. |
| vLLM | Yes | Installed local integration and configured model | Yes | Yes | Adapter is source-present; dependency readiness is environment-gated. |
| Cross-provider fallback | No production chain | Not applicable | No | No | EchoSpeak deliberately keeps the selected Session model exact. Current recovery retries the same selected provider/model. A future fallback policy must be explicit, Session/TaskRun-scoped, cost-visible, and recorded; it must not be inferred silently. |
| Startup prewarming | Yes, opt-in | `enable_prewarm` only | Advanced setting/status | Yes | Startup logs explicitly keep prewarming disabled unless the setting is enabled. |

The current model catalog is intentionally narrower than Hermes. Adding provider cards
before adapters, capability probes, Session binding, and cost/privacy rules exist would
create false readiness, so provider breadth is not a Phase 0 implementation target.

## Search and research

| Capability | Source-present | Deterministic evidence in source | Provider-ready | UI-visible | User-configurable | Confirmed boundary or blocker |
|---|---|---|---|---|---|---|
| Requirement decomposition | Yes | Coverage present | Runtime-owned | Requirements are projected in work/visualizer data | No | The model may propose decomposition, but runtime canonicalization supplies stable requirement identity and strict states. |
| Evidence envelopes and field coverage | Yes | Coverage present | Runtime-owned | Partial | No | Tool success and information sufficiency are separate. Entity, field, freshness, provenance, and verified-absence checks exist. |
| DuckDuckGo/DDGS | Yes | Provider-order and rejection coverage present | Library-dependent, no key | Yes, through the Settings catalog | Advanced configuration | Default general-search acquisition path. Catalog readiness reflects package detection and current selection, not a live search performed by Settings. |
| Brave Search | Yes | Provider-order coverage present | API-key dependent | Yes, through the Settings catalog | Advanced configuration | Credential presence is shown as Connected; it is not promoted to Ready without runtime use/proof. |
| SearXNG | Yes | Provider-order coverage present | Endpoint dependent | Yes, through the Settings catalog | Advanced configuration | Endpoint presence is shown as Connected; the raw endpoint remains behind Advanced. |
| Specialized weather/sports/live data | Yes | Weather and mixed-requirement coverage present | Source/provider dependent | Yes; cards plus result embeds | Advanced configuration | Structured paths feed evidence and are presented without treating an API key as successful retrieval. |
| Safe public-page retrieval | Yes | SSRF/extraction contract coverage exists in the research suite | HTTP/HTTPS public targets only | Evidence projection only | No | Separate from interactive browser automation and constrained by network, redirect, type, size, and timeout policy. |
| Canonical web-search acquisition | Yes | Relevant deterministic coverage exists but was not run in this pass | Depends on selected provider | ToolRun/activity projection | Advanced configuration | One TaskRun requirement/attempt invokes one selected provider adapter and returns provider/query metadata. Provider changes, query reformulation, safe fetch, evidence evaluation, and completion remain runtime-owned separate attempts. |
| SearchGrounder compatibility path | Yes, noncanonical only | Legacy reliability coverage present | Depends on selected providers | Hidden | No | Candidate generation, provider cascading, caching, page fallback, and synthesis remain only for noncanonical compatibility callers. A canonical bound TaskRun cannot enter this path; missing requirement/attempt identity fails closed. |

## Connections, MCP, tools, and secrets

| Capability | Source-present | Deterministic evidence in source | Provider-ready | UI-visible | User-configurable | Confirmed boundary or blocker |
|---|---|---|---|---|---|---|
| Connection registry | Yes | Corruption, scope, and secret-free projection coverage present | Yes | Yes | Yes | Registry owns secret-free identity, scope, lifecycle, health, and capability selection. Tool execution remains owned by ToolRun. |
| Credential broker | Yes | Structural coverage through connection tests | Windows only | Secrets are intentionally hidden | Indirectly | Secrets are stored as opaque references backed by current-user Windows DPAPI ciphertext. |
| Obsidian | Yes | Connection/runtime coverage present | Available local-path adapter | Yes | Yes | The one consumer-facing catalog adapter marked available. Project scope remains required. |
| Custom MCP | Yes | MCP discovery, reconnect, collision, and authority coverage present | Advanced; stdio, Streamable HTTP, or SSE | Yes | Partially, advanced | MCP tools are dynamically registered with origin, server, permissions, approval, resources, and prompts. No live MCP server was connected in this pass. |
| Notion | Catalog plus legacy adapter | Partial | Requires provider adapter for the new lifecycle | Yes | Setup is intentionally blocked/honest | Must not be displayed as ready merely because legacy credentials or tool code exist. |
| GitHub | Catalog plus legacy adapter | Partial | Requires provider adapter for the new lifecycle | Yes | Setup is intentionally blocked/honest | Same distinction: catalog presence is not OAuth readiness. |
| Google Calendar | Catalog plus legacy adapter | Partial | Requires provider adapter | Yes | Setup is intentionally blocked/honest | Read/write risks are separately declared. |
| Spotify | Catalog plus legacy adapter | Partial | Requires provider adapter | Yes | Setup is intentionally blocked/honest | Playback control is a write capability and remains separately governed. |
| Home Assistant | Catalog plus legacy adapter | Partial | Requires provider adapter | Yes | Setup is intentionally blocked/honest | Device control remains a governed write capability. |
| Gmail/Drive, Slack, Microsoft 365 | Catalog only for new lifecycle | No live adapter proof located | Requires provider adapter | Yes | Setup is intentionally blocked/honest | These are discoverable future connections, not working integrations. |
| ToolRegistry | Yes | Authority, inventory, approval, MCP, and skill coverage present | Runtime-owned | Yes in capability surfaces/activity | Permission settings only | One inventory records tool origin, risk, permission flags, and approval requirement. Tool execution still requires current Turn authority and durable ToolRun identity. |
| Skills | Yes | Skill lifecycle and verified-outcome coverage present | Materialization-dependent | Yes | Partially | Skills are procedural capability packages, not credentials or completion authorities. Progressive detail/provenance presentation remains incomplete. |

## Voice and presence

| Capability | Source-present | Deterministic evidence in source | Provider-ready | UI-visible | User-configurable | Confirmed boundary or blocker |
|---|---|---|---|---|---|---|
| Browser speech APIs | Legacy migration evidence only | No production caller remains | Not a production provider | No | No | The former `SpeechRecognition` and `speechSynthesis` implementation is non-executable. Chat uses the typed local Voice transport. |
| Local microphone transport | Yes | Source inspection only in this phase | WebView microphone permission required | Yes | User gesture only | Captures mono PCM with echo cancellation, noise suppression, level projection, bounded silence detection, and a two-minute ceiling. Raw microphone audio is not retained. |
| Voice turn bridge | Yes | Source inspection only in this phase | Runtime-owned | Yes | Through Chat Voice controls | A final transcript is stored in `VoiceTransportTurn`, submitted through `/query/stream`, and rebound to the exact request, Execution, and TaskRun. Voice never creates a second Session or semantic runtime. |
| Partial transcript stream | Not active | Batch STT adapters return one final transcript | Requires a true streaming STT adapter | No fabricated interim text | No | The UI does not invent partial words from audio levels. This remains an explicit streaming-transport gap rather than a false capability claim. |
| Sentence playback and interruption | Yes | Source inspection only in this phase | Selected local TTS must be ready | Yes | Read/Voice controls | Streamed reply beats are queued in order, the already-spoken preamble is not repeated, and Stop aborts local playback plus its exact transport turn. Generated playback clips are integrity-checked durable transport artifacts. |
| Voice conversation toggle | Cascaded source path present | No current live proof | Selected local STT/TTS adapters | Yes | Yes | Uses local capture -> final transcript -> canonical Echo loop -> sentence TTS. It is not full-duplex native speech-to-speech. |
| Wake word | Disabled | No runnable wake-word listener located | No | Truthful disabled control | No | No openWakeWord, sherpa, Porcupine, or other listener is attached. This remains Phase 6 work. |
| Windows Speech STT/TTS | Yes | Source inspection only in this phase | Windows PowerShell plus installed System.Speech language/voice | Status and selection are visible | Yes | Uses bounded hidden System.Speech processes. It does not require browser speech services or pywin32. |
| faster-whisper | Adapter present | Source inspection only in this phase | Ready only with installed runtime and explicit existing model path | Status visible | Advanced path plus provider selection | EchoSpeak never downloads a model implicitly. |
| whisper.cpp | Adapter present | Source inspection only in this phase | Ready only with discovered CLI and explicit existing model file | Status visible | Advanced path plus provider selection | Audio and model paths are passed as process arguments, not shell-interpolated commands. |
| Piper | Adapter present | Source inspection only in this phase | Ready only with discovered CLI and explicit existing voice model | Status visible | Advanced path plus provider selection | Produces bounded WAV chunks for the same transport playback queue. |
| PersonaPlex | Disabled compatibility scaffold | Source inspection only | Hard-disabled | Advanced status only | No execution control | PersonaPlex is a conversational speech model, not passive STT/TTS. Its legacy wrapper cannot connect, open the microphone, route tool intent, or replace the selected Session model. |
| OpenAI Audio/Realtime | Credential detection plus explicit opt-in | Source inspection only in this phase | Explicitly not execution-ready | Status and data-path choice are visible | Yes, opt-in/disable only | Credentials never enable upload. Cloud execution remains blocked until a governed adapter and explicit upload/cost approval exist. |
| Desktop Echo companion | Yes | Desktop contract source exists | Tauri window/runtime dependent | Yes | Yes | Reuses the same backend and explicitly selected Session, observes shared Chat reasoning controls, and never creates a Session or TaskRun. Its own live run uses the shared semantic activity decoder; cross-window display of a run started in the main window remains a future projection improvement. |

Phase 5 evaluation rejects a second realtime agent loop. LiveKit and Pipecat
remain uninstalled architecture references; a future adapter may reuse only
their transport, event, endpointing, and interruption concepts behind the
existing `VoiceTransportTurn` boundary. No native speech-to-speech provider is
reported ready.

## Current Settings and product truth

Phase 3 now projects one secret-free card contract from the existing backend
owners through `GET /settings/catalog`. The projection is read-only and cannot
select a model, grant a permission, store a credential, register a tool, or change
Connection state.

- Top-level groups are `Models`, `Search & Research`, `Voice & Speech`,
  `Connections`, `Local Tools`, `Skills`, `MCP`, `Privacy & Permissions`, and
  `Advanced`.
- Model cards are derived from the current Session model binding and the selected
  provider readiness probe. Search cards describe configured acquisition paths.
- Voice cards project `voice_provider_statuses()` and therefore distinguish
  detection/configuration from executable VoiceJob readiness.
- Connection cards continue to use `ConnectionRegistry` and `CredentialBroker`;
  the catalog never receives credential values. Connected means configuration or
  authorization exists. Ready requires a successful health/capability check.
- Connections now use the same catalog and selected-card detail panel as the
  other Settings categories. Capability enablement, probe/reconnect, disable,
  and disconnect controls call the existing revision-checked Connection APIs;
  the former parallel hand-built Connections grid has been removed.
- Obsidian's normal desktop Connect action uses a native folder picker rather
  than exposing a raw path field. Browser development refuses that local-folder
  setup because it cannot safely provide a durable native filesystem path.
- Local Tools and Skills are projections of `ToolRegistry` and `SkillsRegistry`.
  MCP tools remain the same ToolRegistry entries with MCP origin and server lineage.
- Cards expose locality, cost class, data path, scope, declared capabilities, and
  last check time when an owner supplies one. Detail selection is frontend-only
  presentation state and owns no readiness or lifecycle truth.
- Raw credentials, endpoints, paths, commands, environment data, MCP headers, and
  complete runtime JSON remain in the explicitly labelled Advanced compatibility
  editor. That editor was retained because old consumers have not yet been proven
  absent; it is no longer the normal provider page.

Chat still exposes selected Session provider/model, Thinking, effort, Stop, Steer,
Queue, Read, and Voice. The disabled Wake control is an honest later-phase marker.
Local provider cards can select dictation and playback independently. A cloud card
can record explicit opt-in, but cannot upload or execute while its governed adapter
and approval boundary remain absent.

## Phase 0 authority findings

The following ownership should remain unchanged:

```text
Session state             owns active Project and selected model binding
TaskRun                   owns objective, requirements, recovery, and durable work
TaskRunScheduler          owns the next liveness action
ToolRun / ToolOutcome     own execution truth
ResearchArtifact/evidence owns factual provenance
Requirement evaluator    owns research sufficiency
Control-plane gate        owns final response acceptance
ConnectionRegistry       owns connection identity, scope, health, and capability selection
CredentialBroker         owns secret values
Semantic event stream    projects one run into Chat, Visualizer, avatar, and future voice
```

Phase 1 source consolidation now enforces these ownership changes:

1. A canonical search ToolRun performs one provider-attributable acquisition. The
   historical `SearchGrounder` orchestration is unreachable from a correctly bound
   canonical TaskRun, and a missing binding fails closed.
2. `ModelExecutionControlPlane` remains the only answer-acceptance gate. Post-loop
   response handling no longer revalidates completion against a later TaskRun revision
   or replaces an accepted answer through a competing repair stack.
3. `ProactiveEngine` cannot start or create background work. Routines,
   AutomationRuns, Executions, and TaskRuns retain the active lifecycle.

The historical weather/FIFA/SDK liveness scenario must be the first observable
acceptance scenario when validation is authorized. Until then, the current liveness
repairs remain source-present with deterministic coverage, not live-proven.
