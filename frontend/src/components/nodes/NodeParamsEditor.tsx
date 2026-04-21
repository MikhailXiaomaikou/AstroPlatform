import { useEffect, useState } from "react";

/* ── param schema per node type ── */

interface ParamDef {
  key: string;
  label: string;
  type: "text" | "number" | "select" | "checkboxes";
  default?: string | number;
  options?: string[]; // for select and checkboxes
  required?: boolean;
  placeholder?: string;
}

const NODE_PARAM_DEFS: Record<string, ParamDef[]> = {
  QueryData: [
    { key: "query", label: "Target Name", type: "text", required: true, placeholder: "M31, Pleiades, Crab Nebula, 10.68 41.27" },
    { key: "sources", label: "Sources", type: "checkboxes", default: "simbad,gaia", options: ["simbad", "gaia", "sdss", "vizier", "mast", "ned", "2mass", "chandra", "allwise", "alma", "eso", "irsa", "jwst", "lamost", "desi"] },
    { key: "radius", label: "Search Radius (deg)", type: "number", default: 0.1 },
    { key: "max_results", label: "Max Results", type: "number", default: 100 },
    { key: "ra", label: "RA (optional, deg)", type: "number", placeholder: "Auto-resolved from target name" },
    { key: "dec", label: "Dec (optional, deg)", type: "number", placeholder: "Auto-resolved from target name" },
  ],
  ImportWorkspace: [
    { key: "path", label: "Workspace File", type: "select", required: true },
  ],
  LoadData: [
    { key: "fits_path", label: "FITS Path", type: "text" },
  ],
  BiasSubtract: [
    { key: "science_fits_path", label: "Science FITS (optional)", type: "text", placeholder: "Falls back to upstream image or run input path" },
    { key: "bias_paths", label: "Bias Paths", type: "text", placeholder: 'Comma-separated FITS paths or leave blank if using bias_frames' },
  ],
  DarkCorrect: [
    { key: "science_fits_path", label: "Science FITS (optional)", type: "text" },
    { key: "dark_paths", label: "Dark Paths", type: "text", placeholder: "Comma-separated FITS paths" },
    { key: "science_exptime", label: "Science EXPTIME", type: "number", default: 1.0 },
    { key: "dark_exptime", label: "Dark EXPTIME", type: "number", default: 1.0 },
  ],
  FlatField: [
    { key: "science_fits_path", label: "Science FITS (optional)", type: "text" },
    { key: "flat_paths", label: "Flat Paths", type: "text", placeholder: "Comma-separated FITS paths" },
  ],
  CosmicRayReject: [
    { key: "fits_path", label: "FITS Path (optional)", type: "text" },
    { key: "sigclip", label: "Sigma Clip", type: "number", default: 5.0 },
    { key: "sigfrac", label: "Sigma Fraction", type: "number", default: 0.3 },
    { key: "objlim", label: "Object Limit", type: "number", default: 5.0 },
  ],
  AstrometricSolve: [
    { key: "fits_path", label: "FITS Path", type: "text", placeholder: "Uses upstream output_path or fits_path if blank" },
  ],
  SourceExtract: [
    { key: "fits_path", label: "FITS Path (optional)", type: "text", placeholder: "Uses upstream output_path or fits_path if blank" },
    { key: "aperture_radii", label: "Aperture Radii", type: "text", default: "3,5,7" },
  ],
  PSFPhotometry: [
    { key: "fits_path", label: "FITS Path (optional)", type: "text", placeholder: "Uses upstream image if blank" },
    { key: "fwhm", label: "FWHM (pixels)", type: "number", default: 3.0 },
    { key: "threshold_sigma", label: "Detection Threshold (\u03c3)", type: "number", default: 5.0 },
    { key: "aperture_radii", label: "Aperture Radii (px)", type: "text", default: "3,5,7" },
  ],
  Denoise: [
    { key: "method", label: "Method", type: "select", options: ["sigma_clip", "savgol", "wavelet", "gaussian", "median"], default: "sigma_clip" },
    { key: "flux_key", label: "Flux Key", type: "text", default: "flux" },
    { key: "sigma", label: "Sigma (clip threshold)", type: "number", default: 3.0, placeholder: "For sigma_clip method" },
    { key: "max_iters", label: "Max Iterations", type: "number", default: 5, placeholder: "For sigma_clip method" },
    { key: "window_length", label: "Window Length", type: "number", default: 11, placeholder: "For savgol method (odd number)" },
    { key: "polyorder", label: "Polynomial Order", type: "number", default: 3, placeholder: "For savgol method" },
    { key: "wavelet", label: "Wavelet", type: "select", options: ["db4", "db6", "db8", "sym4", "sym6", "coif2", "haar"], default: "db4" },
    { key: "threshold_sigma", label: "Threshold (σ)", type: "number", default: 2.0, placeholder: "For wavelet method" },
    { key: "kernel_size", label: "Kernel Size", type: "number", default: 5, placeholder: "For gaussian/median method" },
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
  Condition: [
    { key: "expression", label: "Condition", type: "text", required: true, placeholder: "row_count > 100" },
  ],
  CustomScript: [
    {
      key: "code",
      label: "Python Code",
      type: "text",
      default: "# Access upstream data via input_data dict\n# Assign output to 'result' variable\nimport numpy as np\nresult = input_data",
    },
    {
      key: "output_key",
      label: "Output Variable Name",
      type: "text",
      default: "result",
    },
  ],
};

/* ── component ── */

interface NodeParamsEditorProps {
  nodeId: string;
  nodeType: string;
  nodeLabel: string;
  currentParams: Record<string, unknown>;
  workspacePaths?: string[];
  onApply: (nodeId: string, params: Record<string, unknown>) => void;
  onCancel: () => void;
}

export default function NodeParamsEditor({
  nodeId,
  nodeType,
  nodeLabel,
  currentParams,
  workspacePaths = [],
  onApply,
  onCancel,
}: NodeParamsEditorProps) {
  const paramDefs = NODE_PARAM_DEFS[nodeType] ?? [];

  // Initialise local form state from currentParams + defaults
  const [formValues, setFormValues] = useState<Record<string, string | number>>({});
  const [touched, setTouched] = useState<Record<string, boolean>>({});

  useEffect(() => {
    const initial: Record<string, string | number> = {};
    for (const def of paramDefs) {
      if (currentParams[def.key] !== undefined && currentParams[def.key] !== null) {
        const currentValue = currentParams[def.key];
        if (Array.isArray(currentValue) && ["bias_paths", "dark_paths", "flat_paths", "aperture_radii"].includes(def.key)) {
          initial[def.key] = currentValue.join(",");
        } else {
          initial[def.key] = currentValue as string | number;
        }
      } else if (def.default !== undefined) {
        initial[def.key] = def.default;
      } else if (def.type === "select" && nodeType === "ImportWorkspace" && def.key === "path" && workspacePaths.length > 0) {
        initial[def.key] = workspacePaths[0];
      } else {
        initial[def.key] = def.type === "number" ? "" as unknown as number : "";
      }
    }
    // Q3: initialize form values when selected node changes — reset per
    // node-type + saved params.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFormValues(initial);

  }, [currentParams, nodeId, nodeType, paramDefs, workspacePaths]);

  const handleChange = (key: string, value: string, type: "text" | "number" | "select" | "checkboxes") => {
    setTouched((prev) => ({ ...prev, [key]: true }));
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

  const handleBlur = (key: string) => {
    setTouched((prev) => ({ ...prev, [key]: true }));
  };

  const getFieldError = (def: ParamDef): string | null => {
    if (!touched[def.key]) return null;
    const val = formValues[def.key];
    if (def.required && (val === "" || val === undefined || val === null)) {
      return "This field is required";
    }
    if (def.type === "number" && val !== "" && val !== undefined) {
      const numVal = typeof val === "string" ? Number(val) : val;
      if (isNaN(numVal as number)) {
        return "Must be a valid number";
      }
    }
    return null;
  };

  const handleApply = () => {
    // Build clean params object, omitting empty strings
    const params: Record<string, unknown> = {};
    for (const def of paramDefs) {
      const val = formValues[def.key];
      if (val !== "" && val !== undefined) {
        if (typeof val === "string" && ["bias_paths", "dark_paths", "flat_paths", "aperture_radii"].includes(def.key)) {
          params[def.key] = val
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean)
            .map((item) => (def.key === "aperture_radii" ? Number(item) : item));
        } else {
          params[def.key] = val;
        }
      }
    }
    onApply(nodeId, params);
  };

  return (
    <div className="node-params-editor" role="dialog" aria-label="Node parameters">
      <div className="node-params-editor-header">
        <h3>{nodeLabel}</h3>
        <span className="node-params-type-badge">{nodeType}</span>
      </div>

      {paramDefs.length === 0 && (
        <p className="node-params-empty">This node has no configurable parameters.</p>
      )}

      <div className="node-params-fields">
        {paramDefs.map((def) => {
          const fieldError = getFieldError(def);
          return (
          <div className="node-params-field" key={def.key}>
            <label htmlFor={`param-${nodeId}-${def.key}`}>
              {def.label}
              {def.required && <span className="node-params-required">*</span>}
            </label>

            {def.type === "checkboxes" && def.options ? (
              <div className="node-params-checkboxes">
                {def.options.map((opt) => {
                  const selected = String(formValues[def.key] ?? "").split(",").map((s) => s.trim()).filter(Boolean);
                  const checked = selected.includes(opt);
                  return (
                    <label key={opt} className="node-params-checkbox-label">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => {
                          const next = checked ? selected.filter((s) => s !== opt) : [...selected, opt];
                          handleChange(def.key, next.join(","), "text");
                        }}
                      />
                      <span>{opt.toUpperCase()}</span>
                    </label>
                  );
                })}
              </div>
            ) : def.type === "select" && !(nodeType === "ImportWorkspace" && def.key === "path" && workspacePaths.length === 0) ? (
              <select
                id={`param-${nodeId}-${def.key}`}
                value={String(formValues[def.key] ?? "")}
                onChange={(e) => handleChange(def.key, e.target.value, def.type)}
              >
                {(nodeType === "ImportWorkspace" && def.key === "path" ? workspacePaths : def.options)?.map((opt) => (
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
                onBlur={() => handleBlur(def.key)}
                placeholder={def.placeholder || (def.default !== undefined ? `Default: ${def.default}` : undefined)}
                style={fieldError ? { borderColor: "#f44336", boxShadow: "0 0 0 1px #f44336" } : undefined}
              />
            )}
            {fieldError && (
              <span style={{ color: "#f44336", fontSize: "0.72rem", marginTop: 2, display: "block" }}>{fieldError}</span>
            )}
            {nodeType === "ImportWorkspace" && def.key === "path" && workspacePaths.length === 0 && (
              <div className="node-params-empty" style={{ marginTop: 6 }}>
                No synced workspace files found. Enter a storage path manually or save files to Workspace first.
              </div>
            )}
          </div>
          );
        })}
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
