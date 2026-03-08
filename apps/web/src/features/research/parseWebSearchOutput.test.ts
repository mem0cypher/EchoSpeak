import { describe, expect, it } from "vitest";
import { parseWebSearchOutput } from "./parseWebSearchOutput";

describe("parseWebSearchOutput", () => {
  it("parses numbered search results into structured entries", () => {
    const output = [
      "1. EchoSpeak Architecture Audit",
      "URL: https://example.com/audit",
      "Deep review of the backend and frontend architecture.",
      "",
      "2. EchoSpeak Roadmap",
      "URL: https://example.com/roadmap",
      "Three implementation phases for the platform.",
    ].join("\n");

    expect(parseWebSearchOutput(output)).toEqual([
      {
        title: "EchoSpeak Architecture Audit",
        url: "https://example.com/audit",
        snippet: "Deep review of the backend and frontend architecture.",
      },
      {
        title: "EchoSpeak Roadmap",
        url: "https://example.com/roadmap",
        snippet: "Three implementation phases for the platform.",
      },
    ]);
  });

  it("returns an empty list for empty or no-result payloads", () => {
    expect(parseWebSearchOutput("")).toEqual([]);
    expect(parseWebSearchOutput("No search results were found.")).toEqual([]);
  });
});
