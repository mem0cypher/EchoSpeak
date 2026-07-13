/**
 * EchoSpeak Video workspace — same monochrome shell language as CodeWorkspace.
 * Lives in the visualizer column; main Echo chat stays on the right for agentic direction.
 *
 * Opening this view never creates Session / document / timeline.
 * Import: file picker + multi-select + drag-drop.
 * Generate / Export stay disabled until those pipelines ship.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  applyVideoOperations,
  createVideoDocument,
  decideVideoApproval,
  listVideoDocuments,
  loadVideoDocument,
  undoRedoVideo,
  uploadVideoAssets,
} from "./api.ts";
import type { EditOperation, MediaAsset, VideoClip, VideoDocument } from "./types.ts";
import { secondsFromTime, timeFromSeconds } from "./types.ts";
import {
  defaultVideoEditorLayout,
  formatTimecode,
  loadVideoEditorLayout,
  saveVideoEditorLayout,
  type VideoEditorPanelLayout,
} from "./videoEditorLayout.ts";

type Props = {
  apiBase: string;
  sessionId: string;
  projectId: string;
  projectName?: string;
  pendingApproval?: {
    has_pending?: boolean;
    approval_id?: string | null;
    action?: {
      id?: string;
      tool?: string;
      kwargs?: Record<string, unknown>;
    } | null;
  } | null;
  onApprovalChanged?(): void | Promise<unknown>;
};

type MediaTab = "all" | "video" | "audio" | "image" | "generated";

const mono = "'JetBrains Mono', 'SF Mono', Consolas, monospace";
const sans = "'Inter', 'Space Grotesk', system-ui, sans-serif";

/** EchoSpeak monochrome — match CodeWorkspace (white accent, soft panels). */
const c = {
  bg: "#000000",
  panel: "#0a0a0a",
  panel2: "#0f0f0f",
  elevate: "rgba(255,255,255,0.03)",
  line: "rgba(255,255,255,0.08)",
  lineSoft: "rgba(255,255,255,0.05)",
  text: "#f5f5f5",
  dim: "rgba(255,255,255,0.48)",
  mute: "rgba(255,255,255,0.28)",
  white: "#ffffff",
  danger: "#ff5555",
  playhead: "rgba(255,255,255,0.85)",
  trackVideo: "rgba(255,255,255,0.10)",
  trackAudio: "rgba(255,255,255,0.07)",
  clip: "rgba(255,255,255,0.14)",
  clipSel: "rgba(255,255,255,0.22)",
};

const ghostBtn = (active = false, disabled = false): React.CSSProperties => ({
  height: 28,
  padding: "0 11px",
  borderRadius: 3,
  border: `1px solid ${active ? "rgba(255,255,255,0.22)" : c.line}`,
  background: active ? "rgba(255,255,255,0.08)" : "transparent",
  color: disabled ? c.mute : active ? c.white : c.dim,
  fontSize: 11,
  fontWeight: 600,
  cursor: disabled ? "not-allowed" : "pointer",
  fontFamily: sans,
  letterSpacing: 0.2,
  opacity: disabled ? 0.45 : 1,
  whiteSpace: "nowrap",
});

function SoftPill({ children }: { children: React.ReactNode }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "2px 8px",
        borderRadius: 999,
        border: `1px solid ${c.line}`,
        background: c.elevate,
        color: c.dim,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: 0.4,
        textTransform: "uppercase",
        fontFamily: mono,
      }}
    >
      {children}
    </span>
  );
}

function usePanelLayout() {
  const [layout, setLayout] = useState<VideoEditorPanelLayout>(() =>
    typeof window !== "undefined" ? loadVideoEditorLayout(window.localStorage) : defaultVideoEditorLayout,
  );
  useEffect(() => {
    if (typeof window !== "undefined") saveVideoEditorLayout(window.localStorage, layout);
  }, [layout]);
  return [layout, setLayout] as const;
}

export function VideoEditorView({
  apiBase,
  sessionId,
  projectId,
  projectName,
  pendingApproval,
  onApprovalChanged,
}: Props) {
  const [layout, setLayout] = usePanelLayout();
  const [documents, setDocuments] = useState<VideoDocument[]>([]);
  const [document, setDocument] = useState<VideoDocument | null>(null);
  const [selectedAssetId, setSelectedAssetId] = useState("");
  const [selectedTrackId, setSelectedTrackId] = useState("");
  const [selectedClipId, setSelectedClipId] = useState("");
  const [playhead, setPlayhead] = useState(0);
  const [zoom, setZoom] = useState(36);
  const [snap, setSnap] = useState(true);
  const [mediaTab, setMediaTab] = useState<MediaTab>("all");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [dragOver, setDragOver] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const scopeKey = `${sessionId}\u0000${projectId}`;
  const scopeKeyRef = useRef(scopeKey);
  const documentIdRef = useRef("");
  const hydrateRequestRef = useRef(0);
  const mutationRequestRef = useRef(0);
  const busyRef = useRef(false);
  const mountedRef = useRef(true);

  const hasProject = Boolean(sessionId && projectId);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      hydrateRequestRef.current += 1;
      mutationRequestRef.current += 1;
      busyRef.current = false;
    };
  }, []);

  const scopeChanged = scopeKeyRef.current !== scopeKey;
  if (scopeChanged) {
    scopeKeyRef.current = scopeKey;
    documentIdRef.current = "";
    hydrateRequestRef.current += 1;
    mutationRequestRef.current += 1;
    busyRef.current = false;
  }
  if (!scopeChanged) documentIdRef.current = document?.id || "";

  const hydrate = useCallback(
    async (preferredDocumentId = "") => {
      const ownerScope = `${sessionId}\u0000${projectId}`;
      const requestId = ++hydrateRequestRef.current;
      if (!sessionId || !projectId) {
        if (mountedRef.current && scopeKeyRef.current === ownerScope && requestId === hydrateRequestRef.current) {
          setDocuments([]);
          setDocument(null);
        }
        return;
      }
      const items = await listVideoDocuments(apiBase, sessionId, projectId);
      if (!mountedRef.current || scopeKeyRef.current !== ownerScope || requestId !== hydrateRequestRef.current) return;
      setDocuments(items);
      const currentDocumentId = documentIdRef.current;
      let id =
        [preferredDocumentId, currentDocumentId].find((c) => c && items.some((item) => item.id === c)) || "";
      if (!id && items.length === 1) id = items[0].id;
      if (!id) {
        setDocument(null);
        documentIdRef.current = "";
        return;
      }
      const nextDocument = await loadVideoDocument(apiBase, sessionId, projectId, id);
      if (!mountedRef.current || scopeKeyRef.current !== ownerScope || requestId !== hydrateRequestRef.current) return;
      if (nextDocument.project_id !== projectId) return;
      documentIdRef.current = nextDocument.id;
      setDocument(nextDocument);
    },
    [apiBase, sessionId, projectId],
  );

  const runScopedMutation = async <T,>(
    work: () => Promise<T>,
    applyResult: (result: T, isCurrent: () => boolean) => void | Promise<void>,
  ) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setMessage("");
    const ownerScope = scopeKeyRef.current;
    const ownerDocumentId = documentIdRef.current;
    const requestId = ++mutationRequestRef.current;
    const isCurrent = () =>
      mountedRef.current &&
      scopeKeyRef.current === ownerScope &&
      requestId === mutationRequestRef.current &&
      (!ownerDocumentId || documentIdRef.current === ownerDocumentId || !documentIdRef.current);
    try {
      const result = await work();
      if (!isCurrent()) return;
      await applyResult(result, isCurrent);
    } catch (error) {
      if (isCurrent()) setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      if (mountedRef.current && scopeKeyRef.current === ownerScope && requestId === mutationRequestRef.current) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  };

  useEffect(() => {
    setDocuments([]);
    setDocument(null);
    documentIdRef.current = "";
    setSelectedAssetId("");
    setSelectedTrackId("");
    setSelectedClipId("");
    busyRef.current = false;
    setBusy(false);
    setMessage("");
    const ownerScope = `${sessionId}\u0000${projectId}`;
    void hydrate().catch((error) => {
      if (mountedRef.current && scopeKeyRef.current === ownerScope) setMessage(String(error));
    });
  }, [apiBase, sessionId, projectId, hydrate]);

  const assets = useMemo(
    () => (document ? [...document.assets, ...document.generated_assets] : []),
    [document],
  );
  const filteredAssets = useMemo(() => {
    if (mediaTab === "generated") return document?.generated_assets || [];
    const base = assets.filter((a) => !document?.generated_assets.some((g) => g.id === a.id));
    if (mediaTab === "all") return base;
    return base.filter((a) => a.kind === mediaTab);
  }, [assets, document, mediaTab]);

  const selectedAsset = assets.find((item) => item.id === selectedAssetId) || null;
  const selectedClipPair = useMemo(() => {
    if (!document) return null;
    for (const track of document.timeline.tracks) {
      const clip = track.clips.find((item) => item.id === selectedClipId);
      if (clip) return { track, clip };
    }
    return null;
  }, [document, selectedClipId]);

  const pendingVideoApprovalId = useMemo(() => {
    if (!document || !pendingApproval?.has_pending) return "";
    const action = pendingApproval.action;
    const kwargs = action?.kwargs || {};
    if (action?.tool !== "video_apply_transaction") return "";
    if (String(kwargs.session_id || "") !== sessionId) return "";
    if (String(kwargs.project_id || "") !== projectId) return "";
    if (String(kwargs.document_id || "") !== document.id) return "";
    return String(pendingApproval.approval_id || action.id || "");
  }, [document, pendingApproval, projectId, sessionId]);

  const ensureDocument = async (): Promise<VideoDocument | null> => {
    if (document) return document;
    if (!hasProject) {
      setMessage("Attach or create a Project to import media.");
      return null;
    }
    const created = await createVideoDocument(
      apiBase,
      sessionId,
      projectId,
      `${projectName || "Project"} Video`,
    );
    documentIdRef.current = created.id;
    setDocument(created);
    setDocuments((prev) => (prev.some((d) => d.id === created.id) ? prev : [...prev, created]));
    return created;
  };

  const ensureDefaultTracks = async (doc: VideoDocument): Promise<VideoDocument> => {
    if (doc.timeline.tracks.length > 0) return doc;
    const result = await applyVideoOperations(apiBase, sessionId, projectId, doc.id, [
      {
        schema_version: 1,
        operation_type: "add_track",
        payload: { track_id: "v1", kind: "video", name: "V1" },
        expected_revision: doc.revision,
        source: "manual",
      },
      {
        schema_version: 1,
        operation_type: "add_track",
        payload: { track_id: "a1", kind: "audio", name: "A1" },
        expected_revision: doc.revision,
        source: "manual",
      },
    ]);
    return result.document as VideoDocument;
  };

  const importFiles = async (fileList: FileList | File[]) => {
    const files = Array.from(fileList || []).filter(Boolean);
    if (!files.length) return;
    if (!hasProject) {
      setMessage("Attach or create a Project to import media.");
      return;
    }
    await runScopedMutation(
      async () => {
        let doc = await ensureDocument();
        if (!doc) throw new Error("Attach or create a Project to import media.");
        doc = await ensureDefaultTracks(doc);
        return uploadVideoAssets(apiBase, sessionId, projectId, doc.id, files);
      },
      (result) => {
        const next = result.document as VideoDocument;
        documentIdRef.current = next.id;
        setDocument(next);
        const first = Array.isArray(result.assets) && result.assets[0] ? result.assets[0].id : "";
        if (first) setSelectedAssetId(first);
        setMessage(`Imported ${result.count || files.length} file(s) into the media bin.`);
      },
    );
  };

  const createBlankTimeline = async () => {
    if (!hasProject) {
      setMessage("Attach or create a Project first.");
      return;
    }
    await runScopedMutation(
      async () => {
        let doc = await ensureDocument();
        if (!doc) throw new Error("Attach or create a Project first.");
        return ensureDefaultTracks(doc);
      },
      (doc) => {
        documentIdRef.current = doc.id;
        setDocument(doc);
        setMessage("Timeline ready (V1 / A1).");
      },
    );
  };

  const runOperation = async (
    operationType: EditOperation["operation_type"],
    payload: Record<string, unknown>,
  ) => {
    if (!document) return;
    const documentId = document.id;
    // Manual timeline ops apply immediately. Agent edits go through the right-side chat.
    const operation: EditOperation = {
      schema_version: 1,
      operation_type: operationType,
      payload,
      expected_revision: document.revision,
      source: "manual",
    };
    await runScopedMutation(
      () => applyVideoOperations(apiBase, sessionId, projectId, documentId, [operation]),
      (result) => {
        setDocument(result.document);
        documentIdRef.current = result.document.id;
        setMessage(`Applied ${operationType} · rev ${result.document.revision}`);
      },
    );
  };

  const applyUndoRedo = async (action: "undo" | "redo") => {
    if (!document) return;
    const documentId = document.id;
    await runScopedMutation(
      () => undoRedoVideo(apiBase, sessionId, projectId, documentId, action),
      (result) => {
        documentIdRef.current = result.id;
        setDocument(result);
        setMessage(`${action === "undo" ? "Undo" : "Redo"} · rev ${result.revision}`);
      },
    );
  };

  const decidePendingVideoApproval = async (decision: "confirm" | "cancel") => {
    if (!document || !pendingVideoApprovalId) return;
    const documentId = document.id;
    await runScopedMutation(
      () => decideVideoApproval(apiBase, sessionId, pendingVideoApprovalId, decision),
      async (result, isCurrent) => {
        await onApprovalChanged?.();
        if (!isCurrent()) return;
        if (decision === "confirm") {
          await hydrate(documentId);
          if (!isCurrent()) return;
          setMessage(String(result.response || "Approved edit applied."));
        } else {
          setMessage("Proposal canceled.");
        }
      },
    );
  };

  const insertSelectedAsset = async () => {
    if (!document || !selectedAsset) return;
    let trackId = selectedTrackId;
    if (!trackId) {
      const prefer = selectedAsset.kind === "audio" ? "audio" : "video";
      trackId =
        document.timeline.tracks.find((t) => t.kind === prefer)?.id || document.timeline.tracks[0]?.id || "";
    }
    if (!trackId) {
      setMessage("Create a blank timeline first.");
      return;
    }
    await runOperation("insert_clip", {
      track_id: trackId,
      asset_id: selectedAsset.id,
      timeline_start: timeFromSeconds(playhead),
      source_in: timeFromSeconds(0),
      duration: selectedAsset.duration || timeFromSeconds(5),
      name: selectedAsset.name,
    });
  };

  const startResize = (edge: "left" | "timeline", event: React.MouseEvent) => {
    event.preventDefault();
    const startX = event.clientX;
    const startY = event.clientY;
    const origin = { ...layout };
    const onMove = (ev: MouseEvent) => {
      if (edge === "left") {
        setLayout((L) => ({ ...L, leftWidth: Math.min(360, Math.max(180, origin.leftWidth + (ev.clientX - startX))) }));
      } else {
        setLayout((L) => ({
          ...L,
          timelineHeight: Math.min(420, Math.max(140, origin.timelineHeight - (ev.clientY - startY))),
        }));
      }
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const displayTracks =
    document && document.timeline.tracks.length
      ? document.timeline.tracks.map((t) => ({ id: t.id, name: t.name, kind: t.kind, clips: t.clips, phantom: false }))
      : [
          { id: "phantom-v1", name: "V1", kind: "video", clips: [] as VideoClip[], phantom: true },
          { id: "phantom-a1", name: "A1", kind: "audio", clips: [] as VideoClip[], phantom: true },
        ];

  const previewUrl =
    document && selectedAsset
      ? `${apiBase}/video/documents/${encodeURIComponent(document.id)}/assets/${encodeURIComponent(selectedAsset.id)}/content?session_id=${encodeURIComponent(sessionId)}&project_id=${encodeURIComponent(projectId)}`
      : "";

  const docLabel = document?.name || "Video workspace";
  const canUndo = Boolean(document?.undo_revision_ids?.length);
  const canRedo = Boolean(document?.redo_revision_ids?.length);

  return (
    <div
      data-testid="video-editor-shell"
      style={{
        height: "100%",
        minHeight: 0,
        display: "grid",
        gridTemplateRows: `34px minmax(0,1fr) 4px ${layout.timelineHeight}px`,
        background: c.bg,
        color: c.text,
        fontFamily: sans,
      }}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        if (e.dataTransfer.files?.length) void importFiles(e.dataTransfer.files);
      }}
    >
      {/* Toolbar — CodeWorkspace density */}
      <header
        data-testid="video-editor-toolbar"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "0 10px",
          borderBottom: `1px solid ${c.line}`,
          background: c.panel,
        }}
      >
        <SoftPill>Video</SoftPill>
        <span style={{ fontSize: 12, fontWeight: 650, letterSpacing: 0.2 }}>{docLabel}</span>
        {document && (
          <span style={{ fontSize: 10, color: c.mute, fontFamily: mono }}>rev {document.revision}</span>
        )}
        {projectName && <span style={{ fontSize: 10, color: c.mute }}>· {projectName}</span>}
        {!hasProject && (
          <span data-testid="video-no-project-banner" style={{ fontSize: 10, color: c.dim, marginLeft: 4 }}>
            Attach or create a Project to import media.
          </span>
        )}
        <div style={{ flex: 1 }} />
        <button
          type="button"
          data-testid="video-media-toggle"
          style={ghostBtn(layout.mediaBinVisible)}
          title={layout.mediaBinVisible ? "Hide media bin (widen preview)" : "Show media bin"}
          onClick={() => setLayout((L) => ({ ...L, mediaBinVisible: !L.mediaBinVisible }))}
        >
          {layout.mediaBinVisible ? "Hide media" : "Show media"}
        </button>
        <button type="button" style={ghostBtn(false, !canUndo || busy)} disabled={!canUndo || busy} onClick={() => void applyUndoRedo("undo")}>
          Undo
        </button>
        <button type="button" style={ghostBtn(false, !canRedo || busy)} disabled={!canRedo || busy} onClick={() => void applyUndoRedo("redo")}>
          Redo
        </button>
        <button
          type="button"
          data-testid="video-import-btn"
          style={ghostBtn(true, busy)}
          disabled={busy}
          onClick={() => {
            if (!hasProject) {
              setMessage("Attach or create a Project to import media.");
              return;
            }
            fileInputRef.current?.click();
          }}
        >
          Import
        </button>
        <button type="button" style={ghostBtn(false, true)} disabled title="Generation adapters not installed">
          Generate
        </button>
        <button type="button" style={ghostBtn(false, true)} disabled title="Export pipeline not available yet">
          Export
        </button>
        <input
          ref={fileInputRef}
          data-testid="video-file-input"
          type="file"
          multiple
          accept="video/*,audio/*,image/*"
          style={{ display: "none" }}
          onChange={(e) => {
            if (e.target.files?.length) void importFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </header>

      {/* Main: optional media bin | viewer (viewer expands when media is hidden) */}
      <div
        style={{
          minHeight: 0,
          display: "grid",
          gridTemplateColumns: layout.mediaBinVisible
            ? `${layout.leftWidth}px 4px minmax(0,1fr)`
            : "minmax(0,1fr)",
        }}
      >
        {layout.mediaBinVisible && (
          <>
            <aside
              data-testid="video-media-bin"
              style={{
                minHeight: 0,
                display: "grid",
                gridTemplateRows: "auto auto 1fr",
                background: c.panel,
                borderRight: `1px solid ${c.lineSoft}`,
              }}
            >
              <div style={{ ...sectionLabel, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <span>Media</span>
                <button
                  type="button"
                  data-testid="video-media-hide"
                  style={{ ...ghostBtn(false), height: 22, padding: "0 8px", fontSize: 10 }}
                  title="Hide media bin"
                  onClick={() => setLayout((L) => ({ ...L, mediaBinVisible: false }))}
                >
                  Hide
                </button>
              </div>
              <div style={{ display: "flex", gap: 4, padding: "6px 8px", borderBottom: `1px solid ${c.lineSoft}`, flexWrap: "wrap" }}>
                {([
                  ["all", "All"],
                  ["video", "Video"],
                  ["audio", "Audio"],
                  ["image", "Images"],
                  ["generated", "Gen"],
                ] as const).map(([id, label]) => (
                  <button key={id} type="button" style={ghostBtn(mediaTab === id)} onClick={() => setMediaTab(id)}>
                    {label}
                  </button>
                ))}
              </div>
              <div style={{ overflow: "auto", minHeight: 0 }}>
                {!filteredAssets.length ? (
                  <div data-testid="video-media-empty" style={emptyCard}>
                    No media yet — Import video, audio, or images.
                  </div>
                ) : (
                  filteredAssets.map((asset) => (
                    <button
                      key={asset.id}
                      type="button"
                      onClick={() => setSelectedAssetId(asset.id)}
                      onDoubleClick={() => {
                        setSelectedAssetId(asset.id);
                        void insertSelectedAsset();
                      }}
                      style={{
                        width: "100%",
                        border: 0,
                        borderBottom: `1px solid ${c.lineSoft}`,
                        background: asset.id === selectedAssetId ? "rgba(255,255,255,0.06)" : "transparent",
                        color: "inherit",
                        padding: "9px 10px",
                        textAlign: "left",
                        cursor: "pointer",
                      }}
                    >
                      <div style={{ fontSize: 11, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis" }}>{asset.name}</div>
                      <div style={{ fontSize: 9, color: c.mute, marginTop: 2, fontFamily: mono }}>
                        {asset.kind} · {(asset.size_bytes / 1048576).toFixed(1)}MB
                      </div>
                    </button>
                  ))
                )}
              </div>
            </aside>
            <div
              role="separator"
              aria-label="Resize media bin"
              onMouseDown={(e) => startResize("left", e)}
              style={{ cursor: "col-resize", background: c.lineSoft }}
            />
          </>
        )}

        <section style={{ minHeight: 0, display: "grid", gridTemplateRows: "minmax(0,1fr) auto", background: c.bg }}>
          <div data-testid="video-program-viewer" style={{ minHeight: 0, display: "grid", placeItems: "center", position: "relative", background: "#050505" }}>
            {selectedAsset?.kind === "video" && previewUrl ? (
              <video key={previewUrl} src={previewUrl} controls style={{ maxWidth: "100%", maxHeight: "100%" }} />
            ) : selectedAsset?.kind === "audio" && previewUrl ? (
              <audio key={previewUrl} src={previewUrl} controls />
            ) : selectedAsset?.kind === "image" && previewUrl ? (
              <img src={previewUrl} alt={selectedAsset.name} style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }} />
            ) : (
              <div data-testid="video-viewer-empty" style={{ color: c.mute, fontSize: 12, textAlign: "center", padding: 20 }}>
                No active timeline.
                <div style={{ fontSize: 11, marginTop: 6, color: c.mute }}>Import media or create a blank timeline. Use chat on the right for agent edits.</div>
              </div>
            )}
            {dragOver && (
              <div style={{ position: "absolute", inset: 10, border: `1px dashed rgba(255,255,255,0.35)`, borderRadius: 6, background: "rgba(255,255,255,0.04)", display: "grid", placeItems: "center", fontSize: 12, pointerEvents: "none" }}>
                Drop to import
              </div>
            )}
          </div>

          {/* Transport + clip tools only — agent chat is the main right panel */}
          <div style={{ borderTop: `1px solid ${c.line}`, background: c.panel }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", flexWrap: "wrap" }}>
              <button type="button" style={ghostBtn(false, true)} disabled title="Playback engine not available yet">
                ▶
              </button>
              <span data-testid="video-timecode" style={{ fontFamily: mono, fontSize: 11, color: c.white, minWidth: 48 }}>
                {formatTimecode(playhead)}
              </span>
              <input
                type="range"
                min={0}
                max={60}
                step={0.1}
                value={playhead}
                onChange={(e) => setPlayhead(Number(e.target.value || 0))}
                style={{ flex: 1, minWidth: 80, accentColor: "#fff" }}
                title="Playhead scrub (not full playback)"
              />
              <button type="button" style={ghostBtn(false, busy || !hasProject)} disabled={busy || !hasProject} onClick={() => void createBlankTimeline()}>
                Blank timeline
              </button>
              <button type="button" style={ghostBtn(false, !selectedAsset || !document || busy)} disabled={!selectedAsset || !document || busy} onClick={() => void insertSelectedAsset()}>
                Insert
              </button>
              {selectedClipPair && (
                <>
                  <button type="button" style={ghostBtn()} disabled={busy} onClick={() => void runOperation("split_clip", { clip_id: selectedClipId, at: timeFromSeconds(playhead) })}>
                    Split
                  </button>
                  <button type="button" style={{ ...ghostBtn(), color: c.danger }} disabled={busy} onClick={() => void runOperation("delete_clip", { clip_id: selectedClipId })}>
                    Delete
                  </button>
                </>
              )}
              {pendingVideoApprovalId && (
                <>
                  <button type="button" style={ghostBtn(true)} disabled={busy} onClick={() => void decidePendingVideoApproval("confirm")}>
                    Approve
                  </button>
                  <button type="button" style={ghostBtn()} disabled={busy} onClick={() => void decidePendingVideoApproval("cancel")}>
                    Cancel
                  </button>
                </>
              )}
            </div>
            {(message || busy) && (
              <div data-testid="video-status-bar" style={{ padding: "0 10px 6px", fontSize: 10, color: /fail|error|attach/i.test(message) ? c.danger : c.mute }}>
                {message || "Working…"}
              </div>
            )}
          </div>
        </section>
      </div>

      <div role="separator" onMouseDown={(e) => startResize("timeline", e)} style={{ cursor: "row-resize", background: c.lineSoft }} />

      {/* Timeline */}
      <section data-testid="video-timeline" style={{ minHeight: 0, display: "grid", gridTemplateRows: "auto 1fr", background: c.panel, borderTop: `1px solid ${c.line}` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 10px", borderBottom: `1px solid ${c.lineSoft}` }}>
          <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: c.dim }}>Timeline</span>
          <div style={{ flex: 1 }} />
          <label style={{ fontSize: 10, color: c.dim, display: "flex", alignItems: "center", gap: 6 }}>
            Zoom
            <input type="range" min={20} max={72} value={zoom} onChange={(e) => setZoom(Number(e.target.value))} style={{ width: 80, accentColor: "#fff" }} />
          </label>
          <label style={{ fontSize: 10, color: c.dim, display: "flex", alignItems: "center", gap: 5 }}>
            <input type="checkbox" checked={snap} onChange={(e) => setSnap(e.target.checked)} />
            Snap
          </label>
        </div>
        <div style={{ overflow: "auto", minHeight: 0, position: "relative" }}>
          <div
            style={{
              position: "sticky",
              top: 0,
              zIndex: 4,
              height: 20,
              paddingLeft: 88,
              borderBottom: `1px solid ${c.lineSoft}`,
              background: c.panel2,
              fontSize: 9,
              color: c.mute,
              fontFamily: mono,
              display: "flex",
              alignItems: "center",
            }}
          >
            {Array.from({ length: 13 }, (_, i) => (
              <span key={i} style={{ width: zoom * 5, flexShrink: 0 }}>
                {i * 5}s
              </span>
            ))}
          </div>
          {!document && (
            <div data-testid="video-timeline-empty" style={{ ...emptyCard, position: "absolute", inset: "28px 12px 12px", zIndex: 2, pointerEvents: "none" }}>
              Drop media here or create a blank timeline.
            </div>
          )}
          {displayTracks.map((track) => (
            <div
              key={track.id}
              style={{
                height: 46,
                display: "grid",
                gridTemplateColumns: "88px minmax(520px,1fr)",
                borderBottom: `1px solid ${c.lineSoft}`,
                opacity: track.phantom ? 0.65 : 1,
              }}
            >
              <button
                type="button"
                disabled={Boolean(track.phantom)}
                onClick={() => !track.phantom && setSelectedTrackId(track.id)}
                style={{
                  border: 0,
                  borderRight: `1px solid ${c.lineSoft}`,
                  background: track.id === selectedTrackId ? "rgba(255,255,255,0.06)" : c.panel2,
                  color: "inherit",
                  textAlign: "left",
                  padding: "6px 8px",
                  cursor: track.phantom ? "default" : "pointer",
                }}
              >
                <div style={{ fontSize: 11, fontWeight: 700 }}>{track.name}</div>
                <div style={{ fontSize: 8, color: c.mute, textTransform: "uppercase", fontFamily: mono }}>{track.kind}</div>
              </button>
              <div style={{ position: "relative", background: track.kind === "audio" ? "rgba(255,255,255,0.02)" : "transparent" }}>
                {track.clips.map((clip) => (
                  <ClipBlock
                    key={clip.id}
                    clip={clip}
                    zoom={zoom}
                    asset={assets.find((item) => item.id === clip.asset_id)}
                    selected={clip.id === selectedClipId}
                    onSelect={() => {
                      setSelectedTrackId(track.id);
                      setSelectedClipId(clip.id);
                    }}
                  />
                ))}
                <div
                  style={{
                    position: "absolute",
                    top: 0,
                    bottom: 0,
                    left: playhead * zoom,
                    width: 1,
                    background: c.playhead,
                    pointerEvents: "none",
                    zIndex: 3,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

const sectionLabel: React.CSSProperties = {
  padding: "8px 10px",
  borderBottom: `1px solid ${c.lineSoft}`,
  fontSize: 10,
  fontWeight: 700,
  letterSpacing: "0.1em",
  textTransform: "uppercase",
  color: c.dim,
};

const emptyCard: React.CSSProperties = {
  margin: 10,
  padding: "14px 12px",
  border: `1px dashed ${c.line}`,
  borderRadius: 4,
  fontSize: 11,
  lineHeight: 1.45,
  color: c.mute,
  textAlign: "center",
};

function ClipBlock({
  clip,
  asset,
  selected,
  zoom,
  onSelect,
}: {
  clip: VideoClip;
  asset?: MediaAsset;
  selected: boolean;
  zoom: number;
  onSelect(): void;
}) {
  const start = secondsFromTime(clip.timeline_start);
  const duration = Math.max(0.2, secondsFromTime(clip.duration));
  return (
    <button
      type="button"
      onClick={onSelect}
      title={`${asset?.name || clip.name} · ${duration.toFixed(2)}s`}
      style={{
        position: "absolute",
        left: start * zoom,
        top: 7,
        height: 32,
        width: Math.max(24, duration * zoom),
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
        border: `1px solid ${selected ? "rgba(255,255,255,0.45)" : "rgba(255,255,255,0.12)"}`,
        borderRadius: 3,
        background: selected ? c.clipSel : c.clip,
        color: c.text,
        fontSize: 9,
        textAlign: "left",
        padding: "0 6px",
        cursor: "pointer",
        fontFamily: sans,
      }}
    >
      {asset?.name || clip.name || clip.id.slice(0, 6)}
    </button>
  );
}
