"""Focused locks for the deterministic Union3 reproduction workflow."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.services import union3_independent_verifier as independent
from app.services import union3_reproduction as reproduction
from app.services import union3_verification_service as verification_service
from app.services.registered_workflows import (
    UNION3_REPRODUCTION_WORKFLOW_ID,
    get_registered_workflow,
    list_registered_workflows,
)
from app.services.union3_verification_service import verify_union3_primary_result


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    return reproduction.run_union3_primary_reproduction()


@pytest.fixture(scope="module")
def verification(result: dict[str, Any]) -> dict[str, Any]:
    return verify_union3_primary_result(result)


def _assert_decimal_strings(value: Any) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, dict):
        for nested in value.values():
            _assert_decimal_strings(nested)
        return
    if isinstance(value, list):
        for nested in value:
            _assert_decimal_strings(nested)
        return
    assert isinstance(value, str), (type(value), value)


def _data_paths() -> tuple[Path, Path]:
    data_dir = Path(reproduction.__file__).resolve().parents[2] / "data" / "union3"
    return data_dir / "lcparam_full.txt", data_dir / "mag_covmat.txt"


def test_registered_workflow_is_fixed_and_defensively_copied():
    assert list_registered_workflows() == (UNION3_REPRODUCTION_WORKFLOW_ID,)
    workflow = get_registered_workflow(UNION3_REPRODUCTION_WORKFLOW_ID)
    assert workflow["primary_executor"].endswith("run_union3_primary_reproduction")
    assert workflow["independent_verifier"].endswith("verify_union3_primary_result")
    assert workflow["method"]["omega_m_min"] == "0.05"
    assert workflow["method"]["omega_m_max"] == "0.80"
    assert workflow["method"]["grid_step"] == "0.0005"
    assert workflow["method"]["radiation_convention"] == (
        "omega_r_zero_author_code_approximation"
    )
    assert workflow["sources"]["author_execution_convention"]["commit"] == (
        "7f805c9cc4e7643f0392faad03a275094501f8a2"
    )
    workflow["method"]["grid_step"] = "changed"
    assert (
        get_registered_workflow(UNION3_REPRODUCTION_WORKFLOW_ID)["method"]["grid_step"]
        == "0.0005"
    )


def test_primary_result_cannot_write_a_scientific_verdict(result: dict[str, Any]):
    assert result["status"] == "COMPLETED"
    assert result["scientific_status"] == "WITHHELD"
    assert result["review_status"] == "NOT_SUBMITTED"
    assert result["machine_support_eligible"] is False
    assert result["primary_ready"] is True
    assert result["reproduction_ready"] is False
    assert result["publication_ready"] is False
    assert all(gate["passed"] for gate in result["primary_gates"].values())


def test_reproduced_profile_matches_pinned_science_values(result: dict[str, Any]):
    statistics = result["statistics"]
    assert float(statistics["omega_m_best"]) == pytest.approx(0.3559244, abs=5e-8)
    assert float(statistics["omega_m_lower"]) == pytest.approx(0.32948065, abs=5e-8)
    assert float(statistics["omega_m_upper"]) == pytest.approx(0.38352307, abs=5e-8)
    assert float(statistics["chi_square_min"]) == pytest.approx(23.95789014, abs=5e-8)
    assert statistics["degrees_of_freedom"] == "20"


def test_half_step_and_independent_checks_are_separate(
    result: dict[str, Any], verification: dict[str, Any]
):
    assert result["half_step_statistics"]["grid_step"] == "0.00025000"
    assert result["half_step_statistics"]["grid_point_count"] == "3001"
    assert len(result["normalized_profile"]) == 41
    assert "independent_analysis" not in result
    assert len(verification["independent_analysis"]["normalized_profile"]) == 41
    assert Decimal(
        verification["verification_gates"]["independent_profile"]["measured"]
    ) <= Decimal("0.0001")
    assert Decimal(
        verification["verification_gates"]["independent_best_and_endpoints"]["measured"]
    ) <= Decimal("0.0002")
    assert verification["verification_ready"] is True
    assert verification["machine_support_eligible"] is True
    assert verification["scientific_status"] == "WITHHELD"
    assert verification["review_status"] == "PENDING"
    assert verification["reproduction_ready"] is False
    assert verification["publication_ready"] is False


def test_half_step_gate_is_derived_from_signed_endpoint_strings(
    result: dict[str, Any],
):
    statistics = result["statistics"]
    half_step = result["half_step_statistics"]
    expected = max(
        abs(Decimal(statistics[field]) - Decimal(half_step[field]))
        for field in ("omega_m_best", "omega_m_lower", "omega_m_upper")
    )
    gate = result["primary_gates"]["half_step_stability"]

    assert Decimal(gate["measured"]) == expected
    assert gate["passed"] is (expected <= Decimal(gate["threshold"]))


@pytest.mark.parametrize(
    ("primary_raw", "half_step_raw", "raw_measurement", "canonical_measurement"),
    [
        (0.3559244049, 0.3559243951, "0.00000001", "0.00000000"),
        (0.3559244051, 0.3559244049, "0.00000000", "0.00000001"),
    ],
)
def test_half_step_gate_uses_canonical_domain_across_rounding_boundaries(
    primary_raw: float,
    half_step_raw: float,
    raw_measurement: str,
    canonical_measurement: str,
):
    primary = {
        field: reproduction._decimal_string(primary_raw)
        for field in ("omega_m_best", "omega_m_lower", "omega_m_upper")
    }
    half_step = {
        field: reproduction._decimal_string(half_step_raw)
        for field in ("omega_m_best", "omega_m_lower", "omega_m_upper")
    }

    assert reproduction._decimal_string(abs(primary_raw - half_step_raw)) == (
        raw_measurement
    )
    assert (
        reproduction._decimal_string(
            reproduction._canonical_half_step_difference(primary, half_step)
        )
        == canonical_measurement
    )


def test_public_output_has_no_binary_floats_or_overclaim_language(
    result: dict[str, Any],
):
    _assert_decimal_strings(result)
    serialized = json.dumps(result, sort_keys=True).lower()
    for forbidden in ("posterior", "h0", "discovery"):
        assert forbidden not in serialized
    assert result["mcmc_diagnostics"] == "not_applicable"


def test_independent_verifier_does_not_import_production_science_paths():
    source = Path(independent.__file__).read_text(encoding="utf-8")
    assert "from app.services.union3_reproduction" not in source
    assert "import app.services.union3_reproduction" not in source
    assert "cosmology_likelihood" not in source


def test_exact_hash_mismatch_fails_closed(tmp_path: Path):
    vector_path, covariance_path = _data_paths()
    changed_vector = tmp_path / vector_path.name
    changed_vector.write_bytes(vector_path.read_bytes() + b"\n")

    result = reproduction.run_union3_primary_reproduction(
        vector_path=changed_vector,
        covariance_path=covariance_path,
    )
    assert result["status"] == "FAILED_FINAL"
    assert result["scientific_status"] == "WITHHELD"
    assert result["reproduction_ready"] is False
    assert result["publication_ready"] is False
    assert "sha256 mismatch" in result["failure_reason"]

    independent_result = independent.run_union3_independent_verification(
        vector_path=changed_vector,
        covariance_path=covariance_path,
    )
    assert independent_result["status"] == "FAILED_FINAL"
    assert independent_result["verification_ready"] is False
    assert independent_result["publication_ready"] is False


def test_reproduction_is_deterministic(result: dict[str, Any]):
    assert reproduction.run_union3_primary_reproduction() == result


def test_client_controlled_ready_flags_or_numbers_cannot_pass_verifier(
    result: dict[str, Any],
):
    forged = json.loads(json.dumps(result))
    forged["machine_support_eligible"] = True
    forged["reproduction_ready"] = True
    forged["scientific_status"] = "SUPPORTED"
    rejected = verify_union3_primary_result(forged)
    assert rejected["verification_ready"] is False
    assert rejected["machine_support_eligible"] is False
    assert rejected["scientific_status"] == "WITHHELD"

    changed_profile = json.loads(json.dumps(result))
    changed_profile["normalized_profile"][0]["normalized_chi_square"] = "0.00000000"
    rejected_profile = verify_union3_primary_result(changed_profile)
    assert rejected_profile["verification_ready"] is False
    assert rejected_profile["scientific_status"] == "WITHHELD"


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("claim", "A forged discovery claim"),
        ("method", {"statistic": "posterior"}),
        ("paper_reference", {"source": "unregistered"}),
        ("primary_gates", {"input_integrity": {"passed": False}}),
        ("review_status", "APPROVED"),
    ],
)
def test_semantically_forged_primary_contract_fails_closed(
    result: dict[str, Any], field: str, forged_value: Any
):
    forged = json.loads(json.dumps(result))
    forged[field] = forged_value
    rejected = verify_union3_primary_result(forged)
    assert rejected["failure_code"] == "PRIMARY_RESULT_INVALID"
    assert rejected["verification_ready"] is False
    assert rejected["scientific_status"] == "WITHHELD"


@pytest.mark.parametrize(
    ("extra_key", "extra_value"),
    [
        ("scientific_verdict", "SUPPORTED"),
        ("extra_claim", "We discovered evolving dark energy"),
        ("publication_ready_v2", True),
    ],
)
def test_extra_untrusted_primary_fields_fail_closed(
    result: dict[str, Any], extra_key: str, extra_value: Any
):
    forged = json.loads(json.dumps(result))
    forged[extra_key] = extra_value
    rejected = verify_union3_primary_result(forged)
    assert rejected["failure_code"] == "PRIMARY_RESULT_INVALID"
    assert rejected["verification_ready"] is False


@pytest.mark.parametrize("bad_result", [None, ["not", "an", "object"]])
def test_invalid_independent_return_fails_closed(
    result: dict[str, Any], monkeypatch: pytest.MonkeyPatch, bad_result: Any
):
    monkeypatch.setattr(
        verification_service,
        "run_union3_independent_verification",
        lambda **_kwargs: bad_result,
    )
    rejected = verify_union3_primary_result(result)
    assert rejected["failure_code"] == "INDEPENDENT_RESULT_INVALID"
    assert rejected["verification_ready"] is False


def test_independent_exception_fails_closed(
    result: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    def _explode(**_kwargs):
        raise RuntimeError("sensitive internal error")

    monkeypatch.setattr(
        verification_service, "run_union3_independent_verification", _explode
    )
    rejected = verify_union3_primary_result(result)
    assert rejected["failure_code"] == "INDEPENDENT_VERIFICATION_FAILED"
    assert rejected["failure_reason"] == "RuntimeError"
    assert rejected["verification_ready"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["normalized_profile"].__setitem__(0, "bad row"),
        lambda payload: payload["normalized_profile"][0].__setitem__(
            "omega_m", "0.06000000"
        ),
    ],
)
def test_malformed_or_shifted_independent_profile_fails_closed(
    result: dict[str, Any], monkeypatch: pytest.MonkeyPatch, mutation
):
    independent_result = independent.run_union3_independent_verification()
    mutation(independent_result)
    monkeypatch.setattr(
        verification_service,
        "run_union3_independent_verification",
        lambda **_kwargs: independent_result,
    )
    rejected = verify_union3_primary_result(result)
    assert rejected["failure_code"] == "INDEPENDENT_RESULT_INVALID"
    assert rejected["verification_ready"] is False
