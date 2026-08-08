import { describe, expect, it } from "vitest";
import {
  buildLiveOperationalStatus,
  canApplyFinalToChat,
  mergeFinalReply,
  shouldIncludeChatActivity,
  shouldPersistChatActivity,
  shouldShowLiveChatActivity,
} from "./chatPresentation";

describe("calm Chat presentation", () => {
  it("persists actionable errors but not internal operation rows after the turn", () => {
    expect(shouldPersistChatActivity("error")).toEqual(true);
    for (const kind of ["thinking", "tool", "memory", "task", "stage", "evidence"]) {
      expect(shouldPersistChatActivity(kind)).toEqual(false);
    }
  });

  it("shows ephemeral thinking activity only while streaming", () => {
    expect(shouldShowLiveChatActivity("thinking", true)).toEqual(true);
    expect(shouldShowLiveChatActivity("thinking", false)).toEqual(false);
    expect(shouldShowLiveChatActivity("tool", true)).toEqual(false);
    expect(shouldIncludeChatActivity("thinking", true)).toEqual(true);
    expect(shouldIncludeChatActivity("thinking", false)).toEqual(false);
    expect(shouldIncludeChatActivity("error", false)).toEqual(true);
  });

  it("commits partial beats into one final assistant reply", () => {
    expect(mergeFinalReply("Final answer", "", ["First update"], ["First update", "Second update"]))
      .toEqual("First update\n\nSecond update\n\nFinal answer");
  });

  it("does not duplicate partial text already included in the final response", () => {
    expect(mergeFinalReply("First update\n\nFinal answer", "", ["First update"]))
      .toEqual("First update\n\nFinal answer");
  });

  it("rejects finals that no longer own the active Session/Project", () => {
    expect(
      canApplyFinalToChat({
        activeThreadId: "s1",
        activeProjectId: "p1",
        ownedThreadId: "s1",
        ownedProjectId: "p1",
        streamOpen: true,
      })
    ).toEqual(true);
    expect(
      canApplyFinalToChat({
        activeThreadId: "s2",
        activeProjectId: "p1",
        ownedThreadId: "s1",
        ownedProjectId: "p1",
        streamOpen: true,
      })
    ).toEqual(false);
    expect(
      canApplyFinalToChat({
        activeThreadId: "s1",
        activeProjectId: "p2",
        ownedThreadId: "s1",
        ownedProjectId: "p1",
        streamOpen: true,
      })
    ).toEqual(false);
  });

  it("maps phases to concise operational headlines without chain-of-thought", () => {
    expect(buildLiveOperationalStatus({ phase: "thinking", streaming: true }).headline).toEqual("Thinking");
    expect(buildLiveOperationalStatus({ phase: "tool_search", activeToolName: "web_search" }).headline).toEqual(
      "Searching"
    );
    expect(
      buildLiveOperationalStatus({
        phase: "tool_generic",
        activeToolName: "weather_lookup",
        taskDescription: "Inspecting current weather data",
      })
    ).toEqual({
      headline: "Working on task",
      task: "Inspecting current weather data",
      tool: "weather_lookup",
      skill: undefined,
      search: undefined,
      verifying: undefined,
    });
    expect(
      buildLiveOperationalStatus({
        phase: "tool_generic",
        activeToolName: "weather_lookup",
      }).headline
    ).toEqual("Using tool");
    expect(buildLiveOperationalStatus({ phase: "streaming_reply" }).headline).toEqual("Responding");
    expect(
      buildLiveOperationalStatus({ phase: "tool_generic", activeToolName: "skill_live_research" }).headline
    ).toEqual("Running Skill");
  });
});
