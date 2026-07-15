export type MediaLibraryAsset = {
  schema_version: 1;
  id: string;
  project_id: string;
  session_id: string;
  document_id: string;
  name: string;
  media_kind: "image" | "video" | "audio" | "caption" | "unknown";
  source_kind: "imported" | "generated" | "rendered" | "proxy";
  project_relative_path: string;
  sha256: string;
  size_bytes: number;
  immutable: boolean;
  status: "ready" | "failed" | "cancelled";
  prompt: string;
  provider: string;
  model: string;
  settings: Record<string, unknown>;
  job_id: string;
  created_at: number;
};

export type MediaLibraryResponse = { items: MediaLibraryAsset[]; count: number };

export type MediaRuntimeJob = {
  id: string;
  session_id: string;
  project_id: string;
  status: "queued" | "running" | "completed" | "blocked" | "failed" | "cancelled";
  progress: number;
  provider_id: string;
  model: string;
  error: string;
  created_at: number;
  kind?: "image" | "video";
  operation?: "speech_to_text" | "text_to_speech" | "realtime";
  prompt?: string;
  text?: string;
};

export type MediaRuntimeJobsResponse = { items: MediaRuntimeJob[]; count: number };
