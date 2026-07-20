"""Small, immutable registry for controlled scientific reproduction workflows.

These entries describe the scientific contract.  They do not execute analyses
and they never turn literature values into likelihood inputs.
"""

from __future__ import annotations

import copy
from typing import Any, Final


UNION3_REPRODUCTION_WORKFLOW_ID: Final = "union3_flat_lcdm_sn_only_v1"
UNION3_REGISTERED_CLAIM: Final = (
    "Using the released Union3/UNITY1.5 22-node distance product and full "
    "covariance, the supernova-only flat-LambdaCDM profile-chi-square "
    "calculation reproduces omega_m = 0.356 (+0.028/-0.026) at 68.3% "
    "confidence (Delta chi-square = 1), consistent with Rubin et al. Table 9."
)
UNION3_RADIATION_CONVENTION: Final = "omega_r_zero_author_code_approximation"
UNION3_VECTOR_SHA256: Final = (
    "a840fe71c606bda11b869dbfcacc21c0199a5dc393f3790d10a7b58de97deae7"
)
UNION3_COVARIANCE_SHA256: Final = (
    "64c79abd24bf5154bc1e38ad0c031e31dd6247cdcc5ca930829698169809a146"
)
UNION3_PDF_SHA256: Final = (
    "6a8fccccecfc083d24c07f508d15ba273ebec1333fe4702d226520ffbaa603c9"
)


_UNION3_DATASET_PINS: Final[tuple[dict[str, str], ...]] = (
    {"key": "union3_lcparam_full", "sha256": UNION3_VECTOR_SHA256},
    {"key": "union3_mag_covmat", "sha256": UNION3_COVARIANCE_SHA256},
    {"key": "union3_arxiv_2311_12098v4_pdf", "sha256": UNION3_PDF_SHA256},
)


_UNION3_REPRODUCTION_WORKFLOW: Final[dict[str, Any]] = {
    "workflow_id": UNION3_REPRODUCTION_WORKFLOW_ID,
    "workflow_version": "1.0.0",
    "primary_executor": (
        "app.services.union3_reproduction.run_union3_primary_reproduction"
    ),
    "independent_verifier": (
        "app.services.union3_verification_service.verify_union3_primary_result"
    ),
    "dataset_key": "union3",
    "model": "flat_lcdm",
    "parameter": "omega_m",
    "statistic": "profile_chi_square",
    "claim_scope": "reproduction_of_published_constraint",
    "sources": {
        "paper": {
            "identifier": "arXiv:2311.12098v4",
            "url": "https://arxiv.org/abs/2311.12098v4",
            "locator": "PDF Table 9, Flat LambdaCDM, SNe row",
            "pdf_sha256": UNION3_PDF_SHA256,
        },
        "execution_files": {
            "repository": "CobayaSampler/sn_data",
            "commit": "261e3564f532964b83647ae88b3d2eb01a015257",
            "measurement_vector_sha256": (
                UNION3_VECTOR_SHA256
            ),
            "covariance_sha256": (
                UNION3_COVARIANCE_SHA256
            ),
        },
        "author_execution_convention": {
            "repository": "rubind/union3_release",
            "release": "v1.0",
            "commit": "7f805c9cc4e7643f0392faad03a275094501f8a2",
            "path": "stan_code_fixed.txt",
            "sha256": (
                "ed156b739f7bffd208f1dec48ddbe7039b1c88c8ac85ee3703195ae4e5f23c2d"
            ),
        },
    },
    "method": {
        "omega_m_min": "0.05",
        "omega_m_max": "0.80",
        "grid_step": "0.0005",
        "refinement": "Brent minimum and Delta-chi-square=1 roots",
        "magnitude_offset": "analytically_eliminated",
        "confidence_level": "0.683",
        "radiation_convention": UNION3_RADIATION_CONVENTION,
    },
    "dataset_pins": list(_UNION3_DATASET_PINS),
    "acceptance": {
        "profile_points": "41",
        "max_normalized_chi2_difference": "0.0001",
        "max_best_or_endpoint_difference": "0.0002",
        "paper_center_max_sigma": "0.1",
        "paper_interval_width_max_relative_difference": "0.05",
        "paper_chi2_max_absolute_difference": "0.2",
        "paper_degrees_of_freedom": "20",
    },
    "output_policy": {
        "reproduction_ready_requires_all_gates": True,
        "publication_ready": False,
        "scientific_verdict_before_human_review": "WITHHELD",
        "supported_requires_different_human_reviewer": True,
        "numeric_encoding": "decimal_strings",
        "mcmc_diagnostics": "not_applicable",
    },
}


REGISTERED_WORKFLOWS: Final[dict[str, dict[str, Any]]] = {
    UNION3_REPRODUCTION_WORKFLOW_ID: _UNION3_REPRODUCTION_WORKFLOW,
}


def get_registered_workflow(workflow_id: str) -> dict[str, Any]:
    """Return a defensive copy of a registered workflow contract."""

    try:
        workflow = REGISTERED_WORKFLOWS[workflow_id]
    except KeyError as exc:
        raise KeyError(f"Unknown registered workflow: {workflow_id}") from exc
    return copy.deepcopy(workflow)


def list_registered_workflows() -> tuple[str, ...]:
    """Return registered workflow identifiers in deterministic order."""

    return tuple(sorted(REGISTERED_WORKFLOWS))


def get_registered_dataset_pins(workflow_id: str) -> list[dict[str, str]]:
    """Return the only dataset-pin envelope accepted for a workflow."""

    workflow = get_registered_workflow(workflow_id)
    pins = workflow.get("dataset_pins")
    if not isinstance(pins, list):
        raise KeyError(f"Registered workflow has no dataset pins: {workflow_id}")
    return copy.deepcopy(pins)
