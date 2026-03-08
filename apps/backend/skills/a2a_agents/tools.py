"""
A2A outbound tools — discover remote agents and delegate tasks.

  A2A_ENABLED=true
  A2A_KNOWN_AGENTS=http://agent1:8000,http://agent2:8000
"""

from __future__ import annotations

from typing import Optional

from loguru import logger
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from agent.tool_registry import ToolRegistry


# ── Helpers ──────────────────────────────────────────────────────────

def _a2a_check():
    """Verify A2A is enabled."""
    try:
        from config import config
    except ImportError:
        raise RuntimeError("Config not available")

    if not getattr(config, "a2a_enabled", False):
        raise RuntimeError("A2A protocol is disabled. Set A2A_ENABLED=true in .env")


# ── Schemas ─────────────────────────────────────────────────────────

class A2ADiscoverArgs(BaseModel):
    agent_url: str = Field(description="Base URL of the remote agent (e.g. 'http://agent.example.com:8000')")


class A2ADelegateArgs(BaseModel):
    agent_url: str = Field(description="Base URL of the remote A2A agent")
    message: str = Field(description="Task message to send to the agent")
    auth_key: Optional[str] = Field(default=None, description="Optional API key for authenticated agents")


# ── a2a_discover ────────────────────────────────────────────────────

@ToolRegistry.register(
    name="a2a_discover",
    description="Discover a remote agent's capabilities by fetching its A2A Agent Card.",
    category="a2a",
    risk_level="safe",
)
@tool(args_schema=A2ADiscoverArgs)
def a2a_discover(agent_url: str) -> str:
    """Discover a remote A2A agent's capabilities."""
    try:
        _a2a_check()
        from agent.a2a import get_a2a_client
        client = get_a2a_client()

        card = client.discover(agent_url.strip())
        if not card:
            return f"❌ Could not discover agent at {agent_url}. No Agent Card found."

        name = card.get("name", "Unknown")
        desc = card.get("description", "No description")
        version = card.get("version", "?")
        skills = card.get("skills", [])

        lines = [
            f"🤖 **{name}** (v{version})",
            f"📝 {desc}",
            "",
        ]

        if skills:
            lines.append("**Skills:**")
            for s in skills[:10]:
                s_name = s.get("name", s.get("id", "?"))
                s_desc = s.get("description", "")[:100]
                lines.append(f"• **{s_name}** — {s_desc}")
        else:
            lines.append("_No skills advertised._")

        caps = card.get("capabilities", {})
        if caps:
            cap_list = [k for k, v in caps.items() if v]
            if cap_list:
                lines.append(f"\n**Capabilities:** {', '.join(cap_list)}")

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"a2a_discover failed: {exc}")
        return f"❌ Discovery failed: {exc}"


# ── a2a_delegate ────────────────────────────────────────────────────

@ToolRegistry.register(
    name="a2a_delegate",
    description="Send a task to a remote A2A agent and get the result.",
    category="a2a",
    is_action=True,
    risk_level="moderate",
)
@tool(args_schema=A2ADelegateArgs)
def a2a_delegate(agent_url: str, message: str, auth_key: Optional[str] = None) -> str:
    """Delegate a task to a remote A2A agent."""
    try:
        _a2a_check()
        from agent.a2a import get_a2a_client
        client = get_a2a_client()

        result = client.send_task(agent_url.strip(), message.strip(), auth_key=auth_key)
        if not result:
            return f"❌ Task delegation to {agent_url} failed. No response received."

        # Extract response from task result
        task_status = result.get("status", "unknown")
        messages = result.get("messages", [])

        agent_responses = [
            m for m in messages
            if isinstance(m, dict) and m.get("role") == "agent"
        ]

        if agent_responses:
            last_msg = agent_responses[-1]
            parts = last_msg.get("parts", [])
            text_parts = [p.get("text", "") for p in parts if p.get("type") == "text"]
            response_text = "\n".join(text_parts) if text_parts else str(last_msg)
        else:
            response_text = f"Task {task_status} but no agent response received."

        task_id = result.get("id", "?")
        return (
            f"🤖 **Remote Agent Response** (task: `{task_id}`, status: {task_status})\n\n"
            f"{response_text}"
        )
    except Exception as exc:
        logger.error(f"a2a_delegate failed: {exc}")
        return f"❌ Delegation failed: {exc}"
