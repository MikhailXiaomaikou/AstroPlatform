import type { NodeType } from "../../api/client";
import { useI18n } from "../../i18n";

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

const NODE_TYPE_I18N_KEY: Record<string, string> = {
  QueryData: "pipeline.node_query_data",
  ImportWorkspace: "pipeline.node_import_workspace",
  LoadData: "pipeline.node_load_data",
  BiasSubtract: "pipeline.node_bias_subtract",
  DarkCorrect: "pipeline.node_dark_correct",
  FlatField: "pipeline.node_flat_field",
  CosmicRayReject: "pipeline.node_cosmic_ray_reject",
  AstrometricSolve: "pipeline.node_astrometric_solve",
  SourceExtract: "pipeline.node_source_extract",
  Denoise: "pipeline.node_denoise",
  SpectralFit: "pipeline.node_spectral_fit",
  CoordTransform: "pipeline.node_coord_transform",
  Plot: "pipeline.node_plot",
  RedshiftEstimate: "pipeline.node_redshift_estimate",
  EquivalentWidth: "pipeline.node_equivalent_width",
  SEDFit: "pipeline.node_sed_fit",
  CrossMatch: "pipeline.node_cross_match",
  PhotCalibrate: "pipeline.node_phot_calibrate",
  ImageStack: "pipeline.node_image_stack",
  InteractivePlot: "pipeline.node_interactive_plot",
};

export default function NodePalette({ nodeTypes }: Props) {
  const { t } = useI18n();

  const onDragStart = (e: React.DragEvent, nodeType: NodeType) => {
    e.dataTransfer.setData("application/reactflow-type", nodeType.type);
    e.dataTransfer.setData("application/reactflow-label", nodeType.label);
    e.dataTransfer.effectAllowed = "move";
  };

  return (
    <div className="node-palette">
      <h3>{t("pipeline.nodes_title")}</h3>
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
          <strong>{NODE_TYPE_I18N_KEY[nt.type] ? t(NODE_TYPE_I18N_KEY[nt.type]) : nt.label}</strong>
          <span className="palette-desc">{nt.description}</span>
        </div>
      ))}
    </div>
  );
}
