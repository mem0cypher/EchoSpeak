import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { ResponseRenderBlock, ResponseRenderPlan } from "./types";

type Palette = {
  panel2: string;
  line: string;
  text: string;
  textDim: string;
};

const labelStyle: React.CSSProperties = {
  fontSize: 10,
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  fontFamily: "'JetBrains Mono', ui-monospace, monospace",
};

const Panel: React.FC<{ title?: string; colors: Palette; children: React.ReactNode }> = ({ title, colors, children }) => (
  <div
    style={{
      border: `1px solid ${colors.line}`,
      background: "rgba(255,255,255,0.035)",
      borderRadius: 6,
      padding: "10px 12px",
      minWidth: 0,
      maxWidth: "100%",
      width: "100%",
      boxSizing: "border-box",
      overflowWrap: "anywhere",
      wordBreak: "break-word",
    }}
  >
    {title ? <div style={{ ...labelStyle, color: colors.textDim, marginBottom: 8 }}>{title}</div> : null}
    {children}
  </div>
);

const TableBlock: React.FC<{ block: Extract<ResponseRenderBlock, { kind: "table" }>; colors: Palette }> = ({ block, colors }) => (
  <Panel title={block.title} colors={colors}>
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5, lineHeight: 1.45 }}>
        <thead>
          <tr>
            {block.table.columns.map((col) => (
              <th key={col} style={{ color: colors.textDim, textAlign: "left", fontWeight: 600, padding: "0 10px 6px 0" }}>
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.table.rows.map((row, rowIdx) => (
            <tr key={rowIdx} style={{ borderTop: `1px solid ${colors.line}` }}>
              {row.map((cell, cellIdx) => (
                <td key={cellIdx} style={{ color: colors.text, padding: "6px 10px 5px 0", fontVariantNumeric: "tabular-nums" }}>
                  {String(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  </Panel>
);

const ChartBlock: React.FC<{ block: Extract<ResponseRenderBlock, { kind: "chart" }>; colors: Palette }> = ({ block, colors }) => {
  const values = block.chart.points.map((point) => point.value);
  const max = Math.max(...values.map((v) => Math.abs(v)), 1);
  return (
    <Panel title={block.title || "Chart"} colors={colors}>
      <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
        {block.chart.points.map((point) => {
          const pct = Math.max(3, Math.round((Math.abs(point.value) / max) * 100));
          return (
            <div key={point.label} style={{ display: "grid", gridTemplateColumns: "minmax(70px, 130px) 1fr auto", gap: 8, alignItems: "center" }}>
              <div style={{ color: colors.textDim, fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{point.label}</div>
              <div style={{ height: 6, background: "rgba(255,255,255,0.08)", borderRadius: 2, overflow: "hidden" }}>
                <div style={{ width: `${pct}%`, height: "100%", background: "rgba(190,205,255,0.78)", borderRadius: 2 }} />
              </div>
              <div style={{ color: colors.text, fontSize: 11, fontVariantNumeric: "tabular-nums" }}>
                {point.value}
                {block.chart.unit || ""}
              </div>
            </div>
          );
        })}
      </div>
    </Panel>
  );
};

const EvidenceBlock: React.FC<{ block: Extract<ResponseRenderBlock, { kind: "evidence" }>; colors: Palette }> = ({ block, colors }) => (
  <details style={{ border: `1px solid ${colors.line}`, background: "rgba(255,255,255,0.025)", borderRadius: 6, padding: "5px 8px" }}>
    <summary style={{ cursor: "pointer", color: colors.textDim, fontSize: 11, fontWeight: 600 }}>
      {block.title || "Sources"} ({block.items.length})
    </summary>
    <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 5 }}>
      {block.items.map((item, idx) => (
        <div key={`${item.url || item.title}-${idx}`} style={{ minWidth: 0 }}>
          <a
            href={item.url || undefined}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              color: colors.text,
              textDecoration: item.url ? "underline" : "none",
              textUnderlineOffset: 3,
              fontSize: 12.5,
              fontWeight: 600,
            }}
          >
            {item.title}
          </a>
          <div style={{ color: colors.textDim, fontSize: 10.5, marginTop: 1 }}>{item.domain}</div>
          {item.snippet ? <div style={{ color: "rgba(255,255,255,0.52)", fontSize: 11.5, lineHeight: 1.45, marginTop: 2 }}>{item.snippet}</div> : null}
        </div>
      ))}
    </div>
  </details>
);

const BlockView: React.FC<{ block: ResponseRenderBlock; colors: Palette }> = ({ block, colors }) => {
  if (block.kind === "section") {
    return (
      <Panel title={block.title} colors={colors}>
        <div className="chat-markdown chat-line-assistant" style={{ fontSize: 13.5 }}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.body}</ReactMarkdown>
        </div>
      </Panel>
    );
  }
  if (block.kind === "cards") {
    return (
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(128px, 1fr))", gap: 8 }}>
        {block.cards.map((card, idx) => (
          <Panel key={`${card.title}-${idx}`} title={card.title} colors={colors}>
            {card.value ? <div style={{ color: colors.text, fontSize: 18, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{card.value}</div> : null}
            {card.detail ? <div style={{ color: colors.textDim, fontSize: 11.5, lineHeight: 1.45, marginTop: card.value ? 4 : 0 }}>{card.detail}</div> : null}
          </Panel>
        ))}
      </div>
    );
  }
  if (block.kind === "table") return <TableBlock block={block} colors={colors} />;
  if (block.kind === "chart") return <ChartBlock block={block} colors={colors} />;
  if (block.kind === "evidence") return <EvidenceBlock block={block} colors={colors} />;
  if (block.kind === "timeline") {
    return (
      <Panel title={block.title || "Timeline"} colors={colors}>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {block.items.map((item, idx) => (
            <div key={`${item.label}-${idx}`} style={{ display: "grid", gridTemplateColumns: "72px 1fr", gap: 9 }}>
              <div style={{ color: colors.textDim, fontSize: 10.5, fontFamily: "'JetBrains Mono', ui-monospace, monospace" }}>{item.time || ""}</div>
              <div>
                <div style={{ color: colors.text, fontSize: 12.5, fontWeight: 650 }}>{item.label}</div>
                {item.detail ? <div style={{ color: colors.textDim, fontSize: 11.5, lineHeight: 1.45 }}>{item.detail}</div> : null}
              </div>
            </div>
          ))}
        </div>
      </Panel>
    );
  }
  if (block.kind === "status") {
    return (
      <Panel title={block.title || "Status"} colors={colors}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {block.items.map((item, idx) => (
            <div key={`${item.label}-${idx}`} style={{ display: "grid", gridTemplateColumns: "58px 1fr", gap: 8, alignItems: "baseline" }}>
              <span style={{ ...labelStyle, color: colors.textDim }}>{item.status || block.status}</span>
              <span style={{ color: colors.text, fontSize: 12.5 }}>
                {item.label}
                {item.detail ? <span style={{ color: colors.textDim }}> - {item.detail}</span> : null}
              </span>
            </div>
          ))}
        </div>
      </Panel>
    );
  }
  return null;
};

export const ResponseRenderer: React.FC<{ plan?: ResponseRenderPlan; fallbackText: string; colors: Palette; stillTyping?: boolean }> = ({
  plan,
  fallbackText,
  colors,
  stillTyping = false,
}) => {
  const text = plan?.summaryText ?? fallbackText;
  const blocks = stillTyping ? [] : plan?.blocks || [];
  return (
    <>
      {text ? (
        <div className="chat-markdown chat-line-assistant">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
        </div>
      ) : null}
      {blocks.length ? (
        <div
          style={{
            marginTop: text ? 6 : 0,
            display: "flex",
            flexDirection: "column",
            gap: 5,
            width: "100%",
            maxWidth: "100%",
            minWidth: 0,
            boxSizing: "border-box",
          }}
        >
          {blocks.map((block) => (
            <BlockView key={block.id} block={block} colors={colors} />
          ))}
        </div>
      ) : null}
    </>
  );
};
