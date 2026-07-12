import type { ResearchRun } from "../research/types";

export type ResponseRenderBlockKind =
  | "section"
  | "cards"
  | "table"
  | "timeline"
  | "status"
  | "evidence"
  | "chart";

export type ResponseRenderCard = {
  title: string;
  value?: string;
  detail?: string;
};

export type ResponseRenderTable = {
  columns: string[];
  rows: Array<Array<string | number>>;
};

export type ResponseRenderChartPoint = {
  label: string;
  value: number;
};

export type ResponseRenderBlock =
  | {
      id: string;
      kind: "section";
      title: string;
      body: string;
    }
  | {
      id: string;
      kind: "cards";
      title?: string;
      cards: ResponseRenderCard[];
    }
  | {
      id: string;
      kind: "table";
      title?: string;
      table: ResponseRenderTable;
    }
  | {
      id: string;
      kind: "timeline";
      title?: string;
      items: { label: string; detail?: string; time?: string }[];
    }
  | {
      id: string;
      kind: "status";
      title?: string;
      status: "running" | "done" | "blocked" | "failed" | "info";
      items: { label: string; detail?: string; status?: "running" | "done" | "blocked" | "failed" | "info" }[];
    }
  | {
      id: string;
      kind: "evidence";
      title?: string;
      items: { title: string; url?: string; domain?: string; snippet?: string }[];
    }
  | {
      id: string;
      kind: "chart";
      title?: string;
      chart: {
        type: "bar";
        unit?: string;
        points: ResponseRenderChartPoint[];
      };
    };

export type ResponseRenderIntent = {
  blocks?: ResponseRenderBlock[];
};

export type ResponseRenderPlan = {
  summaryText: string;
  blocks: ResponseRenderBlock[];
};

export type BuildResponseRenderPlanInput = {
  answerText: string;
  intent?: ResponseRenderIntent | null;
  researchRuns?: ResearchRun[];
  searchQueries?: string[];
};
