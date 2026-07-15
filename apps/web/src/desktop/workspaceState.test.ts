import { describe, expect, it } from "vitest";
import { desktopVisualizerMode, desktopWorkspaceForView, isDesktopContextualSurface } from "./workspaceState";

describe("desktop workspace owner", () => {
  it("maps every sidebar view to exactly one primary surface", () => {
    expect([
      desktopWorkspaceForView("chat"),
      desktopWorkspaceForView("avatar"),
      desktopWorkspaceForView("research"),
      desktopWorkspaceForView("code"),
      desktopWorkspaceForView("media"),
      desktopWorkspaceForView("tasks"),
      desktopWorkspaceForView("studio"),
    ]).toEqual(["chat", "visualizer", "research", "code", "media", "tasks", "studio"]);
  });

  it("uses one visualizer mode only for visualizer-backed surfaces", () => {
    expect(desktopVisualizerMode("visualizer")).toEqual("ring");
    expect(desktopVisualizerMode("research")).toEqual("research");
    expect(desktopVisualizerMode("code")).toEqual("coding");
    expect(desktopVisualizerMode("media")).toEqual(null);
  });

  it("keeps chat as the only full conversation surface", () => {
    expect(isDesktopContextualSurface("chat")).toEqual(false);
    expect(isDesktopContextualSurface("studio")).toEqual(true);
  });
});
