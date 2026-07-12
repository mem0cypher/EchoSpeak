# Echo Search v1

Echo Search v1 is a deterministic, single-agent search workflow. The model
does not issue raw searches. It first returns a structured `SearchPlan`; the
harness executes that plan through bounded mode-specific behavior.

## Modes

| Mode | Behavior |
| --- | --- |
| `QUICK_SEARCH` | Runs 1-2 planned queries, keeps top 5 results, and does not run a second round unless a caller explicitly creates a new plan. Uses snippets as evidence. |
| `COMPARE_SEARCH` | Runs 4-8 planned queries, dedupes URLs/domains, fetches readable page text, and returns evidence suitable for comparison tables. |
| `DEEP_SEARCH` | Runs an initial planned batch, extracts evidence, analyzes gaps, optionally runs one more batch, checks contradictions, then synthesizes cited output. |
| `LOCAL_FIRST_SEARCH` | Searches project files first. Web search only follows when local evidence is insufficient or current/public information is required. |

## Plan Contract

`agent/search_plan.py` owns the `SearchPlan` schema, JSON parsing, query
dedupe, and per-mode query clamps. The prompt tells the selected model to
return only JSON and to reformulate user requests into focused queries. This is
where typo normalization and source planning belong.

## Execution

`agent/deep_search_workflow.py` owns execution. It consumes a `SearchPlan`, not
the raw user message. It enforces:

- Max 8 queries per deep-search round.
- Max 2 rounds by default.
- `QUICK_SEARCH` never loops.
- `LOCAL_FIRST_SEARCH` can stop before web if local evidence satisfies the
  plan.
- Weak evidence produces a cautious answer instead of a guessed answer.

## Retrieval

`agent/search_provider.py` provides the SearXNG backend adapter. Echo Search v1
uses a self-hosted SearXNG instance when `SEARXNG_BASE_URL` is set and normalizes
results to:

```json
{
  "title": "...",
  "url": "...",
  "snippet": "...",
  "source": "searxng",
  "engine": "...",
  "rank": 1
}
```

## Evidence

`agent/evidence_store.py` handles canonical URL cache keys, SQLite page cache,
static page fetches via `httpx`, optional Playwright fallback, readable text
extraction, and deterministic evidence snippets.

Evidence shape:

```json
{
  "claim": "...",
  "source_url": "...",
  "source_title": "...",
  "quote_or_summary": "...",
  "confidence": "low | medium | high",
  "supports_answer_part": "..."
}
```

## Known Limits

- Legacy `web_search` remains available for existing callers; new Echo Search
  callers should use the plan-driven workflow.
- Planning quality still depends on the selected model returning valid JSON.
- Static `httpx` extraction is preferred. Playwright fallback is opt-in because
  it is slower and requires browser runtime support.
- User-facing ToolRun identity / dual search-stack debt: see
  `docs/RUNTIME_CONTRACTS.md` §E and §J (Known limitations) and
  `docs/LIFECYCLE_TRUTHFULNESS.md` §5. Do not claim “one search row” is
  live-closed without Runtime §K / Lifecycle §11 validation.
