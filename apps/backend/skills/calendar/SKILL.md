Google Calendar integration for EchoSpeak.

## When to use

When the user asks about:
- "What's on my schedule today/this week?"
- "Do I have any meetings?"
- "Create a meeting with X at Y time"
- "Delete/cancel my 3pm meeting"
- "Am I free on Friday afternoon?"
- "What's my calendar look like?"

## Tool reference

### calendar_get_today
Quick view of today's events. Use this as the default when the user asks about their schedule without specifying a date range.

### calendar_list_events
List events within a date range. Use when the user asks about a specific period ("this week", "next Monday", "March 15th").

### calendar_create_event
Create a new calendar event. Requires title, start time, and end time. Optional: description, location, attendees. This is an action tool — requires confirmation.

### calendar_delete_event
Delete an event by ID. First use calendar_list_events or calendar_get_today to find the event, then pass the event ID. This is an action tool — requires confirmation.

## Requirements

The user must set `ALLOW_CALENDAR=true` and provide Google Calendar OAuth2 credentials via `GOOGLE_CALENDAR_CREDENTIALS_PATH`.

## Output style

Keep calendar summaries concise. Use bullet points for event lists. Include time, title, and location if available. For today's schedule, group by morning/afternoon/evening.
