export function shouldPersistChatActivity(kind: string): boolean {
  return kind === "error";
}

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
