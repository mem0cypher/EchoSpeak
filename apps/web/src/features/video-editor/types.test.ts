import { describe, expect, it } from "vitest";

import { secondsFromTime, timeFromSeconds } from "./types.ts";

describe("video rational-time projection", () => {
  it("keeps authoritative ticks as strings", () => {
    const value = timeFromSeconds(23.976);
    expect(value).toEqual({ ticks: "23976", time_base: { numerator: 1, denominator: 1000 } });
    expect(secondsFromTime(value)).toBeCloseTo(23.976, 6);
  });
});
