"""
Spotify tools — playback control, search, and queue management.

Requires:
  pip install spotipy
  ALLOW_SPOTIFY=true
  SPOTIFY_CLIENT_ID=...
  SPOTIFY_CLIENT_SECRET=...
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from loguru import logger
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from agent.tool_registry import ToolRegistry

# ── Helpers ──────────────────────────────────────────────────────────

SPOTIFY_SCOPES = (
    "user-read-playback-state "
    "user-modify-playback-state "
    "user-read-currently-playing "
    "playlist-read-private "
    "playlist-read-collaborative"
)


def _get_spotify_client():
    """Build and return an authenticated Spotipy client."""
    try:
        from config import config
    except ImportError:
        raise RuntimeError("Config not available")

    if not getattr(config, "allow_spotify", False):
        raise RuntimeError("Spotify integration is disabled. Set ALLOW_SPOTIFY=true in .env")

    client_id = getattr(config, "spotify_client_id", "")
    client_secret = getattr(config, "spotify_client_secret", "")
    redirect_uri = getattr(config, "spotify_redirect_uri", "http://127.0.0.1:8888/callback")
    token_path = getattr(config, "spotify_token_path", "")

    if not client_id or not client_secret:
        raise RuntimeError("SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set in .env")

    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    cache_path = token_path or str(Path.home() / ".cache" / "spotify_token.json")
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SPOTIFY_SCOPES,
        cache_path=cache_path,
    )

    return spotipy.Spotify(auth_manager=auth_manager)


def _format_track(track: dict) -> str:
    """Format a track for display."""
    name = track.get("name", "Unknown")
    artists = ", ".join(a.get("name", "?") for a in track.get("artists", []))
    album = track.get("album", {}).get("name", "")
    uri = track.get("uri", "")
    duration_ms = track.get("duration_ms", 0)
    duration = f"{duration_ms // 60000}:{(duration_ms % 60000) // 1000:02d}"
    return f"🎵 **{name}** — {artists} ({duration})\n   Album: {album}\n   URI: `{uri}`"


# ── Pydantic schemas ────────────────────────────────────────────────

class SpotifyCurrentTrackArgs(BaseModel):
    pass


class SpotifyPlayArgs(BaseModel):
    uri: Optional[str] = Field(
        default=None,
        description="Spotify URI to play (track, album, or playlist). If empty, resumes current playback.",
    )


class SpotifyPauseArgs(BaseModel):
    pass


class SpotifyNextArgs(BaseModel):
    pass


class SpotifyPrevArgs(BaseModel):
    pass


class SpotifySearchArgs(BaseModel):
    query: str = Field(description="Search query (song name, artist, album)")
    search_type: str = Field(
        default="track",
        description="Type of search: 'track', 'artist', 'album', or 'playlist'",
    )
    limit: int = Field(default=5, description="Number of results to return")


class SpotifyQueueArgs(BaseModel):
    uri: str = Field(description="Spotify track URI to add to the queue")


# ── spotify_current_track ───────────────────────────────────────────

@ToolRegistry.register(
    name="spotify_current_track",
    description="Show what's currently playing on Spotify — track name, artist, progress.",
    category="spotify",
    risk_level="safe",
)
@tool(args_schema=SpotifyCurrentTrackArgs)
def spotify_current_track() -> str:
    """Get the currently playing track on Spotify."""
    try:
        sp = _get_spotify_client()
        current = sp.current_playback()

        if not current or not current.get("item"):
            return "🎵 Nothing is currently playing on Spotify."

        track = current["item"]
        is_playing = current.get("is_playing", False)
        progress_ms = current.get("progress_ms", 0)
        duration_ms = track.get("duration_ms", 1)
        progress_pct = int((progress_ms / duration_ms) * 100) if duration_ms else 0
        progress_str = f"{progress_ms // 60000}:{(progress_ms % 60000) // 1000:02d}"
        duration_str = f"{duration_ms // 60000}:{(duration_ms % 60000) // 1000:02d}"

        name = track.get("name", "Unknown")
        artists = ", ".join(a.get("name", "?") for a in track.get("artists", []))
        album = track.get("album", {}).get("name", "")
        status = "▶️ Playing" if is_playing else "⏸️ Paused"

        return (
            f"{status}: 🎵 **{name}** — {artists}\n"
            f"Album: {album}\n"
            f"Progress: {progress_str} / {duration_str} ({progress_pct}%)"
        )
    except Exception as exc:
        logger.error(f"spotify_current_track failed: {exc}")
        return f"❌ Failed to get current track: {exc}"


# ── spotify_play ────────────────────────────────────────────────────

@ToolRegistry.register(
    name="spotify_play",
    description="Resume playback or play a specific track/album/playlist by Spotify URI.",
    category="spotify",
    is_action=True,
    risk_level="safe",
)
@tool(args_schema=SpotifyPlayArgs)
def spotify_play(uri: Optional[str] = None) -> str:
    """Play or resume Spotify playback."""
    try:
        sp = _get_spotify_client()

        if uri:
            uri = uri.strip()
            if "track" in uri:
                sp.start_playback(uris=[uri])
            else:
                # Album, playlist, or artist
                sp.start_playback(context_uri=uri)
            return f"▶️ Playing: `{uri}`"
        else:
            sp.start_playback()
            return "▶️ Playback resumed."
    except Exception as exc:
        logger.error(f"spotify_play failed: {exc}")
        return f"❌ Failed to play: {exc}"


# ── spotify_pause ───────────────────────────────────────────────────

@ToolRegistry.register(
    name="spotify_pause",
    description="Pause Spotify playback.",
    category="spotify",
    is_action=True,
    risk_level="safe",
)
@tool(args_schema=SpotifyPauseArgs)
def spotify_pause() -> str:
    """Pause Spotify playback."""
    try:
        sp = _get_spotify_client()
        sp.pause_playback()
        return "⏸️ Playback paused."
    except Exception as exc:
        logger.error(f"spotify_pause failed: {exc}")
        return f"❌ Failed to pause: {exc}"


# ── spotify_next ────────────────────────────────────────────────────

@ToolRegistry.register(
    name="spotify_next",
    description="Skip to the next track on Spotify.",
    category="spotify",
    is_action=True,
    risk_level="safe",
)
@tool(args_schema=SpotifyNextArgs)
def spotify_next() -> str:
    """Skip to the next track."""
    try:
        sp = _get_spotify_client()
        sp.next_track()
        return "⏭️ Skipped to next track."
    except Exception as exc:
        logger.error(f"spotify_next failed: {exc}")
        return f"❌ Failed to skip: {exc}"


# ── spotify_prev ────────────────────────────────────────────────────

@ToolRegistry.register(
    name="spotify_prev",
    description="Go back to the previous track on Spotify.",
    category="spotify",
    is_action=True,
    risk_level="safe",
)
@tool(args_schema=SpotifyPrevArgs)
def spotify_prev() -> str:
    """Go back to the previous track."""
    try:
        sp = _get_spotify_client()
        sp.previous_track()
        return "⏮️ Back to previous track."
    except Exception as exc:
        logger.error(f"spotify_prev failed: {exc}")
        return f"❌ Failed to go back: {exc}"


# ── spotify_search ──────────────────────────────────────────────────

@ToolRegistry.register(
    name="spotify_search",
    description="Search Spotify for tracks, artists, albums, or playlists.",
    category="spotify",
    risk_level="safe",
)
@tool(args_schema=SpotifySearchArgs)
def spotify_search(query: str, search_type: str = "track", limit: int = 5) -> str:
    """Search Spotify and return results."""
    try:
        sp = _get_spotify_client()
        valid_types = {"track", "artist", "album", "playlist"}
        if search_type not in valid_types:
            search_type = "track"

        results = sp.search(q=query, type=search_type, limit=limit)

        key = f"{search_type}s"
        items = results.get(key, {}).get("items", [])
        if not items:
            return f"🔍 No {search_type}s found for \"{query}\"."

        lines = [f"🔍 **Spotify Search:** \"{query}\" ({search_type}s)\n"]
        for i, item in enumerate(items, 1):
            if search_type == "track":
                lines.append(f"{i}. {_format_track(item)}")
            elif search_type == "artist":
                name = item.get("name", "?")
                followers = item.get("followers", {}).get("total", 0)
                uri = item.get("uri", "")
                lines.append(f"{i}. 🎤 **{name}** ({followers:,} followers)\n   URI: `{uri}`")
            elif search_type == "album":
                name = item.get("name", "?")
                artists = ", ".join(a.get("name", "?") for a in item.get("artists", []))
                uri = item.get("uri", "")
                lines.append(f"{i}. 💿 **{name}** — {artists}\n   URI: `{uri}`")
            elif search_type == "playlist":
                name = item.get("name", "?")
                owner = item.get("owner", {}).get("display_name", "?")
                tracks = item.get("tracks", {}).get("total", 0)
                uri = item.get("uri", "")
                lines.append(f"{i}. 📋 **{name}** by {owner} ({tracks} tracks)\n   URI: `{uri}`")

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"spotify_search failed: {exc}")
        return f"❌ Failed to search: {exc}"


# ── spotify_queue ───────────────────────────────────────────────────

@ToolRegistry.register(
    name="spotify_queue",
    description="Add a track to the Spotify playback queue by URI.",
    category="spotify",
    is_action=True,
    risk_level="safe",
)
@tool(args_schema=SpotifyQueueArgs)
def spotify_queue(uri: str) -> str:
    """Add a track to the queue."""
    try:
        sp = _get_spotify_client()
        sp.add_to_queue(uri=uri.strip())
        return f"✅ Added to queue: `{uri}`"
    except Exception as exc:
        logger.error(f"spotify_queue failed: {exc}")
        return f"❌ Failed to queue track: {exc}"
