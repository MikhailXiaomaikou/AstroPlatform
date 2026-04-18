import type { NodeType } from "../../api/client";
import { useI18n } from "../../i18n";

interface Props {
  nodeTypes: NodeType[];
}

// Journal-edition palette — muted editorial tones, grouped by node family.
// Kept in sync with components/nodes/PipelineNode.tsx.
const TYPE_COLORS: Record<string, string> = {
  // Ingest — deep blue
  QueryData:        "#2a5d7b",
  ImportWorkspace:  "#2a5d7b",
  LoadData:         "#2a5d7b",
  // Reduction — deep blue (still ingest-adjacent)
  BiasSubtract:     "#2a5d7b",
  DarkCorrect:      "#2a5d7b",
  FlatField:        "#2a5d7b",
  CosmicRayReject:  "#2a5d7b",
  AstrometricSolve: "#2a5d7b",
  SourceExtract:    "#2a5d7b",
  // Transform — ochre
  Denoise:          "#a06500",
  CoordTransform:   "#a06500",
  EquivalentWidth:  "#a06500",
  // Analyse — burgundy
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
  FluxCalibrate: "pipeline.node_flux_calibrate",
  TelluricCorrect: "pipeline.node_telluric_correct",
  SpectraStack: "pipeline.node_spectra_stack",
  PhotoZPro: "pipeline.node_photoz_pro",
  BayesianFit: "pipeline.node_bayesian_fit",
  TransitFit: "pipeline.node_transit_fit",
  GPDetrend: "pipeline.node_gp_detrend",
  Reproject: "pipeline.node_reproject",
  Mosaic: "pipeline.node_mosaic",
  PSFMatch: "pipeline.node_psf_match",
  Deblend: "pipeline.node_deblend",
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
