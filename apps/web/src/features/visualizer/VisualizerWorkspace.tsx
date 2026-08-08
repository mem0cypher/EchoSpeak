import React, { useEffect, useMemo, useState } from "react";
import type { AgentActivityState, SemanticRequirementActivity } from "../../agentActivity";
import { useWorkStore } from "../work/store";
import "./visualizer.css";

type VisualizerWorkspaceProps = {
  apiBase: string;
  sessionId: string;
  projectId: string;
  activity?: AgentActivityState;
};

const terminal = new Set(["completed", "cancelled", "superseded", "quarantined"]);
const completedNode = new Set(["completed", "satisfied", "skipped"]);
const activeNode = new Set(["running", "ready", "active", "in_progress"]);
const failedNode = new Set(["failed", "blocked", "quarantined", "exhausted"]);

const words = (value: unknown) =>
  String(value || "pending").replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

const clock = (value: unknown) => {
  const timestamp = Number(value || 0);
  if (!timestamp) return "";
  return new Date(timestamp * (timestamp < 1e12 ? 1000 : 1)).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
};

const statusClass = (value: unknown) => {
  const status = String(value || "pending").toLowerCase();
  if (completedNode.has(status)) return "is-complete";
  if (activeNode.has(status)) return "is-active";
  if (failedNode.has(status)) return "is-failed";
  return "is-pending";
};

export function VisualizerWorkspace({
  apiBase,
  sessionId,
  projectId,
  activity: liveActivity,
}: VisualizerWorkspaceProps) {
  const {
    runs,
    selectedTaskRunId,
    detail,
    loading,
    detailLoading,
    error,
    loadRuns,
    selectRun,
    clear,
  } = useWorkStore();
  const [overview, setOverview] = useState<any>(null);

  useEffect(() => {
    if (!sessionId) {
      clear();
      return;
    }
    let disposed = false;
    const refresh = async () => {
      if (!disposed) await loadRuns(apiBase, sessionId, projectId || "");
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3500);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [apiBase, sessionId, projectId, loadRuns, clear]);

  useEffect(() => {
    if (!sessionId) {
      setOverview(null);
      return;
    }
    let disposed = false;
    const refresh = async () => {
      try {
        const response = await fetch(
          `${apiBase}/studio/overview?session_id=${encodeURIComponent(sessionId)}`,
        );
        if (!response.ok) return;
        const payload = await response.json();
        if (!disposed) setOverview(payload);
      } catch {
        // Background refresh failure must not blank the last verified view.
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [apiBase, sessionId]);

  const selected = useMemo(
    () => runs.find((run) => run.id === selectedTaskRunId) || runs[0] || null,
    [runs, selectedTaskRunId],
  );
  const activeRun = useMemo(
    () => runs.find((run) => !terminal.has(String(run.status || "").toLowerCase())) || selected,
    [runs, selected],
  );

  useEffect(() => {
    if (
      activeRun &&
      activeRun.id !== selectedTaskRunId &&
      sessionId
    ) {
      void selectRun(apiBase, sessionId, projectId || "", activeRun.id);
    }
  }, [activeRun, selectedTaskRunId, apiBase, sessionId, projectId, selectRun]);

  const nodes = Array.isArray(detail?.stage?.nodes) ? detail.stage.nodes : [];
  const completeCount = nodes.filter((node: any) =>
    completedNode.has(String(node.status || "").toLowerCase()),
  ).length;
  const liveRequirements = Array.isArray(liveActivity?.requirements)
    ? liveActivity.requirements
    : [];
  const durableRequirements: SemanticRequirementActivity[] = useMemo(() => {
    if (!detail || !Array.isArray(detail.requirements)) return [];
    const states = (detail.requirement_states || {}) as Record<string, any>;
    return detail.requirements.map((requirement: any) => {
      const state = states[String(requirement.requirement_id || "")] || {};
      return {
        label: String(requirement.objective || requirement.requested_operation || "Requirement"),
        kind: String(requirement.kind || ""),
        status: String(state.status || "pending"),
        missing_fields: Array.isArray(state.missing_fields) ? state.missing_fields : [],
        attempt_count: Array.isArray(state.attempt_ids) ? state.attempt_ids.length : 0,
        retry_count: Number(state.retry_count || 0),
        source_count: Number(state.source_count || 0),
        required: requirement.required !== false,
      };
    });
  }, [detail]);
  const visibleRequirements = liveActivity?.streaming && liveRequirements.length
    ? liveRequirements
    : durableRequirements;
  const requiredRequirements = visibleRequirements.filter((item) => item.required !== false);
  const satisfiedRequirements = requiredRequirements.filter((item) => item.status === "satisfied");
  const liveRequirementIndex = visibleRequirements.findIndex((item) =>
    ["active", "pending", "weak"].includes(String(item.status || "").toLowerCase()),
  );
  const progress = requiredRequirements.length
    ? Math.round((satisfiedRequirements.length / requiredRequirements.length) * 100)
    : nodes.length
      ? Math.round((completeCount / nodes.length) * 100)
      : detail?.completion?.finalizable
        ? 100
        : 0;
  const currentNode =
    nodes.find((node: any) => activeNode.has(String(node.status || "").toLowerCase())) ||
    nodes.find((node: any) => !completedNode.has(String(node.status || "").toLowerCase())) ||
    nodes[nodes.length - 1] ||
    null;
  const currentStep = currentNode ? Math.max(1, nodes.indexOf(currentNode) + 1) : 0;

  const durableActivity = useMemo(() => {
    if (!detail) return [];
    return [
      ...detail.executions.map((row: any) => ({
        key: `execution-${row.id}`,
        label: words(row.phase || row.status || "Execution"),
        status: row.status,
        at: row.updated_at || row.created_at,
      })),
      ...detail.tool_runs.map((row: any) => ({
        key: `tool-${row.id}`,
        label: `${words(row.tool || row.tool_name || "Tool")} ${words(row.status)}`,
        status: row.status,
        at: row.updated_at || row.created_at,
      })),
      ...(detail.specialist_runs || []).map((row: any) => ({
        key: `specialist-${row.id}`,
        label: `${words(row.runtime_id || "Specialist")} ${words(row.status)}`,
        status: row.status,
        at: row.updated_at || row.created_at,
      })),
    ]
      .sort((a, b) => Number(a.at || 0) - Number(b.at || 0))
      .slice(-7);
  }, [detail]);
  const activity = useMemo(() => {
    const live = (liveActivity?.timeline || []).map((row) => ({
      key: `live-${row.key}`,
      label: row.label,
      status: row.status,
      at: row.at,
    }));
    return [...durableActivity, ...live]
      .sort((a, b) => Number(a.at || 0) - Number(b.at || 0))
      .filter((row, index, rows) => index === 0 || row.label !== rows[index - 1].label || row.status !== rows[index - 1].status)
      .slice(-7);
  }, [durableActivity, liveActivity?.timeline]);

  const specialist = [...(detail?.specialist_runs || [])].sort(
    (a: any, b: any) => Number(b.updated_at || b.created_at || 0) - Number(a.updated_at || a.created_at || 0),
  )[0];
  const specialistActive = specialist && !terminal.has(String(specialist.status || "").toLowerCase());

  const capabilities = useMemo(() => {
    const tools = Array.isArray(overview?.tools) ? overview.tools : [];
    const connections = Array.isArray(overview?.connections) ? overview.connections : [];
    const runtimes = Array.isArray(overview?.specialist_runtimes) ? overview.specialist_runtimes : [];
    const rows: Array<{ label: string; status: string; active: boolean }> = [];
    const hasTool = (matcher: (name: string) => boolean) =>
      tools.some((tool: any) => tool.available && matcher(String(tool.name || tool.id || "").toLowerCase()));
    if (hasTool((name) => name.includes("web") || name.includes("search"))) {
      rows.push({ label: "Web", status: "Active", active: true });
    }
    if (hasTool((name) => name.startsWith("file_") || name.includes("filesystem"))) {
      rows.push({ label: "Files", status: "Active", active: true });
    }
    for (const connection of connections.slice(0, 5)) {
      rows.push({
        label: words(connection.name || connection.provider || connection.id),
        status: words(connection.health || connection.status || "Configured"),
        active: ["ready", "healthy", "active", "connected"].includes(
          String(connection.health || connection.status || "").toLowerCase(),
        ),
      });
    }
    for (const runtime of runtimes) {
      if (!["codex", "opencode"].includes(String(runtime.runtime_id || "").toLowerCase())) continue;
      rows.push({
        label: runtime.display_name || words(runtime.runtime_id),
        status: words(runtime.state),
        active: runtime.state === "available",
      });
    }
    return rows.slice(0, 8);
  }, [overview]);

  const objective = (liveActivity?.streaming && liveActivity.objective)
    || activeRun?.objective
    || "No active work";
  const objectiveDetail =
    (liveActivity?.streaming
      ? String(liveActivity.activeRequirement || "Echo is keeping this objective and its requirements together in one TaskRun.")
      : String((detail?.task as any)?.objective_detail || "")) ||
      (activeRun
        ? "Echo is keeping this objective and its requirements together in one TaskRun."
        : "Start durable work in Chat and its live execution will appear here.");
  const liveness = ((detail?.task as any)?.liveness || {}) as Record<string, any>;
  const nextAction = String(
    liveness.next_action || activeRun?.next_runtime_action || "",
  );
  const recoveryStrategy = String(liveness.recovery_strategy || "");
  const preferredTool = String(
    (liveActivity?.streaming && liveActivity.activeToolName)
      || liveness.preferred_tool_name
      || activeRun?.preferred_tool_name
      || "",
  );
  const liveCurrentStep = liveActivity?.streaming
    ? String(liveActivity.activeRequirement || liveActivity.label || "")
    : "";

  return (
    <section className="echo-visualizer" aria-label="Echo execution visualizer">
      <header className="visualizer-objective">
        <div className="visualizer-objective-copy">
          <span>Current objective</span>
          <h1>{objective}</h1>
          <p>{objectiveDetail}</p>
          {liveActivity?.streaming ? <small className="visualizer-live-mark">Live</small> : null}
        </div>
        <div className="visualizer-progress">
          <span>Progress</span>
          <strong>{progress}%</strong>
          <div><i style={{ width: `${progress}%` }} /></div>
        </div>
        <div className="visualizer-step">
          <span>Step</span>
          <strong>
            {liveActivity?.streaming && visibleRequirements.length
              ? `${Math.max(1, liveRequirementIndex + 1)} of ${visibleRequirements.length}`
              : nodes.length
                ? `${currentStep} of ${nodes.length}`
                : "—"}
          </strong>
        </div>
      </header>

      <section className="visualizer-graph-panel">
        <div className="visualizer-section-label">TaskRun execution graph</div>
        {(loading || detailLoading) && !detail ? (
          <div className="visualizer-empty">Reading the current TaskRun…</div>
        ) : error ? (
          <div className="visualizer-empty is-error">{error}</div>
        ) : nodes.length ? (
          <div className="visualizer-graph" role="list" aria-label="Execution graph nodes">
            {nodes.map((node: any, index: number) => (
              <React.Fragment key={node.node_id || `${node.kind}-${index}`}>
                {index ? <div className="visualizer-edge" aria-hidden /> : null}
                <article className={`visualizer-node ${statusClass(node.status)}`} role="listitem">
                  <div className="visualizer-node-mark" aria-hidden />
                  <h2>{node.label || words(node.kind)}</h2>
                  <span>{words(node.status)}</span>
                  {node.updated_at ? <small>{clock(node.updated_at)}</small> : null}
                </article>
              </React.Fragment>
            ))}
          </div>
        ) : (
          <div className="visualizer-empty">No execution graph is active for this conversation.</div>
        )}
        <div className="visualizer-legend" aria-label="Graph status legend">
          <span><i className="is-complete" />Completed</span>
          <span><i className="is-active" />In progress</span>
          <span><i className="is-pending" />Pending</span>
          <span><i className="is-failed" />Blocked or failed</span>
        </div>
        {visibleRequirements.length ? (
          <div className="visualizer-requirements" aria-label="Current requirements">
            {visibleRequirements.slice(0, 6).map((requirement, index) => (
              <article key={`${requirement.label}:${index}`} className={statusClass(requirement.status)}>
                <div><i aria-hidden /><span>{words(requirement.status)}</span></div>
                <strong>{requirement.label}</strong>
                <small>
                  {requirement.attempt_count} attempts · {requirement.source_count} sources
                  {requirement.missing_fields.length ? ` · ${requirement.missing_fields.length} gaps` : ""}
                </small>
              </article>
            ))}
          </div>
        ) : null}
      </section>

      <div className="visualizer-lower">
        <section className="visualizer-info-panel">
          <h2>Activity feed</h2>
          <div className="visualizer-feed">
            {activity.length ? activity.map((item) => (
              <div key={item.key}>
                <i className={statusClass(item.status)} />
                <span>{item.label}</span>
                <time>{clock(item.at)}</time>
              </div>
            )) : <p>No durable activity for this Session yet.</p>}
          </div>
        </section>

        <section className="visualizer-info-panel current-step">
          <h2>Current step details</h2>
          {currentNode || liveCurrentStep ? (
            <>
              <div className="visualizer-current-title">
                <i className={statusClass(liveActivity?.streaming ? "active" : currentNode?.status)} />
                <strong>{liveCurrentStep || currentNode?.label || words(currentNode?.kind)}</strong>
                <span>{liveActivity?.streaming ? "Live" : words(currentNode?.status)}</span>
              </div>
              <p>
                {liveActivity?.streaming && liveActivity.nextAction
                  ? `Runtime next action: ${words(liveActivity.nextAction)}${liveActivity.recoveryReason ? ` · ${liveActivity.recoveryReason}` : ""}.`
                  : nextAction
                    ? `Runtime next action: ${words(nextAction)}${recoveryStrategy ? ` using ${words(recoveryStrategy)}` : ""}.`
                    : currentNode?.outcome_code
                      ? words(currentNode.outcome_code)
                    : "Echo is working within this graph node."}
              </p>
              <dl>
                <div><dt>Stage</dt><dd>{words(detail?.stage?.workflow_stage)}</dd></div>
                <div><dt>Preferred tool</dt><dd>{preferredTool ? words(preferredTool) : "None"}</dd></div>
                <div><dt>Attempts</dt><dd>{liveActivity?.streaming ? liveActivity.attemptCount : visibleRequirements.reduce((sum, item) => sum + item.attempt_count, 0)}</dd></div>
                <div><dt>Sources</dt><dd>{liveActivity?.streaming ? liveActivity.sourceCount : visibleRequirements.reduce((sum, item) => sum + item.source_count, 0)}</dd></div>
              </dl>
              {liveActivity?.sources.length ? (
                <div className="visualizer-source-list" aria-label="Current sources">
                  {liveActivity.sources.slice(-4).map((source, index) => source.url ? (
                    <a key={`${source.url}:${index}`} href={source.url} target="_blank" rel="noreferrer">
                      {source.label}
                    </a>
                  ) : <span key={`${source.label}:${index}`}>{source.label}</span>)}
                </div>
              ) : null}
            </>
          ) : <p>No current execution step.</p>}
        </section>

        <section className="visualizer-info-panel specialist-panel">
          <h2>Codex / Specialist activity</h2>
          <div className={`specialist-glyph ${specialistActive ? "is-active" : ""}`}>&lt;/&gt;</div>
          <strong>
            {specialist
              ? `${words(specialist.runtime_id)} ${words(specialist.status)}`
              : "Codex idle"}
          </strong>
          <p>
            {specialist
              ? specialist.objective || "Specialist work is bound to the current TaskRun."
              : "Coding is not currently required for this objective."}
          </p>
        </section>

        <section className="visualizer-info-panel capability-panel">
          <h2>Connectors & capabilities</h2>
          <div>
            {capabilities.length ? capabilities.map((capability) => (
              <div key={capability.label}>
                <span>{capability.label}</span>
                <small><i className={capability.active ? "is-active" : ""} />{capability.status}</small>
              </div>
            )) : <p>No configured capabilities reported.</p>}
          </div>
        </section>
      </div>
    </section>
  );
}

export default VisualizerWorkspace;
