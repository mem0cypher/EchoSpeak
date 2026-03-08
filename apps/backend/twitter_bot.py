"""
Twitter/X integration for EchoSpeak (v6.7.0).

Provides:
  - OAuth 2.0 user-context tweet posting (POST /2/tweets)
  - Mention polling (GET /2/users/:id/mentions)
  - Auto-reply to mentions through the agent pipeline
  - Timeline reading
  - Bearer token (app-only) and user access token support

Design notes:
  - Uses polling for mentions (not webhooks — X webhooks require Enterprise)
  - OAuth 2.0 with PKCE is the recommended auth flow for user actions
  - For simplicity, we support pre-generated access tokens via env vars
"""

import hashlib
import json
import random
import threading
import time
import base64
import hmac
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, quote, urlsplit

import httpx
from loguru import logger


# ============================================================================
# CONSTANTS
# ============================================================================

TWITTER_API_BASE = "https://api.twitter.com/2"
TWITTER_OAUTH2_TOKEN_URL = "https://api.twitter.com/2/oauth2/token"

# Polling state file
_DATA_DIR = Path(__file__).resolve().parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
_TWITTER_STATE_PATH = _DATA_DIR / "twitter_state.json"


# ============================================================================
# TWITTER API CLIENT
# ============================================================================

class TwitterAPIClient:
    """Low-level Twitter/X API v2 client."""

    def __init__(
        self,
        bearer_token: str = "",
        access_token: str = "",
        access_token_secret: str = "",
        client_id: str = "",
        client_secret: str = "",
    ):
        self._bearer_token = bearer_token
        self._access_token = access_token
        self._access_token_secret = access_token_secret
        self._client_id = client_id
        self._client_secret = client_secret

    def _user_headers(self) -> Dict[str, str]:
        """Headers using User Access Token (for tweet.write, etc.)."""
        token = self._access_token or self._bearer_token
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _app_headers(self) -> Dict[str, str]:
        """Headers using Bearer Token (app-only, for reading)."""
        return {
            "Authorization": f"Bearer {self._bearer_token}",
            "Content-Type": "application/json",
        }

    def _has_oauth1_credentials(self) -> bool:
        return bool(
            self._client_id
            and self._client_secret
            and self._access_token
            and self._access_token_secret
        )

    def _percent_encode(self, value: Any) -> str:
        return quote(str(value or ""), safe="~-._")

    def _oauth1_headers(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        parsed = urlsplit(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        oauth_params: Dict[str, str] = {
            "oauth_consumer_key": self._client_id,
            "oauth_nonce": f"{int(time.time() * 1000)}{random.randint(100000, 999999)}",
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_token": self._access_token,
            "oauth_version": "1.0",
        }
        signature_items: List[tuple[str, str]] = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            signature_items.append((self._percent_encode(key), self._percent_encode(value)))
        for key, value in (params or {}).items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                for item in value:
                    signature_items.append((self._percent_encode(key), self._percent_encode(item)))
            else:
                signature_items.append((self._percent_encode(key), self._percent_encode(value)))
        for key, value in oauth_params.items():
            signature_items.append((self._percent_encode(key), self._percent_encode(value)))
        signature_items.sort()
        normalized = "&".join(f"{k}={v}" for k, v in signature_items)
        base_string = "&".join(
            [
                self._percent_encode(method.upper()),
                self._percent_encode(base_url),
                self._percent_encode(normalized),
            ]
        )
        signing_key = (
            f"{self._percent_encode(self._client_secret)}"
            f"&{self._percent_encode(self._access_token_secret)}"
        )
        signature = base64.b64encode(
            hmac.new(
                signing_key.encode("utf-8"),
                base_string.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("utf-8")
        oauth_params["oauth_signature"] = signature
        auth_header = "OAuth " + ", ".join(
            f'{self._percent_encode(key)}="{self._percent_encode(value)}"'
            for key, value in sorted(oauth_params.items())
        )
        return {
            "Authorization": auth_header,
            "Content-Type": "application/json",
        }

    def _user_auth_headers(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        if self._has_oauth1_credentials():
            return self._oauth1_headers(method, url, params=params)
        return self._user_headers()

    # ── Tweet Operations ──────────────────────────────────────────

    def post_tweet(
        self,
        text: str,
        reply_to_id: Optional[str] = None,
        quote_tweet_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Post a tweet. Requires user access token with tweet.write scope."""
        url = f"{TWITTER_API_BASE}/tweets"
        payload: Dict[str, Any] = {"text": text[:280]}
        if reply_to_id:
            payload["reply"] = {"in_reply_to_tweet_id": reply_to_id}
        if quote_tweet_id:
            payload["quote_tweet_id"] = quote_tweet_id

        try:
            with httpx.Client(timeout=15) as client:
                resp = client.post(url, json=payload, headers=self._user_auth_headers("POST", url))
                resp.raise_for_status()
                result = resp.json()
                tweet_id = result.get("data", {}).get("id", "?")
                logger.info(f"Tweet posted: id={tweet_id}")
                return result
        except httpx.HTTPStatusError as e:
            logger.error(f"Twitter post_tweet failed ({e.response.status_code}): {e.response.text[:300]}")
            return {"error": str(e), "status_code": e.response.status_code}
        except Exception as e:
            logger.error(f"Twitter post_tweet failed: {e}")
            return {"error": str(e)}

    def delete_tweet(self, tweet_id: str) -> bool:
        """Delete a tweet by ID."""
        url = f"{TWITTER_API_BASE}/tweets/{tweet_id}"
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.delete(url, headers=self._user_auth_headers("DELETE", url))
                return resp.status_code == 200
        except Exception as e:
            logger.error(f"Twitter delete_tweet failed: {e}")
            return False

    # ── Read Operations ───────────────────────────────────────────

    def get_user_mentions(
        self,
        user_id: str,
        since_id: Optional[str] = None,
        max_results: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get recent mentions for a user. Uses Bearer Token."""
        url = f"{TWITTER_API_BASE}/users/{user_id}/mentions"
        params: Dict[str, Any] = {
            "max_results": min(max_results, 100),
            "tweet.fields": "author_id,created_at,conversation_id,in_reply_to_user_id,text",
            "expansions": "author_id",
            "user.fields": "username,name",
        }
        if since_id:
            params["since_id"] = since_id

        try:
            headers = self._app_headers() if self._bearer_token else self._user_auth_headers("GET", url, params=params)
            with httpx.Client(timeout=15) as client:
                resp = client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                tweets = data.get("data", [])
                # Attach user info from includes
                users_map = {}
                for u in data.get("includes", {}).get("users", []):
                    users_map[u["id"]] = u
                for tweet in tweets:
                    author = users_map.get(tweet.get("author_id", ""), {})
                    tweet["_author_username"] = author.get("username", "")
                    tweet["_author_name"] = author.get("name", "")
                return tweets
        except httpx.HTTPStatusError as e:
            logger.error(f"Twitter get_user_mentions failed ({e.response.status_code}): {e.response.text[:200]}")
            return []
        except Exception as e:
            logger.error(f"Twitter get_user_mentions failed: {e}")
            return []

    def get_user_timeline(
        self,
        user_id: str,
        max_results: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get a user's recent tweets."""
        url = f"{TWITTER_API_BASE}/users/{user_id}/tweets"
        params = {
            "max_results": min(max_results, 100),
            "tweet.fields": "created_at,public_metrics,text",
        }
        try:
            headers = self._app_headers() if self._bearer_token else self._user_auth_headers("GET", url, params=params)
            with httpx.Client(timeout=15) as client:
                resp = client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json().get("data", [])
        except Exception as e:
            logger.error(f"Twitter get_user_timeline failed: {e}")
            return []

    def get_me(self) -> Dict[str, Any]:
        """Get the authenticated user's info."""
        url = f"{TWITTER_API_BASE}/users/me"
        params = {"user.fields": "id,username,name,description"}
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(url, params=params, headers=self._user_auth_headers("GET", url, params=params))
                resp.raise_for_status()
                return resp.json().get("data", {})
        except Exception as e:
            logger.error(f"Twitter get_me failed: {e}")
            return {"error": str(e)}

    def lookup_user_by_username(self, username: str) -> Dict[str, Any]:
        """Look up a user by username."""
        url = f"{TWITTER_API_BASE}/users/by/username/{username}"
        params = {"user.fields": "id,username,name,description,public_metrics"}
        try:
            headers = self._app_headers() if self._bearer_token else self._user_auth_headers("GET", url, params=params)
            with httpx.Client(timeout=10) as client:
                resp = client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                return resp.json().get("data", {})
        except Exception as e:
            logger.error(f"Twitter lookup_user failed: {e}")
            return {"error": str(e)}


# ============================================================================
# POLLING STATE PERSISTENCE
# ============================================================================

def _load_poll_state() -> Dict[str, Any]:
    try:
        if _TWITTER_STATE_PATH.exists():
            return json.loads(_TWITTER_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_poll_state(state: Dict[str, Any]) -> None:
    try:
        _TWITTER_STATE_PATH.write_text(
            json.dumps(state, default=str, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"Failed to save Twitter poll state: {e}")


# ============================================================================
# MAIN TWITTER BOT CLASS
# ============================================================================

# No-tweet sentinel (agent says it has nothing to post)
_NO_TWEET_SENTINEL = "NO_TWEET"

# Autonomous tweet state file
_AUTO_TWEET_STATE_PATH = _DATA_DIR / "twitter_auto_tweet_state.json"


def _load_auto_tweet_state() -> Dict[str, Any]:
    try:
        if _AUTO_TWEET_STATE_PATH.exists():
            return json.loads(_AUTO_TWEET_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"tweets_today": [], "recent_hashes": [], "pending_approval": None}


def _save_auto_tweet_state(state: Dict[str, Any]) -> None:
    try:
        _AUTO_TWEET_STATE_PATH.write_text(
            json.dumps(state, default=str, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"Failed to save auto tweet state: {e}")


def _content_hash(text: str) -> str:
    """Short hash of tweet content for dedup."""
    return hashlib.sha256(text.strip().lower().encode()).hexdigest()[:16]


class EchoSpeakTwitterBot:
    """Manages the Twitter/X integration lifecycle.

    - Polls for new mentions at a configurable interval
    - Routes mentions through the EchoSpeak agent pipeline
    - Posts replies via the Twitter API v2
    - Autonomous tweeting: Echo decides what to tweet on a schedule
    - Supports manual tweet posting via tools
    """

    def __init__(self):
        from config import config

        self._config = config
        self._api: Optional[TwitterAPIClient] = None
        self._running = False
        self._poll_thread: Optional[threading.Thread] = None
        self._auto_tweet_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._agent = None
        self._pending_tweet: Optional[str] = None
        self._pending_tweet_lock = threading.Lock()
        self._auto_tweet_history: List[Dict[str, Any]] = []
        self._auto_tweet_history_lock = threading.Lock()
        self._resolved_user_id = ""
        self._resolved_username = ""
        self._resolved_name = ""

    def set_agent(self, agent: Any) -> None:
        self._agent = agent

    def _effective_bot_user_id(self) -> str:
        return str(self._resolved_user_id or self._config.twitter_bot_user_id or "").strip()

    def _sanitize_generated_tweet(self, text: Optional[str]) -> str:
        cleaned = str(text or "").replace("\r\n", "\n").strip()
        if not cleaned:
            return ""
        if cleaned.startswith("```") and cleaned.endswith("```"):
            cleaned = cleaned.strip("`").strip()
        first_block = cleaned.split("\n\n", 1)[0].strip()
        candidate = first_block or cleaned
        lowered = candidate.lower()
        prefixes = (
            "tweet:",
            "tweet text:",
            "draft tweet:",
            "proposed tweet:",
            "x post:",
            "post:",
        )
        for prefix in prefixes:
            if lowered.startswith(prefix):
                candidate = candidate[len(prefix):].strip()
                lowered = candidate.lower()
                break
        if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {'"', "'"}:
            candidate = candidate[1:-1].strip()
            lowered = candidate.lower()
        if lowered in {"no_tweet", "no tweet"}:
            return _NO_TWEET_SENTINEL
        invalid_prefixes = (
            "post to discord channel",
            "send to discord",
            "reply in the web ui",
            "plan:",
            "action:",
            "tool:",
        )
        if any(lowered.startswith(prefix) for prefix in invalid_prefixes):
            return ""
        lines = [line.strip() for line in candidate.splitlines() if line.strip()]
        candidate = lines[0] if lines else candidate.strip()
        return candidate[:280].strip()

    def _generate_tweet_text(self, prompt: str) -> str:
        if not self._agent:
            return ""
        llm = getattr(self._agent, "llm_wrapper", None)
        if llm is None:
            return ""
        full_prompt = "\n\n".join(
            [
                "Write exactly one finished X/Twitter post.",
                "Do not call tools.",
                "Do not describe a plan.",
                "Do not propose any other action.",
                "Do not say 'tweet:' or 'post:'.",
                "Return only the final post text, or NO_TWEET.",
                str(prompt or "").strip(),
            ]
        )
        try:
            raw = str(llm.invoke(full_prompt) or "").strip()
        except Exception as e:
            logger.warning(f"Twitter generation failed: {e}")
            return ""
        return self._sanitize_generated_tweet(raw)

    def _generate_tweet_agentic(self, prompt: str) -> str:
        """Generate a tweet through the full agent pipeline.

        Unlike ``_generate_tweet_text`` which calls ``llm.invoke()`` directly
        (blind, no tools, no memory), this routes through ``process_query``
        so the agent can:
          - Use tools (self_read, self_grep, self_git_status, web_search, …)
          - Access conversation memory for grounding
          - Reason through its full pipeline with Soul personality

        Falls back to the blind path if the agent pipeline fails.
        """
        if not self._agent:
            return ""
        try:
            response, ok = self._agent.process_query(
                user_input=prompt,
                include_memory=True,
                source="twitter_autonomous",
            )
            if not ok or not response:
                logger.debug("Agentic tweet generation: agent returned empty/failed, falling back")
                return self._generate_tweet_text(prompt)
            return self._sanitize_generated_tweet(str(response))
        except Exception as e:
            logger.warning(f"Agentic tweet generation failed ({e}), falling back to blind LLM")
            return self._generate_tweet_text(prompt)

    @property
    def is_enabled(self) -> bool:
        return bool(
            getattr(self._config, "allow_twitter", False)
            and (
                getattr(self._config, "twitter_bearer_token", "")
                or getattr(self._config, "twitter_access_token", "")
            )
        )

    async def start(self) -> None:
        """Initialize API client and start mention polling."""
        if not self.is_enabled:
            logger.info("Twitter/X integration disabled or missing credentials")
            return

        self._api = TwitterAPIClient(
            bearer_token=self._config.twitter_bearer_token,
            access_token=self._config.twitter_access_token,
            access_token_secret=self._config.twitter_access_token_secret,
            client_id=self._config.twitter_client_id,
            client_secret=self._config.twitter_client_secret,
        )
        self._running = True
        self._stop_event.clear()
        self._rehydrate_pending_tweet()

        me = self._api.get_me()
        if isinstance(me, dict) and me.get("id"):
            self._resolved_user_id = str(me.get("id") or "").strip()
            self._resolved_username = str(me.get("username") or "").strip()
            self._resolved_name = str(me.get("name") or "").strip()
            configured_user_id = str(self._config.twitter_bot_user_id or "").strip()
            if configured_user_id and configured_user_id != self._resolved_user_id:
                logger.warning(
                    f"TWITTER_BOT_USER_ID ({configured_user_id}) does not match authenticated account @{self._resolved_username} ({self._resolved_user_id}); using authenticated account ID"
                )
            else:
                logger.info(
                    f"Twitter/X authenticated as @{self._resolved_username} (user_id={self._resolved_user_id})"
                )
        else:
            self._resolved_user_id = ""
            self._resolved_username = ""
            self._resolved_name = ""

        # Start polling thread if auto-reply is enabled
        if self._config.twitter_auto_reply_mentions:
            bot_user_id = self._effective_bot_user_id()
            if bot_user_id:
                self._poll_thread = threading.Thread(
                    target=self._mention_poll_loop,
                    args=(bot_user_id,),
                    daemon=True,
                    name="twitter-mention-poll",
                )
                self._poll_thread.start()
                logger.info(
                    f"Twitter mention polling started "
                    f"(interval={self._config.twitter_poll_interval}s, "
                    f"user_id={bot_user_id})"
                )
            else:
                logger.warning(
                    "Twitter auto-reply enabled but authenticated account ID could not be resolved"
                )

        # Start autonomous tweet thread if enabled
        if self._config.twitter_autonomous_enabled:
            self._auto_tweet_thread = threading.Thread(
                target=self._autonomous_tweet_loop,
                daemon=True,
                name="twitter-auto-tweet",
            )
            self._auto_tweet_thread.start()
            logger.info(
                f"Twitter autonomous tweeting started "
                f"(interval={self._config.twitter_autonomous_interval}m, "
                f"max_daily={self._config.twitter_autonomous_max_daily}, "
                f"approval={'required' if self._config.twitter_autonomous_require_approval else 'auto-post'})"
            )

        logger.info("Twitter/X bot started")

    def _mention_poll_loop(self, user_id: str) -> None:
        """Background thread: poll for mentions and process them."""
        interval = max(self._config.twitter_poll_interval, 30)  # min 30s
        state = _load_poll_state()
        last_mention_id = state.get("last_mention_id")

        while not self._stop_event.is_set():
            try:
                mentions = self._api.get_user_mentions(
                    user_id=user_id,
                    since_id=last_mention_id,
                    max_results=5,
                )

                if mentions:
                    # Process oldest first
                    for mention in reversed(mentions):
                        self._process_mention(mention, user_id)
                    # Update watermark to newest mention
                    last_mention_id = mentions[0].get("id", last_mention_id)
                    _save_poll_state({"last_mention_id": last_mention_id})

            except Exception as e:
                logger.error(f"Twitter mention poll error: {e}")

            self._stop_event.wait(interval)

    def _process_mention(self, mention: Dict[str, Any], bot_user_id: str) -> None:
        """Process a single mention through the agent pipeline."""
        tweet_id = mention.get("id", "")
        author_id = mention.get("author_id", "")
        author_username = mention.get("_author_username", "")
        text = mention.get("text", "").strip()

        if author_id == bot_user_id:
            return  # Don't reply to self

        logger.info(f"[TWITTER MENTION] @{author_username}: {text[:100]}")

        if not self._agent or not self._api:
            return

        try:
            # Strip the @mention of our bot from the text
            import re
            me_pattern = re.compile(r"@\w+\s*", re.IGNORECASE)
            clean_text = me_pattern.sub("", text).strip()

            if not clean_text:
                return

            twitter_user_info = {
                "user_id": author_id,
                "username": author_username,
                "display_name": mention.get("_author_name", author_username),
                "platform": "twitter",
            }

            resp, ok = self._agent.process_query(
                user_input=f"User request: {clean_text}",
                include_memory=False,
                source="twitter",
                discord_user_info=twitter_user_info,
            )

            if resp and str(resp).strip():
                reply_text = f"@{author_username} {str(resp).strip()}"
                if len(reply_text) > 280:
                    reply_text = reply_text[:277] + "..."
                self._api.post_tweet(text=reply_text, reply_to_id=tweet_id)
                logger.info(f"[TWITTER] Replied to @{author_username} (tweet {tweet_id})")

        except Exception as e:
            logger.error(f"Twitter mention processing error: {e}")

    # ── Autonomous Tweeting ────────────────────────────────────────

    def _autonomous_tweet_loop(self) -> None:
        """Background thread: periodically ask the agent to compose a tweet.

        Safety guardrails:
          - Daily cap (default 6 tweets/day)
          - Content dedup (no substantially similar tweets)
          - Random jitter on timing (±20%) to avoid clockwork patterns
          - Optional owner-approval mode (default ON)
          - Minimum 30 minutes between tweets regardless of config

        Also checks for new git commits on each tick. If new code was pushed,
        Echo composes an update tweet announcing the changes (changelog duty).
        """
        # Let the agent fully initialize before first tick
        time.sleep(30)

        interval_minutes = max(self._config.twitter_autonomous_interval, 30)  # min 30m
        max_daily = max(self._config.twitter_autonomous_max_daily, 1)

        while not self._stop_event.is_set():
            try:
                # Priority: check for new git commits first
                changelog_posted = self._maybe_changelog_tweet(max_daily)
                # If no changelog tweet, do a regular creative tweet
                if not changelog_posted:
                    self._autonomous_tweet_tick(max_daily)
            except Exception as e:
                logger.error(f"Autonomous tweet tick error: {e}")

            # Sleep with ±20% jitter
            base_seconds = interval_minutes * 60
            jitter = random.uniform(-0.2, 0.2) * base_seconds
            sleep_seconds = base_seconds + jitter

            # Sleep in small increments so stop() is responsive
            elapsed = 0.0
            while elapsed < sleep_seconds and not self._stop_event.is_set():
                time.sleep(min(10, sleep_seconds - elapsed))
                elapsed += 10

    def _maybe_changelog_tweet(self, max_daily: int) -> bool:
        """Handle pending/new git changelog work for Discord and Twitter.

        Returns True when the Discord leg is fully handled.
        That includes intentionally skipped cases (Discord bot disabled, feature disabled,
        or no channels configured) so changelog delivery doesn't stall forever.
        """
        try:
            from agent.git_changelog import (
                check_for_new_commits,
                format_update_tweet_prompt,
                mark_changelog_announced,
                update_pending_changelog,
            )
        except ImportError:
            return False

        changelog_data = check_for_new_commits()
        if not changelog_data:
            return False

        head_sha = str(changelog_data.get("head_sha") or "")

        if not bool(changelog_data.get("discord_handled")):
            if self._announce_changelog_to_discord(changelog_data):
                changelog_data = update_pending_changelog(head_sha, discord_handled=True) or changelog_data
                changelog_data["discord_handled"] = True

        if bool(changelog_data.get("twitter_handled")):
            if bool(changelog_data.get("discord_handled")):
                mark_changelog_announced(head_sha)
            return True

        # Check daily budget before generating
        state = _load_auto_tweet_state()
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tweets_today = [t for t in state.get("tweets_today", []) if t.get("date") == today_str]
        if len(tweets_today) >= max_daily:
            logger.debug("Changelog tweet: daily cap reached, deferring")
            return True

        if not self._agent:
            logger.debug("Changelog tweet: agent not ready")
            return True

        prompt = format_update_tweet_prompt(changelog_data)
        tweet_text = self._generate_tweet_agentic(prompt)
        if not tweet_text or _NO_TWEET_SENTINEL in tweet_text.upper():
            logger.debug("Changelog tweet: agent declined")
            return True

        if len(tweet_text) > 280:
            tweet_text = tweet_text[:277] + "..."

        # Dedup check
        content_h = _content_hash(tweet_text)
        recent_hashes = state.get("recent_hashes", [])
        if content_h in recent_hashes:
            logger.info("Changelog tweet: dedup blocked")
            changelog_data = update_pending_changelog(head_sha, twitter_handled=True) or changelog_data
            changelog_data["twitter_handled"] = True
            if bool(changelog_data.get("discord_handled")):
                mark_changelog_announced(head_sha)
            self._record_auto_tweet_history(tweet_text, "changelog_dedup_blocked")
            return True

        # Use the same approval/auto-post path as regular autonomous tweets
        if self._config.twitter_autonomous_require_approval:
            with self._pending_tweet_lock:
                self._pending_tweet = tweet_text
            self._record_auto_tweet_history(tweet_text, "pending_approval_changelog")
            self._notify_owner_pending_tweet(tweet_text)
            state["pending_approval"] = {
                "text": tweet_text,
                "type": "changelog",
                "changelog_head_sha": head_sha,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            _save_auto_tweet_state(state)
            changelog_data = update_pending_changelog(head_sha, twitter_handled=True) or changelog_data
            changelog_data["twitter_handled"] = True
            if bool(changelog_data.get("discord_handled")):
                mark_changelog_announced(head_sha)
            logger.info(f"Changelog tweet queued for approval: {tweet_text[:80]}...")
            return True

        # Auto-post mode
        result = self._api.post_tweet(tweet_text)
        if "error" not in result:
            tweet_id = result.get("data", {}).get("id", "?")
            logger.info(f"Changelog tweet posted: id={tweet_id}")
            tweets_today.append({
                "date": today_str,
                "text": tweet_text,
                "tweet_id": tweet_id,
                "type": "changelog",
                "at": datetime.now(timezone.utc).isoformat(),
            })
            recent_hashes.append(content_h)
            state["tweets_today"] = tweets_today
            state["recent_hashes"] = recent_hashes[-50:]
            state["pending_approval"] = None
            _save_auto_tweet_state(state)
            self._record_auto_tweet_history(tweet_text, "changelog_posted", tweet_id)
            changelog_data = update_pending_changelog(head_sha, twitter_handled=True) or changelog_data
            changelog_data["twitter_handled"] = True
            if bool(changelog_data.get("discord_handled")):
                mark_changelog_announced(head_sha)
            return True
        else:
            logger.error(f"Changelog tweet failed: {result}")
            self._record_auto_tweet_history(tweet_text, "changelog_error")
            return True

    def _autonomous_tweet_tick(self, max_daily: int) -> None:
        """One autonomous tweet cycle: check budget, generate, maybe post."""
        state = _load_auto_tweet_state()

        # Prune tweets_today to only today's entries
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tweets_today = [
            t for t in state.get("tweets_today", [])
            if t.get("date") == today_str
        ]

        if len(tweets_today) >= max_daily:
            logger.debug(
                f"Autonomous tweet: daily cap reached ({len(tweets_today)}/{max_daily})"
            )
            return

        if not self._agent:
            logger.debug("Autonomous tweet: agent not ready")
            return

        # Build prompt with recent tweet context so the agent doesn't repeat
        recent_texts = [t.get("text", "") for t in tweets_today[-5:]]
        prompt = self._config.twitter_autonomous_prompt

        # Enrich with grounded project updates so the agent has real context
        try:
            from agent.update_context import get_update_context_service

            grounded_context = get_update_context_service().build_context_block(
                limit=5,
                public=False,
                include_diff=True,
                max_diff_chars=1200,
                heading="Grounded recent EchoSpeak work",
            )
            if grounded_context:
                prompt += f"\n\n{grounded_context}"
        except Exception:
            pass

        if recent_texts:
            recent_block = "\n".join(f"- {t}" for t in recent_texts)
            prompt += f"\n\nYour recent tweets today (don't repeat these topics):\n{recent_block}"

        prompt += (
            "\n\nIMPORTANT: Only tweet about things you can verify from the context above, "
            "your memory, or by using your tools (self_read, self_grep). "
            "Do NOT invent or assume technical details. "
            "If you have nothing grounded to say right now, reply with NO_TWEET."
        )

        tweet_text = self._generate_tweet_agentic(prompt)

        if not tweet_text or _NO_TWEET_SENTINEL in tweet_text.upper():
            logger.debug("Autonomous tweet: agent declined to tweet")
            self._record_auto_tweet_history(None, "declined")
            return

        if len(tweet_text) > 280:
            tweet_text = tweet_text[:277] + "..."

        content_h = _content_hash(tweet_text)
        recent_hashes = state.get("recent_hashes", [])
        if content_h in recent_hashes:
            logger.info("Autonomous tweet: dedup blocked (too similar to recent tweet)")
            self._record_auto_tweet_history(tweet_text, "dedup_blocked")
            return

        if self._config.twitter_autonomous_require_approval:
            with self._pending_tweet_lock:
                self._pending_tweet = tweet_text
            logger.info(f"Autonomous tweet queued for approval: {tweet_text[:80]}...")
            self._record_auto_tweet_history(tweet_text, "pending_approval")
            self._notify_owner_pending_tweet(tweet_text)
            state["pending_approval"] = {
                "text": tweet_text,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            _save_auto_tweet_state(state)
            return

        result = self._api.post_tweet(tweet_text)
        if "error" not in result:
            tweet_id = result.get("data", {}).get("id", "?")
            logger.info(f"Autonomous tweet posted: id={tweet_id}, text={tweet_text[:80]}...")
            tweets_today.append({
                "date": today_str,
                "text": tweet_text,
                "tweet_id": tweet_id,
                "at": datetime.now(timezone.utc).isoformat(),
            })
            recent_hashes.append(content_h)
            # Keep last 50 hashes
            if len(recent_hashes) > 50:
                recent_hashes = recent_hashes[-50:]
            state["tweets_today"] = tweets_today
            state["recent_hashes"] = recent_hashes
            state["pending_approval"] = None
            _save_auto_tweet_state(state)
            self._record_auto_tweet_history(tweet_text, "posted", tweet_id)
        else:
            logger.error(f"Autonomous tweet failed: {result}")
            self._record_auto_tweet_history(tweet_text, "error")

    def _notify_owner_pending_tweet(self, tweet_text: str) -> None:
        """Send a Discord DM to the owner about a pending autonomous tweet."""
        try:
            from agent.heartbeat import route_message
            preview = (tweet_text or "").replace("```", "'''").strip()
            msg = (
                f"🐦 **EchoSpeak wants to tweet:**\n"
                f"```text\n{preview[:280]}\n```\n"
                f"Reply `approve` or `reject` here in Discord DM, use the Web UI, "
                f"or call `POST /twitter/autonomous/approve` / `POST /twitter/autonomous/reject`."
            )
            route_message(msg, ["discord"], label="Autonomous Tweet")
        except Exception:
            pass

    def _announce_changelog_to_discord(self, changelog_data: Dict[str, Any]) -> bool:
        """Post a git changelog announcement into a configured Discord server channel.

        Returns True when the Discord leg is fully handled.
        That includes intentionally skipped cases (Discord bot disabled, feature disabled,
        or no channels configured) so changelog delivery doesn't stall forever.
        """
        if not bool(getattr(self._config, "discord_changelog_enabled", True)):
            return True

        if not bool(getattr(self._config, "allow_discord_bot", False)):
            return True

        channel_candidates = list(getattr(self._config, "discord_changelog_channels", []) or [])
        if not channel_candidates:
            logger.info("Discord changelog route: no channels configured, skipping")
            return True

        try:
            from agent.git_changelog import format_discord_update_message
            from discord_bot import queue_discord_channel_with_status
        except Exception as e:
            logger.warning(f"Discord changelog route unavailable: {e}")
            return False

        message = format_discord_update_message(changelog_data)
        server = str(getattr(self._config, "discord_changelog_server", "") or "").strip() or None
        saw_retryable_failure = False

        for channel in channel_candidates:
            status = queue_discord_channel_with_status(channel, message, server=server)
            if status == "sent":
                logger.info(f"Changelog announcement sent to Discord channel target '{channel}'")
                return True
            if status in {"error", "unavailable"}:
                saw_retryable_failure = True

        if saw_retryable_failure:
            logger.warning(
                "Discord changelog route hit a retryable failure for targets: "
                + ", ".join(str(c) for c in channel_candidates)
            )
            return False

        logger.warning(
            "Discord changelog route found no matching channel for targets: "
            + ", ".join(str(c) for c in channel_candidates)
        )
        return True

    def _record_auto_tweet_history(self, text: Optional[str], status: str, tweet_id: str = "") -> None:
        """Record an autonomous tweet attempt in the in-memory history ring buffer."""
        entry = {
            "text": text,
            "status": status,
            "tweet_id": tweet_id,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        with self._auto_tweet_history_lock:
            self._auto_tweet_history.append(entry)
            if len(self._auto_tweet_history) > 50:
                self._auto_tweet_history = self._auto_tweet_history[-50:]

    def _pending_tweet_from_state(self) -> Optional[str]:
        state = _load_auto_tweet_state()
        pending = state.get("pending_approval")
        if isinstance(pending, dict):
            text = str(pending.get("text") or "").strip()
            return text or None
        if isinstance(pending, str):
            text = pending.strip()
            return text or None
        return None

    def _rehydrate_pending_tweet(self) -> None:
        persisted = self._pending_tweet_from_state()
        with self._pending_tweet_lock:
            self._pending_tweet = persisted
        if persisted:
            logger.info("Twitter autonomous pending tweet restored from disk")

    # ── Autonomous tweet approval/rejection ────────────────────────

    def approve_pending_tweet(self) -> Dict[str, Any]:
        """Approve and post the pending autonomous tweet."""
        with self._pending_tweet_lock:
            text = self._pending_tweet
            if not text:
                text = self._pending_tweet_from_state()
            self._pending_tweet = None

        if not text:
            return {"ok": False, "error": "No pending tweet to approve"}

        if not self._api:
            return {"ok": False, "error": "Twitter API client not initialized"}

        result = self._api.post_tweet(text)
        if "error" not in result:
            tweet_id = result.get("data", {}).get("id", "?")
            logger.info(f"Approved autonomous tweet posted: id={tweet_id}")
            # Update state
            state = _load_auto_tweet_state()
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            tweets_today = [t for t in state.get("tweets_today", []) if t.get("date") == today_str]
            tweets_today.append({
                "date": today_str,
                "text": text,
                "tweet_id": tweet_id,
                "at": datetime.now(timezone.utc).isoformat(),
            })
            recent_hashes = state.get("recent_hashes", [])
            recent_hashes.append(_content_hash(text))
            state["tweets_today"] = tweets_today
            state["recent_hashes"] = recent_hashes[-50:]
            state["pending_approval"] = None
            _save_auto_tweet_state(state)
            self._record_auto_tweet_history(text, "approved_and_posted", tweet_id)
            return {"ok": True, "tweet_id": tweet_id, "text": text}
        else:
            return {"ok": False, "error": result.get("error", "unknown")}

    def reject_pending_tweet(self) -> Dict[str, Any]:
        """Reject the pending autonomous tweet."""
        with self._pending_tweet_lock:
            text = self._pending_tweet
            if not text:
                text = self._pending_tweet_from_state()
            self._pending_tweet = None

        if not text:
            return {"ok": False, "error": "No pending tweet to reject"}

        # Clear from state
        state = _load_auto_tweet_state()
        state["pending_approval"] = None
        _save_auto_tweet_state(state)
        self._record_auto_tweet_history(text, "rejected")
        logger.info(f"Autonomous tweet rejected: {text[:80]}...")
        return {"ok": True, "rejected_text": text}

    def get_pending_tweet(self) -> Optional[str]:
        """Get the currently pending autonomous tweet, if any."""
        with self._pending_tweet_lock:
            text = self._pending_tweet
        if text:
            return text
        return self._pending_tweet_from_state()

    def get_auto_tweet_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent autonomous tweet history (newest first)."""
        with self._auto_tweet_history_lock:
            return list(reversed(self._auto_tweet_history))[:limit]

    # ── Public methods for tools ──────────────────────────────────

    def post_tweet(self, text: str) -> Dict[str, Any]:
        """Post a new tweet (for use by agent tools)."""
        if not self._api:
            return {"error": "Twitter API client not initialized"}
        return self._api.post_tweet(text)

    def reply_to_tweet(self, tweet_id: str, text: str) -> Dict[str, Any]:
        """Reply to a tweet (for use by agent tools)."""
        if not self._api:
            return {"error": "Twitter API client not initialized"}
        return self._api.post_tweet(text, reply_to_id=tweet_id)

    def get_mentions(self, max_results: int = 10) -> List[Dict[str, Any]]:
        """Get recent mentions (for use by agent tools)."""
        if not self._api:
            return []
        user_id = self._effective_bot_user_id()
        if not user_id:
            return []
        return self._api.get_user_mentions(user_id, max_results=max_results)

    def get_timeline(self, max_results: int = 10) -> List[Dict[str, Any]]:
        """Get own timeline (for use by agent tools)."""
        if not self._api:
            return []
        user_id = self._effective_bot_user_id()
        if not user_id:
            return []
        return self._api.get_user_timeline(user_id, max_results=max_results)

    async def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5)
        if self._auto_tweet_thread and self._auto_tweet_thread.is_alive():
            self._auto_tweet_thread.join(timeout=5)
        logger.info("Twitter/X bot stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self.is_enabled,
            "running": self._running,
            "polling": bool(self._poll_thread and self._poll_thread.is_alive()),
            "poll_interval": self._config.twitter_poll_interval,
            "auto_reply": self._config.twitter_auto_reply_mentions,
            "bot_user_id": self._effective_bot_user_id(),
            "configured_bot_user_id": (self._config.twitter_bot_user_id or ""),
            "resolved_username": self._resolved_username,
            "resolved_name": self._resolved_name,
            "autonomous": {
                "enabled": self._config.twitter_autonomous_enabled,
                "running": bool(self._auto_tweet_thread and self._auto_tweet_thread.is_alive()),
                "interval_minutes": self._config.twitter_autonomous_interval,
                "max_daily": self._config.twitter_autonomous_max_daily,
                "require_approval": self._config.twitter_autonomous_require_approval,
                "pending_tweet": self.get_pending_tweet(),
            },
        }


# Singleton instance
_twitter_bot: Optional[EchoSpeakTwitterBot] = None


def get_twitter_bot() -> EchoSpeakTwitterBot:
    global _twitter_bot
    if _twitter_bot is None:
        _twitter_bot = EchoSpeakTwitterBot()
    return _twitter_bot
