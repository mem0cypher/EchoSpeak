# EchoSpeak Integrations

This document describes the current external-capability boundary. Integrations
extend Echo; they do not become alternate agents, state owners, or completion
paths.

## Ownership

- `ConnectionRegistry` is the sole owner of secret-free connection identity,
  lifecycle, health, capability metadata, and scopes.
- `CredentialBroker` stores credential payloads with Windows current-user
  DPAPI. Settings and Connection records retain opaque references only.
- The provider catalog describes setup options; it is not a connection-state
  store. `ALLOW_*` configuration remains a compatibility permission input and
  is not proof that a provider is connected or healthy.
- `ToolRegistry` is the authoritative inventory of executable capabilities.
- `SkillsRegistry` describes bounded workflows over registered capabilities.
- MCP servers remain external providers whose tools enter the same registry,
  permission, approval, ToolRun, and verification boundary.
- `TaskRun` owns the user's objective and requirements.
- `ToolRun`/`ToolOutcome` own execution truth.
- `ResearchArtifact` owns retained evidence/provenance.
- The canonical runtime owns sufficiency and finalization.

Credentials are resolved only at a provider/transport boundary. They are never
copied into TaskRuns, model prompts, frontend state, logs, public Settings,
Connection projections, or SpecialistRuns. Existing plaintext Settings secrets
are migrated into the broker before their legacy file is cleared.

## Connection lifecycle

The consumer Connections catalog joins static provider descriptions to
Project/Session-scoped `ConnectionRegistry` records. Begin, probe, capability
management, reconnect, disable, and disconnect operations update that registry
with revision checks. Disconnect also removes its credential reference and
runtime-owned tools.

The broader `GET /settings/catalog` endpoint is a read-only, secret-free product
projection. It may describe connection, model, search, voice, tool, skill, and
MCP state but cannot authorize, probe, register, or execute anything. Connected
means that configuration or authorization exists. Ready means the corresponding
owner has reported executable readiness. The catalog projection never receives
raw keys, endpoints, local paths, commands, environment values, or MCP headers.
An explicit desktop folder picker may return one user-selected local path directly
to the bounded authorization request; that path is not stored in catalog state.

Obsidian/local-vault setup and custom MCP transport foundations are active.
Existing GitHub, Notion, Google Calendar, Spotify, and Home Assistant Settings
are projected as explicit legacy/manual records. Their browser OAuth adapters
remain unavailable until each provider implements state/redirect validation,
code exchange, refresh, revocation, and narrow incremental scopes. The UI must
show this honestly and must not manufacture a connected state.

## Web and structured retrieval

`SearchGrounder` is the one general web-search orchestrator. Its configured
provider adapters normalize discovery results. `safe_web_fetch` performs
bounded public HTTP/HTTPS retrieval with redirect/address validation, private
network blocking, content-size/type/time limits, no browser credentials, and
untrusted-content handling. Interactive browser automation remains a separate,
approval-sensitive capability.

Weather, sports, finance, calculation, memory, and other structured tools are
preferred when their declared fields match the requirement. Generic search
success is discovery, not automatic evidence sufficiency.

## MCP

MCP uses the official Python SDK rather than EchoSpeak-owned JSON-RPC framing.
The active client negotiates protocol `2025-11-25` with standard stdio and
Streamable HTTP transports; legacy SSE remains an explicit compatibility
transport. Tools, resources, resource templates, prompts, pagination, list
change signals, progress, cancellation, rich content, structured content, and
input/output schemas stay typed through the SDK boundary.

Echo snapshots current server health and tool inventory into per-Turn
authority. Every tool call is revalidated against the current inventory before
execution. Capability policy is per tool. Unknown tools default to governed
write/action behavior; a server-wide trust flag cannot make them safe. A
server-provided read-only annotation is accepted only when the reviewed server
configuration explicitly opts into those hints.

Content received from an MCP server is untrusted data and cannot grant itself
more authority, approve a mutation, alter the Session model, or answer outside
the canonical finalization gate.

## Specialist runtimes

Codex App Server and OpenCode are specialist agent runtimes, not ToolRegistry
functions and not Echo model providers. They own their internal coding threads
and loops. Echo's `SpecialistRunStore` retains only the bounded bridge needed to
correlate their work to one TaskRun requirement and graph node.

Codex uses JSON-RPC over stdio. OpenCode uses authenticated loopback HTTP/SSE
and requires the explicit unsandboxed-host opt-in plus current file/terminal
permissions. Claude Code is configuration/discovery only until a genuine
executable permission bridge is implemented.

## Channels and automations

Discord, Telegram, Twitch, Twitter/X, routines, and heartbeat occurrences enter
the same canonical semantic runtime. They do not execute email, WhatsApp, or
other actions directly. Background occurrences retain an exact AutomationRun,
Execution, TaskRun, ToolRun, and delivery lineage.

## Media and voice

Image/video generation jobs, voice jobs, and the media library retain their
domain stores and bind governed executions to the current Session, TaskRun,
requirement, attempt, and ToolRun when applicable. The Media Visualizer panel is
a read-only projection. Voice and Companion settings configure input/output
behavior; they do not create a separate agent identity or execution runtime.

Chat Voice uses a local-first cascaded transport: user-gesture microphone
capture, an explicitly selected local STT adapter, the same canonical Session
query path, and ordered local TTS chunks. Windows System.Speech, faster-whisper,
whisper.cpp, and Piper report Ready only when their required local runtime and
explicit model/voice files are present. No model is downloaded implicitly. Raw
microphone audio is not retained; final transcripts and exact transport lineage
are durable. Cloud Voice is visible only as an explicit opt-in/data-path choice
until a reviewed upload/cost approval and execution adapter exist.

## Adding an integration

An integration is acceptable only when it:

1. declares a narrow capability and typed inputs/outputs;
2. registers executable actions in `ToolRegistry`;
3. preserves Session/Project scope and current model binding;
4. uses existing permission and exact approval checks;
5. creates durable ToolRuns for execution;
6. returns structured result state and provenance;
7. treats external content as untrusted;
8. has bounded cancellation/retry behavior;
9. remains projection-only in the UI;
10. cannot create a second completion or final-response path.

Package installation, subagent execution, Hubs, wallets, and swarms are not
active integrations and must not be presented as completed product systems.
