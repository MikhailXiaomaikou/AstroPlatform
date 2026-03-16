import { useEffect, useState } from "react";
import Plot from "react-plotly.js";
import { exportViz, generateViz, getVizTemplates, type ChartTemplate } from "../../api/client";

interface Props {
  initialData?: Record<string, unknown>;
  initialChartType?: string;
  onClose?: () => void;
}

export default function PlotBuilder({ initialData, initialChartType, onClose }: Props) {
  const [templates, setTemplates] = useState<Record<string, ChartTemplate>>({});
  const [chartType, setChartType] = useState(initialChartType || "spectrum_overlay");
  const [plotJson, setPlotJson] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getVizTemplates().then(setTemplates).catch(() => {});
  }, []);

  useEffect(() => {
    if (initialData && chartType) {
      handleGenerate();
    }
  }, [chartType]);  // regenerate when chart type changes

  const handleGenerate = async () => {
    if (!initialData) return;
    setLoading(true);
    setError(null);
    try {
      const result = await generateViz(chartType, initialData);
      setPlotJson(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to generate plot");
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async (format: "png" | "pdf") => {
    if (!plotJson) return;
    try {
      const blob = await exportViz(plotJson, format);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `astro_plot.${format}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      // fallback: use plotly's built-in download
    }
  };

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
            {Object.entries(templates).map(([key, tpl]) => (
              <option key={key} value={key}>{tpl.name}</option>
            ))}
          </select>
          <button className="btn-secondary btn-small" onClick={() => handleExport("png")}>PNG</button>
          <button className="btn-secondary btn-small" onClick={() => handleExport("pdf")}>PDF</button>
          {onClose && (
            <button className="btn-secondary btn-small" onClick={onClose}>Close</button>
          )}
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {loading ? (
        <div className="fits-loading"><span className="spinner spinner-blue" /> Generating plot...</div>
      ) : plotJson ? (
        <Plot
          data={plotJson.data || []}
          layout={{
            ...(plotJson.layout || {}),
            autosize: true,
          }}
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
        <div className="plot-empty">Select a chart type and generate to see the visualization</div>
      )}
    </div>
  );
}
