"""Project notes manager (v8.0) for EchoSpeak.

Maintains a persistent `PROJECT_NOTES.md` file at the root of the active
coding project containing goals, current phase, files touched, and next steps.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from loguru import logger


def update_project_notes(project_path: str, state: Any) -> None:
    """Write a human-readable projection of authoritative workflow state.

    Project notes never derive objectives or action history from conversational
    text. ThreadSessionState and verified active-work events remain authoritative.
    """
    if not project_path:
        return
    try:
        root = Path(project_path).resolve()
        if not root.is_dir():
            return

        notes_path = root / "PROJECT_NOTES.md"

        # Extract values safely
        goal = getattr(state, "objective", "") or getattr(state, "goal", "") or f"Develop {root.name}"
        phase = getattr(state, "phase", "") or "idle"
        next_step = getattr(state, "next_step", "") or "Proceed to next task"
        files = getattr(state, "files_known", []) or []
        last_msg = getattr(state, "last_verified_action", "") or ""

        # Format files list
        files_str = "\n".join(f"- {f}" for f in sorted(files)[:30]) if files else "- None registered yet"

        content = f"""# Project Notes: {root.name}

## Objective
{goal}

## Current Status
- **Current Phase**: `{phase}`
- **Last Action / Request**: *"{last_msg}"*
- **Next Step**: {next_step}

## Project Structure / Known Files
{files_str}

---
*These notes are maintained automatically by EchoSpeak. You can edit them to update goals or inject context.*
"""
        # Save to disk
        notes_path.write_text(content.strip() + "\n", encoding="utf-8")
        logger.info("[Project Notes] Updated PROJECT_NOTES.md in {}", root.name)
    except Exception as exc:
        logger.warning("[Project Notes] Failed to update notes: {}", exc)
