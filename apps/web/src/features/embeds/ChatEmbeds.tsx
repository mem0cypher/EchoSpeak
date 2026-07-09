import React, { useState } from "react";
import type { ChatEmbed, ChatEmbedSourceItem } from "./types";

type Palette = {
  panel2: string;
  line: string;
  text: string;
  textDim: string;
};

/** Same visual language as the bubble time / token meta line. */
const metaFont: React.CSSProperties = {
  fontSize: 10,
  color: "rgba(255,255,255,0.28)",
  fontFamily: "'JetBrains Mono', ui-monospace, monospace",
  letterSpacing: "0.06em",
};

const shortLabel = (item: ChatEmbedSourceItem): string => {
  const d = (item.domain || "").replace(/^www\./, "").trim();
  if (d) return d.length > 22 ? `${d.slice(0, 20)}…` : d;
  const t = (item.title || "source").trim();
  return t.length > 22 ? `${t.slice(0, 20)}…` : t;
};

const BODY_KINDS = new Set(["weather_stat", "stat_row", "schedule_list"]);
const FOOTER_KINDS = new Set(["sources", "query_chip", "link_card"]);

/**
 * Collapsed toggle + expand panel for sources.
 * When `inline`, no outer block margin (lives in a horizontal footer row).
 */
const SourcesToggle: React.FC<{
  embed: Extract<ChatEmbed, { kind: "sources" }>;
  open: boolean;
  onToggle: () => void;
}> = ({ embed, open, onToggle }) => {
  const n = embed.items.length;
  if (!n) return null;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      style={{
        ...metaFont,
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        padding: 0,
        border: "none",
        background: "none",
        cursor: "pointer",
        userSelect: "none",
      }}
      title={open ? "Hide sources" : "Show sources"}
    >
      <span style={{ opacity: 0.9 }}>sources · {n}</span>
      <span style={{ opacity: 0.45, fontSize: 9 }}>{open ? "▾" : "▸"}</span>
    </button>
  );
};

const SourcesExpanded: React.FC<{
  embed: Extract<ChatEmbed, { kind: "sources" }>;
}> = ({ embed }) => {
  const [detailId, setDetailId] = useState<string | null>(null);
  return (
    <div
      style={{
        marginTop: 4,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        width: "100%",
      }}
    >
      {embed.items.map((item, i) => {
        const label = shortLabel(item);
        const hasUrl = Boolean(item.url);
        const detail = detailId === item.id;
        const hasSnippet = Boolean(item.snippet?.trim());
        return (
          <div key={item.id} style={{ minWidth: 0 }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                ...metaFont,
                color: "rgba(255,255,255,0.38)",
                lineHeight: 1.45,
              }}
            >
              <span style={{ opacity: 0.4, minWidth: 12 }}>{i + 1}</span>
              {hasUrl ? (
                <a
                  href={item.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={item.title || item.url}
                  style={{
                    color: "rgba(255,255,255,0.42)",
                    textDecoration: "none",
                    borderBottom: "1px dotted rgba(255,255,255,0.16)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    maxWidth: 200,
                  }}
                  className="chat-embed-source-link"
                >
                  {label}
                </a>
              ) : (
                <span
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    maxWidth: 200,
                  }}
                >
                  {label}
                </span>
              )}
              {hasSnippet || hasUrl ? (
                <button
                  type="button"
                  onClick={() => setDetailId((cur) => (cur === item.id ? null : item.id))}
                  style={{
                    ...metaFont,
                    padding: 0,
                    border: "none",
                    background: "none",
                    cursor: "pointer",
                    opacity: 0.55,
                    flexShrink: 0,
                  }}
                >
                  {detail ? "less" : "more"}
                </button>
              ) : null}
            </div>
            {detail ? (
              <div
                style={{
                  margin: "2px 0 4px 18px",
                  ...metaFont,
                  color: "rgba(255,255,255,0.32)",
                  letterSpacing: "0.02em",
                  lineHeight: 1.4,
                  maxWidth: 420,
                }}
              >
                {item.title ? (
                  <div
                    style={{
                      color: "rgba(255,255,255,0.45)",
                      marginBottom: 2,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {item.title}
                  </div>
                ) : null}
                {hasSnippet ? (
                  <div
                    style={{
                      display: "-webkit-box",
                      WebkitLineClamp: 3,
                      WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                    }}
                  >
                    {item.snippet}
                  </div>
                ) : null}
                {hasUrl ? (
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      display: "inline-block",
                      marginTop: 3,
                      color: "rgba(255,255,255,0.4)",
                      textDecoration: "underline",
                      textUnderlineOffset: 2,
                      wordBreak: "break-all",
                      fontSize: 9,
                    }}
                  >
                    open ↗
                  </a>
                ) : null}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
};

const SearchedToggle: React.FC<{
  embed: Extract<ChatEmbed, { kind: "query_chip" }>;
  open: boolean;
  onToggle: () => void;
}> = ({ embed, open, onToggle }) => {
  const n = embed.queries.length;
  if (!n) return null;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      style={{
        ...metaFont,
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        padding: 0,
        border: "none",
        background: "none",
        cursor: "pointer",
        userSelect: "none",
      }}
      title={open ? "Hide searches" : "Show searches"}
    >
      <span>searched · {n}</span>
      <span style={{ opacity: 0.45, fontSize: 9 }}>{open ? "▾" : "▸"}</span>
    </button>
  );
};

const SearchedExpanded: React.FC<{
  embed: Extract<ChatEmbed, { kind: "query_chip" }>;
}> = ({ embed }) => (
  <div style={{ marginTop: 4, display: "flex", flexDirection: "column", gap: 2, width: "100%" }}>
    {embed.queries.map((q) => (
      <div
        key={q}
        title={q}
        style={{
          ...metaFont,
          color: "rgba(255,255,255,0.34)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          maxWidth: 420,
          letterSpacing: "0.02em",
        }}
      >
        {q}
      </div>
    ))}
  </div>
);

const WeatherStat: React.FC<{
  embed: Extract<ChatEmbed, { kind: "weather_stat" }>;
  colors: Palette;
}> = ({ embed, colors }) => {
  const unit = embed.unit ? `°${embed.unit}` : "°";
  return (
    <div style={{ padding: "8px 0 2px", display: "flex", alignItems: "stretch", gap: 12 }}>
      <div
        style={{
          width: 36,
          height: 36,
          borderRadius: 8,
          background: "rgba(120,180,255,0.1)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 18,
          flexShrink: 0,
        }}
        aria-hidden
      >
        {embed.condition && /rain/i.test(embed.condition)
          ? "🌧"
          : embed.condition && /snow/i.test(embed.condition)
            ? "❄"
            : embed.condition && /cloud/i.test(embed.condition)
              ? "☁"
              : "☀"}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 11, color: colors.textDim, letterSpacing: "0.06em", textTransform: "uppercase" }}>
          {embed.place || "Forecast"}
          {embed.condition ? ` · ${embed.condition}` : ""}
        </div>
        <div style={{ display: "flex", gap: 14, marginTop: 4, flexWrap: "wrap", alignItems: "baseline" }}>
          {embed.high != null ? (
            <div>
              <span style={{ fontSize: 10, color: colors.textDim }}>High</span>
              <div style={{ fontSize: 20, fontWeight: 700, color: colors.text, fontVariantNumeric: "tabular-nums" }}>
                {embed.high}
                {unit}
              </div>
            </div>
          ) : null}
          {embed.low != null ? (
            <div>
              <span style={{ fontSize: 10, color: colors.textDim }}>Low</span>
              <div style={{ fontSize: 20, fontWeight: 700, color: colors.text, fontVariantNumeric: "tabular-nums" }}>
                {embed.low}
                {unit}
              </div>
            </div>
          ) : null}
          {embed.current != null ? (
            <div>
              <span style={{ fontSize: 10, color: colors.textDim }}>Now</span>
              <div style={{ fontSize: 20, fontWeight: 700, color: colors.text, fontVariantNumeric: "tabular-nums" }}>
                {embed.current}
                {unit}
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
};

const ScheduleList: React.FC<{
  embed: Extract<ChatEmbed, { kind: "schedule_list" }>;
  colors: Palette;
}> = ({ embed, colors }) => (
  <div style={{ padding: "6px 0 2px" }}>
    <div
      style={{
        fontSize: 11,
        color: colors.textDim,
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        marginBottom: 4,
      }}
    >
      {embed.title || "Schedule"}
    </div>
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {embed.items.map((item, i) => (
        <div
          key={`${item.matchup}-${i}`}
          style={{ display: "flex", gap: 10, alignItems: "baseline", padding: "3px 0" }}
        >
          {item.when ? (
            <span
              style={{
                fontSize: 11,
                color: colors.textDim,
                minWidth: 88,
                fontFamily: "'JetBrains Mono', ui-monospace, monospace",
              }}
            >
              {item.when}
            </span>
          ) : null}
          <span style={{ fontSize: 13, color: colors.text, fontWeight: 550 }}>{item.matchup}</span>
        </div>
      ))}
    </div>
  </div>
);

/** Body embeds under the answer (weather / schedule / stats) — not sources. */
export const ChatEmbeds: React.FC<{ embeds?: ChatEmbed[]; colors: Palette }> = ({ embeds, colors }) => {
  if (!embeds || !embeds.length) return null;
  const body = embeds.filter((e) => BODY_KINDS.has(e.kind));
  if (!body.length) return null;

  const order = ["weather_stat", "stat_row", "schedule_list"];
  const sorted = [...body].sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));

  return (
    <div
      className="chat-embeds"
      style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 2, maxWidth: 520 }}
    >
      {sorted.map((embed) => {
        if (embed.kind === "weather_stat") {
          return <WeatherStat key={embed.id} embed={embed} colors={colors} />;
        }
        if (embed.kind === "schedule_list") {
          return <ScheduleList key={embed.id} embed={embed} colors={colors} />;
        }
        if (embed.kind === "stat_row") {
          return (
            <div
              key={embed.id}
              style={{
                padding: "6px 0",
                display: "flex",
                justifyContent: "space-between",
                gap: 12,
                ...metaFont,
                color: "rgba(255,255,255,0.38)",
              }}
            >
              <span>{embed.label}</span>
              <span style={{ color: "rgba(255,255,255,0.5)" }}>{embed.value}</span>
            </div>
          );
        }
        return null;
      })}
    </div>
  );
};

/**
 * Footer meta row BELOW time/tokens: "sources · N  ·  searched · M" on one line.
 * Expand panels open under the row.
 */
export const ChatEmbedFooter: React.FC<{ embeds?: ChatEmbed[]; colors: Palette }> = ({ embeds }) => {
  const [openSources, setOpenSources] = useState(false);
  const [openSearched, setOpenSearched] = useState(false);

  if (!embeds || !embeds.length) return null;

  const hasSources = embeds.some((e) => e.kind === "sources");
  const footer = embeds.filter((e) => {
    if (!FOOTER_KINDS.has(e.kind)) return false;
    if (e.kind === "link_card" && hasSources) return false;
    return true;
  });
  if (!footer.length) return null;

  const sources = footer.find((e): e is Extract<ChatEmbed, { kind: "sources" }> => e.kind === "sources");
  const searched = footer.find(
    (e): e is Extract<ChatEmbed, { kind: "query_chip" }> => e.kind === "query_chip"
  );
  const linkOnly = !sources
    ? footer.find((e): e is Extract<ChatEmbed, { kind: "link_card" }> => e.kind === "link_card")
    : undefined;

  if (!sources && !searched && !linkOnly) return null;

  return (
    <div className="chat-embed-footer" style={{ marginTop: 4, maxWidth: 520 }}>
      {/* Single horizontal meta row — sources and searched side by side */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: 6,
          ...metaFont,
        }}
      >
        {sources ? (
          <SourcesToggle
            embed={sources}
            open={openSources}
            onToggle={() => {
              setOpenSources((v) => !v);
              if (openSources) {
                /* closing */
              }
            }}
          />
        ) : null}
        {sources && searched ? <span style={{ opacity: 0.45 }}>·</span> : null}
        {searched ? (
          <SearchedToggle
            embed={searched}
            open={openSearched}
            onToggle={() => setOpenSearched((v) => !v)}
          />
        ) : null}
        {linkOnly ? (
          <>
            {(sources || searched) && <span style={{ opacity: 0.45 }}>·</span>}
            <a
              href={linkOnly.url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ ...metaFont, color: "rgba(255,255,255,0.38)", textDecoration: "none" }}
              className="chat-embed-source-link"
              title={linkOnly.title}
            >
              {(linkOnly.domain || "link").replace(/^www\./, "")} ↗
            </a>
          </>
        ) : null}
      </div>

      {openSources && sources ? <SourcesExpanded embed={sources} /> : null}
      {openSearched && searched ? <SearchedExpanded embed={searched} /> : null}
    </div>
  );
};
