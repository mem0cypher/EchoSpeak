"""Durable active-work state for multi-turn goals (coding projects, unfinished tools).

Problem this solves
-------------------
Every user turn can re-list Desktop, re-ask \"what kind of game?\", and forget
the pin/path/phase because agent memory is per-instance and Stage-4 models
don't reliably carry \"where we left off\".

This module stores a compact, thread-scoped work fingerprint:
  project path · goal · phase · files known · next step · last evidence

It is the code-enforced continuity layer — not prompt-only memory.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from config import DATA_DIR
except Exception:
    DATA_DIR = Path("data")

ACTIVE_WORK_DIR = Path(DATA_DIR) / "active_work"


def _slug(thread_id: Optional[str]) -> str:
    raw = str(thread_id or "default").strip().lower()
    raw = re.sub(r"[^a-z0-9_.-]+", "_", raw).strip("._")
    return raw or "default"


@dataclass
class ActiveWorkState:
    """Persistent multi-turn work fingerprint for a chat thread."""

    thread_id: str = "default"
    kind: str = ""  # coding_project | research | ""
    phase: str = "idle"  # idle | inspect | ready | implement | blocked | done
    project_path: str = ""
    project_name: str = ""
    goal: str = ""
    last_user_message: str = ""
    next_step: str = ""
    files_known: List[str] = field(default_factory=list)
    listing: str = ""
    code_digest: str = ""  # short samples / structure notes
    # Session-scoped coding memory (multi-turn incremental work)
    features_present: List[str] = field(default_factory=list)  # e.g. health, score, death_screen
    file_mtimes: Dict[str, float] = field(default_factory=dict)  # basename or path → mtime
    last_tools: List[str] = field(default_factory=list)
    stall_count: int = 0
    updated_at: float = 0.0

    def is_active(self) -> bool:
        return bool(self.kind and self.phase not in ("", "idle", "done") and self.project_path)

    def same_project(self, project_path: str) -> bool:
        try:
            a = Path(str(self.project_path or "")).resolve()
            b = Path(str(project_path or "")).resolve()
            return bool(self.project_path) and a == b
        except Exception:
            return bool(self.project_path) and str(self.project_path).lower() == str(project_path or "").lower()

    def has_usable_scan(self) -> bool:
        """True when we can skip a full re-inspect on a follow-up."""
        return bool(
            self.project_path
            and self.files_known
            and self.code_digest
            and len(self.code_digest) > 80
            and self.phase not in ("", "idle")
        )

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def request_continues_project(user_input: str, state: "ActiveWorkState") -> bool:
    """Hard relevance gate: only resume stored project when the ask is about THAT project.

    Critical safety: never reuse an unrelated prior pin (e.g. 2d-shooter-game) for a
    brand-new app request (e.g. to-do list). False → treat as fresh project.
    """
    if not state or not state.project_path:
        return False
    text = re.sub(r"\s+", " ", str(user_input or "").strip().lower())
    if not text:
        return False

    name = (state.project_name or Path(state.project_path).name or "").lower()
    name_tokens = {t for t in re.split(r"[-_\s.]+", name) if len(t) >= 2}

    # Explicit continuity: user names the stored project / path
    if name and name in text.replace(" ", "-"):
        return True
    if name_tokens and all(tok in text for tok in name_tokens if tok not in {"2d", "app", "game", "project"}):
        # e.g. "shooter" "game" both in text for 2d-shooter-game
        strong = [t for t in name_tokens if t not in {"2d", "app", "game", "project", "the", "my"}]
        if strong and any(t in text for t in strong):
            # still need no conflicting new-product intent below
            pass

    # Explicit NEW project language → never resume
    if re.search(
        r"\b("
        r"new project|brand[- ]?new|from scratch|start over|different project|"
        r"another project|separate project|instead build|instead create|"
        r"build me|create me|make me|scaffold|greenfield"
        r")\b",
        text,
    ):
        return False

    # "build/create/make a|an <product>" where product is NOT the stored project
    m = re.search(
        r"\b(?:build|create|make|scaffold|start)\s+(?:me\s+|us\s+)?(?:a|an|the|my|our)\s+([a-z0-9][\w\s-]{1,48})",
        text,
    )
    if m:
        product = re.sub(r"\s+", " ", m.group(1).strip().lower())
        product = re.sub(r"\b(app|application|project|website|site|tool|game)\b", "", product).strip()
        product_tokens = {t for t in re.split(r"[-_\s]+", product) if len(t) >= 3}
        # If product shares almost no tokens with stored name → new project
        if product_tokens and name_tokens:
            overlap = product_tokens & name_tokens
            # allow generic words
            generic = {"app", "game", "web", "simple", "basic", "new", "the", "for"}
            overlap -= generic
            product_tokens -= generic
            if product_tokens and not overlap:
                return False
        elif product_tokens and not name_tokens:
            return False

    # Domain conflict: stored looks like a game, user asks for todo/list/notes/etc.
    stored_blob = " ".join(
        [
            name,
            " ".join(state.files_known or [])[:200],
            (state.goal or "")[:200],
            (state.code_digest or "")[:400],
        ]
    ).lower()
    is_game_stored = bool(
        re.search(r"\b(shooter|game\.js|canvas|enemy|npc|bullet|player\.hp)\b", stored_blob)
        or re.search(r"\bgame\b", name)
    )
    is_todo_ask = bool(
        re.search(r"\b(to-?do|todo|task list|checklist|notes app|habit tracker|kanban)\b", text)
    )
    is_other_app = bool(
        re.search(
            r"\b(weather app|chat app|blog|calculator|dashboard|crm|portfolio|landing page)\b",
            text,
        )
    )
    if is_game_stored and (is_todo_ask or is_other_app):
        return False
    if is_todo_ask and not re.search(r"\b(to-?do|todo|task)\b", stored_blob):
        return False

    # Continuity language without a conflicting new product
    if re.search(
        r"\b("
        r"also|same project|this project|the project|that project|"
        r"continue|keep going|next|follow[- ]?up|while you'?re at it|"
        r"in the game|to the game|our game|the code we|what we (?:just |already )?built"
        r")\b",
        text,
    ):
        # Block if they also introduced a clearly different product noun phrase
        if m and product_tokens and name_tokens and not (product_tokens & name_tokens):
            return False
        return True

    # Feature edit that matches stored features / domain (follow-up style)
    if state.features_present:
        for feat in state.features_present:
            ft = str(feat or "").lower()
            if ft and ft in text:
                return True

    # Default: NO resume unless clearly continuous — safer than silent wrong pin
    # Short referential follow-ups without new product nouns
    if re.search(r"\b(add|fix|edit|change|update|implement)\b", text) and not m:
        if re.search(r"\b(it|this|that|there|here)\b", text) or len(text.split()) <= 14:
            # still reject domain flip
            if is_game_stored and is_todo_ask:
                return False
            return True

    return False


def infer_new_project_slug(user_input: str) -> str:
    """Slug for a brand-new Desktop project folder from the user utterance."""
    text = re.sub(r"\s+", " ", str(user_input or "").strip().lower())
    m = re.search(
        r"\b(?:build|create|make|scaffold|start)\s+(?:me\s+|us\s+)?(?:a|an|the|my|our)\s+([a-z0-9][\w\s-]{1,48})",
        text,
    )
    raw = m.group(1) if m else ""
    if not raw:
        # fallback: last noun-ish chunk
        raw = re.sub(r"^(please|can you|could you|lets|let's)\s+", "", text)[:48]
    raw = re.sub(
        r"\b("
        r"app|application|project|website|site|for me|please|"
        r"brand[- ]?new|on my desktop|on the desktop|from scratch"
        r")\b",
        " ",
        raw,
    )
    slug = re.sub(r"[^a-z0-9]+", "-", raw.strip()).strip("-")[:48]
    return slug or "new-project"

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ActiveWorkStore:
    """Load/save ActiveWorkState per thread on disk."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or ACTIVE_WORK_DIR)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, thread_id: Optional[str]) -> Path:
        return self.root / f"{_slug(thread_id)}.json"

    def load(self, thread_id: Optional[str]) -> ActiveWorkState:
        path = self.path_for(thread_id)
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    base = asdict(ActiveWorkState(thread_id=_slug(thread_id)))
                    base.update({k: v for k, v in data.items() if k in base})
                    return ActiveWorkState(**base)
        except Exception:
            pass
        return ActiveWorkState(thread_id=_slug(thread_id))

    def save(self, state: ActiveWorkState, thread_id: Optional[str] = None) -> None:
        tid = thread_id or state.thread_id
        state.thread_id = _slug(tid)
        state.updated_at = time.time()
        path = self.path_for(tid)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Cap large fields for disk
        payload = state.as_dict()
        payload["listing"] = str(payload.get("listing") or "")[:4000]
        payload["code_digest"] = str(payload.get("code_digest") or "")[:8000]
        payload["goal"] = str(payload.get("goal") or "")[:500]
        payload["next_step"] = str(payload.get("next_step") or "")[:500]
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def clear(self, thread_id: Optional[str]) -> None:
        path = self.path_for(thread_id)
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

    def context_block(self, thread_id: Optional[str], max_chars: int = 3500) -> str:
        """Compact block for system/context injection every turn."""
        s = self.load(thread_id)
        if not s.is_active() and not s.project_path:
            return ""
        lines = [
            "=== ACTIVE WORK (durable — do not restart from scratch) ===",
            f"kind: {s.kind or 'unknown'}",
            f"phase: {s.phase}",
            f"project_path: {s.project_path}",
            f"project_name: {s.project_name}",
            f"goal: {s.goal or '(none set yet)'}",
            f"next_step: {s.next_step or 'continue from project files'}",
        ]
        if s.files_known:
            lines.append("files_known: " + ", ".join(s.files_known[:20]))
        if s.features_present:
            lines.append("features_already_present: " + ", ".join(s.features_present[:20]))
        if s.listing:
            lines.append("listing:\n" + s.listing[:800])
        if s.code_digest:
            lines.append("code_digest:\n" + s.code_digest[:2000])
        lines.append(
            "RULES: Project is already open with prior scan state. "
            "Do NOT re-list Desktop. Do NOT full re-inspect every file from scratch. "
            "On follow-ups: use files_known + features_already_present + code_digest; "
            "only re-read files relevant to the new ask or marked stale. "
            "Plan = changes given current state, not a brand-new project plan."
        )
        lines.append("=== END ACTIVE WORK ===")
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "\n...[trimmed active work]"
        return text


def infer_goal_from_user(user_input: str) -> str:
    """Short goal label from the user utterance."""
    t = re.sub(r"\s+", " ", str(user_input or "").strip())
    if not t:
        return ""
    # Prefer the concrete ask after soft openers
    t = re.sub(
        r"(?i)^(can we|lets|let's|please|okay|ok|start|start on|work on)\s+",
        "",
        t,
    ).strip()
    return t[:240]


def next_step_for_phase(phase: str, *, has_samples: bool, goal: str) -> str:
    if phase in ("inspect", "ready") and has_samples:
        if goal and re.search(r"(?i)\b(edit|add|fix|score|code|implement|change)\b", goal):
            return f"Implement in project files: {goal[:160]}"
        return "Project inspected — wait for the next edit request, or propose concrete next edits."
    if phase == "implement":
        return f"Continue implementation: {goal[:160]}" if goal else "Continue editing project files."
    if phase == "blocked":
        return "Recover: re-read key files under project_path, then continue the goal."
    return "Continue the active project goal."


def looks_like_desktop_relist(answer: str) -> bool:
    """True when the model re-dumped Desktop siblings instead of working inside the pin."""
    low = re.sub(r"\s+", " ", str(answer or "").lower())
    if not low:
        return False
    siblings = len(
        re.findall(
            r"\b(echospeak|win11debloat|antigravity|2d-shooter-game)\b",
            low,
        )
    )
    if siblings >= 2 and re.search(r"\b(desktop|folder|see|found|list)\b", low):
        return True
    if re.search(r"\b(what kind of (?:game|project)|gotta see what|before i can try)\b", low):
        return True
    return False


def goal_looks_incomplete(state: ActiveWorkState, answer: str, *, tools_ran: Optional[List[str]] = None) -> bool:
    """Heuristic: active work still open and this turn did not finish the goal."""
    if not state.is_active():
        return False
    if state.phase in ("done", "idle"):
        return False
    low = re.sub(r"\s+", " ", str(answer or "").lower())
    tools = {str(t or "").lower() for t in (tools_ran or state.last_tools or [])}
    wrote = bool(tools & {"file_write", "artifact_write", "self_edit"})
    # Implement phase without a write → incomplete
    if state.phase == "implement" and not wrote:
        return True
    # Stall / re-list language
    if looks_like_desktop_relist(low):
        return True
    if re.search(
        r"\b(what kind of|gotta see what|throwing a fit|before i can try|"
        r"what(?:'s| is) the first thing|where (?:do|should) we start)\b",
        low,
    ):
        return True
    return False
