Notion workspace integration for EchoSpeak.

## When to use

When the user asks about:
- "Search my Notion for X"
- "List my Notion pages"
- "Create a Notion page about X"
- "Add a note to my Notion page"
- "What's in my Notion?"

## Tool reference

### notion_search
Search across the entire Notion workspace by query. Returns matching pages and databases with titles and URLs.

### notion_get_page
Get a specific page by its Notion page ID. Returns the page title, properties, and content blocks.

### notion_list_pages
List pages from the configured default database, or list recent pages from the workspace.

### notion_create_page
Create a new page in the default database or as a standalone page. Requires a title and optional content. This is an action tool — requires confirmation.

### notion_append_block
Append a text block to an existing Notion page by page ID. Good for adding notes, to-do items, or updates. This is an action tool — requires confirmation.

## Requirements

Set `ALLOW_NOTION=true` and `NOTION_TOKEN` (Notion integration token). Optionally set `NOTION_DEFAULT_DATABASE_ID` for listing pages from a specific database.

## Output style

Keep Notion content summaries concise. Use markdown formatting. Show page titles as links when possible.
