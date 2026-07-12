/**
 * Shared agent activity state machine for chat + avatar.
 * Derive phase only from real stream signals — see docs/UI_AGENT_STATE_MAP.md.
 */

export type AgentPhase =
  | "idle"
  | "thinking"
  | "streaming_reply"
  | "tool_search"
  | "tool_file"
  | "tool_terminal"
  | "tool_generic"
  | "awaiting_confirm"
  | "error"
  | "task_running";

export type OpenToolEntry = { id: string; name: string };

export type AgentActivityState = {
  phase: AgentPhase;
  streaming: boolean;
  thinkingText: string;
  replyDraft: string;
  activeToolName: string;
  activeToolId: string;
  openToolIds: string[];
  /** Parallel name map so concurrent tools re-bind phase correctly on tool_end. */
  openTools: OpenToolEntry[];
  openToolCount: number;
  pendingConfirmation: boolean;
  lastError: string;
  label: string;
};

export const initialAgentActivity = (): AgentActivityState => ({
  phase: "idle",
  streaming: false,
  thinkingText: "",
  replyDraft: "",
  activeToolName: "",
  activeToolId: "",
  openToolIds: [],
  openTools: [],
  openToolCount: 0,
  pendingConfirmation: false,
  lastError: "",
  label: "Idle",
});

const FILE_TOOLS = new Set([
  "file_write",
  "file_read",
  "file_list",
  "file_mkdir",
  "file_move",
  "file_copy",
  "file_delete",
  "artifact_write",
  "notepad_write",
]);

function phaseFromTool(name: string): AgentPhase {
  const n = (name || "").toLowerCase();
  if (n === "web_search" || n.includes("search")) return "tool_search";
  if (FILE_TOOLS.has(n) || n.includes("file") || n.includes("artifact")) return "tool_file";
  if (n === "terminal_run" || n.includes("terminal") || n.includes("command")) return "tool_terminal";
  return "tool_generic";
}

function labelFor(state: Omit<AgentActivityState, "label">): string {
  switch (state.phase) {
    case "thinking":
      return state.thinkingText ? "Reasoning" : "Waiting";
    case "streaming_reply":
      return "Writing";
    case "tool_search":
      return "Searching";
    case "tool_file":
      return "Files";
    case "tool_terminal":
      return "Terminal";
    case "tool_generic":
      return state.activeToolName ? `Tool: ${state.activeToolName}` : "Working";
    case "awaiting_confirm":
      return "Confirm";
    case "error":
      return "Error";
    case "task_running":
      return "Tasks";
    default:
      return "Idle";
  }
}

function withLabel(state: Omit<AgentActivityState, "label">): AgentActivityState {
  return { ...state, label: labelFor(state) };
}

export type ActivityAction =
  | { type: "stream_start" }
  | { type: "stream_end" }
  | { type: "thinking"; content: string }
  | { type: "agent_token"; token: string }
  | { type: "tool_start"; id: string; name: string }
  | { type: "tool_end"; id: string }
  | { type: "tool_error"; id: string; message?: string }
  | { type: "status_mode"; mode: string; tool?: string }
  | { type: "task_step"; status: string }
  | { type: "final"; response: string; executionStatus?: string; success?: boolean }
  | { type: "error"; message: string }
  | { type: "reset" };

export function agentActivityReducer(state: AgentActivityState, action: ActivityAction): AgentActivityState {
  switch (action.type) {
    case "reset":
      return initialAgentActivity();

    case "stream_start":
      return withLabel({
        ...initialAgentActivity(),
        streaming: true,
        phase: "thinking",
      });

    case "stream_end":
      return withLabel({
        ...state,
        streaming: false,
        openToolCount: 0,
        openToolIds: [],
        openTools: [],
        activeToolName: "",
        activeToolId: "",
        phase: state.pendingConfirmation ? "awaiting_confirm" : state.phase === "error" ? "error" : "idle",
      });

    case "thinking":
      return withLabel({
        ...state,
        thinkingText: action.content || state.thinkingText,
        phase:
          state.openToolCount > 0
            ? phaseFromTool(state.activeToolName)
            : state.replyDraft
              ? "streaming_reply"
              : "thinking",
      });

    case "agent_token": {
      const replyDraft = (state.replyDraft || "") + (action.token || "");
      return withLabel({
        ...state,
        replyDraft,
        phase: state.openToolCount > 0 ? phaseFromTool(state.activeToolName) : "streaming_reply",
      });
    }

    case "tool_start": {
      const already = state.openTools.some((t) => t.id === action.id);
      const openTools = already
        ? state.openTools.map((t) => (t.id === action.id ? { id: action.id, name: action.name } : t))
        : [...state.openTools, { id: action.id, name: action.name }];
      const openToolIds = openTools.map((t) => t.id);
      const openToolCount = openTools.length;
      return withLabel({
        ...state,
        openTools,
        openToolIds,
        openToolCount,
        activeToolName: action.name,
        activeToolId: action.id,
        phase: phaseFromTool(action.name),
      });
    }

    case "tool_end": {
      const openTools = state.openTools.filter((t) => t.id !== action.id);
      const openToolIds = openTools.map((t) => t.id);
      const openToolCount = openTools.length;
      const next = openToolCount > 0 ? openTools[openTools.length - 1] : null;
      return withLabel({
        ...state,
        openTools,
        openToolIds,
        openToolCount,
        activeToolName: next?.name || "",
        activeToolId: next?.id || "",
        phase:
          openToolCount > 0
            ? phaseFromTool(next?.name || "")
            : state.replyDraft
              ? "streaming_reply"
              : state.streaming
                ? "thinking"
                : "idle",
      });
    }

    case "tool_error": {
      const openTools = state.openTools.filter((t) => t.id !== action.id);
      const openToolIds = openTools.map((t) => t.id);
      const openToolCount = openTools.length;
      const next = openToolCount > 0 ? openTools[openTools.length - 1] : null;
      return withLabel({
        ...state,
        openTools,
        openToolIds,
        openToolCount,
        lastError: action.message || "Tool failed",
        phase: openToolCount > 0 ? phaseFromTool(next?.name || "") : "error",
        activeToolName: next?.name || "",
        activeToolId: next?.id || "",
      });
    }

    case "status_mode": {
      const mode = (action.mode || "").toLowerCase();
      if (mode === "idle" && !state.streaming) {
        return withLabel({ ...state, phase: state.pendingConfirmation ? "awaiting_confirm" : "idle" });
      }
      if (mode === "research" || action.tool === "web_search") {
        return withLabel({ ...state, phase: "tool_search", activeToolName: action.tool || state.activeToolName });
      }
      if (mode === "coding") {
        return withLabel({ ...state, phase: "tool_file", activeToolName: action.tool || state.activeToolName });
      }
      if (mode === "thinking") {
        return withLabel({ ...state, phase: state.openToolCount > 0 ? phaseFromTool(state.activeToolName) : "thinking" });
      }
      if (mode === "working" && state.openToolCount > 0) {
        return withLabel({ ...state, phase: phaseFromTool(action.tool || state.activeToolName) });
      }
      return state;
    }

    case "task_step": {
      const st = (action.status || "").toLowerCase();
      if (st === "running" || st === "retrying") {
        return withLabel({ ...state, phase: "task_running" });
      }
      if (st === "awaiting_confirmation" || st === "pending_confirmation") {
        return withLabel({ ...state, pendingConfirmation: true, phase: "awaiting_confirm" });
      }
      return state;
    }

    case "final": {
      const executionStatus = String(action.executionStatus || "").toLowerCase();
      const pendingConfirmation = ["needs_permission", "needs_approval"].includes(executionStatus);
      const failed = action.success === false || ["failed", "blocked", "retryable"].includes(executionStatus);
      return withLabel({
        ...state,
        streaming: false,
        openToolCount: 0,
        openToolIds: [],
        openTools: [],
        activeToolName: "",
        activeToolId: "",
        replyDraft: "",
        pendingConfirmation,
        phase: pendingConfirmation ? "awaiting_confirm" : failed ? "error" : "idle",
      });
    }

    case "error":
      return withLabel({
        ...state,
        streaming: false,
        lastError: action.message || "Error",
        phase: "error",
        openToolCount: 0,
        openToolIds: [],
        openTools: [],
        activeToolName: "",
        activeToolId: "",
      });

    default:
      return state;
  }
}

/**
 * Whether a stream event should mutate the *visible* chat for the active Session.
 * Aborted streams must not append messages/tools to a new Session — but callers
 * should still reset the activity machine (see sendText finally).
 */
export function isStreamEventCurrent(
  originatingThreadId: string,
  activeThreadId: string,
  aborted: boolean,
): boolean {
  return !aborted && Boolean(originatingThreadId) && originatingThreadId === activeThreadId;
}

/** Thread still matches; used when we must finish cleanup even if the stream was aborted. */
export function isStreamThreadCurrent(originatingThreadId: string, activeThreadId: string): boolean {
  return Boolean(originatingThreadId) && originatingThreadId === activeThreadId;
}

export function toolCategoryFromPhase(phase: AgentPhase): string {
  switch (phase) {
    case "tool_search":
      return "search";
    case "tool_file":
      return "file_write";
    case "tool_terminal":
      return "terminal";
    case "thinking":
    case "streaming_reply":
      return "generic";
    default:
      return "generic";
  }
}
