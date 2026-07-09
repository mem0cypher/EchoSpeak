import { describe, expect, it } from "vitest";
import {
  agentActivityReducer,
  initialAgentActivity,
  isConfirmPromptText,
} from "./agentActivity";

describe("agentActivityReducer", () => {
  it("starts in thinking on stream_start", () => {
    const s = agentActivityReducer(initialAgentActivity(), { type: "stream_start" });
    expect(s.phase).toEqual("thinking");
    expect(s.streaming).toEqual(true);
  });

  it("maps web_search tool to tool_search and back to thinking", () => {
    let s = agentActivityReducer(initialAgentActivity(), { type: "stream_start" });
    s = agentActivityReducer(s, { type: "tool_start", id: "1", name: "web_search" });
    expect(s.phase).toEqual("tool_search");
    expect(s.openToolCount).toEqual(1);
    s = agentActivityReducer(s, { type: "tool_end", id: "1" });
    expect(s.openToolCount).toEqual(0);
    expect(s.phase).toEqual("thinking");
  });

  it("marks awaiting_confirm from final confirm prompt", () => {
    let s = agentActivityReducer(initialAgentActivity(), { type: "stream_start" });
    s = agentActivityReducer(s, {
      type: "final",
      response: "I can do this: Write file. Reply 'confirm' to proceed or 'cancel' to abort.",
    });
    expect(s.phase).toEqual("awaiting_confirm");
    expect(s.pendingConfirmation).toEqual(true);
    expect(isConfirmPromptText("Reply 'confirm' to proceed")).toEqual(true);
  });

  it("streams reply tokens into streaming_reply phase", () => {
    let s = agentActivityReducer(initialAgentActivity(), { type: "stream_start" });
    s = agentActivityReducer(s, { type: "agent_token", token: "Hello" });
    expect(s.phase).toEqual("streaming_reply");
    expect(s.replyDraft).toEqual("Hello");
  });
});
