# EchoSpeak Pre-Testing Technical Audit - 2026-07-05

This audit captures the current EchoSpeak state before the next manual testing pass. It is intentionally pre-testing: the goal is to identify architecture, integration, safety, reliability, memory, UI, and production-readiness risks before spending time on live validation.

Audit scope:
- Repo inspected locally in `work/EchoSpeak-github`.
- Current uncommitted v7.3 work included in the review.
- External research checked against current agentic patterns from Claude Code, Letta, LangGraph, OpenAI Agents SDK, MCP, and OpenHands.
- Runtime code was not changed by this audit document.

External reference baseline:
- Claude Code memory docs: https://docs.anthropic.com/en/docs/claude-code/memory
- Claude Code workflows: https://docs.anthropic.com/en/docs/claude-code/common-workflows
- Letta stateful agents: https://docs.letta.com/guides/agents/memory
- LangGraph overview: https://docs.langchain.com/oss/python/langgraph/overview
- OpenAI Agents SDK: https://openai.github.io/openai-agents-python/
- MCP security best practices: https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices
- OpenHands docs: https://docs.openhands.dev/overview/introduction

## 1. Executive Summary

EchoSpeak is directionally strong for the product Ty is building: a local-first, multi-surface autonomous assistant that can chat, research, use tools, operate across Discord/Telegram/Twitch/Twitter, schedule background routines, manage memory, and increasingly act like a coding agent.

The most important finding is that EchoSpeak does not lack capability. It has the opposite problem: capability is broad, stateful, and concentrated in a few large files. The next reliability jump should come from tightening contracts, tests, docs, and trust boundaries around existing surfaces.

Current readiness:
- Local development readiness: 7.0/10
- Controlled personal beta readiness: 6.2/10
- Network-exposed production readiness: 4.5/10
- Agentic coding-loop readiness: 6.5/10 after the v7.3 changes, pending test coverage
- Memory-readiness: 6.8/10, with good direction but still needing UI clarity and compaction workflows

Top pre-testing blockers:
1. API is local-first but defaults to `0.0.0.0` with no global auth layer.
2. MCP is represented in configuration and UI trust metadata, but `agent/mcp_client.py` is missing, so MCP is not actually available yet.
3. Several docs/settings still say terminal allowlist even though runtime moved to terminal denylist.
4. The biggest runtime files are too large for safe iteration without stronger endpoint/UI/coding-loop regression tests.
5. FAISS memory/document stores use dangerous deserialization, acceptable only if those directories remain trusted local state.
6. New v7.3 endpoint contracts need focused tests before manual product testing.

## 2. Overall Architecture Review

EchoSpeak currently has five major layers:
- React Web UI: chat, avatar, research panel, memory UI, tools/capabilities, code/workspace surfaces.
- FastAPI control plane: settings, query, stream, memory, providers, documents, routines, social integrations, A2A, vision, workspace, todos, health.
- Agent core: provider abstraction, workspace selection, routing, memory injection, LangGraph/AgentExecutor/fallback tool loop, approvals, reflection, traces.
- Tool layer: web search, files, terminal, desktop/browser, Discord, documents, self-modification, email, skills, and other integrations.
- Persistent local state: settings, secrets, memory, thread state, traces, todos, documents, provider/session state.

Core observations:
- The high-level design matches modern agent systems: tool loop, durable state, memory, approvals, streaming, traceability, role-aware tools, and platform integrations.
- EchoSpeak has already adopted important patterns from Claude Code and Letta: explicit injected memory/context, local project rules/workspaces, persistent state, and user-visible action approval.
- The system currently relies on a custom harness more than a compact framework runtime. That is fine for local-first control, but it makes regression tests and documentation more important.
- The API server exposes about 102 HTTP routes in one file. That is a large operational surface for a personal assistant.
- The biggest files are `agent/core.py` at about 8350 lines, `api/server.py` at about 5019 lines, `apps/web/src/index.tsx` at about 7905 lines, `agent/tools.py` at about 4544 lines, and `agent/memory.py` at about 1431 lines.

## 3. System Strengths

- Strong local-first direction: user data, memory, settings, and artifacts are held locally by default.
- Provider readiness preflight now prevents generic LM Studio/Ollama connection failures from masquerading as agent reasoning failures.
- Coding workspace prompt now pushes an inspect -> plan -> implement -> verify -> summarize lifecycle.
- Chat continuity was improved with explicit current-subject state and referential follow-up resolution.
- Reflection has moved in the right direction: deterministic checks now run before LLM self-grading for terminal/file/JSON outcomes.
- Memory is no longer just "save every chat": profile facts, typed memory, pinned context, doctor, compaction, and importance gates are present.
- Tool metadata is centralized enough to show risk, confirmation, policy flags, and origin in the UI.
- Discord role separation is mature: public/trusted/owner behavior is explicit, and PUBLIC users are blocked from owner-level tools and owner memory.
- Query streaming emits thinking, task-plan, tool, memory, and final events for a good UI foundation.
- The research panel has a structured evidence contract around `web_search` output instead of plain text only.

## 4. High-Risk Issues

### H1. Local-first API is not production-authenticated

- Severity: High
- Root cause: FastAPI has broad local control endpoints, but only admin restart routes use `X-Admin-Key`; the rest rely mainly on local deployment assumptions, CORS, rate limiting, source roles, and per-tool safety gates.
- Evidence: CORS is configured for local dev origins at `apps/backend/api/server.py:1306`; admin key verification exists at `apps/backend/api/server.py:1427`; many sensitive routes such as `/settings`, `/query`, `/workspace`, `/memory`, `/documents/upload`, `/provider/switch`, `/gateway/ws`, and social endpoints are not globally auth-gated. `API_HOST=0.0.0.0` appears in `apps/backend/.env.example:280` and config default host is `0.0.0.0` at `apps/backend/config.py:311`.
- Expected impact: If the backend is reachable from another device or exposed through a tunnel, a hostile site/user on the reachable network may interact with settings, memory, tools, or chat endpoints. Tool confirmation still helps, but read endpoints and configuration endpoints remain sensitive.
- Recommended solution: Before production/network exposure, add an API auth mode for all non-health endpoints. At minimum, require a local session token or `X-EchoSpeak-Key` for REST and WebSocket, and default host to `127.0.0.1` in examples unless explicitly deploying.
- Implementation priority: P0 before any public/private network deployment.
- Estimated effort: 1-2 days for middleware, UI header wiring, tests, docs.
- Trade-offs: Adds setup friction. For a personal local app, keep a dev bypass for `localhost` only.

### H2. MCP appears configured but the actual MCP client is missing

- Severity: High
- Root cause: Config and UI now expose MCP trust metadata, but the client file expected by the runtime does not exist.
- Evidence: `MCP_SERVERS` is parsed in `apps/backend/config.py:576`; core tries to import `agent.mcp_client.MCPManager` at `apps/backend/agent/core.py:1666`; capabilities checks `(BASE_DIR / "agent" / "mcp_client.py").exists()` at `apps/backend/api/server.py:2237`; local inspection found `apps/backend/agent/mcp_client.py` is absent.
- Expected impact: User expects MCP servers/tools to work, but configured MCP servers will be inert or produce warnings. This can look like an agent reasoning failure even though it is an integration implementation gap.
- Recommended solution: Either implement the MCP client in v7.5 or make MCP visibly "planned/not installed" in settings and docs. Keep the trust-center UI, but label it as metadata-only until a client exists.
- Implementation priority: P0 if MCP testing is in scope; P2 if postponed.
- Estimated effort: 2-5 days depending on stdio/http transport scope and sandbox posture.
- Trade-offs: Implementing MCP expands capability and attack surface. Leaving it disabled is safer but less impressive.

### H3. FAISS dangerous deserialization must stay inside trusted state

- Severity: High
- Root cause: FAISS local load requires pickle-like deserialization; the code enables `allow_dangerous_deserialization=True`.
- Evidence: Memory store load uses `FAISS.load_local(... allow_dangerous_deserialization=True)` at `apps/backend/agent/memory.py:558`; document store load does the same at `apps/backend/agent/document_store.py:390`.
- Expected impact: If an attacker can write files into the FAISS index directories, loading memory/doc indexes can become code execution risk.
- Recommended solution: Treat memory/document index directories as trusted-only local state. Add docs warning, verify permissions on startup, and never load FAISS indexes from uploaded/user-provided paths. Longer term, add index signing or rebuild-from-json fallback for untrusted restores.
- Implementation priority: P0 for docs and path checks before network exposure; P2 for signed/rebuildable indexes.
- Estimated effort: 0.5 day for warning/doctor checks; 2-4 days for signing/rebuild flow.
- Trade-offs: Safer restore flow costs complexity and slower migration/rebuild.

### H4. Runtime complexity is concentrated in giant files

- Severity: High
- Root cause: Agent pipeline, routing, memory, prompt construction, tool cascade, approvals, reflection, and UI rendering have accumulated in a few files.
- Evidence: `apps/backend/agent/core.py` is about 8350 lines; `apps/backend/api/server.py` is about 5019 lines; `apps/web/src/index.tsx` is about 7905 lines.
- Expected impact: Small changes can create hidden regressions in unrelated chat, Discord, memory, or coding flows. It also makes it hard to onboard future agents/humans.
- Recommended solution: Do not refactor blindly before testing. First add contract tests around critical paths. Then extract stable modules: provider readiness, memory doctor, capabilities/trust, query stream event reducer, workspace/coding readiness, and integration health.
- Implementation priority: P1.
- Estimated effort: 1 day for tests around existing behavior; 3-7 days for safe extraction.
- Trade-offs: Extraction temporarily slows feature work but reduces future bug rate.

## 5. Medium-Risk Issues

### M1. Terminal denylist migration is incomplete in docs/examples

- Severity: Medium
- Root cause: Runtime moved from allowlist to denylist, but stale docs/settings remain.
- Evidence: `README.md:116` says terminal uses allowlisted commands; `apps/backend/.env.example:192` still defines `TERMINAL_COMMAND_ALLOWLIST`; `apps/backend/TEST_RUNDOWN.md:356` says terminal commands are only allowlisted; `apps/onboard-tui/src/index.tsx:160` still writes `terminal_command_allowlist`; stale generated `apps/web/src/index.js` still renders "Terminal Allowlist".
- Expected impact: Users may configure the wrong setting, think the fix did not land, or test against stale behavior. Onboarding may persist a useless legacy setting.
- Recommended solution: Replace all active docs/examples/onboarding settings with `TERMINAL_COMMAND_DENYLIST`. Remove or clearly mark generated JS artifacts as stale/non-authoritative if they are retained.
- Implementation priority: P0 before user testing.
- Estimated effort: 0.5-1 day.
- Trade-offs: A denylist increases command reach, so docs must emphasize confirmation gates, file-root limits, and blocked destructive tokens.

### M2. New v7.3 endpoint contracts need direct regression tests

- Severity: Medium
- Root cause: Recent endpoint changes are correct by inspection, but not all have focused tests.
- Evidence: New/changed contracts include `/coding/readiness` at `apps/backend/api/server.py:2376`, `/capabilities.trust` at `apps/backend/api/server.py:2219`, `/memory/compact` at `apps/backend/api/server.py:2136`, and signed routine webhooks at `apps/backend/api/server.py:2868`. Existing tests cover provider readiness and memory doctor, but not all these endpoint contracts.
- Expected impact: UI may silently break again if response models filter fields, query/body compatibility regresses, or webhook signing is bypassed.
- Recommended solution: Add tests for response shape, query params, missing MCP client warning, provider `ok` mapping, memory compact by query string, and webhook HMAC reject/accept.
- Implementation priority: P1 before manual test sweep.
- Estimated effort: 0.5-1 day.
- Trade-offs: Requires FastAPI test dependencies in the dev environment.

### M3. WebSocket gateway has no visible auth gate

- Severity: Medium
- Root cause: `/gateway/ws` accepts WebSocket connections directly and then processes query payloads.
- Evidence: WebSocket is accepted at `apps/backend/api/server.py:3795`; UI connects at `apps/web/src/index.tsx:3665`.
- Expected impact: Same local-first assumption as REST API. If exposed, external clients can start chat/query streams.
- Recommended solution: Reuse the API auth token for WebSocket handshake/query param/header, or restrict to localhost by default.
- Implementation priority: P1 for network deployment; P2 for purely local dev.
- Estimated effort: 0.5 day.
- Trade-offs: Browser WebSocket auth headers are awkward; query token is simpler but must not be logged casually.

### M4. Generated JS artifacts in `src/` can drift from TypeScript source

- Severity: Medium
- Root cause: The repo contains `.tsx` source plus compiled `.js` and `.d.ts` siblings under `apps/web/src`.
- Evidence: `apps/web/src/index.tsx` uses terminal denylist UI around `apps/web/src/index.tsx:5474`; stale `apps/web/src/index.js` still has "Terminal Allowlist" around its matching old generated block. TS config includes `src` but does not enable `allowJs`.
- Expected impact: Humans and agents may inspect the wrong file. If tooling or imports ever resolve to `.js`, stale behavior could ship.
- Recommended solution: Decide whether generated JS/DTS files are source artifacts. If not, remove them from source control and ignore them. If yes, rebuild them whenever TS changes.
- Implementation priority: P1.
- Estimated effort: 0.5 day cleanup plus build verification.
- Trade-offs: Removing generated files reduces confusion but may affect any workflow that expected them.

### M5. API route surface is broad for one file and one rate limit

- Severity: Medium
- Root cause: `/api/server.py` combines settings, query, memory, documents, social integrations, A2A, routines, vision, provider switching, and todos.
- Evidence: Route count is about 102. Rate limiting is global IP-based at `apps/backend/api/server.py:1327`.
- Expected impact: Endpoint interactions are harder to reason about. A single global rate limit may be too blunt for expensive `/query`, uploads, memory, and lightweight health routes.
- Recommended solution: Group routers by domain and add per-route-class limits: query/stream/upload/provider switch/webhooks should be stricter than reads.
- Implementation priority: P2.
- Estimated effort: 2-4 days.
- Trade-offs: Refactor risk. Do after tests lock current behavior.

### M6. Local model tool-calling mode is still a major behavioral variable

- Severity: Medium
- Root cause: Local providers can run native tool calling, tool_calling_llm wrapper, JSON action parser, or direct LLM fallback depending on config/provider.
- Evidence: Tool-calling diagnostics live at `apps/backend/agent/core.py:4497`; mode labels at `apps/backend/agent/core.py:4514`; Stage 4 branch metadata at `apps/backend/agent/core.py:7832`.
- Expected impact: The same user request may behave differently across Gemma/Qwen/Ollama/LM Studio, creating "agent got dumb" reports that are actually mode/branch differences.
- Recommended solution: During testing, record provider, model, `tool_calling_mode`, and `stage4_branch` for every failure. Add a UI badge or debug drawer for this metadata.
- Implementation priority: P1 for debugging.
- Estimated effort: 0.5-1 day.
- Trade-offs: More visible metadata can overwhelm users; keep it collapsed or in diagnostics.

## 6. Low-Risk Improvements

### L1. Update old version labels in test rundown and audit docs

- Severity: Low
- Root cause: Documentation accumulated over releases.
- Evidence: `apps/backend/TEST_RUNDOWN.md:435` still says generated for v6.7.0, while active docs now reference v7.3.
- Expected impact: Confuses testing order and bug reports.
- Recommended solution: Refresh test rundown header/date and mark old sections as historical where needed.
- Implementation priority: P2.
- Estimated effort: 0.5 day.
- Trade-offs: None beyond documentation time.

### L2. Make "Memory saved" event less noisy in user-facing chat

- Severity: Low
- Root cause: The stream still emits memory saved metadata after successful memory count changes.
- Evidence: Backend emits `memory_saved` at `apps/backend/api/server.py:1091`; UI reacts at `apps/web/src/index.tsx:3537`.
- Expected impact: User sees operational noise, especially while memory doctor already exists.
- Recommended solution: Keep event for state refresh, but render it only in a subtle status area or diagnostics mode.
- Implementation priority: P2.
- Estimated effort: 0.5 day.
- Trade-offs: Hiding it too much may make memory behavior feel invisible.

### L3. Document exactly which integrations are "real", "configured", and "planned"

- Severity: Low
- Root cause: Skills, settings, docs, and UI list many integrations at different maturity levels.
- Evidence: Skills include Discord, Telegram, WhatsApp, Slack, email, GitHub, Notion, Spotify, smart home, A2A, etc. Core runtime has deeper direct support for some but not all.
- Expected impact: User may assume every listed skill is equally production-ready.
- Recommended solution: Add integration maturity badges: built-in, skill stub, external bridge required, planned, disabled by config, missing dependency.
- Implementation priority: P2.
- Estimated effort: 1 day.
- Trade-offs: More UI/documentation surface, but much clearer.

## 7. Security Findings

Security posture is good for local trusted personal use and insufficient for network production.

Key strengths:
- System actions default off in `.env.example`.
- File tools are constrained to `FILE_TOOL_ROOT` and extra roots.
- Terminal commands are confirmation-gated and first-token denylisted.
- Destructive/moderate tool metadata is visible.
- Discord role handling prevents PUBLIC users from owner memory and dangerous tools.
- Webhook signing exists for routine webhooks when a global secret is configured.
- Settings validation warns on missing tokens/secrets for key integrations.

Security gaps:
- No global auth for most API/WebSocket endpoints.
- Default host example is `0.0.0.0`.
- FAISS dangerous deserialization must not touch untrusted files.
- MCP trust metadata exists but real MCP sandbox/transport/auth is missing.
- A2A can be enabled without auth key, currently warned but not prevented.
- Document upload/RAG should be treated as prompt-injection-bearing untrusted input.

Recommended security rule:
EchoSpeak should be safe-by-default on `localhost`, explicit-before-network, and deny-by-default for any new executable integration.

## 8. Performance Findings

- Memory vector store caching exists with `_vector_stores`, which helps repeated loads.
- Provider readiness preflight avoids slow failed agent loops when LM Studio/Ollama are down.
- Research parsing and structured evidence prevent the UI from repeatedly reparsing raw tool text.
- Large single files increase frontend rebuild/typecheck and human/agent review time.
- The web search tool does ranking, recency logic, fallback, and output formatting in one function; good behavior but hard to profile in pieces.
- Query streaming and background memory extraction are good latency choices, but they need tests for race conditions around memory count and UI refresh.

Recommended next step:
Add timing metrics per Stage 1-5 plus tool-call counts to execution traces so slow requests can be diagnosed by branch, not vibes.

## 9. Reliability Findings

Reliability has improved meaningfully in v7.3:
- Provider readiness is explicit.
- Stage 4 branch and tool-calling mode are recorded.
- Referential follow-ups use current subject.
- Coding workspace now has a concrete lifecycle.
- Reflection has deterministic checks before LLM judgment.

Remaining reliability risk:
- New behavior is not fully covered by endpoint tests.
- Large files make unrelated regressions likely.
- Local model tool-calling mode differences can look like random reasoning failures.
- Settings/docs drift can lead testers into invalid states.
- Missing MCP client is visible but still a gap.

## 10. AI Architecture Review

EchoSpeak uses a harness-first architecture:
- Stage 1: parse/preempt, approvals, routing, action parser, workspace selection.
- Stage 2: memory/doc/profile/time/context construction.
- Stage 3: shortcut web/research paths.
- Stage 4: LangGraph -> AgentExecutor -> fallback.
- Stage 5: direct LLM fallback, cleanup, TTS, memory recording.

This is appropriate for a small local model because the harness compensates for model weakness. The goal should not be to remove the harness. The goal should be to make the harness deterministic where possible and measurable where not.

Compared to current agent patterns:
- Claude Code emphasizes project memory files, concise loaded context, and hooks for enforcement. Echo has analogous workspaces/docs but needs stricter stale-doc cleanup.
- Letta persists messages, runs, steps, memory blocks, and tool calls in a database. Echo has state store/traces/memory, but it is more file-based and less queryable.
- LangGraph emphasizes durable execution, human-in-the-loop, persistence, and debugging. Echo uses LangGraph, but much orchestration remains custom.
- OpenAI Agents SDK emphasizes small primitives, guardrails, sessions, tracing, MCP, and sandbox agents. Echo has many of these concepts, but not yet a clean sandbox story.
- MCP security guidance reinforces that tool metadata, transport, auth, and command visibility are security boundaries, not UI decoration.

## 11. Memory System Review

Current memory shape:
- Profile memory: deterministic facts in `profile.json`.
- Vector memory: FAISS semantic store for conversation and durable items.
- Typed memories: `preference`, `profile`, `project`, `contacts`, `credentials_hint`, `note`.
- Pinned memory: injected with tight budget.
- Memory doctor: reports duplicates, type coverage, pinned/profile counts, raw conversation auto-store state.
- Raw conversation auto-store is off by default.

This is aligned with the user's goal: Echo should not memorize every conversation by default. It should remember high-signal facts and have a tool/UI to look back.

Gaps:
- Memory doctor exists, but memory editing/review still needs to feel more like Claude Code `/memory`: visible, editable, and understandable.
- FAISS load trust boundary needs explicit warning.
- Long-term "lesson" memory is still text tips, not retrievable action sequences.
- Memory compaction is exact/near duplicate oriented; stale-memory review is not yet mature.

Recommended memory direction:
Adopt a three-tier memory policy:
1. Working context: recent chat/thread state, not long-term.
2. Durable pinned/profile memory: small, visible, editable.
3. Searchable archives: retrievable by tools, not blindly injected.

## 12. Infrastructure Review

Current infrastructure is local-dev oriented:
- Backend defaults to FastAPI/uvicorn.
- Frontend uses Vite.
- Local providers include Ollama, LM Studio, LocalAI, llama.cpp, vLLM.
- State is file-based under `apps/backend/data`.
- Tests exist for backend pytest and frontend vitest.

Infra gaps:
- Git is not on user PATH by default; previous workflow required bundled Git.
- Frontend dependencies may not be installed in the sandbox, blocking `npm/pnpm` checks.
- Default API bind address should be revisited before production.
- No container/sandbox strategy for coding-agent execution yet.
- MCP server isolation is not implemented.

Recommended infra path:
Keep local-first, but define two modes:
- Developer mode: localhost, low friction, tools opt-in.
- Hosted/network mode: auth required, localhost-by-default disabled only intentionally, stricter rate limits, signed webhooks, and isolated execution.

## 13. Documentation Audit

Good docs:
- `docs/AGENT.md` is now a useful developer map.
- `docs/INTEGRATIONS.md` captures v7.3 endpoint contracts and integration doctor coverage.
- `docs/AGENTIC_BASELINE_2026.md` gives strategic direction.
- `CHANGES.md` has current v7.3 notes.

Docs needing cleanup:
- Terminal allowlist references remain in `README.md`, `.env.example`, `TEST_RUNDOWN.md`, `onboard-tui`, and stale generated JS.
- `AUDIT.md` is older and overlaps with this pre-testing audit.
- `TEST_RUNDOWN.md` still carries v6.7 language and old terminal behavior.
- Integration docs should distinguish "implemented runtime" vs "skill present" vs "external bridge required" vs "planned".

## 14. Code Quality Review

Strengths:
- Types and Pydantic models exist for many API contracts.
- Tool metadata centralization is improving.
- Tests cover many routing, memory, provider, Discord, and reflection paths.
- Code comments often explain safety boundaries.

Concerns:
- Giant files make reasoning difficult.
- Some generated JS/DTS artifacts live beside TS sources.
- Multiple routing systems coexist: router, action parser, heuristics, workspace auto-detect, Stage 3 web shortcuts, LangGraph tools.
- Several docs refer to previous behavior.

Recommendation:
Freeze feature work briefly and add targeted tests for the v7.3 surfaces, then extract modules carefully.

## 15. Technical Debt Assessment

Highest debt:
- Monolithic `core.py`, `server.py`, `index.tsx`.
- Mixed generated/source files in frontend.
- Stale allowlist docs/settings.
- MCP partial implementation.
- No global API auth model.
- No isolated coding sandbox.

Debt that is acceptable for now:
- Custom five-stage harness, because it compensates for local small-model weakness.
- File-based state, because local-first beta is the current wedge.
- Heuristic routing, as long as failures are measured and tests cover known cases.

## 16. Missing Tests

Add before manual testing:
1. `/coding/readiness` returns provider `ok`, required tools, file roots, terminal denylist, and recommendations.
2. `/capabilities` includes `trust`, risk counts, MCP configured count, MCP missing-client warning, and per-tool risk metadata.
3. `/memory/compact` accepts `thread_id` by query and JSON body.
4. `/webhooks/{path}` rejects invalid HMAC when a global secret exists and accepts valid HMAC.
5. Referential follow-up: user asks a subject, then "do a deeper search"; resolved query retains subject.
6. Coding request on Desktop: agent uses file roots or reports exact missing root gate.
7. Terminal denylist: harmless `echo` is not blocked by first-token allowlist, destructive tokens are blocked.
8. Stream event ordering: thinking/task_plan/tool events append in timeline and do not pin old plans to top.
9. Memory saved event remains state-only/subtle after UI cleanup.
10. MCP configured but missing client produces visible warning, not silent failure.

## 17. Production Readiness Score

Score by axis:
- Architecture: 7/10
- Local safety: 7/10
- Network safety: 4/10
- Tool governance: 7/10
- Coding-agent loop: 6.5/10
- Memory: 6.8/10
- Observability: 6/10
- Test coverage: 5.5/10
- Documentation accuracy: 6/10
- UI maturity: 6.5/10

Overall:
- Local personal beta: 6.2/10
- Public or shared deployment: 4.5/10

## 18. Deployment Readiness Checklist

- [x] Local provider readiness check exists.
- [x] Settings validation warns on missing important integration secrets.
- [x] Tool risk metadata exists.
- [x] Confirmation records exist.
- [x] Memory doctor exists.
- [x] Thread state exists.
- [x] Basic rate limiting exists.
- [x] Global API auth for non-health endpoints.
- [x] WebSocket auth.
- [ ] Default network binding reviewed.
- [x] MCP client implemented or clearly disabled.
- [ ] Stale allowlist docs/settings removed.
- [ ] FAISS trusted-state warning/permission check.
- [x] Endpoint contract tests for new v7.3 trust/auth continuity surfaces.
- [ ] Coding-agent sandbox story.
- [ ] Repeatable evaluation scenarios.

## 19. Prioritized Action Plan

P0 - before manual testing:
1. Clean terminal denylist documentation/settings drift.
2. Add endpoint tests for `/coding/readiness`, `/capabilities.trust`, `/memory/compact`, and signed webhooks.
3. Mark MCP as unavailable/planned unless `mcp_client.py` is implemented. Completed for `/capabilities.trust`; implementation remains a v7.5 feature.
4. Add a clear note that current backend is local-first and should not be exposed without auth. Completed with optional shared-key API/WebSocket auth settings.

P1 - during v7.3/v7.4:
1. Add coding-loop scenario tests: create file, inspect workspace, run verification, explain blockers.
2. Add UI event-order regression tests for thinking/task-plan/tool event rendering.
3. Add memory doctor UI polish and make memory saved less noisy.
4. Add trace timing by pipeline stage and tool mode.

P2 - v7.5 and beyond:
1. Implement MCP client with trust registry and sandbox posture.
2. Split `server.py`, `core.py`, and `index.tsx` along stable domain boundaries.
3. Add local sandbox/container option for coding commands.
4. Add automated benchmark suite for local-model agent reliability.

## 20. Immediate Fixes Before Testing

Do these before Ty's next manual run:
1. Replace `TERMINAL_COMMAND_ALLOWLIST` with `TERMINAL_COMMAND_DENYLIST` in `.env.example` and onboarding.
2. Update README and test rundown terminal wording.
3. Remove or regenerate stale frontend `.js/.d.ts` artifacts if they are not source-of-truth.
4. Add the four v7.3 endpoint tests.
5. Run backend reflection/provider/memory tests.
6. Run frontend typecheck/test when dependencies are available.
7. In the UI, test the exact reported flows:
   - coding request on Desktop,
   - harmless terminal command,
   - live score query,
   - "do a deeper search" follow-up,
   - Discord recap,
   - memory doctor and compaction,
   - tool trust panel.

## 21. Long-Term Strategic Improvements

1. Make Echo's advantage explicit: small local model plus strong deterministic harness.
2. Build an eval suite that proves local 4B/8B models can pass real tasks through harness support.
3. Promote operational lessons from text snippets into retrievable action playbooks.
4. Treat memory as governed state: ingest, revise, forget, retrieve.
5. Make MCP a first-class trust surface, not just a tool list.
6. Add a real sandbox story for coding and desktop actions.
7. Split personality/SOUL from internal tool-selection prompt where possible.
8. Keep user-facing reasoning transparent, but keep internal stage labels mostly diagnostic.

## 22. Future Scaling Recommendations

For a single-user local beta:
- Keep file-based state.
- Keep system actions opt-in.
- Keep provider readiness and memory doctor visible.
- Add scenario tests and endpoint tests.

For a private multi-device deployment:
- Require auth on all endpoints.
- Bind to localhost by default and require explicit network mode.
- Add TLS/reverse-proxy docs.
- Add per-user/session permissions.

For a multi-user/community deployment:
- Move state to a database.
- Add RBAC.
- Add audit logs for every action tool.
- Add sandboxed execution.
- Add signed MCP manifests and server allow/deny policy.
- Add CI for backend, frontend, and integration contract tests.

## Final Judgment

EchoSpeak is not fundamentally missing "agentic capability." It is missing enough hard edges around that capability: contract tests, accurate docs, endpoint auth posture, MCP implementation clarity, and sandbox boundaries.

The best next move is not another broad feature pass. The best next move is a short reliability lock-in pass, then Ty tests the exact workflows that motivated v7.3:
- coding agent loop,
- memory doctor,
- tool trust,
- Discord/research continuity,
- transparent reasoning/task-plan UI,
- terminal denylist behavior.

If those pass, EchoSpeak is ready for the next branch. If they fail, the new diagnostics should tell us whether the failure is provider readiness, tool-calling mode, Stage 4 branch, workspace/tool availability, memory continuity, or UI rendering.
