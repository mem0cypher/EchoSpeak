import { describe, expect, it } from "vitest";
import {
  desktopExecutionProfile,
  desktopVisualizerPanelLabel,
  desktopWorkspaceForView,
  isDesktopContextualSurface,
} from "./workspaceState";

describe("desktop workspace owner", () => {
  it("exposes exactly two primary desktop surfaces", () => {
    expect([
      desktopWorkspaceForView("chat"),
      desktopWorkspaceForView("avatar"),
    ]).toEqual(["chat", "visualizer"]);
  });

  it("labels internal Visualizer panels without promoting them to surfaces", () => {
    expect(desktopVisualizerPanelLabel("ring")).toEqual("Echo Visualizer");
    expect(desktopVisualizerPanelLabel("work")).toEqual("Work");
    expect(desktopVisualizerPanelLabel("coding")).toEqual("Code");
  });

  it("keeps chat as the only full conversation surface", () => {
    expect(isDesktopContextualSurface("chat")).toEqual(false);
    expect(isDesktopContextualSurface("visualizer")).toEqual(true);
  });

  it("derives presentation profiles from the Visualizer panel", () => {
    expect(desktopExecutionProfile("chat")).toEqual("chat");
    expect(desktopExecutionProfile("visualizer")).toEqual("chat");
    expect(desktopExecutionProfile("visualizer", "work")).toEqual("work");
    expect(desktopExecutionProfile("visualizer", "research")).toEqual("work");
    expect(desktopExecutionProfile("visualizer", "media")).toEqual("work");
    expect(desktopExecutionProfile("visualizer", "tasks")).toEqual("work");
    expect(desktopExecutionProfile("visualizer", "coding")).toEqual("code");
  });
});
