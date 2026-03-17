import { useMemo, useState } from "react";
import Plot from "react-plotly.js";

interface Props {
  initialData?: Record<string, unknown>;
  initialChartType?: string;
  onClose?: () => void;
}

const FONT = "Times New Roman, STIXGeneral, serif";

const AXIS_BASE: Record<string, unknown> = {
  gridcolor: "rgba(200,200,200,0.25)",
  gridwidth: 1,
  zerolinecolor: "rgba(255,255,255,0.2)",
  linecolor: "rgba(255,255,255,0.5)",
  linewidth: 1.5,
  mirror: true,
  ticks: "outside",
  ticklen: 5,
  tickwidth: 1.5,
  tickcolor: "rgba(255,255,255,0.5)",
  tickfont: { family: FONT, size: 13, color: "rgba(255,255,255,0.85)" },
};

function ax(title: string, extra?: Record<string, unknown>) {
  return { ...AXIS_BASE, title: { text: title, font: { family: FONT, size: 15 } }, ...extra };
}

function layout(title: string, xaxis: Record<string, unknown>, yaxis: Record<string, unknown>, extra?: Record<string, unknown>) {
  return {
    paper_bgcolor: "rgba(20,20,24,1)",
    plot_bgcolor: "rgba(30,30,36,1)",
    font: { family: FONT, color: "rgba(255,255,255,0.9)", size: 13 },
    margin: { l: 75, r: 25, t: 55, b: 65 },
    autosize: true,
    showlegend: false,
    title: { text: title, font: { family: FONT, size: 16 } },
    xaxis, yaxis,
    ...extra,
  };
}

/* ── Fitting ── */
function linearFit(x: number[], y: number[]): { slope: number; intercept: number; r2: number } {
  const n = x.length;
  const sx = x.reduce((a, b) => a + b, 0);
  const sy = y.reduce((a, b) => a + b, 0);
  const sxx = x.reduce((a, b, i) => a + b * x[i], 0);
  const sxy = x.reduce((a, b, i) => a + b * y[i], 0);
  const slope = (n * sxy - sx * sy) / (n * sxx - sx * sx);
  const intercept = (sy - slope * sx) / n;
  const yMean = sy / n;
  const ssTot = y.reduce((a, b) => a + (b - yMean) ** 2, 0);
  const ssRes = y.reduce((a, b, i) => a + (b - (slope * x[i] + intercept)) ** 2, 0);
  const r2 = ssTot > 0 ? 1 - ssRes / ssTot : 0;
  return { slope, intercept, r2 };
}

function gaussianKDE(values: number[], nPoints = 100): { x: number[]; y: number[] } {
  if (values.length === 0) return { x: [], y: [] };
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const bandwidth = range / Math.max(5, Math.sqrt(values.length));
  const xs: number[] = [];
  const ys: number[] = [];
  for (let i = 0; i < nPoints; i++) {
    const x = min - range * 0.1 + (range * 1.2 * i) / (nPoints - 1);
    let density = 0;
    for (const v of values) {
      density += Math.exp(-0.5 * ((x - v) / bandwidth) ** 2);
    }
    density /= values.length * bandwidth * Math.sqrt(2 * Math.PI);
    xs.push(x);
    ys.push(density);
  }
  return { x: xs, y: ys };
}

function buildPlot(
  chartType: string,
  data: Record<string, unknown>,
  showFit: boolean,
): { data: Record<string, unknown>[]; layout: Record<string, unknown> } {
  const ra = (data.ra || []) as number[];
  const dec = (data.dec || []) as number[];
  const names = (data.names || []) as string[];
  const redshift = (data.redshift || []) as number[];
  const magnitude = (data.magnitude || []) as number[];

  if (chartType === "sky_coverage") {
    const hasZ = redshift.length > 0;
    const traces: Record<string, unknown>[] = [{
      type: "scattergl", mode: "markers",
      x: ra, y: dec, text: names,
      marker: {
        size: 4,
        color: hasZ && redshift.length === ra.length ? redshift : "rgba(56,189,248,0.7)",
        colorscale: "Viridis",
        showscale: hasZ && redshift.length === ra.length,
        colorbar: hasZ && redshift.length === ra.length ? {
          title: { text: "z", font: { family: FONT, size: 13 } },
          tickfont: { family: FONT, size: 11 }, thickness: 15, len: 0.8,
        } : undefined,
        line: { width: 0.3, color: "rgba(255,255,255,0.3)" },
      },
      hovertemplate: "<b>%{text}</b><br>\u03b1=%{x:.5f}\u00b0 \u03b4=%{y:.5f}\u00b0<extra></extra>",
    }];
    if (showFit && ra.length >= 2) {
      const fit = linearFit(ra, dec);
      const xMin = Math.min(...ra), xMax = Math.max(...ra);
      traces.push({
        type: "scatter", mode: "lines",
        x: [xMin, xMax], y: [fit.slope * xMin + fit.intercept, fit.slope * xMax + fit.intercept],
        line: { color: "#FF453A", width: 2, dash: "dash" },
        name: `Linear fit (R\u00b2=${fit.r2.toFixed(3)})`,
        showlegend: true,
      });
    }
    return {
      data: traces,
      layout: layout(`Sky Distribution (N=${ra.length})`,
        ax("Right Ascension \u03b1 (deg)", { autorange: "reversed" }),
        ax("Declination \u03b4 (deg)"),
        { showlegend: showFit }),
    };
  }

  if (chartType === "redshift_histogram") {
    if (redshift.length === 0) return { data: [], layout: layout("No redshift data", ax("z"), ax("N")) };
    const zMin = Math.min(...redshift), zMax = Math.max(...redshift);
    const zRange = zMax - zMin;
    const nBins = Math.min(40, Math.max(8, Math.ceil(redshift.length / 3)));
    const traces: Record<string, unknown>[] = [{
      type: "histogram", x: redshift,
      marker: { color: "rgba(56,189,248,0.65)", line: { color: "rgba(56,189,248,1)", width: 1 } },
      xbins: { start: zMin - zRange * 0.05, end: zMax + zRange * 0.05, size: Math.max((zRange * 1.1) / nBins, 0.001) },
    }];
    if (showFit) {
      const kde = gaussianKDE(redshift, 80);
      // Scale KDE to match histogram area
      const binWidth = Math.max((zRange * 1.1) / nBins, 0.001);
      const scale = redshift.length * binWidth;
      traces.push({
        type: "scatter", mode: "lines",
        x: kde.x, y: kde.y.map((v) => v * scale),
        line: { color: "#FF453A", width: 2.5 },
        name: "KDE fit", showlegend: true,
      });
    }
    return {
      data: traces,
      layout: layout(`Redshift Distribution (N=${redshift.length})`,
        ax("Redshift (z)", { range: [zMin - zRange * 0.1, zMax + zRange * 0.1] }),
        ax("Number of Objects"),
        { bargap: 0.05, showlegend: showFit }),
    };
  }

  if (chartType === "magnitude_histogram") {
    if (magnitude.length === 0) return { data: [], layout: layout("No magnitude data", ax("mag"), ax("N")) };
    const mMin = Math.min(...magnitude), mMax = Math.max(...magnitude), mRange = mMax - mMin;
    const nBins = Math.min(40, Math.max(8, Math.ceil(magnitude.length / 3)));
    const traces: Record<string, unknown>[] = [{
      type: "histogram", x: magnitude,
      marker: { color: "rgba(244,114,182,0.65)", line: { color: "rgba(244,114,182,1)", width: 1 } },
      xbins: { start: mMin - mRange * 0.05, end: mMax + mRange * 0.05, size: Math.max((mRange * 1.1) / nBins, 0.01) },
    }];
    if (showFit) {
      const kde = gaussianKDE(magnitude, 80);
      const binWidth = Math.max((mRange * 1.1) / nBins, 0.01);
      const scale = magnitude.length * binWidth;
      traces.push({
        type: "scatter", mode: "lines",
        x: kde.x, y: kde.y.map((v) => v * scale),
        line: { color: "#FF453A", width: 2.5 },
        name: "KDE fit", showlegend: true,
      });
    }
    return {
      data: traces,
      layout: layout(`Magnitude Distribution (N=${magnitude.length})`,
        ax("Apparent Magnitude (mag)"),
        ax("Number of Objects"),
        { bargap: 0.05, showlegend: showFit }),
    };
  }

  if (chartType === "ra_dec_redshift") {
    const minLen = Math.min(ra.length, dec.length, redshift.length);
    if (minLen === 0) return { data: [], layout: layout("Insufficient data", ax(""), ax("")) };
    return {
      data: [{
        type: "scattergl", mode: "markers",
        x: ra.slice(0, minLen), y: dec.slice(0, minLen), text: names.slice(0, minLen),
        marker: {
          size: 5, color: redshift.slice(0, minLen), colorscale: "Portland",
          showscale: true,
          colorbar: { title: { text: "z", font: { family: FONT, size: 13 } }, tickfont: { family: FONT, size: 11 }, thickness: 15, len: 0.8 },
          line: { width: 0.3, color: "rgba(255,255,255,0.2)" },
        },
        hovertemplate: "<b>%{text}</b><br>\u03b1=%{x:.5f}\u00b0 \u03b4=%{y:.5f}\u00b0<br>z=%{marker.color:.4f}<extra></extra>",
      }],
      layout: layout(`Sky Position by Redshift (N=${minLen})`,
        ax("Right Ascension \u03b1 (deg)", { autorange: "reversed" }),
        ax("Declination \u03b4 (deg)")),
    };
  }

  if (chartType === "redshift_ra") {
    const minLen = Math.min(ra.length, redshift.length);
    if (minLen === 0) return { data: [], layout: layout("No data", ax(""), ax("")) };
    const traces: Record<string, unknown>[] = [{
      type: "scattergl", mode: "markers",
      x: ra.slice(0, minLen), y: redshift.slice(0, minLen), text: names.slice(0, minLen),
      marker: { size: 4, color: "rgba(52,211,153,0.7)", line: { width: 0.3, color: "rgba(255,255,255,0.2)" } },
      hovertemplate: "<b>%{text}</b><br>\u03b1=%{x:.5f}\u00b0<br>z=%{y:.4f}<extra></extra>",
    }];
    if (showFit && minLen >= 2) {
      const xd = ra.slice(0, minLen), yd = redshift.slice(0, minLen);
      const fit = linearFit(xd, yd);
      const xMin = Math.min(...xd), xMax = Math.max(...xd);
      traces.push({
        type: "scatter", mode: "lines",
        x: [xMin, xMax], y: [fit.slope * xMin + fit.intercept, fit.slope * xMax + fit.intercept],
        line: { color: "#FF453A", width: 2, dash: "dash" },
        name: `Linear (R\u00b2=${fit.r2.toFixed(3)})`, showlegend: true,
      });
    }
    return {
      data: traces,
      layout: layout(`Redshift vs RA (N=${minLen})`,
        ax("Right Ascension \u03b1 (deg)"),
        ax("Redshift (z)"),
        { showlegend: showFit }),
    };
  }

  // Fallback: auto scatter
  const numKeys = Object.keys(data).filter(
    (k) => Array.isArray(data[k]) && (data[k] as unknown[]).length > 0 && typeof (data[k] as unknown[])[0] === "number"
  );
  const xKey = numKeys[0] || "ra", yKey = numKeys[1] || "dec";
  const xArr = (data[xKey] || []) as number[], yArr = (data[yKey] || []) as number[];
  const minLen = Math.min(xArr.length, yArr.length);
  const traces: Record<string, unknown>[] = [{
    type: "scattergl", mode: "markers",
    x: xArr.slice(0, minLen), y: yArr.slice(0, minLen), text: names,
    marker: { size: 4, color: "rgba(52,211,153,0.7)" },
  }];
  if (showFit && minLen >= 2) {
    const fit = linearFit(xArr.slice(0, minLen), yArr.slice(0, minLen));
    const xMin = Math.min(...xArr), xMax = Math.max(...xArr);
    traces.push({
      type: "scatter", mode: "lines",
      x: [xMin, xMax], y: [fit.slope * xMin + fit.intercept, fit.slope * xMax + fit.intercept],
      line: { color: "#FF453A", width: 2, dash: "dash" },
      name: `Linear (R\u00b2=${fit.r2.toFixed(3)})`, showlegend: true,
    });
  }
  return {
    data: traces,
    layout: layout(`${xKey} vs ${yKey} (N=${minLen})`, ax(xKey), ax(yKey), { showlegend: showFit }),
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
  const [showFit, setShowFit] = useState(false);

  const availableCharts = useMemo(() => {
    if (!initialData) return CHART_TYPES;
    const z = (initialData.redshift || []) as unknown[];
    const mag = (initialData.magnitude || []) as unknown[];
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(CHART_TYPES)) {
      if ((k === "redshift_histogram" || k === "ra_dec_redshift" || k === "redshift_ra") && z.length === 0) continue;
      if (k === "magnitude_histogram" && mag.length === 0) continue;
      out[k] = v;
    }
    return out;
  }, [initialData]);

  const plotResult = useMemo(() => {
    if (!initialData) return null;
    return buildPlot(chartType, initialData, showFit);
  }, [chartType, initialData, showFit]);

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
          <label className="fit-toggle">
            <input type="checkbox" checked={showFit} onChange={(e) => setShowFit(e.target.checked)} />
            Fit
          </label>
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
