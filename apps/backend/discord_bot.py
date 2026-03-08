"""
Discord bot integration for EchoSpeak.
Allows users to interact with EchoSpeak directly from Discord servers.
"""

import asyncio
import logging
import re
from typing import Optional, Callable, Any

from config import config

logger = logging.getLogger(__name__)

# Global bot instance
_bot_instance: Optional["EchoSpeakDiscordBot"] = None


def get_bot() -> Optional["EchoSpeakDiscordBot"]:
    """Get the global Discord bot instance."""
    return _bot_instance


def get_discord_bot_instance() -> Optional["EchoSpeakDiscordBot"]:
    return _bot_instance


def _get_live_bot_and_loop() -> tuple[Optional["EchoSpeakDiscordBot"], Optional[asyncio.AbstractEventLoop]]:
    bot = get_bot()
    if bot is None or bot.client is None:
        return None, None
    loop = bot.get_loop()
    if loop is None:
        return bot, None
    try:
        if hasattr(loop, "is_closed") and loop.is_closed():
            return bot, None
        if hasattr(loop, "is_running") and not loop.is_running():
            return bot, None
    except Exception:
        pass
    return bot, loop


def _normalize_channel_lookup(raw: str) -> str:
    channel = str(raw or "").strip().lower()
    if channel.startswith("#"):
        channel = channel[1:]
    return channel


def _strip_channel_name(name: str) -> str:
    cleaned = (name or "").lower().strip()
    cleaned = re.sub(r"^[^a-z0-9]+", "", cleaned)
    cleaned = re.sub(r"[-_]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _resolve_channel_in_guild(guild: Any, channel: str) -> Any:
    target_channel = None
    channel_lookup = _normalize_channel_lookup(channel)
    text_channels = list(getattr(guild, "text_channels", []) or [])

    if channel.isdigit():
        try:
            target_channel = guild.get_channel(int(channel))
        except Exception:
            target_channel = None

    if target_channel is None:
        for ch in text_channels:
            if (getattr(ch, "name", "") or "").lower() == channel_lookup:
                target_channel = ch
                break

    if target_channel is None:
        for ch in text_channels:
            ch_name_lower = (getattr(ch, "name", "") or "").lower()
            if channel_lookup and channel_lookup in ch_name_lower:
                target_channel = ch
                break

    if target_channel is None:
        for ch in text_channels:
            stripped = _strip_channel_name(getattr(ch, "name", "") or "")
            if stripped and (channel_lookup in stripped or stripped in channel_lookup):
                target_channel = ch
                break

    if target_channel is None:
        for ch in text_channels:
            stripped = _strip_channel_name(getattr(ch, "name", "") or "")
            if stripped == channel_lookup:
                target_channel = ch
                break

    return target_channel


async def _resolve_target_channel(bot: "EchoSpeakDiscordBot", channel: str, server: Optional[str] = None) -> tuple[Any, Any]:
    client = getattr(bot, "client", None)
    if client is None:
        return None, None

    guilds = []
    server_lookup = str(server or "").strip()
    if server_lookup:
        if server_lookup.isdigit():
            try:
                guild = client.get_guild(int(server_lookup))
            except Exception:
                guild = None
            if guild is not None:
                guilds = [guild]
        if not guilds:
            for guild in (getattr(client, "guilds", None) or []):
                if (getattr(guild, "name", "") or "").lower() == server_lookup.lower():
                    guilds = [guild]
                    break
    else:
        guilds = list(getattr(client, "guilds", None) or [])

    for guild in guilds:
        target_channel = _resolve_channel_in_guild(guild, channel)
        if target_channel is not None:
            return target_channel, guild
    return None, None


def queue_discord_dm(user_id: str | int, message: str) -> bool:
    try:
        bot, loop = _get_live_bot_and_loop()
        if bot is None:
            return False
        if loop is None:
            return False

        async def _send() -> None:
            target = await bot.client.fetch_user(int(user_id))
            if target is None:
                return
            dm = await target.create_dm()
            await dm.send(str(message or "")[:2000])

        asyncio.run_coroutine_threadsafe(_send(), loop)
        return True
    except Exception:
        return False


def queue_discord_channel_with_status(channel: str, message: str, server: Optional[str] = None) -> str:
    try:
        msg = str(message or "").strip()
        channel_name = str(channel or "").strip()
        if not msg or not channel_name:
            return "missing"

        bot, loop = _get_live_bot_and_loop()
        if bot is None or loop is None:
            return "unavailable"

        async def _send() -> str:
            target_channel, guild = await _resolve_target_channel(bot, channel_name, server)
            if target_channel is None or guild is None:
                logger.debug(
                    f"Discord changelog route: channel '{channel_name}' not found"
                    + (f" in server '{server}'" if server else " in any connected server")
                )
                return "missing"

            await bot._send_long_message(target_channel, msg)
            logger.info(
                f"Discord channel message sent to #{getattr(target_channel, 'name', '?')} on '{getattr(guild, 'name', '?')}'"
            )
            return "sent"

        fut = asyncio.run_coroutine_threadsafe(_send(), loop)
        return str(fut.result(timeout=30) or "error")
    except Exception as e:
        logger.debug(f"queue_discord_channel failed: {e}")
        return "error"


def queue_discord_channel(channel: str, message: str, server: Optional[str] = None) -> bool:
    return queue_discord_channel_with_status(channel, message, server=server) == "sent"


class EchoSpeakDiscordBot:
    """
    Discord bot that connects EchoSpeak to Discord servers.
    Responds to mentions and DMs by routing queries through EchoSpeak.
    """

    def __init__(self, token: str, process_query_func: Callable, agent_name: str = "EchoSpeak"):
        """
        Initialize the Discord bot.

        Args:
            token: Discord bot token
            process_query_func: Function to call for processing queries (agent.process_query)
            agent_name: Name to use for the bot in Discord
        """
        self.token = token
        self.process_query = process_query_func
        self.agent_name = agent_name
        self.client = None
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Task] = None

    def _normalized_discord_access_values(self, values: Any) -> set[str]:
        out: set[str] = set()
        if not isinstance(values, (list, tuple, set)):
            return out
        for value in values:
            raw = str(value or "").strip().lower()
            if raw:
                out.add(raw)
        return out

    def _discord_member_roles(self, author: Any) -> tuple[list[str], list[str], set[str]]:
        role_names: list[str] = []
        role_ids: list[str] = []
        role_tokens: set[str] = set()
        for role in (getattr(author, "roles", None) or []):
            role_name = str(getattr(role, "name", "") or "").strip()
            role_id = str(getattr(role, "id", "") or "").strip()
            if role_name and role_name != "@everyone":
                role_names.append(role_name)
                role_tokens.add(role_name.lower())
            if role_id:
                role_ids.append(role_id)
                role_tokens.add(role_id.lower())
        return role_names, role_ids, role_tokens

    async def _mutual_guild_member_roles(self, user_id: str) -> tuple[list[str], list[str], set[str]]:
        role_names: list[str] = []
        role_ids: list[str] = []
        role_tokens: set[str] = set()
        client = getattr(self, "client", None)
        if client is None:
            return role_names, role_ids, role_tokens
        try:
            user_id_int = int(str(user_id or "").strip())
        except Exception:
            return role_names, role_ids, role_tokens

        for guild in (getattr(client, "guilds", None) or []):
            member = None
            try:
                member = guild.get_member(user_id_int)
            except Exception:
                member = None
            if member is None:
                try:
                    member = await guild.fetch_member(user_id_int)
                except Exception:
                    continue
            guild_role_names, guild_role_ids, guild_role_tokens = self._discord_member_roles(member)
            for role_name in guild_role_names:
                if role_name not in role_names:
                    role_names.append(role_name)
            for role_id in guild_role_ids:
                if role_id not in role_ids:
                    role_ids.append(role_id)
            role_tokens |= guild_role_tokens
        return role_names, role_ids, role_tokens

    async def _invocation_access(self, author: Any, is_dm: bool) -> tuple[bool, str, list[str], list[str]]:
        user_id = str(getattr(author, "id", "") or "").strip()
        owner_id = str(getattr(config, "discord_bot_owner_id", "") or "").strip()
        trusted_ids = self._normalized_discord_access_values(getattr(config, "discord_bot_trusted_users", []))
        allowed_users = self._normalized_discord_access_values(getattr(config, "discord_bot_allowed_users", []))
        allowed_roles = self._normalized_discord_access_values(getattr(config, "discord_bot_allowed_roles", []))
        role_names, role_ids, role_tokens = self._discord_member_roles(author)

        if owner_id and user_id == owner_id:
            return True, "owner_id", role_names, role_ids
        if user_id and user_id.lower() in trusted_ids:
            return True, "trusted_user_id", role_names, role_ids
        if user_id and user_id.lower() in allowed_users:
            return True, "allowed_user_id", role_names, role_ids

        if is_dm:
            dm_role_names, dm_role_ids, dm_role_tokens = role_names, role_ids, role_tokens
            if allowed_roles and user_id:
                dm_role_names, dm_role_ids, dm_role_tokens = await self._mutual_guild_member_roles(user_id)
                if dm_role_tokens & allowed_roles:
                    return True, "verified_allowed_role_dm", dm_role_names, dm_role_ids
            if allowed_users or allowed_roles:
                return False, "dm_not_allowlisted", dm_role_names, dm_role_ids
            return True, "open_dm", dm_role_names, dm_role_ids

        if allowed_roles:
            if role_tokens & allowed_roles:
                return True, "allowed_role", role_names, role_ids
            return False, "missing_allowed_role", role_names, role_ids

        if allowed_users:
            return False, "user_not_allowlisted", role_names, role_ids

        return True, "open_server", role_names, role_ids

    class _DiscordToolCallbacks:
        """Lightweight callback adapter to surface tool progress into Discord."""

        def __init__(self, loop: asyncio.AbstractEventLoop, channel: "discord.abc.Messageable", source: str = "discord_bot"):
            self._loop = loop
            self._channel = channel
            self._source = str(source or "discord_bot")
            self._tool_started = False
            self._tools_used: set[str] = set()
            # LangChain/LangGraph callback managers may introspect these.
            self.ignore_chain = False
            self.raise_error = False
            self.ignore_llm = False
            self.ignore_chat_model = False
            self.ignore_agent = False

        def _send_async(self, text: str) -> None:
            try:
                # Schedule a send on the main event loop from a worker thread.
                self._loop.call_soon_threadsafe(asyncio.create_task, self._channel.send(text))
            except Exception:
                # Best-effort only; never let progress pings crash the agent.
                pass

        def on_tool_start(self, serialized: dict, input_str: str, run_id: str, parent_run_id: Optional[str] = None, **_: Any) -> None:
            try:
                name = (serialized or {}).get("name") or "tool"
                self._last_tool_name = str(name)
                self._tools_used.add(str(name))
                # Suppress noisy progress messages for low-signal internal tools.
                if str(name) in {"get_system_time"}:
                    return
                # Only send a short progress ping the first few times to avoid spam.
                if not self._tool_started:
                    self._tool_started = True
                    pretty = str(name).replace("_", " ")
                    self._send_async(f"Running {pretty} for you...")
            except Exception:
                pass

        def on_tool_end(self, output: str, run_id: str, parent_run_id: Optional[str] = None, **_: Any) -> None:
            # Broadcast tool activity to Web UI via gateway WebSocket (Fix 5)
            try:
                from api.server import broadcast_discord_event
                broadcast_discord_event({
                    "type": "discord_activity",
                    "tool": self._last_tool_name if hasattr(self, "_last_tool_name") else "unknown",
                    "source": self._source,
                    "at": __import__("time").time(),
                })
            except Exception:
                pass

        def on_tool_error(self, error: BaseException, run_id: str, parent_run_id: Optional[str] = None, **_: Any) -> None:
            try:
                self._send_async("I hit an issue while running a tool, but I'll still try to answer as best I can.")
            except Exception:
                pass

        def on_chain_start(self, serialized: dict, inputs: dict, run_id: str, parent_run_id: Optional[str] = None, **_: Any) -> None:
            return

        def on_chain_end(self, outputs: dict, run_id: str, parent_run_id: Optional[str] = None, **_: Any) -> None:
            return

        def on_chain_error(self, error: BaseException, run_id: str, parent_run_id: Optional[str] = None, **_: Any) -> None:
            return

        # Some LangChain/LangGraph runners expect chat-model / llm callbacks to exist.
        # Provide no-ops so missing hooks don't crash or spam logs.
        def on_chat_model_start(self, serialized: dict, messages: Any, run_id: str, parent_run_id: Optional[str] = None, **_: Any) -> None:
            return

        def on_chat_model_end(self, response: Any, run_id: str, parent_run_id: Optional[str] = None, **_: Any) -> None:
            return

        def on_llm_start(self, serialized: dict, prompts: Any, run_id: str, parent_run_id: Optional[str] = None, **_: Any) -> None:
            return

        def on_llm_new_token(self, token: str, run_id: Optional[str] = None, parent_run_id: Optional[str] = None, **_: Any) -> None:
            return

        def on_llm_end(self, response: Any, run_id: str, parent_run_id: Optional[str] = None, **_: Any) -> None:
            return

        def on_llm_error(self, error: BaseException, run_id: str, parent_run_id: Optional[str] = None, **_: Any) -> None:
            return

        def on_agent_action(self, action: Any, run_id: str, parent_run_id: Optional[str] = None, **_: Any) -> None:
            return

        def on_agent_finish(self, finish: Any, run_id: str, parent_run_id: Optional[str] = None, **_: Any) -> None:
            return

    async def start(self):
        """Start the Discord bot connection."""
        if self._running:
            logger.warning("Discord bot already running")
            return

        # Validate token before attempting connection
        if not self.token or len(self.token) < 50:
            logger.error(f"Discord bot token is invalid or too short (len={len(self.token) if self.token else 0})")
            raise ValueError("Invalid Discord bot token")

        logger.info(f"Discord bot token validation passed (len={len(self.token)})")

        try:
            import discord
            from discord.ext import commands

            # Set up intents - MUST match what's enabled in Discord Developer Portal
            intents = discord.Intents.default()
            intents.message_content = True  # REQUIRED - must also enable in Dev Portal
            intents.messages = True
            intents.guilds = True
            intents.dm_messages = True

            logger.info(f"Discord intents configured: message_content={intents.message_content}")

            # Create bot with command prefix (but we'll mainly use mentions)
            self.client = commands.Bot(
                command_prefix="!",
                intents=intents,
                help_command=None
            )

            @self.client.event
            async def on_ready():
                logger.info(f"Discord bot logged in as {self.client.user} (ID: {self.client.user.id})")
                try:
                    # Log a brief summary of enabled intents to help debug "online but silent" issues.
                    intents = getattr(self.client, "intents", None)
                    if intents is not None:
                        logger.info(
                            f"Discord intents: messages={getattr(intents, 'messages', None)} dm_messages={getattr(intents, 'dm_messages', None)} message_content={getattr(intents, 'message_content', None)} guilds={getattr(intents, 'guilds', None)}"
                        )
                except Exception:
                    pass
                # Log whitelist configuration
                allowed_ids = getattr(config, "discord_bot_allowed_users", [])
                allowed_roles = getattr(config, "discord_bot_allowed_roles", [])
                logger.info(f"Discord ALLOWED_USERS whitelist: {allowed_ids}")
                logger.info(f"Discord ALLOWED_ROLES gate: {allowed_roles}")
                if not allowed_ids and not allowed_roles:
                    logger.info("Discord access gate is OPEN - bot will respond to all reachable users")
                else:
                    logger.info(
                        f"Discord bot invocation is restricted by user IDs={allowed_ids} and server roles={allowed_roles}"
                    )
                logger.info(
                    "IMPORTANT: If bot is online but silent, ensure 'Message Content Intent' is ENABLED in Discord Developer Portal > Bot > Privileged Gateway Intents"
                )
                self._running = True
                # Store the event loop reference for tools to use
                self._loop = asyncio.get_running_loop()

            @self.client.event
            async def on_message(message: discord.Message):
                try:
                    # Minimal receipt proof (stdout is what you've been watching)
                    print(
                        f"[DISCORD RAW] message received: {message.content[:100] if message.content else '(empty)'} from {message.author}",
                        flush=True,
                    )

                    # If on_ready hasn't fired yet, don't attempt mention logic.
                    if self.client.user is None:
                        logger.error("Discord on_message: client.user is None (bot not ready)")
                        return

                    # Ignore own messages
                    if message.author == self.client.user:
                        return

                    raw_content = (getattr(message, "content", "") or "").strip()
                    low_content = raw_content.lower()

                    # DM detection: safest is guild==None (works for DMs, group DMs)
                    is_dm = getattr(message, "guild", None) is None

                    # Mention detection: guard message.mentions (can be missing/None in some cases)
                    mentions = getattr(message, "mentions", None) or []
                    bot_id = getattr(self.client.user, "id", None)
                    is_mentioned = False
                    if bot_id is not None:
                        try:
                            is_mentioned = any(getattr(m, "id", None) == bot_id for m in mentions)
                        except Exception:
                            is_mentioned = False

                    wake_phrases = [
                        "hey echo",
                        f"hey {self.agent_name.lower()}",
                    ]
                    uses_wake_phrase = any(low_content.startswith(p) for p in wake_phrases)

                    if is_mentioned or is_dm or uses_wake_phrase:
                        try:
                            access_ok, access_reason, role_names, role_ids = await self._invocation_access(message.author, is_dm=is_dm)
                            if not access_ok:
                                logger.info(
                                    "Discord access denied for %s (ID:%s, source=%s, reason=%s, roles=%s)",
                                    getattr(message.author, "display_name", None) or getattr(message.author, "name", "unknown"),
                                    str(getattr(message.author, "id", "") or ""),
                                    "DM" if is_dm else "server",
                                    access_reason,
                                    role_names,
                                )
                                return

                            # Strip mention / wake phrase
                            content = raw_content
                            if is_mentioned:
                                content = content.replace(f"<@{self.client.user.id}>", "").strip()
                                content = content.replace(f"<@!{self.client.user.id}>", "").strip()
                            elif uses_wake_phrase:
                                for p in wake_phrases:
                                    if low_content.startswith(p):
                                        content = raw_content[len(p) :].lstrip(" ,:").strip()
                                        break

                            if not content:
                                await message.channel.send("Hey! How can I help you?")
                                return

                            # ── DM Command Interception ──
                            # Catch approve/reject commands before they hit
                            # process_query() so tweet approvals actually work.
                            # Handles: "approve", "/reject", "reject the tweet",
                            #          "can you reject the echospeak tweet", etc.
                            import re as _re
                            _cmd = content.strip().lower().lstrip("/")
                            _is_tweet_action = (
                                _cmd in {"approve", "reject", "approve tweet", "reject tweet"}
                                or bool(_re.search(r"\b(approve|reject)\b.*\btweet\b", _cmd))
                                or bool(_re.search(r"\btweet\b.*\b(approve|reject)\b", _cmd))
                            )
                            if _is_tweet_action:
                                _is_approve = "approve" in _cmd
                                try:
                                    from twitter_bot import get_twitter_bot
                                    tw_bot = get_twitter_bot()
                                    if tw_bot and getattr(tw_bot, "is_running", False):
                                        if _is_approve:
                                            result = tw_bot.approve_pending_tweet()
                                        else:
                                            result = tw_bot.reject_pending_tweet()
                                        ok = result.get("ok", False) if isinstance(result, dict) else False
                                        if ok:
                                            action = "approved and posted" if _is_approve else "rejected"
                                            await message.channel.send(f"Done — tweet {action}.")
                                        else:
                                            err = result.get("error", "unknown error") if isinstance(result, dict) else "unknown error"
                                            await message.channel.send(f"No pending tweet to {'approve' if _is_approve else 'reject'}. {err}")
                                    else:
                                        await message.channel.send("Twitter bot isn't running right now.")
                                except ImportError:
                                    await message.channel.send("Twitter module isn't available.")
                                except Exception as _tw_exc:
                                    await message.channel.send(f"Tweet action failed: {_tw_exc}")
                                return

                            async with message.channel.typing():
                                channel_ctx = await self._maybe_get_channel_context(message, content)
                                followup_ctx = await self._maybe_get_followup_context(message, content)

                                if channel_ctx and followup_ctx:
                                    content_for_agent = (
                                        f"{channel_ctx}\n\n{followup_ctx}\n\nUser request: {content}".strip()
                                    )
                                elif channel_ctx:
                                    content_for_agent = f"{channel_ctx}\n\nUser request: {content}".strip()
                                elif followup_ctx:
                                    content_for_agent = f"{followup_ctx}\n\nUser request: {content}".strip()
                                else:
                                    content_for_agent = f"User request: {content}".strip()

                                callbacks: list[Any] = []
                                cb: Optional["EchoSpeakDiscordBot._DiscordToolCallbacks"] = None
                                if getattr(self, "_loop", None) is not None:
                                    try:
                                        if isinstance(message.channel, discord.abc.Messageable):
                                            cb = self._DiscordToolCallbacks(self._loop, message.channel, "discord_bot_dm" if is_dm else "discord_bot")
                                            callbacks.append(cb)
                                    except Exception:
                                        cb = None

                                import asyncio

                                loop = asyncio.get_event_loop()
                                used_tools = False

                                discord_user_info = {
                                    "user_id": str(getattr(message.author, "id", "")),
                                    "username": str(getattr(message.author, "name", "")),
                                    "display_name": str(getattr(message.author, "display_name", "") or getattr(message.author, "name", "")),
                                    "is_dm": is_dm,
                                    "channel_id": str(getattr(message.channel, "id", "")),
                                    "guild_id": str(getattr(message.guild, "id", "")) if getattr(message, "guild", None) else "",
                                    "role_names": role_names,
                                    "role_ids": role_ids,
                                    "access_reason": access_reason,
                                }

                                # Resolve role for security audit trail
                                _resolved_role_value = "public"
                                try:
                                    from config import DiscordUserRole, config as _cfg
                                    _uid = discord_user_info["user_id"]
                                    _owner = str(getattr(_cfg, "discord_bot_owner_id", "") or "").strip()
                                    _trusted = {str(x).strip() for x in (getattr(_cfg, "discord_bot_trusted_users", []) or []) if str(x).strip()}
                                    if _owner and _uid == _owner:
                                        _resolved_role = DiscordUserRole.OWNER
                                    elif _uid in _trusted:
                                        _resolved_role = DiscordUserRole.TRUSTED
                                    else:
                                        _resolved_role = DiscordUserRole.PUBLIC
                                    _resolved_role_value = _resolved_role.value
                                    logger.info(
                                        f"Discord user {discord_user_info['display_name']} "
                                        f"(ID:{_uid}) resolved to role={_resolved_role_value} "
                                        f"(source={'DM' if is_dm else 'server'})"
                                    )
                                except Exception:
                                    pass

                                # ── Security Gate 1: Prompt Injection Screening ──
                                # Runs BEFORE rate limiting so blocked messages
                                # don't waste the user's rate-limit slots.
                                try:
                                    from agent.security import screen_for_injection, log_security_event, notify_owner_security_event
                                    _inj = screen_for_injection(content, _resolved_role_value)
                                    if _inj.is_suspicious:
                                        _event = log_security_event(
                                            "prompt_injection_detected",
                                            user_id=discord_user_info["user_id"],
                                            username=discord_user_info.get("display_name", ""),
                                            role=_resolved_role_value,
                                            source="discord_bot_dm" if is_dm else "discord_bot",
                                            severity=_inj.severity,
                                            details={
                                                "matched": _inj.matched_patterns,
                                                "input_preview": content[:200],
                                                "blocked": _inj.should_block,
                                            },
                                        )
                                        notify_owner_security_event(_event)
                                        if _inj.should_block:
                                            await message.channel.send(
                                                "I can't process that request. "
                                                "If you think this is a mistake, try rephrasing."
                                            )
                                            return
                                except Exception as _inj_exc:
                                    logger.debug(f"Injection screening skipped: {_inj_exc}")

                                # ── Security Gate 2: Rate Limiting ──
                                # Only counted AFTER injection check passes so
                                # blocked messages don't penalise the user.
                                try:
                                    from agent.security import check_rate_limit, log_security_event, notify_owner_security_event
                                    _rl_ok, _rl_msg = check_rate_limit(
                                        discord_user_info["user_id"], _resolved_role_value,
                                    )
                                    if not _rl_ok:
                                        log_security_event(
                                            "rate_limit_hit",
                                            user_id=discord_user_info["user_id"],
                                            username=discord_user_info.get("display_name", ""),
                                            role=_resolved_role_value,
                                            source="discord_bot_dm" if is_dm else "discord_bot",
                                            severity="medium",
                                        )
                                        await message.channel.send(_rl_msg)
                                        return
                                except Exception as _rl_exc:
                                    logger.debug(f"Rate limit check skipped: {_rl_exc}")

                                _msg_start_ts = __import__("time").time()

                                def _run_agent():
                                    nonlocal used_tools
                                    resp, ok = self.process_query(
                                        user_input=content_for_agent,
                                        include_memory=True,
                                        callbacks=callbacks or None,
                                        thread_id=f"discord_{message.channel.id}_{message.author.id}",
                                        source=("discord_bot_dm" if is_dm else "discord_bot"),
                                        discord_user_info=discord_user_info,
                                    )
                                    try:
                                        if cb is not None and getattr(cb, "_tools_used", None):
                                            used_tools = bool(cb._tools_used)  # type: ignore[attr-defined]
                                    except Exception:
                                        used_tools = False
                                    return resp, ok

                                response, _ = await loop.run_in_executor(None, _run_agent)
                                _msg_elapsed = __import__("time").time() - _msg_start_ts
                                logger.info(
                                    f"[DISCORD PERF] user={discord_user_info.get('display_name','?')} "
                                    f"role={_resolved_role_value} elapsed={_msg_elapsed:.1f}s "
                                    f"response_len={len(response or '')} content={content[:60]!r}"
                                )
                                if not response or not str(response).strip():
                                    logger.warning(
                                        f"[DISCORD] Empty response for content={content[:80]!r} "
                                        f"user={discord_user_info.get('display_name','?')}"
                                    )
                                    response = "Hmm, I couldn't come up with a response. Try asking again."
                                await self._send_long_message(message.channel, response)
                        except Exception as e:
                            logger.exception("Discord message handling failed", exc_info=e)
                            try:
                                await message.channel.send("I hit an error handling that message.")
                            except Exception:
                                pass
                except Exception as e:
                    logger.exception("Discord on_message crashed", exc_info=e)
                    try:
                        await message.channel.send("I hit an error handling that message.")
                    except Exception:
                        pass
                finally:
                    # Ensure discord.py command processing continues to work
                    try:
                        await self.client.process_commands(message)
                    except Exception:
                        pass

            # Start the bot
            logger.info("Starting Discord bot connection...")
            await self.client.start(self.token)

        except ImportError:
            logger.error("discord.py not installed. Run: pip install discord.py")
            raise
        except Exception as e:
            logger.error(f"Failed to start Discord bot: {e}")
            raise

    async def _send_long_message(self, channel, content: str, max_length: int = 2000):
        """Send a message, safely chunking it if it exceeds Discord's character limit."""
        if not content:
            return

        if len(content) <= max_length:
            await channel.send(content)
            return

        # Split by paragraphs first
        paragraphs = content.split("\n\n")
        current_msg = ""

        for para in paragraphs:
            # If a single paragraph is too large on its own
            if len(para) > max_length:
                # Flush whatever we have so far
                if current_msg:
                    await channel.send(current_msg.strip())
                    current_msg = ""
                    
                # Chunk this massive paragraph
                while len(para) > max_length:
                    # Find a good break point (space or punctuation) near max_length
                    break_idx = para.rfind(" ", 0, max_length)
                    if break_idx <= 0:
                        break_idx = max_length # Hard break if no spaces found
                    
                    chunk = para[:break_idx].strip()
                    if chunk:
                        await channel.send(chunk)
                    para = para[break_idx:].lstrip()
                
                if para.strip():
                    current_msg = para.strip()
                continue

            # Normal paragraph that fits in a message
            if current_msg and len(current_msg) + len(para) + 2 <= max_length:
                current_msg += "\n\n" + para
            elif not current_msg and len(para) <= max_length:
                current_msg = para
            else:
                if current_msg:
                    await channel.send(current_msg.strip())
                current_msg = para

        if current_msg:
            await channel.send(current_msg.strip())

    async def _maybe_get_channel_context(self, message, user_text: str) -> str:
        """Fetch recent channel messages when user is asking what people are saying."""
        try:
            import re

            if not getattr(message, "guild", None):
                return ""  # Not a server message

            low = (user_text or "").lower()
            wants_context = any(
                p in low
                for p in [
                    "what are people saying",
                    "what's everyone saying",
                    "what are they saying",
                    "catch me up",
                    "summarize",
                    "recap",
                    "check the server",
                    "check discord",
                    "read the channel",
                ]
            )
            if not wants_context:
                return ""

            # In shared server mode, only use the current channel for recap context.
            # Broader cross-channel read/post behavior is intentionally reserved for
            # the Web UI or direct-message owner workflows.
            target = message.channel

            if not hasattr(target, "history"):
                return ""

            lines = []
            max_messages = 25
            max_chars = 1500
            async for msg in target.history(limit=max_messages, oldest_first=False):
                if getattr(msg, "author", None) is None:
                    continue
                if getattr(msg.author, "bot", False):
                    continue
                text = (msg.content or "").strip()
                if not text:
                    continue
                author = getattr(msg.author, "display_name", None) or getattr(msg.author, "name", "unknown")
                # Basic cleanup
                text = re.sub(r"\s+", " ", text)
                lines.append(f"- {author}: {text}")
                if sum(len(x) for x in lines) > max_chars:
                    break

            if not lines:
                return f"Recent messages in #{getattr(target, 'name', 'channel')}: (no recent messages found)"

            chan_name = getattr(target, "name", "channel")
            return f"Recent messages in #{chan_name} (most recent first):\n" + "\n".join(lines)
        except Exception as e:
            logger.info(f"Discord channel context fetch skipped: {e}")
            return ""

    def _is_smalltalk_message(self, user_text: str) -> bool:
        try:
            import re

            text = re.sub(r"\s+", " ", str(user_text or "").strip().lower())
            if not text:
                return False
            patterns = [
                r"^(?:yo|hi|hey|hello|sup|what(?:'s|s) up|good morning|good night|later|bye|goodbye|cya|gn|night)\s*[!.?]*$",
                r"^what(?:\s+are|\s*'re|\s*re)?\s+you\s+up\s+to(?:\s+today)?\s*[!.?]*$",
                r"^what(?:\s+are|\s*'re|\s*re)?\s+you\s+doing(?:\s+today)?\s*[!.?]*$",
                r"^wyd(?:\s+today)?\s*[!.?]*$",
            ]
            return any(re.fullmatch(pattern, text) is not None for pattern in patterns)
        except Exception:
            return False

    async def _maybe_get_followup_context(self, message, user_text: str) -> str:
        """Fetch a small slice of recent bot+user conversation when the message looks like a follow-up.

        This helps resolve references like "that", "everything", "what do you think?" that depend
        on the immediately prior bot response in the channel.
        """
        try:
            import re

            text = (user_text or "").strip()
            low = text.lower()
            if not text:
                return ""
            if self._is_smalltalk_message(text):
                return ""

            followup_markers = [
                "everything",
                "all that",
                "that",
                "this",
                "what about",
                "how about",
                "your honest opinion",
                "your opinion",
                "thoughts",
                "what do you think",
                "wtf",
            ]

            # Implicit continuation phrases — the user is clearly
            # asking to modify / continue the previous answer even
            # though no explicit pronoun is used.
            implicit_followup_phrases = [
                "simpler version",
                "shorter version",
                "longer version",
                "simple version",
                "make it simpler",
                "make it shorter",
                "make it longer",
                "give me a simpler",
                "give me a shorter",
                "give me a longer",
                "give me an example",
                "give me another",
                "give me a different",
                "give an example",
                "now explain",
                "now give",
                "now show",
                "now tell",
                "more detail",
                "more info",
                "elaborate",
                "expand on",
                "break it down",
                "dumb it down",
                "eli5",
                "in plain english",
                "in simpler terms",
                "can you clarify",
                "what do you mean",
                "say that again",
                "one more time",
                "try again",
                "rephrase",
            ]

            has_marker = any(m in low for m in followup_markers)
            has_implicit_followup = any(p in low for p in implicit_followup_phrases)
            has_deictic_reference = re.search(
                r"\b(it|that|this|everything|all of it|them|him|her|his|hers|their|he|she|they|those|these|we)\b", low
            ) is not None
            starts_with_joiner = re.search(r"^(and|but|so|also|then|now|because|cause|cuz|okay|ok|well|wait)\b", low) is not None
            refers_to_prior_action = re.search(r"\b(?:why|how come)\s+(?:did\s+)?(?:you|u)\s+(?:leave|left|go|went|stop|disappear)\b", low) is not None
            looks_like_followup = (
                has_marker
                or has_implicit_followup
                or refers_to_prior_action
                or (len(text) <= 80 and (has_deictic_reference or starts_with_joiner))
            )
            if not looks_like_followup:
                return ""

            if not hasattr(message.channel, "history"):
                return ""

            def _msg_looks_like_followup(msg_text: str) -> bool:
                """Check if a user message is a follow-up vs. a new standalone topic."""
                _low = re.sub(r"<@\d+>\s*", "", (msg_text or "").lower()).strip()
                if not _low or len(_low) <= 12:
                    return True  # very short → likely follow-up
                if re.search(r"\b(it|that|this|them|him|her|his|he|she|they|those|these|we)\b", _low):
                    return True
                if re.search(r"^(and|but|so|also|then|now|because|cause|cuz|okay|ok|well|wait)\b", _low):
                    return True
                _fup_words = [
                    "simpler", "shorter", "longer", "example", "more detail",
                    "elaborate", "expand", "another", "different", "again",
                    "rephrase", "clarify", "version", "break it down",
                    "dumb it down", "eli5", "in plain english",
                ]
                if any(w in _low for w in _fup_words):
                    return True
                return False

            # Collect the last few messages between the user and this bot,
            # stopping at topic boundaries to avoid mixing contexts.
            lines = []
            max_messages = 12
            max_chars = 1200
            _user_msg_count = 0
            async for msg in message.channel.history(limit=max_messages, oldest_first=False):
                try:
                    if getattr(msg, "id", None) == getattr(message, "id", None):
                        continue
                except Exception:
                    pass
                author = getattr(msg, "author", None)
                if author is None:
                    continue
                # Only include the current user and the bot.
                is_bot_author = False
                try:
                    is_bot_author = bool(getattr(self.client, "user", None) and author == self.client.user)
                except Exception:
                    is_bot_author = False
                is_user_author = False
                try:
                    is_user_author = bool(getattr(message, "author", None) and author == message.author)
                except Exception:
                    is_user_author = False
                if not (is_bot_author or is_user_author):
                    continue

                msg_text = (getattr(msg, "content", "") or "").strip()
                if not msg_text:
                    continue
                msg_text = re.sub(r"\s+", " ", msg_text)
                label = "EchoSpeak" if is_bot_author else "User"

                # Topic-boundary detection: once we have at least one
                # exchange pair, a user message that looks like a new
                # standalone question marks the topic start — include it
                # and stop so we don't bleed in older unrelated topics.
                if is_user_author:
                    _user_msg_count += 1
                    if _user_msg_count >= 2 and not _msg_looks_like_followup(msg_text):
                        lines.append(f"User: {msg_text}")
                        break

                lines.append(f"{label}: {msg_text}")
                if sum(len(x) for x in lines) > max_chars:
                    break

            if not lines:
                return ""

            # Reverse to chronological order.
            lines = list(reversed(lines))
            return "Recent conversation context:\n" + "\n".join(lines)
        except Exception as e:
            logger.info(f"Discord follow-up context fetch skipped: {e}")
            return ""

    async def stop(self):
        """Stop the Discord bot."""
        if self.client and self._running:
            logger.info("Stopping Discord bot...")
            await self.client.close()
            self._running = False

    def is_running(self) -> bool:
        """Check if the bot is currently running."""
        return self._running

    def get_loop(self):
        """Get the event loop the bot is running on."""
        return getattr(self, "_loop", None)


async def start_discord_bot(token: str, process_query_func: Callable, agent_name: str = "EchoSpeak") -> EchoSpeakDiscordBot:
    """
    Start the Discord bot in a background task.

    Args:
        token: Discord bot token
        process_query_func: Agent's process_query method
        agent_name: Bot display name

    Returns:
        The bot instance
    """
    global _bot_instance

    if _bot_instance and _bot_instance.is_running():
        logger.warning("Discord bot already running")
        return _bot_instance

    _bot_instance = EchoSpeakDiscordBot(token, process_query_func, agent_name)

    # Start in background
    task = asyncio.create_task(_bot_instance.start())
    _bot_instance._task = task

    def _on_done(t: asyncio.Task) -> None:
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            logger.info("Discord bot task was cancelled")
            return
        except Exception as e:
            logger.error(f"Discord bot task error inspection failed: {e}")
            return
        if exc is not None:
            logger.exception("Discord bot background task crashed", exc_info=exc)

    try:
        task.add_done_callback(_on_done)
    except Exception:
        pass

    return _bot_instance


async def stop_discord_bot():
    """Stop the Discord bot if running."""
    global _bot_instance

    if _bot_instance:
        await _bot_instance.stop()
        _bot_instance = None
