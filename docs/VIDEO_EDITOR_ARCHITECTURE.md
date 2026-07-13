# EchoSpeak Video Editor Architecture

**Status:** foundation + **agentic layer** implemented in code + unit tests;
not browser-acceptance closed. Render, analysis, and generation workers remain
**adapter shells** (capability registry + durable job records; ToolRun-linked).
Do not claim playback, WebCodecs preview, FFmpeg export, live transcription,
C2PA, OTIO, or live generation as shipped.

## Agentic layer (this phase)

Echo operates the Video Editor through the same hierarchy as every other
capability: **Project → Session → Turn/Execution → Item → ToolRun**.

| Layer | Owner | Role |
|-------|--------|------|
| Structured editor context | `video_editor/context.py` | Document, revision, tracks/clips, selection, jobs, plans, capabilities, authority |
| Capability report | `video_editor/capabilities.py` | Deterministic features + model capability tokens (not product names) |
| Formal tool catalog | `video_editor/tool_catalog.py` + `tools.py` | Inspect / plan / propose / apply / jobs / memory |
| VideoSkills | `video_editor/skills.py` + `skills/video_editor/` | Intentions, tools, models, templates, approvals, verification |
| Planning | `video_editor/planning.py` | Request → context → skill → ops/jobs → missing requirements (no mutation) |
| Jobs | `video_editor/jobs.py` + store | Durable identity, cancel/retry, ToolRun/Execution linkage, no false completion |
| Creative memory | `video_editor/memory.py` | Style/format/objective/conventions only — never playhead/selection |
| Model adapters | `video_editor/adapters.py` | Capability-based local/cloud contracts; zero generative models required for manual/agent edits |

### Planning path (truthful)

```
user request
  → video_get_editor_context (structured, not chat prose)
  → video_list_capabilities / VideoSkill match
  → video_plan_request (VideoAgentPlan; may be blocked on missing models/permissions)
  → video_propose_operations → ApprovalRecord (revision + operation_hash bound)
  → confirm → video_apply_transaction (parent + per-op child ToolRuns)
  → video_inspect_timeline verify new revision
  → durable jobs via video_submit_job (blocked ≠ completed)
```

Complex work remains resumable: unfinished `VideoEditPlan` / `VideoAgentPlan`
IDs, job records, and creative-memory unfinished plan IDs survive across Turns.
Stale `expected_revision` invalidates proposals. Opening `/app/video` still
creates **no** Session or document.

### API surface (agentic)

- `POST /video/context` — structured `VideoEditorContext`
- `GET /video/capabilities` — capability report
- `GET /video/tools` — formal tool catalog
- `GET /video/skills` — registered VideoSkills
- `POST /video/plan` — plan + ToolRun (no mutation)
- `POST /video/creative-memory` — durable prefs only
- Job submit/get/cancel/retry with Execution + ToolRun projection

## Normal chat Turn integration

Video is not a private chat endpoint. On each production Turn,
`EchoSpeakAgent._bind_video_turn_to_decision`:

1. Detects video intent or open VideoDocument (utility small-talk stays clean).
2. Loads `VideoEditorContext` + skill selection from the **canonical**
   `SkillsRegistry` (package skills + bridged video domain skills).
3. Filters `video_*` tools **out** of non-video Turns; includes them only when
   the Turn is video-relevant.
4. Injects structured context into the system prompt (not chat prose inference).
5. Records `SkillExecutionRecord` when a skill is selected (status planned).

Skill selection outcomes: `selected`, `direct_tool_better`, blocked_*,
`ambiguous`, `no_matching_skill`. Stale prior skills are not reused without
continue/retry language.

`skill_create` writes experimental **disabled** packages + a `SkillProposal`;
execution requires a later `skill_enable` after review.

## Worktree change count (reconciled 2026-07-12)

Codex reports that said “~20 modified files” were **under-counts**. Exact
measurement on branch `feature/v7.6.10-runtime-lifecycle-honesty` after the
video + coding reliability pass:

| Metric | Count | How measured |
|--------|------:|--------------|
| Tracked modified files | **30** | `git status --porcelain` lines matching ` M` / `M ` |
| Untracked paths (expanded) | **24** | `git ls-files --others --exclude-standard` |
| **Total change set** | **54** | tracked modified + expanded untracked |
| Porcelain top-level lines | **40** | dirs collapse untracked children (e.g. `video_editor/`) |

So “20” was wrong: it did not match either tracked-only (30) or full set (54).
Likely causes: counting only a subset of previously dirty tracked files, or
not expanding untracked directories.

### What the foundation includes (code)

- Route `/app/video` + left-rail Video view (`VideoEditorView`)
- Versioned `VideoProjectDocument` models + rational-time (`Rational` / `RationalTime`)
- Project-bound media ingest + probe (`media.py`)
- Timeline / tracks / clips / revisions / typed operations
- Manual apply + agent propose → ApprovalRecord → confirm path
- Parent/child ToolRuns on apply (`video_apply_transaction` + per-op children)
- Undo/redo creating new revisions from immutable snapshots
- Adapter **declarations** + job shells (not live generators)
- Backend API `apps/backend/api/video_editor.py`
- Fail-closed corrupt-state quarantine under `data/video_editor/`

### Explicitly deferred (slots only)

- Real timeline playback / scrub preview
- Complete WebCodecs preview path
- Proxy generation, waveforms, thumbnails
- Deterministic FFmpeg render/export
- Analysis workers, transcription, captions
- Active local/cloud generation adapters (beyond registry)
- Generated-candidate → timeline import product flow
- OTIO interchange, C2PA provenance
- Object tracking, removal, segmentation, AI effects

### UI shell (production route)

**Route:** `/app/video` → `apps/web/src/index.tsx` sets `leftTab === "video_editor"`
and renders **only** `features/video-editor/VideoEditorView.tsx` (full-bleed over
the visualizer column; global EchoSpeak sidebar remains).

**Layout:** Always the desktop editor chrome (toolbar · media bin · program
viewer · right dock · timeline). Opening the page does **not** create a Session,
document, or timeline. Empty states live inside the panels. Import is file
picker / multi-select / drag-drop (multipart upload into Project `media/`).
Generate and Export are disabled until those pipelines ship.

### Next product step (do this before more features)

Browser acceptance only — see checklist in `docs/RUNTIME_CONTRACTS.md` §K-video
and the “Best next move” note in the milestone status. **No new video features
until that pass green.**

## Product boundary

The Video Editor combines three workflows without merging their authority:

1. Manual editing emits typed operations from explicit UI gestures.
2. Agentic editing emits the same typed operations as a proposal, persists an
   exact ApprovalRecord, and cannot mutate the timeline before confirmation.
3. Generative media runs as a durable job and produces immutable candidate
   assets. Generation completion alone never inserts a candidate into a
   timeline.

The hierarchy remains Project → Session → Turn/Execution → Item → ToolRun.
ProjectManager owns the Project identity and canonical root. ThreadSessionState
owns attachment only. StateStore owns approvals, executions, Items, and
ToolRuns. `VideoEditorStore` alone owns video documents, assets/provenance,
timeline heads, transactions, immutable revision snapshots, undo/redo stacks,
jobs, and candidates. The frontend is a projection and replaces its document
after every backend response.

## Authoritative schemas

All first-foundation records use `schema_version: 1`:

- `VideoProjectDocument`
- `MediaAsset`, `GeneratedAsset`, `MediaStream`, `MediaProvenance`
- `Timeline`, `Track`, `Clip`
- `Rational`, `RationalTime`
- `EditOperation`
- `VideoEditPlan`, `VideoEditTransaction`
- `VideoRevision`
- `VideoJob`, `GeneratedCandidate`
- `EditorSelectionContext`

`RationalTime` stores decimal-string integer ticks plus an explicit rational
time base. Browser clients do not convert authoritative time to floating point;
float seconds are display/gesture inputs only. Operation validation uses exact
fractions. Imported sources are immutable identities (Project-relative path,
size, mtime, SHA-256); timeline clips reference assets and source ranges.

The operation engine accepts only:

- `add_track`
- `insert_clip`
- `split_clip`
- `trim_clip`
- `move_clip`
- `delete_clip`

It stages every operation on a copy, validates revision, ownership, IDs, locked
tracks, nonnegative/exact time, source duration, and same-track overlap, then
atomically promotes one new document head. Models never rewrite timeline JSON.

## Persistence and recovery

Video state lives beneath `apps/backend/data/video_editor/projects/<project-id>`
and is keyed by Project/document identities. Each commit writes an immutable
revision snapshot before atomically replacing the document head. Transaction
IDs and operation hashes make confirmation/retry idempotent. Undo and redo
restore the timeline portion of immutable snapshots into new revisions while
preserving current media assets, jobs, candidates, and document metadata;
coding checkpoints are not reused. External storage keys accept only bounded
identifier characters and are containment-checked before use as paths.

Malformed JSON, schema-invalid documents, and revision snapshots with a wrong
identity or SHA-256 fail closed. The original is not overwritten. EchoSpeak
copies it byte-for-byte beneath `data/video_editor/corrupt-state`, writes a
machine-readable diagnostic and `RECOVERY.txt`, and requires manual
repair/restore while EchoSpeak is stopped.

## Approval and runtime projection

Manual UI operations are explicit user gestures and commit through the same
transaction engine. Agent proposals persist a `VideoEditPlan`, prepared
`VideoEditTransaction`, preview, and generic ApprovalRecord with:

- Session and Project IDs;
- document and transaction IDs;
- expected document revision;
- canonical arguments hash;
- operation hash;
- `video_apply_transaction` tool identity.

Consumption re-reads Session attachment and ProjectManager, checks that the
saved root still matches ProjectManager, checks current Session permissions,
allowed tool inventory, constraints, registration, policy flags, `Enable
System Actions`, and **Allow Video Agent Edits**, then matches arguments,
operation hashes, and source preconditions against the current document head.
StateStore atomically claims the exact pending ApprovalRecord before mutation,
so concurrent confirmations cannot both cross the boundary. One parent
`video_apply_transaction` ToolRun owns the transaction and each contained
EditOperation receives one durable child ToolRun tagged with its operation ID;
all terminalize only after atomic commit succeeds.

## Media ingest

Ingest accepts Project-relative local files only. It rejects URLs, paths outside
the Project, and symlink/junction/reparse chains. `ffprobe` runs as a bounded
argument array with `shell=False`, stdin disabled, timeout, and output caps.
Malformed/failed probes create no usable MediaAsset.

The source path, reparse chain, size, mtime, and SHA-256 are revalidated after
the slow probe/hash boundary and again before preview content is served. A
changed source must be re-imported; stale immutable identity is never served.

Stored evidence includes every stream, codec, time base, duration ticks,
average and nominal frame rates, sample rate/channels, dimensions/pixel format,
color metadata, rotation/disposition, format, chapters, file identity, and
provenance. VFR media is never mapped with `seconds × average FPS`.

FFmpeg documents ffprobe JSON/stream selection and machine-readable probing:
https://ffmpeg.org/ffprobe.html. Protocol inputs remain restricted because the
libavformat protocol layer is broader than local files:
https://www.ffmpeg.org/doxygen/trunk/group__libavf.html.

## Preview and render

Foundation preview serves the authorized selected asset to native media/image
elements. It is not final render truth. The next preview phase should use
Project-bound proxies, native decode, Canvas composition, Web Audio timing, and
`requestVideoFrameCallback`; WebCodecs and WebGPU remain capability-detected
optional backends because implementations do not guarantee every codec/GPU:

- https://www.w3.org/TR/webcodecs/
- https://wicg.github.io/video-rvfc/
- https://www.w3.org/TR/webaudio-1.1/
- https://www.w3.org/TR/webgpu/all/

Final render should compile an allowlisted render graph into FFmpeg argument
arrays. Models must never emit executable FFmpeg/filtergraph strings. Use
`-nostdin`, machine-readable `-progress pipe:1`, temporary output, verification,
and atomic promotion: https://www.ffmpeg.org/ffmpeg.html. Pin and audit the
redistributed binary; FFmpeg licensing changes when GPL/optional components are
enabled: https://ffmpeg.org/legal.html.

OpenTimelineIO is a future import/export adapter, not internal authority. It is
an interchange format and keeps media external:
https://github.com/AcademySoftwareFoundation/OpenTimelineIO.

## Jobs, adapters, and candidates

`VideoAdapterRegistry` exposes capabilities without installation side effects.
The foundation declares local/cloud candidates and creates durable job shells
only; declared-but-unavailable adapters create an explicit `blocked` job, not a
false queued/running claim. Idempotency keys are bound to canonical inputs and
return the persisted job on an exact retry. The adapter interface is `capabilities`, `estimate`, `submit`,
`poll`, `cancel`, `fetch_candidates`, and `verify_output`.

Initial candidates based on current primary sources:

- Wan 2.2 local experimental (Apache-2.0, substantial GPU requirements):
  https://github.com/Wan-Video/Wan2.2
- LTX cloud: https://docs.ltx.video/
- Runway cloud: https://docs.dev.runwayml.com/api/
- Google Veo future enterprise adapter:
  https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/veo-video-generation

Sora 2 is not a new dependency because its official Videos API is deprecated
for shutdown on September 24, 2026:
https://developers.openai.com/api/docs/guides/video-generation.

Candidate selection must create/import a GeneratedAsset and then use a normal
timeline transaction. Cloud upload, cost, retention, and deletion require
separate approval. Provenance stores hashes, parent assets/ranges, exact adapter
and model version, prompt/settings hash, seed, Session/Execution/ToolRun/job,
license evidence, retention, and disclosure. C2PA is a future signing/export
adapter, not internal truth: https://spec.c2pa.org/about/.

## Safety and resource boundaries

- No real Project or source asset is modified by opening the editor.
- Navigation never creates a Session or document.
- Imports are explicit and metadata-only in this foundation.
- Source files remain immutable; edits reference ranges.
- Package scripts, raw terminal commands, raw FFmpeg strings, and arbitrary
  generated Python skills are outside the video operation engine.
- Adapter availability is not inferred from a declared model name.
- Interrupted jobs recover as interrupted/blocked/retryable, never complete.
- Project deletion makes video state inaccessible but does not silently delete
  managed evidence/blobs.

## Deferred phases

Proxy generation, waveform/thumbnail caches, Canvas/WebCodecs compositor,
allowlisted render compiler, distributed workers, analysis artifacts (speech,
silence, masks, tracking), cloud/local generation adapters, candidate import,
portable interchange, C2PA signing, and declarative VideoSkill authoring remain
explicit future work. None is represented as complete by the current job shell.
