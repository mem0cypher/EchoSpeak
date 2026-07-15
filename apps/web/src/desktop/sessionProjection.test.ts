import { describe, expect, it } from "vitest";
import { canApplySessionHistory, ownsStreamCleanup } from "./sessionProjection";

describe("Session projection ownership", () => {
  const base = {
    activeSessionId: "A",
    targetSessionId: "A",
    currentRequestSeq: 2,
    requestSeq: 2,
    currentRevision: 4,
    startingRevision: 4,
    streamInFlight: false,
  };

  it("accepts only the current unchanged Session hydration", () => {
    expect(canApplySessionHistory(base)).toEqual(true);
    expect(canApplySessionHistory({ ...base, activeSessionId: "B" })).toEqual(false);
    expect(canApplySessionHistory({ ...base, currentRequestSeq: 3 })).toEqual(false);
    expect(canApplySessionHistory({ ...base, currentRevision: 5 })).toEqual(false);
    expect(canApplySessionHistory({ ...base, streamInFlight: true })).toEqual(false);
  });

  it("prevents an old same-Session stream from cleaning up its replacement", () => {
    const oldController = {};
    const newController = {};
    expect(ownsStreamCleanup(newController, oldController)).toEqual(false);
    expect(ownsStreamCleanup(newController, newController)).toEqual(true);
  });
});
