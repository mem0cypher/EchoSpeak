/** Only durable, user-actionable activity stays in the Chat transcript after a turn. */
export function shouldPersistChatActivity(kind: string): boolean {
  const value = String(kind || "").toLowerCase();
  return value === "error";
}
/**
 * Ephemeral live rows shown only while the active Session is streaming.
 * Thinking cards carry tool/search/task steps; they must not remain after the turn.
 */
export function shouldShowLiveChatActivity(kind: string, streaming: boolean): boolean {
  if (!streaming) return false;
  const value = String(kind || "").toLowerCase();
  return value === "thinking";
}

/** Whether a timeline activity item should appear in Chat right now. */
export function shouldIncludeChatActivity(kind: string, streaming: boolean): boolean {
  return shouldPersistChatActivity(kind) || shouldShowLiveChatActivity(kind, streaming);
}

/** One final assistant bubble — partial stream beats + final text, no duplicates. */
export function mergeFinalReply(
  response: unknown,
  liveDraft: unknown,
  partialReplies: unknown[],
  eventPartialReplies: unknown[] = []
): string {
  let reply = String(response || liveDraft || "").trim();
  const partials = [...partialReplies, ...eventPartialReplies]
    .map((part) => String(part || "").trim())
    .filter(Boolean)
    .filter((part, index, rows) => rows.indexOf(part) === index);
  if (partials.length && !partials.some((part) => reply.includes(part))) {
    reply = [...partials, reply].filter(Boolean).join("\n\n");
  }
  return reply;
}

/**
 * Stale completion guard: a final response may only land if it still owns the
 * active Session and Project the user is viewing.
 */
export function canApplyFinalToChat(args: {
  activeThreadId: string;
  activeProjectId: string;
  ownedThreadId: string;
  ownedProjectId?: string;
  streamOpen: boolean;
}): boolean {
  if (!args.streamOpen) return false;
  if (!args.activeThreadId || args.activeThreadId !== args.ownedThreadId) return false;
  const ownedProject = String(args.ownedProjectId || "").trim();
  const activeProject = String(args.activeProjectId || "").trim();
  if (ownedProject && activeProject && ownedProject !== activeProject) return false;
  return true;
}

export type LiveOperationalStatus = {
  headline: string;
  task?: string;
  tool?: string;
  skill?: string;
  search?: string;
  verifying?: boolean;
};

/** Map agent phase + open work into concise operational labels (no chain-of-thought). */
export function buildLiveOperationalStatus(args: {
  phase?: string;
  streaming?: boolean;
  label?: string;
  activeToolName?: string;
  thinkingText?: string;
  taskDescription?: string;
  searchHint?: string;
}): LiveOperationalStatus {
  const phase = String(args.phase || "").toLowerCase();
  const tool = String(args.activeToolName || "").trim();
  const toolLow = tool.toLowerCase();
  const think = String(args.thinkingText || "").toLowerCase();
  const skill =
    toolLow.startsWith("skill_")
      ? tool.slice(6).replace(/_/g, " ")
      : toolLow.includes("skill") && !toolLow.includes("skill_writer")
        ? tool
        : "";

  let headline = "Thinking";
  if (phase === "understanding") headline = "Understanding";
  else if (phase === "planning") headline = "Planning";
  else if (phase === "waiting_for_model" || phase === "thinking") headline = "Thinking";
  else if (phase === "responding" || phase === "streaming_reply") headline = "Responding";
  else if (phase === "awaiting_confirm") headline = "Awaiting approval";
  else if (phase === "error") headline = "Error";
  else if (skill) headline = "Running Skill";
  else if (phase === "tool_search" || toolLow === "web_search" || (toolLow.includes("search") && !skill))
    headline = "Searching";
  else if (phase === "task_running" || args.taskDescription) headline = "Working on task";
  else if (phase.startsWith("tool_")) headline = "Using tool";
  else if (think.includes("verif")) headline = "Verifying";
  else if (think.includes("plan")) headline = "Planning";
  else if (think.includes("understand")) headline = "Understanding";
  else if (args.streaming) headline = "Thinking";
  else if (args.label) headline = String(args.label);

  const search =
    args.searchHint ||
    (headline === "Searching" && tool ? tool : undefined) ||
    (toolLow.includes("search") ? tool : undefined);

  const verifying = headline === "Verifying" || think.includes("verif");

  return {
    headline,
    task: args.taskDescription ? String(args.taskDescription).slice(0, 120) : undefined,
    tool: tool && !skill ? tool : tool && skill ? tool : tool || undefined,
    skill: skill || undefined,
    search: search || undefined,
    verifying: verifying || undefined,
  };
}

/** Sanitize output to strip internal envelopes, IDs, policy texts, or tracebacks from normal Chat. */
export function sanitizeUserFacingText(text: string): string {
  if (!text) return "";
  let clean = String(text);
  clean = clean.replace(/\[ECHOSPEAK_MODEL_TURN_ENVELOPE\][\s\S]*?\[\/ECHOSPEAK_MODEL_TURN_ENVELOPE\]/g, "");
  clean = clean.replace(/\[ECHOSPEAK_MODEL_TURN_ENVELOPE\]/g, "");
  clean = clean.replace(/task_run_[a-f0-9\-]{8,}/gi, "");
  clean = clean.replace(/req_[a-f0-9\-]{8,}/gi, "");
  clean = clean.replace(/<agent_decision>[\s\S]*?<\/agent_decision>/gi, "");
  clean = clean.replace(/Selected provider could not complete[\s\S]*/gi, "Provider stalled — retrying.");
  return clean.trim();
}
