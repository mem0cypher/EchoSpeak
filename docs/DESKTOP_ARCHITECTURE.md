# EchoSpeak Desktop Architecture

Status: desktop foundation implemented; native compilation, sidecar packaging,
installer creation, and packaged-app acceptance are pending the documented
Windows toolchain gate.

This document is canonical for desktop-host behavior. The existing browser UI,
FastAPI runtime, safety policy, Project model, Sessions, Turns, ToolRuns,
approvals, jobs, memory, and runtime data formats remain authoritative in their
existing modules and contracts.

## Component and ownership map

```text
Windows user
  -> Tauri host (apps/desktop/src-tauri)
       owns: native window, single instance, sidecar process tree,
             dynamic loopback port, per-launch API key, data/log roots,
             startup/recovery phase
       -> packaged Python entry (apps/desktop/backend/echospeak_backend.py)
            validates: loopback + auth + parent + data contract
            watches: Tauri parent lifetime
            -> existing FastAPI app (apps/backend)
                 owns: EchoSpeak domain/runtime state and all safety policy
       -> typed bridge (apps/web/src/desktop/bridge.ts)
            owns: authenticated HTTP/WebSocket adaptation and native IPC calls
            -> existing Dashboard with desktop-only ActiveSurface layout
```

There is one owner per desktop concern:

| Concern | Authoritative owner | Projection/consumer |
|---|---|---|
| Window and single-instance lifecycle | Tauri host | title-bar bridge |
| Sidecar PID/process tree | Rust `DesktopState` | runtime status snapshot |
| Desktop startup/recovery phase | Rust supervisor | reducer/UI projection |
| Loopback port and session key | Rust supervisor | Python environment and in-memory bridge |
| Runtime data root | Rust host | `config.DATA_DIR` and backend modules |
| Logs directory | Tauri log plugin/host | **Open logs** command |
| Sessions, Projects, Turns, ToolRuns, approvals, jobs, memory | existing backend owners | desktop reuses existing APIs |
| Active desktop surface | Dashboard `desktopSurface` | Sidebar and workspace render |
| Browser routing/development | existing web application | Tauri reuses its production bundle |

The desktop host does not create a second Project, Session, approval, or job
store. It also does not infer a Session from Sidebar selection. Existing backend
creation endpoints remain the only Session mutation boundary, and the desktop
UI reaches them through the same explicit plus/new-session actions as the web UI.

## Startup and recovery state machine

```text
starting -> ready
   |          |
   |          +-- sidecar exits / health fails --> recovering
   |                                             |       |
   +-- launch fails ------------------------------+       +--> ready
                                                         |
                                                         +--> failed
failed -- manual restart --> recovering
any phase -- application exit --> stopped
```

- The host reserves an ephemeral `127.0.0.1` port and generates a 64-hex-character
  key for each application launch.
- The sidecar is not declared ready until `/health` returns the expected healthy
  response. The current health deadline is 90 seconds.
- Unexpected exit and failed health checks use at most three automatic recovery
  attempts with exponential delays of 0.5, 1, and 2 seconds.
- Once the workspace has become ready, recovery does not unmount it. This keeps
  renderer-local UI state intact while a visible banner reports service state.
- Manual restart invalidates the old generation, kills its process tree, resets
  consecutive failure count, and starts one new generation.
- Normal exit invalidates the generation and terminates the sidecar process tree.
  The Python parent watchdog is the crash-path backstop when the host disappears.
  PyInstaller's worker keeps the shell event pipes open, so the host also probes
  the tracked bootloader process independently of shell termination events. The
  worker watches both the Tauri host and its bootloader, including before the API
  is healthy. A detected bootloader failure atomically invalidates that generation
  before cleanup; the authenticated admin exit route is the healthy-worker
  fallback. Replacement is blocked if the owned endpoint still responds, so an
  orphan worker cannot compete for the port and one crash cannot consume two
  recovery attempts.

No retry phase is stored as application domain truth. `DesktopRuntime` is a
read-only IPC snapshot of host state; the Rust supervisor remains authoritative.

## Sidecar launch protocol

The protocol is intentionally small and host-owned.

Command-line fields:

```text
echospeak-backend --host 127.0.0.1 --port <ephemeral-port> --parent-pid <tauri-pid>
```

Required environment:

| Name | Required value/owner |
|---|---|
| `ECHOSPEAK_RUNTIME_KIND` | `desktop` |
| `ECHOSPEAK_DATA_DIR` | absolute host-resolved application data root |
| `ECHOSPEAK_LOGS_DIR` | absolute host-owned persistent log root |
| `API_HOST` | `127.0.0.1` |
| `API_PORT` | same port as the command line |
| `API_AUTH_ENABLED` | `true` |
| `API_AUTH_LOCALHOST_BYPASS` | `false` |
| `API_AUTH_KEY` | per-launch host-generated key |
| `ADMIN_API_KEY` | same per-launch key, used only by the host for owned worker exit |
| `API_TRUST_PROXY_HEADERS` | `false` |
| `PYTHONUNBUFFERED` | `1` for captured logs |

The Python entrypoint independently validates the contract, changes its working
directory to the data root, starts the parent watchdog, then imports the existing
FastAPI server. Standard output and error are streamed to desktop logs. A
termination event is an input to the Rust recovery state machine, not a durable
job queue or a second callback owner.

## Local transport security

- The desktop server binds only `127.0.0.1`. Both the Rust launcher and Python
  entrypoint set/check the host, and existing backend bind validation remains in
  force.
- Protected HTTP calls receive `X-EchoSpeak-Key` from a fetch wrapper installed
  before Dashboard mounts. The wrapper adds it only when the target origin exactly
  matches the owned runtime API origin.
- Browser WebSocket APIs cannot set arbitrary headers. The bridge sends the key
  in an `echospeak-auth-<key>` subprotocol token and the server uses constant-time
  key comparison. The key is never placed in a URL or persistent web storage.
- The backend disables localhost bypass and trusted proxy headers for desktop.
  Tauri origins are explicit CORS entries.
- The Tauri capability exposes five owned commands only: runtime snapshot,
  backend restart, native folder selection, owned log directory open, and own
  window control. It grants no renderer shell-spawn permission.
- CSP permits loopback HTTP/WebSocket connections but not `0.0.0.0`, disallows
  frames and objects, and freezes prototypes. The bridge key is necessarily in
  renderer memory, so preventing renderer script injection remains a security
  invariant.

`/health` remains a public liveness route by existing backend design. It carries
no mutable or user data. All protected routes still require the session key.

## Data and serialization compatibility

The browser default is unchanged: `config.DATA_DIR` continues to default to
`apps/backend/data`. Desktop launches override `ECHOSPEAK_DATA_DIR` with the
Tauri application-local runtime directory. The packaged `SOUL.md` is seeded
once into that mutable root, and backend logs use the separate host log root
instead of PyInstaller extraction storage. Todos, avatar configuration,
heartbeat state, Twitter state, changelog state, security audit, screenshots,
and tool artifacts now derive their fallback paths from that same configured
root. Existing JSON formats and API payloads are unchanged; only the root used
by an installed desktop process differs.

`ECHOSPEAK_DESKTOP_DATA_DIR` is a host-level override for disposable development
and acceptance runs. `ECHOSPEAK_DESKTOP_LOG_DIR` does the same for Tauri and
captured backend logs. The host creates and canonicalizes both directories before
launching the sidecar. Neither is a renderer setting, and neither weakens Project
path or tool authorization.

Uninstall must not delete runtime data. Future data migrations must be additive,
versioned, recoverable, and implemented in the existing backend owner rather
than in the Tauri renderer.

## Adaptive workspace contract

The browser application retains its BrowserRouter, marketing route, and existing
layout. Under Tauri, a MemoryRouter mounts `DesktopApp`, and the Dashboard uses a
desktop-only explicit surface union:

```text
Chat | Visualizer
```

Exactly one primary surface owns the main workspace. Work, Research, Code,
Checklist, and Media are internal Visualizer panels over the same canonical
Session/TaskRun state. Settings is a centered modal and not a third workspace.
Chat uses the full conversation surface and may show compact durable-work
status. Navigation never creates a Session, TaskRun, Execution, or handoff.
The composer remains the bottom row instead of a permanent third column.
Desktop CSS is scoped beneath `html.echospeak-desktop-root` and
`.desktop-window`, so the browser build does not inherit native-shell behavior.

Native Project and local-Connection folder pickers return a path only after an
explicit user gesture. Attaching a Project or authorizing the selected local
folder remains an existing backend API action, preserving Project, Connection,
scope, revision, and path-validation authority.

## Build and release boundary

PyInstaller produces a target-suffixed backend sidecar. Tauri consumes the shared
web production bundle and packages the host plus sidecar into NSIS and MSI
bundles. The exact prerequisites, commands, expected artifacts, and acceptance
matrix are in [`../apps/desktop/README.md`](../apps/desktop/README.md).

The following are not established by source review or JavaScript/Python tests:

- Rust compilation and generated Tauri capability schemas;
- successful PyInstaller dependency collection;
- sidecar boot from a one-file executable;
- NSIS/MSI creation or signing;
- native window, DPI, focus, WebView2, process-tree, install/uninstall behavior;
- packaged offline/model-provider acceptance.

Those claims require the environment-gated native build and manual packaged
acceptance procedure. Documentation must continue to distinguish implemented
source from validated native behavior.
