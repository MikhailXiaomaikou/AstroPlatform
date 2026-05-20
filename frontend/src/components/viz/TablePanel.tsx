/**
 * Generic 2-column table panel for tool_result key-value display.
 *
 * Used by solar_system / exoplanet tools that return a single dict of named
 * scalars (orbital elements, planet parameters, etc.). Replaces a dedicated
 * per-tool component (Karpathy 三相似才抽象 — 4 generic panels cover 20+ tools).
 *
 * M1 (2026-05-20).
 */
import type { ReactNode } from "react";

export interface TableField {
  label: string;
  value: number | string | null | undefined;
  unit?: string;
  precision?: number;  // sig figs for number values
}

interface TablePanelProps {
  title?: string;
  subtitle?: string;
  fields: TableField[];
  caveat?: string;
  footer?: ReactNode;
}

function formatValue(field: TableField): string {
  const { value, precision } = field;
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "—";
    if (precision !== undefined && precision > 0) {
      return value.toPrecision(precision);
    }
    // default: 4 sig figs for non-integers, plain int otherwise
    if (Number.isInteger(value)) return String(value);
    return value.toPrecision(4);
  }
  return String(value);
}

export default function TablePanel({ title, subtitle, fields, caveat, footer }: TablePanelProps) {
  return (
    <div className="table-panel" style={{
      border: "1px solid #d1cdbc",
      borderRadius: 4,
      padding: "10px 14px",
      background: "#fcfbf7",
      fontFamily: "var(--font-serif, Georgia, serif)",
      maxWidth: 560,
    }}>
      {title && <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>{title}</div>}
      {subtitle && <div style={{ fontSize: 12, color: "#666", marginBottom: 8 }}>{subtitle}</div>}
      <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 13 }}>
        <tbody>
          {fields.map((f, i) => (
            <tr key={i} style={{ borderBottom: "1px dashed #e5e2d3" }}>
              <td style={{ padding: "3px 8px 3px 0", color: "#444", width: "55%" }}>{f.label}</td>
              <td style={{ padding: "3px 0", fontFamily: "monospace", textAlign: "right" }}>
                {formatValue(f)}
                {f.unit && <span style={{ color: "#888", marginLeft: 4 }}>{f.unit}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {caveat && (
        <div style={{ fontSize: 11, color: "#7b2d26", marginTop: 8, fontStyle: "italic" }}>
          ⚠ {caveat}
        </div>
      )}
      {footer && <div style={{ fontSize: 11, color: "#666", marginTop: 6 }}>{footer}</div>}
    </div>
  );
}
