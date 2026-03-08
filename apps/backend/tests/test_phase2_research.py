import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.research import build_research_run


def test_build_research_run_parses_numbered_search_results():
    output = "\n\n".join([
        "1. EchoSpeak Roadmap\n   URL: https://example.com/roadmap\n   Query: echospeak roadmap latest\n   Date: 2 hours ago\n   Snippet: Strategic roadmap update\n   Extract: Phase 2 research lane is now complete.",
        "2. EchoSpeak Audit\n   URL: https://example.com/audit\n   Date: 2026-03-05\n   Snippet: Architecture audit\n   Extract: Platform integrity and research extraction details.",
    ])

    run = build_research_run(
        run_id="run-1",
        tool_name="web_search",
        tool_input='{"query": "echospeak roadmap latest"}',
        output=output,
        at=123.0,
    )

    assert run is not None
    assert run["mode"] == "recent"
    assert run["recency_intent"] is True
    assert run["evidence_count"] == 2
    assert run["evidence"][0]["title"] == "EchoSpeak Roadmap"
    assert run["evidence"][0]["domain"] == "example.com"
    assert run["evidence"][0]["recency_bucket"] in {"breaking", "recent"}


def test_build_research_run_ignores_unsupported_tool():
    output = "Title: Live Scoreboard\nURL: https://example.com/live\n\nContent:\nThe latest live coverage and stats."

    run = build_research_run(
        run_id="run-2",
        tool_name="unsupported_search_tool",
        tool_input="latest live scores",
        output=output,
        at=456.0,
    )

    assert run is None
