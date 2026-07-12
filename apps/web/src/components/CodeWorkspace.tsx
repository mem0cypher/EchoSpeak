/**
 * EchoSpeak Code workspace — Preview / Files / Terminal / Changes.
 * EchoSpeak monochrome identity (not red-tab IDE chrome). Logic talks to /code/* APIs.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { InlineCodeDiff } from "./InlineCodeDiff";
import type { CodeDiffSession } from "./InlineCodeDiff";

export type CodeFileNode = {
  name: string;
  path: string;
  type: "file" | "directory";
  size?: number;
  children?: CodeFileNode[];
  item_count?: number;
};

export type CodeDetection = {
  kind: string;
  label: string;
  entrypoints?: string[];
  preview_available?: boolean;
  preview_strategy?: string;
  preview_command?: string;
  preview_entry?: string;
  run_command_hint?: string;
  reason?: string;
  signals?: string[];
};

export type CodePreviewStatus = {
  running?: boolean;
  status?: string;
  url?: string;
  port?: number;
  error?: string;
  command?: string;
  entry?: string;
  pid?: number;
  project_root?: string;
};

export type CodeWorkspaceSnapshot = {
  ok: boolean;
  attached: boolean;
  thread_id: string;
  project_id?: string;
  project_name?: string;
  root?: string;
  display_name?: string;
  files: CodeFileNode[];
  detection: CodeDetection;
  preview: CodePreviewStatus;
  mode?: string;
  phase?: string;
  objective?: string;
  current_subject?: string;
  execution_status?: string;
  active_turn_id?: string;
  writable?: boolean;
  terminal_enabled?: boolean;
  message?: string;
};

export type LiveTerminalEntry = {
  id: string;
  command: string;
  output: string;
  status: string;
  exitCode?: number | null;
  running: boolean;
  at: number;
  turnId?: string;
  toolRunId?: string;
  mode?: string;
  reason?: string;
};

export type LiveFileChange = {
  id: string;
  path: string;
  toolName: string;
  action: string;
  status: string;
  summary?: string;
  at: number;
  turnId?: string;
  toolRunId?: string;
  verified?: boolean;
  originalContent?: string;
  currentContent?: string;
};

type WorkspaceTab = "preview" | "files" | "terminal" | "changes";

type Props = {
  apiBase: string;
  threadId: string;
  liveTerminal?: LiveTerminalEntry[];
  liveChanges?: LiveFileChange[];
  codeSessions?: CodeDiffSession[];
  pendingConfirmPath?: string;
  onConfirmSave?: () => void;
  onCancelSave?: () => void;
  refreshToken?: number;
};

const mono = "'JetBrains Mono', 'SF Mono', Consolas, monospace";
const sans = "'Inter', 'Space Grotesk', system-ui, sans-serif";

/** EchoSpeak shell palette — white accent, soft panels, no loud red chrome */
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
  ok: "rgba(255,255,255,0.78)",
  warn: "rgba(255,255,255,0.65)",
  danger: "#ff5555",
  termBg: "#050505",
  termChrome: "#0c0c0c",
  termGreen: "rgba(180, 255, 200, 0.85)",
  termMuted: "rgba(255,255,255,0.35)",
};

const formatSize = (bytes?: number): string => {
  if (bytes == null || !Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const formatTime = (ts?: number): string => {
  if (!ts) return "";
  const d = new Date(ts > 1e12 ? ts : ts * 1000);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
};

const basename = (p: string): string => {
  const s = String(p || "").replace(/\\/g, "/");
  const parts = s.split("/").filter(Boolean);
  return parts[parts.length - 1] || s || "file";
};

const fileGlyph = (name: string, isDir: boolean, expanded?: boolean): string => {
  if (isDir) return expanded ? "▾" : "▸";
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (["ts", "tsx", "js", "jsx", "mjs", "cjs"].includes(ext)) return "{ }";
  if (["py"].includes(ext)) return "py";
  if (["html", "htm", "css", "scss"].includes(ext)) return "<>";
  if (["json", "yml", "yaml", "toml"].includes(ext)) return "{}";
  if (["md", "txt"].includes(ext)) return "¶";
  if (["png", "jpg", "jpeg", "gif", "svg", "webp"].includes(ext)) return "▣";
  return "·";
};

const ghostBtn = (active = false): React.CSSProperties => ({
  height: 28,
  padding: "0 12px",
  borderRadius: 3,
  border: `1px solid ${active ? "rgba(255,255,255,0.22)" : c.line}`,
  background: active ? "rgba(255,255,255,0.08)" : "transparent",
  color: active ? c.white : c.dim,
  fontSize: 11,
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: sans,
  letterSpacing: 0.2,
});

function SoftPill({ children, tone = "neutral" }: { children: React.ReactNode; tone?: "neutral" | "live" | "soft" }) {
  const map = {
    neutral: { bg: "rgba(255,255,255,0.04)", border: c.line, color: c.dim },
    live: { bg: "rgba(255,255,255,0.08)", border: "rgba(255,255,255,0.18)", color: c.white },
    soft: { bg: "rgba(255,255,255,0.03)", border: c.lineSoft, color: c.mute },
  }[tone];
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "2px 8px",
        borderRadius: 999,
        border: `1px solid ${map.border}`,
        background: map.bg,
        color: map.color,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: 0.4,
        textTransform: "uppercase",
        fontFamily: mono,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

function TreeNode({
  node,
  depth,
  selectedPath,
  onOpen,
}: {
  node: CodeFileNode;
  depth: number;
  selectedPath: string;
  onOpen: (node: CodeFileNode) => void;
}) {
  const [expanded, setExpanded] = useState(depth < 1);
  const isDir = node.type === "directory";
  const selected = !isDir && selectedPath === node.path;

  return (
    <div>
      <div
        onClick={() => {
          if (isDir) setExpanded((v) => !v);
          else onOpen(node);
        }}
        onDoubleClick={() => {
          if (!isDir) onOpen(node);
        }}
        title={isDir ? node.path : "Open file"}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: `5px 12px 5px ${12 + depth * 14}px`,
          cursor: "pointer",
          fontSize: 12,
          fontFamily: mono,
          color: selected ? c.white : isDir ? "rgba(255,255,255,0.62)" : "rgba(255,255,255,0.78)",
          background: selected
            ? "linear-gradient(90deg, rgba(255,255,255,0.09), rgba(255,255,255,0.02) 70%, transparent)"
            : "transparent",
          borderLeft: selected ? "2px solid #fff" : "2px solid transparent",
          userSelect: "none",
          transition: "background 0.12s ease",
        }}
        onMouseEnter={(e) => {
          if (!selected) e.currentTarget.style.background = "rgba(255,255,255,0.035)";
        }}
        onMouseLeave={(e) => {
          if (!selected) e.currentTarget.style.background = "transparent";
        }}
      >
        <span
          style={{
            width: 18,
            textAlign: "center",
            fontSize: isDir ? 10 : 9,
            color: selected ? c.white : c.mute,
            flexShrink: 0,
            fontFamily: mono,
            opacity: isDir ? 1 : 0.7,
          }}
        >
          {fileGlyph(node.name, isDir, expanded)}
        </span>
        <span
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: 1,
            fontWeight: isDir || selected ? 600 : 400,
          }}
        >
          {node.name}
        </span>
        {!isDir && node.size != null ? (
          <span style={{ fontSize: 10, color: c.mute, flexShrink: 0 }}>{formatSize(node.size)}</span>
        ) : null}
        {isDir && node.item_count != null && node.item_count > 0 ? (
          <span style={{ fontSize: 10, color: c.mute, flexShrink: 0 }}>{node.item_count}</span>
        ) : null}
      </div>
      {isDir && expanded && node.children && node.children.length > 0 ? (
        <div>
          {node.children.map((child) => (
            <TreeNode key={child.path} node={child} depth={depth + 1} selectedPath={selectedPath} onOpen={onOpen} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function CodeFileViewer({
  path,
  name,
  content,
  error,
  loading,
  onClose,
}: {
  path: string;
  name: string;
  content: string;
  error?: string;
  loading?: boolean;
  onClose: () => void;
}) {
  const lines = useMemo(() => String(content || "").replace(/\r\n/g, "\n").split("\n"), [content]);
  const lineNoWidth = Math.max(2, String(lines.length).length);

  return (
    <div
      style={{
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        background: c.bg,
        borderLeft: `1px solid ${c.line}`,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
          padding: "8px 12px",
          borderBottom: `1px solid ${c.lineSoft}`,
          background: "linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.015))",
          flexShrink: 0,
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div style={{ fontFamily: mono, fontSize: 12, fontWeight: 600, color: c.white, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {name}
          </div>
          <div style={{ fontFamily: mono, fontSize: 10, color: c.mute, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {path}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          {!error && !loading ? (
            <span style={{ fontSize: 10, color: c.mute, fontFamily: mono }}>{lines.length} lines</span>
          ) : null}
          <button type="button" onClick={onClose} style={ghostBtn()}>
            Close
          </button>
        </div>
      </div>
      {loading ? (
        <div style={{ padding: 20, color: c.mute, fontSize: 12, fontFamily: mono }}>Reading…</div>
      ) : error ? (
        <div style={{ padding: 20, color: c.dim, fontSize: 12, fontFamily: mono }}>{error}</div>
      ) : (
        <div style={{ flex: 1, minHeight: 0, overflow: "auto", background: "#030303" }}>
          <div style={{ display: "grid", gridTemplateColumns: `${lineNoWidth + 2}ch minmax(0, 1fr)`, minWidth: "100%" }}>
            <div
              style={{
                padding: "12px 0 12px 10px",
                textAlign: "right",
                userSelect: "none",
                fontFamily: mono,
                fontSize: 11.5,
                lineHeight: 1.55,
                color: "rgba(255,255,255,0.18)",
                borderRight: `1px solid ${c.lineSoft}`,
                background: "rgba(255,255,255,0.015)",
              }}
            >
              {lines.map((_, i) => (
                <div key={i}>{i + 1}</div>
              ))}
            </div>
            <pre
              style={{
                margin: 0,
                padding: "12px 14px",
                fontFamily: mono,
                fontSize: 11.5,
                lineHeight: 1.55,
                color: "rgba(255,255,255,0.82)",
                whiteSpace: "pre",
                overflow: "visible",
              }}
            >
              {lines.map((line, i) => (
                <div key={i}>{line.length ? line : " "}</div>
              ))}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

export function CodeWorkspace({
  apiBase,
  threadId,
  liveTerminal = [],
  liveChanges = [],
  codeSessions = [],
  pendingConfirmPath,
  onConfirmSave,
  onCancelSave,
  refreshToken = 0,
}: Props) {
  const [tab, setTab] = useState<WorkspaceTab>("preview");
  const [snapshot, setSnapshot] = useState<CodeWorkspaceSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activity, setActivity] = useState<{
    terminal: any[];
    changes: any[];
    checkpoints: any[];
  }>({ terminal: [], changes: [], checkpoints: [] });
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewFrameKey, setPreviewFrameKey] = useState(0);
  const [openFile, setOpenFile] = useState<{
    path: string;
    name: string;
    content: string;
    binary?: boolean;
    error?: string;
  } | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [selectedChangeId, setSelectedChangeId] = useState<string | null>(null);
  const [diffSession, setDiffSession] = useState<CodeDiffSession | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const termEndRef = useRef<HTMLDivElement | null>(null);
  const lastThreadRef = useRef<string>("");

  const tid = String(threadId || "").trim() || "default";

  const fetchWorkspace = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/code/workspace?thread_id=${encodeURIComponent(tid)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as CodeWorkspaceSnapshot;
      setSnapshot(data);
    } catch (e: any) {
      setError(e?.message || "Failed to load workspace");
    } finally {
      setLoading(false);
    }
  }, [apiBase, tid]);

  const fetchActivity = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/code/activity?thread_id=${encodeURIComponent(tid)}&limit=80`);
      if (!res.ok) return;
      const data = await res.json();
      setActivity({
        terminal: Array.isArray(data.terminal) ? data.terminal : [],
        changes: Array.isArray(data.changes) ? data.changes : [],
        checkpoints: Array.isArray(data.checkpoints) ? data.checkpoints : [],
      });
    } catch {
      /* ignore poll errors */
    }
  }, [apiBase, tid]);

  useEffect(() => {
    if (lastThreadRef.current && lastThreadRef.current !== tid) {
      setOpenFile(null);
      setDiffSession(null);
      setSelectedChangeId(null);
      setTab("preview");
    }
    lastThreadRef.current = tid;
    void fetchWorkspace();
    void fetchActivity();
  }, [tid, fetchWorkspace, fetchActivity, refreshToken]);

  useEffect(() => {
    const id = window.setInterval(() => {
      void fetchActivity();
      void fetch(`${apiBase}/code/preview?thread_id=${encodeURIComponent(tid)}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((p) => {
          if (!p) return;
          setSnapshot((prev) => (prev ? { ...prev, preview: p } : prev));
        })
        .catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(id);
  }, [apiBase, tid, fetchActivity]);

  useEffect(() => {
    if (!liveChanges.length) return;
    const last = liveChanges[0];
    if (last && (last.action === "modify" || last.toolName === "file_write") && last.verified !== false) {
      setPreviewFrameKey((k) => k + 1);
      void fetchWorkspace();
      void fetchActivity();
    }
  }, [liveChanges, fetchWorkspace, fetchActivity]);

  useEffect(() => {
    if (tab === "terminal") {
      termEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [liveTerminal, activity.terminal, tab]);

  const openProjectFile = useCallback(
    async (path: string) => {
      setFileLoading(true);
      setTab("files");
      try {
        const res = await fetch(
          `${apiBase}/code/file?thread_id=${encodeURIComponent(tid)}&path=${encodeURIComponent(path)}`,
        );
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          setOpenFile({
            path,
            name: basename(path),
            content: "",
            error: data.detail || `Failed to open (${res.status})`,
          });
          return;
        }
        if (data.binary) {
          setOpenFile({
            path: data.path || path,
            name: data.name || basename(path),
            content: "",
            binary: true,
            error: "Binary file — contents not shown.",
          });
          return;
        }
        setOpenFile({
          path: data.path || path,
          name: data.name || basename(path),
          content: String(data.content || ""),
        });
      } catch (e: any) {
        setOpenFile({
          path,
          name: basename(path),
          content: "",
          error: e?.message || "Failed to open file",
        });
      } finally {
        setFileLoading(false);
      }
    },
    [apiBase, tid],
  );

  const startPreview = async () => {
    setPreviewBusy(true);
    try {
      const res = await fetch(`${apiBase}/code/preview/start?thread_id=${encodeURIComponent(tid)}`, {
        method: "POST",
      });
      const data = await res.json().catch(() => ({}));
      setSnapshot((prev) =>
        prev
          ? {
              ...prev,
              preview: {
                running: Boolean(data.running),
                status: data.status,
                url: data.url,
                port: data.port,
                error: data.error,
                command: data.command,
                entry: data.entry,
              },
              detection: data.detection || prev.detection,
            }
          : prev,
      );
      if (data.running) {
        setPreviewFrameKey((k) => k + 1);
        setTab("preview");
      }
    } finally {
      setPreviewBusy(false);
    }
  };

  const stopPreview = async () => {
    setPreviewBusy(true);
    try {
      await fetch(`${apiBase}/code/preview/stop?thread_id=${encodeURIComponent(tid)}`, { method: "POST" });
      setSnapshot((prev) =>
        prev
          ? {
              ...prev,
              preview: { running: false, status: "stopped", url: "", port: 0, error: "", command: "" },
            }
          : prev,
      );
    } finally {
      setPreviewBusy(false);
    }
  };

  const loadChangeDiff = async (change: any) => {
    const path = String(change.path || change.original_path || "").trim();
    setSelectedChangeId(String(change.id || change.tool_run_id || path));
    if (!path) {
      setDiffSession(null);
      return;
    }
    const live = liveChanges.find((x) => x.id === change.id || x.path === path);
    const sessionMatch =
      codeSessions.find((s) => s.filename === path || basename(s.filename) === basename(path)) || null;
    if (sessionMatch && (sessionMatch.originalContent !== sessionMatch.currentContent || sessionMatch.status !== "read")) {
      setDiffSession({
        ...sessionMatch,
        pendingConfirmation: Boolean(
          pendingConfirmPath &&
            (pendingConfirmPath === sessionMatch.filename ||
              basename(pendingConfirmPath) === basename(sessionMatch.filename)),
        ),
      });
      return;
    }
    if (live?.originalContent != null && live?.currentContent != null) {
      setDiffSession({
        filename: path,
        language: "text",
        originalContent: live.originalContent,
        currentContent: live.currentContent,
        status: live.action === "inspect" ? "read" : "saved",
        summary: live.summary,
      });
      return;
    }
    setDiffLoading(true);
    try {
      const res = await fetch(
        `${apiBase}/code/diff?thread_id=${encodeURIComponent(tid)}&path=${encodeURIComponent(path)}`,
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setDiffSession({
          filename: path,
          language: "text",
          originalContent: "",
          currentContent: data.detail || "Diff unavailable",
          status: "output",
          summary: "Could not load diff",
        });
        return;
      }
      setDiffSession({
        filename: path,
        language: "text",
        originalContent: String(data.original || ""),
        currentContent: String(data.current || ""),
        status: data.has_checkpoint ? "saved" : "read",
        summary: data.message || (data.has_checkpoint ? "Checkpoint → current" : "Current file"),
      });
    } finally {
      setDiffLoading(false);
    }
  };

  const mergedTerminal = useMemo(() => {
    const byId = new Map<string, LiveTerminalEntry>();
    for (const t of [...activity.terminal].reverse()) {
      const id = String(t.id || t.tool_run_id || "");
      byId.set(id, {
        id,
        command: String(t.command || ""),
        output: String(t.output || t.raw_output || ""),
        status: String(t.status || ""),
        exitCode: t.exit_code ?? null,
        running: false,
        at: Number(t.created_at || t.completed_at || 0),
        turnId: t.turn_id,
        toolRunId: t.tool_run_id || id,
        mode: t.mode,
        reason: t.reason,
      });
    }
    for (const t of liveTerminal) {
      byId.set(t.id, t);
    }
    return Array.from(byId.values()).sort((a, b) => (a.at || 0) - (b.at || 0));
  }, [liveTerminal, activity.terminal]);

  const mergedChanges = useMemo(() => {
    const list: Array<LiveFileChange & { fromLive?: boolean }> = [];
    const seen = new Set<string>();
    for (const ch of liveChanges) {
      const key = ch.id || `${ch.path}:${ch.at}`;
      seen.add(key);
      list.push({ ...ch, fromLive: true });
    }
    for (const ch of activity.changes) {
      const key = String(ch.id || ch.tool_run_id || "");
      if (seen.has(key)) continue;
      list.push({
        id: key,
        path: String(ch.path || ""),
        toolName: String(ch.tool_name || ""),
        action: String(ch.action || ""),
        status: String(ch.status || ""),
        summary: String(ch.summary || ""),
        at: Number(ch.created_at || 0),
        turnId: ch.turn_id,
        toolRunId: ch.tool_run_id || key,
        verified: Boolean(ch.verified),
      });
    }
    return list;
  }, [liveChanges, activity.changes]);

  const detection = snapshot?.detection;
  const preview = snapshot?.preview || {};
  const attached = Boolean(snapshot?.attached && snapshot?.root);
  const previewRunning = Boolean(preview.running && preview.url);

  const tabs: { id: WorkspaceTab; label: string }[] = [
    { id: "preview", label: "Preview" },
    { id: "files", label: "Files" },
    { id: "terminal", label: "Terminal" },
    { id: "changes", label: "Changes" },
  ];

  const pendingSession = useMemo(() => {
    if (!pendingConfirmPath) return null;
    return (
      codeSessions.find(
        (s) =>
          s.filename === pendingConfirmPath || basename(s.filename) === basename(pendingConfirmPath),
      ) || null
    );
  }, [codeSessions, pendingConfirmPath]);

  const emptyCenter = (title: string, body: string, action?: React.ReactNode) => (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 28,
        background:
          "radial-gradient(ellipse 70% 50% at 50% 40%, rgba(255,255,255,0.035), transparent 60%)",
      }}
    >
      <div style={{ maxWidth: 380, textAlign: "center" }}>
        <div
          style={{
            width: 36,
            height: 36,
            margin: "0 auto 14px",
            borderRadius: 3,
            border: `1px solid ${c.line}`,
            background: c.elevate,
          }}
        />
        <div style={{ fontSize: 14, fontWeight: 650, color: c.white, letterSpacing: -0.2 }}>{title}</div>
        <div style={{ marginTop: 8, fontSize: 12, color: c.dim, lineHeight: 1.55 }}>{body}</div>
        {action ? <div style={{ marginTop: 16, display: "flex", justifyContent: "center", gap: 8 }}>{action}</div> : null}
      </div>
    </div>
  );

  return (
    <div
      className="code-workspace"
      style={{
        width: "100%",
        height: "100%",
        maxHeight: "100%",
        minHeight: 0,
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        background: c.bg,
        color: c.text,
        fontFamily: sans,
        boxSizing: "border-box",
      }}
    >
      {/* Project header — soft, like prior workspace header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          padding: "10px 14px",
          borderBottom: `1px solid ${c.lineSoft}`,
          background: "rgba(255,255,255,0.02)",
          flexShrink: 0,
        }}
      >
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: -0.2, color: c.white }}>
              {snapshot?.display_name || snapshot?.project_name || "Code"}
            </span>
            {snapshot?.mode ? <SoftPill>{snapshot.mode}{snapshot.phase ? ` · ${snapshot.phase}` : ""}</SoftPill> : null}
            {previewRunning ? <SoftPill tone="live">live</SoftPill> : null}
          </div>
          <div
            style={{
              marginTop: 3,
              fontSize: 11,
              color: c.mute,
              fontFamily: mono,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            title={snapshot?.root || ""}
          >
            {attached
              ? snapshot?.root
              : snapshot?.message || "Attach a Project to this session to open the workspace."}
          </div>
        </div>
        <button
          type="button"
          onClick={() => {
            void fetchWorkspace();
            void fetchActivity();
            if (previewRunning) setPreviewFrameKey((k) => k + 1);
          }}
          style={ghostBtn()}
        >
          {loading ? "…" : "Refresh"}
        </button>
      </div>

      {/* Tabs — white active underline, no red chrome */}
      <div
        style={{
          display: "flex",
          gap: 2,
          padding: "0 10px",
          borderBottom: `1px solid ${c.lineSoft}`,
          background: c.panel,
          flexShrink: 0,
        }}
      >
        {tabs.map((t) => {
          const active = tab === t.id;
          const badge =
            t.id === "terminal"
              ? mergedTerminal.filter((x) => x.running).length || (mergedTerminal.length ? mergedTerminal.length : 0)
              : t.id === "changes"
                ? mergedChanges.length
                : 0;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              style={{
                padding: "11px 14px 10px",
                border: "none",
                borderBottom: active ? "2px solid #fff" : "2px solid transparent",
                marginBottom: -1,
                background: "transparent",
                color: active ? c.white : c.dim,
                fontSize: 12,
                fontWeight: active ? 650 : 500,
                cursor: "pointer",
                letterSpacing: 0.15,
                transition: "color 0.15s ease",
              }}
            >
              {t.label}
              {badge > 0 && (t.id === "terminal" || t.id === "changes") ? (
                <span style={{ marginLeft: 6, color: c.mute, fontFamily: mono, fontSize: 10 }}>{badge}</span>
              ) : null}
            </button>
          );
        })}
      </div>

      {error ? (
        <div
          style={{
            padding: "8px 14px",
            color: c.dim,
            fontSize: 12,
            borderBottom: `1px solid ${c.lineSoft}`,
            background: "rgba(255,255,255,0.02)",
          }}
        >
          {error}
        </div>
      ) : null}

      <div style={{ flex: 1, minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        {!attached && !loading
          ? emptyCenter(
              "No project attached",
              "Add a folder from the sidebar so Echo builds against a real Project root. Files, Terminal, Preview, and Changes stay scoped to that project and this session.",
            )
          : null}

        {/* ── PREVIEW ── */}
        {attached && tab === "preview" ? (
          <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
            {previewRunning && preview.url ? (
              <>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 10,
                    padding: "8px 14px",
                    borderBottom: `1px solid ${c.lineSoft}`,
                    background: c.panel2,
                    flexShrink: 0,
                  }}
                >
                  <div style={{ minWidth: 0, fontFamily: mono, fontSize: 11, color: c.mute, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {preview.url}
                    {preview.command ? `  ·  ${preview.command}` : ""}
                  </div>
                  <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                    <button type="button" onClick={() => setPreviewFrameKey((k) => k + 1)} style={ghostBtn()}>
                      Reload
                    </button>
                    <button type="button" onClick={() => void stopPreview()} disabled={previewBusy} style={ghostBtn()}>
                      Stop
                    </button>
                  </div>
                </div>
                {preview.error ? (
                  <div style={{ padding: "8px 14px", fontSize: 12, color: c.dim, borderBottom: `1px solid ${c.lineSoft}` }}>
                    {preview.error}
                  </div>
                ) : null}
                <iframe
                  key={previewFrameKey}
                  title="Project preview"
                  src={preview.url}
                  style={{ flex: 1, width: "100%", border: "none", background: "#111", minHeight: 0 }}
                />
              </>
            ) : detection?.preview_available ? (
              emptyCenter(
                "Preview ready",
                detection.reason || "This project can run a local preview from the attached folder.",
                <button type="button" onClick={() => void startPreview()} disabled={previewBusy} style={ghostBtn(true)}>
                  {previewBusy ? "Starting…" : "Start preview"}
                </button>,
              )
            ) : (
              emptyCenter(
                "No preview",
                detection?.reason ||
                  "Nothing visual to embed for this project. Use Files, Terminal, and Changes — they still track real activity.",
                detection?.run_command_hint ? (
                  <span style={{ fontFamily: mono, fontSize: 11, color: c.mute }}>{detection.run_command_hint}</span>
                ) : undefined,
              )
            )}
          </div>
        ) : null}

        {/* ── FILES ── */}
        {attached && tab === "files" ? (
          <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
            <div
              style={{
                width: openFile ? "38%" : "100%",
                minWidth: 168,
                maxWidth: openFile ? 340 : undefined,
                display: "flex",
                flexDirection: "column",
                background: c.panel,
                borderRight: openFile ? `1px solid ${c.line}` : "none",
                minHeight: 0,
              }}
            >
              <div
                style={{
                  padding: "10px 14px 8px",
                  borderBottom: `1px solid ${c.lineSoft}`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 8,
                  flexShrink: 0,
                }}
              >
                <div>
                  <div style={{ fontSize: 11, fontWeight: 700, color: c.white, letterSpacing: 0.2 }}>Project files</div>
                  <div style={{ fontSize: 10, color: c.mute, marginTop: 2 }}>Click a file to open</div>
                </div>
                {snapshot?.writable ? <SoftPill tone="soft">write</SoftPill> : null}
              </div>
              <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: "6px 0" }}>
                {loading && !snapshot?.files?.length ? (
                  <div style={{ padding: 20, color: c.mute, fontSize: 12, fontFamily: mono }}>Loading…</div>
                ) : snapshot?.files?.length ? (
                  snapshot.files.map((n) => (
                    <TreeNode
                      key={n.path}
                      node={n}
                      depth={0}
                      selectedPath={openFile?.path || ""}
                      onOpen={(node) => void openProjectFile(node.path)}
                    />
                  ))
                ) : (
                  <div style={{ padding: 20, color: c.mute, fontSize: 12 }}>No files in project root.</div>
                )}
              </div>
            </div>
            {openFile ? (
              <CodeFileViewer
                path={openFile.path}
                name={openFile.name}
                content={openFile.content}
                error={openFile.error}
                loading={fileLoading}
                onClose={() => setOpenFile(null)}
              />
            ) : null}
          </div>
        ) : null}

        {/* ── TERMINAL ── */}
        {attached && tab === "terminal" ? (
          <div
            style={{
              flex: 1,
              minHeight: 0,
              display: "flex",
              flexDirection: "column",
              background: c.termBg,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "8px 12px",
                background: c.termChrome,
                borderBottom: `1px solid ${c.lineSoft}`,
                flexShrink: 0,
              }}
            >
              <span style={{ width: 8, height: 8, borderRadius: "50%", background: "rgba(255,255,255,0.12)" }} />
              <span style={{ width: 8, height: 8, borderRadius: "50%", background: "rgba(255,255,255,0.12)" }} />
              <span style={{ width: 8, height: 8, borderRadius: "50%", background: "rgba(255,255,255,0.12)" }} />
              <span style={{ marginLeft: 8, fontFamily: mono, fontSize: 11, color: c.termMuted }}>
                echo · terminal
                {!snapshot?.terminal_enabled ? " · disabled in settings" : ""}
              </span>
            </div>
            <div
              style={{
                flex: 1,
                minHeight: 0,
                overflow: "auto",
                padding: "12px 14px 18px",
                fontFamily: mono,
                fontSize: 12,
                lineHeight: 1.5,
              }}
            >
              {mergedTerminal.length === 0 ? (
                <div style={{ color: c.termMuted, paddingTop: 8 }}>
                  <div style={{ color: c.termGreen }}>$</div>
                  <div style={{ marginTop: 10 }}>
                    Waiting for Echo to run a command in this session.
                    <br />
                    Real <span style={{ color: "rgba(255,255,255,0.55)" }}>terminal_run</span> output appears here — nothing simulated.
                  </div>
                </div>
              ) : (
                mergedTerminal.map((entry) => {
                  const failed =
                    !entry.running &&
                    ((entry.exitCode != null && entry.exitCode !== 0) ||
                      entry.status === "fail" ||
                      entry.status === "failed" ||
                      entry.status === "timeout");
                  return (
                    <div key={entry.id} style={{ marginBottom: 18 }}>
                      <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                        <span style={{ color: c.termGreen, flexShrink: 0 }}>$</span>
                        <span style={{ color: c.white, wordBreak: "break-all" }}>{entry.command || "(command)"}</span>
                        <span style={{ fontSize: 10, color: c.termMuted, marginLeft: "auto" }}>
                          {entry.running
                            ? "running…"
                            : entry.exitCode != null
                              ? `exit ${entry.exitCode}`
                              : entry.status || "done"}
                          {entry.at ? ` · ${formatTime(entry.at)}` : ""}
                        </span>
                      </div>
                      {(entry.output || entry.reason) && (
                        <pre
                          style={{
                            margin: "6px 0 0",
                            padding: 0,
                            whiteSpace: "pre-wrap",
                            wordBreak: "break-word",
                            color: failed ? "rgba(255,180,180,0.85)" : "rgba(255,255,255,0.62)",
                            fontSize: 11.5,
                            lineHeight: 1.45,
                            fontFamily: mono,
                          }}
                        >
                          {entry.output || ""}
                          {entry.reason ? `\n${entry.reason}` : ""}
                        </pre>
                      )}
                      {entry.toolRunId ? (
                        <div style={{ marginTop: 4, fontSize: 10, color: "rgba(255,255,255,0.18)" }}>
                          tool {String(entry.toolRunId).slice(0, 8)}
                          {entry.turnId ? ` · turn ${String(entry.turnId).slice(0, 8)}` : ""}
                          {entry.mode ? ` · ${entry.mode}` : ""}
                        </div>
                      ) : null}
                    </div>
                  );
                })
              )}
              <div ref={termEndRef} />
            </div>
          </div>
        ) : null}

        {/* ── CHANGES ── */}
        {attached && tab === "changes" ? (
          <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
            <div
              style={{
                width: diffSession || diffLoading ? "36%" : "100%",
                minWidth: 180,
                maxWidth: diffSession || diffLoading ? 320 : undefined,
                borderRight: diffSession || diffLoading ? `1px solid ${c.line}` : "none",
                overflow: "auto",
                background: c.panel,
              }}
            >
              <div style={{ padding: "10px 14px 8px", borderBottom: `1px solid ${c.lineSoft}` }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: c.white }}>Session changes</div>
                <div style={{ fontSize: 10, color: c.mute, marginTop: 2 }}>Inspected & modified by Echo</div>
              </div>

              {pendingSession ? (
                <div
                  style={{
                    margin: 10,
                    padding: 12,
                    borderRadius: 4,
                    border: `1px solid rgba(255,255,255,0.14)`,
                    background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02))",
                  }}
                >
                  <div style={{ fontSize: 11, fontWeight: 700, color: c.white }}>Awaiting approval</div>
                  <div style={{ fontFamily: mono, fontSize: 11, color: c.dim, marginTop: 4 }}>{pendingSession.filename}</div>
                  <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                    {onCancelSave ? (
                      <button type="button" onClick={onCancelSave} style={ghostBtn()}>
                        Cancel
                      </button>
                    ) : null}
                    {onConfirmSave ? (
                      <button type="button" onClick={onConfirmSave} style={ghostBtn(true)}>
                        Confirm
                      </button>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => setDiffSession({ ...pendingSession, pendingConfirmation: true })}
                      style={ghostBtn()}
                    >
                      View
                    </button>
                  </div>
                </div>
              ) : null}

              {mergedChanges.length === 0 ? (
                <div style={{ padding: 20, color: c.mute, fontSize: 12, lineHeight: 1.5 }}>
                  No file activity yet this session. When Echo reads or writes, entries land here with turn / tool-run ids.
                </div>
              ) : (
                mergedChanges.map((ch) => {
                  const id = ch.id;
                  const active = selectedChangeId === id;
                  return (
                    <button
                      key={id}
                      type="button"
                      onClick={() => void loadChangeDiff(ch)}
                      style={{
                        display: "block",
                        width: "100%",
                        textAlign: "left",
                        padding: "11px 14px",
                        border: "none",
                        borderBottom: `1px solid ${c.lineSoft}`,
                        borderLeft: active ? "2px solid #fff" : "2px solid transparent",
                        background: active
                          ? "linear-gradient(90deg, rgba(255,255,255,0.07), transparent)"
                          : "transparent",
                        color: c.text,
                        cursor: "pointer",
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                        <span
                          style={{
                            fontFamily: mono,
                            fontSize: 12,
                            fontWeight: 600,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                            color: c.white,
                          }}
                        >
                          {ch.path ? basename(ch.path) : ch.toolName}
                        </span>
                        <SoftPill tone={ch.action === "inspect" ? "soft" : "neutral"}>{ch.action || ch.toolName}</SoftPill>
                      </div>
                      <div style={{ fontSize: 10, color: c.mute, marginTop: 4, fontFamily: mono }}>
                        {ch.toolName}
                        {ch.toolRunId ? ` · ${String(ch.toolRunId).slice(0, 8)}` : ""}
                        {ch.at ? ` · ${formatTime(ch.at)}` : ""}
                      </div>
                      {ch.summary ? (
                        <div
                          style={{
                            fontSize: 11,
                            color: c.dim,
                            marginTop: 4,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {ch.summary}
                        </div>
                      ) : null}
                    </button>
                  );
                })
              )}
            </div>
            {diffSession || diffLoading ? (
              <div style={{ flex: 1, minWidth: 0, overflow: "hidden", background: c.bg }}>
                {diffLoading ? (
                  <div style={{ padding: 16, color: c.mute, fontSize: 12, fontFamily: mono }}>Loading diff…</div>
                ) : diffSession ? (
                  <InlineCodeDiff
                    session={diffSession}
                    onAccept={diffSession.pendingConfirmation ? onConfirmSave : undefined}
                    onDecline={diffSession.pendingConfirmation ? onCancelSave : undefined}
                  />
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default CodeWorkspace;
