import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
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
