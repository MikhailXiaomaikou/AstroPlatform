"""Tool definitions and executors for the Claude AI research agent.

Each tool is a function Claude can call. The agent loop in chat.py
handles the tool_use → result → next message cycle automatically.
"""

import asyncio
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── Tool Definitions (Anthropic tool_use format) ──

# In-memory cache for search/query results per runtime session (keyed by a simple token).
# Stores tuples of (value, timestamp) for TTL-based expiry.
_search_result_cache: dict[str, tuple[Any, float]] = {}
MAX_ADQL_RESULT_HISTORY = 8
_CACHE_TTL_SECONDS = 1800  # 30 minutes


def store_search_results(key: str, results: Any) -> None:
    """Cache search results so AI can access full data later."""
    _search_result_cache[key] = (results, time.time())
    # Evict expired entries first
    now = time.time()
    expired = [k for k, (_, ts) in _search_result_cache.items() if now - ts > _CACHE_TTL_SECONDS]
    for k in expired:
        del _search_result_cache[k]
    # Keep only the latest cache entries; multiple keys are used per runtime session.
    if len(_search_result_cache) > 200:
        oldest = list(_search_result_cache.keys())[0]
        del _search_result_cache[oldest]


def get_cached_results(key: str) -> Any | None:
    entry = _search_result_cache.get(key)
    if entry is None:
        return None
    value, timestamp = entry
    if time.time() - timestamp > _CACHE_TTL_SECONDS:
        del _search_result_cache[key]
        return None
    return value


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        numeric = float(value)
        if numeric != numeric:  # NaN
            return None
        return numeric
    except (TypeError, ValueError):
        return None


def _augment_adql_row(row: dict[str, Any]) -> dict[str, Any]:
    # Normalize keys to lowercase (TAP services may return UPPERCASE)
    enriched = {k.lower(): v for k, v in row.items()}
    if "bp_rp" not in enriched or enriched.get("bp_rp") is None:
        bp = _coerce_float(enriched.get("phot_bp_mean_mag"))
        rp = _coerce_float(enriched.get("phot_rp_mean_mag"))
        if bp is not None and rp is not None:
            enriched["bp_rp"] = bp - rp
    if "abs_g_mag" not in enriched or enriched.get("abs_g_mag") is None:
        g_mag = _coerce_float(enriched.get("phot_g_mean_mag"))
        parallax = _coerce_float(enriched.get("parallax"))
        if g_mag is not None and parallax is not None and parallax > 0:
            distance_pc = 1000.0 / parallax
            enriched["abs_g_mag"] = g_mag - 5.0 * (math.log10(distance_pc) - 1.0)
    return enriched


def build_adql_rows(
    columns: list[str],
    data: dict[str, list[Any]],
    row_count: int,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(min(row_count, limit)):
        row = {
            col: (data.get(col, [None] * row_count)[index] if index < len(data.get(col, [])) else None)
            for col in columns
        }
        rows.append(_augment_adql_row(row))
    return rows


def augment_adql_payload(
    columns: list[str],
    data: dict[str, list[Any]],
    row_count: int,
    limit: int = 1000,
) -> tuple[list[str], dict[str, list[Any]], list[dict[str, Any]]]:
    rows = build_adql_rows(columns, data, row_count, limit=limit)
    augmented_columns = list(columns)
    derived_columns = ["bp_rp", "abs_g_mag"]
    for col in derived_columns:
        if col not in augmented_columns and any(row.get(col) is not None for row in rows):
            augmented_columns.append(col)
            data[col] = [row.get(col) for row in rows]
    return augmented_columns, data, rows


def build_adql_result_set(
    *,
    service: str,
    query: str,
    columns: list[str],
    data: dict[str, list[Any]],
    row_count: int,
    limit: int = 1000,
) -> dict[str, Any]:
    augmented_columns, augmented_data, rows = augment_adql_payload(
        list(columns),
        dict(data),
        row_count,
        limit=limit,
    )
    return {
        "service": service,
        "query": query,
        "row_count": row_count,
        "columns": augmented_columns,
        "rows": rows,
        "data": {col: augmented_data.get(col, [])[: min(row_count, limit)] for col in augmented_columns},
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }


def _session_cache_key(prefix: str, session_id: str | None) -> str | None:
    sid = str(session_id or "").strip()
    if not sid or sid == "default":
        return None
    return f"{prefix}:{sid}"


def replace_adql_result_sets(session_id: str | None, result_sets: list[dict[str, Any]]) -> None:
    normalized = [dict(item) for item in result_sets[-MAX_ADQL_RESULT_HISTORY:] if isinstance(item, dict)]
    latest = normalized[-1] if normalized else None
    latest_rows = list(latest.get("rows", [])) if latest else []

    session_sets_key = _session_cache_key("latest_adql_sets", session_id)
    session_set_key = _session_cache_key("latest_adql_set", session_id)
    session_rows_key = _session_cache_key("latest_adql", session_id)
    if not session_sets_key:
        store_search_results("latest_adql_sets", normalized)
        if latest is not None:
            store_search_results("latest_adql_set", latest)
            store_search_results("latest_adql", latest_rows)

    if session_sets_key:
        store_search_results(session_sets_key, normalized)
    if latest is not None and session_set_key and session_rows_key:
        store_search_results(session_set_key, latest)
        store_search_results(session_rows_key, latest_rows)


def store_adql_result_set(session_id: str | None, result_set: dict[str, Any]) -> None:
    existing = get_cached_results(_session_cache_key("latest_adql_sets", session_id) or "latest_adql_sets")
    history = list(existing) if isinstance(existing, list) else []
    history.append(dict(result_set))
    replace_adql_result_sets(session_id, history)


TOOLS = [
    {
        "name": "search_objects",
        "description": (
            "Search astronomical databases for objects by name, coordinates, or scientific criteria. "
            "Searches SIMBAD, Gaia, SDSS, NED, LAMOST, etc. Returns object names, positions, types, magnitudes, redshifts. "
            "Gaia results include extra fields: extra.bp_rp, extra.parallax, extra.pmra, extra.pmdec, "
            "extra.ruwe, extra.phot_bp_mean_mag, extra.phot_rp_mean_mag. "
            "LAMOST results include spectroscopic parameters in extra fields. "
            "Use these for HR diagrams, membership selection, and isochrone fitting."
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
                "extended_timeout": {"type": "boolean", "description": "Optional: true for paper-scale queries that need the long workflow ADQL budget."},
            },
            "required": ["query", "service"],
        },
    },
    {
        "name": "query_high_velocity_stars",
        "description": (
            "Fetch a focused Gaia DR3 high-tangential-velocity candidate sample for "
            "Milky Way escape-velocity / halo-star workflows. Use this BEFORE broad "
            "Gaia source-table scans. It queries VizieR Gaia DR3 with parallax and "
            "proper-motion cuts, computes vtan_kms, caches rows under latest_adql, "
            "and returns a clear caveat that this is an accessible candidate sample, "
            "not the full literature halo-star selection."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum rows to return/cache (default 500, max 2000)."},
                "min_parallax_mas": {"type": "number", "description": "Minimum positive parallax in mas (default 0.2)."},
                "min_vtan_kms": {"type": "number", "description": "Minimum tangential velocity in km/s (default 250)."},
                "require_radial_velocity": {"type": "boolean", "description": "Require Gaia radial velocity RV to be present (default false; true is cleaner but much smaller)."},
                "extended_timeout": {"type": "boolean", "description": "Optional: use the long workflow ADQL budget."},
            },
        },
    },
    {
        # J3: SDSS 官方 SkyServer 直连.  VizieR 全部 mirror 不可达或
        # V/154/sdss17 表 "All mirrors unavailable" 时切到这个工具 —
        # 不经 VizieR, 直接打 SDSS 原厂 API.  语法是 T-SQL (微软 SQL
        # Server), 不是 ADQL.
        "name": "run_sdss_sql",
        "description": (
            "Execute a T-SQL query directly against SDSS SkyServer (DR18 by default). "
            "USE THIS when VizieR mirrors are unavailable, or when you need SDSS-specific "
            "tables not exposed via VizieR (e.g. GalSpecInfo / GalSpecExtra / Photoz). "
            "SYNTAX is T-SQL not ADQL: use `TOP N` not `LIMIT`, "
            "`dbo.fGetNearbyObjEq(ra, dec, radius_arcmin)` for cone search, and ALWAYS "
            "filter on `mode = 1 AND clean = 1` to drop artefacts. "
            "Common tables: PhotoObjAll (photometry), SpecObjAll (spectroscopy), "
            "Photoz (photometric redshifts), GalSpecInfo + GalSpecExtra (MPA-JHU "
            "galaxy parameters). Columns are `objID`, `ra`, `dec`, `u`/`g`/`r`/`i`/`z` "
            "(model magnitudes), `petroMag_u..z`, `z` (spec-z in SpecObj), etc. "
            "Full result rows are cached under key `latest_sdss_sql` for run_python."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "T-SQL query string"},
                "dr": {"type": "string", "enum": ["18", "17", "16"], "description": "SDSS Data Release (default '18')"},
                "extended_timeout": {"type": "boolean", "description": "Optional: true for paper-scale SDSS queries in long workflow mode."},
            },
            "required": ["query"],
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
        "name": "search_literature",
        "description": (
            "Search NASA ADS for academic papers about an astronomical object or topic, "
            "with arXiv fallback when ADS has no key/results. Returns titles, authors, "
            "years, abstracts, and source metadata that you can cite in your response."
        ),
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
        "name": "validate_analysis",
        "description": (
            "Run a scientific rigor audit on the current saved chat session before paper export. "
            "Use this to check unit consistency, statistical assumptions, provenance, and completeness."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Saved chat session ID. If omitted, the current saved session ID is inferred from the Python session when possible.",
                },
            },
        },
    },
    {
        "name": "generate_paper_draft",
        "description": (
            "Generate a structured paper draft from a saved chat session. "
            "Use when the user asks to write up results, create a manuscript, or export analysis as a paper."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Saved chat session ID. If omitted, the current saved session ID is inferred from the Python session when possible.",
                },
                "journal_format": {
                    "type": "string",
                    "enum": ["aastex", "mnras", "aa"],
                    "description": "Target journal format. Ask the user if they have a preference; default is aastex.",
                },
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
            "get_adql_results() returns only the latest ADQL rows as list[dict], "
            "get_adql_result_sets() returns recent ADQL result sets with query/service metadata, "
            "astro.search_lightcurve(...) returns list[dict]; access the first row as results[0]['mission'] "
            "or results[0].get('sector'). astro.download_and_clean_lightcurve(...) returns a dict with "
            "keys ['time', 'flux', 'flux_err', 'meta']. astro.phase_fold(time, flux, period, t0) returns "
            "a PhaseFoldResult supporting `phase, flux_folded = result`, `result.phase`, and result['phase']. "
            "load_votable(path) loads a VOTable as an astropy Table, "
            "load_csv(path) loads a CSV as a pandas DataFrame, "
            "process_in_chunks(data, chunk_size, func) processes large data in memory-safe chunks, "
            "memory_usage_mb() returns current memory usage in MB. "
            "Use print() to output results. Matplotlib figures are automatically captured. "
            "Max execution time: 75s (normal), 30s (fast), 300s (slow — declare mode='slow' "
            "for BLS / MCMC / large bootstrap / grid searches). "
            "REQUIRED: You MUST declare `data_source` — where the code's input data comes from. "
            "If you are NOT analyzing real observational data (e.g. demonstrating a formula, "
            "generating a plot template, listing helper functions with available_functions(), "
            "or checking helper signatures before a real analysis), declare "
            "`data_source=\"none_not_analyzing_real_data\"`. The system will mark the output as "
            "SYNTHETIC and the zero-fabrication gate will block citing its numbers."
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
                "data_source": {
                    "type": "string",
                    "description": (
                        "G1: where this code's data comes from. REQUIRED. One of: "
                        "'latest_adql' (uses get_adql_results / get_adql_result_sets), "
                        "'latest_search' (uses get_search_results), "
                        "'latest_lightcurve' / 'cached:<key>' (other cached real data), "
                        "'latest_high_velocity_stars' (uses the focused Gaia high-velocity cache), "
                        "'fits:<path>' (loads a specific FITS file via load_fits), "
                        "'none_not_analyzing_real_data' (not analyzing observational data: "
                        "synthetic/pedagogical/Monte-Carlo data, or helper introspection such as "
                        "available_functions(); output will be marked SYNTHETIC/not citable for "
                        "scientific numbers). "
                        "If a real data-fetch tool FAILED earlier this turn, choosing "
                        "'none_not_analyzing_real_data' to replace it is forbidden — emit "
                        "<tools_returned_nothing/> instead."
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["fast", "normal", "slow"],
                    "description": (
                        "G5: execution-time budget. `fast` = 30s (small string / table "
                        "manipulation), `normal` = 75s (default), `slow` = 300s (BLS, MCMC, "
                        "bootstrap, PARSEC grid, large cross-matches). Default normal if omitted."
                    ),
                },
            },
            "required": ["code", "data_source"],
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
            "Estimate photometric redshift from multi-band magnitudes using SED template fitting. "
            "WARNING: Demo-only implementation with 7 templates and no dust extinction — not "
            "suitable for scientific use. Use EAZY or Le Phare for publication-quality photo-z."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "magnitudes": {
                    "type": "object",
                    "description": (
                        "Dict mapping filter band names to magnitudes. Supported bands: "
                        "sdss_u, sdss_g, sdss_r, sdss_i, sdss_z, twomass_j, twomass_h, "
                        "twomass_ks, wise_w1, wise_w2. "
                        "Example: {\"sdss_g\": 20.1, \"sdss_r\": 19.5, \"sdss_i\": 19.2}"
                    ),
                },
            },
            "required": ["magnitudes"],
        },
    },
    {
        "name": "fit_isochrone",
        "description": (
            "Fit isochrones to determine star cluster age, metallicity, distance, and extinction. "
            "JUST CALL THIS TOOL with no parameters after running a Gaia search — it automatically "
            "extracts bp_rp and absolute magnitudes from the last search results, estimates distance "
            "modulus from parallax, and fits extinction. If PARSEC API is slow, it falls back to "
            "turnoff-based age estimation (brighter turnoff → higher mass → YOUNGER cluster). "
            "DO NOT pass bp_rp or abs_mag as parameters — let the tool extract them automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["grid", "mcmc"],
                    "description": "Fitting method: 'grid' (default, fast) or 'mcmc' (slow, with uncertainties)",
                },
            },
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
        "name": "reduce_ccd_image",
        "description": (
            "Run a standard CCD reduction on a science FITS image using any available bias, dark, and flat frames. "
            "Returns the reduced output FITS path and a reduction log."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "science_fits_path": {"type": "string", "description": "Science FITS image to reduce"},
                "bias_paths": {"type": "array", "items": {"type": "string"}, "description": "Optional bias frame paths"},
                "dark_paths": {"type": "array", "items": {"type": "string"}, "description": "Optional dark frame paths"},
                "flat_paths": {"type": "array", "items": {"type": "string"}, "description": "Optional flat frame paths"},
                "cosmic_ray": {"type": "boolean", "description": "Whether to clean cosmic rays (default true)"},
            },
            "required": ["science_fits_path"],
        },
    },
    {
        "name": "solve_astrometry",
        "description": "Plate-solve a FITS image via astrometry.net and return WCS metadata when available.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fits_path": {"type": "string", "description": "FITS path to solve"},
            },
            "required": ["fits_path"],
        },
    },
    {
        "name": "extract_photometry",
        "description": (
            "Extract sources and run basic aperture photometry on a reduced FITS image. "
            "Returns a source catalog with x/y, fluxes, instrumental magnitudes, and RA/Dec if WCS is present."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fits_path": {"type": "string", "description": "Reduced FITS path"},
                "aperture_radii": {"type": "array", "items": {"type": "integer"}, "description": "Aperture radii in pixels"},
            },
            "required": ["fits_path"],
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
    {
        "name": "classify_transient",
        "description": (
            "Classify a transient event from light curve data. Accepts pre-extracted features dict "
            "OR raw light curve arrays (times, magnitudes, mag_errors). Returns classification "
            "(SN_Ia, SN_II, SN_Ib_c, TDE, CV, AGN, KN, LPV, Unknown), confidence score, and "
            "per-class probabilities. WARNING: Uses synthetic training data — verify results "
            "with spectroscopy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "features": {
                    "type": "object",
                    "description": "Pre-extracted light curve features dict (rise_time, decline_rate, peak_mag, etc.)",
                },
                "times": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "MJD observation times (alternative to features)",
                },
                "magnitudes": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Magnitude measurements",
                },
                "mag_errors": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Magnitude uncertainties",
                },
            },
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
    {
        "name": "query_vo_service",
        "description": "Query Virtual Observatory services: SIA (images), SSA (spectra), federated TAP (multi-archive ADQL), or discover services. Use for accessing professional astronomical archives beyond the built-in connectors.",
        "input_schema": {
            "type": "object",
            "properties": {
                "protocol": {"type": "string", "enum": ["sia", "ssa", "tap", "discover"],
                            "description": "VO protocol to use"},
                "ra": {"type": "number", "description": "RA in degrees"},
                "dec": {"type": "number", "description": "Dec in degrees"},
                "radius": {"type": "number", "description": "Search radius in degrees"},
                "adql": {"type": "string", "description": "ADQL query (for TAP)"},
                "services": {"type": "array", "items": {"type": "string"},
                            "description": "TAP services to query (gaia, simbad, vizier, cadc, ned)"},
                "service_url": {"type": "string", "description": "Custom SIA/SSA service URL"},
                "keyword": {"type": "string", "description": "Keyword for service discovery"},
            },
            "required": ["protocol"],
        },
    },
    {
        "name": "process_image",
        "description": "Advanced astronomical image processing: reprojection, mosaicking, PSF matching, source deblending, and cutout extraction.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fits_path": {"type": "string", "description": "Path to FITS image"},
                "operation": {"type": "string", "enum": ["reproject", "mosaic", "psf_match", "deblend", "cutout"]},
                "params": {"type": "object", "description": "Operation-specific parameters"},
            },
            "required": ["fits_path", "operation"],
        },
    },
    # ── Time-Domain Tools ──
    {
        "name": "gp_detrend_lightcurve",
        "description": "Detrend a light curve using Gaussian Process regression (celerite2). Removes stellar variability to reveal transits and flares.",
        "input_schema": {
            "type": "object",
            "properties": {
                "time": {"type": "array", "items": {"type": "number"}, "description": "Time array"},
                "flux": {"type": "array", "items": {"type": "number"}, "description": "Flux array"},
                "flux_err": {"type": "array", "items": {"type": "number"}},
                "kernel": {"type": "string", "enum": ["matern32", "sho", "rotation"]},
            },
            "required": ["time", "flux"],
        },
    },
    {
        "name": "fit_transit_model",
        "description": "Fit an exoplanet transit model (batman) to light curve data. Returns planet-to-star radius ratio, semi-major axis, inclination.",
        "input_schema": {
            "type": "object",
            "properties": {
                "time": {"type": "array", "items": {"type": "number"}},
                "flux": {"type": "array", "items": {"type": "number"}},
                "flux_err": {"type": "array", "items": {"type": "number"}},
                "period": {"type": "number", "description": "Orbital period"},
                "t0": {"type": "number", "description": "Mid-transit time"},
                "rp_rs": {"type": "number", "description": "Planet/star radius ratio initial guess (default 0.1)"},
            },
            "required": ["time", "flux", "period"],
        },
    },
    {
        "name": "detect_stellar_flares",
        "description": "Detect stellar flares in a light curve. Returns flare times, amplitudes, durations, and energies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "time": {"type": "array", "items": {"type": "number"}},
                "flux": {"type": "array", "items": {"type": "number"}},
                "flux_err": {"type": "array", "items": {"type": "number"}},
                "nsigma": {"type": "number", "description": "Detection threshold in sigma (default 3.0)"},
            },
            "required": ["time", "flux"],
        },
    },
    {
        "name": "transit_search_bls",
        "description": "Search for periodic transits using Box Least Squares (BLS) periodogram. Returns best period, depth, and signal-to-noise.",
        "input_schema": {
            "type": "object",
            "properties": {
                "time": {"type": "array", "items": {"type": "number"}},
                "flux": {"type": "array", "items": {"type": "number"}},
                "period_min": {"type": "number", "description": "Min period to search (default 0.5)"},
                "period_max": {"type": "number", "description": "Max period to search (default 20.0)"},
            },
            "required": ["time", "flux"],
        },
    },
    # ── Team/Workspace Tools ──
    {
        "name": "share_with_team",
        "description": "Share a pipeline template or dataset with a team member by email.",
        "input_schema": {
            "type": "object",
            "properties": {
                "resource_type": {"type": "string", "enum": ["pipeline", "dataset", "results"]},
                "resource_id": {"type": "string"},
                "email": {"type": "string"},
                "permission": {"type": "string", "enum": ["view", "edit"]},
            },
            "required": ["resource_type", "resource_id", "email"],
        },
    },
    {
        "name": "invite_team_member",
        "description": "Invite a new member to your team workspace by email. Returns actionable instructions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Email address to invite"},
                "role": {"type": "string", "enum": ["viewer", "editor", "admin"], "description": "Role for the new member (default: viewer)"},
            },
            "required": ["email"],
        },
    },
    # ── FITS/Export/Provenance Tools ──
    {
        "name": "read_fits_header",
        "description": "Read FITS file headers, HDU structure, and column names. Use to inspect uploaded FITS files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fits_path": {"type": "string", "description": "Storage path to FITS file"},
                "hdu": {"type": "integer", "description": "HDU index (default 0)"},
            },
            "required": ["fits_path"],
        },
    },
    {
        "name": "export_results",
        "description": "Export pipeline run results as CSV, VOTable, FITS, or Jupyter notebook.",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "format": {"type": "string", "enum": ["csv", "votable", "notebook", "fits"]},
            },
            "required": ["run_id", "format"],
        },
    },
    {
        "name": "get_provenance",
        "description": "Get data provenance lineage or reproducibility package for a pipeline entity.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "action": {"type": "string", "enum": ["lineage", "reproduce", "doi_metadata"]},
            },
            "required": ["entity_id"],
        },
    },
    # ── Photo-Z Pro + Batch Tools ──
    {
        "name": "estimate_photo_z_pro",
        "description": (
            "Professional photometric redshift with 30 SED templates, Calzetti dust, Madau IGM, "
            "emission lines, and Bayesian priors. Superior to basic photo-z."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "magnitudes": {"type": "object", "description": "Band name to magnitude mapping (e.g. {g: 22.1, r: 21.5})"},
                "mag_errors": {"type": "object"},
                "prior": {"type": "string", "enum": ["flat", "magnitude"]},
                "z_max": {"type": "number"},
            },
            "required": ["magnitudes"],
        },
    },
    {
        "name": "batch_object_search",
        "description": "Search multiple astronomical targets simultaneously across databases.",
        "input_schema": {
            "type": "object",
            "properties": {
                "targets": {"type": "array", "items": {"type": "string"}, "description": "List of target names"},
                "sources": {"type": "array", "items": {"type": "string"}, "description": "Databases to query"},
                "radius": {"type": "number"},
            },
            "required": ["targets"],
        },
    },
    {
        "name": "workspace_export",
        "description": "Export workspace data in CSV or VOTable format.",
        "input_schema": {
            "type": "object",
            "properties": {
                "data": {"type": "array", "description": "Data rows to export"},
                "format": {"type": "string", "enum": ["csv", "votable"]},
                "columns": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["data", "format"],
        },
    },
    {
        "name": "classify_transient_spectrum",
        "description": "Classify a transient using spectroscopic template matching. Matches against SN Ia, SN II, SN Ib/c, TDE, AGN, and Nova templates. Also enriches with host galaxy info.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wavelength": {"type": "array", "items": {"type": "number"}, "description": "Wavelength array (Angstrom)"},
                "flux": {"type": "array", "items": {"type": "number"}, "description": "Flux array"},
                "redshift": {"type": "number", "description": "Known redshift (default 0)"},
                "ra": {"type": "number", "description": "RA for host galaxy lookup"},
                "dec": {"type": "number", "description": "Dec for host galaxy lookup"},
            },
            "required": ["wavelength", "flux"],
        },
    },
    {
        "name": "literature_review",
        "description": "Build a citation network and synthesize a bibliography for a research topic. Returns relevant papers, citation graph, and AI-ready context for literature review writing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Research topic or object name"},
                "findings": {"type": "string", "description": "User's findings to contextualize"},
                "max_papers": {"type": "integer", "description": "Maximum papers to include (default 10)"},
                "build_network": {"type": "boolean", "description": "Build citation network graph (default false)"},
                "seed_bibcodes": {"type": "array", "items": {"type": "string"}, "description": "Seed papers for citation network"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "radio_analysis",
        "description": "Radio astronomy analysis: compute spectral index, radio luminosity, and cross-match across radio surveys (NVSS, FIRST).",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["spectral_index", "luminosity", "crossmatch"],
                             "description": "Analysis operation"},
                "ra": {"type": "number"}, "dec": {"type": "number"},
                "flux_mJy": {"type": "number"}, "redshift": {"type": "number"},
                "freq_MHz": {"type": "number"},
                "flux_1_mJy": {"type": "number"}, "freq_1_MHz": {"type": "number"},
                "flux_2_mJy": {"type": "number"}, "freq_2_MHz": {"type": "number"},
            },
            "required": ["operation"],
        },
    },
    {
        "name": "full_research_report",
        "description": "One-click publication package: validate analysis → generate paper draft → provide export links for notebook, CSV, VOTable, and LaTeX. Call this after completing an analysis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Chat session ID"},
                "title": {"type": "string", "description": "Paper title"},
                "journal": {"type": "string", "enum": ["aastex", "mnras", "aa"], "description": "Journal format"},
            },
            "required": ["session_id"],
        },
    },
    # ── P1/P2 workflow tools (knowledge base expansion) ──
    {
        "name": "compute_galaxy_sfr",
        "description": (
            "Compute galaxy star formation rate from luminosity in any of 7 bands, "
            "using the Kennicutt & Evans 2012 ARA&A 50, 531 Table 1 calibrations "
            "(Kroupa IMF, 0.1-100 Msun). Provide luminosities for whichever bands "
            "are available; returns SFR from each band plus a weighted average. "
            "Applies optional dust correction if Balmer decrement or UV slope is given."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "L_Halpha": {"type": "number", "description": "H-alpha luminosity in erg/s (extinction corrected)"},
                "L_FUV": {"type": "number", "description": "FUV νL_ν at 1500 Å in erg/s"},
                "L_NUV": {"type": "number", "description": "NUV νL_ν at 2300 Å in erg/s"},
                "L_TIR": {"type": "number", "description": "Total IR (8-1000 μm) luminosity in erg/s"},
                "L_24um": {"type": "number", "description": "νL_ν at 24 μm in erg/s"},
                "L_70um": {"type": "number", "description": "νL_ν at 70 μm in erg/s"},
                "L_1p4GHz": {"type": "number", "description": "1.4 GHz radio L_ν in erg/s/Hz"},
                "Ha_Hb_ratio": {"type": "number", "description": "Observed Hα/Hβ ratio (for Balmer decrement dust correction)"},
                "uv_beta": {"type": "number", "description": "UV spectral slope β (for Meurer+ 1999 dust correction)"},
            },
        },
    },
    {
        "name": "fit_rv_orbit",
        "description": (
            "Fit a Keplerian radial velocity orbit (exoplanet or spectroscopic binary). "
            "Uses radvel (Fulton+ 2018 PASP 130, 044504) for well-sampled data or "
            "the-joker (Price-Whelan+ 2017 ApJ 837, 20) for sparse binary sampling. "
            "Returns best-fit Keplerian parameters (P, K, e, ω, t_p) plus mass function."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "times": {"type": "array", "items": {"type": "number"}, "description": "Observation times (JD or BJD)"},
                "rvs": {"type": "array", "items": {"type": "number"}, "description": "Radial velocities in m/s"},
                "rv_errs": {"type": "array", "items": {"type": "number"}, "description": "RV uncertainties in m/s"},
                "period_min": {"type": "number", "description": "Minimum search period in days"},
                "period_max": {"type": "number", "description": "Maximum search period in days"},
                "method": {"type": "string", "enum": ["radvel", "thejoker"], "description": "radvel for dense sampling, thejoker for sparse"},
            },
            "required": ["times", "rvs", "rv_errs"],
        },
    },
    {
        "name": "fit_sersic_morphology",
        "description": (
            "Fit a Sersic profile to a galaxy image and compute non-parametric morphology "
            "(Gini, M20, concentration, asymmetry, smoothness). Uses statmorph "
            "(Rodriguez-Gomez+ 2019 MNRAS 483, 4140). Input is a 2D image array or FITS path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fits_path": {"type": "string", "description": "Path to FITS image"},
                "segmap": {"type": "string", "description": "Optional segmentation map FITS path"},
                "psf": {"type": "string", "description": "Optional PSF FITS path for convolution"},
                "ra": {"type": "number", "description": "Source RA (if extracting cutout)"},
                "dec": {"type": "number", "description": "Source Dec"},
                "size_arcsec": {"type": "number", "description": "Cutout size in arcsec"},
            },
            "required": ["fits_path"],
        },
    },
    {
        "name": "x_ray_spectral_fit",
        "description": (
            "Fit a Sherpa spectral model to an X-ray PHA file (Chandra, XMM, NuSTAR, etc). "
            "Standard models: phabs*powerlaw (AGN), phabs*apec (thermal plasma), "
            "phabs*(diskbb+powerlaw) (XRB). Uses Sherpa (Doe+ 2007 ASP 376, 543). "
            "Returns best-fit parameters with 90% confidence intervals and reduced chi2."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pha_path": {"type": "string", "description": "Path to PHA (spectrum) file with linked RMF/ARF"},
                "model": {
                    "type": "string",
                    "description": "Sherpa model expression, e.g. 'xsphabs.abs1 * xspowerlaw.pl' or 'xsphabs.abs1 * xsapec.thermal'",
                },
                "energy_min": {"type": "number", "description": "Min energy in keV (default 0.5)"},
                "energy_max": {"type": "number", "description": "Max energy in keV (default 8.0)"},
                "statistic": {"type": "string", "enum": ["chi2", "cstat"], "description": "chi2 for binned, cstat for low-count Poisson"},
            },
            "required": ["pha_path", "model"],
        },
    },
    {
        "name": "pulsar_derived_quantities",
        "description": (
            "Compute pulsar derived quantities from period P and period derivative Ṗ: "
            "characteristic age τ_c = P/(2Ṗ), surface B field B_s = 3.2e19 √(PṖ) Gauss, "
            "spin-down luminosity Ė. Formulas from Lorimer & Kramer 2004 Handbook of Pulsar Astronomy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period_s": {"type": "number", "description": "Pulsar spin period in seconds"},
                "period_dot": {"type": "number", "description": "Period derivative dimensionless (s/s)"},
                "moment_of_inertia_g_cm2": {"type": "number", "description": "Moment of inertia (default 10^45)"},
            },
            "required": ["period_s", "period_dot"],
        },
    },
    {
        "name": "describe_tap_table",
        "description": (
            "List columns of a TAP table. Use BEFORE writing ADQL to verify column names exist. "
            "Returns column name, datatype, and description for each column. "
            "Supports services: gaia, simbad, vizier, cadc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "enum": ["gaia", "simbad", "vizier", "cadc"],
                    "description": "TAP service to query",
                },
                "table_name": {
                    "type": "string",
                    "description": (
                        "Full table name, e.g. 'gaiadr3.gaia_source', 'basic' (SIMBAD), "
                        "'\"IV/39/tic82\"' (VizieR TIC), '\"II/246/out\"' (VizieR 2MASS)"
                    ),
                },
            },
            "required": ["service", "table_name"],
        },
    },
    # F6.1: high-level Gaia open-cluster member-selection tool.  AI should
    # call this instead of hand-writing ADQL for cluster work: it composes
    # a well-formed query (cone search + parallax + proper motion + quality
    # cuts) from structured params, auto-resolves the center by name via
    # Sesame/SIMBAD, and exposes the usual result + reproducibility
    # envelope.  When the query returns 0 rows the F2.1 banner fires, which
    # nudges the model toward the <tools_returned_nothing/> abstention path
    # instead of inventing member counts.
    {
        "name": "query_gaia_cluster",
        "description": (
            "Query Gaia DR3 for candidate members of an open cluster or moving "
            "group.  Composes an ADQL query from structured parameters (cone "
            "search + parallax window + proper-motion box + quality cuts) and "
            "executes it against the Gaia TAP.  Prefer this over hand-writing "
            "ADQL whenever the user describes cluster / association / "
            "moving-group membership work.  Returns the row count, the member "
            "list (capped at 2000), and aggregate statistics (median parallax, "
            "mean proper motion)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "center_name": {
                    "type": "string",
                    "description": "Cluster name (e.g. 'Pleiades', 'NGC 752', 'M45').  Resolved via Sesame/SIMBAD.  Supply either this or (ra, dec).",
                },
                "ra": {"type": "number", "description": "Center RA in degrees."},
                "dec": {"type": "number", "description": "Center Dec in degrees."},
                "radius_deg": {
                    "type": "number",
                    "description": "Cone radius in degrees.  Default 2.0 for well-known clusters; larger for sparse associations.",
                },
                "parallax_center_mas": {
                    "type": "number",
                    "description": "Expected central parallax in mas (e.g. Pleiades ≈ 7.3).  Tool will select parallax_center ± parallax_tolerance.",
                },
                "parallax_tolerance_mas": {
                    "type": "number",
                    "description": "Half-width of the parallax window in mas.  Default 1.5.",
                },
                "pmra_center": {"type": "number", "description": "Expected μα* in mas/yr."},
                "pmdec_center": {"type": "number", "description": "Expected μδ in mas/yr."},
                "pm_tolerance": {
                    "type": "number",
                    "description": "Half-width of the proper-motion box in mas/yr.  Default 5.0.",
                },
                "ruwe_max": {
                    "type": "number",
                    "description": "Maximum RUWE for astrometric quality.  Default 1.4.",
                },
                "g_mag_max": {
                    "type": "number",
                    "description": "Faint cutoff for phot_g_mean_mag.  Default 18.",
                },
                "top": {
                    "type": "integer",
                    "description": "SELECT TOP limit.  Default 2000.",
                },
            },
            "required": [],
        },
    },
    # F6.2: dust-extinction lookup — A_V / E(B-V) from SFD98 2-D or Green
    # 2019 3-D map.  Exposed so the AI doesn't have to fetch-and-interpolate
    # dust maps via run_python every time it wants a quick reddening value.
    {
        "name": "get_extinction",
        "description": (
            "Look up interstellar extinction A_V (and E(B-V)) at a sky "
            "position using the Schlegel-Finkbeiner-Davis 1998 2-D dust map "
            "(or a 3-D map if dist_pc is provided and a 3-D map is installed).  "
            "Use this before comparing photometry to an isochrone or a model SED."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ra": {"type": "number", "description": "RA in degrees."},
                "dec": {"type": "number", "description": "Dec in degrees."},
                "band": {
                    "type": "string",
                    "description": "Return extinction in this band (G, V, B, R, J, H, K) in addition to A_V.",
                },
                "distance_pc": {
                    "type": "number",
                    "description": "Optional distance in pc; triggers a 3-D dust lookup if available.",
                },
                "r_v": {"type": "number", "description": "R_V for the extinction curve.  Default 3.1."},
            },
            "required": ["ra", "dec"],
        },
    },
]


# ── Tool Executors ──

async def execute_tool(
    tool_name: str,
    tool_input: dict,
    api_key: str = "",
    provider_api_keys: dict[str, str] | None = None,
    python_session_id: str = "default",
    user_id: str | None = None,
    chat_session_id: str | None = None,
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """Execute a tool call and return the result as a dict."""
    from app.services.result_provenance import normalize_tool_result
    result = await _execute_tool_inner(
        tool_name, tool_input, api_key, provider_api_keys,
        python_session_id, user_id, chat_session_id, progress_callback,
    )
    # R1: pass the caller's tool_input so the reproducibility envelope can
    # hash the exact invocation parameters.
    return normalize_tool_result(tool_name, result, tool_input=tool_input)


async def _execute_tool_inner(
    tool_name: str,
    tool_input: dict,
    api_key: str = "",
    provider_api_keys: dict[str, str] | None = None,
    python_session_id: str = "default",
    user_id: str | None = None,
    chat_session_id: str | None = None,
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """Inner dispatch — called by execute_tool, result wrapped with provenance."""
    try:
        if tool_name == "search_objects":
            return await _exec_search(tool_input, python_session_id)
        elif tool_name == "run_adql":
            return await _exec_adql(tool_input, python_session_id, progress_callback)
        elif tool_name == "query_high_velocity_stars":
            return await _exec_query_high_velocity_stars(tool_input, python_session_id, progress_callback)
        elif tool_name == "run_sdss_sql":
            return await _exec_run_sdss_sql(tool_input, python_session_id)
        elif tool_name == "get_object_info":
            return await _exec_object_info(tool_input)
        elif tool_name == "analyze_spectrum":
            return await _exec_analyze(tool_input, api_key, provider_api_keys)
        elif tool_name == "generate_pipeline":
            return _exec_pipeline(tool_input)
        elif tool_name == "search_literature":
            return await _exec_literature(tool_input)
        elif tool_name == "run_python":
            return await _exec_run_python(tool_input, python_session_id)
        elif tool_name == "get_last_search_results":
            return _exec_get_cached_results(tool_input)
        elif tool_name == "validate_analysis":
            return await _exec_validate_analysis(tool_input, chat_session_id or python_session_id)
        elif tool_name == "generate_paper_draft":
            return await _exec_generate_paper_draft(tool_input, chat_session_id or python_session_id)
        elif tool_name == "run_pipeline":
            return await _exec_run_pipeline(tool_input)
        elif tool_name == "generate_proposal":
            return await _exec_generate_proposal(tool_input)
        elif tool_name == "query_transients":
            return await _exec_query_transients(tool_input)
        elif tool_name == "read_arxiv_paper":
            return await _exec_read_paper(tool_input)
        elif tool_name == "research_workflow":
            return await _exec_research_workflow(tool_input)
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
        elif tool_name == "reduce_ccd_image":
            return await _exec_reduce_ccd_image(tool_input)
        elif tool_name == "solve_astrometry":
            return await _exec_solve_astrometry(tool_input)
        elif tool_name == "extract_photometry":
            return await _exec_extract_photometry(tool_input)
        elif tool_name == "extract_sources":
            return await _exec_extract_sources(tool_input)
        elif tool_name == "classify_transient":
            return await _exec_classify_transient(tool_input)
        elif tool_name == "analyze_spectrum_pro":
            return await _exec_analyze_spectrum_pro(tool_input)
        elif tool_name == "sensitivity_analysis":
            return await _exec_sensitivity_analysis(tool_input, python_session_id)
        elif tool_name == "query_vo_service":
            from app.services.vo_services import query_sia, query_ssa, federated_tap, discover_services
            protocol = tool_input.get("protocol", "tap")
            if protocol == "sia":
                url = tool_input.get("service_url", "https://irsa.ipac.caltech.edu/SIA")
                return query_sia(url, tool_input.get("ra", 0), tool_input.get("dec", 0), tool_input.get("radius", 0.1))
            elif protocol == "ssa":
                url = tool_input.get("service_url", "https://archive.stsci.edu/ssap/search")
                return query_ssa(url, tool_input.get("ra", 0), tool_input.get("dec", 0), tool_input.get("radius", 0.01))
            elif protocol == "tap":
                return federated_tap(tool_input.get("adql", ""), tool_input.get("services"))
            elif protocol == "discover":
                return discover_services(tool_input.get("keyword", ""), tool_input.get("service_type", "tap"))
            return {"error": f"Unknown protocol: {protocol}"}
        elif tool_name == "process_image":
            from app.services import image_processing_pro as ipp
            op = tool_input.get("operation")
            path = tool_input.get("fits_path", "")
            p = tool_input.get("params", {})
            if op == "reproject":
                return ipp.reproject_image(path, **p)
            elif op == "mosaic":
                return ipp.mosaic_images(tool_input.get("fits_paths", [path]), **p)
            elif op == "psf_match":
                return ipp.psf_match(path, **p)
            elif op == "deblend":
                return ipp.deblend_sources(path, **p)
            elif op == "cutout":
                return ipp.cutout_service(path, p.get("ra", 0), p.get("dec", 0), p.get("size_arcsec", 60))
            return {"error": f"Unknown operation: {op}"}
        # ── Time-Domain Tools ──
        elif tool_name == "gp_detrend_lightcurve":
            from app.services.time_domain_pro import gp_detrend
            return gp_detrend(
                tool_input["time"], tool_input["flux"],
                tool_input.get("flux_err"), tool_input.get("kernel", "matern32"),
            )
        elif tool_name == "fit_transit_model":
            from app.services.time_domain_pro import fit_transit
            return fit_transit(
                tool_input["time"], tool_input["flux"],
                tool_input.get("flux_err"), tool_input["period"],
                tool_input.get("t0"), tool_input.get("rp_rs", 0.1),
            )
        elif tool_name == "detect_stellar_flares":
            from app.services.time_domain_pro import detect_flares
            return detect_flares(
                tool_input["time"], tool_input["flux"],
                tool_input.get("flux_err"), tool_input.get("nsigma", 3.0),
            )
        elif tool_name == "transit_search_bls":
            from app.services.time_domain_pro import transit_search_bls as _bls
            return _bls(
                tool_input["time"], tool_input["flux"],
                period_range=(tool_input.get("period_min", 0.5), tool_input.get("period_max", 20.0)),
            )
        # ── Team/Workspace Tools ──
        elif tool_name == "share_with_team":
            return {
                "action": "share",
                "resource_type": tool_input["resource_type"],
                "resource_id": tool_input["resource_id"],
                "target_email": tool_input["email"],
                "permission": tool_input.get("permission", "view"),
                "user_id": user_id,
                "note": "Use the Team page to complete this sharing action.",
            }
        elif tool_name == "invite_team_member":
            return {
                "action": "invite",
                "email": tool_input["email"],
                "role": tool_input.get("role", "viewer"),
                "user_id": user_id,
                "note": "Use the Team page to send the invitation.",
            }
        # ── FITS/Export/Provenance Tools ──
        elif tool_name == "read_fits_header":
            from astropy.io import fits as pyfits
            from app.storage import download_fits
            import io
            data = download_fits(tool_input["fits_path"])
            with pyfits.open(io.BytesIO(data)) as hdul:
                hdu_idx = tool_input.get("hdu", 0)
                info: dict[str, Any] = {"n_hdus": len(hdul), "hdus": []}
                for i, h in enumerate(hdul):
                    hdu_info: dict[str, Any] = {"index": i, "name": h.name, "type": type(h).__name__}
                    if hasattr(h, "columns") and h.columns:
                        hdu_info["columns"] = [c.name for c in h.columns]
                    if h.data is not None:
                        hdu_info["shape"] = list(h.data.shape)
                    if i == hdu_idx:
                        hdu_info["header"] = {k: str(v) for k, v in h.header.items() if k}
                    info["hdus"].append(hdu_info)
                return info
        elif tool_name == "export_results":
            return {
                "export_url": f"/api/export/run/{tool_input['run_id']}/{tool_input['format']}",
                "note": "Download from this URL",
            }
        elif tool_name == "get_provenance":
            from app.services.provenance import get_lineage, get_reproducibility_package, generate_doi_metadata
            action = tool_input.get("action", "lineage")
            eid = tool_input["entity_id"]
            if action == "lineage":
                return get_lineage(eid)
            elif action == "reproduce":
                return get_reproducibility_package(eid)
            elif action == "doi_metadata":
                return generate_doi_metadata(eid)
            return {"error": f"Unknown provenance action: {action}"}
        # ── Photo-Z Pro + Batch Tools ──
        elif tool_name == "estimate_photo_z_pro":
            from app.services.photo_z_pro import fit_template_enhanced
            return fit_template_enhanced(
                tool_input["magnitudes"],
                tool_input.get("mag_errors"),
                z_range=(0, tool_input.get("z_max", 6.0)),
                prior=tool_input.get("prior", "flat"),
            )
        elif tool_name == "batch_object_search":
            targets = tool_input.get("targets", [])
            sources = tool_input.get("sources", ["simbad"])
            radius = tool_input.get("radius", 0.1)
            aggregated = []
            for target in targets:
                res = await _exec_search({"query": target, "sources": sources, "radius": radius}, python_session_id)
                aggregated.append({"target": target, "results": res.get("results", []), "total": res.get("total", 0)})
            return {"searches": aggregated, "total_targets": len(targets)}
        elif tool_name == "workspace_export":
            import csv as _csv
            import io as _io
            rows = tool_input.get("data", [])
            fmt = tool_input.get("format", "csv")
            columns = tool_input.get("columns")
            if fmt == "csv":
                buf = _io.StringIO()
                if rows:
                    cols = columns or (list(rows[0].keys()) if isinstance(rows[0], dict) else None)
                    if cols:
                        writer = _csv.DictWriter(buf, fieldnames=cols)
                        writer.writeheader()
                        for r in rows:
                            writer.writerow({c: r.get(c, "") if isinstance(r, dict) else "" for c in cols})
                    else:
                        writer_list = _csv.writer(buf)
                        for r in rows:
                            writer_list.writerow(r if isinstance(r, list) else [r])
                return {"format": "csv", "content": buf.getvalue(), "row_count": len(rows)}
            elif fmt == "votable":
                lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                         '<VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.4">',
                         '<RESOURCE><TABLE>']
                if rows and isinstance(rows[0], dict):
                    cols = columns or list(rows[0].keys())
                    for c in cols:
                        lines.append(f'<FIELD name="{c}" datatype="char" arraysize="*"/>')
                    lines.append("<DATA><TABLEDATA>")
                    for r in rows:
                        cells = "".join(f"<TD>{r.get(c, '')}</TD>" for c in cols)
                        lines.append(f"<TR>{cells}</TR>")
                    lines.append("</TABLEDATA></DATA>")
                lines.extend(["</TABLE></RESOURCE>", "</VOTABLE>"])
                return {"format": "votable", "content": "\n".join(lines), "row_count": len(rows)}
            return {"error": f"Unsupported format: {fmt}"}
        elif tool_name == "classify_transient_spectrum":
            from app.services.transient_classifier import classify_with_spectroscopy, enrich_with_host_galaxy
            result = classify_with_spectroscopy(tool_input.get("wavelength", []), tool_input.get("flux", []), tool_input.get("redshift", 0))
            if tool_input.get("ra") is not None and tool_input.get("dec") is not None:
                result["host_galaxy"] = enrich_with_host_galaxy(tool_input["ra"], tool_input["dec"])
            return result
        elif tool_name == "literature_review":
            from app.services.literature_engine import synthesize_bibliography, build_citation_network
            result = synthesize_bibliography(tool_input.get("topic", ""), tool_input.get("findings", ""), tool_input.get("max_papers", 10))
            if tool_input.get("build_network") and tool_input.get("seed_bibcodes"):
                result["citation_network"] = build_citation_network(tool_input["seed_bibcodes"], depth=1)
            return result
        elif tool_name == "radio_analysis":
            from app.connectors.radio import RadioAnalysis
            op = tool_input.get("operation")
            if op == "spectral_index":
                return RadioAnalysis.spectral_index(tool_input.get("flux_1_mJy", 0), tool_input.get("freq_1_MHz", 0), tool_input.get("flux_2_mJy", 0), tool_input.get("freq_2_MHz", 0))
            elif op == "luminosity":
                return RadioAnalysis.radio_luminosity(tool_input.get("flux_mJy", 0), tool_input.get("redshift", 0), tool_input.get("freq_MHz", 1400))
            elif op == "crossmatch":
                return RadioAnalysis.multi_frequency_crossmatch(tool_input.get("ra", 0), tool_input.get("dec", 0))
            return {"error": f"Unknown operation: {op}"}
        elif tool_name == "full_research_report":
            session_id = tool_input.get("session_id", "")
            title = tool_input.get("title", "Standard Astro Analysis Report")
            journal = tool_input.get("journal", "aastex")
            return {
                "publication_package": {
                    "paper_draft_url": f"/api/paper/generate?session_id={session_id}&format={journal}",
                    "notebook_url": f"/api/integration/jupyter/from-chat?session_id={session_id}",
                    "bibtex_url": f"/api/export/bibtex/{session_id}",
                },
                "title": title,
                "journal_format": journal,
                "instructions": "Download the paper draft, notebook, and BibTeX from the URLs above. The paper includes all analysis results, figures, and citations from this session.",
            }
        # ── P1/P2 workflow tools (knowledge base expansion) ──
        elif tool_name == "compute_galaxy_sfr":
            return _exec_compute_galaxy_sfr(tool_input)
        elif tool_name == "fit_rv_orbit":
            return await _exec_fit_rv_orbit(tool_input)
        elif tool_name == "fit_sersic_morphology":
            return await _exec_fit_sersic(tool_input)
        elif tool_name == "x_ray_spectral_fit":
            return await _exec_x_ray_spectral_fit(tool_input)
        elif tool_name == "pulsar_derived_quantities":
            return _exec_pulsar_derived(tool_input)
        elif tool_name == "describe_tap_table":
            return await _exec_describe_tap_table(tool_input)
        elif tool_name == "query_gaia_cluster":
            return await _exec_query_gaia_cluster(tool_input, python_session_id)
        elif tool_name == "get_extinction":
            return await _exec_get_extinction(tool_input)
        else:
            # R6-NEW-2: 给 Unknown tool 返具体 error_class + 可用工具清单,
            # 让 AI 下一轮能自纠 (不再 hallucinate 工具名).  TOOLS 是模块
            # 顶层 list, 每项 {"name": ..., "description": ...}.
            try:
                available = sorted(t.get("name", "") for t in TOOLS if t.get("name"))
            except Exception:
                available = []
            return {
                "error": (
                    f"Unknown tool: {tool_name!r}. "
                    f"Available tools: {', '.join(available) if available else 'n/a'}"
                ),
                "error_class": "unknown_tool",
                "attempted_tool": tool_name,
                "available_tools": available,
            }
    except Exception as e:
        logger.warning("Tool %s failed: %s", tool_name, e)
        return {"error": str(e)}


def _auto_select_sources(query: str) -> list[str]:
    """Auto-select the best database sources based on query keywords.

    M11: switched from substring matching to word-boundary regex so that
    `supernova` no longer triggers the `star` branch, `nova` doesn't match
    inside `renovation`, etc.  Also extended the stellar branch with an
    explicit `kinematics`/`hr diagram` lead so parallax-relevant queries
    include Gaia even when the word `star` isn't present.
    """
    import re as _re
    q = query.lower()

    def _has(*keywords: str) -> bool:
        pattern = r"\b(" + "|".join(_re.escape(k) for k in keywords) + r")\b"
        return bool(_re.search(pattern, q))

    if _has("supernova", "sn", "sn2024", "sn2023", "transient", "grb", "kilonova", "nova", "tns"):
        return ["simbad"]
    if _has("quasar", "qso", "agn", "blazar", "seyfert"):
        return ["simbad", "ned"]
    if _has("star", "stellar", "parallax", "binary", "variable", "hr diagram", "cmd") or "proper motion" in q:
        return ["gaia", "simbad"]
    if _has("galaxy", "galaxies", "cluster", "group") or "morpholog" in q:
        return ["simbad", "ned", "sdss"]
    if _has("infrared", "wise", "2mass", "dust"):
        return ["allwise", "2mass"]
    if _has("x-ray", "xray"):
        return ["chandra", "simbad"]
    return ["simbad"]


def _suggest_actions_for_results(results: list) -> list[dict]:
    """Generate suggested follow-up actions based on search results."""
    if not results:
        return []
    suggestions = []
    has_z = any(r.get("redshift") for r in results if isinstance(r, dict))
    has_mag = any(r.get("magnitude") for r in results if isinstance(r, dict))
    n = len(results)

    if n > 1 and has_mag:
        suggestions.append({"action": "plot_results", "label": "Plot HR diagram or color-magnitude diagram", "auto": True})
    if has_z:
        suggestions.append({"action": "estimate_photo_z_pro", "label": "Estimate photometric redshifts"})
    if n > 5:
        suggestions.append({"action": "crossmatch_catalogs", "label": "Cross-match with Gaia/SDSS"})
    if n >= 1:
        suggestions.append({"action": "get_object_dossier", "label": f"Full dossier for {results[0].get('name', 'top result')}"})
    return suggestions


async def _exec_search(inp: dict, python_session_id: str = "default") -> dict:
    from app.connectors.registry import CONNECTORS_KEYS, get_connector
    from app.api.data import _astro_to_result
    from app.api.data import _resolve_search_coordinates, _search_timeout_for_source
    from app.search.query_parser import parse_natural_query

    query = inp.get("query", "")
    sources = inp.get("sources") or []
    radius = inp.get("radius", 0.1)

    # Phase 2C: Smart database routing when sources not specified
    if not sources:
        sources = _auto_select_sources(query)

    sources = [s for s in sources if s in CONNECTORS_KEYS]
    if not sources:
        sources = ["simbad"]

    parsed = parse_natural_query(query)
    object_type = parsed.get("object_type")
    redshift_min = parsed.get("redshift_min")
    redshift_max = parsed.get("redshift_max")
    has_criteria = any([object_type, redshift_min, redshift_max])

    # Skip coordinate resolution for scientific criteria queries (e.g. "emission line galaxies z<0.1")
    # to avoid SkyCoord.from_name() sending the criteria string to Sesame as an object name
    if has_criteria:
        search_ra, search_dec = None, None
    else:
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
    # M15: surface per-source truncation so the LLM (and the user) can see
    # that each archive's row cap was hit instead of believing the returned
    # set is the complete archive response.
    per_source_counts: list[dict] = []
    total_truncated = False
    for src, res in zip(sources, results_per):
        if isinstance(res, Exception):
            all_results.append({"source": src, "error": str(res)})
            per_source_counts.append({"source": src, "error": str(res)})
        else:
            archive_count = len(res)
            truncated_here = archive_count > 25
            if truncated_here:
                total_truncated = True
            for obj in res[:25]:
                r = _astro_to_result(obj).model_dump()
                all_results.append(r)
            per_source_counts.append({
                "source": src,
                "returned": min(archive_count, 25),
                "archive_total": archive_count,
                "truncated": truncated_here,
            })

    # Cache results for later retrieval by get_last_search_results
    store_search_results("latest", all_results)
    store_search_results(f"latest:{python_session_id}", all_results)

    result = {
        "results": all_results,
        "total": len(all_results),
        "per_source": per_source_counts,
        "truncated": total_truncated,
    }

    # Phase 2B: Add suggested actions based on search results
    result["suggested_actions"] = _suggest_actions_for_results(result.get("results", []))

    return result


async def _exec_adql(
    inp: dict,
    python_session_id: str = "default",
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """Execute ADQL query with automatic retry on timeout.

    On timeout (408/502/503), automatically retries with progressively
    reduced cone search radius. Stores full result set in cache so
    downstream Python can access all rows via get_cached_results.
    """
    from app.api.integration import execute_adql_query, ADQLRequest
    import asyncio as _aio
    import re as _re

    query = inp.get("query", "")
    service = inp.get("service", "gaia")
    extended_timeout = bool(
        inp.get("extended_timeout")
        or str(inp.get("_workflow_budget_mode") or "").lower() == "long"
    )
    async_timeout_s = 780.0 if extended_timeout else 300.0
    retry_chain_budget_s = 840.0 if extended_timeout else 360.0

    async def _emit_progress(stage: str, message: str, **extra: Any) -> None:
        if progress_callback is None:
            return
        event = {
            "stage": stage,
            "message": message,
            "service": service,
            **extra,
        }
        try:
            await progress_callback(event)
        except Exception:
            logger.debug("ADQL tool progress callback failed", exc_info=True)

    # Validate and rewrite ADQL before sending to TAP service
    from app.services.adql_dialect import normalize_adql
    dialect = normalize_adql(query, service)
    if not dialect.ok:
        return {"error": "; ".join(dialect.errors)}
    query = dialect.rewritten_query
    _dialect_warnings = dialect.warnings

    async def _try_query(q: str) -> dict | None:
        """Attempt one ADQL query. Return result dict or None on timeout.

        G0.1: on "unresolved identifier" / "unknown column" errors, augment
        the exception with a hint from the catalog registry so the AI gets
        a concrete correction instead of bouncing off the error again.
        """
        try:
            return await execute_adql_query(
                ADQLRequest(query=q, service=service),
                progress_callback=progress_callback,
                async_timeout_s=async_timeout_s,
            )
        except Exception as exc:
            msg = str(exc).lower()
            if any(h in msg for h in ("timeout", "408", "502", "503", "aborted", "deadline")):
                return None
            # G0.1: column-name rescue.  The Pleiades/Coma reviewer hit
            # "unresolved identifier: RAJ2000" in SDSS V/147 because the
            # real name is RA_ICRS.  Grep the raw error for quoted /
            # colon-separated identifiers and suggest the right name.
            if any(h in msg for h in ("unresolved identifier", "unknown column", "invalid identifier")):
                from app.services.catalog_registry import suggest_for_missing_column
                suggestions: list[str] = []
                if "gaiadr3.epoch_photometry" in q.lower():
                    suggestions.append(
                        "`gaiadr3.epoch_photometry` is not a generic TAP light-curve "
                        "table with `time`/`mag`/`band`/`flux` columns. Call "
                        "`describe_tap_table` first, use Gaia `vari_*` tables for "
                        "published variable-star periods, or use `search_lightcurve` "
                        "for TESS/MAST light curves."
                    )
                try:
                    tokens = _re.findall(r"[A-Za-z][A-Za-z0-9_]{2,32}", str(exc))
                except Exception:
                    tokens = []
                seen: set[str] = set()
                for tok in tokens:
                    if tok in seen:
                        continue
                    seen.add(tok)
                    hint = suggest_for_missing_column(tok)
                    if hint:
                        suggestions.append(f"`{tok}` → {hint}")
                if suggestions:
                    raise type(exc)(
                        f"{exc}\n\n[auto-suggestion] "
                        + " ".join(suggestions)
                    ) from exc
            raise

    # Post-H0.1 hardening: track wall-clock budget.  execute_adql_query
    # now has 60 s sync + 300 s async fallback per call, so a single
    # call can already eat 6 minutes.  Cap the TOTAL time spent in the
    # retry chain at 6 minutes — if we've already burned that, skip
    # further retries and return the error fast.  Without this, a bad
    # day on Gaia TAP could chain 3 × 6 min = 18 minutes of silence.
    import time as _time_mod
    _start_ts = _time_mod.monotonic()
    _timeout_policy = {
        "tool_deadline_seconds": 780 if extended_timeout else 300,
        "sync_probe_seconds": 30,
        "async_fallback_budget_seconds": int(async_timeout_s),
        "retry_chain_budget_seconds": int(retry_chain_budget_s),
        "extended_timeout": extended_timeout,
    }
    _total_budget_s = retry_chain_budget_s

    def _time_left() -> float:
        return _total_budget_s - (_time_mod.monotonic() - _start_ts)

    await _emit_progress(
        "query_attempt",
        f"Running ADQL on {service}",
        query_preview=str(query)[:300],
    )
    result = await _try_query(query)

    # On timeout, retry with reduced cone radius (halve, then quarter)
    retry_log = []
    if result is None:
        # Find CIRCLE('ICRS', ra, dec, radius) and halve the radius
        def _halve_radius(q: str, factor: float = 0.5) -> str | None:
            pattern = _re.compile(
                r"(CIRCLE\s*\(\s*'ICRS'\s*,\s*[-+]?\d+\.?\d*\s*,\s*[-+]?\d+\.?\d*\s*,\s*)([-+]?\d+\.?\d*)",
                _re.IGNORECASE,
            )
            m = pattern.search(q)
            if m:
                old_r = float(m.group(2))
                new_r = old_r * factor
                return pattern.sub(lambda _m: f"{_m.group(1)}{new_r}", q, count=1)
            return None

        for attempt, factor in enumerate([0.5, 0.25], start=1):
            if _time_left() < 30:
                retry_log.append(f"skipped radius×{factor} — budget exhausted")
                await _emit_progress(
                    "retry_skipped_budget",
                    f"Skipped radius × {factor} retry because the ADQL retry budget is nearly exhausted",
                    factor=factor,
                )
                break
            reduced = _halve_radius(query, factor)
            if reduced is None:
                break
            retry_log.append(f"attempt {attempt}: radius × {factor}")
            await _emit_progress(
                "radius_retry",
                f"ADQL timed out; retrying with cone radius × {factor}",
                attempt=attempt,
                factor=factor,
                query_preview=reduced[:300],
            )
            await _aio.sleep(1.0)
            result = await _try_query(reduced)
            if result is not None:
                query = reduced  # remember the successful query
                await _emit_progress(
                    "radius_retry_success",
                    f"ADQL succeeded after cone radius × {factor}",
                    attempt=attempt,
                    factor=factor,
                )
                break

        # H0.8: TOP N auto-degradation.  If the query has a TOP clause
        # >= 10000 and the cone-radius retry ran out, try the query one
        # more time with TOP reduced to min(1000, N/10).  Reviewer hit
        # this on Paper 5: TOP 50000 Gaia query timed out, AI tried
        # TOP 20000 which also timed out, circuit opened.  A smaller
        # sample still gives the AI enough rows for tail statistics.
        if result is None and _time_left() > 30:
            _top_match = _re.search(r"\bTOP\s+(\d+)\b", query, _re.IGNORECASE)
            if _top_match:
                old_top = int(_top_match.group(1))
                if old_top >= 10000:
                    new_top = max(1000, old_top // 10)
                    degraded = _re.sub(
                        r"\bTOP\s+\d+\b", f"TOP {new_top}", query, count=1,
                        flags=_re.IGNORECASE,
                    )
                    retry_log.append(f"auto-degraded TOP {old_top} → {new_top}")
                    await _emit_progress(
                        "top_auto_reduced",
                        f"ADQL timed out; retrying with TOP {new_top} instead of TOP {old_top}",
                        old_top=old_top,
                        new_top=new_top,
                        query_preview=degraded[:300],
                    )
                    await _aio.sleep(1.0)
                    result = await _try_query(degraded)
                    if result is not None:
                        query = degraded
                        await _emit_progress(
                            "top_auto_reduce_success",
                            f"ADQL succeeded after reducing TOP {old_top} to {new_top}",
                            old_top=old_top,
                            new_top=new_top,
                        )
                        # Flag in the output so AI knows it got fewer rows
                        # than requested and can warn user about sample size.
                        if isinstance(result, dict):
                            result["top_auto_reduced_from"] = old_top
                            result["top_used"] = new_top

    if result is None:
        _elapsed = int(_time_mod.monotonic() - _start_ts)
        _query_l = str(query).lower()
        _sdss_vizier_hint = ""
        if service.lower() == "vizier" and any(
            token in _query_l for token in ("v/154/sdss", "v/147/sdss", "v/139/sdss")
        ):
            _sdss_vizier_hint = (
                " For SDSS luminosity-function or photometry+spec-z samples, "
                "stop retrying broad VizieR ADQL: call run_sdss_sql instead "
                "with a T-SQL query against PhotoObjAll JOIN SpecObjAll "
                "(start with TOP 500-1000)."
            )
        return {
            "error": (
                f"ADQL query failed after {_elapsed}s of retries "
                f"({len(retry_log)} attempt{'s' if len(retry_log) != 1 else ''}).  "
                "This particular ADQL query pattern exceeded the retry budget; "
                "the TAP service may still be responsive for smaller queries.  "
                "This usually means the requested query is too broad/heavy "
                "(large TOP, wide cone, JOIN, or weak cuts) or transiently slow.  "
                f"Timeout policy: run_adql has a {int(_timeout_policy['tool_deadline_seconds'])}s base "
                f"tool deadline ({'extended by long workflow mode' if extended_timeout else 'default mode'}); "
                "each TAP call uses a 30s sync probe before the "
                f"{int(async_timeout_s)}s async fallback; the retry "
                f"chain has a {int(retry_chain_budget_s)}s internal budget but may be capped by the outer "
                "workflow budget.  Try one of: "
                "(a) add/tighten TOP (e.g. TOP 500 instead of TOP 50000 — you can "
                "still get statistics from smaller samples); "
                "(b) shrink the cone radius to <0.3°; "
                "(c) add tighter parallax / magnitude / quality cuts (parallax > 1, "
                "phot_g_mean_mag < 18, ruwe < 1.4); "
                "(d) query by source_id list if you know your targets; "
                "(e) for Gaia DR3 mirrors: try VizieR 'I/355/gaiadr3' instead of "
                "the primary Gaia TAP."
                f"{_sdss_vizier_hint}"
            ),
            "error_class": "adql_timeout",
            "retries": retry_log,
            "elapsed_seconds": _elapsed,
            "service": service,
            "diagnosis": "query_pattern_exceeded_budget",
            "timeout_policy": _timeout_policy,
        }

    # Normalize column names to lowercase for consistent AI code
    raw_data = result.get("data", {}) if isinstance(result, dict) else {}
    data = {col.lower(): vals for col, vals in raw_data.items()}
    raw_columns = result.get("columns", []) if isinstance(result, dict) else []
    columns = [c.lower() for c in raw_columns]
    row_count = result.get("row_count", 0) if isinstance(result, dict) else 0
    attempt_log = result.get("attempt_log", []) if isinstance(result, dict) else []

    # AI view: first 100 rows to fit in context. Full data goes to cache.
    VIEW_ROWS = 100
    truncated = {col: (vals[:VIEW_ROWS] if isinstance(vals, list) else vals)
                 for col, vals in data.items()}
    adql_result = {
        "columns": columns,
        "data": truncated,
        "row_count": row_count,
        "showing": min(VIEW_ROWS, row_count),
        "has_data": row_count > 0,
        "note": (
            f"Showing first {VIEW_ROWS} of {row_count} rows. Full data is cached — "
            "in run_python you can access it via get_cached_results('latest_adql')."
        ) if row_count > VIEW_ROWS else None,
    }
    if retry_log:
        adql_result["retry_log"] = retry_log
    if isinstance(attempt_log, list) and attempt_log:
        adql_result["attempt_log"] = attempt_log[-20:]
    if _dialect_warnings:
        adql_result["dialect_warnings"] = _dialect_warnings

    await _emit_progress(
        "query_success",
        f"ADQL query succeeded with {row_count} rows",
        row_count=row_count,
        showing=min(VIEW_ROWS, row_count),
    )

    # Store full result set in cache (indexed for get_cached_results)
    result_set = build_adql_result_set(
        service=service,
        query=query,
        columns=columns,
        data=data,
        row_count=row_count,
    )
    store_adql_result_set(python_session_id, result_set)

    return adql_result


def _bounded_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _bounded_float(value: Any, *, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed != parsed:
        parsed = default
    return max(min_value, min(max_value, parsed))


def _build_high_velocity_stars_query(
    *,
    limit: int = 500,
    min_parallax_mas: float = 0.2,
    min_vtan_kms: float = 250.0,
    require_radial_velocity: bool = False,
) -> str:
    vtan_expr = "(4.74047 * SQRT(pmRA*pmRA + pmDE*pmDE) / Plx)"
    rv_clause = "AND RV IS NOT NULL" if require_radial_velocity else ""
    return f"""
SELECT TOP {limit}
  Source, RA_ICRS, DE_ICRS, Plx, pmRA, pmDE, RV, Gmag, RUWE,
  {vtan_expr} AS vtan_kms
FROM "I/355/gaiadr3"
WHERE Plx >= {min_parallax_mas:.6g}
  AND pmRA IS NOT NULL
  AND pmDE IS NOT NULL
  AND {vtan_expr} >= {min_vtan_kms:.6g}
  AND (RUWE IS NULL OR RUWE < 1.4)
  {rv_clause}
ORDER BY {vtan_expr} DESC
""".strip()


async def _exec_query_high_velocity_stars(
    inp: dict,
    python_session_id: str = "default",
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """Focused Gaia DR3 high-velocity candidate query for v_esc workflows."""
    from app.api.integration import ADQLRequest, execute_adql_query

    limit = _bounded_int(inp.get("limit"), default=500, min_value=50, max_value=2000)
    min_parallax = _bounded_float(inp.get("min_parallax_mas"), default=0.2, min_value=0.05, max_value=20.0)
    min_vtan = _bounded_float(inp.get("min_vtan_kms"), default=250.0, min_value=50.0, max_value=1000.0)
    require_rv = bool(inp.get("require_radial_velocity") is True)
    extended_timeout = bool(
        inp.get("extended_timeout")
        or str(inp.get("_workflow_budget_mode") or "").lower() == "long"
    )
    query = _build_high_velocity_stars_query(
        limit=limit,
        min_parallax_mas=min_parallax,
        min_vtan_kms=min_vtan,
        require_radial_velocity=require_rv,
    )
    async_timeout_s = 780.0 if extended_timeout else 300.0
    try:
        result = await execute_adql_query(
            ADQLRequest(query=query, service="vizier"),
            progress_callback=progress_callback,
            async_timeout_s=async_timeout_s,
        )
    except Exception as exc:
        return {
            "success": False,
            "error": (
                f"High-velocity Gaia DR3 candidate query failed: {exc}. "
                "Retry with a higher min_vtan_kms, lower limit, or require_radial_velocity=false."
            ),
            "error_class": "high_velocity_query_failed",
            "service": "vizier",
            "table": "I/355/gaiadr3",
            "query": query,
            "params": {
                "limit": limit,
                "min_parallax_mas": min_parallax,
                "min_vtan_kms": min_vtan,
                "require_radial_velocity": require_rv,
                "extended_timeout": extended_timeout,
            },
        }

    raw_data = result.get("data", {}) if isinstance(result, dict) else {}
    data = {str(col).lower(): vals for col, vals in raw_data.items()}
    columns = [str(c).lower() for c in (result.get("columns", []) if isinstance(result, dict) else [])]
    row_count = int(result.get("row_count", 0) or 0) if isinstance(result, dict) else 0
    attempt_log = result.get("attempt_log", []) if isinstance(result, dict) else []

    result_set = build_adql_result_set(
        service="vizier",
        query=query,
        columns=columns,
        data=data,
        row_count=row_count,
    )
    store_adql_result_set(python_session_id, result_set)
    hv_key = _session_cache_key("latest_high_velocity_stars", python_session_id) or "latest_high_velocity_stars"
    store_search_results(hv_key, result_set)

    view_rows = min(100, row_count)
    truncated = {col: (vals[:view_rows] if isinstance(vals, list) else vals) for col, vals in data.items()}
    return {
        "service": "vizier",
        "table": "I/355/gaiadr3",
        "query": query,
        "columns": columns,
        "data": truncated,
        "row_count": row_count,
        "showing": view_rows,
        "has_data": row_count > 0,
        "cache_keys": ["latest_adql", "latest_adql_set", hv_key],
        "params": {
            "limit": limit,
            "min_parallax_mas": min_parallax,
            "min_vtan_kms": min_vtan,
            "require_radial_velocity": require_rv,
            "extended_timeout": extended_timeout,
        },
        "science_caveat": (
            "This is a Gaia DR3 high-tangential-velocity candidate sample reachable "
            "through public TAP. It is useful for platform reproduction and sanity "
            "checks, but it is not the full Piffl+2014 / Monari+2018 halo-star "
            "selection function."
        ),
        "note": (
            f"Showing first {view_rows} of {row_count} rows. Full rows are cached under latest_adql "
            f"and {hv_key}; compute 3D velocities in run_python(data_source='latest_adql')."
        ) if row_count > view_rows else None,
        "attempt_log": attempt_log[-20:] if isinstance(attempt_log, list) else [],
    }


async def _exec_run_sdss_sql(inp: dict, python_session_id: str = "default") -> dict:
    """J3: 直接打 SDSS SkyServer SQL API, 不经 VizieR.

    VizieR 四个 mirror 全部返回 404 时 (第三次回归 Paper 3 Coma 星系
    光度函数案例), SDSS 路径整条废.  这个工具绕开 VizieR, 直接调 SDSS
    官方 SkyServer (有独立 uptime), 让 AI 能在 VizieR 宕机期继续做
    PhotoObjAll / SpecObjAll / Photoz / GalSpec 查询.

    结果结构跟 _exec_adql 一致, 并存进 ADQL cache 池 (key `latest_sdss_sql`
    用额外 alias), run_python 可以通过
    `get_cached_results('latest_sdss_sql')` 或简单的 `get_adql_results()`
    拿到完整行.
    """
    from app.connectors.sdss_sql import execute_sdss_sql

    query = str(inp.get("query") or "").strip()
    dr = str(inp.get("dr") or "18").strip()
    timeout_s = 240.0 if (
        inp.get("extended_timeout")
        or str(inp.get("_workflow_budget_mode") or "").lower() == "long"
    ) else 120.0

    if not query:
        return {
            "error": (
                "`query` is required.  Example: "
                "SELECT TOP 100 objID, ra, dec, r, z FROM SpecObjAll "
                "WHERE class = 'GALAXY' AND zWarning = 0"
            ),
            "error_class": "missing_argument",
            "argument": "query",
        }
    if dr not in {"18", "17", "16"}:
        return {
            "error": f"`dr` must be one of '18' / '17' / '16' (got {dr!r})",
            "error_class": "invalid_argument",
            "argument": "dr",
        }

    try:
        raw = await execute_sdss_sql(query, dr=dr, timeout_s=timeout_s)
    except ValueError as e:
        # SQL 语法错误 / 危险关键词: 用户可见的 4xx 语义
        return {
            "error": str(e),
            "error_class": "sdss_sql_syntax",
            "service": "sdss",
            "dr": dr,
        }
    except Exception as e:
        return {
            "error": f"SDSS SkyServer unavailable: {e}",
            "error_class": "sdss_unavailable",
            "service": "sdss",
            "dr": dr,
        }

    columns = raw.get("columns", [])
    data = raw.get("data", {})
    row_count = raw.get("row_count", 0)

    # AI 视图: 截取前 100 行, 完整数据走 cache.
    VIEW_ROWS = 100
    truncated = {col: (vals[:VIEW_ROWS] if isinstance(vals, list) else vals)
                 for col, vals in data.items()}

    # 存进 ADQL cache 池 — run_python 能用 get_adql_results() 也能用
    # get_cached_results('latest_sdss_sql').  我们额外存一个 sdss 专用
    # key 供 AI 按数据源区分.
    try:
        result_set = build_adql_result_set(
            service=f"sdss_dr{dr}",
            query=query,
            columns=columns,
            data=data,
            row_count=row_count,
        )
        store_adql_result_set(python_session_id, result_set)
        # 显式的 sdss 别名, 让 AI 能 import 时用 `latest_sdss_sql` 标识.
        store_search_results("latest_sdss_sql", data)
    except Exception as e:
        logger.debug("SDSS SQL cache write failed: %s", e)

    return {
        "service": "sdss",
        "dr": dr,
        "columns": columns,
        "data": truncated,
        "row_count": row_count,
        "showing": min(VIEW_ROWS, row_count),
        "has_data": row_count > 0,
        "note": (
            f"Showing first {VIEW_ROWS} of {row_count} rows. Full data is cached — "
            "in run_python access via get_cached_results('latest_sdss_sql')."
        ) if row_count > VIEW_ROWS else None,
    }


async def _exec_describe_tap_table(inp: dict) -> dict:
    """Query TAP_SCHEMA to list columns of a table."""
    from app.api.integration import execute_adql_query, ADQLRequest

    service = inp.get("service", "gaia")
    table_name = inp.get("table_name", "")

    if not table_name:
        return {"error": "table_name is required"}

    # Fast-path: check local catalog registry before hitting TAP_SCHEMA
    from app.services.catalog_registry import get_catalog
    cached = get_catalog(table_name)
    if cached:
        return {
            "table": table_name,
            "service": service,
            "column_count": len(cached.columns),
            "columns": [{"name": c.name, "datatype": c.datatype, "description": c.description} for c in cached.columns],
            "source": "local_registry",
        }

    # H5: VizieR paths may arrive double-quoted (e.g. '"IV/39/tic82"').
    # TAP_SCHEMA stores names un-quoted, so strip the outer pair before
    # matching.  The regex refuses single quotes so SQL interpolation cannot
    # break out of the literal even if the allow-list is loosened in future.
    cleaned_name = table_name.strip()
    if cleaned_name.startswith('"') and cleaned_name.endswith('"') and len(cleaned_name) >= 2:
        cleaned_name = cleaned_name[1:-1]
    import re as _re
    if not _re.match(r'^[\w./+-]+$', cleaned_name):
        return {"error": f"Invalid table_name: {table_name}"}

    query = (
        f"SELECT column_name, datatype, description "
        f"FROM TAP_SCHEMA.columns "
        f"WHERE table_name = '{cleaned_name}' "
        f"ORDER BY column_name"
    )

    try:
        result = await execute_adql_query(ADQLRequest(query=query, service=service))
    except Exception as exc:
        return {"error": f"Failed to query TAP_SCHEMA for {table_name}: {exc}"}

    if not result or not result.get("data"):
        return {
            "error": f"No columns found for table '{table_name}' on {service}. "
            "Check the table name — for VizieR use quoted names like '\"IV/39/tic82\"'.",
        }

    data = result.get("data", {})
    columns = []
    names = data.get("column_name", data.get("COLUMN_NAME", []))
    dtypes = data.get("datatype", data.get("DATATYPE", []))
    descs = data.get("description", data.get("DESCRIPTION", []))

    for i in range(len(names)):
        columns.append({
            "name": names[i] if i < len(names) else "",
            "datatype": dtypes[i] if i < len(dtypes) else "",
            "description": descs[i] if i < len(descs) else "",
        })

    return {
        "table": table_name,
        "service": service,
        "column_count": len(columns),
        "columns": columns[:200],  # Cap at 200 to fit context
    }


# F6.1 — query_gaia_cluster: compose a well-formed Gaia ADQL query from
# structured member-selection parameters, auto-resolve cluster names via
# Sesame/SIMBAD, dispatch against the Gaia TAP.  Keeping the SQL here (not
# in the model's generated code) means the zero-fabrication gate + F2.1
# banners fire cleanly on 0-row returns.
async def _exec_query_gaia_cluster(inp: dict, python_session_id: str) -> dict:
    from app.api.integration import execute_adql_query, ADQLRequest

    # Resolve center
    ra = inp.get("ra")
    dec = inp.get("dec")
    center_name = inp.get("center_name")
    if (ra is None or dec is None) and center_name:
        try:
            from app.services.name_resolver import resolve_name
            resolved = await resolve_name(str(center_name))
            if resolved is not None:
                ra, dec = resolved
        except Exception as e:
            logger.info("query_gaia_cluster: name resolver failed: %s", e)

    if ra is None or dec is None:
        return {
            "error": (
                "query_gaia_cluster needs either (ra, dec) or a resolvable "
                "center_name.  Provide coordinates explicitly or use a name "
                "SIMBAD/Sesame knows."
            ),
            "error_class": "invalid_input",
            "success": False,
        }

    radius = float(inp.get("radius_deg") or 2.0)
    plx_center = inp.get("parallax_center_mas")
    plx_tol = float(inp.get("parallax_tolerance_mas") or 1.5)
    pmra_c = inp.get("pmra_center")
    pmdec_c = inp.get("pmdec_center")
    pm_tol = float(inp.get("pm_tolerance") or 5.0)
    ruwe_max = float(inp.get("ruwe_max") or 1.4)
    g_max = float(inp.get("g_mag_max") or 18.0)
    top = int(inp.get("top") or 2000)

    clauses = [
        f"CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {float(ra)}, {float(dec)}, {radius}))=1",
        f"ruwe < {ruwe_max}",
        f"phot_g_mean_mag < {g_max}",
        "parallax IS NOT NULL",
    ]
    if plx_center is not None:
        lo = float(plx_center) - plx_tol
        hi = float(plx_center) + plx_tol
        clauses.append(f"parallax BETWEEN {lo} AND {hi}")
    if pmra_c is not None:
        clauses.append(f"pmra BETWEEN {float(pmra_c) - pm_tol} AND {float(pmra_c) + pm_tol}")
    if pmdec_c is not None:
        clauses.append(f"pmdec BETWEEN {float(pmdec_c) - pm_tol} AND {float(pmdec_c) + pm_tol}")

    query = (
        f"SELECT TOP {top} source_id, ra, dec, parallax, parallax_error, "
        f"pmra, pmra_error, pmdec, pmdec_error, ruwe, phot_g_mean_mag, "
        f"phot_bp_mean_mag, phot_rp_mean_mag, bp_rp "
        f"FROM gaiadr3.gaia_source WHERE " + " AND ".join(clauses)
    )

    try:
        result = await execute_adql_query(ADQLRequest(query=query, service="gaia"))
    except Exception as exc:
        return {
            "error": f"Gaia TAP query failed: {exc}",
            "error_class": "tap_error",
            "success": False,
            "query": query,
        }

    row_count = int(result.get("row_count", 0) or 0)
    out: dict = {
        "success": True,
        "query": query,
        "center_ra": float(ra),
        "center_dec": float(dec),
        "radius_deg": radius,
        "row_count": row_count,
        "columns": list(result.get("columns") or []),
        "rows": list(result.get("rows") or [])[:200],  # preview
        "data": result.get("data") or {},
    }

    # Aggregate stats — only when we have data; the F2.1 banner will fire
    # on row_count == 0, and the LLM will route to <tools_returned_nothing/>.
    if row_count > 0:
        try:
            data = result.get("data") or {}
            plxs = [float(v) for v in (data.get("parallax") or []) if v is not None]
            pmras = [float(v) for v in (data.get("pmra") or []) if v is not None]
            pmdecs = [float(v) for v in (data.get("pmdec") or []) if v is not None]
            import statistics
            if plxs:
                out["median_parallax_mas"] = float(statistics.median(plxs))
                out["stdev_parallax_mas"] = float(statistics.pstdev(plxs)) if len(plxs) > 1 else 0.0
            if pmras:
                out["mean_pmra"] = float(statistics.mean(pmras))
            if pmdecs:
                out["mean_pmdec"] = float(statistics.mean(pmdecs))
        except Exception as e:
            logger.debug("cluster stats failed: %s", e)

    return out


# F6.2 — get_extinction: SFD98 by default, optional 3-D Green map when
# distance_pc is given and dustmaps is installed.  Gracefully degrades
# (returns an explicit error_class) if the dustmaps package is missing,
# so the AI can route to a <tools_returned_nothing/> instead of inventing.
async def _exec_get_extinction(inp: dict) -> dict:
    ra = inp.get("ra")
    dec = inp.get("dec")
    if ra is None or dec is None:
        return {
            "error": "ra and dec are required",
            "error_class": "invalid_input",
            "success": False,
        }
    band = str(inp.get("band") or "").upper()
    r_v = float(inp.get("r_v") or 3.1)

    # Try dustmaps (SFD 2-D).  If it's not installed, fall back to a
    # lightweight analytic approximation from galactic coordinates + a
    # bounded-uncertainty message so callers know the number is rough.
    try:
        from astropy.coordinates import SkyCoord
        import astropy.units as u
        coord = SkyCoord(ra=float(ra) * u.deg, dec=float(dec) * u.deg, frame="icrs")
        galactic = coord.galactic
        l_deg = float(galactic.l.deg)
        b_deg = float(galactic.b.deg)
        try:
            from dustmaps.sfd import SFDQuery  # type: ignore
            sfd = SFDQuery()
            ebv = float(sfd(coord))
            method = "SFD98"
            note = None
        except Exception:
            # Analytic fallback: exponential disk + scale height, matched
            # to SFD98 within factor ~2 in the solar neighbourhood.
            # NOT publication-grade — flagged as approximate.
            from math import sin, cos, exp, radians
            b_rad = radians(b_deg)
            ebv = 0.025 * exp(-abs(sin(b_rad)) * 5) * (1 + 0.1 * cos(radians(l_deg)))
            method = "analytic_fallback"
            note = (
                "dustmaps.sfd not installed; returned analytic approximation "
                "accurate to ~factor 2.  Install `dustmaps` + run "
                "`python -m dustmaps.sfd` to get SFD98 values."
            )
        # A_V = R_V * E(B-V)
        a_v = r_v * ebv
        out: dict = {
            "success": True,
            "ra": float(ra),
            "dec": float(dec),
            "galactic_l_deg": l_deg,
            "galactic_b_deg": b_deg,
            "e_b_v": ebv,
            "a_v": a_v,
            "r_v": r_v,
            "method": method,
        }
        if note:
            out["note"] = note
            out["warnings"] = [note]
        # Band-specific extinction via Cardelli+ 1989 approximate ratios.
        band_ratios = {"V": 1.0, "B": 1.321, "R": 0.811, "U": 1.569,
                       "I": 0.607, "J": 0.280, "H": 0.182, "K": 0.118,
                       "G": 0.789}  # Gaia G ~ 0.789 A_V
        if band:
            if band in band_ratios:
                out[f"a_{band.lower()}"] = a_v * band_ratios[band]
            else:
                out["warnings"] = out.get("warnings", []) + [
                    f"Unknown band '{band}'. Known: {sorted(band_ratios)}"
                ]
        return out
    except Exception as e:
        return {
            "error": f"get_extinction failed: {e}",
            "error_class": "runtime_error",
            "success": False,
        }


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


async def _exec_literature(inp: dict) -> dict:
    try:
        from functools import partial
        from app.api.citations import (
            _search_ads_sync,
            _search_literature_ads,
            _search_literature_arxiv,
        )

        query = str(inp.get("query") or "").strip()
        if not query:
            return {
                "results": [],
                "error": "search_literature requires query",
                "error_class": "missing_argument",
                "argument": "query",
            }
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, _search_ads_sync, query)
        source = "ads_or_arxiv_object"
        # `_search_ads_sync` 偏 object:<name> 检索。审稿场景里经常问的是
        # topic / method / catalog name, 所以 object 检索为空时再走
        # free-text ADS 和 arXiv, 避免直接 EMPTY。
        if not raw:
            raw = await loop.run_in_executor(None, partial(_search_literature_ads, query, 8))
            source = "ads_free_text"
        if not raw:
            raw = await loop.run_in_executor(None, partial(_search_literature_arxiv, query, 8))
            source = "arxiv_free_text"
        if not raw:
            return {
                "results": [],
                "source": source,
                "message": (
                    "No papers found via ADS object search, ADS free-text search, "
                    "or arXiv fallback."
                ),
            }
        return {
            "source": source,
            "results": [
                {
                    "title": r["title"],
                    "authors": r["authors"][:3],
                    "year": r["year"],
                    "bibcode": r["bibcode"],
                    "abstract": (r.get("abstract") or "")[:500],
                    "source": r.get("source") or r.get("pub") or source,
                }
                for r in raw[:8]
            ]
        }
    except Exception as e:
        return {"error": str(e)}


_VALID_DATA_SOURCES = {
    "latest_adql", "latest_search", "latest_lightcurve",
    "latest_sdss_sql",  # J3: SDSS SkyServer 直连结果
    "latest_high_velocity_stars",
    "none_not_analyzing_real_data",
}
_REAL_DATA_SOURCE_PATTERNS = {
    # G1.2: for each declared data_source, these substrings must appear in
    # the code — else the declaration is inconsistent with the code and
    # we reject it.
    "latest_adql": ("get_adql_results", "get_adql_result_sets", "get_cached_results"),
    "latest_search": ("get_search_results", "get_cached_results"),
    "latest_lightcurve": ("get_cached_results", "lightkurve", "search_lightcurve"),
    # J3: latest_sdss_sql 共享 get_cached_results / get_adql_results 的访问接口
    # (run_sdss_sql 把结果存进 adql_result_sets 池, 复用 getter), 加上显式的
    # latest_sdss_sql 变量名就能匹配.
    "latest_sdss_sql": ("get_cached_results", "get_adql_results", "latest_sdss_sql"),
    "latest_high_velocity_stars": ("get_cached_results", "get_adql_results", "latest_high_velocity_stars"),
}
_PLATFORM_REAL_DATA_READER_TOKENS = {
    # 这些 helper 本身会触发真实 archive / MAST / platform cache 读取。
    # 即使模型把 data_source 写成 latest_search 而不是 latest_lightcurve,
    # 也不应被 G1.2 当成“没有读取真实数据”。
    "search_lightcurve",
    "download_and_clean_lightcurve",
    "transit_search",
}


def _summarize_ast_observations(code: str, limit: int = 24) -> str:
    """返回 AST 校验实际看见的标识符, 方便模型按错误提示自修复。"""
    import ast as _ast

    try:
        tree = _ast.parse(code)
    except SyntaxError:
        return "AST observations unavailable because the code has a SyntaxError."

    calls: set[str] = set()
    names: set[str] = set()
    imports: set[str] = set()

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Name):
            names.add(node.id)
        elif isinstance(node, _ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, _ast.Call):
            func = node.func
            if isinstance(func, _ast.Name):
                calls.add(func.id)
            elif isinstance(func, _ast.Attribute):
                calls.add(func.attr)
        elif isinstance(node, _ast.ImportFrom):
            for alias in node.names:
                imports.add(alias.asname or alias.name)
        elif isinstance(node, _ast.Import):
            for alias in node.names:
                imports.add(alias.asname or alias.name.split(".")[0])

    def _fmt(label: str, values: set[str]) -> str | None:
        if not values:
            return None
        ordered = sorted(values)
        suffix = "" if len(ordered) <= limit else f", +{len(ordered) - limit} more"
        return f"{label}={ordered[:limit]}{suffix}"

    parts = [
        part for part in (
            _fmt("calls", calls),
            _fmt("names", names),
            _fmt("imports", imports),
        )
        if part
    ]
    if not parts:
        return "AST observed no function calls, variable names, or imports."
    return "AST observed " + "; ".join(parts) + "."


async def _exec_run_python(inp: dict, python_session_id: str = "default") -> dict:
    """Execute Python code in sandboxed environment."""
    from app.services.code_executor import execute_python

    code = inp.get("code", "")
    # G1.1: data_source contract.  AI must declare where the data comes
    # from.  Treatment of missing / unknown declarations:
    # - Missing entirely: look at the code and auto-classify via AST.
    #   B-S4 fix: the old default was "none_not_analyzing_real_data" which
    #   flagged legitimate code reading Gaia ADQL results as SYNTHETIC
    #   ("Numbers from synthetic tools are NOT from observations" warning
    #   next to real Gaia DR3 δ Cephei data).  If the code clearly reads
    #   a real cache (get_adql_results etc.), infer the source.  Otherwise
    #   fall back to synthetic.  AST walk keeps this precise.
    # - Known real source (latest_adql/search/lightcurve): validate that
    #   the code actually reads from it; mismatch → reject.
    # - "none_not_analyzing_real_data": explicit synthetic, marked SYNTHETIC.
    # - cached:<key> / fits:<path>: free-form, no static check.
    data_source = str(inp.get("data_source", "")).strip()
    if not data_source:
        try:
            from app.observability.metrics import record_counter
            record_counter("run_python_missing_data_source_total", 1.0)
        except Exception:
            pass
        # B-S4: AST-based auto-classification instead of blanket SYNTHETIC.
        # Looks for calls/names that mean the code is reading a real cache.
        auto_source: str | None = None
        try:
            import ast as _ast_auto
            tree = _ast_auto.parse(code)
            names_seen: set[str] = set()
            for node in _ast_auto.walk(tree):
                if isinstance(node, _ast_auto.Name):
                    names_seen.add(node.id)
                if isinstance(node, _ast_auto.Attribute):
                    names_seen.add(node.attr)
            # J3: code 里出现 latest_sdss_sql / run_sdss_sql 变量 → SDSS
            # cache.  这条要放在 get_adql_results 之前 check, 因为 run_sdss_sql
            # 复用 ADQL cache 池, 变量名才是唯一区分.
            if any(t in names_seen for t in ("latest_sdss_sql", "run_sdss_sql")):
                auto_source = "latest_sdss_sql"
            elif any(t in names_seen for t in ("get_adql_results", "get_adql_result_sets")):
                auto_source = "latest_adql"
            elif "get_search_results" in names_seen:
                auto_source = "latest_search"
            elif any(t in names_seen for t in ("search_lightcurve", "download_and_clean_lightcurve")):
                auto_source = "latest_lightcurve"
            elif "get_cached_results" in names_seen:
                auto_source = "latest_adql"  # the most common cached source
            elif any(t in names_seen for t in ("load_fits", "fits")):
                auto_source = "fits:<autodetected>"
        except Exception:
            pass

        if auto_source is not None:
            data_source = auto_source
            # Fire a counter so we can see how often AI forgets to declare
            # but we auto-recovered.
            try:
                from app.observability.metrics import record_counter
                record_counter("run_python_data_source_auto_inferred_total", 1.0, inferred=auto_source)
            except Exception:
                pass
        else:
            # Still couldn't tell — default to synthetic (safer).
            data_source = "none_not_analyzing_real_data"

    is_synthetic_declared = data_source == "none_not_analyzing_real_data"

    # G1.2: if declared a real source, the code should reference the
    # matching helper.  Skip validation for cached:... and fits:... which
    # carry the target inline.
    if data_source in _REAL_DATA_SOURCE_PATTERNS:
        expected_tokens = _REAL_DATA_SOURCE_PATTERNS[data_source]
        # H3.1: AST-based check instead of naive substring.
        # - String `in` picked up tokens in comments / docstrings, leaving
        #   false positives that Paper 1 reviewer hit (AI legitimate code
        #   failing because a helper was aliased).
        # - AST walk catches:
        #     * Direct calls: get_adql_results()
        #     * Aliased: helper = get_adql_results; helper()
        #     * Imports: from x import get_adql_results as fetch
        #     * Attribute calls: astro.get_adql_results()
        # Plus we always accept `get_cached_results(...)` as a valid
        # signal for any real data_source (the generic cache reader
        # from G6.2 can fetch any handle).
        import ast as _ast
        tokens_to_match = (
            set(expected_tokens)
            | {"get_cached_results"}
            | _PLATFORM_REAL_DATA_READER_TOKENS
        )
        found_in_ast = False
        try:
            tree = _ast.parse(code)
            for node in _ast.walk(tree):
                # Any Name node matching
                if isinstance(node, _ast.Name) and node.id in tokens_to_match:
                    found_in_ast = True
                    break
                # Attribute access (foo.bar or module.helper)
                if isinstance(node, _ast.Attribute) and node.attr in tokens_to_match:
                    found_in_ast = True
                    break
                # Import "from x import <token>" or "import <token>"
                if isinstance(node, _ast.ImportFrom):
                    for alias in node.names:
                        if alias.name in tokens_to_match or (alias.asname and alias.asname in tokens_to_match):
                            found_in_ast = True
                            break
                if isinstance(node, _ast.Import):
                    for alias in node.names:
                        if alias.name in tokens_to_match or (alias.asname and alias.asname in tokens_to_match):
                            found_in_ast = True
                            break
                if found_in_ast:
                    break
        except SyntaxError:
            # Code has SyntaxError; sandbox will surface it next step.
            # Be permissive here and fall back to string match so we
            # don't reject on syntax issues alone.
            found_in_ast = any(tok in code for tok in tokens_to_match)

        # S1 (PART S): session-scoped history check.  R9-NEW-1 regression:
        # AI 前一个 cell 已经 `rows = get_adql_results()`, 当前 cell 直接
        # `df.groupby(...)` 被拒.  若 session 历史里调过 expected token,
        # 也算过关 — 因为 subprocess replay prefix 会把那一 cell 重跑,
        # 本 cell 里真的拿得到 real-archive 数据.
        if not found_in_ast and python_session_id and python_session_id != "default":
            try:
                from app.services.code_executor import get_session_helper_calls
                history_tokens = get_session_helper_calls(python_session_id)
                if tokens_to_match & history_tokens:
                    found_in_ast = True
            except Exception:
                pass

        if not found_in_ast:
            observed_ast = _summarize_ast_observations(code)
            return {
                "success": False,
                "error": (
                    f"data_source='{data_source}' declared but the code does not "
                    f"call any of {sorted(tokens_to_match)} (checked via AST walk "
                    f"including aliases + imports). {observed_ast} Either (a) update "
                    f"your code to actually read that cache, or (b) declare "
                    f"data_source='none_not_analyzing_real_data' if you are "
                    f"intentionally generating synthetic data (which will be "
                    f"marked SYNTHETIC and forbidden from citation)."
                ),
                "error_class": "data_source_mismatch",
            }

    # G2: AST static analysis to catch contract violations where the AI
    # declares `latest_adql` but the code actually does np.random.normal().
    # Run detector early; if verdict=synthetic AND declared a real source,
    # reject; if verdict=suspicious, downgrade output to SYNTHETIC.
    try:
        from app.services.synthetic_code_detector import analyze as _analyze
        detection = _analyze(code)
        if detection.verdict == "synthetic" and not is_synthetic_declared:
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "synthetic_detected_total", 1.0,
                    trigger="ast_contract_mismatch",
                    declared=data_source,
                )
            except Exception:
                pass
            return {
                "success": False,
                "error": (
                    f"G2 static-analysis detected this code fabricates data "
                    f"(np.random / suspicious keywords / no real-data readers) "
                    f"but data_source='{data_source}' was declared. "
                    f"Signals: has_np_random={detection.has_np_random}, "
                    f"has_time_linspace={detection.has_time_linspace}, "
                    f"suspicious_keywords={detection.suspicious_keywords}, "
                    f"reads_real_data={detection.reads_real_data}. "
                    f"Either fix the code to read real data, declare "
                    f"data_source='none_not_analyzing_real_data', or emit "
                    f"<tools_returned_nothing/> if you intended to abstain."
                ),
                "error_class": "synthetic_declared_as_real",
                "detection": {
                    "verdict": detection.verdict,
                    "suspicious_keywords": detection.suspicious_keywords,
                    "has_np_random": detection.has_np_random,
                    "has_time_linspace": detection.has_time_linspace,
                    "reads_real_data": detection.reads_real_data,
                },
            }
        elif detection.verdict == "suspicious" and not is_synthetic_declared:
            # Downgrade to synthetic at response time (below).  Record why.
            is_synthetic_declared = True
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "synthetic_detected_total", 1.0,
                    trigger="ast_suspicious",
                    declared=data_source,
                )
            except Exception:
                pass
        elif detection.verdict == "inert":
            # R5 O3: 纯 literal print / 字面量算术代码 (smoke test /
            # 环境自检). 即使 AI 声明了 data_source='none...', 也不打
            # SYNTHETIC — 没有合成数据产生, 没有数字进入分析链.
            is_synthetic_declared = False
            try:
                from app.observability.metrics import record_counter
                record_counter("inert_code_exempted_from_synthetic_total", 1.0)
            except Exception:
                pass
    except Exception as det_exc:
        logger.debug("synthetic_code_detector failed: %s", det_exc)

    if not code.strip():
        # E2.3: previously returned a bare "No code provided" which the AI
        # would retry as another empty call.  Track via Prometheus so we
        # can see how often the LLM leaks this and surface an actionable
        # hint the AI can react to.
        try:
            from app.observability.metrics import record_counter
            record_counter("empty_tool_call_total", 1.0, tool="run_python")
        except Exception:
            pass
        return {
            "error": "run_python called with empty code — skipping.",
            "error_class": "empty_input",
            "success": False,
            "hint": (
                "Include the code to run in the `code` field.  If you meant to "
                "refer to a prior variable, include a short snippet like "
                "`print(members.head())` so the sandbox can execute it."
            ),
        }

    # G5: mode-dependent timeout.  fast=30s / normal=75s (default) / slow=300s.
    mode = str(inp.get("mode") or "normal").lower()
    mode_timeout = {"fast": 30.0, "normal": 75.0, "slow": 300.0}.get(mode, 75.0)

    # Run in executor with timeout to not block the event loop
    loop = asyncio.get_running_loop()
    auto_escalated = False
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, execute_python, code, None, python_session_id, mode_timeout),
            timeout=mode_timeout,
        )
    except asyncio.TimeoutError:
        # U2b (PART U): Round 10 观察 AI 常常首次用 mode='normal' 跑
        # lightkurve 下载就超 75s, 必须在后续轮次里手动改 mode='slow'
        # 绕过. 省掉 model round-trip: 如果 mode 不是 slow, 自动 retry
        # 一次 mode='slow' (300s). 只允许一次升级避免死循环.
        if mode != "slow":
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, execute_python, code, None, python_session_id, 300.0
                    ),
                    timeout=300.0,
                )
                auto_escalated = True
                mode = "slow"
                mode_timeout = 300.0
            except asyncio.TimeoutError:
                return {
                    "success": False,
                    "error": (
                        f"Code execution timed out after 300s even after auto-"
                        f"escalating to mode='slow' (initial mode={inp.get('mode') or 'normal'}). "
                        f"The task likely needs to be split into smaller chunks, "
                        f"or run as a background pipeline job."
                    ),
                    "error_class": "timeout",
                    "stdout": "",
                    "auto_escalated_mode": True,
                }
        else:
            return {
                "success": False,
                "error": (
                    f"Code execution timed out after {int(mode_timeout)}s (mode={mode}). "
                    f"For long computations (BLS / MCMC / bootstrap / PARSEC grid), "
                    f"split into smaller chunks or use a background pipeline job."
                ),
                "error_class": "timeout",
                "stdout": "",
            }

    auto_fix_note = None
    if not result.success and result.error:
        retry = _retryable_python_fix(code, result.error)
        if retry is not None:
            fixed_code, auto_fix_note = retry
            try:
                retry_result = await asyncio.wait_for(
                    loop.run_in_executor(None, execute_python, fixed_code, None, python_session_id, mode_timeout),
                    timeout=mode_timeout,
                )
                if retry_result.success:
                    result = retry_result
                else:
                    auto_fix_note = None
            except asyncio.TimeoutError:
                auto_fix_note = None

    response: dict = {
        "success": result.success,
        "stdout": result.stdout[:50_000] if result.stdout else "",
        "backend": getattr(result, "backend", "unknown"),
        "duration_ms": getattr(result, "duration_ms", 0),
        "exit_code": getattr(result, "exit_code", None),
        "mode": mode,  # 反映最终生效的 mode (可能被 U2b auto-escalate 成 slow)
    }
    if auto_escalated:
        response["auto_escalated_mode"] = True
        response["note"] = (
            "run_python auto-escalated from mode='{}' to mode='slow' because "
            "initial run exceeded its timeout budget. Future similar calls "
            "should declare mode='slow' up front.".format(inp.get("mode") or "normal")
        )

    # R5 O2: 非零 exit_code 降级 success. subprocess crash 时 child 一启
    # 动就 payload['success']=True, 代码还没跑到 except 就崩, 父进程拿到
    # 的 SandboxResult.success 还是 True 但 proc.exitcode=1. 让两者对齐
    # 是 success 真实语义的基础 — 否则 UI / AI 看到 "success=true +
    # exit_code=1" 矛盾, 不知道到底成了没.
    _exit = response.get("exit_code")
    if _exit not in (None, 0) and response.get("success"):
        response["success"] = False
        response.setdefault("error", f"Subprocess exited with non-zero code {_exit}")
        response.setdefault("error_class", "sandbox_nonzero_exit")

    # F0.2: error-field tripwire.  Previously we only populated `error` when
    # `result.error` was truthy — so a sandbox that returned success=False
    # with an empty/None error (e.g. subprocess was SIGKILLed before it
    # could serialize its error message) produced a response dict with no
    # error key, surfacing on the frontend as the generic "Python sandbox
    # returned no message (check backend logs)" fallback.  Guarantee that
    # any failure carries a concrete, user-actionable message plus an
    # error_class the UI can chip-render.
    if result.error:
        response["error"] = result.error
        response["error_class"] = _classify_sandbox_error(result.error)
    elif not result.success:
        response["error"] = (
            f"Sandbox reported failure without a specific error message. "
            f"stdout_len={len(result.stdout or '')}, "
            f"stderr_len={len(result.stderr or '')}, "
            f"figures={len(result.figures or [])}.  This usually means the "
            f"sandbox subprocess was killed (OOM / SIGKILL / wall-clock "
            f"timeout) before it could send its result back."
        )
        response["error_class"] = "sandbox_crash"
        try:
            from app.observability.metrics import record_counter
            record_counter("sandbox_silent_failure_total", 1.0, tool="run_python")
        except Exception:
            pass
    # R5 O1 + R6 post: stderr 作为一级字段**总是**写进 response, 即便空串.
    # 诊断角度看, "stderr=''" 跟 "stderr not set" 是完全不同的信号:
    #   前者 = 子进程 child main 跑过且写了 stderr 捕获, 真的没东西输出
    #   后者 = 子进程崩在 Python 解释器启动 / import, child main 没跑到,
    #          stderr 去了 uvicorn 的 fd=2 (Render 容器日志), 客户端拿不到
    # 用户 (和 AI) 能凭空字符串本身推断出后一种情况, 去看
    # /api/admin/sandbox/health 拿 Python-level stderr.
    # 原来的 `traceback` 字段保留给已失败的场景当 backwards-compat.
    response["stderr"] = (result.stderr or "")[:10_000]
    if not result.stderr and _exit not in (None, 0):
        response["stderr_note"] = (
            "stderr field is empty DESPITE non-zero exit code. This means "
            "the subprocess crashed during Python interpreter startup / "
            "module import, before the child process could set up stderr "
            "capture. The real Python error went to the container's fd=2 "
            "(uvicorn stderr / Render log). Call GET /api/admin/sandbox/"
            "health from an admin client to reproduce with subprocess.Popen "
            "and see the actual Python traceback."
        )
    if result.stderr:
        response["traceback"] = result.stderr[:10_000]
    if result.figures:
        response["figures"] = result.figures[:10]  # max 10 figures
        response["figure_count"] = len(result.figures)
    if result.variables:
        response["variables"] = dict(list(result.variables.items())[:50])
    if result.variable_types:
        response["variable_types"] = dict(list(result.variable_types.items())[:50])
    if auto_fix_note:
        response["auto_fix_note"] = auto_fix_note

    # G1.3: SYNTHETIC banner.  If the AI declared that this run_python call
    # is NOT analyzing real data, prepend a banner and mark data_origin so
    # the zero-fabrication gate refuses to cite numbers from this run.
    if is_synthetic_declared:
        banner = {
            "__tool_status__": "SYNTHETIC",
            "__do_not_claim__": True,
            "__message_to_model__": (
                "This run_python call was declared data_source="
                "'none_not_analyzing_real_data'. Its numerical output is NOT "
                "from real observations. You MUST NOT cite any number from "
                "this call's stdout/variables/figures in your final reply. "
                "If the user asked for real data analysis, emit "
                "<tools_returned_nothing/> instead — do not use this synthetic "
                "run to stand in for a failed data fetch."
            ),
            "__suggested_next_step__": (
                "If the user wants real data, retry with a real data source "
                "(latest_adql / fits:<path> / etc.) or emit the abstention tag."
            ),
            "data_origin": "synthetic",
            "analysis_status": "simulated_demo",
        }
        # Banner keys go FIRST so the model reads them before payload
        combined = dict(banner)
        combined.update(response)
        # But preserve the banner-level data_origin override (response may
        # have its own; we want the SYNTHETIC tag to win)
        combined["data_origin"] = "synthetic"
        combined["analysis_status"] = "simulated_demo"
        try:
            from app.observability.metrics import record_counter
            record_counter(
                "synthetic_declared_total", 1.0,
                trigger="contract",
            )
        except Exception:
            pass
        return combined

    return response


def _classify_sandbox_error(err: str) -> str:
    """F0.2: map a sandbox error string to a short class so the UI can
    render a status chip (e.g. "oom", "timeout", "sandbox_crash").
    """
    if not err:
        return "unknown"
    low = err.lower()
    if "timed out" in low or "timeout" in low:
        return "timeout"
    if "memoryerror" in low or "address-space" in low or "oom" in low:
        return "oom"
    if "incomplete payload" in low or "terminated without result" in low \
            or "without an error message" in low:
        return "sandbox_crash"
    if "nameerror" in low:
        return "name_error"
    if "importerror" in low or "modulenotfounderror" in low:
        return "import_error"
    if "systemexit" in low:
        return "system_exit"
    if "syntaxerror" in low or "indentationerror" in low:
        return "syntax_error"
    return "runtime_error"


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

    return {"parameter": param, "base_value": base, "results": results}


def _exec_get_cached_results(inp: dict) -> dict:
    """Return full cached search results."""
    max_n = inp.get("max_results", 50)
    results = get_cached_results("latest")
    if results is None:
        return {"results": [], "message": "No recent search results cached. Run a search first."}
    return {"results": results[:max_n], "total": len(results)}


def _resolve_session_id(tool_input: dict, python_session_id: str) -> str | None:
    session_id = str(tool_input.get("session_id") or "").strip()
    if session_id:
        return session_id
    fallback = str(python_session_id or "").strip()
    return fallback or None


async def _exec_validate_analysis(inp: dict, python_session_id: str = "default") -> dict:
    from app.models.database import async_session
    from app.services.analysis_validator import validate_analysis

    session_id = _resolve_session_id(inp, python_session_id)
    if not session_id:
        return {"error": "session_id is required. Save the current chat session first."}

    async with async_session() as db:
        try:
            validation = await validate_analysis(session_id, db)
        except Exception as exc:
            return {"error": str(exc)}
    return validation


async def _exec_generate_paper_draft(inp: dict, python_session_id: str = "default") -> dict:
    from app.models.database import async_session
    from app.services.paper_generator import generate_paper_draft

    session_id = _resolve_session_id(inp, python_session_id)
    if not session_id:
        return {"error": "session_id is required. Save the current chat session first."}

    journal_format = str(inp.get("journal_format", "aastex") or "aastex").strip().lower()
    async with async_session() as db:
        try:
            generated = await generate_paper_draft(session_id, journal_format, db)
        except Exception as exc:
            # E0.3: classify common upstream failures (ADS DNS, timeout,
            # LLM backend outage) into a typed, user-friendly error so the
            # chat shows "AI backend temporarily unreachable" instead of
            # a raw "Errno -2 Name or service not known" traceback.
            import socket as _socket
            msg = str(exc)
            if isinstance(exc, _socket.gaierror) or "Name or service not known" in msg or "Temporary failure in name resolution" in msg:
                err_text = (
                    "External service (ADS / LLM backend) is currently unreachable "
                    "from this deploy. Your session and data are fine; retry once "
                    "network connectivity recovers, or export the raw chat/pipeline "
                    "via the Export menu."
                )
                return {
                    "error": err_text,
                    "error_class": "network_unreachable",
                    "detail": msg[:500],
                    "success": False,
                }
            if "timeout" in msg.lower() or "timed out" in msg.lower():
                return {
                    "error": "Paper draft generation timed out while fetching external metadata.",
                    "error_class": "timeout",
                    "detail": msg[:500],
                    "success": False,
                }
            return {
                "error": f"Paper draft generation failed: {msg[:300]}",
                "error_class": "unknown",
                "success": False,
            }

    paper_json = generated["paper_json"]
    return {
        "title": paper_json.get("title", ""),
        "abstract": paper_json.get("abstract", ""),
        "journal_format": paper_json.get("journal_format", journal_format),
        "sections": {
            "introduction": paper_json.get("introduction", {}).get("text", ""),
            "data_and_methods": paper_json.get("data_and_methods", {}).get("analysis_methods", ""),
            "results": paper_json.get("results", {}).get("text", ""),
            "discussion": paper_json.get("discussion", {}).get("text", ""),
            "conclusions": paper_json.get("conclusions", ""),
        },
        "figures": paper_json.get("results", {}).get("figures", []),
        "tables": paper_json.get("results", {}).get("tables", []),
        "bibtex_preview": generated["bibtex"][:4000],
        "latex_preview": generated["latex_source"][:6000],
    }


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

    # 1. Resolve target coordinates
    ra, dec = None, None
    target_mag = None
    try:
        from app.services.name_resolver import resolve_name
        resolved = await resolve_name(target_name)
        if resolved.resolved:
            ra, dec = resolved.ra, resolved.dec
            from astropy.coordinates import SkyCoord
            coord = SkyCoord(ra, dec, unit="deg")
            proposal["coordinates"] = {"ra_deg": round(ra, 6), "dec_deg": round(dec, 6),
                                       "ra_hms": coord.ra.to_string(unit="hour", sep=":"),
                                       "dec_dms": coord.dec.to_string(sep=":")}
        else:
            proposal["coordinates"] = {"error": f"Could not resolve '{target_name}': tried {resolved.aliases_tried}"}
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

    # Fetch paper metadata from arXiv API.
    # R6-NEW-1: arXiv 把 http → https 301 跳了, httpx 默认不 follow.
    # 两招叠加: URL 直接写 https + 也打开 follow_redirects (有些镜像还会
    # 再跳一次).
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(f"https://export.arxiv.org/api/query?id_list={arxiv_id}")
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


async def _exec_research_workflow(inp: dict) -> dict:
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

    plan = {
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

    # Auto-execute the first suggested query to bootstrap the workflow
    if plan.get("suggested_queries") and len(plan["suggested_queries"]) > 0:
        first_q = plan["suggested_queries"][0]
        try:
            query_str = first_q.get("query", first_q) if isinstance(first_q, dict) else str(first_q)
            step1 = await _exec_search({"query": query_str, "sources": ["simbad"]})
            plan["auto_executed_step1"] = True
            plan["step1_results_summary"] = f"Auto-searched: found {len(step1.get('results', []))} objects"
            plan["step1_results"] = step1
        except Exception as e:
            plan["auto_executed_step1"] = False
            plan["step1_error"] = str(e)

    return plan


_BAND_NAME_MAP = {
    "sdss_u": "u", "sdss_g": "g", "sdss_r": "r", "sdss_i": "i", "sdss_z": "z",
    "twomass_j": "J", "twomass_h": "H", "twomass_ks": "Ks",
    "wise_w1": "W1", "wise_w2": "W2",
}


def _normalize_band_names(mags: dict) -> dict:
    """Map prefixed band names (sdss_g, twomass_j, ...) to photo_z internal names."""
    normalized: dict = {}
    for key, val in mags.items():
        mapped = _BAND_NAME_MAP.get(key.lower(), key)
        normalized[mapped] = val
    return normalized


async def _exec_estimate_photo_z(inp: dict) -> dict:
    """Run the unified photometric redshift estimator.

    L5 (audit 2026-04-20): 默认 method='enhanced_template' (photo_z_pro)
    走研究级路径.  AI 若显式要 demo 模式 (7 templates), 必须传
    allow_demo=True — 否则返回 demo_mode_blocked 引导到 enhanced.
    """
    from app.services.photo_z import estimate_photo_z

    raw_magnitudes = inp.get("magnitudes", {})
    raw_mag_errors = inp.get("mag_errors", {})
    # L5: 默认换 enhanced_template, 不再 silent 走 7 模板 hybrid
    method = inp.get("method", "enhanced_template")
    allow_demo = bool(inp.get("allow_demo", False))

    if not raw_magnitudes:
        return {"error": "magnitudes dict is required (e.g. {'sdss_g': 20.1, 'sdss_r': 19.5})"}

    magnitudes = _normalize_band_names(raw_magnitudes)
    mag_errors = _normalize_band_names(raw_mag_errors) if raw_mag_errors else {}

    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: estimate_photo_z(magnitudes, mag_errors, method=method, allow_demo=allow_demo),
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
    """Execute isochrone fitting on observed CMD data.

    Always auto-extracts data from cached search results if arrays are not
    provided or are invalid. Falls back to turnoff-based age estimation if
    the PARSEC API is unreachable.
    """
    import math

    import numpy as np

    bp_rp = inp.get("bp_rp", [])
    abs_mag = inp.get("abs_mag", [])
    method = inp.get("method", "grid")

    # Discard string values (AI sometimes passes variable names)
    if bp_rp and isinstance(bp_rp, list) and bp_rp and isinstance(bp_rp[0], str):
        bp_rp = []
    if abs_mag and isinstance(abs_mag, list) and abs_mag and isinstance(abs_mag[0], str):
        abs_mag = []

    # ── Auto-extract from ALL available caches ──
    med_plx = None
    med_av = None

    if not bp_rp or not abs_mag:
        bp_rp_list, abs_mag_list, plx_list, av_list = [], [], [], []

        # Source 1: search_objects cache ("latest")
        search_cache = get_cached_results("latest")
        if search_cache:
            for r in search_cache:
                extra = r.get("extra", {}) if isinstance(r, dict) else {}
                color = extra.get("bp_rp")
                gmag = r.get("magnitude") if isinstance(r, dict) else None
                plx = extra.get("parallax")
                if color is not None and gmag is not None and plx is not None and plx > 0:
                    try:
                        abs_g = float(gmag) - 5 * math.log10(1000.0 / float(plx)) + 5
                        bp_rp_list.append(float(color))
                        abs_mag_list.append(abs_g)
                        plx_list.append(float(plx))
                        av = extra.get("ag_gspphot")
                        if av is not None:
                            av_list.append(float(av))
                    except (ValueError, TypeError):
                        pass

        # Source 2: ADQL result cache (run_adql stores here, NOT in "latest")
        if not bp_rp_list:
            adql_cache = get_cached_results("latest_adql")
            if not adql_cache:
                adql_set = get_cached_results("latest_adql_set")
                if isinstance(adql_set, dict):
                    adql_cache = adql_set.get("rows") or adql_set.get("data")
            if isinstance(adql_cache, dict):
                # ADQL data is columnar: {"col_name": [val1, val2, ...]}
                bp_col = adql_cache.get("bp_rp", [])
                g_col = adql_cache.get("phot_g_mean_mag", [])
                plx_col = adql_cache.get("parallax", [])
                ag_col = adql_cache.get("ag_gspphot", [])
                n_rows = max(len(bp_col), len(g_col))
                for i in range(n_rows):
                    try:
                        color = float(bp_col[i]) if i < len(bp_col) and bp_col[i] is not None else None
                        gmag = float(g_col[i]) if i < len(g_col) and g_col[i] is not None else None
                        plx = float(plx_col[i]) if i < len(plx_col) and plx_col[i] is not None else None
                        if color is not None and gmag is not None and plx is not None and plx > 0:
                            abs_g = gmag - 5 * math.log10(1000.0 / plx) + 5
                            bp_rp_list.append(color)
                            abs_mag_list.append(abs_g)
                            plx_list.append(plx)
                            if i < len(ag_col) and ag_col[i] is not None:
                                av_list.append(float(ag_col[i]))
                    except (ValueError, TypeError, IndexError):
                        pass
            elif isinstance(adql_cache, list):
                # Row-based format
                for row in adql_cache:
                    if not isinstance(row, dict):
                        continue
                    color = row.get("bp_rp")
                    gmag = row.get("phot_g_mean_mag")
                    plx = row.get("parallax")
                    if color is not None and gmag is not None and plx is not None and plx > 0:
                        try:
                            abs_g = float(gmag) - 5 * math.log10(1000.0 / float(plx)) + 5
                            bp_rp_list.append(float(color))
                            abs_mag_list.append(abs_g)
                            plx_list.append(float(plx))
                            av = row.get("ag_gspphot")
                            if av is not None:
                                av_list.append(float(av))
                        except (ValueError, TypeError):
                            pass

        if bp_rp_list:
            bp_rp = bp_rp_list
            abs_mag = abs_mag_list
            if plx_list:
                med_plx = float(np.median(plx_list))
            if av_list:
                med_av = float(np.median(av_list))

    if not bp_rp or not abs_mag:
        return {
            "error": "No data available for isochrone fitting. "
            "Run a Gaia query first (search_objects with sources=['gaia'] or run_adql), "
            "then call fit_isochrone again."
        }

    # ── Synchronized NaN/Inf filtering (robust to "null"/"nan" strings) ──
    def _safe_to_float(x):
        if x is None:
            return float("nan")
        if isinstance(x, str):
            if x.strip().lower() in ("null", "nan", "none", "na", ""):
                return float("nan")
            try:
                return float(x)
            except ValueError:
                return float("nan")
        try:
            return float(x)
        except (TypeError, ValueError):
            return float("nan")

    bp_arr = np.array([_safe_to_float(x) for x in bp_rp], dtype=float)
    mag_arr = np.array([_safe_to_float(x) for x in abs_mag], dtype=float)
    min_len = min(len(bp_arr), len(mag_arr))
    bp_arr = bp_arr[:min_len]
    mag_arr = mag_arr[:min_len]
    valid = np.isfinite(bp_arr) & np.isfinite(mag_arr)
    bp_rp = bp_arr[valid].tolist()
    abs_mag = mag_arr[valid].tolist()

    if len(bp_rp) < 5:
        return {"error": f"Need at least 5 valid data points after NaN filtering, got {len(bp_rp)}"}

    # ── Range setup ──
    # We pass ABSOLUTE magnitudes (computed from G - 5*log10(1000/plx) + 5),
    # so the dm parameter in fit_isochrone becomes a small residual correction
    # around zero (accounting for parallax zero-point errors etc).
    # DO NOT let dm_range default to (0, 20) — that would let grid search wander
    # into unphysical territory and return dm=10 as a spurious best.
    dm_range = tuple(inp.get("dm_range", [-0.5, 0.5]))
    av_range = tuple(inp.get("av_range", [0.0, 2.5]))

    av_warning = None
    if med_av is not None and av_range == (0.0, 2.5):
        # Gaia GSP-Phot ag_gspphot is known to be biased high (~3-5×) for
        # low-extinction directions |b| > 15° (Andrae+ 2023 A&A 674, A27).
        # Pleiades 4-branch test 2026-04-15: per-star median A_V = 0.6,
        # literature = 0.12, so a 5× overestimate. If we center av_range on
        # this biased value the fit locks onto a wrong extinction.
        #
        # Policy: only tighten av_range when med_av is small (< 0.3 mag). For
        # larger values, keep the full (0, 2.5) range and let the grid search
        # find the real minimum. Emit a warning so the caller knows the
        # GSP-Phot prior was not trusted.
        if med_av < 0.3:
            av_range = (max(0.0, med_av - 0.2), med_av + 0.6)
        else:
            av_warning = (
                f"Gaia GSP-Phot median A_V = {med_av:.2f} ignored as prior "
                "(known to be biased high by 3-5× for low-extinction lines "
                "of sight). Using full av_range = (0, 2.5). For clusters at "
                "|b| > 15°, verify extinction via lookup_ebv_irsa."
            )

    # ── Try PARSEC isochrone fitting (E0.1: two-stage grid) ──
    # Stage 1 — coarse grid (6 × 3 × 6 × 5 = 540 points ≈ 25-40 s) finds
    # the best cell; Stage 2 — fine grid (12 × 5 × 8 × 5 = 2400 points
    # ≈ 60-90 s) zooms in.  Even on a 200-member CMD the total stays
    # under 150 s, well inside E0.1's new 180 s fit_isochrone deadline.
    import asyncio
    from app.services.astro_analysis import fit_isochrone

    loop = asyncio.get_running_loop()

    def _run_stage(n_age, n_met, dm_r=None, av_r=None):
        return fit_isochrone(
            bp_rp, abs_mag,
            method=method,
            dm_range=dm_r if dm_r is not None else dm_range,
            av_range=av_r if av_r is not None else av_range,
            n_grid_age=n_age,
            n_grid_met=n_met,
        )

    try:
        # Stage 1: coarse pass.
        coarse = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: _run_stage(6, 3)),
            timeout=60.0,
        )
        # Narrow the grid around the coarse best-fit.  Use the coarse
        # result's DM/AV as anchor when available; otherwise fall back
        # to a tight window around the caller's ranges.
        best_logage = coarse.get("best_log_age")
        best_dm = coarse.get("best_dm", (dm_range[0] + dm_range[1]) / 2 if dm_range else None)
        best_av = coarse.get("best_av", (av_range[0] + av_range[1]) / 2 if av_range else None)
        dm_window = (best_dm - 0.3, best_dm + 0.3) if best_dm is not None else dm_range
        av_window = (max(0.0, best_av - 0.2), best_av + 0.2) if best_av is not None else av_range

        result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: _run_stage(12, 5, dm_window, av_window)),
            timeout=100.0,
        )
        result["fit_stages"] = {
            "coarse_best_log_age": best_logage,
            "refined_dm_range": dm_window,
            "refined_av_range": av_window,
        }
        # Sanity check.
        chi2_red = result.get("chi2_reduced", 0)
        if chi2_red is not None and chi2_red > 1e6:
            logger.warning("PARSEC fit chi2_reduced=%.2e is unreasonable, using turnoff estimation", chi2_red)
            raise RuntimeError(f"fit did not converge (chi2_reduced={chi2_red:.2e})")
        for key in ("corner_fig", "chain", "samples", "posterior_samples"):
            if key in result:
                del result[key]
        for k, v in list(result.items()):
            if isinstance(v, list) and len(v) > 100:
                result[k] = f"[truncated: {len(v)} elements]"
        if av_warning:
            result.setdefault("warnings", []).append(av_warning)
        return result
    except (asyncio.TimeoutError, Exception) as exc:
        logger.warning("PARSEC isochrone fitting failed (%s), using turnoff estimation", exc)

    # ── Fallback: turnoff-based age estimation ──
    return _estimate_age_from_turnoff(bp_rp, abs_mag, med_plx, med_av)


def _estimate_age_from_turnoff(bp_rp: list, abs_mag: list,
                                med_plx: float | None, med_av: float | None) -> dict:
    """Estimate cluster age from main-sequence turnoff without PARSEC API.

    Uses a PARSEC-calibrated lookup table (Bressan+ 2012) mapping turnoff
    absolute G magnitude to log(age). This is much more accurate than a
    simple power-law formula.
    """
    import numpy as np

    bp = np.asarray(bp_rp)
    mg = np.asarray(abs_mag)

    # ── Find the main-sequence turnoff ──
    # Physical definition: MSTO is the BLUEST star still on the main sequence
    # (hotter/more massive stars have already evolved off to become subgiants/RGB).
    # Previous "brightest 10% of blue stars" + sigma clipping was unreliable
    # because magnitude outliers and the bulk-magnitude distribution biased
    # the result toward the faint side of the MS.

    # Step 1: Exclude red giants — they are bright AND red, above the MS line.
    # Gaia DR3 solar-metallicity MS ridge (Mamajek 2013 + Bressan+ 2012 PARSEC):
    #   M_G ≈ 0.0 + 4.2 * BP_RP for BP-RP in [0, 2]
    # Use ±3 mag band to include turnoff of clusters from young (BP-RP ≈ 0)
    # through ancient (NGC 188 7 Gyr, turnoff at BP-RP ≈ 0.95, M_G ≈ +4.4).
    ms_ridge = 0.0 + 4.2 * bp
    ms_mask = (mg > ms_ridge - 3.0) & (mg < ms_ridge + 3.0)
    if np.sum(ms_mask) < 10:
        ms_mask = np.ones(len(bp), dtype=bool)

    ms_bp = bp[ms_mask]
    ms_mg = mg[ms_mask]

    # Step 2: Find the MSTO via MS-locus binning (PART E3 fix).
    # The previous "bluest 5%" method was biased high for old clusters:
    # for NGC 752 (1.56 Gyr literature) it returned 3.65 Gyr because
    # blue stragglers + unresolved binaries contaminate the extreme blue
    # tail of the CMD. The true MSTO is the bluest BIN of the MS where
    # the locus is still monotonically populated — isolated blue outliers
    # (stragglers, foreground, photometric errors) don't form a bin.
    #
    # Algorithm:
    #   1. Bin BP-RP in 0.10-mag-wide bins across the MS sample.
    #   2. Require ≥3 stars per valid bin — isolated outliers excluded.
    #   3. The bluest valid bin defines the turnoff: its median BP-RP is
    #      the turnoff color, its median M_G is the turnoff luminosity.
    #
    # Falls back to the older "bluest 5%" method when the sample is
    # too small (<30 stars) to bin reliably.
    if len(ms_bp) >= 30:
        bin_width = 0.10
        bp_min = float(np.min(ms_bp))
        bp_max = float(np.max(ms_bp))
        edges = np.arange(bp_min, bp_max + bin_width, bin_width)
        # For each bin find count + median M_G; keep only bins with ≥3 stars
        valid_bins: list[tuple[float, float]] = []  # (bin_center_bp_rp, median_m_g)
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            in_bin = (ms_bp >= lo) & (ms_bp < hi)
            if int(np.sum(in_bin)) >= 3:
                valid_bins.append((
                    float(np.median(ms_bp[in_bin])),
                    float(np.median(ms_mg[in_bin])),
                ))
        if len(valid_bins) >= 3:
            # Bluest valid bin = MSTO
            valid_bins.sort(key=lambda p: p[0])  # by BP-RP ascending
            turnoff_bp_rp, raw_turnoff_mg = valid_bins[0]
        else:
            # Too few valid bins — fall back to "bluest 5%"
            n_blue = max(5, int(0.05 * len(ms_bp)))
            blue_idx = np.argsort(ms_bp)[:n_blue]
            raw_turnoff_mg = float(np.median(ms_mg[blue_idx]))
            turnoff_bp_rp = float(np.median(ms_bp[blue_idx]))
    else:
        # Small sample — legacy behaviour
        n_blue = max(5, int(0.05 * len(ms_bp)))
        blue_idx = np.argsort(ms_bp)[:n_blue]
        raw_turnoff_mg = float(np.median(ms_mg[blue_idx]))
        turnoff_bp_rp = float(np.median(ms_bp[blue_idx]))

    # ── Guard: refuse to fit when the sample lacks a real turnoff population ──
    # If even the "bluest" stars are redder than BP-RP ≈ 0.9 the sample is
    # almost certainly a low-mass selection with no MSTO coverage. Returning
    # an age from such a sample is scientifically meaningless and was the
    # cause of the Pleiades 125→1193 Myr regression.
    if turnoff_bp_rp > 0.9 and len(bp) > 30:
        return {
            "error": "turnoff_not_sampled",
            "message": (
                f"The input sample's bluest MS stars have BP-RP ≈ "
                f"{turnoff_bp_rp:.2f} — no B/A-type turnoff population. "
                "Age cannot be estimated without a sample that includes the "
                "main-sequence turnoff. Re-query without magnitude / color "
                "pre-filters, or provide an external age constraint."
            ),
            "diagnostics": {
                "bluest_bp_rp_median": round(turnoff_bp_rp, 3),
                "n_stars": len(bp),
                "n_bluest_used": n_blue,
                "hint": "Young clusters need stars with BP-RP < 0.3; "
                        "Gyr-old clusters need BP-RP < 0.7.",
            },
        }

    # Empirical binary bias correction (NOT a published standard value).
    # Physical rationale: equal-mass unresolved binaries are 2.5*log10(2) =
    # 0.753 mag brighter than single stars (Hurley+ 2005). The "brightest 5%"
    # selection preferentially samples binary systems, biasing the measured
    # turnoff brighter than the true single-star MSTO by ~0.2-0.4 mag
    # depending on the cluster's binary fraction (typically 20-50% for
    # open clusters). The 0.3 mag value was tuned against 7 well-studied
    # clusters (Pleiades through NGC 188) to minimize systematic offset.
    BINARY_BIAS_CORRECTION = 0.3
    turnoff_mg = raw_turnoff_mg + BINARY_BIAS_CORRECTION

    # ── PARSEC-calibrated turnoff M_G → log(age) table ──
    # Solar metallicity, Gaia DR3 G-band. Bressan+ 2012 PARSEC 1.2S
    # (CMD 3.9) isochrone turnoff magnitudes. Brighter turnoff → younger.
    # Calibrated against literature ages of well-studied open clusters:
    #   Pleiades (70 Myr), NGC 1647 (200 Myr), Hyades (625 Myr),
    #   NGC 752 (1.4 Gyr), M67 (4 Gyr), NGC 188 (7 Gyr)
    _turnoff_mg = [-4.5, -3.2, -1.5, -0.3, +0.3, +1.2, +1.8, +2.3, +2.8, +3.8, +4.4, +4.8]
    _turnoff_la = [ 7.0,  7.5,  8.0,  8.3,  8.5,  8.8,  9.0,  9.15, 9.3,  9.6,  9.85, 10.0]

    # Enforce monotonicity (np.interp requires sorted x)
    _sorted = sorted(zip(_turnoff_mg, _turnoff_la))
    _turnoff_mg = [p[0] for p in _sorted]
    _turnoff_la = [p[1] for p in _sorted]

    # Clamp to table range
    mg_clamped = max(min(turnoff_mg, _turnoff_mg[-1]), _turnoff_mg[0])
    log_age = float(np.interp(mg_clamped, _turnoff_mg, _turnoff_la))
    age_myr = 10 ** log_age / 1e6

    # Approximate stellar mass from turnoff G magnitude (rough — display only).
    # For A/F turnoff stars: M_V ≈ M_G to within ~0.05 mag (Jordi+ 2010).
    # Uses M_bol_sun = 4.74 directly with G mag (good to ~0.5 mag for A-type).
    # Mass-luminosity slope L ∝ M^3.5 (Salpeter, intermediate-mass MS).
    log_l = (4.74 - turnoff_mg) / 2.5
    mass = max(10 ** (log_l / 3.5), 0.3)

    # Distance from parallax
    distance_pc = 1000.0 / med_plx if med_plx and med_plx > 0 else None

    warnings_list: list[str] = []
    reported_av: float | None = None
    if med_av is not None:
        # Gaia GSP-Phot A_V is biased high by 3-5× for |b|>15° (Andrae+ 2023).
        # If the turnoff fallback is being used we have no chi² to fit A_V
        # against, so don't report an unreliable GSP-Phot-derived value.
        if med_av < 0.3:
            reported_av = round(med_av, 3)
        else:
            warnings_list.append(
                f"Gaia GSP-Phot median A_V = {med_av:.2f} suppressed "
                "(known high bias for low-extinction lines of sight). "
                "Use lookup_ebv_irsa to get the true SFD/Planck A_V."
            )

    return {
        "best_fit": {
            "log_age": round(log_age, 3),
            "age_myr": round(age_myr, 1),
            "distance_pc": round(distance_pc, 1) if distance_pc else None,
            "A_V": reported_av,
        },
        "turnoff": {
            "abs_mag_G": round(turnoff_mg, 3),
            "bp_rp": round(turnoff_bp_rp, 3),
            "approx_mass_msun": round(mass, 2),
        },
        "method": "turnoff_fallback (MS-locus binning + PARSEC-calibrated lookup)",
        "n_data": len(bp_rp),
        "note": "Age from PARSEC-calibrated turnoff M_G → log(age) table. "
                "Turnoff colour = bluest BP-RP bin (0.10-mag width, ≥3 stars). "
                "Brighter turnoff → higher mass → YOUNGER cluster.",
        "warnings": warnings_list,
    }


async def _exec_get_dossier(inp: dict) -> dict:
    """Generate a cross-match dossier for sky coordinates."""
    from app.services.dossier_generator import generate_dossier

    ra = inp.get("ra")
    dec = inp.get("dec")
    name = inp.get("name")

    if ra is None or dec is None:
        if name:
            from app.services.name_resolver import resolve_name
            resolved = await resolve_name(name)
            if not resolved.resolved:
                return {"error": f"Could not resolve '{name}': tried {resolved.aliases_tried}"}
            ra, dec = resolved.ra, resolved.dec
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
        if name:
            from app.services.name_resolver import resolve_name
            resolved = await resolve_name(name)
            if not resolved.resolved:
                return {"error": f"Could not resolve '{name}': tried {resolved.aliases_tried}"}
            ra, dec = resolved.ra, resolved.dec
        else:
            return {"error": "ra and dec are required (or provide a name for resolution)"}

    result = await cross_wavelength_analysis(ra=ra, dec=dec)
    return result


async def _exec_crossmatch_catalogs(inp: dict, python_session_id: str = "default") -> dict:
    """Cross-match two catalogs obtained via ADQL queries."""
    import pandas as pd
    from app.api.integration import execute_adql_query, ADQLRequest
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
        result = await execute_adql_query(ADQLRequest(query=query, service=service))
        data = result.get("data", {}) if isinstance(result, dict) else {}
        if not data:
            raise ValueError(f"ADQL query returned no data: {query[:80]}...")
        # M17: TAP responses occasionally have ragged columns when optional
        # fields are sparsely populated.  pd.DataFrame() raises a cryptic
        # "All arrays must be of the same length" in that case; pad the
        # shorter columns with None so the caller gets a helpful error
        # downstream (or the data flows through unchanged).
        max_len = max((len(v) for v in data.values() if isinstance(v, list)), default=0)
        padded = {}
        for col, values in data.items():
            if isinstance(values, list) and len(values) < max_len:
                padded[col] = values + [None] * (max_len - len(values))
            else:
                padded[col] = values
        return pd.DataFrame(padded)

    try:
        t1, t2 = await asyncio.gather(
            _run_adql(q1, service1),
            _run_adql(q2, service2),
        )
    except Exception as e:
        return {"error": f"Failed to fetch catalogs: {e}"}

    # M16: ADQL TAP responses commonly return uppercase column names (RA,
    # DEC).  Normalise to lowercase before the cross-match engine sees them
    # so a valid query doesn't get rejected over casing alone.
    for tbl in (t1, t2):
        rename_map = {c: c.lower() for c in tbl.columns if c != c.lower()}
        if rename_map:
            tbl.rename(columns=rename_map, inplace=True)

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

    target = str(inp.get("target") or "").strip()
    mission = str(inp.get("mission") or "kepler").strip().lower()
    # K2: 明确的 "target 缺失" 错误 — 第三次回归里 AI 第一次调用时漏了
    # target 参数, 只看到无信息的 "target is required" 然后第二次才补回.
    # 把示例直接写进错误消息, AI 第一次就能修对.
    if not target:
        return {
            "error": (
                "`target` is required.  Pass the star name or catalog ID — "
                "e.g. target='HD 189733', 'Kepler-10', 'TIC 261136679', "
                "'KIC 11904151', 'EPIC 201367065', or 'delta Cep'.  Mission "
                "can be 'kepler' (default), 'tess', or 'k2'."
            ),
            "error_class": "missing_argument",
            "argument": "target",
        }
    if mission not in {"kepler", "tess", "k2"}:
        return {
            "error": (
                f"`mission` must be one of 'kepler' / 'tess' / 'k2' "
                f"(got {mission!r})."
            ),
            "error_class": "invalid_argument",
            "argument": "mission",
        }

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: search_lightcurve(target, mission=mission))
    return result


async def _exec_reduce_ccd_image(inp: dict) -> dict:
    from app.analysis.image_reduction import full_reduction

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        full_reduction,
        inp["science_fits_path"],
        inp.get("bias_paths"),
        inp.get("dark_paths"),
        inp.get("flat_paths"),
        inp.get("cosmic_ray", True),
    )
    return {
        "output_path": result.get("output_path"),
        "reduction_log": result.get("reduction_log", []),
        "cosmic_ray_mask_pixels": result.get("cosmic_ray_mask_pixels", 0),
        "header": result.get("header", {}),
    }


async def _exec_solve_astrometry(inp: dict) -> dict:
    from app.analysis.image_reduction import solve_astrometry

    return await solve_astrometry(inp["fits_path"])


async def _exec_extract_photometry(inp: dict) -> dict:
    from app.analysis.image_reduction import extract_and_photometer

    loop = asyncio.get_running_loop()
    table = await loop.run_in_executor(
        None,
        extract_and_photometer,
        inp["fits_path"],
        inp.get("aperture_radii"),
    )
    rows = table.where(table.notna(), None).to_dict(orient="records")
    return {
        "row_count": int(len(table)),
        "columns": list(table.columns),
        "rows": rows,
    }


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


async def _exec_classify_transient(inp: dict) -> dict:
    """Classify a transient from features or raw light curve data."""
    from app.services.transient_classifier import TransientClassifier, extract_lc_features

    features = inp.get("features")
    times = inp.get("times")
    magnitudes = inp.get("magnitudes")
    mag_errors = inp.get("mag_errors")

    if features is None and times is None:
        return {"error": "Either 'features' dict or 'times'+'magnitudes' arrays are required"}

    loop = asyncio.get_running_loop()

    def _do_classify():
        if features is not None:
            return TransientClassifier.classify_transient(features)
        # Extract features from raw light curve, then classify
        mags = magnitudes or []
        errs = mag_errors if mag_errors else None
        extracted = extract_lc_features(times, mags, errs)
        return TransientClassifier.classify_transient(extracted)

    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, _do_classify),
            timeout=60.0,
        )
    except asyncio.TimeoutError:
        return {"error": "Transient classification timed out after 60 seconds"}

    return result


# ── P1/P2 workflow tool executors ──

def _exec_compute_galaxy_sfr(inp: dict) -> dict:
    """Compute SFR from luminosities using Kennicutt & Evans 2012 ARA&A 50, 531 Table 1.

    All calibrations assume Kroupa IMF, 0.1-100 Msun. Formulas:
        log(SFR/Msun yr^-1) = log(L) - log C
    where log C is band-specific (values from K&E 2012 Table 1).
    """
    import math

    # K&E 2012 Table 1 coefficients — DO NOT MODIFY
    # (each entry is log C such that log SFR = log L - log C)
    _KE12 = {
        "L_Halpha":  41.27,  # erg/s
        "L_FUV":     43.35,  # νL_ν at 1500 Å, erg/s
        "L_NUV":     43.17,  # νL_ν at 2300 Å, erg/s
        "L_TIR":     43.41,  # total IR 8-1000μm, erg/s
        "L_24um":    42.69,  # νL_ν at 24 μm, erg/s
        "L_70um":    43.23,  # νL_ν at 70 μm, erg/s
        "L_1p4GHz":  28.20,  # 1.4 GHz L_ν, erg/s/Hz
    }

    sfrs = {}
    for band, log_c in _KE12.items():
        L = inp.get(band)
        if L is None or L <= 0:
            continue
        try:
            log_sfr = math.log10(float(L)) - log_c
            sfrs[band] = round(10 ** log_sfr, 4)
        except (ValueError, TypeError):
            continue

    if not sfrs:
        return {
            "error": "Provide at least one luminosity: L_Halpha, L_FUV, L_NUV, L_TIR, L_24um, L_70um, or L_1p4GHz (all in erg/s, except L_1p4GHz in erg/s/Hz)"
        }

    # Weighted mean (geometric mean of log SFR)
    log_sfrs = [math.log10(v) for v in sfrs.values()]
    mean_log = sum(log_sfrs) / len(log_sfrs)
    mean_sfr = 10 ** mean_log

    # Dust correction (optional)
    dust_note = None
    ha_hb = inp.get("Ha_Hb_ratio")
    if ha_hb and ha_hb > 2.86:
        # Balmer decrement → E(B-V)_gas = 1.97 × log10(ratio / 2.86)
        ebv_gas = 1.97 * math.log10(ha_hb / 2.86)
        dust_note = (
            f"Balmer decrement suggests E(B-V)_gas = {ebv_gas:.2f}, "
            f"A_Hα ≈ {2.53 * ebv_gas:.2f} mag. "
            f"Correct L_Hα by factor 10^(0.4 × A_Hα) before passing to this tool."
        )

    return {
        "individual_SFRs": sfrs,
        "mean_SFR_Msun_yr": round(mean_sfr, 4),
        "log_mean_SFR": round(mean_log, 3),
        "n_bands": len(sfrs),
        "reference": "Kennicutt & Evans 2012 ARA&A 50, 531 Table 1 (Kroupa IMF, 0.1-100 Msun)",
        "dust_correction_note": dust_note,
    }


async def _exec_fit_rv_orbit(inp: dict) -> dict:
    """Fit a Keplerian RV orbit using radvel or thejoker."""
    import asyncio as _aio

    times = inp.get("times") or []
    rvs = inp.get("rvs") or []
    rv_errs = inp.get("rv_errs") or []

    if len(times) < 5 or len(rvs) != len(times) or len(rv_errs) != len(times):
        return {"error": f"Need ≥5 observations with matching rvs/rv_errs arrays. Got times={len(times)}, rvs={len(rvs)}, errs={len(rv_errs)}"}

    # `method` is reserved for future switching between radvel and thejoker;
    # current implementation only supports radvel.
    _ = inp.get("method") or "radvel"
    p_min = float(inp.get("period_min") or 1.0)
    p_max = float(inp.get("period_max") or 1000.0)

    use_mcmc = bool(inp.get("use_mcmc", True))
    n_walkers = int(inp.get("n_walkers") or 32)
    n_steps = int(inp.get("n_steps") or 1500)
    n_burn = int(inp.get("n_burn") or 500)
    random_seed = inp.get("random_seed")

    def _do_fit():
        import numpy as _np
        t = _np.asarray(times, dtype=float)
        v = _np.asarray(rvs, dtype=float)
        e = _np.asarray(rv_errs, dtype=float)

        try:
            import radvel
            from radvel import posterior
            from scipy import optimize as _opt
        except ImportError:
            return {"error": "radvel not available. Install via pip install radvel"}

        from astropy.timeseries import LombScargle
        ls = LombScargle(t, v, dy=e)
        freqs, power = ls.autopower(minimum_frequency=1.0/p_max, maximum_frequency=1.0/p_min)
        p_init = 1.0 / freqs[_np.argmax(power)]

        params = radvel.Parameters(1, basis="per tc e w k")
        params["per1"] = radvel.Parameter(value=p_init)
        params["tc1"] = radvel.Parameter(value=float(t[0]))
        params["e1"] = radvel.Parameter(value=0.05)
        params["w1"] = radvel.Parameter(value=0.0)
        params["k1"] = radvel.Parameter(value=float(_np.std(v)))
        params["dvdt"] = radvel.Parameter(value=0.0, vary=False)
        params["curv"] = radvel.Parameter(value=0.0, vary=False)

        model = radvel.RVModel(params)
        like = radvel.likelihood.RVLikelihood(model, t, v, e)
        post = posterior.Posterior(like)

        res = _opt.minimize(
            lambda x: -post.logprob_array(x),
            post.get_vary_params(),
            method="Nelder-Mead",
            options={"maxiter": 500, "xatol": 1e-4},
        )

        # D3.3 — MCMC-backed uncertainties on top of the local optimum,
        # with stellar jitter σ_jit as a free parameter (half-normal
        # prior with scale = median(e)).  Falls back to the deterministic
        # Nelder-Mead fit when emcee is unavailable.
        mcmc_results: dict = {"ran_mcmc": False}
        if use_mcmc:
            try:
                import emcee

                init = _np.asarray(post.get_vary_params(), dtype=float)
                # Append jitter parameter at the end.
                jitter_init = float(_np.median(e))
                init = _np.concatenate([init, [jitter_init]])
                n_dim = init.size

                def _log_prob(theta):
                    theta_rv = theta[:-1]
                    jitter = float(theta[-1])
                    if jitter < 0:
                        return -_np.inf
                    try:
                        lp = post.logprob_array(theta_rv)
                    except Exception:
                        return -_np.inf
                    # Add jitter penalty: likelihood uses sqrt(e^2 + jitter^2).
                    # We use the Gaussian approximation: re-evaluate chi^2
                    # under inflated errors; subtract the radvel likelihood's
                    # baseline (too expensive to re-derive cleanly, so we
                    # treat this as a penalty term).
                    jprior = -0.5 * (jitter / jitter_init) ** 2
                    return float(lp) + jprior

                rng_seed = int(random_seed) if random_seed is not None else 2024
                rng = _np.random.default_rng(rng_seed)
                p0 = init + 1e-3 * rng.standard_normal((n_walkers, n_dim)) * _np.abs(init + 1e-6)
                sampler = emcee.EnsembleSampler(n_walkers, n_dim, _log_prob)
                sampler.run_mcmc(p0, n_steps, progress=False)
                chain = sampler.get_chain(discard=n_burn, flat=True)
                mcmc_results["ran_mcmc"] = True
                mcmc_results["n_samples"] = int(chain.shape[0])

                try:
                    import arviz as _az
                    mcmc_results["hdi_68_jitter_ms"] = _az.hdi(
                        chain[:, -1], hdi_prob=0.68,
                    ).tolist()
                except Exception:
                    mcmc_results["hdi_68_jitter_ms"] = [
                        float(_np.percentile(chain[:, -1], 16)),
                        float(_np.percentile(chain[:, -1], 84)),
                    ]
                mcmc_results["jitter_ms_median"] = float(_np.median(chain[:, -1]))
                mcmc_results["param_covariance"] = (
                    _np.cov(chain[:, :-1].T).tolist()
                    if chain.shape[1] > 2 else None
                )
            except ImportError:
                mcmc_results["note"] = "emcee unavailable; point-estimate only"
            except Exception as _exc:
                mcmc_results["note"] = f"MCMC failed: {_exc}"

        P = post.params["per1"].value
        K = post.params["k1"].value
        ecc = post.params["e1"].value
        w = post.params["w1"].value
        tc = post.params["tc1"].value

        G = 6.674e-11
        P_sec = P * 86400.0
        fm_kg = P_sec * (K ** 3) * ((1 - ecc ** 2) ** 1.5) / (2 * _np.pi * G)
        fm_msun = fm_kg / 1.989e30

        return {
            "period_days": round(float(P), 4),
            "semi_amplitude_ms": round(float(K), 2),
            "eccentricity": round(float(ecc), 4),
            "omega_deg": round(float(_np.degrees(w)), 2),
            "t_conjunction": round(float(tc), 4),
            "mass_function_Msun": float(f"{fm_msun:.3e}"),
            "n_observations": len(times),
            "fit_success": bool(res.success),
            "method": "radvel + emcee" if mcmc_results.get("ran_mcmc") else "radvel",
            "mcmc": mcmc_results,
            "reference": "Fulton+ 2018 PASP 130, 044504; Hilditch 2001 Eq 2.53 for mass function",
        }

    loop = _aio.get_running_loop()
    try:
        return await _aio.wait_for(loop.run_in_executor(None, _do_fit), timeout=60.0)
    except _aio.TimeoutError:
        return {"error": "RV orbit fitting timed out after 60 s"}
    except Exception as exc:
        return {"error": f"RV fit failed: {exc}"}


async def _exec_fit_sersic(inp: dict) -> dict:
    """Fit Sersic profile + compute non-parametric morphology via statmorph."""
    import asyncio as _aio

    fits_path = inp.get("fits_path")
    if not fits_path:
        return {"error": "fits_path is required"}

    def _do_fit():
        try:
            import statmorph
            from astropy.io import fits as _fits
            import numpy as _np
        except ImportError:
            return {"error": "statmorph not available. Install via pip install statmorph"}

        import os as _os
        base_dir = _os.path.join(_os.path.dirname(__file__), "..", "..", "data")
        full_path = _os.path.normpath(_os.path.join(base_dir, fits_path))
        if not _os.path.isfile(full_path):
            full_path = fits_path
        if not _os.path.isfile(full_path):
            return {"error": f"FITS not found: {fits_path}"}

        with _fits.open(full_path) as hdul:
            image = hdul[0].data
        if image is None:
            return {"error": "FITS file has no image data"}
        image = _np.asarray(image, dtype=float)

        # Simple segmentation: threshold above 1.5 sigma
        from photutils.segmentation import detect_sources
        threshold = _np.nanmedian(image) + 1.5 * _np.nanstd(image)
        segmap = detect_sources(image, threshold, npixels=10)
        if segmap is None or segmap.nlabels == 0:
            return {"error": "No sources detected"}

        # Use the largest source
        labels = segmap.labels
        sizes = [_np.sum(segmap.data == lab) for lab in labels]
        main_label = labels[int(_np.argmax(sizes))]
        segmap_main = (segmap.data == main_label).astype(int) * main_label

        # Statmorph wants labelled integer segmap matching source_id=main_label
        morph = statmorph.source_morphology(
            image, segmap_main, weightmap=_np.ones_like(image), psf=None,
        )
        if not morph:
            return {"error": "statmorph returned no morphology"}
        m = morph[0]
        return {
            "sersic_n": round(float(m.sersic_n), 3),
            "sersic_Re_pixels": round(float(m.sersic_rhalf), 3),
            "sersic_ellip": round(float(m.sersic_ellip), 3),
            "sersic_PA_deg": round(float(_np.degrees(m.sersic_theta)), 2),
            "gini": round(float(m.gini), 4),
            "M20": round(float(m.m20), 4),
            "concentration": round(float(m.concentration), 3),
            "asymmetry": round(float(m.asymmetry), 4),
            "smoothness": round(float(m.smoothness), 4),
            "flag": int(m.flag),
            "reference": "statmorph (Rodriguez-Gomez+ 2019 MNRAS 483, 4140); Sersic 1963",
        }

    loop = _aio.get_running_loop()
    try:
        return await _aio.wait_for(loop.run_in_executor(None, _do_fit), timeout=120.0)
    except _aio.TimeoutError:
        return {"error": "Sersic fit timed out after 120 s"}
    except Exception as exc:
        return {"error": f"Sersic fit failed: {exc}"}


async def _exec_x_ray_spectral_fit(inp: dict) -> dict:
    """Fit an X-ray spectrum using Sherpa."""
    import asyncio as _aio

    pha_path = inp.get("pha_path")
    model_expr = inp.get("model")
    if not pha_path or not model_expr:
        return {"error": "pha_path and model are required"}

    def _do_fit():
        try:
            from sherpa.astro import ui as _sui
        except ImportError:
            return {"error": "sherpa not available. Install via pip install sherpa"}

        import os as _os
        base_dir = _os.path.join(_os.path.dirname(__file__), "..", "..", "data")
        full_path = _os.path.normpath(_os.path.join(base_dir, pha_path))
        if not _os.path.isfile(full_path):
            full_path = pha_path
        if not _os.path.isfile(full_path):
            return {"error": f"PHA not found: {pha_path}"}

        try:
            _sui.load_pha(full_path)
            _sui.notice(inp.get("energy_min") or 0.5, inp.get("energy_max") or 8.0)
            # NOTE: We cannot dynamically instantiate arbitrary Sherpa models
            # from a string without eval. Require user to pre-set model via
            # a known template or use the sherpa UI from run_python directly.
            # For this tool we support a limited set of templates:
            tpl = model_expr.lower().strip()
            if "powerlaw" in tpl and "apec" not in tpl:
                _sui.set_source(_sui.xsphabs.abs1 * _sui.xspowerlaw.pl)
            elif "apec" in tpl:
                _sui.set_source(_sui.xsphabs.abs1 * _sui.xsapec.thermal)
            elif "diskbb" in tpl and "powerlaw" in tpl:
                _sui.set_source(_sui.xsphabs.abs1 * (_sui.xsdiskbb.disk + _sui.xspowerlaw.pl))
            else:
                return {
                    "error": f"Model template not recognised: '{model_expr}'. "
                             f"Supported: 'phabs*powerlaw', 'phabs*apec', 'phabs*(diskbb+powerlaw)'. "
                             f"For custom models, use run_python with sherpa.astro.ui directly."
                }
            stat = inp.get("statistic") or "chi2"
            _sui.set_stat("cstat" if stat == "cstat" else "chi2datavar")
            _sui.fit()
            result = _sui.get_fit_results()
            params = {}
            for p in result.parnames:
                try:
                    params[p] = round(float(_sui.get_par(p).val), 4)
                except Exception:
                    pass
            return {
                "parameters": params,
                "statistic": stat,
                "statval": round(float(result.statval), 3),
                "dof": int(result.dof),
                "reduced_stat": round(float(result.rstat), 4) if result.rstat else None,
                "reference": "Sherpa (Doe+ 2007 ASP 376, 543); tbabs/apec/diskbb from XSPEC models",
            }
        except Exception as e:
            return {"error": f"Sherpa fit failed: {type(e).__name__}: {str(e)[:200]}"}

    loop = _aio.get_running_loop()
    try:
        return await _aio.wait_for(loop.run_in_executor(None, _do_fit), timeout=120.0)
    except _aio.TimeoutError:
        return {"error": "X-ray fit timed out after 120 s"}


def _exec_pulsar_derived(inp: dict) -> dict:
    """Compute pulsar derived quantities from P and Ṗ.

    Formulas from Lorimer & Kramer 2004 "Handbook of Pulsar Astronomy":
    - Characteristic age τ_c = P / (2 Ṗ)           (Eq 3.16)
    - Surface B field B_s ≈ 3.2e19 √(P Ṗ) Gauss    (Eq 3.18)
    - Spin-down luminosity Ė = 4π² I Ṗ / P³        (Eq 3.14)
    """
    import math

    P = inp.get("period_s")
    Pdot = inp.get("period_dot")
    if P is None or Pdot is None:
        return {"error": "period_s (seconds) and period_dot (dimensionless) are required"}
    try:
        P = float(P)
        Pdot = float(Pdot)
    except (TypeError, ValueError):
        return {"error": "period_s and period_dot must be numeric"}
    if P <= 0:
        return {"error": "period_s must be positive"}
    if Pdot <= 0:
        return {"error": "period_dot must be positive (spin-down)"}

    # D2.7 — I is class-dependent.  Users can pass a numeric override,
    # a class keyword (young NS, recycled MSP, magnetar), or rely on the
    # canonical 1e45 g·cm² value.  The braking index n=3 assumption is
    # also now explicit in the output envelope.
    pulsar_class = str(inp.get("pulsar_class") or "").lower().strip()
    _class_I = {
        "young": 1e45,
        "recycled": 1.4e45,   # MSPs, typically more massive
        "msp": 1.4e45,
        "magnetar": 1e45,
    }
    if inp.get("moment_of_inertia_g_cm2") is not None:
        I_moi = float(inp["moment_of_inertia_g_cm2"])
        I_source = "user_supplied"
    elif pulsar_class in _class_I:
        I_moi = _class_I[pulsar_class]
        I_source = f"class_default_{pulsar_class}"
    else:
        I_moi = 1e45
        I_source = "canonical_1e45"

    # Braking index — default n=3 (magnetic dipole); caller may supply
    # measured value (e.g. Crab n≈2.51).  Characteristic age changes:
    # τ_c = P / ((n-1) Ṗ).  For n=3, the 1/(2 Ṗ) form recovers.
    n_brake = float(inp.get("braking_index") or 3.0)
    if n_brake <= 1.0:
        n_brake = 3.0

    tau_c_sec = P / ((n_brake - 1.0) * Pdot)
    tau_c_yr = tau_c_sec / (365.25 * 86400)

    B_s_Gauss = 3.2e19 * math.sqrt(P * Pdot)

    # Ė = 4π² I Ṗ / P³  in erg/s (I in g·cm²)
    E_dot = 4.0 * (math.pi ** 2) * I_moi * Pdot / (P ** 3)

    return {
        "characteristic_age_yr": float(f"{tau_c_yr:.3e}"),
        "characteristic_age_Myr": round(tau_c_yr / 1e6, 3),
        "surface_B_field_G": float(f"{B_s_Gauss:.3e}"),
        "surface_B_field_log10": round(math.log10(B_s_Gauss), 3),
        "spin_down_luminosity_erg_s": float(f"{E_dot:.3e}"),
        "period_s": P,
        "period_dot": Pdot,
        "moment_of_inertia_g_cm2": I_moi,
        "moment_of_inertia_source": I_source,
        "pulsar_class": pulsar_class or "unspecified",
        "braking_index_assumed": n_brake,
        "assumptions_note": (
            "Characteristic age assumes constant braking index n; surface B "
            "assumes pure magnetic-dipole braking with R=10 km, sin α=1; "
            "Ė assumes spherical canonical I.  User-supplied I or class "
            "default overrides the canonical 1e45 g·cm² value."
        ),
        "reference": "Lorimer & Kramer 2004 Handbook of Pulsar Astronomy Eq 3.14, 3.16, 3.18",
    }


def _retryable_python_fix(code: str, error: str) -> tuple[str, str] | None:
    if "Unknown format code 'd' for object of type 'float'" in error:
        fixed = re.sub(r":(\d*)d(?=})", lambda m: f":{m.group(1)}.0f", code)
        if fixed != code:
            return fixed, "Adjusted float formatting from integer `:d` to `:.0f` and retried."

    # KeyError for column names — try swapping case (e.g. 'SOURCE_ID' vs 'source_id')
    key_match = re.search(r"KeyError:\s*['\"](\w+)['\"]", error)
    if key_match:
        bad_key = key_match.group(1)
        alt_key = bad_key.lower() if bad_key == bad_key.upper() else bad_key.upper()
        if alt_key != bad_key:
            fixed = code.replace(f"'{bad_key}'", f"'{alt_key}'").replace(f'"{bad_key}"', f'"{alt_key}"')
            if fixed != code:
                return fixed, f"Swapped column name case from '{bad_key}' to '{alt_key}' and retried."

    return None
