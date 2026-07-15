"""Provider-neutral contracts and deterministic routing for live retrieval.

This module deliberately performs no network I/O and owns no credentials.
SearchGrounder remains the production grounded-search owner; configured live
providers can implement :class:`LiveRetrievalAdapter` and be registered later.
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from pydantic import BaseModel, Field

from .research_artifacts import ResearchMode


class LiveDomain(str, Enum):
    SPORTS = "sports"
    FINANCE_CRYPTO = "finance_crypto"
    WEATHER = "weather"
    FLIGHTS_AIRPORTS = "flights_airports"
    SCHEDULES_EVENTS = "schedules_events"
    MAPS_LOCATIONS = "maps_locations"
    PRODUCTS_AVAILABILITY = "products_availability"
    TECHNICAL_DOCUMENTATION = "technical_documentation"
    ACADEMIC = "academic"
    NEWS_PUBLIC_CURRENT = "news_public_current"
    UNKNOWN = "unknown"


class LiveIntent(str, Enum):
    CURRENT_STATUS = "current_status"
    CURRENT_VALUE = "current_value"
    FORECAST = "forecast"
    SCHEDULE = "schedule"
    PURCHASABLE_OFFER = "purchasable_offer"
    COMPARATIVE_RESEARCH = "comparative_research"
    DOCUMENT_LOOKUP = "document_lookup"
    LOCAL_PRIVATE = "local_private"
    GENERAL_LOOKUP = "general_lookup"


class ResolvedEntity(BaseModel):
    id: str = ""
    name: str
    entity_type: str = "unknown"
    aliases: List[str] = Field(default_factory=list)
    resolution_confidence: float = Field(default=1.0, ge=0, le=1)


class ExactValue(BaseModel):
    name: str
    value: Any
    unit: str = ""
    currency: str = ""
    event_type: str = ""
    status_type: str = ""
    effective_at: Optional[float] = None


class LiveProvenance(BaseModel):
    provider: str
    source_identifier: str
    url: str = ""
    retrieved_at: float = Field(default_factory=time.time)
    notes: Dict[str, Any] = Field(default_factory=dict)


class StructuredLiveResult(BaseModel):
    """Stable result shape shared by every configured live-data provider."""

    schema_version: int = 1
    domain: LiveDomain
    resolved_entities: List[ResolvedEntity] = Field(default_factory=list)
    exact_values: List[ExactValue] = Field(default_factory=list)
    units: Dict[str, str] = Field(default_factory=dict)
    currencies: Dict[str, str] = Field(default_factory=dict)
    event_type: str = ""
    status_type: str = ""
    provider_timestamp: Optional[float] = None
    retrieval_timestamp: float = Field(default_factory=time.time)
    freshness: str = "unknown"
    provider: str
    source_identifier: str = ""
    provenance: List[LiveProvenance] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    completeness: float = Field(default=0.0, ge=0, le=1)
    contradictions: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    unavailable_fields: List[str] = Field(default_factory=list)


class LiveRetrievalRequest(BaseModel):
    query: str = Field(min_length=1)
    domain: Optional[LiveDomain] = None
    project_id: str = ""
    session_id: str = ""
    expected_entities: List[str] = Field(default_factory=list)
    expected_unit: str = ""
    expected_currency: str = ""


class LiveRoute(BaseModel):
    mode: ResearchMode
    domain: LiveDomain = LiveDomain.UNKNOWN
    intent: LiveIntent = LiveIntent.GENERAL_LOOKUP
    requires_freshness: bool = False
    adapter_name: str = ""
    fallback_browsing_required: bool = False
    reason: str = ""


class LiveRetrievalAdapter(ABC):
    """Replaceable provider contract. Implementations return the common type."""

    name: str
    domains: Set[LiveDomain]
    priority: int = 100

    def is_available(self) -> bool:
        """Return false when configuration is absent; do not raise for that case."""
        return True

    def supports(self, domain: LiveDomain) -> bool:
        return domain in self.domains

    @abstractmethod
    def lookup(self, request: LiveRetrievalRequest) -> StructuredLiveResult:
        raise NotImplementedError


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", str(query or "").strip().casefold())


class FixtureLiveAdapter(LiveRetrievalAdapter):
    """Credential-free, deterministic adapter for evaluations and development."""

    def __init__(
        self,
        fixtures: Mapping[str, StructuredLiveResult | Mapping[str, Any]],
        *,
        name: str = "deterministic_fixture",
        domains: Optional[Iterable[LiveDomain]] = None,
        available: bool = True,
        priority: int = 0,
    ) -> None:
        self.name = name
        self.priority = priority
        self._available = available
        self._fixtures: Dict[str, StructuredLiveResult] = {
            _normalize_query(query): (
                result
                if isinstance(result, StructuredLiveResult)
                else StructuredLiveResult.model_validate(result)
            )
            for query, result in fixtures.items()
        }
        inferred = {result.domain for result in self._fixtures.values()}
        self.domains = set(domains or inferred)

    def is_available(self) -> bool:
        return self._available

    def lookup(self, request: LiveRetrievalRequest) -> StructuredLiveResult:
        normalized = _normalize_query(request.query)
        result = self._fixtures.get(normalized)
        if result is None:
            domain = request.domain or LiveDomain.UNKNOWN
            return unavailable_live_result(
                domain=domain,
                provider=self.name,
                error="No deterministic fixture matches this query",
            )
        return result.model_copy(deep=True)


_DOMAIN_PATTERNS: Sequence[tuple[LiveDomain, re.Pattern[str]]] = (
    (LiveDomain.SPORTS, re.compile(r"\b(score|scorer|match|game|standings|nfl|nba|nhl|mlb|soccer|football)\b")),
    (LiveDomain.FINANCE_CRYPTO, re.compile(r"\b(price|quote|market cap|stock|share|bitcoin|btc|ethereum|eth|crypto|forex)\b")),
    (LiveDomain.WEATHER, re.compile(r"\b(weather|temperature|forecast|rain|snow|wind|humidity|air quality)\b")),
    (LiveDomain.FLIGHTS_AIRPORTS, re.compile(r"\b(flight|airport|airline|departure|arrival|gate|terminal)\b")),
    (LiveDomain.SCHEDULES_EVENTS, re.compile(r"\b(schedule|calendar|event|concert|showtime|starts? at|opening hours)\b")),
    (LiveDomain.MAPS_LOCATIONS, re.compile(r"\b(map|directions|distance|route to|located|location|near me|address)\b")),
    (LiveDomain.PRODUCTS_AVAILABILITY, re.compile(r"\b(in stock|availability|available|buy|product|shipping|delivery)\b")),
    (LiveDomain.TECHNICAL_DOCUMENTATION, re.compile(r"\b(api|sdk|documentation|docs|release notes|version|library|framework)\b")),
    (LiveDomain.ACADEMIC, re.compile(r"\b(paper|study|journal|doi|research article|citation|arxiv|academic)\b")),
    (LiveDomain.NEWS_PUBLIC_CURRENT, re.compile(r"\b(news|breaking|current events|election|public record|latest announcement)\b")),
)


def classify_live_domain(query: str) -> LiveDomain:
    normalized = _normalize_query(query)
    for domain, pattern in _DOMAIN_PATTERNS:
        if pattern.search(normalized):
            return domain
    return LiveDomain.UNKNOWN


def classify_live_intent(query: str, domain: LiveDomain) -> LiveIntent:
    normalized = _normalize_query(query)
    if re.search(r"\b(this project|project files?|local files?|private notes?|my workspace)\b", normalized):
        return LiveIntent.LOCAL_PRIVATE
    if re.search(r"\b(best|compare|comparison|versus|vs\.?|recommend)\b", normalized):
        return LiveIntent.COMPARATIVE_RESEARCH
    if domain == LiveDomain.FLIGHTS_AIRPORTS:
        if re.search(r"\b(book|buy|fare|ticket|offer|cheapest|from .+ to)\b", normalized):
            return LiveIntent.PURCHASABLE_OFFER
        if re.search(r"\b(status|delayed|cancelled|canceled|gate|landed|departed|arriv)\w*\b", normalized):
            return LiveIntent.CURRENT_STATUS
        return LiveIntent.SCHEDULE
    if domain == LiveDomain.WEATHER:
        return LiveIntent.FORECAST if re.search(r"\b(forecast|tomorrow|week|later)\b", normalized) else LiveIntent.CURRENT_STATUS
    if domain == LiveDomain.FINANCE_CRYPTO:
        return LiveIntent.CURRENT_VALUE
    if domain == LiveDomain.SPORTS:
        return LiveIntent.CURRENT_STATUS
    if domain in {LiveDomain.SCHEDULES_EVENTS, LiveDomain.PRODUCTS_AVAILABILITY}:
        return LiveIntent.SCHEDULE if domain == LiveDomain.SCHEDULES_EVENTS else LiveIntent.CURRENT_STATUS
    if domain in {LiveDomain.TECHNICAL_DOCUMENTATION, LiveDomain.ACADEMIC}:
        return LiveIntent.DOCUMENT_LOOKUP
    return LiveIntent.GENERAL_LOOKUP


def unavailable_live_result(
    *, domain: LiveDomain, provider: str = "unavailable", error: str
) -> StructuredLiveResult:
    return StructuredLiveResult(
        domain=domain,
        provider=provider,
        freshness="unavailable",
        confidence=0.0,
        completeness=0.0,
        errors=[error],
        unavailable_fields=[
            "resolved_entities",
            "exact_values",
            "provider_timestamp",
            "source_identifier",
            "provenance",
        ],
    )


def structured_sports_result(result: Any, *, query: str) -> StructuredLiveResult:
    """Project the existing sports provider into the common live-result type."""
    ok = bool(getattr(result, "ok", False))
    provider = str(getattr(result, "provider", "sports_live") or "sports_live")
    sport_key = str(getattr(result, "sport_key", "") or "")
    summary = str(getattr(result, "summary", "") or "").strip()
    error = str(getattr(result, "error", "") or "").strip()
    timestamp = getattr(result, "retrieved_at", None)
    try:
        provider_timestamp = float(timestamp) if timestamp is not None else None
    except (TypeError, ValueError):
        provider_timestamp = None
    return StructuredLiveResult(
        domain=LiveDomain.SPORTS,
        resolved_entities=[
            ResolvedEntity(name=sport_key, entity_type="sport", resolution_confidence=1.0)
        ] if sport_key else [],
        exact_values=[
            ExactValue(
                name="provider_result",
                value=summary,
                event_type=str(getattr(result, "mode", "") or "sports_status"),
                status_type="available" if ok else "unavailable",
                effective_at=provider_timestamp,
            )
        ] if summary else [],
        event_type=str(getattr(result, "mode", "") or "sports_status"),
        status_type="available" if ok else "unavailable",
        provider_timestamp=provider_timestamp,
        freshness="live" if ok else "unavailable",
        provider=provider,
        source_identifier=str(getattr(result, "source_identifier", "") or query),
        provenance=[
            LiveProvenance(
                provider=provider,
                source_identifier=str(getattr(result, "source_identifier", "") or query),
                retrieved_at=provider_timestamp or time.time(),
            )
        ] if ok else [],
        confidence=0.9 if ok else 0.0,
        completeness=1.0 if ok and summary else 0.0,
        errors=[] if ok else [error or "Sports provider returned no result"],
        unavailable_fields=[] if ok else ["exact_values", "provenance"],
    )


class LiveRetrievalRouter:
    """Deterministically selects a research mode and a configured adapter."""

    def __init__(self, adapters: Iterable[LiveRetrievalAdapter] = ()) -> None:
        self._adapters: List[LiveRetrievalAdapter] = []
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: LiveRetrievalAdapter) -> None:
        if any(existing.name == adapter.name for existing in self._adapters):
            raise ValueError(f"Live adapter already registered: {adapter.name}")
        self._adapters.append(adapter)
        self._adapters.sort(key=lambda item: (item.priority, item.name))

    def _select_adapter(self, domain: LiveDomain) -> Optional[LiveRetrievalAdapter]:
        for adapter in self._adapters:
            if adapter.supports(domain) and adapter.is_available():
                return adapter
        return None

    def route(self, request: LiveRetrievalRequest) -> LiveRoute:
        query = _normalize_query(request.query)
        domain = request.domain or classify_live_domain(query)
        intent = classify_live_intent(query, domain)
        current_terms = bool(re.search(r"\b(current|currently|right now|today|tonight|live|latest|now)\b", query))
        inherently_live = domain in {
            LiveDomain.SPORTS,
            LiveDomain.FINANCE_CRYPTO,
            LiveDomain.WEATHER,
            LiveDomain.FLIGHTS_AIRPORTS,
            LiveDomain.SCHEDULES_EVENTS,
            LiveDomain.PRODUCTS_AVAILABILITY,
        }
        complex_terms = bool(re.search(r"\b(comprehensive|deep dive|investigate|multi[- ]source|systematic)\b", query))

        if intent == LiveIntent.LOCAL_PRIVATE:
            mode: ResearchMode = "local_private"
            reason = "Request explicitly targets local or private Project context"
        elif complex_terms:
            mode = "deep_research"
            reason = "Request requires bounded multi-stage investigation"
        elif intent == LiveIntent.COMPARATIVE_RESEARCH:
            mode = "standard_research"
            reason = "Comparative requests require source evaluation and synthesis"
        elif inherently_live or current_terms:
            mode = "live_structured"
            reason = "Request requires a current exact structured value or status"
        elif domain in {LiveDomain.ACADEMIC, LiveDomain.NEWS_PUBLIC_CURRENT}:
            mode = "standard_research"
            reason = "Request requires evidence-oriented source retrieval"
        else:
            mode = "quick_lookup"
            reason = "Request is a bounded lookup without deep or live requirements"

        adapter = self._select_adapter(domain) if mode == "live_structured" else None
        return LiveRoute(
            mode=mode,
            domain=domain,
            intent=intent,
            requires_freshness=mode == "live_structured",
            adapter_name=adapter.name if adapter else "",
            fallback_browsing_required=mode == "live_structured" and adapter is None,
            reason=reason,
        )

    def lookup(self, request: LiveRetrievalRequest) -> StructuredLiveResult:
        route = self.route(request)
        if route.mode != "live_structured":
            return unavailable_live_result(
                domain=route.domain,
                error=f"Router selected {route.mode}; structured live lookup was not executed",
            )
        adapter = self._select_adapter(route.domain)
        if adapter is None:
            return unavailable_live_result(
                domain=route.domain,
                error="No configured structured provider is available; targeted browsing is required",
            )
        routed_request = request.model_copy(update={"domain": route.domain})
        try:
            result = adapter.lookup(routed_request)
        except Exception as exc:
            return unavailable_live_result(
                domain=route.domain,
                provider=adapter.name,
                error=f"Structured provider failed: {exc}",
            )
        if result.domain != route.domain:
            return unavailable_live_result(
                domain=route.domain,
                provider=adapter.name,
                error=f"Structured provider returned the wrong domain: {result.domain.value}",
            )
        return result
