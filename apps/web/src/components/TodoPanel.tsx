import React, { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";

type TodoItem = {
  id: string;
  title: string;
  description: string;
  status: "pending" | "in_progress" | "done";
  priority: "low" | "medium" | "high";
  created_at: string;
  updated_at: string;
};

type TodoPanelProps = {
  apiBase: string;
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
  if (status === "done") {
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

export const TodoPanel: React.FC<TodoPanelProps> = ({ apiBase, colors, variant = "panel" }) => {
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
      const data = await requestJson(`${apiBase}/todos`);
      setTodos(Array.isArray(data.todos) ? data.todos : []);
    } catch (e: any) {
      setError(e.message || "Failed to load todos");
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

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
    try {
      await requestJson(`${apiBase}/todos/${todo.id}`, {
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
    try {
      await requestJson(`${apiBase}/todos/${todo.id}`, {
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
      await requestJson(`${apiBase}/todos/${id}`, { method: "DELETE" });
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
        padding: "14px 16px 18px",
        background: "#000",
        color: colors.text,
      }
    : {};

  const monoLabel: React.CSSProperties = {
    fontFamily: mono,
    fontSize: 9,
    fontWeight: 600,
    letterSpacing: "0.12em",
    textTransform: "uppercase",
    color: "rgba(255,255,255,0.35)",
  };

  const filterBtnStyle = (active: boolean): React.CSSProperties => ({
    padding: "5px 11px",
    borderRadius: 3,
    border: active ? "1px solid rgba(255,255,255,0.22)" : "1px solid rgba(255,255,255,0.10)",
    background: active ? "rgba(255,255,255,0.1)" : "transparent",
    color: active ? "#fff" : "rgba(255,255,255,0.48)",
    fontSize: 11,
    fontWeight: 600,
    cursor: "pointer",
    transition: "background 0.15s, border-color 0.15s, color 0.15s",
    letterSpacing: "0.04em",
    textTransform: "uppercase",
    fontFamily: mono,
  });

  const inputStyle: React.CSSProperties = {
    width: "100%",
    boxSizing: "border-box",
    background: "rgba(255,255,255,0.03)",
    border: "1px solid rgba(255,255,255,0.12)",
    borderRadius: 3,
    padding: "9px 12px",
    color: colors.text,
    fontSize: 13,
    outline: "none",
    fontFamily: "inherit",
  };

  const softChip = (active = false): React.CSSProperties => ({
    fontSize: 10,
    fontWeight: 600,
    letterSpacing: 0.5,
    textTransform: "uppercase",
    padding: "3px 7px",
    borderRadius: 3,
    fontFamily: mono,
    background: active ? "rgba(255,255,255,0.1)" : "rgba(255,255,255,0.04)",
    color: active ? "rgba(255,255,255,0.9)" : "rgba(255,255,255,0.45)",
    border: active ? "1px solid rgba(255,255,255,0.22)" : "1px solid rgba(255,255,255,0.1)",
  });

  const priorityWeight: Record<TodoItem["priority"], number> = { high: 3, medium: 2, low: 1 };

  const content = (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, height: isViz ? "100%" : undefined, minHeight: 0 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 12 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#fff", letterSpacing: -0.2 }}>Tasks</div>
          <div style={{ ...monoLabel, marginTop: 4 }}>Echo workspace checklist</div>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button type="button" onClick={refresh} disabled={loading} style={{ ...filterBtnStyle(false), opacity: loading ? 0.5 : 1 }}>
            {loading ? "…" : "Refresh"}
          </button>
          <button
            type="button"
            onClick={() => setShowAdd((v) => !v)}
            style={{
              ...filterBtnStyle(showAdd),
              background: showAdd ? "rgba(255,255,255,0.1)" : "rgba(255,255,255,0.06)",
              borderColor: showAdd ? "rgba(255,255,255,0.28)" : "rgba(255,255,255,0.14)",
              color: "#fff",
            }}
          >
            {showAdd ? "Close" : "+ Add"}
          </button>
        </div>
      </div>

      {/* Counts — monochrome rail */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
          gap: 1,
          background: "rgba(255,255,255,0.08)",
          border: "1px solid rgba(255,255,255,0.1)",
          borderRadius: 3,
          overflow: "hidden",
        }}
      >
        {(
          [
            ["All", counts.all],
            ["Pending", counts.pending],
            ["Working", counts.in_progress],
            ["Done", counts.done],
          ] as const
        ).map(([label, val]) => (
          <div
            key={label}
            style={{
              padding: "10px 12px",
              background: "#0a0a0a",
              display: "flex",
              flexDirection: "column",
              gap: 4,
            }}
          >
            <div style={monoLabel}>{label}</div>
            <div style={{ fontSize: 20, fontWeight: 700, color: "#fff", lineHeight: 1, fontFamily: mono }}>{val}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
        {(["all", "pending", "in_progress", "done"] as const).map((f) => (
          <button key={f} type="button" onClick={() => setFilter(f)} style={filterBtnStyle(filter === f)}>
            {f === "all" ? "All" : f === "in_progress" ? "In Progress" : f[0].toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {error ? (
        <div
          style={{
            color: "rgba(255,255,255,0.75)",
            background: "rgba(255,255,255,0.04)",
            border: "1px solid rgba(255,255,255,0.14)",
            borderRadius: 3,
            padding: "10px 12px",
            fontSize: 12,
          }}
        >
          {error}
        </div>
      ) : null}

      {/* Add form */}
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
                padding: 14,
                background: "rgba(255,255,255,0.03)",
                border: "1px solid rgba(255,255,255,0.12)",
                borderRadius: 3,
                display: "flex",
                flexDirection: "column",
                gap: 10,
              }}
            >
              <input
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") addTodo();
                }}
                placeholder="Task title..."
                autoFocus
                style={inputStyle}
              />
              <textarea
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                placeholder="Description / context for Echo (optional)"
                rows={2}
                style={{ ...inputStyle, resize: "vertical", lineHeight: 1.5 }}
              />
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                <div style={{ display: "flex", gap: 6 }}>
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
                    padding: "7px 16px",
                    borderRadius: 3,
                    border: "1px solid rgba(255,255,255,0.22)",
                    background: "rgba(255,255,255,0.08)",
                    color: "#fff",
                    fontSize: 12,
                    fontWeight: 700,
                    cursor: newTitle.trim() ? "pointer" : "not-allowed",
                    opacity: newTitle.trim() ? 1 : 0.45,
                    fontFamily: mono,
                  }}
                >
                  Create
                </button>
              </div>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      {/* List */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 6,
          flex: isViz ? 1 : undefined,
          overflowY: isViz ? "auto" : undefined,
          minHeight: 0,
        }}
      >
        {loading && todos.length === 0 ? (
          <div style={{ padding: "24px 0", textAlign: "center", color: "rgba(255,255,255,0.35)", fontSize: 12, fontFamily: mono }}>
            Loading tasks…
          </div>
        ) : null}

        {!loading && filteredTodos.length === 0 ? (
          <div
            style={{
              padding: "36px 20px",
              textAlign: "center",
              background: "rgba(255,255,255,0.02)",
              border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 3,
            }}
          >
            <div
              style={{
                width: 28,
                height: 28,
                margin: "0 auto 12px",
                borderRadius: 3,
                border: "1px solid rgba(255,255,255,0.12)",
                background: "rgba(255,255,255,0.03)",
              }}
            />
            <div style={{ fontSize: 13, color: "rgba(255,255,255,0.45)" }}>
              {filter === "all" ? "No tasks yet — add one above" : `No ${filter.replace("_", " ")} tasks`}
            </div>
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
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
              >
                {editingId === todo.id ? (
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      gap: 10,
                      padding: "12px 14px",
                      background: "rgba(255,255,255,0.03)",
                      border: "1px solid rgba(255,255,255,0.16)",
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
                      style={{ ...inputStyle, resize: "vertical", lineHeight: 1.5 }}
                    />
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
                      <div style={{ display: "flex", gap: 6 }}>
                        {(["low", "medium", "high"] as const).map((p) => (
                          <button key={p} type="button" onClick={() => setEditPriority(p)} style={softChip(editPriority === p)}>
                            {p}
                          </button>
                        ))}
                      </div>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button
                          type="button"
                          onClick={() => saveEdit(todo)}
                          style={{
                            padding: "5px 12px",
                            borderRadius: 3,
                            border: "1px solid rgba(255,255,255,0.22)",
                            background: "rgba(255,255,255,0.08)",
                            color: "#fff",
                            fontSize: 11,
                            fontWeight: 700,
                            cursor: "pointer",
                            fontFamily: mono,
                          }}
                        >
                          Save
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingId(null)}
                          style={{
                            padding: "5px 12px",
                            borderRadius: 3,
                            border: "1px solid rgba(255,255,255,0.1)",
                            background: "transparent",
                            color: "rgba(255,255,255,0.5)",
                            fontSize: 11,
                            fontWeight: 600,
                            cursor: "pointer",
                            fontFamily: mono,
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: 12,
                      padding: "12px 14px",
                      background: todo.status === "done" ? "rgba(255,255,255,0.02)" : "rgba(255,255,255,0.03)",
                      border: "1px solid rgba(255,255,255,0.1)",
                      borderLeft:
                        todo.status === "done"
                          ? "2px solid rgba(255,255,255,0.35)"
                          : todo.status === "in_progress"
                            ? "2px solid rgba(255,255,255,0.7)"
                            : "2px solid rgba(255,255,255,0.12)",
                      borderRadius: 3,
                      transition: "border-color 0.15s, background 0.15s",
                    }}
                  >
                    <button
                      type="button"
                      onClick={() => updateStatus(todo, cycleStatus(todo.status))}
                      title={`Cycle status (currently ${STATUS_LABELS[todo.status]})`}
                      style={{
                        flexShrink: 0,
                        marginTop: 1,
                        background: "none",
                        border: "none",
                        cursor: "pointer",
                        padding: 0,
                        lineHeight: 0,
                      }}
                    >
                      <StatusIcon status={todo.status} />
                    </button>

                    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 5 }}>
                      <div
                        style={{
                          fontSize: 13,
                          fontWeight: 600,
                          color: todo.status === "done" ? "rgba(255,255,255,0.4)" : "#fff",
                          textDecoration: todo.status === "done" ? "line-through" : "none",
                          lineHeight: 1.35,
                          wordBreak: "break-word",
                        }}
                      >
                        {todo.title}
                      </div>
                      {todo.description ? (
                        <div style={{ fontSize: 12, color: "rgba(255,255,255,0.42)", lineHeight: 1.5, wordBreak: "break-word" }}>
                          {todo.description}
                        </div>
                      ) : null}
                      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", marginTop: 2 }}>
                        <span style={softChip(todo.priority === "high")}>{todo.priority}</span>
                        <span style={softChip(todo.status === "in_progress")}>{STATUS_LABELS[todo.status]}</span>
                        {todo.updated_at ? (
                          <span style={{ fontSize: 10, color: "rgba(255,255,255,0.22)", fontFamily: mono }}>
                            {new Date(todo.updated_at).toLocaleDateString()}
                          </span>
                        ) : null}
                      </div>
                    </div>

                    <div style={{ display: "flex", gap: 2, flexShrink: 0, alignItems: "flex-start" }}>
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
                          color: "rgba(255,255,255,0.4)",
                          fontSize: 11,
                          cursor: "pointer",
                          padding: "3px 7px",
                          borderRadius: 3,
                          fontFamily: mono,
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.color = "#fff";
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.color = "rgba(255,255,255,0.4)";
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
                          color: "rgba(255,255,255,0.35)",
                          fontSize: 11,
                          cursor: "pointer",
                          padding: "3px 7px",
                          borderRadius: 3,
                          fontFamily: mono,
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.color = "rgba(255,255,255,0.85)";
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.color = "rgba(255,255,255,0.35)";
                        }}
                      >
                        ×
                      </button>
                    </div>
                  </div>
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
