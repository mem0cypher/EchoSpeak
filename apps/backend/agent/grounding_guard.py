"""Post-response grounding guard for EchoSpeak.

Detects when the model invents specific facts (dates, scores, events, quotes)
that don't appear in any source material (conversation history, tool results,
retrieved memory). Strips or hedges ungrounded claims.

This is a regex-based post-hoc validator, NOT an LLM call. It runs fast
and catches obvious confabulations without being so strict it flags
legitimate inference or general knowledge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from loguru import logger


@dataclass
class GroundingResult:
    """Result of a grounding check."""
    is_grounded: bool = True
    ungrounded_claims: List[str] = field(default_factory=list)
    grounded_claims: List[str] = field(default_factory=list)
    claim_provenance: Dict[str, str] = field(default_factory=dict)


# ── Claim extraction patterns ──

# Specific date/time claims: "on July 15", "starts at 8pm", "in 2025"
_DATE_CLAIMS = re.compile(
    r"\b(?:on|starts?|begins?|ends?|scheduled for|set for|held on|"
    r"from|until|by)\s+"
    r"(?:(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+\d{1,2}(?:,?\s+\d{4})?|\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?"
    r"(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec))",
    re.IGNORECASE,
)

# Time claims: "at 8pm", "at 3:30"
_TIME_CLAIMS = re.compile(
    r"\b(?:at|starts? at|begins? at|kicks? off at)\s+\d{1,2}(?::\d{2})?\s*"
    r"(?:am|pm|AM|PM|a\.m\.|p\.m\.)",
    re.IGNORECASE,
)

# Score claims: "3-2", "won 105-98", "scored 4 goals"
_SCORE_CLAIMS = re.compile(
    r"\b(?:won|lost|beat|defeated|tied|drew|scored|finished|winning|losing|victory|defeat|win|loss|final(?:ly)?)\s+"
    r"(?:\d{1,3}\s*[-–]\s*\d{1,3}|\d{1,3}\s+(?:goals?|points?|runs?|sets?))",
    re.IGNORECASE,
)

# Standalone score patterns: "the score was 3-2"
_SCORE_STANDALONE = re.compile(
    r"\b(?:score\s+(?:was|is|ended)\s+|final\s+score\s*(?::|was|is)?\s*)"
    r"\d{1,3}\s*[-–]\s*\d{1,3}",
    re.IGNORECASE,
)

# Named event claims that are very specific: "the training camp", "the combine"
_EVENT_CLAIMS = re.compile(
    r"\b(?:the|their|its)?\s*"
    r"(?:training camps?|spring training|pre-?season|combine|draft|"
    r"trade deadline|all[- ]star|pro bowl|"
    r"opening ceremony|closing ceremony|inauguration|summit|"
    r"annual conference|shareholder meeting)\b",
    re.IGNORECASE,
)

# Statistical claims: "averaging 25 points", "batting .300"
_STAT_CLAIMS = re.compile(
    r"\b(?:averaging|scored|recorded|posted|hitting|batting|shooting)\s+"
    r"(?:\d{1,4}(?:\.\d{1,3})?\s*(?:points?|goals?|assists?|rebounds?|"
    r"yards?|touchdowns?|home runs?|strikeouts?|saves?|percent|%)|"
    r"\.\d{3})\b",
    re.IGNORECASE,
)

# Numeric rate / odds claims: "1 in 255", "approximately 1 in 8192", "0.012%"
_ODDS_RATE_CLAIMS = re.compile(
    r"\b(?:(?:approximately|about|roughly|around|nearly|exactly)\s+)?"
    r"(?:1\s*in\s*[\d,]+(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s*%|"
    r"odds?\s+(?:of\s+|are\s+|is\s+)?(?:1\s*in\s*)?[\d,]+|"
    r"(?:shiny\s+)?(?:rate|chance|probability)\s+(?:of\s+|is\s+|are\s+)?"
    r"(?:about\s+|approximately\s+|roughly\s+)?(?:1\s*in\s*[\d,]+|[\d.]+%))\b",
    re.IGNORECASE,
)


def extract_factual_claims(text: str) -> List[str]:
    """Extract specific factual assertions from response text.

    Only extracts claims that are precise enough to verify:
    dates, scores, statistics, named events, odds/rates. General statements
    like "they're a good team" are NOT extracted.
    """
    claims: List[str] = []
    for pattern in (_DATE_CLAIMS, _TIME_CLAIMS, _SCORE_CLAIMS,
                    _SCORE_STANDALONE, _EVENT_CLAIMS, _STAT_CLAIMS,
                    _ODDS_RATE_CLAIMS):
        for m in pattern.finditer(text):
            claim = m.group(0).strip()
            if claim and len(claim) > 4:
                claims.append(claim)
    return claims


def _normalize_for_match(text: str) -> str:
    """Normalize text for fuzzy substring matching."""
    return re.sub(r"\s+", " ", text.lower().strip())


def _claim_in_sources(claim: str, sources_normalized: List[str]) -> bool:
    """Check if a claim (or close variant) appears in any source text."""
    claim_norm = _normalize_for_match(claim)
    if not claim_norm or len(claim_norm) < 4:
        return True  # too short to verify

    # Direct check for score patterns (e.g. 105-98)
    score_m = re.search(r"(\d+)\s*[-–]\s*(\d+)", claim_norm)
    if score_m:
        score_pat = f"{score_m.group(1)}-{score_m.group(2)}"
        for source in sources_normalized:
            src_norm = re.sub(r"(\d+)\s*[-–]\s*(\d+)", r"\1-\2", source)
            if score_pat in src_norm:
                return True

    # Extract the key numbers/dates from the claim
    numbers = re.findall(r"\d+", claim_norm)
    # Named entities (proper nouns / event names)
    named = re.findall(r"[a-z]{3,}", claim_norm)

    for source in sources_normalized:
        # Direct substring match
        if claim_norm in source:
            return True
        # All numbers from the claim appear in the same source
        if numbers and all(n in source for n in numbers):
            # And at least one key word matches too
            if any(w in source for w in named if w not in {
                "the", "their", "its", "was", "won", "lost", "beat",
                "scored", "at", "on", "from", "starts", "begins",
            }):
                return True
    return False


def check_grounding(
    response: str,
    sources: List[str],
    *,
    user_constraints: Optional[List[str]] = None,
    source_records: Optional[List[Dict[str, str]]] = None,
) -> GroundingResult:
    """Check if factual claims in the response are grounded in source material.

    Args:
        response: The model's response text
        sources: List of source texts (conversation history, tool results, memory)

    Returns:
        GroundingResult with grounded/ungrounded claim lists
    """
    claims = extract_factual_claims(response)
    if not claims:
        return GroundingResult(is_grounded=True)

    typed_sources: List[tuple[str, str]] = [
        ("verified_tool_outcome", _normalize_for_match(s)) for s in sources if s
    ]
    for record in source_records or []:
        provenance = str(record.get("provenance") or "").strip()
        content = str(record.get("content") or "").strip()
        if provenance in {
            "user_input", "authorized_memory", "project_context",
            "verified_tool_outcome", "assistant_inference",
        } and content:
            typed_sources.append((provenance, _normalize_for_match(content)))
    constraints_normalized = [_normalize_for_match(s) for s in (user_constraints or []) if s]

    result = GroundingResult()
    for claim in claims:
        claim_norm = _normalize_for_match(claim)
        # Repeating an exact user-supplied constraint is not a new assistant
        # factual assertion. It remains tagged as user_input, not verified evidence.
        if any(claim_norm in item for item in constraints_normalized):
            result.grounded_claims.append(claim)
            result.claim_provenance[claim] = "user_input"
        else:
            matched_provenance = next(
                (
                    provenance for provenance, content in typed_sources
                    if provenance != "assistant_inference" and _claim_in_sources(claim, [content])
                ),
                "",
            )
            if matched_provenance:
                result.grounded_claims.append(claim)
                result.claim_provenance[claim] = matched_provenance
            else:
                result.ungrounded_claims.append(claim)
                result.claim_provenance[claim] = "assistant_inference"

    result.is_grounded = len(result.ungrounded_claims) == 0
    return result


def apply_grounding_guard(
    response: str,
    sources: List[str],
    *,
    user_constraints: Optional[List[str]] = None,
    source_records: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Main entry point: validate and optionally modify the response.

    If ungrounded specific claims are detected, appends a caveat rather
    than aggressively stripping content (which could break sentence flow).
    Only flags claims that are very specific AND completely absent from sources.

    Args:
        response: The model's response text
        sources: List of source texts to check against

    Returns:
        The response, potentially with a grounding caveat appended
    """
    if not response or not response.strip():
        return response

    result = check_grounding(
        response,
        sources,
        user_constraints=user_constraints,
        source_records=source_records,
    )

    if result.is_grounded:
        return response

    # Log the violation for debugging
    logger.warning(
        "Grounding guard: {} ungrounded claim(s) detected: {}",
        len(result.ungrounded_claims),
        result.ungrounded_claims[:3],
    )

    # An unsupported named event is a topic drift, not a harmless caveat. Drop
    # the affected sentence so a stale sports thread cannot become a made-up
    # explanation for an unrelated request (for example a weather lookup).
    unsupported_events = [claim for claim in result.ungrounded_claims if _EVENT_CLAIMS.search(claim)]
    if unsupported_events:
        sentences = re.split(r"(?<=[.!?])\s+", response)
        kept = [
            sentence for sentence in sentences
            if not any(re.search(re.escape(claim), sentence, flags=re.IGNORECASE) for claim in unsupported_events)
        ]
        cleaned = " ".join(kept).strip()
        if cleaned:
            return cleaned
        return "I don't have evidence for that specific event, so I won't assume it applies here."

    # Numeric odds/rates without evidence: do not present the invented figure.
    # Safer to withhold the number than to state it and append a caveat.
    odds_claims = [
        claim for claim in result.ungrounded_claims if _ODDS_RATE_CLAIMS.search(claim)
    ]
    if odds_claims:
        sentences = re.split(r"(?<=[.!?])\s+", response)
        kept = []
        for sentence in sentences:
            if any(
                re.search(re.escape(claim), sentence, flags=re.IGNORECASE)
                for claim in odds_claims
            ):
                continue
            kept.append(sentence)
        cleaned = " ".join(kept).strip()
        offer = (
            "I don't have a verified source for that exact figure, so I won't invent one. "
            "Want me to search and confirm the precise number?"
        )
        if cleaned:
            return f"{cleaned} {offer}".strip()
        return offer

    # For responses with many ungrounded claims (likely full hallucination),
    # add a clear caveat.
    if len(result.ungrounded_claims) >= 3:
        response = response.rstrip()
        response += (
            "\n\n(Heads up, some of those specific details might not be accurate. "
            "Want me to search and double-check?)"
        )
    # 1-2 non-odds claims: log only (existing contract). Odds/rates already
    # received an explicit uncertainty caveat above.

    return response
