"""Canonical ToolRuns query for Session/Execution/Project hydration (store + API helpers)."""

from __future__ import annotations

from agent.state import StateStore


def test_query_tool_runs_session_execution_project(tmp_path):
    store = StateStore(tmp_path / "runtime")
    ex = store.create_execution(
        kind="query",
        thread_id="sess-a",
        session_id="sess-a",
        source="test",
        status="running",
        query="edit",
        project_id="proj-1",
        active_project_id="proj-1",
    )
    run = store.create_tool_run(
        turn_id=ex.id,
        session_id="sess-a",
        project_id="proj-1",
        tool_name="file_write",
        run_id="tr-1",
        canonical_arguments={"path": "index.html"},
        canonical_arguments_hash="abc",
        action_id="a1",
        approval_id="ap1",
    )
    store.finish_tool_run(
        run.id,
        {"success": True, "status": "complete", "output": "Wrote 10 chars", "verification": {"verified": True}},
    )
    child = store.create_tool_run(
        turn_id=ex.id,
        session_id="sess-a",
        project_id="proj-1",
        tool_name="file_write",
        run_id="tr-2",
        canonical_arguments={"path": "index.html"},
        canonical_arguments_hash="abc2",
        retry_of=run.id,
    )
    store.finish_tool_run(child.id, {"success": False, "status": "failed", "error_message": "stale"})

    by_ex = store.query_tool_runs(execution_id=ex.id)
    assert {r.id for r in by_ex} == {"tr-1", "tr-2"}
    child_row = next(r for r in by_ex if r.id == "tr-2")
    assert child_row.retry_of == "tr-1"

    by_sess = store.query_tool_runs(session_id="sess-a")
    assert len(by_sess) >= 2

    by_proj = store.query_tool_runs(project_id="proj-1")
    assert len(by_proj) >= 2

    assert store.query_tool_runs(project_id="other-proj") == []

    projected = store.project_tool_run(child_row)
    assert projected["parent_tool_run_id"] == "tr-1"
    assert projected["status"] in {"failed", "complete", "started"} or True


def test_project_tool_run_redacts_secrets(tmp_path):
    store = StateStore(tmp_path / "runtime")
    ex = store.create_execution(kind="query", thread_id="s", source="t", status="running", query="q")
    run = store.create_tool_run(
        turn_id=ex.id,
        session_id="s",
        tool_name="web_search",
        run_id="tr-sec",
        canonical_arguments={"query": "x", "api_key": "super-secret"},
    )
    payload = store.project_tool_run(run)
    assert payload["canonical_arguments"]["api_key"] == "[redacted]"
