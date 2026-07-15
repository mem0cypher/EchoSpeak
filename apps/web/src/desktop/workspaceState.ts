export type DesktopWorkspaceSurface =
  | "chat"
  | "visualizer"
  | "research"
  | "code"
  | "media"
  | "tasks"
  | "studio";

export type DesktopSidebarView = "chat" | "avatar" | "research" | "code" | "media" | "tasks" | "studio";

export const desktopWorkspaceForView = (view: DesktopSidebarView): DesktopWorkspaceSurface =>
  view === "avatar" ? "visualizer" : view;

export const desktopWorkspaceLabel = (surface: DesktopWorkspaceSurface): string => ({
  chat: "Conversation",
  visualizer: "Echo Visualizer",
  research: "Research",
  code: "Code",
  media: "Media",
  tasks: "Tasks",
  studio: "Studio",
})[surface];

export const desktopVisualizerMode = (
  surface: DesktopWorkspaceSurface,
): "ring" | "research" | "coding" | "tasks" | null => ({
  visualizer: "ring" as const,
  research: "research" as const,
  code: "coding" as const,
  tasks: "tasks" as const,
  chat: null,
  media: null,
  studio: null,
})[surface];

export const isDesktopContextualSurface = (surface: DesktopWorkspaceSurface): boolean => surface !== "chat";
