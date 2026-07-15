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
      setJobs([...generation.items, ...voice.items].sort((left, right) => right.created_at - left.created_at).slice(0, 20));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setLoading(false); }
  };

  useEffect(() => { void refresh(); }, [apiBase, projectId, sessionId]);
  const visible = useMemo(() => filter === "all" ? assets : assets.filter((item) => item.media_kind === filter), [assets, filter]);

  return (
    <section className="media-library" aria-label="Media library">
      <header className="media-library-header">
        <div><span className="media-kicker">Assets</span><h2>Media</h2><p>Immutable generated and imported sources owned by EchoSpeak.</p></div>
        <button type="button" onClick={() => void refresh()} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button>
      </header>
      <div className="media-filters" role="group" aria-label="Media filters">
        {(["all", "image", "video", "audio"] as const).map((value) => (
          <button type="button" key={value} className={filter === value ? "is-active" : ""} onClick={() => setFilter(value)}>{value}</button>
        ))}
      </div>
      {jobs.length > 0 ? (
        <section className="media-jobs" aria-label="Recent media jobs">
          <div className="media-jobs-title"><span>Recent jobs</span><span>{jobs.length}</span></div>
          <div className="media-jobs-list">
            {jobs.map((job) => (
              <article key={job.id} className={`media-job is-${job.status}`} title={job.error || job.id}>
                <div><strong>{job.kind || job.operation || "media"}</strong><span>{job.provider_id}{job.model ? ` · ${job.model}` : ""}</span></div>
                <div className="media-job-status"><span>{job.status}</span><span>{Math.round(job.progress * 100)}%</span></div>
                <div className="media-job-progress"><i style={{ width: `${Math.max(0, Math.min(100, job.progress * 100))}%` }} /></div>
              </article>
            ))}
          </div>
        </section>
      ) : null}
      {error ? <div className="media-empty is-error">{error}</div> : null}
      {!error && !sessionId ? <div className="media-empty">Select or create a Session to view its governed Media assets.</div> : null}
      {!error && sessionId && !loading && visible.length === 0 ? <div className="media-empty">No verified Media assets in this {projectId ? "Project" : "Session"} yet.</div> : null}
      <div className="media-grid">
        {visible.map((asset) => (
          <article className="media-card" key={asset.id}>
            <Preview apiBase={apiBase} sessionId={sessionId} asset={asset} />
            <div className="media-card-body">
              <div className="media-card-title"><strong title={asset.name}>{asset.name}</strong><span>{asset.source_kind}</span></div>
              <p>{asset.provider ? `${asset.provider}${asset.model ? ` · ${asset.model}` : ""}` : asset.project_relative_path}</p>
              {asset.prompt ? <p className="media-prompt">{asset.prompt}</p> : null}
              <div className="media-meta"><span>{asset.media_kind}</span><span>{Math.max(1, Math.round(asset.size_bytes / 1024))} KB</span><span>{new Date(asset.created_at * 1000).toLocaleString()}</span></div>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
};
