"""Adversarial scientific guards for the analytic compressed runner."""

from __future__ import annotations

import math

import pytest


def test_analytic_runner_enforces_hard_prior_on_every_reported_interval():
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
    assert result["preliminary_ready"] is True
    h0 = result["parameters"]["H0"]
    for key in ("mean", "median", "hdi_low_94", "hdi_high_94"):
        assert low <= h0[key] <= high, (key, h0[key])
    assert h0["hdi_94"][0] >= low
    assert h0["hdi_94"][1] <= high
    prior_sampling = result["chain_diagnostics"]["prior_sampling"]
    assert prior_sampling["method"] == "exact_box_rejection"
    assert 0.0 < prior_sampling["acceptance_rate"] < 1.0


def test_planck_analytic_s8_is_derived_once_not_independently_reweighted():
    from app.services.cosmology_likelihoods import (
        get_cosmology_dataset,
        run_likelihood_chain,
    )

    result = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["planck2018_compressed"],
        n_samples=12000,
        random_seed=123,
    )

    spec = get_cosmology_dataset("planck2018_compressed").compressed_likelihood
    assert spec is not None
    names = list(spec.parameters)
    om = float(spec.mean[names.index("omegam")])
    sigma8 = float(spec.mean[names.index("sigma8")])
    om_std = math.sqrt(float(spec.covariance[names.index("omegam")][names.index("omegam")]))
    sigma8_std = math.sqrt(
        float(spec.covariance[names.index("sigma8")][names.index("sigma8")])
    )
    # Delta-method variance for S8=sigma8*sqrt(Omega_m/0.3), using exactly
    # the diagonal Omega_m/sigma8 Gaussian that the analytic path executes.
    d_sigma8 = math.sqrt(om / 0.3)
    d_om = sigma8 / (2.0 * math.sqrt(0.3 * om))
    expected_std = math.sqrt(
        (d_sigma8 * sigma8_std) ** 2 + (d_om * om_std) ** 2
    )

    assert result["publication_ready"] is False
    assert result["preliminary_ready"] is True
    assert result["parameters"]["S8"]["std"] == pytest.approx(
        expected_std, rel=0.08
    )
    # The old extra S8 Gaussian shrank this to about 0.0086.
    assert result["parameters"]["S8"]["std"] > 0.0105
    assert result["fit_statistics"]["n_constraints"] == 3
    assert any("not multiplied" in warning for warning in result["warnings"])


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
    assert "too little posterior mass" in " ".join(result["warnings"])
