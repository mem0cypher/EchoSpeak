"""Focused contracts for versioned research state and provider-neutral live data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_typed_research_artifact_round_trip_is_atomic_and_exact_scoped(tmp_path, monkeypatch):
    import agent.research_artifacts as ra

    monkeypatch.setattr(ra, "_ROOT", tmp_path / "research")
    source = ra.ResearchSource(
        id="source-1",
        branch_id="branch-1",
        provider="fixture",
        source_identifier="fixture:one",
        title="Primary source",
        url="https://example.test/source",
        freshness="current",
        provenance={"retrieval": "structured"},
    )
    evidence = ra.EvidenceRecord(
        id="evidence-1",
        branch_id="branch-1",
        source_id=source.id,
        content="The exact value is 42.",
        locator="field:value",
        confidence=0.99,
        exact=True,
    )
    claim = ra.ClaimRecord(
        id="claim-1",
        branch_id="branch-1",
        text="The value is 42.",
        evidence_ids=[evidence.id],
        confidence=0.99,
        status="supported",
        contradiction_ids=["contradiction-1"],
    )
    artifact = ra.ResearchArtifact(
        id="artifact-1",
        project_id="project-a",
        session_id="session-a",
        objective="Resolve an exact value",
        query="What is the value?",
        resolved_scope="fixture:one",
        model_provider="configured-provider",
        model_id="active-session-model",
        plan=ra.ResearchPlan(
            id="plan-1",
            mode="deep_research",
            question="What is the value?",
            branch_ids=["branch-1"],
            budget=ra.ResearchBudget(max_sources=3, max_branches=1),
            stop_conditions=["supported exact claim"],
        ),
        branches=[
            ra.ResearchBranch(
                id="branch-1",
                objective="Extract the exact value",
                status="completed",
                source_ids=[source.id],
                evidence_ids=[evidence.id],
                claim_ids=[claim.id],
            )
        ],
        sources=[source],
        evidence=[evidence],
        claims=[claim],
        contradictions=[
            ra.ContradictionRecord(
                id="contradiction-1",
                claim_ids=[claim.id],
                description="A weaker source reported 41.",
                status="resolved",
                resolution="Primary structured source wins.",
            )
        ],
        coverage_gaps=[
            ra.CoverageGap(
                id="gap-1",
                branch_id="branch-1",
                question="Is there a second primary source?",
                reason="Fixture budget exhausted",
                status="accepted",
            )
        ],
        verification=[
            ra.VerificationRecord(
                id="verification-1",
                claim_ids=[claim.id],
                evidence_ids=[evidence.id],
                method="independent exact-value check",
                status="passed",
                summary="Value and source locator agree.",
                model_provider="configured-provider",
                model_id="active-session-model",
            )
        ],
        summary="The exact value is 42.",
        citations=[{"url": source.url, "title": source.title}],
        source_urls=[source.url],
        outcome="verified",
    )

    stored = ra.save_research_artifact(artifact)
    loaded = ra.get_research_artifact(artifact.id)

    assert stored.schema_version == ra.CURRENT_RESEARCH_SCHEMA_VERSION
    assert loaded is not None
    assert loaded.plan is not None
    assert loaded.plan.budget.max_branches == 1
    assert loaded.branches[0].status == "completed"
    assert loaded.evidence[0].exact is True
    assert loaded.claims[0].evidence_ids == ["evidence-1"]
    assert loaded.verification[0].status == "passed"
    assert ra.require_exact_artifact_scope(
        loaded, project_id="project-a", session_id="session-a"
    ) is loaded
    assert ra.get_research_artifact_for_scope(
        artifact.id, project_id="project-a", session_id="wrong-session"
    ) is None
    with pytest.raises(ra.ResearchArtifactAccessError):
        ra.require_exact_artifact_scope(loaded, project_id="project-a", session_id="")

    ra.save_research_artifact(
        ra.ResearchArtifact(
            id="artifact-2",
            project_id="project-a",
            session_id="session-b",
            summary="Other session",
        )
    )
    scoped = ra.list_research_artifacts_for_scope(
        project_id="project-a", session_id="session-a"
    )
    assert [row.id for row in scoped] == ["artifact-1"]
    assert not list((tmp_path / "research").glob("*.tmp"))


def test_v1_artifact_migrates_in_memory_without_rewriting_authority(tmp_path, monkeypatch):
    import agent.research_artifacts as ra

    root = tmp_path / "research"
    root.mkdir()
    monkeypatch.setattr(ra, "_ROOT", root)
    legacy = {
        "schema_version": 1,
        "id": "legacy-one",
        "project_id": "project-a",
        "session_id": "session-a",
        "objective": "Legacy objective",
        "query": "legacy query",
        "summary": "Legacy synthesis",
        "citations": [{"url": "https://example.test/legacy", "title": "Legacy"}],
        "source_urls": ["https://example.test/legacy"],
        "status": "ready",
    }
    path = root / "legacy-one.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    loaded = ra.get_research_artifact("legacy-one")

    assert loaded is not None
    assert loaded.schema_version == 2
    assert loaded.migrated_from_schema == 1
    assert loaded.summary == "Legacy synthesis"
    assert loaded.sources == []
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_corrupt_authoritative_artifact_fails_closed_and_writes_recovery_guide(
    tmp_path, monkeypatch
):
    import agent.research_artifacts as ra

    root = tmp_path / "research"
    root.mkdir()
    monkeypatch.setattr(ra, "_ROOT", root)
    path = root / "broken.json"
    corrupt_text = '{"schema_version": 2, "id": "broken"'
    path.write_text(corrupt_text, encoding="utf-8")

    with pytest.raises(ra.ResearchArtifactStoreError, match="original was not overwritten"):
        ra.get_research_artifact("broken")

    assert path.read_text(encoding="utf-8") == corrupt_text
    quarantine_dirs = list((root / "corrupt-state").iterdir())
    assert len(quarantine_dirs) == 1
    quarantine = quarantine_dirs[0]
    assert (quarantine / "broken.json").read_text(encoding="utf-8") == corrupt_text
    recovery = (quarantine / "RECOVERY.txt").read_text(encoding="utf-8")
    assert "Manual recovery" in recovery
    assert "Repair the authoritative JSON" in recovery


def test_live_router_uses_deterministic_structured_adapter_and_preserves_provenance():
    from agent.live_retrieval import (
        ExactValue,
        FixtureLiveAdapter,
        LiveDomain,
        LiveProvenance,
        LiveRetrievalRequest,
        LiveRetrievalRouter,
        ResolvedEntity,
        StructuredLiveResult,
    )

    provider_timestamp = 1_700_000_000.0
    retrieval_timestamp = 1_700_000_001.0
    query = "What is the Bitcoin price in USD right now?"
    expected = StructuredLiveResult(
        domain=LiveDomain.FINANCE_CRYPTO,
        resolved_entities=[ResolvedEntity(id="btc", name="Bitcoin", entity_type="crypto_asset")],
        exact_values=[
            ExactValue(
                name="spot_price",
                value="68123.45",
                currency="USD",
                effective_at=provider_timestamp,
            )
        ],
        currencies={"spot_price": "USD"},
        event_type="market_quote",
        status_type="current",
        provider_timestamp=provider_timestamp,
        retrieval_timestamp=retrieval_timestamp,
        freshness="current",
        provider="fixture-market",
        source_identifier="BTC-USD",
        provenance=[
            LiveProvenance(
                provider="fixture-market",
                source_identifier="BTC-USD",
                retrieved_at=retrieval_timestamp,
            )
        ],
        confidence=0.99,
        completeness=1.0,
    )
    router = LiveRetrievalRouter([FixtureLiveAdapter({query: expected}, name="fixture-market")])
    request = LiveRetrievalRequest(query=query, expected_currency="USD")

    route = router.route(request)
    result = router.lookup(request)

    assert route.mode == "live_structured"
    assert route.domain == LiveDomain.FINANCE_CRYPTO
    assert route.adapter_name == "fixture-market"
    assert result.exact_values[0].value == "68123.45"
    assert result.exact_values[0].currency == "USD"
    assert result.provider_timestamp == provider_timestamp
    assert result.retrieval_timestamp == retrieval_timestamp
    assert result.provenance[0].source_identifier == "BTC-USD"
    assert not result.errors


def test_live_router_distinguishes_flight_status_offers_and_honestly_abstains():
    from agent.live_retrieval import LiveIntent, LiveRetrievalRequest, LiveRetrievalRouter

    router = LiveRetrievalRouter()
    status = router.route(LiveRetrievalRequest(query="Is flight UA123 delayed today?"))
    offers = router.route(
        LiveRetrievalRequest(query="Find a flight from Denver to Boston and buy a ticket")
    )
    local = router.route(
        LiveRetrievalRequest(query="Search this Project's local files for the API decision")
    )
    comparative = router.route(
        LiveRetrievalRequest(query="What is the best website for learning Rust?")
    )
    missing = router.lookup(LiveRetrievalRequest(query="Weather in Denver today"))

    assert status.intent == LiveIntent.CURRENT_STATUS
    assert offers.intent == LiveIntent.PURCHASABLE_OFFER
    assert status.mode == offers.mode == "live_structured"
    assert local.mode == "local_private"
    assert comparative.mode == "standard_research"
    assert missing.completeness == 0
    assert missing.freshness == "unavailable"
    assert missing.errors == [
        "No configured structured provider is available; targeted browsing is required"
    ]
    assert "exact_values" in missing.unavailable_fields
