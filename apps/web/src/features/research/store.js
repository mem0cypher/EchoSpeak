import { create } from "zustand";
export const useResearchStore = create((set) => ({
    runs: [],
    prependRun: (run) => set((state) => {
        const deduped = state.runs.filter((item) => item.id !== run.id);
        return { runs: [run, ...deduped].slice(0, 20) };
    }),
    replaceRuns: (runs) => set(() => {
        const seen = new Set();
        const next = [];
        for (const run of runs) {
            if (!run?.id || seen.has(run.id))
                continue;
            seen.add(run.id);
            next.push(run);
        }
        return { runs: next.slice(0, 20) };
    }),
    clearRuns: () => set({ runs: [] }),
}));
