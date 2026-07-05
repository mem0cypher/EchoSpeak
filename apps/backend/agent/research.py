import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlparse

_RECENT_TERMS = {
    "news",
    "latest",
    "recent",
    "today",
    "update",
    "breaking",
    "headline",
    "war",
    "conflict",
    "crisis",
    "yesterday",
    "this week",
    "tonight",
}

_LIVE_SCORE_TERMS = {
    "score",
    "scores",
    "result",
    "results",
    "who won",
    "winning",
    "live",
    "current score",
}

_SCHEDULE_TERMS = {
    "schedule",
    "next game",
    "next match",
    "upcoming",
    "kickoff",
    "start time",
    "fixture",
    "who plays",
    "playing today",
    "games today",
    "matches today",
    "what games",
    "what matches",
}


@dataclass
class SearchIntent:
    original_request: str
    resolved_request: str
    current_subject: str = ""
    mode: str = "general"
    recency_need: bool = False
    live_score_need: bool = False
    schedule_need: bool = False
    specific_answer_need: bool = False
    current_day_need: bool = False
    ambiguous: bool = False


@dataclass
class SearchCandidate:
    query: str
    reason: str
    confidence: float = 0.5
    tags: list[str] = field(default_factory=list)


@dataclass
class GroundedEvidence:
    title: str
    url: str
    summary: str
    relevance_score: float
    recency_bucket: str = "unknown"
    matched_terms: list[str] = field(default_factory=list)
    rejection_reason: str = ""
    fetched_full_page: bool = False


@dataclass
class GroundedSearchResult:
    chosen_query: str
    candidates: list[SearchCandidate]
    evidence: list[GroundedEvidence]
    rejected_candidates: list[dict[str, Any]]
    condensed_evidence: str
    raw_output: str = ""
    accepted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "chosen_query": self.chosen_query,
            "candidates": [asdict(c) for c in self.candidates],
            "evidence": [asdict(e) for e in self.evidence],
            "rejected_candidates": self.rejected_candidates,
            "condensed_evidence": self.condensed_evidence,
            "raw_output": self.raw_output,
            "accepted": self.accepted,
        }


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def extract_research_query(input_text: str) -> str:
    raw = str(input_text or "").strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            query = parsed.get("query")
            if query is not None:
                return _normalize_text(query)
    except Exception:
        pass

    match = re.search(r"query\s*[:=]\s*['\"]([^'\"]+)['\"]", raw, flags=re.IGNORECASE)
    if match:
        return _normalize_text(match.group(1))
    return _normalize_text(raw)


def _parse_date_value(value: str) -> Optional[datetime]:
    s = str(value or "").strip()
    if not s:
        return None
    low = s.lower()
    match = re.match(r"^(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago\b", low)
    if match:
        n = int(match.group(1))
        unit = match.group(2)
        now = datetime.now(timezone.utc)
        if unit == "minute":
            return now - timedelta(minutes=n)
        if unit == "hour":
            return now - timedelta(hours=n)
        if unit == "day":
            return now - timedelta(days=n)
        if unit == "week":
            return now - timedelta(weeks=n)
        if unit == "month":
            return now - timedelta(days=30 * n)
        if unit == "year":
            return now - timedelta(days=365 * n)

    iso = s.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        pass
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            parsed = datetime.strptime(s, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _classify_recency(published_raw: str) -> tuple[Optional[str], str]:
    dt = _parse_date_value(published_raw)
    if dt is None:
        return None, "unknown"
    now = datetime.now(timezone.utc)
    age = max((now - dt).total_seconds(), 0.0)
    if age <= 72 * 3600:
        bucket = "breaking"
    elif age <= 30 * 24 * 3600:
        bucket = "recent"
    else:
        bucket = "archive"
    return dt.isoformat(), bucket


def _domain(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _infer_mode(query: str) -> str:
    low = str(query or "").strip().lower()
    if not low:
        return "general"
    if any(term in low for term in _RECENT_TERMS):
        return "recent"
    return "general"


def build_search_intent(original_request: str, resolved_request: str = "", current_subject: str = "") -> SearchIntent:
    resolved = _normalize_text(resolved_request or original_request)
    original = _normalize_text(original_request)
    low = resolved.lower()
    recency = any(term in low for term in _RECENT_TERMS) or any(t in low for t in ["right now", "currently", "current"])
    live_score = any(term in low for term in _LIVE_SCORE_TERMS) and any(
        sport in low
        for sport in ["game", "match", "fifa", "world cup", "soccer", "football", "nhl", "nba", "nfl", "mlb", "canada", "morocco"]
    )
    schedule = any(term in low for term in _SCHEDULE_TERMS)
    current_day = any(term in low for term in ["today", "tonight", "right now", "currently", "current"])
    specific_answer = bool(
        live_score
        or schedule
        or (
            current_day
            and any(term in low for term in ["who plays", "what games", "what matches", "events", "available", "odds", "release", "released"])
        )
        or any(term in low for term in ["odds", "availability", "released", "release date", "score", "scores"])
    )
    ambiguous = bool(current_subject and re.search(r"\b(deeper|more|again|that|this|it|continue|go further)\b", original.lower()))
    mode = "recent" if recency else "general"
    if live_score:
        mode = "live_score"
    elif schedule:
        mode = "schedule"
    return SearchIntent(
        original_request=original,
        resolved_request=resolved,
        current_subject=_normalize_text(current_subject),
        mode=mode,
        recency_need=recency,
        live_score_need=live_score,
        schedule_need=schedule,
        specific_answer_need=specific_answer,
        current_day_need=current_day,
        ambiguous=ambiguous,
    )


class SearchGrounder:
    """Deterministic query construction, retry, and evidence condensation before LLM synthesis."""

    def __init__(self, max_candidates: int = 3, relevance_threshold: float = 0.36):
        self.max_candidates = max(1, int(max_candidates or 3))
        self.relevance_threshold = float(relevance_threshold)

    def build_candidates(self, intent: SearchIntent) -> list[SearchCandidate]:
        base = _normalize_text(intent.resolved_request or intent.original_request)
        if intent.ambiguous and intent.current_subject and intent.current_subject.lower() not in base.lower():
            base = f"{base} about {intent.current_subject}"
        base = self._clean_query(base)
        candidates: list[SearchCandidate] = [SearchCandidate(base, "cleaned user intent", 0.72, ["base"])]

        if intent.live_score_need:
            cleaned = re.sub(r"\b(date|schedule|start time|kickoff|kick-off)\b", "", base, flags=re.IGNORECASE)
            cleaned = self._clean_query(cleaned)
            candidates = [
                SearchCandidate(f"{cleaned} live score result today", "live score intent", 0.96, ["live_score", "current"]),
                SearchCandidate(f"{cleaned} current score live updates", "live score fallback", 0.9, ["live_score", "fallback"]),
                *candidates,
            ]
        elif intent.recency_need:
            year = datetime.now().strftime("%Y")
            candidates.append(SearchCandidate(f"{base} latest current {year}", "recency intent", 0.84, ["recent"]))
        elif intent.schedule_need:
            today = datetime.now().strftime("%Y-%m-%d")
            candidates.append(SearchCandidate(f"{base} schedule today or later {today}", "schedule intent", 0.82, ["schedule"]))

        deduped: list[SearchCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            q = self._clean_query(candidate.query)
            key = q.lower()
            if q and key not in seen:
                candidate.query = q
                deduped.append(candidate)
                seen.add(key)
        return deduped[: self.max_candidates]

    def ground(
        self,
        *,
        original_request: str,
        resolved_request: str = "",
        current_subject: str = "",
        execute: Callable[[str], str],
        fetch_url: Optional[Callable[[str], str]] = None,
    ) -> GroundedSearchResult:
        intent = build_search_intent(original_request, resolved_request, current_subject)
        candidates = self.build_candidates(intent)
        rejected: list[dict[str, Any]] = []
        best_query = candidates[0].query if candidates else _normalize_text(resolved_request or original_request)
        best_output = ""
        best_evidence: list[GroundedEvidence] = []
        best_score = -1.0

        for candidate in candidates:
            output = str(execute(candidate.query) or "")
            evidence = self.score_evidence(output, candidate.query, intent)
            if intent.specific_answer_need and fetch_url:
                evidence, output = self._maybe_fetch_full_page(evidence, output, intent, fetch_url)
            top_score = max([e.relevance_score for e in evidence], default=0.0)
            if top_score > best_score:
                best_score = top_score
                best_query = candidate.query
                best_output = output
                best_evidence = evidence
            accepted = self._accept_evidence(evidence, intent)
            if accepted:
                return GroundedSearchResult(
                    chosen_query=candidate.query,
                    candidates=candidates,
                    evidence=evidence,
                    rejected_candidates=rejected,
                    condensed_evidence=self.condense_evidence(evidence, output),
                    raw_output=output,
                    accepted=True,
                )
            rejected.append({"query": candidate.query, "reason": self._rejection_reason(evidence, intent), "score": top_score})

        return GroundedSearchResult(
            chosen_query=best_query,
            candidates=candidates,
            evidence=best_evidence,
            rejected_candidates=rejected,
            condensed_evidence=self.condense_evidence(best_evidence, best_output),
            raw_output=best_output,
            accepted=False,
        )

    def _maybe_fetch_full_page(
        self,
        evidence: list[GroundedEvidence],
        output: str,
        intent: SearchIntent,
        fetch_url: Callable[[str], str],
    ) -> tuple[list[GroundedEvidence], str]:
        if not evidence or self._accept_evidence(evidence, intent):
            return evidence, output
        top = evidence[0]
        if not top.url or self._has_specific_answer_signal(top.summary.lower() + " " + top.title.lower(), intent):
            return evidence, output
        try:
            page_text = _normalize_text(fetch_url(top.url) or "")
        except Exception:
            page_text = ""
        if not page_text:
            return evidence, output
        combined = f"{top.title} {page_text}".lower()
        if not self._has_specific_answer_signal(combined, intent):
            top.rejection_reason = "Snippet and fetched page did not contain the requested specific answer."
            return evidence, output
        boosted = min(1.0, max(top.relevance_score, self.relevance_threshold + 0.12))
        enriched = GroundedEvidence(
            title=top.title,
            url=top.url,
            summary=page_text[:700],
            relevance_score=boosted,
            recency_bucket=top.recency_bucket,
            matched_terms=top.matched_terms,
            rejection_reason="",
            fetched_full_page=True,
        )
        rest = [e for e in evidence[1:]]
        return [enriched, *rest], f"{output.rstrip()}\n\nFetched page text from {top.url}:\n{page_text[:1800]}"

    def score_evidence(self, output: str, query: str, intent: SearchIntent) -> list[GroundedEvidence]:
        items = [_normalize_evidence(item, tool_name="web_search", fallback_query=query, position=i) for i, item in enumerate(_parse_numbered_blocks(output), start=1)]
        if not items and output.strip():
            fallback_summary = _normalize_text(output[:600])
            items = [{
                "title": "Search output",
                "url": "",
                "snippet": fallback_summary,
                "extract": fallback_summary,
                "recency_bucket": "unknown",
                "content": _normalize_text(output[:1200]),
            }]
        evidence: list[GroundedEvidence] = []
        terms = self._intent_terms(intent)
        for item in items:
            hay = " ".join(str(item.get(k) or "") for k in ("title", "summary", "content", "page_title")).lower()
            matched = [t for t in terms if t in hay]
            score = min(1.0, 0.12 * len(matched))
            if intent.live_score_need:
                score += 0.55 if self._has_score_signal(hay) else -0.25
                if any(t in hay for t in ["schedule", "date", "kickoff", "start time"]) and not self._has_score_signal(hay):
                    score -= 0.25
            elif intent.specific_answer_need:
                score += 0.38 if self._has_specific_answer_signal(hay, intent) else -0.22
                if self._looks_like_date_or_nav_only(hay, intent):
                    score -= 0.25
            if intent.recency_need and str(item.get("recency_bucket") or "") in {"breaking", "recent"}:
                score += 0.18
            if item.get("url"):
                score += 0.05
            rejection = "" if score >= self.relevance_threshold else "Evidence did not strongly match the requested intent."
            evidence.append(GroundedEvidence(
                title=str(item.get("title") or "Untitled source"),
                url=str(item.get("url") or ""),
                summary=str(item.get("summary") or item.get("content") or "")[:700],
                relevance_score=max(0.0, min(1.0, score)),
                recency_bucket=str(item.get("recency_bucket") or "unknown"),
                matched_terms=matched[:12],
                rejection_reason=rejection,
            ))
        evidence.sort(key=lambda e: e.relevance_score, reverse=True)
        return evidence[:8]

    def condense_evidence(self, evidence: list[GroundedEvidence], raw_output: str) -> str:
        usable = [e for e in evidence if e.relevance_score >= 0.12]
        if not usable:
            return str(raw_output or "").strip()
        lines = []
        for idx, item in enumerate(usable[:5], start=1):
            source = f" ({item.url})" if item.url else ""
            lines.append(
                f"{idx}. {item.title}{source}\n"
                f"   Relevance: {item.relevance_score:.2f}; Recency: {item.recency_bucket}; Matches: {', '.join(item.matched_terms) or 'none'}\n"
                f"   Evidence: {item.summary}"
            )
        return "\n\n".join(lines).strip()

    def _accept_evidence(self, evidence: list[GroundedEvidence], intent: SearchIntent) -> bool:
        if not evidence:
            return False
        top = evidence[0]
        if intent.live_score_need:
            return top.relevance_score >= self.relevance_threshold and self._has_score_signal(top.summary.lower() + " " + top.title.lower())
        if intent.specific_answer_need:
            hay = top.summary.lower() + " " + top.title.lower()
            return top.relevance_score >= self.relevance_threshold and self._has_specific_answer_signal(hay, intent)
        return top.relevance_score >= self.relevance_threshold or (len(evidence) >= 2 and top.relevance_score >= 0.25)

    def _rejection_reason(self, evidence: list[GroundedEvidence], intent: SearchIntent) -> str:
        if not evidence:
            return "No usable evidence returned."
        if intent.live_score_need:
            return "Evidence did not look like a live/current score result."
        if intent.specific_answer_need:
            return "Evidence did not contain the requested specific current answer."
        return evidence[0].rejection_reason or "Evidence relevance below threshold."

    def _intent_terms(self, intent: SearchIntent) -> list[str]:
        words = re.findall(r"[a-z0-9]{3,}", (intent.resolved_request + " " + intent.current_subject).lower())
        stop = {"what", "when", "where", "which", "with", "about", "please", "search", "deeper", "right", "currently", "today"}
        terms = [w for w in words if w not in stop]
        if intent.live_score_need:
            terms.extend(["score", "live", "result", "current"])
        if intent.recency_need:
            terms.extend(["latest", "recent", "today", "current"])
        if intent.schedule_need:
            terms.extend(["schedule", "game", "match", "today", "time"])
        return list(dict.fromkeys(terms))[:20]

    def _has_score_signal(self, text: str) -> bool:
        return bool(
            any(sig in text for sig in ["score", "final", "live", "result", "full-time", "halftime", "goals"])
            or re.search(r"\b\d{1,2}\s*[-:]\s*\d{1,2}\b", text)
        )

    def _has_specific_answer_signal(self, text: str, intent: SearchIntent) -> bool:
        hay = str(text or "").lower()
        if intent.live_score_need:
            return self._has_score_signal(hay)
        if "odds" in (intent.resolved_request or "").lower():
            return bool(re.search(r"[+-]\d{3,4}\b|\b\d+\.\d{2}\b|\bodds\b.*(?:spread|moneyline|total)", hay))
        if intent.schedule_need or intent.current_day_need:
            has_time = bool(re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|et|pt|ct|mt|utc|edt|pdt)\b", hay))
            has_date = bool(
                re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+20\d{2}\b", hay)
                or re.search(r"\b20\d{2}-\d{2}-\d{2}\b", hay)
            )
            has_matchup = bool(re.search(r"\b(?:vs\.?|versus| at )\b", hay))
            has_event_words = any(word in hay for word in ["play", "plays", "playing", "game", "match", "kickoff", "tipoff", "starts"])
            has_named_result = bool(re.search(r"\b[a-z][a-z .'-]{2,}\s+(?:vs\.?|versus|at)\s+[a-z][a-z .'-]{2,}\b", hay))
            return (has_event_words and (has_time or has_date or has_matchup or has_named_result)) or has_named_result
        if any(term in hay for term in ["available", "released", "launches", "starts", "opens"]):
            return True
        return False

    def _looks_like_date_or_nav_only(self, text: str, intent: SearchIntent) -> bool:
        hay = str(text or "").lower()
        has_date = bool(
            re.search(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday),?\s+[a-z]+\s+\d{1,2},?\s+20\d{2}\b", hay)
            or re.search(r"\b20\d{2}-\d{2}-\d{2}\b", hay)
        )
        nav_words = sum(1 for word in ["schedule", "standings", "fixtures", "results", "teams", "stats", "tickets"] if word in hay)
        return bool((has_date or nav_words >= 3) and not self._has_specific_answer_signal(hay, intent))

    def _clean_query(self, query: str) -> str:
        q = _normalize_text(query)
        q = re.sub(r"\b(can you|please|tell me|what is|what's|search the internet|look up)\b", "", q, flags=re.IGNORECASE)
        return _normalize_text(q)


def _parse_numbered_blocks(output: str) -> list[dict[str, Any]]:
    blocks = re.split(r"\n\s*\n", str(output or "").strip())
    items: list[dict[str, Any]] = []
    for block in blocks:
        lines = [line.rstrip() for line in str(block or "").splitlines() if line.strip()]
        if not lines:
            continue
        title_match = re.match(r"^\d+\.\s*(.*)$", lines[0].strip())
        title = (title_match.group(1) if title_match else lines[0]).strip()
        fields: dict[str, str] = {}
        current_label: Optional[str] = None
        for raw_line in lines[1:]:
            line = raw_line.strip()
            field_match = re.match(r"^(URL|Query|Date|Snippet|Page|Extract|Content|Title):\s*(.*)$", line, flags=re.IGNORECASE)
            if field_match:
                current_label = field_match.group(1).lower()
                fields[current_label] = field_match.group(2).strip()
                continue
            if current_label:
                fields[current_label] = (fields.get(current_label, "") + " " + line).strip()
        items.append({
            "title": title,
            "url": fields.get("url", ""),
            "query": fields.get("query", ""),
            "published_raw": fields.get("date", ""),
            "snippet": fields.get("snippet", ""),
            "page_title": fields.get("page", "") or fields.get("title", ""),
            "extract": fields.get("extract", "") or fields.get("content", ""),
        })
    return items


def _normalize_evidence(item: dict[str, Any], *, tool_name: str, fallback_query: str, position: int) -> dict[str, Any]:
    published_at, recency_bucket = _classify_recency(str(item.get("published_raw") or ""))
    query = _normalize_text(item.get("query") or fallback_query)
    url = _normalize_text(item.get("url"))
    title = _normalize_text(item.get("title")) or "Untitled source"
    snippet = _normalize_text(item.get("snippet"))
    extract = _normalize_text(item.get("extract"))
    page_title = _normalize_text(item.get("page_title"))
    summary = snippet or extract or page_title
    if len(summary) > 600:
        summary = summary[:600].rstrip() + "…"
    content = extract or snippet
    if len(content) > 2000:
        content = content[:2000].rstrip() + "…"
    return {
        "id": f"{tool_name}-{position}-{abs(hash((url, title, query))) % 1000000}",
        "kind": "search_result",
        "position": position,
        "query": query,
        "title": title,
        "url": url,
        "domain": _domain(url),
        "summary": summary,
        "snippet": snippet,
        "content": content,
        "page_title": page_title,
        "published_raw": _normalize_text(item.get("published_raw")),
        "published_at": published_at,
        "recency_bucket": recency_bucket,
    }


def build_research_run(*, run_id: str, tool_name: str, tool_input: str, output: str, at: float) -> Optional[dict[str, Any]]:
    if tool_name != "web_search":
        return None
    query = extract_research_query(tool_input)
    raw = str(output or "").strip()
    if not raw or raw.lower().startswith("search failed") or raw.lower().startswith("no search results"):
        evidence: list[dict[str, Any]] = []
    else:
        evidence = [_normalize_evidence(item, tool_name=tool_name, fallback_query=query, position=index) for index, item in enumerate(_parse_numbered_blocks(raw), start=1)]

    evidence = [item for item in evidence if item.get("title") or item.get("url") or item.get("summary")]
    mode = _infer_mode(query)
    return {
        "id": run_id,
        "tool": tool_name,
        "query": query,
        "at": at,
        "mode": mode,
        "recency_intent": mode == "recent",
        "evidence_count": len(evidence),
        "evidence": evidence,
    }
