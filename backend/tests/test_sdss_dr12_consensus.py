"""SDSS BOSS DR12 consensus BAO integration (2026-06-12, P2b user pick).

The BAO-only consensus likelihood of Alam et al. 2017 (arXiv:1607.03155) — the
"+BAO" column behind the Planck 2018 parameter tables. The release stores
DIMENSIONAL values (rs_fid = 147.78 Mpc convention): D_M·(rs_fid/r_d) in Mpc
and H·(r_d/rs_fid) in km/s/Mpc — same 'DM_over_rs' label as the eBOSS DR16
FSBAO files but a DIFFERENT meaning (those are dimensionless D_M/r_d), which
is why DR12 has its own prediction kernel and never flows through
_fsbao_predictions.

Strongest check here: parity against the REAL cobaya bao.sdss_dr12_consensus_bao
likelihood instantiated on the same files (skipped when the cobaya packages
checkout is absent, e.g. bare CI).
"""
from __future__ import annotations

import pathlib

import numpy as np
import pytest

import app.services.cosmology_likelihoods as cl

KEY = "sdss_dr12_consensus_bao"
PLANCK_POINT = np.array([[67.36, 0.3153, 147.09]])  # H0, omegam, rd
ORDER = ["H0", "omegam", "rd"]

_COBAYA_PKG = pathlib.Path(__file__).resolve().parents[1] / "packages"
_HAS_COBAYA_DATA = (_COBAYA_PKG / "data" / "bao_data" / "sdss_DR12Consensus_bao.dat").exists()


def test_loader_verifies_pinned_files():
    v = cl.load_verified_dr12_consensus_data(KEY)
    assert v["hash_verified"] is True
    assert v["cov_fidelity"] == "full"
    assert len(v["mean_vector"]) == 6
    assert {row[2] for row in v["mean_vector"]} == {"DM_over_rs", "bao_Hz_rs"}
    assert sorted({row[0] for row in v["mean_vector"]}) == [0.38, 0.51, 0.61]


def test_predictions_are_dimensional_not_ratio():
    """Guard the convention: a silent regression to the dimensionless D_M/r_d
    kernel would predict ~10 instead of ~1512 and the chi2 would explode."""
    v = cl.load_verified_dr12_consensus_data(KEY)
    pred = cl._dr12_consensus_predictions(PLANCK_POINT, ORDER, v["mean_vector"])[0]
    for (z, value, quantity), p in zip(v["mean_vector"], pred):
        assert abs(p - value) / value < 0.05, (z, quantity, value, p)


def test_chi2_at_planck_fiducial_is_order_unity():
    chi2 = cl._dr12_chi2_samples(PLANCK_POINT, ORDER, KEY)[0]
    assert 1.0 < chi2 < 12.0  # 6 data points; Planck18 fits DR12 well
    # Per-point pulls stay sub-2σ at the Planck 2018 best fit.
    v = cl.load_verified_dr12_consensus_data(KEY)
    pred = cl._dr12_consensus_predictions(PLANCK_POINT, ORDER, v["mean_vector"])[0]
    sigmas = np.sqrt(np.diag(v["covariance"]))
    observed = np.array([row[1] for row in v["mean_vector"]])
    pulls = (pred - observed) / sigmas
    assert np.all(np.abs(pulls) < 2.0), pulls


@pytest.mark.skipif(not _HAS_COBAYA_DATA, reason="cobaya bao_data checkout absent")
def test_parity_against_real_cobaya_likelihood():
    """Instantiate cobaya's bao.sdss_dr12_consensus_bao on the same released
    files and compare: (a) per-row theory values (the rs_fid convention),
    (b) the full-covariance chi2. Differences are bounded by the documented
    ~2e-4 relative precision of our flat-DE distance kernel vs astropy."""
    from types import SimpleNamespace

    from astropy.cosmology import FlatLambdaCDM
    from cobaya.likelihoods.bao.sdss_dr12_consensus_bao import sdss_dr12_consensus_bao

    lik = sdss_dr12_consensus_bao(
        info={"packages_path": str(_COBAYA_PKG)}, name="bao.sdss_dr12_consensus_bao"
    )
    h0, om, rd = PLANCK_POINT[0]
    cosmo = FlatLambdaCDM(H0=h0, Om0=om, Tcmb0=2.7255)
    lik.provider = SimpleNamespace(
        get_angular_diameter_distance=lambda z: np.atleast_1d(
            cosmo.angular_diameter_distance(z).value
        ),
        get_Hubble=lambda z, units="km/s/Mpc": np.atleast_1d(cosmo.H(z).value),
        get_param=lambda name: {"rdrag": rd}[name],
    )
    chi2_cobaya = -2.0 * lik.logp()
    chi2_ours = cl._dr12_chi2_samples(PLANCK_POINT, ORDER, KEY)[0]
    assert abs(chi2_ours - chi2_cobaya) < 0.1, (chi2_ours, chi2_cobaya)

    v = cl.load_verified_dr12_consensus_data(KEY)
    pred = cl._dr12_consensus_predictions(PLANCK_POINT, ORDER, v["mean_vector"])[0]
    for (z, _value, quantity), p in zip(v["mean_vector"], pred):
        theory = np.asarray(lik.theory_fun(z, quantity.removeprefix("bao_"))).reshape(-1)[0]
        assert abs(p - theory) / abs(theory) < 5e-4, (z, quantity, p, theory)


def test_chain_runs_in_process_as_preliminary():
    r = cl.run_likelihood_chain(model="lcdm", dataset_keys=[KEY], n_samples=2000, random_seed=42)
    assert r["chain_tier"] == "exploratory"
    assert r["publication_ready"] is False
    assert r["preliminary_ready"] is True
    used = {d["key"] for d in r["datasets_used"]}
    assert KEY in used
    assert not r["datasets_not_run"]
    # BAO-alone degeneracy: wide but sane constraints.
    h0 = r["parameters"]["H0"]
    assert 55.0 < h0["median"] < 80.0
    assert "rd" in r["parameters"]
    # Verified provenance flows through.
    assert r["provenance"]["cosmology_likelihood"]["cov_fidelity"] == "full"


def test_unsupported_quantity_raises_loudly():
    v = cl.load_verified_dr12_consensus_data(KEY)
    tampered = (*v["mean_vector"][:5], (0.61, 98.96, "DV_over_rs"))
    with pytest.raises(ValueError, match="unsupported DR12 consensus quantity"):
        cl._dr12_consensus_predictions(PLANCK_POINT, ORDER, tampered)


def test_unverified_data_refuses_chi2(monkeypatch):
    monkeypatch.setattr(
        cl,
        "load_verified_dr12_consensus_data",
        lambda key=KEY: {
            "mean_vector": None, "covariance": None, "sha256": None,
            "hash_verified": False, "cov_fidelity": "unverified",
        },
    )
    with pytest.raises(ValueError, match="refusing to compute chi2 from unverified data"):
        cl._dr12_chi2_samples(PLANCK_POINT, ORDER, KEY)


def test_do_not_combine_with_eboss_and_desi_is_reciprocal():
    dr12 = cl.get_cosmology_dataset(KEY)
    # eBOSS: the DR12 z=0.61 bin shares BOSS galaxies with the DR16 LRG sample.
    # DESI: overlapping sky + redshift bins; the DESI key papers partition at
    # z=0.6 rather than co-add (2026-06-12 review MAJOR).
    for other in ("eboss_dr16_lrg_fsbao", "eboss_dr16_rsd", "desi_dr1_bao", "desi_dr2_bao"):
        assert other in dr12.do_not_combine_with, other
        assert KEY in cl.get_cosmology_dataset(other).do_not_combine_with, other


def test_combination_with_eboss_lrg_warns():
    r = cl.run_likelihood_chain(
        model="lcdm",
        dataset_keys=[KEY, "eboss_dr16_lrg_fsbao"],
        n_samples=800,
        random_seed=42,
    )
    assert any(
        KEY in str(w) and "co-added" in str(w) for w in r.get("warnings", [])
    ), r.get("warnings")
    # A do-not-combine violation double-counts shared galaxies — never
    # publication-ready.
    assert r["publication_ready"] is False


def test_not_silently_dropped_in_multi_probe_chain():
    """Silent-drop audit (the 20f6274 lesson): the joint chain's chi2 must
    actually move when DR12 is added, and the dataset must appear in
    datasets_used — not just ride along."""
    base = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["cosmic_chronometers"], n_samples=1200, random_seed=42,
    )
    joint = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=[KEY, "cosmic_chronometers"], n_samples=1200, random_seed=42,
    )
    assert {d["key"] for d in joint["datasets_used"]} == {KEY, "cosmic_chronometers"}
    assert joint["fit_statistics"]["chi2"] != base["fit_statistics"]["chi2"]
    # The joint posterior must actually tighten omegam vs CC alone.
    assert joint["parameters"]["omegam"]["std"] < base["parameters"]["omegam"]["std"]
