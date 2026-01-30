import React, { useEffect, useRef } from "react";
import ReactDOM from "react-dom/client";
import { motion, useReducedMotion, type Variants, AnimatePresence } from "framer-motion";
import { Link } from "react-router-dom";
import { gsap } from "gsap";

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
    title: "Local-first by design",
    description:
      "Keep data on your machine with configurable models, storage, and streaming. No forced cloud lock-in.",
  },
  {
    title: "Voice, vision, automation",
    description:
      "Speak naturally, analyze screens, and trigger automations with confirmation gates for safety.",
  },
  {
    title: "Tools that feel native",
    description:
      "Search, file access, browser control, and desktop actions live in one cohesive assistant workspace.",
  },
  {
    title: "Built to extend",
    description:
      "Drop in new tools, providers, or workflows without rewriting the core. EchoSpeak grows with you.",
  },
];

const workflow = [
  {
    title: "Listen",
    description: "Capture voice, text, and context in real time with low-latency streaming.",
  },
  {
    title: "Reason",
    description: "Route every request through structured planning, tool selection, and safety rules.",
  },
  {
    title: "Confirm and act",
    description: "Sensitive actions always prompt for confirmation, so you stay in control.",
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
      "Captures mic + text input, streams events to /query/stream, and plays /tts audio.",
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
      "Bubble Tea terminal client that consumes the same streaming API and /tts audio.",
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
      "USE_LOCAL_MODELS=true\\nLOCAL_MODEL_PROVIDER=lmstudio\\nLOCAL_MODEL_URL=http://localhost:1234\\nLOCAL_MODEL_NAME=openai/gpt-oss-20b\\nUSE_POCKET_TTS=true",
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
    description: "Final response + spoken_text returned for Pocket-TTS playback.",
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
  { method: "POST", path: "/tts", description: "Pocket-TTS synthesis returning audio/wav." },
  { method: "POST", path: "/stt", description: "Optional local speech-to-text transcription." },
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
    title: "Local search (SearxNG + DDG)",
    description: "Local-first search with optional SearxNG and automatic fallback.",
  },
  {
    title: "YouTube transcript + summary",
    description: "Pull transcripts and summarize video content with a single prompt.",
  },
  {
    title: "Playwright browse_task",
    description: "Confirmation-gated browsing for reliable page summaries.",
  },
  {
    title: "Windows desktop automation",
    description: "pywinauto + PyAutoGUI with confirmation before system actions.",
  },
  {
    title: "Document RAG + FAISS memory",
    description: "Upload documents, retrieve sources, and keep long-term memory local.",
  },
  {
    title: "Pocket-TTS voice",
    description: "High-quality local voice output via /tts for web + TUI.",
  },
  {
    title: "Local STT (optional)",
    description: "Offline speech-to-text via /stt when enabled.",
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
    title: "Voice (Pocket-TTS)",
    items: [
      "USE_POCKET_TTS",
      "POCKET_TTS_DEFAULT_VOICE",
      "POCKET_TTS_DEFAULT_VOICE_PROMPT",
      "POCKET_TTS_TEMP",
      "POCKET_TTS_MAX_CHARS",
    ],
  },
  {
    title: "Search + tools",
    items: ["SEARXNG_URL", "SEARXNG_TIMEOUT", "WEB_SEARCH_USE_SCRAPLING", "USE_TOOL_CALLING_LLM"],
  },
  {
    title: "STT + documents",
    items: ["LOCAL_STT_ENABLED", "LOCAL_STT_MODEL", "DOCUMENT_RAG_ENABLED", "DOC_UPLOAD_MAX_MB"],
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
    description: "TUI speaks spoken_text only while full replies stay visible.",
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
  padding: 140px 0 100px;
  position: relative;
}
.hero-grid {
  display: grid;
  grid-template-columns: 0.8fr 1.2fr;
  gap: 60px;
  align-items: center;
}
.eyebrow {
  display: inline-block;
  padding: 4px 10px;
  border: 1px solid var(--fg);
  font-family: 'Space Grotesk', monospace;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 24px;
}
.hero h1 {
  font-family: 'Space Grotesk', sans-serif;
  font-weight: 700;
  font-size: clamp(48px, 6vw, 80px);
  line-height: 1.05;
  margin: 0 0 24px;
  letter-spacing: -0.03em;
}
.hero p {
  font-size: 18px;
  line-height: 1.6;
  color: var(--fg-muted);
  margin: 0 0 32px;
  max-width: 480px;
}
.hero-actions { display: flex; gap: 16px; }

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
  background: var(--fg);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: auto;
}
.sidebar-logo img {
  filter: grayscale(100%) invert(1);
  width: 24px;
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

@media (max-width: 900px) {
  .sidebar { width: 100%; height: 60px; flex-direction: row; padding: 0 20px; bottom: 0; top: auto; border-right: none; border-top: 1px solid var(--border); }
  .site-main { padding-left: 0; padding-bottom: 60px; }
  .sidebar-logo { margin-bottom: 0; margin-right: auto; }
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
        <div style={{ width: '20px', height: '20px', background: '#000' }} />
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

  return (
    <div className="site">
      <style>{globalCss}</style>
      <GeometricBackground />
      <Sidebar />

      <main className="site-main" style={{ paddingLeft: '80px' }}>
        <header className="nav">
          <div className="container nav-inner">
            <div className="logo">
              <span className="logo-square" />
              <span style={{ fontFamily: 'Space Grotesk' }}>EchoSpeak</span>
            </div>
            <nav className="nav-links">
              <a href="#overview">Vision</a>
              <a href="#features">Core</a>
              <a href="#docs">Documentation</a>
              <a href={LINKS.discord}>Community</a>
              <a href={LINKS.github}>GitHub</a>
            </nav>
            <div className="nav-cta">
              <Link className="btn primary" to="/app" style={{ padding: '14px 32px', fontSize: '15px' }}>
                Access Platform
              </Link>
            </div>
          </div>
        </header>

        <motion.section className="hero" id="overview" initial="hidden" animate="show" variants={stagger}>
          <div className="container hero-grid">
            <motion.div variants={fadeUp(0)}>
              <span className="eyebrow">Local-First Intelligence</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: '20px', margin: '0 0 24px' }}>
                <span className="logo-square" style={{ width: 'clamp(24px, 3vw, 40px)', height: 'clamp(24px, 3vw, 40px)' }} />
                <h1 style={{ margin: 0 }}>EchoSpeak</h1>
              </div>
              <p>
                The private voice OS that redefines mastery. Local-first, privacy-absolute,
                and engineered for those who demand ultimate control.
              </p>
              <div className="hero-actions">
                <Link className="btn primary" to="/app">
                  Initialize
                </Link>
                <a className="btn ghost" href={LINKS.github}>
                  Source
                </a>
              </div>
            </motion.div>
            <motion.div className="hero-visual" variants={fadeUp(0.2)}>
              <div style={{
                width: '100%',
                border: '1px solid var(--border)',
                background: 'rgba(255,255,255,0.02)',
                boxShadow: '0 20px 50px rgba(0,0,0,0.5)',
                overflow: 'hidden'
              }}>
                <img
                  src="/REAL1.png"
                  alt="EchoSpeak Interface"
                  style={{ width: '100%', height: 'auto', display: 'block' }}
                />
              </div>
            </motion.div>
          </div>
        </motion.section>

        <motion.section className="section" id="features" initial="hidden" animate="show" variants={stagger}>
          <div className="container">
            <motion.div className="section-title" variants={fadeUp(0)} style={{ marginBottom: '60px' }}>
              <h2>CORE INNOVATIONS</h2>
            </motion.div>
            <motion.div className="feature-grid" variants={stagger}>
              {features.map((feature) => (
                <motion.div className="feature-card" key={feature.title} variants={fadeUp(0)}>
                  <h3>{feature.title}</h3>
                  <p>{feature.description}</p>
                </motion.div>
              ))}
            </motion.div>
          </div>
        </motion.section>

        <motion.section className="section alt" initial="hidden" animate="show" variants={stagger} style={{ padding: '100px 0' }}>
          <div className="container">
            <motion.div className="section-title" variants={fadeUp(0)}>
              <h2>LOGIC FLOW</h2>
            </motion.div>
            <motion.div className="workflow-grid" variants={stagger}>
              {workflow.map((step) => (
                <motion.div className="workflow-card" key={step.title} variants={fadeUp(0)}>
                  <h4>{step.title}</h4>
                  <p>{step.description}</p>
                </motion.div>
              ))}
            </motion.div>
          </div>
        </motion.section>

        <footer className="footer">
          <div className="container">
            <p>© 2026 EchoSpeak Labs. System Online.</p>
          </div>
        </footer>
      </main>
    </div>
  );
};
// Marketing component is exported above. Rendering handled by App.tsx router.
