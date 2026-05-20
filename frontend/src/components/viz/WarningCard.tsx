/**
 * Generic warning / risk card for tool_result single-value display with caveat.
 *
 * Used by solar_system (sentry_risk / opik_collision / afrho / neatm) and
 * exoplanet (any future risk metric) for emphasizing a single numeric value
 * with severity + warning context.
 *
 * M1 (2026-05-20).
 */
import type { ReactNode } from "react";

type Severity = "info" | "warn" | "danger" | "ok";

interface WarningCardProps {
  title?: string;
  value: number | string | null | undefined;
  unit?: string;
  precision?: number;
  severity?: Severity;
  caveat?: ReactNode;
  footer?: ReactNode;
}

const SEVERITY_STYLES: Record<Severity, { border: string; bg: string; chip: string; text: string }> = {
  info:   { border: "#2a5d7b", bg: "#f0f5fa", chip: "#2a5d7b", text: "INFO" },
  ok:     { border: "#2e6a4e", bg: "#eff7f1", chip: "#2e6a4e", text: "OK" },
  warn:   { border: "#b08800", bg: "#fdf6e3", chip: "#b08800", text: "CAUTION" },
  danger: { border: "#7b2d26", bg: "#fbecea", chip: "#7b2d26", text: "DANGER" },
};

function formatNumber(v: number | string | null | undefined, precision = 4): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (!Number.isFinite(v)) return "—";
  if (Math.abs(v) < 1e-3 || Math.abs(v) >= 1e6) return v.toExponential(3);
  return v.toPrecision(precision);
}

export default function WarningCard({
  title,
  value,
  unit,
  precision,
  severity = "info",
  caveat,
  footer,
}: WarningCardProps) {
  const style = SEVERITY_STYLES[severity];
  return (
    <div style={{
      border: `2px solid ${style.border}`,
      borderRadius: 6,
      padding: "12px 16px",
      background: style.bg,
      fontFamily: "var(--font-serif, Georgia, serif)",
      maxWidth: 560,
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        {title && <div style={{ fontWeight: 600, fontSize: 14 }}>{title}</div>}
        <span style={{
          background: style.chip,
          color: "white",
          padding: "2px 10px",
          borderRadius: 12,
          fontSize: 10,
          letterSpacing: 1,
          fontWeight: 700,
        }}>{style.text}</span>
      </div>
      <div style={{
        fontSize: 28,
        fontFamily: "monospace",
        fontWeight: 600,
        color: style.border,
        textAlign: "center",
        margin: "10px 0",
      }}>
        {formatNumber(value, precision)}
        {unit && <span style={{ fontSize: 16, color: "#888", marginLeft: 6 }}>{unit}</span>}
      </div>
      {caveat && (
        <div style={{ fontSize: 12, color: "#444", marginTop: 6, lineHeight: 1.5 }}>
          {caveat}
        </div>
      )}
      {footer && <div style={{ fontSize: 11, color: "#666", marginTop: 8 }}>{footer}</div>}
    </div>
  );
}
