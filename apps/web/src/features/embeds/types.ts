/**
 * Structured rich content attached to an assistant message.
 * Inspired by ChatGPT source cards + Claude-style secondary surfaces:
 * chat stays text-first; embeds sit under the answer as professional chrome.
 */

export type ChatEmbedKind =
  | "sources"
  | "link_card"
  | "weather_stat"
  | "schedule_list"
  | "stat_row"
  | "query_chip";

export type ChatEmbedSourceItem = {
  id: string;
  title: string;
  url: string;
  domain: string;
  snippet?: string;
  recency?: string;
};

export type ChatEmbed =
  | {
      id: string;
      kind: "sources";
      title?: string;
      items: ChatEmbedSourceItem[];
    }
  | {
      id: string;
      kind: "link_card";
      title: string;
      url: string;
      domain: string;
      snippet?: string;
      faviconLetter?: string;
    }
  | {
      id: string;
      kind: "weather_stat";
      place?: string;
      high?: string;
      low?: string;
      current?: string;
      unit?: string;
      condition?: string;
      rawHint?: string;
    }
  | {
      id: string;
      kind: "schedule_list";
      title?: string;
      items: { when?: string; matchup: string; note?: string }[];
    }
  | {
      id: string;
      kind: "stat_row";
      label: string;
      value: string;
      hint?: string;
    }
  | {
      id: string;
      kind: "query_chip";
      queries: string[];
    };
