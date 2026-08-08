/**
 * Open-application allowlist draft helpers.
 * Entries may contain spaces and hyphens; only comma / newline / Enter commits a token.
 */

export function normalizeAllowlistEntry(raw: string): string {
  return String(raw || "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

/** Split pasted or stored multi-value text into entries (comma / newline / semicolon). */
export function parseAllowlistText(raw: string): string[] {
  const parts = String(raw || "")
    .split(/[\n,;]+/)
    .map(normalizeAllowlistEntry)
    .filter(Boolean);
  return dedupeAllowlist(parts);
}

/** Canonical string[] for API persistence. Migrates legacy single-string forms. */
export function coerceAllowlistValue(value: unknown): string[] {
  if (Array.isArray(value)) {
    return dedupeAllowlist(value.map((v) => normalizeAllowlistEntry(String(v ?? ""))).filter(Boolean));
  }
  if (typeof value === "string") {
    return parseAllowlistText(value);
  }
  return [];
}

export function dedupeAllowlist(entries: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const entry of entries) {
    const key = normalizeAllowlistEntry(entry);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(key);
  }
  return out;
}

/**
 * Commit draft buffer into the list.
 * `force` commits the whole buffer (Enter / Add); otherwise comma/newline commits tokens.
 */
export function commitAllowlistDraft(
  existing: string[],
  draft: string,
  opts?: { force?: boolean }
): { entries: string[]; draft: string } {
  const text = String(draft || "");
  if (opts?.force) {
    return { entries: dedupeAllowlist([...existing, ...parseAllowlistText(text)]), draft: "" };
  }
  if (!/[\n,]/.test(text)) {
    return { entries: dedupeAllowlist(existing), draft: text };
  }
  const endsWithSep = /[\n,]\s*$/.test(text);
  const parts = text.split(/[\n,]/);
  const committed = endsWithSep ? parts : parts.slice(0, -1);
  const tail = endsWithSep ? "" : String(parts[parts.length - 1] ?? "");
  const toAdd = committed.map(normalizeAllowlistEntry).filter(Boolean);
  return {
    entries: dedupeAllowlist([...existing, ...toAdd]),
    draft: tail,
  };
}

export function removeAllowlistEntry(entries: string[], target: string): string[] {
  const key = normalizeAllowlistEntry(target);
  return dedupeAllowlist(entries).filter((e) => e !== key);
}
