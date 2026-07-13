"""Change checkpoints (undo system) for EchoSpeak.

Creates a backup of files before mutations (file_write, file_delete)
and provides an undo mechanism to revert to the last state.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger

# Use the same configurable durable root as approvals, executions, active work,
# and memory. Tests set this before imports and therefore cannot touch live data.
try:
    from config import DATA_DIR
except Exception:  # pragma: no cover - import-safe fallback
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CHECKPOINTS_DIR = Path(DATA_DIR) / "checkpoints"


def _safe_init() -> None:
    try:
        CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _load_index() -> List[Dict[str, Any]]:
    _safe_init()
    index_path = CHECKPOINTS_DIR / "checkpoints_index.json"
    if not index_path.exists():
        return []
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save_index(index: List[Dict[str, Any]]) -> None:
    _safe_init()
    index_path = CHECKPOINTS_DIR / "checkpoints_index.json"
    try:
        # Keep index capped to latest 50 entries
        trimmed = index[-50:]
        index_path.write_text(json.dumps(trimmed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to save checkpoints index: {}", exc)


def get_last_checkpoint(thread_id: str = "default", project_root: str = "") -> Optional[Dict[str, Any]]:
    """Return a copy of the checkpoint undo would consume, without mutating it."""
    wanted_thread = str(thread_id or "default")
    wanted_root = str(project_root or "").strip()
    for candidate in reversed(_load_index()):
        if str(candidate.get("thread_id") or "legacy") != wanted_thread:
            continue
        if wanted_root and str(candidate.get("project_root") or "").strip() != wanted_root:
            continue
        return dict(candidate)
    return None


def create_checkpoint(file_path: str, reason: str = "before_write") -> Optional[str]:
    """Create a backup checkpoint of a file before it is modified or deleted.

    Returns the backup file path, or None if file doesn't exist or error.
    """
    _safe_init()
    try:
        p = Path(file_path).resolve()
        if not p.is_file():
            return None

        # Read original content
        content = p.read_text(encoding="utf-8", errors="replace")

        # Generate backup filename
        timestamp = int(time.time() * 1000)
        filename = f"{timestamp}_{p.name}.bak"
        backup_path = CHECKPOINTS_DIR / filename

        # Save backup content
        backup_path.write_text(content, encoding="utf-8")

        # Log in index
        index = _load_index()
        try:
            from agent.tools import get_tool_execution_context
            context = get_tool_execution_context()
        except Exception:
            context = {}
        index.append({
            "timestamp": timestamp,
            "original_path": str(p),
            "backup_path": str(backup_path),
            "reason": reason,
            "filename": p.name,
            "thread_id": str(context.get("thread_id") or "legacy"),
            "project_root": str(context.get("project_root") or ""),
            "execution_id": str(context.get("execution_id") or ""),
        })
        _save_index(index)

        logger.info("[Checkpoints] Created checkpoint for {} -> {}", p.name, filename)
        return str(backup_path)
    except Exception as exc:
        logger.warning("[Checkpoints] Failed to create checkpoint: {}", exc)
        return None


def undo_last_change(thread_id: str = "default", project_root: str = "") -> str:
    """Restore the last saved checkpoint and revert the file.

    Returns status description message.
    """
    _safe_init()
    index = _load_index()
    if not index:
        return "No checkpoints found. Nothing to undo."

    wanted_thread = str(thread_id or "default")
    wanted_root = str(project_root or "").strip()
    match_index = None
    for idx in range(len(index) - 1, -1, -1):
        candidate = index[idx]
        if str(candidate.get("thread_id") or "legacy") != wanted_thread:
            continue
        candidate_root = str(candidate.get("project_root") or "").strip()
        if wanted_root and candidate_root != wanted_root:
            continue
        match_index = idx
        break
    if match_index is None:
        return "No checkpoint exists for this thread and project scope. Nothing to undo."
    entry = index.pop(match_index)
    orig_path_str = entry.get("original_path")
    backup_path_str = entry.get("backup_path")

    if not orig_path_str or not backup_path_str:
        return "Invalid checkpoint entry. Undo failed."

    orig_p = Path(orig_path_str)
    backup_p = Path(backup_path_str)
    if wanted_root:
        try:
            if os.path.commonpath([str(Path(wanted_root).resolve()), str(orig_p.resolve())]) != str(Path(wanted_root).resolve()):
                return "Checkpoint path is outside the current thread project scope. Undo blocked."
        except Exception:
            return "Checkpoint scope could not be verified. Undo blocked."

    if not backup_p.is_file():
        # Clean up stale index and try again
        _save_index(index)
        return f"Backup file {backup_p.name} not found. Re-run undo if older checkpoints exist."

    try:
        # Read backup content
        content = backup_p.read_text(encoding="utf-8")

        # Re-write original file
        orig_p.parent.mkdir(parents=True, exist_ok=True)
        orig_p.write_text(content, encoding="utf-8")

        # Remove the backup file from disk
        try:
            backup_p.unlink()
        except Exception:
            pass

        # Save updated index
        _save_index(index)

        logger.info("[Checkpoints] Undid change to {}", orig_p.name)
        return f"Successfully reverted {orig_p.name} to last checkpoint."
    except Exception as exc:
        logger.error("[Checkpoints] Reverting to checkpoint failed: {}", exc)
        return f"Failed to revert file: {exc}"
