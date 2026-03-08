"""
WhatsApp tools — send messages, read conversations, list chats.

Requires a whatsapp-web.js bridge server running separately.
  ALLOW_WHATSAPP=true
  WHATSAPP_API_URL=http://localhost:3001
"""

from __future__ import annotations

from typing import Optional

import requests
from loguru import logger
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from agent.tool_registry import ToolRegistry

# ── Helpers ──────────────────────────────────────────────────────────

def _wa_config():
    """Get WhatsApp bridge URL."""
    try:
        from config import config
    except ImportError:
        raise RuntimeError("Config not available")

    if not getattr(config, "allow_whatsapp", False):
        raise RuntimeError("WhatsApp integration is disabled. Set ALLOW_WHATSAPP=true in .env")

    api_url = getattr(config, "whatsapp_api_url", "").rstrip("/")
    if not api_url:
        raise RuntimeError("WHATSAPP_API_URL not set in .env")

    return api_url


def _wa_get(path: str):
    """GET request to WhatsApp bridge."""
    url = _wa_config()
    resp = requests.get(f"{url}{path}", timeout=15)
    resp.raise_for_status()
    return resp.json()


def _wa_post(path: str, data: dict | None = None):
    """POST request to WhatsApp bridge."""
    url = _wa_config()
    resp = requests.post(f"{url}{path}", json=data or {}, timeout=15)
    resp.raise_for_status()
    return resp.json() if resp.text else {}


# ── Schemas ─────────────────────────────────────────────────────────

class WASendArgs(BaseModel):
    to: str = Field(description="Recipient phone number with country code (e.g. '+1234567890') or chat name")
    message: str = Field(description="Message text to send")


class WAReadRecentArgs(BaseModel):
    chat_id: Optional[str] = Field(
        default=None,
        description="Chat ID or phone number to read from. If empty, reads across all chats.",
    )
    limit: int = Field(default=10, description="Number of messages to retrieve")


class WAListChatsArgs(BaseModel):
    limit: int = Field(default=20, description="Number of chats to list")


# ── whatsapp_send ───────────────────────────────────────────────────

@ToolRegistry.register(
    name="whatsapp_send",
    description="Send a WhatsApp message to a contact by phone number or chat name.",
    category="whatsapp",
    is_action=True,
    risk_level="moderate",
)
@tool(args_schema=WASendArgs)
def whatsapp_send(to: str, message: str) -> str:
    """Send a WhatsApp message."""
    try:
        result = _wa_post("/api/send", {
            "to": to.strip(),
            "message": message.strip(),
        })
        msg_id = result.get("id", "unknown")
        return f"📱 Message sent to **{to}**\n- ID: `{msg_id}`"
    except Exception as exc:
        logger.error(f"whatsapp_send failed: {exc}")
        return f"❌ Failed to send WhatsApp message: {exc}"


# ── whatsapp_read_recent ────────────────────────────────────────────

@ToolRegistry.register(
    name="whatsapp_read_recent",
    description="Read recent WhatsApp messages from a specific chat or across all chats.",
    category="whatsapp",
    risk_level="safe",
)
@tool(args_schema=WAReadRecentArgs)
def whatsapp_read_recent(chat_id: Optional[str] = None, limit: int = 10) -> str:
    """Read recent WhatsApp messages."""
    try:
        params = f"?limit={limit}"
        if chat_id:
            params += f"&chat_id={chat_id.strip()}"

        result = _wa_get(f"/api/messages{params}")
        messages = result.get("messages", [])

        if not messages:
            target = f" from {chat_id}" if chat_id else ""
            return f"📱 No recent messages{target}."

        lines = ["📱 **Recent WhatsApp Messages**\n"]
        for msg in messages[:limit]:
            sender = msg.get("sender", msg.get("from", "Unknown"))
            body = msg.get("body", msg.get("message", ""))[:200]
            timestamp = msg.get("timestamp", "")
            chat_name = msg.get("chat_name", "")
            is_me = msg.get("fromMe", False)

            prefix = "➡️ You" if is_me else f"⬅️ {sender}"
            chat_label = f" ({chat_name})" if chat_name and not chat_id else ""
            lines.append(f"• {prefix}{chat_label}: {body}")
            if timestamp:
                lines.append(f"  {timestamp}")

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"whatsapp_read_recent failed: {exc}")
        return f"❌ Failed to read messages: {exc}"


# ── whatsapp_list_chats ─────────────────────────────────────────────

@ToolRegistry.register(
    name="whatsapp_list_chats",
    description="List recent WhatsApp chats with last message preview and unread count.",
    category="whatsapp",
    risk_level="safe",
)
@tool(args_schema=WAListChatsArgs)
def whatsapp_list_chats(limit: int = 20) -> str:
    """List WhatsApp chats."""
    try:
        result = _wa_get(f"/api/chats?limit={limit}")
        chats = result.get("chats", [])

        if not chats:
            return "📱 No WhatsApp chats found."

        lines = ["📱 **WhatsApp Chats**\n"]
        for chat in chats[:limit]:
            name = chat.get("name", chat.get("id", "Unknown"))
            last_msg = (chat.get("last_message", "") or "")[:80]
            unread = chat.get("unread_count", 0)
            is_group = chat.get("isGroup", False)

            icon = "👥" if is_group else "👤"
            unread_badge = f" 🔴 {unread}" if unread else ""
            lines.append(f"• {icon} **{name}**{unread_badge}\n  {last_msg}")

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"whatsapp_list_chats failed: {exc}")
        return f"❌ Failed to list chats: {exc}"
