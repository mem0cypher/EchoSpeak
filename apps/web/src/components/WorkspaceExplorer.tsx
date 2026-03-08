import React, { useState, useEffect, useCallback } from "react";

export type FileNode = {
  name: string;
  path: string;
  type: "file" | "directory";
  size?: number;
  children?: FileNode[];
  item_count?: number;
};

export type WorkspaceData = {
  root: string;
  display_name: string;
  files: FileNode[];
  writable: boolean;
  terminal: boolean;
};

type WorkspaceExplorerProps = {
  apiBase: string;
  onFileSelect?: (path: string) => void;
};

const formatSize = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const fileIcon = (name: string): string => {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["py"].includes(ext)) return "🐍";
  if (["ts", "tsx"].includes(ext)) return "🔷";
  if (["js", "jsx"].includes(ext)) return "🟡";
  if (["md"].includes(ext)) return "📝";
  if (["json"].includes(ext)) return "📋";
  if (["env", "env.example"].includes(ext) || name.startsWith(".env")) return "🔐";
  if (["txt", "log"].includes(ext)) return "📄";
  if (["html", "css"].includes(ext)) return "🌐";
  if (["yml", "yaml", "toml"].includes(ext)) return "⚙️";
  return "📄";
};

function TreeNode({ node, depth, onFileSelect }: { node: FileNode; depth: number; onFileSelect?: (path: string) => void }) {
  const [expanded, setExpanded] = useState(depth < 1);
  const isDir = node.type === "directory";

  return (
    <div>
      <div
        onClick={() => {
          if (isDir) setExpanded(!expanded);
          else if (onFileSelect) onFileSelect(node.path);
        }}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "3px 8px 3px " + (12 + depth * 16) + "px",
          cursor: "pointer",
          fontSize: 12,
          color: isDir ? "#93c5fd" : "rgba(255,255,255,0.7)",
          fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
          borderRadius: 4,
          transition: "background 0.1s",
          userSelect: "none",
        }}
        onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(255,255,255,0.05)"; }}
        onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
      >
        <span style={{ fontSize: 10, width: 14, textAlign: "center", flexShrink: 0, opacity: isDir ? 1 : 0.3 }}>
          {isDir ? (expanded ? "▼" : "▶") : " "}
        </span>
        <span style={{ fontSize: 13, flexShrink: 0 }}>
          {isDir ? (expanded ? "📂" : "📁") : fileIcon(node.name)}
        </span>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
          {node.name}
        </span>
        {!isDir && node.size !== undefined && (
          <span style={{ fontSize: 10, color: "rgba(255,255,255,0.25)", flexShrink: 0 }}>
            {formatSize(node.size)}
          </span>
        )}
        {isDir && node.item_count !== undefined && node.item_count > 0 && (
          <span style={{ fontSize: 10, color: "rgba(255,255,255,0.2)", flexShrink: 0 }}>
            {node.item_count}
          </span>
        )}
      </div>
      {isDir && expanded && node.children && node.children.length > 0 && (
        <div>
          {node.children.map((child) => (
            <TreeNode key={child.path} node={child} depth={depth + 1} onFileSelect={onFileSelect} />
          ))}
        </div>
      )}
    </div>
  );
}

export function WorkspaceExplorer({ apiBase, onFileSelect }: WorkspaceExplorerProps) {
  const [workspace, setWorkspace] = useState<WorkspaceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [changingDir, setChangingDir] = useState(false);
  const [newPath, setNewPath] = useState("");

  const openChangeDir = useCallback(() => {
    setNewPath(workspace?.root || "");
    setChangingDir((prev) => !prev);
  }, [workspace]);

  const fetchWorkspace = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/workspace`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setWorkspace(data);
      setNewPath((current) => current || data.root || "");
    } catch (e: any) {
      setError(e.message || "Failed to load workspace");
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => { fetchWorkspace(); }, [fetchWorkspace]);

  const handleChangeDir = async () => {
    if (!newPath.trim()) return;
    setError(null);
    try {
      const res = await fetch(`${apiBase}/workspace`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ root: newPath.trim() }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setWorkspace(data);
      setChangingDir(false);
      setNewPath("");
    } catch (e: any) {
      setError(e.message || "Failed to change directory");
    }
  };

  return (
    <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Header */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 8,
        padding: "10px 14px",
        borderBottom: "1px solid rgba(255,255,255,0.06)",
        background: "rgba(255,255,255,0.02)",
        flexShrink: 0,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <span style={{ fontSize: 14 }}>📂</span>
          <div style={{ minWidth: 0 }}>
            <div style={{
              fontSize: 12,
              fontWeight: 700,
              color: "#e2e8f0",
              fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}>
              {workspace?.display_name || "Workspace"}
            </div>
            {workspace && (
              <div style={{
                fontSize: 10,
                color: "rgba(255,255,255,0.35)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                maxWidth: 280,
              }}>
                {workspace.root}
              </div>
            )}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
          {workspace && (
            <>
              {workspace.writable && (
                <span style={{
                  padding: "2px 6px",
                  borderRadius: 4,
                  background: "rgba(34,197,94,0.12)",
                  border: "1px solid rgba(34,197,94,0.25)",
                  color: "#4ade80",
                  fontSize: 9,
                  fontWeight: 700,
                }}>
                  WRITE
                </span>
              )}
              {workspace.terminal && (
                <span style={{
                  padding: "2px 6px",
                  borderRadius: 4,
                  background: "rgba(96,165,250,0.12)",
                  border: "1px solid rgba(96,165,250,0.25)",
                  color: "#93c5fd",
                  fontSize: 9,
                  fontWeight: 700,
                }}>
                  TERM
                </span>
              )}
            </>
          )}
          <button
            onClick={openChangeDir}
            title="Change working directory"
            style={{
              padding: "4px 8px",
              borderRadius: 6,
              border: "1px solid rgba(255,255,255,0.1)",
              background: changingDir ? "rgba(139,92,246,0.15)" : "rgba(255,255,255,0.04)",
              color: changingDir ? "#c4b5fd" : "rgba(255,255,255,0.5)",
              fontSize: 11,
              cursor: "pointer",
              transition: "all 0.15s",
            }}
          >
            cd
          </button>
          <button
            onClick={fetchWorkspace}
            title="Refresh"
            style={{
              padding: "4px 8px",
              borderRadius: 6,
              border: "1px solid rgba(255,255,255,0.1)",
              background: "rgba(255,255,255,0.04)",
              color: "rgba(255,255,255,0.5)",
              fontSize: 11,
              cursor: "pointer",
              transition: "all 0.15s",
            }}
          >
            ↻
          </button>
        </div>
      </div>

      {/* Change directory bar */}
      {changingDir && (
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "8px 14px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          background: "rgba(139,92,246,0.04)",
          flexShrink: 0,
        }}>
          <input
            type="text"
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleChangeDir(); if (e.key === "Escape") setChangingDir(false); }}
            placeholder={workspace?.root || "/path/to/your/workspace"}
            autoFocus
            style={{
              flex: 1,
              padding: "6px 10px",
              borderRadius: 6,
              border: "1px solid rgba(255,255,255,0.1)",
              background: "rgba(0,0,0,0.3)",
              color: "#e2e8f0",
              fontSize: 11,
              fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
              outline: "none",
            }}
          />
          <button
            onClick={handleChangeDir}
            style={{
              padding: "6px 12px",
              borderRadius: 6,
              border: "1px solid rgba(139,92,246,0.3)",
              background: "rgba(139,92,246,0.15)",
              color: "#c4b5fd",
              fontSize: 11,
              fontWeight: 700,
              cursor: "pointer",
            }}
          >
            Go
          </button>
        </div>
      )}

      {/* Error */}
      {error && (
        <div style={{
          padding: "8px 14px",
          background: "rgba(239,68,68,0.08)",
          borderBottom: "1px solid rgba(239,68,68,0.15)",
          color: "#fca5a5",
          fontSize: 11,
          flexShrink: 0,
        }}>
          {error}
        </div>
      )}

      {/* File tree */}
      <div style={{ flex: 1, overflow: "auto", padding: "6px 0" }}>
        {loading ? (
          <div style={{ textAlign: "center", padding: 30, color: "rgba(255,255,255,0.3)", fontSize: 12 }}>
            Loading workspace...
          </div>
        ) : workspace && workspace.files.length > 0 ? (
          workspace.files.map((node) => (
            <TreeNode key={node.path} node={node} depth={0} onFileSelect={onFileSelect} />
          ))
        ) : (
          <div style={{ textAlign: "center", padding: 30, color: "rgba(255,255,255,0.25)", fontSize: 12, fontStyle: "italic" }}>
            No files found in workspace
          </div>
        )}
      </div>
    </div>
  );
}
