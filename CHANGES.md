# Changes

## Architecture note — unified coordination (docs)

**Principle:** optimize for the next subsystem being easy to build, not for the
next demo. Reduce places EchoSpeak must *guess* (status, permission, resume,
completion). Capture: `docs/UNIFIED_COORDINATION.md` (linked from Runtime
Contracts, ARCHITECTURE, README).

---

## v7.6.10+ - Coding reliability + Video Editor foundation (in progress)

**Status: implemented in code + unit/frontend tests; pending browser acceptance.**
Not closed until Lifecycle §11, Runtime §K, and **Runtime §K-video** pass.

### File-count reconciliation (do not use “~20 files”)

On branch `feature/v7.6.10-runtime-lifecycle-honesty` after this pass:

- **30** tracked modified files  
- **24** untracked files (expanded)  
- **54** total change-set paths  
- **40** porcelain lines (untracked dirs collapse children)

Earlier “20 modified files” reports were incorrect under-counts.

### Coding reliability (strengthened)
- Exact named files win over stale heuristics; supporting reads ≠ write targets  
- Reads fail closed (truncation, binary, wrappers, encoding)  
- Write/copy/move/delete/mkdir preconditions; atomic write + read-back  
- Approvals revalidate authority; Project roots not rewritten by ActiveWork  
- Single coding-readiness owner; disposable fixture workflow tests  
- Tests: `test_coding_fixture_workflow.py`, `test_coding_readiness_final.py`

### Video Editor foundation (architecture + shells)
- Domain: `agent/video_editor/*`, API `api/video_editor.py`, UI `features/video-editor/*`  
- Models, rational time, Project-bound ingest, timeline ops, revisions, undo/redo  
- Manual + agent-proposed ops; approval-bound mutations; parent/child ToolRuns  
- Adapter registry + durable job shells — **generation not functional**  
- Architecture: `docs/VIDEO_EDITOR_ARCHITECTURE.md`  
- **Deferred:** real playback, WebCodecs preview, proxies, FFmpeg export, analysis,
  captions, live generation, OTIO, C2PA, tracking/AI effects  

### Next (product order)
1. Browser K-video acceptance only (no new features)  
2. Playback + proxies + FFmpeg preview/export  
3. Generative adapters after ordinary footage path is solid  

---

## v7.6.10 - Runtime contracts / lifecycle truthfulness (in progress)

**Status: implemented in code (partial); pending live validation** — not closed
until `docs/LIFECYCLE_TRUTHFULNESS.md` §11 **and** `docs/RUNTIME_CONTRACTS.md` §K
pass. Unit tests alone are not acceptance.

### Canonical docs (do not fork rules here)

| Doc | Owns |
|-----|------|
| **`docs/RUNTIME_CONTRACTS.md`** | Equal models; Mode/Project/permissions; Project+Code lifecycle; refresh hydration; search/utility/references; coding targets; streaming/concurrency; approval-scope identity; **Known limitations**; live gate §K |
| **`docs/LIFECYCLE_TRUTHFULNESS.md`** | Recovery evidence (I.1); confirm types A–D (I.2); ToolRun truth; projection/status (I.3); corruption; truthful finals |

### Documented but not “closed”
- Equal model access (no local/small gates; real context window)
- Chat ≠ workspace; Project remains in chat; `/capabilities` Session-bound; no TOOLS.txt hard ceiling
- Attach/switch/detach/delete scope transaction; Code workspace real Project state
- Full Turn hydrate after refresh (ToolRuns, research, approvals, verification)
- Utility ≠ research; one search row; offered actions; durable claims for double-check
- Placeholders rejected; list→read; no understand/mutate without ToolRuns
- Client request_id ↔ Turn; Session-switch abort; process lock
- **Gap called out:** explicit `index.html` edit retargeted to `game.js`; supporting reads must not become write targets; approval must use stable project/session/path/hash; Studio/capabilities refresh must not cancel pending approval
- Known debt: ToolOutcome→text, dual search stacks, `core.py` size, tool bypasses, approval revalidation, missing transition tests

### Explicit non-goals
- UI redesign; Project authority loosen; editing user’s `Desktop/2d-shooter-game` during repair

---

## v7.6.9 - Code visualizer + file read/write payload pipeline

### Live bugs
- Code pane showed “Loaded 15 chars” / summary only — no real source
- `tool_end` truncated non-search tools to **800** chars; `tool_start` input to **600**
- `file_write` returned only `Wrote N chars to path` (UI had nothing to render)
- `file_read` default max 4k; errors opaque (“File not found.”)

### Fix
- `<<<ECHO_FILE path=…>>>…<<<END_ECHO_FILE>>>` on read/write so UI gets real bodies
- Stream limits: coding tools up to 120k input/output
- Web Code pane: parse ECHO_FILE + robust path/content extraction; show full source in InlineCodeDiff
- Clearer path errors with allowed roots + active project hint

## v7.6.8 - Anti-hardcode audit: structural coding + product title extraction

### Principle
Fix the *capability gap*, not the reported example. No “add 2d-shooter / 3D shooter to a list.”

### Replaced
- Coding intent: structural `build/create me a <anything>` (not genre noun list)
- Desktop project pin: match user tokens → **real Desktop folders** (discovery)
- GTA-only normalizers → free-form **title entity extraction** (trailer/cast/release/price)
- Weak-answer refine: no more injected `FIFA World Cup …` if not in the query
- Match detection: `vs` / structure, not country whitelist

### Tests
- `tests/test_general_no_hardcode.py` — mystery adventure, rhythm game, tower defense, silksong/dune titles, NHL refine without FIFA inject

## v7.6.7 - Coding path pin + reject stub writes (white-screen game fix)

### Live 2d-shooter session failures
- `game.js` ended as `// Implement collision…` (~55 chars) — plan steps overwrote real code with stubs
- Bare `index.html` resolved to **EchoSpeak repo root**, not `Desktop/2d-shooter-game/`
- file_read "File not found" then wrote `EchoSpeak\index.html` ("Game Loaded!")

### Fix
- `set_active_project_root` / mkdir+write pin Desktop project; relative paths resolve there
- `file_write` rejects comment-only / tiny code stubs
- Pending actions rewrite bare paths via `_normalize_coding_file_path`
- Working shooter scaffold written for the user project

## v7.6.6 - System-wide keep-trying for web answers (not plan-only)

### Why Echo "gave up"
- `WebTaskReflector` **no-op'd all grounded search packets** (`is_grounded_search_output` → return immediately)
- Stage 3: search once → summarize → ship, even if draft said "I don't have the time"
- `ReflectionEngine` only runs on multi-step **plans** (size ≥ 2)
- Weather had a one-off repair; FIFA/timezone/price did not

### Fix
- Grounded packets are quality-gated; weak schedule/timezone evidence **retries** with sharper queries
- Stage 3: `_web_research_answer_with_retries` — if answer abdicates or lacks required facts → re-search + re-summarize (×2) → force evidence-bound rewrite
- Stage 4: `_ensure_web_answer_does_not_give_up` when tools already ran
- Anti-give-up rules in web summary prompts
- Tests: abdication detection + grounded packet acceptability

## v7.6.5 - Clarifier follow-ups (timezone / currency / deictics)

### Live bug
- FIFA: “France vs Morocco 4pm” → “4pm my time? MNT time or when?” searched bare **MNT time zone** and lost the match.
- Root cause: timezone/currency clarifiers were **not** referential; `in cad?` falsely matched **location swap**.

### Fix (`core.py`)
- Detect timezone / currency / short deictic clarifiers; bind to `current_subject`
- Resolve e.g. `… France Morocco … kickoff timezone MNT Mountain …` / `bitcoin price in CAD`
- Reject CAD/USD/MNT as city location swaps
- `_active_user_query` uses resolved rewrite so multi-search can’t drop subject
- Tests: `test_clarifier_followups_bind_timezone_and_currency_to_subject`

## v7.6.4b - Orphan cost rebind + stale tool labels

### Live retest (GTA come out + how much will it cost + FIFA today)
- Python / Bitcoin / release notes: solid (official docs, BTC sources, tool rows)
- Multi fan-out worked, but cost shipped as bare `how much will it cost` (no GTA noun)
- Thinking strip sometimes showed prior-turn Python search on a later multi turn
- FIFA query OK but answer still soft (DDG snippets) — query now asks matchups/teams/kickoff

### Fixes
- `_rebind_orphan_queries` / grounded price check: bare cost → `GTA 6 price cost pre-order…`
- Clear `toolInfoRef` each stream; tag tool meta with `requestId` so done-labels don't inherit
- FIFA sports normalize: `matchups teams kickoff` for denser snippets

## v7.6.4 - Live transcript query quality (GTA+FIFA, release notes, July 9)

### Live multi-prompt dump failures
- GTA release + **price** + FIFA tomorrow was decomposing into chatty residue (`i need you to search…`, orphaned `who is playing`) and **dropped cost entirely**
- Sports domain carve started at message start → `_clip_span_to_clause` kept GTA half and **lost FIFA**
- `python release notes` rewrote to `python release date` (docs ≠ launch)
- `tommrrow` never fixed → relative day pins failed
- `sorry not tomorrow today! july 9th…` emitted apology as a search query
- July 9 schedule follow-up searched generic “sports games” with **no FIFA subject inheritance**

### Fixes (`research.py` + subject enrich in `core.py`)
- Spelling: tommrrow/tommorow → tomorrow (applied in prep)
- GTA release + price force-domain queries; sports span starts at league keywords
- Preserve release notes / changelog queries
- Drop apology/correction smalltalk clauses
- Explicit calendar pin (`July 9 2026`); `enrich_sports_query_with_subject` for follow-ups
- Test: `test_e22_live_transcript_gta_fifa_release_notes_july9`

## v7.6.3 - Search provider cascade + free DDG engineering

### Principle
- Own the **agent layer** (multi-intent, grounder); **pluggable retrieval** for indexes.
- Free path stays DuckDuckGo by default — engineered to act more like paid agent search.

### Code
- `agent/web_search_providers.py`: DDG / Tavily / Brave adapters + cascade
- Free upgrades: news channel, query variants (date / site: authority), empty-result simplify retry, thin-snippet `ddgs.extract`
- `web_search` tool uses orchestrator; `WEB_SEARCH_PROVIDER=auto|duckduckgo|tavily|brave`
- Docs: `docs/SEARCH_ENGINEERING.md` (scope, gaps, DIY vs paid)

### Tests
- `tests/test_web_search_providers.py`

## v7.6.2 - Live sports data path (vs crawl search) + provider notes

### Category mismatch
- Web search (Tavily/Brave/Exa) = **crawled** pages; live scores/odds need **structured feeds**.
- New first-class path: `sports_live` + `agent/sports_data.py` (The Odds API).
- Preferred for live score / who-won / moneyline; **schedule/slate** stays on `web_search`.
- Fallback to grounded web search if key missing, league unmapped, or API empty.

### Config
- `ODDS_API_KEY` / `THE_ODDS_API_KEY`, `SPORTS_LIVE_ENABLED` (default true)
- Prepared: `WEB_SEARCH_PROVIDER`, `BRAVE_SEARCH_API_KEY` (Brave swap = follow-up)

### Docs
- `docs/SEARCH_INFRASTRUCTURE.md` — Tavily acquisition uncertainty, Brave/Exa/Firecrawl notes

### Tests
- `tests/test_sports_live.py` — intent classification + missing-key fallback

## v7.6.1 - Chat embeds (sources, weather, fixtures under answers)

### UI (ChatGPT/Claude-inspired, text-first)
- Assistant **final** bubbles can carry structured `embeds` under the markdown answer.
- Types: **source chips**, **featured link card**, **weather high/low stat**, **schedule list**, **search query chips**.
- Built client-side from this turn’s research runs + answer text (`buildChatEmbeds`) so placement stays under the relevant reply — not a separate chaotic panel.
- Research panel still holds the full audit trail; embeds are the polished “spice” in-chat.

### Files
- `apps/web/src/features/embeds/*` — types, builder, `ChatEmbeds` renderer, vitest
- `index.tsx` — Message.embeds, accumulate turn research, render under ChatBubble

## v7.6.0d - Stage 3 no longer collapses multi-intent to one search

### Live log root cause
- Stage 3 forced schedule path for FIFA+tomorrow, then `_extract_search_query` → `normalize_web_search_query` returned **only the primary** (FIFA).
- That single string was passed as **both** tool query and `original_request`, so weather was never searched.
- Log line `Search grounding %s query=%r` was also broken (loguru needs `{}`, not `%s`).

### Fix
- Multi-intent Stage 3: pass **full user turn** into grounded search; never run multi through `_extract_search_query`.
- `_grounded_web_search` prefers `_active_user_query` when it has more domains than the collapsed arg.
- `_invoke_web_research_query(..., original_request=)` keeps the full utterance for fan-out.
- Force Stage 3 for multi-intent on tool-calling models (not only pure schedule).
- Fix search grounding log format.

## v7.6.0c - General multi-intent domains (no more combo recipes)

### Honest diagnosis
- General decomposition **was** already wired (`looks_like_multi_intent` → `decompose_search_intents` → `resolve_web_search_queries`), but it was too weak: sports detection missed FIFA/`matches`, domain diversity was not a first-class multi signal, and `plus`/also splits + model single-arg paths could collapse to one query. Live “temp tomorrow + FIFA matches” only ran weather.

### System-wide fix (not weather+FIFA recipe)
- **`intent_domains()`** — weather / sports / finance / entertainment / news / fact / odds tags
- Multi-intent when **2+ domains** in one utterance (works on novel combos)
- Broader sports clause: FIFA, world cup, matches/games + day, leagues — general sports normalize, not a multi recipe
- Heuristic decompose splits on **also / plus / as well as**; domain carve-out if split yields one query
- Model tool arg never replaces multi user text; same-domain model dupes not appended
- User turn is always original_request for grounded multi fan-out

### Weather synthesis consistency
- Never ask “what city?” when evidence/subject already names a place
- Repair path catches city-ask contradictions + multi-question “answer every block” instructions

### Tests
- E21: live weather+FIFA transcript + 4 novel no-recipe combos + city-ask detector

## v7.6.0b - Search honesty, tomorrow schedules, dedupe, routine isolation

### Critical — false “I can’t search”
- **Root cause:** “do a deeper search” expanded to meta-queries (`do a deeper search about …`), Stage 3 skipped for tool-calling models, and live-web recovery didn’t treat deeper-search as needing web — so the model invented “tools don’t let me search” after successful searches.
- **Fix:** expand deeper-search to the **current subject** (not the meta phrase); strip deeper-search wrappers in normalize; force Stage 3 grounded path for deeper/schedule follow-ups even on tool-calling models; `_ensure_search_capability_honesty` rewrites false unavailability claims; grounded packets forbid claiming web_search is disabled.

### Schedule “tomorrow” + STT typos
- Schedule intent covers who-is-playing / games **tomorrow|tonight|today** (not only “today”).
- Soft-accept near-future fixture matchups; schedule candidates include tomorrow ISO + day word.
- Spelling fixes for common STT errors (`maracco`→`morocco`, etc.).

### Wasteful re-search
- Per-request `_request_search_cache` on raw Tavily execute (identical queries hit network once).
- Default grounding max candidates lowered to 2; multi-intent small-talk no longer fans out as fake sub-queries.

### Background task bleed
- Routines run with `source=routine`, isolated `thread_id`, no UI callbacks/stream buffer.
- Snapshot/restore `current_subject` + last web context so a “daily news briefing” cannot clobber the active chat topic.

### Tests
- E20 / E20b–d: deeper-search honesty, tomorrow+spelling, search cache, routine subject isolation.

## v7.6.0 - Real MCP stdio client (start / list / call)

### Backend / MCP
- Replaced stub `mcp_client.py` with a real **stdio JSON-RPC** client (Content-Length framing, MCP 2024-11-05 subset).
- Flow: start process → `initialize` → `notifications/initialized` → `tools/list` → register → `tools/call`.
- Tools register as `mcp__<server>__<tool>` into `ToolRegistry` (category `mcp`); LangChain `StructuredTool` when possible.
- Process-wide singleton `get_mcp_manager()` shared by agent init and Trust Center / capabilities.
- **Trust honesty:** configured servers ≠ available tools; start failures surface `last_error` per server; `mcp_available` only when `loaded_tool_count > 0`.
- Untrusted servers mark tools `is_action` + moderate risk; `trust: trusted` is safe/non-action.
- Windows-safe: background reader thread (no `select` on pipes).
- Config: existing `MCP_SERVERS` JSON / `config.mcp_servers` (`command`, `args`, `env`, `transport`, `trust`, `enabled`, `timeout_s`).

### Tests
- `tests/fixtures/mock_mcp_server.py` — minimal echo/add MCP server.
- `tests/test_mcp_v760.py` — list+call, bad command fails loud, configured≠available, disabled/unsupported transport.

## v7.5.4 - General multi-intent decomposition fallback

### Backend / Research
- **Problem:** multi-intent only worked for hand-written recipes; other compounds fell to one junk string then the model’s lazy tool arg.
- **Fix:** `looks_like_multi_intent` (cheap gate) → recipe fast path → `decompose_search_intents` (LLM or heuristic) → each sub-query through existing `_grounded_web_search`.
- `resolve_web_search_queries` is the single entry; never overwrites a real multi-split with a single model query.
- Recipes (weather+sports, GTA trailer+cast) unchanged as free fast path.
- E19: novel Dubai/Tesla compound + simple-question false-positive guard.

## v7.5.3 - GTA multi-intent (Trailer 3 + characters) no more release-only give-up

### Backend / Research
- **Why it failed:** multi-intent split only handled sports+weather; “trailer 3 + characters in gta 6” collapsed to one weak string; then `_grounded_web_search` **replaced** a single split with the model’s `gta 6 release date` arg — characters never searched; `accepted=false` told the model to give up.
- **Fix:** fan-out Trailer N + cast/characters queries; keep user-turn multi splits (don’t overwrite with model-only arg); soft-accept when Lucia/Jason/trailer rumor snippets appear so synthesis reports best-available facts.
- E18 locks the live failure case.

## v7.5.2 - Coding loop wired into agent + multi-file eval

### Backend
- `EchoSpeakAgent` owns `_coding_loop`; starts on coding workspace / coding intent in `process_query`.
- Tool completions (`_emit_tool_end`, TaskPlanner, pending `file_write`) call `_coding_loop_note_tool` to advance phases from real tool use (not model memory).
- `CodingLoop.note_tool` / `fast_forward_to` auto-walk legal edges for inspect/implement/verify/confirm.
- Doctor report + `/coding/readiness` expose live `coding_loop` snapshot (phase, files_touched, verify/exit status).
- E17 multi-file coding-loop fixture on the eval board.

## v7.5.1 - Mount escape hardening + dual denylist + coding loop machine

### Backend
- **Path boundary:** `assert_safe_project_path` / `path_is_within_root` resolve real paths so symlink roots and `..` climbs cannot map CWD outside FILE_TOOL roots; forbidden mounts (docker.sock, .ssh, …) skipped.
- **Denylist dual-layer:** sandbox path always re-checks denylist via `default_denylist_check` (same config list as host tools) even if the caller forgets to pass a checker.
- **Coding loop (v7.5.2 foundation):** `agent/coding_loop.py` enforces inspect→plan→implement→verify→confirm→summarize as illegal-transition-proof states; exit statuses pass/fail/timeout/denied/sandbox_unavailable/cancelled/pending; `project_folder_for_name` for named project dirs under FILE_TOOL_ROOT/projects/.
- `/coding/readiness` recommended_loop now includes **confirm**.

### Tests
- `tests/test_sandbox_v751.py` — escape, symlink, denylist, coding loop order.

## v7.5.0 - Terminal execution modes + Docker sandbox skeleton

### Backend
- Added `agent/sandbox.py`: `TERMINAL_EXECUTION_MODE=host` (default, unchanged) vs `docker`/`sandbox`.
- Docker path: one-shot `docker run --rm --network=none`, non-root user, memory/CPU limits, mounts **only** `FILE_TOOL_ROOT` + `FILE_TOOL_EXTRA_ROOTS`, **never** docker.sock; denylist re-checked before start.
- **No silent host fallback** when sandbox mode is selected — returns `Status=sandbox_unavailable` with an explicit reason.
- Host path now also emits `Status=pass|fail|timeout` + `Mode=host` for consistent classification.
- Config: `TERMINAL_DOCKER_IMAGE`, `TERMINAL_DOCKER_MEMORY`, `TERMINAL_DOCKER_CPUS`, `TERMINAL_DOCKER_USER`.
- `GET /coding/readiness` includes `sandbox` status (mode, ready, mounts, docker probe message).

### Tests
- `tests/test_sandbox_v750.py` — mode normalize, mounts, unavailable/denied paths, no host fallback, readiness contract.

## v7.4.9 - Single first-beat + schedule deeper search (no early give-up)

### Backend
- **Double first beat:** ReAct loop reset `_preamble_done_this_gen` so a second `web_search` emitted another “Doing good / Pretty good” spoken line. Now `_preamble_done_this_request` seals **one** pre-tool beat per request.
- **Oilers/schedule “gave up”:** Schedule evidence was scored like generic “specific answer” and often rejected (or instructed as insufficient), so the model said “check NHL.com” even with sources. Dedicated `_has_schedule_signal`, schedule scoring/accept, deeper second-pass candidates (`site:nhl.com`, next game year), soft-accept when date/matchup snippets exist, and synthesis instructions to report best-available dates instead of bailing.

### Tests
- E15: single preamble per request across gen reset
- E16: schedule next-game snippets accepted

## v7.4.8 - Fix weather search RecursionError (city-less “check the weather”)

### Backend / Research
- **Root cause (confirmed, not guessed):** `_normalize_weather_query` called `normalize_web_search_query_single`, which re-entered `_normalize_weather_query` for any weather line without a city → infinite recursion → `Failed web_search: maximum recursion depth exceeded` on prompts like “can you check the weather for me tho?”.
- Fixed: weather normalizer is a **leaf** (uses `_strip_weather_chat_filler` only); no cycle with single-query normalize.
- Bare weather queries compact to `weather today high low temperature forecast`; city from subject/context still injected when known.
- E14 regression locks the no-recursion path for social+weather and city-less weather.

## v7.4.7 - Multi-intent search fan-out + tool rows after multi-beat

### Backend / Research
- Fixed Oilers+weather multi-intent: one blended query (`oilers game weather forecast`) dropped schedule and polluted weather. Now `split_web_search_queries()` fans out to separate grounded searches (e.g. `Edmonton Oilers next game schedule NHL` + `Edmonton weather today high low temperature forecast`), with team→city inference for bare “what’s the weather”.
- Intent mode is classified from the **resolved** query only so sports queries never enter weather candidate rewrite.
- Multi-intent `_grounded_web_search` emits per-query tool_start/end for chat visibility.

### Web UI
- After a `partial_reply` (social beat), thinking activity is re-timestamped and shows `checking…` then real `Searching: <query>` / `Search done (N sources): <query>` rows **below** the spoken beat (same as single-response turns).

### Tests
- E13: Oilers + weather split, no blend, dual tool emits.

## v7.4.6 - Search query normalization (no raw chat as Tavily query)

### Backend / Research
- Root cause: multi-intent chat (e.g. “how’re you feeling? and i wonder when trailer 3 for gta 6…”) was passed through `_extract_search_query` / Stage-4 tool args almost unchanged; `_clean_query` only stripped a few polite words. Also `"won"` substring matched inside `"wonder"` as a live-web false positive.
- Added `normalize_web_search_query()` — strips social openers/filler, picks the factual clause, rewrites GTA/trailer release asks to compact queries like `GTA 6 Trailer 3 release date`.
- Applied at `_extract_search_query`, `_grounded_web_search` entry, SearchGrounder candidates, and extract_research_query.
- Live-web triggers now use word boundaries; release-date intent sets specific+recency candidates.
- E12 search-query fixture locks the GTA multi-intent case.

## v7.4.5 - Memory save discipline + E11 long-conversation board

### Backend / Memory
- Fixed regression: `_record_turn` always called `add_conversation` (type=conversation → FAISS), so UI "Memory saved (X)" fired nearly every turn even with `MEMORY_AUTO_STORE_CONVERSATIONS=false`. Raw turns are gated again; durable path remains profile/curated/typed only.
- Vector injection prefers typed memories; when auto-store is off, raw `type=conversation` dumps are not injected into prompt context (session + ephemeral chat own multi-turn).
- Stronger subject continuity: hollow opinion follow-ups ("what do you think about it?"), pronoun-heavy short questions, and explicit "remember …" writes no longer overwrite `current_subject`.

### Tests
- Added E11 long-conversation eval (20 turns: follow-ups, weather switch + switch-back, late referential resolve, memory-save frequency, session vs vector agreement, Memory Doctor dominance check). Board is now E1–E11.

## v7.4.4 - Eval Harness E1–E10 (CI fixtures)

### Backend / Tests
- Added `tests/test_eval_harness_e1_e10.py` with deterministic recorded fixtures (no live Tavily).
- Scenarios: E1 printed file_write pending, E2 live-score reject/retry, E3 full-page buried schedule, E4 capability-gap odds, E5 subject continuity, E6 terminal ExitCode=1, E7 LM Studio readiness, E8 coding write pending, E9 weak evidence insufficient, E10 context flood protects subject.
- Baseline board: **10/10** harness fixtures pass in CI (product bar ≥8/10). Live Gemma 4 runs remain manual.

## v7.4.3 - Full Suite Green + Endpoint Contracts

### Tests
- Updated research routing tests to patch `_raw_web_search_execute` (shared grounder backend) instead of tool.invoke.
- Hardened web_search timeout test so DuckDuckGo fallback cannot mask Tavily timeouts.
- Added endpoint contracts: `/coding/readiness`, `/memory/compact`, terminal denylist allow/deny, readiness response shape.
- Full backend suite: **244 passed**.

## v7.4.2 - Context Budget Completeness

### Backend
- `ContextBudgetManager` now **compresses** non-protected blocks under soft_trim/summarize/compact (head+tail) instead of only logging pressure.
- Protected overflow under compact can hard-clip with compression markers.
- Stage 5 finalize builds prompts through the same budget manager (partial tools protected; history/docs compressible).
- Mid-task tool outputs are fit/compressed and written back into `_partial_tool_results`.
- Added `compress_text()`, `fit_text()`, and `compressed_blocks` on budget reports.

## v7.4.1 - Tool-Call Contract + Stage 4 Recovery

### Backend
- Expanded printed-tool detection (`Action:`, `call_tool:`, function-call shapes) and last-line defense that never leaves raw tool markup in chat.
- Unrecognized tool-shaped text always records `tool_call_syntax_unrecognized` telemetry.
- Recovered `web_search` from printed syntax runs through `_grounded_web_search`; action tools stay **pending confirm**.
- Harvest LangGraph ToolMessages into partial-tool state; synthesize answers when the graph returns empty after tools ran.
- Stage 4 cascade end: partial-tool synthesis or explicit blocker — never silent amnesia of completed tool work.
- Stage 5 injects partial tool context into direct-LLM fallback prompts.

## v7.4.0 - Search Path Parity (Harness Completeness)

### Backend
- Added a single shared `_grounded_web_search()` on `EchoSpeakAgent` as the only grounding entry point for web search (Workstream A).
- Raw Tavily execution is isolated in `_raw_web_search_execute()` so candidate loops never re-enter the grounder.
- Stage 3 shortcut path, TaskPlanner/WebTaskReflector, and native LangGraph/ReAct `web_search` tools all use the same helper.
- Wrapped `lc_tools` `web_search` via `_apply_search_grounding_to_lc_tools` / `_make_grounded_web_search_lc_tool` so Gemma 4 native tool-calling cannot bypass grounding.
- `self.tools` `web_search` wrapper also routes through `_grounded_web_search`.
- Added `format_grounded_tool_output()` / `is_grounded_search_output()` / `GROUNDED_SEARCH_MARKER` in `research.py`.
- Insufficient evidence returns structured `SEARCH_EVIDENCE_INSUFFICIENT` text that forbids inventing scores/schedules and blocks confident empty answers.
- Accepted evidence is marked `accepted=true` with condensation for synthesis only.
- Grounded results always persist to `_last_grounded_search_result` (doctor/Research panel) regardless of path.
- WebTaskReflector skips re-grounding when output already carries the grounded marker (no double-ground).

### Verification
- Extended `tests/test_reliability_architecture.py` for insufficient/accepted formatting, single-path persistence, double-ground skip, and lc-tool wrapper routing (15 tests passing).

## v7.3.2 - Reliability Contract Cleanup

### Backend
- Added a unified printed-tool-call interceptor for weak local models. Echo now catches `|TOOL|`, `<execute_tool>...</execute_tool>`, `<tool_call>...</tool_call>`, fenced JSON/tool blocks, and function-style calls such as `terminal_run(command="...", cwd=".")` before chat display. Recognized actions become pending confirmations; malformed tool-looking output becomes a clear blocked parse message instead of raw fake tool text.
- Hardened printed file-tool recovery for weak-model near misses such as `<execute_tool> file_write(file_path="index.html", content="...") </execute_tool>` with `file_path` aliases or a missing final parenthesis, so the output becomes a pending file action instead of fake chat text.
- Added recovery for LM-Studio/harmony-style printed tool tokens such as `<|tool_call>call:file_write{path:<|"|>index.html<|"|>, content:<|"|>...<|"|>}`. If the model provides file content but omits the path, Echo can infer the requested filename from the user prompt and still convert the output into a confirmation-gated pending action.
- Generalized search evidence sufficiency beyond live scores. Current-day schedules, events, odds, availability, releases, and "who plays today" style requests now require snippets or fetched page text that actually contains the requested answer.
- Added bounded read-only full-page extraction as a fallback for promising search URLs when snippets are date/nav-only or otherwise too shallow.
- Narrowed capability shortcuts in both the router and core tool-selection path. Generic "what can you do?" questions still stay lightweight; topic-specific capability-gap questions such as live sports odds now route through normal reasoning/search.
- Fixed workspace allowlist policy drift: workspace allowlists remain the ceiling and are no longer intersected with every active skill, which keeps safe workspace tools such as `web_search`, `calculate`, `get_system_time`, and `project_update_context` visible where intended.
- Made TaskPlanner action execution confirmation-gated by default for local/web use. Planner-created file, terminal, and social actions now pause for confirmation unless a future explicit autonomy mode enables auto-run.
- Preserved LangGraph thread-id behavior while still passing the system/context message into pre-model-hook graph runs.
- Added explicit graduated context-pressure stages (`none`, `soft_trim`, `summarize`, `compact`) and protected pending actions, active task plans, current subject, profile, pinned memory, and session memory from low-priority trimming.
- Added mid-task context pressure checks before tool steps and after large tool outputs.
- Made verification weighting active: printed tool syntax, search sufficiency, action args, terminal, file writes, and retry exhaustion are high-weight clusters; simple time/calculator/project-update reads remain low-weight.

### Verification
- Updated stale LangChain tool tests to assert `.invoke(...)`/metadata instead of Python callability.
- Added regression tests for printed tool syntax interception, schedule evidence insufficiency, full-page search fallback, capability-gap routing, context pressure stages, and verification weighting.

## v7.3.1 - Reliability Architecture Pass

### Backend
- Added a general `SearchGrounder` in `apps/backend/agent/research.py` with explicit intent/candidate/evidence/result types. Web search now builds up to three grounded candidates, anchors referential follow-ups to the current subject, rejects weak/date-only score evidence, and feeds condensed evidence to the LLM instead of trusting the first raw search result.
- Added `apps/backend/agent/context_budget.py` and wired it into Stage 2 context building. Echo now reserves model headroom before invocation and trims lower-priority blocks before profile, pinned memory, current subject, and session summary.
- Added `apps/backend/agent/session_memory.py` and update hooks after completed turns. Each thread can now maintain a durable session summary with current subject, open tasks, facts, preferences, unresolved questions, and decisions separate from raw chat/vector memory.
- Added `apps/backend/agent/verification.py` and connected telemetry to search grounding, printed/action-parser tool failures, deterministic terminal/file failures, and exhausted reflection cycles.
- Extended `/doctor` with reliability diagnostics for search grounding, context budget, session memory, and verification clusters.
- Extended `/memory/doctor` with session-memory status, path, last update, current subject, turn count, and summary size.
- Added `agent.adapters` and a minimal `agent.mcp_client` bridge so referenced integration modules exist and Trust Center can distinguish configured MCP servers from actually loaded MCP tools.
- Corrected MCP trust summary semantics: MCP is only reported as available when configured, client-present, and loaded tools exist.

### Verification
- Python compile passed for touched backend modules.
- Focused pytest pass: 12 targeted tests passed for reliability architecture, research parsing, referential follow-up continuity, printed `|TOOL|` recovery, deterministic terminal failure, and reflection stop conditions.
- Full backend pytest is improved but still not clean: 191 passed / 12 failed. Remaining failures are pre-existing or adjacent contract drift around LangChain `StructuredTool` callability, workspace allowlist state, project-update tool exposure, LangGraph history expectations, and task-planner auto-confirm behavior.

## v7.3.0 - Pre-Testing Technical Audit

- Added `docs/PRE_TESTING_TECHNICAL_AUDIT_2026.md`, a pre-testing architecture/security/reliability audit covering the current agent loop, memory system, coding readiness, integration endpoints, MCP trust gap, UI surfaces, docs drift, tests, and production-readiness checklist.
- Marked the main pre-test blockers: terminal denylist documentation drift, missing MCP client implementation, local-first API auth posture, FAISS trusted-state boundary, and missing endpoint contract tests for the new v7.3 routes.
- Added `INFRASTRUCTURE.md`, a full backend infrastructure guide covering FastAPI routes, the agent pipeline, model/tool modes, memory tiers, search grounding, reflection, safety gates, integrations, diagnostics, and the recommended next architecture upgrades.

## v7.3.0 - Coding Loop, Memory Doctor UI, Tool Trust

### Backend
- Added `GET /coding/readiness` to report provider readiness, coding workspace state, file roots, terminal denylist, and required coding tool availability.
- Extended `GET /capabilities` with tool origin/trust metadata and an MCP summary so configured-but-missing MCP capability is reported as unavailable, not as real loaded tooling.
- Added optional shared-key API auth (`API_AUTH_ENABLED`, `API_AUTH_KEY`, `API_AUTH_LOCALHOST_BYPASS`) across HTTP endpoints and the WebSocket gateway for non-local/remote exposure.
- Fixed the `/capabilities` response model so the new `trust` payload is not filtered out by a duplicate schema definition.
- Fixed `/coding/readiness` provider readiness so it uses the shared preflight `ok` field instead of a non-existent `ready` field.
- Made `POST /memory/compact` accept both JSON body and query parameters, matching the Memory UI compact button.
- Hardened routine webhooks: `/webhooks/{path}` now verifies the global webhook HMAC signature when a webhook secret is configured.
- Extended settings validation and `/doctor` integration diagnostics for Telegram, Twitch, Twitter/X, Discord, and routine webhook signing.

### Agent
- Added explicit conversation continuity state (`current_subject` / resolved follow-up input) so prompts like "do a deeper search" carry forward the topic Echo just answered instead of falling into a generic clarification loop.
- Added Stage 4 diagnostics for the agent cascade. Each execution can now report the active tool-calling mode plus whether Echo used LangGraph, AgentExecutor, fallback executor, or direct-LLM fallback.
- Extended `/doctor` with Discord shared-core diagnostics so the bot path can be checked for enabled/running state, source/thread role state, and whether it is using the same `process_query` route.
- Hardened `ReflectionEngine` with verifier-first checks for terminal exit codes, file operations, file reads/lists, and structured JSON before asking the model to grade its own work.
- Changed exhausted reflection cycles from implicit success to a blocker signal unless a deterministic check has already proved the step worked.
- Added recovery for weak-model printed tool directives such as `|TOOL| terminal_run {...}` so they become normal pending actions instead of leaking inert raw tool text into chat.
- Strengthened the coding workspace prompt around the lifecycle: inspect, plan, implement, verify, summarize.
- Added explicit guidance for Desktop-targeted coding requests so Echo uses configured file roots instead of getting stuck saying it cannot see the desktop.
- Fixed optional vision import behavior so missing OpenCV does not crash backend import through evaluated `np.ndarray` annotations.

### Web UI
- Added a Coding readiness card to the Code visualizer.
- Added a Memory Doctor card to the Memory studio tab.
- Added Coding Agent Loop and Tool Trust Center sections to the Tools studio tab.
- Encoded thread IDs in Tool Trust and memory compact calls, and refreshed Memory Doctor after compaction.

### Verification
- Python compile passed for the touched backend modules.
- Focused pytest pass: 11 targeted tests passed for deterministic reflection, plan stop conditions, MCP unavailable reporting, API auth checks, current-subject continuity, and printed `|TOOL|` recovery.
- A broad test-file run still needs the project dependency set and writable temp setup; the first whole-file attempt exposed unrelated optional dependency/temp-folder issues, so verification was narrowed to the patched behaviors.

## v7.2.0 - Provider Readiness Preflight

### Backend
- Added a fast provider readiness preflight before `/query` and `/query/stream` start full agent execution. If LM Studio, Ollama, LocalAI, or vLLM is selected but unreachable, Echo now returns a clear recovery message instead of falling through to a generic connection error.
- Added readiness metadata to `/provider` (`ready`, `readiness_message`, `readiness_detail`) so the UI can surface provider status without starting a chat request.
- Added a read-only `/memory/doctor` report that summarizes memory count, type distribution, pinned/profile coverage, duplicate-looking groups, raw conversation auto-store status, warnings, and recommendations.
- Added focused tests for LM Studio unreachable, OpenAI key-ready readiness checks, and memory-doctor duplicate/conversation-dominance warnings.

## v7.1.3 - Agentic Baseline Documentation

### Docs
- Added `docs/AGENTIC_BASELINE_2026.md`, a July 4, 2026 baseline comparing EchoSpeak with current Claude Code, Letta, LangGraph, OpenAI Agents SDK, OpenHands, CrewAI, and MCP patterns.
- Updated `ROADMAP.md` with proposed v7.2 priorities: provider readiness, coding lifecycle, memory doctor, MCP trust center, and evaluation harness.
- Updated `docs/AGENT.md` so developers can find the new baseline and understand the v7.1.2/v7.1.3 direction.

## v7.1.2 - Agentic Tool Loop, Transparent Reasoning Trace, and Safety Cleanup

### Backend
- Replaced the terminal command allowlist model with a **terminal denylist**. Common harmless commands such as `echo` are no longer blocked just because they were not prelisted, while destructive command names still stay blocked by default.
- Added **coding/project intent promotion** so requests like "build a website", "create files on my desktop", or "start coding" enter the coding workspace with file and terminal tools available.
- Added **extra file roots** via `file_tool_extra_roots`; `Desktop/...` now resolves to the user's real desktop while the main project root stays at `C:\Projects\EchoSpeak-main\EchoSpeak-main`.
- Corrected stale runtime paths in settings so Echo no longer points at the old desktop project copy for file and terminal work.
- Hardened web-search reflection for **live sports score queries**. Date-only or schedule-only search results are rejected, the query is rewritten toward "live score/current score/result", and the UI receives a thinking step explaining the retry.
- Added a compact **operational lesson store** (`data/agent_lessons.json`) for repeated tool-quality lessons. These are injected into the system prompt as distilled lessons instead of saving every chat turn as memory.
- Kept raw conversation memory disabled by default so Echo stops building a huge memory bank from ordinary conversations. Explicit/profile memories remain available.

### Frontend
- Made the thinking/reasoning stream transparent again instead of rendering it in a blue card, while preserving the typewriter-style live text.
- Made the task plan checklist transparent so it blends into the chat timeline and remains at the bottom with new activity.
- Improved live tool trace text so users see action-oriented lines such as "Searching web_search: ..." or "Reading file_read: ..." instead of generic tool labels.

### Docs & Verification
- Updated architecture/audit documentation for the denylist, operational lessons, and live-score reflection changes.
- Python compile passed for the touched backend modules.
- Web TypeScript typecheck passed for the React app and Vite config.
- Focused live-score smoke check confirmed that date-only results are rejected and actual score-style results are accepted.

## v7.1.1 — Heartbeat Fix, Discord Tweet Approve/Reject, and Repo Presentation

### Backend
- **Heartbeat uses `llm_wrapper.invoke()` directly** instead of `process_query()` — the heartbeat no longer has access to tools, cannot plan actions, and cannot generate confirmation prompts. It produces text only; routing is handled by `route_message()`.
- Added **`_sanitize_response()`** to `HeartbeatManager` — strips any residual plan blocks, confirmation prompts, and action lines from the LLM response before routing to Discord/Telegram/etc.
- Updated **heartbeat prompt** to explicitly instruct the LLM: text-only output, no tools, no plans, no "confirm" prompts.
- Added **Discord DM command interception** for tweet approve/reject — typing `approve`, `reject`, `/approve`, `/reject`, or natural language like "reject the tweet" or "can you reject the echospeak tweet" in Discord DM now routes directly to the Twitter autonomous API instead of going through `process_query()`.
- Fixed **restart-safe Twitter approval state** — pending autonomous/changelog tweet approvals are now restored from `twitter_auto_tweet_state.json` on startup, and `approve_pending_tweet()` / `reject_pending_tweet()` fall back to the persisted pending item if in-memory state is empty.
- Fixed **Discord DM reply matching** for the tweet queue — replies like `reject please`, `decline`, `deny the twitter notification`, and `can you reject the tweet` now resolve against the pending tweet context instead of falling through to normal chat.

### Repo Presentation
- **New README.md** — clean hero section with inline logo + title, tagline ("Your AI. Your machine. Your rules."), quick links bar, highlights, organized feature categories, compact install section. Removed inline changelog.
- Added **MIT LICENSE** file.
- Created **git tag `v7.1.0`** and first GitHub Release.
- Removed hardcoded `/home/mem0` filesystem path from `WorkspaceExplorer.tsx`.

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
