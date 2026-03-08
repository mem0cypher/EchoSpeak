Discord contacts and messaging (DMs/personal only).

## IMPORTANT: This skill is for DMs/personal Discord, NOT server channels

You have the ability to send Discord messages directly using the `discord_web_send` tool (Playwright; personal Discord web session). When someone asks you to send a DM, DO IT.

For Discord **server channels** (e.g. `#general`, `#updates`), use the bot tools `discord_read_channel` / `discord_send_channel` instead. The agent automatically routes server channel queries to bot tools.

Tools: `discord_contacts_add`, `discord_contacts_discover`, `discord_web_read_recent`, `discord_web_send`

## Intent-based Routing

The agent detects server channel intent (e.g., `#general`, "what are people saying in #updates") and routes to bot tools automatically. This skill's tools are for DM/personal messaging only.

If the user asks to DM someone by a known contacts key (like `oxi`) using phrasing such as "send a personal message to oxi saying ...", the agent can route to `discord_web_send` even if the user doesn't explicitly type the word "discord".

## Reading messages

When someone asks to check Discord or read recent messages, just do it. Don't explain what you're doing or list timestamps. Read the messages and tell them what people said, naturally.

Bad: "Here are the recent messages from Discord: [12:53 pm] chase: hello"
Good: "chase said hello a bit ago"

If there's a conversation, summarize it conversationally. Don't dump raw output.

## Adding contacts

If they want to add someone to Discord contacts, ask for their Discord link or use auto-discovery if Playwright is enabled.

Keep contact names simple and lowercase (e.g., "mayo", "chase", "oxi").

## Natural language queries

You can ask about Discord in natural ways:
- "what did oxi say on discord"
- "check my dms"
- "what was the last thing chase sent me"
- "read messages from oxi on discord"

The system will figure out who you mean and read their messages.

## Examples

"what did chase say on discord"
→ Read messages, tell them naturally what chase said

"check my dms"
→ Read recent DMs, summarize who messaged and what about

"add oxi to discord contacts"
→ Ask for their Discord link, or auto-discover if Playwright enabled

"send a message to oxi on discord saying yo"
→ Just send it

## Requirements

- `ENABLE_SYSTEM_ACTIONS=true` in `.env`
- `ALLOW_PLAYWRIGHT=true` in `.env`
- A logged-in Discord Web session (run once with `headless=false` to log in)
