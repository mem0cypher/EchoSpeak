Discord messaging and reading.

## IMPORTANT: You CAN send Discord messages

You have the ability to send Discord messages.

There are two different ways:

- Server channels (bot account): use `discord_send_channel` / `discord_read_channel`
- Personal Discord (Playwright web session): use `discord_web_send` / `discord_web_read_recent`

## Intent-based Routing (Automatic)

The agent automatically routes Discord queries based on intent:

- **Server channels** (`#general`, `#updates`, etc.) → bot tools (`discord_read_channel`, `discord_send_channel`)
- **DMs/personal messaging** → Playwright web tools (`discord_web_send`, `discord_web_read_recent`)

This routing happens in `_allowed_lc_tool_names` and `_should_use_tool` in `core.py`.

It also triggers on `#channel` patterns and common channel names (like `general`, `updates`) even if the user does not include the word "discord".

## Context Extraction (Fixed 2026-03-01)

The Discord bot injects context blocks into queries:
- `Recent conversation context:` - Previous messages in the conversation
- `User request:` - The actual user's message

The agent uses `_extract_user_request_text()` to parse out the actual user request from these wrapped inputs. This ensures tool routing decisions are based on what the user actually asked, not on injected context.

**Key fixes applied:**
- Discord bot now ALWAYS adds `User request:` marker (even without context)
- `_should_use_tool` fallback extracts user request before tool selection
- Tool routing skips entirely if extraction returns empty (context-only payload)
- All tool handlers use extracted input instead of raw input

This prevents false positives like "Missing Discord target" when the context block mentions Discord but the user's actual message is conversational.

## Sending messages

When someone wants to post/send a message to a Discord server channel like `#updates` or `#general`, send it using `discord_send_channel` (this posts as the bot account).

When someone wants to DM someone *from the user's personal Discord account*, use `discord_web_send`.

If the recipient matches a saved Discord contacts key (see `discord_contacts` skill), DM routing can work even if the user omits the word "discord".

You CAN and DO send Discord messages. This is a core capability.

## Reading messages

When they ask to check Discord or read messages:

- If they mention a server channel like `#general` (or say `read general chat` / `search general chat ...`), call `discord_read_channel`.
- If they mean their personal DMs, call `discord_web_read_recent`.

Channel name matching is fuzzy for server channels (for example, `#general` can match emoji-prefixed channels like `#💬-general`).

Bad: "Here are the recent messages: chase: hello, mem0: hi"
Good: "chase said hello, then mem0 replied hi"

Don't dump raw output. Summarize conversationally.

## Natural language queries

You can ask about Discord naturally:
- "what did oxi say on discord"
- "what was the last thing chase sent me"
- "check my dms"
- "read messages from oxi"

The system extracts the contact name and reads their messages.

## Tools

Web tools (Playwright - recommended):
- `discord_web_send` - Send messages via Discord Web (YOU HAVE THIS)
- `discord_web_read_recent` - Read recent messages from DMs/channels

Bot tools (server channels):
- `discord_read_channel` - Read recent messages from a server channel via the bot
- `discord_send_channel` - Post a message to a server channel via the bot

Desktop automation (Windows only):
- `desktop_activate_window`, `desktop_click`, `desktop_type_text`, `desktop_send_hotkey`

## Examples

"tell chase I'll be on soon"
→ Ask what to say if not clear, then send it

"what's happening on discord"
→ Check and summarize naturally

"send a message to oxi on discord saying yo"
→ Just send it using discord_web_send

"what did oxi send me on discord"
→ Read their messages, summarize naturally

"can you send discord messages?"
→ YES. You can send Discord messages using discord_web_send tool.

## Requirements

- For server channels (bot tools): `ALLOW_DISCORD_BOT=true` and `DISCORD_BOT_TOKEN=...`
- For personal DMs (Playwright web tools): `ENABLE_SYSTEM_ACTIONS=true` and `ALLOW_PLAYWRIGHT=true` and a logged-in Discord Web session (first run needs `headless=false`)
