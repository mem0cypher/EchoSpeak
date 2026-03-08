import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import React, { useEffect, useRef } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Link } from "react-router-dom";
import { SquareAvatarVisual } from "./components/SquareAvatarVisual";
const LINKS = {
    download: "#download",
    github: "https://github.com", // Updated as per prompt's "View GitHub" requirement
    docs: "#docs",
    discord: "#",
    youtube: "#",
    x: "#",
};
const highlights = [
    "Local-first voice and chat",
    "Action confirmation built-in",
    "Open-source tooling stack",
    "Runs on your hardware",
];
const features = [
    {
        title: "Proactive Heartbeat",
        description: "EchoSpeak wakes up autonomously. Configure scheduled intervals where the agent reasons about its environment, checks tasks, and acts contextually without you saying a word.",
        className: "span-2",
    },
    {
        title: "Multi-Modal Scale",
        description: "The core engine connects directly to your Discord servers, Telegram chats, and a web dashboard simultaneously.",
        className: "",
    },
    {
        title: "Digital Dexterity",
        description: "Equipped with 46 executable tools. From Desktop PyAutoGUI automation to Playwright web scraping and IMAP/SMTP native email integration.",
        className: "",
    },
    {
        title: "Absolute Privacy. Vector Memory.",
        description: "Everything runs locally. Keep your FAISS vector memory entirely on your own hardware. Supports LM Studio, Ollama, vLLM, or llama.cpp.",
        className: "span-2",
    },
];
const allTools = [
    "Tavily Web Search",
    "YouTube Transcripts", "Local Directory Lister", "Local File Reader", "File Writer", "File Move/Rename",
    "File Delete", "Directory Maker", "Allowed Shell Access", "Read Active Windows", "Find UI Controls",
    "Click UI Elements", "Type Text to Apps", "Bring App to Foreground", "Send OS Hotkeys",
    "Launch Executables", "Chrome Automation", "Screen Capture", "LLM Screen OCR", "Vision Q&A",
    "Notepad Quick-Type", "Get Hardware Info", "Get Current Time", "Math Evaluator", "Read Source Code",
    "Grep Codebase", "List Codebase Files", "Edit Source Code", "Rollback Edits", "Check Git Status",
    "Discord Read Sync", "Discord Post as Bot", "Discord DM Capture", "Discord DM Reply",
    "Telegram Read Sync", "Telegram Send", "IMAP Inbox Reader", "SMTP Mail Sender",
    "Email Thread Context", "Daily News Briefing"
];
const TypewriterText = ({ items }) => {
    const [index, setIndex] = React.useState(0);
    const [subIndex, setSubIndex] = React.useState(0);
    const [reverse, setReverse] = React.useState(false);
    const [blink, setBlink] = React.useState(true);
    React.useEffect(() => {
        if (subIndex === items[index].length + 1 && !reverse) {
            setTimeout(() => setReverse(true), 1500);
            return;
        }
        if (subIndex === 0 && reverse) {
            setReverse(false);
            setIndex((prev) => (prev + 1) % items.length);
            return;
        }
        const timeout = setTimeout(() => {
            setSubIndex((prev) => prev + (reverse ? -1 : 1));
        }, Math.max(reverse ? 25 : subIndex === items[index].length ? 1000 : 50, Math.random() * 50));
        return () => clearTimeout(timeout);
    }, [subIndex, index, reverse, items]);
    React.useEffect(() => {
        const timeout2 = setInterval(() => {
            setBlink((prev) => !prev);
        }, 500);
        return () => clearInterval(timeout2);
    }, []);
    return (_jsx("span", { style: { color: 'var(--fg)', fontWeight: 600 }, children: `${items[index].substring(0, subIndex)}${blink ? "|" : " "}` }));
};
const workflow = [
    {
        title: "Stage 1: Parse & Preempt",
        description: "Plugin hooks fire first. Identifies Discord routes, slash commands, and multi-task planning before context is even built. Returns instantly if preempted.",
    },
    {
        title: "Stage 2: Build Context",
        description: "Synthesizes the ContextBundle. Injects the agent Soul, active Project details, Workspace tool permissions, and semantic FAISS memory.",
    },
    {
        title: "Stage 3: Shortcut Queries",
        description: "Heuristic routing without LLM hits. Detects specific shortcut intents like Discord channel lookups, calculator, or time checks for blazing fast responses.",
    },
    {
        title: "Stage 4: Invoke LLM",
        description: "Routes through a LangGraph ReAct agent. Executes tools gracefully. Sensitive destructive tools are paused and pushed to the 4-layer confirmation gate.",
    },
    {
        title: "Stage 5: Finalize",
        description: "Clamps long text for voice TTS. Records new conversational facts to the semantic memory curator. Fires final plugin success hooks.",
    },
];
const downloads = [
    {
        name: "Windows",
        detail: "Installer + portable build",
        status: "Recommended",
        href: LINKS.download,
    },
    {
        name: "macOS",
        detail: "Apple Silicon and Intel",
        status: "Coming soon",
        href: "#",
    },
    {
        name: "Linux",
        detail: "AppImage + deb",
        status: "Coming soon",
        href: "#",
    },
];
const community = [
    { name: "GitHub", detail: "Releases, roadmap, and issues", href: LINKS.github },
    { name: "Discord", detail: "Community support and updates", href: LINKS.discord },
    { name: "YouTube", detail: "Demos and walkthroughs", href: LINKS.youtube },
    { name: "X", detail: "Announcements and news", href: LINKS.x },
];
const docNav = [
    { label: "Overview", href: "#docs-overview" },
    { label: "Quickstart", href: "#docs-quickstart" },
    { label: "Architecture", href: "#docs-architecture" },
    { label: "API endpoints", href: "#docs-api" },
    { label: "Safety model", href: "#docs-safety" },
    { label: "Tools & integrations", href: "#docs-tools" },
    { label: "Environment flags", href: "#docs-env" },
    { label: "TUI & voice", href: "#docs-tui" },
];
const docPillars = ["Local-first", "Confirmation-gated", "Tool-first", "Open-source"];
const docOverviewCards = [
    {
        title: "Web UI (React/Vite)",
        description: "Captures mic + text input, streams events to /query/stream, and can speak replies with browser TTS.",
        meta: "apps/web/",
    },
    {
        title: "FastAPI server",
        description: "Hosts the REST API, streaming endpoints, and provider routing for local models.",
        meta: "apps/backend/api/server.py",
    },
    {
        title: "Agent core",
        description: "LLM wrapper, tool routing, memory, and confirmation gating live in the agent core.",
        meta: "apps/backend/agent/core.py",
    },
    {
        title: "Tools + memory",
        description: "Search, YouTube, browser, desktop automation, FAISS memory, and document RAG.",
        meta: "apps/backend/agent/tools.py",
    },
    {
        title: "Optional Go TUI",
        description: "Bubble Tea terminal client that consumes the same streaming API.",
        meta: "apps/tui/",
    },
];
const docQuickstart = [
    {
        title: "Install backend dependencies",
        description: "Set up the Python backend and install requirements.",
        code: "cd apps\\backend\\npip install -r requirements.txt",
    },
    {
        title: "Configure .env",
        description: "Point to your local model provider and enable the voice engine.",
        code: "USE_LOCAL_MODELS=true\nLOCAL_MODEL_PROVIDER=lmstudio\nLOCAL_MODEL_URL=http://localhost:1234\nLOCAL_MODEL_NAME=qwen/qwen3-coder-30b",
    },
    {
        title: "Start the API",
        description: "Launch FastAPI in API mode (default http://localhost:8000).",
        code: "python app.py --mode api",
    },
    {
        title: "Start the web UI",
        description: "Run the Vite dev server (default http://localhost:5174).",
        code: "cd apps\\web\\nnpm install\\nnpm run dev",
    },
];
const docArchitecture = [
    {
        title: "Capture + stream",
        description: "UI records text or mic audio, then streams to /query/stream.",
    },
    {
        title: "Reason + select tools",
        description: "Agent core routes the request, chooses tools, and manages memory.",
    },
    {
        title: "Confirm actions",
        description: "Sensitive system actions always require confirm or cancel.",
    },
    {
        title: "Respond + speak",
        description: "Final response + spoken_text returned for browser speech playback.",
    },
];
const docFileMap = `apps/web/
apps/backend/
  app.py
  api/server.py
  agent/core.py
  agent/tools.py
  io_module/
  data/
apps/tui/`;
const apiEndpoints = [
    { method: "GET", path: "/health", description: "Health check and uptime signal." },
    { method: "POST", path: "/query/stream", description: "Streaming agent events (UI default)." },
    { method: "POST", path: "/query", description: "Single request/response query." },
    { method: "GET", path: "/documents", description: "List/upload documents for RAG." },
    { method: "GET", path: "/provider/models", description: "List models for providers." },
    { method: "POST", path: "/vision/analyze", description: "OCR + screen analysis." },
];
const safetySteps = [
    "Echo proposes an action and stores it as a pending action.",
    "You reply confirm or cancel to approve the exact plan.",
    "Only after confirmation does the action tool execute.",
];
const integrationHighlights = [
    {
        title: "Proactive Heartbeat",
        description: "Autonomous reasoning intervals. The agent wakes up, checks its surroundings, and acts without you saying a word.",
    },
    {
        title: "Discord & Telegram Ready",
        description: "Multi-channel presence. Connect the EchoSpeak core brain directly to your servers or DMs.",
    },
    {
        title: "Native Email Automation",
        description: "IMAP/SMTP integrated. It can read your inbox, summarize threads, and draft replies autonomously.",
    },
    {
        title: "Multi-Agent Swarm",
        description: "Deploy an entire pool of specialized sub-agents to tackle complex, parallel objectives simultaneously.",
    },
    {
        title: "Web Search (Tavily)",
        description: "Fresh Tavily-backed web search for current answers without scraping fallbacks.",
    },
    {
        title: "Desktop & UI Automation",
        description: "PyWinAuto + PyAutoGUI + Playwright. Confirmation-gated actions to control your actual machine.",
    },
    {
        title: "Document RAG & FAISS",
        description: "Upload local documents and build a long-term, searchable semantic memory bank on your machine.",
    },
    {
        title: "Browser Voice",
        description: "Speech playback and dictation use your browser's built-in voice features.",
    },
];
const envGroups = [
    {
        title: "Local models",
        items: [
            "USE_LOCAL_MODELS",
            "LOCAL_MODEL_PROVIDER",
            "LOCAL_MODEL_URL",
            "LOCAL_MODEL_NAME",
            "LOCAL_MODEL_TEMPERATURE",
            "LOCAL_MODEL_MAX_TOKENS",
        ],
    },
    {
        title: "Safety gates",
        items: [
            "ENABLE_SYSTEM_ACTIONS",
            "ALLOW_OPEN_CHROME",
            "ALLOW_PLAYWRIGHT",
            "ALLOW_DESKTOP_AUTOMATION",
            "ALLOW_FILE_WRITE",
        ],
    },
    {
        title: "Search + tools",
        items: ["TAVILY_API_KEY", "TAVILY_SEARCH_DEPTH", "TAVILY_MAX_RESULTS", "USE_TOOL_CALLING_LLM"],
    },
    {
        title: "Documents",
        items: ["DOCUMENT_RAG_ENABLED", "DOC_UPLOAD_MAX_MB"],
    },
];
const tuiNotes = [
    {
        title: "Start the TUI",
        description: "Bubble Tea terminal UI that uses the same streaming API.",
        code: "cd apps\\tui\\ngo run .",
    },
    {
        title: "Windows mic capture",
        description: "Enable FFmpeg DirectShow recording for alt+r mic capture.",
        items: [
            "ECHOSPEAK_FFMPEG (path to ffmpeg.exe)",
            "ECHOSPEAK_MIC_DSHOW_DEVICE",
            "ECHOSPEAK_MIC_SECONDS",
        ],
        code: "ffmpeg -hide_banner -list_devices true -f dshow -i dummy",
    },
    {
        title: "TTS summary-only",
        description: "TUI can speak spoken_text while full replies stay visible.",
    },
];
const globalCss = `
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap');
:root {
  --bg: #000000;
  --bg-alt: #0a0a0a;
  --fg: #ffffff;
  --fg-muted: #888888;
  --border: #333333;
  --accent: #ffffff;
}
* { box-sizing: border-box; cursor: auto !important; }
body {
  margin: 0;
  font-family: 'Manrope', sans-serif;
  background-color: var(--bg);
  color: var(--fg);
  min-height: 100vh;
  overflow-x: hidden;
}
.geometric-canvas {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  z-index: -1;
  pointer-events: none;
  background: var(--bg);
}
a { color: inherit; text-decoration: none; }
img { max-width: 100%; display: block; }

.site { position: relative; min-height: 100vh; overflow-x: hidden; }
.container { width: min(1280px, 100% - 64px); margin: 0 auto; position: relative; z-index: 2; }

.nav {
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(12px);
  background: rgba(0, 0, 0, 0.85);
  border-bottom: 1px solid var(--border);
  transition: all 0.3s ease;
}
.nav-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 24px 0;
  gap: 40px;
}
.logo {
  display: flex;
  align-items: center;
  gap: 16px;
  font-family: 'Space Grotesk', sans-serif;
  font-weight: 700;
  font-size: 24px;
  letter-spacing: -0.02em;
}
.logo-square {
  width: 16px;
  height: 16px;
  background: #fff;
}
.nav-links {
  display: flex;
  gap: 32px;
  font-size: 15px;
  font-weight: 600;
  color: var(--fg-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.nav-links a {
  transition: color 0.2s ease;
}
.nav-links a:hover { color: var(--fg); }
.nav-cta { display: flex; gap: 16px; }

.hero {
  padding: 110px 0 80px;
  position: relative;
}
.hero-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 24px;
  align-items: center;
  justify-items: center;
}
.hero-copy {
  text-align: center;
  max-width: 860px;
}
.eyebrow {
  display: inline-block;
  padding: 4px 10px;
  border: 1px solid var(--fg);
  font-family: 'Space Grotesk', monospace;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 18px;
}
.hero h1 {
  font-family: 'Space Grotesk', sans-serif;
  font-weight: 700;
  font-size: clamp(48px, 6vw, 80px);
  line-height: 1.05;
  margin: 0 0 18px;
  letter-spacing: -0.03em;
}
.hero p {
  font-size: 17px;
  line-height: 1.6;
  color: var(--fg-muted);
  margin: 0 auto 26px;
  max-width: 760px;
}
.hero-actions { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }

.btn {
  padding: 12px 24px;
  font-weight: 600;
  font-size: 14px;
  transition: all 0.2s ease;
  border-radius: 0;
  font-family: 'Space Grotesk', sans-serif;
}
.btn.primary {
  background: var(--fg);
  color: var(--bg);
  border: 1px solid var(--fg);
}
.btn.primary:hover {
  background: transparent;
  color: var(--fg);
}
.btn.ghost {
  background: transparent;
  color: var(--fg);
  border: 1px solid var(--border);
}
.btn.ghost:hover {
  border-color: var(--fg);
}

.sidebar {
  position: fixed;
  left: 0;
  top: 0;
  height: 100vh;
  width: 80px;
  background: var(--bg-alt);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 32px 0;
  z-index: 20;
}
.sidebar-logo {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: auto;
}
.sidebar-logo img {
  width: 24px;
  border-radius: 4px;
}

.feature-grid, .workflow-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 24px;
  margin-top: 60px;
}
.feature-card, .workflow-card {
  padding: 32px;
  border: 1px solid var(--border);
  background: rgba(0,0,0,0.5);
  transition: border-color 0.3s ease;
}
.feature-card:hover, .workflow-card:hover {
  border-color: var(--fg);
}
.feature-card h3, .workflow-card h4 {
  margin: 0 0 16px;
  font-family: 'Space Grotesk', sans-serif;
  font-size: 20px;
}
.feature-card p, .workflow-card p {
  color: var(--fg-muted);
  font-size: 15px;
  line-height: 1.5;
  margin: 0;
}

.footer {
  border-top: 1px solid var(--border);
  padding: 40px 0;
  margin-top: 80px;
  font-size: 13px;
  color: var(--fg-muted);
  background: var(--bg-alt);
}

/* Bento Box Layout */
.bento-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 24px;
}
.bento-card {
  padding: 40px;
  border: 1px solid var(--border);
  background: rgba(0,0,0,0.5);
  transition: all 0.4s ease;
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  min-height: 280px;
}
.bento-card:hover {
  border-color: var(--fg);
  transform: translateY(-2px);
  box-shadow: 0 10px 40px -10px rgba(255,255,255,0.05);
}
.bento-card.span-2 { grid-column: span 2; }
.bento-card.span-3 { grid-column: span 3; }
.bento-card h3 {
  margin: 0 0 12px;
  font-family: 'Space Grotesk', sans-serif;
  font-size: 24px;
  z-index: 2;
  color: var(--fg);
}
.bento-card p {
  color: var(--fg-muted);
  font-size: 16px;
  line-height: 1.6;
  margin: 0;
  z-index: 2;
  max-width: 90%;
}

/* Tool Marquee Setup */
.marquee-container {
  overflow: hidden;
  position: relative;
  width: 100%;
  display: flex;
  background: var(--bg-alt);
  padding: 30px 0;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
}
.marquee-container::before, .marquee-container::after {
  content: '';
  position: absolute;
  top: 0;
  width: 15%;
  height: 100%;
  z-index: 2;
  pointer-events: none;
}
.marquee-container::before {
  left: 0;
  background: linear-gradient(to right, var(--bg-alt) 0%, transparent 100%);
}
.marquee-container::after {
  right: 0;
  background: linear-gradient(to left, var(--bg-alt) 0%, transparent 100%);
}
.marquee-content {
  display: flex;
  gap: 16px;
  animation: scroll-left 40s linear infinite;
  white-space: nowrap;
}
@keyframes scroll-left {
  0% { transform: translateX(0); }
  100% { transform: translateX(-50%); }
}
.tool-pill {
  padding: 8px 16px;
  background: rgba(255,255,255,0.03);
  border: 1px solid var(--border);
  border-radius: 40px;
  font-family: 'Space Grotesk', monospace;
  font-size: 13px;
  color: var(--fg-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.tool-pill::before {
  content: '';
  width: 6px;
  height: 6px;
  background: var(--border);
  border-radius: 50%;
  display: inline-block;
}

/* Timeline Layout for 5-Stage Pipeline */
.timeline-grid {
  display: flex;
  flex-direction: column;
  gap: 40px;
  position: relative;
  max-width: 800px;
  margin-left: auto;
  margin-right: auto;
}
.timeline-grid::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 24px;
  width: 1px;
  background: var(--border);
}
.timeline-card {
  position: relative;
  padding-left: 80px;
}
.timeline-node {
  position: absolute;
  left: 17px;
  top: 6px;
  width: 15px;
  height: 15px;
  border-radius: 50%;
  background: var(--bg);
  border: 2px solid var(--fg);
  z-index: 2;
}
.timeline-card h4 {
  margin: 0 0 12px;
  font-family: 'Space Grotesk', sans-serif;
  font-size: 20px;
  color: var(--fg);
}
.timeline-card p {
  color: var(--fg-muted);
  font-size: 16px;
  line-height: 1.6;
  margin: 0;
}

@media (max-width: 900px) {
  .sidebar { width: 100%; height: 60px; flex-direction: row; padding: 0 20px; bottom: 0; top: auto; border-right: none; border-top: 1px solid var(--border); }
  .site-main { padding-left: 0; padding-bottom: 60px; }
  .sidebar-logo { margin-bottom: 0; margin-right: auto; }
}
`;
const GeometricBackground = () => {
    const canvasRef = useRef(null);
    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas)
            return;
        const ctx = canvas.getContext("2d");
        if (!ctx)
            return;
        let w = (canvas.width = window.innerWidth);
        let h = (canvas.height = window.innerHeight);
        const shapes = [];
        const count = 40;
        // Initialize shapes
        for (let i = 0; i < count; i++) {
            shapes.push({
                x: Math.random() * w,
                y: Math.random() * h,
                size: Math.random() * 40 + 10,
                type: Math.random() > 0.5 ? "square" : "circle",
                dx: (Math.random() - 0.5) * 0.5,
                dy: (Math.random() - 0.5) * 0.5,
                opacity: Math.random() * 0.2 + 0.05,
            });
        }
        const drawGrid = () => {
            ctx.fillStyle = "rgba(255, 255, 255, 0.03)";
            const step = 40;
            for (let x = 0; x < w; x += step) {
                for (let y = 0; y < h; y += step) {
                    ctx.fillRect(x, y, 1, 1);
                }
            }
        };
        const render = () => {
            ctx.clearRect(0, 0, w, h);
            ctx.fillStyle = "#000000";
            ctx.fillRect(0, 0, w, h);
            drawGrid();
            ctx.strokeStyle = "rgba(255, 255, 255, 0.1)";
            ctx.lineWidth = 1;
            shapes.forEach((s) => {
                s.x += s.dx;
                s.y += s.dy;
                if (s.x < -50)
                    s.x = w + 50;
                if (s.x > w + 50)
                    s.x = -50;
                if (s.y < -50)
                    s.y = h + 50;
                if (s.y > h + 50)
                    s.y = -50;
                ctx.beginPath();
                if (s.type === "square") {
                    ctx.strokeRect(s.x, s.y, s.size, s.size);
                }
                else {
                    ctx.arc(s.x, s.y, s.size / 2, 0, Math.PI * 2);
                    ctx.stroke();
                }
            });
            animationFrameId = requestAnimationFrame(render);
        };
        let animationFrameId = requestAnimationFrame(render);
        const handleResize = () => {
            w = canvas.width = window.innerWidth;
            h = canvas.height = window.innerHeight;
        };
        window.addEventListener("resize", handleResize);
        return () => {
            window.removeEventListener("resize", handleResize);
            cancelAnimationFrame(animationFrameId);
        };
    }, []);
    return _jsx("canvas", { ref: canvasRef, className: "geometric-canvas" });
};
const Sidebar = () => {
    return (_jsxs("aside", { className: "sidebar", children: [_jsx("div", { className: "sidebar-logo", children: _jsx("img", { src: "/logo.png", alt: "EchoSpeak Logo", style: { width: 24, borderRadius: 4 } }) }), _jsx("div", { style: { marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: '24px', alignItems: 'center' }, children: _jsx("a", { href: LINKS.github, target: "_blank", rel: "noreferrer", title: "GitHub", style: { opacity: 0.6 }, children: _jsx("svg", { width: "20", height: "20", viewBox: "0 0 24 24", fill: "currentColor", children: _jsx("path", { d: "M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.042-1.416-4.042-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z" }) }) }) })] }));
};
export const Marketing = () => {
    const reduceMotion = useReducedMotion();
    const viewport = { once: true, amount: 0.3 };
    const fadeUp = (delay = 0) => ({
        hidden: { opacity: 0, y: reduceMotion ? 0 : 40 },
        show: {
            opacity: 1,
            y: 0,
            transition: { duration: 0.8, ease: [0.215, 0.61, 0.355, 1], delay },
        },
    });
    const stagger = {
        hidden: {},
        show: {
            transition: { staggerChildren: reduceMotion ? 0 : 0.1, delayChildren: 0.1 },
        },
    };
    const floatAnimation = reduceMotion ? {} : {
        y: [0, -6, 0],
        transition: { duration: 4, repeat: Infinity, ease: "easeInOut" }
    };
    const pulseAnimation = reduceMotion ? {} : {
        scale: [1, 1.15, 1],
        boxShadow: ["0 0 0px var(--fg)", "0 0 15px var(--fg)", "0 0 0px var(--fg)"],
        transition: { duration: 2, repeat: Infinity, ease: "easeInOut" }
    };
    return (_jsxs("div", { className: "site", children: [_jsx("style", { children: globalCss }), _jsx(GeometricBackground, {}), _jsx(Sidebar, {}), _jsxs("main", { className: "site-main", style: { paddingLeft: '80px' }, children: [_jsx("header", { className: "nav", children: _jsxs("div", { className: "container nav-inner", children: [_jsxs("div", { className: "logo", children: [_jsx("img", { src: "/logo.png", alt: "EchoSpeak", style: { width: 20, borderRadius: 4 } }), _jsx("span", { style: { fontFamily: 'Space Grotesk' }, children: "EchoSpeak" })] }), _jsxs("nav", { className: "nav-links", children: [_jsx("a", { href: "#overview", children: "Vision" }), _jsx("a", { href: "#features", children: "Core" }), _jsx("a", { href: "#docs", children: "Documentation" }), _jsx("a", { href: LINKS.discord, children: "Community" }), _jsx("a", { href: LINKS.github, children: "GitHub" })] }), _jsx("div", { className: "nav-cta", children: _jsx(Link, { className: "btn primary", to: "/app", style: { padding: '14px 32px', fontSize: '15px' }, children: "Access Platform" }) })] }) }), _jsx(motion.section, { className: "hero", id: "overview", initial: "hidden", animate: "show", variants: stagger, children: _jsx("div", { className: "container hero-grid", children: _jsxs(motion.div, { className: "hero-copy", variants: fadeUp(0), children: [_jsx("span", { className: "eyebrow", style: { borderColor: 'var(--fg)', background: 'rgba(255,255,255,0.05)' }, children: "EchoSpeak v5.4.0 \u2022 System Online" }), _jsx("div", { style: { display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '20px', margin: '0 0 24px' }, children: _jsx("h1", { style: { margin: 0 }, children: "EchoSpeak" }) }), _jsx("p", { style: { maxWidth: '800px', fontSize: '20px' }, children: "The Autonomous, Local-First AI Operating System." }), _jsxs("div", { style: {
                                            padding: '16px',
                                            background: 'rgba(0,0,0,0.8)',
                                            border: '1px solid var(--border)',
                                            borderRadius: '8px',
                                            maxWidth: '600px',
                                            margin: '0 auto 32px',
                                            fontFamily: 'monospace',
                                            fontSize: '14px',
                                            textAlign: 'left',
                                            color: 'var(--fg-muted)',
                                            overflow: 'hidden',
                                            whiteSpace: 'nowrap',
                                            textOverflow: 'ellipsis',
                                            display: 'flex',
                                            alignItems: 'center',
                                            gap: '8px'
                                        }, children: [_jsx("span", { style: { color: '#4ade80', flexShrink: 0 }, children: "\u279C" }), _jsx("span", { style: { flexShrink: 0 }, children: "root@echo:~#" }), _jsx("span", { style: { overflow: 'hidden', textOverflow: 'ellipsis' }, children: _jsx(TypewriterText, { items: [
                                                        "\"Read my unread emails and draft replies.\"",
                                                        "\"Open Chrome and find flight deals to Tokyo.\"",
                                                        "\"Analyze my screen and explain this code error.\"",
                                                        "\"Search Discord for the latest project updates.\"",
                                                        "\"Read my codebase and refactor api/server.py.\"",
                                                        "\"Wait quietly and brief me every morning at 9am.\""
                                                    ] }) })] }), _jsxs("div", { className: "hero-actions", children: [_jsx(Link, { className: "btn primary", to: "/app", children: "Initialize System" }), _jsx("a", { className: "btn ghost", href: LINKS.github, children: "View Source" })] })] }) }) }), _jsx(motion.section, { className: "section", id: "features", initial: "hidden", animate: "show", variants: stagger, style: { padding: '80px 0 100px', borderTop: '1px solid var(--border)' }, children: _jsxs("div", { className: "container", children: [_jsx(motion.div, { className: "section-title", variants: fadeUp(0), style: { marginBottom: '60px' }, children: _jsx("h2", { children: "CORE CAPABILITIES" }) }), _jsx(motion.div, { className: "bento-grid", variants: stagger, children: features.map((feature, idx) => (_jsxs(motion.div, { className: `bento-card ${feature.className}`, variants: fadeUp(0), animate: floatAnimation, style: { animationDelay: `${idx * 0.2}s` }, children: [_jsx("h3", { children: feature.title }), _jsx("p", { children: feature.description })] }, feature.title))) })] }) }), _jsx(motion.section, { className: "section", id: "meet-echo", initial: "hidden", animate: "show", variants: stagger, style: { padding: '100px 0', borderTop: '1px solid var(--border)' }, children: _jsxs("div", { className: "container", style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(350px, 1fr))', gap: '60px', alignItems: 'center' }, children: [_jsx(motion.div, { variants: fadeUp(0), style: { display: 'flex', justifyContent: 'center', position: 'relative' }, children: _jsx("div", { style: {
                                            width: '320px', height: '320px',
                                            display: 'flex', alignItems: 'center', justifyContent: 'center'
                                        }, children: _jsx("div", { style: { transform: 'scale(2.2)' }, children: _jsx(SquareAvatarVisual, { speaking: false, backendOnline: true, isThinking: false }) }) }) }), _jsxs(motion.div, { variants: fadeUp(0), children: [_jsx("span", { className: "eyebrow", style: { borderColor: 'var(--fg)', background: 'transparent' }, children: "Meet Echo" }), _jsx("h2", { style: { fontFamily: 'Space Grotesk', fontSize: 'clamp(32px, 4vw, 48px)', margin: '0 0 24px', lineHeight: 1.1 }, children: "A digital soul with mechanical precision." }), _jsx("p", { style: { fontSize: '18px', color: 'var(--fg-muted)', lineHeight: 1.6, marginBottom: '24px' }, children: "Echo isn't just a voice module\u2014he's the interactive avatar of your entire local operating system. Designed with a synthetic, 8-bit personality, he visually reacts to his real-time processing states." }), _jsxs("ul", { style: { listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '16px' }, children: [_jsxs("li", { style: { display: 'flex', gap: '12px', alignItems: 'flex-start' }, children: [_jsx("span", { style: { color: 'var(--fg)', fontSize: '20px' }, children: "\u2726" }), _jsxs("div", { children: [_jsx("h4", { style: { margin: '0 0 4px', fontSize: '16px', color: 'var(--fg)', fontFamily: 'Space Grotesk' }, children: "Visual State Engine" }), _jsx("p", { style: { margin: 0, fontSize: '15px', color: 'var(--fg-muted)', lineHeight: 1.5 }, children: "Watch him smoothly transition from sleep, to active scanning, to speaking. Thought bubbles give you a direct window into his background reasoning." })] })] }), _jsxs("li", { style: { display: 'flex', gap: '12px', alignItems: 'flex-start' }, children: [_jsx("span", { style: { color: 'var(--fg)', fontSize: '20px' }, children: "\u2726" }), _jsxs("div", { children: [_jsx("h4", { style: { margin: '0 0 4px', fontSize: '16px', color: 'var(--fg)', fontFamily: 'Space Grotesk' }, children: "Autonomous Memory" }), _jsx("p", { style: { margin: 0, fontSize: '15px', color: 'var(--fg-muted)', lineHeight: 1.5 }, children: "He maintains a persistent semantic FAISS memory bank. When you converse, he remembers your past projects, context, and preferences securely on your own disk." })] })] })] })] })] }) }), _jsx(motion.section, { className: "section alt", initial: "hidden", animate: "show", variants: stagger, style: { padding: '40px 0 100px', borderTop: '1px solid var(--border)' }, children: _jsx("div", { className: "container", children: _jsxs(motion.div, { style: {
                                    display: 'grid',
                                    gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
                                    gap: '24px',
                                }, variants: stagger, children: [_jsxs(motion.div, { variants: fadeUp(0), style: {
                                            padding: '40px',
                                            background: 'rgba(0,0,0,0.5)',
                                            border: '1px solid var(--border)',
                                            borderRadius: '16px',
                                            transition: 'transform 0.3s ease, box-shadow 0.3s ease, border-color 0.3s ease',
                                            cursor: 'pointer',
                                            position: 'relative',
                                            overflow: 'hidden'
                                        }, onMouseEnter: (e) => { e.currentTarget.style.transform = 'translateY(-4px)'; e.currentTarget.style.borderColor = 'rgba(255,255,255,0.3)'; }, onMouseLeave: (e) => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.borderColor = 'var(--border)'; }, children: [_jsxs("div", { style: { display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '20px' }, children: [_jsx("svg", { width: "32", height: "32", viewBox: "0 0 127.14 96.36", fill: "var(--fg)", children: _jsx("path", { d: "M107.7 8.07A105.15 105.15 0 0081.47 0a72.06 72.06 0 00-3.36 6.83 97.68 97.68 0 00-29.08 0A72.37 72.37 0 0045.67 0a105.46 105.46 0 00-26.23 8.07C2.04 33.84-2.23 58.9.79 83.56a105.74 105.74 0 0032.14 16.15 77.7 77.7 0 006.89-11.3 68.42 68.42 0 01-10.85-5.18c.91-.66 1.8-1.34 2.66-2a75.57 75.57 0 0064.32 0c.87.71 1.76 1.39 2.68 2a67.48 67.48 0 01-10.87 5.19 77 77 0 006.89 11.29 105.25 105.25 0 0032.19-16.15c3.31-27.46-2.57-50-19.44-75.49zM42.63 68.32c-5.2 0-9.49-4.78-9.49-10.6 0-5.83 4.19-10.6 9.49-10.6s9.54 4.78 9.49 10.6c0 5.82-4.24 10.6-9.49 10.6zm41.96 0c-5.2 0-9.49-4.78-9.49-10.6 0-5.83 4.19-10.6 9.49-10.6s9.54 4.78 9.49 10.6c0 5.82-4.24 10.6-9.49 10.6z" }) }), _jsx("h3", { style: { margin: 0, fontSize: '24px', fontFamily: '"Space Grotesk", sans-serif' }, children: "Discord" })] }), _jsx("p", { style: { color: 'var(--fg-muted)', fontSize: '15px', lineHeight: 1.6, margin: 0 }, children: "Reads active channels, intercepts targeted @mentions, captures DMs via token, and replies autonomously using Playwright or Discord Bot accounts." })] }), _jsxs(motion.div, { variants: fadeUp(0), style: {
                                            padding: '40px',
                                            background: 'rgba(0,0,0,0.5)',
                                            border: '1px solid var(--border)',
                                            borderRadius: '16px',
                                            transition: 'transform 0.3s ease, box-shadow 0.3s ease, border-color 0.3s ease',
                                            cursor: 'pointer',
                                            position: 'relative',
                                            overflow: 'hidden'
                                        }, onMouseEnter: (e) => { e.currentTarget.style.transform = 'translateY(-4px)'; e.currentTarget.style.borderColor = 'rgba(255,255,255,0.3)'; }, onMouseLeave: (e) => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.borderColor = 'var(--border)'; }, children: [_jsxs("div", { style: { display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '20px' }, children: [_jsx("svg", { width: "32", height: "32", viewBox: "0 0 24 24", fill: "var(--fg)", children: _jsx("path", { d: "M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.888-.662 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z" }) }), _jsx("h3", { style: { margin: 0, fontSize: '24px', fontFamily: '"Space Grotesk", sans-serif' }, children: "Telegram" })] }), _jsx("p", { style: { color: 'var(--fg-muted)', fontSize: '15px', lineHeight: 1.6, margin: 0 }, children: "Native bot polling loop built-in. Read channel posts, synchronize message histories, and let the agent proactively post alerts directly to your groups." })] }), _jsxs(motion.div, { variants: fadeUp(0), style: {
                                            padding: '40px',
                                            background: 'rgba(0,0,0,0.5)',
                                            border: '1px solid var(--border)',
                                            borderRadius: '16px',
                                            transition: 'transform 0.3s ease, box-shadow 0.3s ease, border-color 0.3s ease',
                                            cursor: 'pointer',
                                            position: 'relative',
                                            overflow: 'hidden'
                                        }, onMouseEnter: (e) => { e.currentTarget.style.transform = 'translateY(-4px)'; e.currentTarget.style.borderColor = 'rgba(255,255,255,0.3)'; }, onMouseLeave: (e) => { e.currentTarget.style.transform = 'translateY(0)'; e.currentTarget.style.borderColor = 'var(--border)'; }, children: [_jsxs("div", { style: { display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '20px' }, children: [_jsx("svg", { width: "32", height: "32", viewBox: "0 0 24 24", fill: "var(--fg)", children: _jsx("path", { d: "M12.031 0A12.015 12.015 0 0 0 .157 16.03l-1.028 4.673a1.205 1.205 0 0 0 1.458 1.42l4.8-1.018A12.016 12.016 0 1 0 12.031 0zm5.836 17.065c-.25.703-1.42 1.34-1.957 1.41-.5.066-1.127.18-3.18-.673-2.484-1.03-4.08-3.565-4.204-3.73-.122-.164-1.006-1.34-1.006-2.557 0-1.218.636-1.815.86-2.062.223-.245.485-.306.647-.306.162 0 .324.004.468.01.155.01.365-.06.57.442.217.533.742 1.815.81 1.95.067.135.111.294.029.46-.084.164-.127.266-.25.408-.122.144-.26.311-.37.433-.122.135-.255.281-.11.53.145.245.642 1.056 1.385 1.718.96.856 1.764 1.12 2.01 1.244.244.122.387.102.53-.062.143-.162.617-.714.78-.96.163-.244.325-.203.548-.12.224.084 1.417.672 1.66.793.245.122.438.375.438.874 0 .498-.217 1.408-.468 2.11z" }) }), _jsx("h3", { style: { margin: 0, fontSize: '24px', fontFamily: '"Space Grotesk", sans-serif' }, children: "WhatsApp" })] }), _jsx("p", { style: { color: 'var(--fg-muted)', fontSize: '15px', lineHeight: 1.6, margin: 0 }, children: "Automate WhatsApp Web securely via Playwright local proxy. Send urgent updates, scan threads, and forward insights directly to your phone." })] })] }) }) }), _jsx(motion.section, { className: "section", initial: "hidden", animate: "show", variants: stagger, style: { padding: '100px 0', borderTop: '1px solid var(--border)' }, children: _jsxs("div", { className: "container", children: [_jsx(motion.div, { className: "section-title", variants: fadeUp(0), style: { marginBottom: '60px' }, children: _jsx("h2", { style: { textAlign: 'center' }, children: "THE 5-STAGE COGNITION PIPELINE" }) }), _jsx(motion.div, { className: "timeline-grid", variants: stagger, children: workflow.map((step, idx) => (_jsxs(motion.div, { className: "timeline-card", variants: fadeUp(0), children: [_jsx(motion.div, { className: "timeline-node", animate: pulseAnimation, style: { animationDelay: `${idx * 0.4}s` } }), _jsx("h4", { children: step.title }), _jsx("p", { children: step.description })] }, step.title))) })] }) }), _jsx(motion.section, { className: "section alt", initial: "hidden", animate: "show", variants: stagger, style: { padding: '40px 0 100px', borderTop: 'none', background: 'rgba(255,255,255,0.01)' }, children: _jsx("div", { className: "container", children: _jsxs(motion.div, { className: "bento-grid", variants: stagger, children: [_jsxs(motion.div, { className: "bento-card span-2", variants: fadeUp(0), children: [_jsx("div", { style: { padding: '12px', background: 'var(--fg)', borderRadius: '12px', width: 'fit-content', marginBottom: '24px', color: 'var(--bg)' }, children: _jsxs("svg", { width: "24", height: "24", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", children: [_jsx("path", { d: "m12 14 4-4" }), _jsx("path", { d: "M3.34 19a10 10 0 1 1 17.32 0" })] }) }), _jsx("h3", { children: "Executable Tools" }), _jsx("p", { children: "Deploy native desktop automation, web scaling via Playwright, system monitors, and Python interpreters securely restricted by Workspace boundaries. Every destructive execution runs through a strict per-action confirmation payload." }), _jsxs("div", { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '16px', position: 'relative', zIndex: 2 }, children: [_jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "desktop_click" }), _jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "desktop_type_text" }), _jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "desktop_list_windows" }), _jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "desktop_find_control" }), _jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "browse_task" }), _jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "youtube_transcript" }), _jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "web_search" }), _jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "email_send" }), _jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "email_read_inbox" }), _jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "file_write" }), _jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "file_read" }), _jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "terminal_run" })] })] }), _jsxs(motion.div, { className: "bento-card", variants: fadeUp(0), children: [_jsx("div", { style: { padding: '12px', background: 'var(--fg)', borderRadius: '12px', width: 'fit-content', marginBottom: '24px', color: 'var(--bg)' }, children: _jsx("svg", { width: "24", height: "24", viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "2", strokeLinecap: "round", strokeLinejoin: "round", children: _jsx("path", { d: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10" }) }) }), _jsx("h3", { children: "Behavioral Skills" }), _jsx("p", { children: "Inject custom reasoning patterns, pre-hooks, and post-hooks directly into the 5-stage processing pipeline." }), _jsxs("div", { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '16px', position: 'relative', zIndex: 2 }, children: [_jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "system_monitor" }), _jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "daily_briefing" }), _jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "code_reviewer" }), _jsx(motion.span, { whileHover: { scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }, className: "tool-pill", style: { cursor: 'pointer' }, children: "memory_curator" })] })] })] }) }) }), _jsx("section", { className: "marquee-container", style: { marginTop: '40px', borderBottom: 'none' }, children: _jsx("div", { className: "marquee-content", children: allTools.concat(allTools).map((tool, i) => (_jsx("span", { className: "tool-pill", children: tool }, i))) }) }), _jsx("footer", { className: "footer", style: { marginTop: 0 }, children: _jsx("div", { className: "container", children: _jsx("p", { children: "\u00A9 2026 EchoSpeak Labs. System Online." }) }) })] })] }));
};
