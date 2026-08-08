import React, { useEffect, useReducer, useRef } from "react";
import { Dashboard } from "../index";
import {
  controlDesktopWindow,
  hydrateDesktopBootstrap,
  installDesktopTransport,
  openDesktopLogs,
  readDesktopWindowLabel,
  readDesktopReadiness,
  readDesktopRuntime,
  restartDesktopBackend,
} from "./bridge";
import { initialDesktopBootState, reduceDesktopBootState } from "./runtimeState";
import { CompanionApp } from "./CompanionApp";
import "./desktop.css";

const WindowControls = () => (
  <div className="desktop-window-controls" role="group" aria-label="Window controls">
    <button type="button" aria-label="Minimize" title="Minimize" onClick={() => void controlDesktopWindow("minimize")}>
      <svg viewBox="0 0 12 12" aria-hidden><path d="M2 6.5h8" /></svg>
    </button>
    <button type="button" aria-label="Maximize or restore" title="Maximize or restore" onClick={() => void controlDesktopWindow("toggle_maximize")}>
      <svg viewBox="0 0 12 12" aria-hidden><rect x="2.5" y="2.5" width="7" height="7" /></svg>
    </button>
    <button className="is-close" type="button" aria-label="Close" title="Close" onClick={() => void controlDesktopWindow("close")}>
      <svg viewBox="0 0 12 12" aria-hidden><path d="m2.5 2.5 7 7m0-7-7 7" /></svg>
    </button>
  </div>
);

export function DesktopApp() {
  document.documentElement.classList.add("echospeak-desktop-root");
  const [windowKind, setWindowKind] = React.useState<"main" | "settings" | "companion" | null>(null);
  const [boot, dispatch] = useReducer(reduceDesktopBootState, initialDesktopBootState);
  const startupStartedAtRef = useRef(Date.now());
  const bootstrappedInstanceRef = useRef("");

  useEffect(() => {
    let disposed = false;
    void readDesktopWindowLabel()
      .then((label) => {
        if (!disposed) {
          setWindowKind(label === "settings" || label === "companion" ? label : "main");
        }
      })
      .catch(() => {
        if (!disposed) setWindowKind("main");
      });
    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    let stableReadyTicks = 0;
    const poll = async () => {
      try {
        const runtime = await readDesktopRuntime();
        if (cancelled) return;
        installDesktopTransport(runtime);
        dispatch({ type: "snapshot", runtime });
        if (runtime.backend_phase === "ready") {
          const controller = new AbortController();
          const timeout = window.setTimeout(() => controller.abort(), 6000);
          try {
            const readiness = await readDesktopReadiness(runtime, controller.signal);
            if (readiness.core_ready && bootstrappedInstanceRef.current !== runtime.instance_id) {
              await hydrateDesktopBootstrap(runtime, readiness, controller.signal);
              bootstrappedInstanceRef.current = runtime.instance_id;
            }
            if (!cancelled) dispatch({ type: "readiness", readiness });
            if (readiness.core_ready) {
              stableReadyTicks += 1;
            } else {
              stableReadyTicks = 0;
            }
          } catch (error) {
            stableReadyTicks = 0;
            if (!cancelled) {
              const elapsed = Date.now() - startupStartedAtRef.current;
              if (elapsed >= 90_000) dispatch({ type: "startup_timeout" });
              else dispatch({ type: "readiness_error", message: error instanceof Error ? error.message : String(error) });
            }
          } finally {
            window.clearTimeout(timeout);
          }
        } else {
          stableReadyTicks = 0;
        }
      } catch (error) {
        stableReadyTicks = 0;
        if (!cancelled) dispatch({ type: "bridge_error", message: error instanceof Error ? error.message : String(error) });
      } finally {
        if (cancelled) return;
        // After two consecutive ready ticks, stop 1Hz polling. Manual recovery
        // (retry) restarts this effect by remounting / re-dispatching.
        if (stableReadyTicks >= 2) {
          timer = window.setTimeout(poll, 30_000);
          return;
        }
        timer = window.setTimeout(poll, 750);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  const retry = async () => {
    startupStartedAtRef.current = Date.now();
    bootstrappedInstanceRef.current = "";
    dispatch({ type: "retry_requested" });
    try {
      const runtime = await restartDesktopBackend();
      installDesktopTransport(runtime);
      dispatch({ type: "snapshot", runtime });
    } catch (error) {
      dispatch({ type: "bridge_error", message: error instanceof Error ? error.message : String(error) });
    }
  };

  const showWorkspace = boot.hasBeenReady;
  if (windowKind === null) return null;
  if (windowKind === "companion") {
    document.documentElement.classList.add("echospeak-companion-root");
    return <CompanionApp backendReady={showWorkspace && boot.phase === "ready"} />;
  }
  const settingsWindow = windowKind === "settings";
  return (
    <div className={`desktop-window${settingsWindow ? " desktop-settings-window" : ""}`}>
      <header className="desktop-titlebar" data-tauri-drag-region>
        <div className="desktop-titlebar-brand" data-tauri-drag-region>
          <img src="/logo.png" alt="" draggable={false} />
          <span data-tauri-drag-region>EchoSpeak</span>
          <small data-tauri-drag-region>{settingsWindow ? "Settings" : "Desktop"}</small>
        </div>
        <div className={`desktop-titlebar-status is-${boot.phase}`} data-tauri-drag-region>
          <i aria-hidden />
          <span data-tauri-drag-region>{boot.phase === "ready" ? "Local service ready" : boot.detail}</span>
        </div>
        <WindowControls />
      </header>

      <main className="desktop-content">
        {showWorkspace ? <Dashboard desktopSettingsWindow={settingsWindow} /> : (
          <section className="desktop-boot-state" aria-live="polite">
            <div className="desktop-boot-echo" aria-hidden><img src="/logo.png" alt="" draggable={false} /></div>
            <div className="desktop-boot-progress" aria-hidden><span /></div>
            <p className="desktop-boot-detail">{boot.detail}</p>
            {boot.readiness && !boot.readiness.core_ready ? (
              <p className="desktop-boot-step">{boot.readiness.completed_steps} of {boot.readiness.total_steps}</p>
            ) : null}
            {boot.phase === "failed" ? (
              <div className="desktop-recovery-actions">
                <button type="button" className="is-primary" onClick={() => void retry()}>Restart service</button>
                <button type="button" onClick={() => void openDesktopLogs()}>Open logs</button>
              </div>
            ) : null}
          </section>
        )}
        {showWorkspace && boot.phase !== "ready" ? (
          <aside className={`desktop-service-banner is-${boot.phase}`} role="status">
            <span>{boot.detail}</span>
            {boot.phase === "failed" ? (
              <div>
                <button type="button" onClick={() => void openDesktopLogs()}>Logs</button>
                <button type="button" onClick={() => void retry()}>Restart</button>
              </div>
            ) : null}
          </aside>
        ) : null}
      </main>
    </div>
  );
}
