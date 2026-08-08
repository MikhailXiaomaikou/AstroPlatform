"""Deterministic scalar calculations for auditable source checks.

The service intentionally supports a small operation vocabulary.  It never
evaluates model-authored expressions or executes generated code.  Every result
is produced from an analytic Jacobian (or the exact independent-product
variance identity) and an explicitly declared covariance model so that
missing correlation information cannot silently become an independence
assumption.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np


Operation = Literal["ratio", "difference", "product", "weighted_mean"]
UncertaintyKind = Literal["independent", "correlation_matrix", "covariance_matrix"]


class ScalarDerivationError(ValueError):
    """A user-correctable input error in a deterministic derivation."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Quantity:
    id: str
    label: str
    value: float
    standard_uncertainty: float
    unit: str
    source_ref: str
    source_locator: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "value": self.value,
            "standard_uncertainty": self.standard_uncertainty,
            "unit": self.unit,
            "source_ref": self.source_ref,
            "source_locator": self.source_locator,
        }


_DIMENSIONLESS_UNITS = {"", "1", "dimensionless", "unitless", "无量纲"}
_UNIT_ALIASES = {
    "mpc": "Mpc",
    "gpc": "Gpc",
    "kpc": "kpc",
    "pc": "pc",
    "km/s/mpc": "km s^-1 Mpc^-1",
    "kms-1mpc-1": "km s^-1 Mpc^-1",
    "kms^-1mpc^-1": "km s^-1 Mpc^-1",
    "kms⁻¹mpc⁻¹": "km s^-1 Mpc^-1",
}


def _finite_float(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ScalarDerivationError(
            f"{field} must be a finite number.", code="invalid_number"
        ) from exc
    if not math.isfinite(number):
        raise ScalarDerivationError(
            f"{field} must be a finite number.", code="invalid_number"
        )
    return number


def _require_finite_derived(value: Any, *, field: str) -> None:
    """Fail closed before any non-finite derived value reaches a receipt."""
    try:
        finite = bool(np.all(np.isfinite(np.asarray(value, dtype=float))))
    except (TypeError, ValueError, OverflowError):
        finite = False
    if not finite:
        raise ScalarDerivationError(
            f"The derived {field} is not finite for the supplied inputs.",
            code="non_finite_result",
        )


def normalize_unit(unit: Any) -> str:
    """Normalize conservative aliases without pretending to be a unit parser."""
    text = str(unit or "").strip()
    if text.lower() in _DIMENSIONLESS_UNITS:
        return "dimensionless"
    compact = re.sub(r"[·*\s_/{}()]", "", text.lower())
    compact = compact.replace("\\mathrm", "").replace("\\", "")
    return _UNIT_ALIASES.get(text.lower(), _UNIT_ALIASES.get(compact, text))


def _parse_quantities(raw: Any) -> list[Quantity]:
    if not isinstance(raw, list):
        raise ScalarDerivationError(
            "quantities must be a list.", code="invalid_quantities"
        )
    parsed: list[Quantity] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ScalarDerivationError(
                f"quantities[{index}] must be an object.", code="invalid_quantities"
            )
        quantity_id = str(item.get("id") or "").strip()
        if not quantity_id or quantity_id in seen_ids:
            raise ScalarDerivationError(
                "Each quantity requires a unique non-empty id.",
                code="invalid_quantity_id",
            )
        seen_ids.add(quantity_id)
        uncertainty = _finite_float(
            item.get("standard_uncertainty"),
            field=f"quantities[{index}].standard_uncertainty",
        )
        if uncertainty < 0:
            raise ScalarDerivationError(
                "Standard uncertainties cannot be negative.",
                code="negative_uncertainty",
            )
        parsed.append(
            Quantity(
                id=quantity_id,
                label=str(item.get("label") or quantity_id).strip(),
                value=_finite_float(item.get("value"), field=f"quantities[{index}].value"),
                standard_uncertainty=uncertainty,
                unit=normalize_unit(item.get("unit")),
                source_ref=str(item.get("source_ref") or "").strip(),
                source_locator=str(item.get("source_locator") or "").strip(),
            )
        )
    return parsed


def _validate_operation_arity(operation: str, count: int) -> None:
    if operation in {"ratio", "difference", "product"} and count != 2:
        raise ScalarDerivationError(
            f"{operation} requires exactly two quantities.", code="invalid_arity"
        )
    if operation == "weighted_mean" and count < 2:
        raise ScalarDerivationError(
            "weighted_mean requires at least two quantities.", code="invalid_arity"
        )


def _matrix_from_input(raw: Any, *, count: int, label: str) -> np.ndarray:
    try:
        matrix = np.asarray(raw, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ScalarDerivationError(
            f"{label} must contain only finite numbers.", code="invalid_matrix"
        ) from exc
    if matrix.shape != (count, count) or not np.all(np.isfinite(matrix)):
        raise ScalarDerivationError(
            f"{label} must be a finite {count}x{count} matrix.",
            code="invalid_matrix",
        )
    # Codex review P1 (PR #46, round 16): a fixed 1e-12 floor accepted a
    # maximally asymmetric covariance whose entire variance scale was 1e-18.
    # Keep the existing relative policy, but scale it to this matrix instead
    # of one implicit unit.
    symmetry_scale = float(np.max(np.abs(matrix)))
    symmetry_tolerance = max(
        np.finfo(float).tiny,
        symmetry_scale * 1e-10,
    )
    if float(np.max(np.abs(matrix - matrix.T))) > symmetry_tolerance:
        raise ScalarDerivationError(
            f"{label} must be symmetric.", code="non_symmetric_matrix"
        )
    return matrix


def _validate_positive_semidefinite(matrix: np.ndarray) -> None:
    eigenvalues = np.linalg.eigvalsh(matrix)
    # Codex review P1 (PR #46, round 12): PSD is scale-relative. An absolute
    # 1.0 floor let a materially negative 1e-12 eigenvalue pass whenever the
    # covariance happened to be expressed in small units.
    spectral_scale = float(np.max(np.abs(eigenvalues)))
    tolerance = max(
        np.finfo(float).tiny,
        spectral_scale * len(eigenvalues) * np.finfo(float).eps,
    )
    if float(np.min(eigenvalues)) < -tolerance:
        raise ScalarDerivationError(
            "The uncertainty matrix must be positive semidefinite.",
            code="non_psd_matrix",
        )


def build_covariance(
    quantities: list[Quantity], uncertainty_model: Any
) -> tuple[np.ndarray, dict[str, Any]]:
    """Build a validated covariance matrix and a compact audit description."""
    if not isinstance(uncertainty_model, dict):
        raise ScalarDerivationError(
            "uncertainty_model must explicitly declare a kind.",
            code="missing_uncertainty_model",
        )
    kind = str(uncertainty_model.get("kind") or "").strip()
    count = len(quantities)
    standard_uncertainties = np.asarray(
        [quantity.standard_uncertainty for quantity in quantities], dtype=float
    )
    if kind == "independent":
        if uncertainty_model.get("matrix") not in (None, []):
            raise ScalarDerivationError(
                "An independent uncertainty model must not include a matrix.",
                code="unexpected_matrix",
            )
        covariance = np.diag(np.square(standard_uncertainties))
        normalized = {
            "kind": kind,
            "matrix": None,
            "source_ref": str(uncertainty_model.get("source_ref") or "").strip(),
        }
    elif kind == "correlation_matrix":
        correlation = _matrix_from_input(
            uncertainty_model.get("matrix"), count=count, label="correlation matrix"
        )
        if not np.allclose(np.diag(correlation), np.ones(count), atol=1e-10):
            raise ScalarDerivationError(
                "A correlation matrix must have ones on its diagonal.",
                code="invalid_correlation_diagonal",
            )
        _validate_positive_semidefinite(correlation)
        covariance = np.outer(standard_uncertainties, standard_uncertainties) * correlation
        normalized = {
            "kind": kind,
            "matrix": correlation.tolist(),
            "source_ref": str(uncertainty_model.get("source_ref") or "").strip(),
        }
    elif kind == "covariance_matrix":
        covariance = _matrix_from_input(
            uncertainty_model.get("matrix"), count=count, label="covariance matrix"
        )
        _validate_positive_semidefinite(covariance)
        expected_variances = np.square(standard_uncertainties)
        if not np.allclose(
            np.diag(covariance), expected_variances, rtol=1e-6, atol=0.0
        ):
            raise ScalarDerivationError(
                "Covariance diagonal must match the supplied standard uncertainties.",
                code="covariance_uncertainty_mismatch",
            )
        normalized = {
            "kind": kind,
            "matrix": covariance.tolist(),
            "source_ref": str(uncertainty_model.get("source_ref") or "").strip(),
        }
    else:
        raise ScalarDerivationError(
            "uncertainty_model.kind must be independent, correlation_matrix, or covariance_matrix.",
            code="invalid_uncertainty_model",
        )
    _require_finite_derived(covariance, field="covariance matrix")
    _validate_positive_semidefinite(covariance)
    return covariance, normalized


def _require_compatible_units(quantities: list[Quantity]) -> str:
    units = {quantity.unit for quantity in quantities}
    if len(units) != 1:
        raise ScalarDerivationError(
            "This operation requires compatible units.", code="unit_conflict"
        )
    return quantities[0].unit


def _result_unit(operation: Operation, quantities: list[Quantity]) -> str:
    if operation in {"difference", "weighted_mean"}:
        return _require_compatible_units(quantities)
    first, second = quantities
    if operation == "ratio":
        if first.unit != second.unit:
            raise ScalarDerivationError(
                "Ratio units must cancel exactly in v0.2.", code="unit_conflict"
            )
        return "dimensionless"
    if first.unit == "dimensionless":
        return second.unit
    if second.unit == "dimensionless":
        return first.unit
    return f"{first.unit} · {second.unit}"


def _rounded_display(value: float, uncertainty: float, unit: str) -> str:
    # Keep the canonical unit in the receipt, but render the common Hubble
    # unit without exponent tokens.  ``km s^-1 Mpc^-1`` is scientifically
    # conventional, yet a prose-level numeric scanner can legitimately read
    # the embedded ``-1 Mpc`` as a separate numeric claim.  The slash form is
    # equivalent, clearer in chat, and has no spurious scalar token.
    display_unit = (
        "km/s/Mpc" if unit == "km s^-1 Mpc^-1" else unit
    )
    unit_suffix = "" if display_unit == "dimensionless" else f" {display_unit}"
    return f"{value:.8g} ± {uncertainty:.8g}{unit_suffix}"


def canonical_receipt_sha256(receipt: dict[str, Any]) -> str:
    """Hash a receipt while excluding an existing receipt hash field."""
    payload = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def derive_scalar(
    *,
    operation: Operation,
    quantities: list[dict[str, Any]],
    uncertainty_model: dict[str, Any],
) -> dict[str, Any]:
    """Run one controlled derivation and return a deterministic audit payload."""
    if operation not in {"ratio", "difference", "product", "weighted_mean"}:
        raise ScalarDerivationError(
            "Unsupported scalar operation.", code="unsupported_operation"
        )
    parsed = _parse_quantities(quantities)
    _validate_operation_arity(operation, len(parsed))
    covariance, normalized_uncertainty_model = build_covariance(
        parsed, uncertainty_model
    )
    if (
        operation == "product"
        and normalized_uncertainty_model["kind"] != "independent"
    ):
        # Covariance alone does not determine Var(XY): the fourth mixed moment
        # is missing. Do not certify a first-order approximation as exact.
        raise ScalarDerivationError(
            "Exact product uncertainty requires explicitly independent inputs; "
            "a correlation or covariance matrix does not supply the higher "
            "mixed moment needed by this nonlinear operation.",
            code="nonlinear_uncertainty_requires_independence",
        )
    values = np.asarray([quantity.value for quantity in parsed], dtype=float)
    result_unit = _result_unit(operation, parsed)

    if operation == "ratio":
        if values[1] == 0:
            raise ScalarDerivationError(
                "The denominator of a ratio cannot be zero.", code="zero_denominator"
            )
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            result_value = float(values[0] / values[1])
            jacobian = np.asarray(
                [1.0 / values[1], -values[0] / values[1] ** 2]
            )
        formula = "q0 / q1; uncertainty = first-order delta method"
    elif operation == "difference":
        result_value = float(values[0] - values[1])
        jacobian = np.asarray([1.0, -1.0])
        formula = "q0 - q1"
    elif operation == "product":
        with np.errstate(over="ignore", invalid="ignore"):
            result_value = float(values[0] * values[1])
        jacobian = np.asarray([values[1], values[0]])
        formula = (
            "q0 × q1; Var = q1² Var(q0) + q0² Var(q1) + "
            "Var(q0) Var(q1) for independent inputs"
        )
    else:
        # Codex review P1 (PR #46, round 2): the pseudoinverse maps a
        # zero-variance direction to zero precision, so an exact input
        # (sigma=0) would get ZERO weight and the "weighted mean" would
        # ignore the one measurement that should pin it. Abstain on the
        # singular case instead of silently inverting the weighting.
        if any(float(covariance[i, i]) == 0.0 for i in range(len(parsed))):
            raise ScalarDerivationError(
                "A weighted mean is undefined when an input has zero "
                "uncertainty: the exact value would pin the mean, not "
                "average with it. State how the exact input should be "
                "treated, or drop it from the combination.",
                code="zero_uncertainty_weighted_mean",
            )
        ones = np.ones(len(parsed))
        # Codex review P1 (PR #46, round 9): a singular covariance can have
        # nonzero diagonal entries.  For C=[[1,2],[2,4]], pinv(C) produces a
        # nonzero-variance weighting even though a normalized nullspace
        # vector gives zero variance.  The v0.2 contract does not specify how
        # to choose among exact constrained solutions, so accept only a
        # positive-definite covariance and fail closed on every singular case.
        eigenvalues = np.linalg.eigvalsh(covariance)
        # Codex review P2 (PR #46, round 11): scale the singularity threshold
        # to the covariance spectrum. An absolute 1.0 floor rejects perfectly
        # conditioned measurements merely because their units make every
        # variance small (for example sigma=1e-6). The machine-precision floor
        # still treats numerical zero as singular without imposing a unit.
        spectral_scale = float(np.max(np.abs(eigenvalues)))
        singular_tolerance = max(
            np.finfo(float).tiny,
            spectral_scale * len(parsed) * np.finfo(float).eps,
        )
        if float(np.min(eigenvalues)) <= singular_tolerance:
            raise ScalarDerivationError(
                "A singular covariance matrix cannot define an unambiguous "
                "weighted mean in v0.2.",
                code="singular_weighted_mean",
            )
        precision_ones = np.linalg.solve(covariance, ones)
        denominator = float(ones @ precision_ones)
        if denominator <= 0 or not math.isfinite(denominator):
            raise ScalarDerivationError(
                "The covariance matrix cannot define a weighted mean.",
                code="singular_weighted_mean",
            )
        jacobian = np.asarray(precision_ones / denominator, dtype=float)
        result_value = float(jacobian @ values)
        formula = "(1ᵀ C⁻¹ q) / (1ᵀ C⁻¹ 1)"

    _require_finite_derived(result_value, field="result value")
    _require_finite_derived(jacobian, field="Jacobian")
    with np.errstate(over="ignore", invalid="ignore"):
        propagated_variance = float(jacobian @ covariance @ jacobian.T)
    if operation == "product":
        # For independent X and Y this is exact and distribution-free:
        # Var(XY)=mu_y² Var(X)+mu_x² Var(Y)+Var(X)Var(Y).
        with np.errstate(over="ignore", invalid="ignore"):
            propagated_variance += float(covariance[0, 0] * covariance[1, 1])
    _require_finite_derived(propagated_variance, field="propagated variance")
    tolerance = max(1.0, abs(result_value)) * 1e-12
    if propagated_variance < -tolerance:
        raise ScalarDerivationError(
            "Uncertainty propagation produced a negative variance.",
            code="negative_propagated_variance",
        )
    result_uncertainty = math.sqrt(max(0.0, propagated_variance))
    _require_finite_derived(
        result_uncertainty, field="standard uncertainty"
    )
    result_payload: dict[str, Any] = {
        "value": result_value,
        "standard_uncertainty": result_uncertainty,
        "unit": result_unit,
        "rounded_display": _rounded_display(
            result_value, result_uncertainty, result_unit
        ),
    }
    if operation == "ratio":
        result_payload["uncertainty_method"] = "first_order_delta"
        result_payload["approximation_caveat"] = (
            "Ratio uncertainty is a first-order delta-method approximation; "
            "means and covariance alone do not determine the exact ratio "
            "distribution or guarantee finite moments near a zero denominator."
        )
    if operation == "difference" and result_uncertainty > 0:
        standardized = abs(result_value) / result_uncertainty
        _require_finite_derived(
            standardized, field="standardized difference"
        )
        result_payload["standardized_difference_abs"] = standardized
        result_payload["standardized_difference_display"] = f"{standardized:.8g} sigma"
    if uncertainty_model.get("kind") != "independent":
        independent_covariance = np.diag(
            np.square([quantity.standard_uncertainty for quantity in parsed])
        )
        with np.errstate(over="ignore", invalid="ignore"):
            independent_variance = float(
                jacobian @ independent_covariance @ jacobian.T
            )
        _require_finite_derived(
            independent_variance, field="independent propagated variance"
        )
        independent_uncertainty = math.sqrt(max(0.0, independent_variance))
        _require_finite_derived(
            independent_uncertainty,
            field="independent standard uncertainty",
        )
        result_payload["independent_standard_uncertainty"] = independent_uncertainty
        if independent_uncertainty > 0:
            relative_change = (
                result_uncertainty / independent_uncertainty - 1.0
            )
            _require_finite_derived(
                relative_change,
                field="relative uncertainty change",
            )
            result_payload["relative_uncertainty_change_vs_independent"] = (
                relative_change
            )
            relative_change_percent = 100.0 * relative_change
            _require_finite_derived(
                relative_change_percent,
                field="relative uncertainty change percent",
            )
            result_payload[
                "relative_uncertainty_change_percent_vs_independent"
            ] = relative_change_percent
    calculation_status = (
        "linearized_approximation"
        if operation == "ratio"
        else "verified_deterministic"
    )
    result = {
        "operation": operation,
        "result": result_payload,
        "inputs": [quantity.as_dict() for quantity in parsed],
        "formula": formula,
        "jacobian": jacobian.tolist(),
        "uncertainty_model": normalized_uncertainty_model,
        "calculation_status": calculation_status,
    }
    return result
