export type StudioNavigationKey = "ArrowLeft" | "ArrowRight" | "Home" | "End";

/** Canonical Studio section order — every tab must remain reachable via scroll. */
export const STUDIO_SECTION_ORDER = [
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
] as const;

export function nextStudioTabIndex(currentIndex: number, tabCount: number, key: string): number | null {
  if (!Number.isInteger(currentIndex) || tabCount <= 0) return null;
  if (key === "ArrowRight") return (currentIndex + 1) % tabCount;
  if (key === "ArrowLeft") return (currentIndex - 1 + tabCount) % tabCount;
  if (key === "Home") return 0;
  if (key === "End") return tabCount - 1;
  return null;
}
