import { describe, expect, it } from "vitest";
import {
  buildChatEmbeds,
  parseScheduleList,
  parseWeatherStat,
  sourcesFromResearch,
} from "./buildChatEmbeds";
import type { ResearchRun } from "../research/types";

const sampleRun = (overrides?: Partial<ResearchRun>): ResearchRun => ({
  id: "r1",
  at: Date.now(),
  query: "Edmonton weather tomorrow",
  mode: "recent",
  recency_intent: true,
  evidence_count: 1,
  evidence: [
    {
      id: "e1",
      kind: "search_result",
      position: 1,
      query: "Edmonton weather",
      title: "Edmonton Weather Forecast",
      url: "https://www.accuweather.com/en/ca/edmonton/weather-forecast",
      domain: "accuweather.com",
      summary: "High 12 Low 3 cloudy",
      snippet: "High 12 Low 3 cloudy",
      content: "",
      page_title: "",
      published_raw: "",
      recency_bucket: "recent",
    },
  ],
  ...overrides,
});

describe("buildChatEmbeds", () => {
  it("parses weather high/low from answer text", () => {
    const w = parseWeatherStat("Edmonton tomorrow: high 12 low 3, cloudy.");
    expect(w == null).toEqual(false);
    expect(w?.high).toEqual("12");
    expect(w?.low).toEqual("3");
    expect((w?.place || "").toLowerCase().includes("edmonton")).toEqual(true);
  });

  it("parses schedule list with dates", () => {
    const s = parseScheduleList(
      "June 13, 2026: Brazil vs Morocco\nJune 14, 2026: Germany vs Curaçao\nJune 15, 2026: Spain vs Cape Verde"
    );
    expect(s == null).toEqual(false);
    expect((s?.items.length || 0) >= 2).toEqual(true);
    expect((s?.items[0]?.matchup || "").toLowerCase().includes("brazil")).toEqual(true);
  });

  it("builds sources from research (link card optional chrome)", () => {
    const embeds = buildChatEmbeds({
      answerText: "Edmonton high 12 low 3. FIFA slate tomorrow.",
      researchRuns: [sampleRun()],
      searchQueries: ["Edmonton weather tomorrow", "FIFA World Cup matches"],
    });
    const kinds = embeds.map((e) => e.kind);
    expect(kinds.includes("sources")).toEqual(true);
    expect(kinds.includes("weather_stat")).toEqual(true);
    expect(kinds.includes("query_chip")).toEqual(true);
    // Featured link may still be built; ChatEmbeds hides it when sources exist
  });

  it("dedupes sources by url", () => {
    const run = sampleRun();
    run.evidence.push({ ...run.evidence[0], id: "e2", position: 2 });
    const items = sourcesFromResearch([run]);
    expect(items.length).toEqual(1);
  });

  it("returns empty for plain chitchat with no research", () => {
    const embeds = buildChatEmbeds({ answerText: "Hey, how's it going?" });
    expect(embeds).toEqual([]);
  });

  it("does not invent sources or searched chips without research runs", () => {
    const embeds = buildChatEmbeds({
      answerText: "It will be 12 degrees in Edmonton tomorrow.",
      searchQueries: ["stale query from nowhere"],
    });
    const kinds = embeds.map((e) => e.kind);
    expect(kinds.includes("sources")).toEqual(false);
    expect(kinds.includes("query_chip")).toEqual(false);
    // Weather body embed may still parse from answer text
  });
});
