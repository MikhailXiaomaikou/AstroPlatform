/**
 * Generic horizontal bar chart for classification confidence (χ² / probability)
 * over a fixed set of categories.
 *
 * Used by:
 *   - classify_asteroid_busdemeo (12 Bus-DeMeo classes, χ² per class)
 *   - classify_asteroid_sdss_colors (9 Carvano classes, χ² per class)
 *   - any future categorical-distance result.
 *
 * M1 (2026-05-20).
 */
import { useMemo } from "react";
import Plot from "react-plotly.js";

/* eslint-disable @typescript-eslint/no-explicit-any */
type PlotData = Record<string, any>;
type PlotLayout = Record<string, any>;

interface BarChartPanelProps {
  categories: string[];
  values: number[];
  highlight_label?: string;     // best-class label, drawn in accent color
  title?: string;
  value_label?: string;          // x-axis label (e.g. "χ²")
  // when smaller value = better fit (χ², distance), pass true
  smaller_is_better?: boolean;
  caveat?: string;
}

export default function BarChartPanel({
  categories,
  values,
  highlight_label,
  title,
  value_label = "value",
  smaller_is_better = true,
  caveat,
}: BarChartPanelProps) {
  const traces = useMemo<PlotData[]>(() => {
    const colors = categories.map(c => c === highlight_label ? "#7b2d26" : "#2a5d7b");
    return [{
      type: "bar",
      orientation: "h",
      x: values,
      y: categories,
      marker: { color: colors },
      hovertemplate: `%{y}: ${value_label}=%{x:.3f}<extra></extra>`,
    }];
  }, [categories, values, highlight_label, value_label]);

  const layout = useMemo<PlotLayout>(() => ({
    title: title || "",
    xaxis: {
      title: value_label,
      ...(smaller_is_better ? { autorange: "reversed" } : {}),
    },
    yaxis: { automargin: true },
    height: Math.max(180, categories.length * 28 + 80),
    margin: { l: 80, r: 30, t: 30, b: 40 },
    showlegend: false,
    paper_bgcolor: "#fcfbf7",
    plot_bgcolor: "#fcfbf7",
    font: { family: "Georgia, serif" },
  }), [title, value_label, smaller_is_better, categories.length]);

  return (
    <div style={{ maxWidth: 560, border: "1px solid #d1cdbc", borderRadius: 4, background: "#fcfbf7", padding: 8 }}>
      <Plot
        data={traces}
        layout={layout}
        config={{ responsive: true, displaylogo: false }}
        style={{ width: "100%" }}
      />
      {highlight_label && (
        <div style={{ fontSize: 12, color: "#7b2d26", textAlign: "center", marginTop: 4 }}>
          <strong>Best class:</strong> {highlight_label}
        </div>
      )}
      {caveat && (
        <div style={{ fontSize: 11, color: "#7b2d26", padding: "4px 8px", fontStyle: "italic" }}>
          ⚠ {caveat}
        </div>
      )}
    </div>
  );
}
