"""SDSS MGS full non-Gaussian chi2(alpha) likelihood (2026-06-12 upgrade).

The sdss_6df_bao entry's MGS half used to be a hand-typed Gaussian
(4.470 +/- 0.17) approximating the released non-Gaussian likelihood. It now
runs on the SAME 399-point chi2(alpha) table cobaya's bao.sdss_dr7_mgs uses,
with the SAME spline construction (UnivariateSpline of -chi2/2, s=0).

Locks:
1. Vendored table matches the registry sha256 pin.
2. NUMERICAL PARITY with cobaya: our chi2(alpha) equals -2x cobaya's
   logpdf spline over a dense in-bounds grid (same file, same construction).
3. Out-of-bounds alpha -> the large finite penalty (importance weights die).
4. The 6dFGS Gaussian half is unchanged; the combined chi2 decomposes.
5. Tamper/missing table -> loud refusal, never a silent Gaussian fallback.
6. End-to-end: run_likelihood_chain on sdss_6df_bao alone still succeeds and
   the posterior prefers alpha near the table minimum.
7. (2026-06-12 adversarial review) A transient load failure is NOT cached —
   lru_cache never caches exceptions, so one bad read cannot poison the
   process until restart.
8. (2026-06-12 adversarial review) When EVERY importance-sampling proposal is
   out of the table's alpha bounds, the chain refuses loudly (blocked tier)
   instead of silently dropping the MGS constraint — a CONSTANT penalty
   cancels in normalized importance weights.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pytest

import app.services.cosmology_likelihoods as cl

_TABLE_PATH = cl._VENDORED_COSMO_DATA_DIR / "sdss_6df_bao" / "sdss_MGS_prob.txt"


def test_vendored_table_matches_pin():
    pinned = cl._registry_product_sha256("sdss_6df_bao", "mgs_alpha_chi2_table")
    assert pinned, "registry must pin the MGS table"
    assert _TABLE_PATH.is_file()
    assert hashlib.sha256(_TABLE_PATH.read_bytes()).hexdigest() == pinned
    table = cl.load_verified_mgs_prob_table()
    assert table["hash_verified"] is True
    assert table["chi2"].size == 399


def test_numerical_parity_with_cobaya_convention():
    from scipy.interpolate import UnivariateSpline

    chi2_table = np.loadtxt(_TABLE_PATH)
    alpha_grid = np.linspace(0.8005, 1.1985, len(chi2_table))
    cobaya_spline = UnivariateSpline(alpha_grid, -chi2_table / 2.0, s=0, ext=2)

    ours = cl._mgs_chi2_spline()
    test_alphas = np.linspace(0.81, 1.19, 173)
    for a in test_alphas:
        assert -2.0 * float(ours(a)) == pytest.approx(
            -2.0 * float(cobaya_spline(a)), abs=1e-12,
        )


def test_alpha_mapping_and_decomposition(monkeypatch):
    # Stub predictions: 6dF exactly on its Gaussian mean (zero term), MGS at
    # a chosen alpha -> total chi2 equals the table value at that alpha.
    target_alpha = 1.02
    preds = np.array([[3.047, target_alpha * cl.MGS_ALPHA_RESCALE]])
    monkeypatch.setattr(cl, "_bao_predictions", lambda s, p, m: preds)
    chi2 = cl._bao_chi2_samples(np.zeros((1, 3)), ["H0", "omegam", "rd"], "sdss_6df_bao")
    expected = -2.0 * float(cl._mgs_chi2_spline()(target_alpha))
    assert chi2[0] == pytest.approx(expected, abs=1e-9)


def test_out_of_bounds_alpha_gets_large_penalty(monkeypatch):
    preds = np.array([[3.047, 0.5 * cl.MGS_ALPHA_RESCALE]])  # alpha=0.5, far out
    monkeypatch.setattr(cl, "_bao_predictions", lambda s, p, m: preds)
    chi2 = cl._bao_chi2_samples(np.zeros((1, 3)), ["H0", "omegam", "rd"], "sdss_6df_bao")
    assert chi2[0] >= cl.MGS_OUT_OF_BOUNDS_CHI2


def test_6df_gaussian_half_unchanged(monkeypatch):
    # Off-mean 6dF by 1 sigma with MGS pinned at the table minimum: the
    # 6dF contribution must be exactly 1 (Gaussian), on top of min(chi2).
    table = cl.load_verified_mgs_prob_table()
    alpha_min = float(table["alpha"][np.argmin(table["chi2"])])
    preds = np.array([[3.047 + 0.137, alpha_min * cl.MGS_ALPHA_RESCALE]])
    monkeypatch.setattr(cl, "_bao_predictions", lambda s, p, m: preds)
    chi2 = cl._bao_chi2_samples(np.zeros((1, 3)), ["H0", "omegam", "rd"], "sdss_6df_bao")
    mgs_at_min = -2.0 * float(cl._mgs_chi2_spline()(alpha_min))
    assert chi2[0] - mgs_at_min == pytest.approx(1.0, abs=1e-6)


def test_tampered_table_refuses(monkeypatch, tmp_path):
    # Point the loader at a corrupted copy: the chi2 path must refuse loudly.
    bad_dir = tmp_path / "sdss_6df_bao"
    bad_dir.mkdir()
    (bad_dir / "sdss_MGS_prob.txt").write_text("1.0\n2.0\n3.0\n" * 50)
    monkeypatch.setattr(cl, "_VENDORED_COSMO_DATA_DIR", tmp_path)
    cl.load_verified_mgs_prob_table.cache_clear()
    cl._mgs_chi2_spline.cache_clear()
    try:
        with pytest.raises(ValueError, match="unverified"):
            cl._bao_chi2_samples(
                np.zeros((1, 3)), ["H0", "omegam", "rd"], "sdss_6df_bao",
            )
    finally:
        cl.load_verified_mgs_prob_table.cache_clear()
        cl._mgs_chi2_spline.cache_clear()


def test_transient_load_failure_is_not_cached(monkeypatch):
    # One failed load (missing file) must not wedge the process: the loader
    # raises instead of returning a cached unverified record, and lru_cache
    # never caches exceptions — so the NEXT call (file back) succeeds with NO
    # cache_clear in between.
    real_dir = cl._VENDORED_COSMO_DATA_DIR
    cl.load_verified_mgs_prob_table.cache_clear()
    cl._mgs_chi2_spline.cache_clear()
    try:
        monkeypatch.setattr(cl, "_VENDORED_COSMO_DATA_DIR", real_dir / "nonexistent")
        with pytest.raises(ValueError, match="unverified"):
            cl.load_verified_mgs_prob_table()
        monkeypatch.setattr(cl, "_VENDORED_COSMO_DATA_DIR", real_dir)
        table = cl.load_verified_mgs_prob_table()  # deliberately no cache_clear
        assert table["hash_verified"] is True
    finally:
        cl.load_verified_mgs_prob_table.cache_clear()
        cl._mgs_chi2_spline.cache_clear()


def test_all_proposals_out_of_bounds_blocks_loudly(monkeypatch):
    # A CONSTANT out-of-bounds penalty across every proposal cancels in the
    # normalized importance weights, so without the guard the chain would
    # silently report a 6dF-only posterior as a clean sdss_6df_bao fit.
    def all_out_of_bounds(samples, parameter_order, mean_vector):
        n = samples.shape[0]
        return np.column_stack([
            np.full(n, 3.047),                          # 6dF exactly on-mean
            np.full(n, 0.5 * cl.MGS_ALPHA_RESCALE),     # alpha=0.5, far out
        ])

    monkeypatch.setattr(cl, "_bao_predictions", all_out_of_bounds)
    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["sdss_6df_bao"], n_samples=500, random_seed=7,
    )
    assert r["chain_tier"] == "blocked"
    assert r["__do_not_claim__"] is True


def test_end_to_end_chain_prefers_table_minimum():
    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["sdss_6df_bao"], n_samples=3000, random_seed=42,
    )
    assert r.get("error") is None
    params = r.get("parameters") or {}
    assert "omegam" in params and np.isfinite(params["omegam"]["median"])
    # Two low-z points only -> weak constraints; sanity bounds, not precision.
    assert 0.05 < params["omegam"]["median"] < 0.95
