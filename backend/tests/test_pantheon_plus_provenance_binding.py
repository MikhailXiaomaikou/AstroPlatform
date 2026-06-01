"""T1-U6a: provenance-bind the Pantheon+SH0ES full-covariance supernova fit.

Mirrors the BAO/CC/RSD pattern — the 1701-SN distance-modulus covariance the χ²
inverts must be the sha256-pinned committed data product (object identity), not an
unverified ``np.load``.  Unlike CC/RSD (diagonal), the Pantheon+SH0ES stat+sys
matrix IS a released FULL covariance, so cov_fidelity is "full" once the vendored
``data.npz`` digest matches the registry pin.  The npz lives under
``data/pantheon_plus_2022/`` (not ``data/cosmology/``), so the loader keys off
``_PANTHEON_PLUS_DATA_DIR``.
"""
from __future__ import annotations

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


def test_fitted_cov_is_the_checksummed_object():
    """OBJECT IDENTITY: the covariance the χ² inverts IS the verified loader's
    array — proving the fit reads the sha256-pinned npz, not an unchecked copy."""
    from app.services import cosmology_likelihoods as cl

    verified = cl.load_verified_pantheon_plus_data("pantheon_plus")
    fitted = cl._load_pantheon_plus_data()
    assert fitted["cov"] is verified["cov"]
    assert fitted["cov_inv"] is verified["cov_inv"]


def test_pantheon_binding_zero_drift():
    """Binding must not move any number: shapes hold and the χ² at the
    Pantheon+SH0ES fiducial equals its pre-binding value."""
    from app.services import cosmology_likelihoods as cl

    data = cl._load_pantheon_plus_data()
    assert data["mu"].shape == (1701,)
    assert data["cov"].shape == (1701, 1701)
    theta = np.array([[73.04, 0.334, -19.253]])
    chi2 = float(cl._pantheon_plus_chi2_samples(theta, ["H0", "omegam", "M_B"])[0])
    # Pin the actual chi2 (captured pre-binding) so any prediction/data drift fails.
    assert chi2 == pytest.approx(1755.9316662998824, abs=1e-3)
