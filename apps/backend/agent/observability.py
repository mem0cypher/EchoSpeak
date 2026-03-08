"""
Observability dashboard — metrics, latency tracking, and error aggregation.

Provides a central metrics collector that accumulates runtime statistics
for tools, requests, and system health. Exposed via the /observability API.
"""

from __future__ import annotations

import time
import json
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from threading import Lock
from pathlib import Path
from collections import deque

from loguru import logger


@dataclass
class RequestMetric:
    """Metrics for a single request."""
    request_id: str
    started_at: float
    finished_at: float = 0.0
    latency_ms: float = 0.0
    tool_count: int = 0
    token_count: int = 0
    source: str = ""
    thread_id: str = ""
    success: bool = True
    error: Optional[str] = None


@dataclass
class ToolMetric:
    """Aggregated metrics for a specific tool."""
    name: str
    total_calls: int = 0
    total_errors: int = 0
    total_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    max_latency_ms: float = 0.0
    last_called_at: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.total_calls if self.total_calls > 0 else 0.0

    @property
    def success_rate(self) -> float:
        return ((self.total_calls - self.total_errors) / self.total_calls * 100) if self.total_calls > 0 else 100.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "total_calls": self.total_calls,
            "total_errors": self.total_errors,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "min_latency_ms": round(self.min_latency_ms, 2) if self.min_latency_ms < float("inf") else 0.0,
            "max_latency_ms": round(self.max_latency_ms, 2),
            "success_rate": round(self.success_rate, 1),
            "last_called_at": self.last_called_at,
        }


class ObservabilityCollector:
    """Central metrics collector for the observability dashboard.

    Thread-safe. Stores tool metrics, recent request metrics, and
    error history. Intended to be queried via the /observability endpoint.
    """

    MAX_RECENT_REQUESTS = 200
    MAX_RECENT_ERRORS = 100

    def __init__(self):
        self._lock = Lock()
        self._tool_metrics: Dict[str, ToolMetric] = {}
        self._recent_requests: deque[RequestMetric] = deque(maxlen=self.MAX_RECENT_REQUESTS)
        self._recent_errors: deque[dict] = deque(maxlen=self.MAX_RECENT_ERRORS)
        self._started_at = time.time()
        self._total_requests = 0
        self._total_errors = 0

    # ── Recording ───────────────────────────────────────────────

    def record_request(self, metric: RequestMetric) -> None:
        """Record a completed request."""
        with self._lock:
            self._recent_requests.append(metric)
            self._total_requests += 1
            if not metric.success:
                self._total_errors += 1
                self._recent_errors.append({
                    "request_id": metric.request_id,
                    "error": metric.error or "unknown",
                    "timestamp": metric.finished_at,
                    "source": metric.source,
                })

    def record_tool_call(
        self,
        tool_name: str,
        latency_ms: float,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """Record a tool invocation."""
        with self._lock:
            if tool_name not in self._tool_metrics:
                self._tool_metrics[tool_name] = ToolMetric(name=tool_name)

            tm = self._tool_metrics[tool_name]
            tm.total_calls += 1
            tm.total_latency_ms += latency_ms
            tm.last_called_at = time.time()
            tm.min_latency_ms = min(tm.min_latency_ms, latency_ms)
            tm.max_latency_ms = max(tm.max_latency_ms, latency_ms)

            if not success:
                tm.total_errors += 1
                self._recent_errors.append({
                    "tool": tool_name,
                    "error": error or "unknown",
                    "timestamp": time.time(),
                    "latency_ms": latency_ms,
                })

    # ── Dashboard data ──────────────────────────────────────────

    def get_dashboard(self) -> dict:
        """Get the full dashboard data for the /observability endpoint."""
        with self._lock:
            uptime = time.time() - self._started_at
            avg_latency = 0.0
            if self._recent_requests:
                latencies = [r.latency_ms for r in self._recent_requests if r.latency_ms > 0]
                avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

            # P95 latency
            p95 = 0.0
            if latencies := sorted(r.latency_ms for r in self._recent_requests if r.latency_ms > 0):
                idx = int(len(latencies) * 0.95)
                p95 = latencies[min(idx, len(latencies) - 1)]

            request_rate = self._total_requests / (uptime / 3600) if uptime > 0 else 0.0

            # Tool rankings
            tool_data = sorted(
                [tm.to_dict() for tm in self._tool_metrics.values()],
                key=lambda t: t["total_calls"],
                reverse=True,
            )

            return {
                "system": {
                    "uptime_seconds": round(uptime, 0),
                    "uptime_human": self._format_uptime(uptime),
                    "started_at": self._started_at,
                    "total_requests": self._total_requests,
                    "total_errors": self._total_errors,
                    "error_rate": round(
                        (self._total_errors / self._total_requests * 100)
                        if self._total_requests > 0
                        else 0.0,
                        2,
                    ),
                    "requests_per_hour": round(request_rate, 1),
                },
                "latency": {
                    "avg_ms": round(avg_latency, 2),
                    "p95_ms": round(p95, 2),
                },
                "tools": {
                    "total_registered": len(self._tool_metrics),
                    "rankings": tool_data[:20],
                },
                "recent_errors": list(self._recent_errors)[-10:],
                "recent_requests": [
                    {
                        "request_id": r.request_id,
                        "latency_ms": round(r.latency_ms, 2),
                        "tool_count": r.tool_count,
                        "source": r.source,
                        "success": r.success,
                        "timestamp": r.started_at,
                    }
                    for r in list(self._recent_requests)[-20:]
                ],
            }

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        """Format uptime as human-readable string."""
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        return " ".join(parts)


# ── Singleton ───────────────────────────────────────────────────────

_collector: Optional[ObservabilityCollector] = None


def get_observability_collector() -> ObservabilityCollector:
    """Get or create the global observability collector."""
    global _collector
    if _collector is None:
        _collector = ObservabilityCollector()
    return _collector
