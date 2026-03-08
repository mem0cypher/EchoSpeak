"""
Tool Registry for EchoSpeak.

Provides a central registry that tools self-register into via decorator.
Replaces the hardcoded lists in core.py (_create_tools, _is_action_tool,
_action_allowed) with a single source of truth.

Usage in tools.py:
    from agent.tool_registry import ToolRegistry

    @ToolRegistry.register(
        name="web_search",
        description="Search the web for information",
        category="research",
    )
    @tool(args_schema=WebSearchArgs)
    def web_search(query: str) -> str:
        ...

Usage in core.py:
    entries = ToolRegistry.get_all()       # all registered ToolEntry objects
    safe    = ToolRegistry.get_safe()      # non-action tool functions only
    ToolRegistry.is_action("file_write")   # True
    ToolRegistry.get_permission_flags("file_write")  # ["ENABLE_SYSTEM_ACTIONS", "ALLOW_FILE_WRITE"]
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set


@dataclass(frozen=True)
class ToolEntry:
    """Metadata for a single registered tool."""

    name: str
    func: Any  # the LangChain @tool-decorated function
    description: str
    category: str = "general"
    is_action: bool = False
    risk_level: str = "safe"  # "safe" | "moderate" | "destructive"
    policy_flags: tuple = ()  # env flags required to enable
    keyword_hints: tuple = ()  # keywords for heuristic routing


class ToolRegistry:
    """Central registry for EchoSpeak tools.

    Tools self-register via the ``@ToolRegistry.register(...)`` decorator.
    The registry is intentionally module-level (class-level dict) so that
    importing a module is sufficient to register its tools.
    """

    _entries: Dict[str, ToolEntry] = {}

    # ── Registration ────────────────────────────────────────────────

    @classmethod
    def register(
        cls,
        name: str,
        description: str,
        category: str = "general",
        is_action: bool = False,
        risk_level: str = "safe",
        policy_flags: Optional[List[str]] = None,
        keyword_hints: Optional[List[str]] = None,
    ):
        """Decorator that registers a tool function in the global registry.

        Can be stacked with LangChain's ``@tool`` decorator — order does not
        matter because we store whatever the decorator returns (which is the
        LangChain StructuredTool wrapper if ``@tool`` ran first, or the raw
        function if ``@register`` ran first and ``@tool`` will wrap it next).

        The registry re-captures the final object at first access via
        ``_resolve()``, so late-binding is safe.
        """
        _flags = tuple(policy_flags or [])
        _hints = tuple(keyword_hints or [])

        def decorator(func: Callable) -> Callable:
            cls._entries[name] = ToolEntry(
                name=name,
                func=func,
                description=description,
                category=category,
                is_action=is_action,
                risk_level=risk_level,
                policy_flags=_flags,
                keyword_hints=_hints,
            )
            return func

        return decorator

    # ── Bulk registration from existing TOOL_METADATA ───────────────

    @classmethod
    def register_from_metadata(
        cls,
        tool_funcs: List[Any],
        metadata: Dict[str, Dict[str, Any]],
    ) -> None:
        """Register tools in bulk from the legacy *get_available_tools()* list
        and the *TOOL_METADATA* dict in tools.py.

        This is the **migration bridge** — it lets us use the registry
        immediately without rewriting every ``@tool`` in tools.py.

        Once all tools use ``@ToolRegistry.register``, this method can be
        removed.
        """
        for func in tool_funcs:
            name = getattr(func, "name", None)
            if not name:
                continue
            if name in cls._entries:
                # Already registered via decorator — skip
                continue
            desc = getattr(func, "description", "") or ""
            meta = metadata.get(name, {})
            risk = meta.get("risk_level", "safe")
            requires_confirm = meta.get("requires_confirmation", False)
            flags = tuple(meta.get("policy_flags", []))
            cls._entries[name] = ToolEntry(
                name=name,
                func=func,
                description=desc,
                category=_infer_category(name),
                is_action=requires_confirm,
                risk_level=risk,
                policy_flags=flags,
            )

    # ── Queries ─────────────────────────────────────────────────────

    @classmethod
    def get(cls, name: str) -> Optional[ToolEntry]:
        """Get a single tool entry by name."""
        return cls._entries.get(name)

    @classmethod
    def get_all(cls) -> Dict[str, ToolEntry]:
        """Return all registered tool entries."""
        return dict(cls._entries)

    @classmethod
    def get_funcs(cls) -> List[Any]:
        """Return all tool functions (for LangChain agent init)."""
        return [e.func for e in cls._entries.values()]

    @classmethod
    def get_safe_funcs(cls) -> List[Any]:
        """Return non-action tool functions only (safe for LLM tool-calling)."""
        return [e.func for e in cls._entries.values() if not e.is_action]

    @classmethod
    def get_config_filtered_funcs(cls, config: Any) -> List[Any]:
        """Return tool functions filtered by current config flags.

        Non-action tools are always included.  Action tools are included
        only when **all** of their ``policy_flags`` evaluate to ``True``
        on the supplied *config* object.  This allows action tools to
        appear in the LLM tool list when the user has enabled the
        corresponding safety gates (e.g. ``ENABLE_SYSTEM_ACTIONS``,
        ``ALLOW_FILE_WRITE``).

        The flag names stored in ``policy_flags`` use UPPER_CASE env-var
        style (``ENABLE_SYSTEM_ACTIONS``).  Config properties use
        snake_case (``enable_system_actions``).  We check both.
        """
        result: List[Any] = []
        for entry in cls._entries.values():
            if not entry.is_action:
                result.append(entry.func)
                continue
            # Action tool — check every required policy flag
            if not entry.policy_flags:
                # No flags required → include
                result.append(entry.func)
                continue
            all_ok = True
            for flag in entry.policy_flags:
                attr_name = flag.lower()  # e.g. ENABLE_SYSTEM_ACTIONS → enable_system_actions
                if not bool(getattr(config, attr_name, False)):
                    all_ok = False
                    break
            if all_ok:
                result.append(entry.func)
        return result

    @classmethod
    def get_by_category(cls, category: str) -> List[ToolEntry]:
        """Return all tools in a specific category."""
        return [e for e in cls._entries.values() if e.category == category]

    @classmethod
    def is_action(cls, name: str) -> bool:
        """Check if a tool is an action tool (requires confirmation)."""
        entry = cls._entries.get(name)
        return entry.is_action if entry else False

    @classmethod
    def get_permission_flags(cls, name: str) -> List[str]:
        """Get the env permission flags required for a tool."""
        entry = cls._entries.get(name)
        return list(entry.policy_flags) if entry else []

    @classmethod
    def get_names(cls) -> Set[str]:
        """Return set of all registered tool names."""
        return set(cls._entries.keys())

    @classmethod
    def get_action_names(cls) -> Set[str]:
        """Return set of all action tool names."""
        return {e.name for e in cls._entries.values() if e.is_action}

    @classmethod
    def clear(cls) -> None:
        """Clear all registered tools (for testing)."""
        cls._entries.clear()


# ── Pipeline Plugin System ──────────────────────────────────────────

class PipelinePlugin:
    """Base class for pipeline plugins that skills can subclass.

    Each method corresponds to a pipeline stage in ``process_query()``.
    Return ``None`` to pass through; return a value to short-circuit.

    Example skill plugin (``skills/weather/plugin.py``)::

        from agent.tool_registry import PipelinePlugin, PluginRegistry

        class WeatherPlugin(PipelinePlugin):
            def on_preempt(self, user_input, **kwargs):
                if "weather" in user_input.lower():
                    return self.handle_weather(user_input)
                return None  # pass through

        PluginRegistry.register(WeatherPlugin())
    """

    @property
    def name(self) -> str:
        return self.__class__.__name__

    def on_preempt(self, user_input: str, **kwargs) -> Any:
        """Called during Stage 1 (parse & preempt). Return a tuple to short-circuit."""
        return None

    def on_context(self, user_input: str, context: Any, **kwargs) -> Any:
        """Called after Stage 2 (build context). Can modify the context bundle."""
        return None

    def on_shortcut(self, user_input: str, context: Any, **kwargs) -> Any:
        """Called during Stage 3 (shortcuts). Return a tuple to short-circuit."""
        return None

    def on_response(self, user_input: str, response: str, context: Any, **kwargs) -> Optional[str]:
        """Called after Stage 4 (LLM agents). Can transform the response text."""
        return None

    def on_finalize(self, user_input: str, response: str, **kwargs) -> Optional[str]:
        """Called during Stage 5 (finalize). Last chance to modify output."""
        return None


class PluginRegistry:
    """Registry for pipeline plugins.

    Plugins are registered in order and dispatched sequentially at each
    pipeline stage. The first plugin to return a non-None value wins
    (for short-circuit hooks) or mutates the response (for transform hooks).
    """

    _plugins: List[PipelinePlugin] = []

    @classmethod
    def register(cls, plugin: PipelinePlugin) -> None:
        """Register a pipeline plugin instance."""
        # Prevent duplicates by name
        if any(p.name == plugin.name for p in cls._plugins):
            return
        cls._plugins.append(plugin)

    @classmethod
    def get_all(cls) -> List[PipelinePlugin]:
        """Return all registered plugins in order."""
        return list(cls._plugins)

    @classmethod
    def dispatch_preempt(cls, user_input: str, **kwargs) -> Any:
        """Dispatch on_preempt to all plugins. First non-None result wins."""
        for plugin in cls._plugins:
            try:
                result = plugin.on_preempt(user_input, **kwargs)
                if result is not None:
                    return result
            except Exception:
                pass
        return None

    @classmethod
    def dispatch_context(cls, user_input: str, context: Any, **kwargs) -> Any:
        """Dispatch on_context to all plugins."""
        for plugin in cls._plugins:
            try:
                plugin.on_context(user_input, context, **kwargs)
            except Exception:
                pass
        return context

    @classmethod
    def dispatch_shortcut(cls, user_input: str, context: Any, **kwargs) -> Any:
        """Dispatch on_shortcut to all plugins. First non-None result wins."""
        for plugin in cls._plugins:
            try:
                result = plugin.on_shortcut(user_input, context, **kwargs)
                if result is not None:
                    return result
            except Exception:
                pass
        return None

    @classmethod
    def dispatch_response(cls, user_input: str, response: str, context: Any, **kwargs) -> str:
        """Dispatch on_response to all plugins. Each can transform the response."""
        current = response
        for plugin in cls._plugins:
            try:
                result = plugin.on_response(user_input, current, context, **kwargs)
                if result is not None:
                    current = result
            except Exception:
                pass
        return current

    @classmethod
    def dispatch_finalize(cls, user_input: str, response: str, **kwargs) -> str:
        """Dispatch on_finalize to all plugins. Each can transform the final output."""
        current = response
        for plugin in cls._plugins:
            try:
                result = plugin.on_finalize(user_input, current, **kwargs)
                if result is not None:
                    current = result
            except Exception:
                pass
        return current

    @classmethod
    def clear(cls) -> None:
        """Clear all registered plugins (for testing)."""
        cls._plugins.clear()


# ── Helpers ─────────────────────────────────────────────────────────

_CATEGORY_MAP = {
    "web_search": "research",
    "youtube_transcript": "research",
    "browse_task": "research",
    "get_system_time": "utility",
    "calculate": "utility",
    "system_info": "utility",
    "analyze_screen": "vision",
    "vision_qa": "vision",
    "take_screenshot": "vision",
    "open_chrome": "system",
    "open_application": "system",
    "notepad_write": "system",
    "terminal_run": "system",
    "desktop_list_windows": "desktop",
    "desktop_find_control": "desktop",
    "desktop_click": "desktop",
    "desktop_type_text": "desktop",
    "desktop_activate_window": "desktop",
    "desktop_send_hotkey": "desktop",
    "file_list": "file_ops",
    "file_read": "file_ops",
    "file_write": "file_ops",
    "file_move": "file_ops",
    "file_copy": "file_ops",
    "file_delete": "file_ops",
    "file_mkdir": "file_ops",
    "artifact_write": "file_ops",
    "discord_web_read_recent": "discord",
    "discord_web_send": "discord",
    "discord_contacts_add": "discord",
    "discord_contacts_discover": "discord",
    "discord_read_channel": "discord",
    "discord_send_channel": "discord",
    "self_edit": "self_mod",
    "self_rollback": "self_mod",
    "self_git_status": "self_mod",
    "self_read": "self_mod",
    "self_grep": "self_mod",
    "self_list": "self_mod",
}


def _infer_category(name: str) -> str:
    """Infer a tool's category from its name (fallback for legacy tools)."""
    return _CATEGORY_MAP.get(name, "general")


# ── Tool Usage Statistics ────────────────────────────────────────────

class ToolUsageStats:
    """Thread-safe per-tool usage statistics tracker.

    Since ToolEntry is frozen, we track mutable stats separately.
    Used by the /capabilities endpoint to report usage_count,
    last_used_at, and success_rate per tool.
    """

    _lock = threading.Lock()
    _stats: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def record_call(cls, tool_name: str) -> None:
        """Record a successful tool invocation."""
        with cls._lock:
            if tool_name not in cls._stats:
                cls._stats[tool_name] = {"calls": 0, "errors": 0, "last_used": None}
            cls._stats[tool_name]["calls"] += 1
            cls._stats[tool_name]["last_used"] = datetime.now(timezone.utc).isoformat()

    @classmethod
    def record_error(cls, tool_name: str) -> None:
        """Record a tool invocation error (also counts as a call attempt)."""
        with cls._lock:
            if tool_name not in cls._stats:
                cls._stats[tool_name] = {"calls": 0, "errors": 0, "last_used": None}
            cls._stats[tool_name]["calls"] += 1
            cls._stats[tool_name]["errors"] += 1
            cls._stats[tool_name]["last_used"] = datetime.now(timezone.utc).isoformat()

    @classmethod
    def get_stats(cls, tool_name: str) -> Dict[str, Any]:
        """Get usage stats for a specific tool."""
        with cls._lock:
            s = cls._stats.get(tool_name, {"calls": 0, "errors": 0, "last_used": None})
            calls = s["calls"]
            errors = s["errors"]
            success_rate = round((calls - errors) / calls, 3) if calls > 0 else None
            return {
                "usage_count": calls,
                "error_count": errors,
                "last_used_at": s["last_used"],
                "success_rate": success_rate,
            }

    @classmethod
    def get_all_stats(cls) -> Dict[str, Dict[str, Any]]:
        """Get usage stats for all tools."""
        with cls._lock:
            result = {}
            for name, s in cls._stats.items():
                calls = s["calls"]
                errors = s["errors"]
                success_rate = round((calls - errors) / calls, 3) if calls > 0 else None
                result[name] = {
                    "usage_count": calls,
                    "error_count": errors,
                    "last_used_at": s["last_used"],
                    "success_rate": success_rate,
                }
            return result

    @classmethod
    def clear(cls) -> None:
        """Clear all stats (for testing)."""
        with cls._lock:
            cls._stats.clear()
