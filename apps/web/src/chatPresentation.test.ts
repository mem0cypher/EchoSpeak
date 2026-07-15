import { describe, expect, it } from "vitest";
import { canApplyFinalToChat, mergeFinalReply, shouldPersistChatActivity } from "./chatPresentation";

describe("calm Chat presentation", () => {
  it("persists actionable errors but not internal operation rows", () => {
    expect(shouldPersistChatActivity("error")).toEqual(true);
    for (const kind of ["thinking", "tool", "memory", "task", "stage", "evidence"]) {
      expect(shouldPersistChatActivity(kind)).toEqual(false);
    }
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
});
