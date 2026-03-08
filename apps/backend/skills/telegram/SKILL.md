# Telegram Bot Integration

Native Telegram bot that routes messages through the EchoSpeak agent pipeline.

## Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) on Telegram
2. Copy the bot token
3. Set the following in `.env`:

```
ALLOW_TELEGRAM_BOT=true
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_ALLOWED_USERS=your_username    # optional, comma-separated
TELEGRAM_AUTO_CONFIRM=true              # auto-approve tool actions
```

4. Install dependency: `pip install python-telegram-bot`
5. Restart the backend

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/status` | Show agent status (provider, model, tools) |
| `/help` | Show capabilities |
| _(any text)_ | Routes through full agent pipeline |

## Features

- **Full Pipeline** — Messages go through the same agent pipeline as web/Discord
- **Auth** — Optional user allowlist (username or user ID)
- **Heartbeat** — Receives proactive heartbeat messages
- **4096-char Splitting** — Long responses auto-split across messages

## API Endpoints

- `GET /telegram` — Bot status
- `POST /telegram/send` — Send a message to a chat (`{text, chat_id}`)

## Status

✅ Implemented — v5.4.0
