"""Pantheon (2018) full 1048-SN vector (2026-06-13, P2b user pick).

Scolnic et al. 2018 (arXiv:1710.00845) — the SN anchor of the 2018-2022
literature era. Env-gated behind PANTHEON18_FULL_CHI2_ENABLED (the DES-SN5YR
precedent: a 1048x1048 per-sample cost has no place on the default chat
deadline); the compressed SN-only Ωm=0.298±0.022 registry record is a
published posterior summary for context only and is not executed by default.

Convention locked here (cobaya sn.pantheon, use_abs_mag=False, pecz=0,
intrinsicdisp=0): C_total = C_sys + diag(dmb²); the constant magnitude offset
is analytically marginalized, so mb works directly as an offset-normalized
μ_obs and the likelihood constrains Ωm (+w0/wa), never H0.
"""
from __future__ import annotations

import numpy as np
import pytest

import app.services.cosmology_likelihoods as cl

ORDER = ["H0", "omegam"]


def test_vendored_files_match_pins():
    v = cl.load_verified_pantheon18_data()
    assert v["hash_verified"] is True
    assert v["cov_fidelity"] == "full"
    assert len(v["mb"]) == 1048
    assert v["sha256"] == cl._registry_product_sha256("pantheon18", "covariance")


def test_covariance_is_sys_plus_diagonal_stat():
    """The cobaya convention: the vendored sys matrix alone is NOT the fit
    covariance — diag(dmb²) must be added (pecz=0, intrinsicdisp=0)."""
    raw = cl._load_pantheon18_raw()
    sys_tokens = (cl._PANTHEON18_DATA_DIR / "sys_full_long.txt").read_text().split()
    n = int(float(sys_tokens[0]))
    cov_sys = np.asarray(sys_tokens[1:], dtype=float).reshape(n, n)
    diag_added = np.diag(raw["cov"]) - np.diag(cov_sys)
    assert np.all(diag_added > 0)  # every SN carries a positive dmb
    # Off-diagonals are untouched.
    off = raw["cov"] - np.diag(np.diag(raw["cov"]))
    off_sys = cov_sys - np.diag(np.diag(cov_sys))
    np.testing.assert_allclose(off, off_sys, atol=0, rtol=0)


def test_marginalization_parity_with_cobaya_projection():
    # cobaya (_marginalize_abs_mag): invcov_marg = C⁻¹ − (C⁻¹1)(1ᵀC⁻¹1)⁻¹(1ᵀC⁻¹);
    # our per-sample χ² must equal the quadratic form under that projection.
    data = cl._load_pantheon18_data()
    cinv = data["cov_inv"]
    ones = np.ones(len(data["mu"]))
    invcov_marg = cinv - np.outer(cinv @ ones, ones @ cinv) / float(ones @ cinv @ ones)
    samples = np.array([[70.0, 0.298], [68.0, 0.32], [72.0, 0.26]])
    ours = cl._pantheon18_chi2_samples(samples, ORDER)
    h0_fid = np.full(len(samples), 70.0)
    dm = cl._flat_de_dm_grid_vectorized(
        data["z_hd"], h0_fid, samples[:, 1], np.full(len(samples), -1.0),
        np.zeros(len(samples)),
    )
    mu_model = 5.0 * np.log10((1.0 + data["z_hel"][:, None]) * dm) + 25.0
    delta = mu_model - data["mu"][:, None]
    proj_chi2 = np.einsum("in,ij,jn->n", delta, invcov_marg, delta)
    # 1048² float accumulation: ~1e-4 absolute on chi2 ~1e3 (union3's 22 bins
    # lock 1e-8; the relative scale is the same ~1e-7 class).
    np.testing.assert_allclose(ours, proj_chi2, atol=5e-4, rtol=0)


def test_chi2_minimum_at_published_omegam():
    """Scolnic+2018 SN-only flat-ΛCDM: Ωm = 0.298 ± 0.022, χ²/dof ≈ 1."""
    oms = np.linspace(0.20, 0.42, 45)
    samples = np.column_stack([np.full(45, 70.0), oms])
    chi2 = cl._pantheon18_chi2_samples(samples, ORDER)
    om_best = float(oms[np.argmin(chi2)])
    assert abs(om_best - 0.298) < 0.022, om_best
    assert 0.85 < float(chi2.min()) / 1048 < 1.1


def test_chi2_is_h0_invariant():
    """The offset marginalization must remove H0 entirely."""
    a = cl._pantheon18_chi2_samples(np.array([[60.0, 0.30]]), ORDER)[0]
    b = cl._pantheon18_chi2_samples(np.array([[80.0, 0.30]]), ORDER)[0]
    assert abs(a - b) < 1e-6


def test_flag_off_keeps_published_summary_context_only():
    """Without the full-vector gate, the published Ωm posterior stays context.

    It must be reported as not run, never multiplied as a Gaussian likelihood
    and never silently relabelled as the full vector.
    """
    if cl.PANTHEON18_FULL_CHI2_ENABLED:
        pytest.skip("flag enabled in this environment")
    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["pantheon18"], n_samples=1500, random_seed=42,
    )
    assert r["analysis_status"] == "NO_COMPRESSED_LIKELIHOOD"
    assert r["datasets_used"] == []
    assert {d["key"] for d in r["datasets_not_run"]} == {"pantheon18"}
    assert "parameters" not in r
    registered = r["datasets"][0]["compressed_likelihood"]
    assert registered["statistical_role"] == "published_posterior_summary"
    assert "Ωm = 0.298" in registered["source_locator"]


def test_flag_on_chain_runs_full_vector_via_emcee():
    """The enabled path always takes emcee (a 1048-SN ridge cannot be
    importance-sampled). Gated on the env flag — CI default skips."""
    if not cl.PANTHEON18_FULL_CHI2_ENABLED:
        pytest.skip("needs PANTHEON18_FULL_CHI2_ENABLED (~60-90s emcee fit)")
    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["pantheon18"], n_samples=2000, random_seed=42,
    )
    assert r["sampler"] == "sn_emcee"
    assert abs(r["parameters"]["omegam"]["median"] - 0.298) < 0.03
    assert "H0" not in r["parameters"]


def test_offset_sn_dispatch_covers_pantheon18():
    samples = np.array([[70.0, 0.3]])
    chi2 = cl._offset_sn_chi2_samples(samples, ORDER, "pantheon18")
    assert np.isfinite(chi2[0])
    assert cl._offset_sn_n_points("pantheon18") == 1048


def test_tampered_data_refuses(monkeypatch, tmp_path):
    bad = tmp_path / "pantheon18"
    bad.mkdir()
    (bad / "lcparam_full_long_zhel.txt").write_text("# fake\nsn 0.1 0.1 0 20.0 0.1\n")
    (bad / "sys_full_long.txt").write_text("1\n0.01\n")
    monkeypatch.setattr(cl, "_PANTHEON18_DATA_DIR", tmp_path / "pantheon18")
    cl._load_pantheon18_raw.cache_clear()
    cl._pantheon18_cov_inv.cache_clear()
    try:
        with pytest.raises(ValueError, match="unverified"):
            cl._load_pantheon18_raw()
        record = cl.load_verified_pantheon18_data()
        assert record["hash_verified"] is False
        assert record["cov_fidelity"] == "unverified"
    finally:
        cl._load_pantheon18_raw.cache_clear()
        cl._pantheon18_cov_inv.cache_clear()


def test_transient_load_failure_is_not_cached(monkeypatch, tmp_path):
    """The union3 raise-inside-cache pattern: a transient failure heals."""
    monkeypatch.setattr(cl, "_PANTHEON18_DATA_DIR", tmp_path / "nowhere")
    cl._load_pantheon18_raw.cache_clear()
    assert cl.load_verified_pantheon18_data()["hash_verified"] is False
    monkeypatch.undo()
    cl._load_pantheon18_raw.cache_clear()
    assert cl.load_verified_pantheon18_data()["hash_verified"] is True


def test_do_not_combine_with_sn_family_is_reciprocal():
    p18 = cl.get_cosmology_dataset("pantheon18")
    for other in ("pantheon_plus", "des_sn5yr", "union3"):
        assert other in p18.do_not_combine_with, other
    # Reciprocals live on the entries that carry lists (union3 carries none;
    # the pair still warns from pantheon18's own declaration).
    assert "pantheon18" in cl.get_cosmology_dataset("pantheon_plus").do_not_combine_with
    assert "pantheon18" in cl.get_cosmology_dataset("des_sn5yr").do_not_combine_with
    e1 = cl.get_cosmology_dataset("pantheon18")
    e2 = cl.get_cosmology_dataset("union3")
    assert cl._combination_warnings([e1, e2]), "pantheon18×union3 pair must warn"


def test_flag_on_routing_via_monkeypatched_keys(monkeypatch):
    """CI-side mirror of the env-gated live test (the DES precedent,
    test_des_sn5yr_full_path_labels_emcee_sampler): routing reads the KEYS
    set — monkeypatching the bool would silently test nothing."""
    monkeypatch.setattr(cl, "PANTHEON18_EXECUTABLE_KEYS", {"pantheon18"})
    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["pantheon18"], n_samples=1500, random_seed=42,
    )
    assert r["sampler"] == "sn_emcee"
    assert abs(r["parameters"]["omegam"]["median"] - 0.298) < 0.03
    assert "H0" not in r["parameters"]
    # The full-vector provenance record is honest here — the full chi2 RAN.
    sources = r["provenance"]["cosmology_likelihood"]["compressed_sources"]
    p18 = [s for s in sources if s.get("dataset_key") == "pantheon18"]
    assert p18 and "1048-SN apparent magnitudes" in p18[0]["source_locator"]


def test_flag_off_provenance_attests_no_likelihood_ran(monkeypatch):
    """With the flag off, provenance must attest that no likelihood ran."""
    monkeypatch.setattr(cl, "PANTHEON18_EXECUTABLE_KEYS", set())
    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["pantheon18"], n_samples=1200, random_seed=42,
    )
    likelihood_provenance = r["provenance"]["cosmology_likelihood"]
    assert "compressed_sources" not in likelihood_provenance
    assert likelihood_provenance["datasets_used"] == []
    assert likelihood_provenance["datasets_not_run"] == ["pantheon18"]
    registered = r["datasets"][0]["compressed_likelihood"]
    assert registered["statistical_role"] == "published_posterior_summary"
    assert "Ωm = 0.298" in registered["source_locator"]
    assert "1048-SN apparent magnitudes" not in registered["source_locator"]
