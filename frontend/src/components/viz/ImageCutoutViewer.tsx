import { useState, useMemo } from "react";
import Plot from "react-plotly.js";

/* eslint-disable @typescript-eslint/no-explicit-any */
type PlotData = Record<string, any>;
type PlotLayout = Record<string, any>;

interface ImageCutoutViewerProps {
  imageData?: number[][];
  title?: string;
  ra?: number;
  dec?: number;
  sources?: { x: number; y: number; id: number }[];
}

export default function ImageCutoutViewer({ imageData, title, ra, dec, sources }: ImageCutoutViewerProps) {
  const [colormap, setColormap] = useState("Greys_r");
  const [stretch, setStretch] = useState("linear");

  const processedData = useMemo(() => {
    if (!imageData) return null;
    let data = imageData.map(row => [...row]);
    if (stretch === "log") {
      data = data.map(row => row.map(v => v > 0 ? Math.log10(v) : 0));
    } else if (stretch === "sqrt") {
      data = data.map(row => row.map(v => v > 0 ? Math.sqrt(v) : 0));
    }
    return data;
  }, [imageData, stretch]);

  if (!processedData) return <div style={{ padding: 16, color: "var(--color-text-secondary)" }}>No image data</div>;

  const traces: PlotData[] = [{
    z: processedData, type: "heatmap", colorscale: colormap === "Greys_r" ? "Greys" : colormap,
    reversescale: colormap === "Greys_r",
  }];

  if (sources) {
    traces.push({
      x: sources.map(s => s.x), y: sources.map(s => s.y),
      type: "scatter", mode: "markers",
      marker: { color: "transparent", size: 12, line: { color: "#ef4444", width: 2 } },
      name: "Sources", text: sources.map(s => `ID: ${s.id}`),
    });
  }

  const layout: PlotLayout = {
    title: title || `Cutout${ra ? ` (${ra.toFixed(4)}, ${dec?.toFixed(4)})` : ""}`,
    font: { family: "STIX Two Text, serif" },
    paper_bgcolor: "#fafaf8", margin: { l: 50, r: 30, t: 40, b: 40 },
    height: 400, yaxis: { scaleanchor: "x" },
  };

  return (
    <div className="image-cutout-viewer">
      <Plot data={traces} layout={layout} config={{ responsive: true }} style={{ width: "100%" }} />
      <div style={{ display: "flex", gap: 12, padding: "8px 0", fontSize: "0.85rem" }}>
        <select value={colormap} onChange={e => setColormap(e.target.value)}>
          <option value="Greys_r">Greyscale</option>
          <option value="Viridis">Viridis</option>
          <option value="Hot">Heat</option>
          <option value="Inferno">Inferno</option>
        </select>
        <select value={stretch} onChange={e => setStretch(e.target.value)}>
          <option value="linear">Linear</option>
          <option value="log">Log</option>
          <option value="sqrt">Sqrt</option>
        </select>
      </div>
    </div>
  );
}
