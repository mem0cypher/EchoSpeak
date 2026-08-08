import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const config = JSON.parse(await readFile(new URL("../src-tauri/tauri.conf.json", import.meta.url), "utf8"));
const capability = JSON.parse(await readFile(new URL("../src-tauri/capabilities/desktop-main.json", import.meta.url), "utf8"));
const rust = await readFile(new URL("../src-tauri/src/backend.rs", import.meta.url), "utf8");
const host = await readFile(new URL("../src-tauri/src/lib.rs", import.meta.url), "utf8");
const entry = await readFile(new URL("../backend/echospeak_backend.py", import.meta.url), "utf8");
const cargo = await readFile(new URL("../src-tauri/Cargo.toml", import.meta.url), "utf8");
const desktopApp = await readFile(new URL("../../web/src/desktop/DesktopApp.tsx", import.meta.url), "utf8");
const desktopCss = await readFile(new URL("../../web/src/desktop/desktop.css", import.meta.url), "utf8");
const dashboard = await readFile(new URL("../../web/src/index.tsx", import.meta.url), "utf8");
const sidebar = await readFile(new URL("../../web/src/components/ProjectSidebar.tsx", import.meta.url), "utf8");

test("desktop window is a bounded native shell over the shared frontend", () => {
  assert.equal(config.build.frontendDist, "../../web/dist");
  assert.equal(config.app.windows[0].decorations, false);
  assert.equal(config.app.windows[0].resizable, true);
  assert.ok(config.app.windows[0].minWidth >= 900);
  assert.ok(config.bundle.targets.includes("nsis"));
  assert.ok(config.bundle.targets.includes("msi"));
});

test("renderer capability cannot spawn arbitrary shell commands", () => {
  assert.deepEqual(capability.windows, ["main", "settings", "companion"]);
  assert.ok(!capability.permissions.some((permission) => String(permission).startsWith("shell:")));
  assert.ok(config.app.security.csp.includes("http://127.0.0.1:*"));
  assert.ok(!config.app.security.csp.includes("http://0.0.0.0"));
});

test("custom chrome can drag while controls and composer remain interactive", () => {
  assert.ok(capability.permissions.includes("core:window:allow-start-dragging"));
  assert.ok(desktopApp.includes('className="desktop-titlebar" data-tauri-drag-region'));
  assert.ok(!desktopApp.includes('className="desktop-window-controls" data-tauri-drag-region'));
  const composer = dashboard.match(/<textarea[\s\S]{0,1600}aria-label="Ask Echo anything"/i)?.[0] || "";
  assert.ok(composer, "canonical composer textarea was not found");
  assert.ok(composer.includes("disabled={!activeThreadId}"), "composer must require an explicitly created Session");
  assert.ok(desktopCss.includes("pointer-events: auto"));
  assert.ok(desktopCss.includes("user-select: text"));
});

test("desktop composer submits only into an explicitly selected Session", () => {
  assert.ok(dashboard.includes("Session creation has one explicit owner: the + controls in the sidebar."));
  assert.ok(dashboard.includes('const streamThreadId = String(activeThreadIdRef.current || activeThreadId || "").trim()'));
  assert.ok(dashboard.includes("if (!streamThreadId) return"));
  assert.ok(dashboard.includes('e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing'));
  assert.ok(dashboard.includes("void sendText()"));
  assert.ok(dashboard.includes("disabled={!activeThreadId || !input.trim()}"));
  assert.ok(dashboard.includes("disabled={!activeThreadId}"));
});

test("desktop startup and sidebar use one monochrome Echo identity", () => {
  assert.ok(desktopApp.includes('className="desktop-boot-echo"'));
  assert.ok(desktopApp.includes('className="desktop-boot-progress"'));
  assert.ok(!desktopApp.includes("desktop-boot-mark"));
  assert.ok(!desktopApp.includes("desktop-boot-orbit"));
  assert.ok(desktopCss.includes("@keyframes desktop-echo-rotate"));
  assert.ok(desktopCss.includes("@keyframes desktop-progress"));
  assert.ok(sidebar.includes("{!props.desktop ? <div"));
  assert.ok(sidebar.includes("Desktop identity belongs to the native title bar"));
  assert.ok(sidebar.includes('title="Collapse sidebar"'));
  assert.ok(sidebar.includes('title="Expand sidebar"'));
});

test("desktop opens only after the authenticated product-readiness contract", () => {
  assert.ok(desktopApp.includes("readDesktopReadiness"));
  assert.ok(desktopApp.includes("startup_timeout"));
  assert.ok(desktopApp.includes("boot.hasBeenReady"));
  assert.ok(desktopApp.includes("completed_steps"));
});

test("desktop hydration is retryable and Session navigation preserves request ownership", () => {
  assert.ok(sidebar.includes("Restoring Projects and Sessions"));
  assert.ok(dashboard.includes("for (let attempt = 0; attempt < 4"));
  assert.ok(dashboard.includes('localStorage.getItem("echospeak.active_thread_id")'));
  assert.ok(dashboard.includes("streamControllersRef.current.has(id)"));
  assert.ok(dashboard.includes("Navigation changes only the projection"));
  assert.ok(dashboard.includes("await loadHistory(streamThreadId)"));
  assert.ok(!dashboard.includes("Abort synchronously BEFORE clearing UI"));
});

test("desktop sidecar transport is loopback-only and authenticated per launch", () => {
  for (const invariant of [
    '.env("API_HOST", "127.0.0.1")',
    '.env("API_AUTH_ENABLED", "true")',
    '.env("API_AUTH_LOCALHOST_BYPASS", "false")',
    '.env("ADMIN_API_KEY", &api_session_key)',
    '.env("API_TRUST_PROXY_HEADERS", "false")',
  ]) {
    assert.ok(rust.includes(invariant), `missing Rust invariant: ${invariant}`);
  }
  assert.ok(entry.includes('args.host != "127.0.0.1"'));
  assert.ok(entry.includes('API_AUTH_LOCALHOST_BYPASS'));
  assert.ok(entry.includes('ECHOSPEAK_DATA_DIR'));
  assert.ok(entry.includes('ECHOSPEAK_LOGS_DIR'));
  assert.ok(entry.includes('_seed_mutable_defaults'));
});

test("sidecar lifetime has both graceful and crash recovery ownership", () => {
  assert.ok(rust.includes("MAX_AUTOMATIC_RESTARTS"));
  assert.ok(rust.includes("terminate_process_tree"));
  assert.ok(rust.includes("request_backend_exit"));
  assert.ok(rust.includes("process_is_alive"));
  assert.ok(rust.includes("PROCESS_SYNCHRONIZE"));
  assert.ok(rust.includes("take_failed_generation"));
  assert.ok(entry.includes("desktop-parent-watchdog"));
  assert.ok(entry.includes("desktop-bootloader-watchdog"));
  assert.ok(entry.includes("WaitForSingleObject"));
});

test("native contract is reproducible and supports disposable acceptance data", () => {
  for (const version of [
    'tauri-build = { version = "=2.6.3"',
    'tauri = { version = "=2.11.5"',
    'tauri-plugin-shell = "=2.3.5"',
    'tauri-plugin-single-instance = "=2.4.2"',
    'tauri-plugin-window-state = "=2.4.1"',
  ]) {
    assert.ok(cargo.includes(version), `missing pinned native dependency: ${version}`);
  }
  assert.ok(host.includes('var_os("ECHOSPEAK_DESKTOP_DATA_DIR")'));
  assert.ok(host.includes('var_os("ECHOSPEAK_DESKTOP_LOG_DIR")'));
  assert.ok(host.includes("TargetKind::Folder"));
  assert.ok(rust.includes('.env("ECHOSPEAK_DATA_DIR", &data_dir)'));
  assert.ok(rust.includes('.env("ECHOSPEAK_LOGS_DIR", &log_dir)'));
});
