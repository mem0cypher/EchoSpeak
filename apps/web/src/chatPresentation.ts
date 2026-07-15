/** Only durable, user-actionable activity stays in the Chat transcript. */
export function shouldPersistChatActivity(kind: string): boolean {
  const value = String(kind || "").toLowerCase();
  return value === "error";
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
