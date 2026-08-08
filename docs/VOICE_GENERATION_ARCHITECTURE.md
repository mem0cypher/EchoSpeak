# Voice and generation provider foundation

Status: implemented foundation, researched 2026-07-13. Python remains the
authoritative owner of VoiceJob, GenerationJob, provider readiness, action
permission, and canonical MediaAsset registration.

## Decision

EchoSpeak uses provider-neutral requests and opt-in ToolRegistry actions. A
credential or a reachable local server is capability evidence, never action
permission. The read-only `/media-runtime/*` projection cannot submit jobs.
Cloud upload and paid-provider execution remain blocked until a dedicated
ApprovalRecord includes current provider, model, cost/quota, input-asset hash,
Project, Session, and upload scope. There is no silent local-to-cloud fallback.

The first real path is Windows SAPI text-to-speech. It writes a WAV beneath the
active Project, verifies a non-empty file, hashes it, and registers the same
immutable asset in Media. This path is offline, uses an installed Windows voice,
does not open the microphone, and does not upload text or audio. SAPI synthesis
is not interruptible mid-call, so the capability truthfully reports no running
job cancellation or streaming.

## Voice research

| Candidate | Fit | Windows / hardware | License and privacy | EchoSpeak status |
|---|---|---|---|---|
| Windows SAPI | Very small, offline TTS baseline; quality depends on installed voice | Built into Windows; CPU | OS component; text remains local | Real governed WAV path implemented |
| faster-whisper | Strong batch/near-streaming STT through CTranslate2 | CPU INT8 or CUDA; model is selected separately and may download | MIT implementation; offline after model installation | Detected only; no model is installed or downloaded |
| whisper.cpp | Good distributable offline STT candidate, VAD, quantization | Windows MSVC/MinGW; CPU, CUDA, Vulkan, ROCm and OpenVINO options | MIT; offline | CLI detected only; explicit model path required |
| Piper | Fast local neural TTS with many downloadable voices | Current PyPI publishes Windows x86-64 wheels | Current maintained package is GPL-3.0-or-later; voice model licenses must also be checked | Detected only; explicit binary and voice model required |
| PersonaPlex | Full-duplex conversational-model experiment | Separate Linux/GPU-oriented service plus audio dependencies | MIT code; NVIDIA Open Model License weights | Compatibility source retained but hard-disabled because it would compete with the selected Session model and canonical runtime |
| OpenAI Audio / Realtime | Hosted STT, TTS, streaming and realtime paths | Network; provider-side compute | User audio/text leaves device; usage and credentials required | Capability/config only; upload/cost approval not implemented |

Primary references: [faster-whisper](https://github.com/SYSTRAN/faster-whisper),
[whisper.cpp](https://github.com/ggml-org/whisper.cpp),
[Piper](https://github.com/OHF-voice/piper1-gpl),
[OpenAI Realtime API](https://platform.openai.com/docs/api-reference/realtime), and
[OpenAI Audio API](https://platform.openai.com/docs/api-reference/audio).

Desktop microphone access is a separate native/browser permission boundary.
Provider capability checks never request microphone access. A future capture
surface must first obtain visible user consent, bind captured bytes to one
Session, and then request a governed Voice action; it must not stream on mount.

## Image and video generation research

| Candidate | Fit | Local hardware / hosted constraints | EchoSpeak status |
|---|---|---|---|
| ComfyUI local API | Best initial local adapter because workflows are explicit and jobs expose progress/history | Windows desktop currently targets NVIDIA; models are large and remain user-managed | Loopback health and explicit workflow-path detection implemented; compiler/output verifier intentionally gated |
| OpenAI Images and Video | Hosted image jobs and Sora video jobs with provider task identity | Paid/network path; reference inputs can leave device | Credential/config capability only; approval and adapter gated |
| Vertex AI Imagen / Veo | Hosted image edit/generation and asynchronous Veo video operations | Google Cloud project, auth, region, quota/billing and Cloud Storage may be required | Project/config capability only; approval and adapter gated |
| Runway API | Hosted image/video tasks with polling and cancellation | Paid credits; upload and duration/model rules vary | Secret/config capability only; cost/upload approval and adapter gated |

Primary references: [ComfyUI local API and installation](https://docs.comfy.org/),
[OpenAI video jobs](https://platform.openai.com/docs/api-reference/videos),
[Vertex AI image generation](https://cloud.google.com/vertex-ai/generative-ai/docs/image/overview),
[Vertex AI Veo](https://cloud.google.com/vertex-ai/generative-ai/docs/video/generate-videos-from-text),
[Runway API](https://docs.dev.runwayml.com/), and
[Runway pricing](https://docs.dev.runwayml.com/guides/pricing/).

## Durable contracts and gates

- `VoiceJob` and `GenerationJob` use stable idempotency identity, atomic writes,
  explicit terminal blocked states, corrupt-record quarantine, diagnostics, and
  manual recovery instructions.
- `voice_synthesize_speech` requires `ENABLE_SYSTEM_ACTIONS`,
  `ALLOW_VOICE_ACTIONS`, current Session permission, current Project binding,
  and current ToolRegistry inventory.
- `generation_submit` requires `ENABLE_SYSTEM_ACTIONS`,
  `ALLOW_GENERATION_ACTIONS`, the same fresh authority checks, and never guesses
  a provider or silently changes locality.
- Generated output registration verifies Project containment and bytes before
  creating an immutable MediaAsset. Chat and Media must project that asset id;
  a Chat message is never the asset authority.
- Provider-specific request payloads belong inside future adapters. The durable
  request exposes normalized width, height, duration, quality, seed, negative
  prompt, model and input asset ids only.

## Exact environment gates

Local STT: install either faster-whisper plus an explicitly reviewed model path,
or a whisper.cpp Windows binary plus an explicitly reviewed GGML model. No model
is auto-downloaded. Local Piper TTS requires the Piper executable and a reviewed
voice model/license. Local generation requires a loopback ComfyUI server and an
explicit API-format workflow; EchoSpeak still needs the governed workflow
compiler and output verifier before `execution_ready` can become true.

Cloud adapters additionally require user-selected provider configuration,
secret storage, current quota/cost lookup, an ApprovalRecord with upload scope,
provider task polling/cancellation, verified output download, and immutable
Media registration. Until each condition exists, readiness remains false even
when credentials are detected.

## Phase 5 realtime transport decision (source evaluation, 2026-08-01)

No realtime framework is enabled in production. The current `VoiceTransportTurn`,
canonical query path, and semantic activity projection already provide the
correct ownership boundary; a future adapter should transport audio and
transcript events into those owners rather than importing another agent loop.

| Candidate | Current primary-source behavior | EchoSpeak decision |
|---|---|---|
| LiveKit Agents | `AgentSession` orchestrates user input, STT/realtime models, LLM, tools, output, turn detection, and interruption | Do not embed `AgentSession` as Echo's runtime. A future adapter may reuse LiveKit Room/WebRTC transport concepts only if TaskRun and the selected Session model remain authoritative. |
| Pipecat | Composable realtime pipelines include transports, model services, conversation state, interruption frames, flows, and multi-agent workers | Do not install the full pipeline for the current desktop path. Its transport/frame concepts are useful, but its orchestration and conversation ownership would duplicate EchoSpeak. |
| PersonaPlex | A full-duplex conversational speech model based on Moshi, with its own role prompt, generated dialogue, voice conditioning, and server | Reject as an ordinary Echo transport: it would substitute a second conversational model for the exact Session model. The legacy wrapper is hard-disabled and no longer performs keyword tool routing. |
| Provider-native speech-to-speech | Usually combines turn detection, conversation state, model inference, and generated audio | Keep unavailable until an adapter can prove it is only a renderer/transport, or until the product explicitly changes the one-selected-model invariant. No such exception exists today. |

Primary references: [LiveKit AgentSession](https://docs.livekit.io/agents/logic/sessions/),
[LiveKit turn handling](https://docs.livekit.io/agents/logic/turns/),
[Pipecat](https://github.com/pipecat-ai/pipecat), and
[NVIDIA PersonaPlex](https://github.com/NVIDIA/personaplex).

The next technically compatible improvement is therefore streaming **cascaded**
voice, not native speech-to-speech:

```text
one user gesture and microphone lease
  -> local streaming STT adapter
  -> ephemeral partial transcript events
  -> one durable final transcript
  -> canonical Echo Session and TaskRun
  -> existing semantic activity events
  -> interruptible local streaming TTS
```

The adapter must preserve exact Session/client-turn identity across reconnect,
discard partial transcripts unless finalized, stop local playback immediately,
route TaskRun cancellation through the existing exact request endpoint, and
never own tools, evidence, requirements, memory, or response finalization.
Enabling it remains gated on manual acceptance for interruption, transcript
synchronization, cancellation, background work, reconnect, privacy, local
fallback, and voice identity consistency.

## Phase 6 presence and wake-word gate (source review, 2026-08-01)

The desktop companion is an additional projection over the existing backend and
the explicitly selected Chat Session. It does not call Session creation APIs,
does not own a TaskRun, and submits companion text through the same canonical
`/query/stream` boundary. The main window publishes the selected Session and
Chat reasoning controls through shared desktop storage; the companion observes
those values and never chooses another model or creates fallback work. Tauri's
window-state owner remains responsible for restoring the companion position.

Only the main Chat surface currently instantiates `LocalVoiceInput`, and capture
still requires an explicit microphone-button gesture. Settings, provider probes,
startup, and the desktop companion do not open the microphone. This means there
is no competing microphone consumer in current production source, but there is
also no durable cross-window microphone lease yet.

Wake-word activation therefore remains disabled. It may be enabled only after:

1. ordinary cascaded voice passes the manual Phase 4 acceptance scenarios;
2. one desktop microphone lease arbitrates Chat, future wake listening, and any
   future companion voice control;
3. the local wake engine releases that lease before command capture;
4. active Session routing is explicit and a missing Session never creates one;
5. wake listening has a visible privacy state and an immediate stop control.

No background listener, model download, cloud upload, or second voice agent is
introduced by the current presence implementation.
