# Server Restart

This skill allows the agent to restart the EchoSpeak server.

## Purpose
- Restart the server after making configuration changes
- Apply new skills without manual intervention
- Reload the soul configuration

## How to Restart

Use the restart endpoint:

```
POST http://localhost:8000/admin/restart
```

This endpoint requires an admin key header:

```
X-Admin-Key: <ADMIN_API_KEY>
```

Or use the `terminal_run` tool with curl:

```bash
curl -X POST http://localhost:8000/admin/restart \
  -H "X-Admin-Key: $ADMIN_API_KEY"
```

## Workflow

1. **Make changes** (edit SOUL.md, create new skill, modify config)
2. **Announce restart** - Tell the user you're restarting to apply changes
3. **Call restart endpoint** - The server will exit gracefully after your response completes
4. **External process manager** (systemd, docker, uvicorn) will restart the server

## Important Notes

- The restart happens AFTER your current response completes
- The user will see a brief disconnect while the server restarts
- This requires an external process manager to auto-restart
- If running manually (python app.py --mode api), the server will NOT restart automatically

## Example

```
User: "Create a new skill for calendar management and apply it"

Agent: 
1. Creates the skill files
2. Says: "I've created the calendar skill. Restarting server to apply changes..."
3. Calls: POST /admin/restart with `X-Admin-Key`
4. Response completes
5. Server exits and restarts
6. User reconnects to fresh server with new skill loaded
```

## Safety

- Only restart when necessary (new skills, config changes)
- Always warn the user before restarting
- The restart is graceful - current response will complete first
