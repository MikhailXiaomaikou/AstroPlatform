"""Catalog / archive query tools (search, ADQL/TAP, SDSS, Gaia, extinction).

Moved verbatim out of app/services/ai_tools/__init__.py (H2 split,
2026-07-03). Tool schemas here cover: search_objects, run_adql, query_high_velocity_stars, run_sdss_sql,
get_object_info, describe_tap_table, query_gaia_cluster, get_extinction.
Schemas are reassembled into TOOLS (exact pre-split order) and tool
calls are still dispatched by _execute_tool_inner in the package
__init__ — this module is an implementation detail, import from
app.services.ai_tools.
"""

import asyncio
import math
import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.services.ai_tools import (
    _coerce_float,
    _session_cache_key,
    build_adql_result_set,
    build_adql_rows,
    logger,
    store_adql_result_set,
)

TOOL_SCHEMAS = [
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
            "Look up the total line-of-sight interstellar extinction A_V (and "
            "E(B-V)) at a sky position using the Schlegel-Finkbeiner-Davis 1998 "
            "2-D dust map.  Returns the integrated column (not a distance-resolved "
            "value).  Use this before comparing photometry to an isochrone or a "
            "model SED."
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
                "r_v": {"type": "number", "description": "R_V for the extinction curve.  Default 3.1."},
            },
            "required": ["ra", "dec"],
        },
    },
]


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
    # Lazy package import: tests monkeypatch these names on app.services.ai_tools;
    # resolving at call time preserves pre-split behavior (module globals == package namespace).
    from app.services.ai_tools import store_search_results
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
    # Lazy package import: tests monkeypatch these names on app.services.ai_tools;
    # resolving at call time preserves pre-split behavior (module globals == package namespace).
    from app.services.ai_tools import store_search_results
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
    # Lazy package import: tests monkeypatch these names on app.services.ai_tools;
    # resolving at call time preserves pre-split behavior (module globals == package namespace).
    from app.services.ai_tools import store_search_results
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
            if resolved.resolved:
                ra, dec = resolved.ra, resolved.dec
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
        if method == "analytic_fallback":
            # Toy formula, not an archive/checksummed lookup — must NOT enter
            # the claimable numeric universe wearing real-archive provenance.
            # Stamp SYNTHETIC so normalize_tool_result preserves it and
            # claim_validator excludes it from claimable success.
            out["data_origin"] = "synthetic"
            out["analysis_status"] = "simulated_demo"
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
