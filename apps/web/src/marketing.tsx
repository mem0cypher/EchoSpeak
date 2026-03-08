import React, { useEffect, useRef } from "react";
import ReactDOM from "react-dom/client";
import { motion, useReducedMotion, type Variants, AnimatePresence } from "framer-motion";
import { Link } from "react-router-dom";
import { gsap } from "gsap";
import { SquareAvatarVisual } from "./components/SquareAvatarVisual";

const LINKS = {
  download: "#download",
  github: "https://github.com",
  docs: "#docs",
  discord: "#",
  youtube: "#",
  x: "#",
};

const highlights = [
  "Local-first voice and chat",
  "Confirmation-gated actions",
  "Open-source tooling stack",
  "Runs on your hardware",
];

const features = [
  {
    title: "Proactive Heartbeat",
    description:
      "EchoSpeak wakes up autonomously. Configure scheduled intervals where the agent reasons about its environment, checks tasks, and acts contextually without you saying a word.",
    className: "span-2",
  },
  {
    title: "Multi-Surface Presence",
    description:
      "The core engine connects to Discord servers, Telegram chats, Twitter/X, Twitch streams, and a web dashboard simultaneously.",
    className: "",
  },
  {
    title: "Digital Dexterity",
    description:
      "Equipped with 50+ executable tools. Desktop automation, Playwright browser control, terminal commands, IMAP/SMTP email, codebase editing, and more.",
    className: "",
  },
  {
    title: "Absolute Privacy. Vector Memory.",
    description:
      "Everything runs locally. FAISS vector memory, profile facts, curated notes, and document RAG stay on your own hardware. Supports LM Studio, Ollama, Gemini, or OpenAI.",
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
  "Discord Read Channel", "Discord Post as Bot", "Discord DM via Bot", "Discord Web Read",
  "Telegram Read Sync", "Telegram Send", "IMAP Inbox Reader", "SMTP Mail Sender",
  "Email Thread Context", "Daily News Briefing", "Tweet Post", "Tweet Delete",
  "Browse Task (Playwright)", "Project Update Context", "Workspace Explorer",
];

const TypewriterText: React.FC<{ items: string[] }> = ({ items }) => {
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

  return (
    <span style={{ color: 'var(--fg)', fontWeight: 600 }}>
      {`${items[index].substring(0, subIndex)}${blink ? "|" : " "}`}
    </span>
  );
};

const workflow = [
  {
    title: "Stage 1: Parse & Preempt",
    description: "Plugin hooks fire first. Multi-task planning, approval hydration, slash commands, and Discord/Twitter/Twitch routing before context is even built. Returns instantly if preempted.",
  },
  {
    title: "Stage 2: Build Context",
    description: "Synthesizes the ContextBundle. Injects the Soul, active Project details, Workspace tool permissions, update context, semantic FAISS memory, and user role identity.",
  },
  {
    title: "Stage 3: Shortcut Queries",
    description: "Heuristic routing without LLM hits. Detects help/capability, calculator, time, profile recall, schedule lookups, and multi-web fan-out for blazing fast responses.",
  },
  {
    title: "Stage 4: Invoke LLM",
    description: "Routes through a LangGraph ReAct agent with reflection. Executes tools with the reflection engine evaluating results. Destructive tools pause at the confirmation gate.",
  },
  {
    title: "Stage 5: Finalize",
    description: "Clamps long text for voice TTS. Records conversational facts to the semantic memory curator. Fires plugin success hooks. Emits execution traces and thread state.",
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
    description:
      "Captures mic + text input, streams events to /query/stream, and can speak replies with browser TTS.",
    meta: "apps/web/",
  },
  {
    title: "FastAPI server",
    description:
      "Hosts the REST API, streaming endpoints, and provider routing for local models.",
    meta: "apps/backend/api/server.py",
  },
  {
    title: "Agent core",
    description:
      "LLM wrapper, tool routing, memory, and confirmation gating live in the agent core.",
    meta: "apps/backend/agent/core.py",
  },
  {
    title: "Tools + memory",
    description:
      "Search, YouTube, browser, desktop automation, FAISS memory, and document RAG.",
    meta: "apps/backend/agent/tools.py",
  },
  {
    title: "Optional Go TUI",
    description:
      "Bubble Tea terminal client that consumes the same streaming API.",
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
    code:
      "USE_LOCAL_MODELS=true\nLOCAL_MODEL_PROVIDER=lmstudio\nLOCAL_MODEL_URL=http://localhost:1234\nLOCAL_MODEL_NAME=qwen/qwen3-coder-30b",
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
    title: "Discord, Telegram, Twitter/X, Twitch",
    description: "Multi-surface presence. Connect to Discord servers and DMs, Telegram groups, Twitter/X with autonomous tweets, and Twitch chat simultaneously.",
  },
  {
    title: "Native Email Automation",
    description: "IMAP/SMTP integrated. It can read your inbox, summarize threads, and draft replies autonomously.",
  },
  {
    title: "Multi-Task Planner & Reflection",
    description: "Decomposes complex queries into parallel sub-tasks with dependency ordering. A reflection engine evaluates tool results and retries when needed.",
  },
  {
    title: "Web Search (Tavily)",
    description: "Fresh Tavily-backed web search for current answers. Schedule-aware date handling and deep-search mode for thorough research.",
  },
  {
    title: "Desktop & Browser Automation",
    description: "PyAutoGUI + Playwright. Confirmation-gated actions to control your machine, automate browsers, and capture screenshots.",
  },
  {
    title: "Document RAG & FAISS Memory",
    description: "Upload local documents and build a long-term, searchable semantic memory bank. Profile facts, curated notes, and vector retrieval.",
  },
  {
    title: "Inline Code Editing",
    description: "SEARCH/REPLACE diff editing with 80-95% token savings. Accept/decline in the diff view. Workspace explorer with file tree.",
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
  --page-scale: 1.1;
  --bg: #000000;
  --bg-alt: #0a0a0a;
  --fg: #ffffff;
  --fg-muted: #888888;
  --border: #333333;
  --accent: #ffffff;
  --accent-blue: #4f8eff;
  --accent-purple: #a855f7;
  --accent-green: #4ade80;
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
.site-main {
  width: 100%;
  min-height: 100vh;
}
.container { width: min(1520px, 100% - 56px); margin: 0 auto; position: relative; z-index: 2; }

.nav {
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(14px);
  background: rgba(0, 0, 0, 0.88);
  border-bottom: 1px solid var(--border);
  transition: all 0.3s ease;
}
.nav-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 26px 0;
  gap: 44px;
}
.logo {
  display: flex;
  align-items: center;
  gap: 18px;
  font-family: 'Space Grotesk', sans-serif;
  font-weight: 700;
  font-size: 26px;
  letter-spacing: -0.02em;
}
.logo-square {
  width: 18px;
  height: 18px;
  background: #fff;
}
.nav-links {
  display: flex;
  gap: 36px;
  font-size: 16px;
  font-weight: 600;
  color: var(--fg-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.nav-links a {
  transition: color 0.2s ease;
}
.nav-links a:hover { color: var(--fg); }
.nav-cta { display: flex; gap: 18px; }

.hero {
  padding: 120px 0 90px;
  position: relative;
}
.hero-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 28px;
  align-items: center;
  justify-items: center;
  width: 100%;
  max-width: 1040px;
  margin: 0 auto;
}
.hero-copy {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  max-width: 940px;
  margin: 0 auto;
}
.eyebrow {
  display: inline-block;
  padding: 5px 12px;
  border: 1px solid var(--fg);
  font-family: 'Space Grotesk', monospace;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 20px;
}
.hero h1 {
  font-family: 'Space Grotesk', sans-serif;
  font-weight: 700;
  font-size: clamp(54px, 7vw, 90px);
  line-height: 1.05;
  margin: 0 0 20px;
  letter-spacing: -0.03em;
}
.hero p {
  font-size: 19px;
  line-height: 1.65;
  color: var(--fg-muted);
  margin: 0 auto 28px;
  max-width: 840px;
}
.hero-actions { display: flex; gap: 14px; justify-content: center; flex-wrap: wrap; }

.btn {
  padding: 14px 28px;
  font-weight: 600;
  font-size: 15px;
  transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1);
  border-radius: 18px;
  font-family: 'Space Grotesk', sans-serif;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  box-shadow: 0 10px 30px -18px rgba(0,0,0,0.7), inset 0 1px 0 rgba(255,255,255,0.14);
}
.btn.primary {
  background: linear-gradient(135deg, rgba(255,255,255,0.98), rgba(226,232,240,0.92));
  color: var(--bg);
  border: 1px solid rgba(255,255,255,0.9);
}
.btn.primary:hover {
  transform: translateY(-2px);
  background: linear-gradient(135deg, rgba(255,255,255,1), rgba(248,250,252,0.96));
  color: var(--bg);
  box-shadow: 0 18px 44px -20px rgba(255,255,255,0.34), inset 0 1px 0 rgba(255,255,255,0.22);
}
.btn.ghost {
  background: linear-gradient(135deg, rgba(255,255,255,0.08), rgba(255,255,255,0.02));
  color: var(--fg);
  border: 1px solid var(--border);
}
.btn.ghost:hover {
  transform: translateY(-2px);
  border-color: rgba(255,255,255,0.35);
  background: linear-gradient(135deg, rgba(255,255,255,0.12), rgba(255,255,255,0.04));
  box-shadow: 0 18px 44px -20px rgba(255,255,255,0.16), inset 0 1px 0 rgba(255,255,255,0.14);
}

.feature-grid, .workflow-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(310px, 1fr));
  gap: 28px;
  margin-top: 66px;
}
.feature-card, .workflow-card {
  padding: 36px;
  border: 1px solid var(--border);
  background: rgba(0,0,0,0.5);
  transition: border-color 0.3s ease;
}
.feature-card:hover, .workflow-card:hover {
  border-color: var(--fg);
}
.feature-card h3, .workflow-card h4 {
  margin: 0 0 18px;
  font-family: 'Space Grotesk', sans-serif;
  font-size: 22px;
}
.feature-card p, .workflow-card p {
  color: var(--fg-muted);
  font-size: 16px;
  line-height: 1.55;
  margin: 0;
}

.footer {
  border-top: 1px solid var(--border);
  padding: 44px 0;
  margin-top: 88px;
  font-size: 14px;
  color: var(--fg-muted);
  background: var(--bg-alt);
}

/* Bento Box Layout */
.bento-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 28px;
}
.bento-card {
  padding: 44px;
  border: 1px solid var(--border);
  background: rgba(0,0,0,0.5);
  transition: all 0.4s ease;
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  min-height: 310px;
}
.bento-card:hover {
  border-color: var(--fg);
  transform: translateY(-3px);
  box-shadow: 0 12px 44px -10px rgba(255,255,255,0.06);
}
.bento-card.span-2 { grid-column: span 2; }
.bento-card.span-3 { grid-column: span 3; }
.bento-card h3 {
  margin: 0 0 14px;
  font-family: 'Space Grotesk', sans-serif;
  font-size: 26px;
  z-index: 2;
  color: var(--fg);
}
.bento-card p {
  color: var(--fg-muted);
  font-size: 17px;
  line-height: 1.65;
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
  padding: 34px 0;
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
  gap: 18px;
  animation: scroll-left 180s linear infinite;
  white-space: nowrap;
}
@keyframes scroll-left {
  0% { transform: translateX(0); }
  100% { transform: translateX(-50%); }
}
.tool-pill {
  padding: 9px 18px;
  background: rgba(255,255,255,0.03);
  border: 1px solid var(--border);
  border-radius: 40px;
  font-family: 'Space Grotesk', monospace;
  font-size: 14px;
  color: var(--fg-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  display: inline-flex;
  align-items: center;
  gap: 9px;
}
.tool-pill::before {
  content: '';
  width: 7px;
  height: 7px;
  background: var(--border);
  border-radius: 50%;
  display: inline-block;
}

/* Timeline Layout for 5-Stage Pipeline */
.timeline-grid {
  display: flex;
  flex-direction: column;
  gap: 44px;
  position: relative;
  max-width: 880px;
  margin-left: auto;
  margin-right: auto;
}
.timeline-grid::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 26px;
  width: 1px;
  background: var(--border);
}
.timeline-card {
  position: relative;
  padding-left: 88px;
}
.timeline-node {
  position: absolute;
  left: 19px;
  top: 7px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--bg);
  border: 2px solid var(--fg);
  z-index: 2;
}
.timeline-card h4 {
  margin: 0 0 14px;
  font-family: 'Space Grotesk', sans-serif;
  font-size: 22px;
  color: var(--fg);
}
.timeline-card p {
  color: var(--fg-muted);
  font-size: 17px;
  line-height: 1.65;
  margin: 0;
}

/* Integration card grid */
.integration-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 28px;
}
.integration-card {
  padding: 44px;
  background: rgba(0,0,0,0.5);
  border: 1px solid var(--border);
  border-radius: 18px;
  transition: transform 0.3s ease, box-shadow 0.3s ease, border-color 0.3s ease;
  position: relative;
  overflow: hidden;
}
.integration-card:hover {
  transform: translateY(-4px);
  border-color: rgba(255,255,255,0.3);
  box-shadow: 0 12px 40px -8px rgba(255,255,255,0.06);
}

/* Glow animation for accent cards */
@keyframes glow-pulse {
  0%, 100% { box-shadow: 0 0 0px transparent; }
  50% { box-shadow: 0 0 20px rgba(79, 142, 255, 0.15); }
}
.glow-card {
  animation: glow-pulse 4s ease-in-out infinite;
}

/* Section title styling */
.section-title h2 {
  font-family: 'Space Grotesk', sans-serif;
  font-size: clamp(28px, 3.5vw, 44px);
  font-weight: 700;
  letter-spacing: -0.02em;
  text-align: center;
  margin: 0;
}
.section-title p {
  font-size: 18px;
  color: var(--fg-muted);
  text-align: center;
  margin: 14px auto 0;
  max-width: 700px;
  line-height: 1.6;
}

/* Stat counter */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 28px;
  margin-top: 60px;
}
.stat-card {
  text-align: center;
  padding: 36px 20px;
  border: 1px solid var(--border);
  background: rgba(0,0,0,0.5);
  transition: border-color 0.3s ease;
}
.stat-card:hover { border-color: var(--fg); }
.stat-number {
  font-family: 'Space Grotesk', sans-serif;
  font-size: 48px;
  font-weight: 700;
  display: block;
  margin-bottom: 8px;
}
.stat-label {
  font-size: 15px;
  color: var(--fg-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

@media (max-width: 900px) {
  .bento-grid { grid-template-columns: 1fr; }
  .bento-card.span-2, .bento-card.span-3 { grid-column: span 1; }
  .integration-grid { grid-template-columns: 1fr; }
  .stat-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 600px) {
  .stat-grid { grid-template-columns: 1fr; }
  .nav-links { display: none; }
}
`;


const GeometricBackground: React.FC = () => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let w = (canvas.width = window.innerWidth);
    let h = (canvas.height = window.innerHeight);

    const shapes: any[] = [];
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

        if (s.x < -50) s.x = w + 50;
        if (s.x > w + 50) s.x = -50;
        if (s.y < -50) s.y = h + 50;
        if (s.y > h + 50) s.y = -50;

        ctx.beginPath();
        if (s.type === "square") {
          ctx.strokeRect(s.x, s.y, s.size, s.size);
        } else {
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

  return <canvas ref={canvasRef} className="geometric-canvas" />;
};



const Sidebar: React.FC = () => {
  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <img src="/logo.png" alt="EchoSpeak Logo" style={{ width: 24, borderRadius: 4 }} />
      </div>
      <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: '24px', alignItems: 'center' }}>
        <a href={LINKS.github} target="_blank" rel="noreferrer" title="GitHub" style={{ opacity: 0.6 }}>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.042-1.416-4.042-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z" /></svg>
        </a>
      </div>
    </aside>
  );
};

export const Marketing: React.FC = () => {
  const reduceMotion = useReducedMotion();
  const viewport = { once: true, amount: 0.3 };

  const fadeUp = (delay = 0): Variants => ({
    hidden: { opacity: 0, y: reduceMotion ? 0 : 40 },
    show: {
      opacity: 1,
      y: 0,
      transition: { duration: 0.8, ease: [0.215, 0.61, 0.355, 1], delay },
    },
  });

  const stagger: Variants = {
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

  return (
    <div className="site">
      <style>{globalCss}</style>
      <GeometricBackground />

      <main className="site-main">
        <header className="nav">
          <div className="container nav-inner">
            <div className="logo">
              <img src="/logo.png" alt="EchoSpeak" style={{ width: 22, borderRadius: 4 }} />
              <span style={{ fontFamily: 'Space Grotesk' }}>EchoSpeak</span>
            </div>
            <nav className="nav-links">
              <a href="#overview">Vision</a>
              <a href="#features">Core</a>
              <a href="#meet-echo">Avatar</a>
              <a href={LINKS.discord}>Community</a>
              <a href={LINKS.github}>GitHub</a>
            </nav>
            <div className="nav-cta">
              <Link className="btn primary" to="/app" style={{ padding: '15px 34px', fontSize: '16px' }}>
                Access Platform
              </Link>
            </div>
          </div>
        </header>

        <motion.section className="hero" id="overview" initial="hidden" animate="show" variants={stagger}>
          <div className="container hero-grid">
            <motion.div className="hero-copy" variants={fadeUp(0)}>
              <span className="eyebrow" style={{ borderColor: 'var(--fg)', background: 'rgba(255,255,255,0.05)' }}>EchoSpeak v7.1.0 • System Online</span>

              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '22px', margin: '0 0 26px' }}>
                <h1 style={{ margin: 0 }}>EchoSpeak</h1>
              </div>
              <p style={{ maxWidth: '880px', fontSize: '22px' }}>
                The Autonomous, Local-First AI Operating System.
              </p>

              <div style={{
                padding: '18px 20px',
                background: 'rgba(0,0,0,0.8)',
                border: '1px solid var(--border)',
                borderRadius: '10px',
                maxWidth: '660px',
                margin: '0 auto 36px',
                fontFamily: 'monospace',
                fontSize: '15px',
                textAlign: 'left',
                color: 'var(--fg-muted)',
                overflow: 'hidden',
                whiteSpace: 'nowrap',
                textOverflow: 'ellipsis',
                display: 'flex',
                alignItems: 'center',
                gap: '10px'
              }}>
                <span style={{ color: '#4ade80', flexShrink: 0 }}>➜</span>
                <span style={{ flexShrink: 0 }}>root@echo:~#</span>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  <TypewriterText items={[
                    "\"Read my unread emails and draft replies.\"",
                    "\"Open Chrome and find flight deals to Tokyo.\"",
                    "\"Analyze my screen and explain this code error.\"",
                    "\"Search Discord for the latest project updates.\"",
                    "\"Read my codebase and refactor api/server.py.\"",
                    "\"Tweet about what I shipped today.\"",
                    "\"Wait quietly and brief me every morning at 9am.\""
                  ]} />
                </span>
              </div>
              <div className="hero-actions">
                <Link className="btn primary" to="/app" style={{ padding: '16px 36px', fontSize: '16px' }}>
                  Initialize System
                </Link>
                <a className="btn ghost" href={LINKS.github} style={{ padding: '16px 36px', fontSize: '16px' }}>
                  View Source
                </a>
              </div>
            </motion.div>
          </div>
        </motion.section>

        {/* Removed Meet Echo section from here to drop it down below Core Capabilities */}

        <motion.section className="section" id="features" initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.2 }} variants={stagger} style={{ padding: '90px 0 110px', borderTop: '1px solid var(--border)' }}>
          <div className="container">
            <motion.div className="section-title" variants={fadeUp(0)} style={{ marginBottom: '66px' }}>
              <h2>CORE CAPABILITIES</h2>
              <p>50+ tools, 5 platforms, 1 unified agent pipeline.</p>
            </motion.div>
            <motion.div className="bento-grid" variants={stagger}>
              {features.map((feature, idx) => (
                <motion.div
                  className={`bento-card ${feature.className}`}
                  key={feature.title}
                  variants={fadeUp(0)}
                  animate={floatAnimation}
                  style={{ animationDelay: `${idx * 0.2}s` }}
                >
                  <h3>{feature.title}</h3>
                  <p>{feature.description}</p>
                </motion.div>
              ))}
            </motion.div>
          </div>
        </motion.section>

        <motion.section className="section" id="meet-echo" initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.2 }} variants={stagger} style={{ padding: '110px 0', borderTop: '1px solid var(--border)' }}>
          <div className="container" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))', gap: '66px', alignItems: 'center' }}>

            {/* Left: The Avatar */}
            <motion.div variants={fadeUp(0)} style={{ display: 'flex', justifyContent: 'center', position: 'relative' }}>
              <div style={{
                width: '352px', height: '352px',
                display: 'flex', alignItems: 'center', justifyContent: 'center'
              }}>
                <div style={{ transform: 'scale(2.4)' }}>
                  <SquareAvatarVisual speaking={false} backendOnline={true} isThinking={false} />
                </div>
              </div>
            </motion.div>

            {/* Right: The Soul/Personality text */}
            <motion.div variants={fadeUp(0.1)}>
              <span className="eyebrow" style={{ borderColor: 'var(--fg)', background: 'transparent' }}>Change Echo's Look</span>
              <h2 style={{ fontFamily: 'Space Grotesk', fontSize: 'clamp(36px, 4.5vw, 52px)', margin: '0 0 26px', lineHeight: 1.1 }}>
                A digital soul with persistent memory.
              </h2>
              <p style={{ fontSize: '19px', color: 'var(--fg-muted)', lineHeight: 1.65, marginBottom: '28px' }}>
                Echo isn't just a voice module — he's the interactive avatar of your entire local operating system. Designed with a synthetic, 8-bit personality, he visually reacts to real-time processing states and remembers your interactions.
              </p>
              <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '18px' }}>
                <li style={{ display: 'flex', gap: '14px', alignItems: 'flex-start' }}>
                  <span style={{ color: 'var(--fg)', fontSize: '22px' }}>✦</span>
                  <div>
                    <h4 style={{ margin: '0 0 5px', fontSize: '17px', color: 'var(--fg)', fontFamily: 'Space Grotesk' }}>Visual State Engine</h4>
                    <p style={{ margin: 0, fontSize: '16px', color: 'var(--fg-muted)', lineHeight: 1.55 }}>Watch him transition from sleep, to active scanning, to speaking. Thought bubbles give you a direct window into his background reasoning.</p>
                  </div>
                </li>
                <li style={{ display: 'flex', gap: '14px', alignItems: 'flex-start' }}>
                  <span style={{ color: 'var(--fg)', fontSize: '22px' }}>✦</span>
                  <div>
                    <h4 style={{ margin: '0 0 5px', fontSize: '17px', color: 'var(--fg)', fontFamily: 'Space Grotesk' }}>Persistent Memory System</h4>
                    <p style={{ margin: 0, fontSize: '16px', color: 'var(--fg-muted)', lineHeight: 1.55 }}>Profile facts, curated durable memories, FAISS vector retrieval, and document RAG. He remembers your projects, context, and preferences securely on your own disk.</p>
                  </div>
                </li>
                <li style={{ display: 'flex', gap: '14px', alignItems: 'flex-start' }}>
                  <span style={{ color: 'var(--fg)', fontSize: '22px' }}>✦</span>
                  <div>
                    <h4 style={{ margin: '0 0 5px', fontSize: '17px', color: 'var(--fg)', fontFamily: 'Space Grotesk' }}>Customizable Soul</h4>
                    <p style={{ margin: 0, fontSize: '16px', color: 'var(--fg-muted)', lineHeight: 1.55 }}>Define how he communicates via SOUL.md. Edit his personality live in the Web UI Soul tab — tone, values, and behavioral style are all configurable.</p>
                  </div>
                </li>
              </ul>
            </motion.div>

          </div>
        </motion.section>

        {/* Stats Section */}
        <motion.section className="section" initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.3 }} variants={stagger} style={{ padding: '80px 0', borderTop: '1px solid var(--border)' }}>
          <div className="container">
            <div className="stat-grid">
              <motion.div className="stat-card" variants={fadeUp(0)}>
                <span className="stat-number">50+</span>
                <span className="stat-label">Executable Tools</span>
              </motion.div>
              <motion.div className="stat-card" variants={fadeUp(0.05)}>
                <span className="stat-number">5</span>
                <span className="stat-label">Pipeline Stages</span>
              </motion.div>
              <motion.div className="stat-card" variants={fadeUp(0.1)}>
                <span className="stat-number">6</span>
                <span className="stat-label">Surfaces</span>
              </motion.div>
              <motion.div className="stat-card" variants={fadeUp(0.15)}>
                <span className="stat-number">3</span>
                <span className="stat-label">Memory Layers</span>
              </motion.div>
            </div>
          </div>
        </motion.section>

        {/* Visual Integrations Section */}
        <motion.section className="section alt" initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.15 }} variants={stagger} style={{ padding: '50px 0 110px', borderTop: '1px solid var(--border)' }}>
          <div className="container">
            <motion.div className="section-title" variants={fadeUp(0)} style={{ marginBottom: '50px' }}>
              <h2>MULTI-SURFACE INTEGRATIONS</h2>
              <p>One agent brain, connected everywhere.</p>
            </motion.div>

            <motion.div className="integration-grid" variants={stagger}>

              {/* Discord */}
              <motion.div className="integration-card" variants={fadeUp(0)}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '18px', marginBottom: '22px' }}>
                  <svg width="34" height="34" viewBox="0 0 127.14 96.36" fill="var(--fg)"><path d="M107.7 8.07A105.15 105.15 0 0081.47 0a72.06 72.06 0 00-3.36 6.83 97.68 97.68 0 00-29.08 0A72.37 72.37 0 0045.67 0a105.46 105.46 0 00-26.23 8.07C2.04 33.84-2.23 58.9.79 83.56a105.74 105.74 0 0032.14 16.15 77.7 77.7 0 006.89-11.3 68.42 68.42 0 01-10.85-5.18c.91-.66 1.8-1.34 2.66-2a75.57 75.57 0 0064.32 0c.87.71 1.76 1.39 2.68 2a67.48 67.48 0 01-10.87 5.19 77 77 0 006.89 11.29 105.25 105.25 0 0032.19-16.15c3.31-27.46-2.57-50-19.44-75.49zM42.63 68.32c-5.2 0-9.49-4.78-9.49-10.6 0-5.83 4.19-10.6 9.49-10.6s9.54 4.78 9.49 10.6c0 5.82-4.24 10.6-9.49 10.6zm41.96 0c-5.2 0-9.49-4.78-9.49-10.6 0-5.83 4.19-10.6 9.49-10.6s9.54 4.78 9.49 10.6c0 5.82-4.24 10.6-9.49 10.6z" /></svg>
                  <h3 style={{ margin: 0, fontSize: '26px', fontFamily: '"Space Grotesk", sans-serif' }}>Discord</h3>
                </div>
                <p style={{ color: 'var(--fg-muted)', fontSize: '16px', lineHeight: 1.65, margin: 0 }}>Reads channels, intercepts @mentions, captures DMs, and replies autonomously. Role-based access control with OWNER/TRUSTED/PUBLIC tiers. Intent-based routing for natural phrasing.</p>
              </motion.div>

              {/* Telegram */}
              <motion.div className="integration-card" variants={fadeUp(0.05)}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '18px', marginBottom: '22px' }}>
                  <svg width="34" height="34" viewBox="0 0 24 24" fill="var(--fg)"><path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.696.065-1.225-.46-1.9-.902-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.888-.662 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z" /></svg>
                  <h3 style={{ margin: 0, fontSize: '26px', fontFamily: '"Space Grotesk", sans-serif' }}>Telegram</h3>
                </div>
                <p style={{ color: 'var(--fg-muted)', fontSize: '16px', lineHeight: 1.65, margin: 0 }}>Native bot polling loop built-in. Read channel posts, synchronize message histories, and let the agent proactively post alerts directly to your groups.</p>
              </motion.div>

              {/* Twitter / X */}
              <motion.div className="integration-card" variants={fadeUp(0.1)}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '18px', marginBottom: '22px' }}>
                  <svg width="34" height="34" viewBox="0 0 24 24" fill="var(--fg)"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" /></svg>
                  <h3 style={{ margin: 0, fontSize: '26px', fontFamily: '"Space Grotesk", sans-serif' }}>Twitter / X</h3>
                </div>
                <p style={{ color: 'var(--fg-muted)', fontSize: '16px', lineHeight: 1.65, margin: 0 }}>Autonomous tweet generation grounded by real git commits and code diffs. Mention polling with auto-reply. Changelog tweets on new commits. Approval-gated or auto-post modes.</p>
              </motion.div>

              {/* Twitch */}
              <motion.div className="integration-card" variants={fadeUp(0.15)}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '18px', marginBottom: '22px' }}>
                  <svg width="34" height="34" viewBox="0 0 24 24" fill="var(--fg)"><path d="M11.571 4.714h1.715v5.143H11.57zm4.715 0H18v5.143h-1.714zM6 0L1.714 4.286v15.428h5.143V24l4.286-4.286h3.428L22.286 12V0zm14.571 11.143l-3.428 3.428h-3.429l-3 3v-3H6.857V1.714h13.714Z" /></svg>
                  <h3 style={{ margin: 0, fontSize: '26px', fontFamily: '"Space Grotesk", sans-serif' }}>Twitch</h3>
                </div>
                <p style={{ color: 'var(--fg-muted)', fontSize: '16px', lineHeight: 1.65, margin: 0 }}>EventSub webhook integration with HMAC verification. Chat messages mentioning the bot or starting with ! are routed through the full agent pipeline. Stream online/offline event handling.</p>
              </motion.div>

              {/* Terminal */}
              <motion.div className="integration-card" variants={fadeUp(0.2)}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '18px', marginBottom: '22px' }}>
                  <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="var(--fg)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="4 17 10 11 4 5" /><line x1="12" y1="19" x2="20" y2="19" /></svg>
                  <h3 style={{ margin: 0, fontSize: '26px', fontFamily: '"Space Grotesk", sans-serif' }}>Terminal</h3>
                </div>
                <p style={{ color: 'var(--fg-muted)', fontSize: '16px', lineHeight: 1.65, margin: 0 }}>Allowlisted shell command execution. Run git, python, npm, pytest, and more. Bubble Tea Go TUI for terminal-native interaction with streaming responses.</p>
              </motion.div>

              {/* Email */}
              <motion.div className="integration-card" variants={fadeUp(0.25)}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '18px', marginBottom: '22px' }}>
                  <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="var(--fg)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect width="20" height="16" x="2" y="4" rx="2" /><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" /></svg>
                  <h3 style={{ margin: 0, fontSize: '26px', fontFamily: '"Space Grotesk", sans-serif' }}>Email</h3>
                </div>
                <p style={{ color: 'var(--fg-muted)', fontSize: '16px', lineHeight: 1.65, margin: 0 }}>IMAP inbox reader and SMTP mail sender. Summarize threads, draft replies, and send emails autonomously — all confirmation-gated for safety.</p>
              </motion.div>

            </motion.div>
          </div>
        </motion.section>

        <motion.section className="section" initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.2 }} variants={stagger} style={{ padding: '110px 0', borderTop: '1px solid var(--border)' }}>
          <div className="container">
            <motion.div className="section-title" variants={fadeUp(0)} style={{ marginBottom: '66px' }}>
              <h2>THE 5-STAGE COGNITION PIPELINE</h2>
              <p>Every query flows through a modular, testable pipeline — from parse to finalize.</p>
            </motion.div>
            <motion.div className="timeline-grid" variants={stagger}>
              {workflow.map((step, idx) => (
                <motion.div className="timeline-card" key={step.title} variants={fadeUp(0)}>
                  <motion.div className="timeline-node" animate={pulseAnimation} style={{ animationDelay: `${idx * 0.4}s` }} />
                  <h4>{step.title}</h4>
                  <p>{step.description}</p>
                </motion.div>
              ))}
            </motion.div>
          </div>
        </motion.section>

        {/* Tools & Skills Section */}
        <motion.section className="section alt" initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.15 }} variants={stagger} style={{ padding: '50px 0 110px', borderTop: 'none', background: 'rgba(255,255,255,0.01)' }}>
          <div className="container">

            <motion.div className="bento-grid" variants={stagger}>
              <motion.div className="bento-card span-2" variants={fadeUp(0)}>
                <div style={{ padding: '14px', background: 'var(--fg)', borderRadius: '14px', width: 'fit-content', marginBottom: '26px', color: 'var(--bg)' }}>
                  <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m12 14 4-4" /><path d="M3.34 19a10 10 0 1 1 17.32 0" /></svg>
                </div>
                <h3>Executable Tools</h3>
                <p>Desktop automation, Playwright browser control, terminal commands, file operations, codebase editing, email, and web search — all securely restricted by Workspace boundaries. Every destructive execution runs through a strict per-action confirmation gate.</p>
                <div style={{ display: 'flex', gap: '9px', flexWrap: 'wrap', marginTop: '18px', position: 'relative', zIndex: 2 }}>
                  {["desktop_click", "desktop_type_text", "browse_task", "web_search", "terminal_run", "file_write", "file_read", "edit_source_code", "email_send", "email_read_inbox", "discord_send_channel", "tweet_post"].map((t) => (
                    <motion.span key={t} whileHover={{ scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }} className="tool-pill" style={{ cursor: 'pointer' }}>{t}</motion.span>
                  ))}
                </div>
              </motion.div>

              <motion.div className="bento-card" variants={fadeUp(0.1)}>
                <div style={{ padding: '14px', background: 'var(--fg)', borderRadius: '14px', width: 'fit-content', marginBottom: '26px', color: 'var(--bg)' }}>
                  <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10" /></svg>
                </div>
                <h3>Skills & Plugins</h3>
                <p>Inject custom reasoning patterns, pipeline hooks, and tool bundles directly into the 5-stage processing pipeline. Skills can intercept any stage for instant responses or context injection.</p>
                <div style={{ display: 'flex', gap: '9px', flexWrap: 'wrap', marginTop: '18px', position: 'relative', zIndex: 2 }}>
                  {["system_monitor", "daily_briefing", "code_reviewer", "memory_curator"].map((t) => (
                    <motion.span key={t} whileHover={{ scale: 1.05, borderColor: '#fff', boxShadow: '0 0 15px rgba(255,255,255,0.2)' }} className="tool-pill" style={{ cursor: 'pointer' }}>{t}</motion.span>
                  ))}
                </div>
              </motion.div>
            </motion.div>
          </div>
        </motion.section>

        {/* Security & Safety Section */}
        <motion.section className="section" initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.2 }} variants={stagger} style={{ padding: '110px 0', borderTop: '1px solid var(--border)' }}>
          <div className="container">
            <motion.div className="section-title" variants={fadeUp(0)} style={{ marginBottom: '66px' }}>
              <h2>LAYERED SECURITY MODEL</h2>
              <p>Six layers of protection between intent and execution.</p>
            </motion.div>

            <motion.div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '28px' }} variants={stagger}>
              {[
                { icon: <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>, title: "Environment Flags", desc: "Master switches gate every capability. ENABLE_SYSTEM_ACTIONS, ALLOW_FILE_WRITE, ALLOW_TERMINAL_COMMANDS, and more." },
                { icon: <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>, title: "Discord User Roles", desc: "OWNER / TRUSTED / PUBLIC tiers. Rate limits, tool lockdown, and memory isolation per role." },
                { icon: <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>, title: "Workspace Allowlists", desc: "TOOLS.txt and SKILLS.txt define the ceiling of available capabilities per workspace context." },
                { icon: <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>, title: "Prompt Injection Guard", desc: "20+ regex patterns across 4 severity tiers detect and block malicious prompt injection attempts." },
                { icon: <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>, title: "Rate Limiting", desc: "Per-user rate limits by role tier. OWNER 60/min, TRUSTED 20/min, PUBLIC 5/min." },
                { icon: <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>, title: "Confirmation Gate", desc: "Every side-effect action pauses behind a persisted approval record. You always reply confirm or cancel." },
              ].map((item, idx) => (
                <motion.div key={item.title} variants={fadeUp(idx * 0.05)} style={{
                  padding: '36px',
                  border: '1px solid var(--border)',
                  background: 'linear-gradient(135deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01))',
                  backdropFilter: 'blur(12px)',
                  WebkitBackdropFilter: 'blur(12px)',
                  transition: 'all 0.3s cubic-bezier(0.16, 1, 0.3, 1)',
                  borderRadius: '16px',
                }}
                  whileHover={{ borderColor: 'rgba(255,255,255,0.3)', y: -4, boxShadow: '0 12px 30px -10px rgba(0,0,0,0.5)' }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: '52px', height: '52px', borderRadius: '14px', background: 'linear-gradient(135deg, rgba(255,255,255,0.1), rgba(255,255,255,0.02))', border: '1px solid rgba(255,255,255,0.1)', color: 'var(--fg)', marginBottom: '22px', boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.2)' }}>{item.icon}</div>
                  <h4 style={{ margin: '0 0 12px', fontFamily: 'Space Grotesk', fontSize: '20px', color: 'var(--fg)' }}>{item.title}</h4>
                  <p style={{ margin: 0, fontSize: '16px', color: 'var(--fg-muted)', lineHeight: 1.6 }}>{item.desc}</p>
                </motion.div>
              ))}
            </motion.div>
          </div>
        </motion.section>

        {/* Architecture Overview Section */}
        <motion.section className="section" initial="hidden" whileInView="show" viewport={{ once: true, amount: 0.2 }} variants={stagger} style={{ padding: '110px 0', borderTop: '1px solid var(--border)' }}>
          <div className="container">
            <motion.div className="section-title" variants={fadeUp(0)} style={{ marginBottom: '66px' }}>
              <h2>ARCHITECTURE</h2>
              <p>A modular, privacy-first agent platform built for extensibility.</p>
            </motion.div>

            <motion.div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '24px' }} variants={stagger}>
              {[
                { title: "Web UI (React/Vite)", desc: "Streaming chat, inline code diff, workspace explorer, research panel, and the interactive Meet Echo avatar.", meta: "apps/web/" },
                { title: "FastAPI Server", desc: "REST API, streaming endpoints, provider routing, approval/execution/trace endpoints, and workspace management.", meta: "apps/backend/api/" },
                { title: "Agent Core", desc: "5-stage query pipeline, LLM wrapper with reasoning extraction, tool routing, memory, confirmation gating, and multi-task planning.", meta: "agent/core.py" },
                { title: "Tool Registry", desc: "50+ tools with metadata, permission flags, and policy gating. Skills auto-register custom tools on load.", meta: "agent/tools.py" },
                { title: "Memory System", desc: "FAISS vector store, profile facts, curated durable memories, document RAG with BM25+reranking, and pinned memory injection.", meta: "agent/memory.py" },
                { title: "Go TUI", desc: "Bubble Tea terminal client with session management, streaming responses, and mic capture via FFmpeg.", meta: "apps/tui/" },
              ].map((item, idx) => (
                <motion.div key={item.title} variants={fadeUp(idx * 0.05)} style={{
                  padding: '32px',
                  border: '1px solid var(--border)',
                  background: 'rgba(0,0,0,0.5)',
                  transition: 'border-color 0.3s ease',
                }}
                  whileHover={{ borderColor: 'rgba(255,255,255,0.4)' }}
                >
                  <h4 style={{ margin: '0 0 12px', fontFamily: 'Space Grotesk', fontSize: '20px', color: 'var(--fg)' }}>{item.title}</h4>
                  <p style={{ margin: '0 0 16px', fontSize: '15px', color: 'var(--fg-muted)', lineHeight: 1.6 }}>{item.desc}</p>
                  <span style={{ fontFamily: 'monospace', fontSize: '13px', color: 'var(--fg-muted)', opacity: 0.7 }}>{item.meta}</span>
                </motion.div>
              ))}
            </motion.div>
          </div>
        </motion.section>

        <section className="marquee-container" style={{ marginTop: '0', borderBottom: 'none' }}>
          <div className="marquee-content">
            {allTools.concat(allTools).map((tool, i) => (
              <span key={i} className="tool-pill">{tool}</span>
            ))}
          </div>
        </section>

        <footer className="footer" style={{ marginTop: 0 }}>
          <div className="container" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
            <p style={{ margin: 0 }}>© 2026 EchoSpeak Labs. System Online. v7.1.0</p>
            <div style={{ display: 'flex', gap: '24px' }}>
              <a href={LINKS.github} style={{ color: 'var(--fg-muted)', transition: 'color 0.2s' }}>GitHub</a>
              <a href={LINKS.discord} style={{ color: 'var(--fg-muted)', transition: 'color 0.2s' }}>Discord</a>
              <a href={LINKS.x} style={{ color: 'var(--fg-muted)', transition: 'color 0.2s' }}>X</a>
            </div>
          </div>
        </footer>
      </main>
    </div>
  );
};
// Marketing component is exported above. Rendering handled by App.tsx router.
