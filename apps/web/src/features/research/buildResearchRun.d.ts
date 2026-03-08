import type { ResearchRun } from "./types";
export declare const normalizeResearchRun: (value: unknown) => ResearchRun | null;
export declare const buildResearchRunFromToolEvent: (toolId: string, toolName: string, toolInput: string, output: string, at: number) => ResearchRun | null;
