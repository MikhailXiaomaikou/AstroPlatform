import { useMemo, useState } from "react";
import Plot from "react-plotly.js";

interface Props {
  initialData?: Record<string, unknown>;
  initialChartType?: string;
  onClose?: () => void;
}

/* ── Publication-quality style ── */
const FONT = "Times New Roman, STIXGeneral, serif";
const AXIS_STYLE = {
  gridcolor: "rgba(200,200,200,0.3)",
  gridwidth: 1,
  zerolinecolor: "rgba(255,255,255,0.2)",
  linecolor: "rgba(255,255,255,0.5)",
  linewidth: 1.5,
  mirror: true,
  ticks: "outside" as const,
  ticklen: 5,
  tickwidth: 1.5,
  tickcolor: "rgba(255,255,255,0.5)",
  tickfont: { family: FONT, size: 13, color: "rgba(255,255,255,0.85)" },
  title: { font: { family: FONT, size: 15, color: "rgba(255,255,255,0.95)" } },
};

const BASE_LAYOUT: Record<string, unknown> = {
  paper_bgcolor: "rgba(20, 20, 24, 1)",
  plot_bgcolor: "rgba(30, 30, 36, 1)",
  font: { family: FONT, color: "rgba(255,255,255,0.9)", size: 13 },
  margin: { l: 75, r: 20, t: 55, b: 65 },
  autosize: true,
  showlegend: false,
};

function makeLayout(overrides: Record<string, unknown>) {
  return {
    ...(BASE_LAYOUT as any),
    xaxis: { ...AXIS_STYLE, ...(overrides.xaxis as any || {}) },
    yaxis: { ...AXIS_STYLE, ...(overrides.yaxis as any || {}) },
    ...overrides,
  };
}

function buildPlotData(
  chartType: string,
  data: Record<string, unknown>
): { data: Record<string, unknown>[]; layout: Record<string, unknown> } {
  const ra = (data.ra || []) as number[];
  const dec = (data.dec || []) as number[];
  const names = (data.names || []) as string[];
  const redshift = (data.redshift || []) as number[];
  const magnitude = (data.magnitude || []) as number[];

  if (chartType === "sky_coverage") {
    const hasZ = redshift.length === ra.length && redshift.length > 0;
    return {
      data: [{
        type: "scattergl",
        mode: "markers",
        x: ra, y: dec, text: names,
        marker: {
          size: 4,
          color: hasZ ? redshift : "rgba(56,189,248,0.7)",
          colorscale: "Viridis",
          showscale: hasZ,
          colorbar: hasZ ? {
            title: { text: "Redshift (z)", font: { family: FONT, size: 13 } },
            tickfont: { family: FONT, size: 11 },
            thickness: 15, len: 0.8,
          } : undefined,
          line: { width: 0.3, color: "rgba(255,255,255,0.3)" },
        },
        hovertemplate: "<b>%{text}</b><br>\u03b1 = %{x:.5f}\u00b0<br>\u03b4 = %{y:.5f}\u00b0<extra></extra>",
      }],
      layout: makeLayout({
        title: { text: `Sky Distribution (N = ${ra.length})`, font: { family: FONT, size: 16 } },
        xaxis: { ...AXIS_STYLE, title: { text: "Right Ascension \u03b1 (deg)", font: { family: FONT, size: 15 } }, autorange: "reversed" },
        yaxis: { ...AXIS_STYLE, title: { text: "Declination \u03b4 (deg)", font: { family: FONT, size: 15 } } },
      }),
    };
  }

  if (chartType === "redshift_histogram") {
    if (redshift.length === 0) {
      return { data: [], layout: makeLayout({ title: { text: "No redshift data available" } }) };
    }
    const zMin = Math.min(...redshift);
    const zMax = Math.max(...redshift);
    return {
      data: [{
        type: "histogram",
        x: redshift,
        marker: { color: "rgba(56,189,248,0.75)", line: { color: "rgba(56,189,248,1)", width: 1 } },
        nbinsx: Math.min(40, Math.max(10, Math.ceil(redshift.length / 3))),
      }],
      layout: makeLayout({
        title: { text: `Redshift Distribution (N = ${redshift.length})`, font: { family: FONT, size: 16 } },
        xaxis: { ...AXIS_STYLE, title: { text: "Redshift (z)", font: { family: FONT, size: 15 } }, range: [zMin - 0.1, zMax + 0.1] },
        yaxis: { ...AXIS_STYLE, title: { text: "Number of Objects", font: { family: FONT, size: 15 } } },
        bargap: 0.05,
      }),
    };
  }

  if (chartType === "magnitude_histogram") {
    if (magnitude.length === 0) {
      return { data: [], layout: makeLayout({ title: { text: "No magnitude data available" } }) };
    }
    return {
      data: [{
        type: "histogram",
        x: magnitude,
        marker: { color: "rgba(244,114,182,0.75)", line: { color: "rgba(244,114,182,1)", width: 1 } },
        nbinsx: Math.min(40, Math.max(10, Math.ceil(magnitude.length / 3))),
      }],
      layout: makeLayout({
        title: { text: `Magnitude Distribution (N = ${magnitude.length})`, font: { family: FONT, size: 16 } },
        xaxis: { ...AXIS_STYLE, title: { text: "Apparent Magnitude (mag)", font: { family: FONT, size: 15 } } },
        yaxis: { ...AXIS_STYLE, title: { text: "Number of Objects", font: { family: FONT, size: 15 } } },
        bargap: 0.05,
      }),
    };
  }

  if (chartType === "ra_dec_redshift") {
    const minLen = Math.min(ra.length, dec.length, redshift.length);
    if (minLen === 0) {
      return { data: [], layout: makeLayout({ title: { text: "Insufficient data for this plot" } }) };
    }
    return {
      data: [{
        type: "scattergl",
        mode: "markers",
        x: ra.slice(0, minLen), y: dec.slice(0, minLen), text: names.slice(0, minLen),
        marker: {
          size: 5,
          color: redshift.slice(0, minLen),
          colorscale: "Portland",
          showscale: true,
          colorbar: {
            title: { text: "z", font: { family: FONT, size: 13 } },
            tickfont: { family: FONT, size: 11 },
            thickness: 15, len: 0.8,
          },
          line: { width: 0.3, color: "rgba(255,255,255,0.2)" },
        },
        hovertemplate: "<b>%{text}</b><br>\u03b1 = %{x:.5f}\u00b0<br>\u03b4 = %{y:.5f}\u00b0<br>z = %{marker.color:.4f}<extra></extra>",
      }],
      layout: makeLayout({
        title: { text: `Sky Position Colored by Redshift (N = ${minLen})`, font: { family: FONT, size: 16 } },
        xaxis: { ...AXIS_STYLE, title: { text: "Right Ascension \u03b1 (deg)", font: { family: FONT, size: 15 } }, autorange: "reversed" },
        yaxis: { ...AXIS_STYLE, title: { text: "Declination \u03b4 (deg)", font: { family: FONT, size: 15 } } },
      }),
    };
  }

  if (chartType === "redshift_ra" && redshift.length > 0) {
    const minLen = Math.min(ra.length, redshift.length);
    return {
      data: [{
        type: "scattergl",
        mode: "markers",
        x: ra.slice(0, minLen), y: redshift.slice(0, minLen), text: names.slice(0, minLen),
        marker: { size: 4, color: "rgba(52,211,153,0.7)", line: { width: 0.3, color: "rgba(255,255,255,0.2)" } },
        hovertemplate: "<b>%{text}</b><br>\u03b1 = %{x:.5f}\u00b0<br>z = %{y:.4f}<extra></extra>",
      }],
      layout: makeLayout({
        title: { text: `Redshift vs Right Ascension (N = ${minLen})`, font: { family: FONT, size: 16 } },
        xaxis: { ...AXIS_STYLE, title: { text: "Right Ascension \u03b1 (deg)", font: { family: FONT, size: 15 } } },
        yaxis: { ...AXIS_STYLE, title: { text: "Redshift (z)", font: { family: FONT, size: 15 } } },
      }),
    };
  }

  // Fallback: scatter of first two numeric arrays
  const numericKeys = Object.keys(data).filter(
    (k) => Array.isArray(data[k]) && (data[k] as unknown[]).length > 0 && typeof (data[k] as unknown[])[0] === "number"
  );
  const xKey = numericKeys[0] || "ra";
  const yKey = numericKeys[1] || "dec";
  const xArr = (data[xKey] || []) as number[];
  const yArr = (data[yKey] || []) as number[];
  return {
    data: [{
      type: "scattergl", mode: "markers",
      x: xArr, y: yArr, text: names,
      marker: { size: 4, color: "rgba(52,211,153,0.7)" },
    }],
    layout: makeLayout({
      title: { text: `${xKey} vs ${yKey} (N = ${Math.min(xArr.length, yArr.length)})`, font: { family: FONT, size: 16 } },
      xaxis: { ...AXIS_STYLE, title: { text: xKey } },
      yaxis: { ...AXIS_STYLE, title: { text: yKey } },
    }),
  };
}

const CHART_TYPES: Record<string, string> = {
  sky_coverage: "Sky Distribution (\u03b1 vs \u03b4)",
  redshift_histogram: "Redshift Distribution",
  magnitude_histogram: "Magnitude Distribution",
  ra_dec_redshift: "Sky Position (colored by z)",
  redshift_ra: "Redshift vs RA",
  scatter_custom: "Custom Scatter",
};

export default function PlotBuilder({ initialData, initialChartType, onClose }: Props) {
  const [chartType, setChartType] = useState(initialChartType || "sky_coverage");

  // Filter chart types based on available data
  const availableCharts = useMemo(() => {
    if (!initialData) return CHART_TYPES;
    const z = (initialData.redshift || []) as unknown[];
    const mag = (initialData.magnitude || []) as unknown[];
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(CHART_TYPES)) {
      if (k === "redshift_histogram" && z.length === 0) continue;
      if (k === "magnitude_histogram" && mag.length === 0) continue;
      if (k === "ra_dec_redshift" && z.length === 0) continue;
      if (k === "redshift_ra" && z.length === 0) continue;
      out[k] = v;
    }
    return out;
  }, [initialData]);

  const plotResult = useMemo(() => {
    if (!initialData) return null;
    return buildPlotData(chartType, initialData);
  }, [chartType, initialData]);

  return (
    <div className="plot-builder">
      <div className="plot-builder-header">
        <h3>Interactive Visualization</h3>
        <div className="plot-builder-controls">
          <select value={chartType} onChange={(e) => setChartType(e.target.value)} className="image-select">
            {Object.entries(availableCharts).map(([key, label]) => (
              <option key={key} value={key}>{label}</option>
            ))}
          </select>
          {onClose && (
            <button className="btn-secondary btn-small" onClick={onClose}>Close</button>
          )}
        </div>
      </div>

      {plotResult && plotResult.data.length > 0 ? (
        <Plot
          data={plotResult.data as any}
          layout={plotResult.layout as any}
          config={{
            displayModeBar: true,
            displaylogo: false,
            modeBarButtonsToRemove: ["lasso2d", "select2d"],
            toImageButtonOptions: { format: "png", filename: "astro_plot", width: 1200, height: 800, scale: 2 },
          }}
          style={{ width: "100%", height: "550px" }}
          className="plot-container"
        />
      ) : (
        <div className="plot-empty">
          {plotResult ? "No data available for this chart type" : "No data to visualize"}
        </div>
      )}
    </div>
  );
}
