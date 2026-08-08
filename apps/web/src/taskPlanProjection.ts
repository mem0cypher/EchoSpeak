/**
 * Read-only projection of the selected model's descriptive TaskRun plan.
 *
 * TaskRun requirements and its execution graph own progress and completion.
 * These rows exist only to provide a compact current-step label in Chat.
 */

export type TaskPlanStepStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "retrying"
  | "awaiting_confirmation"
  | "blocked"
  | "skipped";

export interface TaskPlanStepProjection {
  index: number;
  description: string;
  tool: string;
  status: TaskPlanStepStatus;
}

export interface TaskPlanProjection {
  tasks: TaskPlanStepProjection[];
  active: boolean;
}

export function createEmptyTaskPlan(): TaskPlanProjection {
  return { tasks: [], active: false };
}

function normalizeStatus(value: unknown): TaskPlanStepStatus {
  const status = String(value || "pending").toLowerCase();
  if (["complete", "completed", "success"].includes(status)) return "done";
  if (["active", "in_progress"].includes(status)) return "running";
  if (
    ["pending_confirmation", "needs_permission", "approval_required"].includes(
      status,
    )
  ) {
    return "awaiting_confirmation";
  }
  if (
    [
      "done",
      "running",
      "failed",
      "retrying",
      "awaiting_confirmation",
      "blocked",
      "skipped",
    ].includes(status)
  ) {
    return status as TaskPlanStepStatus;
  }
  return "pending";
}

export function taskPlanReducer(
  state: TaskPlanProjection,
  event: { type: string; data?: unknown },
): TaskPlanProjection {
  if (event.type !== "task_plan" || !Array.isArray(event.data)) return state;
  return {
    tasks: event.data.map((raw: unknown, index) => {
      const item =
        raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
      return {
        index: Number.isInteger(item.index) ? Number(item.index) : index,
        description: String(item.description || item.tool || "Step"),
        tool: String(item.tool || ""),
        status: normalizeStatus(item.status),
      };
    }),
    active: true,
  };
}
