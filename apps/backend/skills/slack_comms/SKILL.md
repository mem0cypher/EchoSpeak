# Slack Communications

Send messages to Slack channels via webhook integration.

## Available Tools

- `slack_send_message` - Send a message to a Slack channel
- `slack_list_channels` - List available Slack channels
- `slack_search_users` - Search for Slack users by name

## How to Send a Slack Message

1. **List available channels:**
   ```
   slack_list_channels()
   ```
   Returns list of channels the webhook has access to.

2. **Send message:**
   ```
   slack_send_message(channel="#general", text="Hello from EchoSpeak!")
   ```
   Creates an approval-backed action requiring confirmation.

3. **Confirm send:**
   User confirms the message content and destination, then the message is posted.

## Safety Rules

- ALWAYS require user confirmation before sending
- NEVER send sensitive credentials or secrets
- Validate channel names start with '#'
- Keep messages appropriate and professional
- Use blocks/formatting for rich messages when needed

## Prerequisites

- Slack App with incoming webhook configured
- `ENABLE_SYSTEM_ACTIONS=true` in `.env`
- `ALLOW_SLACK_SEND=true` in `.env`

## Configuration

Set these environment variables:
```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T00/B00/xxx
SLACK_BOT_TOKEN=xoxb-...  # Optional, for listing channels
```

## Example Flow

User: "Post 'Meeting starting now' to #general on Slack"

Agent:
1. `slack_list_channels()` → verify #general exists
2. `slack_send_message(channel="#general", text="Meeting starting now")`
3. Show preview: "Post to #general: 'Meeting starting now'"
4. Ask: "Should I send this message?"
5. User confirms
6. Message posted via webhook

## Rich Message Formatting

Use Slack blocks for formatted messages:
```
slack_send_message(
  channel="#general",
  blocks=[
    {"type": "section", "text": {"type": "mrkdwn", "text": "*Bold header*"}},
    {"type": "divider"},
    {"type": "section", "text": {"type": "plain_text", "text": "Message body"}}
  ]
)
```

## User Search

Find Slack users by name:
```
slack_search_users(query="john")
```
Returns matching users with their Slack IDs and usernames.
