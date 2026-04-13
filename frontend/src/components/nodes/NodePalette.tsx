import type { NodeType } from "../../api/client";

interface Props {
  nodeTypes: NodeType[];
}

const TYPE_COLORS: Record<string, string> = {
  QueryData: "#2563eb",
  ImportWorkspace: "#3b82f6",
  LoadData: "#0A84FF",
  BiasSubtract: "#1d4ed8",
  DarkCorrect: "#2563eb",
  FlatField: "#3b82f6",
  CosmicRayReject: "#0ea5e9",
  AstrometricSolve: "#38bdf8",
  SourceExtract: "#60a5fa",
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

const TYPE_ORDER = [
  "QueryData",
  "ImportWorkspace",
  "LoadData",
  "BiasSubtract",
  "DarkCorrect",
  "FlatField",
  "CosmicRayReject",
  "AstrometricSolve",
  "SourceExtract",
];

export default function NodePalette({ nodeTypes }: Props) {
  const onDragStart = (e: React.DragEvent, nodeType: NodeType) => {
    e.dataTransfer.setData("application/reactflow-type", nodeType.type);
    e.dataTransfer.setData("application/reactflow-label", nodeType.label);
    e.dataTransfer.effectAllowed = "move";
  };

  return (
    <div className="node-palette">
      <h3>Nodes</h3>
      {[...nodeTypes].sort((a, b) => {
        const ai = TYPE_ORDER.indexOf(a.type);
        const bi = TYPE_ORDER.indexOf(b.type);
        if (ai === -1 && bi === -1) return a.label.localeCompare(b.label);
        if (ai === -1) return 1;
        if (bi === -1) return -1;
        return ai - bi;
      }).map((nt) => (
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
