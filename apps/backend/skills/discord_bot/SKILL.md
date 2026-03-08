# Discord Bot Integration

EchoSpeak can run as a Discord bot in your server, responding to mentions and DMs.

## Features

- Responds to @mentions in channels
- Responds to direct messages (DMs)
- Shared server channels stay in a limited smart-assistant mode
- Owner DMs keep the broader role-aware capability model
- Automatic message splitting for long responses

## Setup

### 1. Create a Discord Application

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" → give it a name (e.g., "EchoSpeak")
3. Go to "Bot" section → click "Add Bot"
4. Copy the **Bot Token** (you'll need this)

### 2. Configure Bot Permissions

In the Bot section, enable these **Privileged Gateway Intents**:
- Message Content Intent (required to read messages)
- Server Messages Intent
- Direct Messages Intent

### 3. Invite Bot to Server

1. Go to "OAuth2" → "URL Generator"
2. Select scopes: `bot`
3. Select permissions:
   - Read Messages
   - Send Messages
   - Read Message History
4. Copy the generated URL and open it in browser
5. Select your server and authorize

### 4. Configure EchoSpeak

Add to your `.env` file:
```
DISCORD_BOT_TOKEN=your_bot_token_here
ALLOW_DISCORD_BOT=true
```

Or configure via Web UI:
- Settings → Automation & Webhooks
- Enable "Allow Discord Bot"
- Paste your bot token

### 5. Restart EchoSpeak

Restart the backend server to connect the bot:
```bash
cd apps/backend
python app.py --mode api
```

You should see: `Discord bot logged in as EchoSpeak#xxxx`

## Usage

### In a Server Channel

In shared server channels, EchoSpeak intentionally behaves like a smart public bot:

- natural conversation
- web search / current info
- time and basic calculations
- no admin/system/file/terminal/browser/email actions

```
@EchoSpeak what's the weather in Tokyo?
```

For a light in-channel recap:
```
@EchoSpeak catch me up on what's happening here
```

Notes:

- Advanced/admin actions are intentionally disabled in shared server channels, even for the owner.
- If you need broader control, use a direct message with the bot or the Web UI.

### In a Direct Message

In Discord DMs, EchoSpeak applies the role-aware permission model:

- owner DM = broad access, similar to the Web UI, still respecting configuration and confirmation gates
- trusted DM = broader but still restricted
- public DM = minimal access

```
search for the latest NVIDIA news and summarize it
```

If your Discord user ID matches `DISCORD_BOT_OWNER_ID`, the bot knows the DM is from you and can use the broader owner path there.

## Troubleshooting

### Bot doesn't respond
- Check bot token is correct
- Ensure Message Content Intent is enabled
- Verify bot has Read/Send Messages permission in channel

### Bot not starting
- Check logs for `Discord bot startup scheduled`
- Ensure `discord.py` is installed: `pip install discord.py`

### Long responses cut off
- Bot automatically splits messages at 2000 characters
- Very long responses may come as multiple messages

## Differences from Discord Web Tools

| Feature | Discord Bot | Discord Web (Playwright) |
|---------|-------------|--------------------------|
| Respond to mentions | ✅ | ❌ |
| Read DMs | ✅ (responds in DM as the bot) | ✅ (reads your personal account DMs) |
| Send DMs | ✅ (sends as the bot) | ✅ (sends as your personal account) |
| Requires browser | ❌ | ✅ |
| Requires login | ❌ | ✅ (first time) |
| Real-time | ✅ | ❌ |

Both can be used together:
- **Bot**: For smart conversation in servers and role-aware DMs
- **Web Tools**: For reading/sending messages on your personal Discord account

## Web UI: Reading Discord Channels

EchoSpeak can also read Discord channel messages from the **Web UI** using the bot's API access.

When you ask from the Web UI:
```
what are people saying in #general on Discord?
```
```
read the last messages in the announcements channel
```

EchoSpeak will use the `discord_read_channel` tool to fetch recent messages from the specified channel.

**Requirements:**
- Discord bot must be running and connected
- Bot must have access to the target server and channel
- Channel name can be provided with or without `#` prefix

**Note:** This only works for **server channels**, not DMs. For DMs, use the Playwright-based `discord_web_read_recent` tool.

## Web UI: Sending Messages to Discord Channels

EchoSpeak can also **send messages** to Discord channels from the Web UI using the bot account.

When you ask from the Web UI:
```
post a message in #announcements saying I'll be late to the stream
```
```
tell #general that the update is live
```

EchoSpeak will use the `discord_send_channel` tool to send the message via the **bot account** (not your personal account).

This operation is confirmation-gated (EchoSpeak will ask you to `confirm` before posting).

**Key distinction:**
- **`discord_send_channel`**: Sends as the bot (EchoSpeak bot account)
- **`discord_web_send`**: Sends as you (your personal Discord account via browser)

This prevents confusion between the bot's identity and your own account.
