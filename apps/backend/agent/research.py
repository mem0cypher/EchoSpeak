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
    "who is playing",
    "who's playing",
    "who playing",
    "playing today",
    "playing tomorrow",
    "playing tonight",
    "games today",
    "games tomorrow",
    "games tonight",
    "matches today",
    "matches tomorrow",
    "matches tonight",
    "what games",
    "what matches",
    "on tomorrow",
    "for tomorrow",
}

# Common STT / typo variants → canonical (sports & countries). Applied before search.
_SPELLING_FIXES = {
    "maracco": "morocco",
    "morroco": "morocco",
    "moroco": "morocco",
    "marocco": "morocco",
    "cananda": "canada",
    "canadah": "canada",
    "edmontom": "edmonton",
    "edminton": "edmonton",
    "oilors": "oilers",
    "oilres": "oilers",
    "flams": "flames",
    "canuks": "canucks",
    "portugul": "portugal",
    "portugual": "portugal",
    "spane": "spain",
    "brazile": "brazil",
    "argintina": "argentina",
    "argentia": "argentina",
    "wordlcup": "world cup",
    "worldcup": "world cup",
    # Common speech/typo day words (must land before relative-day labels)
    "tommrrow": "tomorrow",
    "tommorow": "tomorrow",
    "tommorrow": "tomorrow",
    "tomorow": "tomorrow",
    "tomorro": "tomorrow",
    "todya": "today",
}

_WEATHER_TERMS = {
    "weather",
    "forecast",
    "temperature",
    "temp",
    "humidity",
    "precipitation",
    "rain",
    "snow",
    "wind chill",
    "feels like",
    "high of",
    "low of",
    "°c",
    "°f",
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
    weather_need: bool = False
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
                return normalize_web_search_query(_normalize_text(query))
    except Exception:
        pass

    match = re.search(r"query\s*[:=]\s*['\"]([^'\"]+)['\"]", raw, flags=re.IGNORECASE)
    if match:
        return normalize_web_search_query(_normalize_text(match.group(1)))
    return normalize_web_search_query(_normalize_text(raw))


# Social / chat filler that must never ship to Tavily as the search string.
_SOCIAL_OPEN_RE = re.compile(
    r"(?i)\b(?:"
    r"how(?:'re| are) you(?:\s+feeling)?|"
    r"how(?:'s| is) it going|"
    r"how you doing|"
    r"how(?:'re| are) things|"
    r"how(?:'s| is) everything|"
    r"what(?:'s| is) up|"
    r"wyd|"
    r"how(?:'s| is) your day|"
    r"good (?:morning|afternoon|evening|night)"
    r")\b[^.?!]*[.?!]?"
)
_CHAT_FILLER_RE = re.compile(
    r"(?i)\b(?:"
    r"can you|could you|would you|please|pls|"
    r"i wonder(?:ing)?|i was wondering|just wondering|"
    r"tell me|do you know|any idea|any news|"
    r"hey|hi|hello|yo|sup|"
    r"for me|thanks|thank you|thx|"
    r"real quick|quickly|btw|by the way"
    r")\b"
)
_RELEASE_DATE_RE = re.compile(
    r"(?i)(?:"
    r"release\s*date|"
    r"when\s+(?:does|is|will|do|did)\b.+\b(?:come\s+out|coming\s+out|release|released|drop(?:ping)?|launch(?:ing|es)?)\b|"
    r"\bcomes?\s+out\b|"
    r"\bcoming\s+out\b"
    r")"
)

# Team → home city for bare "what's the weather" alongside a sports ask.
_TEAM_CITY = {
    "oilers": "Edmonton",
    "oiler": "Edmonton",
    "flames": "Calgary",
    "canucks": "Vancouver",
    "leafs": "Toronto",
    "maple leafs": "Toronto",
    "canadiens": "Montreal",
    "habs": "Montreal",
    "jets": "Winnipeg",
    "senators": "Ottawa",
    "sens": "Ottawa",
    "rangers": "New York",
    "bruins": "Boston",
    "blackhawks": "Chicago",
    "kings": "Los Angeles",
    "ducks": "Anaheim",
    "sharks": "San Jose",
    "knights": "Las Vegas",
    "avalanche": "Denver",
    "avs": "Denver",
    "stars": "Dallas",
    "wild": "Minnesota",
    "predators": "Nashville",
    "blues": "St. Louis",
    "red wings": "Detroit",
    "penguins": "Pittsburgh",
    "capitals": "Washington",
    "caps": "Washington",
    "raptors": "Toronto",
    "blue jays": "Toronto",
    "jays": "Toronto",
    "elks": "Edmonton",
    "stampeders": "Calgary",
    "roughriders": "Regina",
    "bombers": "Winnipeg",
}


def apply_spelling_fixes(text: str) -> str:
    """Fix common speech-to-text / typo variants in search queries (maracco→morocco)."""
    s = str(text or "")
    if not s:
        return s
    # Multi-word first
    low = s.lower()
    for bad, good in (("wordl cup", "world cup"), ("worldcup", "world cup"), ("wordlcup", "world cup")):
        if bad in low:
            s = re.sub(re.escape(bad), good, s, flags=re.IGNORECASE)
            low = s.lower()

    def _fix_token(m: re.Match) -> str:
        word = m.group(0)
        key = re.sub(r"[^a-z0-9]", "", word.lower())
        fixed = _SPELLING_FIXES.get(key)
        if not fixed:
            return word
        # Preserve simple capitalization
        if word.isupper():
            return fixed.upper()
        if word[:1].isupper():
            return fixed[:1].upper() + fixed[1:]
        return fixed

    return re.sub(r"[A-Za-z][A-Za-z']*", _fix_token, s)


def _infer_city_from_text(text: str) -> str:
    low = (text or "").lower()
    # Explicit city first
    m = re.search(
        r"\b(?:in|for|near|around)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
        text or "",
    )
    if m:
        place = m.group(1).strip()
        if place.lower() not in {"the", "a", "an", "my", "our", "today", "tonight"}:
            return place
    for team, city in sorted(_TEAM_CITY.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(team)}\b", low):
            return city
    # Common bare city names
    for city in (
        "Edmonton", "Calgary", "Vancouver", "Toronto", "Montreal", "Winnipeg",
        "Ottawa", "Seattle", "Denver", "Boston", "Chicago", "Dallas",
    ):
        if re.search(rf"\b{re.escape(city)}\b", text or "", flags=re.IGNORECASE):
            return city
    return ""


def _is_weather_clause(text: str) -> bool:
    low = (text or "").lower()
    if any(t in low for t in _WEATHER_TERMS):
        return True
    # Spoken shorthand: "what's the temp tomorrow"
    if re.search(r"\btemp(?:s|erature|eratures)?\b", low):
        return True
    return False


def _is_schedule_or_sports_clause(text: str) -> bool:
    """True for schedules, fixtures, leagues — not only NHL team names."""
    low = (text or "").lower()
    if any(t in low for t in _SCHEDULE_TERMS):
        return True
    if re.search(r"\b(next|upcoming)\s+(game|match|matches|fixture|fixtures)\b", low):
        return True
    # Plural matches/games alone + competition or day
    if re.search(r"\b(matches|games|fixtures)\b", low) and re.search(
        r"\b(today|tonight|tomorrow|this week|weekend|schedule|happening|playing|fifa|world cup|"
        r"nhl|nba|nfl|mlb|soccer|football|premier|uefa|mls)\b",
        low,
    ):
        return True
    if re.search(r"\b(game|match|schedule|fixture)\b", low) and (
        any(team in low for team in _TEAM_CITY)
        or re.search(
            r"\b(nhl|nba|nfl|mlb|oilers?|flames|canucks|leafs|fifa|world cup|soccer|football)\b",
            low,
        )
    ):
        return True
    # League + day/playing without the word "match"
    if re.search(r"\b(fifa|world cup|uefa|premier league|champions league)\b", low) and re.search(
        r"\b(today|tonight|tomorrow|schedule|playing|fixtures?|matches?|games?)\b",
        low,
    ):
        return True
    return False


def intent_domains(text: str) -> set[str]:
    """
    Lightweight domain tags for multi-intent detection.

    General mechanism — not a per-combo recipe. Two or more distinct domains
    in one utterance ⇒ multi-intent, regardless of whether we have a recipe.
    """
    low = (text or "").lower()
    if not low:
        return set()
    domains: set[str] = set()
    if _is_weather_clause(low):
        domains.add("weather")
    if _is_schedule_or_sports_clause(low) or re.search(
        r"\b(fifa|world cup|nhl|nba|nfl|mlb|soccer|football|premier league|"
        r"match(?:es)?|score(?:s)?|standings|playoff)\b",
        low,
    ):
        domains.add("sports")
    if re.search(
        r"\b(stock|share price|nasdaq|s&p|dow jones|bitcoin|btc|ethereum|eth|crypto|ticker)\b",
        low,
    ):
        domains.add("finance")
    if re.search(
        r"\b(movie|film|trailer|netflix|show|series|album|gta|rockstar|box office)\b",
        low,
    ) or _has_trailer_intent(low) or _has_character_cast_intent(low):
        domains.add("entertainment")
    if re.search(r"\b(news|headline|breaking|headlines)\b", low) and "weather" not in domains:
        domains.add("news")
    if re.search(
        r"\b(capital of|who is the|ceo of|founded|invented|tallest|longest|population of)\b",
        low,
    ):
        domains.add("fact")
    if re.search(r"\b(odds|betting|moneyline|spread)\b", low):
        domains.add("odds")
    return domains


def _relative_day_labels(text: str) -> tuple[str, str]:
    """Return (day_word, calendar_label) e.g. ('tomorrow', 'Thursday July 9 2026')."""
    low = (text or "").lower()
    now = datetime.now()
    if re.search(r"\btomorrow\b", low):
        d = now + timedelta(days=1)
        return "tomorrow", d.strftime("%A %B %d %Y")
    if re.search(r"\btonight\b", low):
        return "tonight", now.strftime("%A %B %d %Y")
    if re.search(r"\btoday\b", low):
        return "today", now.strftime("%A %B %d %Y")
    return "", ""


def _explicit_calendar_date_label(text: str) -> str:
    """Pull 'July 9 2026' / 'July 9th' style dates into a compact calendar pin."""
    low = (text or "").lower()
    m = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)"
        r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?\b",
        low,
    )
    if not m:
        return ""
    month, day_n = m.group(1).title(), m.group(2)
    year = m.group(3) or str(datetime.now().year)
    return f"{month} {int(day_n)} {year}"


def _normalize_sports_query(text: str) -> str:
    q = _normalize_text(apply_spelling_fixes(text or ""))
    low = q.lower()
    day, cal = _relative_day_labels(low)
    explicit = _explicit_calendar_date_label(low)
    # FIFA / World Cup fixtures (general league normalize — not multi-intent recipe)
    if re.search(r"\b(fifa|world cup)\b", low):
        # Keep named sides when present (morocco, portugal, …)
        named = re.findall(
            r"\b(morocco|portugal|spain|brazil|argentina|france|germany|england|"
            r"canada|mexico|usa|japan|korea|croatia|netherlands|italy|belgium|"
            r"uruguay|colombia|senegal|australia)\b",
            low,
        )
        side = " ".join(dict.fromkeys(named))  # stable unique
        # Ask for teams + kickoff so DDG/snippets surface concrete matchups, not tournament fluff
        base = "FIFA World Cup matchups teams kickoff schedule fixtures"
        if side:
            base = f"FIFA World Cup {side} matchups teams kickoff schedule fixtures"
        # Pin calendar date so Tavily doesn't return a random tournament month
        if day and cal:
            return f"{base} {day} {cal}"
        if day:
            return f"{base} {day}"
        if explicit:
            return f"{base} {explicit}"
        return base
    # Edmonton Oilers / Oilers
    if re.search(r"\boilers?\b", low):
        if re.search(r"\b(next|upcoming|when)\b", low) or "schedule" in low or "game" in low:
            return "Edmonton Oilers next game schedule NHL"
        return "Edmonton Oilers " + q
    if re.search(r"\bflames\b", low) and re.search(r"\b(next|game|schedule)\b", low):
        return "Calgary Flames next game schedule NHL"
    if re.search(r"\bcanucks\b", low) and re.search(r"\b(next|game|schedule)\b", low):
        return "Vancouver Canucks next game schedule NHL"
    if re.search(r"\b(next|upcoming)\s+(game|match)\b", low):
        team = re.sub(
            r"(?i)\b(when|is|the|next|upcoming|game|match|schedule|for|of|what|time)\b",
            " ",
            q,
        )
        team = _normalize_text(team)
        if team:
            return f"{team} next game schedule"
    # Generic "matches/games happening" + relative day or explicit calendar date
    if re.search(r"\b(matches|games|fixtures|playing|matchup)\b", low) and (day or explicit):
        cleaned = re.sub(
            r"(?i)\b(what|which|are|is|happening|for|the|a|an|also|just|wondering|sorry|not|"
            r"then|being|played|explain|me|who|when|next)\b",
            " ",
            q,
        )
        cleaned = _normalize_text(cleaned) or q
        pin = f"{day} {cal}".strip() if day and cal else (day or explicit)
        # Prefer league/context words if present; else compact "games schedule DATE"
        if re.search(r"\b(fifa|world cup|nhl|nba|nfl|mlb|soccer|football)\b", cleaned.lower()):
            return f"{cleaned} schedule fixtures {pin}".strip()
        return f"sports games matches schedule fixtures {pin}".strip()
    return q


def _strip_weather_chat_filler(text: str) -> str:
    """Leaf cleaner for weather strings — must NEVER call normalize_web_search_query*."""
    q = _normalize_text(text)
    if not q:
        return ""
    q = q.replace("\u2019", "'").replace("\u2018", "'")
    q = _SOCIAL_OPEN_RE.sub(" ", q)
    # Drop compliments / small-talk that often co-occur with weather asks
    q = re.sub(
        r"(?i)\b(you look(?:ing)? great|looking good|look good|hope you(?:'re| are) well|"
        r"not much|just chilling|just chiiling|what'?s up|whats up|echo)\b[^.?!]*",
        " ",
        q,
    )
    q = _CHAT_FILLER_RE.sub(" ", q)
    q = re.sub(
        r"(?i)\b(can you|could you|would you|please|pls|check|look up|get me|for me|"
        r"tho|though|right now|real quick|quickly|hope|well|just|much)\b",
        " ",
        q,
    )
    q = re.sub(r"(?i)^(what(?:'s| is)|how(?:'s| is)|and|also|the|a|an)\s+", "", q)
    q = re.sub(r"[?!.]+", " ", q)
    q = _normalize_text(q)
    return q


def _normalize_weather_query(text: str, *, city_hint: str = "") -> str:
    """Build a compact weather search string. Must not call normalize_web_search_query*."""
    city = (city_hint or "").strip() or _infer_city_from_text(text)
    day, cal = _relative_day_labels(text or "")
    if not day:
        day = "today"
        cal = datetime.now().strftime("%A %B %d %Y")
    day_part = f"{day} {cal}".strip() if cal else day
    if city:
        return f"{city} weather {day_part} high low temperature forecast"
    # No city: strip chat filler only (never re-enter normalize_web_search_query_single —
    # that path used to recurse: weather → single → weather → …).
    cleaned = _strip_weather_chat_filler(text)
    cleaned = re.sub(r"(?i)\b(weather|forecast|temperature|temp)\b", " ", cleaned)
    cleaned = re.sub(r"(?i)\b(the|a|an|for|me|my|you|your|tomorrow|today|tonight)\b", " ", cleaned)
    cleaned = _normalize_text(cleaned)
    # Only keep cleaned if it looks like a place name (short, no leftover chatter/day words)
    if (
        cleaned
        and 2 <= len(cleaned) <= 40
        and len(cleaned.split()) <= 4
        and not re.search(r"(?i)\b(high|low|going|be|what|matches|fifa)\b", cleaned)
    ):
        return f"{cleaned} weather {day_part} high low temperature forecast"
    return f"weather {day_part} high low temperature forecast"


def _has_gta_context(text: str) -> bool:
    return bool(re.search(r"(?i)\b(?:gta|grand theft auto)\b", text or ""))


def _has_trailer_intent(text: str) -> bool:
    return bool(re.search(r"(?i)\btrailer\s*(?:#?\s*)?(\d+|three|two|one)\b", text or ""))


def _has_character_cast_intent(text: str) -> bool:
    low = (text or "").lower()
    return bool(
        re.search(r"\b(characters?|cast|protagonists?|playable)\b", low)
        or re.search(r"\bnames of the (?:characters?|cast)\b", low)
        # "who is in the cast" / "who are the characters" — not "who is the president"
        or re.search(r"\bwho (?:is|are) (?:in|the) (?:cast|characters?|game|movie|film)\b", low)
        or re.search(r"\bwho (?:is|are) (?:playable|protagonists?)\b", low)
    )


def _has_gta_release_intent(text: str) -> bool:
    low = (text or "").lower()
    if not _has_gta_context(low):
        return False
    return bool(
        re.search(
            r"\b(release(?:s|d)?|come\s+out|coming\s+out|launch(?:es|ing)?|drop(?:s|ping)?|"
            r"when\s+(?:does|is|will)|out\s+on)\b",
            low,
        )
    )


def _has_gta_price_intent(text: str) -> bool:
    low = (text or "").lower()
    if not _has_gta_context(low) and not re.search(r"\b(it|game|edition)\b", low):
        # Bare cost only counts when GTA is already in the full message context
        return False
    return bool(
        re.search(
            r"\b(how much|cost(?:s|ing)?|price|pricing|msrp|pre-?order|edition(?:s)?|"
            r"money it costs|dollars?)\b",
            low,
        )
    )


def _normalize_gta_trailer_query(text: str, full_context: str = "") -> str:
    blob = f"{text or ''} {full_context or ''}"
    m = re.search(r"(?i)\btrailer\s*(?:#?\s*)?(\d+|three|two|one)\b", blob)
    num = ""
    if m:
        raw_n = m.group(1).lower()
        num = {"one": "1", "two": "2", "three": "3"}.get(raw_n, raw_n)
    if num:
        return f"GTA 6 Trailer {num} release date announcement Rockstar"
    return "GTA 6 Trailer 3 release date announcement Rockstar"


def _normalize_gta_characters_query(text: str = "") -> str:
    return "GTA 6 characters cast Lucia Jason Duval known details plot"


def _normalize_gta_release_query(text: str = "") -> str:
    return "GTA 6 release date launch platforms Rockstar official"


def _normalize_gta_price_query(text: str = "") -> str:
    return "GTA 6 price cost pre-order editions PS5 Xbox"


def _prep_search_work_text(text: str) -> str:
    """Strip social fluff before intent detection / split."""
    raw = _normalize_text(text)
    if not raw:
        return ""
    raw = raw.replace("\u2019", "'").replace("\u2018", "'")
    raw = apply_spelling_fixes(raw)
    work = _SOCIAL_OPEN_RE.sub(" ", raw)
    work = re.sub(
        r"(?i)\b(you look(?:ing)? great|looking good|love (?:the|your) (?:look|design|avatar)|really liking how you look)[^.?!]*[.?!]?",
        " ",
        work,
    )
    return _normalize_text(work)


def _is_smalltalk_clause(text: str) -> bool:
    """True for pure social/filler clauses that must not become search queries."""
    low = _normalize_text(apply_spelling_fixes(text or "")).lower()
    if not low:
        return True
    if _is_weather_clause(low) or _is_schedule_or_sports_clause(low):
        return False
    if any(
        t in low
        for t in (
            "score", "odds", "price", "stock", "news", "headline", "trailer",
            "release", "gta", "forecast", "temperature", "schedule", "fixture",
            "games", "matches", "fifa", "python", "bitcoin",
        )
    ):
        return False
    # Apology / self-correction prefixes: "sorry not tomorrow today" (real ask follows)
    if re.search(
        r"(?i)^\s*(?:sorry|my bad|oops|actually|i meant|never ?mind|nvm)\b",
        low,
    ) and len(low.split()) <= 8:
        if not re.search(
            r"(?i)\b(what|when|where|who|which|how much|how many|find|check|search|"
            r"look up|games?|matches?|weather|price|score)\b",
            low,
        ):
            return True
    if re.search(
        r"(?i)\b(not much|just chilling|chilling|hope you(?:'re| are) well|"
        r"look(?:ing)? good|you look|sounds good|lol|haha|thanks|thank you|"
        r"how(?:'re| are) you|what's up|whats up|cool|nice|awesome|hey|hi|hello)\b",
        low,
    ):
        # Pure vibes / greeting with no fact noun
        if not re.search(r"(?i)\b(what|when|where|who|which|how much|how many|find|check|search|look up)\b", low):
            return True
        # "look good" style — not "look up"
        if re.search(r"(?i)\blook(?:ing)?\s+good\b", low) and "look up" not in low:
            return True
    return False


def looks_like_multi_intent(text: str) -> bool:
    """
    Cheap classifier: is this message more than one distinct ask?

    Must stay false for simple single-fact questions so they never pay
    decomposition latency (no extra LLM call, no extra Tavily fan-out).

    Primary general signal: **two or more intent domains** (weather+sports,
    finance+weather, fact+entertainment, …) — not a list of hand-written combos.
    """
    t = _prep_search_work_text(text)
    if not t:
        return False
    words = t.split()
    # Domain diversity is the real multi-intent signal (works on novel combos).
    domains = intent_domains(t)
    if len(domains) >= 2:
        return True
    # Single-fact short asks never multi
    if len(words) < 10:
        return False
    low = t.lower()
    # Two+ explicit question marks
    if t.count("?") >= 2:
        return True
    # Clear multi-join markers with substance on both sides
    if re.search(
        r"(?i)\b(?:and also|also|plus|as well as|and then)\b",
        low,
    ):
        parts = re.split(r"(?i)\b(?:and also|also|plus|as well as|and then)\b", t)
        parts = [
            p.strip(" ,;.")
            for p in parts
            if p and len(p.split()) >= 2 and not _is_smalltalk_clause(p)
        ]
        if len(parts) >= 2:
            # Two fact-bearing sides → multi even if domains only resolved on full text
            return True
    # Two interrogative heads in one message
    inters = re.findall(
        r"(?i)\b(what|when|where|who|which|how|why|find out|look up|check|tell me)\b",
        low,
    )
    if len(inters) >= 2 and len(words) >= 12:
        return True
    # "A, and B" with two clause-like segments — ignore pure small-talk clauses
    clauses = [
        c.strip(" ,;:")
        for c in re.split(r"[?!.]+|\band\b", t, flags=re.IGNORECASE)
        if c and len(c.split()) >= 3 and not _is_smalltalk_clause(c)
    ]
    if len(clauses) >= 2:
        # Distinct domains across clauses
        clause_domains = [intent_domains(c) for c in clauses]
        union: set[str] = set()
        for d in clause_domains:
            union |= d
        if len(union) >= 2:
            return True
        qish = sum(
            1
            for c in clauses
            if re.search(r"(?i)\b(what|when|where|who|which|how|find|check|tell|names?)\b", c)
        )
        if qish >= 2:
            return True
        # One interrogative + another substantive fact clause (e.g. "tallest building in Dubai
        # and who is the CEO of Tesla") — still multi even if only one clause has who/what.
        if qish >= 1 and len(clauses) >= 2 and all(len(c.split()) >= 3 for c in clauses[:2]):
            return True
        # Two noun-heavy clauses with little overlap (independent facts joined by "and")
        if len(words) >= 10 and all(len(c.split()) >= 3 for c in clauses[:2]):
            a = set(re.findall(r"[a-z0-9]{4,}", clauses[0].lower()))
            b = set(re.findall(r"[a-z0-9]{4,}", clauses[1].lower()))
            stop = {"what", "when", "where", "which", "that", "this", "with", "from", "about", "current", "right"}
            a, b = a - stop, b - stop
            if a and b and len(a & b) / max(1, len(a | b)) < 0.35:
                return True
    return False


def recipe_multi_search_queries(text: str) -> list[str]:
    """
    Fast path: hand-written multi-intent recipes only.

    Returns 2+ queries when a recipe matches, else [].
    Single-intent weather/sports alone is *not* multi — handled by single normalize.
    """
    work = _prep_search_work_text(text)
    if not work:
        return []

    city_hint = _infer_city_from_text(work)
    has_weather = _is_weather_clause(work)
    has_sports = _is_schedule_or_sports_clause(work)
    has_gta = _has_gta_context(work)
    has_trailer = _has_trailer_intent(work)
    has_chars = _has_character_cast_intent(work)

    out: list[str] = []
    if has_sports and has_weather:
        clauses = [
            c.strip(" ,;:")
            for c in re.split(r"[?!.]+|\band\b|\balso\b", work, flags=re.IGNORECASE)
            if c and c.strip(" ,;:")
        ]
        sports_clause = next((c for c in clauses if _is_schedule_or_sports_clause(c)), work)
        weather_clause = next((c for c in clauses if _is_weather_clause(c)), "weather")
        out.append(_normalize_sports_query(sports_clause))
        w_city = _infer_city_from_text(weather_clause) or city_hint
        out.append(_normalize_weather_query(weather_clause, city_hint=w_city))
    elif (has_trailer or has_chars) and (has_gta or has_trailer):
        if has_trailer:
            out.append(_normalize_gta_trailer_query(work, full_context=work))
        if has_chars or (has_gta and re.search(r"(?i)\b(who|names?|know|cast)\b", work)):
            out.append(_normalize_gta_characters_query(work))

    return _dedupe_queries(out)[:4]


def _dedupe_queries(queries: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for q in queries:
        key = _normalize_text(q).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(_normalize_text(q))
    return deduped


def _is_orphan_price_query(q: str) -> bool:
    """True for bare 'how much will it cost' with no product/entity noun."""
    low = _normalize_text(q).lower()
    if not low:
        return False
    if not re.search(r"(?i)\b(how much|cost(?:s|ing)?|price|pricing|msrp|pre-?order)\b", low):
        return False
    # Already entity-grounded
    if re.search(
        r"(?i)\b(gta|grand theft auto|bitcoin|btc|ethereum|stock|iphone|ps5|xbox|"
        r"rockstar|game|fifa|python|nvidia|tesla|apple|microsoft)\b",
        low,
    ):
        return False
    # Short cost-only / "will it cost" clauses
    if len(low.split()) <= 8:
        return True
    return bool(re.search(r"(?i)\b(how much will it cost|how much does it cost|what does it cost)\b", low))


def _rebind_orphan_queries(work: str, queries: list[str]) -> list[str]:
    """Attach bare cost/price sub-queries to GTA (or other) entity from full message."""
    work_n = _normalize_text(work)
    if not work_n or not queries:
        return queries
    out: list[str] = []
    for q in queries:
        if _is_orphan_price_query(q) and _has_gta_context(work_n):
            out.append(_normalize_gta_price_query(work_n))
        elif _is_orphan_price_query(q) and re.search(r"(?i)\b(bitcoin|btc)\b", work_n):
            out.append("current bitcoin price USD")
        else:
            out.append(q)
    return _dedupe_queries(out)


def _normalize_clause_for_search(part: str, *, full_context: str = "") -> str:
    """Normalize one fact clause with domain-aware compactors."""
    p = _normalize_text(apply_spelling_fixes(part or ""))
    if not p or _is_smalltalk_clause(p):
        return ""
    ctx = _normalize_text(full_context or p)
    if _is_weather_clause(p) and not _is_schedule_or_sports_clause(p):
        return _normalize_weather_query(p, city_hint=_infer_city_from_text(p) or _infer_city_from_text(ctx))
    if _is_schedule_or_sports_clause(p) and not _is_weather_clause(p):
        return _normalize_sports_query(p)
    if _has_trailer_intent(p) and (_has_gta_context(p) or _has_gta_context(ctx) or _has_trailer_intent(p)):
        if _has_character_cast_intent(p):
            # Both in one clause — leave to recipe; single side only here
            pass
        return _normalize_gta_trailer_query(p, full_context=ctx)
    if (_has_gta_context(p) or _has_gta_context(ctx)) and _has_character_cast_intent(p):
        return _normalize_gta_characters_query(p)
    if (_has_gta_context(p) or _has_gta_context(ctx)) and (
        _has_gta_release_intent(p) or _has_gta_release_intent(f"{p} {ctx}" if _has_gta_context(ctx) else p)
    ):
        # "when does gta 6 come out" on this clause, or release intent only on full text
        if _has_gta_context(p) and _has_gta_release_intent(p):
            return _normalize_gta_release_query(p)
        if _has_gta_context(p) and re.search(r"(?i)\b(how much|cost|price|money)\b", p):
            return _normalize_gta_price_query(p)
        if _has_gta_release_intent(p) and _has_gta_context(ctx):
            return _normalize_gta_release_query(ctx)
    # Bare "how much will it cost" after a GTA clause in the same message
    if _is_orphan_price_query(p) and _has_gta_context(ctx):
        return _normalize_gta_price_query(ctx)
    if _has_gta_context(p) and re.search(r"(?i)\b(how much|cost|price|money)\b", p):
        return _normalize_gta_price_query(p)
    n = normalize_web_search_query_single(p) or p
    return _normalize_text(n)


def _clip_span_to_clause(span: str) -> str:
    """Stop a domain span at multi-intent joiners so domains don't bleed into each other."""
    s = str(span or "")
    s = re.split(r"(?i)\b(?:and also|also|plus|as well as|and then)\b", s)[0]
    return _normalize_text(s)


def _force_domain_decompose(text: str) -> list[str]:
    """
    When the full message has 2+ domains but clause split only yielded one query,
    carve domain-specific compact queries from the whole text.

    This is still *general* (domain tags), not a weather+FIFA special case.
    """
    work = _prep_search_work_text(text)
    domains = intent_domains(work)
    if len(domains) < 2:
        return []
    out: list[str] = []
    city_hint = _infer_city_from_text(work)

    if "weather" in domains:
        m = re.search(
            r"(?i)(?:what(?:'s| is)?\s+)?(?:the\s+)?(?:temp(?:erature)?s?|weather|forecast|high|low)"
            r".{0,80}?(?:tomorrow|today|tonight|this week)?(?:\s+in\s+[A-Za-z .'-]+)?",
            work,
        )
        span = _clip_span_to_clause(m.group(0) if m else "")
        if not span or not _is_weather_clause(span):
            # Recover from "… weather in Denver" style after a joiner
            m2 = re.search(
                r"(?i)\b(?:temp(?:erature)?s?|weather|forecast)\b.{0,60}",
                work,
            )
            span = _clip_span_to_clause(m2.group(0) if m2 else "weather")
        # Strip non-weather domains from span
        span = re.sub(
            r"(?i)\b(bitcoin|btc|stock|nasdaq|fifa|world cup|score|movie|trailer|news)\b",
            " ",
            span,
        )
        wq = _normalize_weather_query(span, city_hint=city_hint or _infer_city_from_text(work))
        if wq:
            out.append(wq)

    if "sports" in domains:
        # Start near sports keywords — NEVER from message start (GTA clause used to
        # swallow the whole string, then _clip_span_to_clause dropped FIFA).
        kw = re.search(
            r"(?i)\b(?:fifa|world\s*cup|nhl|nba|nfl|mlb|uefa|premier\s*league|"
            r"matches?|games?|fixtures?|matchup|score|playing|oilers?|flames|canucks|lakers)\b",
            work,
        )
        if kw:
            # Expand left only to last multi-intent joiner / punctuation
            left = work[: kw.start()]
            cut = 0
            for mjoin in re.finditer(
                r"(?i)(?:[?!.]+\s*|\b(?:and also|as well as|and then|also|plus)\b\s*)",
                left,
            ):
                cut = mjoin.end()
            span = work[cut : min(len(work), kw.end() + 90)]
        else:
            span = work
        span = _clip_span_to_clause(span)
        # If clip still landed on a non-sports half, take from keyword only
        if kw and not _is_schedule_or_sports_clause(span) and not re.search(
            r"(?i)\b(fifa|world cup|match|game|score|nhl|nba|nfl|mlb)\b", span
        ):
            span = work[kw.start() : min(len(work), kw.end() + 90)]
        span = re.sub(r"(?i)\b(temp(?:erature)?s?|weather|forecast|humidity|bitcoin|stock|gta)\b", " ", span)
        # Orphan "who is playing" alone → keep parent sports context
        if re.search(r"(?i)^\s*who\s+is\s+playing\s*$", span.strip()) and kw:
            span = work[max(0, kw.start() - 40) : min(len(work), kw.end() + 90)]
        sq = _normalize_sports_query(span)
        if sq and not _is_weather_clause(sq) and not _has_gta_context(sq):
            out.append(sq)
        elif re.search(r"(?i)\b(fifa|world cup)\b", work):
            out.append(_normalize_sports_query("fifa world cup matches " + (span or "")))

    if "finance" in domains:
        m = re.search(
            r"(?i)(?:what(?:'s| is)?\s+)?(?:the\s+)?(?:\w+\s+)?(?:stock|share)\s*price|"
            r"(?:bitcoin|btc|ethereum|eth|nasdaq|crypto)\s*(?:price)?|"
            r"price of \w+",
            work,
        )
        span = _clip_span_to_clause(m.group(0) if m else "")
        if not span:
            m2 = re.search(r"(?i)\b(?:bitcoin|btc|ethereum|stock|nasdaq|crypto)\b.{0,40}", work)
            span = _clip_span_to_clause(m2.group(0) if m2 else "")
        span = re.sub(r"(?i)\b(weather|forecast|temp(?:erature)?s?|fifa|match(?:es)?)\b", " ", span)
        span = _normalize_text(span)
        if span:
            fq = normalize_web_search_query_single(span) or span
            # Keep finance-y; don't let weather normalizer swallow it
            if fq and not _is_weather_clause(fq):
                out.append(fq)
            elif span:
                out.append(span)

    if "entertainment" in domains:
        if _has_gta_context(work) or _has_trailer_intent(work):
            if _has_trailer_intent(work):
                out.append(_normalize_gta_trailer_query(work, full_context=work))
            if _has_character_cast_intent(work):
                out.append(_normalize_gta_characters_query(work))
            # Release / launch (not trailer-only)
            if _has_gta_release_intent(work) and not _has_trailer_intent(work):
                out.append(_normalize_gta_release_query(work))
            # Price / cost / editions — separate search so release evidence isn't the only hit
            if _has_gta_price_intent(work) or (
                _has_gta_context(work)
                and re.search(r"(?i)\b(how much|cost|price|money it costs|pre-?order)\b", work)
            ):
                out.append(_normalize_gta_price_query(work))
            # Bare GTA with no specific intent → release as default fact ask
            if (
                _has_gta_context(work)
                and not _has_trailer_intent(work)
                and not _has_character_cast_intent(work)
                and not _has_gta_release_intent(work)
                and not re.search(r"(?i)\b(how much|cost|price)\b", work)
            ):
                out.append(_normalize_gta_release_query(work))
        else:
            m = re.search(
                r"(?i)(?:when|what).{0,40}\b(?:movie|film|trailer|release|netflix|show|dune)\b.{0,40}",
                work,
            )
            if m:
                span = _clip_span_to_clause(m.group(0))
                out.append(normalize_web_search_query_single(span) or span)

    if "news" in domains:
        m = re.search(r"(?i)(?:local\s+)?news.{0,40}|headlines.{0,40}", work)
        if m:
            span = _clip_span_to_clause(m.group(0))
            span = re.sub(r"(?i)\b(weather|stock|bitcoin|fifa)\b", " ", span)
            nq = normalize_web_search_query_single(span) or span
            if nq:
                out.append(nq)

    if "fact" in domains:
        m = re.search(
            r"(?i)(?:what(?:'s| is)|who is|tallest|capital of|ceo of).{0,60}",
            work,
        )
        if m:
            span = _clip_span_to_clause(m.group(0))
            span = re.sub(r"(?i)\b(weather|stock|score|match(?:es)?)\b", " ", span)
            fq = normalize_web_search_query_single(span) or span
            if fq and not _is_weather_clause(fq) and not _is_schedule_or_sports_clause(fq):
                out.append(fq)

    if "odds" in domains and "sports" not in domains:
        m = re.search(r"(?i)(?:odds|betting|moneyline).{0,40}", work)
        if m:
            out.append(normalize_web_search_query_single(_clip_span_to_clause(m.group(0))) or m.group(0))

    return _dedupe_queries(out)[:5]


def _heuristic_decompose(text: str) -> list[str]:
    """No-LLM fallback: split on and/also/plus/? into compact sub-queries."""
    work = _prep_search_work_text(text)
    if not work:
        return []
    parts = [
        c.strip(" ,;:")
        for c in re.split(
            r"[?!.]+|\band also\b|\bas well as\b|\band then\b|\balso\b|\bplus\b|\band\b",
            work,
            flags=re.IGNORECASE,
        )
        if c and len(c.split()) >= 2 and not _is_smalltalk_clause(c)
    ]
    # Merge orphaned "who is playing" onto prior sports clause
    merged_parts: list[str] = []
    for p in parts:
        if (
            merged_parts
            and re.search(r"(?i)^\s*who\s+(?:is|are)\s+playing\b", p)
            and _is_schedule_or_sports_clause(merged_parts[-1])
        ):
            merged_parts[-1] = f"{merged_parts[-1]} {p}".strip()
            continue
        merged_parts.append(p)
    parts = merged_parts
    out: list[str] = []
    for p in parts:
        # Prefer specialized single normalize per clause (full work rebinds bare "how much")
        n = _normalize_clause_for_search(p, full_context=work)
        if n and len(n) >= 4 and not _is_smalltalk_clause(n):
            out.append(n)
    out = _rebind_orphan_queries(work, _dedupe_queries(out))
    # Prefer force domain when multi domains and heuristic stayed chatty / incomplete
    forced = _force_domain_decompose(work) if len(intent_domains(work)) >= 2 else []
    if len(out) < 2 and forced:
        if len(forced) >= 2:
            return forced
        out = _dedupe_queries(out + forced)
    elif forced and len(forced) >= 2:
        # Heuristic chatty residue: "i need you to search…", "explain to me…"
        chatty = sum(
            1
            for q in out
            if re.search(
                r"(?i)\b(i need|can you|please|explain to me|search when|how much money it)\b",
                q,
            )
            or len(q.split()) > 12
        )
        # "how much will it cost" alone still matches cost — require entity-grounded price
        has_grounded_price = any(
            re.search(r"(?i)\b(gta|price cost|pre-?order|bitcoin|msrp)\b", q)
            and re.search(r"(?i)\b(price|cost|pre-?order|msrp)\b", q)
            for q in out
        ) or any(
            re.search(r"(?i)\b(gta|grand theft auto).{0,40}\b(price|cost)\b", q)
            or re.search(r"(?i)\b(price|cost).{0,40}\b(gta|grand theft auto)\b", q)
            for q in out
        )
        missing_price = bool(re.search(r"(?i)\b(how much|cost|price)\b", work)) and not has_grounded_price
        orphan_price = any(_is_orphan_price_query(q) for q in out)
        missing_fifa = re.search(r"(?i)\b(fifa|world cup)\b", work) and not any(
            re.search(r"(?i)\b(fifa|world cup)\b", q) for q in out
        )
        if chatty or missing_price or orphan_price or missing_fifa or len(forced) > len(out):
            return forced[:5]
    return out[:5]


def decompose_search_intents(text: str, llm_invoke=None) -> list[str]:
    """
    General multi-intent decomposition (fallback when no recipe matches).

    llm_invoke: optional callable(str) -> str for a focused decompose prompt.
    If missing or parse fails, uses heuristic clause split.
    """
    work = _prep_search_work_text(text)
    if not work:
        return []

    if callable(llm_invoke):
        prompt = (
            "Break the user message into the minimum independent web-search sub-questions "
            "needed to answer it fully (2-5 max). Preserve key nouns, places, names, dates.\n"
            "Return ONLY a JSON array of short search strings, e.g. "
            '["tallest building in Dubai", "CEO of Tesla 2026"].\n'
            "If there is only ONE ask, return a one-element array.\n"
            "Do not answer the questions. Do not add commentary.\n\n"
            f"User: {work[:500]}\n"
        )
        try:
            raw = str(llm_invoke(prompt) or "").strip()
            # Prefer JSON array
            m = re.search(r"\[[\s\S]*\]", raw)
            if m:
                import json as _json

                data = _json.loads(m.group(0))
                if isinstance(data, list):
                    items = [_normalize_text(str(x)) for x in data if str(x or "").strip()]
                    items = [x for x in items if len(x) >= 3]
                    if items:
                        return _dedupe_queries(items)[:5]
            # Numbered / bulleted lines
            lines = []
            for line in raw.splitlines():
                line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip().strip("\"'")
                if len(line.split()) >= 2:
                    lines.append(line)
            if lines:
                return _dedupe_queries(lines)[:5]
        except Exception:
            pass

    return _heuristic_decompose(work)


def enrich_sports_query_with_subject(query: str, subject: str) -> str:
    """Pin league/team from prior subject onto bare schedule follow-ups.

    Live: \"july 9th what games?\" after a FIFA turn should not search generic \"sports games\".
    """
    q = _normalize_text(query)
    sub = _normalize_text(subject)
    if not q or not sub:
        return q
    # Already league-specific
    if re.search(r"(?i)\b(fifa|world\s*cup|nhl|nba|nfl|mlb|uefa|premier\s*league|oilers|flames)\b", q):
        return q
    # Only enrich schedule / slate style asks
    if not (
        _is_schedule_or_sports_clause(q)
        or re.search(r"(?i)\b(games?|matches?|fixtures?|playing|schedule)\b", q)
    ):
        return q
    # Don't rewrite non-sports subjects
    if not (
        _is_schedule_or_sports_clause(sub)
        or re.search(r"(?i)\b(fifa|world\s*cup|nhl|nba|nfl|mlb|match|score|oilers)\b", sub)
    ):
        return q
    if re.search(r"(?i)\b(fifa|world\s*cup)\b", sub):
        return _normalize_sports_query(f"FIFA World Cup {q}")
    if re.search(r"(?i)\b(nhl|oilers|flames|canucks)\b", sub):
        return _normalize_sports_query(f"NHL {q}")
    if re.search(r"(?i)\bnba\b", sub):
        return _normalize_sports_query(f"NBA {q}")
    if re.search(r"(?i)\bnfl\b", sub):
        return _normalize_sports_query(f"NFL {q}")
    if re.search(r"(?i)\bmlb\b", sub):
        return _normalize_sports_query(f"MLB {q}")
    return q


def resolve_web_search_queries(
    user_text: str,
    model_query: str = "",
    *,
    llm_invoke=None,
    use_decomposition: bool = True,
) -> list[str]:
    """
    Full query resolution for grounded search.

    Order:
      1) Recipe multi-split (free, instant) when it returns 2+ queries
      2) If multi-intent suspected and no recipe: general decomposition
         (domain diversity + also/and splits + optional LLM)
      3) Single compact query from user text
      4) Never silently replace a real multi-split with the model's single tool arg

    Critical: model tool args are often single-intent. User text is authoritative
    for multi detection — never collapse multi user text to the model arg alone.
    """
    user = _normalize_text(user_text)
    model_q = _normalize_text(model_query)

    # Prefer user text for multi detection — model tool args are often single-intent.
    multi_src = user or model_q
    if user and (len(intent_domains(user)) >= 2 or looks_like_multi_intent(user)):
        multi_src = user
    elif model_q and len(intent_domains(model_q)) >= 2:
        multi_src = model_q

    # 1) Fast recipes
    recipes = recipe_multi_search_queries(multi_src)
    if len(recipes) >= 2:
        multi = list(recipes)
    else:
        multi = []
        # 2) General decomposition fallback (only when multi-intent looks real)
        multi_suspected = use_decomposition and (
            looks_like_multi_intent(multi_src)
            or len(intent_domains(multi_src)) >= 2
        )
        if multi_suspected:
            decomp = decompose_search_intents(multi_src, llm_invoke=llm_invoke)
            if len(decomp) < 2:
                # Hard fail-safe: domain carve even if LLM returned one blob
                decomp = _force_domain_decompose(multi_src) or decomp
            if len(decomp) >= 2:
                multi = decomp
        # 3) Single-intent compact
        if not multi:
            one = normalize_web_search_query_single(user) or normalize_web_search_query_single(model_q)
            if not one:
                one = model_q or user
            multi = [one] if one else []

    # Optionally append distinct model tool arg if useful and not already covered.
    # Never add a same-domain duplicate (model often rephrases weather already in multi).
    if model_q and len(multi) >= 2:
        keys = {m.lower() for m in multi}
        model_c = normalize_web_search_query_single(model_q) or model_q
        model_dom = intent_domains(model_c)
        covered_dom: set[str] = set()
        for m in multi:
            covered_dom |= intent_domains(m)
        same_domain = bool(model_dom and model_dom.issubset(covered_dom))
        if (
            model_c
            and len(model_c) >= 8
            and len(model_c.split()) >= 2
            and model_c.lower() not in keys
            and len(model_c.split()) <= 14
            and not same_domain
            and not re.match(r"(?i)^(i |can you|please|find out|what |when )", model_c)
        ):
            multi.append(model_c)

    # Final guard: if user had 2+ domains but multi is still 1, force domain split
    if use_decomposition and len(multi) < 2 and len(intent_domains(multi_src)) >= 2:
        forced = _force_domain_decompose(multi_src)
        if len(forced) >= 2:
            multi = forced

    # Rebind bare "how much will it cost" → GTA price when parent turn has GTA
    multi = _rebind_orphan_queries(multi_src, multi)
    # If GTA+price still missing grounded price query, inject it
    if _has_gta_context(multi_src) and re.search(r"(?i)\b(how much|cost|price)\b", multi_src):
        if not any(
            re.search(r"(?i)\b(price|cost|pre-?order)\b", q) and re.search(r"(?i)\bgta\b", q)
            for q in multi
        ):
            multi = _dedupe_queries(list(multi) + [_normalize_gta_price_query(multi_src)])

    return _dedupe_queries(multi)[:5]


def split_web_search_queries(text: str) -> list[str]:
    """
    Split multi-intent chat into separate compact search queries.

    Fast path: recipes. Fallback: general decomposition when multi-intent is detected.
    For single-intent weather/sports alone, returns one normalized query (no LLM).
    """
    work = _prep_search_work_text(text)
    if not work:
        return []

    # Recipes that produce 2+ queries
    recipes = recipe_multi_search_queries(work)
    if len(recipes) >= 2:
        return recipes

    # General multi-intent (no LLM here — callers that have an LLM should use
    # resolve_web_search_queries(..., llm_invoke=...). Heuristic only for pure split.)
    if looks_like_multi_intent(work):
        decomp = _heuristic_decompose(work)
        if len(decomp) >= 2:
            return decomp

    # Single-intent specialties (weather alone, sports alone, etc.)
    city_hint = _infer_city_from_text(work)
    if _is_weather_clause(work) and not _is_schedule_or_sports_clause(work):
        return [_normalize_weather_query(work, city_hint=city_hint)]
    if _is_schedule_or_sports_clause(work) and not _is_weather_clause(work):
        return [_normalize_sports_query(work)]
    if (_has_trailer_intent(work) or _has_character_cast_intent(work)) and (
        _has_gta_context(work) or _has_trailer_intent(work)
    ):
        # Single side of GTA (only trailer or only cast) — one query
        if _has_trailer_intent(work) and not _has_character_cast_intent(work):
            return [_normalize_gta_trailer_query(work, full_context=work)]
        if _has_character_cast_intent(work) and not _has_trailer_intent(work):
            return [_normalize_gta_characters_query(work)]

    single = normalize_web_search_query_single(work)
    return [single] if single else []


def normalize_web_search_query_single(query: str) -> str:
    """Compact a single-intent string (no multi-intent fan-out)."""
    q = _normalize_text(query)
    if not q:
        return ""
    q = q.replace("\u2019", "'").replace("\u2018", "'")
    q = apply_spelling_fixes(q)

    # Drop "do a deeper search about …" wrappers so we never Tavily the meta-phrase.
    q = re.sub(
        r"(?i)\b(?:please\s+)?(?:can you\s+)?(?:do\s+a\s+)?(?:deep(?:er)?|more)\s+(?:web\s+)?search(?:\s+(?:about|on|for|into))?\b",
        " ",
        q,
    )
    q = re.sub(
        r"(?i)\b(?:dig|go)\s+deeper(?:\s+(?:on|into|about|for))?\b",
        " ",
        q,
    )
    q = re.sub(
        r"(?i)\b(?:search|research)\s+deeper(?:\s+(?:about|on|into|for))?\b",
        " ",
        q,
    )
    q = re.sub(r"(?i)\b(?:look\s+into\s+it\s+more|check\s+more|expand\s+on\s+that)\b", " ", q)
    q = _normalize_text(q)

    # Drop leading social openers / greeting clauses.
    q = _SOCIAL_OPEN_RE.sub(" ", q)
    # Split multi-intent: prefer the clause that looks like the factual ask.
    clauses = [
        c.strip(" ,;:")
        for c in re.split(r"[?!.]+|\band\b|\balso\b", q, flags=re.IGNORECASE)
        if c and c.strip(" ,;:")
    ]

    def _clause_score(c: str) -> int:
        low = c.lower()
        score = 0
        if _RELEASE_DATE_RE.search(c):
            score += 5
        if any(t in low for t in ("weather", "forecast", "score", "trailer", "gta", "news", "price", "stock")):
            score += 3
        if re.search(r"\b(when|what|who|where|which|how much|how many)\b", low):
            score += 2
        if re.search(r"\b(hey|hi|hello|feeling|doing)\b", low):
            score -= 3
        score += min(len(c.split()), 8)  # slight preference for substance
        return score

    if clauses:
        # Always rebuild from clauses so leftover "and …" glue is dropped.
        q = max(clauses, key=_clause_score)

    q = _CHAT_FILLER_RE.sub(" ", q)
    q = re.sub(r"(?i)\b(i wonder(?:ing)?|just wondering)\b", " ", q)
    # Drop glue left after social/clause splits
    q = re.sub(r"(?i)^(and|also|plus|so|but|well)\s+", "", q)
    q = re.sub(r"(?i)^(what(?:'s| is)|how(?:'s| is)|when(?:'s| is))\s+", "", q)
    q = _normalize_text(q)

    # "release notes" is documentation — never rewrite into "release date"
    if re.search(r"(?i)\brelease\s+notes\b", q):
        q_notes = re.sub(
            r"(?i)^(search(?:\s+for)?|look\s+up|find(?:\s+out)?|research|check|get)\s+",
            "",
            q,
        )
        q_notes = re.sub(r"(?i)\b(latest|new|official|current)\b", " ", q_notes)
        q_notes = _normalize_text(q_notes)
        # Prefer crisp doc query
        if re.search(r"(?i)\bpython\b", q_notes):
            return "Python latest release notes changelog official"
        if q_notes:
            return q_notes if "release notes" in q_notes.lower() else f"{q_notes} release notes"
        return "release notes changelog official"

    # Known entity rewrite: GTA / Grand Theft Auto trailer N (and bare "trailer 3")
    if _has_trailer_intent(q) and (_has_gta_context(q) or _has_trailer_intent(q)):
        # Prefer GTA Trailer N when trailer number present (default franchise context)
        return _normalize_gta_trailer_query(q, full_context=q)
    if _has_gta_context(q) and _has_character_cast_intent(q):
        return _normalize_gta_characters_query(q)
    # GTA release / price (single-intent compact — multi fans out elsewhere)
    if _has_gta_context(q):
        wants_price = bool(
            re.search(r"(?i)\b(how much|cost|price|money it costs|pre-?order|editions?)\b", q)
        )
        wants_release = _has_gta_release_intent(q) or bool(
            re.search(r"(?i)\b(when|release|launch|come out)\b", q)
        )
        if wants_price and not wants_release:
            return _normalize_gta_price_query(q)
        if wants_release:
            # Prefer release for single-string path; multi-intent adds price separately
            return _normalize_gta_release_query(q)
        if wants_price:
            return _normalize_gta_price_query(q)
    gta = re.search(
        r"(?i)\b(?:gta|grand theft auto)\s*(?:6|vi|six)?\b.*?\btrailer\s*(\d+)\b"
        r"|\btrailer\s*(\d+)\b.*?\b(?:gta|grand theft auto)\s*(?:6|vi|six)?\b",
        q,
    )
    if not gta:
        # "trailer 3 for gta 6" / "new trailer ... gta 6"
        gta = re.search(
            r"(?i)\btrailer\s*(\d+)\b.{0,40}\b(?:gta|grand theft auto)\s*(6|vi|six)\b"
            r"|\b(?:gta|grand theft auto)\s*(6|vi|six)\b.{0,40}\btrailer\s*(\d+)\b"
            r"|\b(?:gta|grand theft auto)\s*(6|vi|six)\b.{0,40}\btrailer\b",
            q,
        )
    if gta:
        nums = [g for g in gta.groups() if g]
        trailer_n = next((n for n in nums if n.isdigit()), None)
        if trailer_n:
            return f"GTA 6 Trailer {trailer_n} release date announcement Rockstar"
        return "GTA 6 trailer release date announcement Rockstar"

    # Sports / next-game compact form
    if _is_schedule_or_sports_clause(q):
        sports = _normalize_sports_query(q)
        if sports:
            return sports

    # Weather compact form (with inferred city when possible)
    if _is_weather_clause(q):
        return _normalize_weather_query(q, city_hint=_infer_city_from_text(q))

    # Generic: "when does X come out / release" → "X release date"
    # Never treat "release notes" as a product launch date.
    if not re.search(r"(?i)\brelease\s+notes\b", q):
        m = re.search(
            r"(?i)(?:when\s+(?:does|is|will|do|did)\s+)?(.+?)\s+"
            r"(?:come\s+out|coming\s+out|release(?:s|d)?|drop(?:s|ping)?|launch(?:es|ing)?)\b",
            q,
        )
        if m:
            subject = _normalize_text(m.group(1))
            subject = re.sub(
                r"(?i)^(when|that|the|a|an|new|latest|next|search for|look up)\s+",
                "",
                subject,
            )
            subject = re.sub(r"(?i)\b(that|the|a|an|new|latest|search|for)\b", " ", subject)
            subject = _normalize_text(subject)
            # Reject chatty leftovers ("i need you to search when gta 6 is")
            if (
                2 <= len(subject) <= 80
                and not re.search(r"(?i)\b(i need|can you|please|explain|search when)\b", subject)
                and len(subject.split()) <= 8
            ):
                return f"{subject} release date"

    # Strip leftover conversational glue / trailing greetings
    q = re.sub(
        r"(?i)\b(when|what|who|where|which|how|does|is|are|will|do|did|the|a|an|that|this|for|of|to|me|you|please)\b",
        " ",
        q,
    ) if len(q.split()) > 12 else q
    # Lighter strip for shorter queries — only leading wrappers
    q = re.sub(
        r"(?i)^(search(?:\s+for)?|look\s+up|find(?:\s+out)?|research(?:\s+deeply)?|check|get)\s+",
        "",
        q,
    )
    q = re.sub(r"(?i)\b(hey|hi|hello|yo|sup)\b[?.!]*$", "", q)
    q = _normalize_text(q.strip(" ?!.,;:"))
    return q or _normalize_text(query)


def normalize_web_search_query(query: str) -> str:
    """Turn a chatty multi-intent user line into a compact web search string.

    For multi-intent (sports + weather), returns the *primary* query only.
    Callers that need every intent must use ``split_web_search_queries``.

    Examples:
      "how're you feeling? and i wonder when that new trailer comes out for trailer 3 for gta 6 hey?"
        → "GTA 6 Trailer 3 release date"
      "search for canada vs morocco score"
        → "canada vs morocco score"
    """
    parts = split_web_search_queries(query)
    if parts:
        # Prefer fact-bearing queries over leftover small-talk if split misfired.
        def _part_score(p: str) -> int:
            low = (p or "").lower()
            score = 0
            if any(
                t in low
                for t in (
                    "weather", "forecast", "score", "schedule", "trailer", "odds",
                    "price", "nhl", "world cup", "gta", "temperature",
                )
            ):
                score += 6
            if re.search(r"\b(vs|versus|tomorrow|today|tonight|release)\b", low):
                score += 3
            if _is_smalltalk_clause(p):
                score -= 10
            score += min(len(p.split()), 6)
            return score

        return max(parts, key=_part_score)
    return normalize_web_search_query_single(query)


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
    # Classify primarily from the *resolved* compact query so a multi-intent
    # original ("oilers game and weather") does not force weather mode onto a
    # sports-only resolved string (that produced "oilers game weather forecast").
    original = apply_spelling_fixes(_normalize_text(original_request))
    raw_resolved = apply_spelling_fixes(_normalize_text(resolved_request or original_request))
    # Already-compact caller query (from multi-split): refine single-intent only.
    # Full chat originals go through multi-aware normalize.
    if resolved_request and _normalize_text(resolved_request) != _normalize_text(original_request):
        resolved = normalize_web_search_query_single(raw_resolved) or raw_resolved
    else:
        resolved = normalize_web_search_query(raw_resolved) or raw_resolved
    resolved = apply_spelling_fixes(resolved)

    low = resolved.lower()
    low_orig = original.lower()
    combined = f"{low} {low_orig}"
    release_date_need = bool(
        _RELEASE_DATE_RE.search(original)
        or _RELEASE_DATE_RE.search(resolved)
        or "release date" in low
    )
    # Mode flags from resolved only (critical — do not OR-in original weather).
    weather = any(term in low for term in _WEATHER_TERMS)
    near_future = bool(
        re.search(r"\b(today|tonight|tomorrow|this weekend|this week|next week)\b", combined)
    )
    who_playing = bool(
        re.search(r"\bwho(?:'s| is|s)?\s+playing\b", combined)
        or re.search(r"\bwho\s+plays\b", combined)
        or re.search(r"\bwhat\s+(?:games?|matches?)\b", combined)
    )
    sports_event = bool(
        re.search(
            r"\b(world cup|fifa|nhl|nba|nfl|mlb|soccer|football|hockey|match(?:es)?|fixture|tournament)\b",
            combined,
        )
    )
    schedule = (
        any(term in low for term in _SCHEDULE_TERMS)
        or any(term in low_orig for term in _SCHEDULE_TERMS)
        or bool(re.search(r"\b(next game|schedule|fixture)\b", low))
        or bool(re.search(r"\b(oilers|flames|canucks|nhl)\b", low) and re.search(r"\b(game|match)\b", low))
        or (who_playing and (near_future or sports_event))
        or (near_future and sports_event and re.search(r"\b(play|playing|game|match|vs|versus)\b", combined))
    )
    live_score = any(term in low for term in _LIVE_SCORE_TERMS) and any(
        sport in low
        for sport in [
            "game", "match", "fifa", "world cup", "soccer", "football",
            "nhl", "nba", "nfl", "mlb", "canada", "morocco", "oilers",
        ]
    )
    recency = (
        any(term in low for term in _RECENT_TERMS)
        or any(t in low for t in ["right now", "currently", "current", "tomorrow"])
        or release_date_need
        or schedule
        or weather
    )
    current_day = any(
        term in combined
        for term in ["today", "tonight", "right now", "currently", "current", "tomorrow", "this weekend"]
    )
    specific_answer = bool(
        live_score
        or schedule
        or weather
        or release_date_need
        or (
            current_day
            and any(
                term in combined
                for term in [
                    "who plays", "who is playing", "who's playing", "what games",
                    "what matches", "events", "available", "odds", "release", "released", "playing",
                ]
            )
        )
        or any(term in low for term in ["odds", "availability", "released", "release date", "score", "scores", "next game"])
    )
    ambiguous = bool(current_subject and re.search(r"\b(deeper|more|again|that|this|it|continue|go further)\b", low_orig))
    mode = "recent" if recency else "general"
    # Never force weather mode onto a pure sports/schedule query.
    sportsy = bool(
        re.search(
            r"\b(oilers|flames|canucks|nhl|nba|nfl|mlb|next game|schedule|fixture|world cup|fifa)\b",
            low,
        )
    ) or schedule
    if weather and not sportsy:
        mode = "weather"
    elif live_score and not schedule:
        mode = "live_score"
    elif schedule or sportsy:
        mode = "schedule"
    elif weather:
        mode = "weather"
    return SearchIntent(
        original_request=original,
        resolved_request=resolved,
        current_subject=_normalize_text(current_subject),
        mode=mode,
        recency_need=recency or weather,
        live_score_need=live_score and mode == "live_score",
        schedule_need=schedule or (mode == "schedule"),
        weather_need=(mode == "weather"),
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
        # Always normalize — never ship raw multi-intent chat as the Tavily string.
        base = normalize_web_search_query(intent.resolved_request or intent.original_request)
        if not base:
            base = _normalize_text(intent.resolved_request or intent.original_request)
        if intent.ambiguous and intent.current_subject and intent.current_subject.lower() not in base.lower():
            base = f"{base} about {intent.current_subject}"
        base = self._clean_query(base)
        candidates: list[SearchCandidate] = [SearchCandidate(base, "cleaned user intent", 0.72, ["base"])]

        # Release-date / trailer style asks need tight factual candidates.
        if "release date" in base.lower() or _RELEASE_DATE_RE.search(intent.original_request or ""):
            year = datetime.now().strftime("%Y")
            candidates = [
                SearchCandidate(f"{base} official", "release date official", 0.95, ["release", "official"]),
                SearchCandidate(f"{base} {year}", "release date year-bound", 0.9, ["release", "recent"]),
                SearchCandidate(base, "release date base", 0.86, ["release"]),
            ]

        if intent.weather_need:
            # Prefer forecast-page queries that return °C/°F numbers, not homepage chrome.
            day_word, cal = _relative_day_labels(intent.resolved_request or intent.original_request or "")
            day_hint = day_word or "today"
            day_part = f"{day_hint} {cal}".strip() if cal else day_hint
            cleaned = re.sub(
                r"\b(please|check|look up|what(?:'s| is)|how(?:'s| is)|"
                r"how(?:'re| are) you(?: doing)?(?: today)?|"
                r"and|also|plus|thanks|thank you|for me)\b",
                " ",
                base,
                flags=re.IGNORECASE,
            )
            cleaned = self._clean_query(cleaned) or base
            # Drop leftover small-talk crumbs
            cleaned = re.sub(r"\b(doing|good|great|fine)\b", " ", cleaned, flags=re.IGNORECASE)
            cleaned = self._clean_query(cleaned) or base
            # Avoid "weather weather tomorrow" duplication if base already is a weather query
            if re.search(r"(?i)\bweather\b", cleaned) and re.search(r"(?i)\b(high|low|forecast|temperature)\b", cleaned):
                primary = cleaned
            else:
                primary = f"{cleaned} weather {day_part} high low temperature forecast"
            candidates = [
                SearchCandidate(primary, "weather forecast numbers", 0.96, ["weather", "forecast"]),
                SearchCandidate(
                    f"{cleaned} {day_part} high low temperature AccuWeather OR Environment Canada",
                    "weather authority dated",
                    0.93,
                    ["weather", "source"],
                ),
                SearchCandidate(
                    f"{cleaned} current weather temperature humidity wind",
                    "weather current conditions",
                    0.9,
                    ["weather", "current"],
                ),
                *candidates,
            ]
        elif intent.live_score_need:
            cleaned = re.sub(r"\b(date|schedule|start time|kickoff|kick-off)\b", "", base, flags=re.IGNORECASE)
            cleaned = self._clean_query(cleaned)
            candidates = [
                SearchCandidate(f"{cleaned} live score result today", "live score intent", 0.96, ["live_score", "current"]),
                SearchCandidate(f"{cleaned} current score live updates", "live score fallback", 0.9, ["live_score", "fallback"]),
                *candidates,
            ]
        elif intent.schedule_need:
            today = datetime.now().strftime("%Y-%m-%d")
            tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            year = datetime.now().strftime("%Y")
            day_word, cal = _relative_day_labels(intent.resolved_request or intent.original_request or "")
            if not day_word:
                day_word = "today"
                cal = datetime.now().strftime("%A %B %d %Y")
            day_iso = tomorrow if day_word == "tomorrow" else today
            # Prefer authority schedule pages over vague "this year" recaps.
            # Pin calendar label so "tomorrow" isn't a whole tournament month.
            candidates = [
                SearchCandidate(f"{base}", "schedule base", 0.94, ["schedule"]),
                SearchCandidate(
                    f"{base} {day_word} {cal} kickoff",
                    f"schedule {day_word} calendar",
                    0.95,
                    ["schedule", day_word, "date"],
                ),
                SearchCandidate(
                    f"{base} {day_iso}",
                    "schedule iso date",
                    0.93,
                    ["schedule", "date"],
                ),
                SearchCandidate(
                    f"{base} fixtures {day_word} ESPN OR FIFA.com",
                    "schedule authority",
                    0.9,
                    ["schedule", "source"],
                ),
            ]
        elif intent.recency_need:
            year = datetime.now().strftime("%Y")
            candidates.append(SearchCandidate(f"{base} latest current {year}", "recency intent", 0.84, ["recent"]))

        deduped: list[SearchCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            # Schedule/sports bases are already compact — do not re-run multi-intent normalize.
            if intent.schedule_need or intent.live_score_need:
                q = _normalize_text(candidate.query)
            else:
                q = self._clean_query(candidate.query)
            key = q.lower()
            if q and key not in seen:
                candidate.query = q
                deduped.append(candidate)
                seen.add(key)
        return deduped[: self.max_candidates]

    def _deeper_schedule_candidates(self, base: str, intent: SearchIntent) -> list[SearchCandidate]:
        """Second-pass queries when first schedule candidates were weak."""
        year = datetime.now().strftime("%Y")
        team = _normalize_text(re.sub(r"(?i)\b(next game|schedule|nhl|date|time|this year)\b", " ", base))
        team = team or base
        return [
            SearchCandidate(f"{team} next game {year}", "deeper next game year", 0.92, ["schedule", "deeper"]),
            SearchCandidate(f"site:nhl.com {team} schedule", "deeper nhl.com", 0.91, ["schedule", "deeper"]),
            SearchCandidate(f"{team} upcoming games schedule ESPN", "deeper espn", 0.88, ["schedule", "deeper"]),
        ]

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

        def _run_candidates(cands: list[SearchCandidate]) -> Optional[GroundedSearchResult]:
            nonlocal best_query, best_output, best_evidence, best_score
            for candidate in cands:
                output = str(execute(candidate.query) or "")
                evidence = self.score_evidence(output, candidate.query, intent)
                # Fetch pages when we need a concrete answer (schedule, weather, live score, etc.)
                if (intent.specific_answer_need or intent.schedule_need or intent.weather_need) and fetch_url:
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
                rejected.append(
                    {"query": candidate.query, "reason": self._rejection_reason(evidence, intent), "score": top_score}
                )
            return None

        hit = _run_candidates(candidates)
        if hit is not None:
            return hit

        # Board-wide deeper pass for schedule/sports when first pass failed.
        if intent.schedule_need:
            deeper = self._deeper_schedule_candidates(best_query or resolved_request, intent)
            # Avoid re-running identical queries
            seen_q = {str(r.get("query") or "").lower() for r in rejected}
            deeper = [c for c in deeper if c.query.lower() not in seen_q]
            if deeper:
                hit = _run_candidates(deeper)
                if hit is not None:
                    return hit

        # Soft-accept: if best evidence still has schedule signals, treat as usable
        # so the model is instructed to report best-available dates instead of giving up.
        # Covers today/tomorrow/near-future fixture asks equally.
        if (intent.schedule_need or intent.current_day_need) and best_evidence:
            for e in best_evidence[:5]:
                hay = f"{e.title} {e.summary}".lower()
                if self._has_schedule_signal(hay) and e.relevance_score >= 0.18:
                    return GroundedSearchResult(
                        chosen_query=best_query,
                        candidates=candidates,
                        evidence=best_evidence,
                        rejected_candidates=rejected,
                        condensed_evidence=self.condense_evidence(best_evidence, best_output),
                        raw_output=best_output,
                        accepted=True,
                    )
                # Near-future slate: "tomorrow's fixtures" pages often list matchups without "next game"
                if intent.current_day_need and e.relevance_score >= 0.22 and (
                    re.search(r"\b(?:vs\.?|versus|@)\b", hay)
                    or re.search(r"\b(?:group\s+[a-h]|round of|knockout|fixture|kickoff)\b", hay)
                ):
                    return GroundedSearchResult(
                        chosen_query=best_query,
                        candidates=candidates,
                        evidence=best_evidence,
                        rejected_candidates=rejected,
                        condensed_evidence=self.condense_evidence(best_evidence, best_output),
                        raw_output=best_output,
                        accepted=True,
                    )

        # Soft-accept entertainment / cast / trailer rumors when named entities appear
        # (prevents "no information about characters" after sources mention Lucia/Jason).
        low_resolved = (intent.resolved_request or "").lower()
        if best_evidence and (
            "character" in low_resolved
            or "cast" in low_resolved
            or "trailer" in low_resolved
            or "lucia" in low_resolved
            or "gta" in low_resolved
        ):
            for e in best_evidence[:5]:
                hay = f"{e.title} {e.summary}".lower()
                has_names = bool(
                    re.search(r"\b(lucia|jason|duval|caminos|leonida|vice city|rockstar)\b", hay)
                )
                has_trailer_window = bool(
                    re.search(
                        r"\b(trailer\s*\d+|summer|fall|spring|202[5-9]|announc|pre-?order|rumor|rumour|expected)\b",
                        hay,
                    )
                )
                if e.relevance_score >= 0.18 and (has_names or has_trailer_window):
                    return GroundedSearchResult(
                        chosen_query=best_query,
                        candidates=candidates,
                        evidence=best_evidence,
                        rejected_candidates=rejected,
                        condensed_evidence=self.condense_evidence(best_evidence, best_output),
                        raw_output=best_output,
                        accepted=True,
                    )

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
            content = str(item.get("content") or item.get("extract") or "")
            summary = str(item.get("summary") or item.get("snippet") or "")
            # Prefer the denser field for weather (raw extract often has °C/°F; titles are SEO fluff).
            fact_blob = content if len(content) > len(summary) else summary
            hay = " ".join(str(item.get(k) or "") for k in ("title", "summary", "content", "page_title")).lower()
            matched = [t for t in terms if t in hay]
            score = min(1.0, 0.12 * len(matched))
            if intent.weather_need:
                score += 0.55 if self._has_weather_signal(hay) else -0.3
                # Prefer forecast domains / pages over generic "weather" landing pages
                if any(d in hay for d in ("accuweather", "weather.com", "environment canada", "weather.gc.ca", "wunderground", "theweathernetwork")):
                    score += 0.12
                if any(noise in hay for noise in ("cookie", "sign in", "subscribe", "advertisement", "privacy policy")):
                    score -= 0.15
            elif intent.live_score_need:
                score += 0.55 if self._has_score_signal(hay) else -0.25
                if any(t in hay for t in ["schedule", "date", "kickoff", "start time"]) and not self._has_score_signal(hay):
                    score -= 0.25
            elif intent.schedule_need:
                # Schedule pages are date-heavy by nature — boost matchup/date signals,
                # never penalize as "nav only" the way live-score does.
                score += 0.52 if self._has_schedule_signal(hay) else -0.12
                if any(d in hay for d in ("nhl.com", "espn.com", "cbssports", "rotowire", "theathletic")):
                    score += 0.1
            elif intent.specific_answer_need:
                score += 0.38 if self._has_specific_answer_signal(hay, intent) else -0.22
                if self._looks_like_date_or_nav_only(hay, intent):
                    score -= 0.25
            if intent.recency_need and str(item.get("recency_bucket") or "") in {"breaking", "recent"}:
                score += 0.18
            if item.get("url"):
                score += 0.05
            # For weather, keep more of the fact-dense extract in the summary field
            if intent.weather_need and self._has_weather_signal(fact_blob):
                display_summary = _normalize_text(fact_blob)[:900]
            else:
                display_summary = str(item.get("summary") or item.get("content") or "")[:700]
            rejection = "" if score >= self.relevance_threshold else "Evidence did not strongly match the requested intent."
            evidence.append(GroundedEvidence(
                title=str(item.get("title") or "Untitled source"),
                url=str(item.get("url") or ""),
                summary=display_summary,
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
        # Prefer fact-bearing weather snippets first when mixed with nav noise
        def _fact_rank(e: GroundedEvidence) -> tuple:
            hay = f"{e.title} {e.summary}".lower()
            has_wx = 1 if self._has_weather_signal(hay) else 0
            return (has_wx, e.relevance_score)

        usable = sorted(usable, key=_fact_rank, reverse=True)
        lines = []
        for idx, item in enumerate(usable[:6], start=1):
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
        if intent.weather_need:
            # Accept if any of top results carry real weather numbers (not just page titles).
            for e in evidence[:4]:
                hay = f"{e.title} {e.summary}".lower()
                if e.relevance_score >= max(0.28, self.relevance_threshold - 0.05) and self._has_weather_signal(hay):
                    return True
            return False
        if intent.live_score_need:
            return top.relevance_score >= self.relevance_threshold and self._has_score_signal(top.summary.lower() + " " + top.title.lower())
        if intent.schedule_need:
            # Accept if any of top results have date/matchup/next-game signals.
            for e in evidence[:5]:
                hay = f"{e.title} {e.summary}".lower()
                if e.relevance_score >= max(0.24, self.relevance_threshold - 0.1) and self._has_schedule_signal(hay):
                    return True
            return False
        if intent.specific_answer_need:
            hay = top.summary.lower() + " " + top.title.lower()
            return top.relevance_score >= self.relevance_threshold and self._has_specific_answer_signal(hay, intent)
        return top.relevance_score >= self.relevance_threshold or (len(evidence) >= 2 and top.relevance_score >= 0.25)

    def _rejection_reason(self, evidence: list[GroundedEvidence], intent: SearchIntent) -> str:
        if not evidence:
            return "No usable evidence returned."
        if intent.live_score_need:
            return "Evidence did not look like a live/current score result."
        if intent.schedule_need:
            return "Evidence did not contain a clear next-game date/time/matchup."
        if intent.specific_answer_need:
            return "Evidence did not contain the requested specific current answer."
        return evidence[0].rejection_reason or "Evidence relevance below threshold."

    def _intent_terms(self, intent: SearchIntent) -> list[str]:
        words = re.findall(r"[a-z0-9]{3,}", (intent.resolved_request + " " + intent.current_subject).lower())
        stop = {"what", "when", "where", "which", "with", "about", "please", "search", "deeper", "right", "currently", "today", "doing", "check"}
        terms = [w for w in words if w not in stop]
        if intent.weather_need:
            terms.extend(["weather", "forecast", "temperature", "high", "low", "°c", "humidity", "wind"])
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

    def _has_weather_signal(self, text: str) -> bool:
        """Concrete forecast/condition facts — not marketing nav from weather sites."""
        hay = str(text or "").lower()
        if re.search(r"\d+\s*°\s*[cf]\b|\d+\s*degrees|\bhigh(?:s)?\s*(?:near|around|of)?\s*-?\d+|\blow(?:s)?\s*(?:near|around|of)?\s*-?\d+", hay):
            return True
        if re.search(r"-?\d{1,2}\s*/\s*-?\d{1,2}\s*°", hay):
            return True
        # temp + weather vocab together
        if re.search(r"-?\d{1,3}\b", hay) and any(
            w in hay for w in ("°", "celsius", "fahrenheit", "humidity", "wind", "km/h", "mph", "precip", "chance of rain", "feels like")
        ):
            return True
        return False

    def _has_schedule_signal(self, text: str) -> bool:
        """Next-game / fixture facts: date, time, opponent — not pure schedule nav chrome."""
        hay = str(text or "").lower()
        # Pure schedule hub chrome (date + Standings/Tickets/etc.) is NOT a next-game fact.
        nav_hits = sum(
            1
            for word in ("standings", "fixtures", "results", "teams", "stats", "tickets", "scores", "news")
            if word in hay
        )
        has_time = bool(
            re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|et|pt|ct|mt|utc|edt|pdt|est|pst|mst|cst)\b", hay)
        )
        has_date = bool(
            re.search(
                r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,?\s+20\d{2})?\b",
                hay,
            )
            or re.search(r"\b20\d{2}-\d{2}-\d{2}\b", hay)
            or re.search(
                r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.{0,40}\b\d{1,2}\b",
                hay,
            )
        )
        has_matchup = bool(re.search(r"\b(?:vs\.?|versus|@)\b", hay) or re.search(r"\bat\s+[a-z]", hay))
        has_named = bool(
            re.search(r"\b[a-z][a-z .'-]{2,}\s+(?:vs\.?|versus|@|at)\s+[a-z][a-z .'-]{2,}\b", hay)
        )
        has_next = any(
            p in hay
            for p in (
                "next game",
                "upcoming game",
                "upcoming games",
                "next match",
                "puck drop",
                "faceoff",
                "tipoff",
                "tip-off",
            )
        )
        # Nav hub: "Sunday July 5 Schedule Results Standings Teams Stats Tickets"
        if nav_hits >= 3 and not (has_named or has_matchup or has_time or has_next):
            return False
        # Date+matchup, named matchup, next-game + date/time, or time + game language
        if has_named or (has_matchup and (has_date or has_time)):
            return True
        if has_next and (has_date or has_time or has_matchup):
            return True
        if has_time and any(w in hay for w in ("game", "match", "play", "host", "visit", "face", "oilers", "flames")):
            return True
        if has_date and has_matchup:
            return True
        return False

    def _has_specific_answer_signal(self, text: str, intent: SearchIntent) -> bool:
        hay = str(text or "").lower()
        if intent.weather_need:
            return self._has_weather_signal(hay)
        if intent.live_score_need:
            return self._has_score_signal(hay)
        if "odds" in (intent.resolved_request or "").lower():
            return bool(re.search(r"[+-]\d{3,4}\b|\b\d+\.\d{2}\b|\bodds\b.*(?:spread|moneyline|total)", hay))
        if intent.schedule_need or intent.current_day_need:
            return self._has_schedule_signal(hay)
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
        q = normalize_web_search_query(query) or _normalize_text(query)
        q = apply_spelling_fixes(q)
        q = re.sub(
            r"\b(can you|could you|please|tell me|what is|what's|search the internet|look up|i wonder|hey|hi|hello|"
            r"do a deeper search|deeper search|dig deeper|go deeper|search deeper)\b",
            "",
            q,
            flags=re.IGNORECASE,
        )
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


# Marker so wrappers can detect already-grounded tool output and avoid double-grounding.
GROUNDED_SEARCH_MARKER = "[GROUNDED_SEARCH]"


def is_grounded_search_output(text: str) -> bool:
    return GROUNDED_SEARCH_MARKER in str(text or "")


def format_grounded_tool_output(result: GroundedSearchResult) -> str:
    """Format grounded search for LLM/tool consumers.

    Accepted evidence is condensed for synthesis. Rejected / insufficient evidence
    returns a structured block that forbids inventing a confident answer.
    """
    chosen = _normalize_text(result.chosen_query)
    if result.accepted:
        body = (result.condensed_evidence or result.raw_output or "").strip()
        return (
            f"{GROUNDED_SEARCH_MARKER} accepted=true query={chosen}\n"
            "Use ONLY the evidence below. Prefer concrete facts from these sources. "
            "Do not invent scores, times, temperatures, or outcomes not present here. "
            "For weather: quote high/low/current temps and conditions when present; "
            "never tell the user to 'check AccuWeather' if numbers are already below. "
            "For schedules/next games (today OR tomorrow OR near-future): if a date, time, "
            "or opponent appears below, report it clearly — do not tell the user to check "
            "NHL.com/FIFA when the fact is present. "
            "Never claim you cannot search the web — search already succeeded.\n\n"
            f"{body}"
        ).strip()

    reason = "No candidate reached the relevance threshold."
    if result.rejected_candidates:
        reason = str(result.rejected_candidates[-1].get("reason") or reason)
    best = _normalize_text(result.condensed_evidence or result.raw_output) or "(no usable snippets)"
    rejected_lines = []
    for item in (result.rejected_candidates or [])[:5]:
        rejected_lines.append(
            f"- query={_normalize_text(item.get('query'))}; "
            f"score={item.get('score')}; "
            f"reason={_normalize_text(item.get('reason'))}"
        )
    rejected_block = "\n".join(rejected_lines) if rejected_lines else "- (none recorded)"
    return (
        f"{GROUNDED_SEARCH_MARKER} accepted=false\n"
        "SEARCH_EVIDENCE_INSUFFICIENT: true\n"
        f"REASON: {reason}\n"
        f"CHOSEN_QUERY: {chosen}\n"
        "INSTRUCTION: Do NOT invent an answer. Do NOT claim a specific score, result, schedule, "
        "odds, or release fact unless it appears in BEST_AVAILABLE_EVIDENCE below. "
        "If BEST_AVAILABLE_EVIDENCE contains dates, opponents, times, or temps, report those as "
        "best-available (with uncertainty) — do NOT tell the user to go check an official site "
        "when usable fragments are already present. Only say evidence was insufficient when "
        "BEST_AVAILABLE_EVIDENCE has no relevant fragments. "
        "CRITICAL: web_search DID run. Never claim your tools cannot search the web, that search "
        "is disabled, or that you lack web access — say only that this particular evidence was thin.\n"
        f"REJECTED_CANDIDATES:\n{rejected_block}\n"
        f"BEST_AVAILABLE_EVIDENCE:\n{best}"
    ).strip()


def build_research_run(*, run_id: str, tool_name: str, tool_input: str, output: str, at: float) -> Optional[dict[str, Any]]:
    if tool_name != "web_search":
        return None
    query = extract_research_query(tool_input)
    raw = str(output or "").strip()
    if not raw or raw.lower().startswith("search failed") or raw.lower().startswith("no search results"):
        evidence: list[dict[str, Any]] = []
    else:
        # Strip grounded headers so the research panel still parses numbered blocks.
        parse_src = raw
        if is_grounded_search_output(raw):
            # Prefer BEST_AVAILABLE_EVIDENCE / body after the instruction block.
            marker_split = re.split(r"BEST_AVAILABLE_EVIDENCE:\s*", raw, maxsplit=1, flags=re.IGNORECASE)
            if len(marker_split) == 2:
                parse_src = marker_split[1]
            else:
                # accepted=true body after blank line following header
                parts = raw.split("\n\n", 1)
                parse_src = parts[1] if len(parts) == 2 else raw
        evidence = [_normalize_evidence(item, tool_name=tool_name, fallback_query=query, position=index) for index, item in enumerate(_parse_numbered_blocks(parse_src), start=1)]

    evidence = [item for item in evidence if item.get("title") or item.get("url") or item.get("summary")]
    mode = _infer_mode(query)
    grounded_accepted: Optional[bool] = None
    if is_grounded_search_output(raw):
        grounded_accepted = "accepted=true" in raw.splitlines()[0].lower() if raw else None
        if grounded_accepted is None:
            grounded_accepted = "SEARCH_EVIDENCE_INSUFFICIENT: true" not in raw
    return {
        "id": run_id,
        "tool": tool_name,
        "query": query,
        "at": at,
        "mode": mode,
        "recency_intent": mode == "recent",
        "evidence_count": len(evidence),
        "evidence": evidence,
        "grounded": is_grounded_search_output(raw),
        "grounded_accepted": grounded_accepted,
    }
