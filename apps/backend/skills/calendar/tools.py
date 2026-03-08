"""
Google Calendar tools — list events, create meetings, check today's schedule.

Requires:
  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
  ALLOW_CALENDAR=true
  GOOGLE_CALENDAR_CREDENTIALS_PATH=path/to/credentials.json
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List

from loguru import logger
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from agent.tool_registry import ToolRegistry

# ── Helpers ──────────────────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _get_calendar_service():
    """Build and return an authenticated Google Calendar API service."""
    try:
        from config import config
    except ImportError:
        raise RuntimeError("Config not available")

    if not getattr(config, "allow_calendar", False):
        raise RuntimeError("Calendar integration is disabled. Set ALLOW_CALENDAR=true in .env")

    creds_path = getattr(config, "google_calendar_credentials_path", "")
    token_path = getattr(config, "google_calendar_token_path", "")

    if not creds_path:
        raise RuntimeError("GOOGLE_CALENDAR_CREDENTIALS_PATH not set")

    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    token_file = Path(token_path) if token_path else Path(creds_path).parent / "gcal_token.json"

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return build("calendar", "v3", credentials=creds)


def _format_event(event: dict) -> str:
    """Format a single calendar event for display."""
    start = event.get("start", {})
    end = event.get("end", {})
    start_str = start.get("dateTime", start.get("date", "?"))
    end_str = end.get("dateTime", end.get("date", ""))

    title = event.get("summary", "(No title)")
    location = event.get("location", "")
    event_id = event.get("id", "")

    # Parse time for friendlier display
    try:
        dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        time_str = dt.strftime("%I:%M %p").lstrip("0")
    except Exception:
        time_str = start_str

    parts = [f"• **{time_str}** — {title}"]
    if location:
        parts.append(f"  📍 {location}")
    if event_id:
        parts.append(f"  ID: `{event_id[:20]}...`")

    return "\n".join(parts)


# ── Pydantic schemas ────────────────────────────────────────────────

class CalendarGetTodayArgs(BaseModel):
    pass


class CalendarListEventsArgs(BaseModel):
    start_date: Optional[str] = Field(
        default=None,
        description="Start date in ISO format (YYYY-MM-DD). Defaults to today.",
    )
    end_date: Optional[str] = Field(
        default=None,
        description="End date in ISO format (YYYY-MM-DD). Defaults to start_date + lookahead_days.",
    )
    max_results: int = Field(default=20, description="Max number of events to return")


class CalendarCreateEventArgs(BaseModel):
    title: str = Field(description="Event title/summary")
    start_time: str = Field(description="Start time in ISO format (e.g. '2026-03-15T14:00:00')")
    end_time: str = Field(description="End time in ISO format (e.g. '2026-03-15T15:00:00')")
    description: Optional[str] = Field(default=None, description="Event description")
    location: Optional[str] = Field(default=None, description="Event location")
    attendees: Optional[List[str]] = Field(
        default=None,
        description="Email addresses of attendees",
    )


class CalendarDeleteEventArgs(BaseModel):
    event_id: str = Field(description="The Google Calendar event ID to delete")


# ── calendar_get_today ──────────────────────────────────────────────

@ToolRegistry.register(
    name="calendar_get_today",
    description="Get today's calendar events. Returns a formatted list of all events for today.",
    category="calendar",
    risk_level="safe",
)
@tool(args_schema=CalendarGetTodayArgs)
def calendar_get_today() -> str:
    """Get today's schedule from Google Calendar."""
    try:
        service = _get_calendar_service()
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)

        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=start_of_day.isoformat(),
                timeMax=end_of_day.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            )
            .execute()
        )

        events = events_result.get("items", [])
        if not events:
            return "📅 **Today's Schedule:** No events scheduled for today."

        lines = [f"📅 **Today's Schedule** ({now.strftime('%A, %B %d')})\n"]
        for event in events:
            lines.append(_format_event(event))

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"calendar_get_today failed: {exc}")
        return f"❌ Failed to get today's events: {exc}"


# ── calendar_list_events ────────────────────────────────────────────

@ToolRegistry.register(
    name="calendar_list_events",
    description="List calendar events within a date range. Specify start and end dates.",
    category="calendar",
    risk_level="safe",
)
@tool(args_schema=CalendarListEventsArgs)
def calendar_list_events(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_results: int = 20,
) -> str:
    """List events from Google Calendar within a date range."""
    try:
        from config import config
        service = _get_calendar_service()

        lookahead = getattr(config, "calendar_lookahead_days", 7)

        if start_date:
            start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        else:
            start_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        if end_date:
            end_dt = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc) + timedelta(days=1)
        else:
            end_dt = start_dt + timedelta(days=lookahead)

        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=start_dt.isoformat(),
                timeMax=end_dt.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=max_results,
            )
            .execute()
        )

        events = events_result.get("items", [])
        if not events:
            return f"📅 No events found between {start_dt.strftime('%b %d')} and {end_dt.strftime('%b %d')}."

        lines = [f"📅 **Events** ({start_dt.strftime('%b %d')} — {end_dt.strftime('%b %d')})\n"]
        current_day = ""
        for event in events:
            start = event.get("start", {})
            day_str = start.get("dateTime", start.get("date", ""))[:10]
            if day_str != current_day:
                current_day = day_str
                try:
                    day_dt = datetime.fromisoformat(day_str)
                    lines.append(f"\n**{day_dt.strftime('%A, %b %d')}**")
                except Exception:
                    lines.append(f"\n**{day_str}**")
            lines.append(_format_event(event))

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"calendar_list_events failed: {exc}")
        return f"❌ Failed to list events: {exc}"


# ── calendar_create_event ───────────────────────────────────────────

@ToolRegistry.register(
    name="calendar_create_event",
    description="Create a new Google Calendar event. Requires title, start_time, and end_time.",
    category="calendar",
    is_action=True,
    risk_level="moderate",
)
@tool(args_schema=CalendarCreateEventArgs)
def calendar_create_event(
    title: str,
    start_time: str,
    end_time: str,
    description: Optional[str] = None,
    location: Optional[str] = None,
    attendees: Optional[List[str]] = None,
) -> str:
    """Create a new event on Google Calendar."""
    try:
        from config import config
        service = _get_calendar_service()

        tz = getattr(config, "calendar_default_timezone", "") or "UTC"

        event_body: dict = {
            "summary": title,
            "start": {"dateTime": start_time, "timeZone": tz},
            "end": {"dateTime": end_time, "timeZone": tz},
        }
        if description:
            event_body["description"] = description
        if location:
            event_body["location"] = location
        if attendees:
            event_body["attendees"] = [{"email": e.strip()} for e in attendees]

        event = service.events().insert(calendarId="primary", body=event_body).execute()

        link = event.get("htmlLink", "")
        return (
            f"✅ Event created: **{title}**\n"
            f"- Start: {start_time}\n"
            f"- End: {end_time}\n"
            f"- ID: `{event.get('id', 'unknown')}`\n"
            + (f"- Link: {link}" if link else "")
        )
    except Exception as exc:
        logger.error(f"calendar_create_event failed: {exc}")
        return f"❌ Failed to create event: {exc}"


# ── calendar_delete_event ───────────────────────────────────────────

@ToolRegistry.register(
    name="calendar_delete_event",
    description="Delete a Google Calendar event by its event ID.",
    category="calendar",
    is_action=True,
    risk_level="high",
)
@tool(args_schema=CalendarDeleteEventArgs)
def calendar_delete_event(event_id: str) -> str:
    """Delete a Google Calendar event."""
    try:
        service = _get_calendar_service()
        service.events().delete(calendarId="primary", eventId=event_id.strip()).execute()
        return f"✅ Event `{event_id[:20]}...` deleted successfully."
    except Exception as exc:
        logger.error(f"calendar_delete_event failed: {exc}")
        return f"❌ Failed to delete event: {exc}"
