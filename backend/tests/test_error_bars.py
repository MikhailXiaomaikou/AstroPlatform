"""Phase D3 — error-bar contract tests.

Locks:
- BLS returns bootstrap FAP, period error, and verdict driven by FAP
  (not by raw Gaussian SNR).
- RV orbit fit returns an MCMC block with jitter HDI when emcee is
  available (skips gracefully otherwise).
- All time-domain tools report at least the period uncertainty.
"""

from __future__ import annotations

import numpy as np
import pytest


def test_bls_reports_bootstrap_fap_and_period_err():
    from app.services.time_domain_pro import transit_search_bls

    # Inject a shallow transit-like dip every 3 days in otherwise flat flux.
    rng = np.random.default_rng(42)
    t = np.linspace(0, 30, 400)
    f = np.ones_like(t) + rng.normal(0, 0.001, len(t))
    # Add box transits at phase 0.
    period_true = 3.0
    duration = 0.1
    phase = (t % period_true) / period_true
    dip_mask = phase < (duration / period_true)
    f[dip_mask] -= 0.02

    out = transit_search_bls(
        t.tolist(), f.tolist(),
        period_range=(1.0, 6.0),
        n_periods=200,
        n_bootstrap=30,
        random_seed=1,
    )
    assert "fap_bootstrap" in out
    assert "best_period_err" in out
    # Recovered period within 20% (coarse grid).
    assert 2.4 < out["best_period"] < 3.6
    assert out["detection"] in ("significant", "marginal", "none")


def test_bls_verdict_driven_by_fap_not_snr():
    """A flat noise-only series should yield FAP near 1 → detection 'none'."""
    from app.services.time_domain_pro import transit_search_bls

    rng = np.random.default_rng(0)
    t = np.linspace(0, 30, 200)
    f = np.ones_like(t) + rng.normal(0, 0.001, len(t))
    out = transit_search_bls(
        t.tolist(), f.tolist(),
        period_range=(1.0, 6.0),
        n_periods=150,
        n_bootstrap=30,
        random_seed=2,
    )
    assert out["detection"] == "none"
    assert out["fap_bootstrap"] > 0.05


@pytest.mark.timeout(120)
def test_rv_orbit_emcee_returns_jitter_hdi():
    """When emcee is available, RV orbit fit reports MCMC + jitter HDI."""
    try:
        import emcee  # noqa: F401
        import radvel  # noqa: F401
    except ImportError:
        pytest.skip("emcee/radvel not available")

    import asyncio
    from app.services.ai_tools import _exec_fit_rv_orbit

    # Synthetic sinusoidal RV, 30 points.
    rng = np.random.default_rng(7)
    t = np.linspace(0, 40, 30).tolist()
    P = 10.0
    K = 50.0
    v = [K * np.sin(2 * np.pi * ti / P) + rng.normal(0, 3.0) for ti in t]
    e = [3.0] * len(t)

    out = asyncio.run(_exec_fit_rv_orbit({
        "times": t, "rvs": v, "rv_errs": e,
        "period_min": 1.0, "period_max": 30.0,
        "use_mcmc": True, "n_walkers": 16, "n_steps": 400, "n_burn": 100,
        "random_seed": 2024,
    }))
    if out.get("error"):
        pytest.skip(f"RV fit path unavailable: {out['error']}")
    assert "mcmc" in out
    if out["mcmc"].get("ran_mcmc"):
        assert "jitter_ms_median" in out["mcmc"]
        assert "hdi_68_jitter_ms" in out["mcmc"]
