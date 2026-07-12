"""
Thread persistence manager for multi-turn conversations.

Saves thread metadata (title, created, last_active, message_count, source)
to a JSON file. Threads are lightweight wrappers — the actual conversation
history lives in LangGraph checkpoints and the memory system.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Dict, List, Any
from threading import Lock

from loguru import logger


@dataclass
class ThreadInfo:
    """Metadata for a conversation thread."""
    thread_id: str
    title: str = ""
    created_at: float = 0.0
    last_active_at: float = 0.0
    message_count: int = 0
    source: str = "web"  # web, discord, telegram, whatsapp, heartbeat, api
    workspace_id: str = ""
    pinned: bool = False
    archived: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ThreadInfo":
        # Only pass known fields
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class ThreadManager:
    """Persistent thread metadata manager.

    Stores thread metadata in a JSON file at ``data/threads.json``.
    Thread IDs are passed to the agent via ``process_query(thread_id=...)``,
    which routes to the correct LangGraph checkpoint and memory partition.
    """

    def __init__(self, persist_path: Optional[Path] = None):
        if persist_path is None:
            try:
                from config import DATA_DIR
                persist_path = DATA_DIR / "threads.json"
            except ImportError:
                persist_path = Path("data/threads.json")
        self._path = persist_path
        self._lock = Lock()
        self._threads: Dict[str, ThreadInfo] = {}
        self._load()

    # ── Persistence ─────────────────────────────────────────────

    def _load(self) -> None:
        """Load threads from disk."""
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for tid, data in raw.items():
                        try:
                            self._threads[tid] = ThreadInfo.from_dict(data)
                        except Exception:
                            continue
                logger.debug(f"Loaded {len(self._threads)} threads from {self._path}")
        except Exception as exc:
            logger.warning(f"Failed to load threads: {exc}")

    def _save(self) -> None:
        """Persist threads to disk."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {tid: t.to_dict() for tid, t in self._threads.items()}
            temp = self._path.with_suffix(self._path.suffix + ".tmp")
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self._path)
        except Exception as exc:
            logger.warning(f"Failed to save threads: {exc}")

    # ── CRUD ────────────────────────────────────────────────────

    def create_thread(
        self,
        title: str = "",
        source: str = "web",
        workspace_id: str = "",
        thread_id: Optional[str] = None,
    ) -> ThreadInfo:
        """Create a new conversation thread."""
        with self._lock:
            tid = thread_id or str(uuid.uuid4())
            now = time.time()
            thread = ThreadInfo(
                thread_id=tid,
                title=(title or "New Session").strip() or "New Session",
                created_at=now,
                last_active_at=now,
                message_count=0,
                source=source,
                workspace_id=workspace_id,
            )
            self._threads[tid] = thread
            self._save()
            logger.info(f"Thread created: {tid} ({title})")
            return thread

    def get_thread(self, thread_id: str) -> Optional[ThreadInfo]:
        """Get thread info by ID."""
        with self._lock:
            return self._threads.get(thread_id)

    def list_threads(
        self,
        include_archived: bool = False,
        source: Optional[str] = None,
        limit: int = 50,
    ) -> List[ThreadInfo]:
        """List threads, sorted by last_active descending."""
        with self._lock:
            threads = list(self._threads.values())

        if not include_archived:
            threads = [t for t in threads if not t.archived]
        if source:
            threads = [t for t in threads if t.source == source]

        threads.sort(key=lambda t: t.last_active_at, reverse=True)
        return threads[:limit]

    def touch_thread(self, thread_id: str, increment_messages: bool = True) -> None:
        """Update last_active timestamp and optionally increment message count."""
        with self._lock:
            thread = self._threads.get(thread_id)
            if thread:
                thread.last_active_at = time.time()
                if increment_messages:
                    thread.message_count += 1
                self._save()

    def record_user_message(self, thread_id: str, message: str) -> Optional[ThreadInfo]:
        """Touch a Session and derive its initial title without another model call."""
        with self._lock:
            thread = self._threads.get(thread_id)
            if thread is None:
                return None
            if thread.message_count == 0 and self._is_placeholder_title(thread.title):
                thread.title = self._title_from_message(message)
            thread.message_count += 1
            thread.last_active_at = time.time()
            self._save()
            return ThreadInfo.from_dict(thread.to_dict())

    @staticmethod
    def _is_placeholder_title(title: str) -> bool:
        value = str(title or "").strip()
        return not value or bool(re.fullmatch(r"(?:new|default)?\s*(?:session|thread)(?:\s+\d+)?", value, re.I))

    @staticmethod
    def _title_from_message(message: str) -> str:
        text = re.sub(r"[`#>*_]+", " ", str(message or ""))
        text = re.sub(r"\s+", " ", text).strip(" .,:;-\t\r\n")
        if not text:
            return "New Session"
        words = text.split()
        title = " ".join(words[:9])
        if len(title) > 64:
            title = title[:64].rsplit(" ", 1)[0] or title[:64]
        return title + ("…" if len(words) > 9 or len(text) > len(title) else "")

    def update_thread(
        self,
        thread_id: str,
        title: Optional[str] = None,
        pinned: Optional[bool] = None,
        archived: Optional[bool] = None,
    ) -> Optional[ThreadInfo]:
        """Update thread metadata."""
        with self._lock:
            thread = self._threads.get(thread_id)
            if not thread:
                return None
            if title is not None:
                thread.title = title
            if pinned is not None:
                thread.pinned = pinned
            if archived is not None:
                thread.archived = archived
            self._save()
            return thread

    def delete_thread(self, thread_id: str) -> bool:
        """Delete a thread by ID."""
        with self._lock:
            if thread_id in self._threads:
                del self._threads[thread_id]
                self._save()
                logger.info(f"Thread deleted: {thread_id}")
                return True
            return False

    def get_or_create(
        self,
        thread_id: Optional[str] = None,
        title: str = "",
        source: str = "web",
        workspace_id: str = "",
    ) -> ThreadInfo:
        """Get existing thread or create a new one."""
        if thread_id:
            existing = self.get_thread(thread_id)
            if existing:
                return existing
        return self.create_thread(
            title=title,
            source=source,
            workspace_id=workspace_id,
            thread_id=thread_id,
        )


# ── Singleton ───────────────────────────────────────────────────────

_thread_manager: Optional[ThreadManager] = None


def get_thread_manager() -> ThreadManager:
    """Get or create the global ThreadManager instance."""
    global _thread_manager
    if _thread_manager is None:
        _thread_manager = ThreadManager()
    return _thread_manager
