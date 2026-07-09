# UI Agent State Map (from live code — v7.4)

Research pass before UI work. Sources: `agent/stream_events.py`, `api/server.py` `_StreamingHandler` + `/query/stream`, frontend `index.tsx` consumer.

## Two stream channels (do not confuse)

| Channel | Path | Format | Used by Web UI chat? |
|---------|------|--------|----------------------|
| **Primary query stream** | `POST /query/stream` | NDJSON queue events from `_StreamingHandler` + agent thread | **Yes** (default) |
| **StreamBuffer API** | `GET /stream/{request_id}` | `StreamEvent` (`stream_events.py`) | Secondary / diagnostics; agent also mirrors task_* into both |

## Primary NDJSON events (what the chat UI actually parses)

Defined in `apps/web/src/index.tsx` `AgentStreamEvent` and produced mainly by `api/server.py`:

| `type` | When emitted | Payload (key fields) | Reliability |
|--------|--------------|----------------------|-------------|
| `thinking` | LLM reasoning / `<think>` / pipeline notes / loop warning | `content`, `at`, `request_id` | **High** when model streams reasoning or callbacks fire; content is **cumulative**, not delta |
| `thinking_step` | Core `_emit_thinking_step` (search/read/tool/thought labels) | `step_type`, `content`, `status` (`running`\|`done`), `at` | **Medium** — TaskPlanner & grounder paths; not every LangGraph tool loop |
| `tool_start` | LangChain `on_tool_start` **or** agent `_emit_tool_start` | `id`, `name`, `input`, `at` | **High** for real tool calls via callbacks; also Stage-3 grounder multi-candidate starts |
| `tool_end` | `on_tool_end` / `_emit_tool_end` | `id`, `name?`, `output`, optional `research`, `at` | **High** paired with start when same `id` |
| `tool_error` | Tool failure | `id`, `error`, `at` | **High** on failure path |
| `status` + `agent_mode` | On tool_start (`research`/`coding`/`working`), on final (`idle`) | `agent_mode`, optional `tool` | **Medium** — mode set on start; **not** reset to idle between tools; final forces idle |
| `task_plan` | TaskPlanner plan emit | `data`: task list | Only multi-task planner path |
| `task_step` | Task status transitions | `data`: index, status, tool, result_preview | Planner only (`pending`→`running`→`done`/`failed`/`retrying`/`awaiting_confirmation`) |
| `task_reflection` | Reflection accept/reject | `data`: index, accepted, reason, cycle | Planner + reflection only |
| `memory_saved` | After turn memory write | `memory_count` | When memory records |
| `final` | Turn complete | `response`, `spoken_text?`, `success`, `memory_count`, `doc_sources?`, `research?`, `execution_id?`, `thread_state?` | **Always** on success path (or replaced by `error`) |
| `error` | Uncaught agent failure | `message` | Fail path |

### Not on primary chat path today

| Event | Where defined | Gap |
|-------|---------------|-----|
| `agent_token` | `StreamBuffer.push_token` | **Not** put on `/query/stream` queue for assistant reply text. Final answer arrives as one `final.response` blob. |
| Context compact stage | Backend has budget reports | **Not** streamed as a first-class NDJSON type to the UI. |
| Search grounding accepted/insufficient | Stored on agent + tool output marker | Surfaced inside `tool_end.output` / research object when web_search; **no dedicated event type**. |
| Confirmation pending | Pending action in thread state / final response text | Detected by **frontend regex** on assistant text (`confirm` prompt), not a stream event. |

## StreamBuffer-only types (`stream_events.py`)

`tool_start`, `tool_chunk`, `tool_end`, `tool_error`, `agent_token`, `status`, `task_plan`, `task_step`, `task_reflection`.

## Frontend consumption today

- **Thinking card**: typewriter over cumulative `thinking.content`; steps from `thinking_step` + derived from `tool_start`/`tool_end`.
- **Avatar**: driven by `streaming`, `agentMode` (from status), `pendingConfirmation` (text heuristic), random idle micro-behaviors when not busy — **not** a single shared state machine with the chat panel.
- **Final reply**: instant full text on `final` (typewriter only on thinking card).
- **Studio**: shares left column with avatar; `leftTab !== chat|research` opens studio panels in a constrained layout.

## Canonical UI phases (target)

Map only signals that actually fire:

| Phase | Primary signals | Notes |
|-------|-----------------|-------|
| `idle` | not streaming; or `status.agent_mode=idle` after final | |
| `thinking` | `thinking` content growing, or LLM start, no open tools | Loading spinner only if no content yet |
| `streaming_reply` | (post-v7.4.5) `agent_token` deltas; else typewriter on `final` | |
| `tool_search` | `tool_start` name=web_search / mode research | |
| `tool_file` | file_* / artifact tools | |
| `tool_terminal` | terminal_run | |
| `tool_generic` | other tools | |
| `awaiting_confirm` | final text is confirm prompt **or** pending approval API | Must stay until confirm/cancel |
| `error` | `error` or `tool_error` with no recovery | |
| `task_running` | `task_step` status running | Optional overlay |

## Reliability fixes required before polish animations

1. Emit `status.agent_mode=thinking` on LLM start; emit `idle` (or prior) on tool_end when no tools remain.
2. Emit `agent_token` for non-reasoning content tokens on `/query/stream` so reply can stream live.
3. Prefer single `AgentActivity` reducer in the frontend so avatar + chat never diverge.
