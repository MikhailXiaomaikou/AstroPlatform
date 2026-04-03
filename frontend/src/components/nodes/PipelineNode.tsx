import { memo } from "react";
import { Handle, Position, type NodeProps } from "reactflow";

const TYPE_COLORS: Record<string, string> = {
  LoadData: "#0A84FF",
  Denoise: "#BF5AF2",
  SpectralFit: "#64D2FF",
  CoordTransform: "#FF9F0A",
  Plot: "#30D158",
  RedshiftEstimate: "#FF6961",
  EquivalentWidth: "#FFD60A",
  SEDFit: "#AC8E68",
  CrossMatch: "#5AC8FA",
  PhotCalibrate: "#FF9500",
  ImageStack: "#AF52DE",
  InteractivePlot: "#8b5cf6",
};

const PROGRESS_COLORS: Record<string, string> = {
  pending: "rgba(255,255,255,0.1)",
  running: "#FF9F0A",
  completed: "#30D158",
  error: "#FF453A",
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
          <ul className="pipeline-node-params">
            {Object.entries(data.params).map(([k, v]) => (
              <li key={k}>
                <span className="param-key">{k}:</span> {String(v)}
              </li>
            ))}
          </ul>
        )}
        {data.progressError && (
          <div className="node-error-msg">{data.progressError}</div>
        )}
      </div>
      {data.nodeType !== "LoadData" && (
        <Handle type="target" position={Position.Left} className="pipeline-handle pipeline-handle-target" />
      )}
      {data.nodeType !== "Plot" && (
        <Handle type="source" position={Position.Right} className="pipeline-handle pipeline-handle-source" />
      )}
      {data.nodeType === "Plot" && (
        <Handle type="source" position={Position.Right} className="pipeline-handle" style={{ opacity: 0.3 }} />
      )}
      {data.nodeType === "LoadData" && (
        <Handle type="target" position={Position.Left} className="pipeline-handle" style={{ opacity: 0.3 }} />
      )}
    </div>
  );
}

export default memo(PipelineNode);
