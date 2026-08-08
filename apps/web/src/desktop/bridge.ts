export type DesktopBackendPhase = "starting" | "ready" | "recovering" | "failed" | "stopped";

export type DesktopRuntime = {
  environment: "desktop";
  api_base: string;
  api_session_key: string;
  backend_phase: DesktopBackendPhase;
  backend_detail: string;
  backend_pid?: number | null;
  connection_generation: number;
  instance_id: string;
  restart_count: number;
  max_automatic_restarts: number;
  data_dir: string;
  log_dir: string;
};

export type DesktopReadinessComponent = {
  key: string;
  label: string;
  ready: boolean;
  critical: boolean;
  detail: string;
  metadata: Record<string, unknown>;
};

export type DesktopProductReadiness = {
  protocol_version: string;
  runtime_schema_version: number;
  core_ready: boolean;
  status: string;
  completed_steps: number;
  total_steps: number;
  data_root: string;
  active_project_id: string;
  active_session_id: string;
  components: DesktopReadinessComponent[];
  checked_at: number;
  runtime_kind: string;
  instance_id: string;
};

export type DesktopBootstrap = {
  connection_generation: number;
  instance_id: string;
  projects: Array<Record<string, unknown>>;
  threads: Array<Record<string, unknown>>;
  active_project_id: string;
  active_session_id: string;
  thread_state: Record<string, unknown> | null;
};

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

declare global {
  interface Window {
    __TAURI__?: {
      core?: { invoke?: Invoke };
    };
    __ECHOSPEAK_DESKTOP_RUNTIME__?: DesktopRuntime;
    __ECHOSPEAK_DESKTOP_BOOTSTRAP__?: DesktopBootstrap;
  }
}

let originalFetch: typeof window.fetch | null = null;

const invoke = async <T>(command: string, args?: Record<string, unknown>): Promise<T> => {
  const handler = window.__TAURI__?.core?.invoke;
  if (!handler) throw new Error("EchoSpeak desktop bridge is unavailable");
  return handler<T>(command, args);
};

export const isDesktopRuntime = (): boolean =>
  typeof window !== "undefined" && typeof window.__TAURI__?.core?.invoke === "function";

export const readDesktopRuntime = (): Promise<DesktopRuntime> => invoke<DesktopRuntime>("desktop_runtime");

export const readDesktopReadiness = async (
  runtime: DesktopRuntime,
  signal?: AbortSignal,
): Promise<DesktopProductReadiness> => {
  const response = await window.fetch(`${runtime.api_base}/startup/readiness`, { cache: "no-store", signal });
  if (!response.ok) throw new Error(`Startup readiness returned HTTP ${response.status}`);
  return response.json() as Promise<DesktopProductReadiness>;
};

export const hydrateDesktopBootstrap = async (
  runtime: DesktopRuntime,
  readiness: DesktopProductReadiness,
  signal?: AbortSignal,
): Promise<DesktopBootstrap> => {
  const [projectsResponse, threadsResponse] = await Promise.all([
    window.fetch(`${runtime.api_base}/projects`, { cache: "no-store", signal }),
    window.fetch(`${runtime.api_base}/threads?limit=200`, { cache: "no-store", signal }),
  ]);
  if (!projectsResponse.ok) throw new Error(`Project hydration returned HTTP ${projectsResponse.status}`);
  if (!threadsResponse.ok) throw new Error(`Session hydration returned HTTP ${threadsResponse.status}`);
  const projectsBody = await projectsResponse.json() as { items?: Array<Record<string, unknown>> };
  const threadsBody = await threadsResponse.json() as Array<Record<string, unknown>>;
  const projects = Array.isArray(projectsBody?.items) ? projectsBody.items : [];
  const threads = Array.isArray(threadsBody) ? threadsBody : [];
  const storedSessionId = window.localStorage.getItem("echospeak.active_thread_id") || "";
  const activeSessionId = threads.some((item) => String(item.thread_id || "") === storedSessionId)
    ? storedSessionId
    : readiness.active_session_id;
  let threadState: Record<string, unknown> | null = null;
  if (activeSessionId) {
    const response = await window.fetch(
      `${runtime.api_base}/threads/${encodeURIComponent(activeSessionId)}/state`,
      { cache: "no-store", signal },
    );
    if (!response.ok) throw new Error(`Active Session hydration returned HTTP ${response.status}`);
    threadState = await response.json() as Record<string, unknown>;
  }
  const bootstrap: DesktopBootstrap = {
    connection_generation: runtime.connection_generation,
    instance_id: runtime.instance_id,
    projects,
    threads,
    active_project_id: String(threadState?.active_project_id || readiness.active_project_id || ""),
    active_session_id: activeSessionId,
    thread_state: threadState,
  };
  window.__ECHOSPEAK_DESKTOP_BOOTSTRAP__ = bootstrap;
  window.dispatchEvent(new CustomEvent("echospeak-desktop-bootstrap", { detail: bootstrap }));
  return bootstrap;
};

export const restartDesktopBackend = (): Promise<DesktopRuntime> =>
  invoke<DesktopRuntime>("restart_desktop_backend");

export const pickDesktopProjectFolder = (): Promise<string | null> =>
  invoke<string | null>("pick_project_folder");

export const pickDesktopConnectionFolder = (providerName: string): Promise<string | null> =>
  invoke<string | null>("pick_connection_folder", { providerName });

export const openDesktopLogs = (): Promise<void> => invoke<void>("open_desktop_logs");

export const openDesktopSettingsWindow = (): Promise<void> => invoke<void>("open_settings_window");

export const openDesktopCompanionWindow = (): Promise<void> => invoke<void>("open_companion_window");

export const setDesktopCompanionAlwaysOnTop = (enabled: boolean): Promise<void> =>
  invoke<void>("set_companion_always_on_top", { enabled });

export const readDesktopWindowLabel = (): Promise<string> => invoke<string>("desktop_window_label");

export const controlDesktopWindow = (action: "minimize" | "toggle_maximize" | "close"): Promise<void> =>
  invoke<void>("control_desktop_window", { action });

const authenticatedRequest = (
  baseFetch: typeof window.fetch,
  runtime: DesktopRuntime,
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> => {
  const requestUrl = input instanceof Request ? input.url : String(input);
  let target: URL;
  try {
    target = new URL(requestUrl, window.location.href);
  } catch {
    return baseFetch(input, init);
  }
  if (target.origin !== new URL(runtime.api_base).origin) return baseFetch(input, init);

  const headers = new Headers(input instanceof Request ? input.headers : undefined);
  new Headers(init?.headers).forEach((value, key) => headers.set(key, value));
  headers.set("X-EchoSpeak-Key", runtime.api_session_key);
  if (input instanceof Request) {
    return baseFetch(new Request(input, { ...init, headers }));
  }
  return baseFetch(input, { ...init, headers });
};

/** Install one transport boundary before Dashboard mounts. Re-install updates credentials after recovery. */
export const installDesktopTransport = (runtime: DesktopRuntime): void => {
  window.__ECHOSPEAK_DESKTOP_RUNTIME__ = runtime;
  if (!originalFetch) originalFetch = window.fetch.bind(window);
  const baseFetch = originalFetch;
  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
    authenticatedRequest(baseFetch, runtime, input, init)) as typeof window.fetch;
};

export const getEchoSpeakApiBase = (): string =>
  (window.__ECHOSPEAK_DESKTOP_RUNTIME__?.api_base || import.meta.env.VITE_API_BASE_URL || "http://localhost:8000")
    .replace(/\/$/, "");

export const createEchoSpeakWebSocket = (url: string): WebSocket => {
  const runtime = window.__ECHOSPEAK_DESKTOP_RUNTIME__;
  if (!runtime) return new WebSocket(url);
  return new WebSocket(url, ["echospeak", `echospeak-auth-${runtime.api_session_key}`]);
};
