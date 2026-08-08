from __future__ import annotations

import json

import pytest

from agent.execution_graph import (
    ExecutionProfile,
    GraphEdge,
    GraphEdgeKind,
    GraphNode,
    GraphNodeKind,
    GraphNodeStatus,
    GraphTransition,
    GraphTransitionKind,
    apply_graph_transition,
    TaskGraph,
)
from agent.research_runtime import (
    CompletionDisposition,
    CompletionVerdict,
    RequirementState,
    RequirementStatus,
    TurnRequirement,
)
from agent.task_runs import TaskRun, TaskRunStatus, TaskRunStore


def test_v2_taskrun_migrates_to_current_compatibility_graph() -> None:
    task = TaskRun.model_validate({
        "schema_version": 2,
        "id": "task-1",
        "session_id": "session-1",
        "objective": "Find the current schedule and remember my preference",
        "permitted_capabilities": ["research", "memory"],
        "requirements": [
            {"requirement_id": "req-search", "objective": "Find the schedule"},
            {"requirement_id": "req-memory", "kind": "memory", "objective": "Recall preference"},
        ],
    })

    assert task.schema_version == 5
    assert task.execution_profile == ExecutionProfile.WORK
    assert task.execution_graph is not None
    assert task.execution_graph_state is not None
    assert task.execution_graph.source.value == "runtime"
    requirement_nodes = [
        item for item in task.execution_graph.nodes if item.kind == GraphNodeKind.REQUIREMENT
    ]
    assert {item.requirement_id for item in requirement_nodes} == {"req-search", "req-memory"}
    assert task.execution_graph_state.shadow_completion_ready is False


def test_satisfied_requirement_node_is_preserved_when_other_requirement_changes() -> None:
    task = TaskRun(
        id="task-1",
        session_id="session-1",
        objective="Complete two independent parts",
        requirements=[
            TurnRequirement(requirement_id="req-a", objective="Part A"),
            TurnRequirement(requirement_id="req-b", objective="Part B"),
        ],
        requirement_states={
            "req-a": RequirementState(requirement_id="req-a", status=RequirementStatus.SATISFIED),
            "req-b": RequirementState(requirement_id="req-b", status=RequirementStatus.PENDING),
        },
    )
    graph = task.execution_graph
    state = task.execution_graph_state
    assert graph is not None and state is not None
    req_a_node = next(item.node_id for item in graph.nodes if item.requirement_id == "req-a")
    req_b_node = next(item.node_id for item in graph.nodes if item.requirement_id == "req-b")
    assert state.node_states[req_a_node].status == GraphNodeStatus.COMPLETED
    assert state.node_states[req_b_node].status == GraphNodeStatus.READY

    updated = TaskRun.model_validate(task.model_copy(update={
        "requirement_states": {
            **task.requirement_states,
            "req-b": task.requirement_states["req-b"].model_copy(
                update={"status": RequirementStatus.ACTIVE}
            ),
        }
    }).model_dump())
    assert updated.execution_graph_state is not None
    assert updated.execution_graph_state.node_states[req_a_node].status == GraphNodeStatus.COMPLETED
    assert updated.execution_graph_state.node_states[req_b_node].status == GraphNodeStatus.RUNNING


def test_graph_rejects_unreachable_node_and_unbounded_cycle() -> None:
    nodes = [
        GraphNode(node_id="start", kind=GraphNodeKind.START),
        GraphNode(node_id="work", kind=GraphNodeKind.MODEL_STEP),
        GraphNode(node_id="orphan", kind=GraphNodeKind.MODEL_STEP),
        GraphNode(node_id="finalize", kind=GraphNodeKind.FINALIZATION),
    ]
    with pytest.raises(ValueError, match="unreachable"):
        TaskGraph(
            graph_id="graph-test",
            entry_node_id="start",
            finalization_node_id="finalize",
            nodes=nodes,
            edges=[
                GraphEdge(source_node_id="start", target_node_id="work"),
                GraphEdge(source_node_id="work", target_node_id="finalize"),
            ],
        )

    with pytest.raises(ValueError, match="unbounded cycle"):
        TaskGraph(
            graph_id="graph-test",
            entry_node_id="start",
            finalization_node_id="finalize",
            nodes=[
                GraphNode(node_id="start", kind=GraphNodeKind.START),
                GraphNode(node_id="work", kind=GraphNodeKind.MODEL_STEP),
                GraphNode(node_id="review", kind=GraphNodeKind.MODEL_STEP),
                GraphNode(node_id="finalize", kind=GraphNodeKind.FINALIZATION),
            ],
            edges=[
                GraphEdge(source_node_id="start", target_node_id="work"),
                GraphEdge(source_node_id="work", target_node_id="review"),
                GraphEdge(source_node_id="review", target_node_id="work"),
                GraphEdge(source_node_id="review", target_node_id="finalize"),
            ],
        )


def test_only_explicit_bounded_retry_edges_may_form_cycles() -> None:
    graph = TaskGraph(
        graph_id="graph-test",
        entry_node_id="start",
        finalization_node_id="finalize",
        nodes=[
            GraphNode(node_id="start", kind=GraphNodeKind.START),
            GraphNode(node_id="work", kind=GraphNodeKind.MODEL_STEP),
            GraphNode(node_id="finalize", kind=GraphNodeKind.FINALIZATION),
        ],
        edges=[
            GraphEdge(source_node_id="start", target_node_id="work"),
            GraphEdge(
                source_node_id="work",
                target_node_id="work",
                kind=GraphEdgeKind.RETRY,
                max_traversals=3,
            ),
            GraphEdge(source_node_id="work", target_node_id="finalize"),
        ],
    )
    assert graph.edges[1].max_traversals == 3


def test_legal_transition_releases_only_ready_downstream_nodes() -> None:
    graph = TaskGraph(
        graph_id="graph-planned",
        source="planned",
        entry_node_id="start",
        finalization_node_id="finalize",
        nodes=[
            GraphNode(node_id="start", kind=GraphNodeKind.START),
            GraphNode(node_id="work", kind=GraphNodeKind.MODEL_STEP),
            GraphNode(node_id="review", kind=GraphNodeKind.MODEL_STEP),
            GraphNode(node_id="requirement", kind=GraphNodeKind.REQUIREMENT, requirement_id="req-work"),
            GraphNode(node_id="finalize", kind=GraphNodeKind.FINALIZATION),
        ],
        edges=[
            GraphEdge(source_node_id="start", target_node_id="work"),
            GraphEdge(source_node_id="work", target_node_id="review"),
            GraphEdge(source_node_id="review", target_node_id="requirement"),
            GraphEdge(source_node_id="requirement", target_node_id="finalize"),
        ],
    )
    task = TaskRun(
        id="task-graph",
        session_id="session-1",
        objective="Planned work",
        requirements=[TurnRequirement(requirement_id="req-work", objective="Planned work")],
        execution_graph=graph,
    )
    state = task.execution_graph_state
    assert state is not None
    running = apply_graph_transition(graph, state, GraphTransition(
        transition_id="transition-start",
        node_id="work",
        kind=GraphTransitionKind.START,
        reason_code="runtime_started_node",
        expected_status=GraphNodeStatus.PENDING,
    ))
    completed = apply_graph_transition(graph, running, GraphTransition(
        transition_id="transition-complete",
        node_id="work",
        kind=GraphTransitionKind.COMPLETE,
        reason_code="verified_node_result",
        expected_status=GraphNodeStatus.RUNNING,
    ))
    assert completed.node_states["review"].status == GraphNodeStatus.READY
    assert completed.node_states["finalize"].status == GraphNodeStatus.PENDING


def test_finalization_and_requirement_nodes_cannot_be_mutated_by_graph_transition(tmp_path) -> None:
    store = TaskRunStore(tmp_path / "task-runs.json")
    task = store.create(
        id="task-1",
        session_id="session-1",
        objective="Find data",
        requirements=[TurnRequirement(requirement_id="req-a", objective="Find data")],
    )
    graph = task.execution_graph
    assert graph is not None
    requirement_node = next(item.node_id for item in graph.nodes if item.requirement_id == "req-a")
    for node_id, message in (
        (requirement_node, "requirement ledger"),
        (graph.finalization_node_id, "finalization gate"),
    ):
        with pytest.raises(ValueError, match=message):
            store.transition_graph(
                task.id,
                session_id=task.session_id,
                project_id=task.project_id,
                expected_revision=task.revision,
                transition=GraphTransition(
                    transition_id=f"transition-{node_id}",
                    node_id=node_id,
                    kind=GraphTransitionKind.COMPLETE,
                    reason_code="model_requested_completion",
                ),
            )


def test_store_checkpoints_graph_projection_without_a_second_completion_owner(tmp_path) -> None:
    path = tmp_path / "task-runs.json"
    store = TaskRunStore(path)
    task = store.create(
        id="task-1",
        session_id="session-1",
        objective="Find current data",
        requirements=[TurnRequirement(requirement_id="req-a", objective="Find current data")],
    )
    updated = store.update(
        task.id,
        session_id=task.session_id,
        project_id=task.project_id,
        expected_revision=task.revision,
        requirement_states={
            "req-a": task.requirement_states["req-a"].model_copy(
                update={"status": RequirementStatus.ACTIVE}
            )
        },
        workflow_stage="research:primary_capability",
    )
    assert updated.execution_graph_state is not None
    assert updated.execution_graph_state.checkpoints[-1].reason_code == "research:primary_capability"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 5
    assert payload["task_runs"][0]["execution_graph"]["source"] == "runtime"


def test_terminal_task_status_is_projected_without_becoming_a_completion_rule() -> None:
    task = TaskRun(
        id="task-1",
        session_id="session-1",
        objective="Answer",
        permitted_capabilities=["conversation"],
    )
    completed = TaskRun.model_validate(task.model_copy(update={
        "status": TaskRunStatus.COMPLETED,
        "completion_evaluation": CompletionVerdict(
            disposition=CompletionDisposition.COMPLETE,
            finalizable=True,
            reason_code="all_required_requirements_satisfied",
        ),
    }).model_dump())
    assert completed.execution_graph is not None
    assert completed.execution_graph_state is not None
    finalization = completed.execution_graph.finalization_node_id
    assert completed.execution_graph_state.node_states[finalization].status == GraphNodeStatus.COMPLETED
    assert completed.execution_graph_state.shadow_completion_ready is True


def test_future_taskrun_and_graph_versions_fail_closed() -> None:
    with pytest.raises(ValueError, match="future TaskRun"):
        TaskRun.model_validate({
            "schema_version": 6,
            "session_id": "session-1",
            "objective": "No downgrade",
        })


def test_explicit_surface_handoff_replaces_task_in_same_session_and_clears_authority_snapshots(tmp_path) -> None:
    store = TaskRunStore(tmp_path / "task-runs.json")
    current = store.create(
        id="task-1",
        project_id="project-1",
        session_id="session-1",
        objective="Inspect and then implement",
        permitted_capabilities=["research"],
        capability_snapshot={
            "session_id": "session-1",
            "project_id": "project-1",
            "inventory_revision": 3,
        },
        retry_identity={"stable_action_id": "old-action"},
    )
    previous, replacement = store.handoff_to_profile(
        current.id,
        session_id=current.session_id,
        project_id=current.project_id,
        expected_revision=current.revision,
        execution_id="execution-handoff",
        target_profile=ExecutionProfile.CODE,
    )
    assert previous.status == TaskRunStatus.SUPERSEDED
    assert replacement.session_id == current.session_id
    assert replacement.project_id == current.project_id
    assert replacement.execution_profile == ExecutionProfile.CODE
    assert replacement.parent_task_run_id == current.id
    assert replacement.handoff_context_id
    assert replacement.capability_snapshot is None
    assert replacement.retry_identity == {}
    assert replacement.verified_tool_outcomes == []
    assert replacement.tool_run_ids == []
    assert replacement.legacy_provenance["inherited_tool_run_ids"] == current.tool_run_ids
    assert replacement.model_binding_events[-1]["requires_fresh_authority_validation"] is True
    with pytest.raises(ValueError, match="future TaskGraph"):
        TaskGraph.model_validate({
            "schema_version": 2,
            "graph_id": "graph-test",
            "entry_node_id": "start",
            "finalization_node_id": "finalize",
            "nodes": [],
            "edges": [],
        })
