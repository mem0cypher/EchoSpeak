import { describe, expect, it } from "vitest";
import { nextStudioTabIndex } from "./studioNavigation";

describe("Studio keyboard navigation", () => {
  it("wraps arrow navigation without clipping a reachable tab", () => {
    expect(nextStudioTabIndex(13, 14, "ArrowRight")).toBe(0);
    expect(nextStudioTabIndex(0, 14, "ArrowLeft")).toBe(13);
  });

  it("supports Home and End", () => {
    expect(nextStudioTabIndex(6, 14, "Home")).toBe(0);
    expect(nextStudioTabIndex(6, 14, "End")).toBe(13);
  });

  it("does not consume unrelated keys or invalid tab sets", () => {
    expect(nextStudioTabIndex(2, 14, "Enter")).toBeNull();
    expect(nextStudioTabIndex(0, 0, "ArrowRight")).toBeNull();
  });
});
