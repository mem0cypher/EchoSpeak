import React, { useEffect, useMemo, useState } from "react";
import type { MediaLibraryAsset, MediaLibraryResponse, MediaRuntimeJob, MediaRuntimeJobsResponse } from "./types";
import "./media.css";

const Preview: React.FC<{ apiBase: string; sessionId: string; asset: MediaLibraryAsset }> = ({ apiBase, sessionId, asset }) => {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let disposed = false;
    let objectUrl = "";
    const load = async () => {
      if (asset.status !== "ready" || !["image", "video", "audio"].includes(asset.media_kind)) return;
      try {
        const response = await fetch(`${apiBase}/media/assets/${encodeURIComponent(asset.id)}/content?session_id=${encodeURIComponent(sessionId)}`);
        if (!response.ok) return;
        objectUrl = URL.createObjectURL(await response.blob());
        if (!disposed) setUrl(objectUrl);
      } catch { /* metadata card remains truthful when preview transport is unavailable */ }
    };
    void load();
    return () => { disposed = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [apiBase, asset.id, asset.media_kind, asset.status, sessionId]);

  if (!url) return <div className="media-preview-placeholder"><span>{asset.media_kind}</span></div>;
  if (asset.media_kind === "image") return <img className="media-preview" src={url} alt={asset.name} />;
  if (asset.media_kind === "video") return <video className="media-preview" src={url} controls preload="metadata" />;
  return <div className="media-preview-placeholder"><audio src={url} controls preload="metadata" /></div>;
};

export const MediaLibraryView: React.FC<{
  apiBase: string;
  sessionId: string;
  projectId: string;
}> = ({ apiBase, sessionId, projectId }) => {
  const [assets, setAssets] = useState<MediaLibraryAsset[]>([]);
  const [jobs, setJobs] = useState<MediaRuntimeJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<"all" | "image" | "video" | "audio">("all");

  const refresh = async () => {
    if (!sessionId) { setAssets([]); setJobs([]); return; }
    setLoading(true); setError("");
    try {
      const query = new URLSearchParams({ session_id: sessionId, limit: "200" });
      if (projectId) query.set("project_id", projectId);
      const [assetResponse, generationResponse, voiceResponse] = await Promise.all([
        fetch(`${apiBase}/media/assets?${query}`),
        fetch(`${apiBase}/media-runtime/generation/jobs?${query}`),
        fetch(`${apiBase}/media-runtime/voice/jobs?${query}`),
      ]);
      if (!assetResponse.ok) throw new Error(`Media hydration failed (${assetResponse.status})`);
      const data = await assetResponse.json() as MediaLibraryResponse;
      setAssets(Array.isArray(data.items) ? data.items : []);
      const generation = generationResponse.ok ? await generationResponse.json() as MediaRuntimeJobsResponse : { items: [], count: 0 };
      const voice = voiceResponse.ok ? await voiceResponse.json() as MediaRuntimeJobsResponse : { items: [], count: 0 };
      setJobs([...generation.items, ...voice.items].sort((left, right) => right.created_at - left.created_at).slice(0, 12));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setLoading(false); }
  };

  useEffect(() => { void refresh(); }, [apiBase, projectId, sessionId]);
  const visible = useMemo(() => filter === "all" ? assets : assets.filter((item) => item.media_kind === filter), [assets, filter]);
  const activeJobs = useMemo(
    () => jobs.filter((j) => !["completed", "failed", "cancelled", "done"].includes(String(j.status || "").toLowerCase())),
    [jobs],
  );

  return (
    <section className="media-library" aria-label="Media library">
      <div className="media-toolbar">
        <div className="media-filters" role="group" aria-label="Media filters">
          {(["all", "image", "video", "audio"] as const).map((value) => (
            <button type="button" key={value} className={filter === value ? "is-active" : ""} onClick={() => setFilter(value)}>
              {value}
            </button>
          ))}
        </div>
        <div className="media-toolbar-meta">
          {visible.length > 0 ? <span className="media-count">{visible.length}</span> : null}
          <button type="button" onClick={() => void refresh()} disabled={loading} title="Refresh">
            {loading ? "…" : "↻"}
          </button>
        </div>
      </div>

      {activeJobs.length > 0 ? (
        <div className="media-jobs" aria-label="Active media jobs">
          {activeJobs.map((job) => (
            <article key={job.id} className={`media-job is-${job.status}`} title={job.error || job.id}>
              <div className="media-job-row">
                <strong>{job.kind || job.operation || "job"}</strong>
                <span>{job.status}{job.progress != null ? ` · ${Math.round(job.progress * 100)}%` : ""}</span>
              </div>
              <div className="media-job-progress"><i style={{ width: `${Math.max(0, Math.min(100, (job.progress || 0) * 100))}%` }} /></div>
            </article>
          ))}
        </div>
      ) : null}

      {error ? <div className="media-empty is-error">{error}</div> : null}
      {!error && !sessionId ? <div className="media-empty">Select a session to view media.</div> : null}
      {!error && sessionId && !loading && visible.length === 0 ? (
        <div className="media-empty">No media yet.</div>
      ) : null}

      <div className="media-grid">
        {visible.map((asset) => (
          <article className="media-card" key={asset.id}>
            <Preview apiBase={apiBase} sessionId={sessionId} asset={asset} />
            <div className="media-card-body">
              <div className="media-card-title">
                <strong title={asset.name}>{asset.name}</strong>
                <span>{asset.media_kind}</span>
              </div>
              {asset.prompt ? <p className="media-prompt">{asset.prompt}</p> : null}
              <div className="media-meta">
                <span>{Math.max(1, Math.round(asset.size_bytes / 1024))} KB</span>
                {asset.provider ? <span>{asset.provider}</span> : null}
              </div>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
};
