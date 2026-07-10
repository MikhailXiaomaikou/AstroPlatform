"""M3 acceptance: kelly07_linmix_fit recovery accuracy on synthetic data with known ground truth.

This is the hard test for whether the fit_line_lfr Bayesian path is actually correct.
We construct a synthetic LFR sample of N=80 with known (alpha, beta, sigma_int),
add two-axis Gaussian noise, run linmix, and check whether the 94% HDI covers the
true values and ESS passes the publication threshold.

The test intentionally uses a short chain (miniter=2000, maxiter=8000) to keep CI
time under 1 minute; in real usage fit_line_lfr defaults to miniter=4000 / maxiter=20000.
Small N + short chain + large variance is the "harshest yet still reasonable" setting;
if this fails, the math or interface has a problem.
"""

import numpy as np
import pytest

from app.services.bayesian_inference import kelly07_linmix_fit


@pytest.mark.timeout(120)
def test_linmix_recovers_known_alpha_beta_sigma_int():
    rng = np.random.default_rng(20260426)
    N = 80
    true_alpha, true_beta, true_sigma = 1.2, 1.6, 0.25

    # latent xi with mild non-gaussianity (mixture of two gaussians)
    half = N // 2
    xi = np.concatenate([
        rng.normal(-0.5, 0.7, half),
        rng.normal(+0.8, 0.6, N - half),
    ])
    rng.shuffle(xi)
    eta = true_alpha + true_beta * xi + rng.normal(0, true_sigma, N)
    xerr = np.abs(rng.normal(0.10, 0.02, N))
    yerr = np.abs(rng.normal(0.15, 0.03, N))
    x = xi + rng.normal(0, xerr)
    y = eta + rng.normal(0, yerr)

    out = kelly07_linmix_fit(
        x=x, y=y, xerr=xerr, yerr=yerr,
        K=2, nchains=4,
        miniter=2000, maxiter=8000,
        seed=42, parallelize=False,
    )

    # ── Sanity on shape ─────────────────────────────────────────────
    assert out["method"] == "bayesian_xyerr_linmix"
    assert "parameters" in out
    for name in ("alpha", "beta", "sigma_int"):
        p = out["parameters"][name]
        assert {"mean", "std", "median", "hdi_low_94", "hdi_high_94", "ess"} <= set(p.keys())

    # ── HDI must cover the true value ───────────────────────────────
    a = out["parameters"]["alpha"]
    b = out["parameters"]["beta"]
    s = out["parameters"]["sigma_int"]
    assert a["hdi_low_94"] <= true_alpha <= a["hdi_high_94"], (
        f"alpha 94% HDI [{a['hdi_low_94']}, {a['hdi_high_94']}] missed true {true_alpha}"
    )
    assert b["hdi_low_94"] <= true_beta <= b["hdi_high_94"], (
        f"beta 94% HDI [{b['hdi_low_94']}, {b['hdi_high_94']}] missed true {true_beta}"
    )
    assert s["hdi_low_94"] <= true_sigma <= s["hdi_high_94"], (
        f"sigma_int 94% HDI [{s['hdi_low_94']}, {s['hdi_high_94']}] missed true {true_sigma}"
    )

    # ── Convenience aliases must agree with parameters ──────────────
    assert out["alpha_median"] == a["median"]
    assert out["beta_median"] == b["median"]
    assert out["intrinsic_scatter_dex"] == s["median"]
    assert out["intrinsic_scatter_dex_hdi"][0] == s["hdi_low_94"]
    assert out["intrinsic_scatter_dex_hdi"][1] == s["hdi_high_94"]


@pytest.mark.timeout(60)
def test_linmix_rejects_undersized_input():
    """< 5 rows → ValueError before we even spin up the sampler."""
    with pytest.raises(ValueError, match="at least 5 rows"):
        kelly07_linmix_fit(
            x=np.array([1.0, 2.0]),
            y=np.array([1.5, 2.5]),
            xerr=np.array([0.1, 0.1]),
            yerr=np.array([0.1, 0.1]),
        )


@pytest.mark.timeout(60)
def test_linmix_validates_array_shapes():
    with pytest.raises(ValueError, match="same shape"):
        kelly07_linmix_fit(
            x=np.array([1.0, 2.0, 3.0, 4.0, 5.0]),
            y=np.array([1.0, 2.0, 3.0, 4.0]),  # short by 1
            xerr=np.array([0.1] * 5),
            yerr=np.array([0.1] * 5),
        )


@pytest.mark.timeout(120)
def test_linmix_seed_reproducibility():
    """Same seed → identical posterior medians."""
    rng = np.random.default_rng(123)
    N = 30
    xi = rng.normal(0, 1, N)
    eta = 1.0 + 1.5 * xi + rng.normal(0, 0.2, N)
    xerr = np.full(N, 0.1)
    yerr = np.full(N, 0.15)
    x = xi + rng.normal(0, xerr)
    y = eta + rng.normal(0, yerr)

    out1 = kelly07_linmix_fit(x=x, y=y, xerr=xerr, yerr=yerr,
                              K=2, nchains=2, miniter=1000, maxiter=4000,
                              seed=99, parallelize=False)
    out2 = kelly07_linmix_fit(x=x, y=y, xerr=xerr, yerr=yerr,
                              K=2, nchains=2, miniter=1000, maxiter=4000,
                              seed=99, parallelize=False)
    # Every non-parallel chain now receives RandomState(seed + chain_index),
    # so the retained posterior and stopping iteration must be replayable.
    for name in ("alpha", "beta", "sigma_int"):
        p1 = out1["parameters"][name]
        p2 = out2["parameters"][name]
        assert p1 == p2
    assert out1["iterations"] == out2["iterations"]
    assert out1["random_seed"] == out2["random_seed"] == 99


@pytest.mark.timeout(60)
def test_linmix_maxiter_stop_is_not_reported_converged(monkeypatch):
    from app.services._vendored.linmix import LinMix

    monkeypatch.setattr(LinMix, "_get_Rhat", lambda self: np.full(6, 2.0))
    rng = np.random.default_rng(321)
    x = rng.normal(size=24)
    y = 0.5 + 1.2 * x + rng.normal(0, 0.4, size=x.size)
    err = np.full(x.size, 0.15)

    out = kelly07_linmix_fit(
        x=x,
        y=y,
        xerr=err,
        yerr=err,
        K=2,
        nchains=4,
        miniter=100,
        maxiter=100,
        seed=17,
        parallelize=False,
    )

    assert out["iterations"] == 100
    assert out["converged"] is False
    assert out["publication_ready"] is False
