import React, { useEffect, useMemo, useState } from "react";
import "./settingsCatalog.css";

export type SettingsCatalogCategory =
  | "models"
  | "search_research"
  | "voice_speech"
  | "connections"
  | "local_tools"
  | "skills"
  | "mcp";

export type SettingsCatalogAction = {
  kind: "connect" | "configure" | "manage" | "advanced" | "inspect" | "none";
  label: string;
  enabled: boolean;
};

export type SettingsCatalogCapability = {
  id: string;
  label: string;
  access: "read" | "write" | "destructive" | "runtime";
  available: boolean;
  enabled: boolean;
  approval_required: boolean;
};

export type SettingsCatalogCard = {
  id: string;
  category: SettingsCatalogCategory;
  name: string;
  description: string;
  status: string;
  status_label: string;
  locality: "local" | "cloud" | "hybrid" | "varies";
  cost_class: "local" | "free" | "usage_based" | "varies" | "included";
  data_path: string;
  connected: boolean;
  ready: boolean;
  enabled: boolean;
  selected: boolean;
  capabilities: SettingsCatalogCapability[];
  last_checked_at?: number | null;
  scope_label: string;
  detail: string;
  issue: string;
  primary_action: SettingsCatalogAction;
  secondary_action?: SettingsCatalogAction | null;
};

type SettingsCatalogProps = {
  category: SettingsCatalogCategory;
  cards: SettingsCatalogCard[];
  loading?: boolean;
  error?: string;
  onRefresh?: () => void;
  onAction?: (card: SettingsCatalogCard, action: SettingsCatalogAction) => void;
  onCapabilityAction?: (card: SettingsCatalogCard, capability: SettingsCatalogCapability) => void;
  capabilityActionDisabled?: (card: SettingsCatalogCard, capability: SettingsCatalogCapability) => boolean;
  detailAddon?: (card: SettingsCatalogCard) => React.ReactNode;
};

const categoryCopy: Record<SettingsCatalogCategory, { title: string; description: string }> = {
  models: {
    title: "Models",
    description: "Choose the exact model used by this Session. Echo never substitutes another provider silently.",
  },
  search_research: {
    title: "Search & Research",
    description: "See which acquisition paths are configured, where requests go, and which provider is selected.",
  },
  voice_speech: {
    title: "Voice & Speech",
    description: "Speech providers are descriptive until their governed runtime reports that they can execute.",
  },
  connections: {
    title: "Connections",
    description: "External accounts and local applications stay scoped through the canonical Connection registry.",
  },
  local_tools: {
    title: "Local Tools",
    description: "The current runtime inventory, including approval requirements and real availability.",
  },
  skills: {
    title: "Skills",
    description: "Reviewed workflow packages and whether their required tools are actually reachable.",
  },
  mcp: {
    title: "MCP",
    description: "Configured MCP servers and tools discovered into Echo's governed inventory.",
  },
};

const formatCost = (value: string) => {
  if (value === "usage_based") return "Usage based";
  if (value === "included") return "Included";
  return value.replace(/_/g, " ").replace(/^./, (letter) => letter.toUpperCase());
};

export const SettingsCatalog: React.FC<SettingsCatalogProps> = ({
  category,
  cards,
  loading = false,
  error = "",
  onRefresh,
  onAction,
  onCapabilityAction,
  capabilityActionDisabled,
  detailAddon,
}) => {
  const visibleCards = useMemo(() => cards.filter((card) => card.category === category), [cards, category]);
  const [selectedId, setSelectedId] = useState("");

  useEffect(() => {
    if (!visibleCards.length) {
      setSelectedId("");
      return;
    }
    if (!visibleCards.some((card) => card.id === selectedId)) {
      setSelectedId(visibleCards.find((card) => card.selected)?.id || visibleCards[0].id);
    }
  }, [selectedId, visibleCards]);

  const selected = visibleCards.find((card) => card.id === selectedId) || null;
  const copy = categoryCopy[category];

  return (
    <div className="settings-catalog">
      <header className="settings-catalog-intro">
        <div>
          <h3>{copy.title}</h3>
          <p>{copy.description}</p>
        </div>
        {onRefresh ? (
          <button type="button" className="settings-catalog-button is-quiet" onClick={onRefresh} disabled={loading}>
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        ) : null}
      </header>

      {error ? <div className="settings-catalog-error">{error}</div> : null}

      <div className="settings-catalog-layout">
        <div className="settings-catalog-grid" role="list" aria-label={`${copy.title} catalog`}>
          {visibleCards.map((card) => (
            <button
              type="button"
              role="listitem"
              key={card.id}
              className={`settings-capability-card${card.id === selectedId ? " is-selected" : ""}`}
              onClick={() => setSelectedId(card.id)}
            >
              <span className="settings-card-heading">
                <span>
                  <strong>{card.name}</strong>
                  {card.selected ? (
                    <small>{card.category === "models" ? "Selected for this Session" : "Selected"}</small>
                  ) : null}
                </span>
                <em data-status={card.status}>{card.status_label}</em>
              </span>
              <span className="settings-card-description">{card.description}</span>
              <span className="settings-card-meta">
                <span>{card.locality}</span>
                <span>{formatCost(card.cost_class)}</span>
                <span>{card.capabilities.length} capabilities</span>
              </span>
            </button>
          ))}
          {!loading && !visibleCards.length ? (
            <div className="settings-catalog-empty">No entries are registered in this category.</div>
          ) : null}
        </div>

        <aside className="settings-catalog-detail" aria-live="polite">
          {selected ? (
            <>
              <div className="settings-detail-heading">
                <div>
                  <span>Capability details</span>
                  <h4>{selected.name}</h4>
                </div>
                <em data-status={selected.status}>{selected.status_label}</em>
              </div>

              <p className="settings-detail-description">{selected.description}</p>
              {selected.issue ? <div className="settings-detail-issue">{selected.issue}</div> : null}

              <dl className="settings-detail-facts">
                <div><dt>Location</dt><dd>{selected.locality}</dd></div>
                <div><dt>Cost</dt><dd>{formatCost(selected.cost_class)}</dd></div>
                <div><dt>Scope</dt><dd>{selected.scope_label}</dd></div>
                <div><dt>Data path</dt><dd>{selected.data_path}</dd></div>
                <div><dt>Connected</dt><dd>{selected.connected ? "Yes" : "No"}</dd></div>
                <div><dt>Ready</dt><dd>{selected.ready ? "Yes" : "No"}</dd></div>
                <div>
                  <dt>Last check</dt>
                  <dd>{selected.last_checked_at ? new Date(selected.last_checked_at * 1000).toLocaleString() : "Not checked"}</dd>
                </div>
              </dl>

              {selected.detail ? <p className="settings-detail-note">{selected.detail}</p> : null}

              <div className="settings-detail-capabilities">
                <span>Capabilities</span>
                {selected.capabilities.length ? selected.capabilities.map((capability) => {
                  const content = (
                    <>
                      <span>{capability.label}</span>
                      <small>
                        {!capability.enabled ? "disabled · " : ""}
                        {capability.access.replace(/_/g, " ")}
                        {capability.approval_required ? " · approval" : ""}
                      </small>
                    </>
                  );
                  const shared = {
                    "data-available": capability.available ? "true" : "false",
                    "data-enabled": capability.enabled ? "true" : "false",
                  };
                  return onCapabilityAction ? (
                    <button
                      key={capability.id}
                      {...shared}
                      type="button"
                      aria-pressed={capability.enabled}
                      disabled={capabilityActionDisabled?.(selected, capability) ?? false}
                      onClick={() => onCapabilityAction?.(selected, capability)}
                    >
                      {content}
                    </button>
                  ) : <div key={capability.id} {...shared}>{content}</div>;
                }) : <small>No capabilities are currently advertised.</small>}
              </div>

              {detailAddon ? detailAddon(selected) : null}

              <div className="settings-detail-actions">
                {selected.primary_action.kind !== "none" ? (
                  <button
                    type="button"
                    className="settings-catalog-button"
                    disabled={!selected.primary_action.enabled}
                    onClick={() => onAction?.(selected, selected.primary_action)}
                  >
                    {selected.primary_action.label}
                  </button>
                ) : null}
                {selected.secondary_action && selected.secondary_action.kind !== "none" ? (
                  <button
                    type="button"
                    className="settings-catalog-button is-quiet"
                    disabled={!selected.secondary_action.enabled}
                    onClick={() => onAction?.(selected, selected.secondary_action!)}
                  >
                    {selected.secondary_action.label}
                  </button>
                ) : null}
              </div>
            </>
          ) : (
            <div className="settings-catalog-empty">Select an entry to inspect its current state.</div>
          )}
        </aside>
      </div>

      <footer className="settings-catalog-legend">
        <span><b>Connected</b> means configuration or authorization exists.</span>
        <span><b>Ready</b> means the runtime has verified that the capability can execute.</span>
      </footer>
    </div>
  );
};
