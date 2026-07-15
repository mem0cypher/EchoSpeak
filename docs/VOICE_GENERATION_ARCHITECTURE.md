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
| PersonaPlex | Low-latency duplex/interruptible experiment | Separate local service plus audio dependencies | Depends on deployed model/service | Existing client retained but not represented as execution-ready until governed jobs and mic consent are attached |
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
