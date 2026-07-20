"""Render-side independent verification of an untrusted Union3 worker result."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

import numpy as np

from app.services.registered_workflows import (
    UNION3_RADIATION_CONVENTION,
    UNION3_REGISTERED_CLAIM,
    UNION3_REPRODUCTION_WORKFLOW_ID,
)
from app.services.union3_independent_verifier import run_union3_independent_verification

_VECTOR_SHA256: Final = (
    "a840fe71c606bda11b869dbfcacc21c0199a5dc393f3790d10a7b58de97deae7"
)
_COVARIANCE_SHA256: Final = (
    "64c79abd24bf5154bc1e38ad0c031e31dd6247cdcc5ca930829698169809a146"
)
_PROFILE_TOLERANCE: Final = Decimal("0.0001")
_ENDPOINT_TOLERANCE: Final = Decimal("0.0002")
_HALF_STEP_TOLERANCE: Final = Decimal("0.0002")
_PAPER_CENTER: Final = Decimal("0.356")
_PAPER_MINUS: Final = Decimal("0.026")
_PAPER_PLUS: Final = Decimal("0.028")
_PAPER_CHI_SQUARE: Final = Decimal("24.0")
_PAPER_CENTER_TOLERANCE: Final = Decimal("0.0027")
_PAPER_WIDTH_RELATIVE_TOLERANCE: Final = Decimal("0.05")
_PAPER_CHI_SQUARE_TOLERANCE: Final = Decimal("0.2")
_EXPECTED_METHOD: Final[dict[str, Any]] = {
    "model": "flat_lcdm",
    "fitted_parameter": "omega_m",
    "statistic": "profile_chi_square",
    "magnitude_offset": "analytically_eliminated",
    "omega_m_min": "0.05000000",
    "omega_m_max": "0.80000000",
    "grid_step": "0.00050000",
    "grid_point_count": "1501",
    "refinement": "Brent minimum and Delta-chi-square=1 roots",
    "radiation_convention": UNION3_RADIATION_CONVENTION,
    "radiation_convention_source": {
        "repository": "rubind/union3_release",
        "release": "v1.0",
        "commit": "7f805c9cc4e7643f0392faad03a275094501f8a2",
        "path": "stan_code_fixed.txt",
        "sha256": "ed156b739f7bffd208f1dec48ddbe7039b1c88c8ac85ee3703195ae4e5f23c2d",
    },
}
_EXPECTED_PAPER_REFERENCE: Final[dict[str, str]] = {
    "source": "arXiv:2311.12098v4",
    "locator": "PDF Table 9, Flat LambdaCDM, SNe row",
    "omega_m_center": "0.356",
    "minus_error": "0.026",
    "plus_error": "0.028",
    "chi_square": "24.0",
    "degrees_of_freedom": "20",
}
_EXPECTED_PRIMARY_LIMITATIONS: Final[list[str]] = [
    "This reproduces the released 22-node distance product only.",
    "The constant magnitude zero point is eliminated, so no absolute scale is inferred.",
    "This does not rerun the 2,087 individual light curves.",
    "This is not a new scientific finding or a peer-review decision.",
    "The calculation follows the authors' released flat-LambdaCDM code with omega_r=0. "
    "Adding the paper's fixed-radiation term shifts the reported best value and interval "
    "endpoints by less than 0.0002 and does not change the rounded Table 9 reproduction, "
    "but it defines a different profile curve and is not used in the "
    "cross-implementation 0.0001 profile gate.",
]


class Union3PrimaryResultError(ValueError):
    """The worker result does not satisfy the registered primary schema."""


def _decimal(value: Any, field: str) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        raise Union3PrimaryResultError(f"{field} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise Union3PrimaryResultError(f"{field} is not decimal") from exc
    if not parsed.is_finite():
        raise Union3PrimaryResultError(f"{field} must be finite")
    return parsed


def _gate(passed: bool, measured: Decimal | str, threshold: Decimal | str) -> dict[str, Any]:
    return {
        "passed": bool(passed),
        "measured": str(measured),
        "threshold": str(threshold),
    }


def _withheld(code: str, reason: str) -> dict[str, Any]:
    return {
        "workflow_id": UNION3_REPRODUCTION_WORKFLOW_ID,
        "status": "COMPLETED",
        "scientific_status": "WITHHELD",
        "review_status": "NOT_SUBMITTED",
        "verification_ready": False,
        "machine_support_eligible": False,
        "reproduction_ready": False,
        "publication_ready": False,
        "failure_code": code,
        "failure_reason": reason,
        "mcmc_diagnostics": "not_applicable",
    }


def _validate_primary_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise Union3PrimaryResultError("primary result must be an object")
    allowed_keys = {
        "workflow_id",
        "status",
        "scientific_status",
        "review_status",
        "machine_support_eligible",
        "primary_ready",
        "reproduction_ready",
        "publication_ready",
        "claim_scope",
        "claim",
        "input_verification",
        "method",
        "statistics",
        "half_step_statistics",
        "paper_reference",
        "normalized_profile",
        "primary_gates",
        "mcmc_diagnostics",
        "limitations",
    }
    if set(result) != allowed_keys:
        raise Union3PrimaryResultError("primary result schema mismatch")
    required_equal = {
        "workflow_id": UNION3_REPRODUCTION_WORKFLOW_ID,
        "status": "COMPLETED",
        "scientific_status": "WITHHELD",
        "publication_ready": False,
        "reproduction_ready": False,
        "primary_ready": True,
        "machine_support_eligible": False,
        "review_status": "NOT_SUBMITTED",
    }
    for field, expected in required_equal.items():
        if result.get(field) != expected:
            raise Union3PrimaryResultError(f"primary result {field} mismatch")
    if result.get("claim_scope") != "reproduction_of_published_constraint":
        raise Union3PrimaryResultError("primary claim scope mismatch")
    if result.get("claim") != UNION3_REGISTERED_CLAIM:
        raise Union3PrimaryResultError("primary registered claim mismatch")
    if result.get("method") != _EXPECTED_METHOD:
        raise Union3PrimaryResultError("primary method contract mismatch")
    if result.get("paper_reference") != _EXPECTED_PAPER_REFERENCE:
        raise Union3PrimaryResultError("primary paper reference mismatch")
    if result.get("mcmc_diagnostics") != "not_applicable":
        raise Union3PrimaryResultError("primary MCMC applicability mismatch")
    if result.get("limitations") != _EXPECTED_PRIMARY_LIMITATIONS:
        raise Union3PrimaryResultError("primary limitation contract mismatch")
    input_verification = result.get("input_verification")
    if not isinstance(input_verification, dict):
        raise Union3PrimaryResultError("primary input verification is missing")
    if input_verification != {
        "measurement_vector_sha256": _VECTOR_SHA256,
        "covariance_sha256": _COVARIANCE_SHA256,
        "vector_length": "22",
        "covariance_shape": ["22", "22"],
        "symmetric": True,
        "cholesky": "passed",
    }:
        raise Union3PrimaryResultError("primary input binding mismatch")
    statistics = result.get("statistics")
    half_step = result.get("half_step_statistics")
    profile = result.get("normalized_profile")
    if not isinstance(statistics, dict) or not isinstance(half_step, dict):
        raise Union3PrimaryResultError("primary statistics are missing")
    if set(statistics) != {
        "omega_m_best",
        "omega_m_lower",
        "omega_m_upper",
        "minus_error",
        "plus_error",
        "confidence_level",
        "chi_square_min",
        "degrees_of_freedom",
    }:
        raise Union3PrimaryResultError("primary statistics schema mismatch")
    if set(half_step) != {
        "grid_step",
        "grid_point_count",
        "omega_m_best",
        "omega_m_lower",
        "omega_m_upper",
    }:
        raise Union3PrimaryResultError("primary half-step schema mismatch")
    if not isinstance(profile, list) or len(profile) != 41:
        raise Union3PrimaryResultError("primary normalized profile must have 41 points")
    return result


def verify_union3_primary_result(
    primary_result: Any,
    *,
    vector_path: str | Path | None = None,
    covariance_path: str | Path | None = None,
) -> dict[str, Any]:
    """Independently recompute and gate a signed-but-untrusted worker result.

    Even a successful return remains WITHHELD and has
    ``reproduction_ready=false`` until a distinct configured reviewer approves
    the immutable claim/source hashes and the deterministic finalizer runs.
    """

    try:
        primary = _validate_primary_result(primary_result)
        statistics = primary["statistics"]
        half_step = primary["half_step_statistics"]
        primary_best = _decimal(statistics.get("omega_m_best"), "omega_m_best")
        primary_lower = _decimal(statistics.get("omega_m_lower"), "omega_m_lower")
        primary_upper = _decimal(statistics.get("omega_m_upper"), "omega_m_upper")
        primary_chi_square = _decimal(
            statistics.get("chi_square_min"), "chi_square_min"
        )
        primary_minus = _decimal(statistics.get("minus_error"), "minus_error")
        primary_plus = _decimal(statistics.get("plus_error"), "plus_error")
        if statistics.get("degrees_of_freedom") != "20":
            raise Union3PrimaryResultError("primary degrees of freedom mismatch")
        if statistics.get("confidence_level") != "0.683":
            raise Union3PrimaryResultError("primary confidence level mismatch")
        if not Decimal("0.05") < primary_lower < primary_best < primary_upper < Decimal(
            "0.80"
        ):
            raise Union3PrimaryResultError("primary interval is invalid or on a boundary")
        if abs((primary_best - primary_lower) - primary_minus) > Decimal("1e-7"):
            raise Union3PrimaryResultError("primary minus interval is inconsistent")
        if abs((primary_upper - primary_best) - primary_plus) > Decimal("1e-7"):
            raise Union3PrimaryResultError("primary plus interval is inconsistent")

        half_differences = (
            abs(primary_best - _decimal(half_step.get("omega_m_best"), "half_best")),
            abs(primary_lower - _decimal(half_step.get("omega_m_lower"), "half_lower")),
            abs(primary_upper - _decimal(half_step.get("omega_m_upper"), "half_upper")),
        )
        half_step_difference = max(half_differences)
        if half_step.get("grid_step") != "0.00025000":
            raise Union3PrimaryResultError("primary half-step size mismatch")
        if half_step.get("grid_point_count") != "3001":
            raise Union3PrimaryResultError("primary half-step point count mismatch")
        primary_gates = primary.get("primary_gates")
        if not isinstance(primary_gates, dict):
            raise Union3PrimaryResultError("primary gates are missing")
        expected_primary_gates = {
            "input_integrity": {
                "passed": True,
                "measured": "two exact sha256 pins and valid matrices",
                "threshold": "required",
            },
            "half_step_stability": {
                "passed": half_step_difference <= _HALF_STEP_TOLERANCE,
                "measured": format(half_step_difference.quantize(Decimal("0.00000001")), "f"),
                "threshold": "0.00020000",
            },
        }
        if primary_gates != expected_primary_gates:
            raise Union3PrimaryResultError("primary gates do not match recomputed values")
        primary_profile_rows = primary["normalized_profile"]
        expected_omega = np.linspace(0.05, 0.80, 41)
        primary_profile: list[Decimal] = []
        for index, row in enumerate(primary_profile_rows):
            if not isinstance(row, dict):
                raise Union3PrimaryResultError("primary profile row is invalid")
            if set(row) != {"omega_m", "normalized_chi_square"}:
                raise Union3PrimaryResultError("primary profile row schema mismatch")
            observed_omega = _decimal(row.get("omega_m"), f"profile[{index}].omega_m")
            if abs(observed_omega - Decimal(str(expected_omega[index]))) > Decimal("5e-8"):
                raise Union3PrimaryResultError("primary profile omega grid mismatch")
            delta = _decimal(
                row.get("normalized_chi_square"),
                f"profile[{index}].normalized_chi_square",
            )
            if delta < Decimal("-1e-7"):
                raise Union3PrimaryResultError("primary normalized chi-square is negative")
            primary_profile.append(delta)
    except (Union3PrimaryResultError, KeyError) as exc:
        return _withheld("PRIMARY_RESULT_INVALID", str(exc))

    try:
        independent = run_union3_independent_verification(
            vector_path=vector_path,
            covariance_path=covariance_path,
        )
    except Exception as exc:
        return _withheld("INDEPENDENT_VERIFICATION_FAILED", type(exc).__name__)
    if not isinstance(independent, dict):
        return _withheld(
            "INDEPENDENT_RESULT_INVALID", "independent result must be an object"
        )
    if independent.get("verification_ready") is not True:
        return _withheld(
            "INDEPENDENT_VERIFICATION_FAILED",
            str(independent.get("failure_reason") or "independent verifier failed"),
        )
    try:
        if independent.get("status") != "COMPLETED":
            raise Union3PrimaryResultError("independent status mismatch")
        if independent.get("publication_ready") is not False:
            raise Union3PrimaryResultError("independent publication flag mismatch")
        if independent.get("mcmc_diagnostics") != "not_applicable":
            raise Union3PrimaryResultError("independent MCMC applicability mismatch")
        if independent.get("radiation_convention") != UNION3_RADIATION_CONVENTION:
            raise Union3PrimaryResultError("independent radiation convention mismatch")
        if independent.get("method") != (
            "scalar_adaptive_quadrature_with_analytic_offset_elimination"
        ):
            raise Union3PrimaryResultError("independent method mismatch")
        if set(independent) != {
            "status",
            "verification_ready",
            "publication_ready",
            "method",
            "radiation_convention",
            "input_verification",
            "statistics",
            "normalized_profile",
            "mcmc_diagnostics",
        }:
            raise Union3PrimaryResultError("independent result schema mismatch")
        independent_inputs = independent.get("input_verification")
        if not isinstance(independent_inputs, dict) or independent_inputs != {
            "measurement_vector_sha256": _VECTOR_SHA256,
            "covariance_sha256": _COVARIANCE_SHA256,
            "vector_length": "22",
            "covariance_shape": ["22", "22"],
            "symmetric": True,
            "cholesky": "passed",
        }:
            raise Union3PrimaryResultError("independent input binding mismatch")
        independent_statistics = independent["statistics"]
        if not isinstance(independent_statistics, dict):
            raise Union3PrimaryResultError("independent statistics are missing")
        if set(independent_statistics) != {
            "omega_m_best",
            "omega_m_lower",
            "omega_m_upper",
            "minus_error",
            "plus_error",
            "chi_square_min",
            "degrees_of_freedom",
        }:
            raise Union3PrimaryResultError("independent statistics schema mismatch")
        independent_best = _decimal(
            independent_statistics.get("omega_m_best"), "independent_best"
        )
        independent_lower = _decimal(
            independent_statistics.get("omega_m_lower"), "independent_lower"
        )
        independent_upper = _decimal(
            independent_statistics.get("omega_m_upper"), "independent_upper"
        )
        independent_chi_square = _decimal(
            independent_statistics.get("chi_square_min"), "independent_chi_square"
        )
        independent_minus = _decimal(
            independent_statistics.get("minus_error"), "independent_minus"
        )
        independent_plus = _decimal(
            independent_statistics.get("plus_error"), "independent_plus"
        )
        if not (
            Decimal("0.05")
            < independent_lower
            < independent_best
            < independent_upper
            < Decimal("0.80")
        ):
            raise Union3PrimaryResultError("independent interval is invalid")
        if abs((independent_best - independent_lower) - independent_minus) > Decimal(
            "1e-7"
        ):
            raise Union3PrimaryResultError("independent minus interval is inconsistent")
        if abs((independent_upper - independent_best) - independent_plus) > Decimal(
            "1e-7"
        ):
            raise Union3PrimaryResultError("independent plus interval is inconsistent")
        independent_profile_rows = independent.get("normalized_profile")
        if not isinstance(independent_profile_rows, list) or len(
            independent_profile_rows
        ) != 41:
            raise Union3PrimaryResultError("independent profile length mismatch")
        independent_profile: list[Decimal] = []
        for index, row in enumerate(independent_profile_rows):
            if not isinstance(row, dict):
                raise Union3PrimaryResultError("independent profile row is invalid")
            if set(row) != {"omega_m", "normalized_chi_square"}:
                raise Union3PrimaryResultError(
                    "independent profile row schema mismatch"
                )
            observed_omega = _decimal(
                row.get("omega_m"), f"independent_profile[{index}].omega_m"
            )
            if abs(observed_omega - Decimal(str(expected_omega[index]))) > Decimal(
                "5e-8"
            ):
                raise Union3PrimaryResultError(
                    "independent profile omega grid mismatch"
                )
            delta = _decimal(
                row.get("normalized_chi_square"),
                f"independent_profile[{index}].normalized_chi_square",
            )
            if delta < Decimal("-1e-7"):
                raise Union3PrimaryResultError(
                    "independent normalized chi-square is negative"
                )
            independent_profile.append(delta)
    except (KeyError, TypeError, Union3PrimaryResultError) as exc:
        return _withheld("INDEPENDENT_RESULT_INVALID", str(exc))

    profile_difference = max(
        abs(primary_value - independent_value)
        for primary_value, independent_value in zip(
            primary_profile, independent_profile, strict=True
        )
    )
    endpoint_difference = max(
        abs(primary_best - independent_best),
        abs(primary_lower - independent_lower),
        abs(primary_upper - independent_upper),
    )
    center_difference = abs(primary_best - _PAPER_CENTER)
    minus_relative_difference = abs(primary_minus - _PAPER_MINUS) / _PAPER_MINUS
    plus_relative_difference = abs(primary_plus - _PAPER_PLUS) / _PAPER_PLUS
    chi_square_difference = abs(primary_chi_square - _PAPER_CHI_SQUARE)
    independent_chi_square_difference = abs(
        independent_chi_square - primary_chi_square
    )
    gates = {
        "primary_half_step_stability": _gate(
            half_step_difference <= _HALF_STEP_TOLERANCE,
            half_step_difference,
            _HALF_STEP_TOLERANCE,
        ),
        "independent_profile": _gate(
            profile_difference <= _PROFILE_TOLERANCE,
            profile_difference,
            _PROFILE_TOLERANCE,
        ),
        "independent_best_and_endpoints": _gate(
            endpoint_difference <= _ENDPOINT_TOLERANCE,
            endpoint_difference,
            _ENDPOINT_TOLERANCE,
        ),
        "independent_chi_square": _gate(
            independent_chi_square_difference <= _PROFILE_TOLERANCE,
            independent_chi_square_difference,
            _PROFILE_TOLERANCE,
        ),
        "paper_center": _gate(
            center_difference <= _PAPER_CENTER_TOLERANCE,
            center_difference,
            _PAPER_CENTER_TOLERANCE,
        ),
        "paper_minus_width": _gate(
            minus_relative_difference <= _PAPER_WIDTH_RELATIVE_TOLERANCE,
            minus_relative_difference,
            _PAPER_WIDTH_RELATIVE_TOLERANCE,
        ),
        "paper_plus_width": _gate(
            plus_relative_difference <= _PAPER_WIDTH_RELATIVE_TOLERANCE,
            plus_relative_difference,
            _PAPER_WIDTH_RELATIVE_TOLERANCE,
        ),
        "paper_chi_square": _gate(
            chi_square_difference <= _PAPER_CHI_SQUARE_TOLERANCE,
            chi_square_difference,
            _PAPER_CHI_SQUARE_TOLERANCE,
        ),
        "paper_degrees_of_freedom": _gate(
            independent_statistics.get("degrees_of_freedom") == "20",
            str(independent_statistics.get("degrees_of_freedom")),
            "20",
        ),
    }
    machine_support_eligible = all(gate["passed"] for gate in gates.values())
    return {
        "workflow_id": UNION3_REPRODUCTION_WORKFLOW_ID,
        "status": "COMPLETED",
        "scientific_status": "WITHHELD",
        "review_status": "PENDING" if machine_support_eligible else "NOT_SUBMITTED",
        "verification_ready": machine_support_eligible,
        "machine_support_eligible": machine_support_eligible,
        "reproduction_ready": False,
        "publication_ready": False,
        "claim_scope": "reproduction_of_published_constraint",
        "primary_analysis": primary,
        "independent_analysis": independent,
        "verification_gates": gates,
        "mcmc_diagnostics": "not_applicable",
        "limitations": [
            "Machine verification is not a scientific verdict.",
            "A different configured human reviewer must approve the immutable claim and source.",
            "This reproduces the released 22-node product and cannot measure H0.",
        ],
    }
