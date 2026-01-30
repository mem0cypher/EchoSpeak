import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactDOM from "react-dom/client";
import { AnimatePresence, motion } from "framer-motion";
import { create } from "zustand";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Types
type Role = "user" | "assistant";
type DocSource = {
  id: string;
  filename?: string;
  source?: string;
  chunk?: number;
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
  | { type: "tool_end"; id: string; output: string; at: number; request_id?: string }
  | { type: "tool_error"; id: string; error: string; at: number; request_id?: string }
  | { type: "memory_saved"; memory_count: number; at: number; request_id?: string }
  | { type: "final"; response: string; spoken_text?: string; success: boolean; memory_count: number; doc_sources?: DocSource[]; request_id?: string; at: number }
  | { type: "error"; message: string; at: number; request_id?: string };

type ActivityItem =
  | { kind: "thinking"; id: string; at: number }
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
};

type ProviderModelsResponse = {
  provider: string;
  models: string[];
};

type ResearchResult = {
  title: string;
  url: string;
  snippet: string;
};

type MemoryItem = {
  id: string;
  text: string;
  timestamp?: string;
  metadata?: Record<string, unknown>;
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

type STTInfoResponse = {
  enabled: boolean;
  model: string;
  device: string;
  compute_type: string;
};

type VisionAnalyzeResponse = {
  text: string;
  text_length: number;
  has_text: boolean;
  image_size: Record<string, number>;
};

type ResearchGroup = {
  id: string;
  at: number;
  query: string;
  results: ResearchResult[];
};

const parseWebSearchOutput = (output: string): ResearchResult[] => {
  const txt = (output || "").trim();
  if (!txt || txt.toLowerCase().includes("no search results")) return [];
  const blocks = txt.split(/\n\s*\n/g).map((b) => b.trim()).filter(Boolean);
  const results: ResearchResult[] = [];
  for (const block of blocks) {
    const lines = block.split("\n").map((l) => l.trim()).filter(Boolean);
    if (!lines.length) continue;
    const m = lines[0].match(/^\d+\.\s*(.*)$/);
    const title = (m ? m[1] : lines[0]).trim();
    const urlLine = lines.find((l) => l.toLowerCase().startsWith("url:")) || "";
    const url = urlLine.replace(/^url:\s*/i, "").trim();
    const snippetLines = lines.filter((l) => !l.toLowerCase().startsWith("url:") && l !== lines[0]);
    const snippet = snippetLines.join(" ").trim();
    if (!title && !snippet) continue;
    results.push({ title, url, snippet });
  }
  return results;
};

const openaiModelOptions = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "gpt-3.5-turbo"];
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

const fallbackProviders: ProviderListItem[] = [
  { id: "openai", name: "OpenAI", local: false, description: "OpenAI GPT models" },
  { id: "ollama", name: "Ollama", local: true, description: "Local Ollama models" },
  { id: "lmstudio", name: "LM Studio (GGUF direct)", local: true, description: "LM Studio (GGUF direct via OpenAI-compatible API)" },
  { id: "localai", name: "LocalAI", local: true, description: "LocalAI (OpenAI compatible)" },
  { id: "llama_cpp", name: "llama.cpp", local: true, description: "Direct llama.cpp models" },
  { id: "vllm", name: "vLLM", local: true, description: "vLLM server" },
];

type AppState = {
  messages: Message[];
  streaming: boolean;
  listening: boolean;
  speaking: boolean;
  speechBeat: number;
  addMessage: (msg: Message) => void;
  setStreaming: (v: boolean) => void;
  setListening: (v: boolean) => void;
  setSpeaking: (v: boolean) => void;
  bumpSpeechBeat: () => void;
};

const useAppStore = create<AppState>((set) => ({
  messages: [],
  streaming: false,
  listening: false,
  speaking: false,
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
         .app-shell {
           height: 100vh;
           display: grid;
           grid-template-columns: 320px 1fr 400px;
           gap: 0;
           padding: 0;
           background: ${colors.bg};
         }
         .glow-panel {
           background: ${colors.panel};
           border-right: 1px solid ${colors.line};
           display: flex;
           flex-direction: column;
           height: 100vh;
           transition: all 0.3s ease;
         }
         .glow-panel:last-child {
           border-right: none;
           border-left: 1px solid ${colors.line};
         }
         .panel-header {
           display: flex;
           align-items: center;
           justify-content: space-between;
           padding: 24px;
           border-bottom: 1px solid ${colors.line};
         }
         .panel-header .title {
           display: flex;
           gap: 12px;
           align-items: center;
           font-family: 'Space Grotesk', sans-serif;
           font-size: 18px;
           font-weight: 700;
           letter-spacing: -0.02em;
           color: ${colors.text};
         }
         .panel-dot {
           width: 12px;
           height: 12px;
           background: #fff;
           border-radius: 0;
         }
         .panel-body {
           flex: 1;
           display: flex;
           flex-direction: column;
           padding: 24px;
           overflow: hidden;
           min-height: 0;
           gap: 24px;
         }
         .research-panel {
           display: flex;
           flex-direction: column;
           height: 100%;
           gap: 20px;
         }
         .tab-bar {
           display: flex;
           gap: 20px;
           border-bottom: 1px solid ${colors.line};
           padding-bottom: 2px;
         }
         .tab-button {
           padding: 8px 4px;
           background: transparent;
           border: none;
           border-bottom: 2px solid transparent;
           color: ${colors.textDim};
           font-size: 14px;
           font-weight: 500;
           cursor: pointer;
           transition: all 0.2s ease;
           position: relative;
         }
         .tab-button:hover {
           color: ${colors.text};
         }
         .tab-button.active {
           color: ${colors.accent};
           border-bottom-color: ${colors.accent};
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
           background: ${colors.panel2};
           border: 1px solid ${colors.line};
           border-radius: 8px;
           padding: 16px;
           transition: border-color 0.2s ease, transform 0.2s ease;
         }
         .research-card:hover {
           border-color: rgba(255, 255, 255, 0.15);
           transform: translateY(-1px);
         }
         .research-title {
           font-size: 14px;
           font-weight: 600;
           color: ${colors.text};
           margin-bottom: 8px;
           line-height: 1.4;
         }
         .research-snippet {
           font-size: 13px;
           line-height: 1.6;
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
           background: ${colors.panel2};
           border: 1px solid ${colors.line};
           border-radius: 12px;
           padding: 14px 18px;
           color: ${colors.text};
           font-size: 14px;
           outline: none;
           transition: border-color 0.2s ease;
         }
         .input-field:focus {
           border-color: ${colors.accent};
         }
         .send-button {
           width: 48px;
           height: 48px;
           display: grid;
           place-items: center;
           background: ${colors.accent};
           border: none;
           border-radius: 12px;
           color: white;
           cursor: pointer;
           transition: all 0.2s ease;
         }
         .send-button:hover {
           background: ${colors.accent}dd;
           transform: translateY(-1px);
         }
         .send-button:active {
           transform: translateY(0);
         }
         .controls-row {
           display: flex;
           align-items: center;
           gap: 12px;
         }
         .mic-button {
           width: 40px;
           height: 40px;
           display: grid;
           place-items: center;
           background: ${colors.panel2};
           border: 1px solid ${colors.line};
           border-radius: 10px;
           color: ${colors.textDim};
           cursor: pointer;
           transition: all 0.2s ease;
         }
         .mic-button:hover {
           color: ${colors.text};
           border-color: rgba(255, 255, 255, 0.2);
         }
         .mic-button.active {
           background: ${colors.danger}15;
           border-color: ${colors.danger}44;
           color: ${colors.danger};
         }
         .inline-switcher {
           margin-left: auto;
           display: flex;
           align-items: center;
           gap: 12px;
           padding: 6px 12px;
           background: ${colors.panel2};
           border: 1px solid ${colors.line};
           border-radius: 8px;
         }
         .switcher-dot {
           width: 6px;
           height: 6px;
           border-radius: 50%;
           background: #475569;
         }
         .switcher-dot.online { background: #22c55e; box-shadow: 0 0 8px #22c55e44; }
         .switcher-dot.offline { background: #ef4444; box-shadow: 0 0 8px #ef444444; }
         .inline-switcher select {
           background: transparent;
           border: none;
           color: ${colors.text};
           font-size: 13px;
           font-weight: 500;
           outline: none;
           cursor: pointer;
         }
       `;

const sanitizeForTTS = (input: string) => {
  let text = input || "";
  text = text.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, "");
  text = text.replace(/[\u2300-\u23FF\u2600-\u27BF]/g, "");
  text = text.replace(/[\u200D\uFE0E\uFE0F]/g, "");
  return text.replace(/\s+/g, " ").trim();
};

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

let activeTtsAudio: HTMLAudioElement | null = null;
let audioUnlocked = false;
let audioUnlockPromise: Promise<void> | null = null;
let ttsSequence = 0;
let ttsAbort: AbortController | null = null;

const ttsTabId = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Math.random());
let ttsChannel: BroadcastChannel | null = null;
try {
  ttsChannel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("echospeak_tts") : null;
} catch {
  ttsChannel = null;
}

const stopTts = () => {
  ttsSequence += 1;
  if (ttsAbort) {
    try {
      ttsAbort.abort();
    } catch { }
    ttsAbort = null;
  }
  if (activeTtsAudio) {
    try {
      activeTtsAudio.pause();
    } catch { }
    try {
      activeTtsAudio.src = "";
    } catch { }
    activeTtsAudio = null;
  }
  useAppStore.getState().setSpeaking(false);
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

const ensureAudioUnlocked = async () => {
  if (audioUnlocked) return;
  if (audioUnlockPromise) return audioUnlockPromise;
  audioUnlockPromise = (async () => {
    try {
      const AnyWindow = window as any;
      const Ctx = window.AudioContext || AnyWindow.webkitAudioContext;
      if (!Ctx) {
        audioUnlocked = true;
        return;
      }

      const ctx: AudioContext = new Ctx();
      try {
        if (ctx.state === "suspended") {
          await ctx.resume();
        }
      } catch { }

      if (ctx.state === "running") {
        try {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          gain.gain.value = 0;
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start();
          osc.stop(ctx.currentTime + 0.02);
        } catch { }
        audioUnlocked = true;
      }

      window.setTimeout(() => {
        try {
          ctx.close();
        } catch { }
      }, 150);
    } finally {
      if (!audioUnlocked) {
        audioUnlockPromise = null;
      }
    }
  })();
  return audioUnlockPromise;
};

const speakText = async (text: string, apiBase: string) => {
  const cleaned = sanitizeForTTS(text);
  if (!cleaned) return;
  const { setSpeaking, bumpSpeechBeat } = useAppStore.getState();
  const sequenceId = ++ttsSequence;

  if (ttsChannel) {
    try {
      ttsChannel.postMessage({ type: "tts_start", tabId: ttsTabId, at: Date.now() });
    } catch { }
  }

  if (ttsAbort) {
    try {
      ttsAbort.abort();
    } catch { }
  }
  ttsAbort = new AbortController();

  if (activeTtsAudio) {
    try {
      activeTtsAudio.pause();
    } catch { }
    try {
      activeTtsAudio.src = "";
    } catch { }
    try {
      activeTtsAudio.load();
    } catch { }
    activeTtsAudio = null;
  }

  setSpeaking(false);

  const chunks = chunkTextForTTS(cleaned, 260);
  if (!chunks.length) return;

  let beatTimer: number | null = null;
  let beatStopTimer: number | null = null;
  const startBeat = () => {
    if (beatStopTimer != null) {
      window.clearTimeout(beatStopTimer);
      beatStopTimer = null;
    }
    if (beatTimer == null) {
      beatTimer = window.setInterval(() => bumpSpeechBeat(), 130);
    }
    setSpeaking(true);
  };
  const scheduleBeatStop = () => {
    if (beatStopTimer != null) {
      window.clearTimeout(beatStopTimer);
    }
    beatStopTimer = window.setTimeout(() => {
      if (beatTimer != null) {
        window.clearInterval(beatTimer);
        beatTimer = null;
      }
      setSpeaking(false);
      beatStopTimer = null;
    }, 650);
  };
  const stopBeat = () => {
    if (beatTimer != null) {
      window.clearInterval(beatTimer);
      beatTimer = null;
    }
    if (beatStopTimer != null) {
      window.clearTimeout(beatStopTimer);
      beatStopTimer = null;
    }
    setSpeaking(false);
  };

  const fetchChunk = async (chunk: string) => {
    const resp = await fetch(`${apiBase}/tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-EchoSpeak-Client": "web" },
      body: JSON.stringify({ text: chunk }),
      signal: ttsAbort?.signal,
    });

    if (!resp.ok) {
      throw new Error(await resp.text());
    }

    const blob = await resp.blob();
    return URL.createObjectURL(blob);
  };

  const playUrl = async (url: string, lengthHint: number) =>
    new Promise<void>((resolve, reject) => {
      const audio = new Audio(url);
      activeTtsAudio = audio;
      let done = false;

      const cleanup = (err?: Error) => {
        if (done) return;
        done = true;
        try {
          URL.revokeObjectURL(url);
        } catch { }
        if (activeTtsAudio === audio) activeTtsAudio = null;
        if (err) reject(err);
        else resolve();
      };

      const safetyTimeout = window.setTimeout(
        () => cleanup(new Error("timeout")),
        Math.max(2800, Math.min(12000, lengthHint * 90))
      );

      const clearSafety = () => window.clearTimeout(safetyTimeout);

      audio.addEventListener("playing", () => startBeat());
      audio.addEventListener("ended", () => {
        clearSafety();
        scheduleBeatStop();
        cleanup();
      });
      audio.addEventListener("pause", () => {
        clearSafety();
        scheduleBeatStop();
        cleanup();
      });
      audio.addEventListener("error", () => {
        clearSafety();
        cleanup(new Error("audio_error"));
      });

      audio.play().catch((err) => cleanup(err instanceof Error ? err : new Error(String(err))));
    });

  let pending: Promise<string> = Promise.resolve("");
  let currentIndex = 0;
  try {
    pending = fetchChunk(chunks[0]);
    for (let i = 0; i < chunks.length; i += 1) {
      currentIndex = i;
      const url = await pending;
      if (sequenceId !== ttsSequence) {
        try {
          URL.revokeObjectURL(url);
        } catch { }
        stopBeat();
        return;
      }
      pending = i + 1 < chunks.length ? fetchChunk(chunks[i + 1]) : Promise.resolve("");
      await playUrl(url, chunks[i].length);
    }
    stopBeat();
  } catch (err) {
    stopBeat();
    const name = (err as any)?.name ? String((err as any).name) : "";
    if (name === "NotAllowedError" || name === "AbortError") return;
    setSpeaking(false);
  } finally {
    pending.catch(() => null);
  }
};

// Hook: mic capture -> browser SpeechRecognition or local STT
const useMicStreamer = (apiBase: string, useLocalStt: boolean, onFinalTranscript?: (text: string) => void) => {
  const mediaRef = useRef<MediaRecorder | null>(null);
  const recRef = useRef<any>(null);
  const transcriptRef = useRef<string>("");
  const chunksRef = useRef<Blob[]>([]);
  const submitRef = useRef(false);
  const { setListening, setStreaming, addMessage } = useAppStore();

  const stopAll = (submitTranscript: boolean) => {
    if (useLocalStt) {
      submitRef.current = submitTranscript;
      try {
        mediaRef.current?.stop?.();
      } catch {
        // ignore
      }
      try {
        mediaRef.current?.stream.getTracks().forEach((t: MediaStreamTrack) => t.stop());
      } catch {
        // ignore
      }
      mediaRef.current = null;
      recRef.current = null;
      setListening(false);
      setStreaming(false);
      return;
    }

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
      // ignore
    }
    recRef.current = null;

    try {
      mediaRef.current?.stream.getTracks().forEach((t: MediaStreamTrack) => t.stop());
    } catch {
      // ignore
    }
    mediaRef.current = null;
    setListening(false);
    setStreaming(false);
  };

  const start = async () => {
    if (mediaRef.current || recRef.current) stopAll(false);
    try {
      if (useLocalStt) {
        if (!navigator.mediaDevices?.getUserMedia) {
          addMessage({ id: crypto.randomUUID(), role: "assistant", text: "Mic unavailable: no audio capture support.", at: Date.now() });
          stopAll(false);
          return;
        }

        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const recorder = new MediaRecorder(stream);
        mediaRef.current = recorder;
        chunksRef.current = [];
        submitRef.current = false;

        recorder.ondataavailable = (evt: BlobEvent) => {
          if (evt.data && evt.data.size) chunksRef.current.push(evt.data);
        };

        recorder.onstop = async () => {
          const shouldSubmit = submitRef.current;
          submitRef.current = false;
          if (!shouldSubmit) return;
          const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
          chunksRef.current = [];
          try {
            const form = new FormData();
            form.append("audio", blob, "audio.webm");
            const resp = await fetch(`${apiBase}/stt`, { method: "POST", body: form });
            if (!resp.ok) throw new Error(await resp.text());
            const data = (await resp.json()) as { text?: string };
            const text = (data?.text || "").trim();
            if (text) onFinalTranscript?.(text);
            else {
              addMessage({ id: crypto.randomUUID(), role: "assistant", text: "Mic: I didn't catch any speech.", at: Date.now() });
            }
          } catch (err) {
            addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Local STT error: ${String(err)}`, at: Date.now() });
          }
        };

        recorder.start();
        setListening(true);
        setStreaming(true);
        return;
      }

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
          transcriptRef.current = (finals + interim).trim();
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
      submitRef.current = true;
      stopAll(true);
    },
  };
};

const ChatBubble: React.FC<{ msg: Message }> = ({ msg }) => {
  const isUser = msg.role === "user";
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.24, ease: "easeOut" }}
      style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start" }}
    >
      <div
        style={{
          maxWidth: "82%",
          background: isUser ? "rgba(29, 108, 255, 0.16)" : colors.panel2,
          color: colors.text,
          border: `1px solid ${isUser ? "rgba(45,108,255,0.5)" : colors.line}`,
          borderRadius: 10,
          padding: "12px 14px",
          boxShadow: isUser ? `0 2px 12px rgba(45,108,255,0.15)` : "none",
        }}
      >
        {isUser ? (
          <div className="chat-text">{msg.text}</div>
        ) : (
          <div className="chat-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.text}</ReactMarkdown>
          </div>
        )}
        <div style={{ marginTop: 4, fontSize: 10.5, color: colors.textDim }}>{new Date(msg.at).toLocaleTimeString()}</div>
      </div>
    </motion.div>
  );
};

const ActivityCard: React.FC<{ item: ActivityItem }> = ({ item }) => {
  if (item.kind === "thinking") {
    const badge = { label: "Thinking", color: "rgba(140,160,255,0.9)", bg: "rgba(45,108,255,0.12)", border: "rgba(45,108,255,0.3)" };
    const title = "Echo is working…";
    const body = "Planning next action";
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
            maxWidth: "90%",
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

  if (item.kind === "memory") {
    const badge = { label: "Memory", color: "rgba(140,255,200,0.9)", bg: "rgba(45,255,160,0.10)", border: "rgba(45,255,160,0.25)" };
    const title = "Saved to memory";
    const body = `Memory items: ${item.memoryCount}`;
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
            maxWidth: "90%",
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
            maxWidth: "90%",
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

  const badge =
    item.status === "running"
      ? { label: "Tool", color: "rgba(140,160,255,0.9)", bg: "rgba(45,108,255,0.10)", border: "rgba(45,108,255,0.25)" }
      : item.status === "error"
        ? { label: "Tool", color: "rgba(255,140,160,0.95)", bg: "rgba(255,77,109,0.10)", border: "rgba(255,77,109,0.35)" }
        : { label: "Tool", color: "rgba(170,190,255,0.9)", bg: "rgba(120,160,255,0.08)", border: "rgba(120,160,255,0.22)" };
  const title = `Using ${item.name}`;
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
          maxWidth: "90%",
          width: "fit-content",
          background: colors.panel2,
          color: colors.text,
          border: `1px solid ${colors.line}`,
          borderRadius: 14,
          padding: "10px 12px",
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
          <div style={{ fontSize: 13, fontWeight: 650 }}>{title}</div>
        </div>
        <div style={{ marginTop: 6, fontSize: 12.5, lineHeight: 1.5, color: colors.textDim, whiteSpace: "pre-wrap" }}>{body}</div>
      </div>
    </motion.div>
  );
};

const RingVisual = React.memo(({ speaking, speechBeat }: { speaking: boolean; speechBeat: number }) => {
  const baseRef = useRef<SVGPathElement | null>(null);
  const glowRef = useRef<SVGPathElement | null>(null);
  const groupRef = useRef<SVGGElement | null>(null);
  const speakingRef = useRef(speaking);
  const lastBeatMsRef = useRef(0);

  useEffect(() => {
    speakingRef.current = speaking;
  }, [speaking]);

  useEffect(() => {
    lastBeatMsRef.current = performance.now();
  }, [speechBeat]);

  useEffect(() => {
    const center = 110;
    const points = 60;
    const baseR = 76;
    const TAU = Math.PI * 2;
    const RAD2DEG = 180 / Math.PI;

    const cosA = new Float32Array(points);
    const sinA = new Float32Array(points);
    const cos3A = new Float32Array(points);
    const triA = new Float32Array(points);
    for (let i = 0; i < points; i += 1) {
      const a = (i / points) * TAU;
      cosA[i] = Math.cos(a);
      sinA[i] = Math.sin(a);
      cos3A[i] = Math.cos(3 * a);
    }

    const triSharpness = 1.25;
    const triNorm = Math.tanh(triSharpness) || 1;
    for (let i = 0; i < points; i += 1) {
      triA[i] = Math.tanh(cos3A[i] * triSharpness) / triNorm;
    }

    const xs = new Float32Array(points);
    const ys = new Float32Array(points);
    const fmt = (v: number) => v.toFixed(1);

    const toPath = () => {
      let d = `M ${fmt(xs[0])} ${fmt(ys[0])}`;
      for (let i = 0; i < points; i += 1) {
        const i0 = (i - 1 + points) % points;
        const i1 = i;
        const i2 = (i + 1) % points;
        const i3 = (i + 2) % points;

        const cp1x = xs[i1] + (xs[i2] - xs[i0]) / 6;
        const cp1y = ys[i1] + (ys[i2] - ys[i0]) / 6;
        const cp2x = xs[i2] - (xs[i3] - xs[i1]) / 6;
        const cp2y = ys[i2] - (ys[i3] - ys[i1]) / 6;
        d += ` C ${fmt(cp1x)} ${fmt(cp1y)} ${fmt(cp2x)} ${fmt(cp2y)} ${fmt(xs[i2])} ${fmt(ys[i2])}`;
      }
      return `${d} Z`;
    };

    let raf = 0;
    let lastNow = performance.now();
    let speakMix = speakingRef.current ? 1 : 0;
    const tick = (now: number) => {
      const t = now / 1000;
      const dt = Math.min(0.05, Math.max(0.001, (now - lastNow) / 1000));
      lastNow = now;

      const spTarget = speakingRef.current ? 1 : 0;
      speakMix += (spTarget - speakMix) * (1 - Math.exp(-dt * 10));

      const idlePhase = (Math.sin((t * TAU) / 5) + 1) / 2;
      const ampMax = 0.16;
      const amp = (1 - idlePhase) * ampMax * (1 - speakMix);

      const idleBreath = 0.014 * Math.sin((t * TAU) / 3.4) * (1 - speakMix);
      const talkBreath = 0.024 * Math.sin(t * TAU * 1.6) * speakMix;

      const sinceBeat = (now - lastBeatMsRef.current) / 1000;
      const beat = speakMix > 0.001 ? Math.exp(-sinceBeat * 8) : 0;

      const scale = 1 + idleBreath + talkBreath + 0.055 * beat * speakMix;
      const rotSpeed = 0.28 + (0.9 - 0.28) * speakMix;
      const rotDeg = t * rotSpeed * RAD2DEG;
      groupRef.current?.setAttribute("transform", `rotate(${rotDeg.toFixed(2)} ${center} ${center})`);

      for (let i = 0; i < points; i += 1) {
        const tri = triA[i];
        const r = baseR * scale * (1 + amp * tri);
        xs[i] = center + r * cosA[i];
        ys[i] = center + r * sinA[i];
      }

      const d = toPath();
      baseRef.current?.setAttribute("d", d);
      glowRef.current?.setAttribute("d", d);
      raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(raf);
  }, []);

  return (
    <div className={`ring-shell ${speaking ? "speaking" : "idle"}`}>
      <div className="ring-core" />
      <svg className="ring-svg" width="240" height="240" viewBox="0 0 220 220" fill="none" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="ringGradient" x1="22" y1="18" x2="196" y2="206" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="#ff8bd7" />
            <stop offset="0.42" stopColor="#c4a0ff" />
            <stop offset="0.78" stopColor="#79a9ff" />
            <stop offset="1" stopColor="#6fd0ff" />
          </linearGradient>
          <linearGradient id="ringBlue" x1="22" y1="18" x2="196" y2="206" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="#2d6cff" />
            <stop offset="1" stopColor="#6fd0ff" />
          </linearGradient>
          <filter id="ringGlow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="8" result="blur" />
            <feColorMatrix
              type="matrix"
              values="0 0 0 0 0.55  0 0 0 0 0.62  0 0 0 0 1  0 0 0 0.75 0"
            />
          </filter>
        </defs>
        <g ref={groupRef}>
          <path
            ref={baseRef}
            className="ring-base"
            d="M110 24 C165 24 196 63 191 111 C186 165 147 196 110 196 C69 196 28 163 32 106 C35 58 68 24 110 24 Z"
            stroke={speaking ? "#2d6cff" : "#1d2432"}
            strokeWidth={speaking ? 9.5 : 8}
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="transparent"
          />
          <path
            ref={glowRef}
            className="ring-glow"
            d="M110 24 C165 24 196 63 191 111 C186 165 147 196 110 196 C69 196 28 163 32 106 C35 58 68 24 110 24 Z"
            stroke={speaking ? "url(#ringBlue)" : "url(#ringGradient)"}
            strokeWidth={speaking ? 10 : 8}
            strokeLinecap="round"
            strokeLinejoin="round"
            fill="transparent"
            filter="url(#ringGlow)"
          />
        </g>
      </svg>
    </div>
  );
});

RingVisual.displayName = "RingVisual";

export const Dashboard: React.FC = () => {
  const { messages, addMessage, listening, streaming, setListening, setStreaming, speaking, speechBeat } = useAppStore();
  const [input, setInput] = useState("");
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [research, setResearch] = useState<ResearchGroup[]>([]);
  const [leftTab, setLeftTab] = useState<"research" | "memory" | "docs">("research");
  const [memoryItems, setMemoryItems] = useState<MemoryItem[]>([]);
  const [memoryCount, setMemoryCount] = useState<number>(0);
  const [memoryLoading, setMemoryLoading] = useState<boolean>(false);
  const [docItems, setDocItems] = useState<DocumentItem[]>([]);
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
  const threadIdRef = useRef<string>(crypto.randomUUID());
  const docInputRef = useRef<HTMLInputElement | null>(null);
  const apiBase = useMemo(() => "http://localhost:8000", []);
  const bootedRef = useRef(false);
  const backendRetryRef = useRef<{ attempt: number; timer: number | null }>({ attempt: 0, timer: null });

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
  const [sttInfo, setSttInfo] = useState<STTInfoResponse | null>(null);

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
      const resp = await fetchWithTimeout(`${apiBase}/provider`);
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

  const refreshMemory = async () => {
    setMemoryLoading(true);
    try {
      const resp = await fetchWithTimeout(`${apiBase}/memory?offset=0&limit=200`);
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
        body: JSON.stringify({ ids: [id] }),
      });
      if (!resp.ok) throw new Error(`Delete failed (${resp.status})`);
      await refreshMemory();
    } catch (e) {
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${String(e)}`, at: Date.now() });
    }
  };

  const clearAllMemory = async () => {
    if (!window.confirm("Clear all saved memory?")) return;
    try {
      const resp = await fetchWithTimeout(`${apiBase}/memory/clear`, { method: "POST" });
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

  const refreshSttInfo = async () => {
    try {
      const resp = await fetchWithTimeout(`${apiBase}/stt/info`);
      if (!resp.ok) throw new Error();
      const data = (await resp.json()) as STTInfoResponse;
      setSttInfo(data);
    } catch {
      setSttInfo(null);
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

  const sendText = async (overrideText?: string) => {
    const raw = (overrideText ?? input).trim();
    if (!raw) return;

    stopTts();

    try {
      await ensureAudioUnlocked();
    } catch { }

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
    setDocSources([]);
    setActivities([{ kind: "thinking", id: crypto.randomUUID(), at: Date.now() + 1 }]);
    try {
      const resp = await fetch(`${apiBase}/query/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: requestText,
          include_memory: true,
          thread_id: threadIdRef.current,
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
            const withoutThinking = prev.filter((p) => p.kind !== "thinking");
            return [
              ...withoutThinking,
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
            const parsed = parseWebSearchOutput(evt.output || "");
            const query = (info.input || "").trim();
            const group: ResearchGroup = {
              id: crypto.randomUUID(),
              at: Date.now(),
              query,
              results: parsed,
            };
            setResearch((prev) => [group, ...prev].slice(0, 20));
            const summary = parsed.length ? `Captured ${parsed.length} sources (see Research panel)` : "No sources found";
            setActivities((prev) =>
              prev.map((p) => (p.kind === "tool" && p.id === evt.id ? { ...p, status: "done", output: summary } : p))
            );
            return;
          }
          setActivities((prev) =>
            prev.map((p) => (p.kind === "tool" && p.id === evt.id ? { ...p, status: "done", output: evt.output } : p))
          );
          return;
        }

        if (evt.type === "tool_error") {
          setActivities((prev) =>
            prev.map((p) => (p.kind === "tool" && p.id === evt.id ? { ...p, status: "error", output: evt.error } : p))
          );
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

          if (evt.type === "tool_start" || evt.type === "tool_end" || evt.type === "tool_error") {
            upsertTool(evt);
          } else if (evt.type === "memory_saved") {
            setActivities((prev) => [
              ...prev,
              { kind: "memory", id: crypto.randomUUID(), memoryCount: evt.memory_count, at: Date.now() },
            ]);
            setMemoryCount(evt.memory_count);
            if (leftTab === "memory") {
              refreshMemory();
            }
          } else if (evt.type === "error") {
            setActivities((prev) => [
              ...prev.filter((p) => p.kind !== "thinking"),
              { kind: "error", id: crypto.randomUUID(), message: evt.message, at: Date.now() },
            ]);
          } else if (evt.type === "final") {
            const reply = evt.response || "(no response)";
            const spoken = (evt.spoken_text || "").trim();
            setDocSources(Array.isArray(evt.doc_sources) ? evt.doc_sources : []);
            const botMsg: Message = { id: crypto.randomUUID(), role: "assistant", text: reply, at: Date.now() };
            addMessage(botMsg);
            if (spoken) {
              speakText(spoken, apiBase);
            } else {
              speakText(reply, apiBase);
            }
            setActivities((prev) => prev.filter((p) => p.kind !== "thinking"));
          }
        }
      }
    } catch (err) {
      const msg = String(err);
      const pretty = msg.includes("Failed to fetch") ? `Backend offline (${apiBase})` : msg;
      setBackendOnline(false);
      addMessage({ id: crypto.randomUUID(), role: "assistant", text: `Error: ${pretty}`, at: Date.now() });
      setActivities((prev) => [
        ...prev.filter((p) => p.kind !== "thinking"),
        { kind: "error", id: crypto.randomUUID(), message: pretty, at: Date.now() },
      ]);
    }
  };

  const { start, stop } = useMicStreamer(apiBase, Boolean(sttInfo?.enabled), (t: string) => {
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
    refreshSttInfo();
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
  }, [leftTab]);

  useEffect(() => {
    if (backendOnline === false) return;
    if (providerDraft.provider === "openai") {
      setProviderModels(openaiModelOptions);
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
    if (providerModels.length && (providerDraft.provider === "openai" || listableProviders.includes(providerDraft.provider))) {
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

  return (
    <div
      style={{
        minHeight: "100vh",
        background: colors.bg,
        color: colors.text,
      }}
    >
      <style>{globalCss}</style>
      <div className="app-shell">
        <div className="glow-panel">
          <div className="panel-header">
            <div className="title">
              <span className="panel-dot" />
              <span>Resources</span>
            </div>
          </div>
          <div className="panel-body">
            <div className="research-panel">
              <div className="tab-bar">
                <button
                  type="button"
                  className={`tab-button ${leftTab === "research" ? "active" : ""}`}
                  onClick={() => setLeftTab("research")}
                >
                  Research
                </button>
                <button
                  type="button"
                  className={`tab-button ${leftTab === "memory" ? "active" : ""}`}
                  onClick={() => setLeftTab("memory")}
                >
                  Memory
                </button>
                <button
                  type="button"
                  className={`tab-button ${leftTab === "docs" ? "active" : ""}`}
                  onClick={() => setLeftTab("docs")}
                >
                  Documents
                </button>
              </div>

              <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: -12 }}>
                {leftTab === "research" ? (
                  <>
                    <button
                      className="icon-button"
                      style={{ height: 28, padding: "0 10px", fontSize: 12, flex: 1 }}
                      onClick={() => setResearch([])}
                      disabled={!research.length}
                      type="button"
                    >
                      Clear Results ({research.length})
                    </button>
                  </>
                ) : leftTab === "memory" ? (
                  <>
                    <button
                      className="icon-button"
                      style={{ height: 28, padding: "0 10px", fontSize: 12, flex: 1 }}
                      onClick={refreshMemory}
                      type="button"
                    >
                      Refresh
                    </button>
                    <button
                      className="icon-button"
                      style={{ height: 28, padding: "0 10px", fontSize: 12, flex: 1 }}
                      onClick={clearAllMemory}
                      disabled={!memoryCount}
                      type="button"
                    >
                      Clear All
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      className="icon-button"
                      style={{ height: 28, padding: "0 10px", fontSize: 12, flex: 1 }}
                      onClick={refreshDocuments}
                      type="button"
                    >
                      Refresh
                    </button>
                    <button
                      className="icon-button"
                      style={{ height: 28, padding: "0 10px", fontSize: 12, flex: 1 }}
                      onClick={() => docInputRef.current?.click()}
                      disabled={!docEnabled}
                      type="button"
                    >
                      Upload
                    </button>
                  </>
                )}
              </div>

              <div className="research-scroll">
                {leftTab === "research" ? (
                  research.length ? (
                    research.map((g) => (
                      <div key={g.id} className="research-card">
                        <div style={{ fontSize: 12, color: colors.textDim, marginBottom: 8, fontWeight: 500 }}>{g.query || "Web search"}</div>
                        {g.results.length ? (
                          g.results.map((r, idx) => {
                            let host = "";
                            try {
                              host = r.url ? new URL(r.url).host : "";
                            } catch {
                              host = "";
                            }
                            return (
                              <div key={`${g.id}-${idx}`} style={{ marginTop: idx ? 14 : 0 }}>
                                <div className="research-title">{r.title || "(untitled)"}</div>
                                {r.snippet ? <div className="research-snippet">{r.snippet}</div> : null}
                                {r.url ? (
                                  <a className="research-source" href={r.url} target="_blank" rel="noreferrer">
                                    {host || r.url}
                                  </a>
                                ) : null}
                              </div>
                            );
                          })
                        ) : (
                          <div className="research-snippet">No sources captured.</div>
                        )}
                      </div>
                    ))
                  ) : (
                    <div className="research-card">
                      <div className="research-snippet">Web search sources will appear here automatically.</div>
                    </div>
                  )
                ) : leftTab === "memory" ? (
                  memoryLoading ? (
                    <div className="research-card">
                      <div className="research-snippet">Loading memory…</div>
                    </div>
                  ) : memoryItems.length ? (
                    memoryItems.map((m) => {
                      const ts = (m.timestamp || String(m.metadata?.timestamp || "")).trim();
                      const preview = (m.text || "").trim();
                      return (
                        <div key={m.id} className="research-card">
                          <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center", marginBottom: 10 }}>
                            <div style={{ fontSize: 12, color: colors.textDim, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              {ts ? ts : "(no timestamp)"}
                            </div>
                            <button
                              className="icon-button"
                              style={{ height: 28, padding: "0 12px", fontSize: 13 }}
                              type="button"
                              onClick={() => deleteMemoryItem(m.id)}
                            >
                              Delete
                            </button>
                          </div>
                          <div className="research-snippet" style={{ whiteSpace: "pre-wrap" }}>{preview || "(empty)"}</div>
                        </div>
                      );
                    })
                  ) : (
                    <div className="research-card">
                      <div className="research-snippet">No saved memories yet.</div>
                    </div>
                  )
                ) : (
                  <div className="research-card">
                    {!docEnabled ? (
                      <div className="research-snippet">Document RAG is disabled. Set DOCUMENT_RAG_ENABLED=true to enable uploads.</div>
                    ) : null}
                    {docFile ? <div className="research-snippet">Selected: {docFile.name}</div> : null}
                    {docError ? <div className="research-snippet">Error: {docError}</div> : null}
                  </div>
                )}
                {leftTab === "docs" && docSources.length ? (
                  <div className="research-card">
                    <div style={{ fontSize: 12, color: colors.textDim, marginBottom: 8, fontWeight: 500 }}>Sources used in last response</div>
                    {docSources.map((s) => (
                      <div key={`${s.id}-${s.chunk ?? ""}`} className="research-snippet">
                        {s.filename || s.source || s.id}
                      </div>
                    ))}
                  </div>
                ) : null}
                {leftTab === "docs" ? (
                  docLoading ? (
                    <div className="research-card">
                      <div className="research-snippet">Loading documents…</div>
                    </div>
                  ) : docItems.length ? (
                    docItems.map((doc) => (
                      <div key={doc.id} className="research-card">
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, marginBottom: 8 }}>
                          <div style={{ fontSize: 13, fontWeight: 600 }}>{doc.filename}</div>
                          <button
                            className="icon-button"
                            style={{ height: 28, padding: "0 12px", fontSize: 13 }}
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
                  )
                ) : null}
              </div>
            </div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", borderLeft: `1px solid ${colors.line}`, borderRight: `1px solid ${colors.line}`, background: "rgba(0,0,0,0.2)" }}>
          <RingVisual speaking={speaking} speechBeat={speechBeat} />
        </div>

        <div className="glow-panel">
          <div className="panel-header">
            <div className="title">
              <span className="panel-dot" />
              <span>Conversation</span>
            </div>
            <div className="provider-status">
              <span>{speaking ? "Speaking" : listening ? "Listening" : "Idle"}</span>
              {streaming && <span className="provider-pill">Live</span>}
            </div>
          </div>
          <div className="panel-body">
            <div className="chat-scroll">
              <AnimatePresence initial={false}>
                {timeline.map((t) =>
                  t.kind === "message" ? (
                    <ChatBubble key={`msg-${t.id}`} msg={t.msg} />
                  ) : (
                    <ActivityCard key={`act-${t.id}`} item={t.item} />
                  )
                )}
              </AnimatePresence>
            </div>
            <div className="input-bar">
              <div className="input-row">
                <input
                  className="input-field"
                  value={input}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setInput(e.target.value)}
                  onKeyDown={(e: React.KeyboardEvent<HTMLInputElement>) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      sendText();
                    }
                  }}
                  placeholder="Ask Echo anything..."
                />
                <button className="send-button" onClick={() => sendText()} type="button">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M5 12L19 12M19 12L13 6M19 12L13 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </button>
              </div>
              <div className="controls-row">
                <button
                  className={`mic-button ${listening ? "active" : ""}`}
                  type="button"
                  onClick={() => listening ? (stop(), setListening(false), setStreaming(false)) : start()}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" fill="currentColor" />
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                </button>
                <button
                  className={`mic-button ${monitoring ? "active" : ""}`}
                  type="button"
                  onClick={() => setMonitoring(v => { const n = !v; if (n) refreshMonitor(); return n; })}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <rect x="2" y="4" width="20" height="12" rx="2" stroke="currentColor" strokeWidth="2" />
                    <path d="M12 16v4M8 20h8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                </button>
                <div className="inline-switcher">
                  <span className={`switcher-dot ${backendOnline === true ? "online" : backendOnline === false ? "offline" : ""}`} />
                  <select
                    value={providerDraft.provider}
                    onChange={(e) => {
                      const p = e.target.value;
                      setProviderDraft(d => ({ ...d, provider: p, model: p === "openai" ? openaiModelOptions[0] : (providerModels[0] || d.model) }));
                    }}
                    disabled={switchingProvider || lmStudioOnly}
                  >
                    {(providerInfo?.available_providers || fallbackProviders)
                      .filter(p => !lmStudioOnly || p.id === "lmstudio")
                      .map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                </div>
              </div>
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

