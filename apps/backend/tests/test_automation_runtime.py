from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError


def _binding(session_id: str):
    from agent.automation_runtime import AutomationModelBinding, ModelBindingPolicy

    return AutomationModelBinding(
        policy=ModelBindingPolicy.SESSION_DEFAULT,
        source_session_id=session_id,
    )


def _create_run(store, *, key: str = "routine:r1:2026-07-14T09:00", project: str = "project-a", session: str = "session-a", max_attempts: int = 3):
    return store.create_run(
        idempotency_key=key,
        project_id=project,
        session_id=session,
        task_id="task-1",
        routine_id="routine-1",
        trigger_id="trigger-1",
        source="routine",
        source_id="routine-1",
        objective="Prepare the morning briefing",
        model_binding=_binding(session),
        max_attempts=max_attempts,
    )


def test_automation_run_identity_is_idempotent_and_project_session_isolated(tmp_path: Path) -> None:
    from agent.automation_runtime import (
        AutomationConflictError,
        AutomationRunStore,
        AutomationScopeError,
    )

    store = AutomationRunStore(tmp_path / "automations" / "runs.json")
    first = _create_run(store)
    replay = _create_run(store)
    assert replay.id == first.id

    with pytest.raises(AutomationConflictError, match="another automation identity or scope"):
        _create_run(store, project="project-b")
    with pytest.raises(AutomationConflictError, match="another automation identity or scope"):
        store.create_run(
            idempotency_key="routine:r1:2026-07-14T09:00",
            project_id="project-a",
            session_id="session-a",
            task_id="task-1",
            routine_id="routine-1",
            trigger_id="trigger-1",
            source="routine",
            source_id="routine-1",
            objective="A changed action must not reuse the old Run",
            model_binding=_binding("session-a"),
        )

    assert [run.id for run in store.list_runs(project_id="project-a", session_id="session-a")] == [first.id]
    assert store.list_runs(project_id="project-b") == []
    assert store.get_run(first.id, project_id="project-b", session_id="session-a") is None
    with pytest.raises(AutomationScopeError):
        store.claim(
            first.id,
            project_id="project-b",
            session_id="session-a",
            claimant_id="heartbeat-1",
        )


def test_concurrent_trigger_claims_produce_one_lease_and_one_attempt(tmp_path: Path) -> None:
    from agent.automation_runtime import AutomationRunStatus, AutomationRunStore

    store = AutomationRunStore(tmp_path / "runs.json")
    run = _create_run(store)

    def claim(index: int):
        return store.claim(
            run.id,
            project_id=run.project_id,
            session_id=run.session_id,
            claimant_id=f"heartbeat-{index}",
            lease_seconds=30,
            now=100.0,
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        claims = list(pool.map(claim, range(32)))

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    assert winners[0].status == AutomationRunStatus.PREPARING
    assert winners[0].attempt == 1
    assert winners[0].lease is not None
    persisted = store.get_run(run.id, project_id=run.project_id, session_id=run.session_id)
    assert persisted is not None and persisted.lease == winners[0].lease


def test_lease_renewal_model_binding_checkpoints_and_verified_terminal_transition(tmp_path: Path) -> None:
    from agent.automation_runtime import (
        AutomationLeaseError,
        AutomationModelBinding,
        AutomationRunStatus,
        AutomationRunStore,
        AutomationTransitionError,
        ModelBindingPolicy,
    )

    path = tmp_path / "runs.json"
    store = AutomationRunStore(path)
    run = _create_run(store)
    claimed = store.claim(
        run.id,
        project_id=run.project_id,
        session_id=run.session_id,
        claimant_id="heartbeat-a",
        lease_seconds=10,
        now=100.0,
    )
    assert claimed is not None and claimed.lease is not None
    token = claimed.lease.token

    renewed = store.renew_lease(
        run.id,
        project_id=run.project_id,
        session_id=run.session_id,
        claimant_id="heartbeat-a",
        lease_token=token,
        lease_seconds=20,
        now=105.0,
    )
    assert renewed.lease is not None and renewed.lease.expires_at == 125.0
    with pytest.raises(AutomationLeaseError, match="identity"):
        store.renew_lease(
            run.id,
            project_id=run.project_id,
            session_id=run.session_id,
            claimant_id="heartbeat-a",
            lease_token="wrong-token",
            now=106.0,
        )

    bound = store.bind_model(
        run.id,
        AutomationModelBinding(
            policy=ModelBindingPolicy.SESSION_DEFAULT,
            source_session_id=run.session_id,
            resolved_provider="lmstudio",
            resolved_model_id="fixture-model",
            model_snapshot={"context_limit": 32768},
        ),
        project_id=run.project_id,
        session_id=run.session_id,
        claimant_id="heartbeat-a",
        lease_token=token,
        now=107.0,
    )
    assert bound.model_binding.resolved_model_id == "fixture-model"

    checkpointed = store.append_checkpoint(
        run.id,
        project_id=run.project_id,
        session_id=run.session_id,
        kind="preflight_complete",
        payload={"target": "briefing"},
        execution_id="execution-1",
        tool_run_ids=["tool-run-1"],
        claimant_id="heartbeat-a",
        lease_token=token,
        now=108.0,
    )
    assert checkpointed.checkpoints[0].sequence == 1
    assert checkpointed.execution_id == "execution-1"

    running = store.transition(
        run.id,
        AutomationRunStatus.RUNNING,
        project_id=run.project_id,
        session_id=run.session_id,
        claimant_id="heartbeat-a",
        lease_token=token,
        now=109.0,
    )
    assert running.lease is not None
    completed = store.transition(
        run.id,
        AutomationRunStatus.COMPLETED,
        project_id=run.project_id,
        session_id=run.session_id,
        claimant_id="heartbeat-a",
        lease_token=token,
        execution_id="execution-1",
        tool_run_ids=["tool-run-2"],
        artifact_ids=["artifact-1"],
        outcome={"verified": True},
        now=110.0,
    )
    assert completed.lease is None
    assert completed.completed_at == 110.0
    assert completed.tool_run_ids == ["tool-run-1", "tool-run-2"]
    assert completed.outcome == {"verified": True}
    with pytest.raises(AutomationTransitionError, match="Invalid Automation Run transition"):
        store.transition(
            run.id,
            AutomationRunStatus.QUEUED,
            project_id=run.project_id,
            session_id=run.session_id,
            now=111.0,
        )

    reloaded = AutomationRunStore(path).get_run(
        run.id, project_id=run.project_id, session_id=run.session_id
    )
    assert reloaded is not None and reloaded.model_binding.resolved_model_id == "fixture-model"
    assert reloaded.status == AutomationRunStatus.COMPLETED


def test_expired_lease_recovery_requeues_then_fails_at_retry_budget(tmp_path: Path) -> None:
    from agent.automation_runtime import AutomationRunStatus, AutomationRunStore

    path = tmp_path / "runs.json"
    store = AutomationRunStore(path)
    run = _create_run(store, max_attempts=2)
    first = store.claim(
        run.id,
        project_id=run.project_id,
        session_id=run.session_id,
        claimant_id="worker-1",
        lease_seconds=10,
        now=100.0,
    )
    assert first is not None and first.lease is not None
    store.transition(
        run.id,
        AutomationRunStatus.RUNNING,
        project_id=run.project_id,
        session_id=run.session_id,
        claimant_id="worker-1",
        lease_token=first.lease.token,
        now=101.0,
    )

    restarted = AutomationRunStore(path)
    recovered = restarted.recover_expired(now=111.0)
    assert len(recovered) == 1
    assert recovered[0].status == AutomationRunStatus.QUEUED
    assert recovered[0].checkpoints[-1].payload["requeued"] is True

    second = restarted.claim(
        run.id,
        project_id=run.project_id,
        session_id=run.session_id,
        claimant_id="worker-2",
        lease_seconds=10,
        now=112.0,
    )
    assert second is not None and second.lease is not None and second.attempt == 2
    restarted.transition(
        run.id,
        AutomationRunStatus.RUNNING,
        project_id=run.project_id,
        session_id=run.session_id,
        claimant_id="worker-2",
        lease_token=second.lease.token,
        now=113.0,
    )
    exhausted = restarted.recover_expired(now=123.0)
    assert len(exhausted) == 1
    assert exhausted[0].status == AutomationRunStatus.FAILED
    assert exhausted[0].completed_at == 123.0
    assert "retry budget exhausted" in exhausted[0].error


def test_automation_state_corruption_fails_closed_and_preserves_authority(tmp_path: Path) -> None:
    from agent.automation_runtime import AutomationRunStore, AutomationStateError

    path = tmp_path / "automations" / "runs.json"
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(AutomationStateError, match="not overwritten"):
        AutomationRunStore(path)
    assert path.read_text(encoding="utf-8") == "{broken"
    assert list((path.parent / "corrupt-state").rglob("RECOVERY.txt"))


def _connection_record(*, connection_id: str, project_id: str = "project-a", global_access: bool = False):
    from agent.connections import (
        ConnectionAuthentication,
        ConnectionCapability,
        ConnectionCapabilityKind,
        ConnectionHealth,
        ConnectionKind,
        ConnectionRecord,
        ConnectionScope,
    )

    return ConnectionRecord(
        id=connection_id,
        kind=ConnectionKind.MCP_SERVER,
        display_name=f"Fixture {connection_id}",
        provider="fixture",
        source_ref=f"settings:mcp_servers:{connection_id}",
        health=ConnectionHealth.HEALTHY,
        authentication=ConnectionAuthentication.CONFIGURED,
        scope=ConnectionScope(
            allow_global=global_access,
            project_ids=[] if global_access else [project_id],
            session_ids=["session-a"] if not global_access else [],
            network_hosts=["fixture.example"],
            permissions=["calendar.read"],
        ),
        capabilities=[
            ConnectionCapability(
                id="calendar.read",
                kind=ConnectionCapabilityKind.RESOURCE,
                name="Read calendar",
                resource_types=["calendar_event"],
                permissions=["calendar.read"],
            ),
            ConnectionCapability(
                id="calendar.write",
                kind=ConnectionCapabilityKind.TOOL,
                name="Write calendar",
                tool_names=["calendar_event_create"],
                requires_approval=True,
                permissions=["calendar.write"],
            ),
        ],
        provenance={"owner": "MCPManager"},
    )


def test_connection_registry_projects_secret_free_scoped_capability_refs(tmp_path: Path) -> None:
    from agent.connections import (
        ConnectionCapability,
        ConnectionCapabilityKind,
        ConnectionRecord,
        ConnectionReference,
        ConnectionRegistry,
        ConnectionRegistryError,
        ConnectionScopeError,
    )

    path = tmp_path / "connections" / "registry.json"
    registry = ConnectionRegistry(path)
    scoped = registry.register(_connection_record(connection_id="calendar-a"))
    registry.register(_connection_record(connection_id="global-provider", global_access=True))

    visible = registry.list(project_id="project-a", session_id="session-a")
    assert {item.id for item in visible} == {"calendar-a", "global-provider"}
    assert {item.id for item in registry.list(project_id="project-b", session_id="session-a")} == {
        "global-provider"
    }
    assert registry.get("calendar-a", project_id="project-b", session_id="session-a") is None

    resolved = registry.resolve_references(
        [ConnectionReference(connection_id="calendar-a", capability_ids=["calendar.read"])],
        project_id="project-a",
        session_id="session-a",
    )
    assert [capability["id"] for capability in resolved[0].capabilities] == ["calendar.read"]
    with pytest.raises(ConnectionScopeError):
        registry.resolve_references(
            [{"connection_id": "calendar-a", "capability_ids": ["calendar.read"]}],
            project_id="project-b",
            session_id="session-a",
        )
    with pytest.raises(ConnectionRegistryError, match="explicit capability"):
        registry.resolve_references(
            [{"connection_id": "calendar-a", "capability_ids": []}],
            project_id="project-a",
            session_id="session-a",
        )

    updated = registry.update(
        scoped.id,
        expected_revision=scoped.revision,
        errors=["provider rejected token=super-secret Bearer abc.def.ghi"],
        last_checked_at=100.0,
    )
    projection = registry.list(project_id="project-a", session_id="session-a")[0]
    assert "super-secret" not in updated.errors[0]
    assert "abc.def.ghi" not in updated.errors[0]
    assert "super-secret" not in projection.errors[0]
    assert "abc.def.ghi" not in projection.errors[0]
    assert "[REDACTED]" in projection.errors[0]

    with pytest.raises(ValidationError, match="secret field"):
        ConnectionRecord(
            **{
                **_connection_record(connection_id="secret-fixture").model_dump(),
                "metadata": {"api_key": "must-not-be-stored"},
            }
        )
    with pytest.raises(ValidationError, match="unrestricted shell"):
        ConnectionCapability(
            id="unsafe",
            kind=ConnectionCapabilityKind.TOOL,
            name="Unsafe",
            tool_names=["terminal_run"],
        )

    reloaded = ConnectionRegistry(path)
    assert reloaded.get("calendar-a", project_id="project-a", session_id="session-a") is not None


def test_connection_registry_corruption_fails_closed_and_preserves_authority(tmp_path: Path) -> None:
    from agent.connections import ConnectionRegistry, ConnectionStateError

    path = tmp_path / "connections" / "registry.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version": 99, "connections": {}}', encoding="utf-8")
    with pytest.raises(ConnectionStateError, match="not overwritten"):
        ConnectionRegistry(path)
    assert '"schema_version": 99' in path.read_text(encoding="utf-8")
    assert list((path.parent / "corrupt-state").rglob("RECOVERY.txt"))
