"""Deterministic Echo Search v1 workflow.

QUARANTINED from production chat routing (2026-07).
Live research uses SearchGrounder + web_search via agent.core.
This module remains for tests and a future single-stack migration —
do not wire it into process_query / ModeController without removing
SearchGrounder dual paths first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from agent.evidence_store import Evidence, PageCache, extract_evidence_from_text, fetch_page_text
from agent.search_plan import PlannedQuery, SearchMode, SearchPlan, build_search_plan
from agent.search_provider import NormalizedSearchResult, SearXNGSearchProvider


@dataclass(frozen=True)
class GapAnalysis:
    enough_evidence: bool
    need_more_queries: bool = False
    need_official_source: bool = False
    need_newer_source: bool = False
    conflicting_evidence: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EchoSearchResult:
    plan: SearchPlan
    results: list[NormalizedSearchResult]
    evidence: list[Evidence]
    gap: GapAnalysis
    answer: str
    rounds: int


def dedupe_results(results: list[NormalizedSearchResult], *, max_per_domain: int = 2) -> list[NormalizedSearchResult]:
    seen_urls = set()
    domains: dict[str, int] = {}
    out: list[NormalizedSearchResult] = []
    for item in results:
        key = item.url.lower().rstrip("/") or item.title.lower()
        if not key or key in seen_urls:
            continue
        host = (urlparse(item.url).netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host and domains.get(host, 0) >= max_per_domain:
            continue
        seen_urls.add(key)
        if host:
            domains[host] = domains.get(host, 0) + 1
        out.append(item)
    return out


def analyze_gaps(plan: SearchPlan, evidence: list[Evidence], results: list[NormalizedSearchResult]) -> GapAnalysis:
    reasons: list[str] = []
    supported_parts = {ev.supports_answer_part for ev in evidence}
    missing_parts = [part for part in plan.must_answer if part not in supported_parts]
    official_expected = any(q.expected_source_type == "official" for q in plan.queries)
    has_official = any("official" in (ev.source_title + " " + ev.source_url).lower() for ev in evidence) or any(
        q.expected_source_type == "official" and any(q.query.lower().split()[0] in (r.title + r.url).lower() for r in results)
        for q in plan.queries
        if q.query
    )
    if missing_parts:
        reasons.append("missing answer parts: " + "; ".join(missing_parts[:3]))
    if official_expected and not has_official:
        reasons.append("official source still needed")
    if plan.recency_required and not evidence:
        reasons.append("newer source still needed")
    conflicting = detect_conflicts(evidence)
    if conflicting:
        reasons.append("conflicting evidence detected")
    enough = bool(evidence) and not missing_parts and not (official_expected and not has_official) and not conflicting
    return GapAnalysis(
        enough_evidence=enough,
        need_more_queries=not enough,
        need_official_source=official_expected and not has_official,
        need_newer_source=plan.recency_required and not evidence,
        conflicting_evidence=conflicting,
        reasons=reasons,
    )


def detect_conflicts(evidence: list[Evidence]) -> bool:
    by_part: dict[str, set[str]] = {}
    for ev in evidence:
        nums = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", ev.quote_or_summary))
        if nums:
            by_part.setdefault(ev.supports_answer_part, set()).update(nums)
    return any(len(nums) > 3 for nums in by_part.values())


def build_gap_queries(plan: SearchPlan, gap: GapAnalysis) -> list[PlannedQuery]:
    queries: list[PlannedQuery] = []
    base = " ".join([*plan.entities, *plan.must_answer[:2]]).strip() or plan.user_intent
    if gap.need_official_source:
        queries.append(PlannedQuery(f"{base} official documentation", "Fill official-source gap", "official"))
    if gap.need_newer_source:
        queries.append(PlannedQuery(f"{base} latest update", "Fill recency gap", "news"))
    if gap.need_more_queries:
        queries.append(PlannedQuery(f"{base} evidence", "Fill missing evidence", "general"))
    return queries[:8]


def synthesize_answer(plan: SearchPlan, evidence: list[Evidence], gap: GapAnalysis) -> str:
    if not evidence:
        return "I could not find enough evidence to answer confidently."
    lines = []
    if gap.enough_evidence:
        lines.append("Based on the retrieved evidence:")
    else:
        lines.append("Evidence is incomplete, so treat this as a cautious synthesis:")
    for i, ev in enumerate(evidence[:8], 1):
        lines.append(f"{i}. {ev.claim} [{ev.source_url}]")
    if gap.reasons:
        lines.append("Remaining gaps: " + "; ".join(gap.reasons))
    return "\n".join(lines)


class EchoSearchWorkflow:
    def __init__(
        self,
        *,
        provider: SearXNGSearchProvider,
        cache: PageCache,
        local_root: str | Path | None = None,
        max_rounds: int = 2,
        max_deep_queries_per_round: int = 8,
    ):
        self.provider = provider
        self.cache = cache
        self.local_root = Path(local_root).resolve() if local_root else None
        self.max_rounds = max(1, min(int(max_rounds), 2))
        self.max_deep_queries_per_round = max(1, min(int(max_deep_queries_per_round), 8))

    def run(self, *, user_request: str, llm: Callable[[str], str] | object) -> EchoSearchResult:
        plan = build_search_plan(user_request, llm)
        return self.run_plan(plan)

    def run_plan(self, plan: SearchPlan) -> EchoSearchResult:
        rounds = 0
        all_results: list[NormalizedSearchResult] = []
        all_evidence: list[Evidence] = []
        queries = list(plan.queries)
        if plan.mode == SearchMode.LOCAL_FIRST_SEARCH:
            local_evidence = self._search_local_first(plan)
            all_evidence.extend(local_evidence)
            local_gap = analyze_gaps(plan, all_evidence, all_results)
            if local_gap.enough_evidence and not plan.recency_required:
                return EchoSearchResult(
                    plan=plan,
                    results=[],
                    evidence=all_evidence,
                    gap=local_gap,
                    answer=synthesize_answer(plan, all_evidence, local_gap),
                    rounds=0,
                )
        while queries and rounds < self.max_rounds:
            rounds += 1
            if plan.mode == SearchMode.QUICK_SEARCH and rounds > 1:
                break
            batch = queries[: self.max_deep_queries_per_round]
            limit = 5 if plan.mode == SearchMode.QUICK_SEARCH else 10
            round_results: list[NormalizedSearchResult] = []
            for planned in batch:
                round_results.extend(self.provider.search(planned.query, limit=limit))
            selected = dedupe_results(round_results)
            all_results = dedupe_results([*all_results, *selected])
            if plan.mode == SearchMode.QUICK_SEARCH:
                for result in selected[:5]:
                    if result.snippet:
                        all_evidence.extend(
                            extract_evidence_from_text(
                                text=result.snippet,
                                source_url=result.url,
                                source_title=result.title,
                                must_answer=plan.must_answer,
                                entities=plan.entities,
                                max_items=1,
                            )
                        )
                gap = analyze_gaps(plan, all_evidence, all_results)
                break
            fetch_limit = 8
            for result in selected[:fetch_limit]:
                text = fetch_page_text(result.url, self.cache)
                if not text:
                    text = result.snippet
                all_evidence.extend(
                    extract_evidence_from_text(
                        text=text,
                        source_url=result.url,
                        source_title=result.title,
                        must_answer=plan.must_answer,
                        entities=plan.entities,
                    )
                )
            gap = analyze_gaps(plan, all_evidence, all_results)
            if gap.enough_evidence:
                break
            if rounds >= self.max_rounds:
                break
            queries = build_gap_queries(plan, gap)
        else:
            gap = analyze_gaps(plan, all_evidence, all_results)
        return EchoSearchResult(
            plan=plan,
            results=all_results,
            evidence=all_evidence,
            gap=gap,
            answer=synthesize_answer(plan, all_evidence, gap),
            rounds=rounds,
        )
    def _search_local_first(self, plan: SearchPlan) -> list[Evidence]:
        if not self.local_root or not self.local_root.exists():
            return []
        terms = [t.lower() for t in [*plan.entities, *plan.must_answer] if len(str(t).strip()) >= 3]
        if not terms:
            return []
        allowed_ext = {".md", ".txt", ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml"}
        ignored_dirs = {".git", "node_modules", ".pnpm-store", "__pycache__", ".pytest_cache", "data"}
        out: list[Evidence] = []
        for path in self.local_root.rglob("*"):
            if len(out) >= 8:
                break
            if not path.is_file() or path.suffix.lower() not in allowed_ext:
                continue
            if any(part in ignored_dirs for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")[:20000]
            except Exception:
                continue
            if not any(term in text.lower() for term in terms):
                continue
            rel = str(path.relative_to(self.local_root))
            out.extend(
                extract_evidence_from_text(
                    text=text,
                    source_url=f"file://{rel}",
                    source_title=rel,
                    must_answer=plan.must_answer,
                    entities=plan.entities,
                    max_items=2,
                )
            )
        return out[:8]


