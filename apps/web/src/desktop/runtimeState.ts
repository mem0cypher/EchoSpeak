import type { DesktopProductReadiness, DesktopRuntime } from "./bridge";

export const DESKTOP_PROTOCOL_VERSION = "1";

export type DesktopBootPhase = "connecting" | "ready" | "recovering" | "failed";

export type DesktopBootState = {
  phase: DesktopBootPhase;
  runtime: DesktopRuntime | null;
  readiness: DesktopProductReadiness | null;
  detail: string;
  hasBeenReady: boolean;
};

export type DesktopBootEvent =
  | { type: "snapshot"; runtime: DesktopRuntime }
  | { type: "readiness"; readiness: DesktopProductReadiness }
  | { type: "readiness_error"; message: string }
  | { type: "startup_timeout" }
  | { type: "bridge_error"; message: string }
  | { type: "retry_requested" };

export const initialDesktopBootState: DesktopBootState = {
  phase: "connecting",
  runtime: null,
  readiness: null,
  detail: "Starting the local EchoSpeak service",
  hasBeenReady: false,
};

export function reduceDesktopBootState(state: DesktopBootState, event: DesktopBootEvent): DesktopBootState {
  if (event.type === "retry_requested") {
    return { ...state, phase: "recovering", readiness: null, detail: "Restarting the local EchoSpeak service" };
  }
  if (event.type === "bridge_error") {
    return { ...state, phase: "failed", detail: event.message || "Desktop bridge unavailable" };
  }
  if (event.type === "startup_timeout") {
    return { ...state, phase: "failed", detail: "EchoSpeak could not restore its core workspace within 90 seconds" };
  }
  if (event.type === "readiness_error") {
    return { ...state, phase: "connecting", detail: event.message || "Checking workspace readiness" };
  }
  if (event.type === "readiness") {
    if (event.readiness.protocol_version !== DESKTOP_PROTOCOL_VERSION) {
      return {
        ...state,
        phase: "failed",
        readiness: event.readiness,
        detail: `Desktop protocol ${event.readiness.protocol_version} is incompatible with ${DESKTOP_PROTOCOL_VERSION}`,
      };
    }
    if (state.runtime?.instance_id && event.readiness.instance_id !== state.runtime.instance_id) {
      return { ...state, phase: "connecting", readiness: null, detail: "Waiting for the current local runtime" };
    }
    const phase: DesktopBootPhase = event.readiness.core_ready ? "ready" : "connecting";
    return {
      ...state,
      phase,
      readiness: event.readiness,
      detail: event.readiness.status,
      hasBeenReady: state.hasBeenReady || phase === "ready",
    };
  }
  const phase: DesktopBootPhase =
    event.runtime.backend_phase === "ready"
      ? state.readiness?.core_ready
        ? "ready"
        : "connecting"
      : event.runtime.backend_phase === "failed" || event.runtime.backend_phase === "stopped"
        ? "failed"
        : event.runtime.backend_phase === "recovering"
          ? "recovering"
          : "connecting";
  return {
    phase,
    runtime: event.runtime,
    readiness: event.runtime.backend_phase === "ready" ? state.readiness : null,
    detail:
      event.runtime.backend_phase === "ready" && !state.readiness?.core_ready
        ? "Preparing workspace"
        : event.runtime.backend_detail,
    hasBeenReady: state.hasBeenReady || phase === "ready",
  };
}
