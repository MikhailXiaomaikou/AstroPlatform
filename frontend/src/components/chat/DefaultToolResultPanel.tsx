// Fallback renderer for tool results whose toolName does not match any of
// the specialised panel routes in ChatPage.AutoToolResult. Replaces a
// previous bare ``<pre>`` JSON dump that gave users no signal about what
// they were looking at (no header, no status, no actionable next step).
//
// Triggers in two situations:
// 1. Backend ships a new tool the frontend hasn't routed yet — without
//    this, the result silently rendered as 500 chars of JSON.
// 2. A tool returns an unexpected payload shape that the specialised
//    panel's gate can't recognise (e.g. analysis_status not in the
//    whitelist after backend domain changes).
//
// Keep this card visually consistent with the other chat/* panels:
// header chip + brief context + collapsible payload.

import type { CSSProperties } from "react";

interface DefaultToolResultPanelProps {
  toolName: string;
  result: Record<string, unknown>;
}

function statusTone(status: string): { color: string; bg: string; border: string } {
  const key = status.toUpperCase();
  if (key === "COMPLETED" || key === "READY") {
    return { color: "#166534", bg: "rgba(34,197,94,.10)", border: "#22c55e" };
  }
  if (key === "FAILED" || key === "ERROR") {
    return { color: "#7f1d1d", bg: "rgba(239,68,68,.10)", border: "#ef4444" };
  }
  if (key === "PARTIAL" || key === "EMPTY" || key === "UNAVAILABLE" || key === "EXPLORATORY") {
    return { color: "#8a5b00", bg: "rgba(255,183,0,.13)", border: "#d99a00" };
  }
  return { color: "var(--color-text-tertiary)", bg: "rgba(127,127,127,.08)", border: "var(--color-border)" };
}

const PRE_STYLE: CSSProperties = {
  margin: 0,
  padding: 8,
  background: "rgba(0,0,0,.04)",
  borderRadius: 4,
  fontSize: "0.7rem",
  maxHeight: 280,
  overflow: "auto",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  color: "var(--color-text-secondary)",
};

export default function DefaultToolResultPanel({ toolName, result }: DefaultToolResultPanelProps) {
  const status = String(result.__tool_status__ || result.analysis_status || "").toUpperCase();
  const tone = statusTone(status);
  const message = typeof result.__message_to_model__ === "string"
    ? result.__message_to_model__
    : typeof result.user_facing_message === "string"
      ? result.user_facing_message
      : "";
  const error = typeof result.error === "string" ? result.error : "";
  const warnings = Array.isArray(result.warnings)
    ? result.warnings.filter((w): w is string => typeof w === "string")
    : [];

  let prettyJson = "";
  try {
    prettyJson = JSON.stringify(result, null, 2);
  } catch {
    prettyJson = String(result);
  }
  // Hard-cap to keep the DOM cheap; users can still copy/paste the visible portion.
  const truncated = prettyJson.length > 12000;
  const previewJson = truncated ? prettyJson.slice(0, 12000) + "\n… (truncated)" : prettyJson;

  return (
    <div style={{ fontSize: "0.78rem", color: "var(--color-text-secondary)", lineHeight: 1.45 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        <strong style={{ color: "var(--color-text-primary)" }}>{toolName}</strong>
        {status ? (
          <span
            style={{
              color: tone.color,
              background: tone.bg,
              border: `1px solid ${tone.border}`,
              borderRadius: 4,
              padding: "1px 6px",
              fontSize: "0.7rem",
              fontWeight: 600,
            }}
          >
            {status}
          </span>
        ) : null}
        <span style={{ fontSize: "0.7rem", color: "var(--color-text-tertiary)" }}>
          (no dedicated view for this tool yet — showing raw payload)
        </span>
      </div>

      {error ? (
        <div style={{ color: "#7f1d1d", marginBottom: 6 }}>{error}</div>
      ) : null}

      {message ? (
        <div style={{ marginBottom: 6, color: "var(--color-text-secondary)" }}>{message}</div>
      ) : null}

      {warnings.length > 0 ? (
        <ul style={{ margin: "0 0 8px 18px", padding: 0, color: "#8a5b00" }}>
          {warnings.slice(0, 4).map((w) => (
            <li key={w}>⚠ {w}</li>
          ))}
        </ul>
      ) : null}

      <details>
        <summary style={{ cursor: "pointer", color: "var(--color-text-tertiary)", fontSize: "0.72rem" }}>
          Show raw payload ({prettyJson.length.toLocaleString()} chars)
        </summary>
        <pre style={PRE_STYLE}>{previewJson}</pre>
      </details>
    </div>
  );
}
