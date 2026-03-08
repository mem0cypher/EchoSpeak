"""
Twitch integration for EchoSpeak (v6.7.0).

Provides:
  - EventSub webhook handling (chat messages, stream online/offline)
  - Helix API for sending chat messages
  - App Access Token management
  - HMAC signature verification for webhook security
"""

import hashlib
import hmac
import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from loguru import logger


# ============================================================================
# CONSTANTS
# ============================================================================

TWITCH_HELIX_BASE = "https://api.twitch.tv/helix"
TWITCH_AUTH_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_EVENTSUB_URL = f"{TWITCH_HELIX_BASE}/eventsub/subscriptions"

# EventSub message types
EVENTSUB_MSG_NOTIFICATION = "notification"
EVENTSUB_MSG_VERIFICATION = "webhook_callback_verification"
EVENTSUB_MSG_REVOCATION = "revocation"


# ============================================================================
# TOKEN MANAGEMENT
# ============================================================================

class TwitchTokenManager:
    """Manages Twitch App Access Tokens with automatic refresh."""

    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token: str = ""
        self._expires_at: float = 0.0
        self._lock = threading.Lock()

    @property
    def client_id(self) -> str:
        return self._client_id

    def get_token(self) -> str:
        """Get a valid App Access Token, refreshing if needed."""
        with self._lock:
            if self._access_token and time.time() < self._expires_at - 60:
                return self._access_token
        return self._refresh_token()

    def _refresh_token(self) -> str:
        """Request a new App Access Token via client credentials."""
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(TWITCH_AUTH_URL, data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                })
                resp.raise_for_status()
                data = resp.json()
            with self._lock:
                self._access_token = data["access_token"]
                self._expires_at = time.time() + data.get("expires_in", 3600)
            logger.info(f"Twitch App Access Token refreshed, expires in {data.get('expires_in', '?')}s")
            return self._access_token
        except Exception as e:
            logger.error(f"Failed to refresh Twitch App Access Token: {e}")
            return self._access_token

    def helix_headers(self) -> Dict[str, str]:
        """Return headers for Helix API calls."""
        return {
            "Authorization": f"Bearer {self.get_token()}",
            "Client-Id": self._client_id,
            "Content-Type": "application/json",
        }


# ============================================================================
# HELIX API HELPERS
# ============================================================================

class TwitchHelixAPI:
    """Thin wrapper around the Twitch Helix API."""

    def __init__(self, token_manager: TwitchTokenManager):
        self._tm = token_manager

    def send_chat_message(
        self,
        broadcaster_id: str,
        sender_id: str,
        message: str,
        *,
        sender_access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a chat message to a channel via Helix.

        The sender_access_token must be a User Access Token with
        user:write:chat scope for the bot user.
        """
        url = f"{TWITCH_HELIX_BASE}/chat/messages"
        payload = {
            "broadcaster_id": broadcaster_id,
            "sender_id": sender_id,
            "message": message[:500],  # Twitch chat limit
        }
        headers = {
            "Client-Id": self._tm.client_id,
            "Content-Type": "application/json",
        }
        if sender_access_token:
            headers["Authorization"] = f"Bearer {sender_access_token}"
        else:
            headers["Authorization"] = f"Bearer {self._tm.get_token()}"

        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"Twitch send_chat_message failed: {e}")
            return {"error": str(e)}

    def get_channel_info(self, broadcaster_id: str) -> Dict[str, Any]:
        """Get channel information."""
        url = f"{TWITCH_HELIX_BASE}/channels?broadcaster_id={broadcaster_id}"
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(url, headers=self._tm.helix_headers())
                resp.raise_for_status()
                data = resp.json()
                return data.get("data", [{}])[0] if data.get("data") else {}
        except Exception as e:
            logger.error(f"Twitch get_channel_info failed: {e}")
            return {"error": str(e)}

    def get_stream_info(self, broadcaster_id: str) -> Optional[Dict[str, Any]]:
        """Get live stream info, or None if offline."""
        url = f"{TWITCH_HELIX_BASE}/streams?user_id={broadcaster_id}"
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(url, headers=self._tm.helix_headers())
                resp.raise_for_status()
                data = resp.json()
                streams = data.get("data", [])
                return streams[0] if streams else None
        except Exception as e:
            logger.error(f"Twitch get_stream_info failed: {e}")
            return None


# ============================================================================
# EVENTSUB WEBHOOK VERIFICATION
# ============================================================================

def verify_eventsub_signature(
    secret: str,
    message_id: str,
    timestamp: str,
    body: bytes,
    expected_signature: str,
) -> bool:
    """Verify Twitch EventSub webhook HMAC signature.

    Twitch signs: HMAC-SHA256(secret, message_id + timestamp + body)
    Header format: sha256=<hex>
    """
    if not secret or not expected_signature:
        return False
    hmac_message = message_id.encode() + timestamp.encode() + body
    computed = "sha256=" + hmac.new(
        secret.encode(), hmac_message, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, expected_signature)


# ============================================================================
# EVENTSUB SUBSCRIPTION MANAGEMENT
# ============================================================================

def create_eventsub_subscription(
    token_manager: TwitchTokenManager,
    sub_type: str,
    version: str,
    condition: Dict[str, str],
    callback_url: str,
    secret: str,
) -> Dict[str, Any]:
    """Create an EventSub webhook subscription."""
    payload = {
        "type": sub_type,
        "version": version,
        "condition": condition,
        "transport": {
            "method": "webhook",
            "callback": callback_url,
            "secret": secret,
        },
    }
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                TWITCH_EVENTSUB_URL,
                json=payload,
                headers=token_manager.helix_headers(),
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"EventSub subscription created: type={sub_type}")
            return result
    except Exception as e:
        logger.error(f"Failed to create EventSub subscription ({sub_type}): {e}")
        return {"error": str(e)}


def list_eventsub_subscriptions(token_manager: TwitchTokenManager) -> list:
    """List all active EventSub subscriptions."""
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                TWITCH_EVENTSUB_URL,
                headers=token_manager.helix_headers(),
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
    except Exception as e:
        logger.error(f"Failed to list EventSub subscriptions: {e}")
        return []


def delete_eventsub_subscription(
    token_manager: TwitchTokenManager, subscription_id: str
) -> bool:
    """Delete an EventSub subscription by ID."""
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.delete(
                f"{TWITCH_EVENTSUB_URL}?id={subscription_id}",
                headers=token_manager.helix_headers(),
            )
            return resp.status_code == 204
    except Exception as e:
        logger.error(f"Failed to delete EventSub subscription: {e}")
        return False


# ============================================================================
# MAIN TWITCH BOT CLASS
# ============================================================================

class EchoSpeakTwitchBot:
    """Manages the Twitch integration lifecycle.

    - Registers EventSub subscriptions on startup
    - Processes incoming EventSub notifications (chat messages, stream events)
    - Routes chat messages through the EchoSpeak agent pipeline
    - Sends replies via Helix chat API
    """

    def __init__(self):
        from config import config

        self._config = config
        self._token_manager: Optional[TwitchTokenManager] = None
        self._helix: Optional[TwitchHelixAPI] = None
        self._running = False
        self._stream_online = False
        self._agent = None  # Set externally after agent init

    def set_agent(self, agent: Any) -> None:
        self._agent = agent

    @property
    def is_enabled(self) -> bool:
        return bool(
            getattr(self._config, "allow_twitch", False)
            and getattr(self._config, "twitch_client_id", "")
            and getattr(self._config, "twitch_client_secret", "")
        )

    async def start(self) -> None:
        """Initialize token manager, Helix API, and register EventSub subscriptions."""
        if not self.is_enabled:
            logger.info("Twitch integration disabled or missing credentials")
            return

        self._token_manager = TwitchTokenManager(
            self._config.twitch_client_id,
            self._config.twitch_client_secret,
        )
        self._helix = TwitchHelixAPI(self._token_manager)
        self._running = True

        # Register EventSub subscriptions if callback URL is configured
        callback = (self._config.twitch_eventsub_callback_url or "").strip()
        secret = (self._config.twitch_eventsub_secret or "").strip()
        broadcaster = (self._config.twitch_broadcaster_id or "").strip()

        if callback and secret and broadcaster:
            self._register_subscriptions(broadcaster, callback, secret)
        else:
            logger.warning(
                "Twitch EventSub not fully configured — "
                "need TWITCH_EVENTSUB_CALLBACK_URL, TWITCH_EVENTSUB_SECRET, "
                "and TWITCH_BROADCASTER_ID"
            )

        logger.info("Twitch bot started")

    def _register_subscriptions(self, broadcaster_id: str, callback: str, secret: str) -> None:
        """Register the core EventSub subscriptions."""
        bot_user_id = (self._config.twitch_bot_user_id or "").strip()

        # channel.chat.message — requires user:read:chat on the bot + channel:bot
        if bot_user_id:
            create_eventsub_subscription(
                self._token_manager,
                sub_type="channel.chat.message",
                version="1",
                condition={
                    "broadcaster_user_id": broadcaster_id,
                    "user_id": bot_user_id,
                },
                callback_url=callback,
                secret=secret,
            )

        # stream.online
        create_eventsub_subscription(
            self._token_manager,
            sub_type="stream.online",
            version="1",
            condition={"broadcaster_user_id": broadcaster_id},
            callback_url=callback,
            secret=secret,
        )

        # stream.offline
        create_eventsub_subscription(
            self._token_manager,
            sub_type="stream.offline",
            version="1",
            condition={"broadcaster_user_id": broadcaster_id},
            callback_url=callback,
            secret=secret,
        )

    async def handle_eventsub_webhook(
        self,
        headers: Dict[str, str],
        body: bytes,
    ) -> Dict[str, Any]:
        """Process an incoming EventSub webhook request.

        Returns a dict with:
          - "challenge": str if this is a verification request
          - "ok": True if processed successfully
          - "error": str if verification failed
        """
        msg_id = headers.get("twitch-eventsub-message-id", "")
        msg_timestamp = headers.get("twitch-eventsub-message-timestamp", "")
        msg_signature = headers.get("twitch-eventsub-message-signature", "")
        msg_type = headers.get("twitch-eventsub-message-type", "")

        secret = (self._config.twitch_eventsub_secret or "").strip()

        # Verify HMAC signature
        if not verify_eventsub_signature(secret, msg_id, msg_timestamp, body, msg_signature):
            logger.warning(f"Twitch EventSub signature verification failed (msg_id={msg_id})")
            return {"error": "signature_invalid"}

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return {"error": "invalid_json"}

        # Handle verification challenge
        if msg_type == EVENTSUB_MSG_VERIFICATION:
            challenge = payload.get("challenge", "")
            logger.info(f"Twitch EventSub verification challenge received")
            return {"challenge": challenge}

        # Handle revocation
        if msg_type == EVENTSUB_MSG_REVOCATION:
            sub = payload.get("subscription", {})
            logger.warning(
                f"Twitch EventSub subscription revoked: "
                f"type={sub.get('type')}, status={sub.get('status')}"
            )
            return {"ok": True}

        # Handle notification
        if msg_type == EVENTSUB_MSG_NOTIFICATION:
            sub_type = payload.get("subscription", {}).get("type", "")
            event = payload.get("event", {})

            if sub_type == "channel.chat.message":
                await self._handle_chat_message(event)
            elif sub_type == "stream.online":
                await self._handle_stream_online(event)
            elif sub_type == "stream.offline":
                await self._handle_stream_offline(event)
            else:
                logger.info(f"Twitch EventSub unhandled type: {sub_type}")

            return {"ok": True}

        return {"error": f"unknown_message_type: {msg_type}"}

    async def _handle_chat_message(self, event: Dict[str, Any]) -> None:
        """Process an incoming Twitch chat message through the agent pipeline."""
        chatter_name = event.get("chatter_user_name", "")
        chatter_id = event.get("chatter_user_id", "")
        message_text = event.get("message", {}).get("text", "").strip()
        broadcaster_id = event.get("broadcaster_user_id", "")
        broadcaster_name = event.get("broadcaster_user_name", "")

        if not message_text:
            return

        bot_user_id = (self._config.twitch_bot_user_id or "").strip()
        if chatter_id == bot_user_id:
            return  # Don't respond to own messages

        logger.info(
            f"[TWITCH CHAT] {chatter_name}: {message_text[:100]}"
        )

        # Only respond if chat reply is enabled
        if not self._config.twitch_chat_reply_enabled:
            return

        # Check if the message mentions the bot or is a command
        bot_name = (self._config.twitch_bot_username or "").strip().lower()
        msg_lower = message_text.lower()
        is_directed = (
            bot_name and bot_name in msg_lower
            or message_text.startswith("!")
        )

        if not is_directed:
            return

        # Route through agent pipeline
        if self._agent is None:
            logger.warning("Twitch chat: agent not initialized")
            return

        try:
            import asyncio

            loop = asyncio.get_event_loop()
            twitch_user_info = {
                "user_id": chatter_id,
                "username": chatter_name,
                "display_name": chatter_name,
                "platform": "twitch",
                "channel": broadcaster_name,
            }

            content = message_text
            if bot_name and content.lower().startswith(f"@{bot_name}"):
                content = content[len(bot_name) + 1:].strip()
            elif content.startswith("!"):
                content = content[1:].strip()

            if not content:
                return

            def _run_agent():
                resp, ok = self._agent.process_query(
                    user_input=f"User request: {content}",
                    include_memory=False,
                    source="twitch",
                    discord_user_info=twitch_user_info,
                )
                return resp

            response = await loop.run_in_executor(None, _run_agent)

            if response and str(response).strip():
                # Truncate for Twitch chat (500 char limit)
                reply = str(response).strip()
                if len(reply) > 480:
                    reply = reply[:477] + "..."
                self._send_chat(broadcaster_id, reply)

        except Exception as e:
            logger.error(f"Twitch chat agent error: {e}")

    def _send_chat(self, broadcaster_id: str, message: str) -> None:
        """Send a chat message to a channel."""
        if not self._helix:
            return
        bot_user_id = (self._config.twitch_bot_user_id or "").strip()
        bot_token = (self._config.twitch_bot_access_token or "").strip()
        if not bot_user_id:
            logger.warning("Twitch: cannot send chat — TWITCH_BOT_USER_ID not set")
            return
        self._helix.send_chat_message(
            broadcaster_id=broadcaster_id,
            sender_id=bot_user_id,
            message=message,
            sender_access_token=bot_token or None,
        )

    async def _handle_stream_online(self, event: Dict[str, Any]) -> None:
        """Handle stream.online event."""
        self._stream_online = True
        broadcaster = event.get("broadcaster_user_name", "?")
        started_at = event.get("started_at", "")
        logger.info(f"[TWITCH] Stream ONLINE: {broadcaster} at {started_at}")

        # Fire a routine/heartbeat-style notification
        try:
            from api.server import broadcast_discord_event
            broadcast_discord_event({
                "type": "twitch_stream_online",
                "broadcaster": broadcaster,
                "started_at": started_at,
                "at": time.time(),
            })
        except Exception:
            pass

    async def _handle_stream_offline(self, event: Dict[str, Any]) -> None:
        """Handle stream.offline event."""
        self._stream_online = False
        broadcaster = event.get("broadcaster_user_name", "?")
        logger.info(f"[TWITCH] Stream OFFLINE: {broadcaster}")

        try:
            from api.server import broadcast_discord_event
            broadcast_discord_event({
                "type": "twitch_stream_offline",
                "broadcaster": broadcaster,
                "at": time.time(),
            })
        except Exception:
            pass

    async def stop(self) -> None:
        self._running = False
        logger.info("Twitch bot stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_stream_online(self) -> bool:
        return self._stream_online

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.is_enabled,
            "running": self._running,
            "stream_online": self._stream_online,
            "broadcaster_id": (self._config.twitch_broadcaster_id or ""),
            "bot_username": (self._config.twitch_bot_username or ""),
        }


# Singleton instance
_twitch_bot: Optional[EchoSpeakTwitchBot] = None


def get_twitch_bot() -> EchoSpeakTwitchBot:
    global _twitch_bot
    if _twitch_bot is None:
        _twitch_bot = EchoSpeakTwitchBot()
    return _twitch_bot
