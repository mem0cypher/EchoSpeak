# Settings Required for EchoSpeak Coding

EchoSpeak coding is ready only when the active Session has a real Project, a
ready model path, and the required tools in the backend executable inventory.
The **Tools** screen shows the authoritative backend result from
`GET /coding/readiness?thread_id=<session>`; the browser does not calculate a
second answer.

## File reading

1. In the sidebar, select an existing Session.
2. Attach a folder as a **Project** to that Session.
3. Open **Settings → Local Tools → Inventory** and refresh.

There is no separate “Allow File Read” toggle. Safe reads require all of:

- an attached Project ID;
- a ProjectManager record whose root exists;
- the Session root projection matching that record;
- `file_list` and `file_read` registered and present in the executable inventory;
- an available native or structured-fallback model tool path.

Chat mode does not remove an attached Project. Mode guides interaction; the
Project owns path scope.

## File writing

In **Settings → Privacy & Permissions → System Actions (Safety Gates)** enable:

- **Enable System Actions** (`enable_system_actions`, default `false`);
- **Allow File Write** (`allow_file_write`, default `false`).

Writes remain exact-confirmation actions in the Web UI. Echo prepares a bounded
diff and durable ApprovalRecord, then pauses. Approve in the same Session or
reply `confirm`; `cancel` performs no mutation. At confirmation Echo revalidates
the current Project, path, permissions, configuration, executable inventory,
arguments, and source/destination versions. A successful write is followed by a
real `file_read`; “saved” and “verified” are separate facts.

Settings saved through the UI are applied immediately and agents are rebuilt.
Editing `.env` directly requires an API restart.

## Terminal (optional)

Ordinary file reading and writing do not require a terminal.

To run builds or tests, enable **Allow Terminal Commands** and choose **Terminal
Execution Mode**:

- **Docker sandbox (recommended)** maps to `terminal_execution_mode=docker` and
  requires Docker Engine/Desktop to be ready.
- **Host terminal (unsandboxed opt-in)** maps to
  `terminal_execution_mode=host` and runs commands directly on the host.

Echo never silently falls back from Docker to host. The Terminal Denylist,
timeout, and output limit remain active in either mode. The safe `project_status`
tool reports metadata and git state only; tests and package scripts go through
the approval-gated terminal boundary. Code Preview automatically serves static
HTML only and does not execute package scripts on the host.

## Model and provider

Choose the provider/model in the top model selector or provider Settings.

- Hosted OpenAI/Gemini require their configured API key.
- Local LM Studio/Ollama/LocalAI/vLLM require a reachable server and the exact
  configured model in the provider model inventory.
- **Ollama Tool-Calling Wrapper** applies only to the Ollama compatibility
  wrapper.
- **Disable Native Tools (Use Fallback)** is the real global override. When off,
  every configured provider may attempt native tools. When on, Echo uses its
  structured fallback where available.

Small local models have the same functional authority as hosted models. They
can reliably execute bounded exact-file workflows when they return valid
structured edits; they are more likely to produce malformed edits, incomplete
plans, or invalid tool arguments. A stronger hosted model improves planning and
edit quality, but cannot repair a missing Project, disabled write permission,
filtered tool, stale approval, source-version conflict, or failed filesystem
mutation.

## Other relevant labels

- **File Tool Root** is the global default/ceiling for unbound legacy tools. It
  is not Project selection and does not override a Session-attached Project.
- **Allow Playwright**, **Allow Desktop Automation**, and **Allow Open
  Application** are unrelated to normal Project editing.
- **Allow Video Agent Edits** authorizes exact approved timeline transactions;
  it does not authorize source-code writes.

## Reading a diagnostic

`ready_for_reading` and `ready_for_editing` are independent. Tool states are:

- `available`: registered, loaded, scoped, and currently configured;
- `disabled`: present but blocked by Project/permission/configuration;
- `unregistered`: no backend registration exists;
- `filtered`: registered but absent from the Session executable inventory.

Common messages are deliberately specific:

- “No Project is attached to this Session.” → attach a Project.
- “File writing is disabled…” → enable the two write gates above.
- “registered but not present…” → rebuild/refresh the executable inventory;
  changing interaction mode is not the fix.
- “configured model is not loaded” → load the selected model or select the
  loaded provider model.
- “edit is waiting for confirmation” → decide it in the same Session.
- “Project changed” or “file changed” → inspect current state and prepare a new
  diff; stale authority is never reused.
- “Docker terminal unavailable” → start Docker only if terminal verification is
  needed; file editing can continue without it.
