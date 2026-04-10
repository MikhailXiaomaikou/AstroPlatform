import { useMemo, useState } from "react";
import Plot from "react-plotly.js";

interface Props {
  initialData?: Record<string, unknown>;
  initialChartType?: string;
  onClose?: () => void;
}

/* ── Helpers ── */
function arrMin(a: number[]): number { return a.reduce((x, y) => x < y ? x : y, a[0]); }
function arrMax(a: number[]): number { return a.reduce((x, y) => x > y ? x : y, a[0]); }
function median(a: number[]): number { const s = [...a].sort((x, y) => x - y); const m = Math.floor(s.length / 2); return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; }

function degToHMS(deg: number): string {
  const h = deg / 15; const hh = Math.floor(h);
  const mm = Math.floor((h - hh) * 60);
  const ss = ((h - hh) * 60 - mm) * 60;
  return `${hh}ʰ${String(mm).padStart(2, "0")}ᵐ${ss.toFixed(1)}ˢ`;
}
function degToDMS(deg: number): string {
  const sign = deg < 0 ? "−" : "+";
  const a = Math.abs(deg); const dd = Math.floor(a);
  const mm = Math.floor((a - dd) * 60);
  const ss = ((a - dd) * 60 - mm) * 60;
  return `${sign}${dd}°${String(mm).padStart(2, "0")}′${ss.toFixed(0)}″`;
}

/* ── Publication style constants ── */
const FONT = "'CMU Serif', 'STIX Two Text', 'Times New Roman', 'STIXGeneral', Georgia, serif";
const MONO = "'CMU Typewriter Text', 'Courier New', monospace";

const COLORS = {
  bg: "#fafaf8",       // warm white paper
  plot: "#ffffff",
  grid: "rgba(0,0,0,0.08)",
  axis: "#333333",
  tick: "#444444",
  title: "#1a1a1a",
  text: "#333333",
  // Data colors — colorblind-safe palette (Tol)
  blue: "#4477AA",
  cyan: "#66CCEE",
  green: "#228833",
  yellow: "#CCBB44",
  red: "#EE6677",
  purple: "#AA3377",
  grey: "#BBBBBB",
};

function mkAxis(label: string, extra?: Record<string, unknown>): Record<string, unknown> {
  return {
    title: { text: label, font: { family: FONT, size: 16, color: COLORS.title }, standoff: 12 },
    gridcolor: COLORS.grid,
    gridwidth: 1,
    linecolor: COLORS.axis,
    linewidth: 1.5,
    mirror: "ticks",
    ticks: "inside",
    ticklen: 6,
    tickwidth: 1.2,
    tickcolor: COLORS.axis,
    tickfont: { family: MONO, size: 11, color: COLORS.tick },
    tickformat: ".4g",
    minor: { ticks: "inside", ticklen: 3, tickwidth: 0.8, tickcolor: "rgba(0,0,0,0.3)" },
    zeroline: false,
    showline: true,
    ...extra,
  };
}

function mkLayout(title: string, xa: Record<string, unknown>, ya: Record<string, unknown>, extra?: Record<string, unknown>): Record<string, unknown> {
  const hasColorbar = extra?.hasColorbar;
  const cleanExtra = extra ? Object.fromEntries(Object.entries(extra).filter(([k]) => k !== "hasColorbar")) : {};
  return {
    paper_bgcolor: COLORS.bg,
    plot_bgcolor: COLORS.plot,
    font: { family: FONT, color: COLORS.text, size: 13 },
    margin: { l: 90, r: hasColorbar ? 110 : 40, t: 65, b: 80, pad: 4 },
    autosize: true,
    showlegend: false,
    title: { text: title, font: { family: FONT, size: 18, color: COLORS.title }, x: 0.5, xanchor: "center", y: 0.97 },
    xaxis: xa,
    yaxis: ya,
    ...cleanExtra,
  };
}

function mkColorbar(label: string): Record<string, unknown> {
  return {
    title: { text: label, font: { family: FONT, size: 14, color: COLORS.title }, side: "right" },
    tickfont: { family: MONO, size: 11, color: COLORS.tick },
    thickness: 16,
    len: 0.7,
    outlinewidth: 1,
    outlinecolor: COLORS.axis,
    borderwidth: 0,
    xpad: 12,
  };
}

/* ── Fitting ── */
function linearFit(x: number[], y: number[]): { slope: number; intercept: number; r2: number } {
  const n = x.length;
  const sx = x.reduce((a, b) => a + b, 0), sy = y.reduce((a, b) => a + b, 0);
  const sxx = x.reduce((a, b, i) => a + b * x[i], 0), sxy = x.reduce((a, b, i) => a + b * y[i], 0);
  const slope = (n * sxy - sx * sy) / (n * sxx - sx * sx);
  const intercept = (sy - slope * sx) / n;
  const yMean = sy / n;
  const ssTot = y.reduce((a, b) => a + (b - yMean) ** 2, 0);
  const ssRes = y.reduce((a, b, i) => a + (b - (slope * x[i] + intercept)) ** 2, 0);
  return { slope, intercept, r2: ssTot > 0 ? 1 - ssRes / ssTot : 0 };
}

function gaussianKDE(values: number[], nPts = 120): { x: number[]; y: number[] } {
  if (values.length === 0) return { x: [], y: [] };
  const lo = arrMin(values), hi = arrMax(values), rng = hi - lo || 1;
  // Silverman's rule for bandwidth
  const std = Math.sqrt(values.reduce((a, v) => a + (v - values.reduce((s, w) => s + w, 0) / values.length) ** 2, 0) / values.length) || rng / 4;
  const h = 1.06 * std * Math.pow(values.length, -0.2);
  const xs: number[] = [], ys: number[] = [];
  for (let i = 0; i < nPts; i++) {
    const x = lo - rng * 0.15 + (rng * 1.3 * i) / (nPts - 1);
    let d = 0;
    for (const v of values) d += Math.exp(-0.5 * ((x - v) / h) ** 2);
    d /= values.length * h * Math.sqrt(2 * Math.PI);
    xs.push(x); ys.push(d);
  }
  return { x: xs, y: ys };
}

/* ── Annotation helper ── */
function statsAnnotation(values: number[], _label: string, xRef = "paper", yRef = "paper", x = 0.98, y = 0.95): Record<string, unknown> {
  if (values.length === 0) return {};
  const n = values.length;
  const mean = values.reduce((a, b) => a + b, 0) / n;
  const med = median(values);
  const std = Math.sqrt(values.reduce((a, v) => a + (v - mean) ** 2, 0) / n);
  return {
    text: `<i>N</i> = ${n}<br>μ = ${mean.toFixed(4)}<br>med = ${med.toFixed(4)}<br>σ = ${std.toFixed(4)}`,
    showarrow: false,
    xref: xRef, yref: yRef, x, y,
    xanchor: "right", yanchor: "top",
    font: { family: MONO, size: 11, color: COLORS.text },
    bgcolor: "rgba(255,255,255,0.85)",
    bordercolor: COLORS.grid,
    borderwidth: 1,
    borderpad: 6,
  };
}

/* ── Build plots ── */
function buildPlot(
  chartType: string,
  data: Record<string, unknown>,
  showFit: boolean,
  showStats: boolean,
  customScatterOpts?: { xCol: string; yCol: string; colorCol: string; flipY: boolean },
): { data: Record<string, unknown>[]; layout: Record<string, unknown> } {
  const ra = (data.ra || []) as number[];
  const dec = (data.dec || []) as number[];
  const names = (data.names || []) as string[];
  const redshift = ((data.redshift || []) as (number | null)[]).filter((v): v is number => v != null && v === v);
  const magnitude = ((data.magnitude || []) as (number | null)[]).filter((v): v is number => v != null && v === v);

  if (chartType === "sky_coverage") {
    const hasZ = redshift.length === ra.length && redshift.length > 0;
    const traces: Record<string, unknown>[] = [{
      type: "scattergl", mode: "markers", x: ra, y: dec, text: names,
      marker: {
        size: hasZ ? 5 : 4,
        symbol: "circle",
        color: hasZ ? redshift : COLORS.blue,
        colorscale: "Plasma",
        reversescale: false,
        showscale: hasZ,
        colorbar: hasZ ? mkColorbar("Redshift <i>z</i>") : undefined,
        line: { width: 0.5, color: "rgba(0,0,0,0.15)" },
        opacity: 0.85,
      },
      hovertemplate: "<b>%{text}</b><br>α = %{x:.5f}°<br>δ = %{y:.5f}°" + (hasZ ? "<br><i>z</i> = %{marker.color:.4f}" : "") + "<extra></extra>",
    }];
    if (showFit && ra.length >= 2) {
      const fit = linearFit(ra, dec);
      const x0 = arrMin(ra), x1 = arrMax(ra);
      traces.push({
        type: "scatter", mode: "lines",
        x: [x0, x1], y: [fit.slope * x0 + fit.intercept, fit.slope * x1 + fit.intercept],
        line: { color: COLORS.red, width: 2, dash: "dash" },
        name: `Linear: R² = ${fit.r2.toFixed(4)}`, showlegend: true,
      });
    }
    const annotations: Record<string, unknown>[] = [];
    if (showStats) annotations.push(statsAnnotation(ra, "RA"));
    return {
      data: traces,
      layout: mkLayout(`Sky Distribution (<i>N</i> = ${ra.length})`,
        mkAxis("Right Ascension α (deg)", { autorange: "reversed" }),
        mkAxis("Declination δ (deg)"),
        { hasColorbar: hasZ, showlegend: showFit, annotations, legend: { font: { family: FONT, size: 12 }, bgcolor: "rgba(255,255,255,0.8)", bordercolor: COLORS.grid, borderwidth: 1, x: 0.02, y: 0.98 } }),
    };
  }

  if (chartType === "redshift_histogram") {
    if (redshift.length === 0) return { data: [], layout: mkLayout("No redshift data", mkAxis(""), mkAxis("")) };
    const lo = arrMin(redshift), hi = arrMax(redshift), rng = hi - lo;
    const nBins = Math.min(50, Math.max(8, Math.ceil(Math.sqrt(redshift.length))));
    const binW = Math.max(rng * 1.1 / nBins, 0.0005);
    const traces: Record<string, unknown>[] = [{
      type: "histogram", x: redshift,
      marker: { color: COLORS.blue, line: { color: "rgba(0,0,0,0.3)", width: 0.8 } },
      xbins: { start: lo - rng * 0.05, end: hi + rng * 0.05, size: binW },
      opacity: 0.82,
    }];
    if (showFit) {
      const kde = gaussianKDE(redshift, 120);
      const scale = redshift.length * binW;
      traces.push({
        type: "scatter", mode: "lines",
        x: kde.x, y: kde.y.map((v) => v * scale),
        line: { color: COLORS.red, width: 2.5 },
        name: "KDE", showlegend: true,
      });
    }
    const annotations: Record<string, unknown>[] = [];
    if (showStats) annotations.push(statsAnnotation(redshift, "z"));
    return {
      data: traces,
      layout: mkLayout(`Redshift Distribution (<i>N</i> = ${redshift.length})`,
        mkAxis("Redshift <i>z</i>", { range: [lo - rng * 0.12, hi + rng * 0.12] }),
        mkAxis("Number of Objects"),
        { bargap: 0.03, showlegend: showFit, annotations, legend: { font: { family: FONT, size: 12 }, bgcolor: "rgba(255,255,255,0.8)", x: 0.75, y: 0.95 } }),
    };
  }

  if (chartType === "magnitude_histogram") {
    if (magnitude.length === 0) return { data: [], layout: mkLayout("No magnitude data", mkAxis(""), mkAxis("")) };
    const lo = arrMin(magnitude), hi = arrMax(magnitude), rng = hi - lo;
    const nBins = Math.min(50, Math.max(8, Math.ceil(Math.sqrt(magnitude.length))));
    const binW = Math.max(rng * 1.1 / nBins, 0.01);
    const traces: Record<string, unknown>[] = [{
      type: "histogram", x: magnitude,
      marker: { color: COLORS.purple, line: { color: "rgba(0,0,0,0.3)", width: 0.8 } },
      xbins: { start: lo - rng * 0.05, end: hi + rng * 0.05, size: binW },
      opacity: 0.82,
    }];
    if (showFit) {
      const kde = gaussianKDE(magnitude, 120);
      const scale = magnitude.length * binW;
      traces.push({
        type: "scatter", mode: "lines",
        x: kde.x, y: kde.y.map((v) => v * scale),
        line: { color: COLORS.red, width: 2.5 },
        name: "KDE", showlegend: true,
      });
    }
    const annotations: Record<string, unknown>[] = [];
    if (showStats) annotations.push(statsAnnotation(magnitude, "mag"));
    return {
      data: traces,
      layout: mkLayout(`Magnitude Distribution (<i>N</i> = ${magnitude.length})`,
        mkAxis("Apparent Magnitude (mag)"),
        mkAxis("Number of Objects"),
        { bargap: 0.03, showlegend: showFit, annotations }),
    };
  }

  if (chartType === "ra_dec_redshift") {
    const n = Math.min(ra.length, dec.length, redshift.length);
    if (n === 0) return { data: [], layout: mkLayout("Insufficient data", mkAxis(""), mkAxis("")) };
    return {
      data: [{
        type: "scattergl", mode: "markers",
        x: ra.slice(0, n), y: dec.slice(0, n), text: names.slice(0, n),
        marker: {
          size: 6, symbol: "circle", color: redshift.slice(0, n),
          colorscale: "Turbo", showscale: true,
          colorbar: mkColorbar("<i>z</i>"),
          line: { width: 0.5, color: "rgba(0,0,0,0.12)" },
          opacity: 0.88,
        },
        hovertemplate: "<b>%{text}</b><br>α = %{x:.5f}°<br>δ = %{y:.5f}°<br><i>z</i> = %{marker.color:.4f}<extra></extra>",
      }],
      layout: mkLayout(`Sky Position by Redshift (<i>N</i> = ${n})`,
        mkAxis("Right Ascension α (deg)", { autorange: "reversed" }),
        mkAxis("Declination δ (deg)"),
        { hasColorbar: true }),
    };
  }

  if (chartType === "redshift_ra") {
    const n = Math.min(ra.length, redshift.length);
    if (n === 0) return { data: [], layout: mkLayout("No data", mkAxis(""), mkAxis("")) };
    const xd = ra.slice(0, n), yd = redshift.slice(0, n);
    const traces: Record<string, unknown>[] = [{
      type: "scattergl", mode: "markers",
      x: xd, y: yd, text: names.slice(0, n),
      marker: { size: 5, color: COLORS.green, symbol: "circle", line: { width: 0.5, color: "rgba(0,0,0,0.12)" }, opacity: 0.82 },
      hovertemplate: "<b>%{text}</b><br>α = %{x:.5f}°<br><i>z</i> = %{y:.4f}<extra></extra>",
    }];
    if (showFit && n >= 2) {
      const fit = linearFit(xd, yd);
      const x0 = arrMin(xd), x1 = arrMax(xd);
      traces.push({
        type: "scatter", mode: "lines",
        x: [x0, x1], y: [fit.slope * x0 + fit.intercept, fit.slope * x1 + fit.intercept],
        line: { color: COLORS.red, width: 2, dash: "dash" },
        name: `Linear: <i>R</i>² = ${fit.r2.toFixed(4)}`, showlegend: true,
      });
    }
    const annotations: Record<string, unknown>[] = [];
    if (showStats) annotations.push(statsAnnotation(yd, "z"));
    return {
      data: traces,
      layout: mkLayout(`Redshift vs Right Ascension (<i>N</i> = ${n})`,
        mkAxis("Right Ascension α (deg)"),
        mkAxis("Redshift <i>z</i>"),
        { showlegend: showFit, annotations }),
    };
  }

  if (chartType === "redshift_dec") {
    const n = Math.min(dec.length, redshift.length);
    if (n === 0) return { data: [], layout: mkLayout("No data", mkAxis(""), mkAxis("")) };
    const xd = dec.slice(0, n), yd = redshift.slice(0, n);
    const traces: Record<string, unknown>[] = [{
      type: "scattergl", mode: "markers",
      x: xd, y: yd, text: names.slice(0, n),
      marker: { size: 5, color: COLORS.purple, symbol: "circle", line: { width: 0.5, color: "rgba(0,0,0,0.12)" }, opacity: 0.82 },
      hovertemplate: "<b>%{text}</b><br>δ = %{x:.5f}°<br><i>z</i> = %{y:.4f}<extra></extra>",
    }];
    if (showFit && n >= 2) {
      const fit = linearFit(xd, yd);
      const x0 = arrMin(xd), x1 = arrMax(xd);
      traces.push({
        type: "scatter", mode: "lines",
        x: [x0, x1], y: [fit.slope * x0 + fit.intercept, fit.slope * x1 + fit.intercept],
        line: { color: COLORS.red, width: 2, dash: "dash" },
        name: `Linear: <i>R</i>² = ${fit.r2.toFixed(4)}`, showlegend: true,
      });
    }
    const annotations: Record<string, unknown>[] = [];
    if (showStats) annotations.push(statsAnnotation(yd, "z"));
    return {
      data: traces,
      layout: mkLayout(`Redshift vs Declination (<i>N</i> = ${n})`,
        mkAxis("Declination δ (deg)"),
        mkAxis("Redshift <i>z</i>"),
        { showlegend: showFit, annotations }),
    };
  }

  if (chartType === "density_sky") {
    if (ra.length === 0) return { data: [], layout: mkLayout("No data", mkAxis(""), mkAxis("")) };
    return {
      data: [{
        type: "histogram2d",
        x: ra, y: dec,
        colorscale: "Hot", reversescale: true,
        colorbar: mkColorbar("Count"),
        nbinsx: Math.min(30, Math.max(8, Math.ceil(Math.sqrt(ra.length)))),
        nbinsy: Math.min(30, Math.max(8, Math.ceil(Math.sqrt(ra.length)))),
      }],
      layout: mkLayout(`Sky Density Map (<i>N</i> = ${ra.length})`,
        mkAxis("Right Ascension α (deg)", { autorange: "reversed" }),
        mkAxis("Declination δ (deg)"),
        { hasColorbar: true }),
    };
  }

  if (chartType === "scatter_custom" && customScatterOpts) {
    const { xCol, yCol, colorCol, flipY } = customScatterOpts;
    const xArr = (data[xCol] || []) as number[];
    const yArr = (data[yCol] || []) as number[];
    const n = Math.min(xArr.length, yArr.length);
    if (n === 0) return { data: [], layout: mkLayout("No data for selected columns", mkAxis(""), mkAxis("")) };
    const hasColor = colorCol && colorCol !== "" && data[colorCol];
    const colorArr = hasColor ? (data[colorCol] as number[]).slice(0, n) : undefined;
    const traces: Record<string, unknown>[] = [{
      type: "scattergl", mode: "markers",
      x: xArr.slice(0, n), y: yArr.slice(0, n),
      marker: {
        size: 4, symbol: "circle",
        color: colorArr || COLORS.cyan,
        colorscale: colorArr ? "Viridis" : undefined,
        showscale: !!colorArr,
        colorbar: colorArr ? mkColorbar(colorCol) : undefined,
        line: { width: 0.5, color: "rgba(0,0,0,0.12)" },
        opacity: 0.82,
      },
    }];
    if (showFit && n >= 2) {
      const fit = linearFit(xArr.slice(0, n), yArr.slice(0, n));
      const x0 = arrMin(xArr.slice(0, n)), x1 = arrMax(xArr.slice(0, n));
      traces.push({
        type: "scatter", mode: "lines",
        x: [x0, x1], y: [fit.slope * x0 + fit.intercept, fit.slope * x1 + fit.intercept],
        line: { color: COLORS.red, width: 2, dash: "dash" },
        name: `Linear: <i>R</i>\u00B2 = ${fit.r2.toFixed(4)}`, showlegend: true,
      });
    }
    const annotations: Record<string, unknown>[] = [];
    if (showStats) annotations.push(statsAnnotation(yArr.slice(0, n), yCol));
    return {
      data: traces,
      layout: mkLayout(`${xCol} vs ${yCol} (<i>N</i> = ${n})`,
        mkAxis(xCol),
        mkAxis(yCol, flipY ? { autorange: "reversed" } : undefined),
        { showlegend: showFit, annotations, hasColorbar: !!colorArr }),
    };
  }

  // Fallback: auto scatter
  const numKeys = Object.keys(data).filter(
    (k) => Array.isArray(data[k]) && (data[k] as unknown[]).length > 0 && typeof (data[k] as unknown[])[0] === "number"
  );
  const xKey = numKeys[0] || "ra", yKey = numKeys[1] || "dec";
  const xArr = (data[xKey] || []) as number[], yArr = (data[yKey] || []) as number[];
  const n = Math.min(xArr.length, yArr.length);
  const traces: Record<string, unknown>[] = [{
    type: "scattergl", mode: "markers",
    x: xArr.slice(0, n), y: yArr.slice(0, n), text: names,
    marker: { size: 5, color: COLORS.cyan, symbol: "circle", line: { width: 0.5, color: "rgba(0,0,0,0.12)" }, opacity: 0.82 },
  }];
  if (showFit && n >= 2) {
    const fit = linearFit(xArr.slice(0, n), yArr.slice(0, n));
    const x0 = arrMin(xArr), x1 = arrMax(xArr);
    traces.push({
      type: "scatter", mode: "lines",
      x: [x0, x1], y: [fit.slope * x0 + fit.intercept, fit.slope * x1 + fit.intercept],
      line: { color: COLORS.red, width: 2, dash: "dash" },
      name: `Linear: <i>R</i>² = ${fit.r2.toFixed(4)}`, showlegend: true,
    });
  }
  return {
    data: traces,
    layout: mkLayout(`${xKey} vs ${yKey} (<i>N</i> = ${n})`, mkAxis(xKey), mkAxis(yKey), { showlegend: showFit }),
  };
}

const CHART_TYPES: Record<string, string> = {
  sky_coverage: "Sky Distribution (α vs δ)",
  density_sky: "Sky Density Map (2D histogram)",
  redshift_histogram: "Redshift Distribution",
  magnitude_histogram: "Magnitude Distribution",
  ra_dec_redshift: "Sky Position (colored by z)",
  redshift_ra: "Redshift vs RA",
  redshift_dec: "Redshift vs Dec",
  scatter_custom: "Custom Scatter",
};

export default function PlotBuilder({ initialData, initialChartType, onClose }: Props) {
  const [chartType, setChartType] = useState(initialChartType || "sky_coverage");
  const [showFit, setShowFit] = useState(false);
  const [showStats, setShowStats] = useState(true);
  const [xMin, setXMin] = useState("");
  const [xMax, setXMax] = useState("");
  const [yMin, setYMin] = useState("");
  const [yMax, setYMax] = useState("");
  const [coordFormat, setCoordFormat] = useState<"decimal" | "hms">("decimal");

  // Custom scatter column selectors
  const numericColumns = useMemo(() => {
    if (!initialData) return [];
    return Object.keys(initialData).filter((k) => {
      const arr = initialData[k];
      return Array.isArray(arr) && arr.length > 0 && typeof arr[0] === "number";
    });
  }, [initialData]);
  const [customX, setCustomX] = useState("");
  const [customY, setCustomY] = useState("");
  const [customColor, setCustomColor] = useState("");
  const [flipY, setFlipY] = useState(false);

  const availableCharts = useMemo(() => {
    if (!initialData) return CHART_TYPES;
    const z = ((initialData.redshift || []) as unknown[]).filter((v) => v != null);
    const mag = ((initialData.magnitude || []) as unknown[]).filter((v) => v != null);
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(CHART_TYPES)) {
      if ((k === "redshift_histogram" || k === "ra_dec_redshift" || k === "redshift_ra" || k === "redshift_dec") && z.length === 0) continue;
      if (k === "magnitude_histogram" && mag.length === 0) continue;
      out[k] = v;
    }
    return out;
  }, [initialData]);

  const plotResult = useMemo(() => {
    if (!initialData) return null;
    const scatterOpts = chartType === "scatter_custom" && customX && customY
      ? { xCol: customX, yCol: customY, colorCol: customColor, flipY }
      : undefined;
    const result = buildPlot(chartType, initialData, showFit, showStats, scatterOpts);

    // Apply custom axis ranges
    const xHasMin = xMin !== "" && !isNaN(Number(xMin));
    const xHasMax = xMax !== "" && !isNaN(Number(xMax));
    const yHasMin = yMin !== "" && !isNaN(Number(yMin));
    const yHasMax = yMax !== "" && !isNaN(Number(yMax));
    if (xHasMin || xHasMax) {
      const xa = { ...(result.layout.xaxis as Record<string, unknown>) };
      xa.range = [xHasMin ? Number(xMin) : undefined, xHasMax ? Number(xMax) : undefined];
      xa.autorange = false;
      result.layout = { ...result.layout, xaxis: xa };
    }
    if (yHasMin || yHasMax) {
      const ya = { ...(result.layout.yaxis as Record<string, unknown>) };
      ya.range = [yHasMin ? Number(yMin) : undefined, yHasMax ? Number(yMax) : undefined];
      ya.autorange = false;
      result.layout = { ...result.layout, yaxis: ya };
    }

    // HMS/DMS formatting
    if (coordFormat === "hms" && (chartType === "sky_coverage" || chartType === "ra_dec_redshift")) {
      const ra = (initialData.ra || []) as number[];
      const dec = (initialData.dec || []) as number[];
      if (ra.length > 0 && dec.length > 0) {
        const xa = { ...(result.layout.xaxis as Record<string, unknown>) };
        const ya = { ...(result.layout.yaxis as Record<string, unknown>) };
        const raLo = arrMin(ra), raHi = arrMax(ra), raStep = Math.max((raHi - raLo) / 7, 0.001);
        const raTicks: number[] = [], raLabels: string[] = [];
        for (let v = Math.ceil(raLo / raStep) * raStep; v <= raHi; v += raStep) { raTicks.push(v); raLabels.push(degToHMS(v)); }
        xa.tickvals = raTicks; xa.ticktext = raLabels;
        const decLo = arrMin(dec), decHi = arrMax(dec), decStep = Math.max((decHi - decLo) / 7, 0.001);
        const decTicks: number[] = [], decLabels: string[] = [];
        for (let v = Math.ceil(decLo / decStep) * decStep; v <= decHi; v += decStep) { decTicks.push(v); decLabels.push(degToDMS(v)); }
        ya.tickvals = decTicks; ya.ticktext = decLabels;
        result.layout = { ...result.layout, xaxis: xa, yaxis: ya };
      }
    }

    return result;
  }, [chartType, initialData, showFit, showStats, xMin, xMax, yMin, yMax, coordFormat, customX, customY, customColor, flipY]);

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
          <label className="fit-toggle">
            <input type="checkbox" checked={showStats} onChange={(e) => setShowStats(e.target.checked)} />
            Stats
          </label>
          {onClose && (
            <button className="btn-secondary btn-small" onClick={onClose}>Close</button>
          )}
        </div>
      </div>

      <div className="plot-axis-ranges">
        <label className="plot-range-label">X min <input type="text" value={xMin} onChange={(e) => setXMin(e.target.value)} className="plot-range-input" placeholder="auto" /></label>
        <label className="plot-range-label">X max <input type="text" value={xMax} onChange={(e) => setXMax(e.target.value)} className="plot-range-input" placeholder="auto" /></label>
        <label className="plot-range-label">Y min <input type="text" value={yMin} onChange={(e) => setYMin(e.target.value)} className="plot-range-input" placeholder="auto" /></label>
        <label className="plot-range-label">Y max <input type="text" value={yMax} onChange={(e) => setYMax(e.target.value)} className="plot-range-input" placeholder="auto" /></label>
        <label className="plot-range-label">
          Coords{" "}
          <select value={coordFormat} onChange={(e) => setCoordFormat(e.target.value as "decimal" | "hms")} className="plot-range-input" style={{ width: "auto", minWidth: 90 }}>
            <option value="decimal">Decimal</option>
            <option value="hms">HMS/DMS</option>
          </select>
        </label>
      </div>

      {chartType === "scatter_custom" && numericColumns.length > 0 && (
        <div className="plot-axis-ranges" style={{ marginTop: 4 }}>
          <label className="plot-range-label">
            X axis{" "}
            <select value={customX} onChange={(e) => setCustomX(e.target.value)} className="plot-range-input" style={{ width: "auto", minWidth: 120 }}>
              <option value="">-- select --</option>
              {numericColumns.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label className="plot-range-label">
            Y axis{" "}
            <select value={customY} onChange={(e) => setCustomY(e.target.value)} className="plot-range-input" style={{ width: "auto", minWidth: 120 }}>
              <option value="">-- select --</option>
              {numericColumns.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label className="plot-range-label">
            Color{" "}
            <select value={customColor} onChange={(e) => setCustomColor(e.target.value)} className="plot-range-input" style={{ width: "auto", minWidth: 120 }}>
              <option value="">none</option>
              {numericColumns.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label className="fit-toggle">
            <input type="checkbox" checked={flipY} onChange={(e) => setFlipY(e.target.checked)} />
            Flip Y
          </label>
        </div>
      )}

      {plotResult && plotResult.data.length > 0 ? (
        <Plot
          data={plotResult.data as any}
          layout={plotResult.layout as any}
          config={{
            displayModeBar: true,
            displaylogo: false,
            modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"],
            toImageButtonOptions: { format: "png", filename: "astro_plot", width: 1400, height: 1000, scale: 3 },
          }}
          style={{ width: "100%", height: "600px" }}
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
