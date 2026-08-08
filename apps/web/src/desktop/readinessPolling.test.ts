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

const readiness = (core_ready = true): DesktopProductReadiness => ({
  protocol_version: "1",
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

describe("desktop readiness boot state for polling lifecycle", () => {
  it("becomes ready once core_ready is true (pollers may back off)", () => {
    let state = initialDesktopBootState;
    state = reduceDesktopBootState(state, { type: "snapshot", runtime: runtime("ready") });
    state = reduceDesktopBootState(state, { type: "readiness", readiness: readiness(true) });
    expect(state.phase).toEqual("ready");
    expect(state.hasBeenReady).toEqual(true);
  });

  it("stays connecting until core_ready so fast poll continues only while unresolved", () => {
    let state = initialDesktopBootState;
    state = reduceDesktopBootState(state, { type: "snapshot", runtime: runtime("ready") });
    state = reduceDesktopBootState(state, { type: "readiness", readiness: readiness(false) });
    expect(state.phase).toEqual("connecting");
    expect(state.hasBeenReady).toEqual(false);
  });
});
