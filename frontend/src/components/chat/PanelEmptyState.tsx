// Shared empty-state body for chat result panels.
//
// Problem: when a tool returns EMPTY / UNAVAILABLE / FAILED and the
// specialised panel has no payload to render, the card historically
// showed only a status chip on top + empty whitespace below. Users
// hit this with ResearchProgramPanel ("Research Plan ⚠ 1 data warning"
// box with nothing inside) and again with CosmologyMCMCPanel when the
// chain returned NO_COMPRESSED_LIKELIHOOD with empty parameters.
//
// This component is the canonical "nothing happened, here's why" body
// every panel should render in those states. The copy is keyed off
// the tool's status string plus the optional ``__message_to_model__``
// the backend already emits.

import type { CSSProperties } from "react";

interface PanelEmptyStateProps {
  status: string;
  message?: string;
  suggestion?: string;
  /** Optional title override (defaults: "No data" / "Unavailable" / "Failed"). */
  title?: string;
}

const ROOT_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  padding: "10px 12px",
  borderRadius: 6,
  fontSize: "0.78rem",
  lineHeight: 1.45,
};

const ICON_STYLE: CSSProperties = {
  fontSize: "1rem",
  marginRight: 6,
};

interface Tone {
  bg: string;
  border: string;
  fg: string;
  icon: string;
  defaultTitle: string;
  defaultBody: string;
}

function toneForStatus(status: string): Tone {
  const key = status.toUpperCase();
  if (key === "FAILED" || key === "ERROR") {
    return {
      bg: "rgba(239, 68, 68, 0.08)",
      border: "#ef4444",
      fg: "#7f1d1d",
      icon: "⚠",
      defaultTitle: "Tool failed",
      defaultBody: "The tool returned an error before producing any data.",
    };
  }
  if (key === "UNAVAILABLE" || key === "MAINTENANCE") {
    return {
      bg: "rgba(127, 127, 127, 0.08)",
      border: "var(--color-border)",
      fg: "var(--color-text-secondary)",
      icon: "🛠",
      defaultTitle: "Source under maintenance",
      defaultBody:
        "This source is temporarily unavailable while its provenance metadata is being upgraded.",
    };
  }
  if (key === "EMPTY") {
    return {
      bg: "rgba(255, 183, 0, 0.10)",
      border: "#d99a00",
      fg: "#8a5b00",
      icon: "∅",
      defaultTitle: "No data returned",
      defaultBody:
        "The tool ran without error but returned 0 rows. Try broadening the search parameters or pick a different data source.",
    };
  }
  if (key === "PARTIAL") {
    return {
      bg: "rgba(255, 183, 0, 0.08)",
      border: "#d99a00",
      fg: "#8a5b00",
      icon: "◐",
      defaultTitle: "Partial result",
      defaultBody:
        "The tool produced a partial result. Some required fields are missing — see warnings.",
    };
  }
  if (key === "BLOCKED") {
    return {
      bg: "rgba(239, 68, 68, 0.06)",
      border: "#ef4444",
      fg: "#7f1d1d",
      icon: "✕",
      defaultTitle: "Result blocked",
      defaultBody:
        "The tool produced output that cannot be cited (e.g. inline rows without attestation, or insufficient ESS). See warnings.",
    };
  }
  return {
    bg: "rgba(127, 127, 127, 0.08)",
    border: "var(--color-border)",
    fg: "var(--color-text-secondary)",
    icon: "ℹ",
    defaultTitle: "Nothing to display",
    defaultBody:
      "The tool returned, but the panel found no fields to render. See the raw payload for details.",
  };
}

export default function PanelEmptyState({
  status,
  message,
  suggestion,
  title,
}: PanelEmptyStateProps) {
  const tone = toneForStatus(status);
  return (
    <div
      style={{
        ...ROOT_STYLE,
        background: tone.bg,
        border: `1px solid ${tone.border}`,
        color: tone.fg,
      }}
      role="status"
    >
      <div style={{ display: "flex", alignItems: "center", color: tone.fg, fontWeight: 600 }}>
        <span style={ICON_STYLE} aria-hidden="true">
          {tone.icon}
        </span>
        {title || tone.defaultTitle}
      </div>
      <div style={{ color: "var(--color-text-secondary)" }}>
        {message?.trim() || tone.defaultBody}
      </div>
      {suggestion ? (
        <div style={{ color: "var(--color-text-tertiary)", fontSize: "0.72rem", marginTop: 2 }}>
          {suggestion}
        </div>
      ) : null}
    </div>
  );
}
