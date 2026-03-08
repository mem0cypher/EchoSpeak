"""
Notion tools — search, read, create, and append to Notion pages.

Requires:
  pip install notion-client
  ALLOW_NOTION=true
  NOTION_TOKEN=secret_...
"""

from __future__ import annotations

import json
from typing import Optional, List

from loguru import logger
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from agent.tool_registry import ToolRegistry

# ── Helpers ──────────────────────────────────────────────────────────

def _get_notion_client():
    """Build and return a Notion client."""
    try:
        from config import config
    except ImportError:
        raise RuntimeError("Config not available")

    if not getattr(config, "allow_notion", False):
        raise RuntimeError("Notion integration is disabled. Set ALLOW_NOTION=true in .env")

    token = getattr(config, "notion_token", "")
    if not token:
        raise RuntimeError("NOTION_TOKEN not set in .env")

    from notion_client import Client
    return Client(auth=token)


def _extract_title(page: dict) -> str:
    """Extract the title from a Notion page object."""
    props = page.get("properties", {})
    for key, val in props.items():
        if val.get("type") == "title":
            title_arr = val.get("title", [])
            return "".join(t.get("plain_text", "") for t in title_arr) or "(Untitled)"
    return "(Untitled)"


def _page_url(page: dict) -> str:
    """Get the Notion URL for a page."""
    return page.get("url", "")


def _format_page_summary(page: dict) -> str:
    """Format a page for list display."""
    title = _extract_title(page)
    url = _page_url(page)
    page_id = page.get("id", "")[:12]
    last_edited = page.get("last_edited_time", "")[:10]
    return f"• **{title}** — [link]({url})\n  ID: `{page_id}…` | Edited: {last_edited}"


# ── Schemas ─────────────────────────────────────────────────────────

class NotionSearchArgs(BaseModel):
    query: str = Field(description="Search query to find pages or databases in Notion")
    limit: int = Field(default=10, description="Max number of results")


class NotionGetPageArgs(BaseModel):
    page_id: str = Field(description="Notion page ID")


class NotionListPagesArgs(BaseModel):
    database_id: Optional[str] = Field(
        default=None,
        description="Database ID to list from. Uses default database if not provided.",
    )
    limit: int = Field(default=20, description="Max number of pages")


class NotionCreatePageArgs(BaseModel):
    title: str = Field(description="Page title")
    content: Optional[str] = Field(default=None, description="Initial page content (plain text)")
    database_id: Optional[str] = Field(
        default=None,
        description="Database ID to create the page in. Uses default if not provided.",
    )


class NotionAppendBlockArgs(BaseModel):
    page_id: str = Field(description="Page ID to append content to")
    content: str = Field(description="Text content to append as a paragraph block")


# ── notion_search ───────────────────────────────────────────────────

@ToolRegistry.register(
    name="notion_search",
    description="Search across the Notion workspace for pages and databases matching a query.",
    category="notion",
    risk_level="safe",
)
@tool(args_schema=NotionSearchArgs)
def notion_search(query: str, limit: int = 10) -> str:
    """Search Notion workspace."""
    try:
        client = _get_notion_client()
        results = client.search(query=query, page_size=min(limit, 100))
        items = results.get("results", [])

        if not items:
            return f"🔍 No results found for \"{query}\" in Notion."

        lines = [f"🔍 **Notion Search:** \"{query}\" ({len(items)} results)\n"]
        for item in items[:limit]:
            obj_type = item.get("object", "page")
            if obj_type == "page":
                lines.append(_format_page_summary(item))
            elif obj_type == "database":
                title_arr = item.get("title", [])
                db_title = "".join(t.get("plain_text", "") for t in title_arr) or "(Untitled DB)"
                lines.append(f"• 🗃️ **{db_title}** (database)\n  ID: `{item.get('id', '')[:12]}…`")

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"notion_search failed: {exc}")
        return f"❌ Failed to search Notion: {exc}"


# ── notion_get_page ─────────────────────────────────────────────────

@ToolRegistry.register(
    name="notion_get_page",
    description="Get a specific Notion page by ID with its properties and content blocks.",
    category="notion",
    risk_level="safe",
)
@tool(args_schema=NotionGetPageArgs)
def notion_get_page(page_id: str) -> str:
    """Get a Notion page and its content."""
    try:
        client = _get_notion_client()
        page = client.pages.retrieve(page_id=page_id.strip())
        title = _extract_title(page)
        url = _page_url(page)

        # Get content blocks
        blocks_response = client.blocks.children.list(block_id=page_id.strip(), page_size=50)
        blocks = blocks_response.get("results", [])

        lines = [f"📄 **{title}**\n[Open in Notion]({url})\n"]

        for block in blocks:
            block_type = block.get("type", "")
            block_data = block.get(block_type, {})

            if block_type in ("paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item"):
                rich_texts = block_data.get("rich_text", [])
                text = "".join(rt.get("plain_text", "") for rt in rich_texts)
                if block_type == "heading_1":
                    lines.append(f"# {text}")
                elif block_type == "heading_2":
                    lines.append(f"## {text}")
                elif block_type == "heading_3":
                    lines.append(f"### {text}")
                elif block_type in ("bulleted_list_item", "numbered_list_item"):
                    lines.append(f"• {text}")
                else:
                    lines.append(text)
            elif block_type == "to_do":
                rich_texts = block_data.get("rich_text", [])
                text = "".join(rt.get("plain_text", "") for rt in rich_texts)
                checked = "✅" if block_data.get("checked") else "☐"
                lines.append(f"{checked} {text}")
            elif block_type == "code":
                rich_texts = block_data.get("rich_text", [])
                text = "".join(rt.get("plain_text", "") for rt in rich_texts)
                lang = block_data.get("language", "")
                lines.append(f"```{lang}\n{text}\n```")
            elif block_type == "divider":
                lines.append("---")

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"notion_get_page failed: {exc}")
        return f"❌ Failed to get page: {exc}"


# ── notion_list_pages ───────────────────────────────────────────────

@ToolRegistry.register(
    name="notion_list_pages",
    description="List pages from a Notion database, or recent pages from the workspace.",
    category="notion",
    risk_level="safe",
)
@tool(args_schema=NotionListPagesArgs)
def notion_list_pages(database_id: Optional[str] = None, limit: int = 20) -> str:
    """List pages from a Notion database."""
    try:
        from config import config
        client = _get_notion_client()

        db_id = (database_id or "").strip() or getattr(config, "notion_default_database_id", "")
        if not db_id:
            # Fallback: search for recent pages
            results = client.search(filter={"property": "object", "value": "page"}, page_size=min(limit, 100))
            items = results.get("results", [])
            if not items:
                return "📋 No pages found in Notion workspace."
            lines = [f"📋 **Recent Notion Pages** ({len(items)} found)\n"]
            for page in items[:limit]:
                lines.append(_format_page_summary(page))
            return "\n".join(lines)

        results = client.databases.query(
            database_id=db_id,
            page_size=min(limit, 100),
        )
        pages = results.get("results", [])
        if not pages:
            return f"📋 No pages in database `{db_id[:12]}…`."

        lines = [f"📋 **Database Pages** ({len(pages)} found)\n"]
        for page in pages[:limit]:
            lines.append(_format_page_summary(page))

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"notion_list_pages failed: {exc}")
        return f"❌ Failed to list pages: {exc}"


# ── notion_create_page ──────────────────────────────────────────────

@ToolRegistry.register(
    name="notion_create_page",
    description="Create a new Notion page with a title and optional content.",
    category="notion",
    is_action=True,
    risk_level="moderate",
)
@tool(args_schema=NotionCreatePageArgs)
def notion_create_page(
    title: str,
    content: Optional[str] = None,
    database_id: Optional[str] = None,
) -> str:
    """Create a new Notion page."""
    try:
        from config import config
        client = _get_notion_client()

        db_id = (database_id or "").strip() or getattr(config, "notion_default_database_id", "")

        body: dict = {
            "properties": {
                "title": {
                    "title": [{"text": {"content": title.strip()}}]
                }
            }
        }

        if db_id:
            body["parent"] = {"database_id": db_id}
        else:
            # Create as standalone page in workspace
            body["parent"] = {"type": "workspace", "workspace": True}

        # Add initial content as a paragraph block
        if content:
            body["children"] = [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"text": {"content": content.strip()}}]
                    },
                }
            ]

        page = client.pages.create(**body)
        url = _page_url(page)
        return (
            f"✅ Page created: **{title}**\n"
            f"- ID: `{page.get('id', 'unknown')[:12]}…`\n"
            f"- URL: {url}"
        )
    except Exception as exc:
        logger.error(f"notion_create_page failed: {exc}")
        return f"❌ Failed to create page: {exc}"


# ── notion_append_block ─────────────────────────────────────────────

@ToolRegistry.register(
    name="notion_append_block",
    description="Append a text paragraph block to an existing Notion page.",
    category="notion",
    is_action=True,
    risk_level="moderate",
)
@tool(args_schema=NotionAppendBlockArgs)
def notion_append_block(page_id: str, content: str) -> str:
    """Append a text block to a Notion page."""
    try:
        client = _get_notion_client()
        client.blocks.children.append(
            block_id=page_id.strip(),
            children=[
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"text": {"content": content.strip()}}]
                    },
                }
            ],
        )
        return f"✅ Content appended to page `{page_id[:12]}…`."
    except Exception as exc:
        logger.error(f"notion_append_block failed: {exc}")
        return f"❌ Failed to append block: {exc}"
