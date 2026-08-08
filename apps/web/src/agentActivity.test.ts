import { describe, expect, it } from "vitest";
import {
  agentActivityReducer,
  initialAgentActivity,
  isStreamEventCurrent,
  isStreamThreadCurrent,
} from "./agentActivity";

describe("agentActivityReducer", () => {
  it("waits for a real backend phase after stream_start", () => {
    const s = agentActivityReducer(initialAgentActivity(), { type: "stream_start" });
    expect(s.phase).toEqual("waiting_for_model");
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

  it("marks awaiting_confirm only from structured execution state", () => {
    let s = agentActivityReducer(initialAgentActivity(), { type: "stream_start" });
    s = agentActivityReducer(s, {
      type: "final",
      response: "I can do this: Write file. Reply 'confirm' to proceed or 'cancel' to abort.",
      executionStatus: "needs_permission",
      success: true,
    });
    expect(s.phase).toEqual("awaiting_confirm");
    expect(s.pendingConfirmation).toEqual(true);
  });

  it("does not infer approval authority from assistant prose", () => {
    let s = agentActivityReducer(initialAgentActivity(), { type: "stream_start" });
    s = agentActivityReducer(s, {
      type: "final",
      response: "Reply 'confirm' to proceed.",
      executionStatus: "complete",
      success: true,
    });
    expect(s.phase).toEqual("idle");
    expect(s.pendingConfirmation).toEqual(false);
  });

  it("streams reply tokens into streaming_reply phase", () => {
    let s = agentActivityReducer(initialAgentActivity(), { type: "stream_start" });
    s = agentActivityReducer(s, { type: "agent_token", token: "Hello" });
    expect(s.phase).toEqual("streaming_reply");
    expect(s.replyDraft).toEqual("Hello");
  });

  it("correlates repeated tool names by distinct run ids", () => {
    let s = agentActivityReducer(initialAgentActivity(), { type: "stream_start" });
    s = agentActivityReducer(s, { type: "tool_start", id: "run-a", name: "file_read" });
    s = agentActivityReducer(s, { type: "tool_start", id: "run-b", name: "file_read" });
    s = agentActivityReducer(s, { type: "tool_end", id: "run-a" });
    expect(s.openToolIds).toEqual(["run-b"]);
    expect(s.openToolCount).toEqual(1);
    expect(s.activeToolId).toEqual("run-b");
    expect(s.activeToolName).toEqual("file_read");
  });

  it("rebinds active tool to remaining open tool after concurrent end", () => {
    let s = agentActivityReducer(initialAgentActivity(), { type: "stream_start" });
    s = agentActivityReducer(s, { type: "tool_start", id: "search-1", name: "web_search" });
    s = agentActivityReducer(s, { type: "tool_start", id: "term-1", name: "terminal_run" });
    s = agentActivityReducer(s, { type: "tool_end", id: "term-1" });
    expect(s.openToolIds).toEqual(["search-1"]);
    expect(s.phase).toEqual("tool_search");
    expect(s.activeToolName).toEqual("web_search");
  });

  it("reset clears sticky phase after abort/session switch", () => {
    let s = agentActivityReducer(initialAgentActivity(), { type: "stream_start" });
    s = agentActivityReducer(s, { type: "tool_start", id: "1", name: "web_search" });
    s = agentActivityReducer(s, { type: "reset" });
    expect(s.phase).toEqual("idle");
    expect(s.streaming).toEqual(false);
    expect(s.openToolCount).toEqual(0);
  });

  it("rejects late events after a thread switch or abort", () => {
    expect(isStreamEventCurrent("thread-a", "thread-a", false)).toEqual(true);
    expect(isStreamEventCurrent("thread-a", "thread-b", false)).toEqual(false);
    expect(isStreamEventCurrent("thread-a", "thread-a", true)).toEqual(false);
    expect(isStreamThreadCurrent("thread-a", "thread-a")).toEqual(true);
    expect(isStreamThreadCurrent("thread-a", "thread-b")).toEqual(false);
  });
});
