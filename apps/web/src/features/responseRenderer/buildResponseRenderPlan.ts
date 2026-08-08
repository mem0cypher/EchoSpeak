import type {
  BuildResponseRenderPlanInput,
  ResponseRenderBlock,
  ResponseRenderChartPoint,
  ResponseRenderPlan,
  ResponseRenderTable,
} from "./types";

const normalize = (value: unknown): string => String(value ?? "").replace(/\s+/g, " ").trim();

const domainOf = (url: string): string => {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return "";
  }
};

const asNumber = (value: unknown): number | null => {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const cleaned = String(value ?? "").replace(/[%,$]/g, "").trim();
  if (!cleaned || !/^-?\d+(?:\.\d+)?$/.test(cleaned)) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
};

const isValidTable = (table?: ResponseRenderTable): table is ResponseRenderTable =>
  Boolean(
    table &&
      Array.isArray(table.columns) &&
      table.columns.length > 0 &&
      Array.isArray(table.rows) &&
      table.rows.length > 0 &&
      table.rows.every((row) => Array.isArray(row) && row.length === table.columns.length)
  );

const chartFromTable = (table: ResponseRenderTable, title?: string): ResponseRenderBlock | null => {
  if (table.columns.length < 2 || table.rows.length < 2 || table.rows.length > 8) return null;
  const points: ResponseRenderChartPoint[] = [];
  for (const row of table.rows) {
    const label = normalize(row[0]);
    const value = asNumber(row[1]);
    if (!label || value == null) return null;
    points.push({ label, value });
  }
  return {
    id: `chart-${title || "table"}`,
    kind: "chart",
    title,
    chart: { type: "bar", points },
  };
};

const sanitizeBlock = (block: ResponseRenderBlock, index: number): ResponseRenderBlock | null => {
  const id = normalize(block.id) || `block-${index}`;
  if (block.kind === "section") {
    const title = normalize(block.title);
    const body = normalize(block.body);
    return title && body ? { ...block, id, title, body } : null;
  }
  if (block.kind === "cards") {
    const cards = (block.cards || [])
      .map((card) => ({
        title: normalize(card.title),
        value: normalize(card.value),
        detail: normalize(card.detail),
      }))
      .filter((card) => card.title || card.value || card.detail)
      .slice(0, 6);
    return cards.length ? { ...block, id, title: normalize(block.title), cards } : null;
  }
  if (block.kind === "table") {
    if (!isValidTable(block.table)) return null;
    const table = {
      columns: block.table.columns.map(normalize).filter(Boolean),
      rows: block.table.rows.slice(0, 12),
    };
    return table.columns.length === block.table.columns.length
      ? { ...block, id, title: normalize(block.title), table }
      : null;
  }
  if (block.kind === "timeline") {
    const items = (block.items || [])
      .map((item) => ({
        label: normalize(item.label),
        detail: normalize(item.detail),
        time: normalize(item.time),
      }))
      .filter((item) => item.label)
      .slice(0, 8);
    return items.length ? { ...block, id, title: normalize(block.title), items } : null;
  }
  if (block.kind === "status") {
    const items = (block.items || [])
      .map((item) => ({
        label: normalize(item.label),
        detail: normalize(item.detail),
        status: item.status,
      }))
      .filter((item) => item.label)
      .slice(0, 8);
    return items.length ? { ...block, id, title: normalize(block.title), items } : null;
  }
  if (block.kind === "evidence") {
    const items = (block.items || [])
      .map((item) => ({
        title: normalize(item.title || item.domain || item.url),
        url: normalize(item.url),
        domain: normalize(item.domain) || domainOf(normalize(item.url)),
        snippet: normalize(item.snippet).slice(0, 260),
      }))
      .filter((item) => item.title || item.url)
      .slice(0, 6);
    return items.length ? { ...block, id, title: normalize(block.title) || "Evidence", items } : null;
  }
  if (block.kind === "chart") {
    const points = (block.chart?.points || [])
      .map((point) => ({ label: normalize(point.label), value: asNumber(point.value) }))
      .filter((point): point is ResponseRenderChartPoint => Boolean(point.label) && point.value != null)
      .slice(0, 8);
    if (points.length < 2) return null;
    return {
      ...block,
      id,
      title: normalize(block.title),
      chart: { type: "bar", unit: normalize(block.chart?.unit), points },
    };
  }
  return null;
};

const stripRenderedMarkdownTables = (text: string): string => {
  const lines = String(text || "").split(/\r?\n/);
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const next = lines[i + 1] || "";
    if (line.includes("|") && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(next)) {
      i += 1;
      while (i + 1 < lines.length && lines[i + 1].includes("|")) i += 1;
      continue;
    }
    out.push(line);
  }
  return out.join("\n").replace(/\n{3,}/g, "\n\n").trim();
};

const parseMarkdownTables = (text: string): ResponseRenderBlock[] => {
  const lines = String(text || "").split(/\r?\n/);
  const blocks: ResponseRenderBlock[] = [];
  for (let i = 0; i < lines.length - 1; i++) {
    const header = lines[i];
    const separator = lines[i + 1];
    if (!header.includes("|")) continue;
    if (!/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(separator)) continue;

    const split = (line: string) =>
      line
        .trim()
        .replace(/^\|/, "")
        .replace(/\|$/, "")
        .split("|")
        .map((cell) => normalize(cell));
    const columns = split(header);
    const rows: Array<Array<string | number>> = [];
    let j = i + 2;
    while (j < lines.length && lines[j].includes("|")) {
      const row = split(lines[j]);
      if (row.length === columns.length) rows.push(row);
      j += 1;
    }
    if (columns.length >= 2 && rows.length > 0) {
      const table = { columns, rows };
      blocks.push({ id: `table-${blocks.length}`, kind: "table", table });
      const chart = chartFromTable(table, columns[1]);
      if (chart) blocks.push(chart);
    }
    i = j;
  }
  return blocks;
};

const sectionsFromText = (text: string): ResponseRenderBlock[] => {
  const matches = [...String(text || "").matchAll(/^#{2,3}\s+(.+?)\s*$/gm)];
  if (matches.length < 2) return [];
  const blocks: ResponseRenderBlock[] = [];
  for (let i = 0; i < matches.length; i++) {
    const match = matches[i];
    const next = matches[i + 1];
    const title = normalize(match[1]);
    const start = (match.index || 0) + match[0].length;
    const end = next?.index ?? text.length;
    const body = text.slice(start, end).trim();
    if (title && body) blocks.push({ id: `section-${i}`, kind: "section", title, body });
  }
  return blocks.length >= 2 ? blocks.slice(0, 6) : [];
};

const evidenceFromResearch = (input: BuildResponseRenderPlanInput): ResponseRenderBlock | null => {
  const items = (input.researchRuns || [])
    .flatMap((run) => run.evidence || [])
    .map((ev) => {
      const url = normalize(ev.url);
      return {
        title: normalize(ev.title) || domainOf(url) || "Source",
        url,
        domain: normalize(ev.domain) || domainOf(url),
        snippet: normalize(ev.summary || ev.snippet || ev.content).slice(0, 260),
      };
    })
    .filter((item, index, all) => {
      const key = (item.url || item.title).toLowerCase();
      return key && all.findIndex((other) => (other.url || other.title).toLowerCase() === key) === index;
    })
    .slice(0, 6);
  if (!items.length) return null;
  return { id: "evidence", kind: "evidence", title: "Evidence", items };
};

export const buildResponseRenderPlan = (input: BuildResponseRenderPlanInput): ResponseRenderPlan => {
  const answerText = String(input.answerText || "").trim();
  const explicitBlocks = (input.intent?.blocks || [])
    .map(sanitizeBlock)
    .filter((block): block is ResponseRenderBlock => Boolean(block));
  const inferredTables = explicitBlocks.some((block) => block.kind === "table") ? [] : parseMarkdownTables(answerText);
  const inferredSections = explicitBlocks.length || inferredTables.length ? [] : sectionsFromText(answerText);
  // Chat never shows an Evidence card/bar. Evidence remains durable in backend,
  // Studio Viewer, and Research artifacts — not permanent chat chrome.
  const blocks = [
    ...explicitBlocks.filter((block) => block.kind !== "evidence"),
    ...inferredTables,
    ...inferredSections,
  ];
  return {
    summaryText: inferredTables.length ? stripRenderedMarkdownTables(answerText) : answerText,
    blocks,
  };
};
