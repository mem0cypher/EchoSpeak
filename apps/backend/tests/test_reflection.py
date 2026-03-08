"""
Tests for the ReflectionEngine (v7.0.0).

Covers:
- should_reflect heuristics (trivial tools, plan size, result length)
- reflect_on_step LLM evaluation
- reflect_on_plan post-plan evaluation
- get_retry_params adjustments
- Anti-loop guards (max cycles)
- Stream event emission for task_plan / task_step / task_reflection
- TaskPlanner integration with ReflectionEngine
- Result passing between dependent tasks
"""

import os
import sys
import pytest
from unittest.mock import Mock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── ReflectionEngine unit tests ─────────────────────────────────────

class TestReflectionEngineHeuristics:
    """Tests for should_reflect() gating logic."""

    def _make_engine(self):
        from agent.reflection import ReflectionEngine
        agent = Mock()
        agent.llm_wrapper = Mock()
        return ReflectionEngine(agent)

    def test_skip_trivial_tools(self):
        engine = self._make_engine()
        task = {"tool": "get_system_time", "index": 0}
        assert engine.should_reflect(task, "2026-03-07T19:30:00Z", plan_size=3) is False

        task2 = {"tool": "calculate", "index": 1}
        assert engine.should_reflect(task2, "42", plan_size=3) is False

        task3 = {"tool": "project_update_context", "index": 2}
        assert engine.should_reflect(task3, "recent changes...", plan_size=3) is False

    def test_skip_small_plans(self):
        engine = self._make_engine()
        task = {"tool": "web_search", "index": 0}
        assert engine.should_reflect(task, "", plan_size=1) is False

    def test_skip_substantial_results(self):
        engine = self._make_engine()
        task = {"tool": "web_search", "index": 0}
        long_result = "x" * 300
        assert engine.should_reflect(task, long_result, plan_size=3) is False

    def test_reflect_on_empty_result(self):
        engine = self._make_engine()
        task = {"tool": "web_search", "index": 0}
        assert engine.should_reflect(task, "", plan_size=3) is True

    def test_reflect_on_short_result(self):
        engine = self._make_engine()
        task = {"tool": "web_search", "index": 0}
        assert engine.should_reflect(task, "No results found", plan_size=2) is True

    def test_reflect_on_failure_signals(self):
        engine = self._make_engine()
        task = {"tool": "browse_task", "index": 0}
        assert engine.should_reflect(task, "Error: page not found", plan_size=2) is True
        assert engine.should_reflect(task, "Request timed out", plan_size=2) is True

    def test_max_cycles_prevents_infinite_loop(self):
        engine = self._make_engine()
        engine.max_cycles = 2
        task = {"tool": "web_search", "index": 0}

        # Simulate exhausting cycles
        engine._step_states[0] = Mock(cycles_used=2)
        assert engine.should_reflect(task, "", plan_size=3) is False


class TestReflectionEngineStepReflection:
    """Tests for reflect_on_step() LLM-based evaluation."""

    def _make_engine(self):
        from agent.reflection import ReflectionEngine
        agent = Mock()
        agent.llm_wrapper = Mock()
        return ReflectionEngine(agent)

    def test_accept_response_parsed_correctly(self):
        engine = self._make_engine()
        engine.agent.llm_wrapper.invoke.return_value = "ACCEPT: Result contains a valid cat meme URL"

        task = {"tool": "web_search", "index": 0, "description": "Search for cat memes"}
        result = engine.reflect_on_step(task, "imgur.com/cat123.jpg", "find a cat meme", 2)

        assert result.accepted is True
        assert "cat meme" in result.reason.lower()
        assert result.cycle == 1

    def test_retry_response_parsed_correctly(self):
        engine = self._make_engine()
        engine.agent.llm_wrapper.invoke.return_value = "RETRY: cat meme high quality funny"

        task = {"tool": "web_search", "index": 0, "description": "Search for cat memes"}
        result = engine.reflect_on_step(task, "no results", "find a cat meme", 2)

        assert result.accepted is False
        assert result.suggestion == "cat meme high quality funny"
        assert result.cycle == 1

    def test_ambiguous_response_defaults_to_accept(self):
        engine = self._make_engine()
        engine.agent.llm_wrapper.invoke.return_value = "Looks fine I guess"

        task = {"tool": "web_search", "index": 0}
        result = engine.reflect_on_step(task, "some result", "goal", 2)

        assert result.accepted is True

    def test_llm_failure_defaults_to_accept(self):
        engine = self._make_engine()
        engine.agent.llm_wrapper.invoke.side_effect = Exception("LLM down")

        task = {"tool": "web_search", "index": 0}
        result = engine.reflect_on_step(task, "some result", "goal", 2)

        assert result.accepted is True
        assert "failed" in result.reason.lower()

    def test_no_llm_defaults_to_accept(self):
        engine = self._make_engine()
        engine.agent.llm_wrapper = None

        task = {"tool": "web_search", "index": 0}
        result = engine.reflect_on_step(task, "some result", "goal", 2)

        assert result.accepted is True

    def test_cycle_budget_enforced(self):
        engine = self._make_engine()
        engine.max_cycles = 1
        engine.agent.llm_wrapper.invoke.return_value = "RETRY: try again"

        task = {"tool": "web_search", "index": 0}

        # First call uses the budget
        r1 = engine.reflect_on_step(task, "bad", "goal", 2)
        assert r1.accepted is False

        # Second call exceeds budget — auto-accept
        r2 = engine.reflect_on_step(task, "bad", "goal", 2)
        assert r2.accepted is True
        assert "max" in r2.reason.lower()


class TestReflectionEnginePlanReflection:
    """Tests for reflect_on_plan() post-plan evaluation."""

    def _make_engine(self):
        from agent.reflection import ReflectionEngine
        agent = Mock()
        agent.llm_wrapper = Mock()
        return ReflectionEngine(agent)

    def test_accomplished_parsed(self):
        engine = self._make_engine()
        engine.agent.llm_wrapper.invoke.return_value = "ACCOMPLISHED: All tasks completed successfully"

        result = engine.reflect_on_plan("find and post a cat meme", [
            {"description": "Search memes", "status": "completed", "result": "found"},
            {"description": "Post meme", "status": "completed", "result": "sent"},
        ])

        assert result.accepted is True

    def test_failed_parsed(self):
        engine = self._make_engine()
        engine.agent.llm_wrapper.invoke.return_value = "FAILED: Could not find any cat memes"

        result = engine.reflect_on_plan("find a cat meme", [
            {"description": "Search memes", "status": "failed", "result": "no results"},
        ])

        assert result.accepted is False

    def test_empty_tasks_accepted(self):
        engine = self._make_engine()
        result = engine.reflect_on_plan("goal", [])
        assert result.accepted is True


class TestReflectionEngineRetryParams:
    """Tests for get_retry_params() parameter adjustment."""

    def _make_engine(self):
        from agent.reflection import ReflectionEngine
        agent = Mock()
        return ReflectionEngine(agent)

    def test_web_search_retry_uses_suggestion_as_query(self):
        from agent.reflection import ReflectionResult
        engine = self._make_engine()
        task = {"tool": "web_search", "index": 0}
        reflection = ReflectionResult(accepted=False, suggestion="funny cat meme imgur")
        params = {"q": "cat meme"}

        new_params = engine.get_retry_params(task, reflection, params)
        assert new_params is not None
        assert new_params["q"] == "funny cat meme imgur"

    def test_accepted_result_returns_none(self):
        from agent.reflection import ReflectionResult
        engine = self._make_engine()
        task = {"tool": "web_search", "index": 0}
        reflection = ReflectionResult(accepted=True)
        assert engine.get_retry_params(task, reflection, {"q": "x"}) is None

    def test_no_suggestion_returns_none(self):
        from agent.reflection import ReflectionResult
        engine = self._make_engine()
        task = {"tool": "web_search", "index": 0}
        reflection = ReflectionResult(accepted=False, suggestion="")
        assert engine.get_retry_params(task, reflection, {"q": "x"}) is None

    def test_unknown_tool_returns_none(self):
        from agent.reflection import ReflectionResult
        engine = self._make_engine()
        task = {"tool": "some_custom_tool", "index": 0}
        reflection = ReflectionResult(accepted=False, suggestion="try this")
        assert engine.get_retry_params(task, reflection, {"x": "y"}) is None

    def test_browse_task_url_suggestion(self):
        from agent.reflection import ReflectionResult
        engine = self._make_engine()
        task = {"tool": "browse_task", "index": 0}
        reflection = ReflectionResult(accepted=False, suggestion="https://example.com/cats")
        params = {"url": "https://old.com"}

        new_params = engine.get_retry_params(task, reflection, params)
        assert new_params is not None
        assert new_params["url"] == "https://example.com/cats"

    def test_reset_clears_state(self):
        from agent.reflection import ReflectionEngine, StepReflectionState
        engine = self._make_engine()
        engine._step_states[0] = StepReflectionState(task_index=0, cycles_used=2)
        engine.reset()
        assert len(engine._step_states) == 0


# ── TaskPlanner integration tests ──────────────────────────────────

class TestTaskPlannerReflectionIntegration:
    """Tests for ReflectionEngine integration with TaskPlanner."""

    def _make_planner(self, tmp_path):
        from agent.core import TaskPlanner
        agent = Mock()
        agent.tools = []
        agent._stream_buffer = None
        agent._is_action_tool = Mock(return_value=False)
        agent._action_allowed = Mock(return_value=True)
        agent._emit_tool_start = Mock()
        agent._emit_tool_end = Mock()
        agent._emit_tool_error = Mock()
        agent._build_time_aware_web_query = Mock(side_effect=lambda q, _: q)
        planner = TaskPlanner(agent)
        return planner, agent

    def test_reflection_engine_lazy_init(self, tmp_path):
        planner, _ = self._make_planner(tmp_path)
        engine = planner.reflection_engine
        assert engine is not None

    def test_emit_task_plan_with_stream_buffer(self, tmp_path):
        from agent.stream_events import StreamBuffer
        planner, agent = self._make_planner(tmp_path)
        buf = StreamBuffer()
        agent._stream_buffer = buf

        planner.pending_tasks = [
            {"index": 0, "description": "Search web", "tool": "web_search", "status": "pending"},
            {"index": 1, "description": "Post result", "tool": "discord_send_channel", "status": "pending"},
        ]
        planner._emit_task_plan()

        events = buf.drain()
        assert len(events) == 1
        assert events[0].event_type == "task_plan"
        assert len(events[0].data) == 2

    def test_emit_task_step_with_stream_buffer(self, tmp_path):
        from agent.stream_events import StreamBuffer
        planner, agent = self._make_planner(tmp_path)
        buf = StreamBuffer()
        agent._stream_buffer = buf

        planner.pending_tasks = [
            {"index": 0, "description": "Search", "tool": "web_search", "status": "pending"},
        ]
        task = planner.pending_tasks[0]
        planner._emit_task_step(task, "running")

        events = buf.drain()
        assert len(events) == 1
        assert events[0].event_type == "task_step"
        assert events[0].data["status"] == "running"

    def test_emit_task_reflection_with_stream_buffer(self, tmp_path):
        from agent.stream_events import StreamBuffer
        planner, agent = self._make_planner(tmp_path)
        buf = StreamBuffer()
        agent._stream_buffer = buf

        planner.pending_tasks = [
            {"index": 0, "description": "Search", "tool": "web_search", "status": "pending"},
        ]
        task = planner.pending_tasks[0]
        planner._emit_task_reflection(task, False, "Result too short", 1)

        events = buf.drain()
        assert len(events) == 1
        assert events[0].event_type == "task_reflection"
        assert events[0].data["accepted"] is False


class TestTaskPlannerDependentResults:
    """Tests for result passing between dependent tasks."""

    def _make_planner(self):
        from agent.core import TaskPlanner
        agent = Mock()
        return TaskPlanner(agent)

    def test_resolve_prev_result_placeholder(self):
        planner = self._make_planner()
        planner.completed_tasks = [
            {"result": "https://imgur.com/cat123.jpg", "status": "completed"},
        ]

        task = {
            "params": {"message": "{{prev_result}}"},
            "depends_on": 0,
        }
        resolved = planner._resolve_dependent_params(task)
        assert resolved["message"] == "https://imgur.com/cat123.jpg"

    def test_auto_inject_empty_message(self):
        planner = self._make_planner()
        planner.completed_tasks = [
            {"result": "search result text", "status": "completed"},
        ]

        task = {
            "params": {"message": ""},
            "depends_on": 0,
        }
        resolved = planner._resolve_dependent_params(task)
        assert resolved["message"] == "search result text"

    def test_no_inject_for_independent_task(self):
        planner = self._make_planner()
        planner.completed_tasks = []

        task = {
            "params": {"q": "cat meme"},
            "depends_on": -1,
        }
        resolved = planner._resolve_dependent_params(task)
        assert resolved["q"] == "cat meme"

    def test_reset_clears_user_goal_and_reflection(self):
        planner = self._make_planner()
        planner._user_goal = "some goal"
        planner.pending_tasks = [{"index": 0}]
        planner.completed_tasks = [{"index": 0}]
        planner.current_task_index = 1

        planner.reset()

        assert planner._user_goal == ""
        assert planner.pending_tasks == []
        assert planner.completed_tasks == []
        assert planner.current_task_index == 0


# ── StreamBuffer event method tests ────────────────────────────────

class TestStreamBufferTaskEvents:
    """Tests for the new task event push methods on StreamBuffer."""

    def test_push_task_plan(self):
        from agent.stream_events import StreamBuffer
        buf = StreamBuffer()
        tasks = [
            {"index": 0, "description": "Search", "tool": "web_search", "status": "pending"},
            {"index": 1, "description": "Post", "tool": "discord_send_channel", "status": "pending"},
        ]
        buf.push_task_plan(tasks)
        events = buf.drain()
        assert len(events) == 1
        assert events[0].event_type == "task_plan"
        assert events[0].data == tasks

    def test_push_task_step(self):
        from agent.stream_events import StreamBuffer
        buf = StreamBuffer()
        buf.push_task_step(index=0, status="running", description="Search", tool="web_search", total=2)
        events = buf.drain()
        assert len(events) == 1
        assert events[0].event_type == "task_step"
        assert events[0].data["index"] == 0
        assert events[0].data["status"] == "running"

    def test_push_task_reflection(self):
        from agent.stream_events import StreamBuffer
        buf = StreamBuffer()
        buf.push_task_reflection(index=0, accepted=False, reason="Too short", cycle=1)
        events = buf.drain()
        assert len(events) == 1
        assert events[0].event_type == "task_reflection"
        assert events[0].data["accepted"] is False
        assert events[0].data["reason"] == "Too short"

    def test_result_preview_truncated(self):
        from agent.stream_events import StreamBuffer
        buf = StreamBuffer()
        long_preview = "x" * 500
        buf.push_task_step(index=0, status="done", result_preview=long_preview)
        events = buf.drain()
        assert len(events[0].data["result_preview"]) == 200


class TestNeedsPlanningDetection:
    """Regression tests for needs_planning() action verb coverage."""

    def _make_planner(self):
        from agent.core import TaskPlanner
        return TaskPlanner(Mock())

    def test_search_and_post_triggers_planning(self):
        planner = self._make_planner()
        assert planner.needs_planning("search your latest updates in ur changelog and then post it in discord #updates channel") is True

    def test_find_and_post_triggers_planning(self):
        planner = self._make_planner()
        assert planner.needs_planning("find a cat meme and post it in discord general") is True

    def test_search_and_share_triggers_planning(self):
        planner = self._make_planner()
        assert planner.needs_planning("search for Python news and share it on Twitter") is True

    def test_check_and_send_triggers_planning(self):
        planner = self._make_planner()
        assert planner.needs_planning("check my email and send a reply to John") is True

    def test_browse_and_post_triggers_planning(self):
        planner = self._make_planner()
        assert planner.needs_planning("browse github trending and post the top repo in #general") is True

    def test_search_and_tweet_triggers_planning(self):
        planner = self._make_planner()
        assert planner.needs_planning("search for NBA scores and tweet the results") is True

    def test_single_post_does_not_trigger(self):
        planner = self._make_planner()
        assert planner.needs_planning("post a message in discord") is False

    def test_single_search_does_not_trigger(self):
        planner = self._make_planner()
        assert planner.needs_planning("search for the latest news") is False

    def test_what_changed_does_not_trigger(self):
        planner = self._make_planner()
        assert planner.needs_planning("what changed recently") is False

    def test_simple_send_does_not_trigger(self):
        planner = self._make_planner()
        assert planner.needs_planning("send a message to oxi on Discord") is False

    def test_capability_query_does_not_trigger(self):
        planner = self._make_planner()
        assert planner.needs_planning("what can you do right now?") is False

    def test_remember_does_not_trigger(self):
        planner = self._make_planner()
        assert planner.needs_planning("remember that my favorite color is blue") is False


class TestDependentParamResolution:
    """Regression tests for {{prev_result}} resolution and dependency handling."""

    def _make_planner(self, tmp_path=None):
        from agent.core import TaskPlanner
        agent = Mock()
        agent._stream_buffer = None
        agent._is_action_tool = Mock(return_value=False)
        agent._action_allowed = Mock(return_value=True)
        planner = TaskPlanner(agent)
        return planner, agent

    def test_prev_result_resolved_before_action_gate(self):
        """{{prev_result}} must be replaced with actual content before creating pending action."""
        planner, agent = self._make_planner()
        agent._is_action_tool = Mock(return_value=True)
        agent._action_allowed = Mock(return_value=True)
        agent._set_pending_action = Mock()
        agent._format_pending_action = Mock(return_value="Post to Discord")
        agent._last_user_input_for_plan = "test query"

        # Task 0 completed with result
        task0 = {"index": 0, "tool": "project_update_context", "params": {}, "depends_on": -1, "status": "completed", "result": "Changelog v7.0.0: big update"}
        planner.completed_tasks = [task0]

        # Task 1 depends on task 0, has {{prev_result}} placeholder
        task1 = {"index": 1, "tool": "discord_send_channel", "params": {"channel": "updates", "message": "{{prev_result}}", "server": "test"}, "depends_on": 0, "status": "pending", "result": None}
        planner.pending_tasks = [task0, task1]
        planner.current_task_index = 1

        # Mock tool
        mock_tool = Mock()
        mock_tool.name = "discord_send_channel"

        result = planner.execute_next_task([mock_tool])
        assert result["status"] == "pending_confirmation"

        # Verify the pending action kwargs have resolved content, not the placeholder
        call_args = agent._set_pending_action.call_args
        pending_action = call_args[0][0]
        assert "{{prev_result}}" not in pending_action["kwargs"]["message"]
        assert "Changelog v7.0.0" in pending_action["kwargs"]["message"]

    def test_auto_inject_into_empty_message_for_action_tool(self):
        """Empty message param should be auto-filled from dependency result for action tools."""
        planner, agent = self._make_planner()
        agent._is_action_tool = Mock(return_value=True)
        agent._action_allowed = Mock(return_value=True)
        agent._set_pending_action = Mock()
        agent._format_pending_action = Mock(return_value="Post to Discord")
        agent._last_user_input_for_plan = "test"

        task0 = {"index": 0, "tool": "web_search", "params": {"q": "news"}, "depends_on": -1, "status": "completed", "result": "Top news: AI breakthrough"}
        planner.completed_tasks = [task0]

        task1 = {"index": 1, "tool": "discord_send_channel", "params": {"channel": "general", "message": "", "server": "test"}, "depends_on": 0, "status": "pending", "result": None}
        planner.pending_tasks = [task0, task1]
        planner.current_task_index = 1

        mock_tool = Mock()
        mock_tool.name = "discord_send_channel"

        result = planner.execute_next_task([mock_tool])
        call_args = agent._set_pending_action.call_args
        pending_action = call_args[0][0]
        assert pending_action["kwargs"]["message"] == "Top news: AI breakthrough"


class TestBlockedTaskFailForward:
    """Regression tests for blocked-task handling (no infinite loops)."""

    def _make_planner(self):
        from agent.core import TaskPlanner
        agent = Mock()
        agent._stream_buffer = None
        agent._is_action_tool = Mock(return_value=False)
        planner = TaskPlanner(agent)
        return planner, agent

    def test_blocked_task_fails_forward_when_dependency_failed(self):
        """A task whose dependency failed should fail-forward, not loop forever."""
        planner, agent = self._make_planner()

        task0 = {"index": 0, "tool": "missing_tool", "params": {}, "depends_on": -1, "status": "failed", "result": "Tool not found"}
        task1 = {"index": 1, "tool": "discord_send_channel", "params": {"message": "test"}, "depends_on": 0, "status": "pending", "result": None}

        planner.pending_tasks = [task0, task1]
        planner.completed_tasks = [task0]
        planner.current_task_index = 1

        mock_tool = Mock()
        mock_tool.name = "discord_send_channel"

        result = planner.execute_next_task([mock_tool])
        assert result["status"] == "failed"
        assert planner.current_task_index == 2  # Advanced past the blocked task

    def test_blocked_task_fails_when_dependency_not_completed(self):
        """A task whose dependency hasn't completed at all should fail, not block."""
        planner, agent = self._make_planner()

        task0 = {"index": 0, "tool": "web_search", "params": {}, "depends_on": -1, "status": "pending", "result": None}
        task1 = {"index": 1, "tool": "discord_send_channel", "params": {"message": "test"}, "depends_on": 0, "status": "pending", "result": None}

        planner.pending_tasks = [task0, task1]
        planner.completed_tasks = []  # task0 never completed
        planner.current_task_index = 1

        mock_tool = Mock()
        mock_tool.name = "discord_send_channel"

        result = planner.execute_next_task([mock_tool])
        assert result["status"] == "failed"
        assert planner.current_task_index == 2

    def test_execute_all_safety_cap_prevents_infinite_loop(self):
        """execute_all() should break after safety cap iterations."""
        planner, agent = self._make_planner()

        # Create a scenario where execute_next_task always returns None (shouldn't happen but tests safety)
        planner.pending_tasks = [{"index": 0, "tool": "x", "params": {}, "depends_on": -1, "status": "pending", "result": None}]
        planner.current_task_index = 0

        # Patch execute_next_task to simulate stuck behavior (returns task but doesn't advance)
        call_count = [0]
        original = planner.execute_next_task
        def patched_execute(tools, callbacks=None):
            call_count[0] += 1
            if call_count[0] > 10:
                # Safety: force advance to prevent actual infinite loop in test
                planner.current_task_index = len(planner.pending_tasks)
                return None
            return {"status": "running", "tool": "x"}  # Never advances naturally
        planner.execute_next_task = patched_execute

        results = planner.execute_all([], None)
        # Should have been capped, not looped forever
        assert call_count[0] <= 10
