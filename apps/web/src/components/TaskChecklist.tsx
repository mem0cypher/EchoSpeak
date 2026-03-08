/**
 * TaskChecklist — Live task plan checklist for EchoSpeak v7.0.0.
 *
 * Renders an inline checklist in the chat showing real-time progress
 * of multi-step task plans. Each step shows status (pending, running,
 * done, failed, retrying, awaiting_confirmation) with icons and
 * optional result previews.
 *
 * Receives task_plan, task_step, and task_reflection NDJSON events
 * from the backend StreamBuffer.
 */

import React from "react";
import { motion, AnimatePresence } from "framer-motion";

// ── Types ──────────────────────────────────────────────────────────

export type TaskStepStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "retrying"
  | "awaiting_confirmation"
  | "blocked";

export interface TaskStep {
  index: number;
  description: string;
  tool: string;
  status: TaskStepStatus;
  resultPreview?: string;
}

export interface TaskReflection {
  index: number;
  accepted: boolean;
  reason: string;
  cycle: number;
}

export interface TaskPlanState {
  tasks: TaskStep[];
  reflections: TaskReflection[];
  active: boolean;
}

// ── Initial state factory ──────────────────────────────────────────

export function createEmptyTaskPlan(): TaskPlanState {
  return { tasks: [], reflections: [], active: false };
}

// ── Reducer for stream events ──────────────────────────────────────

export function taskPlanReducer(
  state: TaskPlanState,
  event: { type: string; data?: any },
): TaskPlanState {
  if (event.type === "task_plan" && Array.isArray(event.data)) {
    return {
      tasks: event.data.map((t: any) => ({
        index: t.index ?? 0,
        description: t.description ?? t.tool ?? "Task",
        tool: t.tool ?? "",
        status: (t.status ?? "pending") as TaskStepStatus,
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
      tasks: state.tasks.map((t) =>
        t.index === idx
          ? {
              ...t,
              status: (d.status ?? t.status) as TaskStepStatus,
              resultPreview: d.result_preview || t.resultPreview,
            }
          : t,
      ),
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

function statusIcon(status: TaskStepStatus): string {
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

function statusColor(status: TaskStepStatus): string {
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

// ── Component ──────────────────────────────────────────────────────

interface TaskChecklistProps {
  plan: TaskPlanState;
}

export const TaskChecklist: React.FC<TaskChecklistProps> = ({ plan }) => {
  if (!plan.active || plan.tasks.length === 0) return null;

  const completedCount = plan.tasks.filter((t) => t.status === "done").length;
  const totalCount = plan.tasks.length;
  const allDone = completedCount === totalCount;
  const hasFailed = plan.tasks.some((t) => t.status === "failed");

  return (
    <div
      style={{
        margin: "8px 0",
        padding: "12px 16px",
        borderRadius: "10px",
        background: "rgba(30, 30, 46, 0.85)",
        border: "1px solid rgba(255,255,255,0.08)",
        fontFamily: "'Inter', 'SF Pro', system-ui, sans-serif",
        fontSize: "13px",
        maxWidth: "480px",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "10px",
          paddingBottom: "8px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
        }}
      >
        <span style={{ color: "#e2e8f0", fontWeight: 600, fontSize: "13px" }}>
          Task Plan
        </span>
        <span
          style={{
            color: allDone ? "#4ade80" : hasFailed ? "#f87171" : "#94a3b8",
            fontSize: "12px",
            fontWeight: 500,
          }}
        >
          {completedCount}/{totalCount} {allDone ? "complete" : "in progress"}
        </span>
      </div>

      {/* Steps */}
      <AnimatePresence>
        {plan.tasks.map((task) => {
          const icon = statusIcon(task.status);
          const color = statusColor(task.status);
          const reflection = plan.reflections.find(
            (r) => r.index === task.index,
          );

          return (
            <motion.div
              key={task.index}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2, delay: task.index * 0.05 }}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: "10px",
                padding: "6px 0",
              }}
            >
              {/* Icon */}
              <span
                style={{
                  color,
                  fontSize: "14px",
                  lineHeight: "20px",
                  minWidth: "18px",
                  textAlign: "center",
                  fontWeight: 700,
                }}
              >
                {task.status === "running" ? (
                  <motion.span
                    animate={{ opacity: [1, 0.3, 1] }}
                    transition={{ duration: 1.2, repeat: Infinity }}
                  >
                    {icon}
                  </motion.span>
                ) : (
                  icon
                )}
              </span>

              {/* Content */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    color:
                      task.status === "done"
                        ? "#94a3b8"
                        : task.status === "running"
                          ? "#e2e8f0"
                          : "#cbd5e1",
                    lineHeight: "20px",
                    textDecoration:
                      task.status === "done" ? "line-through" : "none",
                    textDecorationColor: "rgba(148,163,184,0.4)",
                  }}
                >
                  {task.description}
                </div>

                {/* Result preview */}
                {task.resultPreview && task.status === "done" && (
                  <div
                    style={{
                      color: "#64748b",
                      fontSize: "11px",
                      marginTop: "2px",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      maxWidth: "380px",
                    }}
                  >
                    {task.resultPreview}
                  </div>
                )}

                {/* Reflection note */}
                {reflection && !reflection.accepted && (
                  <div
                    style={{
                      color: "#fbbf24",
                      fontSize: "11px",
                      marginTop: "2px",
                      fontStyle: "italic",
                    }}
                  >
                    ↻ Reflecting: {reflection.reason}
                  </div>
                )}
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
};

export default TaskChecklist;
