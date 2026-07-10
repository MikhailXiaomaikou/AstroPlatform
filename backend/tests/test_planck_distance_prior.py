"""planck2018_compressed correlated distance-prior execution (2026-07-07).

Before this upgrade the entry CLAIMED observables (R, l_A, ombh2, ns) but on
ΛCDM chains EXECUTED a diagonal (H0, Omega_m, sigma8, S8) parameter Gaussian.
Now every flat model (ΛCDM included) executes the Chen-Huang-Wang 2019
(arXiv:1808.05724) Table-I 4-dim correlated distance priors plus the S8
growth row on derived S8.  These tests fail on the pre-fix code:

- ΛCDM chi2 was the diagonal Gaussian, insensitive to ombh2/ns (tests 2, 3);
- the sampled axes had no ombh2/ns for ΛCDM (test 4);
- the extended-DE branch used a 3-dim (R, l_A, ombh2) prior without ns
  (test 6's ns sensitivity);
- missing distance-prior axes silently fell back to the diagonal Gaussian
  instead of failing loud (test 5).

Expected values are pinned to the paper table (source note below), per the
no-uncited-magic-numbers rule.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.services.cosmology_likelihoods.cmb import (
    _PLANCK18_DP_CORR,
    _PLANCK18_DP_MEAN,
    _PLANCK18_DP_SIGMA,
    _cmb_distance_priors,
    _compressed_chi2_samples,
)
from app.services.cosmology_likelihoods.registry import _REGISTRY

# ── Fixture: Chen, Huang & Wang 2019 (arXiv:1808.05724, JCAP 02 028) Table I,
# Planck 2018 TT,TE,EE+lowE, base ΛCDM. Visually verified against the
# published PDF (2026-07-07): R = 1.7502 ± 0.0046, l_A = 301.471 +0.089/-0.090
# (production symmetrizes to 0.090), ombh2 = 0.02236 ± 0.00015,
# n_s = 0.9649 ± 0.0043, with the printed correlation matrix.
CHW2019_TABLE1_MEAN = (1.7502, 301.471, 0.02236, 0.9649)
CHW2019_TABLE1_SIGMA = (0.0046, 0.090, 0.00015, 0.0043)
CHW2019_TABLE1_CORR = (
    (1.00, 0.46, -0.66, -0.74),
    (0.46, 1.00, -0.33, -0.35),
    (-0.66, -0.33, 1.00, 0.46),
    (-0.74, -0.35, 0.46, 1.00),
)
# CHW2019 appendix (~/cosmomc/data/Distance_invcov.txt): the UNNORMALIZED
# inverse covariance of the (R, l_A, ombh2) sub-block, machine precision.
CHW2019_APPENDIX_INVCOV3 = (
    (94392.3971, -1360.4913, 1664517.2916),
    (-1360.4913, 161.4349, 3671.6180),
    (1664517.2916, 3671.6180, 79719182.5162),
)


def _planck_entry():
    return _REGISTRY["planck2018_compressed"]


def _dp_chi2_reference(samples: np.ndarray, order: list[str], w0=-1.0, wa=0.0) -> np.ndarray:
    """Independent re-computation of the expected chi2: CHW2019 4-dim
    correlated prior + Planck S8 row on derived S8."""
    sigma = np.asarray(CHW2019_TABLE1_SIGMA)
    cov = sigma[:, None] * sigma[None, :] * np.asarray(CHW2019_TABLE1_CORR)
    inv = np.linalg.inv(cov)
    om = samples[:, order.index("omegam")]
    h0 = samples[:, order.index("H0")]
    obh2 = samples[:, order.index("ombh2")]
    ns = samples[:, order.index("ns")]
    big_r, l_a, _ = _cmb_distance_priors(om, h0, obh2, w0=w0, wa=wa)
    resid = np.column_stack([big_r, l_a, obh2, ns]) - np.asarray(CHW2019_TABLE1_MEAN)
    chi2 = np.einsum("ni,ij,nj->n", resid, inv, resid)
    # S8 growth row from the registry spec (Planck VI Table 2 column).
    spec = _planck_entry().compressed_likelihood
    params = list(spec.parameters)
    j = params.index("S8")
    s8_mean = float(np.asarray(spec.mean)[j])
    s8_var = float(np.asarray(spec.covariance)[j][j])
    s8 = samples[:, order.index("sigma8")] * np.sqrt(om / 0.3)
    return chi2 + (s8 - s8_mean) ** 2 / s8_var


# ── 1. Production constants pinned to the paper ─────────────────────────────

def test_chw2019_table1_constants_pinned_to_paper():
    assert np.allclose(_PLANCK18_DP_MEAN, CHW2019_TABLE1_MEAN)
    assert np.allclose(_PLANCK18_DP_SIGMA, CHW2019_TABLE1_SIGMA)
    assert np.allclose(_PLANCK18_DP_CORR, CHW2019_TABLE1_CORR)
    # Correlation matrix must be symmetric positive definite.
    assert np.allclose(_PLANCK18_DP_CORR, np.asarray(_PLANCK18_DP_CORR).T)
    np.linalg.cholesky(np.asarray(_PLANCK18_DP_CORR))


def test_table1_reproduces_paper_appendix_inverse_covariance():
    """Provenance cross-check: inverting the (R, l_A, ombh2) sub-covariance
    built from Table I sigmas+correlations reproduces the machine-precision
    inverse the paper ships in its CosmoMC appendix.  Tolerances reflect the
    2-digit rounding of the printed correlations; the (l_A, ombh2) element is
    the rounding-dominated small one."""
    sigma3 = np.asarray(CHW2019_TABLE1_SIGMA[:3])
    corr3 = np.asarray(CHW2019_TABLE1_CORR)[:3, :3]
    inv3 = np.linalg.inv(sigma3[:, None] * sigma3[None, :] * corr3)
    paper = np.asarray(CHW2019_APPENDIX_INVCOV3)
    ratio = inv3 / paper
    for i in range(3):
        assert abs(ratio[i, i] - 1.0) < 0.05, (i, ratio[i, i])
    assert abs(ratio[0, 1] - 1.0) < 0.10
    assert abs(ratio[0, 2] - 1.0) < 0.10
    assert abs(ratio[1, 2] - 1.0) < 0.25  # tiny element, rounding-dominated


# ── 2/3. ΛCDM executes the correlated prior (fail-before: diagonal Gaussian) ─

LCDM_ORDER = ["H0", "omegam", "rd", "sigma8", "ombh2", "ns"]


def _lcdm_samples() -> np.ndarray:
    # Around the Planck 2018 baseline, with deliberate offsets on every axis.
    return np.array([
        [67.36, 0.3153, 147.0, 0.8111, 0.02236, 0.9649],
        [68.20, 0.3050, 148.0, 0.8000, 0.02260, 0.9700],
        [66.50, 0.3300, 146.0, 0.8250, 0.02200, 0.9580],
    ])


def test_lcdm_chi2_is_the_correlated_distance_prior_plus_s8_row():
    samples = _lcdm_samples()
    chi2, errors = _compressed_chi2_samples(samples, LCDM_ORDER, [_planck_entry()])
    assert errors == []
    expected = _dp_chi2_reference(samples, LCDM_ORDER)
    assert np.allclose(chi2, expected, rtol=1e-10), (chi2, expected)


def test_lcdm_chi2_responds_to_ombh2_and_ns():
    base = _lcdm_samples()[:1]
    moved_obh2 = base.copy()
    moved_obh2[0, LCDM_ORDER.index("ombh2")] += 3 * CHW2019_TABLE1_SIGMA[2]
    moved_ns = base.copy()
    moved_ns[0, LCDM_ORDER.index("ns")] += 3 * CHW2019_TABLE1_SIGMA[3]
    chi2_base, _ = _compressed_chi2_samples(base, LCDM_ORDER, [_planck_entry()])
    chi2_obh2, _ = _compressed_chi2_samples(moved_obh2, LCDM_ORDER, [_planck_entry()])
    chi2_ns, _ = _compressed_chi2_samples(moved_ns, LCDM_ORDER, [_planck_entry()])
    assert chi2_obh2[0] > chi2_base[0] + 1.0
    assert chi2_ns[0] > chi2_base[0] + 1.0


# ── 4. ΛCDM sampled axes include the distance-prior columns ─────────────────

def test_lcdm_sampling_parameter_order_includes_dp_axes():
    from app.services.cosmology_likelihoods.sampling import _sampling_parameter_order

    order = _sampling_parameter_order(
        [_REGISTRY["desi_dr1_bao"]], [_planck_entry()]
    )
    assert "ombh2" in order and "ns" in order, order
    assert "S8" not in order  # still derived, never sampled


# ── 5. Missing distance-prior axes fail loud, never silently degrade ────────

def test_missing_dp_axes_fail_loud_not_silent():
    order = ["H0", "omegam", "rd", "sigma8"]  # no ombh2 / ns
    samples = np.array([[67.36, 0.3153, 147.0, 0.8111]])
    chi2, errors = _compressed_chi2_samples(samples, order, [_planck_entry()])
    assert len(errors) == 1
    assert "ombh2" in errors[0] and "ns" in errors[0]
    # The failed entry contributes nothing rather than a silently different
    # likelihood (the caller demotes the chain on any invalid spec).
    assert np.allclose(chi2, 0.0)


# ── 6. Extended flat-DE keeps the prior and now carries the ns axis ─────────

def test_w0wa_chi2_uses_4dim_prior_with_ns():
    order = ["H0", "omegam", "rd", "w0", "wa", "sigma8", "ombh2", "ns"]
    samples = np.array([
        [67.0, 0.316, 147.5, -0.9, -0.3, 0.81, 0.02236, 0.9649],
        [68.0, 0.300, 148.5, -1.1, 0.2, 0.80, 0.02250, 0.9700],
    ])
    chi2, errors = _compressed_chi2_samples(samples, order, [_planck_entry()])
    assert errors == []
    w0 = samples[:, order.index("w0")]
    wa = samples[:, order.index("wa")]
    expected = _dp_chi2_reference(samples, order, w0=w0, wa=wa)
    assert np.allclose(chi2, expected, rtol=1e-10)
    # ns sensitivity (the pre-fix DE branch ran a 3-dim prior without ns).
    moved = samples.copy()
    moved[:, order.index("ns")] += 3 * CHW2019_TABLE1_SIGMA[3]
    chi2_moved, _ = _compressed_chi2_samples(moved, order, [_planck_entry()])
    assert np.all(chi2_moved > chi2 + 0.5)


# ── 7. Curved models keep the parameter-summary path (defensive control) ────

def test_curved_model_keeps_parameter_summary_path():
    order = ["H0", "omegam", "sigma8", "omegak"]
    samples = np.array([[67.36, 0.3153, 0.8111, 0.01]])
    chi2, errors = _compressed_chi2_samples(samples, order, [_planck_entry()])
    assert errors == []
    spec = _planck_entry().compressed_likelihood
    params = list(spec.parameters)
    mean = np.asarray(spec.mean)
    cov = np.asarray(spec.covariance)
    # Diagonal spec Gaussian over (H0, omegam, sigma8) + S8 row on derived S8.
    expected = 0.0
    for name in ("H0", "omegam", "sigma8"):
        j = params.index(name)
        expected += (samples[0, order.index(name)] - mean[j]) ** 2 / cov[j][j]
    j = params.index("S8")
    derived = samples[0, order.index("sigma8")] * np.sqrt(samples[0, order.index("omegam")] / 0.3)
    expected += (derived - mean[j]) ** 2 / cov[j][j]
    assert chi2[0] == pytest.approx(expected, rel=1e-10)


# ── 8. Proposal moments are physically sensible (proposal-only helper) ──────

def test_dp_lcdm_proposal_moments_match_planck_lcdm_shape():
    # Imported lazily so the pre-fix code fails this test alone (helper
    # missing) instead of killing the whole module at collection.
    from app.services.cosmology_likelihoods.cmb import _planck_dp_lcdm_proposal_moments

    moments = _planck_dp_lcdm_proposal_moments()
    assert moments is not None
    names, mean, cov = moments
    assert names == ("H0", "omegam", "ombh2", "ns")
    sig = np.sqrt(np.diag(cov))
    corr = cov / np.outer(sig, sig)
    # The linearized image of the CHW2019 prior must look like the Planck
    # ΛCDM posterior: H0 ~ 67.3 ± 0.6, Om ~ 0.316 ± 0.009, strong H0-Om
    # anticorrelation. Loose windows — this is a proposal sanity check.
    assert 66.5 < mean[0] < 68.5
    assert 0.30 < mean[1] < 0.33
    assert 0.3 < sig[0] < 1.2
    assert corr[0, 1] < -0.9
    np.linalg.cholesky(cov)


# ── 9. Integration: ΛCDM BAO+CMB anchor stays numerically correct/preliminary ─

def test_lcdm_bao_cmb_recovers_h0_preliminary_tier():
    from app.services.cosmology_likelihoods import run_likelihood_chain

    r = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["desi_dr1_bao", "planck2018_compressed"],
        n_samples=4000,
        random_seed=42,
    )
    assert r["chain_tier"] == "exploratory"
    assert r["publication_ready"] is False
    assert r["preliminary_ready"] is True
    assert "compressed_or_approximate_likelihood" in r["preliminary_reasons"]
    ess = float(r["chain_diagnostics"]["proposal_ess"])
    assert ess >= 400.0, ess
    h0 = float(r["parameters"]["H0"]["median"])
    assert 66.5 < h0 < 68.5, h0
    # The distance-prior axes are now real sampled posteriors.
    assert "ombh2" in r["parameters"] and "ns" in r["parameters"]
    obh2 = float(r["parameters"]["ombh2"]["median"])
    assert 0.0215 < obh2 < 0.0232, obh2
    ns = float(r["parameters"]["ns"]["median"])
    assert 0.95 < ns < 0.98, ns
    # S8 remains derived-only (reported, never sampled).
    assert "S8" in r["derived_params"]
