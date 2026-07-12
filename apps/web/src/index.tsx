import React, { useCallback, useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { create } from "zustand";
import { SquareAvatarVisual } from "./components/SquareAvatarVisual";
import { getToolCategory, getToolDisplayDetails } from "./components/echoAnimationUtils";
import type { CodeDiffSession } from "./components/InlineCodeDiff";
import { CodeWorkspace } from "./components/CodeWorkspace";
import type { LiveFileChange, LiveTerminalEntry } from "./components/CodeWorkspace";
import { TaskChecklist, createEmptyTaskPlan, taskPlanReducer } from "./components/TaskChecklist";
import type { TaskPlanState } from "./components/TaskChecklist";
import type { EchoReaction, ToolCategory } from "./components/echoAnimationUtils";
import { TodoPanel } from "./components/TodoPanel";
import { AvatarEditor } from "./components/AvatarEditor";
import { ProjectSidebar } from "./components/ProjectSidebar";
import { loadRuntimeLayout, runtimeGridColumns, saveRuntimeLayout } from "./runtimeLayout";
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
  agentActivityReducer,
  initialAgentActivity,
  isStreamEventCurrent,
  isStreamThreadCurrent,
  toolCategoryFromPhase,
  type AgentActivityState,
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

type AgentStreamEvent =
  | { type: "tool_start"; id: string; name: string; input: string; at: number; request_id?: string }
  | { type: "tool_end"; id: string; name?: string; output: string; research?: ResearchRun; outcome?: { success: boolean; status: string; error_code?: string; error_message?: string; retryable?: boolean }; at: number; request_id?: string }
  | { type: "tool_error"; id: string; error: string; at: number; request_id?: string }
  | { type: "thinking"; content: string; at: number; request_id?: string }
  | { type: "thinking_step"; step_type: string; content: string; status: string; at: number; request_id?: string }
  | { type: "agent_token"; data: string; at: number; request_id?: string }
  | { type: "memory_saved"; memory_count: number; at: number; request_id?: string }
  | { type: "task_plan"; data: any[]; at?: number; request_id?: string }
  | { type: "task_step"; data: { index: number; status: string; description?: string; tool?: string; result_preview?: string; total?: number }; at?: number; request_id?: string }
  | { type: "task_reflection"; data: { index: number; accepted: boolean; reason?: string; cycle?: number }; at?: number; request_id?: string }
  | { type: "partial_reply"; response: string; speak?: boolean; segment?: number; reason?: string; request_id?: string; at: number }
  | { type: "final"; response: string; spoken_text?: string; success: boolean; memory_count: number; doc_sources?: DocSource[]; research?: ResearchRun[]; response_render?: ResponseRenderIntent; execution_id?: string; trace_id?: string; thread_state?: ThreadSessionState | null; partial_replies?: string[]; request_id?: string; at: number }
  | { type: "turn_bound"; request_id?: string; execution_id?: string; turn_id?: string; thread_id?: string; active_project_id?: string; at: number }
  | { type: "error"; message: string; at: number; request_id?: string };

/** Square spinner — Echo's shape, no emoji */
const SquareLoader: React.FC<{ size?: number; color?: string }> = ({ size = 12, color = "rgba(140,160,255,0.95)" }) => (
  <span
    aria-hidden
    style={{
      display: "inline-block",
      width: size,
      height: size,
      borderRadius: 2,
      border: `2px solid ${color}`,
      borderTopColor: "transparent",
      animation: "echo-square-spin 0.75s linear infinite",
      verticalAlign: "middle",
      flexShrink: 0,
    }}
  />
);

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
  plan: TaskPlanState;
  request_id?: string;
};

type TimelineItem =
  | { kind: "message"; id: string; at: number; msg: Message }
  | { kind: "activity"; id: string; at: number; item: ActivityItem }
  | { kind: "task_plan"; id: string; at: number; entry: TaskPlanEntry };

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

type CodingReadiness = {
  ok: boolean;
  provider: { name: string; ready: boolean; message: string; detail?: string };
  workspace: Record<string, any>;
  file_roots: { root?: string; extra_roots?: string[]; terminal_execution_mode?: string; terminal_denylist?: string[] };
  tools: { name: string; loaded: boolean; allowed: boolean; risk_level?: string; requires_confirmation?: boolean; policy_flags?: string[]; reason?: string }[];
  blocked_tools: string[];
  missing_tools: string[];
  recommended_loop: string[];
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

const fetchWithTimeout = async (url: string, init?: RequestInit, timeoutMs: number = 4500) => {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(id);
  }
};

const normalizeTimestampMs = (value: unknown): number => {
  const num = Number(value);
  if (!Number.isFinite(num) || num <= 0) return Date.now();
  return num < 1_000_000_000_000 ? num * 1000 : num;
};

const replaceCodeSession = (sessions: CodeDiffSession[], nextSession: CodeDiffSession): [CodeDiffSession[], number] => {
  const existingIndex = sessions.findIndex((session) => session.filename === nextSession.filename);
  if (existingIndex >= 0) {
    const next = [...sessions];
    next[existingIndex] = nextSession;
    return [next, existingIndex];
  }
  const next = [...sessions, nextSession].slice(-10);
  const nextIndex = next.findIndex((session) => session.filename === nextSession.filename);
  return [next, nextIndex >= 0 ? nextIndex : Math.max(0, next.length - 1)];
};

const isFileWriteSummary = (value: string): boolean => /^(Wrote|Appended) \d+ chars to /.test(value);

/** Parse <<<ECHO_FILE ...>>> ... <<<END_ECHO_FILE>>> blocks from tool output. */
const parseEchoFileBlock = (
  raw: string
): { path: string; content: string; action: string; chars: number } | null => {
  const text = String(raw || "");
  const m = text.match(
    /<<<ECHO_FILE\s+([^>]*?)>>>\r?\n?([\s\S]*?)\r?\n?<<<END_ECHO_FILE>>>/i
  );
  if (!m) return null;
  const meta = m[1] || "";
  const content = m[2] ?? "";
  const pathM = meta.match(/\bpath=([^\s]+)/i);
  const actionM = meta.match(/\baction=([^\s]+)/i);
  const charsM = meta.match(/\bchars=(\d+)/i);
  return {
    path: (pathM?.[1] || "").replace(/^["']|["']$/g, ""),
    content,
    action: actionM?.[1] || "read",
    chars: charsM ? Number(charsM[1]) : content.length,
  };
};

/** Extract path + content from tool input (JSON / kwargs / free form). */
const parseFileToolInput = (rawInput: string): { path: string; content: string } => {
  const raw = String(rawInput || "");
  let path = "";
  let content = "";
  try {
    const j = JSON.parse(raw);
    if (j && typeof j === "object") {
      path = String(j.path || j.file_path || j.filepath || j.filename || "").trim();
      content = typeof j.content === "string" ? j.content : typeof j.text === "string" ? j.text : "";
      if (path || content) return { path, content };
    }
  } catch {
    /* not JSON */
  }
  const pathM =
    raw.match(/['"]path['"]\s*:\s*['"]([^'"]+)['"]/i) ||
    raw.match(/\bpath\s*=\s*['"]([^'"]+)['"]/i) ||
    raw.match(/\bpath\s*[:=]\s*([^\s,}\]]+)/i);
  if (pathM) path = pathM[1].replace(/^['"]|['"]$/g, "");
  // content after content= / "content": "
  const contentKey = raw.search(/['"]content['"]\s*:/i);
  if (contentKey >= 0) {
    const after = raw.slice(contentKey);
    const quoted = after.match(/['"]content['"]\s*:\s*(")([\s\S]*)$/i);
    if (quoted) {
      // naive unescape of JSON string remainder
      let body = quoted[2];
      // trim trailing ", append flags
      const end = body.lastIndexOf('"');
      if (end >= 0) body = body.slice(0, end);
      content = body.replace(/\\n/g, "\n").replace(/\\t/g, "\t").replace(/\\"/g, '"').replace(/\\\\/g, "\\");
    }
  }
  if (!path) {
    // first path-like token
    const bare = raw.match(/(?:Desktop[/\\][^\s,'"]+\.\w{1,8}|[A-Za-z]:\\[^\s,'"]+|\/[^\s,'"]+\.\w{1,8}|[\w./\\-]+\.\w{1,8})/);
    if (bare) path = bare[0];
  }
  return { path, content };
};

const basenamePath = (p: string): string => {
  const s = String(p || "").replace(/\\/g, "/");
  const parts = s.split("/").filter(Boolean);
  return parts[parts.length - 1] || s || "file";
};

const langFromFilename = (filename: string, toolName: string): string => {
  if (toolName === "terminal_run") return "bash";
  const ext = (filename.split(".").pop() || "text").toLowerCase();
  if (ext === "js" || ext === "mjs" || ext === "cjs") return "javascript";
  if (ext === "ts" || ext === "tsx") return "typescript";
  if (ext === "py") return "python";
  if (ext === "htm") return "html";
  return ext || "text";
};

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
  selectedVoice: string | null;
  speechBeat: number;
  addMessage: (msg: Message) => void;
  setStreaming: (v: boolean) => void;
  setListening: (v: boolean) => void;
  setSpeaking: (v: boolean) => void;
  setSpeechEnabled: (v: boolean) => void;
  setSelectedVoice: (v: string | null) => void;
  bumpSpeechBeat: () => void;
};

const useAppStore = create<AppState>((set) => ({
  messages: [],
  streaming: false,
  listening: false,
  speaking: false,
  speechEnabled: true,
  setSpeechEnabled: (v) => set({ speechEnabled: v }),
  selectedVoice: null,
  setSelectedVoice: (v) => set({ selectedVoice: v }),
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
         /* ── EchoSpeak Studio shell (portaled to body — full workspace, no chrome bleed) ── */
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
           visibility: hidden;
           pointer-events: none;
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
           z-index: 2;
           display: flex;
           justify-content: center;
           border-bottom: 1px solid rgba(255,255,255,0.06);
           background: rgba(255,255,255,0.015);
         }
         .studio-nav-inner {
           display: flex;
           gap: 0;
           overflow-x: auto;
           max-width: 960px;
           width: 100%;
           padding: 0 8px;
           scrollbar-width: none;
         }
         .studio-nav-inner::-webkit-scrollbar { display: none; }
         .studio-tab {
           height: 44px;
           padding: 0 16px;
           border: none;
           border-bottom: 2px solid transparent;
           background: transparent;
           color: rgba(255,255,255,0.4);
           font-size: 12px;
           font-weight: 600;
           letter-spacing: 0.06em;
           cursor: pointer;
           white-space: nowrap;
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
           background: rgba(239,68,68,0.12);
           border-color: rgba(239,68,68,0.45);
           color: #f87171;
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
           flex-wrap: nowrap;
           align-items: stretch;
           width: 100%;
           min-width: 0;
           background: rgba(255,255,255,0.08);
           border: 1px solid rgba(255,255,255,0.1);
           border-radius: 3px;
           overflow: hidden;
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
  try {
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.pause();
      window.speechSynthesis.cancel();
    }
  } catch { }
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
  const { setListening, setStreaming, addMessage } = useAppStore();

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
    setStreaming(false);
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
      setStreaming(true);

      rec.onstart = () => {
        setListening(true);
        setStreaming(true);
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
      layout
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
        className="chat-flat"
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

        {/* Compact footer: debug/thread context sits tight against time/tok/ctx/sources */}
        <div
          style={{
            marginTop: 4,
            display: "flex",
            flexDirection: "column",
            gap: 3,
            minWidth: 0,
            width: "100%",
          }}
        >
          {!isUser && msg.operation ? (
            <OperationalStateCard
              state={msg.operation.state}
              success={msg.operation.success}
              executionId={msg.operation.executionId || msg.executionId}
              compact
            />
          ) : null}

          {!isUser && msg.docSources?.length ? (
            <details style={{ color: colors.textDim, fontSize: 11, minWidth: 0, maxWidth: "100%", margin: 0 }}>
              <summary style={{ cursor: "pointer", color: colors.text }}>
                Local sources ({msg.docSources.length})
              </summary>
              <div style={{ display: "grid", gap: 2, marginTop: 3, overflowWrap: "anywhere", wordBreak: "break-word" }}>
                {msg.docSources.map((source, index) => (
                  <div key={`${source.id || source.source || source.filename || "source"}:${index}`}>
                    {source.filename || source.source || source.id}
                    {typeof source.chunk === "number" ? ` · chunk ${source.chunk}` : ""}
                  </div>
                ))}
              </div>
            </details>
          ) : null}

          {(() => {
            const msgTokens = msg.usage?.tokens ?? estimateTokens(msg.text);
            const ctxUsed = msg.usage?.contextUsed ?? msgTokens;
            const ctxWindow = msg.usage?.contextWindow || contextWindow || 32768;
            const ctxPct = ctxWindow > 0 ? Math.min(100, Math.round((ctxUsed / ctxWindow) * 100)) : 0;
            const prov = msg.usage?.provider || providerLabel || "";
            const model = msg.usage?.model || modelLabel || "";
            return (
              <div
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
                }}
              >
                <span>{new Date(msg.at).toLocaleTimeString()}</span>
                <span style={{ opacity: 0.45 }}>·</span>
                <span
                  onMouseEnter={() => setMetaHover(true)}
                  onMouseLeave={() => setMetaHover(false)}
                  style={{
                    cursor: "default",
                    borderBottom: "1px dotted rgba(255,255,255,0.18)",
                    paddingBottom: 1,
                  }}
                >
                  ~{formatTokenCount(msgTokens)} tok
                  {!isUser ? (
                    <>
                      <span style={{ opacity: 0.45 }}> · </span>
                      {ctxPct}% ctx
                    </>
                  ) : null}
                </span>
                {!isUser && !stillTyping && msg.embeds?.length ? (
                  <ChatEmbedFooter embeds={msg.embeds} colors={colors} />
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

const ThinkingActivityCard: React.FC<{ item: { kind: "thinking"; id: string; content: string; at: number; steps?: ThinkingStep[]; request_id?: string } }> = ({ item }) => {
  // One clean list: drop pure thought dumps; keep at most one soft "thinking…" if nothing else yet.
  const rawSteps = item.steps || [];
  const workSteps = rawSteps.filter((s) => s.type !== "thought");
  const hasRealWork = workSteps.some(
    (s) => !/^(thinking|thinking…|waiting|working)(\s|\.|…)*$/i.test(String(s.content || "").trim())
  );
  const steps: ThinkingStep[] = hasRealWork
    ? workSteps.filter(
        (s) => !/^(thinking|thinking…|waiting|working)(\s|\.|…)*$/i.test(String(s.content || "").trim())
      )
    : workSteps.length > 0
      ? [workSteps[workSteps.length - 1]] // single placeholder spinner row
      : [];
  const anyRunning = steps.some((s) => s.status === "running");
  const containerRef = useRef<HTMLDivElement>(null);

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
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      style={{ display: "flex", justifyContent: "flex-start", width: "100%", padding: "2px 0 2px" }}
      ref={containerRef}
    >
      <div className="chat-flat" style={{ width: "100%", maxWidth: "100%", color: colors.textDim }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {steps.map((step) => {
            const failed = step.status === "failed";
            const running = step.status === "running";
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
                  {running ? (
                    <SquareLoader size={9} color="rgba(255,255,255,0.85)" />
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
                        background: "rgba(255,255,255,0.28)",
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

const ActivityCard: React.FC<{ item: ActivityItem }> = ({ item }) => {
  if (item.kind === "thinking") {
    return <ThinkingActivityCard item={item} />;
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
      layout
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
  const toolName = action?.tool || "unknown";
  const kwargs = action?.kwargs || {};
  const safeArgumentEntries = Object.entries(kwargs).filter(([key]) =>
    !/(content|text|message|password|token|secret|api[_-]?key|credential)/i.test(key)
  );
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
        style={{
          maxWidth: "96%",
          width: "fit-content",
          background: colors.panel2,
          color: colors.text,
          border: `1px solid ${colors.line}`,
          borderRadius: 14,
          padding: "14px 16px",
          boxShadow: `0 0 20px ${riskColor}15`,
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
            onClick={onConfirm}
            disabled={missingPolicyFlags.length > 0}
            style={{
              flex: 1,
              padding: "8px 16px",
              fontSize: 13,
              fontWeight: 600,
              borderRadius: 8,
              border: "none",
              background: riskLevel === "destructive" ? "#ef4444" : colors.accent,
              color: "#fff",
              cursor: "pointer",
              minWidth: 80,
            }}
          >
            Confirm
          </button>
          {dryRunAvailable && onDryRun && (
            <button
              onClick={onDryRun}
              style={{
                flex: 1,
                padding: "8px 16px",
                fontSize: 13,
                fontWeight: 600,
                borderRadius: 8,
                border: `1px solid ${colors.line}`,
                background: "transparent",
                color: colors.text,
                cursor: "pointer",
                minWidth: 80,
              }}
            >
              Dry Run
            </button>
          )}
          <button
            onClick={onCancel}
            style={{
              flex: 1,
              padding: "8px 16px",
              fontSize: 13,
              fontWeight: 600,
              borderRadius: 8,
              border: `1px solid ${colors.line}`,
              background: "transparent",
              color: colors.textDim,
              cursor: "pointer",
              minWidth: 80,
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    </motion.div>
  );
};

export const Dashboard: React.FC = () => {
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
    selectedVoice,
    setSelectedVoice,
  } = useAppStore();

  const unlockSpeech = () => {
    try {
      stopTts();
      if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
      setSpeechEnabled(true);

      const u = new SpeechSynthesisUtterance(" ");
      u.volume = 0;
      u.onend = () => {
        try {
          window.speechSynthesis.cancel();
        } catch { }
      };
      try {
        window.speechSynthesis.resume();
      } catch { }
      window.speechSynthesis.speak(u);
      window.setTimeout(() => {
        try {
          window.speechSynthesis.cancel();
        } catch { }
      }, 120);
    } catch {
      // ignore
    }
  };

  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);

  const silenceTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    const updateVoices = () => {
      setVoices(window.speechSynthesis.getVoices());
    };
    updateVoices();
    window.speechSynthesis.onvoiceschanged = updateVoices;

    // Hotkey and Transcript Auto-Send Logic
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key.toLowerCase() === "m") {
        e.preventDefault();
        const listeningState = useAppStore.getState().listening;
        if (listeningState) {
          stop();
          useAppStore.getState().setListening(false);
          useAppStore.getState().setStreaming(false);
        } else {
          start();
        }
      }
    };

    const handleTranscript = (e: Event) => {
      const customEvent = e as CustomEvent<string>;
      const text = customEvent.detail;
      setInput(text);

      // Reset the silence timer
      if (silenceTimeoutRef.current) {
        window.clearTimeout(silenceTimeoutRef.current);
      }

      // Auto-send after 2 seconds of silence
      silenceTimeoutRef.current = window.setTimeout(() => {
        const state = useAppStore.getState();
        stop();
        state.setListening(false);
        state.setStreaming(false);
      }, 2000);
    };

    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("echospeak-transcript", handleTranscript);

    return () => {
      window.speechSynthesis.onvoiceschanged = null;
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("echospeak-transcript", handleTranscript);
      if (silenceTimeoutRef.current) window.clearTimeout(silenceTimeoutRef.current);
    };
  }, []);

  const [input, setInput] = useState("");
  const workspaceMode = "auto";
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [taskPlans, setTaskPlans] = useState<TaskPlanEntry[]>([]);
  const activeTaskPlanIdRef = useRef<string | null>(null);
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
  const prependResearchRun = useResearchStore((state) => state.prependRun);
  const replaceResearchRuns = useResearchStore((state) => state.replaceRuns);
  const clearResearchRuns = useResearchStore((state) => state.clearRuns);
  const [leftTab, setLeftTab] = useState<"chat" | "research" | "memory" | "docs" | "settings" | "capabilities" | "approvals" | "executions" | "projects" | "routines" | "soul" | "services" | "avatar_editor">("chat");
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
  const [visualizerPin, setVisualizerPin] = useState<null | "ring" | "research" | "coding" | "tasks">(null);
  const [liveReplyDraft, setLiveReplyDraft] = useState("");
  const liveReplyDraftRef = useRef("");
  const [codeSessions, setCodeSessions] = useState<CodeDiffSession[]>([]);
  const [liveTerminal, setLiveTerminal] = useState<LiveTerminalEntry[]>([]);
  const [liveFileChanges, setLiveFileChanges] = useState<LiveFileChange[]>([]);
  const [codeRefreshToken, setCodeRefreshToken] = useState(0);
  const [avatarConfig, setAvatarConfig] = useState<AvatarConfig>(defaultAvatarConfig);
  const [memoryItems, setMemoryItems] = useState<MemoryItem[]>([]);
  const [memoryCount, setMemoryCount] = useState<number>(0);
  const [memoryLoading, setMemoryLoading] = useState<boolean>(false);
  const [memoryDoctor, setMemoryDoctor] = useState<MemoryDoctorReport | null>(null);
  const [memoryDoctorLoading, setMemoryDoctorLoading] = useState<boolean>(false);
  const [docItems, setDocItems] = useState<DocumentItem[]>([]);
  const [servicesHeartbeatStatus, setServicesHeartbeatStatus] = useState<any>(null);
  const [servicesHeartbeatHistory, setServicesHeartbeatHistory] = useState<any[]>([]);
  const [servicesTelegramStatus, setServicesTelegramStatus] = useState<any>(null);
  const [servicesDiscordStatus, setServicesDiscordStatus] = useState<any>(null);
  const [servicesLoading, setServicesLoading] = useState<boolean>(false);
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
  const latestCodeFilenameRef = useRef<string | null>(null);
  const [capabilitiesData, setCapabilitiesData] = useState<any>(null);
  const [codingReadiness, setCodingReadiness] = useState<CodingReadiness | null>(null);
  const [codingReadinessLoading, setCodingReadinessLoading] = useState<boolean>(false);
  const [memoryFilterType, setMemoryFilterType] = useState<string>("");
  const [selectedMemoryIds, setSelectedMemoryIds] = useState<string[]>([]);
  const [editingMemoryId, setEditingMemoryId] = useState<string | null>(null);
  const [editingMemoryText, setEditingMemoryText] = useState<string>("");
  const [projects, setProjects] = useState<{
    id: string; name: string; description?: string; context_prompt?: string; tags?: string[];
    workspace_root?: string; archived?: boolean; git_metadata?: Record<string, any>;
  }[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string>("");
  const [folderDropActive, setFolderDropActive] = useState(false);
  const [projectsLoading, setProjectsLoading] = useState<boolean>(false);
  const [threadState, setThreadState] = useState<ThreadSessionState | null>(null);
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

  const [threads, setThreads] = useState<{ id: string; name: string; at: number; projectId?: string; messageCount?: number }[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string>("");
  const activeThreadIdRef = useRef<string>("");
  const activeStreamAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    activeThreadIdRef.current = activeThreadId;
    activeStreamAbortRef.current?.abort();
    activeStreamAbortRef.current = null;
  }, [activeThreadId]);

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
    try {
      const tid = encodeURIComponent(String(threadId || "").trim());
      const resp = await fetchWithTimeout(`${apiBase}/history?thread_id=${tid}`, undefined, 12000);
      if (!resp.ok) return;
      const data = await resp.json();
      if (activeThreadIdRef.current !== threadId) return;

      const turns: any[] = Array.isArray(data?.turns) ? data.turns : [];
      if (turns.length > 0) {
        const loadedMsgs: Message[] = [];
        const loadedActs: ActivityItem[] = [];
        const hydratedResearch: ResearchRun[] = [];
        const ctxWindow = Number(providerInfo?.context_window || 0) || 32768;

        for (const turn of turns) {
          const executionId = String(turn.execution_id || turn.execution?.id || "").trim();
          const turnStatus = String(turn.progress_status || turn.terminal_status || turn.status || "complete");
          const turnOpen = ["running", "in_progress", "started", "interrupted"].includes(
            String(turn.status || "").toLowerCase()
          ) || turnStatus === "interrupted";
          const baseAt = Number(turn.created_at || 0) * 1000 || Date.now();
          const doneAt = Number(turn.completed_at || turn.created_at || 0) * 1000 || baseAt + 1;

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
            const turnScopedState: OperationalThreadState = {
              mode: String(turn.execution?.mode || "chat"),
              phase: String(turn.execution?.phase || ""),
              execution_status: turnStatus === "interrupted" ? "in_progress" : String(turnStatus || "complete"),
              current_execution_id: executionId,
              last_execution_id: executionId,
              terminal_status: String(turn.terminal_status || turnStatus),
              safest_next_action:
                turnStatus && !["complete", "completed", "ready", ""].includes(String(turnStatus))
                  ? String(turn.verification?.next_action || turn.progress?.status || "")
                  : "",
              completed_actions: [],
              failed_actions: [],
              pending_actions: [],
              plan_steps: [],
              operation_details: {
                tools: (Array.isArray(turn.tool_runs) ? turn.tool_runs : [])
                  .filter((r: any) => {
                    const st = String(r.status || "").toLowerCase();
                    return !["cancelled", "canceled", "interrupted"].includes(st);
                  })
                  .map((r: any) => String(r.tool_name || ""))
                  .filter(Boolean),
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
              // Interrupted mid-run after browser refresh — show as failed/interrupted, not live.
              uiStatus = turnOpen ? "error" : "done";
            }
            const outText =
              String(outcome.output || outcome.error_message || outcome.error_code || "").trim() ||
              (uiStatus === "error" && turnOpen ? "Interrupted (page refresh or disconnect)" : "");
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
        // Historical research for Studio panel; chat embeds already on assistant messages.
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
    }
  }, [leftTab, activeThreadId]);

  useEffect(() => {
    refreshThreads();
    refreshProjects();
  }, []);

  const refreshThreads = async () => {
    try {
      const resp = await fetchWithTimeout(`${apiBase}/threads?limit=50`, undefined, 6000);
      if (!resp.ok) throw new Error(`Threads failed (${resp.status})`);
      const data = await resp.json();
      const items = Array.isArray(data) ? data : [];
      let retainedEmptyPlaceholder = false;
      const mapped = items.map((item: any) => ({
        id: String(item.thread_id || item.id || ""),
        name: String(item.title || item.name || "Session"),
        at: normalizeTimestampMs(item.last_active_at || item.created_at || Date.now()),
        projectId: String(item.project_id || ""),
        messageCount: Number(item.message_count || 0),
      })).filter((item: any) => {
        if (!item.id) return false;
        const placeholder = item.messageCount === 0 && /^(?:new|default)?\s*(?:session|thread)(?:\s+\d+)?$/i.test(item.name.trim());
        if (!placeholder) return true;
        if (retainedEmptyPlaceholder) return false;
        retainedEmptyPlaceholder = true;
        item.name = "New Session";
        return true;
      });
      if (mapped.length) {
        setThreads(mapped);
        setActiveThreadId((current) => mapped.some((item: any) => item.id === current) ? current : mapped[0].id);
      } else {
        await createNewThread();
      }
    } catch (e) {
      console.error("Failed to refresh threads:", e);
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
    setApprovalDecisionBusy(true);
    try {
      const resp = await fetchWithTimeout(`${apiBase}/approvals/${encodeURIComponent(approvalId)}/${decision}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }, 30000);
      if (!resp.ok) {
        const detail = await resp.text().catch(() => "");
        throw new Error(resp.status === 409 ? "That approval is stale; nothing was executed." : `Approval failed (${resp.status}): ${detail}`);
      }
      const data = (await resp.json()) as ApprovalDecisionEnvelope;
      if (data.thread_state) {
        setThreadState(data.thread_state);
        setLatestExecutionId(String(data.execution_id || data.thread_state.last_execution_id || ""));
      }
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
      const message = error instanceof Error ? error.message : String(error);
      setEchoReaction("error");
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: message, at: Date.now(), skipTypewriter: true });
    } finally {
      setApprovalDecisionBusy(false);
      await refreshThreadState(activeThreadId);
      await refreshPendingApproval(activeThreadId);
      await refreshApprovals(activeThreadId);
      await refreshExecutions(activeThreadId);
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
      const data = await res.json();
      setProjects(data.items || []);
    } catch (e) {
      console.error("Failed to load projects:", e);
    } finally {
      setProjectsLoading(false);
    }
  };

  const createNewThread = async (projectId: string = "") => {
    try {
      const resp = await fetch(`${apiBase}/threads`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "New Session", source: "web", project_id: projectId }),
      });
      if (!resp.ok) throw new Error(`Create thread failed (${resp.status})`);
      const data = await resp.json();
      const nextThread = { id: String(data.thread_id), name: String(data.title || "New Session"), at: normalizeTimestampMs(data.last_active_at || data.created_at || Date.now()), projectId: String(data.project_id || projectId || "") };
      // Abort previous Session stream before swapping UI state.
      activeStreamAbortRef.current?.abort();
      activeStreamAbortRef.current = null;
      activeThreadIdRef.current = nextThread.id;
      setThreads((prev) => [nextThread, ...prev.filter((item) => item.id !== nextThread.id)]);
      setActiveThreadId(nextThread.id);
      useAppStore.setState({ messages: [] });
      setActivities([]);
      setTaskPlans([]);
      activeTaskPlanIdRef.current = null;
      clearResearchRuns();
      latestCodeFilenameRef.current = null;
      setCodeSessions([]);
      setLiveTerminal([]);
      setLiveFileChanges([]);
      setCodeRefreshToken((n) => n + 1);
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
    } catch (e) {
      console.error("Failed to create thread:", e);
    }
  };

  const switchThread = (id: string) => {
    if (id === activeThreadId) return;
    // Abort synchronously BEFORE clearing UI so late NDJSON cannot repaint the new Session.
    activeStreamAbortRef.current?.abort();
    activeStreamAbortRef.current = null;
    activeThreadIdRef.current = id;
    setActiveThreadId(id);
    dispatchActivity({ type: "reset" });
    setStreaming(false);
    liveReplyDraftRef.current = "";
    setLiveReplyDraft("");
    setDocSources([]);
    toolInfoRef.current = {};
    // In a real app, we might fetch history from backend here.
    // For now, we'll clear local state to start fresh in the new context.
    useAppStore.setState({ messages: [] });
    setActivities([]);
    setTaskPlans([]);
    activeTaskPlanIdRef.current = null;
    clearResearchRuns();
    latestCodeFilenameRef.current = null;
    setCodeSessions([]);
    setLiveTerminal([]);
    setLiveFileChanges([]);
    setCodeRefreshToken((n) => n + 1);
    setPendingApproval(null);
    setApprovals([]);
    setExecutions([]);
    setSelectedTrace(null);
    setSelectedTraceId("");
    // Re-onboard if switching to a fresh state or keep history if backend supports it
    addMessage({
      id: crypto.randomUUID(),
      role: "assistant",
      text: "Switched session. How can I assist you in this context?",
      at: Date.now(),
    });
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
      else await createNewThread();
    }
  };

  const attachFolder = async (candidatePath: string = "") => {
    let path = candidatePath.trim();
    if (!path) {
      try {
        const picker = await fetch(`${apiBase}/projects/pick-folder`, { method: "POST" });
        if (picker.ok) path = String((await picker.json()).path || "");
      } catch { /* native picker may not be available outside the desktop host */ }
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
  const apiBase = useMemo(() => (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, ""), []);
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
    const delay = Math.min(6000, Math.round(600 * Math.pow(1.6, attempt)));
    backendRetryRef.current.attempt = Math.min(attempt + 1, 12);
    backendRetryRef.current.timer = window.setTimeout(() => {
      backendRetryRef.current.timer = null;
      refreshProviderInfo({ allowRetry: true });
    }, delay);
  };

  const refreshProviderInfo = async (opts: { allowRetry?: boolean } = {}) => {
    try {
      setProviderError(null);
      const resp = await fetchWithTimeout(`${apiBase}/provider`, undefined, 10000);
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
      const pretty = offline ? "Backend offline" : msg;
      setProviderError(offline && opts.allowRetry ? "Backend offline — retrying" : pretty);
      if (opts.allowRetry) scheduleBackendRetry();
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
      setSettingsDraft({ ...(effective || {}), ...(overrides || {}) });
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
      const resp = await fetchWithTimeout(`${apiBase}/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settingsDraft || {}),
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
      setSettingsDraft({ ...((data.settings as any) || {}), ...((data.overrides as any) || {}) });
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
    if (leftTab === "settings") {
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
    try {
      const tid = encodeURIComponent(String(activeThreadId || "").trim());
      const threadQs = tid ? `&thread_id=${tid}` : "";
      const resp = await fetchWithTimeout(`${apiBase}/memory?offset=0&limit=200${threadQs}`);
      if (!resp.ok) throw new Error(`Memory request failed (${resp.status})`);
      const data = (await resp.json()) as MemoryListResponse;
      setMemoryItems(Array.isArray(data.items) ? data.items : []);
      setMemoryCount(typeof data.count === "number" ? data.count : 0);
    } catch (e) {
      setMemoryItems([]);
      setMemoryCount(0);
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${String(e)}`, at: Date.now() });
    } finally {
      setMemoryLoading(false);
    }
  };

  const refreshMemoryDoctor = async () => {
    setMemoryDoctorLoading(true);
    try {
      const tid = encodeURIComponent(String(activeThreadId || "").trim());
      const qs = tid ? `?thread_id=${tid}&max_scan=300` : "?max_scan=300";
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

  const refreshCodingReadiness = async () => {
    setCodingReadinessLoading(true);
    try {
      const tid = encodeURIComponent(String(activeThreadId || "").trim());
      const qs = tid ? `?thread_id=${tid}` : "";
      const resp = await fetchWithTimeout(`${apiBase}/coding/readiness${qs}`, undefined, 7000);
      if (!resp.ok) throw new Error(`Coding readiness failed (${resp.status})`);
      const data = (await resp.json()) as CodingReadiness;
      setCodingReadiness(data);
    } catch (e) {
      console.error("Coding readiness error:", e);
    } finally {
      setCodingReadinessLoading(false);
    }
  };

  const deleteMemoryItem = async (id: string) => {
    try {
      const resp = await fetchWithTimeout(`${apiBase}/memory/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: [id], thread_id: activeThreadId }),
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
        body: JSON.stringify({ id: item.id, pinned: !Boolean(item.pinned), thread_id: activeThreadId }),
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
      const threadQs = tid ? `?thread_id=${tid}` : "";
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
      const resp = await fetchWithTimeout(`${apiBase}/documents`);
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
      const resp = await fetchWithTimeout(`${apiBase}/documents/upload`, { method: "POST", body: form }, 12000);
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
        body: JSON.stringify({ ids: [id] }),
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
      const resp = await fetchWithTimeout(`${apiBase}/documents/clear`, { method: "POST" });
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
    try {
      const body: any = { provider: next.provider };
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

  /**
   * ONE plan for the current turn only — sticky under tools/thinking.
   * Never inject historical task_plan rows into the timeline (that pinned
   * checklists high above the current conversation).
   */
  const liveTaskPlan = useMemo(() => {
    if (!taskPlans.length) return null;
    // Always the latest plan only (one checklist, current turn).
    const latest = taskPlans[taskPlans.length - 1];
    if (!latest?.plan?.tasks?.length) return null;
    return latest;
  }, [taskPlans]);

  const timeline = useMemo<TimelineItem[]>(() => {
    // Plans are NOT merged into history — only messages + activity cards.
    const merged: TimelineItem[] = [
      ...messages.map(
        (m): TimelineItem => ({
          kind: "message",
          id: m.id,
          at: m.at,
          msg: m,
        })
      ),
      ...activities.map(
        (a): TimelineItem => ({
          kind: "activity",
          id: a.id,
          at: a.at,
          item: a,
        })
      ),
    ];
    // Chronological with multi-beat contract:
    //   user → first spoken assistant beat (partial) → tool/search rows → final answer
    // Plan checklist is rendered separately under the stream (liveTaskPlan).
    const kindRank = (k: TimelineItem["kind"]) =>
      k === "message" ? 0 : k === "activity" ? 1 : 2;
    const isPartialBeat = (t: TimelineItem) => {
      if (t.kind !== "message") return false;
      const m = (t as Extract<TimelineItem, { kind: "message" }>).msg;
      return m.role === "assistant" && m.streamBeat === "partial";
    };
    const isToolActivity = (t: TimelineItem) =>
      t.kind === "activity" &&
      ((t as Extract<TimelineItem, { kind: "activity" }>).item.kind === "thinking" ||
        (t as Extract<TimelineItem, { kind: "activity" }>).item.kind === "tool");

    merged.sort((a, b) => {
      const dt = a.at - b.at;
      // Close in time: partial spoken beat always above tool/search activity.
      if (Math.abs(dt) < 180_000) {
        if (isPartialBeat(a) && isToolActivity(b)) return -1;
        if (isToolActivity(a) && isPartialBeat(b)) return 1;
        if (a.kind !== "message" && b.kind !== "message") {
          const ra = kindRank(a.kind);
          const rb = kindRank(b.kind);
          if (ra !== rb) return ra - rb;
        }
      }
      if (dt !== 0) return dt;
      if (isPartialBeat(a) && isToolActivity(b)) return -1;
      if (isToolActivity(a) && isPartialBeat(b)) return 1;
      return kindRank(a.kind) - kindRank(b.kind);
    });
    return merged;
  }, [messages, activities]);

  const lastMsgLen = messages.length ? (messages[messages.length - 1]?.text || "").length : 0;
  const activityLen = activities.length;
  const taskPlanLen = taskPlans.reduce((sum, entry) => sum + entry.plan.tasks.length + entry.plan.reflections.length, 0);

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
  };

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

  const sendText = async (overrideText?: string) => {
    const raw = overrideText ?? input;
    if (!raw.trim()) return;
    const streamThreadId = activeThreadId;
    activeStreamAbortRef.current?.abort();
    const streamController = new AbortController();
    activeStreamAbortRef.current = streamController;

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

    const desktopContext = shouldAttachMonitor(raw) ? clampContext(monitorText, 1200) : "";
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
    addMessage(userMsg);
    setInput("");
    setUserIsTyping(false);
    if (userTypingTimerRef.current) clearTimeout(userTypingTimerRef.current);
    setDocSources([]);
    // Turn-local research only — do not carry prior Session source cards into this answer.
    // Global research panel history is still available in Studio; chat embeds use turnResearchRuns.
    activeTaskPlanIdRef.current = null;
    // Fresh turn = fresh checklist only (no stacked plans from prior messages)
    setTaskPlans([]);
    liveReplyDraftRef.current = "";
    setLiveReplyDraft("");
    dispatchActivity({ type: "stream_start" });
    setStreaming(true);
    // Drop prior-turn tool metadata so done-labels never inherit stale queries
    // (e.g. Python search label leaking into a later GTA+FIFA turn).
    toolInfoRef.current = {};
    // Seed a visible "working" step immediately so chat always shows progress
    // (avatar can animate before the first tool/thinking event arrives).
    const runRequestId = crypto.randomUUID();
    const bootstrapStepId = `${runRequestId}:working`;
    /** Backend Turn id once create_execution emits turn_bound / final. */
    let durableTurnId = "";
    let finalHandled = false;
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
      return [
        ...pruned,
        {
          kind: "thinking" as const,
          id: crypto.randomUUID(),
          content: "thinking…",
          at: Date.now(),
          request_id: runRequestId,
          steps: [
            {
              id: bootstrapStepId,
              type: "tool" as const,
              content: "thinking…",
              status: "running" as const,
              at: Date.now(),
            },
          ],
        },
      ];
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
          if (evt.type === "task_plan") {
            const id = crypto.randomUUID();
            activeTaskPlanIdRef.current = id;
            return [
              ...prev,
              {
                id,
                at: eventAt,
                request_id: reqId || runRequestId,
                plan: taskPlanReducer(createEmptyTaskPlan(), evt),
              },
            ];
          }

          const activeId =
            activeTaskPlanIdRef.current ||
            [...prev].reverse().find((entry) => entry.request_id === reqId || entry.request_id === runRequestId)?.id ||
            [...prev].reverse().find((entry) => entry.plan.active)?.id;
          if (!activeId) return prev;

          return prev.map((entry) =>
            entry.id === activeId
              ? {
                  ...entry,
                  // Bump timestamp on every step so historical placement follows the turn
                  at: eventAt,
                  plan: taskPlanReducer(entry.plan, evt),
                }
              : entry
          );
        });
      };
      const upsertTool = (evt: AgentStreamEvent) => {
        if (evt.type === "tool_start") {
          // Scope tool metadata to this stream turn (avoid stale labels from prior turns)
          toolInfoRef.current[evt.id] = { name: evt.name, input: evt.input, requestId: runRequestId };
          dispatchActivity({ type: "tool_start", id: evt.id, name: evt.name });
          const toolNameStart = String(evt.name || "").toLowerCase();
          // Live Code workspace: real terminal process start (this session stream only)
          if (toolNameStart === "terminal_run") {
            const rawIn = String(evt.input || "");
            let command = rawIn;
            try {
              const j = JSON.parse(rawIn);
              if (j && typeof j === "object" && j.command) command = String(j.command);
            } catch {
              const m = rawIn.match(/['"]command['"]\s*:\s*['"]([^'"]+)['"]/i);
              if (m) command = m[1];
            }
            setLiveTerminal((prev) => [
              ...prev.filter((t) => t.id !== evt.id),
              {
                id: evt.id,
                command: command.replace(/\s+/g, " ").trim().slice(0, 500) || "(terminal)",
                output: "",
                status: "running",
                exitCode: null,
                running: true,
                at: normalizeTimestampMs(evt.at || Date.now()),
                turnId: durableTurnId || runRequestId,
                toolRunId: evt.id,
              },
            ]);
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
          dispatchActivity(toolFailed
            ? { type: "tool_error", id: evt.id, message: evt.outcome?.error_message || "Tool failed" }
            : { type: "tool_end", id: evt.id });
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
          // Capture code for Code workspace (real file bodies + terminal ToolRuns)
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
            const rawInput = info?.input || "";
            const rawOutput = String(evt.output || "");
            const echo = parseEchoFileBlock(rawOutput);
            const parsedIn = parseFileToolInput(rawInput);
            const pathFromSummary = (rawOutput.match(/(?:Wrote|Appended|Read|Deleted|Moved|Copied).+?(?:to|from)\s+(.+?)(?:\n|$)/i) || [])[1]?.trim() || "";
            const fullPath = echo?.path || parsedIn.path || pathFromSummary || "";
            const filename = basenamePath(fullPath) || basenamePath(parsedIn.path) || toolName || "output";
            const lang = langFromFilename(filename, toolName);
            const eventAt = normalizeTimestampMs(evt.at || Date.now());

            let fileBody = "";
            if (echo?.content != null && echo.content.length > 0) {
              fileBody = echo.content;
            } else if (toolName === "file_write" && parsedIn.content) {
              fileBody = parsedIn.content;
            } else if (toolName === "file_read" && rawOutput && !/^File not found|^Path not allowed|^Failed to read|^Path is a directory|^Binary file/i.test(rawOutput.trim())) {
              // legacy: raw file body without ECHO_FILE wrapper
              const stripped = rawOutput.replace(/^Read \d+ chars from .+\n?/i, "");
              fileBody = stripped || rawOutput;
            } else if (toolName === "terminal_run" || toolName === "notepad_write" || toolName === "artifact_write") {
              fileBody = rawOutput;
            }

            // Always open the coding pane on file ops (even errors — show the message)
            const displayContent =
              fileBody ||
              (rawOutput.trim() ? rawOutput : `(no content returned for ${filename})`);
            const isError =
              /^(File not found|Path not allowed|Failed to |Path is a directory|Binary file|Rejected stub)/i.test(
                rawOutput.trim()
              );

            if (toolName === "terminal_run") {
              const exitM = rawOutput.match(/ExitCode\s*=\s*(-?\d+)/i);
              const statusM = rawOutput.match(/Status\s*=\s*(\S+)/i);
              const modeM = rawOutput.match(/Mode\s*=\s*(\S+)/i);
              const reasonM = rawOutput.match(/Reason\s*=\s*(.+)/i);
              const exitCode = exitM ? Number(exitM[1]) : null;
              let command = rawInput;
              try {
                const j = JSON.parse(rawInput);
                if (j && typeof j === "object" && j.command) command = String(j.command);
              } catch {
                const m = rawInput.match(/['"]command['"]\s*:\s*['"]([^'"]+)['"]/i);
                if (m) command = m[1];
              }
              const bodyLines: string[] = [];
              let pastHeader = false;
              for (const line of rawOutput.split("\n")) {
                if (!pastHeader && /^(ExitCode|Status|Mode|Reason|DurationMs)\s*=/i.test(line)) continue;
                pastHeader = true;
                bodyLines.push(line);
              }
              setLiveTerminal((prev) => {
                const without = prev.filter((t) => t.id !== evt.id);
                return [
                  ...without,
                  {
                    id: evt.id,
                    command: String(command || "").replace(/\s+/g, " ").trim().slice(0, 500) || "(terminal)",
                    output: bodyLines.join("\n").trim() || rawOutput,
                    status: statusM?.[1] || (toolFailed ? "fail" : "pass"),
                    exitCode: Number.isFinite(exitCode as number) ? exitCode : null,
                    running: false,
                    at: eventAt,
                    turnId: durableTurnId || runRequestId,
                    toolRunId: evt.id,
                    mode: modeM?.[1] || "",
                    reason: reasonM?.[1]?.trim() || "",
                  },
                ];
              });
            } else {
              const action =
                toolName === "file_read"
                  ? "inspect"
                  : toolName === "file_delete"
                    ? "delete"
                    : toolName === "file_mkdir"
                      ? "mkdir"
                      : toolName.startsWith("file_")
                        ? toolName.replace("file_", "")
                        : "modify";
              setLiveFileChanges((prev) => [
                {
                  id: evt.id,
                  path: fullPath || filename,
                  toolName,
                  action,
                  status: toolFailed ? "failed" : "complete",
                  summary: (rawOutput.split("\n")[0] || toolName).slice(0, 200),
                  at: eventAt,
                  turnId: durableTurnId || runRequestId,
                  toolRunId: evt.id,
                  verified: !toolFailed && !isError,
                  currentContent: toolName === "file_write" || toolName === "file_read" ? fileBody || undefined : undefined,
                },
                ...prev.filter((c) => c.id !== evt.id),
              ].slice(0, 80));
            }

            latestCodeFilenameRef.current = filename;
            setCodeSessions((prev) => {
              const existing =
                prev.find((session) => session.filename === filename) ||
                prev.find((session) => fullPath && session.filename === fullPath) ||
                prev.find((session) => fullPath && session.filename === fullPath);
              let nextSession: CodeDiffSession;

              if (toolName === "file_read") {
                nextSession = {
                  filename: fullPath || filename,
                  language: lang,
                  originalContent: isError ? "" : displayContent,
                  currentContent: displayContent,
                  status: isError ? "output" : "read",
                  summary: isError
                    ? rawOutput.slice(0, 120)
                    : `Loaded ${displayContent.length.toLocaleString()} chars`,
                };
              } else if (toolName === "file_write") {
                const hasBody = Boolean(fileBody);
                nextSession = {
                  filename: fullPath || filename,
                  language: lang,
                  originalContent: existing?.originalContent || existing?.currentContent || "",
                  currentContent: hasBody ? fileBody : existing?.currentContent || displayContent,
                  status: isFileWriteSummary(rawOutput.split("\n")[0] || "") || hasBody ? "saved" : "draft",
                  summary: hasBody
                    ? `Saved ${fileBody.length.toLocaleString()} chars → ${basenamePath(fullPath || filename)}`
                    : (rawOutput.split("\n")[0] || `Write ${filename}`).slice(0, 160),
                };
              } else if (toolName === "terminal_run") {
                // Terminal lives in the Terminal pane — keep codeSessions for file diffs only
                return prev;
              } else {
                nextSession = {
                  filename: fullPath || filename,
                  language: lang,
                  originalContent: existing?.originalContent || "",
                  currentContent: displayContent,
                  status: "output",
                  summary: undefined,
                };
              }

              const [nextSessions] = replaceCodeSession(prev, nextSession);
              return nextSessions;
            });
            setCodeRefreshToken((n) => n + 1);
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
          dispatchActivity({ type: "tool_error", id: evt.id, message: evt.error });
          const info = toolInfoRef.current[evt.id];
          const toolName = info?.name || "tool";
          if (String(toolName || "").toLowerCase() === "terminal_run") {
            setLiveTerminal((prev) =>
              prev.map((t) =>
                t.id === evt.id
                  ? {
                      ...t,
                      running: false,
                      status: "fail",
                      output: t.output || String(evt.error || "Terminal error"),
                      at: normalizeTimestampMs(evt.at || Date.now()),
                    }
                  : t,
              ),
            );
          }
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
        if (!isStreamEventCurrent(streamThreadId, activeThreadIdRef.current, streamController.signal.aborted)) break;
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx = buffer.indexOf("\n");
        while (idx !== -1) {
          const line = buffer.slice(0, idx).trim();
          buffer = buffer.slice(idx + 1);
          idx = buffer.indexOf("\n");
          if (!line) continue;

          let evt: AgentStreamEvent;
          try {
            evt = JSON.parse(line) as AgentStreamEvent;
          } catch (e) {
            continue;
          }
          if (!isStreamEventCurrent(streamThreadId, activeThreadIdRef.current, streamController.signal.aborted)) continue;

          if (evt.type === "turn_bound") {
            const execId = String(evt.execution_id || evt.turn_id || "").trim();
            if (execId) {
              durableTurnId = execId;
              setLatestExecutionId(execId);
              // Remap any live Code rows that still carry the client stream key.
              setLiveTerminal((prev) =>
                prev.map((t) => (t.turnId === runRequestId ? { ...t, turnId: execId } : t))
              );
              setLiveFileChanges((prev) =>
                prev.map((c) => (c.turnId === runRequestId ? { ...c, turnId: execId } : c))
              );
            }
          } else if (evt.type === "task_plan" || evt.type === "task_step" || evt.type === "task_reflection") {
            upsertTaskPlan(evt);
            if (evt.type === "task_step" && evt.data?.status) {
              dispatchActivity({ type: "task_step", status: String(evt.data.status) });
            }
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
              dispatchActivity({ type: "agent_token", token: tok });
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
            // Multi-beat: seal the chatty preamble (e.g. "doing great") before tools run.
            const text = String(evt.response || liveReplyDraftRef.current || "").trim();
            if (!text) continue;
            if (partialReplies.some((p) => p.trim() === text)) continue;
            partialReplies.push(text);
            const ctxWindow = Number(providerInfo?.context_window || 0) || 32768;
            const beatAt = Date.now();
            sawPartialBeat = true;
            // Tools must render strictly below this spoken beat (across all multi-beat turns).
            toolsAfterPartialAt = beatAt + 10;
            addMessage({
              id: crypto.randomUUID(),
              role: "assistant",
              text,
              at: beatAt,
              skipTypewriter: true,
              streamBeat: "partial",
              usage: buildMessageUsage(text, useAppStore.getState().messages, ctxWindow, {
                provider: providerInfo?.provider,
                model: providerInfo?.model,
              }),
            });
            liveReplyDraftRef.current = "";
            setLiveReplyDraft("");
            // Speak this beat now — tools may follow, then a second reply.
            if (evt.speak !== false) {
              void speakText(text);
            }
            // Move the thinking/tool card under this beat. Keep any tool rows already
            // recorded (they may have finished before this event was painted).
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
            dispatchActivity({ type: "thinking", content: "working" });
          } else if (evt.type === "thinking") {
            const content = (evt.content || "").trim();
            const reqId = eventRequestId(evt);
            if (content) {
              dispatchActivity({ type: "thinking", content });
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
            dispatchActivity({ type: "status_mode", mode, tool: (evt as any).tool });
          } else if (evt.type === "error") {
            setStreaming(false);
            dispatchActivity({ type: "error", message: evt.message });
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
            let reply = String(evt.response || liveDraft || "").trim();
            // Drop any mid-turn beats already committed as partial_reply.
            for (const part of [...partialReplies, ...(Array.isArray(evt.partial_replies) ? evt.partial_replies : [])]) {
              const p = String(part || "").trim();
              if (!p) continue;
              if (reply === p) {
                reply = "";
                break;
              }
              if (reply.startsWith(p)) {
                reply = reply.slice(p.length).replace(/^[\s\n\-–—]+/, "").trim();
              }
            }
            // Prefer remaining live draft (post-tool generation) when backend still echoes preamble.
            if ((!reply || partialReplies.some((p) => reply === p.trim())) && liveDraft) {
              let draft = liveDraft;
              for (const part of partialReplies) {
                const p = part.trim();
                if (draft.startsWith(p)) draft = draft.slice(p.length).replace(/^[\s\n\-–—]+/, "").trim();
              }
              if (draft) reply = draft;
            }

            if (evt.execution_id) {
              durableTurnId = String(evt.execution_id);
            }
            const executionStatus = String(evt.thread_state?.execution_status || "");
            dispatchActivity({
              type: "final",
              response: reply || partialReplies[partialReplies.length - 1] || "",
              executionStatus,
              success: evt.success,
            });
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
            }

            liveReplyDraftRef.current = "";
            setLiveReplyDraft("");
            setStreaming(false);

            // Second beat (tool result): only add/speak if there's new content after partials.
            if (reply && !partialReplies.some((p) => p.trim() === reply)) {
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
              const liveOpState = evt.thread_state
                ? ({
                    ...evt.thread_state,
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
              const spoken = (evt.spoken_text || "").trim();
              const speakVal = spoken && spoken === reply ? spoken : reply;
              void speakText(speakVal);
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
            void refreshThreads();
          }
        }
      }
    } catch (err) {
      if (streamController.signal.aborted) {
        // Aborted (Session switch / cancel): never paint error into the next Session.
        return;
      }
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
      const owned = activeStreamAbortRef.current === streamController;
      const sameThread = isStreamThreadCurrent(streamThreadId, activeThreadIdRef.current);
      const aborted = streamController.signal.aborted;

      if (owned) {
        activeStreamAbortRef.current = null;
      }

      // Always clear phase machine so tool/search never sticks after switch or cancel.
      if (aborted || !sameThread) {
        dispatchActivity({ type: "reset" });
      } else {
        dispatchActivity({ type: "stream_end" });
      }

      // Do not mutate chat of a different Session (switch already cleared UI).
      if (!sameThread) {
        return;
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
        void speakText(orphan);
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
    }
  };

  const { start, stop } = useMicStreamer((t: string) => {
    sendText(t);
  });

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
  }, [apiBase]);

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

        const ws = new WebSocket(gatewayUrl);
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
    if (leftTab === "memory") {
      refreshMemory();
      refreshMemoryDoctor();
    }
    if (leftTab === "docs") refreshDocuments();
    if (leftTab === "soul") refreshSoul();
    if (leftTab === "services") refreshServices();
    if (leftTab === "projects") refreshProjects();
    if (leftTab === "capabilities") {
      refreshCodingReadiness();
    }
    if (leftTab === "approvals") {
      refreshPendingApproval();
      refreshApprovals();
    }
    if (leftTab === "executions") {
      refreshExecutions();
      if (latestTraceId) loadTrace(latestTraceId);
    }
  }, [leftTab]);

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
    const listener = () => stop();
    window.addEventListener("beforeunload", listener);
    return () => window.removeEventListener("beforeunload", listener);
  }, [stop]);

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
  const studioTabs: { id: typeof leftTab; label: string; group: string }[] = [
    { id: "memory", label: "Memory", group: "Knowledge" },
    { id: "docs", label: "Docs", group: "Knowledge" },
    { id: "settings", label: "Settings", group: "Config" },
    { id: "capabilities", label: "Tools", group: "Config" },
    { id: "soul", label: "Soul", group: "Config" },
    { id: "avatar_editor", label: "Avatar", group: "Config" },
    { id: "approvals", label: "Approvals", group: "Automation" },
    { id: "executions", label: "Executions", group: "Automation" },
    { id: "projects", label: "Projects", group: "Automation" },
    { id: "routines", label: "Routines", group: "Automation" },
    { id: "services", label: "Services", group: "Automation" },
  ];
  const studioOpen = leftTab !== "chat" && leftTab !== "research";
  const studioActiveTab = studioTabs.find((t) => t.id === leftTab);
  const closeStudio = () => {
    setLeftTab("chat");
    setShowVisualizer(true);
  };

  const shellColumns = runtimeGridColumns({
    sidebarVisible: showSidebar,
    sidebarCollapsed: sidebarCollapsed || narrowLayout,
    visualizerVisible: showVisualizer && !narrowLayout,
    visualizerDensity,
  });

  return (
    <div
      className="echo-root"
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
        className={"app-shell" + (studioOpen ? " is-studio-covered" : "")}
        style={{
          gridTemplateColumns: shellColumns,
        }}
        aria-hidden={studioOpen || undefined}
      >
        {showSidebar ? <ProjectSidebar
          collapsed={sidebarCollapsed || narrowLayout}
          projects={projects}
          sessions={threads}
          activeProjectId={activeProjectId}
          activeSessionId={activeThreadId}
          activeView={leftTab === "research" ? "research" : leftTab !== "chat" ? "studio" : visualizerPin === "coding" ? "code" : visualizerPin === "tasks" ? "tasks" : "avatar"}
          onToggleCollapsed={() => setSidebarCollapsed(v => !v)}
          onNewSession={createNewThread}
          onAddFolder={() => void attachFolder()}
          onSelectSession={switchThread}
          onSelectProject={async (id) => {
            const existing = threads.find(item => item.projectId === id);
            if (existing) switchThread(existing.id);
            else await createNewThread(id);
          }}
          onDetachProject={async () => {
            const response = await fetch(`${apiBase}/projects/deactivate?thread_id=${encodeURIComponent(activeThreadId)}`, { method: "POST" });
            if (!response.ok) return;
            const data = await response.json();
            setActiveProjectId("");
            setThreads(items => items.map(item => item.id === activeThreadId ? { ...item, projectId: "" } : item));
            if (data.thread_state) setThreadState(data.thread_state);
            // Clear live Code projection so detached Project cannot linger in UI.
            setLiveTerminal([]);
            setLiveFileChanges([]);
            setCodeSessions([]);
            setCodeRefreshToken((n) => n + 1);
          }}
          onRenameSession={(id, title) => void renameThread(id, title)}
          onDeleteSession={(id) => void deleteThread(id)}
          onDeleteProject={async (id) => {
            const response = await fetch(`${apiBase}/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
            if (!response.ok) return;
            setThreads(items => items.map(item => item.projectId === id ? { ...item, projectId: "" } : item));
            if (activeProjectId === id) { setActiveProjectId(""); await refreshThreadState(activeThreadId); }
            await refreshProjects();
          }}
          onView={(view) => {
            if (view === "studio") { setLeftTab("memory"); return; }
            setLeftTab(view === "research" ? "research" : "chat");
            setShowVisualizer(true);
            setVisualizerPin(view === "code" ? "coding" : view === "tasks" ? "tasks" : view === "research" ? "research" : "ring");
          }}
        /> : null}
        {showVisualizer && !narrowLayout ? (
          <div className="visualizer-pane">
            {/* Mode Indicator Tabs */}
            <div style={{
              display: "none",
              gap: 0,
              padding: 0,
              justifyContent: "center",
              alignItems: "center",
              background: "rgba(10, 12, 16, 0.85)",
              borderRadius: "4px",
              border: "1px solid rgba(255,255,255,0.12)",
              boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
              backdropFilter: "blur(20px)",
              WebkitBackdropFilter: "blur(20px)",
              margin: "12px auto 8px auto",
              width: "fit-content",
              zIndex: 10,
              overflow: "hidden",
            }}>
              {(["ring", "research", "coding", "tasks"] as const).map((m) => {
                const effectiveMode = visualizerPin || (agentMode === "research" ? "research" : agentMode === "coding" ? "coding" : "ring");
                const isActive = effectiveMode === m;
                const isPinned = visualizerPin === m;
                const icons: Record<string, React.ReactNode> = {
                  ring: (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="3" y="3" width="18" height="18" rx="3" />
                      <circle cx="9" cy="10" r="1.5" fill="currentColor" stroke="none" />
                      <circle cx="15" cy="10" r="1.5" fill="currentColor" stroke="none" />
                      <path d="M9 15c0 0 1 1.5 3 1.5s3-1.5 3-1.5" />
                    </svg>
                  ),
                  research: (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="11" cy="11" r="7" />
                      <path d="M21 21l-4.35-4.35" />
                    </svg>
                  ),
                  coding: (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="16 18 22 12 16 6" />
                      <polyline points="8 6 2 12 8 18" />
                    </svg>
                  ),
                  tasks: (
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="9 11 12 14 22 4" />
                      <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
                    </svg>
                  ),
                };
                const labels: Record<string, string> = {
                  ring: "Avatar",
                  research: "Research",
                  coding: "Code",
                  tasks: "Tasks",
                };
                return (
                  <button
                    key={m}
                    onClick={() => setVisualizerPin(isPinned ? null : m)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "7px",
                      padding: "13px 20px",
                      borderRadius: "0px",
                      fontSize: 13,
                      fontWeight: 600,
                      cursor: "pointer",
                      border: "none",
                      borderRight: m === "tasks" ? "none" : "1px solid rgba(255,255,255,0.08)",
                      background: isActive
                        ? "linear-gradient(180deg, rgba(255,255,255,0.13) 0%, rgba(255,255,255,0.03) 100%)"
                        : "transparent",
                      color: isActive ? "#ffffff" : "rgba(255,255,255,0.4)",
                      transition: "all 0.2s ease-in-out",
                      boxShadow: isActive ? "inset 0 -2px 0 rgba(140,180,255,0.85)" : "none",
                      textDecoration: "none",
                      letterSpacing: "0.01em",
                    }}
                  >
                    <span style={{ opacity: isActive ? 1 : 0.65, display: "flex" }}>
                      {icons[m]}
                    </span>
                    <span style={{ textShadow: isActive ? "0 0 10px rgba(255,255,255,0.35)" : "none" }}>
                      {labels[m]}
                    </span>
                  </button>
                );
              })}
            </div>
            {/* Visualizer Content — workspace modes fill the cell; avatar stays centered */}
            {(() => {
              const effectiveMode = visualizerPin || (agentMode === "research" ? "research" : agentMode === "coding" ? "coding" : "ring");
              const isAvatar = effectiveMode === "ring";
              return (
                <div className={`visualizer-pane-body ${isAvatar ? "is-avatar" : "is-workspace"}`}>
                  {(() => {
                    if (effectiveMode === "research") {
                      return (
                        <div style={{ width: "100%", height: "100%", minHeight: 0, minWidth: 0, padding: "12px 16px 16px", overflowY: "auto", overflowX: "hidden", display: "flex", flexDirection: "column", gap: 12, boxSizing: "border-box" }}>
                          <div style={{ fontSize: 13, fontWeight: 700, color: "rgba(255,255,255,0.5)", textTransform: "uppercase", letterSpacing: 1.5, padding: "8px 0" }}>
                            🔍 Research Feed
                          </div>
                          {research.length === 0 ? (
                            <div style={{ textAlign: "center", color: "rgba(255,255,255,0.25)", fontSize: 13, padding: 40, fontStyle: "italic" }}>
                              Research results will appear here when the agent searches the web...
                            </div>
                          ) : (
                            research.slice(0, 8).map((group, gi) => (
                              <motion.div
                                key={group.id}
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ duration: 0.35, delay: gi * 0.05 }}
                                style={{
                                  background: "rgba(255,255,255,0.03)",
                                  border: "1px solid rgba(255,255,255,0.08)",
                                  borderRadius: 14,
                                  padding: 16,
                                }}
                              >
                                <div style={{ fontSize: 12, fontWeight: 600, color: "rgba(139,92,246,0.9)", marginBottom: 10, display: "flex", alignItems: "center", gap: 8 }}>
                                  <span style={{ fontSize: 14 }}>🔎</span>
                                  <span style={{ fontStyle: "italic" }}>
                                    "{group.query}"
                                  </span>
                                </div>
                                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                                  {group.evidence.slice(0, 5).map((r, ri) => (
                                    <motion.div
                                      key={r.id || ri}
                                      initial={{ opacity: 0, x: -10 }}
                                      animate={{ opacity: 1, x: 0 }}
                                      transition={{ duration: 0.25, delay: ri * 0.08 }}
                                      style={{
                                        display: "flex",
                                        flexDirection: "column",
                                        gap: 3,
                                        padding: "8px 12px",
                                        borderRadius: 10,
                                        background: "rgba(255,255,255,0.02)",
                                        borderLeft: "3px solid rgba(139,92,246,0.4)",
                                      }}
                                    >
                                      <div style={{ fontSize: 12, fontWeight: 600, color: "rgba(96,165,250,0.9)" }}>
                                        {r.title || r.url}
                                      </div>
                                      <div style={{ fontSize: 10, color: "rgba(255,255,255,0.3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                        {r.url}
                                      </div>
                                      {(r.summary || r.snippet) && (
                                        <div style={{ fontSize: 11, color: "rgba(255,255,255,0.5)", lineHeight: 1.4 }}>
                                          {(r.summary || r.snippet).slice(0, 150)}{(r.summary || r.snippet).length > 150 ? "…" : ""}
                                        </div>
                                      )}
                                    </motion.div>
                                  ))}
                                </div>
                              </motion.div>
                            ))
                          )}
                        </div>
                      );
                    }
                    if (effectiveMode === "coding") {
                      return (
                        <div style={{ flex: 1, width: "100%", height: "100%", minHeight: 0, minWidth: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
                          <CodeWorkspace
                            apiBase={apiBase}
                            threadId={activeThreadId || "default"}
                            liveTerminal={liveTerminal}
                            liveChanges={liveFileChanges}
                            codeSessions={codeSessions}
                            pendingConfirmPath={
                              pendingApproval?.has_pending && pendingApproval?.action?.tool === "file_write"
                                ? String(pendingApproval?.action?.kwargs?.path || "")
                                : undefined
                            }
                            onConfirmSave={() => sendText("confirm")}
                            onCancelSave={() => sendText("cancel")}
                            refreshToken={codeRefreshToken}
                          />
                        </div>
                      );
                    }
                    if (effectiveMode === "tasks") {
                      return (
                        <div style={{ flex: 1, width: "100%", height: "100%", minHeight: 0, minWidth: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
                          <TodoPanel apiBase={apiBase} colors={colors} variant="visualizer" />
                        </div>
                      );
                    }
                    // Avatar phase is driven by the same agentActivity reducer as chat.
                    const phase = agentActivity.phase;
                    const isThinking =
                      !listening &&
                      !speaking &&
                      (phase === "thinking" ||
                        phase === "streaming_reply" ||
                        phase === "task_running" ||
                        phase.startsWith("tool_"));
                    const currentToolCategory = (
                      agentActivity.activeToolName
                        ? getToolCategory(agentActivity.activeToolName)
                        : toolCategoryFromPhase(phase)
                    ) as ToolCategory;
                    const activeToolName = agentActivity.activeToolName || undefined;
                    const thinkingText =
                      phase === "awaiting_confirm"
                        ? "Awaiting confirmation"
                        : phase === "error"
                          ? agentActivity.lastError || "Error"
                          : phase === "tool_search"
                            ? "Searching"
                            : phase === "streaming_reply"
                              ? "Writing"
                              : agentActivity.activeToolName
                                ? getToolDisplayDetails(agentActivity.activeToolName, "")
                                : agentActivity.label;
                    const pendingConfirm =
                      agentActivity.pendingConfirmation || Boolean(pendingApproval?.has_pending);
                    return (
                      <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center", transformOrigin: "center center" }}>
                        <SquareAvatarVisual
                          speaking={speaking}
                          backendOnline={backendOnline}
                          isThinking={isThinking}
                          thinkingText={thinkingText}
                          activeToolName={activeToolName}
                          heartbeatEnabled={settingsDraft?.heartbeat_enabled}
                          toolCategory={currentToolCategory}
                          userIsTyping={userIsTyping}
                          pendingConfirmation={pendingConfirm}
                          reaction={echoReaction}
                          onReactionDone={() => setEchoReaction(null)}
                          spotifyPlaying={spotifyPlaying?.is_playing ? spotifyPlaying : null}
                          avatarConfig={avatarConfig}
                        />
                      </div>
                    );
                  })()}
                </div>
              );
            })()}
          </div>
        ) : null}
        <div className="glow-panel">
          <div className="panel-header">
            <div className="title">
              <img src="/logo.png" alt="Logo" style={{ width: 14, height: 14, borderRadius: 2 }} />
              <span>EchoSpeak</span>
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
                  setLeftTab("memory");
                }}
                title={studioOpen ? "Close Studio" : "Open Studio"}
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
                Studio
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
                    { id: 'automation', label: 'Automation', icon: '🤖', tabs: [{ id: 'approvals', label: 'Approvals' }, { id: 'executions', label: 'Executions' }, { id: 'projects', label: 'Projects' }, { id: 'routines', label: 'Routines' }, { id: 'services', label: 'Services' }] },
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
                          { id: 'automation', label: 'Automation', icon: '🤖', tabs: [{ id: 'approvals', label: 'Approvals' }, { id: 'executions', label: 'Executions' }, { id: 'projects', label: 'Projects' }, { id: 'routines', label: 'Routines' }, { id: 'services', label: 'Services' }] },
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
                  <div className="chat-scroll" style={{ flex: 1 }} ref={chatScrollRef} onScroll={onChatScroll}>
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
                        ) : t.kind === "task_plan" ? (
                          <TaskChecklist key={`plan-${t.id}`} plan={t.entry.plan} />
                        ) : (
                          <ActivityCard key={`act-${t.id}`} item={t.item} />
                        )
                      )}
                    </AnimatePresence>
                    {/* Current-turn plan only — always under latest tools, never mid-history */}
                    {liveTaskPlan && liveTaskPlan.plan.tasks.length > 0 ? (
                      <div
                        key={`live-plan-${liveTaskPlan.id}`}
                        style={{ width: "100%", padding: "4px 4px 6px" }}
                      >
                        <TaskChecklist plan={liveTaskPlan.plan} />
                      </div>
                    ) : null}
                    {pendingApproval?.has_pending && pendingApproval.action ? (
                      <div style={{ width: "100%", padding: "2px 4px 4px" }}>
                        <OperationalStateCard
                          state={threadState}
                          approval={{
                            ...pendingApproval.action,
                            policy_flags: pendingApproval.policy_flags || pendingApproval.action.policy_flags,
                            session_permissions: pendingApproval.session_permissions || pendingApproval.action.session_permissions,
                          }}
                          busy={approvalDecisionBusy}
                          onDecision={decideApproval}
                          compact
                        />
                      </div>
                    ) : null}
                    {!pendingApproval?.has_pending && threadState && threadState.mode !== "chat" &&
                    messages[messages.length - 1]?.operation?.executionId !== threadState.last_execution_id ? (
                      <div style={{ width: "100%", padding: "2px 4px 4px" }}>
                        <OperationalStateCard state={threadState} compact />
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
                    {/* Footer spinner only if timeline has no running step yet (avoids double spinners). */}
                    {streaming &&
                    !liveReplyDraft &&
                    !activities.some(
                      (a) => a.kind === "thinking" && (a.steps || []).some((s) => s.status === "running")
                    ) ? (
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          padding: "6px 4px 10px",
                          fontSize: 12,
                          color: "rgba(255,255,255,0.45)",
                          fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                          letterSpacing: "0.04em",
                        }}
                      >
                        <SquareLoader size={11} color="rgba(255,255,255,0.85)" />
                        <span>{(agentActivity.label || "Working").toLowerCase()}</span>
                      </div>
                    ) : null}
                    <div ref={chatBottomRef} style={{ height: 1 }} />
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
                          onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => {
                            updateComposerInput(e.target.value);
                          }}
                          onKeyDown={(e: React.KeyboardEvent<HTMLTextAreaElement>) => {
                            if (e.key === "Enter" && !e.shiftKey) {
                              e.preventDefault();
                              sendText();
                            }
                          }}
                          placeholder="Ask Echo anything..."
                          aria-label="Ask Echo anything"
                        />
                      </div>
                      <div className="composer-trailing">
                        <ContextMeter messages={messages} contextWindow={providerInfo?.context_window || 0} />
                        <button
                          className="send-button"
                          onClick={() => sendText()}
                          type="button"
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
                      <div className="composer-tools-slot" role="group" aria-label="Input tools">
                        <button
                          className={`mic-button ${listening ? "active" : ""}`}
                          type="button"
                          title={listening ? "Stop microphone" : "Start microphone"}
                          aria-label={listening ? "Stop microphone" : "Start microphone"}
                          onClick={() =>
                            listening
                              ? (stop(), setListening(false), setStreaming(false))
                              : start()
                          }
                        >
                          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
                            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" fill="currentColor" />
                            <path d="M19 10v2a7 7 0 0 1-14 0v-2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                          </svg>
                        </button>
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
                          className={`composer-square ${showVisualizer && !narrowLayout ? "active" : ""}`}
                          type="button"
                          title={showVisualizer && !narrowLayout ? "Hide visualizer" : "Show visualizer"}
                          aria-label={showVisualizer && !narrowLayout ? "Hide visualizer" : "Show visualizer"}
                          onClick={() => setShowVisualizer((v) => !v)}
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
                    </div>
                  </div>
                </>
              )}

              {studioOpen && createPortal(
                <motion.div
                  className="studio-shell"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  role="dialog"
                  aria-modal="true"
                  aria-label="EchoSpeak Studio"
                >
                  <div className="studio-top">
                    <div className="studio-brand">
                      <div className="studio-brand-mark">
                        <img src="/logo.png" alt="" />
                      </div>
                      <div>
                        <div className="studio-title">EchoSpeak Studio</div>
                        <div className="studio-sub">your machine · your rules</div>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="studio-x"
                      onClick={closeStudio}
                      title="Close Studio"
                      aria-label="Close Studio"
                    >
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                        <path d="M18 6L6 18M6 6l12 12" />
                      </svg>
                    </button>
                  </div>

                  <div className="studio-nav">
                    <div className="studio-nav-inner">
                      {studioTabs.map((tab) => (
                        <button
                          key={tab.id}
                          type="button"
                          className={"studio-tab" + (leftTab === tab.id ? " active" : "")}
                          onClick={() => setLeftTab(tab.id)}
                          title={tab.group}
                        >
                          {tab.label}
                        </button>
                      ))}
                    </div>
                  </div>



                  <div className="studio-body">
                    <div className="studio-column">
                      <div className="studio-hero">
                        <h2>{studioActiveTab?.label || "Studio"}</h2>
                        <span>{studioActiveTab?.group || "workspace"}</span>
                      </div>
                      <div style={{ flex: 1, minHeight: 0, overflow: "hidden", display: "flex", flexDirection: "column", width: "100%" }}>

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
                          const res = await fetch(`${apiBase}/memory/compact?thread_id=${encodeURIComponent(activeThreadId)}`, { method: "POST" });
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
                            body: JSON.stringify({ ids: selectedMemoryIds, thread_id: activeThreadId }),
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
                                body: JSON.stringify({ id, thread_id: activeThreadId, memory_type: newType }),
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
                    {memoryLoading ? (
                      <div className="research-card">
                        <div className="research-snippet">Loading memory…</div>
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
                          return (
                            <div key={m.id} className="research-card" style={{ border: isSelected ? `1px solid ${colors.accent}` : undefined }}>
                              <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center", marginBottom: 10 }}>
                                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
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
                                  <div style={{ fontSize: 14, color: colors.textDim, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                    {ts ? ts : "(no timestamp)"}
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
                                        body: JSON.stringify({ id: m.id, thread_id: activeThreadId, memory_type: newType }),
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
                                        body: JSON.stringify({ id: m.id, thread_id: activeThreadId, text: editingMemoryText }),
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
              {leftTab === "settings" && (
                <>
                  <div className="research-scroll">
                    <div className="research-card">
                      <div className="research-title">Runtime Settings</div>
                      <div className="research-snippet" style={{ marginBottom: 12 }}>
                        These settings are saved to <code>apps/backend/data/settings.json</code> and override <code>.env</code> defaults.
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
                                label="Enable Tool Calling"
                                checked={Boolean(settingsDraft.use_tool_calling_llm)}
                                onChange={(v) => updateDraft("use_tool_calling_llm", v)}
                              />
                              <Toggle
                                label="LM Studio Tool Calling"
                                checked={Boolean(settingsDraft.lmstudio_tool_calling)}
                                onChange={(v) => updateDraft("lmstudio_tool_calling", v)}
                              />
                              <Toggle
                                label="Gemini LangGraph Tools"
                                checked={Boolean(settingsDraft.gemini_use_langgraph)}
                                onChange={(v) => updateDraft("gemini_use_langgraph", v)}
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

                          <div>
                            <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Open application allowlist</label>
                            <input
                              type="text"
                              className="input-field"
                              value={Array.isArray(settingsDraft.open_application_allowlist) ? settingsDraft.open_application_allowlist.join(",") : ""}
                              placeholder="notepad,calc,chrome"
                              onChange={(e) =>
                                updateDraft(
                                  "open_application_allowlist",
                                  e.target.value
                                    .split(",")
                                    .map((x) => x.trim().toLowerCase())
                                    .filter(Boolean)
                                )
                              }
                              style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                            />
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
                              <div style={{ flex: "1 1 180px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Web max retries</label>
                                <input
                                  type="number"
                                  className="input-field"
                                  value={Number(settingsDraft.web_task_max_retries ?? 2)}
                                  min={0}
                                  max={5}
                                  onChange={(e) => updateDraft("web_task_max_retries", Number(e.target.value || 0))}
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
                              <Toggle label="Orchestration" checked={Boolean(settingsDraft.orchestration_enabled)} onChange={(v) => updateDraft("orchestration_enabled", v)} />
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
              {leftTab === "capabilities" && (
                <>
                  <div className="research-scroll">
                    <div className="research-card">
                      <div className="research-title">Capabilities & Permissions</div>
                      <div className="research-snippet" style={{ marginBottom: 12 }}>
                        View available tools, loaded skills, pipeline plugins, and what permissions they require.
                      </div>
                      <button
                        className="icon-button"
                        style={{ padding: "8px 16px", fontSize: 13, marginBottom: 16 }}
                        type="button"
                        onClick={async () => {
                          try {
                            const res = await fetch(`${apiBase}/capabilities?thread_id=${encodeURIComponent(activeThreadId)}`);
                            const data = await res.json();
                            setCapabilitiesData(data);
                            await refreshCodingReadiness();
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

                          {/* Coding Readiness */}
                          <div style={{ background: "linear-gradient(135deg, rgba(34,197,94,0.08), rgba(255,255,255,0.01))", padding: 12, borderRadius: 12, border: "1px solid rgba(34,197,94,0.18)", marginBottom: 16 }}>
                            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 8 }}>
                              <div style={{ fontSize: 12, fontWeight: 700 }}>Coding Agent Loop</div>
                              <span style={{ fontSize: 11, fontWeight: 700, color: codingReadiness?.ok ? "#22c55e" : "#f59e0b" }}>
                                {codingReadinessLoading ? "checking" : codingReadiness?.ok ? "ready" : "needs review"}
                              </span>
                            </div>
                            {codingReadiness ? (
                              <>
                                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 8, marginBottom: 8 }}>
                                  <div className="research-snippet">Provider: {codingReadiness.provider?.message || codingReadiness.provider?.name || "unknown"}</div>
                                  <div className="research-snippet">Tools: {codingReadiness.tools.filter(t => t.allowed).length}/{codingReadiness.tools.length} ready</div>
                                  <div className="research-snippet">Loop: {(codingReadiness.recommended_loop || []).join(" -> ")}</div>
                                </div>
                                {(codingReadiness.blocked_tools || []).length ? (
                                  <div className="research-snippet" style={{ color: "#f59e0b" }}>Blocked: {codingReadiness.blocked_tools.join(", ")}</div>
                                ) : null}
                                {(codingReadiness.missing_tools || []).length ? (
                                  <div className="research-snippet" style={{ color: colors.danger }}>Missing: {codingReadiness.missing_tools.join(", ")}</div>
                                ) : null}
                                {(codingReadiness.recommendations || []).slice(0, 2).map((r, i) => (
                                  <div key={`cr-${i}`} className="research-snippet">{r}</div>
                                ))}
                              </>
                            ) : (
                              <div className="research-snippet">Refresh tools to check whether coding can inspect, write, and verify projects.</div>
                            )}
                          </div>

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

                          {/* Skills & Plugins */}
                          <div style={{ background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)", marginBottom: 16 }}>
                            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>Loaded Skills & Plugins</div>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                              {(capabilitiesData.skills || []).length > 0 ? (
                                (capabilitiesData.skills || []).map((skill: any) => (
                                  <div key={skill.id || skill.name} style={{ fontSize: 11, padding: "4px 10px", borderRadius: 6, background: colors.panel2, border: `1px solid ${colors.line}`, display: "flex", alignItems: "center", gap: 6 }}>
                                    <span>{skill.name || skill.id}</span>
                                    {skill.has_tools && <span style={{ fontSize: 9, padding: "1px 4px", borderRadius: 3, background: "rgba(59,130,246,0.15)", color: "#3b82f6", fontWeight: 600 }}>TOOL</span>}
                                    {skill.has_plugin && <span style={{ fontSize: 9, padding: "1px 4px", borderRadius: 3, background: "rgba(168,85,247,0.15)", color: "#a855f7", fontWeight: 600 }}>PLUGIN</span>}
                                  </div>
                                ))
                              ) : (
                                <div style={{ fontSize: 11, color: colors.textDim }}>No external skills or plugins are currently loaded.</div>
                              )}
                            </div>
                          </div>

                          {/* Tools List */}
                          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>
                            Tools ({(capabilitiesData.tools?.items || []).length}
                            {typeof capabilitiesData.tools?.count === "number" ? ` of ${capabilitiesData.tools.count}` : ""})
                          </div>
                          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                            {(capabilitiesData.tools?.items || []).map((tool: any) => (
                              <div
                                key={tool.name}
                                style={{
                                  background: colors.panel2,
                                  padding: 10,
                                  borderRadius: 6,
                                  border: `1px solid ${tool.allowed ? colors.line : colors.danger}`,
                                  opacity: tool.allowed ? 1 : 0.6,
                                }}
                              >
                                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                                  <span style={{ fontSize: 13, fontWeight: 600, fontFamily: "monospace" }}>{tool.name}</span>
                                  <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                                    {/* Risk Level Badge */}
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
                                    {/* Confirmation Badge */}
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
                                    {/* Allowed/Blocked Status */}
                                    <span
                                      style={{
                                        fontSize: 11,
                                        fontWeight: 600,
                                        color: tool.allowed ? "#22c55e" : colors.danger,
                                      }}
                                    >
                                      {tool.allowed ? "✓" : "✗"}
                                    </span>
                                  </div>
                                </div>
                                {/* Blocked Reason */}
                                {!tool.allowed && tool.blocked_reason && (
                                  <div style={{ fontSize: 11, color: colors.danger, marginBottom: 4 }}>
                                    {tool.blocked_reason}
                                  </div>
                                )}
                                {/* Policy Flags */}
                                {tool.policy_flags && tool.policy_flags.length > 0 && (
                                  <div style={{ fontSize: 10, color: colors.textDim }}>
                                    Requires: {tool.policy_flags.join(", ")}
                                  </div>
                                )}
                                <div style={{ fontSize: 10, color: colors.textDim }}>
                                  Trust: {tool.trust_state || "built_in"}{tool.mcp_server ? ` · MCP server: ${tool.mcp_server}` : ""}
                                </div>
                              </div>
                            ))}
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

                  <div style={{ padding: "12px 14px", marginBottom: 12, borderRadius: 12, background: "linear-gradient(135deg, rgba(168,85,247,0.08), rgba(168,85,247,0.02))", border: "1px solid rgba(168,85,247,0.2)" }}>
                    <div style={{ fontSize: 11, color: "#c084fc", fontWeight: 600, letterSpacing: "0.04em", marginBottom: 6 }}>EXECUTION STATE</div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10 }}>
                      <div><div style={{ fontSize: 10, color: colors.textDim }}>Latest execution</div><div style={{ fontSize: 13, fontWeight: 600 }}>{latestExecutionId || threadState?.last_execution_id || "—"}</div></div>
                      <div><div style={{ fontSize: 10, color: colors.textDim }}>Latest trace</div><div style={{ fontSize: 13, fontWeight: 600 }}>{latestTraceId || threadState?.last_trace_id || "—"}</div></div>
                      <div><div style={{ fontSize: 10, color: colors.textDim }}>Pending approval</div><div style={{ fontSize: 13, fontWeight: 600 }}>{threadState?.pending_approval_id || "none"}</div></div>
                    </div>
                  </div>

                  <div className="research-scroll">
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

              {/* Routines Tab */}
              {leftTab === "routines" && (
                <>
                  {/* Pipeline Status */}
                  <div style={{ padding: "10px 14px", marginBottom: 4, borderRadius: 10, background: "linear-gradient(135deg, rgba(34,197,94,0.08), rgba(34,197,94,0.02))", border: "1px solid rgba(34,197,94,0.2)" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#22c55e", boxShadow: "0 0 6px rgba(34,197,94,0.5)", animation: "pulse 2s infinite" }} />
                      <span style={{ fontSize: 11, color: "#22c55e", fontWeight: 600, letterSpacing: "0.03em" }}>SCHEDULER ACTIVE · CONNECTED TO PIPELINE</span>
                    </div>
                    <div style={{ fontSize: 10, color: colors.textDim, marginTop: 4 }}>Routines fire through process_query() — full tool access, safety gating, and memory recording.</div>
                  </div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={async () => {
                        setRoutinesLoading(true);
                        try {
                          const res = await fetch(`${apiBase}/routines`);
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
                          <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 4 }}>
                            <strong>Type:</strong> {routine.action_type} | <strong>Runs:</strong> {routine.run_count}
                          </div>
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
                                await fetch(`${apiBase}/routines/${routine.id}/run`, { method: "POST" });
                                // Refresh to update run count
                                const res = await fetch(`${apiBase}/routines`);
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
                                await fetch(`${apiBase}/routines/${routine.id}`, {
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
                                await fetch(`${apiBase}/routines/${routine.id}`, { method: "DELETE" });
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
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                        <h3 style={{ margin: 0, fontSize: 16, color: colors.text }}>Agent Soul</h3>
                        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          {soulEnabled ? (
                            <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(34,197,94,0.12)", color: "#22c55e" }}>ENABLED</span>
                          ) : (
                            <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(107,114,128,0.12)", color: colors.textDim }}>DISABLED</span>
                          )}
                          {soulSavedAt && (
                            <span style={{ fontSize: 10, color: colors.textDim }}>Saved {new Date(soulSavedAt).toLocaleTimeString()}</span>
                          )}
                        </div>
                      </div>

                      <div style={{ fontSize: 12, color: colors.textDim, marginBottom: 12 }}>
                        The soul defines the agent's core identity, values, communication style, and boundaries. Changes apply to new conversations.
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
                <AvatarEditor apiBase={apiBase} colors={colors} onConfigChange={setAvatarConfig} />
              )}

              {leftTab === "services" && (
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
                </div>
              )}
                      </div>
                    </div>
                  </div>
                </motion.div>
              , document.body)}
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
