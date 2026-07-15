import { describe, expect, it } from "vitest";
import { mergeFinalReply, shouldPersistChatActivity } from "./chatPresentation";

describe("calm Chat presentation", () => {
  it("persists actionable errors but not internal operation rows", () => {
    expect(shouldPersistChatActivity("error")).toBe(true);
    for (const kind of ["thinking", "tool", "memory", "task", "stage", "evidence"]) {
      expect(shouldPersistChatActivity(kind)).toBe(false);
    }
  });

  it("commits partial beats into one final assistant reply", () => {
    expect(mergeFinalReply("Final answer", "", ["First update"], ["First update", "Second update"]))
      .toBe("First update\n\nSecond update\n\nFinal answer");
  });

  it("does not duplicate partial text already included in the final response", () => {
    expect(mergeFinalReply("First update\n\nFinal answer", "", ["First update"]))
      .toBe("First update\n\nFinal answer");
  });
});
