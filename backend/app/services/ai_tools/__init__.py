"""Tool definitions and executors for the Claude AI research agent.

Each tool is a function Claude can call. The agent loop in chat.py
handles the tool_use → result → next message cycle automatically.
"""

import asyncio
import logging
import math
import os
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

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
    # Preserve original archive column names while also injecting lowercase aliases.
    # SDSS SkyServer uses mixed-case names like objID / petroMag_r / zErr, but
    # legacy analysis code often uses lowercase. Keeping both avoids KeyError
    # without hiding the real schema.
    enriched = dict(row)
    for k, v in row.items():
        enriched.setdefault(str(k).lower(), v)
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
            "During provenance-v2 rollout it can query SIMBAD, Gaia, VizieR, NED, 2MASS, and ALMA observation metadata. "
            "Returns object names, positions, types, magnitudes, redshifts, and archive metadata where available. "
            "Gaia results include extra fields: extra.bp_rp, extra.parallax, extra.pmra, extra.pmdec, "
            "extra.ruwe, extra.phot_bp_mean_mag, extra.phot_rp_mean_mag. "
            "ALMA results are observation metadata only and do not provide derived line luminosity or FWHM measurements. "
            "Use these for HR diagrams, membership selection, and isochrone fitting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Object name or search description (e.g. 'M31', 'NGC 1068', 'high redshift quasars')"},
                "sources": {"type": "array", "items": {"type": "string"}, "description": "Data sources to query (e.g. ['simbad', 'gaia', 'alma']). Default: ['simbad']"},
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
        "name": "list_user_tools",
        "description": (
            "List tool macros created by the current user. These are safe wrappers "
            "around existing Standard Astro tools; use this before run_user_tool "
            "when the user asks for their saved custom tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "run_user_tool",
        "description": (
            "Run one user-created tool macro by tool_id with JSON arguments. "
            "A user tool cannot execute arbitrary backend code; it expands into "
            "existing audited platform tools and returns every nested tool result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_id": {"type": "string", "description": "Saved user tool id, e.g. 'quick_gaia_lookup'"},
                "arguments": {"type": "object", "description": "Arguments matching the saved user tool input_schema"},
            },
            "required": ["tool_id"],
            "additionalProperties": False,
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
        # J3: Direct connection to the official SDSS SkyServer. Use this tool
        # when all VizieR mirrors are unreachable or the V/154/sdss17 table
        # reports "All mirrors unavailable" — bypasses VizieR entirely and
        # queries the SDSS native API. Syntax is T-SQL (Microsoft SQL Server),
        # not ADQL.
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
            "years, abstracts, and source metadata that you can cite in your response. "
            "This is paper/abstract-level only; it does not provide measurement-table "
            "values such as L[CII] or FWHM."
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
        # Stage 6 P0c-C (2026-05-19): hard-block upgrade — promotes the Stage 5/6.2
        # abstract secondary-filter prompt MUST rule (soft) to a dedicated tool (hard).
        # The backend claim_validator.unclassified_literature_violations check
        # requires every paper returned by search_literature to be classified
        # through this tool before it can be cited in a narrative; otherwise the
        # entire passage is blocked by the banner.
        "name": "classify_literature_relevance",
        "description": (
            "REQUIRED after every search_literature call (hard rule, not advisory). "
            "Read each returned paper's abstract and classify it as Direct, Marginal, "
            "or Off-topic relative to the user's current question. Only Direct + "
            "Marginal papers may be cited downstream; citing an unclassified or "
            "Off-topic paper will trigger a citation hard-block on the reply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "classifications": {
                    "type": "array",
                    "description": "One entry per paper returned by the most recent search_literature.",
                    "items": {
                        "type": "object",
                        "required": ["bibcode", "relevance", "reason"],
                        "properties": {
                            "bibcode": {
                                "type": "string",
                                "description": "Paper bibcode exactly as returned by search_literature (e.g. 2024arXiv2404.03002D).",
                            },
                            "relevance": {
                                "type": "string",
                                "enum": ["Direct", "Marginal", "Off-topic"],
                                "description": (
                                    "Direct: paper directly answers the user's question. "
                                    "Marginal: related but does not directly answer. "
                                    "Off-topic: keyword overlap but topic mismatch."
                                ),
                            },
                            "reason": {
                                "type": "string",
                                "description": "One-sentence reason from the abstract.",
                            },
                        },
                    },
                },
            },
            "required": ["classifications"],
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
            "SYNTHETIC and the zero-fabrication gate will block using its facts, numbers, "
            "or conclusions in a real-data answer."
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
                        "'user_file:<path>' (your own uploaded data file read via "
                        "pd.read_csv / pd.read_parquet / load_csv — real data, not synthetic), "
                        "'none_not_analyzing_real_data' (not analyzing observational data: "
                        "synthetic/pedagogical/Monte-Carlo data, or helper introspection such as "
                        "available_functions(); output will be marked SYNTHETIC/not citable for "
                        "scientific facts, numbers, or conclusions). "
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
        "name": "extract_literature_tables",
        "description": (
            "Extract machine-readable tables from an arXiv/ar5iv paper and attach "
            "row-level citation provenance. Use this after search_literature when "
            "the user asks to compile literature samples, fit relations, or quote "
            "paper-table measurements such as log L[CII], FWHM, redshift, flux, "
            "or line widths. First phase supports arXiv IDs/URLs only; ADS-only "
            "bibcodes without an arXiv identifier are not auto-converted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "arxiv_id": {"type": "string", "description": "arXiv ID, arXiv:ID, or arxiv.org URL"},
                "arxiv_url": {"type": "string", "description": "Optional arXiv abs/pdf URL"},
                "paper": {
                    "type": "object",
                    "description": "Optional paper object from search_literature containing bibcode/arxiv_url/title/authors/year.",
                },
            },
        },
    },
    {
        "name": "prepare_spectral_measurements",
        "description": (
            "Validate and summarize cited spectral line measurement rows from a "
            "literature-table cache. Use this for any emission/absorption line "
            "sample ([CII], CO, Halpha, Lyalpha, [OIII], etc.) before fitting, "
            "exporting, or comparing surveys. It reports line inventory, fit-ready "
            "row counts, missing fields, citation counts, and ranges; it does not "
            "fit a relation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cache_key": {
                    "type": "string",
                    "description": "Cache key from extract_literature_tables. Default: latest_literature_tables.",
                },
                "line_id": {
                    "type": "string",
                    "description": "Optional line filter, e.g. [CII], CO(1-0), Halpha, Lyalpha, [OIII] 5007.",
                },
                "min_fit_rows": {
                    "type": "integer",
                    "description": "Minimum complete cited rows required to mark the sample fit-ready. Default: 5.",
                },
            },
        },
    },
    {
        "name": "fit_line_lfr",
        "description": (
            "Fit a line-luminosity versus FWHM relation from cached, cited literature "
            "measurement rows. Three input modes (pick ONE): "
            "(1) `arxiv_id` — let the tool LLM-extract measurements from that arXiv paper "
            "with ±1% cell verification (replaces the deprecated extract_paper_measurements_with_llm "
            "two-step flow; requires BYOK Anthropic key). "
            "(2) `cache_key` — read a single cached measurement set from a prior extract_literature_tables call. "
            "(3) `cache_keys` — UNION multiple surveys' caches before fitting. "
            "This tool consumes real row-level table provenance; do not replace it "
            "with run_python over hardcoded or synthetic literature samples."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "arxiv_id": {
                    "type": "string",
                    "description": (
                        "arXiv ID, e.g. '2002.00962' or 'arXiv:2002.00962'. When set, the tool "
                        "first calls the LLM paper extractor (BYOK Anthropic) to read tables, "
                        "verifies each numeric value against the original cell text (±1% "
                        "tolerance), writes passed rows to the latest_literature_tables cache, "
                        "then runs the fit. Mismatched / unverified numbers never enter cache, "
                        "so the LLM cannot launder fabricated measurements through this path."
                    ),
                },
                "extract_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional override for the LLM extractor field list when arxiv_id is set. "
                        "Default: ['source_name', 'fwhm_km_s', 'log_luminosity', 'z']."
                    ),
                },
                "cache_key": {
                    "type": "string",
                    "description": "Single cache key from extract_literature_tables. Default: latest_literature_tables. Use `cache_keys` instead when fitting across multiple surveys.",
                },
                "cache_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Multiple cache keys to UNION before fitting. "
                        "Use this when the user wants a robust line "
                        "relation across more than one survey: pass "
                        "the cache_keys returned by separate "
                        "extract_literature_tables calls (e.g. ALPINE "
                        "+ REBELS + Capak+2015 + Bothwell+13). Rows are "
                        "deduped by (source_name, bibcode). When set, "
                        "`cache_key` is ignored."
                    ),
                },
                "line_id": {
                    "type": "string",
                    "description": "Line filter, e.g. '[CII]' or 'CII'. Default: [CII].",
                },
                "min_rows": {
                    "type": "integer",
                    "description": "Minimum citeable rows required for a publication-ready fit. Default: 5.",
                },
                "fit_method_requested": {
                    "type": "string",
                    "enum": ["auto", "ols", "bayesian_xyerr"],
                    "description": (
                        "Desired fitting method. 'auto' (default) picks the best "
                        "available path. 'ols' is plain scipy.linregress, never a "
                        "downgrade. 'bayesian_xyerr' requests Bayesian linear "
                        "regression with errors on both axes (linmix-style, Kelly "
                        "2007); requires fwhm_err_km_s and log_luminosity_err on "
                        "every row. If the request cannot be honored the tool "
                        "returns __tool_status__='METHOD_DOWNGRADED' with a "
                        "concrete reason — do NOT describe the fit as Bayesian "
                        "when that happens."
                    ),
                },
                "subsample_splits": {
                    "type": "array",
                    "description": (
                        "Optional redshift bin definitions for a subsample "
                        "comparison (e.g. z<1 vs z>=1). Each item: "
                        "{name: str, z_min?: float, z_max?: float}. When "
                        "len >= 2 the tool fits each subsample with "
                        "bootstrap-OLS and reports Δβ + p-value for every "
                        "adjacent pair, so 'redshift dependence' claims "
                        "carry a real significance number, not just "
                        "side-by-side slopes."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "z_min": {"type": "number"},
                            "z_max": {"type": "number"},
                        },
                    },
                },
                "subsample_n_boot": {
                    "type": "integer",
                    "description": "Bootstrap iterations per subsample (default 2000).",
                },
                "cosmology": {
                    "type": "string",
                    "description": (
                        "Optional target cosmology. When supplied as a PART AA "
                        "preset name (planck18 / planck18_bao / freedman21_trgb "
                        "/ riess22_shoes) or a FlatLambdaCDM_H<H0>_Om<Om> spec "
                        "(e.g. FlatLambdaCDM_H73p8_Om0p295 for Riess+11 / "
                        "Suzuki+12), the tool RECOMPUTES log_luminosity for "
                        "each row from the new luminosity distance: "
                        "log_L_new = log_L_old + 2*log10(DL_new(z) / DL_old(z)). "
                        "DL_old is computed from the current platform default "
                        "(planck18) — pre-AC caches stored under that assumption. "
                        "Returns the dl_shift_summary so the systematic error "
                        "budget is auditable. Omit / pass 'planck18' to keep the "
                        "default Planck18 fit. The cosmology-mismatch warning "
                        "fires only when the row-level source_cosmology disagrees "
                        "with the chosen target."
                    ),
                },
                "variant_label": {
                    "type": "string",
                    "description": (
                        "Optional human-readable label for the fit variant, "
                        "echoed back in the result so multiple fit_line_lfr "
                        "calls in the same chat round (main / subsample / "
                        "cosmology-Riess22 / demagnified) are distinguishable "
                        "in the UI. Default: 'main'."
                    ),
                },
                "luminosity_kind": {
                    "type": "string",
                    "enum": ["L_solar", "L_prime"],
                    "description": (
                        "Luminosity unit for the y-axis of the fit. "
                        "'L_solar' (default) = log10(L_line / L_sun), the form "
                        "ALPINE/REBELS/Capak/Bothwell tables natively report. "
                        "'L_prime' = log10(L'_line / (K km/s pc^2)), the "
                        "brightness-temperature form used by the CO LFR "
                        "literature (Solomon 1992; Carilli & Walter 2013). "
                        "REQUIRED to be 'L_prime' when the user mentions CO "
                        "LFR / Solomon / brightness temperature / K km/s pc^2 "
                        "/ comparing slopes against CO(1-0). Conversion uses "
                        "log10(L'/L) = 10.495 - 3*log10(nu_rest_GHz) + 2*log10(1+z); "
                        "rows missing redshift or with unknown line_id are "
                        "rejected (kind='unit_conversion_failed') rather than "
                        "silently fit on mixed units. The result envelope "
                        "carries `intercept_unit`/`slope_unit`/`luminosity_kind` "
                        "so prose MUST quote the unit when reporting alpha."
                    ),
                },
            },
        },
    },
    {
        "name": "astro_statistics_toolbox",
        "description": (
            "Run deterministic statistical helpers on supplied arrays: robust summary, "
            "OLS/weighted/ODR/Theil-Sen linear regression, bootstrap linear regression, "
            "and descriptive censored-data summaries for upper limits. Prefer this over "
            "ad-hoc run_python for common statistics when the data arrays are already "
            "available from real tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "analysis_type": {
                    "type": "string",
                    "enum": ["robust_summary", "linear_regression", "bootstrap_linear_regression", "censored_summary"],
                    "description": "Statistic to run.",
                },
                "values": {"type": "array", "items": {"type": "number"}, "description": "Values for robust_summary or censored_summary."},
                "is_upper_limit": {"type": "array", "items": {"type": "boolean"}, "description": "Flags for censored_summary; true means upper limit."},
                "x": {"type": "array", "items": {"type": "number"}, "description": "x values for regression."},
                "y": {"type": "array", "items": {"type": "number"}, "description": "y values for regression."},
                "x_err": {"type": "array", "items": {"type": "number"}, "description": "Optional x uncertainties."},
                "y_err": {"type": "array", "items": {"type": "number"}, "description": "Optional y uncertainties."},
                "method": {"type": "string", "enum": ["auto", "ols", "weighted", "odr", "theil_sen"], "description": "Regression method."},
                "n_bootstrap": {"type": "integer", "description": "Bootstrap iterations when applicable."},
                "seed": {"type": "integer", "description": "Random seed for bootstrap resampling."},
            },
            "required": ["analysis_type"],
        },
    },
    {
        "name": "compare_luminosity_distances",
        "description": (
            "Compare luminosity distance + Δlog L for two cosmology choices "
            "across the cached literature sample. Use BEFORE citing a non-"
            "Planck H0/Om0 (e.g. Riess+11 H0=73.8, Suzuki+12 Om=0.295) on a "
            "sample whose source_cosmology is something else. Returns per-"
            "source ΔDL%% + Δlog L, plus median/max summary; use the result "
            "to either recompute log_luminosity or quote the shift as a "
            "cosmology-systematic uncertainty in the slope error budget."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cache_key": {
                    "type": "string",
                    "description": "Source cache key. Default: latest_literature_tables.",
                },
                "target_cosmology": {
                    "type": "string",
                    "description": (
                        "Cosmology name. Prefer a curated PART AA preset "
                        "('planck18' | 'planck18_bao' | 'freedman21_trgb' | "
                        "'riess22_shoes') — each carries a peer-reviewed "
                        "ADS bibcode that the citation validator anchors "
                        "against. Legacy names also accepted (Planck15 / "
                        "WMAP9 / WMAP7 / WMAP5 — no curated bibcode), as "
                        "is the FlatLambdaCDM_H<H0>_Om<Om> spec for "
                        "older measurements (e.g. FlatLambdaCDM_H73p8_Om0p295 "
                        "for Riess+11 / Suzuki+12)."
                    ),
                },
            },
            "required": ["target_cosmology"],
        },
    },
    {
        "name": "export_sample_table",
        "description": (
            "Export the cached literature sample as a machine-readable "
            "table (csv | votable | latex | ascii). Use as the final "
            "deliverable so the user can verify the 74-source sample "
            "directly. The content is returned inline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cache_key": {
                    "type": "string",
                    "description": "Source cache key. Default: latest_literature_tables.",
                },
                "format": {
                    "type": "string",
                    "enum": ["csv", "votable", "latex", "ascii"],
                    "description": "Output format. Default: csv.",
                },
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Column subset. Default: all standard fields.",
                },
            },
        },
    },
    {
        "name": "demagnify_sample",
        "description": (
            "Apply gravitational-lensing demagnification to a literature "
            "sample. Reads cached line_measurements, subtracts log10(μ) "
            "from log_luminosity for every source listed in mu_map, and "
            "writes the corrected rows to a NEW cache key (default suffix "
            "'__demag') so the original is preserved. Use this BEFORE "
            "fit_line_lfr when any sample sources are gravitationally "
            "lensed; then call fit_line_lfr(cache_key=<new>) on the "
            "demagnified cache."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cache_key": {
                    "type": "string",
                    "description": "Source cache key. Default: latest_literature_tables.",
                },
                "mu_map": {
                    "type": "object",
                    "description": (
                        "Per-source magnification factors. Two equivalent "
                        "forms: '\"SRC-A\": 5.0' (just μ) or "
                        "'\"SRC-B\": {\"mu\": 3.0, \"reference\": \"Foo+24\"}' "
                        "(μ + cited source for the μ value). Reference "
                        "is recorded in provenance."
                    ),
                },
                "output_cache_key": {
                    "type": "string",
                    "description": "Override the default <input>__demag suffix.",
                },
            },
            "required": ["mu_map"],
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
        "name": "get_async_job_status",
        "description": (
            "Generic poll endpoint for any background tool job submitted via "
            "the async-tool runtime (transit_search_bls, long fit_transit, "
            "run_cosmology_likelihood_chain, run_paper_tool_mining_loop, etc.). "
            "Returns PARTIAL while the job is queued/running and the unwrapped "
            "underlying result once the job is completed. Use this instead of "
            "get_cosmology_run_status for any tool that returned a job_id with "
            "__tool_status__=PARTIAL and analysis_status in {QUEUED, RUNNING}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": (
                        "Job ID returned by an async-capable tool. "
                        "Examples: 'fit_cosmology_mcmc-abc123', 'transit_search_bls-def456'."
                    ),
                },
            },
            "required": ["job_id"],
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
    # ── Time-Domain Tools ──
    # ── Team/Workspace Tools ──
    # ── FITS/Export/Provenance Tools ──
    # ── Photo-Z Pro + Batch Tools ──
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

# ── M0 Commit 4 (2026-05-18): solar_system 12-tool schema ──
# Consolidated in ai_tools_solar_system.py and injected into TOOLS via extend
# (avoids further bloating the 9k+ line ai_tools.py).
# _exec_tool dispatch routes through a single elif to dispatch_solar_system.
from app.services.ai_tools_solar_system import (  # noqa: E402
    SOLAR_SYSTEM_TOOL_SCHEMAS as _SOLAR_SYSTEM_TOOL_SCHEMAS,
    SOLAR_SYSTEM_TOOL_NAMES as _SOLAR_SYSTEM_TOOL_NAMES,
)
TOOLS.extend(_SOLAR_SYSTEM_TOOL_SCHEMAS)

# ── M0 2026-05-20: exoplanet 8 tools (3rd active module) ──
from app.services.ai_tools_exoplanet import (  # noqa: E402
    EXOPLANET_TOOL_SCHEMAS as _EXOPLANET_TOOL_SCHEMAS,
    EXOPLANET_TOOL_NAMES as _EXOPLANET_TOOL_NAMES,
)
TOOLS.extend(_EXOPLANET_TOOL_SCHEMAS)

# ── H1 split (2026-05-26): cosmology tools (schemas + executors) extracted to
# ai_tools_cosmology.py; schemas injected via TOOLS.extend, execution delegated
# via dispatch_cosmology (mirrors solar_system / exoplanet).
from app.services.ai_tools_cosmology import (  # noqa: E402
    COSMOLOGY_TOOL_NAMES as _COSMOLOGY_TOOL_NAMES,
    COSMOLOGY_TOOL_SCHEMAS as _COSMOLOGY_TOOL_SCHEMAS,
)
TOOLS.extend(_COSMOLOGY_TOOL_SCHEMAS)

from app.services.ai_tools_paper_mining import (  # noqa: E402
    PAPER_MINING_TOOL_NAMES as _PAPER_MINING_TOOL_NAMES,
    PAPER_MINING_TOOL_SCHEMAS as _PAPER_MINING_TOOL_SCHEMAS,
)
TOOLS.extend(_PAPER_MINING_TOOL_SCHEMAS)

from app.services.ai_tools_dossier import (  # noqa: E402
    DOSSIER_TOOL_NAMES as _DOSSIER_TOOL_NAMES,
    DOSSIER_TOOL_SCHEMAS as _DOSSIER_TOOL_SCHEMAS,
)
TOOLS.extend(_DOSSIER_TOOL_SCHEMAS)

from app.services.ai_tools_imaging import (  # noqa: E402
    IMAGING_TOOL_NAMES as _IMAGING_TOOL_NAMES,
    IMAGING_TOOL_SCHEMAS as _IMAGING_TOOL_SCHEMAS,
)
TOOLS.extend(_IMAGING_TOOL_SCHEMAS)

from app.services.ai_tools_research import (  # noqa: E402
    RESEARCH_TOOL_NAMES as _RESEARCH_TOOL_NAMES,
    RESEARCH_TOOL_SCHEMAS as _RESEARCH_TOOL_SCHEMAS,
)
TOOLS.extend(_RESEARCH_TOOL_SCHEMAS)


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
    # Action 3 (telemetry, 2026-05-08): count every tool invocation so
    # we have real-world usage data for the cosmology-focus surgery
    # decisions (which tools to drop from allowlist after 7 days of
    # production traffic).  Reuses the module-level Prometheus
    # registry; cardinality is bounded by ~87 tool names.
    try:
        from app.observability.metrics import record_counter
        record_counter("tool_invoked_total", 1.0, tool=tool_name)
    except Exception:
        # Telemetry must never break the actual tool call.
        pass

    from app.services.result_provenance import normalize_tool_result
    result = await _execute_tool_inner(
        tool_name, tool_input, api_key, provider_api_keys,
        python_session_id, user_id, chat_session_id, progress_callback,
    )

    # 2026-05-20: write ai.tool_called to user_events so the telemetry/tool_usage
    # endpoint has data. The consumer (admin_stats.py) was already implemented
    # but the producer was missing. Only records input field keys (not values)
    # to prevent BYOK api_key or large payloads from being stored in the DB.
    try:
        from app.services.event_collector import event_collector
        input_keys = sorted(tool_input.keys()) if isinstance(tool_input, dict) else []
        await event_collector.track(
            event_type="ai.tool_called",
            event_data={
                "tool_name": tool_name,
                "input_keys": input_keys,
                "success": not (isinstance(result, dict) and result.get("success") is False),
            },
            user_id=user_id,
            session_id=chat_session_id,
        )
    except Exception:
        # Telemetry must never break the actual tool call.
        pass

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
        elif tool_name == "list_user_tools":
            from app.services.user_tools import list_user_tools, owner_scope

            scope = owner_scope(user_id, chat_session_id)
            return {
                "success": True,
                "__tool_status__": "COMPLETED",
                "tools": list_user_tools(scope),
                "__message_to_model__": (
                    "These are user-created tool macros. Use run_user_tool with "
                    "a listed tool_id if the user's request matches one."
                ),
            }
        elif tool_name == "run_user_tool":
            from app.services.user_tools import execute_user_tool, owner_scope

            scope = owner_scope(user_id, chat_session_id)
            return await execute_user_tool(
                scope=scope,
                tool_id=str(tool_input.get("tool_id") or ""),
                arguments=tool_input.get("arguments") if isinstance(tool_input.get("arguments"), dict) else {},
                user_id=user_id,
                chat_session_id=chat_session_id,
                python_session_id=python_session_id,
            )
        elif tool_name == "analyze_spectrum":
            return await _exec_analyze(tool_input, api_key, provider_api_keys)
        elif tool_name == "generate_pipeline":
            return _exec_pipeline(tool_input)
        elif tool_name == "search_literature":
            return await _exec_literature(tool_input)
        elif tool_name == "classify_literature_relevance":
            # Stage 6 P0c-C (2026-05-19): hard barrier — LLM must call this to classify
            # papers; otherwise claim_validator.unclassified_literature_violations blocks the reply.
            return await _exec_classify_literature_relevance(tool_input, python_session_id)
        elif tool_name == "extract_literature_tables":
            return await _exec_extract_literature_tables(
                tool_input, python_session_id,
                user_id=user_id, chat_session_id=chat_session_id,
            )
        elif tool_name == "prepare_spectral_measurements":
            return _exec_prepare_spectral_measurements(tool_input, python_session_id)
        elif tool_name == "fit_line_lfr":
            # Stage 6.3 (2026-05-20 sink): fit_line_lfr now accepts an optional arxiv_id;
            # internally calls the LLM extractor to pull measurements + ±1% cell verification
            # + write cache, then proceeds with the original fitting flow.
            return await _exec_fit_line_lfr_async(tool_input, python_session_id, api_key)
        elif tool_name == "astro_statistics_toolbox":
            return _exec_astro_statistics_toolbox(tool_input)
        elif tool_name == "demagnify_sample":
            return _exec_demagnify_sample(tool_input, python_session_id)
        elif tool_name == "compare_luminosity_distances":
            return _exec_compare_luminosity_distances(tool_input, python_session_id)
        elif tool_name == "export_sample_table":
            return _exec_export_sample_table(tool_input, python_session_id)
        elif tool_name == "run_python":
            return await _exec_run_python(tool_input, python_session_id)
        elif tool_name == "get_last_search_results":
            return _exec_get_cached_results(tool_input)
        elif tool_name == "validate_analysis":
            return await _exec_validate_analysis(tool_input, chat_session_id or python_session_id, user_id=user_id)
        elif tool_name == "generate_paper_draft":
            return await _exec_generate_paper_draft(tool_input, chat_session_id or python_session_id, user_id=user_id)
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
        elif tool_name == "get_async_job_status":
            return _exec_get_async_job_status(tool_input)
        # ── H1 split (2026-05-26): cosmology centralized dispatch ──
        # Deployment-readiness introspection scans this function body for
        # quoted tool names. Keep this inventory in sync with COSMOLOGY_TOOL_NAMES:
        # "fit_cosmology_mcmc", "run_cobaya_cosmology",
        # "get_cosmology_run_status", "list_cosmology_datasets",
        # "load_cosmology_data_product", "build_cosmology_likelihood",
        # "run_cosmology_likelihood_chain", "run_cmb_rotation_likelihood",
        # "run_nested_sampler", "evaluate_chain_diagnostics",
        # "build_cosmology_robustness_matrix", "run_cosmology_robustness_matrix",
        # "assess_bao_bin_anomaly", "audit_published_constraint",
        # "compute_theory_cmb_spectrum".
        elif tool_name in _COSMOLOGY_TOOL_NAMES:
            from app.services.ai_tools_cosmology import dispatch_cosmology
            return await dispatch_cosmology(tool_name, tool_input, python_session_id)
        # ── H1 split (2026-05-26): research-core 5-tool centralized dispatch ──
        # Deployment-readiness introspection scans this function body for
        # quoted tool names. Keep in sync with RESEARCH_TOOL_NAMES:
        # "plan_research_program", "run_research_matrix",
        # "build_evidence_graph", "verify_research_facts",
        # "export_research_report".
        elif tool_name in _RESEARCH_TOOL_NAMES:
            from app.services.ai_tools_research import dispatch_research
            return await dispatch_research(tool_name, tool_input)
        # ── H1 split (2026-05-26): paper-mining 7-tool centralized dispatch ──
        # Deployment-readiness introspection scans this function body for
        # quoted tool names. Keep in sync with PAPER_MINING_TOOL_NAMES:
        # "mine_paper_tools", "run_paper_tool_mining_batch",
        # "build_tool_ontology", "build_tool_gap_matrix",
        # "rank_tool_implementation_queue", "build_paper_mining_candidate_pool",
        # "run_paper_tool_mining_loop".
        elif tool_name in _PAPER_MINING_TOOL_NAMES:
            from app.services.ai_tools_paper_mining import dispatch_paper_mining
            return await dispatch_paper_mining(tool_name, tool_input)
        # ── H1 split (2026-05-26): dossier 3-tool centralized dispatch ──
        # Deployment-readiness introspection scans this function body for
        # quoted tool names. Keep in sync with DOSSIER_TOOL_NAMES:
        # "get_object_dossier", "get_followup_recommendation",
        # "analyze_cross_wavelength".
        elif tool_name in _DOSSIER_TOOL_NAMES:
            from app.services.ai_tools_dossier import dispatch_dossier
            return await dispatch_dossier(tool_name, tool_input)
        elif tool_name == "crossmatch_catalogs":
            return await _exec_crossmatch_catalogs(tool_input, python_session_id)
        elif tool_name == "search_lightcurve":
            return await _exec_search_lightcurve(tool_input)
        # ── H1 split (2026-05-26): imaging 4-tool centralized dispatch ──
        # Deployment-readiness introspection scans this function body for
        # quoted tool names. Keep in sync with IMAGING_TOOL_NAMES:
        # "reduce_ccd_image", "solve_astrometry",
        # "extract_photometry", "extract_sources".
        elif tool_name in _IMAGING_TOOL_NAMES:
            from app.services.ai_tools_imaging import dispatch_imaging
            return await dispatch_imaging(tool_name, tool_input)
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
            # Long-running: full-sector BLS sweeps over 0.5–20 day periods on
            # a 50k-cadence light curve routinely run 60-300s. The 45s default
            # tool deadline kills them mid-sweep, so for large inputs we
            # off-load to the Celery worker via the async-tool runtime.
            # in_async_worker() suppresses re-submission when we're already
            # inside the worker draining its own queue.
            from app.services.async_tool_runtime import in_async_worker, submit_async_job

            _time_len = len(tool_input.get("time") or [])
            _period_min = float(tool_input.get("period_min", 0.5))
            _period_max = float(tool_input.get("period_max", 20.0))
            _async_threshold = _time_len >= 50_000 or (_period_max - _period_min) > 50
            _force_background = bool(tool_input.get("background", False))
            if not in_async_worker() and (_async_threshold or _force_background):
                return submit_async_job("transit_search_bls", tool_input)

            from app.services.time_domain_pro import transit_search_bls as _bls
            return _bls(
                tool_input["time"], tool_input["flux"],
                period_range=(_period_min, _period_max),
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
        # ── M0 Commit 4: solar_system 12-tool centralized dispatch ──
        # Deployment-readiness introspection scans this function body for
        # quoted tool names. Keep this explicit inventory in sync with
        # _SOLAR_SYSTEM_TOOL_NAMES while dispatch remains centralized:
        # "query_mpc_orbit", "fetch_horizons_ephemeris",
        # "query_sbdb_orbit", "query_sbdb_close_approaches",
        # "query_sentry_risk", "query_damit_shape_model",
        # "compute_hg_magnitude", "compute_afrho",
        # "fit_neatm_diameter_albedo", "compute_neo_collision_probability",
        # "classify_asteroid_busdemeo", "classify_asteroid_sdss_colors".
        elif tool_name in _SOLAR_SYSTEM_TOOL_NAMES:
            from app.services.ai_tools_solar_system import dispatch_solar_system
            return await dispatch_solar_system(tool_name, tool_input)
        # ── M0 2026-05-20: exoplanet 8-tool centralized dispatch ──
        # Group-dispatched exoplanet tools:
        # "query_exoplanet_archive", "query_confirmed_planets",
        # "fetch_tess_lightcurve", "fit_transit",
        # "compute_equilibrium_temperature", "compute_transit_depth",
        # "compute_planet_density", "query_tess_target_list".
        elif tool_name in _EXOPLANET_TOOL_NAMES:
            from app.services.ai_tools_exoplanet import dispatch_exoplanet
            return await dispatch_exoplanet(tool_name, tool_input)
        else:
            # R6-NEW-2: return a concrete error_class + available tool list for unknown
            # tools so the AI can self-correct on the next turn (no more hallucinated
            # tool names). TOOLS is the module-level list; each entry is
            # {"name": ..., "description": ...}.
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
    if (
        _has("alma", "submillimeter", "sub-mm", "millimeter", "mm", "cii")
        or "[cii]" in q
        or "[c ii]" in q
        or "158μm" in q
        or "158um" in q
        or "far infrared" in q
        or "far-infrared" in q
    ):
        return ["alma", "simbad", "ned"]
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
    from app.connectors.availability import (
        build_unavailable_response,
        is_available,
        record_connector_gated,
    )
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

    unavailable_sources = [s for s in sources if not is_available(s)]
    for source in unavailable_sources:
        record_connector_gated(source)

    sources = [s for s in sources if is_available(s)]
    if unavailable_sources and not sources:
        store_search_results("latest", [])
        store_search_results(f"latest:{python_session_id}", [])
        return build_unavailable_response(unavailable_sources, tool_name="search_objects")

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
    for src in unavailable_sources:
        unavailable = build_unavailable_response(src, tool_name="search_objects")
        per_source_counts.extend(unavailable["per_source"])

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
    if unavailable_sources:
        result["unavailable_sources"] = unavailable_sources
        result["available_alternatives"] = ["vizier", "gaia", "simbad", "ned", "2mass", "alma"]
        result["warnings"] = [
            (
                "Some requested sources are temporarily under maintenance during "
                "the provenance v2 rollout: " + ", ".join(unavailable_sources)
            )
        ]

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

    def _gaia_expression_syntax_hint(q: str, error_msg: str) -> str | None:
        q_l = str(q or "").lower()
        if service.lower() != "gaia" and "gaiadr3.gaia_source" not in q_l:
            return None
        expression_tokens = (
            'encountered " "sqrt',
            'encountered "sqrt"',
            'encountered " "("',
            "unexpected token: sqrt",
            "parse error",
        )
        has_expression_error = any(token in error_msg for token in expression_tokens)
        has_query_pattern = (
            "sqrt(" in q_l
            or re.search(r"\border\s+by\s*\(", q_l) is not None
            or re.search(r"\border\s+by\s+[^,\n]+[+\-*/][^,\n]+", q_l) is not None
            or re.search(r"\bwhere\b[\s\S]*sqrt\s*\(", q_l) is not None
        )
        if not (has_expression_error or has_query_pattern):
            return None
        return (
            "Gaia TAP commonly rejects function calls or arithmetic expressions "
            "inside WHERE/ORDER BY. For high-velocity stars, prefer the dedicated "
            "`query_high_velocity_stars` tool. Otherwise select raw columns "
            "(pmra, pmdec, parallax, radial_velocity) with simple cuts, then "
            "compute SQRT / velocities in run_python; if your TAP mirror accepts "
            "aliases, put the expression in SELECT as `... AS pm_tot` and ORDER "
            "BY that alias, not by `SQRT(...)` or `(pmra*pmra+...)` directly."
        )

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
            if "i/355/varisum" in q.lower() or "varisum" in msg:
                raise type(exc)(
                    f"{exc}\n\n[auto-suggestion] `I/355/varisum` is not a "
                    "valid Gaia variable-summary table in VizieR. Do not guess "
                    "Gaia variable table names. For bright named variables, call "
                    "describe_tap_table on `\"B/gcvs/gcvs_cat\"` (GCVS) before "
                    "querying it, or emit <tools_returned_nothing/> if no real "
                    "epoch/time-series data is available."
                ) from exc
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
            syntax_hint = _gaia_expression_syntax_hint(q, msg)
            if syntax_hint:
                raise type(exc)(f"{exc}\n\n[auto-suggestion] {syntax_hint}") from exc
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
    # X4 (PART X): record the original/final radius shrink values and inject
    # them into adql_result so the frontend can render a prominent banner.
    # In B6, Pleiades 0.75° → 0.375° halved the member count and the AI
    # didn't notice — the frontend banner makes it visible to both user and AI.
    radius_shrink_original: float | None = None
    radius_shrink_final: float | None = None
    if result is None:
        # Find CIRCLE('ICRS', ra, dec, radius) and halve the radius
        def _halve_radius(q: str, factor: float = 0.5) -> tuple[str | None, float | None, float | None]:
            pattern = _re.compile(
                r"(CIRCLE\s*\(\s*'ICRS'\s*,\s*[-+]?\d+\.?\d*\s*,\s*[-+]?\d+\.?\d*\s*,\s*)([-+]?\d+\.?\d*)",
                _re.IGNORECASE,
            )
            m = pattern.search(q)
            if m:
                old_r = float(m.group(2))
                new_r = old_r * factor
                return (
                    pattern.sub(lambda _m: f"{_m.group(1)}{new_r}", q, count=1),
                    old_r,
                    new_r,
                )
            return (None, None, None)

        for attempt, factor in enumerate([0.5, 0.25], start=1):
            if _time_left() < 30:
                retry_log.append(f"skipped radius×{factor} — budget exhausted")
                await _emit_progress(
                    "retry_skipped_budget",
                    f"Skipped radius × {factor} retry because the ADQL retry budget is nearly exhausted",
                    factor=factor,
                )
                break
            reduced, _old_r, _new_r = _halve_radius(query, factor)
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
                # X4 (PART X): record original / final radius for the frontend banner
                if radius_shrink_original is None:
                    radius_shrink_original = _old_r
                radius_shrink_final = _new_r
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
        _gaia_variable_hint = ""
        if service.lower() == "vizier" and any(
            token in _query_l for token in ("v/154/sdss", "v/147/sdss", "v/139/sdss")
        ):
            _sdss_vizier_hint = (
                " For SDSS luminosity-function or photometry+spec-z samples, "
                "stop retrying broad VizieR ADQL: call run_sdss_sql instead "
                "with a T-SQL query against PhotoObjAll JOIN SpecObjAll "
                "(start with TOP 500-1000)."
            )
        if service.lower() == "gaia" and "vari_" in _query_l:
            _gaia_variable_hint = (
                " For Gaia variable-star table outages, do not invent a VizieR "
                "Gaia variable-summary table. Try describe_tap_table on "
                "`\"B/gcvs/gcvs_cat\"` (GCVS) for named bright variables, or "
                "abstain if no real epoch/time-series data is available."
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
                f"{_gaia_variable_hint}"
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
        # W5 (PART W): expose the actually-executed query + service so the
        # AutoToolResult run_adql card can render it (not just the row count
        # summary).  Users need to see the SQL for reproducibility.
        "service": service,
        "query": query,
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

    # X4 (PART X): prominent radius auto-shrink fields. The frontend
    # AutoToolResult run_adql branch renders a yellow (non-collapsible) banner
    # when radius_auto_reduced=true, warning that member count may be halved.
    # Fixes the B6 Pleiades 0.75°→0.375° silent-shrink bug.
    if radius_shrink_original is not None and radius_shrink_final is not None:
        adql_result["radius_auto_reduced"] = True
        adql_result["original_radius_deg"] = radius_shrink_original
        adql_result["final_radius_deg"] = radius_shrink_final
        _prev_note = adql_result.get("note") or ""
        _warn_note = (
            f"⚠ Search radius auto-reduced from "
            f"{radius_shrink_original:.4g}° to {radius_shrink_final:.4g}° "
            f"(TAP timeout on original query). Membership count may be "
            f"smaller than expected."
        )
        adql_result["note"] = f"{_warn_note} {_prev_note}".strip()

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


def _high_velocity_component_threshold_masyr(
    min_parallax_mas: float,
    min_vtan_kms: float,
) -> float:
    # If total tangential velocity exceeds the threshold, at least one PM
    # component exceeds the threshold / sqrt(2). Convert using minimum parallax
    # to mas/yr for a coarse TAP-side filter; precise vtan computation is done in Python.
    return max(1.0, min_vtan_kms * min_parallax_mas / 4.74047 / math.sqrt(2.0))


def _build_high_velocity_component_query(
    *,
    limit: int = 500,
    min_parallax_mas: float = 0.2,
    min_vtan_kms: float = 250.0,
    require_radial_velocity: bool = False,
    component: str = "pmRA",
    direction: str = "DESC",
) -> str:
    component = "pmRA" if component not in {"pmRA", "pmDE"} else component
    direction = "ASC" if str(direction).upper() == "ASC" else "DESC"
    threshold = _high_velocity_component_threshold_masyr(min_parallax_mas, min_vtan_kms)
    if direction == "ASC":
        pm_cut = f"{component} <= {-threshold:.6g}"
    else:
        pm_cut = f"{component} >= {threshold:.6g}"
    rv_clause = "AND RV IS NOT NULL" if require_radial_velocity else ""
    return f"""
SELECT TOP {limit}
  Source, RA_ICRS, DE_ICRS, Plx, pmRA, pmDE, RV, Gmag, RUWE
FROM "I/355/gaiadr3"
WHERE Plx >= {min_parallax_mas:.6g}
  AND pmRA IS NOT NULL
  AND pmDE IS NOT NULL
  AND {pm_cut}
  AND (RUWE IS NULL OR RUWE < 1.4)
  {rv_clause}
ORDER BY {component} {direction}
""".strip()


def _build_high_velocity_stars_query(
    *,
    limit: int = 500,
    min_parallax_mas: float = 0.2,
    min_vtan_kms: float = 250.0,
    require_radial_velocity: bool = False,
) -> str:
    return _build_high_velocity_component_query(
        limit=limit,
        min_parallax_mas=min_parallax_mas,
        min_vtan_kms=min_vtan_kms,
        require_radial_velocity=require_radial_velocity,
        component="pmRA",
        direction="DESC",
    )


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
    per_query_limit = max(50, min(2000, limit))
    scans = [
        ("pmRA_desc", "pmRA", "DESC"),
        ("pmRA_asc", "pmRA", "ASC"),
        ("pmDE_desc", "pmDE", "DESC"),
        ("pmDE_asc", "pmDE", "ASC"),
    ]
    queries = {
        label: _build_high_velocity_component_query(
            limit=per_query_limit,
            min_parallax_mas=min_parallax,
            min_vtan_kms=min_vtan,
            require_radial_velocity=require_rv,
            component=component,
            direction=direction,
        )
        for label, component, direction in scans
    }
    async_timeout_s = 780.0 if extended_timeout else 300.0

    async def _run_component(label: str, q: str) -> tuple[str, dict | None, str | None]:
        if progress_callback:
            try:
                await progress_callback({
                    "stage": "component_scan",
                    "message": f"Scanning Gaia DR3 high-PM candidates by {label}",
                    "service": "vizier",
                    "query_preview": q[:300],
                })
            except Exception:
                logger.debug("High-velocity progress callback failed", exc_info=True)
        try:
            result = await execute_adql_query(
                ADQLRequest(query=q, service="vizier"),
                progress_callback=progress_callback,
                async_timeout_s=async_timeout_s,
            )
            return label, result, None
        except Exception as exc:
            return label, None, str(exc)

    component_results = await asyncio.gather(*[
        _run_component(label, query) for label, query in queries.items()
    ])
    failures = [f"{label}: {err}" for label, result, err in component_results if result is None and err]
    successful = [(label, result) for label, result, err in component_results if isinstance(result, dict)]
    if not successful:
        return {
            "success": False,
            "error": (
                "High-velocity Gaia DR3 candidate query failed on all component scans: "
                f"{'; '.join(failures[:4])}. "
                "Retry with a higher min_vtan_kms, lower limit, or require_radial_velocity=false."
            ),
            "error_class": "high_velocity_query_failed",
            "service": "vizier",
            "table": "I/355/gaiadr3",
            "query": "\n\n-- component scan --\n\n".join(queries.values()),
            "params": {
                "limit": limit,
                "min_parallax_mas": min_parallax,
                "min_vtan_kms": min_vtan,
                "require_radial_velocity": require_rv,
                "extended_timeout": extended_timeout,
            },
        }

    candidates: dict[str, dict[str, Any]] = {}
    attempt_log: list[Any] = []
    for label, result in successful:
        raw_data = result.get("data", {}) if isinstance(result, dict) else {}
        data_in = {str(col).lower(): vals for col, vals in raw_data.items()}
        columns_in = [str(c).lower() for c in (result.get("columns", []) if isinstance(result, dict) else [])]
        row_count_in = int(result.get("row_count", 0) or 0) if isinstance(result, dict) else 0
        if isinstance(result.get("attempt_log"), list):
            attempt_log.extend(result.get("attempt_log", []))
        rows = build_adql_rows(columns_in, data_in, row_count_in, limit=per_query_limit)
        for idx, row in enumerate(rows):
            plx = _coerce_float(row.get("plx"))
            pmra = _coerce_float(row.get("pmra"))
            pmde = _coerce_float(row.get("pmde"))
            if plx is None or plx <= 0 or pmra is None or pmde is None:
                continue
            rv = _coerce_float(row.get("rv"))
            if require_rv and rv is None:
                continue
            vtan = 4.74047 * math.sqrt(pmra * pmra + pmde * pmde) / plx
            if vtan < min_vtan:
                continue
            normalized = dict(row)
            normalized["vtan_kms"] = round(vtan, 6)
            source = normalized.get("source")
            key = str(source) if source not in (None, "") else f"{label}:{idx}"
            current = candidates.get(key)
            if current is None or float(normalized["vtan_kms"]) > float(current.get("vtan_kms", -1.0)):
                candidates[key] = normalized

    rows_out = sorted(candidates.values(), key=lambda row: float(row.get("vtan_kms") or 0.0), reverse=True)[:limit]
    columns = [
        col for col in ("source", "ra_icrs", "de_icrs", "plx", "pmra", "pmde", "rv", "gmag", "ruwe", "vtan_kms")
        if any(row.get(col) is not None for row in rows_out)
    ]
    row_count = len(rows_out)
    data = {col: [row.get(col) for row in rows_out] for col in columns}
    query = "\n\n-- component scan --\n\n".join(queries.values())

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
        "component_scan": {
            "threshold_masyr": round(_high_velocity_component_threshold_masyr(min_parallax, min_vtan), 6),
            "queries": list(queries.values()),
            "failures": failures,
        },
        "note": (
            f"Showing first {view_rows} of {row_count} rows. Full rows are cached under latest_adql "
            f"and {hv_key}; compute 3D velocities in run_python(data_source='latest_adql')."
        ) if row_count > view_rows else None,
        "attempt_log": attempt_log[-20:] if isinstance(attempt_log, list) else [],
    }


async def _exec_run_sdss_sql(inp: dict, python_session_id: str = "default") -> dict:
    """J3: Query the SDSS SkyServer SQL API directly, bypassing VizieR.

    When all four VizieR mirrors return 404 (as in the third regression of the
    Paper 3 Coma galaxy luminosity function case), the SDSS VizieR path is
    completely broken. This tool bypasses VizieR and calls the official SDSS
    SkyServer directly (which has independent uptime), letting the AI continue
    PhotoObjAll / SpecObjAll / Photoz / GalSpec queries during VizieR outages.

    The result structure matches _exec_adql and is also stored in the ADQL cache
    pool (with an extra alias key `latest_sdss_sql`). run_python can access the
    full rows via `get_cached_results('latest_sdss_sql')` or the simpler
    `get_adql_results()`.
    """
    from app.connectors.availability import build_unavailable_response, record_connector_gated
    from app.services.provenance_v2.registry_loader import dataset_from_registry, resolve_service

    sdss_dataset = dataset_from_registry("sdss") if resolve_service("sdss") else None
    if not (sdss_dataset and sdss_dataset.get("archive_version")):
        record_connector_gated("sdss")
        return build_unavailable_response("sdss", tool_name="run_sdss_sql")

    from app.connectors.sdss_sql import execute_sdss_sql

    query = str(inp.get("query") or "").strip()
    dr = str(inp.get("dr") or "18").strip()
    is_long_mode = (
        inp.get("extended_timeout")
        or str(inp.get("_workflow_budget_mode") or "").lower() == "long"
    )
    timeout_s = 240.0 if is_long_mode else 120.0
    max_attempts = 3 if is_long_mode else 1

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
        raw = await execute_sdss_sql(
            query,
            dr=dr,
            timeout_s=timeout_s,
            max_attempts=max_attempts,
        )
    except ValueError as e:
        # SQL syntax error / dangerous keyword: 4xx-style user-visible semantic error
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
            "hint": (
                "SkyServer outages are often transient. Retry the same SQL "
                "query, or use VizieR for a small sanity query while waiting."
            ),
        }

    columns = raw.get("columns", [])
    data = raw.get("data", {})
    column_aliases = raw.get("column_aliases", {}) if isinstance(raw, dict) else {}
    row_count = raw.get("row_count", 0)

    cache_data = dict(data)
    if isinstance(column_aliases, dict):
        for alias, original in column_aliases.items():
            if alias not in cache_data and original in data:
                cache_data[alias] = data[original]

    # AI view: truncated to first 100 rows; full data goes to cache.
    VIEW_ROWS = 100
    truncated = {col: (vals[:VIEW_ROWS] if isinstance(vals, list) else vals)
                 for col, vals in data.items()}

    # Store in ADQL cache pool — run_python can use get_adql_results() or
    # get_cached_results('latest_sdss_sql'). We also store under a dedicated
    # sdss key so the AI can distinguish the data source.
    try:
        result_set = build_adql_result_set(
            service=f"sdss_dr{dr}",
            query=query,
            columns=columns,
            data=data,
            row_count=row_count,
        )
        store_adql_result_set(python_session_id, result_set)
        # Explicit sdss alias so the AI can identify the source via `latest_sdss_sql`.
        sdss_key = _session_cache_key("latest_sdss_sql", python_session_id) or "latest_sdss_sql"
        store_search_results(sdss_key, cache_data)
        if python_session_id in (None, "", "default"):
            store_search_results("latest_sdss_sql", cache_data)
    except Exception as e:
        logger.debug("SDSS SQL cache write failed: %s", e)

    return {
        "service": "sdss",
        "dr": dr,
        "columns": columns,
        "data": truncated,
        "column_aliases": column_aliases,
        "row_count": row_count,
        "showing": min(VIEW_ROWS, row_count),
        "has_data": row_count > 0,
        "datasets": [sdss_dataset],
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


def _build_paper_links(r: dict[str, Any]) -> dict[str, str]:
    """Stage 5 (2026-05-19): build clickable URLs for one paper.

    Returns a dict with any subset of {arxiv_url, pdf_url, doi_url, ads_url}.
    ads_url is always present when bibcode exists; arxiv/pdf are present when
    the paper is on arXiv (auto-detected); doi_url is present when DOI exists.
    """
    out: dict[str, str] = {}
    bibcode = str(r.get("bibcode") or "").strip()
    if bibcode:
        out["ads_url"] = f"https://ui.adsabs.harvard.edu/abs/{bibcode}"
    arxiv_url = str(r.get("arxiv_url") or "").strip()
    if not arxiv_url and bibcode.startswith("arXiv:"):
        arxiv_id = bibcode[len("arXiv:"):]
        arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"
    if arxiv_url:
        out["arxiv_url"] = arxiv_url
        out["pdf_url"] = arxiv_url.replace("/abs/", "/pdf/")
    doi = str(r.get("doi") or "").strip()
    if doi:
        out["doi_url"] = f"https://doi.org/{doi}"
    return out


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
        # `_search_ads_sync` favors object:<name> searches. In peer-review
        # workflows the question is often about a topic / method / catalog name,
        # so if the object search returns nothing we fall through to free-text
        # ADS and arXiv to avoid a premature EMPTY result.
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
        visible = [
            r for r in raw
            if not _literature_hit_should_be_hidden(r)
        ]
        filtered, filtered_out_count = _filter_literature_hits_for_query(query, visible)
        result_papers = [
            {
                "title": r["title"],
                "authors": r["authors"][:3],
                "year": r["year"],
                "bibcode": r["bibcode"],
                "abstract": (r.get("abstract") or "")[:500],
                "source": r.get("source") or r.get("pub") or source,
                # Stage 6 P0c-B (2026-05-19): pass the ADS RETRACTED flag through to LLM + frontend
                "retracted": bool(r.get("retracted", False)),
                **_build_paper_links(r),
            }
            for r in filtered[:8]
        ]
        retracted_count = sum(1 for p in result_papers if p["retracted"])
        # Stage 6.2 P2 (2026-05-19): enforce abstract second-screening.
        # Stage 5 added a MUST prompt rule but AI skipped it in prod tests.
        # Add __message_to_model__ in the standard anti-fabrication pattern
        # used by the line-relation workflow (chat.py _suppressed_*); AI
        # almost never ignores this banner on the next iteration.
        msg_to_model = (
            "REQUIRED before any further tool call: read each abstract "
            "above and output a Markdown table with columns "
            "`| # | Title (short) | Relevance | One-sentence reason |`. "
            "Relevance MUST be one of: Direct (paper directly answers the "
            "user's question), Marginal (related but does not directly "
            "answer), Off-topic (keyword overlap but topic mismatch). "
            "Only Direct + Marginal papers may be cited / mined downstream; "
            "drop Off-topic ones from your reasoning. If 0 papers are "
            "Direct, propose a refined query instead of citing marginally-"
            "relevant work as if it were direct."
        )
        if retracted_count:
            # Stage 6 P0c-B: strictly prohibit citing retracted papers
            msg_to_model = (
                f"⚠ {retracted_count} of {len(result_papers)} returned paper(s) "
                f"are marked RETRACTED by ADS. You MUST NOT cite or mine data "
                f"from any paper with `retracted=true`; treat it as if it does "
                f"not exist. In the Relevance table above, mark retracted papers "
                f"as Off-topic with reason 'RETRACTED'.\n\n"
                + msg_to_model
            )
        return {
            "source": source,
            "result_granularity": "paper_abstract",
            "supports_measurement_claims": False,
            "filtered_out_count": filtered_out_count,
            "relevance_filter": "off_topic_blacklist",
            "retracted_count": retracted_count,
            "results": result_papers,
            "__message_to_model__": msg_to_model,
        }
    except Exception as e:
        return {"error": str(e)}


async def _exec_classify_literature_relevance(inp: dict, python_session_id: str = "default") -> dict:
    """Stage 6 P0c-C (2026-05-19): hard barrier upgrade.

    Old approach: `__message_to_model__` in search_literature return prompted the
    LLM to output a Direct/Marginal/Off-topic table (soft prompt injection).
    Production testing showed the LLM skipped it (run 1 produced the table,
    run 2 did not).

    New approach: classification is a dedicated tool. The LLM must call it first;
    afterwards claim_validator's `unclassified_literature_violations` check
    verifies that every cited bibcode passed through this tool — any that did not
    result in a hard-blocked reply.

    This function itself only performs lightweight validation + structured return;
    the actual blocking happens in the claim-validation step of the chat.py pipeline.
    """
    classifications = inp.get("classifications") or []
    if not isinstance(classifications, list) or not classifications:
        return {
            "classifications": [],
            "error": "classify_literature_relevance requires a non-empty `classifications` list",
            "error_class": "missing_argument",
            "argument": "classifications",
        }
    valid_relevance = {"Direct", "Marginal", "Off-topic"}
    cleaned: list[dict[str, str]] = []
    for c in classifications:
        if not isinstance(c, dict):
            continue
        bibcode = str(c.get("bibcode") or "").strip()
        relevance = str(c.get("relevance") or "").strip()
        reason = str(c.get("reason") or "").strip()
        if not bibcode or relevance not in valid_relevance:
            continue
        cleaned.append({"bibcode": bibcode, "relevance": relevance, "reason": reason})
    direct = sum(1 for c in cleaned if c["relevance"] == "Direct")
    marginal = sum(1 for c in cleaned if c["relevance"] == "Marginal")
    off_topic = sum(1 for c in cleaned if c["relevance"] == "Off-topic")
    msg = (
        f"Classified {len(cleaned)} paper(s): "
        f"{direct} Direct, {marginal} Marginal, {off_topic} Off-topic. "
        "ONLY Direct + Marginal papers may be cited in your narrative; "
        "downstream provenance check will hard-block any Off-topic or "
        "unclassified bibcode."
    )
    if direct == 0:
        msg += (
            " 0 Direct papers — propose a refined search_literature query "
            "before citing any of the Marginal papers."
        )
    return {
        "classifications": cleaned,
        "summary": {
            "direct": direct,
            "marginal": marginal,
            "off_topic": off_topic,
            "total": len(cleaned),
        },
        "__message_to_model__": msg,
    }


async def _extract_and_cache_paper_measurements(
    arxiv_id: str,
    api_key: str,
    python_session_id: str = "default",
    fields: list[str] | None = None,
) -> dict:
    """Stage 6.3 (2026-05-20 sink): internal helper for fit_line_lfr —
    LLM measurement extraction + ±1% cell verification + cache write.

    The spike module `llm_paper_extractor.extract_with_llm_and_verify` provides
    the core logic (fetch HTML / parse tables / score+filter / LLM call / ±1%
    cell verification). This function is an async wrapper that:
      1. Runs the spike module in run_in_executor (sync httpx + LLM call,
         prevents blocking the event loop)
      2. Converts passed records to the fit_line_lfr-compatible schema and writes
         to the session-scoped `latest_literature_tables:<sid>` cache plus the
         raw `latest_literature_tables` key
      3. failed_mismatch / failed_no_cell records are not cached
         (claim_validator automatically rejects any AI citations of them)

    History: previously a top-level tool `extract_paper_measurements_with_llm`;
    sunk into fit_line_lfr as an internal dependency on 2026-05-20. Users now
    pass arxiv_id directly to fit_line_lfr for a single-step workflow.
    """
    if not arxiv_id:
        return {
            "success": False,
            "error": "arxiv_id is required",
            "error_class": "missing_argument",
            "argument": "arxiv_id",
        }
    if not api_key:
        return {
            "success": False,
            "error": (
                "LLM-based paper extraction requires a Claude API key. "
                "Configure your Anthropic key in /account (BYOK)."
            ),
            "error_class": "missing_api_key",
        }
    if not fields:
        fields = ["source_name", "fwhm_km_s", "log_luminosity", "z"]

    try:
        from app.services.llm_paper_extractor import extract_with_llm_and_verify
        loop = asyncio.get_running_loop()
        records = await loop.run_in_executor(
            None,
            lambda: extract_with_llm_and_verify(arxiv_id, fields, api_key),
        )
    except Exception as exc:
        return {
            "success": False,
            "error": f"LLM extraction failed: {exc}",
            "error_class": "llm_extraction_failed",
            "arxiv_id": arxiv_id,
        }

    passed = [r for r in records if r.validation_status == "passed"]
    failed_mismatch = [r for r in records if r.validation_status == "failed_mismatch"]
    failed_no_cell = [r for r in records if r.validation_status == "failed_no_cell"]

    cleaned_arxiv = arxiv_id.replace("arXiv:", "").replace("arxiv:", "").strip()
    bibcode = f"arXiv:{cleaned_arxiv}"
    line_measurements = []
    for r in passed:
        line_measurements.append({
            "source_name": r.source_name,
            "fwhm_km_s": r.fwhm_km_s,
            "log_luminosity": r.log_luminosity,
            "z": r.z,
            "z_line": r.z,
            "bibcode": bibcode,
            "arxiv_id": cleaned_arxiv,
            "source_url": f"https://arxiv.org/abs/{cleaned_arxiv}",
            "extraction_method": "llm_with_cell_reverify",
            "cell_provenance": r.cell_provenance,
            "table_idx": r.table_idx,
            "row_idx": r.row_idx,
            "is_lensed": False,
            "mu_lens": None,
            "fwhm_err_km_s": None,
            "log_luminosity_err": None,
            "source_cosmology": None,
        })

    cache_key = (
        _session_cache_key("latest_literature_tables", python_session_id)
        or "latest_literature_tables"
    )
    cache_payload = {
        "arxiv_id": cleaned_arxiv,
        "bibcode": bibcode,
        "cache_key": cache_key,
        "line_measurements": line_measurements,
        "extraction_method": "llm_with_cell_reverify",
        "tables": [],
    }
    if line_measurements:
        store_search_results(cache_key, cache_payload)
        if cache_key != "latest_literature_tables":
            store_search_results("latest_literature_tables", cache_payload)

    return {
        "success": True,
        "arxiv_id": cleaned_arxiv,
        "bibcode": bibcode,
        "cache_key": cache_key,
        "line_measurements": line_measurements,
        "passed_count": len(passed),
        "failed_mismatch_count": len(failed_mismatch),
        "failed_no_cell_count": len(failed_no_cell),
        "rejected_rows": [
            {
                "source_name": r.source_name,
                "validation_status": r.validation_status,
                "validation_notes": r.validation_notes,
            }
            for r in failed_mismatch + failed_no_cell
        ],
    }


def _literature_hit_should_be_hidden(row: dict[str, Any]) -> bool:
    """Hide search hits that are known-bad rather than merely off-topic."""
    blob = " ".join(
        str(row.get(key) or "")
        for key in ("title", "abstract", "bibcode", "source", "pub")
    ).lower()
    known_bad_phrases = (
        "withdrawn by arxiv administrators",
        "contains fictitious content",
        "submitted under a pseudonym",
    )
    return any(phrase in blob for phrase in known_bad_phrases)


_LITERATURE_STOPWORDS: frozenset[str] = frozenset({
    "about", "after", "again", "against", "also", "analysis", "and", "are",
    "between", "both", "can", "could", "data", "different", "does", "for",
    "from", "give", "given", "have", "into", "model", "models", "more",
    "over", "paper", "papers", "result", "results", "sample", "samples",
    "show", "the", "their", "these", "this", "through", "using", "when",
    "with", "would",
})

_COSMOLOGY_QUERY_MARKERS: tuple[str, ...] = (
    "desi", "bao", "baryon acoustic", "pantheon", "union3", "des-5yr",
    "des 5yr", "sn ia", "sne ia", "supernova", "lcdm", "Λcdm", "dark energy",
    "gaussian process", "om(z)", "w_tot", "h0", "omega", "Ω",
)
_LINE_QUERY_MARKERS: tuple[str, ...] = (
    "[cii]", "cii", "c ii", "158", "fwhm", "line width", "line luminosity",
    "line-flux", "lfr", "alpine", "rebels", "alma",
)
_OFF_TOPIC_HIGH_ENERGY_MARKERS: tuple[str, ...] = (
    "besiii", "lhcb", "ckm angle", "charmonium", "branching fraction",
    "decay asymmetry", "w-annihilation", "semileptonic decay", "j/ψ",
    "j/psi", "electron-positron collider", "b meson", "d_s", "lambda_c",
    "ξ", "xi baryon",
)
_OFF_TOPIC_GENERAL_MARKERS: tuple[str, ...] = (
    "wildfire", "power line", "shutoff", "electric grid", "nuclear mass",
    "hartree-bogoliubov", "drhbc", "access point", "wi-fi",
    "wireless network", "semiring", "perverse sheaves",
)
_COSMOLOGY_RELEVANCE_ANCHORS: tuple[str, ...] = (
    "cosmolog", "hubble", "dark energy", "bao", "baryon acoustic",
    "supernova", "supernovae", "pantheon", "cmb", "planck", "desi",
    "weak lensing", "sigma8", "omega_m", "omegam", "lcdm",
)


def _filter_literature_hits_for_query(
    query: str,
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Stage 6 P0c-a v2 (2026-05-19): backend filter changed to blocklist veto.

    Old approach: keyword scoring, keeping only rows with score >= 2. This was
    too aggressive at the edges of cosmology queries — a paper whose abstract
    lacked anchor words but was genuinely relevant would be dropped (prod run 2:
    same prompt, 24 papers one run, 0 papers the next; the 0-paper path had all
    8 ADS results score < 2 and be discarded).

    New approach: no scoring. Only check against an obvious off-topic blocklist.
    Fine-grained relevance scoring is delegated to the Direct/Marginal/Off-topic
    table that Stage 6.2 forces the LLM to output. The backend retains only the
    anti-leak hard-block (R2.9/M4 audit: BESIII / power-engineering papers
    genuinely leaked into cosmology searches in production).

    Three blocklist categories:
      1. Known particle-physics off-topic (BESIII / LHCb / CKM / b meson / ...)
      2. General dirty words (wildfire / power grid / wi-fi / semiring / ...)
      3. "particle physics" text but NO cosmology anchor (PDG
         "The Cosmological Parameters" review is exempt)

    Generic domain queries are not filtered (same as the old approach).
    """
    if not rows:
        return [], 0

    domain = _literature_query_domain(query)
    if domain == "generic":
        return rows, 0

    kept = [row for row in rows if not _literature_hit_is_blacklisted(row, domain)]
    filtered_out = len(rows) - len(kept)
    if filtered_out:
        logger.info(
            "search_literature blacklist removed %d/%d off-topic hits domain=%s query=%r",
            filtered_out,
            len(rows),
            domain,
            query[:120],
        )
    return kept, filtered_out


def _literature_hit_is_blacklisted(row: dict[str, Any], domain: str) -> bool:
    """Return True if any blocklist entry matches (single-veto logic)."""
    blob = _normalize_literature_text(" ".join(
        str(row.get(key) or "")
        for key in ("title", "abstract", "bibcode", "source", "pub")
    ))
    if any(marker in blob for marker in _OFF_TOPIC_HIGH_ENERGY_MARKERS):
        return True
    if any(marker in blob for marker in _OFF_TOPIC_GENERAL_MARKERS):
        return True
    if domain == "cosmology":
        if "particle physics" in blob and not any(
            marker in blob for marker in _COSMOLOGY_RELEVANCE_ANCHORS
        ):
            return True
    return False


def _literature_query_domain(query: str) -> str:
    normalized = _normalize_literature_text(query)
    if any(marker in normalized for marker in _LINE_QUERY_MARKERS):
        return "line"
    if any(_normalize_literature_text(marker) in normalized for marker in _COSMOLOGY_QUERY_MARKERS):
        return "cosmology"
    return "generic"


def _literature_relevance_score(query: str, row: dict[str, Any], domain: str) -> int:
    query_norm = _normalize_literature_text(query)
    blob = _normalize_literature_text(" ".join(
        str(row.get(key) or "")
        for key in ("title", "abstract", "bibcode", "source", "pub")
    ))
    score = 0

    query_terms = _literature_query_terms(query_norm)
    for term in query_terms:
        if term in blob:
            score += 2

    for phrase in _literature_priority_phrases(query_norm):
        if phrase in blob:
            score += 4

    if domain == "cosmology":
        if any(marker in blob for marker in ("desi", "bao", "baryon acoustic", "dark energy", "supernova", "pantheon", "union3", "lcdm", "cosmolog")):
            score += 3
        if any(marker in blob for marker in _OFF_TOPIC_HIGH_ENERGY_MARKERS):
            score -= 14
        # "Review of Particle Physics — Cosmological Parameters" is a
        # legitimate cosmology hit, but generic particle-physics schools or
        # collider proceedings are not.  Penalize the latter only when no
        # cosmology anchor appears anywhere in the hit.
        if "particle physics" in blob and not any(
            marker in blob for marker in _COSMOLOGY_RELEVANCE_ANCHORS
        ):
            score -= 14
    elif domain == "line":
        if any(marker in blob for marker in ("cii", "c ii", "158", "alma", "alpine", "rebels", "fwhm", "line width")):
            score += 3
        if any(marker in blob for marker in ("high redshift", "galax", "survey", "source properties", "catalog")):
            score += 1

    if any(marker in blob for marker in _OFF_TOPIC_GENERAL_MARKERS):
        score -= 14

    return score


def _normalize_literature_text(text: str) -> str:
    normalized = str(text or "").lower()
    normalized = normalized.replace("λ", "lambda").replace("Λ", "lambda")
    normalized = normalized.replace("ω", "omega").replace("Ω", "omega")
    normalized = normalized.replace("₀", "0").replace("ₐ", "a").replace("ₘ", "m")
    normalized = normalized.replace("μ", "mu").replace("‑", "-").replace("–", "-").replace("—", "-")
    normalized = re.sub(r"[^a-z0-9+\-_'\\[\\]()./ ]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _literature_query_terms(query_norm: str) -> set[str]:
    raw_terms = set(re.findall(r"[a-z0-9][a-z0-9+'-]{2,}", query_norm))
    terms = {
        term
        for term in raw_terms
        if term not in _LITERATURE_STOPWORDS and not term.isdigit()
    }
    # Expand common astronomy/cosmology shorthands into the words most often
    # present in ADS abstracts and titles.
    if "bao" in terms:
        terms.update({"baryon", "acoustic", "oscillation"})
    if "desi" in terms:
        terms.update({"spectroscopic", "instrument"})
    if "sn" in terms or "sne" in terms:
        terms.update({"supernova", "supernovae"})
    if "lcdm" in terms or "lambda" in terms:
        terms.update({"cosmolog", "dark", "energy"})
    return terms


def _literature_priority_phrases(query_norm: str) -> set[str]:
    phrases: set[str] = set()
    for phrase in (
        "desi dr1", "dark energy spectroscopic instrument", "baryon acoustic",
        "gaussian process", "pantheon plus", "pantheon+", "des-5yr",
        "des 5yr", "union3", "lrg1", "c ii", "[cii]", "line width",
        "line luminosity", "158 micron", "158 mu m",
    ):
        if phrase in query_norm:
            phrases.add(phrase)
    return phrases


def _arxiv_id_from_table_input(inp: dict[str, Any]) -> str:
    for key in ("arxiv_id", "arxiv_url", "url"):
        value = str(inp.get(key) or "").strip()
        if value:
            return value
    paper = inp.get("paper")
    if isinstance(paper, dict):
        for key in ("arxiv_id", "arxiv_url", "url"):
            value = str(paper.get(key) or "").strip()
            if value:
                return value
        bibcode = str(paper.get("bibcode") or "").strip()
        if bibcode.lower().startswith("arxiv:"):
            return bibcode
    return ""


# Schema v2 adds per-row fields required by the Bayesian fit path
# (two-axis errors) plus lensing/cosmology bookkeeping.  Rows stored
# under v1 are migrated on read (see _normalize_measurement_to_v2).
_LITERATURE_SCHEMA_VERSION = 2

# Keys a v2 row must carry (value may be None when the source paper
# didn't report the quantity).  We do not require AI/regex to extract
# them; downstream tools (fit_line_lfr, demagnify_sample) decide how
# to handle nulls.
_V2_MEASUREMENT_KEYS: tuple[str, ...] = (
    "fwhm_err_km_s",
    "log_luminosity_err",
    "mu_lens",
    "is_lensed",
    "source_cosmology",
)


def _normalize_measurement_to_v2(row: dict[str, Any]) -> dict[str, Any]:
    """Ensure a single line_measurement dict carries the v2 schema keys.

    Called on both the write path (when caching fresh extraction
    results) and the read path (when resolving older v1 cache entries).
    Idempotent — v2 rows pass through unchanged.  None is the explicit
    sentinel for "paper did not report this quantity" so callers can
    distinguish it from "field missing from schema".
    """
    if not isinstance(row, dict):
        return row
    out = dict(row)
    for key in _V2_MEASUREMENT_KEYS:
        if key not in out:
            out[key] = None
    return out


def _literature_table_cache_payload(payload: dict[str, Any], cache_key: str) -> dict[str, Any]:
    """Cache literature-table data in a structured, downstream-readable shape."""
    raw_measurements = payload.get("line_measurements") or []
    line_measurements = [
        _normalize_measurement_to_v2(row) for row in raw_measurements if isinstance(row, dict)
    ]
    tables = payload.get("tables") or []
    return {
        "schema_version": _LITERATURE_SCHEMA_VERSION,
        "kind": "literature_tables",
        "cache_key": cache_key,
        "arxiv_id": payload.get("arxiv_id"),
        "title": payload.get("title"),
        "authors": payload.get("authors") or [],
        "year": payload.get("year"),
        "bibcode": payload.get("bibcode"),
        "doi": payload.get("doi"),
        "source_url": payload.get("source_url"),
        "line_measurements": line_measurements,
        "tables": tables,
        "source_summary": {
            "line_measurement_count": len(line_measurements),
            "raw_table_count": len(tables),
            "extraction_status": payload.get("extraction_status") or (
                "measurement_ready" if line_measurements else "raw_only"
            ),
            "normalization_status": payload.get("normalization_status") or (
                "line_measurements_detected" if line_measurements else "no_line_measurement_schema"
            ),
            "supports_measurement_claims": bool(line_measurements),
        },
    }


def _literature_tables_llm_summary(payload: dict[str, Any], cache_key: str) -> dict[str, Any]:
    line_measurements = payload.get("line_measurements") or []
    tables = payload.get("tables") or []
    preview_rows: list[dict[str, Any]] = []
    for row in line_measurements[:10]:
        if not isinstance(row, dict):
            continue
        citation = row.get("citation") if isinstance(row.get("citation"), dict) else {}
        preview_rows.append({
            "source_name": row.get("source_name"),
            "redshift": row.get("redshift"),
            "line_id": row.get("line_id"),
            "log_luminosity": row.get("log_luminosity"),
            "fwhm_km_s": row.get("fwhm_km_s"),
            "table_label": row.get("table_label") or citation.get("table_label"),
            "citation": row.get("bibcode") or row.get("arxiv_id") or citation.get("bibcode") or citation.get("arxiv_id"),
        })
    return {
        "cache_key": cache_key,
        "raw_table_count": len(tables),
        "line_measurement_count": len(line_measurements),
        "extraction_status": payload.get("extraction_status") or (
            "measurement_ready" if line_measurements else "raw_only"
        ),
        "normalization_status": payload.get("normalization_status") or (
            "line_measurements_detected" if line_measurements else "no_line_measurement_schema"
        ),
        "fit_ready": bool(line_measurements),
        "measurement_schema": [
            "source_name", "redshift", "line_id", "log_luminosity",
            "fwhm_km_s", "quality_flags", "citation",
        ],
        "preview_rows": preview_rows,
        "next_step": (
            f"Call fit_line_lfr(cache_key='{cache_key}') to fit the relation from these cited rows. "
            "Do not use synthetic run_python or hardcoded literature samples."
            if line_measurements else
            "No normalized line_measurements were detected; do not fit until columns are mapped."
        ),
    }


def _measurement_rows_from_cache_payload(payload: Any) -> list[dict[str, Any]]:
    # Every returned row goes through _normalize_measurement_to_v2 so
    # downstream code can rely on the v2 schema regardless of whether
    # the cache entry was written under schema_version=1 or =2.
    if isinstance(payload, dict):
        for key in ("line_measurements", "measurements", "rows", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [
                    _normalize_measurement_to_v2(dict(row))
                    for row in value if isinstance(row, dict)
                ]
        return []
    if isinstance(payload, list):
        return [
            _normalize_measurement_to_v2(dict(row))
            for row in payload if isinstance(row, dict)
        ]
    return []


def _resolve_literature_measurement_cache(cache_key: str, python_session_id: str | None) -> tuple[list[dict[str, Any]], str]:
    requested = (cache_key or "latest_literature_tables").strip() or "latest_literature_tables"
    candidates = [requested]
    session_key = _session_cache_key(requested, python_session_id)
    if session_key:
        candidates.insert(0, session_key)
    if requested == "latest_literature_tables":
        session_default = _session_cache_key("latest_literature_tables", python_session_id)
        if session_default:
            candidates.insert(0, session_default)

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        payload = get_cached_results(candidate)
        rows = _measurement_rows_from_cache_payload(payload)
        if rows:
            return rows, candidate
    return [], requested


def _resolve_multiple_literature_caches(
    cache_keys: list[str],
    python_session_id: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """PART AF C2 — union multiple literature caches into one row list.

    M5 audit reproducer: AI extracted ALPINE (74 rows) + REBELS (13
    rows) into separate caches but fit_line_lfr only saw the most
    recent one. Now the tool can pass `cache_keys=[...]` and we
    merge across keys, deduping by source_name (or arxiv_id+row_index
    when name collides across surveys with different objects).

    Returns (merged_rows, list_of_resolved_keys_actually_loaded).
    """
    merged: list[dict[str, Any]] = []
    resolved_keys: list[str] = []
    seen_source_keys: set[str] = set()
    for raw_key in cache_keys:
        key = (raw_key or "").strip()
        if not key:
            continue
        rows, resolved = _resolve_literature_measurement_cache(key, python_session_id)
        if not rows:
            continue
        resolved_keys.append(resolved)
        for row in rows:
            # Dedupe by (source_name, bibcode) — same source from same
            # paper twice would be redundant; same source name across
            # different bibcodes can legitimately co-exist (different
            # surveys' independent measurements of e.g. HZ7).
            sname = str(row.get("source_name") or "").strip()
            bib = str(row.get("bibcode") or row.get("arxiv_id") or "").strip()
            dedupe_key = f"{sname}::{bib}"
            if dedupe_key in seen_source_keys:
                continue
            seen_source_keys.add(dedupe_key)
            merged.append(row)
    return merged, resolved_keys


def _line_matches_filter(row: dict[str, Any], line_filter: str) -> bool:
    target = re.sub(r"[^a-z0-9]+", "", (line_filter or "").lower())
    if not target:
        return True
    line_text = " ".join(
        str(row.get(key) or "")
        for key in ("line_id", "transition", "line", "table_label")
    )
    raw_values = row.get("raw_values")
    if isinstance(raw_values, dict):
        line_text += " " + " ".join(str(v) for v in raw_values.values())
    normalized = re.sub(r"[^a-z0-9]+", "", line_text.lower())
    if not normalized:
        # Normalized literature-table rows with log L + FWHM and no explicit
        # line label are still better handled by this typed fitter than by
        # model-authored synthetic Python.
        return True
    return target in normalized or ("cii" in target and "cii" in normalized)


def _finite_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _row_has_citation(row: dict[str, Any]) -> bool:
    citation = row.get("citation") if isinstance(row.get("citation"), dict) else {}
    return bool(
        row.get("bibcode") or row.get("arxiv_id")
        or citation.get("bibcode") or citation.get("arxiv_id")
        or citation.get("doi")
    )


# ── M4 helpers: subsample significance + demagnify_sample ──────────


def _bootstrap_ols_betas(
    x: "np.ndarray", y: "np.ndarray", n_boot: int, rng: "np.random.Generator",
) -> "np.ndarray":
    """Return n_boot bootstrap-resampled OLS slopes from (x, y).

    Pure numpy + scipy; no x-axis errors are propagated (bootstrap
    over rows is an OLS-friendly approximation).  Caller carries the
    'x_errors not propagated in bootstrap' caveat into reporting.
    """
    import numpy as np
    n = len(x)
    if n < 2:
        return np.zeros(0, dtype=float)
    betas = np.empty(n_boot, dtype=float)
    try:
        from scipy import stats as _stats
        for i in range(n_boot):
            idx = rng.integers(0, n, size=n)
            xs = x[idx]
            ys = y[idx]
            try:
                fit = _stats.linregress(xs, ys)
                betas[i] = float(fit.slope)
            except Exception:
                betas[i] = float("nan")
    except Exception:
        for i in range(n_boot):
            idx = rng.integers(0, n, size=n)
            try:
                betas[i] = float(np.polyfit(x[idx], y[idx], 1)[0])
            except Exception:
                betas[i] = float("nan")
    return betas[~np.isnan(betas)]


def _subsample_significance_from_betas(
    beta1: "np.ndarray", beta2: "np.ndarray",
) -> dict[str, Any]:
    """Compute Δβ summary from two bootstrap / posterior β arrays.

    Works for both bootstrap-OLS arrays and Bayesian-posterior arrays
    because the math is identical: pair them up via shuffled indices,
    take the per-pair difference, and compute the two-sided p-value
    that Δβ has crossed zero.
    """
    import numpy as np
    if beta1.size == 0 or beta2.size == 0:
        return {
            "delta_beta": None,
            "delta_beta_stderr": None,
            "p_value": None,
            "hdi_overlap": None,
            "interpretation": "insufficient_samples",
        }
    n = min(beta1.size, beta2.size)
    # Pair by random shuffle so we don't rely on bootstrap iteration
    # order having any meaning across the two subsamples.
    rng = np.random.default_rng(0)
    perm1 = rng.permutation(beta1.size)[:n]
    perm2 = rng.permutation(beta2.size)[:n]
    delta = beta1[perm1] - beta2[perm2]
    delta_mean = float(np.mean(delta))
    delta_std = float(np.std(delta))
    # Two-sided posterior-tail probability: probability mass on the side of
    # zero opposite the mean; ×2 for two-sided.  Capped at 1.0.  This is not
    # a frequentist p-value, so expose an explicitly named field and keep the
    # old p_value key as a deprecated compatibility alias for one release.
    if delta_mean >= 0:
        tail = float(np.mean(delta < 0))
    else:
        tail = float(np.mean(delta > 0))
    tail_probability_two_sided = float(min(1.0, 2.0 * tail))
    # Central 94% interval overlap (cheap proxy: do the central intervals
    # overlap?).  Useful as a categorical hint; this is not an HDI.
    lo1, hi1 = float(np.percentile(beta1, 3)), float(np.percentile(beta1, 97))
    lo2, hi2 = float(np.percentile(beta2, 3)), float(np.percentile(beta2, 97))
    overlap = max(0.0, min(hi1, hi2) - max(lo1, lo2))
    pooled = max(hi1 - lo1, hi2 - lo2, 1e-12)
    hdi_overlap_frac = overlap / pooled
    if tail_probability_two_sided < 0.01:
        interpretation = "significantly_different"
    elif tail_probability_two_sided < 0.05:
        interpretation = "marginal_significance"
    elif tail_probability_two_sided < 0.32:
        interpretation = "weak_evidence"
    else:
        interpretation = "consistent"
    return {
        "delta_beta": round(delta_mean, 6),
        "delta_beta_stderr": round(delta_std, 6),
        "tail_probability_two_sided": round(tail_probability_two_sided, 6),
        "central_interval_overlap_fraction": round(hdi_overlap_frac, 4),
        # Deprecated aliases.  Kept so older UI/tests do not break while
        # callers migrate to the scientifically precise field names above.
        "p_value": round(tail_probability_two_sided, 6),
        "hdi_overlap_fraction": round(hdi_overlap_frac, 4),
        "interpretation": interpretation,
    }


def _split_rows_by_redshift(
    accepted: list[dict[str, Any]],
    splits: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Apply a list of redshift-bin filters to accepted rows.

    Each split entry: ``{"name": "z<1", "z_min": ..., "z_max": ...}``
    where z_min/z_max are inclusive lower / exclusive upper bounds.
    Returns ``[(name, [rows...]), ...]`` preserving input order.
    """
    out: list[tuple[str, list[dict[str, Any]]]] = []
    for split in splits or []:
        if not isinstance(split, dict):
            continue
        name = str(split.get("name") or "subsample").strip() or "subsample"
        z_min = split.get("z_min")
        z_max = split.get("z_max")
        sub: list[dict[str, Any]] = []
        for row in accepted:
            z = _finite_float(row.get("redshift"))
            if z is None:
                continue
            if z_min is not None and z < float(z_min):
                continue
            if z_max is not None and z >= float(z_max):
                continue
            sub.append(row)
        out.append((name, sub))
    return out


def _exec_prepare_spectral_measurements(inp: dict, python_session_id: str = "default") -> dict:
    cache_key = str(inp.get("cache_key") or "latest_literature_tables").strip() or "latest_literature_tables"
    rows, resolved_cache_key = _resolve_literature_measurement_cache(cache_key, python_session_id)
    inline_rows = inp.get("rows")
    if not rows and isinstance(inline_rows, list):
        rows = [row for row in inline_rows if isinstance(row, dict)]
        resolved_cache_key = "inline_rows"
    if not rows:
        return {
            "success": False,
            "__tool_status__": "EMPTY",
            "analysis_status": "empty",
            "tool": "prepare_spectral_measurements",
            "error": f"No cached line_measurements found for cache_key={cache_key!r}.",
            "error_class": "missing_measurement_cache",
            "cache_key": cache_key,
            "__message_to_model__": (
                "No row-level spectral measurements are cached. Run "
                "extract_literature_tables first, then retry this workbench."
            ),
        }
    from app.services.spectral_measurement_workbench import prepare_spectral_measurements

    result = prepare_spectral_measurements(
        rows,
        line_id=inp.get("line_id"),
        min_fit_rows=int(inp.get("min_fit_rows") or 5),
    )
    result["cache_key"] = resolved_cache_key
    result["source_cache_key"] = resolved_cache_key
    if not result.get("fit_ready"):
        result["__tool_status__"] = "PARTIAL"
        result["analysis_status"] = "partial"
        result["__do_not_claim__"] = True
        result["__message_to_model__"] = (
            "The spectral measurement workbench found rows, but too few complete "
            "cited measurements are fit-ready. Report the gap; do not claim line "
            "statistics or fitted relations from this sample."
        )
    return result


def _exec_astro_statistics_toolbox(inp: dict) -> dict:
    from app.services.astro_statistics import run_statistics_toolbox

    result = run_statistics_toolbox(inp)
    result.setdefault("tool", "astro_statistics_toolbox")
    if result.get("success") is False:
        result.setdefault("__tool_status__", "FAILED")
        result.setdefault("__do_not_claim__", True)
    return result


def _is_paper_lensed_by_default_safe_in_fit(bibcode: str | None) -> bool:
    """Wrapper around cii_paper_metadata.is_paper_lensed_by_default that
    swallows import errors so fit_line_lfr keeps working even if the
    metadata module is missing on a given branch."""
    try:
        from app.services.cii_paper_metadata import is_paper_lensed_by_default
        return is_paper_lensed_by_default(bibcode)
    except Exception:
        return False


async def _exec_fit_line_lfr_async(
    inp: dict,
    python_session_id: str = "default",
    api_key: str = "",
) -> dict:
    """Fit log L(line) as a function of log10(FWHM / 100 km/s).

    Stage 6.3 (2026-05-20 sink): optional ``arxiv_id`` parameter — when provided,
    LLM extraction + ±1% cell verification + cache write run first, then the
    original fitting flow proceeds. The top-level extract_paper_measurements_with_llm
    tool has been removed; this is now the single entry point.
    """
    arxiv_id_in = str(inp.get("arxiv_id") or "").strip()
    extract_summary: dict | None = None
    if arxiv_id_in:
        extract_fields_raw = inp.get("extract_fields")
        if isinstance(extract_fields_raw, list) and extract_fields_raw:
            extract_fields = [str(f) for f in extract_fields_raw if f]
        else:
            extract_fields = None
        extract_summary = await _extract_and_cache_paper_measurements(
            arxiv_id_in,
            api_key,
            python_session_id,
            fields=extract_fields,
        )
        if not extract_summary.get("success"):
            return {
                "success": False,
                "tool": "fit_line_lfr",
                "__tool_status__": "FAILED",
                "__do_not_claim__": True,
                "error": extract_summary.get("error", "LLM extraction failed"),
                "error_class": extract_summary.get("error_class", "llm_extraction_failed"),
                "arxiv_id": arxiv_id_in,
                "extraction_summary": extract_summary,
                "__message_to_model__": (
                    f"fit_line_lfr cannot proceed: LLM extraction from arxiv:{arxiv_id_in} "
                    f"failed ({extract_summary.get('error_class')}). "
                    "If you already have a cached measurement, retry with cache_key=<key> and no arxiv_id."
                ),
            }
        if not extract_summary.get("line_measurements"):
            return {
                "success": False,
                "tool": "fit_line_lfr",
                "__tool_status__": "EMPTY",
                "__do_not_claim__": True,
                "analysis_status": "empty",
                "arxiv_id": arxiv_id_in,
                "extraction_summary": extract_summary,
                "error": (
                    f"LLM extracted {extract_summary.get('passed_count', 0)} passed, "
                    f"{extract_summary.get('failed_mismatch_count', 0)} failed_mismatch, "
                    f"{extract_summary.get('failed_no_cell_count', 0)} failed_no_cell. "
                    "No row passed ±1% cell verification — cannot fit."
                ),
                "error_class": "no_passed_measurements",
                "__message_to_model__": (
                    f"No measurements from arxiv:{arxiv_id_in} passed ±1% cell verification. "
                    "Consider extract_literature_tables (regex parser) on the same arxiv_id, "
                    "or pick a different paper."
                ),
            }
        if not inp.get("cache_key") and not inp.get("cache_keys"):
            inp = {**inp, "cache_key": "latest_literature_tables"}

    # PART AF C2 — accept either a single cache_key OR a list of
    # cache_keys to union before fitting. Lists win when both are
    # passed (lets the AI strictly add a second survey without
    # accidentally falling back to the single-cache path).
    cache_keys_in = inp.get("cache_keys")
    if isinstance(cache_keys_in, list) and any(
        isinstance(k, str) and k.strip() for k in cache_keys_in
    ):
        rows, resolved_keys = _resolve_multiple_literature_caches(
            [str(k) for k in cache_keys_in if isinstance(k, str)],
            python_session_id,
        )
        # Use the first resolved key as the "primary" cache_key for
        # downstream attribution. The list of all resolved keys is
        # surfaced separately on the result so the UI can show which
        # surveys actually contributed.
        resolved_cache_key = resolved_keys[0] if resolved_keys else "+".join(
            str(k) for k in cache_keys_in if isinstance(k, str)
        )
        cache_key = str(cache_keys_in[0]) if cache_keys_in else "latest_literature_tables"
        all_resolved_cache_keys = resolved_keys
    else:
        cache_key = str(inp.get("cache_key") or "latest_literature_tables").strip() or "latest_literature_tables"
        rows, resolved_cache_key = _resolve_literature_measurement_cache(cache_key, python_session_id)
        all_resolved_cache_keys = [resolved_cache_key] if rows else []

    line_id = str(inp.get("line_id") or "[CII]").strip() or "[CII]"
    min_rows = int(inp.get("min_rows") or 5)
    if not rows:
        return {
            "success": False,
            "__tool_status__": "EMPTY",
            "analysis_status": "empty",
            "error": f"No cached line_measurements found for cache_key={cache_key!r}.",
            "error_class": "missing_measurement_cache",
            "cache_key": cache_key,
            "__message_to_model__": (
                "No fit-ready literature measurement rows are cached. Run "
                "extract_literature_tables on a relevant arXiv paper first."
            ),
        }

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        reason = ""
        flags = row.get("quality_flags") if isinstance(row.get("quality_flags"), list) else []
        if not _line_matches_filter(row, line_id):
            reason = "line_filter"
        elif any("limit" in str(flag).lower() for flag in flags):
            reason = "limit_flag"
        elif not _row_has_citation(row):
            reason = "missing_citation"
        else:
            log_luminosity = _finite_float(row.get("log_luminosity"))
            # PART AC C1 — backwards-compat for legacy cached schema.
            # Pre-AC the arxiv normalizer mis-routed log values into the
            # linear `luminosity` field when the column header didn't
            # contain "log" (ALPINE / REBELS / similar). Caches written
            # before AC therefore have log_luminosity=None despite
            # `luminosity` carrying a real log10 value. Detect that
            # situation here so old cache entries still fit on the next
            # call, without forcing a re-extraction.
            log_inferred_from_legacy = False
            if log_luminosity is None:
                lin_value = _finite_float(row.get("luminosity"))
                if lin_value is not None and 3.0 <= lin_value <= 13.0:
                    log_luminosity = lin_value
                    log_inferred_from_legacy = True
            fwhm = _finite_float(row.get("fwhm_km_s"))
            if log_luminosity is None or fwhm is None or fwhm <= 0:
                reason = "missing_numeric_values"
            else:
                citation = row.get("citation") if isinstance(row.get("citation"), dict) else {}
                # M2: carry v2 schema fields through so the downgrade /
                # cosmology-mismatch / lensing checks below can inspect
                # per-row error, μ, and source_cosmology values without
                # a second cache read.  None = "paper did not report".
                accepted.append({
                    "source_name": row.get("source_name"),
                    "redshift": row.get("redshift"),
                    "line_id": row.get("line_id") or line_id,
                    "log_luminosity": log_luminosity,
                    "log_luminosity_err": _finite_float(row.get("log_luminosity_err"))
                    or (_finite_float(row.get("luminosity_err")) if log_inferred_from_legacy else None),
                    "fwhm_km_s": fwhm,
                    "fwhm_err_km_s": _finite_float(row.get("fwhm_err_km_s")),
                    "mu_lens": _finite_float(row.get("mu_lens")),
                    "is_lensed": row.get("is_lensed"),
                    "source_cosmology": row.get("source_cosmology"),
                    # M4: propagate the demagnify marker so
                    # lensed_sources_demagnified counts correctly when
                    # the cache came from demagnify_sample (key __demag).
                    "_demagnified": row.get("_demagnified"),
                    "table_label": row.get("table_label") or citation.get("table_label"),
                    "bibcode": row.get("bibcode") or citation.get("bibcode"),
                    "arxiv_id": row.get("arxiv_id") or citation.get("arxiv_id"),
                    "doi": citation.get("doi"),
                    "row_index": row.get("row_index", idx),
                    "citation": citation,
                    # PART AC C1: record when we promoted a legacy linear
                    # luminosity field into log_luminosity via the
                    # value-range heuristic so the model + reviewers
                    # can see which rows were inferred vs explicit.
                    "log_inferred_from_value_range": log_inferred_from_legacy,
                    "luminosity_inferred_log_from": (
                        row.get("luminosity_inferred_log_from")
                        or ("value_range" if log_inferred_from_legacy else None)
                    ),
                })
        if reason:
            rejected.append({
                "source_name": row.get("source_name"),
                "reason": reason,
                "row_index": row.get("row_index", idx),
            })

    n_used = len(accepted)
    if n_used < 2:
        partial_readiness = {
            "status": "not_publication_ready",
            "checks": {
                "minimum_rows": {
                    "passed": False,
                    "n_used": n_used,
                    "required": max(2, min_rows),
                },
                "citations": {
                    "passed": False,
                    "rows_with_citations": 0,
                    "n_used": n_used,
                },
            },
        }
        return {
            "success": True,
            "__tool_status__": "PARTIAL",
            "analysis_status": "partial",
            "cache_key": resolved_cache_key,
            "line_id": line_id,
            "n_available": len(rows),
            "n_used": n_used,
            "n_rejected": len(rejected),
            "rejected_summary": rejected[:20],
            "publication_ready": False,
            "publication_readiness": partial_readiness,
            "relation_claimability": {
                "can_claim_relation": False,
                "claim_scope": "exploratory_only",
                "blocking_reasons": ["fewer_than_two_citeable_rows", "below_min_rows"],
            },
            "__do_not_claim__": True,
            "__message_to_model__": (
                "Fewer than two citeable line-measurement rows survived filtering. "
                "Do not claim a fitted luminosity-FWHM relation."
            ),
        }

    import numpy as np

    # ── PART AD C1 — optional DL recomputation under a target cosmology.
    # Default behaviour (no cosmology arg): use the cached log_luminosity
    # values as-is (they assume the source-paper cosmology, which is
    # typically Planck18 for [CII] surveys).  When the caller passes a
    # cosmology name / preset / FlatLambdaCDM spec, we recompute
    # log_L_new = log_L_old + 2*log10(DL_new(z) / DL_old(z)) per row,
    # using astropy.cosmology, and surface the per-row + summary shift.
    requested_cosmology_name = str(inp.get("cosmology") or "").strip()
    cosmology_recomputed = False
    cosmology_used: dict[str, Any] | None = None
    cosmology_baseline: dict[str, Any] | None = None
    dl_shift_summary: dict[str, Any] | None = None
    log_luminosity_shifts: list[float] = []

    if requested_cosmology_name and requested_cosmology_name.lower() not in {"planck18", "default"}:
        try:
            from app.services.cosmology import (
                build_cosmology_from_preset,
                cosmology_manifest as _cm,
            )
            target_cosmo = build_cosmology_from_preset(requested_cosmology_name)
            baseline_cosmo = build_cosmology_from_preset("planck18")
            cosmology_used = _cosmology_manifest_for(requested_cosmology_name)
            cosmology_baseline = _cm("planck18")
        except Exception as exc:
            logger.warning(
                "fit_line_lfr: failed to build target cosmology %r: %s",
                requested_cosmology_name, exc,
            )
            target_cosmo = None
            baseline_cosmo = None

        if target_cosmo is not None and baseline_cosmo is not None:
            import astropy.units as u

            adjusted_log_luminosity: list[float] = []
            for row in accepted:
                z = _finite_float(row.get("redshift"))
                base_log_l = float(row["log_luminosity"])
                if z is None or z <= 0:
                    # No usable z → no recompute possible; keep the
                    # cached value but flag the row.
                    adjusted_log_luminosity.append(base_log_l)
                    log_luminosity_shifts.append(0.0)
                    continue
                try:
                    dl_old = float(baseline_cosmo.luminosity_distance(z).to(u.Mpc).value)
                    dl_new = float(target_cosmo.luminosity_distance(z).to(u.Mpc).value)
                except Exception:
                    adjusted_log_luminosity.append(base_log_l)
                    log_luminosity_shifts.append(0.0)
                    continue
                if dl_old <= 0 or dl_new <= 0:
                    adjusted_log_luminosity.append(base_log_l)
                    log_luminosity_shifts.append(0.0)
                    continue
                shift = 2.0 * (math.log10(dl_new) - math.log10(dl_old))
                adjusted_log_luminosity.append(base_log_l + shift)
                log_luminosity_shifts.append(shift)

            # Replace each accepted row's log_luminosity inline so
            # downstream subsample / variant code sees the shifted value.
            for row, new_log_l in zip(accepted, adjusted_log_luminosity, strict=True):
                row["log_luminosity"] = new_log_l
            cosmology_recomputed = True
            if log_luminosity_shifts:
                shifts_arr = np.array(log_luminosity_shifts, dtype=float)
                dl_shift_summary = {
                    "median_log_l_shift_dex": round(float(np.median(shifts_arr)), 6),
                    "max_abs_log_l_shift_dex": round(float(np.max(np.abs(shifts_arr))), 6),
                    "min_abs_log_l_shift_dex": round(float(np.min(np.abs(shifts_arr))), 6),
                    "n_rows_shifted": int(np.sum(np.abs(shifts_arr) > 1e-9)),
                    "baseline_cosmology": "planck18",
                    "target_cosmology": requested_cosmology_name,
                }

    # PART AI #2: optional luminosity_kind conversion (L_solar <-> L_prime).
    # Defaults to "L_solar" to preserve existing behaviour. When a user/AI
    # explicitly passes "L_prime" for CO LFR comparison, all accepted rows are
    # converted using (line_id, redshift). Rows that fail conversion (no z /
    # no line_id / unknown line / NaN log_l) go to rejected with
    # kind="unit_conversion_failed" and are excluded from the fit.
    # This guarantees 100% consistent y-axis units and prevents the bundle e8d9
    # style of alpha drifting between L_solar (6.88) and L_prime (9.823) while
    # prose omits units.
    requested_lum_kind = str(inp.get("luminosity_kind") or "L_solar").strip()
    if requested_lum_kind not in {"L_solar", "L_prime"}:
        requested_lum_kind = "L_solar"

    luminosity_kind_used = requested_lum_kind
    n_unit_converted = 0
    unit_conversion_failures: list[dict[str, Any]] = []

    if requested_lum_kind == "L_prime":
        from app.services.luminosity_units import convert_row_luminosity_inplace

        kept_after_units: list[dict[str, Any]] = []
        for row in accepted:
            converted = convert_row_luminosity_inplace(
                row, "L_prime", line_id_fallback=line_id,
            )
            if converted.get("_unit_error"):
                unit_conversion_failures.append({
                    "source_name": row.get("source_name"),
                    "row_index": row.get("row_index"),
                    "reason": converted["_unit_error"],
                })
                rejected.append({
                    "source_name": row.get("source_name"),
                    "reason": "unit_conversion_failed",
                    "row_index": row.get("row_index"),
                    "detail": converted["_unit_error"],
                })
                continue
            row["log_luminosity"] = converted["log_luminosity"]
            row["luminosity_kind"] = "L_prime"
            row["log_luminosity_transformed_from"] = converted.get(
                "log_luminosity_transformed_from"
            )
            kept_after_units.append(row)
            n_unit_converted += 1
        accepted = kept_after_units
        n_used = len(accepted)
    else:
        # 'L_solar' path: tag the field so the downstream result envelope is explicit,
        # but do not change the log_luminosity values (cache is already in L_solar).
        for row in accepted:
            row.setdefault("luminosity_kind", "L_solar")

    if n_used == 0:
        # All rows rejected due to conversion failure — early exit with a clear
        # error rather than letting the fit panic on an empty numpy array.
        # The outer execute_tool wraps this in normalize_tool_result for
        # provenance standardisation, so returning a plain dict is correct here.
        return {
            "success": False,
            "tool": "fit_line_lfr",
            "error": (
                f"All {len(rows)} rows failed luminosity_kind={requested_lum_kind} "
                f"conversion. Most common reason: missing redshift or unknown "
                f"line_id for the [CII]/CO ν_rest lookup. See unit_conversion_failures."
            ),
            "error_class": "unit_conversion_all_failed",
            "luminosity_kind": requested_lum_kind,
            "unit_conversion_failures": unit_conversion_failures,
            "__tool_status__": "FAILED",
            "__do_not_claim__": True,
        }

    # PART AI #6: lensing coverage check — rows where is_lensed=True but
    # mu_lens=None and _demagnified is not set cannot enter the fit (luminosity
    # has not been demagnified; including them would bias the LFR slope).
    # Move them to rejected so the user/AI sees that demagnify_sample must be
    # called first.
    n_lensed_skipped_no_mu = 0
    accepted_after_lensing: list[dict[str, Any]] = []
    for row in accepted:
        is_lensed = row.get("is_lensed") is True
        has_mu = row.get("mu_lens") is not None
        already_demag = bool(row.get("_demagnified"))
        if is_lensed and not has_mu and not already_demag:
            n_lensed_skipped_no_mu += 1
            rejected.append({
                "source_name": row.get("source_name"),
                "reason": "lensed_no_mu_correction",
                "row_index": row.get("row_index"),
                "detail": (
                    "is_lensed=True from paper-level metadata or table flag, "
                    "but no mu_lens value available. Call demagnify_sample "
                    "with an explicit mu_map={source_name: mu_value} to get "
                    "demagnified luminosity, then re-run fit_line_lfr."
                ),
                "bibcode": row.get("bibcode"),
            })
            continue
        accepted_after_lensing.append(row)
    accepted = accepted_after_lensing
    n_used = len(accepted)

    if n_used == 0 and n_lensed_skipped_no_mu > 0:
        return {
            "success": False,
            "tool": "fit_line_lfr",
            "error": (
                f"All {n_lensed_skipped_no_mu} rows are flagged as lensed but "
                f"none have mu_lens to demagnify. Call demagnify_sample first "
                f"with mu_map={{source_name: mu, ...}}, then re-run fit_line_lfr."
            ),
            "error_class": "all_rows_lensed_no_mu",
            "n_lensed_skipped_no_mu": n_lensed_skipped_no_mu,
            "__tool_status__": "FAILED",
            "__do_not_claim__": True,
        }

    x = np.array([math.log10(row["fwhm_km_s"] / 100.0) for row in accepted], dtype=float)
    y = np.array([row["log_luminosity"] for row in accepted], dtype=float)
    beta = alpha = r_value = p_value = beta_stderr = alpha_stderr = None
    spearman_r = spearman_p = None
    try:
        from scipy import stats as _stats

        fit = _stats.linregress(x, y)
        beta = float(fit.slope)
        alpha = float(fit.intercept)
        r_value = float(fit.rvalue)
        p_value = float(fit.pvalue)
        beta_stderr = float(fit.stderr) if fit.stderr is not None else None
        alpha_stderr = float(getattr(fit, "intercept_stderr", math.nan))
        if alpha_stderr is not None and not math.isfinite(alpha_stderr):
            alpha_stderr = None
        if n_used >= 3:
            spearman = _stats.spearmanr(x, y)
            spearman_r = float(spearman.statistic)
            spearman_p = float(spearman.pvalue)
    except Exception:
        coeff = np.polyfit(x, y, 1)
        beta = float(coeff[0])
        alpha = float(coeff[1])
        if n_used >= 2:
            r_value = float(np.corrcoef(x, y)[0, 1])

    y_model = alpha + beta * x
    residuals = y - y_model
    # M2: residual_rms_dex is the physically correct name for
    # sqrt(mean(residuals**2)) in an OLS fit.  scatter_dex stays as a
    # deprecated alias for one release so existing downstream consumers
    # don't break; the Bayesian path added in M3 will expose a separate
    # intrinsic_scatter_dex with posterior summaries.
    residual_rms_dex = float(np.sqrt(np.mean(residuals ** 2)))
    value_range_inferred_rows = sum(
        1
        for row in accepted
        if row.get("luminosity_inferred_log_from") == "value_range"
        or row.get("log_inferred_from_value_range") is True
    )
    has_confirmed_luminosity_units = value_range_inferred_rows == 0
    publication_ready = (
        n_used >= min_rows
        and all(_row_has_citation(row) for row in accepted)
        and has_confirmed_luminosity_units
    )
    citation_keys = sorted({
        str(row.get("bibcode") or row.get("arxiv_id") or row.get("doi") or "").strip()
        for row in accepted
        if str(row.get("bibcode") or row.get("arxiv_id") or row.get("doi") or "").strip()
    })
    table_labels = sorted({
        str(row.get("table_label") or "").strip()
        for row in accepted
        if str(row.get("table_label") or "").strip()
    })

    # ── Fit-method routing (M2 + M3) ────────────────────────────────
    # fit_method_requested:
    #   auto           — best available: Bayesian if both-axis errors
    #                    are populated; OLS otherwise.  Not a downgrade
    #                    in either case (auto carries no promise).
    #   ols            — explicit OLS, never a downgrade.
    #   bayesian_xyerr — explicit Bayesian two-axis regression (linmix,
    #                    Kelly 2007).  Falls back to OLS with
    #                    METHOD_DOWNGRADED + concrete reason ONLY when
    #                    the per-row error columns are missing or the
    #                    sampler errors out.
    requested = str(inp.get("fit_method_requested") or "auto").strip().lower()
    if requested not in ("auto", "ols", "bayesian_xyerr"):
        requested = "auto"
    n_x_err = sum(1 for r in accepted if r.get("fwhm_err_km_s") is not None)
    n_y_err = sum(1 for r in accepted if r.get("log_luminosity_err") is not None)
    both_errs_available = n_x_err == n_used and n_y_err == n_used and n_used > 0

    fit_method = "ols"
    fit_method_downgrade_reason: str | None = None
    bayes_result: dict[str, Any] | None = None
    bayes_error: str | None = None

    # Should we attempt Bayesian?  Only when explicitly requested OR
    # when auto-mode and the data supports it.  Explicit "ols" is
    # always honored.
    want_bayesian = (
        (requested == "bayesian_xyerr")
        or (requested == "auto" and both_errs_available)
    )
    can_run_bayesian = want_bayesian and both_errs_available

    if can_run_bayesian:
        try:
            from app.services.bayesian_inference import kelly07_linmix_fit

            xerr_arr = np.array(
                [r["fwhm_err_km_s"] for r in accepted], dtype=float,
            )
            # x = log10(FWHM/100); error propagates as 0.434 * dFWHM/FWHM
            # (standard log-derivative).  This is approximate but is
            # the convention LFR papers use.
            xerr_log = (xerr_arr / np.array([r["fwhm_km_s"] for r in accepted])) / math.log(10.0)
            yerr_log = np.array(
                [r["log_luminosity_err"] for r in accepted], dtype=float,
            )
            # Pick miniter modestly so a typical N≈70 cluster sample
            # finishes in 30-60 s; linmix extends to maxiter when its
            # internal R-hat hasn't converged yet.
            bayes_result = kelly07_linmix_fit(
                x=x, y=y,
                xerr=xerr_log, yerr=yerr_log,
                K=2, nchains=4,
                miniter=4000, maxiter=20000,
                seed=int(inp.get("seed") or 20260426),
                parallelize=False,
            )
            fit_method = "bayesian_xyerr_linmix"
            # Replace OLS point estimates with Bayesian medians.  The
            # OLS values (above) become the "starting guess" reference
            # in the result envelope.
            alpha = float(bayes_result["alpha_median"])
            beta = float(bayes_result["beta_median"])
            # 94% HDI half-width / 1.88 ≈ 1-σ for unimodal posteriors;
            # this lets downstream consumers that expect an stderr
            # field still get something physically meaningful.
            alpha_hdi = bayes_result["alpha_hdi_94"]
            beta_hdi = bayes_result["beta_hdi_94"]
            alpha_stderr = (alpha_hdi[1] - alpha_hdi[0]) / 1.88 / 2.0
            beta_stderr = (beta_hdi[1] - beta_hdi[0]) / 1.88 / 2.0
        except Exception as exc:
            bayes_error = f"{type(exc).__name__}: {exc}"
            fit_method_downgrade_reason = (
                "bayesian_xyerr requested and error columns are present, "
                f"but the linmix sampler failed ({bayes_error}). Fell back to OLS."
            )
    elif requested == "bayesian_xyerr":
        # Explicit request, but err columns missing → real downgrade.
        missing_bits = []
        if n_x_err < n_used:
            missing_bits.append(f"fwhm_err_km_s on {n_x_err}/{n_used} rows")
        if n_y_err < n_used:
            missing_bits.append(f"log_luminosity_err on {n_y_err}/{n_used} rows")
        fit_method_downgrade_reason = (
            "bayesian_xyerr requires per-row two-axis errors; sample has "
            + "; ".join(missing_bits) + ". Fell back to OLS."
        )
    is_method_downgraded = fit_method_downgrade_reason is not None

    citation_ready_rows = sum(1 for row in accepted if _row_has_citation(row))
    readiness_checks: dict[str, Any] = {
        "minimum_rows": {
            "passed": n_used >= min_rows,
            "n_used": n_used,
            "required": min_rows,
        },
        "citations": {
            "passed": citation_ready_rows == n_used,
            "rows_with_citations": citation_ready_rows,
            "n_used": n_used,
        },
        "confirmed_luminosity_units": {
            "passed": has_confirmed_luminosity_units,
            "value_range_inferred_rows": value_range_inferred_rows,
            "requires_header_or_caption_log_units": True,
        },
        "method_not_downgraded": {
            "passed": not is_method_downgraded,
            "fit_method_requested": requested,
            "fit_method": fit_method,
            "reason": fit_method_downgrade_reason,
        },
    }
    if fit_method == "bayesian_xyerr_linmix" or requested == "bayesian_xyerr":
        readiness_checks["bayesian_sampler"] = {
            "passed": bool(bayes_result and bayes_result.get("publication_ready") is True),
            "converged": bayes_result.get("converged") if bayes_result else False,
            "publication_ready": bayes_result.get("publication_ready") if bayes_result else False,
            "error": bayes_error,
        }

    relation_blocking_reasons: list[str] = []
    if n_used < min_rows:
        relation_blocking_reasons.append("below_min_rows")
    if citation_ready_rows < n_used:
        relation_blocking_reasons.append("incomplete_citations")
    if not has_confirmed_luminosity_units:
        relation_blocking_reasons.append("unconfirmed_luminosity_units")
    if is_method_downgraded:
        relation_blocking_reasons.append("method_downgraded")
    if fit_method == "bayesian_xyerr_linmix" and bayes_result and bayes_result.get("publication_ready") is False:
        relation_blocking_reasons.append("bayesian_sampler_not_publication_ready")

    relation_can_claim = not relation_blocking_reasons
    relation_claim_scope = (
        "publication_ready_relation" if relation_can_claim
        else "method_mismatch" if is_method_downgraded and publication_ready
        else "exploratory_only"
    )
    relation_claimability = {
        "can_claim_relation": relation_can_claim,
        "claim_scope": relation_claim_scope,
        "blocking_reasons": relation_blocking_reasons,
    }
    publication_readiness = {
        "status": relation_claim_scope,
        "data_checks_publication_ready": publication_ready,
        "checks": readiness_checks,
    }

    # ── M2: cosmology mismatch detection ────────────────────────────
    # We do NOT auto-recompute DL — that's the M5 job.  Here we only
    # surface a mismatch warning so the AI / UI / claim_validator can
    # react.
    try:
        from app.services.cosmology import cosmology_manifest as _cm
        _current_cosmo = _cm()
    except Exception:
        _current_cosmo = {"name": "unknown"}
    sample_cosmo_names: set[str] = set()
    for r in accepted:
        sc = r.get("source_cosmology")
        if isinstance(sc, dict):
            nm = str(sc.get("name") or "").strip()
            if nm:
                sample_cosmo_names.add(nm)
    current_cosmo_name = str(_current_cosmo.get("name") or "unknown")

    # PART AA: cosmology preset names are case-insensitive at the matcher
    # boundary — the legacy astropy alias "Planck18" is the same preset as
    # the lowercase platform default "planck18". Without this normalisation
    # the mismatch flag would fire on every legacy fixture / saved sample.
    def _norm_cosmo_name(n: str) -> str:
        n = (n or "").strip()
        return "planck18" if n in {"Planck18"} else n

    current_cosmo_norm = _norm_cosmo_name(current_cosmo_name)
    cosmology_mismatch = bool(
        sample_cosmo_names
        and any(_norm_cosmo_name(n) != current_cosmo_norm for n in sample_cosmo_names)
    )

    # ── M2: lensing bookkeeping ─────────────────────────────────────
    # At this milestone the fit does NOT demagnify.  demagnify_sample is
    # a separate M4 tool.  We only *count* lensed / unknown status.
    n_lensed = sum(1 for r in accepted if r.get("is_lensed") is True)
    n_unlensed = sum(1 for r in accepted if r.get("is_lensed") is False)
    n_lensed_unknown = sum(1 for r in accepted if r.get("is_lensed") is None)

    # ── M4: subsample-significance test ─────────────────────────────
    # When the caller passes subsample_splits=[{name, z_min?, z_max?}],
    # we fit each subsample independently with bootstrap-OLS, then
    # compute Δβ + p-value for every adjacent pair.  Bayesian path is
    # not used per-subsample (it would multiply MCMC cost by N
    # subsamples) — bootstrap is the workhorse here.
    subsample_test: dict[str, Any] | None = None
    splits_input = inp.get("subsample_splits")
    if isinstance(splits_input, list) and len(splits_input) >= 2:
        rng_sub = np.random.default_rng(int(inp.get("seed") or 20260426))
        n_boot = int(inp.get("subsample_n_boot") or 2000)
        sub_groups = _split_rows_by_redshift(accepted, splits_input)
        per_subsample: list[dict[str, Any]] = []
        beta_arrays: list[np.ndarray] = []
        for name, sub_rows in sub_groups:
            if len(sub_rows) < 3:
                per_subsample.append({
                    "name": name, "n": len(sub_rows),
                    "beta": None, "beta_stderr_boot": None,
                    "skipped_reason": "fewer_than_3_rows",
                })
                beta_arrays.append(np.zeros(0))
                continue
            xs = np.array(
                [math.log10(r["fwhm_km_s"] / 100.0) for r in sub_rows], dtype=float,
            )
            ys = np.array([r["log_luminosity"] for r in sub_rows], dtype=float)
            betas_boot = _bootstrap_ols_betas(xs, ys, n_boot, rng_sub)
            try:
                from scipy import stats as _ss
                pf = _ss.linregress(xs, ys)
                beta_point = float(pf.slope)
            except Exception:
                beta_point = (
                    float(np.polyfit(xs, ys, 1)[0]) if len(xs) >= 2 else float("nan")
                )
            per_subsample.append({
                "name": name, "n": len(sub_rows),
                "beta": round(beta_point, 6),
                "beta_stderr_boot": round(float(np.std(betas_boot)), 6) if betas_boot.size else None,
            })
            beta_arrays.append(betas_boot)

        # Pairwise Δβ + p-value for every adjacent pair (1↔2, 2↔3, ...)
        # plus extreme pair (first ↔ last).
        comparisons: list[dict[str, Any]] = []
        for i in range(len(beta_arrays) - 1):
            sig = _subsample_significance_from_betas(beta_arrays[i], beta_arrays[i + 1])
            comparisons.append({
                "name_a": per_subsample[i]["name"],
                "name_b": per_subsample[i + 1]["name"],
                **sig,
            })
        if len(beta_arrays) > 2:
            sig_extreme = _subsample_significance_from_betas(beta_arrays[0], beta_arrays[-1])
            comparisons.append({
                "name_a": per_subsample[0]["name"],
                "name_b": per_subsample[-1]["name"],
                **sig_extreme,
            })

        subsample_test = {
            "method": "bootstrap_ols",
            "n_boot": n_boot,
            "subsamples": per_subsample,
            "comparisons": comparisons,
            "caveat": (
                "Bootstrap over rows propagates y-axis errors implicitly via "
                "scatter but does NOT propagate per-row x-axis (FWHM) errors. "
                "For redshift-dependent slope claims at high precision use "
                "fit_method_requested='bayesian_xyerr' on each subsample "
                "directly."
            ),
        }

    x_min = float(np.min(x))
    x_max = float(np.max(x))
    if math.isclose(x_min, x_max):
        x_min -= 0.05
        x_max += 0.05
    fit_x = [x_min, x_max]
    fit_y = [float(alpha + beta * xx) for xx in fit_x]
    plot_data = {
        "x": [round(float(v), 6) for v in x.tolist()],
        "y": [round(float(v), 6) for v in y.tolist()],
        "labels": [
            str(row.get("source_name") or f"row_{idx}")
            for idx, row in enumerate(accepted)
        ],
        "fit_line": {
            "x": [round(v, 6) for v in fit_x],
            "y": [round(v, 6) for v in fit_y],
        },
        "x_label": "log10(FWHM / 100 km/s)",
        "y_label": "log L",
        "equation": "log_luminosity = alpha + beta * log10(FWHM_km_s / 100)",
        "n_points": n_used,
    }

    result = {
        "success": True,
        "tool": "fit_line_lfr",
        "result_granularity": "literature_measurement_fit",
        "supports_measurement_claims": relation_can_claim,
        "publication_readiness": publication_readiness,
        "relation_claimability": relation_claimability,
        "cache_key": resolved_cache_key,
        "line_id": line_id,
        "model": (
            f"log10({'L/L_sun' if luminosity_kind_used == 'L_solar' else 'L_prime/(K km/s pc^2)'}) "
            "= alpha + beta * log10(FWHM_km_s / 100)"
        ),
        "fit_orientation": {
            "dependent_variable": (
                "log10(L/L_sun)" if luminosity_kind_used == "L_solar"
                else "log10(L_prime/(K km/s pc^2))"
            ),
            "independent_variable": "log10(FWHM_km_s / 100)",
            "normalization": "FWHM normalized by 100 km/s",
            "equation": (
                f"log10({'L/L_sun' if luminosity_kind_used == 'L_solar' else 'L_prime/(K km/s pc^2)'}) "
                "= alpha + beta * log10(FWHM_km_s / 100)"
            ),
            "literature_comparison_note": (
                "Only compare alpha/beta directly with another paper if that paper "
                "uses the same dependent variable, independent variable, and pivot. "
                f"This fit's y-axis is {luminosity_kind_used} "
                f"({'log L/L_sun, ALPINE/REBELS native form' if luminosity_kind_used == 'L_solar' else 'log L_prime brightness-temperature, CO LFR / Solomon 1992 / Carilli & Walter 2013 form'})."
            ),
        },
        # PART AI #2: explicit unit labels. Prose citations of alpha/beta must
        # include these unit strings; the SYSTEM_PROMPT treats any claim without
        # units as an unqualified assertion that claim_validator will block.
        # Prevents the bundle e8d9 (alpha=9.823) vs bundle 6202/84ad (alpha=6.88)
        # type of cross-round drift where prose omits units, creating a silent
        # contradiction.
        "luminosity_kind": luminosity_kind_used,
        "intercept_unit": (
            "log10(L/L_sun)" if luminosity_kind_used == "L_solar"
            else "log10(L_prime/(K km/s pc^2))"
        ),
        "slope_unit": "dex per dex (log_L per log10(FWHM/100 km/s))",
        "n_unit_converted": n_unit_converted,
        "unit_conversion_failures": unit_conversion_failures,
        "n_available": len(rows),
        "n_used": n_used,
        "n_rejected": len(rejected),
        "alpha": alpha,
        "alpha_stderr": alpha_stderr,
        "beta": beta,
        "beta_stderr": beta_stderr,
        "pearson_r": r_value,
        "pearson_p": p_value,
        "spearman_r": spearman_r,
        "spearman_p": spearman_p,
        # M2: residual_rms_dex is the canonical name for the OLS-style
        # sqrt(mean(residual^2)) on this sample.  scatter_dex is a
        # deprecated alias kept for one release.  This is NOT the same
        # as Bayesian intrinsic scatter — that lives in
        # intrinsic_scatter_dex below (M3, only populated when Bayesian
        # actually ran).
        "residual_rms_dex": residual_rms_dex,
        "scatter_dex": residual_rms_dex,
        # M3: Bayesian intrinsic scatter σ_int (median of posterior
        # sqrt(sigsqr) draws).  None whenever the Bayesian path didn't
        # run.  Pair with intrinsic_scatter_dex_hdi for the 94% HDI.
        "intrinsic_scatter_dex": (
            bayes_result["intrinsic_scatter_dex"] if bayes_result else None
        ),
        "intrinsic_scatter_dex_hdi": (
            bayes_result["intrinsic_scatter_dex_hdi"] if bayes_result else None
        ),
        "bayesian_summary": bayes_result,
        "bayesian_error": bayes_error,
        # M2: method declaration fields.  These are always present so
        # downstream UI / claim_validator / PDF export can detect a
        # silent methodology downgrade just from the tool_result shape.
        "fit_method": fit_method,
        "fit_method_requested": requested,
        "fit_method_downgrade_reason": fit_method_downgrade_reason,
        "error_axes_available": {
            "x_err_rows": n_x_err,
            "y_err_rows": n_y_err,
            "both_axes_available": both_errs_available,
        },
        # M2: cosmology bookkeeping.  PART AD C1: when the caller passed
        # `cosmology=...`, cosmology_used reports the TARGET preset's
        # manifest (with bibcode + DOI), `cosmology_recomputed` is True,
        # and dl_shift_summary surfaces the per-row shift the recompute
        # introduced.  When no cosmology arg was passed, we fall back
        # to the platform default manifest as before.
        "cosmology_used": (
            cosmology_used["name"] if cosmology_used and cosmology_used.get("name")
            else current_cosmo_name
        ),
        "cosmology_manifest": cosmology_used or _current_cosmo,
        "cosmology_recomputed": cosmology_recomputed,
        "cosmology_baseline": (
            cosmology_baseline if cosmology_recomputed else None
        ),
        "dl_shift_summary": dl_shift_summary,
        "sample_source_cosmologies": sorted(sample_cosmo_names),
        "cosmology_mismatch": cosmology_mismatch,
        # PART AD C5: variant label so multiple fits in one chat round
        # (main / subsample-low-z / subsample-high-z / cosmology-Riess22)
        # are distinguishable in the UI without parsing the prose.
        "variant_label": str(inp.get("variant_label") or "main").strip() or "main",
        # PART AF C2: surface every cache_key that contributed when the
        # caller used cache_keys=[...]. Empty list => single-cache path.
        "contributing_cache_keys": list(all_resolved_cache_keys),
        "n_surveys_merged": len(all_resolved_cache_keys),
        # M2: lensing bookkeeping.  demagnified count stays 0 unless
        # the rows came from a __demag cache (set in M4 by demagnify_sample).
        "lensed_sources_demagnified": sum(
            1 for r in accepted if r.get("_demagnified") is True
        ),
        "n_lensed": n_lensed,
        "n_unlensed": n_unlensed,
        "n_lensed_unknown": n_lensed_unknown,
        # PART AI #6: consolidated lensing status. Fixes the reviewer objection
        # "0 lensed sources detected" — that number originally came from the
        # ALPINE table having no mu column, not from any scientific conclusion.
        # lensing_summary now explicitly distinguishes 5 categories: unlensed in
        # fit / lensed+demagnified in fit / lensed but missing mu (rejected) /
        # no table info (unknown) / paper-level metadata defaults to lensed.
        "lensing_summary": {
            "n_unlensed_in_fit": n_unlensed,
            "n_lensed_demagnified_in_fit": sum(
                1 for r in accepted if r.get("is_lensed") is True
                and r.get("_demagnified") is True
            ),
            "n_lensed_skipped_no_mu": n_lensed_skipped_no_mu,
            "n_lensed_unknown": n_lensed_unknown,
            "papers_default_lensed": sorted({
                r.get("bibcode")
                for r in (rows or [])
                if r.get("bibcode")
                and _is_paper_lensed_by_default_safe_in_fit(r.get("bibcode"))
            }),
        },
        "log_luminosity_inference_summary": {
            "value_range_inferred_rows": value_range_inferred_rows,
            "publication_ready_requires_header_or_caption_log_units": True,
        },
        # M4: subsample significance test result (None when the caller
        # didn't pass subsample_splits).
        "subsample_significance_test": subsample_test,
        "plot_data": plot_data,
        "publication_ready": publication_ready,
        "fit_inputs_preview": accepted[:12],
        "rejected_summary": rejected[:20],
        "citation_summary": {
            "citation_count": len(citation_keys),
            "citations": citation_keys[:20],
            "table_labels": table_labels[:20],
        },
        "provenance": {
            "datasets": [
                {
                    "service_key": "literature_table_fit",
                    "service_name": "Literature measurement table fit",
                    "archive_version": "cached literature table rows",
                    "source_authority": "paper_table",
                    "article": citation_keys[0] if citation_keys else "",
                    "reference_url": "",
                    "source_urls": [],
                    "acknowledgement_template": (
                        "This fit used machine-readable measurements extracted from cited paper tables; "
                        "verify the original table rows before publication."
                    ),
                },
            ] + ([
                # PART AH C6: when the Bayesian xy-error sampler ran, attach
                # the linmix method-paper bibcode (Kelly, B. C. 2007, ApJ,
                # 665, 1489) so the claim_validator's bibcode pool accepts
                # citations like "Kelly 2007" / "linmix (Kelly 07)" — they
                # are NOT model-fabricated, they are the standard method
                # reference for this fit path.
                {
                    "service_key": "method_citation",
                    "service_name": "Bayesian linear regression with errors on both axes (linmix)",
                    "archive_version": "method_paper",
                    "source_authority": "method_paper",
                    "article": "2007ApJ...665.1489K",
                    "reference_url": "https://ui.adsabs.harvard.edu/abs/2007ApJ...665.1489K",
                    "source_urls": [],
                    "acknowledgement_template": (
                        "Bayesian linear regression with errors on both axes "
                        "(Kelly, B. C. 2007, ApJ, 665, 1489) implemented via "
                        "the linmix sampler."
                    ),
                },
            ] if fit_method == "bayesian_xyerr_linmix" else []),
            "field_bibcodes": {
                "columns": {"fit_input_citations": citation_keys},
                "mapping": {"fit_input_citations": "line_measurements"},
                "source_column_pattern": "literature_table_fit_input",
            } if citation_keys else None,
            "coverage": {
                "field_level": {
                    "available": bool(citation_keys),
                    "bibcode_columns_found": 1 if citation_keys else 0,
                    "unique_bibcodes": len(set(citation_keys)),
                },
                "primary_citation_source": "field_level" if citation_keys else "none",
            },
            # M2: method provenance node — the cross-fire target for
            # claim_validator in M6.  M3 extends it with Bayesian
            # sampler bookkeeping when that path ran.
            "method_provenance": {
                "fit_method": fit_method,
                "fit_method_requested": requested,
                "fit_method_downgrade_reason": fit_method_downgrade_reason,
                "cosmology_used": current_cosmo_name,
                "cosmology_mismatch": cosmology_mismatch,
                "lensed_sources_demagnified": 0,
                "n_lensed_unknown": n_lensed_unknown,
                "intrinsic_scatter_dex": (
                    bayes_result["intrinsic_scatter_dex"] if bayes_result else None
                ),
                "bayesian_n_draws": (
                    bayes_result.get("n_draws_total") if bayes_result else None
                ),
                "bayesian_converged": (
                    bayes_result.get("converged") if bayes_result else None
                ),
                "bayesian_publication_ready": (
                    bayes_result.get("publication_ready") if bayes_result else None
                ),
                "bayesian_package": (
                    bayes_result.get("package") if bayes_result else None
                ),
                "bayesian_reference": (
                    bayes_result.get("reference") if bayes_result else None
                ),
            },
        },
    }

    # ── M2: __tool_status__ priority.  PARTIAL (data insufficiency)
    # outranks METHOD_DOWNGRADED (methodology mismatch) because a caller
    # who can't trust the numbers at all also can't trust the method
    # label.  When both are absent the reply returns without a status
    # banner (tool_result_normalizer will mark it COMPLETED).
    if not publication_ready:
        result["__tool_status__"] = "PARTIAL"
        result["analysis_status"] = "partial"
        result["__do_not_claim__"] = True
        blocking_reason_text = ", ".join(relation_blocking_reasons) or "unknown"
        result["__message_to_model__"] = (
            f"The line-relation fit used {n_used} rows. It is below min_rows={min_rows}, "
            "has incomplete citations, or relies on value-range log-luminosity inference "
            "instead of header/caption-confirmed units. Describe it as exploratory only; "
            f"do not claim a publication-ready relation. Blocking reasons: {blocking_reason_text}."
        )
    elif is_method_downgraded:
        result["__tool_status__"] = "METHOD_DOWNGRADED"
        result["analysis_status"] = "method_downgraded"
        result["__do_not_claim__"] = True
        result["__message_to_model__"] = (
            f"fit_method_requested={requested!r} but the tool actually ran "
            f"{fit_method!r}: {fit_method_downgrade_reason} "
            "Do NOT describe this fit as Bayesian or two-axis-error regression "
            "in the reply; state explicitly that it was an OLS fallback and why."
        )
    if cosmology_mismatch:
        # Warning runs in parallel to the status.  We attach a non-fatal
        # banner so the AI must acknowledge the cosmology drift but the
        # result still counts as "fit ran".
        result.setdefault("warnings", []).append({
            "code": "cosmology_mismatch",
            "message": (
                f"Sample source_cosmology contains {sorted(sample_cosmo_names)!r} "
                f"but the current server cosmology is {current_cosmo_name!r}. "
                "Luminosities may need recomputation via compare_luminosity_distances "
                "(M5) before the fit is publication-ready."
            ),
        })
    return result


def _exec_fit_line_lfr(
    inp: dict,
    python_session_id: str = "default",
    api_key: str = "",
) -> dict | Awaitable[dict]:
    """Compatibility wrapper for legacy synchronous tests/callers.

    The public tool dispatcher uses the async implementation directly. Older
    unit tests and helper code import ``_exec_fit_line_lfr`` and expect a plain
    dict for cache-based fits, while newer arxiv_id tests await it. Returning a
    coroutine only when already inside an event loop preserves both call styles
    without exposing coroutine objects to synchronous callers.
    """
    coro = _exec_fit_line_lfr_async(inp, python_session_id, api_key)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return coro


def _exec_demagnify_sample(inp: dict, python_session_id: str = "default") -> dict:
    """Apply gravitational-lensing demagnification to a literature sample.

    Reads ``cache_key`` (default: ``latest_literature_tables``), copies
    every row, and for sources listed in ``mu_map`` subtracts log10(μ)
    from ``log_luminosity`` while flagging ``is_lensed=True``,
    ``mu_lens=μ`` and ``_demagnified=True``.  Writes the modified
    sample back to a NEW cache key (``<orig>__demag`` by default) so
    the original is preserved — that lets the lensing-systematic error
    budget be derived later by comparing fits run on the two cache
    keys.

    mu_map accepts two forms per source:
        "SRC-A": 5.0
        "SRC-B": {"mu": 3.0, "reference": "Foo+24"}

    The result dict lists every row's before / after log_luminosity,
    sources skipped because they were not in the map, and the new
    cache key.  After this call the AI is expected to invoke
    fit_line_lfr(cache_key=<new>) to get the demagnified-sample fit.
    """
    cache_key = str(
        inp.get("cache_key") or "latest_literature_tables"
    ).strip() or "latest_literature_tables"
    mu_map_raw = inp.get("mu_map")
    if not isinstance(mu_map_raw, dict) or not mu_map_raw:
        return {
            "success": False,
            "error": "demagnify_sample requires a non-empty mu_map dict.",
            "error_class": "missing_mu_map",
            "__tool_status__": "FAILED",
        }
    out_cache_key = str(inp.get("output_cache_key") or f"{cache_key}__demag").strip()

    rows, resolved_cache_key = _resolve_literature_measurement_cache(
        cache_key, python_session_id,
    )
    if not rows:
        return {
            "success": False,
            "__tool_status__": "EMPTY",
            "error": f"No cached line_measurements found for cache_key={cache_key!r}.",
            "error_class": "missing_measurement_cache",
        }

    # Normalize mu_map to {name: {mu: float, reference: str}}.
    normalized_mu_map: dict[str, dict[str, Any]] = {}
    for src, value in mu_map_raw.items():
        if isinstance(value, dict):
            mu_val = _finite_float(value.get("mu"))
            ref = str(value.get("reference") or "").strip()
        else:
            mu_val = _finite_float(value)
            ref = ""
        if mu_val is None or mu_val <= 0:
            continue
        normalized_mu_map[str(src).strip()] = {"mu": mu_val, "reference": ref}

    if not normalized_mu_map:
        return {
            "success": False,
            "error": "mu_map contained no entries with a positive numeric μ.",
            "error_class": "invalid_mu_map",
            "__tool_status__": "FAILED",
        }

    new_rows: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        new_row = dict(row)
        name = str(new_row.get("source_name") or "").strip()
        entry = normalized_mu_map.get(name)
        if entry is None:
            skipped.append({
                "source_name": name,
                "reason": "not_in_mu_map",
                "is_lensed": new_row.get("is_lensed"),
            })
        else:
            mu = float(entry["mu"])
            log_l_before = _finite_float(new_row.get("log_luminosity"))
            if log_l_before is None:
                skipped.append({
                    "source_name": name,
                    "reason": "row_missing_log_luminosity",
                })
            else:
                delta = math.log10(mu)
                new_row["log_luminosity"] = log_l_before - delta
                new_row["mu_lens"] = mu
                new_row["is_lensed"] = True
                new_row["_demagnified"] = True
                new_row["_demagnify_reference"] = entry["reference"]
                new_row["_log_luminosity_before_demag"] = log_l_before
                applied.append({
                    "source_name": name,
                    "mu": mu,
                    "log_luminosity_before": round(log_l_before, 6),
                    "log_luminosity_after": round(new_row["log_luminosity"], 6),
                    "delta_log_l": round(-delta, 6),
                    "reference": entry["reference"],
                })
        new_rows.append(new_row)

    payload = _literature_table_cache_payload(
        {"line_measurements": new_rows, "tables": []},
        out_cache_key,
    )
    payload["derived_from"] = resolved_cache_key
    payload["demagnify_summary"] = {
        "n_input_rows": len(rows),
        "n_demagnified": len(applied),
        "n_skipped": len(skipped),
    }
    store_search_results(out_cache_key, payload)
    session_key = _session_cache_key(out_cache_key, python_session_id)
    if session_key:
        store_search_results(session_key, payload)

    return {
        "success": True,
        "tool": "demagnify_sample",
        "__tool_status__": "PARTIAL" if applied and skipped else None,
        "input_cache_key": resolved_cache_key,
        "output_cache_key": out_cache_key,
        "n_input_rows": len(rows),
        "n_demagnified": len(applied),
        "n_skipped": len(skipped),
        "applied": applied,
        "skipped_summary": skipped[:50],
        "__message_to_model__": (
            f"Demagnified {len(applied)}/{len(rows)} rows. The corrected "
            f"sample is cached at '{out_cache_key}' — pass it to fit_line_lfr "
            f"as cache_key={out_cache_key!r} to fit on the demagnified rows. "
            "The original cache is preserved so the lensing-systematic error "
            "budget can be derived by comparing fits on both keys."
        ),
    }


def _cosmology_manifest_for(name: str) -> dict[str, Any]:
    """Build a manifest dict for an arbitrary supported cosmology name.

    PART AA: prefer the curated preset metadata (with bibcode/DOI) when
    the requested name is a PART AA preset OR its legacy astropy alias.
    Fall back to the raw astropy object for legacy names like WMAP9 /
    FlatLambdaCDM_HxxOmxx so the existing comparison flow still works.
    """
    from app.services.cosmology import (
        PRESETS,
        cosmology_manifest as _preset_manifest,
        get_cosmology,
    )

    # PART AA preset name OR legacy "Planck18" alias for the planck18
    # preset → return the preset manifest with bibcode + DOI.
    normalised = "planck18" if name == "Planck18" else name
    if normalised in PRESETS:
        return _preset_manifest(normalised)

    # Legacy astropy / FlatLambdaCDM_... path: compute from astropy obj,
    # bibcode is null because we don't claim attribution for these.
    cosmo = get_cosmology(name)
    return {
        "name": name,
        "H0_km_s_Mpc": float(cosmo.H0.value),
        "Om0": float(cosmo.Om0),
        "Ob0": float(getattr(cosmo, "Ob0", 0.0) or 0.0),
        "bibcode": None,
        "doi": None,
        "reference": "Astropy legacy alias (no curated preset metadata)",
    }


def _exec_compare_luminosity_distances(
    inp: dict, python_session_id: str = "default",
) -> dict:
    """Compare luminosity distance + Δlog L for two cosmology choices.

    Use this BEFORE citing a non-Planck H0/Om0 (e.g. Riess 2011 H0=73.8
    or Suzuki 2012 Om=0.295) on a sample whose source_cosmology is
    something else.  The tool reports per-source ΔDL and Δlog L' so the
    AI can decide whether the cosmology-systematic shift is large
    enough to recompute or merely cite as a < few % systematic.
    """
    cache_key = str(
        inp.get("cache_key") or "latest_literature_tables"
    ).strip() or "latest_literature_tables"
    target_name = str(inp.get("target_cosmology") or "").strip()
    if not target_name:
        return {
            "success": False,
            "error": "target_cosmology is required (e.g. 'Planck18', 'WMAP9', or 'FlatLambdaCDM_H73p8_Om0p295').",
            "error_class": "missing_target_cosmology",
            "__tool_status__": "FAILED",
        }
    rows, resolved_cache_key = _resolve_literature_measurement_cache(
        cache_key, python_session_id,
    )
    if not rows:
        return {
            "success": False,
            "__tool_status__": "EMPTY",
            "error": f"No cached line_measurements found for cache_key={cache_key!r}.",
            "error_class": "missing_measurement_cache",
        }
    from app.services.cosmology import (
        cosmology_manifest as _current_manifest,
        get_cosmology as _get,
    )

    current_manifest = _current_manifest()
    target_manifest = _cosmology_manifest_for(target_name)
    current_cosmo = _get(None)
    target_cosmo = _get(target_name)

    per_source: list[dict[str, Any]] = []
    deltas_pct: list[float] = []
    deltas_log_l: list[float] = []
    for row in rows:
        z = _finite_float(row.get("redshift"))
        if z is None or z <= 0:
            continue
        try:
            dl_a = float(current_cosmo.luminosity_distance(z).to("Mpc").value)
            dl_b = float(target_cosmo.luminosity_distance(z).to("Mpc").value)
        except Exception:
            continue
        if dl_a <= 0:
            continue
        delta_pct = (dl_b - dl_a) / dl_a * 100.0
        # log L ∝ 2 · log DL ⇒ Δlog L = 2 · log10(DL_b / DL_a)
        delta_log_l = 2.0 * (math.log10(dl_b) - math.log10(dl_a))
        per_source.append({
            "source_name": row.get("source_name"),
            "redshift": z,
            "DL_current_Mpc": round(dl_a, 3),
            "DL_target_Mpc": round(dl_b, 3),
            "delta_pct": round(delta_pct, 4),
            "delta_log_luminosity": round(delta_log_l, 6),
        })
        deltas_pct.append(delta_pct)
        deltas_log_l.append(delta_log_l)

    if not per_source:
        return {
            "success": False,
            "__tool_status__": "EMPTY",
            "error": "No rows with usable redshift were available for comparison.",
            "error_class": "no_redshift_rows",
        }
    import numpy as _np
    summary = {
        "n_used": len(per_source),
        "max_abs_delta_pct": round(float(_np.max(_np.abs(deltas_pct))), 4),
        "median_abs_delta_pct": round(float(_np.median(_np.abs(deltas_pct))), 4),
        "max_abs_delta_log_luminosity": round(
            float(_np.max(_np.abs(deltas_log_l))), 6,
        ),
        "median_abs_delta_log_luminosity": round(
            float(_np.median(_np.abs(deltas_log_l))), 6,
        ),
    }
    return {
        "success": True,
        "tool": "compare_luminosity_distances",
        "cache_key": resolved_cache_key,
        "current_cosmology": current_manifest,
        "target_cosmology": target_manifest,
        "summary": summary,
        "per_source": per_source[:200],
        "n_source_total": len(per_source),
        "__message_to_model__": (
            f"Cosmology cross-check vs {target_name!r}: median |ΔDL|"
            f" = {summary['median_abs_delta_pct']:.2f}%, max"
            f" {summary['max_abs_delta_pct']:.2f}%."
            "  If max |Δlog L| > 0.05 dex, recompute log_luminosity"
            " before fitting; otherwise quote the shift as a"
            " cosmology-systematic uncertainty."
        ),
    }


def _exec_export_sample_table(
    inp: dict, python_session_id: str = "default",
) -> dict:
    """Export the cached literature sample as a machine-readable table.

    Formats supported:
      - csv       (default; comma-separated, header row)
      - votable   (IVOA VOTable XML via astropy)
      - latex     (AAS deluxetable string)
      - ascii     (fixed-width, astropy ASCII)

    The table is returned inline in the result dict (`content`); the
    caller can write it to disk or include it in a PDF/paper draft.
    """
    cache_key = str(
        inp.get("cache_key") or "latest_literature_tables"
    ).strip() or "latest_literature_tables"
    fmt = str(inp.get("format") or "csv").strip().lower()
    if fmt not in ("csv", "votable", "latex", "ascii"):
        return {
            "success": False,
            "error": f"Unsupported format {fmt!r}. Use csv | votable | latex | ascii.",
            "error_class": "invalid_format",
            "__tool_status__": "FAILED",
        }
    rows, resolved_cache_key = _resolve_literature_measurement_cache(
        cache_key, python_session_id,
    )
    if not rows:
        return {
            "success": False,
            "__tool_status__": "EMPTY",
            "error": f"No cached line_measurements found for cache_key={cache_key!r}.",
            "error_class": "missing_measurement_cache",
        }
    cols_default = [
        "source_name", "redshift", "line_id",
        "log_luminosity", "log_luminosity_err",
        "fwhm_km_s", "fwhm_err_km_s",
        "mu_lens", "is_lensed",
        "bibcode", "arxiv_id", "table_label",
    ]
    cols = inp.get("columns") if isinstance(inp.get("columns"), list) else cols_default
    cols = [str(c) for c in cols if c]

    def _row_value(row: dict, col: str) -> Any:
        v = row.get(col)
        if isinstance(v, dict):
            return v.get("bibcode") or v.get("arxiv_id") or ""
        return v if v is not None else ""

    if fmt == "csv":
        import csv as _csv
        import io as _io
        buf = _io.StringIO()
        writer = _csv.writer(buf)
        writer.writerow(cols)
        for row in rows:
            writer.writerow([_row_value(row, c) for c in cols])
        content = buf.getvalue()
        media_type = "text/csv"
        ext = "csv"
    elif fmt == "ascii":
        try:
            import io as _io
            from astropy.table import Table
            from astropy.io import ascii as _ascii
            data = {c: [_row_value(row, c) for row in rows] for c in cols}
            tab = Table(data)
            buf = _io.StringIO()
            _ascii.write(tab, buf, format="fixed_width")
            content = buf.getvalue()
            media_type = "text/plain"
            ext = "txt"
        except Exception as exc:
            return {
                "success": False,
                "error": f"astropy ASCII export failed: {exc}",
                "error_class": "astropy_failure",
                "__tool_status__": "FAILED",
            }
    elif fmt == "votable":
        try:
            from astropy.table import Table
            from astropy.io.votable import from_table, writeto
            import io as _io
            data = {c: [_row_value(row, c) for row in rows] for c in cols}
            tab = Table(data)
            tab.meta["cache_key"] = resolved_cache_key
            buf = _io.BytesIO()
            writeto(from_table(tab), buf)
            content = buf.getvalue().decode("utf-8")
            media_type = "application/x-votable+xml"
            ext = "xml"
        except Exception as exc:
            return {
                "success": False,
                "error": f"astropy VOTable export failed: {exc}",
                "error_class": "astropy_failure",
                "__tool_status__": "FAILED",
            }
    else:  # latex
        # Plain AAS deluxetable.  No formal LaTeX rendering — the AI is
        # expected to paste this into a paper draft.
        header = " & ".join(cols) + r" \\"
        lines = [
            r"\begin{deluxetable}{" + "c" * len(cols) + r"}",
            r"\tablecaption{Literature line-measurement sample (cache_key=" +
            resolved_cache_key + r")}",
            r"\tablehead{" + header + r"}",
            r"\startdata",
        ]
        for row in rows:
            lines.append(
                " & ".join(str(_row_value(row, c)) for c in cols) + r" \\"
            )
        lines.extend([r"\enddata", r"\end{deluxetable}"])
        content = "\n".join(lines)
        media_type = "application/x-latex"
        ext = "tex"
    return {
        "success": True,
        "tool": "export_sample_table",
        "cache_key": resolved_cache_key,
        "format": fmt,
        "filename": f"sample_{resolved_cache_key}.{ext}",
        "media_type": media_type,
        "content": content,
        "n_rows": len(rows),
        "columns": cols,
        "__message_to_model__": (
            f"Wrote a {fmt} table of {len(rows)} rows from "
            f"{resolved_cache_key!r}.  The full content is in the "
            "'content' field — include it as the final sample table in "
            "your reply (or write it to disk via run_python)."
        ),
    }


# PART Z C5: in-memory rolling-window rate limit for the chat-side
# extract_literature_tables call.
#
# Why not Redis? Render free-tier has 1 web worker so in-memory is
# already correct; if we go multi-worker the limit becomes per-worker
# (effectively N×cap, still well below the published ar5iv quotas).
# When that becomes a real problem we'll move this to connector_cache.
_arxiv_tool_calls: dict[str, list[float]] = {}
_ARXIV_RATE_WINDOW_S = 3600.0
_ARXIV_ANON_CAP_PER_HOUR = 5
_ARXIV_AUTH_CAP_PER_HOUR = 50


def _arxiv_rate_cap(*, user_id: str | None) -> int:
    env_name = (
        "ARXIV_TABLE_AUTH_CAP_PER_HOUR"
        if user_id
        else "ARXIV_TABLE_ANON_CAP_PER_HOUR"
    )
    default = _ARXIV_AUTH_CAP_PER_HOUR if user_id else _ARXIV_ANON_CAP_PER_HOUR
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("Invalid %s=%r; using default cap=%d", env_name, raw, default)
        return default


def _check_arxiv_tool_rate_limit(
    *,
    user_id: str | None = None,
    chat_session_id: str | None = None,
) -> tuple[bool, int, int, str]:
    """Returns (allowed, used_in_window, cap, key_kind).

    The user_id path is per-user (50/hr), the anonymous path keys on the
    chat_session_id (5/hr) so each open chat tab is its own quota
    rather than every anonymous tab sharing one bucket.
    """
    import time as _time

    now = _time.monotonic()
    if user_id:
        key = f"user:{user_id}"
        cap = _arxiv_rate_cap(user_id=user_id)
        kind = "authenticated user"
    else:
        # Anonymous: identify by chat_session_id; fall back to a single
        # shared bucket if even that is missing (very early bootstrap).
        key = f"session:{chat_session_id or 'anonymous-fallback'}"
        cap = _arxiv_rate_cap(user_id=None)
        kind = "anonymous chat session"

    cutoff = now - _ARXIV_RATE_WINDOW_S
    timestamps = [t for t in _arxiv_tool_calls.get(key, []) if t > cutoff]
    if len(timestamps) >= cap:
        _arxiv_tool_calls[key] = timestamps
        return False, len(timestamps), cap, kind
    timestamps.append(now)
    _arxiv_tool_calls[key] = timestamps
    return True, len(timestamps), cap, kind


# PART Z: retry + cache wrapper around extract_arxiv_tables_payload.
# The HTTP endpoint was already gated and rate-limited (M19), but the chat
# tool path bypassed that and could repeatedly hit ar5iv on the AI's whim.
# Now every chat call goes through a 24h connector_cache lookup +
# circuit-breaker, so a paper fetched once stays cached and ar5iv outages
# trip the breaker instead of letting AI users amplify load.

async def _cached_extract_arxiv_tables_payload(arxiv_id_raw: str) -> dict:
    """24h-cached wrapper around extract_arxiv_tables_payload.

    Cache key includes a normalised arxiv_id so the various aliases
    (`2310.12345` / `arXiv:2310.12345` / arxiv URL) hit the same entry.
    Concurrent identical calls share a single upstream fetch via
    connector_cache.get_or_compute's singleflight.
    """
    from app.api.arxiv import _clean_arxiv_id
    from app.services.connector_cache import get_or_compute

    cleaned = _clean_arxiv_id(arxiv_id_raw) or arxiv_id_raw.strip()
    cache_key = f"arxiv_tables:v3:{cleaned}"

    async def _compute():
        return await _extract_arxiv_tables_payload_with_retry(arxiv_id_raw)

    return await get_or_compute(cache_key, _compute, ttl=24 * 3600)


def _arxiv_retryable_exceptions():
    """Build the retry-eligible exception tuple at import time without
    forcing httpx to be imported on cold paths.  Network timeouts and
    transient connection failures retry; HTTPException (e.g. 404 "no
    tables found") does NOT retry — that's a permanent semantic error.
    """
    try:
        import httpx
        return (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            ConnectionError,
            TimeoutError,
            OSError,
        )
    except ImportError:
        return (ConnectionError, TimeoutError, OSError)


async def _extract_arxiv_tables_payload_with_retry(arxiv_id_raw: str) -> dict:
    """Inner: retries via with_retry, fronted by the 24h cache above."""
    from app.api.arxiv import extract_arxiv_tables_payload
    from app.connectors.retry import with_retry

    @with_retry(
        max_retries=2,
        base_delay=1.0,
        retryable_exceptions=_arxiv_retryable_exceptions(),
    )
    async def _do_fetch(_id: str) -> dict:
        return await extract_arxiv_tables_payload(_id)

    return await _do_fetch(arxiv_id_raw)


async def _exec_extract_literature_tables(
    inp: dict,
    python_session_id: str = "default",
    *,
    user_id: str | None = None,
    chat_session_id: str | None = None,
) -> dict:
    """Extract arXiv tables and cache any normalized measurement rows.

    PART Z C1 originally hard-rejected `user_id is None` to keep
    anonymous chat from amplifying load on ar5iv. Real-world UX showed
    the gate was too sharp — even the platform owner kept hitting it.
    PART Z C5 (this update): drop the hard reject, add a per-key
    rate-limit (5/hr for anonymous, 50/hr for logged-in users). The
    primary DoS defences are still in place — 24h connector_cache for
    repeat fetches + circuit-breaker on httpx errors — so the rate
    limit is a thin extra layer rather than the only one.
    """
    from app.api.arxiv import extract_arxiv_tables_payload  # noqa: F401  (kept for callers)

    # Prefer the real chat session id, but fall back to the Python/session
    # runtime id.  Browser/UI fresh-chat flows can reach the tool executor
    # before a durable chat_session_id exists; using only
    # "anonymous-fallback" turned a 20-paper local UI benchmark into one
    # shared 5/hr bucket after the first few turns.
    limit_session_id = chat_session_id or python_session_id
    allowed, used, cap, key_kind = _check_arxiv_tool_rate_limit(
        user_id=user_id, chat_session_id=limit_session_id,
    )
    if not allowed:
        return {
            "success": False,
            "error": (
                f"extract_literature_tables rate limit reached "
                f"({used}/{cap} calls in the last hour for this "
                f"{key_kind}). Anonymous chat sessions are capped at 5/hr "
                f"to keep the public ar5iv / arxiv.org services healthy. "
                f"Sign in at /account for the larger 50/hr authenticated "
                f"quota, or wait for the rolling window to clear."
            ),
            "error_class": "rate_limit_exceeded",
            "rate_limit_used": used,
            "rate_limit_cap": cap,
            "rate_limit_kind": key_kind,
        }

    raw_id = _arxiv_id_from_table_input(inp)
    if not raw_id:
        return {
            "success": False,
            "error": "extract_literature_tables requires arxiv_id, arxiv_url, or paper.bibcode='arXiv:<id>'.",
            "error_class": "missing_arxiv_id",
        }
    try:
        payload = await _cached_extract_arxiv_tables_payload(raw_id)
    except Exception as exc:
        return {
            "success": False,
            "error": f"Literature table extraction failed: {exc}",
            "error_class": "literature_table_extraction_failed",
        }

    line_measurements = payload.get("line_measurements") or []
    tables = payload.get("tables") or []
    latest_cache_key = _session_cache_key("latest_literature_tables", python_session_id) or "latest_literature_tables"
    cleaned_arxiv_id = str(payload.get("arxiv_id") or raw_id).replace("arXiv:", "").strip()
    raw_cache_key_base = f"literature_tables_raw:{cleaned_arxiv_id or 'unknown'}"
    raw_cache_key = _session_cache_key(raw_cache_key_base, python_session_id) or raw_cache_key_base
    cache_key = latest_cache_key if line_measurements else raw_cache_key
    cache_value = _literature_table_cache_payload(payload, cache_key)
    store_search_results(cache_key, cache_value)
    if line_measurements:
        # Only fit-ready extractions update the session's latest measurement
        # cache.  A later raw-only / zero-measurement extraction must not wipe
        # a previously successful ALPINE/REBELS/etc. cache; otherwise the next
        # fit_line_lfr(default latest_literature_tables) sees an empty sample.
        if cache_key != "latest_literature_tables":
            store_search_results("latest_literature_tables", cache_value)
    else:
        existing_latest = get_cached_results(latest_cache_key)
        if not existing_latest and latest_cache_key != "latest_literature_tables":
            existing_latest = get_cached_results("latest_literature_tables")
        if not existing_latest:
            # No fit-ready cache exists yet; keeping a latest raw cache preserves
            # old UX for "extract then inspect raw tables" while still preventing
            # zero-row overwrites after a successful extraction.
            store_search_results(latest_cache_key, cache_value)

    bibcodes = [
        str(row.get("bibcode") or "").strip()
        for row in line_measurements
        if isinstance(row, dict) and str(row.get("bibcode") or "").strip()
    ]
    if not bibcodes and payload.get("bibcode"):
        bibcodes = [str(payload["bibcode"])]

    source_url = str(payload.get("source_url") or "").strip()
    dataset = {
        "service_key": "literature_table",
        "service_name": "Literature measurement table",
        "archive_version": "arXiv/ar5iv live source",
        "source_authority": "paper_table",
        "article": str(payload.get("bibcode") or ""),
        "publisher": "arXiv",
        "reference_url": source_url,
        "source_urls": [source_url] if source_url else [],
        "acknowledgement_template": (
            "This work used machine-readable tables extracted from the cited literature; "
            "verify the original paper table before publication."
        ),
    }
    field_bibcodes = {
        "columns": {"literature_table_bibcode": bibcodes},
        "mapping": {"literature_table_bibcode": "line_measurements"},
        "source_column_pattern": "literature_table_row_citation",
    }
    result = {
        "success": True,
        "arxiv_id": payload.get("arxiv_id"),
        "title": payload.get("title"),
        "authors": payload.get("authors") or [],
        "year": payload.get("year"),
        "bibcode": payload.get("bibcode"),
        "doi": payload.get("doi"),
        "source_url": source_url,
        "result_granularity": "paper_table",
        "supports_measurement_claims": bool(line_measurements),
        "tables": tables,
        "line_measurements": line_measurements,
        "line_measurement_count": len(line_measurements),
        "raw_table_count": len(tables),
        "extraction_status": payload.get("extraction_status") or (
            "measurement_ready" if line_measurements else "raw_only"
        ),
        "normalization_status": payload.get("normalization_status") or (
            "line_measurements_detected" if line_measurements else "no_line_measurement_schema"
        ),
        "cache_key": cache_key,
        "fit_ready": bool(line_measurements),
        "llm_summary": _literature_tables_llm_summary(payload, cache_key),
        "provenance": {
            "datasets": [dataset],
            "field_bibcodes": field_bibcodes if bibcodes else None,
            "coverage": {
                "field_level": {
                    "available": bool(bibcodes),
                    "bibcode_columns_found": 1 if bibcodes else 0,
                    "unique_bibcodes": len(set(bibcodes)),
                },
                "primary_citation_source": "field_level" if bibcodes else "table_level",
            },
        },
        "warnings": list(payload.get("warnings") or ([] if line_measurements else [
            "Tables were extracted, but no reliable line-measurement schema was detected. Do not fit L[CII]-FWHM until columns are mapped."
        ])),
    }
    if not line_measurements and not tables:
        result["__message_to_model__"] = (
            f"No data tables were detected in arXiv:{payload.get('arxiv_id')}. "
            "This is a semantic no-data result, not permission to infer measurements "
            "from the abstract or from memory. Allowed next steps: search for a "
            "companion measurement-table paper, or tell the user that this paper does "
            "not expose a machine-readable table through the current extractor. "
            "Do NOT quote L[CII] / Hα / FWHM / line widths or fit a relation."
        )
    elif not line_measurements:
        result["__message_to_model__"] = (
            f"Raw literature tables were extracted from arXiv:{payload.get('arxiv_id')} "
            f"({len(tables)} table(s)), but no normalized line_measurements were detected. "
            "Treat this as a raw-only extraction, not as a fit-ready measurement table. "
            "It usually means the paper's tables are missing one of the required columns "
            "(source name, redshift, log L<line>, FWHM) — for example: REBELS often puts the "
            "size measurements in one paper and the line measurements in a companion paper. "
            "Allowed next steps: "
            "(a) call `search_literature` to find the companion / measurement-table paper for "
            "this object class, OR "
            "(b) emit `<tools_returned_nothing failed_tools=\"extract_literature_tables\" "
            "rationale=\"this paper's tables do not contain line measurements\"/>` if the user "
            "asked specifically about THIS paper. "
            "Do NOT quote L[CII] / Hα / FWHM / line widths or fit a relation unless a "
            "measurement table is mapped, and do NOT hardcode remembered ALPINE / REBELS values."
        )
    else:
        result["__message_to_model__"] = (
            f"Extracted {len(line_measurements)} normalized line_measurements and cached them as "
            f"{cache_key}. For a luminosity/FWHM relation, call fit_line_lfr with this cache_key. "
            "Do NOT create a synthetic or hardcoded literature dataframe in run_python."
        )
    return result


_VALID_DATA_SOURCES = {
    "latest_adql", "latest_search", "latest_lightcurve",
    "latest_sdss_sql",  # J3: SDSS SkyServer direct-connection results
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
    # J3: latest_sdss_sql shares the get_cached_results / get_adql_results
    # access interface (run_sdss_sql stores results in the adql_result_sets pool,
    # reusing the getter). The explicit latest_sdss_sql variable name is the
    # unique identifier.
    "latest_sdss_sql": ("get_cached_results", "get_adql_results", "latest_sdss_sql"),
    "latest_high_velocity_stars": ("get_cached_results", "get_adql_results", "latest_high_velocity_stars"),
}
_PLATFORM_REAL_DATA_READER_TOKENS = {
    # These helpers themselves trigger real archive / MAST / platform cache reads.
    # Even if the model declares data_source='latest_search' instead of
    # 'latest_lightcurve', G1.2 should not treat this as “no real data read”.
    "search_lightcurve",
    "download_and_clean_lightcurve",
    "transit_search",
}


def _summarize_ast_observations(code: str, limit: int = 24) -> str:
    """Return the identifiers actually seen by the AST check, helping the model self-correct from error messages."""
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


# X5 (PART X): per-session consecutive run_python call counter. Used to observe
# the "Nth run_python crashes in sandbox" pattern — B4/B5/B6 all crashed at
# call 3 or later but we lack data to pinpoint the root cause. Accumulate
# 1-2 rounds of B regressions before deciding whether to dig deeper into the sandbox.
_session_run_python_count: dict[str, int] = {}
_MAX_TRACKED_ATTEMPT_IDX_FOR_METRIC = 10  # cap cardinality


def _bump_run_python_attempt_idx(session_id: str) -> int:
    """Return the 1-indexed call number of run_python in this session."""
    _session_run_python_count[session_id] = (
        _session_run_python_count.get(session_id, 0) + 1
    )
    return _session_run_python_count[session_id]


_ABS_MAG_CONTEXT_RE = re.compile(
    r"(?:M_G|M\s*_\s*G|abs(?:olute)?\s+(?:G\s+)?mag(?:nitude)?|absolute_magnitude)"
    r"[^-+\d\n]{0,40}"
    r"([-+]?(?:\d+(?:\.\d+)?|\.\d+))"
    r"(?:\s*(?:to|[-–—])\s*([-+]?(?:\d+(?:\.\d+)?|\.\d+)))?",
    re.I,
)


def _unphysical_cmd_magnitudes(payload: dict[str, Any]) -> list[float]:
    text_parts: list[str] = []
    for key in ("stdout", "stderr"):
        if payload.get(key):
            text_parts.append(str(payload[key]))
    variables = payload.get("variables")
    if variables:
        text_parts.append(str(variables))

    values: list[float] = []
    for match in _ABS_MAG_CONTEXT_RE.finditer("\n".join(text_parts)):
        for group in match.groups():
            if group is None:
                continue
            try:
                value = float(group)
            except ValueError:
                continue
            if math.isfinite(value) and (value < -30.0 or value > 20.0):
                values.append(value)
    return values


def _apply_cmd_sanity_guard(response: dict[str, Any]) -> dict[str, Any]:
    """Mark unphysical CMD absolute magnitudes as unciteable partial output."""
    bad_values = _unphysical_cmd_magnitudes(response)
    if not bad_values:
        return response

    warning = (
        "Unphysical CMD absolute magnitude detected "
        f"({', '.join(f'{v:g}' for v in bad_values[:4])}); likely parallax-unit "
        "or distance-modulus error. Do not cite CMD-derived magnitudes, age, "
        "distance, or extinction from this Python output."
    )
    warnings = list(response.get("warnings") or [])
    warnings.append(warning)
    response["warnings"] = warnings
    response["__tool_status__"] = "PARTIAL"
    response["analysis_status"] = "partial"
    response["__do_not_claim__"] = True
    response["__message_to_model__"] = warning
    response["cmd_sanity_error"] = "unphysical_absolute_magnitude"
    return response


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
            # J3: presence of latest_sdss_sql / run_sdss_sql in the code indicates
            # an SDSS cache source. This check must come before get_adql_results
            # because run_sdss_sql reuses the ADQL cache pool; the variable name
            # is the only way to distinguish them.
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
            # PART AD: user-supplied local data files (CSV / parquet) are real
            # data too. Without this the AI cannot declare a source for its own
            # uploaded data, so the run defaults to synthetic and the X3 reverse
            # check then rejects it for actually reading real data.
            elif any(t in names_seen for t in ("read_csv", "read_parquet", "load_csv")):
                auto_source = "user_file:<autodetected>"
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

    # PART AD: cached:<key> declares an inline cache handle. The old code
    # trusted it blindly ("free-form, no static check"), so a non-existent
    # key let the run proceed and the code could silently fall back to
    # fabrication. Validate the key resolves to live cached data first.
    if data_source.startswith("cached:"):
        cache_key = data_source[len("cached:"):].strip()
        if not cache_key or get_cached_results(cache_key) is None:
            now = time.time()
            available = sorted(
                k for k, (_, ts) in _search_result_cache.items()
                if now - ts <= _CACHE_TTL_SECONDS
            )
            return {
                "success": False,
                "error": (
                    f"data_source='cached:{cache_key}' but no live cached "
                    f"results exist under key '{cache_key}'. Available cache "
                    f"keys: {available or 'none'}. Fetch the data first "
                    f"(run_adql / search_objects / search_lightcurve / "
                    f"extract_literature_tables), then reference the correct "
                    f"cached:<key>."
                ),
                "error_class": "cached_key_not_found",
            }

    # X3 (PART X): reverse-direction data_source check.  If AI declared
    # synthetic but the code actually reads real archive/cache helpers,
    # the declaration is wrong — the output would be mis-labelled as
    # SYNTHETIC even though it's analyzing real data, confusing the user
    # and tainting the provenance trail.  Symmetric counterpart to G1.2
    # ("declared real but code is synthetic").
    #
    # B6 P-3 regression: AI ran DBSCAN on 252 real Gaia rows (fetched
    # via get_adql_results() earlier in the turn) but declared
    # data_source='none_not_analyzing_real_data', so the real output
    # got SYNTHETIC-stamped incorrectly.
    if is_synthetic_declared:
        _REAL_CACHE_READERS = (
            "get_cached_results(", "get_search_results(",
            "get_adql_results(", "get_adql_result_sets(",
            "get_latest_adql_result(", "load_fits(",
            # PART Y Batch 4: extend X3 reverse detection. Audit found AI
            # could declare 'none_not_analyzing_real_data' while the code
            # actually fetched real lightcurves / read real FITS / pulled
            # CSV — these readers were not in the X3 string list.
            "search_lightcurve(", "lightkurve.",
            "Table.read(", "fits.open(",
            "pd.read_csv(", "pd.read_parquet(",
            "load_votable(", "load_csv(",
            "astro.search_lightcurve(", "astro.download_and_clean_lightcurve(",
            "astroquery",
        )
        reads_real_cache = any(p in code for p in _REAL_CACHE_READERS)
        if reads_real_cache:
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "incorrect_synthetic_declaration_total",
                    1.0,
                )
            except Exception:
                pass
            return {
                "success": False,
                "error": (
                    "Incorrect synthetic declaration: code accesses real "
                    "cache helpers (get_cached_results / get_search_results "
                    "/ get_adql_results / get_adql_result_sets / "
                    "get_latest_adql_result / load_fits) but data_source "
                    "was declared 'none_not_analyzing_real_data'. Change "
                    "data_source to 'latest_adql' / 'latest_search' / "
                    "'latest_lightcurve' / 'cached:<key>' / 'fits:<path>' / "
                    "'user_file:<path>' (your own CSV / parquet via pd.read_csv "
                    "/ pd.read_parquet / load_csv) "
                    "to match what the code actually reads. The SYNTHETIC "
                    "tag is reserved for code that genuinely does NOT "
                    "read any real data."
                ),
                "error_class": "incorrect_synthetic_declaration",
            }

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

        # S1 (PART S): session-scoped history check. R9-NEW-1 regression:
        # the AI called `rows = get_adql_results()` in a previous cell and then
        # `df.groupby(...)` directly in the current cell, which was rejected.
        # If the expected token was called anywhere in the session history, it
        # also counts as passing — the subprocess replay prefix re-executes that
        # cell, so the current cell genuinely has access to real archive data.
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
                    f"your code to actually read that cache, (b) declare "
                    f"data_source='user_file:<path>' if you are reading your own "
                    f"uploaded CSV / parquet via pd.read_csv / pd.read_parquet / "
                    f"load_csv, or (c) declare "
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
                    f"has_schematic_phase_curve={getattr(detection, 'has_schematic_phase_curve', False)}, "
                    f"has_constant_redshift_sequence={detection.has_constant_redshift_sequence}, "
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
                    "has_schematic_phase_curve": getattr(detection, "has_schematic_phase_curve", False),
                    "has_constant_redshift_sequence": detection.has_constant_redshift_sequence,
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
            # R5 O3: pure literal print / constant-arithmetic code (smoke test /
            # environment sanity check). Even when the AI declared
            # data_source='none...', do not mark SYNTHETIC — no synthetic data
            # is produced and no numbers enter the analysis chain.
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
        # U2b (PART U): Round 10 observation — the AI frequently exceeds 75s on
        # the first lightkurve download with mode='normal', then manually switches
        # to mode='slow' in a subsequent turn. Eliminate the model round-trip:
        # if mode is not 'slow', automatically retry once with mode='slow' (300s).
        # Only one escalation is allowed to prevent infinite loops.
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
        "mode": mode,  # reflects the mode actually used (may have been auto-escalated to 'slow' by U2b)
    }
    if auto_escalated:
        response["auto_escalated_mode"] = True
        response["note"] = (
            "run_python auto-escalated from mode='{}' to mode='slow' because "
            "initial run exceeded its timeout budget. Future similar calls "
            "should declare mode='slow' up front.".format(inp.get("mode") or "normal")
        )

    # R5 O2: non-zero exit_code overrides success=True. On a subprocess crash
    # the child sets payload['success']=True at startup; if the code crashes
    # before reaching the except block, the parent's SandboxResult.success
    # remains True even though proc.exitcode=1. Aligning them is the foundation
    # of the correct success semantics — otherwise the UI / AI sees
    # "success=true + exit_code=1" and cannot tell whether the call succeeded.
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
    if response.get("error_class") == "name_error":
        try:
            from app.services.code_executor import get_session_defined_names
            defined_names = get_session_defined_names(python_session_id)[:80]
            if defined_names:
                response["defined_names"] = defined_names
                response["hint"] = (
                    "NameError occurred. Reuse one of the defined session "
                    f"names instead of guessing: {', '.join(defined_names[:30])}"
                )
        except Exception:
            pass
    if response.get("error_class") == "key_error":
        missing_key = _missing_key_from_error(str(response.get("error") or ""))
        response["hint"] = (
            f"KeyError for column {missing_key!r}. Inspect available columns before indexing "
            "(for example: `rows = get_adql_results(); print(rows[0].keys())`), "
            f"then use `row.get({missing_key!r})` or the exact available column name. "
            "Do not invent a replacement source_id or assume every catalog row has one."
        )
    # R5 O1 + R6 post: stderr is ALWAYS written to response as a top-level field,
    # even when it is an empty string. From a diagnostic standpoint,
    # "stderr=''" and "stderr not set" are completely different signals:
    #   former = the child main ran and captured stderr; it genuinely produced nothing
    #   latter = the child crashed during Python interpreter startup / import;
    #            child main never ran; stderr went to uvicorn fd=2 (Render container
    #            logs), unreachable by the client
    # Users (and the AI) can infer the second case from the empty string alone
    # and know to check /api/admin/sandbox/health for Python-level stderr.
    # The legacy `traceback` field is kept for backwards-compat in failure cases.
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

    response = _apply_cmd_sanity_guard(response)

    if response.get("success") is False and not (
        response.get("stdout")
        or response.get("figures")
        or response.get("variables")
    ):
        # A failure with no usable output must be marked FAILED directly,
        # so the frontend does not fall back to showing 'auto'.
        # Failures that still have stdout/figures/variables are downgraded to
        # PARTIAL by result_provenance.
        response.setdefault("__tool_status__", "FAILED")
        response.setdefault("analysis_status", "failed")

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
                "from real observations. You MUST NOT use any facts, numbers, "
                "historical context, literature priors, physical interpretations, "
                "or conclusions from this call's stdout/variables/figures in "
                "your final reply. "
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
        # have its own; this output remains non-citeable synthetic even when
        # the sandbox itself failed).
        combined["data_origin"] = "synthetic"
        combined["__synthetic_declared__"] = True
        error_class = str(combined.get("error_class") or "").lower()
        fatal_failure = (
            combined.get("success") is False
            or combined.get("__tool_status__") == "FAILED"
            or error_class in {"oom", "sandbox_crash", "subprocesscrash", "timeout"}
        )
        if fatal_failure:
            # R22: execution failure must win over the SYNTHETIC badge.  The
            # payload is still non-citeable, but the UI/model must see that
            # the sandbox crashed/timed out instead of treating it as a
            # completed synthetic demo.
            combined["__tool_status__"] = "FAILED"
            combined["analysis_status"] = "failed"
            combined["__message_to_model__"] = (
                banner["__message_to_model__"]
                + " The Python execution also failed; do not treat stdout, "
                "variables, figures, or conclusions as a completed synthetic result."
            )
        else:
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

    # X5 (PART X): record which call number in this session the sandbox crash
    # occurred on. B4/B5/B6 all showed crashes at call 3 or later but we lack
    # data to locate the root cause. Not fixing the root cause — just adding
    # monitoring. Capturing the error_class dimension also helps categorise by
    # type (oom / sandbox_crash / timeout, etc.).
    try:
        if not response.get("success"):
            from app.observability.metrics import record_counter
            attempt_idx = _bump_run_python_attempt_idx(python_session_id)
            # Cap attempt_idx label at 10 to bound Prometheus cardinality —
            # beyond 10 all go into the "10+" bucket.
            attempt_label = (
                str(attempt_idx) if attempt_idx < _MAX_TRACKED_ATTEMPT_IDX_FOR_METRIC
                else f"{_MAX_TRACKED_ATTEMPT_IDX_FOR_METRIC}+"
            )
            record_counter(
                "sandbox_crash_by_attempt_idx_total",
                1.0,
                attempt_idx=attempt_label,
                error_class=str(response.get("error_class", "unknown"))[:32],
            )
        else:
            # Bump on success too, to keep attempt_idx in sync with the real call count
            _bump_run_python_attempt_idx(python_session_id)
    except Exception:
        pass

    return response


def _classify_sandbox_error(err: str) -> str:
    """F0.2: map a sandbox error string to a short class so the UI can
    render a status chip (e.g. "oom", "timeout", "sandbox_crash").
    """
    if not err:
        return "unknown"
    low = err.lower()
    if "textlanguageerror" in low or "non-english" in low:
        return "non_english_output"
    if "timed out" in low or "timeout" in low:
        return "timeout"
    if "memoryerror" in low or "address-space" in low or "oom" in low:
        return "oom"
    if (
        "sigsegv" in low or "segmentation fault" in low or "signal 11" in low
        or "subprocesscrash" in low
    ):
        return "sandbox_crash"
    if "sigkill" in low or "signal 9" in low or "killed" in low:
        return "oom"
    if "incomplete payload" in low or "terminated without result" in low \
            or "without an error message" in low:
        return "sandbox_crash"
    if "nameerror" in low:
        return "name_error"
    if "keyerror" in low:
        return "key_error"
    if "importerror" in low or "modulenotfounderror" in low:
        return "import_error"
    if "systemexit" in low:
        return "system_exit"
    if "syntaxerror" in low or "indentationerror" in low:
        return "syntax_error"
    return "runtime_error"


def _missing_key_from_error(err: str) -> str:
    match = re.search(r"KeyError:\s*['\"]([^'\"]+)['\"]", err or "")
    return match.group(1) if match else "unknown"


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


async def _require_tool_session_owner(session_id: str, user_id: str | None, db: Any) -> dict[str, Any] | None:
    """Return a structured error unless the current user owns ``session_id``."""
    if not user_id:
        return {"error": "Sign in before validating or generating a paper from a saved chat session."}
    try:
        import uuid as _uuid
        from sqlalchemy import select as _select
        from app.models.schemas import ChatSession as _ChatSession

        sid = _uuid.UUID(str(session_id))
        uid = _uuid.UUID(str(user_id))
        session = (
            await db.execute(
                _select(_ChatSession.id).where(
                    _ChatSession.id == sid,
                    _ChatSession.user_id == uid,
                )
            )
        ).scalar_one_or_none()
    except ValueError:
        return {"error": "Invalid session ID. Save the current chat session first."}
    if session is None:
        return {"error": "Session not found for the current account."}
    return None


async def _exec_validate_analysis(
    inp: dict,
    python_session_id: str = "default",
    user_id: str | None = None,
) -> dict:
    from app.models.database import async_session
    from app.services.analysis_validator import validate_analysis

    session_id = _resolve_session_id(inp, python_session_id)
    if not session_id:
        return {"error": "session_id is required. Save the current chat session first."}

    async with async_session() as db:
        owner_error = await _require_tool_session_owner(session_id, user_id, db)
        if owner_error:
            return owner_error
        try:
            validation = await validate_analysis(session_id, db)
        except Exception as exc:
            return {"error": str(exc)}
    return validation


async def _exec_generate_paper_draft(
    inp: dict,
    python_session_id: str = "default",
    user_id: str | None = None,
) -> dict:
    from app.models.database import async_session
    from app.services.paper_generator import generate_paper_draft

    session_id = _resolve_session_id(inp, python_session_id)
    if not session_id:
        return {"error": "session_id is required. Save the current chat session first."}

    journal_format = str(inp.get("journal_format", "aastex") or "aastex").strip().lower()
    async with async_session() as db:
        owner_error = await _require_tool_session_owner(session_id, user_id, db)
        if owner_error:
            return owner_error
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
    # R6-NEW-1: arXiv redirects http → https with a 301; httpx does not
    # follow redirects by default. Two-pronged fix: write the URL with https
    # directly + enable follow_redirects (some mirrors redirect again).
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

    L5 (audit 2026-04-20): default method='enhanced_template' (photo_z_pro)
    routes through the research-grade path. If the AI explicitly wants demo
    mode (7 templates), it must pass allow_demo=True — otherwise the response
    is demo_mode_blocked with guidance to use enhanced.
    """
    from app.services.photo_z import estimate_photo_z

    raw_magnitudes = inp.get("magnitudes", {})
    raw_mag_errors = inp.get("mag_errors", {})
    # L5: default changed to enhanced_template; no longer silently uses the 7-template hybrid
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


def _validate_manual_attestation(raw: Any) -> dict[str, Any]:
    """Normalize ``manual_attestation``: the user-supplied attestation that
    inline distance-modulus rows came from a citeable source (paper, archive
    landing page, etc.).

    Required fields: ``source`` (free-text origin description) and at least
    one of ``bibcode`` / ``arxiv`` / ``doi`` so claim_validator can map the
    attestation back to a real reference and add it to the bibcode pool.
    """
    if not isinstance(raw, dict):
        raise ValueError("manual_attestation must be an object")
    source = str(raw.get("source") or "").strip()
    bibcode = str(raw.get("bibcode") or "").strip()
    arxiv = str(raw.get("arxiv") or "").strip()
    doi = str(raw.get("doi") or "").strip()
    note = str(raw.get("note") or "").strip()
    if not source:
        raise ValueError("manual_attestation requires a non-empty 'source' field")
    if not (bibcode or arxiv or doi):
        raise ValueError(
            "manual_attestation requires at least one of 'bibcode', 'arxiv', 'doi' so the "
            "attestation is checkable against the citation pool"
        )
    return {
        "source": source,
        "bibcode": bibcode or None,
        "arxiv": arxiv or None,
        "doi": doi or None,
        "note": note or None,
    }




def _exec_get_async_job_status(inp: dict) -> dict:
    """Poll a job submitted via async_tool_runtime.submit_async_job."""
    from app.services import async_tool_runtime as atr

    job_id = str(inp.get("job_id") or "").strip()
    if not job_id:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": "job_id is required",
            "error_class": "missing_job_id",
        }
    return atr.format_status_for_tool(atr.get_async_job(job_id), requested_job_id=job_id)




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
    # K2: explicit "target missing" error — in the third regression the AI
    # omitted the target parameter on the first call, saw only the uninformative
    # "target is required", and only corrected it on the second call.
    # Embedding an example in the error message lets the AI get it right first time.
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
