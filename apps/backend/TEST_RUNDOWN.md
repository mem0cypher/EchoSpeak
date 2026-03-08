# EchoSpeak Complete Test Rundown

## Quick Reference: All 45+ Tools

---

## 0. STABILITY REGRESSION TESTS (Latest Update)

| Behavior | Test Command | Expected Result |
|----------|--------------|-----------------|
| Capability fast path | "What can you do right now?" | Fast direct answer describing capabilities; should not wander into `get_system_time` |
| Remember fast path | "Remember that my favorite color is blue" | Fast acknowledgment like "Got it. I'll remember that." |
| Preference recall | "What is my favorite color?" | Deterministic direct recall: "Your favorite color is blue." |
| Profile recall shorthand | "What my name?" | Deterministic direct recall of stored name |
| Discord recap fail-fast | "Can you check my Discord general chat and see what people are saying" | Either a quick recap or a short timeout response; should not appear hung for ~30 seconds |
| Update context (Web UI) | "What changed recently?" | Grounded response citing real commits/changelog; routes to `project_update_context` tool |
| Update context (Discord) | "What's new with EchoSpeak?" via Discord server | Same grounded response with public-safe detail level |
| Update context (no self-mod) | "Any updates?" with `ALLOW_SELF_MODIFICATION=false` | Still works — uses safe `project_update_context`, not `self_git_status` |

---

## 0c. REFLECTION LOOP & TASK CHECKLIST TESTS (v7.0.0)

| Behavior | Test Command / Scenario | Expected Result |
|----------|------------------------|----------------|
| Trivial tool skip | ReflectionEngine with `get_system_time` task | `should_reflect()` returns False |
| Small plan skip | ReflectionEngine with 1-task plan | `should_reflect()` returns False |
| Substantial result skip | ReflectionEngine with 300+ char result | `should_reflect()` returns False |
| Empty result triggers reflection | ReflectionEngine with empty result, 2+ task plan | `should_reflect()` returns True |
| Failure signal triggers reflection | Result containing "error" or "not found" | `should_reflect()` returns True |
| Max cycle enforcement | 2 reflection cycles exhausted | `should_reflect()` returns False, no more retries |
| ACCEPT response parsing | LLM returns "ACCEPT: reason" | `reflect_on_step()` returns `accepted=True` |
| RETRY response parsing | LLM returns "RETRY: refined query" | `reflect_on_step()` returns `accepted=False, suggestion="refined query"` |
| Ambiguous response default | LLM returns unclear text | `reflect_on_step()` defaults to `accepted=True` |
| LLM failure fallback | LLM raises exception | `reflect_on_step()` defaults to `accepted=True` |
| Post-plan ACCOMPLISHED | LLM returns "ACCOMPLISHED: all done" | `reflect_on_plan()` returns `accepted=True` |
| Post-plan FAILED | LLM returns "FAILED: search returned nothing" | `reflect_on_plan()` returns `accepted=False` |
| Retry params for web_search | Rejected with suggestion | `get_retry_params()` returns `{q: suggestion}` |
| Retry params for browse_task | Rejected with URL suggestion | `get_retry_params()` returns `{url: suggestion_url}` |
| Unknown tool retry | Rejected with suggestion for unknown tool | `get_retry_params()` returns None |
| `{{prev_result}}` placeholder | Task with `{{prev_result}}` in message param | `_resolve_dependent_params()` injects previous task result |
| Empty message auto-inject | Task with empty message and depends_on | `_resolve_dependent_params()` injects dependency result |
| task_plan stream event | TaskPlanner with stream buffer | `push_task_plan()` emits event with task list |
| task_step stream event | Task status change | `push_task_step()` emits event with index and status |
| task_reflection stream event | Reflection evaluation | `push_task_reflection()` emits event with accepted/reason |
| Result preview truncation | Long result preview | Truncated to 200 chars |
| Multi-step plan (Web UI) | "Search for Python news and post it in Discord" | Task checklist appears with ○/●/✓ icons; action step pauses for confirm |
| Reflection retry (Web UI) | "Find the latest NBA scores" with bad first result | Agent retries with refined query; checklist shows ↻ retrying state |

**Automated regression tests** (run via `pytest tests/test_reflection.py`):
- `TestReflectionEngineHeuristics` — 6 tests for should_reflect gating
- `TestReflectionEngineStepReflection` — 6 tests for reflect_on_step LLM parsing
- `TestReflectionEnginePlanReflection` — 3 tests for reflect_on_plan
- `TestReflectionEngineRetryParams` — 6 tests for get_retry_params + reset
- `TestTaskPlannerReflectionIntegration` — 3 tests for stream event emission
- `TestTaskPlannerDependentResults` — 4 tests for result passing
- `TestStreamBufferTaskEvents` — 4 tests for push methods

---

## 0d. INLINE CODE DIFF & EFFICIENT EDITING TESTS (v7.1.0)

| Behavior | Test Command / Scenario | Expected Result |
|----------|------------------------|----------------|
| Inline diff display | "Edit soul.md and make it shorter" | Code panel shows single-file unified diff with green additions and red deletions; full file visible |
| Accept button | Click Accept in diff header when file_write pending | Sends "confirm", file saves, status changes to "Saved" |
| Decline button | Click Decline in diff header when file_write pending | Sends "cancel", edit discarded |
| SEARCH/REPLACE parsing | LLM outputs `<<<<<<< SEARCH / ======= / >>>>>>> REPLACE` blocks | `_parse_search_replace_blocks()` extracts blocks correctly |
| SEARCH/REPLACE application | Parsed blocks applied to original content | `_apply_search_replace()` returns patched content with correct applied/skipped counts |
| Fuzzy whitespace match | LLM outputs blocks with trailing whitespace differences | Fuzzy fallback matches and applies correctly |
| Full-file fallback | LLM outputs no SEARCH/REPLACE blocks | Falls back to full-file rewrite prompt without error |
| All-blocks-skipped fallback | All SEARCH/REPLACE blocks fail to match | Falls back to full-file rewrite prompt |
| Per-file session model | file_read then file_write for same file | Single CodeDiffSession with originalContent from read and currentContent from write |
| Status pills | Various file operations | Correct status: "Read" after file_read, "Draft changes" after preview, "Awaiting save" when pending, "Saved" after confirm |
| Context Ring | Messages in chat with provider context_window set | Ring shows estimated token usage percentage with correct color coding |
| Thread switch reset | Switch threads | codeSessions, activeCodeTab, latestCodeFilenameRef all reset |
| Workspace explorer load | Open Code panel "📂 Files" tab | File tree renders from `GET /workspace` with correct root, display_name, file listing |
| Workspace permission badges | `ALLOW_FILE_WRITE=true` + `ENABLE_SYSTEM_ACTIONS=true` | WRITE badge visible in workspace header |
| Workspace terminal badge | `ALLOW_TERMINAL_COMMANDS=true` + `ENABLE_SYSTEM_ACTIONS=true` | TERM badge visible in workspace header |
| Workspace cd | Click "cd" button, enter new path, click "Go" | `POST /workspace` changes FILE_TOOL_ROOT; file tree refreshes to new directory |
| Workspace cd invalid | Enter non-existent path | Error message displayed: "Path does not exist" |
| Workspace refresh | Click refresh button | File tree re-fetched from `GET /workspace` |
| Workspace browse | `GET /workspace/browse?path=agent` | Returns shallow listing of the `agent/` subdirectory |
| Workspace browse outside root | `GET /workspace/browse?path=../../etc` | Returns 403: "Path not allowed" |
| Files tab always visible | Code sessions active | "📂 Files" tab visible as first tab alongside file session tabs |

---

## 0b. UPDATE CONTEXT & TWITTER AUTONOMOUS TESTS (v6.7.0)

| Behavior | Test Command / Scenario | Expected Result |
|----------|------------------------|----------------|
| Update intent detection | "what changed?", "what's new?", "any updates?" | `UpdateContextService.is_update_intent()` returns True |
| Safe tool routing | Update query on any source | Routes to `project_update_context`, NOT `self_git_status` |
| Public source rendering | Twitter mention asking "what changed?" | High-level update context (no diffs) |
| Owner source rendering | Web UI asking "what changed?" | Full detail including diff summary |
| Discord server parity | Discord server update query | Uses `project_update_context` from server assistant allowlist |
| Autonomous tweet grounding | Autonomous tweet tick fires | Prompt includes `UpdateContextService` context with recent commits |
| Changelog tweet | New git commits detected | `_maybe_changelog_tweet()` generates tweet via agentic pipeline |
| Source role resolution | Twitter mention source | Resolves to `PUBLIC` role |
| Source role resolution | Twitch chat source | Resolves to `PUBLIC` role |
| Source role resolution | `twitter_autonomous` source | Resolves to `OWNER` role |

**Automated regression tests** (run via `pytest tests/test_echospeak.py::TestUpdateContextParity`):
- `test_update_query_uses_safe_project_update_context_without_self_modification`
- `test_discord_server_update_query_uses_safe_update_tool`
- `test_resolve_user_role_marks_public_social_sources_as_public`
- `test_update_context_plugin_injects_update_context_for_update_queries`
- `test_autonomous_tweet_prompt_uses_shared_update_context`

---

## 1. WEB & SEARCH TOOLS

| Tool | Description | Test Command |
|------|-------------|--------------|
| `web_search` | Search the web via Tavily | "Search for latest Python news" |
| `browse_task` | Full Playwright browser automation | "Go to github.com and find trending repos" |
| `youtube_transcript` | Get transcript from YouTube video | "Get transcript from https://youtube.com/watch?v=abc123" |

**Test on Discord:** Same commands work via DM or mention

---

## 2. MEMORY & KNOWLEDGE TOOLS

| Tool | Description | Test Command |
|------|-------------|--------------|
| Memory (auto) | Stores facts about user | "Remember that my favorite color is blue" |
| Deterministic preference recall | Retrieves common profile/preference facts | "What is my favorite color?" |
| Deterministic profile recall | Retrieves stored profile facts | "What my name?" |
| Memory recall | Retrieves stored facts semantically | "What do you know about me?" |
| Document RAG | Upload/search documents | Upload a PDF via GUI, then "What does the document say about X?" |

---

## 3. DISCORD TOOLS (Bot-based)

| Tool | Description | Test Command |
|------|-------------|--------------|
| `discord_read_channel` | Read messages from server channel | "What are people saying in #general?" |
| `discord_send_channel` | Send message to server channel | "Say 'hello everyone' in #general" |

**Requires:** `ALLOW_DISCORD_BOT=true` + `DISCORD_BOT_TOKEN`

**Expected behavior note:** channel recap reads should now return quickly even when Discord history fetch is unhealthy. A short timeout response is acceptable; a long apparent hang is not.

---

## 4. DISCORD TOOLS (Playwright Web)

| Tool | Description | Test Command |
|------|-------------|--------------|
| `discord_web_read_recent` | Read DMs via browser automation | "Check my Discord DMs from John" |
| `discord_web_send` | Send DM via browser automation | "Send 'hey' to John on Discord" |
| `discord_contacts_add` | Add Discord contact mapping | "Add Discord contact John with URL discord.com/channels/@me/123" |
| `discord_contacts_discover` | Discover recent Discord contacts | "Find my recent Discord contacts" |

**Requires:** `ENABLE_SYSTEM_ACTIONS=true` + `ALLOW_PLAYWRIGHT=true` + logged-in Discord Web session

---

## 5. FILE TOOLS

| Tool | Description | Test Command |
|------|-------------|--------------|
| `file_list` | List files in directory | "List files in the current folder" |
| `file_read` | Read text file content | "Read the file README.md" |
| `file_write` | Write/create text file | "Create a file called test.txt with content 'hello world'" |
| `file_move` | Move/rename file | "Rename test.txt to renamed.txt" |
| `file_copy` | Copy file | "Copy renamed.txt to copy.txt" |
| `file_delete` | Delete file | "Delete copy.txt" |
| `file_mkdir` | Create folder | "Create a folder called test_folder" |

**Requires:** `ENABLE_SYSTEM_ACTIONS=true` + `ALLOW_FILE_WRITE=true` for write operations

---

## 6. ARTIFACT & NOTEPAD TOOLS

| Tool | Description | Test Command |
|------|-------------|--------------|
| `artifact_write` | Write to safe artifacts folder | "Save this note to artifacts: 'Meeting notes from today'" |
| `notepad_write` | Open Notepad, type, save to artifacts | "Open Notepad and type 'Hello from EchoSpeak'" |

**Requires:** `ENABLE_SYSTEM_ACTIONS=true`

---

## 7. TERMINAL & SYSTEM TOOLS

| Tool | Description | Test Command |
|------|-------------|--------------|
| `terminal_run` | Run allowlisted terminal command | "Run git status" |
| `system_info` | Get hardware info (OS, CPU, GPU, RAM) | "What's my system info?" |
| `get_system_time` | Get current date/time | "What time is it?" |
| `calculate` | Evaluate math expression | "Calculate 25 * 4 + 100" |

**Requires:** `ENABLE_SYSTEM_ACTIONS=true` + `ALLOW_TERMINAL_COMMANDS=true`

---

## 8. BROWSER AUTOMATION TOOLS

| Tool | Description | Test Command |
|------|-------------|--------------|
| `open_chrome` | Open URL in Chrome | "Open chrome and go to google.com" |

**Requires:** `ENABLE_SYSTEM_ACTIONS=true` + `ALLOW_PLAYWRIGHT=true`

---

## 9. DESKTOP AUTOMATION TOOLS (Windows)

| Tool | Description | Test Command |
|------|-------------|--------------|
| `desktop_list_windows` | List open windows | "What windows are open?" |
| `desktop_find_control` | Find UI controls in window | "Find buttons in the Calculator window" |
| `desktop_click` | Click UI control | "Click the '5' button in Calculator" |
| `desktop_type_text` | Type into UI control | "Type 'hello' in Notepad" |
| `desktop_activate_window` | Focus a window | "Activate the Notepad window" |
| `desktop_send_hotkey` | Send keyboard shortcut | "Send ctrl+s to save" |

**Requires:** `ENABLE_SYSTEM_ACTIONS=true` + `ALLOW_DESKTOP_AUTOMATION=true` + Windows OS

---

## 10. APPLICATION TOOLS

| Tool | Description | Test Command |
|------|-------------|--------------|
| `open_application` | Launch allowlisted app | "Open Calculator" |

**Requires:** `ENABLE_SYSTEM_ACTIONS=true` + `ALLOW_OPEN_APPLICATION=true`

---

## 11. VISION TOOLS

| Tool | Description | Test Command |
|------|-------------|--------------|
| `take_screenshot` | Capture screen | "Take a screenshot" |
| `analyze_screen` | Describe what's on screen | "What's on my screen right now?" |
| `vision_qa` | Answer questions about screen | "Is there a Chrome window open?" |

**Requires:** Vision dependencies installed (cv2, PIL)

---

## 12. SELF-MODIFICATION TOOLS

| Tool | Description | Test Command |
|------|-------------|--------------|
| `self_read` | Read own source files | "Read your own tools.py file" |
| `self_grep` | Search own codebase | "Search your code for 'discord'" |
| `self_list` | List own project files | "List your own source directory" |
| `self_edit` | Modify own source code | "Add a comment to tools.py line 100" |
| `self_git_status` | Check git status of self | "What files have changed in your codebase?" |
| `self_rollback` | Revert self changes | "Rollback the last change to tools.py" |

**Requires:** `ENABLE_SYSTEM_ACTIONS=true` + `ALLOW_FILE_WRITE=true`

---

## 13. EMAIL TOOLS (v5.4.0)

| Tool | Description | Test Command |
|------|-------------|--------------|
| `email_read_inbox` | Read recent inbox emails | "Check my email" |
| `email_search` | Search emails by keyword | "Search emails from John" |
| `email_get_thread` | Fetch full thread by ID | "Show me the full email thread for message <ID>" |
| `email_send` | Send a new email (confirm) | "Send an email to john@example.com about the meeting" |
| `email_reply` | Reply to an email (confirm) | "Reply to that email saying thanks" |

**Requires:** `ALLOW_EMAIL=true` + `EMAIL_USERNAME` + `EMAIL_PASSWORD` configured

---

## 14. TELEGRAM BOT (v5.4.0)

| Feature | Description | Test Command |
|---------|-------------|--------------|
| `/start` | Welcome message | Send `/start` to bot on Telegram |
| `/status` | Agent status | Send `/status` to bot |
| `/help` | Show capabilities | Send `/help` to bot |
| Messages | Full agent pipeline | Send any text to bot |

**Requires:** `ALLOW_TELEGRAM_BOT=true` + `TELEGRAM_BOT_TOKEN` + `pip install python-telegram-bot`

---

## 15. HEARTBEAT SCHEDULER (v5.4.0)

| Feature | Description | Test Command |
|---------|-------------|--------------|
| Status | Check heartbeat | `curl http://localhost:8000/heartbeat` |
| Start | Start scheduler | `curl -X POST http://localhost:8000/heartbeat/start` |
| Stop | Stop scheduler | `curl -X POST http://localhost:8000/heartbeat/stop` |
| History | View past results | `curl http://localhost:8000/heartbeat/history?limit=5` |
| Config | Update interval | `curl -X POST http://localhost:8000/heartbeat -d '{"heartbeat_interval":5}'` |

**Requires:** `HEARTBEAT_ENABLED=true` in `.env`

---

## 16. MULTI-TASK & PLANNING

| Feature | Description | Test Command |
|---------|-------------|--------------|
| Multi-task planner | Decomposes complex queries into parallel tasks | "Search for Python news, check the weather, and tell me the time - all at once" |
| Action plan | Sequential step execution | "Create a plan to: read README.md, summarize it, and save the summary to artifacts" |
| Web task reflection | Self-corrects web automation failures | "Go to a shopping site and find the best rated laptop under $1000" |

**Requires:** `MULTI_TASK_PLANNER_ENABLED=true` (default)

---

## 17. SOUL / PERSONALITY

| Feature | Description | Test Command |
|---------|-------------|--------------|
| SOUL.md | Personality/identity system | "Who are you?" or "What's your personality?" |
| Praise response | Natural acknowledgment | "You're awesome!" |
| Opinion grounding | Uses memory/search for opinions | "What do you think about AI?" |

---

## TEST MATRIX

### Test on GUI (Web UI at localhost:8000)

1. Start backend: `cd apps/backend && uv run python -m uvicorn api.server:app --host 0.0.0.0 --port 8000`
2. Open browser to `http://localhost:8000`
3. Test each category above

### Test on Discord

1. Ensure Discord bot is running (`ALLOW_DISCORD_BOT=true`)
2. DM the bot or mention it in a server channel
3. Test commands that make sense in Discord context:
   - Memory operations
   - Web search
   - Discord channel read/send
   - System info
   - Calculations

---

## KNOWN LIMITATIONS

| Tool | Limitation |
|------|------------|
| Desktop automation | Windows only |
| Vision tools | Requires cv2, PIL installation |
| Discord web tools | Requires logged-in Discord Web session (headless=false first run) |
| Terminal commands | Only allowlisted commands (git, ls, cat, python, etc.) |
| File operations | Restricted to `FILE_TOOL_ROOT` directory |

---

## CONFIG FLAGS QUICK REFERENCE

| Flag | Enables |
|------|---------|
| `ENABLE_SYSTEM_ACTIONS` | Master switch for system-level tools |
| `ALLOW_FILE_WRITE` | file_write, file_move, file_copy, file_delete, file_mkdir |
| `ALLOW_TERMINAL_COMMANDS` | terminal_run |
| `ALLOW_PLAYWRIGHT` | browse_task, discord_web_* |
| `ALLOW_DESKTOP_AUTOMATION` | desktop_* tools |
| `ALLOW_DISCORD_BOT` | discord_read_channel, discord_send_channel |
| `ALLOW_OPEN_APPLICATION` | open_application |
| `ALLOW_OPEN_CHROME` | open_chrome |
| `ALLOW_EMAIL` | email_read_inbox, email_search, email_get_thread, email_send, email_reply |
| `ALLOW_TELEGRAM_BOT` | Telegram bot integration |
| `HEARTBEAT_ENABLED` | Heartbeat scheduler |

---

## RECOMMENDED TEST SEQUENCE

### Phase 1: Basic (No special flags needed)
1. ✅ "What time is it?"
2. ✅ "Calculate 100 * 50"
3. ✅ "Search for latest tech news"
4. ✅ "Remember my name is Memo"
5. ✅ "What do you know about me?"
6. ✅ "Read the file README.md"
7. ✅ "List files in the current folder"

### Phase 2: System Actions (ENABLE_SYSTEM_ACTIONS=true)
8. ⚠️ "Save 'test note' to artifacts"
9. ⚠️ "Run git status"
10. ⚠️ "What's my system info?"

### Phase 3: Discord Bot (ALLOW_DISCORD_BOT=true)
11. 🔷 "What are people saying in #general?"
12. 🔷 "Say 'test from EchoSpeak' in #general"

### Phase 4: Multi-Task
13. 🔄 "Search for weather, calculate 5+5, and tell me the time all at once"

### Phase 5: Personality
14. 💬 "Who are you?"
15. 💬 "What's your opinion on AI?"
16. 💬 "You're amazing!"

### Phase 6: Email (ALLOW_EMAIL=true + EMAIL_* configured)
17. 📧 "Check my email"
18. 📧 "Search emails from John"
19. ⚠️ "Send an email to test@example.com about hello world"

### Phase 7: Telegram (ALLOW_TELEGRAM_BOT=true + token)
20. 📩 Send `/start` to bot on Telegram
21. 📩 Send a question to bot and verify response

### Phase 8: Heartbeat (HEARTBEAT_ENABLED=true)
22. 💓 `curl http://localhost:8000/heartbeat`
23. 💓 `curl -X POST http://localhost:8000/heartbeat/start`
24. 💓 `curl http://localhost:8000/heartbeat/history?limit=5`

---

### Phase 9: Update Context (v6.7.0)
25. 🔍 "What changed recently?" (Web UI) → grounded update response
26. 🔍 "What's new?" (Discord server) → public-safe update response
27. 🔍 "Any updates?" with `ALLOW_SELF_MODIFICATION=false` → still works via safe tool

### Phase 10: Twitter/Twitch (v6.7.0)
28. 🐦 Autonomous tweet tick → grounded tweet with real commit context
29. 🐦 New commits detected → changelog tweet generated via agentic pipeline
30. 🎮 Twitch chat message → routed through `process_query(source="twitch")` with PUBLIC role

---

*Generated for EchoSpeak v6.7.0 testing*
