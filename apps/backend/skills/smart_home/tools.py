"""
Home Assistant tools — control smart home devices via the HA REST API.

Requires:
  pip install requests  (already a dep)
  ALLOW_HOME_ASSISTANT=true
  HOME_ASSISTANT_URL=http://homeassistant.local:8123
  HOME_ASSISTANT_TOKEN=eyJ...
"""

from __future__ import annotations

import json
from typing import Optional, Dict, Any

import requests
from loguru import logger
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from agent.tool_registry import ToolRegistry

# ── Helpers ──────────────────────────────────────────────────────────

def _ha_config():
    """Get HA URL and auth headers."""
    try:
        from config import config
    except ImportError:
        raise RuntimeError("Config not available")

    if not getattr(config, "allow_home_assistant", False):
        raise RuntimeError("Home Assistant is disabled. Set ALLOW_HOME_ASSISTANT=true in .env")

    url = getattr(config, "home_assistant_url", "").rstrip("/")
    token = getattr(config, "home_assistant_token", "")

    if not url or not token:
        raise RuntimeError("HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN must be set in .env")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    return url, headers


def _ha_get(path: str) -> Any:
    """GET request to HA REST API."""
    url, headers = _ha_config()
    resp = requests.get(f"{url}/api/{path}", headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _ha_post(path: str, data: dict | None = None) -> Any:
    """POST request to HA REST API."""
    url, headers = _ha_config()
    resp = requests.post(f"{url}/api/{path}", headers=headers, json=data or {}, timeout=10)
    resp.raise_for_status()
    return resp.json() if resp.text else {}


def _friendly_name(entity: dict) -> str:
    """Get the friendly name of an entity."""
    return entity.get("attributes", {}).get("friendly_name", entity.get("entity_id", "unknown"))


# ── Schemas ─────────────────────────────────────────────────────────

class HAListEntitiesArgs(BaseModel):
    domain: Optional[str] = Field(
        default=None,
        description="Filter by domain: 'light', 'switch', 'sensor', 'climate', 'cover', 'media_player', etc.",
    )
    limit: int = Field(default=30, description="Max entities to return")


class HAGetStateArgs(BaseModel):
    entity_id: str = Field(description="Entity ID, e.g. 'light.living_room'")


class HATurnOnArgs(BaseModel):
    entity_id: str = Field(description="Entity ID to turn on, e.g. 'light.living_room'")
    brightness: Optional[int] = Field(default=None, description="Brightness 0-255 (lights only)")
    color_temp: Optional[int] = Field(default=None, description="Color temperature in mireds (lights only)")
    temperature: Optional[float] = Field(default=None, description="Target temperature (climate only)")


class HATurnOffArgs(BaseModel):
    entity_id: str = Field(description="Entity ID to turn off")


class HACallServiceArgs(BaseModel):
    domain: str = Field(description="Service domain, e.g. 'light', 'scene', 'automation'")
    service: str = Field(description="Service name, e.g. 'turn_on', 'activate', 'trigger'")
    entity_id: Optional[str] = Field(default=None, description="Target entity ID")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Additional service data as JSON")


# ── ha_list_entities ────────────────────────────────────────────────

@ToolRegistry.register(
    name="ha_list_entities",
    description="List Home Assistant entities, optionally filtered by domain (light, sensor, etc.).",
    category="smart_home",
    risk_level="safe",
)
@tool(args_schema=HAListEntitiesArgs)
def ha_list_entities(domain: Optional[str] = None, limit: int = 30) -> str:
    """List Home Assistant entities."""
    try:
        states = _ha_get("states")

        if domain:
            domain = domain.strip().lower()
            states = [s for s in states if s.get("entity_id", "").startswith(f"{domain}.")]

        if not states:
            return f"🏠 No entities found" + (f" for domain `{domain}`" if domain else "") + "."

        # Group by domain
        grouped: dict = {}
        for entity in states[:limit]:
            eid = entity.get("entity_id", "")
            d = eid.split(".")[0] if "." in eid else "other"
            grouped.setdefault(d, []).append(entity)

        lines = [f"🏠 **Smart Home Entities** ({len(states)} total)\n"]
        for d, entities in sorted(grouped.items()):
            lines.append(f"**{d}** ({len(entities)})")
            for e in entities:
                name = _friendly_name(e)
                state = e.get("state", "unknown")
                icon = "🟢" if state in ("on", "home", "playing") else "🔴" if state in ("off", "away", "idle") else "🟡"
                lines.append(f"  {icon} `{e.get('entity_id', '')}` — {name}: **{state}**")

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"ha_list_entities failed: {exc}")
        return f"❌ Failed to list entities: {exc}"


# ── ha_get_state ────────────────────────────────────────────────────

@ToolRegistry.register(
    name="ha_get_state",
    description="Get the current state and attributes of a Home Assistant entity.",
    category="smart_home",
    risk_level="safe",
)
@tool(args_schema=HAGetStateArgs)
def ha_get_state(entity_id: str) -> str:
    """Get the state of an entity."""
    try:
        entity = _ha_get(f"states/{entity_id.strip()}")
        name = _friendly_name(entity)
        state = entity.get("state", "unknown")
        attrs = entity.get("attributes", {})
        last_changed = entity.get("last_changed", "")[:19].replace("T", " ")

        lines = [f"🏠 **{name}** (`{entity_id}`)\n"]
        lines.append(f"State: **{state}**")
        lines.append(f"Last changed: {last_changed}")

        # Show key attributes
        skip_keys = {"friendly_name", "icon", "entity_picture"}
        for key, val in attrs.items():
            if key in skip_keys:
                continue
            if isinstance(val, (list, dict)):
                continue  # Skip complex attrs
            lines.append(f"  • {key}: `{val}`")

        return "\n".join(lines)
    except Exception as exc:
        logger.error(f"ha_get_state failed: {exc}")
        return f"❌ Failed to get state for `{entity_id}`: {exc}"


# ── ha_turn_on ──────────────────────────────────────────────────────

@ToolRegistry.register(
    name="ha_turn_on",
    description="Turn on a Home Assistant entity (light, switch, etc.) with optional parameters.",
    category="smart_home",
    is_action=True,
    risk_level="moderate",
)
@tool(args_schema=HATurnOnArgs)
def ha_turn_on(
    entity_id: str,
    brightness: Optional[int] = None,
    color_temp: Optional[int] = None,
    temperature: Optional[float] = None,
) -> str:
    """Turn on a device."""
    try:
        domain = entity_id.strip().split(".")[0] if "." in entity_id else "homeassistant"
        data: dict = {"entity_id": entity_id.strip()}
        if brightness is not None:
            data["brightness"] = max(0, min(255, brightness))
        if color_temp is not None:
            data["color_temp"] = color_temp
        if temperature is not None:
            data["temperature"] = temperature

        service = "turn_on"
        if domain == "climate":
            service = "set_temperature" if temperature else "turn_on"

        _ha_post(f"services/{domain}/{service}", data)
        extras = []
        if brightness is not None:
            extras.append(f"brightness {brightness}")
        if temperature is not None:
            extras.append(f"temp {temperature}°")
        extra_str = f" ({', '.join(extras)})" if extras else ""
        return f"✅ `{entity_id}` turned **on**{extra_str}."
    except Exception as exc:
        logger.error(f"ha_turn_on failed: {exc}")
        return f"❌ Failed to turn on `{entity_id}`: {exc}"


# ── ha_turn_off ─────────────────────────────────────────────────────

@ToolRegistry.register(
    name="ha_turn_off",
    description="Turn off a Home Assistant entity (light, switch, etc.).",
    category="smart_home",
    is_action=True,
    risk_level="moderate",
)
@tool(args_schema=HATurnOffArgs)
def ha_turn_off(entity_id: str) -> str:
    """Turn off a device."""
    try:
        domain = entity_id.strip().split(".")[0] if "." in entity_id else "homeassistant"
        _ha_post(f"services/{domain}/turn_off", {"entity_id": entity_id.strip()})
        return f"✅ `{entity_id}` turned **off**."
    except Exception as exc:
        logger.error(f"ha_turn_off failed: {exc}")
        return f"❌ Failed to turn off `{entity_id}`: {exc}"


# ── ha_call_service ─────────────────────────────────────────────────

@ToolRegistry.register(
    name="ha_call_service",
    description="Call any Home Assistant service — scenes, automations, covers, media players, etc.",
    category="smart_home",
    is_action=True,
    risk_level="high",
)
@tool(args_schema=HACallServiceArgs)
def ha_call_service(
    domain: str,
    service: str,
    entity_id: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
) -> str:
    """Call a HA service."""
    try:
        payload: dict = {}
        if entity_id:
            payload["entity_id"] = entity_id.strip()
        if data:
            payload.update(data)

        _ha_post(f"services/{domain.strip()}/{service.strip()}", payload)
        target = f" on `{entity_id}`" if entity_id else ""
        return f"✅ Service `{domain}.{service}` called{target}."
    except Exception as exc:
        logger.error(f"ha_call_service failed: {exc}")
        return f"❌ Failed to call `{domain}.{service}`: {exc}"
