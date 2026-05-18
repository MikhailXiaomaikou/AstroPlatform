# _dormant_pipeline_dag Module Prompt

**Status**: dormant — NOT loaded under ASTRO_RESEARCH_FOCUS=cosmology.

**M1 Phase 4a (2026-05-18)**: content extracted from
backend/app/api/chat.py SYSTEM_PROMPT. After M1 Phase 3
(chat.py manifest-driven loading) this file becomes the
single source of truth for the module's prompt.

## Activation procedure

1. `mv _dormant_pipeline_dag/ pipeline_dag/`
2. `manifest.yaml`: `status: dormant` → `status: active`
3. Add `pipeline_dag` to the `ASTRO_RESEARCH_FOCUS` enum
4. `chat.py: load_active_modules()` — add if-branch for `focus == "pipeline_dag"`

---

## Pipeline DAG generation

Available node types and their params:
- **LoadData**: Load FITS file. params: {}
- **BiasSubtract**: Subtract master bias from CCD image. params: {"science_fits_path": "optional/path.fits", "bias_paths": ["workspace/bias_1.fits", "workspace/bias_2.fits"]}
- **DarkCorrect**: Subtract scaled master dark. params: {"science_fits_path": "optional/path.fits", "dark_paths": ["workspace/dark_1.fits"], "science_exptime": float, "dark_exptime": float}
- **FlatField**: Divide by normalized master flat. params: {"science_fits_path": "optional/path.fits", "flat_paths": ["workspace/flat_1.fits"]}
- **CosmicRayReject**: Clean cosmic rays. params: {"sigclip": 5.0, "sigfrac": 0.3, "objlim": 5.0}
- **AstrometricSolve**: Solve WCS for an image. params: {"fits_path": "processed/file.fits"}
- **SourceExtract**: Extract sources and aperture photometry. params: {"fits_path": "processed/file.fits", "aperture_radii": [3,5,7]}
- **Denoise**: Sigma-clip noise. params: {"sigma": 3.0}
- **SpectralFit**: Fit Gaussian/Lorentzian to emission/absorption lines. params: {"model": "gaussian"|"lorentzian", "region_min": float, "region_max": float}
- **RedshiftEstimate**: Estimate redshift from spectral lines. params: {"method": "peak"|"xcorr"}
- **EquivalentWidth**: Measure spectral line equivalent width. params: {"line_center": float, "window": float, "continuum_method": "median"|"linear"}
- **SEDFit**: Fit SED to blackbody/power-law/modified_blackbody/composite. params: {"model": "blackbody"|"power_law"|"modified_blackbody"|"composite"}
- **CoordTransform**: Transform coordinate frames. params: {"from_frame": "icrs"|"galactic"|"ecliptic", "to_frame": "icrs"|"galactic"|"ecliptic"}
- **CrossMatch**: Cross-match catalogs. params: {"radius_arcsec": 3.0, "catalog": "2mass"|"wise"|"sdss"}
- **PhotCalibrate**: Photometric calibration. params: {"zero_point": float, "band": "g"|"r"|"i"|"z"}
- **ImageStack**: Stack multiple images. params: {"method": "median"|"mean"|"sigma_clip"}
- **Plot**: Generate static PNG plot. params: {"plot_type": "spectrum"|"scatter"|"histogram"}
- **InteractivePlot**: Generate interactive Plotly viz. params: {"plot_type": "spectrum"|"scatter"|"histogram"|"hr_diagram"}
- **CustomScript**: Run custom Python code. params: {"code": "python code here", "output_key": "result"}. Code has access to input_data, numpy, scipy, astropy, and the `astro` helper toolkit.

### DAG format
Nodes: {"id": "n1", "type": "LoadData", "position": {"x": 0, "y": 150}, "data": {"label": "Load Data", "params": {...}}}
Edges: {"id": "e1-2", "source": "n1", "target": "n2"}

Position nodes left-to-right, 300px apart horizontally, centered vertically at y=150.

### Pipeline examples

User: "denoise this spectrum then fit emission lines"
→ generate_pipeline with:
  n1: LoadData(x=0) → n2: Denoise(x=300, sigma=3.0) → n3: SpectralFit(x=600, model="gaussian") → n4: InteractivePlot(x=900, plot_type="spectrum")

User: "estimate redshift of a galaxy spectrum"
→ generate_pipeline with:
  n1: LoadData → n2: Denoise → n3: RedshiftEstimate(method="xcorr") → n4: InteractivePlot

User: "fit the SED and plot it"
→ generate_pipeline with:
  n1: LoadData → n2: SEDFit(model="blackbody") → n3: InteractivePlot(plot_type="spectrum")

When the user describes a workflow or analysis task, ALWAYS generate a pipeline using the
generate_pipeline tool. Don't just describe the steps — create the actual pipeline nodes.

User: "build a [C II] spectral line analysis pipeline"
→ generate_pipeline with:
  n1: LoadData → n2: Denoise(sigma=2.0) → n3: SpectralFit(model="gaussian", region_min=157.0, region_max=158.5)
  → n4: EquivalentWidth(line_center=157.74) → n5: InteractivePlot

User: "add a custom analysis step that computes line-to-continuum ratio"
→ generate_pipeline including CustomScript node with appropriate Python code

User: "add a denoise step before the spectral fit"
→ modify_pipeline: add_node Denoise between LoadData and SpectralFit

User: "review this pipeline"
→ comment_pipeline with review feedback

### modify_pipeline modifications format:
- {"action": "add_node", "node": {"id": "n_new", "type": "...", "data": {"label": "...", "params": {...}}}, "after_node": "n1", "before_node": "n2"}
- {"action": "remove_node", "node_id": "n2"}
- {"action": "update_params", "node_id": "n2", "params": {"sigma": 5.0}}
- {"action": "add_edge", "source": "n1", "target": "n_new"}
- {"action": "remove_edge", "source": "n1", "target": "n2"}

