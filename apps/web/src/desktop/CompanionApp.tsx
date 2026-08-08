import React, { useEffect, useReducer, useRef, useState } from "react";
import { SquareAvatarVisual } from "../components/SquareAvatarVisual";
import type { ToolCategory } from "../components/echoAnimationUtils";
import {
  activityActionsFromStreamEvent,
  agentActivityReducer,
  initialAgentActivity,
  toolCategoryFromPhase,
} from "../agentActivity";
import {
  controlDesktopWindow,
  getEchoSpeakApiBase,
  setDesktopCompanionAlwaysOnTop,
} from "./bridge";

type CompanionMode = "idle" | "listening" | "thinking" | "working" | "error";

const ACTIVE_SESSION_KEY = "echospeak.active_thread_id";
const THINKING_ENABLED_KEY = "echospeak.chat.thinking_enabled";
const REASONING_EFFORT_KEY = "echospeak.chat.reasoning_effort";
const REASONING_EFFORTS = new Set([
  "minimal",
  "low",
  "medium",
  "high",
  "extra_high",
  "max",
  "ultra",
]);

const readThinkingEnabled = (): boolean =>
  window.localStorage.getItem(THINKING_ENABLED_KEY) !== "false";

const readReasoningEffort = (): string => {
  const value = window.localStorage.getItem(REASONING_EFFORT_KEY) || "medium";
  return REASONING_EFFORTS.has(value) ? value : "medium";
};

export function CompanionApp({ backendReady }: { backendReady: boolean }) {
  const apiBase = getEchoSpeakApiBase();
  const [sessionId, setSessionId] = useState(
    () => window.localStorage.getItem(ACTIVE_SESSION_KEY) || "",
  );
  const [thinkingEnabled, setThinkingEnabled] = useState(readThinkingEnabled);
  const [reasoningEffort, setReasoningEffort] = useState(readReasoningEffort);
  const [input, setInput] = useState("");
  const [inputFocused, setInputFocused] = useState(false);
  const [running, setRunning] = useState(false);
  const [activity, dispatchActivity] = useReducer(
    agentActivityReducer,
    undefined,
    initialAgentActivity,
  );
  const [reply, setReply] = useState("");
  const [alwaysOnTop, setAlwaysOnTop] = useState(
    () => window.localStorage.getItem("echospeak.companion.always_on_top") === "true",
  );
  const [avatarConfig, setAvatarConfig] = useState<Record<string, unknown> | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const refreshSharedState = () => {
      setSessionId(window.localStorage.getItem(ACTIVE_SESSION_KEY) || "");
      setThinkingEnabled(readThinkingEnabled());
      setReasoningEffort(readReasoningEffort());
    };
    const handleStorage = (event: StorageEvent) => {
      if (
        event.key === ACTIVE_SESSION_KEY ||
        event.key === THINKING_ENABLED_KEY ||
        event.key === REASONING_EFFORT_KEY
      ) {
        refreshSharedState();
      }
    };
    window.addEventListener("storage", handleStorage);
    const timer = window.setInterval(refreshSharedState, 5000);
    return () => {
      window.removeEventListener("storage", handleStorage);
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    void setDesktopCompanionAlwaysOnTop(alwaysOnTop).catch(() => undefined);
    window.localStorage.setItem("echospeak.companion.always_on_top", String(alwaysOnTop));
  }, [alwaysOnTop]);

  useEffect(() => {
    if (!backendReady) return;
    void fetch(`${apiBase}/avatar/config`)
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => payload && setAvatarConfig(payload))
      .catch(() => undefined);
  }, [apiBase, backendReady]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const send = async () => {
    const message = input.trim();
    if (!message || !sessionId || !backendReady || controllerRef.current) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setRunning(true);
    setInput("");
    setReply("");
    dispatchActivity({ type: "stream_start" });
    try {
      const response = await fetch(`${apiBase}/query/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          message,
          include_memory: true,
          thread_id: sessionId,
          client_request_id: crypto.randomUUID(),
          thinking_enabled: thinkingEnabled,
          reasoning_effort: reasoningEffort,
        }),
      });
      if (!response.ok || !response.body) throw new Error("companion_query_unavailable");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let draft = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let newline = buffer.indexOf("\n");
        while (newline >= 0) {
          const line = buffer.slice(0, newline).trim();
          buffer = buffer.slice(newline + 1);
          newline = buffer.indexOf("\n");
          if (!line) continue;
          const event = JSON.parse(line) as Record<string, unknown>;
          const type = String(event.type || "");
          for (const action of activityActionsFromStreamEvent(event)) {
            dispatchActivity(action);
          }
          if (type === "agent_token") {
            draft += String(event.data || "");
            setReply(draft.slice(-420));
          } else if (type === "final") {
            setReply(String(event.response || draft || "").slice(0, 420));
          } else if (type === "error") {
            setReply("Echo couldn't finish that turn.");
          }
        }
      }
    } catch {
      if (!controller.signal.aborted) {
        setReply("Echo couldn't finish that turn.");
        dispatchActivity({ type: "error", message: "Echo couldn't finish that turn." });
      }
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
      setRunning(false);
      dispatchActivity({ type: "stream_end" });
    }
  };

  const mode: CompanionMode = activity.phase === "error" || activity.phase === "blocked"
    ? "error"
    : ["tool_search", "tool_file", "tool_terminal", "tool_generic", "task_running"].includes(activity.phase)
      ? "working"
      : activity.streaming || ["understanding", "planning", "waiting_for_model", "thinking", "streaming_reply"].includes(activity.phase)
        ? "thinking"
        : inputFocused
          ? "listening"
          : "idle";
  const activeTool = toolCategoryFromPhase(activity.phase) as ToolCategory;

  const status = !backendReady
    ? "Starting EchoSpeak"
    : !sessionId
      ? "Select a Chat Session in EchoSpeak"
      : mode === "listening"
        ? "Listening"
        : mode === "thinking" || mode === "working"
          ? activity.label || (mode === "working" ? "Working" : "Thinking")
            : mode === "error"
              ? "Needs another try"
              : "Echo is ready";

  return (
    <main className="echo-companion" data-mode={mode}>
      <div className="echo-companion-controls">
        <button
          type="button"
          className={alwaysOnTop ? "is-active" : ""}
          onClick={() => setAlwaysOnTop((value) => !value)}
          title="Keep Echo above other windows"
          aria-label="Keep Echo above other windows"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="m9 3 6 6M7 8l9 9M14 4l6 6-4 2-4 4-2 4-6-6 4-2 4-4zM4 20l5-5"/></svg>
        </button>
        <button
          type="button"
          onClick={() => void controlDesktopWindow("close")}
          title="Hide Echo companion"
          aria-label="Hide Echo companion"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden><path d="M6 6l12 12M18 6 6 18"/></svg>
        </button>
      </div>
      <div className="echo-companion-avatar" data-tauri-drag-region title="Drag Echo">
        <div data-tauri-drag-region>
          <SquareAvatarVisual
            speaking={false}
            backendOnline={backendReady}
            isThinking={mode === "thinking" || mode === "working"}
            thinkingText={status}
            toolCategory={activeTool}
            userIsTyping={Boolean(input)}
            reaction={mode === "error" ? "error" : null}
            avatarConfig={{ ...(avatarConfig || {}), bg_color: "rgba(0,0,0,0)" }}
          />
        </div>
      </div>
      <div className="echo-companion-status" aria-live="polite">{status}</div>
      {reply ? <div className="echo-companion-reply">{reply}</div> : null}
      <div className="echo-companion-input-shell">
        <textarea
          value={input}
          rows={1}
          disabled={!backendReady || !sessionId || running}
          placeholder={sessionId ? "Message Echo…" : "Select a Session first"}
          onFocus={() => setInputFocused(true)}
          onBlur={() => setInputFocused(false)}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void send();
            }
          }}
          aria-label="Message Echo from the desktop companion"
        />
        <button type="button" onClick={() => void send()} disabled={!input.trim() || !sessionId || !backendReady || running} aria-label="Send message" title="Send">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M5 12h14M13 6l6 6-6 6"/></svg>
        </button>
      </div>
    </main>
  );
}
