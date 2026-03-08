Agent-to-Agent (A2A) protocol tools for EchoSpeak.

## When to use

When the user asks to:
- "Ask the research agent to look into X"
- "Delegate this task to the coding agent"
- "What agents are available?"
- "Discover agents at https://example.com"
- "Send a task to the analytics agent"

## Tool reference

### a2a_discover
Fetch a remote agent's capabilities by looking up its Agent Card at `/.well-known/agent.json`. Returns the agent's name, description, skills, and supported features.

### a2a_delegate
Send a task to a remote A2A agent and wait for the result. Requires the agent's base URL and the task message. Returns the agent's response.

## Requirements

Set `A2A_ENABLED=true`. Optionally configure `A2A_KNOWN_AGENTS` with comma-separated base URLs of known agents for quick discovery.

## Output style

When presenting agent capabilities, use a concise table format. When displaying delegation results, summarize the remote agent's response naturally.
