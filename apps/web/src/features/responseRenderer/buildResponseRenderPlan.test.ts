import { describe, expect, it } from "vitest";
import { buildResponseRenderPlan } from "./buildResponseRenderPlan";
import type { ResearchRun } from "../research/types";

describe("buildResponseRenderPlan", () => {
  it("keeps plain chat as plain text", () => {
    const plan = buildResponseRenderPlan({ answerText: "That sounds good to me." });
    expect(plan.summaryText).toEqual("That sounds good to me.");
    expect(plan.blocks).toEqual([]);
  });

  it("accepts structured sections from response intent", () => {
    const plan = buildResponseRenderPlan({
      answerText: "Short answer.",
      intent: {
        blocks: [
          { id: "why", kind: "section", title: "Why", body: "Because the evidence points that way." },
          { id: "next", kind: "status", status: "running", items: [{ label: "Checking sources", status: "running" }] },
        ],
      },
    });
    expect(plan.blocks.map((block) => block.kind)).toEqual(["section", "status"]);
  });

  it("turns markdown comparison tables into table blocks and numeric charts", () => {
    const plan = buildResponseRenderPlan({
      answerText: [
        "Here is the comparison:",
        "",
        "| Team | Wins |",
        "| --- | ---: |",
        "| A | 12 |",
        "| B | 8 |",
      ].join("\n"),
    });
    expect(plan.summaryText.includes("| Team |")).toEqual(false);
    expect(plan.blocks.some((block) => block.kind === "table")).toEqual(true);
    expect(plan.blocks.some((block) => block.kind === "chart")).toEqual(true);
  });

  it("does not chart non-numeric tables", () => {
    const plan = buildResponseRenderPlan({
      answerText: [
        "| Choice | Note |",
        "| --- | --- |",
        "| A | Better docs |",
        "| B | Faster setup |",
      ].join("\n"),
    });
    expect(plan.blocks.some((block) => block.kind === "table")).toEqual(true);
    expect(plan.blocks.some((block) => block.kind === "chart")).toEqual(false);
  });

  it("never attaches Evidence cards to Chat render plans", () => {
    const run: ResearchRun = {
      id: "r1",
      at: Date.now(),
      query: "sample",
      mode: "recent",
      recency_intent: true,
      evidence_count: 1,
      evidence: [
        {
          id: "e1",
          kind: "search_result",
          position: 1,
          query: "sample",
          title: "Source title",
          url: "https://example.com/report",
          domain: "example.com",
          summary: "Retrieved fact",
          snippet: "",
          content: "",
          page_title: "",
          published_raw: "",
          recency_bucket: "recent",
        },
      ],
    };
    const plan = buildResponseRenderPlan({ answerText: "Answer with evidence.", researchRuns: [run] });
    expect(plan.blocks.some((block) => block.kind === "evidence")).toEqual(false);
    // Explicit evidence intent is also stripped from Chat.
    const forced = buildResponseRenderPlan({
      answerText: "Answer",
      intent: {
        blocks: [{ id: "ev", kind: "evidence", title: "Evidence", items: [{ title: "X", url: "https://x.test" }] }],
      },
    });
    expect(forced.blocks.some((block) => block.kind === "evidence")).toEqual(false);
  });
});
