"""
Multi-Agent Orchestrator for EchoSpeak.

Decomposes complex queries into sub-tasks, dispatches them in parallel
across the agent pool, respects dependency ordering (DAG), and aggregates
results via LLM.
"""

from __future__ import annotations

import json
import time
import uuid
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger
from agent.state import get_state_store


# ═══════════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════════

class SubTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SubTask:
    """A single sub-task in an orchestration plan."""
    id: str = ""
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    priority: int = 0  # lower = higher priority
    status: SubTaskStatus = SubTaskStatus.PENDING
    result: str = ""
    error: Optional[str] = None
    latency_ms: float = 0.0
    execution_id: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "depends_on": self.depends_on,
            "priority": self.priority,
            "status": self.status.value,
            "result": self.result[:500] if self.result else "",
            "error": self.error,
            "latency_ms": round(self.latency_ms, 2),
            "execution_id": self.execution_id,
        }


@dataclass
class OrchestrationPlan:
    """A full orchestration plan with sub-tasks and results."""
    id: str = ""
    query: str = ""
    sub_tasks: list[SubTask] = field(default_factory=list)
    status: PlanStatus = PlanStatus.CREATED
    merged_response: str = ""
    created_at: float = 0.0
    completed_at: float = 0.0
    total_latency_ms: float = 0.0
    execution_id: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "query": self.query[:200],
            "status": self.status.value,
            "sub_tasks": [st.to_dict() for st in self.sub_tasks],
            "merged_response": self.merged_response,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "execution_id": self.execution_id,
        }


# ═══════════════════════════════════════════════════════════════════
# Orchestrator
# ═══════════════════════════════════════════════════════════════════

class Orchestrator:
    """Multi-agent task orchestrator.

    Pipeline: decompose → execute (parallel, DAG-aware) → aggregate.
    """

    def __init__(
        self,
        max_subtasks: int = 5,
        timeout: int = 120,
        max_workers: int = 4,
    ):
        self._lock = Lock()
        self._plans: Dict[str, OrchestrationPlan] = {}
        self._max_subtasks = max_subtasks
        self._timeout = timeout
        self._max_workers = max_workers

    # ── Decompose ────────────────────────────────────────────────

    def decompose(self, query: str) -> list[SubTask]:
        """Use LLM to decompose a complex query into sub-tasks.

        Returns a list of SubTask objects. For simple queries, returns
        a single sub-task (pass-through, no overhead).
        """
        try:
            from agent.core import LLMWrapper
            llm = LLMWrapper()
        except Exception:
            logger.warning("Orchestrator: LLMWrapper unavailable, using single task")
            return [SubTask(id="t1", description=query, priority=0)]

        prompt = (
            "You are a task decomposition engine. Analyze the user query and break it into "
            "independent or dependent sub-tasks that can be executed by separate AI agents.\n\n"
            "Rules:\n"
            "- Return ONLY valid JSON, no markdown, no explanation\n"
            "- Each sub-task has: id (t1, t2...), description, depends_on (list of ids), priority (0=highest)\n"
            "- If the query is simple (single intent), return exactly ONE sub-task\n"
            f"- Maximum {self._max_subtasks} sub-tasks\n"
            "- Keep descriptions self-contained — each agent has no context of other sub-tasks\n\n"
            f"User query: {query}\n\n"
            'Return format: {"tasks": [{"id": "t1", "description": "...", "depends_on": [], "priority": 0}]}'
        )

        try:
            raw = str(llm.invoke(prompt) or "").strip()
            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            data = json.loads(raw)
            tasks_data = data.get("tasks", [])
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning(f"Orchestrator decompose failed: {exc}, using single task")
            return [SubTask(id="t1", description=query, priority=0)]

        sub_tasks: list[SubTask] = []
        for td in tasks_data[:self._max_subtasks]:
            sub_tasks.append(SubTask(
                id=td.get("id", f"t{len(sub_tasks)+1}"),
                description=td.get("description", query),
                depends_on=td.get("depends_on", []),
                priority=td.get("priority", len(sub_tasks)),
            ))

        if not sub_tasks:
            sub_tasks = [SubTask(id="t1", description=query, priority=0)]

        return sub_tasks

    # ── Execute ──────────────────────────────────────────────────

    def _execute_subtask(self, sub_task: SubTask, dep_results: dict[str, str]) -> SubTask:
        """Execute a single sub-task using a pooled agent."""
        start = time.time()
        sub_task.status = SubTaskStatus.RUNNING
        state_store = get_state_store()
        execution = state_store.create_execution(
            kind="orchestration_subtask",
            thread_id=f"orchestrator:{sub_task.id}",
            source="orchestrator",
            status="running",
            query=sub_task.description[:2000],
            metadata={"depends_on": list(sub_task.depends_on)},
        )
        sub_task.execution_id = execution.id

        try:
            # Lazy import to avoid circular dep
            from api.server import get_agent

            # Build context from dependency results
            context = ""
            if dep_results:
                dep_lines = [f"[{k}]: {v[:300]}" for k, v in dep_results.items()]
                context = "Context from prior sub-tasks:\n" + "\n".join(dep_lines) + "\n\n"

            full_query = context + sub_task.description if context else sub_task.description

            agent = get_agent(f"orch_{sub_task.id}_{uuid.uuid4().hex[:6]}")
            response, success = agent.process_query(
                full_query,
                include_memory=False,
                source="orchestrator",
            )

            sub_task.result = str(response)
            sub_task.status = SubTaskStatus.COMPLETED if success else SubTaskStatus.FAILED
            if not success:
                sub_task.error = "Agent returned failure"
            state_store.update_execution(
                execution.id,
                status="completed" if success else "failed",
                success=bool(success),
                response_preview=sub_task.result[:500],
                error=sub_task.error or "",
            )
        except Exception as exc:
            logger.error(f"Orchestrator sub-task {sub_task.id} failed: {exc}")
            sub_task.status = SubTaskStatus.FAILED
            sub_task.error = str(exc)
            sub_task.result = f"Sub-task failed: {exc}"
            state_store.update_execution(
                execution.id,
                status="failed",
                success=False,
                response_preview=sub_task.result[:500],
                error=str(exc),
            )

        sub_task.latency_ms = (time.time() - start) * 1000
        return sub_task

    def execute(self, sub_tasks: list[SubTask]) -> list[SubTask]:
        """Execute sub-tasks with dependency-aware parallel scheduling.

        Tasks with no dependencies run in parallel. Tasks with dependencies
        wait for their prerequisites to complete.
        """
        completed_results: dict[str, str] = {}
        remaining = {st.id: st for st in sub_tasks}
        completed_ids: set[str] = set()

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            while remaining:
                # Find tasks whose dependencies are all satisfied
                ready = [
                    st for st in remaining.values()
                    if all(dep in completed_ids for dep in st.depends_on)
                ]

                if not ready:
                    # Deadlock — skip remaining
                    for st in remaining.values():
                        st.status = SubTaskStatus.SKIPPED
                        st.error = "Dependency deadlock"
                    break

                # Sort by priority
                ready.sort(key=lambda s: s.priority)

                # Dispatch ready tasks in parallel
                futures = {}
                for st in ready:
                    dep_results = {d: completed_results.get(d, "") for d in st.depends_on}
                    futures[executor.submit(self._execute_subtask, st, dep_results)] = st.id

                for future in as_completed(futures, timeout=self._timeout):
                    task_id = futures[future]
                    try:
                        result_st = future.result(timeout=self._timeout)
                        completed_results[task_id] = result_st.result
                        completed_ids.add(task_id)
                    except Exception as exc:
                        st = remaining.get(task_id)
                        if st:
                            st.status = SubTaskStatus.FAILED
                            st.error = f"Timeout or error: {exc}"
                        completed_ids.add(task_id)
                    finally:
                        remaining.pop(task_id, None)

        return sub_tasks

    # ── Aggregate ────────────────────────────────────────────────

    def aggregate(self, query: str, sub_tasks: list[SubTask]) -> str:
        """Merge sub-task results into a unified response via LLM."""
        # Single task — return directly, no aggregation overhead
        if len(sub_tasks) == 1:
            return sub_tasks[0].result

        # Filter successful results
        results = [
            f"[Sub-task {st.id}: {st.description}]\n{st.result}"
            for st in sub_tasks
            if st.status == SubTaskStatus.COMPLETED and st.result
        ]

        if not results:
            failed = [st for st in sub_tasks if st.error]
            if failed:
                return f"All sub-tasks failed. Errors: {'; '.join(st.error or '' for st in failed)}"
            return "No results were produced."

        try:
            from agent.core import LLMWrapper
            llm = LLMWrapper()
        except Exception:
            return "\n\n---\n\n".join(results)

        prompt = (
            "You are a result aggregation engine. The user's complex query was decomposed "
            "into sub-tasks, each processed by a separate agent. Merge the results into a "
            "single coherent, natural response.\n\n"
            "Rules:\n"
            "- Combine all results smoothly — don't mention sub-tasks or agents\n"
            "- Remove duplicate information\n"
            "- Be conversational and concise\n"
            "- If some sub-tasks failed, acknowledge gaps naturally\n\n"
            f"Original query: {query}\n\n"
            "Sub-task results:\n" + "\n\n".join(results) + "\n\n"
            "Merged response:"
        )

        try:
            return str(llm.invoke(prompt) or "").strip()
        except Exception:
            return "\n\n---\n\n".join(results)

    # ── Run (full pipeline) ──────────────────────────────────────

    def run(self, query: str) -> OrchestrationPlan:
        """Full orchestration pipeline: decompose → execute → aggregate."""
        state_store = get_state_store()
        execution = state_store.create_execution(
            kind="orchestration",
            thread_id="orchestrator",
            source="orchestrator",
            status="running",
            query=query[:2000],
        )
        plan = OrchestrationPlan(
            id=str(uuid.uuid4()),
            query=query,
            created_at=time.time(),
            status=PlanStatus.CREATED,
            execution_id=execution.id,
        )

        with self._lock:
            self._plans[plan.id] = plan

        # Emit stream event for orchestration start
        try:
            from agent.stream_events import get_stream_buffer
            sb = get_stream_buffer(f"orch_{plan.id}")
            sb.push_status("orchestrating")
        except Exception:
            sb = None

        logger.info(f"Orchestration {plan.id}: decomposing query")
        plan.sub_tasks = self.decompose(query)
        plan.status = PlanStatus.RUNNING

        if sb:
            try:
                sb.push_status(f"executing {len(plan.sub_tasks)} sub-tasks")
            except Exception:
                pass

        logger.info(f"Orchestration {plan.id}: executing {len(plan.sub_tasks)} sub-tasks")
        plan.sub_tasks = self.execute(plan.sub_tasks)

        if sb:
            try:
                sb.push_status("aggregating results")
            except Exception:
                pass

        logger.info(f"Orchestration {plan.id}: aggregating results")
        plan.merged_response = self.aggregate(query, plan.sub_tasks)

        plan.status = PlanStatus.COMPLETED
        plan.completed_at = time.time()
        plan.total_latency_ms = (plan.completed_at - plan.created_at) * 1000

        # Check for partial failures
        failed = [st for st in plan.sub_tasks if st.status == SubTaskStatus.FAILED]
        if failed and len(failed) == len(plan.sub_tasks):
            plan.status = PlanStatus.FAILED
        state_store.update_execution(
            execution.id,
            status="completed" if plan.status != PlanStatus.FAILED else "failed",
            success=bool(plan.status != PlanStatus.FAILED),
            response_preview=plan.merged_response[:500],
            error="; ".join([st.error or "" for st in failed if st.error]),
            tools_used=[st.id for st in plan.sub_tasks],
            metadata={"sub_task_count": len(plan.sub_tasks), "failed_sub_task_count": len(failed)},
        )

        # Record orchestration as an observability request metric
        try:
            from agent.observability import get_observability_collector, RequestMetric
            get_observability_collector().record_request(RequestMetric(
                request_id=plan.id,
                started_at=plan.created_at,
                finished_at=plan.completed_at,
                latency_ms=plan.total_latency_ms,
                tool_count=len(plan.sub_tasks),
                source="orchestrator",
                success=(plan.status != PlanStatus.FAILED),
            ))
        except Exception:
            pass

        if sb:
            try:
                sb.push_status("done")
            except Exception:
                pass

        logger.info(
            f"Orchestration {plan.id}: done in {plan.total_latency_ms:.0f}ms, "
            f"{len(plan.sub_tasks)} tasks, {len(failed)} failed"
        )
        return plan


    # ── Plan retrieval ───────────────────────────────────────────

    def get_plan(self, plan_id: str) -> Optional[OrchestrationPlan]:
        with self._lock:
            return self._plans.get(plan_id)

    def list_plans(self, limit: int = 20) -> list[OrchestrationPlan]:
        with self._lock:
            plans = sorted(self._plans.values(), key=lambda p: p.created_at, reverse=True)
            return plans[:limit]


# ═══════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════

_orchestrator: Optional[Orchestrator] = None


def get_orchestrator() -> Orchestrator:
    """Get or create the global orchestrator."""
    global _orchestrator
    if _orchestrator is None:
        try:
            from config import config
            max_st = int(getattr(config, "orchestration_max_subtasks", 5))
            timeout = int(getattr(config, "orchestration_timeout", 120))
        except Exception:
            max_st, timeout = 5, 120
        _orchestrator = Orchestrator(max_subtasks=max_st, timeout=timeout)
    return _orchestrator
