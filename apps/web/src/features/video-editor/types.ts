export type Rational = { numerator: number; denominator: number };
export type RationalTime = { ticks: string; time_base: Rational };

export type MediaAsset = {
  id: string;
  name: string;
  kind: "video" | "audio" | "image" | "caption" | "unknown";
  project_relative_path: string;
  sha256: string;
  size_bytes: number;
  duration?: RationalTime | null;
};

export type VideoClip = {
  id: string;
  asset_id: string;
  name: string;
  timeline_start: RationalTime;
  source_in: RationalTime;
  duration: RationalTime;
  enabled: boolean;
  opacity: number;
  volume: number;
};

export type VideoTrack = {
  id: string;
  kind: string;
  name: string;
  order: number;
  locked: boolean;
  muted: boolean;
  clips: VideoClip[];
};

export type EditOperation = {
  schema_version: 1;
  id?: string;
  operation_type: "add_track" | "insert_clip" | "split_clip" | "trim_clip" | "move_clip" | "delete_clip";
  payload: Record<string, unknown>;
  expected_revision: number;
  source: "manual" | "agent";
};

export type VideoJob = {
  id: string;
  kind: string;
  adapter_id: string;
  status: string;
  progress: number;
  diagnostics: string[];
};

export type VideoDocument = {
  schema_version: 1;
  id: string;
  project_id: string;
  name: string;
  revision: number;
  head_revision_id: string;
  timeline: { id: string; name: string; tracks: VideoTrack[] };
  assets: MediaAsset[];
  generated_assets: MediaAsset[];
  jobs: VideoJob[];
  candidates: Array<{ id: string; name: string; status: string; preview_url?: string }>;
  undo_revision_ids: string[];
  redo_revision_ids: string[];
};

export type VideoAdapter = {
  adapter_id: string;
  display_name: string;
  location: string;
  available: boolean;
  operations: string[];
  license: string;
  notes: string[];
};

export const timeFromSeconds = (seconds: number): RationalTime => ({
  ticks: String(Math.max(0, Math.round(Number(seconds || 0) * 1000))),
  time_base: { numerator: 1, denominator: 1000 },
});

export const secondsFromTime = (value?: RationalTime | null): number => {
  if (!value) return 0;
  return Number(value.ticks || 0) * Number(value.time_base?.numerator || 1) / Number(value.time_base?.denominator || 1);
};
