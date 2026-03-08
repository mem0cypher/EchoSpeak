import { describe, expect, it } from "vitest";
import { buildResearchRunFromToolEvent, normalizeResearchRun } from "./buildResearchRun";

describe("buildResearchRun", () => {
  it("normalizes backend research payloads", () => {
    const run = normalizeResearchRun({
      id: "run-1",
      tool: "web_search",
      at: 123,
      query: "latest echospeak roadmap",
      mode: "recent",
      recency_intent: true,
      evidence_count: 1,
      evidence: [
        {
          id: "e1",
          kind: "search_result",
          position: 1,
          query: "latest echospeak roadmap",
          title: "EchoSpeak Roadmap",
          url: "https://example.com/roadmap",
          domain: "example.com",
          summary: "Phase 2 progress",
          snippet: "Phase 2 progress",
          content: "Phase 2 progress",
          page_title: "EchoSpeak Roadmap",
          published_raw: "2 hours ago",
          published_at: "2026-03-06T00:00:00+00:00",
          recency_bucket: "breaking",
        },
      ],
    });

    expect(run).toEqual({
      id: "run-1",
      tool: "web_search",
      at: 123,
      query: "latest echospeak roadmap",
      mode: "recent",
      recency_intent: true,
      evidence_count: 1,
      evidence: [
        {
          id: "e1",
          kind: "search_result",
          position: 1,
          query: "latest echospeak roadmap",
          title: "EchoSpeak Roadmap",
          url: "https://example.com/roadmap",
          domain: "example.com",
          summary: "Phase 2 progress",
          snippet: "Phase 2 progress",
          content: "Phase 2 progress",
          page_title: "EchoSpeak Roadmap",
          published_raw: "2 hours ago",
          published_at: "2026-03-06T00:00:00+00:00",
          recency_bucket: "breaking",
        },
      ],
    });
  });

  it("builds a fallback research run from legacy tool output", () => {
    const run = buildResearchRunFromToolEvent(
      "run-2",
      "web_search",
      '{"query":"latest echospeak roadmap"}',
      [
        "1. EchoSpeak Roadmap",
        "URL: https://example.com/roadmap",
        "Phase 2 research lane completed.",
      ].join("\n"),
      456,
    );

    expect(run).toEqual({
      id: "run-2",
      tool: "web_search",
      at: 456,
      query: "latest echospeak roadmap",
      mode: "recent",
      recency_intent: true,
      evidence_count: 1,
      evidence: [
        {
          id: "web_search-1-https://example.com/roadmap",
          kind: "search_result",
          position: 1,
          query: "latest echospeak roadmap",
          title: "EchoSpeak Roadmap",
          url: "https://example.com/roadmap",
          domain: "example.com",
          summary: "Phase 2 research lane completed.",
          snippet: "Phase 2 research lane completed.",
          content: "Phase 2 research lane completed.",
          page_title: "",
          published_raw: "",
          published_at: null,
          recency_bucket: "unknown",
        },
      ],
    });
  });
});
