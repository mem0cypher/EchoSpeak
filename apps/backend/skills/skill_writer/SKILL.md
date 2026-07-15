Self-writing skill system for EchoSpeak.

## When to use

When the user asks you to:
- Create a new skill or capability
- "Learn how to do X" — by creating a skill for X
- "Add a tool for Y" — by writing a skill with tool definitions
- List installed skills or see what skills are available
- Enable or disable a specific skill

## Tool reference

### skill_create (governed)

Creates an **experimental + DISABLED** package only. Never executes the skill in the same Turn. Approved review and a separate `skill_enable` Turn are required. Does not install dependencies, download models, or grant permissions.

### skill_create
Creates a new skill directory with SKILL.md and skill.json.
Supply a clear `name`, `description`, and `prompt` that instructs the agent how to behave when the skill is active.
Optionally provide `tool_names` (list of existing tool names) the skill needs access to.
The candidate stays unavailable until approved review and explicit enablement.

### skill_list
Returns a table of all installed skills with their ID, name, description, and what files they include (tools.py, plugin.py).
Use this to understand what skills already exist before creating duplicates.

### skill_review
After isolated tests, records concise validation evidence and the active registration approval. It promotes the candidate to installed-but-disabled; it does not load or enable the skill.

### skill_enable
Enable or disable a reviewed skill by ID. Draft or experimental candidates fail closed. Disabling creates a `.disabled` marker; enabling removes it and refreshes the canonical registry.
Use this when the user wants to turn off a skill without deleting it.

## Best practices

- Always call `skill_list` first to avoid creating duplicate skills
- Use clear, specific prompts in SKILL.md — they become part of the agent's system prompt
- Use snake_case for skill IDs (derived from name)
- Keep skill prompts under 500 words — concise instructions work best
- If the user's request maps to an existing tool, mention that instead of creating a new skill
