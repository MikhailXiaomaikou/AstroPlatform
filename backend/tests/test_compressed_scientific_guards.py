"""Adversarial scientific guards for the analytic compressed runner."""

from __future__ import annotations

def test_narrow_caller_prior_is_flagged_and_blocked():
    from app.services.cosmology_likelihoods import run_likelihood_chain

    low, high = 67.3, 67.4
    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["planck2018_compressed"],
        priors={"H0": [low, high]},
        n_samples=4000,
        random_seed=42,
    )

    assert result["success"] is True
    assert result["publication_ready"] is False
    assert result["preliminary_ready"] is False
    assert result["chain_tier"] == "blocked"
    assert result["__do_not_claim__"] is True
    assert "parameters" not in result
    assert result["prior_dominance_screen"]["screen_passed"] is False
    assert "H0" in result["prior_dominance_screen"]["flagged_parameters"]


def test_planck_posterior_rows_are_proposal_only_not_reported_constraints():
    from app.services.cosmology_likelihoods import run_likelihood_chain

    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["planck2018_compressed"],
        n_samples=12000,
        random_seed=123,
    )

    assert result["publication_ready"] is False
    assert result["preliminary_ready"] is True
    assert set(result["parameters"]) == {"H0", "omegam", "ombh2", "ns"}
    assert "sigma8" not in result["parameters"]
    assert "S8" not in result["derived_params"]
    assert result["fit_statistics"]["n_constraints"] == 4
    source = result["provenance"]["cosmology_likelihood"]["compressed_sources"][0]
    assert source["executed_component"]["statistical_role"] == "likelihood_approximation"
    assert source["executed_component"]["parameters"] == ["R", "l_A", "ombh2", "ns"]
    assert source["registered_parameter_block"]["statistical_role"] == "proposal_only"
    assert source["proposal_rows_not_executed"] is True


def test_tiny_far_tail_prior_fails_closed_instead_of_leaking_gaussian_draws():
    from app.services.cosmology_likelihoods import run_likelihood_chain

    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["planck2018_compressed"],
        priors={"H0": [89.9, 90.0]},
        n_samples=256,
        random_seed=42,
    )

    assert result["success"] is True
    assert result["publication_ready"] is False
    assert result["__do_not_claim__"] is True
    assert "parameters" not in result
    assert "ESS=1.0 below publication threshold" in " ".join(result["warnings"])
