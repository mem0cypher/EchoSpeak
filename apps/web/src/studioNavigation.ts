export type StudioNavigationKey = "ArrowLeft" | "ArrowRight" | "Home" | "End";

/** Canonical Studio section order — every tab must remain reachable via scroll. */
export const STUDIO_SECTION_ORDER = [
  "settings",
  "search_settings",
  "services",
  "avatar_editor",
  "connections",
  "capabilities",
  "skills",
  "mcp_settings",
  "approvals",
  "memory",
  "docs",
  "soul",
  "overview",
  "projects",
  "executions",
  "automations",
  "system_services",
  "advanced_settings",
] as const;

export function nextStudioTabIndex(currentIndex: number, tabCount: number, key: string): number | null {
  if (!Number.isInteger(currentIndex) || tabCount <= 0) return null;
  if (key === "ArrowRight") return (currentIndex + 1) % tabCount;
  if (key === "ArrowLeft") return (currentIndex - 1 + tabCount) % tabCount;
  if (key === "Home") return 0;
  if (key === "End") return tabCount - 1;
  return null;
}
