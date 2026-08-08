import React, { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";

type TodoItem = {
  id: string;
  title: string;
  description: string;
  status: "pending" | "in_progress" | "needs_permission" | "blocked" | "failed" | "cancelled" | "complete" | "done";
  priority: "low" | "medium" | "high";
  created_at: string;
  updated_at: string;
  source?: string;
  automation_run_ids?: string[];
  task_run_ids?: string[];
};

type TodoPanelProps = {
  apiBase: string;
  projectId: string;
  sessionId: string;
  colors: {
    bg: string;
    panel: string;
    panel2: string;
    accent: string;
    text: string;
    textDim: string;
    line: string;
    danger: string;
  };
  variant?: "panel" | "visualizer";
};

const mono = "'JetBrains Mono', ui-monospace, monospace";

const STATUS_LABELS: Record<TodoItem["status"], string> = {
  pending: "Pending",
  in_progress: "In Progress",
  needs_permission: "Needs Permission",
  blocked: "Blocked",
  failed: "Failed",
  cancelled: "Cancelled",
  complete: "Complete",
  done: "Done",
};

async function requestJson(url: string, init?: RequestInit) {
  const res = await fetch(url, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data?.detail || data?.message || `Request failed (${res.status})`);
  }
  return data;
}

/** Monochrome status mark — Echo style, no green/amber chrome */
const StatusIcon: React.FC<{ status: TodoItem["status"] }> = ({ status }) => {
  if (status === "done" || status === "complete") {
    return (
      <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden>
        <circle cx="10" cy="10" r="8.5" fill="rgba(255,255,255,0.92)" stroke="rgba(255,255,255,0.92)" strokeWidth="1.2" />
        <path d="M6.2 10.3l2.4 2.4 5-5" stroke="#0a0a0a" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  if (status === "in_progress") {
    return (
      <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden>
        <circle cx="10" cy="10" r="8.5" stroke="rgba(255,255,255,0.55)" strokeWidth="1.4" />
        <path d="M10 5.5v5l3 2" stroke="rgba(255,255,255,0.85)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden>
      <circle cx="10" cy="10" r="8.5" stroke="rgba(255,255,255,0.22)" strokeWidth="1.4" />
    </svg>
  );
};

export const TodoPanel: React.FC<TodoPanelProps> = ({ apiBase, projectId, sessionId, colors, variant = "panel" }) => {
  const [todos, setTodos] = useState<TodoItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newPriority, setNewPriority] = useState<TodoItem["priority"]>("medium");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editPriority, setEditPriority] = useState<TodoItem["priority"]>("medium");
  const [filter, setFilter] = useState<"all" | TodoItem["status"]>("all");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (!projectId || !sessionId) {
        setTodos([]);
        return;
      }
      const scope = `session_id=${encodeURIComponent(sessionId)}&project_id=${encodeURIComponent(projectId)}`;
      const data = await requestJson(`${apiBase}/todos?${scope}`);
      setTodos(Array.isArray(data.todos) ? data.todos : []);
    } catch (e: any) {
      setError(e.message || "Failed to load todos");
    } finally {
      setLoading(false);
    }
  }, [apiBase, projectId, sessionId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const addTodo = async () => {
    if (!newTitle.trim()) return;
    try {
      await requestJson(`${apiBase}/todos`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: newTitle.trim(),
          description: newDesc.trim(),
          priority: newPriority,
          status: "pending",
          project_id: projectId,
          session_id: sessionId,
        }),
      });
      setNewTitle("");
      setNewDesc("");
      setNewPriority("medium");
      setShowAdd(false);
      await refresh();
    } catch (e: any) {
      setError(e.message || "Failed to add todo");
    }
  };

  const updateStatus = async (todo: TodoItem, status: TodoItem["status"]) => {
    if (todo.source !== "user" || todo.automation_run_ids?.length || todo.task_run_ids?.length) return;
    try {
      await requestJson(`${apiBase}/todos/${todo.id}?session_id=${encodeURIComponent(sessionId)}&project_id=${encodeURIComponent(projectId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      await refresh();
    } catch (e: any) {
      setError(e.message || "Failed to update todo");
    }
  };

  const saveEdit = async (todo: TodoItem) => {
    if (todo.source !== "user" || todo.automation_run_ids?.length || todo.task_run_ids?.length) return;
    try {
      await requestJson(`${apiBase}/todos/${todo.id}?session_id=${encodeURIComponent(sessionId)}&project_id=${encodeURIComponent(projectId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: editTitle.trim() || todo.title,
          description: editDesc,
          status: todo.status,
          priority: editPriority,
        }),
      });
      setEditingId(null);
      await refresh();
    } catch (e: any) {
      setError(e.message || "Failed to save todo");
    }
  };

  const deleteTodo = async (id: string) => {
    try {
      await requestJson(`${apiBase}/todos/${id}?session_id=${encodeURIComponent(sessionId)}&project_id=${encodeURIComponent(projectId)}`, { method: "DELETE" });
      await refresh();
    } catch (e: any) {
      setError(e.message || "Failed to delete todo");
    }
  };

  const cycleStatus = (status: TodoItem["status"]): TodoItem["status"] => {
    if (status === "pending") return "in_progress";
    if (status === "in_progress") return "done";
    return "pending";
  };

  const filteredTodos = filter === "all" ? todos : todos.filter((t) => t.status === filter);
  const counts = {
    all: todos.length,
    pending: todos.filter((t) => t.status === "pending").length,
    in_progress: todos.filter((t) => t.status === "in_progress").length,
    done: todos.filter((t) => t.status === "done").length,
  };

  const isViz = variant === "visualizer";

  const wrapStyle: React.CSSProperties = isViz
    ? {
        height: "100%",
        overflowY: "auto",
        display: "flex",
        flexDirection: "column",
        gap: 0,
        padding: "12px 14px 16px",
        background: "#080809",
        color: colors.text,
      }
    : {};

  const filterBtnStyle = (active: boolean): React.CSSProperties => ({
    padding: "5px 10px",
    borderRadius: 3,
    border: active ? "1px solid rgba(255,255,255,0.24)" : "1px solid rgba(255,255,255,0.10)",
    background: active ? "rgba(255,255,255,0.08)" : "transparent",
    color: active ? "#fff" : "rgba(255,255,255,0.48)",
    fontSize: 11,
    fontWeight: 600,
    cursor: "pointer",
    transition: "background 0.15s, border-color 0.15s, color 0.15s",
  });

  const inputStyle: React.CSSProperties = {
    width: "100%",
    boxSizing: "border-box",
    background: "rgba(255,255,255,0.03)",
    border: "1px solid rgba(255,255,255,0.12)",
    borderRadius: 3,
    padding: "8px 11px",
    color: colors.text,
    fontSize: 13,
    outline: "none",
    fontFamily: "inherit",
  };

  const softChip = (active = false): React.CSSProperties => ({
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: 0.3,
    textTransform: "uppercase",
    padding: "2px 6px",
    borderRadius: 3,
    fontFamily: mono,
    background: active ? "rgba(255,255,255,0.1)" : "rgba(255,255,255,0.03)",
    color: active ? "rgba(255,255,255,0.85)" : "rgba(255,255,255,0.4)",
    border: active ? "1px solid rgba(255,255,255,0.2)" : "1px solid rgba(255,255,255,0.08)",
  });

  const priorityWeight: Record<TodoItem["priority"], number> = { high: 3, medium: 2, low: 1 };

  const content = (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, height: isViz ? "100%" : undefined, minHeight: 0 }}>
      {/* Toolbar: filters + actions */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 4, alignItems: "center", flexWrap: "wrap" }}>
          {(["all", "pending", "in_progress", "done"] as const).map((f) => (
            <button key={f} type="button" onClick={() => setFilter(f)} style={filterBtnStyle(filter === f)}>
              {f === "all" ? `All${counts.all ? ` ${counts.all}` : ""}` : f === "in_progress" ? "Active" : f[0].toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          <button type="button" onClick={refresh} disabled={loading} title="Refresh" style={{ ...filterBtnStyle(false), opacity: loading ? 0.5 : 1, minWidth: 32 }}>
            {loading ? "…" : "↻"}
          </button>
          <button type="button" onClick={() => setShowAdd((v) => !v)} style={filterBtnStyle(showAdd)}>
            {showAdd ? "Close" : "+"}
          </button>
        </div>
      </div>

      {error ? (
        <div
          style={{
            color: "rgba(255,255,255,0.7)",
            background: "rgba(255,255,255,0.03)",
            border: "1px solid rgba(255,255,255,0.12)",
            borderRadius: 3,
            padding: "8px 11px",
            fontSize: 12,
          }}
        >
          {error}
        </div>
      ) : null}

      <AnimatePresence initial={false}>
        {showAdd ? (
          <motion.div
            key="add-form"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            style={{ overflow: "hidden" }}
          >
            <div
              style={{
                padding: 12,
                background: "rgba(255,255,255,0.025)",
                border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: 3,
                display: "flex",
                flexDirection: "column",
                gap: 8,
              }}
            >
              <input
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") addTodo();
                }}
                placeholder="Task title"
                autoFocus
                style={inputStyle}
              />
              <textarea
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                placeholder="Notes (optional)"
                rows={2}
                style={{ ...inputStyle, resize: "vertical", lineHeight: 1.45 }}
              />
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                <div style={{ display: "flex", gap: 4 }}>
                  {(["low", "medium", "high"] as const).map((p) => (
                    <button key={p} type="button" onClick={() => setNewPriority(p)} style={softChip(newPriority === p)}>
                      {p}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  onClick={addTodo}
                  disabled={!newTitle.trim()}
                  style={{
                    padding: "6px 14px",
                    borderRadius: 3,
                    border: "1px solid rgba(255,255,255,0.2)",
                    background: "rgba(255,255,255,0.08)",
                    color: "#fff",
                    fontSize: 12,
                    fontWeight: 600,
                    cursor: newTitle.trim() ? "pointer" : "not-allowed",
                    opacity: newTitle.trim() ? 1 : 0.45,
                  }}
                >
                  Add
                </button>
              </div>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 4,
          flex: isViz ? 1 : undefined,
          overflowY: isViz ? "auto" : undefined,
          minHeight: 0,
        }}
      >
        {loading && todos.length === 0 ? (
          <div style={{ padding: "28px 0", textAlign: "center", color: "rgba(255,255,255,0.32)", fontSize: 12 }}>Loading…</div>
        ) : null}

        {!loading && filteredTodos.length === 0 ? (
          <div
            style={{
              padding: "40px 16px",
              textAlign: "center",
              color: "rgba(255,255,255,0.35)",
              fontSize: 12,
              border: "1px dashed rgba(255,255,255,0.08)",
              borderRadius: 3,
            }}
          >
            {filter === "all" ? "No tasks yet" : `No ${filter === "in_progress" ? "active" : filter} tasks`}
          </div>
        ) : null}

        <AnimatePresence initial={false}>
          {[...filteredTodos]
            .sort((a, b) => {
              if (a.status === "done" && b.status !== "done") return 1;
              if (a.status !== "done" && b.status === "done") return -1;
              return priorityWeight[b.priority] - priorityWeight[a.priority];
            })
            .map((todo) => (
              <motion.div
                key={todo.id}
                layout
                initial={{ opacity: 0, y: 3 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.12 }}
              >
                {editingId === todo.id ? (
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 8,
                      padding: "10px 12px",
                      background: "rgba(255,255,255,0.03)",
                      border: "1px solid rgba(255,255,255,0.14)",
                      borderRadius: 3,
                    }}
                  >
                    <input
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") saveEdit(todo);
                      }}
                      style={inputStyle}
                    />
                    <textarea
                      value={editDesc}
                      onChange={(e) => setEditDesc(e.target.value)}
                      rows={2}
                      style={{ ...inputStyle, resize: "vertical", lineHeight: 1.45 }}
                    />
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                      <div style={{ display: "flex", gap: 4 }}>
                        {(["low", "medium", "high"] as const).map((p) => (
                          <button key={p} type="button" onClick={() => setEditPriority(p)} style={softChip(editPriority === p)}>
                            {p}
                          </button>
                        ))}
                      </div>
                      <div style={{ display: "flex", gap: 4 }}>
                        <button type="button" onClick={() => saveEdit(todo)} style={{ ...filterBtnStyle(true), padding: "5px 12px" }}>
                          Save
                        </button>
                        <button type="button" onClick={() => setEditingId(null)} style={{ ...filterBtnStyle(false), padding: "5px 12px" }}>
                          Cancel
                        </button>
                      </div>
                    </div>
                  </div>
                ) : (
                  (() => {
                  const readOnlyProjection = todo.source !== "user" || Boolean(todo.automation_run_ids?.length || todo.task_run_ids?.length);
                  return (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: 10,
                      padding: "10px 12px",
                      background: "transparent",
                      border: "1px solid rgba(255,255,255,0.08)",
                      borderRadius: 3,
                      opacity: todo.status === "done" ? 0.55 : 1,
                    }}
                  >
                    <button
                      type="button"
                      onClick={() => updateStatus(todo, cycleStatus(todo.status))}
                      disabled={readOnlyProjection}
                      title={STATUS_LABELS[todo.status]}
                      style={{
                        flexShrink: 0,
                        marginTop: 1,
                        background: "none",
                        border: "none",
                        cursor: readOnlyProjection ? "default" : "pointer",
                        padding: 0,
                        lineHeight: 0,
                      }}
                    >
                      <StatusIcon status={todo.status} />
                    </button>

                    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 3 }}>
                      <div
                        style={{
                          fontSize: 13,
                          fontWeight: 600,
                          color: todo.status === "done" ? "rgba(255,255,255,0.45)" : "#fff",
                          textDecoration: todo.status === "done" ? "line-through" : "none",
                          lineHeight: 1.35,
                          wordBreak: "break-word",
                        }}
                      >
                        {todo.title}
                      </div>
                      {todo.description ? (
                        <div style={{ fontSize: 12, color: "rgba(255,255,255,0.38)", lineHeight: 1.45, wordBreak: "break-word" }}>
                          {todo.description}
                        </div>
                      ) : null}
                      {todo.priority === "high" || todo.status === "in_progress" ? (
                        <div style={{ display: "flex", gap: 4, marginTop: 2 }}>
                          {todo.priority === "high" ? <span style={softChip(true)}>high</span> : null}
                          {todo.status === "in_progress" ? <span style={softChip(true)}>active</span> : null}
                        </div>
                      ) : null}
                      {readOnlyProjection ? <div><span style={softChip(false)}>runtime projection</span></div> : null}
                    </div>

                    {!readOnlyProjection ? <div style={{ display: "flex", gap: 0, flexShrink: 0 }}>
                      <button
                        type="button"
                        onClick={() => {
                          setEditingId(todo.id);
                          setEditTitle(todo.title);
                          setEditDesc(todo.description);
                          setEditPriority(todo.priority);
                        }}
                        style={{
                          background: "none",
                          border: "none",
                          color: "rgba(255,255,255,0.35)",
                          fontSize: 11,
                          cursor: "pointer",
                          padding: "2px 6px",
                        }}
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteTodo(todo.id)}
                        style={{
                          background: "none",
                          border: "none",
                          color: "rgba(255,255,255,0.3)",
                          fontSize: 14,
                          cursor: "pointer",
                          padding: "0 4px",
                          lineHeight: 1,
                        }}
                      >
                        ×
                      </button>
                    </div> : null}
                  </div>
                  );
                  })()
                )}
              </motion.div>
            ))}
        </AnimatePresence>
      </div>
    </div>
  );

  if (variant === "visualizer") {
    return <div style={wrapStyle}>{content}</div>;
  }

  return (
    <div className="research-scroll">
      <div className="research-card">{content}</div>
    </div>
  );
};
