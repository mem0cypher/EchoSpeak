import { create } from "zustand";
import type { ResearchRun } from "./types";

type ResearchState = {
  runs: ResearchRun[];
  prependRun: (run: ResearchRun) => void;
  replaceRuns: (runs: ResearchRun[]) => void;
  clearRuns: () => void;
};

export const useResearchStore = create<ResearchState>((set) => ({
  runs: [],
  prependRun: (run) =>
    set((state) => {
      const deduped = state.runs.filter((item) => item.id !== run.id);
      return { runs: [run, ...deduped].slice(0, 20) };
    }),
  replaceRuns: (runs) =>
    set(() => {
      const seen = new Set<string>();
      const next: ResearchRun[] = [];
      for (const run of runs) {
        if (!run?.id || seen.has(run.id)) continue;
        seen.add(run.id);
        next.push(run);
      }
      return { runs: next.slice(0, 20) };
    }),
  clearRuns: () => set({ runs: [] }),
}));
