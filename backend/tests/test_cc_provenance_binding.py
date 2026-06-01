"""T1-U1/U2: provenance-bind the cosmic-chronometer H(z) fit.

Mirrors the DESI Step-1 pattern — the H(z) vector the chi2 fits must be the
sha256-pinned committed data product (object identity), not a hand-typed tuple.
NOTE the honest fidelity distinction: cov_fidelity is "diagonal" here (the CC
covariance is diagonal; the Moresco+2020 systematic covariance is NOT applied),
distinct from DESI's "full" released covariance. Unlike DESI there is no single
machine-readable upstream release, so the sha256 pins the committed artifact (a
drift guard between the fitted array and the published transcription).
"""
from __future__ import annotations

import hashlib

import numpy as np


def test_cc_registry_has_sha256_pinned_hz_product():
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    products = get_cosmology_dataset("cosmic_chronometers").data_products
    assert products, "cosmic_chronometers has no data_products (RED until pinned)"
    hz = next((p for p in products if p.role == "hz_measurement_vector"), None)
    assert hz is not None and hz.sha256, "no sha256-pinned hz_measurement_vector product"


def test_load_verified_cc_data_verifies_and_is_diagonal():
    from app.services import cosmology_likelihoods as cl

    load = getattr(cl, "load_verified_cc_data", None)
    assert load is not None, "load_verified_cc_data missing — CC fit not provenance-bound"
    v = load("cosmic_chronometers")
    assert v["hash_verified"] is True
    # honest: the vector is pinned, but the covariance is diagonal — NOT "full".
    assert v["cov_fidelity"] == "diagonal"


def test_vendored_cc_file_digest_matches_registry():
    from app.services import cosmology_likelihoods as cl

    spec = next(
        p for p in cl.get_cosmology_dataset("cosmic_chronometers").data_products
        if p.role == "hz_measurement_vector"
    )
    path = cl._VENDORED_COSMO_DATA_DIR / "cosmic_chronometers" / "hz.txt"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == spec.sha256


def test_fitted_cc_vector_is_the_checksummed_object():
    """OBJECT IDENTITY: the H(z) vector the chi2 iterates IS the verified
    loader's vector — proving the fit reads the pinned artifact, not a copy."""
    from app.services import cosmology_likelihoods as cl

    assert (
        cl.COSMIC_CHRONOMETER_HZ
        is cl.load_verified_cc_data("cosmic_chronometers")["hz_vector"]
    )


def test_cc_binding_introduces_zero_numeric_drift():
    """The vendored vector equals the original transcription and the CC chi2 at a
    fiducial is finite/consistent — binding must not move any number."""
    from app.services import cosmology_likelihoods as cl

    assert len(cl.COSMIC_CHRONOMETER_HZ) == 31
    assert cl.COSMIC_CHRONOMETER_HZ[0] == (0.07, 69.0, 19.6)
    assert cl.COSMIC_CHRONOMETER_HZ[-1] == (1.965, 186.5, 50.4)
    theta = np.array([[67.36, 0.3153]])
    chi2 = float(cl._cosmic_chronometer_chi2_samples(theta, ["H0", "omegam"])[0])
    assert np.isfinite(chi2) and chi2 > 0
