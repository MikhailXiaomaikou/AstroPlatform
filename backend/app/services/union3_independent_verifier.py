"""Independent numerical verifier for the controlled Union3 reproduction.

This module deliberately does not import the production reproduction service or
the existing cosmology-likelihood package.  It uses a scalar adaptive integral
and the analytic scalar offset formula as a genuinely separate calculation.
"""

from __future__ import annotations

import hashlib
import math
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any, Final

import numpy as np
from scipy.integrate import quad
from scipy.optimize import brentq, minimize_scalar

from app.services.registered_workflows import UNION3_RADIATION_CONVENTION


# Independently duplicated pins for the Union3 products at
# CobayaSampler/sn_data commit 261e3564f532964b83647ae88b3d2eb01a015257.
_VECTOR_SHA256: Final = (
    "a840fe71c606bda11b869dbfcacc21c0199a5dc393f3790d10a7b58de97deae7"
)
_COVARIANCE_SHA256: Final = (
    "64c79abd24bf5154bc1e38ad0c031e31dd6247cdcc5ca930829698169809a146"
)
_DATA_DIR: Final = Path(__file__).resolve().parents[2] / "data" / "union3"
_OMEGA_MIN: Final = 0.05
_OMEGA_MAX: Final = 0.80
_PROFILE_POINT_COUNT: Final = 41
_C_LIGHT_KM_S: Final = 299792.458
_H0_REFERENCE: Final = 70.0
_DECIMAL_QUANTUM: Final = Decimal("0.00000001")


class Union3IndependentVerificationError(ValueError):
    """Raised when independent verification cannot safely produce a result."""


def _decimal_string(value: float) -> str:
    return format(
        Decimal(str(float(value))).quantize(_DECIMAL_QUANTUM, rounding=ROUND_HALF_EVEN),
        "f",
    )


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise Union3IndependentVerificationError(f"input file unreadable: {path.name}") from exc


def _load_inputs(vector_path: Path, covariance_path: Path) -> tuple[np.ndarray, ...]:
    vector_digest = _sha256(vector_path)
    covariance_digest = _sha256(covariance_path)
    if vector_digest != _VECTOR_SHA256 or covariance_digest != _COVARIANCE_SHA256:
        raise Union3IndependentVerificationError("input sha256 does not match the pinned files")

    try:
        vector = np.loadtxt(vector_path, comments="#", usecols=(1, 2, 4), ndmin=2)
        covariance_tokens = covariance_path.read_text(encoding="utf-8").split()
        dimension = int(covariance_tokens[0])
        covariance_values = np.asarray(covariance_tokens[1:], dtype=np.float64)
    except (OSError, UnicodeError, ValueError, IndexError) as exc:
        raise Union3IndependentVerificationError("input parsing failed") from exc

    if vector.shape != (22, 3):
        raise Union3IndependentVerificationError("measurement vector must contain 22 rows")
    if dimension != 22 or covariance_values.size != 22 * 22:
        raise Union3IndependentVerificationError("covariance must contain a 22 by 22 matrix")
    covariance = covariance_values.reshape(22, 22)
    if not np.all(np.isfinite(vector)) or not np.all(np.isfinite(covariance)):
        raise Union3IndependentVerificationError("inputs must be finite")
    if not np.allclose(covariance, covariance.T, atol=1e-12, rtol=1e-12):
        raise Union3IndependentVerificationError("covariance must be symmetric")
    try:
        np.linalg.cholesky(covariance)
    except np.linalg.LinAlgError as exc:
        raise Union3IndependentVerificationError("covariance must be positive definite") from exc

    z_cmb = vector[:, 0]
    z_hel = vector[:, 1]
    observed_mu = vector[:, 2]
    if np.any(z_cmb <= 0.0) or np.any(np.diff(z_cmb) <= 0.0):
        raise Union3IndependentVerificationError("redshift nodes must be positive and increasing")
    return z_cmb, z_hel, observed_mu, covariance


def _build_scalar_chi_square(
    z_cmb: np.ndarray,
    z_hel: np.ndarray,
    observed_mu: np.ndarray,
    covariance: np.ndarray,
):
    precision = np.linalg.solve(covariance, np.eye(covariance.shape[0]))
    ones = np.ones(observed_mu.size, dtype=np.float64)
    precision_ones = precision @ ones
    offset_norm = float(ones @ precision_ones)

    def chi_square(omega_m: float) -> float:
        if not _OMEGA_MIN <= omega_m <= _OMEGA_MAX:
            return math.inf
        distances = np.empty(z_cmb.size, dtype=np.float64)
        for index, redshift in enumerate(z_cmb):
            integral, _ = quad(
                lambda sample_z: 1.0
                / math.sqrt(omega_m * (1.0 + sample_z) ** 3 + (1.0 - omega_m)),
                0.0,
                float(redshift),
                epsabs=1e-12,
                epsrel=1e-12,
                limit=200,
            )
            distances[index] = (_C_LIGHT_KM_S / _H0_REFERENCE) * integral
        model_mu = 5.0 * np.log10((1.0 + z_hel) * distances) + 25.0
        residual = model_mu - observed_mu
        precision_residual = precision @ residual
        raw_chi_square = float(residual @ precision_residual)
        offset_projection = float(ones @ precision_residual)
        return raw_chi_square - offset_projection**2 / offset_norm

    return chi_square


def _independent_analysis(
    vector_path: Path,
    covariance_path: Path,
) -> dict[str, Any]:
    z_cmb, z_hel, observed_mu, covariance = _load_inputs(vector_path, covariance_path)
    chi_square = _build_scalar_chi_square(z_cmb, z_hel, observed_mu, covariance)

    coarse_omega = np.linspace(_OMEGA_MIN, _OMEGA_MAX, 101)
    coarse_chi_square = np.asarray([chi_square(value) for value in coarse_omega])
    coarse_index = int(np.argmin(coarse_chi_square))
    if coarse_index in {0, coarse_omega.size - 1}:
        raise Union3IndependentVerificationError("profile minimum is on a search boundary")
    minimum = minimize_scalar(
        chi_square,
        method="bounded",
        bounds=(float(coarse_omega[coarse_index - 1]), float(coarse_omega[coarse_index + 1])),
        options={"xatol": 1e-12, "maxiter": 500},
    )
    if not minimum.success or not math.isfinite(float(minimum.fun)):
        raise Union3IndependentVerificationError("independent Brent minimum failed")

    omega_best = float(minimum.x)
    chi_square_min = float(minimum.fun)
    target = chi_square_min + 1.0
    omega_lower = float(
        brentq(lambda value: chi_square(value) - target, _OMEGA_MIN, omega_best, xtol=1e-12)
    )
    omega_upper = float(
        brentq(lambda value: chi_square(value) - target, omega_best, _OMEGA_MAX, xtol=1e-12)
    )
    profile_omega = np.linspace(_OMEGA_MIN, _OMEGA_MAX, _PROFILE_POINT_COUNT)
    normalized_chi_square = np.asarray(
        [chi_square(value) - chi_square_min for value in profile_omega], dtype=np.float64
    )
    return {
        "omega_best": omega_best,
        "omega_lower": omega_lower,
        "omega_upper": omega_upper,
        "chi_square_min": chi_square_min,
        "degrees_of_freedom": int(observed_mu.size - 2),
        "profile_omega": profile_omega,
        "normalized_chi_square": normalized_chi_square,
    }


def run_union3_independent_verification(
    *,
    vector_path: str | Path | None = None,
    covariance_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the isolated Union3 calculation and return JSON-safe decimal strings."""

    vector = Path(vector_path) if vector_path is not None else _DATA_DIR / "lcparam_full.txt"
    covariance = (
        Path(covariance_path)
        if covariance_path is not None
        else _DATA_DIR / "mag_covmat.txt"
    )
    try:
        analysis = _independent_analysis(vector, covariance)
    except Union3IndependentVerificationError as exc:
        return {
            "status": "FAILED_FINAL",
            "verification_ready": False,
            "publication_ready": False,
            "failure_code": "INDEPENDENT_VERIFICATION_FAILED",
            "failure_reason": str(exc),
            "mcmc_diagnostics": "not_applicable",
        }

    profile = [
        {
            "omega_m": _decimal_string(omega_m),
            "normalized_chi_square": _decimal_string(delta_chi_square),
        }
        for omega_m, delta_chi_square in zip(
            analysis["profile_omega"], analysis["normalized_chi_square"], strict=True
        )
    ]
    return {
        "status": "COMPLETED",
        "verification_ready": True,
        "publication_ready": False,
        "method": "scalar_adaptive_quadrature_with_analytic_offset_elimination",
        "radiation_convention": UNION3_RADIATION_CONVENTION,
        "input_verification": {
            "measurement_vector_sha256": _VECTOR_SHA256,
            "covariance_sha256": _COVARIANCE_SHA256,
            "vector_length": "22",
            "covariance_shape": ["22", "22"],
            "symmetric": True,
            "cholesky": "passed",
        },
        "statistics": {
            "omega_m_best": _decimal_string(analysis["omega_best"]),
            "omega_m_lower": _decimal_string(analysis["omega_lower"]),
            "omega_m_upper": _decimal_string(analysis["omega_upper"]),
            "minus_error": _decimal_string(
                analysis["omega_best"] - analysis["omega_lower"]
            ),
            "plus_error": _decimal_string(
                analysis["omega_upper"] - analysis["omega_best"]
            ),
            "chi_square_min": _decimal_string(analysis["chi_square_min"]),
            "degrees_of_freedom": str(analysis["degrees_of_freedom"]),
        },
        "normalized_profile": profile,
        "mcmc_diagnostics": "not_applicable",
    }
