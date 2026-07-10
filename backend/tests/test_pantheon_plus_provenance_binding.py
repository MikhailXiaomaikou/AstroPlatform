"""Provenance and scientific-schema gates for Pantheon+SH0ES."""
from __future__ import annotations

import asyncio
import hashlib

import numpy as np
import pytest


def test_pantheon_registry_has_sha256_pinned_npz():
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    products = get_cosmology_dataset("pantheon_plus").data_products
    npz = next((p for p in products if p.role == "sn_full_data_npz"), None)
    assert npz is not None, "no sha256-pinned sn_full_data_npz product (RED until pinned)"
    assert npz.sha256, "sn_full_data_npz product has no sha256 pin"
    assert npz.local_path == "data/pantheon_plus_2022/data.npz"


def test_vendored_npz_digest_matches_registry():
    from app.services import cosmology_likelihoods as cl

    spec = next(
        p for p in cl.get_cosmology_dataset("pantheon_plus").data_products
        if p.role == "sn_full_data_npz"
    )
    path = cl._PANTHEON_PLUS_DATA_DIR / "data.npz"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == spec.sha256


def test_load_verified_pantheon_is_full_and_verified():
    from app.services import cosmology_likelihoods as cl

    load = getattr(cl, "load_verified_pantheon_plus_data", None)
    assert load is not None, "load_verified_pantheon_plus_data missing — SN fit not bound"
    v = load("pantheon_plus")
    assert v["hash_verified"] is True
    # The Pantheon+SH0ES stat+sys covariance IS a released FULL covariance.
    assert v["cov_fidelity"] == "full"
    assert v["sha256"]
    assert v["shoes_calibration_ready"] is True
    assert v["likelihood_ready"] is True
    assert v["selection_mask"] is not None
    assert v["n_selected"] == 1657
    assert v["n_calibrators"] == 77
    assert v["scientific_issues"] == ()
    assert v["covariance_max_asymmetry_raw"] <= 5e-8


def test_current_pantheon_bundle_executes_official_selected_likelihood():
    from app.services import cosmology_likelihoods as cl

    fitted = cl._load_pantheon_plus_data()
    assert fitted["z_hd"].shape == (1657,)
    assert fitted["m_b_corr"].shape == (1657,)
    assert fitted["is_calibrator"].sum() == 77
    assert fitted["cov"].shape == (1657, 1657)
    assert fitted["cov_inv"].shape == (1657, 1657)


def test_complete_bundle_applies_official_selection_before_inversion(tmp_path, monkeypatch):
    """A complete future bundle executes only the release likelihood rows."""
    from app.services import cosmology_likelihoods as cl

    z_hd = np.array([0.005, 0.005, 0.02])
    z_hel = np.array([0.0045, 0.0045, 0.019])
    is_calibrator = np.array([False, True, False])
    covariance = np.diag([1.0, 2.0, 3.0])
    path = tmp_path / "data.npz"
    np.savez_compressed(
        path,
        cid=np.array(["excluded", "calibrator", "hubble_flow"]),
        z_hd=z_hd,
        z_hel=z_hel,
        mu=np.array([30.0, 31.0, 34.0]),
        mu_err_diag=np.sqrt(np.diag(covariance)),
        m_b_corr=np.array([10.0, 11.0, 14.0]),
        is_calibrator=is_calibrator,
        cepheid_distance=np.array([-9.0, 30.0, -9.0]),
        cov=covariance,
    )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    monkeypatch.setattr(cl, "_PANTHEON_PLUS_DATA_DIR", tmp_path)
    monkeypatch.setattr(cl, "_registry_product_sha256", lambda *_args: digest)
    cl.load_verified_pantheon_plus_data.cache_clear()
    cl._load_pantheon_plus_data.cache_clear()
    cl._pantheon_plus_cov_inv.cache_clear()
    try:
        verified = cl.load_verified_pantheon_plus_data("pantheon_plus")
        assert verified["likelihood_ready"] is True
        np.testing.assert_array_equal(verified["selection_mask"], [False, True, True])
        assert verified["n_selected"] == 2
        assert verified["n_calibrators"] == 1

        fitted = cl._load_pantheon_plus_data()
        np.testing.assert_array_equal(fitted["is_calibrator"], [True, False])
        np.testing.assert_array_equal(fitted["cov"], np.diag([2.0, 3.0]))
        np.testing.assert_allclose(fitted["cov_inv"], np.diag([0.5, 1.0 / 3.0]))
    finally:
        cl.load_verified_pantheon_plus_data.cache_clear()
        cl._load_pantheon_plus_data.cache_clear()
        cl._pantheon_plus_cov_inv.cache_clear()


def test_cepheid_calibrator_theory_breaks_h0_mb_degeneracy(monkeypatch):
    """Hubble-flow rows alone have an H0-M_B ridge; calibrators break it."""
    import app.services.cosmology_likelihoods.sn as sn

    z_hd = np.array([0.05, 0.005])
    z_hel = np.array([0.049, 0.0045])
    is_calibrator = np.array([False, True])
    cepheid_distance = np.array([-9.0, 31.0])
    h0 = 70.0
    omega_m = 0.3
    m_b = -19.253

    dm = sn._flat_de_dm_grid_vectorized(
        z_hd,
        np.array([h0]),
        np.array([omega_m]),
        np.array([-1.0]),
        np.array([0.0]),
    )[:, 0]
    hubble_flow_mu = 5.0 * np.log10((1.0 + z_hel[0]) * dm[0]) + 25.0
    m_obs = np.array([hubble_flow_mu + m_b, cepheid_distance[1] + m_b])

    monkeypatch.setattr(
        sn,
        "_load_pantheon_plus_data",
        lambda: {
            "z_hd": z_hd,
            "z_hel": z_hel,
            "m_b_corr": m_obs,
            "is_calibrator": is_calibrator,
            "cepheid_distance": cepheid_distance,
            "cov_inv": np.eye(2),
        },
    )

    shifted_h0 = 80.0
    ridge_shift = 5.0 * np.log10(shifted_h0 / h0)
    samples = np.array(
        [
            [h0, omega_m, m_b],
            [shifted_h0, omega_m, m_b + ridge_shift],
        ]
    )
    chi2 = sn._pantheon_plus_chi2_samples(samples, ["H0", "omegam", "M_B"])

    assert chi2[0] == pytest.approx(0.0, abs=1e-20)
    # The Hubble-flow prediction is unchanged along the ridge, while
    # CEPH_DIST + M_B shifts and penalizes the second sample.
    assert chi2[1] == pytest.approx(ridge_shift**2, rel=0.0, abs=1e-12)


def test_default_pantheon_product_is_not_the_binary_npz():
    """REGRESSION (code-review #1): the sha256-pinned binary .npz must NOT be the
    DEFAULT product of load_cosmology_data_product (no role). It has a local_path,
    so if it is products[0] the loader reads 20MB of binary, UTF-8-decodes it, and
    _parse_product_text reports ~556932 junk rows as COMPLETED + publication_ready
    — a zero-fabrication regression. The ASCII distance table must stay the default."""
    from app.services.cosmology_data_products import load_cosmology_data_product

    out = asyncio.run(
        load_cosmology_data_product(dataset_key="pantheon_plus", allow_network=False)
    )
    assert "data.npz" not in str(out.get("source") or ""), "default product is the binary npz"
    # network-free, the default ASCII table has no local copy -> UNAVAILABLE, never
    # a publication-ready fabricated table.
    assert out.get("publication_ready") is not True
