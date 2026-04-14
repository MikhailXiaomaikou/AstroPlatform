import { useState, useMemo } from "react";
import Plot from "react-plotly.js";

/* eslint-disable @typescript-eslint/no-explicit-any */
type PlotData = Record<string, any>;
type PlotLayout = Record<string, any>;

const SPECTRAL_LINES = [
  { name: "Ly-\u03B1", wave: 1215.67 }, { name: "C IV", wave: 1549.06 },
  { name: "Mg II", wave: 2798.75 }, { name: "[O II]", wave: 3727.0 },
  { name: "H-\u03B2", wave: 4861.33 }, { name: "[O III]", wave: 4959.0 },
  { name: "[O III]", wave: 5007.0 }, { name: "H-\u03B1", wave: 6562.79 },
  { name: "[N II]", wave: 6584.0 }, { name: "[S II]", wave: 6717.0 },
  { name: "Ca K", wave: 3933.66 }, { name: "Ca H", wave: 3968.47 },
  { name: "Na D", wave: 5893.0 }, { name: "Ca triplet", wave: 8542.0 },
];

interface SpectrumViewerProps {
  wavelength: number[];
  flux: number[];
  fluxErr?: number[];
  lines?: { observed_wavelength: number; identification?: string; line_type?: string }[];
  redshift?: number;
  title?: string;
}

export default function SpectrumViewer({ wavelength, flux, fluxErr, lines, redshift: initZ = 0, title }: SpectrumViewerProps) {
  const [z, setZ] = useState(initZ);
  const [showLines, setShowLines] = useState(true);

  const traces = useMemo<PlotData[]>(() => {
    const t: PlotData[] = [{
      x: wavelength, y: flux, type: "scatter", mode: "lines",
      name: "Flux", line: { color: "#2563eb", width: 1 },
    }];
    if (fluxErr && fluxErr.length === flux.length) {
      const upper = flux.map((f, i) => f + fluxErr[i]);
      const lower = flux.map((f, i) => f - fluxErr[i]);
      t.push({
        x: [...wavelength, ...wavelength.slice().reverse()],
        y: [...upper, ...lower.reverse()],
        fill: "toself", fillcolor: "rgba(37,99,235,0.1)",
        line: { color: "transparent" }, name: "1\u03C3 error", showlegend: false,
        type: "scatter",
      });
    }
    return t;
  }, [wavelength, flux, fluxErr]);

  const lineShapes = useMemo(() => {
    if (!showLines) return [];
    return SPECTRAL_LINES.map(l => ({
      type: "line" as const, x0: l.wave * (1 + z), x1: l.wave * (1 + z),
      y0: 0, y1: 1, yref: "paper" as const,
      line: { color: "rgba(239,68,68,0.4)", width: 1, dash: "dot" as const },
    }));
  }, [z, showLines]);

  const lineAnnotations = useMemo(() => {
    if (!showLines) return [];
    return SPECTRAL_LINES.filter(l => {
      const obs = l.wave * (1 + z);
      return obs > (wavelength[0] ?? 0) && obs < (wavelength[wavelength.length - 1] ?? 10000);
    }).map(l => ({
      x: l.wave * (1 + z), y: 1, yref: "paper" as const,
      text: l.name, showarrow: false, font: { size: 9, color: "#ef4444" },
      textangle: -90, yanchor: "bottom" as const,
    }));
  }, [z, showLines, wavelength]);

  const layout: PlotLayout = {
    title: title || "Spectrum", font: { family: "STIX Two Text, serif" },
    xaxis: { title: "Wavelength (\u00C5)" }, yaxis: { title: "Flux" },
    paper_bgcolor: "#fafaf8", plot_bgcolor: "#fafaf8",
    margin: { l: 70, r: 30, t: 40, b: 50 },
    shapes: lineShapes, annotations: lineAnnotations,
    height: 350, showlegend: false,
  };

  return (
    <div className="spectrum-viewer">
      <Plot data={traces} layout={layout} config={{ responsive: true }} style={{ width: "100%" }} />
      <div style={{ display: "flex", gap: 16, alignItems: "center", padding: "8px 0", fontSize: "0.85rem" }}>
        <label>
          z = <input type="range" min="0" max="5" step="0.001" value={z}
            onChange={e => setZ(parseFloat(e.target.value))}
            style={{ width: 200, verticalAlign: "middle" }} />
          <span style={{ minWidth: 50, display: "inline-block" }}>{z.toFixed(3)}</span>
        </label>
        <label>
          <input type="checkbox" checked={showLines} onChange={e => setShowLines(e.target.checked)} />
          {" "}Show lines
        </label>
      </div>
      {lines && lines.length > 0 && (
        <details style={{ fontSize: "0.8rem", marginTop: 4 }}>
          <summary>{lines.length} line(s) identified</summary>
          <table className="results-table" style={{ fontSize: "0.75rem" }}>
            <thead><tr><th>\u03BB_obs (\u00C5)</th><th>ID</th><th>Element</th><th>Type</th></tr></thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={i}>
                  <td>{l.observed_wavelength?.toFixed(1)}</td>
                  <td>{l.identification || "?"}</td>
                  <td>{(l as Record<string, unknown>).element as string || ""}</td>
                  <td>{l.line_type || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}
