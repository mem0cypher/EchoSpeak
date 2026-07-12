import { describe, expect, it } from "vitest";
import { defaultRuntimeLayout, loadRuntimeLayout, runtimeGridColumns, saveRuntimeLayout } from "./runtimeLayout";

describe("runtime layout", () => {
  it("supports independent sidebar and visualizer visibility", () => {
    expect(runtimeGridColumns({ ...defaultRuntimeLayout, visualizerVisible: false })).toEqual("252px minmax(0, 1fr)");
    expect(runtimeGridColumns({ ...defaultRuntimeLayout, sidebarVisible: false })).toEqual(
      "minmax(320px, 1.2fr) minmax(340px, 1fr)",
    );
    expect(runtimeGridColumns({ ...defaultRuntimeLayout, sidebarVisible: false, visualizerVisible: false })).toEqual(
      "minmax(0, 1fr)",
    );
    expect(runtimeGridColumns({ ...defaultRuntimeLayout, sidebarCollapsed: true })).toEqual(
      "56px minmax(320px, 1.2fr) minmax(340px, 1fr)",
    );
    expect(runtimeGridColumns(defaultRuntimeLayout)).toEqual(
      "252px minmax(320px, 1.2fr) minmax(340px, 1fr)",
    );
  });

  it("persists and restores layout", () => {
    let raw = "";
    const storage = { getItem: () => raw, setItem: (_key: string, value: string) => { raw = value; } };
    const expected = { ...defaultRuntimeLayout, sidebarCollapsed: true, visualizerDensity: "calm" as const };
    saveRuntimeLayout(storage, expected);
    expect(loadRuntimeLayout(storage)).toEqual(expected);
  });
});
