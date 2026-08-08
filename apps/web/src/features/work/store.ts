import { create } from "zustand";

export type TaskRunSummary = {
  id: string;
  project_id: string;
  session_id: string;
  objective: string;
  status: string;
  workflow_stage: string;
  execution_profile: "chat" | "work" | "code";
  parent_task_run_id: string;
  handoff_context_id: string;
  requirement_statuses: Record<string, string>;
  active_graph_node_ids: string[];
  completion_finalizable: boolean;
  completion_disposition: string;
  next_runtime_action: string;
  active_requirement_id: string;
  preferred_tool_name: string;
  recovery_epoch: number;
  revision: number;
  updated_at: number;
  last_execution_id?: string;
  trigger_occurrence_id?: string;
  research_depth?: string;
};

export type TaskRunDetail = {
  task: TaskRunSummary & Record<string, unknown>;
  requirements: Array<Record<string, any>>;
  requirement_states: Record<string, Record<string, any>>;
  completion: Record<string, any> | null;
  stage: Record<string, any>;
  approvals: Array<Record<string, any>>;
  executions: Array<Record<string, any>>;
  tool_runs: Array<Record<string, any>>;
  research_artifacts: Array<Record<string, any>>;
  media_jobs: Array<Record<string, any>>;
  specialist_runs: Array<Record<string, any>>;
};

type WorkStore = {
  scopeKey: string;
  runs: TaskRunSummary[];
  selectedTaskRunId: string;
  detail: TaskRunDetail | null;
  loading: boolean;
  detailLoading: boolean;
  error: string;
  loadRuns(apiBase: string, sessionId: string, projectId: string): Promise<void>;
  selectRun(apiBase: string, sessionId: string, projectId: string, taskRunId: string): Promise<void>;
  clear(): void;
};

const scopeOf = (sessionId: string, projectId: string) => `${sessionId}\u0000${projectId}`;

const responseError = async (response: Response): Promise<string> => {
  try {
    const payload = await response.json();
    return String(payload?.detail || payload?.error || `${response.status} ${response.statusText}`);
  } catch {
    return `${response.status} ${response.statusText}`;
  }
};

export const useWorkStore = create<WorkStore>((set, get) => ({
  scopeKey: "",
  runs: [],
  selectedTaskRunId: "",
  detail: null,
  loading: false,
  detailLoading: false,
  error: "",
  clear: () => set({ scopeKey: "", runs: [], selectedTaskRunId: "", detail: null, error: "" }),
  loadRuns: async (apiBase, sessionId, projectId) => {
    const scopeKey = scopeOf(sessionId, projectId);
    const sameScope = get().scopeKey === scopeKey;
    set({
      scopeKey,
      loading: !sameScope || get().runs.length === 0,
      error: "",
      ...(sameScope ? {} : { detail: null, selectedTaskRunId: "" }),
    });
    try {
      const params = new URLSearchParams({
        session_id: sessionId,
        project_id: projectId,
        include_terminal: "true",
      });
      const response = await fetch(`${apiBase}/task-runs?${params.toString()}`);
      if (!response.ok) throw new Error(await responseError(response));
      const runs = (await response.json()) as TaskRunSummary[];
      if (get().scopeKey !== scopeKey) return;
      const current = get().selectedTaskRunId;
      const selectedTaskRunId = runs.some((item) => item.id === current)
        ? current
        : runs[0]?.id || "";
      set({ runs, selectedTaskRunId, loading: false });
      if (selectedTaskRunId) {
        await get().selectRun(apiBase, sessionId, projectId, selectedTaskRunId);
      }
    } catch (error) {
      if (get().scopeKey === scopeKey) {
        set({ loading: false, error: error instanceof Error ? error.message : String(error) });
      }
    }
  },
  selectRun: async (apiBase, sessionId, projectId, taskRunId) => {
    const scopeKey = scopeOf(sessionId, projectId);
    set({ selectedTaskRunId: taskRunId, detailLoading: true, error: "" });
    try {
      const params = new URLSearchParams({ session_id: sessionId, project_id: projectId });
      const response = await fetch(
        `${apiBase}/task-runs/${encodeURIComponent(taskRunId)}?${params.toString()}`,
      );
      if (!response.ok) throw new Error(await responseError(response));
      const detail = (await response.json()) as TaskRunDetail;
      if (get().scopeKey !== scopeKey || get().selectedTaskRunId !== taskRunId) return;
      set({ detail, detailLoading: false });
    } catch (error) {
      if (get().scopeKey === scopeKey && get().selectedTaskRunId === taskRunId) {
        set({ detail: null, detailLoading: false, error: error instanceof Error ? error.message : String(error) });
      }
    }
  },
}));
