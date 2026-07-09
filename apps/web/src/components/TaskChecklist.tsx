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
    // Never show raw file bodies / ECHO wrappers in the checklist
    let preview = String(d.result_preview || "").trim();
    if (preview) {
      preview = preview
        .replace(/<<<ECHO_FILE\b[^>]*>>>/gi, "")
        .replace(/<<<END_ECHO_FILE>>>/gi, "")
        .replace(/^(Read|Wrote|Appended)\s+\d+\s+chars\b.*$/im, "")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, 90);
      // Drop if it still looks like source code dump
      if (preview.length > 80 || /[{};]|function\s|const\s|=>/.test(preview)) {
        if (!/chars|saved|listed|scanned|present|done/i.test(preview)) {
          preview = preview.slice(0, 48) + (preview.length > 48 ? "…" : "");
        }
      }
    }
    return {
      ...state,
      tasks: state.tasks.map((t) =>
        t.index === idx
          ? {
              ...t,
              status: (d.status ?? t.status) as TaskStepStatus,
              resultPreview: preview || t.resultPreview,
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
        margin: "6px 0 10px",
        padding: "2px 0",
        borderRadius: 0,
        background: "transparent",
        border: "none",
        boxShadow: "none",
        fontFamily: "'JetBrains Mono', ui-monospace, monospace",
        fontSize: "12px",
        maxWidth: "100%",
        width: "100%",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "8px",
          paddingBottom: "6px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
        }}
      >
        <span style={{ color: "rgba(255,255,255,0.55)", fontWeight: 600, fontSize: "11px", letterSpacing: "0.08em", textTransform: "uppercase" }}>
          tasks
        </span>
        <span
          style={{
            color: allDone ? "#4ade80" : hasFailed ? "#f87171" : "rgba(255,255,255,0.4)",
            fontSize: "11px",
            fontWeight: 600,
            letterSpacing: "0.04em",
          }}
        >
          {completedCount}/{totalCount}
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
                gap: "8px",
                padding: "5px 0",
              }}
            >
              {/* Icon */}
              <span
                style={{
                  color,
                  fontSize: "14px",
                  lineHeight: "18px",
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
                    overflowWrap: "anywhere",
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
                      maxWidth: "340px",
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
