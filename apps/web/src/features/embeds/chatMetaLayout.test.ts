import { describe, expect, it } from "vitest";
import { buildChatEmbeds } from "./buildChatEmbeds";
import { buildResponseRenderPlan } from "../responseRenderer/buildResponseRenderPlan";
import type { ResearchRun } from "../research/types";

const sampleRun: ResearchRun = {
  id: "r1",
  at: Date.now(),
  query: "weather tokyo",
  mode: "recent",
  recency_intent: true,
  evidence_count: 1,
  evidence: [
    {
      id: "e1",
      kind: "search_result",
      position: 1,
      query: "weather tokyo",
      title: "Weather report",
      url: "https://example.com/wx",
      domain: "example.com",
      summary: "Sunny",
      snippet: "",
      content: "",
      page_title: "",
      published_raw: "",
      recency_bucket: "recent",
    },
  ],
};

describe("chat meta: no Evidence bar; Sources/Search as compact meta", () => {
  it("does not place evidence blocks on completed chat plans", () => {
    const plan = buildResponseRenderPlan({
      answerText: "Tokyo will be sunny.",
      researchRuns: [sampleRun],
    });
    expect(plan.blocks.some((b) => b.kind === "evidence")).toEqual(false);
    expect(plan.blocks.some((b) => String(b.title || "").toLowerCase() === "evidence")).toEqual(false);
  });

  it("exposes sources and search queries for the compact meta row only", () => {
    const embeds = buildChatEmbeds({
      answerText: "Tokyo will be sunny.",
      researchRuns: [sampleRun],
      searchQueries: ["weather tokyo"],
    });
    const kinds = embeds.map((e) => e.kind);
    expect(kinds.includes("sources")).toEqual(true);
    expect(kinds.includes("query_chip")).toEqual(true);
    // No evidence embed kind exists in chat embeds
    expect(kinds.includes("evidence" as any)).toEqual(false);
  });
});
