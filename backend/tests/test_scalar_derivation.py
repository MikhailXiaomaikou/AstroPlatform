from __future__ import annotations

import math

import pytest

from app.services.scalar_derivation import (
    ScalarDerivationError,
    canonical_receipt_sha256,
    derive_scalar,
)


def _quantity(
    quantity_id: str,
    value: float,
    uncertainty: float,
    *,
    unit: str = "Mpc",
) -> dict:
    return {
        "id": quantity_id,
        "label": quantity_id,
        "value": value,
        "standard_uncertainty": uncertainty,
        "unit": unit,
        "source_ref": "desi-dr2",
        "source_locator": "Table 4, LRG2",
    }


def test_desi_ratio_with_correlation_matches_preregistered_value() -> None:
    result = derive_scalar(
        operation="ratio",
        quantities=[
            _quantity("D_M", 17.351, 0.177),
            _quantity("D_H", 19.455, 0.330),
        ],
        uncertainty_model={
            "kind": "correlation_matrix",
            "matrix": [[1.0, -0.404], [-0.404, 1.0]],
            "source_ref": "desi-dr2-table-4",
        },
    )

    assert result["calculation_status"] == "verified_deterministic"
    assert result["result"]["unit"] == "dimensionless"
    assert result["result"]["value"] == pytest.approx(0.891852994, abs=1e-9)
    assert result["result"]["standard_uncertainty"] == pytest.approx(
        0.020562805, abs=1e-9
    )
    assert result["result"]["independent_standard_uncertainty"] == pytest.approx(
        0.017652837, abs=1e-9
    )
    assert result["result"]["relative_uncertainty_change_vs_independent"] == pytest.approx(
        0.165, abs=0.001
    )
    assert result["result"][
        "relative_uncertainty_change_percent_vs_independent"
    ] == pytest.approx(16.5, abs=0.1)


def test_negative_correlation_increases_ratio_uncertainty() -> None:
    quantities = [
        _quantity("D_M", 17.351, 0.177),
        _quantity("D_H", 19.455, 0.330),
    ]
    correlated = derive_scalar(
        operation="ratio",
        quantities=quantities,
        uncertainty_model={
            "kind": "correlation_matrix",
            "matrix": [[1.0, -0.404], [-0.404, 1.0]],
        },
    )
    independent = derive_scalar(
        operation="ratio",
        quantities=quantities,
        uncertainty_model={"kind": "independent"},
    )

    assert correlated["result"]["standard_uncertainty"] > independent["result"][
        "standard_uncertainty"
    ]
    underestimation = (
        correlated["result"]["standard_uncertainty"]
        / independent["result"]["standard_uncertainty"]
        - 1
    )
    assert underestimation == pytest.approx(0.165, abs=0.005)


@pytest.mark.parametrize(
    ("operation", "expected_value", "expected_uncertainty", "expected_unit"),
    [
        ("difference", 2.0, math.sqrt(0.05), "Mpc"),
        ("product", 15.0, math.sqrt(1.09), "Mpc · Mpc"),
    ],
)
def test_two_quantity_operations(
    operation: str,
    expected_value: float,
    expected_uncertainty: float,
    expected_unit: str,
) -> None:
    result = derive_scalar(
        operation=operation,
        quantities=[_quantity("a", 5.0, 0.1), _quantity("b", 3.0, 0.2)],
        uncertainty_model={"kind": "independent"},
    )

    assert result["result"]["value"] == expected_value
    assert result["result"]["standard_uncertainty"] == pytest.approx(
        expected_uncertainty
    )
    assert result["result"]["unit"] == expected_unit


def test_weighted_mean_uses_generalized_inverse_covariance() -> None:
    result = derive_scalar(
        operation="weighted_mean",
        quantities=[
            _quantity("a", 10.0, 1.0, unit="km/s/Mpc"),
            _quantity("b", 14.0, 2.0, unit="km s^-1 Mpc^-1"),
        ],
        uncertainty_model={"kind": "independent"},
    )

    assert result["result"]["value"] == pytest.approx(10.8)
    assert result["result"]["standard_uncertainty"] == pytest.approx(
        math.sqrt(0.8)
    )


def test_difference_reports_a_standardized_difference_when_defined() -> None:
    result = derive_scalar(
        operation="difference",
        quantities=[
            _quantity("act_h0", 67.6, 1.2, unit="km/s/Mpc"),
            _quantity("reference_h0", 73.0, 0.0, unit="km/s/Mpc"),
        ],
        uncertainty_model={"kind": "independent"},
    )

    assert result["result"]["value"] == pytest.approx(-5.4)
    assert result["result"]["standardized_difference_abs"] == pytest.approx(4.5)
    assert result["result"]["unit"] == "km s^-1 Mpc^-1"
    assert result["result"]["rounded_display"].endswith("km/s/Mpc")


@pytest.mark.parametrize(
    ("uncertainty_model", "code"),
    [
        ({"kind": "correlation_matrix", "matrix": [[1, 0.2], [0.1, 1]]}, "non_symmetric_matrix"),
        ({"kind": "correlation_matrix", "matrix": [[1, 2], [2, 1]]}, "non_psd_matrix"),
        ({"kind": "correlation_matrix", "matrix": [[2, 0], [0, 1]]}, "invalid_correlation_diagonal"),
        ({"kind": "covariance_matrix", "matrix": [[1, 0], [0, 1]]}, "covariance_uncertainty_mismatch"),
    ],
)
def test_invalid_uncertainty_matrices_fail_closed(
    uncertainty_model: dict, code: str
) -> None:
    with pytest.raises(ScalarDerivationError) as exc_info:
        derive_scalar(
            operation="difference",
            quantities=[_quantity("a", 5.0, 0.1), _quantity("b", 3.0, 0.2)],
            uncertainty_model=uncertainty_model,
        )

    assert exc_info.value.code == code


def test_unit_conflict_abstains_instead_of_converting_silently() -> None:
    with pytest.raises(ScalarDerivationError) as exc_info:
        derive_scalar(
            operation="difference",
            quantities=[
                _quantity("a", 5.0, 0.1, unit="Mpc"),
                _quantity("b", 3.0, 0.2, unit="km/s/Mpc"),
            ],
            uncertainty_model={"kind": "independent"},
        )

    assert exc_info.value.code == "unit_conflict"


def test_independence_must_be_explicit() -> None:
    with pytest.raises(ScalarDerivationError) as exc_info:
        derive_scalar(
            operation="ratio",
            quantities=[_quantity("a", 5.0, 0.1), _quantity("b", 3.0, 0.2)],
            uncertainty_model={},
        )

    assert exc_info.value.code == "invalid_uncertainty_model"


def test_receipt_hash_is_stable_and_excludes_existing_hash() -> None:
    receipt = {"schema_version": 1, "result": {"value": 1.0}}
    first = canonical_receipt_sha256(receipt)
    second = canonical_receipt_sha256({**receipt, "receipt_sha256": "stale"})

    assert first == second
    assert len(first) == 64


def test_weighted_mean_rejects_zero_uncertainty_inputs() -> None:
    # Codex review P1 (PR #46, round 2): pinv treats a zero-variance input as
    # zero precision, so an exact measurement 10 +/- 0 got ZERO weight and the
    # weighted mean returned 20 +/- 1 as verified_deterministic. The singular
    # case must abstain instead of silently inverting the weighting.
    with pytest.raises(ScalarDerivationError) as exc_info:
        derive_scalar(
            operation="weighted_mean",
            quantities=[
                _quantity("exact", 10.0, 0.0, unit="km/s/Mpc"),
                _quantity("measured", 20.0, 1.0, unit="km/s/Mpc"),
            ],
            uncertainty_model={"kind": "independent"},
        )

    assert exc_info.value.code == "zero_uncertainty_weighted_mean"


def test_weighted_mean_rejects_singular_nonzero_covariance() -> None:
    # Codex review P1 (PR #46, round 9): with sigma=(1, 2) and rho=1,
    # C=[[1, 2], [2, 4]] is singular despite its nonzero diagonal.  The
    # pseudoinverse returns a non-minimum-variance estimator, while weights
    # (2, -1) lie in C's nullspace and have exactly zero variance.  Fail
    # closed instead of reporting the pseudoinverse result as deterministic.
    with pytest.raises(ScalarDerivationError) as exc_info:
        derive_scalar(
            operation="weighted_mean",
            quantities=[
                _quantity("a", 10.0, 1.0, unit="km/s/Mpc"),
                _quantity("b", 14.0, 2.0, unit="km/s/Mpc"),
            ],
            uncertainty_model={
                "kind": "correlation_matrix",
                "matrix": [[1.0, 1.0], [1.0, 1.0]],
            },
        )

    assert exc_info.value.code == "singular_weighted_mean"


def test_weighted_mean_accepts_small_scale_positive_definite_covariance() -> None:
    # Codex review P2 (PR #46, round 11): singularity is about conditioning,
    # not absolute units. Two independent 1e-6 uncertainties have a valid
    # covariance with 1e-12 eigenvalues and must not be rejected.
    result = derive_scalar(
        operation="weighted_mean",
        quantities=[
            _quantity("a", 10.0, 1e-6, unit="km/s/Mpc"),
            _quantity("b", 14.0, 1e-6, unit="km/s/Mpc"),
        ],
        uncertainty_model={"kind": "independent"},
    )

    assert result["result"]["value"] == pytest.approx(12.0)
    assert result["result"]["standard_uncertainty"] == pytest.approx(
        1e-6 / math.sqrt(2.0)
    )


def test_small_scale_non_psd_covariance_fails_closed() -> None:
    # Codex review P1 (PR #46, round 12): the absolute PSD tolerance allowed
    # a covariance with eigenvalue -1e-12 to produce zero uncertainty.
    with pytest.raises(ScalarDerivationError) as exc_info:
        derive_scalar(
            operation="difference",
            quantities=[
                _quantity("a", 10.0, 1e-6, unit="km/s/Mpc"),
                _quantity("b", 0.0, 1e-6, unit="km/s/Mpc"),
            ],
            uncertainty_model={
                "kind": "covariance_matrix",
                "matrix": [[1e-12, 2e-12], [2e-12, 1e-12]],
            },
        )

    assert exc_info.value.code == "non_psd_matrix"
