import json

from agent.deep_search_workflow import EchoSearchWorkflow, analyze_gaps, dedupe_results
from agent.evidence_store import Evidence, PageCache
from agent.search_plan import SearchMode, build_search_plan
from agent.search_provider import NormalizedSearchResult, SearXNGSearchProvider, normalize_searxng_results


class StubLLM:
    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return json.dumps(self.payload)


def test_search_plan_from_llm_json_is_clamped_and_structured():
    llm = StubLLM(
        {
            "mode": "QUICK_SEARCH",
            "user_intent": "Find latest project pricing",
            "must_answer": ["current price", "plan limits"],
            "entities": ["Example API"],
            "recency_required": True,
            "queries": [
                {"query": "Example API pricing 2026", "purpose": "pricing", "expected_source_type": "pricing"},
                {"query": "Example API docs pricing", "purpose": "official docs", "expected_source_type": "official"},
                {"query": "extra query should be clamped", "purpose": "extra", "expected_source_type": "general"},
            ],
            "stop_condition": "price and limits found",
            "risk_notes": ["pricing changes often"],
        }
    )
    plan = build_search_plan("How much is Example API now?", llm)
    assert plan.mode == SearchMode.QUICK_SEARCH
    assert len(plan.queries) == 2
    assert plan.queries[0].query != "How much is Example API now?"
    assert "SearchPlan schema" in llm.prompts[0]




def test_search_plan_normalizes_planner_query_typos():
    plan = build_search_plan(
        "What are the Oilers chances?",
        StubLLM(
            {
                "mode": "QUICK_SEARCH",
                "user_intent": "Estimate Edmonton Oilers Stanley Cup chances",
                "must_answer": ["Stanley Cup chances"],
                "entities": ["Edmonton Oilers"],
                "recency_required": True,
                "queries": [
                    {
                        "query": "chances edmonton oilers win the standly cup",
                        "purpose": "odds lookup",
                        "expected_source_type": "general",
                    }
                ],
                "stop_condition": "odds found",
                "risk_notes": [],
            }
        ),
    )
    assert "standly" not in plan.queries[0].query.lower()
    assert "stanley" in plan.queries[0].query.lower()
def test_searxng_result_normalization_shape_and_rank():
    payload = {
        "results": [
            {"title": "Official Docs", "url": "https://example.com/docs", "content": "Docs snippet", "engine": "duckduckgo"},
            {"title": "Forum", "url": "https://forum.example.com/t", "content": "Forum snippet", "engines": ["brave", "google"]},
        ]
    }
    rows = normalize_searxng_results(payload, limit=5)
    assert rows[0].as_dict() == {
        "title": "Official Docs",
        "url": "https://example.com/docs",
        "snippet": "Docs snippet",
        "source": "searxng",
        "engine": "duckduckgo",
        "rank": 1,
    }
    assert rows[1].engine == "brave,google"
    assert rows[1].rank == 2


def test_dedupe_results_limits_duplicate_urls_and_domains():
    rows = [
        NormalizedSearchResult("A", "https://example.com/a", "", rank=1),
        NormalizedSearchResult("A duplicate", "https://example.com/a", "", rank=2),
        NormalizedSearchResult("B", "https://example.com/b", "", rank=3),
        NormalizedSearchResult("C", "https://example.com/c", "", rank=4),
        NormalizedSearchResult("D", "https://other.com/d", "", rank=5),
    ]
    deduped = dedupe_results(rows, max_per_domain=2)
    assert [r.url for r in deduped] == [
        "https://example.com/a",
        "https://example.com/b",
        "https://other.com/d",
    ]


def test_gap_analysis_requires_official_source_when_planned():
    plan = build_search_plan(
        "Find official API behavior",
        StubLLM(
            {
                "mode": "DEEP_SEARCH",
                "user_intent": "Find official API behavior",
                "must_answer": ["API behavior"],
                "entities": ["Example API"],
                "recency_required": False,
                "queries": [{"query": "Example API official docs", "purpose": "official", "expected_source_type": "official"}],
                "stop_condition": "official behavior found",
                "risk_notes": [],
            }
        ),
    )
    weak = [
        Evidence(
            claim="A forum says the API behaves this way.",
            source_url="https://forum.example.com/t",
            source_title="Forum thread",
            quote_or_summary="A forum says the API behaves this way.",
            confidence="medium",
            supports_answer_part="API behavior",
        )
    ]
    gap = analyze_gaps(plan, weak, [])
    assert not gap.enough_evidence
    assert gap.need_official_source


def test_deep_workflow_stops_after_first_round_when_evidence_sufficient(tmp_path, monkeypatch):
    plan = build_search_plan(
        "Compare official behavior",
        StubLLM(
            {
                "mode": "DEEP_SEARCH",
                "user_intent": "Compare official behavior",
                "must_answer": ["API behavior"],
                "entities": ["Example API"],
                "recency_required": False,
                "queries": [{"query": "Example API official docs behavior", "purpose": "official", "expected_source_type": "official"}],
                "stop_condition": "API behavior found",
                "risk_notes": [],
            }
        ),
    )

    class FakeProvider(SearXNGSearchProvider):
        def __init__(self):
            self.calls = 0

        def search(self, query, *, limit=5, categories=None):
            self.calls += 1
            return [NormalizedSearchResult("Official Example API docs", "https://official.example.com/docs", "API behavior details", rank=1)]

    monkeypatch.setattr(
        "agent.deep_search_workflow.fetch_page_text",
        lambda url, cache: "The official Example API documentation states the API behavior is deterministic and supported.",
    )
    provider = FakeProvider()
    workflow = EchoSearchWorkflow(provider=provider, cache=PageCache(tmp_path / "pages.sqlite"))
    result = workflow.run_plan(plan)
    assert result.rounds == 1
    assert provider.calls == 1
    assert result.gap.enough_evidence

def test_workflow_uses_planned_query_not_raw_user_request(tmp_path):
    raw_request = "chances edmonton oilers win the standly cup"

    class FakeProvider(SearXNGSearchProvider):
        def __init__(self):
            self.queries = []

        def search(self, query, *, limit=5, categories=None):
            self.queries.append(query)
            return [
                NormalizedSearchResult(
                    "NHL analysis",
                    "https://example.com/nhl",
                    "Edmonton Oilers Stanley Cup chances are discussed with current playoff odds.",
                    rank=1,
                )
            ]

    llm = StubLLM(
        {
            "mode": "QUICK_SEARCH",
            "user_intent": "Estimate Edmonton Oilers Stanley Cup odds",
            "must_answer": ["Stanley Cup chances"],
            "entities": ["Edmonton Oilers"],
            "recency_required": True,
            "queries": [
                {
                    "query": "Edmonton Oilers Stanley Cup chances current odds",
                    "purpose": "Use normalized spelling and current odds framing",
                    "expected_source_type": "general",
                }
            ],
            "stop_condition": "current odds evidence found",
            "risk_notes": ["sports odds change frequently"],
        }
    )
    provider = FakeProvider()
    result = EchoSearchWorkflow(provider=provider, cache=PageCache(tmp_path / "pages.sqlite")).run(
        user_request=raw_request,
        llm=llm,
    )
    assert provider.queries == ["Edmonton Oilers Stanley Cup chances current odds"]
    assert raw_request not in provider.queries
    assert result.rounds == 1


def test_local_first_search_can_stop_before_web(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "api.md").write_text(
        "The Example API retry policy is deterministic and supported for idempotent requests.",
        encoding="utf-8",
    )

    plan = build_search_plan(
        "Find the local Example API retry policy",
        StubLLM(
            {
                "mode": "LOCAL_FIRST_SEARCH",
                "user_intent": "Find local Example API retry policy",
                "must_answer": ["retry policy"],
                "entities": ["Example API"],
                "recency_required": False,
                "queries": [{"query": "Example API retry policy", "purpose": "fallback web search", "expected_source_type": "docs"}],
                "stop_condition": "local retry policy found",
                "risk_notes": [],
            }
        ),
    )

    class NoWebProvider(SearXNGSearchProvider):
        def __init__(self):
            self.calls = 0

        def search(self, query, *, limit=5, categories=None):
            self.calls += 1
            raise AssertionError("LOCAL_FIRST_SEARCH should not hit web when local evidence is sufficient")

    provider = NoWebProvider()
    result = EchoSearchWorkflow(
        provider=provider,
        cache=PageCache(tmp_path / "pages.sqlite"),
        local_root=docs,
    ).run_plan(plan)
    assert provider.calls == 0
    assert result.rounds == 0
    assert result.gap.enough_evidence
    assert result.evidence[0].source_url.startswith("file://")

