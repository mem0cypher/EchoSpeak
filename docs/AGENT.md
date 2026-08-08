# EchoSpeak Agent Developer Guide

This guide describes the active EchoSpeak 8.0 runtime. Historical release notes
are intentionally excluded because they had become a misleading second
architecture document.

## Product model

Echo is the user-facing personal agent. Chat is the conversational surface.
Visualizer is a read-only projection of durable work. Work and Code are
Visualizer panels, not separate agents or top-level desktop applications.
Settings is a centered configuration modal.

The governing rule is:

```text
GRAPH OUTSIDE. LOOPS INSIDE.
```

TaskRun owns an actionable objective, requirements, and its execution graph.
The selected Echo model reasons inside a bounded agent node. A ToolRun owns one
capability execution. A SpecialistRun owns the bridge to Codex or OpenCode.
Only the canonical completion evaluator and finalization gate may complete the
overall request.

## Production entry points

- `agent/core.py::EchoSpeakAgent.process_query()` enters
  `CanonicalSemanticRuntime`.
- `agent/semantic_runtime.py` owns Turn Understanding, TaskRun arbitration,
  per-Turn authority, the bounded model loop, and finalization handoff.
- `agent/model_control_plane.py` validates model decisions and enforces loop,
  tool, malformed-output, retry, elapsed-time, and no-progress budgets.
- `agent/task_runs.py` and `agent/execution_graph.py` own durable objective and
  graph state.
- `agent/state.py` owns Session/Execution/ToolRun/ToolOutcome/Approval records.
- `agent/specialist_runtime.py` and `agent/specialist_store.py` own the
  specialist bridge and normalized specialist events.

Do not restore AgentExecutor, ReAct, LangGraph, TaskPlanner, ReflectionEngine,
the coding ledger, or the in-process coding loop as fallback paths.

## Casual Chat

A validated `casual_conversation` Turn uses the exact Session-selected model,
Echo identity, authorized memory/context, and recent conversation. It creates
an Execution record for lifecycle truth but no TaskRun, graph, requirement,
AgentDecision loop, ToolRun, or SpecialistRun.

An actionable request is decomposed into runtime-validated requirements and
bound to a TaskRun. Follow-ups such as “try again,” “continue,” and “finish
that” may select a recoverable active/recent TaskRun; ordinary social chat
cannot resume work.

## Model loop

The control plane accepts only validated native or structured model decisions:

1. A valid tool call is checked against immutable per-Turn authority and fresh
   current Session, Project, model binding, permissions, policy, configuration,
   inventory, and mutation preconditions.
2. The governed tool creates one durable ToolRun and ToolOutcome.
3. A structured observation returns to the same selected model.
4. Repairable malformed output or rejected proposals receive bounded corrective
   feedback without creating fake ToolRuns.
5. Repeated actions, provider failures, elapsed time, tool count, model
   iterations, and repair count terminate through explicit budget outcomes.
6. A proposed answer passes the one finalization gate.

Printed tool-like prose is inert. Provider/model syntax ends at
`model_adapters.py`. GLM accepts documented native `tool_calls`; Gemma accepts
native calls plus its bounded documented sentinel form. There is no silent
provider or model substitution.

## Tools, approvals, and evidence

All executable capabilities converge on `ToolRegistry`. Connections and MCP
servers are transports/providers, not execution authorities. Mutations require
the existing permission and approval rules. Approval consumption first matches
stable action identity, then revalidates current Session, Project/root, path
and source revisions, tool inventory, model binding, configuration, policy,
permissions, and mutation preconditions.

Tool execution success is not semantic success. Evidence is bound to the exact
TaskRun requirement and attempt. `RequirementCompletionEvaluator` determines
coverage and sufficiency; the model cannot establish it through prose.

`SearchGrounder` is the single web acquisition orchestrator. Pure
`WebEvidenceHeuristics` predicates may classify returned evidence but cannot
call providers, retry research, create ToolRuns, or mutate requirements.

## Specialist coding

Coding work requires an attached Project and a specialist requirement/graph
node. Echo creates a SpecialistRun correlated to the exact TaskRun,
requirement, graph node, Project, runtime session, and runtime turn.

Codex App Server or OpenCode owns its internal coding loop. Echo stores only
normalized progress, approval/input waits, external IDs needed for resume, and
the current terminal outcome. A terminal specialist event updates the
SpecialistRun, projects verified evidence onto the owning requirement,
reevaluates TaskRun readiness, and schedules a canonical Echo continuation.
The callback never answers the user directly.

Claude Code remains discovery/configuration only until a real executable
permission bridge exists.

## Memory

`MemoryCurator` is the durable semantic-memory writer. Memory contains personal
facts, preferences, decisions, project context, and useful summaries. It is
not execution authority and is not a mirror of Notion, Obsidian, or every
external document. Model context remembers conversation; TaskRun and ToolRun
records remember execution.

## Frontend contract

The frontend renders backend IDs and revisions. Navigation never creates a
Session or starts work. The explicit plus button creates a Session. Visualizer
may display graph, requirements, evidence, specialist events, waits, and
recovery, but cannot advance them. Normal Chat language must not expose raw
requirement, ToolRun, or internal diagnostic IDs.

## Extension rule

Any new capability needs:

- one durable owner;
- explicit Session/Project identity and scope;
- ToolRegistry registration for executable operations;
- current authority and approval behavior;
- bounded retries and cancellation;
- structured verification;
- restart recovery;
- a projection-only UI.

Do not add a parallel model loop, hidden provider fallback, completion gate,
permission system, research runtime, or client-owned lifecycle.

See `SYSTEM_ARCHITECTURE.md`, `RUNTIME_CONTRACTS.md`,
`LIFECYCLE_TRUTHFULNESS.md`, and `MODEL_EXECUTION_CONTROL_PLANE.md` for the
canonical detailed contracts.
