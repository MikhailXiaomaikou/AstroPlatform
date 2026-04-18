import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";

// Journal-edition palette — muted editorial tones, grouped by node family.
const TYPE_COLORS: Record<string, string> = {
  // Ingest — deep blue
  QueryData:        "#2a5d7b",
  ImportWorkspace:  "#2a5d7b",
  LoadData:         "#2a5d7b",
  // Transform — ochre
  Denoise:          "#a06500",
  CoordTransform:   "#a06500",
  EquivalentWidth:  "#a06500",
  // Analyse — burgundy (accent)
  SpectralFit:      "#7b2d26",
  RedshiftEstimate: "#7b2d26",
  SEDFit:           "#7b2d26",
  BayesianFit:      "#7b2d26",
  TransitFit:       "#7b2d26",
  GPDetrend:        "#7b2d26",
  PhotoZPro:        "#7b2d26",
  // Photometry / calibration — plum
  PhotCalibrate:    "#6b4a7e",
  FluxCalibrate:    "#6b4a7e",
  TelluricCorrect:  "#6b4a7e",
  PSFPhotometry:    "#6b4a7e",
  // Cross / merge — forest green
  CrossMatch:       "#2e6a4e",
  ImageStack:       "#2e6a4e",
  SpectraStack:     "#2e6a4e",
  Reproject:        "#2e6a4e",
  Mosaic:           "#2e6a4e",
  PSFMatch:         "#2e6a4e",
  Deblend:          "#2e6a4e",
  // Output / plot — ink
  Plot:             "#1a1a1a",
  InteractivePlot:  "#1a1a1a",
  // Control flow — warn ochre
  Condition:        "#a06500",
};

const SOURCE_NODE_TYPES = new Set(["QueryData", "ImportWorkspace", "LoadData"]);
const TERMINAL_NODE_TYPES = new Set(["Plot"]);

const PROGRESS_COLORS: Record<string, string> = {
  pending:   "rgba(26, 26, 26, 0.08)",
  running:   "#a06500",
  completed: "#2e6a4e",
  error:     "#b23a2e",
};

interface PipelineNodeData {
  label: string;
  nodeType: string;
  params?: Record<string, unknown>;
  progress?: string;
  progressError?: string;
}

function PipelineNode({ data, selected }: NodeProps<PipelineNodeData>) {
  const color = TYPE_COLORS[data.nodeType] || "#8E8E93";
  const progressColor = data.progress ? PROGRESS_COLORS[data.progress] : null;

  return (
    <div
      className={`pipeline-node${selected ? " selected" : ""}${data.progress ? ` node-${data.progress}` : ""}`}
      style={{ borderColor: color }}
    >
      {progressColor && (
        <div
          className="node-progress-indicator"
          style={{ backgroundColor: progressColor }}
        />
      )}
      <div className="pipeline-node-header" style={{ background: color }}>
        {data.label}
        {data.progress === "running" && (
          <span className="spinner spinner-node" />
        )}
      </div>
      <div className="pipeline-node-body">
        <span className="pipeline-node-type">{data.nodeType}</span>
        {data.params && Object.keys(data.params).length > 0 && (
          data.nodeType === "QueryData" ? (
            <div className="pipeline-node-summary">
              {data.params.query ? <span className="param-summary-target">{String(data.params.query)}</span> : null}
              {data.params.sources ? (
                <span className="param-summary-sources">
                  {String(data.params.sources).split(",").map((s) => s.trim().toUpperCase()).join("+")}
                </span>
              ) : null}
              {data.params.radius != null ? <span className="param-summary-detail">{String(data.params.radius)}&deg;</span> : null}
            </div>
          ) : data.nodeType === "ImportWorkspace" ? (
            <div className="pipeline-node-summary">
              <span className="param-summary-target">{String(data.params.path ?? "").split("/").pop() || "No file"}</span>
            </div>
          ) : data.nodeType === "Denoise" ? (
            <div className="pipeline-node-summary">
              <span className="param-summary-target">{String(data.params.method ?? "sigma_clip")}</span>
              {data.params.sigma != null && String(data.params.method ?? "sigma_clip") === "sigma_clip" ? <span className="param-summary-detail">{String(data.params.sigma)}σ</span> : null}
              {data.params.window_length != null && String(data.params.method) === "savgol" ? <span className="param-summary-detail">w={String(data.params.window_length)}</span> : null}
              {data.params.wavelet != null && String(data.params.method) === "wavelet" ? <span className="param-summary-detail">{String(data.params.wavelet)}</span> : null}
            </div>
          ) : (
            <ul className="pipeline-node-params">
              {Object.entries(data.params).map(([k, v]) => (
                <li key={k}>
                  <span className="param-key">{k}:</span> {String(v)}
                </li>
              ))}
            </ul>
          )
        )}
        {data.progressError && (
          <div className="node-error-msg">{data.progressError}</div>
        )}
      </div>
      {!SOURCE_NODE_TYPES.has(data.nodeType) && (
        <Handle type="target" position={Position.Left} className="pipeline-handle pipeline-handle-target" />
      )}
      {data.nodeType === "Condition" ? (
        <>
          <Handle
            type="source"
            position={Position.Right}
            id="true"
            className="pipeline-handle pipeline-handle-source"
            style={{ top: "35%", background: "#30D158" }}
            title="True branch"
          />
          <span className="condition-handle-label condition-handle-true" style={{ position: "absolute", right: -18, top: "28%", fontSize: "0.6rem", color: "#30D158", fontWeight: 700 }}>T</span>
          <Handle
            type="source"
            position={Position.Right}
            id="false"
            className="pipeline-handle pipeline-handle-source"
            style={{ top: "65%", background: "#FF453A" }}
            title="False branch"
          />
          <span className="condition-handle-label condition-handle-false" style={{ position: "absolute", right: -18, top: "58%", fontSize: "0.6rem", color: "#FF453A", fontWeight: 700 }}>F</span>
        </>
      ) : (
        <>
          {!TERMINAL_NODE_TYPES.has(data.nodeType) && (
            <Handle type="source" position={Position.Right} className="pipeline-handle pipeline-handle-source" />
          )}
          {TERMINAL_NODE_TYPES.has(data.nodeType) && (
            <Handle type="source" position={Position.Right} className="pipeline-handle" style={{ opacity: 0.3 }} />
          )}
        </>
      )}
      {SOURCE_NODE_TYPES.has(data.nodeType) && (
        <Handle type="target" position={Position.Left} className="pipeline-handle" style={{ opacity: 0.3 }} />
      )}
    </div>
  );
}

export default memo(PipelineNode);
