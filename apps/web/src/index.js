import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { create } from "zustand";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { SquareAvatarVisual } from "./components/SquareAvatarVisual";
import { getToolCategory, getToolDisplayDetails } from "./components/echoAnimationUtils";
import { InlineCodeDiff } from "./components/InlineCodeDiff";
import { WorkspaceExplorer } from "./components/WorkspaceExplorer";
import { TaskChecklist, createEmptyTaskPlan, taskPlanReducer } from "./components/TaskChecklist";
import { ResearchPanel } from "./features/research/ResearchPanel";
import { buildResearchRunFromToolEvent, normalizeResearchRun } from "./features/research/buildResearchRun";
import { useResearchStore } from "./features/research/store";
const openaiModelOptions = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "gpt-3.5-turbo"];
const geminiModelOptions = ["gemini-3.1-flash-lite-preview", "gemini-3.1-pro-preview", "gemini-3-flash-preview", "gemini-2.5-pro"];
const listableProviders = ["ollama", "lmstudio", "localai", "vllm"];
const isLmStudioOnlyLocked = (info) => {
    const providers = info?.available_providers || [];
    if (!providers.length)
        return false;
    return providers.length === 1 && providers[0].id === "lmstudio";
};
const workspaceModes = ["auto", "chat", "coding", "research"];
const fetchWithTimeout = async (url, init, timeoutMs = 4500) => {
    const controller = new AbortController();
    const id = setTimeout(() => controller.abort(), timeoutMs);
    try {
        return await fetch(url, { ...init, signal: controller.signal });
    }
    finally {
        clearTimeout(id);
    }
};
const normalizeTimestampMs = (value) => {
    const num = Number(value);
    if (!Number.isFinite(num) || num <= 0)
        return Date.now();
    return num < 1000000000000 ? num * 1000 : num;
};
const replaceCodeSession = (sessions, nextSession) => {
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
const isFileWriteSummary = (value) => /^(Wrote|Appended) \d+ chars to /.test(value);
const fallbackProviders = [
    { id: "openai", name: "OpenAI", local: false, description: "OpenAI GPT models" },
    { id: "gemini", name: "Google Gemini", local: false, description: "Google Gemini models" },
    { id: "ollama", name: "Ollama", local: true, description: "Local Ollama models" },
    { id: "lmstudio", name: "LM Studio (GGUF direct)", local: true, description: "LM Studio (GGUF direct via OpenAI-compatible API)" },
    { id: "localai", name: "LocalAI", local: true, description: "LocalAI (OpenAI compatible)" },
    { id: "vllm", name: "vLLM", local: true, description: "vLLM (OpenAI compatible)" },
    { id: "llama_cpp", name: "llama.cpp", local: true, description: "llama.cpp (local + OpenAI compatible)" },
];
const useAppStore = create((set) => ({
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
const globalCss = `
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
           height: 100vh;
           display: grid;
           gap: 0;
           padding: 0;
           background: ${colors.bg};
         }
         .visualizer-pane {
           display: flex;
           align-items: center;
           justify-content: center;
           background: rgba(0,0,0,0.2);
           border-right: 1px solid ${colors.line};
           height: 100vh;
           overflow: hidden;
         }
         .glow-panel {
           background: ${colors.panel};
           display: flex;
           flex-direction: column;
           height: 100vh;
           overflow: visible;
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
          overflow: visible;
          min-height: 0;
          gap: 24px;
        }
        .research-panel {
          display: flex;
          flex-direction: column;
          height: 100%;
          overflow: visible;
          gap: 20px;
        }
        .tab-bar {
          display: flex;
          flex-wrap: wrap;
           gap: 6px;
           padding-bottom: 8px;
           border-bottom: 1px solid rgba(255,255,255,0.08);
           overflow-y: visible;
           overflow-x: auto;
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
           padding-right: 8px;
         }
       .input-bar {
           margin-top: auto;
           display: flex;
           flex-direction: column;
           gap: 16px;
         }
         .input-row {
           display: flex;
           gap: 12px;
           align-items: flex-end;
         }
         .input-field {
           flex: 1;
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
           display: flex;
           align-items: center;
           gap: 12px;
         }

         /* Liquid Metal Base for Bottom Controls */
         .icon-button, .mic-button, .provider-picker, .model-picker, .mode-picker {
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
         .icon-button::before, .mic-button::before, .provider-picker::before, .model-picker::before, .mode-picker::before {
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
         .icon-button > *, .mic-button > *, .provider-picker > *, .model-picker > *, .mode-picker > * { position: relative; z-index: 1; }
         
         .icon-button:hover:not(:disabled), .mic-button:hover:not(:disabled), .provider-picker:hover:not(:disabled), .model-picker:hover:not(:disabled), .mode-picker:hover:not(:disabled) {
           background: linear-gradient(135deg, rgba(255,255,255,0.18), rgba(255,255,255,0.05));
           border-color: rgba(255, 255, 255, 0.4);
           transform: translateY(-2px);
           box-shadow: 0 6px 20px -4px rgba(0,0,0,0.25), inset 0 1px 0 rgba(255,255,255,0.3);
         }
         .icon-button:hover:not(:disabled)::before, .mic-button:hover:not(:disabled)::before, .provider-picker:hover:not(:disabled)::before, .model-picker:hover:not(:disabled)::before, .mode-picker:hover:not(:disabled)::before {
           opacity: 1;
           animation: liquid-metal-shift 0.9s ease-in-out infinite alternate;
         }
         .icon-button:active:not(:disabled), .mic-button:active:not(:disabled), .provider-picker:active:not(:disabled), .model-picker:active:not(:disabled), .mode-picker:active:not(:disabled) {
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
           margin-left: auto;
           display: flex;
           align-items: center;
           gap: 12px;
           padding: 0;
           background: transparent;
           border: none;
           border-radius: 0;
         }
         .switcher-dot {
           width: 6px;
           height: 6px;
           border-radius: 50%;
           background: #475569;
         }
         .switcher-dot.online { background: #22c55e; box-shadow: 0 0 8px #22c55e44; }
         .switcher-dot.offline { background: #ef4444; box-shadow: 0 0 8px #ef444444; }
         
         .provider-picker, .model-picker, .mode-picker {
           border-radius: 12px;
           font-size: 13px;
           font-weight: 600;
           outline: none;
           padding: 6px 12px;
           line-height: 1.2;
         }
         .provider-picker { max-width: 220px; }
         .model-picker { max-width: 320px; font-size: 12px; font-weight: 500; }
         .mode-picker { width: 120px; height: 38px; padding: 0 12px; border-radius: 8px; }

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
       `;
const sanitizeForTTS = (input) => {
    let text = input || "";
    text = text.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, "");
    text = text.replace(/[\u2300-\u23FF\u2600-\u27BF]/g, "");
    text = text.replace(/[\u200D\uFE0E\uFE0F]/g, "");
    return text.replace(/\s+/g, " ").trim();
};
const Toggle = ({ checked, onChange, label }) => {
    return (_jsxs("div", { style: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "4px 0" }, children: [_jsx("span", { style: { fontSize: 14, color: colors.text }, children: label }), _jsxs("div", { style: { display: "flex", alignItems: "center", gap: 10 }, children: [_jsx("span", { style: { fontSize: 12, fontWeight: 700, letterSpacing: "0.06em", color: checked ? colors.accent : colors.textDim }, children: checked ? "ON" : "OFF" }), _jsx("button", { type: "button", onClick: () => onChange(!checked), style: {
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
                        }, children: _jsx("div", { style: {
                                width: 18,
                                height: 18,
                                borderRadius: "50%",
                                background: "#fff",
                                position: "absolute",
                                left: checked ? 24 : 2,
                                transition: "all 0.2s cubic-bezier(0.4, 0, 0.2, 1)",
                                boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
                            } }) })] })] }));
};
const settingsSectionStyle = {
    background: "rgba(255, 255, 255, 0.02)",
    border: "1px solid rgba(255, 255, 255, 0.08)",
    borderRadius: "12px",
    padding: "20px",
    marginBottom: "20px",
};
const platformCardStyle = {
    padding: 16,
    background: "linear-gradient(135deg, rgba(255,255,255,0.05), rgba(255,255,255,0.015))",
    borderRadius: 16,
    border: "1px solid rgba(255,255,255,0.08)",
    boxShadow: "0 10px 30px -20px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.04)",
};
const PlatformHeader = ({ icon, title, subtitle, accent, }) => (_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }, children: [_jsx("div", { style: {
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
            }, children: icon }), _jsxs("div", { children: [_jsx("div", { style: { fontSize: 14, fontWeight: 700, color: colors.text }, children: title }), _jsx("div", { style: { fontSize: 12, color: colors.textDim }, children: subtitle })] })] }));
const chunkTextForTTS = (text, maxChars = 260) => {
    const cleaned = (text || "").replace(/\s+/g, " ").trim();
    if (!cleaned)
        return [];
    const parts = cleaned.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [cleaned];
    const chunks = [];
    let current = "";
    const pushCurrent = () => {
        if (current.trim())
            chunks.push(current.trim());
        current = "";
    };
    for (const raw of parts) {
        const part = raw.trim();
        if (!part)
            continue;
        const candidate = current ? `${current} ${part}` : part;
        if (candidate.length <= maxChars) {
            current = candidate;
            continue;
        }
        if (current)
            pushCurrent();
        if (part.length <= maxChars) {
            current = part;
            continue;
        }
        const words = part.split(/\s+/).filter(Boolean);
        let buf = "";
        for (const word of words) {
            const next = buf ? `${buf} ${word}` : word;
            if (next.length > maxChars) {
                if (buf)
                    chunks.push(buf);
                buf = word;
            }
            else {
                buf = next;
            }
        }
        if (buf)
            chunks.push(buf);
    }
    pushCurrent();
    return chunks.filter(Boolean);
};
let ttsSequence = 0;
const ttsTabId = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Math.random());
let ttsChannel = null;
try {
    ttsChannel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("echospeak_tts") : null;
}
catch {
    ttsChannel = null;
}
const stopTts = () => {
    ttsSequence += 1;
    try {
        if (typeof window !== "undefined" && "speechSynthesis" in window) {
            window.speechSynthesis.pause();
            window.speechSynthesis.cancel();
        }
    }
    catch { }
    useAppStore.getState().setSpeaking(false);
};
const pulseSpeaking = (ms) => {
    const { setSpeaking, bumpSpeechBeat } = useAppStore.getState();
    try {
        setSpeaking(true);
        bumpSpeechBeat();
        window.setTimeout(() => setSpeaking(false), Math.max(250, ms));
    }
    catch {
        // ignore
    }
};
if (ttsChannel) {
    try {
        ttsChannel.onmessage = (evt) => {
            const data = evt?.data;
            if (!data || typeof data !== "object")
                return;
            if (data.type === "tts_start" && data.tabId && data.tabId !== ttsTabId) {
                stopTts();
            }
        };
    }
    catch { }
}
const speakText = async (text) => {
    const cleaned = sanitizeForTTS(text);
    if (!cleaned)
        return;
    const { setSpeaking, bumpSpeechBeat, addMessage } = useAppStore.getState();
    const sequenceId = ++ttsSequence;
    try {
        if (typeof window !== "undefined" && window.localStorage?.getItem("echospeak.tts_debug") === "1") {
            console.debug("[EchoSpeak TTS] speakText len=%d text=", cleaned.length, cleaned);
        }
    }
    catch {
        // ignore
    }
    if (ttsChannel) {
        try {
            ttsChannel.postMessage({ type: "tts_start", tabId: ttsTabId, at: Date.now() });
        }
        catch { }
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
                text: "Speech is enabled but your browser reports 0 voices. On Linux this usually means the system TTS backend isn't installed/running. " +
                    "Try installing speech-dispatcher / espeak-ng and restarting the browser.",
                at: Date.now(),
            });
            return;
        }
    }
    catch { }
    try {
        // Some browsers (esp. Linux) get stuck in a paused state.
        window.speechSynthesis.resume();
    }
    catch { }
    try {
        // Aggressive flush to clear any stuck utterances before starting a new sequence
        window.speechSynthesis.pause();
        window.speechSynthesis.cancel();
        window.speechSynthesis.resume();
    }
    catch { }
    const chunks = chunkTextForTTS(cleaned, 260);
    if (!chunks.length)
        return;
    let beatPollTimer = null;
    const startBeat = () => {
        if (beatPollTimer == null) {
            beatPollTimer = window.setInterval(() => {
                try {
                    if (typeof window !== "undefined" && "speechSynthesis" in window) {
                        const isSpeaking = window.speechSynthesis.speaking || window.speechSynthesis.pending;
                        setSpeaking(isSpeaking);
                    }
                }
                catch { }
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
    const speakChunk = async (chunk) => new Promise((resolve, reject) => {
        const { speechEnabled, selectedVoice } = useAppStore.getState();
        if (!speechEnabled) {
            resolve();
            return;
        }
        // Start animation immediately; some browsers delay/skip onstart.
        startBeat();
        const utter = new SpeechSynthesisUtterance(chunk);
        // Prevent browser garbage collection bug that stops TTS mid-speech
        const win = window;
        win._activeUtterances = win._activeUtterances || [];
        win._activeUtterances.push(utter);
        if (selectedVoice) {
            const voices = window.speechSynthesis.getVoices();
            const found = voices.find(v => v.name === selectedVoice);
            if (found)
                utter.voice = found;
        }
        let done = false;
        const cleanup = (err) => {
            if (done)
                return;
            done = true;
            const active = win._activeUtterances;
            if (active) {
                const idx = active.indexOf(utter);
                if (idx > -1)
                    active.splice(idx, 1);
            }
            if (err)
                reject(err);
            else
                resolve();
        };
        const safetyTimeout = window.setTimeout(() => {
            scheduleBeatStop();
            addMessage({
                id: crypto.randomUUID(),
                role: "assistant",
                text: "Speech timed out (browser TTS glitch). Try toggling Speech off/on, or click Unlock Speech.",
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
            }
            catch { }
            window.speechSynthesis.speak(utter);
        }
        catch (e) {
            clearSafety();
            const msg = e instanceof Error ? e.message : String(e);
            addMessage({
                id: crypto.randomUUID(),
                role: "assistant",
                text: "Speech failed to start. If you're on Chrome/Edge, click anywhere in the page once and try again. Details: " +
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
    }
    catch (err) {
        stopBeat();
        const name = err?.name ? String(err.name) : "";
        if (name === "NotAllowedError" || name === "AbortError")
            return;
        setSpeaking(false);
    }
};
// Hook: mic capture -> browser SpeechRecognition
const useMicStreamer = (onFinalTranscript) => {
    const recRef = useRef(null);
    const transcriptRef = useRef("");
    const { setListening, setStreaming, addMessage } = useAppStore();
    const stopAll = (submitTranscript) => {
        const t = transcriptRef.current.trim();
        transcriptRef.current = "";
        if (submitTranscript && t) {
            try {
                onFinalTranscript?.(t);
            }
            catch {
                // ignore
            }
        }
        else if (submitTranscript && !t) {
            addMessage({
                id: crypto.randomUUID(),
                role: "assistant",
                text: "Mic: I didn't catch any speech. Try again and speak a bit longer.",
                at: Date.now(),
            });
        }
        try {
            recRef.current?.stop?.();
        }
        catch {
        }
        recRef.current = null;
        setListening(false);
        setStreaming(false);
    };
    const start = async () => {
        if (recRef.current)
            stopAll(false);
        try {
            const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
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
            rec.onresult = (evt) => {
                try {
                    const results = evt?.results;
                    if (!results || typeof results.length !== "number")
                        return;
                    let finals = "";
                    let interim = "";
                    for (let i = 0; i < results.length; i += 1) {
                        const res = results[i];
                        const txt = res && res[0] && typeof res[0].transcript === "string" ? String(res[0].transcript) : "";
                        if (!txt)
                            continue;
                        if (res.isFinal)
                            finals += txt.trim() + " ";
                        else
                            interim = txt.trim();
                    }
                    const fullText = (finals + interim).trim();
                    transcriptRef.current = fullText;
                    // Dispatch to the React component layer so it can update the input box and handle auto-send
                    window.dispatchEvent(new CustomEvent("echospeak-transcript", { detail: fullText }));
                }
                catch {
                    // ignore
                }
            };
            rec.onerror = (e) => {
                const msg = e?.error ? String(e.error) : "unknown";
                if (msg === "network") {
                    addMessage({
                        id: crypto.randomUUID(),
                        role: "assistant",
                        text: "Mic error: network. Your browser's SpeechRecognition service couldn't be reached. " +
                            "Make sure you're online, not blocking it with VPN/adblock/firewall, and use Chrome/Edge.",
                        at: Date.now(),
                    });
                }
                else {
                    addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Mic error: ${msg}`, at: Date.now() });
                }
                stopAll(false);
            };
            rec.onend = () => {
                stopAll(false);
            };
            rec.start();
        }
        catch (err) {
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
const ContextRing = ({ messages, contextWindow }) => {
    const [hover, setHover] = React.useState(false);
    if (!contextWindow || contextWindow <= 0)
        return null;
    const totalChars = messages.reduce((sum, m) => sum + (m.text?.length || 0), 0);
    const estimatedTokens = Math.round(totalChars / 3.5);
    const pct = Math.min(estimatedTokens / contextWindow, 1);
    const displayPct = Math.round(pct * 100);
    const size = 32;
    const stroke = 3;
    const radius = (size - stroke) / 2;
    const circumference = 2 * Math.PI * radius;
    const dashOffset = circumference * (1 - pct);
    const ringColor = pct > 0.85 ? "rgba(255,80,80,0.9)" : pct > 0.6 ? "rgba(255,180,60,0.9)" : "rgba(140,180,255,0.7)";
    const formatTokens = (n) => (n >= 1000000 ? `${(n / 1000000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n));
    return (_jsxs("div", { style: { position: "relative", display: "grid", placeItems: "center", width: size, height: size, cursor: "default", flexShrink: 0 }, onMouseEnter: () => setHover(true), onMouseLeave: () => setHover(false), children: [_jsxs("svg", { width: size, height: size, viewBox: `0 0 ${size} ${size}`, style: { transform: "rotate(-90deg)" }, children: [_jsx("circle", { cx: size / 2, cy: size / 2, r: radius, fill: "none", stroke: "rgba(255,255,255,0.08)", strokeWidth: stroke }), _jsx("circle", { cx: size / 2, cy: size / 2, r: radius, fill: "none", stroke: ringColor, strokeWidth: stroke, strokeLinecap: "round", strokeDasharray: circumference, strokeDashoffset: dashOffset, style: { transition: "stroke-dashoffset 0.4s ease, stroke 0.3s ease" } })] }), _jsxs("span", { style: {
                    position: "absolute", fontSize: 8, fontWeight: 700, color: ringColor,
                    letterSpacing: "-0.3px", lineHeight: 1, userSelect: "none",
                }, children: [displayPct, "%"] }), hover && (_jsxs("div", { style: {
                    position: "absolute", bottom: "calc(100% + 8px)", left: "50%", transform: "translateX(-50%)",
                    background: "rgba(20,22,30,0.95)", border: "1px solid rgba(255,255,255,0.12)",
                    borderRadius: 10, padding: "8px 12px", whiteSpace: "nowrap", zIndex: 999,
                    boxShadow: "0 4px 20px rgba(0,0,0,0.5)", backdropFilter: "blur(12px)",
                    fontSize: 12, color: colors.text, lineHeight: 1.5,
                }, children: [_jsx("div", { style: { fontWeight: 600, marginBottom: 2, color: ringColor }, children: "Context Window" }), _jsxs("div", { children: [_jsx("span", { style: { color: colors.textDim }, children: "Used \u2248" }), " ", formatTokens(estimatedTokens), " / ", formatTokens(contextWindow), " tokens"] }), _jsxs("div", { children: [_jsx("span", { style: { color: colors.textDim }, children: "Fill" }), " ", displayPct, "%"] })] }))] }));
};
const ChatBubble = ({ msg, streaming, onQuickReply }) => {
    const isUser = msg.role === "user";
    const isConfirmPrompt = !isUser
        ? (() => {
            const t = (msg.text || "").toLowerCase();
            if (!t)
                return false;
            if (t.includes("reply 'confirm'"))
                return true;
            if (t.includes('reply "confirm"'))
                return true;
            if (t.includes("confirm' to proceed") || t.includes('confirm" to proceed'))
                return true;
            if (t.includes("pending action") && t.includes("confirm"))
                return true;
            return false;
        })()
        : false;
    const canQuickReply = Boolean(isConfirmPrompt && onQuickReply && !streaming);
    return (_jsx(motion.div, { layout: true, initial: { opacity: 0, scale: 0.95, y: 10 }, animate: { opacity: 1, scale: 1, y: 0 }, exit: { opacity: 0, scale: 0.95, y: -8 }, transition: { duration: 0.28, ease: [0.16, 1, 0.3, 1] }, style: { display: "flex", justifyContent: isUser ? "flex-end" : "flex-start", position: "relative" }, children: _jsxs("div", { style: {
                position: "relative",
                maxWidth: "82%",
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
            }, children: [isUser ? (_jsx("div", { className: "chat-text", children: msg.text })) : (_jsx("div", { className: "chat-markdown", children: _jsx(ReactMarkdown, { remarkPlugins: [remarkGfm], children: msg.text }) })), !isUser && isConfirmPrompt ? (_jsxs("div", { style: { display: "flex", gap: 8, marginTop: 10 }, children: [_jsx("button", { onClick: () => onQuickReply?.("confirm"), disabled: !canQuickReply, style: {
                                padding: "8px 10px",
                                borderRadius: 8,
                                border: `1px solid ${colors.line}`,
                                background: canQuickReply ? "rgba(34,197,94,0.14)" : "rgba(148,163,184,0.12)",
                                color: colors.text,
                                cursor: canQuickReply ? "pointer" : "not-allowed",
                                fontSize: 13,
                                fontWeight: 600,
                            }, children: "Confirm" }), _jsx("button", { onClick: () => onQuickReply?.("cancel"), disabled: !canQuickReply, style: {
                                padding: "8px 10px",
                                borderRadius: 8,
                                border: `1px solid ${colors.line}`,
                                background: canQuickReply ? "rgba(239,68,68,0.12)" : "rgba(148,163,184,0.12)",
                                color: colors.text,
                                cursor: canQuickReply ? "pointer" : "not-allowed",
                                fontSize: 13,
                                fontWeight: 600,
                            }, children: "Cancel" })] })) : null, _jsx("div", { style: { marginTop: 4, fontSize: 10.5, color: colors.textDim }, children: new Date(msg.at).toLocaleTimeString() })] }) }));
};
const ThinkingActivityCard = ({ item }) => {
    const [expanded, setExpanded] = useState(false);
    const badge = { label: "Thinking", color: "rgba(140,160,255,0.9)", bg: "rgba(45,108,255,0.12)", border: "rgba(45,108,255,0.3)" };
    const content = (item.content || "").trim();
    const preview = content.length > 280 ? `${content.slice(0, 280).trimEnd()}…` : content;
    const body = expanded ? content : preview;
    return (_jsx(motion.div, { layout: true, initial: { opacity: 0, y: 10 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -8 }, transition: { duration: 0.22, ease: "easeOut" }, style: { display: "flex", justifyContent: "flex-start" }, children: _jsxs("div", { style: {
                maxWidth: "90%",
                width: "fit-content",
                background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))",
                backdropFilter: "blur(12px)",
                WebkitBackdropFilter: "blur(12px)",
                color: colors.text,
                border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: 12,
                padding: "12px 16px",
                boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)",
            }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 10, justifyContent: "space-between", flexWrap: "wrap" }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 10 }, children: [_jsx("div", { style: {
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
                                    }, children: _jsx("span", { children: badge.label }) }), _jsx("div", { style: { fontSize: 13, fontWeight: 650 }, children: "Model reasoning captured" })] }), content.length > preview.length ? (_jsx("button", { type: "button", onClick: () => setExpanded((v) => !v), style: {
                                border: `1px solid ${badge.border}`,
                                background: badge.bg,
                                color: badge.color,
                                borderRadius: 999,
                                padding: "4px 10px",
                                fontSize: 11,
                                fontWeight: 700,
                                cursor: "pointer",
                            }, children: expanded ? "Hide" : "Show" })) : null] }), _jsx("div", { style: { marginTop: 8, fontSize: 12.5, lineHeight: 1.5, color: colors.textDim, whiteSpace: "pre-wrap" }, children: body })] }) }));
};
const ActivityCard = ({ item }) => {
    if (item.kind === "thinking") {
        return _jsx(ThinkingActivityCard, { item: item });
    }
    if (item.kind === "memory") {
        return (_jsx(motion.div, { layout: true, initial: { opacity: 0, y: 5 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0 }, transition: { duration: 0.2 }, style: { display: "flex", justifyContent: "flex-start", marginLeft: "0px", marginTop: "-6px", marginBottom: "4px" }, children: _jsxs("div", { style: { fontSize: 11, color: "rgba(255,255,255,0.4)", display: "flex", alignItems: "center", gap: 6, fontWeight: 500 }, children: [_jsx("span", { style: { opacity: 0.7 }, children: "\u2713" }), _jsxs("span", { children: ["Memory saved (", item.memoryCount, ")"] })] }) }));
    }
    if (item.kind === "error") {
        const badge = { label: "Error", color: "rgba(255,120,140,0.95)", bg: "rgba(255,77,109,0.10)", border: "rgba(255,77,109,0.35)" };
        const title = "Agent error";
        const body = item.message;
        return (_jsx(motion.div, { layout: true, initial: { opacity: 0, y: 10 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -8 }, transition: { duration: 0.22, ease: "easeOut" }, style: { display: "flex", justifyContent: "flex-start" }, children: _jsxs("div", { style: {
                    maxWidth: "90%",
                    width: "fit-content",
                    background: colors.panel2,
                    color: colors.text,
                    border: `1px solid ${colors.line}`,
                    borderRadius: 10,
                    padding: "12px 14px",
                    boxShadow: "none",
                }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 10 }, children: [_jsx("div", { style: {
                                    fontSize: 10.5,
                                    fontWeight: 700,
                                    letterSpacing: 0.5,
                                    textTransform: "uppercase",
                                    padding: "4px 8px",
                                    borderRadius: 999,
                                    color: badge.color,
                                    background: badge.bg,
                                    border: `1px solid ${badge.border}`,
                                }, children: _jsx("span", { children: badge.label }) }), _jsx("div", { style: { fontSize: 13, fontWeight: 650 }, children: title })] }), _jsx("div", { style: { marginTop: 6, fontSize: 12.5, lineHeight: 1.5, color: colors.textDim, whiteSpace: "pre-wrap" }, children: body })] }) }));
    }
    const toolName = (item.name || "").toLowerCase();
    const isTerminal = item.kind === "tool" && toolName === "terminal_run";
    const isConsoleTool = isTerminal;
    // Apply the same "liquid metal" / dark aesthetic to both terminal and browser
    const badge = item.status === "running"
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
    return (_jsx(motion.div, { layout: true, initial: { opacity: 0, y: 10 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -8 }, transition: { duration: 0.22, ease: "easeOut" }, style: { display: "flex", justifyContent: "flex-start" }, children: _jsxs("div", { style: {
                maxWidth: "90%",
                width: "fit-content",
                background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))",
                backdropFilter: "blur(12px)",
                WebkitBackdropFilter: "blur(12px)",
                color: colors.text,
                border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: 14,
                padding: "10px 12px",
                boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)",
            }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 10 }, children: [_jsxs("div", { style: {
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
                            }, children: [item.kind === "tool" && item.status === "running" ? (_jsx("span", { style: {
                                        width: 8,
                                        height: 8,
                                        borderRadius: 99,
                                        background: colors.accent,
                                        boxShadow: "0 0 10px rgba(45,108,255,0.8)",
                                    } })) : null, _jsx("span", { children: badge.label })] }), title && _jsx("div", { style: { fontSize: 13, fontWeight: 650 }, children: title })] }), isConsoleTool ? (_jsxs("div", { style: { marginTop: 8, display: "flex", flexDirection: "column", gap: 8 }, children: [_jsx("div", { style: {
                                fontSize: 12.5,
                                lineHeight: 1.5,
                                color: colors.textDim,
                                whiteSpace: "pre-wrap",
                            }, children: item.status === "running" ? "Command" : "Result" }), _jsx("div", { style: {
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
                            }, children: body })] })) : (_jsx("div", { style: { marginTop: 6, fontSize: 12.5, lineHeight: 1.5, color: colors.textDim, whiteSpace: "pre-wrap" }, children: body }))] }) }));
};
const ConfirmationCard = ({ action, riskLevel = "safe", riskColor = "#22c55e", policyFlags = [], sessionPermissions = {}, dryRunAvailable = false, onConfirm, onCancel, onDryRun, }) => {
    const toolName = action?.tool || "unknown";
    const kwargs = action?.kwargs || {};
    const riskLabels = {
        safe: "Safe",
        moderate: "Moderate Risk",
        destructive: "High Risk",
    };
    const riskBgColors = {
        safe: "rgba(34,197,94,0.12)",
        moderate: "rgba(245,158,11,0.12)",
        destructive: "rgba(239,68,68,0.12)",
    };
    return (_jsx(motion.div, { layout: true, initial: { opacity: 0, y: 10 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -8 }, transition: { duration: 0.22, ease: "easeOut" }, style: { display: "flex", justifyContent: "flex-start" }, children: _jsxs("div", { style: {
                maxWidth: "90%",
                width: "fit-content",
                background: colors.panel2,
                color: colors.text,
                border: `1px solid ${riskColor}40`,
                borderRadius: 14,
                padding: "14px 16px",
                boxShadow: `0 0 20px ${riskColor}15`,
            }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }, children: [_jsx("div", { style: {
                                fontSize: 10.5,
                                fontWeight: 700,
                                letterSpacing: 0.5,
                                textTransform: "uppercase",
                                padding: "4px 10px",
                                borderRadius: 999,
                                color: riskColor,
                                background: riskBgColors[riskLevel] || riskBgColors.safe,
                                border: `1px solid ${riskColor}50`,
                            }, children: riskLabels[riskLevel] || "Safe" }), _jsx("div", { style: { fontSize: 13, fontWeight: 650 }, children: "Confirm Action" })] }), _jsx("div", { style: {
                        fontSize: 12,
                        fontFamily: "ui-monospace, monospace",
                        color: colors.accent,
                        marginBottom: 8,
                        padding: "6px 10px",
                        background: "rgba(0,0,0,0.2)",
                        borderRadius: 6,
                    }, children: toolName }), _jsx("div", { style: { fontSize: 12.5, lineHeight: 1.6, color: colors.textDim, marginBottom: 10 }, children: Object.entries(kwargs).map(([key, value]) => (_jsxs("div", { style: { marginBottom: 4 }, children: [_jsxs("span", { style: { color: colors.text, fontWeight: 500 }, children: [key, ":"] }), " ", _jsx("span", { style: { wordBreak: "break-word" }, children: typeof value === "string" && value.length > 100
                                    ? value.slice(0, 100) + "…"
                                    : String(value) })] }, key))) }), policyFlags.length > 0 && (_jsxs("div", { style: { fontSize: 10, color: colors.textDim, marginBottom: 10 }, children: ["Requires: ", policyFlags.join(", ")] })), _jsx("div", { style: {
                        display: "flex",
                        flexWrap: "wrap",
                        gap: 6,
                        marginBottom: 12,
                        fontSize: 10,
                    }, children: Object.entries(sessionPermissions).map(([key, enabled]) => (_jsxs("span", { style: {
                            padding: "2px 6px",
                            borderRadius: 4,
                            background: enabled ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)",
                            color: enabled ? "#22c55e" : "#ef4444",
                        }, children: [enabled ? "✓" : "✗", " ", key] }, key))) }), _jsxs("div", { style: { display: "flex", gap: 8, flexWrap: "wrap" }, children: [_jsx("button", { onClick: onConfirm, style: {
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
                            }, children: "Confirm" }), dryRunAvailable && onDryRun && (_jsx("button", { onClick: onDryRun, style: {
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
                            }, children: "Dry Run" })), _jsx("button", { onClick: onCancel, style: {
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
                            }, children: "Cancel" })] })] }) }));
};
export const Dashboard = () => {
    const { messages, addMessage, streaming, setStreaming, listening, setListening, speaking, speechBeat, speechEnabled, setSpeechEnabled, selectedVoice, setSelectedVoice, } = useAppStore();
    const unlockSpeech = () => {
        try {
            stopTts();
            if (typeof window === "undefined" || !("speechSynthesis" in window))
                return;
            setSpeechEnabled(true);
            const u = new SpeechSynthesisUtterance(" ");
            u.volume = 0;
            u.onend = () => {
                try {
                    window.speechSynthesis.cancel();
                }
                catch { }
            };
            try {
                window.speechSynthesis.resume();
            }
            catch { }
            window.speechSynthesis.speak(u);
            window.setTimeout(() => {
                try {
                    window.speechSynthesis.cancel();
                }
                catch { }
            }, 120);
        }
        catch {
            // ignore
        }
    };
    const [voices, setVoices] = useState([]);
    const silenceTimeoutRef = useRef(null);
    useEffect(() => {
        const updateVoices = () => {
            setVoices(window.speechSynthesis.getVoices());
        };
        updateVoices();
        window.speechSynthesis.onvoiceschanged = updateVoices;
        // Hotkey and Transcript Auto-Send Logic
        const handleKeyDown = (e) => {
            if (e.ctrlKey && e.key.toLowerCase() === "m") {
                e.preventDefault();
                const listeningState = useAppStore.getState().listening;
                if (listeningState) {
                    stop();
                    useAppStore.getState().setListening(false);
                    useAppStore.getState().setStreaming(false);
                }
                else {
                    start();
                }
            }
        };
        const handleTranscript = (e) => {
            const customEvent = e;
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
            if (silenceTimeoutRef.current)
                window.clearTimeout(silenceTimeoutRef.current);
        };
    }, []);
    const [input, setInput] = useState("");
    const [workspaceMode, setWorkspaceMode] = useState(() => {
        try {
            const raw = localStorage.getItem("echospeak_workspace_mode") || "auto";
            return (workspaceModes.includes(raw) ? raw : "auto");
        }
        catch {
            return "auto";
        }
    });
    const [activities, setActivities] = useState([]);
    const [taskPlan, setTaskPlan] = useState(createEmptyTaskPlan());
    const [echoReaction, setEchoReaction] = useState(null);
    const [userIsTyping, setUserIsTyping] = useState(false);
    const userTypingTimerRef = useRef(0);
    const research = useResearchStore((state) => state.runs);
    const prependResearchRun = useResearchStore((state) => state.prependRun);
    const replaceResearchRuns = useResearchStore((state) => state.replaceRuns);
    const clearResearchRuns = useResearchStore((state) => state.clearRuns);
    const [leftTab, setLeftTab] = useState("chat");
    const [activeGroup, setActiveGroup] = useState(null);
    const activeGroupButtonRef = useRef(null);
    const activeGroupMenuRef = useRef(null);
    const [activeGroupPos, setActiveGroupPos] = useState(null);
    const [showVisualizer, setShowVisualizer] = useState(true);
    const [agentMode, setAgentMode] = useState("idle");
    const [visualizerPin, setVisualizerPin] = useState(null);
    const [codeSessions, setCodeSessions] = useState([]);
    const [activeCodeTab, setActiveCodeTab] = useState(0);
    const [memoryItems, setMemoryItems] = useState([]);
    const [memoryCount, setMemoryCount] = useState(0);
    const [memoryLoading, setMemoryLoading] = useState(false);
    const [docItems, setDocItems] = useState([]);
    const [servicesHeartbeatStatus, setServicesHeartbeatStatus] = useState(null);
    const [servicesHeartbeatHistory, setServicesHeartbeatHistory] = useState([]);
    const [servicesTelegramStatus, setServicesTelegramStatus] = useState(null);
    const [servicesDiscordStatus, setServicesDiscordStatus] = useState(null);
    const [servicesLoading, setServicesLoading] = useState(false);
    const [docCount, setDocCount] = useState(0);
    const [docLoading, setDocLoading] = useState(false);
    const [docEnabled, setDocEnabled] = useState(false);
    const [docError, setDocError] = useState(null);
    const [docSources, setDocSources] = useState([]);
    const [docFile, setDocFile] = useState(null);
    const [docUploading, setDocUploading] = useState(false);
    const [monitoring, setMonitoring] = useState(false);
    const [monitorText, setMonitorText] = useState("");
    const [monitorAt, setMonitorAt] = useState(0);
    const [monitorError, setMonitorError] = useState(null);
    const toolInfoRef = useRef({});
    const latestCodeFilenameRef = useRef(null);
    const [capabilitiesData, setCapabilitiesData] = useState(null);
    const [memoryFilterType, setMemoryFilterType] = useState("");
    const [selectedMemoryIds, setSelectedMemoryIds] = useState([]);
    const [editingMemoryId, setEditingMemoryId] = useState(null);
    const [editingMemoryText, setEditingMemoryText] = useState("");
    const [projects, setProjects] = useState([]);
    const [activeProjectId, setActiveProjectId] = useState("");
    const [projectsLoading, setProjectsLoading] = useState(false);
    const [threadState, setThreadState] = useState(null);
    const [pendingApproval, setPendingApproval] = useState(null);
    const [approvals, setApprovals] = useState([]);
    const [approvalsLoading, setApprovalsLoading] = useState(false);
    const [executions, setExecutions] = useState([]);
    const activeCodeSession = useMemo(() => {
        const base = codeSessions[activeCodeTab];
        if (!base)
            return null;
        const pendingPath = String(pendingApproval?.action?.kwargs?.path || "");
        const isPendingSave = Boolean(pendingApproval?.has_pending
            && pendingApproval?.action?.tool === "file_write"
            && pendingPath === base.filename);
        return {
            ...base,
            pendingConfirmation: isPendingSave,
        };
    }, [activeCodeTab, codeSessions, pendingApproval]);
    const [executionsLoading, setExecutionsLoading] = useState(false);
    const [selectedTrace, setSelectedTrace] = useState(null);
    const [selectedTraceId, setSelectedTraceId] = useState("");
    const [traceLoading, setTraceLoading] = useState(false);
    const [latestExecutionId, setLatestExecutionId] = useState("");
    const [latestTraceId, setLatestTraceId] = useState("");
    const [routines, setRoutines] = useState([]);
    const [routinesLoading, setRoutinesLoading] = useState(false);
    const [threads, setThreads] = useState([]);
    const [activeThreadId, setActiveThreadId] = useState("");
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
        }
        catch (e) {
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
    const loadHistory = async (threadId) => {
        try {
            const tid = encodeURIComponent(String(threadId || "").trim());
            const resp = await fetchWithTimeout(`${apiBase}/history?thread_id=${tid}`, undefined, 8000);
            if (resp.ok) {
                const data = await resp.json();
                if (data && data.history && Array.isArray(data.history)) {
                    // Parse the string representations back into structured UI messages if possible
                    // The backend returns a list of strings for /history. We will map them to basic chat bubbles.
                    const loadedMsgs = data.history.map((h, i) => {
                        const isUser = h.startsWith("Human:");
                        const text = h.replace(/^(Human:|Assistant:)\s*/, "").trim();
                        return {
                            id: `hist-${Date.now()}-${i}`,
                            role: isUser ? "user" : "assistant",
                            text: text,
                            at: Date.now() - (data.history.length - i) * 1000
                        };
                    }).filter((m) => m.text);
                    if (loadedMsgs.length > 0) {
                        useAppStore.setState({ messages: loadedMsgs });
                    }
                }
            }
        }
        catch (e) {
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
            if (!resp.ok)
                throw new Error(`Threads failed (${resp.status})`);
            const data = await resp.json();
            const items = Array.isArray(data) ? data : [];
            const mapped = items.map((item) => ({
                id: String(item.thread_id || item.id || ""),
                name: String(item.title || item.name || "Session"),
                at: normalizeTimestampMs(item.last_active_at || item.created_at || Date.now()),
            })).filter((item) => item.id);
            if (mapped.length) {
                setThreads(mapped);
                setActiveThreadId((current) => current || mapped[0].id);
            }
        }
        catch (e) {
            console.error("Failed to refresh threads:", e);
        }
    };
    const refreshThreadState = async (threadId = activeThreadId) => {
        if (!threadId)
            return null;
        try {
            const resp = await fetchWithTimeout(`${apiBase}/threads/${encodeURIComponent(threadId)}/state`, undefined, 5000);
            if (!resp.ok)
                throw new Error(`Thread state failed (${resp.status})`);
            const data = (await resp.json());
            setThreadState(data);
            setActiveProjectId(String(data.active_project_id || ""));
            setLatestExecutionId(String(data.last_execution_id || ""));
            setLatestTraceId(String(data.last_trace_id || ""));
            return data;
        }
        catch (e) {
            console.error("Failed to refresh thread state:", e);
            return null;
        }
    };
    const refreshPendingApproval = async (threadId = activeThreadId) => {
        if (!threadId)
            return null;
        try {
            const resp = await fetchWithTimeout(`${apiBase}/pending-action?thread_id=${encodeURIComponent(threadId)}`, undefined, 5000);
            if (!resp.ok)
                throw new Error(`Pending action failed (${resp.status})`);
            const data = (await resp.json());
            setPendingApproval(data);
            return data;
        }
        catch (e) {
            console.error("Failed to refresh pending approval:", e);
            return null;
        }
    };
    const refreshApprovals = async (threadId = activeThreadId) => {
        if (!threadId)
            return;
        setApprovalsLoading(true);
        try {
            const resp = await fetchWithTimeout(`${apiBase}/approvals?thread_id=${encodeURIComponent(threadId)}&limit=25`, undefined, 6000);
            if (!resp.ok)
                throw new Error(`Approvals failed (${resp.status})`);
            const data = (await resp.json());
            setApprovals(Array.isArray(data.items) ? data.items : []);
        }
        catch (e) {
            console.error("Failed to refresh approvals:", e);
        }
        finally {
            setApprovalsLoading(false);
        }
    };
    const refreshExecutions = async (threadId = activeThreadId) => {
        if (!threadId)
            return;
        setExecutionsLoading(true);
        try {
            const resp = await fetchWithTimeout(`${apiBase}/executions?thread_id=${encodeURIComponent(threadId)}&limit=25`, undefined, 6000);
            if (!resp.ok)
                throw new Error(`Executions failed (${resp.status})`);
            const data = (await resp.json());
            setExecutions(Array.isArray(data.items) ? data.items : []);
        }
        catch (e) {
            console.error("Failed to refresh executions:", e);
        }
        finally {
            setExecutionsLoading(false);
        }
    };
    const loadTrace = async (traceId) => {
        if (!traceId)
            return;
        setTraceLoading(true);
        setSelectedTraceId(traceId);
        try {
            const resp = await fetchWithTimeout(`${apiBase}/traces/${encodeURIComponent(traceId)}`, undefined, 7000);
            if (!resp.ok)
                throw new Error(`Trace failed (${resp.status})`);
            const data = await resp.json();
            setSelectedTrace(data && typeof data === "object" ? data : null);
        }
        catch (e) {
            console.error("Failed to load trace:", e);
            setSelectedTrace(null);
        }
        finally {
            setTraceLoading(false);
        }
    };
    const refreshProjects = async () => {
        setProjectsLoading(true);
        try {
            const res = await fetch(`${apiBase}/projects`);
            const data = await res.json();
            setProjects(data.items || []);
        }
        catch (e) {
            console.error("Failed to load projects:", e);
        }
        finally {
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
            if (!resp.ok)
                throw new Error(`Create thread failed (${resp.status})`);
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
        }
        catch (e) {
            console.error("Failed to create thread:", e);
        }
    };
    const switchThread = (id) => {
        if (id === activeThreadId)
            return;
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
    const deleteThread = async (id, e) => {
        e.stopPropagation();
        if (threads.length <= 1)
            return;
        try {
            await fetch(`${apiBase}/threads/${encodeURIComponent(id)}`, { method: "DELETE" });
        }
        catch (e2) {
            console.error("Failed to delete thread:", e2);
        }
        const nextThreads = threads.filter((t) => t.id !== id);
        setThreads(nextThreads);
        if (id === activeThreadId && nextThreads[0]) {
            switchThread(nextThreads[0].id);
        }
    };
    const docInputRef = useRef(null);
    const apiBase = useMemo(() => "http://localhost:8000", []);
    const bootedRef = useRef(false);
    const backendRetryRef = useRef({ attempt: 0, timer: null });
    const [providerInfo, setProviderInfo] = useState(null);
    const [providerModels, setProviderModels] = useState([]);
    const [providerDraft, setProviderDraft] = useState({
        provider: "",
        model: "",
        base_url: "",
    });
    const lmStudioOnly = useMemo(() => isLmStudioOnlyLocked(providerInfo), [providerInfo]);
    const [providerError, setProviderError] = useState(null);
    const [switchingProvider, setSwitchingProvider] = useState(false);
    const [modelsLoading, setModelsLoading] = useState(false);
    const [backendOnline, setBackendOnline] = useState(null);
    const [showSessions, setShowSessions] = useState(false);
    const gatewaySocketRef = useRef(null);
    const gatewayRetryTimerRef = useRef(null);
    const gatewayRetryAttemptRef = useRef(0);
    const [discordGatewayConnected, setDiscordGatewayConnected] = useState(false);
    const [discordGatewaySessionId, setDiscordGatewaySessionId] = useState("");
    const [discordLiveEvents, setDiscordLiveEvents] = useState([]);
    const [spotifyPlaying, setSpotifyPlaying] = useState(null);
    const [runtimeSettings, setRuntimeSettings] = useState(null);
    const [runtimeOverrides, setRuntimeOverrides] = useState(null);
    const [settingsDraft, setSettingsDraft] = useState({});
    const [settingsLoading, setSettingsLoading] = useState(false);
    const [settingsSaving, setSettingsSaving] = useState(false);
    const [settingsError, setSettingsError] = useState(null);
    const [settingsIssues, setSettingsIssues] = useState([]);
    const [settingsSavedAt, setSettingsSavedAt] = useState(null);
    const [settingsTests, setSettingsTests] = useState({});
    const [settingsTesting, setSettingsTesting] = useState({});
    const [settingsTestedKeys, setSettingsTestedKeys] = useState({});
    // Soul state
    const [soulContent, setSoulContent] = useState("");
    const [soulEnabled, setSoulEnabled] = useState(true);
    const [soulPath, setSoulPath] = useState("./SOUL.md");
    const [soulMaxChars, setSoulMaxChars] = useState(8000);
    const [soulExists, setSoulExists] = useState(false);
    const [soulLoading, setSoulLoading] = useState(false);
    const [soulSaving, setSoulSaving] = useState(false);
    const [soulError, setSoulError] = useState(null);
    const [soulSavedAt, setSoulSavedAt] = useState(null);
    const chatScrollRef = useRef(null);
    const chatBottomRef = useRef(null);
    const stickToBottomRef = useRef(true);
    const lastAppliedProviderRef = useRef(null);
    const suppressAutoApplyRef = useRef(true);
    const scheduleBackendRetry = () => {
        if (backendRetryRef.current.timer != null)
            return;
        const attempt = backendRetryRef.current.attempt;
        const delay = Math.min(6000, Math.round(600 * Math.pow(1.6, attempt)));
        backendRetryRef.current.attempt = Math.min(attempt + 1, 12);
        backendRetryRef.current.timer = window.setTimeout(() => {
            backendRetryRef.current.timer = null;
            refreshProviderInfo({ allowRetry: true });
        }, delay);
    };
    const refreshProviderInfo = async (opts = {}) => {
        try {
            setProviderError(null);
            const resp = await fetchWithTimeout(`${apiBase}/provider`, undefined, 10000);
            if (!resp.ok)
                throw new Error(`${resp.status} ${resp.statusText}`);
            const info = (await resp.json());
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
        }
        catch (e) {
            setBackendOnline(false);
            const err = e instanceof Error ? e : new Error(String(e));
            const msg = err.message || String(e);
            const aborted = err.name === "AbortError" || msg.toLowerCase().includes("aborted");
            const offline = aborted || msg.includes("Failed to fetch");
            const pretty = offline ? "Backend offline" : msg;
            setProviderError(offline && opts.allowRetry ? "Backend offline — retrying" : pretty);
            if (opts.allowRetry)
                scheduleBackendRetry();
        }
    };
    const refreshSettings = async () => {
        setSettingsLoading(true);
        setSettingsError(null);
        try {
            const resp = await fetchWithTimeout(`${apiBase}/settings`, undefined, 10000);
            if (!resp.ok)
                throw new Error(`${resp.status} ${resp.statusText}`);
            const data = (await resp.json());
            const effective = (data && typeof data === "object" ? data.settings : null) || null;
            const overrides = (data && typeof data === "object" ? data.overrides : null) || null;
            const issues = Array.isArray(data?.issues) ? data.issues : [];
            setRuntimeSettings(effective);
            setRuntimeOverrides(overrides);
            setSettingsDraft({ ...(effective || {}), ...(overrides || {}) });
            setSettingsIssues(issues);
        }
        catch (e) {
            const err = e instanceof Error ? e : new Error(String(e));
            setSettingsError(err.message || String(e));
            setSettingsIssues([]);
        }
        finally {
            setSettingsLoading(false);
        }
    };
    const refreshSoul = async () => {
        setSoulLoading(true);
        setSoulError(null);
        try {
            const resp = await fetchWithTimeout(`${apiBase}/soul`);
            if (!resp.ok)
                throw new Error(`${resp.status} ${resp.statusText}`);
            const data = await resp.json();
            setSoulEnabled(data.enabled);
            setSoulPath(data.path);
            setSoulContent(data.content);
            setSoulMaxChars(data.max_chars);
            setSoulExists(data.exists);
        }
        catch (e) {
            const err = e instanceof Error ? e : new Error(String(e));
            setSoulError(err.message || String(e));
        }
        finally {
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
                let details = null;
                try {
                    details = await resp.json();
                }
                catch {
                    details = await resp.text().catch(() => "");
                }
                throw new Error(`Save failed (${resp.status}): ${typeof details === "string" ? details : details?.detail || resp.statusText}`);
            }
            const data = await resp.json();
            setSoulEnabled(data.enabled);
            setSoulPath(data.path);
            setSoulContent(data.content);
            setSoulMaxChars(data.max_chars);
            setSoulExists(data.exists);
            setSoulSavedAt(Date.now());
            addMessage({ id: crypto.randomUUID(), role: "assistant", text: "Soul updated. Changes will apply to new conversations.", at: Date.now() });
        }
        catch (e) {
            const err = e instanceof Error ? e : new Error(String(e));
            setSoulError(err.message || String(e));
        }
        finally {
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
                let details = null;
                try {
                    details = await resp.json();
                }
                catch {
                    details = await resp.text().catch(() => "");
                }
                if (resp.status === 422 && details && typeof details === "object") {
                    const issues = details?.detail?.issues;
                    if (Array.isArray(issues))
                        setSettingsIssues(issues);
                    throw new Error(details?.detail?.message || "Invalid settings");
                }
                throw new Error(`Save failed (${resp.status}): ${typeof details === "string" ? details : resp.statusText}`);
            }
            const data = (await resp.json());
            setRuntimeSettings(data.settings || null);
            setRuntimeOverrides(data.overrides || null);
            setSettingsDraft({ ...(data.settings || {}), ...(data.overrides || {}) });
            setSettingsIssues(Array.isArray(data?.issues) ? data.issues : []);
            setSettingsSavedAt(Date.now());
            await refreshProviderInfo();
            await refreshServices();
            addMessage({ id: crypto.randomUUID(), role: "assistant", text: "Settings saved.", at: Date.now() });
        }
        catch (e) {
            const err = e instanceof Error ? e : new Error(String(e));
            setSettingsError(err.message || String(e));
        }
        finally {
            setSettingsSaving(false);
        }
    };
    const settingsErrors = useMemo(() => settingsIssues.filter((i) => i.severity === "error"), [settingsIssues]);
    const settingsWarnings = useMemo(() => settingsIssues.filter((i) => i.severity === "warning"), [settingsIssues]);
    const issueByKey = useMemo(() => {
        const map = {};
        for (const it of settingsIssues) {
            if (!it || typeof it.key !== "string")
                continue;
            if (map[it.key])
                continue;
            map[it.key] = { message: it.message, severity: it.severity };
        }
        return map;
    }, [settingsIssues]);
    const getIssue = (key) => issueByKey[key];
    const isError = (key) => getIssue(key)?.severity === "error";
    const RequiredBadge = ({ issueKey }) => {
        const it = getIssue(issueKey);
        if (!it)
            return null;
        const color = it.severity === "error" ? colors.danger : "#f59e0b";
        return (_jsx("span", { title: it.message, style: {
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
            }, children: it.severity === "error" ? "Required" : "Check" }));
    };
    const runSettingsTest = async (target) => {
        setSettingsTesting((m) => ({ ...m, [target]: true }));
        try {
            const payload = { target };
            if (target === "openai") {
                payload.api_key = String(settingsDraft?.openai?.api_key || "") === "***" ? "" : String(settingsDraft?.openai?.api_key || "");
                setSettingsTestedKeys((m) => ({ ...m, openai: payload.api_key }));
            }
            else if (target === "gemini") {
                payload.api_key = String(settingsDraft?.gemini?.api_key || "") === "***" ? "" : String(settingsDraft?.gemini?.api_key || "");
                setSettingsTestedKeys((m) => ({ ...m, gemini: payload.api_key }));
            }
            else if (target === "tavily") {
                payload.api_key = String(settingsDraft?.tavily_api_key || "") === "***" ? "" : String(settingsDraft?.tavily_api_key || "");
                setSettingsTestedKeys((m) => ({ ...m, tavily: payload.api_key }));
            }
            else {
                payload.provider = String(settingsDraft?.local?.provider || providerDraft.provider || "");
                payload.base_url = String(settingsDraft?.local?.base_url || providerDraft.base_url || "");
                payload.model = String(settingsDraft?.local?.model_name || providerDraft.model || "");
            }
            const resp = await fetchWithTimeout(`${apiBase}/settings/test`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            }, 10000);
            const data = (await resp.json().catch(() => null));
            if (!resp.ok) {
                const msg = data?.message || `${resp.status} ${resp.statusText}`;
                setSettingsTests((m) => ({ ...m, [target]: { ok: false, target, message: String(msg) } }));
                return;
            }
            setSettingsTests((m) => ({ ...m, [target]: data }));
        }
        catch (e) {
            setSettingsTests((m) => ({ ...m, [target]: { ok: false, target, message: String(e) } }));
        }
        finally {
            setSettingsTesting((m) => ({ ...m, [target]: false }));
        }
    };
    useEffect(() => {
        if (leftTab === "settings") {
            refreshSettings();
        }
    }, [leftTab]);
    const updateDraft = (key, value) => {
        setSettingsDraft((d) => ({ ...d, [key]: value }));
    };
    const updateDraftSection = (section, key, value) => {
        setSettingsDraft((d) => ({
            ...d,
            [section]: { ...(d[section] || {}), [key]: value },
        }));
    };
    const refreshMemory = async () => {
        setMemoryLoading(true);
        try {
            const tid = encodeURIComponent(String(activeThreadId || "").trim());
            const threadQs = tid ? `&thread_id=${tid}` : "";
            const resp = await fetchWithTimeout(`${apiBase}/memory?offset=0&limit=200${threadQs}`);
            if (!resp.ok)
                throw new Error(`Memory request failed (${resp.status})`);
            const data = (await resp.json());
            setMemoryItems(Array.isArray(data.items) ? data.items : []);
            setMemoryCount(typeof data.count === "number" ? data.count : 0);
        }
        catch (e) {
            setMemoryItems([]);
            setMemoryCount(0);
            addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${String(e)}`, at: Date.now() });
        }
        finally {
            setMemoryLoading(false);
        }
    };
    const deleteMemoryItem = async (id) => {
        try {
            const resp = await fetchWithTimeout(`${apiBase}/memory/delete`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ ids: [id], thread_id: activeThreadId }),
            });
            if (!resp.ok)
                throw new Error(`Delete failed (${resp.status})`);
            await refreshMemory();
        }
        catch (e) {
            addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${String(e)}`, at: Date.now() });
        }
    };
    const togglePinMemoryItem = async (item) => {
        try {
            const resp = await fetchWithTimeout(`${apiBase}/memory/update`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ id: item.id, pinned: !Boolean(item.pinned), thread_id: activeThreadId }),
            });
            if (!resp.ok)
                throw new Error(`Update failed (${resp.status})`);
            setMemoryItems((prev) => prev.map((m) => (m.id === item.id ? { ...m, pinned: !Boolean(item.pinned) } : m)));
        }
        catch (e) {
            addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${String(e)}`, at: Date.now() });
        }
    };
    const clearAllMemory = async () => {
        if (!window.confirm("Clear all saved memory?"))
            return;
        try {
            const tid = encodeURIComponent(String(activeThreadId || "").trim());
            const threadQs = tid ? `?thread_id=${tid}` : "";
            const resp = await fetchWithTimeout(`${apiBase}/memory/clear${threadQs}`, { method: "POST" });
            if (!resp.ok)
                throw new Error(`Clear failed (${resp.status})`);
            await refreshMemory();
        }
        catch (e) {
            addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${String(e)}`, at: Date.now() });
        }
    };
    const refreshDocuments = async () => {
        setDocLoading(true);
        setDocError(null);
        try {
            const resp = await fetchWithTimeout(`${apiBase}/documents`);
            if (!resp.ok)
                throw new Error(`Documents request failed (${resp.status})`);
            const data = (await resp.json());
            setDocEnabled(Boolean(data.enabled));
            setDocItems(Array.isArray(data.items) ? data.items : []);
            setDocCount(typeof data.count === "number" ? data.count : 0);
        }
        catch (e) {
            setDocEnabled(false);
            setDocItems([]);
            setDocCount(0);
            setDocError(String(e));
        }
        finally {
            setDocLoading(false);
        }
    };
    const uploadDocument = async () => {
        if (!docFile)
            return;
        setDocUploading(true);
        setDocError(null);
        try {
            const form = new FormData();
            form.append("file", docFile);
            const resp = await fetchWithTimeout(`${apiBase}/documents/upload`, { method: "POST", body: form }, 12000);
            if (!resp.ok)
                throw new Error(await resp.text());
            setDocFile(null);
            if (docInputRef.current)
                docInputRef.current.value = "";
            await refreshDocuments();
        }
        catch (e) {
            setDocError(String(e));
        }
        finally {
            setDocUploading(false);
        }
    };
    const deleteDocument = async (id) => {
        try {
            const resp = await fetchWithTimeout(`${apiBase}/documents/delete`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ ids: [id] }),
            });
            if (!resp.ok)
                throw new Error(`Delete failed (${resp.status})`);
            await refreshDocuments();
        }
        catch (e) {
            setDocError(String(e));
        }
    };
    const clearAllDocuments = async () => {
        if (!window.confirm("Clear all uploaded documents?"))
            return;
        try {
            const resp = await fetchWithTimeout(`${apiBase}/documents/clear`, { method: "POST" });
            if (!resp.ok)
                throw new Error(`Clear failed (${resp.status})`);
            await refreshDocuments();
        }
        catch (e) {
            setDocError(String(e));
        }
    };
    const refreshProviderModels = async (provider) => {
        try {
            setModelsLoading(true);
            const resp = await fetchWithTimeout(`${apiBase}/provider/models?provider=${encodeURIComponent(provider)}`);
            if (!resp.ok)
                return;
            const data = (await resp.json());
            setProviderModels(Array.isArray(data.models) ? data.models : []);
        }
        catch {
            setProviderModels([]);
        }
        finally {
            setModelsLoading(false);
        }
    };
    const applyProviderSwitch = async (draft) => {
        if (lmStudioOnly)
            return;
        const next = draft || providerDraft;
        if (!next.provider)
            return;
        setSwitchingProvider(true);
        setProviderError(null);
        try {
            const body = { provider: next.provider };
            if (next.provider === "openai")
                body.openai_model = next.model || undefined;
            else if (next.provider === "gemini")
                body.gemini_model = next.model || undefined;
            else
                body.model = next.model || undefined;
            if (next.base_url)
                body.base_url = next.base_url;
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
        }
        catch (e) {
            setProviderError(e instanceof Error ? e.message : String(e));
        }
        finally {
            setSwitchingProvider(false);
        }
    };
    const timeline = useMemo(() => {
        const merged = [
            ...messages.map((m) => ({
                kind: "message",
                id: m.id,
                at: m.at,
                msg: m,
            })),
            ...activities.map((a) => ({
                kind: "activity",
                id: a.id,
                at: a.at,
                item: a,
            })),
        ];
        merged.sort((a, b) => a.at - b.at);
        return merged;
    }, [messages, activities]);
    const lastMsgLen = messages.length ? (messages[messages.length - 1]?.text || "").length : 0;
    const activityLen = activities.length;
    const scrollChatToBottom = (behavior = "smooth", force = false) => {
        try {
            if (!force && !stickToBottomRef.current)
                return;
            const el = chatScrollRef.current;
            if (el)
                el.scrollTo({ top: el.scrollHeight, behavior });
        }
        catch {
            // ignore
        }
    };
    const onChatScroll = () => {
        const el = chatScrollRef.current;
        if (!el)
            return;
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
        if (leftTab !== "chat")
            return;
        const timerId = setTimeout(() => {
            scrollChatToBottom(streaming ? "auto" : "smooth");
        }, 50);
        return () => clearTimeout(timerId);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [timeline.length, lastMsgLen, activityLen, streaming]);
    const sendText = async (overrideText) => {
        const raw = overrideText ?? input;
        if (!raw.trim())
            return;
        stickToBottomRef.current = true; // force sticky to bottom when sending a message
        if (!overrideText)
            setInput("");
        const clampContext = (t, n) => {
            const s = (t || "").replace(/\s+/g, " ").trim();
            if (s.length <= n)
                return s;
            return s.slice(0, n).trimEnd() + "…";
        };
        const shouldAttachMonitor = (q) => {
            const low = (q || "").toLowerCase();
            if (!monitoring)
                return false;
            if (!monitorText || !monitorText.trim())
                return false;
            if (low.includes("on my screen") || low.includes("on my desktop") || low.includes("what am i looking") || low.includes("what do you see"))
                return true;
            if (low.includes("watching") || low.includes("seeing") || low.includes("look at") || low.includes("this") || low.includes("that") || low.includes("here"))
                return true;
            return false;
        };
        const desktopContext = shouldAttachMonitor(raw) ? clampContext(monitorText, 1200) : "";
        const requestText = desktopContext ? `${raw}\n\nLive desktop context:\n${desktopContext}` : raw;
        const userMsg = { id: crypto.randomUUID(), role: "user", text: raw, at: Date.now() };
        addMessage(userMsg);
        setInput("");
        setUserIsTyping(false);
        if (userTypingTimerRef.current)
            clearTimeout(userTypingTimerRef.current);
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
            const upsertTool = (evt) => {
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
                        setActivities((prev) => prev.map((p) => (p.kind === "tool" && p.id === evt.id ? { ...p, status: "done", output: summary } : p)));
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
                                let nextSession;
                                if (info?.name === "file_read") {
                                    nextSession = {
                                        filename,
                                        language: lang,
                                        originalContent: content,
                                        currentContent: content,
                                        status: "read",
                                        summary: `Loaded ${content.length} chars`,
                                    };
                                }
                                else if (info?.name === "file_write") {
                                    if (isFileWriteSummary(content)) {
                                        nextSession = {
                                            filename,
                                            language: lang,
                                            originalContent: existing?.originalContent || existing?.currentContent || "",
                                            currentContent: existing?.currentContent || "",
                                            status: "saved",
                                            summary: content,
                                        };
                                    }
                                    else {
                                        nextSession = {
                                            filename,
                                            language: lang,
                                            originalContent: existing?.originalContent || existing?.currentContent || "",
                                            currentContent: content,
                                            status: "draft",
                                            summary: `Preview ${content.length} chars`,
                                        };
                                    }
                                }
                                else {
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
                    setActivities((prev) => prev.map((p) => (p.kind === "tool" && p.id === evt.id ? { ...p, status: "done", output: activityOutput } : p)));
                    return;
                }
                if (evt.type === "tool_error") {
                    setActivities((prev) => prev.map((p) => (p.kind === "tool" && p.id === evt.id ? { ...p, status: "error", output: evt.error } : p)));
                    setEchoReaction("error");
                }
            };
            while (true) {
                const { done, value } = await reader.read();
                if (done)
                    break;
                buffer += decoder.decode(value, { stream: true });
                let idx = buffer.indexOf("\n");
                while (idx !== -1) {
                    const line = buffer.slice(0, idx).trim();
                    buffer = buffer.slice(idx + 1);
                    idx = buffer.indexOf("\n");
                    if (!line)
                        continue;
                    let evt;
                    try {
                        evt = JSON.parse(line);
                    }
                    catch (e) {
                        continue;
                    }
                    if (evt.type === "task_plan" || evt.type === "task_step" || evt.type === "task_reflection") {
                        setTaskPlan((prev) => taskPlanReducer(prev, evt));
                    }
                    else if (evt.type === "tool_start" || evt.type === "tool_end" || evt.type === "tool_error") {
                        upsertTool(evt);
                    }
                    else if (evt.type === "thinking") {
                        const content = (evt.content || "").trim();
                        if (content) {
                            setActivities((prev) => {
                                if (prev.some((p) => p.kind === "thinking" && p.content === content))
                                    return prev;
                                return [...prev, { kind: "thinking", id: crypto.randomUUID(), content, at: Date.now() }];
                            });
                        }
                    }
                    else if (evt.type === "memory_saved") {
                        setActivities((prev) => [
                            ...prev,
                            { kind: "memory", id: crypto.randomUUID(), memoryCount: evt.memory_count, at: Date.now() },
                        ]);
                        setMemoryCount(evt.memory_count);
                        setEchoReaction("memory_saved");
                        if (leftTab === "memory") {
                            refreshMemory();
                        }
                    }
                    else if (evt.type === "status" && evt.agent_mode) {
                        setAgentMode(evt.agent_mode);
                    }
                    else if (evt.type === "error") {
                        setStreaming(false);
                        setActivities((prev) => [
                            ...prev,
                            { kind: "error", id: crypto.randomUUID(), message: evt.message, at: Date.now() },
                        ]);
                        setEchoReaction("error");
                    }
                    else if (evt.type === "final") {
                        const reply = evt.response || "(no response)";
                        const spoken = (evt.spoken_text || "").trim();
                        setDocSources(Array.isArray(evt.doc_sources) ? evt.doc_sources : []);
                        if (evt.thread_state) {
                            setThreadState(evt.thread_state);
                            setActiveProjectId(String(evt.thread_state.active_project_id || ""));
                            setLatestExecutionId(String(evt.thread_state.last_execution_id || evt.execution_id || ""));
                            setLatestTraceId(String(evt.thread_state.last_trace_id || evt.trace_id || ""));
                        }
                        else {
                            if (evt.execution_id)
                                setLatestExecutionId(String(evt.execution_id));
                            if (evt.trace_id)
                                setLatestTraceId(String(evt.trace_id));
                        }
                        if (Array.isArray(evt.research) && evt.research.length) {
                            replaceResearchRuns(evt.research.map((item) => normalizeResearchRun(item)).filter((item) => Boolean(item)));
                        }
                        const botMsg = { id: crypto.randomUUID(), role: "assistant", text: reply, at: Date.now() };
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
        }
        catch (err) {
            const msg = String(err);
            const pretty = msg.includes("Failed to fetch") ? `Backend offline (${apiBase})` : msg;
            setBackendOnline(false);
            addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${pretty}`, at: Date.now() });
            setActivities((prev) => [
                ...prev,
                { kind: "error", id: crypto.randomUUID(), message: pretty, at: Date.now() },
            ]);
            setEchoReaction("error");
        }
        finally {
            setStreaming(false);
        }
    };
    const { start, stop } = useMicStreamer((t) => {
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
            const data = (await resp.json());
            setMonitorText(String(data?.text || ""));
            setMonitorAt(Date.now());
        }
        catch (e) {
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
        }
        catch (e) {
            console.error("Failed to refresh services", e);
        }
        finally {
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
            if (disposed || gatewayRetryTimerRef.current != null)
                return;
            const attempt = gatewayRetryAttemptRef.current + 1;
            gatewayRetryAttemptRef.current = attempt;
            const delay = Math.min(1000 * Math.pow(2, Math.max(0, attempt - 1)), 10000);
            gatewayRetryTimerRef.current = window.setTimeout(() => {
                gatewayRetryTimerRef.current = null;
                connectGateway();
            }, delay);
        };
        const connectGateway = () => {
            if (disposed)
                return;
            try {
                if (gatewaySocketRef.current) {
                    try {
                        gatewaySocketRef.current.close();
                    }
                    catch {
                        // ignore
                    }
                    gatewaySocketRef.current = null;
                }
                const ws = new WebSocket(gatewayUrl);
                gatewaySocketRef.current = ws;
                ws.onopen = () => {
                    if (disposed)
                        return;
                    clearRetryTimer();
                    gatewayRetryAttemptRef.current = 0;
                    setDiscordGatewayConnected(true);
                };
                ws.onmessage = (evt) => {
                    if (disposed)
                        return;
                    let payload = null;
                    try {
                        payload = JSON.parse(String(evt.data || ""));
                    }
                    catch {
                        return;
                    }
                    if (!payload || typeof payload !== "object")
                        return;
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
                            const nextEvent = {
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
                            const nextEvent = {
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
                    if (disposed)
                        return;
                    setDiscordGatewayConnected(false);
                };
                ws.onclose = () => {
                    if (disposed)
                        return;
                    setDiscordGatewayConnected(false);
                    setDiscordGatewaySessionId("");
                    setSpotifyPlaying(null);
                    if (gatewaySocketRef.current === ws) {
                        gatewaySocketRef.current = null;
                    }
                    scheduleReconnect();
                };
            }
            catch (e) {
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
                }
                catch {
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
        if (leftTab === "memory")
            refreshMemory();
        if (leftTab === "docs")
            refreshDocuments();
        if (leftTab === "soul")
            refreshSoul();
        if (leftTab === "services")
            refreshServices();
        if (leftTab === "projects")
            refreshProjects();
        if (leftTab === "approvals") {
            refreshPendingApproval();
            refreshApprovals();
        }
        if (leftTab === "executions") {
            refreshExecutions();
            if (latestTraceId)
                loadTrace(latestTraceId);
        }
    }, [leftTab]);
    useEffect(() => {
        if (backendOnline === false)
            return;
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
        if (lmStudioOnly)
            return;
        if (suppressAutoApplyRef.current)
            return;
        if (switchingProvider)
            return;
        const next = { provider: providerDraft.provider, model: providerDraft.model, base_url: providerDraft.base_url };
        const last = lastAppliedProviderRef.current;
        if (last && last.provider === next.provider && last.model === (next.model || ""))
            return;
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
            if (!btn)
                return;
            const r = btn.getBoundingClientRect();
            setActiveGroupPos({
                top: Math.round(r.bottom + 8),
                left: Math.round(r.left),
            });
        };
        computePos();
        const onKeyDown = (e) => {
            if (e.key === "Escape")
                setActiveGroup(null);
        };
        const onPointerDown = (e) => {
            const t = e.target;
            if (!t)
                return;
            const menu = activeGroupMenuRef.current;
            const btn = activeGroupButtonRef.current;
            if (menu && menu.contains(t))
                return;
            if (btn && btn.contains(t))
                return;
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
        if (!monitoring)
            return;
        let cancelled = false;
        let inFlight = false;
        const tick = async () => {
            if (cancelled)
                return;
            if (inFlight) {
                window.setTimeout(tick, 1200);
                return;
            }
            inFlight = true;
            try {
                await refreshMonitor();
            }
            finally {
                inFlight = false;
            }
            window.setTimeout(tick, 2200);
        };
        tick();
        return () => {
            cancelled = true;
        };
    }, [monitoring, apiBase]);
    return (_jsxs("div", { style: {
            minHeight: "100vh",
            background: colors.bg,
            color: colors.text,
        }, children: [_jsx("style", { children: globalCss }), _jsxs("div", { className: "app-shell", style: {
                    gridTemplateColumns: showVisualizer ? "1fr 1fr" : "1fr",
                }, children: [showVisualizer ? (_jsxs("div", { className: "visualizer-pane", style: { display: "flex", flexDirection: "column", position: "relative" }, children: [_jsx("div", { style: {
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
                                }, children: ["ring", "research", "coding"].map((m) => {
                                    const effectiveMode = visualizerPin || (agentMode === "research" ? "research" : agentMode === "coding" ? "coding" : "ring");
                                    const isActive = effectiveMode === m;
                                    const isPinned = visualizerPin === m;
                                    const labels = {
                                        ring: { icon: "🤖", text: "Avatar" },
                                        research: { icon: "🔍", text: "Research" },
                                        coding: { icon: "💻", text: "Code" }
                                    };
                                    return (_jsxs("button", { onClick: () => setVisualizerPin(isPinned ? null : m), style: {
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
                                        }, children: [_jsx("span", { style: { fontSize: "16px", filter: "brightness(0) invert(1)", opacity: isActive ? 1 : 0.7 }, children: labels[m].icon }), _jsx("span", { style: { textShadow: isActive ? "0 0 8px rgba(255,255,255,0.4)" : "none" }, children: labels[m].text })] }, m));
                                }) }), (() => {
                                const effectiveMode = visualizerPin || (agentMode === "research" ? "research" : agentMode === "coding" ? "coding" : "ring");
                                return (_jsx("div", { style: { flex: 1, display: "flex", alignItems: "center", justifyContent: "center", overflow: effectiveMode === "ring" ? "visible" : "hidden" }, children: (() => {
                                        if (effectiveMode === "research") {
                                            return (_jsxs("div", { style: { width: "100%", height: "100%", padding: "0 20px 20px", overflowY: "auto", display: "flex", flexDirection: "column", gap: 12 }, children: [_jsx("div", { style: { fontSize: 13, fontWeight: 700, color: "rgba(255,255,255,0.5)", textTransform: "uppercase", letterSpacing: 1.5, padding: "8px 0" }, children: "\uD83D\uDD0D Research Feed" }), research.length === 0 ? (_jsx("div", { style: { textAlign: "center", color: "rgba(255,255,255,0.25)", fontSize: 13, padding: 40, fontStyle: "italic" }, children: "Research results will appear here when the agent searches the web..." })) : (research.slice(0, 8).map((group, gi) => (_jsxs(motion.div, { initial: { opacity: 0, y: 20 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.35, delay: gi * 0.05 }, style: {
                                                            background: "rgba(255,255,255,0.03)",
                                                            border: "1px solid rgba(255,255,255,0.08)",
                                                            borderRadius: 14,
                                                            padding: 16,
                                                        }, children: [_jsxs("div", { style: { fontSize: 12, fontWeight: 600, color: "rgba(139,92,246,0.9)", marginBottom: 10, display: "flex", alignItems: "center", gap: 8 }, children: [_jsx("span", { style: { fontSize: 14 }, children: "\uD83D\uDD0E" }), _jsxs("span", { style: { fontStyle: "italic" }, children: ["\"", group.query, "\""] })] }), _jsx("div", { style: { display: "flex", flexDirection: "column", gap: 8 }, children: group.evidence.slice(0, 5).map((r, ri) => (_jsxs(motion.div, { initial: { opacity: 0, x: -10 }, animate: { opacity: 1, x: 0 }, transition: { duration: 0.25, delay: ri * 0.08 }, style: {
                                                                        display: "flex",
                                                                        flexDirection: "column",
                                                                        gap: 3,
                                                                        padding: "8px 12px",
                                                                        borderRadius: 10,
                                                                        background: "rgba(255,255,255,0.02)",
                                                                        borderLeft: "3px solid rgba(139,92,246,0.4)",
                                                                    }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, color: "rgba(96,165,250,0.9)" }, children: r.title || r.url }), _jsx("div", { style: { fontSize: 10, color: "rgba(255,255,255,0.3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, children: r.url }), (r.summary || r.snippet) && (_jsxs("div", { style: { fontSize: 11, color: "rgba(255,255,255,0.5)", lineHeight: 1.4 }, children: [(r.summary || r.snippet).slice(0, 150), (r.summary || r.snippet).length > 150 ? "…" : ""] }))] }, r.id || ri))) })] }, group.id))))] }));
                                        }
                                        if (effectiveMode === "coding") {
                                            return (_jsxs("div", { style: { width: "100%", height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }, children: [_jsxs("div", { style: {
                                                            display: "flex",
                                                            gap: 2,
                                                            padding: "8px 12px 0",
                                                            overflowX: "auto",
                                                            scrollbarWidth: "none",
                                                            flexShrink: 0,
                                                        }, children: [_jsx("button", { onClick: () => setActiveCodeTab(-1), style: {
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
                                                                }, children: "\uD83D\uDCC2 Files" }), codeSessions.map((session, i) => (_jsx("button", { onClick: () => setActiveCodeTab(i), style: {
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
                                                                }, children: session.language === "bash" ? "⬛ Terminal" : `📄 ${session.filename.split("/").pop()}` }, session.filename)))] }), (activeCodeTab === -1 || codeSessions.length === 0) ? (_jsx("div", { style: { flex: 1, overflow: "hidden" }, children: _jsx(WorkspaceExplorer, { apiBase: apiBase }) })) : (_jsx("div", { style: {
                                                            flex: 1,
                                                            margin: "0 12px 12px",
                                                            borderRadius: "0 0 12px 12px",
                                                            background: activeCodeSession?.language === "bash" ? "#0d1117" : "#1a1b26",
                                                            border: "1px solid rgba(255,255,255,0.06)",
                                                            overflow: "auto",
                                                            position: "relative",
                                                        }, children: activeCodeSession ? (_jsx(InlineCodeDiff, { session: activeCodeSession, onAccept: activeCodeSession.pendingConfirmation ? () => sendText("confirm") : undefined, onDecline: activeCodeSession.pendingConfirmation ? () => sendText("cancel") : undefined })) : null }))] }));
                                        }
                                        const hasRunningTool = activities.some(a => a.kind === "tool" && a.status === "running");
                                        const isThinking = !listening && !speaking && (streaming || hasRunningTool);
                                        // Find the latest running tool, if any
                                        const latestRunningTool = [...activities].reverse().find(a => a.kind === "tool" && a.status === "running");
                                        const currentToolCategory = latestRunningTool ? getToolCategory(latestRunningTool.name) : "generic";
                                        const activeToolName = latestRunningTool?.name;
                                        const thinkingText = latestRunningTool ? getToolDisplayDetails(latestRunningTool.name, latestRunningTool.input) : "processing...";
                                        return (_jsx(SquareAvatarVisual, { speaking: speaking, backendOnline: backendOnline, isThinking: isThinking, thinkingText: thinkingText, activeToolName: activeToolName, heartbeatEnabled: settingsDraft?.heartbeat_enabled, toolCategory: currentToolCategory, userIsTyping: userIsTyping, pendingConfirmation: pendingApproval?.has_pending || false, reaction: echoReaction, onReactionDone: () => setEchoReaction(null), spotifyPlaying: spotifyPlaying?.is_playing ? spotifyPlaying : null }));
                                    })() }));
                            })()] })) : null, _jsxs("div", { className: "glow-panel", children: [_jsxs("div", { className: "panel-header", children: [_jsxs("div", { className: "title", children: [_jsx("img", { src: "/logo.png", alt: "Logo", style: { width: 14, height: 14, borderRadius: 2 } }), _jsx("span", { children: leftTab === "chat" ? "EchoSpeak" : leftTab === "research" ? "Research" : leftTab === "memory" ? "Memory" : leftTab === "capabilities" ? "Capabilities" : leftTab === "approvals" ? "Approvals" : leftTab === "executions" ? "Executions" : leftTab === "projects" ? "Projects" : leftTab === "routines" ? "Routines" : leftTab === "settings" ? "Settings" : leftTab === "soul" ? "Soul" : "Documents" }), activeProjectId && leftTab === "chat" && (_jsxs("span", { style: { fontSize: 10, padding: "2px 8px", borderRadius: 6, background: "linear-gradient(135deg, rgba(34,197,94,0.15), rgba(34,197,94,0.05))", border: "1px solid rgba(34,197,94,0.25)", color: "#22c55e", fontWeight: 600, marginLeft: 8 }, children: ["\uD83D\uDCC1 ", projects.find(p => p.id === activeProjectId)?.name || "Project Active"] }))] }), _jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center" }, children: [_jsx("div", { className: `switcher-dot ${backendOnline ? "online" : "offline"}`, title: backendOnline ? "Connected" : "Disconnected" }), _jsx("button", { type: "button", className: "icon-button", onClick: () => setSpeechEnabled(!speechEnabled), title: speechEnabled ? "Mute Speech" : "Unmute Speech", style: {
                                                    color: "#fff",
                                                    background: speechEnabled ? "#222" : "transparent",
                                                    border: `1px solid ${colors.line}`,
                                                }, children: speechEnabled ? (_jsxs("svg", { width: "18", height: "18", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", children: [_jsx("polygon", { points: "11 5 6 9 2 9 2 15 6 15 11 19 11 5" }), _jsx("path", { d: "M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07" })] })) : (_jsxs("svg", { width: "18", height: "18", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", children: [_jsx("polygon", { points: "11 5 6 9 2 9 2 15 6 15 11 19 11 5" }), _jsx("line", { x1: "23", y1: "9", x2: "17", y2: "15" }), _jsx("line", { x1: "17", y1: "9", x2: "23", y2: "15" })] })) }), _jsx("button", { type: "button", className: "icon-button", onClick: () => setShowVisualizer((v) => !v), title: showVisualizer ? "Hide visualizer" : "Show visualizer", style: {
                                                    color: "#fff",
                                                    background: showVisualizer ? "#222" : "transparent",
                                                    border: `1px solid ${colors.line}`,
                                                }, children: showVisualizer ? (_jsxs("svg", { width: "18", height: "18", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", children: [_jsx("rect", { x: "3", y: "4", width: "18", height: "16", rx: "2" }), _jsx("path", { d: "M12 4v16" })] })) : (_jsxs("svg", { width: "18", height: "18", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", children: [_jsx("rect", { x: "3", y: "4", width: "18", height: "16", rx: "2" }), _jsx("path", { d: "M12 4v16" }), _jsx("path", { d: "M8 9h2M8 13h2M8 17h2" })] })) })] })] }), _jsx("div", { className: "panel-body", children: _jsxs("div", { className: "research-panel", children: [_jsx("div", { className: "tab-bar", style: {
                                                position: "relative",
                                                overflow: "visible",
                                                marginBottom: "16px",
                                            }, children: _jsx("div", { style: {
                                                    display: "flex",
                                                    gap: 8,
                                                    alignItems: "center",
                                                    padding: "6px 8px",
                                                    background: "linear-gradient(135deg, rgba(255,255,255,0.08) 0%, rgba(255,255,255,0.02) 100%)",
                                                    borderRadius: "16px",
                                                    border: "1px solid rgba(255,255,255,0.1)",
                                                    boxShadow: "inset 0 1px 1px rgba(255,255,255,0.15), 0 4px 12px rgba(0,0,0,0.3)",
                                                    backdropFilter: "blur(20px)",
                                                    WebkitBackdropFilter: "blur(20px)",
                                                    overflowX: "auto",
                                                    overflowY: "hidden",
                                                    whiteSpace: "nowrap",
                                                    scrollbarWidth: "none",
                                                    flexWrap: "nowrap",
                                                }, children: [
                                                    { id: 'core', label: 'Core', icon: '⚡', tabs: [{ id: 'chat', label: 'Chat' }, { id: 'research', label: 'Research' }] },
                                                    { id: 'knowledge', label: 'Knowledge', icon: '📚', tabs: [{ id: 'memory', label: 'Memory' }, { id: 'docs', label: 'Docs' }] },
                                                    { id: 'config', label: 'Config', icon: '⚙️', tabs: [{ id: 'settings', label: 'Settings' }, { id: 'capabilities', label: 'Tools' }] },
                                                    { id: 'automation', label: 'Automation', icon: '🤖', tabs: [{ id: 'approvals', label: 'Approvals' }, { id: 'executions', label: 'Executions' }, { id: 'projects', label: 'Projects' }, { id: 'routines', label: 'Routines' }, { id: 'services', label: 'Services' }] },
                                                    { id: 'identity', label: 'Identity', icon: '👤', tabs: [{ id: 'soul', label: 'Soul' }] },
                                                ].map((group) => {
                                                    const isGroupActive = group.tabs.some(t => t.id === leftTab);
                                                    return (_jsx("div", { style: { position: "relative", display: "flex", alignItems: "center" }, children: _jsxs("button", { type: "button", className: `tab-button ${isGroupActive ? "active" : ""}`, ref: (el) => {
                                                                if (activeGroup === group.id)
                                                                    activeGroupButtonRef.current = el;
                                                            }, onClick: (e) => {
                                                                if (group.tabs.length === 1) {
                                                                    setLeftTab(group.tabs[0].id);
                                                                    setActiveGroup(null);
                                                                }
                                                                else {
                                                                    activeGroupButtonRef.current = e.currentTarget;
                                                                    setActiveGroup(activeGroup === group.id ? null : group.id);
                                                                }
                                                            }, style: {
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
                                                            }, children: [_jsx("span", { style: { fontSize: "16px", filter: "brightness(0) invert(1)", opacity: isGroupActive ? 1 : 0.7 }, children: group.icon }), _jsx("span", { style: { textShadow: isGroupActive ? "0 0 8px rgba(255,255,255,0.4)" : "none" }, children: group.label }), group.tabs.length > 1 && (_jsx("span", { style: { fontSize: "10px", opacity: 0.5, marginLeft: 4 }, children: activeGroup === group.id ? '▲' : '▼' }))] }) }, group.id));
                                                }) }) }), activeGroup && activeGroupPos
                                            ? createPortal(_jsx(AnimatePresence, { children: _jsx(motion.div, { ref: (el) => {
                                                        activeGroupMenuRef.current = el;
                                                    }, initial: { opacity: 0, y: 8, scale: 0.95 }, animate: { opacity: 1, y: 0, scale: 1 }, exit: { opacity: 0, y: 4, scale: 0.95 }, transition: { duration: 0.15 }, style: {
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
                                                        boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.5)",
                                                        minWidth: "140px",
                                                    }, children: ([
                                                        { id: 'core', label: 'Core', icon: '⚡', tabs: [{ id: 'chat', label: 'Chat' }, { id: 'research', label: 'Research' }] },
                                                        { id: 'knowledge', label: 'Knowledge', icon: '📚', tabs: [{ id: 'memory', label: 'Memory' }, { id: 'docs', label: 'Docs' }] },
                                                        { id: 'config', label: 'Config', icon: '⚙️', tabs: [{ id: 'settings', label: 'Settings' }, { id: 'capabilities', label: 'Tools' }] },
                                                        { id: 'automation', label: 'Automation', icon: '🤖', tabs: [{ id: 'approvals', label: 'Approvals' }, { id: 'executions', label: 'Executions' }, { id: 'projects', label: 'Projects' }, { id: 'routines', label: 'Routines' }, { id: 'services', label: 'Services' }] },
                                                        { id: 'identity', label: 'Identity', icon: '👤', tabs: [{ id: 'soul', label: 'Soul' }] },
                                                    ].find((g) => g.id === activeGroup)?.tabs || []).map((tab) => (_jsx("button", { type: "button", className: `tab-button ${leftTab === tab.id ? "active" : ""}`, onClick: () => {
                                                            setLeftTab(tab.id);
                                                            setActiveGroup(null);
                                                        }, style: {
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
                                                        }, children: tab.label }, tab.id))) }) }), document.body)
                                            : null, leftTab === "chat" && (_jsxs(_Fragment, { children: [_jsxs("div", { className: "chat-scroll", style: { flex: 1 }, ref: chatScrollRef, onScroll: onChatScroll, children: [taskPlan.active && taskPlan.tasks.length > 0 && (_jsx(TaskChecklist, { plan: taskPlan })), _jsx(AnimatePresence, { initial: false, children: timeline.map((t) => t.kind === "message" ? (_jsx(ChatBubble, { msg: t.msg, streaming: streaming, onQuickReply: (text) => {
                                                                    try {
                                                                        stopTts();
                                                                    }
                                                                    catch {
                                                                        // ignore
                                                                    }
                                                                    sendText(text);
                                                                } }, `msg-${t.id}`)) : (_jsx(ActivityCard, { item: t.item }, `act-${t.id}`))) }), _jsx("div", { ref: chatBottomRef, style: { height: 1 } })] }), _jsxs("div", { className: "input-bar", children: [_jsxs("div", { className: "input-row", children: [_jsx("input", { className: "input-field", value: input, onChange: (e) => {
                                                                        setInput(e.target.value);
                                                                        setUserIsTyping(true);
                                                                        if (userTypingTimerRef.current)
                                                                            clearTimeout(userTypingTimerRef.current);
                                                                        userTypingTimerRef.current = window.setTimeout(() => setUserIsTyping(false), 1500);
                                                                    }, onKeyDown: (e) => {
                                                                        if (e.key === "Enter" && !e.shiftKey) {
                                                                            e.preventDefault();
                                                                            sendText();
                                                                        }
                                                                    }, placeholder: "Ask Echo anything..." }), _jsx(ContextRing, { messages: messages, contextWindow: providerInfo?.context_window || 0 }), _jsx("button", { className: "send-button", onClick: () => sendText(), type: "button", children: _jsx("svg", { width: "22", height: "22", viewBox: "0 0 24 24", fill: "none", xmlns: "http://www.w3.org/2000/svg", children: _jsx("path", { d: "M5 12L19 12M19 12L13 6M19 12L13 18", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round" }) }) })] }), _jsxs("div", { className: "controls-row", children: [_jsxs("div", { style: {
                                                                        position: "relative",
                                                                        display: "flex",
                                                                        alignItems: "center"
                                                                    }, children: [_jsxs("button", { type: "button", className: "icon-button", onClick: () => setShowSessions(!showSessions), title: "Sessions", style: {
                                                                                color: "#fff",
                                                                                background: showSessions ? "rgba(255,255,255,0.1)" : "transparent",
                                                                                border: `1px solid ${colors.line}`,
                                                                                padding: "6px 12px",
                                                                                display: "flex",
                                                                                alignItems: "center",
                                                                                gap: 8,
                                                                                fontSize: 13,
                                                                                fontWeight: 500,
                                                                                borderRadius: 8,
                                                                                height: 38,
                                                                            }, children: [_jsx("svg", { width: "14", height: "14", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", children: _jsx("path", { d: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" }) }), _jsx("span", { children: threads.find(t => t.id === activeThreadId)?.name || "Session" })] }), _jsx(AnimatePresence, { children: showSessions && (_jsxs(motion.div, { initial: { opacity: 0, y: 10, scale: 0.95 }, animate: { opacity: 1, y: 0, scale: 1 }, exit: { opacity: 0, y: 10, scale: 0.95 }, style: {
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
                                                                                }, children: [_jsx("div", { style: { padding: "4px 8px 8px 8px", fontSize: 11, fontWeight: 600, color: colors.textDim, textTransform: "uppercase", letterSpacing: 0.5 }, children: "Recent Sessions" }), _jsx("div", { style: { maxHeight: 300, overflowY: "auto", display: "flex", flexDirection: "column", gap: 2 }, children: threads.map((t) => (_jsxs("div", { onClick: () => {
                                                                                                switchThread(t.id);
                                                                                                setShowSessions(false);
                                                                                            }, style: {
                                                                                                padding: "8px 10px",
                                                                                                borderRadius: 8,
                                                                                                background: t.id === activeThreadId ? "rgba(255,255,255,0.08)" : "transparent",
                                                                                                cursor: "pointer",
                                                                                                display: "flex",
                                                                                                justifyContent: "space-between",
                                                                                                alignItems: "center",
                                                                                                transition: "background 0.2s"
                                                                                            }, onMouseEnter: (e) => (e.currentTarget.style.background = "rgba(255,255,255,0.05)"), onMouseLeave: (e) => (e.currentTarget.style.background = t.id === activeThreadId ? "rgba(255,255,255,0.08)" : "transparent"), children: [_jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 2, overflow: "hidden" }, children: [_jsx("span", { style: { fontSize: 13, fontWeight: t.id === activeThreadId ? 600 : 400, color: t.id === activeThreadId ? colors.text : colors.textDim, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }, children: t.name }), _jsx("span", { style: { fontSize: 10, color: colors.textDim }, children: new Date(t.at).toLocaleDateString() })] }), threads.length > 1 && (_jsx("button", { onClick: (e) => deleteThread(t.id, e), style: {
                                                                                                        background: "transparent",
                                                                                                        border: "none",
                                                                                                        color: colors.textDim,
                                                                                                        cursor: "pointer",
                                                                                                        padding: 4,
                                                                                                        borderRadius: 4,
                                                                                                        display: "flex",
                                                                                                        alignItems: "center",
                                                                                                        justifyContent: "center"
                                                                                                    }, onMouseEnter: (e) => (e.currentTarget.style.color = colors.danger), onMouseLeave: (e) => (e.currentTarget.style.color = colors.textDim), children: _jsx("svg", { width: "14", height: "14", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", children: _jsx("path", { d: "M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2v2" }) }) }))] }, t.id))) }), _jsx("div", { style: { height: 1, background: colors.line, margin: "4px 0" } }), _jsxs("button", { onClick: () => {
                                                                                            createNewThread();
                                                                                            setShowSessions(false);
                                                                                        }, style: {
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
                                                                                        }, onMouseEnter: (e) => (e.currentTarget.style.background = "rgba(255,255,255,0.1)"), onMouseLeave: (e) => (e.currentTarget.style.background = "rgba(255,255,255,0.05)"), children: [_jsxs("svg", { width: "14", height: "14", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", children: [_jsx("line", { x1: "12", y1: "5", x2: "12", y2: "19" }), _jsx("line", { x1: "5", y1: "12", x2: "19", y2: "12" })] }), "New Session"] })] })) })] }), _jsx("select", { className: "mode-picker", value: workspaceMode, onChange: (e) => {
                                                                        const v = (e.target.value || "auto");
                                                                        setWorkspaceMode(v);
                                                                        try {
                                                                            localStorage.setItem("echospeak_workspace_mode", v);
                                                                        }
                                                                        catch { }
                                                                    }, title: "Mode", children: workspaceModes.map(m => (_jsx("option", { value: m, children: m }, m))) }), _jsx("button", { className: `mic-button ${listening ? "active" : ""}`, type: "button", onClick: () => listening ? (stop(), setListening(false), setStreaming(false)) : start(), children: _jsxs("svg", { width: "20", height: "20", viewBox: "0 0 24 24", fill: "none", xmlns: "http://www.w3.org/2000/svg", children: [_jsx("path", { d: "M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z", fill: "currentColor" }), _jsx("path", { d: "M19 10v2a7 7 0 0 1-14 0v-2", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round" })] }) }), _jsx("button", { className: `mic-button ${monitoring ? "active" : ""}`, type: "button", onClick: () => setMonitoring(v => { const n = !v; if (n)
                                                                        refreshMonitor(); return n; }), children: _jsxs("svg", { width: "20", height: "20", viewBox: "0 0 24 24", fill: "none", xmlns: "http://www.w3.org/2000/svg", children: [_jsx("rect", { x: "2", y: "4", width: "20", height: "12", rx: "2", stroke: "currentColor", strokeWidth: "2" }), _jsx("path", { d: "M12 16v4M8 20h8", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round" })] }) }), _jsxs("div", { className: "inline-switcher", children: [_jsx("span", { className: `switcher-dot ${backendOnline === true ? "online" : backendOnline === false ? "offline" : ""}` }), _jsx("select", { className: "provider-picker", value: providerDraft.provider, onChange: (e) => {
                                                                                const p = e.target.value;
                                                                                setProviderDraft(d => ({ ...d, provider: p, model: p === "openai" ? openaiModelOptions[0] : p === "gemini" ? geminiModelOptions[0] : (providerModels[0] || d.model) }));
                                                                            }, disabled: switchingProvider || lmStudioOnly, children: (providerInfo?.available_providers || fallbackProviders)
                                                                                .filter(p => !lmStudioOnly || p.id === "lmstudio")
                                                                                .map(p => _jsx("option", { value: p.id, children: p.name }, p.id)) })] }), (providerDraft.provider === "openai" || providerDraft.provider === "gemini" || providerModels.length > 0) && (_jsx("select", { className: "model-picker", value: providerDraft.model, onChange: (e) => setProviderDraft(d => ({ ...d, model: e.target.value })), disabled: switchingProvider, children: (providerDraft.provider === "openai" ? openaiModelOptions : providerDraft.provider === "gemini" ? geminiModelOptions : providerModels).map(m => (_jsx("option", { value: m, children: m }, m))) }))] })] })] })), leftTab === "research" && (_jsx(ResearchPanel, { colors: colors, runs: research, selectedVoice: selectedVoice, voices: voices, onSelectedVoiceChange: setSelectedVoice, onClear: clearResearchRuns })), leftTab === "memory" && (_jsxs(_Fragment, { children: [_jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center", marginBottom: -12, flexWrap: "wrap" }, children: [_jsx("button", { className: "icon-button", style: { height: 32, padding: "0 12px", fontSize: 14, flex: 1 }, onClick: refreshMemory, type: "button", children: "Refresh" }), _jsx("button", { className: "icon-button", style: { height: 32, padding: "0 12px", fontSize: 14, flex: 1 }, onClick: async () => {
                                                                try {
                                                                    const res = await fetch(`${apiBase}/memory/compact?thread_id=${activeThreadId}`, { method: "POST" });
                                                                    if (res.ok) {
                                                                        refreshMemory();
                                                                    }
                                                                }
                                                                catch (e) {
                                                                    console.error("Compact memory error:", e);
                                                                }
                                                            }, disabled: !memoryCount, type: "button", children: "Compact" }), _jsx("button", { className: "icon-button", style: { height: 32, padding: "0 12px", fontSize: 14, flex: 1 }, onClick: clearAllMemory, disabled: !memoryCount, type: "button", children: "Clear All" }), _jsxs("select", { className: "input-field", style: { height: 32, padding: "0 10px", fontSize: 13, width: 120 }, value: memoryFilterType, onChange: (e) => setMemoryFilterType(e.target.value), children: [_jsx("option", { value: "", children: "All Types" }), _jsx("option", { value: "conversation", children: "Conversation" }), _jsx("option", { value: "preference", children: "Preference" }), _jsx("option", { value: "profile", children: "Profile" }), _jsx("option", { value: "project", children: "Project" }), _jsx("option", { value: "contacts", children: "Contacts" }), _jsx("option", { value: "note", children: "Note" })] })] }), selectedMemoryIds.length > 0 && (_jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center", marginTop: 8, padding: "8px 12px", background: colors.panel2, borderRadius: 8 }, children: [_jsxs("span", { style: { fontSize: 12, color: colors.textDim }, children: [selectedMemoryIds.length, " selected"] }), _jsx("button", { className: "icon-button", style: { height: 28, padding: "0 10px", fontSize: 12 }, type: "button", onClick: async () => {
                                                                for (const id of selectedMemoryIds) {
                                                                    await fetch(`${apiBase}/memory/${id}?thread_id=${activeThreadId}`, { method: "DELETE" });
                                                                }
                                                                setSelectedMemoryIds([]);
                                                                refreshMemory();
                                                            }, children: "Delete Selected" }), _jsxs("select", { className: "input-field", style: { height: 28, padding: "0 8px", fontSize: 12, width: 100 }, value: "", onChange: async (e) => {
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
                                                            }, children: [_jsx("option", { value: "", children: "Set Type" }), _jsx("option", { value: "conversation", children: "Conversation" }), _jsx("option", { value: "preference", children: "Preference" }), _jsx("option", { value: "profile", children: "Profile" }), _jsx("option", { value: "project", children: "Project" }), _jsx("option", { value: "note", children: "Note" })] }), _jsx("button", { className: "icon-button", style: { height: 28, padding: "0 10px", fontSize: 12 }, type: "button", onClick: () => setSelectedMemoryIds([]), children: "Deselect" })] })), _jsx("div", { className: "research-scroll", children: memoryLoading ? (_jsx("div", { className: "research-card", children: _jsx("div", { className: "research-snippet", children: "Loading memory\u2026" }) })) : memoryItems.length ? (memoryItems
                                                        .filter((m) => !memoryFilterType || m.memory_type === memoryFilterType)
                                                        .map((m) => {
                                                        const ts = (m.timestamp || String(m.metadata?.timestamp || "")).trim();
                                                        const preview = (m.text || "").trim();
                                                        const pinned = Boolean(m.pinned);
                                                        const memoryType = String(m.memory_type || "").trim();
                                                        const isEditing = editingMemoryId === m.id;
                                                        const isSelected = selectedMemoryIds.includes(m.id);
                                                        return (_jsxs("div", { className: "research-card", style: { border: isSelected ? `1px solid ${colors.accent}` : undefined }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center", marginBottom: 10 }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 8 }, children: [_jsx("input", { type: "checkbox", checked: isSelected, onChange: (e) => {
                                                                                        if (e.target.checked) {
                                                                                            setSelectedMemoryIds([...selectedMemoryIds, m.id]);
                                                                                        }
                                                                                        else {
                                                                                            setSelectedMemoryIds(selectedMemoryIds.filter((id) => id !== m.id));
                                                                                        }
                                                                                    }, style: { width: 16, height: 16 } }), _jsx("div", { style: { fontSize: 14, color: colors.textDim, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }, children: ts ? ts : "(no timestamp)" })] }), _jsxs("div", { style: { display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }, children: [_jsxs("select", { className: "input-field", style: { height: 28, padding: "0 8px", fontSize: 12, width: 90 }, value: memoryType, onChange: async (e) => {
                                                                                        const newType = e.target.value;
                                                                                        await fetch(`${apiBase}/memory/update`, {
                                                                                            method: "POST",
                                                                                            headers: { "Content-Type": "application/json" },
                                                                                            body: JSON.stringify({ id: m.id, thread_id: activeThreadId, memory_type: newType }),
                                                                                        });
                                                                                        refreshMemory();
                                                                                    }, children: [_jsx("option", { value: "", children: "No Type" }), _jsx("option", { value: "conversation", children: "Conversation" }), _jsx("option", { value: "preference", children: "Preference" }), _jsx("option", { value: "profile", children: "Profile" }), _jsx("option", { value: "project", children: "Project" }), _jsx("option", { value: "contacts", children: "Contacts" }), _jsx("option", { value: "note", children: "Note" })] }), _jsx("button", { className: "icon-button", style: { height: 28, padding: "0 10px", fontSize: 12 }, type: "button", onClick: () => {
                                                                                        setEditingMemoryId(isEditing ? null : m.id);
                                                                                        setEditingMemoryText(preview);
                                                                                    }, children: isEditing ? "Cancel" : "Edit" }), _jsx("button", { className: "icon-button", style: { height: 28, padding: "0 10px", fontSize: 12 }, type: "button", onClick: () => togglePinMemoryItem(m), title: pinned ? "Unpin" : "Pin", children: pinned ? "📌" : "Pin" }), _jsx("button", { className: "icon-button", style: { height: 28, padding: "0 10px", fontSize: 12 }, type: "button", onClick: () => deleteMemoryItem(m.id), children: "Delete" })] })] }), isEditing ? (_jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 8 }, children: [_jsx("textarea", { className: "input-field", style: { width: "100%", minHeight: 80, padding: 10, fontSize: 13, resize: "vertical" }, value: editingMemoryText, onChange: (e) => setEditingMemoryText(e.target.value) }), _jsx("button", { className: "icon-button", style: { height: 32, padding: "0 14px", fontSize: 13, alignSelf: "flex-end" }, type: "button", onClick: async () => {
                                                                                await fetch(`${apiBase}/memory/${m.id}?thread_id=${activeThreadId}`, {
                                                                                    method: "PUT",
                                                                                    headers: { "Content-Type": "application/json" },
                                                                                    body: JSON.stringify({ text: editingMemoryText }),
                                                                                });
                                                                                setEditingMemoryId(null);
                                                                                refreshMemory();
                                                                            }, children: "Save" })] })) : (_jsx("div", { className: "research-snippet", style: { whiteSpace: "pre-wrap" }, children: preview || "(empty)" }))] }, m.id));
                                                    })) : (_jsx("div", { className: "research-card", children: _jsx("div", { className: "research-snippet", children: "No saved memories yet." }) })) })] })), leftTab === "docs" && (_jsxs(_Fragment, { children: [_jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center", marginBottom: -12 }, children: [_jsx("button", { className: "icon-button", style: { height: 32, padding: "0 12px", fontSize: 14, flex: 1 }, onClick: refreshDocuments, type: "button", children: "Refresh" }), _jsx("button", { className: "icon-button", style: { height: 32, padding: "0 12px", fontSize: 14, flex: 1 }, onClick: () => docInputRef.current?.click(), disabled: !docEnabled, type: "button", children: "Upload" })] }), _jsxs("div", { className: "research-scroll", children: [_jsxs("div", { className: "research-card", children: [!docEnabled ? (_jsx("div", { className: "research-snippet", children: "Document RAG is disabled. Set DOCUMENT_RAG_ENABLED=true to enable uploads." })) : null, docFile ? _jsxs("div", { className: "research-snippet", children: ["Selected: ", docFile.name] }) : null, docError ? _jsxs("div", { className: "research-snippet", children: ["Error: ", docError] }) : null] }), docSources.length ? (_jsxs("div", { className: "research-card", children: [_jsx("div", { style: { fontSize: 14, color: colors.textDim, marginBottom: 8, fontWeight: 500 }, children: "Sources used in last response" }), docSources.map((s) => (_jsx("div", { className: "research-snippet", children: s.filename || s.source || s.id }, `${s.id}-${s.chunk ?? ""}`)))] })) : null, docLoading ? (_jsx("div", { className: "research-card", children: _jsx("div", { className: "research-snippet", children: "Loading documents\u2026" }) })) : docItems.length ? (docItems.map((doc) => (_jsxs("div", { className: "research-card", children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginBottom: 8 }, children: [_jsx("div", { style: { fontSize: 15, fontWeight: 600 }, children: doc.filename }), _jsx("button", { className: "icon-button", style: { height: 32, padding: "0 14px", fontSize: 14 }, type: "button", onClick: () => deleteDocument(doc.id), children: "Delete" })] }), _jsxs("div", { className: "research-snippet", children: ["Chunks: ", doc.chunks] }), doc.timestamp ? _jsx("div", { className: "research-snippet", children: doc.timestamp }) : null] }, doc.id)))) : (_jsx("div", { className: "research-card", children: _jsx("div", { className: "research-snippet", children: "No documents uploaded yet." }) }))] })] })), leftTab === "settings" && (_jsx(_Fragment, { children: _jsx("div", { className: "research-scroll", children: _jsxs("div", { className: "research-card", children: [_jsx("div", { className: "research-title", children: "Runtime Settings" }), _jsxs("div", { className: "research-snippet", style: { marginBottom: 12 }, children: ["These settings are saved to ", _jsx("code", { children: "apps/backend/data/settings.json" }), " and override ", _jsx("code", { children: ".env" }), " defaults."] }), settingsError ? _jsxs("div", { className: "research-snippet", children: ["Error: ", settingsError] }) : null, settingsLoading ? _jsx("div", { className: "research-snippet", children: "Loading settings\u2026" }) : null, !settingsLoading && (settingsErrors.length || settingsWarnings.length) ? (_jsxs("div", { className: "research-card", style: { background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", border: "1px solid rgba(255,255,255,0.1)", marginTop: 12, backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", borderRadius: 12, padding: 16, boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)" }, children: [_jsx("div", { style: { fontSize: 14, fontWeight: 700, marginBottom: 8 }, children: "Configuration checks" }), settingsErrors.length ? (_jsxs("div", { style: { marginBottom: 10 }, children: [_jsx("div", { style: { fontSize: 13, fontWeight: 700, color: colors.danger, marginBottom: 6 }, children: "Errors (must fix)" }), settingsErrors.map((i, idx) => (_jsx("div", { className: "research-snippet", style: { color: colors.danger }, children: i.message }, `${i.key}-${idx}`)))] })) : null, settingsWarnings.length ? (_jsxs("div", { children: [_jsx("div", { style: { fontSize: 13, fontWeight: 700, color: "#f59e0b", marginBottom: 6 }, children: "Warnings" }), settingsWarnings.map((i, idx) => (_jsx("div", { className: "research-snippet", children: i.message }, `${i.key}-${idx}`)))] })) : null] })) : null, !settingsLoading && runtimeSettings ? (_jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 12 }, children: [_jsxs("div", { className: "settings-section", style: { background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)" }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }, children: [_jsx("span", { style: { fontSize: 13, fontWeight: 600 }, children: "Current Provider" }), _jsxs("span", { style: { display: "flex", alignItems: "center", gap: 6 }, children: [_jsx("span", { className: `switcher-dot ${backendOnline === true ? "online" : backendOnline === false ? "offline" : ""}` }), _jsx("span", { style: { fontSize: 14, fontWeight: 500 }, children: providerInfo?.available_providers?.find(p => p.id === providerDraft.provider)?.name || providerDraft.provider || "Unknown" })] })] }), _jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center" }, children: [_jsx("select", { className: "input-field", value: providerDraft.provider, onChange: (e) => {
                                                                                        const p = e.target.value;
                                                                                        setProviderDraft(d => ({ ...d, provider: p, model: p === "openai" ? openaiModelOptions[0] : p === "gemini" ? geminiModelOptions[0] : (providerModels[0] || d.model) }));
                                                                                    }, disabled: switchingProvider || lmStudioOnly, style: { flex: 1, padding: "8px 12px", fontSize: 14 }, children: (providerInfo?.available_providers || fallbackProviders)
                                                                                        .filter(p => !lmStudioOnly || p.id === "lmstudio")
                                                                                        .map(p => _jsx("option", { value: p.id, children: p.name }, p.id)) }), (providerDraft.provider === "openai" || providerDraft.provider === "gemini" || providerModels.length > 0) && (_jsx("select", { className: "input-field", value: providerDraft.model, onChange: (e) => setProviderDraft(d => ({ ...d, model: e.target.value })), disabled: switchingProvider, style: { flex: 1, padding: "8px 12px", fontSize: 13 }, children: (providerDraft.provider === "openai" ? openaiModelOptions : providerDraft.provider === "gemini" ? geminiModelOptions : providerModels).map(m => (_jsx("option", { value: m, children: m }, m))) }))] }), switchingProvider ? (_jsx("div", { className: "research-snippet", style: { marginTop: 6, color: colors.accent }, children: "Switching provider..." })) : null] }), _jsxs("div", { style: { marginTop: 32, marginBottom: 16, paddingBottom: 8, borderBottom: `1px solid rgba(255,255,255,0.1)` }, children: [_jsx("div", { style: { fontSize: 18, fontWeight: 700, color: colors.text }, children: "APIs & AI Providers" }), _jsx("div", { style: { fontSize: 13, color: colors.textDim }, children: "Set API keys for remote LLMs, local runners, embeddings, and voice models." })] }), _jsxs("div", { className: "settings-section", style: {
                                                                        background: "rgba(255, 255, 255, 0.02)",
                                                                        border: "1px solid rgba(255, 255, 255, 0.08)",
                                                                        borderRadius: "12px",
                                                                        padding: "20px",
                                                                        marginBottom: "20px"
                                                                    }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }, children: "Cloud Providers" }), _jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 16 }, children: [_jsxs("div", { style: { padding: 12, background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)" }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }, children: [_jsx("label", { style: { fontSize: 13, fontWeight: 500 }, children: "OpenAI" }), settingsTests.openai?.ok && settingsTestedKeys.openai === String(settingsDraft?.openai?.api_key ?? "") && (_jsx("span", { style: { fontSize: 11, color: "#22c55e" }, children: "\u2713 Connected" }))] }), _jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsx("input", { type: "password", className: "input-field", value: String(settingsDraft?.openai?.api_key ?? ""), placeholder: "sk-...", onChange: (e) => {
                                                                                                        const next = e.target.value;
                                                                                                        setSettingsDraft((d) => ({ ...d, openai: { ...(d.openai || {}), api_key: next } }));
                                                                                                        setSettingsTests((m) => ({ ...m, openai: null }));
                                                                                                        setSettingsTestedKeys((m) => {
                                                                                                            const copy = { ...m };
                                                                                                            delete copy.openai;
                                                                                                            return copy;
                                                                                                        });
                                                                                                    }, style: { flex: 1, padding: "8px 12px", fontSize: 13 } }), _jsx("button", { className: "icon-button", style: { padding: "0 12px", fontSize: 12 }, type: "button", onClick: () => runSettingsTest("openai"), disabled: Boolean(settingsTesting.openai), children: settingsTesting.openai ? "..." : "Test" })] }), settingsTests.openai && !settingsTests.openai.ok && (_jsx("div", { className: "research-snippet", style: { marginTop: 4, color: colors.danger, fontSize: 11 }, children: settingsTests.openai.message }))] }), _jsxs("div", { style: { padding: 12, background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", boxShadow: providerDraft.provider === "gemini" ? "0 4px 16px -4px rgba(45,108,255,0.2), inset 0 1px 0 rgba(255,255,255,0.1), 0 0 0 1px rgba(140,180,255,0.4)" : "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)" }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }, children: [_jsx("label", { style: { fontSize: 13, fontWeight: 500 }, children: "Google Gemini" }), settingsTests.gemini?.ok && settingsTestedKeys.gemini === String(settingsDraft?.gemini?.api_key ?? "") && (_jsx("span", { style: { fontSize: 11, color: "#22c55e" }, children: "\u2713 Connected" }))] }), _jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsx("input", { type: "password", className: "input-field", value: String(settingsDraft?.gemini?.api_key ?? ""), placeholder: "AIza...", onChange: (e) => {
                                                                                                        const next = e.target.value;
                                                                                                        setSettingsDraft((d) => ({ ...d, gemini: { ...(d.gemini || {}), api_key: next } }));
                                                                                                        setSettingsTests((m) => ({ ...m, gemini: null }));
                                                                                                        setSettingsTestedKeys((m) => {
                                                                                                            const copy = { ...m };
                                                                                                            delete copy.gemini;
                                                                                                            return copy;
                                                                                                        });
                                                                                                    }, style: { flex: 1, padding: "8px 12px", fontSize: 13 } }), _jsx("button", { className: "icon-button", style: { padding: "0 12px", fontSize: 12 }, type: "button", onClick: () => runSettingsTest("gemini"), disabled: Boolean(settingsTesting.gemini), children: settingsTesting.gemini ? "..." : "Test" })] }), settingsTests.gemini && !settingsTests.gemini.ok && (_jsx("div", { className: "research-snippet", style: { marginTop: 4, color: colors.danger, fontSize: 11 }, children: settingsTests.gemini.message }))] })] }), _jsx("div", { className: "research-snippet", style: { marginTop: 8, fontSize: 11 }, children: "Keys are saved securely and redacted on reload. Test to verify connectivity." })] }), _jsxs("div", { className: "settings-section", style: {
                                                                        background: "rgba(255, 255, 255, 0.02)",
                                                                        border: "1px solid rgba(255, 255, 255, 0.08)",
                                                                        borderRadius: "12px",
                                                                        padding: "20px",
                                                                        marginBottom: "20px"
                                                                    }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }, children: "Local Models" }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px" }, children: [_jsx(Toggle, { label: "LM Studio Only", checked: Boolean(settingsDraft.lm_studio_only), onChange: (v) => updateDraft("lm_studio_only", v) }), _jsx(Toggle, { label: "Use Local Models", checked: Boolean(settingsDraft.use_local_models), onChange: (v) => updateDraft("use_local_models", v) }), _jsx(Toggle, { label: "Enable Tool Calling", checked: Boolean(settingsDraft.use_tool_calling_llm), onChange: (v) => updateDraft("use_tool_calling_llm", v) }), _jsx(Toggle, { label: "LM Studio Tool Calling", checked: Boolean(settingsDraft.lmstudio_tool_calling), onChange: (v) => updateDraft("lmstudio_tool_calling", v) }), _jsx(Toggle, { label: "Gemini LangGraph Tools", checked: Boolean(settingsDraft.gemini_use_langgraph), onChange: (v) => updateDraft("gemini_use_langgraph", v) })] }), settingsSavedAt ? (_jsxs("div", { className: "research-snippet", style: { marginTop: 10, fontSize: 11 }, children: ["Last saved: ", new Date(settingsSavedAt).toLocaleString()] })) : null] }), _jsxs("div", { className: "settings-section", style: {
                                                                        background: "rgba(255, 255, 255, 0.02)",
                                                                        border: "1px solid rgba(255, 255, 255, 0.08)",
                                                                        borderRadius: "12px",
                                                                        padding: "20px",
                                                                        marginBottom: "20px"
                                                                    }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }, children: "System Actions (Safety Gates)" }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px" }, children: [_jsx(Toggle, { label: "Enable System Actions", checked: Boolean(settingsDraft.enable_system_actions), onChange: (v) => updateDraft("enable_system_actions", v) }), _jsx(Toggle, { label: "Allow Playwright", checked: Boolean(settingsDraft.allow_playwright), onChange: (v) => updateDraft("allow_playwright", v) }), _jsx(Toggle, { label: "Allow Terminal Commands", checked: Boolean(settingsDraft.allow_terminal_commands), onChange: (v) => updateDraft("allow_terminal_commands", v) }), _jsx(Toggle, { label: "Allow File Write", checked: Boolean(settingsDraft.allow_file_write), onChange: (v) => updateDraft("allow_file_write", v) }), _jsx(Toggle, { label: "Allow Desktop Automation", checked: Boolean(settingsDraft.allow_desktop_automation), onChange: (v) => updateDraft("allow_desktop_automation", v) }), _jsx(Toggle, { label: "Allow Open Application", checked: Boolean(settingsDraft.allow_open_application), onChange: (v) => updateDraft("allow_open_application", v) }), _jsx(Toggle, { label: "Allow Open Chrome", checked: Boolean(settingsDraft.allow_open_chrome), onChange: (v) => updateDraft("allow_open_chrome", v) }), _jsx(Toggle, { label: "Allow Self Modification", checked: Boolean(settingsDraft.allow_self_modification), onChange: (v) => updateDraft("allow_self_modification", v) })] })] }), _jsxs("div", { children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Terminal Allowlist (first token)" }), _jsx("input", { type: "text", className: "input-field", value: Array.isArray(settingsDraft.terminal_command_allowlist) ? settingsDraft.terminal_command_allowlist.join(",") : "", placeholder: "git,rg,ls,cat,find,grep,python,python3,uv,pytest,npm,npx,node,go,make", onChange: (e) => updateDraft("terminal_command_allowlist", e.target.value
                                                                                .split(",")
                                                                                .map((x) => x.trim().toLowerCase())
                                                                                .filter(Boolean)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "File Tool Root" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.file_tool_root || ""), placeholder: "/absolute/path/to/workspace", onChange: (e) => updateDraft("file_tool_root", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { display: "flex", gap: 10, flexWrap: "wrap" }, children: [_jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Terminal timeout (s)" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.terminal_command_timeout ?? 20), min: 1, onChange: (e) => updateDraft("terminal_command_timeout", Number(e.target.value || 0)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 220px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Terminal max output chars" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.terminal_max_output_chars ?? 8000), min: 100, onChange: (e) => updateDraft("terminal_max_output_chars", Number(e.target.value || 0)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] }), _jsxs("div", { children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Open application allowlist" }), _jsx("input", { type: "text", className: "input-field", value: Array.isArray(settingsDraft.open_application_allowlist) ? settingsDraft.open_application_allowlist.join(",") : "", placeholder: "notepad,calc,chrome", onChange: (e) => updateDraft("open_application_allowlist", e.target.value
                                                                                .split(",")
                                                                                .map((x) => x.trim().toLowerCase())
                                                                                .filter(Boolean)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { marginTop: 32, marginBottom: 16, paddingBottom: 8, borderBottom: `1px solid rgba(255,255,255,0.1)` }, children: [_jsx("div", { style: { fontSize: 18, fontWeight: 700, color: colors.text }, children: "Platforms & Bots" }), _jsx("div", { style: { fontSize: 13, color: colors.textDim }, children: "Configure messaging platforms, bot channels, and communication surfaces in one place." })] }), _jsxs("div", { className: "settings-section", style: { ...settingsSectionStyle, ...platformCardStyle }, children: [_jsx(PlatformHeader, { icon: "\u2709\uFE0F", title: "Email", subtitle: "IMAP / SMTP automation channel", accent: "#60a5fa" }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px", marginBottom: 12 }, children: [_jsx(Toggle, { label: "Allow Email Automation", checked: Boolean(settingsDraft.allow_email), onChange: (v) => updateDraft("allow_email", v) }), _jsx(Toggle, { label: "Use TLS", checked: Boolean(settingsDraft.email_use_tls ?? true), onChange: (v) => updateDraft("email_use_tls", v) })] }), _jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 12 }, children: [_jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsxs("div", { style: { flex: 2 }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "IMAP Host" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.email_imap_host || ""), placeholder: "imap.gmail.com", onChange: (e) => updateDraft("email_imap_host", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: 1 }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "IMAP Port" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.email_imap_port || 993), onChange: (e) => updateDraft("email_imap_port", parseInt(e.target.value) || 993), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] }), _jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsxs("div", { style: { flex: 2 }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "SMTP Host" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.email_smtp_host || ""), placeholder: "smtp.gmail.com", onChange: (e) => updateDraft("email_smtp_host", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: 1 }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "SMTP Port" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.email_smtp_port || 587), onChange: (e) => updateDraft("email_smtp_port", parseInt(e.target.value) || 587), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] }), _jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsxs("div", { style: { flex: 1 }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Email Username" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.email_username || ""), placeholder: "user@example.com", onChange: (e) => updateDraft("email_username", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: 1 }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "App Password" }), _jsx("input", { type: "password", className: "input-field", value: String(settingsDraft.email_password || ""), placeholder: "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022", onChange: (e) => updateDraft("email_password", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] })] })] }), _jsxs("div", { className: "settings-section", style: { ...settingsSectionStyle, ...platformCardStyle }, children: [_jsx(PlatformHeader, { icon: "\u2708\uFE0F", title: "Telegram", subtitle: "Direct bot control and notifications", accent: "#38bdf8" }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "12px 24px", marginBottom: 16 }, children: [_jsx(Toggle, { label: "Enable Telegram Bot", checked: Boolean(settingsDraft.allow_telegram_bot), onChange: (v) => updateDraft("allow_telegram_bot", v) }), _jsx(Toggle, { label: "Auto-Confirm Telegram Actions", checked: Boolean(settingsDraft.telegram_auto_confirm ?? true), onChange: (v) => updateDraft("telegram_auto_confirm", v) })] }), _jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 16 }, children: [_jsxs("div", { children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: "Bot Token (from @BotFather)" }), _jsx("input", { type: "password", className: "input-field", value: String(settingsDraft.telegram_bot_token || ""), placeholder: "123456789:ABCdefGHIjklMNO...", onChange: (e) => updateDraft("telegram_bot_token", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: "Allowed Users (comma separated @usernames)" }), _jsx("input", { type: "text", className: "input-field", value: Array.isArray(settingsDraft.telegram_allowed_users) ? settingsDraft.telegram_allowed_users.join(",") : "", placeholder: "@bob,@alice", onChange: (e) => updateDraft("telegram_allowed_users", e.target.value
                                                                                                .split(",")
                                                                                                .map((x) => x.trim().toLowerCase())
                                                                                                .filter(Boolean)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] })] }), _jsxs("div", { className: "settings-section", style: { ...settingsSectionStyle, ...platformCardStyle }, children: [_jsx(PlatformHeader, { icon: "\uD83C\uDFAE", title: "Discord", subtitle: "Role-based server access, webhook delivery, and trusted-user controls", accent: "#818cf8" }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "12px 24px", marginBottom: 16 }, children: [_jsx(Toggle, { label: "Enable Discord Bot", checked: Boolean(settingsDraft.allow_discord_bot), onChange: (v) => updateDraft("allow_discord_bot", v) }), _jsx(Toggle, { label: "Allow Discord Webhook", checked: Boolean(settingsDraft.allow_discord_webhook), onChange: (v) => updateDraft("allow_discord_webhook", v) }), _jsx(Toggle, { label: "Auto-Confirm Discord Actions", checked: Boolean(settingsDraft.discord_bot_auto_confirm ?? true), onChange: (v) => updateDraft("discord_bot_auto_confirm", v) })] }), _jsxs("div", { style: { display: "flex", flexWrap: "wrap", gap: 16 }, children: [_jsxs("div", { style: { flex: "1 1 300px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: "Bot Token" }), _jsx("input", { type: "password", className: "input-field", value: String(settingsDraft.discord_bot_token || ""), placeholder: "Bot token for EchoSpeak Discord bot", onChange: (e) => updateDraft("discord_bot_token", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 300px" }, children: [_jsxs("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: ["Allowed Server Roles (comma separated)", _jsx(RequiredBadge, { issueKey: "discord_bot_allowed_roles" })] }), _jsx("input", { type: "text", className: "input-field", value: Array.isArray(settingsDraft.discord_bot_allowed_roles) ? settingsDraft.discord_bot_allowed_roles.join(",") : String(settingsDraft.discord_bot_allowed_roles || ""), onChange: (e) => updateDraft("discord_bot_allowed_roles", e.target.value
                                                                                                .split(",")
                                                                                                .map((x) => x.trim())
                                                                                                .filter(Boolean)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 300px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: "Allowed User IDs (optional fallback)" }), _jsx("input", { type: "text", className: "input-field", value: Array.isArray(settingsDraft.discord_bot_allowed_users) ? settingsDraft.discord_bot_allowed_users.join(",") : String(settingsDraft.discord_bot_allowed_users || ""), placeholder: "Optional explicit user allowlist", onChange: (e) => updateDraft("discord_bot_allowed_users", e.target.value
                                                                                                .split(",")
                                                                                                .map((x) => x.trim())
                                                                                                .filter(Boolean)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 300px" }, children: [_jsxs("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: ["Owner User ID", _jsx(RequiredBadge, { issueKey: "discord_bot_owner_id" })] }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.discord_bot_owner_id || ""), placeholder: "Your Discord user ID", onChange: (e) => updateDraft("discord_bot_owner_id", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14, borderColor: isError("discord_bot_owner_id") ? colors.danger : undefined } })] }), _jsxs("div", { style: { flex: "1 1 100%" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: "Webhook URL" }), _jsx("input", { type: "password", className: "input-field", value: String(settingsDraft.discord_webhook_url || ""), placeholder: "https://discord.com/api/webhooks/...", onChange: (e) => updateDraft("discord_webhook_url", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 100%" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: "Trusted User IDs (comma separated)" }), _jsx("input", { type: "text", className: "input-field", value: Array.isArray(settingsDraft.discord_bot_trusted_users) ? settingsDraft.discord_bot_trusted_users.join(",") : "", placeholder: "1234567890,0987654321", onChange: (e) => updateDraft("discord_bot_trusted_users", e.target.value
                                                                                                .split(",")
                                                                                                .map((x) => x.trim())
                                                                                                .filter(Boolean)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] }), _jsxs("div", { style: { marginTop: 20, paddingTop: 16, borderTop: "1px solid rgba(255,255,255,0.08)" }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }, children: [_jsx("span", { style: { fontSize: 14 }, children: "\uD83D\uDCCB" }), _jsx("span", { style: { fontSize: 13, fontWeight: 600, color: colors.text }, children: "Changelog Announcements" }), _jsx("span", { style: { fontSize: 11, color: colors.textDim }, children: "(git push \u2192 Discord channel)" })] }), _jsx("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "12px 24px", marginBottom: 12 }, children: _jsx(Toggle, { label: "Enable Changelog Posts", checked: Boolean(settingsDraft.discord_changelog_enabled ?? true), onChange: (v) => updateDraft("discord_changelog_enabled", v) }) }), _jsxs("div", { style: { display: "flex", flexWrap: "wrap", gap: 16 }, children: [_jsxs("div", { style: { flex: "2 1 320px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: "Channel targets (comma separated, first match wins)" }), _jsx("input", { type: "text", className: "input-field", value: Array.isArray(settingsDraft.discord_changelog_channels) ? settingsDraft.discord_changelog_channels.join(",") : String(settingsDraft.discord_changelog_channels || ""), placeholder: "updates,changes,changelog,dev-updates,announcements", onChange: (e) => updateDraft("discord_changelog_channels", e.target.value
                                                                                                        .split(",")
                                                                                                        .map((x) => x.trim())
                                                                                                        .filter(Boolean)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 240px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: "Server name or ID (blank = search all)" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.discord_changelog_server || ""), placeholder: "Leave blank to auto-detect", onChange: (e) => updateDraft("discord_changelog_server", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] }), _jsx("div", { className: "research-snippet", style: { marginTop: 8, fontSize: 11, color: colors.textDim }, children: "When new commits are pushed, EchoSpeak posts an update to the first matching Discord channel. Supports channel names, IDs, and fuzzy matching." })] })] }), _jsxs("div", { className: "settings-section", style: { ...settingsSectionStyle, ...platformCardStyle }, children: [_jsx(PlatformHeader, { icon: "\uD83D\uDFE2", title: "WhatsApp", subtitle: "External WhatsApp bridge / API endpoint", accent: "#22c55e" }), _jsx("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "12px 24px", marginBottom: 16 }, children: _jsx(Toggle, { label: "Enable WhatsApp", checked: Boolean(settingsDraft.allow_whatsapp), onChange: (v) => updateDraft("allow_whatsapp", v) }) }), _jsx("div", { style: { display: "flex", gap: 12, flexWrap: "wrap" }, children: _jsxs("div", { style: { flex: "2 1 320px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "WhatsApp API URL" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.whatsapp_api_url || ""), placeholder: "http://localhost:3001", onChange: (e) => updateDraft("whatsapp_api_url", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }) })] }), _jsxs("div", { className: "settings-section", style: { ...settingsSectionStyle, ...platformCardStyle }, children: [_jsx(PlatformHeader, { icon: "\uD83D\uDC26", title: "Twitter / X", subtitle: "Autonomous tweeting, changelog posts, and mention replies", accent: "#1d9bf0" }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "12px 24px", marginBottom: 16 }, children: [_jsx(Toggle, { label: "Enable Twitter", checked: Boolean(settingsDraft.allow_twitter), onChange: (v) => updateDraft("allow_twitter", v) }), _jsx(Toggle, { label: "Autonomous Tweeting", checked: Boolean(settingsDraft.twitter_autonomous_enabled), onChange: (v) => updateDraft("twitter_autonomous_enabled", v) }), _jsx(Toggle, { label: "Require Approval", checked: Boolean(settingsDraft.twitter_autonomous_require_approval ?? true), onChange: (v) => updateDraft("twitter_autonomous_require_approval", v) }), _jsx(Toggle, { label: "Auto-Reply Mentions", checked: Boolean(settingsDraft.twitter_auto_reply_mentions), onChange: (v) => updateDraft("twitter_auto_reply_mentions", v) })] }), _jsxs("div", { style: { fontSize: 12, color: colors.textDim, marginBottom: 14, lineHeight: 1.5 }, children: ["Use the X app's OAuth 1.0a credentials from the bot account: paste ", _jsx("strong", { style: { color: colors.text }, children: "Consumer Key" }), " into Client ID, ", _jsx("strong", { style: { color: colors.text }, children: "Consumer Secret" }), " into Client Secret, and the OAuth 1.0a access token pair into the access token fields. You can leave Bot User ID blank and let EchoSpeak auto-detect the authenticated account on startup."] }), _jsxs("div", { style: { display: "flex", flexWrap: "wrap", gap: 16 }, children: [_jsxs("div", { style: { flex: "1 1 300px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: "Client ID / Consumer Key" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.twitter_client_id || ""), placeholder: "OAuth 1.0a Consumer Key", onChange: (e) => updateDraft("twitter_client_id", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 300px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: "Client Secret / Consumer Secret" }), _jsx("input", { type: "password", className: "input-field", value: String(settingsDraft.twitter_client_secret || ""), placeholder: "OAuth 1.0a Consumer Secret", onChange: (e) => updateDraft("twitter_client_secret", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 300px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: "Access Token" }), _jsx("input", { type: "password", className: "input-field", value: String(settingsDraft.twitter_access_token || ""), placeholder: "OAuth 1.0a access token", onChange: (e) => updateDraft("twitter_access_token", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 300px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: "Access Token Secret" }), _jsx("input", { type: "password", className: "input-field", value: String(settingsDraft.twitter_access_token_secret || ""), placeholder: "OAuth 1.0a token secret", onChange: (e) => updateDraft("twitter_access_token_secret", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 300px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: "Bearer Token (app-only)" }), _jsx("input", { type: "password", className: "input-field", value: String(settingsDraft.twitter_bearer_token || ""), placeholder: "App bearer token", onChange: (e) => updateDraft("twitter_bearer_token", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 300px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 6 }, children: "Bot User ID" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.twitter_bot_user_id || ""), placeholder: "Optional \u2014 leave blank to auto-detect", onChange: (e) => updateDraft("twitter_bot_user_id", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] }), _jsxs("div", { style: { display: "flex", gap: 10, flexWrap: "wrap", marginTop: 16 }, children: [_jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Poll interval (s)" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.twitter_poll_interval ?? 120), min: 30, onChange: (e) => updateDraft("twitter_poll_interval", Number(e.target.value || 120)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Autonomous interval (min)" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.twitter_autonomous_interval ?? 120), min: 30, onChange: (e) => updateDraft("twitter_autonomous_interval", Number(e.target.value || 120)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Max daily tweets" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.twitter_autonomous_max_daily ?? 6), min: 1, max: 20, onChange: (e) => updateDraft("twitter_autonomous_max_daily", Number(e.target.value || 6)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] })] }), _jsxs("div", { className: "settings-section", style: settingsSectionStyle, children: [_jsx("div", { style: { fontSize: 13, fontWeight: 700, color: colors.text, marginBottom: 4 }, children: "Productivity & Service Integrations" }), _jsx("div", { style: { fontSize: 12, color: colors.textDim, marginBottom: 14 }, children: "Keep non-messaging integrations grouped here for workspaces, content, calendars, and home services." }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px", marginBottom: 12 }, children: [_jsx(Toggle, { label: "Google Calendar", checked: Boolean(settingsDraft.allow_calendar), onChange: (v) => updateDraft("allow_calendar", v) }), _jsx(Toggle, { label: "Spotify", checked: Boolean(settingsDraft.allow_spotify), onChange: (v) => updateDraft("allow_spotify", v) }), _jsx(Toggle, { label: "Notion", checked: Boolean(settingsDraft.allow_notion), onChange: (v) => updateDraft("allow_notion", v) }), _jsx(Toggle, { label: "GitHub", checked: Boolean(settingsDraft.allow_github), onChange: (v) => updateDraft("allow_github", v) }), _jsx(Toggle, { label: "Home Assistant", checked: Boolean(settingsDraft.allow_home_assistant), onChange: (v) => updateDraft("allow_home_assistant", v) })] }), _jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 12 }, children: [_jsxs("div", { style: { display: "flex", gap: 8, flexWrap: "wrap" }, children: [_jsxs("div", { style: { flex: "2 1 320px" }, children: [_jsx("label", { style: { display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }, children: "Google Calendar credentials path" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.google_calendar_credentials_path || ""), placeholder: "/path/to/google_credentials.json", onChange: (e) => updateDraft("google_calendar_credentials_path", e.target.value), style: { width: "100%", padding: "8px 12px", fontSize: 13 } })] }), _jsxs("div", { style: { flex: "2 1 280px" }, children: [_jsx("label", { style: { display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }, children: "Google Calendar token path" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.google_calendar_token_path || ""), placeholder: "/path/to/gcal_token.json", onChange: (e) => updateDraft("google_calendar_token_path", e.target.value), style: { width: "100%", padding: "8px 12px", fontSize: 13 } })] }), _jsxs("div", { style: { flex: "1 1 200px" }, children: [_jsx("label", { style: { display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }, children: "Calendar timezone" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.calendar_default_timezone || ""), placeholder: "America/Denver", onChange: (e) => updateDraft("calendar_default_timezone", e.target.value), style: { width: "100%", padding: "8px 12px", fontSize: 13 } })] })] }), _jsxs("div", { style: { display: "flex", gap: 8, flexWrap: "wrap" }, children: [_jsxs("div", { style: { flex: "1 1 220px" }, children: [_jsx("label", { style: { display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }, children: "Spotify client ID" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.spotify_client_id || ""), placeholder: "spotify client id", onChange: (e) => updateDraft("spotify_client_id", e.target.value), style: { width: "100%", padding: "8px 12px", fontSize: 13 } })] }), _jsxs("div", { style: { flex: "1 1 220px" }, children: [_jsx("label", { style: { display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }, children: "Spotify client secret" }), _jsx("input", { type: "password", className: "input-field", value: String(settingsDraft.spotify_client_secret || ""), placeholder: "spotify client secret", onChange: (e) => updateDraft("spotify_client_secret", e.target.value), style: { width: "100%", padding: "8px 12px", fontSize: 13 } })] }), _jsxs("div", { style: { flex: "2 1 280px" }, children: [_jsx("label", { style: { display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }, children: "Spotify redirect URI" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.spotify_redirect_uri || ""), placeholder: "http://127.0.0.1:8888/callback", onChange: (e) => updateDraft("spotify_redirect_uri", e.target.value), style: { width: "100%", padding: "8px 12px", fontSize: 13 } })] }), _jsxs("div", { style: { flex: "2 1 280px" }, children: [_jsx("label", { style: { display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }, children: "Spotify token path" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.spotify_token_path || ""), placeholder: "/path/to/spotify_token.json", onChange: (e) => updateDraft("spotify_token_path", e.target.value), style: { width: "100%", padding: "8px 12px", fontSize: 13 } })] })] }), _jsxs("div", { style: { display: "flex", gap: 8, flexWrap: "wrap" }, children: [_jsxs("div", { style: { flex: "1 1 240px" }, children: [_jsx("label", { style: { display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }, children: "Notion token" }), _jsx("input", { type: "password", className: "input-field", value: String(settingsDraft.notion_token || ""), placeholder: "secret_...", onChange: (e) => updateDraft("notion_token", e.target.value), style: { width: "100%", padding: "8px 12px", fontSize: 13 } })] }), _jsxs("div", { style: { flex: "2 1 280px" }, children: [_jsx("label", { style: { display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }, children: "Notion default database ID" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.notion_default_database_id || ""), placeholder: "database id", onChange: (e) => updateDraft("notion_default_database_id", e.target.value), style: { width: "100%", padding: "8px 12px", fontSize: 13 } })] }), _jsxs("div", { style: { flex: "1 1 240px" }, children: [_jsx("label", { style: { display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }, children: "GitHub token" }), _jsx("input", { type: "password", className: "input-field", value: String(settingsDraft.github_token || ""), placeholder: "ghp_...", onChange: (e) => updateDraft("github_token", e.target.value), style: { width: "100%", padding: "8px 12px", fontSize: 13 } })] }), _jsxs("div", { style: { flex: "2 1 280px" }, children: [_jsx("label", { style: { display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }, children: "GitHub default repo" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.github_default_repo || ""), placeholder: "owner/repo", onChange: (e) => updateDraft("github_default_repo", e.target.value), style: { width: "100%", padding: "8px 12px", fontSize: 13 } })] })] }), _jsxs("div", { style: { display: "flex", gap: 8, flexWrap: "wrap" }, children: [_jsxs("div", { style: { flex: "2 1 280px" }, children: [_jsx("label", { style: { display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }, children: "Home Assistant URL" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.home_assistant_url || ""), placeholder: "http://homeassistant.local:8123", onChange: (e) => updateDraft("home_assistant_url", e.target.value), style: { width: "100%", padding: "8px 12px", fontSize: 13 } })] }), _jsxs("div", { style: { flex: "1 1 240px" }, children: [_jsx("label", { style: { display: "block", fontSize: 12, color: colors.textDim, marginBottom: 4 }, children: "Home Assistant token" }), _jsx("input", { type: "password", className: "input-field", value: String(settingsDraft.home_assistant_token || ""), placeholder: "home assistant token", onChange: (e) => updateDraft("home_assistant_token", e.target.value), style: { width: "100%", padding: "8px 12px", fontSize: 13 } })] })] })] })] }), _jsxs("div", { style: { marginTop: 32, marginBottom: 16, paddingBottom: 8, borderBottom: `1px solid rgba(255,255,255,0.1)` }, children: [_jsx("div", { style: { fontSize: 18, fontWeight: 700, color: colors.text }, children: "Core Engine & Modules" }), _jsx("div", { style: { fontSize: 13, color: colors.textDim }, children: "Configure internal proactive limits, RAG limits, and web search features." })] }), _jsxs("div", { className: "settings-section", style: {
                                                                        background: "rgba(255, 255, 255, 0.02)",
                                                                        border: "1px solid rgba(255, 255, 255, 0.08)",
                                                                        borderRadius: "12px",
                                                                        padding: "20px",
                                                                        marginBottom: "20px"
                                                                    }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }, children: "Heartbeat (Proactive Mode)" }), _jsx("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px", marginBottom: 12 }, children: _jsx(Toggle, { label: "Enable Proactive Heartbeat", checked: Boolean(settingsDraft.heartbeat_enabled), onChange: (v) => updateDraft("heartbeat_enabled", v) }) }), _jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 12 }, children: [_jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsxs("div", { style: { flex: 1 }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Interval (minutes)" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.heartbeat_interval || 30), min: 1, onChange: (e) => updateDraft("heartbeat_interval", parseInt(e.target.value) || 30), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: 2 }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Channels (comma separated)" }), _jsx("input", { type: "text", className: "input-field", value: Array.isArray(settingsDraft.heartbeat_channels) ? settingsDraft.heartbeat_channels.join(",") : "", placeholder: "web,discord,telegram", onChange: (e) => updateDraft("heartbeat_channels", e.target.value
                                                                                                        .split(",")
                                                                                                        .map((x) => x.trim().toLowerCase())
                                                                                                        .filter(Boolean)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] }), _jsxs("div", { children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "System Prompt (Internal thought trigger)" }), _jsx("textarea", { className: "input-field", value: String(settingsDraft.heartbeat_prompt || ""), placeholder: "Review my recent memories and decide if anything needs my attention...", onChange: (e) => updateDraft("heartbeat_prompt", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14, minHeight: "80px", resize: "vertical" } })] })] })] }), _jsxs("div", { className: "settings-section", style: {
                                                                        background: "rgba(255, 255, 255, 0.02)",
                                                                        border: "1px solid rgba(255, 255, 255, 0.08)",
                                                                        borderRadius: "12px",
                                                                        padding: "20px",
                                                                        marginBottom: "20px"
                                                                    }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }, children: "Documents & RAG" }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px", marginBottom: 12 }, children: [_jsx(Toggle, { label: "Enable Document RAG", checked: Boolean(settingsDraft.document_rag_enabled), onChange: (v) => updateDraft("document_rag_enabled", v) }), _jsx(Toggle, { label: "Rerank Results", checked: Boolean(settingsDraft.doc_rerank_enabled), onChange: (v) => updateDraft("doc_rerank_enabled", v) }), _jsx(Toggle, { label: "Graph Expansion", checked: Boolean(settingsDraft.doc_graph_enabled), onChange: (v) => updateDraft("doc_graph_enabled", v) })] }), _jsxs("div", { style: { display: "flex", gap: 10, marginTop: 10, flexWrap: "wrap" }, children: [_jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Upload max (MB)" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.doc_upload_max_mb ?? 25), onChange: (e) => updateDraft("doc_upload_max_mb", Number(e.target.value || 0)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Context chars" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.doc_context_max_chars ?? 2800), onChange: (e) => updateDraft("doc_context_max_chars", Number(e.target.value || 0)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] })] }), _jsxs("div", { className: "settings-section", style: {
                                                                        background: "rgba(255, 255, 255, 0.02)",
                                                                        border: "1px solid rgba(255, 255, 255, 0.08)",
                                                                        borderRadius: "12px",
                                                                        padding: "20px",
                                                                        marginBottom: "20px"
                                                                    }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }, children: "Web Search" }), _jsx("div", { style: { display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }, children: _jsxs("div", { style: { flex: "2 1 340px" }, children: [_jsxs("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: ["Tavily API key", _jsx(RequiredBadge, { issueKey: "tavily_api_key" })] }), _jsxs("div", { style: { display: "flex", gap: 8 }, children: [_jsx("input", { type: "password", className: "input-field", value: String(settingsDraft.tavily_api_key || ""), placeholder: "tvly-...", onChange: (e) => {
                                                                                                    const next = e.target.value;
                                                                                                    updateDraft("tavily_api_key", next);
                                                                                                    setSettingsTests((m) => ({ ...m, tavily: null }));
                                                                                                    setSettingsTestedKeys((m) => {
                                                                                                        const copy = { ...m };
                                                                                                        delete copy.tavily;
                                                                                                        return copy;
                                                                                                    });
                                                                                                }, style: { flex: 1, padding: "10px 14px", fontSize: 14, borderColor: isError("tavily_api_key") ? colors.danger : undefined } }), _jsx("button", { className: "icon-button", style: { padding: "0 12px", fontSize: 12 }, type: "button", onClick: () => runSettingsTest("tavily"), disabled: Boolean(settingsTesting.tavily), children: settingsTesting.tavily ? "..." : "Test" })] }), settingsTests.tavily ? (_jsx("div", { className: "research-snippet", style: { marginTop: 4, color: settingsTests.tavily.ok ? colors.textDim : colors.danger, fontSize: 11 }, children: settingsTests.tavily.message })) : null] }) }), _jsxs("div", { style: { display: "flex", gap: 10, marginTop: 10, flexWrap: "wrap" }, children: [_jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Timeout (s)" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.web_search_timeout ?? 10), onChange: (e) => updateDraft("web_search_timeout", Number(e.target.value || 0)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Tavily depth" }), _jsxs("select", { className: "input-field", value: String(settingsDraft.tavily_search_depth || "advanced"), onChange: (e) => updateDraft("tavily_search_depth", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 }, children: [_jsx("option", { value: "basic", children: "basic" }), _jsx("option", { value: "advanced", children: "advanced" })] })] }), _jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Tavily max results" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.tavily_max_results ?? 8), min: 1, max: 10, onChange: (e) => updateDraft("tavily_max_results", Number(e.target.value || 0)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] }), _jsxs("div", { style: { marginTop: 10 }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Blocked domains" }), _jsx("input", { type: "text", className: "input-field", value: Array.isArray(settingsDraft.web_search_blocked_domains) ? settingsDraft.web_search_blocked_domains.join(",") : "", placeholder: "msn.com,pinterest.com", onChange: (e) => updateDraft("web_search_blocked_domains", e.target.value
                                                                                        .split(",")
                                                                                        .map((x) => x.trim().toLowerCase().replace(/^\./, ""))
                                                                                        .filter(Boolean)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] }), _jsxs("div", { className: "settings-section", style: {
                                                                        background: "rgba(255, 255, 255, 0.02)",
                                                                        border: "1px solid rgba(255, 255, 255, 0.08)",
                                                                        borderRadius: "12px",
                                                                        padding: "20px",
                                                                        marginBottom: "20px"
                                                                    }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }, children: "Memory & Planning" }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px", marginBottom: 12 }, children: [_jsx(Toggle, { label: "Action Plan", checked: Boolean(settingsDraft.action_plan_enabled), onChange: (v) => updateDraft("action_plan_enabled", v) }), _jsx(Toggle, { label: "Action Parser", checked: Boolean(settingsDraft.action_parser_enabled), onChange: (v) => updateDraft("action_parser_enabled", v) }), _jsx(Toggle, { label: "Multi-task Planner", checked: Boolean(settingsDraft.multi_task_planner_enabled), onChange: (v) => updateDraft("multi_task_planner_enabled", v) }), _jsx(Toggle, { label: "Web Reflection / Retry", checked: Boolean(settingsDraft.web_task_reflection_enabled), onChange: (v) => updateDraft("web_task_reflection_enabled", v) }), _jsx(Toggle, { label: "File Memory", checked: Boolean(settingsDraft.file_memory_enabled), onChange: (v) => updateDraft("file_memory_enabled", v) }), _jsx(Toggle, { label: "Memory Flush", checked: Boolean(settingsDraft.memory_flush_enabled), onChange: (v) => updateDraft("memory_flush_enabled", v) }), _jsx(Toggle, { label: "Memory Partitioning", checked: Boolean(settingsDraft.memory_partition_enabled), onChange: (v) => updateDraft("memory_partition_enabled", v) }), _jsx(Toggle, { label: "Memory Importance Auto-save", checked: Boolean(settingsDraft.memory_importance_enabled), onChange: (v) => updateDraft("memory_importance_enabled", v) }), _jsx(Toggle, { label: "Log Raw Memory Conversations", checked: Boolean(settingsDraft.file_memory_log_conversations), onChange: (v) => updateDraft("file_memory_log_conversations", v) })] }), _jsx("div", { style: { display: "flex", gap: 10, marginTop: 10, flexWrap: "wrap" }, children: _jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Web max retries" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.web_task_max_retries ?? 2), min: 0, max: 5, onChange: (e) => updateDraft("web_task_max_retries", Number(e.target.value || 0)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }) }), _jsxs("div", { style: { display: "flex", gap: 10, marginTop: 10, flexWrap: "wrap" }, children: [_jsxs("div", { style: { flex: "2 1 280px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "File memory dir" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.file_memory_dir || ""), onChange: (e) => updateDraft("file_memory_dir", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Summary trigger" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.summary_trigger_turns ?? 18), onChange: (e) => updateDraft("summary_trigger_turns", Number(e.target.value || 0)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Summary keep last turns" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.summary_keep_last_turns ?? 6), onChange: (e) => updateDraft("summary_keep_last_turns", Number(e.target.value || 0)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 220px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "File memory max chars" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.file_memory_max_chars ?? 2000), onChange: (e) => updateDraft("file_memory_max_chars", Number(e.target.value || 0)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] })] }), _jsxs("div", { className: "settings-section", style: {
                                                                        background: "rgba(255, 255, 255, 0.02)",
                                                                        border: "1px solid rgba(255, 255, 255, 0.08)",
                                                                        borderRadius: "12px",
                                                                        padding: "20px",
                                                                        marginBottom: "20px"
                                                                    }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }, children: "Local Provider & Embeddings" }), _jsxs("div", { style: { display: "flex", gap: 10, flexWrap: "wrap" }, children: [_jsxs("div", { style: { flex: "1 1 220px" }, children: [_jsxs("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: ["Local provider", _jsx(RequiredBadge, { issueKey: "local.provider" })] }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft?.local?.provider ?? ""), placeholder: "ollama | lmstudio | localai | llama_cpp | vllm", onChange: (e) => updateDraftSection("local", "provider", e.target.value), style: {
                                                                                                width: "100%",
                                                                                                padding: "10px 14px",
                                                                                                fontSize: 14,
                                                                                                borderColor: isError("local.provider") ? colors.danger : undefined,
                                                                                            } })] }), _jsxs("div", { style: { flex: "2 1 320px" }, children: [_jsxs("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: ["Local base URL", _jsx(RequiredBadge, { issueKey: "local.base_url" })] }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft?.local?.base_url ?? ""), placeholder: "http://localhost:11434", onChange: (e) => updateDraftSection("local", "base_url", e.target.value), style: {
                                                                                                width: "100%",
                                                                                                padding: "10px 14px",
                                                                                                fontSize: 14,
                                                                                                borderColor: isError("local.base_url") ? colors.danger : undefined,
                                                                                            } })] }), _jsxs("div", { style: { flex: "1 1 220px" }, children: [_jsxs("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: ["Local model", _jsx(RequiredBadge, { issueKey: "local.model_name" })] }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft?.local?.model_name ?? ""), placeholder: "llama3", onChange: (e) => updateDraftSection("local", "model_name", e.target.value), style: {
                                                                                                width: "100%",
                                                                                                padding: "10px 14px",
                                                                                                fontSize: 14,
                                                                                                borderColor: isError("local.model_name") ? colors.danger : undefined,
                                                                                            } })] })] }), _jsxs("div", { style: { display: "flex", gap: 10, marginTop: 10, flexWrap: "wrap", alignItems: "center" }, children: [_jsx("button", { className: "icon-button", style: { height: 32, padding: "0 14px", fontSize: 13 }, type: "button", onClick: () => runSettingsTest("local"), disabled: Boolean(settingsTesting.local), children: settingsTesting.local ? "Testing…" : "Test Local (/v1/models)" }), _jsx("button", { className: "icon-button", style: { height: 32, padding: "0 14px", fontSize: 13 }, type: "button", onClick: () => runSettingsTest("ollama"), disabled: Boolean(settingsTesting.ollama), children: settingsTesting.ollama ? "Testing…" : "Test Ollama" }), settingsTests.local ? (_jsxs("div", { className: "research-snippet", style: { color: settingsTests.local.ok ? colors.textDim : colors.danger }, children: ["Local: ", settingsTests.local.message, typeof settingsTests.local.latency_ms === "number" ? ` (${Math.round(settingsTests.local.latency_ms)}ms)` : ""] })) : null, settingsTests.ollama ? (_jsxs("div", { className: "research-snippet", style: { color: settingsTests.ollama.ok ? colors.textDim : colors.danger }, children: ["Ollama: ", settingsTests.ollama.message, typeof settingsTests.ollama.latency_ms === "number" ? ` (${Math.round(settingsTests.ollama.latency_ms)}ms)` : ""] })) : null] }), _jsxs("div", { style: { marginTop: 16, paddingTop: 12, borderTop: "1px solid rgba(255,255,255,0.06)" }, children: [_jsx("div", { style: { fontSize: 11, fontWeight: 600, color: colors.textDim, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 8 }, children: "Embeddings" }), _jsxs("div", { style: { display: "flex", gap: 10, flexWrap: "wrap" }, children: [_jsxs("div", { style: { flex: "1 1 220px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Embedding provider" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft?.embedding?.provider ?? ""), placeholder: "openai | ollama | lmstudio", onChange: (e) => updateDraftSection("embedding", "provider", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 260px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Embedding model" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft?.embedding?.model ?? ""), onChange: (e) => updateDraftSection("embedding", "model", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] })] }), _jsxs("div", { style: { marginTop: 16, paddingTop: 12, borderTop: "1px solid rgba(255,255,255,0.06)" }, children: [_jsx("div", { style: { fontSize: 11, fontWeight: 600, color: colors.textDim, textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: 8 }, children: "Speech" }), _jsxs("div", { style: { display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }, children: [_jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Voice rate (words/min)" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft?.voice?.rate ?? 150), onChange: (e) => updateDraftSection("voice", "rate", Number(e.target.value || 0)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsx("div", { style: { flex: "2 1 300px" }, children: _jsx("div", { className: "research-snippet", style: { fontSize: 11, color: colors.textDim, marginBottom: 4 }, children: "Voice playback and dictation use your browser's built-in speech engine. Only the playback rate is configurable here." }) })] })] })] }), _jsxs("div", { className: "settings-section", style: {
                                                                        background: "rgba(255, 255, 255, 0.02)",
                                                                        border: "1px solid rgba(255, 255, 255, 0.08)",
                                                                        borderRadius: "12px",
                                                                        padding: "20px",
                                                                        marginBottom: "20px"
                                                                    }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }, children: "Automation & Webhooks" }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px", marginBottom: 12 }, children: [_jsx(Toggle, { label: "Enable Cron", checked: Boolean(settingsDraft.cron_enabled), onChange: (v) => updateDraft("cron_enabled", v) }), _jsx(Toggle, { label: "Enable Webhooks", checked: Boolean(settingsDraft.webhook_enabled), onChange: (v) => updateDraft("webhook_enabled", v) })] }), _jsxs("div", { style: { display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }, children: [_jsxs("div", { style: { flex: "1 1 260px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Webhook secret" }), _jsx("input", { type: "password", className: "input-field", value: String(settingsDraft.webhook_secret || ""), onChange: (e) => updateDraft("webhook_secret", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 260px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Webhook secret path" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.webhook_secret_path || ""), onChange: (e) => updateDraft("webhook_secret_path", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 260px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Cron state path" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.cron_state_path || ""), onChange: (e) => updateDraft("cron_state_path", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] })] }), _jsxs("div", { className: "settings-section", style: {
                                                                        background: "rgba(255, 255, 255, 0.02)",
                                                                        border: "1px solid rgba(255, 255, 255, 0.08)",
                                                                        borderRadius: "12px",
                                                                        padding: "20px",
                                                                        marginBottom: "20px"
                                                                    }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }, children: "System & Tracing" }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: "8px 24px", marginBottom: 12 }, children: [_jsx(Toggle, { label: "Enable Trace", checked: Boolean(settingsDraft.trace_enabled), onChange: (v) => updateDraft("trace_enabled", v) }), _jsx(Toggle, { label: "Multi-agent pool", checked: Boolean(settingsDraft.multi_agent_enabled), onChange: (v) => updateDraft("multi_agent_enabled", v) }), _jsx(Toggle, { label: "A2A Protocol", checked: Boolean(settingsDraft.a2a_enabled), onChange: (v) => updateDraft("a2a_enabled", v) }), _jsx(Toggle, { label: "Orchestration", checked: Boolean(settingsDraft.orchestration_enabled), onChange: (v) => updateDraft("orchestration_enabled", v) })] }), _jsxs("div", { style: { display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }, children: [_jsxs("div", { style: { flex: "2 1 280px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Trace path" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.trace_path || ""), onChange: (e) => updateDraft("trace_path", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Default workspace" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.default_workspace || ""), onChange: (e) => updateDraft("default_workspace", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "2 1 260px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Notification channels" }), _jsx("input", { type: "text", className: "input-field", value: Array.isArray(settingsDraft.notification_channels) ? settingsDraft.notification_channels.join(",") : "", placeholder: "web,discord,telegram", onChange: (e) => updateDraft("notification_channels", e.target.value
                                                                                                .split(",")
                                                                                                .map((x) => x.trim().toLowerCase())
                                                                                                .filter(Boolean)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] }), _jsxs("div", { style: { display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }, children: [_jsxs("div", { style: { flex: "2 1 280px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Artifacts dir" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.artifacts_dir || ""), onChange: (e) => updateDraft("artifacts_dir", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "2 1 280px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Skills dir" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.skills_dir || ""), onChange: (e) => updateDraft("skills_dir", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "2 1 280px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Workspaces dir" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.workspaces_dir || ""), onChange: (e) => updateDraft("workspaces_dir", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] }), _jsxs("div", { style: { display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }, children: [_jsxs("div", { style: { flex: "1 1 220px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "A2A agent name" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.a2a_agent_name || ""), onChange: (e) => updateDraft("a2a_agent_name", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "2 1 320px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "A2A description" }), _jsx("input", { type: "text", className: "input-field", value: String(settingsDraft.a2a_agent_description || ""), onChange: (e) => updateDraft("a2a_agent_description", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 240px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "A2A auth key" }), _jsx("input", { type: "password", className: "input-field", value: String(settingsDraft.a2a_auth_key || ""), onChange: (e) => updateDraft("a2a_auth_key", e.target.value), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] }), _jsxs("div", { style: { display: "flex", gap: 10, flexWrap: "wrap", marginTop: 10 }, children: [_jsxs("div", { style: { flex: "2 1 280px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "A2A known agents" }), _jsx("input", { type: "text", className: "input-field", value: Array.isArray(settingsDraft.a2a_known_agents) ? settingsDraft.a2a_known_agents.join(",") : "", placeholder: "https://agent-a.example.com,https://agent-b.example.com", onChange: (e) => updateDraft("a2a_known_agents", e.target.value
                                                                                                .split(",")
                                                                                                .map((x) => x.trim())
                                                                                                .filter(Boolean)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Orchestration max subtasks" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.orchestration_max_subtasks ?? 5), min: 1, onChange: (e) => updateDraft("orchestration_max_subtasks", Number(e.target.value || 0)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] }), _jsxs("div", { style: { flex: "1 1 180px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Orchestration timeout (s)" }), _jsx("input", { type: "number", className: "input-field", value: Number(settingsDraft.orchestration_timeout ?? 120), min: 1, onChange: (e) => updateDraft("orchestration_timeout", Number(e.target.value || 0)), style: { width: "100%", padding: "10px 14px", fontSize: 14 } })] })] })] }), _jsxs("div", { className: "settings-section", style: {
                                                                        background: "rgba(255, 255, 255, 0.02)",
                                                                        border: "1px solid rgba(255, 255, 255, 0.08)",
                                                                        borderRadius: "12px",
                                                                        padding: "20px",
                                                                        marginBottom: "20px"
                                                                    }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, color: colors.accent, textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 12 }, children: "Advanced Runtime JSON" }), _jsxs("div", { className: "research-snippet", style: { marginBottom: 12 }, children: ["Full live settings visibility. Effective settings include `.env` + runtime overrides. Runtime overrides are what get written to ", _jsx("code", { children: "apps/backend/data/settings.json" }), "."] }), _jsxs("div", { style: { display: "flex", gap: 10, flexWrap: "wrap" }, children: [_jsxs("div", { style: { flex: "1 1 420px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Effective settings" }), _jsx("textarea", { className: "input-field", readOnly: true, value: JSON.stringify(runtimeSettings || {}, null, 2), style: { width: "100%", minHeight: 240, padding: "10px 14px", fontSize: 12, fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace", resize: "vertical" } })] }), _jsxs("div", { style: { flex: "1 1 420px" }, children: [_jsx("label", { style: { display: "block", fontSize: 13, color: colors.textDim, marginBottom: 4 }, children: "Runtime overrides" }), _jsx("textarea", { className: "input-field", readOnly: true, value: JSON.stringify(runtimeOverrides || {}, null, 2), style: { width: "100%", minHeight: 240, padding: "10px 14px", fontSize: 12, fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace", resize: "vertical" } })] })] })] }), _jsxs("div", { style: { display: "flex", gap: 10 }, children: [_jsx("button", { className: "icon-button", style: { padding: "8px 16px", fontSize: 14 }, type: "button", onClick: saveSettings, disabled: settingsSaving, children: settingsSaving ? "Saving…" : "Save Settings" }), _jsx("button", { className: "icon-button", style: { padding: "8px 16px", fontSize: 14 }, type: "button", onClick: refreshSettings, disabled: settingsLoading || settingsSaving, children: "Reload" })] })] })) : null] }) }) })), leftTab === "capabilities" && (_jsx(_Fragment, { children: _jsx("div", { className: "research-scroll", children: _jsxs("div", { className: "research-card", children: [_jsx("div", { className: "research-title", children: "Capabilities & Permissions" }), _jsx("div", { className: "research-snippet", style: { marginBottom: 12 }, children: "View available tools, loaded skills, pipeline plugins, and what permissions they require." }), _jsx("button", { className: "icon-button", style: { padding: "8px 16px", fontSize: 13, marginBottom: 16 }, type: "button", onClick: async () => {
                                                                try {
                                                                    const res = await fetch(`${apiBase}/capabilities?thread_id=${activeThreadId}`);
                                                                    const data = await res.json();
                                                                    setCapabilitiesData(data);
                                                                }
                                                                catch (e) {
                                                                    console.error("Failed to fetch capabilities:", e);
                                                                }
                                                            }, children: "Refresh Capabilities" }), capabilitiesData && (_jsxs(_Fragment, { children: [_jsxs("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }, children: [_jsxs("div", { style: { background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)" }, children: [_jsx("div", { style: { fontSize: 11, color: colors.textDim, marginBottom: 4 }, children: "PROVIDER" }), _jsx("div", { style: { fontSize: 14, fontWeight: 600 }, children: capabilitiesData.provider || "Unknown" })] }), _jsxs("div", { style: { background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)" }, children: [_jsx("div", { style: { fontSize: 11, color: colors.textDim, marginBottom: 4 }, children: "WORKSPACE" }), _jsx("div", { style: { fontSize: 14, fontWeight: 600 }, children: capabilitiesData.workspace?.name || capabilitiesData.workspace?.id || "Default" })] })] }), _jsxs("div", { style: { background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)", marginBottom: 16 }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, marginBottom: 8 }, children: "Feature Flags" }), _jsx("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 6 }, children: Object.entries(capabilitiesData.features || {}).map(([key, value]) => (_jsxs("div", { style: { fontSize: 11, display: "flex", alignItems: "center", gap: 4 }, children: [_jsx("span", { style: { color: value ? "#22c55e" : colors.textDim }, children: value ? "✓" : "○" }), _jsx("span", { style: { color: colors.textDim }, children: key.replace(/_/g, " ") })] }, key))) })] }), _jsxs("div", { style: { background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", padding: 12, borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)", marginBottom: 16 }, children: [_jsx("div", { style: { fontSize: 12, fontWeight: 600, marginBottom: 8 }, children: "Loaded Skills & Plugins" }), _jsx("div", { style: { display: "flex", flexWrap: "wrap", gap: 6 }, children: (capabilitiesData.skills || []).length > 0 ? ((capabilitiesData.skills || []).map((skill) => (_jsxs("div", { style: { fontSize: 11, padding: "4px 10px", borderRadius: 6, background: colors.panel2, border: `1px solid ${colors.line}`, display: "flex", alignItems: "center", gap: 6 }, children: [_jsx("span", { children: skill.name || skill.id }), skill.has_tools && _jsx("span", { style: { fontSize: 9, padding: "1px 4px", borderRadius: 3, background: "rgba(59,130,246,0.15)", color: "#3b82f6", fontWeight: 600 }, children: "TOOL" }), skill.has_plugin && _jsx("span", { style: { fontSize: 9, padding: "1px 4px", borderRadius: 3, background: "rgba(168,85,247,0.15)", color: "#a855f7", fontWeight: 600 }, children: "PLUGIN" })] }, skill.id || skill.name)))) : (_jsx("div", { style: { fontSize: 11, color: colors.textDim }, children: "No external skills or plugins are currently loaded." })) })] }), _jsxs("div", { style: { fontSize: 12, fontWeight: 600, marginBottom: 8 }, children: ["Tools (", (capabilitiesData.tools?.items || []).length, typeof capabilitiesData.tools?.count === "number" ? ` of ${capabilitiesData.tools.count}` : "", ")"] }), _jsx("div", { style: { display: "flex", flexDirection: "column", gap: 8 }, children: (capabilitiesData.tools?.items || []).map((tool) => (_jsxs("div", { style: {
                                                                            background: colors.panel2,
                                                                            padding: 10,
                                                                            borderRadius: 6,
                                                                            border: `1px solid ${tool.allowed ? colors.line : colors.danger}`,
                                                                            opacity: tool.allowed ? 1 : 0.6,
                                                                        }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }, children: [_jsx("span", { style: { fontSize: 13, fontWeight: 600, fontFamily: "monospace" }, children: tool.name }), _jsxs("div", { style: { display: "flex", gap: 6, alignItems: "center" }, children: [_jsx("span", { style: {
                                                                                                    fontSize: 10,
                                                                                                    padding: "2px 6px",
                                                                                                    borderRadius: 4,
                                                                                                    background: tool.risk_level === "safe" ? "#22c55e22" : tool.risk_level === "moderate" ? "#f59e0b22" : "#ef444422",
                                                                                                    color: tool.risk_level === "safe" ? "#22c55e" : tool.risk_level === "moderate" ? "#f59e0b" : "#ef4444",
                                                                                                    fontWeight: 600,
                                                                                                    textTransform: "uppercase",
                                                                                                }, children: tool.risk_level || "safe" }), tool.requires_confirmation && (_jsx("span", { style: {
                                                                                                    fontSize: 10,
                                                                                                    padding: "2px 6px",
                                                                                                    borderRadius: 4,
                                                                                                    background: "#3b82f622",
                                                                                                    color: "#3b82f6",
                                                                                                    fontWeight: 600,
                                                                                                }, children: "CONFIRM" })), _jsx("span", { style: {
                                                                                                    fontSize: 11,
                                                                                                    fontWeight: 600,
                                                                                                    color: tool.allowed ? "#22c55e" : colors.danger,
                                                                                                }, children: tool.allowed ? "✓" : "✗" })] })] }), !tool.allowed && tool.blocked_reason && (_jsx("div", { style: { fontSize: 11, color: colors.danger, marginBottom: 4 }, children: tool.blocked_reason })), tool.policy_flags && tool.policy_flags.length > 0 && (_jsxs("div", { style: { fontSize: 10, color: colors.textDim }, children: ["Requires: ", tool.policy_flags.join(", ")] }))] }, tool.name))) })] }))] }) }) })), leftTab === "approvals" && (_jsxs(_Fragment, { children: [_jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }, children: [_jsx("button", { className: "icon-button", style: { height: 32, padding: "0 12px", fontSize: 14, flex: 1 }, onClick: async () => {
                                                                await refreshThreadState();
                                                                await refreshPendingApproval();
                                                                await refreshApprovals();
                                                            }, type: "button", children: "Refresh" }), _jsx("button", { className: "icon-button", style: { height: 32, padding: "0 12px", fontSize: 14, flex: 1 }, onClick: () => setLeftTab("executions"), type: "button", children: "View Executions" })] }), _jsxs("div", { style: { padding: "12px 14px", marginBottom: 12, borderRadius: 12, background: "linear-gradient(135deg, rgba(59,130,246,0.08), rgba(59,130,246,0.02))", border: "1px solid rgba(59,130,246,0.2)" }, children: [_jsx("div", { style: { fontSize: 11, color: "#60a5fa", fontWeight: 600, letterSpacing: "0.04em", marginBottom: 6 }, children: "THREAD CONTROL PLANE" }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }, children: [_jsxs("div", { children: [_jsx("div", { style: { fontSize: 10, color: colors.textDim }, children: "Thread" }), _jsx("div", { style: { fontSize: 13, fontWeight: 600 }, children: threadState?.thread_id || activeThreadId || "—" })] }), _jsxs("div", { children: [_jsx("div", { style: { fontSize: 10, color: colors.textDim }, children: "Workspace" }), _jsx("div", { style: { fontSize: 13, fontWeight: 600 }, children: threadState?.workspace_id || workspaceMode || "default" })] }), _jsxs("div", { children: [_jsx("div", { style: { fontSize: 10, color: colors.textDim }, children: "Project" }), _jsx("div", { style: { fontSize: 13, fontWeight: 600 }, children: threadState?.active_project_id || activeProjectId || "none" })] }), _jsxs("div", { children: [_jsx("div", { style: { fontSize: 10, color: colors.textDim }, children: "Provider" }), _jsx("div", { style: { fontSize: 13, fontWeight: 600 }, children: threadState?.runtime_provider || providerDraft.provider || providerInfo?.provider || "unknown" })] })] })] }), pendingApproval?.has_pending && pendingApproval.action ? (_jsx("div", { style: { marginBottom: 14 }, children: _jsx(ConfirmationCard, { action: { tool: pendingApproval.action.tool, kwargs: pendingApproval.action.kwargs }, riskLevel: pendingApproval.risk_level || pendingApproval.action.risk_level, riskColor: pendingApproval.risk_color || undefined, policyFlags: pendingApproval.policy_flags || pendingApproval.action.policy_flags, sessionPermissions: pendingApproval.session_permissions || pendingApproval.action.session_permissions, dryRunAvailable: Boolean(pendingApproval.dry_run_available), onConfirm: () => sendText("confirm"), onCancel: () => sendText("cancel") }) })) : (_jsxs("div", { className: "research-card", style: { marginBottom: 12 }, children: [_jsx("div", { className: "research-title", children: "No active approval" }), _jsx("div", { className: "research-snippet", children: "This thread currently has no pending action waiting for confirmation." })] })), _jsx("div", { className: "research-scroll", children: approvalsLoading ? (_jsx("div", { className: "research-card", children: _jsx("div", { className: "research-snippet", children: "Loading approvals\u2026" }) })) : approvals.length ? (approvals.map((approval) => (_jsxs("div", { className: "research-card", style: { border: approval.status === "pending" ? "1px solid rgba(245,158,11,0.35)" : undefined }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginBottom: 8 }, children: [_jsx("div", { style: { fontSize: 14, fontWeight: 600, fontFamily: "monospace" }, children: approval.tool }), _jsx("span", { style: { fontSize: 10, padding: "2px 8px", borderRadius: 999, background: approval.status === "pending" ? "rgba(245,158,11,0.12)" : approval.status === "approved" || approval.status === "auto_approved" ? "rgba(34,197,94,0.12)" : "rgba(239,68,68,0.12)", color: approval.status === "pending" ? "#f59e0b" : approval.status === "approved" || approval.status === "auto_approved" ? "#22c55e" : "#ef4444", fontWeight: 700, textTransform: "uppercase" }, children: approval.status })] }), _jsx("div", { style: { fontSize: 12, color: colors.textDim, marginBottom: 6 }, children: approval.preview || approval.summary || approval.original_input || "Pending action" }), _jsxs("div", { style: { fontSize: 11, color: colors.textDim, marginBottom: 6 }, children: ["Risk: ", approval.risk_level, " \u00B7 Execution: ", approval.execution_id || "—"] }), approval.policy_flags?.length ? (_jsxs("div", { style: { fontSize: 11, color: colors.textDim, marginBottom: 6 }, children: ["Requires: ", approval.policy_flags.join(", ")] })) : null, approval.outcome_summary ? (_jsx("div", { style: { fontSize: 11, color: colors.textDim }, children: approval.outcome_summary })) : null] }, approval.id)))) : (_jsx("div", { className: "research-card", children: _jsx("div", { className: "research-snippet", children: "No approval history for this thread yet." }) })) })] })), leftTab === "executions" && (_jsxs(_Fragment, { children: [_jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }, children: [_jsx("button", { className: "icon-button", style: { height: 32, padding: "0 12px", fontSize: 14, flex: 1 }, onClick: async () => {
                                                                await refreshThreadState();
                                                                await refreshExecutions();
                                                                if (latestTraceId)
                                                                    await loadTrace(latestTraceId);
                                                            }, type: "button", children: "Refresh" }), _jsx("button", { className: "icon-button", style: { height: 32, padding: "0 12px", fontSize: 14, flex: 1 }, onClick: () => latestTraceId && loadTrace(latestTraceId), disabled: !latestTraceId, type: "button", children: "Load Latest Trace" })] }), _jsxs("div", { style: { padding: "12px 14px", marginBottom: 12, borderRadius: 12, background: "linear-gradient(135deg, rgba(168,85,247,0.08), rgba(168,85,247,0.02))", border: "1px solid rgba(168,85,247,0.2)" }, children: [_jsx("div", { style: { fontSize: 11, color: "#c084fc", fontWeight: 600, letterSpacing: "0.04em", marginBottom: 6 }, children: "EXECUTION STATE" }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 10 }, children: [_jsxs("div", { children: [_jsx("div", { style: { fontSize: 10, color: colors.textDim }, children: "Latest execution" }), _jsx("div", { style: { fontSize: 13, fontWeight: 600 }, children: latestExecutionId || threadState?.last_execution_id || "—" })] }), _jsxs("div", { children: [_jsx("div", { style: { fontSize: 10, color: colors.textDim }, children: "Latest trace" }), _jsx("div", { style: { fontSize: 13, fontWeight: 600 }, children: latestTraceId || threadState?.last_trace_id || "—" })] }), _jsxs("div", { children: [_jsx("div", { style: { fontSize: 10, color: colors.textDim }, children: "Pending approval" }), _jsx("div", { style: { fontSize: 13, fontWeight: 600 }, children: threadState?.pending_approval_id || "none" })] })] })] }), _jsxs("div", { className: "research-scroll", children: [executionsLoading ? (_jsx("div", { className: "research-card", children: _jsx("div", { className: "research-snippet", children: "Loading executions\u2026" }) })) : executions.length ? (executions.map((execution) => (_jsxs("div", { className: "research-card", style: { border: execution.id === latestExecutionId ? "1px solid rgba(168,85,247,0.4)" : undefined }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginBottom: 8 }, children: [_jsx("div", { style: { fontSize: 13, fontWeight: 700 }, children: execution.kind.toUpperCase() }), _jsx("span", { style: { fontSize: 10, padding: "2px 8px", borderRadius: 999, background: execution.status === "completed" ? "rgba(34,197,94,0.12)" : execution.status === "pending_approval" ? "rgba(245,158,11,0.12)" : execution.status === "failed" ? "rgba(239,68,68,0.12)" : "rgba(255,255,255,0.08)", color: execution.status === "completed" ? "#22c55e" : execution.status === "pending_approval" ? "#f59e0b" : execution.status === "failed" ? "#ef4444" : colors.text, fontWeight: 700, textTransform: "uppercase" }, children: execution.status })] }), _jsx("div", { style: { fontSize: 12, fontWeight: 600, marginBottom: 6 }, children: execution.query || "(no query)" }), execution.response_preview ? _jsx("div", { style: { fontSize: 11, color: colors.textDim, marginBottom: 6 }, children: execution.response_preview }) : null, _jsxs("div", { style: { fontSize: 11, color: colors.textDim, marginBottom: 6 }, children: ["Tools: ", execution.tools_used?.length ? execution.tools_used.join(", ") : "none"] }), _jsxs("div", { style: { fontSize: 11, color: colors.textDim, marginBottom: 8 }, children: ["Approvals: ", execution.approvals?.length || 0, " \u00B7 Provider: ", execution.runtime_provider || "unknown"] }), execution.trace_id ? (_jsx("button", { className: "icon-button", style: { height: 28, padding: "0 10px", fontSize: 12 }, type: "button", onClick: () => loadTrace(String(execution.trace_id || "")), children: "Open Trace" })) : null] }, execution.id)))) : (_jsx("div", { className: "research-card", children: _jsx("div", { className: "research-snippet", children: "No executions recorded for this thread yet." }) })), _jsxs("div", { className: "research-card", style: { marginTop: 8 }, children: [_jsx("div", { className: "research-title", children: "Trace Viewer" }), _jsxs("div", { className: "research-snippet", style: { marginBottom: 8 }, children: ["Trace ID: ", selectedTraceId || latestTraceId || "none loaded"] }), _jsx("div", { style: { border: `1px solid ${colors.line}`, borderRadius: 12, background: "rgba(0,0,0,0.22)", padding: 12, maxHeight: 320, overflow: "auto", fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace", fontSize: 11, whiteSpace: "pre-wrap", color: colors.textDim }, children: traceLoading ? "Loading trace…" : selectedTrace ? JSON.stringify(selectedTrace, null, 2) : "Select an execution trace to inspect persisted tool and latency details." })] })] })] })), leftTab === "projects" && (_jsxs(_Fragment, { children: [_jsxs("div", { style: { padding: "10px 14px", marginBottom: 4, borderRadius: 10, background: "linear-gradient(135deg, rgba(34,197,94,0.08), rgba(34,197,94,0.02))", border: "1px solid rgba(34,197,94,0.2)" }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 8 }, children: [_jsx("span", { style: { width: 8, height: 8, borderRadius: "50%", background: "#22c55e", boxShadow: "0 0 6px rgba(34,197,94,0.5)" } }), _jsx("span", { style: { fontSize: 11, color: "#22c55e", fontWeight: 600, letterSpacing: "0.03em" }, children: "CONNECTED TO PIPELINE" })] }), _jsx("div", { style: { fontSize: 10, color: colors.textDim, marginTop: 4 }, children: "Active project context is injected into every AI response via the system prompt." })] }), _jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center", marginBottom: -12, flexWrap: "wrap" }, children: [_jsx("button", { className: "icon-button", style: { height: 32, padding: "0 12px", fontSize: 14, flex: 1 }, onClick: async () => {
                                                                await refreshProjects();
                                                                await refreshThreadState();
                                                            }, type: "button", children: "Refresh" }), _jsx("button", { className: "icon-button", style: { height: 32, padding: "0 12px", fontSize: 14, flex: 1 }, onClick: async () => {
                                                                const name = prompt("Project name:");
                                                                if (!name)
                                                                    return;
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
                                                                }
                                                                catch (e) {
                                                                    console.error("Failed to create project:", e);
                                                                }
                                                            }, type: "button", children: "New Project" })] }), activeProjectId && (_jsxs("div", { style: { marginTop: 8, padding: "12px 16px", background: "linear-gradient(135deg, rgba(255,255,255,0.06), rgba(255,255,255,0.01))", backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 12, marginBottom: 8, boxShadow: "0 4px 16px -4px rgba(0,0,0,0.2)" }, children: [_jsx("div", { style: { fontSize: 12, color: colors.textDim, marginBottom: 4 }, children: "Active Project" }), _jsx("div", { style: { fontSize: 14, fontWeight: 600 }, children: projects.find(p => p.id === activeProjectId)?.name || "Unknown" }), _jsx("button", { className: "icon-button", style: { height: 24, padding: "0 8px", fontSize: 11, marginTop: 6 }, type: "button", onClick: async () => {
                                                                setActiveProjectId("");
                                                                localStorage.removeItem("echospeak.active_project_id");
                                                                try {
                                                                    await fetch(`${apiBase}/projects/deactivate?thread_id=${encodeURIComponent(activeThreadId)}`, { method: "POST" });
                                                                    await refreshThreadState();
                                                                }
                                                                catch (e) { }
                                                            }, children: "Deactivate" })] })), _jsx("div", { className: "research-scroll", children: projectsLoading ? (_jsx("div", { className: "research-card", children: _jsx("div", { className: "research-snippet", children: "Loading projects\u2026" }) })) : projects.length ? (projects.map((project) => (_jsxs("div", { className: "research-card", style: {
                                                            border: activeProjectId === project.id ? `1px solid ${colors.accent}` : undefined,
                                                            cursor: "pointer",
                                                        }, onClick: async () => {
                                                            setActiveProjectId(project.id);
                                                            localStorage.setItem("echospeak.active_project_id", project.id);
                                                            try {
                                                                await fetch(`${apiBase}/projects/${project.id}/activate?thread_id=${encodeURIComponent(activeThreadId)}`, { method: "POST" });
                                                                await refreshThreadState();
                                                            }
                                                            catch (e) { }
                                                        }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }, children: [_jsx("div", { style: { fontSize: 15, fontWeight: 600 }, children: project.name }), _jsxs("div", { style: { display: "flex", gap: 6 }, children: [activeProjectId === project.id && (_jsx("span", { style: { fontSize: 10, padding: "2px 6px", borderRadius: 4, background: colors.accent + "22", color: colors.accent }, children: "ACTIVE" })), _jsx("button", { className: "icon-button", style: { height: 28, padding: "0 10px", fontSize: 12 }, type: "button", onClick: async (e) => {
                                                                                    e.stopPropagation();
                                                                                    if (!confirm("Delete this project?"))
                                                                                        return;
                                                                                    await fetch(`${apiBase}/projects/${project.id}`, { method: "DELETE" });
                                                                                    setProjects(projects.filter(p => p.id !== project.id));
                                                                                    if (activeProjectId === project.id) {
                                                                                        setActiveProjectId("");
                                                                                        localStorage.removeItem("echospeak.active_project_id");
                                                                                    }
                                                                                }, children: "Delete" })] })] }), project.description && (_jsx("div", { style: { fontSize: 12, color: colors.textDim, marginBottom: 6 }, children: project.description })), project.context_prompt && (_jsxs("div", { style: { fontSize: 11, color: colors.textDim, fontStyle: "italic", marginBottom: 4 }, children: ["Context: ", project.context_prompt.slice(0, 100), project.context_prompt.length > 100 ? "…" : ""] })), project.tags && project.tags.length > 0 && (_jsx("div", { style: { display: "flex", gap: 4, flexWrap: "wrap" }, children: project.tags.map((tag, i) => (_jsx("span", { style: { fontSize: 10, padding: "2px 8px", borderRadius: 999, background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.1)", color: colors.text }, children: tag }, i))) }))] }, project.id)))) : (_jsx("div", { className: "research-card", children: _jsx("div", { className: "research-snippet", children: "No projects yet. Create one to organize your memories and context." }) })) })] })), leftTab === "routines" && (_jsxs(_Fragment, { children: [_jsxs("div", { style: { padding: "10px 14px", marginBottom: 4, borderRadius: 10, background: "linear-gradient(135deg, rgba(34,197,94,0.08), rgba(34,197,94,0.02))", border: "1px solid rgba(34,197,94,0.2)" }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 8 }, children: [_jsx("span", { style: { width: 8, height: 8, borderRadius: "50%", background: "#22c55e", boxShadow: "0 0 6px rgba(34,197,94,0.5)", animation: "pulse 2s infinite" } }), _jsx("span", { style: { fontSize: 11, color: "#22c55e", fontWeight: 600, letterSpacing: "0.03em" }, children: "SCHEDULER ACTIVE \u00B7 CONNECTED TO PIPELINE" })] }), _jsx("div", { style: { fontSize: 10, color: colors.textDim, marginTop: 4 }, children: "Routines fire through process_query() \u2014 full tool access, safety gating, and memory recording." })] }), _jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center", marginBottom: -12, flexWrap: "wrap" }, children: [_jsx("button", { className: "icon-button", style: { height: 32, padding: "0 12px", fontSize: 14, flex: 1 }, onClick: async () => {
                                                                setRoutinesLoading(true);
                                                                try {
                                                                    const res = await fetch(`${apiBase}/routines`);
                                                                    const data = await res.json();
                                                                    setRoutines(data.items || []);
                                                                }
                                                                catch (e) {
                                                                    console.error("Failed to load routines:", e);
                                                                }
                                                                setRoutinesLoading(false);
                                                            }, type: "button", children: "Refresh" }), _jsx("button", { className: "icon-button", style: { height: 32, padding: "0 12px", fontSize: 14, flex: 1 }, onClick: async () => {
                                                                const name = prompt("Routine name:");
                                                                if (!name)
                                                                    return;
                                                                const triggerType = prompt("Trigger type (schedule/webhook/manual):", "manual") || "manual";
                                                                let schedule = null;
                                                                let webhookPath = null;
                                                                if (triggerType === "schedule") {
                                                                    schedule = prompt("Cron schedule (e.g., '0 9 * * *' for daily at 9am):");
                                                                }
                                                                else if (triggerType === "webhook") {
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
                                                                }
                                                                catch (e) {
                                                                    console.error("Failed to create routine:", e);
                                                                }
                                                            }, type: "button", children: "New Routine" })] }), _jsx("div", { className: "research-scroll", children: routinesLoading ? (_jsx("div", { className: "research-card", children: _jsx("div", { className: "research-snippet", children: "Loading routines\u2026" }) })) : routines.length ? (routines.map((routine) => (_jsxs("div", { className: "research-card", children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }, children: [_jsx("div", { style: { fontSize: 15, fontWeight: 600 }, children: routine.name }), _jsxs("div", { style: { display: "flex", gap: 6, alignItems: "center" }, children: [_jsx("span", { style: {
                                                                                    fontSize: 10,
                                                                                    padding: "2px 6px",
                                                                                    borderRadius: 4,
                                                                                    background: routine.enabled ? "rgba(34,197,94,0.12)" : "rgba(107,114,128,0.12)",
                                                                                    color: routine.enabled ? "#22c55e" : colors.textDim,
                                                                                }, children: routine.enabled ? "ENABLED" : "DISABLED" }), _jsx("span", { style: {
                                                                                    fontSize: 10,
                                                                                    padding: "2px 6px",
                                                                                    borderRadius: 4,
                                                                                    background: colors.panel2,
                                                                                    color: colors.textDim,
                                                                                }, children: routine.trigger_type.toUpperCase() })] })] }), routine.description && (_jsx("div", { style: { fontSize: 12, color: colors.textDim, marginBottom: 6 }, children: routine.description })), _jsxs("div", { style: { fontSize: 11, color: colors.textDim, marginBottom: 4 }, children: [_jsx("strong", { children: "Type:" }), " ", routine.action_type, " | ", _jsx("strong", { children: "Runs:" }), " ", routine.run_count] }), routine.schedule && (_jsxs("div", { style: { fontSize: 11, color: colors.textDim, marginBottom: 4 }, children: [_jsx("strong", { children: "Schedule:" }), " ", routine.schedule] })), routine.webhook_path && (_jsxs("div", { style: { fontSize: 11, color: colors.textDim, marginBottom: 4, fontFamily: "monospace" }, children: [_jsx("strong", { children: "Webhook:" }), " POST /webhooks", routine.webhook_path] })), routine.next_run && (_jsxs("div", { style: { fontSize: 11, color: colors.accent, marginBottom: 4 }, children: [_jsx("strong", { children: "Next run:" }), " ", new Date(routine.next_run).toLocaleString()] })), routine.last_run && (_jsxs("div", { style: { fontSize: 11, color: colors.textDim, marginBottom: 4 }, children: [_jsx("strong", { children: "Last run:" }), " ", new Date(routine.last_run).toLocaleString()] })), _jsxs("div", { style: { display: "flex", gap: 6, marginTop: 8 }, children: [_jsx("button", { className: "icon-button", style: { height: 28, padding: "0 10px", fontSize: 12 }, type: "button", onClick: async () => {
                                                                            await fetch(`${apiBase}/routines/${routine.id}/run`, { method: "POST" });
                                                                            // Refresh to update run count
                                                                            const res = await fetch(`${apiBase}/routines`);
                                                                            const data = await res.json();
                                                                            setRoutines(data.items || []);
                                                                        }, children: "Run Now" }), _jsx("button", { className: "icon-button", style: { height: 28, padding: "0 10px", fontSize: 12 }, type: "button", onClick: async () => {
                                                                            await fetch(`${apiBase}/routines/${routine.id}`, {
                                                                                method: "PUT",
                                                                                headers: { "Content-Type": "application/json" },
                                                                                body: JSON.stringify({ enabled: !routine.enabled }),
                                                                            });
                                                                            setRoutines(routines.map(r => r.id === routine.id ? { ...r, enabled: !r.enabled } : r));
                                                                        }, children: routine.enabled ? "Disable" : "Enable" }), _jsx("button", { className: "icon-button", style: { height: 28, padding: "0 10px", fontSize: 12 }, type: "button", onClick: async () => {
                                                                            if (!confirm("Delete this routine?"))
                                                                                return;
                                                                            await fetch(`${apiBase}/routines/${routine.id}`, { method: "DELETE" });
                                                                            setRoutines(routines.filter(r => r.id !== routine.id));
                                                                        }, children: "Delete" })] })] }, routine.id)))) : (_jsx("div", { className: "research-card", children: _jsx("div", { className: "research-snippet", children: "No routines yet. Create one to automate actions on a schedule or via webhook." }) })) })] })), leftTab === "soul" && (_jsx(_Fragment, { children: _jsx("div", { className: "research-scroll", children: _jsxs("div", { className: "research-card", children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }, children: [_jsx("h3", { style: { margin: 0, fontSize: 16, color: colors.text }, children: "Agent Soul" }), _jsxs("div", { style: { display: "flex", gap: 8, alignItems: "center" }, children: [soulEnabled ? (_jsx("span", { style: { fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(34,197,94,0.12)", color: "#22c55e" }, children: "ENABLED" })) : (_jsx("span", { style: { fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(107,114,128,0.12)", color: colors.textDim }, children: "DISABLED" })), soulSavedAt && (_jsxs("span", { style: { fontSize: 10, color: colors.textDim }, children: ["Saved ", new Date(soulSavedAt).toLocaleTimeString()] }))] })] }), _jsx("div", { style: { fontSize: 12, color: colors.textDim, marginBottom: 12 }, children: "The soul defines the agent's core identity, values, communication style, and boundaries. Changes apply to new conversations." }), soulLoading ? (_jsx("div", { style: { color: colors.textDim, padding: 20, textAlign: "center" }, children: "Loading..." })) : soulError ? (_jsx("div", { style: { color: "#ef4444", padding: 12, background: "rgba(239,68,68,0.1)", borderRadius: 6, marginBottom: 12 }, children: soulError })) : (_jsxs(_Fragment, { children: [_jsx("div", { style: { marginBottom: 12 }, children: _jsxs("div", { style: { fontSize: 11, color: colors.textDim, marginBottom: 4 }, children: ["Path: ", _jsx("code", { style: { background: colors.panel2, padding: "2px 6px", borderRadius: 4 }, children: soulPath }), " | ", "Max chars: ", _jsx("code", { style: { background: colors.panel2, padding: "2px 6px", borderRadius: 4 }, children: soulMaxChars }), " | ", "Characters: ", _jsx("code", { style: { background: colors.panel2, padding: "2px 6px", borderRadius: 4 }, children: soulContent.length })] }) }), _jsx("textarea", { value: soulContent, onChange: (e) => setSoulContent(e.target.value), placeholder: "# EchoSpeak Soul\n\n## Identity\nI am EchoSpeak, a personal AI assistant...\n\n## Communication Style\n- Direct and concise\n- No corporate pleasantries\n\n## Values\n- Honesty over politeness\n- Getting things done\n\n## Boundaries\n- I won't reveal API keys\n- I won't sugarcoat technical realities", style: {
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
                                                                    } }), _jsxs("div", { style: { display: "flex", gap: 8, marginTop: 12 }, children: [_jsx("button", { className: "icon-button", style: { height: 32, padding: "0 16px", fontSize: 13 }, type: "button", onClick: saveSoul, disabled: soulSaving, children: soulSaving ? "Saving..." : "Save Soul" }), _jsx("button", { className: "icon-button", style: { height: 32, padding: "0 16px", fontSize: 13 }, type: "button", onClick: refreshSoul, children: "Reset" })] }), soulContent.length > soulMaxChars && (_jsxs("div", { style: { color: "#ef4444", fontSize: 11, marginTop: 8 }, children: ["\u26A0\uFE0F Content exceeds max chars limit (", soulContent.length, " / ", soulMaxChars, ")"] }))] }))] }) }) })), leftTab === "services" && (_jsx("div", { className: "research-scroll", children: _jsxs("div", { className: "research-card", children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }, children: [_jsx("h3", { style: { margin: 0, fontSize: 16, color: colors.text }, children: "\u26A1 System Services" }), _jsx("button", { className: "icon-button", onClick: refreshServices, disabled: servicesLoading, style: { fontSize: 12, padding: "4px 10px", height: "auto" }, children: servicesLoading ? "Refreshing..." : "Refresh" })] }), _jsx("div", { style: { fontSize: 12, color: colors.textDim, marginBottom: 20 }, children: "Monitor and control background services like Heartbeat, Telegram, and the Discord bot's live activity bridge." }), _jsxs("div", { style: { background: colors.panel2, borderRadius: 8, padding: 16, border: `1px solid ${colors.line}`, marginBottom: 16 }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 10 }, children: [_jsx("span", { style: { fontSize: 18 }, children: "\uD83D\uDC93" }), _jsx("span", { style: { fontSize: 14, fontWeight: 600, color: colors.text }, children: "Heartbeat Scheduler" })] }), _jsx("div", { style: { display: "flex", alignItems: "center", gap: 8 }, children: servicesHeartbeatStatus?.running ? (_jsxs("span", { style: { fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(34,197,94,0.12)", color: "#22c55e", display: "flex", alignItems: "center", gap: 4 }, children: [_jsx("span", { style: { width: 6, height: 6, borderRadius: "50%", background: "#22c55e", display: "inline-block", boxShadow: "0 0 8px #22c55e" } }), " RUNNING"] })) : (_jsx("span", { style: { fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(107,114,128,0.12)", color: colors.textDim }, children: "STOPPED" })) })] }), _jsxs("div", { style: { display: "flex", gap: 8, marginBottom: 16 }, children: [_jsx("button", { className: "icon-button", style: { fontSize: 12, padding: "6px 12px", height: "auto", background: servicesHeartbeatStatus?.running ? "rgba(255,255,255,0.05)" : "rgba(34,197,94,0.2)", color: servicesHeartbeatStatus?.running ? colors.textDim : "#22c55e", border: `1px solid ${servicesHeartbeatStatus?.running ? colors.line : "rgba(34,197,94,0.5)"}` }, onClick: async () => {
                                                                            if (servicesHeartbeatStatus?.running)
                                                                                return;
                                                                            await fetchWithTimeout(`${apiBase}/heartbeat/start`, { method: "POST" });
                                                                            refreshServices();
                                                                        }, disabled: servicesHeartbeatStatus?.running || servicesLoading, children: "Start Heartbeat" }), _jsx("button", { className: "icon-button", style: { fontSize: 12, padding: "6px 12px", height: "auto", background: !servicesHeartbeatStatus?.running ? "rgba(255,255,255,0.05)" : "rgba(239,68,68,0.2)", color: !servicesHeartbeatStatus?.running ? colors.textDim : "#ef4444", border: `1px solid ${!servicesHeartbeatStatus?.running ? colors.line : "rgba(239,68,68,0.5)"}` }, onClick: async () => {
                                                                            if (!servicesHeartbeatStatus?.running)
                                                                                return;
                                                                            await fetchWithTimeout(`${apiBase}/heartbeat/stop`, { method: "POST" });
                                                                            refreshServices();
                                                                        }, disabled: !servicesHeartbeatStatus?.running || servicesLoading, children: "Stop Heartbeat" })] }), _jsx("div", { style: { fontSize: 12, color: colors.text, marginBottom: 8, fontWeight: 600 }, children: "Recent Proactive Thoughts" }), _jsx("div", { style: { display: "flex", flexDirection: "column", gap: 8 }, children: !servicesHeartbeatHistory || servicesHeartbeatHistory.length === 0 ? (_jsx("div", { style: { fontSize: 12, color: colors.textDim, fontStyle: "italic" }, children: "No recent history." })) : (servicesHeartbeatHistory.map((h, i) => (_jsxs("div", { style: { background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6, fontSize: 12, borderLeft: `3px solid ${h.status === "error" ? "#ef4444" : h.status === "ran_tools" ? "#3b82f6" : colors.line}` }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", marginBottom: 4 }, children: [_jsx("span", { style: { color: colors.textDim }, children: new Date(h.timestamp * 1000 || h.timestamp).toLocaleString() }), _jsx("span", { style: { textTransform: "uppercase", fontSize: 10, color: h.status === "error" ? "#ef4444" : colors.accent }, children: h.status })] }), _jsx("div", { style: { color: colors.text }, children: h.result || h.action })] }, i)))) })] }), _jsxs("div", { style: { background: colors.panel2, borderRadius: 8, padding: 16, border: `1px solid ${colors.line}` }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 10 }, children: [_jsx("span", { style: { fontSize: 18 }, children: "\u2708\uFE0F" }), _jsx("span", { style: { fontSize: 14, fontWeight: 600, color: colors.text }, children: "Telegram Bot" })] }), _jsx("div", { style: { display: "flex", alignItems: "center", gap: 8 }, children: servicesTelegramStatus?.running ? (_jsxs("span", { style: { fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(56,189,248,0.12)", color: "#38bdf8", display: "flex", alignItems: "center", gap: 4 }, children: [_jsx("span", { style: { width: 6, height: 6, borderRadius: "50%", background: "#38bdf8", display: "inline-block", boxShadow: "0 0 8px #38bdf8" } }), " ONLINE"] })) : (_jsx("span", { style: { fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(107,114,128,0.12)", color: colors.textDim }, children: "OFFLINE" })) })] }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, fontSize: 12 }, children: [_jsxs("div", { style: { background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6 }, children: [_jsx("span", { style: { color: colors.textDim, display: "block", marginBottom: 4 }, children: "Bot Username" }), _jsx("span", { style: { color: colors.text }, children: servicesTelegramStatus?.username ? `@${servicesTelegramStatus.username}` : "N/A" })] }), _jsxs("div", { style: { background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6 }, children: [_jsx("span", { style: { color: colors.textDim, display: "block", marginBottom: 4 }, children: "Allowed Users" }), _jsx("span", { style: { color: colors.text }, children: servicesTelegramStatus?.allowed_users?.length ? servicesTelegramStatus.allowed_users.join(", ") : "N/A" })] })] }), !servicesTelegramStatus?.running && (_jsx("div", { style: { fontSize: 11, color: colors.textDim, marginTop: 12, fontStyle: "italic" }, children: "The bot is offline. Make sure you have configured a valid Bot Token in the Settings tab and toggled the bot on." }))] }), _jsxs("div", { style: { background: colors.panel2, borderRadius: 8, padding: 16, border: `1px solid ${colors.line}`, marginTop: 16 }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }, children: [_jsxs("div", { style: { display: "flex", alignItems: "center", gap: 10 }, children: [_jsx("span", { style: { fontSize: 18 }, children: "\uD83C\uDFAE" }), _jsx("span", { style: { fontSize: 14, fontWeight: 600, color: colors.text }, children: "Discord Bot" })] }), _jsx("div", { style: { display: "flex", alignItems: "center", gap: 8 }, children: servicesDiscordStatus?.running ? (_jsxs("span", { style: { fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(99,102,241,0.12)", color: "#818cf8", display: "flex", alignItems: "center", gap: 4 }, children: [_jsx("span", { style: { width: 6, height: 6, borderRadius: "50%", background: "#818cf8", display: "inline-block", boxShadow: "0 0 8px #818cf8" } }), " ONLINE"] })) : servicesDiscordStatus?.enabled ? (_jsx("span", { style: { fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(245,158,11,0.12)", color: "#f59e0b" }, children: "OFFLINE" })) : (_jsx("span", { style: { fontSize: 10, padding: "2px 6px", borderRadius: 4, background: "rgba(107,114,128,0.12)", color: colors.textDim }, children: "DISABLED" })) })] }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, fontSize: 12 }, children: [_jsxs("div", { style: { background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6 }, children: [_jsx("span", { style: { color: colors.textDim, display: "block", marginBottom: 4 }, children: "Bot Username" }), _jsx("span", { style: { color: colors.text }, children: servicesDiscordStatus?.username ? `@${servicesDiscordStatus.username}` : "N/A" })] }), _jsxs("div", { style: { background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6 }, children: [_jsx("span", { style: { color: colors.textDim, display: "block", marginBottom: 4 }, children: "Guilds (Servers)" }), _jsx("span", { style: { color: colors.text }, children: servicesDiscordStatus?.guilds || "0" })] })] }), _jsxs("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, fontSize: 12, marginTop: 12 }, children: [_jsxs("div", { style: { background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6 }, children: [_jsx("span", { style: { color: colors.textDim, display: "block", marginBottom: 4 }, children: "Gateway Link" }), _jsx("span", { style: { color: discordGatewayConnected ? "#22c55e" : colors.textDim }, children: discordGatewayConnected ? "Connected" : "Disconnected" })] }), _jsxs("div", { style: { background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6 }, children: [_jsx("span", { style: { color: colors.textDim, display: "block", marginBottom: 4 }, children: "Gateway Session" }), _jsx("span", { style: { color: colors.text }, children: discordGatewaySessionId || "Waiting..." })] })] }), _jsx("div", { style: { fontSize: 12, color: colors.text, marginTop: 16, marginBottom: 8, fontWeight: 600 }, children: "Live Discord Activity" }), _jsx("div", { style: { display: "flex", flexDirection: "column", gap: 8 }, children: discordLiveEvents.length === 0 ? (_jsx("div", { style: { fontSize: 12, color: colors.textDim, fontStyle: "italic" }, children: "No live Discord activity yet. When the bot runs Discord tools, events will appear here automatically." })) : (discordLiveEvents.map((event) => (_jsxs("div", { style: { background: "rgba(0,0,0,0.2)", padding: 10, borderRadius: 6, borderLeft: `3px solid ${event.kind === "error" ? "#ef4444" : "#818cf8"}` }, children: [_jsxs("div", { style: { display: "flex", justifyContent: "space-between", marginBottom: 4, gap: 12 }, children: [_jsx("span", { style: { color: colors.textDim, fontSize: 11 }, children: new Date(event.at).toLocaleString() }), _jsx("span", { style: { textTransform: "uppercase", fontSize: 10, color: event.kind === "error" ? "#ef4444" : "#818cf8" }, children: event.kind === "error" ? "gateway error" : (event.source || "discord_bot") })] }), _jsx("div", { style: { color: colors.text, fontSize: 12.5 }, children: event.kind === "error" ? (event.message || "Gateway error") : `Tool activity: ${event.tool || "unknown"}` })] }, event.id)))) }), !servicesDiscordStatus?.running && (_jsx("div", { style: { fontSize: 11, color: colors.textDim, marginTop: 12, fontStyle: "italic" }, children: !servicesDiscordStatus?.enabled
                                                                    ? "The bot is disabled in Settings. Enable Discord Bot and save settings to bring it online."
                                                                    : !servicesDiscordStatus?.token_set
                                                                        ? "The bot is enabled but no bot token is configured in Settings."
                                                                        : "The bot is enabled but not connected. Check the token and Discord privileged intents, then save settings again or restart the API." }))] })] }) }))] }) })] })] }), _jsx("input", { type: "file", ref: docInputRef, style: { display: "none" }, onChange: (e) => setDocFile(e.target.files?.[0] || null) })] }));
};
