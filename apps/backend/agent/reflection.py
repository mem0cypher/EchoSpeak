"""
Reflection Engine for EchoSpeak v7.0.0.

Provides general-purpose reflection capabilities for the agent pipeline:
- Per-step reflection: "Does this tool result satisfy what we need?"
- Post-plan reflection: "Did the overall execution match user intent?"
- Anti-loop guards: max cycles, trivial-tool skipping, simple-query bypass

The engine is tool-agnostic — it evaluates ANY tool result against the
user's original goal and the current task description. It does not
hardcode specific tool names or sequences.

Absorbs the existing WebTaskReflector as a specialized fast-path for
web_search result quality checks (date staleness, market queries, etc.).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core import EchoSpeakAgent

logger = logging.getLogger(__name__)


# ── Configuration defaults ──────────────────────────────────────────

DEFAULT_MAX_REFLECTION_CYCLES = 2
DEFAULT_REFLECTION_TEMPERATURE = 0.1
# Tools whose output is trivially correct and never need reflection
TRIVIAL_TOOLS = frozenset({
    "get_system_time",
    "calculate",
    "project_update_context",
})
# Minimum plan size to trigger per-step reflection
MIN_PLAN_SIZE_FOR_REFLECTION = 2
# Result length threshold — long results are usually acceptable
SUBSTANTIAL_RESULT_LENGTH = 200


# ── Data classes ────────────────────────────────────────────────────

@dataclass
class ReflectionResult:
    """Outcome of a single reflection evaluation."""
    accepted: bool
    reason: str = ""
    suggestion: str = ""
    cycle: int = 0


@dataclass
class StepReflectionState:
    """Tracks reflection cycles for a single task step."""
    task_index: int
    cycles_used: int = 0
    reflections: List[ReflectionResult] = field(default_factory=list)


# ── ReflectionEngine ───────────────────────────────────────────────

class ReflectionEngine:
    """
    General-purpose reflection engine for multi-step task plans.

    Evaluates tool results between steps to determine if the agent
    should accept the result, retry with adjustments, or skip to
    the next task. Tool-agnostic — works with any tool in the registry.

    Usage:
        engine = ReflectionEngine(agent)
        result = engine.reflect_on_step(task, tool_result, user_goal, plan_size)
        if not result.accepted:
            # retry or adjust
    """

    def __init__(
        self,
        agent: "EchoSpeakAgent",
        max_cycles: int = DEFAULT_MAX_REFLECTION_CYCLES,
        reflection_temp: float = DEFAULT_REFLECTION_TEMPERATURE,
    ):
        self.agent = agent
        self.max_cycles = max_cycles
        self.reflection_temp = reflection_temp
        self._step_states: Dict[int, StepReflectionState] = {}

    # ── Public API ──────────────────────────────────────────────────

    def should_reflect(
        self,
        task: Dict[str, Any],
        result: str,
        plan_size: int,
    ) -> bool:
        """
        Heuristic: decide whether this step warrants reflection.

        Returns False for:
        - Plans smaller than MIN_PLAN_SIZE_FOR_REFLECTION
        - Trivial tools (time, calculate)
        - Results that are already substantial (>200 chars)
        - Steps that have exhausted their reflection cycles
        """
        tool_name = str(task.get("tool", "")).strip()
        task_index = int(task.get("index", 0))

        # Small plans don't need per-step reflection
        if plan_size < MIN_PLAN_SIZE_FOR_REFLECTION:
            return False

        # Trivial tools are always correct
        if tool_name in TRIVIAL_TOOLS:
            return False

        # Check cycle budget
        state = self._step_states.get(task_index)
        if state and state.cycles_used >= self.max_cycles:
            return False

        # Substantial results are usually fine
        result_len = len(str(result or ""))
        if result_len > SUBSTANTIAL_RESULT_LENGTH:
            return False

        # Empty or very short results should be reflected on
        if result_len < 50:
            return True

        # Failed results need reflection
        result_lower = str(result or "").lower()
        failure_signals = [
            "error", "failed", "not found", "no results",
            "unavailable", "timed out", "timeout",
        ]
        if any(sig in result_lower for sig in failure_signals):
            return True

        return False

    def reflect_on_step(
        self,
        task: Dict[str, Any],
        result: str,
        user_goal: str,
        plan_size: int,
        plan_tasks: Optional[List[Dict[str, Any]]] = None,
    ) -> ReflectionResult:
        """
        Evaluate a single step's result against the user's goal.

        Uses a cheap LLM call to ask: "Does this result satisfy what
        we need for the current step and the overall goal?"

        Returns a ReflectionResult with accepted=True/False and
        an optional suggestion for how to retry.
        """
        task_index = int(task.get("index", 0))
        tool_name = str(task.get("tool", "")).strip()
        description = str(task.get("description", tool_name))

        # Initialize or get step state
        if task_index not in self._step_states:
            self._step_states[task_index] = StepReflectionState(task_index=task_index)
        state = self._step_states[task_index]

        # Check cycle budget
        if state.cycles_used >= self.max_cycles:
            r = ReflectionResult(
                accepted=True,
                reason="Max reflection cycles reached, accepting result",
                cycle=state.cycles_used,
            )
            state.reflections.append(r)
            return r

        state.cycles_used += 1

        # Build the reflection prompt
        prompt = self._build_step_reflection_prompt(
            task, result, user_goal, description, plan_tasks, state
        )

        # Call LLM for reflection
        try:
            llm_wrapper = getattr(self.agent, "llm_wrapper", None)
            if llm_wrapper is None:
                return ReflectionResult(accepted=True, reason="No LLM available", cycle=state.cycles_used)

            raw = str(llm_wrapper.invoke(prompt) or "").strip()
            reflection = self._parse_reflection_response(raw, state.cycles_used)
            state.reflections.append(reflection)

            logger.info(
                "ReflectionEngine: step %d (%s) cycle %d → accepted=%s reason=%s",
                task_index, tool_name, state.cycles_used,
                reflection.accepted, reflection.reason[:80],
            )
            return reflection

        except Exception as e:
            logger.warning("ReflectionEngine: reflection LLM call failed: %s", e)
            r = ReflectionResult(
                accepted=True,
                reason=f"Reflection failed ({e}), accepting result",
                cycle=state.cycles_used,
            )
            state.reflections.append(r)
            return r

    def reflect_on_plan(
        self,
        user_goal: str,
        completed_tasks: List[Dict[str, Any]],
    ) -> ReflectionResult:
        """
        Post-plan reflection: evaluate whether the overall execution
        accomplished the user's goal.

        Called after all tasks in a plan have completed.
        """
        if not completed_tasks:
            return ReflectionResult(accepted=True, reason="No tasks to reflect on")

        # Build summary of completed tasks
        task_summaries = []
        for t in completed_tasks:
            desc = t.get("description", t.get("tool", "Unknown"))
            status = t.get("status", "unknown")
            result_preview = str(t.get("result", ""))[:150]
            task_summaries.append(f"- {desc}: [{status}] {result_preview}")

        prompt = (
            "You are evaluating whether a multi-step task plan accomplished the user's goal.\n\n"
            f'User\'s original request: "{user_goal}"\n\n'
            f"Completed tasks:\n" + "\n".join(task_summaries) + "\n\n"
            "Answer with EXACTLY one of:\n"
            "ACCOMPLISHED: <brief reason>\n"
            "PARTIAL: <what was missed>\n"
            "FAILED: <what went wrong>\n\n"
            "Be concise. One line only."
        )

        try:
            llm_wrapper = getattr(self.agent, "llm_wrapper", None)
            if llm_wrapper is None:
                return ReflectionResult(accepted=True, reason="No LLM available")

            raw = str(llm_wrapper.invoke(prompt) or "").strip()
            raw_lower = raw.lower()

            if raw_lower.startswith("accomplished"):
                return ReflectionResult(accepted=True, reason=raw)
            elif raw_lower.startswith("partial"):
                return ReflectionResult(accepted=False, reason=raw)
            elif raw_lower.startswith("failed"):
                return ReflectionResult(accepted=False, reason=raw)
            else:
                # Ambiguous — accept
                return ReflectionResult(accepted=True, reason=raw)

        except Exception as e:
            logger.warning("ReflectionEngine: plan reflection failed: %s", e)
            return ReflectionResult(accepted=True, reason=f"Plan reflection failed ({e})")

    def get_retry_params(
        self,
        task: Dict[str, Any],
        reflection: ReflectionResult,
        original_params: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Generate adjusted parameters for a retry based on reflection feedback.

        Returns new params dict if retry is warranted, or None if no
        meaningful adjustment can be made.
        """
        if reflection.accepted:
            return None

        suggestion = (reflection.suggestion or "").strip()
        if not suggestion:
            return None

        tool_name = str(task.get("tool", "")).strip()

        # For search tools, the suggestion is typically a refined query
        if tool_name in {"web_search"}:
            new_params = dict(original_params)
            new_params["q"] = suggestion
            return new_params

        # For browse tools, the suggestion might be a different URL
        if tool_name in {"browse_task"}:
            new_params = dict(original_params)
            if suggestion.startswith("http"):
                new_params["url"] = suggestion
            return new_params

        # Generic: can't auto-adjust params for unknown tools
        return None

    def reset(self) -> None:
        """Reset all step states for a new plan."""
        self._step_states.clear()

    # ── Private helpers ─────────────────────────────────────────────

    def _build_step_reflection_prompt(
        self,
        task: Dict[str, Any],
        result: str,
        user_goal: str,
        description: str,
        plan_tasks: Optional[List[Dict[str, Any]]],
        state: StepReflectionState,
    ) -> str:
        """Build the LLM prompt for step-level reflection."""
        tool_name = str(task.get("tool", "")).strip()
        result_preview = str(result or "")[:500]

        # Show what comes next in the plan
        next_steps = ""
        if plan_tasks:
            task_index = int(task.get("index", 0))
            remaining = [
                t for t in plan_tasks
                if int(t.get("index", 0)) > task_index
            ]
            if remaining:
                next_steps = "\nUpcoming steps:\n" + "\n".join(
                    f"- {t.get('description', t.get('tool', '?'))}"
                    for t in remaining[:3]
                )

        # Include previous reflection feedback if retrying
        prev_feedback = ""
        if state.reflections:
            last = state.reflections[-1]
            prev_feedback = f"\nPrevious reflection: {last.reason}"

        prompt = (
            "You are a quality-check agent evaluating a tool result.\n\n"
            f'User\'s goal: "{user_goal}"\n'
            f'Current step: "{description}" (tool: {tool_name})\n'
            f"Result:\n{result_preview}\n"
            f"{next_steps}"
            f"{prev_feedback}\n\n"
            "Does this result provide what we need for this step?\n\n"
            "Answer with EXACTLY one of:\n"
            "ACCEPT: <brief reason>\n"
            "RETRY: <suggested refined query or approach>\n\n"
            "Be concise. One line only. If the result has useful content, ACCEPT it."
        )
        return prompt

    def _parse_reflection_response(self, raw: str, cycle: int) -> ReflectionResult:
        """Parse the LLM's reflection response."""
        raw_stripped = raw.strip()
        raw_lower = raw_stripped.lower()

        if raw_lower.startswith("accept"):
            reason = raw_stripped[len("accept"):].strip(": ")
            return ReflectionResult(accepted=True, reason=reason or "Accepted", cycle=cycle)

        if raw_lower.startswith("retry"):
            suggestion = raw_stripped[len("retry"):].strip(": ")
            return ReflectionResult(
                accepted=False,
                reason="Reflection suggests retry",
                suggestion=suggestion,
                cycle=cycle,
            )

        # Ambiguous — default to accept to avoid infinite loops
        return ReflectionResult(accepted=True, reason=raw_stripped or "Ambiguous, accepting", cycle=cycle)
