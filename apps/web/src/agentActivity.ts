/**
 * Shared agent activity state machine for chat + avatar.
 * Derive phase only from real stream signals — see docs/UI_AGENT_STATE_MAP.md.
 */

export type AgentPhase =
  | "idle"
  | "understanding"
  | "planning"
  | "waiting_for_model"
  | "thinking"
  | "streaming_reply"
  | "tool_search"
  | "tool_file"
  | "tool_terminal"
  | "tool_generic"
  | "awaiting_confirm"
  | "waiting_for_user"
  | "blocked"
  | "error"
  | "task_running";

export type OpenToolEntry = { id: string; name: string };

export type SemanticRequirementActivity = {
  label: string;
  kind: string;
  status: string;
  missing_fields: string[];
  attempt_count: number;
  retry_count: number;
  source_count: number;
  required: boolean;
};

export type SemanticActivitySource = {
  label: string;
  url: string;
};

export type SemanticActivityEvent = {
  schema_version: 1;
  kind: string;
  stage?: string;
  status?: string;
  label?: string;
  objective?: string;
  active_requirement?: string;
  model?: string;
  tool_name?: string;
  next_action?: string;
  recovery_reason?: string;
  requirements?: SemanticRequirementActivity[];
  missing_fields?: string[];
  sources?: SemanticActivitySource[];
  attempt_count?: number;
  retry_count?: number;
  source_count?: number;
  recovery_epoch?: number;
  completion_disposition?: string;
  finalizable?: boolean;
  iteration?: number;
};

export type AgentActivityTimelineEntry = {
  key: string;
  kind: string;
  stage: string;
  status: string;
  label: string;
  at: number;
};

export type AgentActivityState = {
  phase: AgentPhase;
  streaming: boolean;
  thinkingText: string;
  replyDraft: string;
  activeToolName: string;
  activeToolId: string;
  inputPreview: string;
  openToolIds: string[];
  /** Parallel name map so concurrent tools re-bind phase correctly on tool_end. */
  openTools: OpenToolEntry[];
  openToolCount: number;
  pendingConfirmation: boolean;
  lastError: string;
  label: string;
  objective: string;
  activeRequirement: string;
  activeModel: string;
  recoveryReason: string;
  nextAction: string;
  startTime: number;
  tokenUsage: { prompt?: number; completion?: number; total?: number } | null;
  iteration: number;
  requirements: SemanticRequirementActivity[];
  missingFields: string[];
  attemptCount: number;
  retryCount: number;
  sourceCount: number;
  recoveryEpoch: number;
  completionDisposition: string;
  finalizable: boolean;
  timeline: AgentActivityTimelineEntry[];
  sources: SemanticActivitySource[];
};

export const initialAgentActivity = (): AgentActivityState => ({
  phase: "idle",
  streaming: false,
  thinkingText: "",
  replyDraft: "",
  activeToolName: "",
  activeToolId: "",
  inputPreview: "",
  openToolIds: [],
  openTools: [],
  openToolCount: 0,
  pendingConfirmation: false,
  lastError: "",
  label: "Idle",
  objective: "",
  activeRequirement: "",
  activeModel: "",
  recoveryReason: "",
  nextAction: "",
  startTime: 0,
  tokenUsage: null,
  iteration: 0,
  requirements: [],
  missingFields: [],
  attemptCount: 0,
  retryCount: 0,
  sourceCount: 0,
  recoveryEpoch: 0,
  completionDisposition: "pending",
  finalizable: false,
  timeline: [],
  sources: [],
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
  const tool = String(state.activeToolName || "").toLowerCase();
  const think = String(state.thinkingText || "").toLowerCase();
  const skillLike = tool.startsWith("skill_") || (tool.includes("skill") && !tool.includes("skill_writer"));
  if (state.recoveryReason) return state.recoveryReason;
  switch (state.phase) {
    case "understanding":
      return "Understanding";
    case "planning":
      return "Planning";
    case "waiting_for_model":
      return "Waiting for model";
    case "thinking":
      if (think.includes("verif")) return "Verifying";
      if (think.includes("plan")) return "Planning";
      if (think.includes("understand")) return "Understanding";
      return "Thinking";
    case "streaming_reply":
      return "Responding";
    case "tool_search":
      return "Searching";
    case "tool_file":
    case "tool_terminal":
    case "tool_generic":
      if (skillLike) return "Running Skill";
      return state.activeToolName ? "Using tool" : "Working";
    case "awaiting_confirm":
      return "Awaiting approval";
    case "waiting_for_user":
      return "Waiting for you";
    case "blocked":
      return "Blocked";
    case "error":
      return "Error";
    case "task_running":
      return "Working on task";
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
  | { type: "tool_start"; id: string; name: string; input_preview?: string }
  | { type: "tool_end"; id: string; summary?: string }
  | { type: "tool_error"; id: string; message?: string }
  | { type: "status_mode"; mode: string; tool?: string }
  | { type: "lifecycle"; phase: string; error?: string }
  | { type: "turn_bound"; model?: string; objective?: string }
  | { type: "step_update"; requirement?: string; nextAction?: string }
  | { type: "recovery"; reason: string }
  | { type: "steer"; instruction: string }
  | { type: "token_usage"; prompt?: number; completion?: number; total?: number }
  | { type: "iteration_boundary"; iteration: number; model?: string }
  | { type: "semantic"; activity: SemanticActivityEvent; at?: number }
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
        phase: "waiting_for_model",
        startTime: Date.now(),
      });

    case "semantic": {
      const activity = action.activity;
      const kind = String(activity.kind || "status").toLowerCase();
      const stage = String(activity.stage || "").toLowerCase();
      const status = String(activity.status || "").toLowerCase();
      const toolName = activity.tool_name || state.activeToolName;
      let phase = state.phase;
      let streaming = state.streaming;
      let pendingConfirmation = state.pendingConfirmation;
      if (kind === "tool") {
        phase = status === "running" ? phaseFromTool(toolName) : state.replyDraft ? "streaming_reply" : "thinking";
        streaming = status === "running" ? true : state.streaming;
      } else if (kind === "model") {
        phase = "waiting_for_model";
        streaming = true;
      } else if (kind === "reasoning" || kind === "recovery") {
        phase = "thinking";
        streaming = true;
      } else if (kind === "response") {
        if (["needs_permission", "needs_approval"].includes(status)) {
          phase = "awaiting_confirm";
          pendingConfirmation = true;
          streaming = false;
        } else if (status === "needs_clarification") {
          phase = "waiting_for_user";
          streaming = false;
        } else if (status === "blocked") {
          phase = "blocked";
          streaming = false;
        } else if (["failed", "retryable"].includes(status)) {
          phase = "error";
          streaming = false;
        } else {
          phase = status === "running" ? "streaming_reply" : "idle";
          streaming = status === "running";
        }
      } else if (kind === "error") {
        phase = "error";
        streaming = false;
      } else if (kind === "task") {
        if (activity.next_action === "wait_for_approval" || status === "suspended_waiting_for_approval") {
          phase = "awaiting_confirm";
          pendingConfirmation = true;
          streaming = false;
        } else if (activity.next_action === "wait_for_user" || status === "suspended_waiting_for_user") {
          phase = "waiting_for_user";
          streaming = false;
        } else if (
          activity.next_action === "hard_failure" ||
          status.startsWith("failed") ||
          ["quarantined", "blocked_policy", "runtime_authority_conflict", "completion_projection_conflict"].includes(status)
        ) {
          phase = status === "blocked_policy" ? "blocked" : "error";
          streaming = false;
        } else if (activity.next_action === "wait_for_external_result" || status === "suspended_waiting_for_external_result") {
          phase = "task_running";
          streaming = false;
        } else if (["completed", "cancelled", "superseded"].includes(status)) {
          phase = "idle";
          streaming = false;
        } else {
          phase = "task_running";
          streaming = true;
        }
      } else if (kind === "lifecycle") {
        if (stage === "understanding") phase = "understanding";
        else if (stage === "planning") phase = "planning";
        else if (stage === "waiting_for_model") phase = "waiting_for_model";
        else if (stage === "thinking") phase = "thinking";
        else if (stage === "responding") phase = "streaming_reply";
        else if (stage === "waiting_for_approval") {
          phase = "awaiting_confirm";
          pendingConfirmation = true;
          streaming = false;
        } else if (stage === "waiting_for_user") {
          phase = "waiting_for_user";
          streaming = false;
        } else if (stage === "blocked") {
          phase = "blocked";
          streaming = false;
        } else if (stage === "failed") {
          phase = "error";
          streaming = false;
        } else if (["completed", "cancelled"].includes(stage)) {
          phase = "idle";
          streaming = false;
        } else {
          streaming = true;
        }
      } else if (kind === "status" && stage === "idle") {
        phase = pendingConfirmation ? "awaiting_confirm" : "idle";
        streaming = false;
      }

      const label = String(activity.label || "").trim();
      const at = Number(action.at || Date.now());
      const previous = state.timeline[state.timeline.length - 1];
      const shouldAppend = Boolean(
        label && (!previous || previous.label !== label || previous.status !== status),
      );
      const timeline = shouldAppend
        ? [
            ...state.timeline,
            {
              key: `${at}:${kind}:${stage}:${status}:${label}`,
              kind,
              stage,
              status,
              label,
              at,
            },
          ].slice(-12)
        : state.timeline;
      const sources = activity.sources
        ? [...state.sources, ...activity.sources]
            .filter((source, index, rows) => rows.findIndex((item) =>
              (item.url && item.url === source.url) || (!item.url && item.label === source.label),
            ) === index)
            .slice(-8)
        : state.sources;
      const next = {
        ...state,
        phase,
        streaming,
        pendingConfirmation,
        label: label || state.label,
        objective: activity.objective ?? state.objective,
        activeRequirement: activity.active_requirement ?? state.activeRequirement,
        activeModel: activity.model ?? state.activeModel,
        activeToolName:
          kind === "tool" && status !== "running"
            ? state.activeToolName
            : activity.tool_name ?? state.activeToolName,
        nextAction: activity.next_action ?? state.nextAction,
        recoveryReason:
          kind === "model" || (kind === "response" && status !== "running") || (kind === "status" && stage === "idle")
            ? ""
            : activity.recovery_reason ?? (kind === "recovery" ? label : state.recoveryReason),
        requirements: activity.requirements ?? state.requirements,
        missingFields: activity.missing_fields ?? state.missingFields,
        attemptCount: activity.attempt_count ?? state.attemptCount,
        retryCount: activity.retry_count ?? state.retryCount,
        sourceCount:
          kind === "tool" && activity.source_count != null
            ? Math.max(state.sourceCount, activity.source_count)
            : activity.source_count ?? state.sourceCount,
        recoveryEpoch: activity.recovery_epoch ?? state.recoveryEpoch,
        completionDisposition: activity.completion_disposition ?? state.completionDisposition,
        finalizable: activity.finalizable ?? state.finalizable,
        iteration: Math.max(state.iteration, Number(activity.iteration || 0)),
        timeline,
        sources,
      };
      return { ...next, label: label || labelFor(next) };
    }

    case "iteration_boundary":
      return withLabel({
        ...state,
        iteration: Math.max(state.iteration, Number(action.iteration || 0)),
        activeModel: action.model || state.activeModel,
        recoveryReason: "",
      });

    case "turn_bound":
      return withLabel({
        ...state,
        activeModel: action.model || state.activeModel,
        objective: action.objective || state.objective,
      });

    case "step_update":
      return withLabel({
        ...state,
        activeRequirement: action.requirement || state.activeRequirement,
        nextAction: action.nextAction || state.nextAction,
      });

    case "recovery":
      return withLabel({
        ...state,
        recoveryReason: action.reason || "",
      });

    case "steer":
      return withLabel({
        ...state,
        objective: state.objective
          ? `${state.objective}\n[Steer]: ${action.instruction}`
          : action.instruction,
        recoveryReason: "",
      });

    case "token_usage":
      return withLabel({
        ...state,
        tokenUsage: {
          prompt: action.prompt ?? state.tokenUsage?.prompt,
          completion: action.completion ?? state.tokenUsage?.completion,
          total: action.total ?? state.tokenUsage?.total,
        },
      });


    case "lifecycle": {
      const phase = String(action.phase || "").toLowerCase();
      if (phase === "understanding") return withLabel({ ...state, streaming: true, phase: "understanding" });
      if (phase === "planning") return withLabel({ ...state, streaming: true, phase: "planning" });
      if (phase === "waiting_for_model") return withLabel({ ...state, streaming: true, phase: "waiting_for_model" });
      if (phase === "thinking") return withLabel({ ...state, streaming: true, phase: "thinking" });
      if (phase === "responding") return withLabel({ ...state, streaming: true, phase: "streaming_reply" });
      if (phase === "waiting_for_approval") {
        return withLabel({ ...state, streaming: false, pendingConfirmation: true, phase: "awaiting_confirm" });
      }
      if (phase === "waiting_for_user") return withLabel({ ...state, streaming: false, phase: "waiting_for_user" });
      if (phase === "blocked") return withLabel({ ...state, streaming: false, lastError: action.error || state.lastError, phase: "blocked" });
      if (phase === "failed") return withLabel({ ...state, streaming: false, lastError: action.error || state.lastError, phase: "error" });
      if (phase === "cancelled" || phase === "completed") {
        return withLabel({ ...state, streaming: false, phase: "idle" });
      }
      return state;
    }

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
        inputPreview: action.input_preview || "",
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
        inputPreview: next ? state.inputPreview : "",
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
        inputPreview: next ? state.inputPreview : "",
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

    case "final": {
      const executionStatus = String(action.executionStatus || "").toLowerCase();
      const pendingConfirmation = ["needs_permission", "needs_approval"].includes(executionStatus);
      const blocked = executionStatus === "blocked";
      const waitingForUser = executionStatus === "needs_clarification";
      const failed = !blocked && (action.success === false || ["failed", "retryable"].includes(executionStatus));
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
        phase: pendingConfirmation
          ? "awaiting_confirm"
          : waitingForUser
            ? "waiting_for_user"
            : blocked
              ? "blocked"
              : failed
                ? "error"
                : "idle",
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
    case "understanding":
    case "planning":
    case "waiting_for_model":
      return "generic";
    default:
      return "generic";
  }
}

type StreamActivityPacket = Record<string, unknown>;

const packetString = (packet: StreamActivityPacket, key: string) =>
  String(packet[key] || "").trim();

const packetNumber = (packet: StreamActivityPacket, key: string) => {
  const value = Number(packet[key] || 0);
  return Number.isFinite(value) ? value : 0;
};

const packetTimeMs = (packet: StreamActivityPacket) => {
  const value = packetNumber(packet, "at");
  if (!value) return Date.now();
  return value < 1e12 ? value * 1000 : value;
};

/**
 * The sole browser-side decoder for canonical /query/stream activity.
 *
 * The backend's versioned `activity` object owns user-facing semantics. Raw
 * packet mapping remains only as a compatibility projection for older sidecars
 * and for mechanical data such as streamed reply tokens and ToolRun identity.
 */
export function activityActionsFromStreamEvent(event: StreamActivityPacket): ActivityAction[] {
  const type = packetString(event, "type").toLowerCase();
  const actions: ActivityAction[] = [];
  const activityValue = event.activity;
  const semantic = (
    activityValue &&
    typeof activityValue === "object" &&
    Number((activityValue as Record<string, unknown>).schema_version || 0) === 1
  ) ? activityValue as SemanticActivityEvent : null;

  if (type === "agent_token") {
    const token = String(event.data || "");
    if (token) actions.push({ type: "agent_token", token });
  } else if (type === "tool_start") {
    actions.push({
      type: "tool_start",
      id: packetString(event, "id"),
      name: packetString(event, "name"),
      input_preview: packetString(event, "input"),
    });
  } else if (type === "tool_end") {
    const outcome = event.outcome && typeof event.outcome === "object"
      ? event.outcome as Record<string, unknown>
      : {};
    if (outcome.success === false) {
      actions.push({
        type: "tool_error",
        id: packetString(event, "id"),
        message: String(outcome.error_message || "Tool failed"),
      });
    } else {
      actions.push({ type: "tool_end", id: packetString(event, "id") });
    }
  } else if (type === "tool_error") {
    actions.push({
      type: "tool_error",
      id: packetString(event, "id"),
      message: packetString(event, "error") || "Tool failed",
    });
  } else if (type === "final") {
    const threadState = event.thread_state && typeof event.thread_state === "object"
      ? event.thread_state as Record<string, unknown>
      : {};
    actions.push({
      type: "final",
      response: packetString(event, "response"),
      executionStatus: String(threadState.execution_status || ""),
      success: event.success !== false,
    });
  } else if (type === "error") {
    actions.push({ type: "error", message: packetString(event, "message") || "This run stopped safely." });
  }

  if (semantic) {
    actions.push({ type: "semantic", activity: semantic, at: packetTimeMs(event) });
    return actions;
  }

  // Compatibility-only semantic mapping for an older local sidecar.
  if (type === "turn_bound") {
    actions.push({ type: "turn_bound", model: packetString(event, "model") });
  } else if (type === "task_bound") {
    actions.push({ type: "turn_bound", objective: packetString(event, "objective") });
    actions.push({
      type: "step_update",
      requirement: packetString(event, "active_requirement"),
      nextAction: packetString(event, "next_action"),
    });
  } else if (type === "iteration_boundary") {
    actions.push({
      type: "iteration_boundary",
      iteration: packetNumber(event, "iteration"),
      model: packetString(event, "model"),
    });
  } else if (type === "token_usage") {
    actions.push({
      type: "token_usage",
      prompt: packetNumber(event, "prompt") || undefined,
      completion: packetNumber(event, "completion") || undefined,
      total: packetNumber(event, "total") || undefined,
    });
  } else if (type === "reasoning_summary" || type === "thinking") {
    const content = packetString(event, "content");
    if (content) actions.push({ type: "thinking", content });
  } else if (type === "recovery") {
    actions.push({ type: "recovery", reason: packetString(event, "message") || "Trying another approach." });
  } else if (type === "lifecycle") {
    actions.push({
      type: "lifecycle",
      phase: packetString(event, "phase"),
      error: packetString(event, "error") || undefined,
    });
  } else if (type === "status") {
    actions.push({
      type: "status_mode",
      mode: packetString(event, "agent_mode"),
      tool: packetString(event, "tool") || undefined,
    });
  } else if (type === "partial_reply") {
    actions.push({ type: "thinking", content: "Working" });
  }
  return actions;
}
