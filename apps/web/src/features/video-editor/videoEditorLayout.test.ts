import { describe, expect, it } from "vitest";

import {
  defaultVideoEditorLayout,
  formatTimecode,
  loadVideoEditorLayout,
  saveVideoEditorLayout,
} from "./videoEditorLayout.ts";

describe("videoEditorLayout", () => {
  it("loads defaults when storage is empty", () => {
    const storage = {
      getItem: () => null,
      setItem: () => undefined,
    };
    expect(loadVideoEditorLayout(storage)).toEqual(defaultVideoEditorLayout);
  });

  it("persists and reloads panel sizes", () => {
    const bag: Record<string, string> = {};
    const storage = {
      getItem: (k: string) => bag[k] ?? null,
      setItem: (k: string, v: string) => {
        bag[k] = v;
      },
    };
    saveVideoEditorLayout(storage, {
      leftWidth: 300,
      rightWidth: 320,
      timelineHeight: 280,
      mediaBinVisible: false,
    });
    expect(loadVideoEditorLayout(storage)).toEqual({
      leftWidth: 300,
      rightWidth: 320,
      timelineHeight: 280,
      mediaBinVisible: false,
    });
  });

  it("formats monochrome timeline timecode", () => {
    expect(formatTimecode(0)).toEqual("00:00");
    expect(formatTimecode(75)).toEqual("01:15");
    expect(formatTimecode(3661)).toEqual("01:01:01");
  });
});
