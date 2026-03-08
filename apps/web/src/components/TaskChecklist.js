import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { motion, AnimatePresence } from "framer-motion";
// ── Initial state factory ──────────────────────────────────────────
export function createEmptyTaskPlan() {
    return { tasks: [], reflections: [], active: false };
}
// ── Reducer for stream events ──────────────────────────────────────
export function taskPlanReducer(state, event) {
    if (event.type === "task_plan" && Array.isArray(event.data)) {
        return {
            tasks: event.data.map((t) => ({
                index: t.index ?? 0,
                description: t.description ?? t.tool ?? "Task",
                tool: t.tool ?? "",
                status: (t.status ?? "pending"),
                resultPreview: "",
            })),
            reflections: [],
            active: true,
        };
    }
    if (event.type === "task_step" && event.data) {
        const d = event.data;
        const idx = d.index ?? 0;
        return {
            ...state,
            tasks: state.tasks.map((t) => t.index === idx
                ? {
                    ...t,
                    status: (d.status ?? t.status),
                    resultPreview: d.result_preview || t.resultPreview,
                }
                : t),
        };
    }
    if (event.type === "task_reflection" && event.data) {
        return {
            ...state,
            reflections: [
                ...state.reflections,
                {
                    index: event.data.index ?? 0,
                    accepted: event.data.accepted ?? true,
                    reason: event.data.reason ?? "",
                    cycle: event.data.cycle ?? 0,
                },
            ],
        };
    }
    return state;
}
// ── Status icon helper ─────────────────────────────────────────────
function statusIcon(status) {
    switch (status) {
        case "done":
            return "✓";
        case "failed":
            return "✗";
        case "running":
            return "●";
        case "retrying":
            return "↻";
        case "awaiting_confirmation":
            return "⏸";
        case "blocked":
            return "⊘";
        case "pending":
        default:
            return "○";
    }
}
function statusColor(status) {
    switch (status) {
        case "done":
            return "#4ade80"; // green
        case "failed":
            return "#f87171"; // red
        case "running":
            return "#60a5fa"; // blue
        case "retrying":
            return "#fbbf24"; // amber
        case "awaiting_confirmation":
            return "#a78bfa"; // purple
        case "blocked":
            return "#6b7280"; // gray
        case "pending":
        default:
            return "#9ca3af"; // light gray
    }
}
export const TaskChecklist = ({ plan }) => {
    if (!plan.active || plan.tasks.length === 0)
        return null;
    const completedCount = plan.tasks.filter((t) => t.status === "done").length;
    const totalCount = plan.tasks.length;
    const allDone = completedCount === totalCount;
    const hasFailed = plan.tasks.some((t) => t.status === "failed");
    return (_jsxs("div", { style: {
            margin: "8px 0",
            padding: "12px 16px",
            borderRadius: "10px",
            background: "rgba(30, 30, 46, 0.85)",
            border: "1px solid rgba(255,255,255,0.08)",
            fontFamily: "'Inter', 'SF Pro', system-ui, sans-serif",
            fontSize: "13px",
            maxWidth: "480px",
        }, children: [_jsxs("div", { style: {
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    marginBottom: "10px",
                    paddingBottom: "8px",
                    borderBottom: "1px solid rgba(255,255,255,0.06)",
                }, children: [_jsx("span", { style: { color: "#e2e8f0", fontWeight: 600, fontSize: "13px" }, children: "Task Plan" }), _jsxs("span", { style: {
                            color: allDone ? "#4ade80" : hasFailed ? "#f87171" : "#94a3b8",
                            fontSize: "12px",
                            fontWeight: 500,
                        }, children: [completedCount, "/", totalCount, " ", allDone ? "complete" : "in progress"] })] }), _jsx(AnimatePresence, { children: plan.tasks.map((task) => {
                    const icon = statusIcon(task.status);
                    const color = statusColor(task.status);
                    const reflection = plan.reflections.find((r) => r.index === task.index);
                    return (_jsxs(motion.div, { initial: { opacity: 0, y: 4 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.2, delay: task.index * 0.05 }, style: {
                            display: "flex",
                            alignItems: "flex-start",
                            gap: "10px",
                            padding: "6px 0",
                        }, children: [_jsx("span", { style: {
                                    color,
                                    fontSize: "14px",
                                    lineHeight: "20px",
                                    minWidth: "18px",
                                    textAlign: "center",
                                    fontWeight: 700,
                                }, children: task.status === "running" ? (_jsx(motion.span, { animate: { opacity: [1, 0.3, 1] }, transition: { duration: 1.2, repeat: Infinity }, children: icon })) : (icon) }), _jsxs("div", { style: { flex: 1, minWidth: 0 }, children: [_jsx("div", { style: {
                                            color: task.status === "done"
                                                ? "#94a3b8"
                                                : task.status === "running"
                                                    ? "#e2e8f0"
                                                    : "#cbd5e1",
                                            lineHeight: "20px",
                                            textDecoration: task.status === "done" ? "line-through" : "none",
                                            textDecorationColor: "rgba(148,163,184,0.4)",
                                        }, children: task.description }), task.resultPreview && task.status === "done" && (_jsx("div", { style: {
                                            color: "#64748b",
                                            fontSize: "11px",
                                            marginTop: "2px",
                                            overflow: "hidden",
                                            textOverflow: "ellipsis",
                                            whiteSpace: "nowrap",
                                            maxWidth: "380px",
                                        }, children: task.resultPreview })), reflection && !reflection.accepted && (_jsxs("div", { style: {
                                            color: "#fbbf24",
                                            fontSize: "11px",
                                            marginTop: "2px",
                                            fontStyle: "italic",
                                        }, children: ["\u21BB Reflecting: ", reflection.reason] }))] })] }, task.index));
                }) })] }));
};
export default TaskChecklist;
