# EchoSpeak Complete Test Rundown

**Updated:** 2026-07-08 (v7.4.6)  
**Automated suite (this machine):** backend **248 passed**, web **12 passed**  
**Eval board:** E1–E12 in `tests/test_eval_harness_e1_e10.py`  
**Default model path:** Gemma 4 via LM Studio (native tool-calling / Stage 4)

This is the **master checklist** for verifying EchoSpeak end-to-end — not only recent fixes. Use it in order: automated → smoke → deep capabilities → integrations.

---

## A. Automated regression (always run first)

```powershell
# Backend (from apps/backend)
python -m pytest tests/ -q

# Eval board only
python -m pytest tests/test_eval_harness_e1_e10.py -v

# Reliability / search / memory architecture
python -m pytest tests/test_reliability_architecture.py tests/test_phase1_integrity.py -q

# Web unit tests (from apps/web)
npx vitest run
```

| Suite | What it covers |
|-------|----------------|
| `test_eval_harness_e1_e10.py` | E1–E12 product board (file write, live score, schedule, odds, subject, terminal, provider, coding, weak evidence, context budget, long memory, social preamble, **search query normalize**) |
| `test_reliability_architecture.py` | SearchGrounder, context budget, session memory, telemetry, LC tool wrap, partial tools |
| `test_reflection.py` | Reflection engine + task planner |
| `test_phase1_integrity.py` | Routes, readiness, memory doctor, terminal denylist, MCP trust honesty, settings |
| `test_phase2_research.py` | Research run parse |
| `test_router.py` | Intent routing (chat vs search vs Discord vs time vs vision) |
| `test_echospeak.py` | Config, memory, tools, Discord hardening, coding workspace, TTS, voice/vision stubs, API smoke |
| Web vitest | Research panel parse + agent activity |

**Pass bar:** full backend green before manual live testing.

---

## B. Preflight (before any live chat)

| Check | How | Pass if |
|-------|-----|---------|
| Health | `GET http://localhost:8000/health` | 200 |
| Provider | Settings → model ready, or `GET /provider` / doctor | LM Studio / API key ready |
| Doctor | `GET http://localhost:8000/doctor` | No critical failures |
| Memory doctor | `GET http://localhost:8000/memory/doctor` | `auto_store_conversations: false`; no “Conversation memories dominate” on a clean profile |
| Capabilities | `GET http://localhost:8000/capabilities` | Tools list matches flags |
| Coding readiness | `GET http://localhost:8000/coding/readiness` | Matches your file/terminal flags |
| Settings | UI Settings saved | Provider, Tavily key if testing search, workspace |

**Flags you’ll need for full tool matrix:**

| Flag | Unlocks |
|------|---------|
| *(none)* | Chat, time, calc, memory, web_search (if Tavily/key), SOUL |
| `ENABLE_SYSTEM_ACTIONS=true` | Master for system tools |
| `ALLOW_FILE_WRITE=true` | file_write / move / copy / delete / mkdir |
| `ALLOW_TERMINAL_COMMANDS=true` | terminal_run (denylist, not allowlist) |
| `ALLOW_PLAYWRIGHT=true` | browse_task, discord_web_* |
| `ALLOW_DESKTOP_AUTOMATION=true` | desktop_* (Windows) |
| `ALLOW_OPEN_APPLICATION=true` | open_application |
| `ALLOW_OPEN_CHROME=true` | open_chrome |
| `ALLOW_DISCORD_BOT=true` + token | discord_read/send_channel |
| `ALLOW_EMAIL=true` + IMAP/SMTP | email_* |
| `ALLOW_TELEGRAM_BOT=true` + token | Telegram |
| `HEARTBEAT_ENABLED=true` | Heartbeat scheduler |
| `MEMORY_AUTO_STORE_CONVERSATIONS=false` | **Default** — do not enable unless debugging |
| `SEARCH_GROUNDING_ENABLED=true` | **Default** — grounded search |

---

## C. Live smoke (~15–20 min) — core product must work

Run in **Web UI** with streaming visible (partial_reply, Search done, final).

### C1. Chat & multi-beat

| # | You say | Expect |
|---|---------|--------|
| 1 | `hi` / `how are you?` | Short social reply; **no** web_search; no tool spam |
| 2 | `how're you feeling? and what's the weather in Calgary?` | **Beat 1:** social + on-it (not bare “Checking that now.”) → **Beat 2:** real temps (°C/°F or high/low numbers) |
| 3 | `what about in Vancouver?` | Weather for **Vancouver**, subject keeps weather topic |
| 4 | `how're you feeling? and i wonder when that new trailer comes out for trailer 3 for gta 6 hey?` | Social-first preamble; search query ≈ **`GTA 6 Trailer 3 release date`** (not full chat line); factual answer |
| 5 | `what do you think about it?` | Still about **GTA trailer**, not weather-only |

### C2. Time / calc / silent tools

| # | You say | Expect |
|---|---------|--------|
| 6 | `what time is it?` | Correct local time; **no** spoken “searching…” preamble for `get_system_time` |
| 7 | `calculate 25 * 4 + 100` | `200`; silent calc |

### C3. Search quality

| # | You say | Expect |
|---|---------|--------|
| 8 | `Search for latest Python news` | Compact query;  sources; no raw tool syntax in chat |
| 9 | `who won Canada vs Morocco today?` (or live sports) | Live-score language; not date-only schedule page as “score” |
| 10 | `do a deeper search` (after a real topic) | Keeps **current subject** |

### C4. Memory (durable vs chatter)

| # | You say | Expect |
|---|---------|--------|
| 11 | `Remember that my favorite color is blue` | Ack; **Memory saved** once for durable fact |
| 12 | `What is my favorite color?` | “blue” without re-search |
| 13 | `My name is Ty` then `What's my name?` | Profile recall |
| 14 | Random chatter 5 turns (`lol`, `ok`, `cool`) | **No** “Memory saved” every turn (`auto_store` off) |
| 15 | `GET /memory/doctor` after long chat | Conversation type not dominating |

### C5. Coding / confirmations

| # | You say | Expect |
|---|---------|--------|
| 16 | Coding workspace: `create hello.html with Hi` | Pending **confirm**; no raw `file_write(...)` as the only reply |
| 17 | Accept in UI / type `confirm` | File written; Code panel / workspace updates |
| 18 | `list files` then `read hello.html` | Correct content |

### C6. Terminal

| # | You say | Expect |
|---|---------|--------|
| 19 | `run git status` (allow terminal) | Output; ExitCode 0 reflected honestly |
| 20 | Something denylisted (`rm -rf /`) | Blocked; clear denial |

### C7. Update context / personality

| # | You say | Expect |
|---|---------|--------|
| 21 | `What changed recently?` | Uses `project_update_context`; real commits/changelog |
| 22 | `Who are you?` | SOUL personality, not tool dump |
| 23 | `What can you do right now?` | Capability answer; not hung on `get_system_time` |

---

## D. Full capability matrix (tools + features)

**47 registered core tools** (skills may add more). Confirm each only if the flag is on.

### D1. Web & research

| Tool / feature | Prompt | Pass |
|----------------|--------|------|
| `web_search` | “Search for …” | Grounded results; compact query |
| Multi-intent normalize | Social + GTA trailer (C4) | No raw chat as Tavily query |
| `browse_task` | “Go to example.com and summarize the page” | Playwright run; summary |
| `youtube_transcript` | “Transcript for \<youtube url\>” | Transcript or clear failure |
| Research panel | Any search | Sources parse in UI |
| Weak evidence | Obscure live fact with bad pages | “insufficient” honesty, not invented score |

### D2. Memory & documents

| Feature | Prompt / action | Pass |
|---------|-----------------|------|
| Remember / profile | C4 above | Durable only |
| Semantic recall | “What do you know about me?” | Uses typed/profile/session |
| Session subject | 15+ turns with switch-back | Subject + session summary coherent |
| Documents | Upload PDF → “What does the doc say about X?” | RAG hit |
| Memory compact | `POST /memory/compact` | Completes; count drops or no-op cleanly |
| Memory doctor | UI or `GET /memory/doctor` | Warnings accurate |

### D3. Files & workspace

| Tool | Prompt | Pass |
|------|--------|------|
| `file_list` | “List files in the current folder” | Tree listing |
| `file_read` | “Read README.md” | Content (if in `FILE_TOOL_ROOT`) |
| `file_write` | “Create test_echo.txt with hello” | Confirm → write |
| `file_move` / `copy` / `delete` / `mkdir` | Obvious ops | Confirm when required |
| Workspace explorer | Code panel → Files | `GET /workspace` tree |
| Workspace cd | Change root in UI | New tree; outside root 403 |

### D4. Terminal & system

| Tool | Prompt | Pass |
|------|--------|------|
| `terminal_run` | “Run git status” | Honest exit codes |
| `system_info` | “What's my system info?” | OS/CPU/RAM |
| `get_system_time` | “What time is it?” | Silent tool |
| `calculate` | “Calculate …” | Correct math |
| `artifact_write` | “Save this note to artifacts: …” | File under artifacts |
| `todo_manage` | “Add a todo: buy milk” | Todo appears / listed |

### D5. Desktop / apps (Windows)

| Tool | Prompt | Pass |
|------|--------|------|
| `desktop_list_windows` | “What windows are open?” | Titles |
| `open_application` | “Open Calculator” | App launches |
| `open_chrome` | “Open chrome to google.com” | Browser |
| `notepad_write` | “Open Notepad and type hello” | Notepad + text |
| `desktop_click` / `type` / `hotkey` | Calculator or Notepad task | UI acts |
| `take_screenshot` / `analyze_screen` / `vision_qa` | “What's on my screen?” | Description |

### D6. Discord

| Path | Prompt | Pass |
|------|--------|------|
| Bot read | “What are people saying in #general?” | Recap or fast timeout (not 30s hang) |
| Bot send | “Say ‘echo test’ in #general” | Confirm → post |
| Web DM read/send | With Playwright + logged-in Discord | DM ops |
| Public user isolation | Non-owner Discord user | No owner memory / dangerous tools |

### D7. Email

| Tool | Prompt | Pass |
|------|--------|------|
| `email_read_inbox` | “Check my email” | Headers |
| `email_search` | “Search emails from …” | Results |
| `email_send` / `reply` | Send test | **Confirm** before send |

### D8. Self-modification (danger — use carefully)

| Tool | Prompt | Pass |
|------|--------|------|
| `self_list` / `self_grep` / `self_read` | “Search your code for web_search” | Read-only ok |
| `self_edit` / `self_rollback` | Only in a throwaway clone | Confirm + reversible |
| Prefer | “What changed recently?” | Safe `project_update_context` |

### D9. Background / social platforms

| Surface | Test | Pass |
|---------|------|------|
| Heartbeat | `GET/POST /heartbeat`, start/stop, history | Tick results |
| Proactive | `GET /proactive`, history | Tasks listed |
| Routines | Create/run routine in UI | Fires |
| Telegram | `/start`, question | Reply |
| Twitter | Mentions / autonomous approve | Public vs owner roles |
| Twitch | Chat message | Public role |
| A2A | `GET /.well-known/agent.json`, `POST /a2a` | Schema / task |
| Orchestrate | `POST /orchestrate` | Plan id |

### D10. UI / avatar / stream

| Feature | Test | Pass |
|---------|------|------|
| Streaming | Any tool turn | partial_reply → final; **no double TTS** of same text |
| Avatar | Idle / thinking / tool / memory_saved | Animations match status |
| Context meter | Long chat | Ring/square updates; not stuck at 0% forever |
| Task checklist | Multi-step plan | ○/●/✓ / retry states |
| Inline code diff | file_write pending | Accept/Decline works |
| Provider offline | Stop LM Studio mid-chat | Clear readiness error, not empty hang |

---

## E. Eval board map (deterministic CI)

| ID | Scenario |
|----|----------|
| E1 | Printed `file_write` → pending confirm |
| E2 | Live score rejects date-only, accepts real score |
| E3 | Buried schedule needs full-page fetch |
| E4 | Odds capability gap (not canned) |
| E5 | Deeper search keeps subject |
| E6 | Terminal nonzero fails reflection honestly |
| E7 | LM Studio down → readiness false |
| E8 | Coding file_write pending |
| E9 | Weak evidence insufficient structure |
| E10 | Context flood protects subject |
| E11 | Long conversation memory + subject + tier agreement |
| E12a | Search query ≠ raw multi-intent chat (GTA Trailer 3) |
| E12b | Social+task preamble answers “feeling” |

---

## F. Recommended full-day sequence

### Phase 0 — Automate (5–10 min)
1. `pytest tests/ -q` → 248+ green  
2. `npx vitest run` → 12 green  

### Phase 1 — Smoke (C1–C7) (~20 min)
All rows in section C with backend + UI running.

### Phase 2 — System actions (~20 min)
Enable `ENABLE_SYSTEM_ACTIONS` + file + terminal; D3–D4.

### Phase 3 — Research deep (~15 min)
Weather multi-city, sports, multi-intent GTA, “deeper search”, research panel.

### Phase 4 — Coding loop (~20 min)
Workspace coding → plan → write → confirm → terminal verify → summarize.

### Phase 5 — Memory stress (~15 min)
20-turn conversation (E11 style live): topic switch, remember fact, late “what do you think about it?”, memory doctor.

### Phase 6 — Integrations (as configured)
Discord → Telegram → Email → Heartbeat → Twitter/Twitch.

### Phase 7 — Desktop/vision (Windows optional)
Calculator / Notepad / screenshot QA.

### Phase 8 — Negative / safety
- Denylist command  
- Public Discord can’t write owner memory  
- API auth if `API_AUTH_ENABLED=true`  
- Cancel pending file_write  

---

## G. Audit status (2026-07-08)

### Green (verified this session)

| Area | Status |
|------|--------|
| Full backend pytest | **248 passed** |
| Web vitest | **12 passed** |
| Raw conversation auto-store | **Off by default**; gated in `_record_turn` |
| Search query normalize | **Fixed** — multi-intent chat not sent raw to Tavily |
| Social-first preamble fallback | **Fixed** when LLM fails |
| Subject continuity | Hollow follow-ups / remember / pronoun questions |
| Search grounding single path | Stage 3 / planner / LC tools share `_grounded_web_search` |
| Memory doctor conversation dominance | Metric + warning present |
| Provider readiness | LM Studio unreachable tested |
| Terminal denylist | Integrity tests present |
| MCP Trust Center honesty | Configured ≠ available; only `loaded_tool_count > 0` → available (v7.6.0) |
| MCP stdio client | Real initialize/list/call + `mcp__` registry | Set `MCP_SERVERS` JSON; run `tests/test_mcp_v760.py` |

### Known gaps / risks (not fully “product done”)

| Item | Reality | Action when testing |
|------|---------|---------------------|
| **MCP runtime** | **v7.6.0 real stdio client** — list/call work; HTTP/SSE not yet | Configure `MCP_SERVERS`; check `/capabilities` trust summary; mock fixture in `tests/fixtures/mock_mcp_server.py` |
| **MCP chat E2E** | Tools register into agent; full chat polish is v7.6.1 | Manually: enable a trusted mock/time server, ask the agent to use it |
| **API bind host** | Default still `0.0.0.0` | For local-only, prefer `API_HOST=127.0.0.1`; enable `API_AUTH_*` if LAN-exposed |
| **Skill tools** | Spotify/Notion/GitHub/etc. are skills — may need separate enablement | Check `/capabilities` and skill folders |
| **Live search quality** | Depends on Tavily + grounding | Always verify **query string** in logs/tool input, not only the spoken answer |
| **core.py size** | Very large; high regression risk | Prefer eval board + this rundown over ad-hoc only |
| **Self-modify** | Dangerous on real repo | Test only in a throwaway worktree |
| **Discord web / Playwright** | Needs logged-in browser profile | First run often `headless=false` |
| **Desktop automation** | Windows-only | Skip on Linux/Mac |

### Recent fixes you should re-validate live

1. **Memory saved every turn** → only durable saves  
2. **GTA multi-intent search** → `GTA 6 Trailer 3 release date`  
3. **“how’re you feeling? + task”** → social then task, not only “Checking that now.”  
4. **Location follow-up** weather Calgary → Vancouver  

---

## H. Log signals to watch

| Signal | Good | Bad |
|--------|------|-----|
| Tool start `web_search` input | Compact factual query | Full “how’re you feeling? and i wonder…” |
| `memory_saved` stream event | Rare (facts only) | Every turn |
| `partial_reply` then `final` | One social preamble, then facts | Double greeting / double TTS |
| Search grounding log | `accepted` + chosen_query | Always insufficient on easy facts |
| Provider readiness | Blocks when LM Studio down | Silent hang / empty reply |

Backend log examples:

```
Search grounding accepted query='GTA 6 Trailer 3 release date official' ...
```

---

## I. Config quick reference

| Flag | Role |
|------|------|
| `ENABLE_SYSTEM_ACTIONS` | Master system tools |
| `ALLOW_FILE_WRITE` | Mutating file tools |
| `ALLOW_TERMINAL_COMMANDS` | terminal_run + denylist |
| `TERMINAL_COMMAND_DENYLIST` | Destructive tokens |
| `ALLOW_PLAYWRIGHT` | browse + discord web |
| `ALLOW_DESKTOP_AUTOMATION` | desktop_* |
| `ALLOW_DISCORD_BOT` | Bot channel tools |
| `ALLOW_EMAIL` | Email tools |
| `ALLOW_TELEGRAM_BOT` | Telegram |
| `HEARTBEAT_ENABLED` | Heartbeat |
| `MEMORY_AUTO_STORE_CONVERSATIONS` | Raw FAISS turns (**keep false**) |
| `SEARCH_GROUNDING_ENABLED` | Evidence grounding (**keep true**) |
| `SESSION_MEMORY_ENABLED` | Per-thread session summary |
| `API_AUTH_ENABLED` / `API_AUTH_KEY` | Gate non-local API use |
| `API_HOST` | Prefer `127.0.0.1` for personal use |

---

## J. Tool inventory (core, 47)

```
analyze_screen, artifact_write, browse_task, calculate,
desktop_activate_window, desktop_click, desktop_find_control,
desktop_list_windows, desktop_send_hotkey, desktop_type_text,
discord_contacts_add, discord_contacts_discover,
discord_read_channel, discord_send_channel,
discord_web_read_recent, discord_web_send,
email_get_thread, email_read_inbox, email_reply, email_search, email_send,
file_copy, file_delete, file_list, file_mkdir, file_move, file_read, file_write,
get_system_time, notepad_write, open_application, open_chrome,
project_status, project_update_context,
self_edit, self_git_status, self_grep, self_list, self_read, self_rollback,
system_info, take_screenshot, terminal_run, todo_manage, vision_qa,
web_search, youtube_transcript
```

Plus skills under `apps/backend/skills/` (daily_briefing, system_monitor, calendar, spotify, …) when loaded.

---

## K. Pass / fail summary sheet (print this)

| Area | Pass? | Notes |
|------|-------|-------|
| pytest full | ☐ | |
| vitest | ☐ | |
| Social + weather multi-beat | ☐ | |
| GTA multi-intent **query** quality | ☐ | |
| Weather location swap | ☐ | |
| Memory not every turn | ☐ | |
| Remember + recall | ☐ | |
| File write confirm | ☐ | |
| Terminal denylist | ☐ | |
| Provider offline message | ☐ | |
| No double TTS | ☐ | |
| Memory doctor healthy | ☐ | |
| Discord recap (if enabled) | ☐ | |
| Coding workspace loop | ☐ | |

When all **Phase 0–1** and your enabled **Phase 2–5** boxes pass, EchoSpeak is doing its full core job on this machine.
