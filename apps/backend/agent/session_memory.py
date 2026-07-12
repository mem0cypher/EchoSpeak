"""Continuous durable session memory for EchoSpeak."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def _slug(value: Optional[str]) -> str:
    raw = str(value or "default").strip().lower()
    raw = re.sub(r"[^a-z0-9_.-]+", "_", raw).strip("._")
    return raw or "default"


@dataclass
class SessionMemoryState:
    thread_id: str
    current_subject: str = ""
    current_objective: str = ""
    turn_count: int = 0
    last_updated: float = 0.0
    summary: str = ""
    durable_facts: List[str] = field(default_factory=list)
    user_preferences: List[str] = field(default_factory=list)
    open_tasks: List[str] = field(default_factory=list)
    unresolved_questions: List[str] = field(default_factory=list)
    recent_decisions: List[str] = field(default_factory=list)
    completed_actions: List[str] = field(default_factory=list)


class SessionMemoryDistiller:
    """Maintains a compact per-thread session summary without waiting for compaction."""

    def __init__(self, root: str | Path, *, update_turns: int = 1, max_items: int = 12):
        self.root = Path(root).expanduser()
        self.update_turns = max(1, int(update_turns or 1))
        self.max_items = max(4, int(max_items or 12))

    def path_for(self, thread_id: Optional[str]) -> Path:
        return self.root / "session_memory" / f"{_slug(thread_id)}.json"

    def load(self, thread_id: Optional[str]) -> SessionMemoryState:
        path = self.path_for(thread_id)
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return SessionMemoryState(**{**asdict(SessionMemoryState(thread_id=_slug(thread_id))), **data})
        except Exception:
            pass
        return SessionMemoryState(thread_id=_slug(thread_id))

    def update_turn(
        self,
        *,
        thread_id: Optional[str],
        user_input: str,
        response_text: str,
        current_subject: str = "",
        current_objective: str = "",
        completed_actions: Optional[List[str]] = None,
    ) -> SessionMemoryState:
        state = self.load(thread_id)
        state.turn_count += 1
        if current_subject:
            state.current_subject = str(current_subject).strip()[:240]
        elif not state.current_subject:
            state.current_subject = self._infer_subject(user_input, response_text)
        if current_objective:
            state.current_objective = re.sub(r"\s+", " ", str(current_objective)).strip()[:240]

        # Durable session state is user-authored by default. Mining arbitrary
        # assistant prose here turns a one-off hallucination into future context.
        # Tool-backed project state is persisted separately by active_work.
        user_text = str(user_input or "").strip()
        self._merge_items(state.durable_facts, self._extract_facts(user_text))
        self._merge_items(state.user_preferences, self._extract_preferences(user_text))
        self._merge_items(state.open_tasks, self._extract_tasks(user_text))
        self._merge_items(state.unresolved_questions, self._extract_questions(user_text))
        self._merge_items(state.recent_decisions, self._extract_decisions(user_text))
        self._merge_items(state.completed_actions, list(completed_actions or []))
        state.summary = self._build_summary(state, user_input, response_text)
        state.last_updated = time.time()
        self._save(state, thread_id)
        return state

    def context_for(self, thread_id: Optional[str], max_chars: int = 1200) -> str:
        state = self.load(thread_id)
        if not state.summary and not state.current_subject:
            return ""
        parts: List[str] = []
        if state.current_subject:
            parts.append(f"Current subject: {state.current_subject}")
        if state.summary:
            parts.append(f"Session summary: {state.summary}")
        for label, values in (
            ("Durable facts", state.durable_facts),
            ("Preferences", state.user_preferences),
            ("Open tasks", state.open_tasks),
            ("Unresolved questions", state.unresolved_questions),
            ("Recent decisions", state.recent_decisions),
            ("Completed actions", state.completed_actions),
        ):
            if values:
                parts.append(label + ":\n" + "\n".join(f"- {v}" for v in values[:6]))
        text = "\n".join(parts).strip()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "\n...[trimmed session memory]"
        return text

    def doctor(self, thread_id: Optional[str]) -> Dict[str, Any]:
        path = self.path_for(thread_id)
        state = self.load(thread_id)
        return {
            "enabled": True,
            "path": str(path),
            "exists": path.exists(),
            "last_updated": state.last_updated or None,
            "current_subject": state.current_subject,
            "current_objective": state.current_objective,
            "turn_count": state.turn_count,
            "summary_chars": len(state.summary or ""),
        }

    def _save(self, state: SessionMemoryState, thread_id: Optional[str]) -> None:
        path = self.path_for(thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(asdict(state), indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)

    def _merge_items(self, target: List[str], items: List[str]) -> None:
        seen = {x.lower() for x in target}
        for item in items:
            clean = re.sub(r"\s+", " ", str(item or "")).strip()
            if not clean:
                continue
            key = clean.lower()
            if key not in seen:
                target.insert(0, clean[:240])
                seen.add(key)
        del target[self.max_items :]

    def _build_summary(self, state: SessionMemoryState, user_input: str, response_text: str) -> str:
        subject = state.current_subject or self._infer_subject(user_input, response_text)
        latest = re.sub(r"\s+", " ", str(user_input or "")).strip()[:220]
        if subject and latest:
            return f"Session is focused on {subject}. Latest user request: {latest}"
        return latest or state.summary

    def _infer_subject(self, user_input: str, response_text: str) -> str:
        text = re.sub(r"\s+", " ", str(user_input or response_text or "")).strip()
        text = re.sub(r"^(can you|please|ok|okay|so|now)\b", "", text, flags=re.IGNORECASE).strip()
        return text[:180]

    def _extract_facts(self, text: str) -> List[str]:
        out = []
        patterns = [
            r"\bmy name is ([A-Za-z][A-Za-z0-9_-]{1,32})\b",
            r"\bremember(?: that)? (.+?)(?:[.!?]|$)",
        ]
        for pat in patterns:
            for m in re.finditer(pat, text, flags=re.IGNORECASE):
                out.append(m.group(0).strip())
        return out

    def _extract_preferences(self, text: str) -> List[str]:
        out = []
        for m in re.finditer(r"\bI (?:like|prefer|want|don't want|do not want) (.+?)(?:[.!?]|$)", text, flags=re.IGNORECASE):
            out.append(m.group(0).strip())
        return out

    def _extract_tasks(self, text: str) -> List[str]:
        out = []
        for m in re.finditer(r"\b(?:todo|task|need to|we need to|next(?: step)? is) (.+?)(?:[.!?]|$)", text, flags=re.IGNORECASE):
            out.append(m.group(0).strip())
        return out

    def _extract_questions(self, text: str) -> List[str]:
        return [q.strip() for q in re.findall(r"([^?.!]{8,}\?)", text)[:6]]

    def _extract_decisions(self, text: str) -> List[str]:
        out = []
        for m in re.finditer(r"\b(?:decided|decision|we will|we should|we are going to) (.+?)(?:[.!?]|$)", text, flags=re.IGNORECASE):
            out.append(m.group(0).strip())
        return out
