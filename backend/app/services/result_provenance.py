"""Result provenance and safety helpers for AI tool outputs.

The chat agent must distinguish between real archive data, cached real data,
user-supplied data, synthetic demonstrations, and unavailable data.  These
helpers keep that contract consistent across tools and make it harder for a
simulated example to be presented as a scientific measurement.

Phase 1 / R1 — reproducibility envelope:
Every tool return additionally carries a minimal reproducibility envelope
(`run_id`, `tool_version`, `query_hash`, `timestamp_utc`, optional
`random_seed`).  A user who later wants to replay an analysis can feed
these fields back in and get the same result (modulo archive updates
captured in `archive_version`).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from datetime import datetime, timezone
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
# F2.1: explicit status for a tool that ran cleanly but produced no data
# (e.g. ADQL returned 0 rows, search_objects returned [], run_python had
# empty stdout and no figures).  Distinct from FAILED so the UI / LLM /
# metrics can tell the two apart.
EMPTY = "empty"

_VALID_ORIGINS = {REAL_ARCHIVE, CACHED_REAL, USER_UPLOADED, SYNTHETIC, UNAVAILABLE}
_VALID_STATUS = {COMPLETED, PARTIAL, SIMULATED_DEMO, FAILED, EMPTY}

# Build-time tool version; populated by the Dockerfile via
# `ARG TOOL_VERSION` / `ENV TOOL_VERSION=...`.  Falls back to "dev" when
# running uvicorn locally.  Accessed lazily so tests can monkeypatch.
def _tool_version() -> str:
    return os.getenv("TOOL_VERSION", "dev")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compute_query_hash(tool_name: str, tool_input: Any) -> str:
    """Deterministic short SHA256 of (tool_name, tool_input)."""
    try:
        payload = json.dumps({"tool": tool_name, "input": tool_input},
                              sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = repr((tool_name, tool_input))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def reproducibility_envelope(
    tool_name: str,
    tool_input: Any,
    *,
    random_seed: int | None = None,
    archive_version: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Return the minimal metadata needed to replay a tool call.

    Every tool result carries this so later analyses (golden-path tests,
    user-triggered replays, audit-log inspection) can verify that the same
    input against the same archive version would produce the same output.
    """
    envelope: dict[str, Any] = {
        "run_id": run_id or str(uuid.uuid4()),
        "tool_version": _tool_version(),
        "query_hash": compute_query_hash(tool_name, tool_input),
        "timestamp_utc": _now_utc_iso(),
    }
    if random_seed is not None:
        envelope["random_seed"] = int(random_seed)
    if archive_version:
        envelope["archive_version"] = str(archive_version)
    return envelope


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
    # F6.1 / F6.2: new high-level astro helpers
    "query_gaia_cluster", "get_extinction",
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


def normalize_tool_result(
    tool_name: str,
    result: Any,
    *,
    tool_input: Any = None,
    random_seed: int | None = None,
    archive_version: str | None = None,
) -> dict[str, Any]:
    """Ensure every tool result is a dict with provenance metadata + envelope.

    The reproducibility envelope is added once per call so the payload
    always carries run_id / tool_version / query_hash / timestamp and (when
    supplied) random_seed + archive_version.  Existing envelope fields on
    the result are respected (idempotent — called multiple times on the
    same payload does not re-stamp).
    """
    if not isinstance(result, dict):
        result = {"value": result}

    # Attach envelope if not already present.  Tools that construct their
    # own envelope (e.g., pipeline-executed runs with upstream run_ids) win.
    if "reproducibility" not in result:
        result = dict(result)
        result["reproducibility"] = reproducibility_envelope(
            tool_name,
            tool_input if tool_input is not None else result.get("_tool_input"),
            random_seed=random_seed,
            archive_version=archive_version,
        )

    # R4: best-effort unit + frame annotation for well-known astronomical
    # column names (ra/dec → deg + ICRS, parallax → mas, teff → K, …).
    # Idempotent; pre-existing `*_unit` siblings win.
    try:
        from app.services.measurement import annotate_known_fields
        annotate_known_fields(result)
    except Exception:
        pass

    # R3: automatically run sanity checks and fold them into the result's
    # warnings list so the UI can surface a ⚠ chip per offending field.
    # We also emit a Prometheus counter per warning class so ops can see
    # the rate of physically-suspicious values at a glance.
    sanity = numeric_sanity_warnings(result)
    if sanity:
        existing = result.get("warnings") or []
        if isinstance(existing, str):
            existing = [existing]
        existing_set = {str(w) for w in existing}
        for w in sanity:
            if w not in existing_set:
                existing.append(w)
        result["warnings"] = existing
        try:
            from app.observability.metrics import record_counter
            record_counter(
                "sanity_warning_total", float(len(sanity)), tool=tool_name,
            )
        except Exception:
            pass

    # F2.1: detect empty tool returns BEFORE we stamp COMPLETED. An ADQL
    # query that returned 0 rows is a legitimate "no data" outcome, not a
    # success — the LLM must not derive any claims from it.  We inject a
    # machine-readable banner so the model literally cannot miss it.
    is_empty = _is_empty_payload(tool_name, result)
    if is_empty:
        result = _inject_empty_banner(result, tool_name)
        try:
            from app.observability.metrics import record_counter
            record_counter("empty_tool_result_total", 1.0, tool=tool_name)
        except Exception:
            pass

    if "data_origin" in result and "analysis_status" in result:
        # Still inject the banner if we detected empty, even on a pre-stamped result
        return result

    if result.get("error"):
        result = _inject_failed_banner(result, tool_name)
        return attach_provenance(result, data_origin=UNAVAILABLE, analysis_status=FAILED)

    if is_empty:
        # Clean-ran-but-no-data path
        if tool_name in _COMPUTE_TOOLS:
            origin = REAL_ARCHIVE
        elif tool_name in _DATA_TOOLS or tool_name in _REFERENCE_TOOLS:
            origin = REAL_ARCHIVE
        else:
            origin = UNAVAILABLE
        return attach_provenance(result, data_origin=origin, analysis_status=EMPTY)

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
        if not success:
            result = _inject_failed_banner(result, tool_name)
        return attach_provenance(result, data_origin=origin, analysis_status=status)

    if tool_name in _REFERENCE_TOOLS:
        return attach_provenance(result, data_origin=REAL_ARCHIVE, analysis_status=COMPLETED)

    return attach_provenance(result, data_origin=UNAVAILABLE, analysis_status=PARTIAL)


def _is_empty_payload(tool_name: str, result: dict[str, Any]) -> bool:
    """F2.1: decide whether a tool return has no data to back any claim.

    Conservative — we mark empty only on signals that are unambiguously
    "no result", not on merely small results.
    """
    if result.get("error"):
        return False  # FAILED path handles this
    # ADQL / VO / TAP
    if "row_count" in result:
        try:
            if int(result["row_count"]) == 0:
                return True
        except (ValueError, TypeError):
            pass
    rows = result.get("rows")
    if isinstance(rows, list) and not rows and result.get("row_count", 0) == 0:
        return True
    # search_objects / crossmatch / query_transients
    results_field = result.get("results")
    if isinstance(results_field, list) and not results_field:
        return True
    # run_python — empty stdout and no figures and no variables
    if tool_name == "run_python":
        stdout = result.get("stdout", "") or ""
        figures = result.get("figures") or []
        variables = result.get("variables") or {}
        if result.get("success") is True and not stdout.strip() and not figures and not variables:
            return True
    return False


def _inject_empty_banner(result: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """F2.1: prepend a machine-readable banner so the LLM cannot miss it.

    Model sees __tool_status__, __do_not_claim__, __message_to_model__,
    __suggested_next_step__ as the first keys of the tool_result dict.
    """
    banner = {
        "__tool_status__": "EMPTY",
        "__do_not_claim__": True,
        "__message_to_model__": (
            f"Tool `{tool_name}` ran but returned no data (0 rows / empty "
            f"result).  You MUST NOT claim any numerical result derived from "
            f"this call.  Either (a) retry with different parameters, or "
            f"(b) emit a <tools_returned_nothing/> structured abstention — "
            f"see system prompt."
        ),
        "__suggested_next_step__": _suggest_next_step(tool_name),
    }
    # Banner keys go FIRST so anything the model reads left-to-right hits
    # them before the actual (empty) payload.
    new_result = dict(banner)
    new_result.update(result)
    return new_result


def _inject_failed_banner(result: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """F2.1: same idea as empty, but for explicit tool failures."""
    err = str(result.get("error") or "unknown").strip()
    banner = {
        "__tool_status__": "FAILED",
        "__do_not_claim__": True,
        "__message_to_model__": (
            f"Tool `{tool_name}` failed with: {err!r}.  You MUST NOT claim any "
            f"numerical result derived from this call.  Either (a) retry with "
            f"different parameters, or (b) emit a <tools_returned_nothing/> "
            f"structured abstention — see system prompt."
        ),
        "__suggested_next_step__": _suggest_next_step(tool_name, error=err),
    }
    new_result = dict(banner)
    new_result.update(result)
    return new_result


def _suggest_next_step(tool_name: str, error: str | None = None) -> str:
    """Tool-specific next-step hint that the model can echo into a
    <tools_returned_nothing suggested_next_step="..."/> tag."""
    if error:
        lower = error.lower()
        if "timed out" in lower or "timeout" in lower:
            return f"Retry `{tool_name}` with a tighter scope (e.g. smaller radius, fewer sources) or ask for a slower async variant."
        if "oom" in lower or "memoryerror" in lower:
            return f"Reduce the data volume before calling `{tool_name}` again."
        if "import" in lower:
            return f"The requested library is not available in this sandbox; rewrite without it."
    if tool_name == "run_adql":
        return "Widen the query (larger cone radius / looser quality cuts), verify the table + column names exist, or try a different archive."
    if tool_name == "run_python":
        return "Ensure the code produces at least one print statement or figure; check that required inputs are present."
    if tool_name in {"search_objects", "crossmatch_catalogs", "query_transients"}:
        return "Widen the cone radius, relax magnitude cuts, or confirm the target name / coordinates."
    if tool_name == "search_literature":
        return "Broaden the keyword list or try a different archive (ADS vs arXiv)."
    return "Retry with different parameters, or ask the user to provide the target values explicitly."


def numeric_sanity_warnings(payload: Any) -> list[str]:
    """Find common physically suspicious numeric outputs.

    Phase 1 / R3 extends the original two checks (zero uncertainty, zero
    abundance) with:
    - negative parallax (unphysical; Gaia allows it but the derived
      distance is meaningless without a prior)
    - RA out of [0, 360)
    - Dec out of [-90, 90]
    - redshift below -0.01 (blueshifts inside the Local Group are ~-0.001;
      anything more negative is usually a unit error)
    - log g outside [0, 6] (stars span ~0-5; WDs up to ~9 but flagged)
    - absolute magnitudes fainter than +20 (likely error-bar leak)
    - negative masses / radii / luminosities
    Each warning references the dotted path so the UI can highlight the
    exact offending field.
    """
    warnings: list[str] = []

    def _walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                key_l = str(key).lower()
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    num = float(item)
                    if math.isfinite(num):
                        # Zero-valued uncertainty (original check)
                        if num == 0.0 and any(token in key_l for token in ("noise", "sigma", "uncert", "error")):
                            warnings.append(f"{child_path} is exactly zero; check units and noise propagation.")
                        # Zero abundance ratios
                        if num == 0.0 and key_l in {"c_o", "n_o", "c/o", "n/o"}:
                            warnings.append(f"{child_path} is exactly zero; check abundance accounting.")
                        # Negative parallax (physical but suspicious)
                        if "parallax" in key_l and num < 0:
                            warnings.append(
                                f"{child_path} = {num} is negative; Gaia allows it but derived distance is meaningless without a prior."
                            )
                        # RA range
                        if key_l in {"ra", "ra_deg", "raj2000"} and not (0.0 <= num < 360.0):
                            warnings.append(f"{child_path} = {num} is outside [0, 360).")
                        # Dec range
                        if key_l in {"dec", "dec_deg", "dej2000"} and not (-90.0 <= num <= 90.0):
                            warnings.append(f"{child_path} = {num} is outside [-90, 90].")
                        # Redshift floor
                        if key_l in {"z", "redshift", "rvz_redshift"} and num < -0.01:
                            warnings.append(f"{child_path} = {num}: blueshift this large is usually a unit or sign error.")
                        # log g
                        if key_l in {"log_g", "logg"} and not (0.0 <= num <= 6.5):
                            warnings.append(f"{child_path} = {num} is outside [0, 6.5]; likely a unit mistake.")
                        # Magnitudes: sanity not strictly bounded, but
                        # absolute magnitudes > 20 or < -30 are unphysical
                        if "abs_mag" in key_l and (num < -30 or num > 20):
                            warnings.append(f"{child_path} = {num} is outside [-30, 20]; check distance modulus.")
                        # Negative physical scalars
                        if key_l in {"mass", "mass_solar", "radius_solar", "radius", "luminosity", "l_bol"} and num < 0:
                            warnings.append(f"{child_path} = {num} is negative; unphysical.")
                _walk(item, child_path)
        elif isinstance(value, list):
            for index, item in enumerate(value[:200]):
                _walk(item, f"{path}[{index}]")

    _walk(payload)
    return warnings
