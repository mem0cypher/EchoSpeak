import React, { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";

export type AvatarConfig = {
  body_color: string;
  eye_color: string;
  bg_color: string;
  glow_color: string;
  idle_activity: string;
  breathing_speed: number;
  eye_size: number;
  body_roundness: number;
  enable_particles: boolean;
  enable_glow: boolean;
  enable_idle_activities: boolean;
  custom_status_text: string;
};

export const DEFAULT_AVATAR_CONFIG: AvatarConfig = {
  body_color: "#ffffff",
  eye_color: "#000000",
  bg_color: "#0a0a0a",
  glow_color: "#4f8eff",
  idle_activity: "auto",
  breathing_speed: 1,
  eye_size: 1,
  body_roundness: 14,
  enable_particles: true,
  enable_glow: true,
  enable_idle_activities: true,
  custom_status_text: "",
};

const PRESETS: Array<{ name: string; config: Partial<AvatarConfig> }> = [
  { name: "Default", config: { body_color: "#ffffff", eye_color: "#000000", glow_color: "#4f8eff", bg_color: "#0a0a0a" } },
  { name: "Midnight", config: { body_color: "#8b5cf6", eye_color: "#ddd6fe", glow_color: "#7c3aed", bg_color: "#140b27" } },
  { name: "Ember", config: { body_color: "#fb923c", eye_color: "#fde68a", glow_color: "#ef4444", bg_color: "#190a02" } },
  { name: "Ocean", config: { body_color: "#22d3ee", eye_color: "#cffafe", glow_color: "#0ea5e9", bg_color: "#03151e" } },
  { name: "Forest", config: { body_color: "#4ade80", eye_color: "#dcfce7", glow_color: "#16a34a", bg_color: "#06170d" } },
  { name: "Mono", config: { body_color: "#d4d4d8", eye_color: "#fafafa", glow_color: "#71717a", bg_color: "#0a0a0a" } },
];

type AvatarEditorProps = {
  apiBase: string;
  colors: {
    bg: string;
    panel: string;
    panel2: string;
    accent: string;
    text: string;
    textDim: string;
    line: string;
    danger: string;
  };
  onConfigChange?: (config: AvatarConfig) => void;
};

async function requestJson(url: string, init?: RequestInit) {
  const res = await fetch(url, init);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data?.detail || data?.message || `Request failed (${res.status})`);
  }
  return data;
}

export const AvatarEditor: React.FC<AvatarEditorProps> = ({ apiBase, colors, onConfigChange }) => {
  const [config, setConfig] = useState<AvatarConfig>(DEFAULT_AVATAR_CONFIG);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await requestJson(`${apiBase}/avatar/config`);
      const next = { ...DEFAULT_AVATAR_CONFIG, ...data };
      setConfig(next);
      setDirty(false);
      onConfigChange?.(next);
    } catch (e: any) {
      setError(e.message || "Failed to load avatar config");
    } finally {
      setLoading(false);
    }
  }, [apiBase, onConfigChange]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const updateField = <K extends keyof AvatarConfig>(key: K, value: AvatarConfig[K]) => {
    setConfig((prev) => {
      const next = { ...prev, [key]: value };
      onConfigChange?.(next);
      return next;
    });
    setDirty(true);
    setSaved(false);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const data = await requestJson(`${apiBase}/avatar/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      const next = { ...DEFAULT_AVATAR_CONFIG, ...data };
      setConfig(next);
      setDirty(false);
      setSaved(true);
      onConfigChange?.(next);
      window.setTimeout(() => setSaved(false), 1800);
    } catch (e: any) {
      setError(e.message || "Failed to save avatar config");
    } finally {
      setSaving(false);
    }
  };

  const reset = async () => {
    setError(null);
    try {
      const data = await requestJson(`${apiBase}/avatar/config/reset`, { method: "POST" });
      const next = { ...DEFAULT_AVATAR_CONFIG, ...data };
      setConfig(next);
      setDirty(false);
      setSaved(false);
      onConfigChange?.(next);
    } catch (e: any) {
      setError(e.message || "Failed to reset avatar config");
    }
  };

  const applyPreset = (preset: Partial<AvatarConfig>) => {
    setConfig((prev) => {
      const next = { ...prev, ...preset };
      onConfigChange?.(next);
      return next;
    });
    setDirty(true);
    setSaved(false);
  };

  const cardStyle: React.CSSProperties = {
    background: "linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.02))",
    border: `1px solid ${colors.line}`,
    borderRadius: 16,
    padding: 16,
    boxShadow: "0 18px 40px rgba(0,0,0,0.18)",
  };

  const labelStyle: React.CSSProperties = {
    fontSize: 11,
    fontWeight: 700,
    color: colors.textDim,
    textTransform: "uppercase",
    letterSpacing: 0.8,
    marginBottom: 10,
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    background: "rgba(255,255,255,0.05)",
    border: `1px solid ${colors.line}`,
    borderRadius: 10,
    padding: "10px 12px",
    color: colors.text,
    fontSize: 12,
    outline: "none",
  };

  if (loading) {
    return (
      <div className="research-scroll">
        <div className="research-card" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: 240, color: colors.textDim }}>
          Loading avatar settings...
        </div>
      </div>
    );
  }

  return (
    <div className="research-scroll">
      <div className="research-card" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ ...cardStyle, padding: 20, background: "linear-gradient(135deg, rgba(79,142,255,0.16), rgba(255,255,255,0.04))" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: colors.text }}>Avatar Editor</div>
              <div style={{ fontSize: 12, color: colors.textDim, maxWidth: 560, lineHeight: 1.55 }}>
                Style Echo&apos;s visualizer avatar and preview changes live. Save persists to the backend, reset restores the default profile.
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              {dirty ? <span style={{ fontSize: 11, color: "#f59e0b" }}>Unsaved</span> : null}
              {saved ? <span style={{ fontSize: 11, color: "#22c55e" }}>Saved</span> : null}
              <button className="icon-button" type="button" onClick={save} disabled={saving || !dirty} style={{ height: 34, padding: "0 14px", fontSize: 12, opacity: dirty ? 1 : 0.55 }}>
                {saving ? "Saving..." : "Save"}
              </button>
              <button className="icon-button" type="button" onClick={reset} style={{ height: 34, padding: "0 14px", fontSize: 12 }}>
                Reset
              </button>
            </div>
          </div>
        </div>

        {error ? (
          <div style={{ color: colors.danger, padding: 12, borderRadius: 12, border: `1px solid ${colors.danger}33`, background: "rgba(239,68,68,0.08)", fontSize: 12 }}>
            {error}
          </div>
        ) : null}

        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.15fr) minmax(280px, 0.85fr)", gap: 16 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={cardStyle}>
              <div style={labelStyle}>Presets</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {PRESETS.map((preset) => (
                  <button key={preset.name} type="button" onClick={() => applyPreset(preset.config)} style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderRadius: 999, border: `1px solid ${colors.line}`, background: "rgba(255,255,255,0.04)", color: colors.text, cursor: "pointer", fontSize: 12 }}>
                    <span style={{ width: 10, height: 10, borderRadius: "50%", background: preset.config.body_color || "#fff", boxShadow: `0 0 0 2px ${(preset.config.glow_color || "#fff")}33` }} />
                    {preset.name}
                  </button>
                ))}
              </div>
            </div>

            <div style={cardStyle}>
              <div style={labelStyle}>Appearance</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 12 }}>
                {([
                  ["Body", "body_color"],
                  ["Eyes", "eye_color"],
                  ["Glow", "glow_color"],
                  ["Backdrop", "bg_color"],
                ] as const).map(([label, field]) => (
                  <div key={field} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    <span style={{ fontSize: 12, color: colors.textDim }}>{label}</span>
                    <div style={{ display: "flex", gap: 8 }}>
                      <input type="color" value={config[field]} onChange={(e) => updateField(field, e.target.value)} style={{ width: 44, height: 36, borderRadius: 10, border: `1px solid ${colors.line}`, background: "transparent", padding: 0 }} />
                      <input value={config[field]} onChange={(e) => updateField(field, e.target.value)} style={{ ...inputStyle, fontFamily: "monospace" }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div style={cardStyle}>
              <div style={labelStyle}>Motion</div>
              <div style={{ display: "grid", gap: 14 }}>
                {([
                  ["Eye Size", "eye_size", 0.5, 2, 0.1, "x"],
                  ["Roundness", "body_roundness", 4, 40, 1, "px"],
                  ["Breathing", "breathing_speed", 0.4, 2.6, 0.1, "x"],
                ] as const).map(([label, field, min, max, step, unit]) => (
                  <div key={field} style={{ display: "grid", gridTemplateColumns: "110px 1fr 52px", alignItems: "center", gap: 12 }}>
                    <span style={{ fontSize: 12, color: colors.textDim }}>{label}</span>
                    <input type="range" min={min} max={max} step={step} value={config[field]} onChange={(e) => updateField(field, parseFloat(e.target.value) as never)} style={{ width: "100%", accentColor: config.glow_color }} />
                    <span style={{ fontSize: 11, color: colors.text, fontFamily: "monospace", textAlign: "right" }}>{config[field].toFixed(step < 1 ? 1 : 0)}{unit}</span>
                  </div>
                ))}
              </div>
            </div>

            <div style={cardStyle}>
              <div style={labelStyle}>Behavior</div>
              <div style={{ display: "grid", gap: 14 }}>
                <div style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: 12, alignItems: "center" }}>
                  <span style={{ fontSize: 12, color: colors.textDim }}>Idle Mode</span>
                  <select value={config.idle_activity} onChange={(e) => updateField("idle_activity", e.target.value)} style={{ ...inputStyle, paddingRight: 32 }}>
                    <option value="auto">Auto</option>
                    <option value="gaming">Gaming</option>
                    <option value="floating">Floating</option>
                    <option value="napping">Napping</option>
                    <option value="vibing">Vibing</option>
                    <option value="stretching">Stretching</option>
                    <option value="none">Static</option>
                  </select>
                </div>
                {([
                  ["Glow", "enable_glow"],
                  ["Particles", "enable_particles"],
                  ["Idle Activities", "enable_idle_activities"],
                ] as const).map(([label, field]) => (
                  <div key={field} style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <span style={{ fontSize: 12, color: colors.textDim }}>{label}</span>
                    <button type="button" onClick={() => updateField(field, (!config[field]) as never)} style={{ width: 42, height: 24, borderRadius: 999, border: "none", background: config[field] ? `${config.glow_color}66` : "rgba(255,255,255,0.1)", cursor: "pointer", position: "relative" }}>
                      <motion.div animate={{ x: config[field] ? 20 : 2 }} transition={{ duration: 0.16 }} style={{ position: "absolute", top: 2, width: 20, height: 20, borderRadius: "50%", background: config[field] ? "#ffffff" : colors.textDim }} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ ...cardStyle, padding: 18, background: `linear-gradient(180deg, ${config.bg_color}, rgba(255,255,255,0.02))` }}>
              <div style={labelStyle}>Live Preview</div>
              <div style={{ position: "relative", height: 260, borderRadius: 18, border: `1px solid ${colors.line}`, overflow: "hidden", background: `radial-gradient(circle at center, ${config.bg_color} 0%, rgba(0,0,0,0) 75%)` }}>
                <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  {config.enable_particles ? (
                    <>
                      {[0, 1, 2].map((i) => (
                        <motion.div key={i} animate={{ y: [0, -16, 0], opacity: [0.2, 0.55, 0.2] }} transition={{ duration: 3 + i, repeat: Infinity, ease: "easeInOut", delay: i * 0.3 }} style={{ position: "absolute", top: `${28 + i * 16}%`, left: `${30 + i * 14}%`, width: 8, height: 8, borderRadius: 999, background: config.glow_color, filter: "blur(1px)" }} />
                      ))}
                    </>
                  ) : null}
                  <motion.div animate={{ y: [0, -4 * config.breathing_speed, 0] }} transition={{ duration: Math.max(0.9, 3 / Math.max(0.25, config.breathing_speed)), repeat: Infinity, repeatType: "reverse", ease: "easeInOut" }} style={{ width: 136, height: 136, borderRadius: config.body_roundness, background: `linear-gradient(135deg, ${config.body_color}, ${config.body_color}dd)`, boxShadow: config.enable_glow ? `0 0 36px ${config.glow_color}55` : "none", border: config.enable_glow ? `4px solid ${config.glow_color}33` : "4px solid rgba(255,255,255,0.08)", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", position: "relative" }}>
                    <div style={{ display: "flex", gap: Math.max(22, 28 + config.eye_size * 6), marginTop: -18 }}>
                      <div style={{ width: 18 * config.eye_size, height: 22 * config.eye_size, borderRadius: 999, background: config.eye_color }} />
                      <div style={{ width: 18 * config.eye_size, height: 22 * config.eye_size, borderRadius: 999, background: config.eye_color }} />
                    </div>
                    <div style={{ width: 20, height: 6, borderRadius: 999, background: config.eye_color, marginTop: 24, opacity: 0.9 }} />
                  </motion.div>
                </div>
                {config.custom_status_text ? (
                  <div style={{ position: "absolute", bottom: 14, left: "50%", transform: "translateX(-50%)", padding: "6px 12px", borderRadius: 999, background: "rgba(10,10,10,0.65)", border: `1px solid ${config.glow_color}33`, color: config.body_color, fontSize: 11, fontWeight: 600, whiteSpace: "nowrap" }}>
                    {config.custom_status_text}
                  </div>
                ) : null}
              </div>
            </div>

            <div style={cardStyle}>
              <div style={labelStyle}>Status Label</div>
              <input value={config.custom_status_text} onChange={(e) => updateField("custom_status_text", e.target.value)} placeholder="Optional status line under the avatar" style={inputStyle} />
            </div>

            <div style={cardStyle}>
              <div style={labelStyle}>What is wired</div>
              <div style={{ display: "grid", gap: 8, fontSize: 12, color: colors.textDim, lineHeight: 1.55 }}>
                <div>Colors, roundness, eye size, breathing, glow, particles, idle mode, and status text all feed the visualizer live.</div>
                <div>Save persists the profile to the backend API. Reset restores the default server-side profile.</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
