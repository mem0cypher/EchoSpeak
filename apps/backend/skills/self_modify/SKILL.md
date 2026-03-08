# Self-Modification

This skill allows EchoSpeak to modify its own codebase with automatic git-based rollback.

## Purpose
- Allow the agent to improve itself
- Add new capabilities dynamically
- Fix bugs in its own code
- Experiment safely with rollback capability

## Safety Mechanisms

1. **Git Commit Before Each Change** - Every edit creates a backup commit
2. **Rollback Tool** - Can undo recent changes with `self_rollback`
3. **Config Flag Required** - Must set `ALLOW_SELF_MODIFICATION=true`
4. **Confirmation Required** - Each edit needs user approval

## Tools

### Exploration Tools (Safe - No Confirmation Needed)

#### self_list
List files and directories in EchoSpeak's codebase.

```
path: Relative path to list (empty = project root)
```

Example:
```
self_list(path="apps/backend/agent")
```

#### self_read
Read a file from EchoSpeak's codebase with line numbers.

```
file_path: Relative path from project root
start_line: Start line number (default: 1)
end_line: End line number (default: 100)
```

Example:
```
self_read(file_path="apps/backend/agent/core.py", start_line=1, end_line=50)
```

#### self_grep
Search for patterns in EchoSpeak's codebase.

```
pattern: Search pattern (regex supported)
path: Relative path to search in (empty = whole project)
```

Example:
```
self_grep(pattern="def process_query", path="apps/backend/agent")
```

### Modification Tools (Require Confirmation)

#### self_edit
Edit a file in EchoSpeak's codebase.

```
file_path: Relative path from project root (e.g., "apps/backend/agent/core.py")
old_content: Exact text to replace (must match exactly)
new_content: New text to write
commit_message: Description for the backup commit
```

Example:
```
self_edit(
    file_path="apps/backend/SOUL.md",
    old_content="## Values\n- Honesty over politeness",
    new_content="## Values\n- Honesty over politeness\n- Continuous self-improvement",
    commit_message="Add continuous self-improvement value"
)
```

#### self_rollback
Undo recent self-modification commits.

```
steps: Number of commits to roll back (1-10)
```

Example:
```
self_rollback(steps=1)  # Undo last change
```

#### self_git_status
View git status and recent commits to see what can be rolled back.

## Workflow

1. **Explore** - Use `self_list`, `self_read`, `self_grep` to understand the code
2. **Make edit** - Use `self_edit` with exact old/new content
3. **Test** - Restart server and verify the change works
4. **Rollback if broken** - Use `self_rollback` if something went wrong

## Multi-step behavior

EchoSpeak can now execute multi-step tool sequences in a single user message. For self-modification, that means you can ask for an end-to-end flow like:

"search your code for where file_write is handled, read that section, then patch it"

and EchoSpeak should:

- run one or more exploration tools (`self_grep`, `self_read`, `self_list`)
- then propose a `self_edit` (confirmation-gated)

You should see each tool invocation in the UI as it happens.

## Important Notes

- The agent can modify ANY file in its codebase
- Changes require server restart to take effect
- Always verify changes work before making more edits
- Use `self_git_status` frequently to track changes
- Rollback is destructive - it discards commits

## Configuration

Enable self-modification in `.env`:

```
ENABLE_SYSTEM_ACTIONS=true
ALLOW_SELF_MODIFICATION=true
```

## Example Session

```
User: "Add a new tool that tells jokes"

Agent:
1. self_list(path="apps/backend/agent")  # Explore structure
2. self_read(file_path="apps/backend/agent/tools.py", start_line=1, end_line=100)  # See how tools work
3. self_grep(pattern="@tool", path="apps/backend/agent/tools.py")  # Find tool examples
4. self_edit(...)  # Add the joke tool
5. Says: "I've added a joke tool. Restarting to apply..."
6. Calls /admin/restart endpoint
7. Server restarts with new tool

If it breaks:
1. User: "That broke something, roll back"
2. Agent uses self_rollback(steps=1)
3. Restarts server
4. Code is restored to working state
```

## Safety Guidelines

- Only edit files you understand
- Make small, focused changes
- Test after each change
- Keep commits atomic (one logical change per commit)
- Document what you're changing in commit messages
- Use exploration tools liberally before editing
