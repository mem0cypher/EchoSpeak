import React from "react";

export type OperationalStep = {
  index?: number;
  description?: string;
  tool?: string;
  status?: string;
  result_preview?: string;
};

export type OperationalThreadState = {
  thread_id: string;
  workspace_id?: string;
  active_project_id?: string;
  workspace_root?: string;
  project_path?: string;
  objective?: string;
  current_subject?: string;
  mode?: string;
  phase?: string;
  permissions?: Record<string, boolean>;
  constraints?: string[];
  completed_actions?: Record<string, any>[];
  pending_actions?: Record<string, any>[];
  failed_actions?: Record<string, any>[];
  plan_steps?: OperationalStep[];
  retry_target?: Record<string, any>;
  last_tool_outcome?: Record<string, any>;
  operation_details?: Record<string, any>;
  continuity_notice?: string;
  execution_status?: string;
  safest_next_action?: string;
  current_execution_id?: string;
  pending_approval_id?: string;
  last_execution_id?: string;
  ledger?: Record<string, any>[];
};

export type OperationalApproval = {
  id: string;
  thread_id: string;
  execution_id?: string | null;
  status: string;
  tool: string;
  kwargs?: Record<string, any>;
  preview?: string;
  summary?: string;
  risk_level?: string;
  policy_flags?: string[];
  session_permissions?: Record<string, boolean>;
  permission_level?: string;
  constraints?: string[];
  execution_context?: Record<string, any>;
};

type Props = {
  state?: OperationalThreadState | null;
  approval?: OperationalApproval | null;
  success?: boolean | null;
  busy?: boolean;
  /** Tighter spacing when stacked under a chat bubble near time/tok meta */
  compact?: boolean;
  onDecision?: (approvalId: string, decision: "confirm" | "cancel") => void;
};

const STATUS_LABELS: Record<string, string> = {
  complete: "Complete",
  partially_complete: "Partially complete",
  needs_permission: "Needs approval",
  needs_approval: "Needs approval",
  needs_clarification: "Needs clarification",
  blocked: "Blocked",
  failed: "Failed",
  retryable: "Can retry",
  cancelled: "Cancelled",
  in_progress: "In progress",
};

const ACTIVITY_LABELS: Record<string, string> = {
  "coding:inspect": "Inspecting project",
  "coding:plan": "Preparing a proposal",
  "coding:implement": "Preparing changes",
  "coding:verify": "Verifying changes",
  task_research: "Researching sources",
  chat: "Working",
};

const permissionKey = (flag: string): string => {
  const value = String(flag || "").toUpperCase();
  if (value === "ENABLE_SYSTEM_ACTIONS") return "system_actions";
  if (value === "ALLOW_FILE_WRITE") return "file_write";
  if (value === "ALLOW_TERMINAL_COMMANDS") return "terminal";
  if (value === "ALLOW_DESKTOP_AUTOMATION") return "desktop";
  if (value === "ALLOW_PLAYWRIGHT") return "playwright";
  return value.toLowerCase();
};

const safeTarget = (approval: OperationalApproval): string => {
  const args = approval.kwargs || {};
  for (const key of ["path", "src", "dst", "cwd", "url", "app", "recipient", "service"]) {
    const value = String(args[key] || "").trim();
    if (value) return value.slice(0, 220);
  }
  return String(approval.execution_context?.project_path || approval.execution_context?.workspace_root || "Current task scope");
};

const stepLabel = (status: string): string => {
  const value = String(status || "pending").toLowerCase();
  if (["done", "completed", "complete"].includes(value)) return "Complete";
  if (["running", "active", "in_progress"].includes(value)) return "Active";
  if (["awaiting_confirmation", "pending_confirmation", "needs_permission"].includes(value)) return "Awaiting approval";
  if (value === "failed") return "Failed";
  if (value === "blocked") return "Blocked";
  if (value === "skipped") return "Skipped";
  if (value === "retrying") return "Retrying";
  return "Pending";
};

export const OperationalStateCard: React.FC<Props & { executionId?: string | null }> = ({
  state,
  approval,
  success,
  busy = false,
  compact = false,
  onDecision,
  executionId,
}) => {
  if (!state && !approval) return null;
  const mode = String(state?.mode || "chat");
  const phase = String(state?.phase || "");
  const status = String(state?.execution_status || (success === false ? "failed" : success === true ? "complete" : ""));
  const steps = Array.isArray(state?.plan_steps) ? state!.plan_steps! : [];
  // Strict Turn scope: never show Session-wide actions under the wrong Turn.
  const owner =
    String(executionId || state?.current_execution_id || state?.last_execution_id || "").trim();
  const scoped = (items: Record<string, any>[]) =>
    items.filter((item) => {
      const eid = String(item?.execution_id || "").trim();
      if (!owner) return !eid; // only unassigned legacy if no owner
      if (!eid) return false; // do not guess ownership
      return eid === owner;
    });
  const completed = scoped(Array.isArray(state?.completed_actions) ? state!.completed_actions! : []);
  const failed = scoped(Array.isArray(state?.failed_actions) ? state!.failed_actions! : []);
  const pending = scoped(Array.isArray(state?.pending_actions) ? state!.pending_actions! : []);
  // Chat turns with no turn-owned progress must not show research/progress chrome.
  const show = Boolean(
    approval ||
      (mode !== "chat" && (steps.length || completed.length || failed.length || pending.length)) ||
      (mode === "chat" && (completed.length || failed.length || pending.length)) ||
      (status && !["", "ready", "complete"].includes(status) && (mode !== "chat" || completed.length || failed.length)),
  );
  if (!show) return null;

  const activity = approval
    ? "Waiting for approval"
    : mode === "chat"
      ? STATUS_LABELS[status] || "Working"
      : ACTIVITY_LABELS[`${mode}:${phase}`] || ACTIVITY_LABELS[mode] || STATUS_LABELS[status] || "Working";
  // Never show research subject chrome for chat/utility turns.
  const contextLabel = mode === "coding" && state?.project_path
    ? `Active project: ${String(state.project_path).split(/[\\/]/).filter(Boolean).pop()}`
    : mode === "task_research" && state?.current_subject
      ? `Research subject: ${state.current_subject}`
      : "";
  const missingFlags = (approval?.policy_flags || []).filter((flag) => {
    const key = permissionKey(flag);
    return approval?.session_permissions && approval.session_permissions[key] === false;
  });
  const canApprove = Boolean(approval && approval.status === "pending" && !missingFlags.length && onDecision && !busy);
  const sectionGap = compact ? 4 : 10;
  const innerGap = compact ? 3 : 5;

  return (
    <section
      aria-label="Task operational state"
      style={{
        marginTop: compact ? 0 : 12,
        marginBottom: 0,
        padding: compact ? "6px 8px" : "10px 12px",
        border: "1px solid rgba(255,255,255,0.1)",
        borderRadius: compact ? 6 : 10,
        background: "rgba(255,255,255,0.025)",
      }}
    >
      <div role="status" aria-live="polite" style={{ display: "flex", gap: 10, alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap" }}>
        <span style={{ fontSize: compact ? 11 : 12, fontWeight: 700 }}>{activity}</span>
        {status ? <span style={{ fontSize: 11, color: "rgba(255,255,255,0.58)" }}>{STATUS_LABELS[status] || status.replace(/_/g, " ")}</span> : null}
      </div>
      {contextLabel ? <div style={{ marginTop: innerGap, fontSize: 11, color: "rgba(255,255,255,0.45)" }}>{contextLabel}</div> : null}
      {state?.continuity_notice ? <div style={{ marginTop: innerGap, fontSize: 11, color: "rgba(147,197,253,0.9)" }}>{state.continuity_notice}</div> : null}

      {approval ? (
        <div style={{ marginTop: sectionGap, paddingTop: sectionGap, borderTop: "1px solid rgba(255,255,255,0.08)" }}>
          <div style={{ fontSize: 12, fontWeight: 650 }}>{approval.summary || approval.preview || `Run ${approval.tool}`}</div>
          <div style={{ marginTop: innerGap, fontSize: 11, color: "rgba(255,255,255,0.55)", overflowWrap: "anywhere" }}>
            {approval.permission_level === "read" ? "Read-only" : "Modifying"} · {safeTarget(approval)} · {approval.risk_level || "safe"} risk
          </div>
          {missingFlags.length ? (
            <div style={{ marginTop: innerGap, fontSize: 11, color: "#fbbf24" }}>
              Configuration required: {missingFlags.join(", ")}. This is an EchoSpeak policy setting, not evidence of a Windows administrator or signature block.
            </div>
          ) : null}
          <div style={{ display: "flex", gap: 8, marginTop: sectionGap }}>
            <button type="button" disabled={!canApprove} onClick={() => approval && onDecision?.(approval.id, "confirm")} aria-label={`Approve ${approval.tool}`}>
              {busy ? "Working…" : "Approve"}
            </button>
            <button type="button" disabled={busy || approval.status !== "pending"} onClick={() => onDecision?.(approval.id, "cancel")} aria-label={`Decline ${approval.tool}`}>
              Decline
            </button>
          </div>
        </div>
      ) : null}

      {(steps.length || completed.length || failed.length || pending.length || state?.safest_next_action) ? (
        <details style={{ marginTop: sectionGap }}>
          <summary style={{ cursor: "pointer", fontSize: 11, color: "rgba(255,255,255,0.62)" }}>Progress and details</summary>
          {steps.length ? (
            <ol style={{ margin: compact ? "4px 0 0" : "8px 0 0", paddingLeft: 20 }}>
              {steps.map((step, index) => (
                <li key={`${step.index ?? index}-${step.description || step.tool}`} style={{ margin: compact ? "2px 0" : "4px 0", fontSize: 11 }}>
                  {step.description || step.tool || "Task"} <span style={{ color: "rgba(255,255,255,0.45)" }}>— {stepLabel(step.status || "pending")}</span>
                </li>
              ))}
            </ol>
          ) : null}
          {[...completed, ...failed, ...pending].slice(-12).map((item, index) => (
            <div key={`${item.execution_id || "action"}-${index}`} style={{ marginTop: innerGap, fontSize: 11, color: item.success === false ? "#fca5a5" : "rgba(255,255,255,0.56)" }}>
              {String(item.summary || item.tool || item.status || "Action")}
              {item.verified ? " · verified" : ""}
            </div>
          ))}
          {Object.entries(state?.operation_details || {}).map(([label, value]) => {
            if (value == null || value === "" || (Array.isArray(value) && !value.length)) return null;
            const text = Array.isArray(value)
              ? value.join(", ")
              : typeof value === "object"
                ? String(value.summary || value.status || "")
                : String(value);
            if (!text) return null;
            return (
              <div key={label} style={{ marginTop: innerGap, fontSize: 11, color: "rgba(255,255,255,0.52)", overflowWrap: "anywhere" }}>
                <span style={{ color: "rgba(255,255,255,0.72)" }}>{label.replace(/_/g, " ")}:</span> {text}
              </div>
            );
          })}
          {/* Next-action only when it belongs to this Turn's non-complete status */}
          {state?.safest_next_action && status && !["", "ready", "complete"].includes(status) ? (
            <div style={{ marginTop: innerGap, fontSize: 11, color: "rgba(255,255,255,0.5)" }}>
              Next: {state.safest_next_action}
            </div>
          ) : null}
        </details>
      ) : null}
    </section>
  );
};

export const CapabilityRegistryGroups: React.FC<{ registry?: Record<string, any> | null }> = ({ registry }) => {
  const entries = Object.entries(registry || {});
  if (!entries.length) return null;
  const groups: Record<string, [string, any][]> = {
    "Available now": [],
    "Available through tools or multiple steps": [],
    "Requires permission": [],
    "Requires configuration": [],
    "Unsupported": [],
  };
  for (const entry of entries) {
    const spec = entry[1] || {};
    const status = String(spec.status || "unsupported");
    if (status === "direct") groups["Available now"].push(entry);
    else if (status === "tool_supported" && Array.isArray(spec.permissions) && spec.permissions.length) groups["Requires permission"].push(entry);
    else if (status === "tool_supported") groups["Available through tools or multiple steps"].push(entry);
    else if (status === "blocked_configuration") groups["Requires configuration"].push(entry);
    else groups.Unsupported.push(entry);
  }
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 8, marginBottom: 16 }}>
      {Object.entries(groups).filter(([, items]) => items.length).map(([label, items]) => (
        <section key={label} style={{ padding: 10, border: "1px solid rgba(255,255,255,0.1)", borderRadius: 10, background: "rgba(255,255,255,0.025)" }}>
          <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 7 }}>{label}</div>
          {items.map(([name, spec]) => (
            <div key={name} style={{ marginTop: 5, fontSize: 10.5, color: "rgba(255,255,255,0.58)" }}>
              <span style={{ color: "rgba(255,255,255,0.86)" }}>{String(name).replace(/_/g, " ")}</span>
              {spec.supported_task ? ` — ${spec.supported_task}` : ""}
            </div>
          ))}
        </section>
      ))}
    </div>
  );
};

export default OperationalStateCard;
