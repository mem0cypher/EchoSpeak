import React, { useMemo, useState } from "react";

type Project = { id: string; name: string; workspace_root?: string; archived?: boolean; git_metadata?: Record<string, any> };
type Session = { id: string; name: string; at: number; projectId?: string };

type SidebarProps = {
  desktop?: boolean;
  hydrating?: boolean;
  collapsed: boolean;
  projects: Project[];
  sessions: Session[];
  activeProjectId: string;
  activeSessionId: string;
  activeView: string;
  onToggleCollapsed(): void;
  onNewSession(projectId?: string): void;
  onAddFolder(): void;
  onSelectSession(id: string): void;
  onRenameSession(id: string, title: string): void;
  onDeleteSession(id: string): void;
  onDeleteProject(id: string): void;
  onView(view: "chat" | "avatar" | "research" | "code" | "tasks" | "media" | "studio"): void;
};

const surface = "#0a0a0a";
const border = "rgba(255,255,255,.10)";
const muted = "rgba(255,255,255,.48)";

/** Minimal monochrome icons — readable in the 50px collapsed rail. */
function Icon({
  name,
  size = 15,
  active = false,
}: {
  name:
    | "chat"
    | "session"
    | "folder"
    | "avatar"
    | "research"
    | "code"
    | "tasks"
    | "media"
    | "studio"
    | "plus"
    | "more"
    | "trash"
    | "chevron"
    | "expand"
    | "collapse";
  size?: number;
  active?: boolean;
}) {
  const stroke = active ? "#fff" : "rgba(255,255,255,0.72)";
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none" as const,
    stroke,
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true as const,
  };

  switch (name) {
    case "chat":
      return (
        <svg {...common}>
          <path d="M5 6.5h14a1.5 1.5 0 0 1 1.5 1.5v7a1.5 1.5 0 0 1-1.5 1.5H10l-4 3v-3H5A1.5 1.5 0 0 1 3.5 15V8A1.5 1.5 0 0 1 5 6.5z" />
        </svg>
      );
    case "session":
      return (
        <svg {...common}>
          <rect x="4.5" y="5.5" width="15" height="13" rx="2" />
          <path d="M8 10h8M8 13.5h5" />
        </svg>
      );
    case "folder":
      return (
        <svg {...common}>
          <path d="M3.5 8.5V7a1.5 1.5 0 0 1 1.5-1.5h4l2 2H19A1.5 1.5 0 0 1 20.5 9v8a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 17V8.5z" />
        </svg>
      );
    case "avatar":
      return (
        <svg {...common}>
          <rect x="4" y="4" width="16" height="16" rx="2.5" />
          <circle cx="9.5" cy="11" r="1.15" fill={stroke} stroke="none" />
          <circle cx="14.5" cy="11" r="1.15" fill={stroke} stroke="none" />
          <path d="M9.2 15c.7 1 1.8 1.5 2.8 1.5s2.1-.5 2.8-1.5" />
        </svg>
      );
    case "research":
      return (
        <svg {...common}>
          <circle cx="11" cy="11" r="6" />
          <path d="M20 20l-3.5-3.5" />
        </svg>
      );
    case "code":
      return (
        <svg {...common}>
          <path d="M9 7.5 4.5 12 9 16.5" />
          <path d="M15 7.5 19.5 12 15 16.5" />
        </svg>
      );
    case "tasks":
      return (
        <svg {...common}>
          <path d="M9.5 12.5 11.5 14.5 16 9.5" />
          <rect x="4" y="4" width="16" height="16" rx="2.5" />
        </svg>
      );
    case "media":
      return (
        <svg {...common}>
          <rect x="3.5" y="4.5" width="17" height="15" rx="2" />
          <path d="m6.5 16 4-4 2.5 2.5 2-2 2.5 3.5" />
          <circle cx="15.5" cy="9" r="1.3" />
        </svg>
      );
    case "studio":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="3" />
          <path d="M12 4.5v2.2M12 17.3v2.2M4.5 12h2.2M17.3 12h2.2M6.7 6.7l1.6 1.6M15.7 15.7l1.6 1.6M17.3 6.7l-1.6 1.6M8.3 15.7l-1.6 1.6" />
        </svg>
      );
    case "plus":
      return (
        <svg {...common}>
          <path d="M12 6v12M6 12h12" />
        </svg>
      );
    case "more":
      return (
        <svg {...common}>
          <circle cx="6" cy="12" r="1" fill={stroke} stroke="none" />
          <circle cx="12" cy="12" r="1" fill={stroke} stroke="none" />
          <circle cx="18" cy="12" r="1" fill={stroke} stroke="none" />
        </svg>
      );
    case "trash":
      return (
        <svg {...common}>
          <path d="M5.5 7.5h13M9 7.5V5.7h6v1.8M7.5 7.5l.7 11h7.6l.7-11M10 10.5v5M14 10.5v5" />
        </svg>
      );
    case "chevron":
      return (
        <svg {...common}>
          <path d="m8.5 10 3.5 3.5 3.5-3.5" />
        </svg>
      );
    case "expand":
      return (
        <svg {...common}>
          <path d="M9 6.5 14.5 12 9 17.5" />
        </svg>
      );
    case "collapse":
      return (
        <svg {...common}>
          <path d="M15 6.5 9.5 12 15 17.5" />
        </svg>
      );
    default:
      return null;
  }
}

const viewDefs: {
  id: "avatar" | "research" | "code" | "tasks" | "media" | "studio";
  label: string;
  icon: "avatar" | "research" | "code" | "tasks" | "media" | "studio";
  title: string;
}[] = [
  { id: "avatar", label: "Avatar", icon: "avatar", title: "Avatar" },
  { id: "research", label: "Research", icon: "research", title: "Research" },
  { id: "code", label: "Code", icon: "code", title: "Code" },
  { id: "tasks", label: "Tasks", icon: "tasks", title: "Tasks" },
  { id: "media", label: "Media", icon: "media", title: "Media library" },
  { id: "studio", label: "Studio", icon: "studio", title: "Studio · Settings & Models" },
];

const desktopViewDefs: {
  id: "chat" | "avatar" | "research" | "code" | "tasks" | "media" | "studio";
  label: string;
  icon: "chat" | "avatar" | "research" | "code" | "tasks" | "media" | "studio";
  title: string;
}[] = [
  { id: "chat", label: "Chat", icon: "chat", title: "Conversation" },
  { id: "avatar", label: "Visualizer", icon: "avatar", title: "Echo Visualizer" },
  { id: "research", label: "Research", icon: "research", title: "Research workspace" },
  { id: "code", label: "Code", icon: "code", title: "Code workspace" },
  { id: "media", label: "Media", icon: "media", title: "Media library" },
  { id: "tasks", label: "Tasks", icon: "tasks", title: "Tasks workspace" },
  { id: "studio", label: "Studio", icon: "studio", title: "Studio and settings" },
];

export function ProjectSidebar(props: SidebarProps) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [projectsOpen, setProjectsOpen] = useState(true);
  const [viewsOpen, setViewsOpen] = useState(true);
  const iconOnly = props.collapsed;
  const projects = useMemo(() => props.projects.filter((project) => !project.archived), [props.projects]);
  const sessions = props.sessions;
  const looseSessions = sessions.filter((session) => !session.projectId);
  const activeProject = projects.find((project) => project.id === props.activeProjectId);
  const activeSession = sessions.find((session) => session.id === props.activeSessionId);
  const contextLabel = activeProject?.name || "Quick chat";
  const workspaceViews = props.desktop ? desktopViewDefs : viewDefs;

  const railButton = (active = false): React.CSSProperties => ({
    // Do not force width:100% here — row items share space with fixed action buttons.
    width: iconOnly ? "100%" : undefined,
    maxWidth: "100%",
    minHeight: iconOnly ? 38 : 36,
    border: 0,
    borderRadius: 2,
    background: active
      ? "linear-gradient(90deg, rgba(255,255,255,.10), rgba(255,255,255,.025) 62%, transparent)"
      : "transparent",
    color: active ? "#fff" : "rgba(255,255,255,.62)",
    display: "flex",
    alignItems: "center",
    justifyContent: iconOnly ? "center" : "flex-start",
    gap: 8,
    padding: iconOnly ? 0 : "0 8px",
    cursor: "pointer",
    fontFamily: "'JetBrains Mono', ui-monospace, monospace",
    fontSize: 12.5,
    textAlign: "left",
    minWidth: 0,
    boxSizing: "border-box",
  });

  const iconSlot = (active = false): React.CSSProperties => ({
    width: iconOnly ? 23 : 21,
    height: iconOnly ? 23 : 21,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    opacity: active ? 1 : 0.88,
  });

  /** Title text: always truncates; never steals space from trailing action buttons. */
  const titleEllipsis: React.CSSProperties = {
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    minWidth: 0,
    flex: "1 1 auto",
  };

  const sessionRow = (session: Session, nested = false) => (
    <div
      key={session.id}
      className="echo-side-row"
      style={{
        display: "flex",
        alignItems: "center",
        width: "100%",
        maxWidth: "100%",
        minWidth: 0,
        // Indent title only via padding — marginLeft + width 100% was clipping × off the right.
        paddingLeft: !iconOnly && nested ? 13 : 0,
        boxSizing: "border-box",
      }}
    >
      <button
        className={`echo-side-button ${props.activeSessionId === session.id ? "is-active" : ""}`}
        type="button"
        style={{
          ...railButton(props.activeSessionId === session.id),
          flex: "1 1 auto",
          minWidth: 0,
          width: "auto",
          // Nested: slightly less left pad so label still reads as indented under project
          paddingLeft: !iconOnly && nested ? 4 : undefined,
        }}
        onClick={() => props.onSelectSession(session.id)}
        title={session.name}
        aria-label={`Session: ${session.name}`}
      >
        <span style={iconSlot(props.activeSessionId === session.id)}>
          <Icon name="session" size={iconOnly ? 16 : 15} active={props.activeSessionId === session.id} />
        </span>
        {!iconOnly && <span style={titleEllipsis}>{session.name}</span>}
      </button>
      {!iconOnly && (
        <div className="echo-row-actions" aria-label="Session actions">
          <button
            type="button"
            title="Rename Session"
            aria-label={`Rename ${session.name}`}
            onClick={() => {
              const title = window.prompt("Rename Session", session.name)?.trim();
              if (title && title !== session.name) props.onRenameSession(session.id, title);
            }}
          >
            <Icon name="more" size={15} />
          </button>
          <button
            type="button"
            title="Delete Session"
            aria-label={`Delete ${session.name}`}
            onClick={() => {
              if (window.confirm(`Delete “${session.name}”?`)) props.onDeleteSession(session.id);
            }}
          >
            <Icon name="trash" size={14} />
          </button>
        </div>
      )}
    </div>
  );

  return (
    <aside
      className="echo-sidebar"
      aria-label="Project and Session sidebar"
      style={{
        minWidth: 0,
        width: "100%",
        height: "100%",
        overflow: "hidden",
        borderRight: `1px solid ${border}`,
        background: `linear-gradient(180deg, rgba(255,255,255,.018) 0%, transparent 18%), ${surface}`,
        /* No right padding — scroll track must sit on the sidebar edge */
        padding: iconOnly ? "6px 0 6px 6px" : "10px 0 10px 10px",
        display: "flex",
        flexDirection: "column",
        gap: iconOnly ? 8 : 12,
      }}
    >
      <style>{`
      .echo-side-button { transition: background .14s ease, color .14s ease, opacity .14s ease; }
      .echo-side-button:hover { background: linear-gradient(90deg, rgba(255,255,255,.065), rgba(255,255,255,.018) 68%, transparent) !important; color: #fff !important; }
      .echo-side-button:focus-visible, .echo-row-actions button:focus-visible { outline: 1px solid rgba(255,255,255,.72); outline-offset: 2px; }
      .echo-side-button.is-active { font-weight: 600; position: relative; }
      .echo-side-button.is-active::before {
        content: "";
        position: absolute;
        left: 0;
        top: 9px;
        bottom: 9px;
        width: 2px;
        border-radius: 2px;
        background: rgba(255,255,255,.9);
        box-shadow: 0 0 12px rgba(255,255,255,.22);
      }
      .echo-side-button:active { background: linear-gradient(90deg, rgba(255,255,255,.09), rgba(255,255,255,.025) 68%, transparent) !important; }
      /* Fixed trailing slot — never shrinks when titles are long */
      .echo-row-actions {
        display: flex;
        flex: 0 0 auto;
        flex-shrink: 0;
        align-items: center;
        opacity: 0.55;
        transition: opacity .14s ease;
      }
      .echo-side-row:hover .echo-row-actions,
      .echo-side-row:focus-within .echo-row-actions { opacity: 1; }
      .echo-row-actions button {
        width: 24px;
        height: 30px;
        border: 0;
        background: transparent;
        color: rgba(255,255,255,.7);
        cursor: pointer;
        padding: 0;
        font-size: 13px;
        flex-shrink: 0;
        display: grid;
        place-items: center;
        border-radius: 2px;
      }
      .echo-row-actions button:hover { color: #fff; background: #181818; }
      .echo-side-row {
        min-width: 0;
        max-width: 100%;
        overflow: hidden;
        box-sizing: border-box;
      }
      /* Keep action cluster flush to content edge (padding clears the scrollbar) */
      .echo-side-row .echo-row-actions {
        margin-left: auto;
        margin-right: 0;
      }
      .echo-sidebar-scroll {
        flex: 1 1 auto;
        min-height: 0;
        overflow-x: hidden;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: ${iconOnly ? 8 : 12}px;
        /* Content inset only — scrollbar stays on the sidebar's right edge */
        padding-right: ${iconOnly ? 2 : 6}px;
        scrollbar-width: thin;
        scrollbar-color: rgba(255,255,255,.12) transparent;
        scrollbar-gutter: auto;
      }
      .echo-sidebar-scroll::-webkit-scrollbar {
        width: 4px;
      }
      .echo-sidebar-scroll::-webkit-scrollbar-track {
        background: transparent;
        margin: 0;
      }
      .echo-sidebar-scroll::-webkit-scrollbar-thumb {
        background: rgba(255,255,255,.12);
        border-radius: 0;
      }
      .echo-sidebar-scroll::-webkit-scrollbar-thumb:hover {
        background: rgba(255,255,255,.2);
      }
      /* Brand / footer keep the previous right inset since they sit outside the scroller */
      .echo-sidebar-edge-pad {
        padding-right: ${iconOnly ? 6 : 10}px;
        box-sizing: border-box;
      }
      .echo-sidebar-footer {
        flex: 0 0 auto;
        border: 1px solid rgba(255,255,255,.085);
        background: linear-gradient(145deg, rgba(255,255,255,.045), rgba(255,255,255,.012));
        box-shadow: inset 0 1px 0 rgba(255,255,255,.025), 0 12px 28px rgba(0,0,0,.18);
      }
      .echo-footer-action {
        border: 1px solid rgba(255,255,255,.09);
        background: rgba(255,255,255,.025);
        color: rgba(255,255,255,.72);
        transition: background .14s ease, border-color .14s ease, color .14s ease;
      }
      .echo-footer-action:hover {
        background: rgba(255,255,255,.075);
        border-color: rgba(255,255,255,.16);
        color: #fff;
      }
      .echo-footer-action:focus-visible { outline: 1px solid rgba(255,255,255,.72); outline-offset: 2px; }
      .echo-rail-divider {
        height: 1px;
        background: rgba(255,255,255,.07);
        margin: ${iconOnly ? "4px 6px" : "5px 3px"};
      }
    `}</style>

      {/* Browser branding remains here. Desktop identity belongs to the native title bar. */}
      {!props.desktop ? <div
        className="echo-sidebar-edge-pad"
        style={{
          height: 38,
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: iconOnly ? "center" : "space-between",
          paddingLeft: iconOnly ? 0 : 4,
          gap: 6,
        }}
      >
        {iconOnly ? (
          <button
            className="echo-side-button"
            type="button"
            title="Expand sidebar"
            aria-label="Expand sidebar"
            onClick={props.onToggleCollapsed}
            style={{
              width: 34,
              height: 34,
              borderRadius: 2,
              border: 0,
              background: "transparent",
              cursor: "pointer",
              display: "grid",
              placeItems: "center",
              padding: 0,
            }}
          >
            <img src="/logo.png" alt="EchoSpeak" style={{ width: 18, height: 18, borderRadius: 2, display: "block" }} />
          </button>
        ) : (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 9, minWidth: 0 }}>
              <div style={{ width: 24, height: 24, display: "grid", placeItems: "center", border: "1px solid rgba(255,255,255,.1)", background: "rgba(255,255,255,.035)", borderRadius: 3 }}>
                <img src="/logo.png" alt="" style={{ width: 15, height: 15, borderRadius: 2 }} />
              </div>
              <div style={{ minWidth: 0, lineHeight: 1.05 }}>
                <strong style={{ display: "block", fontFamily: "'Space Grotesk', sans-serif", fontSize: 15.5, letterSpacing: "-.01em" }}>EchoSpeak</strong>
                <span style={{ display: "block", marginTop: 4, color: "rgba(255,255,255,.35)", fontSize: 8.5, letterSpacing: ".13em", textTransform: "uppercase" }}>Local workspace</span>
              </div>
            </div>
            <button
              className="echo-side-button"
              type="button"
              title="Collapse sidebar"
              aria-label="Collapse sidebar"
              onClick={props.onToggleCollapsed}
              style={{
                width: 28,
                height: 28,
                borderRadius: 2,
                border: 0,
                color: "rgba(255,255,255,.72)",
                background: "transparent",
                cursor: "pointer",
                display: "grid",
                placeItems: "center",
                padding: 0,
              }}
            >
              <Icon name="collapse" size={15} />
            </button>
          </>
        )}
      </div> : null}

      <div className="echo-sidebar-scroll">
        <section aria-label="Workspace" style={{ padding: iconOnly ? "0 1px" : 0, display: "grid", gap: 7, flex: "0 0 auto" }}>
          <div style={{ display: "grid", gap: 2 }}>
            {props.desktop && iconOnly ? (
              <button
                className="echo-side-button"
                type="button"
                style={railButton()}
                onClick={props.onToggleCollapsed}
                title="Expand sidebar"
                aria-label="Expand sidebar"
              >
                <span style={iconSlot()}><Icon name="expand" size={16} /></span>
              </button>
            ) : null}
            {!iconOnly && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "4px 4px 2px",
                  minWidth: 0,
                  width: "100%",
                  boxSizing: "border-box",
                }}
              >
                <span
                  style={{
                    fontSize: 10,
                    color: muted,
                    letterSpacing: ".1em",
                    textTransform: "uppercase",
                    minWidth: 0,
                    flex: "1 1 auto",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  Quick Chats
                </span>
                <button
                  className="echo-side-button"
                  type="button"
                  onClick={() => props.onNewSession()}
                  title="Start new chat"
                  aria-label="Start new chat"
                  style={{
                    width: 26,
                    height: 24,
                    border: 0,
                    background: "transparent",
                    color: "rgba(255,255,255,.7)",
                    borderRadius: 2,
                    cursor: "pointer",
                    display: "grid",
                    placeItems: "center",
                    padding: 0,
                    flex: "0 0 auto",
                    flexShrink: 0,
                  }}
                >
                  <Icon name="plus" size={15} />
                </button>
                {props.desktop ? (
                  <button
                    className="echo-side-button"
                    type="button"
                    onClick={props.onToggleCollapsed}
                    title="Collapse sidebar"
                    aria-label="Collapse sidebar"
                    style={{
                      width: 26,
                      height: 24,
                      border: 0,
                      background: "transparent",
                      color: "rgba(255,255,255,.7)",
                      borderRadius: 2,
                      cursor: "pointer",
                      display: "grid",
                      placeItems: "center",
                      padding: 0,
                      flex: "0 0 auto",
                    }}
                  >
                    <Icon name="collapse" size={14} />
                  </button>
                ) : null}
              </div>
            )}

            {iconOnly && (
              <button
                className="echo-side-button"
                type="button"
                style={railButton()}
                onClick={() => props.onNewSession()}
                title="New chat"
                aria-label="New chat"
              >
                <span style={iconSlot()}>
                  <Icon name="plus" size={16} />
                </span>
              </button>
            )}

            <button
              className={`echo-side-button ${!props.activeProjectId ? "is-active" : ""}`}
              type="button"
              style={{ ...railButton(!props.activeProjectId), width: "100%" }}
              onClick={() => {
                const existing = looseSessions[0];
                if (existing) props.onSelectSession(existing.id);
              }}
              title={looseSessions.length ? "Open the latest Quick Chat" : "No Quick Chats yet · use + to create one"}
              aria-label="Quick chats outside projects"
            >
              <span style={iconSlot(!props.activeProjectId)}>
                <Icon name="chat" size={iconOnly ? 16 : 15} active={!props.activeProjectId} />
              </span>
              {!iconOnly && <span style={titleEllipsis}>Outside Projects</span>}
            </button>

            {looseSessions.map((session) => sessionRow(session))}

            <div className="echo-rail-divider" />

            {!iconOnly && (
              <button
                className="echo-side-button"
                type="button"
                onClick={() => setProjectsOpen((value) => !value)}
                aria-expanded={projectsOpen}
                style={{ ...railButton(), minHeight: 28, padding: "0 4px", color: muted }}
              >
                <span style={{ flex: 1, fontSize: 10, letterSpacing: ".1em", textTransform: "uppercase" }}>Projects</span>
                <span style={{ minWidth: 18, height: 18, padding: "0 5px", display: "inline-grid", placeItems: "center", borderRadius: 9, background: "rgba(255,255,255,.045)", color: "rgba(255,255,255,.45)", fontSize: 9 }}>{projects.length}</span>
                <span style={{ display: "grid", placeItems: "center", transform: projectsOpen ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform .15s ease" }}><Icon name="chevron" size={13} /></span>
              </button>
            )}

            {!iconOnly && projectsOpen && !projects.length && !props.hydrating && (
              <button
                className="echo-footer-action"
                type="button"
                onClick={props.onAddFolder}
                style={{ margin: "2px 3px 4px", minHeight: 46, borderRadius: 3, padding: "8px 10px", cursor: "pointer", textAlign: "left", fontFamily: "'JetBrains Mono', ui-monospace, monospace" }}
              >
                <span style={{ display: "block", fontSize: 10.5, color: "rgba(255,255,255,.72)" }}>Add your first Project</span>
                <span style={{ display: "block", marginTop: 4, fontSize: 9, color: "rgba(255,255,255,.36)" }}>Attach a local folder</span>
              </button>
            )}

            {!iconOnly && props.hydrating ? (
              <div role="status" style={{ padding: "7px 7px 5px", color: muted, fontSize: 9.5 }}>
                Restoring Projects and Sessions…
              </div>
            ) : null}

            {(iconOnly || projectsOpen) &&
              projects.map((project) => {
                const childSessions = sessions.filter((session) => session.projectId === project.id);
                const open = expanded[project.id] ?? props.activeProjectId === project.id;
                const isActive = props.activeProjectId === project.id;
                return (
                  <div key={project.id} style={{ minWidth: 0, maxWidth: "100%" }}>
                    <div
                      className="echo-side-row"
                      style={{
                        display: "flex",
                        alignItems: "center",
                        width: "100%",
                        maxWidth: "100%",
                        minWidth: 0,
                        boxSizing: "border-box",
                      }}
                    >
                      <button
                        className={`echo-side-button ${isActive ? "is-active" : ""}`}
                        type="button"
                        style={{
                          ...railButton(isActive),
                          flex: "1 1 auto",
                          minWidth: 0,
                          width: "auto",
                        }}
                        onClick={() => {
                          setExpanded((value) => ({ ...value, [project.id]: !open }));
                          // A Project row is navigation, never Session creation.
                          // Select an existing child only; the adjacent + owns creation.
                          if (!isActive && childSessions[0]) props.onSelectSession(childSessions[0].id);
                        }}
                        title={project.workspace_root || project.name}
                        aria-label={`Project: ${project.name}`}
                      >
                        <span style={iconSlot(isActive)}>
                          <Icon name="folder" size={iconOnly ? 16 : 15} active={isActive} />
                        </span>
                        {!iconOnly && <span style={titleEllipsis}>{project.name}</span>}
                      </button>
                      {!iconOnly && (
                        <div className="echo-row-actions" aria-label="Project actions">
                          <button type="button" title="New Session in Project" aria-label={`New Session in ${project.name}`} onClick={() => props.onNewSession(project.id)}>
                            <Icon name="plus" size={14} />
                          </button>
                          <button
                            type="button"
                            title="Delete Project"
                            aria-label={`Delete ${project.name}`}
                            onClick={() => {
                              if (
                                window.confirm(
                                  `Delete Project “${project.name}”? Its Sessions will become independent.`,
                                )
                              )
                                props.onDeleteProject(project.id);
                            }}
                          >
                            <Icon name="trash" size={14} />
                          </button>
                        </div>
                      )}
                    </div>
                    {/* Sessions under a project: hide in rail to keep icons clear; open sidebar to manage */}
                    {!iconOnly && open && childSessions.map((session) => sessionRow(session, true))}
                    {!iconOnly && open && !childSessions.length && (
                      <button
                        type="button"
                        className="echo-side-button"
                        onClick={() => props.onNewSession(project.id)}
                        style={{
                          ...railButton(),
                          width: "100%",
                          maxWidth: "100%",
                          boxSizing: "border-box",
                          paddingLeft: 21,
                          color: muted,
                        }}
                      >
                        + Session in Project
                      </button>
                    )}
                  </div>
                );
              })}

          </div>
        </section>

        <div className="echo-rail-divider" />

        <nav aria-label="Workspace views" style={{ display: "grid", gap: 3, flex: "0 0 auto" }}>
          {!iconOnly && (
            <button
              className="echo-side-button"
              type="button"
              onClick={() => setViewsOpen((value) => !value)}
              aria-expanded={viewsOpen}
              style={{ ...railButton(), minHeight: 28, padding: "0 4px", color: muted }}
            >
              <span style={{ flex: 1, fontSize: 10, letterSpacing: ".12em", textTransform: "uppercase" }}>Views</span>
              <span style={{ display: "grid", placeItems: "center", transform: viewsOpen ? "rotate(0deg)" : "rotate(-90deg)", transition: "transform .15s ease" }}><Icon name="chevron" size={13} /></span>
            </button>
          )}
          {(iconOnly || viewsOpen) &&
            workspaceViews.map((view) => {
              const active = props.activeView === view.id;
              return (
                <button
                  className={`echo-side-button ${active ? "is-active" : ""}`}
                  type="button"
                  key={view.id}
                  style={{ ...railButton(active), width: "100%" }}
                  onClick={() => props.onView(view.id)}
                  title={view.title}
                  aria-label={view.title}
                >
                  <span style={iconSlot(active)}>
                    <Icon name={view.icon} size={iconOnly ? 16 : 15} active={active} />
                  </span>
                  {!iconOnly && <span style={titleEllipsis}>{view.label}</span>}
                </button>
              );
            })}
        </nav>
      </div>

      {!iconOnly ? (
        <footer className="echo-sidebar-footer" style={{ borderRadius: 4, padding: 10, marginRight: 10 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <span style={{ color: "rgba(255,255,255,.35)", fontSize: 8.5, letterSpacing: ".13em", textTransform: "uppercase" }}>Active context</span>
            <span style={{ color: "rgba(255,255,255,.28)", fontSize: 8 }}>{projects.length} projects · {sessions.length} sessions</span>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8, minWidth: 0 }}>
            <span aria-hidden style={{ width: 6, height: 6, flex: "0 0 auto", borderRadius: "50%", background: "rgba(255,255,255,.72)", boxShadow: "0 0 0 3px rgba(255,255,255,.05)" }} />
            <div style={{ minWidth: 0, lineHeight: 1.15 }}>
              <div title={activeProject?.workspace_root || contextLabel} style={{ ...titleEllipsis, display: "block", color: "rgba(255,255,255,.86)", fontSize: 11.5, fontWeight: 600 }}>{contextLabel}</div>
              <div style={{ ...titleEllipsis, display: "block", marginTop: 4, color: "rgba(255,255,255,.38)", fontSize: 9.5 }}>{activeSession?.name || "No active Session"}</div>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 10 }}>
            <button
              className="echo-footer-action"
              type="button"
              onClick={() => props.onNewSession(props.activeProjectId || undefined)}
              style={{ minHeight: 30, borderRadius: 3, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 6, fontFamily: "'JetBrains Mono', ui-monospace, monospace", fontSize: 9.5 }}
            >
              <Icon name="plus" size={13} /> New Session
            </button>
            <button
              className="echo-footer-action"
              type="button"
              onClick={props.onAddFolder}
              style={{ minHeight: 30, borderRadius: 3, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 6, fontFamily: "'JetBrains Mono', ui-monospace, monospace", fontSize: 9.5 }}
            >
              <Icon name="folder" size={13} /> Add Project
            </button>
          </div>
        </footer>
      ) : (
        <div className="echo-sidebar-edge-pad" style={{ display: "grid", gap: 4, flexShrink: 0, paddingTop: 5, borderTop: "1px solid rgba(255,255,255,.07)" }}>
          <button className="echo-side-button" type="button" title="New Session" aria-label="New Session" onClick={() => props.onNewSession(props.activeProjectId || undefined)} style={railButton()}>
            <span style={iconSlot()}><Icon name="plus" size={16} /></span>
          </button>
          <button className="echo-side-button" type="button" title="Add Project folder" aria-label="Add Project folder" onClick={props.onAddFolder} style={railButton()}>
            <span style={iconSlot()}><Icon name="folder" size={16} /></span>
          </button>
        </div>
      )}
    </aside>
  );
}
