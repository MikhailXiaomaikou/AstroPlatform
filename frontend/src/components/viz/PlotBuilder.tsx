import { useEffect, useState } from "react";
import Plot from "react-plotly.js";

interface Props {
  initialData?: Record<string, unknown>;
  initialChartType?: string;
  onClose?: () => void;
}

const CHART_TYPES: Record<string, string> = {
  sky_coverage: "Sky Coverage (RA vs Dec)",
  redshift_histogram: "Redshift Histogram",
  magnitude_histogram: "Magnitude Histogram",
  ra_dec_redshift: "RA vs Dec (colored by z)",
  scatter_custom: "Custom Scatter",
};

function buildPlotData(
  chartType: string,
  data: Record<string, unknown>
): { data: Record<string, unknown>[]; layout: Record<string, unknown> } {
  const ra = (data.ra || []) as number[];
  const dec = (data.dec || []) as number[];
  const names = (data.names || []) as string[];
  const redshift = (data.redshift || []) as number[];
  const magnitude = (data.magnitude || []) as number[];

  const darkLayout: Record<string, unknown> = {
    paper_bgcolor: "rgba(28, 28, 30, 1)",
    plot_bgcolor: "rgba(44, 44, 46, 1)",
    font: { color: "rgba(255, 255, 255, 0.85)", family: "system-ui" },
    xaxis: { gridcolor: "rgba(255,255,255,0.06)", zerolinecolor: "rgba(255,255,255,0.1)" },
    yaxis: { gridcolor: "rgba(255,255,255,0.06)", zerolinecolor: "rgba(255,255,255,0.1)" },
    margin: { l: 60, r: 30, t: 50, b: 50 },
    autosize: true,
  };

  if (chartType === "sky_coverage") {
    return {
      data: [{
        type: "scattergl",
        mode: "markers",
        x: ra,
        y: dec,
        text: names,
        marker: {
          size: 5,
          color: redshift.length === ra.length ? redshift : "#38bdf8",
          colorscale: "Viridis",
          showscale: redshift.length === ra.length,
          colorbar: redshift.length === ra.length ? { title: "Redshift" } : undefined,
        },
        hovertemplate: "%{text}<br>RA: %{x:.4f}°<br>Dec: %{y:.4f}°<extra></extra>",
      }],
      layout: {
        ...(darkLayout as any),
        title: `Sky Coverage (${ra.length} objects)`,
        xaxis: { ...(darkLayout.xaxis as any), title: "RA (deg)", autorange: "reversed" },
        yaxis: { ...(darkLayout.yaxis as any), title: "Dec (deg)" },
      },
    };
  }

  if (chartType === "redshift_histogram") {
    return {
      data: [{
        type: "histogram",
        x: redshift,
        marker: { color: "#38bdf8" },
        nbinsx: Math.min(50, Math.max(10, Math.ceil(redshift.length / 5))),
      }],
      layout: {
        ...(darkLayout as any),
        title: `Redshift Distribution (${redshift.length} objects)`,
        xaxis: { ...(darkLayout.xaxis as any), title: "Redshift (z)" },
        yaxis: { ...(darkLayout.yaxis as any), title: "Count" },
      },
    };
  }

  if (chartType === "magnitude_histogram") {
    return {
      data: [{
        type: "histogram",
        x: magnitude,
        marker: { color: "#f472b6" },
        nbinsx: Math.min(50, Math.max(10, Math.ceil(magnitude.length / 5))),
      }],
      layout: {
        ...(darkLayout as any),
        title: `Magnitude Distribution (${magnitude.length} objects)`,
        xaxis: { ...(darkLayout.xaxis as any), title: "Magnitude" },
        yaxis: { ...(darkLayout.yaxis as any), title: "Count" },
      },
    };
  }

  if (chartType === "ra_dec_redshift") {
    const minLen = Math.min(ra.length, dec.length, redshift.length);
    return {
      data: [{
        type: "scattergl",
        mode: "markers",
        x: ra.slice(0, minLen),
        y: dec.slice(0, minLen),
        text: names.slice(0, minLen),
        marker: {
          size: 6,
          color: redshift.slice(0, minLen),
          colorscale: "Portland",
          showscale: true,
          colorbar: { title: "z" },
        },
        hovertemplate: "%{text}<br>RA: %{x:.4f}°<br>Dec: %{y:.4f}°<br>z: %{marker.color:.4f}<extra></extra>",
      }],
      layout: {
        ...(darkLayout as any),
        title: `RA vs Dec colored by Redshift (${minLen} objects)`,
        xaxis: { ...(darkLayout.xaxis as any), title: "RA (deg)", autorange: "reversed" },
        yaxis: { ...(darkLayout.yaxis as any), title: "Dec (deg)" },
      },
    };
  }

  // Default: scatter of first two numeric arrays
  const numericKeys = Object.keys(data).filter(
    (k) => Array.isArray(data[k]) && (data[k] as unknown[]).length > 0 && typeof (data[k] as unknown[])[0] === "number"
  );
  const xKey = numericKeys[0] || "ra";
  const yKey = numericKeys[1] || "dec";
  const xArr = (data[xKey] || []) as number[];
  const yArr = (data[yKey] || []) as number[];

  return {
    data: [{
      type: "scattergl",
      mode: "markers",
      x: xArr,
      y: yArr,
      text: names,
      marker: { size: 5, color: "#34d399" },
    }],
    layout: {
      ...(darkLayout as any),
      title: `${xKey} vs ${yKey}`,
      xaxis: { ...(darkLayout.xaxis as any), title: xKey },
      yaxis: { ...(darkLayout.yaxis as any), title: yKey },
    },
  };
}

export default function PlotBuilder({ initialData, initialChartType, onClose }: Props) {
  const [chartType, setChartType] = useState(initialChartType || "sky_coverage");
  const [plotResult, setPlotResult] = useState<{ data: Record<string, unknown>[]; layout: Record<string, unknown> } | null>(null);

  useEffect(() => {
    if (initialData) {
      setPlotResult(buildPlotData(chartType, initialData));
    }
  }, [chartType, initialData]);

  return (
    <div className="plot-builder">
      <div className="plot-builder-header">
        <h3>Interactive Visualization</h3>
        <div className="plot-builder-controls">
          <select
            value={chartType}
            onChange={(e) => setChartType(e.target.value)}
            className="image-select"
          >
            {Object.entries(CHART_TYPES).map(([key, label]) => (
              <option key={key} value={key}>{label}</option>
            ))}
          </select>
          {onClose && (
            <button className="btn-secondary btn-small" onClick={onClose}>Close</button>
          )}
        </div>
      </div>

      {plotResult ? (
        <Plot
          data={plotResult.data}
          layout={plotResult.layout}
          config={{
            displayModeBar: true,
            displaylogo: false,
            modeBarButtonsToRemove: ["lasso2d", "select2d"],
            toImageButtonOptions: {
              format: "png",
              filename: "astro_plot",
              width: 1200,
              height: 800,
            },
          }}
          style={{ width: "100%", height: "500px" }}
          className="plot-container"
        />
      ) : (
        <div className="plot-empty">No data to visualize</div>
      )}
    </div>
  );
}
