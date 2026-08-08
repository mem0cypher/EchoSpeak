# EchoSpeak Desktop

`apps/desktop` is the Windows desktop host for the existing EchoSpeak frontend
and backend. It is additive: browser development still uses `apps/web`, and the
FastAPI application remains in `apps/backend`.

The implemented foundation includes:

- a Tauri 2 Windows shell with a custom title bar, minimum window bounds,
  persisted window state, single-instance focus, and dark first paint;
- a Rust-owned Python sidecar lifecycle with health-gated startup, bounded
  automatic recovery, manual recovery, log capture, and process-tree shutdown;
- a per-launch authenticated loopback transport and a typed renderer bridge;
- a desktop-only Chat/Visualizer workspace with Settings in a centered modal;
- PyInstaller and Tauri packaging scripts for NSIS and MSI outputs; and
- static contract, frontend, and focused backend tests.

Native compilation and packaging remain environment-gated on each release
machine. The current Windows environment completed the sidecar, release host,
NSIS, and MSI builds on 2026-07-13; see **Current validation status** below.

## Architecture and security

See [`../../docs/DESKTOP_ARCHITECTURE.md`](../../docs/DESKTOP_ARCHITECTURE.md).
The short version is that the Tauri host owns the backend process, port,
per-launch key, data directory, log directory, and native window lifecycle. The
renderer cannot spawn a shell. The Python entrypoint independently rejects a
non-loopback host, missing authentication, localhost bypass, missing data root,
or an invalid parent PID.

## Checks available without native toolchains

Run these from the repository root. Backend commands use a fresh disposable
data root and do not touch `apps/backend/data`.

```powershell
npm --prefix apps/desktop ci
npm --prefix apps/desktop test
npm --prefix apps/web run check
npm --prefix apps/web run build

$env:ECHOSPEAK_DATA_DIR = Join-Path $PWD ".test-state\desktop-backend"
$env:ECHOSPEAK_LOGS_DIR = Join-Path $PWD ".test-state\desktop-backend-logs"
$env:PYTHONPATH = Join-Path $PWD "apps\backend"
python -m pytest --basetemp (Join-Path $PWD ".test-state\desktop-pytest-tmp") apps/backend/tests/test_desktop_transport.py apps/backend/tests/test_phase1_integrity.py
```

The full backend regression command is:

```powershell
$env:ECHOSPEAK_DATA_DIR = Join-Path $PWD ".test-state\desktop-full"
$env:ECHOSPEAK_LOGS_DIR = Join-Path $PWD ".test-state\desktop-full-logs"
$env:PYTHONPATH = Join-Path $PWD "apps\backend"
python -m pytest --basetemp (Join-Path $PWD ".test-state\desktop-full-pytest-tmp") apps/backend
```

## Required Windows build environment

Install these only when native build work is authorized:

- Windows 10 or 11 x64 with WebView2 Runtime;
- Visual Studio 2022 Build Tools with **Desktop development with C++** and a
  current Windows 10/11 SDK;
- Rust stable `1.84.0` or newer, including Cargo and the
  `x86_64-pc-windows-msvc` target;
- Node.js `24.16.0` and npm `11.13.0` (the repository also supports its stated
  broader Node range, but these are the versions used for this foundation);
- Python `3.12.10` with the backend requirements; and
- PyInstaller exactly `6.21.0`.

The desktop npm package pins Tauri CLI `2.11.4`. `Cargo.toml` pins Tauri
`2.11.5`, Tauri build `2.6.3`, shell `2.3.5`, single-instance `2.4.2`,
window-state `2.4.1`, and log `2.8.0`. The first successful Cargo operation
must produce a reviewed, tracked `Cargo.lock` before release reproducibility is
claimed.

## Current validation status (2026-07-13)

The installed toolchains produced these local, uncommitted artifacts:

- packaged Python sidecar at
  `src-tauri/binaries/echospeak-backend-x86_64-pc-windows-msvc.exe`;
- release host at `src-tauri/target/release/echospeak-desktop.exe`;
- NSIS current-user installer at
  `src-tauri/target/release/bundle/nsis/EchoSpeak_8.0.0_x64-setup.exe`; and
- MSI at
  `src-tauri/target/release/bundle/msi/EchoSpeak_8.0.0_x64_en-US.msi`.

The release executable was exercised with disposable data. Startup reached a
healthy authenticated loopback sidecar, a bootloader-only crash retired its
still-running PyInstaller worker and recovered to exactly one replacement
process tree on the owned port, the log override was honored, and host crash
cleanup left no owned backend process. Web type checking and 42 tests, five
desktop contract tests, Cargo formatting/checking, and 26 focused backend tests
also passed. The full backend suite is not currently green (see the handoff
report for the recorded result), so this is not a claim of complete product
acceptance.

The installers were created but have not yet been installed/uninstalled in a
clean Windows snapshot. Signing, Start Menu/upgrade behavior, DPI and
multi-monitor coverage, offline/provider coverage, and installer data-retention
acceptance remain release gates in the procedure below.

## Environment-gated build commands

From the repository root:

```powershell
rustup default stable-msvc
rustup target add x86_64-pc-windows-msvc
python -m pip install -r apps/backend/requirements.txt
python -m pip install PyInstaller==6.21.0
npm --prefix apps/web ci
npm --prefix apps/desktop ci
npm --prefix apps/desktop run build
```

`npm --prefix apps/desktop run build` first packages the backend, then runs the
shared web production build through Tauri, then requests both Windows bundles.
The scripts fail closed if Rust is missing, the Rust target cannot be found,
PyInstaller is not exactly `6.21.0`, the sidecar is absent, or a build command
returns non-zero.

Expected artifacts after a successful x64 build:

- `apps/desktop/src-tauri/binaries/echospeak-backend-x86_64-pc-windows-msvc.exe`
- `apps/desktop/src-tauri/target/release/echospeak-desktop.exe`
- an NSIS setup executable below
  `apps/desktop/src-tauri/target/release/bundle/nsis/`
- an MSI below `apps/desktop/src-tauri/target/release/bundle/msi/`

Typical installer names are `EchoSpeak_8.0.0_x64-setup.exe` and
`EchoSpeak_8.0.0_x64_en-US.msi`; treat the actual files emitted by Tauri as
authoritative. Publisher metadata does not sign an installer. Code signing and
release provenance remain separate release gates.

For native development after producing the target-suffixed sidecar:

```powershell
$env:ECHOSPEAK_DESKTOP_DATA_DIR = Join-Path $PWD ".test-state\desktop-dev"
$env:ECHOSPEAK_DESKTOP_LOG_DIR = Join-Path $PWD ".test-state\desktop-dev-logs"
npm --prefix apps/desktop run dev
```

## Packaged acceptance procedure

Use Windows Sandbox, a clean VM snapshot, or a dedicated test account so all
data and installation changes are disposable. Do not use an existing EchoSpeak
runtime directory. If launching from PowerShell, set an absolute disposable
`ECHOSPEAK_DESKTOP_DATA_DIR`; the host resolves it and passes it to every
mutable backend subsystem. Set `ECHOSPEAK_DESKTOP_LOG_DIR` to a second absolute
disposable path so Tauri and captured backend logs are isolated too.

After both bundles exist:

1. Verify the sidecar and installers exist and record hashes with
   `Get-FileHash -Algorithm SHA256`.
2. Install the NSIS bundle as the current user. Confirm the Start Menu entry and
   launch EchoSpeak by double-clicking it.
3. Confirm there is no browser chrome or white flash, the custom window controls
   work, the window cannot resize below 960x640, and size/position survive a
   normal close and reopen.
4. Launch the shortcut again. Confirm the first window focuses and no second
   app or backend process remains.
5. Confirm the boot screen waits for backend health. In Task Manager, verify one
   EchoSpeak host and one sidecar. Confirm the listener is only on `127.0.0.1`
   with `Get-NetTCPConnection -OwningProcess <sidecar-pid>`.
6. Request `/health` without the per-launch key and confirm only that public
   health route is reachable. Request a protected route without a key and
   confirm HTTP 401. Confirm the renderer can hydrate through its bridge.
7. Exercise Chat and Visualizer, including the Work, Research, Code, Checklist,
   and Media panels, then open and close Settings. Confirm panel navigation is
   projection-only and the composer remains usable. Confirm selecting a Project
   or Quick Chat does not create a Session; only an explicit plus/new-session
   action does.
8. Use the native Project folder picker. Confirm canceled selection is inert and
   a selected folder still passes the existing backend Project/path policy.
9. Terminate only the sidecar. Confirm the recovery banner appears, workspace
   state remains mounted, bounded restart succeeds, and no duplicate sidecar
   remains. Repeat until exhaustion and confirm manual restart plus **Open logs**.
10. Close the app during startup and after readiness. Confirm the entire sidecar
    process tree exits. Reopen and confirm durable state comes only from the
    disposable data root.
11. Repeat at 100%, 125%, 150%, and 200% display scale, at minimum size, maximized,
    and on a second monitor. Check keyboard focus, scrolling, long names, and
    reduced-motion behavior.
12. Run the same flow with the network unavailable and with each supported local
    model provider configuration. Failures must remain recoverable and explicit.
13. Uninstall each bundle in a fresh snapshot. Verify application binaries are
    removed. Confirm runtime data is not silently deleted by uninstall.

Record the exact installer, OS build, WebView2 version, toolchain versions,
hashes, observed processes, listener addresses, and failures. Only after this
procedure passes may packaged-app acceptance be claimed.
