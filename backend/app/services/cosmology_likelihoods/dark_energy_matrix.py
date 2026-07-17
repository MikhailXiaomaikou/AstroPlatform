"""DESI DR2 dark-energy evidence matrix and overlap-safe comparisons."""

from __future__ import annotations

import itertools
from typing import Any

from app.services.cosmology_likelihoods.analysis_registry import (
    DESI_DR2_ANALYSIS_REGISTRY_VERSION,
    DESI_DR2_CHAIN_LANDING_URL,
    DESI_DR2_CHAIN_MANIFEST_SHA256,
    DESI_DR2_CHAIN_MANIFEST_URL,
    get_cosmology_analysis,
    summarize_official_analysis,
)
from app.services.cosmology_likelihoods.config_builder import (
    _validate_model,
    build_likelihood_config,
)


_DARK_ENERGY_MATRIX_MODELS = frozenset({"lcdm", "wcdm", "w0wa_cdm"})
_OFFICIAL_SN_SELECTIONS = (
    "pantheon_plus",
    "union3",
    "des_sn5yr",
)
_COMPARISON_PARAMETER_ORDER = ("w0", "wa", "w", "omegam", "H0")


def _validate_supernova_selections(
    supernova_sets: list[str] | None,
) -> list[str]:
    selections = (
        list(supernova_sets)
        if supernova_sets
        else list(_OFFICIAL_SN_SELECTIONS)
    )
    normalized = [str(value).strip().lower() for value in selections]
    invalid = sorted(set(normalized) - set(_OFFICIAL_SN_SELECTIONS))
    if invalid:
        raise ValueError(
            "unsupported official DESI SN selections: "
            f"{invalid}; choose from {list(_OFFICIAL_SN_SELECTIONS)}"
        )
    if len(normalized) != len(set(normalized)):
        raise ValueError("supernova_sets contains duplicates")
    return normalized


def _official_matrix_cell(model: str, sn_selection: str) -> dict[str, Any]:
    entry = get_cosmology_analysis(
        model=model,
        supernova_selection=sn_selection,
    )
    cell_id = f"desi_dr2:{model}:{sn_selection}:default_cmb"
    if entry is None:
        return {
            "cell_id": cell_id,
            "model": model,
            "bao_dataset_key": "desi_dr2_bao",
            "supernova_selection": sn_selection,
            "execution_source": "published_external",
            "evidence_tier": "withheld",
            "status": "WITHHELD",
            "publication_ready": False,
            "parameter_intervals": {},
            "overlap_groups": [
                "desi_dr2_bao:all",
                "cmb:planck_pr3_pr4_act_dr6",
            ],
            "withheld_reasons": [
                "official_joint_chain_not_registered_for_model"
            ],
            "warnings": [
                "The official DESI v1.0 manifest does not contain a joint "
                "DESI+CMB+SN chain for this model/selection under the frozen "
                "registry contract. No substitute chain was used."
            ],
            "source_url": DESI_DR2_CHAIN_LANDING_URL,
            "support_artifacts": [],
        }

    summary = summarize_official_analysis(entry)
    status = str(summary.get("status") or "WITHHELD")
    ready = status == "READY" and summary.get("success") is True
    return {
        "cell_id": cell_id,
        "analysis_id": entry.key,
        "model": model,
        "bao_dataset_key": "desi_dr2_bao",
        # This is an input selection token.  The exact official component is
        # emitted separately so callers cannot confuse Pantheon+ (uncalibrated)
        # with the platform's Pantheon+SH0ES dataset key.
        "supernova_selection": sn_selection,
        "official_sn_component": entry.official_sn_component,
        "data_components": list(entry.data_components),
        "execution_source": "published_external",
        "evidence_tier": "published_external" if ready else "withheld",
        "status": "COMPLETED" if ready else "WITHHELD",
        "publication_ready": False,
        "claim_scope": "published_external_chain_context",
        "parameter_intervals": (
            summary.get("parameter_intervals") if ready else {}
        ),
        "two_dimensional_contours": (
            summary.get("two_dimensional_contours") if ready else {}
        ),
        "diagnostics": summary.get("diagnostics") if ready else None,
        "overlap_groups": list(entry.overlap_groups),
        "withheld_reasons": list(summary.get("withheld_reasons") or []),
        "warnings": [entry.notes],
        "source_url": entry.source_url,
        "paper_arxiv": entry.paper_arxiv,
        "paper_doi": entry.paper_doi,
        "analysis_contract": {
            "release": entry.release,
            "parameter_map": dict(entry.parameter_map),
            "weight_column": entry.weight_column,
            "burn_in_rule": entry.burn_in_rule,
            "chain_format": entry.chain_format,
        },
        "license": {
            "name": entry.license_name,
            "url": entry.license_url,
        },
        "support_artifacts": summary.get("support_artifacts") or [],
    }


def _dr1_config_reference_cell(
    model: str,
    sn_selection: str,
) -> dict[str, Any]:
    dataset_keys = [
        "desi_dr1_bao",
        sn_selection,
        "planck2018_compressed",
    ]
    try:
        config = build_likelihood_config(
            model=model,
            dataset_keys=dataset_keys,
            sampler="mcmc",
            output_format="cobaya",
        )
    except Exception as exc:
        return {
            "cell_id": f"desi_dr1_reference:{model}:{sn_selection}",
            "model": model,
            "bao_dataset_key": "desi_dr1_bao",
            "supernova_selection": sn_selection,
            "dataset_keys": dataset_keys,
            "execution_source": "platform_config",
            "evidence_tier": "withheld",
            "status": "WITHHELD",
            "publication_ready": False,
            "parameter_intervals": {},
            "overlap_groups": [
                "desi_dr1_bao:all",
                "cmb:planck2018_compressed",
                f"sn:platform:{sn_selection}",
            ],
            "withheld_reasons": [f"dr1_reference_config_failed:{exc}"],
        }
    return {
        "cell_id": f"desi_dr1_reference:{model}:{sn_selection}",
        "model": model,
        "bao_dataset_key": "desi_dr1_bao",
        "supernova_selection": sn_selection,
        "dataset_keys": dataset_keys,
        "execution_source": "platform_config",
        "evidence_tier": "withheld",
        "status": "CONFIG_ONLY",
        "publication_ready": False,
        "parameter_intervals": {},
        "config_hash": config["config_hash"],
        "overlap_groups": [
            "desi_dr1_bao:all",
            "cmb:planck2018_compressed",
            f"sn:platform:{sn_selection}",
        ],
        "withheld_reasons": ["dr1_reference_chain_not_run"],
        "warnings": [
            "This is a separate platform DR1 configuration reference, not an "
            "official DESI DR1 posterior and not part of any DR2 likelihood. "
            "The platform Pantheon+ key is SH0ES-calibrated and is not the "
            "official DR2 chain's uncalibrated pantheonplus component."
        ],
    }


def _interval_width(summary: dict[str, Any]) -> float | None:
    try:
        q16 = float(summary["q16"])
        q84 = float(summary["q84"])
    except (KeyError, TypeError, ValueError):
        return None
    if q84 < q16:
        return None
    return q84 - q16


def build_overlap_safe_tension_summaries(
    cells: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare interval shifts without assuming overlapping cells independent.

    This v1 registry contains no cross-covariance or paired-resampling product,
    so it never emits a numerical tension significance.  The explicit ``None``
    value is load-bearing: clients must not backfill sqrt(sigma1^2+sigma2^2).
    """

    ready = [
        cell
        for cell in cells
        if cell.get("status") == "COMPLETED"
        and isinstance(cell.get("parameter_intervals"), dict)
        and cell.get("parameter_intervals")
    ]
    comparisons: list[dict[str, Any]] = []
    contour_comparisons: list[dict[str, Any]] = []
    for left, right in itertools.combinations(ready, 2):
        left_intervals = left["parameter_intervals"]
        right_intervals = right["parameter_intervals"]
        common_params = [
            name
            for name in _COMPARISON_PARAMETER_ORDER
            if name in left_intervals and name in right_intervals
        ]
        shared_groups = sorted(
            set(left.get("overlap_groups") or [])
            & set(right.get("overlap_groups") or [])
        )
        left_contours = left.get("two_dimensional_contours") or {}
        right_contours = right.get("two_dimensional_contours") or {}
        common_contours = sorted(set(left_contours) & set(right_contours))
        if common_contours:
            contour_comparisons.append(
                {
                    "cell_a": left["cell_id"],
                    "cell_b": right["cell_id"],
                    "status": "correlated_contour_comparison_only",
                    "contour_pairs": common_contours,
                    "overlap_groups": shared_groups,
                    "cross_covariance_status": "not_registered",
                    "tension_sigma": None,
                    "reason": (
                        "Overlay the registered empirical 2D grids for visual "
                        "comparison only. Shared DESI/CMB information prevents "
                        "an independent-significance calculation."
                    ),
                }
            )
        for parameter in common_params:
            left_summary = left_intervals[parameter]
            right_summary = right_intervals[parameter]
            try:
                center_left = float(left_summary["mean"])
                center_right = float(right_summary["mean"])
            except (KeyError, TypeError, ValueError):
                continue
            comparisons.append(
                {
                    "cell_a": left["cell_id"],
                    "cell_b": right["cell_id"],
                    "parameter": parameter,
                    "status": "correlated_tension_withheld",
                    "center_a": center_left,
                    "center_b": center_right,
                    "center_shift": center_right - center_left,
                    "interval_width_a": _interval_width(left_summary),
                    "interval_width_b": _interval_width(right_summary),
                    "overlap_groups": shared_groups,
                    "cross_covariance_status": "not_registered",
                    "paired_resampling_status": "not_registered",
                    "tension_sigma": None,
                    "reason": (
                        "The analyses share DESI and/or CMB observations and "
                        "the registry has no verified cross-covariance or "
                        "paired resampling product. Independent-Gaussian "
                        "tension significance is therefore undefined."
                    ),
                }
            )
    return {
        "status": (
            "correlated_tension_withheld"
            if comparisons
            else "withheld_no_verified_intervals"
        ),
        "comparisons": comparisons,
        "contour_comparisons": contour_comparisons,
        "naive_independent_sigma_allowed": False,
        "method": (
            "Report centers and interval widths only. A numerical tension may "
            "be added only after a byte-pinned cross-covariance or paired "
            "resampling product is registered and validated."
        ),
    }


def run_dark_energy_evidence_matrix(
    *,
    model: str,
    supernova_sets: list[str] | None = None,
    include_desi_dr1_reference: bool = False,
) -> dict[str, Any]:
    """Read the pinned official DESI DR2+CMB+SN comparison matrix.

    No network access or automatic chain download occurs.  Without a complete
    local mirror configured through ``DESI_DR2_OFFICIAL_CHAIN_ROOT``, every
    affected cell remains WITHHELD and contains no posterior numbers.
    """

    model_key = _validate_model(model)
    if model_key not in _DARK_ENERGY_MATRIX_MODELS:
        raise ValueError(
            "dark-energy evidence matrix supports lcdm, wcdm, or w0wa_cdm"
        )
    sn_selections = _validate_supernova_selections(supernova_sets)
    official_cells = [
        _official_matrix_cell(model_key, sn_selection)
        for sn_selection in sn_selections
    ]
    reference_cells = (
        [
            _dr1_config_reference_cell(model_key, sn_selection)
            for sn_selection in sn_selections
        ]
        if include_desi_dr1_reference
        else []
    )
    matrix = [*official_cells, *reference_cells]
    ready_count = sum(cell.get("status") == "COMPLETED" for cell in official_cells)
    tension_lab = build_overlap_safe_tension_summaries(official_cells)
    return {
        "success": True,
        "__tool_status__": "PARTIAL",
        "analysis_status": (
            "DARK_ENERGY_EVIDENCE_MATRIX_READY"
            if ready_count == len(official_cells)
            else "DARK_ENERGY_EVIDENCE_MATRIX_PARTIAL"
        ),
        # A matrix aggregate cannot launder any nested child interval into a
        # top-level claim, even when every official child is byte-verified.
        "publication_ready": False,
        "__do_not_claim__": True,
        "claim_scope": "dark_energy_evidence_matrix_diagnostic",
        "model": model_key,
        "bao_dataset_key": "desi_dr2_bao",
        "supernova_sets": sn_selections,
        "include_desi_dr1_reference": bool(include_desi_dr1_reference),
        "matrix_size": len(matrix),
        "official_ready_cells": ready_count,
        "official_withheld_cells": len(official_cells) - ready_count,
        "matrix": matrix,
        "tension_lab": tension_lab,
        "warnings": [
            "Official posterior chains are published-external evidence, not "
            "likelihood factors and are never multiplied together here.",
            "The three DR2+CMB+SN cells share DESI and CMB information. No "
            "independent-error tension sigma is computed.",
            "Official DESI pantheonplus is uncalibrated Pantheon+; it is not "
            "the platform's SH0ES-calibrated pantheon_plus dataset key.",
        ],
        "literature_context": {
            "source": DESI_DR2_CHAIN_LANDING_URL,
            "paper_arxiv": "2503.14738",
            "paper_doi": "10.1103/tr6y-kpc6",
            "note": (
                "Any significance quoted by the DESI paper is literature "
                "context, not a significance recomputed by this tool."
            ),
        },
        "provenance": {
            "cosmology_analysis_registry": {
                "registry_version": DESI_DR2_ANALYSIS_REGISTRY_VERSION,
                "manifest_url": DESI_DR2_CHAIN_MANIFEST_URL,
                "manifest_sha256": DESI_DR2_CHAIN_MANIFEST_SHA256,
                "analysis_ids": [
                    cell.get("analysis_id")
                    for cell in official_cells
                    if cell.get("analysis_id")
                ],
            }
        },
        "__message_to_model__": (
            "This aggregate is diagnostic-only. Report which official cells "
            "were verified or withheld and why. Do not quote nested posterior "
            "numbers as platform results, and never derive a naive tension "
            "sigma for cells sharing DESI or CMB data."
        ),
    }
