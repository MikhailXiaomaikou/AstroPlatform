"""Scientific publication-gate contract shared by cosmology samplers."""

from __future__ import annotations

import numpy as np


def _good_per_parameter(*names: str) -> dict[str, dict[str, float]]:
    return {
        name: {"rhat": 1.001, "ess_bulk": 800.0}
        for name in names
    }


def test_shared_gate_requires_four_chains_strict_rank_rhat_and_each_parameter_ess():
    from app.services.cosmology_likelihoods.verification import (
        _assess_publication_gate,
    )

    params = ("H0", "omegam")
    good = _assess_publication_gate(
        cov_fidelity="full",
        likelihood_is_compressed_or_approximate=False,
        n_independent_chains=4,
        per_parameter=_good_per_parameter(*params),
        critical_parameters=params,
    )
    assert good["eligible"] is True
    assert good["reasons"] == []
    assert good["thresholds"] == {
        "min_independent_chains": 4,
        "rhat_method": "rank",
        "rhat_max_exclusive": 1.01,
        "ess_method": "bulk",
        "ess_min": 400.0,
    }

    too_few = _assess_publication_gate(
        cov_fidelity="full",
        likelihood_is_compressed_or_approximate=False,
        n_independent_chains=3,
        per_parameter=_good_per_parameter(*params),
        critical_parameters=params,
    )
    assert too_few["eligible"] is False
    assert "fewer_than_four_independent_chains" in too_few["reasons"]

    boundary = _good_per_parameter(*params)
    boundary["H0"]["rhat"] = 1.01
    boundary["omegam"]["ess_bulk"] = 399.0
    failed = _assess_publication_gate(
        cov_fidelity="full",
        likelihood_is_compressed_or_approximate=False,
        n_independent_chains=4,
        per_parameter=boundary,
        critical_parameters=params,
    )
    assert failed["eligible"] is False
    assert failed["parameter_failures"]["H0"] == [
        "rank_normalized_rhat_at_or_above_1.01"
    ]
    assert failed["parameter_failures"]["omegam"] == ["bulk_ess_below_400"]


def test_shared_gate_refuses_literature_typed_and_compressed_inputs():
    from app.services.cosmology_likelihoods.verification import (
        _assess_publication_gate,
    )

    gate = _assess_publication_gate(
        cov_fidelity="literature_typed",
        likelihood_is_compressed_or_approximate=True,
        n_independent_chains=4,
        per_parameter=_good_per_parameter("H0"),
        critical_parameters=("H0",),
    )
    assert gate["eligible"] is False
    assert "literature_typed_input" in gate["reasons"]
    assert "compressed_or_approximate_likelihood" in gate["reasons"]


def test_inprocess_importance_result_is_preliminary_not_publication():
    from app.services.cosmology_likelihoods import run_likelihood_chain

    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["desi_dr1_bao"],
        n_samples=1000,
        random_seed=11,
    )
    assert result["publication_ready"] is False
    assert result["preliminary_ready"] is True
    assert result["chain_tier"] == "exploratory"
    assert result["chain_diagnostics"]["n_independent_chains"] == 0
    assert "importance_samples_are_not_independent_chains" in result[
        "preliminary_reasons"
    ]
    assert "rank_normalized_rhat_unavailable" in result["preliminary_reasons"]


def test_cobaya_diagnostics_single_chain_never_fabricates_rhat():
    from app.services.cobaya_runner import _compute_diagnostics

    rng = np.random.default_rng(7)
    diagnostics = _compute_diagnostics(
        [rng.normal(size=(1000, 2))], ["H0", "omegam"]
    )
    assert diagnostics["overall_status"] == "single_chain_only"
    assert diagnostics["rhat"] is None
    assert diagnostics["ess_bulk"] is None
    assert diagnostics["n_independent_chains"] == 1


def test_cobaya_diagnostics_two_chains_are_preliminary_four_can_pass():
    from app.services.cobaya_runner import _compute_diagnostics

    rng = np.random.default_rng(19)
    two = [rng.normal(size=(2000, 2)) for _ in range(2)]
    diagnostics_two = _compute_diagnostics(two, ["H0", "omegam"])
    assert diagnostics_two["overall_status"] == "insufficient"
    assert diagnostics_two["n_independent_chains"] == 2

    four = [rng.normal(size=(4000, 2)) for _ in range(4)]
    diagnostics_four = _compute_diagnostics(four, ["H0", "omegam"])
    assert diagnostics_four["overall_status"] == "ok", diagnostics_four
    assert diagnostics_four["n_independent_chains"] == 4
    assert all(
        record["rhat"] < 1.01 and record["ess_bulk"] >= 400
        for record in diagnostics_four["per_parameter"].values()
    )

