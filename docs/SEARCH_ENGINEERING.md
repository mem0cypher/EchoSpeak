# Search engineering: scope, gaps, and free-path upgrades

**Audience:** building Echo-quality search **with what we have** (DuckDuckGo + grounder), while keeping **paid options** pluggable.

---

## 1. Scope: two different products

| Layer | Job | Paid APIs | Echo today |
|-------|-----|-----------|------------|
| **Retrieval** | Find URLs + text for a query | Brave, Tavily, Exa, Firecrawl, Google… | DDG free (`ddgs`) |
| **Agent packaging** | Multi-intent, rank, accept, synthesize | Bundled lightly in Tavily/Exa UX | **Strong** (grounder, domains, sports_live path) |

We **cannot** cheaply rebuild a full web index.  
We **can** engineer packaging so free retrieval behaves more like paid agent search.

---

## 2. What’s missing vs paid (honest gap list)

| Paid affordance | Free DDG alone | Engineering fix |
|-----------------|----------------|-----------------|
| Fresh independent index | Weaker / variable | Optional Brave/Tavily adapter |
| Clean structured JSON | We format ourselves | Unified `SearchHit` shape ✅ |
| Full page content | Snippets only | `ddgs.extract` + grounder fetch ✅ |
| Recency filters | Weak | News channel + date variants ✅ |
| Rate limits / SLA | Throttle / empty | Retry simplify + cascade ✅ |
| Live scores | Crawl lag | `sports_live` API (separate) ✅ |
| Semantic “find similar” | No | Exa later if needed |

---

## 3. Architecture (options + free default)

```
WEB_SEARCH_PROVIDER=auto|duckduckgo|tavily|brave

run_web_search(query)
  ├─ build_query_variants()     # date, site: authority, simplify
  ├─ for provider in order:
  │     ddg.text + ddg.news
  │     tavily / brave if keys
  ├─ empty → simplify retry
  └─ thin snippets → ddg.extract(top URLs)

SearchGrounder (unchanged)
  └─ multi-intent / score / soft-accept / synthesize
```

**Default with no keys:** DuckDuckGo-only cascade (your current reality, upgraded).

---

## 4. Free-path engineering (implemented)

Module: `agent/web_search_providers.py`

1. **Provider interface** — `duckduckgo` | `tavily` | `brave`
2. **Cascade** — `auto` picks available keys, always can fall to DDG
3. **Query variants** — month/year for news; `site:` for weather/sports authority
4. **News channel** — `ddgs.news` when query looks newsworthy
5. **Empty retry** — simplified query if zero hits
6. **Extract upgrade** — when snippet thin / weather without numbers, `ddgs.extract(url)`
7. **Metadata** — `Provider:` line in tool output for debugging

`web_search` tool now routes through this orchestrator.

---

## 5. Config

```env
# auto = use any configured paid keys, always DDG fallback
WEB_SEARCH_PROVIDER=auto

# optional paid
TAVILY_API_KEY=
BRAVE_SEARCH_API_KEY=

# live sports (not crawl)
ODDS_API_KEY=
SPORTS_LIVE_ENABLED=true
```

---

## 6. What we will not DIY

- Scraping Google SERP HTML as a “secret API”
- Rebuilding a global crawl/index
- Claiming crawl search is live odds/scores

---

## 7. Next engineering options (priority)

| # | Work | Effort | Impact |
|---|------|--------|--------|
| 1 | Keep using free DDG path (done upgrades) | — | Baseline |
| 2 | Wire Brave when you have a key | Low | Big reliability |
| 3 | Domain tools (weather API, sports API) | Med | Live accuracy |
| 4 | Cache SERP across turns (disk TTL) | Low | Less rate limit |
| 5 | Parallel variant fetch with budget | Med | Speed/quality |
| 6 | Firecrawl-style extract only on accept fail | Med | Deep pages |

---

## 8. Principle

> **Research how paid search behaves. Own the agent layer. Rent or free the index. Cascade so no single vendor owns Echo.**

That is how we get “the same outcome” without pretending we are a search engine company.
