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

const PRIORITY_COLORS: Record<TodoItem["priority"], string> = {
  high: "#ef4444",
  medium: "#f59e0b",
  low: "#64748b",
};

const STATUS_LABELS: Record<TodoItem["status"], string> = {
  pending: "Pending",
  in_progress: "Working",
  done: "Done",
};

const STATUS_ICONS: Record<TodoItem["status"], string> = {
  pending: "○",
  in_progress: "◔",
  done: "●",
};

async function requestJson(url: string, init?: RequestInit) {
  const res = await fetch(url, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data?.detail || data?.message || `Request failed (${res.status})`);
  }
  return data;
}

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
        body: JSON.stringify({ title: newTitle.trim(), description: newDesc.trim(), priority: newPriority, status: "pending" }),
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
          priority: todo.priority,
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

  const filteredTodos = filter === "all" ? todos : todos.filter((todo) => todo.status === filter);
  const counts = {
    all: todos.length,
    pending: todos.filter((todo) => todo.status === "pending").length,
    in_progress: todos.filter((todo) => todo.status === "in_progress").length,
    done: todos.filter((todo) => todo.status === "done").length,
  };
  const completion = counts.all ? Math.round((counts.done / counts.all) * 100) : 0;
  const rootStyle: React.CSSProperties = variant === "visualizer"
    ? { height: "100%", overflowY: "auto", padding: "18px 18px 22px" }
    : {};
  const cardShell: React.CSSProperties = {
    display: "flex",
    flexDirection: "column",
    gap: 16,
    background: variant === "visualizer" ? "transparent" : undefined,
  };
  const surfaceStyle: React.CSSProperties = {
    background: "linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.02))",
    border: `1px solid ${colors.line}`,
    borderRadius: 16,
    boxShadow: variant === "visualizer" ? "0 18px 40px rgba(0,0,0,0.2)" : "none",
  };

  const content = (
    <div style={cardShell}>
      <div style={{ ...surfaceStyle, padding: 18, background: "linear-gradient(135deg, rgba(79,142,255,0.16), rgba(255,255,255,0.04))" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ fontSize: 18, fontWeight: 700, color: colors.text }}>Echo Tasks</div>
            <div style={{ fontSize: 12, color: colors.textDim, lineHeight: 1.55, maxWidth: 560 }}>
              This is Echo&apos;s working list for the visualizer side. The backend API and the agent tool both point at the same task store.
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 11, color: colors.textDim }}>{counts.done}/{counts.all} complete</span>
            <button className="icon-button" type="button" onClick={refresh} disabled={loading} style={{ height: 34, padding: "0 12px", fontSize: 12 }}>
              {loading ? "Refreshing..." : "Refresh"}
            </button>
            <button className="icon-button" type="button" onClick={() => setShowAdd((value) => !value)} style={{ height: 34, padding: "0 12px", fontSize: 12, background: showAdd ? "rgba(255,255,255,0.12)" : undefined }}>
              {showAdd ? "Close" : "Add Task"}
            </button>
          </div>
        </div>
        <div style={{ marginTop: 14, height: 6, borderRadius: 999, overflow: "hidden", background: "rgba(255,255,255,0.06)" }}>
          <motion.div animate={{ width: `${completion}%` }} transition={{ duration: 0.4, ease: "easeOut" }} style={{ height: "100%", borderRadius: 999, background: "linear-gradient(90deg, #4f8eff, #22c55e)" }} />
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10 }}>
        {([
          ["All", counts.all, "#94a3b8"],
          ["Pending", counts.pending, "#64748b"],
          ["Working", counts.in_progress, "#f59e0b"],
          ["Done", counts.done, "#22c55e"],
        ] as const).map(([label, value, tone]) => (
          <div key={label} style={{ ...surfaceStyle, padding: 12 }}>
            <div style={{ fontSize: 11, color: colors.textDim, textTransform: "uppercase", letterSpacing: 0.6 }}>{label}</div>
            <div style={{ marginTop: 6, fontSize: 22, fontWeight: 700, color: tone }}>{value}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {(["all", "pending", "in_progress", "done"] as const).map((value) => (
          <button key={value} type="button" onClick={() => setFilter(value)} style={{ padding: "7px 12px", borderRadius: 999, border: `1px solid ${filter === value ? "rgba(255,255,255,0.26)" : colors.line}`, background: filter === value ? "rgba(255,255,255,0.1)" : "rgba(255,255,255,0.03)", color: filter === value ? colors.text : colors.textDim, fontSize: 12, fontWeight: 600, cursor: "pointer" }}>
            {value === "all" ? "All" : value === "in_progress" ? "In Progress" : value[0].toUpperCase() + value.slice(1)}
          </button>
        ))}
      </div>

      {error ? <div style={{ color: colors.danger, background: "rgba(239,68,68,0.08)", border: `1px solid ${colors.danger}33`, borderRadius: 12, padding: 12, fontSize: 12 }}>{error}</div> : null}

      <AnimatePresence initial={false}>
        {showAdd ? (
          <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} style={{ ...surfaceStyle, padding: 16 }}>
            <div style={{ display: "grid", gap: 12 }}>
              <input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") addTodo(); }} placeholder="Task title" style={{ width: "100%", background: "rgba(255,255,255,0.05)", border: `1px solid ${colors.line}`, borderRadius: 10, padding: "10px 12px", color: colors.text, fontSize: 13, outline: "none" }} />
              <textarea value={newDesc} onChange={(e) => setNewDesc(e.target.value)} placeholder="Optional context for Echo" rows={3} style={{ width: "100%", background: "rgba(255,255,255,0.05)", border: `1px solid ${colors.line}`, borderRadius: 10, padding: "10px 12px", color: colors.text, fontSize: 12, outline: "none", resize: "vertical", fontFamily: "inherit" }} />
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                <div style={{ display: "flex", gap: 8 }}>
                  {(["low", "medium", "high"] as const).map((priority) => (
                    <button key={priority} type="button" onClick={() => setNewPriority(priority)} style={{ padding: "7px 10px", borderRadius: 999, border: `1px solid ${newPriority === priority ? PRIORITY_COLORS[priority] : colors.line}`, background: newPriority === priority ? `${PRIORITY_COLORS[priority]}1f` : "rgba(255,255,255,0.03)", color: newPriority === priority ? PRIORITY_COLORS[priority] : colors.textDim, cursor: "pointer", fontSize: 11, fontWeight: 700, textTransform: "uppercase" }}>
                      {priority}
                    </button>
                  ))}
                </div>
                <button className="icon-button" type="button" onClick={addTodo} disabled={!newTitle.trim()} style={{ height: 34, padding: "0 14px", fontSize: 12 }}>
                  Create Task
                </button>
              </div>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <AnimatePresence initial={false}>
          {filteredTodos.map((todo) => (
            <motion.div key={todo.id} layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} style={{ ...surfaceStyle, padding: 14, background: todo.status === "done" ? "linear-gradient(180deg, rgba(34,197,94,0.07), rgba(255,255,255,0.02))" : undefined }}>
              {editingId === todo.id ? (
                <div style={{ display: "grid", gap: 10 }}>
                  <input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") saveEdit(todo); }} style={{ width: "100%", background: "rgba(255,255,255,0.05)", border: `1px solid ${colors.line}`, borderRadius: 10, padding: "10px 12px", color: colors.text, fontSize: 13, outline: "none" }} />
                  <textarea value={editDesc} onChange={(e) => setEditDesc(e.target.value)} rows={3} style={{ width: "100%", background: "rgba(255,255,255,0.05)", border: `1px solid ${colors.line}`, borderRadius: 10, padding: "10px 12px", color: colors.text, fontSize: 12, outline: "none", resize: "vertical", fontFamily: "inherit" }} />
                  <div style={{ display: "flex", gap: 8 }}>
                    <button className="icon-button" type="button" onClick={() => saveEdit(todo)} style={{ height: 32, padding: "0 12px", fontSize: 12 }}>Save</button>
                    <button className="icon-button" type="button" onClick={() => setEditingId(null)} style={{ height: 32, padding: "0 12px", fontSize: 12 }}>Cancel</button>
                  </div>
                </div>
              ) : (
                <>
                  <div style={{ display: "grid", gridTemplateColumns: "24px 1fr auto auto", gap: 10, alignItems: "start" }}>
                    <button type="button" onClick={() => updateStatus(todo, cycleStatus(todo.status))} style={{ marginTop: 2, background: "none", border: "none", color: todo.status === "done" ? "#22c55e" : todo.status === "in_progress" ? "#f59e0b" : colors.textDim, fontSize: 18, cursor: "pointer", padding: 0, lineHeight: 1 }}>
                      {STATUS_ICONS[todo.status]}
                    </button>
                    <div style={{ display: "grid", gap: 6 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, color: todo.status === "done" ? colors.textDim : colors.text, textDecoration: todo.status === "done" ? "line-through" : "none" }}>{todo.title}</div>
                      {todo.description ? <div style={{ fontSize: 12, color: colors.textDim, lineHeight: 1.55 }}>{todo.description}</div> : null}
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <span style={{ fontSize: 10, padding: "4px 8px", borderRadius: 999, background: `${PRIORITY_COLORS[todo.priority]}22`, color: PRIORITY_COLORS[todo.priority], fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5 }}>{todo.priority}</span>
                        <span style={{ fontSize: 10, padding: "4px 8px", borderRadius: 999, background: "rgba(255,255,255,0.06)", color: colors.textDim, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.5 }}>{STATUS_LABELS[todo.status]}</span>
                        <span style={{ fontSize: 10, color: colors.textDim }}>Updated {todo.updated_at ? new Date(todo.updated_at).toLocaleString() : "—"}</span>
                      </div>
                    </div>
                    <button type="button" onClick={() => { setEditingId(todo.id); setEditTitle(todo.title); setEditDesc(todo.description); }} style={{ background: "none", border: "none", color: colors.textDim, fontSize: 12, cursor: "pointer", padding: "4px 6px" }}>Edit</button>
                    <button type="button" onClick={() => deleteTodo(todo.id)} style={{ background: "none", border: "none", color: colors.textDim, fontSize: 12, cursor: "pointer", padding: "4px 6px" }}>Delete</button>
                  </div>
                </>
              )}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {!loading && filteredTodos.length === 0 ? (
        <div style={{ ...surfaceStyle, padding: 24, textAlign: "center", color: colors.textDim, fontSize: 13 }}>
          {filter === "all" ? "No tasks yet. Create one for Echo." : `No ${filter.replace("_", " ")} tasks right now.`}
        </div>
      ) : null}
    </div>
  );

  if (variant === "visualizer") {
    return <div style={rootStyle}>{content}</div>;
  }

  return (
    <div className="research-scroll">
      <div className="research-card">{content}</div>
    </div>
  );
};
