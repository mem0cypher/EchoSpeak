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
                  lineHeight: 1.45,
                  width: "100%",
                  maxWidth: "100%",
                  minWidth: 0,
                  boxSizing: "border-box",
                  overflowWrap: "anywhere",
                  wordBreak: "break-word",
                  whiteSpace: "pre-wrap",
                }}
              >
                {item.title ? (
                  <div
                    style={{
                      color: "rgba(255,255,255,0.45)",
                      marginBottom: 2,
                      overflowWrap: "anywhere",
                      wordBreak: "break-word",
                      whiteSpace: "normal",
                    }}
                  >
                    {item.title}
                  </div>
                ) : null}
                {hasSnippet ? (
                  <div style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", wordBreak: "break-word" }}>
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
                      overflowWrap: "anywhere",
                      fontSize: 9,
                      maxWidth: "100%",
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
  <div style={{ marginTop: 4, display: "flex", flexDirection: "column", gap: 2, width: "100%", minWidth: 0 }}>
    {embed.queries.map((q) => (
      <div
        key={q}
        title={q}
        style={{
          ...metaFont,
          color: "rgba(255,255,255,0.34)",
          whiteSpace: "pre-wrap",
          overflowWrap: "anywhere",
          wordBreak: "break-word",
          maxWidth: "100%",
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
    <div className="chat-embed-weather" style={{ padding: "8px 0 2px", display: "flex", alignItems: "stretch", gap: 12 }}>
      <div
        style={{
          width: 36,
          height: 36,
          borderRadius: 8,
          background: "rgba(255,255,255,0.055)",
          border: "1px solid rgba(255,255,255,0.07)",
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
  <div className="chat-embed-schedule" style={{ padding: "6px 0 2px" }}>
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
          className="chat-embed-schedule-row"
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
      style={{
        marginTop: 4,
        display: "flex",
        flexDirection: "column",
        gap: 2,
        width: "100%",
        maxWidth: "100%",
        minWidth: 0,
        boxSizing: "border-box",
      }}
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
              className="chat-embed-stat"
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
 * Compact meta chips only: "· Sources · N · Search · M".
 * No expandable Evidence/source boxes under the bubble — full evidence lives in Studio/Viewer/Research.
 */
export const ChatEmbedFooter: React.FC<{ embeds?: ChatEmbed[]; colors: Palette; extraSources?: number }> = ({
  embeds,
  extraSources = 0,
}) => {
  if ((!embeds || !embeds.length) && !extraSources) return null;

  const hasSources = embeds?.some((e) => e.kind === "sources");
  const footer = (embeds || []).filter((e) => {
    if (!FOOTER_KINDS.has(e.kind)) return false;
    if (e.kind === "link_card" && hasSources) return false;
    return true;
  });

  const sources = footer.find((e): e is Extract<ChatEmbed, { kind: "sources" }> => e.kind === "sources");
  const searched = footer.find(
    (e): e is Extract<ChatEmbed, { kind: "query_chip" }> => e.kind === "query_chip"
  );
  const sourceCount = Math.max(sources?.items.length || 0, Number(extraSources) || 0);
  const searchCount = searched?.queries?.length || 0;

  if (!sourceCount && !searchCount) return null;

  return (
    <>
      {sourceCount ? (
        <>
          <span style={{ opacity: 0.45 }}>·</span>
          <span
            data-testid="chat-meta-sources"
            title={sources?.items.map((s) => s.title || s.domain || s.url).filter(Boolean).join(" · ") || "Sources"}
            style={{ ...metaFont, color: "rgba(255,255,255,0.34)" }}
          >
            Sources · {sourceCount}
          </span>
        </>
      ) : null}
      {searchCount ? (
        <>
          <span style={{ opacity: 0.45 }}>·</span>
          <span
            data-testid="chat-meta-search"
            title={(searched?.queries || []).join(" · ") || "Search"}
            style={{ ...metaFont, color: "rgba(255,255,255,0.34)" }}
          >
            Search · {searchCount}
          </span>
        </>
      ) : null}
    </>
  );
};
