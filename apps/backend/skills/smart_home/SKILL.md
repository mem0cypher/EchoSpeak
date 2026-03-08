Smart Home control via Home Assistant for EchoSpeak.

## When to use

When the user asks to:
- "Turn on the living room lights"
- "Turn off the bedroom fan"
- "What's the temperature in the house?"
- "List my smart devices"
- "Set the scene to movie night"
- "Is the garage door open?"

## Tool reference

### ha_list_entities
List all Home Assistant entities, optionally filtered by domain (light, switch, sensor, climate, etc.). Good for discovery.

### ha_get_state
Get the current state of a specific entity by entity_id (e.g. "light.living_room"). Returns state, attributes, and last updated time.

### ha_turn_on
Turn on a device by entity_id. Supports optional parameters like brightness for lights or temperature for climate. This is an action tool.

### ha_turn_off
Turn off a device by entity_id. This is an action tool.

### ha_call_service
Call any Home Assistant service with custom data. Used for advanced operations like activating scenes, setting cover positions, or triggering automations. This is an action tool.

## Requirements

Set `ALLOW_HOME_ASSISTANT=true`, `HOME_ASSISTANT_URL` (your HA instance URL), and `HOME_ASSISTANT_TOKEN` (long-lived access token from HA).

## Output style

Keep responses natural: "Done, living room lights are on." Use device friendly names when possible. For sensor readings, include units.
