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

# Speech/typo fixes that are *structural* (day words, politeness, compound forms).
# Not entity/team routing maps — only words that break *parsing* when mangled.
_SPELLING_FIXES = {
    "wordlcup": "world cup",
    "worldcup": "world cup",
    "tommrrow": "tomorrow",
    "tommorow": "tomorrow",
    "tommorrow": "tomorrow",
    "tomorow": "tomorrow",
    "tomorro": "tomorrow",
    "todya": "today",
    # Politeness STT (must not become its own search: "pelsae check")
    "pelsae": "please",
    "plese": "please",
    "plase": "please",
    "pealse": "please",
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

# Stopwords stripped when extracting free-form team / place / vs sides (structure only).
_SPORTS_STOP = {
    "when", "is", "the", "next", "upcoming", "game", "games", "match", "matches",
    "schedule", "for", "of", "what", "time", "times", "a", "an", "do", "does",
    "did", "play", "plays", "playing", "who", "whom", "whose", "are", "was",
    "were", "will", "would", "can", "could", "please", "check", "look", "up",
    "get", "me", "my", "our", "their", "and", "or", "also", "then", "today",
    "tonight", "tomorrow", "this", "that", "week", "weekend", "score", "scores",
    "live", "right", "now", "fixture", "fixtures", "kickoff", "kick", "off",
    "versus", "vs", "against", "at", "on", "in", "to", "from", "with", "about",
    "happening", "being", "played", "explain", "tell", "show", "find", "search",
}


def apply_spelling_fixes(text: str) -> str:
    """Apply structural speech/typo fixes (day words, world-cup compound). No entity map."""
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
        if word.isupper():
            return fixed.upper()
        if word[:1].isupper():
            return fixed[:1].upper() + fixed[1:]
        return fixed

    return re.sub(r"[A-Za-z][A-Za-z']*", _fix_token, s)


_PLACE_STOP = {
    "the", "a", "an", "my", "our", "today", "tonight", "tomorrow",
    "weather", "forecast", "temperature", "temp", "me", "you", "us",
    "this", "that", "week", "weekend", "morning", "evening", "night",
    "what", "whats", "what's", "check", "look", "get", "tell", "please",
    "can", "could", "would", "how", "is", "are", "like", "for", "me",
    "check the", "what the", "whats the", "what's the", "the weather",
    # League/sport tokens must never become "city" from "for fifa" / "in nhl"
    "fifa", "nhl", "nba", "nfl", "mlb", "uefa", "mls", "soccer", "football",
    "hockey", "basketball", "baseball", "world", "cup", "matches", "match",
    "games", "game", "score", "schedule", "fixture", "fixtures",
}


def _trim_place_candidate(place: str) -> str:
    """Keep leading place tokens; stop before day/weather/chat stopwords."""
    toks = []
    for tok in (place or "").split():
        if tok.lower() in _PLACE_STOP:
            break
        if re.search(r"(?i)^(high|low|degrees?)$", tok):
            break
        toks.append(tok)
    return " ".join(toks).strip()


def _looks_like_place(place: str) -> bool:
    p = _trim_place_candidate(place)
    if not p or len(p) < 2:
        return False
    low = p.lower()
    if low in _PLACE_STOP:
        return False
    if any(tok in _PLACE_STOP for tok in low.split()):
        return False
    if re.search(r"(?i)\b(high|low|degrees?|check|weather|forecast)\b", p):
        return False
    return True


def _infer_city_from_text(text: str) -> str:
    """
    Structural place extraction only — never invents a city from a team nickname.

    Accepts: 'in Denver', 'weather for Osaka', leading 'Seattle weather…'.
    Rejects: team→home-city maps and fixed city whitelists.
    """
    raw = text or ""
    # Explicit preposition + place (allow lower or Title case from speech)
    m = re.search(
        r"(?i)\b(?:in|for|near|around|at)\s+([A-Za-z][A-Za-z.'-]{1,}(?:\s+[A-Za-z][A-Za-z.'-]{1,}){0,2})\b",
        raw,
    )
    if m:
        place = _trim_place_candidate(m.group(1).strip())
        if _looks_like_place(place):
            return place.title() if place.islower() else place
    # Leading place before weather/forecast: "Osaka weather tomorrow"
    m2 = re.search(
        r"(?i)^\s*([A-Za-z][A-Za-z.'-]{1,}(?:\s+[A-Za-z][A-Za-z.'-]{1,}){0,2})\s+"
        r"(?:weather|forecast|temperature|temps?)\b",
        raw.strip(),
    )
    if m2:
        place = _trim_place_candidate(m2.group(1).strip())
        if _looks_like_place(place):
            return place.title() if place.islower() else place
    return ""


def _is_weather_clause(text: str) -> bool:
    low = (text or "").lower()
    if any(t in low for t in _WEATHER_TERMS):
        return True
    # Spoken shorthand: "what's the temp tomorrow"
    if re.search(r"\btemp(?:s|erature|eratures)?\b", low):
        return True
    return False


def _is_local_or_software_game_context(text: str) -> bool:
    """Video-game / code-project language — must NOT be classified as sports."""
    low = (text or "").lower()
    if not low:
        return False
    if re.search(
        r"\b(desktop|folder|directory|codebase|repo|workspace|file_list|file_read|"
        r"html|css|javascript|typescript|python|godot|unity|unreal|pygame|"
        r"2d|3d|shooter|platformer|roguelike|rpg|sandbox|indie)\b",
        low,
    ):
        return True
    # "build/code/make a game" / "start the X game" with software framing
    if re.search(r"\b(build|code|make|create|scaffold|implement|develop)\b.{0,40}\bgame\b", low):
        return True
    if re.search(r"\bgame\b.{0,40}\b(project|folder|desktop|files?|code|scan|together)\b", low):
        return True
    if re.search(r"\b(project|folder|desktop|files?|code|scan)\b.{0,40}\bgame\b", low):
        return True
    return False


def _is_schedule_or_sports_clause(text: str) -> bool:
    """True for schedules, fixtures, leagues — structural, not team-name lists."""
    low = (text or "").lower()
    # Software / local project "game" is never sports
    if _is_local_or_software_game_context(low):
        return False
    if any(t in low for t in _SCHEDULE_TERMS):
        return True
    if re.search(r"\b(next|upcoming)\s+(game|match|matches|fixture|fixtures)\b", low):
        return True
    # Plural matches/games alone + competition or day
    if re.search(r"\b(matches|games|fixtures)\b", low) and re.search(
        r"\b(today|tonight|tomorrow|this week|weekend|schedule|happening|playing|fifa|world cup|"
        r"nhl|nba|nfl|mlb|soccer|football|premier|uefa|mls|hockey|basketball)\b",
        low,
    ):
        return True
    # game/match/schedule + league OR vs-structure OR residual team-ish noun
    # Bare "game" + residual words alone is too weak (catches "2d shooter game")
    if re.search(r"\b(match|schedule|fixture)\b", low) and (
        re.search(
            r"\b(nhl|nba|nfl|mlb|fifa|world cup|soccer|football|hockey|basketball|uefa|mls)\b",
            low,
        )
        or re.search(r"\b(?:vs\.?|versus|against)\b", low)
        or _extract_teamish_phrase(low)
    ):
        return True
    if re.search(r"\bgame\b", low) and (
        re.search(
            r"\b(nhl|nba|nfl|mlb|fifa|world cup|soccer|football|hockey|basketball|uefa|mls|"
            r"next game|upcoming game|score|kickoff)\b",
            low,
        )
        or re.search(r"\b(?:vs\.?|versus|against)\b", low)
    ):
        return True
    # League + day/playing without the word "match"
    if re.search(r"\b(fifa|world cup|uefa|premier league|champions league)\b", low) and re.search(
        r"\b(today|tonight|tomorrow|schedule|playing|fixtures?|matches?|games?)\b",
        low,
    ):
        return True
    return False


def _clean_match_side(s: str) -> str:
    """Normalize one free-form match side (any nation/club — no whitelist)."""
    s = _normalize_text(s)
    for _ in range(4):
        nxt = re.sub(
            r"(?i)^(the|a|an|who|what|which|when|wins?|win|plays?|playing|with|between)\s+",
            "",
            s,
        )
        if nxt == s:
            break
        s = nxt
    s = re.sub(
        r"(?i)\b(fifa|world\s*cup|nhl|nba|nfl|mlb|uefa|premier\s*league|champions\s*league|"
        r"soccer|football|hockey|basketball|baseball)\b",
        " ",
        s,
    )
    s = re.sub(
        r"(?i)\s+\b(kickoff|time|schedule|fixtures?|matches?|games?|today|tomorrow|tonight|"
        r"start|starts|starting|et|pt|mt|ct|utc|gmt|mnt|mst|est|pst)\b.*$",
        "",
        s,
    )
    s = _normalize_text(s)
    toks = s.split()
    if len(toks) > 3:
        s = " ".join(toks[-3:])
    return _normalize_text(s)


def _extract_vs_sides(text: str) -> str:
    """
    Structural matchup parse — free-form sides, no country whitelist.

    Accepts:
      France vs Morocco | A versus B | X against Y
      with France and Morocco | between A and B
      game with France and maracoo  (STT OK — keep free-form spelling)
    """
    raw = text or ""
    patterns = (
        # Classic vs
        r"(?iu)\b([\w][\w .'-]{0,40}?)\s+(?:vs\.?|versus|against)\s+([\w][\w .'-]{0,40}?)\b",
        # with/between X and Y (live: "fifa game with france and maracoo")
        r"(?iu)\b(?:with|between)\s+([\w][\w'-]{1,30})\s+and\s+([\w][\w'-]{1,30})\b",
        # "with france game" / "france game today" (opponent unknown — keep named side)
        r"(?iu)\b(?:with|for)\s+([\w][\w'-]{2,30})\s+(?:game|match|fixture)\b",
        # game/match ... X and Y
        r"(?iu)\b(?:game|match|fixture|matchup)\s+(?:with\s+|between\s+)?"
        r"([\w][\w'-]{1,30})\s+and\s+([\w][\w'-]{1,30})\b",
    )
    for pat in patterns:
        m = re.search(pat, raw)
        if not m:
            continue
        # One named side only (e.g. "with france game") — still useful
        if m.lastindex == 1:
            a = _clean_match_side(m.group(1))
            if a and len(a) >= 2 and a.lower() not in _SPORTS_STOP:
                if a.lower() not in {"time", "what", "when", "start", "does", "the", "game", "match"}:
                    return a
            continue
        a, b = _clean_match_side(m.group(1)), _clean_match_side(m.group(2))
        if not a or not b or len(a) < 2 or len(b) < 2:
            continue
        if a.lower() in _SPORTS_STOP or b.lower() in _SPORTS_STOP:
            continue
        # Reject obvious non-sides ("time and the")
        if a.lower() in {"time", "what", "when", "start", "does", "the"} or b.lower() in {
            "time", "what", "when", "start", "does", "the", "today", "tomorrow",
        }:
            continue
        return f"{a} {b}"
    return ""


def _extract_teamish_phrase(text: str) -> str:
    """
    Residual team/org phrase after stripping schedule stopwords.
    Works for any club/nation — not a nickname map.
    """
    words = re.findall(r"(?u)[\w]+(?:['-][\w]+)?", text or "")
    # Drop pure digits
    words = [w for w in words if not w.isdigit()]
    keep = [w for w in words if w.lower() not in _SPORTS_STOP]
    # Drop pure league tokens from the "team" phrase (kept separately by caller)
    leagueish = {
        "nhl", "nba", "nfl", "mlb", "fifa", "uefa", "mls", "soccer", "football",
        "hockey", "basketball", "world", "cup", "premier", "league", "champions",
    }
    keep = [w for w in keep if w.lower() not in leagueish]
    if not keep:
        return ""
    # Cap length so we don't re-absorb the whole chatty utterance
    phrase = " ".join(keep[:5])
    if len(phrase) < 2:
        return ""
    return phrase


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
    if not _is_local_or_software_game_context(low) and (
        _is_schedule_or_sports_clause(low)
        or re.search(
            r"\b(fifa|world cup|nhl|nba|nfl|mlb|soccer|football|premier league|"
            r"match(?:es)?|score(?:s)?|standings|playoff)\b",
            low,
        )
    ):
        domains.add("sports")
    if re.search(
        r"\b(stock|share price|nasdaq|s&p|dow jones|bitcoin|btc|ethereum|eth|crypto|ticker)\b",
        low,
    ):
        domains.add("finance")
    if re.search(
        r"\b(movie|film|trailer|netflix|show|series|album|box office|dlc|sequel|pre-?order)\b",
        low,
    ) or _has_trailer_intent(low) or _has_character_cast_intent(low) or _has_product_title_context(low):
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
    """
    Compact sports/schedule search string via structure (league, vs-sides, day, TZ).
    Never rewrites to a hard-coded franchise string (no team→canonical map).
    """
    q = _normalize_text(apply_spelling_fixes(text or ""))
    low = q.lower()
    day, cal = _relative_day_labels(low)
    explicit = _explicit_calendar_date_label(low)
    # Timezone conversion follow-ups must NOT collapse to a fresh full-day slate
    # (lost prior match/clock context on "what time MNT?").
    tz_convert = bool(
        re.search(
            r"\b("
            r"timezone|time\s*zone|convert\s+local|my\s+time|local\s+time|"
            r"mountain\s+time|pacific\s+time|eastern\s+time|central\s+time|"
            r"\bmnt\b|\bmst\b|\bmdt\b|\best\b|\bedt\b|\bpst\b|\bpdt\b|\butc\b|\bgmt\b"
            r")\b",
            low,
        )
    )
    side = _extract_vs_sides(q)
    clock = ""
    tm = re.search(
        r"\b(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?|am|pm))\b",
        low,
        flags=re.IGNORECASE,
    )
    if tm:
        clock = tm.group(1).strip()
    league = ""
    if re.search(r"\b(fifa|world cup)\b", low):
        league = "FIFA World Cup"
    elif re.search(r"\bnhl\b|\bhockey\b", low):
        league = "NHL"
    elif re.search(r"\bnba\b|\bbasketball\b", low):
        league = "NBA"
    elif re.search(r"\bnfl\b", low):
        league = "NFL"
    elif re.search(r"\bmlb\b|\bbaseball\b", low):
        league = "MLB"
    elif re.search(r"\buefa|premier league|champions league\b", low):
        league = "soccer"

    def _pin() -> str:
        if day and cal:
            return f"{day} {cal}".strip()
        return day or explicit or ""

    if tz_convert:
        tz_bits = re.findall(
            r"\b(mnt|mst|mdt|est|edt|pst|pdt|cst|cdt|utc|gmt|mt|et|pt|"
            r"mountain|pacific|eastern|central)\b",
            low,
        )
        tz_label = " ".join(dict.fromkeys(tz_bits[:3])) or "Mountain Time"
        pin = _pin() or "today"
        if not side and not league:
            team = _extract_teamish_phrase(q)
            head = team or "match"
            return f"{head} kickoff {pin} convert to {tz_label} timezone ET schedule".strip()
        if not side:
            head = league or "sports"
            return (
                f"{head} {pin} full match list each kickoff time "
                f"ET and {tz_label} convert timezone schedule"
            ).strip()
        parts = [league or "match", side, "kickoff"]
        if clock:
            parts.append(clock)
        parts.append(f"what time is that in {tz_label} convert timezone ET")
        if pin:
            parts.append(pin)
        return " ".join(p for p in parts if p).strip()

    # League slate (World Cup / NHL / …) — demand concrete kickoffs, pin calendar
    if league and re.search(r"\b(fifa|world cup)\b", low):
        if side:
            base = f"{league} {side} kickoff time ET schedule fixtures"
        else:
            base = f"{league} match list kickoff times ET each game schedule fixtures"
        if clock:
            base = f"{base} {clock}"
        pin = _pin()
        if pin:
            return f"{base} {pin}".strip()
        return base

    # Next/upcoming game for any free-form team phrase (no franchise rewrite)
    if re.search(r"\b(next|upcoming)\s+(game|match)\b", low) or (
        re.search(r"\b(when|schedule)\b", low) and re.search(r"\b(game|match|play)\b", low)
    ):
        team = _extract_teamish_phrase(q)
        if team:
            bits = [team, "next game schedule"]
            if league:
                bits.append(league)
            pin = _pin()
            if pin:
                bits.append(pin)
            return " ".join(bits).strip()

    # Generic matches/games happening + relative day or explicit calendar date
    if re.search(r"\b(matches|games|fixtures|playing|matchup)\b", low) and (day or explicit):
        cleaned = re.sub(
            r"(?i)\b(what|which|are|is|happening|for|the|a|an|also|just|wondering|sorry|not|"
            r"then|being|played|explain|me|who|when|next)\b",
            " ",
            q,
        )
        cleaned = _normalize_text(cleaned) or q
        pin = _pin()
        if side:
            head = f"{league} {side}".strip() if league else side
            return f"{head} schedule fixtures {pin}".strip()
        if re.search(r"\b(fifa|world cup|nhl|nba|nfl|mlb|soccer|football)\b", cleaned.lower()):
            return f"{cleaned} schedule fixtures {pin}".strip()
        if league:
            return f"{league} schedule fixtures {pin}".strip()
        team = _extract_teamish_phrase(cleaned)
        if team:
            return f"{team} schedule fixtures {pin}".strip()
        return f"sports games matches schedule fixtures {pin}".strip()

    # Bare vs-sides without day
    if side:
        bits = [league, side, "schedule fixtures"]
        pin = _pin()
        if pin:
            bits.append(pin)
        return " ".join(b for b in bits if b).strip()

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
    cleaned = re.sub(
        r"(?i)\b(the|a|an|for|me|my|you|your|tomorrow|today|tonight|check|look|up|"
        r"please|can|could|would|get|tell|like|whats|what's|how|is|are)\b",
        " ",
        cleaned,
    )
    cleaned = _normalize_text(cleaned)
    # Only keep cleaned if it looks like a place name (short, no leftover chatter/day words)
    if (
        cleaned
        and 2 <= len(cleaned) <= 40
        and len(cleaned.split()) <= 4
        and not re.search(
            r"(?i)\b(high|low|going|be|what|matches|fifa|check|tho|though|well|hope)\b",
            cleaned,
        )
    ):
        return f"{cleaned} weather {day_part} high low temperature forecast"
    return f"weather {day_part} high low temperature forecast"


def _clean_title_entity(raw: str) -> str:
    """Strip chat filler from a free-form title/product span."""
    s = _normalize_text(raw)
    s = re.sub(
        r"(?i)\b(i need you to|can you|could you|please|search for|search when|look up|find out|explain to me)\b",
        " ",
        s,
    )
    s = re.sub(
        r"(?i)^(the|a|an|new|latest|that|this|for|about|of|when|does|is|will|do|did|search)\s+",
        "",
        s,
    )
    # Chat particles that leak into titles from social openers ("gta 6 hey")
    s = re.sub(r"(?i)\b(hey|hi|hello|yo|sup|please|thanks|thank you)\b", " ", s)
    s = re.sub(
        r"(?i)\b(the|a|an|new|latest|please|trailer|release|price|cost|characters?|cast|search|when)\b",
        " ",
        s,
    )
    s = _normalize_text(s)
    # Trailing roman/word sequels only (any title): "Foo VI" / "Foo six" → "Foo 6"
    s = re.sub(r"(?i)\s+(vi|six)\s*$", " 6", s)
    s = re.sub(r"(?i)\s+(v|five)\s*$", " 5", s)
    if len(s) > 40:
        s = " ".join(s.split()[-4:])
    return s


def _extract_title_entity(text: str) -> str:
    """
    Free-form product/title extraction from the user utterance.

    No whitelist of games/movies — only structural patterns
    (\"X trailer 3\", \"when does X come out\", \"price of X\").
    """
    work = _normalize_text(apply_spelling_fixes(text or ""))
    if not work:
        return ""
    # Light structural cleanup: trailing roman/word sequels only (no franchise aliases)
    work = re.sub(r"(?i)\s+(vi|six)\b", " 6", work)

    patterns = (
        # Short subject: "when X is released" (X <= 5 tokens)
        r"(?i)\bwhen\s+((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,4})\s+is\s+(?:released|launching|dropping)\b",
        r"(?i)\bwhen\s+(?:does|is|will)\s+((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,4})\s+(?:come\s+out|release|launch|drop)\b",
        r"(?i)\bhow much (?:does|will|is)\s+((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,4})\s+(?:cost|be|go for)\b",
        r"(?i)\b(?:price of|cost of)\s+((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,5})\b",
        r"(?i)\btrailer\s*(?:#?\s*)?(?:\d+|three|two|one)\s+(?:for|of)\s+((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,5})\b",
        r"(?i)\b((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,5})\s+trailer\s*(?:#?\s*)?(?:\d+|three|two|one)\b",
        r"(?i)\b(?:characters?|cast|protagonists?)\s+(?:of|in|for)\s+((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,5})\b",
        r"(?i)\b(?:for|about)\s+(?:the\s+)?(?:new\s+)?((?:[a-z0-9][\w'.-]*)(?:\s+[a-z0-9][\w'.-]*){0,5})\s+(?:trailer|release|price|cast)\b",
    )
    for pat in patterns:
        m = re.search(pat, work)
        if m:
            ent = _clean_title_entity(m.group(1))
            bad = {"i need", "you to", "search when", "search", "when", "how much"}
            if ent and len(ent) >= 2 and ent.lower() not in bad and not ent.lower().startswith("i need"):
                return ent
    return ""


def _has_product_title_context(text: str) -> bool:
    """True when utterance carries a title/product entity (any franchise — not GTA-only)."""
    if _extract_title_entity(text):
        return True
    # Explicit media/product markers with some content around them
    low = (text or "").lower()
    return bool(
        re.search(r"\b(trailer|pre-?order|box office|dlc|sequel|season pass)\b", low)
        and len((text or "").split()) >= 3
    )


# Backward-compatible alias (tests/history) — now product-general
def _has_gta_context(text: str) -> bool:
    return _has_product_title_context(text)


def _has_trailer_intent(text: str) -> bool:
    return bool(re.search(r"(?i)\btrailer\s*(?:#?\s*)?(\d+|three|two|one)\b", text or ""))


def _has_character_cast_intent(text: str) -> bool:
    low = (text or "").lower()
    return bool(
        re.search(r"\b(characters?|cast|protagonists?|playable)\b", low)
        or re.search(r"\bnames of the (?:characters?|cast)\b", low)
        or re.search(r"\bwho (?:is|are) (?:in|the) (?:cast|characters?|game|movie|film)\b", low)
        or re.search(r"\bwho (?:is|are) (?:playable|protagonists?)\b", low)
    )


def _has_product_release_intent(text: str) -> bool:
    low = (text or "").lower()
    if not (_has_product_title_context(low) or _extract_title_entity(text)):
        # "when does it release" with product only in subject is handled via rebind
        if not re.search(r"\b(it|this|that|game|movie|show|album)\b", low):
            return False
    return bool(
        re.search(
            r"\b(release(?:s|d)?|come\s+out|coming\s+out|launch(?:es|ing)?|drop(?:s|ping)?|"
            r"when\s+(?:does|is|will)|out\s+on)\b",
            low,
        )
    )


def _has_product_price_intent(text: str) -> bool:
    low = (text or "").lower()
    return bool(
        re.search(
            r"\b(how much|cost(?:s|ing)?|price|pricing|msrp|pre-?order|edition(?:s)?|"
            r"money it costs|dollars?)\b",
            low,
        )
    )


def _has_gta_release_intent(text: str) -> bool:
    return _has_product_release_intent(text)


def _has_gta_price_intent(text: str) -> bool:
    return _has_product_price_intent(text)


def _normalize_product_trailer_query(text: str, full_context: str = "") -> str:
    blob = f"{text or ''} {full_context or ''}"
    entity = _extract_title_entity(blob) or _extract_title_entity(text) or "trailer"
    m = re.search(r"(?i)\btrailer\s*(?:#?\s*)?(\d+|three|two|one)\b", blob)
    num = ""
    if m:
        raw_n = m.group(1).lower()
        num = {"one": "1", "two": "2", "three": "3"}.get(raw_n, raw_n)
    if num:
        return f"{entity} Trailer {num} release date announcement"
    return f"{entity} trailer release date announcement"


def _normalize_product_cast_query(text: str = "") -> str:
    entity = _extract_title_entity(text) or "title"
    return f"{entity} characters cast protagonists known details"


def _normalize_product_release_query(text: str = "") -> str:
    entity = _extract_title_entity(text) or "title"
    return f"{entity} release date launch platforms official"


def _normalize_product_price_query(text: str = "") -> str:
    entity = _extract_title_entity(text) or "title"
    return f"{entity} price cost pre-order editions"


# Backward-compatible aliases used by older tests/call sites
def _normalize_gta_trailer_query(text: str, full_context: str = "") -> str:
    return _normalize_product_trailer_query(text, full_context=full_context)


def _normalize_gta_characters_query(text: str = "") -> str:
    return _normalize_product_cast_query(text)


def _normalize_gta_release_query(text: str = "") -> str:
    return _normalize_product_release_query(text)


def _normalize_gta_price_query(text: str = "") -> str:
    return _normalize_product_price_query(text)


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
    # Trailing politeness must never become its own search ("pelsae check" / "please check")
    work = re.sub(
        r"(?i)[?!.]?\s*\b(?:please|pls|plz)\s*(?:check|look(?:\s+up)?|search|confirm|verify)?\s*[?!.]*\s*$",
        " ",
        work,
    )
    work = re.sub(
        r"(?i)\b(?:can you|could you|would you)\s+(?:please\s+)?(?:check|look up|search|confirm)\s*[?!.]*\s*$",
        " ",
        work,
    )
    return _normalize_text(work)


def _is_hollow_secondary_clause(text: str) -> bool:
    """True for tails like 'recommend the best value pick' that need the prior topic.

    These are the same research ask, not a second independent multi-intent search.
    """
    low = _normalize_text(text).lower()
    if not low or len(low.split()) > 10:
        return False
    # Already has a concrete product/topic noun → keep as its own query
    if re.search(
        r"(?i)\b(microphone|mic|iphone|laptop|bitcoin|weather|stock|"
        r"python|nvidia|tesla|trailer|price|release|score|schedule)\b",
        low,
    ):
        return False
    if _extract_title_entity(low) or _extract_teamish_phrase(low):
        return False
    if re.search(
        r"(?i)^\s*(?:and\s+)?(?:also\s+)?(?:please\s+)?"
        r"(?:recommend|pick|choose|which\s+(?:one|is)|the\s+best\s+value|"
        r"best\s+value\s+pick|value\s+pick|which\s+to\s+buy)\b",
        low,
    ):
        return True
    if re.search(r"(?i)\b(best value pick|recommend the best|which is better)\b", low):
        return True
    return False


def _is_smalltalk_clause(text: str) -> bool:
    """True for pure social/filler clauses that must not become search queries."""
    low = _normalize_text(apply_spelling_fixes(text or "")).lower()
    if not low:
        return True
    if _is_hollow_secondary_clause(low):
        return True
    # Pure politeness / confirmation tails (never search these alone)
    if re.search(
        r"(?i)^\s*(?:please|pls|plz)?\s*(?:check|look|confirm|verify|thanks|thank you)?\s*[?!.]*\s*$",
        low,
    ) or re.fullmatch(r"(?:please|pelsae|pls|plz)(?:\s+check)?", low):
        return True
    if _is_weather_clause(low) or _is_schedule_or_sports_clause(low):
        return False
    if any(
        t in low
        for t in (
            "score", "odds", "price", "stock", "news", "headline", "trailer",
            "release", "forecast", "temperature", "schedule", "fixture",
            "games", "matches", "fifa", "python", "bitcoin", "cast", "characters",
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
    # Single sports/schedule ask is never multi (even with "france and morocco" or trailing please)
    if domains == {"sports"} or (
        "sports" in domains and len(domains) == 1
    ):
        return False
    # Two+ explicit question marks (after stripping politeness tails)
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
    # Two interrogative heads — ignore trailing "check" politeness after please-strip
    inters = re.findall(
        r"(?i)\b(what|when|where|who|which|how|why|find out|look up|tell me)\b",
        low,
    )
    # Bare "check" only counts mid-sentence as its own ask, not "please check"
    if re.search(r"(?i)\bcheck\b", low) and not re.search(
        r"(?i)\b(?:please|pls|plz)?\s*check\s*$", low
    ):
        if re.search(r"(?i)\bcheck\b.+\b(weather|score|price|news|status)\b", low):
            inters.append("check")
    if len(inters) >= 2 and len(words) >= 12:
        return True
    # "A, and B" clause split — do NOT split matchup "X and Y" pairs into fake multi
    # Protect "with France and Morocco" style before splitting on and
    protected = re.sub(
        r"(?iu)\b((?:with|between)\s+[\w][\w'-]{1,30}\s+)and(\s+[\w][\w'-]{1,30})\b",
        r"\1&AND&\2",
        t,
    )
    protected = re.sub(
        r"(?iu)\b((?:game|match|fixture)\s+(?:with\s+|between\s+)?[\w][\w'-]{1,30}\s+)and(\s+[\w][\w'-]{1,30})\b",
        r"\1&AND&\2",
        protected,
    )
    clauses = [
        c.strip(" ,;:").replace("&AND&", "and")
        for c in re.split(r"[?!.]+|\band\b", protected, flags=re.IGNORECASE)
        if c
        and len(c.split()) >= 3
        and not _is_smalltalk_clause(c.replace("&AND&", "and"))
        and not _is_hollow_secondary_clause(c.replace("&AND&", "and"))
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
    has_product = _has_product_title_context(work)
    has_trailer = _has_trailer_intent(work)
    has_chars = _has_character_cast_intent(work)

    out: list[str] = []
    # Domain-pair recipe: weather+sports (structural domains, not a product whitelist)
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
    # Product multi: trailer and/or cast for whatever title was mentioned
    elif (has_trailer or has_chars) and (has_product or has_trailer):
        if has_trailer:
            out.append(_normalize_product_trailer_query(work, full_context=work))
        if has_chars or (has_product and re.search(r"(?i)\b(who|names?|know|cast)\b", work)):
            out.append(_normalize_product_cast_query(work))

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
    # Already entity-grounded: free-form title OR residual non-price tokens (any product)
    if _extract_title_entity(q):
        return False
    if re.search(r"(?i)\b(bitcoin|btc|ethereum|stock|iphone|ps5|xbox|python|nvidia|tesla)\b", low):
        return False
    price_stop = {
        "how", "much", "will", "it", "cost", "costs", "costing", "price", "pricing",
        "msrp", "pre", "order", "preorder", "editions", "edition", "the", "a", "an",
        "for", "of", "does", "is", "be", "go", "what", "dollars", "money", "and",
    }
    residual = [
        t for t in re.findall(r"[a-z0-9]+", low)
        if t not in price_stop and not t.isdigit()
    ]
    # "gta 6 price…" / "silksong price…" have residual product tokens → not orphan
    if residual:
        return False
    # Short cost-only / "will it cost" clauses
    if len(low.split()) <= 8:
        return True
    return bool(re.search(r"(?i)\b(how much will it cost|how much does it cost|what does it cost)\b", low))


def _rebind_orphan_queries(work: str, queries: list[str]) -> list[str]:
    """Attach bare cost/price sub-queries to title/entity from full message."""
    work_n = _normalize_text(work)
    if not work_n or not queries:
        return queries
    out: list[str] = []
    for q in queries:
        if _is_orphan_price_query(q) and _has_product_title_context(work_n):
            out.append(_normalize_product_price_query(work_n))
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
    if _has_trailer_intent(p) and (
        _has_product_title_context(p) or _has_product_title_context(ctx) or _has_trailer_intent(p)
    ):
        if _has_character_cast_intent(p):
            pass
        return _normalize_product_trailer_query(p, full_context=ctx)
    if (_has_product_title_context(p) or _has_product_title_context(ctx)) and _has_character_cast_intent(p):
        return _normalize_product_cast_query(p if _extract_title_entity(p) else ctx)
    if (_has_product_title_context(p) or _has_product_title_context(ctx)) and (
        _has_product_release_intent(p)
        or _has_product_release_intent(f"{p} {ctx}" if _has_product_title_context(ctx) else p)
    ):
        if _has_product_title_context(p) and _has_product_release_intent(p):
            return _normalize_product_release_query(p)
        if _has_product_title_context(p) and re.search(r"(?i)\b(how much|cost|price|money)\b", p):
            return _normalize_product_price_query(p)
        if _has_product_release_intent(p) and _has_product_title_context(ctx):
            return _normalize_product_release_query(ctx)
    # Bare "how much will it cost" after a product clause in the same message
    if _is_orphan_price_query(p) and _has_product_title_context(ctx):
        return _normalize_product_price_query(ctx)
    if _has_product_title_context(p) and re.search(r"(?i)\b(how much|cost|price|money)\b", p):
        return _normalize_product_price_query(p)
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
        # Start near sports keywords — NEVER from message start (product clause used to
        # swallow the whole string, then _clip_span_to_clause dropped the sports half).
        kw = re.search(
            r"(?i)\b(?:fifa|world\s*cup|nhl|nba|nfl|mlb|uefa|premier\s*league|"
            r"matches?|games?|fixtures?|matchup|score|playing|"
            r"vs\.?|versus|against|next\s+game|schedule)\b",
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
            r"(?i)\b(fifa|world cup|match|game|score|nhl|nba|nfl|mlb|vs\.?|versus)\b", span
        ):
            span = work[kw.start() : min(len(work), kw.end() + 90)]
        span = re.sub(
            r"(?i)\b(temp(?:erature)?s?|weather|forecast|humidity|bitcoin|stock)\b",
            " ",
            span,
        )
        # Orphan "who is playing" alone → keep parent sports context
        if re.search(r"(?i)^\s*who\s+is\s+playing\s*$", span.strip()) and kw:
            span = work[max(0, kw.start() - 40) : min(len(work), kw.end() + 90)]
        sq = _normalize_sports_query(span)
        # Avoid treating a pure product-title span as sports
        if sq and not _is_weather_clause(sq) and not (
            _has_product_title_context(sq) and not _is_schedule_or_sports_clause(sq)
        ):
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
        if _has_product_title_context(work) or _has_trailer_intent(work):
            if _has_trailer_intent(work):
                out.append(_normalize_product_trailer_query(work, full_context=work))
            if _has_character_cast_intent(work):
                out.append(_normalize_product_cast_query(work))
            if _has_product_release_intent(work) and not _has_trailer_intent(work):
                out.append(_normalize_product_release_query(work))
            if _has_product_price_intent(work) or (
                _has_product_title_context(work)
                and re.search(r"(?i)\b(how much|cost|price|money it costs|pre-?order)\b", work)
            ):
                out.append(_normalize_product_price_query(work))
            if (
                _has_product_title_context(work)
                and not _has_trailer_intent(work)
                and not _has_character_cast_intent(work)
                and not _has_product_release_intent(work)
                and not re.search(r"(?i)\b(how much|cost|price)\b", work)
            ):
                out.append(_normalize_product_release_query(work))
        else:
            m = re.search(
                r"(?i)(?:when|what).{0,40}\b(?:movie|film|trailer|release|netflix|show|series|album)\b.{0,40}",
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
    # Single-domain sports/schedule: one compact query (never explode matchup "and")
    if intent_domains(work) <= {"sports"} and _is_schedule_or_sports_clause(work):
        one = _normalize_clause_for_search(work, full_context=work)
        return [one] if one else []
    # Protect matchup "X and Y" from clause splits
    protected = re.sub(
        r"(?iu)\b((?:with|between)\s+[\w][\w'-]{1,30}\s+)and(\s+[\w][\w'-]{1,30})\b",
        r"\1&AND&\2",
        work,
    )
    protected = re.sub(
        r"(?iu)\b((?:game|match|fixture)\s+(?:with\s+|between\s+)?[\w][\w'-]{1,30}\s+)and(\s+[\w][\w'-]{1,30})\b",
        r"\1&AND&\2",
        protected,
    )
    parts = [
        c.strip(" ,;:").replace("&AND&", "and")
        for c in re.split(
            r"[?!.]+|\band also\b|\bas well as\b|\band then\b|\balso\b|\bplus\b|\band\b",
            protected,
            flags=re.IGNORECASE,
        )
        if c and len(c.replace("&AND&", "and").split()) >= 2 and not _is_smalltalk_clause(c.replace("&AND&", "and"))
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
            re.search(r"(?i)\b(price|cost|pre-?order|msrp)\b", q)
            and (
                _extract_title_entity(q)
                or re.search(r"(?i)\b(bitcoin|btc|ethereum|msrp|edition)\b", q)
                or len(q.split()) >= 4
            )
            for q in out
        )
        missing_price = bool(re.search(r"(?i)\b(how much|cost|price)\b", work)) and not has_grounded_price
        orphan_price = any(_is_orphan_price_query(q) for q in out)
        # Sports domain present in work but missing from outputs
        missing_sports = "sports" in intent_domains(work) and not any(
            _is_schedule_or_sports_clause(q) or re.search(r"(?i)\b(fifa|world cup|nhl|nba|nfl|mlb|schedule|fixture)\b", q)
            for q in out
        )
        if chatty or missing_price or orphan_price or missing_sports or len(forced) > len(out):
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
            "You convert chat into web SEARCH QUERIES (not answers).\n"
            "Rules:\n"
            "1) ONE independent fact ask → ONE query. Do NOT split politeness "
            "('please check'), STT noise, or matchup names joined by 'and' "
            "(France and Morocco = one match).\n"
            "2) Multiple DIFFERENT fact domains (e.g. weather + sports, release + price) "
            "→ separate queries (2-5 max).\n"
            "3) Each query must be a compact search string with the specific anchors: "
            "who/what/where (names, places, products), when (today/tomorrow/dates), "
            "and the fact type (kickoff time, high/low, price, release date).\n"
            "4) Never emit fragments like 'please check', 'start today', or bare 'and X'.\n"
            "5) Return ONLY a JSON array of strings.\n"
            'Example one-ask: ["FIFA World Cup France Morocco kickoff time today"]\n'
            'Example multi: ["Osaka weather tomorrow high low", '
            '"FIFA World Cup match list kickoff tomorrow"]\n\n'
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
    """Pin league/match context from prior subject onto bare schedule follow-ups.

    Structural: \"july 9th what games?\" after a World Cup turn keeps competition context.
    Never injects a hard-coded franchise nickname.
    """
    q = _normalize_text(query)
    sub = _normalize_text(subject)
    if not q or not sub:
        return q
    # Already has league or a vs-side matchup
    if re.search(r"(?i)\b(fifa|world\s*cup|nhl|nba|nfl|mlb|uefa|premier\s*league)\b", q):
        return q
    if _extract_vs_sides(q):
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
        or re.search(r"(?i)\b(fifa|world\s*cup|nhl|nba|nfl|mlb|match|score|vs\.?|versus)\b", sub)
    ):
        return q
    if re.search(r"(?i)\b(fifa|world\s*cup)\b", sub):
        return _normalize_sports_query(f"FIFA World Cup {q}")
    if re.search(r"(?i)\bnhl\b|\bhockey\b", sub):
        return _normalize_sports_query(f"NHL {q}")
    if re.search(r"(?i)\bnba\b|\bbasketball\b", sub):
        return _normalize_sports_query(f"NBA {q}")
    if re.search(r"(?i)\bnfl\b", sub):
        return _normalize_sports_query(f"NFL {q}")
    if re.search(r"(?i)\bmlb\b|\bbaseball\b", sub):
        return _normalize_sports_query(f"MLB {q}")
    # Prior subject has free-form sides or team phrase — prepend them
    sides = _extract_vs_sides(sub)
    if sides:
        return _normalize_sports_query(f"{sides} {q}")
    team = _extract_teamish_phrase(sub)
    if team and team.lower() not in q.lower():
        return _normalize_sports_query(f"{team} {q}")
    return q


def _search_content_anchors(text: str) -> set[str]:
    """
    Tokens that make a search *specific*: places, names, numbers, domain keywords.

    Fragment queries like \"maracoo start today\" or \"pelsae check\" fail this bar
    when the parent utterance had richer anchors they dropped.
    """
    low = (text or "").lower()
    anchors: set[str] = set()
    # Numbers / clock / dates
    for m in re.finditer(r"\b(\d{1,4}(?::\d{2})?|\d{4})\b", low):
        anchors.add(m.group(1))
    # Domain keywords (category, not entity hardcode)
    for tok in (
        "weather", "forecast", "temperature", "fifa", "world", "cup", "nhl", "nba",
        "nfl", "mlb", "kickoff", "schedule", "fixture", "score", "odds", "price",
        "cost", "release", "trailer", "bitcoin", "stock", "news", "tomorrow", "today",
        "tonight",
    ):
        if re.search(rf"\b{re.escape(tok)}\b", low):
            anchors.add(tok)
    # Content words: length >= 4, not stopwords
    stop = {
        "what", "when", "where", "which", "with", "from", "that", "this", "they",
        "them", "have", "does", "will", "would", "could", "should", "please", "check",
        "start", "starts", "starting", "about", "into", "just", "also", "then",
        "there", "here", "your", "you", "for", "the", "and", "are", "was", "were",
        "how", "who", "why", "can", "need", "want", "tell", "find", "look", "search",
        "game", "games", "match", "matches", "time", "times",
    }
    for w in re.findall(r"(?u)[\w']+", low):
        if len(w) >= 4 and w not in stop and not w.isdigit():
            anchors.add(w)
    return anchors


def is_viable_search_query(q: str, *, parent: str = "") -> bool:
    """
    True only for search strings that look like *reframed factual asks*.

    Rejects: politeness fragments, mid-sentence debris, empty chat crumbs.
    Keeps: entity+intent compact queries (place, teams, titles, numbers).
    """
    s = _normalize_text(q)
    if not s or len(s) < 4:
        return False
    if _is_smalltalk_clause(s) or _is_hollow_secondary_clause(s):
        return False
    low = s.lower()
    # Pure politeness / meta
    if re.fullmatch(
        r"(?:please|pls|plz|pelsae|check|look|confirm|verify|thanks|thank you|"
        r"can you|could you)(?:\s+\w+){0,2}",
        low,
    ):
        return False
    words = low.split()
    if len(words) < 2:
        return False
    anchors = _search_content_anchors(s)
    if not anchors:
        return False

    has_domain = bool(
        re.search(
            r"(?i)\b(weather|forecast|fifa|world cup|nhl|nba|nfl|mlb|kickoff|schedule|"
            r"fixture|score|odds|price|cost|release|trailer|bitcoin|stock|news|"
            r"temperature|high|low|capital|population|ceo|founded|invented|"
            r"tallest|longest|who|what|when|where)\b",
            low,
        )
    )
    if parent:
        parent_anchors = _search_content_anchors(parent)
        parent_has_domain = bool(
            re.search(
                r"(?i)\b(weather|forecast|fifa|world cup|nhl|nba|nfl|mlb|kickoff|"
                r"price|cost|release|trailer|bitcoin|stock|news|capital|score)\b",
                parent,
            )
        )
        distinctive = parent_anchors - {
            "today", "tomorrow", "tonight", "start", "time", "game", "check",
            "please", "pelsae", "starts", "starting",
        }
        kept = anchors & distinctive
        # Short debris with no domain keyword while parent was a domain ask
        if parent_has_domain and not has_domain and len(words) <= 5:
            return False
        # Kept almost none of parent's distinctive anchors and is short
        if distinctive and len(distinctive) >= 2 and len(kept) <= 1 and len(words) <= 4 and not has_domain:
            return False
    # Short queries without a domain keyword are almost always fragments
    if not has_domain and len(words) <= 4:
        return False
    return True


def quality_gate_search_queries(queries: list[str], parent: str) -> list[str]:
    """
    Final filter: only ship entity-rich, intent-clear queries to the web.

    If multi-split produced junk fragments, drop them. If nothing survives,
    fall back to one compact query from the full parent utterance.
    """
    parent_n = _prep_search_work_text(parent) or _normalize_text(parent)
    cleaned: list[str] = []
    for q in queries or []:
        n = _normalize_text(q)
        if not n:
            continue
        # Prefer already-viable candidates AS-IS. Re-normalizing a compact sports
        # string (\"FIFA … france maracoo kickoff\") used to wipe free-form sides.
        if is_viable_search_query(n, parent=parent_n):
            cleaned.append(n)
            continue
        compact = normalize_web_search_query_single(n) or n
        if compact != n and is_viable_search_query(compact, parent=parent_n):
            cleaned.append(compact)
    cleaned = _dedupe_queries(cleaned)
    if cleaned:
        return cleaned[:5]
    # Fallback: one well-formed query from the whole user turn
    one = normalize_web_search_query_single(parent_n) or parent_n
    return [one] if one else []


def resolve_web_search_queries(
    user_text: str,
    model_query: str = "",
    *,
    llm_invoke=None,
    use_decomposition: bool = True,
) -> list[str]:
    """
    Full query resolution for grounded search.

    Architecture (intent → reframed search strings, not utterance fragments):
      1) Prep: strip social/politeness fluff (please check, greetings)
      2) Detect domains / multi-intent (2+ distinct fact domains only)
      3) Recipe multi-split OR domain carve OR single compact normalize
      4) Quality gate: drop fragment/filler queries; require content anchors
         (places, names, numbers, domain keywords)

    Critical: model tool args are often single-intent or chatty. User text is
    authoritative for multi detection — never ship raw chat crumbs to Tavily.
    """
    user = _normalize_text(user_text)
    model_q = _normalize_text(model_query)
    user_prep = _prep_search_work_text(user) or user

    # Prefer user text for multi detection — model tool args are often single-intent.
    multi_src = user_prep or model_q
    if user_prep and (len(intent_domains(user_prep)) >= 2 or looks_like_multi_intent(user_prep)):
        multi_src = user_prep
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
        # 3) Single-intent compact — full utterance, not a clause fragment
        if not multi:
            one = (
                normalize_web_search_query_single(user_prep)
                or normalize_web_search_query_single(user)
                or normalize_web_search_query_single(model_q)
            )
            if not one:
                one = model_q or user_prep or user
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
            and is_viable_search_query(model_c, parent=multi_src)
            and not re.match(r"(?i)^(i |can you|please|find out|what |when )", model_c)
        ):
            multi.append(model_c)

    # Final guard: if user had 2+ domains but multi is still 1, force domain split
    if use_decomposition and len(multi) < 2 and len(intent_domains(multi_src)) >= 2:
        forced = _force_domain_decompose(multi_src)
        if len(forced) >= 2:
            multi = forced

    # Rebind bare "how much will it cost" → product price when parent turn has a title
    multi = _rebind_orphan_queries(multi_src, multi)
    # If product+price still missing grounded price query, inject it
    if _has_product_title_context(multi_src) and re.search(r"(?i)\b(how much|cost|price)\b", multi_src):
        if not any(re.search(r"(?i)\b(price|cost|pre-?order)\b", q) for q in multi):
            multi = _dedupe_queries(list(multi) + [_normalize_product_price_query(multi_src)])

    # 4) Quality gate — never ship utterance fragments as searches
    return quality_gate_search_queries(multi, multi_src)[:5]


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
        _has_product_title_context(work) or _has_trailer_intent(work)
    ):
        if _has_trailer_intent(work) and not _has_character_cast_intent(work):
            return [_normalize_product_trailer_query(work, full_context=work)]
        if _has_character_cast_intent(work) and not _has_trailer_intent(work):
            return [_normalize_product_cast_query(work)]

    single = normalize_web_search_query_single(work)
    return [single] if single else []


def normalize_web_search_query_single(query: str) -> str:
    """Compact a single-intent string (no multi-intent fan-out)."""
    # Prep first: spelling + strip "pelsae check" so we never search politeness
    q = _prep_search_work_text(query) or _normalize_text(query)
    if not q:
        return ""
    q = q.replace("\u2019", "'").replace("\u2018", "'")

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

    # Sports/schedule first on FULL string — before "and" clause splits destroy matchups
    # (live: "france and maracoo" must not become "maracoo start today" alone).
    if _is_schedule_or_sports_clause(q) or re.search(r"(?i)\b(fifa|world cup)\b", q):
        sports = _normalize_sports_query(q)
        if sports and (
            _extract_vs_sides(q)
            or re.search(r"(?i)\b(fifa|world cup|nhl|nba|nfl|mlb|schedule|kickoff)\b", sports)
        ):
            return sports

    # Drop leading social openers / greeting clauses.
    q = _SOCIAL_OPEN_RE.sub(" ", q)
    # Split multi-intent: prefer the clause that looks like the factual ask.
    # Protect matchup "X and Y" from being torn apart.
    protected = re.sub(
        r"(?iu)\b((?:with|between)\s+[\w][\w'-]{1,30}\s+)and(\s+[\w][\w'-]{1,30})\b",
        r"\1&AND&\2",
        q,
    )
    protected = re.sub(
        r"(?iu)\b((?:game|match|fixture)\s+(?:with\s+|between\s+)?[\w][\w'-]{1,30}\s+)and(\s+[\w][\w'-]{1,30})\b",
        r"\1&AND&\2",
        protected,
    )
    clauses = [
        c.strip(" ,;:").replace("&AND&", "and")
        for c in re.split(r"[?!.]+|\band\b|\balso\b", protected, flags=re.IGNORECASE)
        if c and c.strip(" ,;:")
    ]

    def _clause_score(c: str) -> int:
        low = c.lower()
        score = 0
        if _RELEASE_DATE_RE.search(c):
            score += 5
        if any(t in low for t in ("weather", "forecast", "score", "trailer", "news", "price", "stock", "release", "fifa", "kickoff")):
            score += 3
        if re.search(r"\b(when|what|who|where|which|how much|how many)\b", low):
            score += 2
        if re.search(r"\b(hey|hi|hello|feeling|doing|please|check)\b", low) and len(c.split()) <= 3:
            score -= 8
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
        # Keep whatever product/language the user named (not Python-only)
        if q_notes:
            if "release notes" in q_notes.lower() or "changelog" in q_notes.lower():
                return q_notes
            return f"{q_notes} release notes changelog official"
        return "release notes changelog official"

    # Product/title rewrite: any franchise with trailer / cast / release / price structure
    if _has_trailer_intent(q) and (_has_product_title_context(q) or _has_trailer_intent(q)):
        return _normalize_product_trailer_query(q, full_context=q)
    if _has_product_title_context(q) and _has_character_cast_intent(q):
        return _normalize_product_cast_query(q)
    if _has_product_title_context(q) or _extract_title_entity(q):
        wants_price = _has_product_price_intent(q)
        wants_release = _has_product_release_intent(q) or bool(
            re.search(r"(?i)\b(when|release|launch|come out)\b", q)
        )
        if wants_price and not wants_release:
            return _normalize_product_price_query(q)
        if wants_release:
            return _normalize_product_release_query(q)
        if wants_price:
            return _normalize_product_price_query(q)

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
                    "price", "nhl", "nba", "nfl", "mlb", "world cup", "temperature",
                    "release", "cast", "characters",
                )
            ):
                score += 6
            if re.search(r"\b(vs|versus|tomorrow|today|tonight|release)\b", low):
                score += 3
            if _extract_title_entity(p) or _extract_teamish_phrase(p):
                score += 2
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
    # original ("team game and weather") does not force weather mode onto a
    # sports-only resolved string.
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
            r"\b(world cup|fifa|nhl|nba|nfl|mlb|soccer|football|hockey|basketball|"
            r"match(?:es)?|fixture|tournament|vs\.?|versus)\b",
            combined,
        )
        or bool(_extract_teamish_phrase(low) and re.search(r"\b(game|match|schedule|score)\b", low))
    )
    schedule = (
        any(term in low for term in _SCHEDULE_TERMS)
        or any(term in low_orig for term in _SCHEDULE_TERMS)
        or bool(re.search(r"\b(next game|schedule|fixture)\b", low))
        or bool(
            re.search(r"\b(game|match)\b", low)
            and (
                re.search(r"\b(nhl|nba|nfl|mlb|fifa|world cup)\b", low)
                or _extract_teamish_phrase(low)
                or _extract_vs_sides(low)
            )
        )
        or (who_playing and (near_future or sports_event))
        or (near_future and sports_event and re.search(r"\b(play|playing|game|match|vs|versus)\b", combined))
    )
    # Product "price" / "live price" is never a sports live-score intent
    productish = bool(
        re.search(
            r"\b(price|cost|msrp|pre-?order|trailer|release date|steam|bitcoin|btc|crypto)\b",
            low,
        )
    )
    live_score = (not productish) and any(term in low for term in _LIVE_SCORE_TERMS) and (
        re.search(
            r"\b(game|match|fifa|world cup|soccer|football|nhl|nba|nfl|mlb|vs\.?|versus|"
            r"score|scores|who won)\b",
            low,
        )
        or (
            # Bare "live" is not enough — need score language or a vs-side
            bool(_extract_vs_sides(low))
            or (
                re.search(r"\b(score|scores)\b", low)
                and bool(_extract_teamish_phrase(low))
            )
        )
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
    # Teamish phrase alone is not sportsy (place names like "Edmonton weather" are not schedules).
    sportsy = bool(
        re.search(
            r"\b(nhl|nba|nfl|mlb|next game|schedule|fixture|world cup|fifa|vs\.?|versus)\b",
            low,
        )
        or (
            _extract_teamish_phrase(low)
            and re.search(r"\b(game|match|score|schedule|fixture|play|playing)\b", low)
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
            # Already-rich query (calendar + kickoff + TZ) → single candidate, no variant storm
            rich = bool(
                re.search(r"(?i)\b(kickoff|convert|timezone|mnt|mountain|match list)\b", base)
                and re.search(r"(?i)\b(today|tomorrow|thursday|friday|july|\d{4})\b", base)
            )
            if rich:
                candidates = [
                    SearchCandidate(base, "schedule rich single", 0.96, ["schedule", "rich"]),
                ]
            else:
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
                        f"{base} fixtures {day_word} site:espn.com OR site:cbssports.com",
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
        team = _normalize_text(
            re.sub(r"(?i)\b(next game|schedule|nhl|nba|nfl|mlb|fifa|world cup|date|time|this year)\b", " ", base)
        )
        team = team or base
        low = (base or "").lower()
        # Authority site from league keyword in query — not a fixed franchise host
        site = "site:espn.com"
        if re.search(r"\b(nhl|hockey)\b", low):
            site = "site:nhl.com OR site:espn.com"
        elif re.search(r"\b(nba|basketball)\b", low):
            site = "site:nba.com OR site:espn.com"
        elif re.search(r"\b(nfl)\b", low):
            site = "site:nfl.com OR site:espn.com"
        elif re.search(r"\b(mlb|baseball)\b", low):
            site = "site:mlb.com OR site:espn.com"
        elif re.search(r"\b(fifa|world cup)\b", low):
            site = "site:fifa.com OR site:espn.com"
        return [
            SearchCandidate(f"{team} next game {year}", "deeper next game year", 0.92, ["schedule", "deeper"]),
            SearchCandidate(f"{site} {team} schedule", "deeper authority", 0.91, ["schedule", "deeper"]),
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
        # Skip when the base query was already TZ/kickoff-rich (deeper only churns variants).
        base_rich = bool(
            re.search(
                r"(?i)\b(kickoff|convert|timezone|mnt|mountain|match list)\b",
                best_query or resolved_request or "",
            )
        )
        if intent.schedule_need and not base_rich:
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

        # Soft-accept entertainment / cast / trailer when evidence carries structure
        # (named people, trailer windows) — no franchise-specific character lists.
        low_resolved = (intent.resolved_request or "").lower()
        if best_evidence and (
            "character" in low_resolved
            or "cast" in low_resolved
            or "trailer" in low_resolved
            or "protagonist" in low_resolved
            or "release" in low_resolved
        ):
            for e in best_evidence[:5]:
                hay = f"{e.title} {e.summary}".lower()
                # Structural name signals: First Last pairs, or "playable/protagonist" language
                has_names = bool(
                    re.search(
                        r"\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b",
                        f"{e.title} {e.summary}",
                    )
                    or re.search(
                        r"\b(protagonist|playable|cast includes|voiced by|stars?)\b",
                        hay,
                    )
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
        if has_time and any(w in hay for w in ("game", "match", "play", "host", "visit", "face", "kickoff", "vs")):
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
