import type { NodeType } from "../../api/client";

interface Props {
  nodeTypes: NodeType[];
}

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

export default function NodePalette({ nodeTypes }: Props) {
  const onDragStart = (e: React.DragEvent, nodeType: NodeType) => {
    e.dataTransfer.setData("application/reactflow-type", nodeType.type);
    e.dataTransfer.setData("application/reactflow-label", nodeType.label);
    e.dataTransfer.effectAllowed = "move";
  };

  return (
    <div className="node-palette">
      <h3>Nodes</h3>
      {nodeTypes.map((nt) => (
        <div
          key={nt.type}
          className="palette-item"
          draggable
          onDragStart={(e) => onDragStart(e, nt)}
          style={{ borderLeftColor: TYPE_COLORS[nt.type] || "#64748b" }}
        >
          <strong>{nt.label}</strong>
          <span className="palette-desc">{nt.description}</span>
        </div>
      ))}
    </div>
  );
}
