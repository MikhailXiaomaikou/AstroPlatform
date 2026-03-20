import { useEffect, useState } from "react";

/* ── param schema per node type ── */

interface ParamDef {
  key: string;
  label: string;
  type: "text" | "number" | "select";
  default?: string | number;
  options?: string[]; // for select
  required?: boolean;
}

const NODE_PARAM_DEFS: Record<string, ParamDef[]> = {
  LoadData: [
    { key: "fits_path", label: "FITS Path", type: "text" },
  ],
  Denoise: [
    { key: "sigma", label: "Sigma", type: "number", default: 3.0 },
    { key: "max_iters", label: "Max Iterations", type: "number", default: 5 },
    { key: "flux_key", label: "Flux Key", type: "text", default: "flux" },
  ],
  SpectralFit: [
    { key: "model", label: "Model", type: "select", options: ["gaussian", "lorentzian"], default: "gaussian" },
    { key: "x_key", label: "X Key", type: "text", default: "index" },
    { key: "y_key", label: "Y Key", type: "text", default: "flux" },
  ],
  CoordTransform: [
    { key: "from_frame", label: "From Frame", type: "select", options: ["icrs", "galactic", "fk5", "fk4"], default: "icrs" },
    { key: "to_frame", label: "To Frame", type: "select", options: ["icrs", "galactic", "fk5", "fk4"], default: "galactic" },
    { key: "ra_key", label: "RA Key", type: "text", default: "ra" },
    { key: "dec_key", label: "Dec Key", type: "text", default: "dec" },
  ],
  Plot: [
    { key: "plot_type", label: "Plot Type", type: "select", options: ["spectrum", "image", "scatter"], default: "spectrum" },
    { key: "x_key", label: "X Key", type: "text" },
    { key: "y_key", label: "Y Key", type: "text" },
    { key: "title", label: "Title", type: "text" },
    { key: "x_label", label: "X Label", type: "text" },
    { key: "y_label", label: "Y Label", type: "text" },
  ],
  RedshiftEstimate: [
    { key: "flux_key", label: "Flux Key", type: "text", default: "flux" },
    { key: "wavelength_key", label: "Wavelength Key", type: "text", default: "wavelength" },
    { key: "method", label: "Method", type: "select", options: ["peak", "xcorr"], default: "peak" },
  ],
  EquivalentWidth: [
    { key: "flux_key", label: "Flux Key", type: "text", default: "flux" },
    { key: "wavelength_key", label: "Wavelength Key", type: "text", default: "wavelength" },
    { key: "line_center", label: "Line Center", type: "number" },
  ],
  SEDFit: [
    { key: "model", label: "Model", type: "select", options: ["blackbody", "powerlaw", "modified_blackbody"], default: "blackbody" },
    { key: "flux_key", label: "Flux Key", type: "text" },
    { key: "wavelength_key", label: "Wavelength Key", type: "text" },
  ],
  CrossMatch: [
    { key: "ra_key_1", label: "RA Key 1", type: "text" },
    { key: "dec_key_1", label: "Dec Key 1", type: "text" },
    { key: "ra_key_2", label: "RA Key 2", type: "text" },
    { key: "dec_key_2", label: "Dec Key 2", type: "text" },
    { key: "max_sep", label: "Max Separation (arcsec)", type: "number", default: 3.0 },
  ],
  PhotCalibrate: [
    { key: "zeropoint", label: "Zeropoint", type: "number", required: true },
    { key: "extinction_coeff", label: "Extinction Coeff", type: "number", default: 0.0 },
    { key: "airmass", label: "Airmass", type: "number", default: 1.0 },
    { key: "flux_key", label: "Flux Key", type: "text" },
  ],
  ImageStack: [
    { key: "method", label: "Method", type: "select", options: ["mean", "median", "sum", "sigma_clip"], default: "mean" },
    { key: "sigma", label: "Sigma", type: "number", default: 3.0 },
  ],
  InteractivePlot: [
    {
      key: "chart_type",
      label: "Chart Type",
      type: "select",
      options: [
        "hr_diagram",
        "sed_fit",
        "spectrum_overlay",
        "redshift_histogram",
        "sky_coverage",
        "correlation_scatter",
        "corner_plot",
      ],
      default: "spectrum_overlay",
    },
  ],
};

/* ── component ── */

interface NodeParamsEditorProps {
  nodeId: string;
  nodeType: string;
  nodeLabel: string;
  currentParams: Record<string, unknown>;
  onApply: (nodeId: string, params: Record<string, unknown>) => void;
  onCancel: () => void;
}

export default function NodeParamsEditor({
  nodeId,
  nodeType,
  nodeLabel,
  currentParams,
  onApply,
  onCancel,
}: NodeParamsEditorProps) {
  const paramDefs = NODE_PARAM_DEFS[nodeType] ?? [];

  // Initialise local form state from currentParams + defaults
  const [formValues, setFormValues] = useState<Record<string, string | number>>({});

  useEffect(() => {
    const initial: Record<string, string | number> = {};
    for (const def of paramDefs) {
      if (currentParams[def.key] !== undefined && currentParams[def.key] !== null) {
        initial[def.key] = currentParams[def.key] as string | number;
      } else if (def.default !== undefined) {
        initial[def.key] = def.default;
      } else {
        initial[def.key] = def.type === "number" ? "" as unknown as number : "";
      }
    }
    setFormValues(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeId, nodeType]);

  const handleChange = (key: string, value: string, type: "text" | "number" | "select") => {
    if (type === "number") {
      // Allow empty string while typing; store parsed number otherwise
      setFormValues((prev) => ({
        ...prev,
        [key]: value === "" ? "" : Number(value),
      }));
    } else {
      setFormValues((prev) => ({ ...prev, [key]: value }));
    }
  };

  const handleApply = () => {
    // Build clean params object, omitting empty strings
    const params: Record<string, unknown> = {};
    for (const def of paramDefs) {
      const val = formValues[def.key];
      if (val !== "" && val !== undefined) {
        params[def.key] = val;
      }
    }
    onApply(nodeId, params);
  };

  return (
    <div className="node-params-editor">
      <div className="node-params-editor-header">
        <h3>{nodeLabel}</h3>
        <span className="node-params-type-badge">{nodeType}</span>
      </div>

      {paramDefs.length === 0 && (
        <p className="node-params-empty">This node has no configurable parameters.</p>
      )}

      <div className="node-params-fields">
        {paramDefs.map((def) => (
          <div className="node-params-field" key={def.key}>
            <label htmlFor={`param-${nodeId}-${def.key}`}>
              {def.label}
              {def.required && <span className="node-params-required">*</span>}
            </label>

            {def.type === "select" ? (
              <select
                id={`param-${nodeId}-${def.key}`}
                value={String(formValues[def.key] ?? "")}
                onChange={(e) => handleChange(def.key, e.target.value, def.type)}
              >
                {def.options?.map((opt) => (
                  <option key={opt} value={opt}>
                    {opt}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id={`param-${nodeId}-${def.key}`}
                type={def.type === "number" ? "number" : "text"}
                step={def.type === "number" ? "any" : undefined}
                value={formValues[def.key] ?? ""}
                onChange={(e) => handleChange(def.key, e.target.value, def.type)}
                placeholder={def.default !== undefined ? `Default: ${def.default}` : undefined}
              />
            )}
          </div>
        ))}
      </div>

      <div className="node-params-actions">
        <button className="btn-secondary btn-small" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn-primary btn-small" onClick={handleApply}>
          Apply
        </button>
      </div>
    </div>
  );
}
