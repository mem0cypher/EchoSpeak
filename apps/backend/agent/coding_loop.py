"""Coding loop state machine (v7.5.2 foundation).

Enforces inspect → plan → implement → verify → confirm → summarize as real
states — not a prompt suggestion. The model cannot skip to implement without
inspect+plan, or mark summarize without verify (unless verify was skipped with
an explicit reason such as terminal disabled).

Terminal/sandbox exit classification reuses sandbox.STATUS_* constants.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class CodingPhase(str, Enum):
    IDLE = "idle"
    INSPECT = "inspect"
    PLAN = "plan"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    CONFIRM = "confirm"
    SUMMARIZE = "summarize"
    DONE = "done"
    FAILED = "failed"


class CodingExit(str, Enum):
    """Honest terminal/task outcomes — never a vague 'done' without status."""

    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    DENIED = "denied"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    CANCELLED = "cancelled"
    PENDING = "pending"


# Legal forward edges (enforced). FAILED/DONE are terminals.
_TRANSITIONS: Dict[CodingPhase, Set[CodingPhase]] = {
    CodingPhase.IDLE: {CodingPhase.INSPECT, CodingPhase.FAILED},
    CodingPhase.INSPECT: {CodingPhase.PLAN, CodingPhase.FAILED},
    CodingPhase.PLAN: {CodingPhase.IMPLEMENT, CodingPhase.FAILED},
    CodingPhase.IMPLEMENT: {CodingPhase.VERIFY, CodingPhase.CONFIRM, CodingPhase.FAILED},
    # verify may go to confirm (writes pending) or summarize (read-only verify)
    CodingPhase.VERIFY: {CodingPhase.CONFIRM, CodingPhase.SUMMARIZE, CodingPhase.FAILED},
    CodingPhase.CONFIRM: {CodingPhase.SUMMARIZE, CodingPhase.FAILED, CodingPhase.IMPLEMENT},
    CodingPhase.SUMMARIZE: {CodingPhase.DONE, CodingPhase.FAILED},
    CodingPhase.DONE: set(),
    CodingPhase.FAILED: set(),
}


@dataclass
class CodingLoopEvent:
    at: float
    from_phase: str
    to_phase: str
    note: str = ""


@dataclass
class CodingLoopState:
    """Per-task coding lifecycle."""

    task_id: str
    phase: str = CodingPhase.IDLE.value
    project_folder: str = ""
    files_touched: List[str] = field(default_factory=list)
    verify_status: str = CodingExit.PENDING.value
    confirm_status: str = CodingExit.PENDING.value
    exit_status: str = CodingExit.PENDING.value
    notes: List[str] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CodingLoopError(ValueError):
    pass


class CodingLoop:
    """Code-enforced coding lifecycle (not prompt-only)."""

    def __init__(self, task_id: Optional[str] = None, project_folder: str = ""):
        self.state = CodingLoopState(
            task_id=task_id or str(uuid.uuid4()),
            project_folder=str(project_folder or "").strip(),
        )

    @property
    def phase(self) -> CodingPhase:
        try:
            return CodingPhase(self.state.phase)
        except Exception:
            return CodingPhase.IDLE

    def _record(self, frm: CodingPhase, to: CodingPhase, note: str = "") -> None:
        self.state.history.append(
            {
                "at": time.time(),
                "from_phase": frm.value,
                "to_phase": to.value,
                "note": note,
            }
        )
        self.state.updated_at = time.time()
        if note:
            self.state.notes.append(note)

    def advance(self, to: CodingPhase | str, *, note: str = "") -> CodingLoopState:
        target = CodingPhase(to) if not isinstance(to, CodingPhase) else to
        current = self.phase
        if current in (CodingPhase.DONE, CodingPhase.FAILED):
            raise CodingLoopError(f"Coding loop is terminal ({current.value}); start a new task.")
        allowed = _TRANSITIONS.get(current, set())
        if target not in allowed:
            raise CodingLoopError(
                f"Illegal coding-loop transition {current.value} → {target.value}. "
                f"Allowed: {sorted(p.value for p in allowed) or '∅'}"
            )
        self._record(current, target, note)
        self.state.phase = target.value
        return self.state

    def start(self, *, project_folder: str = "", note: str = "coding task started") -> CodingLoopState:
        if project_folder:
            self.state.project_folder = str(project_folder).strip()
        if self.phase == CodingPhase.IDLE:
            return self.advance(CodingPhase.INSPECT, note=note)
        return self.state

    def mark_files(self, paths: List[str]) -> None:
        for p in paths or []:
            s = str(p or "").strip()
            if s and s not in self.state.files_touched:
                self.state.files_touched.append(s)

    def set_verify_status(self, status: CodingExit | str) -> None:
        self.state.verify_status = CodingExit(status).value if not isinstance(status, CodingExit) else status.value
        self.state.updated_at = time.time()

    def set_confirm_status(self, status: CodingExit | str) -> None:
        self.state.confirm_status = CodingExit(status).value if not isinstance(status, CodingExit) else status.value
        self.state.updated_at = time.time()

    def fail(self, reason: str, *, exit_status: CodingExit | str = CodingExit.FAIL) -> CodingLoopState:
        current = self.phase
        if current not in (CodingPhase.DONE, CodingPhase.FAILED):
            self._record(current, CodingPhase.FAILED, reason)
            self.state.phase = CodingPhase.FAILED.value
        self.state.exit_status = (
            CodingExit(exit_status).value if not isinstance(exit_status, CodingExit) else exit_status.value
        )
        return self.state

    def complete(self, *, exit_status: CodingExit | str = CodingExit.PASS) -> CodingLoopState:
        if self.phase != CodingPhase.SUMMARIZE:
            raise CodingLoopError("complete() only allowed from summarize phase")
        self.advance(CodingPhase.DONE, note="coding task complete")
        self.state.exit_status = (
            CodingExit(exit_status).value if not isinstance(exit_status, CodingExit) else exit_status.value
        )
        return self.state

    def suggested_next(self) -> List[str]:
        return sorted(p.value for p in _TRANSITIONS.get(self.phase, set()))

    def fast_forward_to(self, target: CodingPhase | str, *, note: str = "") -> CodingLoopState:
        """Walk legal edges to *target* (BFS). Used when tools imply a later phase."""
        target_ph = CodingPhase(target) if not isinstance(target, CodingPhase) else target
        if self.phase == target_ph:
            return self.state
        if self.phase in (CodingPhase.DONE, CodingPhase.FAILED):
            raise CodingLoopError(f"Cannot fast-forward from terminal phase {self.phase.value}")

        # BFS for shortest legal path
        from collections import deque

        start = self.phase
        q: deque = deque([(start, [])])
        seen = {start}
        found: Optional[List[CodingPhase]] = None
        while q:
            node, path = q.popleft()
            if node == target_ph:
                found = path
                break
            for nxt in _TRANSITIONS.get(node, set()):
                if nxt not in seen and nxt not in (CodingPhase.FAILED,):
                    seen.add(nxt)
                    q.append((nxt, path + [nxt]))
        if not found:
            raise CodingLoopError(
                f"No legal path from {self.phase.value} → {target_ph.value}"
            )
        for step in found:
            self.advance(step, note=note or f"auto → {step.value}")
        return self.state

    def note_tool(
        self,
        tool_name: str,
        *,
        path: str = "",
        terminal_status: str = "",
        pending_write: bool = False,
    ) -> CodingLoopState:
        """
        Soft-advance the loop from observed tool usage (code-enforced, not model memory).

        - file_list / file_read / file_mkdir → at least inspect
        - file_write / mutations → at least implement (+ confirm if pending_write)
        - terminal_run → at least verify (+ status)
        """
        name = str(tool_name or "").strip().lower()
        if self.phase == CodingPhase.IDLE:
            self.start(note="tool observed")
        if name in {"file_list", "file_read", "file_mkdir"}:
            if self.phase == CodingPhase.IDLE:
                self.start()
            # stay in inspect until something forces plan
            if path:
                self.mark_files([path])
            return self.state
        if name in {"file_write", "file_move", "file_copy", "file_delete", "artifact_write", "notepad_write"}:
            if self.phase in (CodingPhase.IDLE, CodingPhase.INSPECT):
                self.fast_forward_to(CodingPhase.PLAN, note=f"tool {name}")
            if self.phase == CodingPhase.PLAN:
                self.advance(CodingPhase.IMPLEMENT, note=f"tool {name}")
            elif self.phase not in (
                CodingPhase.IMPLEMENT,
                CodingPhase.VERIFY,
                CodingPhase.CONFIRM,
                CodingPhase.SUMMARIZE,
                CodingPhase.DONE,
                CodingPhase.FAILED,
            ):
                try:
                    self.fast_forward_to(CodingPhase.IMPLEMENT, note=f"tool {name}")
                except CodingLoopError:
                    pass
            if path:
                self.mark_files([path])
            if pending_write and self.phase == CodingPhase.IMPLEMENT:
                try:
                    self.advance(CodingPhase.CONFIRM, note="write pending confirmation")
                except CodingLoopError:
                    # implement → verify → confirm
                    try:
                        self.fast_forward_to(CodingPhase.CONFIRM, note="write pending confirmation")
                    except CodingLoopError:
                        pass
            return self.state
        if name == "terminal_run":
            try:
                if self.phase in (CodingPhase.IDLE, CodingPhase.INSPECT, CodingPhase.PLAN, CodingPhase.IMPLEMENT):
                    self.fast_forward_to(CodingPhase.VERIFY, note="terminal verify")
                elif self.phase == CodingPhase.CONFIRM:
                    # re-verify after confirm is unusual; allow summarize path later
                    pass
                elif self.phase not in (CodingPhase.VERIFY, CodingPhase.SUMMARIZE, CodingPhase.DONE, CodingPhase.FAILED):
                    self.fast_forward_to(CodingPhase.VERIFY, note="terminal verify")
            except CodingLoopError:
                pass
            if terminal_status:
                try:
                    self.set_verify_status(terminal_status)
                except Exception:
                    self.state.verify_status = str(terminal_status)
            return self.state
        return self.state

    def as_dict(self) -> Dict[str, Any]:
        d = self.state.as_dict()
        d["suggested_next"] = self.suggested_next()
        return d


def project_folder_for_name(name: str, root: str) -> str:
    """Named project folder under FILE_TOOL_ROOT — not repo root scatter."""
    import re
    from pathlib import Path

    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", (name or "project").strip()).strip("-._") or "project"
    slug = slug[:64]
    base = Path(root).expanduser()
    return str((base / "projects" / slug))


def parse_terminal_status_block(text: str) -> str:
    """Extract Status= from terminal_run output; default fail if missing."""
    for line in str(text or "").splitlines():
        if line.strip().lower().startswith("status="):
            return line.split("=", 1)[1].strip().lower()
    # Legacy host output without Status=
    if "ExitCode=0" in str(text or "") or str(text or "").strip().endswith("ExitCode=0"):
        return CodingExit.PASS.value
    if "timed out" in str(text or "").lower():
        return CodingExit.TIMEOUT.value
    if "denylist" in str(text or "").lower() or "blocked" in str(text or "").lower():
        return CodingExit.DENIED.value
    if "sandbox_unavailable" in str(text or "").lower():
        return CodingExit.SANDBOX_UNAVAILABLE.value
    return CodingExit.FAIL.value
