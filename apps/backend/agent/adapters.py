"""Platform adapter compatibility layer for EchoSpeak.

The core pipeline delegates source-specific role resolution and response
post-processing here. Keep the default adapter deliberately small so local web
chat stays the source of truth and social surfaces only add safety clamps.
"""

from __future__ import annotations

from typing import Any, Optional

from config import DiscordUserRole, config


class BaseAdapter:
    def resolve_role(self, source: Optional[str], discord_user_info: Optional[dict[str, Any]] = None) -> str:
        src = str(source or "web").strip().lower()
        if src in {"twitter", "twitch"}:
            return DiscordUserRole.PUBLIC
        if src in {"twitter_autonomous", "proactive", "heartbeat", "routine", "system", "web"}:
            return DiscordUserRole.OWNER

        if src in {"discord_bot", "discord_bot_dm"}:
            info = discord_user_info or {}
            access_reason = str(info.get("access_reason") or "").strip().lower()
            user_id = str(info.get("user_id") or "").strip()
            owner_id = str(getattr(config, "discord_bot_owner_id", "") or "").strip()
            trusted_ids = {str(x).strip() for x in (getattr(config, "discord_trusted_user_ids", []) or []) if str(x).strip()}
            if access_reason == "owner_id" or (owner_id and user_id == owner_id):
                return DiscordUserRole.OWNER
            if access_reason == "trusted_user" or user_id in trusted_ids:
                return DiscordUserRole.TRUSTED
            return DiscordUserRole.PUBLIC

        return DiscordUserRole.OWNER

    def preprocess_query(self, agent: Any, user_input: str, callbacks: Optional[list] = None):
        return None

    def postprocess_response(self, agent: Any, user_input: str, response_text: str) -> str:
        return str(response_text or "")


class DiscordAdapter(BaseAdapter):
    def postprocess_response(self, agent: Any, user_input: str, response_text: str) -> str:
        src = str(getattr(agent, "_current_source", "") or "").strip().lower()
        if src not in {"discord_bot", "discord_bot_dm"}:
            return str(response_text or "")
        clamp = getattr(agent, "_clamp_discord_casual_reply", None)
        if callable(clamp):
            return str(clamp(user_input, response_text) or "")
        return str(response_text or "")


_DEFAULT = BaseAdapter()
_DISCORD = DiscordAdapter()


def get_adapter(source: Optional[str]) -> BaseAdapter:
    src = str(source or "web").strip().lower()
    if src in {"discord_bot", "discord_bot_dm"}:
        return _DISCORD
    return _DEFAULT
