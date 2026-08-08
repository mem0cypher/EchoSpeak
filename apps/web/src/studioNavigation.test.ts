import { describe, expect, it } from "vitest";
import { nextStudioTabIndex, STUDIO_SECTION_ORDER } from "./studioNavigation";

describe("Studio keyboard navigation", () => {
  it("wraps arrow navigation without clipping a reachable tab", () => {
    expect(nextStudioTabIndex(13, 14, "ArrowRight")).toEqual(0);
    expect(nextStudioTabIndex(0, 14, "ArrowLeft")).toEqual(13);
  });

  it("supports Home and End", () => {
    expect(nextStudioTabIndex(6, 14, "Home")).toEqual(0);
    expect(nextStudioTabIndex(6, 14, "End")).toEqual(13);
  });

  it("does not consume unrelated keys or invalid tab sets", () => {
    expect(nextStudioTabIndex(2, 14, "Enter")).toEqual(null);
    expect(nextStudioTabIndex(0, 0, "ArrowRight")).toEqual(null);
  });

  it("lists every Studio section including trailing Automation tabs", () => {
    expect(STUDIO_SECTION_ORDER).toEqual([
      "overview",
      "skills",
      "memory",
      "docs",
      "settings",
      "capabilities",
      "soul",
      "avatar_editor",
      "approvals",
      "executions",
      "projects",
      "automations",
      "connections",
      "services",
    ]);
  });
});
