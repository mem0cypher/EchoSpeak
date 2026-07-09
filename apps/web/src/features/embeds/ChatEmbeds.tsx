import React, { useState } from "react";
import type { ChatEmbed, ChatEmbedSourceItem } from "./types";

type Palette = {
  panel2: string;
  line: string;
  text: string;
  textDim: string;
};

/** Sleek source row — no pill/bubble chrome; title is the primary click target. */
const SourceRow: React.FC<{
  item: ChatEmbedSourceItem;
  index: number;
  colors: Palette;
  expanded: boolean;
  onToggle: () => void;
}> = ({ item, index, colors, expanded, onToggle }) => {
  const hasUrl = Boolean(item.url);
  const hasSnippet = Boolean(item.snippet && item.snippet.trim());

  const titleEl = (
    <span
      style={{
        fontSize: 13,
        fontWeight: 550,
        color: hasUrl ? colors.text : colors.textDim,
        lineHeight: 1.35,
        textDecoration: hasUrl ? "none" : undefined,
        borderBottom: hasUrl ? "1px solid transparent" : undefined,
        transition: "border-color 0.12s ease, color 0.12s ease",
      }}
      className={hasUrl ? "chat-embed-source-title" : undefined}
    >
      {item.title}
    </span>
  );

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "22px 1fr",
        gap: "2px 10px",
        padding: "10px 0",
        borderTop: index === 0 ? "none" : `1px solid ${colors.line}`,
        alignItems: "start",
      }}
    >
      <span
        style={{
          fontSize: 11,
          color: colors.textDim,
          fontFamily: "'JetBrains Mono', ui-monospace, monospace",
          lineHeight: "18px",
          paddingTop: 1,
          opacity: 0.75,
        }}
      >
        {index + 1}
      </span>

      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
          {hasUrl ? (
            <a
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              title={item.url}
              style={{ textDecoration: "none", color: "inherit", minWidth: 0 }}
            >
              {titleEl}
            </a>
          ) : (
            titleEl
          )}
          {(item.domain || item.recency) && (
            <span
              style={{
                fontSize: 11,
                color: colors.textDim,
                opacity: 0.85,
                whiteSpace: "nowrap",
              }}
            >
              {item.domain || "web"}
              {item.recency ? ` · ${item.recency}` : ""}
            </span>
          )}
          {hasUrl ? (
            <a
              href={item.url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`Open ${item.title}`}
              style={{
                fontSize: 11,
                color: colors.textDim,
                textDecoration: "none",
                opacity: 0.7,
                marginLeft: "auto",
                flexShrink: 0,
              }}
              className="chat-embed-source-open"
            >
              Open ↗
            </a>
          ) : null}
        </div>

        {hasSnippet ? (
          <>
            <button
              type="button"
              onClick={onToggle}
              aria-expanded={expanded}
              style={{
                display: "block",
                marginTop: 4,
                padding: 0,
                border: "none",
                background: "none",
                cursor: "pointer",
                textAlign: "left",
                width: "100%",
                font: "inherit",
              }}
            >
              <div
                style={{
                  fontSize: 12,
                  color: colors.textDim,
                  lineHeight: 1.45,
                  ...(expanded
                    ? {}
                    : {
                        display: "-webkit-box",
                        WebkitLineClamp: 1,
                        WebkitBoxOrient: "vertical" as const,
                        overflow: "hidden",
                      }),
                }}
              >
                {item.snippet}
              </div>
              <span
                style={{
                  fontSize: 10.5,
                  color: colors.textDim,
                  opacity: 0.65,
                  marginTop: 2,
                  display: "inline-block",
                }}
              >
                {expanded ? "Hide snippet" : "What it found · click to expand"}
              </span>
            </button>
            {expanded && hasUrl ? (
              <a
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  display: "inline-block",
                  marginTop: 6,
                  fontSize: 11.5,
                  color: colors.text,
                  opacity: 0.8,
                  textDecoration: "underline",
                  textUnderlineOffset: 3,
                  wordBreak: "break-all",
                }}
              >
                {item.url}
              </a>
            ) : null}
          </>
        ) : hasUrl ? (
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: "block",
              marginTop: 3,
              fontSize: 11,
              color: colors.textDim,
              opacity: 0.7,
              textDecoration: "none",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
            className="chat-embed-source-url"
          >
            {item.url}
          </a>
        ) : null}
      </div>
    </div>
  );
};

const SourcesList: React.FC<{
  embed: Extract<ChatEmbed, { kind: "sources" }>;
  colors: Palette;
}> = ({ embed, colors }) => {
  const [openId, setOpenId] = useState<string | null>(null);

  return (
    <div style={{ maxWidth: 520 }}>
      <div
        style={{
          fontSize: 11,
          color: colors.textDim,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          fontWeight: 550,
          marginBottom: 2,
          opacity: 0.8,
        }}
      >
        {embed.title || "Sources"}
      </div>
      <div>
        {embed.items.map((item, i) => (
          <SourceRow
            key={item.id}
            item={item}
            index={i}
            colors={colors}
            expanded={openId === item.id}
            onToggle={() => setOpenId((cur) => (cur === item.id ? null : item.id))}
          />
        ))}
      </div>
    </div>
  );
};

const LinkCard: React.FC<{ embed: Extract<ChatEmbed, { kind: "link_card" }>; colors: Palette }> = ({
  embed,
  colors,
}) => (
  <a
    href={embed.url}
    target="_blank"
    rel="noopener noreferrer"
    style={{ textDecoration: "none", color: "inherit", display: "block" }}
  >
    <div
      style={{
        borderTop: `1px solid ${colors.line}`,
        padding: "10px 0",
        transition: "opacity 0.15s ease",
      }}
      className="chat-embed-link"
    >
      <div style={{ fontSize: 11, color: colors.textDim, marginBottom: 3, letterSpacing: "0.04em" }}>
        {embed.domain || "Link"} · featured
      </div>
      <div
        style={{
          fontSize: 13.5,
          fontWeight: 650,
          color: colors.text,
          lineHeight: 1.35,
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
        className="chat-embed-source-title"
      >
        {embed.title}
      </div>
      {embed.snippet ? (
        <div
          style={{
            fontSize: 12,
            color: colors.textDim,
            marginTop: 4,
            lineHeight: 1.4,
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {embed.snippet}
        </div>
      ) : null}
      <div style={{ fontSize: 11, color: colors.textDim, marginTop: 6, opacity: 0.75 }}>Open ↗</div>
    </div>
  </a>
);

const WeatherStat: React.FC<{
  embed: Extract<ChatEmbed, { kind: "weather_stat" }>;
  colors: Palette;
}> = ({ embed, colors }) => {
  const unit = embed.unit ? `°${embed.unit}` : "°";
  return (
    <div
      style={{
        padding: "10px 0",
        borderTop: `1px solid ${colors.line}`,
        display: "flex",
        alignItems: "stretch",
        gap: 14,
      }}
    >
      <div
        style={{
          width: 40,
          height: 40,
          borderRadius: 10,
          background: "rgba(120,180,255,0.1)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 20,
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
        <div style={{ display: "flex", gap: 16, marginTop: 6, flexWrap: "wrap", alignItems: "baseline" }}>
          {embed.high != null ? (
            <div>
              <span style={{ fontSize: 10, color: colors.textDim }}>High</span>
              <div style={{ fontSize: 22, fontWeight: 700, color: colors.text, fontVariantNumeric: "tabular-nums" }}>
                {embed.high}
                {unit}
              </div>
            </div>
          ) : null}
          {embed.low != null ? (
            <div>
              <span style={{ fontSize: 10, color: colors.textDim }}>Low</span>
              <div style={{ fontSize: 22, fontWeight: 700, color: colors.text, fontVariantNumeric: "tabular-nums" }}>
                {embed.low}
                {unit}
              </div>
            </div>
          ) : null}
          {embed.current != null ? (
            <div>
              <span style={{ fontSize: 10, color: colors.textDim }}>Now</span>
              <div style={{ fontSize: 22, fontWeight: 700, color: colors.text, fontVariantNumeric: "tabular-nums" }}>
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
  <div style={{ padding: "8px 0", borderTop: `1px solid ${colors.line}` }}>
    <div
      style={{
        fontSize: 11,
        color: colors.textDim,
        letterSpacing: "0.06em",
        textTransform: "uppercase",
        marginBottom: 6,
      }}
    >
      {embed.title || "Schedule"}
    </div>
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {embed.items.map((item, i) => (
        <div
          key={`${item.matchup}-${i}`}
          style={{
            display: "flex",
            gap: 10,
            alignItems: "baseline",
            padding: "5px 0",
            borderTop: i === 0 ? "none" : `1px solid ${colors.line}`,
          }}
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

export const ChatEmbeds: React.FC<{ embeds?: ChatEmbed[]; colors: Palette }> = ({ embeds, colors }) => {
  if (!embeds || !embeds.length) return null;

  // Order: weather/stats → schedule → query chips → link card → source strip
  const order = ["weather_stat", "stat_row", "schedule_list", "query_chip", "link_card", "sources"];
  const sorted = [...embeds].sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));

  return (
    <div
      className="chat-embeds"
      style={{
        marginTop: 14,
        display: "flex",
        flexDirection: "column",
        gap: 4,
        maxWidth: 520,
      }}
    >
      {sorted.map((embed) => {
        if (embed.kind === "weather_stat") {
          return <WeatherStat key={embed.id} embed={embed} colors={colors} />;
        }
        if (embed.kind === "schedule_list") {
          return <ScheduleList key={embed.id} embed={embed} colors={colors} />;
        }
        if (embed.kind === "query_chip") {
          // Text-only searched queries — no pill bubbles
          return (
            <div
              key={embed.id}
              style={{
                padding: "8px 0",
                borderTop: `1px solid ${colors.line}`,
                display: "flex",
                flexDirection: "column",
                gap: 4,
              }}
            >
              <div
                style={{
                  fontSize: 11,
                  color: colors.textDim,
                  letterSpacing: "0.06em",
                  textTransform: "uppercase",
                  opacity: 0.75,
                }}
              >
                Searched
              </div>
              {embed.queries.map((q) => (
                <div
                  key={q}
                  title={q}
                  style={{
                    fontSize: 12,
                    color: colors.textDim,
                    lineHeight: 1.4,
                    paddingLeft: 2,
                    borderLeft: `2px solid ${colors.line}`,
                    marginLeft: 1,
                    paddingTop: 1,
                    paddingBottom: 1,
                    paddingRight: 4,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {q}
                </div>
              ))}
            </div>
          );
        }
        if (embed.kind === "link_card") {
          return <LinkCard key={embed.id} embed={embed} colors={colors} />;
        }
        if (embed.kind === "sources") {
          return (
            <div key={embed.id} style={{ borderTop: `1px solid ${colors.line}`, paddingTop: 10 }}>
              <SourcesList embed={embed} colors={colors} />
            </div>
          );
        }
        if (embed.kind === "stat_row") {
          return (
            <div
              key={embed.id}
              style={{
                padding: "10px 0",
                borderTop: `1px solid ${colors.line}`,
                display: "flex",
                justifyContent: "space-between",
                gap: 12,
              }}
            >
              <span style={{ color: colors.textDim, fontSize: 12 }}>{embed.label}</span>
              <span style={{ color: colors.text, fontWeight: 650, fontSize: 13 }}>{embed.value}</span>
            </div>
          );
        }
        return null;
      })}
    </div>
  );
};
