"""
System Monitor Pipeline Plugin

Demonstrates the Plugin Pipeline (Update 2) and 5-Stage Pipeline (Update 1).

This plugin intercepts "system status" queries at Stage 1 (on_preempt) and
returns system health data directly — no LLM call needed. It also adds a
lightweight system load hint at Stage 2 (on_context) for all queries.
"""

import os
import platform
import time
from datetime import datetime

from agent.tool_registry import PipelinePlugin, PluginRegistry

# Trigger phrases that activate the preempt
_STATUS_TRIGGERS = {
    "system status", "health check", "system health",
    "check system", "how is the system", "server status",
    "system info", "sys status",
}


def _matches_trigger(user_input: str) -> bool:
    """Check if user input matches any status trigger phrase."""
    normalized = user_input.lower().strip().rstrip("?!.")
    return normalized in _STATUS_TRIGGERS


def _collect_system_stats() -> dict:
    """Collect system health metrics using stdlib (no psutil dependency)."""
    stats = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "platform": platform.system(),
        "python": platform.python_version(),
    }

    # CPU load (Unix: /proc/loadavg, fallback: os.getloadavg)
    try:
        load1, load5, load15 = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        stats["cpu_load_1m"] = f"{load1:.2f}"
        stats["cpu_load_5m"] = f"{load5:.2f}"
        stats["cpu_cores"] = cpu_count
        stats["cpu_usage_pct"] = f"{(load1 / cpu_count) * 100:.1f}%"
    except (OSError, AttributeError):
        stats["cpu_load"] = "unavailable"

    # Memory (Unix: /proc/meminfo)
    try:
        with open("/proc/meminfo", "r") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
            total_kb = meminfo.get("MemTotal", 0)
            avail_kb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
            used_kb = total_kb - avail_kb
            stats["mem_total"] = f"{total_kb / 1024 / 1024:.1f} GB"
            stats["mem_used"] = f"{used_kb / 1024 / 1024:.1f} GB"
            stats["mem_pct"] = f"{(used_kb / total_kb) * 100:.1f}%" if total_kb else "?"
    except (OSError, ValueError):
        stats["memory"] = "unavailable"

    # Disk (os.statvfs)
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        stats["disk_total"] = f"{total / 1024 / 1024 / 1024:.1f} GB"
        stats["disk_used"] = f"{used / 1024 / 1024 / 1024:.1f} GB"
        stats["disk_pct"] = f"{(used / total) * 100:.1f}%" if total else "?"
    except (OSError, AttributeError):
        stats["disk"] = "unavailable"

    # Uptime (Unix: /proc/uptime)
    try:
        with open("/proc/uptime", "r") as f:
            uptime_secs = float(f.read().split()[0])
            days = int(uptime_secs // 86400)
            hours = int((uptime_secs % 86400) // 3600)
            mins = int((uptime_secs % 3600) // 60)
            stats["uptime"] = f"{days}d {hours}h {mins}m"
    except (OSError, ValueError):
        stats["uptime"] = "unavailable"

    # Process memory (own process)
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        stats["process_mem"] = f"{usage.ru_maxrss / 1024:.1f} MB"
    except (ImportError, AttributeError):
        stats["process_mem"] = "unavailable"

    return stats


def _format_status(stats: dict) -> str:
    """Format system stats into a conversational response."""
    lines = [f"System Status as of {stats.get('time', 'now')}:\n"]

    cpu_pct = stats.get("cpu_usage_pct", stats.get("cpu_load", "?"))
    lines.append(f"CPU: {cpu_pct} load ({stats.get('cpu_cores', '?')} cores)")

    if "mem_used" in stats:
        lines.append(f"Memory: {stats['mem_used']} / {stats['mem_total']} ({stats['mem_pct']} used)")

    if "disk_used" in stats:
        lines.append(f"Disk: {stats['disk_used']} / {stats['disk_total']} ({stats['disk_pct']} used)")

    if "uptime" in stats:
        lines.append(f"Uptime: {stats['uptime']}")

    if "process_mem" in stats:
        lines.append(f"Agent process memory: {stats['process_mem']}")

    lines.append(f"Platform: {stats.get('platform', '?')} / Python {stats.get('python', '?')}")

    return "\n".join(lines)


def _get_load_level() -> str:
    """Quick system load level for context enrichment."""
    try:
        load1, _, _ = os.getloadavg()
        cores = os.cpu_count() or 1
        ratio = load1 / cores
        if ratio < 0.5:
            return "low"
        elif ratio < 1.0:
            return "medium"
        return "high"
    except (OSError, AttributeError):
        return "unknown"


class SystemMonitorPlugin(PipelinePlugin):
    """Pipeline plugin that intercepts system status queries.

    - on_preempt: detects status triggers → returns instant system stats
    - on_context: adds lightweight system_load hint to all queries
    """

    def on_preempt(self, user_input: str, **kwargs):
        """Intercept system status queries and return stats directly."""
        if not _matches_trigger(user_input):
            return None

        stats = _collect_system_stats()
        response = _format_status(stats)
        # Return (response_text, success) tuple to match process_query return
        return (response, True)

    def on_context(self, user_input: str, context, **kwargs):
        """Add system load level to context for all queries."""
        # Only add if context has an attribute we can set
        if hasattr(context, "extra"):
            if isinstance(context.extra, dict):
                context.extra["system_load"] = _get_load_level()
        return None


# Auto-register on import
PluginRegistry.register(SystemMonitorPlugin())
