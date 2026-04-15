import { useState, useMemo } from "react";
import Plot from "react-plotly.js";

/* eslint-disable @typescript-eslint/no-explicit-any */
type PlotData = Record<string, any>;
type PlotLayout = Record<string, any>;

interface LightCurveViewerProps {
  time: number[];
  flux: number[];
  fluxErr?: number[];
  modelFlux?: number[];
  trend?: number[];
  flares?: { start_time: number; end_time: number; peak_time: number }[];
  period?: number;
  title?: string;
}

export default function LightCurveViewer({ time, flux, fluxErr: _fluxErr, modelFlux, trend, flares, period, title }: LightCurveViewerProps) {
  const [phaseFold, setPhaseFold] = useState(false);
  const [showTrend, setShowTrend] = useState(!!trend);
  const [customPeriod, setCustomPeriod] = useState(period || 1.0);

  const xData = useMemo(() => {
    if (!phaseFold || !customPeriod) return time;
    return time.map(t => ((t - time[0]) / customPeriod) % 1.0);
  }, [time, phaseFold, customPeriod]);

  const traces = useMemo<PlotData[]>(() => {
    // D1: auto-upgrade to WebGL backend when N > 5000 so Kepler / TESS
    // light curves with 60k+ cadences render at 30+ FPS instead of
    // locking the browser. Overlays (trend/model) stay on SVG because
    // they're already short line plots.
    const pointType = flux.length > 5000 ? "scattergl" : "scatter";
    const t: PlotData[] = [{
      x: xData, y: flux, type: pointType, mode: "markers",
      name: "Flux", marker: { color: "#2563eb", size: 3 },
    }];
    if (showTrend && trend) {
      t.push({ x: xData, y: trend, type: "scatter", mode: "lines",
        name: "Trend", line: { color: "#f59e0b", width: 2 } });
    }
    if (modelFlux) {
      t.push({ x: xData, y: modelFlux, type: "scatter", mode: "lines",
        name: "Model", line: { color: "#ef4444", width: 2 } });
    }
    return t;
  }, [xData, flux, trend, modelFlux, showTrend]);

  const flareShapes = useMemo(() => {
    if (!flares) return [];
    return flares.map(f => ({
      type: "rect" as const,
      x0: phaseFold ? ((f.start_time - time[0]) / customPeriod) % 1 : f.start_time,
      x1: phaseFold ? ((f.end_time - time[0]) / customPeriod) % 1 : f.end_time,
      y0: 0, y1: 1, yref: "paper" as const,
      fillcolor: "rgba(239,68,68,0.15)", line: { width: 0 },
    }));
  }, [flares, phaseFold, time, customPeriod]);

  const layout: PlotLayout = {
    title: title || "Light Curve", font: { family: "STIX Two Text, serif" },
    xaxis: { title: phaseFold ? "Phase" : "Time" },
    yaxis: { title: "Flux" },
    paper_bgcolor: "#fafaf8", plot_bgcolor: "#fafaf8",
    margin: { l: 70, r: 30, t: 40, b: 50 },
    shapes: flareShapes, height: 320,
  };

  return (
    <div className="lightcurve-viewer">
      <Plot data={traces} layout={layout} config={{ responsive: true }} style={{ width: "100%" }} />
      <div style={{ display: "flex", gap: 16, alignItems: "center", padding: "8px 0", fontSize: "0.85rem" }}>
        <label>
          <input type="checkbox" checked={phaseFold} onChange={e => setPhaseFold(e.target.checked)} />
          {" "}Phase fold
        </label>
        {phaseFold && (
          <label>
            P = <input type="number" value={customPeriod} step="0.001" style={{ width: 80 }}
              onChange={e => setCustomPeriod(parseFloat(e.target.value) || 1)} /> d
          </label>
        )}
        {trend && (
          <label>
            <input type="checkbox" checked={showTrend} onChange={e => setShowTrend(e.target.checked)} />
            {" "}Show trend
          </label>
        )}
        {flares && <span>{flares.length} flare(s) detected</span>}
      </div>
    </div>
  );
}
