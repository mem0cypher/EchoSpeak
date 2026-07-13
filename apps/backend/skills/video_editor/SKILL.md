# Video Editor skill

You edit video through EchoSpeak’s **structured video domain**, not free-form prose or shell.

## Authority chain

Project → Session → Turn/Execution → Item → ToolRun

- `VideoEditorStore` owns documents, timeline revisions, jobs, and candidates.
- StateStore owns approvals, executions, and ToolRuns.
- Frontend is a projection of durable backend truth.
- Opening the editor never creates a Session or document.

## Required workflow

1. Call `video_get_editor_context` (or rely on injected structured context). Do **not** invent document revision, clip IDs, or job status from chat.
2. Call `video_list_capabilities` when generative/analysis work is needed. Prefer capability tokens (`text_to_video`, `transcription`) over product names.
3. Match a VideoSkill via `video_list_skills` / `video_plan_request` when the intention fits.
4. For timeline mutation: propose exact `EditOperation`s with the **current** `expected_revision`, then wait for approval. Apply only via the approval path (`video_apply_transaction`).
5. For long work: `video_submit_job` creates a durable job with ToolRun linkage. Blocked/queued is not completion. Never claim generated media until a job completes and verifies an artifact.
6. Research (script, facts, references) uses Echo Research tools; results become plan inputs, not a parallel editor.

## Hard rules

- Never rewrite timeline JSON.
- Never emit FFmpeg filtergraphs, shell pipelines, or package scripts.
- Stale revision → re-plan; do not force apply.
- Failed ToolRuns are not success.
- Playhead/selection are ephemeral — only durable creative prefs go to memory.
- Generated candidates are never auto-inserted onto the timeline.

## Continuity

Unfinished plans and jobs remain resumable across Turns. On “continue” / “yes” / “retry”, re-read context and pending plans; do not invent new Session identity.
