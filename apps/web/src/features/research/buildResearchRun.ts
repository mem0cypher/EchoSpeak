import { parseWebSearchOutput } from "./parseWebSearchOutput";
import type { ResearchEvidence, ResearchRun } from "./types";

const normalizeText = (value: unknown): string => String(value ?? "").replace(/\s+/g, " ").trim();

const inferMode = (query: string): "general" | "recent" => {
  const low = query.toLowerCase();
  return /(news|latest|recent|today|breaking|headline|update|war|conflict|crisis)/.test(low) ? "recent" : "general";
};

const extractQuery = (input: string): string => {
  const raw = String(input || "").trim();
  if (!raw) return "";
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && "query" in parsed) {
      return normalizeText((parsed as { query?: unknown }).query);
    }
  } catch {
  }
  const match = raw.match(/query\s*[:=]\s*['\"]([^'\"]+)['\"]/i);
  if (match) return normalizeText(match[1]);
  return normalizeText(raw);
};

const buildEvidenceFromParsed = (query: string, tool: string, results: ReturnType<typeof parseWebSearchOutput>): ResearchEvidence[] => {
  return results.map((result, index) => {
    let domain = "";
    try {
      domain = result.url ? new URL(result.url).host.replace(/^www\./, "") : "";
    } catch {
      domain = "";
    }
    return {
      id: `${tool}-${index + 1}-${result.url || result.title || index}`,
      kind: "search_result",
      position: index + 1,
      query,
      title: normalizeText(result.title) || "Untitled source",
      url: normalizeText(result.url),
      domain,
      summary: normalizeText(result.snippet),
      snippet: normalizeText(result.snippet),
      content: normalizeText(result.snippet),
      page_title: "",
      published_raw: "",
      published_at: null,
      recency_bucket: "unknown",
    };
  });
};

export const normalizeResearchRun = (value: unknown): ResearchRun | null => {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const evidence = Array.isArray(raw.evidence)
    ? raw.evidence
        .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
        .map((item, index) => ({
          id: normalizeText(item.id) || `evidence-${index + 1}`,
          kind: normalizeText(item.kind) || "search_result",
          position: Number(item.position) || index + 1,
          query: normalizeText(item.query),
          title: normalizeText(item.title) || "Untitled source",
          url: normalizeText(item.url),
          domain: normalizeText(item.domain),
          summary: normalizeText(item.summary),
          snippet: normalizeText(item.snippet),
          content: normalizeText(item.content),
          page_title: normalizeText(item.page_title),
          published_raw: normalizeText(item.published_raw),
          published_at: item.published_at == null ? null : normalizeText(item.published_at),
          recency_bucket: normalizeText(item.recency_bucket) || "unknown",
        }))
    : [];

  const query = normalizeText(raw.query);
  const mode = raw.mode === "recent" ? "recent" : inferMode(query);
  return {
    id: normalizeText(raw.id) || `research-${Date.now()}`,
    tool: normalizeText(raw.tool),
    at: Number(raw.at) || Date.now(),
    query,
    mode,
    recency_intent: Boolean(raw.recency_intent ?? mode === "recent"),
    evidence_count: Number(raw.evidence_count) || evidence.length,
    evidence,
  };
};

export const buildResearchRunFromToolEvent = (toolId: string, toolName: string, toolInput: string, output: string, at: number): ResearchRun | null => {
  if (toolName !== "web_search") return null;
  const query = extractQuery(toolInput);
  const evidence = buildEvidenceFromParsed(query, toolName, parseWebSearchOutput(output || ""));
  const mode = inferMode(query);
  return {
    id: toolId,
    tool: toolName,
    at,
    query,
    mode,
    recency_intent: mode === "recent",
    evidence_count: evidence.length,
    evidence,
  };
};
