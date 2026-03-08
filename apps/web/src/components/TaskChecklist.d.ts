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
export type TaskStepStatus = "pending" | "running" | "done" | "failed" | "retrying" | "awaiting_confirmation" | "blocked";
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
export declare function createEmptyTaskPlan(): TaskPlanState;
export declare function taskPlanReducer(state: TaskPlanState, event: {
    type: string;
    data?: any;
}): TaskPlanState;
interface TaskChecklistProps {
    plan: TaskPlanState;
}
export declare const TaskChecklist: React.FC<TaskChecklistProps>;
export default TaskChecklist;
