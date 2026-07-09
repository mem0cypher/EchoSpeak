/**
 * Derive chat embeds from research evidence + answer text.
 * Pure / deterministic — UI filters what to show so the bubble stays professional.
 */

import type { ResearchRun } from "../research/types";
import type { ChatEmbed, ChatEmbedSourceItem } from "./types";

const normalize = (v: unknown) => String(v ?? "").replace(/\s+/g, " ").trim();

const domainOf = (url: string): string => {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return "";
  }
};

/** Collect unique source cards from research runs for this turn. */
export const sourcesFromResearch = (runs: ResearchRun[], limit = 6): ChatEmbedSourceItem[] => {
  const out: ChatEmbedSourceItem[] = [];
  const seen = new Set<string>();
  for (const run of runs || []) {
    for (const ev of run.evidence || []) {
      const url = normalize(ev.url);
      const title = normalize(ev.title);
      if (!url && !title) continue;
      const key = (url || title).toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({
        id: ev.id || `src-${out.length}`,
        title: title || domainOf(url) || "Source",
        url,
        domain: normalize(ev.domain) || domainOf(url),
        // Keep enough snippet so "what it found" expand is useful in chat
        snippet: normalize(ev.summary || ev.snippet || ev.content).slice(0, 320),
        recency: ev.recency_bucket && ev.recency_bucket !== "unknown" ? ev.recency_bucket : undefined,
      });
      if (out.length >= limit) return out;
    }
  }
  return out;
};

/** Parse weather-ish numbers from assistant text or evidence blob. */
export const parseWeatherStat = (
  text: string
): Extract<ChatEmbed, { kind: "weather_stat" }> | null => {
  const hay = normalize(text);
  if (!hay) return null;
  const looksWeather =
    /\b(weather|forecast|°|degrees?|high|low|temp|celsius|fahrenheit|cloudy|sunny|rain|snow)\b/i.test(
      hay
    );
  if (!looksWeather) return null;

  const placeMatch = hay.match(
    /\b(?:in|for)\s+([A-Z][a-zA-Z.'-]{2,}(?:\s+[A-Z][a-zA-Z.'-]{2,})?)\b/
  ) || hay.match(
    /\b(Edmonton|Calgary|Vancouver|Toronto|Montreal|Winnipeg|Ottawa|Seattle|Denver|Boston|Chicago|Dallas)\b/
  );
  const place = placeMatch ? placeMatch[1] : undefined;

  // high 12 / high of 12 / highs near 56
  const highM = hay.match(/\bhigh(?:s)?\s*(?:near|around|of|:)?\s*(-?\d{1,3})\s*°?\s*([cCfF])?\b/i);
  const lowM = hay.match(/\blow(?:s)?\s*(?:near|around|of|:)?\s*(-?\d{1,3})\s*°?\s*([cCfF])?\b/i);
  // 12/3 or 56°/39°
  const slash = hay.match(/\b(-?\d{1,3})\s*°?\s*[\/–—-]\s*(-?\d{1,3})\s*°?\s*([cCfF])?\b/);
  const currentM = hay.match(
    /\b(?:currently|current(?:ly)?|now)\s*(?:around|near|at|:)?\s*(-?\d{1,3})\s*°?\s*([cCfF])?\b/i
  );

  let high = highM?.[1];
  let low = lowM?.[1];
  let unit = (highM?.[2] || lowM?.[2] || slash?.[3] || currentM?.[2] || "").toUpperCase() || undefined;
  if (slash && !high && !low) {
    high = slash[1];
    low = slash[2];
  }
  const current = currentM?.[1];
  if (!high && !low && !current) return null;

  const conditionM = hay.match(
    /\b(sunny|clear|cloudy|overcast|rain(?:y)?|snow(?:y)?|stormy|foggy|windy|partly cloudy)\b/i
  );

  return {
    id: "weather-stat",
    kind: "weather_stat",
    place,
    high,
    low,
    current,
    unit: unit === "C" || unit === "F" ? unit : unit || undefined,
    condition: conditionM?.[1],
    rawHint: undefined,
  };
};

/** Lightweight schedule rows from text (vs lines with dates). */
export const parseScheduleList = (
  text: string
): Extract<ChatEmbed, { kind: "schedule_list" }> | null => {
  const lines = String(text || "")
    .split(/\n|•|\*|·/)
    .map((l) => l.trim())
    .filter(Boolean);
  const items: { when?: string; matchup: string; note?: string }[] = [];
  for (const line of lines) {
    // June 13, 2026: Brazil vs Morocco
    const m = line.match(
      /^(?:[-–—]\s*)?((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,?\s+\d{4})?|(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)[^:]*|\d{4}-\d{2}-\d{2})\s*[:\-–—]\s*(.+)$/i
    );
    if (m) {
      const matchup = normalize(m[2]);
      if (/\bvs\.?\b|versus|@/i.test(matchup) || matchup.length > 6) {
        items.push({ when: normalize(m[1]), matchup });
      }
      continue;
    }
    if (/\bvs\.?\b|versus/i.test(line) && line.length < 120) {
      items.push({ matchup: normalize(line.replace(/^[-–—•*]\s*/, "")) });
    }
  }
  if (items.length < 2) return null;
  return {
    id: "schedule-list",
    kind: "schedule_list",
    title: "Fixtures",
    items: items.slice(0, 8),
  };
};

export type BuildChatEmbedsInput = {
  answerText: string;
  researchRuns?: ResearchRun[];
  searchQueries?: string[];
};

/**
 * Build ordered embeds for an assistant final bubble.
 * Filter rules keep chrome sparse: only show when evidence is real.
 */
export const buildChatEmbeds = (input: BuildChatEmbedsInput): ChatEmbed[] => {
  const answer = normalize(input.answerText);
  const runs = input.researchRuns || [];
  const embeds: ChatEmbed[] = [];

  const queries = (input.searchQueries || [])
    .map(normalize)
    .filter(Boolean)
    .slice(0, 4);
  const runQueries = runs.map((r) => normalize(r.query)).filter(Boolean);
  const allQueries = [...new Set([...queries, ...runQueries])].slice(0, 4);
  if (allQueries.length >= 2) {
    embeds.push({ id: "queries", kind: "query_chip", queries: allQueries });
  }

  const weather = parseWeatherStat(answer);
  if (weather) embeds.push(weather);

  // Also try evidence snippets if answer was thin
  if (!weather) {
    const blob = runs
      .flatMap((r) => (r.evidence || []).map((e) => e.summary || e.snippet || ""))
      .join(" ");
    const fromEv = parseWeatherStat(blob);
    if (fromEv) embeds.push({ ...fromEv, id: "weather-stat-ev" });
  }

  const schedule = parseScheduleList(answer);
  if (schedule) embeds.push(schedule);

  const sources = sourcesFromResearch(runs, 6);
  if (sources.length >= 1) {
    embeds.push({
      id: "sources",
      kind: "sources",
      title: sources.length === 1 ? "Source" : "Sources",
      items: sources,
    });
    // Top source as featured link card (ChatGPT-style citation card)
    const top = sources[0];
    if (top.url) {
      embeds.push({
        id: `link-${top.id}`,
        kind: "link_card",
        title: top.title,
        url: top.url,
        domain: top.domain || domainOf(top.url),
        snippet: top.snippet,
        faviconLetter: (top.domain || top.title || "?").charAt(0).toUpperCase(),
      });
    }
  }

  return embeds;
};
