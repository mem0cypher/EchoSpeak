"""Lightweight verification telemetry for EchoSpeak reliability work."""

from __future__ import annotations

import json
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional


@dataclass
class VerificationEvent:
    kind: str
    tool: str = ""
    reason: str = ""
    severity: str = "medium"
    metadata: Dict[str, Any] | None = None
    at: float = 0.0


class VerificationTelemetry:
    """In-process + JSONL telemetry for the failure clusters Echo should verify hardest."""

    HIGH_WEIGHT_KINDS = {
        "search_query_rejected",
        "search_evidence_irrelevant",
        "action_args_invalid",
        "terminal_nonzero",
        "file_operation_failed",
        "max_retries_exhausted",
    }

    LOW_WEIGHT_TOOLS = {"file_read", "file_list", "get_system_time", "calculate", "project_update_context"}

    def __init__(self, path: Optional[str] = None, max_recent: int = 80, enabled: bool = True):
        self.enabled = bool(enabled)
        self.path = Path(path).expanduser() if path else None
        self.recent: Deque[VerificationEvent] = deque(maxlen=max_recent)
        self.counts: Counter[str] = Counter()

    def record(
        self,
        kind: str,
        *,
        tool: str = "",
        reason: str = "",
        severity: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> VerificationEvent:
        sev = severity or ("high" if kind in self.HIGH_WEIGHT_KINDS else "low" if tool in self.LOW_WEIGHT_TOOLS else "medium")
        event = VerificationEvent(
            kind=str(kind or "unknown"),
            tool=str(tool or ""),
            reason=str(reason or "")[:500],
            severity=sev,
            metadata=metadata or {},
            at=time.time(),
        )
        self.recent.append(event)
        self.counts[event.kind] += 1
        if self.enabled and self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
            except Exception:
                pass
        return event

    def should_verify(self, tool: str, kind: str = "") -> bool:
        if str(kind or "") in self.HIGH_WEIGHT_KINDS:
            return True
        return str(tool or "") not in self.LOW_WEIGHT_TOOLS

    def report(self, limit: int = 12) -> Dict[str, Any]:
        recent_items: List[Dict[str, Any]] = [asdict(e) for e in list(self.recent)[-limit:]]
        return {
            "enabled": self.enabled,
            "persisted": bool(self.enabled and self.path),
            "count": int(sum(self.counts.values())),
            "clusters": dict(self.counts.most_common(20)),
            "recent": recent_items,
            "high_weight_kinds": sorted(self.HIGH_WEIGHT_KINDS),
        }
