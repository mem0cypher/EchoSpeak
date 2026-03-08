import React from "react";
import type { ResearchRun } from "./types";

type Palette = {
  panel2: string;
  line: string;
  text: string;
  textDim: string;
};

type ResearchPanelProps = {
  colors: Palette;
  runs: ResearchRun[];
  selectedVoice: string | null;
  voices: SpeechSynthesisVoice[];
  onSelectedVoiceChange: (value: string | null) => void;
  onClear: () => void;
};

const recencyLabel = (value: string): string => {
  if (value === "breaking") return "Breaking";
  if (value === "recent") return "Recent";
  if (value === "archive") return "Archive";
  return "Undated";
};

export function ResearchPanel({
  colors,
  runs,
  selectedVoice,
  voices,
  onSelectedVoiceChange,
  onClear,
}: ResearchPanelProps) {
  return (
    <>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: -12 }}>
        <select
          className="icon-button"
          style={{ height: 32, padding: "0 10px", fontSize: 13, flex: 2, background: colors.panel2, border: `1px solid ${colors.line}`, color: colors.text }}
          value={selectedVoice || ""}
          onChange={(e) => onSelectedVoiceChange(e.target.value || null)}
        >
          <option value="">Default Voice</option>
          {voices.map((voice) => (
            <option key={voice.name} value={voice.name}>{voice.name} ({voice.lang})</option>
          ))}
        </select>
        <button
          className="icon-button"
          style={{ height: 32, padding: "0 12px", fontSize: 14, flex: 1 }}
          onClick={onClear}
          disabled={!runs.length}
          type="button"
        >
          Clear ({runs.length})
        </button>
      </div>
      <div className="research-scroll">
        {runs.length ? (
          runs.map((run) => (
            <div key={run.id} className="research-card">
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 8, flexWrap: "wrap" }}>
                <div style={{ fontSize: 14, color: colors.textDim, fontWeight: 500 }}>{run.query || "Web search"}</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 10.5, padding: "3px 8px", borderRadius: 999, background: "rgba(255,255,255,0.08)", color: colors.textDim, border: `1px solid ${colors.line}` }}>
                    {run.mode === "recent" ? "Recent-aware" : "General"}
                  </span>
                  <span style={{ fontSize: 10.5, padding: "3px 8px", borderRadius: 999, background: "rgba(255,255,255,0.08)", color: colors.textDim, border: `1px solid ${colors.line}` }}>
                    {run.evidence_count} source{run.evidence_count === 1 ? "" : "s"}
                  </span>
                </div>
              </div>
              {run.evidence.length ? (
                run.evidence.map((item) => (
                  <div key={item.id} style={{ marginTop: item.position > 1 ? 14 : 0 }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                      <div className="research-title">{item.title || "(untitled)"}</div>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        {item.recency_bucket && item.recency_bucket !== "unknown" ? (
                          <span style={{ fontSize: 10.5, padding: "2px 7px", borderRadius: 999, background: "rgba(255,255,255,0.06)", color: colors.textDim, border: `1px solid ${colors.line}` }}>
                            {recencyLabel(item.recency_bucket)}
                          </span>
                        ) : null}
                        {item.published_raw ? (
                          <span style={{ fontSize: 10.5, padding: "2px 7px", borderRadius: 999, background: "rgba(255,255,255,0.06)", color: colors.textDim, border: `1px solid ${colors.line}` }}>
                            {item.published_raw}
                          </span>
                        ) : null}
                      </div>
                    </div>
                    {item.summary ? <div className="research-snippet">{item.summary}</div> : null}
                    {item.url ? (
                      <a className="research-source" href={item.url} target="_blank" rel="noreferrer">
                        {item.domain || item.url}
                      </a>
                    ) : null}
                  </div>
                ))
              ) : (
                <div className="research-snippet">No sources captured.</div>
              )}
            </div>
          ))
        ) : (
          <div className="research-card">
            <div className="research-snippet">Structured research evidence will appear here automatically.</div>
          </div>
        )}
      </div>
    </>
  );
}
