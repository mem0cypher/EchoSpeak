import React, { useMemo } from "react";

export type CodeSessionStatus = "read" | "draft" | "saved" | "output";

export type CodeDiffSession = {
  filename: string;
  language: string;
  originalContent: string;
  currentContent: string;
  status: CodeSessionStatus;
  summary?: string;
  pendingConfirmation?: boolean;
};

type DiffRow = {
  kind: "context" | "added" | "removed";
  oldNumber: number | null;
  newNumber: number | null;
  text: string;
};

const splitLines = (value: string): string[] => String(value || "").replace(/\r\n/g, "\n").split("\n");

const buildUnifiedRows = (original: string, current: string): DiffRow[] => {
  const a = splitLines(original);
  const b = splitLines(current);

  if (original === current) {
    return b.map((line, index) => ({
      kind: "context",
      oldNumber: index + 1,
      newNumber: index + 1,
      text: line,
    }));
  }

  const dp = Array.from({ length: a.length + 1 }, () => Array<number>(b.length + 1).fill(0));

  for (let i = 1; i <= a.length; i += 1) {
    for (let j = 1; j <= b.length; j += 1) {
      dp[i][j] = a[i - 1] === b[j - 1]
        ? dp[i - 1][j - 1] + 1
        : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }

  const rows: DiffRow[] = [];
  let i = a.length;
  let j = b.length;

  while (i > 0 && j > 0) {
    if (a[i - 1] === b[j - 1]) {
      rows.push({ kind: "context", oldNumber: i, newNumber: j, text: a[i - 1] });
      i -= 1;
      j -= 1;
    } else if (dp[i - 1][j] >= dp[i][j - 1]) {
      rows.push({ kind: "removed", oldNumber: i, newNumber: null, text: a[i - 1] });
      i -= 1;
    } else {
      rows.push({ kind: "added", oldNumber: null, newNumber: j, text: b[j - 1] });
      j -= 1;
    }
  }

  while (i > 0) {
    rows.push({ kind: "removed", oldNumber: i, newNumber: null, text: a[i - 1] });
    i -= 1;
  }

  while (j > 0) {
    rows.push({ kind: "added", oldNumber: null, newNumber: j, text: b[j - 1] });
    j -= 1;
  }

  return rows.reverse();
};

type InlineCodeDiffProps = {
  session: CodeDiffSession;
  onAccept?: () => void;
  onDecline?: () => void;
};

export function InlineCodeDiff({ session, onAccept, onDecline }: InlineCodeDiffProps) {
  const rows = useMemo(() => {
    return buildUnifiedRows(session.originalContent, session.currentContent);
  }, [session.currentContent, session.originalContent]);

  const stats = useMemo(() => rows.reduce(
    (acc, row) => {
      if (row.kind === "added") acc.added += 1;
      if (row.kind === "removed") acc.removed += 1;
      return acc;
    },
    { added: 0, removed: 0 },
  ), [rows]);

  const hasDiff = stats.added > 0 || stats.removed > 0;

  const statusLabel = session.pendingConfirmation
    ? "Awaiting save"
    : session.status === "draft"
      ? "Draft changes"
      : session.status === "saved"
        ? "Saved"
        : session.status === "output"
          ? "Output"
          : "Read";

  const statusColors = session.pendingConfirmation
    ? { bg: "rgba(250,204,21,0.18)", border: "rgba(250,204,21,0.35)", text: "#fde68a" }
    : session.status === "saved"
      ? { bg: "rgba(34,197,94,0.18)", border: "rgba(34,197,94,0.35)", text: "#86efac" }
      : session.status === "draft"
        ? { bg: "rgba(96,165,250,0.18)", border: "rgba(96,165,250,0.35)", text: "#93c5fd" }
        : { bg: "rgba(255,255,255,0.05)", border: "rgba(255,255,255,0.08)", text: "rgba(255,255,255,0.75)" };

  return (
    <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Header bar */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        padding: "10px 16px",
        borderBottom: "1px solid rgba(255,255,255,0.06)",
        background: "rgba(255,255,255,0.03)",
        flexShrink: 0,
      }}>
        <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 3 }}>
          <div style={{ fontSize: 12, fontWeight: 700, color: "#e2e8f0", fontFamily: "'JetBrains Mono', 'Fira Code', monospace", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {session.filename}
          </div>
          {session.summary ? (
            <div style={{ fontSize: 11, color: "rgba(255,255,255,0.42)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {session.summary}
            </div>
          ) : null}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          {hasDiff ? (
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, fontWeight: 700 }}>
              <span style={{ color: "#4ade80" }}>+{stats.added}</span>
              <span style={{ color: "#f87171" }}>-{stats.removed}</span>
            </div>
          ) : null}
          <div style={{
            padding: "4px 8px",
            borderRadius: 999,
            background: statusColors.bg,
            border: `1px solid ${statusColors.border}`,
            color: statusColors.text,
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: 0.2,
          }}>
            {statusLabel}
          </div>
        </div>
      </div>

      {/* Accept / Decline bar */}
      {session.pendingConfirmation && (onAccept || onDecline) ? (
        <div style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
          padding: "10px 16px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
          background: "rgba(250,204,21,0.06)",
          flexShrink: 0,
        }}>
          <span style={{ fontSize: 12, color: "#fde68a", fontWeight: 600 }}>
            Echo wants to save this file. Apply changes?
          </span>
          <div style={{ display: "flex", gap: 8 }}>
            {onDecline ? (
              <button
                onClick={onDecline}
                style={{
                  padding: "6px 16px",
                  borderRadius: 8,
                  border: "1px solid rgba(239,68,68,0.4)",
                  background: "rgba(239,68,68,0.12)",
                  color: "#fca5a5",
                  fontSize: 12,
                  fontWeight: 700,
                  cursor: "pointer",
                  transition: "all 0.15s",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(239,68,68,0.25)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "rgba(239,68,68,0.12)"; }}
              >
                Decline
              </button>
            ) : null}
            {onAccept ? (
              <button
                onClick={onAccept}
                style={{
                  padding: "6px 16px",
                  borderRadius: 8,
                  border: "1px solid rgba(34,197,94,0.5)",
                  background: "rgba(34,197,94,0.18)",
                  color: "#4ade80",
                  fontSize: 12,
                  fontWeight: 700,
                  cursor: "pointer",
                  transition: "all 0.15s",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(34,197,94,0.35)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "rgba(34,197,94,0.18)"; }}
              >
                Accept
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* Full file diff body */}
      <div style={{ flex: 1, overflow: "auto" }}>
        <div style={{ minWidth: "100%", fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace", fontSize: 12, lineHeight: 1.7, color: "#c9d1d9" }}>
          {rows.map((row, index) => {
            const isAdded = row.kind === "added";
            const isRemoved = row.kind === "removed";

            const rowBg = isAdded
              ? "rgba(34,197,94,0.15)"
              : isRemoved
                ? "rgba(239,68,68,0.15)"
                : "transparent";
            const borderColor = isAdded
              ? "#4ade80"
              : isRemoved
                ? "#f87171"
                : "transparent";
            const textColor = isAdded
              ? "#bbf7d0"
              : isRemoved
                ? "#fecaca"
                : "#c9d1d9";
            const sign = isAdded ? "+" : isRemoved ? "−" : " ";
            const signColor = isAdded ? "#4ade80" : isRemoved ? "#f87171" : "rgba(255,255,255,0.12)";
            const gutterBg = isAdded
              ? "rgba(34,197,94,0.08)"
              : isRemoved
                ? "rgba(239,68,68,0.08)"
                : "transparent";

            return (
              <div key={`${row.kind}-${row.oldNumber ?? "x"}-${row.newNumber ?? "x"}-${index}`} style={{
                display: "grid",
                gridTemplateColumns: "38px 38px 22px minmax(0, 1fr)",
                background: rowBg,
                borderLeft: `3px solid ${borderColor}`,
                minHeight: 22,
              }}>
                <span style={{
                  color: "rgba(255,255,255,0.22)",
                  textAlign: "right",
                  userSelect: "none",
                  padding: "0 6px 0 0",
                  background: gutterBg,
                  fontSize: 11,
                }}>
                  {row.oldNumber ?? ""}
                </span>
                <span style={{
                  color: "rgba(255,255,255,0.22)",
                  textAlign: "right",
                  userSelect: "none",
                  padding: "0 6px 0 0",
                  background: gutterBg,
                  fontSize: 11,
                }}>
                  {row.newNumber ?? ""}
                </span>
                <span style={{
                  color: signColor,
                  userSelect: "none",
                  fontWeight: 800,
                  textAlign: "center",
                  fontSize: 13,
                }}>
                  {sign}
                </span>
                <span style={{
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  padding: "1px 12px 1px 4px",
                  color: textColor,
                  textDecoration: isRemoved ? "line-through" : "none",
                  textDecorationColor: isRemoved ? "rgba(248,113,113,0.4)" : undefined,
                }}>
                  {row.text || " "}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
