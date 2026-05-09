"""Importance-sampler regression tests for compressed-likelihood combos.

Workflow 2 smoke test (2026-05-09) showed BAO + Planck-compressed CMB
collapsing ``proposal_ess`` to ~1 against the 400 publication threshold.
Root cause: ``_draw_importance_posterior`` used a uniform-on-prior-box
proposal, but Planck's compressed Gaussian σ_H0=0.54 / σ_Ωm=0.0073
occupies ~0.04% of the (H0, Ωm) prior volume, so almost no proposal
mass landed where the joint likelihood was non-negligible.

Fix: when any compressed entry has a Gaussian likelihood overlapping the
sampling parameters, draw the proposal from a 5σ-inflated multivariate
Gaussian centered on the tightest such entry, and apply the standard
importance correction ``log w = -0.5 chi² - log q(theta)``.

These tests lock the ESS recovery and prevent regressions back to the
ESS=1 state.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.cosmology_likelihoods import (
    _draw_gaussian_centered_proposal,
    _draw_importance_posterior,
    get_cosmology_dataset,
)


def _entry(key: str):
    return get_cosmology_dataset(key)


# ── Layer 1: helper picks the tightest entry and yields valid log_q ───


def test_gaussian_centered_proposal_picks_planck_when_present():
    rng = np.random.default_rng(0)
    parameter_order = ["H0", "omegam", "rd"]
    prior_bounds = {"H0": (50.0, 90.0), "omegam": (0.10, 0.50), "rd": (130.0, 165.0)}
    compressed_entries = [_entry("planck2018_compressed")]

    result = _draw_gaussian_centered_proposal(
        rng, parameter_order, prior_bounds, compressed_entries, proposal_count=2000
    )
    assert result is not None
    samples, log_q = result
    assert samples.shape == (2000, 3)
    assert log_q.shape == (2000,)

    # H0 column should sit near Planck mean 67.36, not uniform-spread to 50–90.
    assert 65.0 < float(np.mean(samples[:, 0])) < 70.0
    assert 0.5 < float(np.std(samples[:, 0])) < 5.0
    # rd is not in the Planck Gaussian, so it should be ~uniform.
    assert 130.0 <= float(np.min(samples[:, 2]))
    assert float(np.max(samples[:, 2])) <= 165.0
    rd_std = float(np.std(samples[:, 2]))
    assert rd_std > 5.0  # uniform on a 35-wide box has σ ≈ 10


def test_gaussian_centered_proposal_returns_none_without_compressed_gaussian():
    rng = np.random.default_rng(0)
    parameter_order = ["H0", "omegam", "rd"]
    prior_bounds = {"H0": (50.0, 90.0), "omegam": (0.10, 0.50), "rd": (130.0, 165.0)}
    # DESI BAO is NOT a compressed-Gaussian entry (it has chi²-only support
    # via the BAO mean/cov files), so the helper must decline.
    compressed_entries: list = []
    result = _draw_gaussian_centered_proposal(
        rng, parameter_order, prior_bounds, compressed_entries, proposal_count=1000
    )
    assert result is None


def test_gaussian_centered_proposal_returns_none_when_gaussian_does_not_intersect():
    """Compressed entry whose parameters don't overlap parameter_order is unusable."""
    rng = np.random.default_rng(0)
    parameter_order = ["w0"]  # only w0; Planck has H0/Ωm/σ8/S8, no overlap
    prior_bounds = {"w0": (-1.5, -0.5)}
    compressed_entries = [_entry("planck2018_compressed")]
    result = _draw_gaussian_centered_proposal(
        rng, parameter_order, prior_bounds, compressed_entries, proposal_count=500
    )
    assert result is None


# ── Layer 2: importance sampler ESS recovery on the canonical bug case ─


def test_importance_posterior_bao_plus_planck_recovers_ess():
    """Workflow 2 BAO+CMB regression: ESS used to be ~1, must now exceed 400."""
    rng = np.random.default_rng(42)
    parameter_order = ["H0", "omegam", "rd"]
    prior_bounds = {"H0": (50.0, 90.0), "omegam": (0.10, 0.50), "rd": (130.0, 165.0)}
    bao_entries = [_entry("desi_dr1_bao")]
    compressed_entries = [_entry("planck2018_compressed")]

    posterior, best_chi2, proposal_ess, n_samples, errors = _draw_importance_posterior(
        rng, parameter_order, prior_bounds,
        bao_entries, compressed_entries, sample_count=4000,
    )
    assert errors == [], f"Compressed-likelihood errors: {errors}"
    assert proposal_ess > 400.0, (
        f"BAO+Planck ESS={proposal_ess:.1f} regressed below the 400 publication "
        f"threshold; the Gaussian-centered proposal should keep ESS at thousands."
    )
    # Posterior must concentrate near the Planck H0 (BAO is consistent and only
    # tightens). Allow a 2σ_Planck = ±1.08 tolerance.
    assert 66.0 < float(np.median(posterior[:, 0])) < 69.0
    assert 0.27 < float(np.median(posterior[:, 1])) < 0.33


def test_importance_posterior_planck_only_has_high_ess():
    """Pure Planck compressed (no BAO chi²) should give ESS close to sample_count."""
    rng = np.random.default_rng(7)
    parameter_order = ["H0", "omegam"]
    prior_bounds = {"H0": (50.0, 90.0), "omegam": (0.10, 0.50)}
    bao_entries: list = []
    compressed_entries = [_entry("planck2018_compressed")]

    _, _, proposal_ess, _, errors = _draw_importance_posterior(
        rng, parameter_order, prior_bounds,
        bao_entries, compressed_entries, sample_count=4000,
    )
    assert errors == []
    assert proposal_ess > 1000.0


def test_importance_posterior_falls_back_to_uniform_without_gaussian():
    """No compressed-Gaussian entries → uniform proposal path (legacy behaviour).

    BAO-on-its-own is normally routed to ``_draw_desi_bao_only_posterior``
    by ``run_likelihood_chain``, so this exercises the explicit fall-back
    where the importance sampler is forced to handle a BAO-only call
    directly: ESS stays sane because BAO chi² fits inside the prior box.
    """
    rng = np.random.default_rng(11)
    parameter_order = ["H0", "omegam", "rd"]
    prior_bounds = {"H0": (60.0, 80.0), "omegam": (0.20, 0.40), "rd": (140.0, 160.0)}
    bao_entries = [_entry("desi_dr1_bao")]
    compressed_entries: list = []

    _, _, proposal_ess, _, errors = _draw_importance_posterior(
        rng, parameter_order, prior_bounds,
        bao_entries, compressed_entries, sample_count=2000,
    )
    assert errors == []
    # BAO posterior covers a non-trivial fraction of this tighter prior box,
    # so ESS should be at least ~hundreds even without the Gaussian proposal.
    assert proposal_ess > 100.0


# ── Sanity: importance-correction math ────────────────────────────────


def test_importance_correction_unbiased_with_gaussian_proposal():
    """If the proposal already matches the target, importance weights are
    uniform and ESS = sample_count.  Acts as a sanity check that the
    -log_proposal_pdf correction is wired correctly (without it the
    weights would skew toward proposal density and ESS would drop)."""
    rng = np.random.default_rng(99)
    parameter_order = ["H0", "omegam"]
    prior_bounds = {"H0": (50.0, 90.0), "omegam": (0.10, 0.50)}
    compressed_entries = [_entry("planck2018_compressed")]

    posterior, _, ess, n, errors = _draw_importance_posterior(
        rng, parameter_order, prior_bounds, [], compressed_entries,
        sample_count=2000,
    )
    assert errors == []
    # Posterior centroid sticks at the Planck mean (the target).
    assert abs(float(np.median(posterior[:, 0])) - 67.36) < 1.0
    assert abs(float(np.median(posterior[:, 1])) - 0.3153) < 0.02
    assert ess > 1000.0
    assert n > 0
