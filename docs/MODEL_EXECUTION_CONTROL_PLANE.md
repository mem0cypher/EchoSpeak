# EchoSpeak 8.0 Model Execution Control Plane

**Status:** canonical worktree architecture. The semantic-runtime refactor in
this pass was statically reviewed; its packaging/launch results are reported
separately. This document is the
canonical model-boundary contract. `RUNTIME_CONTRACTS.md` remains canonical for
Project, Session, approval, ToolRun, and persistence authority.

## Authority invariant

`ProjectManager` owns Project identity/root, `ThreadSessionState` owns Session
bindings and references, `TaskRunStore` owns cross-Turn semantic state, and
`ExecutionRecord` owns one Turn's interpretation. The runtime owns tool
inventory, policy, approvals, memory scope, ToolOutcomes, and completion. The
selected model owns semantic understanding and may only return typed proposals.
Model prose, reasoning, or tool syntax never grants authority or establishes
success.

```text
latest message + TaskRun candidates + approvals + bounded context
                         |
            selected-model Turn Understanding
                         v
       documented response-channel extraction
                         v
     bounded canonical property/value normalization
                         v
             validated TurnInterpretation
                         |
           TaskRun CAS + post-understanding policy
                         v
ProjectManager + ThreadSessionState refs + TaskRun + ToolRegistry
        + scoped memory + verified ToolOutcomes
                         |
              ModelTurnEnvelopeCompiler
                         v
                ModelTurnEnvelope
                         |
               selected family adapter
                         |
             AgentDecision (one action batch)
                         |
     runtime validation + authority-wrapped tool call(s)
                         |
        verified durable ToolOutcome(s), each recorded
                         |
              fresh envelope on next loop
```

The canonical owners are:

| Concern | Owner |
|---|---|
| Selected-model semantic boundary | `TurnUnderstandingCompiler` + `TurnInterpreter` |
| Cross-Turn semantic state | `TaskRunStore` |
| Session serialization | `SessionTurnCoordinator` (per Session, not process-global) |
| Runtime-to-model projection | `ModelTurnEnvelopeCompiler` |
| Tool and decision vocabulary | `agent/model_contracts.py` |
| Qwen/Gemma syntax and parsing | `agent/model_adapters.py` |
| Bounded model loop and safe-read batch scheduler | `ModelExecutionControlPlane` |
| Tool authorization and persistence | existing `AuthorityCheckedTool`, `StateStore`, and `ToolRegistry` boundary |
| Durable ToolOutcome | existing `agent.state.ToolOutcome` (re-exported, not duplicated) |
| Native Qwen process | `NativeLlamaCppRuntime` |

## Production path

Every ordinary provider/model uses this path:

1. Create the Turn/Execution before semantic inference or provider failure.
2. Compile the bounded Soul-owned Echo identity, conversation, candidate
   TaskRuns, approvals, deterministic authorized memory/Project context,
   verified outcomes, entities, source, and capability vocabulary into
   `TurnUnderstandingEnvelope`.
3. Ask the selected model for one strict `TurnInterpretation`. OpenAI, Gemini,
   and Ollama use native JSON Schema when their concrete runnable exposes it;
   LM Studio/LocalAI require an explicit model-profile declaration, while
   direct llama.cpp/vLLM use bounded JSON extraction. All paths then use the
   same canonical decoder and strict validator. Reject malformed, ambiguous,
   out-of-scope TaskRun, approval, or capability selection.
4. Create/select/update the TaskRun with revision CAS. Pre-task ambiguity does
   not invent a TaskRun; only a validated `ask_for_input` decision persists a
   complete `suspended_waiting_for_user` checkpoint before emitting its question.
5. Derive mode and a narrow tool inventory from the typed interpretation.
6. Build a fresh envelope from the current Execution, TaskRun, the same identity
   and memory projection, and exact Session/Project.
7. Render the family adapter contract and the current narrow tool schemas.
8. Stream through the selected provider transport.
9. Separate private reasoning, assemble argument fragments, and parse a typed
   `AgentDecision`.
10. Validate the decision against current actions, tool schema, missing input,
   approval state, and completion requirements.
11. Execute every validated independent safe-read ToolCall in the proposal
    through the existing request-time authority wrapper. Mutating or
    approval-bound actions remain single-call proposals.
12. Persist and verify each exact-scope ToolOutcome. A failed call cannot erase
    successful outcomes already recorded for another requirement.
13. Recompile the envelope and reinject the normalized ToolOutcome before the
    next model call.
14. Stop on answer, input request, cancellation, a non-recoverable block, or
    the bounded loop/time/no-progress limit. Plan updates, malformed-output
    repair, premature answers, and retryable proposal failures feed back into
    the same bounded selected-model loop.

An outcome satisfies required evidence only when `execution_status=success`,
its typed `result_state` is usable, and runtime verification is present. A
non-retryable unavailable provider is removed from the current Execution's next
tool projection while remaining allowed tools can serve as governed fallbacks.
Declared missing user input prevents `answer`, but it does not remove `call_tool`:
the model may either ask for genuinely required user data or use an allowed tool
to discover external facts needed to make progress. Exact event-time fields are
non-blocking only for purely informational research/live-sports work; user-owned
locations, recipients, paths, mutation inputs, and approval facts remain blocking.

Execution context is a separate bounded projection: selected TaskRun fields and
plan are direct typed fields; supporting context is limited to current scope,
current-Turn outcomes, short recent conversation, and relevant scoped
memory/documents. Legacy ActiveWork and Session semantic projections cannot
enter this envelope.

Direct runtime-bound helper calls fail closed without an active Execution.
There is no ordinary-Turn compatibility cascade, direct-model fallback, printed
tool execution fallback, or silent model switch. Exact typed approval endpoints
and slash commands are the only pre-understanding controls.

### Liveness and continuation

`failed_provider`, `failed_model_output`, and `failed_tool_parse` preserve the
failed attempt but are resumable TaskRun checkpoints. They are suspended
lifecycle states rather than completed/cancelled/policy-blocked terminal truth.
The failed Execution remains terminal; its owning TaskRun keeps satisfied
requirements and evidence.

An exact continuation phrase or leading command such as `continue`,
`continue and use another source`, `retry`, or `try again for the unfinished
parts` deterministically selects the only eligible
running/background/recoverable TaskRun in the same Session and Project. Any
bounded trailing text becomes a continuation constraint. Waiting-for-input and
waiting-for-approval records are never consumed by this shortcut. Multiple
eligible candidates still require selected-model interpretation.

The default model-loop budget is 12 model calls, 16 ToolCalls, and 600 seconds.
For local models, the runtime raises the total bound from observed
Turn-Understanding latency and the configured provider-call timeout. A newly
persisted ToolOutcome always receives one bounded post-tool synthesis
opportunity before elapsed-time terminalization. Provider-native parallel safe-read
ToolCalls remain a batch at the adapter boundary and are all recorded; the
runtime executes them sequentially so fresh authority validation still occurs
at each governed tool boundary.

The stream owner passes a request-scoped cancellation token. The understanding
wait polls that token, the provider transport checks it while streaming, and the
Session coordinator releases its lock after a durable cancelled terminal state.

### TurnInterpretation compatibility boundary

The model-facing schema is generated from the authoritative Pydantic contract,
with internal titles and `$defs` references removed. It exposes canonical
lower-snake-case properties, the complete inline `relation` enum, one complete
example, compatible nullable fields, and `additionalProperties: false`.

After the provider's documented structured or message-content channel is
extracted, `decode_turn_interpretation_payload` applies only its explicit field
alias table. It also folds whitespace, case, camel-case, and hyphen formatting
for `relation` only when the result exactly names an authoritative enum member.
Unknown keys survive and are rejected by `extra="forbid"`; unknown enum values,
non-scalar relation values, missing required fields, and selected-task invariant
violations remain terminal. Canonical/alias duplicates must be equivalent after
safe primitive normalization or the decoder rejects the payload as ambiguous.
Diagnostics record key names and collision codes, never prompt content, model
reasoning, or conflicting values.

For `new_task` and `switch_task` only, a missing `proposed_objective` may be
folded from already model-authored requirement objectives when every
requirement has a non-empty objective. This bounded structural normalization
does not invent an entity, capability, requirement, or synonym.

Optional Turn selector metadata uses explicit JSON nullability. Omission and
`null` both represent an unused `selected_task_id`, `selected_approval_id`,
`approval_decision`, `proposed_objective`, `requested_operation`, or
`clarification_question`; empty legacy task/approval identifiers normalize to
`null`. Collections remain non-null arrays/objects. `resume_approval` alone may
carry approval fields, requires an `approve`/`cancel` decision, and is still
checked against the current boundary's authorized approval IDs after structural
validation. Other relations reject stale or fabricated approval data.

## Diagnostics and privacy

Diagnostics include contract version, Project/Session/Turn/Execution/request
IDs, exact provider/model/family/adapter/template, task status, tool policy,
allowed tool names, memory types/count, approval status, verified ToolRun IDs,
valid actions, token estimate, parser event counts, argument/content character
counts, hashes, decisions, and loop status.

They exclude full prompts, scoped-memory bodies, credentials, tool-result
bodies, and private reasoning. Provider reasoning channels and `<think>` blocks
are separated for parsing and retained only as length/hash diagnostics; they are
not emitted to chat.

## Family findings

- Qwen works with the exact Jinja chat template embedded in its GGUF. Forcing a
  literal `qwen3` llama.cpp template was invalid for the tested server build.
- Gemma requires its family adapter and a larger conformance generation budget;
  128 tokens truncated tool decisions, while 512 completed all cases.
- Both families reconstructed streamed multi-fragment arguments, consumed
  returned ToolOutcomes, completed two sequential calls, and blocked truthfully
  after a failed tool.
- The tested safe recommendation is at most two narrowly selected exposed tools
  for both exact local models. This is a conformance recommendation, not a
  permission grant.

## Recorded conformance (historical, not rerun in this refactor)

Five live scenarios plus three deterministic parser/runtime scenarios cover the
required eight cases.

| Provider | Exact model | Template | Live result | Recommended exposed tools |
|---|---|---|---:|---:|
| LM Studio | `qwen/qwen3.5-9b` | GGUF model metadata | 5/5 | 2 |
| LM Studio | `google/gemma-4-e2b` | `gemma` | 5/5 | 2 |
| Echo native llama.cpp CPU | `Qwen3.5-9B-Q4_K_M.gguf` | GGUF model metadata | 5/5 | 2 |

Deterministic tests pass streamed argument reconstruction, reasoning/argument
separation, and rejection of prose completion while mandatory tool work is
unverified. Additional tests cover malformed calls, typed non-answer decisions,
truncated finishes, exact ToolOutcome scope, cancellation, and loop bounds.

The native Qwen live cases took about 73-154 seconds each on CPU. LM Studio Qwen
took about 18-32 seconds and Gemma about 9-11 seconds. Those timings describe
this machine and are not product guarantees. The native cancellation smoke
closed an in-flight stream in 1.54 seconds, returned `finish_reason=cancelled`,
kept the sidecar healthy, and then unloaded it.

## Native runtime proof

The native proof launches one explicitly approved Qwen `.gguf` in a dedicated
`llama-server.exe` child process on a random loopback port. It never uses a
shell, downloads a model, or accepts a non-Qwen model. Automatic discovery
prefers the dependency-light AVX2 build; a GPU build must be selected explicitly
with its matching runtime dependencies.

It supports load, readiness health, streaming, active-stream cancellation,
telemetry, crash detection, bounded shutdown, and unload. LM Studio remains an
independent optional provider.

Commands used for repeatable validation:

```powershell
cd apps/backend
python scripts/run_model_conformance.py --provider lmstudio --model qwen/qwen3.5-9b --output ../../.test-state/model-conformance/lmstudio-qwen3.5-9b.json
python scripts/run_model_conformance.py --provider lmstudio --model google/gemma-4-e2b --output ../../.test-state/model-conformance/lmstudio-gemma-4-e2b-512.json
python scripts/run_model_conformance.py --provider echo-native --llama-server <llama-server.exe> --model <Qwen3.5-9B-Q4_K_M.gguf> --gpu-layers 0 --output ../../.test-state/model-conformance/native-qwen3.5-9b-metadata.json
python scripts/run_native_cancellation_smoke.py --llama-server <llama-server.exe> --model <Qwen3.5-9B-Q4_K_M.gguf> --gpu-layers 0
```

## Deferred paths

- Family-specific hosted/cloud adapters beyond the preserved generic/Gemini
  provider compatibility layer.
- Model downloading, a Models UI, fine-tuning, LoRA, and broad cross-platform
  native packaging.
- GPU-native acceptance on this machine. The discovered CUDA 12 launcher exited
  with Windows code `0xC0000135` because its matching dependency was absent; the
  AVX2 runtime was used and recorded honestly.

The next milestone should make the adapter registry declarative, add explicit
provider/family capability probes, and promote conformance reports into a
versioned operator UI without turning them into authority or automatic model
switching.
