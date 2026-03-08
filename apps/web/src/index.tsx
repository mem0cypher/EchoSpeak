import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { create } from "zustand";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { SquareAvatarVisual } from "./components/SquareAvatarVisual";
import { getToolCategory, getToolDisplayDetails } from "./components/echoAnimationUtils";
import { InlineCodeDiff } from "./components/InlineCodeDiff";
import type { CodeDiffSession } from "./components/InlineCodeDiff";
import { WorkspaceExplorer } from "./components/WorkspaceExplorer";
import { TaskChecklist, createEmptyTaskPlan, taskPlanReducer } from "./components/TaskChecklist";
import type { TaskPlanState } from "./components/TaskChecklist";
import type { EchoReaction } from "./components/echoAnimationUtils";
import { TodoPanel } from "./components/TodoPanel";
import { AvatarEditor } from "./components/AvatarEditor";
import { ResearchPanel } from "./features/research/ResearchPanel";
import { buildResearchRunFromToolEvent, normalizeResearchRun } from "./features/research/buildResearchRun";
import { useResearchStore } from "./features/research/store";
import type { ResearchRun } from "./features/research/types";

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

type Message = {
  id: string;
  role: Role;
  text: string;
  at: number;
};

type AgentStreamEvent =
  | { type: "tool_start"; id: string; name: string; input: string; at: number; request_id?: string }
  | { type: "tool_end"; id: string; name?: string; output: string; research?: ResearchRun; at: number; request_id?: string }
  | { type: "tool_error"; id: string; error: string; at: number; request_id?: string }
  | { type: "thinking"; content: string; at: number; request_id?: string }
  | { type: "memory_saved"; memory_count: number; at: number; request_id?: string }
  | { type: "task_plan"; data: any[]; at?: number; request_id?: string }
  | { type: "task_step"; data: { index: number; status: string; description?: string; tool?: string; result_preview?: string; total?: number }; at?: number; request_id?: string }
  | { type: "task_reflection"; data: { index: number; accepted: boolean; reason?: string; cycle?: number }; at?: number; request_id?: string }
  | { type: "final"; response: string; spoken_text?: string; success: boolean; memory_count: number; doc_sources?: DocSource[]; research?: ResearchRun[]; execution_id?: string; trace_id?: string; thread_state?: ThreadSessionState | null; request_id?: string; at: number }
  | { type: "error"; message: string; at: number; request_id?: string };

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
  | { kind: "thinking"; id: string; content: string; at: number }
  | { kind: "tool"; id: string; name: string; input: string; status: "running" | "done" | "error"; output?: string; at: number }
  | { kind: "memory"; id: string; memoryCount: number; at: number }
  | { kind: "error"; id: string; message: string; at: number };

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

type ThreadSessionState = {
  thread_id: string;
  workspace_id: string;
  active_project_id: string;
  pending_approval_id: string;
  last_execution_id: string;
  last_trace_id: string;
  runtime_provider: string;
  updated_at: number;
};

type ApprovalRecord = {
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
const geminiModelOptions = ["gemini-3.1-flash-lite-preview", "gemini-3.1-pro-preview", "gemini-3-flash-preview", "gemini-2.5-pro"];
const listableProviders = ["ollama", "lmstudio", "localai", "vllm"];
const isLmStudioOnlyLocked = (info: ProviderInfo | null): boolean => {
  const providers = info?.available_providers || [];
  if (!providers.length) return false;
  return providers.length === 1 && providers[0].id === "lmstudio";
};

const workspaceModes = ["auto", "chat", "coding", "research"] as const;
type WorkspaceMode = (typeof workspaceModes)[number];

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
  enable_particles: boolean;
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
  enable_particles: true,
  enable_glow: true,
  enable_idle_activities: true,
  custom_status_text: "",
};

const globalCss = `
         :root { --ui-scale: 1.1; }
         @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;700&display=swap');
         * { box-sizing: border-box; }
         body { margin: 0; background: ${colors.bg}; font-family: 'Manrope', sans-serif; -webkit-font-smoothing: antialiased; }
         * { scrollbar-width: thin; scrollbar-color: #333 transparent; }
         *::-webkit-scrollbar { width: 6px; height: 6px; }
         *::-webkit-scrollbar-track { background: transparent; }
         *::-webkit-scrollbar-thumb { background: #333; border-radius: 0; }
         *::-webkit-scrollbar-thumb:hover { background: #444; }
         ::selection { background: #fff; color: #000; }
         
         .chat-markdown p:first-of-type { margin-top: 0; }
         .chat-markdown p:last-of-type { margin-bottom: 0; }
         
         .app-shell {
           width: calc(100vw / var(--ui-scale) + 4px);
           height: calc(100vh / var(--ui-scale));
           display: grid;
           gap: 0;
           padding: 0;
           background: ${colors.bg};
           zoom: var(--ui-scale);
           transform-origin: top left;
         }
         .visualizer-pane {
           display: flex;
           align-items: center;
           justify-content: center;
           background: rgba(0,0,0,0.2);
           border-right: 1px solid ${colors.line};
           height: 100%;
           overflow: hidden;
         }
         .glow-panel {
           background: ${colors.panel};
           display: flex;
           flex-direction: column;
           height: 100%;
           overflow: hidden;
           transition: all 0.3s ease;
         }
         .panel-header {
           display: flex;
           align-items: center;
           justify-content: space-between;
           padding: 20px 28px;
           border-bottom: 1px solid ${colors.line};
         }
         .panel-header .title {
           display: flex;
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
          flex: 1;
          display: flex;
          flex-direction: column;
          padding: 28px;
          overflow: hidden;
          min-height: 0;
          gap: 24px;
        }
        .research-panel {
          display: flex;
          flex-direction: column;
          height: 100%;
          flex: 1;
          overflow: hidden;
          min-height: 0;
          gap: 20px;
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
           gap: 16px;
           padding-right: 8px;
         }
         .research-card {
           background: linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01));
           backdrop-filter: blur(12px);
           -webkit-backdrop-filter: blur(12px);
           border: 1px solid rgba(255, 255, 255, 0.1);
           border-radius: 16px;
           padding: 16px 20px;
           transition: all 0.3s ease;
           box-shadow: 0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05);
         }
         .research-card:hover {
           border-color: rgba(255, 255, 255, 0.2);
           transform: translateY(-2px);
           box-shadow: 0 8px 24px -4px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.08);
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
         .research-source:hover {
           opacity: 1;
           text-decoration: underline;
         }
         .chat-scroll {
           flex: 1;
           overflow-y: auto;
           display: flex;
           flex-direction: column;
           gap: 20px;
           width: 100%;
           padding-right: 0;
         }
       .input-bar {
           margin-top: auto;
           display: flex;
           flex-direction: column;
           gap: 12px;
         }
         .input-row {
           display: flex;
           gap: 12px;
           align-items: center;
         }
         .input-field {
           flex: 1;
           min-width: 0;
           background: linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01));
           backdrop-filter: blur(12px);
           -webkit-backdrop-filter: blur(12px);
           border: 1px solid rgba(255, 255, 255, 0.12);
           border-radius: 16px;
           padding: 16px 20px;
           color: ${colors.text};
           font-size: 16px;
           outline: none;
           transition: all 0.3s ease;
           box-shadow: inset 0 2px 4px rgba(0,0,0,0.2), 0 4px 16px -4px rgba(0,0,0,0.3);
         }
         .input-field:focus {
           background: linear-gradient(135deg, rgba(45,108,255,0.08), rgba(255,255,255,0.02));
           border-color: rgba(140,180,255,0.4);
           box-shadow: inset 0 2px 4px rgba(0,0,0,0.2), 0 0 0 2px rgba(45,108,255,0.2), 0 4px 16px -4px rgba(0,0,0,0.3);
         }
         .send-button {
           width: 52px;
           height: 52px;
           display: grid;
           place-items: center;
           background: linear-gradient(135deg, rgba(45,108,255,0.2), rgba(45,108,255,0.05));
           backdrop-filter: blur(12px);
           -webkit-backdrop-filter: blur(12px);
           border: 1px solid rgba(140,180,255,0.4);
           border-radius: 16px;
           color: #fff;
           cursor: pointer;
           transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
           position: relative;
           overflow: hidden;
           box-shadow: 0 4px 16px -4px rgba(45,108,255,0.3), inset 0 1px 0 rgba(255,255,255,0.2);
         }
         .send-button::before {
           content: '';
           position: absolute;
           inset: -40%;
           background: linear-gradient(
             120deg,
             rgba(255, 255, 255, 0.00) 0%,
             rgba(255, 255, 255, 0.20) 18%,
             rgba(255, 255, 255, 0.05) 38%,
             rgba(255, 255, 255, 0.25) 55%,
             rgba(255, 255, 255, 0.00) 72%
           );
           transform: translateX(-35%) rotate(8deg);
           opacity: 0;
           transition: opacity 0.22s ease;
           pointer-events: none;
         }
         .send-button > * { position: relative; z-index: 1; }
         @keyframes liquid-metal-shift {
           0% { transform: translateX(-45%) rotate(8deg); }
           100% { transform: translateX(45%) rotate(8deg); }
         }
         .send-button:hover {
           background: linear-gradient(135deg, rgba(45,108,255,0.3), rgba(45,108,255,0.1));
           border-color: rgba(140,180,255,0.6);
           transform: translateY(-2px);
           box-shadow: 0 6px 20px -4px rgba(45,108,255,0.4), inset 0 1px 0 rgba(255,255,255,0.3);
         }
         .send-button:hover::before {
           opacity: 1;
           animation: liquid-metal-shift 0.9s ease-in-out infinite alternate;
         }
         .send-button:active {
           transform: translateY(0);
         }
         .controls-row {
           display: grid;
           grid-template-columns: repeat(4, minmax(0, 1fr));
           align-items: stretch;
           gap: 12px;
           width: 100%;
         }

         .control-slot {
           display: flex;
           align-items: stretch;
           min-width: 0;
           width: 100%;
         }

         .session-slot {
           position: relative;
         }

         .mode-slot,
         .provider-slot,
         .model-slot {
           width: 100%;
         }

         .input-side-tools {
           display: flex;
           align-items: center;
           gap: 10px;
         }

         /* Liquid Metal Base for Bottom Controls */
         .icon-button, .mic-button, .provider-picker, .model-picker, .mode-picker, .toolbar-button {
           position: relative;
           overflow: hidden;
           background: linear-gradient(135deg, rgba(255,255,255,0.12), rgba(255,255,255,0.02));
           backdrop-filter: blur(12px);
           -webkit-backdrop-filter: blur(12px);
           border: 1px solid rgba(255, 255, 255, 0.25);
           box-shadow: inset 0 1px 0 rgba(255,255,255,0.2), 0 4px 12px rgba(0,0,0,0.15);
           color: #fff;
           cursor: pointer;
           transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
         }
         .icon-button::before, .mic-button::before, .provider-picker::before, .model-picker::before, .mode-picker::before, .toolbar-button::before {
           content: '';
           position: absolute;
           inset: -40%;
           background: linear-gradient(
             120deg,
             rgba(255, 255, 255, 0.00) 0%,
             rgba(255, 255, 255, 0.20) 18%,
             rgba(255, 255, 255, 0.05) 38%,
             rgba(255, 255, 255, 0.25) 55%,
             rgba(255, 255, 255, 0.00) 72%
           );
           transform: translateX(-35%) rotate(8deg);
           opacity: 0;
           transition: opacity 0.22s ease;
           pointer-events: none;
           z-index: 0;
         }
         .icon-button > *, .mic-button > *, .provider-picker > *, .model-picker > *, .mode-picker > *, .toolbar-button > * { position: relative; z-index: 1; }
         
         .icon-button:hover:not(:disabled), .mic-button:hover:not(:disabled), .provider-picker:hover:not(:disabled), .model-picker:hover:not(:disabled), .mode-picker:hover:not(:disabled), .toolbar-button:hover:not(:disabled) {
           background: linear-gradient(135deg, rgba(255,255,255,0.18), rgba(255,255,255,0.05));
           border-color: rgba(255, 255, 255, 0.4);
           transform: translateY(-2px);
           box-shadow: 0 6px 20px -4px rgba(0,0,0,0.25), inset 0 1px 0 rgba(255,255,255,0.3);
         }
         .icon-button:hover:not(:disabled)::before, .mic-button:hover:not(:disabled)::before, .provider-picker:hover:not(:disabled)::before, .model-picker:hover:not(:disabled)::before, .mode-picker:hover:not(:disabled)::before, .toolbar-button:hover:not(:disabled)::before {
           opacity: 1;
           animation: liquid-metal-shift 0.9s ease-in-out infinite alternate;
         }
         .icon-button:active:not(:disabled), .mic-button:active:not(:disabled), .provider-picker:active:not(:disabled), .model-picker:active:not(:disabled), .mode-picker:active:not(:disabled), .toolbar-button:active:not(:disabled) {
           transform: translateY(0);
         }

         .icon-button {
           display: flex;
           align-items: center;
           justify-content: center;
           border-radius: 8px;
         }
         
         .mic-button {
           width: 44px;
           height: 44px;
           flex: 0 0 44px;
           display: grid;
           place-items: center;
           border-radius: 12px;
         }
         .mic-button.active {
           background: linear-gradient(135deg, rgba(239,68,68,0.25), rgba(239,68,68,0.08));
           border-color: rgba(239,68,68,0.5);
           color: #ef4444;
           box-shadow: inset 0 1px 0 rgba(255,255,255,0.15), 0 2px 12px rgba(239,68,68,0.3);
         }
         .mic-button.active::before {
           background: linear-gradient(120deg, rgba(239,68,68,0) 0%, rgba(239,68,68,0.3) 18%, rgba(239,68,68,0.05) 38%, rgba(239,68,68,0.4) 55%, rgba(239,68,68,0) 72%);
         }
         .inline-switcher {
           display: flex;
           align-items: center;
           gap: 0;
           padding: 0;
           background: transparent;
           border: none;
           border-radius: 0;
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
           height: 44px;
           border-radius: 14px;
           font-size: 13px;
           font-weight: 600;
           outline: none;
           padding: 0 14px;
           line-height: 1.2;
           min-width: 0;
         }
         .toolbar-button {
           width: 100%;
           display: flex;
           align-items: center;
           justify-content: space-between;
           text-align: left;
         }
         .provider-picker, .model-picker, .mode-picker {
           width: 100%;
           max-width: none;
           appearance: none;
         }

         .provider-picker option, .model-picker option, .mode-picker option {
           background: #1e222d;
           color: ${colors.text};
         }
         
         /* Fix native dropdown options being white in the browser */
         select {
           color: ${colors.text};
         }
         select option {
           background: #1e222d;
           color: ${colors.text};
         }
         @media (max-width: 980px) {
           .controls-row {
             grid-template-columns: repeat(4, minmax(180px, 1fr));
             overflow-x: auto;
             padding-bottom: 2px;
           }
           .control-slot {
             min-width: 180px;
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

const ContextRing: React.FC<{ messages: Message[]; contextWindow: number }> = ({ messages, contextWindow }) => {
  const [hover, setHover] = React.useState(false);
  if (!contextWindow || contextWindow <= 0) return null;
  const totalChars = messages.reduce((sum, m) => sum + (m.text?.length || 0), 0);
  const estimatedTokens = Math.round(totalChars / 3.5);
  const pct = Math.min(estimatedTokens / contextWindow, 1);
  const displayPct = Math.round(pct * 100);
  const size = 32;
  const stroke = 3;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference * (1 - pct);
  const ringColor =
    pct > 0.85 ? "rgba(255,80,80,0.9)" : pct > 0.6 ? "rgba(255,180,60,0.9)" : "rgba(140,180,255,0.7)";
  const formatTokens = (n: number) => (n >= 1000000 ? `${(n / 1000000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n));
  return (
    <div
      style={{ position: "relative", display: "grid", placeItems: "center", width: size, height: size, cursor: "default", flexShrink: 0 }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ transform: "rotate(-90deg)" }}>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth={stroke} />
        <circle
          cx={size / 2} cy={size / 2} r={radius} fill="none"
          stroke={ringColor} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={circumference} strokeDashoffset={dashOffset}
          style={{ transition: "stroke-dashoffset 0.4s ease, stroke 0.3s ease" }}
        />
      </svg>
      <span style={{
        position: "absolute", fontSize: 8, fontWeight: 700, color: ringColor,
        letterSpacing: "-0.3px", lineHeight: 1, userSelect: "none",
      }}>
        {displayPct}%
      </span>
      {hover && (
        <div style={{
          position: "absolute", bottom: "calc(100% + 8px)", left: "50%", transform: "translateX(-50%)",
          background: "rgba(20,22,30,0.95)", border: "1px solid rgba(255,255,255,0.12)",
          borderRadius: 10, padding: "8px 12px", whiteSpace: "nowrap", zIndex: 999,
          boxShadow: "0 4px 20px rgba(0,0,0,0.5)", backdropFilter: "blur(12px)",
          fontSize: 12, color: colors.text, lineHeight: 1.5,
        }}>
          <div style={{ fontWeight: 600, marginBottom: 2, color: ringColor }}>Context Window</div>
          <div><span style={{ color: colors.textDim }}>Used ≈</span> {formatTokens(estimatedTokens)} / {formatTokens(contextWindow)} tokens</div>
          <div><span style={{ color: colors.textDim }}>Fill</span> {displayPct}%</div>
        </div>
      )}
    </div>
  );
};

const ChatBubble: React.FC<{
  msg: Message;
  streaming?: boolean;
  onQuickReply?: (text: string) => void;
}> = ({ msg, streaming, onQuickReply }) => {
  const isUser = msg.role === "user";
  const isConfirmPrompt = !isUser
    ? (() => {
      const t = (msg.text || "").toLowerCase();
      if (!t) return false;
      if (t.includes("reply 'confirm'")) return true;
      if (t.includes('reply "confirm"')) return true;
      if (t.includes("confirm' to proceed") || t.includes('confirm" to proceed')) return true;
      if (t.includes("pending action") && t.includes("confirm")) return true;
      return false;
    })()
    : false;

  const canQuickReply = Boolean(isConfirmPrompt && onQuickReply && !streaming);
  return (
    <motion.div
      layout
      initial={{ opacity: 0, scale: 0.95, y: 10 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95, y: -8 }}
      transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
      style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start", position: "relative", width: "100%" }}
    >
      <div
        style={{
          position: "relative",
          maxWidth: isUser ? "88%" : "94%",
          background: isUser
            ? "linear-gradient(135deg, rgba(45,108,255,0.18), rgba(45,108,255,0.06))"
            : "linear-gradient(135deg, rgba(255,255,255,0.08), rgba(255,255,255,0.02))",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
          color: colors.text,
          border: isUser
            ? "1px solid rgba(140,180,255,0.3)"
            : "1px solid rgba(255,255,255,0.12)",
          borderRadius: isUser ? "18px 18px 4px 18px" : "18px 18px 18px 4px",
          padding: "12px 16px",
          boxShadow: isUser
            ? "0 4px 16px -4px rgba(45,108,255,0.2), inset 0 1px 0 rgba(255,255,255,0.1)"
            : "0 4px 16px -4px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.05)",
          overflow: "hidden"
        }}
      >
        {isUser ? (
          <div className="chat-text">{msg.text}</div>
        ) : (
          <div className="chat-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
          </div>
        )}

        {!isUser && isConfirmPrompt ? (
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button
              onClick={() => onQuickReply?.("confirm")}
              disabled={!canQuickReply}
              style={{
                padding: "8px 10px",
                borderRadius: 8,
                border: `1px solid ${colors.line}`,
                background: canQuickReply ? "rgba(34,197,94,0.14)" : "rgba(148,163,184,0.12)",
                color: colors.text,
                cursor: canQuickReply ? "pointer" : "not-allowed",
                fontSize: 13,
                fontWeight: 600,
              }}
            >
              Confirm
            </button>
            <button
              onClick={() => onQuickReply?.("cancel")}
              disabled={!canQuickReply}
              style={{
                padding: "8px 10px",
                borderRadius: 8,
                border: `1px solid ${colors.line}`,
                background: canQuickReply ? "rgba(239,68,68,0.12)" : "rgba(148,163,184,0.12)",
                color: colors.text,
                cursor: canQuickReply ? "pointer" : "not-allowed",
                fontSize: 13,
                fontWeight: 600,
              }}
            >
              Cancel
            </button>
          </div>
        ) : null}
        <div style={{ marginTop: 4, fontSize: 10.5, color: colors.textDim }}>{new Date(msg.at).toLocaleTimeString()}</div>
      </div>
    </motion.div>
  );
};

const ThinkingActivityCard: React.FC<{ item: { kind: "thinking"; id: string; content: string; at: number } }> = ({ item }) => {
  const [expanded, setExpanded] = useState(false);
  const badge = { label: "Thinking", color: "rgba(140,160,255,0.9)", bg: "rgba(45,108,255,0.12)", border: "rgba(45,108,255,0.3)" };
  const content = (item.content || "").trim();
  const preview = content.length > 280 ? `${content.slice(0, 280).trimEnd()}…` : content;
  const body = expanded ? content : preview;
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
          background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
          color: colors.text,
          border: "1px solid rgba(255,255,255,0.1)",
          borderRadius: 12,
          padding: "12px 16px",
          boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, justifyContent: "space-between", flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div
              style={{
                fontSize: 10.5,
                fontWeight: 700,
                letterSpacing: 0.5,
                textTransform: "uppercase",
                padding: "4px 8px",
                borderRadius: 6,
                color: badge.color,
                background: badge.bg,
                border: `1px solid ${badge.border}`,
                boxShadow: `inset 0 1px 0 rgba(255,255,255,0.1), 0 0 12px ${badge.border}`,
              }}
            >
              <span>{badge.label}</span>
            </div>
            <div style={{ fontSize: 13, fontWeight: 650 }}>Model reasoning captured</div>
          </div>
          {content.length > preview.length ? (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              style={{
                border: `1px solid ${badge.border}`,
                background: badge.bg,
                color: badge.color,
                borderRadius: 999,
                padding: "4px 10px",
                fontSize: 11,
                fontWeight: 700,
                cursor: "pointer",
              }}
            >
              {expanded ? "Hide" : "Show"}
            </button>
          ) : null}
        </div>
        <div style={{ marginTop: 8, fontSize: 12.5, lineHeight: 1.5, color: colors.textDim, whiteSpace: "pre-wrap" }}>{body}</div>
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
    const badge = { label: "Error", color: "rgba(255,120,140,0.95)", bg: "rgba(255,77,109,0.10)", border: "rgba(255,77,109,0.35)" };
    const title = "Agent error";
    const body = item.message;
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
            borderRadius: 10,
            padding: "12px 14px",
            boxShadow: "none",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div
              style={{
                fontSize: 10.5,
                fontWeight: 700,
                letterSpacing: 0.5,
                textTransform: "uppercase",
                padding: "4px 8px",
                borderRadius: 999,
                color: badge.color,
                background: badge.bg,
                border: `1px solid ${badge.border}`,
              }}
            >
              <span>{badge.label}</span>
            </div>
            <div style={{ fontSize: 13, fontWeight: 650 }}>{title}</div>
          </div>
          <div style={{ marginTop: 6, fontSize: 12.5, lineHeight: 1.5, color: colors.textDim, whiteSpace: "pre-wrap" }}>{body}</div>
        </div>
      </motion.div>
    );
  }

  const toolName = (item.name || "").toLowerCase();
  const isTerminal = item.kind === "tool" && toolName === "terminal_run";
  const isConsoleTool = isTerminal;

  // Apply the same "liquid metal" / dark aesthetic to both terminal and browser
  const badge =
    item.status === "running"
      ? {
        label: isConsoleTool ? "Terminal" : "Tool",
        color: "rgba(255,255,255,0.9)",
        bg: "rgba(255,255,255,0.06)",
        border: "rgba(255,255,255,0.15)",
      }
      : item.status === "error"
        ? {
          label: isConsoleTool ? "Terminal" : "Tool",
          color: "rgba(255,140,160,0.95)",
          bg: "rgba(255,77,109,0.10)",
          border: "rgba(255,77,109,0.35)",
        }
        : {
          label: isConsoleTool ? "Terminal" : "Tool",
          color: "rgba(255,255,255,0.7)",
          bg: "transparent",
          border: "rgba(255,255,255,0.1)",
        };
  const title = isConsoleTool ? "" : `Using ${item.name}`;
  const body = item.status === "running" ? item.input || "Running…" : item.output || (item.status === "error" ? "Tool failed" : "Done");

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
          background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
          color: colors.text,
          border: "1px solid rgba(255,255,255,0.1)",
          borderRadius: 14,
          padding: "10px 12px",
          boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div
            style={{
              fontSize: 10.5,
              fontWeight: 700,
              letterSpacing: 0.5,
              textTransform: "uppercase",
              padding: "4px 8px",
              borderRadius: 6,
              color: badge.color,
              background: badge.bg,
              border: `1px solid ${badge.border}`,
              boxShadow: `inset 0 1px 0 rgba(255,255,255,0.1), 0 0 12px ${badge.border}`,
              display: "flex",
              alignItems: "center",
              gap: 7,
            }}
          >
            {item.kind === "tool" && item.status === "running" ? (
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: 99,
                  background: colors.accent,
                  boxShadow: "0 0 10px rgba(45,108,255,0.8)",
                }}
              />
            ) : null}
            <span>{badge.label}</span>
          </div>
          {title && <div style={{ fontSize: 13, fontWeight: 650 }}>{title}</div>}
        </div>
        {isConsoleTool ? (
          <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
            <div
              style={{
                fontSize: 12.5,
                lineHeight: 1.5,
                color: colors.textDim,
                whiteSpace: "pre-wrap",
              }}
            >
              {item.status === "running" ? "Command" : "Result"}
            </div>
            <div
              style={{
                border: `1px solid ${colors.line}`,
                borderRadius: 12,
                padding: "10px 12px",
                background: "rgba(0,0,0,0.25)",
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
                fontSize: 12.5,
                lineHeight: 1.55,
                color: colors.text,
                whiteSpace: "pre-wrap",
                overflowX: "auto",
                overflowY: "auto",
                maxHeight: 280,
              }}
            >
              {body}
            </div>
          </div>
        ) : (
          <div style={{ marginTop: 6, fontSize: 12.5, lineHeight: 1.5, color: colors.textDim, whiteSpace: "pre-wrap" }}>{body}</div>
        )}
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
          {Object.entries(kwargs).map(([key, value]) => (
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
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>(() => {
    try {
      const raw = localStorage.getItem("echospeak_workspace_mode") || "auto";
      return (workspaceModes.includes(raw as any) ? (raw as WorkspaceMode) : "auto");
    } catch {
      return "auto";
    }
  });
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [taskPlan, setTaskPlan] = useState<TaskPlanState>(createEmptyTaskPlan());
  const [echoReaction, setEchoReaction] = useState<EchoReaction | null>(null);
  const [userIsTyping, setUserIsTyping] = useState(false);
  const userTypingTimerRef = useRef<number>(0);
  const research = useResearchStore((state) => state.runs);
  const prependResearchRun = useResearchStore((state) => state.prependRun);
  const replaceResearchRuns = useResearchStore((state) => state.replaceRuns);
  const clearResearchRuns = useResearchStore((state) => state.clearRuns);
  const [leftTab, setLeftTab] = useState<"chat" | "research" | "memory" | "docs" | "settings" | "capabilities" | "approvals" | "executions" | "projects" | "routines" | "soul" | "services" | "avatar_editor">("chat");
  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const activeGroupButtonRef = useRef<HTMLButtonElement | null>(null);
  const activeGroupMenuRef = useRef<HTMLDivElement | null>(null);
  const [activeGroupPos, setActiveGroupPos] = useState<{ top: number; left: number } | null>(null);
  const [showVisualizer, setShowVisualizer] = useState<boolean>(true);
  const [agentMode, setAgentMode] = useState<"idle" | "research" | "coding" | "working">("idle");
  const [visualizerPin, setVisualizerPin] = useState<null | "ring" | "research" | "coding" | "tasks">(null);
  const [codeSessions, setCodeSessions] = useState<CodeDiffSession[]>([]);
  const [activeCodeTab, setActiveCodeTab] = useState<number>(0);
  const [avatarConfig, setAvatarConfig] = useState<AvatarConfig>(defaultAvatarConfig);
  const [memoryItems, setMemoryItems] = useState<MemoryItem[]>([]);
  const [memoryCount, setMemoryCount] = useState<number>(0);
  const [memoryLoading, setMemoryLoading] = useState<boolean>(false);
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
  const toolInfoRef = useRef<Record<string, { name: string; input: string }>>({});
  const latestCodeFilenameRef = useRef<string | null>(null);
  const [capabilitiesData, setCapabilitiesData] = useState<any>(null);
  const [memoryFilterType, setMemoryFilterType] = useState<string>("");
  const [selectedMemoryIds, setSelectedMemoryIds] = useState<string[]>([]);
  const [editingMemoryId, setEditingMemoryId] = useState<string | null>(null);
  const [editingMemoryText, setEditingMemoryText] = useState<string>("");
  const [projects, setProjects] = useState<{ id: string; name: string; description?: string; context_prompt?: string; tags?: string[] }[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string>("");
  const [projectsLoading, setProjectsLoading] = useState<boolean>(false);
  const [threadState, setThreadState] = useState<ThreadSessionState | null>(null);
  const [pendingApproval, setPendingApproval] = useState<PendingActionEnvelope | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [approvalsLoading, setApprovalsLoading] = useState<boolean>(false);
  const [executions, setExecutions] = useState<ExecutionRecord[]>([]);
  const activeCodeSession = useMemo(() => {
    const base = codeSessions[activeCodeTab];
    if (!base) return null;
    const pendingPath = String(pendingApproval?.action?.kwargs?.path || "");
    const isPendingSave = Boolean(
      pendingApproval?.has_pending
      && pendingApproval?.action?.tool === "file_write"
      && pendingPath === base.filename,
    );
    return {
      ...base,
      pendingConfirmation: isPendingSave,
    };
  }, [activeCodeTab, codeSessions, pendingApproval]);
  const [executionsLoading, setExecutionsLoading] = useState<boolean>(false);
  const [selectedTrace, setSelectedTrace] = useState<Record<string, any> | null>(null);
  const [selectedTraceId, setSelectedTraceId] = useState<string>("");
  const [traceLoading, setTraceLoading] = useState<boolean>(false);
  const [latestExecutionId, setLatestExecutionId] = useState<string>("");
  const [latestTraceId, setLatestTraceId] = useState<string>("");
  const [routines, setRoutines] = useState<{ id: string; name: string; description?: string; enabled: boolean; trigger_type: string; schedule?: string; webhook_path?: string; action_type: string; action_config: Record<string, any>; last_run?: string; next_run?: string; run_count: number }[]>([]);
  const [routinesLoading, setRoutinesLoading] = useState<boolean>(false);

  const [threads, setThreads] = useState<{ id: string; name: string; at: number }[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string>("");

  useEffect(() => {
    try {
      const saved = localStorage.getItem("echospeak.threads");
      let list = saved ? JSON.parse(saved) : [];
      if (!Array.isArray(list) || list.length === 0) {
        const defaultId = localStorage.getItem("echospeak.thread_id") || crypto.randomUUID();
        list = [{ id: defaultId, name: "Default Session", at: Date.now() }];
      }
      setThreads(list);
      const lastActive = localStorage.getItem("echospeak.active_thread_id") || list[0].id;
      setActiveThreadId(lastActive);
    } catch (e) {
      const defaultId = crypto.randomUUID();
      setThreads([{ id: defaultId, name: "Default Session", at: Date.now() }]);
      setActiveThreadId(defaultId);
    }
  }, []);

  useEffect(() => {
    if (threads.length > 0) {
      localStorage.setItem("echospeak.threads", JSON.stringify(threads));
    }
  }, [threads]);

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

  const loadHistory = async (threadId: string) => {
    try {
      const tid = encodeURIComponent(String(threadId || "").trim());
      const resp = await fetchWithTimeout(`${apiBase}/history?thread_id=${tid}`, undefined, 8000);
      if (resp.ok) {
        const data = await resp.json();
        if (data && data.history && Array.isArray(data.history)) {
          // Parse the string representations back into structured UI messages if possible
          // The backend returns a list of strings for /history. We will map them to basic chat bubbles.
          const loadedMsgs = data.history.map((h: string, i: number) => {
            const isUser = h.startsWith("Human:");
            const text = h.replace(/^(Human:|Assistant:)\s*/, "").trim();
            return {
              id: `hist-${Date.now()}-${i}`,
              role: isUser ? "user" : "assistant",
              text: text,
              at: Date.now() - (data.history.length - i) * 1000
            };
          }).filter((m: any) => m.text);
          if (loadedMsgs.length > 0) {
            useAppStore.setState({ messages: loadedMsgs });
          }
        }
      }
    } catch (e) {
      console.error("Failed to load history:", e);
    }
  };

  useEffect(() => {
    if (leftTab === "capabilities") {
      fetch(`${apiBase}/capabilities?thread_id=${activeThreadId}`)
        .then(res => res.json())
        .then(data => setCapabilitiesData(data))
        .catch(e => console.error("Failed to fetch capabilities:", e));
    }
  }, [leftTab, activeThreadId]);

  useEffect(() => {
    refreshThreads();
    refreshProjects();
  }, []);

  useEffect(() => {
    const latestFilename = latestCodeFilenameRef.current;
    if (latestFilename) {
      const nextIndex = codeSessions.findIndex((session) => session.filename === latestFilename);
      latestCodeFilenameRef.current = null;
      if (nextIndex >= 0 && nextIndex !== activeCodeTab) {
        setActiveCodeTab(nextIndex);
        return;
      }
    }
    if (codeSessions.length === 0 && activeCodeTab !== 0) {
      setActiveCodeTab(0);
      return;
    }
    if (activeCodeTab >= codeSessions.length && codeSessions.length > 0) {
      setActiveCodeTab(codeSessions.length - 1);
    }
  }, [activeCodeTab, codeSessions]);

  const refreshThreads = async () => {
    try {
      const resp = await fetchWithTimeout(`${apiBase}/threads?limit=50`, undefined, 6000);
      if (!resp.ok) throw new Error(`Threads failed (${resp.status})`);
      const data = await resp.json();
      const items = Array.isArray(data) ? data : [];
      const mapped = items.map((item: any) => ({
        id: String(item.thread_id || item.id || ""),
        name: String(item.title || item.name || "Session"),
        at: normalizeTimestampMs(item.last_active_at || item.created_at || Date.now()),
      })).filter((item: any) => item.id);
      if (mapped.length) {
        setThreads(mapped);
        setActiveThreadId((current) => current || mapped[0].id);
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
      setPendingApproval(data);
      return data;
    } catch (e) {
      console.error("Failed to refresh pending approval:", e);
      return null;
    }
  };

  const refreshApprovals = async (threadId: string = activeThreadId) => {
    if (!threadId) return;
    setApprovalsLoading(true);
    try {
      const resp = await fetchWithTimeout(`${apiBase}/approvals?thread_id=${encodeURIComponent(threadId)}&limit=25`, undefined, 6000);
      if (!resp.ok) throw new Error(`Approvals failed (${resp.status})`);
      const data = (await resp.json()) as ApprovalListResponse;
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

  const createNewThread = async () => {
    try {
      const resp = await fetch(`${apiBase}/threads`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: `Session ${threads.length + 1}`, source: "web", workspace_id: workspaceMode === "auto" ? "" : workspaceMode }),
      });
      if (!resp.ok) throw new Error(`Create thread failed (${resp.status})`);
      const data = await resp.json();
      const nextThread = { id: String(data.thread_id), name: String(data.title || `Session ${threads.length + 1}`), at: normalizeTimestampMs(data.last_active_at || data.created_at || Date.now()) };
      setThreads((prev) => [nextThread, ...prev.filter((item) => item.id !== nextThread.id)]);
      setActiveThreadId(nextThread.id);
      useAppStore.setState({ messages: [] });
      setActivities([]);
      setTaskPlan(createEmptyTaskPlan());
      clearResearchRuns();
      latestCodeFilenameRef.current = null;
      setCodeSessions([]);
      setActiveCodeTab(0);
      setPendingApproval(null);
      setApprovals([]);
      setExecutions([]);
      setSelectedTrace(null);
    } catch (e) {
      console.error("Failed to create thread:", e);
    }
  };

  const switchThread = (id: string) => {
    if (id === activeThreadId) return;
    setActiveThreadId(id);
    // In a real app, we might fetch history from backend here.
    // For now, we'll clear local state to start fresh in the new context.
    useAppStore.setState({ messages: [] });
    setActivities([]);
    setTaskPlan(createEmptyTaskPlan());
    clearResearchRuns();
    latestCodeFilenameRef.current = null;
    setCodeSessions([]);
    setActiveCodeTab(0);
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

  const deleteThread = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (threads.length <= 1) return;
    try {
      await fetch(`${apiBase}/threads/${encodeURIComponent(id)}`, { method: "DELETE" });
    } catch (e2) {
      console.error("Failed to delete thread:", e2);
    }
    const nextThreads = threads.filter((t) => t.id !== id);
    setThreads(nextThreads);
    if (id === activeThreadId && nextThreads[0]) {
      switchThread(nextThreads[0].id);
    }
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
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: "Settings saved.", at: Date.now() });
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

  const runSettingsTest = async (target: "openai" | "gemini" | "tavily" | "local" | "ollama") => {
    setSettingsTesting((m) => ({ ...m, [target]: true }));
    try {
      const payload: any = { target };
      if (target === "openai") {
        payload.api_key = String(settingsDraft?.openai?.api_key || "") === "***" ? "" : String(settingsDraft?.openai?.api_key || "");
        setSettingsTestedKeys((m) => ({ ...m, openai: payload.api_key }));
      } else if (target === "gemini") {
        payload.api_key = String(settingsDraft?.gemini?.api_key || "") === "***" ? "" : String(settingsDraft?.gemini?.api_key || "");
        setSettingsTestedKeys((m) => ({ ...m, gemini: payload.api_key }));
      } else if (target === "tavily") {
        payload.api_key = String(settingsDraft?.tavily_api_key || "") === "***" ? "" : String(settingsDraft?.tavily_api_key || "");
        setSettingsTestedKeys((m) => ({ ...m, tavily: payload.api_key }));
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

  const timeline = useMemo<TimelineItem[]>(() => {
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
    merged.sort((a, b) => a.at - b.at);
    return merged;
  }, [messages, activities]);

  const lastMsgLen = messages.length ? (messages[messages.length - 1]?.text || "").length : 0;
  const activityLen = activities.length;

  const scrollChatToBottom = (behavior: ScrollBehavior = "smooth", force: boolean = false) => {
    try {
      if (!force && !stickToBottomRef.current) return;
      const el = chatScrollRef.current;
      if (el) el.scrollTo({ top: el.scrollHeight, behavior });
    } catch {
      // ignore
    }
  };

  const onChatScroll = () => {
    const el = chatScrollRef.current;
    if (!el) return;
    // Generous threshold to avoid un-sticking when large text blocks rapidly render
    const threshold = 300;
    const distFromBottom = el.scrollHeight - Math.ceil(el.scrollTop) - el.clientHeight;
    stickToBottomRef.current = distFromBottom <= threshold;
  };

  useEffect(() => {
    // initial mount / tab switch
    if (leftTab === "chat") {
      requestAnimationFrame(() => scrollChatToBottom("auto", true));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leftTab]);

  useEffect(() => {
    // new messages/activities should keep you pinned to bottom unless you scrolled up
    if (leftTab !== "chat") return;
    const timerId = setTimeout(() => {
      scrollChatToBottom(streaming ? "auto" : "smooth");
    }, 50);
    return () => clearTimeout(timerId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeline.length, lastMsgLen, activityLen, streaming]);

  const sendText = async (overrideText?: string) => {
    const raw = overrideText ?? input;
    if (!raw.trim()) return;

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

    const userMsg: Message = { id: crypto.randomUUID(), role: "user", text: raw, at: Date.now() };
    addMessage(userMsg);
    setInput("");
    setUserIsTyping(false);
    if (userTypingTimerRef.current) clearTimeout(userTypingTimerRef.current);
    setDocSources([]);
    setActivities([]);
    setStreaming(true);
    try {
      const resp = await fetch(`${apiBase}/query/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: requestText,
          include_memory: true,
          thread_id: activeThreadId,
          workspace: workspaceMode,
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
      const upsertTool = (evt: AgentStreamEvent) => {
        if (evt.type === "tool_start") {
          toolInfoRef.current[evt.id] = { name: evt.name, input: evt.input };
          setActivities((prev) => {
            return [
              ...prev,
              {
                kind: "tool",
                id: evt.id,
                name: evt.name,
                input: evt.input,
                status: "running",
                at: Date.now(),
              },
            ];
          });
          return;
        }

        if (evt.type === "tool_end") {
          const info = toolInfoRef.current[evt.id];
          if (info?.name === "web_search") {
            const normalized = normalizeResearchRun(evt.research) || buildResearchRunFromToolEvent(evt.id, info?.name || evt.name || "", info?.input || "", evt.output || "", evt.at || Date.now());
            if (normalized) {
              prependResearchRun(normalized);
            }
            const count = normalized?.evidence_count || 0;
            const summary = count ? `Captured ${count} sources (see Research panel)` : "No sources found";
            setActivities((prev) =>
              prev.map((p) => (p.kind === "tool" && p.id === evt.id ? { ...p, status: "done", output: summary } : p))
            );
            return;
          }
          // Capture code blocks for CodeVisualizer
          const codingTools = new Set(["file_write", "file_read", "artifact_write", "terminal_run", "notepad_write"]);
          if (codingTools.has(info?.name || "")) {
            const rawInput = info?.input || "";
            const filename = rawInput.split(/[\n,]/)[0]?.replace(/^.*?['"]([^'"]+)['"].*$/, "$1") || info?.name || "output";
            const lang = info?.name === "terminal_run" ? "bash" : filename.split(".").pop() || "text";
            const content = evt.output || "";
            if (content.length > 0) {
              latestCodeFilenameRef.current = filename;
              setCodeSessions((prev) => {
                const existing = prev.find((session) => session.filename === filename);
                let nextSession: CodeDiffSession;

                if (info?.name === "file_read") {
                  nextSession = {
                    filename,
                    language: lang,
                    originalContent: content,
                    currentContent: content,
                    status: "read",
                    summary: `Loaded ${content.length} chars`,
                  };
                } else if (info?.name === "file_write") {
                  if (isFileWriteSummary(content)) {
                    nextSession = {
                      filename,
                      language: lang,
                      originalContent: existing?.originalContent || existing?.currentContent || "",
                      currentContent: existing?.currentContent || "",
                      status: "saved",
                      summary: content,
                    };
                  } else {
                    nextSession = {
                      filename,
                      language: lang,
                      originalContent: existing?.originalContent || existing?.currentContent || "",
                      currentContent: content,
                      status: "draft",
                      summary: `Preview ${content.length} chars`,
                    };
                  }
                } else {
                  nextSession = {
                    filename,
                    language: lang,
                    originalContent: content,
                    currentContent: content,
                    status: "output",
                    summary: info?.name === "terminal_run" ? "Terminal output" : undefined,
                  };
                }

                const [nextSessions] = replaceCodeSession(prev, nextSession);
                return nextSessions;
              });
              setVisualizerPin("coding");
            }
          }
          // For file tools, show a short summary in the activity feed (full content is in code panel)
          const toolName = info?.name || "";
          const isFileOp = codingTools.has(toolName);
          const activityOutput = isFileOp && (evt.output || "").length > 200
            ? `${toolName === "file_read" ? "Read" : "Wrote"} ${(evt.output || "").length} chars → Code panel`
            : evt.output;
          setActivities((prev) =>
            prev.map((p) => (p.kind === "tool" && p.id === evt.id ? { ...p, status: "done", output: activityOutput } : p))
          );
          return;
        }

        if (evt.type === "tool_error") {
          setActivities((prev) =>
            prev.map((p) => (p.kind === "tool" && p.id === evt.id ? { ...p, status: "error", output: evt.error } : p))
          );
          setEchoReaction("error");
        }
      };

      while (true) {
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

          if (evt.type === "task_plan" || evt.type === "task_step" || evt.type === "task_reflection") {
            setTaskPlan((prev) => taskPlanReducer(prev, evt));
          } else if (evt.type === "tool_start" || evt.type === "tool_end" || evt.type === "tool_error") {
            upsertTool(evt);
          } else if (evt.type === "thinking") {
            const content = (evt.content || "").trim();
            if (content) {
              setActivities((prev) => {
                if (prev.some((p) => p.kind === "thinking" && p.content === content)) return prev;
                return [...prev, { kind: "thinking", id: crypto.randomUUID(), content, at: Date.now() }];
              });
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
            setAgentMode((evt as any).agent_mode);
          } else if (evt.type === "error") {
            setStreaming(false);
            setActivities((prev) => [
              ...prev,
              { kind: "error", id: crypto.randomUUID(), message: evt.message, at: Date.now() },
            ]);
            setEchoReaction("error");
          } else if (evt.type === "final") {
            const reply = evt.response || "(no response)";
            const spoken = (evt.spoken_text || "").trim();
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
              replaceResearchRuns(evt.research.map((item) => normalizeResearchRun(item)).filter((item): item is ResearchRun => Boolean(item)));
            }
            const botMsg: Message = { id: crypto.randomUUID(), role: "assistant", text: reply, at: Date.now() };
            addMessage(botMsg);
            // Speak exactly what the user sees in the chat bubble.
            // Only use backend-provided spoken_text if it matches the displayed reply.
            const speakVal = spoken && spoken === reply.trim() ? spoken : reply;
            speakText(speakVal);
            setStreaming(false);
            setEchoReaction("success");
            setAgentMode("idle");
            refreshPendingApproval(activeThreadId);
            refreshApprovals(activeThreadId);
            refreshExecutions(activeThreadId);
          }
        }
      }
    } catch (err) {
      const msg = String(err);
      const pretty = msg.includes("Failed to fetch") ? `Backend offline (${apiBase})` : msg;
      setBackendOnline(false);
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${pretty}`, at: Date.now() });
      setActivities((prev) => [
        ...prev,
        { kind: "error", id: crypto.randomUUID(), message: pretty, at: Date.now() },
      ]);
      setEchoReaction("error");
    } finally {
      setStreaming(false);
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
    if (leftTab === "memory") refreshMemory();
    if (leftTab === "docs") refreshDocuments();
    if (leftTab === "soul") refreshSoul();
    if (leftTab === "services") refreshServices();
    if (leftTab === "projects") refreshProjects();
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

  const showModelPicker =
    providerDraft.provider === "openai" ||
    providerDraft.provider === "gemini" ||
    providerModels.length > 0;
  const modelPickerOptions = showModelPicker
    ? (providerDraft.provider === "openai" ? openaiModelOptions : providerDraft.provider === "gemini" ? geminiModelOptions : providerModels)
    : [providerDraft.model || "Default model"];
  const modelPickerValue = showModelPicker ? providerDraft.model : modelPickerOptions[0];

  return (
    <div
      style={{
        minHeight: "100vh",
        background: colors.bg,
        color: colors.text,
        overflow: "hidden",
      }}
    >
      <style>{globalCss}</style>
      <div
        className="app-shell"
        style={{
          gridTemplateColumns: showVisualizer ? "1fr 1fr" : "1fr",
        }}
      >
        {showVisualizer ? (
          <div className="visualizer-pane" style={{ display: "flex", flexDirection: "column", position: "relative" }}>
            {/* Mode Indicator Pills */}
            <div style={{
              display: "flex",
              gap: 8,
              padding: "6px 8px",
              justifyContent: "center",
              alignItems: "center",
              background: "linear-gradient(135deg, rgba(255,255,255,0.08) 0%, rgba(255,255,255,0.02) 100%)",
              borderRadius: "20px",
              border: "1px solid rgba(255,255,255,0.1)",
              boxShadow: "inset 0 1px 1px rgba(255,255,255,0.15), 0 4px 12px rgba(0,0,0,0.3)",
              backdropFilter: "blur(20px)",
              WebkitBackdropFilter: "blur(20px)",
              margin: "12px auto 8px auto",
              width: "fit-content",
              zIndex: 10,
            }}>
              {(["ring", "research", "coding", "tasks"] as const).map((m) => {
                const effectiveMode = visualizerPin || (agentMode === "research" ? "research" : agentMode === "coding" ? "coding" : "ring");
                const isActive = effectiveMode === m;
                const isPinned = visualizerPin === m;
                const labels: Record<string, { icon: string; text: string }> = {
                  ring: { icon: "🤖", text: "Avatar" },
                  research: { icon: "🔍", text: "Research" },
                  coding: { icon: "💻", text: "Code" },
                  tasks: { icon: "📋", text: "Tasks" }
                };
                return (
                  <button
                    key={m}
                    onClick={() => setVisualizerPin(isPinned ? null : m)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "6px",
                      padding: "8px 16px",
                      borderRadius: "14px",
                      fontSize: 13,
                      fontWeight: 600,
                      cursor: "pointer",
                      border: isActive ? "1px solid rgba(255,255,255,0.2)" : "1px solid transparent",
                      background: isActive ? "linear-gradient(135deg, rgba(255,255,255,0.15) 0%, rgba(255,255,255,0.05) 100%)" : "transparent",
                      color: isActive ? "#ffffff" : "rgba(255,255,255,0.6)",
                      transition: "all 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
                      boxShadow: isActive ? "inset 0 1px 1px rgba(255,255,255,0.3), 0 2px 8px rgba(0,0,0,0.2)" : "none",
                      textDecoration: isPinned ? "underline" : "none",
                    }}
                  >
                    <span style={{ fontSize: "16px", filter: "brightness(0) invert(1)", opacity: isActive ? 1 : 0.7 }}>
                      {labels[m].icon}
                    </span>
                    <span style={{ textShadow: isActive ? "0 0 8px rgba(255,255,255,0.4)" : "none" }}>{labels[m].text}</span>
                  </button>
                );
              })}
            </div>
            {/* Visualizer Content */}
            {(() => {
              const effectiveMode = visualizerPin || (agentMode === "research" ? "research" : agentMode === "coding" ? "coding" : "ring");
              return (
                <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", overflow: effectiveMode === "ring" ? "visible" : "hidden" }}>
                  {(() => {
                    if (effectiveMode === "research") {
                      return (
                        <div style={{ width: "100%", height: "100%", padding: "0 20px 20px", overflowY: "auto", display: "flex", flexDirection: "column", gap: 12 }}>
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
                        <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
                          {/* Tab bar: always show Files tab + any code session tabs */}
                          <div style={{
                            display: "flex",
                            gap: 2,
                            padding: "8px 12px 0",
                            overflowX: "auto",
                            scrollbarWidth: "none",
                            flexShrink: 0,
                          }}>
                            <button
                              onClick={() => setActiveCodeTab(-1)}
                              style={{
                                padding: "6px 14px",
                                borderRadius: "8px 8px 0 0",
                                fontSize: 11,
                                fontWeight: 600,
                                fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                                cursor: "pointer",
                                border: "none",
                                background: (activeCodeTab === -1 || codeSessions.length === 0) ? "rgba(255,255,255,0.08)" : "transparent",
                                color: (activeCodeTab === -1 || codeSessions.length === 0) ? "#e2e8f0" : "rgba(255,255,255,0.3)",
                                borderBottom: (activeCodeTab === -1 || codeSessions.length === 0) ? "2px solid rgba(139,92,246,0.7)" : "2px solid transparent",
                                transition: "all 0.2s",
                                whiteSpace: "nowrap",
                              }}
                            >
                              📂 Files
                            </button>
                            {codeSessions.map((session, i) => (
                              <button
                                key={session.filename}
                                onClick={() => setActiveCodeTab(i)}
                                style={{
                                  padding: "6px 14px",
                                  borderRadius: "8px 8px 0 0",
                                  fontSize: 11,
                                  fontWeight: 600,
                                  fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                                  cursor: "pointer",
                                  border: "none",
                                  background: i === activeCodeTab ? "rgba(255,255,255,0.08)" : "transparent",
                                  color: i === activeCodeTab ? "#e2e8f0" : "rgba(255,255,255,0.3)",
                                  borderBottom: i === activeCodeTab ? "2px solid rgba(139,92,246,0.7)" : "2px solid transparent",
                                  transition: "all 0.2s",
                                  whiteSpace: "nowrap",
                                }}
                              >
                                {session.language === "bash" ? "⬛ Terminal" : `📄 ${session.filename.split("/").pop()}`}
                              </button>
                            ))}
                          </div>
                          {/* Content area */}
                          {(activeCodeTab === -1 || codeSessions.length === 0) ? (
                            <div style={{ flex: 1, overflow: "hidden" }}>
                              <WorkspaceExplorer apiBase={apiBase} />
                            </div>
                          ) : (
                            <div style={{
                              flex: 1,
                              margin: "0 12px 12px",
                              borderRadius: "0 0 12px 12px",
                              background: activeCodeSession?.language === "bash" ? "#0d1117" : "#1a1b26",
                              border: "1px solid rgba(255,255,255,0.06)",
                              overflow: "auto",
                              position: "relative",
                            }}>
                              {activeCodeSession ? (
                                <InlineCodeDiff
                                  session={activeCodeSession}
                                  onAccept={activeCodeSession.pendingConfirmation ? () => sendText("confirm") : undefined}
                                  onDecline={activeCodeSession.pendingConfirmation ? () => sendText("cancel") : undefined}
                                />
                              ) : null}
                            </div>
                          )}
                        </div>
                      );
                    }
                    if (effectiveMode === "tasks") {
                      return (
                        <div style={{ width: "100%", height: "100%", overflow: "hidden" }}>
                          <TodoPanel apiBase={apiBase} colors={colors} variant="visualizer" />
                        </div>
                      );
                    }
                    const hasRunningTool = activities.some(a => a.kind === "tool" && a.status === "running");
                    const isThinking = !listening && !speaking && (streaming || hasRunningTool);
                    // Find the latest running tool, if any
                    const latestRunningTool = [...activities].reverse().find(a => a.kind === "tool" && a.status === "running") as any;
                    const currentToolCategory = latestRunningTool ? getToolCategory(latestRunningTool.name) : "generic";
                    const activeToolName = latestRunningTool?.name;
                    const thinkingText = latestRunningTool ? getToolDisplayDetails(latestRunningTool.name, latestRunningTool.input) : "processing...";
                    return (
                      <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center", transform: "translateY(-24px)" }}>
                        <SquareAvatarVisual
                          speaking={speaking}
                          backendOnline={backendOnline}
                          isThinking={isThinking}
                          thinkingText={thinkingText}
                          activeToolName={activeToolName}
                          heartbeatEnabled={settingsDraft?.heartbeat_enabled}
                          toolCategory={currentToolCategory}
                          userIsTyping={userIsTyping}
                          pendingConfirmation={pendingApproval?.has_pending || false}
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
              <span>{leftTab === "chat" ? "EchoSpeak" : leftTab === "research" ? "Research" : leftTab === "memory" ? "Memory" : leftTab === "capabilities" ? "Capabilities" : leftTab === "approvals" ? "Approvals" : leftTab === "executions" ? "Executions" : leftTab === "projects" ? "Projects" : leftTab === "routines" ? "Routines" : leftTab === "settings" ? "Settings" : leftTab === "soul" ? "Soul" : leftTab === "avatar_editor" ? "Avatar Editor" : leftTab === "services" ? "Services" : "Documents"}</span>
              {activeProjectId && leftTab === "chat" && (
                <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 6, background: "linear-gradient(135deg, rgba(34,197,94,0.15), rgba(34,197,94,0.05))", border: "1px solid rgba(34,197,94,0.25)", color: "#22c55e", fontWeight: 600, marginLeft: 8 }}>
                  📁 {projects.find(p => p.id === activeProjectId)?.name || "Project Active"}
                </span>
              )}
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
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
              {leftTab === "chat" && (
                <>
                  <div className="chat-scroll" style={{ flex: 1 }} ref={chatScrollRef} onScroll={onChatScroll}>
                    {taskPlan.active && taskPlan.tasks.length > 0 && (
                      <TaskChecklist plan={taskPlan} />
                    )}
                    <AnimatePresence initial={false}>
                      {timeline.map((t) =>
                        t.kind === "message" ? (
                          <ChatBubble
                            key={`msg-${t.id}`}
                            msg={t.msg}
                            streaming={streaming}
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
                          <ActivityCard key={`act-${t.id}`} item={t.item} />
                        )
                      )}
                    </AnimatePresence>
                    <div ref={chatBottomRef} style={{ height: 1 }} />
                  </div>
                  <div className="input-bar">
                    <div className="input-row">
                      <div className="input-side-tools">
                        <button
                          className={`mic-button ${listening ? "active" : ""}`}
                          type="button"
                          onClick={() => listening ? (stop(), setListening(false), setStreaming(false)) : start()}
                        >
                          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" fill="currentColor" />
                            <path d="M19 10v2a7 7 0 0 1-14 0v-2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                          </svg>
                        </button>
                        <button
                          className={`mic-button ${monitoring ? "active" : ""}`}
                          type="button"
                          onClick={() => setMonitoring(v => { const n = !v; if (n) refreshMonitor(); return n; })}
                        >
                          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <rect x="2" y="4" width="20" height="12" rx="2" stroke="currentColor" strokeWidth="2" />
                            <path d="M12 16v4M8 20h8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                          </svg>
                        </button>
                      </div>
                      <input
                        className="input-field"
                        value={input}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) => {
                          setInput(e.target.value);
                          setUserIsTyping(true);
                          if (userTypingTimerRef.current) clearTimeout(userTypingTimerRef.current);
                          userTypingTimerRef.current = window.setTimeout(() => setUserIsTyping(false), 1500);
                        }}
                        onKeyDown={(e: React.KeyboardEvent<HTMLInputElement>) => {
                          if (e.key === "Enter" && !e.shiftKey) {
                            e.preventDefault();
                            sendText();
                          }
                        }}
                        placeholder="Ask Echo anything..."
                      />
                      <ContextRing messages={messages} contextWindow={providerInfo?.context_window || 0} />
                      <button className="send-button" onClick={() => sendText()} type="button">
                        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                          <path d="M5 12L19 12M19 12L13 6M19 12L13 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      </button>
                    </div>
                    <div className="controls-row">
                      <div className="control-slot session-slot">
                        <button
                          type="button"
                          className="toolbar-button"
                          onClick={() => setShowSessions(!showSessions)}
                          title="Sessions"
                          style={{
                            background: showSessions ? "rgba(255,255,255,0.1)" : "transparent",
                          }}
                        >
                          <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                            </svg>
                            <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{threads.find(t => t.id === activeThreadId)?.name || "Session"}</span>
                          </span>
                          <span style={{ fontSize: 10, opacity: 0.6 }}>{showSessions ? "▲" : "▼"}</span>
                        </button>

                        <AnimatePresence>
                          {showSessions && (
                            <motion.div
                              initial={{ opacity: 0, y: 10, scale: 0.95 }}
                              animate={{ opacity: 1, y: 0, scale: 1 }}
                              exit={{ opacity: 0, y: 10, scale: 0.95 }}
                              style={{
                                position: "absolute",
                                bottom: "100%",
                                left: 0,
                                marginBottom: 8,
                                width: 240,
                                background: colors.panel2,
                                border: `1px solid ${colors.line}`,
                                borderRadius: 12,
                                boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.4), 0 8px 10px -6px rgba(0, 0, 0, 0.4)",
                                zIndex: 100,
                                padding: 8,
                                display: "flex",
                                flexDirection: "column",
                                gap: 4
                              }}
                            >
                              <div style={{ padding: "4px 8px 8px 8px", fontSize: 11, fontWeight: 600, color: colors.textDim, textTransform: "uppercase", letterSpacing: 0.5 }}>
                                Recent Sessions
                              </div>
                              <div style={{ maxHeight: 300, overflowY: "auto", display: "flex", flexDirection: "column", gap: 2 }}>
                                {threads.map((t) => (
                                  <div
                                    key={t.id}
                                    onClick={() => {
                                      switchThread(t.id);
                                      setShowSessions(false);
                                    }}
                                    style={{
                                      padding: "8px 10px",
                                      borderRadius: 8,
                                      background: t.id === activeThreadId ? "rgba(255,255,255,0.08)" : "transparent",
                                      cursor: "pointer",
                                      display: "flex",
                                      justifyContent: "space-between",
                                      alignItems: "center",
                                      transition: "background 0.2s"
                                    }}
                                    onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(255,255,255,0.05)")}
                                    onMouseLeave={(e) => (e.currentTarget.style.background = t.id === activeThreadId ? "rgba(255,255,255,0.08)" : "transparent")}
                                  >
                                    <div style={{ display: "flex", flexDirection: "column", gap: 2, overflow: "hidden" }}>
                                      <span style={{ fontSize: 13, fontWeight: t.id === activeThreadId ? 600 : 400, color: t.id === activeThreadId ? colors.text : colors.textDim, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                                        {t.name}
                                      </span>
                                      <span style={{ fontSize: 10, color: colors.textDim }}>{new Date(t.at).toLocaleDateString()}</span>
                                    </div>
                                    {threads.length > 1 && (
                                      <button
                                        onClick={(e) => deleteThread(t.id, e)}
                                        style={{
                                          background: "transparent",
                                          border: "none",
                                          color: colors.textDim,
                                          cursor: "pointer",
                                          padding: 4,
                                          borderRadius: 4,
                                          display: "flex",
                                          alignItems: "center",
                                          justifyContent: "center"
                                        }}
                                        onMouseEnter={(e) => (e.currentTarget.style.color = colors.danger)}
                                        onMouseLeave={(e) => (e.currentTarget.style.color = colors.textDim)}
                                      >
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2v2"></path></svg>
                                      </button>
                                    )}
                                  </div>
                                ))}
                              </div>
                              <div style={{ height: 1, background: colors.line, margin: "4px 0" }} />
                              <button
                                onClick={() => {
                                  createNewThread();
                                  setShowSessions(false);
                                }}
                                style={{
                                  padding: "10px",
                                  borderRadius: 8,
                                  background: "rgba(255,255,255,0.05)",
                                  border: `1px dashed ${colors.line}`,
                                  color: colors.text,
                                  cursor: "pointer",
                                  fontSize: 12,
                                  fontWeight: 600,
                                  display: "flex",
                                  alignItems: "center",
                                  justifyContent: "center",
                                  gap: 8,
                                  transition: "background 0.2s"
                                }}
                                onMouseEnter={(e) => (e.currentTarget.style.background = "rgba(255,255,255,0.1)")}
                                onMouseLeave={(e) => (e.currentTarget.style.background = "rgba(255,255,255,0.05)")}
                              >
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                                New Session
                              </button>
                            </motion.div>
                          )}
                        </AnimatePresence>
                      </div>
                      <div className="control-slot mode-slot">
                        <select
                          className="mode-picker"
                          value={workspaceMode}
                          onChange={(e) => {
                            const v = (e.target.value || "auto") as WorkspaceMode;
                            setWorkspaceMode(v);
                            try { localStorage.setItem("echospeak_workspace_mode", v); } catch { }
                          }}
                          title="Mode"
                        >
                          {workspaceModes.map(m => (
                            <option key={m} value={m}>{m}</option>
                          ))}
                        </select>
                      </div>
                      <div className="control-slot provider-slot">
                        <div className="inline-switcher">
                          <select
                            className="provider-picker"
                            value={providerDraft.provider}
                            onChange={(e) => {
                              const p = e.target.value;
                              setProviderDraft(d => ({ ...d, provider: p, model: p === "openai" ? openaiModelOptions[0] : p === "gemini" ? geminiModelOptions[0] : (providerModels[0] || d.model) }));
                            }}
                            disabled={switchingProvider || lmStudioOnly}
                          >
                            {(providerInfo?.available_providers || fallbackProviders)
                              .filter(p => !lmStudioOnly || p.id === "lmstudio")
                              .map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                          </select>
                        </div>
                      </div>
                      <div className="control-slot model-slot">
                        <select
                          className="model-picker"
                          value={modelPickerValue}
                          onChange={(e) => {
                            if (!showModelPicker) return;
                            setProviderDraft(d => ({ ...d, model: e.target.value }));
                          }}
                          disabled={switchingProvider || !showModelPicker}
                        >
                          {modelPickerOptions.map(m => (
                            <option key={m} value={m}>{m}</option>
                          ))}
                        </select>
                      </div>
                    </div>
                  </div>
                </>
              )}

              {/* Research Tab */}
              {leftTab === "research" && (
                <ResearchPanel
                  colors={colors}
                  runs={research}
                  selectedVoice={selectedVoice}
                  voices={voices}
                  onSelectedVoiceChange={setSelectedVoice}
                  onClear={clearResearchRuns}
                />
              )}

              {/* Memory Tab */}
              {leftTab === "memory" && (
                <>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: -12, flexWrap: "wrap" }}>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={refreshMemory}
                      type="button"
                    >
                      Refresh
                    </button>
                    <button
                      className="icon-button"
                      style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
                      onClick={async () => {
                        try {
                          const res = await fetch(`${apiBase}/memory/compact?thread_id=${activeThreadId}`, { method: "POST" });
                          if (res.ok) {
                            refreshMemory();
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
                      <option value="conversation">Conversation</option>
                      <option value="preference">Preference</option>
                      <option value="profile">Profile</option>
                      <option value="project">Project</option>
                      <option value="contacts">Contacts</option>
                      <option value="note">Note</option>
                    </select>
                  </div>
                  {selectedMemoryIds.length > 0 && (
                    <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, padding: "8px 12px", background: colors.panel2, borderRadius: 8 }}>
                      <span style={{ fontSize: 12, color: colors.textDim }}>{selectedMemoryIds.length} selected</span>
                      <button
                        className="icon-button"
                        style={{ height: 28, padding: "0 10px", fontSize: 12 }}
                        type="button"
                        onClick={async () => {
                          for (const id of selectedMemoryIds) {
                            await fetch(`${apiBase}/memory/${id}?thread_id=${activeThreadId}`, { method: "DELETE" });
                          }
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
                              await fetch(`${apiBase}/memory/${id}?thread_id=${activeThreadId}`, {
                                method: "PUT",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ memory_type: newType }),
                              });
                            }
                            setSelectedMemoryIds([]);
                            refreshMemory();
                          }
                        }}
                      >
                        <option value="">Set Type</option>
                        <option value="conversation">Conversation</option>
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
                                    <option value="conversation">Conversation</option>
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
                                      await fetch(`${apiBase}/memory/${m.id}?thread_id=${activeThreadId}`, {
                                        method: "PUT",
                                        headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify({ text: editingMemoryText }),
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
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: -12 }}>
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
                            <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Terminal Allowlist (first token)</label>
                            <input
                              type="text"
                              className="input-field"
                              value={Array.isArray(settingsDraft.terminal_command_allowlist) ? settingsDraft.terminal_command_allowlist.join(",") : ""}
                              placeholder="git,rg,ls,cat,find,grep,python,python3,uv,pytest,npm,npx,node,go,make"
                              onChange={(e) =>
                                updateDraft(
                                  "terminal_command_allowlist",
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
                            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
                              <div style={{ flex: "2 1 340px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>
                                  Tavily API key
                                  <RequiredBadge issueKey="tavily_api_key" />
                                </label>
                                <div style={{ display: "flex", gap: 8 }}>
                                  <input
                                    type="password"
                                    className="input-field"
                                    value={String(settingsDraft.tavily_api_key || "")}
                                    placeholder="tvly-..."
                                    onChange={(e) => {
                                      const next = e.target.value;
                                      updateDraft("tavily_api_key", next);
                                      setSettingsTests((m) => ({ ...m, tavily: null }));
                                      setSettingsTestedKeys((m) => {
                                        const copy = { ...m };
                                        delete (copy as any).tavily;
                                        return copy;
                                      });
                                    }}
                                    style={{ flex: 1, padding: "10px 14px", fontSize: 14, borderColor: isError("tavily_api_key") ? colors.danger : undefined }}
                                  />
                                  <button
                                    className="icon-button"
                                    style={{ padding: "0 12px", fontSize: 12 }}
                                    type="button"
                                    onClick={() => runSettingsTest("tavily")}
                                    disabled={Boolean(settingsTesting.tavily)}
                                  >
                                    {settingsTesting.tavily ? "..." : "Test"}
                                  </button>
                                </div>
                                {settingsTests.tavily ? (
                                  <div className="research-snippet" style={{ marginTop: 4, color: settingsTests.tavily.ok ? colors.textDim : colors.danger, fontSize: 11 }}>
                                    {settingsTests.tavily.message}
                                  </div>
                                ) : null}
                              </div>
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
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Tavily depth</label>
                                <select
                                  className="input-field"
                                  value={String(settingsDraft.tavily_search_depth || "advanced")}
                                  onChange={(e) => updateDraft("tavily_search_depth", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                >
                                  <option value="basic">basic</option>
                                  <option value="advanced">advanced</option>
                                </select>
                              </div>
                              <div style={{ flex: "1 1 180px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>Tavily max results</label>
                                <input
                                  type="number"
                                  className="input-field"
                                  value={Number(settingsDraft.tavily_max_results ?? 8)}
                                  min={1}
                                  max={10}
                                  onChange={(e) => updateDraft("tavily_max_results", Number(e.target.value || 0))}
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
                            <div style={{ fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }}>Memory & Planning</div>
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px", marginBottom: 12 }}>
                              <Toggle label="Action Plan" checked={Boolean(settingsDraft.action_plan_enabled)} onChange={(v) => updateDraft("action_plan_enabled", v)} />
                              <Toggle label="Action Parser" checked={Boolean(settingsDraft.action_parser_enabled)} onChange={(v) => updateDraft("action_parser_enabled", v)} />
                              <Toggle label="Multi-task Planner" checked={Boolean(settingsDraft.multi_task_planner_enabled)} onChange={(v) => updateDraft("multi_task_planner_enabled", v)} />
                              <Toggle label="Web Reflection / Retry" checked={Boolean(settingsDraft.web_task_reflection_enabled)} onChange={(v) => updateDraft("web_task_reflection_enabled", v)} />
                              <Toggle label="File Memory" checked={Boolean(settingsDraft.file_memory_enabled)} onChange={(v) => updateDraft("file_memory_enabled", v)} />
                              <Toggle label="Memory Flush" checked={Boolean(settingsDraft.memory_flush_enabled)} onChange={(v) => updateDraft("memory_flush_enabled", v)} />
                              <Toggle label="Memory Partitioning" checked={Boolean(settingsDraft.memory_partition_enabled)} onChange={(v) => updateDraft("memory_partition_enabled", v)} />
                              <Toggle label="Memory Importance Auto-save" checked={Boolean(settingsDraft.memory_importance_enabled)} onChange={(v) => updateDraft("memory_importance_enabled", v)} />
                              <Toggle label="Log Raw Memory Conversations" checked={Boolean(settingsDraft.file_memory_log_conversations)} onChange={(v) => updateDraft("file_memory_log_conversations", v)} />
                            </div>
                            <div style={{ display: "flex", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
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
                            <div style={{ display: "flex", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
                              <div style={{ flex: "2 1 280px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>File memory dir</label>
                                <input
                                  type="text"
                                  className="input-field"
                                  value={String(settingsDraft.file_memory_dir || "")}
                                  onChange={(e) => updateDraft("file_memory_dir", e.target.value)}
                                  style={{ width: "100%", padding: "10px 14px", fontSize: 14 }}
                                />
                              </div>
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
                              <div style={{ flex: "1 1 220px" }}>
                                <label style={{ display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }}>File memory max chars</label>
                                <input
                                  type="number"
                                  className="input-field"
                                  value={Number(settingsDraft.file_memory_max_chars ?? 2000)}
                                  onChange={(e) => updateDraft("file_memory_max_chars", Number(e.target.value || 0))}
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
                            <div style={{ fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }}>System & Tracing</div>
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
                            const res = await fetch(`${apiBase}/capabilities?thread_id=${activeThreadId}`);
                            const data = await res.json();
                            setCapabilitiesData(data);
                          } catch (e) {
                            console.error("Failed to fetch capabilities:", e);
                          }
                        }}
                      >
                        Refresh Capabilities
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
                              <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 4 }}>WORKSPACE</div>
                              <div style={{ fontSize: 14, fontWeight: 600 }}>{capabilitiesData.workspace?.name || capabilitiesData.workspace?.id || "Default"}</div>
                            </div>
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
                        action={{ tool: pendingApproval.action.tool, kwargs: pendingApproval.action.kwargs }}
                        riskLevel={pendingApproval.risk_level || pendingApproval.action.risk_level}
                        riskColor={pendingApproval.risk_color || undefined}
                        policyFlags={pendingApproval.policy_flags || pendingApproval.action.policy_flags}
                        sessionPermissions={pendingApproval.session_permissions || pendingApproval.action.session_permissions}
                        dryRunAvailable={Boolean(pendingApproval.dry_run_available)}
                        onConfirm={() => sendText("confirm")}
                        onCancel={() => sendText("cancel")}
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
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: -12, flexWrap: "wrap" }}>
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
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: -12, flexWrap: "wrap" }}>
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

