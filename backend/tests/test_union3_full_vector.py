"""Union3/UNITY1.5 full 22-bin binned-distance likelihood (2026-06-12 upgrade).

The union3 entry used to execute as a 1D compressed Omega_m Gaussian
(0.356 +/- 0.027). It now runs the SAME 22-bin lcparam_full.txt +
mag_covmat.txt cobaya's sn.union3 reads, with the constant magnitude offset
analytically marginalized — always on, no env flag (a 22x22 covariance has no
per-sample cost worth gating, unlike DES-SN5YR's 1829x1829).

Locks:
1. Both vendored files match their registry sha256 pins.
2. MARGINALIZATION PARITY with cobaya: our per-sample chi2
   (delta' C^-1 delta - (Sum C^-1 delta)^2 / Sum C^-1) equals the quadratic
   form under cobaya's _marginalize_abs_mag-projected inverse covariance.
3. End-to-end: the chain's Omega_m recovers the published anchor
   (Rubin+2023 Table 9: 0.356) — the chi2 minimum sits at 0.3560 exactly.
4. Tamper -> loud refusal on the fit path AND a dirty registry audit; never a
   silent fallback to the compressed Gaussian.
5. The offset-SN family dispatch is else-raise: an unknown key cannot
   silently run on another dataset's data.
"""
from __future__ import annotations

import hashlib
import pathlib

import numpy as np
import pytest

import app.services.cosmology_likelihoods as cl

_VEC_PATH = cl._UNION3_DATA_DIR / "lcparam_full.txt"
_COV_PATH = cl._UNION3_DATA_DIR / "mag_covmat.txt"


def _clear_union3_caches() -> None:
    cl._load_union3_raw.cache_clear()
    cl._union3_cov_inv.cache_clear()


def test_vendored_files_match_pins():
    for path, role in ((_VEC_PATH, "measurement_vector"), (_COV_PATH, "covariance")):
        pinned = cl._registry_product_sha256("union3", role)
        assert pinned, f"registry must pin the union3 {role}"
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == pinned
    data = cl.load_verified_union3_data("union3")
    assert data["hash_verified"] is True
    assert data["cov_fidelity"] == "full"
    assert data["mb"].size == 22
    assert data["cov"].shape == (22, 22)


def test_marginalization_parity_with_cobaya_projection():
    # cobaya (_marginalize_abs_mag): invcov_marg = C^-1 - (C^-1 1)(1'C^-1 1)^-1(1'C^-1),
    # then chi2 = delta' invcov_marg delta. Ours computes the algebraically
    # identical delta'C^-1 delta - (Sum C^-1 delta)^2 / (Sum C^-1) per sample.
    data = cl._load_union3_data()
    cov_inv = data["cov_inv"]
    ones = np.ones((cov_inv.shape[0], 1))
    derivp = cov_inv @ ones
    fisher = ones.T @ derivp
    invcov_marg = cov_inv - derivp @ np.linalg.solve(fisher, derivp.T)

    order = ["omegam"]
    om_grid = np.linspace(0.25, 0.45, 9)
    samples = om_grid[:, None]
    ours = cl._union3_chi2_samples(samples, order)
    dm = cl._flat_de_dm_grid_vectorized(
        data["z_hd"], np.full(9, 70.0), om_grid, np.full(9, -1.0), np.zeros(9)
    )
    mu_model = 5.0 * np.log10((1.0 + data["z_hel"][:, None]) * dm) + 25.0
    delta = mu_model - data["mu"][:, None]
    cobaya_chi2 = np.einsum("in,ij,jn->n", delta, invcov_marg, delta)
    np.testing.assert_allclose(ours, cobaya_chi2, atol=1e-8, rtol=0)


def test_chi2_minimum_at_published_omegam():
    order = ["omegam"]
    om_grid = np.linspace(0.20, 0.50, 301)
    chi2 = cl._union3_chi2_samples(om_grid[:, None], order)
    om_best = float(om_grid[np.argmin(chi2)])
    assert om_best == pytest.approx(0.356, abs=0.002)
    # chi2/dof order unity at the minimum (22 bins, Omega_m + offset out).
    assert 0.5 < float(np.min(chi2)) / 20.0 < 2.0


def test_end_to_end_chain_recovers_published_anchor():
    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["union3"], n_samples=4000, random_seed=42,
    )
    assert r.get("error") is None
    assert r["chain_tier"] == "publication"
    assert r["parameters"]["omegam"]["median"] == pytest.approx(0.356, abs=0.01)


def test_wcdm_chain_lands_in_published_ballpark():
    # Rubin+2023 flat-wCDM SN-only: w ~ -0.74 (+0.17/-0.20). Loose window —
    # this is a sanity lock, not a precision pin.
    r = cl.run_likelihood_chain(
        model="wcdm", dataset_keys=["union3"], n_samples=4000, random_seed=42,
    )
    assert r.get("error") is None
    w = r["parameters"]["w"]["median"]
    assert -1.2 < w < -0.4


def test_tampered_data_refuses_and_dirties_audit(monkeypatch, tmp_path):
    bad_dir = tmp_path / "union3"
    bad_dir.mkdir()
    bad_dir.joinpath("lcparam_full.txt").write_text(
        "#name zcmb zhel dz mb\nbin00 0.05 0.05 0.0 36.0\n" * 1
    )
    bad_dir.joinpath("mag_covmat.txt").write_text("1\n0.01\n")
    monkeypatch.setattr(cl, "_UNION3_DATA_DIR", tmp_path / "union3")
    _clear_union3_caches()
    cl.load_verified_bao_data.cache_clear()  # unrelated; keep audit fresh
    try:
        with pytest.raises(ValueError, match="sha256"):
            cl._union3_chi2_samples(np.array([[0.3]]), ["omegam"])
        issues = cl.audit_executable_pins()
        assert any("union3" in i for i in issues), issues
    finally:
        monkeypatch.setattr(
            cl, "_UNION3_DATA_DIR",
            pathlib.Path(cl.__file__).resolve().parent.parent.parent / "data" / "union3",
        )
        _clear_union3_caches()
        cl.load_verified_bao_data.cache_clear()


def test_offset_sn_dispatch_is_else_raise():
    with pytest.raises(ValueError, match="no chi2 dispatch"):
        cl._offset_sn_chi2_samples(np.array([[0.3]]), ["omegam"], "bogus_sn_key")
    with pytest.raises(ValueError, match="no data loader"):
        cl._offset_sn_n_points("bogus_sn_key")


# ── 2026-06-12 adversarial-review regressions ────────────────────────────────

def test_transient_load_failure_is_not_cached(monkeypatch):
    # One failed load must not wedge the always-on union3 path until restart:
    # the raw loader raises (exceptions are never cached), so the NEXT call
    # (files back) succeeds with NO cache_clear in between.
    real_dir = cl._UNION3_DATA_DIR
    _clear_union3_caches()
    try:
        monkeypatch.setattr(cl, "_UNION3_DATA_DIR", real_dir / "nonexistent")
        with pytest.raises(ValueError, match="unverified"):
            cl._load_union3_data()
        assert cl.load_verified_union3_data("union3")["cov_fidelity"] == "unverified"
        monkeypatch.setattr(cl, "_UNION3_DATA_DIR", real_dir)
        # Deliberately no cache_clear — the failure must not have been cached.
        assert cl.load_verified_union3_data("union3")["cov_fidelity"] == "full"
    finally:
        _clear_union3_caches()


def test_matrix_path_runs_importance_not_emcee():
    # allow_emcee_fallback=False (the robustness-matrix contract) must keep
    # union3 cells on the fast importance path — the unconditional SN emcee
    # bypass once pushed 30-cell matrices to ~64 s, past the 45 s tool
    # deadline. The cheap 22x22 chi2 importance-samples fine.
    assert cl._sn_emcee_bypass_active([], [cl.get_cosmology_dataset("union3")], False) is False
    assert cl._sn_emcee_bypass_active([], [cl.get_cosmology_dataset("union3")], True) is True
    r = cl._run_sampling_likelihood_chain(
        model_key="lcdm",
        entries=[cl.get_cosmology_dataset("union3")],
        priors=None, seed=42, sample_count=2000,
        allow_emcee_fallback=False,
    )
    assert r["sampler"] == "bao_gaussian_importance"
    assert r["parameters"]["omegam"]["median"] == pytest.approx(0.356, abs=0.02)


def test_pairwise_tensions_still_cover_union3():
    # union3 left compressed_entries when it became executable; the tension
    # diagnostic must still compare its PUBLISHED 1D anchor against other
    # datasets instead of silently dropping the row.
    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["union3", "planck2018_compressed"],
        n_samples=2000, random_seed=42,
    )
    pairs = {(t["dataset_a"], t["dataset_b"]) for t in r["pairwise_tensions"]}
    assert ("union3", "planck2018_compressed") in pairs or (
        "planck2018_compressed", "union3"
    ) in pairs


def test_full_likelihood_claim_not_overclaim_on_union3_chain():
    # F1-specificity class: the union3 chain executes the released full
    # likelihood, so an honest 'full likelihood ... publication-ready' reply
    # must NOT be hard-blocked as full_likelihood_overclaim.
    from app.services.claim_validator import methodology_consistency_violations

    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["union3"], n_samples=2000, random_seed=42,
    )
    assert r["claim_scope"] == "executable_full_fidelity_likelihoods"
    assert r["compressed_likelihood_preliminary"] is False
    violations = methodology_consistency_violations(
        "Union3 ran with the full likelihood and the chain is publication-ready "
        "(Omega_m = 0.358).",
        [{"tool": "run_cosmology_likelihood_chain", "result": r}],
    )
    assert not [v for v in violations if v.kind == "full_likelihood_overclaim"], violations


def test_false_external_run_claim_still_blocked_on_union3_chain():
    # Gate-check break (2026-06-12): the in-process full-fidelity scope must
    # NOT unlock wording that asserts an external Cobaya/CosmoSIS run which
    # never happened — only plain 'full likelihood' wording.
    from app.services.claim_validator import methodology_consistency_violations

    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["union3"], n_samples=2000, random_seed=42,
    )
    assert r["claim_scope"] == "executable_full_fidelity_likelihoods"
    for reply in (
        "We ran the full external Cobaya likelihood for Union3 and the chain "
        "is now publication-ready.",
        "Our full CosmoSIS likelihood analysis is complete and ready; we "
        "quote the Union3 posterior from it.",
    ):
        violations = methodology_consistency_violations(
            reply, [{"tool": "run_cosmology_likelihood_chain", "result": r}],
        )
        kinds = [v.kind for v in violations]
        assert "full_likelihood_overclaim" in kinds, (reply, kinds)


def test_headline_warning_matches_executable_scope():
    # The generic headline warning calls the run 'compressed-likelihood
    # preliminary' — false for the full-fidelity scope; the replacement must
    # still disclose it is not an external desilike/Cobaya run.
    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["union3"], n_samples=2000, random_seed=42,
    )
    assert "compressed-likelihood" not in r["warnings"][0]
    assert "desilike/Cobaya" in r["warnings"][0]


def test_full_likelihood_claim_still_blocked_on_compressed_chain():
    # The other direction must NOT regress: a genuinely compressed chain
    # claiming 'full likelihood' stays a violation.
    from app.services.claim_validator import methodology_consistency_violations

    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["planck2018_compressed"], n_samples=2000, random_seed=42,
    )
    assert r["claim_scope"] == "compressed_likelihood_preliminary"
    violations = methodology_consistency_violations(
        "We ran the full likelihood and the chain is publication-ready.",
        [{"tool": "run_cosmology_likelihood_chain", "result": r}],
    )
    assert [v for v in violations if v.kind == "full_likelihood_overclaim"]


def test_data_product_preview_serves_true_mb():
    # The measurement_vector preview once mislabeled columns positionally
    # (mb showed 0.05 — actually zhel). The first row's mb must be the real
    # bin00 distance modulus under its own label.
    import asyncio

    from app.services.cosmology_data_products import load_cosmology_data_product

    out = asyncio.run(load_cosmology_data_product(
        dataset_key="union3", role="measurement_vector", allow_network=False,
    ))
    assert out.get("hash_verified") is True
    row0 = out["parse"]["preview"][0]
    assert row0["mb"] == pytest.approx(36.630361, abs=1e-6)
    assert row0["zcmb"] == pytest.approx(0.05, abs=1e-9)
