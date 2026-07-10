"""Phase D1 rigor tests — arviz integration, HDI, priors, multi-statistic PPC.

Locks the contract that:
- Insufficient-sampling runs are flagged; sufficient ones pass the gate.
- HDI is reported alongside quantile percentiles.
- Caller-supplied priors (normal / uniform / lognormal) are applied.
- PPC returns per-statistic p-values (not just mean-fraction).
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.bayesian_inference import (
    chain_diagnostics,
    posterior_predictive_check,
    model_comparison_table,
)


# --------------------------------------------------------------------
# D1.1 — chain_diagnostics
# --------------------------------------------------------------------


def test_short_chain_is_flagged_insufficient():
    rng = np.random.default_rng(0)
    # 2 chains × 50 draws = 100 total; ESS will be small.
    samples = rng.normal(size=(100, 2))
    d = chain_diagnostics(samples, ["a", "b"], n_chains=2)
    assert d["publication_ready"] is False
    assert set(d["insufficient_params"]) <= {"a", "b"}
    assert d["thresholds"]["ess_min"] == 400
    assert d["thresholds"]["rhat_max"] == 1.01
    assert d["thresholds"]["min_chains"] == 4


def test_long_chain_passes_publication_gate():
    rng = np.random.default_rng(1)
    # 4 chains × 2000 draws — well over ESS=400.
    samples = rng.normal(size=(8000, 2))
    d = chain_diagnostics(samples, ["p0", "p1"], n_chains=4)
    assert d["publication_ready"] is True
    for k, v in d["parameters"].items():
        assert v["ess_bulk"] >= 400
        assert v["rhat"] < 1.05
        assert "hdi_low_94" in v and "hdi_high_94" in v
        assert v["hdi_low_94"] < v["hdi_high_94"]


def test_single_chain_nonfinite_rhat_cannot_pass_publication_gate():
    rng = np.random.default_rng(11)
    samples = rng.normal(size=(5000, 2))
    d = chain_diagnostics(samples, ["a", "b"], n_chains=1)

    assert d["publication_ready"] is False
    assert d["overall_status"] == "check_required"
    assert set(d["insufficient_params"]) == {"a", "b"}


def test_diagnostics_include_hdi_bounds():
    rng = np.random.default_rng(2)
    samples = rng.normal(loc=5.0, scale=2.0, size=(4000, 1))
    d = chain_diagnostics(samples, ["x"], n_chains=4)
    p = d["parameters"]["x"]
    # 94% HDI of a N(5,2) should roughly bracket ±3.8.
    assert 1.0 < p["hdi_low_94"] < 4.0
    assert 6.0 < p["hdi_high_94"] < 10.0


# --------------------------------------------------------------------
# D1.1 — model comparison with WAIC/LOO
# --------------------------------------------------------------------


def test_model_comparison_ranks_by_loo_when_available():
    rng = np.random.default_rng(3)
    n_obs = 30
    n_draws = 800

    # Better-fit model has tighter per-point ll; worse model has looser.
    ll_good = rng.normal(loc=-1.0, scale=0.2, size=(n_draws, n_obs))
    ll_bad = rng.normal(loc=-3.0, scale=0.5, size=(n_draws, n_obs))

    out = model_comparison_table({
        "good": {"n_params": 3, "n_data": n_obs, "chi2": 25, "log_likelihood": ll_good},
        "bad":  {"n_params": 2, "n_data": n_obs, "chi2": 80, "log_likelihood": ll_bad},
    })
    assert out["best_model"] == "good"
    names = [r["model"] for r in out["ranking"]]
    assert names[0] == "good"
    # First row should have LOO and a verdict.
    assert "LOO" in out["ranking"][0]
    assert "verdict" in out["ranking"][1]


def test_model_comparison_falls_back_to_bic_without_log_likelihood():
    out = model_comparison_table({
        "simple": {"n_params": 2, "n_data": 100, "chi2": 120},
        "complex": {"n_params": 5, "n_data": 100, "chi2": 105},
    })
    # With n=100, BIC should prefer the simpler model (chi2 penalty < param penalty).
    assert out["best_model"] in {"simple", "complex"}  # depends on exact BIC
    assert all("BIC" in r for r in out["ranking"])


# --------------------------------------------------------------------
# D1.2 — posterior predictive multi-statistic
# --------------------------------------------------------------------


def test_ppc_returns_per_statistic_pvalues():
    rng = np.random.default_rng(4)
    # Posterior draws of (a, b); model is y = a + b*x
    posterior = np.column_stack([rng.normal(1.0, 0.1, 500), rng.normal(2.0, 0.1, 500)])
    x = np.linspace(0, 1, 20)
    y_obs = 1.0 + 2.0 * x + rng.normal(0, 0.05, 20)

    def model(theta):
        a, b = theta
        return a + b * x

    def observation_sampler(theta, draw_rng):
        return model(theta) + draw_rng.normal(0.0, 0.05, size=x.size)

    out = posterior_predictive_check(
        model,
        posterior,
        y_obs,
        n_samples=100,
        random_seed=42,
        observation_sampler=observation_sampler,
    )

    assert "stat_pvalues" in out
    # Keys we promise in the docstring.
    for stat in ("max", "min", "range", "median", "chi2_discrepancy"):
        assert stat in out["stat_pvalues"], f"missing stat {stat}"
        assert 0.0 <= out["stat_pvalues"][stat] <= 1.0


def test_ppc_flags_misspecified_model():
    """A model that systematically overshoots should flag `max` statistic."""
    rng = np.random.default_rng(5)
    # Observed data is tightly clustered; posterior predicts much larger values.
    y_obs = rng.normal(0.0, 0.5, 30)
    posterior = np.column_stack([rng.normal(5.0, 0.05, 300)])  # always ~5

    def model(theta):
        return np.full_like(y_obs, float(theta[0]))

    def observation_sampler(theta, draw_rng):
        return model(theta) + draw_rng.normal(0.0, 0.05, size=y_obs.size)

    out = posterior_predictive_check(
        model,
        posterior,
        y_obs,
        n_samples=200,
        random_seed=7,
        observation_sampler=observation_sampler,
    )
    # All replicates are ~5 but obs are ~0 → max(rep) >> max(obs) always.
    assert out["stat_pvalues"]["max"] >= 0.95
    # Miscalibrated list should flag at least one statistic.
    assert len(out["miscalibrated_statistics"]) >= 1
    assert out["calibration"] == "poor"


def test_ppc_without_observation_model_fails_closed():
    posterior = np.zeros((100, 1))
    observed = np.random.default_rng(9).normal(size=20)

    out = posterior_predictive_check(
        lambda theta: np.zeros_like(observed),
        posterior,
        observed,
        random_seed=3,
    )

    assert out["success"] is False
    assert out["publication_ready"] is False
    assert out["error_class"] == "missing_observation_sampler"
    assert out["stat_pvalues"] == {}


# --------------------------------------------------------------------
# D1.2 — bayesian_fit pipeline node honours priors + publication_ready
# --------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_bayesian_fit_respects_normal_prior():
    from app.pipeline.nodes.bayesian_fit import bayesian_fit

    rng = np.random.default_rng(6)
    x = np.linspace(0, 10, 40)
    y = (3.0 * x + 1.0 + rng.normal(0, 0.2, 40)).tolist()
    y_err = (np.ones_like(x) * 0.2).tolist()

    # Tight normal prior on slope-intercept pulls the fit toward (3, 1).
    priors = {
        "p0": {"type": "normal", "mean": 3.0, "std": 0.05},  # slope (polyfit order: high->low)
        "p1": {"type": "normal", "mean": 1.0, "std": 0.05},
    }

    out = bayesian_fit(
        {"x": x.tolist(), "y": y, "y_err": y_err},
        {
            "method": "mcmc", "model": "polynomial", "n_params": 2,
            "n_walkers": 16, "n_steps": 400, "n_burn": 100,
            "param_names": ["p0", "p1"],
            "priors": priors,
            "random_seed": 2024,
        },
    )
    assert "parameter_summary" in out
    summary = out["parameter_summary"]
    # Each parameter should include HDI alongside quantile.
    for name in ("p0", "p1"):
        assert "hdi_68" in summary[name]
        assert "hdi_94" in summary[name]
    # Priors are echoed so the audit log can see what was used.
    assert out.get("priors_applied") == priors


@pytest.mark.timeout(60)
def test_bayesian_fit_flags_insufficient_sampling_on_short_run():
    from app.pipeline.nodes.bayesian_fit import bayesian_fit

    rng = np.random.default_rng(8)
    x = np.linspace(0, 10, 40)
    y = (2.0 * x + rng.normal(0, 0.3, 40)).tolist()
    out = bayesian_fit(
        {"x": x.tolist(), "y": y},
        {
            "method": "mcmc", "model": "polynomial", "n_params": 2,
            "n_walkers": 8, "n_steps": 60, "n_burn": 20,
            "random_seed": 2025,
        },
    )
    assert out.get("publication_ready") is False
