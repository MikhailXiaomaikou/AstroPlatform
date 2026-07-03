"""Stellar / photometric tools: photo-z, isochrones, crossmatch, light curves.

Moved verbatim out of app/services/ai_tools/__init__.py (H2 split,
2026-07-03). Tool schemas here cover: estimate_photo_z, fit_isochrone, get_async_job_status,
crossmatch_catalogs, search_lightcurve, classify_transient.
Schemas are reassembled into TOOLS (exact pre-split order) and tool
calls are still dispatched by _execute_tool_inner in the package
__init__ — this module is an implementation detail, import from
app.services.ai_tools.
"""

import asyncio
from typing import Any

from app.services.ai_tools import get_cached_results, logger

TOOL_SCHEMAS = [
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
                "mag_errors": {
                    "type": "object",
                    "description": (
                        "Optional dict mapping the same filter band names to 1-sigma "
                        "magnitude uncertainties, used to weight the SED fit. "
                        "Example: {\"sdss_g\": 0.05, \"sdss_r\": 0.04}"
                    ),
                },
                "method": {
                    "type": "string",
                    "enum": ["enhanced_template", "template", "ml", "hybrid"],
                    "description": (
                        "Estimator path. Default 'enhanced_template' (research-grade). "
                        "The 7-template demo path requires allow_demo=true."
                    ),
                },
                "allow_demo": {
                    "type": "boolean",
                    "description": (
                        "Set true to permit the 7-template demo path (not "
                        "publication-grade); otherwise demo mode is blocked. Default false."
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
]


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
    # Lazy package import: tests monkeypatch these names on app.services.ai_tools;
    # resolving at call time preserves pre-split behavior (module globals == package namespace).
    from app.services.ai_tools import store_search_results
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
