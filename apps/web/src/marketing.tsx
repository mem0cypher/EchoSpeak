import React from "react";
import { Link } from "react-router-dom";
import { SquareAvatarVisual } from "./components/SquareAvatarVisual";

const stages = [
  ["01", "Interpret", "A backend-owned mode controller separates conversation, current-information research, and local coding work."],
  ["02", "Select context", "Echo assembles the active Project, Session continuity, relevant memory, and only the file or web evidence the turn needs."],
  ["03", "Plan", "The runtime creates a bounded workflow sized to the selected model instead of handing control to open-ended model prose."],
  ["04", "Use tools", "Typed tools run inside the Session and Project scope. Side effects pause behind durable approval records."],
  ["05", "Verify", "Tool outcomes, evidence, errors, and verification are recorded as Items and ToolRuns before Echo reports completion."],
  ["06", "Continue", "Interrupted and unfinished work is recovered from durable Session state, with exact retry targets and a safest next action."],
] as const;

const capabilities = [
  ["Conversation that stays conversation", "Every mention of code does not become a coding job. Echo keeps explanatory questions in chat and promotes requests only when they need current evidence or workspace action."],
  ["Folder-backed Projects", "A Project is the durable container for one local folder and its context. It can contain multiple Sessions; Quick Chat Sessions can remain completely outside Projects."],
  ["Research with visible evidence", "Current-information requests can use web, browser, transcript, time, sports, and calculation tools. Search runs and sources are streamed into the Research view."],
  ["Coding with authority boundaries", "Read, write, terminal, and checkpoint tools are selected by phase and permission. Mutating operations remain approval-aware and Project-scoped."],
  ["Memory without context flooding", "Profile, preference, Project, note, document, and distilled Session memory are retrieved selectively and constrained by a model-aware context budget."],
  ["A runtime you can inspect", "The UI exposes plans, activities, approvals, executions, traces, code changes, research runs, Project state, model state, and memory health."],
] as const;

const runtimeRows = [
  ["Project", "Optional folder-backed boundary", "Workspace root, context, trust, Git metadata (read-only)"],
  ["Session", "Independent conversation or Project child", "History, current objective, permissions, memory, continuation"],
  ["Turn", "One user request", "Intent, plan, model snapshot, context budget, terminal status"],
  ["Item", "Typed event within a Turn", "Messages, reasoning, approvals, tool activity, verification"],
  ["ToolRun", "One exact tool invocation", "Canonical arguments, approval, outcome, retry lineage, evidence"],
] as const;

function Arrow() {
  return <span aria-hidden="true" className="arrow">→</span>;
}

export const Marketing: React.FC = () => (
  <main className="site">
    <style>{`
      :root { color-scheme: dark; }
      * { box-sizing: border-box; }
      html { scroll-behavior: smooth; }
      body { margin: 0; background: #050505; }
      .site { --fg:#f5f5f2; --muted:#969693; --line:rgba(255,255,255,.13); --panel:#0b0b0b; --red:#d94747; min-height:100vh; color:var(--fg); background:#050505; font-family:'Space Grotesk',Inter,system-ui,sans-serif; }
      .wrap { width:min(1180px,calc(100% - 40px)); margin:0 auto; }
      .nav { height:68px; display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid var(--line); }
      .brand { display:flex; align-items:center; gap:10px; color:var(--fg); text-decoration:none; font-weight:700; letter-spacing:-.02em; }
      .brand img { width:20px; height:20px; border-radius:3px; }
      .navlinks { display:flex; align-items:center; gap:24px; }
      .navlinks a { color:var(--muted); text-decoration:none; font:11px 'JetBrains Mono',monospace; text-transform:uppercase; letter-spacing:.09em; }
      .navlinks a:hover { color:#fff; }
      .access { display:inline-flex!important; align-items:center; gap:9px; color:#fff!important; border:1px solid rgba(255,255,255,.28); padding:10px 13px; border-radius:3px; background:#101010; }
      .hero { min-height:720px; display:grid; grid-template-columns:minmax(0,1.15fr) minmax(340px,.85fr); align-items:center; gap:70px; padding:90px 0 100px; }
      .eyebrow { color:var(--red); font:10px 'JetBrains Mono',monospace; letter-spacing:.16em; text-transform:uppercase; }
      h1 { font-size:clamp(56px,8vw,104px); line-height:.89; letter-spacing:-.075em; margin:24px 0 30px; max-width:820px; }
      .hero-copy { color:#b6b6b2; font-size:clamp(17px,2vw,21px); line-height:1.62; max-width:700px; }
      .hero-actions { display:flex; gap:10px; margin-top:36px; flex-wrap:wrap; }
      .primary,.secondary { display:inline-flex; align-items:center; gap:12px; padding:14px 17px; border-radius:3px; text-decoration:none; font:11px 'JetBrains Mono',monospace; text-transform:uppercase; letter-spacing:.08em; }
      .primary { background:#f3f3ef; color:#080808; }
      .secondary { border:1px solid var(--line); color:#fff; background:#0b0b0b; }
      .hero-visual { border:1px solid var(--line); background:#090909; min-height:455px; position:relative; display:grid; place-items:center; overflow:hidden; }
      .hero-visual:before { content:''; position:absolute; inset:0; background:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px); background-size:28px 28px; mask-image:linear-gradient(to bottom,#000,transparent 90%); }
      .avatar-box { position:relative; width:min(340px,86%); height:340px; }
      .avatar-box>div { height:100%; }
      .live-label { position:absolute; left:18px; bottom:16px; display:flex; gap:8px; align-items:center; color:var(--muted); font:10px 'JetBrains Mono',monospace; text-transform:uppercase; letter-spacing:.12em; }
      .live-label i { width:6px; height:6px; border-radius:50%; background:var(--red); box-shadow:0 0 12px rgba(217,71,71,.7); }
      section { border-top:1px solid var(--line); padding:100px 0; }
      .section-head { display:grid; grid-template-columns:240px 1fr; gap:40px; margin-bottom:52px; }
      .kicker { color:var(--muted); font:10px 'JetBrains Mono',monospace; letter-spacing:.14em; text-transform:uppercase; }
      h2 { font-size:clamp(36px,5vw,64px); line-height:1.02; letter-spacing:-.05em; margin:0; max-width:820px; }
      .lede { color:var(--muted); font-size:17px; line-height:1.7; max-width:770px; margin:20px 0 0; }
      .flow { display:grid; grid-template-columns:repeat(3,1fr); border:1px solid var(--line); }
      .stage { min-height:220px; padding:24px; border-right:1px solid var(--line); border-bottom:1px solid var(--line); background:var(--panel); }
      .stage:nth-child(3n) { border-right:0; }
      .stage:nth-child(n+4) { border-bottom:0; }
      .num { color:var(--red); font:10px 'JetBrains Mono',monospace; }
      .stage h3,.card h3 { font-size:18px; margin:36px 0 12px; letter-spacing:-.02em; }
      .stage p,.card p { margin:0; color:var(--muted); font-size:14px; line-height:1.65; }
      .model-map { display:grid; grid-template-columns:1fr 80px 1.1fr 80px 1fr; align-items:stretch; }
      .model-node { border:1px solid var(--line); background:var(--panel); padding:28px; min-height:230px; }
      .model-node strong { font-size:22px; display:block; margin-bottom:14px; }
      .model-node p { color:var(--muted); line-height:1.65; margin:0; font-size:14px; }
      .model-node.center { background:#101010; border-color:rgba(255,255,255,.25); }
      .arrow { display:grid; place-items:center; color:rgba(255,255,255,.45); font-size:24px; }
      .cards { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
      .card { border:1px solid var(--line); background:var(--panel); padding:25px; min-height:230px; }
      .card h3 { margin-top:28px; }
      .card-mark { font:10px 'JetBrains Mono',monospace; color:var(--red); text-transform:uppercase; letter-spacing:.12em; }
      .runtime { border:1px solid var(--line); }
      .runtime-row { display:grid; grid-template-columns:140px 1fr 1.5fr; gap:24px; padding:20px 22px; border-bottom:1px solid var(--line); align-items:center; }
      .runtime-row:last-child { border-bottom:0; }
      .runtime-row strong { font:12px 'JetBrains Mono',monospace; color:#fff; }
      .runtime-row span { color:var(--muted); font-size:13px; line-height:1.5; }
      .trust { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
      .trust-panel { border:1px solid var(--line); background:var(--panel); padding:30px; }
      .trust-panel h3 { font-size:24px; margin:0 0 14px; }
      .trust-panel p { color:var(--muted); line-height:1.7; }
      .steps { display:grid; gap:9px; margin-top:25px; }
      .steps div { display:flex; justify-content:space-between; gap:20px; border-top:1px solid var(--line); padding-top:11px; color:#d8d8d4; font:11px 'JetBrains Mono',monospace; }
      .steps span { color:var(--muted); }
      .cta { display:grid; grid-template-columns:1fr auto; align-items:end; gap:40px; padding-bottom:120px; }
      .cta h2 { max-width:760px; }
      footer { border-top:1px solid var(--line); padding:26px 0 40px; color:var(--muted); font:10px 'JetBrains Mono',monospace; text-transform:uppercase; letter-spacing:.1em; }
      @media(max-width:900px){ .hero{grid-template-columns:1fr;padding-top:60px}.hero-visual{min-height:390px}.section-head{grid-template-columns:1fr}.flow,.cards{grid-template-columns:1fr 1fr}.stage:nth-child(n){border-right:1px solid var(--line);border-bottom:1px solid var(--line)}.stage:nth-child(2n){border-right:0}.stage:nth-last-child(-n+2){border-bottom:0}.model-map{grid-template-columns:1fr}.arrow{height:56px;transform:rotate(90deg)}.trust{grid-template-columns:1fr}.cta{grid-template-columns:1fr;align-items:start}}
      @media(max-width:620px){ .wrap{width:min(100% - 24px,1180px)}.navlinks>a:not(.access){display:none}.hero{min-height:auto;padding:54px 0 72px;gap:42px}h1{font-size:55px}.hero-visual{min-height:330px}.avatar-box{height:290px}.flow,.cards{grid-template-columns:1fr}.stage:nth-child(n){border-right:0;border-bottom:1px solid var(--line)}.stage:last-child{border-bottom:0}.runtime-row{grid-template-columns:1fr;gap:7px}.section-head{margin-bottom:36px}section{padding:72px 0}}
    `}</style>

    <div className="wrap">
      <nav className="nav">
        <a className="brand" href="#top"><img src="/logo.png" alt="" />EchoSpeak</a>
        <div className="navlinks"><a href="#runtime">Runtime</a><a href="#capabilities">Capabilities</a><a href="#authority">Authority</a><Link className="access" to="/app">Access Platform <span>→</span></Link></div>
      </nav>

      <header className="hero" id="top">
        <div><div className="eyebrow">Local-first agent runtime</div><h1>One runtime.<br />Your models.<br />Exact outcomes.</h1><p className="hero-copy">EchoSpeak is a personal agent interface for conversation, research, and local project work. It connects local or hosted models to the same backend-controlled lifecycle, keeps every Session isolated, and makes tools, approvals, verification, and recovery visible.</p><div className="hero-actions"><Link className="primary" to="/app">Access Platform <span>→</span></Link><a className="secondary" href="#how">See how it works</a></div></div>
        <div className="hero-visual" aria-label="EchoSpeak avatar preview"><div className="avatar-box"><SquareAvatarVisual speaking={false} backendOnline={true} isThinking={false} /></div><div className="live-label"><i />Runtime ready</div></div>
      </header>

      <section id="how"><div className="section-head"><div className="kicker">Request lifecycle</div><div><h2>Models propose. The runtime decides what happened.</h2><p className="lede">The model is never the source of truth for permissions, tool success, retries, or completion. EchoSpeak classifies the request, scopes context, runs a bounded workflow, and records observed outcomes.</p></div></div><div className="flow">{stages.map(([n,title,copy])=><article className="stage" key={n}><div className="num">{n}</div><h3>{title}</h3><p>{copy}</p></article>)}</div></section>

      <section><div className="section-head"><div className="kicker">Model connection</div><div><h2>Local and hosted models enter through one contract.</h2><p className="lede">Provider formatting stays in adapters. Capability profiles tell the runtime how much context, planning depth, tool freedom, repair, and parallelism a model can reliably handle.</p></div></div><div className="model-map"><div className="model-node"><strong>Local models</strong><p>LM Studio, Ollama, LocalAI, and vLLM use conservative defaults: smaller context bundles, shorter plans, one tool at a time, and higher confidence thresholds unless configured otherwise.</p></div><Arrow/><div className="model-node center"><strong>EchoSpeak runtime</strong><p>Normalizes messages and tool calls, enforces Session and Project scope, owns approvals and retries, records ToolRuns, and adapts work to measured capability.</p></div><Arrow/><div className="model-node"><strong>Hosted models</strong><p>OpenAI and Gemini adapters can receive larger context budgets and deeper bounded workflows while remaining under the same authority, state, and verification rules.</p></div></div></section>

      <section id="runtime"><div className="section-head"><div className="kicker">State model</div><div><h2>A clear hierarchy, without forcing every chat into a folder.</h2><p className="lede">Projects and Sessions work together, but they are not duplicates. Projects are optional containers. Sessions are the independent continuity boundary.</p></div></div><div className="runtime">{runtimeRows.map(([name,role,state])=><div className="runtime-row" key={name}><strong>{name}</strong><span>{role}</span><span>{state}</span></div>)}</div></section>

      <section id="capabilities"><div className="section-head"><div className="kicker">What Echo does</div><div><h2>Useful across chat, research, and real local work.</h2></div></div><div className="cards">{capabilities.map(([title,copy],index)=><article className="card" key={title}><div className="card-mark">0{index+1}</div><h3>{title}</h3><p>{copy}</p></article>)}</div></section>

      <section id="authority"><div className="section-head"><div className="kicker">Authority + recovery</div><div><h2>Side effects stop at a boundary you can see.</h2><p className="lede">Echo’s persisted state distinguishes a proposal from an approved action and a model claim from a verified result.</p></div></div><div className="trust"><article className="trust-panel"><h3>Approval is a runtime record</h3><p>File writes, terminal commands, desktop automation, and other side effects are filtered through tool policy and Session permissions. Pending approvals survive UI refreshes and are checked against the exact action before execution.</p><div className="steps"><div>Inspect <span>read-only evidence</span></div><div>Propose <span>canonical arguments</span></div><div>Approve <span>confirm or cancel</span></div><div>Execute <span>record outcome</span></div></div></article><article className="trust-panel"><h3>Recovery uses durable facts</h3><p>Each Turn records terminal state, retries, failures, pending actions, verification, and the safest next action. Interrupted Project work can resume from the active objective and exact ToolRun lineage instead of reconstructing progress from chat prose.</p><div className="steps"><div>Interrupted <span>state remains durable</span></div><div>Resume <span>select unfinished Turn</span></div><div>Retry <span>same bounded target</span></div><div>Complete <span>only after outcome</span></div></div></article></div></section>

      <section><div className="section-head"><div className="kicker">Interface</div><div><h2>One product from the avatar to the execution trace.</h2><p className="lede">The monochrome workspace combines streaming chat, an activity visualizer, folder-backed Projects, independent Sessions, research evidence, code changes, tasks, approvals, model controls, memory tools, and Studio settings. The avatar reacts to actual runtime states and tool categories rather than decorative timers.</p></div></div></section>

      <section className="cta"><div><div className="eyebrow">Enter EchoSpeak</div><h2>Start a Quick Chat, or attach a folder and make it a Project.</h2></div><Link className="primary" to="/app">Access Platform <span>→</span></Link></section>
      <footer><div>EchoSpeak · local-first · backend-controlled · model-adaptive</div></footer>
    </div>
  </main>
);
