# EchoSpeak Six-System Implementation Ledger

> Historical implementation ledger. Current EchoSpeak 8.0 runtime and UI
> ownership are documented in `SYSTEM_ARCHITECTURE.md` and
> `ECHO_CORE_CONTRACT_MATRIX.md`.

Last updated: 2026-07-14

## Objective

Close the coordinated redesign of research, memory/context, the coding harness and Editor removal, Studio/Chat presentation, retrieval, and Automations/Connections while preserving EchoSpeak's runtime-authority and Project/Session isolation contracts.

## User decisions

- The Python runtime is authoritative; React/Tauri surfaces are projections.
- The active Session provider/model is the default for every model-driven domain.
- One logical operation has one canonical execution identity and one ToolRun.
- The built-in image/video Editor is to be removed; Media and generation remain.
- Obsidian is optional and governed, never a second authority or bundled dependency.
- Detailed operations remain observable in Studio/Viewer, not persistent Chat cards.
- Existing dirty-worktree work and user data must be preserved.
- Tests use disposable runtime roots and synthetic Projects.
- Do not modify `2d-shooter-game`; do not commit or push.

## Invariants

1. Project and Session authority is explicit and revalidated at consequential boundaries.
2. Canonical JSON stores fail closed and quarantine malformed state.
3. Projections (UI, vectors, Markdown, caches, summaries) are rebuildable and non-authoritative.
4. Mutations bind stable identity, exact arguments/target, authority, source revision, approval, expiry, and consumed state.
5. Heartbeat schedules and claims work; it never executes external effects directly.
6. Models may plan and synthesize but may not grant authority, approve themselves, or establish completion through prose.
7. No hidden chain-of-thought is persisted.
8. Same-model defaults are visible; alternate-model fallback must be explicit and governed.

## Baseline architecture and worktree

- Desktop: Tauri 2/Rust host with React/Vite WebView2 UI and a PyInstaller Python sidecar under `apps/desktop`.
- Runtime: FastAPI/Uvicorn and the agent runtime under `apps/backend`.
- Web projection: React application under `apps/web` reused by browser and desktop entry points.
- Existing canonical work includes Project/Session/Turn/ToolRun authority, typed context assembly, bounded Echo Resolution, governed approvals, TaskStore/Routines/Heartbeat integration, SkillsRegistry governance, Media, and image/video editor subsystems.
- Baseline Git status is intentionally dirty: 55 tracked files modified plus new desktop, context, resolution, task, media, image-editor, startup, voice/generation, and test files. No reset or cleanup is authorized.
- No `AGENTS.md` instruction file was found in the repository audit; `docs/AGENT.md` is product documentation, not a repository instruction override.

## Duplicate authorities and deletion candidates

The read-only audit proved the following competing owners and obsolete paths:

- Built-in image/video Editor routes, stores, APIs, tools, workers, startup checks, and frontend workspaces. Preserve MediaAsset and generation/runtime infrastructure with real non-Editor callers.
- Quarantined `deep_search_workflow.py`, `search_plan.py`, `search_provider.py`, `evidence_store.py`, and unused `research_context.py` are a second unfinished research stack. Production remains on `SearchGrounder`; the useful typed concepts must be absorbed before deletion.
- `RoutineManager` and `HeartbeatManager` both schedule work; `/trigger/cron`, `/trigger/webhook`, `ProactiveEngine`, A2A Tasks, and Twitter autonomous mode can still call `process_query()` outside a canonical automation Run/lease.
- `todo_manage` still reads/writes `todos.json` directly instead of using `TaskStore`; old server todo helpers remain fail-open.
- There is no canonical Automation Run or Connection owner. Routine `last_*` fields, Task execution links, MCP/configured integrations, and service-specific state are partial projections.
- Memory vector/Markdown/profile projections that can still be mistaken for canonical records.
- `SessionMemoryDistiller`, `agent_lessons.json`, direct `profile.json` writers, session-only memory, and pending-memory confirmation paths carry semantics without the full canonical MemoryCurator record contract.
- Frontend execution/evidence cards shown persistently in Chat after final synthesis.

Concrete isolation defects to close:

- Memory semantic-key replacement, curator dedupe, compact, list, and skill retrieval can cross Project boundaries because canonical `project_id` is absent or scope is applied after matching.
- Document graph expansion can reintroduce chunks from another Project after its first scope filter.
- Research artifact read/consume routes do not consistently require exact Project/Session authority, and malformed artifacts are silently skipped.
- Studio Overview returns all Tasks and Routines even while presenting one active Project/Session.
- Task idempotency deduplicates the record but does not atomically claim a single execution, so concurrent triggers can execute twice.

## Migration requirements

- Inventory existing Editor documents and their referenced MediaAssets before disabling Editor readers/writers.
- Provide an idempotent Editor archive/migration that retains original MediaAssets and records unsupported editor-only operations without deleting runtime data.
- Version new research, memory, connection, Routine/Task/Run, and coding-ledger records.
- Treat legacy indexes and Markdown files as rebuildable projections after canonical import.
- Validate migrations only against disposable copies before any real-data path is enabled.

## Phased plan

1. Complete code/data/caller audit and lock shared contracts.
2. Add shared typed identity, provenance, migration, same-model, recovery, and Connection contracts.
3. Implement typed research routing/live adapters/artifacts and workspace projection.
4. Implement typed memory classes/retrieval/consolidation, Memory Studio, and optional Obsidian adapter.
5. Harden durable coding execution ledger/resume, then migrate and remove built-in Editor while retaining Media/generation.
6. Professionalize Studio navigation/surfaces and simplify Chat presentation.
7. Unify Automations, Tasks/Runs, schedules, Heartbeat, and Connections.
8. Run focused, broad, build, packaged/native, and live-model acceptance; review final diff.

## Acceptance criteria

- Exact live-domain routing never treats embedding/search snippets as authoritative current values.
- Deep research has bounded branches/budgets, durable evidence and claim records, restart/resume, citations, and contradiction/gap handling.
- Memory is typed, scoped before relevance, temporal, inspectable, isolated, and written durably only by MemoryCurator.
- Coding can checkpoint/resume a multi-phase objective without duplicate mutations or ToolRuns.
- No built-in Editor route/navigation/API/startup authority remains; Media and configured image/video generation continue working.
- Studio navigation remains usable at narrow, standard, maximized, fullscreen, and DPI-like widths.
- Normal Chat contains messages plus one transient operational status; durable evidence remains available elsewhere.
- One trigger creates at most one Run through durable idempotency/lease/recovery semantics.
- Full backend/web/desktop regression and release builds pass, or remaining failures are classified with evidence.

## Tests and validation ledger

Baseline inherited from the immediately preceding pass:

- Backend: 612 passed, 1 skipped, 1 known deprecation warning.
- Web: typecheck passed; 15 files / 48 tests passed; production build passed with the known large-chunk warning.
- Desktop: 10 contract tests passed; Rust fmt/check/test/clippy passed; sidecar, release executable, NSIS, and MSI builds completed.
- Native disposable acceptance reached local-service readiness, verified no phantom Sessions across views, created one explicit Session, and completed an LM Studio response. The user stopped further manual acceptance and will continue it.

New commands and outcomes will be appended after each phase.

## Risks and unresolved questions

- Existing Editor data shape and non-Editor callers must be proven before deletion.
- Live structured providers may require credentials; deterministic fixtures and honest unavailable states are required.
- External creative applications and Obsidian need user-approved Connections; no application installation or authentication is implied.
- The current worktree contains substantial uncommitted implementation that must be integrated rather than broadly rewritten.
- Current real-data inventory (metadata only): 119 legacy video-editor JSON files across two Project identifiers (838,550 bytes), no media binaries in that store, three research-artifact JSON files, eight memory-root files, and no image-editor document files. Legacy Editor records must remain readable only to the migration/export path until the user exports or archives them.

## Progress

- [x] Read the complete six-system product brief.
- [x] Captured baseline Git status and repository structure.
- [x] Started bounded read-only audits for research/memory, UI/Editor/Chat, and Automations/Connections.
- [x] Completed research/memory and automation/connection caller/data-owner audits.
- [x] Completed UI/Editor/Chat/coding-harness caller and retention audit.
- [x] Proved Editor migration inventory: 33 documents, 86 revisions, 10 referenced assets, 9 present/hash-valid assets, 1 missing asset, 1 artifact, and no jobs across two legacy Project identifiers. Source data remains untouched.
- [x] Architecture redesign baseline committed and pushed on branch `echospeak-8.0` (`ddec8ea`).
- [x] Editor routes/UI removed; Media library + generation paths preserved.
- [x] Chat presentation helpers: calm transcript (errors only), `mergeFinalReply`, stale Session/Project final guard with `activeProjectIdRef` + send-time ownership.
- [x] Studio navigation: dual scroll arrows restored, overflow scroll for all 14 sections including Automations/Connections/Services.
- [x] Studio Memory metadata (scope/project/confidence/source), Tools lifecycle badges, Soul identity surface, Automations heartbeat/run honesty, Connections real backend list.
- [x] Research Feed renders durable ResearchArtifact fields (plan/branches/sources/evidence/claims/gaps/contradictions/synthesis) when present.
- [x] Full backend suite from disposable root: **580 passed, 1 skipped** (2026-07-14).
- [x] Frontend: typecheck, 13 files / 48 tests, production build (large-chunk warning only).
- [x] Desktop: cargo check/clippy/fmt, 10 contract tests; release host launches sidecar on loopback with disposable `ECHOSPEAK_DESKTOP_*` dirs.
- [ ] Full interactive native Chat (model response / Session switch mid-stream) remains manual — requires configured provider (e.g. LM Studio).
- [ ] Packaged installer install/uninstall matrix and multi-DPI visual Studio nav proof remain manual.

## Validation results (2026-07-14 finish pass)

| Surface | Result |
|---------|--------|
| Web typecheck | pass |
| Web vitest | 13 files, 48 tests pass |
| Web `npm run check` | pass |
| Web production build | pass (~914 kB JS chunk warning) |
| Backend focused keyword suite | 112 passed / 469 deselected |
| Backend full suite (disposable `ECHOSPEAK_DATA_DIR`) | **580 passed, 1 skipped**, 1 deprecation warning |
| Editor retirement + retired architecture | 9 passed (subset of full suite) |
| Media library tests | 3 passed |
| Desktop contract tests | 10 passed |
| Cargo check / clippy `-D warnings` / fmt `--check` | pass |
| Native release launch | process up; Uvicorn `127.0.0.1:<ephemeral>`; `/health` 200 from host; readiness + hydrate routes 200; unauthenticated protected routes 401 |

## Changed files for this objective

- `docs/SIX_SYSTEM_IMPLEMENTATION_LEDGER.md` — progress and validation record.
- `apps/web/src/index.tsx` — Chat calm timeline, Studio nav, Memory/Tools/Soul/Automations/Research polish, project-ownership refs.
- `apps/web/src/chatPresentation.ts` / tests — presentation + ownership helpers.
- `apps/web/src/studioNavigation.ts` / tests — section order + keyboard navigation.
- `apps/web/src/vitest.d.ts` — expect matchers used by tests.
- Prior 8.0 baseline commit includes backend Media, Connections, Automations, Editor retirement, desktop shell, and contract docs.
