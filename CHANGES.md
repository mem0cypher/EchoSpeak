# Changes

## v7.1.0 — Inline Code Diff, Accept/Decline Flow, and Efficient Editing

### Frontend
- Added **single-file inline code diff** (`InlineCodeDiff.tsx`) — file edits are now shown in a unified one-pane diff view with green-highlighted additions and red-highlighted deletions (with strikethrough), replacing the old two-tab original/edited snapshot approach. Full file content is always visible.
- Added **Accept / Decline buttons** directly in the diff view header when a `file_write` action is pending confirmation — wired to the existing `sendText("confirm")` / `sendText("cancel")` approval flow, so users can approve or reject changes without leaving the Code panel.
- Replaced `codeBlocks` snapshot array with a **per-file session model** (`codeSessions`) — each file tracks `originalContent`, `currentContent`, `status` (read/draft/saved/output), and `pendingConfirmation`, enabling proper diffing within a single logical file view.
- Added **Context Ring** widget in the chat input bar — a circular SVG gauge showing estimated token usage as a percentage of the provider's context window, with color-coded thresholds (blue < 60%, amber 60–85%, red > 85%) and a hover tooltip showing token counts.
- Code panel now shows **status pills** per file: "Read", "Draft changes", "Awaiting save", "Saved", "Output" with color-coded backgrounds.
- Active file selection stabilized via `latestCodeFilenameRef` to prevent stale tab selection during async state updates.
- Code-related state (`codeSessions`, `activeCodeTab`, `latestCodeFilenameRef`) resets correctly on thread switches.

### Backend
- File-edit pipeline now uses **SEARCH/REPLACE blocks** instead of full-file rewrites — the LLM outputs only the changed sections using `<<<<<<< SEARCH / ======= / >>>>>>> REPLACE` syntax (same pattern used by Aider, Codex, and Cursor). Typical token savings: **80–95%** on edits to large files.
- Added `_parse_search_replace_blocks()` parser and `_apply_search_replace()` applicator with exact-match-first and fuzzy whitespace-normalized fallback matching.
- **Automatic fallback**: if the LLM doesn't produce valid SEARCH/REPLACE blocks or all blocks fail to match, the pipeline falls back to the old full-file rewrite prompt — so editing never breaks.
- Confirmed `file_write` actions now emit the correct file path (from `kwargs`) as `tool_start` input, not the user's original prompt — ensures the frontend maintains stable per-file session context after approval.

### Workspace Explorer
- Added **`WorkspaceExplorer`** React component (`WorkspaceExplorer.tsx`) — a visual file tree browser showing the agent's current `FILE_TOOL_ROOT` directory with recursive folder expansion, file icons by extension, size labels, and permission badges (WRITE / TERM).
- Added **"📂 Files" tab** as a permanent first tab in the Code panel — always accessible even when code sessions are active, so users can see the workspace file tree alongside inline diffs.
- Added **"cd" button** in the workspace header to change the working directory at runtime — input an absolute path and Echo's `FILE_TOOL_ROOT` updates immediately without restart.
- Added **refresh button** to re-fetch the file tree on demand.

### API
- Added **`GET /workspace`** endpoint — returns the current `FILE_TOOL_ROOT`, a recursive file tree (max depth 2, max 150 items), `display_name`, and permission flags (`writable`, `terminal`).
- Added **`POST /workspace`** endpoint — changes the `FILE_TOOL_ROOT` at runtime to a new absolute directory path. Validates the path exists and is a directory.
- Added **`GET /workspace/browse`** endpoint — browses a specific subdirectory within the workspace, returning a shallow file listing for drill-down navigation.

### Docs
- Updated CHANGES.md, ROADMAP.md, README.md, ARCHITECTURE.md, AUDIT.md, docs/AGENT.md, docs/INTEGRATIONS.md, docs/GETTING_STARTED.md, and TEST_RUNDOWN.md for v7.1.0.

## v7.0.0 — Reflection Loop, Live Task Checklist, and Tool Testing

### Backend
- Added a general-purpose **ReflectionEngine** (`agent/reflection.py`) that evaluates tool results between steps in multi-task plans. Per-step reflection asks "Does this result satisfy what we need?" and post-plan reflection asks "Did the overall execution match user intent?" Anti-loop guards enforce max 2 reflection cycles per step, skip trivial tools (`get_system_time`, `calculate`, `project_update_context`), and bypass reflection for small plans or substantial results.
- Integrated `ReflectionEngine` into **`TaskPlanner.execute_next_task()`** — after each tool call, the engine evaluates the result and can trigger a retry with adjusted parameters (e.g., refined search query) if the result is insufficient.
- Added **post-plan reflection** in `TaskPlanner.execute_all()` — after all tasks complete, the engine evaluates whether the overall execution accomplished the user's goal.
- Added **result passing between dependent tasks** via `_resolve_dependent_params()` — tasks can reference previous task results using `{{prev_result}}` placeholders, and empty message/content params auto-inject the dependency result.
- Added three new **NDJSON stream event types** to `StreamBuffer` (`agent/stream_events.py`): `task_plan` (full decomposed plan), `task_step` (per-step status changes: pending → running → done/failed/retrying), and `task_reflection` (reflection evaluation results).
- `TaskPlanner` now emits `_emit_task_plan()` at plan start and `_emit_task_step()` / `_emit_task_reflection()` during execution, enabling real-time frontend rendering.

### Frontend
- Added **`TaskChecklist`** React component (`apps/web/src/components/TaskChecklist.tsx`) — renders an inline live checklist in the chat showing real-time task plan progress with animated status icons (○ pending, ● running, ✓ done, ✗ failed, ↻ retrying, ⏸ awaiting confirmation), result previews, and reflection notes.
- Added `taskPlanReducer` for state management — processes `task_plan`, `task_step`, and `task_reflection` stream events into a `TaskPlanState` object.
- Wired NDJSON stream handler in `index.tsx` to dispatch task events to the `TaskChecklist` component.
- `TaskChecklist` renders above the chat timeline when a plan is active and resets on thread switch.

### Tests
- Added **`tests/test_reflection.py`** with comprehensive test coverage:
  - `TestReflectionEngineHeuristics` — trivial tool skipping, plan size thresholds, result length, failure signals, max cycle enforcement
  - `TestReflectionEngineStepReflection` — ACCEPT/RETRY parsing, ambiguous defaults, LLM failure fallback, cycle budget enforcement
  - `TestReflectionEnginePlanReflection` — ACCOMPLISHED/FAILED/PARTIAL parsing, empty task handling
  - `TestReflectionEngineRetryParams` — web_search query refinement, browse_task URL suggestion, unknown tool fallback
  - `TestTaskPlannerReflectionIntegration` — lazy engine init, stream event emission for task_plan/task_step/task_reflection
  - `TestTaskPlannerDependentResults` — `{{prev_result}}` placeholder resolution, auto-injection, independent task passthrough
  - `TestStreamBufferTaskEvents` — push_task_plan, push_task_step, push_task_reflection, result preview truncation

### Docs
- Updated CHANGES.md, ROADMAP.md, README.md, ARCHITECTURE.md, AUDIT.md, docs/AGENT.md, docs/INTEGRATIONS.md, docs/GETTING_STARTED.md, and TEST_RUNDOWN.md for v7.0.0.

## v6.7.0 — Unified Update Awareness, Twitter/Twitch Presence, and Source Safety

### Backend
- Added a shared **Update Context Layer** (`agent/update_context.py`) with `UpdateContextService` and `UpdateContextPlugin` that detects update-intent queries ("what changed?", "what's new?", "any updates?") and injects deterministic, repo-backed update context (recent commits, changelog highlights, optional diff summary) into the `process_query()` pipeline via the Stage 2 `on_context` plugin hook.
- Added a new **read-only `project_update_context` tool** (`agent/tools.py`) decoupled from `ALLOW_SELF_MODIFICATION` — any source (Web UI, Discord, Twitter, Twitch) can now introspect recent project changes without needing self-modification permissions.
- Separated **read-only update introspection** from **self-modification safety gates**: generic "what changed?" queries now route to the safe `project_update_context` tool instead of the privileged `self_git_status` tool.
- Extended `ContextBundle` with `update_context` and `update_intent` fields for downstream pipeline consumers.
- **Source role hardening**: Twitter mentions and Twitch chat now resolve to `PUBLIC` role; `twitter_autonomous` stays `OWNER`-level for full tool access during autonomous tweet generation.
- Discord server assistant mode tool allowlist now includes `project_update_context` for update-query parity.
- Plugin dispatch in Stage 2 now passes `source` and `agent` into context plugins for source-aware context injection.

### Twitter Bot
- Refactored **autonomous tweet generation** (`twitter_bot.py`) to use the shared `UpdateContextService` for prompt enrichment instead of bespoke recent-commit/diff assembly.
- Refactored **changelog tweet prompts** (`agent/git_changelog.py`) to reuse the shared update-context service with the existing manual summary as fallback.
- Autonomous tweets now go through the full agentic `process_query(source="twitter_autonomous")` pipeline with tools, memory, and grounded update context.

### Tests
- Added **source-parity regression tests** (`tests/test_echospeak.py`) verifying:
  - Web UI update queries route to safe `project_update_context` without self-modification
  - Discord server update queries use the same safe tool
  - Twitter mention queries get public-safe update context injection
  - Public social sources (Twitter, Twitch) resolve to PUBLIC role
  - Autonomous Twitter prompts are enriched via the shared update-context service

### Docs
- Updated CHANGES.md, ROADMAP.md, README.md, ARCHITECTURE.md, AUDIT.md, docs/AGENT.md, docs/INTEGRATIONS.md, docs/GETTING_STARTED.md, and TEST_RUNDOWN.md for v6.7.0.

## v6.6.0 — Tavily-Only Search, Browser-Only Voice, and Cleanup Audit

### Backend
- Removed deprecated non-browser voice paths so backend Pocket-TTS and local STT surfaces no longer participate in runtime behavior.
- Stubbed `apps/backend/io_module/stt_engine.py` and `apps/backend/io_module/pocket_tts_engine.py` to fail clearly if old code paths are invoked.
- Simplified active web search to Tavily-only and removed stale non-Tavily compatibility surfaces from active routing, tool metadata, and persisted settings.
- Split persisted runtime configuration so non-secret overrides stay in `apps/backend/data/settings.json` while secret-bearing overrides move to `apps/backend/data/settings.secrets.json`.
- Removed deprecated non-browser voice and non-Tavily search dependencies from `apps/backend/requirements.txt`.
- Cleaned archived Phase 3 trace artifacts so checked-in audit data no longer advertises removed `live_web_search` executions.

### Frontend
- Removed stale Web UI settings and controls for Pocket-TTS, local STT, live search preference, SearxNG, Scrapling, and other removed search-provider toggles.
- Simplified browser voice handling in `apps/web/src/index.tsx` so speech input/output is browser-native only.
- Kept research and tool event UI aligned to `web_search` as the only active search tool.

### Docs and QA
- Updated the main documentation set to describe Tavily-only web search and browser-only voice behavior.
- Added a lightweight regression script for no-confirmation checks plus an updated manual checklist for confirmation-gated flows.
- Verified focused backend regressions and web typecheck/build after the cleanup.

## v6.5.1 — Backend Routing, Latency, and Discord Read Stability

### Backend
- Added deterministic fast paths for capability/help prompts so questions like `what can you do right now?` return directly instead of drifting into slow tool-enabled LangGraph runs.
- Added deterministic fast paths for explicit `remember ...` prompts, plus structured profile/preference recall for common memory questions.
- Changed `apps/backend/agent/core.py` `_allowed_lc_tool_names()` so ordinary chat now defaults to **no tools** unless there is explicit tool intent or a concrete `_find_tool()` match.
- Suppressed stray time-context injection for capability/help and explicit memory-save prompts.
- Serialized `process_query()` with a request-level lock and taught proactive background tasks to skip themselves while the agent is busy, preventing concurrent state corruption and long stalls.
- Hardened `discord_read_channel()` with readiness checks and a shorter fail-fast timeout so stalled channel reads return quickly instead of hanging the request for ~30 seconds.

### Verification
- Verified live `/query` and `/query/stream` behavior for capability/help prompts, profile recall, explicit memory-save prompts, and deterministic preference recall.
- Confirmed Discord recap requests still depend on Discord history fetch health, but now fail fast with a short timeout instead of stalling the API path.

## v6.5.0 — Phase 3 Control Plane, Approval Center, and Trace Persistence

### Backend
- Added `apps/backend/agent/state.py` as a persistent Phase 3 state store for `ApprovalRecord`, `ExecutionRecord`, `ThreadSessionState`, and JSON trace persistence under `data/phase3/`.
- Rewired `apps/backend/agent/core.py` so pending actions, confirmation lifecycle, execution status, trace finalization, provider/workspace/project state, and approval hydration flow through the shared state store.
- Extended `apps/backend/api/server.py` so `/query` and `/query/stream` return execution metadata and thread state, and made `/history` plus project activation/deactivation thread-aware.
- Added explicit control-plane endpoints for `/pending-action`, `/approvals`, `/executions`, `/threads/{thread_id}/state`, and `/traces/{trace_id}`.
- Wired `apps/backend/agent/orchestrator.py` into the execution ledger so orchestration plans and sub-tasks receive explicit execution IDs.

### Frontend
- Added Phase 3 web types for thread session state, approvals, executions, and traces in `apps/web/src/index.tsx`.
- Added Approval and Executions tabs backed by the new backend control-plane endpoints.
- Updated stream final-event handling so execution IDs, trace IDs, and thread state are synchronized into the UI as runs complete.
- Switched thread/project actions in the web shell to thread-scoped backend APIs.

### Verification
- Verified backend Python syntax for the Phase 3 backend files using `ast.parse`.
- Verified the web shell compiles with `npm run typecheck`.

## v6.4.0 — Phase 2 Research Lane & Evidence Model

### Backend
- Added `apps/backend/agent/research.py` to normalize research tool output into explicit research runs and evidence objects.
- Added `research` payloads to streamed tool events, streamed final events, and the non-stream `POST /query` response.
- Added recency metadata (`mode`, `recency_intent`, `recency_bucket`, parsed publication timestamps) so recent/news research can be treated differently from evergreen retrieval.

### Frontend
- Extracted the research lane into `apps/web/src/features/research/` modules:
  - `types.ts`
  - `store.ts`
  - `buildResearchRun.ts`
  - `ResearchPanel.tsx`
- Rewired `apps/web/src/index.tsx` to consume structured research runs from the backend instead of rebuilding research state from raw search strings.
- Updated the Research tab and visual research preview to render explicit evidence records.

### Tests
- Added backend regression coverage in `apps/backend/tests/test_phase2_research.py`.
- Added frontend regression coverage in `apps/web/src/features/research/buildResearchRun.test.ts`.
- Verified backend tests with the backend venv and verified frontend `npm run check`.

## v6.3.0 — Phase 1 Platform Integrity Tranche

### Backend
- Removed the duplicate `POST /query` route definition from `apps/backend/api/server.py`.
- Added `default_cloud_provider` to the backend config model so OpenAI vs Gemini selection persists cleanly outside the live API process.
- Made provider switches persist back into the runtime settings control plane.
- Aligned `get_llm_config()` and non-API agent startup paths with the persisted cloud provider selection.
- Relaxed embedding validation from a hard error to a warning when local fallback embeddings are available.

### Onboarding
- Reworked `apps/onboard-tui/src/index.tsx` to write backend runtime settings instead of creating a separate `~/.echospeak` configuration surface.
- Added `Safe` and `Advanced` onboarding profiles.
- Kept all system-action permissions off by default.
- Added backend health validation before onboarding marks startup complete.

### Frontend
- Extracted `SquareAvatarVisual` into `apps/web/src/components/SquareAvatarVisual.tsx`.
- Extracted research normalization into `apps/web/src/features/research/buildResearchRun.ts`.
- Updated `marketing.tsx` to depend on shared UI instead of `index.tsx` internals.
- Added frontend quality-rail scripts: `typecheck`, `test`, `test:run`, and `check`.

### Tests
- Added backend regression coverage in `apps/backend/tests/test_phase1_integrity.py`.
- Added frontend research normalization test coverage in `apps/web/src/features/research/buildResearchRun.test.ts`.
- Verified backend regression tests with the backend venv.
- Verified frontend TypeScript compilation with `npm run typecheck`.

### Remaining Phase 1 Follow-up
- Install/update frontend dev dependencies so the new test runner can be executed locally.
- Continue extracting domain modules and state slices from `apps/web/src/index.tsx`.
- Expand regression coverage around settings persistence, permission rails, and onboarding flows.
