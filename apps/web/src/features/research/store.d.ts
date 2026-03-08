import type { ResearchRun } from "./types";
type ResearchState = {
    runs: ResearchRun[];
    prependRun: (run: ResearchRun) => void;
    replaceRuns: (runs: ResearchRun[]) => void;
    clearRuns: () => void;
};
export declare const useResearchStore: import("zustand").UseBoundStore<import("zustand").StoreApi<ResearchState>>;
export {};
