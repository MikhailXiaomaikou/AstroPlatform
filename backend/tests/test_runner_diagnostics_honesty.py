"""In-process runner diagnostics honesty (2026-06-12, P1b items 3-6).

Four fabricated/decorative numbers in the runner results, all removed:

1. chain_diagnostics.rhat was hard-coded 1.0 on every in-process path — a
   never-computed convergence statistic inside the provenance envelope. Now
   None + rhat_note on both runners; the ESS estimate carries an explicit
   ess_source label and the ess_tail clone is gone.
2. _run_emcee_chain's ESS fallback (flat-chain/10 ~ 4800) promoted a chain to
   PUBLICATION at the exact moment the autocorrelation estimate FAILED. Now
   the failure propagates as NaN -> ess None, ess_source=autocorr_failed, a
   loud warning, and the tier is capped at exploratory (never publication,
   never silently blocked either — the numbers stay discussable with the
   caveat).
3. run_alcock_paczynski_test fit the legacy hand-typed DESI constants (outside
   the sha256-pin audit) and stamped publication_ready unconditionally. Now it
   fits the verified vendored arrays (byte-identical values), carries the
   artifact sha256 in provenance, and refuses loudly on an unverified file.
4. fit_statistics.delta_chi2 was a literal 0.0 placeholder in every result
   (both runners + cmb_rotation) — removed; model comparison lives in
   compute_model_comparison.
"""
from __future__ import annotations

import pytest

import app.services.cosmology_likelihoods as cl


def _chain(**kw):
    defaults = dict(model="lcdm", dataset_keys=["desi_dr1_bao", "planck2018_compressed"],
                    n_samples=1500, random_seed=42)
    defaults.update(kw)
    return cl.run_likelihood_chain(**defaults)


def test_rhat_is_honestly_none_on_sampling_runner():
    r = _chain()
    d = r["chain_diagnostics"]
    assert d["rhat"] is None
    assert "not computed" in d["rhat_note"]
    assert d["ess_source"] == "importance_weights"
    assert "ess_tail" not in d
    assert isinstance(d["ess_bulk"], int)


def test_rhat_is_honestly_none_on_analytic_runner():
    r = _chain(dataset_keys=["planck2018_compressed"])
    d = r["chain_diagnostics"]
    assert d["overall_status"].startswith("analytic_gaussian")
    assert d["rhat"] is None
    assert d.get("ess_source") in {"importance_weights", "exact_gaussian_draws"}


def test_delta_chi2_placeholder_removed():
    r = _chain()
    assert "delta_chi2" not in r["fit_statistics"]
    assert "chi2" in r["fit_statistics"]  # the real statistic stays


def test_emcee_ess_failure_caps_tier_at_exploratory(monkeypatch):
    # The old n/10 fallback promoted exactly this case to publication.
    import emcee

    def boom(self, **kw):
        raise RuntimeError("chain too short")

    monkeypatch.setattr(emcee.EnsembleSampler, "get_autocorr_time", boom)
    r = cl._run_sampling_likelihood_chain(
        model_key="lcdm", entries=[cl.get_cosmology_dataset("union3")],
        priors=None, seed=42, sample_count=1500, allow_emcee_fallback=True,
    )
    assert r["sampler"] == "sn_emcee"
    assert r["chain_tier"] == "exploratory"
    assert r["publication_ready"] is False
    d = r["chain_diagnostics"]
    assert d["proposal_ess"] is None
    assert d["ess_bulk"] is None
    assert d["ess_source"] == "autocorr_failed"
    assert any("UNVERIFIED" in w for w in r["warnings"])


def test_emcee_working_autocorr_still_reaches_publication():
    r = cl._run_sampling_likelihood_chain(
        model_key="lcdm", entries=[cl.get_cosmology_dataset("union3")],
        priors=None, seed=42, sample_count=1500, allow_emcee_fallback=True,
    )
    assert r["sampler"] == "sn_emcee"
    assert r["chain_tier"] == "publication"
    assert r["chain_diagnostics"]["ess_source"] == "emcee_autocorr"
    assert r["chain_diagnostics"]["proposal_ess"] > 400


def test_ap_test_fits_verified_arrays_with_provenance():
    ap = cl.run_alcock_paczynski_test()
    assert ap["success"] is True
    # Values unchanged (constants were byte-identical to the vendored file).
    assert ap["omega_m_best"] == pytest.approx(0.314, abs=0.01)
    prov = ap["provenance"]["alcock_paczynski"]
    assert prov["artifact_sha256"] == cl.load_verified_bao_data("desi_dr1_bao")["sha256"]
    assert prov["cov_fidelity"] == "full"
    assert ap["publication_ready"] is True


def test_ap_test_refuses_on_unverified_data(monkeypatch):
    def fake_unverified(key="desi_dr1_bao"):
        return {"mean_vector": None, "covariance": None, "sha256": None,
                "hash_verified": False, "cov_fidelity": "unverified"}

    monkeypatch.setattr(cl, "load_verified_bao_data", fake_unverified)
    ap = cl.run_alcock_paczynski_test()
    assert ap["success"] is False
    assert ap["error_class"] == "unverified_data"
    assert ap["__do_not_claim__"] is True


def test_emcee_fallback_with_failed_diagnostics_keeps_importance_result(monkeypatch):
    # Review follow-up: when the low-ESS emcee upgrade itself returns an
    # UNVERIFIABLE chain (NaN ESS), a measured importance ESS — even a low
    # one — is strictly more honest. The runner must keep the importance
    # posterior instead of adopting the unverified emcee one.
    import numpy as np

    real_importance = cl._draw_importance_posterior

    def low_ess_importance(*a, **kw):
        samples, best_chi2, _ess, draws, errs = real_importance(*a, **kw)
        return samples, best_chi2, 250.0, draws, errs  # finite but < 400

    def nan_emcee(*a, **kw):
        return (np.zeros((100, 3)), 10.0, float("nan"), 100, [])

    monkeypatch.setattr(cl, "_draw_importance_posterior", low_ess_importance)
    monkeypatch.setattr(cl, "_run_emcee_chain", nan_emcee)
    r = cl._run_sampling_likelihood_chain(
        model_key="lcdm",
        entries=[cl.get_cosmology_dataset("desi_dr1_bao"),
                 cl.get_cosmology_dataset("cosmic_chronometers")],
        priors=None, seed=42, sample_count=1500, allow_emcee_fallback=True,
    )
    assert r["sampler"] == "bao_gaussian_importance"  # emcee NOT adopted
    assert r["chain_diagnostics"]["proposal_ess"] == 250.0
    assert r["chain_tier"] == "exploratory"


def test_cmb_rotation_rhat_no_longer_fabricated():
    from app.services.cmb_rotation_likelihoods import run_cmb_rotation_likelihood

    from app.services.cmb_rotation_likelihoods import default_cmb_rotation_dataset_keys

    r = run_cmb_rotation_likelihood(
        dataset_keys=default_cmb_rotation_dataset_keys(),
        model="isotropic_beta", random_seed=42, n_samples=1000,
    )
    if not r.get("success", True) or "chain_diagnostics" not in r:
        import pytest as _pytest
        _pytest.skip("rotation runner unavailable in this environment")
    d = r["chain_diagnostics"]
    assert d["rhat"] is None
    assert d["ess_source"] == "exact_gaussian_draws"
    assert "rhat_max" not in d.get("thresholds", {})
