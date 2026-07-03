"""Line-luminosity/LFR fitting engine.

Moved verbatim out of app/services/ai_tools/__init__.py (H2 split,
2026-07-03). Tool schemas here cover: fit_line_lfr.
Schemas are reassembled into TOOLS (exact pre-split order) and tool
calls are still dispatched by _execute_tool_inner in the package
__init__ — this module is an implementation detail, import from
app.services.ai_tools.
"""

import asyncio
import math
from collections.abc import Awaitable
from typing import Any

from app.services.ai_tools import logger
from app.services.ai_tools.literature import _extract_and_cache_paper_measurements
from app.services.ai_tools.literature_tables import _resolve_multiple_literature_caches
from app.services.ai_tools.sample_export import _cosmology_manifest_for
from app.services.ai_tools.spectral_measurements import (
    _bootstrap_ols_betas,
    _build_censored_upper_limit_row,
    _finite_float,
    _line_matches_filter,
    _load_user_csv_measurements,
    _row_has_citation,
    _split_rows_by_redshift,
    _subsample_significance_from_betas,
)

TOOL_SCHEMAS = [
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
                "user_file": {
                    "type": "string",
                    "description": (
                        "Path of a USER-UPLOADED CSV (must start with 'uploads/'; "
                        "the chat attachment button / POST /api/data/files/upload "
                        "returns it). Fits the user's OWN measurements: values are "
                        "read verbatim, the result is labeled "
                        "input_data_origin='user_uploaded' / source_authority="
                        "'user_provided', and the rows must NEVER be cited as "
                        "literature. Wins over cache_key/cache_keys. Combine with "
                        "column_mapping when the CSV headers are non-standard."
                    ),
                },
                "column_mapping": {
                    "type": "object",
                    "description": (
                        "With user_file: {field: column-header-or-0-based-index} "
                        "pinning which CSV column holds source_name / redshift / "
                        "log_luminosity / luminosity_err / fwhm_km_s / fwhm_err. "
                        "Use when the headers defeat automatic detection; confirm "
                        "the mapping with the user, never guess."
                    ),
                },
                "line_id": {
                    "type": "string",
                    "description": "Line filter, e.g. '[CII]' or 'CII'. Default: [CII].",
                },
                "include_upper_limits": {
                    "type": "boolean",
                    "description": (
                        "Default false (limit rows excluded, declared in "
                        "censoring_hint). True admits '<'-luminosity rows that "
                        "carry a tabulated FWHM(+err) as Kelly 2007 censored "
                        "points in the Bayesian likelihood; requires the "
                        "bayesian path (OLS cannot censor — the tool refuses "
                        "rather than silently dropping limits). Lower limits "
                        "('>') and rows without a real tabulated FWHM stay "
                        "excluded — x is never invented."
                    ),
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
                "seed": {
                    "type": "integer",
                    "description": (
                        "Seed for the bootstrap RNG that drives the Bayesian fit and the "
                        "subsample Δβ / p-value significance. Default 20260426 (deterministic); "
                        "set it to reproduce or vary the quoted significance."
                    ),
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
]


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
    # Lazy package import: tests monkeypatch these names on app.services.ai_tools;
    # resolving at call time preserves pre-split behavior (module globals == package namespace).
    from app.services.ai_tools import _resolve_literature_measurement_cache
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

    # 3B (2026-06-11): user-supplied CSV — the user's OWN measurements,
    # uploaded via /api/data/files/upload. Wins over cache entries when
    # passed. Rows are labeled user_uploaded end to end (claimable as user
    # data, never citeable as literature).
    user_file_in = str(inp.get("user_file") or "").strip()
    rows_origin = "literature_cache"
    if user_file_in:
        user_line_id = str(inp.get("line_id") or "[CII]").strip() or "[CII]"
        user_rows, user_csv_error = _load_user_csv_measurements(
            user_file_in,
            inp.get("column_mapping") if isinstance(inp.get("column_mapping"), dict) else None,
            user_line_id,
        )
        if user_csv_error:
            return {
                "success": False,
                "tool": "fit_line_lfr",
                "__tool_status__": "FAILED",
                "__do_not_claim__": True,
                "error": user_csv_error,
                "error_class": "user_csv_unreadable",
                "user_file": user_file_in,
                "__message_to_model__": (
                    f"fit_line_lfr could not read the user CSV: {user_csv_error} "
                    "Relay this to the user; do NOT substitute remembered or "
                    "fabricated measurements."
                ),
            }

    # PART AF C2 — accept either a single cache_key OR a list of
    # cache_keys to union before fitting. Lists win when both are
    # passed (lets the AI strictly add a second survey without
    # accidentally falling back to the single-cache path).
    cache_keys_in = inp.get("cache_keys")
    if user_file_in:
        rows = user_rows
        rows_origin = "user_uploaded"
        cache_key = f"user_file:{user_file_in}"
        resolved_cache_key = cache_key
        all_resolved_cache_keys = [cache_key]
    elif isinstance(cache_keys_in, list) and any(
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
    # Censoring (2026-06-12, opt-in so every existing baseline is unchanged):
    # when True, '<'-luminosity rows with a GENUINELY tabulated FWHM (+err)
    # enter the Bayesian likelihood as Kelly 2007 §5.3 censored points.
    include_upper_limits = bool(inp.get("include_upper_limits"))
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
    censored_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        reason = ""
        flags = row.get("quality_flags") if isinstance(row.get("quality_flags"), list) else []
        if not _line_matches_filter(row, line_id):
            reason = "line_filter"
        elif any("limit" in str(flag).lower() for flag in flags):
            reason = "limit_flag"
            if include_upper_limits:
                censored_candidate, censored_reject = _build_censored_upper_limit_row(
                    row, flags, idx,
                    require_citation=(rows_origin != "user_uploaded"),
                )
                if censored_candidate is not None:
                    censored_rows.append(censored_candidate)
                    reason = ""  # consumed as a censored point, not rejected
                else:
                    reason = censored_reject
        elif rows_origin != "user_uploaded" and not _row_has_citation(row):
            # User-uploaded rows have no literature citation BY DESIGN —
            # they are the user's own data, gated via input_data_origin
            # instead (mirrors cosmology_mcmc's user_uploaded handling).
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
            # Censored rows (if any were admitted) are NOT silently invisible
            # on this early exit — no fit ran, nothing consumed them.
            "n_censored_used": 0,
            "n_censored_admitted": len(censored_rows),
            "__message_to_model__": (
                "Fewer than two citeable line-measurement rows survived filtering. "
                "Do not claim a fitted luminosity-FWHM relation."
                + (
                    f" ({len(censored_rows)} upper-limit row(s) were admitted but "
                    "cannot be fit without a detected population.)"
                    if censored_rows else ""
                )
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
            # Censored rows shift IDENTICALLY (an upper limit on L scales
            # with d_L^2 exactly like a detection) — skipping them would
            # mix two cosmologies inside one likelihood.
            for row in censored_rows:
                z = _finite_float(row.get("redshift"))
                if z is None or z <= 0:
                    continue
                try:
                    dl_old = float(baseline_cosmo.luminosity_distance(z).to(u.Mpc).value)
                    dl_new = float(target_cosmo.luminosity_distance(z).to(u.Mpc).value)
                except Exception:
                    continue
                if dl_old <= 0 or dl_new <= 0:
                    continue
                row["log_luminosity"] = float(row["log_luminosity"]) + 2.0 * (
                    math.log10(dl_new) - math.log10(dl_old)
                )
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
        # Censored rows convert under the SAME unit transform — the limit
        # value is a luminosity like any other. Failures are rejected, never
        # silently kept in the old frame (the mixed-frame likelihood trap).
        kept_censored_units: list[dict[str, Any]] = []
        for row in censored_rows:
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
            kept_censored_units.append(row)
            n_unit_converted += 1
        censored_rows = kept_censored_units
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
    # The lensing guard applies to censored rows too — a magnified upper
    # limit biases the likelihood exactly like a magnified detection.
    censored_after_lensing: list[dict[str, Any]] = []
    for row in censored_rows:
        if row.get("is_lensed") is True and row.get("mu_lens") is None and not bool(row.get("_demagnified")):
            n_lensed_skipped_no_mu += 1
            rejected.append({
                "source_name": row.get("source_name"),
                "reason": "lensed_no_mu_correction",
                "row_index": row.get("row_index"),
                "detail": (
                    "Censored (upper-limit) row flagged is_lensed=True with no "
                    "mu_lens — demagnify before it can enter the likelihood."
                ),
                "bibcode": row.get("bibcode"),
            })
            continue
        censored_after_lensing.append(row)
    censored_rows = censored_after_lensing

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
        and (
            rows_origin == "user_uploaded"
            or all(_row_has_citation(row) for row in accepted)
        )
        and has_confirmed_luminosity_units
    )
    # Citations cover EVERY row that feeds the likelihood — detections AND
    # censored upper limits (a censored row's source paper must be in the
    # citation summary and the claim-validator bibcode pool, or correctly
    # citing it in prose would be flagged as suspicious).
    citation_keys = sorted({
        str(row.get("bibcode") or row.get("arxiv_id") or row.get("doi") or "").strip()
        for row in (accepted + censored_rows)
        if str(row.get("bibcode") or row.get("arxiv_id") or row.get("doi") or "").strip()
    })
    table_labels = sorted({
        str(row.get("table_label") or "").strip()
        for row in (accepted + censored_rows)
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

    # Censoring can only be honored by the Bayesian likelihood (Kelly 2007
    # delta); OLS has no censored-data concept. Promising upper limits and
    # then silently dropping them would misrepresent the sample — fail loud.
    if censored_rows and requested == "ols":
        return {
            "success": False,
            "tool": "fit_line_lfr",
            "__tool_status__": "FAILED",
            "__do_not_claim__": True,
            "error": (
                f"include_upper_limits=true admitted {len(censored_rows)} censored "
                "row(s), but fit_method_requested='ols' cannot model censored data. "
                "Use fit_method_requested='bayesian_xyerr' (or 'auto')."
            ),
            "error_class": "censoring_requires_bayesian",
            "n_censored_candidates": len(censored_rows),
        }
    if censored_rows and not both_errs_available:
        return {
            "success": False,
            "tool": "fit_line_lfr",
            "__tool_status__": "FAILED",
            "__do_not_claim__": True,
            "error": (
                "include_upper_limits=true requires the Bayesian path, which needs "
                f"two-axis errors on every detected row (have fwhm_err on {n_x_err}/{n_used}, "
                f"log_luminosity_err on {n_y_err}/{n_used}). Censored rows cannot be "
                "honored — refusing rather than silently dropping them."
            ),
            "error_class": "censoring_needs_two_axis_errors",
            "n_censored_candidates": len(censored_rows),
        }
    if censored_rows and n_used < 5:
        # Pre-flight, not a sampler crash: the Kelly likelihood needs a real
        # detected population to anchor the regression before limits can
        # constrain anything. Distinct error_class so remediation is "get
        # more detections", not "retry the sampler".
        return {
            "success": False,
            "tool": "fit_line_lfr",
            "__tool_status__": "FAILED",
            "__do_not_claim__": True,
            "error": (
                f"include_upper_limits=true needs at least 5 DETECTED rows to "
                f"anchor the censored fit; have {n_used} detections + "
                f"{len(censored_rows)} limits."
            ),
            "error_class": "censoring_needs_min_detections",
            "n_censored_candidates": len(censored_rows),
        }

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
            # Kelly 2007 censoring: append the qualified upper-limit rows
            # with delta=0. y = the limit value (verbatim cell); x/xerr from
            # the tabulated FWHM (verbatim — see _build_censored_upper_limit_row
            # for the physics discipline); ysig = the row's own error when the
            # paper quoted one, else the median detected yerr (a standard,
            # DECLARED pragmatic choice — limits are usually quoted bare).
            x_fit, y_fit, xerr_fit, yerr_fit = x, y, xerr_log, yerr_log
            delta_fit = None
            censored_ysig_policy = {"own_err": 0, "median_detected_err": 0}
            if censored_rows:
                x_c = np.array(
                    [math.log10(r["fwhm_km_s"] / 100.0) for r in censored_rows], dtype=float,
                )
                y_c = np.array([r["log_luminosity"] for r in censored_rows], dtype=float)
                xerr_c = (
                    np.array([r["fwhm_err_km_s"] for r in censored_rows], dtype=float)
                    / np.array([r["fwhm_km_s"] for r in censored_rows], dtype=float)
                ) / math.log(10.0)
                median_detected_yerr = float(np.median(yerr_log))
                if median_detected_yerr <= 0:
                    # ysig=0 makes linmix treat a "limit" as an exact
                    # detection AT the limit (wyerr=False short-circuits the
                    # censored resampling) — a silent semantic flip. Refuse.
                    return {
                        "success": False,
                        "tool": "fit_line_lfr",
                        "__tool_status__": "FAILED",
                        "__do_not_claim__": True,
                        "error": (
                            "censored fit needs a positive median detected "
                            "log_luminosity_err to assign ysig to bare limits; "
                            "the detected rows tabulate zero errors."
                        ),
                        "error_class": "censoring_needs_two_axis_errors",
                        "n_censored_candidates": len(censored_rows),
                    }
                yerr_c_list: list[float] = []
                for r in censored_rows:
                    own = _finite_float(r.get("log_luminosity_err"))
                    if own is not None and own > 0:
                        yerr_c_list.append(own)
                        censored_ysig_policy["own_err"] += 1
                    else:
                        yerr_c_list.append(median_detected_yerr)
                        censored_ysig_policy["median_detected_err"] += 1
                x_fit = np.concatenate([x, x_c])
                y_fit = np.concatenate([y, y_c])
                xerr_fit = np.concatenate([xerr_log, xerr_c])
                yerr_fit = np.concatenate([yerr_log, np.array(yerr_c_list, dtype=float)])
                delta_fit = np.concatenate([
                    np.ones(len(accepted), dtype=bool),
                    np.zeros(len(censored_rows), dtype=bool),
                ])
            # Pick miniter modestly so a typical N≈70 cluster sample
            # finishes in 30-60 s; linmix extends to maxiter when its
            # internal R-hat hasn't converged yet.
            bayes_result = kelly07_linmix_fit(
                x=x_fit, y=y_fit,
                xerr=xerr_fit, yerr=yerr_fit,
                delta=delta_fit,
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
            if censored_rows:
                # OLS cannot honor the admitted censored rows — refusing
                # beats silently shipping a fit that dropped them.
                return {
                    "success": False,
                    "tool": "fit_line_lfr",
                    "__tool_status__": "FAILED",
                    "__do_not_claim__": True,
                    "error": (
                        f"censored fit failed in the linmix sampler ({bayes_error}); "
                        "no OLS fallback exists for censored data."
                    ),
                    "error_class": "censoring_sampler_failed",
                    "n_censored_candidates": len(censored_rows),
                }
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
    for r in accepted + censored_rows:  # censored rows obey the same mismatch scan
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

    # Censoring declaration (2026-06-12). Always present so consumers can
    # tell "no limits involved" from "limits excluded" from "limits used".
    n_censored_used = len(censored_rows) if fit_method == "bayesian_xyerr_linmix" else 0
    result["n_censored_used"] = n_censored_used
    result["censoring"] = {
        "include_upper_limits": include_upper_limits,
        "n_censored_used": n_censored_used,
        "method": "kelly2007_linmix_delta" if n_censored_used else None,
        "censored_ysig_policy": (
            censored_ysig_policy if n_censored_used else None
        ),
        "note": (
            "Censored rows enter ONLY the Bayesian likelihood (Kelly 2007 "
            "delta). Their FWHM (x) is the verbatim tabulated value — for "
            "non-detections that is typically an assumed or companion-line "
            "width, never invented here — and the likelihood treats that "
            "width as an unbiased measurement with the quoted error (linmix "
            "has no x-censoring; a survey-wide assumed constant width piles "
            "censored rows at one x). Censored rows pass the same cosmology/"
            "unit/lensing transforms as detections. Correlations, residual "
            "RMS, subsample splits, fit_inputs_preview, plot_data points, "
            "and lensing_summary counts remain detections-only."
            if n_censored_used else None
        ),
    }
    if not include_upper_limits:
        n_limit_rejected = sum(1 for r in rejected if r.get("reason") == "limit_flag")
        if n_limit_rejected:
            result["censoring_hint"] = (
                f"{n_limit_rejected} limit-flagged row(s) were excluded. Rerun "
                "with include_upper_limits=true to admit '<'-luminosity rows "
                "that carry a tabulated FWHM(+err) as Kelly (2007) censored "
                "points in the Bayesian fit."
            )

    if rows_origin == "user_uploaded":
        # 3B honest labeling: this fit ran on the USER'S OWN data. Numeric
        # claims about the fit are allowed (user_uploaded is a claimable
        # origin, mirroring cosmology_mcmc.CLAIMABLE_INPUT_ORIGINS), but the
        # rows are NOT literature measurements: citation_keys is empty, so
        # claim_validator's bibcode pool gets nothing from this result, and
        # the provenance dataset says user_provided instead of paper_table.
        result["input_data_origin"] = "user_uploaded"
        result["claim_scope"] = "user_data"
        result["user_file"] = user_file_in
        result["provenance"]["datasets"][0] = {
            "service_key": "user_uploaded_csv_fit",
            "service_name": "User-uploaded measurement table fit",
            "archive_version": "user CSV upload",
            "source_authority": "user_provided",
            "article": "",
            "reference_url": "",
            "source_urls": [],
            "acknowledgement_template": (
                "This fit used measurements supplied by the user "
                f"({user_file_in}); the user vouches for their provenance."
            ),
        }
        result.setdefault("warnings", []).append({
            "code": "user_uploaded_inputs",
            "message": (
                "Fit inputs are user-supplied (not literature); publication "
                "requires the user to vouch for the data provenance."
            ),
        })
        result["__message_to_model__"] = (
            (str(result.get("__message_to_model__") or "") + " ").strip() + " "
            "These results describe the USER'S OWN uploaded data "
            f"({user_file_in}). Report the fit numbers freely, but do NOT "
            "present the rows as literature measurements and do NOT attach "
            "any bibcode/arXiv citation to them."
        ).strip()
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
