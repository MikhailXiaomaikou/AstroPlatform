import { useEffect, useMemo, useState } from "react";
import Plot from "react-plotly.js";

declare global {
  interface Window {
    Plotly?: {
      downloadImage: (gd: HTMLElement, opts: Record<string, unknown>) => Promise<string>;
    };
  }
}

interface Props {
  initialData?: Record<string, unknown>;
  initialChartType?: string;
  onClose?: () => void;
}

/* ── Helpers ── */
function arrMin(a: number[]): number { return a.reduce((x, y) => x < y ? x : y, a[0]); }
function arrMax(a: number[]): number { return a.reduce((x, y) => x > y ? x : y, a[0]); }
function median(a: number[]): number { const s = [...a].sort((x, y) => x - y); const m = Math.floor(s.length / 2); return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; }
function isNumericSeries(values: unknown): values is number[] {
  if (!Array.isArray(values) || values.length === 0) return false;
  for (const value of values.slice(0, 50)) {
    if (typeof value === "number" && Number.isFinite(value)) return true;
  }
  return false;
}

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
const FONT = "'STIX Two Text', 'Times New Roman', 'STIXGeneral', Georgia, 'Noto Serif', serif";
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
  const w = typeof window !== "undefined" ? window.innerWidth : 800;
  const compact = w < 500;
  return {
    paper_bgcolor: COLORS.bg,
    plot_bgcolor: COLORS.plot,
    font: { family: FONT, color: COLORS.text, size: 13 },
    margin: { l: compact ? 50 : 90, r: compact ? 20 : (hasColorbar ? 110 : 40), t: 50, b: compact ? 40 : 80, pad: 4 },
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
    text: `<i>N</i> = ${n}<br>mean = ${mean.toFixed(4)}<br>med = ${med.toFixed(4)}<br>std = ${std.toFixed(4)}`,
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
  customScatterOpts?: { xCol: string; yCol: string; colorCol: string; flipY: boolean; errorYCol: string | null; errorXCol: string | null },
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
      hovertemplate: "<b>%{text}</b><br>RA = %{x:.5f} deg<br>Dec = %{y:.5f} deg" + (hasZ ? "<br><i>z</i> = %{marker.color:.4f}" : "") + "<extra></extra>",
    }];
    if (showFit && ra.length >= 2) {
      const fit = linearFit(ra, dec);
      const x0 = arrMin(ra), x1 = arrMax(ra);
      traces.push({
        type: "scatter", mode: "lines",
        x: [x0, x1], y: [fit.slope * x0 + fit.intercept, fit.slope * x1 + fit.intercept],
        line: { color: COLORS.red, width: 2, dash: "dash" },
        name: `Linear: R2 = ${fit.r2.toFixed(4)}`, showlegend: true,
      });
    }
    const annotations: Record<string, unknown>[] = [];
    if (showStats) annotations.push(statsAnnotation(ra, "RA"));
    return {
      data: traces,
      layout: mkLayout(`Sky Distribution (<i>N</i> = ${ra.length})`,
        mkAxis("RA (deg)", { autorange: "reversed" }),
        mkAxis("Dec (deg)"),
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
        hovertemplate: "<b>%{text}</b><br>RA = %{x:.5f} deg<br>Dec = %{y:.5f} deg<br><i>z</i> = %{marker.color:.4f}<extra></extra>",
      }],
      layout: mkLayout(`Sky Position by Redshift (<i>N</i> = ${n})`,
        mkAxis("RA (deg)", { autorange: "reversed" }),
        mkAxis("Dec (deg)"),
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
      hovertemplate: "<b>%{text}</b><br>RA = %{x:.5f} deg<br><i>z</i> = %{y:.4f}<extra></extra>",
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
        mkAxis("RA (deg)"),
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
      hovertemplate: "<b>%{text}</b><br>Dec = %{x:.5f} deg<br><i>z</i> = %{y:.4f}<extra></extra>",
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
        mkAxis("Dec (deg)"),
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
        mkAxis("RA (deg)", { autorange: "reversed" }),
        mkAxis("Dec (deg)"),
        { hasColorbar: true }),
    };
  }

  // Case-insensitive column access: TAP services may return UPPERCASE column names
  const col = (name: string): unknown[] | undefined => (data[name] ?? data[name.toUpperCase()] ?? data[name.toLowerCase()]) as unknown[] | undefined;
  const hasColCI = (name: string): boolean => isNumericSeries(col(name));
  const numCol = (name: string): number[] => (col(name) ?? []) as number[];
  const findColCI = (candidates: string[]): string | undefined =>
    candidates.find((c) => isNumericSeries(col(c)));

  if (chartType === "hr_diagram") {
    // Auto-detect X: bp_rp directly, or compute from phot_bp_mean_mag - phot_rp_mean_mag
    let bpRp: number[] | null = null;
    if (hasColCI("bp_rp")) {
      bpRp = numCol("bp_rp");
    } else if (hasColCI("phot_bp_mean_mag") && hasColCI("phot_rp_mean_mag")) {
      const bp = numCol("phot_bp_mean_mag");
      const rp = numCol("phot_rp_mean_mag");
      bpRp = bp.map((v, i) => v - (rp[i] ?? 0));
    }
    // Auto-detect Y: abs_g_mag directly, or compute from phot_g_mean_mag + 5*log10(parallax/1000)+5
    let absG: number[] | null = null;
    if (hasColCI("abs_g_mag")) {
      absG = numCol("abs_g_mag");
    } else if (hasColCI("phot_g_mean_mag") && hasColCI("parallax")) {
      const gMag = numCol("phot_g_mean_mag");
      const plx = numCol("parallax");
      absG = gMag.map((g, i) => {
        const p = plx[i];
        if (!p || p <= 0) return NaN;
        return g + 5 * Math.log10(p / 1000) + 5;
      });
    }
    if (!bpRp || !absG) return { data: [], layout: mkLayout("Insufficient data for H-R Diagram", mkAxis(""), mkAxis("")) };
    // Filter out NaN pairs
    const valid: { x: number; y: number; teff: number | null }[] = [];
    const hasTeff = hasColCI("teff_gspphot");
    const teffArr = hasTeff ? numCol("teff_gspphot") : null;
    for (let i = 0; i < Math.min(bpRp.length, absG.length); i++) {
      if (Number.isFinite(bpRp[i]) && Number.isFinite(absG[i])) {
        valid.push({ x: bpRp[i], y: absG[i], teff: teffArr && i < teffArr.length && Number.isFinite(teffArr[i]) ? teffArr[i] : null });
      }
    }
    if (valid.length === 0) return { data: [], layout: mkLayout("No valid data for H-R Diagram", mkAxis(""), mkAxis("")) };
    const xVals = valid.map((v) => v.x);
    const yVals = valid.map((v) => v.y);
    const colorVals = teffArr ? valid.map((v) => v.teff) : null;
    const hasColorData = colorVals && colorVals.every((v) => v !== null);
    const traces: Record<string, unknown>[] = [{
      type: "scattergl", mode: "markers",
      x: xVals, y: yVals,
      marker: {
        size: 3, symbol: "circle",
        color: hasColorData ? colorVals : COLORS.blue,
        colorscale: hasColorData ? "Hot" : undefined,
        reversescale: hasColorData ? true : undefined,
        showscale: !!hasColorData,
        colorbar: hasColorData ? mkColorbar("T<sub>eff</sub> (K)") : undefined,
        line: { width: 0 },
        opacity: 0.7,
      },
      hovertemplate: "<b>B<sub>P</sub>−R<sub>P</sub></b> = %{x:.3f}<br><b>M<sub>G</sub></b> = %{y:.3f}" + (hasColorData ? "<br>T<sub>eff</sub> = %{marker.color:.0f} K" : "") + "<extra></extra>",
    }];
    const annotations: Record<string, unknown>[] = [{
      text: "<i>Main Sequence</i>",
      showarrow: true,
      arrowhead: 2,
      arrowsize: 1,
      arrowwidth: 1.5,
      arrowcolor: "rgba(0,0,0,0.3)",
      ax: 40, ay: -30,
      xref: "x", yref: "y",
      x: 1.5, y: 6,
      font: { family: FONT, size: 12, color: "rgba(0,0,0,0.5)" },
    }];
    if (showStats) annotations.push(statsAnnotation(yVals, "M_G"));
    return {
      data: traces,
      layout: mkLayout(`H-R Diagram (<i>N</i> = ${valid.length})`,
        mkAxis("B<sub>P</sub> − R<sub>P</sub> (mag)"),
        mkAxis("M<sub>G</sub> (mag)", { autorange: "reversed" }),
        { hasColorbar: !!hasColorData, annotations }),
    };
  }

  if (chartType === "lightcurve") {
    // Auto-detect time column (case-insensitive)
    const timeCandidates = ["hmjd", "mjd", "time", "jd"];
    const timeColName = findColCI(timeCandidates);
    // Auto-detect magnitude/flux column
    const magFluxCandidates = ["mag", "magnitude", "flux"];
    const magFluxCol = findColCI(magFluxCandidates);
    if (!timeColName || !magFluxCol) return { data: [], layout: mkLayout("Insufficient data for Light Curve", mkAxis(""), mkAxis("")) };
    const timeArr = numCol(timeColName);
    const magFluxArr = numCol(magFluxCol);
    const isMag = magFluxCol.toLowerCase().includes("mag");
    // Auto-detect error column
    const errCandidates = ["magerr", "mag_err", "mag_error", "flux_err"];
    const errColName = findColCI(errCandidates);
    const errArr = errColName ? numCol(errColName) : null;
    // Auto-detect band/filter column
    const bandCandidates = ["band", "filter", "filtercode"];
    const bandCol = bandCandidates.find((c) => { const v = col(c); return Array.isArray(v) && v.length > 0; });
    const bandColors: Record<string, string> = { g: "#2ca02c", r: "#d62728", i: "#ff7f0e", z: "#9467bd", u: "#1f77b4" };
    const defaultTraceColors = [COLORS.blue, COLORS.red, COLORS.green, COLORS.purple, COLORS.yellow, COLORS.cyan];
    const traces: Record<string, unknown>[] = [];
    if (bandCol) {
      const bandArr = (col(bandCol) ?? []) as string[];
      const bands = [...new Set(bandArr.map(String))];
      bands.forEach((band, bi) => {
        const indices: number[] = [];
        for (let i = 0; i < Math.min(timeArr.length, magFluxArr.length, bandArr.length); i++) {
          if (String(bandArr[i]) === band && Number.isFinite(timeArr[i]) && Number.isFinite(magFluxArr[i])) indices.push(i);
        }
        if (indices.length === 0) return;
        const color = bandColors[band.toLowerCase()] || defaultTraceColors[bi % defaultTraceColors.length];
        traces.push({
          type: "scatter", mode: "markers+lines",
          x: indices.map((i) => timeArr[i]),
          y: indices.map((i) => magFluxArr[i]),
          name: band,
          showlegend: true,
          marker: { size: 4, color, symbol: "circle" },
          line: { color, width: 1 },
          error_y: errArr ? {
            type: "data" as const,
            array: indices.map((i) => errArr[i] ?? 0),
            visible: true,
            color: "rgba(0,0,0,0.3)",
            thickness: 1,
            width: 2,
          } : undefined,
        });
      });
    } else {
      const n = Math.min(timeArr.length, magFluxArr.length);
      const validX: number[] = [], validY: number[] = [], validErr: number[] = [];
      for (let i = 0; i < n; i++) {
        if (Number.isFinite(timeArr[i]) && Number.isFinite(magFluxArr[i])) {
          validX.push(timeArr[i]);
          validY.push(magFluxArr[i]);
          if (errArr) validErr.push(errArr[i] ?? 0);
        }
      }
      traces.push({
        type: "scatter", mode: "markers+lines",
        x: validX, y: validY,
        marker: { size: 4, color: COLORS.blue, symbol: "circle" },
        line: { color: COLORS.blue, width: 1 },
        error_y: validErr.length > 0 ? {
          type: "data" as const,
          array: validErr,
          visible: true,
          color: "rgba(0,0,0,0.3)",
          thickness: 1,
          width: 2,
        } : undefined,
      });
    }
    const totalPts = traces.reduce((s, t) => s + ((t.x as number[])?.length ?? 0), 0);
    const yLabel = isMag ? "Magnitude" : "Flux";
    const annotations: Record<string, unknown>[] = [];
    if (showStats) {
      const allY = traces.flatMap((t) => (t.y as number[]) ?? []);
      annotations.push(statsAnnotation(allY, yLabel));
    }
    return {
      data: traces,
      layout: mkLayout(`Light Curve (<i>N</i> = ${totalPts})`,
        mkAxis("Time (MJD)"),
        mkAxis(yLabel, isMag ? { autorange: "reversed" } : undefined),
        { showlegend: !!bandCol, annotations, legend: { font: { family: FONT, size: 12 }, bgcolor: "rgba(255,255,255,0.8)", bordercolor: COLORS.grid, borderwidth: 1, x: 0.02, y: 0.98 } }),
    };
  }

  if (chartType === "spectrum") {
    // Auto-detect wavelength column (case-insensitive)
    const waveCandidates = ["wavelength", "wave", "lambda"];
    const waveColName = findColCI(waveCandidates);
    // Auto-detect flux column
    const fluxCandidates = ["flux", "flux_density", "counts"];
    const fluxColName = findColCI(fluxCandidates);
    if (!waveColName || !fluxColName) return { data: [], layout: mkLayout("Insufficient data for Spectrum", mkAxis(""), mkAxis("")) };
    const waveArr = numCol(waveColName);
    const fluxArr = numCol(fluxColName);
    const n = Math.min(waveArr.length, fluxArr.length);
    const validX: number[] = [], validY: number[] = [];
    for (let i = 0; i < n; i++) {
      if (Number.isFinite(waveArr[i]) && Number.isFinite(fluxArr[i])) {
        validX.push(waveArr[i]);
        validY.push(fluxArr[i]);
      }
    }
    if (validX.length === 0) return { data: [], layout: mkLayout("No valid spectrum data", mkAxis(""), mkAxis("")) };
    const traces: Record<string, unknown>[] = [{
      type: "scatter", mode: "lines",
      x: validX, y: validY,
      line: { color: COLORS.blue, width: 1.2 },
      hovertemplate: "Wavelength = %{x:.1f} A<br>Flux = %{y:.4g}<extra></extra>",
    }];
    // Spectral reference lines
    const spectralLines: { name: string; wave: number; color: string }[] = [
      { name: "Ly-a", wave: 1216, color: "rgba(100,100,200,0.5)" },
      { name: "Mg II", wave: 2800, color: "rgba(200,100,100,0.5)" },
      { name: "H-b", wave: 4861, color: "rgba(100,180,100,0.5)" },
      { name: "[OIII]", wave: 5007, color: "rgba(100,180,100,0.5)" },
      { name: "H-a", wave: 6563, color: "rgba(200,80,80,0.5)" },
      { name: "[NII]", wave: 6584, color: "rgba(200,150,80,0.5)" },
    ];
    const waveLo = arrMin(validX), waveHi = arrMax(validX);
    const shapes: Record<string, unknown>[] = [];
    const annotations: Record<string, unknown>[] = [];
    const fluxMax = arrMax(validY);
    for (const sl of spectralLines) {
      if (sl.wave >= waveLo && sl.wave <= waveHi) {
        shapes.push({
          type: "line",
          x0: sl.wave, x1: sl.wave,
          y0: 0, y1: 1,
          xref: "x", yref: "paper",
          line: { color: sl.color, width: 1.5, dash: "dash" },
        });
        annotations.push({
          text: sl.name,
          x: sl.wave, y: fluxMax * 0.95,
          xref: "x", yref: "y",
          showarrow: false,
          font: { family: FONT, size: 10, color: "rgba(0,0,0,0.6)" },
          textangle: -90,
          xanchor: "right",
        });
      }
    }
    if (showStats) annotations.push(statsAnnotation(validY, "Flux"));
    return {
      data: traces,
      layout: mkLayout(`Spectrum (<i>N</i> = ${validX.length} pts)`,
        mkAxis("Wavelength (A)"),
        mkAxis("Flux"),
        { shapes, annotations }),
    };
  }

  if (chartType === "scatter_custom" && customScatterOpts) {
    const { xCol, yCol, colorCol, flipY, errorYCol, errorXCol } = customScatterOpts;
    const xArr = (data[xCol] || []) as number[];
    const yArr = (data[yCol] || []) as number[];
    const n = Math.min(xArr.length, yArr.length);
    if (n === 0) return { data: [], layout: mkLayout("No data for selected columns", mkAxis(""), mkAxis("")) };
    const hasColor = colorCol && colorCol !== "" && data[colorCol];
    const colorArr = hasColor ? (data[colorCol] as number[]).slice(0, n) : undefined;
    const errorYArr = errorYCol && data[errorYCol] ? (data[errorYCol] as number[]).slice(0, n) : undefined;
    const errorXArr = errorXCol && data[errorXCol] ? (data[errorXCol] as number[]).slice(0, n) : undefined;
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
      error_y: errorYArr ? {
        type: "data" as const,
        array: errorYArr,
        visible: true,
        color: "rgba(0,0,0,0.3)",
        thickness: 1,
        width: 2,
      } : undefined,
      error_x: errorXArr ? {
        type: "data" as const,
        array: errorXArr,
        visible: true,
        color: "rgba(0,0,0,0.3)",
        thickness: 1,
        width: 2,
      } : undefined,
    }];
    if (showFit && n >= 2) {
      const fit = linearFit(xArr.slice(0, n), yArr.slice(0, n));
      const x0 = arrMin(xArr.slice(0, n)), x1 = arrMax(xArr.slice(0, n));
      traces.push({
        type: "scatter", mode: "lines",
        x: [x0, x1], y: [fit.slope * x0 + fit.intercept, fit.slope * x1 + fit.intercept],
        line: { color: COLORS.red, width: 2, dash: "dash" },
        name: `Linear: R2 = ${fit.r2.toFixed(4)}`, showlegend: true,
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
    (k) => isNumericSeries(data[k])
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
  hr_diagram: "H-R Diagram (Color-Magnitude)",
  lightcurve: "Light Curve",
  spectrum: "Spectrum",
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
      return isNumericSeries(initialData[k]);
    });
  }, [initialData]);
  const [customX, setCustomX] = useState("");
  const [customY, setCustomY] = useState("");
  const [customColor, setCustomColor] = useState("");
  const [flipY, setFlipY] = useState(false);
  const [errorYCol, setErrorYCol] = useState<string | null>(null);
  const [errorXCol, setErrorXCol] = useState<string | null>(null);

  useEffect(() => {
    if (numericColumns.length === 0) return;

    if (chartType === "hr_diagram") {
      const xCol = numericColumns.includes("bp_rp") ? "bp_rp" : numericColumns.includes("phot_bp_mean_mag") ? "phot_bp_mean_mag" : "";
      const yCol = numericColumns.includes("abs_g_mag") ? "abs_g_mag" : numericColumns.includes("phot_g_mean_mag") ? "phot_g_mean_mag" : "";
      if (xCol) setCustomX(xCol);
      if (yCol) setCustomY(yCol);
      setFlipY(true);
      if (numericColumns.includes("teff_gspphot")) setCustomColor("teff_gspphot");
      return;
    }

    if (chartType === "lightcurve") {
      const timeCol = ["hmjd", "mjd", "time", "jd"].find((c) => numericColumns.includes(c));
      const magFluxCol = ["mag", "magnitude", "flux"].find((c) => numericColumns.includes(c));
      if (timeCol) setCustomX(timeCol);
      if (magFluxCol) {
        setCustomY(magFluxCol);
        setFlipY(magFluxCol.toLowerCase().includes("mag"));
      }
      const errCol = ["magerr", "mag_err", "mag_error", "flux_err"].find((c) => numericColumns.includes(c));
      if (errCol) setErrorYCol(errCol);
      return;
    }

    if (chartType === "spectrum") {
      const waveCol = ["wavelength", "wave", "lambda"].find((c) => numericColumns.includes(c));
      const fluxCol = ["flux", "flux_density", "counts"].find((c) => numericColumns.includes(c));
      if (waveCol) setCustomX(waveCol);
      if (fluxCol) setCustomY(fluxCol);
      setFlipY(false);
      return;
    }

    if (chartType !== "scatter_custom") return;

    const preferredX = numericColumns.includes("bp_rp")
      ? "bp_rp"
      : numericColumns.includes("color")
        ? "color"
        : numericColumns[0];
    const preferredY = numericColumns.includes("abs_g_mag")
      ? "abs_g_mag"
      : numericColumns.includes("phot_g_mean_mag")
        ? "phot_g_mean_mag"
        : numericColumns.find((col) => col !== preferredX) || numericColumns[0];
    const preferredColor = numericColumns.includes("parallax")
      ? "parallax"
      : numericColumns.includes("redshift")
        ? "redshift"
        : "";

    if (!customX || !numericColumns.includes(customX)) setCustomX(preferredX);
    if (!customY || !numericColumns.includes(customY)) setCustomY(preferredY);
    if (preferredColor && (!customColor || !numericColumns.includes(customColor))) {
      setCustomColor(preferredColor);
    }
    if ((preferredX === "bp_rp" && preferredY === "abs_g_mag") || preferredY === "phot_g_mean_mag") {
      setFlipY(true);
    }
  }, [chartType, customColor, customX, customY, numericColumns]);

  const availableCharts = useMemo(() => {
    if (!initialData) return CHART_TYPES;
    const z = ((initialData.redshift || []) as unknown[]).filter((v) => v != null);
    const mag = ((initialData.magnitude || []) as unknown[]).filter((v) => v != null);
    const colCI = (c: string): unknown[] | undefined => (initialData[c] ?? initialData[c.toUpperCase()] ?? initialData[c.toLowerCase()]) as unknown[] | undefined;
    const hasCol = (c: string) => isNumericSeries(colCI(c));
    const hasAnyCol = (cols: string[]) => cols.some(hasCol);
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(CHART_TYPES)) {
      if ((k === "redshift_histogram" || k === "ra_dec_redshift" || k === "redshift_ra" || k === "redshift_dec") && z.length === 0) continue;
      if (k === "magnitude_histogram" && mag.length === 0) continue;
      if (k === "hr_diagram" && !(hasCol("bp_rp") || (hasCol("phot_bp_mean_mag") && hasCol("phot_rp_mean_mag")))) continue;
      if (k === "lightcurve" && !(hasAnyCol(["hmjd", "mjd", "time", "jd"]) && hasAnyCol(["mag", "magnitude", "flux"]))) continue;
      if (k === "spectrum" && !hasAnyCol(["wavelength", "wave", "lambda"])) continue;
      out[k] = v;
    }
    return out;
  }, [initialData]);

  const plotResult = useMemo(() => {
    if (!initialData) return null;
    const scatterOpts = chartType === "scatter_custom" && customX && customY
      ? { xCol: customX, yCol: customY, colorCol: customColor, flipY, errorYCol, errorXCol }
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
  }, [chartType, initialData, showFit, showStats, xMin, xMax, yMin, yMax, coordFormat, customX, customY, customColor, flipY, errorYCol, errorXCol]);

  const handleExportSVG = () => {
    const plotDiv = document.querySelector(".js-plotly-plot") as HTMLElement | null;
    if (plotDiv && window.Plotly) {
      window.Plotly.downloadImage(plotDiv, {
        format: "svg",
        width: 800,
        height: 600,
        filename: "astro_plot",
      });
    }
  };

  const handleExportPDF = () => {
    const plotDiv = document.querySelector(".js-plotly-plot") as HTMLElement | null;
    if (plotDiv && window.Plotly) {
      window.Plotly.downloadImage(plotDiv, {
        format: "pdf",
        width: 800,
        height: 600,
        filename: "astro_plot",
      });
    }
  };

  const handleExportPNG = () => {
    const plotDiv = document.querySelector(".js-plotly-plot") as HTMLElement | null;
    if (plotDiv && window.Plotly) {
      window.Plotly.downloadImage(plotDiv, {
        format: "png",
        width: 2400,
        height: 1800,
        filename: "astro_plot_hires",
        scale: 1,
      });
    }
  };

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
          <label
            className="fit-toggle"
            title={
              showFit
                ? "Linear fit shown for scatter / custom_scatter / mag_color / hr_diagram charts with N ≥ 2. Other chart types draw reference lines (Gaussian, PDF overlay) instead."
                : "Add a linear fit (scatter) or Gaussian overlay (histogram)."
            }
          >
            <input type="checkbox" checked={showFit} onChange={(e) => setShowFit(e.target.checked)} />
            Fit
            {showFit && (
              <span
                style={{
                  marginLeft: 4,
                  fontSize: "0.72em",
                  color: /scatter|hr_diagram|mag_color|custom/i.test(chartType)
                    ? "var(--color-green)"
                    : /hist|pdf/i.test(chartType)
                      ? "var(--color-green)"
                      : "var(--color-red)",
                }}
              >
                {/scatter|hr_diagram|mag_color|custom|hist|pdf/i.test(chartType)
                  ? "✓"
                  : "(not supported for this chart type)"}
              </span>
            )}
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
          <label className="plot-range-label">
            Y Error{" "}
            <select value={errorYCol ?? ""} onChange={(e) => setErrorYCol(e.target.value || null)} className="plot-range-input" style={{ width: "auto", minWidth: 120 }}>
              <option value="">None</option>
              {numericColumns.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label className="plot-range-label">
            X Error{" "}
            <select value={errorXCol ?? ""} onChange={(e) => setErrorXCol(e.target.value || null)} className="plot-range-input" style={{ width: "auto", minWidth: 120 }}>
              <option value="">None</option>
              {numericColumns.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
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

      <div className="plot-export-toolbar" style={{ display: "flex", gap: 8, marginTop: 8, justifyContent: "flex-end" }}>
        <button className="btn-secondary btn-small" onClick={handleExportSVG}>Export SVG</button>
        <button className="btn-secondary btn-small" onClick={handleExportPDF}>Export PDF</button>
        <button className="btn-secondary btn-small" onClick={handleExportPNG}>Export Hi-Res PNG</button>
      </div>
    </div>
  );
}
