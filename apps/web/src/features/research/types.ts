export type ResearchEvidence = {
  id: string;
  kind: string;
  position: number;
  query: string;
  title: string;
  url: string;
  domain: string;
  summary: string;
  snippet: string;
  content: string;
  page_title: string;
  published_raw: string;
  published_at?: string | null;
  recency_bucket: string;
};

export type ResearchRun = {
  id: string;
  tool?: string;
  at: number;
  query: string;
  mode: "general" | "recent";
  recency_intent: boolean;
  evidence_count: number;
  evidence: ResearchEvidence[];
};
