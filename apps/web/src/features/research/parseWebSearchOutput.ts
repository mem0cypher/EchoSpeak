export type ResearchResult = {
  title: string;
  url: string;
  snippet: string;
};

export const parseWebSearchOutput = (output: string): ResearchResult[] => {
  const txt = (output || "").trim();
  if (!txt || txt.toLowerCase().includes("no search results")) return [];
  const blocks = txt.split(/\n\s*\n/g).map((b) => b.trim()).filter(Boolean);
  const results: ResearchResult[] = [];
  for (const block of blocks) {
    const lines = block.split("\n").map((l) => l.trim()).filter(Boolean);
    if (!lines.length) continue;
    const m = lines[0].match(/^\d+\.\s*(.*)$/);
    const title = (m ? m[1] : lines[0]).trim();
    const urlLine = lines.find((l) => l.toLowerCase().startsWith("url:")) || "";
    const url = urlLine.replace(/^url:\s*/i, "").trim();
    const snippetLines = lines.filter((l) => !l.toLowerCase().startsWith("url:") && l !== lines[0]);
    const snippet = snippetLines.join(" ").trim();
    if (!title && !snippet) continue;
    results.push({ title, url, snippet });
  }
  return results;
};
