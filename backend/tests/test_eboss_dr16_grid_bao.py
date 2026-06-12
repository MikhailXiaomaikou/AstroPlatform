"""eBOSS DR16 non-Gaussian BAO grid likelihoods (2026-06-12, P2b user pick).

Three released likelihood surfaces from the SDSS DR16 official suite (Alam et
al. 2021): the ELG 1D D_V/r_d probability table (z=0.845, de Mattia et al.
2021 — the posterior is skewed, hence a table instead of a Gaussian) and the
two Lyα 50×50 (D_M/r_d, D_H/r_d) grids (z=2.334, du Mas des Bourboux et al.
2020) — the only z>2 BAO anchor outside DESI.

All ratios are DIMENSIONLESS (no rs_fid convention — unlike DR12 consensus).
Strongest check: parity against the REAL cobaya likelihoods instantiated on
the same files. One deliberate deviation: cobaya's RectBivariateSpline
silently EXTRAPOLATES the Lyα log-likelihood out-of-grid; we refuse support
(chi2=+inf) instead — fail-safe, locked here.
"""
from __future__ import annotations

import pathlib

import numpy as np
import pytest

import app.services.cosmology_likelihoods as cl

PLANCK_POINT = np.array([[67.36, 0.3153, 147.09]])
ORDER = ["H0", "omegam", "rd"]
GRID_KEYS = sorted(cl.EBOSS_DR16_GRID_BAO_EXECUTABLE_KEYS)

_COBAYA_PKG = pathlib.Path(__file__).resolve().parents[1] / "packages"
_HAS_COBAYA_DATA = (_COBAYA_PKG / "data" / "bao_data" / "sdss_DR16_ELG_BAO_DVtable.txt").exists()

_COBAYA_NAME = {
    "eboss_dr16_elg_bao": "sdss_dr16_bao_elg",
    "eboss_dr16_lyauto_bao": "sdss_dr16_baoplus_lyauto",
    "eboss_dr16_lyxqso_bao": "sdss_dr16_baoplus_lyxqso",
}


@pytest.mark.parametrize("key", GRID_KEYS)
def test_loader_verifies_pinned_files(key):
    v = cl.load_verified_grid_bao_data(key)
    assert v["hash_verified"] is True
    assert v["cov_fidelity"] == "full"
    expected_shape = (399, 2) if key == "eboss_dr16_elg_bao" else (2500, 3)
    assert v["grid"].shape == expected_shape


def test_grid_peaks_sit_at_published_best_fits():
    """The released surfaces peak at the published DR16 values — a wrong
    axis order / transposed reshape would move the apparent peak."""
    elg = cl.load_verified_grid_bao_data("eboss_dr16_elg_bao")["grid"]
    assert abs(elg[np.argmax(elg[:, 1]), 0] - 18.33) < 0.05
    for key, (dm, dh) in (
        ("eboss_dr16_lyauto_bao", (37.76, 8.92)),
        ("eboss_dr16_lyxqso_bao", (37.44, 9.06)),
    ):
        g = cl.load_verified_grid_bao_data(key)["grid"]
        peak = g[np.argmax(g[:, 2])]
        assert abs(peak[0] - dm) < 0.2 and abs(peak[1] - dh) < 0.2, (key, peak)


@pytest.mark.parametrize("key,lo,hi", [
    ("eboss_dr16_elg_bao", 0.05, 4.0),
    ("eboss_dr16_lyauto_bao", 0.3, 6.0),
    ("eboss_dr16_lyxqso_bao", 0.5, 8.0),
])
def test_chi2_at_planck_fiducial_is_sane(key, lo, hi):
    """ELG agrees with Planck (~0.4); the Lyα grids sit at the known mild
    DR16 Lyα-vs-Planck offset (~1.5σ → chi2 ~2-4 for 2 dof)."""
    chi2 = cl._grid_bao_chi2_samples(PLANCK_POINT, ORDER, key)[0]
    assert lo < chi2 < hi, (key, chi2)


@pytest.mark.skipif(not _HAS_COBAYA_DATA, reason="cobaya bao_data checkout absent")
@pytest.mark.parametrize("key", GRID_KEYS)
def test_parity_against_real_cobaya_likelihood(key):
    """-2·logp parity with the real cobaya likelihood at several IN-GRID
    cosmologies (out-of-grid behavior deliberately differs). Tolerance is the
    distance-kernel precision class (~2e-4 relative on distances; the steep
    Lyα log-surface amplifies that to ~1e-2 in chi2)."""
    import importlib
    from types import SimpleNamespace

    from astropy.cosmology import FlatLambdaCDM

    mod = importlib.import_module(f"cobaya.likelihoods.bao.{_COBAYA_NAME[key]}")
    lik_cls = getattr(mod, _COBAYA_NAME[key])
    lik = lik_cls(info={"packages_path": str(_COBAYA_PKG)}, name=f"bao.{_COBAYA_NAME[key]}")

    for h0, om, rd in ((67.36, 0.3153, 147.09), (70.0, 0.30, 145.0), (65.0, 0.33, 149.0)):
        cosmo = FlatLambdaCDM(H0=h0, Om0=om, Tcmb0=2.7255)
        lik.provider = SimpleNamespace(
            get_angular_diameter_distance=lambda z, c=cosmo: np.atleast_1d(
                c.angular_diameter_distance(z).value
            ),
            get_Hubble=lambda z, units="km/s/Mpc", c=cosmo: (
                np.atleast_1d(c.H(z).value)
                if units == "km/s/Mpc"
                else np.atleast_1d(c.H(z).value) / 299792.458
            ),
            get_param=lambda name, r=rd: {"rdrag": r}[name],
        )
        chi2_cobaya = -2.0 * float(np.asarray(lik.logp()).reshape(-1)[0])
        chi2_ours = cl._grid_bao_chi2_samples(np.array([[h0, om, rd]]), ORDER, key)[0]
        assert abs(chi2_ours - chi2_cobaya) < 0.05, (key, h0, om, rd, chi2_ours, chi2_cobaya)


def test_out_of_grid_samples_get_infinite_chi2():
    """The fail-safe deviation from cobaya: no extrapolated likelihood."""
    crazy = np.array([[120.0, 0.05, 80.0]])  # far outside every grid
    for key in GRID_KEYS:
        chi2 = cl._grid_bao_chi2_samples(crazy, ORDER, key)[0]
        assert np.isinf(chi2), (key, chi2)


def test_unverified_data_refuses_spline(monkeypatch):
    monkeypatch.setattr(
        cl,
        "load_verified_grid_bao_data",
        lambda key: {"grid": None, "sha256": None, "hash_verified": False,
                     "cov_fidelity": "unverified"},
    )
    cl._elg_logprob_spline.cache_clear()
    cl._lya_loglike_spline.cache_clear()
    with pytest.raises(ValueError, match="refusing to build the likelihood spline"):
        cl._elg_logprob_spline()
    with pytest.raises(ValueError, match="refusing to build the likelihood spline"):
        cl._lya_loglike_spline("eboss_dr16_lyauto_bao")
    cl._elg_logprob_spline.cache_clear()
    cl._lya_loglike_spline.cache_clear()


def test_chain_runs_in_process_to_publication():
    r = cl.run_likelihood_chain(
        model="lcdm",
        dataset_keys=["eboss_dr16_elg_bao", "eboss_dr16_lyauto_bao", "eboss_dr16_lyxqso_bao"],
        n_samples=3000,
        random_seed=42,
    )
    assert r["chain_tier"] == "publication"
    used = {d["key"] for d in r["datasets_used"]}
    assert used == set(GRID_KEYS)
    assert not r["datasets_not_run"]
    assert r["provenance"]["cosmology_likelihood"]["cov_fidelity"] == "full"


def test_do_not_combine_with_desi_is_reciprocal():
    for key in GRID_KEYS:
        entry = cl.get_cosmology_dataset(key)
        for desi in ("desi_dr1_bao", "desi_dr2_bao"):
            assert desi in entry.do_not_combine_with, (key, desi)
            assert key in cl.get_cosmology_dataset(desi).do_not_combine_with, (key, desi)
    # ELG additionally conflicts with the RSD table (same galaxies feed its
    # fsigma8 point); the Lyα grids do not.
    assert "eboss_dr16_rsd" in cl.get_cosmology_dataset("eboss_dr16_elg_bao").do_not_combine_with
    assert "eboss_dr16_elg_bao" in cl.get_cosmology_dataset("eboss_dr16_rsd").do_not_combine_with


def test_not_silently_dropped_in_multi_probe_chain():
    base = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["cosmic_chronometers"], n_samples=1200, random_seed=42,
    )
    joint = cl.run_likelihood_chain(
        model="lcdm",
        dataset_keys=["eboss_dr16_lyauto_bao", "cosmic_chronometers"],
        n_samples=1200,
        random_seed=42,
    )
    assert {d["key"] for d in joint["datasets_used"]} == {"eboss_dr16_lyauto_bao", "cosmic_chronometers"}
    assert joint["fit_statistics"]["chi2"] != base["fit_statistics"]["chi2"]


def test_joint_with_cmb_anchor_recovers_planck_like_cosmology():
    """Grids + compressed CMB: the high-z Lyα anchor must not drag the joint
    fit away from the Planck-like solution (the known mild Lyα offset is
    absorbed within errors)."""
    r = cl.run_likelihood_chain(
        model="lcdm",
        dataset_keys=["eboss_dr16_elg_bao", "eboss_dr16_lyauto_bao",
                      "eboss_dr16_lyxqso_bao", "planck2018_compressed"],
        n_samples=3000,
        random_seed=42,
    )
    assert r["chain_tier"] == "publication"
    assert 66.5 < r["parameters"]["H0"]["median"] < 68.5
    assert 0.29 < r["parameters"]["omegam"]["median"] < 0.34


def test_version_prose_laundering_closed_and_tuples_ground_honest_claims():
    """2026-06-12 review MAJOR + fix interlock, pinned on a REAL chain result
    (raw tuples, no JSON round-trip): fabricated counts from naming prose are
    blocked, while the honest redshift grounds via the structured z_coverage
    TUPLE (the validator had no tuple branch before this fix — the honest
    claim used to ground only via the launderable prose)."""
    from app.services.claim_validator import validate_claims

    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["eboss_dr16_elg_bao"], n_samples=1500, random_seed=42,
    )
    acc = [{"id": "t", "tool": "run_cosmology_likelihood_chain", "input": {}, "result": r}]
    assert validate_claims("The fit used 399 quasars.", acc).ok is False
    assert validate_claims("We detected 50 quasars.", acc).ok is False
    assert validate_claims("The ELG sample sits at z = 0.845.", acc).ok is True
    h0 = r["parameters"]["H0"]["median"]
    assert validate_claims(f"H0 = {h0:.2f} km/s/Mpc here.", acc).ok is True


def test_refused_prior_volume_is_reported():
    """The out-of-grid truncation is faithful (it IS the release's support)
    but must be observable: an ELG-only chain on default priors refuses ~39%
    of the prior volume and says so."""
    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["eboss_dr16_elg_bao"], n_samples=1500, random_seed=42,
    )
    assert any("grid support" in str(w) and "refused" in str(w) for w in r["warnings"])


def test_fully_unsupported_prior_box_blocks_loudly():
    """A prior box entirely outside the released grid support must come back
    blocked with zero parameters — not a posterior of prior-corner noise."""
    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["eboss_dr16_lyauto_bao"],
        n_samples=800, random_seed=42,
        priors={"H0": [50, 51], "omegam": [0.55, 0.57], "rd": [130, 131]},
    )
    assert r["chain_tier"] == "blocked"
    assert r["__do_not_claim__"] is True
    assert not r.get("parameters")
