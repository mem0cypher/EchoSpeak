"""
Daily Briefing Skill — Custom Tool via Skill-Tool Bridge

Demonstrates the Skill → Tool Bridge (Update 2) and Routines (Update 3).

This file auto-registers the `daily_briefing` tool when the skill is loaded.
No editing of core tools.py, TOOL_METADATA, or _create_tools() needed.
The tool uses existing `web_search` and `get_system_time` tools internally.
"""

from datetime import datetime
from typing import Optional

from langchain.tools import tool
from pydantic import BaseModel, Field

from agent.tool_registry import ToolRegistry


class DailyBriefingArgs(BaseModel):
    """Arguments for the daily_briefing tool."""
    location: Optional[str] = Field(
        default=None,
        description="Optional location for weather info (e.g. 'Edmonton, Canada')"
    )
    topics: Optional[str] = Field(
        default=None,
        description="Optional comma-separated topics of interest (e.g. 'AI, crypto, sports')"
    )


@ToolRegistry.register(
    name="daily_briefing",
    description="Generate a daily morning briefing with news, weather, and trending topics",
    category="information",
)
@tool(args_schema=DailyBriefingArgs, description=(
    "Generate a daily morning briefing with current news, weather, and trending topics. "
    "Call this when the user asks for their daily briefing or morning update."
))
def daily_briefing(location: Optional[str] = None, topics: Optional[str] = None) -> str:
    """Generate a daily briefing by searching for current news and info."""
    from loguru import logger

    now = datetime.now()
    date_str = now.strftime("%A, %B %d, %Y")
    sections = [f"Daily Briefing for {date_str}\n"]

    # Try to use the existing web_search tool
    def _search(query: str) -> str:
        """Run a web search using the existing tool."""
        try:
            from agent.tools import get_available_tools
            tools_dict = {t.name: t for t in get_available_tools()}
            search_tool = tools_dict.get("web_search")
            if search_tool:
                result = search_tool.invoke({"query": query})
                return str(result)
        except Exception as e:
            logger.debug(f"Web search failed for '{query}': {e}")
        return ""

    # 1. Top news
    news_query = f"top news today {date_str}"
    if topics:
        topic_list = [t.strip() for t in topics.split(",")]
        news_query = f"{topic_list[0]} news today {date_str}"

    news_result = _search(news_query)
    if news_result:
        sections.append(f"Top News:\n{news_result[:500]}")
    else:
        sections.append("Top News: Could not fetch news at this time.")

    # 2. Weather (if location provided)
    if location:
        weather_result = _search(f"weather today {location} {date_str}")
        if weather_result:
            sections.append(f"Weather ({location}):\n{weather_result[:300]}")

    # 3. Trending / additional topics
    if topics:
        topic_list = [t.strip() for t in topics.split(",")]
        for topic in topic_list[1:3]:  # Max 2 extra topics
            result = _search(f"{topic} latest news {date_str}")
            if result:
                sections.append(f"{topic.title()}:\n{result[:300]}")

    sections.append(f"\nBriefing generated at {now.strftime('%I:%M %p')}")
    return "\n\n".join(sections)


