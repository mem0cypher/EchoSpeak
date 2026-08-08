export type DesktopWorkspaceSurface = "chat" | "visualizer";
export type DesktopSidebarView = "chat" | "avatar";
export type DesktopVisualizerPanel = "ring" | "work" | "research" | "coding" | "tasks" | "media";
export type DesktopExecutionProfile = "chat" | "work" | "code";

export const desktopWorkspaceForView = (view: DesktopSidebarView): DesktopWorkspaceSurface =>
  view === "avatar" ? "visualizer" : view;

export const desktopWorkspaceLabel = (surface: DesktopWorkspaceSurface): string => ({
  chat: "Conversation",
  visualizer: "Echo Visualizer",
})[surface];

export const desktopVisualizerPanelLabel = (panel: DesktopVisualizerPanel): string => ({
  ring: "Echo Visualizer",
  work: "Work",
  research: "Research",
  coding: "Code",
  tasks: "Checklist",
  media: "Media",
})[panel];

export const isDesktopContextualSurface = (surface: DesktopWorkspaceSurface): boolean => surface !== "chat";

export const desktopExecutionProfile = (
  surface: DesktopWorkspaceSurface,
  panel: DesktopVisualizerPanel = "ring",
): DesktopExecutionProfile => {
  if (surface === "chat" || panel === "ring") return "chat";
  if (panel === "coding") return "code";
  return "work";
};
