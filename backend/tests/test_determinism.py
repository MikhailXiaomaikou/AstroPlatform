"""Determinism smoke tests (Phase 2 / R8).

Locks the contract that stochastic tools produce bit-identical outputs for
the same seed.  If either of these tests starts failing, it means a new
upstream library switched to a non-seedable RNG or a code path added an
un-seeded random call.
"""

from __future__ import annotations

import numpy as np
import pytest


@pytest.mark.timeout(60)
def test_bayesian_fit_is_deterministic_with_fixed_seed():
    """Two runs with the same seed must agree to within float precision."""
    from app.pipeline.nodes.bayesian_fit import bayesian_fit

    # Small synthetic dataset — a noisy line y = 2x + 1.
    np.random.seed(0)
    x = np.linspace(0, 10, 50)
    true_line = 2.0 * x + 1.0
    noise = np.random.normal(0, 0.5, size=50)
    y = (true_line + noise).tolist()
    y_err = (np.ones_like(x) * 0.5).tolist()

    params = {
        "method": "mcmc",
        "model": "polynomial",
        "n_params": 2,
        "n_walkers": 16,
        "n_steps": 200,
        "n_burn": 50,
        "random_seed": 12345,
    }
    input_data = {"x": x.tolist(), "y": y, "y_err": y_err}

    r1 = bayesian_fit(input_data, params)
    r2 = bayesian_fit(input_data, params)

    assert "samples" in r1 and "samples" in r2, (r1, r2)
    s1 = np.asarray(r1["samples"])
    s2 = np.asarray(r2["samples"])
    assert s1.shape == s2.shape
    # Same seed → same samples.  Use allclose to tolerate library-level
    # floating-point reassociation; should be exact in practice.
    assert np.allclose(s1, s2, rtol=0, atol=0), "Samples must be bit-identical for same seed"

    # The result must record the seed so a later replay can reproduce it.
    assert r1.get("random_seed") == 12345


@pytest.mark.timeout(60)
def test_posterior_predictive_check_respects_random_seed():
    from app.services.bayesian_inference import posterior_predictive_check

    samples = np.random.default_rng(1).normal(size=(200, 2)).tolist()

    def model(theta):
        return np.array([theta[0] + theta[1]])

    def observation_sampler(theta, rng):
        return model(theta) + rng.normal(0.0, 0.2, size=1)

    obs = [1.5]

    r1 = posterior_predictive_check(
        model, samples, obs, n_samples=30, random_seed=42,
        observation_sampler=observation_sampler,
    )
    r2 = posterior_predictive_check(
        model, samples, obs, n_samples=30, random_seed=42,
        observation_sampler=observation_sampler,
    )
    r3 = posterior_predictive_check(
        model, samples, obs, n_samples=30, random_seed=7,
        observation_sampler=observation_sampler,
    )

    # The function exposes summary stats, not raw predictions.  Same seed
    # must yield identical mean/std; a different seed must differ.
    p1_mean = np.asarray(r1["predicted_mean"])
    p2_mean = np.asarray(r2["predicted_mean"])
    p3_mean = np.asarray(r3["predicted_mean"])
    assert np.allclose(p1_mean, p2_mean)
    assert not np.allclose(p1_mean, p3_mean)
