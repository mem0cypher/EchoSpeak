"""Governed deterministic video proposals — proposal-only, no creative judgment.

Used when a request maps to exactly one registered mutation tool and all
arguments are fully derivable from structured editor state (selection,
playhead, revision). Never invents success; never applies without approval.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from agent.skill_selection import detect_direct_tool


# Creative / multi-step verbs → never deterministic.
_CREATIVE = re.compile(
    r"\b("
    r"better|cinematic|best|appropriate|generate|create|suggest|choose|"
    r"improve|style|artistic|highlight reel|rough cut|silence|caption|b-?roll|"
    r"workflow|pipeline|and then|also "
    r")\b",
    re.I,
)


def is_deterministic_video_intent(user_text: str) -> bool:
    text = str(user_text or "").strip()
    if not text or _CREATIVE.search(text):
        return False
    return detect_direct_tool(text, domain="video") == "video_propose_operations"


def _volume_value(user_text: str) -> Optional[float]:
    m = re.search(r"\b(?:volume|gain)\s*(?:to|=|:)?\s*(\d+(?:\.\d+)?)\s*%?", user_text or "", re.I)
    if not m:
        m = re.search(r"\b(\d+(?:\.\d+)?)\s*%\s*(?:volume|gain)?\b", user_text or "", re.I)
    if not m:
        if re.search(r"\bmute\b", user_text or "", re.I):
            return 0.0
        return None
    val = float(m.group(1))
    if val > 1.5:  # treat as percent
        val = val / 100.0
    return max(0.0, min(2.0, val))


def build_deterministic_operations(
    *,
    user_text: str,
    document_revision: int,
    selected_clip_ids: list[str],
    playhead_ticks: Optional[str] = None,
) -> dict[str, Any]:
    """Return {ok, operations, tool, error_code, reason} for proposal-only path."""
    text = str(user_text or "").strip()
    if not is_deterministic_video_intent(text):
        return {
            "ok": False,
            "error_code": "not_deterministic",
            "reason": "Intent requires model judgment or is not a direct video op",
            "operations": [],
            "tool": "",
        }
    tool = "video_propose_operations"
    clips = [str(c).strip() for c in (selected_clip_ids or []) if str(c).strip()]
    if not clips:
        return {
            "ok": False,
            "error_code": "missing_selection",
            "reason": "No selected clip; cannot derive exact arguments",
            "operations": [],
            "tool": tool,
        }
    clip_id = clips[0]
    rev = int(document_revision)
    low = text.casefold()

    if re.search(r"\b(split|cut)\b", low) and re.search(r"\b(clip|playhead|here|at)\b", low):
        ticks = str(playhead_ticks or "").strip()
        if not ticks:
            return {
                "ok": False,
                "error_code": "missing_playhead",
                "reason": "Playhead required for split",
                "operations": [],
                "tool": tool,
            }
        op = {
            "operation_type": "split_clip",
            "expected_revision": rev,
            "payload": {
                "clip_id": clip_id,
                "right_clip_id": f"{clip_id}_r_{uuid.uuid4().hex[:8]}",
                "at": {"ticks": ticks, "time_base": {"numerator": 1, "denominator": 1000}},
            },
        }
        return {"ok": True, "operations": [op], "tool": tool, "error_code": "", "reason": "split_selected_at_playhead"}

    if re.search(r"\b(delete|remove)\b", low) and re.search(r"\bclip\b", low):
        op = {
            "operation_type": "delete_clip",
            "expected_revision": rev,
            "payload": {"clip_id": clip_id},
        }
        return {"ok": True, "operations": [op], "tool": tool, "error_code": "", "reason": "delete_selected_clip"}

    if re.search(r"\b(volume|gain|mute)\b", low):
        vol = _volume_value(text)
        if vol is None:
            return {
                "ok": False,
                "error_code": "missing_argument",
                "reason": "Volume value not explicit",
                "operations": [],
                "tool": tool,
            }
        op = {
            "operation_type": "set_clip_volume",
            "expected_revision": rev,
            "payload": {"clip_id": clip_id, "volume": vol},
        }
        return {"ok": True, "operations": [op], "tool": tool, "error_code": "", "reason": "set_selected_volume"}

    return {
        "ok": False,
        "error_code": "not_deterministic",
        "reason": "No exact operation template matched",
        "operations": [],
        "tool": tool,
    }


def provider_tool_capability_matrix(
    *,
    provider: str,
    native_tool_calling_enabled: bool,
    langgraph_available: bool,
    agent_executor_available: bool,
    lmstudio_tool_calling: bool,
    disable_native_tool_calling: bool,
) -> dict[str, Any]:
    """Honest capability report for operators — not a runtime gate."""
    p = str(provider or "").lower()
    native = bool(native_tool_calling_enabled) and not disable_native_tool_calling
    # Local OpenAI-compatible stacks often have weak/no tool calling in practice.
    localish = p in {"lmstudio", "ollama", "localai", "llamacpp", "vllm", "openai_compatible"}
    return {
        "provider": provider,
        "native_tool_calls": bool(native and (langgraph_available or agent_executor_available)),
        "native_tool_calls_attempted": native,
        "langgraph_available": bool(langgraph_available),
        "agent_executor_available": bool(agent_executor_available),
        "validated_json_plan_output": True,  # action parser always available
        "deterministic_direct_tool_fallback": True,  # this module
        "prose_only_risk": bool(localish or not (langgraph_available or agent_executor_available)),
        "lmstudio_tool_calling_flag": bool(lmstudio_tool_calling),
        "disable_native_tool_calling": bool(disable_native_tool_calling),
        "notes": (
            "Deterministic fallback is proposal-only and only for unambiguous "
            "ops with full structured selection. Prose is never converted into a mutation."
        ),
    }
