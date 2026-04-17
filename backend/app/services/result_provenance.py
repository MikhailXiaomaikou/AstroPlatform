"""Result provenance and safety helpers for AI tool outputs.

The chat agent must distinguish between real archive data, cached real data,
user-supplied data, synthetic demonstrations, and unavailable data.  These
helpers keep that contract consistent across tools and make it harder for a
simulated example to be presented as a scientific measurement.
"""

from __future__ import annotations

import math
from typing import Any

REAL_ARCHIVE = "real_archive"
CACHED_REAL = "cached_real"
USER_UPLOADED = "user_uploaded"
SYNTHETIC = "synthetic"
UNAVAILABLE = "unavailable"

COMPLETED = "completed"
PARTIAL = "partial"
SIMULATED_DEMO = "simulated_demo"
FAILED = "failed"

_VALID_ORIGINS = {REAL_ARCHIVE, CACHED_REAL, USER_UPLOADED, SYNTHETIC, UNAVAILABLE}
_VALID_STATUS = {COMPLETED, PARTIAL, SIMULATED_DEMO, FAILED}


def result_contract(
    *,
    data_origin: str,
    analysis_status: str,
    source_urls: list[str] | None = None,
    archive_ids: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build a normalized provenance contract."""
    origin = data_origin if data_origin in _VALID_ORIGINS else UNAVAILABLE
    status = analysis_status if analysis_status in _VALID_STATUS else PARTIAL
    warning_list = [str(item) for item in (warnings or []) if str(item).strip()]
    if origin == SYNTHETIC and status != SIMULATED_DEMO:
        status = SIMULATED_DEMO
        warning_list.append("Synthetic data may only be used as a demonstration, not as a research result.")
    if origin == UNAVAILABLE and status == COMPLETED:
        status = FAILED
    return {
        "data_origin": origin,
        "analysis_status": status,
        "source_urls": [str(item) for item in (source_urls or []) if str(item).strip()],
        "archive_ids": [str(item) for item in (archive_ids or []) if str(item).strip()],
        "warnings": warning_list,
    }


def attach_provenance(
    payload: dict[str, Any],
    *,
    data_origin: str,
    analysis_status: str,
    source_urls: list[str] | None = None,
    archive_ids: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Return a copy of payload with provenance keys set if absent."""
    result = dict(payload)
    existing_warnings = result.get("warnings")
    merged_warnings: list[str] = []
    if isinstance(existing_warnings, list):
        merged_warnings.extend(str(item) for item in existing_warnings if str(item).strip())
    elif isinstance(existing_warnings, str) and existing_warnings.strip():
        merged_warnings.append(existing_warnings)
    merged_warnings.extend(warnings or [])
    contract = result_contract(
        data_origin=str(result.get("data_origin") or data_origin),
        analysis_status=str(result.get("analysis_status") or analysis_status),
        source_urls=list(result.get("source_urls") or source_urls or []),
        archive_ids=list(result.get("archive_ids") or archive_ids or []),
        warnings=merged_warnings,
    )
    result.update(contract)
    return result


# Tool-name → default classification mapping for current HEAD tools (53 tools).
# Keep in sync with TOOLS in app/services/ai_tools.py.  The union of the three
# sets MUST equal the tool registry, or unclassified tools silently fall
# through to UNAVAILABLE/PARTIAL and the LLM downgrades their results.
_DATA_TOOLS = {
    "search_objects", "run_adql", "get_object_info", "get_object_dossier",
    "query_transients", "search_lightcurve", "crossmatch_catalogs",
    "batch_object_search", "describe_tap_table", "query_vo_service",
    "get_last_search_results", "read_fits_header", "get_provenance",
}
_COMPUTE_TOOLS = {
    "run_python", "generate_pipeline", "run_pipeline", "validate_analysis",
    "generate_paper_draft", "fit_isochrone", "estimate_photo_z",
    "estimate_photo_z_pro", "analyze_spectrum", "analyze_spectrum_pro",
    "sensitivity_analysis", "fit_transit_model", "gp_detrend_lightcurve",
    "detect_stellar_flares", "transit_search_bls", "reduce_ccd_image",
    "solve_astrometry", "extract_photometry", "extract_sources",
    "classify_transient", "classify_transient_spectrum",
    "compute_galaxy_sfr", "fit_rv_orbit", "fit_sersic_morphology",
    "x_ray_spectral_fit", "pulsar_derived_quantities",
    "analyze_cross_wavelength", "radio_analysis", "process_image",
    "share_with_team", "invite_team_member", "export_results",
    "workspace_export",
}
_REFERENCE_TOOLS = {
    "search_literature", "read_arxiv_paper", "literature_review",
    "research_workflow", "generate_proposal", "get_followup_recommendation",
    "full_research_report",
}

# Introspection helper for tests / CI: full known tool set.
ALL_KNOWN_TOOLS = _DATA_TOOLS | _COMPUTE_TOOLS | _REFERENCE_TOOLS


def normalize_tool_result(tool_name: str, result: Any) -> dict[str, Any]:
    """Ensure every tool result is a dict with provenance metadata."""
    if not isinstance(result, dict):
        result = {"value": result}
    if "data_origin" in result and "analysis_status" in result:
        return result

    if result.get("error"):
        return attach_provenance(result, data_origin=UNAVAILABLE, analysis_status=FAILED)

    if tool_name in _DATA_TOOLS:
        return attach_provenance(result, data_origin=REAL_ARCHIVE, analysis_status=COMPLETED)

    if tool_name in _COMPUTE_TOOLS:
        success = bool(result.get("success", True))
        # Compute tools almost always operate on data that originated in a real
        # archive (e.g. run_python analyzes the Gaia cache; fit_isochrone reads
        # SDSS photometry).  Default the origin to REAL_ARCHIVE; any tool that
        # genuinely operates on user-uploaded data (read_fits_header, FITS
        # reduction pipeline) should set data_origin explicitly in its result.
        origin = REAL_ARCHIVE if success else UNAVAILABLE
        status = COMPLETED if success else FAILED
        return attach_provenance(result, data_origin=origin, analysis_status=status)

    if tool_name in _REFERENCE_TOOLS:
        return attach_provenance(result, data_origin=REAL_ARCHIVE, analysis_status=COMPLETED)

    return attach_provenance(result, data_origin=UNAVAILABLE, analysis_status=PARTIAL)


def numeric_sanity_warnings(payload: Any) -> list[str]:
    """Find common physically suspicious numeric outputs."""
    warnings: list[str] = []

    def _walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                key_l = str(key).lower()
                if isinstance(item, (int, float)):
                    num = float(item)
                    if math.isfinite(num):
                        if num == 0.0 and any(token in key_l for token in ("noise", "sigma", "uncert", "error")):
                            warnings.append(f"{child_path} is exactly zero; check units and noise propagation.")
                        if num == 0.0 and key_l in {"c_o", "n_o", "c/o", "n/o"}:
                            warnings.append(f"{child_path} is exactly zero; check abundance accounting.")
                _walk(item, child_path)
        elif isinstance(value, list):
            for index, item in enumerate(value[:200]):
                _walk(item, f"{path}[{index}]")

    _walk(payload)
    return warnings
