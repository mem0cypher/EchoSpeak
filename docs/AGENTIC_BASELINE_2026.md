# EchoSpeak Agentic Baseline - July 4, 2026

This document captures where EchoSpeak stands after the v7.1.2 cleanup pass and what should come next. It compares EchoSpeak against current agentic AI patterns from Claude Code, Letta, LangGraph, OpenAI Agents SDK, OpenHands, CrewAI, and MCP.

## Current Position

EchoSpeak is best understood as a local-first personal agent runtime:

- It has a broad tool surface: web search, Discord, browser/desktop automation, file tools, terminal, document memory, schedules, routines, voice/avatar UI, and MCP hooks.
- It has a five-stage query pipeline, task planning, reflection, approval records, execution records, trace persistence, and thread-scoped state.
- It now uses a terminal denylist instead of a narrow allowlist, so normal coding commands are not blocked only because they were not prelisted.
- It has three memory layers: deterministic profile memory, vector memory, and compact operational lessons.
- It has a coding workspace that can inspect and modify files under `FILE_TOOL_ROOT` plus configured extra roots such as the user's Desktop.

The main gap is not raw capability. Echo has many tools. The gap is reliability of orchestration: choosing the right path, recovering from bad tool calls, keeping memory lean, and making failures obvious.

## External Baseline

### Claude Code

Claude Code separates user/project-authored instructions from agent-authored auto memory. Its docs describe `CLAUDE.md` files for persistent project instructions and auto memory for notes written from corrections and preferences. Both are context, not hard enforcement; enforcement belongs in hooks such as `PreToolUse`. Source: [Claude Code memory docs](https://code.claude.com/docs/en/memory).

EchoSpeak already has similar building blocks:

- `SOUL.md` and workspace prompts act like behavioral/project instructions.
- `agent_lessons.json` is the start of auto memory for operational lessons.
- Tool approval and denylist checks are closer to enforcement hooks.

Missing adaptation:

- Add an Echo-native `ECHO.md` or `.echo/rules/*.md` layer for project instructions.
- Keep operational lessons capped, editable, and visible in the UI.
- Treat memory as advisory context, while policy gates remain hard enforcement.

### Letta

Letta presents itself as a memory-first agent. Its memory model includes durable agent memory, conversations as separate sessions, self-editing memory, `/doctor` style audits, background "dream" reflection, and git-backed MemFS with directly inspectable markdown files. Source: [Letta memory docs](https://docs.letta.com/letta-agent/memory).

EchoSpeak is moving in the same direction but should avoid saving every conversation. The right target is:

- Profile facts: always compact and deterministic.
- Operational lessons: compact, deduped, and loaded into system prompt only when useful.
- Long conversation history: searchable archive, not always injected memory.
- Memory maintenance: periodic compaction and stale-memory audit.

Missing adaptation:

- Add a memory doctor command or UI action that reports token load, duplicate memories, stale entries, and misplaced facts.
- Add a background lesson distiller that reviews recent failed traces and writes only durable lessons.
- Consider a markdown-projected memory layer for user-readable memory.

### LangGraph

LangGraph is strongest as an orchestration runtime: durable execution, streaming, human-in-the-loop, persistence, and stateful memory for long-running agents. Source: [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview).

EchoSpeak currently uses LangGraph as one stage in a broader cascade. That is reasonable, but the current five-stage pipeline should be treated as Echo's product-level control plane, not as visible "reasoning" to the user.

Missing adaptation:

- Move more long-running tasks into resumable state objects instead of one-turn plans.
- Add explicit task state transitions: planned, running, blocked, waiting_for_user, retrying, failed, done.
- Make crash/resume behavior first-class for coding and research tasks.

### OpenAI Agents SDK

The OpenAI Agents SDK emphasizes a small primitive set: agents with tools/instructions, handoffs, guardrails, sessions, human-in-the-loop, tracing, MCP server tools, and sandbox agents. Source: [OpenAI Agents SDK docs](https://openai.github.io/openai-agents-python/).

EchoSpeak has many equivalent pieces but they are spread across the codebase:

- Agents/tools: `EchoSpeakAgent`, tool registry, workspaces.
- Guardrails: approval records, action parser, denylist, policy flags.
- Sessions: thread state and trace persistence.
- Tracing: execution records and trace files.
- Sandbox/workspace: `FILE_TOOL_ROOT`, extra roots, terminal execution settings.

Missing adaptation:

- Collapse user-facing concepts into fewer primitives: Agent, Workspace, Tool, Task, Memory, Approval, Trace.
- Add guardrail results as structured events, not just errors or hidden policy checks.
- Introduce a coding sandbox story that is clearer than "host terminal with denylist".

### MCP

MCP standardizes tools, resources, and prompts exposed by servers, with discovery and execution through JSON-RPC. Source: [MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture). The MCP security docs warn that local MCP servers can run with client privileges and recommend exact command visibility, explicit approval, sandboxing, restricted filesystem/network access, and strong auth for non-stdio transports. Source: [MCP security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices).

EchoSpeak has a good start:

- MCP tools are server-scoped as `mcp__server__tool`.
- Action-like tools still follow the same approval posture.
- Local action tools are already policy-gated.

Missing adaptation:

- Store MCP server manifests with trust state, version, command, transport, scopes, and last approval.
- Add an MCP safety scanner for suspicious descriptions, broad scopes, network access, and filesystem access.
- Render MCP tools/resources/prompts separately in the UI instead of flattening everything into one tool list.

### OpenHands

OpenHands focuses on AI-driven development with a browser UI/backend stack, local/hosted modes, a CLI, integrations, and a composable software agent SDK. Source: [OpenHands introduction](https://docs.openhands.dev/overview/introduction).

EchoSpeak is broader than OpenHands because it includes personal channels and automation, but OpenHands is tighter around coding tasks.

Missing adaptation:

- Add a stronger coding project lifecycle: initialize, inspect, plan, implement, verify, summarize.
- Show code tasks as artifacts, diffs, terminal results, and next steps in one coherent coding surface.
- Prefer isolated project folders or sandbox sessions for generated projects.

### CrewAI

CrewAI separates structured Flows from autonomous Crews. Flows manage state, events, and control flow; Crews handle autonomous collaboration and delegated tasks. Source: [CrewAI introduction](https://docs.crewai.com/en/introduction).

EchoSpeak's equivalent should be:

- Flow: deterministic pipeline, state machine, approvals, scheduling, retries.
- Agent: LLM reasoning and tool selection.
- Specialist: optional future subagents for research, coding, memory doctor, and QA.

Missing adaptation:

- Make workflows explicit for common intents instead of relying on one general agent loop for everything.
- Add specialist subagents only where they reduce complexity: coding QA, research verification, memory compaction.

## Runtime Audit From July 4 Log

The pasted runtime log confirms several things:

- Coding auto-detect worked: Echo promoted a coding/game request into the coding workspace.
- The request then failed with `Connection error`, which points to provider/runtime availability rather than the coding intent detector.
- LM Studio model listing failed on `localhost:1234`, so the UI should surface "provider unavailable" earlier and more clearly.
- Discord bot health-check reported disconnected; Echo correctly logged the likely privileged-intents/token issue.
- Memory vectorstore loaded repeatedly during one request, which suggests redundant agent/memory initialization or repeated lazy-load calls. This is a performance hotspot.
- Pipeline stage logs still exist internally. That is fine for debugging, but the user-facing reasoning stream should show model/tool activity, not the fixed five-stage scaffold.

## What EchoSpeak Is Doing Right

- Local-first control: local providers, local filesystem tools, desktop/browser automation, and user-owned data.
- Broad integration surface: Discord, Telegram, Twitter/Twitch history, web search, file tools, terminal, routines, and documents.
- Approval-backed action model: file writes, terminal, desktop/browser automation, and send actions are gated.
- Thread state and traces: enough control-plane structure exists to support auditability.
- Reflection and task plan events: the frontend can show progress instead of hiding tool execution.
- Recent memory shift: raw conversation auto-save is off, which is the right direction.

## Main Gaps

1. Provider readiness is not front-and-center enough.
   If LM Studio is down, Echo should say "model provider unavailable" before starting a large tool/coding path.

2. Coding needs a lifecycle, not just tools.
   For coding requests, Echo should consistently inspect, plan, create/edit, verify, and summarize. The UI should show this as a coding task, not generic chat.

3. Memory needs an audit/doctor flow.
   Echo should distinguish profile facts, operational lessons, project rules, searchable history, and stale memories.

4. MCP needs trust metadata.
   MCP is powerful, but local servers are effectively executable code. Echo needs visible trust, scopes, command display, and sandbox posture.

5. Observability needs a simpler story.
   The trace system exists, but the UI should summarize why a task failed: provider down, tool unavailable, denied by policy, bad result, timeout, or user approval needed.

6. The five-stage pipeline should stay internal.
   It is useful architecture, but the user wants visible reasoning/tool progress, not a repeated stage template.

## Recommended Roadmap

### v7.2 - Agent Reliability

- Add provider readiness checks before invoking heavy agent/coding paths.
- Convert common tool failures into structured failure reasons.
- Add a "next action" recovery message when a provider/tool is unavailable.
- Rename or clarify debug fields like `allowed_tool_names=frozenset()` when empty means "workspace does not restrict beyond policy".

### v7.3 - Coding Agent Lifecycle

- Add a coding task state machine: inspect -> plan -> implement -> verify -> summarize.
- Create a project initializer for simple HTML/JS games/sites that writes a clean folder, not root-level `index.html` and `script.js`.
- Add terminal result classification: passed, failed, timed out, denied, provider unavailable.
- Add a runbook in the coding workspace prompt for generated projects.

### v7.4 - Memory Doctor

- Add `/memory doctor` or a UI action that reports memory count, duplicate clusters, stale memories, pinned/profile facts, and token budget.
- Add memory compaction for old raw conversations already stored.
- Add an operational lesson review UI: approve, edit, pin, delete.
- Consider markdown-projected memory so users can inspect durable knowledge like a small local knowledge repo.

### v7.5 - MCP Trust Center

- Add MCP server registry with name, command, transport, scopes, trust status, last seen version, and last approval.
- Separate MCP tools, resources, and prompts in the UI.
- Add scanner warnings for broad filesystem/network access or suspicious tool descriptions.
- Prefer stdio/local sandbox defaults for local servers and require explicit approval for one-click server startup commands.

### v7.6 - Evaluation Harness

- Add repeatable scenarios:
  - "Create a top-down HTML/JS game on Desktop"
  - "Find current live sports score"
  - "Read Discord channel and summarize"
  - "Remember my name, then answer it later"
  - "Provider offline during coding request"
- Score success using task completion, tool choice, retries, and user-facing recovery clarity.

## Baseline Principle

EchoSpeak should not become a bigger pile of tools. It should become a tighter agent runtime:

- fewer visible concepts,
- clearer task state,
- stronger provider/tool readiness checks,
- memory that learns without hoarding,
- MCP that expands capability without silently expanding risk,
- and a UI that shows real work in progress without exposing internal pipeline scaffolding.
