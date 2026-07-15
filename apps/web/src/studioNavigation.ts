export type StudioNavigationKey = "ArrowLeft" | "ArrowRight" | "Home" | "End";

export function nextStudioTabIndex(currentIndex: number, tabCount: number, key: string): number | null {
  if (!Number.isInteger(currentIndex) || tabCount <= 0) return null;
  if (key === "ArrowRight") return (currentIndex + 1) % tabCount;
  if (key === "ArrowLeft") return (currentIndex - 1 + tabCount) % tabCount;
  if (key === "Home") return 0;
  if (key === "End") return tabCount - 1;
  return null;
}
