# Search engineering: scope, gaps, and free-path upgrades

**Audience:** building Echo-quality search **with what we have** (DuckDuckGo + grounder), while keeping **paid options** pluggable.

**User-facing search honesty / one canonical row / utility ≠ research /
referential double-check / ungrounded numbers:**

- Packaging + utility/sports isolation: **`docs/RUNTIME_CONTRACTS.md` §E**
- ToolRun identity + final-response truth: **`docs/LIFECYCLE_TRUTHFULNESS.md`** §§5, 8

Do not fork rules here. v7.6.10: implemented partial; pending live validation.

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

```text
WEB_SEARCH_PROVIDER=auto|searxng|duckduckgo|brave

run_web_search(query)
  -> normalize_provider_query() and reject vague subjectless searches
  -> build_query_variants() for date, authority, and simplified retry angles
  -> try providers in order:
       searxng when configured, brave when configured, duckduckgo fallback
  -> dedupe URLs and optionally enrich thin DuckDuckGo snippets

SearchGrounder (unchanged)
  -> multi-intent / score / soft-accept / synthesize
```

**Default with no keys or SearXNG:** DuckDuckGo-only cascade.

---

## 4. Free-path engineering (implemented)

Module: `agent/web_search_providers.py`

1. **Provider interface** - `searxng` | `duckduckgo` | `brave`
2. **Cascade** - `auto` prefers SearXNG when explicitly configured, then Brave, always DDG fallback
3. **Query normalization** - shared typo/framing cleanup before providers run
4. **Vague-query guard** - refuses searches with no concrete subject
5. **Query variants** - month/year for news; `site:` for weather/sports authority
6. **News channel** - `ddgs.news` when query looks newsworthy
7. **Empty retry** - simplified query if zero hits
8. **Extract upgrade** - when snippet thin / weather without numbers, `ddgs.extract(url)`
9. **Metadata** - `Provider:` line in tool output for debugging

`web_search` tool now routes through this orchestrator.

---

## 5. Config

```env
# auto = SearXNG when explicitly configured, then optional Brave, always DDG fallback
WEB_SEARCH_PROVIDER=auto
SEARXNG_BASE_URL=http://localhost:8080
BRAVE_SEARCH_API_KEY=
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
