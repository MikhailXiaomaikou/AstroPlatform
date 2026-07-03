"""Spectrum analysis + pipeline suggestion tools.

Moved verbatim out of app/services/ai_tools/__init__.py (H2 split,
2026-07-03). Tool schemas here cover: analyze_spectrum, generate_pipeline, analyze_spectrum_pro,
sensitivity_analysis.
Schemas are reassembled into TOOLS (exact pre-split order) and tool
calls are still dispatched by _execute_tool_inner in the package
__init__ — this module is an implementation detail, import from
app.services.ai_tools.
"""

import asyncio

TOOL_SCHEMAS = [
    {
        "name": "analyze_spectrum",
        "description": (
            "Analyze a FITS spectrum file: detect peaks, classify continuum shape, "
            "estimate redshift, identify spectral lines. Use this when the user has "
            "uploaded a FITS file or fetched one from a database."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fits_path": {"type": "string", "description": "Path to the FITS file in storage"},
            },
            "required": ["fits_path"],
        },
    },
    {
        "name": "generate_pipeline",
        "description": (
            "Create a data processing pipeline DAG from a workflow description. "
            "Available nodes: LoadData, ImportWorkspace, Denoise, SpectralFit, FluxCalibrate, "
            "TelluricCorrect, SpectraStack, RedshiftEstimate, EquivalentWidth, SEDFit, "
            "PhotoZPro, BayesianFit, CoordTransform, CrossMatch, PhotCalibrate, PSFPhotometry, "
            "SourceExtract, ImageStack, CosmicRayReject, BiasSubtract, DarkCorrect, FlatField, "
            "AstrometricSolve, TransitFit, GPDetrend, Reproject, Mosaic, PSFMatch, Deblend, "
            "TimeSeriesAnalysis, Plot, InteractivePlot, Condition, CustomScript."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Pipeline name"},
                "description": {"type": "string", "description": "What the pipeline does"},
                "nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "type": {"type": "string"},
                            "params": {"type": "object"},
                        },
                        "required": ["id", "type"],
                    },
                    "description": "Pipeline nodes in execution order",
                },
                "edges": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source": {"type": "string"},
                            "target": {"type": "string"},
                        },
                        "required": ["source", "target"],
                    },
                    "description": "Edges connecting nodes",
                },
            },
            "required": ["name", "nodes", "edges"],
        },
    },
    {
        "name": "analyze_spectrum_pro",
        "description": (
            "Professional spectral analysis: line identification against NIST catalogs (60+ lines), "
            "Gaussian/Voigt fitting with specutils, equivalent width measurements, heliocentric "
            "correction, and flux calibration. Use this for research-grade spectral analysis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fits_path": {"type": "string", "description": "Path to FITS spectrum file"},
                "operations": {
                    "type": "array",
                    "items": {"type": "string", "enum": [
                        "identify_lines", "fit_lines", "equivalent_width",
                        "heliocentric_correct", "flux_calibrate", "telluric_correct"
                    ]},
                    "description": "Operations to perform (default: identify_lines)",
                },
                "redshift": {"type": "number", "description": "Known redshift for line matching"},
                "ra": {"type": "number", "description": "RA in degrees (for heliocentric)"},
                "dec": {"type": "number", "description": "Dec in degrees (for heliocentric)"},
                "obstime": {"type": "string", "description": "Observation time ISO format"},
                "line_centers": {"type": "array", "items": {"type": "number"}, "description": "Specific line centers to fit"},
                "model": {"type": "string", "enum": ["gaussian", "lorentzian", "voigt"]},
            },
            "required": ["fits_path"],
        },
    },
    {
        "name": "sensitivity_analysis",
        "description": (
            "Run sensitivity analysis by perturbing parameters and observing result changes. "
            "Provide a Python expression or function call, the parameter to vary, and the range. "
            "Returns a table of parameter values vs results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code that computes a result. Must assign to 'result' variable.",
                },
                "parameter": {
                    "type": "string",
                    "description": "Variable name to perturb (must be assigned in the code before use)",
                },
                "base_value": {"type": "number", "description": "Nominal parameter value"},
                "perturbations": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Fractional perturbations to apply (e.g., [-0.2, -0.1, 0, 0.1, 0.2] for ±20%)",
                },
            },
            "required": ["code", "parameter", "base_value"],
        },
    },
]


async def _exec_analyze(inp: dict, api_key: str, provider_api_keys: dict[str, str] | None = None) -> dict:
    from app.services.spectrum_analyzer import extract_spectrum_from_fits, analyze_spectrum, ai_interpret
    from app.pipeline.nodes.redshift import redshift_estimate

    spec = extract_spectrum_from_fits(inp["fits_path"])
    summary = analyze_spectrum(spec["wavelength"], spec["flux"])

    rz_result = None
    if len(spec["wavelength"]) >= 50:
        try:
            rz = redshift_estimate(
                {"data": spec},
                {"method": "chi2_grid", "z_min": 0.0, "z_max": 2.0, "z_step": 0.001},
            )
            rz_result = rz.get("redshift_result")
        except Exception:
            pass

    result = {
        "continuum_shape": summary.continuum_shape,
        "n_peaks": len(summary.peaks),
        "emission_peaks": [{"wavelength": p.wavelength, "snr": p.snr} for p in summary.peaks if p.is_emission][:10],
        "absorption_features": [{"wavelength": p.wavelength, "snr": p.snr} for p in summary.peaks if not p.is_emission][:10],
        "wavelength_range": [summary.wavelength_min, summary.wavelength_max],
        "redshift_estimate": rz_result,
    }
    if api_key or provider_api_keys:
        try:
            result["ai_interpretation"] = await ai_interpret(
                summary,
                rz_result,
                api_key,
                provider_api_keys=provider_api_keys,
            )
        except Exception as exc:
            result["ai_interpretation_error"] = str(exc)
    return result


def _exec_pipeline(inp: dict) -> dict:
    nodes = inp.get("nodes", [])
    edges = inp.get("edges", [])

    # Auto-position
    for i, node in enumerate(nodes):
        if "position" not in node:
            node["position"] = {"x": i * 300, "y": 150}
        if "data" not in node:
            node["data"] = {"label": node.get("type", ""), "params": node.get("params", {})}
        else:
            node["data"].setdefault("label", node.get("type", ""))
            node["data"].setdefault("params", node.get("params", {}))

    # Auto-generate edge IDs
    for i, edge in enumerate(edges):
        if "id" not in edge:
            edge["id"] = f"e{edge['source']}-{edge['target']}"

    dag = {"nodes": nodes, "edges": edges}
    return {
        "name": inp.get("name", "AI Pipeline"),
        "description": inp.get("description", ""),
        "dag": dag,
        "status": "created",
    }


async def _exec_analyze_spectrum_pro(inp: dict) -> dict:
    """Run professional spectral analysis operations on a FITS spectrum."""
    import os
    from app.services.spectral_analysis_pro import (
        load_spectrum, identify_lines, fit_lines,
        measure_equivalent_width, heliocentric_correction,
        flux_calibrate, telluric_correct,
    )

    fits_path = inp.get("fits_path", "")
    if not fits_path:
        return {"error": "fits_path is required"}

    # Resolve path relative to data directory
    base_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data")
    full_path = os.path.normpath(os.path.join(base_dir, fits_path))
    if not os.path.isfile(full_path):
        full_path = fits_path
    if not os.path.isfile(full_path):
        return {"error": f"FITS file not found: {fits_path}"}

    # M13: explicitly-empty operations list used to load the spectrum and
    # return only metadata, which was surprising.  Fall back to the default
    # single-op pipeline when the caller passes [].
    operations = inp.get("operations")
    if operations is None:
        operations = ["identify_lines"]
    elif isinstance(operations, list) and len(operations) == 0:
        operations = ["identify_lines"]
    redshift = inp.get("redshift", 0.0)
    model = inp.get("model", "gaussian")
    line_centers = inp.get("line_centers")

    loop = asyncio.get_running_loop()

    def _run():
        result = {}
        # Load spectrum
        try:
            spec = load_spectrum(full_path)
            result["spectrum_loaded"] = True
            result["n_pixels"] = len(spec.get("wavelength", []))
            result["wave_range"] = [
                float(min(spec["wavelength"])),
                float(max(spec["wavelength"])),
            ] if spec.get("wavelength") else []
        except Exception as e:
            return {"error": f"Failed to load spectrum: {e}"}

        wave = spec["wavelength"]
        flux = spec["flux"]
        flux_err = spec.get("flux_err")

        if "identify_lines" in operations:
            try:
                result["identified_lines"] = identify_lines(
                    wave, flux, flux_err,
                    redshift=redshift,
                )
            except Exception as e:
                result["identify_lines_error"] = str(e)

        if "fit_lines" in operations:
            try:
                result["fitted_lines"] = fit_lines(
                    wave, flux, flux_err,
                    line_centers=line_centers,
                    model=model,
                )
            except Exception as e:
                result["fit_lines_error"] = str(e)

        if "equivalent_width" in operations:
            try:
                centers = line_centers or []
                if not centers and "identified_lines" in result:
                    centers = [
                        line["observed_wavelength"]
                        for line in result["identified_lines"]
                        if line.get("identification") != "unidentified"
                    ][:10]
                ew_results = []
                for c in centers:
                    ew_results.append(measure_equivalent_width(wave, flux, c))
                result["equivalent_widths"] = ew_results
            except Exception as e:
                result["equivalent_width_error"] = str(e)

        if "heliocentric_correct" in operations:
            ra = inp.get("ra")
            dec = inp.get("dec")
            obstime = inp.get("obstime")
            if ra is not None and dec is not None and obstime:
                try:
                    hc = heliocentric_correction(wave, flux, ra, dec, obstime)
                    result["heliocentric"] = {
                        "v_correction_km_s": hc["v_correction_km_s"],
                        "applied": hc["applied"],
                    }
                except Exception as e:
                    result["heliocentric_error"] = str(e)
            else:
                result["heliocentric_error"] = "ra, dec, and obstime required"

        if "flux_calibrate" in operations:
            try:
                fc = flux_calibrate(wave, flux)
                result["flux_calibration"] = {
                    "calibrated": fc["calibrated"],
                    "note": fc.get("note", ""),
                }
            except Exception as e:
                result["flux_calibrate_error"] = str(e)

        if "telluric_correct" in operations:
            try:
                tc = telluric_correct(wave, flux)
                result["telluric_correction"] = {
                    "corrected": tc["corrected"],
                    "model": tc["model"],
                }
            except Exception as e:
                result["telluric_correct_error"] = str(e)

        return result

    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _run),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        return {"error": "Spectral analysis timed out after 120 seconds"}

    return result


async def _exec_sensitivity_analysis(inp: dict, python_session_id: str = "default") -> dict:
    """Perturb a parameter and observe how results change."""
    from app.services.code_executor import execute_python

    code = inp.get("code", "")
    param = inp.get("parameter", "")
    base = float(inp.get("base_value", 0))
    perturbations = inp.get("perturbations", [-0.2, -0.1, 0, 0.1, 0.2])

    results = []
    loop = asyncio.get_running_loop()
    for frac in perturbations:
        value = base * (1 + frac)
        modified_code = f"{param} = {value}\n{code}"
        try:
            exec_result = await asyncio.wait_for(
                loop.run_in_executor(None, execute_python, modified_code, None, python_session_id),
                timeout=30.0,
            )
            if exec_result.success:
                # Extract 'result' variable from output
                result_val = exec_result.variables.get("result")
                results.append({"perturbation": frac, "value": value, "result": result_val, "success": True})
            else:
                results.append({"perturbation": frac, "value": value, "error": exec_result.error, "success": False})
        except asyncio.TimeoutError:
            results.append({"perturbation": frac, "value": value, "error": "timeout", "success": False})

    return {
        "parameter": param,
        "base_value": base,
        "results": results,
        # Every number here is a hypothetical: the perturbed `value` is
        # base*(1+frac) (pure model arithmetic) and each `result` is computed
        # with the parameter SET to that model-chosen value — none is a
        # measurement of reality. Mark the payload non-claimable so a model
        # cannot launder an arbitrary base_value*(1+frac) product into a cited
        # number (2026-06-12 review: live derived-number bypass).
        "__do_not_claim__": True,
        "__message_to_model__": (
            "Sensitivity/what-if output. Discuss the TREND (how results move with "
            "the perturbation) qualitatively; do NOT cite any perturbed value or "
            "result here as a measurement — re-run the real fit to quote a number."
        ),
    }
