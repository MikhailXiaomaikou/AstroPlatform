"""Tool definitions and executors for the Claude AI research agent.

Each tool is a function Claude can call. The agent loop in chat.py
handles the tool_use → result → next message cycle automatically.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


# ── Tool Definitions (Anthropic tool_use format) ──

# In-memory cache for last search results per user session (keyed by a simple token)
_search_result_cache: dict[str, list[dict]] = {}


def store_search_results(key: str, results: list[dict]) -> None:
    """Cache search results so AI can access full data later."""
    _search_result_cache[key] = results
    # Keep only last 20 sessions
    if len(_search_result_cache) > 20:
        oldest = list(_search_result_cache.keys())[0]
        del _search_result_cache[oldest]


def get_cached_results(key: str) -> list[dict] | None:
    return _search_result_cache.get(key)


TOOLS = [
    {
        "name": "search_objects",
        "description": (
            "Search astronomical databases for objects by name, coordinates, or scientific criteria. "
            "Searches SIMBAD, Gaia, SDSS, NED, etc. Returns object names, positions, types, magnitudes, redshifts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Object name or search description (e.g. 'M31', 'NGC 1068', 'high redshift quasars')"},
                "sources": {"type": "array", "items": {"type": "string"}, "description": "Data sources to query (e.g. ['simbad', 'gaia', 'sdss']). Default: ['simbad']"},
                "radius": {"type": "number", "description": "Search radius in degrees. Default: 0.1"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "run_adql",
        "description": (
            "Execute an ADQL query on Gaia DR3, SIMBAD, VizieR, or CADC TAP services. "
            "Use this for precise database queries with column selection and filtering."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "ADQL query string"},
                "service": {"type": "string", "enum": ["gaia", "simbad", "vizier", "cadc"], "description": "TAP service to query"},
            },
            "required": ["query", "service"],
        },
    },
    {
        "name": "get_object_info",
        "description": (
            "Get comprehensive information about a specific astronomical object: "
            "type, redshift, spectral type, morphology, cross-identifications (all known names), "
            "which surveys have data, and literature references from ADS."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Object name (e.g. 'M 77', 'NGC 1068', 'Sirius')"},
                "ra": {"type": "number", "description": "RA in degrees (optional, helps resolve ambiguous names)"},
                "dec": {"type": "number", "description": "Dec in degrees (optional)"},
            },
            "required": ["name"],
        },
    },
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
            "Available nodes: LoadData, Denoise, SpectralFit, RedshiftEstimate, "
            "EquivalentWidth, SEDFit, CoordTransform, CrossMatch, PhotCalibrate, "
            "ImageStack, Plot, InteractivePlot."
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
        "name": "search_literature",
        "description": "Search NASA ADS for academic papers about an astronomical object or topic. Returns titles, authors, years, and paper abstracts that you can cite in your response.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Object name or search query for ADS"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_last_search_results",
        "description": (
            "Retrieve the full list of the user's most recent search results "
            "(up to 200 objects with all fields). Use when the user asks about "
            "'my results', 'the objects I found', etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_results": {"type": "integer", "description": "Max results to return (default 50)", "default": 50},
            },
        },
    },
    {
        "name": "run_pipeline",
        "description": (
            "Execute a pipeline DAG synchronously and return the results. "
            "Use after generating a pipeline to actually run it on data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dag": {
                    "type": "object",
                    "description": "Pipeline DAG with nodes and edges arrays",
                },
                "input_data_id": {"type": "string", "description": "FITS file path to use as input"},
            },
            "required": ["dag", "input_data_id"],
        },
    },
    {
        "name": "run_python",
        "description": (
            "Execute Python code for data analysis, statistical modeling, or visualization. "
            "Available libraries: numpy (as np), scipy, astropy (Table, SkyCoord, units as u), "
            "matplotlib.pyplot (as plt), pandas. "
            "The Standard Astro helper toolkit is preloaded as `astro` and may also be imported as `astro`. "
            "Helper functions: load_fits(path) returns astropy HDUList, "
            "get_search_results() returns the latest search results as a list of dicts, "
            "load_votable(path) loads a VOTable as an astropy Table, "
            "load_csv(path) loads a CSV as a pandas DataFrame, "
            "process_in_chunks(data, chunk_size, func) processes large data in memory-safe chunks, "
            "memory_usage_mb() returns current memory usage in MB. "
            "Use print() to output results. Matplotlib figures are automatically captured. "
            "Max execution time: 75 seconds."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute. Use print() for output and plt for plots.",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what the code does",
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "generate_proposal",
        "description": (
            "Gather data for an observation proposal. Resolves target coordinates, "
            "computes visibility from the telescope's observatory, estimates exposure "
            "time if target magnitude is known, and finds relevant literature. "
            "Returns structured information for drafting a telescope proposal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_name": {"type": "string", "description": "Target object name (e.g., 'NGC 1275')"},
                "telescope": {"type": "string", "description": "Telescope: vlt, keck, gemini, hst, jwst, alma, subaru"},
                "instrument": {"type": "string", "description": "Instrument name (optional, for reference)"},
                "science_goal": {"type": "string", "description": "Brief science justification"},
                "exposure_hours": {"type": "number", "description": "Requested exposure time in hours (optional)"},
            },
            "required": ["target_name", "telescope", "science_goal"],
        },
    },
    {
        "name": "query_transients",
        "description": (
            "Search for astronomical transients and alerts from TNS (Transient Name Server) "
            "and ZTF/Lasair. Find recent supernovae, novae, tidal disruption events, and other "
            "transients by name, coordinates, or type."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Transient name (e.g., 'SN 2024abc', 'AT 2024xyz')"},
                "ra": {"type": "number", "description": "Right ascension in degrees"},
                "dec": {"type": "number", "description": "Declination in degrees"},
                "radius_arcsec": {"type": "number", "description": "Search radius in arcseconds (default 10)"},
                "days_back": {"type": "integer", "description": "Search window in days (default 30)"},
                "obj_type": {"type": "string", "description": "Filter by type: SN, SN Ia, SN II, TDE, nova, AGN, etc."},
            },
        },
    },
    {
        "name": "read_arxiv_paper",
        "description": (
            "Download and read the text content of an arXiv paper. "
            "Provide the arXiv ID (e.g. '2301.12345'). Returns the paper's "
            "title, abstract, and extracted text from the PDF."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "arxiv_id": {"type": "string", "description": "arXiv paper ID (e.g. '2301.12345')"},
            },
            "required": ["arxiv_id"],
        },
    },
    {
        "name": "research_workflow",
        "description": (
            "Plan and execute a complete research workflow from hypothesis to conclusion. "
            "Use this when the user poses a research question or hypothesis that requires "
            "multi-step data analysis. The tool helps structure the investigation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hypothesis": {
                    "type": "string",
                    "description": "The research hypothesis or question to investigate",
                },
                "scope": {
                    "type": "string",
                    "enum": ["quick", "thorough"],
                    "description": "quick = rapid verification with basic statistics; thorough = comprehensive analysis with multiple tests",
                },
            },
            "required": ["hypothesis"],
        },
    },
    {
        "name": "estimate_photo_z",
        "description": (
            "Estimate photometric redshift from multi-band photometry. "
            "Use when users ask about galaxy distances or redshifts for objects without spectra."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "magnitudes": {
                    "type": "object",
                    "description": "Band magnitudes, e.g. {'g': 20.1, 'r': 19.5, 'i': 19.2}",
                },
                "mag_errors": {
                    "type": "object",
                    "description": "Magnitude errors per band",
                },
                "method": {
                    "type": "string",
                    "enum": ["template", "ml", "hybrid"],
                    "description": "Estimation method: 'template' (SED fitting), 'ml' (empirical colors), or 'hybrid' (weighted average of both). Default: 'hybrid'",
                },
            },
            "required": ["magnitudes"],
        },
    },
    {
        "name": "fit_isochrone",
        "description": (
            "Fit PARSEC isochrones to observed colour-magnitude data to determine "
            "cluster age, metallicity, distance, and extinction. Use this when the user "
            "asks about the age of a star cluster, wants to fit isochrones, or asks "
            "'how old is this cluster'. Requires observed BP-RP colours and G magnitudes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "bp_rp": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Observed BP-RP colour array",
                },
                "abs_mag": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Observed G magnitude array (apparent or absolute)",
                },
                "method": {
                    "type": "string",
                    "enum": ["grid", "mcmc"],
                    "description": "Fitting method: 'grid' for fast grid+Nelder-Mead, 'mcmc' for full MCMC with uncertainties",
                },
            },
            "required": ["bp_rp", "abs_mag"],
        },
    },
    {
        "name": "get_object_dossier",
        "description": (
            "Generate a comprehensive cross-match dossier for a sky position. "
            "Queries SIMBAD, Gaia DR3, SDSS, 2MASS, AllWISE, NED, and TNS concurrently "
            "and returns structured photometry (ugriz, JHKs, W1-W4), astrometry "
            "(parallax, proper motion, distance), redshift, host galaxy info, "
            "cross-identifications, and prior transient classifications."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Object name (optional, e.g. 'SN 2024abc')"},
                "ra": {"type": "number", "description": "Right ascension in degrees"},
                "dec": {"type": "number", "description": "Declination in degrees"},
            },
        },
    },
    {
        "name": "get_followup_recommendation",
        "description": (
            "Generate follow-up observation recommendations for a transient alert. "
            "Analyses the classification, confidence, and available ancillary data to "
            "produce a prioritised list of spectroscopy, photometry, X-ray, UV, or "
            "archival follow-up observations with facility suggestions, exposure times, "
            "and science goals."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "alert_id": {
                    "type": "string",
                    "description": "Alert ID (UUID or source_id) to look up from the database",
                },
                "ra": {
                    "type": "number",
                    "description": "Right ascension in degrees (used if alert_id not found or for ad-hoc queries)",
                },
                "dec": {
                    "type": "number",
                    "description": "Declination in degrees",
                },
                "magnitude": {
                    "type": "number",
                    "description": "Apparent magnitude of the transient",
                },
                "classification": {
                    "type": "string",
                    "description": "Classification type (e.g. SN_Ia, TDE, AGN, CV, Unknown)",
                },
            },
        },
    },
    {
        "name": "analyze_cross_wavelength",
        "description": (
            "Run cross-wavelength anomaly detection on a sky position. "
            "Checks IR excess (W1-W2), X-ray/optical ratio, astrometric anomalies, "
            "optical color consistency, SED shape vs blackbody, and variability. "
            "Returns a list of checks with ANOMALY/NORMAL/SKIPPED status, "
            "possible physical causes, and recommended follow-up observations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Object name for coordinate resolution (optional if ra/dec provided)",
                },
                "ra": {
                    "type": "number",
                    "description": "Right ascension in degrees",
                },
                "dec": {
                    "type": "number",
                    "description": "Declination in degrees",
                },
            },
        },
    },
    {
        "name": "crossmatch_catalogs",
        "description": (
            "Cross-match two astronomical catalogs by sky position. "
            "Provide ADQL queries for each catalog (must return 'ra' and 'dec' columns in degrees). "
            "Supports join types: 1and2 (inner), all1 (left), all2 (right), "
            "1or2 (full outer), 1not2 (anti-join), 2not1 (anti-join). "
            "Returns matched rows with separation in arcseconds."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table1_query": {
                    "type": "string",
                    "description": (
                        "ADQL query returning the first catalog (must SELECT ra, dec). "
                        "Example: 'SELECT TOP 1000 ra, dec, phot_g_mean_mag FROM gaiadr3.gaia_source WHERE ...'"
                    ),
                },
                "table2_query": {
                    "type": "string",
                    "description": "ADQL query returning the second catalog (must SELECT ra, dec).",
                },
                "radius_arcsec": {
                    "type": "number",
                    "description": "Maximum match radius in arcseconds (default 3.0)",
                },
                "join_type": {
                    "type": "string",
                    "enum": ["1and2", "1or2", "all1", "all2", "1not2", "2not1"],
                    "description": "Join type (default '1and2' = inner join, matched rows only)",
                },
                "service1": {
                    "type": "string",
                    "enum": ["gaia", "simbad", "vizier", "cadc"],
                    "description": "TAP service for table1 query (default 'gaia')",
                },
                "service2": {
                    "type": "string",
                    "enum": ["gaia", "simbad", "vizier", "cadc"],
                    "description": "TAP service for table2 query (default 'gaia')",
                },
            },
            "required": ["table1_query", "table2_query"],
        },
    },
    {
        "name": "search_lightcurve",
        "description": (
            "Search for Kepler, TESS, or K2 light curves for a given target. "
            "Returns available observations with mission, target name, and exposure time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Target name or TIC/KIC ID (e.g. 'Kepler-10', 'TIC 261136679')",
                },
                "mission": {
                    "type": "string",
                    "enum": ["kepler", "tess", "k2"],
                    "description": "Mission to search (default 'kepler')",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "extract_sources",
        "description": (
            "Extract sources from a FITS image using SEP (SExtractor in Python). "
            "Performs background subtraction, source detection, and Kron aperture photometry. "
            "Returns source positions, shape parameters, fluxes, and instrumental magnitudes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fits_path": {
                    "type": "string",
                    "description": "Path to the FITS image file in storage",
                },
                "threshold_sigma": {
                    "type": "number",
                    "description": "Detection threshold in sigma above background (default 3.0)",
                },
                "min_area": {
                    "type": "integer",
                    "description": "Minimum number of pixels for a detection (default 5)",
                },
            },
            "required": ["fits_path"],
        },
    },
]


# ── Tool Executors ──

async def execute_tool(
    tool_name: str,
    tool_input: dict,
    api_key: str = "",
    python_session_id: str = "default",
) -> dict:
    """Execute a tool call and return the result as a dict."""
    try:
        if tool_name == "search_objects":
            return await _exec_search(tool_input, python_session_id)
        elif tool_name == "run_adql":
            return await _exec_adql(tool_input, python_session_id)
        elif tool_name == "get_object_info":
            return await _exec_object_info(tool_input)
        elif tool_name == "analyze_spectrum":
            return await _exec_analyze(tool_input, api_key)
        elif tool_name == "generate_pipeline":
            return _exec_pipeline(tool_input)
        elif tool_name == "search_literature":
            return await _exec_literature(tool_input)
        elif tool_name == "run_python":
            return await _exec_run_python(tool_input, python_session_id)
        elif tool_name == "get_last_search_results":
            return _exec_get_cached_results(tool_input)
        elif tool_name == "run_pipeline":
            return await _exec_run_pipeline(tool_input)
        elif tool_name == "generate_proposal":
            return await _exec_generate_proposal(tool_input)
        elif tool_name == "query_transients":
            return await _exec_query_transients(tool_input)
        elif tool_name == "read_arxiv_paper":
            return await _exec_read_paper(tool_input)
        elif tool_name == "research_workflow":
            return _exec_research_workflow(tool_input)
        elif tool_name == "estimate_photo_z":
            return await _exec_estimate_photo_z(tool_input)
        elif tool_name == "fit_isochrone":
            return await _exec_fit_isochrone(tool_input)
        elif tool_name == "get_object_dossier":
            return await _exec_get_dossier(tool_input)
        elif tool_name == "get_followup_recommendation":
            return await _exec_get_followup(tool_input)
        elif tool_name == "analyze_cross_wavelength":
            return await _exec_cross_wavelength(tool_input)
        elif tool_name == "crossmatch_catalogs":
            return await _exec_crossmatch_catalogs(tool_input, python_session_id)
        elif tool_name == "search_lightcurve":
            return await _exec_search_lightcurve(tool_input)
        elif tool_name == "extract_sources":
            return await _exec_extract_sources(tool_input)
        else:
            return {"error": f"Unknown tool: {tool_name}"}
    except Exception as e:
        logger.warning("Tool %s failed: %s", tool_name, e)
        return {"error": str(e)}


async def _exec_search(inp: dict, python_session_id: str = "default") -> dict:
    from app.connectors.registry import CONNECTORS_KEYS, get_connector
    from app.api.data import _astro_to_result
    from app.api.data import _resolve_search_coordinates, _search_timeout_for_source
    from app.search.query_parser import parse_natural_query

    query = inp.get("query", "")
    sources = inp.get("sources", ["simbad"])
    radius = inp.get("radius", 0.1)
    sources = [s for s in sources if s in CONNECTORS_KEYS]
    if not sources:
        sources = ["simbad"]

    parsed = parse_natural_query(query)
    object_type = parsed.get("object_type")
    redshift_min = parsed.get("redshift_min")
    redshift_max = parsed.get("redshift_max")
    has_criteria = any([object_type, redshift_min, redshift_max])

    search_ra, search_dec = await _resolve_search_coordinates(query, None, None)

    async def _search_one(source: str):
        connector = get_connector(source)
        if source == "simbad" and has_criteria and hasattr(connector, "search_by_criteria"):
            return await asyncio.wait_for(
                connector.search_by_criteria(
                    object_type=object_type,
                    redshift_min=redshift_min,
                    redshift_max=redshift_max,
                    ra=search_ra, dec=search_dec, radius=radius,
                ), timeout=max(45.0, _search_timeout_for_source(source)),
            )
        return await asyncio.wait_for(
            connector.search(query, ra=search_ra, dec=search_dec, radius=radius),
            timeout=max(45.0, _search_timeout_for_source(source)),
        )

    tasks = [_search_one(s) for s in sources]
    results_per = await asyncio.gather(*tasks, return_exceptions=True)

    all_results = []
    for src, res in zip(sources, results_per):
        if isinstance(res, Exception):
            all_results.append({"source": src, "error": str(res)})
        else:
            for obj in res[:25]:
                r = _astro_to_result(obj).model_dump()
                all_results.append(r)

    # Cache results for later retrieval by get_last_search_results
    store_search_results("latest", all_results)
    store_search_results(f"latest:{python_session_id}", all_results)

    return {"results": all_results, "total": len(all_results)}


async def _exec_adql(inp: dict, python_session_id: str = "default") -> dict:
    from app.api.integration import adql_query, ADQLRequest
    req = ADQLRequest(query=inp.get("query", ""), service=inp.get("service", "gaia"))
    result = await adql_query(req)
    # Truncate for context window
    data = result.get("data", {}) if isinstance(result, dict) else {}
    row_count = result.get("row_count", 0) if isinstance(result, dict) else 0
    truncated = {}
    for col, vals in data.items():
        truncated[col] = vals[:100] if isinstance(vals, list) else vals
    adql_result = {
        "columns": result.get("columns", []) if isinstance(result, dict) else [],
        "data": truncated,
        "row_count": row_count,
        "showing": min(100, row_count),
    }

    # Auto-inject full result into Python sandbox for immediate use
    adql_rows = [
        {col: data.get(col, [None] * row_count)[i] for col in adql_result["columns"]}
        for i in range(min(row_count, 1000))
    ] if data else []
    store_search_results("latest_adql", adql_rows)
    store_search_results(f"latest_adql:{python_session_id}", adql_rows)

    return adql_result


async def _exec_object_info(inp: dict) -> dict:
    from app.connectors.simbad import SIMBADConnector
    simbad = SIMBADConnector()

    detail = await simbad.get_object_detail(inp["name"])
    if not detail:
        return {"error": f"Object '{inp['name']}' not found in SIMBAD"}

    ids = await simbad.get_identifiers(detail.get("name", inp["name"]))

    # Get ADS refs
    refs = []
    try:
        from app.api.citations import _search_ads_sync
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, _search_ads_sync, detail.get("name", inp["name"]))
        refs = [{"title": r["title"], "year": r["year"], "bibcode": r["bibcode"]} for r in (raw or [])[:5]]
    except Exception:
        pass

    return {
        **detail,
        "cross_ids": [i["name"] for i in ids[:20]],
        "references": refs,
    }


async def _exec_analyze(inp: dict, api_key: str) -> dict:
    from app.services.spectrum_analyzer import extract_spectrum_from_fits, analyze_spectrum, build_claude_prompt
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

    return {
        "continuum_shape": summary.continuum_shape,
        "n_peaks": len(summary.peaks),
        "emission_peaks": [{"wavelength": p.wavelength, "snr": p.snr} for p in summary.peaks if p.is_emission][:10],
        "absorption_features": [{"wavelength": p.wavelength, "snr": p.snr} for p in summary.peaks if not p.is_emission][:10],
        "wavelength_range": [summary.wavelength_min, summary.wavelength_max],
        "redshift_estimate": rz_result,
        "prompt_summary": build_claude_prompt(summary, rz_result),
    }


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


async def _exec_literature(inp: dict) -> dict:
    try:
        from app.api.citations import _search_ads_sync
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, _search_ads_sync, inp["query"])
        if not raw:
            return {"results": [], "message": "No papers found. ADS API key may not be configured."}
        return {
            "results": [
                {
                    "title": r["title"],
                    "authors": r["authors"][:3],
                    "year": r["year"],
                    "bibcode": r["bibcode"],
                    "abstract": (r.get("abstract") or "")[:500],
                }
                for r in raw[:8]
            ]
        }
    except Exception as e:
        return {"error": str(e)}


async def _exec_run_python(inp: dict, python_session_id: str = "default") -> dict:
    """Execute Python code in sandboxed environment."""
    from app.services.code_executor import execute_python

    code = inp.get("code", "")
    if not code.strip():
        return {"error": "No code provided"}

    # Run in executor with timeout to not block the event loop
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, execute_python, code, None, python_session_id),
            timeout=90.0,
        )
    except asyncio.TimeoutError:
        return {"success": False, "error": "Code execution timed out after 90 seconds", "stdout": ""}

    response: dict = {
        "success": result.success,
        "stdout": result.stdout[:50_000] if result.stdout else "",
    }

    if result.error:
        response["error"] = result.error
    if result.stderr and not result.success:
        response["traceback"] = result.stderr[:10_000]
    if result.figures:
        response["figures"] = result.figures[:10]  # max 10 figures
        response["figure_count"] = len(result.figures)
    if result.variables:
        response["variables"] = dict(list(result.variables.items())[:50])
    if result.variable_types:
        response["variable_types"] = dict(list(result.variable_types.items())[:50])

    return response


def _exec_get_cached_results(inp: dict) -> dict:
    """Return full cached search results."""
    max_n = inp.get("max_results", 50)
    results = get_cached_results("latest")
    if results is None:
        return {"results": [], "message": "No recent search results cached. Run a search first."}
    return {"results": results[:max_n], "total": len(results)}


async def _exec_run_pipeline(inp: dict) -> dict:
    """Execute a pipeline DAG synchronously and return results."""
    from app.pipeline.engine import execute_dag, topological_sort
    from app.pipeline.nodes import registry

    dag = inp.get("dag", {})
    input_data_id = inp.get("input_data_id", "")

    if "nodes" not in dag or "edges" not in dag:
        return {"error": "DAG must have 'nodes' and 'edges'"}

    # Validate node types
    for node in dag.get("nodes", []):
        if node.get("type") not in registry:
            return {"error": f"Unknown node type: {node.get('type')}"}

    try:
        topological_sort(dag)
    except ValueError as e:
        return {"error": str(e)}

    import uuid
    run_id = str(uuid.uuid4())
    try:
        # Run in executor to avoid blocking the async event loop
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, execute_dag, dag, input_data_id, run_id)
    except Exception as e:
        return {"error": f"Pipeline execution failed: {e}"}

    # Summarize results (truncate large data arrays)
    summary = {}
    for nid, res in results.items():
        s = {}
        if "error" in res:
            s["error"] = res["error"]
        else:
            for k, v in res.items():
                if k == "data" and isinstance(v, dict):
                    s["data_keys"] = list(v.keys())
                    s["data_lengths"] = {dk: len(dv) if isinstance(dv, list) else "scalar" for dk, dv in v.items()}
                elif isinstance(v, list) and len(v) > 10:
                    s[k] = f"[{len(v)} items]"
                else:
                    s[k] = v
        summary[nid] = s

    return {"run_id": run_id, "status": "completed", "results": summary}


async def _exec_generate_proposal(inp: dict) -> dict:
    """Gather observation proposal data: coordinates, visibility, ETC, literature."""
    target_name = inp.get("target_name", "")
    telescope = inp.get("telescope", "vlt").lower()
    instrument = inp.get("instrument", "")
    science_goal = inp.get("science_goal", "")
    exposure_hours = inp.get("exposure_hours")

    proposal: dict = {
        "target_name": target_name,
        "telescope": telescope,
        "instrument": instrument,
        "science_goal": science_goal,
        "requested_exposure_hours": exposure_hours,
    }

    # 1. Resolve target coordinates via SIMBAD
    ra, dec = None, None
    target_mag = None
    try:
        from astropy.coordinates import SkyCoord
        coord = SkyCoord.from_name(target_name)
        ra, dec = float(coord.ra.deg), float(coord.dec.deg)
        proposal["coordinates"] = {"ra_deg": round(ra, 6), "dec_deg": round(dec, 6),
                                   "ra_hms": coord.ra.to_string(unit="hour", sep=":"),
                                   "dec_dms": coord.dec.to_string(sep=":")}
    except Exception as e:
        proposal["coordinates"] = {"error": f"Could not resolve '{target_name}': {e}"}

    # Try to get magnitude and extra info from SIMBAD
    try:
        from app.connectors.simbad import SIMBADConnector
        simbad = SIMBADConnector()
        detail = await simbad.get_object_detail(target_name)
        if detail:
            proposal["object_type"] = detail.get("object_type", "unknown")
            proposal["redshift"] = detail.get("redshift")
            # Try to extract a V magnitude
            extra = detail.get("extra", {})
            if isinstance(extra, dict):
                for key in ("flux_V", "flux_v", "mag_V", "mag_v", "V", "phot_g_mean_mag"):
                    val = extra.get(key)
                    if val is not None:
                        try:
                            target_mag = float(val)
                            break
                        except (ValueError, TypeError):
                            pass
    except Exception:
        pass

    # 2. Compute visibility
    if ra is not None and dec is not None:
        try:
            from app.services.astro_analysis import target_visibility, _TELESCOPE_OBSERVATORY
            obs = _TELESCOPE_OBSERVATORY.get(telescope, "paranal")
            vis = target_visibility(ra, dec, observatory=obs)
            proposal["visibility"] = vis
        except Exception as e:
            proposal["visibility"] = {"error": str(e)}
    else:
        proposal["visibility"] = {"error": "No coordinates available"}

    # 3. Exposure time estimate (if magnitude available)
    if target_mag is not None:
        try:
            from app.services.astro_analysis import exposure_time_estimate
            etc_result = exposure_time_estimate(
                target_mag, snr_target=10, telescope=telescope
            )
            proposal["exposure_estimate"] = etc_result
        except Exception as e:
            proposal["exposure_estimate"] = {"error": str(e)}
    else:
        proposal["exposure_estimate"] = {
            "note": "No magnitude found for automatic ETC. "
                    "Use exposure_time_estimate() in run_python with a known magnitude."
        }

    # 4. Search ADS for recent papers on the target
    try:
        lit_result = await _exec_literature({"query": target_name})
        proposal["recent_literature"] = lit_result.get("results", [])[:5]
    except Exception:
        proposal["recent_literature"] = []

    # 5. Summary notes
    notes = []
    vis = proposal.get("visibility", {})
    if isinstance(vis, dict) and not vis.get("error"):
        hrs = vis.get("hours_observable", 0)
        if hrs < 2:
            notes.append(f"Warning: target only observable {hrs:.1f} hours (alt > 30 deg).")
        if vis.get("never_rises"):
            notes.append("Target never rises from this observatory — choose a different site.")
    if telescope in ("hst", "jwst"):
        notes.append("Space telescope — ground visibility is for reference only. "
                     "Check STScI APT for actual schedulability.")
    if exposure_hours and exposure_hours > 10:
        notes.append("Large time request — ensure strong scientific justification.")
    proposal["notes"] = notes

    return proposal


async def _exec_query_transients(inp: dict) -> dict:
    """Search TNS and Lasair for transients."""
    from app.services.transient_service import query_transients

    result = await query_transients(
        name=inp.get("name"),
        ra=inp.get("ra"),
        dec=inp.get("dec"),
        radius_arcsec=inp.get("radius_arcsec", 10),
        days_back=inp.get("days_back", 30),
        obj_type=inp.get("obj_type"),
    )
    return result


async def _exec_read_paper(inp: dict) -> dict:
    """Download an arXiv paper and extract text content."""
    import httpx
    import re

    arxiv_id = inp.get("arxiv_id", "").strip()
    if not arxiv_id:
        return {"error": "arxiv_id is required"}

    # Clean the ID (remove 'arXiv:' prefix if present)
    arxiv_id = re.sub(r'^arXiv:', '', arxiv_id, flags=re.IGNORECASE)

    # Fetch paper metadata from arXiv API
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"http://export.arxiv.org/api/query?id_list={arxiv_id}")
            resp.raise_for_status()
    except Exception as e:
        return {"error": f"Failed to fetch arXiv metadata: {e}"}

    xml_text = resp.text

    # Parse title and abstract from Atom XML
    title_match = re.search(r'<title[^>]*>(.*?)</title>', xml_text, re.DOTALL)
    abstract_match = re.search(r'<summary[^>]*>(.*?)</summary>', xml_text, re.DOTALL)
    authors = re.findall(r'<name>(.*?)</name>', xml_text)

    title = title_match.group(1).strip() if title_match else "Unknown"
    # Skip the first title match (it's the feed title "ArXiv Query")
    titles = re.findall(r'<title[^>]*>(.*?)</title>', xml_text, re.DOTALL)
    if len(titles) > 1:
        title = titles[1].strip()

    abstract = abstract_match.group(1).strip() if abstract_match else ""
    # Clean up whitespace
    abstract = re.sub(r'\s+', ' ', abstract)

    # Try to get the PDF and extract text (best-effort)
    pdf_text = ""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            pdf_resp = await client.get(f"https://arxiv.org/pdf/{arxiv_id}")
            if pdf_resp.status_code == 200 and len(pdf_resp.content) < 5_000_000:
                # Try to extract text from PDF
                try:
                    import io
                    from pdfminer.high_level import extract_text as _extract_pdf
                    pdf_text = _extract_pdf(io.BytesIO(pdf_resp.content))
                    # Truncate to first ~3000 chars (enough for intro + methods)
                    pdf_text = pdf_text[:3000]
                except ImportError:
                    pdf_text = "(pdfminer not installed — only abstract available)"
                except Exception:
                    pdf_text = "(PDF text extraction failed — only abstract available)"
    except Exception:
        pass

    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "authors": authors[:10],
        "abstract": abstract,
        "pdf_text_preview": pdf_text[:2000] if pdf_text else "",
        "url": f"https://arxiv.org/abs/{arxiv_id}",
    }


def _exec_research_workflow(inp: dict) -> dict:
    """Plan a structured research workflow from hypothesis to conclusion.

    This is a PLANNING tool — it returns a research plan that the AI then
    executes step-by-step using other tools (run_adql, run_python, search_objects).
    """
    hypothesis = inp.get("hypothesis", "")
    scope = inp.get("scope", "thorough")

    if not hypothesis.strip():
        return {"error": "hypothesis is required"}

    # Determine likely data sources based on keywords
    h_lower = hypothesis.lower()
    data_sources = []
    suggested_queries = []

    if any(kw in h_lower for kw in ["star", "stellar", "hr diagram", "parallax", "proper motion", "teff", "luminosity"]):
        data_sources.append("gaia")
        suggested_queries.append("SELECT TOP 1000 ... FROM gaiadr3.gaia_source WHERE ...")
    if any(kw in h_lower for kw in ["galaxy", "galaxies", "redshift", "agn", "quasar", "morpholog"]):
        data_sources.append("simbad")
        suggested_queries.append("SELECT TOP 500 ... FROM basic WHERE otype = 'G' AND rvz_redshift IS NOT NULL")
    if any(kw in h_lower for kw in ["spectrum", "spectral", "emission", "absorption", "line"]):
        data_sources.append("sdss")
    if any(kw in h_lower for kw in ["transient", "supernova", "sn ", "nova", "tde"]):
        data_sources.append("tns")
    if not data_sources:
        data_sources = ["simbad", "gaia"]

    # Build analysis steps based on scope
    analysis_steps = [
        "Acquire sample from database with appropriate selection criteria",
        "Clean data: remove NaN/null values, apply quality cuts",
        "Compute derived quantities (absolute magnitudes, colors, distances, etc.)",
        "Visualize distributions and relationships (histograms, scatter plots)",
    ]
    statistical_tests = ["Pearson/Spearman correlation"]
    suggested_plots = ["histogram", "scatter"]

    if scope == "thorough":
        analysis_steps.extend([
            "Split sample into subgroups if applicable",
            "Perform regression analysis with uncertainties",
            "Check for selection effects and systematic biases",
            "Compare results with published literature",
        ])
        statistical_tests.extend([
            "Linear regression (with bootstrap uncertainties)",
            "KS test (for distribution comparison)",
            "Bayesian analysis if sample size permits",
        ])
        suggested_plots.extend([
            "residual plot",
            "corner/contour plot",
            "comparison with literature values",
            "publication-ready summary figure",
        ])
    else:
        statistical_tests.append("Simple linear fit")
        suggested_plots.append("summary figure")

    return {
        "hypothesis_formal": f"Research question: {hypothesis}",
        "null_hypothesis": "H₀: No significant relationship/difference exists as hypothesized.",
        "data_sources": data_sources,
        "suggested_queries": suggested_queries,
        "analysis_steps": analysis_steps,
        "suggested_plots": suggested_plots,
        "statistical_tests": statistical_tests,
        "scope": scope,
        "next_action": "Begin Step 1: refine the hypothesis, then proceed to data acquisition using run_adql or search_objects.",
    }


async def _exec_estimate_photo_z(inp: dict) -> dict:
    """Run the unified photometric redshift estimator."""
    from app.services.photo_z import estimate_photo_z

    magnitudes = inp.get("magnitudes", {})
    mag_errors = inp.get("mag_errors", {})
    method = inp.get("method", "hybrid")

    if not magnitudes:
        return {"error": "magnitudes dict is required (e.g. {'g': 20.1, 'r': 19.5})"}

    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: estimate_photo_z(magnitudes, mag_errors, method=method),
            ),
            timeout=60.0,
        )
    except asyncio.TimeoutError:
        return {"error": "Photo-z estimation timed out after 60 seconds"}

    # Truncate pdf_z for the context window (keep z_phot, z_err, method, details)
    if "pdf_z" in result:
        result["pdf_z"] = f"[{len(result['pdf_z'])} values]"
    if "z_grid" in result:
        result["z_grid"] = f"[{len(result['z_grid'])} values]"
    # Also truncate nested detail arrays
    for sub_key in ("template", "ml"):
        sub = (result.get("details") or {}).get(sub_key)
        if isinstance(sub, dict):
            if "pdf_z" in sub:
                sub["pdf_z"] = f"[{len(sub['pdf_z'])} values]"
            if "z_grid" in sub:
                sub["z_grid"] = f"[{len(sub['z_grid'])} values]"

    return result


async def _exec_fit_isochrone(inp: dict) -> dict:
    """Execute isochrone fitting on observed CMD data."""
    bp_rp = inp.get("bp_rp", [])
    abs_mag = inp.get("abs_mag", [])
    method = inp.get("method", "grid")

    if not bp_rp or not abs_mag:
        return {"error": "bp_rp and abs_mag arrays are required"}
    if len(bp_rp) != len(abs_mag):
        return {"error": f"bp_rp ({len(bp_rp)}) and abs_mag ({len(abs_mag)}) must have the same length"}

    import asyncio
    from app.services.astro_analysis import fit_isochrone

    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: fit_isochrone(
                    bp_rp, abs_mag,
                    method=method,
                ),
            ),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        return {"error": "Isochrone fitting timed out after 120 seconds"}

    # Remove non-serializable items (matplotlib Figure)
    if "corner_fig" in result:
        del result["corner_fig"]

    return result


async def _exec_get_dossier(inp: dict) -> dict:
    """Generate a cross-match dossier for sky coordinates."""
    from app.services.dossier_generator import generate_dossier

    ra = inp.get("ra")
    dec = inp.get("dec")
    name = inp.get("name")

    if ra is None or dec is None:
        # Try to resolve from name via SIMBAD
        if name:
            try:
                from astropy.coordinates import SkyCoord
                coord = SkyCoord.from_name(name)
                ra = float(coord.ra.deg)
                dec = float(coord.dec.deg)
            except Exception as e:
                return {"error": f"ra and dec required (name resolution failed: {e})"}
        else:
            return {"error": "ra and dec are required (or provide a name for resolution)"}

    dossier = await generate_dossier(ra=ra, dec=dec, name=name)
    # Remove _raw to keep context window manageable
    dossier.pop("_raw", None)
    return dossier


async def _exec_get_followup(inp: dict) -> dict:
    """Generate follow-up observation recommendations for a transient."""
    from app.services.followup_recommender import generate_followup

    alert_id = inp.get("alert_id")
    ra = inp.get("ra")
    dec = inp.get("dec")
    magnitude = inp.get("magnitude")
    classification = inp.get("classification")

    alert_dict: dict | None = None

    # Try to look up from DB if alert_id is provided
    if alert_id:
        try:
            import uuid as _uuid
            from app.models.database import async_session
            from app.models.schemas import TransientAlert
            from sqlalchemy import select as sa_select

            async with async_session() as session:
                try:
                    uid = _uuid.UUID(alert_id)
                    stmt = sa_select(TransientAlert).where(TransientAlert.id == uid)
                except ValueError:
                    stmt = sa_select(TransientAlert).where(TransientAlert.source_id == alert_id)
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                if row:
                    alert_dict = {
                        "id": str(row.id),
                        "source_id": row.source_id,
                        "ra": row.ra,
                        "dec": row.dec,
                        "magnitude": row.magnitude,
                        "classification": row.classification,
                        "classification_confidence": row.classification_confidence,
                        "redshift": row.redshift,
                        "host_galaxy": row.host_galaxy,
                        "discovery_date": row.discovery_date.isoformat() if row.discovery_date else None,
                        "raw_data": row.raw_data,
                    }
        except Exception as exc:
            logger.debug("Alert DB lookup in tool failed: %s", exc)

    # Build alert dict from params if no DB record found
    if alert_dict is None:
        if ra is None or dec is None:
            return {
                "error": (
                    "Could not find alert in database. "
                    "Provide ra and dec for ad-hoc recommendations."
                )
            }
        alert_dict = {
            "source_id": alert_id or "ad-hoc",
            "ra": ra,
            "dec": dec,
            "magnitude": magnitude,
            "classification": classification,
        }
    else:
        # Apply any overrides
        if ra is not None:
            alert_dict["ra"] = ra
        if dec is not None:
            alert_dict["dec"] = dec
        if magnitude is not None:
            alert_dict["magnitude"] = magnitude
        if classification is not None:
            alert_dict["classification"] = classification

    # Generate dossier for enrichment (best-effort)
    dossier = None
    try:
        from app.services.dossier_generator import generate_dossier
        dossier = await generate_dossier(ra=alert_dict["ra"], dec=alert_dict["dec"])
        dossier.pop("_raw", None)
    except Exception:
        pass

    result = await generate_followup(alert=alert_dict, dossier=dossier)
    return result


async def _exec_cross_wavelength(inp: dict) -> dict:
    """Run cross-wavelength anomaly detection on a sky position."""
    from app.services.cross_wavelength import cross_wavelength_analysis

    ra = inp.get("ra")
    dec = inp.get("dec")
    name = inp.get("name")

    if ra is None or dec is None:
        # Try to resolve from name via SIMBAD
        if name:
            try:
                from astropy.coordinates import SkyCoord
                coord = SkyCoord.from_name(name)
                ra = float(coord.ra.deg)
                dec = float(coord.dec.deg)
            except Exception as e:
                return {"error": f"ra and dec required (name resolution failed: {e})"}
        else:
            return {"error": "ra and dec are required (or provide a name for resolution)"}

    result = await cross_wavelength_analysis(ra=ra, dec=dec)
    return result


async def _exec_crossmatch_catalogs(inp: dict, python_session_id: str = "default") -> dict:
    """Cross-match two catalogs obtained via ADQL queries."""
    import pandas as pd
    from app.api.integration import adql_query, ADQLRequest
    from app.services.crossmatch_engine import get_crossmatch_engine

    q1 = inp.get("table1_query", "")
    q2 = inp.get("table2_query", "")
    radius = inp.get("radius_arcsec", 3.0)
    join_type = inp.get("join_type", "1and2")
    service1 = inp.get("service1", "gaia")
    service2 = inp.get("service2", "gaia")

    if not q1 or not q2:
        return {"error": "Both table1_query and table2_query are required"}

    # Run both ADQL queries concurrently
    async def _run_adql(query: str, service: str) -> pd.DataFrame:
        result = await adql_query(ADQLRequest(query=query, service=service))
        data = result.get("data", {}) if isinstance(result, dict) else {}
        if not data:
            raise ValueError(f"ADQL query returned no data: {query[:80]}...")
        return pd.DataFrame(data)

    try:
        t1, t2 = await asyncio.gather(
            _run_adql(q1, service1),
            _run_adql(q2, service2),
        )
    except Exception as e:
        return {"error": f"Failed to fetch catalogs: {e}"}

    # Validate required columns
    for label, tbl in [("table1", t1), ("table2", t2)]:
        if "ra" not in tbl.columns or "dec" not in tbl.columns:
            return {
                "error": (
                    f"{label} query must return 'ra' and 'dec' columns. "
                    f"Got: {list(tbl.columns)}"
                )
            }

    # Run cross-match
    engine = get_crossmatch_engine()
    try:
        matched = await engine.crossmatch(
            t1, t2, radius_arcsec=radius, join=join_type, find="best"
        )
    except Exception as e:
        return {"error": f"Cross-match failed: {e}"}

    # Truncate for context window
    max_rows = 200
    total = len(matched)
    truncated = matched.head(max_rows)

    # Convert to serializable dicts, replacing NaN with None
    rows = truncated.where(truncated.notna(), None).to_dict(orient="records")

    # Cache for the Python sandbox
    store_search_results("latest_crossmatch", rows)
    store_search_results(f"latest_crossmatch:{python_session_id}", rows)

    return {
        "match_count": total,
        "showing": min(max_rows, total),
        "columns": list(matched.columns),
        "rows": rows,
        "join_type": join_type,
        "radius_arcsec": radius,
    }


async def _exec_search_lightcurve(inp: dict) -> dict:
    """Execute search_lightcurve from astro_analysis."""
    from app.services.astro_analysis import search_lightcurve

    target = inp.get("target", "")
    mission = inp.get("mission", "kepler")
    if not target:
        return {"error": "target is required"}

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: search_lightcurve(target, mission=mission))
    return result


async def _exec_extract_sources(inp: dict) -> dict:
    """Load a FITS image and extract sources using SEP."""
    import os
    from app.services.astro_analysis import extract_sources

    fits_path = inp.get("fits_path", "")
    threshold_sigma = inp.get("threshold_sigma", 3.0)
    min_area = inp.get("min_area", 5)

    if not fits_path:
        return {"error": "fits_path is required"}

    # Resolve path relative to data directory
    base_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data")
    full_path = os.path.normpath(os.path.join(base_dir, fits_path))
    if not os.path.isfile(full_path):
        # Try as absolute path
        full_path = fits_path
    if not os.path.isfile(full_path):
        return {"error": f"FITS file not found: {fits_path}"}

    from astropy.io import fits as afits

    loop = asyncio.get_event_loop()

    def _do_extract():
        with afits.open(full_path) as hdul:
            # Find first image extension
            image_data = None
            for hdu in hdul:
                if hdu.data is not None and hdu.data.ndim == 2:
                    image_data = hdu.data
                    break
            if image_data is None:
                return {"error": "No 2D image extension found in FITS file"}
            return extract_sources(image_data, threshold_sigma=threshold_sigma, min_area=min_area)

    result = await loop.run_in_executor(None, _do_extract)
    return result
