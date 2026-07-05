import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.context_budget import ContextBlock, ContextBudgetManager
from agent.research import SearchGrounder, build_search_intent
from agent.session_memory import SessionMemoryDistiller
from agent.verification import VerificationTelemetry


def test_search_grounder_forces_live_score_language():
    intent = build_search_intent("Canada vs Morocco score right now")
    candidates = SearchGrounder(max_candidates=3).build_candidates(intent)
    queries = [c.query.lower() for c in candidates]

    assert any("live score" in q or "current score" in q or "result" in q for q in queries)
    assert not queries[0].endswith("date")


def test_search_grounder_anchors_referential_followup_to_current_subject():
    intent = build_search_intent(
        "do a deeper search",
        resolved_request="do a deeper search",
        current_subject="Canada vs Morocco World Cup score",
    )
    candidates = SearchGrounder(max_candidates=3).build_candidates(intent)

    assert any("canada vs morocco world cup score" in c.query.lower() for c in candidates)


def test_search_grounder_rejects_date_only_score_evidence_then_accepts_live_score():
    calls = []

    def execute(query: str) -> str:
        calls.append(query)
        if len(calls) == 1:
            return "1. Schedule page\n   URL: https://example.com/schedule\n   Snippet: Canada vs Morocco kickoff date and start time."
        return "1. Live scoreboard\n   URL: https://example.com/live\n   Snippet: Canada 2-1 Morocco live score result."

    result = SearchGrounder(max_candidates=3).ground(
        original_request="Canada vs Morocco score right now",
        resolved_request="Canada vs Morocco score right now",
        execute=execute,
    )

    assert result.accepted is True
    assert len(calls) >= 2
    assert result.rejected_candidates
    assert "live score" in result.chosen_query.lower() or "current score" in result.chosen_query.lower()


def test_search_grounder_rejects_schedule_nav_snippet_without_specific_answer():
    def execute(_query: str) -> str:
        return (
            "1. ESPN Schedule\n"
            "   URL: https://example.com/soccer/schedule\n"
            "   Snippet: Sunday, July 5, 2026 Schedule Results Standings Teams Stats Tickets"
        )

    result = SearchGrounder(max_candidates=1).ground(
        original_request="who's playing today?",
        resolved_request="who's playing today?",
        execute=execute,
    )

    assert result.accepted is False
    assert "specific current answer" in result.rejected_candidates[0]["reason"]


def test_search_grounder_fetches_promising_page_when_snippet_is_weak():
    def execute(_query: str) -> str:
        return (
            "1. Today fixtures\n"
            "   URL: https://example.com/fixtures\n"
            "   Snippet: Sunday, July 5, 2026 Schedule Results Standings Teams"
        )

    def fetch(_url: str) -> str:
        return "Canada vs Morocco starts at 3:00 PM ET today. Portugal vs Spain starts at 6:00 PM ET."

    result = SearchGrounder(max_candidates=1).ground(
        original_request="who's playing today?",
        resolved_request="who's playing today?",
        execute=execute,
        fetch_url=fetch,
    )

    assert result.accepted is True
    assert result.evidence[0].fetched_full_page is True
    assert "Canada vs Morocco" in result.condensed_evidence


def test_context_budget_preserves_high_priority_and_trims_low_priority():
    manager = ContextBudgetManager(context_window=140, reserve_tokens=80, enabled=True)
    context, report = manager.fit_blocks(
        [
            ContextBlock("profile", "User's name: Ty", 1, "User profile"),
            ContextBlock("pinned", "Always keep this pinned fact.", 2, "Pinned memory"),
            ContextBlock("raw_history", "old chat " * 600, 9, "Raw chat history"),
        ],
        overhead_tokens=40,
    )

    assert "User's name: Ty" in context
    assert "Always keep this pinned fact." in context
    assert "raw_history" in report.trimmed_blocks


def test_context_budget_reports_graduated_pressure_and_protected_blocks():
    manager = ContextBudgetManager(context_window=200, reserve_tokens=20, enabled=True)
    context, report = manager.fit_blocks(
        [
            ContextBlock("active_task_plan", "Keep this plan", 1, protected=True),
            ContextBlock("raw_history", "old chat " * 260, 9),
        ],
        overhead_tokens=100,
    )

    assert "Keep this plan" in context
    assert "active_task_plan" in (report.protected_blocks or [])
    assert report.stage in {"soft_trim", "summarize", "compact"}


def test_session_memory_updates_durable_summary_file(tmp_path):
    distiller = SessionMemoryDistiller(tmp_path, update_turns=1)
    state = distiller.update_turn(
        thread_id="thread-a",
        user_input="We need to build the coding agent loop.",
        response_text="We decided to start with deterministic file inspection.",
        current_subject="coding agent loop",
    )

    path = distiller.path_for("thread-a")
    context = distiller.context_for("thread-a")

    assert path.exists()
    assert state.current_subject == "coding agent loop"
    assert "coding agent loop" in context
    assert "Latest user request" in context


def test_verification_telemetry_records_failure_clusters(tmp_path):
    telemetry = VerificationTelemetry(path=tmp_path / "verification.jsonl", enabled=True)
    telemetry.record("terminal_nonzero", tool="terminal_run", reason="ExitCode=1")
    telemetry.record("search_query_rejected", tool="web_search", reason="date-only evidence")

    report = telemetry.report()

    assert report["count"] == 2
    assert report["clusters"]["terminal_nonzero"] == 1
    assert report["clusters"]["search_query_rejected"] == 1
    assert (tmp_path / "verification.jsonl").exists()


def test_verification_telemetry_weights_known_failure_clusters():
    telemetry = VerificationTelemetry(enabled=False)

    assert telemetry.verification_level("get_system_time") == "low"
    assert telemetry.should_verify("get_system_time") is False
    assert telemetry.verification_level("web_search") == "high"
    assert telemetry.should_verify("web_search") is True

    telemetry.record("tool_call_syntax_unrecognized", reason="raw execute_tool leaked")
    assert telemetry.verification_level("file_read", "tool_call_syntax_unrecognized") == "high"
