export type RuntimeLayout = {
  sidebarVisible: boolean;
  sidebarCollapsed: boolean;
  visualizerVisible: boolean;
  visualizerDensity: "calm" | "normal" | "dense";
};

export const defaultRuntimeLayout: RuntimeLayout = {
  sidebarVisible: true,
  sidebarCollapsed: false,
  visualizerVisible: true,
  visualizerDensity: "normal",
};

export function loadRuntimeLayout(storage: Pick<Storage, "getItem"> | null): RuntimeLayout {
  if (!storage) return defaultRuntimeLayout;
  try {
    const value = JSON.parse(storage.getItem("echospeak.runtime_layout") || "{}");
    return {
      sidebarVisible: value.sidebarVisible !== false,
      sidebarCollapsed: value.sidebarCollapsed === true,
      visualizerVisible: value.visualizerVisible !== false,
      visualizerDensity: ["calm", "normal", "dense"].includes(value.visualizerDensity) ? value.visualizerDensity : "normal",
    };
  } catch {
    return defaultRuntimeLayout;
  }
}

export function saveRuntimeLayout(storage: Pick<Storage, "setItem"> | null, layout: RuntimeLayout): void {
  if (!storage) return;
  storage.setItem("echospeak.runtime_layout", JSON.stringify(layout));
}

/**
 * Column tracks in pre-zoom layout units.
 * Three-up: visualizer slightly larger for Echo, chat a bit wider than before —
 * neither dominates. Hidden sidebar/visualizer lets remaining columns expand.
 */
export function runtimeGridColumns(layout: RuntimeLayout): string {
  const columns: string[] = [];
  if (layout.sidebarVisible) columns.push(layout.sidebarCollapsed ? "56px" : "252px");
  if (layout.visualizerVisible) {
    // ~55/45 visualizer:chat when both free-grow; mins keep balance on narrow windows
    columns.push("minmax(320px, 1.2fr)");
    columns.push("minmax(340px, 1fr)");
  } else {
    columns.push("minmax(0, 1fr)");
  }
  return columns.join(" ");
}
