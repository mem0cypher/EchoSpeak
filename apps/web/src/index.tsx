import React, { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { useLocation, useNavigate } from "react-router-dom";
import { create } from "zustand";
import { createEmptyTaskPlan, taskPlanReducer } from "./taskPlanProjection";
import type { TaskPlanProjection } from "./taskPlanProjection";
import type { EchoReaction, ToolCategory } from "./components/echoAnimationUtils";
import { AvatarEditor } from "./components/AvatarEditor";
import { ProjectSidebar } from "./components/ProjectSidebar";
import { MediaLibraryView } from "./features/media/MediaLibraryView.tsx";
import { VisualizerWorkspace } from "./features/visualizer/VisualizerWorkspace";
import {
  SettingsCatalog,
  type SettingsCatalogAction,
  type SettingsCatalogCard,
  type SettingsCatalogCategory,
} from "./features/settings/SettingsCatalog";
import { useWorkStore } from "./features/work/store";
import { loadRuntimeLayout, runtimeGridColumns, saveRuntimeLayout } from "./runtimeLayout";
import {
  buildLiveOperationalStatus,
  canApplyFinalToChat,
  mergeFinalReply,
  shouldIncludeChatActivity,
} from "./chatPresentation";
import { STUDIO_SECTION_ORDER } from "./studioNavigation";
import {
  coerceAllowlistValue,
  commitAllowlistDraft,
  removeAllowlistEntry,
} from "./settingsAllowlist";
import { buildResearchRunFromToolEvent, normalizeResearchRun } from "./features/research/buildResearchRun";
import { useResearchStore } from "./features/research/store";
import type { ResearchRun } from "./features/research/types";
import { buildResponseRenderPlan } from "./features/responseRenderer/buildResponseRenderPlan";
import { ResponseRenderer } from "./features/responseRenderer/ResponseRenderer";
import type { ResponseRenderIntent, ResponseRenderPlan } from "./features/responseRenderer/types";
import { buildChatEmbeds } from "./features/embeds/buildChatEmbeds";
import { ChatEmbeds, ChatEmbedFooter } from "./features/embeds/ChatEmbeds";
import type { ChatEmbed } from "./features/embeds/types";
import { CapabilityRegistryGroups, OperationalStateCard } from "./features/operations/OperationalStateCard";
import type { OperationalApproval, OperationalThreadState } from "./features/operations/OperationalStateCard";
import {
  createEchoSpeakWebSocket,
  controlDesktopWindow,
  getEchoSpeakApiBase,
  isDesktopRuntime,
  openDesktopCompanionWindow,
  openDesktopSettingsWindow,
  pickDesktopConnectionFolder,
  pickDesktopProjectFolder,
} from "./desktop/bridge";
import {
  LocalVoiceInput,
  localVoicePlayback,
} from "./voiceTransport";
import type {
  SpeechScope,
  VoiceTranscript,
  VoiceTransportPhase,
} from "./voiceTransport";
import { canApplySessionHistory, ownsStreamCleanup } from "./desktop/sessionProjection";
import {
  desktopExecutionProfile,
  desktopWorkspaceForView,
  desktopWorkspaceLabel,
  desktopVisualizerPanelLabel,
  isDesktopContextualSurface,
  type DesktopSidebarView,
  type DesktopVisualizerPanel,
  type DesktopWorkspaceSurface,
} from "./desktop/workspaceState";
import {
  activityActionsFromStreamEvent,
  agentActivityReducer,
  initialAgentActivity,
  isStreamThreadCurrent,
  toolCategoryFromPhase,
  type AgentActivityState,
  type SemanticActivityEvent,
} from "./agentActivity";

// Types
type Role = "user" | "assistant";
type DocSource = {
  id: string;
  filename?: string;
  source?: string;
  chunk?: number;
};
type RuntimeSettingsEnvelope = {
  settings: Record<string, any>;
  overrides: Record<string, any>;
  issues?: { key: string; message: string; severity: "error" | "warning" }[];
};
type SettingsTestResult = {
  ok: boolean;
  target: string;
  message: string;
  latency_ms?: number;
};
type StreamEvent =
  | { type: "partial"; text: string }
  | { type: "final"; text: string }
  | { type: "error"; message: string };

type MessageUsage = {
  /** Estimated tokens in this bubble */
  tokens: number;
  /** Estimated session/context tokens used after this message */
  contextUsed: number;
  /** Configured context window at send time */
  contextWindow: number;
  provider?: string;
  model?: string;
};

type Message = {
  id: string;
  role: Role;
  text: string;
  at: number;
  usage?: MessageUsage;
  /** True when the reply was already shown live via stream tokens — skip typewriter re-reveal */
  skipTypewriter?: boolean;
  /**
   * Multi-beat: "partial" = first spoken mid-turn line (tools must render below it).
   * "final" = post-tool answer. Used for timeline ordering only.
   */
  streamBeat?: "partial" | "final";
  /** Structured assistant response plan. Plain text remains the fallback. */
  renderPlan?: ResponseRenderPlan;
  /** Research / weather / source embeds under the final answer. */
  embeds?: ChatEmbed[];
  /** Authoritative backend state captured for this assistant turn. */
  operation?: { state: OperationalThreadState; success: boolean; executionId?: string };
  /** Retrieval sources captured for this exact assistant turn. */
  docSources?: DocSource[];
  /** Durable Turn / execution id from backend (maps client stream key → history). */
  executionId?: string;
  /** Client stream key used while the Turn was open (debugging correlation). */
  clientRequestId?: string;
};

/** Rough client-side token estimate (chars / 3.5) — matches context meter */
const estimateTokens = (text: string): number =>
  Math.max(0, Math.round(String(text || "").length / 3.5));

/** Tools that fire every turn and would spam the chat activity list. */
const SILENT_CHAT_TOOLS = new Set([
  "get_system_time",
  "project_update_context",
]);

/** Human label for any tool including MCP (`mcp__server__tool`). */
const formatToolDisplayName = (rawName: string): string => {
  const name = String(rawName || "").trim();
  if (!name) return "tool";
  if (name.startsWith("mcp__")) {
    const parts = name.split("__").filter(Boolean);
    // mcp, server, tool...
    if (parts.length >= 3) {
      const server = parts[1].replace(/_/g, " ");
      const tool = parts.slice(2).join("__").replace(/_/g, " ");
      return `MCP · ${server} · ${tool}`;
    }
    return `MCP · ${name.replace(/^mcp__/, "").replace(/__/g, " · ").replace(/_/g, " ")}`;
  }
  const known: Record<string, string> = {
    web_search: "Web search",
    sports_live: "Live sports",
    file_read: "File read",
    file_write: "File write",
    file_list: "File list",
    file_delete: "File delete",
    file_move: "File move",
    file_copy: "File copy",
    file_mkdir: "Make folder",
    terminal_run: "Terminal",
    artifact_write: "Artifact write",
    notepad_write: "Notepad",
    browse_task: "Browse page",
    youtube_transcript: "YouTube transcript",
    vision_qa: "Vision",
    analyze_screen: "Screen OCR",
    take_screenshot: "Screenshot",
    calculate: "Calculate",
    system_info: "System info",
    daily_briefing: "Daily briefing",
    discord_read_channel: "Discord read",
    discord_send_channel: "Discord send",
  };
  if (known[name]) return known[name];
  return name.replace(/_/g, " ");
};

/** Pull a short human preview from tool input (JSON / query= / free text). */
const previewToolInput = (rawName: string, rawInput: string, maxLen = 100): string => {
  let inputPreview = String(rawInput || "").replace(/\s+/g, " ").trim();
  if (!inputPreview) return "";
  const m =
    inputPreview.match(/['"]query['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/['"]path['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/['"]command['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/['"]url['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/['"]channel['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/['"]text['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/['"]input['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/['"]name['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/\bquery\s*[:=]\s*['"]([^'"]+)['"]/i) ||
    inputPreview.match(/\bpath\s*[:=]\s*['"]([^'"]+)['"]/i);
  if (m && m[1]) inputPreview = m[1].trim().replace(/['"]$/g, "");
  // Strip dict braces noise
  if (inputPreview.startsWith("{") && inputPreview.length > 80) {
    inputPreview = inputPreview.slice(0, 80);
  }
  if (inputPreview.length > maxLen) inputPreview = inputPreview.slice(0, maxLen) + "…";
  if (/how(?:'re| are) you|look great|liking how you look/i.test(inputPreview) && inputPreview.length > 100) {
    inputPreview = inputPreview.slice(0, 80) + "…";
  }
  return inputPreview;
};

const toolActivityStepType = (rawName: string): "search" | "read" | "tool" => {
  const n = String(rawName || "").toLowerCase();
  if (n === "web_search" || n === "sports_live") return "search";
  if (n === "file_read" || n === "browse_task" || n === "youtube_transcript") return "read";
  return "tool"; // includes MCP mcp__server__tool
};

/** Running / done labels for any tool (built-in + MCP). */
const formatToolActivity = (
  rawName: string,
  phase: "start" | "done" | "failed",
  opts?: { input?: string; output?: string; error?: string }
): string => {
  const name = String(rawName || "tool");
  const low = name.toLowerCase();
  const label = formatToolDisplayName(name);
  const input = previewToolInput(name, opts?.input || "");
  const out = String(opts?.output || "").replace(/\s+/g, " ").trim();
  const err = String(opts?.error || "").replace(/\s+/g, " ").trim();

  if (phase === "failed") {
    return `Failed ${label}${err ? `: ${err.slice(0, 100)}` : ""}`;
  }

  // Specialized search copy — one user-facing summary per ToolRun (no provider "via" spam).
  if (low === "web_search") {
    // Wrapper shells are not user-facing completion lines.
    if (/\(expanded to |\(superseded by canonical/i.test(out)) {
      return phase === "start" ? (input ? `Searching: ${input}` : "Searching the web…") : "";
    }
    if (phase === "start") return input ? `Searching: ${input}` : "Searching the web…";
    const insufficient =
      /search_evidence_insufficient|accepted=false/i.test(out) ||
      (/insufficient/i.test(out) && !/\d+\.\s/.test(out));
    const sources = (out.match(/^\s*\d+\.\s+/gm) || []).length;
    if (insufficient) return input ? `Search finished (weak evidence): ${input}` : "Search finished — weak evidence";
    if (sources > 0) return input ? `Search done (${sources} sources): ${input}` : `Search done (${sources} sources)`;
    return input ? `Search done: ${input}` : "Search done";
  }
  if (low === "get_system_time") {
    if (phase === "start") return "Checking the time…";
    return "Got the time";
  }
  if (low === "sports_live") {
    if (phase === "start") return input ? `Live sports: ${input}` : "Live sports data…";
    const ok = /ok\s*=\s*true/i.test(out);
    if (ok) return input ? `Live sports done: ${input}` : "Live sports done";
    return input ? `Live sports unavailable: ${input}` : "Live sports unavailable — may fall back to web";
  }

  if (phase === "start") {
    if (low.startsWith("mcp__")) {
      return input ? `${label}: ${input}` : `Using ${label}…`;
    }
    if (low === "file_read") return input ? `Reading: ${input}` : "Reading file…";
    if (low === "file_write") return input ? `Writing: ${input}` : "Writing file…";
    if (low === "terminal_run") return input ? `Running: ${input}` : "Running terminal…";
    if (low === "browse_task") return input ? `Browsing: ${input}` : "Browsing page…";
    return input ? `${label}: ${input}` : `Using ${label}…`;
  }

  // done
  if (low.startsWith("mcp__")) {
    const preview = out.slice(0, 80);
    return preview ? `${label} done — ${preview}${out.length > 80 ? "…" : ""}` : `${label} done`;
  }
  if (low === "file_read") return input ? `Read ${input}` : "File read done";
  if (low === "file_write") return input ? `Wrote ${input}` : "File write done";
  if (low === "terminal_run") {
    const code = out.match(/ExitCode\s*=\s*(-?\d+)/i)?.[1];
    return code != null ? `Terminal done (exit ${code})` : "Terminal done";
  }
  const preview = out.slice(0, 90);
  return preview ? `${label} done — ${preview}${out.length > 90 ? "…" : ""}` : `${label} done`;
};

const formatTokenCount = (n: number): string => {
  if (!Number.isFinite(n) || n < 0) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(Math.round(n));
};

const buildMessageUsage = (
  text: string,
  priorMessages: Message[],
  contextWindow: number,
  meta?: { provider?: string; model?: string }
): MessageUsage => {
  const tokens = estimateTokens(text);
  const prior = priorMessages.reduce((sum, m) => sum + (m.usage?.tokens ?? estimateTokens(m.text)), 0);
  const window = contextWindow > 0 ? contextWindow : 32768;
  return {
    tokens,
    contextUsed: prior + tokens,
    contextWindow: window,
    provider: meta?.provider,
    model: meta?.model,
  };
};

type AgentStreamEvent = (
  | { type: "tool_start"; id: string; name: string; input: string; at: number; request_id?: string }
  | { type: "tool_end"; id: string; name?: string; output: string; research?: ResearchRun; outcome?: { success: boolean; status: string; error_code?: string; error_message?: string; retryable?: boolean }; at: number; request_id?: string }
  | { type: "tool_error"; id: string; error: string; at: number; request_id?: string }
  | { type: "thinking"; content: string; at: number; request_id?: string }
  | { type: "thinking_step"; step_type: string; content: string; status: string; at: number; request_id?: string }
  | { type: "agent_token"; data: string; at: number; request_id?: string }
  | { type: "memory_saved"; memory_count: number; at: number; request_id?: string }
  | { type: "task_plan"; data: any[]; at?: number; request_id?: string }
  | { type: "partial_reply"; response: string; speak?: boolean; segment?: number; reason?: string; request_id?: string; at: number }
  | { type: "final"; response: string; spoken_text?: string; success: boolean; memory_count: number; doc_sources?: DocSource[]; research?: ResearchRun[]; response_render?: ResponseRenderIntent; execution_id?: string; trace_id?: string; thread_state?: ThreadSessionState | null; execution_projection?: Record<string, any>; partial_replies?: string[]; voice_turn_id?: string; request_id?: string; at: number }
  | { type: "turn_bound"; request_id?: string; execution_id?: string; turn_id?: string; thread_id?: string; active_project_id?: string; model?: string; reasoning_control?: Record<string, unknown>; at: number }
  | { type: "task_bound"; task_run_id: string; task_revision: number; objective?: string; active_requirement?: string; status?: string; request_id?: string; at: number }
  | { type: "iteration_boundary"; iteration: number; phase?: string; model?: string; request_id?: string; at: number }
  | { type: "token_usage"; prompt?: number; completion?: number; total?: number; request_id?: string; at: number }
  | { type: "reasoning_summary"; content: string; iteration?: number; request_id?: string; at: number }
  | { type: "recovery"; message: string; request_id?: string; at: number }
  | { type: "lifecycle"; phase: string; execution_id?: string; error?: string; request_id?: string; at?: number }
  | { type: "error"; message: string; at: number; request_id?: string }
) & { activity?: SemanticActivityEvent; seq?: number };

/** Square spinner — Echo's shape (startup-style), no generic browser spinner / emoji */
const SquareLoader: React.FC<{ size?: number; color?: string; active?: boolean }> = ({
  size = 12,
  color = "rgba(255,255,255,0.88)",
  active = true,
}) => (
  <span
    aria-hidden
    style={{
      display: "inline-block",
      width: size,
      height: size,
      borderRadius: 2,
      border: `2px solid ${color}`,
      borderTopColor: "transparent",
      animation: active ? "echo-square-spin 0.75s linear infinite" : "none",
      verticalAlign: "middle",
      flexShrink: 0,
    }}
  />
);

/** Compact live strip for the active assistant turn — operational status and interrupt controls. */
const LiveChatActivityBar: React.FC<{
  status: ReturnType<typeof buildLiveOperationalStatus>;
  activity: AgentActivityState;
  showSpinner: boolean;
  onStop?: () => void;
  onSteer?: () => void;
  onQueue?: () => void;
}> = ({ status, activity, showSpinner, onStop, onSteer, onQueue }) => {
  const [expanded, setExpanded] = useState(true);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!activity.streaming || !activity.startTime) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [activity.startTime, activity.streaming]);
  const elapsedSeconds = activity.startTime
    ? Math.max(0, Math.floor((now - activity.startTime) / 1000))
    : 0;
  const chips: { key: string; label: string; value: string }[] = [];
  if (status.task) chips.push({ key: "task", label: "Task", value: status.task });
  if (status.tool) chips.push({ key: "tool", label: "Tool", value: status.tool });
  if (status.skill) chips.push({ key: "skill", label: "Skill", value: status.skill });
  if (status.search) chips.push({ key: "search", label: "Search", value: status.search });
  if (status.verifying) chips.push({ key: "verify", label: "Status", value: "Verifying" });
  const requirementRows = activity.requirements.slice(0, 5);
  const timelineRows = activity.timeline.slice(-4);
  return (
    <div className="live-run" data-testid="chat-live-activity" data-expanded={expanded ? "true" : "false"}>
      <div className="live-run-header">
        <div className="live-run-heading">
          <SquareLoader size={12} color="rgba(255,255,255,0.9)" active={showSpinner} />
          <strong>{status.headline}</strong>
          <span className="live-run-meta">
            {elapsedSeconds}s{activity.iteration ? ` · pass ${activity.iteration}` : ""}
          </span>
        </div>
        {(onStop || onSteer || onQueue) && (
          <div className="live-run-actions">
            {onStop && (
              <button
                className="live-run-action is-stop"
                type="button"
                onClick={onStop}
                title="Stop current agent turn"
                aria-label="Stop current turn"
              >
                Stop
              </button>
            )}
            {onSteer && (
              <button
                className="live-run-action"
                type="button"
                onClick={onSteer}
                title="Steer active TaskRun with new instruction"
                aria-label="Steer active TaskRun"
              >
                Steer
              </button>
            )}
            {onQueue && (
              <button
                className="live-run-action"
                type="button"
                onClick={onQueue}
                title="Queue follow-up instruction"
                aria-label="Queue follow-up instruction"
              >
                Queue
              </button>
            )}
            <button
              className="live-run-action is-toggle"
              type="button"
              onClick={() => setExpanded((value) => !value)}
              title={expanded ? "Collapse run details" : "Expand run details"}
              aria-label={expanded ? "Collapse run details" : "Expand run details"}
            >
              {expanded ? "Less" : "More"}
            </button>
          </div>
        )}
      </div>
      {expanded && (activity.objective || activity.activeRequirement || activity.activeModel || activity.tokenUsage || activity.recoveryReason || activity.nextAction || activity.requirements.length) ? (
        <div className="live-run-details">
          {activity.objective ? <div className="live-run-detail"><span>Objective</span>{activity.objective}</div> : null}
          {activity.activeRequirement ? <div className="live-run-detail"><span>Current step</span>{activity.activeRequirement}</div> : null}
          {activity.activeModel ? <div className="live-run-detail"><span>Model</span>{activity.activeModel}</div> : null}
          {activity.tokenUsage?.total ? <div className="live-run-detail"><span>Tokens</span>{formatTokenCount(activity.tokenUsage.total)}</div> : null}
          {activity.recoveryReason ? <div className="live-run-detail"><span>Recovery</span>{activity.recoveryReason}</div> : null}
          {activity.nextAction ? <div className="live-run-detail"><span>Next</span>{activity.nextAction}</div> : null}
        </div>
      ) : null}
      {expanded && (activity.attemptCount || activity.retryCount || activity.sourceCount || activity.missingFields.length) ? (
        <div className="live-run-measures" aria-label="Current run coverage">
          <span><b>{activity.attemptCount}</b> attempts</span>
          <span><b>{activity.retryCount}</b> retries</span>
          <span><b>{activity.sourceCount}</b> sources</span>
          <span><b>{activity.missingFields.length}</b> gaps</span>
        </div>
      ) : null}
      {expanded && requirementRows.length ? (
        <div className="live-run-requirements" aria-label="Task requirements">
          {requirementRows.map((requirement, index) => (
            <div key={`${requirement.label}:${index}`} data-status={requirement.status}>
              <i aria-hidden />
              <span>{requirement.label}</span>
              <small>{requirement.status.replace(/_/g, " ")}</small>
            </div>
          ))}
        </div>
      ) : null}
      {expanded && activity.sources.length ? (
        <div className="live-run-sources" aria-label="Sources used in this run">
          <span>Sources</span>
          <div>
            {activity.sources.slice(-4).map((source, index) => source.url ? (
              <a key={`${source.url}:${index}`} href={source.url} target="_blank" rel="noreferrer">
                {source.label}
              </a>
            ) : (
              <small key={`${source.label}:${index}`}>{source.label}</small>
            ))}
          </div>
        </div>
      ) : null}
      {expanded && chips.length ? (
        <div className="live-run-trace">
          {chips.map((chip) => (
            <div key={chip.key}>
              <span>{chip.label}</span>
              <strong>{chip.value}</strong>
            </div>
          ))}
        </div>
      ) : null}
      {expanded && timelineRows.length ? (
        <div className="live-run-timeline" aria-label="Recent run activity">
          {timelineRows.map((entry) => (
            <div key={entry.key}>
              <i data-status={entry.status} aria-hidden />
              <span>{entry.label}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
};
const SettingsGroupIcon: React.FC<{ name: string }> = ({ name }) => {
  const common = {
    width: 17,
    height: 17,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  if (name === "Models") return <svg {...common}><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 9h6v6H9zM9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M20 9h3M1 15h3M20 15h3"/></svg>;
  if (name === "Search & Research") return <svg {...common}><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5M7.5 10.5h6M10.5 7.5v6"/></svg>;
  if (name === "Connections") return <svg {...common}><path d="M9.5 14.5 14.5 9M7 17l-1.5 1.5a3.5 3.5 0 0 1-5-5L5 9a3.5 3.5 0 0 1 5 0M17 7l1.5-1.5a3.5 3.5 0 0 1 5 5L19 15a3.5 3.5 0 0 1-5 0"/></svg>;
  if (name === "Voice & Speech") return <svg {...common}><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z"/><path d="M5 11v1a7 7 0 0 0 14 0v-1M12 19v3M8 22h8"/></svg>;
  if (name === "Local Tools") return <svg {...common}><path d="M14.5 6.5 17.5 3.5a4 4 0 0 1-5 5L5 16l3 3 7.5-7.5a4 4 0 0 1 5-5l-3 3z"/></svg>;
  if (name === "Skills") return <svg {...common}><path d="M12 3 4 7v10l8 4 8-4V7zM8 9h8M8 13h5"/></svg>;
  if (name === "MCP") return <svg {...common}><rect x="3" y="5" width="7" height="6" rx="1"/><rect x="14" y="13" width="7" height="6" rx="1"/><path d="M10 8h4a3 3 0 0 1 3 3v2M14 16h-4a3 3 0 0 1-3-3v-2"/></svg>;
  if (name === "Privacy & Permissions") return <svg {...common}><path d="M12 3 4.5 6v5.5c0 4.5 3 7.8 7.5 9.5 4.5-1.7 7.5-5 7.5-9.5V6L12 3Z"/><path d="m9 12 2 2 4-4"/></svg>;
  if (name === "Advanced") return <svg {...common}><path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/></svg>;
  return <svg {...common}><path d="M4 5h16v14H4zM8 9h8M8 13h5"/></svg>;
};

type GatewayEvent =
  | { type: "gateway_ready"; session_id?: string; at?: number }
  | { type: "discord_activity"; tool?: string; source?: string; at?: number }
  | { type: "spotify_playback"; is_playing?: boolean; track_id?: string; track_name?: string; track_artist?: string; duration_ms?: number; progress_ms?: number; at?: number }
  | { type: "error"; message?: string; at?: number };

type DiscordLiveEvent = {
  id: string;
  kind: "activity" | "error";
  tool?: string;
  source?: string;
  message?: string;
  at: number;
};

type ActivityItem =
  | { kind: "thinking"; id: string; content: string; at: number; steps?: ThinkingStep[]; request_id?: string }
  | { kind: "tool"; id: string; name: string; input: string; status: "running" | "done" | "error"; output?: string; at: number }
  | { kind: "memory"; id: string; memoryCount: number; at: number }
  | { kind: "error"; id: string; message: string; at: number };

type ThinkingStep = {
  id: string;
  type: "thought" | "search" | "read" | "tool";
  content: string;
  status: "running" | "done" | "failed";
  at: number;
};

type TaskPlanEntry = {
  id: string;
  at: number;
  plan: TaskPlanProjection;
  request_id?: string;
};

type TimelineItem =
  | { kind: "message"; id: string; at: number; msg: Message }
  | { kind: "activity"; id: string; at: number; item: ActivityItem };

type ProviderListItem = {
  id: string;
  name: string;
  local: boolean;
  description: string;
};

type ProviderInfo = {
  provider: string;
  model: string;
  local: boolean;
  base_url?: string | null;
  available_providers: ProviderListItem[];
  context_window?: number;
  max_output_tokens?: number;
  ready?: boolean;
  readiness_message?: string;
  readiness_detail?: string;
  session_id?: string;
  binding_revision?: number;
};

type ProviderModelsResponse = {
  provider: string;
  models: string[];
};

type MemoryItem = {
  id: string;
  text: string;
  timestamp?: string;
  metadata?: Record<string, unknown>;
  memory_type?: string;
  pinned?: boolean;
  owner_id?: string;
  scope?: string;
  source_session_id?: string;
  source_execution_id?: string;
  source_item_id?: string;
  updated_at?: string;
  index_state?: string;
  supersedes?: string;
  /** Optional projection fields from MemoryCurator / list API. */
  project_id?: string;
  project_path?: string;
  confidence?: number | string;
  source?: string;
  status?: string;
  provenance?: string;
};

type MemoryListResponse = {
  items: MemoryItem[];
  count: number;
  use_faiss: boolean;
};

type MemoryDoctorReport = {
  ok: boolean;
  memory_count: number;
  scanned: number;
  use_faiss: boolean;
  auto_store_conversations: boolean;
  type_counts: Record<string, number>;
  pinned_count: number;
  profile_fact_count: number;
  missing_type_count: number;
  duplicate_groups: { count: number; preview?: string; items?: any[] }[];
  warnings: string[];
  recommendations: string[];
};

type DocumentItem = {
  id: string;
  filename: string;
  chunks: number;
  source?: string;
  mime?: string;
  timestamp?: string;
};

type DocumentListResponse = {
  items: DocumentItem[];
  count: number;
  enabled: boolean;
};

type ThreadSessionState = OperationalThreadState & {
  thread_id: string;
  workspace_id?: string;
  active_project_id?: string;
  foreground_task_id?: string;
  suspended_task_ids?: string[];
  pending_approval_ids?: string[];
  source_metadata?: Record<string, any>;
  semantic_schema_version?: number;
  pending_approval_id?: string;
  last_execution_id?: string;
  last_trace_id?: string;
  runtime_provider?: string;
  selected_model_id?: string;
  active_turn_id?: string;
  model_profile?: Record<string, any>;
  context_budget?: Record<string, any>;
  updated_at: number;
};

type ApprovalRecord = OperationalApproval & {
  id: string;
  thread_id: string;
  execution_id?: string | null;
  status: string;
  tool: string;
  kwargs: Record<string, any>;
  original_input: string;
  preview: string;
  summary: string;
  risk_level: string;
  policy_flags: string[];
  permission_level?: string;
  constraints?: string[];
  policy_snapshot?: Record<string, any>;
  retry_count?: number;
  execution_context?: Record<string, any>;
  session_permissions: Record<string, boolean>;
  dry_run_available: boolean;
  source: string;
  workspace_id: string;
  active_project_id: string;
  created_at: number;
  updated_at: number;
  decided_at?: number | null;
  outcome_summary: string;
};

type ApprovalListResponse = {
  items: ApprovalRecord[];
  count: number;
};

type PendingActionEnvelope = {
  has_pending: boolean;
  action?: ApprovalRecord | null;
  approval_id?: string | null;
  risk_level?: string | null;
  risk_color?: string | null;
  policy_flags?: string[];
  session_permissions?: Record<string, boolean>;
  dry_run_available?: boolean;
};

type ApprovalDecisionEnvelope = {
  approval: ApprovalRecord;
  success: boolean;
  response: string;
  execution_id?: string | null;
  thread_state: ThreadSessionState;
};

type ExecutionRecord = {
  id: string;
  request_id: string;
  kind: string;
  thread_id: string;
  source: string;
  status: string;
  query: string;
  workspace_id: string;
  active_project_id: string;
  runtime_provider: string;
  created_at: number;
  updated_at: number;
  completed_at?: number | null;
  success?: boolean | null;
  response_preview: string;
  error: string;
  approvals: string[];
  tools_used: string[];
  tool_latencies_ms: { tool: string; ms: number; error?: boolean }[];
  trace_id?: string | null;
  evaluation: Record<string, any>;
  metadata: Record<string, any>;
};

type ExecutionListResponse = {
  items: ExecutionRecord[];
  count: number;
};

type VisionAnalyzeResponse = {
  text: string;
  text_length: number;
  has_text: boolean;
  image_size: Record<string, number>;
};

const openaiModelOptions = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "gpt-3.5-turbo"];
const geminiModelOptions = ["gemini-3.5-flash", "gemini-3.5-pro", "gemini-3.1-flash-lite-preview", "gemini-3.1-pro-preview", "gemini-2.5-pro"];
const listableProviders = ["ollama", "lmstudio", "localai", "vllm"];
const isLmStudioOnlyLocked = (info: ProviderInfo | null): boolean => {
  const providers = info?.available_providers || [];
  if (!providers.length) return false;
  return providers.length === 1 && providers[0].id === "lmstudio";
};

const sleepMs = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Fetch with timeout. Safe GETs may honor Retry-After once on 429; mutations never auto-replay. */
const fetchWithTimeout = async (
  url: string,
  init?: RequestInit,
  timeoutMs: number = 4500,
  options?: { retrySafeGetOn429?: boolean },
) => {
  const method = String(init?.method || "GET").toUpperCase();
  const allowRetry = options?.retrySafeGetOn429 !== false && method === "GET";
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    let response = await fetch(url, { ...init, signal: controller.signal });
    if (allowRetry && response.status === 429) {
      const retryAfterRaw = response.headers.get("Retry-After");
      const retryAfterSec = Math.min(15, Math.max(1, Number(retryAfterRaw || 2) || 2));
      await sleepMs(retryAfterSec * 1000);
      response = await fetch(url, { ...init, signal: controller.signal });
    }
    return response;
  } finally {
    clearTimeout(id);
  }
};

const normalizeTimestampMs = (value: unknown): number => {
  const num = Number(value);
  if (!Number.isFinite(num) || num <= 0) return Date.now();
  return num < 1_000_000_000_000 ? num * 1000 : num;
};

const isEmptySessionDraft = (session: { name?: string; messageCount?: number }): boolean =>
  Number(session.messageCount || 0) === 0 &&
  /^(?:new|default)?\s*(?:session|thread)(?:\s+\d+)?$/i.test(String(session.name || "").trim());

const fallbackProviders: ProviderListItem[] = [
  { id: "openai", name: "OpenAI", local: false, description: "OpenAI GPT models" },
  { id: "gemini", name: "Google Gemini", local: false, description: "Google Gemini models" },
  { id: "ollama", name: "Ollama", local: true, description: "Local Ollama models" },
  { id: "lmstudio", name: "LM Studio (GGUF direct)", local: true, description: "LM Studio (GGUF direct via OpenAI-compatible API)" },
  { id: "localai", name: "LocalAI", local: true, description: "LocalAI (OpenAI compatible)" },
  { id: "vllm", name: "vLLM", local: true, description: "vLLM (OpenAI compatible)" },
  { id: "llama_cpp", name: "llama.cpp", local: true, description: "llama.cpp (local + OpenAI compatible)" },
];

type AppState = {
  messages: Message[];
  streaming: boolean;
  listening: boolean;
  speaking: boolean;
  speechEnabled: boolean;
  speechBeat: number;
  addMessage: (msg: Message) => void;
  setStreaming: (v: boolean) => void;
  setListening: (v: boolean) => void;
  setSpeaking: (v: boolean) => void;
  setSpeechEnabled: (v: boolean) => void;
  bumpSpeechBeat: () => void;
};

const useAppStore = create<AppState>((set) => ({
  messages: [],
  streaming: false,
  listening: false,
  speaking: false,
  speechEnabled: true,
  setSpeechEnabled: (v) => set({ speechEnabled: v }),
  speechBeat: 0,
  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
  setStreaming: (v) => set({ streaming: v }),
  setListening: (v) => set({ listening: v }),
  setSpeaking: (v) => set({ speaking: v }),
  bumpSpeechBeat: () => set((s) => ({ speechBeat: s.speechBeat + 1 })),
}));

const colors = {
  bg: "#000000",
  panel: "#0a0a0a",
  panel2: "#111111",
  accent: "#ffffff",
  accentSoft: "#222222",
  text: "#ffffff",
  textDim: "#888888",
  line: "#333333",
  danger: "#ff4444",
  glow: "#ffffff",
};

type AvatarConfig = {
  body_color: string;
  eye_color: string;
  bg_color: string;
  glow_color: string;
  idle_activity: string;
  breathing_speed: number;
  eye_size: number;
  body_roundness: number;
  enable_glow: boolean;
  enable_idle_activities: boolean;
  custom_status_text: string;
};

const defaultAvatarConfig: AvatarConfig = {
  body_color: "#ffffff",
  eye_color: "#000000",
  bg_color: "#0a0a0a",
  glow_color: "#4f8eff",
  idle_activity: "auto",
  breathing_speed: 1,
  eye_size: 1,
  body_roundness: 14,
  enable_glow: true,
  enable_idle_activities: true,
  custom_status_text: "",
};

const globalCss = `
         :root {
           /* ~112.5% browser zoom (~90% of prior 125% scale); shell size compensates so layout fits viewport */
           --ui-scale: 1.125;
         }
         @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;700&family=JetBrains+Mono:wght@400;500;600&display=swap');
         * { box-sizing: border-box; }
         html, body {
           margin: 0;
           width: 100%;
           height: 100%;
           overflow: hidden;
           background: ${colors.bg};
           font-family: 'Inter', 'Manrope', system-ui, sans-serif;
           -webkit-font-smoothing: antialiased;
         }
         #root {
           width: 100%;
           height: 100%;
           overflow: hidden;
         }
         * { scrollbar-width: thin; scrollbar-color: #333 transparent; }
         *::-webkit-scrollbar { width: 8px; height: 8px; }
         *::-webkit-scrollbar-track { background: transparent; }
         *::-webkit-scrollbar-thumb { background: #333; border-radius: 0; }
         *::-webkit-scrollbar-thumb:hover { background: #444; }
         ::selection { background: #fff; color: #000; }
         
         .chat-markdown p:first-of-type { margin-top: 0; }
         .chat-markdown p:last-of-type { margin-bottom: 0; }
         .chat-markdown {
           font-size: 15px;
           line-height: 1.65;
           letter-spacing: 0.01em;
           overflow-wrap: anywhere;
           word-break: break-word;
           min-width: 0;
           max-width: 100%;
         }
         .chat-markdown pre {
           max-width: 100%;
           overflow-x: auto;
           white-space: pre;
         }
         .chat-markdown code {
           overflow-wrap: anywhere;
           word-break: break-word;
         }
         .chat-markdown img,
         .chat-markdown table {
           max-width: 100%;
         }
         .chat-markdown table {
           display: block;
           overflow-x: auto;
         }
         .chat-text {
           font-size: 15px;
           line-height: 1.65;
           letter-spacing: 0.01em;
           white-space: pre-wrap;
           overflow-wrap: anywhere;
           word-break: break-word;
           min-width: 0;
           max-width: 100%;
         }
         .chat-line-user { color: rgba(255,255,255,0.55); text-align: right; }
         .chat-line-assistant { color: rgba(255,255,255,0.92); text-align: left; min-width: 0; max-width: 100%; }
         .chat-flat { background: transparent !important; border: none !important; box-shadow: none !important; border-radius: 0 !important; backdrop-filter: none !important; min-width: 0; }
         .chat-embeds,
         .chat-embed-footer {
           width: 100%;
           max-width: 100%;
           min-width: 0;
           box-sizing: border-box;
         }
         
         /* Layout units are pre-zoom; zoom scales the whole UI to ~125% while fitting the real viewport */
         .app-shell {
           width: calc(100vw / var(--ui-scale));
           height: calc(100vh / var(--ui-scale));
           height: calc(100dvh / var(--ui-scale));
           max-width: calc(100vw / var(--ui-scale));
           max-height: calc(100vh / var(--ui-scale));
           max-height: calc(100dvh / var(--ui-scale));
           display: grid;
           grid-template-rows: minmax(0, 1fr);
           gap: 0;
           padding: 0;
           margin: 0;
           background: ${colors.bg};
           zoom: var(--ui-scale);
           transform-origin: top left;
           overflow: hidden;
         }
         .app-shell > * {
           min-width: 0;
           min-height: 0;
           max-height: 100%;
           overflow: hidden;
         }
         .echo-sidebar {
           width: 100%;
           height: 100%;
           min-width: 0;
           min-height: 0;
           overflow: hidden;
           z-index: 2;
           /* density lives on the component; do not add outer padding/gap here */
         }
         .visualizer-pane {
           display: flex;
           flex-direction: column;
           align-items: stretch;
           justify-content: flex-start;
           background: rgba(0,0,0,0.2);
           border-right: 1px solid ${colors.line};
           width: 100%;
           height: 100%;
           min-width: 0;
           min-height: 0;
           overflow: hidden;
           position: relative;
           z-index: 1;
         }
         .visualizer-pane-body {
           flex: 1 1 auto;
           min-width: 0;
           min-height: 0;
           width: 100%;
           height: 100%;
           overflow: hidden;
           display: flex;
           flex-direction: column;
         }
         .visualizer-pane-body.is-avatar {
           align-items: center;
           justify-content: center;
         }
         .visualizer-pane-body.is-workspace {
           align-items: stretch;
           justify-content: flex-start;
         }
         /* Video fills the visualizer column; chat stays in glow-panel (agentic shell). */
         .video-workspace-pane {
           width: 100%;
           height: 100%;
           min-width: 0;
           min-height: 0;
           max-height: 100%;
           overflow: hidden;
           display: flex;
           flex-direction: column;
           background: #000000;
           border-right: 1px solid ${colors.line};
           position: relative;
           z-index: 1;
         }
         .video-workspace-pane > * {
           flex: 1 1 auto;
           min-height: 0;
           min-width: 0;
           height: 100%;
         }
         .glow-panel {
           background: ${colors.panel};
           display: flex;
           flex-direction: column;
           width: 100%;
           height: 100%;
           min-width: 0;
           min-height: 0;
           overflow: hidden;
           transition: all 0.3s ease;
           z-index: 1;
         }
         @keyframes echo-square-spin {
           to { transform: rotate(360deg); }
         }
         @keyframes pulse {
           0%, 100% { opacity: 1; }
           50% { opacity: 0.5; }
         }
         .panel-header {
           display: none;
           align-items: center;
           justify-content: flex-end;
           min-height: 44px;
           padding: 9px 16px;
           border-bottom: 1px solid ${colors.line};
         }
         .panel-header .title {
           display: none;
           gap: 14px;
           align-items: center;
           font-family: 'Space Grotesk', sans-serif;
           font-size: 22px;
           font-weight: 700;
           letter-spacing: -0.02em;
           color: ${colors.text};
         }
         .panel-dot {
           width: 14px;
           height: 14px;
           background: #fff;
           border-radius: 0;
         }
         .panel-body {
          flex: 1 1 auto;
          display: flex;
          flex-direction: column;
          padding: 18px 10px 16px 14px;
          overflow: hidden;
          min-height: 0;
          min-width: 0;
          gap: 14px;
          width: 100%;
          height: 100%;
          box-sizing: border-box;
        }
        .research-panel {
          position: relative;
          display: flex;
          flex-direction: column;
          height: 100%;
          flex: 1;
          overflow: hidden;
          min-height: 0;
          gap: 12px;
        }
        .tab-bar {
          display: flex;
          flex-wrap: nowrap;
           gap: 10px;
           padding-bottom: 10px;
           border-bottom: 1px solid rgba(255,255,255,0.08);
           overflow-y: visible;
           overflow-x: auto;
         }
         .top-tab-groups {
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 10px;
          width: 100%;
          min-width: 0;
        }
         .top-tab-group {
           position: relative;
           display: flex;
           align-items: center;
           min-width: 0;
         }
         .top-tab-group .tab-button {
           width: 100%;
           min-height: 48px;
           justify-content: center;
         }
         .tab-button {
           padding: 6px 12px;
           background: transparent;
           border: 1px solid transparent;
           border-radius: 999px;
           color: ${colors.textDim};
           font-size: 13px;
           font-weight: 500;
           cursor: pointer;
           transition: all 0.2s ease;
           position: relative;
         }
         .tab-button:hover {
           color: ${colors.text};
           background: rgba(255,255,255,0.05);
         }
         .tab-button.active {
           color: #fff;
           background: linear-gradient(135deg, rgba(45,108,255,0.2), rgba(45,108,255,0.05));
           border: 1px solid rgba(140,180,255,0.3);
           box-shadow: 0 4px 12px -4px rgba(45,108,255,0.3), inset 0 1px 0 rgba(255,255,255,0.1);
           text-shadow: 0 0 12px rgba(140,180,255,0.6);
         }
         @media (max-width: 1180px) {
           .top-tab-groups {
             display: flex;
             gap: 8px;
             overflow-x: auto;
           }
           .top-tab-group {
             flex: 0 0 auto;
           }
           .top-tab-group .tab-button {
             width: auto;
             min-width: max-content;
           }
         }
         .tab-group {
           display: flex;
           flex-wrap: wrap;
           gap: 4px;
           padding: 4px;
           background: rgba(255,255,255,0.02);
           border-radius: 8px;
         }
         .tab-group-label {
           font-size: 10px;
           color: ${colors.textDim};
           text-transform: uppercase;
           letter-spacing: 0.5px;
           padding: 2px 8px;
           opacity: 0.7;
         }
         .research-scroll {
           flex: 1;
           overflow-y: auto;
           display: flex;
           flex-direction: column;
           gap: 14px;
           padding: 0;
           width: 100%;
         }
         .research-card {
           background: rgba(255,255,255,0.03);
           border: 1px solid rgba(255, 255, 255, 0.08);
           border-radius: 4px;
           padding: 16px 18px;
           transition: border-color 0.2s ease, background 0.2s ease;
           box-shadow: none;
           backdrop-filter: none;
         }
         .research-card:hover {
           border-color: rgba(255, 255, 255, 0.16);
           transform: none;
           background: rgba(255,255,255,0.045);
           box-shadow: none;
         }
         /* ── EchoSpeak Settings modal (portaled to body, no chrome bleed) ── */
         .studio-shell {
           position: fixed;
           inset: 0;
           z-index: 100000;
           width: 100vw;
           height: 100vh;
           height: 100dvh;
           max-width: 100vw;
           max-height: 100dvh;
           display: flex;
           flex-direction: column;
           background:
             radial-gradient(ellipse 80% 50% at 50% -20%, rgba(255,255,255,0.06), transparent 55%),
             radial-gradient(ellipse 40% 30% at 100% 100%, rgba(255,255,255,0.03), transparent 45%),
             #030406;
           color: #fff;
           overflow: hidden;
         }
         .app-shell.is-studio-covered {
           pointer-events: none;
           filter: blur(2px);
         }
         .studio-shell::before {
           content: "";
           pointer-events: none;
           position: absolute;
           inset: 0;
           background-image:
             linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px),
             linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px);
           background-size: 48px 48px;
           mask-image: radial-gradient(ellipse 70% 60% at 50% 40%, #000 20%, transparent 75%);
           opacity: 0.7;
         }
         .studio-top {
           position: relative;
           z-index: 2;
           display: flex;
           align-items: center;
           justify-content: space-between;
           gap: 16px;
           padding: 18px 28px 14px;
           border-bottom: 1px solid rgba(255,255,255,0.08);
           background: rgba(0,0,0,0.4);
         }
         .studio-brand {
           display: flex;
           align-items: center;
           gap: 14px;
           min-width: 0;
         }
         .studio-brand-mark {
           width: 28px;
           height: 28px;
           border-radius: 3px;
           border: 1px solid rgba(255,255,255,0.2);
           display: flex;
           align-items: center;
           justify-content: center;
           background: rgba(255,255,255,0.04);
           flex-shrink: 0;
         }
         .studio-brand-mark img {
           width: 16px;
           height: 16px;
           border-radius: 1px;
         }
         .studio-title {
           font-family: 'Space Grotesk', Inter, sans-serif;
           font-size: 13px;
           font-weight: 700;
           letter-spacing: 0.14em;
           text-transform: uppercase;
         }
         .studio-sub {
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           font-size: 10px;
           letter-spacing: 0.08em;
           color: rgba(255,255,255,0.38);
           margin-top: 2px;
         }
         .studio-x {
           width: 40px;
           height: 40px;
           border-radius: 3px;
           border: 1px solid rgba(255,255,255,0.18);
           background: transparent;
           color: #fff;
           cursor: pointer;
           display: flex;
           align-items: center;
           justify-content: center;
           transition: background 0.15s ease, border-color 0.15s ease;
           flex-shrink: 0;
         }
         .studio-x:hover {
           background: rgba(255,255,255,0.08);
           border-color: rgba(255,255,255,0.35);
         }
         .studio-nav {
           position: relative;
           z-index: 5;
           display: flex;
           align-items: stretch;
           justify-content: stretch;
           border-bottom: 1px solid rgba(255,255,255,0.06);
           background: rgba(8,8,8,0.98);
           min-width: 0;
           width: 100%;
           flex: 0 0 auto;
         }
         .studio-nav-arrow {
           width: 36px;
           flex: 0 0 36px;
           border: 0;
           border-left: 1px solid rgba(255,255,255,0.06);
           border-right: 1px solid rgba(255,255,255,0.06);
           background: rgba(8,8,8,0.98);
           color: rgba(255,255,255,0.72);
           cursor: pointer;
           font-size: 18px;
           z-index: 2;
         }
         .studio-nav-arrow:hover { color: #fff; background: rgba(255,255,255,0.06); }
         .studio-nav-inner {
           display: flex;
           gap: 0;
           overflow-x: auto;
           overflow-y: hidden;
           min-width: 0;
           flex: 1 1 auto;
           width: auto;
           max-width: none;
           padding: 0 4px;
           scrollbar-width: thin;
           scrollbar-color: rgba(255,255,255,0.25) transparent;
           -webkit-overflow-scrolling: touch;
         }
         .studio-nav-inner::-webkit-scrollbar { height: 4px; }
         .studio-nav-inner::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.22); border-radius: 999px; }
         .studio-tab {
           height: 44px;
           padding: 0 14px;
           border: none;
           border-bottom: 2px solid transparent;
           background: transparent;
           color: rgba(255,255,255,0.4);
           font-size: 12px;
           font-weight: 600;
           letter-spacing: 0.04em;
           cursor: pointer;
           white-space: nowrap;
           flex: 0 0 auto;
           transition: color 0.15s ease;
           font-family: Inter, system-ui, sans-serif;
         }
         .studio-tab:hover { color: rgba(255,255,255,0.75); }
         .studio-tab.active {
           color: #fff;
           border-bottom-color: #fff;
         }
         .studio-body {
           position: relative;
           z-index: 1;
           flex: 1;
           min-height: 0;
           display: flex;
           justify-content: center;
           overflow: hidden;
         }
         .studio-column {
           width: 100%;
           max-width: 960px;
           height: 100%;
           min-height: 0;
           display: flex;
           flex-direction: column;
           padding: 24px 28px 40px;
           box-sizing: border-box;
           overflow: hidden;
         }
         .studio-hero {
           display: flex;
           align-items: baseline;
           justify-content: space-between;
           gap: 12px;
           margin-bottom: 18px;
           padding-bottom: 14px;
           border-bottom: 1px solid rgba(255,255,255,0.06);
           flex-shrink: 0;
         }
         .studio-hero h2 {
           margin: 0;
           font-family: 'Space Grotesk', Inter, sans-serif;
           font-size: 22px;
           font-weight: 700;
           letter-spacing: -0.02em;
         }
         .studio-hero span {
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           font-size: 10px;
           letter-spacing: 0.1em;
           text-transform: uppercase;
           color: rgba(255,255,255,0.35);
         }
         .studio-dock {
           position: absolute;
           right: 24px;
           bottom: 24px;
           width: 88px;
           height: 88px;
           border-radius: 3px;
           overflow: hidden;
           border: 1px solid rgba(255,255,255,0.12);
           background: rgba(0,0,0,0.7);
           z-index: 4;
         }
         .studio-shell .research-scroll {
           align-items: stretch;
           width: 100%;
         }
          .studio-shell .research-card {
            width: 100%;
            box-sizing: border-box;
          }
          /* Reference-faithful Settings composition: title, vertical section rail, content. */
          .studio-backdrop {
            position: fixed;
            inset: 0;
            z-index: 100000;
            display: grid;
            place-items: center;
            padding: 42px;
            box-sizing: border-box;
            overflow: hidden;
            background: rgba(0,0,0,.72);
            backdrop-filter: blur(5px);
            -webkit-backdrop-filter: blur(5px);
          }
          .studio-shell {
            position: relative;
            inset: auto;
            z-index: 1;
            width: min(1060px, calc(100vw - 84px));
            height: min(760px, calc(100dvh - 84px));
            max-width: 1060px;
            max-height: 760px;
            display: grid;
            grid-template-columns: 220px minmax(0, 1fr);
            grid-template-rows: 58px 44px minmax(0, 1fr);
            background: #0a0a0a;
            border: 1px solid rgba(255,255,255,.13);
            border-radius: 12px;
            box-shadow: 0 28px 90px rgba(0,0,0,.68);
            overflow: hidden;
          }
          .studio-top {
            grid-column: 1 / -1;
            grid-row: 1;
            padding: 0 20px 0 22px;
            background: #0b0b0b;
          }
          .studio-title {
            font-family: 'Inter', 'Segoe UI Variable', sans-serif;
            font-size: 17px;
            font-weight: 520;
            letter-spacing: -.015em;
            text-transform: none;
          }
          .studio-x {
            width: 32px;
            height: 32px;
            border: 0;
          }
          .studio-nav {
            grid-column: 1;
            grid-row: 2 / 4;
            min-width: 0;
            width: auto;
            padding: 14px 12px;
            align-items: stretch;
            border-right: 1px solid rgba(255,255,255,.09);
            border-bottom: 0;
            background: #0a0a0a;
          }
          .studio-nav-inner {
            width: 100%;
            padding: 0;
            min-height: 0;
            overflow-x: hidden;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 5px;
            scrollbar-width: thin;
            scrollbar-color: rgba(255,255,255,.18) transparent;
          }
          .studio-nav .studio-tab {
            width: 100%;
            height: 42px;
            display: flex;
            align-items: center;
            gap: 11px;
            padding: 0 12px;
            border: 0;
            border-radius: 5px;
            color: rgba(255,255,255,.67);
            font-size: 12px;
            font-weight: 500;
            letter-spacing: 0;
            text-align: left;
          }
          .studio-nav .studio-tab.active {
            color: #fff;
            background: rgba(255,255,255,.08);
          }
          .studio-tab-icon {
            width: 18px;
            display: inline-grid;
            place-items: center;
            color: rgba(255,255,255,.7);
            font-size: 15px;
          }
          .studio-subnav {
            grid-column: 2;
            grid-row: 2;
            min-width: 0;
            display: flex;
            align-items: end;
            gap: 4px;
            padding: 6px 22px 0;
            overflow-x: auto;
            border-bottom: 1px solid rgba(255,255,255,.07);
            background: #0b0b0b;
          }
          .studio-subnav .studio-tab {
            height: 37px;
            padding: 0 10px;
            font-size: 10px;
            letter-spacing: .02em;
          }
          .studio-body {
            grid-column: 2;
            grid-row: 3;
            display: block;
            overflow: hidden;
          }
          .studio-column {
            max-width: none;
            padding: 20px 22px 28px;
          }
          .studio-hero {
            margin-bottom: 14px;
            padding-bottom: 12px;
          }
          .studio-hero h2 {
            font-family: 'Inter', 'Segoe UI Variable', sans-serif;
            font-size: 19px;
            font-weight: 550;
          }
          .studio-hero span {
            font-family: 'Inter', 'Segoe UI Variable', sans-serif;
            font-size: 10px;
            letter-spacing: .08em;
          }
          .settings-general-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
          }
          .settings-general-card {
            display: flex;
            flex-direction: column;
            gap: 14px;
            padding: 16px;
            border: 1px solid rgba(255,255,255,.09);
            border-radius: 8px;
            background: rgba(255,255,255,.025);
          }
          .settings-general-card-wide {
            grid-column: 1 / -1;
          }
          .settings-general-row {
            min-height: 46px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 18px;
            padding-top: 12px;
            border-top: 1px solid rgba(255,255,255,.07);
            color: #fff;
          }
          .settings-general-row > span {
            display: grid;
            gap: 4px;
          }
          .settings-general-row strong {
            font-size: 12px;
            font-weight: 550;
          }
          .settings-general-row small {
            color: rgba(255,255,255,.44);
            font-size: 10px;
            line-height: 1.4;
          }
          .settings-general-row input[type="checkbox"] {
            width: 17px;
            height: 17px;
            accent-color: #fff;
            flex: 0 0 auto;
          }
          /* One visual language across every Settings group. Existing controls
             retain their backend ownership; this layer only normalizes layout. */
          .studio-shell .research-scroll {
            gap: 12px;
            padding: 0 2px 2px 0;
            scrollbar-gutter: stable;
          }
          .studio-shell .research-card {
            margin: 0;
            padding: 15px 16px;
            border-color: rgba(255,255,255,.09);
            border-radius: 8px;
            background: rgba(255,255,255,.024);
          }
          .studio-shell .research-card:hover {
            border-color: rgba(255,255,255,.14);
            background: rgba(255,255,255,.032);
          }
          .studio-shell .research-title {
            margin-bottom: 5px;
            font-size: 13px;
            font-weight: 560;
            letter-spacing: -.005em;
          }
          .studio-shell .research-snippet {
            font-size: 11px;
            line-height: 1.55;
            color: rgba(255,255,255,.48);
          }
          .studio-shell input:not([type="checkbox"]):not([type="radio"]),
          .studio-shell select,
          .studio-shell textarea {
            min-height: 36px;
            max-width: 100%;
            box-sizing: border-box;
            border: 1px solid rgba(255,255,255,.11);
            border-radius: 5px;
            color: rgba(255,255,255,.9);
            background: #111113;
            font: 500 11px/1.4 'Inter', 'Segoe UI Variable', sans-serif;
          }
          .studio-shell input:not([type="checkbox"]):not([type="radio"]):focus,
          .studio-shell select:focus,
          .studio-shell textarea:focus {
            outline: 1px solid rgba(255,255,255,.62);
            outline-offset: 1px;
            border-color: rgba(255,255,255,.32);
          }
          .studio-shell .icon-button {
            min-height: 32px;
            border-radius: 5px;
            border-color: rgba(255,255,255,.12);
            background: rgba(255,255,255,.035);
            font: 550 11px/1 'Inter', 'Segoe UI Variable', sans-serif;
          }
          @media (max-width: 900px), (max-height: 720px) {
            .studio-backdrop {
              padding: 16px;
            }
            .studio-shell {
              width: calc(100vw - 32px);
              height: calc(100dvh - 32px);
              max-width: none;
              max-height: none;
              grid-template-columns: 184px minmax(0, 1fr);
            }
            .studio-nav {
              padding: 10px 8px;
            }
            .studio-nav .studio-tab {
              height: 38px;
              padding: 0 9px;
              font-size: 11px;
            }
            .studio-column {
              padding: 16px 18px 22px;
            }
            .studio-hero {
              margin-bottom: 11px;
              padding-bottom: 10px;
            }
          }
          @media (max-width: 720px) {
            .studio-shell {
              grid-template-columns: minmax(0, 1fr);
              grid-template-rows: 56px 51px 42px minmax(0, 1fr);
            }
            .studio-top {
              grid-column: 1;
              grid-row: 1;
              padding: 0 14px 0 16px;
            }
            .studio-nav {
              grid-column: 1;
              grid-row: 2;
              width: 100%;
              padding: 6px 8px;
              overflow-x: auto;
              border-right: 0;
              border-bottom: 1px solid rgba(255,255,255,.08);
            }
            .studio-nav-inner {
              flex-direction: row;
              gap: 4px;
              overflow-x: auto;
              overflow-y: hidden;
            }
            .studio-nav .studio-tab {
              width: auto;
              flex: 0 0 auto;
              padding: 0 10px;
            }
            .studio-subnav {
              grid-column: 1;
              grid-row: 3;
              padding: 5px 12px 0;
            }
            .studio-body {
              grid-column: 1;
              grid-row: 4;
            }
            .studio-column {
              padding: 14px 14px 20px;
            }
          }
          @media (max-width: 520px) {
            .studio-backdrop {
              padding: 0;
            }
            .studio-shell {
              width: 100vw;
              height: 100dvh;
              border: 0;
              border-radius: 0;
            }
            .settings-general-grid {
              grid-template-columns: minmax(0, 1fr);
            }
            .settings-general-card-wide {
              grid-column: auto;
            }
            .settings-general-card,
            .studio-shell .research-card {
              padding: 13px;
            }
            .settings-general-row {
              align-items: flex-start;
              gap: 12px;
            }
            .studio-hero span {
              display: none;
            }
          }
         .research-title {
           font-size: 16px;
           font-weight: 600;
           color: ${colors.text};
           margin-bottom: 8px;
           line-height: 1.4;
         }
         .research-snippet {
           font-size: 15px;
           line-height: 1.7;
           color: ${colors.textDim};
           white-space: pre-wrap;
         }
         .research-source {
           margin-top: 12px;
           font-size: 11px;
           font-weight: 500;
           color: ${colors.accent};
           text-decoration: none;
           opacity: 0.8;
           display: block;
           overflow: hidden;
           text-overflow: ellipsis;
         }
         .chat-embed-source-link:hover {
           color: rgba(255,255,255,0.62) !important;
           border-bottom-color: rgba(255,255,255,0.35) !important;
         }
         .research-source:hover {
           opacity: 1;
           text-decoration: underline;
         }
         .chat-scroll {
           flex: 1;
           overflow-y: auto;
           overflow-x: hidden;
           display: flex;
           flex-direction: column;
           gap: 12px;
           width: 100%;
           /* Right padding sits inside stable gutter so text clears the scrollbar */
           padding: 4px 16px 8px 4px;
           scrollbar-gutter: stable;
           scrollbar-width: thin;
           scrollbar-color: rgba(255,255,255,0.18) transparent;
         }
         .chat-scroll::-webkit-scrollbar {
           width: 10px;
         }
         .chat-scroll::-webkit-scrollbar-track {
           background: transparent;
           margin: 8px 0;
         }
         .chat-scroll::-webkit-scrollbar-thumb {
           background: rgba(255,255,255,0.14);
           border-radius: 2px;
           border: 2px solid transparent;
           background-clip: padding-box;
         }
         .chat-scroll::-webkit-scrollbar-thumb:hover {
           background: rgba(255,255,255,0.28);
           background-clip: padding-box;
           border: 2px solid transparent;
         }

         .live-run {
           width: 100%;
           min-width: 0;
           box-sizing: border-box;
           margin: 8px 0 12px;
           padding: 10px 12px;
           border: 1px solid rgba(255,255,255,0.09);
           border-radius: 6px;
           background: rgba(255,255,255,0.022);
         }
         .live-run-header {
           display: flex;
           align-items: center;
           justify-content: space-between;
           gap: 14px;
           min-width: 0;
         }
         .live-run-heading {
           display: flex;
           align-items: center;
           gap: 9px;
           min-width: 0;
           color: rgba(255,255,255,0.82);
           font-size: 12px;
           line-height: 1.35;
         }
         .live-run-heading > strong {
           min-width: 0;
           overflow: hidden;
           text-overflow: ellipsis;
           white-space: nowrap;
           font-weight: 560;
         }
         .live-run-meta {
           flex: 0 0 auto;
           color: rgba(255,255,255,0.36);
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           font-size: 9px;
           letter-spacing: 0.04em;
         }
         .live-run-actions {
           display: flex;
           align-items: center;
           gap: 5px;
           flex: 0 0 auto;
         }
         .live-run-action {
           min-height: 28px;
           padding: 0 9px;
           border: 1px solid rgba(255,255,255,0.12);
           border-radius: 4px;
           color: rgba(255,255,255,0.68);
           background: rgba(255,255,255,0.025);
           font: 550 10px/1 Inter, 'Segoe UI Variable', sans-serif;
           cursor: pointer;
           transition: color .15s ease, border-color .15s ease, background .15s ease;
         }
         .live-run-action:hover {
           color: #fff;
           border-color: rgba(255,255,255,0.25);
           background: rgba(255,255,255,0.07);
         }
         .live-run-action.is-stop {
           color: rgba(255,255,255,0.88);
           border-color: rgba(255,255,255,0.2);
         }
         .live-run-action.is-toggle {
           color: rgba(255,255,255,0.46);
           background: transparent;
         }
         .live-run-details {
           display: grid;
           grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
           gap: 10px 16px;
           margin-top: 10px;
           padding: 10px 0 0 21px;
           border-top: 1px solid rgba(255,255,255,0.065);
         }
         .live-run-detail {
           min-width: 0;
           color: rgba(255,255,255,0.65);
           font-size: 11px;
           line-height: 1.45;
           overflow-wrap: anywhere;
         }
         .live-run-detail > span {
           display: block;
           margin-bottom: 3px;
           color: rgba(255,255,255,0.32);
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           font-size: 8px;
           letter-spacing: 0.09em;
           text-transform: uppercase;
         }
         .live-run-measures {
           display: flex;
           flex-wrap: wrap;
           gap: 6px;
           margin: 9px 0 0 21px;
         }
         .live-run-measures > span {
           padding: 4px 7px;
           border: 1px solid rgba(255,255,255,0.075);
           border-radius: 4px;
           color: rgba(255,255,255,0.38);
           background: rgba(255,255,255,0.018);
           font: 500 8px/1.2 'JetBrains Mono', ui-monospace, monospace;
           letter-spacing: .04em;
           text-transform: uppercase;
         }
         .live-run-measures b {
           color: rgba(255,255,255,0.72);
           font-weight: 650;
         }
         .live-run-requirements,
         .live-run-timeline {
           display: grid;
           gap: 5px;
           margin: 9px 0 0 21px;
           padding-top: 8px;
           border-top: 1px solid rgba(255,255,255,0.05);
         }
         .live-run-requirements > div,
         .live-run-timeline > div {
           display: grid;
           grid-template-columns: 8px minmax(0, 1fr) auto;
           align-items: center;
           gap: 7px;
           min-width: 0;
           color: rgba(255,255,255,0.58);
           font-size: 10px;
           line-height: 1.35;
         }
         .live-run-requirements i,
         .live-run-timeline i {
           width: 5px;
           height: 5px;
           border: 1px solid rgba(255,255,255,0.35);
           border-radius: 50%;
           box-sizing: border-box;
         }
         .live-run-requirements > div[data-status="satisfied"] i,
         .live-run-timeline i[data-status="succeeded"] {
           border-color: rgba(255,255,255,0.8);
           background: rgba(255,255,255,0.8);
         }
         .live-run-requirements > div[data-status="weak"] i,
         .live-run-requirements > div[data-status="blocked"] i,
         .live-run-requirements > div[data-status="exhausted"] i,
         .live-run-timeline i[data-status="failed"] {
           border-style: dashed;
           opacity: .7;
         }
         .live-run-requirements span,
         .live-run-timeline span {
           min-width: 0;
           overflow: hidden;
           text-overflow: ellipsis;
           white-space: nowrap;
         }
         .live-run-requirements small {
           color: rgba(255,255,255,0.3);
           font: 500 8px/1 'JetBrains Mono', ui-monospace, monospace;
           letter-spacing: .05em;
           text-transform: uppercase;
         }
         .live-run-timeline > div {
           grid-template-columns: 8px minmax(0, 1fr);
           color: rgba(255,255,255,0.4);
           font-size: 9px;
         }
         .live-run-sources {
           display: grid;
           grid-template-columns: 58px minmax(0,1fr);
           gap: 8px;
           margin: 9px 0 0 21px;
           padding-top: 8px;
           border-top: 1px solid rgba(255,255,255,0.05);
         }
         .live-run-sources > span {
           color: rgba(255,255,255,0.3);
           font: 600 8px/1.5 'JetBrains Mono', ui-monospace, monospace;
           letter-spacing: .08em;
           text-transform: uppercase;
         }
         .live-run-sources > div {
           display: flex;
           flex-wrap: wrap;
           gap: 5px 10px;
           min-width: 0;
         }
         .live-run-sources a,
         .live-run-sources small {
           max-width: 230px;
           overflow: hidden;
           color: rgba(255,255,255,0.48);
           font-size: 9px;
           line-height: 1.4;
           text-decoration: none;
           text-overflow: ellipsis;
           white-space: nowrap;
         }
         .live-run-sources a:hover {
           color: rgba(255,255,255,0.82);
           text-decoration: underline;
         }
         .live-run-trace {
           display: flex;
           flex-wrap: wrap;
           gap: 5px 12px;
           margin-top: 8px;
           padding-left: 21px;
         }
         .live-run-trace > div {
           display: inline-flex;
           gap: 5px;
           min-width: 0;
           color: rgba(255,255,255,0.34);
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           font-size: 9px;
           line-height: 1.45;
         }
         .live-run-trace strong {
           max-width: 320px;
           overflow: hidden;
           color: rgba(255,255,255,0.56);
           font-weight: 500;
           text-overflow: ellipsis;
           white-space: nowrap;
         }
         .steer-backdrop {
           position: fixed;
           inset: 0;
           z-index: 100000;
           display: grid;
           place-items: center;
           padding: 20px;
           box-sizing: border-box;
           background: rgba(0,0,0,0.72);
           backdrop-filter: blur(5px);
         }
         .steer-dialog {
           width: min(460px, 100%);
           display: flex;
           flex-direction: column;
           gap: 14px;
           padding: 20px;
           box-sizing: border-box;
           border: 1px solid rgba(255,255,255,0.13);
           border-radius: 8px;
           color: #fff;
           background: #0b0b0c;
           box-shadow: 0 28px 80px rgba(0,0,0,0.68);
         }
         .steer-dialog-copy {
           display: grid;
           gap: 4px;
         }
         .steer-dialog-copy span {
           color: rgba(255,255,255,0.34);
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           font-size: 8px;
           letter-spacing: .11em;
           text-transform: uppercase;
         }
         .steer-dialog h3 {
           margin: 0;
           font-size: 17px;
           font-weight: 560;
           letter-spacing: -.01em;
         }
         .steer-dialog > p {
           margin: 0;
           color: rgba(255,255,255,0.5);
           font-size: 12px;
           line-height: 1.55;
         }
         .steer-input {
           width: 100%;
           min-height: 88px;
           box-sizing: border-box;
           resize: vertical;
           border: 1px solid rgba(255,255,255,0.13);
           border-radius: 5px;
           outline: 0;
           padding: 11px 12px;
           color: #fff;
           background: #111113;
           font: 400 13px/1.5 Inter, 'Segoe UI Variable', sans-serif;
         }
         .steer-input:focus {
           border-color: rgba(255,255,255,0.34);
           box-shadow: 0 0 0 1px rgba(255,255,255,0.08);
         }
         .steer-dialog-actions {
           display: flex;
           justify-content: flex-end;
           gap: 7px;
         }
         .steer-button {
           min-height: 34px;
           padding: 0 13px;
           border: 1px solid rgba(255,255,255,0.13);
           border-radius: 4px;
           color: rgba(255,255,255,0.7);
           background: rgba(255,255,255,0.025);
           font: 550 11px/1 Inter, 'Segoe UI Variable', sans-serif;
           cursor: pointer;
         }
         .steer-button.is-primary {
           color: #090909;
           border-color: #f2f2f2;
           background: #f2f2f2;
         }
         .steer-button:disabled {
           opacity: .38;
           cursor: default;
         }

         /* ── Composer dock ──
            Row 1: [ Ask Echo anything ........ ] [ctx] [send]
            Row 2: [ Provider ▾ ] [mic][mon][viz] [ Model ▾ ]
         */
         .input-bar {
           margin-top: auto;
           display: flex;
           flex-direction: column;
           gap: 8px;
           padding-top: 10px;
           border-top: 1px solid rgba(255,255,255,0.06);
           min-width: 0;
           width: 100%;
           overflow: visible;
         }
         .input-row {
           display: flex;
           flex-direction: row;
           flex-wrap: nowrap;
           align-items: flex-end;
           gap: 8px;
           width: 100%;
           min-width: 0;
           overflow: visible;
         }
         /* Session strip + textarea share one column so edges always line up */
         .composer-input-stack {
           flex: 1 1 auto;
           min-width: 0;
           display: flex;
           flex-direction: column;
           gap: 0;
         }
         .session-folder-strip {
           display: inline-flex;
           align-items: center;
           flex-wrap: wrap;
           gap: 12px;
           width: fit-content;
           max-width: 100%;
           box-sizing: border-box;
           padding: 5px 10px;
           margin: 0;
           font-size: 10px;
           line-height: 1.3;
           color: rgba(255,255,255,0.48);
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           border: 1px solid rgba(255,255,255,0.10);
           border-bottom: none;
           background: rgba(16,16,16,0.5);
           border-radius: 3px 3px 0 0;
           position: relative;
           z-index: 1;
         }
         .session-folder-strip.is-drop-active {
           border-color: rgba(255,255,255,0.65);
           border-style: dashed;
           background: rgba(255,255,255,0.07);
         }
         .input-field {
           box-sizing: border-box;
           width: 100%;
           min-width: 0;
           background: rgba(255,255,255,0.03);
           border: 1px solid rgba(255, 255, 255, 0.12);
           border-radius: 0 3px 3px 3px;
           padding: 10px 14px;
           color: ${colors.text};
           font-size: 15px;
           outline: none;
           transition: border-color 0.15s ease, background 0.15s ease;
           box-shadow: none;
         }
         .input-field:focus {
           background: rgba(255,255,255,0.045);
           border-color: rgba(255,255,255,0.28);
         }
         textarea.input-field {
           min-height: 40px;
           max-height: 148px;
           height: 40px;
           resize: none;
           line-height: 1.4;
           overflow-y: auto;
           font-family: inherit;
         }
         .composer-trailing {
           display: flex;
           flex-direction: row;
           flex-wrap: nowrap;
           align-items: center;
           gap: 6px;
           flex: 0 0 auto;
           overflow: visible;
         }
         .composer-square,
         .send-button,
         .mic-button {
           width: 36px;
           height: 36px;
           flex: 0 0 36px;
           display: grid;
           place-items: center;
           border-radius: 3px;
           border: 1px solid rgba(255,255,255,0.14);
           background: rgba(255,255,255,0.03);
           color: #fff;
           cursor: pointer;
           transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
           box-shadow: none;
           padding: 0;
           box-sizing: border-box;
         }
         .send-button {
           width: 40px;
           height: 40px;
           flex: 0 0 40px;
           background: rgba(255,255,255,0.08);
           border-color: rgba(255,255,255,0.22);
         }
         .composer-square:hover:not(:disabled),
         .mic-button:hover:not(:disabled) {
           background: rgba(255,255,255,0.05);
           border-color: rgba(255,255,255,0.22);
         }
         .send-button:hover:not(:disabled) {
           background: rgba(255,255,255,0.14);
           border-color: rgba(255,255,255,0.4);
         }
         .composer-square:active:not(:disabled),
         .send-button:active:not(:disabled),
         .mic-button:active:not(:disabled) {
           background: rgba(255,255,255,0.1);
         }
         .mic-button.active {
            background: rgba(255,255,255,0.12);
            border-color: rgba(255,255,255,0.42);
            color: #fff;
          }
         .composer-square.active {
           background: rgba(255,255,255,0.1);
           border-color: rgba(255,255,255,0.28);
         }
         .context-meter-wrap {
           width: 40px;
           height: 40px;
           flex: 0 0 40px;
           display: grid;
           place-items: center;
           overflow: visible;
           position: relative;
           z-index: 5;
         }

         /* Bottom rail: [mic][mon][viz] [Provider] [Model] */
         .controls-row {
           display: flex;
           flex-direction: row;
           flex-wrap: wrap;
           align-items: stretch;
           gap: 6px;
           width: 100%;
           min-width: 0;
           background: transparent;
           border: 0;
           border-radius: 0;
           overflow: visible;
         }
         .composer-primary-controls {
           display: grid;
           grid-template-columns: auto minmax(112px, .75fr) minmax(156px, 1.25fr) minmax(98px, .6fr);
           flex: 1 1 570px;
           min-width: min(100%, 520px);
           overflow: hidden;
           border: 1px solid rgba(255,255,255,0.1);
           border-radius: 4px;
           background: #0a0a0a;
         }
         .composer-mode-controls {
           display: flex;
           align-items: stretch;
           gap: 4px;
           flex: 0 0 auto;
           min-height: 48px;
           padding: 4px;
           box-sizing: border-box;
           border: 1px solid rgba(255,255,255,0.1);
           border-radius: 4px;
           background: #0a0a0a;
         }
         .control-slot {
           display: flex;
           flex-direction: column;
           align-items: stretch;
           min-width: 0;
           background: #0a0a0a;
           position: relative;
         }
         .control-slot::before {
           content: attr(data-label);
           display: block;
           padding: 6px 10px 0;
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           font-size: 9px;
           font-weight: 600;
           letter-spacing: 0.12em;
           text-transform: uppercase;
           color: rgba(255,255,255,0.32);
           line-height: 1;
         }
         .provider-slot {
           flex: 0 1 38%;
           min-width: 110px;
           max-width: 220px;
           border-right: 1px solid rgba(255,255,255,0.08);
         }
         .model-slot {
           flex: 1 1 auto;
           min-width: 120px;
           border-right: 1px solid rgba(255,255,255,0.08);
         }
         .composer-tools-slot {
           display: flex;
           flex-direction: row;
           flex-wrap: nowrap;
           align-items: center;
           justify-content: flex-start;
           gap: 5px;
           flex: 0 0 auto;
           background: #0a0a0a;
           padding: 0 8px;
           align-self: stretch;
           border-right: 1px solid rgba(255,255,255,0.08);
          }
          .voice-transport-status {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            max-width: 108px;
            min-width: 0;
            padding: 0 4px;
            color: rgba(255,255,255,.52);
            font: 550 9px/1 Inter, 'Segoe UI Variable', sans-serif;
            letter-spacing: .01em;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }
          .voice-transport-status i {
            width: 5px;
            height: 5px;
            flex: 0 0 5px;
            border-radius: 50%;
            background: rgba(255,255,255,.52);
            box-shadow: 0 0 0 3px rgba(255,255,255,.045);
            transition: transform 90ms linear, background .15s ease;
          }
          .voice-transport-status[data-state="listening"],
          .voice-transport-status[data-state="speaking"] {
            color: rgba(255,255,255,.86);
          }
          .voice-transport-status[data-state="listening"] i,
          .voice-transport-status[data-state="speaking"] i {
            background: #fff;
          }
          .voice-transport-status[data-state="error"] {
            color: rgba(255,255,255,.58);
          }
         .composer-mode-button {
           min-width: 54px;
           height: 38px;
           display: inline-flex;
           align-items: center;
           justify-content: center;
           gap: 6px;
           padding: 0 9px;
           border: 1px solid transparent;
           border-radius: 3px;
           color: rgba(255,255,255,0.48);
           background: transparent;
           font: 550 10px/1 Inter, 'Segoe UI Variable', sans-serif;
           cursor: pointer;
           transition: color .15s ease, background .15s ease, border-color .15s ease;
         }
         .composer-mode-button svg {
           width: 14px;
           height: 14px;
           flex: 0 0 auto;
         }
         .composer-mode-button:hover {
           color: rgba(255,255,255,0.82);
           background: rgba(255,255,255,0.045);
         }
         .composer-mode-button.active {
           color: #fff;
           border-color: rgba(255,255,255,0.15);
           background: rgba(255,255,255,0.08);
         }
         .composer-mode-label {
           white-space: nowrap;
         }

         .icon-button, .provider-picker, .model-picker, .mode-picker, .toolbar-button {
           position: relative;
           overflow: hidden;
           background: transparent;
           border: 1px solid transparent;
           box-shadow: none;
           color: #fff;
           cursor: pointer;
           transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
         }
         .icon-button:hover:not(:disabled), .provider-picker:hover:not(:disabled), .model-picker:hover:not(:disabled), .mode-picker:hover:not(:disabled), .toolbar-button:hover:not(:disabled) {
           background: rgba(255,255,255,0.05);
         }
         .icon-button:active:not(:disabled), .provider-picker:active:not(:disabled), .model-picker:active:not(:disabled), .mode-picker:active:not(:disabled), .toolbar-button:active:not(:disabled) {
           background: rgba(255,255,255,0.07);
         }
         .icon-button {
           display: flex;
           align-items: center;
           justify-content: center;
           border-radius: 3px;
           border: 1px solid rgba(255,255,255,0.12);
         }
         .inline-switcher {
           display: flex;
           align-items: center;
           width: 100%;
           min-width: 0;
         }
         .switcher-dot {
           width: 6px;
           height: 6px;
           border-radius: 50%;
           background: #475569;
           flex: 0 0 auto;
         }
         .switcher-dot.online { background: #22c55e; box-shadow: 0 0 8px #22c55e44; }
         .switcher-dot.offline { background: #ef4444; box-shadow: 0 0 8px #ef444444; }

         .provider-picker, .model-picker, .mode-picker, .toolbar-button {
           height: 34px;
           border-radius: 0;
           font-size: 11px;
           font-weight: 500;
           outline: none;
           padding: 0 10px 6px;
           line-height: 1.2;
           min-width: 0;
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           letter-spacing: 0.01em;
         }
         .provider-picker, .model-picker, .mode-picker {
           width: 100%;
           max-width: none;
           appearance: none;
           background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='rgba(255,255,255,0.45)' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
           background-repeat: no-repeat;
           background-position: right 8px center;
           padding-right: 24px;
         }
         .provider-picker option, .model-picker option, .mode-picker option {
           background: #111;
           color: ${colors.text};
         }
         select {
           color: ${colors.text};
         }
         select option {
           background: #111;
           color: ${colors.text};
         }
         @media (max-width: 1120px) {
           .composer-mode-controls {
             flex: 1 1 100%;
             justify-content: flex-end;
           }
         }
         @media (max-width: 760px) {
           .live-run-header {
             align-items: flex-start;
             flex-direction: column;
           }
           .live-run-actions {
             width: 100%;
           }
           .live-run-action.is-toggle {
             margin-left: auto;
           }
           .live-run-details {
             grid-template-columns: minmax(0, 1fr);
             padding-left: 0;
           }
           .live-run-trace {
             padding-left: 0;
           }
           .composer-primary-controls {
             grid-template-columns: repeat(3, minmax(0, 1fr));
           }
           .composer-tools-slot {
             grid-column: 1 / -1;
             min-height: 46px;
             border-right: 0;
             border-bottom: 1px solid rgba(255,255,255,0.08);
           }
           .provider-slot,
           .model-slot,
           .effort-slot {
             min-width: 0;
           }
           .composer-mode-controls {
             justify-content: space-between;
           }
           .composer-mode-button {
             flex: 1 1 0;
           }
         }
         @media (max-width: 520px) {
           .composer-mode-label {
             display: none;
           }
           .composer-mode-button {
             min-width: 38px;
             padding: 0 8px;
           }
         }
       `;

const sanitizeForTTS = (input: string) => {
  let text = input || "";
  text = text.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, "");
  text = text.replace(/[\u2300-\u23FF\u2600-\u27BF]/g, "");
  text = text.replace(/[\u200D\uFE0E\uFE0F]/g, "");
  return text.replace(/\s+/g, " ").trim();
};

const Toggle = ({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) => {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "4px 0" }}>
      <span style={{ fontSize: 14, color: colors.text }}>{label}</span>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ fontSize: 12, fontWeight: 700, letterSpacing: "0.06em", color: checked ? colors.accent : colors.textDim }}>
          {checked ? "ON" : "OFF"}
        </span>
        <button
          type="button"
          onClick={() => onChange(!checked)}
          style={{
            position: "relative",
            width: 44,
            height: 24,
            borderRadius: 12,
            background: checked ? "linear-gradient(135deg, rgba(45,108,255,0.8), rgba(45,108,255,0.6))" : "linear-gradient(135deg, rgba(255,255,255,0.1), rgba(255,255,255,0.05))",
            border: checked ? "1px solid rgba(140,180,255,0.4)" : "1px solid rgba(255,255,255,0.15)",
            boxShadow: checked ? "0 2px 8px rgba(45,108,255,0.4), inset 0 1px 0 rgba(255,255,255,0.2)" : "inset 0 1px 2px rgba(0,0,0,0.2)",
            cursor: "pointer",
            transition: "all 0.3s cubic-bezier(0.16, 1, 0.3, 1)",
            padding: 0,
            display: "flex",
            alignItems: "center",
          }}
        >
          <div
            style={{
              width: 18,
              height: 18,
              borderRadius: "50%",
              background: "#fff",
              position: "absolute",
              left: checked ? 24 : 2,
              transition: "all 0.2s cubic-bezier(0.4, 0, 0.2, 1)",
              boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
            }}
          />
        </button>
      </div>
    </div>
  );
};

const settingsSectionStyle: React.CSSProperties = {
  background: "rgba(255, 255, 255, 0.02)",
  border: "1px solid rgba(255, 255, 255, 0.08)",
  borderRadius: "12px",
  padding: "20px",
  marginBottom: "20px",
};

const platformCardStyle: React.CSSProperties = {
  padding: 16,
  background: "linear-gradient(135deg, rgba(255,255,255,0.05), rgba(255,255,255,0.015))",
  borderRadius: 16,
  border: "1px solid rgba(255,255,255,0.08)",
  boxShadow: "0 10px 30px -20px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.04)",
};

const PlatformHeader = ({
  icon,
  title,
  subtitle,
  accent,
}: {
  icon: string;
  title: string;
  subtitle: string;
  accent: string;
}) => (
  <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
    <div
      style={{
        width: 42,
        height: 42,
        borderRadius: 14,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 20,
        background: `${accent}22`,
        border: `1px solid ${accent}44`,
        boxShadow: `0 10px 30px -18px ${accent}`,
      }}
    >
      {icon}
    </div>
    <div>
      <div style={{ fontSize: 14, fontWeight: 700, color: colors.text }}>{title}</div>
      <div style={{ fontSize: 12, color: colors.textDim }}>{subtitle}</div>
    </div>
  </div>
);

const stopTts = () => {
  localVoicePlayback.stop();
  useAppStore.getState().setSpeaking(false);
};

/*
 * Legacy browser-owned voice implementation retained temporarily as migration
 * evidence only. Production Chat no longer executes this block: microphone
 * capture and playback are owned by voiceTransport.ts and the backend's typed
 * local Voice transport. Keeping the old implementation non-executable avoids
 * a competing transcript/playback authority while the dirty worktree is being
 * consolidated without a destructive whole-file rewrite.
 *
const chunkTextForTTS = (text: string, maxChars: number = 260) => {
  const cleaned = (text || "").replace(/\s+/g, " ").trim();
  if (!cleaned) return [] as string[];
  const parts = cleaned.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [cleaned];
  const chunks: string[] = [];
  let current = "";

  const pushCurrent = () => {
    if (current.trim()) chunks.push(current.trim());
    current = "";
  };

  for (const raw of parts) {
    const part = raw.trim();
    if (!part) continue;
    const candidate = current ? `${current} ${part}` : part;
    if (candidate.length <= maxChars) {
      current = candidate;
      continue;
    }

    if (current) pushCurrent();
    if (part.length <= maxChars) {
      current = part;
      continue;
    }

    const words = part.split(/\s+/).filter(Boolean);
    let buf = "";
    for (const word of words) {
      const next = buf ? `${buf} ${word}` : word;
      if (next.length > maxChars) {
        if (buf) chunks.push(buf);
        buf = word;
      } else {
        buf = next;
      }
    }
    if (buf) chunks.push(buf);
  }

  pushCurrent();
  return chunks.filter(Boolean);
};

let ttsSequence = 0;

const ttsTabId = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Math.random());
let ttsChannel: BroadcastChannel | null = null;
try {
  ttsChannel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("echospeak_tts") : null;
} catch {
  ttsChannel = null;
}

const stopTts = () => {
  ttsSequence += 1;
  localVoicePlayback.stop();
  useAppStore.getState().setSpeaking(false);
};

const pulseSpeaking = (ms: number) => {
  const { setSpeaking, bumpSpeechBeat } = useAppStore.getState();
  try {
    setSpeaking(true);
    bumpSpeechBeat();
    window.setTimeout(() => setSpeaking(false), Math.max(250, ms));
  } catch {
    // ignore
  }
};

if (ttsChannel) {
  try {
    ttsChannel.onmessage = (evt: MessageEvent) => {
      const data = (evt as any)?.data;
      if (!data || typeof data !== "object") return;
      if (data.type === "tts_start" && data.tabId && data.tabId !== ttsTabId) {
        stopTts();
      }
    };
  } catch { }
}

const speakText = async (text: string) => {
  const cleaned = sanitizeForTTS(text);
  if (!cleaned) return;
  const { setSpeaking, bumpSpeechBeat, addMessage } = useAppStore.getState();
  const sequenceId = ++ttsSequence;

  try {
    if (typeof window !== "undefined" && window.localStorage?.getItem("echospeak.tts_debug") === "1") {
      console.debug("[EchoSpeak TTS] speakText len=%d text=", cleaned.length, cleaned);
    }
  } catch {
    // ignore
  }

  if (ttsChannel) {
    try {
      ttsChannel.postMessage({ type: "tts_start", tabId: ttsTabId, at: Date.now() });
    } catch { }
  }

  setSpeaking(false);

  if (typeof window === "undefined" || !("speechSynthesis" in window)) {
    addMessage({
      id: crypto.randomUUID(),
      role: "assistant",
      text: "Speech unavailable: your browser does not support SpeechSynthesis.",
      at: Date.now(),
    });
    return;
  }

  // Signal that speech is starting (the polling loop in startBeat keeps it alive).
  setSpeaking(true);

  try {
    const voices = window.speechSynthesis.getVoices();
    if (!voices || voices.length === 0) {
      addMessage({
        id: crypto.randomUUID(),
        role: "assistant",
        text:
          "Speech is enabled but your browser reports 0 voices. On Linux this usually means the system TTS backend isn't installed/running. " +
          "Try installing speech-dispatcher / espeak-ng and restarting the browser.",
        at: Date.now(),
      });
      return;
    }
  } catch { }

  try {
    // Some browsers (esp. Linux) get stuck in a paused state.
    window.speechSynthesis.resume();
  } catch { }

  try {
    // Aggressive flush to clear any stuck utterances before starting a new sequence
    window.speechSynthesis.pause();
    window.speechSynthesis.cancel();
    window.speechSynthesis.resume();
  } catch { }

  const chunks = chunkTextForTTS(cleaned, 260);
  if (!chunks.length) return;

  let beatPollTimer: number | null = null;
  const startBeat = () => {
    if (beatPollTimer == null) {
      beatPollTimer = window.setInterval(() => {
        try {
          if (typeof window !== "undefined" && "speechSynthesis" in window) {
            const isSpeaking = window.speechSynthesis.speaking || window.speechSynthesis.pending;
            setSpeaking(isSpeaking);
          }
        } catch { }
      }, 100);
    }
    setSpeaking(true);
  };

  const scheduleBeatStop = () => {
    // No-op for compatibility
  };

  const stopBeat = () => {
    if (beatPollTimer != null) {
      window.clearInterval(beatPollTimer);
      beatPollTimer = null;
    }
    // Very short tail debounce so it stops almost instantly when audio ends
    window.setTimeout(() => setSpeaking(false), 50);
  };

  const speakChunk = async (chunk: string) =>
    new Promise<void>((resolve, reject) => {
      const { speechEnabled, selectedVoice } = useAppStore.getState();
      if (!speechEnabled) {
        resolve();
        return;
      }

      // Start animation immediately; some browsers delay/skip onstart.
      startBeat();

      const utter = new SpeechSynthesisUtterance(chunk);
      // Prevent browser garbage collection bug that stops TTS mid-speech
      const win = window as any;
      win._activeUtterances = win._activeUtterances || [];
      win._activeUtterances.push(utter);

      if (selectedVoice) {
        const voices = window.speechSynthesis.getVoices();
        const found = voices.find(v => v.name === selectedVoice);
        if (found) utter.voice = found;
      }
      let done = false;

      const cleanup = (err?: Error) => {
        if (done) return;
        done = true;
        const active = win._activeUtterances;
        if (active) {
          const idx = active.indexOf(utter);
          if (idx > -1) active.splice(idx, 1);
        }
        if (err) reject(err);
        else resolve();
      };

      const safetyTimeout = window.setTimeout(() => {
        scheduleBeatStop();
        addMessage({
          id: crypto.randomUUID(),
          role: "assistant",
          text:
            "Speech timed out (browser TTS glitch). Try toggling Speech off/on, or click Unlock Speech.",
          at: Date.now(),
        });
        cleanup(new Error("timeout"));
      }, Math.max(6000, Math.min(45000, chunk.length * 170)));

      const clearSafety = () => window.clearTimeout(safetyTimeout);

      utter.onstart = () => {
        // Just rely on polling loop for UI
      };
      utter.onboundary = () => {
        // No-op, animation loop runs purely on `speaking` state now
      };
      utter.onend = () => {
        clearSafety();
        cleanup();
      };
      utter.onerror = (e) => {
        clearSafety();
        console.error("TTS chunk error:", e);
        cleanup(new Error("speech_error"));
      };

      try {
        try {
          window.speechSynthesis.resume();
        } catch { }
        window.speechSynthesis.speak(utter);
      } catch (e) {
        clearSafety();
        const msg = e instanceof Error ? e.message : String(e);
        addMessage({
          id: crypto.randomUUID(),
          role: "assistant",
          text:
            "Speech failed to start. If you're on Chrome/Edge, click anywhere in the page once and try again. Details: " +
            msg,
          at: Date.now(),
        });
        cleanup(e instanceof Error ? e : new Error(String(e)));
      }
    });

  try {
    for (let i = 0; i < chunks.length; i += 1) {
      if (sequenceId !== ttsSequence) {
        stopBeat();
        return;
      }
      await speakChunk(chunks[i]);
    }
    stopBeat();
  } catch (err) {
    stopBeat();
    const name = (err as any)?.name ? String((err as any).name) : "";
    if (name === "NotAllowedError" || name === "AbortError") return;
    setSpeaking(false);
  }
};

// Hook: mic capture -> browser SpeechRecognition
const useMicStreamer = (onFinalTranscript?: (text: string) => void) => {
  const recRef = useRef<any>(null);
  const transcriptRef = useRef<string>("");
  const { setListening, addMessage } = useAppStore();

  const stopAll = (submitTranscript: boolean) => {
    const t = transcriptRef.current.trim();
    transcriptRef.current = "";
    if (submitTranscript && t) {
      try {
        onFinalTranscript?.(t);
      } catch {
        // ignore
      }
    } else if (submitTranscript && !t) {
      addMessage({
        id: crypto.randomUUID(),
        role: "assistant",
        text: "Mic: I didn't catch any speech. Try again and speak a bit longer.",
        at: Date.now(),
      });
    }

    try {
      recRef.current?.stop?.();
    } catch {
      
    }
    recRef.current = null;
    setListening(false);
  };

  const start = async () => {
    if (recRef.current) stopAll(false);
    try {
      const SpeechRecognitionCtor = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      if (!SpeechRecognitionCtor) {
        addMessage({
          id: crypto.randomUUID(),
          role: "assistant",
          text: "Mic unavailable: your browser does not support SpeechRecognition. Use Chrome/Edge, or enable the feature.",
          at: Date.now(),
        });
        stopAll(false);
        return;
      }

      const rec = new SpeechRecognitionCtor();
      recRef.current = rec;
      transcriptRef.current = "";
      rec.continuous = true;
      rec.interimResults = true;
      rec.lang = navigator.language || "en-US";

      setListening(true);

      rec.onstart = () => {
        setListening(true);
      };

      rec.onresult = (evt: any) => {
        try {
          const results = evt?.results;
          if (!results || typeof results.length !== "number") return;

          let finals = "";
          let interim = "";
          for (let i = 0; i < results.length; i += 1) {
            const res = results[i];
            const txt = res && res[0] && typeof res[0].transcript === "string" ? String(res[0].transcript) : "";
            if (!txt) continue;
            if (res.isFinal) finals += txt.trim() + " ";
            else interim = txt.trim();
          }

          const fullText = (finals + interim).trim();
          transcriptRef.current = fullText;

          // Dispatch to the React component layer so it can update the input box and handle auto-send
          window.dispatchEvent(new CustomEvent("echospeak-transcript", { detail: fullText }));
        } catch {
          // ignore
        }
      };

      rec.onerror = (e: any) => {
        const msg = e?.error ? String(e.error) : "unknown";
        if (msg === "network") {
          addMessage({
            id: crypto.randomUUID(),
            role: "assistant",
            text:
              "Mic error: network. Your browser's SpeechRecognition service couldn't be reached. " +
              "Make sure you're online, not blocking it with VPN/adblock/firewall, and use Chrome/Edge.",
            at: Date.now(),
          });
        } else {
          addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Mic error: ${msg}`, at: Date.now() });
        }
        stopAll(false);
      };

      rec.onend = () => {
        stopAll(false);
      };

      rec.start();
    } catch (err) {
      console.error("Mic error", err);
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Mic error: ${String(err)}`, at: Date.now() });
      stopAll(false);
    }
  };

  return {
    start,
    stop: () => {
      stopAll(true);
    },
  };
};
*/

const ContextMeter: React.FC<{ messages: Message[]; contextWindow: number }> = ({ messages, contextWindow }) => {
  const [hover, setHover] = React.useState(false);
  if (!contextWindow || contextWindow <= 0) return null;
  const estimatedTokens = messages.reduce((sum, m) => sum + (m.usage?.tokens ?? estimateTokens(m.text)), 0);
  const pct = Math.min(estimatedTokens / contextWindow, 1);
  const displayPct = Math.round(pct * 100);
  const size = 40;
  const fillColor =
    pct > 0.85 ? "rgba(255,255,255,0.95)" : pct > 0.6 ? "rgba(255,255,255,0.88)" : "rgba(255,255,255,0.92)";
  const trackColor = "rgba(255,255,255,0.14)";
  const warnTint =
    pct > 0.85 ? "rgba(255,90,90,0.18)" : pct > 0.6 ? "rgba(255,200,80,0.12)" : "transparent";

  return (
    <div
      className="context-meter-wrap"
      style={{
        position: "relative",
        width: size,
        height: size,
        flexShrink: 0,
        cursor: "default",
        display: "grid",
        placeItems: "center",
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title={`Context ${displayPct}%`}
      aria-label={`Context ${displayPct}% used`}
    >
      <div
        style={{
          position: "relative",
          width: size,
          height: size,
          borderRadius: 3,
          background: warnTint || "rgba(255,255,255,0.03)",
          border: `1px solid ${trackColor}`,
          overflow: "hidden",
          boxSizing: "border-box",
        }}
      >
        <div style={{ position: "absolute", inset: 3, borderRadius: 2, background: "rgba(255,255,255,0.04)" }} />
        <div
          style={{
            position: "absolute",
            left: 3,
            right: 3,
            bottom: 3,
            height: `calc((100% - 6px) * ${pct})`,
            borderRadius: 2,
            background: `linear-gradient(180deg, ${fillColor} 0%, rgba(255,255,255,0.55) 100%)`,
            transition: "height 0.4s ease",
          }}
        />
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "grid",
            placeItems: "center",
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: "-0.3px",
            color: pct > 0.45 ? "rgba(0,0,0,0.78)" : "rgba(255,255,255,0.72)",
            userSelect: "none",
            fontVariantNumeric: "tabular-nums",
            fontFamily: "'JetBrains Mono', ui-monospace, monospace",
          }}
        >
          {displayPct}
        </div>
      </div>
      {hover && (
        <div
          style={{
            position: "absolute",
            bottom: "calc(100% + 10px)",
            /* Open toward the left so the full panel stays visible next to send */
            right: 0,
            left: "auto",
            transform: "none",
            background: "rgba(12,12,14,0.96)",
            border: "1px solid rgba(255,255,255,0.14)",
            borderRadius: 10,
            padding: "10px 12px",
            whiteSpace: "nowrap",
            zIndex: 2000,
            boxShadow: "0 8px 28px rgba(0,0,0,0.55)",
            backdropFilter: "blur(12px)",
            fontSize: 12,
            color: colors.text,
            lineHeight: 1.5,
            minWidth: 160,
            pointerEvents: "none",
          }}
        >
          <div style={{ fontWeight: 700, marginBottom: 4, color: "#fff", letterSpacing: "-0.02em" }}>Context</div>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
            <span style={{ color: colors.textDim }}>Used</span>
            <span style={{ fontWeight: 600 }}>{formatTokenCount(estimatedTokens)}</span>
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
            <span style={{ color: colors.textDim }}>Window</span>
            <span style={{ fontWeight: 600 }}>{formatTokenCount(contextWindow)}</span>
          </div>
          <div
            style={{
              marginTop: 8,
              height: 4,
              borderRadius: 2,
              background: "rgba(255,255,255,0.1)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${displayPct}%`,
                height: "100%",
                background: "#fff",
                borderRadius: 2,
                transition: "width 0.3s ease",
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
};

const ChatBubble: React.FC<{
  msg: Message;
  streaming?: boolean;
  typewriter?: boolean;
  onQuickReply?: (text: string) => void;
  contextWindow?: number;
  providerLabel?: string;
  modelLabel?: string;
}> = ({ msg, streaming, typewriter = false, onQuickReply, contextWindow = 0, providerLabel, modelLabel }) => {
  const isUser = msg.role === "user";
  // Approval controls are rendered only from an exact backend approval record.
  const isConfirmPrompt = false;
  const [shown, setShown] = useState(isUser || !typewriter ? msg.text : "");
  const [metaHover, setMetaHover] = useState(false);

  useEffect(() => {
    if (isUser || !typewriter) {
      setShown(msg.text);
      return;
    }
    const target = msg.text || "";
    if (!target) {
      setShown("");
      return;
    }
    // Progressive reveal independent of backend generation speed.
    let i = 0;
    setShown("");
    const tick = window.setInterval(() => {
      i = Math.min(target.length, i + Math.max(2, Math.ceil(target.length / 80)));
      setShown(target.slice(0, i));
      if (i >= target.length) window.clearInterval(tick);
    }, 18);
    return () => window.clearInterval(tick);
  }, [msg.id, msg.text, isUser, typewriter]);

  const canQuickReply = Boolean(isConfirmPrompt && onQuickReply && !streaming);
  const bodyText = isUser || !typewriter ? msg.text : shown;
  const stillTyping = !isUser && typewriter && shown.length < (msg.text || "").length;
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      style={{
        display: "flex",
        justifyContent: isUser ? "flex-end" : "flex-start",
        position: "relative",
        width: "100%",
        minWidth: 0,
        /* Extra right inset on user rows so text never kisses the scrollbar */
        padding: isUser ? "8px 2px 6px 0" : "8px 4px 6px",
        boxSizing: "border-box",
      }}
    >
      <div
        className={`chat-flat${isUser ? " chat-user-bubble-wrap" : ""}`}
        style={{
          position: "relative",
          maxWidth: isUser ? "94%" : "100%",
          width: isUser ? "auto" : "100%",
          minWidth: 0,
          color: colors.text,
          padding: "0",
          overflow: "visible",
          boxSizing: "border-box",
        }}
      >
        {isUser ? (
          <div className="chat-text chat-line-user" style={{ fontFamily: "'JetBrains Mono', ui-monospace, monospace", fontSize: 13.5 }}>
            {bodyText}
          </div>
        ) : (
          <div className="chat-line-assistant" style={{ minWidth: 0, maxWidth: "100%" }}>
            <ResponseRenderer plan={msg.renderPlan} fallbackText={bodyText} colors={colors} stillTyping={stillTyping} />
            {stillTyping ? (
              <span
                style={{
                  display: "inline-block",
                  width: 8,
                  height: 15,
                  marginLeft: 3,
                  borderRadius: 1,
                  background: "rgba(255,255,255,0.75)",
                  animation: "pulse 0.8s infinite",
                  verticalAlign: "text-bottom",
                }}
              />
            ) : null}
            {!stillTyping && msg.embeds?.length ? (
              <ChatEmbeds embeds={msg.embeds} colors={colors} />
            ) : null}
          </div>
        )}

        {!isUser && isConfirmPrompt ? (
          <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
            <button
              onClick={() => onQuickReply?.("confirm")}
              disabled={!canQuickReply}
              style={{
                padding: "7px 12px",
                borderRadius: 2,
                border: `1px solid ${canQuickReply ? "rgba(255,255,255,0.35)" : colors.line}`,
                background: "transparent",
                color: colors.text,
                cursor: canQuickReply ? "pointer" : "not-allowed",
                fontSize: 12,
                fontWeight: 600,
                fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                letterSpacing: "0.04em",
                textTransform: "uppercase",
              }}
            >
              Confirm
            </button>
            <button
              onClick={() => onQuickReply?.("cancel")}
              disabled={!canQuickReply}
              style={{
                padding: "7px 12px",
                borderRadius: 2,
                border: `1px solid ${colors.line}`,
                background: "transparent",
                color: colors.textDim,
                cursor: canQuickReply ? "pointer" : "not-allowed",
                fontSize: 12,
                fontWeight: 600,
                fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                letterSpacing: "0.04em",
                textTransform: "uppercase",
              }}
            >
              Cancel
            </button>
          </div>
        ) : null}

        {/* Single compact meta row: Time · Tokens · CTX · Sources · Search (wrap only when narrow). */}
        <div
          style={{
            marginTop: 4,
            display: "flex",
            flexDirection: "column",
            gap: 0,
            minWidth: 0,
            width: "100%",
          }}
        >
          {(() => {
            const msgTokens = msg.usage?.tokens ?? estimateTokens(msg.text);
            const ctxUsed = msg.usage?.contextUsed ?? msgTokens;
            const ctxWindow = msg.usage?.contextWindow || contextWindow || 32768;
            const ctxPct = ctxWindow > 0 ? Math.min(100, Math.round((ctxUsed / ctxWindow) * 100)) : 0;
            const prov = msg.usage?.provider || providerLabel || "";
            const model = msg.usage?.model || modelLabel || "";
            return (
              <div
                data-testid="chat-bubble-meta"
                style={{
                  marginTop: 0,
                  fontSize: 10,
                  color: "rgba(255,255,255,0.28)",
                  fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                  letterSpacing: "0.06em",
                  textAlign: isUser ? "right" : "left",
                  display: "flex",
                  justifyContent: isUser ? "flex-end" : "flex-start",
                  alignItems: "center",
                  gap: 6,
                  position: "relative",
                  flexWrap: "wrap",
                  rowGap: 4,
                }}
              >
                <span data-testid="chat-meta-time">{new Date(msg.at).toLocaleTimeString()}</span>
                <span style={{ opacity: 0.45 }}>·</span>
                <span
                  data-testid="chat-meta-tokens"
                  onMouseEnter={() => setMetaHover(true)}
                  onMouseLeave={() => setMetaHover(false)}
                  style={{
                    cursor: "default",
                    borderBottom: "1px dotted rgba(255,255,255,0.18)",
                    paddingBottom: 1,
                  }}
                >
                  ~{formatTokenCount(msgTokens)} tok
                </span>
                {!isUser ? (
                  <>
                    <span style={{ opacity: 0.45 }}>·</span>
                    <span data-testid="chat-meta-ctx">{ctxPct}% ctx</span>
                  </>
                ) : null}
                {!isUser && !stillTyping ? (
                  <ChatEmbedFooter
                    embeds={msg.embeds}
                    colors={colors}
                    extraSources={Array.isArray(msg.docSources) ? msg.docSources.length : 0}
                  />
                ) : null}
                {metaHover && (
                  <div
                    style={{
                      position: "absolute",
                      bottom: "calc(100% + 8px)",
                      [isUser ? "right" : "left"]: 0,
                      background: "rgba(12,12,14,0.96)",
                      border: "1px solid rgba(255,255,255,0.14)",
                      borderRadius: 8,
                      padding: "9px 11px",
                      zIndex: 50,
                      boxShadow: "0 8px 24px rgba(0,0,0,0.5)",
                      backdropFilter: "blur(12px)",
                      fontSize: 11,
                      color: colors.text,
                      lineHeight: 1.55,
                      minWidth: 168,
                      letterSpacing: "0.02em",
                      textAlign: "left",
                      whiteSpace: "nowrap",
                    }}
                  >
                    <div style={{ fontWeight: 700, color: "#fff", marginBottom: 4, letterSpacing: "-0.02em" }}>
                      {isUser ? "Message" : "Response"} usage
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 18 }}>
                      <span style={{ color: colors.textDim }}>This bubble</span>
                      <span style={{ fontWeight: 600 }}>~{formatTokenCount(msgTokens)}</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 18 }}>
                      <span style={{ color: colors.textDim }}>Context used</span>
                      <span style={{ fontWeight: 600 }}>~{formatTokenCount(ctxUsed)}</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 18 }}>
                      <span style={{ color: colors.textDim }}>Window</span>
                      <span style={{ fontWeight: 600 }}>{formatTokenCount(ctxWindow)}</span>
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 18 }}>
                      <span style={{ color: colors.textDim }}>Fill</span>
                      <span style={{ fontWeight: 600 }}>{ctxPct}%</span>
                    </div>
                    {(prov || model) && (
                      <div style={{ marginTop: 6, paddingTop: 6, borderTop: "1px solid rgba(255,255,255,0.08)", color: colors.textDim, fontSize: 10 }}>
                        {[prov, model].filter(Boolean).join(" · ")}
                      </div>
                    )}
                    <div style={{ marginTop: 4, fontSize: 9, color: "rgba(255,255,255,0.28)" }}>
                      Estimates (chars ÷ 3.5)
                    </div>
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      </div>
    </motion.div>
  );
};

const ThinkingActivityCard: React.FC<{
  item: { kind: "thinking"; id: string; content: string; at: number; steps?: ThinkingStep[]; request_id?: string };
  /** Only one card owns the Echo spinner — other rows use static marks. */
  primarySpinner?: boolean;
}> = ({ item, primarySpinner = true }) => {
  // One clean list: drop pure thought dumps; keep at most one soft "thinking…" if nothing else yet.
  const rawSteps = item.steps || [];
  const workSteps = rawSteps.filter((s) => s.type !== "thought");
  const softPlaceholder = /^(understanding|planning|responding|thinking|waiting(?: for model)?|working|checking)(\s|\.|…)*$/i;
  // Soft placeholders are owned by LiveChatActivityBar — only real tool/search/task rows here.
  const steps: ThinkingStep[] = workSteps.filter(
    (s) => !softPlaceholder.test(String(s.content || "").trim())
  );
  const anyRunning = steps.some((s) => s.status === "running");
  const containerRef = useRef<HTMLDivElement>(null);
  // Prefer a single animated Echo mark on the newest running step (when this card owns spin).
  const primaryRunningId = [...steps].reverse().find((s) => s.status === "running")?.id;

  useEffect(() => {
    const el = containerRef.current?.closest(".chat-scroll") as HTMLElement | null;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    // Stay glued to the true bottom while tools/thinking update.
    if (distFromBottom <= 240) {
      el.scrollTop = el.scrollHeight;
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight;
      });
    }
  }, [steps.map((s) => `${s.id}:${s.status}`).join("|"), anyRunning]);

  if (steps.length === 0) {
    return null;
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      style={{ display: "flex", justifyContent: "flex-start", width: "100%", padding: "2px 0 2px" }}
      ref={containerRef}
      data-testid="chat-thinking-activity"
    >
      <div className="chat-flat" style={{ width: "100%", maxWidth: "100%", color: colors.textDim }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {steps.map((step) => {
            const failed = step.status === "failed";
            const running = step.status === "running";
            const spinHere = primarySpinner && running && step.id === primaryRunningId;
            return (
              <div
                key={step.id}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 10,
                  fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                  fontSize: 12,
                  lineHeight: 1.5,
                  letterSpacing: "0.02em",
                  color: failed
                    ? "rgba(255,140,150,0.85)"
                    : running
                      ? "rgba(255,255,255,0.72)"
                      : "rgba(255,255,255,0.38)",
                }}
              >
                <span style={{ marginTop: 3, flexShrink: 0 }}>
                  {spinHere ? (
                    <SquareLoader size={9} color="rgba(255,255,255,0.85)" active />
                  ) : failed ? (
                    <span
                      style={{
                        display: "inline-block",
                        width: 8,
                        height: 8,
                        borderRadius: 1,
                        background: "rgba(248,113,113,0.9)",
                      }}
                      title="failed"
                    />
                  ) : (
                    <span
                      style={{
                        display: "inline-block",
                        width: 7,
                        height: 7,
                        borderRadius: 1,
                        background: running ? "rgba(255,255,255,0.55)" : "rgba(255,255,255,0.28)",
                      }}
                    />
                  )}
                </span>
                <span style={{ flex: 1 }}>
                  {step.content}
                  {failed && !/fail/i.test(step.content) ? " — failed" : ""}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </motion.div>
  );
};

const ActivityCard: React.FC<{ item: ActivityItem; primarySpinner?: boolean }> = ({ item, primarySpinner }) => {
  if (item.kind === "thinking") {
    return <ThinkingActivityCard item={item} primarySpinner={primarySpinner} />;
  }

  if (item.kind === "memory") {
    return (
      <motion.div
        layout
        initial={{ opacity: 0, y: 5 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.2 }}
        style={{ display: "flex", justifyContent: "flex-start", marginLeft: "0px", marginTop: "-6px", marginBottom: "4px" }}
      >
        <div style={{ fontSize: 11, color: "rgba(255,255,255,0.4)", display: "flex", alignItems: "center", gap: 6, fontWeight: 500 }}>
          <span style={{ opacity: 0.7 }}>✓</span>
          <span>Memory saved ({item.memoryCount})</span>
        </div>
      </motion.div>
    );
  }

  if (item.kind === "error") {
    return (
      <motion.div
        layout
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.18 }}
        style={{ display: "flex", justifyContent: "flex-start", padding: "6px 0" }}
      >
        <div className="chat-flat" style={{ width: "100%", fontFamily: "'JetBrains Mono', ui-monospace, monospace" }}>
          <div style={{ fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", color: "rgba(255,120,140,0.9)", marginBottom: 4 }}>
            error
          </div>
          <div style={{ fontSize: 13, lineHeight: 1.55, color: "rgba(255,180,190,0.85)", whiteSpace: "pre-wrap" }}>{item.message}</div>
        </div>
      </motion.div>
    );
  }

  // Standalone tool activity items — flat digital lines (tools also appear in thinking steps).
  const body =
    item.status === "running"
      ? item.input || "running…"
      : item.output || (item.status === "error" ? "failed" : "done");
  const label = item.name || "tool";

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      style={{ display: "flex", justifyContent: "flex-start", padding: "1px 0 2px", width: "100%" }}
    >
      <div className="chat-flat" style={{ width: "100%", maxWidth: "100%" }}>
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 10,
            fontFamily: "'JetBrains Mono', ui-monospace, monospace",
            fontSize: 12,
            lineHeight: 1.5,
            letterSpacing: "0.02em",
            color: item.status === "running" ? "rgba(255,255,255,0.7)" : "rgba(255,255,255,0.38)",
          }}
        >
          <span style={{ marginTop: 3, flexShrink: 0 }}>
            {item.status === "running" ? (
              <SquareLoader size={9} color="rgba(255,255,255,0.7)" />
            ) : (
              <span style={{ display: "inline-block", width: 7, height: 7, borderRadius: 1, background: "rgba(255,255,255,0.28)" }} />
            )}
          </span>
          <span style={{ flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            <span style={{ color: "rgba(255,255,255,0.45)" }}>{label}</span>
            {body ? `  ${String(body).slice(0, 240)}${String(body).length > 240 ? "…" : ""}` : ""}
          </span>
        </div>
      </div>
    </motion.div>
  );
};

type ConfirmationCardProps = {
  action: any;
  riskLevel?: string;
  riskColor?: string;
  policyFlags?: string[];
  sessionPermissions?: Record<string, boolean>;
  dryRunAvailable?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  onDryRun?: () => void;
};

const ConfirmationCard: React.FC<ConfirmationCardProps> = ({
  action,
  riskLevel = "safe",
  riskColor = "#22c55e",
  policyFlags = [],
  sessionPermissions = {},
  dryRunAvailable = false,
  onConfirm,
  onCancel,
  onDryRun,
}) => {
  const [decisionBusy, setDecisionBusy] = React.useState(false);
  const toolName = action?.tool || "unknown";
  const kwargs = action?.kwargs || {};
  const safeArgumentEntries = Object.entries(kwargs).filter(([key]) =>
    !/(content|text|message|password|token|secret|api[_-]?key|credential)/i.test(key)
  );
  const runDecision = (fn: () => void) => {
    if (decisionBusy) return;
    setDecisionBusy(true);
    try {
      fn();
    } catch {
      setDecisionBusy(false);
    }
  };
  const permissionForFlag = (flag: string) => {
    const upper = String(flag || "").toUpperCase();
    if (upper === "ENABLE_SYSTEM_ACTIONS") return "system_actions";
    if (upper === "ALLOW_FILE_WRITE") return "file_write";
    if (upper === "ALLOW_TERMINAL_COMMANDS") return "terminal";
    if (upper === "ALLOW_DESKTOP_AUTOMATION") return "desktop";
    if (upper === "ALLOW_PLAYWRIGHT") return "playwright";
    return upper.toLowerCase();
  };
  const missingPolicyFlags = policyFlags.filter((flag) => sessionPermissions[permissionForFlag(flag)] === false);

  const riskLabels: Record<string, string> = {
    safe: "Safe",
    moderate: "Moderate Risk",
    destructive: "High Risk",
  };

  const riskBgColors: Record<string, string> = {
    safe: "rgba(34,197,94,0.12)",
    moderate: "rgba(245,158,11,0.12)",
    destructive: "rgba(239,68,68,0.12)",
  };

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
      style={{ display: "flex", justifyContent: "flex-start" }}
    >
      <div
        data-testid="approval-confirmation-card"
        data-approval-tool={toolName}
        style={{
          maxWidth: "96%",
          width: "fit-content",
          background: colors.panel2,
          color: colors.text,
          border: `1px solid ${colors.line}`,
          borderRadius: 14,
          padding: "14px 16px",
          boxShadow: `0 0 20px ${riskColor}15`,
          position: "relative",
          zIndex: 20,
        }}
      >
        {/* Header with risk badge */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
          <div
            style={{
              fontSize: 10.5,
              fontWeight: 700,
              letterSpacing: 0.5,
              textTransform: "uppercase",
              padding: "4px 10px",
              borderRadius: 999,
              color: riskColor,
              background: riskBgColors[riskLevel] || riskBgColors.safe,
              border: `1px solid ${riskColor}50`,
            }}
          >
            {riskLabels[riskLevel] || "Safe"}
          </div>
          <div style={{ fontSize: 13, fontWeight: 650 }}>Confirm Action</div>
        </div>

        {/* Tool name */}
        <div style={{
          fontSize: 12,
          fontFamily: "ui-monospace, monospace",
          color: colors.accent,
          marginBottom: 8,
          padding: "6px 10px",
          background: "rgba(0,0,0,0.2)",
          borderRadius: 6,
        }}>
          {toolName}
        </div>

        {/* Action details */}
        <div style={{ fontSize: 12.5, lineHeight: 1.6, color: colors.textDim, marginBottom: 10 }}>
          {safeArgumentEntries.map(([key, value]) => (
            <div key={key} style={{ marginBottom: 4 }}>
              <span style={{ color: colors.text, fontWeight: 500 }}>{key}:</span>{" "}
              <span style={{ wordBreak: "break-word" }}>
                {typeof value === "string" && value.length > 100
                  ? value.slice(0, 100) + "…"
                  : String(value)}
              </span>
            </div>
          ))}
        </div>

        {/* Policy flags */}
        {policyFlags.length > 0 && (
          <div style={{ fontSize: 10, color: colors.textDim, marginBottom: 10 }}>
            Requires: {policyFlags.join(", ")}
          </div>
        )}
        {missingPolicyFlags.length > 0 ? (
          <div style={{ fontSize: 10.5, color: "#f59e0b", marginBottom: 10 }}>
            Configuration required: {missingPolicyFlags.join(", ")}. This is an EchoSpeak policy block, not a detected Windows administrator or signature failure.
          </div>
        ) : null}

        {/* Session permissions */}
        <div style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 6,
          marginBottom: 12,
          fontSize: 10,
        }}>
          {Object.entries(sessionPermissions).map(([key, enabled]) => (
            <span
              key={key}
              style={{
                padding: "2px 6px",
                borderRadius: 4,
                background: enabled ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)",
                color: enabled ? "#22c55e" : "#ef4444",
              }}
            >
              {enabled ? "✓" : "✗"} {key}
            </span>
          ))}
        </div>

        {/* Action buttons */}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            data-testid="approval-confirm-button"
            onClick={() => runDecision(onConfirm)}
            disabled={missingPolicyFlags.length > 0 || decisionBusy}
            aria-busy={decisionBusy}
            style={{
              flex: 1,
              padding: "8px 16px",
              fontSize: 13,
              fontWeight: 600,
              borderRadius: 8,
              border: "none",
              background: riskLevel === "destructive" ? "#ef4444" : colors.accent,
              color: "#fff",
              cursor: missingPolicyFlags.length > 0 || decisionBusy ? "not-allowed" : "pointer",
              minWidth: 80,
              opacity: decisionBusy ? 0.7 : 1,
            }}
          >
            {decisionBusy ? "Working…" : "Confirm"}
          </button>
          {dryRunAvailable && onDryRun && (
            <button
              type="button"
              onClick={onDryRun}
              disabled={decisionBusy}
              style={{
                flex: 1,
                padding: "8px 16px",
                fontSize: 13,
                fontWeight: 600,
                borderRadius: 8,
                border: `1px solid ${colors.line}`,
                background: "transparent",
                color: colors.text,
                cursor: decisionBusy ? "not-allowed" : "pointer",
                minWidth: 80,
              }}
            >
              Dry Run
            </button>
          )}
          <button
            type="button"
            data-testid="approval-cancel-button"
            onClick={() => runDecision(onCancel)}
            disabled={decisionBusy}
            style={{
              flex: 1,
              padding: "8px 16px",
              fontSize: 13,
              fontWeight: 600,
              borderRadius: 8,
              border: `1px solid ${colors.line}`,
              background: "transparent",
              color: colors.textDim,
              cursor: decisionBusy ? "not-allowed" : "pointer",
              minWidth: 80,
              opacity: decisionBusy ? 0.7 : 1,
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    </motion.div>
  );
};

type DashboardTab = "chat" | "research" | "overview" | "skills" | "memory" | "docs" | "settings" | "search_settings" | "mcp_settings" | "advanced_settings" | "system_services" | "capabilities" | "approvals" | "executions" | "projects" | "automations" | "connections" | "soul" | "services" | "avatar_editor";

export const Dashboard: React.FC<{
  initialView?: DashboardTab;
  desktopSettingsWindow?: boolean;
}> = ({ initialView = "chat", desktopSettingsWindow = false }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const desktopMode = useMemo(() => isDesktopRuntime(), []);
  const apiBase = useMemo(() => getEchoSpeakApiBase(), []);
  const workspaceRoute = location.pathname.replace(/\/+$/, "");
  const mediaRouteActive = workspaceRoute === "/app/media";
  const [desktopSurface, setDesktopSurface] = useState<DesktopWorkspaceSurface>(() =>
    mediaRouteActive ? "visualizer" : "chat"
  );
  const [desktopSettingsOpen, setDesktopSettingsOpen] = useState(desktopSettingsWindow);
  const [desktopStudioHost, setDesktopStudioHost] = useState<HTMLDivElement | null>(null);
  const {
    messages,
    addMessage,
    streaming,
    setStreaming,
    listening,
    setListening,
    speaking,
    speechBeat,
    speechEnabled,
    setSpeechEnabled,
  } = useAppStore();

  const [input, setInput] = useState("");
  const workspaceMode = "auto";
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [taskPlans, setTaskPlans] = useState<TaskPlanEntry[]>([]);
  const [echoReaction, setEchoReaction] = useState<EchoReaction | null>(null);
  const [userIsTyping, setUserIsTyping] = useState(false);
  const userTypingTimerRef = useRef<number>(0);
  const updateComposerInput = useCallback((value: string) => {
    setInput(value);
    setUserIsTyping(true);
    if (userTypingTimerRef.current) clearTimeout(userTypingTimerRef.current);
    userTypingTimerRef.current = window.setTimeout(() => setUserIsTyping(false), 1500);
  }, []);
  const research = useResearchStore((state) => state.runs);
  const [researchArtifacts, setResearchArtifacts] = useState<any[]>([]);
  const [researchArtifactsError, setResearchArtifactsError] = useState("");
  const prependResearchRun = useResearchStore((state) => state.prependRun);
  const replaceResearchRuns = useResearchStore((state) => state.replaceRuns);
  const clearResearchRuns = useResearchStore((state) => state.clearRuns);
  const [leftTab, setLeftTab] = useState<DashboardTab>(
    desktopSettingsWindow ? "settings" : initialView
  );

  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const activeGroupButtonRef = useRef<HTMLButtonElement | null>(null);
  const activeGroupMenuRef = useRef<HTMLDivElement | null>(null);
  const [activeGroupPos, setActiveGroupPos] = useState<{ top: number; left: number } | null>(null);
  const [showVisualizer, setShowVisualizer] = useState<boolean>(() => loadRuntimeLayout(typeof window !== "undefined" ? window.localStorage : null).visualizerVisible);
  const [showSidebar, setShowSidebar] = useState<boolean>(() => loadRuntimeLayout(typeof window !== "undefined" ? window.localStorage : null).sidebarVisible);
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => loadRuntimeLayout(typeof window !== "undefined" ? window.localStorage : null).sidebarCollapsed);
  const [visualizerDensity] = useState<"calm" | "normal" | "dense">(() => loadRuntimeLayout(typeof window !== "undefined" ? window.localStorage : null).visualizerDensity);
  const [narrowLayout, setNarrowLayout] = useState<boolean>(() => typeof window !== "undefined" && window.innerWidth < 900);
  const [agentMode, setAgentMode] = useState<"idle" | "research" | "coding" | "working" | "thinking">("idle");
  const [agentActivity, dispatchActivity] = useReducer(agentActivityReducer, undefined, initialAgentActivity);
  const [visualizerPin, setVisualizerPin] = useState<DesktopVisualizerPanel | null>(null);
  useEffect(() => {
    if (mediaRouteActive) {
      setLeftTab("chat");
      if (desktopMode) {
        setDesktopSurface("visualizer");
        setVisualizerPin("media");
      }
      return;
    }
    if (desktopMode) setVisualizerPin((current) => current === "media" ? "ring" : current);
  }, [desktopMode, mediaRouteActive]);
  const [liveReplyDraft, setLiveReplyDraft] = useState("");
  const liveReplyDraftRef = useRef("");
  const [avatarConfig, setAvatarConfig] = useState<AvatarConfig>(defaultAvatarConfig);
  const [memoryItems, setMemoryItems] = useState<MemoryItem[]>([]);
  const [memoryCount, setMemoryCount] = useState<number>(0);
  const [memoryLoading, setMemoryLoading] = useState<boolean>(false);
  const [memoryError, setMemoryError] = useState<string | null>(null);
  const [memoryDoctor, setMemoryDoctor] = useState<MemoryDoctorReport | null>(null);
  const [memoryDoctorLoading, setMemoryDoctorLoading] = useState<boolean>(false);
  const [obsidianPlan, setObsidianPlan] = useState<any>(null);
  const [obsidianLoading, setObsidianLoading] = useState<boolean>(false);
  const [obsidianStatus, setObsidianStatus] = useState<string>("");
  const [docItems, setDocItems] = useState<DocumentItem[]>([]);
  const [servicesHeartbeatStatus, setServicesHeartbeatStatus] = useState<any>(null);
  const [servicesHeartbeatHistory, setServicesHeartbeatHistory] = useState<any[]>([]);
  const [servicesTelegramStatus, setServicesTelegramStatus] = useState<any>(null);
  const [servicesDiscordStatus, setServicesDiscordStatus] = useState<any>(null);
  const [servicesLoading, setServicesLoading] = useState<boolean>(false);
  const [studioOverview, setStudioOverview] = useState<any>(null);
  const [studioOverviewLoading, setStudioOverviewLoading] = useState<boolean>(false);
  const [connectionCatalog, setConnectionCatalog] = useState<any[]>([]);
  const [connectionError, setConnectionError] = useState<string>("");
  const [connectionBusyId, setConnectionBusyId] = useState<string>("");
  const [settingsCatalog, setSettingsCatalog] = useState<SettingsCatalogCard[]>([]);
  const [settingsCatalogLoading, setSettingsCatalogLoading] = useState<boolean>(false);
  const [settingsCatalogError, setSettingsCatalogError] = useState<string>("");
  const [settingsCatalogLoadedAt, setSettingsCatalogLoadedAt] = useState<number>(0);
  const [settingsCatalogScope, setSettingsCatalogScope] = useState<string>("");
  const [catalogModelDrafts, setCatalogModelDrafts] = useState<Record<string, string>>({});
  const settingsCatalogRequestRef = useRef(0);
  const [docCount, setDocCount] = useState<number>(0);
  const [docLoading, setDocLoading] = useState<boolean>(false);
  const [docEnabled, setDocEnabled] = useState<boolean>(false);
  const [docError, setDocError] = useState<string | null>(null);
  const [docSources, setDocSources] = useState<DocSource[]>([]);
  const [docFile, setDocFile] = useState<File | null>(null);
  const [docUploading, setDocUploading] = useState<boolean>(false);
  const [monitoring, setMonitoring] = useState<boolean>(false);
  const [monitorText, setMonitorText] = useState<string>("");
  const [monitorAt, setMonitorAt] = useState<number>(0);
  const [monitorError, setMonitorError] = useState<string | null>(null);
  const toolInfoRef = useRef<Record<string, { name: string; input: string; requestId?: string }>>({});
  const [capabilitiesData, setCapabilitiesData] = useState<any>(null);
  const [skillExecutions, setSkillExecutions] = useState<any[]>([]);
  const [memoryFilterType, setMemoryFilterType] = useState<string>("");
  const [selectedMemoryIds, setSelectedMemoryIds] = useState<string[]>([]);
  const [editingMemoryId, setEditingMemoryId] = useState<string | null>(null);
  const [editingMemoryText, setEditingMemoryText] = useState<string>("");
  const desktopBootstrap = typeof window !== "undefined" ? window.__ECHOSPEAK_DESKTOP_BOOTSTRAP__ : undefined;
  const [projects, setProjects] = useState<{
    id: string; name: string; description?: string; context_prompt?: string; tags?: string[];
    workspace_root?: string; archived?: boolean; git_metadata?: Record<string, any>;
  }[]>(() => (desktopBootstrap?.projects || []) as any[]);
  const [activeProjectId, setActiveProjectId] = useState<string>(() => desktopBootstrap?.active_project_id || "");
  const activeProjectIdRef = useRef<string>(desktopBootstrap?.active_project_id || "");
  const [folderDropActive, setFolderDropActive] = useState(false);
  const [projectsLoading, setProjectsLoading] = useState<boolean>(false);
  const [initialHydrationComplete, setInitialHydrationComplete] = useState(Boolean(desktopBootstrap));
  const [threadState, setThreadState] = useState<ThreadSessionState | null>(() => (desktopBootstrap?.thread_state || null) as ThreadSessionState | null);
  const [pendingApproval, setPendingApproval] = useState<PendingActionEnvelope | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [approvalsLoading, setApprovalsLoading] = useState<boolean>(false);
  const [approvalDecisionBusy, setApprovalDecisionBusy] = useState<boolean>(false);
  const [executions, setExecutions] = useState<ExecutionRecord[]>([]);
  const [executionsLoading, setExecutionsLoading] = useState<boolean>(false);
  const [selectedTrace, setSelectedTrace] = useState<Record<string, any> | null>(null);
  const [selectedTraceId, setSelectedTraceId] = useState<string>("");
  const [traceLoading, setTraceLoading] = useState<boolean>(false);
  const [latestExecutionId, setLatestExecutionId] = useState<string>("");
  const [latestTraceId, setLatestTraceId] = useState<string>("");
  const [routines, setRoutines] = useState<{ id: string; name: string; description?: string; enabled: boolean; trigger_type: string; schedule?: string; webhook_path?: string; action_type: string; action_config: Record<string, any>; last_run?: string; next_run?: string; run_count: number }[]>([]);
  const [routinesLoading, setRoutinesLoading] = useState<boolean>(false);

  const [threads, setThreads] = useState<{ id: string; name: string; at: number; projectId?: string; messageCount?: number }[]>(() =>
    (desktopBootstrap?.threads || []).map((item: any) => ({
      id: String(item.thread_id || item.id || ""),
      name: String(item.title || item.name || "Session"),
      at: normalizeTimestampMs(item.last_active_at || item.created_at || Date.now()),
      projectId: String(item.project_id || ""),
      messageCount: Number(item.message_count || 0),
    })),
  );
  const [activeThreadId, setActiveThreadId] = useState<string>(() => desktopBootstrap?.active_session_id || "");
  const currentWorkRuns = useWorkStore((state) => state.runs);
  const loadCurrentWorkRuns = useWorkStore((state) => state.loadRuns);
  const workProjectionRevisionRef = useRef<string>("");
  const activeThreadIdRef = useRef<string>(desktopBootstrap?.active_session_id || "");
  const threadCreationFlightRef = useRef<Promise<string> | null>(null);
  const streamControllersRef = useRef<Map<string, AbortController>>(new Map());
  const activeRequestIdsRef = useRef<Map<string, string>>(new Map());
  const activeExecutionIdsRef = useRef<Map<string, string>>(new Map());
  const activeTaskRunIdsRef = useRef<Map<string, string>>(new Map());
  const historyRequestSeqRef = useRef<Map<string, number>>(new Map());
  const projectionRevisionRef = useRef<Map<string, number>>(new Map());
  const sessionProjectionRef = useRef<Map<string, { messages: Message[]; activities: ActivityItem[] }>>(new Map());
  const [inFlightSessionIds, setInFlightSessionIds] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    if (!initialHydrationComplete || !activeThreadId) return;
    void loadCurrentWorkRuns(apiBase, activeThreadId, activeProjectId || "");
  }, [activeProjectId, activeThreadId, apiBase, initialHydrationComplete, loadCurrentWorkRuns]);

  useEffect(() => {
    if (!initialHydrationComplete || !activeThreadId) return;
    const activeStatuses = new Set([
      "running",
      "suspended_waiting_for_user",
      "suspended_waiting_for_approval",
    ]);
    if (!currentWorkRuns.some((run) => activeStatuses.has(String(run.status || "").toLowerCase()))) {
      workProjectionRevisionRef.current = currentWorkRuns
        .map((run) => `${run.id}:${run.revision}:${run.status}`)
        .join("|");
      return;
    }
    let cancelled = false;
    const poll = async () => {
      const before = useWorkStore.getState().runs
        .map((run) => `${run.id}:${run.revision}:${run.status}`)
        .join("|");
      await loadCurrentWorkRuns(apiBase, activeThreadId, activeProjectId || "");
      if (cancelled || activeThreadIdRef.current !== activeThreadId) return;
      const after = useWorkStore.getState().runs
        .map((run) => `${run.id}:${run.revision}:${run.status}`)
        .join("|");
      if (after && after !== before && after !== workProjectionRevisionRef.current) {
        workProjectionRevisionRef.current = after;
        await loadHistory(activeThreadId);
        void refreshExecutions(activeThreadId);
      }
    };
    workProjectionRevisionRef.current = currentWorkRuns
      .map((run) => `${run.id}:${run.revision}:${run.status}`)
      .join("|");
    const interval = window.setInterval(() => void poll(), 2500);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [
    activeProjectId,
    activeThreadId,
    apiBase,
    currentWorkRuns,
    initialHydrationComplete,
    loadCurrentWorkRuns,
  ]);

  const setSessionInFlight = useCallback((threadId: string, active: boolean) => {
    setInFlightSessionIds((current) => {
      const next = new Set(current);
      if (active) next.add(threadId);
      else next.delete(threadId);
      return next;
    });
  }, []);

  const cancelSessionTurn = useCallback((
    threadId: string,
    preserveStream = false,
    voiceTranscript?: VoiceTranscript,
  ) => {
    const sessionId = String(threadId || "").trim();
    if (!sessionId) return;
    const requestId = activeRequestIdsRef.current.get(sessionId);
    const executionId = activeExecutionIdsRef.current.get(sessionId) || "";
    // Navigation/supersession detaches local ownership immediately. The user
    // Stop control keeps the exact stream open so the durable cancellation and
    // final "Stopped by Ty." state can arrive from the backend.
    if (!preserveStream) {
      streamControllersRef.current.get(sessionId)?.abort();
      streamControllersRef.current.delete(sessionId);
      activeRequestIdsRef.current.delete(sessionId);
      activeExecutionIdsRef.current.delete(sessionId);
      activeTaskRunIdsRef.current.delete(sessionId);
      setSessionInFlight(sessionId, false);
    }
    if (requestId) {
      void fetch(`${apiBase}/query/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_id: requestId,
          execution_id: executionId,
          thread_id: sessionId,
          voice_turn_id: voiceTranscript?.voiceTurnId,
          voice_transcript: voiceTranscript?.text,
        }),
        keepalive: true,
      }).catch(() => undefined);
    }
  }, [apiBase, setSessionInFlight]);

  useEffect(() => {
    const cancelAll = () => {
      for (const sessionId of Array.from(activeRequestIdsRef.current.keys())) {
        cancelSessionTurn(sessionId);
      }
    };
    window.addEventListener("beforeunload", cancelAll);
    return () => window.removeEventListener("beforeunload", cancelAll);
  }, [cancelSessionTurn]);

  useEffect(() => {
    const applyBootstrap = (event: Event) => {
      const bootstrap = (event as CustomEvent).detail || window.__ECHOSPEAK_DESKTOP_BOOTSTRAP__;
      if (!bootstrap) return;
      const mapped = (Array.isArray(bootstrap.threads) ? bootstrap.threads : []).map((item: any) => ({
        id: String(item.thread_id || item.id || ""),
        name: String(item.title || item.name || "Session"),
        at: normalizeTimestampMs(item.last_active_at || item.created_at || Date.now()),
        projectId: String(item.project_id || ""),
        messageCount: Number(item.message_count || 0),
      }));
      setProjects(Array.isArray(bootstrap.projects) ? bootstrap.projects : []);
      setThreads(mapped);
      setActiveProjectId(String(bootstrap.active_project_id || ""));
      setThreadState((bootstrap.thread_state || null) as ThreadSessionState | null);
      const sessionId = String(bootstrap.active_session_id || "");
      const mustRebind = Boolean(sessionId && activeThreadIdRef.current === sessionId);
      if (mustRebind) {
        activeThreadIdRef.current = "";
        setActiveThreadId("");
        window.setTimeout(() => {
          activeThreadIdRef.current = sessionId;
          setActiveThreadId(sessionId);
        }, 0);
      } else {
        activeThreadIdRef.current = sessionId;
        setActiveThreadId(sessionId);
      }
      setInitialHydrationComplete(true);
    };
    window.addEventListener("echospeak-desktop-bootstrap", applyBootstrap);
    return () => window.removeEventListener("echospeak-desktop-bootstrap", applyBootstrap);
  }, []);

  useEffect(() => {
    activeThreadIdRef.current = activeThreadId;
    setStreaming(Boolean(activeThreadId && streamControllersRef.current.has(activeThreadId)));
  }, [activeThreadId, inFlightSessionIds, setStreaming]);

  useEffect(() => {
    activeProjectIdRef.current = activeProjectId;
  }, [activeProjectId]);

  useEffect(() => {
    if (activeThreadId) {
      localStorage.setItem("echospeak.active_thread_id", activeThreadId);
      // Keep legacy key updated for compatibility if needed
      localStorage.setItem("echospeak.thread_id", activeThreadId);
      loadHistory(activeThreadId);
      refreshThreadState(activeThreadId);
      refreshPendingApproval(activeThreadId);
      refreshApprovals(activeThreadId);
      refreshExecutions(activeThreadId);
    }
  }, [activeThreadId]);

  /**
   * Reconstruct the completed chat timeline from durable Session → Turn records.
   * Never parse assistant prose for tools/sources; never restart live stream chrome.
   */
  const loadHistory = async (threadId: string) => {
    const requestSeq = (historyRequestSeqRef.current.get(threadId) || 0) + 1;
    historyRequestSeqRef.current.set(threadId, requestSeq);
    const startingRevision = projectionRevisionRef.current.get(threadId) || 0;
    try {
      const rawTid = String(threadId || "").trim();
      const tid = encodeURIComponent(rawTid);
      const resp = await fetchWithTimeout(`${apiBase}/history?thread_id=${tid}`, undefined, 12000);
      // Canonical ToolRun list (Session-scoped) — merge when turns omit runs after restart.
      let sessionToolRuns: any[] = [];
      try {
        const trResp = await fetchWithTimeout(
          `${apiBase}/tool-runs?session_id=${encodeURIComponent(rawTid)}&limit=200`,
          undefined,
          8000
        );
        if (trResp.ok) {
          const trBody = await trResp.json();
          sessionToolRuns = Array.isArray(trBody?.items) ? trBody.items : [];
        }
      } catch {
        sessionToolRuns = [];
      }
      if (!resp.ok) return;
      const data = await resp.json();
      if (!canApplySessionHistory({
        activeSessionId: activeThreadIdRef.current,
        targetSessionId: threadId,
        currentRequestSeq: historyRequestSeqRef.current.get(threadId) || 0,
        requestSeq,
        currentRevision: projectionRevisionRef.current.get(threadId) || 0,
        startingRevision,
        streamInFlight: streamControllersRef.current.has(threadId),
      })) return;

      const turns: any[] = Array.isArray(data?.turns) ? data.turns : [];
      if (turns.length > 0) {
        const loadedMsgs: Message[] = [];
        const loadedActs: ActivityItem[] = [];
        const hydratedResearch: ResearchRun[] = [];
        const ctxWindow = Number(providerInfo?.context_window || 0) || 32768;
        const runsByTurn = new Map<string, any[]>();
        for (const run of sessionToolRuns) {
          const turnKey = String(run.turn_id || "").trim();
          if (!turnKey) continue;
          const bucket = runsByTurn.get(turnKey) || [];
          bucket.push(run);
          runsByTurn.set(turnKey, bucket);
        }

        for (const turn of turns) {
          const executionId = String(turn.execution_id || turn.execution?.id || "").trim();
          const turnStatus = String(turn.progress_status || turn.terminal_status || turn.status || "complete");
          const baseAt = Number(turn.created_at || 0) * 1000 || Date.now();
          const doneAt = Number(turn.completed_at || turn.created_at || 0) * 1000 || baseAt + 1;
          // Prefer turn.tool_runs; fall back to canonical /tool-runs by execution id.
          if ((!Array.isArray(turn.tool_runs) || turn.tool_runs.length === 0) && executionId) {
            const fromApi = runsByTurn.get(executionId) || [];
            if (fromApi.length) turn.tool_runs = fromApi;
          }

          // User + assistant messages (durable items / execution fallback)
          for (const msg of Array.isArray(turn.messages) ? turn.messages : []) {
            const role = String(msg.role || "").toLowerCase() === "user" ? "user" : "assistant";
            const text = String(msg.text || "").trim();
            if (!text) continue;
            const atMs = Number(msg.at || 0) * 1000 || (role === "user" ? baseAt : doneAt);
            const msgId = `hist-${executionId || "x"}-${role}-${msg.item_id || loadedMsgs.length}`;
            if (loadedMsgs.some((m) => m.id === msgId || (m.executionId === executionId && m.role === role && m.text === text))) {
              continue;
            }
            const researchRuns: ResearchRun[] = [];
            if (role === "assistant" && Array.isArray(turn.research_runs)) {
              for (const raw of turn.research_runs) {
                const normalized = normalizeResearchRun(raw);
                if (normalized) researchRuns.push(normalized);
              }
            }
            const embeds =
              role === "assistant" && researchRuns.length
                ? buildChatEmbeds({
                    answerText: text,
                    researchRuns,
                    searchQueries: researchRuns.map((r) => r.query).filter(Boolean),
                  })
                : undefined;
            const renderPlan =
              role === "assistant"
                ? buildResponseRenderPlan({
                    answerText: text,
                    researchRuns,
                    searchQueries: researchRuns.map((r) => r.query).filter(Boolean),
                  })
                : undefined;
            for (const r of researchRuns) {
              if (!hydratedResearch.some((h) => h.id === r.id)) hydratedResearch.push(r);
            }
            // Turn-scoped progress only — never attach full Session action lists
            // (that painted Pokémon research under a prior "whats up" chat Turn).
            const executionProjection =
              turn.execution_projection && typeof turn.execution_projection === "object"
                ? turn.execution_projection
                : {};
            const projectedRuns = Array.isArray(turn.tool_runs) ? turn.tool_runs : [];
            const runById = new Map(projectedRuns.map((run: any) => [String(run.id || ""), run]));
            const projectedAction = (runId: unknown, success: boolean) => {
              const run: any = runById.get(String(runId || ""));
              if (!run) return null;
              const outcome = run.outcome || {};
              return {
                execution_id: executionId,
                tool_run_id: String(run.id || ""),
                tool: String(run.tool_name || "tool"),
                summary: String(outcome.output || outcome.error_message || run.status || "").slice(0, 240),
                status: String(run.status || (success ? "complete" : "failed")),
                success,
                execution_status: String(outcome.execution_status || ""),
                result_state: String(outcome.result_state || ""),
                provider: String(outcome.provider || ""),
                observed_at: Number(outcome.observed_at || 0),
                confidence: outcome.confidence == null ? null : Number(outcome.confidence),
              };
            };
            const completedActions = (Array.isArray(executionProjection.successful_mutations)
              ? executionProjection.successful_mutations
              : [])
              .map((id: unknown) => projectedAction(id, true))
              .filter(Boolean) as Record<string, any>[];
            const failedActions = (Array.isArray(executionProjection.blocked_mutations)
              ? executionProjection.blocked_mutations
              : [])
              .map((id: unknown) => projectedAction(id, false))
              .filter(Boolean) as Record<string, any>[];
            const pendingActions = (Array.isArray(turn.approvals) ? turn.approvals : [])
              .filter((approval: any) => String(approval.status || "") === "pending")
              .map((approval: any) => ({
                execution_id: executionId,
                approval_id: String(approval.id || ""),
                tool: String(approval.tool || "action"),
                summary: String(approval.summary || approval.preview || "Awaiting approval"),
                status: "needs_permission",
                success: false,
              }));
            const changedFiles = (Array.isArray(executionProjection.files_actually_changed)
              ? executionProjection.files_actually_changed
              : [])
              .flatMap((item: any) => [String(item?.path || "").trim(), String(item?.destination || "").trim()])
              .filter(Boolean);
            const turnScopedState: OperationalThreadState = {
              thread_id: threadId,
              mode: String(turn.execution?.mode || "chat"),
              phase: String(turn.execution?.phase || ""),
              execution_status:
                turnStatus === "interrupted"
                  ? "in_progress"
                  : String(executionProjection.status || turnStatus || "complete"),
              current_execution_id: executionId,
              last_execution_id: executionId,
              terminal_status: String(turn.terminal_status || turnStatus),
              safest_next_action:
                turnStatus && !["complete", "completed", "ready", ""].includes(String(turnStatus))
                  ? String(executionProjection.next_action || turn.verification?.next_action || turn.progress?.status || "")
                  : "",
              completed_actions: completedActions,
              failed_actions: failedActions,
              pending_actions: pendingActions,
              plan_steps: [],
              retry_target:
                executionProjection.retry_target && typeof executionProjection.retry_target === "object"
                  ? executionProjection.retry_target
                  : {},
              operation_details: {
                tools_used: projectedRuns
                  .filter((r: any) => {
                    const st = String(r.status || "").toLowerCase();
                    return !["cancelled", "canceled", "interrupted"].includes(st);
                  })
                  .map((r: any) => String(r.tool_name || ""))
                  .filter(Boolean),
                files_changed: changedFiles,
                memory_records: Array.isArray(executionProjection.memory_records)
                  ? executionProjection.memory_records.map((item: any) => String(item?.memory_id || item?.item_id || "")).filter(Boolean)
                  : [],
              },
            } as OperationalThreadState;
            loadedMsgs.push({
              id: msgId,
              role,
              text,
              at: atMs,
              skipTypewriter: true,
              streamBeat: role === "assistant" ? "final" : undefined,
              executionId: executionId || undefined,
              clientRequestId: String(turn.request_id || turn.execution?.request_id || "") || undefined,
              embeds: embeds?.length ? embeds : undefined,
              renderPlan,
              operation:
                role === "assistant"
                  ? {
                      state: turnScopedState,
                      success:
                        turn.success !== false &&
                        !["failed", "blocked", "cancelled"].includes(String(turnStatus)),
                      executionId: executionId || undefined,
                    }
                  : undefined,
              usage: buildMessageUsage(text, loadedMsgs, ctxWindow, {
                provider: providerInfo?.provider,
                model: providerInfo?.model,
              }),
            });
          }

          // ToolRuns — exact IDs, completed/failed only (never live spinners after refresh)
          const runs = Array.isArray(turn.tool_runs) ? turn.tool_runs : [];
          for (const run of runs) {
            const runId = String(run.id || "").trim();
            const toolName = String(run.tool_name || "tool").trim();
            if (!runId || SILENT_CHAT_TOOLS.has(toolName)) continue;
            if (loadedActs.some((a) => a.kind === "tool" && a.id === runId)) continue;
            const args = run.canonical_arguments || {};
            const inputPreview = previewToolInput(
              toolName,
              typeof args === "object" ? JSON.stringify(args) : String(args || "")
            );
            const st = String(run.status || "").toLowerCase();
            const outcome = run.outcome || {};
            const outcomeOk = outcome.success === true || st === "complete" || st === "success";
            const outcomeFail =
              outcome.success === false ||
              st === "failed" ||
              st === "error" ||
              Boolean(outcome.error_message) ||
              Boolean(outcome.policy_block);
            let uiStatus: "running" | "done" | "error" = "done";
            if (outcomeFail) uiStatus = "error";
            else if (outcomeOk) uiStatus = "done";
            else if (st === "started" || st === "pending" || st === "running") {
              // A persisted nonterminal ToolRun has no success evidence. Never
              // turn it into "done" merely because its Turn was superseded or
              // finalized; hydrate it as interrupted without a live spinner.
              uiStatus = "error";
            }
            const outText =
              String(outcome.output || outcome.error_message || outcome.error_code || "").trim() ||
              (uiStatus === "error" && ["started", "pending", "running"].includes(st)
                ? "Interrupted before a terminal tool outcome was recorded"
                : "");
            // Place tools strictly inside this Turn's time window so they never
            // sort under a previous casual-chat assistant message.
            const atMs = Math.min(
              Math.max(Number(run.created_at || 0) * 1000 || baseAt + 10, baseAt + 1),
              Math.max(doneAt - 1, baseAt + 2)
            );
            // Skip pure wrapper fan-out shells — children are the canonical rows.
            if (
              toolName === "web_search" &&
              (String(outText || "").startsWith("(expanded to") ||
                String(outText || "").startsWith("(superseded by canonical"))
            ) {
              continue;
            }
            // Hydration: one user-facing web_search row per ToolRun id (already unique).
            // Skip cancelled/wrapper statuses that slipped past earlier filters.
            if (toolName === "web_search" && ["cancelled", "canceled"].includes(st)) {
              continue;
            }
            if (
              toolName === "file_list" && st === "interrupted" &&
              runs.some((other: any) => other.id !== run.id && other.tool_name === "file_list" && ["complete", "success"].includes(String(other.status || "").toLowerCase()))
            ) {
              continue;
            }
            loadedActs.push({
              kind: "tool",
              id: runId,
              name: toolName,
              input: inputPreview,
              status: uiStatus,
              output: outText ? outText.slice(0, 1500) : undefined,
              at: atMs,
            });
          }

          // Durable verification / denial errors as activity rows under the Turn timestamps
          if (turn.error && String(turn.error).trim()) {
            const errId = `hist-err-${executionId}`;
            if (!loadedActs.some((a) => a.id === errId)) {
              loadedActs.push({
                kind: "error",
                id: errId,
                message: String(turn.error).trim().slice(0, 800),
                at: doneAt,
              });
            }
          }
        }

        // Replace — never append (idempotent refresh / session switch)
        useAppStore.setState({ messages: loadedMsgs });
        setActivities(loadedActs);
        sessionProjectionRef.current.set(threadId, { messages: loadedMsgs, activities: loadedActs });
        // Historical research for the Visualizer panel; Chat embeds stay on assistant messages.
        if (hydratedResearch.length) {
          replaceResearchRuns(hydratedResearch);
        } else {
          clearResearchRuns();
        }
        // Never resume live stream chrome from history.
        setAgentMode("idle");
        setEchoReaction(null);
        dispatchActivity({ type: "reset" });
        return;
      }

      // Legacy fallback: string-only history
      if (data && data.history && Array.isArray(data.history)) {
        const loadedMsgs = data.history
          .map((h: string, i: number) => {
            const isUser = h.startsWith("Human:");
            const text = h.replace(/^(Human:|Assistant:)\s*/, "").trim();
            return {
              id: `hist-legacy-${threadId}-${i}`,
              role: (isUser ? "user" : "assistant") as Role,
              text,
              at: Date.now() - (data.history.length - i) * 1000,
              skipTypewriter: true,
            };
          })
          .filter((m: Message) => m.text);
        useAppStore.setState({ messages: loadedMsgs });
        setActivities([]);
        sessionProjectionRef.current.set(threadId, { messages: loadedMsgs, activities: [] });
        clearResearchRuns();
        setAgentMode("idle");
      }
    } catch (e) {
      console.error("Failed to load history:", e);
    }
  };

  useEffect(() => {
    if (leftTab === "capabilities") {
      fetch(`${apiBase}/capabilities?thread_id=${encodeURIComponent(activeThreadId)}`)
        .then(res => res.json())
        .then(data => setCapabilitiesData(data))
        .catch(e => console.error("Failed to fetch capabilities:", e));
      fetch(`${apiBase}/skills/executions?session_id=${encodeURIComponent(activeThreadId)}&limit=20`)
        .then(res => res.ok ? res.json() : { items: [] })
        .then(data => setSkillExecutions(Array.isArray(data.items) ? data.items : []))
        .catch(e => console.error("Failed to fetch skill executions:", e));
    }
  }, [leftTab, activeThreadId, activeProjectId]);

  const refreshThreads = async () => {
    try {
      const resp = await fetchWithTimeout(`${apiBase}/threads?limit=50`, undefined, 6000);
      if (!resp.ok) throw new Error(`Threads failed (${resp.status})`);
      const data = await resp.json();
      const items = Array.isArray(data) ? data : [];
      const selectedId = activeThreadIdRef.current || localStorage.getItem("echospeak.active_thread_id") || "";
      const mapped = items.map((item: any) => ({
        id: String(item.thread_id || item.id || ""),
        name: String(item.title || item.name || "Session"),
        at: normalizeTimestampMs(item.last_active_at || item.created_at || Date.now()),
        projectId: String(item.project_id || ""),
        messageCount: Number(item.message_count || 0),
      })).filter((item: any) => {
        if (!item.id) return false;
        const placeholder = isEmptySessionDraft(item);
        if (!placeholder) return true;
        // A zero-message Session is a draft. Show it only while it is the
        // explicitly selected draft; never resurrect abandoned placeholders
        // after an AI response or Session refresh.
        if (item.id !== selectedId) return false;
        item.name = "New Session";
        return true;
      });
      if (mapped.length) {
        setThreads(mapped);
        const nextId = mapped.some((item: any) => item.id === selectedId) ? selectedId : mapped[0].id;
        activeThreadIdRef.current = nextId;
        setActiveThreadId(nextId);
      } else {
        // Session creation belongs exclusively to explicit New Session/+ UI.
        activeThreadIdRef.current = "";
        setActiveThreadId("");
        setThreads([]);
        useAppStore.setState({ messages: [] });
        setActivities([]);
      }
      return true;
    } catch (e) {
      console.error("Failed to refresh threads:", e);
      return false;
    }
  };

  const refreshThreadState = async (threadId: string = activeThreadId) => {
    if (!threadId) return null;
    try {
      const resp = await fetchWithTimeout(`${apiBase}/threads/${encodeURIComponent(threadId)}/state`, undefined, 5000);
      if (!resp.ok) throw new Error(`Thread state failed (${resp.status})`);
      const data = (await resp.json()) as ThreadSessionState;
      if (activeThreadIdRef.current !== threadId) return data;
      setThreadState(data);
      setActiveProjectId(String(data.active_project_id || ""));
      setLatestExecutionId(String(data.last_execution_id || ""));
      setLatestTraceId(String(data.last_trace_id || ""));
      return data;
    } catch (e) {
      console.error("Failed to refresh thread state:", e);
      return null;
    }
  };

  const refreshPendingApproval = async (threadId: string = activeThreadId) => {
    if (!threadId) return null;
    try {
      const resp = await fetchWithTimeout(`${apiBase}/pending-action?thread_id=${encodeURIComponent(threadId)}`, undefined, 5000);
      if (!resp.ok) throw new Error(`Pending action failed (${resp.status})`);
      const data = (await resp.json()) as PendingActionEnvelope;
      if (activeThreadIdRef.current !== threadId) return data;
      setPendingApproval(data);
      return data;
    } catch (e) {
      console.error("Failed to refresh pending approval:", e);
      return null;
    }
  };

  const decideApproval = async (approvalId: string, decision: "confirm" | "cancel") => {
    if (!approvalId || approvalDecisionBusy) return;
    const expectedSessionId = String(activeThreadIdRef.current || "").trim();
    if (!expectedSessionId) return;
    // Disable immediately so duplicate rapid clicks cannot fire a second mutation.
    setApprovalDecisionBusy(true);
    try {
      const resp = await fetchWithTimeout(
        `${apiBase}/approvals/${encodeURIComponent(approvalId)}/${decision}?expected_session_id=${encodeURIComponent(expectedSessionId)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
        },
        30000,
        // Mutations must never auto-replay on 429.
        { retrySafeGetOn429: false },
      );
      if (!resp.ok) {
        const detail = await resp.text().catch(() => "");
        if (resp.status === 409) {
          throw new Error("That approval is stale or already consumed; nothing was re-executed.");
        }
        if (resp.status === 429) {
          // Do not retry mutation. Surface rate limit honestly.
          throw new Error("Rate limited while confirming. The approval was not re-sent automatically.");
        }
        throw new Error(`Approval failed (${resp.status}): ${detail}`);
      }
      const data = (await resp.json()) as ApprovalDecisionEnvelope;
      if (activeThreadIdRef.current !== expectedSessionId) return;
      if (data.thread_state) {
        setThreadState(data.thread_state);
        setLatestExecutionId(String(data.execution_id || data.thread_state.last_execution_id || ""));
      }
      // Terminal projection: clear pending only after successful HTTP response.
      setPendingApproval(null);
      if (data.response) {
        addMessage({
          id: crypto.randomUUID(),
          role: "assistant",
          text: data.response,
          at: Date.now(),
          skipTypewriter: true,
          operation: data.thread_state ? {
            state: data.thread_state,
            success: Boolean(data.success),
            executionId: data.execution_id || undefined,
          } : undefined,
        });
      }
      setEchoReaction(data.success ? "success" : "error");
    } catch (error) {
      if (activeThreadIdRef.current !== expectedSessionId) return;
      const message = error instanceof Error ? error.message : String(error);
      setEchoReaction("error");
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: message, at: Date.now(), skipTypewriter: true });
    } finally {
      setApprovalDecisionBusy(false);
      if (activeThreadIdRef.current === expectedSessionId) {
        // Hydration failures must not look like mutation failure (safe GET may retry once).
        await refreshThreadState(expectedSessionId);
        await refreshPendingApproval(expectedSessionId);
        await refreshApprovals(expectedSessionId);
        await refreshExecutions(expectedSessionId);
      }
    }
  };

  const refreshApprovals = async (threadId: string = activeThreadId) => {
    if (!threadId) return;
    setApprovalsLoading(true);
    try {
      const resp = await fetchWithTimeout(`${apiBase}/approvals?thread_id=${encodeURIComponent(threadId)}&limit=25`, undefined, 6000);
      if (!resp.ok) throw new Error(`Approvals failed (${resp.status})`);
      const data = (await resp.json()) as ApprovalListResponse;
      if (activeThreadIdRef.current !== threadId) return;
      setApprovals(Array.isArray(data.items) ? data.items : []);
    } catch (e) {
      console.error("Failed to refresh approvals:", e);
    } finally {
      setApprovalsLoading(false);
    }
  };

  const refreshExecutions = async (threadId: string = activeThreadId) => {
    if (!threadId) return;
    setExecutionsLoading(true);
    try {
      const resp = await fetchWithTimeout(`${apiBase}/executions?thread_id=${encodeURIComponent(threadId)}&limit=25`, undefined, 6000);
      if (!resp.ok) throw new Error(`Executions failed (${resp.status})`);
      const data = (await resp.json()) as ExecutionListResponse;
      if (activeThreadIdRef.current !== threadId) return;
      setExecutions(Array.isArray(data.items) ? data.items : []);
    } catch (e) {
      console.error("Failed to refresh executions:", e);
    } finally {
      setExecutionsLoading(false);
    }
  };

  const loadTrace = async (traceId: string) => {
    if (!traceId) return;
    setTraceLoading(true);
    setSelectedTraceId(traceId);
    try {
      const resp = await fetchWithTimeout(`${apiBase}/traces/${encodeURIComponent(traceId)}`, undefined, 7000);
      if (!resp.ok) throw new Error(`Trace failed (${resp.status})`);
      const data = await resp.json();
      setSelectedTrace(data && typeof data === "object" ? data : null);
    } catch (e) {
      console.error("Failed to load trace:", e);
      setSelectedTrace(null);
    } finally {
      setTraceLoading(false);
    }
  };

  const refreshProjects = async () => {
    setProjectsLoading(true);
    try {
      const res = await fetch(`${apiBase}/projects`);
      if (!res.ok) throw new Error(`Projects failed (${res.status})`);
      const data = await res.json();
      setProjects(data.items || []);
      return true;
    } catch (e) {
      console.error("Failed to load projects:", e);
      return false;
    } finally {
      setProjectsLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    const hydrate = async () => {
      // Dashboard mounts only after the desktop host reports ready, but a
      // recovering sidecar can still race the first authenticated requests.
      // Retry safe reads; never create a Session as part of hydration.
      let hydrated = false;
      for (let attempt = 0; attempt < 4 && !cancelled; attempt += 1) {
        const [threadsReady, projectsReady] = await Promise.all([refreshThreads(), refreshProjects()]);
        if (threadsReady && projectsReady) {
          hydrated = true;
          break;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 350 * (attempt + 1)));
      }
      if (!cancelled && hydrated) setInitialHydrationComplete(true);
    };
    void hydrate();
    return () => { cancelled = true; };
  }, [apiBase]);

  const createNewThread = async (projectId: string = "", requestedTitle: string = "New Session"): Promise<string> => {
    if (threadCreationFlightRef.current) return threadCreationFlightRef.current;
    const idempotencyKey = crypto.randomUUID();
    const flight = (async (): Promise<string> => {
      try {
        const resp = await fetch(`${apiBase}/threads`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: requestedTitle, source: "web", project_id: projectId, idempotency_key: idempotencyKey }),
        });
        if (!resp.ok) throw new Error(`Create thread failed (${resp.status})`);
        const data = await resp.json();
        const nextThread = { id: String(data.thread_id), name: String(data.title || "New Session"), at: normalizeTimestampMs(data.last_active_at || data.created_at || Date.now()), projectId: String(data.project_id || projectId || "") };
        projectionRevisionRef.current.set(nextThread.id, 0);
        sessionProjectionRef.current.set(nextThread.id, { messages: [], activities: [] });
        activeThreadIdRef.current = nextThread.id;
        setThreads((prev) => [
          nextThread,
          ...prev.filter((item) => item.id !== nextThread.id && !isEmptySessionDraft(item)),
        ]);
        setActiveThreadId(nextThread.id);
        useAppStore.setState({ messages: [] });
        setActivities([]);
        setTaskPlans([]);
        clearResearchRuns();
        setPendingApproval(null);
        setApprovals([]);
        setExecutions([]);
        setSelectedTrace(null);
        dispatchActivity({ type: "reset" });
        setStreaming(false);
        liveReplyDraftRef.current = "";
        setLiveReplyDraft("");
        setDocSources([]);
        toolInfoRef.current = {};
        return nextThread.id;
      } catch (e) {
        console.error("Failed to create thread:", e);
        return "";
      }
    })();
    threadCreationFlightRef.current = flight;
    try {
      return await flight;
    } finally {
      if (threadCreationFlightRef.current === flight) threadCreationFlightRef.current = null;
    }
  };

  const switchThread = (id: string) => {
    if (id === activeThreadId) return;
    // A Session owns its request. Navigation changes only the projection;
    // it must not cancel another Session's durable execution.
    const previousId = String(activeThreadIdRef.current || activeThreadId || "");
    if (previousId) {
      sessionProjectionRef.current.set(previousId, {
        messages: useAppStore.getState().messages,
        activities,
      });
    }
    activeThreadIdRef.current = id;
    setThreads((prev) => prev.filter((item) => item.id === id || !isEmptySessionDraft(item)));
    setActiveThreadId(id);
    dispatchActivity({ type: "reset" });
    setStreaming(streamControllersRef.current.has(id));
    liveReplyDraftRef.current = "";
    setLiveReplyDraft("");
    setDocSources([]);
    toolInfoRef.current = {};
    // In a real app, we might fetch history from backend here.
    // For now, we'll clear local state to start fresh in the new context.
    const cachedProjection = sessionProjectionRef.current.get(id);
    useAppStore.setState({ messages: cachedProjection?.messages || [] });
    setActivities(cachedProjection?.activities || []);
    setTaskPlans([]);
    clearResearchRuns();
    setPendingApproval(null);
    setApprovals([]);
    setExecutions([]);
    setSelectedTrace(null);
    setSelectedTraceId("");
  };

  const deleteThread = async (id: string) => {
    try {
      const response = await fetch(`${apiBase}/threads/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!response.ok) throw new Error(`Delete Session failed (${response.status})`);
    } catch (e2) {
      console.error("Failed to delete thread:", e2);
    }
    const nextThreads = threads.filter((t) => t.id !== id);
    setThreads(nextThreads);
    if (id === activeThreadId) {
      if (nextThreads[0]) switchThread(nextThreads[0].id);
      else {
        cancelSessionTurn(id);
        activeThreadIdRef.current = "";
        setActiveThreadId("");
        useAppStore.setState({ messages: [] });
        setActivities([]);
        setPendingApproval(null);
        setThreadState(null);
        setActiveProjectId("");
      }
    }
  };

  const attachFolder = async (candidatePath: string = "") => {
    let path = candidatePath.trim();
    if (!path) {
      if (desktopMode) {
        try { path = String(await pickDesktopProjectFolder() || ""); } catch { /* surfaced by the desktop host */ }
      } else {
        try {
          const picker = await fetch(`${apiBase}/projects/pick-folder`, { method: "POST" });
          if (picker.ok) path = String((await picker.json()).path || "");
        } catch { /* native picker may not be available outside the desktop host */ }
      }
    }
    if (!path) return;
    const response = await fetch(`${apiBase}/projects/attach-folder`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, session_id: activeThreadId, trust_state: "trusted" }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "Could not attach folder");
    const project = await response.json();
    await refreshProjects();
    setActiveProjectId(String(project.id || ""));
    setThreads(items => items.map(item => item.id === activeThreadId ? { ...item, projectId: String(project.id || "") } : item));
    await refreshThreadState(activeThreadId);
  };

  const folderPathFromDrop = (event: React.DragEvent): string => {
    const file = event.dataTransfer.files?.[0] as (File & { path?: string }) | undefined;
    if (file?.path) return file.path;
    const uri = event.dataTransfer.getData("text/uri-list").split(/\r?\n/).find(line => line && !line.startsWith("#")) || "";
    const plain = event.dataTransfer.getData("text/plain").trim();
    const value = uri || plain;
    if (/^file:\/\//i.test(value)) {
      try { return decodeURIComponent(new URL(value).pathname).replace(/^\/(?:([A-Za-z]:))/, "$1"); } catch { return ""; }
    }
    return /^[A-Za-z]:[\\/]/.test(value) ? value : "";
  };

  const renameThread = async (id: string, title: string) => {
    const response = await fetch(`${apiBase}/threads/${encodeURIComponent(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) });
    if (!response.ok) return;
    const data = await response.json();
    setThreads(items => items.map(item => item.id === id ? { ...item, name: String(data.title || title) } : item));
  };

  const docInputRef = useRef<HTMLInputElement | null>(null);
  const bootedRef = useRef(false);
  const backendRetryRef = useRef<{ attempt: number; timer: number | null }>({ attempt: 0, timer: null });
  const refreshAvatarConfig = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/avatar/config`);
      if (!res.ok) return;
      const data = await res.json();
      setAvatarConfig({ ...defaultAvatarConfig, ...data });
    } catch {
      // ignore bootstrap avatar failures
    }
  }, [apiBase]);

  useEffect(() => {
    refreshAvatarConfig();
  }, [refreshAvatarConfig]);

  const [providerInfo, setProviderInfo] = useState<ProviderInfo | null>(null);
  const [providerModels, setProviderModels] = useState<string[]>([]);
  const [providerDraft, setProviderDraft] = useState<{ provider: string; model: string; base_url: string }>({
    provider: "",
    model: "",
    base_url: "",
  });
  const [thinkingEnabled, setThinkingEnabled] = useState<boolean>(
    () => window.localStorage.getItem("echospeak.chat.thinking_enabled") !== "false",
  );
  const [reasoningEffort, setReasoningEffort] = useState<
    "minimal" | "low" | "medium" | "high" | "extra_high" | "max" | "ultra"
  >(() => {
    const stored = window.localStorage.getItem("echospeak.chat.reasoning_effort");
    return stored === "minimal" || stored === "low" || stored === "medium" || stored === "high" ||
      stored === "extra_high" || stored === "max" || stored === "ultra"
      ? stored
      : "medium";
  });
  const [voiceReadAloud, setVoiceReadAloud] = useState<boolean>(
    () => window.localStorage.getItem("echospeak.voice.read_aloud") === "true",
  );
  const [voiceConversationMode, setVoiceConversationMode] = useState<boolean>(false);
  const wakeWordEnabled = false;
  const [voicePhase, setVoicePhase] = useState<VoiceTransportPhase>("idle");
  const [voiceNotice, setVoiceNotice] = useState("");
  const [voiceInputLevel, setVoiceInputLevel] = useState(0);
  const voiceInputRef = useRef<LocalVoiceInput | null>(null);
  if (voiceInputRef.current == null) voiceInputRef.current = new LocalVoiceInput();
  const [showSteerModal, setShowSteerModal] = useState<boolean>(false);
  const [steerInput, setSteerInput] = useState<string>("");
  const [steerSubmitting, setSteerSubmitting] = useState<boolean>(false);
  useEffect(() => {
    window.localStorage.setItem("echospeak.voice.read_aloud", String(voiceReadAloud));
  }, [voiceReadAloud]);
  useEffect(() => {
    window.localStorage.setItem("echospeak.chat.thinking_enabled", String(thinkingEnabled));
  }, [thinkingEnabled]);
  useEffect(() => {
    window.localStorage.setItem("echospeak.chat.reasoning_effort", reasoningEffort);
  }, [reasoningEffort]);
  const lmStudioOnly = useMemo(() => isLmStudioOnlyLocked(providerInfo), [providerInfo]);
  const [providerError, setProviderError] = useState<string | null>(null);
  const [switchingProvider, setSwitchingProvider] = useState(false);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const [showSessions, setShowSessions] = useState(false);
  const gatewaySocketRef = useRef<WebSocket | null>(null);
  const gatewayRetryTimerRef = useRef<number | null>(null);
  const gatewayRetryAttemptRef = useRef<number>(0);
  const [discordGatewayConnected, setDiscordGatewayConnected] = useState<boolean>(false);
  const [discordGatewaySessionId, setDiscordGatewaySessionId] = useState<string>("");
  const [discordLiveEvents, setDiscordLiveEvents] = useState<DiscordLiveEvent[]>([]);
  const [spotifyPlaying, setSpotifyPlaying] = useState<{ is_playing: boolean; track_id: string; track_name: string; track_artist: string } | null>(null);

  const [runtimeSettings, setRuntimeSettings] = useState<Record<string, any> | null>(null);
  const [runtimeOverrides, setRuntimeOverrides] = useState<Record<string, any> | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<Record<string, any>>({});
  const [allowlistDraftText, setAllowlistDraftText] = useState("");
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsIssues, setSettingsIssues] = useState<{ key: string; message: string; severity: "error" | "warning" }[]>([]);
  const [settingsSavedAt, setSettingsSavedAt] = useState<number | null>(null);
  const [settingsTests, setSettingsTests] = useState<Record<string, SettingsTestResult | null>>({});
  const [settingsTesting, setSettingsTesting] = useState<Record<string, boolean>>({});
  const [settingsTestedKeys, setSettingsTestedKeys] = useState<Record<string, string>>({});

  // Soul state
  const [soulContent, setSoulContent] = useState<string>("");
  const [soulEnabled, setSoulEnabled] = useState<boolean>(true);
  const [soulPath, setSoulPath] = useState<string>("./SOUL.md");

  const [soulMaxChars, setSoulMaxChars] = useState<number>(8000);
  const [soulExists, setSoulExists] = useState<boolean>(false);
  const [soulLoading, setSoulLoading] = useState<boolean>(false);
  const [soulSaving, setSoulSaving] = useState<boolean>(false);
  const [soulError, setSoulError] = useState<string | null>(null);
  const [soulSavedAt, setSoulSavedAt] = useState<number | null>(null);

  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  const chatBottomRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);
  const sessionScrollRef = useRef<Map<string, { top: number; atBottom: boolean }>>(new Map());
  /** Ignore scroll events caused by our own pin-to-bottom so we never unstick mid-update. */
  const programmaticScrollRef = useRef(false);
  const pinBottomRafRef = useRef(0);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      const nextHeight = Math.min(el.scrollHeight, 156);
      el.style.height = `${nextHeight}px`;
      el.style.overflowY = el.scrollHeight > 156 ? "auto" : "hidden";
    }
  }, [input]);

  const lastAppliedProviderRef = useRef<{ provider: string; model: string } | null>(null);
  const suppressAutoApplyRef = useRef(true);

  const scheduleBackendRetry = () => {
    if (backendRetryRef.current.timer != null) return;
    const attempt = backendRetryRef.current.attempt;
    // Backoff is deliberately slow after the initial recovery window. The old
    // six-second ceiling could hammer a failing provider endpoint indefinitely
    // and turn one backend exception into a noisy desktop-wide failure loop.
    const delay = Math.min(30000, Math.round(900 * Math.pow(1.8, attempt)));
    backendRetryRef.current.attempt = Math.min(attempt + 1, 8);
    backendRetryRef.current.timer = window.setTimeout(() => {
      backendRetryRef.current.timer = null;
      refreshProviderInfo({ allowRetry: true });
    }, delay);
  };

  const refreshProviderInfo = async (opts: { allowRetry?: boolean } = {}) => {
    try {
      setProviderError(null);
      const scope = new URLSearchParams({ session_id: String(activeThreadIdRef.current || "default") });
      const resp = await fetchWithTimeout(`${apiBase}/provider?${scope.toString()}`, undefined, 10000);
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
      const info = (await resp.json()) as ProviderInfo;
      setProviderInfo(info);
      setBackendOnline(true);
      backendRetryRef.current.attempt = 0;
      if (backendRetryRef.current.timer != null) {
        window.clearTimeout(backendRetryRef.current.timer);
        backendRetryRef.current.timer = null;
      }
      lastAppliedProviderRef.current = { provider: info.provider, model: info.model };
      suppressAutoApplyRef.current = false;
      setProviderDraft((d) => ({
        ...d,
        provider: info.provider,
        model: info.model,
        base_url: info.base_url ? String(info.base_url) : d.base_url,
      }));
    } catch (e) {
      setBackendOnline(false);
      const err = e instanceof Error ? e : new Error(String(e));
      const msg = err.message || String(e);
      const aborted = err.name === "AbortError" || msg.toLowerCase().includes("aborted");
      const offline = aborted || msg.includes("Failed to fetch");
      const serverFailure = /\b5\d\d\b/.test(msg);
      const pretty = offline ? "Backend offline" : msg;
      const shouldRetry = Boolean(opts.allowRetry && (offline || serverFailure));
      setProviderError(offline && shouldRetry ? "Backend offline — retrying" : pretty);
      if (shouldRetry) scheduleBackendRetry();
    }
  };

  const refreshSettings = async () => {
    setSettingsLoading(true);
    setSettingsError(null);
    try {
      const resp = await fetchWithTimeout(`${apiBase}/settings`, undefined, 10000);
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
      const data = (await resp.json()) as RuntimeSettingsEnvelope;
      const effective = (data && typeof data === "object" ? (data.settings as Record<string, any>) : null) || null;
      const overrides = (data && typeof data === "object" ? (data.overrides as Record<string, any>) : null) || null;
      const issues = Array.isArray(data?.issues) ? data.issues : [];
      setRuntimeSettings(effective);
      setRuntimeOverrides(overrides);
      const merged = { ...(effective || {}), ...(overrides || {}) };
      merged.open_application_allowlist = coerceAllowlistValue(merged.open_application_allowlist);
      setSettingsDraft(merged);
      setAllowlistDraftText("");
      setSettingsIssues(issues);
    } catch (e) {
      const err = e instanceof Error ? e : new Error(String(e));
      setSettingsError(err.message || String(e));
      setSettingsIssues([]);
    } finally {
      setSettingsLoading(false);
    }
  };

  const refreshSoul = async () => {
    setSoulLoading(true);
    setSoulError(null);
    try {
      const resp = await fetchWithTimeout(`${apiBase}/soul`);
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
      const data = await resp.json() as { enabled: boolean; path: string; content: string; max_chars: number; exists: boolean };
      setSoulEnabled(data.enabled);
      setSoulPath(data.path);
      setSoulContent(data.content);
      setSoulMaxChars(data.max_chars);
      setSoulExists(data.exists);
    } catch (e) {
      const err = e instanceof Error ? e : new Error(String(e));
      setSoulError(err.message || String(e));
    } finally {
      setSoulLoading(false);
    }
  };

  const saveSoul = async () => {
    setSoulSaving(true);
    setSoulError(null);
    try {
      const resp = await fetchWithTimeout(`${apiBase}/soul`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: soulContent }),
      });
      if (!resp.ok) {
        let details: any = null;
        try {
          details = await resp.json();
        } catch {
          details = await resp.text().catch(() => "");
        }
        throw new Error(`Save failed (${resp.status}): ${typeof details === "string" ? details : details?.detail || resp.statusText}`);
      }
      const data = await resp.json() as { enabled: boolean; path: string; content: string; max_chars: number; exists: boolean };
      setSoulEnabled(data.enabled);
      setSoulPath(data.path);
      setSoulContent(data.content);
      setSoulMaxChars(data.max_chars);
      setSoulExists(data.exists);
      setSoulSavedAt(Date.now());
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: "Soul updated. Changes will apply to new conversations.", at: Date.now() });
    } catch (e) {
      const err = e instanceof Error ? e : new Error(String(e));
      setSoulError(err.message || String(e));
    } finally {
      setSoulSaving(false);
    }
  };

  const saveSettings = async () => {
    setSettingsSaving(true);
    setSettingsError(null);
    try {
      // Commit any unfinished allowlist draft so Enter/Add is not required before Save.
      const allowCommitted = commitAllowlistDraft(
        coerceAllowlistValue(settingsDraft.open_application_allowlist),
        allowlistDraftText,
        { force: true }
      );
      const payload = {
        ...(settingsDraft || {}),
        open_application_allowlist: allowCommitted.entries,
      };
      setAllowlistDraftText("");
      setSettingsDraft((d) => ({ ...d, open_application_allowlist: allowCommitted.entries }));
      const resp = await fetchWithTimeout(`${apiBase}/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        let details: any = null;
        try {
          details = await resp.json();
        } catch {
          details = await resp.text().catch(() => "");
        }
        if (resp.status === 422 && details && typeof details === "object") {
          const issues = (details as any)?.detail?.issues;
          if (Array.isArray(issues)) setSettingsIssues(issues);
          throw new Error((details as any)?.detail?.message || "Invalid settings");
        }
        throw new Error(`Save failed (${resp.status}): ${typeof details === "string" ? details : resp.statusText}`);
      }
      const data = (await resp.json()) as RuntimeSettingsEnvelope;
      setRuntimeSettings(data.settings || null);
      setRuntimeOverrides(data.overrides || null);
      {
        const merged = { ...((data.settings as any) || {}), ...((data.overrides as any) || {}) };
        merged.open_application_allowlist = coerceAllowlistValue(merged.open_application_allowlist);
        setSettingsDraft(merged);
        setAllowlistDraftText("");
      }
      setSettingsIssues(Array.isArray(data?.issues) ? data.issues : []);
      setSettingsSavedAt(Date.now());
      await refreshProviderInfo();
      await refreshServices();
    } catch (e) {
      const err = e instanceof Error ? e : new Error(String(e));
      setSettingsError(err.message || String(e));
    } finally {
      setSettingsSaving(false);
    }
  };

  const settingsErrors = useMemo(() => settingsIssues.filter((i) => i.severity === "error"), [settingsIssues]);
  const settingsWarnings = useMemo(() => settingsIssues.filter((i) => i.severity === "warning"), [settingsIssues]);

  const issueByKey = useMemo(() => {
    const map: Record<string, { message: string; severity: "error" | "warning" }> = {};
    for (const it of settingsIssues) {
      if (!it || typeof it.key !== "string") continue;
      if (map[it.key]) continue;
      map[it.key] = { message: it.message, severity: it.severity };
    }
    return map;
  }, [settingsIssues]);

  const getIssue = (key: string) => issueByKey[key];
  const isError = (key: string) => getIssue(key)?.severity === "error";

  const RequiredBadge = ({ issueKey }: { issueKey: string }) => {
    const it = getIssue(issueKey);
    if (!it) return null;
    const color = it.severity === "error" ? colors.danger : "#f59e0b";
    return (
      <span
        title={it.message}
        style={{
          marginLeft: 8,
          fontSize: 11,
          fontWeight: 800,
          padding: "2px 8px",
          borderRadius: 999,
          background: `${color}22`,
          border: `1px solid ${color}55`,
          color,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
        }}
      >
        {it.severity === "error" ? "Required" : "Check"}
      </span>
    );
  };

  const runSettingsTest = async (target: "openai" | "gemini" | "local" | "ollama") => {
    setSettingsTesting((m) => ({ ...m, [target]: true }));
    try {
      const payload: any = { target };
      if (target === "openai") {
        payload.api_key = String(settingsDraft?.openai?.api_key || "") === "***" ? "" : String(settingsDraft?.openai?.api_key || "");
        setSettingsTestedKeys((m) => ({ ...m, openai: payload.api_key }));
      } else if (target === "gemini") {
        payload.api_key = String(settingsDraft?.gemini?.api_key || "") === "***" ? "" : String(settingsDraft?.gemini?.api_key || "");
        setSettingsTestedKeys((m) => ({ ...m, gemini: payload.api_key }));
      } else {
        payload.provider = String(settingsDraft?.local?.provider || providerDraft.provider || "");
        payload.base_url = String(settingsDraft?.local?.base_url || providerDraft.base_url || "");
        payload.model = String(settingsDraft?.local?.model_name || providerDraft.model || "");
      }

      const resp = await fetchWithTimeout(`${apiBase}/settings/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }, 10000);
      const data = (await resp.json().catch(() => null)) as SettingsTestResult | null;
      if (!resp.ok) {
        const msg = (data as any)?.message || `${resp.status} ${resp.statusText}`;
        setSettingsTests((m) => ({ ...m, [target]: { ok: false, target, message: String(msg) } }));
        return;
      }
      setSettingsTests((m) => ({ ...m, [target]: data }));
    } catch (e) {
      setSettingsTests((m) => ({ ...m, [target]: { ok: false, target, message: String(e) } }));
    } finally {
      setSettingsTesting((m) => ({ ...m, [target]: false }));
    }
  };

  useEffect(() => {
    if (leftTab === "advanced_settings") {
      refreshSettings();
    }
  }, [leftTab]);

  const updateDraft = (key: string, value: any) => {
    setSettingsDraft((d) => ({ ...d, [key]: value }));
  };

  const updateDraftSection = (section: string, key: string, value: any) => {
    setSettingsDraft((d) => ({
      ...d,
      [section]: { ...((d as any)[section] || {}), [key]: value },
    }));
  };

  const refreshMemory = async () => {
    setMemoryLoading(true);
    setMemoryError(null);
    try {
      const tid = encodeURIComponent(String(activeThreadId || "").trim());
      const threadQs = tid ? `&thread_id=${tid}` : "";
      const projectQs = `&project_id=${encodeURIComponent(activeProjectId)}`;
      const resp = await fetchWithTimeout(`${apiBase}/memory?offset=0&limit=200${threadQs}${projectQs}`);
      if (!resp.ok) {
        let detail = `Memory request failed (${resp.status})`;
        try {
          const body = await resp.json();
          const d = body?.detail;
          if (typeof d === "string") detail = d;
          else if (d && typeof d === "object") detail = String(d.message || d.error || detail);
        } catch {
          /* keep status detail */
        }
        throw new Error(detail);
      }
      const data = (await resp.json()) as MemoryListResponse;
      // Empty list is a valid success state — never treat as failure.
      setMemoryItems(Array.isArray(data.items) ? data.items : []);
      setMemoryCount(typeof data.count === "number" ? data.count : 0);
      setMemoryError(null);
    } catch (e) {
      setMemoryError(String(e));
    } finally {
      setMemoryLoading(false);
    }
  };

  const refreshMemoryDoctor = async () => {
    setMemoryDoctorLoading(true);
    try {
      const tid = encodeURIComponent(String(activeThreadId || "").trim());
      const qs = tid
        ? `?thread_id=${tid}&project_id=${encodeURIComponent(activeProjectId)}&max_scan=300`
        : `?project_id=${encodeURIComponent(activeProjectId)}&max_scan=300`;
      const resp = await fetchWithTimeout(`${apiBase}/memory/doctor${qs}`, undefined, 7000);
      if (!resp.ok) throw new Error(`Memory doctor failed (${resp.status})`);
      const data = (await resp.json()) as MemoryDoctorReport;
      setMemoryDoctor(data);
    } catch (e) {
      console.error("Memory doctor error:", e);
    } finally {
      setMemoryDoctorLoading(false);
    }
  };

  const deleteMemoryItem = async (id: string) => {
    try {
      const resp = await fetchWithTimeout(`${apiBase}/memory/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: [id], thread_id: activeThreadId, project_id: activeProjectId }),
      });
      if (!resp.ok) throw new Error(`Delete failed (${resp.status})`);
      await refreshMemory();
    } catch (e) {
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${String(e)}`, at: Date.now() });
    }
  };

  const togglePinMemoryItem = async (item: MemoryItem) => {
    try {
      const resp = await fetchWithTimeout(`${apiBase}/memory/update`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: item.id, pinned: !Boolean(item.pinned), thread_id: activeThreadId, project_id: activeProjectId }),
      });
      if (!resp.ok) throw new Error(`Update failed (${resp.status})`);
      setMemoryItems((prev: MemoryItem[]) =>
        prev.map((m: MemoryItem) => (m.id === item.id ? { ...m, pinned: !Boolean(item.pinned) } : m))
      );
    } catch (e) {
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${String(e)}`, at: Date.now() });
    }
  };

  const clearAllMemory = async () => {
    if (!window.confirm("Clear all saved memory?")) return;
    try {
      const tid = encodeURIComponent(String(activeThreadId || "").trim());
      const threadQs = tid
        ? `?thread_id=${tid}&project_id=${encodeURIComponent(activeProjectId)}`
        : `?project_id=${encodeURIComponent(activeProjectId)}`;
      const resp = await fetchWithTimeout(`${apiBase}/memory/clear${threadQs}`, { method: "POST" });
      if (!resp.ok) throw new Error(`Clear failed (${resp.status})`);
      await refreshMemory();
    } catch (e) {
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${String(e)}`, at: Date.now() });
    }
  };

  const refreshDocuments = async () => {
    setDocLoading(true);
    setDocError(null);
    try {
      const docScope = new URLSearchParams({ session_id: activeThreadId });
      if (activeProjectId) docScope.set("project_id", activeProjectId);
      const resp = await fetchWithTimeout(`${apiBase}/documents?${docScope.toString()}`);
      if (!resp.ok) throw new Error(`Documents request failed (${resp.status})`);
      const data = (await resp.json()) as DocumentListResponse;
      setDocEnabled(Boolean(data.enabled));
      setDocItems(Array.isArray(data.items) ? data.items : []);
      setDocCount(typeof data.count === "number" ? data.count : 0);
    } catch (e) {
      setDocEnabled(false);
      setDocItems([]);
      setDocCount(0);
      setDocError(String(e));
    } finally {
      setDocLoading(false);
    }
  };

  const uploadDocument = async () => {
    if (!docFile) return;
    setDocUploading(true);
    setDocError(null);
    try {
      const form = new FormData();
      form.append("file", docFile);
      const docScope = new URLSearchParams({ session_id: activeThreadId });
      if (activeProjectId) docScope.set("project_id", activeProjectId);
      const resp = await fetchWithTimeout(`${apiBase}/documents/upload?${docScope.toString()}`, { method: "POST", body: form }, 12000);
      if (!resp.ok) throw new Error(await resp.text());
      setDocFile(null);
      if (docInputRef.current) docInputRef.current.value = "";
      await refreshDocuments();
    } catch (e) {
      setDocError(String(e));
    } finally {
      setDocUploading(false);
    }
  };

  const deleteDocument = async (id: string) => {
    try {
      const resp = await fetchWithTimeout(`${apiBase}/documents/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: [id], session_id: activeThreadId, project_id: activeProjectId || "" }),
      });
      if (!resp.ok) throw new Error(`Delete failed (${resp.status})`);
      await refreshDocuments();
    } catch (e) {
      setDocError(String(e));
    }
  };

  const clearAllDocuments = async () => {
    if (!window.confirm("Clear all uploaded documents?")) return;
    try {
      const scope = new URLSearchParams({ session_id: activeThreadId });
      if (activeProjectId) scope.set("project_id", activeProjectId);
      const resp = await fetchWithTimeout(`${apiBase}/documents/clear?${scope.toString()}`, { method: "POST" });
      if (!resp.ok) throw new Error(`Clear failed (${resp.status})`);
      await refreshDocuments();
    } catch (e) {
      setDocError(String(e));
    }
  };

  const refreshProviderModels = async (provider: string) => {
    try {
      setModelsLoading(true);
      const resp = await fetchWithTimeout(`${apiBase}/provider/models?provider=${encodeURIComponent(provider)}`);
      if (!resp.ok) return;
      const data = (await resp.json()) as ProviderModelsResponse;
      setProviderModels(Array.isArray(data.models) ? data.models : []);
    } catch {
      setProviderModels([]);
    } finally {
      setModelsLoading(false);
    }
  };

  const applyProviderSwitch = async (draft?: { provider: string; model: string; base_url: string }) => {
    if (lmStudioOnly) return;
    const next = draft || providerDraft;
    if (!next.provider) return;
    setSwitchingProvider(true);
    setProviderError(null);
    cancelSessionTurn(String(activeThreadIdRef.current || ""));
    try {
      const body: any = {
        provider: next.provider,
        session_id: String(activeThreadIdRef.current || "default"),
        expected_revision: Number(providerInfo?.binding_revision || 1),
      };
      if (next.provider === "openai") body.openai_model = next.model || undefined;
      else if (next.provider === "gemini") body.gemini_model = next.model || undefined;
      else body.model = next.model || undefined;
      if (next.base_url) body.base_url = next.base_url;

      const resp = await fetchWithTimeout(`${apiBase}/provider/switch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const t = await resp.text();
        throw new Error(t || `${resp.status} ${resp.statusText}`);
      }
      lastAppliedProviderRef.current = { provider: next.provider, model: next.model || "" };
      await refreshProviderInfo();
      if (listableProviders.includes(next.provider)) {
        await refreshProviderModels(next.provider);
      }
    } catch (e) {
      setProviderError(e instanceof Error ? e.message : String(e));
    } finally {
      setSwitchingProvider(false);
    }
  };

  const timeline = useMemo<TimelineItem[]>(() => {
    // Chat is a conversation projection. Durable operational evidence stays in
    // Studio/Viewer. While streaming, thinking steps are ephemeral live chrome.
    // After the turn, only durable actionable errors remain with message bubbles.
    const merged: TimelineItem[] = [
      ...messages.map(
        (m): TimelineItem => ({
          kind: "message",
          id: m.id,
          at: m.at,
          msg: m,
        })
      ),
      ...activities
        .filter((item) => shouldIncludeChatActivity(String(item.kind || ""), streaming))
        .map(
          (a): TimelineItem => ({
            kind: "activity",
            id: a.id,
            at: a.at,
            item: a,
          })
        ),
    ];
    // Chronological user/assistant history with live activity + persistent errors.
    const kindRank = (k: TimelineItem["kind"]) =>
      k === "message" ? 0 : k === "activity" ? 1 : 2;
    merged.sort((a, b) => {
      const dt = a.at - b.at;
      if (dt !== 0) return dt;
      return kindRank(a.kind) - kindRank(b.kind);
    });
    return merged;
  }, [messages, activities, streaming]);

  const lastMsgLen = messages.length ? (messages[messages.length - 1]?.text || "").length : 0;
  const activityLen = activities.length;
  const taskPlanLen = taskPlans.reduce((sum, entry) => sum + entry.plan.tasks.length, 0);

  /**
   * Pin chat fully to the latest content. Instant scroll only — smooth scrolling
   * gets interrupted mid-animation when content keeps growing and leaves the view at ~90–98%.
   */
  const scrollChatToBottom = useCallback((force: boolean = false) => {
    if (!force && !stickToBottomRef.current) return;
    const el = chatScrollRef.current;
    if (!el) return;

    const pin = () => {
      programmaticScrollRef.current = true;
      // Direct assignment is more reliable than scrollTo for max bottom.
      el.scrollTop = el.scrollHeight;
      // Bottom sentinel (if mounted) — catches residual subpixel / padding cases.
      try {
        chatBottomRef.current?.scrollIntoView({ block: "end", behavior: "auto" });
      } catch {
        // ignore
      }
      el.scrollTop = el.scrollHeight;
    };

    if (pinBottomRafRef.current) cancelAnimationFrame(pinBottomRafRef.current);
    // Two frames: after React paint, then after layout (markdown / framer-motion / embeds).
    pinBottomRafRef.current = requestAnimationFrame(() => {
      pin();
      pinBottomRafRef.current = requestAnimationFrame(() => {
        pin();
        // Clear flag after the browser has emitted the scroll event for our pin.
        requestAnimationFrame(() => {
          programmaticScrollRef.current = false;
        });
      });
    });
  }, []);

  const onChatScroll = () => {
    if (programmaticScrollRef.current) return;
    const el = chatScrollRef.current;
    if (!el) return;
    // User is still "at bottom" if within a small slack of the true end.
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distFromBottom <= 48;
    const sessionId = String(activeThreadIdRef.current || "");
    if (sessionId) sessionScrollRef.current.set(sessionId, { top: el.scrollTop, atBottom: stickToBottomRef.current });
  };

  const refreshObsidianPlan = async () => {
    setObsidianLoading(true);
    setObsidianStatus("");
    try {
      const query = new URLSearchParams({ session_id: activeThreadId, project_id: activeProjectId });
      const response = await fetchWithTimeout(`${apiBase}/memory/obsidian/plan?${query}`, undefined, 7000);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(payload?.detail || `Obsidian sync unavailable (${response.status})`));
      setObsidianPlan(payload);
      setObsidianStatus(`${(payload.actions || []).length} action(s) require review.`);
    } catch (error) {
      setObsidianPlan(null);
      setObsidianStatus(String(error));
    } finally {
      setObsidianLoading(false);
    }
  };

  const applyObsidianPlan = async (direction: "export" | "import") => {
    const allowedKinds = direction === "export" ? ["export_new", "export_update"] : ["import_new", "import_update"];
    const actionIds = (obsidianPlan?.actions || [])
      .filter((action: any) => allowedKinds.includes(String(action.kind)))
      .map((action: any) => String(action.id));
    if (!actionIds.length) return;
    setObsidianLoading(true);
    try {
      const response = await fetch(`${apiBase}/memory/obsidian/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: activeThreadId,
          project_id: activeProjectId,
          direction,
          action_ids: actionIds,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(payload?.detail || `Obsidian ${direction} failed (${response.status})`));
      setObsidianStatus(`${direction === "export" ? "Export" : "Import"} applied. Refreshing the governed plan…`);
      await Promise.all([refreshMemory(), refreshObsidianPlan()]);
    } catch (error) {
      setObsidianStatus(String(error));
    } finally {
      setObsidianLoading(false);
    }
  };

  useLayoutEffect(() => {
    const el = chatScrollRef.current;
    if (!el || !activeThreadId) return;
    const saved = sessionScrollRef.current.get(activeThreadId);
    programmaticScrollRef.current = true;
    requestAnimationFrame(() => {
      if (saved?.atBottom || !saved) el.scrollTop = el.scrollHeight;
      else el.scrollTop = Math.min(saved.top, Math.max(0, el.scrollHeight - el.clientHeight));
      stickToBottomRef.current = saved?.atBottom ?? true;
      requestAnimationFrame(() => { programmaticScrollRef.current = false; });
    });
  }, [activeThreadId]);

  useEffect(() => {
    // initial mount / tab switch — always jump to latest
    if (leftTab === "chat") {
      stickToBottomRef.current = true;
      scrollChatToBottom(true);
    }
  }, [leftTab, scrollChatToBottom]);

  // Re-pin whenever timeline / draft / tools grow, unless user scrolled up.
  useLayoutEffect(() => {
    if (leftTab !== "chat") return;
    scrollChatToBottom(false);
  }, [
    leftTab,
    timeline.length,
    lastMsgLen,
    activityLen,
    taskPlanLen,
    streaming,
    speaking,
    liveReplyDraft,
    pendingApproval?.has_pending,
    scrollChatToBottom,
  ]);

  // While streaming/speaking, content height keeps changing after effects run — keep pinned.
  useEffect(() => {
    if (leftTab !== "chat") return;
    if (!streaming && !speaking) return;
    const id = window.setInterval(() => {
      if (stickToBottomRef.current) scrollChatToBottom(false);
    }, 80);
    return () => window.clearInterval(id);
  }, [leftTab, streaming, speaking, scrollChatToBottom]);

  // When message/tool nodes resize (markdown, embeds, ops card), stay at true bottom.
  useEffect(() => {
    const el = chatScrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;

    const pinIfStuck = () => {
      if (stickToBottomRef.current) scrollChatToBottom(false);
    };

    const ro = new ResizeObserver(() => pinIfStuck());
    const observeChildren = () => {
      ro.disconnect();
      for (const child of Array.from(el.children)) ro.observe(child);
    };
    observeChildren();

    const mo = new MutationObserver(() => {
      observeChildren();
      pinIfStuck();
    });
    mo.observe(el, { childList: true, subtree: true, characterData: true });

    return () => {
      ro.disconnect();
      mo.disconnect();
    };
  }, [scrollChatToBottom, leftTab]);

  const speakLocalText = async (
    text: string,
    metadata: Pick<SpeechScope, "clientTurnId" | "requestId" | "executionId" | "taskRunId" | "completeTurn">,
  ) => {
    const sessionId = String(activeThreadIdRef.current || "").trim();
    const cleaned = sanitizeForTTS(text);
    if (!speechEnabled || !sessionId || !cleaned) return false;
    setVoiceNotice("");
    try {
      await localVoicePlayback.speak(
        cleaned,
        {
          apiBase,
          sessionId,
          projectId: String(activeProjectIdRef.current || ""),
          ...metadata,
        },
        {
          onPhase: (phase, detail) => {
            setVoicePhase(phase);
            setVoiceNotice(detail || "");
            useAppStore.getState().setSpeaking(phase === "speaking");
          },
          onLevel: (level) => {
            if (level > 0) useAppStore.getState().bumpSpeechBeat();
          },
        },
      );
      return true;
    } catch (error) {
      if ((error as any)?.name === "AbortError") return false;
      setVoicePhase("error");
      setVoiceNotice(error instanceof Error ? error.message : "Local speech playback is unavailable.");
      useAppStore.getState().setSpeaking(false);
      return false;
    }
  };

  const sendText = async (overrideText?: string, voiceTranscript?: VoiceTranscript) => {
    const raw = overrideText ?? input;
    if (!raw.trim()) return;
    // Session creation has one explicit owner: the + controls in the sidebar.
    // Composer submission, navigation, hydration, and assistant replies never
    // invent a Session.
    const streamThreadId = String(activeThreadIdRef.current || activeThreadId || "").trim();
    if (!streamThreadId) return;
    const runRequestId = crypto.randomUUID();
    const streamProjectId = String(activeProjectIdRef.current || activeProjectId || "").trim();
    cancelSessionTurn(streamThreadId);
    const streamController = new AbortController();
    streamControllersRef.current.set(streamThreadId, streamController);
    activeRequestIdsRef.current.set(streamThreadId, runRequestId);
    setSessionInFlight(streamThreadId, true);

    stickToBottomRef.current = true; // force sticky to bottom when sending a message
    if (!overrideText) setInput("");

    const clampContext = (t: string, n: number) => {
      const s = (t || "").replace(/\s+/g, " ").trim();
      if (s.length <= n) return s;
      return s.slice(0, n).trimEnd() + "…";
    };

    const shouldAttachMonitor = (q: string) => {
      const low = (q || "").toLowerCase();
      if (!monitoring) return false;
      if (!monitorText || !monitorText.trim()) return false;
      if (low.includes("on my screen") || low.includes("on my desktop") || low.includes("what am i looking") || low.includes("what do you see")) return true;
      if (low.includes("watching") || low.includes("seeing") || low.includes("look at") || low.includes("this") || low.includes("that") || low.includes("here")) return true;
      return false;
    };

    const desktopContext = !voiceTranscript && shouldAttachMonitor(raw) ? clampContext(monitorText, 1200) : "";
    const requestText = desktopContext ? `${raw}\n\nLive desktop context:\n${desktopContext}` : raw;

    const ctxWindow = Number(providerInfo?.context_window || 0) || 32768;
    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: "user",
      text: raw,
      at: Date.now(),
      usage: buildMessageUsage(raw, messages, ctxWindow, {
        provider: providerInfo?.provider,
        model: providerInfo?.model,
      }),
    };
    projectionRevisionRef.current.set(
      streamThreadId,
      (projectionRevisionRef.current.get(streamThreadId) || 0) + 1,
    );
    addMessage(userMsg);
    setInput("");
    setUserIsTyping(false);
    if (userTypingTimerRef.current) clearTimeout(userTypingTimerRef.current);
    setDocSources([]);
    // Turn-local research only — do not carry prior Session source cards into this answer.
    // Global research history remains available in Visualizer; Chat embeds use turnResearchRuns.
    // Fresh turn = fresh checklist only (no stacked plans from prior messages)
    setTaskPlans([]);
    liveReplyDraftRef.current = "";
    setLiveReplyDraft("");
    dispatchActivity({ type: "stream_start" });
    setStreaming(true);
    // Drop prior-turn tool metadata so done-labels never inherit stale queries
    // (e.g. Python search label leaking into a later GTA+FIFA turn).
    toolInfoRef.current = {};
    const bootstrapStepId = `${runRequestId}:working`;
    /** Backend Turn id once create_execution emits turn_bound / final. */
    let durableTurnId = "";
      let finalHandled = false;
      let streamWasHidden = false;
    /** Mid-turn spoken beats already committed (so final doesn't re-add them). */
    const partialReplies: string[] = [];
    /** True once the first spoken mid-turn beat is sealed — tools must sort after it. */
    let sawPartialBeat = false;
    /** Floor timestamp for tool/search activity after the first partial. */
    let toolsAfterPartialAt = 0;
    /** Research runs + queries this turn — feed chat embeds under the final bubble. */
    const turnResearchRuns: ResearchRun[] = [];
    const turnSearchQueries: string[] = [];
    // Close any prior-Turn running chrome so B's tools never paint as A's open work.
    setActivities((prev) => {
      const closed = prev.map((a) => {
        if (a.kind === "tool" && a.status === "running") {
          return {
            ...a,
            status: "error" as const,
            output: a.output || "Superseded by a new Turn",
          };
        }
        if (a.kind === "thinking" && a.steps?.some((s) => s.status === "running")) {
          return {
            ...a,
            steps: a.steps.map((s) =>
              s.status === "running" ? { ...s, status: "done" as const } : s
            ),
          };
        }
        return a;
      });
      // Drop idle "thinking…" shells from prior turns (keep completed tool rows).
      const pruned = closed.filter(
        (a) =>
          !(
            a.kind === "thinking" &&
            (!a.steps?.length || a.steps.every((s) => s.content === "thinking…" || s.status === "done"))
          )
      );
      return pruned;
    });
    try {
      const resp = await fetch(`${apiBase}/query/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: streamController.signal,
        body: JSON.stringify({
          message: requestText,
          include_memory: true,
          thread_id: streamThreadId,
          client_request_id: runRequestId,
          thinking_enabled: thinkingEnabled,
          reasoning_effort: reasoningEffort,
          transport: voiceTranscript ? "voice" : "chat",
          voice_turn_id: voiceTranscript?.voiceTurnId,
        }),
      });
      if (!resp.ok) {
        const errText = await resp.text();
        throw new Error(errText || `HTTP ${resp.status}`);
      }

      if (!resp.body) {
        throw new Error("No response body");
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      // Monotonic stream seq (backend) — ignore reordered/stale reconnect frames.
      let maxStreamSeq = 0;
      // Client turn key groups one thinking card. Backend request_id on events is preserved
      // for durable ToolRun correlation in stream payloads but must not spawn extra cards.
      const eventRequestId = (_evt?: { request_id?: string }) => runRequestId;
      const markThinkingStep = (
        evt: { request_id?: string },
        stepId: string,
        patch: Partial<ThinkingStep>,
        opts?: { toolName?: string; stepType?: ThinkingStep["type"] },
      ) => {
        const reqId = eventRequestId(evt);
        setActivities((prev) =>
          prev.map((p) => {
            if (p.kind !== "thinking" || p.request_id !== reqId || !p.steps?.length) return p;
            let matched = false;
            const steps = p.steps.map((s) => {
              if (s.id === stepId) {
                matched = true;
                return { ...s, ...patch };
              }
              return s;
            });
            // No name/type FIFO fallback — only exact ToolRun id may complete a row.
            if (!matched) return p;
            // Keep card under first partial beat if any.
            const nextAt = sawPartialBeat ? Math.max(p.at, toolsAfterPartialAt) : p.at;
            return { ...p, at: nextAt, steps };
          })
        );
      };

      const appendThinkingStep = (evt: { request_id?: string }, step: ThinkingStep) => {
        const reqId = eventRequestId(evt);
        // Ignore raw thought dumps — they duplicate the bootstrap spinner and clutter chat.
        if (step.type === "thought") {
          const short = "Working";
          setActivities((prev) =>
            prev.map((p) => {
              if (p.kind !== "thinking" || p.request_id !== reqId) return p;
              const steps = (p.steps || []).map((s) =>
                s.id === bootstrapStepId && s.status === "running"
                  ? { ...s, content: short.endsWith("…") ? short : `${short}…` }
                  : s
              );
              return { ...p, content: short, steps };
            })
          );
          return;
        }
        setActivities((prev) => {
          const existingIdx = prev.findIndex((p) => p.kind === "thinking" && p.request_id === reqId);
          if (existingIdx !== -1) {
            const updated = [...prev];
            const existing = updated[existingIdx] as Extract<ActivityItem, { kind: "thinking" }>;
            // Real work arrives → drop bootstrap so we don't stack "thinking…" + tool rows.
            let prevSteps = (existing.steps || []).filter((s) =>
              step.id === bootstrapStepId ? true : s.id !== bootstrapStepId
            );
            // Also drop post-partial placeholder once real tool/search steps land.
            prevSteps = prevSteps.filter((s) => s.id !== `${reqId}:post-partial-working`);
            // Upsert by exact ToolRun id only — never complete a different row by tool name/type.
            const byId = prevSteps.findIndex((s) => s.id === step.id);
            let nextSteps: ThinkingStep[];
            if (byId >= 0) {
              const existing = prevSteps[byId];
              // Terminal steps ignore trailing events (idempotent).
              if (
                (existing.status === "done" || existing.status === "failed") &&
                (step.status === "done" || step.status === "failed" || step.status === "running")
              ) {
                nextSteps = prevSteps;
              } else {
                nextSteps = prevSteps.map((s, i) => (i === byId ? { ...s, ...step } : s));
              }
            } else if (step.status === "done" || step.status === "failed") {
              // No open row with this id — append terminal (do not steal another running row).
              nextSteps = [...prevSteps, step];
            } else {
              // tool_start: drop only provisional request-scoped placeholders of same type
              // (ids like `${reqId}:search:...`), never another real ToolRun UUID.
              prevSteps = prevSteps.filter(
                (s) =>
                  !(
                    s.status === "running" &&
                    s.type === step.type &&
                    s.id !== step.id &&
                    String(s.id).startsWith(`${reqId}:`)
                  )
              );
              nextSteps = [...prevSteps, step];
            }
            // NEVER pull the card earlier (Math.min was pinning Search done above the first beat).
            // After a partial beat, stay strictly after that spoken message.
            const stepAt = step.at || Date.now();
            const floor = sawPartialBeat ? Math.max(toolsAfterPartialAt, existing.at) : existing.at;
            const nextAt = Math.max(floor, stepAt, sawPartialBeat ? toolsAfterPartialAt : 0);
            updated[existingIdx] = {
              ...existing,
              at: nextAt || stepAt,
              steps: nextSteps,
            };
            return updated;
          }
          return [
            ...prev,
            {
              kind: "thinking",
              id: crypto.randomUUID(),
              content: "",
              at: step.at || Date.now(),
              steps: [step],
              request_id: reqId,
            },
          ];
        });
      };

      const completeAllRunningSteps = (status: "done" | "failed" = "done") => {
        // Only this Turn's thinking card — never force-complete prior turns.
        // Provisional chrome ids (`${requestId}:…`) that never got a real tool_end must
        // be dropped, not force-completed as a second "Calculate done" / "Search done".
        setActivities((prev) =>
          prev.map((p) => {
            if (p.kind !== "thinking" || p.request_id !== runRequestId || !p.steps?.length) return p;
            const nextSteps = p.steps
              .map((s) => {
                if (s.status !== "running") return s;
                const id = String(s.id || "");
                const provisional = id.startsWith(`${runRequestId}:`);
                if (provisional && s.type !== "thought") {
                  // Drop orphan tool/search/read chrome — ToolRun UUID rows own those.
                  return null;
                }
                return {
                  ...s,
                  status,
                  content:
                    status === "failed" && !/fail/i.test(s.content)
                      ? `${s.content} — failed`
                      : s.content,
                };
              })
              .filter((s): s is NonNullable<typeof s> => s != null && Boolean(String(s.content || "").trim() || s.type === "thought"));
            return { ...p, steps: nextSteps };
          })
        );
      };
      const upsertTaskPlan = (evt: AgentStreamEvent) => {
        const reqId = eventRequestId(evt);
        // Always prefer "now" for plan placement so the checklist tracks the current turn
        // (backend `at` can lag or collide with older messages and pin the plan high up).
        const eventAt = Date.now();
        setTaskPlans((prev) => {
          if (evt.type !== "task_plan") return prev;
          const id = crypto.randomUUID();
          return [
            ...prev,
            {
              id,
              at: eventAt,
              request_id: reqId || runRequestId,
              plan: taskPlanReducer(createEmptyTaskPlan(), evt),
            },
          ];
        });
      };
      const upsertTool = (evt: AgentStreamEvent) => {
        if (evt.type === "tool_start") {
          // Scope tool metadata to this stream turn (avoid stale labels from prior turns)
          toolInfoRef.current[evt.id] = { name: evt.name, input: evt.input, requestId: runRequestId };
          const toolNameStart = String(evt.name || "").toLowerCase();
          if (toolNameStart === "terminal_run") {
            setVisualizerPin("coding");
            setAgentMode("coding");
          }
          // Only hide pure injects that fire almost every turn
          if (!SILENT_CHAT_TOOLS.has(toolNameStart)) {
            const stepAt = sawPartialBeat
              ? Math.max(Date.now(), toolsAfterPartialAt)
              : normalizeTimestampMs(evt.at || Date.now());
            appendThinkingStep(evt, {
              id: evt.id,
              type: toolActivityStepType(evt.name || ""),
              content: formatToolActivity(evt.name || "tool", "start", { input: evt.input }),
              status: "running",
              at: stepAt,
            });
          }
          return;
        }

        if (evt.type === "tool_end") {
          const info = toolInfoRef.current[evt.id];
          const toolFailed = evt.outcome?.success === false;
          // Ignore tool_end that belongs to another turn's metadata
          if (info && (info as { requestId?: string }).requestId && (info as { requestId?: string }).requestId !== runRequestId) {
            return;
          }
          const toolName = info?.name || evt.name || "tool";
          const toolNameLow = String(toolName || "").toLowerCase();
          if (SILENT_CHAT_TOOLS.has(toolNameLow)) {
            return;
          }
          // Outer fan-out / superseded shells — no second "Search done" timeline line.
          const outRaw = String(evt.output || "");
          if (
            toolNameLow === "web_search" &&
            (/\(expanded to /i.test(outRaw) || /\(superseded by canonical/i.test(outRaw))
          ) {
            markThinkingStep(
              evt,
              evt.id,
              { status: "done", content: "" },
              { toolName, stepType: toolActivityStepType(toolName) },
            );
            // Drop empty wrapper steps from the thinking card
            setActivities((prev) =>
              prev.map((p) => {
                if (p.kind !== "thinking" || p.request_id !== runRequestId || !p.steps) return p;
                return {
                  ...p,
                  steps: p.steps.filter((s) => s.id !== evt.id || Boolean(String(s.content || "").trim())),
                };
              })
            );
            return;
          }
          // Research panel still fed by web_search
          if (!toolFailed && toolNameLow === "web_search") {
            const normalized =
              normalizeResearchRun(evt.research) ||
              buildResearchRunFromToolEvent(
                evt.id,
                toolName,
                info?.input || "",
                evt.output || "",
                evt.at || Date.now()
              );
            if (normalized) {
              prependResearchRun(normalized);
              turnResearchRuns.push(normalized);
              if (normalized.query) turnSearchQueries.push(normalized.query);
            } else if (info?.input) {
              turnSearchQueries.push(String(info.input).replace(/\s+/g, " ").trim().slice(0, 120));
            }
          }
          // The durable ToolRun and specialist projections own code activity.
          // The stream only nudges the Visualizer toward its Code panel.
          const codingTools = new Set([
            "file_write",
            "file_read",
            "file_delete",
            "file_move",
            "file_copy",
            "file_mkdir",
            "artifact_write",
            "terminal_run",
            "notepad_write",
            "checkpoint_undo",
          ]);
          if (codingTools.has(toolName)) {
            setVisualizerPin("coding");
            setAgentMode("coding");
          }
          // Unified done label: built-in, sports, MCP (mcp__server__tool), skills, …
          markThinkingStep(
            evt,
            evt.id,
            {
              status: toolFailed ? "failed" : "done",
              content: formatToolActivity(toolName, toolFailed ? "failed" : "done", {
                input: info?.input || "",
                output: evt.output || "",
                error: evt.outcome?.error_message || "",
              }),
            },
            { toolName, stepType: toolActivityStepType(toolName) },
          );
          return;
        }

        if (evt.type === "tool_error") {
          const info = toolInfoRef.current[evt.id];
          const toolName = info?.name || "tool";
          if (!SILENT_CHAT_TOOLS.has(String(toolName || "").toLowerCase())) {
            markThinkingStep(
              evt,
              evt.id,
              {
                status: "failed",
                content: formatToolActivity(toolName, "failed", {
                  input: info?.input || "",
                  error: evt.error,
                }),
              },
              { toolName, stepType: toolActivityStepType(toolName) },
            );
          }
          setEchoReaction("error");
        }
      };

      while (true) {
        if (streamController.signal.aborted) break;
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx = buffer.indexOf("\n");
        while (idx !== -1) {
          const line = buffer.slice(0, idx).trim();
          buffer = buffer.slice(idx + 1);
          idx = buffer.indexOf("\n");
          if (!line) continue;

          let evt: AgentStreamEvent & { seq?: number };
          try {
            evt = JSON.parse(line) as AgentStreamEvent & { seq?: number };
          } catch (e) {
            continue;
          }
          if (streamController.signal.aborted) continue;
          if (!isStreamThreadCurrent(streamThreadId, activeThreadIdRef.current)) {
            streamWasHidden = true;
            if (evt.type === "final") finalHandled = true;
            continue;
          }
          const evtSeq = Number((evt as { seq?: number }).seq || 0);
          if (evtSeq > 0) {
            if (evtSeq <= maxStreamSeq) {
              // Stale or duplicated frame after reconnect — do not apply.
              continue;
            }
            maxStreamSeq = evtSeq;
          }
          for (const action of activityActionsFromStreamEvent(evt as unknown as Record<string, unknown>)) {
            dispatchActivity(action);
          }

          if (evt.type === "turn_bound") {
            const execId = String(evt.execution_id || evt.turn_id || "").trim();
            if (execId) {
              durableTurnId = execId;
              activeExecutionIdsRef.current.set(streamThreadId, execId);
              setLatestExecutionId(execId);
            }
            dispatchActivity({ type: "turn_bound", objective: raw });
            const reasoningControl = evt.reasoning_control || {};
            if (
              thinkingEnabled &&
              reasoningControl.native_support === false &&
              reasoningEffort !== "medium"
            ) {
              dispatchActivity({
                type: "step_update",
                nextAction: "This provider does not expose native effort control on the active endpoint.",
              });
            }
          } else if (evt.type === "task_bound") {
            activeTaskRunIdsRef.current.set(streamThreadId, String(evt.task_run_id || ""));
          } else if (evt.type === "reasoning_summary") {
            const summary = String(evt.content || "").trim();
            if (thinkingEnabled && summary) {
              appendThinkingStep(evt, {
                id: `${runRequestId}:reasoning-summary:${evt.iteration || 0}`,
                type: "thought",
                content: summary,
                status: "done",
                at: normalizeTimestampMs(evt.at || Date.now()),
              });
            }
          } else if (evt.type === "recovery" || evt.type === "lifecycle" || evt.type === "iteration_boundary" || evt.type === "token_usage") {
            // The shared activity decoder above owns these semantic projections.
          } else if (evt.type === "task_plan") {
            upsertTaskPlan(evt);
          } else if (evt.type === "tool_start" || evt.type === "tool_end" || evt.type === "tool_error") {
            upsertTool(evt);
          } else if (evt.type === "thinking_step") {
            const stepType = (evt.step_type || "tool") as ThinkingStep["type"];
            const st = String(evt.status || "running").toLowerCase();
            const status: ThinkingStep["status"] =
              st === "failed" || st === "error" ? "failed" : st === "done" || st === "complete" ? "done" : "running";
            const content = String(evt.content || "").trim();
            // thinking_step is provisional chrome only. Never complete real ToolRun UUIDs
            // by type/name FIFO — tool_start/tool_end own ToolRun identity.
            if (stepType === "thought") {
              appendThinkingStep(evt, {
                id: `${eventRequestId(evt)}:thought`,
                type: "thought",
                content,
                status: "running",
                at: normalizeTimestampMs(evt.at || Date.now()),
              });
            } else {
              setActivities((prev) => {
                const idx = prev.findIndex((p) => p.kind === "thinking" && p.request_id === runRequestId);
                if (idx >= 0) {
                  const card = prev[idx] as Extract<ActivityItem, { kind: "thinking" }>;
                  const steps = [...(card.steps || [])];
                  // Only update provisional request-scoped placeholders (never ToolRun UUIDs).
                  const provisionalIdx = steps.findIndex(
                    (s) =>
                      s.status === "running" &&
                      s.type === stepType &&
                      String(s.id).startsWith(`${runRequestId}:`)
                  );
                  if (provisionalIdx >= 0 && status === "running") {
                    steps[provisionalIdx] = {
                      ...steps[provisionalIdx],
                      content: content || steps[provisionalIdx].content,
                      status: "running",
                    };
                    const next = [...prev];
                    next[idx] = { ...card, steps };
                    return next;
                  }
                  // Terminal thinking_step without ToolRun id: ignore (wait for tool_end).
                  if (status === "done" || status === "failed") {
                    return prev;
                  }
                  // No open tool row yet — provisional running row only
                  if (status === "running") {
                    const stableId = `${runRequestId}:${stepType}:${content.slice(0, 48)}`;
                    if (!steps.some((s) => s.id === stableId)) {
                      const floor = sawPartialBeat ? Math.max(toolsAfterPartialAt, card.at) : card.at;
                      const next = [...prev];
                      next[idx] = {
                        ...card,
                        at: Math.max(floor, normalizeTimestampMs(evt.at || Date.now())),
                        steps: [
                          ...steps.filter((s) => s.id !== bootstrapStepId && s.id !== `${runRequestId}:post-partial-working`),
                          {
                            id: stableId,
                            type: stepType,
                            content,
                            status: "running",
                            at: normalizeTimestampMs(evt.at || Date.now()),
                          },
                        ],
                      };
                      return next;
                    }
                  }
                } else if (status === "running") {
                  return [
                    ...prev,
                    {
                      kind: "thinking" as const,
                      id: crypto.randomUUID(),
                      content: "",
                      at: normalizeTimestampMs(evt.at || Date.now()),
                      request_id: runRequestId,
                      steps: [
                        {
                          id: `${runRequestId}:${stepType}:${content.slice(0, 48)}`,
                          type: stepType,
                          content,
                          status: "running" as const,
                          at: normalizeTimestampMs(evt.at || Date.now()),
                        },
                      ],
                    },
                  ];
                }
                return prev;
              });
            }
          } else if (evt.type === "agent_token") {
            const tok = String(evt.data || "");
            if (tok) {
              const prev = liveReplyDraftRef.current;
              const next = prev + tok;
              liveReplyDraftRef.current = next;
              // First token — remove bootstrap spinner (reply is the progress now).
              if (!prev) {
                setActivities((acts) =>
                  acts.map((p) => {
                    if (p.kind !== "thinking" || p.request_id !== eventRequestId(evt)) return p;
                    return {
                      ...p,
                      steps: (p.steps || []).filter((s) => s.id !== bootstrapStepId),
                    };
                  })
                );
              }
              setLiveReplyDraft(next);
            }
          } else if (evt.type === "partial_reply") {
            // Keep partial prose transient. The completed Turn is committed as
            // one assistant message when the final event arrives.
            const text = String(evt.response || liveReplyDraftRef.current || "").trim();
            if (!text) continue;
            if (partialReplies.some((p) => p.trim() === text)) continue;
            partialReplies.push(text);
            const beatAt = Date.now();
            sawPartialBeat = true;
            toolsAfterPartialAt = beatAt + 10;
            liveReplyDraftRef.current = text;
            setLiveReplyDraft(text);
            // Speak this beat now — tools may follow, then a second reply.
            if (evt.speak !== false && (voiceReadAloud || voiceConversationMode)) {
              void speakLocalText(text, {
                clientTurnId: voiceTranscript?.clientTurnId || runRequestId,
                requestId: runRequestId,
                executionId: durableTurnId,
                taskRunId: activeTaskRunIdsRef.current.get(streamThreadId) || "",
                completeTurn: false,
              });
            }
            const reqId = eventRequestId(evt);
            setActivities((prev) =>
              prev.map((p) => {
                if (p.kind !== "thinking" || p.request_id !== reqId) return p;
                const kept = (p.steps || []).filter(
                  (s) =>
                    s.id !== bootstrapStepId &&
                    s.id !== `${reqId}:post-partial-working` &&
                    (s.status === "done" || s.status === "failed" || s.status === "running") &&
                    !/^(thinking|thinking…)$/i.test(String(s.content || "").trim())
                );
                const hasToolWork = kept.some(
                  (s) => s.type === "search" || s.type === "tool" || s.type === "read"
                );
                return {
                  ...p,
                  at: toolsAfterPartialAt,
                  content: "working",
                  steps: hasToolWork
                    ? kept
                    : [
                        ...kept,
                        {
                          id: `${reqId}:post-partial-working`,
                          type: "tool" as const,
                          content: "checking…",
                          status: "running" as const,
                          at: toolsAfterPartialAt,
                        },
                      ],
                };
              })
            );
          } else if (evt.type === "thinking") {
            const content = (evt.content || "").trim();
            const reqId = eventRequestId(evt);
            if (content) {
              // Only nudge the single bootstrap label — never stack extra rows.
              setActivities((prev) =>
                prev.map((p) => {
                  if (p.kind !== "thinking" || p.request_id !== reqId) return p;
                  return {
                    ...p,
                    steps: (p.steps || []).map((s) =>
                      s.id === bootstrapStepId && s.status === "running"
                        ? { ...s, content: "thinking…" }
                        : s
                    ),
                  };
                })
              );
            }
          } else if (evt.type === "memory_saved") {
            setActivities((prev) => [
              ...prev,
              { kind: "memory", id: crypto.randomUUID(), memoryCount: evt.memory_count, at: Date.now() },
            ]);
            setMemoryCount(evt.memory_count);
            setEchoReaction("memory_saved");
            if (leftTab === "memory") {
              refreshMemory();
            }
          } else if ((evt as any).type === "status" && (evt as any).agent_mode) {
            const mode = String((evt as any).agent_mode || "idle");
            setAgentMode(mode as any);
          } else if (evt.type === "error") {
            setStreaming(false);
            setLiveReplyDraft("");
            completeAllRunningSteps("failed");
            setActivities((prev) => [
              ...prev,
              { kind: "error", id: crypto.randomUUID(), message: evt.message, at: Date.now() },
            ]);
            setEchoReaction("error");
          } else if (evt.type === "final") {
            // Guard: stream can surface final more than once; never double-commit chat/TTS.
            if (finalHandled) continue;
            finalHandled = true;

            const liveDraft = liveReplyDraftRef.current.trim();
            const reply = mergeFinalReply(
              evt.response,
              liveDraft,
              partialReplies,
              Array.isArray(evt.partial_replies) ? evt.partial_replies : []
            );

            // Stale ownership: if the user switched Session/Project mid-stream,
            // keep durable backend history but do not paint the final into the wrong chat.
            // Own the Project captured at send time; compare against live refs (not stale closures).
            if (
              !canApplyFinalToChat({
                activeThreadId: String(activeThreadIdRef.current || ""),
                activeProjectId: String(activeProjectIdRef.current || ""),
                ownedThreadId: streamThreadId,
                ownedProjectId: streamProjectId,
                streamOpen: streamControllersRef.current.get(streamThreadId) === streamController,
              })
            ) {
              liveReplyDraftRef.current = "";
              setLiveReplyDraft("");
              setStreaming(false);
              continue;
            }

            if (evt.execution_id) {
              durableTurnId = String(evt.execution_id);
            }
            const executionStatus = String(evt.thread_state?.execution_status || "");
            // Only mark tool rows done when backend authority says complete — not on soft success.
            if (executionStatus === "complete" && evt.success) {
              completeAllRunningSteps("done");
            } else if (
              ["failed", "blocked", "retryable", "cancelled", "partially_complete", "in_progress", "needs_permission"].includes(
                executionStatus
              ) ||
              !evt.success
            ) {
              completeAllRunningSteps(executionStatus === "needs_permission" ? "done" : "failed");
            }
            if (typeof evt.memory_count === "number") {
              setMemoryCount(evt.memory_count);
            }
            setDocSources(Array.isArray(evt.doc_sources) ? evt.doc_sources : []);
            if (evt.thread_state) {
              setThreadState(evt.thread_state);
              setActiveProjectId(String(evt.thread_state.active_project_id || ""));
              setLatestExecutionId(String(evt.thread_state.last_execution_id || evt.execution_id || ""));
              setLatestTraceId(String(evt.thread_state.last_trace_id || evt.trace_id || ""));
            } else {
              if (evt.execution_id) setLatestExecutionId(String(evt.execution_id));
              if (evt.trace_id) setLatestTraceId(String(evt.trace_id));
            }
            if (Array.isArray(evt.research) && evt.research.length) {
              const finals = evt.research
                .map((item) => normalizeResearchRun(item))
                .filter((item): item is ResearchRun => Boolean(item));
              replaceResearchRuns(finals);
              for (const r of finals) {
                if (!turnResearchRuns.some((t) => t.id === r.id)) turnResearchRuns.push(r);
                if (r.query) turnSearchQueries.push(r.query);
              }
              void refreshResearchArtifacts();
            }

            liveReplyDraftRef.current = "";
            setLiveReplyDraft("");
            setStreaming(false);

            if (reply) {
              const alreadyStreamed = liveDraft.length > 0;
              const ctxWindow = Number(providerInfo?.context_window || 0) || 32768;
              const renderPlan = buildResponseRenderPlan({
                answerText: reply,
                intent: evt.response_render,
                researchRuns: turnResearchRuns,
                searchQueries: turnSearchQueries,
              });
              const embeds = buildChatEmbeds({
                answerText: reply,
                researchRuns: turnResearchRuns,
                searchQueries: turnSearchQueries,
              });
              const finalExecId = String(evt.execution_id || durableTurnId || "").trim();
              // Scope Session thread_state actions to this execution only.
              const liveProjection = evt.execution_projection || {};
              const projectedChangedFiles = (Array.isArray(liveProjection.files_actually_changed)
                ? liveProjection.files_actually_changed
                : [])
                .flatMap((item: any) => [String(item?.path || "").trim(), String(item?.destination || "").trim()])
                .filter(Boolean);
              const liveOpState = evt.thread_state
                ? ({
                    ...evt.thread_state,
                    execution_status: String(liveProjection.status || evt.thread_state.execution_status || ""),
                    current_execution_id: finalExecId || evt.thread_state.current_execution_id,
                    last_execution_id: finalExecId || evt.thread_state.last_execution_id,
                    completed_actions: (evt.thread_state.completed_actions || []).filter(
                      (a: any) => !finalExecId || String(a?.execution_id || "") === finalExecId
                    ),
                    failed_actions: (evt.thread_state.failed_actions || []).filter(
                      (a: any) => !finalExecId || String(a?.execution_id || "") === finalExecId
                    ),
                    pending_actions: (evt.thread_state.pending_actions || []).filter(
                      (a: any) => !finalExecId || String(a?.execution_id || "") === finalExecId
                    ),
                    retry_target:
                      liveProjection.retry_target && typeof liveProjection.retry_target === "object"
                        ? liveProjection.retry_target
                        : {},
                    operation_details: {
                      ...(evt.thread_state.operation_details || {}),
                      files_changed: projectedChangedFiles,
                      memory_records: Array.isArray(liveProjection.memory_records)
                        ? liveProjection.memory_records.map((item: any) => String(item?.memory_id || item?.item_id || "")).filter(Boolean)
                        : [],
                    },
                  } as OperationalThreadState)
                : undefined;
              addMessage({
                id: crypto.randomUUID(),
                role: "assistant",
                text: reply,
                // Always after tools: toolsAfterPartialAt is 0 when no partial.
                at: Math.max(Date.now(), toolsAfterPartialAt + 1),
                skipTypewriter: alreadyStreamed || partialReplies.length > 0,
                streamBeat: "final",
                renderPlan,
                embeds: embeds.length ? embeds : undefined,
                executionId: finalExecId || undefined,
                clientRequestId: runRequestId,
                operation: liveOpState
                  ? {
                      state: liveOpState,
                      success: Boolean(evt.success),
                      executionId: finalExecId || undefined,
                    }
                  : undefined,
                docSources: Array.isArray(evt.doc_sources) ? evt.doc_sources : undefined,
                usage: buildMessageUsage(reply, useAppStore.getState().messages, ctxWindow, {
                  provider: providerInfo?.provider,
                  model: providerInfo?.model,
                }),
              });
              // The backend's spoken_text is the final remainder after any
              // already-spoken partial reply. Never fall back to the merged
              // display reply when a preamble has already been played.
              const spoken = (evt.spoken_text || "").trim();
              const speakVal = spoken || (partialReplies.length ? "" : reply);
              if (voiceReadAloud || voiceConversationMode) {
                const playbackScope = {
                  apiBase,
                  sessionId: String(activeThreadIdRef.current || ""),
                  projectId: String(activeProjectIdRef.current || ""),
                  clientTurnId: voiceTranscript?.clientTurnId || runRequestId,
                  requestId: runRequestId,
                  executionId: finalExecId,
                  taskRunId: activeTaskRunIdsRef.current.get(streamThreadId) || "",
                };
                const playback = speakVal
                  ? speakLocalText(speakVal, playbackScope)
                  : localVoicePlayback.complete(playbackScope)
                      .then(() => true)
                      .catch((error) => {
                        setVoicePhase("error");
                        setVoiceNotice(error instanceof Error ? error.message : "Voice playback completion could not be saved.");
                        return false;
                      });
                void playback.then((played) => {
                  if (
                    played &&
                    voiceConversationMode &&
                    activeThreadIdRef.current === streamThreadId &&
                    !streamControllersRef.current.has(streamThreadId)
                  ) {
                    void start();
                  }
                });
              }
            } else if (!partialReplies.length && !reply) {
              // True empty — still surface something so the turn doesn't ghost.
              addMessage({
                id: crypto.randomUUID(),
                role: "assistant",
                text: "(no response)",
                at: Date.now(),
                skipTypewriter: true,
                operation: evt.thread_state ? {
                  state: evt.thread_state,
                  success: Boolean(evt.success),
                  executionId: evt.execution_id,
                } : undefined,
              });
            }

            setEchoReaction(evt.success && executionStatus !== "needs_permission" ? "success" : evt.success ? null : "error");
            setAgentMode("idle");
            refreshPendingApproval(streamThreadId);
            refreshApprovals(streamThreadId);
            refreshExecutions(streamThreadId);
            void loadCurrentWorkRuns(apiBase, streamThreadId, activeProjectIdRef.current || "");
            void refreshThreads();
          }
        }
      }
    } catch (err) {
      if (streamController.signal.aborted) {
        // Explicit same-Session supersession/delete: never paint a cancellation error.
        return;
      }
      if (!isStreamThreadCurrent(streamThreadId, activeThreadIdRef.current)) return;
      const msg = String(err);
      const pretty = msg.includes("Failed to fetch") ? `Backend offline (${apiBase})` : msg;
      setBackendOnline(false);
      dispatchActivity({ type: "error", message: pretty });
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${pretty}`, at: Date.now() });
      setActivities((prev) => [
        ...prev,
        { kind: "error", id: crypto.randomUUID(), message: pretty, at: Date.now() },
      ]);
      setEchoReaction("error");
    } finally {
      const owned = ownsStreamCleanup(streamControllersRef.current.get(streamThreadId), streamController);
      const sameThread = isStreamThreadCurrent(streamThreadId, activeThreadIdRef.current);
      const aborted = streamController.signal.aborted;

      if (owned) {
        streamControllersRef.current.delete(streamThreadId);
        if (activeRequestIdsRef.current.get(streamThreadId) === runRequestId) {
          activeRequestIdsRef.current.delete(streamThreadId);
          activeExecutionIdsRef.current.delete(streamThreadId);
          activeTaskRunIdsRef.current.delete(streamThreadId);
        }
        setSessionInFlight(streamThreadId, false);
      }

      // A superseded controller owns no visible or durable projection cleanup.
      if (!owned) return;

      // Only the visible Session owns the current projection's phase machine.
      if (sameThread && aborted) {
        dispatchActivity({ type: "reset" });
      } else if (sameThread) {
        dispatchActivity({ type: "stream_end" });
      }

      // Do not mutate chat of a different Session (switch already cleared UI).
      if (!sameThread) {
        return;
      }

      if (streamWasHidden) {
        // Frames skipped while this Session was hidden are reconstructed from
        // canonical Turns/ToolRuns, preventing duplicate or partially measured rows.
        await loadHistory(streamThreadId);
      }

      setStreaming(false);
      // If stream died without final but we already streamed tokens, promote draft once
      // only when the user did not cancel/abort mid-flight.
      if (!finalHandled && !aborted && liveReplyDraftRef.current.trim()) {
        const orphan = liveReplyDraftRef.current;
        const ctxWindow = Number(providerInfo?.context_window || 0) || 32768;
        addMessage({
          id: crypto.randomUUID(),
          role: "assistant",
          text: orphan,
          at: Date.now(),
          skipTypewriter: true,
          usage: buildMessageUsage(orphan, useAppStore.getState().messages, ctxWindow, {
            provider: providerInfo?.provider,
            model: providerInfo?.model,
          }),
        });
        // A stream without its canonical final event is incomplete. Keep the
        // visible recovered text, but do not speak it as if Echo finalized it.
      }
      liveReplyDraftRef.current = "";
      setLiveReplyDraft("");
      // An EOF without a final event is an interruption, never implicit success.
      // Only close running steps for THIS turn's thinking card.
      setActivities((prev) =>
        prev.map((p) => {
          if (p.kind !== "thinking" || p.request_id !== runRequestId || !p.steps?.length) return p;
          if (!p.steps.some((s) => s.status === "running")) return p;
          return {
            ...p,
            steps: p.steps.map((s) =>
              s.status === "running"
                ? { ...s, status: finalHandled && !aborted ? ("done" as const) : ("failed" as const) }
                : s
            ),
          };
        })
      );
      if (!finalHandled && streamThreadId && !aborted) {
        void refreshThreadState(streamThreadId);
        void refreshPendingApproval(streamThreadId);
        void refreshExecutions(streamThreadId);
      }
      if (finalHandled && !aborted && sameThread) {
        window.setTimeout(async () => {
          try {
            const response = await fetch(
              `${apiBase}/query/queue/claim?thread_id=${encodeURIComponent(streamThreadId)}`,
              { method: "POST" },
            );
            if (!response.ok) return;
            const payload = await response.json();
            const message = String(payload?.item?.message || "").trim();
            if (payload?.claimed && message && activeThreadIdRef.current === streamThreadId) {
              void sendText(message);
            }
          } catch {
            // The durable queue remains available for the next idle reconnect.
          }
        }, 200);
      }
    }
  };

  const queueFollowUp = async () => {
    const sessionId = String(activeThreadIdRef.current || "").trim();
    const message = String(input || "").trim();
    if (!sessionId || !message) {
      textareaRef.current?.focus();
      return;
    }
    const response = await fetch(`${apiBase}/query/queue`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        thread_id: sessionId,
        message,
        client_request_id: crypto.randomUUID(),
      }),
    });
    if (!response.ok) return;
    setInput("");
    setUserIsTyping(false);
    dispatchActivity({ type: "recovery", reason: "Follow-up queued." });
  };

  useEffect(() => {
    const sessionId = String(activeThreadId || "").trim();
    if (!sessionId || streaming || streamControllersRef.current.has(sessionId)) return;
    let cancelled = false;
    const resumeQueued = async () => {
      try {
        const response = await fetch(
          `${apiBase}/query/queue/claim?thread_id=${encodeURIComponent(sessionId)}`,
          { method: "POST" },
        );
        if (!response.ok || cancelled) return;
        const payload = await response.json();
        const message = String(payload?.item?.message || "").trim();
        if (payload?.claimed && message && activeThreadIdRef.current === sessionId) {
          void sendText(message);
        }
      } catch {
        // Persisted input remains queued until the Session is idle again.
      }
    };
    void resumeQueued();
    return () => { cancelled = true; };
    // sendText intentionally follows the current render's exact Session state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeThreadId, apiBase, streaming]);

  const submitVoiceTranscript = async (transcript: VoiceTranscript) => {
    if (
      String(activeThreadIdRef.current || "") !== transcript.sessionId ||
      String(activeProjectIdRef.current || "") !== transcript.projectId
    ) {
      setVoicePhase("error");
      setVoiceNotice("Voice capture ended after the active Session changed, so it was not submitted.");
      return;
    }
    setInput(transcript.text);
    if (transcript.controlHint === "cancel_active") {
      stopTts();
      if (activeRequestIdsRef.current.get(transcript.sessionId)) {
        cancelSessionTurn(transcript.sessionId, true, transcript);
      } else {
        void fetch(
          `${apiBase}/media-runtime/voice/turns/${encodeURIComponent(transcript.voiceTurnId)}/cancel`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: transcript.sessionId }),
          },
        ).catch(() => undefined);
      }
      setVoicePhase("idle");
      setVoiceNotice("Stopped by Ty.");
      setInput("");
      return;
    }
    if (["canonical_steer", "canonical_continue", "canonical_inspect"].includes(transcript.controlHint)) {
      const taskRunId = activeTaskRunIdsRef.current.get(transcript.sessionId) || "";
      const requestId = activeRequestIdsRef.current.get(transcript.sessionId) || "";
      if (taskRunId && requestId) {
        const response = await fetch(`${apiBase}/query/steer`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            thread_id: transcript.sessionId,
            instruction: transcript.text,
            task_run_id: taskRunId,
            client_request_id: requestId,
            voice_turn_id: transcript.voiceTurnId,
          }),
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(String(payload?.detail || "The active run could not accept that direction."));
        }
        dispatchActivity({ type: "steer", instruction: transcript.text });
        setInput("");
        setVoicePhase("idle");
        setVoiceNotice(
          transcript.controlHint === "canonical_inspect"
            ? "Status question attached to the active run."
            : transcript.controlHint === "canonical_continue"
            ? "The active run will continue."
            : "Direction added to the active run.",
        );
        return;
      }
    }
    await sendText(transcript.text, transcript);
  };

  const start = async () => {
    const sessionId = String(activeThreadIdRef.current || "").trim();
    if (!sessionId || !voiceInputRef.current) {
      setVoicePhase("error");
      setVoiceNotice("Create or select a Session before using Voice.");
      return;
    }
    stopTts();
    setVoiceNotice("");
    try {
      await voiceInputRef.current.start(
        {
          apiBase,
          sessionId,
          projectId: String(activeProjectIdRef.current || ""),
        },
        {
          onPhase: (phase, detail) => {
            setVoicePhase(phase);
            setVoiceNotice(detail || "");
            setListening(phase === "listening" || phase === "requesting_permission");
          },
          onLevel: setVoiceInputLevel,
          onFinalTranscript: (transcript) => {
            setListening(false);
            setVoiceInputLevel(0);
            void submitVoiceTranscript(transcript).catch((error) => {
              setVoicePhase("error");
              setVoiceNotice(error instanceof Error ? error.message : "The spoken instruction could not be applied.");
            });
          },
          onFailure: (error) => {
            setListening(false);
            setVoiceInputLevel(0);
            setVoicePhase("error");
            setVoiceNotice(error.message || "Local transcription is unavailable.");
          },
        },
      );
    } catch (error) {
      setListening(false);
      setVoicePhase("error");
      setVoiceNotice(error instanceof Error ? error.message : "Local microphone capture is unavailable.");
    }
  };

  const stop = async () => {
    if (!voiceInputRef.current) return;
    setListening(false);
    try {
      const transcript = await voiceInputRef.current.stop(true);
      if (!transcript) return;
      await submitVoiceTranscript(transcript);
    } catch (error) {
      setVoicePhase("error");
      setVoiceNotice(error instanceof Error ? error.message : "Local transcription is unavailable.");
    } finally {
      setListening(false);
      setVoiceInputLevel(0);
    }
  };

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!event.ctrlKey || event.key.toLowerCase() !== "m") return;
      event.preventDefault();
      if (voiceInputRef.current?.active) void stop();
      else void start();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  });

  useEffect(() => {
    if (voiceInputRef.current?.active) void voiceInputRef.current.stop(false);
    setListening(false);
    setVoiceInputLevel(0);
    setVoicePhase("idle");
    setVoiceNotice("");
    stopTts();
  }, [activeThreadId, activeProjectId]);

  const refreshMonitor = async () => {
    try {
      setMonitorError(null);
      const resp = await fetchWithTimeout(`${apiBase}/vision/analyze`, { method: "POST" }, 6000);
      if (!resp.ok) {
        const t = await resp.text();
        throw new Error(t || `${resp.status} ${resp.statusText}`);
      }
      const data = (await resp.json()) as VisionAnalyzeResponse;
      setMonitorText(String(data?.text || ""));
      setMonitorAt(Date.now());
    } catch (e) {
      setMonitorError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    if (!bootedRef.current) {
      bootedRef.current = true;
      addMessage({
        id: crypto.randomUUID(),
        role: "assistant",
        text: "Hello! I'm EchoSpeak. How can I assist you today?",
        at: Date.now(),
      });
    }
  }, [addMessage]);

  useEffect(() => {
    refreshProviderInfo({ allowRetry: true });
  }, [apiBase, activeThreadId]);

  const refreshServices = async () => {
    setServicesLoading(true);
    try {
      const hdRes = await fetchWithTimeout(`${apiBase}/heartbeat`, undefined, 2000);
      const hb = hdRes.ok ? await hdRes.json() : null;
      const hhRes = await fetchWithTimeout(`${apiBase}/heartbeat/history?limit=10`, undefined, 2000);
      const hh = hhRes.ok ? await hhRes.json() : [];
      const tgRes = await fetchWithTimeout(`${apiBase}/telegram`, undefined, 2000);
      const tg = tgRes.ok ? await tgRes.json() : null;
      const dcRes = await fetchWithTimeout(`${apiBase}/discord`, undefined, 2000);
      const dc = dcRes.ok ? await dcRes.json() : null;

      setServicesHeartbeatStatus(hb);
      setServicesHeartbeatHistory(hh?.history || hh || []);
      setServicesTelegramStatus(tg);
      setServicesDiscordStatus(dc);
    } catch (e) {
      console.error("Failed to refresh services", e);
    } finally {
      setServicesLoading(false);
    }
  };

  const refreshStudioOverview = async () => {
    setStudioOverviewLoading(true);
    try {
      const response = await fetchWithTimeout(
        `${apiBase}/studio/overview?session_id=${encodeURIComponent(activeThreadId)}`,
        undefined,
        4000,
      );
      if (!response.ok) throw new Error(`Settings projection failed (${response.status})`);
      const projection = await response.json();
      setStudioOverview(projection);
      setRoutines(projection.routines || []);
    } catch (error) {
      console.error("Failed to refresh Studio projection", error);
    } finally {
      setStudioOverviewLoading(false);
    }
  };

  const refreshConnections = async () => {
    if (!activeThreadId) {
      setConnectionCatalog([]);
      setConnectionError("");
      return;
    }
    try {
      const query = new URLSearchParams({
        session_id: activeThreadId,
        project_id: activeProjectId,
      });
      const response = await fetchWithTimeout(
        `${apiBase}/connections/catalog?${query}`,
        undefined,
        5000,
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(payload?.detail || `Connections failed (${response.status})`));
      setConnectionCatalog(Array.isArray(payload.items) ? payload.items : []);
      setConnectionError("");
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : String(error));
    }
  };

  const refreshSettingsCatalog = async () => {
    if (!activeThreadId) {
      settingsCatalogRequestRef.current += 1;
      setSettingsCatalog([]);
      setSettingsCatalogError("");
      setSettingsCatalogLoadedAt(0);
      setSettingsCatalogScope("");
      return;
    }
    const requestNumber = ++settingsCatalogRequestRef.current;
    const requestedScope = `${activeThreadId}:${activeProjectId}`;
    setSettingsCatalogLoading(true);
    try {
      const query = new URLSearchParams({
        session_id: activeThreadId,
        project_id: activeProjectId,
      });
      const response = await fetchWithTimeout(
        `${apiBase}/settings/catalog?${query}`,
        undefined,
        12000,
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(payload?.detail || `Settings catalog failed (${response.status})`));
      if (requestNumber !== settingsCatalogRequestRef.current) return;
      setSettingsCatalog(Array.isArray(payload.cards) ? payload.cards : []);
      setSettingsCatalogLoadedAt(Date.now());
      setSettingsCatalogScope(requestedScope);
      setSettingsCatalogError("");
    } catch (error) {
      if (requestNumber !== settingsCatalogRequestRef.current) return;
      setSettingsCatalogError(error instanceof Error ? error.message : String(error));
    } finally {
      if (requestNumber === settingsCatalogRequestRef.current) setSettingsCatalogLoading(false);
    }
  };

  const handleSettingsCatalogAction = (
    card: SettingsCatalogCard,
    action: SettingsCatalogAction,
  ) => {
    if (card.category === "connections" && action.kind === "connect") {
      if (!activeProjectId) {
        setConnectionError("Select a Project before connecting an external capability.");
        return;
      }
      const providerId = card.id.replace(/^connection:/, "");
      const provider = connectionCatalog.find((item: any) => String(item.id || "") === providerId);
      if (provider) void runConnectionAction(provider, "connect");
      else setConnectionError("This Connection is no longer present in the current catalog. Refresh and try again.");
      return;
    }
    if (action.kind === "inspect") {
      if (card.category === "skills") setLeftTab("skills");
      else if (card.category === "local_tools") setLeftTab("capabilities");
      return;
    }
    // Credential entry, provider endpoints, local paths, commands, and MCP
    // transports deliberately remain behind the explicit Advanced surface.
    // This navigation does not mutate provider or Connection authority.
    if (["advanced", "configure", "manage"].includes(action.kind)) {
      setLeftTab("advanced_settings");
    }
  }

  const selectLocalVoiceProvider = async (
    operation: "speech_to_text" | "text_to_speech",
    providerId: string,
  ) => {
    setSettingsSaving(true);
    setSettingsCatalogError("");
    try {
      const key = operation === "speech_to_text" ? "voice_local_stt_provider" : "voice_local_tts_provider";
      const response = await fetchWithTimeout(`${apiBase}/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: providerId }),
      }, 10000);
      if (!response.ok) throw new Error(`Voice preference could not be saved (${response.status})`);
      await refreshSettingsCatalog();
    } catch (error) {
      setSettingsCatalogError(error instanceof Error ? error.message : String(error));
    } finally {
      setSettingsSaving(false);
    }
  };

  const setCloudVoiceOptIn = async (providerId: string, enabled: boolean) => {
    setSettingsSaving(true);
    setSettingsCatalogError("");
    try {
      const response = await fetchWithTimeout(`${apiBase}/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice_cloud_provider: enabled ? providerId : "" }),
      }, 10000);
      if (!response.ok) throw new Error(`Cloud Voice preference could not be saved (${response.status})`);
      await refreshSettingsCatalog();
    } catch (error) {
      setSettingsCatalogError(error instanceof Error ? error.message : String(error));
    } finally {
      setSettingsSaving(false);
    }
  };

  async function runConnectionAction(
    provider: any,
    action: "connect" | "probe" | "reconnect" | "disable" | "disconnect",
  ) {
    if (!activeThreadId || !activeProjectId) return;
    const connection = provider?.connection;
    const busyKey = String(connection?.id || provider?.id || action);
    setConnectionBusyId(busyKey);
    setConnectionError("");
    try {
      let response: Response;
      if (action === "connect") {
        if (provider.id !== "obsidian") {
          throw new Error(
            provider.rollout_state === "advanced"
              ? "Configure custom MCP transports in Advanced until the reviewed editor is available here."
              : `${provider.name} OAuth is not installed yet; EchoSpeak did not create a fake connection.`,
          );
        }
        if (!desktopMode) {
          throw new Error("Connect local folders from the EchoSpeak desktop application. Browser development does not receive filesystem paths.");
        }
        const vaultPath = await pickDesktopConnectionFolder(provider.name);
        if (!vaultPath) return;
        response = await fetchWithTimeout(`${apiBase}/connections/authorize`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider_id: provider.id,
            session_id: activeThreadId,
            project_id: activeProjectId,
            display_name: provider.name,
            configuration: { vault_path: vaultPath },
            credentials: {},
            allow_global: false,
          }),
        }, 8000);
      } else if (action === "disconnect") {
        const query = new URLSearchParams({
          session_id: activeThreadId,
          project_id: activeProjectId,
          expected_revision: String(connection.revision),
        });
        response = await fetchWithTimeout(
          `${apiBase}/connections/${encodeURIComponent(connection.id)}?${query}`,
          { method: "DELETE" },
          5000,
        );
      } else {
        response = await fetchWithTimeout(
          `${apiBase}/connections/${encodeURIComponent(connection.id)}/${action}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              session_id: activeThreadId,
              project_id: activeProjectId,
              expected_revision: connection.revision,
            }),
          },
          8000,
        );
      }
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(payload?.detail || `Connection action failed (${response.status})`));
      await Promise.all([refreshConnections(), refreshSettingsCatalog(), refreshStudioOverview()]);
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : String(error));
    } finally {
      setConnectionBusyId("");
    }
  }

  const toggleConnectionCapability = async (
    connection: any,
    capability: any,
  ) => {
    setConnectionBusyId(String(connection.id));
    try {
      const response = await fetchWithTimeout(
        `${apiBase}/connections/${encodeURIComponent(connection.id)}/capabilities/${encodeURIComponent(capability.id)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: activeThreadId,
            project_id: activeProjectId,
            expected_revision: connection.revision,
            enabled: !capability.enabled,
          }),
        },
        5000,
      );
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(payload?.detail || `Capability update failed (${response.status})`));
      await Promise.all([refreshConnections(), refreshSettingsCatalog()]);
    } catch (error) {
      setConnectionError(error instanceof Error ? error.message : String(error));
    } finally {
      setConnectionBusyId("");
    }
  };

  const refreshResearchArtifacts = async () => {
    if (!activeThreadId || !activeProjectId) {
      setResearchArtifacts([]);
      return;
    }
    try {
      const query = new URLSearchParams({ session_id: activeThreadId, project_id: activeProjectId, limit: "20" });
      const response = await fetchWithTimeout(`${apiBase}/research/artifacts?${query}`, undefined, 5000);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(String(payload?.detail || `Research artifacts failed (${response.status})`));
      setResearchArtifacts(Array.isArray(payload.items) ? payload.items : []);
      setResearchArtifactsError("");
    } catch (error) {
      setResearchArtifactsError(String(error));
    }
  };

  useEffect(() => {
    if (leftTab === "research" || (desktopMode && desktopSurface === "visualizer" && visualizerPin === "research")) void refreshResearchArtifacts();
  }, [activeProjectId, activeThreadId, desktopMode, desktopSurface, leftTab, visualizerPin]);

  useEffect(() => {
    const gatewayUrl = `${apiBase.replace(/^http/i, "ws")}/gateway/ws`;
    let disposed = false;

    const clearRetryTimer = () => {
      if (gatewayRetryTimerRef.current != null) {
        window.clearTimeout(gatewayRetryTimerRef.current);
        gatewayRetryTimerRef.current = null;
      }
    };

    const scheduleReconnect = () => {
      if (disposed || gatewayRetryTimerRef.current != null) return;
      const attempt = gatewayRetryAttemptRef.current + 1;
      gatewayRetryAttemptRef.current = attempt;
      const delay = Math.min(1000 * Math.pow(2, Math.max(0, attempt - 1)), 10000);
      gatewayRetryTimerRef.current = window.setTimeout(() => {
        gatewayRetryTimerRef.current = null;
        connectGateway();
      }, delay);
    };

    const connectGateway = () => {
      if (disposed) return;
      try {
        if (gatewaySocketRef.current) {
          try {
            gatewaySocketRef.current.close();
          } catch {
            // ignore
          }
          gatewaySocketRef.current = null;
        }

        const ws = createEchoSpeakWebSocket(gatewayUrl);
        gatewaySocketRef.current = ws;

        ws.onopen = () => {
          if (disposed) return;
          clearRetryTimer();
          gatewayRetryAttemptRef.current = 0;
          setDiscordGatewayConnected(true);
        };

        ws.onmessage = (evt: MessageEvent) => {
          if (disposed) return;
          let payload: GatewayEvent | null = null;
          try {
            payload = JSON.parse(String(evt.data || "")) as GatewayEvent;
          } catch {
            return;
          }
          if (!payload || typeof payload !== "object") return;

          if (payload.type === "gateway_ready") {
            setDiscordGatewayConnected(true);
            setDiscordGatewaySessionId(String(payload.session_id || ""));
            return;
          }

          if (payload.type === "discord_activity") {
            const at = normalizeTimestampMs(payload.at || Date.now());
            const tool = String(payload.tool || "unknown");
            const source = String(payload.source || "discord_bot");
            setDiscordLiveEvents((prev) => {
              const nextEvent: DiscordLiveEvent = {
                id: crypto.randomUUID(),
                kind: "activity",
                tool,
                source,
                at,
              };
              return [nextEvent, ...prev].slice(0, 25);
            });
            return;
          }

          if (payload.type === "spotify_playback") {
            setSpotifyPlaying({
              is_playing: !!payload.is_playing,
              track_id: String(payload.track_id || ""),
              track_name: String(payload.track_name || ""),
              track_artist: String(payload.track_artist || ""),
            });
            return;
          }

          if (payload.type === "error") {
            setDiscordLiveEvents((prev) => {
              const nextEvent: DiscordLiveEvent = {
                id: crypto.randomUUID(),
                kind: "error",
                message: String(payload.message || "Gateway error"),
                at: normalizeTimestampMs(payload.at || Date.now()),
              };
              return [nextEvent, ...prev].slice(0, 25);
            });
          }
        };

        ws.onerror = () => {
          if (disposed) return;
          setDiscordGatewayConnected(false);
        };

        ws.onclose = () => {
          if (disposed) return;
          setDiscordGatewayConnected(false);
          setDiscordGatewaySessionId("");
          setSpotifyPlaying(null);
          if (gatewaySocketRef.current === ws) {
            gatewaySocketRef.current = null;
          }
          scheduleReconnect();
        };
      } catch (e) {
        setDiscordGatewayConnected(false);
        scheduleReconnect();
      }
    };

    connectGateway();

    return () => {
      disposed = true;
      clearRetryTimer();
      setDiscordGatewayConnected(false);
      setDiscordGatewaySessionId("");
      if (gatewaySocketRef.current) {
        try {
          gatewaySocketRef.current.close();
        } catch {
          // ignore
        }
        gatewaySocketRef.current = null;
      }
    };
  }, [apiBase]);

  useEffect(() => {
    return () => {
      if (backendRetryRef.current.timer != null) {
        window.clearTimeout(backendRetryRef.current.timer);
        backendRetryRef.current.timer = null;
      }
    };
  }, []);

  useEffect(() => {
    if (["overview", "skills", "capabilities", "executions", "automations", "connections"].includes(leftTab)) {
      refreshStudioOverview();
    }
    if (leftTab === "connections") refreshConnections();
    if (
      ["settings", "search_settings", "services", "mcp_settings", "connections", "capabilities", "skills"].includes(leftTab)
      && (
        settingsCatalogScope !== `${activeThreadId}:${activeProjectId}`
        || !settingsCatalog.length
        || Date.now() - settingsCatalogLoadedAt > 15000
      )
    ) {
      refreshSettingsCatalog();
    }
    if (leftTab === "memory") {
      refreshMemory();
      refreshMemoryDoctor();
    }
    if (leftTab === "docs") refreshDocuments();
    if (leftTab === "soul") refreshSoul();
    if (leftTab === "system_services") refreshServices();
    if (leftTab === "projects") refreshProjects();
    if (leftTab === "approvals") {
      refreshPendingApproval();
      refreshApprovals();
    }
    if (leftTab === "executions") {
      refreshExecutions();
      if (latestTraceId) loadTrace(latestTraceId);
    }
  }, [leftTab, activeThreadId, activeProjectId]);

  useEffect(() => {
    if (backendOnline === false) return;
    if (providerDraft.provider === "openai") {
      setProviderModels(openaiModelOptions);
      return;
    }
    if (providerDraft.provider === "gemini") {
      setProviderModels(geminiModelOptions);
      return;
    }
    if (listableProviders.includes(providerDraft.provider)) {
      setProviderModels([]);
      refreshProviderModels(providerDraft.provider);
      return;
    }
    setProviderModels([]);
  }, [providerDraft.provider, backendOnline]);

  useEffect(() => {
    if (providerModels.length && (providerDraft.provider === "openai" || providerDraft.provider === "gemini" || listableProviders.includes(providerDraft.provider))) {
      if (!providerModels.includes(providerDraft.model)) {
        setProviderDraft((d) => ({ ...d, model: providerModels[0] }));
      }
    }
  }, [providerModels, providerDraft.provider, lmStudioOnly, switchingProvider]);

  useEffect(() => {
    if (lmStudioOnly) return;
    if (suppressAutoApplyRef.current) return;
    if (switchingProvider) return;

    const next = { provider: providerDraft.provider, model: providerDraft.model, base_url: providerDraft.base_url };
    const last = lastAppliedProviderRef.current;
    if (last && last.provider === next.provider && last.model === (next.model || "")) return;

    const t = window.setTimeout(() => {
      applyProviderSwitch(next);
    }, next.provider === "llama_cpp" ? 800 : 250);

    return () => window.clearTimeout(t);
  }, [providerDraft.provider, providerDraft.model, providerDraft.base_url, switchingProvider]);

  useEffect(() => {
    const listener = () => {
      void voiceInputRef.current?.stop(false);
      stopTts();
    };
    window.addEventListener("beforeunload", listener);
    return () => window.removeEventListener("beforeunload", listener);
  }, []);

  useEffect(() => {
    if (!activeGroup) {
      setActiveGroupPos(null);
      return;
    }

    const computePos = () => {
      const btn = activeGroupButtonRef.current;
      if (!btn) return;
      const r = btn.getBoundingClientRect();
      setActiveGroupPos({
        top: Math.round(r.bottom + 8),
        left: Math.round(r.left),
      });
    };

    computePos();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setActiveGroup(null);
    };

    const onPointerDown = (e: MouseEvent | PointerEvent) => {
      const t = e.target as Node | null;
      if (!t) return;
      const menu = activeGroupMenuRef.current;
      const btn = activeGroupButtonRef.current;
      if (menu && menu.contains(t)) return;
      if (btn && btn.contains(t)) return;
      setActiveGroup(null);
    };

    const onWindowChange = () => {
      // Reposition on scroll/resize so the menu doesn't look "stuck".
      computePos();
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("resize", onWindowChange);
    window.addEventListener("scroll", onWindowChange, true);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("resize", onWindowChange);
      window.removeEventListener("scroll", onWindowChange, true);
    };
  }, [activeGroup]);

  useEffect(() => {
    if (!monitoring) return;
    let cancelled = false;
    let inFlight = false;

    const tick = async () => {
      if (cancelled) return;
      if (inFlight) {
        window.setTimeout(tick, 1200);
        return;
      }
      inFlight = true;
      try {
        await refreshMonitor();
      } finally {
        inFlight = false;
      }
      window.setTimeout(tick, 2200);
    };

    tick();
    return () => {
      cancelled = true;
    };
  }, [monitoring, apiBase]);

  useEffect(() => {
    saveRuntimeLayout(typeof window !== "undefined" ? window.localStorage : null, {
      sidebarVisible: showSidebar,
      sidebarCollapsed,
      visualizerVisible: showVisualizer,
      visualizerDensity,
    });
  }, [showSidebar, sidebarCollapsed, showVisualizer, visualizerDensity]);

  useEffect(() => {
    const onResize = () => setNarrowLayout(window.innerWidth < 900);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const showModelPicker =
    providerDraft.provider === "openai" ||
    providerDraft.provider === "gemini" ||
    providerModels.length > 0;
  const modelPickerOptions = showModelPicker
    ? (providerDraft.provider === "openai" ? openaiModelOptions : providerDraft.provider === "gemini" ? geminiModelOptions : providerModels)
    : [providerDraft.model || "Default model"];
  const modelPickerValue = showModelPicker ? providerDraft.model : modelPickerOptions[0];
  const studioTabMeta: Record<string, { label: string; group: string }> = {
    settings: { label: "Providers", group: "Models" },
    search_settings: { label: "Providers", group: "Search & Research" },
    services: { label: "Providers", group: "Voice & Speech" },
    avatar_editor: { label: "Echo companion", group: "Voice & Speech" },
    connections: { label: "Accounts & apps", group: "Connections" },
    capabilities: { label: "Inventory", group: "Local Tools" },
    skills: { label: "Registry", group: "Skills" },
    mcp_settings: { label: "Servers & tools", group: "MCP" },
    approvals: { label: "Approvals", group: "Privacy & Permissions" },
    memory: { label: "Memory", group: "Privacy & Permissions" },
    docs: { label: "Documents", group: "Privacy & Permissions" },
    soul: { label: "Soul", group: "Privacy & Permissions" },
    overview: { label: "Appearance", group: "Advanced" },
    projects: { label: "Projects & Sessions", group: "Advanced" },
    executions: { label: "Runtime viewer", group: "Advanced" },
    automations: { label: "Automations", group: "Advanced" },
    system_services: { label: "Background services", group: "Advanced" },
    advanced_settings: { label: "Runtime configuration", group: "Advanced" },
  }
  const studioTabs: { id: typeof leftTab; label: string; group: string }[] = STUDIO_SECTION_ORDER.map((id) => ({
    id: id as typeof leftTab,
    label: studioTabMeta[id]?.label || id,
    group: studioTabMeta[id]?.group || "Advanced",
  }));
  const studioGroups = [
    "Models",
    "Search & Research",
    "Voice & Speech",
    "Connections",
    "Local Tools",
    "Skills",
    "MCP",
    "Privacy & Permissions",
    "Advanced",
  ].map((label) => ({
    label,
    tabs: studioTabs.filter((tab) => tab.group === label),
  }));
  const studioOpen = desktopMode
    ? desktopSettingsOpen
    : leftTab !== "chat" && leftTab !== "research";
  const mediaWorkspaceOpen = !desktopMode && mediaRouteActive;
  const desktopVisualizerOpen = desktopMode && desktopSurface === "visualizer";
  const desktopContextualWorkspace = desktopMode && isDesktopContextualSurface(desktopSurface);
  const activeWorkspaceLabel = desktopMode
    ? desktopSurface === "visualizer"
      ? desktopVisualizerPanelLabel(visualizerPin || "ring")
      : desktopWorkspaceLabel(desktopSurface)
    : "EchoSpeak";
  const studioActiveTab = studioTabs.find((t) => t.id === leftTab);
  const activeStudioGroup = studioGroups.find((group) =>
    group.tabs.some((tab) => tab.id === leftTab)
  ) || studioGroups[0];
  const activeSettingsCatalogCategory: SettingsCatalogCategory | null = ({
    settings: "models",
    search_settings: "search_research",
    services: "voice_speech",
    connections: "connections",
    mcp_settings: "mcp",
    capabilities: "local_tools",
    skills: "skills",
  } as Partial<Record<DashboardTab, SettingsCatalogCategory>>)[leftTab] || null;
  const activeChatTask = currentWorkRuns.find((run) =>
    !["completed", "cancelled", "superseded", "quarantined"].includes(String(run.status || "").toLowerCase())
  ) || null;
  const activeChatRequirementStates = activeChatTask
    ? Object.values(activeChatTask.requirement_statuses || {})
    : [];
  const activeChatSatisfied = activeChatRequirementStates.filter((status) => status === "satisfied").length;
  const studioNavRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    studioNavRef.current?.querySelector<HTMLElement>(`[data-studio-tab="${leftTab}"]`)?.scrollIntoView({
      behavior: "smooth",
      block: "nearest",
      inline: "center",
    });
  }, [leftTab]);
  const closeStudio = () => {
    if (desktopSettingsWindow) {
      void controlDesktopWindow("close");
      return;
    }
    setLeftTab("chat");
    if (desktopMode) setDesktopSettingsOpen(false);
    if (mediaRouteActive) navigate("/app");
    setShowVisualizer(true);
  };
  useEffect(() => {
    if (!desktopMode || !studioOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeStudio();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [desktopMode, studioOpen]);

  const shellColumns = desktopMode
    ? [showSidebar ? (sidebarCollapsed || narrowLayout ? "56px" : "288px") : null, "minmax(0, 1fr)"].filter(Boolean).join(" ")
    : runtimeGridColumns({
      sidebarVisible: showSidebar,
      sidebarCollapsed: sidebarCollapsed || narrowLayout,
      visualizerVisible: mediaWorkspaceOpen ? true : showVisualizer && !narrowLayout,
      visualizerDensity,
    });

  return (
    <div
      className="echo-root"
      data-execution-profile={desktopMode ? desktopExecutionProfile(desktopSurface, visualizerPin || "ring") : undefined}
      style={{
        width: "100%",
        height: "100%",
        maxHeight: "100dvh",
        background: colors.bg,
        color: colors.text,
        overflow: "hidden",
        position: "relative",
      }}
    >
      <style>{globalCss}</style>
      {!showSidebar && !studioOpen ? (
        <button
          className="icon-button"
          onClick={() => setShowSidebar(true)}
          title="Show sidebar"
          style={{ position: "fixed", left: 10, top: 10, zIndex: 100 }}
        >
          ☰
        </button>
      ) : null}
      <div
        className={
          "app-shell" +
          (studioOpen && !desktopMode ? " is-studio-covered" : "") +
          (desktopMode ? " desktop-single-workspace" : "")
        }
        style={{
          gridTemplateColumns: shellColumns,
        }}
        aria-hidden={!desktopMode && studioOpen || undefined}
      >
        {showSidebar ? <ProjectSidebar
          desktop={desktopMode}
          hydrating={!initialHydrationComplete}
          collapsed={sidebarCollapsed || narrowLayout}
          projects={projects}
          sessions={threads}
          activeProjectId={activeProjectId}
          activeSessionId={activeThreadId}
          activeView={desktopMode
            ? (desktopSurface === "visualizer" ? "avatar" : desktopSurface)
            : (showVisualizer && !narrowLayout ? "avatar" : "chat")}
          onToggleCollapsed={() => setSidebarCollapsed(v => !v)}
          onNewSession={createNewThread}
          onAddFolder={() => void attachFolder()}
          onSelectSession={switchThread}
          onRenameSession={(id, title) => void renameThread(id, title)}
          onDeleteSession={(id) => void deleteThread(id)}
          onDeleteProject={async (id) => {
            const response = await fetch(`${apiBase}/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
            if (!response.ok) return;
            setThreads(items => items.map(item => item.projectId === id ? { ...item, projectId: "" } : item));
            if (activeProjectId === id) { setActiveProjectId(""); await refreshThreadState(activeThreadId); }
            await refreshProjects();
          }}
          onSettings={() => {
            setLeftTab("settings");
            if (desktopMode && !desktopSettingsWindow) {
              void openDesktopSettingsWindow().catch(() => setDesktopSettingsOpen(true));
            } else {
              setDesktopSettingsOpen(true);
            }
          }}
          settingsOpen={studioOpen}
          onView={(view) => {
            if (desktopMode) {
              const surface = desktopWorkspaceForView(view as DesktopSidebarView);
              setDesktopSurface(surface);
              if (mediaRouteActive) navigate("/app");
              setLeftTab("chat");
              if (surface === "visualizer" && !visualizerPin) setVisualizerPin("ring");
              return;
            }
            if (mediaRouteActive) navigate("/app");
            setLeftTab("chat");
            setShowVisualizer(view === "avatar");
            if (view === "avatar") setVisualizerPin((current) => current || "ring");
          }}
        /> : null}
        {mediaWorkspaceOpen ? (
          <div className="visualizer-pane media-workspace-pane" data-testid="media-workspace-pane">
            <MediaLibraryView
              apiBase={apiBase}
              sessionId={activeThreadId}
              projectId={activeProjectId}
            />
          </div>
        ) : null}
        {!mediaWorkspaceOpen && (desktopMode ? desktopVisualizerOpen : showVisualizer && !narrowLayout) ? (
          <div className="visualizer-pane">
            <VisualizerWorkspace
              apiBase={apiBase}
              sessionId={activeThreadId || ""}
              projectId={activeProjectId || ""}
              activity={agentActivity}
            />
          </div>
        ) : null}
        {desktopMode && studioOpen ? (
          <div
            className="desktop-studio-host"
            ref={setDesktopStudioHost}
            data-testid="desktop-studio-host"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) closeStudio();
            }}
          />
        ) : null}
        <div className={`glow-panel${desktopContextualWorkspace ? " desktop-contextual-workspace" : desktopMode ? " desktop-chat-workspace" : ""}`}>
          <div className="panel-header">
            <div className="title">
              <img src="/logo.png" alt="Logo" style={{ width: 14, height: 14, borderRadius: 2 }} />
              <span>{activeWorkspaceLabel}</span>
              {activeProjectId && leftTab === "chat" && threadState?.mode === "coding" && (
                <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 6, background: "linear-gradient(135deg, rgba(34,197,94,0.15), rgba(34,197,94,0.05))", border: "1px solid rgba(34,197,94,0.25)", color: "#22c55e", fontWeight: 600, marginLeft: 8 }}>
                  📁 {projects.find(p => p.id === activeProjectId)?.name || "Project Active"}
                </span>
              )}
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <button
                type="button"
                className="icon-button"
                onClick={() => {
                  if (studioOpen) {
                    closeStudio();
                    return;
                  }
                  setShowVisualizer(true);
                  setLeftTab("overview");
                  if (desktopMode && !desktopSettingsWindow) {
                    void openDesktopSettingsWindow().catch(() => setDesktopSettingsOpen(true));
                  } else if (desktopMode) {
                    setDesktopSettingsOpen(true);
                  }
                }}
                title={studioOpen ? "Close Settings" : "Open Settings"}
                style={{
                  display: "none",
                  height: 32,
                  padding: "0 12px",
                  fontSize: 12,
                  fontWeight: 700,
                  color: "#fff",
                  background: studioOpen ? "rgba(140,180,255,0.16)" : "transparent",
                  border: `1px solid ${studioOpen ? "rgba(140,180,255,0.38)" : colors.line}`,
                }}
              >
                Settings
              </button>
              <div
                className={`switcher-dot ${backendOnline ? "online" : "offline"}`}
                title={backendOnline ? "Connected" : "Disconnected"}
              />
              <button
                type="button"
                className="icon-button"
                onClick={() => setSpeechEnabled(!speechEnabled)}
                title={speechEnabled ? "Mute Speech" : "Unmute Speech"}
                style={{
                  display: "none",
                  color: "#fff",
                  background: speechEnabled ? "#222" : "transparent",
                  border: `1px solid ${colors.line}`,
                }}
              >
                {speechEnabled ? (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>
                ) : (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><line x1="23" y1="9" x2="17" y2="15"></line><line x1="17" y1="9" x2="23" y2="15"></line></svg>
                )}
              </button>

              <button
                type="button"
                className="icon-button"
                onClick={() => setShowVisualizer((v) => !v)}
                title={showVisualizer ? "Hide visualizer" : "Show visualizer"}
                style={{
                  display: "none",
                  color: "#fff",
                  background: showVisualizer ? "#222" : "transparent",
                  border: `1px solid ${colors.line}`,
                }}
              >
                {showVisualizer ? (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="4" width="18" height="16" rx="2" />
                    <path d="M12 4v16" />
                  </svg>
                ) : (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="4" width="18" height="16" rx="2" />
                    <path d="M12 4v16" />
                    <path d="M8 9h2M8 13h2M8 17h2" />
                  </svg>
                )}
              </button>
            </div>
          </div>
          <div className="panel-body">
            <div className="research-panel">
              <div className="tab-bar" style={{
                display: "none",
                position: "relative",
                overflow: "visible",
                marginBottom: "16px",
              }}>
                <div className="top-tab-groups" style={{
                  alignItems: "center",
                  padding: 0,
                  background: "transparent",
                  borderRadius: 0,
                  border: "none",
                  boxShadow: "none",
                  backdropFilter: "none",
                  WebkitBackdropFilter: "none",
                  overflowY: "hidden",
                  scrollbarWidth: "none",
                }}>
                  {[
                    { id: 'core', label: 'Core', icon: '⚡', tabs: [{ id: 'chat', label: 'Chat' }, { id: 'research', label: 'Research' }] },
                    { id: 'knowledge', label: 'Knowledge', icon: '📚', tabs: [{ id: 'memory', label: 'Memory' }, { id: 'docs', label: 'Docs' }] },
                    { id: 'config', label: 'Config', icon: '⚙️', tabs: [{ id: 'settings', label: 'Settings' }, { id: 'capabilities', label: 'Tools' }, { id: 'soul', label: 'Soul' }, { id: 'avatar_editor', label: 'Avatar' }] },
                    { id: 'operations', label: 'Operations', icon: '🤖', tabs: [{ id: 'overview', label: 'Overview' }, { id: 'skills', label: 'Skills' }, { id: 'executions', label: 'Viewer' }, { id: 'approvals', label: 'Approvals' }, { id: 'projects', label: 'Projects' }, { id: 'automations', label: 'Automations' }, { id: 'connections', label: 'Connections' }, { id: 'services', label: 'Services' }] },
                  ].map((group) => {
                    const isGroupActive = group.tabs.some(t => t.id === leftTab);
                    return (
                      <div key={group.id} className="top-tab-group">
                        <button
                          type="button"
                          className={`tab-button ${isGroupActive ? "active" : ""}`}
                          ref={(el) => {
                            if (activeGroup === group.id) activeGroupButtonRef.current = el;
                          }}
                          onClick={(e) => {
                            if (group.tabs.length === 1) {
                              setLeftTab(group.tabs[0].id as any);
                              setActiveGroup(null);
                            } else {
                              activeGroupButtonRef.current = e.currentTarget as HTMLButtonElement;
                              setActiveGroup(activeGroup === group.id ? null : group.id);
                            }
                          }}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 6,
                            padding: "8px 16px",
                            borderRadius: "12px",
                            fontSize: "13px",
                            fontWeight: 600,
                            background: isGroupActive ? "linear-gradient(135deg, rgba(255,255,255,0.15) 0%, rgba(255,255,255,0.05) 100%)" : "transparent",
                            border: isGroupActive ? "1px solid rgba(255,255,255,0.2)" : "1px solid transparent",
                            boxShadow: isGroupActive ? "inset 0 1px 1px rgba(255,255,255,0.3), 0 2px 8px rgba(0,0,0,0.2)" : "none",
                            color: isGroupActive ? "#ffffff" : "rgba(255,255,255,0.6)",
                            transition: "all 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
                            cursor: "pointer",
                            whiteSpace: "nowrap"
                          }}
                        >
                          <span style={{ fontSize: "16px", filter: "brightness(0) invert(1)", opacity: isGroupActive ? 1 : 0.7 }}>{group.icon}</span>
                          <span style={{ textShadow: isGroupActive ? "0 0 8px rgba(255,255,255,0.4)" : "none" }}>{group.label}</span>
                          {group.tabs.length > 1 && (
                            <span style={{ fontSize: "10px", opacity: 0.5, marginLeft: 4 }}>{activeGroup === group.id ? '▲' : '▼'}</span>
                          )}
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>

              {activeGroup && activeGroupPos
                ? createPortal(
                  <AnimatePresence>
                    <motion.div
                      ref={(el) => {
                        activeGroupMenuRef.current = el;
                      }}
                      initial={{ opacity: 0, y: 8, scale: 0.95 }}
                      animate={{ opacity: 1, y: 0, scale: 1 }}
                      exit={{ opacity: 0, y: 4, scale: 0.95 }}
                      transition={{ duration: 0.15 }}
                      style={{
                        position: "fixed",
                        top: activeGroupPos.top,
                        left: activeGroupPos.left,
                        zIndex: 2147483647,
                        display: "flex",
                        flexDirection: "column",
                        gap: 2,
                        padding: "6px",
                        background: "rgba(20, 20, 20, 0.95)",
                        backdropFilter: "blur(16px)",
                        WebkitBackdropFilter: "blur(16px)",
                        borderRadius: "12px",
                        border: `1px solid ${colors.line}`,
                        boxShadow:
                          "0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.5)",
                        minWidth: "140px",
                      }}
                    >
                      {(
                        [
                          { id: 'core', label: 'Core', icon: '⚡', tabs: [{ id: 'chat', label: 'Chat' }, { id: 'research', label: 'Research' }] },
                          { id: 'knowledge', label: 'Knowledge', icon: '📚', tabs: [{ id: 'memory', label: 'Memory' }, { id: 'docs', label: 'Docs' }] },
                          { id: 'config', label: 'Config', icon: '⚙️', tabs: [{ id: 'settings', label: 'Settings' }, { id: 'capabilities', label: 'Tools' }, { id: 'soul', label: 'Soul' }, { id: 'avatar_editor', label: 'Avatar' }] },
                          { id: 'operations', label: 'Operations', icon: '🤖', tabs: [{ id: 'overview', label: 'Overview' }, { id: 'skills', label: 'Skills' }, { id: 'executions', label: 'Viewer' }, { id: 'approvals', label: 'Approvals' }, { id: 'projects', label: 'Projects' }, { id: 'automations', label: 'Automations' }, { id: 'connections', label: 'Connections' }, { id: 'services', label: 'Services' }] },
                        ].find((g) => g.id === activeGroup)?.tabs || []
                      ).map((tab) => (
                        <button
                          key={tab.id}
                          type="button"
                          className={`tab-button ${leftTab === tab.id ? "active" : ""}`}
                          onClick={() => {
                            setLeftTab(tab.id as any);
                            setActiveGroup(null);
                          }}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            padding: "8px 12px",
                            borderRadius: "8px",
                            fontSize: "12px",
                            fontWeight: 500,
                            textAlign: "left",
                            background: leftTab === tab.id ? "rgba(255,255,255,0.1)" : "transparent",
                            color: leftTab === tab.id ? colors.text : colors.textDim,
                            border: "none",
                            cursor: "pointer",
                            transition: "all 0.15s ease",
                            width: "100%",
                          }}
                        >
                          {tab.label}
                        </button>
                      ))}
                    </motion.div>
                  </AnimatePresence>,
                  document.body
                )
                : null}

              {/* Chat Tab */}
              {true && (
                <>
                  <div key={activeThreadId || "quick-chat"} className="chat-scroll" style={{ flex: 1 }} ref={chatScrollRef} onScroll={onChatScroll}>
                    {activeChatTask ? (
                      <section style={{ margin: "4px 4px 12px", padding: "11px 13px", border: "1px solid rgba(255,255,255,.1)", background: "linear-gradient(115deg,rgba(255,255,255,.045),rgba(255,255,255,.012))", borderRadius: 5, display: "flex", alignItems: "center", gap: 12 }} aria-label="Current work">
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div style={{ color: "rgba(255,255,255,.38)", font: "600 8px ui-monospace,monospace", letterSpacing: ".12em", textTransform: "uppercase" }}>Current work · {String(activeChatTask.status || "running").replace(/_/g, " ")}</div>
                          <div style={{ marginTop: 5, color: "rgba(255,255,255,.86)", fontSize: 11.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{activeChatTask.objective}</div>
                          <div style={{ marginTop: 4, color: "rgba(255,255,255,.36)", fontSize: 9.5 }}>{activeChatSatisfied}/{activeChatRequirementStates.length || 1} requirements satisfied</div>
                        </div>
                        <button
                          type="button"
                          onClick={() => {
                            setDesktopSurface("visualizer");
                            setShowVisualizer(true);
                          }}
                          style={{ flex: "0 0 auto", border: "1px solid rgba(255,255,255,.15)", background: "#111", color: "#fff", borderRadius: 3, minHeight: 30, padding: "0 10px", cursor: "pointer", fontSize: 9.5 }}
                        >
                          Open in Visualizer
                        </button>
                      </section>
                    ) : null}
                    <AnimatePresence initial={false}>
                      {timeline.map((t) =>
                        t.kind === "message" ? (
                          <ChatBubble
                            key={`msg-${t.id}`}
                            msg={t.msg}
                            streaming={streaming}
                            typewriter={t.msg.role === "assistant" && !t.msg.skipTypewriter}
                            contextWindow={Number(providerInfo?.context_window || 0) || 32768}
                            providerLabel={providerInfo?.provider}
                            modelLabel={providerInfo?.model}
                            onQuickReply={(text) => {
                              try {
                                stopTts();
                              } catch {
                                // ignore
                              }
                              sendText(text);
                            }}
                          />
                        ) : (
                          <ActivityCard
                            key={`act-${t.id}`}
                            item={t.item}
                            // Step list uses static marks when the hero strip owns the Echo spinner.
                            primarySpinner={!streaming}
                          />
                        )
                      )}
                    </AnimatePresence>
                    {pendingApproval?.has_pending && pendingApproval.action ? (
                      <div
                        style={{ width: "100%", padding: "2px 4px 4px", position: "relative", zIndex: 20 }}
                        data-testid="chat-pending-approval"
                        data-approval-id={String(pendingApproval.approval_id || pendingApproval.action.id || "")}
                      >
                        <OperationalStateCard
                          state={threadState}
                          approval={{
                            ...pendingApproval.action,
                            id: String(pendingApproval.approval_id || pendingApproval.action.id || ""),
                            status: String(pendingApproval.action.status || "pending"),
                            policy_flags: pendingApproval.policy_flags || pendingApproval.action.policy_flags,
                            session_permissions: pendingApproval.session_permissions || pendingApproval.action.session_permissions,
                          }}
                          busy={approvalDecisionBusy}
                          onDecision={decideApproval}
                          compact
                        />
                      </div>
                    ) : null}
                    {streaming && liveReplyDraft ? (
                      <div style={{ display: "flex", justifyContent: "flex-start", padding: "10px 4px 8px", width: "100%", minWidth: 0, boxSizing: "border-box" }}>
                        <div
                          className="chat-flat chat-line-assistant"
                          style={{
                            width: "100%",
                            maxWidth: "100%",
                            minWidth: 0,
                            fontSize: 15,
                            lineHeight: 1.65,
                            whiteSpace: "pre-wrap",
                            overflowWrap: "anywhere",
                            wordBreak: "break-word",
                          }}
                        >
                          {liveReplyDraft}
                          <span
                            style={{
                              display: "inline-block",
                              width: 8,
                              height: 15,
                              marginLeft: 3,
                              borderRadius: 1,
                              background: "rgba(255,255,255,0.75)",
                              animation: "pulse 0.8s infinite",
                              verticalAlign: "text-bottom",
                            }}
                          />
                        </div>
                      </div>
                    ) : null}
                    {/* Single Echo activity strip for the active Session stream only. */}
                    {streaming ? (
                      <LiveChatActivityBar
                        activity={agentActivity}
                        showSpinner
                        onStop={() => {
                          stopTts();
                          setVoicePhase("idle");
                          setVoiceNotice("Stopped by Ty.");
                          cancelSessionTurn(activeThreadId, true);
                        }}
                        onSteer={activeTaskRunIdsRef.current.get(activeThreadId) ? () => setShowSteerModal(true) : undefined}
                        onQueue={() => void queueFollowUp()}
                        status={buildLiveOperationalStatus({
                          phase: agentActivity.phase,
                          streaming: true,
                          label: agentActivity.label,
                          activeToolName: agentActivity.activeToolName,
                          thinkingText: agentActivity.thinkingText,
                          taskDescription: (() => {
                            const plan =
                              taskPlans.find((entry) => entry.plan.active) ||
                              [...taskPlans].reverse().find((entry) => entry.plan.tasks.length);
                            const step =
                              plan?.plan.tasks.find((t) =>
                                ["running", "retrying", "awaiting_confirmation"].includes(t.status)
                              ) || plan?.plan.tasks.find((t) => t.status === "pending");
                            return step?.description || "";
                          })(),
                          searchHint: (() => {
                            const thinking = [...activities]
                              .reverse()
                              .find((a) => a.kind === "thinking") as Extract<ActivityItem, { kind: "thinking" }> | undefined;
                            const searchStep = [...(thinking?.steps || [])]
                              .reverse()
                              .find((s) => s.type === "search" && s.status === "running");
                            return searchStep?.content || "";
                          })(),
                        })}
                      />
                    ) : null}
                  </div>
                  <div className="input-bar">
                    {/* Row 1: session strip stacked on input (same column width) + context + send */}
                    <div className="input-row">
                      <div className="composer-input-stack">
                        <div
                          className={"session-folder-strip" + (folderDropActive ? " is-drop-active" : "")}
                          aria-label="Session and Project folder attachment. Drop a local folder here to create or select its Project."
                          onDragEnter={(event) => { event.preventDefault(); setFolderDropActive(true); }}
                          onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "link"; setFolderDropActive(true); }}
                          onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setFolderDropActive(false); }}
                          onDrop={(event) => { event.preventDefault(); setFolderDropActive(false); const path = folderPathFromDrop(event); if (path) void attachFolder(path); else void attachFolder(); }}
                        >
                          <span style={{ whiteSpace: "nowrap" }}>
                            Session: <b style={{ color: "rgba(255,255,255,.8)" }}>{threads.find(t => t.id === activeThreadId)?.name || activeThreadId}</b>
                          </span>
                          {(() => {
                            const folderFull =
                              String(threadState?.workspace_root || threadState?.project_path || "").trim();
                            const folderName = folderFull
                              ? folderFull.replace(/[\\/]+$/, "").split(/[/\\]/).filter(Boolean).pop() || folderFull
                              : "";
                            const gitBranch = projects.find(project => project.id === activeProjectId)?.git_metadata?.is_repository
                              ? String(projects.find(project => project.id === activeProjectId)?.git_metadata?.branch || "repository")
                              : "";
                            return (
                              <button
                                type="button"
                                onClick={() => void attachFolder()}
                                title={
                                  folderFull
                                    ? folderFull
                                    : "Choose or drop a local folder; folders become Projects automatically"
                                }
                                style={{
                                  border: 0,
                                  background: "transparent",
                                  color: "inherit",
                                  padding: 0,
                                  font: "inherit",
                                  cursor: "pointer",
                                  textAlign: "left",
                                  whiteSpace: "nowrap",
                                }}
                              >
                                Folder:{" "}
                                <b style={{ color: "rgba(255,255,255,.8)" }}>
                                  {folderName || "drop folder to start Project"}
                                  {gitBranch ? ` · git:${gitBranch}` : ""}
                                </b>
                              </button>
                            );
                          })()}
                          {(threadState?.workspace_root || threadState?.project_path) && (
                            <button
                              type="button"
                              aria-label="Remove folder from this Session"
                              title="Remove folder from this Session"
                              onClick={async () => {
                                const response = await fetch(`${apiBase}/projects/deactivate?thread_id=${encodeURIComponent(activeThreadId)}`, { method: "POST" });
                                if (!response.ok) return;
                                const data = await response.json();
                                setActiveProjectId(""); setThreadState(data.thread_state || null);
                                setThreads(items => items.map(item => item.id === activeThreadId ? { ...item, projectId: "" } : item));
                              }}
                              style={{ width: 18, height: 18, border: 0, background: "transparent", color: "rgba(255,255,255,.65)", borderRadius: 2, cursor: "pointer", lineHeight: 1, flexShrink: 0 }}
                            >
                              ×
                            </button>
                          )}
                        </div>
                        <textarea
                          ref={textareaRef}
                          className="input-field"
                          value={input}
                          rows={1}
                          disabled={!activeThreadId}
                          onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => {
                            updateComposerInput(e.target.value);
                          }}
                          onKeyDown={(e: React.KeyboardEvent<HTMLTextAreaElement>) => {
                            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                              e.preventDefault();
                              void sendText();
                            }
                          }}
                          placeholder={activeThreadId ? "Ask Echo anything..." : "Create a Session with + to chat"}
                          aria-label="Ask Echo anything"
                        />
                      </div>
                      <div className="composer-trailing">
                        <ContextMeter messages={messages} contextWindow={providerInfo?.context_window || 0} />
                        <button
                          className="send-button"
                          onClick={() => void sendText()}
                          type="button"
                          disabled={!activeThreadId || !input.trim()}
                          title="Send"
                          aria-label="Send message"
                        >
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
                            <path d="M5 12L19 12M19 12L13 6M19 12L13 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        </button>
                      </div>
                    </div>
                    {/* Row 2: mic mon viz | Provider | Model */}
                    <div className="controls-row">
                      <div className="composer-primary-controls">
                        <div className="composer-tools-slot" role="group" aria-label="Input tools">
                        <button
                          className={`mic-button ${listening ? "active" : ""}`}
                          type="button"
                          title={listening ? "Stop microphone" : "Start microphone"}
                          aria-label={listening ? "Stop microphone" : "Start microphone"}
                          disabled={voicePhase === "transcribing" || voicePhase === "requesting_permission"}
                          onClick={() => listening ? void stop() : void start()}
                        >
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
                            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" fill="currentColor" />
                            <path d="M19 10v2a7 7 0 0 1-14 0v-2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                          </svg>
                        </button>
                        {(voicePhase !== "idle" || voiceNotice) ? (
                          <span
                            className="voice-transport-status"
                            data-state={voicePhase}
                            title={voiceNotice || voicePhase}
                            aria-live="polite"
                          >
                            <i style={{ transform: `scale(${1 + voiceInputLevel * 0.55})` }} />
                            {voicePhase === "requesting_permission"
                              ? "Mic access"
                              : voicePhase === "listening"
                              ? "Listening"
                              : voicePhase === "transcribing"
                              ? "Local transcript"
                              : voicePhase === "speaking"
                              ? "Speaking"
                              : voicePhase === "error"
                              ? "Voice setup"
                              : voiceNotice || "Voice ready"}
                          </span>
                        ) : null}
                        <button
                          className={`composer-square ${monitoring ? "active" : ""}`}
                          type="button"
                          title={monitoring ? "Stop screen monitor" : "Screen monitor"}
                          aria-label={monitoring ? "Stop screen monitor" : "Screen monitor"}
                          onClick={() =>
                            setMonitoring((v) => {
                              const n = !v;
                              if (n) refreshMonitor();
                              return n;
                            })
                          }
                        >
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
                            <rect x="2" y="4" width="20" height="12" rx="2" stroke="currentColor" strokeWidth="2" />
                            <path d="M12 16v4M8 20h8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                          </svg>
                        </button>
                        <button
                          className={`composer-square ${(desktopMode ? desktopSurface === "visualizer" : showVisualizer && !narrowLayout) ? "active" : ""}`}
                          type="button"
                          title={(desktopMode ? desktopSurface === "visualizer" : showVisualizer && !narrowLayout) ? "Return to Chat" : "Show visualizer"}
                          aria-label={(desktopMode ? desktopSurface === "visualizer" : showVisualizer && !narrowLayout) ? "Return to Chat" : "Show visualizer"}
                          onClick={() => {
                            if (!desktopMode) {
                              setShowVisualizer((v) => !v);
                              return;
                            }
                            if (mediaRouteActive) navigate("/app");
                            setLeftTab("chat");
                            setDesktopSurface((surface) => surface === "visualizer" ? "chat" : "visualizer");
                          }}
                        >
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                            <rect x="3.5" y="5" width="17" height="14" rx="2" />
                            <path d="M12 5v14" />
                            <path d="M3.5 12h8.5" />
                          </svg>
                        </button>
                        <button
                          className={`composer-square ${!speechEnabled ? "active" : ""}`}
                          type="button"
                          title={speechEnabled ? "Sound on · click to mute" : "Sound off · click to unmute"}
                          aria-label={speechEnabled ? "Mute sound" : "Unmute sound"}
                          onClick={() => setSpeechEnabled(!speechEnabled)}
                        >
                          {speechEnabled ? (
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                              <path d="M4.5 10.5h3l4-3.5v10l-4-3.5h-3z" />
                              <path d="M15.5 9.5a4 4 0 0 1 0 5" />
                              <path d="M17.5 7.5a7 7 0 0 1 0 9" />
                            </svg>
                          ) : (
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                              <path d="M4.5 10.5h3l4-3.5v10l-4-3.5h-3z" />
                              <path d="M16 9.5 20 14.5M20 9.5 16 14.5" />
                            </svg>
                          )}
                        </button>
                        </div>
                        <div className="control-slot provider-slot" data-label="Provider">
                        <div className="inline-switcher">
                          <select
                            className="provider-picker"
                            value={providerDraft.provider}
                            onChange={(e) => {
                              const p = e.target.value;
                              setProviderDraft((d) => ({
                                ...d,
                                provider: p,
                                model:
                                  p === "openai"
                                    ? openaiModelOptions[0]
                                    : p === "gemini"
                                      ? geminiModelOptions[0]
                                      : providerModels[0] || d.model,
                              }));
                            }}
                            disabled={switchingProvider || lmStudioOnly}
                            title="Model provider"
                            aria-label="Model provider"
                          >
                            {(providerInfo?.available_providers || fallbackProviders)
                              .filter((p) => !lmStudioOnly || p.id === "lmstudio")
                              .map((p) => (
                                <option key={p.id} value={p.id}>
                                  {p.name}
                                </option>
                              ))}
                          </select>
                        </div>
                        </div>
                        <div className="control-slot model-slot" data-label="Model">
                        <select
                          className="model-picker"
                          value={modelPickerValue}
                          onChange={(e) => {
                            if (!showModelPicker) return;
                            setProviderDraft((d) => ({ ...d, model: e.target.value }));
                          }}
                          disabled={switchingProvider || !showModelPicker}
                          title="Model"
                          aria-label="Model"
                        >
                          {modelPickerOptions.map((m) => (
                            <option key={m} value={m}>
                              {m}
                            </option>
                          ))}
                        </select>
                        </div>
                        <div className="control-slot effort-slot" data-label="Effort">
                        <select
                          className="model-picker"
                          value={reasoningEffort}
                          onChange={(e: any) => setReasoningEffort(e.target.value)}
                          title="Reasoning effort"
                          aria-label="Reasoning effort"
                        >
                          <option value="minimal">Minimal</option>
                          <option value="low">Low</option>
                          <option value="medium">Medium</option>
                          <option value="high">High</option>
                          <option value="extra_high">Extra High</option>
                          <option value="max">Max</option>
                          <option value="ultra">Ultra</option>
                        </select>
                      </div>
                      </div>
                      <div className="composer-mode-controls" role="group" aria-label="Thinking and voice controls">
                      <button
                        className={`composer-mode-button ${thinkingEnabled ? "active" : ""}`}
                        type="button"
                        title={thinkingEnabled ? "Thinking enabled" : "Thinking disabled"}
                        aria-label={thinkingEnabled ? "Disable thinking" : "Enable thinking"}
                        onClick={() => setThinkingEnabled(!thinkingEnabled)}
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/><circle cx="12" cy="12" r="3"/></svg>
                        <span className="composer-mode-label">Think</span>
                      </button>
                      <button
                        className={`composer-mode-button ${voiceReadAloud ? "active" : ""}`}
                        type="button"
                        title={voiceReadAloud ? "Read replies aloud ON" : "Read replies aloud OFF"}
                        aria-label="Read replies aloud"
                        onClick={() => {
                          const enabled = !voiceReadAloud;
                          setVoiceReadAloud(enabled);
                          if (!enabled) stopTts();
                        }}
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M4 10h3l4-3v10l-4-3H4zM15 9a4 4 0 0 1 0 6M18 6a8 8 0 0 1 0 12"/></svg>
                        <span className="composer-mode-label">Read</span>
                      </button>
                      <button
                        className={`composer-mode-button ${voiceConversationMode ? "active" : ""}`}
                        type="button"
                        title={voiceConversationMode ? "Voice conversation mode ON" : "Voice mode OFF"}
                        aria-label="Voice conversation mode"
                        onClick={() => {
                          const enabled = !voiceConversationMode;
                          setVoiceConversationMode(enabled);
                          if (enabled && !streaming && !listening && voicePhase !== "transcribing") void start();
                          if (!enabled) {
                            void voiceInputRef.current?.stop(false);
                            setListening(false);
                            stopTts();
                            setVoicePhase("idle");
                            setVoiceNotice("");
                          }
                        }}
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M5 10v4M9 7v10M13 4v16M17 7v10M21 10v4"/></svg>
                        <span className="composer-mode-label">Voice</span>
                      </button>
                      <button
                        className={`composer-mode-button ${wakeWordEnabled ? "active" : ""}`}
                        type="button"
                        title="Wake word is not active yet. It follows the local Voice foundation in Phase 6."
                        aria-label="Wake word unavailable"
                        disabled
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><circle cx="12" cy="12" r="3"/><path d="M12 2a10 10 0 0 1 10 10M12 22A10 10 0 0 1 2 12M5 5a10 10 0 0 1 14 14"/></svg>
                        <span className="composer-mode-label">Wake</span>
                      </button>
                      </div>
                    </div>
                  </div>
                </>
              )}

              {showSteerModal && (
                <div className="steer-backdrop" role="presentation">
                  <div className="steer-dialog" role="dialog" aria-modal="true" aria-labelledby="steer-dialog-title">
                    <div className="steer-dialog-copy">
                      <span>Active run</span>
                      <h3 id="steer-dialog-title">Guide Echo</h3>
                    </div>
                    <p>
                      Add a direction for Echo to use at the next safe boundary. Completed work stays intact.
                    </p>
                    <textarea
                      className="steer-input"
                      value={steerInput}
                      onChange={(e) => setSteerInput(e.target.value)}
                      placeholder="For example: focus on the Python files first"
                      rows={3}
                    />
                    <div className="steer-dialog-actions">
                      <button
                        className="steer-button"
                        type="button"
                        onClick={() => { setShowSteerModal(false); setSteerInput(""); }}
                      >
                        Cancel
                      </button>
                      <button
                        className="steer-button is-primary"
                        type="button"
                        disabled={!steerInput.trim() || steerSubmitting}
                        onClick={async () => {
                          if (!steerInput.trim() || !activeThreadId) return;
                          setSteerSubmitting(true);
                          try {
                            const res = await fetch(`${apiBase}/query/steer`, {
                              method: "POST",
                              headers: { "Content-Type": "application/json" },
                              body: JSON.stringify({
                                thread_id: activeThreadId,
                                instruction: steerInput.trim(),
                                task_run_id: activeTaskRunIdsRef.current.get(activeThreadId) || "",
                                client_request_id: activeRequestIdsRef.current.get(activeThreadId) || "",
                              }),
                            });
                            if (res.ok) {
                              dispatchActivity({ type: "steer", instruction: steerInput.trim() });
                              setShowSteerModal(false);
                              setSteerInput("");
                            }
                          } finally {
                            setSteerSubmitting(false);
                          }
                        }}
                      >
                        {steerSubmitting ? "Applying…" : "Apply direction"}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {studioOpen && (!desktopMode || desktopStudioHost) && createPortal(
                <div className="studio-backdrop">
                  <motion.div
                  className="studio-shell"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  role="dialog"
                  aria-modal="true"
                  aria-label="EchoSpeak Settings"
                >
                  <div className="studio-top">
                    <div className="studio-title">Settings</div>
                    <button
                      type="button"
                      className="studio-x"
                      onClick={closeStudio}
                      title="Close Settings"
                      aria-label="Close Settings"
                    >
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                        <path d="M18 6L6 18M6 6l12 12" />
                      </svg>
                    </button>
                  </div>

                  <div className="studio-nav" data-testid="studio-nav">
                    <div className="studio-nav-inner" ref={studioNavRef} role="tablist" aria-label="Settings sections">
                      {studioGroups.map((group) => {
                        const active = group.label === activeStudioGroup.label;
                        return (
                        <button
                          key={group.label}
                          type="button"
                          className={"studio-tab" + (active ? " active" : "")}
                          onClick={() => group.tabs[0] && setLeftTab(group.tabs[0].id)}
                          role="tab"
                          aria-selected={active}
                          tabIndex={active ? 0 : -1}
                          title={group.label}
                        >
                          <span className="studio-tab-icon" aria-hidden><SettingsGroupIcon name={group.label} /></span>
                          {group.label}
                        </button>
                        );
                      })}
                    </div>
                  </div>

                  <div className="studio-subnav" role="tablist" aria-label={`${activeStudioGroup.label} settings`}>
                      {activeStudioGroup.tabs.map((tab) => (
                        <button
                          key={tab.id}
                          type="button"
                          className={"studio-tab" + (leftTab === tab.id ? " active" : "")}
                          onClick={() => setLeftTab(tab.id)}
                          role="tab"
                          aria-selected={leftTab === tab.id}
                          data-studio-tab={tab.id}
                        >
                          {tab.label}
                        </button>
                      ))}
                  </div>

                  <div className="studio-body">
                    <div className="studio-column">
                      <div className="studio-hero">
                        <h2>{studioActiveTab?.label || "Settings"}</h2>
                        <span>{studioActiveTab?.group || "Settings"}</span>
                      </div>
                      <div style={{ flex: 1, minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column", width: "100%" }}>

              {/* Settings Overview */}
              {activeSettingsCatalogCategory ? (
                <SettingsCatalog
                  category={activeSettingsCatalogCategory}
                  cards={settingsCatalog}
                  loading={settingsCatalogLoading}
                  error={settingsCatalogError || (activeSettingsCatalogCategory === "connections" ? connectionError : "")}
                  onRefresh={() => void (
                    activeSettingsCatalogCategory === "connections"
                      ? Promise.all([refreshConnections(), refreshSettingsCatalog()])
                      : refreshSettingsCatalog()
                  )}
                  onAction={handleSettingsCatalogAction}
                  onCapabilityAction={activeSettingsCatalogCategory === "connections" ? (card, capability) => {
                    const providerId = card.id.replace(/^connection:/, "");
                    const provider = connectionCatalog.find((item: any) => String(item.id || "") === providerId);
                    const connection = provider?.connection;
                    const source = (connection?.capabilities || []).find((item: any) => String(item.id || "") === capability.id);
                    if (connection && source) void toggleConnectionCapability(connection, source);
                  } : undefined}
                  capabilityActionDisabled={activeSettingsCatalogCategory === "connections" ? (card, capability) => {
                    const providerId = card.id.replace(/^connection:/, "");
                    const provider = connectionCatalog.find((item: any) => String(item.id || "") === providerId);
                    const connection = provider?.connection;
                    const source = (connection?.capabilities || []).find((item: any) => String(item.id || "") === capability.id);
                    return !activeProjectId || !connection || !source || connectionBusyId === String(connection.id || providerId);
                  } : undefined}
                  detailAddon={activeSettingsCatalogCategory === "models" ? (card) => {
                    const providerId = card.id.replace(/^model:/, "");
                    const defaultModel = card.selected ? String(providerInfo?.model || "") : "";
                    const modelValue = catalogModelDrafts[providerId] ?? defaultModel;
                    const unchanged = card.selected && modelValue === String(providerInfo?.model || "");
                    return (
                      <div className="settings-model-control">
                        <label htmlFor={`settings-model-${providerId}`}>Exact model for this Session</label>
                        <div>
                          <input
                            id={`settings-model-${providerId}`}
                            type="text"
                            value={modelValue}
                            placeholder="Enter the exact model name"
                            onChange={(event) => setCatalogModelDrafts((current) => ({
                              ...current,
                              [providerId]: event.target.value,
                            }))}
                          />
                          <button
                            type="button"
                            className="settings-catalog-button"
                            disabled={!modelValue.trim() || unchanged || switchingProvider || lmStudioOnly}
                            onClick={() => void applyProviderSwitch({
                              provider: providerId,
                              model: modelValue.trim(),
                              base_url: String(providerDraft.base_url || ""),
                            }).then(() => void refreshSettingsCatalog())}
                          >
                            {switchingProvider ? "Applying..." : card.selected ? "Update Session" : "Use for this Session"}
                          </button>
                        </div>
                        {providerError ? <small>{providerError}</small> : null}
                      </div>
                    );
                  } : activeSettingsCatalogCategory === "voice_speech" ? (card) => {
                    const providerId = card.id.replace(/^voice:/, "");
                    const inputCapability = card.capabilities.find((capability) => capability.id === "voice.speech_to_text");
                    const outputCapability = card.capabilities.find((capability) => capability.id === "voice.text_to_speech");
                    const supportsInput = Boolean(inputCapability);
                    const supportsOutput = Boolean(outputCapability);
                    const isCloud = card.locality === "cloud";
                    return (
                      <div className="settings-model-control settings-voice-control">
                        <label>{isCloud ? "Cloud audio permission" : "Use this local provider"}</label>
                        <div>
                          {isCloud ? (
                            <button
                              type="button"
                              className={`settings-catalog-button${card.selected ? " is-quiet" : ""}`}
                              disabled={!card.connected || settingsSaving}
                              onClick={() => void setCloudVoiceOptIn(providerId, !card.selected)}
                            >
                              {card.selected ? "Keep audio on device" : "Opt in to cloud audio"}
                            </button>
                          ) : supportsInput ? (
                            <button
                              type="button"
                              className="settings-catalog-button"
                              disabled={!inputCapability?.available || card.locality !== "local" || settingsSaving}
                              onClick={() => void selectLocalVoiceProvider("speech_to_text", providerId)}
                            >
                              Use for dictation
                            </button>
                          ) : null}
                          {!isCloud && supportsOutput ? (
                            <button
                              type="button"
                              className="settings-catalog-button is-quiet"
                              disabled={!outputCapability?.available || card.locality !== "local" || settingsSaving}
                              onClick={() => void selectLocalVoiceProvider("text_to_speech", providerId)}
                            >
                              Use for playback
                            </button>
                          ) : null}
                        </div>
                        <small>
                          {isCloud
                            ? card.connected
                              ? "Opt-in records your data-path choice. Upload and usage-based execution remain disabled until this provider's governed adapter and approval boundary are ready."
                              : "Connect this provider in Advanced first. Echo will never upload microphone audio because credentials merely exist."
                            : "Microphone permission is requested only when you press the mic button. Local recordings are transcribed in memory and raw audio is not retained."}
                        </small>
                      </div>
                    );
                  } : activeSettingsCatalogCategory === "connections" ? (card) => {
                    const providerId = card.id.replace(/^connection:/, "");
                    const provider = connectionCatalog.find((item: any) => String(item.id || "") === providerId);
                    const connection = provider?.connection;
                    const busy = connectionBusyId === String(connection?.id || providerId);
                    const reconnectRequired = card.status === "reconnect_required" || connection?.authentication === "expired";
                    return (
                      <div className="settings-connection-control">
                        {!activeProjectId ? (
                          <small>Select a Project to connect or manage this capability. Opening Settings never creates a Project or Session.</small>
                        ) : connection ? (
                          <>
                            <div className="settings-connection-actions">
                              <button
                                type="button"
                                className="settings-catalog-button"
                                disabled={busy}
                                onClick={() => void runConnectionAction(provider, reconnectRequired ? "reconnect" : "probe")}
                              >
                                {reconnectRequired ? "Reconnect" : "Check connection"}
                              </button>
                              <button
                                type="button"
                                className="settings-catalog-button is-quiet"
                                disabled={busy}
                                onClick={() => void runConnectionAction(provider, connection.enabled ? "disable" : "reconnect")}
                              >
                                {connection.enabled ? "Disable" : "Enable"}
                              </button>
                              <button
                                type="button"
                                className="settings-catalog-button is-quiet"
                                disabled={busy}
                                onClick={() => {
                                  if (window.confirm(`Disconnect ${card.name}? Echo will lose these capabilities.`)) {
                                    void runConnectionAction(provider, "disconnect");
                                  }
                                }}
                              >
                                Disconnect
                              </button>
                            </div>
                          </>
                        ) : (
                          <small>Connect this provider to choose its Project-scoped capabilities. Unsupported OAuth adapters remain unavailable rather than creating a placeholder connection.</small>
                        )}
                      </div>
                    );
                  } : undefined}
                />
              ) : null}

              {leftTab === "overview" && (
                <div className="research-scroll">
                  <div className="settings-general-grid">
                    <section className="settings-general-card">
                      <div>
                        <div className="research-title">Desktop workspace</div>
                        <div className="research-snippet">Choose how the main EchoSpeak window is arranged.</div>
                      </div>
                      <label className="settings-general-row">
                        <span>
                          <strong>Show sidebar</strong>
                          <small>Keep Chats, Projects, and Settings visible.</small>
                        </span>
                        <input type="checkbox" checked={showSidebar} onChange={(event) => setShowSidebar(event.target.checked)} />
                      </label>
                      <label className="settings-general-row">
                        <span>
                          <strong>Compact sidebar</strong>
                          <small>Use a narrower navigation rail.</small>
                        </span>
                        <input type="checkbox" checked={sidebarCollapsed} onChange={(event) => setSidebarCollapsed(event.target.checked)} />
                      </label>
                    </section>

                    <section className="settings-general-card">
                      <div>
                        <div className="research-title">Conversation</div>
                        <div className="research-snippet">Chat stays conversational; durable work is projected in Visualizer.</div>
                      </div>
                      <div className="settings-general-row">
                        <span>
                          <strong>New conversation</strong>
                          <small>Create a chat only when you explicitly ask for one.</small>
                        </span>
                        <button className="icon-button" type="button" onClick={() => void createNewThread()}>New Chat</button>
                      </div>
                      <div className="settings-general-row">
                        <span>
                          <strong>Visualizer</strong>
                          <small>Open the live projection of Echo&apos;s current work.</small>
                        </span>
                        <button
                          className="icon-button"
                          type="button"
                          onClick={() => {
                            closeStudio();
                            if (desktopMode) setDesktopSurface("visualizer");
                          }}
                        >
                          Open
                        </button>
                      </div>
                    </section>

                    <section className="settings-general-card settings-general-card-wide">
                      <div>
                        <div className="research-title">Local service</div>
                        <div className="research-snippet">Provider, capability, voice, and privacy controls live in the dedicated sections at left.</div>
                      </div>
                      <div className="settings-general-row">
                        <span>
                          <strong>Status</strong>
                          <small>{studioOverviewLoading ? "Refreshing local service state." : "Local service state is available on demand."}</small>
                        </span>
                        <button className="icon-button" type="button" onClick={refreshStudioOverview} disabled={studioOverviewLoading}>
                          {studioOverviewLoading ? "Refreshing..." : "Refresh"}
                        </button>
                      </div>
                    </section>
                  </div>
                </div>
              )}

              {/* Skills */}
              {false && leftTab === "skills" && (
                <div className="research-scroll">
                  <div style={{ display: "none", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 12 }}>
                    <div style={{ color: colors.textDim, fontSize: 12 }}>Visibility is descriptive; only reviewed, enabled registry entries can execute.</div>
                    <button className="icon-button" type="button" onClick={refreshStudioOverview} disabled={studioOverviewLoading}>Refresh</button>
                  </div>
                  {(studioOverview?.skills || []).map((skill: any) => (
                    <div key={skill.id} className="research-card">
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
                        <div>
                          <div className="research-title">{skill.name}</div>
                          <div className="research-snippet">{skill.origin} · v{skill.version} · {skill.manifest_status}</div>
                        </div>
                        <span style={{ fontSize: 10, border: `1px solid ${colors.line}`, borderRadius: 999, padding: "3px 8px", textTransform: "uppercase" }}>{skill.status}</span>
                      </div>
                      <div style={{ fontSize: 11, color: colors.textDim, marginTop: 10 }}>Required tools: {skill.required_tools?.length ? skill.required_tools.join(", ") : "none"}</div>
                      <div style={{ fontSize: 11, color: colors.textDim, marginTop: 4 }}>Models: {skill.required_models?.length ? skill.required_models.join(", ") : "none"}</div>
                      {skill.reasons?.length ? <div style={{ fontSize: 11, marginTop: 8 }}>Blocked reason: {skill.reasons.join(", ")}</div> : null}
                    </div>
                  ))}
                  <div className="research-card">
                    <div className="research-title">Skill proposals</div>
                    {(studioOverview?.skill_proposals || []).map((proposal: any) => (
                      <div key={proposal.id} style={{ borderTop: `1px solid ${colors.line}`, padding: "10px 0" }}>
                        <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}><span>{proposal.name}</span><span style={{ color: colors.textDim, fontSize: 10 }}>{proposal.status}</span></div>
                        <div style={{ color: colors.textDim, fontSize: 10, marginTop: 4 }}>{proposal.registration_approval_id ? `Approval ${proposal.registration_approval_id}` : "Awaiting registration approval"}</div>
                      </div>
                    ))}
                    {!(studioOverview?.skill_proposals || []).length ? <div className="research-snippet" style={{ marginTop: 8 }}>No proposed skills.</div> : null}
                  </div>
                </div>
              )}

              {/* Memory Tab */}
              {leftTab === "memory" && (
                <>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={async () => {
                        await refreshMemory();
                        await refreshMemoryDoctor();
                      }}
                      type="button"
                    >
                      Refresh
                    </button>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={async () => {
                        try {
                          const res = await fetch(`${apiBase}/memory/compact?thread_id=${encodeURIComponent(activeThreadId)}&project_id=${encodeURIComponent(activeProjectId)}`, { method: "POST" });
                          if (res.ok) {
                            await refreshMemory();
                            await refreshMemoryDoctor();
                          }
                        } catch (e) {
                          console.error("Compact memory error:", e);
                        }
                      }}
                      disabled={!memoryCount}
                      type="button"
                    >
                      Compact
                    </button>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={clearAllMemory}
                      disabled={!memoryCount}
                      type="button"
                    >
                      Clear All
                    </button>
                    <select
                      className="input-field"
                      style={{ height: 32, padding: "0 10px", fontSize: 13, width: 120 }}
                      value={memoryFilterType}
                      onChange={(e) => setMemoryFilterType(e.target.value)}
                    >
                      <option value="">All Types</option>
                      <option value="preference">Preference</option>
                      <option value="profile">Profile</option>
                      <option value="project">Project</option>
                      <option value="contacts">Contacts</option>
                      <option value="note">Note</option>
                    </select>
                  </div>
                  <div className="research-card" style={{ marginTop: 20, marginBottom: 12 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 10 }}>
                      <div>
                        <div className="research-title">Memory Doctor</div>
                        <div className="research-snippet">Read-only health check for duplicate, untyped, pinned, and profile memory.</div>
                      </div>
                      <button
                        className="icon-button"
                        style={{ height: 30, padding: "0 12px", fontSize: 12 }}
                        onClick={refreshMemoryDoctor}
                        type="button"
                      >
                        {memoryDoctorLoading ? "Checking..." : "Check"}
                      </button>
                    </div>
                    {memoryDoctor ? (
                      <>
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8, marginBottom: 10 }}>
                          {[
                            ["Status", memoryDoctor.ok ? "Healthy" : "Needs review"],
                            ["Scanned", String(memoryDoctor.scanned)],
                            ["Pinned", String(memoryDoctor.pinned_count)],
                            ["Profile facts", String(memoryDoctor.profile_fact_count)],
                            ["Untyped", String(memoryDoctor.missing_type_count)],
                            ["Duplicates", String(memoryDoctor.duplicate_groups?.length || 0)],
                          ].map(([label, value]) => (
                            <div key={label} style={{ padding: 10, borderRadius: 8, background: "rgba(255,255,255,0.04)", border: `1px solid ${colors.line}` }}>
                              <div style={{ fontSize: 10, color: colors.textDim, marginBottom: 3 }}>{label}</div>
                              <div style={{ fontSize: 13, fontWeight: 700, color: label === "Status" ? (memoryDoctor.ok ? "#22c55e" : "#f59e0b") : colors.text }}>{value}</div>
                            </div>
                          ))}
                        </div>
                        {Object.keys(memoryDoctor.type_counts || {}).length ? (
                          <div className="research-snippet" style={{ marginBottom: 8 }}>
                            Types: {Object.entries(memoryDoctor.type_counts).map(([k, v]) => `${k}:${v}`).join(", ")}
                          </div>
                        ) : null}
                        {(memoryDoctor.warnings || []).slice(0, 3).map((w, i) => (
                          <div key={`mw-${i}`} className="research-snippet" style={{ color: "#f59e0b" }}>{w}</div>
                        ))}
                        {(memoryDoctor.recommendations || []).slice(0, 3).map((r, i) => (
                          <div key={`mr-${i}`} className="research-snippet">{r}</div>
                        ))}
                      </>
                    ) : (
                      <div className="research-snippet">Run the doctor to see memory health for this session.</div>
                    )}
                  </div>
                  <div className="research-card" style={{ marginBottom: 12 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
                      <div>
                        <div className="research-title">Obsidian projection</div>
                        <div className="research-snippet">Optional, explicit sync. EchoSpeak memory remains authoritative.</div>
                      </div>
                      <button className="icon-button" type="button" onClick={refreshObsidianPlan} disabled={obsidianLoading || !activeProjectId || !activeThreadId}>
                        {obsidianLoading ? "Checking…" : "Check sync"}
                      </button>
                    </div>
                    {obsidianStatus ? <div className="research-snippet" style={{ marginTop: 8 }}>{obsidianStatus}</div> : null}
                    {obsidianPlan ? (
                      <>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
                          {(obsidianPlan.actions || []).map((action: any) => (
                            <span key={action.id} title={action.reason} style={{ fontSize: 10, padding: "4px 7px", borderRadius: 4, background: action.kind === "conflict" ? "rgba(245,158,11,.12)" : "rgba(255,255,255,.05)" }}>
                              {action.kind} · {action.note_path}
                            </span>
                          ))}
                        </div>
                        <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                          <button className="icon-button" type="button" disabled={obsidianLoading || !(obsidianPlan.actions || []).some((action: any) => ["export_new", "export_update"].includes(action.kind))} onClick={() => void applyObsidianPlan("export")}>Apply exports</button>
                          <button className="icon-button" type="button" disabled={obsidianLoading || !(obsidianPlan.actions || []).some((action: any) => ["import_new", "import_update"].includes(action.kind))} onClick={() => void applyObsidianPlan("import")}>Apply imports</button>
                        </div>
                        {(obsidianPlan.actions || []).some((action: any) => ["conflict", "note_deleted", "memory_deleted"].includes(action.kind)) ? (
                          <div className="research-snippet" style={{ color: "#f59e0b", marginTop: 8 }}>Conflicts and deletions require manual review and are never auto-applied.</div>
                        ) : null}
                      </>
                    ) : null}
                  </div>
                  {selectedMemoryIds.length > 0 && (
                    <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, padding: "8px 12px", background: colors.panel2, borderRadius: 8 }}>
                      <span style={{ fontSize: 12, color: colors.textDim }}>{selectedMemoryIds.length} selected</span>
                      <button
                        className="icon-button"
                        style={{ height: 28, padding: "0 10px", fontSize: 12 }}
                        type="button"
                        onClick={async () => {
                          await fetch(`${apiBase}/memory/delete`, {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ ids: selectedMemoryIds, thread_id: activeThreadId, project_id: activeProjectId }),
                          });
                          setSelectedMemoryIds([]);
                          refreshMemory();
                        }}
                      >
                        Delete Selected
                      </button>
                      <select
                        className="input-field"
                        style={{ height: 28, padding: "0 8px", fontSize: 12, width: 100 }}
                        value=""
                        onChange={async (e) => {
                          const newType = e.target.value;
                          if (newType) {
                            for (const id of selectedMemoryIds) {
                              await fetch(`${apiBase}/memory/update`, {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ id, thread_id: activeThreadId, project_id: activeProjectId, memory_type: newType }),
                              });
                            }
                            setSelectedMemoryIds([]);
                            refreshMemory();
                          }
                        }}
                      >
                        <option value="">Set Type</option>
                        <option value="preference">Preference</option>
                        <option value="profile">Profile</option>
                        <option value="project">Project</option>
                        <option value="note">Note</option>
                      </select>
                      <button
                        className="icon-button"
                        style={{ height: 28, padding: "0 10px", fontSize: 12 }}
                        type="button"
                        onClick={() => setSelectedMemoryIds([])}
                      >
                        Deselect
                      </button>
                    </div>
                  )}
                  <div className="research-scroll">
                    {memoryError ? (
                      <div className="research-card" style={{ borderColor: "rgba(248,113,113,0.45)" }}>
                        <div className="research-title">Memory unavailable</div>
                        <div className="research-snippet">The authoritative memory request failed. Existing records were not replaced by an empty list.</div>
                        <div className="research-snippet" style={{ color: "#fca5a5" }}>{memoryError}</div>
                      </div>
                    ) : memoryLoading ? (
                      <div className="research-card">
                        <div className="research-snippet">Loading memory…</div>
                      </div>
                    ) : memoryItems.length && !memoryItems.some((m) => !memoryFilterType || m.memory_type === memoryFilterType) ? (
                      <div className="research-card">
                        <div className="research-title">Memories hidden by filter</div>
                        <div className="research-snippet">{memoryItems.length} active memories exist, but none match “{memoryFilterType}”.</div>
                      </div>
                    ) : memoryItems.length ? (
                      memoryItems
                        .filter((m) => !memoryFilterType || m.memory_type === memoryFilterType)
                        .map((m) => {
                          const ts = (m.timestamp || String(m.metadata?.timestamp || "")).trim();
                          const preview = (m.text || "").trim();
                          const pinned = Boolean(m.pinned);
                          const memoryType = String(m.memory_type || "").trim();
                          const isEditing = editingMemoryId === m.id;
                          const isSelected = selectedMemoryIds.includes(m.id);
                          const indexState = String(m.index_state || m.metadata?.index_state || "pending");
                          const scope = String(m.scope || m.metadata?.scope || "account");
                          const projectLabel = String(m.project_id || m.project_path || m.metadata?.project_id || m.metadata?.project_path || "").trim();
                          const confidence = m.confidence ?? m.metadata?.confidence;
                          const sourceLabel = String(m.source || m.metadata?.source || "curated");
                          return (
                            <div key={m.id} className="research-card" style={{ border: isSelected ? `1px solid ${colors.accent}` : undefined }}>
                              <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center", marginBottom: 10 }}>
                                <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0, flex: 1 }}>
                                  <input
                                    type="checkbox"
                                    checked={isSelected}
                                    onChange={(e) => {
                                      if (e.target.checked) {
                                        setSelectedMemoryIds([...selectedMemoryIds, m.id]);
                                      } else {
                                        setSelectedMemoryIds(selectedMemoryIds.filter((id) => id !== m.id));
                                      }
                                    }}
                                    style={{ width: 16, height: 16 }}
                                  />
                                  <div style={{ minWidth: 0 }}>
                                    <div style={{ fontSize: 12, color: colors.textDim, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                      {ts ? ts : "(no timestamp)"} · {sourceLabel}
                                    </div>
                                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
                                      <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 999, border: `1px solid ${colors.line}`, color: colors.textDim }}>
                                        scope:{scope}
                                      </span>
                                      {projectLabel ? (
                                        <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 999, border: `1px solid ${colors.line}`, color: colors.textDim }}>
                                          project:{projectLabel.length > 24 ? `${projectLabel.slice(0, 24)}…` : projectLabel}
                                        </span>
                                      ) : null}
                                      {confidence != null && confidence !== "" ? (
                                        <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 999, border: `1px solid ${colors.line}`, color: colors.textDim }}>
                                          conf:{String(confidence)}
                                        </span>
                                      ) : null}
                                      <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 999, border: `1px solid ${colors.line}`, color: colors.textDim }}>
                                        index:{indexState}
                                      </span>
                                    </div>
                                  </div>
                                </div>
                                <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                                  <select
                                    className="input-field"
                                    style={{ height: 28, padding: "0 8px", fontSize: 12, width: 90 }}
                                    value={memoryType}
                                    onChange={async (e) => {
                                      const newType = e.target.value;
                                      await fetch(`${apiBase}/memory/update`, {
                                        method: "POST",
                                        headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify({ id: m.id, thread_id: activeThreadId, project_id: activeProjectId, memory_type: newType }),
                                      });
                                      refreshMemory();
                                    }}
                                  >
                                    <option value="">No Type</option>
                                    <option value="preference">Preference</option>
                                    <option value="profile">Profile</option>
                                    <option value="project">Project</option>
                                    <option value="contacts">Contacts</option>
                                    <option value="note">Note</option>
                                  </select>
                                  <button
                                    className="icon-button"
                                    style={{ height: 28, padding: "0 10px", fontSize: 12 }}
                                    type="button"
                                    onClick={() => {
                                      setEditingMemoryId(isEditing ? null : m.id);
                                      setEditingMemoryText(preview);
                                    }}
                                  >
                                    {isEditing ? "Cancel" : "Edit"}
                                  </button>
                                  <button
                                    className="icon-button"
                                    style={{ height: 28, padding: "0 10px", fontSize: 12 }}
                                    type="button"
                                    onClick={() => togglePinMemoryItem(m)}
                                    title={pinned ? "Unpin" : "Pin"}
                                  >
                                    {pinned ? "📌" : "Pin"}
                                  </button>
                                  <button
                                    className="icon-button"
                                    style={{ height: 28, padding: "0 10px", fontSize: 12 }}
                                    type="button"
                                    onClick={() => deleteMemoryItem(m.id)}
                                  >
                                    Delete
                                  </button>
                                </div>
                              </div>
                              {isEditing ? (
                                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                                  <textarea
                                    className="input-field"
                                    style={{ width: "100%", minHeight: 80, padding: 10, fontSize: 13, resize: "vertical" }}
                                    value={editingMemoryText}
                                    onChange={(e) => setEditingMemoryText(e.target.value)}
                                  />
                                  <button
                                    className="icon-button"
                                    style={{ height: 32, padding: "0 14px", fontSize: 13, alignSelf: "flex-end" }}
                                    type="button"
                                    onClick={async () => {
                                      await fetch(`${apiBase}/memory/update`, {
                                        method: "POST",
                                        headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify({ id: m.id, thread_id: activeThreadId, project_id: activeProjectId, text: editingMemoryText }),
                                      });
                                      setEditingMemoryId(null);
                                      refreshMemory();
                                    }}
                                  >
                                    Save
                                  </button>
                                </div>
                              ) : (
                                <div className="research-snippet" style={{ whiteSpace: "pre-wrap" }}>{preview || "(empty)"}</div>
                              )}
                              <div className="research-snippet" style={{ marginTop: 8, fontSize: 10 }}>
                                {String(m.scope || "account")} memory · {indexState === "indexed" ? "search index ready" : indexState === "failed" ? "saved; semantic indexing failed" : indexState === "unavailable" ? "saved; semantic index unavailable" : `index ${indexState}`}
                                {m.source_execution_id ? ` · source Turn ${m.source_execution_id}` : ""}
                              </div>
                            </div>
                          );
                        })
                    ) : (
                      <div className="research-card">
                        <div className="research-snippet">No saved memories yet.</div>
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* Documents Tab */}
              {leftTab === "docs" && (
                <>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={refreshDocuments}
                      type="button"
                    >
                      Refresh
                    </button>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={() => docInputRef.current?.click()}
                      disabled={!docEnabled}
                      type="button"
                    >
                      Upload
                    </button>
                  </div>
                  <div className="research-scroll">
                    <div className="research-card">
                      {!docEnabled ? (
                        <div className="research-snippet">Document RAG is disabled. Set DOCUMENT_RAG_ENABLED=true to enable uploads.</div>
                      ) : null}
                      {docFile ? <div className="research-snippet">Selected: {docFile.name}</div> : null}
                      {docError ? <div className="research-snippet">Error: {docError}</div> : null}
                    </div>
                    {docSources.length ? (
                      <div className="research-card">
                        <div style={{ fontSize: 14, color: colors.textDim, marginBottom: 8, fontWeight: 500 }}>Sources used in last response</div>
                        {docSources.map((s) => (
                          <div key={`${s.id}-${s.chunk ?? ""}`} className="research-snippet">
                            {s.filename || s.source || s.id}
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {docLoading ? (
                      <div className="research-card">
                        <div className="research-snippet">Loading documents…</div>
                      </div>
                    ) : docItems.length ? (
                      docItems.map((doc) => (
                        <div key={doc.id} className="research-card">
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginBottom: 8 }}>
                            <div style={{ fontSize: 15, fontWeight: 600 }}>{doc.filename}</div>
                            <button
                              className="icon-button"
                              style={{ height: 32, padding: "0 14px", fontSize: 14 }}
                              type="button"
                              onClick={() => deleteDocument(doc.id)}
                            >
                              Delete
                            </button>
                          </div>
                          <div className="research-snippet">Chunks: {doc.chunks}</div>
                          {doc.timestamp ? <div className="research-snippet">{doc.timestamp}</div> : null}
                        </div>
                      ))
                    ) : (
                      <div className="research-card">
                        <div className="research-snippet">No documents uploaded yet.</div>
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* Settings Tab */}
              {leftTab === "advanced_settings" && (
                <>
                  <div className="research-scroll">
                    <div className="research-card">
                      <div className="research-title">Advanced Runtime Configuration</div>
                      <div className="research-snippet" style={{ marginBottom: 12 }}>
                        Technical compatibility controls live here while their remaining runtime consumers are audited. Credentials, paths, endpoints, commands, and raw configuration are intentionally kept off the main Settings catalog.
                      </div>
                      {settingsError ? <div className="research-snippet">Error: {settingsError}</div> : null}
                      {settingsLoading ? <div className="research-snippet">Loading settings…</div> : null}
                      {!settingsLoading && (settingsErrors.length || settingsWarnings.length) ? (
                        <div className="research-card" style={{ background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", border: "1px solid rgba(255,255,255,0.1)", marginTop: 12, backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", borderRadius: 12, padding: 16, boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)" }}>
                          <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>Configuration checks</div>
                          {settingsErrors.length ? (
                            <div style={{ marginBottom: 10 }}>
                              <div style={{ fontSize: 13, fontWeight: 700, color: colors.danger, marginBottom: 6 }}>Errors (must fix)</div>
                              {settingsErrors.map((i, idx) => (
                                <div key={`${i.key}-${idx}`} className="research-snippet" style={{ color: colors.danger }}>
                                  {i.message}
                                </div>
                              ))}
                            </div>
                          ) : null}
                          {settingsWarnings.length ? (
                            <div>
                              <div style={{ fontSize: 13, fontWeight: 700, color: "#f59e0b", marginBottom: 6 }}>Warnings</div>
                              {settingsWarnings.map((i, idx) => (
                                <div key={`${i.key}-${idx}`} className="research-snippet">
                                  {i.message}
                                </div>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                      {!settingsLoading && runtimeSettings ? (
                        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                          {/* Current Provider Status */}
                          <div className="settings-section" style={{ background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)" }}>
                            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                              <span style={{ fontSize: 13, fontWeight: 600 }}>Current Provider</span>
                              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                                <span className={`switcher-dot ${backendOnline === true ? "online" : backendOnline === false ? "offline" : ""}`} />
                                <span style={{ fontSize: 14, fontWeight: 500 }}>
                                  {providerInfo?.available_providers?.find(p => p.id === providerDraft.provider)?.name || providerDraft.provider || "Unknown"}
                                </span>
                              </span>
                            </div>
                            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                              <select
                                className="input-field"
                                value={providerDraft.provider}
                                onChange={(e) => {
                                  const p = e.target.value;
                                  setProviderDraft(d => ({ ...d, provider: p, model: p === "openai" ? openaiModelOptions[0] : p === "gemini" ? geminiModelOptions[0] : (providerModels[0] || d.model) }));
                                }}
                                disabled={switchingProvider || lmStudioOnly}
                                style={{ flex: 1, padding: "8px 12px", fontSize: 14 }}
                              >
                                {(providerInfo?.available_providers || fallbackProviders)
                                  .filter(p => !lmStudioOnly || p.id === "lmstudio")
                                  .map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                              </select>
                              {(providerDraft.provider === "openai" || providerDraft.provider === "gemini" || providerModels.length > 0) && (
                                <select
                                  className="input-field"
                                  value={providerDraft.model}
                                  onChange={(e) => setProviderDraft(d => ({ ...d, model: e.target.value }))}
                                  disabled={switchingProvider}
                                  style={{ flex: 1, padding: "8px 12px", fontSize: 13 }}
                                >
                                  {(providerDraft.provider === "openai" ? openaiModelOptions : providerDraft.provider === "gemini" ? geminiModelOptions : providerModels).map(m => (
                                    <option key={m} value={m}>{m}</option>
                                  ))}
                                </select>
                              )}
                            </div>
                            {switchingProvider ? (
                              <div className="research-snippet" style={{ marginTop: 6, color: colors.accent }}>Switching provider...</div>
                            ) : null}
                          </div>

                          {/* ----------------- APIs & PROVIDERS ----------------- */}
                          <div style={{ marginTop: 32, marginBottom: 16, paddingBottom: 8, borderBottom: `1px solid rgba(255,255,255,0.1)` }}>
                            <div style={{ fontSize: 18, fontWeight: 700, color: colors.text }}>APIs & AI Providers</div>
                            <div style={{ fontSize: 13, color: colors.textDim }}>Set API keys for remote LLMs, local runners, embeddings, and voice models.</div>
                          </div>

                          {/* Cloud Providers Section */}
                          <div className="settings-section" style={{
                            background: "rgba(255, 255, 255, 0.02)",
                            border: "1px solid rgba(255, 255, 255, 0.08)",
                            borderRadius: "12px",
                            padding: "20px",
                            marginBottom: "20px"
                          }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }}>Cloud Providers</div>
                            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                              {/* OpenAI */}
                              <div style={{ padding: 12, background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)" }}>
                                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                                  <label style={{ fontSize: 13, fontWeight: 500 }}>OpenAI</label>
                                  {settingsTests.openai?.ok && settingsTestedKeys.openai === String(settingsDraft?.openai?.api_key ?? "") && (
                                    <span style={{ fontSize: 11, color: "#22c55e" }}>✓ Connected</span>
                                  )}
                                </div>
                                <div style={{ display: "flex", gap: 8 }}>
                                  <input
                                    type="password"
                                    className="input-field"
                                    value={String(settingsDraft?.openai?.api_key ?? "")}
                                    placeholder="sk-..."
                                    onChange={(e) => {
                                      const next = e.target.value;
                                      setSettingsDraft((d) => ({ ...d, openai: { ...(d.openai || {}), api_key: next } }));
                                      setSettingsTests((m) => ({ ...m, openai: null }));
                                      setSettingsTestedKeys((m) => {
                                        const copy = { ...m };
                                        delete (copy as any).openai;
                                        return copy;
                                      });
                                    }}
                                    style={{ flex: 1, padding: "8px 12px", fontSize: 13 }}
                                  />
                                  <button
                                    className="icon-button"
                                    style={{ padding: "0 12px", fontSize: 12 }}
                                    type="button"
                                    onClick={() => runSettingsTest("openai")}
                                    disabled={Boolean(settingsTesting.openai)}
                                  >
                                    {settingsTesting.openai ? "..." : "Test"}
                                  </button>
                                </div>
                                {settingsTests.openai && !settingsTests.openai.ok && (
                                  <div className="research-snippet" style={{ marginTop: 4, color: colors.danger, fontSize: 11 }}>
                                    {settingsTests.openai.message}
                                  </div>
                                )}
                              </div>

                              {/* Gemini */}
                              <div style={{ padding: 12, background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", boxShadow: providerDraft.provider === "gemini" ? "0 4px 16px -4px rgba(45,108,255,0.2), inset 0 1px 0 rgba(255,255,255,0.1), 0 0 0 1px rgba(140,180,255,0.4)" : "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)" }}>
                                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                                  <label style={{ fontSize: 13, fontWeight: 500 }}>Google Gemini</label>
                                  {settingsTests.gemini?.ok && settingsTestedKeys.gemini === String(settingsDraft?.gemini?.api_key ?? "") && (
                                    <span style={{ fontSize: 11, color: "#22c55e" }}>✓ Connected</span>
                                  )}
                                </div>
                                <div style={{ display: "flex", gap: 8 }}>
                                  <input
                                    type="password"
                                    className="input-field"
                                    value={String(settingsDraft?.gemini?.api_key ?? "")}
                                    placeholder="AIza..."
                                    onChange={(e) => {
                                      const next = e.target.value;
                                      setSettingsDraft((d) => ({ ...d, gemini: { ...(d.gemini || {}), api_key: next } }));
                                      setSettingsTests((m) => ({ ...m, gemini: null }));
                                      setSettingsTestedKeys((m) => {
                                        const copy = { ...m };
                                        delete (copy as any).gemini;
                                        return copy;
                                      });
                                    }}
                                    style={{ flex: 1, padding: "8px 12px", fontSize: 13 }}
                                  />
                                  <button
                                    className="icon-button"
                                    style={{ padding: "0 12px", fontSize: 12 }}
                                    type="button"
                                    onClick={() => runSettingsTest("gemini")}
                                    disabled={Boolean(settingsTesting.gemini)}
                                  >
                                    {settingsTesting.gemini ? "..." : "Test"}
                                  </button>
                                </div>
                                {settingsTests.gemini && !settingsTests.gemini.ok && (
                                  <div className="research-snippet" style={{ marginTop: 4, color: colors.danger, fontSize: 11 }}>
                                    {settingsTests.gemini.message}
                                  </div>
                                )}
                              </div>
                            </div>
                            <div className="research-snippet" style={{ marginTop: 8, fontSize: 11 }}>
                              Keys are saved securely and redacted on reload. Test to verify connectivity.
                            </div>
                          </div>

                          {/* Local Models Section */}
                          <div className="settings-section" style={{
                            background: "rgba(255, 255, 255, 0.02)",
                            border: "1px solid rgba(255, 255, 255, 0.08)",
                            borderRadius: "12px",
                            padding: "20px",
                            marginBottom: "20px"
                          }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }}>Local Models</div>
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px" }}>
                              <Toggle
                                label="LM Studio Only"
                                checked={Boolean(settingsDraft.lm_studio_only)}
                                onChange={(v) => updateDraft("lm_studio_only", v)}
                              />
                              <Toggle
                                label="Use Local Models"
                                checked={Boolean(settingsDraft.use_local_models)}
                                onChange={(v) => updateDraft("use_local_models", v)}
                              />
                              <Toggle
                                label="Ollama Tool-Calling Wrapper"
                                checked={Boolean(settingsDraft.use_tool_calling_llm)}
                                onChange={(v) => updateDraft("use_tool_calling_llm", v)}
                              />
                              <Toggle
                                label="Disable Native Tools (Use Fallback)"
                                checked={Boolean(settingsDraft.disable_native_tool_calling)}
                                onChange={(v) => updateDraft("disable_native_tool_calling", v)}
                              />
                            </div>
                            {settingsSavedAt ? (
                              <div className="research-snippet" style={{ marginTop: 10, fontSize: 11 }}>
                                Last saved: {new Date(settingsSavedAt).toLocaleString()}
                              </div>
                            ) : null}
                          </div>

                          <div className="settings-section" style={{
                            background: "rgba(255, 255, 255, 0.02)",
                            border: "1px solid rgba(255, 255, 255, 0.08)",
                            borderRadius: "12px",
                            padding: "20px",
                            marginBottom: "20px"
                          }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }}>System Actions (Safety Gates)</div>
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px" }}>
                              <Toggle
                                label="Enable System Actions"
                                checked={Boolean(settingsDraft.enable_system_actions)}
                                onChange={(v) => updateDraft("enable_system_actions", v)}
                              />
                              <Toggle
                                label="Allow Playwright"
                                checked={Boolean(settingsDraft.allow_playwright)}
                                onChange={(v) => updateDraft("allow_playwright", v)}
                              />
                              <Toggle
                                label="Allow Terminal Commands"
                                checked={Boolean(settingsDraft.allow_terminal_commands)}
                                onChange={(v) => updateDraft("allow_terminal_commands", v)}
                              />
                              <Toggle
                                label="Allow File Write"
                                checked={Boolean(settingsDraft.allow_file_write)}
                                onChange={(v) => updateDraft("allow_file_write", v)}
                              />
                              <Toggle
                                label="Allow Desktop Automation"
                                checked={Boolean(settingsDraft.allow_desktop_automation)}
                                onChange={(v) => updateDraft("allow_desktop_automation", v)}
                              />
                              <Toggle
                                label="Allow Open Application"
                                checked={Boolean(settingsDraft.allow_open_application)}
                                onChange={(v) => updateDraft("allow_open_application", v)}
                              />
                              <Toggle
                                label="Allow Open Chrome"
                                checked={Boolean(settingsDraft.allow_open_chrome)}
                                onChange={(v) => updateDraft("allow_open_chrome", v)}
                              />
                              <Toggle
                                label="Allow Self Modification"
                                checked={Boolean(settingsDraft.allow_self_modification)}
                                onChange={(v) => updateDraft("allow_self_modification", v)}
                              />
                            </div>
                          </div>

                          <div>
                            <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Terminal Execution Mode</label>
                            <select
                              className="input-field"
                              value={String(settingsDraft.terminal_execution_mode || "docker")}
                              onChange={(e) => updateDraft("terminal_execution_mode", e.target.value)}
                              style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                            >
                              <option value="docker">Docker sandbox (recommended)</option>
                              <option value="host">Host terminal (unsandboxed opt-in)</option>
                            </select>
                            <div className="research-snippet" style={{ marginTop: 5, fontSize: 11 }}>
                              Terminal is optional for ordinary file reads and approval-gated edits. Host mode runs commands directly on this machine.
                            </div>
                          </div>

                          <div>
                            <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Terminal Denylist (first token)</label>
                            <input
                              type="text"
                              className="input-field"
                              value={Array.isArray(settingsDraft.terminal_command_denylist) ? settingsDraft.terminal_command_denylist.join(",") : ""}
                              placeholder="rm,del,erase,rmdir,format,shutdown,regedit,diskpart,powershell,cmd"
                              onChange={(e) =>
                                updateDraft(
                                  "terminal_command_denylist",
                                  e.target.value
                                    .split(",")
                                    .map((x) => x.trim().toLowerCase())
                                    .filter(Boolean)
                                )
                              }
                              style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                            />
                          </div>

                          <div>
                            <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>File Tool Root</label>
                            <input
                              type="text"
                              className="input-field"
                              value={String(settingsDraft.file_tool_root || "")}
                              placeholder="/absolute/path/to/workspace"
                              onChange={(e) => updateDraft("file_tool_root", e.target.value)}
                              style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                            />
                          </div>

                          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                            <div style={{ flex: "1 1 180px" }}>
                              <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Terminal timeout (s)</label>
                              <input
                                type="number"
                                className="input-field"
                                value={Number(settingsDraft.terminal_command_timeout ?? 20)}
                                min={1}
                                onChange={(e) => updateDraft("terminal_command_timeout", Number(e.target.value || 0))}
                                style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                              />
                            </div>
                            <div style={{ flex: "1 1 220px" }}>
                              <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Terminal max output chars</label>
                              <input
                                type="number"
                                className="input-field"
                                value={Number(settingsDraft.terminal_max_output_chars ?? 8000)}
                                min={100}
                                onChange={(e) => updateDraft("terminal_max_output_chars", Number(e.target.value || 0))}
                                style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                              />
                            </div>
                          </div>

                          <div data-testid="open-app-allowlist">
                            <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>
                              Open application allowlist
                            </label>
                            <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 8, lineHeight: 1.45 }}>
                              Full names with spaces or hyphens are allowed. Press Enter, comma, or Add to commit an entry. Paste comma- or newline-separated lists. Non-allowlisted apps stay blocked.
                            </div>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8, minHeight: 28 }}>
                              {coerceAllowlistValue(settingsDraft.open_application_allowlist).map((entry) => (
                                <span
                                  key={entry}
                                  data-testid={`allowlist-chip-${entry.replace(/\s+/g, "-")}`}
                                  style={{
                                    display: "inline-flex",
                                    alignItems: "center",
                                    gap: 6,
                                    padding: "4px 8px",
                                    borderRadius: 999,
                                    border: `1px solid ${colors.line}`,
                                    background: "rgba(255,255,255,0.04)",
                                    fontSize: 12,
                                    color: colors.text,
                                    fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                                  }}
                                >
                                  {entry}
                                  <button
                                    type="button"
                                    className="icon-button"
                                    aria-label={`Remove ${entry}`}
                                    onClick={() =>
                                      updateDraft(
                                        "open_application_allowlist",
                                        removeAllowlistEntry(coerceAllowlistValue(settingsDraft.open_application_allowlist), entry)
                                      )
                                    }
                                    style={{ padding: "0 4px", height: 20, fontSize: 11, lineHeight: 1 }}
                                  >
                                    ×
                                  </button>
                                </span>
                              ))}
                              {!coerceAllowlistValue(settingsDraft.open_application_allowlist).length ? (
                                <span style={{ fontSize: 11, color: colors.textDim }}>No applications allowlisted yet.</span>
                              ) : null}
                            </div>
                            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                              <input
                                type="text"
                                className="input-field"
                                data-testid="allowlist-draft-input"
                                value={allowlistDraftText}
                                placeholder="e.g. visual studio code"
                                onChange={(e) => {
                                  const next = e.target.value;
                                  const committed = commitAllowlistDraft(
                                    coerceAllowlistValue(settingsDraft.open_application_allowlist),
                                    next
                                  );
                                  updateDraft("open_application_allowlist", committed.entries);
                                  setAllowlistDraftText(committed.draft);
                                }}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") {
                                    e.preventDefault();
                                    const committed = commitAllowlistDraft(
                                      coerceAllowlistValue(settingsDraft.open_application_allowlist),
                                      allowlistDraftText,
                                      { force: true }
                                    );
                                    updateDraft("open_application_allowlist", committed.entries);
                                    setAllowlistDraftText("");
                                  }
                                }}
                                onPaste={(e) => {
                                  const text = e.clipboardData?.getData("text") || "";
                                  if (/[\n,]/.test(text)) {
                                    e.preventDefault();
                                    const committed = commitAllowlistDraft(
                                      coerceAllowlistValue(settingsDraft.open_application_allowlist),
                                      `${allowlistDraftText}${text}`,
                                      { force: true }
                                    );
                                    updateDraft("open_application_allowlist", committed.entries);
                                    setAllowlistDraftText("");
                                  }
                                }}
                                style={{ flex: 1, padding: "10px 14px", fontSize: 14 }}
                              />
                              <button
                                type="button"
                                className="icon-button"
                                data-testid="allowlist-add-btn"
                                style={{ padding: "0 12px", height: 38, fontSize: 12 }}
                                onClick={() => {
                                  const committed = commitAllowlistDraft(
                                    coerceAllowlistValue(settingsDraft.open_application_allowlist),
                                    allowlistDraftText,
                                    { force: true }
                                  );
                                  updateDraft("open_application_allowlist", committed.entries);
                                  setAllowlistDraftText("");
                                }}
                              >
                                Add
                              </button>
                            </div>
                          </div>

                          {/* ----------------- BOTS & CONNECTORS ----------------- */}
                          <div style={{ marginTop: 32, marginBottom: 16, paddingBottom: 8, borderBottom: `1px solid rgba(255,255,255,0.1)` }}>
                            <div style={{ fontSize: 18, fontWeight: 700, color: colors.text }}>Platforms & Bots</div>
                            <div style={{ fontSize: 13, color: colors.textDim }}>Configure messaging platforms, bot channels, and communication surfaces in one place.</div>
                          </div>

                          {/* Email Configuration */}
                          <div className="settings-section" style={{ ...settingsSectionStyle, ...platformCardStyle }}>
                            <PlatformHeader icon="✉️" title="Email" subtitle="IMAP / SMTP automation channel" accent="#60a5fa" />
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px", marginBottom: 12 }}>
                              <Toggle
                                label="Allow Email Automation"
                                checked={Boolean(settingsDraft.allow_email)}
                                onChange={(v) => updateDraft("allow_email", v)}
                              />
                              <Toggle
                                label="Use TLS"
                                checked={Boolean(settingsDraft.email_use_tls ?? true)}
                                onChange={(v) => updateDraft("email_use_tls", v)}
                              />
                            </div>
                            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                              <div style={{ display: "flex", gap: 8 }}>
                                <div style={{ flex: 2 }}>
                                  <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>IMAP Host</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={String(settingsDraft.email_imap_host || "")}
                                    placeholder="imap.gmail.com"
                                    onChange={(e) => updateDraft("email_imap_host", e.target.value)}
                                    style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                  />
                                </div>
                                <div style={{ flex: 1 }}>
                                  <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>IMAP Port</label>
                                  <input
                                    type="number"
                                    className="input-field"
                                    value={Number(settingsDraft.email_imap_port || 993)}
                                    onChange={(e) => updateDraft("email_imap_port", parseInt(e.target.value) || 993)}
                                    style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                  />
                                </div>
                              </div>
                              <div style={{ display: "flex", gap: 8 }}>
                                <div style={{ flex: 2 }}>
                                  <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>SMTP Host</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={String(settingsDraft.email_smtp_host || "")}
                                    placeholder="smtp.gmail.com"
                                    onChange={(e) => updateDraft("email_smtp_host", e.target.value)}
                                    style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                  />
                                </div>
                                <div style={{ flex: 1 }}>
                                  <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>SMTP Port</label>
                                  <input
                                    type="number"
                                    className="input-field"
                                    value={Number(settingsDraft.email_smtp_port || 587)}
                                    onChange={(e) => updateDraft("email_smtp_port", parseInt(e.target.value) || 587)}
                                    style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                  />
                                </div>
                              </div>
                              <div style={{ display: "flex", gap: 8 }}>
                                <div style={{ flex: 1 }}>
                                  <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Email Username</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={String(settingsDraft.email_username || "")}
                                    placeholder="user@example.com"
                                    onChange={(e) => updateDraft("email_username", e.target.value)}
                                    style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                  />
                                </div>
                                <div style={{ flex: 1 }}>
                                  <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>App Password</label>
                                  <input
                                    type="password"
                                    className="input-field"
                                    value={String(settingsDraft.email_password || "")}
                                    placeholder="••••••••"
                                    onChange={(e) => updateDraft("email_password", e.target.value)}
                                    style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                  />
                                </div>
                              </div>
                            </div>
                          </div>

                          {/* Telegram Bot Configuration */}
                          <div className="settings-section" style={{ ...settingsSectionStyle, ...platformCardStyle }}>
                            <PlatformHeader icon="✈️" title="Telegram" subtitle="Direct bot control and notifications" accent="#38bdf8" />
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "12px 24px", marginBottom: 16 }}>
                              <Toggle
                                label="Enable Telegram Bot"
                                checked={Boolean(settingsDraft.allow_telegram_bot)}
                                onChange={(v) => updateDraft("allow_telegram_bot", v)}
                              />
                              <Toggle
                                label="Auto-Confirm Telegram Actions"
                                checked={Boolean(settingsDraft.telegram_auto_confirm ?? true)}
                                onChange={(v) => updateDraft("telegram_auto_confirm", v)}
                              />
                            </div>
                            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                              <div>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>Bot Token (from @BotFather)</label>
                                <input
                                  type="password"
                                  className="input-field"
                                  value={String(settingsDraft.telegram_bot_token || "")}
                                  placeholder="123456789:ABCdefGHIjklMNO..."
                                  onChange={(e) => updateDraft("telegram_bot_token", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>Allowed Users (comma separated @usernames)</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={Array.isArray(settingsDraft.telegram_allowed_users) ? settingsDraft.telegram_allowed_users.join(",") : ""}
                                  placeholder="@bob,@alice"
                                  onChange={(e) =>
                                    updateDraft(
                                      "telegram_allowed_users",
                                      e.target.value
                                        .split(",")
                                        .map((x) => x.trim().toLowerCase())
                                        .filter(Boolean)
                                    )
                                  }
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                            </div>
                          </div>

                          {/* Discord Bot Configuration */}
                          <div className="settings-section" style={{ ...settingsSectionStyle, ...platformCardStyle }}>
                            <PlatformHeader icon="🎮" title="Discord" subtitle="Role-based server access, webhook delivery, and trusted-user controls" accent="#818cf8" />
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "12px 24px", marginBottom: 16 }}>
                              <Toggle label="Enable Discord Bot" checked={Boolean(settingsDraft.allow_discord_bot)} onChange={(v) => updateDraft("allow_discord_bot", v)} />
                              <Toggle label="Allow Discord Webhook" checked={Boolean(settingsDraft.allow_discord_webhook)} onChange={(v) => updateDraft("allow_discord_webhook", v)} />
                              <Toggle label="Auto-Confirm Discord Actions" checked={Boolean(settingsDraft.discord_bot_auto_confirm ?? true)} onChange={(v) => updateDraft("discord_bot_auto_confirm", v)} />
                            </div>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
                              <div style={{ flex: "1 1 300px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>Bot Token</label>
                                <input
                                  type="password"
                                  className="input-field"
                                  value={String(settingsDraft.discord_bot_token || "")}
                                  placeholder="Bot token for EchoSpeak Discord bot"
                                  onChange={(e) => updateDraft("discord_bot_token", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 300px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>
                                  Allowed Server Roles (comma separated)
                                  <RequiredBadge issueKey="discord_bot_allowed_roles" />
                                </label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={Array.isArray(settingsDraft.discord_bot_allowed_roles) ? settingsDraft.discord_bot_allowed_roles.join(",") : String(settingsDraft.discord_bot_allowed_roles || "")}
                                  onChange={(e) =>
                                    updateDraft(
                                      "discord_bot_allowed_roles",
                                      e.target.value
                                        .split(",")
                                        .map((x) => x.trim())
                                        .filter(Boolean)
                                    )
                                  }
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 300px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>Allowed User IDs (optional fallback)</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={Array.isArray(settingsDraft.discord_bot_allowed_users) ? settingsDraft.discord_bot_allowed_users.join(",") : String(settingsDraft.discord_bot_allowed_users || "")}
                                  placeholder="Optional explicit user allowlist"
                                  onChange={(e) =>
                                    updateDraft(
                                      "discord_bot_allowed_users",
                                      e.target.value
                                        .split(",")
                                        .map((x) => x.trim())
                                        .filter(Boolean)
                                    )
                                  }
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 300px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>
                                  Owner User ID
                                  <RequiredBadge issueKey="discord_bot_owner_id" />
                                </label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft.discord_bot_owner_id || "")}
                                  placeholder="Your Discord user ID"
                                  onChange={(e) => updateDraft("discord_bot_owner_id", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14, borderColor: isError("discord_bot_owner_id") ? colors.danger : undefined }}
                                />
                              </div>
                              <div style={{ flex: "1 1 100%" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>Webhook URL</label>
                                <input
                                  type="password"
                                  className="input-field"
                                  value={String(settingsDraft.discord_webhook_url || "")}
                                  placeholder="https://discord.com/api/webhooks/..."
                                  onChange={(e) => updateDraft("discord_webhook_url", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 100%" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>Trusted User IDs (comma separated)</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={Array.isArray(settingsDraft.discord_bot_trusted_users) ? settingsDraft.discord_bot_trusted_users.join(",") : ""}
                                  placeholder="1234567890,0987654321"
                                  onChange={(e) =>
                                    updateDraft(
                                      "discord_bot_trusted_users",
                                      e.target.value
                                        .split(",")
                                        .map((x) => x.trim())
                                        .filter(Boolean)
                                    )
                                  }
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                            </div>
                            <div style={{ marginTop: 20, paddingTop: 16, borderTop: "1px solid rgba(255,255,255,0.08)" }}>
                              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                                <span style={{ fontSize: 14 }}>📋</span>
                                <span style={{ fontSize: 13, fontWeight: 600, color: colors.text }}>Changelog Announcements</span>
                                <span style={{ fontSize: 11, color: colors.textDim }}>(git push → Discord channel)</span>
                              </div>
                              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "12px 24px", marginBottom: 12 }}>
                                <Toggle label="Enable Changelog Posts" checked={Boolean(settingsDraft.discord_changelog_enabled ?? true)} onChange={(v) => updateDraft("discord_changelog_enabled", v)} />
                              </div>
                              <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
                                <div style={{ flex: "2 1 320px" }}>
                                  <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>Channel targets (comma separated, first match wins)</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={Array.isArray(settingsDraft.discord_changelog_channels) ? settingsDraft.discord_changelog_channels.join(",") : String(settingsDraft.discord_changelog_channels || "")}
                                    placeholder="updates,changes,changelog,dev-updates,announcements"
                                    onChange={(e) =>
                                      updateDraft(
                                        "discord_changelog_channels",
                                        e.target.value
                                          .split(",")
                                          .map((x) => x.trim())
                                          .filter(Boolean)
                                      )
                                    }
                                    style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                  />
                                </div>
                                <div style={{ flex: "1 1 240px" }}>
                                  <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>Server name or ID (blank = search all)</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={String(settingsDraft.discord_changelog_server || "")}
                                    placeholder="Leave blank to auto-detect"
                                    onChange={(e) => updateDraft("discord_changelog_server", e.target.value)}
                                    style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                  />
                                </div>
                              </div>
                              <div className="research-snippet" style={{ marginTop: 8, fontSize: 11, color: colors.textDim }}>
                                When new commits are pushed, EchoSpeak posts an update to the first matching Discord channel. Supports channel names, IDs, and fuzzy matching.
                              </div>
                            </div>
                          </div>

                          <div className="settings-section" style={{ ...settingsSectionStyle, ...platformCardStyle }}>
                            <PlatformHeader icon="🟢" title="WhatsApp" subtitle="External WhatsApp bridge / API endpoint" accent="#22c55e" />
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "12px 24px", marginBottom: 16 }}>
                              <Toggle label="Enable WhatsApp" checked={Boolean(settingsDraft.allow_whatsapp)} onChange={(v) => updateDraft("allow_whatsapp", v)} />
                            </div>
                            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                              <div style={{ flex: "2 1 320px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>WhatsApp API URL</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft.whatsapp_api_url || "")}
                                  placeholder="http://localhost:3001"
                                  onChange={(e) => updateDraft("whatsapp_api_url", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                            </div>
                          </div>

                          {/* Twitter/X Configuration */}
                          <div className="settings-section" style={{ ...settingsSectionStyle, ...platformCardStyle }}>
                            <PlatformHeader icon="🐦" title="Twitter / X" subtitle="Autonomous tweeting, changelog posts, and mention replies" accent="#1d9bf0" />
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "12px 24px", marginBottom: 16 }}>
                              <Toggle label="Enable Twitter" checked={Boolean(settingsDraft.allow_twitter)} onChange={(v) => updateDraft("allow_twitter", v)} />
                              <Toggle label="Autonomous Tweeting" checked={Boolean(settingsDraft.twitter_autonomous_enabled)} onChange={(v) => updateDraft("twitter_autonomous_enabled", v)} />
                              <Toggle label="Require Approval" checked={Boolean(settingsDraft.twitter_autonomous_require_approval ?? true)} onChange={(v) => updateDraft("twitter_autonomous_require_approval", v)} />
                              <Toggle label="Auto-Reply Mentions" checked={Boolean(settingsDraft.twitter_auto_reply_mentions)} onChange={(v) => updateDraft("twitter_auto_reply_mentions", v)} />
                            </div>
                            <div style={{ fontSize: 12, color: colors.textDim, marginBottom: 14, lineHeight: 1.5 }}>
                              Use the X app's OAuth 1.0a credentials from the bot account: paste <strong style={{ color: colors.text }}>Consumer Key</strong> into Client ID, <strong style={{ color: colors.text }}>Consumer Secret</strong> into Client Secret, and the OAuth 1.0a access token pair into the access token fields. You can leave Bot User ID blank and let EchoSpeak auto-detect the authenticated account on startup.
                            </div>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
                              <div style={{ flex: "1 1 300px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>Client ID / Consumer Key</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft.twitter_client_id || "")}
                                  placeholder="OAuth 1.0a Consumer Key"
                                  onChange={(e) => updateDraft("twitter_client_id", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 300px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>Client Secret / Consumer Secret</label>
                                <input
                                  type="password"
                                  className="input-field"
                                  value={String(settingsDraft.twitter_client_secret || "")}
                                  placeholder="OAuth 1.0a Consumer Secret"
                                  onChange={(e) => updateDraft("twitter_client_secret", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 300px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>Access Token</label>
                                <input
                                  type="password"
                                  className="input-field"
                                  value={String(settingsDraft.twitter_access_token || "")}
                                  placeholder="OAuth 1.0a access token"
                                  onChange={(e) => updateDraft("twitter_access_token", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 300px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>Access Token Secret</label>
                                <input
                                  type="password"
                                  className="input-field"
                                  value={String(settingsDraft.twitter_access_token_secret || "")}
                                  placeholder="OAuth 1.0a token secret"
                                  onChange={(e) => updateDraft("twitter_access_token_secret", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 300px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>Bearer Token (app-only)</label>
                                <input
                                  type="password"
                                  className="input-field"
                                  value={String(settingsDraft.twitter_bearer_token || "")}
                                  placeholder="App bearer token"
                                  onChange={(e) => updateDraft("twitter_bearer_token", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 300px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }}>Bot User ID</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft.twitter_bot_user_id || "")}
                                  placeholder="Optional — leave blank to auto-detect"
                                  onChange={(e) => updateDraft("twitter_bot_user_id", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                            </div>
                            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 16 }}>
                              <div style={{ flex: "1 1 180px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Poll interval (s)</label>
                                <input
                                  type="number"
                                  className="input-field"
                                  value={Number(settingsDraft.twitter_poll_interval ?? 120)}
                                  min={30}
                                  onChange={(e) => updateDraft("twitter_poll_interval", Number(e.target.value || 120))}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 180px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Autonomous interval (min)</label>
                                <input
                                  type="number"
                                  className="input-field"
                                  value={Number(settingsDraft.twitter_autonomous_interval ?? 120)}
                                  min={30}
                                  onChange={(e) => updateDraft("twitter_autonomous_interval", Number(e.target.value || 120))}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 180px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Max daily tweets</label>
                                <input
                                  type="number"
                                  className="input-field"
                                  value={Number(settingsDraft.twitter_autonomous_max_daily ?? 6)}
                                  min={1}
                                  max={20}
                                  onChange={(e) => updateDraft("twitter_autonomous_max_daily", Number(e.target.value || 6))}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                            </div>
                          </div>

                          <div className="settings-section" style={settingsSectionStyle}>
                            <div style={{ fontSize: 13, fontWeight: 700, color: colors.text, marginBottom: 4 }}>Connections are managed in Studio</div>
                            <div style={{ fontSize: 12, color: colors.textDim }}>
                              Provider identity, authorization, health, and capabilities now live in the Connections catalog. Legacy values are migrated into protected manual records and are no longer edited as ordinary Settings fields.
                            </div>
                          </div>

                          <div className="settings-section" style={{ ...settingsSectionStyle, display: "none" }} aria-hidden="true">
                            <div style={{ fontSize: 13, fontWeight: 700, color: colors.text, marginBottom: 4 }}>Productivity & Service Integrations</div>
                            <div style={{ fontSize: 12, color: colors.textDim, marginBottom: 14 }}>Keep non-messaging integrations grouped here for workspaces, content, calendars, and home services.</div>
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px", marginBottom: 12 }}>
                              <Toggle label="Google Calendar" checked={Boolean(settingsDraft.allow_calendar)} onChange={(v) => updateDraft("allow_calendar", v)} />
                              <Toggle label="Spotify" checked={Boolean(settingsDraft.allow_spotify)} onChange={(v) => updateDraft("allow_spotify", v)} />
                              <Toggle label="Notion" checked={Boolean(settingsDraft.allow_notion)} onChange={(v) => updateDraft("allow_notion", v)} />
                              <Toggle label="GitHub" checked={Boolean(settingsDraft.allow_github)} onChange={(v) => updateDraft("allow_github", v)} />
                              <Toggle label="Home Assistant" checked={Boolean(settingsDraft.allow_home_assistant)} onChange={(v) => updateDraft("allow_home_assistant", v)} />
                            </div>
                            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                                <div style={{ flex: "2 1 320px" }}>
                                  <label style={{ display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }}>Google Calendar credentials path</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={String(settingsDraft.google_calendar_credentials_path || "")}
                                    placeholder="/path/to/google_credentials.json"
                                    onChange={(e) => updateDraft("google_calendar_credentials_path", e.target.value)}
                                    style={{ width: "100%", padding: "8px 12px", fontSize: 13 }}
                                  />
                                </div>
                                <div style={{ flex: "2 1 280px" }}>
                                  <label style={{ display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }}>Google Calendar token path</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={String(settingsDraft.google_calendar_token_path || "")}
                                    placeholder="/path/to/gcal_token.json"
                                    onChange={(e) => updateDraft("google_calendar_token_path", e.target.value)}
                                    style={{ width: "100%", padding: "8px 12px", fontSize: 13 }}
                                  />
                                </div>
                                <div style={{ flex: "1 1 200px" }}>
                                  <label style={{ display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }}>Calendar timezone</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={String(settingsDraft.calendar_default_timezone || "")}
                                    placeholder="America/Denver"
                                    onChange={(e) => updateDraft("calendar_default_timezone", e.target.value)}
                                    style={{ width: "100%", padding: "8px 12px", fontSize: 13 }}
                                  />
                                </div>
                              </div>
                              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                                <div style={{ flex: "1 1 220px" }}>
                                  <label style={{ display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }}>Spotify client ID</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={String(settingsDraft.spotify_client_id || "")}
                                    placeholder="spotify client id"
                                    onChange={(e) => updateDraft("spotify_client_id", e.target.value)}
                                    style={{ width: "100%", padding: "8px 12px", fontSize: 13 }}
                                  />
                                </div>
                                <div style={{ flex: "1 1 220px" }}>
                                  <label style={{ display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }}>Spotify client secret</label>
                                  <input
                                    type="password"
                                    className="input-field"
                                    value={String(settingsDraft.spotify_client_secret || "")}
                                    placeholder="spotify client secret"
                                    onChange={(e) => updateDraft("spotify_client_secret", e.target.value)}
                                    style={{ width: "100%", padding: "8px 12px", fontSize: 13 }}
                                  />
                                </div>
                                <div style={{ flex: "2 1 280px" }}>
                                  <label style={{ display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }}>Spotify redirect URI</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={String(settingsDraft.spotify_redirect_uri || "")}
                                    placeholder="http://127.0.0.1:8888/callback"
                                    onChange={(e) => updateDraft("spotify_redirect_uri", e.target.value)}
                                    style={{ width: "100%", padding: "8px 12px", fontSize: 13 }}
                                  />
                                </div>
                                <div style={{ flex: "2 1 280px" }}>
                                  <label style={{ display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }}>Spotify token path</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={String(settingsDraft.spotify_token_path || "")}
                                    placeholder="/path/to/spotify_token.json"
                                    onChange={(e) => updateDraft("spotify_token_path", e.target.value)}
                                    style={{ width: "100%", padding: "8px 12px", fontSize: 13 }}
                                  />
                                </div>
                              </div>
                              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                                <div style={{ flex: "1 1 240px" }}>
                                  <label style={{ display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }}>Notion token</label>
                                  <input
                                    type="password"
                                    className="input-field"
                                    value={String(settingsDraft.notion_token || "")}
                                    placeholder="secret_..."
                                    onChange={(e) => updateDraft("notion_token", e.target.value)}
                                    style={{ width: "100%", padding: "8px 12px", fontSize: 13 }}
                                  />
                                </div>
                                <div style={{ flex: "2 1 280px" }}>
                                  <label style={{ display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }}>Notion default database ID</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={String(settingsDraft.notion_default_database_id || "")}
                                    placeholder="database id"
                                    onChange={(e) => updateDraft("notion_default_database_id", e.target.value)}
                                    style={{ width: "100%", padding: "8px 12px", fontSize: 13 }}
                                  />
                                </div>
                                <div style={{ flex: "1 1 240px" }}>
                                  <label style={{ display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }}>GitHub token</label>
                                  <input
                                    type="password"
                                    className="input-field"
                                    value={String(settingsDraft.github_token || "")}
                                    placeholder="ghp_..."
                                    onChange={(e) => updateDraft("github_token", e.target.value)}
                                    style={{ width: "100%", padding: "8px 12px", fontSize: 13 }}
                                  />
                                </div>
                                <div style={{ flex: "2 1 280px" }}>
                                  <label style={{ display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }}>GitHub default repo</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={String(settingsDraft.github_default_repo || "")}
                                    placeholder="owner/repo"
                                    onChange={(e) => updateDraft("github_default_repo", e.target.value)}
                                    style={{ width: "100%", padding: "8px 12px", fontSize: 13 }}
                                  />
                                </div>
                              </div>
                              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                                <div style={{ flex: "2 1 280px" }}>
                                  <label style={{ display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }}>Home Assistant URL</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={String(settingsDraft.home_assistant_url || "")}
                                    placeholder="http://homeassistant.local:8123"
                                    onChange={(e) => updateDraft("home_assistant_url", e.target.value)}
                                    style={{ width: "100%", padding: "8px 12px", fontSize: 13 }}
                                  />
                                </div>
                                <div style={{ flex: "1 1 240px" }}>
                                  <label style={{ display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }}>Home Assistant token</label>
                                  <input
                                    type="password"
                                    className="input-field"
                                    value={String(settingsDraft.home_assistant_token || "")}
                                    placeholder="home assistant token"
                                    onChange={(e) => updateDraft("home_assistant_token", e.target.value)}
                                    style={{ width: "100%", padding: "8px 12px", fontSize: 13 }}
                                  />
                                </div>
                              </div>
                            </div>
                          </div>

                          {/* ----------------- CORE ENGINE ----------------- */}
                          <div style={{ marginTop: 32, marginBottom: 16, paddingBottom: 8, borderBottom: `1px solid rgba(255,255,255,0.1)` }}>
                            <div style={{ fontSize: 18, fontWeight: 700, color: colors.text }}>Core Engine & Modules</div>
                            <div style={{ fontSize: 13, color: colors.textDim }}>Configure internal proactive limits, RAG limits, and web search features.</div>
                          </div>

                          {/* Heartbeat Configuration */}
                          <div className="settings-section" style={{
                            background: "rgba(255, 255, 255, 0.02)",
                            border: "1px solid rgba(255, 255, 255, 0.08)",
                            borderRadius: "12px",
                            padding: "20px",
                            marginBottom: "20px"
                          }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }}>Heartbeat (Proactive Mode)</div>
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px", marginBottom: 12 }}>
                              <Toggle
                                label="Enable Proactive Heartbeat"
                                checked={Boolean(settingsDraft.heartbeat_enabled)}
                                onChange={(v) => updateDraft("heartbeat_enabled", v)}
                              />
                            </div>
                            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                              <div style={{ display: "flex", gap: 8 }}>
                                <div style={{ flex: 1 }}>
                                  <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Interval (minutes)</label>
                                  <input
                                    type="number"
                                    className="input-field"
                                    value={Number(settingsDraft.heartbeat_interval || 30)}
                                    min={1}
                                    onChange={(e) => updateDraft("heartbeat_interval", parseInt(e.target.value) || 30)}
                                    style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                  />
                                </div>
                                <div style={{ flex: 2 }}>
                                  <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Channels (comma separated)</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={Array.isArray(settingsDraft.heartbeat_channels) ? settingsDraft.heartbeat_channels.join(",") : ""}
                                    placeholder="web,discord,telegram"
                                    onChange={(e) =>
                                      updateDraft(
                                        "heartbeat_channels",
                                        e.target.value
                                          .split(",")
                                          .map((x) => x.trim().toLowerCase())
                                          .filter(Boolean)
                                      )
                                    }
                                    style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                  />
                                </div>
                              </div>
                              <div>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>System Prompt (Internal thought trigger)</label>
                                <textarea
                                  className="input-field"
                                  value={String(settingsDraft.heartbeat_prompt || "")}
                                  placeholder="Review my recent memories and decide if anything needs my attention..."
                                  onChange={(e) => updateDraft("heartbeat_prompt", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14, minHeight: "80px", resize: "vertical" }}
                                />
                              </div>
                            </div>
                          </div>

                          <div className="settings-section" style={{
                            background: "rgba(255, 255, 255, 0.02)",
                            border: "1px solid rgba(255, 255, 255, 0.08)",
                            borderRadius: "12px",
                            padding: "20px",
                            marginBottom: "20px"
                          }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }}>Documents & RAG</div>
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px", marginBottom: 12 }}>
                              <Toggle
                                label="Enable Document RAG"
                                checked={Boolean(settingsDraft.document_rag_enabled)}
                                onChange={(v) => updateDraft("document_rag_enabled", v)}
                              />
                              <Toggle
                                label="Rerank Results"
                                checked={Boolean(settingsDraft.doc_rerank_enabled)}
                                onChange={(v) => updateDraft("doc_rerank_enabled", v)}
                              />
                              <Toggle
                                label="Graph Expansion"
                                checked={Boolean(settingsDraft.doc_graph_enabled)}
                                onChange={(v) => updateDraft("doc_graph_enabled", v)}
                              />
                            </div>
                            <div style={{ display: "flex", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
                              <div style={{ flex: "1 1 180px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Upload max (MB)</label>
                                <input
                                  type="number"
                                  className="input-field"
                                  value={Number(settingsDraft.doc_upload_max_mb ?? 25)}
                                  onChange={(e) => updateDraft("doc_upload_max_mb", Number(e.target.value || 0))}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 180px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Context chars</label>
                                <input
                                  type="number"
                                  className="input-field"
                                  value={Number(settingsDraft.doc_context_max_chars ?? 2800)}
                                  onChange={(e) => updateDraft("doc_context_max_chars", Number(e.target.value || 0))}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                            </div>
                          </div>

                          <div className="settings-section" style={{
                            background: "rgba(255, 255, 255, 0.02)",
                            border: "1px solid rgba(255, 255, 255, 0.08)",
                            borderRadius: "12px",
                            padding: "20px",
                            marginBottom: "20px"
                          }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }}>Web Search</div>
                            <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 12, lineHeight: 1.5 }}>
                              Web search uses DuckDuckGo by default, with Brave as an optional keyed provider.
                            </div>
                            <div style={{ display: "flex", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
                              <div style={{ flex: "1 1 180px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Timeout (s)</label>
                                <input
                                  type="number"
                                  className="input-field"
                                  value={Number(settingsDraft.web_search_timeout ?? 10)}
                                  onChange={(e) => updateDraft("web_search_timeout", Number(e.target.value || 0))}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 180px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Search max results</label>
                                <input
                                  type="number"
                                  className="input-field"
                                  value={Number(settingsDraft.web_search_max_results ?? 8)}
                                  min={1}
                                  max={10}
                                  onChange={(e) => updateDraft("web_search_max_results", Number(e.target.value || 0))}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                            </div>
                            <div style={{ marginTop: 10 }}>
                              <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Blocked domains</label>
                              <input
                                type="text"
                                className="input-field"
                                value={Array.isArray(settingsDraft.web_search_blocked_domains) ? settingsDraft.web_search_blocked_domains.join(",") : ""}
                                placeholder="msn.com,pinterest.com"
                                onChange={(e) =>
                                  updateDraft(
                                    "web_search_blocked_domains",
                                    e.target.value
                                      .split(",")
                                      .map((x) => x.trim().toLowerCase().replace(/^\./, ""))
                                      .filter(Boolean)
                                  )
                                }
                                style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                              />
                            </div>
                          </div>

                          <div className="settings-section" style={{
                            background: "rgba(255, 255, 255, 0.02)",
                            border: "1px solid rgba(255, 255, 255, 0.08)",
                            borderRadius: "12px",
                            padding: "20px",
                            marginBottom: "20px"
                          }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }}>Memory</div>
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px", marginBottom: 12 }}>
                              <Toggle label="Session Memory" checked={Boolean(settingsDraft.session_memory_enabled)} onChange={(v) => updateDraft("session_memory_enabled", v)} />
                            </div>
                            <div style={{ display: "flex", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
                              <div style={{ flex: "1 1 180px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Summary trigger</label>
                                <input
                                  type="number"
                                  className="input-field"
                                  value={Number(settingsDraft.summary_trigger_turns ?? 18)}
                                  onChange={(e) => updateDraft("summary_trigger_turns", Number(e.target.value || 0))}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 180px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Summary keep last turns</label>
                                <input
                                  type="number"
                                  className="input-field"
                                  value={Number(settingsDraft.summary_keep_last_turns ?? 6)}
                                  onChange={(e) => updateDraft("summary_keep_last_turns", Number(e.target.value || 0))}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                            </div>
                          </div>

                          {/* Local Provider & Embeddings */}
                          <div className="settings-section" style={{
                            background: "rgba(255, 255, 255, 0.02)",
                            border: "1px solid rgba(255, 255, 255, 0.08)",
                            borderRadius: "12px",
                            padding: "20px",
                            marginBottom: "20px"
                          }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }}>Local Provider & Embeddings</div>
                            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                              <div style={{ flex: "1 1 220px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>
                                  Local provider
                                  <RequiredBadge issueKey="local.provider" />
                                </label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft?.local?.provider ?? "")}
                                  placeholder="ollama | lmstudio | localai | llama_cpp | vllm"
                                  onChange={(e) => updateDraftSection("local", "provider", e.target.value)}
                                  style={{
                                    width: "100%",
                                    padding: "10px 14px",
                                    fontSize: 14,
                                    borderColor: isError("local.provider") ? colors.danger : undefined,
                                  }}
                                />
                              </div>
                              <div style={{ flex: "2 1 320px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>
                                  Local base URL
                                  <RequiredBadge issueKey="local.base_url" />
                                </label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft?.local?.base_url ?? "")}
                                  placeholder="http://localhost:11434"
                                  onChange={(e) => updateDraftSection("local", "base_url", e.target.value)}
                                  style={{
                                    width: "100%",
                                    padding: "10px 14px",
                                    fontSize: 14,
                                    borderColor: isError("local.base_url") ? colors.danger : undefined,
                                  }}
                                />
                              </div>
                              <div style={{ flex: "1 1 220px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>
                                  Local model
                                  <RequiredBadge issueKey="local.model_name" />
                                </label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft?.local?.model_name ?? "")}
                                  placeholder="llama3"
                                  onChange={(e) => updateDraftSection("local", "model_name", e.target.value)}
                                  style={{
                                    width: "100%",
                                    padding: "10px 14px",
                                    fontSize: 14,
                                    borderColor: isError("local.model_name") ? colors.danger : undefined,
                                  }}
                                />
                              </div>
                            </div>

                            <div style={{ display: "flex", gap: 10, marginTop: 10, flexWrap: "wrap", alignItems: "center" }}>
                              <button
                                className="icon-button"
                                style={{ height: 32, padding: "0 14px", fontSize: 13 }}
                                type="button"
                                onClick={() => runSettingsTest("local")}
                                disabled={Boolean(settingsTesting.local)}
                              >
                                {settingsTesting.local ? "Testing…" : "Test Local (/v1/models)"}
                              </button>
                              <button
                                className="icon-button"
                                style={{ height: 32, padding: "0 14px", fontSize: 13 }}
                                type="button"
                                onClick={() => runSettingsTest("ollama")}
                                disabled={Boolean(settingsTesting.ollama)}
                              >
                                {settingsTesting.ollama ? "Testing…" : "Test Ollama"}
                              </button>
                              {settingsTests.local ? (
                                <div className="research-snippet" style={{ color: settingsTests.local.ok ? colors.textDim : colors.danger }}>
                                  Local: {settingsTests.local.message}
                                  {typeof settingsTests.local.latency_ms === "number" ? ` (${Math.round(settingsTests.local.latency_ms)}ms)` : ""}
                                </div>
                              ) : null}
                              {settingsTests.ollama ? (
                                <div className="research-snippet" style={{ color: settingsTests.ollama.ok ? colors.textDim : colors.danger }}>
                                  Ollama: {settingsTests.ollama.message}
                                  {typeof settingsTests.ollama.latency_ms === "number" ? ` (${Math.round(settingsTests.ollama.latency_ms)}ms)` : ""}
                                </div>
                              ) : null}
                            </div>

                            <div style={{ marginTop: 16, paddingTop: 12, borderTop: "1px solid rgba(255,255,255,0.06)" }}>
                              <div style={{ fontSize: 11, fontWeight: 600, color: colors.textDim, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 8 }}>Embeddings</div>
                              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                                <div style={{ flex: "1 1 220px" }}>
                                  <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Embedding provider</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={String(settingsDraft?.embedding?.provider ?? "")}
                                    placeholder="openai | ollama | lmstudio"
                                    onChange={(e) => updateDraftSection("embedding", "provider", e.target.value)}
                                    style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                  />
                                </div>
                                <div style={{ flex: "1 1 260px" }}>
                                  <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Embedding model</label>
                                  <input
                                    type="text"
                                    className="input-field"
                                    value={String(settingsDraft?.embedding?.model ?? "")}
                                    onChange={(e) => updateDraftSection("embedding", "model", e.target.value)}
                                    style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                  />
                                </div>
                              </div>
                            </div>

                            <div style={{ marginTop: 16, paddingTop: 12, borderTop: "1px solid rgba(255,255,255,0.06)" }}>
                              <div style={{ fontSize: 11, fontWeight: 600, color: colors.textDim, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 8 }}>Speech</div>
                              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
                                <div style={{ flex: "1 1 180px" }}>
                                  <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Voice rate (words/min)</label>
                                  <input
                                    type="number"
                                    className="input-field"
                                    value={Number(settingsDraft?.voice?.rate ?? 150)}
                                    onChange={(e) => updateDraftSection("voice", "rate", Number(e.target.value || 0))}
                                    style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                  />
                                </div>
                                <div style={{ flex: "2 1 300px" }}>
                                  <div className="research-snippet" style={{ fontSize: 11, color: colors.textDim, marginBottom: 4 }}>
                                    Voice playback and dictation use your browser's built-in speech engine. Only the playback rate is configurable here.
                                  </div>
                                </div>
                              </div>
                            </div>
                          </div>

                          <div className="settings-section" style={{
                            background: "rgba(255, 255, 255, 0.02)",
                            border: "1px solid rgba(255, 255, 255, 0.08)",
                            borderRadius: "12px",
                            padding: "20px",
                            marginBottom: "20px"
                          }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }}>Automation & Webhooks</div>
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px", marginBottom: 12 }}>
                              <Toggle label="Enable Cron" checked={Boolean(settingsDraft.cron_enabled)} onChange={(v) => updateDraft("cron_enabled", v)} />
                              <Toggle label="Enable Webhooks" checked={Boolean(settingsDraft.webhook_enabled)} onChange={(v) => updateDraft("webhook_enabled", v)} />
                            </div>
                            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }}>
                              <div style={{ flex: "1 1 260px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Webhook secret</label>
                                <input
                                  type="password"
                                  className="input-field"
                                  value={String(settingsDraft.webhook_secret || "")}
                                  onChange={(e) => updateDraft("webhook_secret", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 260px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Webhook secret path</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft.webhook_secret_path || "")}
                                  onChange={(e) => updateDraft("webhook_secret_path", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 260px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Cron state path</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft.cron_state_path || "")}
                                  onChange={(e) => updateDraft("cron_state_path", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                            </div>
                          </div>

                          <div className="settings-section" style={{
                            background: "rgba(255, 255, 255, 0.02)",
                            border: "1px solid rgba(255, 255, 255, 0.08)",
                            borderRadius: "12px",
                            padding: "20px",
                            marginBottom: "20px"
                          }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }}>Advanced / Experimental</div>
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px", marginBottom: 12 }}>
                              <Toggle label="Enable Trace" checked={Boolean(settingsDraft.trace_enabled)} onChange={(v) => updateDraft("trace_enabled", v)} />
                              <Toggle label="Multi-agent pool" checked={Boolean(settingsDraft.multi_agent_enabled)} onChange={(v) => updateDraft("multi_agent_enabled", v)} />
                              <Toggle label="A2A Protocol" checked={Boolean(settingsDraft.a2a_enabled)} onChange={(v) => updateDraft("a2a_enabled", v)} />
                            </div>
                            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }}>
                              <div style={{ flex: "2 1 280px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Trace path</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft.trace_path || "")}
                                  onChange={(e) => updateDraft("trace_path", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 180px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Default workspace</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft.default_workspace || "")}
                                  onChange={(e) => updateDraft("default_workspace", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "2 1 260px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Notification channels</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={Array.isArray(settingsDraft.notification_channels) ? settingsDraft.notification_channels.join(",") : ""}
                                  placeholder="web,discord,telegram"
                                  onChange={(e) =>
                                    updateDraft(
                                      "notification_channels",
                                      e.target.value
                                        .split(",")
                                        .map((x) => x.trim().toLowerCase())
                                        .filter(Boolean)
                                    )
                                  }
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                            </div>
                            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }}>
                              <div style={{ flex: "2 1 280px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Artifacts dir</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft.artifacts_dir || "")}
                                  onChange={(e) => updateDraft("artifacts_dir", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "2 1 280px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Skills dir</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft.skills_dir || "")}
                                  onChange={(e) => updateDraft("skills_dir", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "2 1 280px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Workspaces dir</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft.workspaces_dir || "")}
                                  onChange={(e) => updateDraft("workspaces_dir", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                            </div>
                            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }}>
                              <div style={{ flex: "1 1 220px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>A2A agent name</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft.a2a_agent_name || "")}
                                  onChange={(e) => updateDraft("a2a_agent_name", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "2 1 320px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>A2A description</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft.a2a_agent_description || "")}
                                  onChange={(e) => updateDraft("a2a_agent_description", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 240px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>A2A auth key</label>
                                <input
                                  type="password"
                                  className="input-field"
                                  value={String(settingsDraft.a2a_auth_key || "")}
                                  onChange={(e) => updateDraft("a2a_auth_key", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                            </div>
                            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }}>
                              <div style={{ flex: "2 1 280px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>A2A known agents</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={Array.isArray(settingsDraft.a2a_known_agents) ? settingsDraft.a2a_known_agents.join(",") : ""}
                                  placeholder="https://agent-a.example.com,https://agent-b.example.com"
                                  onChange={(e) =>
                                    updateDraft(
                                      "a2a_known_agents",
                                      e.target.value
                                        .split(",")
                                        .map((x) => x.trim())
                                        .filter(Boolean)
                                    )
                                  }
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 180px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Orchestration max subtasks</label>
                                <input
                                  type="number"
                                  className="input-field"
                                  value={Number(settingsDraft.orchestration_max_subtasks ?? 5)}
                                  min={1}
                                  onChange={(e) => updateDraft("orchestration_max_subtasks", Number(e.target.value || 0))}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                              <div style={{ flex: "1 1 180px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Orchestration timeout (s)</label>
                                <input
                                  type="number"
                                  className="input-field"
                                  value={Number(settingsDraft.orchestration_timeout ?? 120)}
                                  min={1}
                                  onChange={(e) => updateDraft("orchestration_timeout", Number(e.target.value || 0))}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
                            </div>
                          </div>

                          <div className="settings-section" style={{
                            background: "rgba(255, 255, 255, 0.02)",
                            border: "1px solid rgba(255, 255, 255, 0.08)",
                            borderRadius: "12px",
                            padding: "20px",
                            marginBottom: "20px"
                          }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }}>Advanced Runtime JSON</div>
                            <div className="research-snippet" style={{ marginBottom: 12 }}>
                              Full live settings visibility. Effective settings include `.env` + runtime overrides. Runtime overrides are what get written to <code>apps/backend/data/settings.json</code>.
                            </div>
                            <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                              <div style={{ flex: "1 1 420px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Effective settings</label>
                                <textarea
                                  className="input-field"
                                  readOnly
                                  value={JSON.stringify(runtimeSettings || {}, null, 2)}
                                  style={{ width: "100%", minHeight: 240, padding: "10px 14px", fontSize: 12, fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace", resize: "vertical" }}
                                />
                              </div>
                              <div style={{ flex: "1 1 420px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Runtime overrides</label>
                                <textarea
                                  className="input-field"
                                  readOnly
                                  value={JSON.stringify(runtimeOverrides || {}, null, 2)}
                                  style={{ width: "100%", minHeight: 240, padding: "10px 14px", fontSize: 12, fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace", resize: "vertical" }}
                                />
                              </div>
                            </div>
                          </div>

                          <div style={{ display: "flex", gap: 10 }}>
                            <button
                              className="icon-button"
                              style={{ padding: "8px 16px", fontSize: 14 }}
                              type="button"
                              onClick={saveSettings}
                              disabled={settingsSaving}
                            >
                              {settingsSaving ? "Saving…" : "Save Settings"}
                            </button>
                            <button
                              className="icon-button"
                              style={{ padding: "8px 16px", fontSize: 14 }}
                              type="button"
                              onClick={refreshSettings}
                              disabled={settingsLoading || settingsSaving}
                            >
                              Reload
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  </div>
                </>
              )}

              {/* Capabilities Tab */}
              {false && leftTab === "capabilities" && (
                <>
                  <div className="research-scroll">
                    <div className="research-card">
                      <div className="research-title">Capabilities & Permissions</div>
                      <div className="research-snippet" style={{ marginBottom: 12 }}>
                        View available tools, materialized skills, Connections, and the permissions they require.
                      </div>
                      <button
                        className="icon-button"
                        style={{ padding: "8px 16px", fontSize: 13, marginBottom: 16 }}
                        type="button"
                        onClick={async () => {
                          try {
                            const [res, skillRes] = await Promise.all([
                              fetch(`${apiBase}/capabilities?thread_id=${encodeURIComponent(activeThreadId)}`),
                              fetch(`${apiBase}/skills/executions?session_id=${encodeURIComponent(activeThreadId)}&limit=20`),
                            ]);
                            const data = await res.json();
                            setCapabilitiesData(data);
                            const skillData = skillRes.ok ? await skillRes.json() : { items: [] };
                            setSkillExecutions(Array.isArray(skillData.items) ? skillData.items : []);
                          } catch (e) {
                            console.error("Failed to fetch capabilities:", e);
                          }
                        }}
                      >
                        Refresh Tools
                      </button>

                      {/* Provider & Workspace Info */}
                      {capabilitiesData && (
                        <>
                          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }}>
                            <div style={{ background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)" }}>
                              <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 4 }}>PROVIDER</div>
                              <div style={{ fontSize: 14, fontWeight: 600 }}>{capabilitiesData.provider || "Unknown"}</div>
                            </div>
                            <div style={{ background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)" }}>
                              <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 4 }}>PROJECT</div>
                              <div style={{ fontSize: 14, fontWeight: 600 }}>
                                {capabilitiesData.workspace?.project_attached
                                  ? (capabilitiesData.workspace?.workspace_name || capabilitiesData.workspace?.name || capabilitiesData.workspace?.project_id || "attached")
                                  : "none"}
                              </div>
                              <div style={{ fontSize: 11, color: colors.textDim, marginTop: 6 }}>
                                Mode: {capabilitiesData.workspace?.interaction_mode || "chat"}
                                {capabilitiesData.workspace?.project_id ? ` · id ${String(capabilitiesData.workspace.project_id).slice(0, 8)}…` : ""}
                              </div>
                            </div>
                          </div>

                          <CapabilityRegistryGroups registry={capabilitiesData.capability_registry} />

                          {/* Features */}
                          <div style={{ background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)", marginBottom: 16 }}>
                            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>Feature Flags</div>
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 6 }}>
                              {Object.entries(capabilitiesData.features || {}).map(([key, value]) => (
                                <div key={key} style={{ fontSize: 11, display: "flex", alignItems: "center", gap: 4 }}>
                                  <span style={{ color: value ? "#22c55e" : colors.textDim }}>{value ? "✓" : "○"}</span>
                                  <span style={{ color: colors.textDim }}>{key.replace(/_/g, " ")}</span>
                                </div>
                              ))}
                            </div>
                          </div>

                          {/* Tool Trust */}
                          <div style={{ background: "linear-gradient(135deg, rgba(245,158,11,0.08), rgba(255,255,255,0.01))", padding: 12, borderRadius: 12, border: "1px solid rgba(245,158,11,0.18)", marginBottom: 16 }}>
                            <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>Tool Trust Center</div>
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8, marginBottom: 8 }}>
                              <div className="research-snippet">Local: {capabilitiesData.trust?.origin_counts?.local || 0}</div>
                              <div className="research-snippet">MCP tools: {capabilitiesData.trust?.mcp_tool_count || 0}</div>
                              <div className="research-snippet">MCP configs: {capabilitiesData.trust?.mcp_configured_count || 0}</div>
                              <div className="research-snippet">MCP client: {capabilitiesData.trust?.mcp_client_present ? "present" : "missing"}</div>
                            </div>
                            {(capabilitiesData.trust?.warnings || []).map((w: string, i: number) => (
                              <div key={`tw-${i}`} className="research-snippet" style={{ color: "#f59e0b" }}>{w}</div>
                            ))}
                            {(capabilitiesData.trust?.recommendations || []).slice(0, 2).map((r: string, i: number) => (
                              <div key={`tr-${i}`} className="research-snippet">{r}</div>
                            ))}
                          </div>

                          {/* Skills */}
                          <div style={{ background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)", marginBottom: 16 }}>
                            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>Available Skills</div>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                              {(capabilitiesData.skills || []).length > 0 ? (
                                (capabilitiesData.skills || []).map((skill: any) => (
                                  <div key={skill.id || skill.name} style={{ fontSize: 11, padding: "4px 10px", borderRadius: 6, background: colors.panel2, border: `1px solid ${colors.line}`, display: "flex", alignItems: "center", gap: 6 }}>
                                    <span>{skill.name || skill.id}</span>
                                    {skill.has_tools && <span style={{ fontSize: 9, padding: "1px 4px", borderRadius: 3, background: "rgba(59,130,246,0.15)", color: "#3b82f6", fontWeight: 600 }}>TOOL</span>}
                                    {skill.has_plugin && <span style={{ fontSize: 9, padding: "1px 4px", borderRadius: 3, background: "rgba(255,255,255,0.05)", color: colors.textDim, fontWeight: 600 }}>LEGACY HOOK DISABLED</span>}
                                  </div>
                                ))
                              ) : (
                                <div style={{ fontSize: 11, color: colors.textDim }}>No external skills are currently available.</div>
                              )}
                            </div>
                            <div style={{ marginTop: 12, paddingTop: 10, borderTop: `1px solid ${colors.line}` }}>
                              <div style={{ fontSize: 11, fontWeight: 700, marginBottom: 7 }}>Recent governed executions</div>
                              {skillExecutions.length ? skillExecutions.slice(0, 8).map((execution: any) => (
                                <div key={execution.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 0", fontSize: 10, color: colors.textDim }}>
                                  <span style={{ color: colors.text, minWidth: 120 }}>{execution.skill_id}</span>
                                  <span>{execution.status}</span>
                                  <span>{(execution.tool_run_ids || []).length} ToolRuns</span>
                                  <div style={{ flex: 1 }} />
                                  {["selected", "planned", "running", "pending_approval", "partial", "blocked", "failed"].includes(execution.status) ? (
                                    <button
                                      type="button"
                                      className="icon-button"
                                      style={{ padding: "3px 7px", fontSize: 9 }}
                                      onClick={async () => {
                                        await fetch(`${apiBase}/skills/executions/${encodeURIComponent(execution.id)}/cancel?session_id=${encodeURIComponent(activeThreadId)}`, { method: "POST" });
                                        const response = await fetch(`${apiBase}/skills/executions?session_id=${encodeURIComponent(activeThreadId)}&limit=20`);
                                        const payload = response.ok ? await response.json() : { items: [] };
                                        setSkillExecutions(Array.isArray(payload.items) ? payload.items : []);
                                      }}
                                    >
                                      Cancel
                                    </button>
                                  ) : null}
                                </div>
                              )) : <div style={{ fontSize: 10, color: colors.textDim }}>No governed Skill executions for this Session.</div>}
                            </div>
                          </div>

                          {/* Tools List — ToolRegistry projection authority */}
                          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>
                            Tools ({(capabilitiesData.tools?.items || []).length}
                            {typeof capabilitiesData.tools?.count === "number" ? ` of ${capabilitiesData.tools.count}` : ""})
                          </div>
                          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                            {(capabilitiesData.tools?.items || []).map((tool: any) => {
                              const trust = String(tool.trust_state || tool.health || "").toLowerCase();
                              const missingConfig = Boolean(tool.missing_configuration || tool.config_missing || trust === "missing_configuration");
                              const unhealthy = ["unhealthy", "error", "failed", "degraded"].includes(trust) || Boolean(tool.unhealthy);
                              const promptOnly = Boolean(tool.prompt_only || tool.mode === "prompt_only");
                              const disabled = Boolean(tool.disabled || tool.enabled === false);
                              const providerBacked = Boolean(tool.provider_backed || tool.origin === "provider" || tool.mcp_server || tool.origin === "mcp");
                              const permissionRestricted = !tool.allowed && (Boolean(tool.blocked_reason) || (tool.policy_flags && tool.policy_flags.length));
                              let lifecycle = "registered";
                              if (disabled) lifecycle = "disabled";
                              else if (missingConfig) lifecycle = "missing configuration";
                              else if (unhealthy) lifecycle = "unhealthy";
                              else if (promptOnly) lifecycle = "prompt-only";
                              else if (permissionRestricted) lifecycle = "permission restricted";
                              else if (tool.allowed === false) lifecycle = "blocked";
                              else if (tool.allowed === true && tool.executable === false) lifecycle = "available";
                              else if (tool.allowed === true || tool.executable === true) lifecycle = "executable";
                              else lifecycle = "registered";
                              const lifecycleColor =
                                lifecycle === "executable" ? "#22c55e"
                                  : lifecycle === "available" || lifecycle === "registered" ? "rgba(255,255,255,0.65)"
                                    : lifecycle === "prompt-only" || lifecycle === "missing configuration" ? "#f59e0b"
                                      : "#ef4444";
                              return (
                              <div
                                key={tool.name}
                                style={{
                                  background: colors.panel2,
                                  padding: 10,
                                  borderRadius: 6,
                                  border: `1px solid ${tool.allowed ? colors.line : "rgba(239,68,68,0.45)"}`,
                                  opacity: disabled ? 0.55 : 1,
                                }}
                              >
                                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4, gap: 8, flexWrap: "wrap" }}>
                                  <span style={{ fontSize: 13, fontWeight: 600, fontFamily: "monospace" }}>{tool.name}</span>
                                  <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                                    <span
                                      style={{
                                        fontSize: 10,
                                        padding: "2px 6px",
                                        borderRadius: 4,
                                        background: `${lifecycleColor}22`,
                                        color: lifecycleColor,
                                        fontWeight: 700,
                                        textTransform: "uppercase",
                                        letterSpacing: "0.04em",
                                      }}
                                    >
                                      {lifecycle}
                                    </span>
                                    <span
                                      style={{
                                        fontSize: 10,
                                        padding: "2px 6px",
                                        borderRadius: 4,
                                        background: tool.risk_level === "safe" ? "#22c55e22" : tool.risk_level === "moderate" ? "#f59e0b22" : "#ef444422",
                                        color: tool.risk_level === "safe" ? "#22c55e" : tool.risk_level === "moderate" ? "#f59e0b" : "#ef4444",
                                        fontWeight: 600,
                                        textTransform: "uppercase",
                                      }}
                                    >
                                      {tool.risk_level || "safe"}
                                    </span>
                                    {tool.requires_confirmation && (
                                      <span
                                        style={{
                                          fontSize: 10,
                                          padding: "2px 6px",
                                          borderRadius: 4,
                                          background: "#3b82f622",
                                          color: "#3b82f6",
                                          fontWeight: 600,
                                        }}
                                      >
                                        CONFIRM
                                      </span>
                                    )}
                                    {providerBacked && (
                                      <span
                                        style={{
                                          fontSize: 10,
                                          padding: "2px 6px",
                                          borderRadius: 4,
                                          background: "rgba(168,85,247,0.14)",
                                          color: "#c084fc",
                                          fontWeight: 600,
                                          textTransform: "uppercase",
                                        }}
                                      >
                                        provider-backed
                                      </span>
                                    )}
                                    <span
                                      style={{
                                        fontSize: 10,
                                        padding: "2px 6px",
                                        borderRadius: 4,
                                        background: tool.origin === "mcp" ? "rgba(245,158,11,0.14)" : "rgba(34,197,94,0.12)",
                                        color: tool.origin === "mcp" ? "#f59e0b" : "#22c55e",
                                        fontWeight: 600,
                                        textTransform: "uppercase",
                                      }}
                                    >
                                      {tool.origin || "local"}
                                    </span>
                                  </div>
                                </div>
                                {!tool.allowed && tool.blocked_reason && (
                                  <div style={{ fontSize: 11, color: colors.danger, marginBottom: 4 }}>
                                    {tool.blocked_reason}
                                  </div>
                                )}
                                {tool.policy_flags && tool.policy_flags.length > 0 && (
                                  <div style={{ fontSize: 10, color: colors.textDim }}>
                                    Requires: {tool.policy_flags.join(", ")}
                                  </div>
                                )}
                                <div style={{ fontSize: 10, color: colors.textDim }}>
                                  Trust: {tool.trust_state || "built_in"}
                                  {tool.mcp_server ? ` · MCP server: ${tool.mcp_server}` : ""}
                                  {tool.description ? ` · ${String(tool.description).slice(0, 120)}` : ""}
                                </div>
                              </div>
                              );
                            })}
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                </>
              )}

              {leftTab === "approvals" && (
                <>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={async () => {
                        await refreshThreadState();
                        await refreshPendingApproval();
                        await refreshApprovals();
                      }}
                      type="button"
                    >
                      Refresh
                    </button>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={() => setLeftTab("executions")}
                      type="button"
                    >
                      View Executions
                    </button>
                  </div>

                  <div style={{ padding: "12px 14px", marginBottom: 12, borderRadius: 12, background: "linear-gradient(135deg, rgba(59,130,246,0.08), rgba(59,130,246,0.02))", border: "1px solid rgba(59,130,246,0.2)" }}>
                    <div style={{ fontSize: 11, color: "#60a5fa", fontWeight: 600, letterSpacing: "0.04em", marginBottom: 6 }}>THREAD CONTROL PLANE</div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
                      <div><div style={{ fontSize: 10, color: colors.textDim }}>Thread</div><div style={{ fontSize: 13, fontWeight: 600 }}>{threadState?.thread_id || activeThreadId || "—"}</div></div>
                      <div><div style={{ fontSize: 10, color: colors.textDim }}>Workspace</div><div style={{ fontSize: 13, fontWeight: 600 }}>{threadState?.workspace_id || workspaceMode || "default"}</div></div>
                      <div><div style={{ fontSize: 10, color: colors.textDim }}>Project</div><div style={{ fontSize: 13, fontWeight: 600 }}>{threadState?.active_project_id || activeProjectId || "none"}</div></div>
                      <div><div style={{ fontSize: 10, color: colors.textDim }}>Provider</div><div style={{ fontSize: 13, fontWeight: 600 }}>{threadState?.runtime_provider || providerDraft.provider || providerInfo?.provider || "unknown"}</div></div>
                    </div>
                  </div>

                  {pendingApproval?.has_pending && pendingApproval.action ? (
                    <div style={{ marginBottom: 14 }}>
                      <ConfirmationCard
                        action={pendingApproval.action}
                        riskLevel={pendingApproval.risk_level || pendingApproval.action.risk_level}
                        riskColor={pendingApproval.risk_color || undefined}
                        policyFlags={pendingApproval.policy_flags || pendingApproval.action.policy_flags}
                        sessionPermissions={pendingApproval.session_permissions || pendingApproval.action.session_permissions}
                        dryRunAvailable={Boolean(pendingApproval.dry_run_available)}
                        onConfirm={() => pendingApproval.approval_id && decideApproval(pendingApproval.approval_id, "confirm")}
                        onCancel={() => pendingApproval.approval_id && decideApproval(pendingApproval.approval_id, "cancel")}
                      />
                    </div>
                  ) : (
                    <div className="research-card" style={{ marginBottom: 12 }}>
                      <div className="research-title">No active approval</div>
                      <div className="research-snippet">This thread currently has no pending action waiting for confirmation.</div>
                    </div>
                  )}

                  <div className="research-scroll">
                    {approvalsLoading ? (
                      <div className="research-card"><div className="research-snippet">Loading approvals…</div></div>
                    ) : approvals.length ? (
                      approvals.map((approval) => (
                        <div key={approval.id} className="research-card" style={{ border: approval.status === "pending" ? "1px solid rgba(245,158,11,0.35)" : undefined }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginBottom: 8 }}>
                            <div style={{ fontSize: 14, fontWeight: 600, fontFamily: "monospace" }}>{approval.tool}</div>
                            <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: approval.status === "pending" ? "rgba(245,158,11,0.12)" : approval.status === "approved" || approval.status === "auto_approved" ? "rgba(34,197,94,0.12)" : "rgba(239,68,68,0.12)", color: approval.status === "pending" ? "#f59e0b" : approval.status === "approved" || approval.status === "auto_approved" ? "#22c55e" : "#ef4444", fontWeight: 700, textTransform: "uppercase" }}>
                              {approval.status}
                            </span>
                          </div>
                          <div style={{ fontSize: 12, color: colors.textDim, marginBottom: 6 }}>{approval.preview || approval.summary || approval.original_input || "Pending action"}</div>
                          <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 6 }}>Risk: {approval.risk_level} · Execution: {approval.execution_id || "—"}</div>
                          {approval.policy_flags?.length ? (
                            <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 6 }}>Requires: {approval.policy_flags.join(", ")}</div>
                          ) : null}
                          {approval.outcome_summary ? (
                            <div style={{ fontSize: 11, color: colors.textDim }}>{approval.outcome_summary}</div>
                          ) : null}
                        </div>
                      ))
                    ) : (
                      <div className="research-card"><div className="research-snippet">No approval history for this thread yet.</div></div>
                    )}
                  </div>
                </>
              )}

              {leftTab === "executions" && (
                <>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={async () => {
                        await refreshThreadState();
                        await refreshExecutions();
                        await refreshStudioOverview();
                        if (latestTraceId) await loadTrace(latestTraceId);
                      }}
                      type="button"
                    >
                      Refresh
                    </button>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={() => latestTraceId && loadTrace(latestTraceId)}
                      disabled={!latestTraceId}
                      type="button"
                    >
                      Load Latest Trace
                    </button>
                  </div>

                  <div style={{ padding: "12px 14px", marginBottom: 12, borderRadius: 12, background: "rgba(255,255,255,0.025)", border: `1px solid ${colors.line}` }}>
                    <div style={{ fontSize: 11, color: colors.textDim, fontWeight: 600, letterSpacing: "0.08em", marginBottom: 6 }}>VIEWER · EXECUTION STATE</div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10 }}>
                      <div><div style={{ fontSize: 10, color: colors.textDim }}>Latest execution</div><div style={{ fontSize: 13, fontWeight: 600 }}>{latestExecutionId || threadState?.last_execution_id || "—"}</div></div>
                      <div><div style={{ fontSize: 10, color: colors.textDim }}>Latest trace</div><div style={{ fontSize: 13, fontWeight: 600 }}>{latestTraceId || threadState?.last_trace_id || "—"}</div></div>
                      <div><div style={{ fontSize: 10, color: colors.textDim }}>Pending approval</div><div style={{ fontSize: 13, fontWeight: 600 }}>{threadState?.pending_approval_id || "none"}</div></div>
                    </div>
                  </div>

                  <div className="research-scroll">
                    <div className="research-card">
                      <div className="research-title">Echo Resolution</div>
                      <div className="research-snippet" style={{ marginTop: 6 }}>
                        {studioOverview?.resolution?.ran
                          ? `Ran once · ${studioOverview.resolution.advice?.recommendation || "advisory"}`
                          : "Not needed for the latest Turn"}
                      </div>
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 10 }}>
                      <div className="research-card" style={{ margin: 0 }}>
                        <div className="research-title">Tools</div>
                        {(studioOverview?.tools || []).filter((tool: any) => tool.selected || !tool.available).slice(0, 12).map((tool: any) => (
                          <div key={tool.name} style={{ display: "flex", justifyContent: "space-between", gap: 10, borderTop: `1px solid ${colors.line}`, padding: "8px 0", fontSize: 11 }}>
                            <span>{tool.name}</span>
                            <span style={{ color: colors.textDim }}>{tool.selected ? "selected" : tool.available ? "available" : "blocked"}</span>
                          </div>
                        ))}
                      </div>
                      <div className="research-card" style={{ margin: 0 }}>
                        <div className="research-title">Skills</div>
                        {(studioOverview?.skills || []).slice(0, 12).map((skill: any) => (
                          <div key={skill.id} style={{ display: "flex", justifyContent: "space-between", gap: 10, borderTop: `1px solid ${colors.line}`, padding: "8px 0", fontSize: 11 }}>
                            <span>{skill.name}</span>
                            <span style={{ color: colors.textDim }}>{skill.status}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                    {executionsLoading ? (
                      <div className="research-card"><div className="research-snippet">Loading executions…</div></div>
                    ) : executions.length ? (
                      executions.map((execution) => (
                        <div key={execution.id} className="research-card" style={{ border: execution.id === latestExecutionId ? "1px solid rgba(168,85,247,0.4)" : undefined }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginBottom: 8 }}>
                            <div style={{ fontSize: 13, fontWeight: 700 }}>{execution.kind.toUpperCase()}</div>
                            <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: execution.status === "completed" ? "rgba(34,197,94,0.12)" : execution.status === "pending_approval" ? "rgba(245,158,11,0.12)" : execution.status === "failed" ? "rgba(239,68,68,0.12)" : "rgba(255,255,255,0.08)", color: execution.status === "completed" ? "#22c55e" : execution.status === "pending_approval" ? "#f59e0b" : execution.status === "failed" ? "#ef4444" : colors.text, fontWeight: 700, textTransform: "uppercase" }}>
                              {execution.status}
                            </span>
                          </div>
                          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>{execution.query || "(no query)"}</div>
                          {execution.response_preview ? <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 6 }}>{execution.response_preview}</div> : null}
                          <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 6 }}>Tools: {execution.tools_used?.length ? execution.tools_used.join(", ") : "none"}</div>
                          <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 8 }}>Approvals: {execution.approvals?.length || 0} · Provider: {execution.runtime_provider || "unknown"}</div>
                          {execution.trace_id ? (
                            <button
                              className="icon-button"
                              style={{ height: 28, padding: "0 10px", fontSize: 12 }}
                              type="button"
                              onClick={() => loadTrace(String(execution.trace_id || ""))}
                            >
                              Open Trace
                            </button>
                          ) : null}
                        </div>
                      ))
                    ) : (
                      <div className="research-card"><div className="research-snippet">No executions recorded for this thread yet.</div></div>
                    )}

                    <div className="research-card" style={{ marginTop: 8 }}>
                      <div className="research-title">Trace Viewer</div>
                      <div className="research-snippet" style={{ marginBottom: 8 }}>Trace ID: {selectedTraceId || latestTraceId || "none loaded"}</div>
                      <div style={{ border: `1px solid ${colors.line}`, borderRadius: 12, background: "rgba(0,0,0,0.22)", padding: 12, maxHeight: 320, overflow: "auto", fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace", fontSize: 11, whiteSpace: "pre-wrap", color: colors.textDim }}>
                        {traceLoading ? "Loading trace…" : selectedTrace ? JSON.stringify(selectedTrace, null, 2) : "Select an execution trace to inspect persisted tool and latency details."}
                      </div>
                    </div>
                  </div>
                </>
              )}

              {/* Projects Tab */}
              {leftTab === "projects" && (
                <>
                  {/* Pipeline Status */}
                  <div style={{ padding: "10px 14px", marginBottom: 4, borderRadius: 10, background: "linear-gradient(135deg, rgba(34,197,94,0.08), rgba(34,197,94,0.02))", border: "1px solid rgba(34,197,94,0.2)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#22c55e", boxShadow: "0 0 6px rgba(34,197,94,0.5)" }} />
                      <span style={{ fontSize: 11, color: "#22c55e", fontWeight: 600, letterSpacing: "0.03em" }}>CONNECTED TO PIPELINE</span>
                    </div>
                    <div style={{ fontSize: 10, color: colors.textDim, marginTop: 4 }}>Active project context is injected into every AI response via the system prompt.</div>
                  </div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={async () => {
                        await refreshProjects();
                        await refreshThreadState();
                      }}
                      type="button"
                    >
                      Refresh
                    </button>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={async () => {
                        const name = prompt("Project name:");
                        if (!name) return;
                        const description = prompt("Description (optional):") || "";
                        const context_prompt = prompt("Context prompt (injected into AI responses when active):") || "";
                        try {
                          const res = await fetch(`${apiBase}/projects`, {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ name, description, context_prompt }),
                          });
                          const data = await res.json();
                          setProjects([...projects, data]);
                          setActiveProjectId(data.id);
                          localStorage.setItem("echospeak.active_project_id", data.id);
                          // Activate on backend too
                          await fetch(`${apiBase}/projects/${data.id}/activate?thread_id=${encodeURIComponent(activeThreadId)}`, { method: "POST" });
                          await refreshThreadState();
                        } catch (e) {
                          console.error("Failed to create project:", e);
                        }
                      }}
                      type="button"
                    >
                      New Project
                    </button>
                  </div>
                  {activeProjectId && (
                    <div style={{ marginTop: 8, padding: "12px 16px", background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 12, marginBottom: 8, boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2)" }}>
                      <div style={{ fontSize: 12, color: colors.textDim, marginBottom: 4 }}>Active Project</div>
                      <div style={{ fontSize: 14, fontWeight: 600 }}>
                        {projects.find(p => p.id === activeProjectId)?.name || "Unknown"}
                      </div>
                      <button
                        className="icon-button"
                        style={{ height: 24, padding: "0 8px", fontSize: 11, marginTop: 6 }}
                        type="button"
                        onClick={async () => {
                          setActiveProjectId("");
                          localStorage.removeItem("echospeak.active_project_id");
                          try {
                            await fetch(`${apiBase}/projects/deactivate?thread_id=${encodeURIComponent(activeThreadId)}`, { method: "POST" });
                            await refreshThreadState();
                          } catch (e) { }
                        }}
                      >
                        Deactivate
                      </button>
                    </div>
                  )}
                  <div className="research-scroll">
                    {projectsLoading ? (
                      <div className="research-card">
                        <div className="research-snippet">Loading projects…</div>
                      </div>
                    ) : projects.length ? (
                      projects.map((project) => (
                        <div
                          key={project.id}
                          className="research-card"
                          style={{
                            border: activeProjectId === project.id ? `1px solid ${colors.accent}` : undefined,
                            cursor: "pointer",
                          }}
                          onClick={async () => {
                            setActiveProjectId(project.id);
                            localStorage.setItem("echospeak.active_project_id", project.id);
                            try {
                              await fetch(`${apiBase}/projects/${project.id}/activate?thread_id=${encodeURIComponent(activeThreadId)}`, { method: "POST" });
                              await refreshThreadState();
                            } catch (e) { }
                          }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                            <div style={{ fontSize: 15, fontWeight: 600 }}>{project.name}</div>
                            <div style={{ display: "flex", gap: 6 }}>
                              {activeProjectId === project.id && (
                                <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: colors.accent + "22", color: colors.accent }}>
                                  ACTIVE
                                </span>
                              )}
                              <button
                                className="icon-button"
                                style={{ height: 28, padding: "0 10px", fontSize: 12 }}
                                type="button"
                                onClick={async (e) => {
                                  e.stopPropagation();
                                  if (!confirm("Delete this project?")) return;
                                  await fetch(`${apiBase}/projects/${project.id}`, { method: "DELETE" });
                                  setProjects(projects.filter(p => p.id !== project.id));
                                  if (activeProjectId === project.id) {
                                    setActiveProjectId("");
                                    localStorage.removeItem("echospeak.active_project_id");
                                  }
                                }}
                              >
                                Delete
                              </button>
                            </div>
                          </div>
                          {project.description && (
                            <div style={{ fontSize: 12, color: colors.textDim, marginBottom: 6 }}>{project.description}</div>
                          )}
                          {project.context_prompt && (
                            <div style={{ fontSize: 11, color: colors.textDim, fontStyle: "italic", marginBottom: 4 }}>
                              Context: {project.context_prompt.slice(0, 100)}{project.context_prompt.length > 100 ? "…" : ""}
                            </div>
                          )}
                          {project.tags && project.tags.length > 0 && (
                            <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                              {project.tags.map((tag, i) => (
                                <span key={i} style={{ fontSize: 10, padding: "2px 8px", borderRadius: 999, background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.1)", color: colors.text }}>
                                  {tag}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      ))
                    ) : (
                      <div className="research-card">
                        <div className="research-snippet">No projects yet. Create one to organize your memories and context.</div>
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* Unified Automations Tab */}
              {leftTab === "automations" && (
                <>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8, marginBottom: 10 }}>
                    {[
                      ["Tasks", (studioOverview?.tasks || []).length],
                      ["Runs", (studioOverview?.automation_runs || []).length],
                      ["Routines", routines.length],
                      ["Connections", (studioOverview?.connections || []).length],
                    ].map(([label, value]) => (
                      <div key={String(label)} className="research-card" style={{ padding: 12 }}>
                        <div style={{ fontSize: 10, color: colors.textDim, textTransform: "uppercase", letterSpacing: ".08em" }}>{label}</div>
                        <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{value}</div>
                      </div>
                    ))}
                  </div>
                  {/* Pipeline Status — reflect backend heartbeat when known */}
                  <div style={{ padding: "10px 14px", marginBottom: 4, borderRadius: 10, background: "linear-gradient(135deg, rgba(255,255,255,0.05), rgba(255,255,255,0.01))", border: `1px solid ${colors.line}` }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span
                        style={{
                          width: 8,
                          height: 8,
                          borderRadius: "50%",
                          background: studioOverview?.heartbeat?.running ? "#22c55e" : studioOverview?.heartbeat?.enabled ? "#f59e0b" : "rgba(255,255,255,0.35)",
                          boxShadow: studioOverview?.heartbeat?.running ? "0 0 6px rgba(34,197,94,0.5)" : "none",
                        }}
                      />
                      <span style={{ fontSize: 11, color: colors.text, fontWeight: 600, letterSpacing: "0.03em" }}>
                        {studioOverview?.heartbeat?.running
                          ? "HEARTBEAT RUNNING"
                          : studioOverview?.heartbeat?.enabled
                            ? "SCHEDULER ENABLED · NOT RUNNING"
                            : "SCHEDULER DISABLED OR UNKNOWN"}
                      </span>
                    </div>
                    <div style={{ fontSize: 10, color: colors.textDim, marginTop: 4 }}>
                      Scope: Session <code>{activeThreadId || "none"}</code> · Project <code>{activeProjectId || "detached"}</code>. Each trigger must create one durable Task/Run through the backend; external delivery remains approval-bound.
                    </div>
                  </div>
                  {(studioOverview?.automation_runs || []).length ? (
                    <div className="research-card" style={{ marginBottom: 10 }}>
                      <div className="research-title">Recent Runs</div>
                      {(studioOverview.automation_runs || []).slice(0, 8).map((run: any) => (
                        <div key={run.id || `${run.task_id}-${run.started_at}`} style={{ borderTop: `1px solid ${colors.line}`, padding: "8px 0", display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: 10 }}>
                          <div>
                            <div style={{ fontSize: 12, fontWeight: 650 }}>{run.objective || run.title || run.task_id || "Run"}</div>
                            <div style={{ fontSize: 10, color: colors.textDim, marginTop: 3 }}>
                              {run.source || "automation"} · {run.project_id || "no Project"} · {run.session_id || "no Session"}
                              {run.error ? ` · ${String(run.error).slice(0, 80)}` : ""}
                            </div>
                          </div>
                          <span style={{ fontSize: 10, color: colors.textDim, textTransform: "uppercase" }}>{run.status || "unknown"}</span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={async () => {
                        setRoutinesLoading(true);
                        try {
                          const res = await fetch(`${apiBase}/routines?session_id=${encodeURIComponent(activeThreadId)}&project_id=${encodeURIComponent(activeProjectId)}`);
                          const data = await res.json();
                          setRoutines(data.items || []);
                        } catch (e) {
                          console.error("Failed to load routines:", e);
                        }
                        setRoutinesLoading(false);
                      }}
                      type="button"
                    >
                      Refresh
                    </button>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={async () => {
                        const name = prompt("Routine name:");
                        if (!name) return;
                        const triggerType = prompt("Trigger type (schedule/webhook/manual):", "manual") || "manual";
                        let schedule = null;
                        let webhookPath = null;
                        if (triggerType === "schedule") {
                          schedule = prompt("Cron schedule (e.g., '0 9 * * *' for daily at 9am):");
                        } else if (triggerType === "webhook") {
                          webhookPath = prompt("Webhook path (e.g., 'my-hook'):");
                        }
                        const queryText = prompt("Query/action to run:");
                        try {
                          const res = await fetch(`${apiBase}/routines`, {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({
                              name,
                              trigger_type: triggerType,
                              schedule,
                              webhook_path: webhookPath ? `/${webhookPath}` : null,
                              action_type: "query",
                              action_config: { query: queryText || "" },
                              project_id: activeProjectId,
                              session_id: activeThreadId,
                            }),
                          });
                          const data = await res.json();
                          setRoutines([...routines, data]);
                        } catch (e) {
                          console.error("Failed to create routine:", e);
                        }
                      }}
                      type="button"
                    >
                      New Routine
                    </button>
                  </div>
                  <div className="research-scroll">
                    {routinesLoading ? (
                      <div className="research-card">
                        <div className="research-snippet">Loading routines…</div>
                      </div>
                    ) : routines.length ? (
                      routines.map((routine) => (
                        <div key={routine.id} className="research-card">
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                            <div style={{ fontSize: 15, fontWeight: 600 }}>{routine.name}</div>
                            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                              <span
                                style={{
                                  fontSize: 10,
                                  padding: "2px 6px",
                                  borderRadius: 4,
                                  background: routine.enabled ? "rgba(34,197,94,0.12)" : "rgba(107,114,128,0.12)",
                                  color: routine.enabled ? "#22c55e" : colors.textDim,
                                }}
                              >
                                {routine.enabled ? "ENABLED" : "DISABLED"}
                              </span>
                              <span
                                style={{
                                  fontSize: 10,
                                  padding: "2px 6px",
                                  borderRadius: 4,
                                  background: colors.panel2,
                                  color: colors.textDim,
                                }}
                              >
                                {routine.trigger_type.toUpperCase()}
                              </span>
                            </div>
                          </div>
                          {routine.description && (
                            <div style={{ fontSize: 12, color: colors.textDim, marginBottom: 6 }}>{routine.description}</div>
                          )}
                          {(routine as any).objective || (routine as any).action_config?.query ? (
                            <div style={{ fontSize: 12, color: colors.text, marginBottom: 6, lineHeight: 1.45 }}>
                              <strong style={{ color: colors.textDim, fontWeight: 600 }}>Objective:</strong>{" "}
                              {String((routine as any).objective || (routine as any).action_config?.query || "").slice(0, 240)}
                            </div>
                          ) : null}
                          <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 4 }}>
                            <strong>Type:</strong> {routine.action_type} | <strong>Runs:</strong> {routine.run_count}
                            {(routine as any).project_id ? ` | Project: ${String((routine as any).project_id).slice(0, 12)}…` : " | Project: scope-bound"}
                          </div>
                          {((routine as any).allowed_tools || (routine as any).allowed_skills || (routine as any).allowed_connections) ? (
                            <div style={{ fontSize: 10, color: colors.textDim, marginBottom: 4 }}>
                              {((routine as any).allowed_tools || []).length ? `Tools: ${((routine as any).allowed_tools || []).slice(0, 6).join(", ")} ` : ""}
                              {((routine as any).allowed_skills || []).length ? `Skills: ${((routine as any).allowed_skills || []).slice(0, 4).join(", ")} ` : ""}
                              {((routine as any).allowed_connections || []).length ? `Connections: ${((routine as any).allowed_connections || []).slice(0, 4).join(", ")}` : ""}
                            </div>
                          ) : null}
                          {routine.schedule && (
                            <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 4 }}>
                              <strong>Schedule:</strong> {routine.schedule}
                            </div>
                          )}
                          {routine.webhook_path && (
                            <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 4, fontFamily: "monospace" }}>
                              <strong>Webhook:</strong> POST /webhooks{routine.webhook_path}
                            </div>
                          )}
                          {routine.next_run && (
                            <div style={{ fontSize: 11, color: colors.accent, marginBottom: 4 }}>
                              <strong>Next run:</strong> {new Date(routine.next_run).toLocaleString()}
                            </div>
                          )}
                          {routine.last_run && (
                            <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 4 }}>
                              <strong>Last run:</strong> {new Date(routine.last_run).toLocaleString()}
                            </div>
                          )}
                          <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                            <button
                              className="icon-button"
                              style={{ height: 28, padding: "0 10px", fontSize: 12 }}
                              type="button"
                              onClick={async () => {
                                await fetch(`${apiBase}/routines/${routine.id}/run?session_id=${encodeURIComponent(activeThreadId)}&project_id=${encodeURIComponent(activeProjectId)}`, { method: "POST" });
                                // Refresh to update run count
                                const res = await fetch(`${apiBase}/routines?session_id=${encodeURIComponent(activeThreadId)}&project_id=${encodeURIComponent(activeProjectId)}`);
                                const data = await res.json();
                                setRoutines(data.items || []);
                              }}
                            >
                              Run Now
                            </button>
                            <button
                              className="icon-button"
                              style={{ height: 28, padding: "0 10px", fontSize: 12 }}
                              type="button"
                              onClick={async () => {
                                await fetch(`${apiBase}/routines/${routine.id}?session_id=${encodeURIComponent(activeThreadId)}&project_id=${encodeURIComponent(activeProjectId)}`, {
                                  method: "PUT",
                                  headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify({ enabled: !routine.enabled }),
                                });
                                setRoutines(routines.map(r => r.id === routine.id ? { ...r, enabled: !r.enabled } : r));
                              }}
                            >
                              {routine.enabled ? "Disable" : "Enable"}
                            </button>
                            <button
                              className="icon-button"
                              style={{ height: 28, padding: "0 10px", fontSize: 12 }}
                              type="button"
                              onClick={async () => {
                                if (!confirm("Delete this routine?")) return;
                                await fetch(`${apiBase}/routines/${routine.id}?session_id=${encodeURIComponent(activeThreadId)}&project_id=${encodeURIComponent(activeProjectId)}`, { method: "DELETE" });
                                setRoutines(routines.filter(r => r.id !== routine.id));
                              }}
                            >
                              Delete
                            </button>
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="research-card">
                        <div className="research-snippet">No routines yet. Create one to automate actions on a schedule or via webhook.</div>
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* Soul Tab */}
              {leftTab === "soul" && (
                <>
                  <div className="research-scroll">
                    <div className="research-card">
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, gap: 12, flexWrap: "wrap" }}>
                        <div>
                          <h3 style={{ margin: 0, fontSize: 16, color: colors.text }}>Soul · Identity</h3>
                          <div style={{ fontSize: 11, color: colors.textDim, marginTop: 4 }}>
                            Persona, tone, response style, and creator attribution for Echo. Secrets and hidden prompts are not exposed here.
                          </div>
                        </div>
                        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          {soulEnabled ? (
                            <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(34,197,94,0.12)", color: "#22c55e", fontWeight: 700 }}>ENABLED</span>
                          ) : (
                            <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(107,114,128,0.12)", color: colors.textDim, fontWeight: 700 }}>DISABLED</span>
                          )}
                          {soulSavedAt && (
                            <span style={{ fontSize: 10, color: colors.textDim }}>Saved {new Date(soulSavedAt).toLocaleTimeString()}</span>
                          )}
                        </div>
                      </div>

                      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 8, marginBottom: 14 }}>
                        <div style={{ padding: 10, borderRadius: 8, border: `1px solid ${colors.line}`, background: "rgba(255,255,255,0.02)" }}>
                          <div style={{ fontSize: 10, color: colors.textDim, textTransform: "uppercase", letterSpacing: "0.06em" }}>Persistence</div>
                          <div style={{ fontSize: 12, marginTop: 4, wordBreak: "break-all" }}>{soulPath || "runtime soul file"}</div>
                        </div>
                        <div style={{ padding: 10, borderRadius: 8, border: `1px solid ${colors.line}`, background: "rgba(255,255,255,0.02)" }}>
                          <div style={{ fontSize: 10, color: colors.textDim, textTransform: "uppercase", letterSpacing: "0.06em" }}>Budget</div>
                          <div style={{ fontSize: 12, marginTop: 4 }}>{soulContent.length} / {soulMaxChars} chars</div>
                        </div>
                        <div style={{ padding: 10, borderRadius: 8, border: `1px solid ${colors.line}`, background: "rgba(255,255,255,0.02)" }}>
                          <div style={{ fontSize: 10, color: colors.textDim, textTransform: "uppercase", letterSpacing: "0.06em" }}>Applies to</div>
                          <div style={{ fontSize: 12, marginTop: 4 }}>New turns in this runtime</div>
                        </div>
                      </div>

                      <div style={{ fontSize: 12, color: colors.textDim, marginBottom: 12 }}>
                        Edit the durable identity document below. Changes persist through the Soul API and apply on subsequent conversations — not mid-stream rewrites of private model reasoning.
                      </div>

                      {soulLoading ? (
                        <div style={{ color: colors.textDim, padding: 20, textAlign: "center" }}>Loading...</div>
                      ) : soulError ? (
                        <div style={{ color: "#ef4444", padding: 12, background: "rgba(239,68,68,0.1)", borderRadius: 6, marginBottom: 12 }}>{soulError}</div>
                      ) : (
                        <>
                          <div style={{ marginBottom: 12 }}>
                            <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 4 }}>
                              Path: <code style={{ background: colors.panel2, padding: "2px 6px", borderRadius: 4 }}>{soulPath}</code>
                              {" | "}
                              Max chars: <code style={{ background: colors.panel2, padding: "2px 6px", borderRadius: 4 }}>{soulMaxChars}</code>
                              {" | "}
                              Characters: <code style={{ background: colors.panel2, padding: "2px 6px", borderRadius: 4 }}>{soulContent.length}</code>
                            </div>
                          </div>

                          <textarea
                            value={soulContent}
                            onChange={(e) => setSoulContent(e.target.value)}
                            placeholder="# EchoSpeak Soul

## Identity
I am EchoSpeak, a personal AI assistant...

## Communication Style
- Direct and concise
- No corporate pleasantries

## Values
- Honesty over politeness
- Getting things done

## Boundaries
- I won't reveal API keys
- I won't sugarcoat technical realities"
                            style={{
                              width: "100%",
                              minHeight: 400,
                              background: colors.panel2,
                              border: `1px solid ${colors.line}`,
                              borderRadius: 6,
                              padding: 12,
                              color: colors.text,
                              fontFamily: "monospace",
                              fontSize: 12,
                              resize: "vertical",
                              lineHeight: 1.5,
                            }}
                          />

                          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                            <button
                              className="icon-button"
                              style={{ height: 32, padding: "0 16px", fontSize: 13 }}
                              type="button"
                              onClick={saveSoul}
                              disabled={soulSaving}
                            >
                              {soulSaving ? "Saving..." : "Save Soul"}
                            </button>
                            <button
                              className="icon-button"
                              style={{ height: 32, padding: "0 16px", fontSize: 13 }}
                              type="button"
                              onClick={refreshSoul}
                            >
                              Reset
                            </button>
                          </div>

                          {soulContent.length > soulMaxChars && (
                            <div style={{ color: "#ef4444", fontSize: 11, marginTop: 8 }}>
                              ⚠️ Content exceeds max chars limit ({soulContent.length} / {soulMaxChars})
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                </>
              )}

              {leftTab === "avatar_editor" && (
                <div className="research-scroll">
                  {desktopMode ? (
                    <section className="settings-general-card">
                      <div>
                        <div className="research-title">Desktop companion</div>
                        <div className="research-snippet">Keep the same animated Echo available outside the main window.</div>
                      </div>
                      <div className="settings-general-row">
                        <span>
                          <strong>Show Echo on desktop</strong>
                          <small>Uses the selected Chat Session and the same canonical runtime.</small>
                        </span>
                        <button type="button" className="icon-button" onClick={() => void openDesktopCompanionWindow()}>
                          Open
                        </button>
                      </div>
                    </section>
                  ) : null}
                  <AvatarEditor apiBase={apiBase} colors={colors} onConfigChange={setAvatarConfig} />
                </div>
              )}

              {leftTab === "system_services" && (
                <div className="research-scroll">
                  <div className="research-card">
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                      <h3 style={{ margin: 0, fontSize: 16, color: colors.text }}>⚡ System Services</h3>
                      <button className="icon-button" onClick={refreshServices} disabled={servicesLoading} style={{ fontSize: 12, padding: "4px 10px", height: "auto" }}>
                        {servicesLoading ? "Refreshing..." : "Refresh"}
                      </button>
                    </div>

                    <div style={{ fontSize: 12, color: colors.textDim, marginBottom: 20 }}>
                      Monitor and control background services like Heartbeat, Telegram, and the Discord bot's live activity bridge.
                    </div>

                    {/* Heartbeat Status Panel */}
                    <div style={{ background: colors.panel2, borderRadius: 8, padding: 16, border: `1px solid ${colors.line}`, marginBottom: 16 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                          <span style={{ fontSize: 18 }}>💓</span>
                          <span style={{ fontSize: 14, fontWeight: 600, color: colors.text }}>Heartbeat Scheduler</span>
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          {servicesHeartbeatStatus?.running ? (
                            <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(34,197,94,0.12)", color: "#22c55e", display: "flex", alignItems: "center", gap: 4 }}>
                              <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#22c55e", display: "inline-block", boxShadow: "0 0 8px #22c55e" }}></span> RUNNING
                            </span>
                          ) : (
                            <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(107,114,128,0.12)", color: colors.textDim }}>STOPPED</span>
                          )}
                        </div>
                      </div>

                      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
                        <button
                          className="icon-button"
                          style={{ fontSize: 12, padding: "6px 12px", height: "auto", background: servicesHeartbeatStatus?.running ? "rgba(255,255,255,0.05)" : "rgba(34,197,94,0.2)", color: servicesHeartbeatStatus?.running ? colors.textDim : "#22c55e", border: `1px solid ${servicesHeartbeatStatus?.running ? colors.line : "rgba(34,197,94,0.5)"}` }}
                          onClick={async () => {
                            if (servicesHeartbeatStatus?.running) return;
                            await fetchWithTimeout(`${apiBase}/heartbeat/start`, { method: "POST" });
                            refreshServices();
                          }}
                          disabled={servicesHeartbeatStatus?.running || servicesLoading}
                        >
                          Start Heartbeat
                      </button>
                        <button
                          className="icon-button"
                          style={{ fontSize: 12, padding: "6px 12px", height: "auto", background: !servicesHeartbeatStatus?.running ? "rgba(255,255,255,0.05)" : "rgba(239,68,68,0.2)", color: !servicesHeartbeatStatus?.running ? colors.textDim : "#ef4444", border: `1px solid ${!servicesHeartbeatStatus?.running ? colors.line : "rgba(239,68,68,0.5)"}` }}
                          onClick={async () => {
                            if (!servicesHeartbeatStatus?.running) return;
                            await fetchWithTimeout(`${apiBase}/heartbeat/stop`, { method: "POST" });
                            refreshServices();
                          }}
                          disabled={!servicesHeartbeatStatus?.running || servicesLoading}
                        >
                          Stop Heartbeat
                      </button>
                      </div>
                    </div>

                      <div style={{ fontSize: 12, color: colors.text, marginBottom: 8, fontWeight: 600 }}>Recent Proactive Thoughts</div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                        {!servicesHeartbeatHistory || servicesHeartbeatHistory.length === 0 ? (
                          <div style={{ fontSize: 12, color: colors.textDim, fontStyle: "italic" }}>No recent history.</div>
                        ) : (
                          servicesHeartbeatHistory.map((h: any, i: number) => (
                            <div key={i} style={{ background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6, fontSize: 12, borderLeft: `3px solid ${h.status === "error" ? "#ef4444" : h.status === "ran_tools" ? "#3b82f6" : colors.line}` }}>
                              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                                <span style={{ color: colors.textDim }}>{new Date(h.timestamp * 1000 || h.timestamp).toLocaleString()}</span>
                                <span style={{ textTransform: "uppercase", fontSize: 10, color: h.status === "error" ? "#ef4444" : colors.accent }}>{h.status}</span>
                              </div>
                              <div style={{ color: colors.text }}>{h.result || h.action}</div>
                            </div>
                          ))
                        )}
                      </div>
                    </div>

                    {/* Telegram Bot Panel */}
                    <div style={{ background: colors.panel2, borderRadius: 8, padding: 16, border: `1px solid ${colors.line}` }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                          <span style={{ fontSize: 18 }}>✈️</span>
                          <span style={{ fontSize: 14, fontWeight: 600, color: colors.text }}>Telegram Bot</span>
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          {servicesTelegramStatus?.running ? (
                            <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(56,189,248,0.12)", color: "#38bdf8", display: "flex", alignItems: "center", gap: 4 }}>
                              <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#38bdf8", display: "inline-block", boxShadow: "0 0 8px #38bdf8" }}></span> ONLINE
                            </span>
                          ) : (
                            <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(107,114,128,0.12)", color: colors.textDim }}>OFFLINE</span>
                          )}
                        </div>
                      </div>

                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, fontSize: 12 }}>
                        <div style={{ background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6 }}>
                          <span style={{ color: colors.textDim, display: "block", marginBottom: 4 }}>Bot Username</span>
                          <span style={{ color: colors.text }}>{servicesTelegramStatus?.username ? `@${servicesTelegramStatus.username}` : "N/A"}</span>
                        </div>
                        <div style={{ background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6 }}>
                          <span style={{ color: colors.textDim, display: "block", marginBottom: 4 }}>Allowed Users</span>
                          <span style={{ color: colors.text }}>{servicesTelegramStatus?.allowed_users?.length ? servicesTelegramStatus.allowed_users.join(", ") : "N/A"}</span>
                        </div>
                      </div>
                      {!servicesTelegramStatus?.running && (
                        <div style={{ fontSize: 11, color: colors.textDim, marginTop: 12, fontStyle: "italic" }}>
                          The bot is offline. Make sure you have configured a valid Bot Token in the Settings tab and toggled the bot on.
                        </div>
                      )}
                    </div>

                    {/* Discord Bot Panel */}
                    <div style={{ background: colors.panel2, borderRadius: 8, padding: 16, border: `1px solid ${colors.line}`, marginTop: 16 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                          <span style={{ fontSize: 18 }}>🎮</span>
                          <span style={{ fontSize: 14, fontWeight: 600, color: colors.text }}>Discord Bot</span>
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          {servicesDiscordStatus?.running ? (
                            <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(99,102,241,0.12)", color: "#818cf8", display: "flex", alignItems: "center", gap: 4 }}>
                              <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#818cf8", display: "inline-block", boxShadow: "0 0 8px #818cf8" }}></span> ONLINE
                            </span>
                          ) : servicesDiscordStatus?.enabled ? (
                            <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(245,158,11,0.12)", color: "#f59e0b" }}>OFFLINE</span>
                          ) : (
                            <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(107,114,128,0.12)", color: colors.textDim }}>DISABLED</span>
                          )}
                        </div>
                      </div>

                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, fontSize: 12 }}>
                        <div style={{ background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6 }}>
                          <span style={{ color: colors.textDim, display: "block", marginBottom: 4 }}>Bot Username</span>
                          <span style={{ color: colors.text }}>{servicesDiscordStatus?.username ? `@${servicesDiscordStatus.username}` : "N/A"}</span>
                        </div>
                        <div style={{ background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6 }}>
                          <span style={{ color: colors.textDim, display: "block", marginBottom: 4 }}>Guilds (Servers)</span>
                          <span style={{ color: colors.text }}>{servicesDiscordStatus?.guilds || "0"}</span>
                        </div>
                      </div>
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, fontSize: 12, marginTop: 12 }}>
                        <div style={{ background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6 }}>
                          <span style={{ color: colors.textDim, display: "block", marginBottom: 4 }}>Gateway Link</span>
                          <span style={{ color: discordGatewayConnected ? "#22c55e" : colors.textDim }}>
                            {discordGatewayConnected ? "Connected" : "Disconnected"}
                          </span>
                        </div>
                        <div style={{ background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6 }}>
                          <span style={{ color: colors.textDim, display: "block", marginBottom: 4 }}>Gateway Session</span>
                          <span style={{ color: colors.text }}>{discordGatewaySessionId || "Waiting..."}</span>
                        </div>
                      </div>
                      <div style={{ fontSize: 12, color: colors.text, marginTop: 16, marginBottom: 8, fontWeight: 600 }}>Live Discord Activity</div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                        {discordLiveEvents.length === 0 ? (
                          <div style={{ fontSize: 12, color: colors.textDim, fontStyle: "italic" }}>
                            No live Discord activity yet. When the bot runs Discord tools, events will appear here automatically.
                          </div>
                        ) : (
                          discordLiveEvents.map((event) => (
                            <div key={event.id} style={{ background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6, borderLeft: `3px solid ${event.kind === "error" ? "#ef4444" : "#818cf8"}` }}>
                              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, gap: 12 }}>
                                <span style={{ color: colors.textDim, fontSize: 11 }}>{new Date(event.at).toLocaleString()}</span>
                                <span style={{ textTransform: "uppercase", fontSize: 10, color: event.kind === "error" ? "#ef4444" : "#818cf8" }}>
                                  {event.kind === "error" ? "gateway error" : (event.source || "discord_bot")}
                                </span>
                              </div>
                              <div style={{ color: colors.text, fontSize: 12.5 }}>
                                {event.kind === "error" ? (event.message || "Gateway error") : `Tool activity: ${event.tool || "unknown"}`}
                              </div>
                            </div>
                          ))
                        )}
                      </div>
                      {!servicesDiscordStatus?.running && (
                        <div style={{ fontSize: 11, color: colors.textDim, marginTop: 12, fontStyle: "italic" }}>
                          {!servicesDiscordStatus?.enabled
                            ? "The bot is disabled in Settings. Enable Discord Bot and save settings to bring it online."
                            : !servicesDiscordStatus?.token_set
                              ? "The bot is enabled but no bot token is configured in Settings."
                              : "The bot is enabled but not connected. Check the token and Discord privileged intents, then save settings again or restart the API."}
                        </div>
                      )}
                    </div>
                  </div>
              )}
                      </div>
                    </div>
                  </div>
                  </motion.div>
                </div>,
                desktopMode ? desktopStudioHost! : document.body
              )}
            </div>
          </div>
        </div>
      </div>
      <input
        type="file"
        ref={docInputRef}
        style={{ display: "none" }}
        onChange={(e) => setDocFile(e.target.files?.[0] || null)}
      />
    </div>
  );
};
