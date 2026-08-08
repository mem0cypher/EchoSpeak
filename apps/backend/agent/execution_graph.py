"""Versioned TaskRun execution-graph contracts.

The graph is durable orchestration state owned by :class:`TaskRun`.  It is not
an executor and it cannot complete a TaskRun.  Tool execution remains owned by
durable ToolRuns, requirement sufficiency remains owned by the canonical
runtime evaluator, and response finalization remains owned by the existing
model-control-plane gate.

The production graph is built directly from the TaskRun requirement ledger.
Older serialized compatibility graphs are accepted only by the TaskRun
migration reader and are rebuilt into this runtime-owned representation before
they can participate in current work.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import deque
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.research_runtime import (
    CompletionVerdict,
    RequirementState,
    RequirementStatus,
    ResearchBudgetPolicy,
    TurnRequirement,
)


EXECUTION_GRAPH_SCHEMA_VERSION = 1
MAX_GRAPH_NODES = 128
MAX_GRAPH_EDGES = 512
MAX_GRAPH_CHECKPOINTS = 64


class ExecutionProfile(str, Enum):
    CHAT = "chat"
    WORK = "work"
    CODE = "code"


class GraphSource(str, Enum):
    RUNTIME = "runtime"
    COMPATIBILITY = "compatibility"
    PLANNED = "planned"
    HANDOFF = "handoff"
    ROUTINE = "routine"
    AUTOMATION = "automation"


class GraphNodeKind(str, Enum):
    START = "start"
    REQUIREMENT = "requirement"
    MODEL_STEP = "model_step"
    TOOL = "tool"
    SPECIALIST = "specialist"
    APPROVAL = "approval"
    JOIN = "join"
    HANDOFF = "handoff"
    SUBAGENT = "subagent"
    MEDIA_JOB = "media_job"
    FINALIZATION = "finalization"


class GraphNodeStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_GRAPH_NODE_STATUSES = frozenset({
    GraphNodeStatus.COMPLETED,
    GraphNodeStatus.SKIPPED,
    GraphNodeStatus.BLOCKED,
    GraphNodeStatus.FAILED,
    GraphNodeStatus.CANCELLED,
})


class GraphEdgeKind(str, Enum):
    SEQUENCE = "sequence"
    DEPENDENCY = "dependency"
    SUCCESS = "success"
    FAILURE = "failure"
    RETRY = "retry"
    FALLBACK = "fallback"


class GraphTransitionKind(str, Enum):
    START = "start"
    WAIT = "wait"
    COMPLETE = "complete"
    SKIP = "skip"
    BLOCK = "block"
    FAIL = "fail"
    CANCEL = "cancel"
    RETRY = "retry"
    LINK_TOOL_RUN = "link_tool_run"
    LINK_CHILD_TASK = "link_child_task"


class GraphRetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=1, ge=1, le=20)
    backoff_seconds: float = Field(default=0.0, ge=0.0, le=3600.0)
    retryable_reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_codes(self) -> "GraphRetryPolicy":
        self.retryable_reason_codes = _bounded_unique(self.retryable_reason_codes, limit=32, size=100)
        return self


class GraphBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_transitions: int = Field(default=64, ge=1, le=2048)
    max_wall_time_seconds: float = Field(default=120.0, ge=0.0, le=86400.0)
    max_tool_runs: int = Field(default=24, ge=0, le=512)
    max_concurrency: int = Field(default=1, ge=1, le=8)


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    kind: GraphNodeKind
    label: str = ""
    requirement_id: str = ""
    capability_ids: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    retry_policy: GraphRetryPolicy = Field(default_factory=GraphRetryPolicy)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_node(self) -> "GraphNode":
        self.node_id = _canonical_id(self.node_id, field="node_id")
        self.label = re.sub(r"\s+", " ", str(self.label or "")).strip()[:240]
        self.requirement_id = str(self.requirement_id or "").strip()[:100]
        self.capability_ids = _bounded_unique(self.capability_ids, limit=32, size=160)
        self.depends_on = _bounded_unique(self.depends_on, limit=32, size=100)
        if self.kind in {GraphNodeKind.REQUIREMENT, GraphNodeKind.SPECIALIST} and not self.requirement_id:
            raise ValueError(f"{self.kind.value} node {self.node_id!r} requires requirement_id")
        if self.kind == GraphNodeKind.TOOL and not self.capability_ids:
            raise ValueError(f"tool node {self.node_id!r} requires at least one capability_id")
        if self.kind == GraphNodeKind.FINALIZATION and (
            self.requirement_id or self.capability_ids or self.depends_on
        ):
            raise ValueError("finalization node cannot own requirement, capability, or dependency metadata")
        return self


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_node_id: str
    target_node_id: str
    kind: GraphEdgeKind = GraphEdgeKind.SEQUENCE
    condition_code: str = ""
    max_traversals: int = Field(default=1, ge=1, le=20)

    @model_validator(mode="after")
    def normalize_edge(self) -> "GraphEdge":
        self.source_node_id = _canonical_id(self.source_node_id, field="source_node_id")
        self.target_node_id = _canonical_id(self.target_node_id, field="target_node_id")
        self.condition_code = str(self.condition_code or "").strip()[:100]
        if self.source_node_id == self.target_node_id and self.kind != GraphEdgeKind.RETRY:
            raise ValueError("only an explicit bounded retry edge may target its source")
        if self.kind != GraphEdgeKind.RETRY and self.max_traversals != 1:
            raise ValueError("only retry edges may declare max_traversals greater than one")
        return self


class TaskGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = EXECUTION_GRAPH_SCHEMA_VERSION
    compatibility_revision: int = Field(default=2, ge=1)
    graph_id: str
    source: GraphSource = GraphSource.RUNTIME
    entry_node_id: str
    finalization_node_id: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    budget: GraphBudget = Field(default_factory=GraphBudget)
    created_at: float = Field(default_factory=time.time)

    @model_validator(mode="before")
    @classmethod
    def reject_future_schema(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and int(value.get("schema_version") or 1) > EXECUTION_GRAPH_SCHEMA_VERSION:
            raise ValueError("unsupported future TaskGraph schema version")
        return value

    @model_validator(mode="after")
    def validate_graph(self) -> "TaskGraph":
        self.schema_version = EXECUTION_GRAPH_SCHEMA_VERSION
        self.graph_id = _canonical_id(self.graph_id, field="graph_id")
        self.entry_node_id = _canonical_id(self.entry_node_id, field="entry_node_id")
        self.finalization_node_id = _canonical_id(
            self.finalization_node_id, field="finalization_node_id"
        )
        if not self.nodes or len(self.nodes) > MAX_GRAPH_NODES:
            raise ValueError(f"TaskGraph must contain 1..{MAX_GRAPH_NODES} nodes")
        if len(self.edges) > MAX_GRAPH_EDGES:
            raise ValueError(f"TaskGraph exceeds {MAX_GRAPH_EDGES} edges")

        node_ids = [item.node_id for item in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("TaskGraph node ids must be unique")
        nodes = {item.node_id: item for item in self.nodes}
        if self.entry_node_id not in nodes:
            raise ValueError("TaskGraph entry node does not exist")
        if self.finalization_node_id not in nodes:
            raise ValueError("TaskGraph finalization node does not exist")
        finalizers = [item.node_id for item in self.nodes if item.kind == GraphNodeKind.FINALIZATION]
        if finalizers != [self.finalization_node_id]:
            raise ValueError("TaskGraph must have exactly one declared finalization node")

        edge_keys: set[tuple[str, str, str]] = set()
        incoming: dict[str, set[str]] = {item: set() for item in node_ids}
        outgoing: dict[str, set[str]] = {item: set() for item in node_ids}
        dag_outgoing: dict[str, set[str]] = {item: set() for item in node_ids}
        for edge in self.edges:
            if edge.source_node_id not in nodes or edge.target_node_id not in nodes:
                raise ValueError(
                    f"TaskGraph edge references unknown node: {edge.source_node_id}->{edge.target_node_id}"
                )
            key = (edge.source_node_id, edge.target_node_id, edge.kind.value)
            if key in edge_keys:
                raise ValueError(f"Duplicate TaskGraph edge: {key}")
            edge_keys.add(key)
            incoming[edge.target_node_id].add(edge.source_node_id)
            outgoing[edge.source_node_id].add(edge.target_node_id)
            if edge.kind != GraphEdgeKind.RETRY:
                dag_outgoing[edge.source_node_id].add(edge.target_node_id)

        if incoming[self.entry_node_id]:
            raise ValueError("TaskGraph entry node cannot have incoming edges")
        if outgoing[self.finalization_node_id]:
            raise ValueError("TaskGraph finalization node cannot have outgoing edges")
        for node in self.nodes:
            unknown_dependencies = [item for item in node.depends_on if item not in nodes]
            if unknown_dependencies:
                raise ValueError(
                    f"TaskGraph node {node.node_id} has unknown dependencies: {unknown_dependencies}"
                )
            if node.kind == GraphNodeKind.JOIN and len(incoming[node.node_id]) < 2:
                raise ValueError(f"join node {node.node_id!r} requires at least two incoming branches")

        reachable = _reachable(self.entry_node_id, outgoing)
        if reachable != set(node_ids):
            raise ValueError(f"TaskGraph contains unreachable nodes: {sorted(set(node_ids) - reachable)}")
        can_finalize = _reachable(self.finalization_node_id, _reverse(outgoing))
        if can_finalize != set(node_ids):
            raise ValueError(
                f"TaskGraph contains nodes without a finalization path: {sorted(set(node_ids) - can_finalize)}"
            )
        if _contains_cycle(node_ids, dag_outgoing):
            raise ValueError("TaskGraph contains an unbounded cycle; cycles require explicit retry edges")
        return self

    def node_map(self) -> dict[str, GraphNode]:
        return {item.node_id: item for item in self.nodes}


class GraphNodeState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    status: GraphNodeStatus = GraphNodeStatus.PENDING
    attempt_count: int = Field(default=0, ge=0)
    transition_count: int = Field(default=0, ge=0)
    tool_run_ids: list[str] = Field(default_factory=list)
    child_task_run_ids: list[str] = Field(default_factory=list)
    outcome_code: str = ""
    diagnostic_code: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0
    updated_at: float = Field(default_factory=time.time)

    @model_validator(mode="after")
    def normalize_state(self) -> "GraphNodeState":
        self.node_id = _canonical_id(self.node_id, field="node_id")
        self.tool_run_ids = _bounded_unique(self.tool_run_ids, limit=256, size=100)
        self.child_task_run_ids = _bounded_unique(self.child_task_run_ids, limit=64, size=100)
        self.outcome_code = str(self.outcome_code or "").strip()[:120]
        self.diagnostic_code = str(self.diagnostic_code or "").strip()[:240]
        return self


class GraphCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    task_revision: int = Field(ge=1)
    reason_code: str
    state_sha256: str
    active_node_ids: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)


class TaskGraphState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = EXECUTION_GRAPH_SCHEMA_VERSION
    graph_id: str
    node_states: dict[str, GraphNodeState] = Field(default_factory=dict)
    active_node_ids: list[str] = Field(default_factory=list)
    edge_traversals: dict[str, int] = Field(default_factory=dict)
    transition_count: int = Field(default=0, ge=0)
    shadow_completion_ready: bool = False
    shadow_completion_reason_code: str = "requirements_pending"
    checkpoints: list[GraphCheckpoint] = Field(default_factory=list)
    updated_at: float = Field(default_factory=time.time)

    @model_validator(mode="before")
    @classmethod
    def reject_future_schema(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and int(value.get("schema_version") or 1) > EXECUTION_GRAPH_SCHEMA_VERSION:
            raise ValueError("unsupported future TaskGraphState schema version")
        return value

    @model_validator(mode="after")
    def normalize_state(self) -> "TaskGraphState":
        self.schema_version = EXECUTION_GRAPH_SCHEMA_VERSION
        self.graph_id = _canonical_id(self.graph_id, field="graph_id")
        self.active_node_ids = _bounded_unique(self.active_node_ids, limit=MAX_GRAPH_NODES, size=100)
        self.shadow_completion_reason_code = str(
            self.shadow_completion_reason_code or "requirements_pending"
        ).strip()[:160]
        self.checkpoints = list(self.checkpoints[-MAX_GRAPH_CHECKPOINTS:])
        return self


class GraphTransition(BaseModel):
    """One runtime-authored graph mutation request.

    Requirement nodes are changed only by projecting RequirementState, and the
    finalization node is changed only by projecting the canonical TaskRun
    lifecycle.  This contract is for planned orchestration nodes and binding
    metadata, not a model-writable completion shortcut.
    """

    model_config = ConfigDict(extra="forbid")

    transition_id: str
    node_id: str
    kind: GraphTransitionKind
    reason_code: str
    tool_run_id: str = ""
    child_task_run_id: str = ""
    expected_status: Optional[GraphNodeStatus] = None
    created_at: float = Field(default_factory=time.time)

    @model_validator(mode="after")
    def normalize_transition(self) -> "GraphTransition":
        self.transition_id = _canonical_id(self.transition_id, field="transition_id")
        self.node_id = _canonical_id(self.node_id, field="node_id")
        self.reason_code = str(self.reason_code or "").strip()[:160]
        self.tool_run_id = str(self.tool_run_id or "").strip()[:100]
        self.child_task_run_id = str(self.child_task_run_id or "").strip()[:100]
        if not self.reason_code:
            raise ValueError("GraphTransition reason_code is required")
        if self.kind == GraphTransitionKind.LINK_TOOL_RUN and not self.tool_run_id:
            raise ValueError("link_tool_run transition requires tool_run_id")
        if self.kind == GraphTransitionKind.LINK_CHILD_TASK and not self.child_task_run_id:
            raise ValueError("link_child_task transition requires child_task_run_id")
        return self


def execution_profile_for(
    capabilities: Iterable[str], *, source: str = "", requested_operation: str = ""
) -> ExecutionProfile:
    """Derive a product surface from runtime capabilities, never raw prompt semantics.

    ``requested_operation`` alone must not promote Chat into Work. Work requires
    non-conversational capabilities or an automation-style source. Coding caps
    still map to Code.
    """

    values = {str(item or "").strip() for item in capabilities if str(item or "").strip()}
    if values & {"coding_read", "coding_write", "terminal"}:
        return ExecutionProfile.CODE
    work_caps = values - {"conversation", "memory", "chat"}
    if work_caps or str(source or "").casefold() in {
        "routine", "automation", "heartbeat", "a2a"
    }:
        return ExecutionProfile.WORK
    # Ignore bare requested_operation when only conversational capabilities exist.
    _ = requested_operation
    return ExecutionProfile.CHAT


def build_task_graph(
    *,
    task_run_id: str,
    requirements: Iterable[TurnRequirement],
    budget: Optional[ResearchBudgetPolicy],
) -> TaskGraph:
    """Build the minimal TaskRun-owned graph around canonical requirements.

    The graph describes ordering and projects lifecycle state. It never owns
    requirement sufficiency, ToolRun truth, specialist truth, or finalization.
    """

    rows = list(requirements)
    graph_id = f"graph-{task_run_id}"
    start_id = "start"
    final_id = "finalize"
    nodes: list[GraphNode] = [GraphNode(node_id=start_id, kind=GraphNodeKind.START, label="Task accepted")]
    edges: list[GraphEdge] = []
    requirement_node_ids: dict[str, str] = {}
    for index, requirement in enumerate(rows):
        digest = hashlib.sha256(requirement.requirement_id.encode("utf-8")).hexdigest()[:12]
        specialist = str(getattr(requirement.kind, "value", requirement.kind)) == "specialist"
        node_id = (
            f"specialist-{index + 1}-{digest}"
            if specialist
            else f"requirement-{index + 1}-{digest}"
        )
        requirement_node_ids[requirement.requirement_id] = node_id
        nodes.append(GraphNode(
            node_id=node_id,
            kind=GraphNodeKind.SPECIALIST if specialist else GraphNodeKind.REQUIREMENT,
            label=requirement.objective,
            requirement_id=requirement.requirement_id,
            retry_policy=GraphRetryPolicy(
                max_attempts=int(getattr(budget, "max_attempts_per_requirement", 1) or 1)
            ),
        ))

    for requirement in rows:
        target = requirement_node_ids[requirement.requirement_id]
        if requirement.dependencies:
            for dependency_id in requirement.dependencies:
                edges.append(GraphEdge(
                    source_node_id=requirement_node_ids[dependency_id],
                    target_node_id=target,
                    kind=GraphEdgeKind.DEPENDENCY,
                ))
        else:
            edges.append(GraphEdge(source_node_id=start_id, target_node_id=target))

    terminal_requirement_nodes = [
        requirement_node_ids[item.requirement_id]
        for item in rows
        if not any(item.requirement_id in other.dependencies for other in rows)
    ]
    if len(terminal_requirement_nodes) > 1:
        join_id = "requirements-join"
        nodes.append(GraphNode(node_id=join_id, kind=GraphNodeKind.JOIN, label="All requirements evaluated"))
        for node_id in terminal_requirement_nodes:
            edges.append(GraphEdge(source_node_id=node_id, target_node_id=join_id))
        previous = join_id
    elif terminal_requirement_nodes:
        previous = terminal_requirement_nodes[0]
    else:
        previous = start_id
    nodes.append(GraphNode(node_id=final_id, kind=GraphNodeKind.FINALIZATION, label="Canonical finalization gate"))
    edges.append(GraphEdge(source_node_id=previous, target_node_id=final_id))

    research_budget = budget or ResearchBudgetPolicy()
    graph_budget = GraphBudget(
        max_transitions=max(
            64,
            min(
                2048,
                len(nodes) + len(rows) * (2 * research_budget.max_attempts_per_requirement + 6),
            ),
        ),
        max_wall_time_seconds=research_budget.max_time_seconds,
        max_tool_runs=research_budget.max_external_calls,
        max_concurrency=research_budget.max_concurrency,
    )
    return TaskGraph(
        graph_id=graph_id,
        source=GraphSource.RUNTIME,
        entry_node_id=start_id,
        finalization_node_id=final_id,
        nodes=nodes,
        edges=edges,
        budget=graph_budget,
    )


def reconcile_graph_state(
    graph: TaskGraph,
    previous: Optional[TaskGraphState],
    *,
    requirement_states: Mapping[str, RequirementState],
    completion: Optional[CompletionVerdict],
    task_status: str = "running",
) -> TaskGraphState:
    """Project authoritative runtime ledgers without becoming completion authority."""

    now = time.time()
    previous_states = previous.node_states if previous and previous.graph_id == graph.graph_id else {}
    node_states: dict[str, GraphNodeState] = {}
    active: list[str] = []
    parents: dict[str, set[str]] = {item.node_id: set() for item in graph.nodes}
    for edge in graph.edges:
        if edge.kind != GraphEdgeKind.RETRY:
            parents[edge.target_node_id].add(edge.source_node_id)
    authoritative_terminal_node_ids = {graph.entry_node_id}
    for graph_node in graph.nodes:
        if graph_node.kind not in {GraphNodeKind.REQUIREMENT, GraphNodeKind.SPECIALIST}:
            continue
        requirement_state = requirement_states.get(graph_node.requirement_id)
        if requirement_state and requirement_state.status in {
            RequirementStatus.SATISFIED,
            RequirementStatus.UNAVAILABLE,
            RequirementStatus.BLOCKED,
            RequirementStatus.EXHAUSTED,
        }:
            authoritative_terminal_node_ids.add(graph_node.node_id)

    for node in graph.nodes:
        old = previous_states.get(node.node_id)
        desired = GraphNodeStatus.PENDING
        outcome_code = ""
        tool_run_ids: list[str] = list(old.tool_run_ids if old else [])
        attempt_count = int(old.attempt_count if old else 0)
        started_at = float(old.started_at if old else 0.0)
        completed_at = float(old.completed_at if old else 0.0)

        if node.kind == GraphNodeKind.START:
            desired = GraphNodeStatus.COMPLETED
            outcome_code = "taskrun_created"
            started_at = started_at or now
            completed_at = completed_at or now
        elif node.kind in {GraphNodeKind.REQUIREMENT, GraphNodeKind.SPECIALIST}:
            requirement_state = requirement_states.get(node.requirement_id)
            if requirement_state is None:
                desired = GraphNodeStatus.BLOCKED
                outcome_code = "requirement_state_missing"
            else:
                tool_run_ids = list(requirement_state.tool_run_ids)
                attempt_count = len(requirement_state.attempt_ids)
                outcome_code = requirement_state.status.value
                desired = {
                    RequirementStatus.PENDING: GraphNodeStatus.READY,
                    RequirementStatus.ACTIVE: GraphNodeStatus.RUNNING,
                    RequirementStatus.WEAK: GraphNodeStatus.READY,
                    RequirementStatus.SATISFIED: GraphNodeStatus.COMPLETED,
                    RequirementStatus.UNAVAILABLE: GraphNodeStatus.COMPLETED,
                    RequirementStatus.BLOCKED: GraphNodeStatus.BLOCKED,
                    RequirementStatus.EXHAUSTED: GraphNodeStatus.COMPLETED,
                }[requirement_state.status]
                if desired in {GraphNodeStatus.READY, GraphNodeStatus.RUNNING}:
                    started_at = started_at or (requirement_state.updated_at if desired == GraphNodeStatus.RUNNING else 0.0)
                elif desired in TERMINAL_GRAPH_NODE_STATUSES:
                    started_at = started_at or requirement_state.updated_at
                    completed_at = completed_at or requirement_state.updated_at
        elif node.kind == GraphNodeKind.FINALIZATION:
            normalized_task_status = str(task_status or "running").strip().casefold()
            if normalized_task_status == "completed":
                desired = GraphNodeStatus.COMPLETED
                outcome_code = str(getattr(completion, "disposition", "complete") or "complete")
                started_at = started_at or now
                completed_at = completed_at or now
            elif normalized_task_status == "cancelled":
                desired = GraphNodeStatus.CANCELLED
                outcome_code = "task_cancelled"
                completed_at = completed_at or now
            elif normalized_task_status == "superseded":
                desired = GraphNodeStatus.CANCELLED
                outcome_code = "task_superseded"
                completed_at = completed_at or now
            elif normalized_task_status == "blocked_policy":
                desired = GraphNodeStatus.BLOCKED
                outcome_code = "blocked_policy"
                completed_at = completed_at or now
            elif normalized_task_status.startswith("failed"):
                desired = GraphNodeStatus.FAILED
                outcome_code = normalized_task_status
                completed_at = completed_at or now
            elif normalized_task_status.startswith("suspended_"):
                desired = GraphNodeStatus.WAITING
                outcome_code = normalized_task_status
            elif completion and completion.finalizable:
                desired = GraphNodeStatus.READY
                outcome_code = completion.disposition.value
            else:
                desired = GraphNodeStatus.PENDING
                outcome_code = str(getattr(completion, "reason_code", "") or "requirements_pending")
        elif node.kind == GraphNodeKind.APPROVAL:
            normalized_task_status = str(task_status or "running").strip().casefold()
            if normalized_task_status == "suspended_waiting_for_approval":
                desired = GraphNodeStatus.WAITING
                outcome_code = "approval_pending"
                started_at = started_at or now
            elif normalized_task_status == "blocked_policy":
                desired = GraphNodeStatus.BLOCKED
                outcome_code = "approval_or_policy_blocked"
                completed_at = completed_at or now
            elif parents[node.node_id] and all(
                parent_id in authoritative_terminal_node_ids
                or (
                    (node_states.get(parent_id) or previous_states.get(parent_id))
                    and (node_states.get(parent_id) or previous_states.get(parent_id)).status
                    in TERMINAL_GRAPH_NODE_STATUSES
                )
                for parent_id in parents[node.node_id]
            ):
                desired = GraphNodeStatus.SKIPPED
                outcome_code = "no_pending_approval"
                completed_at = completed_at or now
            else:
                desired = GraphNodeStatus.PENDING
                outcome_code = "requirements_pending"
        elif node.kind == GraphNodeKind.JOIN:
            parent_states = [node_states.get(item) for item in parents[node.node_id]]
            if parent_states and all(item and item.status in TERMINAL_GRAPH_NODE_STATUSES for item in parent_states):
                desired = GraphNodeStatus.COMPLETED
                outcome_code = "branches_terminal"
                started_at = started_at or now
                completed_at = completed_at or now
            else:
                desired = GraphNodeStatus.PENDING
        elif old is not None:
            desired = old.status
            outcome_code = old.outcome_code

        parent_ids = sorted(parents[node.node_id])
        dependency_states = [node_states.get(item) for item in parent_ids]
        if desired == GraphNodeStatus.READY and dependency_states and not all(
            parent_id in authoritative_terminal_node_ids
            or (
                (item or previous_states.get(parent_id))
                and (item or previous_states.get(parent_id)).status in TERMINAL_GRAPH_NODE_STATUSES
            )
            for parent_id, item in zip(parent_ids, dependency_states)
        ):
            desired = GraphNodeStatus.PENDING

        changed = old is None or old.status != desired or old.outcome_code != outcome_code or old.tool_run_ids != tool_run_ids
        state = GraphNodeState(
            node_id=node.node_id,
            status=desired,
            attempt_count=attempt_count,
            transition_count=(old.transition_count if old else 0) + (1 if changed and old else 0),
            tool_run_ids=tool_run_ids,
            child_task_run_ids=list(old.child_task_run_ids if old else []),
            outcome_code=outcome_code,
            diagnostic_code=str(old.diagnostic_code if old else ""),
            started_at=started_at,
            completed_at=completed_at,
            updated_at=now if changed else float(old.updated_at if old else now),
        )
        node_states[node.node_id] = state
        if state.status in {GraphNodeStatus.READY, GraphNodeStatus.RUNNING, GraphNodeStatus.WAITING}:
            active.append(node.node_id)

    # Never advertise finalize as the sole active node while requirement work remains open.
    open_requirement_nodes = [
        node.node_id
        for node in graph.nodes
        if node.kind in {GraphNodeKind.REQUIREMENT, GraphNodeKind.SPECIALIST}
        and node_states.get(node.node_id)
        and node_states[node.node_id].status in {
            GraphNodeStatus.READY, GraphNodeStatus.RUNNING, GraphNodeStatus.WAITING, GraphNodeStatus.PENDING,
        }
    ]
    if open_requirement_nodes:
        finalize_id = graph.finalization_node_id
        if finalize_id in active and node_states.get(finalize_id):
            # Hold finalization pending until requirements terminalize.
            fin = node_states[finalize_id]
            if fin.status == GraphNodeStatus.READY:
                node_states[finalize_id] = fin.model_copy(update={
                    "status": GraphNodeStatus.PENDING,
                    "outcome_code": "requirements_pending",
                    "updated_at": now,
                })
            active = [node_id for node_id in active if node_id != finalize_id]
            # Prefer open requirements as the active set.
            for node_id in open_requirement_nodes:
                if node_id not in active and node_states[node_id].status in {
                    GraphNodeStatus.READY, GraphNodeStatus.RUNNING, GraphNodeStatus.WAITING,
                }:
                    active.append(node_id)

    previous_transition_count = previous.transition_count if previous else 0
    changed_count = sum(
        1
        for node_id, state in node_states.items()
        if node_id in previous_states and previous_states[node_id].status != state.status
    )
    truly_finalizable = bool(
        completion
        and completion.finalizable
        and not open_requirement_nodes
    )
    return TaskGraphState(
        graph_id=graph.graph_id,
        node_states=node_states,
        active_node_ids=active,
        edge_traversals=dict(previous.edge_traversals if previous else {}),
        transition_count=previous_transition_count + changed_count,
        shadow_completion_ready=truly_finalizable,
        shadow_completion_reason_code=str(
            getattr(completion, "reason_code", "") or "requirements_pending"
            if not truly_finalizable
            else getattr(completion, "reason_code", "") or "complete"
        ),
        checkpoints=list(previous.checkpoints if previous else []),
        updated_at=(
            now
            if changed_count or not previous or previous.shadow_completion_ready != truly_finalizable
            else previous.updated_at
        ),
    )


def apply_graph_transition(
    graph: TaskGraph,
    state: TaskGraphState,
    transition: GraphTransition,
) -> TaskGraphState:
    """Apply one bounded legal transition to a non-authoritative graph node."""

    nodes = graph.node_map()
    node = nodes.get(transition.node_id)
    if node is None:
        raise ValueError(f"Unknown TaskGraph node: {transition.node_id}")
    if node.kind in {GraphNodeKind.REQUIREMENT, GraphNodeKind.SPECIALIST}:
        raise ValueError("Requirement nodes are owned by the TaskRun requirement ledger")
    if node.kind == GraphNodeKind.FINALIZATION:
        raise ValueError("Finalization node is owned by the canonical finalization gate")
    current = state.node_states.get(node.node_id)
    if current is None:
        raise ValueError(f"TaskGraph state is missing node: {node.node_id}")
    if transition.expected_status is not None and current.status != transition.expected_status:
        raise ValueError(
            f"TaskGraph node {node.node_id} changed from expected {transition.expected_status.value} "
            f"to {current.status.value}"
        )
    if state.transition_count >= graph.budget.max_transitions:
        raise ValueError("TaskGraph transition budget exceeded")

    if transition.kind == GraphTransitionKind.LINK_TOOL_RUN:
        if node.kind != GraphNodeKind.TOOL:
            raise ValueError("ToolRun bindings are valid only on tool nodes")
        updated_node = current.model_copy(update={
            "tool_run_ids": list(dict.fromkeys([*current.tool_run_ids, transition.tool_run_id])),
            "transition_count": current.transition_count + 1,
            "updated_at": time.time(),
        })
    elif transition.kind == GraphTransitionKind.LINK_CHILD_TASK:
        if node.kind != GraphNodeKind.SUBAGENT:
            raise ValueError("Child TaskRun bindings are valid only on bounded subagent nodes")
        updated_node = current.model_copy(update={
            "child_task_run_ids": list(dict.fromkeys([
                *current.child_task_run_ids, transition.child_task_run_id
            ])),
            "transition_count": current.transition_count + 1,
            "updated_at": time.time(),
        })
    else:
        target = {
            GraphTransitionKind.START: GraphNodeStatus.RUNNING,
            GraphTransitionKind.WAIT: GraphNodeStatus.WAITING,
            GraphTransitionKind.COMPLETE: GraphNodeStatus.COMPLETED,
            GraphTransitionKind.SKIP: GraphNodeStatus.SKIPPED,
            GraphTransitionKind.BLOCK: GraphNodeStatus.BLOCKED,
            GraphTransitionKind.FAIL: GraphNodeStatus.FAILED,
            GraphTransitionKind.CANCEL: GraphNodeStatus.CANCELLED,
            GraphTransitionKind.RETRY: GraphNodeStatus.READY,
        }[transition.kind]
        allowed: dict[GraphNodeStatus, set[GraphNodeStatus]] = {
            GraphNodeStatus.PENDING: {
                GraphNodeStatus.READY, GraphNodeStatus.RUNNING, GraphNodeStatus.WAITING,
                GraphNodeStatus.SKIPPED, GraphNodeStatus.BLOCKED,
                GraphNodeStatus.FAILED, GraphNodeStatus.CANCELLED,
            },
            GraphNodeStatus.READY: {
                GraphNodeStatus.RUNNING, GraphNodeStatus.WAITING, GraphNodeStatus.SKIPPED,
                GraphNodeStatus.BLOCKED, GraphNodeStatus.FAILED, GraphNodeStatus.CANCELLED,
            },
            GraphNodeStatus.RUNNING: {
                GraphNodeStatus.WAITING, GraphNodeStatus.COMPLETED, GraphNodeStatus.BLOCKED,
                GraphNodeStatus.FAILED, GraphNodeStatus.CANCELLED,
            },
            GraphNodeStatus.WAITING: {
                GraphNodeStatus.RUNNING, GraphNodeStatus.COMPLETED, GraphNodeStatus.BLOCKED,
                GraphNodeStatus.FAILED, GraphNodeStatus.CANCELLED,
            },
            GraphNodeStatus.BLOCKED: {GraphNodeStatus.READY},
            GraphNodeStatus.FAILED: {GraphNodeStatus.READY},
            GraphNodeStatus.COMPLETED: set(),
            GraphNodeStatus.SKIPPED: set(),
            GraphNodeStatus.CANCELLED: set(),
        }
        if target not in allowed[current.status]:
            raise ValueError(
                f"Illegal TaskGraph transition for {node.node_id}: {current.status.value}->{target.value}"
            )
        if transition.kind == GraphTransitionKind.RETRY:
            retry_edges = [
                edge for edge in graph.edges
                if edge.kind == GraphEdgeKind.RETRY
                and edge.source_node_id == node.node_id
                and edge.target_node_id == node.node_id
            ]
            if not retry_edges:
                raise ValueError(f"TaskGraph node {node.node_id} has no explicit retry edge")
            max_traversals = min(edge.max_traversals for edge in retry_edges)
            edge_key = f"{node.node_id}->{node.node_id}:{GraphEdgeKind.RETRY.value}"
            traversals = int(state.edge_traversals.get(edge_key, 0))
            if traversals >= max_traversals:
                raise ValueError(f"TaskGraph retry budget exhausted for node {node.node_id}")
        else:
            edge_key = ""
            traversals = 0
        now = time.time()
        updated_node = current.model_copy(update={
            "status": target,
            "attempt_count": current.attempt_count + (1 if target == GraphNodeStatus.RUNNING else 0),
            "transition_count": current.transition_count + 1,
            "outcome_code": transition.reason_code,
            "started_at": current.started_at or (now if target == GraphNodeStatus.RUNNING else 0.0),
            "completed_at": now if target in TERMINAL_GRAPH_NODE_STATUSES else 0.0,
            "updated_at": now,
        })

    node_states = dict(state.node_states)
    node_states[node.node_id] = updated_node
    edge_traversals = dict(state.edge_traversals)
    if transition.kind == GraphTransitionKind.RETRY:
        edge_traversals[edge_key] = traversals + 1
    if updated_node.status in TERMINAL_GRAPH_NODE_STATUSES:
        downstream = sorted({
            edge.target_node_id
            for edge in graph.edges
            if edge.source_node_id == node.node_id and edge.kind != GraphEdgeKind.RETRY
        })
        for target_id in downstream:
            target_node = nodes[target_id]
            target_state = node_states[target_id]
            if target_node.kind in {GraphNodeKind.REQUIREMENT, GraphNodeKind.FINALIZATION}:
                continue
            parent_ids = {
                edge.source_node_id
                for edge in graph.edges
                if edge.target_node_id == target_id and edge.kind != GraphEdgeKind.RETRY
            }
            if target_state.status == GraphNodeStatus.PENDING and all(
                node_states[parent_id].status in TERMINAL_GRAPH_NODE_STATUSES
                for parent_id in parent_ids
            ):
                node_states[target_id] = target_state.model_copy(update={
                    "status": GraphNodeStatus.READY,
                    "transition_count": target_state.transition_count + 1,
                    "outcome_code": "dependencies_terminal",
                    "updated_at": time.time(),
                })
    active = [
        node_id for node_id, item in node_states.items()
        if item.status in {GraphNodeStatus.READY, GraphNodeStatus.RUNNING, GraphNodeStatus.WAITING}
    ]
    return state.model_copy(update={
        "node_states": node_states,
        "active_node_ids": active,
        "edge_traversals": edge_traversals,
        "transition_count": state.transition_count + 1,
        "updated_at": time.time(),
    })


def checkpoint_graph_state(
    state: TaskGraphState,
    *,
    task_revision: int,
    reason_code: str,
) -> TaskGraphState:
    """Append a bounded, content-addressed graph checkpoint."""

    payload = {
        "node_states": {
            key: value.model_dump(mode="json", exclude={"updated_at"})
            for key, value in sorted(state.node_states.items())
        },
        "active_node_ids": list(state.active_node_ids),
        "edge_traversals": dict(sorted(state.edge_traversals.items())),
        "shadow_completion_ready": state.shadow_completion_ready,
        "shadow_completion_reason_code": state.shadow_completion_reason_code,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if state.checkpoints and state.checkpoints[-1].state_sha256 == digest:
        return state
    checkpoint = GraphCheckpoint(
        sequence=(state.checkpoints[-1].sequence + 1 if state.checkpoints else 1),
        task_revision=task_revision,
        reason_code=str(reason_code or "taskrun_updated").strip()[:160],
        state_sha256=digest,
        active_node_ids=list(state.active_node_ids),
    )
    return state.model_copy(update={
        "checkpoints": [*state.checkpoints, checkpoint][-MAX_GRAPH_CHECKPOINTS:],
        "updated_at": time.time(),
    })


def _canonical_id(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 160 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", normalized):
        raise ValueError(f"{field} must be a bounded canonical identifier")
    return normalized


def _bounded_unique(values: Iterable[Any], *, limit: int, size: int) -> list[str]:
    return list(dict.fromkeys(
        str(item or "").strip()[:size]
        for item in values
        if str(item or "").strip()
    ))[:limit]


def _reachable(start: str, outgoing: Mapping[str, set[str]]) -> set[str]:
    visited: set[str] = set()
    queue: deque[str] = deque([start])
    while queue:
        node_id = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        queue.extend(sorted(outgoing.get(node_id, set()) - visited))
    return visited


def _reverse(outgoing: Mapping[str, set[str]]) -> dict[str, set[str]]:
    reversed_edges = {item: set() for item in outgoing}
    for source, targets in outgoing.items():
        for target in targets:
            reversed_edges.setdefault(target, set()).add(source)
    return reversed_edges


def _contains_cycle(node_ids: Iterable[str], outgoing: Mapping[str, set[str]]) -> bool:
    indegree = {item: 0 for item in node_ids}
    for targets in outgoing.values():
        for target in targets:
            indegree[target] += 1
    queue: deque[str] = deque(sorted(item for item, degree in indegree.items() if degree == 0))
    visited = 0
    while queue:
        node_id = queue.popleft()
        visited += 1
        for target in sorted(outgoing.get(node_id, set())):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return visited != len(indegree)
