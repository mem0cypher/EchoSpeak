"""Dedicated sports live-data client (v7.6.2 workstream).

Category mismatch: general web search (Tavily/Brave/Exa) indexes *crawled* pages.
Live scores/odds need structured feeds. This module is the first-class path for
live-score / odds / standings-style asks — web_search is fallback only.

Primary provider: The Odds API (https://the-odds-api.com/)
  - Free tier: 500 credits/month
  - Scores: GET /v4/sports/{sport}/scores/
  - Odds:   GET /v4/sports/{sport}/odds/

Env: ODDS_API_KEY (or THE_ODDS_API_KEY)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from loguru import logger

# ---------------------------------------------------------------------------
# Intent: live structured sports data vs crawlable web
# ---------------------------------------------------------------------------

_LIVE_SCORE_RE = re.compile(
    r"(?i)\b("
    r"live\s*score|current\s*score|score\s*right\s*now|who\s+won|final\s*score|"
    r"scoreline|what(?:'s| is)\s+the\s+score|did\s+\w+\s+win|winning|"
    r"in[- ]play|full[- ]?time|halftime|half[- ]time"
    r")\b"
)
_ODDS_RE = re.compile(
    r"(?i)\b("
    r"odds|moneyline|money\s*line|spread|over\s*under|betting\s*line|"
    r"betting\s*odds|sportsbook|favorite|underdog|ml\b"
    r")\b"
)
_STANDINGS_RE = re.compile(
    r"(?i)\b(standings|league\s*table|points\s*table|playoff\s*picture|conference\s*rankings)\b"
)
# Schedule / "who's playing tomorrow" stays on web_search
_SCHEDULE_ONLY_RE = re.compile(
    r"(?i)\b("
    r"who(?:'s| is)?\s+playing|what\s+matches|fixtures?|kickoff|start\s*time|"
    r"next\s+game|schedule|when\s+(?:do|does|is)\s+.+\s+play"
    r")\b"
)


def is_live_sports_data_intent(text: str) -> bool:
    """True when structured live API is preferred over crawl search."""
    t = str(text or "").strip()
    if not t:
        return False
    low = t.lower()
    # Pure schedule / slate → web search (tournament pages, local listings)
    if _SCHEDULE_ONLY_RE.search(low) and not (_LIVE_SCORE_RE.search(low) or _ODDS_RE.search(low)):
        # "who's playing tomorrow" is schedule; "what's the score" is live
        if not re.search(r"(?i)\b(score|won|winning|odds|standings)\b", low):
            return False
    if _ODDS_RE.search(low):
        return True
    if _STANDINGS_RE.search(low):
        return True
    if _LIVE_SCORE_RE.search(low):
        return True
    # "score" + game language / live deictics / vs-structure — not a team nickname list
    if re.search(r"(?i)\bscore\b", low) and re.search(
        r"(?i)\b(game|match|vs\.?|versus|nhl|nba|nfl|mlb|fifa|world cup|"
        r"live|right now|currently|tonight|today)\b",
        low,
    ):
        return True
    return False


def live_sports_mode(text: str) -> str:
    """odds | scores | standings | none"""
    if not is_live_sports_data_intent(text):
        return "none"
    low = (text or "").lower()
    if _ODDS_RE.search(low):
        return "odds"
    if _STANDINGS_RE.search(low):
        return "standings"
    return "scores"


# Sport key mapping for The Odds API — league/sport keywords only (no franchise nicknames).
# Unknown team without a league keyword → None → web fallback (correct capability boundary).
_SPORT_KEYS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\b(nhl|hockey)\b"), "icehockey_nhl"),
    (re.compile(r"(?i)\b(nba|basketball)\b"), "basketball_nba"),
    (re.compile(r"(?i)\b(nfl|super\s*bowl)\b"), "americanfootball_nfl"),
    (re.compile(r"(?i)\b(mlb|baseball)\b"), "baseball_mlb"),
    (re.compile(r"(?i)\b(mls)\b"), "soccer_usa_mls"),
    (re.compile(r"(?i)\b(epl|premier\s*league)\b"), "soccer_epl"),
    (re.compile(r"(?i)\b(fifa|world\s*cup|uefa)\b"), "soccer_fifa_world_cup"),
    (re.compile(r"(?i)\bsoccer\b"), "soccer_usa_mls"),
]


def infer_sport_key(text: str) -> Optional[str]:
    for pat, key in _SPORT_KEYS:
        if pat.search(text or ""):
            return key
    return None


_TEAM_TOKEN_STOP = {
    "the", "a", "an", "score", "scores", "game", "games", "match", "matches",
    "live", "right", "now", "currently", "tonight", "today", "tomorrow",
    "odds", "moneyline", "spread", "standings", "what", "whats", "who", "won",
    "winning", "for", "of", "and", "vs", "versus", "against", "at", "in",
    "nhl", "nba", "nfl", "mlb", "fifa", "world", "cup", "hockey", "basketball",
    "soccer", "football", "baseball", "please", "check", "get", "me", "my",
}


def infer_team_tokens(text: str) -> List[str]:
    """Free-form residual tokens — not a franchise whitelist."""
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text or "")
    out: List[str] = []
    for w in words:
        lw = w.lower()
        if lw in _TEAM_TOKEN_STOP or len(lw) < 2:
            continue
        if lw not in out:
            out.append(lw)
    # Prefer multi-word vs sides if present
    m = re.search(
        r"(?i)\b([a-z][a-z'-]+)\s+(?:vs\.?|versus|against)\s+([a-z][a-z'-]+)\b",
        text or "",
    )
    if m:
        for side in (m.group(1).lower(), m.group(2).lower()):
            if side not in _TEAM_TOKEN_STOP and side not in out:
                out.insert(0, side)
    return out[:6]


@dataclass
class SportsLiveResult:
    ok: bool
    mode: str  # scores | odds | standings
    provider: str
    sport_key: str = ""
    summary: str = ""
    events: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""
    fallback_to_web: bool = False

    def as_tool_text(self) -> str:
        if not self.ok:
            return (
                f"[SPORTS_LIVE] ok=false provider={self.provider} mode={self.mode}\n"
                f"ERROR: {self.error}\n"
                "FALLBACK: web_search may be used if configured."
            )
        lines = [
            f"[SPORTS_LIVE] ok=true provider={self.provider} mode={self.mode} sport={self.sport_key}",
            "Use ONLY this structured live data for scores/odds. Do not invent lines.",
            "",
            self.summary.strip(),
        ]
        return "\n".join(lines).strip()


class SportsDataClient:
    """The Odds API client with graceful degrade when key missing / sport unknown."""

    BASE = "https://api.the-odds-api.com/v4"

    def __init__(self, api_key: str = "", timeout_s: float = 12.0):
        self.api_key = (api_key or os.getenv("ODDS_API_KEY") or os.getenv("THE_ODDS_API_KEY") or "").strip()
        self.timeout_s = timeout_s
        self.provider = "the_odds_api"

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def status(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "configured": self.available,
            "base": self.BASE,
            "env": "ODDS_API_KEY",
        }

    def _get(self, path: str, params: Optional[dict] = None) -> Tuple[Any, str]:
        if not self.api_key:
            return None, "ODDS_API_KEY not set — live sports API unavailable"
        try:
            import requests

            p = dict(params or {})
            p["apiKey"] = self.api_key
            url = f"{self.BASE}{path}?{urlencode(p)}"
            resp = requests.get(url, timeout=self.timeout_s)
            if resp.status_code == 401:
                return None, "Odds API rejected the key (401)"
            if resp.status_code == 429:
                return None, "Odds API rate limit / out of credits (429)"
            if resp.status_code == 404:
                return None, f"Sport or endpoint not found (404): {path}"
            resp.raise_for_status()
            return resp.json(), ""
        except Exception as exc:
            logger.warning("SportsDataClient request failed: {}", exc)
            return None, str(exc)[:300]

    def list_sports(self) -> List[dict]:
        data, err = self._get("/sports/")
        if err or not isinstance(data, list):
            return []
        return [x for x in data if isinstance(x, dict)]

    def fetch_scores(self, sport_key: str, days_from: int = 1) -> SportsLiveResult:
        data, err = self._get(
            f"/sports/{sport_key}/scores/",
            {"daysFrom": max(1, min(int(days_from), 3)), "dateFormat": "iso"},
        )
        if err:
            return SportsLiveResult(
                ok=False, mode="scores", provider=self.provider, sport_key=sport_key,
                error=err, fallback_to_web=True,
            )
        if not isinstance(data, list) or not data:
            return SportsLiveResult(
                ok=False, mode="scores", provider=self.provider, sport_key=sport_key,
                error="No score events returned for this sport",
                fallback_to_web=True,
            )
        lines = []
        events = []
        for ev in data[:20]:
            if not isinstance(ev, dict):
                continue
            home = str(ev.get("home_team") or "")
            away = str(ev.get("away_team") or "")
            completed = bool(ev.get("completed"))
            scores = ev.get("scores")
            score_txt = ""
            if isinstance(scores, list):
                parts = []
                for s in scores:
                    if isinstance(s, dict):
                        parts.append(f"{s.get('name')}: {s.get('score')}")
                score_txt = " | ".join(parts)
            status = "final" if completed else "in progress / scheduled"
            line = f"- {away} @ {home} ({status})"
            if score_txt:
                line += f" — {score_txt}"
            commence = str(ev.get("commence_time") or "")
            if commence:
                line += f"  [{commence}]"
            lines.append(line)
            events.append(ev)
        summary = "Live/recent scores:\n" + "\n".join(lines)
        return SportsLiveResult(
            ok=True, mode="scores", provider=self.provider, sport_key=sport_key,
            summary=summary, events=events,
        )

    def fetch_odds(self, sport_key: str, regions: str = "us,uk,eu") -> SportsLiveResult:
        data, err = self._get(
            f"/sports/{sport_key}/odds/",
            {
                "regions": regions,
                "markets": "h2h,spreads,totals",
                "oddsFormat": "american",
                "dateFormat": "iso",
            },
        )
        if err:
            return SportsLiveResult(
                ok=False, mode="odds", provider=self.provider, sport_key=sport_key,
                error=err, fallback_to_web=True,
            )
        if not isinstance(data, list) or not data:
            return SportsLiveResult(
                ok=False, mode="odds", provider=self.provider, sport_key=sport_key,
                error="No odds events returned",
                fallback_to_web=True,
            )
        lines = []
        events = []
        for ev in data[:12]:
            if not isinstance(ev, dict):
                continue
            home = str(ev.get("home_team") or "")
            away = str(ev.get("away_team") or "")
            bookmakers = ev.get("bookmakers") or []
            ml_bits = []
            if isinstance(bookmakers, list) and bookmakers:
                bm = bookmakers[0] if isinstance(bookmakers[0], dict) else {}
                markets = bm.get("markets") or []
                for mkt in markets:
                    if not isinstance(mkt, dict):
                        continue
                    if mkt.get("key") == "h2h":
                        for o in mkt.get("outcomes") or []:
                            if isinstance(o, dict):
                                ml_bits.append(f"{o.get('name')}: {o.get('price')}")
            line = f"- {away} @ {home}"
            if ml_bits:
                line += " | ML " + ", ".join(ml_bits[:4])
            lines.append(line)
            events.append(ev)
        summary = "Betting odds (sample book):\n" + "\n".join(lines)
        return SportsLiveResult(
            ok=True, mode="odds", provider=self.provider, sport_key=sport_key,
            summary=summary, events=events,
        )

    def query(self, user_text: str) -> SportsLiveResult:
        """High-level entry: classify + fetch; filter by team tokens when possible."""
        mode = live_sports_mode(user_text)
        if mode == "none":
            return SportsLiveResult(
                ok=False, mode="none", provider=self.provider,
                error="Not a live sports-data intent",
                fallback_to_web=True,
            )
        if not self.available:
            return SportsLiveResult(
                ok=False, mode=mode, provider=self.provider,
                error="ODDS_API_KEY not configured",
                fallback_to_web=True,
            )
        sport = infer_sport_key(user_text)
        if not sport:
            return SportsLiveResult(
                ok=False, mode=mode, provider=self.provider,
                error="Could not map query to a covered league (set sport or use web_search)",
                fallback_to_web=True,
            )
        if mode == "odds":
            result = self.fetch_odds(sport)
        elif mode == "standings":
            # The Odds API has limited standings; fall back honestly
            return SportsLiveResult(
                ok=False, mode="standings", provider=self.provider, sport_key=sport,
                error="Standings not available on free Odds API path — use web_search",
                fallback_to_web=True,
            )
        else:
            result = self.fetch_scores(sport)

        if not result.ok:
            return result

        # Optional team filter for focus
        teams = infer_team_tokens(user_text)
        if teams and result.events:
            filtered_lines = []
            for line in result.summary.splitlines():
                low = line.lower()
                if any(t in low for t in teams) or line.startswith("Live") or line.startswith("Betting"):
                    filtered_lines.append(line)
            # Keep header + matches
            if len(filtered_lines) > 1:
                result.summary = "\n".join(filtered_lines)
        return result


_CLIENT: Optional[SportsDataClient] = None


def get_sports_data_client() -> SportsDataClient:
    global _CLIENT
    if _CLIENT is None:
        try:
            from config import config
            key = str(getattr(config, "odds_api_key", "") or "")
        except Exception:
            key = ""
        _CLIENT = SportsDataClient(api_key=key)
    return _CLIENT
