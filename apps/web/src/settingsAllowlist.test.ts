import { describe, expect, it } from "vitest";
import {
  coerceAllowlistValue,
  commitAllowlistDraft,
  normalizeAllowlistEntry,
  parseAllowlistText,
  removeAllowlistEntry,
} from "./settingsAllowlist";

describe("open application allowlist", () => {
  it("preserves spaces and hyphens inside a single entry", () => {
    expect(normalizeAllowlistEntry("  Visual Studio Code  ")).toEqual("visual studio code");
    expect(normalizeAllowlistEntry("Google-Chrome")).toEqual("google-chrome");
  });

  it("parses comma and newline pasted values into a canonical array", () => {
    expect(parseAllowlistText("notepad, calc\nGoogle Chrome;paint")).toEqual([
      "notepad",
      "calc",
      "google chrome",
      "paint",
    ]);
  });

  it("migrates legacy single-string storage", () => {
    expect(coerceAllowlistValue("notepad,calc")).toEqual(["notepad", "calc"]);
    expect(coerceAllowlistValue(["Notepad", "calc", "notepad"])).toEqual(["notepad", "calc"]);
  });

  it("adds entries on comma without splitting mid-name spaces", () => {
    const step1 = commitAllowlistDraft([], "visual studio code");
    expect(step1.entries).toEqual([]);
    expect(step1.draft).toEqual("visual studio code");
    const step2 = commitAllowlistDraft(step1.entries, "visual studio code,");
    expect(step2.entries).toEqual(["visual studio code"]);
    expect(step2.draft).toEqual("");
  });

  it("adds on force (Enter/Add) and removes individually", () => {
    const added = commitAllowlistDraft(["notepad"], "google chrome", { force: true });
    expect(added.entries).toEqual(["notepad", "google chrome"]);
    expect(removeAllowlistEntry(added.entries, "NOTEPAD")).toEqual(["google chrome"]);
  });
});
