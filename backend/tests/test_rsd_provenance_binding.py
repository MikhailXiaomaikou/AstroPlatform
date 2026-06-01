"""T1-U3/U4: provenance-bind the eBOSS DR16 RSD fσ8 fit.

Mirrors T1-U1/U2 (CC). The fσ8 vector the chi2 fits must be the sha256-pinned
committed data product (object identity), not a hand-typed tuple. Only a diagonal
(per-tracer) covariance is published in Alam+2021 Table III ("correlations
ignored"), so cov_fidelity is "diagonal" — NOT "full"; the real 6x6 inter-bin
covariance is a separate offline reconstruction, not a vendorable table.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pytest


def test_rsd_registry_has_sha256_pinned_product():
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    products = get_cosmology_dataset("eboss_dr16_rsd").data_products
    assert products, "eboss_dr16_rsd has no data_products (RED until pinned)"
    fs = next((p for p in products if p.role == "rsd_measurement_vector"), None)
    assert fs is not None and fs.sha256, "no sha256-pinned rsd_measurement_vector product"


def test_load_verified_rsd_data_verifies_and_is_diagonal():
    from app.services import cosmology_likelihoods as cl

    load = getattr(cl, "load_verified_rsd_data", None)
    assert load is not None, "load_verified_rsd_data missing — RSD fit not provenance-bound"
    v = load("eboss_dr16_rsd")
    assert v["hash_verified"] is True
    # honest: per-tracer diagonal cov only, NOT a released full covariance.
    assert v["cov_fidelity"] == "diagonal"


def test_vendored_rsd_file_digest_matches_registry():
    from app.services import cosmology_likelihoods as cl

    spec = next(
        p for p in cl.get_cosmology_dataset("eboss_dr16_rsd").data_products
        if p.role == "rsd_measurement_vector"
    )
    path = cl._VENDORED_COSMO_DATA_DIR / "eboss_dr16_rsd" / "fsigma8.txt"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == spec.sha256


def test_fitted_rsd_vector_is_the_checksummed_object():
    """OBJECT IDENTITY: the fσ8 vector the chi2 iterates IS the verified loader's
    vector — proving the fit reads the pinned artifact, not a copy."""
    from app.services import cosmology_likelihoods as cl

    assert (
        cl.EBOSS_DR16_FSIGMA8
        is cl.load_verified_rsd_data("eboss_dr16_rsd")["fsigma8_vector"]
    )


def test_rsd_binding_introduces_zero_numeric_drift():
    """The vendored vector equals the original transcription and the RSD chi2 at a
    fiducial is finite — binding must not move any number."""
    from app.services import cosmology_likelihoods as cl

    assert len(cl.EBOSS_DR16_FSIGMA8) == 6
    assert cl.EBOSS_DR16_FSIGMA8[0] == (0.15, 0.53, 0.16)
    assert cl.EBOSS_DR16_FSIGMA8[-1] == (1.48, 0.462, 0.045)
    theta = np.array([[0.3153, 0.811]])
    chi2 = float(cl._eboss_fsigma8_chi2_samples(theta, ["omegam", "sigma8"])[0])
    # Pin the actual chi2 (not just >0) so a prediction-kernel regression is caught.
    assert chi2 == pytest.approx(6.3578, abs=1e-3)


def test_vendored_rsd_matches_registry_rows_and_hardcoded_fallback():
    """Drift guards: registry rows and the hand-typed fallback stay in sync with
    the vendored file the fit reads."""
    from app.services import cosmology_likelihoods as cl

    spec = next(
        p for p in cl.get_cosmology_dataset("eboss_dr16_rsd").data_products
        if p.role == "rsd_measurement_vector"
    )
    assert spec.rows == len(cl.EBOSS_DR16_FSIGMA8)  # #9
    assert tuple(cl._HARDCODED_EBOSS_FSIGMA8) == cl.EBOSS_DR16_FSIGMA8  # #12: literal == file
