import { describe, expect, it } from "vitest";
import type { DesktopProductReadiness, DesktopRuntime } from "./bridge";
import { initialDesktopBootState, reduceDesktopBootState } from "./runtimeState";

const runtime = (backend_phase: DesktopRuntime["backend_phase"]): DesktopRuntime => ({
  environment: "desktop",
  api_base: "http://127.0.0.1:41234",
  api_session_key: "secret",
  backend_phase,
  backend_detail: backend_phase,
  backend_pid: 42,
  connection_generation: 1,
  instance_id: "instance-1",
  restart_count: 0,
  max_automatic_restarts: 3,
  data_dir: "C:\\Temp\\EchoSpeak",
  log_dir: "C:\\Temp\\EchoSpeak\\logs",
});

const readiness = (core_ready = true, protocol_version = "1"): DesktopProductReadiness => ({
  protocol_version,
  runtime_schema_version: 2,
  core_ready,
  status: core_ready ? "Ready" : "Restoring Sessions",
  completed_steps: core_ready ? 16 : 5,
  total_steps: 16,
  data_root: "C:\\Temp\\EchoSpeak",
  active_project_id: "",
  active_session_id: "",
  components: [],
  checked_at: 1,
  runtime_kind: "desktop",
  instance_id: "instance-1",
});

describe("desktop boot state", () => {
  it("does not mount the workspace before the owned backend is ready", () => {
    const next = reduceDesktopBootState(initialDesktopBootState, { type: "snapshot", runtime: runtime("starting") });
    expect(next.phase).toEqual("connecting");
    expect(next.hasBeenReady).toEqual(false);
  });

  it("keeps the mounted workspace alive through later recovery", () => {
    const backend = reduceDesktopBootState(initialDesktopBootState, { type: "snapshot", runtime: runtime("ready") });
    const ready = reduceDesktopBootState(backend, { type: "readiness", readiness: readiness() });
    const recovering = reduceDesktopBootState(ready, { type: "snapshot", runtime: runtime("recovering") });
    expect(recovering.phase).toEqual("recovering");
    expect(recovering.hasBeenReady).toEqual(true);
  });

  it("does not reveal the workspace when HTTP is healthy but durable owners are not ready", () => {
    const backend = reduceDesktopBootState(initialDesktopBootState, { type: "snapshot", runtime: runtime("ready") });
    const loading = reduceDesktopBootState(backend, { type: "readiness", readiness: readiness(false) });
    expect(loading.phase).toEqual("connecting");
    expect(loading.hasBeenReady).toEqual(false);
    expect(loading.detail).toEqual("Restoring Sessions");
  });

  it("fails closed on an incompatible desktop protocol", () => {
    const failed = reduceDesktopBootState(initialDesktopBootState, {
      type: "readiness",
      readiness: readiness(false, "99"),
    });
    expect(failed.phase).toEqual("failed");
    expect(failed.detail.includes("incompatible")).toEqual(true);
  });

  it("surfaces exhausted recovery without inventing readiness", () => {
    const failed = reduceDesktopBootState(initialDesktopBootState, { type: "snapshot", runtime: runtime("failed") });
    expect(failed.phase).toEqual("failed");
    expect(failed.hasBeenReady).toEqual(false);
  });
});
