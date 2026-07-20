"""Deterministic Union3 flat-LambdaCDM profile-chi-square reproduction.

The workflow fits only ``omega_m`` to the released 22-node distance product.
A constant magnitude offset is eliminated analytically.  The public result is
a narrow reproduction certificate, never a publication-readiness certificate.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Final

import numpy as np
from scipy.optimize import brentq, minimize_scalar

from app.services.registered_workflows import (
    UNION3_RADIATION_CONVENTION,
    UNION3_REGISTERED_CLAIM,
    UNION3_REPRODUCTION_WORKFLOW_ID,
)


# Exact Union3 ASCII products introduced at CobayaSampler/sn_data commit
# 261e3564f532964b83647ae88b3d2eb01a015257 and cited by arXiv:2311.12098v4.
UNION3_VECTOR_SHA256: Final = (
    "a840fe71c606bda11b869dbfcacc21c0199a5dc393f3790d10a7b58de97deae7"
)
UNION3_COVARIANCE_SHA256: Final = (
    "64c79abd24bf5154bc1e38ad0c031e31dd6247cdcc5ca930829698169809a146"
)
_DATA_DIR: Final = Path(__file__).resolve().parents[2] / "data" / "union3"
_OMEGA_MIN: Final = 0.05
_OMEGA_MAX: Final = 0.80
_GRID_STEP: Final = 0.0005
_HALF_GRID_STEP: Final = _GRID_STEP / 2.0
_PROFILE_POINT_COUNT: Final = 41
_GL_NODES, _GL_WEIGHTS = np.polynomial.legendre.leggauss(64)
_C_LIGHT_KM_S: Final = 299792.458
_H0_REFERENCE: Final = 70.0

_HALF_STEP_TOLERANCE: Final = Decimal("0.00020000")
_DECIMAL_QUANTUM: Final = Decimal("0.00000001")


class Union3ReproductionError(ValueError):
    """Raised when the deterministic workflow must fail closed."""


@dataclass(frozen=True)
class _VerifiedInput:
    z_cmb: np.ndarray
    z_hel: np.ndarray
    observed_mu: np.ndarray
    covariance: np.ndarray
    projected_precision: np.ndarray


@dataclass(frozen=True)
class _ProfileSummary:
    omega_best: float
    omega_lower: float
    omega_upper: float
    chi_square_min: float
    grid_step: float
    grid_point_count: int


def _decimal_string(value: float | Decimal) -> str:
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(float(value)))
    return format(
        decimal_value.quantize(_DECIMAL_QUANTUM, rounding=ROUND_HALF_EVEN),
        "f",
    )


def _serialized_profile_endpoints(summary: _ProfileSummary) -> dict[str, str]:
    """Return the signed/public endpoint representation for a profile result."""

    return {
        "omega_m_best": _decimal_string(summary.omega_best),
        "omega_m_lower": _decimal_string(summary.omega_lower),
        "omega_m_upper": _decimal_string(summary.omega_upper),
    }


def _canonical_half_step_difference(
    primary_endpoints: dict[str, str],
    half_step_endpoints: dict[str, str],
) -> Decimal:
    """Measure stability in the same canonical Decimal domain the verifier sees."""

    return max(
        abs(Decimal(primary_endpoints[field]) - Decimal(half_step_endpoints[field]))
        for field in ("omega_m_best", "omega_m_lower", "omega_m_upper")
    )


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise Union3ReproductionError(f"input file unreadable: {path.name}") from exc


def _load_verified_input(vector_path: Path, covariance_path: Path) -> _VerifiedInput:
    if _sha256(vector_path) != UNION3_VECTOR_SHA256:
        raise Union3ReproductionError("measurement vector sha256 mismatch")
    if _sha256(covariance_path) != UNION3_COVARIANCE_SHA256:
        raise Union3ReproductionError("covariance sha256 mismatch")

    try:
        vector_rows: list[tuple[float, float, float]] = []
        for line in vector_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            if len(fields) < 5:
                raise Union3ReproductionError("measurement vector row is malformed")
            vector_rows.append((float(fields[1]), float(fields[2]), float(fields[4])))

        covariance_tokens = covariance_path.read_text(encoding="utf-8").split()
        dimension = int(covariance_tokens[0])
        covariance_values = np.asarray(covariance_tokens[1:], dtype=np.float64)
    except (OSError, UnicodeError, ValueError, IndexError) as exc:
        if isinstance(exc, Union3ReproductionError):
            raise
        raise Union3ReproductionError("input parsing failed") from exc

    vector = np.asarray(vector_rows, dtype=np.float64)
    if vector.shape != (22, 3):
        raise Union3ReproductionError("measurement vector must contain exactly 22 rows")
    if dimension != 22 or covariance_values.size != 22 * 22:
        raise Union3ReproductionError("covariance must contain exactly 22 by 22 values")
    covariance = covariance_values.reshape(22, 22)
    if not np.all(np.isfinite(vector)) or not np.all(np.isfinite(covariance)):
        raise Union3ReproductionError("inputs must contain only finite values")
    if not np.allclose(covariance, covariance.T, atol=1e-12, rtol=1e-12):
        raise Union3ReproductionError("covariance is not symmetric")
    try:
        np.linalg.cholesky(covariance)
    except np.linalg.LinAlgError as exc:
        raise Union3ReproductionError("covariance is not positive definite") from exc

    z_cmb = vector[:, 0]
    z_hel = vector[:, 1]
    observed_mu = vector[:, 2]
    if np.any(z_cmb <= 0.0) or np.any(np.diff(z_cmb) <= 0.0):
        raise Union3ReproductionError("redshift nodes must be positive and increasing")

    precision = np.linalg.solve(covariance, np.eye(covariance.shape[0]))
    ones = np.ones((observed_mu.size, 1), dtype=np.float64)
    precision_ones = precision @ ones
    offset_norm = float((ones.T @ precision_ones).item())
    if not math.isfinite(offset_norm) or offset_norm <= 0.0:
        raise Union3ReproductionError("magnitude-offset projection is ill-conditioned")
    projected_precision = precision - (precision_ones @ precision_ones.T) / offset_norm
    return _VerifiedInput(
        z_cmb=z_cmb,
        z_hel=z_hel,
        observed_mu=observed_mu,
        covariance=covariance,
        projected_precision=projected_precision,
    )


def _chi_square_values(data: _VerifiedInput, omega_m: np.ndarray) -> np.ndarray:
    omega = np.asarray(omega_m, dtype=np.float64).reshape(-1)
    if np.any((omega < _OMEGA_MIN) | (omega > _OMEGA_MAX)):
        raise Union3ReproductionError(
            "omega_m lies outside the registered search interval"
        )

    integration_z = 0.5 * data.z_cmb[:, None] * (_GL_NODES[None, :] + 1.0)
    expansion_cubed = (1.0 + integration_z) ** 3
    expansion_rate = np.sqrt(
        omega[:, None, None] * expansion_cubed[None, :, :]
        + (1.0 - omega[:, None, None])
    )
    integrals = (
        0.5
        * data.z_cmb[None, :]
        * np.sum(
            _GL_WEIGHTS[None, None, :] / expansion_rate,
            axis=2,
        )
    )
    comoving_distance = (_C_LIGHT_KM_S / _H0_REFERENCE) * integrals
    luminosity_distance = (1.0 + data.z_hel[None, :]) * comoving_distance
    model_mu = 5.0 * np.log10(luminosity_distance) + 25.0
    residual = model_mu - data.observed_mu[None, :]
    return np.einsum(
        "ni,ij,nj->n", residual, data.projected_precision, residual, optimize=True
    )


def _profile_summary(data: _VerifiedInput, grid_step: float) -> _ProfileSummary:
    interval_count = int(round((_OMEGA_MAX - _OMEGA_MIN) / grid_step))
    grid = np.linspace(_OMEGA_MIN, _OMEGA_MAX, interval_count + 1)
    grid_chi_square = _chi_square_values(data, grid)
    minimum_index = int(np.argmin(grid_chi_square))
    if minimum_index in {0, grid.size - 1}:
        raise Union3ReproductionError("profile minimum is on a search boundary")

    def scalar_chi_square(value: float) -> float:
        return float(_chi_square_values(data, np.asarray([value]))[0])

    minimum = minimize_scalar(
        scalar_chi_square,
        method="brent",
        bracket=(
            float(grid[minimum_index - 1]),
            float(grid[minimum_index]),
            float(grid[minimum_index + 1]),
        ),
        options={"xtol": 1e-12, "maxiter": 500},
    )
    if not minimum.success or not math.isfinite(float(minimum.fun)):
        raise Union3ReproductionError("Brent profile minimum failed")

    omega_best = float(minimum.x)
    chi_square_min = float(minimum.fun)
    target = chi_square_min + 1.0
    if (
        scalar_chi_square(_OMEGA_MIN) <= target
        or scalar_chi_square(_OMEGA_MAX) <= target
    ):
        raise Union3ReproductionError("Delta-chi-square roots are not bracketed")
    omega_lower = float(
        brentq(
            lambda value: scalar_chi_square(value) - target,
            _OMEGA_MIN,
            omega_best,
            xtol=1e-12,
        )
    )
    omega_upper = float(
        brentq(
            lambda value: scalar_chi_square(value) - target,
            omega_best,
            _OMEGA_MAX,
            xtol=1e-12,
        )
    )
    return _ProfileSummary(
        omega_best=omega_best,
        omega_lower=omega_lower,
        omega_upper=omega_upper,
        chi_square_min=chi_square_min,
        grid_step=grid_step,
        grid_point_count=grid.size,
    )


def _failure_result(reason: str) -> dict[str, Any]:
    return {
        "workflow_id": UNION3_REPRODUCTION_WORKFLOW_ID,
        "status": "FAILED_FINAL",
        "scientific_status": "WITHHELD",
        "machine_support_eligible": False,
        "primary_ready": False,
        "reproduction_ready": False,
        "publication_ready": False,
        "failure_code": "UNION3_REPRODUCTION_FAILED",
        "failure_reason": reason,
        "mcmc_diagnostics": "not_applicable",
    }


def _gate(passed: bool, measured: str, threshold: str) -> dict[str, Any]:
    return {"passed": bool(passed), "measured": measured, "threshold": threshold}


def run_union3_primary_reproduction(
    *,
    vector_path: str | Path | None = None,
    covariance_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the local primary calculation without making a scientific verdict.

    The returned record remains WITHHELD. A separate Render verifier must
    recompute it, and a different human reviewer must approve the immutable
    claim/source binding before the deterministic finalizer may write
    ``SUPPORTED``.
    """

    vector = (
        Path(vector_path) if vector_path is not None else _DATA_DIR / "lcparam_full.txt"
    )
    covariance = (
        Path(covariance_path)
        if covariance_path is not None
        else _DATA_DIR / "mag_covmat.txt"
    )
    try:
        data = _load_verified_input(vector, covariance)
        primary = _profile_summary(data, _GRID_STEP)
        half_step = _profile_summary(data, _HALF_GRID_STEP)
    except Union3ReproductionError as exc:
        return _failure_result(str(exc))

    # Receipts sign the 8-decimal strings below, not platform-specific raw floats.
    # Compute the gate in that same canonical domain so ARM64/AMD64 BLAS last-bit
    # differences cannot make an honest receipt fail its exact anti-forgery check.
    primary_endpoints = _serialized_profile_endpoints(primary)
    half_step_endpoints = _serialized_profile_endpoints(half_step)
    half_step_max_difference = _canonical_half_step_difference(
        primary_endpoints, half_step_endpoints
    )

    profile_omega = np.linspace(_OMEGA_MIN, _OMEGA_MAX, _PROFILE_POINT_COUNT)
    primary_profile = _chi_square_values(data, profile_omega) - primary.chi_square_min
    minus_error = primary.omega_best - primary.omega_lower
    plus_error = primary.omega_upper - primary.omega_best
    degrees_of_freedom = int(data.observed_mu.size - 2)

    primary_gates = {
        "input_integrity": _gate(
            True, "two exact sha256 pins and valid matrices", "required"
        ),
        "half_step_stability": _gate(
            half_step_max_difference <= _HALF_STEP_TOLERANCE,
            _decimal_string(half_step_max_difference),
            _decimal_string(_HALF_STEP_TOLERANCE),
        ),
    }
    primary_ready = all(gate["passed"] for gate in primary_gates.values())

    normalized_profile = [
        {
            "omega_m": _decimal_string(omega_m),
            "normalized_chi_square": _decimal_string(delta_chi_square),
        }
        for omega_m, delta_chi_square in zip(
            profile_omega, primary_profile, strict=True
        )
    ]
    return {
        "workflow_id": UNION3_REPRODUCTION_WORKFLOW_ID,
        "status": "COMPLETED",
        "scientific_status": "WITHHELD",
        "review_status": "NOT_SUBMITTED",
        "machine_support_eligible": False,
        "primary_ready": primary_ready,
        "reproduction_ready": False,
        "publication_ready": False,
        "claim_scope": "reproduction_of_published_constraint",
        "claim": UNION3_REGISTERED_CLAIM,
        "input_verification": {
            "measurement_vector_sha256": UNION3_VECTOR_SHA256,
            "covariance_sha256": UNION3_COVARIANCE_SHA256,
            "vector_length": "22",
            "covariance_shape": ["22", "22"],
            "symmetric": True,
            "cholesky": "passed",
        },
        "method": {
            "model": "flat_lcdm",
            "fitted_parameter": "omega_m",
            "statistic": "profile_chi_square",
            "magnitude_offset": "analytically_eliminated",
            "omega_m_min": _decimal_string(_OMEGA_MIN),
            "omega_m_max": _decimal_string(_OMEGA_MAX),
            "grid_step": _decimal_string(_GRID_STEP),
            "grid_point_count": str(primary.grid_point_count),
            "refinement": "Brent minimum and Delta-chi-square=1 roots",
            "radiation_convention": UNION3_RADIATION_CONVENTION,
            "radiation_convention_source": {
                "repository": "rubind/union3_release",
                "release": "v1.0",
                "commit": "7f805c9cc4e7643f0392faad03a275094501f8a2",
                "path": "stan_code_fixed.txt",
                "sha256": (
                    "ed156b739f7bffd208f1dec48ddbe7039b1c88c8ac85ee3703195ae4e5f23c2d"
                ),
            },
        },
        "statistics": {
            "omega_m_best": primary_endpoints["omega_m_best"],
            "omega_m_lower": primary_endpoints["omega_m_lower"],
            "omega_m_upper": primary_endpoints["omega_m_upper"],
            "minus_error": _decimal_string(minus_error),
            "plus_error": _decimal_string(plus_error),
            "confidence_level": "0.683",
            "chi_square_min": _decimal_string(primary.chi_square_min),
            "degrees_of_freedom": str(degrees_of_freedom),
        },
        "half_step_statistics": {
            "grid_step": _decimal_string(_HALF_GRID_STEP),
            "grid_point_count": str(half_step.grid_point_count),
            "omega_m_best": half_step_endpoints["omega_m_best"],
            "omega_m_lower": half_step_endpoints["omega_m_lower"],
            "omega_m_upper": half_step_endpoints["omega_m_upper"],
        },
        "paper_reference": {
            "source": "arXiv:2311.12098v4",
            "locator": "PDF Table 9, Flat LambdaCDM, SNe row",
            "omega_m_center": "0.356",
            "minus_error": "0.026",
            "plus_error": "0.028",
            "chi_square": "24.0",
            "degrees_of_freedom": "20",
        },
        "normalized_profile": normalized_profile,
        "primary_gates": primary_gates,
        "mcmc_diagnostics": "not_applicable",
        "limitations": [
            "This reproduces the released 22-node distance product only.",
            "The constant magnitude zero point is eliminated, so no absolute scale is inferred.",
            "This does not rerun the 2,087 individual light curves.",
            "This is not a new scientific finding or a peer-review decision.",
            "The calculation follows the authors' released flat-LambdaCDM code with "
            "omega_r=0. Adding the paper's fixed-radiation term shifts the reported best "
            "value and interval endpoints by less than 0.0002 and does not change the "
            "rounded Table 9 reproduction, but it defines a different profile curve and "
            "is not used in the cross-implementation 0.0001 profile gate.",
        ],
    }


def run_union3_reproduction(
    *,
    vector_path: str | Path | None = None,
    covariance_path: str | Path | None = None,
) -> dict[str, Any]:
    """Compatibility alias for the primary-only local calculation."""

    return run_union3_primary_reproduction(
        vector_path=vector_path,
        covariance_path=covariance_path,
    )
