# Search & live-data infrastructure (v7.6.2)

## Category mismatch (important)

| Need | Right tool | Wrong tool |
|------|------------|------------|
| Live scores, in-play lines, book odds | **Structured sports API** | Web crawl search |
| “Who’s playing tomorrow”, news, trailers, weather | **Web search** (crawl index) | Sports odds API alone |
| Deep page extract | Firecrawl / browse | Snippet-only SERP |

General web search (Tavily, Brave, Exa, DuckDuckGo) **indexes pages at crawl time**. A scoreboard page may update every second; the search engine’s copy may be minutes–hours old. Smarter query writing cannot fix that structural gap.

## Live sports path (default for scores/odds)

- **Module:** `agent/sports_data.py`
- **Tool:** `sports_live`
- **Provider (v1):** [The Odds API](https://the-odds-api.com/) — free tier ~500 credits/mo
- **Env:** `ODDS_API_KEY` (alias `THE_ODDS_API_KEY`)
- **Enable:** `SPORTS_LIVE_ENABLED=true` (default)
- **Routing:** `is_live_sports_data_intent()` — scores / who-won / moneyline / spreads  
  **Not** schedule-only (“who’s playing tomorrow”) → those stay on `web_search`
- **Fallback:** if key missing, sport unmapped, or API empty → fall through to grounded `web_search`

### Other providers (future)

| Provider | Strengths | Notes |
|----------|-----------|--------|
| **The Odds API** | Simple REST, scores + odds, free tier | Chosen for v1 |
| **SportRadar** | Broad official feeds | Enterprise / contracts |
| **ESPN public endpoints** | Scores UX-like | Unofficial / fragile |
| **SportsGameOdds** | Books + settlement | Paid |

## Crawl web search (everything else)

Default remains **Tavily** via `web_search` + SearchGrounder.

### Provider evaluation (2026)

| Provider | Role | Notes |
|----------|------|--------|
| **Tavily** | Current default | AI-oriented; **acquired by Nebius (Feb 2026)** → product/pricing uncertainty |
| **Brave Search API** | Strong alt | Independent index; often top agent-search benchmarks; ~$5/1k + monthly free credit; privacy |
| **Exa** | Semantic research | Strong “find similar”; can get expensive |
| **Firecrawl** | Search + extract | Good when you need page content, not just SERP |
| **DuckDuckGo** | Offline fallback | Already in Echo when Tavily fails / no key |

### Config (prepared, not required)

```env
WEB_SEARCH_PROVIDER=tavily   # tavily | brave | auto  (brave path optional future)
BRAVE_SEARCH_API_KEY=
TAVILY_API_KEY=
ODDS_API_KEY=
SPORTS_LIVE_ENABLED=true
```

**Recommendation:** keep Tavily short-term; evaluate **Brave as secondary or replacement** in a dedicated PR (wire `WEB_SEARCH_PROVIDER=brave` in `tools.py`). Do **not** use Brave to “fix live scores” — use `sports_live`.

## Multi-intent

Weather + score compounds still **split** via `intent_domains` / multi-intent.  
Only the **live-score sub-intent** should hit `sports_live`; weather keeps crawl search.

## Acceptance checks

1. `what's the oilers score right now` → prefers `sports_live` when key set; never only stale crawl if API ok  
2. `who is playing tomorrow fifa` → **web_search**, not sports_live  
3. No `ODDS_API_KEY` → explicit miss + web_search fallback (no crash)  
4. Odds ask → odds mode packet with book lines when API covers league  
