/**
 * Generic Plotly X-Y scatter / line panel.
 *
 * Used by tool_results that return parallel x/y arrays (ephemeris, light curve,
 * phase-magnitude table, transit fit). Auto-upgrades to scattergl for N > 5000.
 *
 * M1 (2026-05-20).
 */
import { useMemo } from "react";
import Plot from "react-plotly.js";

/* eslint-disable @typescript-eslint/no-explicit-any */
type PlotData = Record<string, any>;
type PlotLayout = Record<string, any>;

interface PlotlyXYPanelProps {
  x: number[];
  y: number[];
  x_label?: string;
  y_label?: string;
  title?: string;
  mode?: "markers" | "lines" | "lines+markers";
  error_y?: number[];
  // optional second trace overlay
  y2?: number[];
  y2_label?: string;
  // invert y-axis (e.g. for magnitudes)
  y_invert?: boolean;
  caveat?: string;
}

export default function PlotlyXYPanel({
  x, y, x_label, y_label, title,
  mode = "markers",
  error_y, y2, y2_label,
  y_invert = false,
  caveat,
}: PlotlyXYPanelProps) {
  const traces = useMemo<PlotData[]>(() => {
    const pointType = x.length > 5000 ? "scattergl" : "scatter";
    const out: PlotData[] = [
      {
        x, y, type: pointType, mode,
        name: y_label || "y",
        marker: { color: "#2a5d7b", size: 4 },
        line: { color: "#2a5d7b", width: 1.5 },
        ...(error_y ? { error_y: { type: "data", array: error_y, visible: true, color: "#888" } } : {}),
      },
    ];
    if (y2 && y2.length === x.length) {
      out.push({
        x, y: y2, type: pointType, mode: "lines",
        name: y2_label || "y2",
        line: { color: "#7b2d26", width: 1.5, dash: "dash" },
        yaxis: "y2",
      });
    }
    return out;
  }, [x, y, mode, error_y, y2, y_label, y2_label]);

  const layout = useMemo<PlotLayout>(() => ({
    title: title || "",
    xaxis: { title: x_label || "x" },
    yaxis: {
      title: y_label || "y",
      ...(y_invert ? { autorange: "reversed" } : {}),
    },
    ...(y2 ? {
      yaxis2: { title: y2_label || "y2", overlaying: "y", side: "right" },
    } : {}),
    height: 320,
    margin: { l: 60, r: 60, t: 30, b: 40 },
    showlegend: !!y2,
    paper_bgcolor: "#fcfbf7",
    plot_bgcolor: "#fcfbf7",
    font: { family: "Georgia, serif" },
  }), [title, x_label, y_label, y_invert, y2, y2_label]);

  return (
    <div style={{ maxWidth: 720, border: "1px solid #d1cdbc", borderRadius: 4, background: "#fcfbf7", padding: 8 }}>
      <Plot
        data={traces}
        layout={layout}
        config={{ responsive: true, displaylogo: false }}
        style={{ width: "100%" }}
      />
      {caveat && (
        <div style={{ fontSize: 11, color: "#7b2d26", padding: "4px 8px", fontStyle: "italic" }}>
          ⚠ {caveat}
        </div>
      )}
    </div>
  );
}
