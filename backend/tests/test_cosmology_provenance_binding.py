"""Step 1 milestone: provenance-BIND the fit (correctness, not just honesty).

The platform today sha256-pins the real DESI DR1 BAO covariance file in the
registry (DataProductSpec.sha256 = bbafa907...), but the chi2 actually fits a
SEPARATE hand-typed module-level tuple (DESI_DR1_BAO_COVARIANCE, read via
_BAO_DATA in _bao_chi2_samples). So the checksum certifies a file the fit never
reads — provenance is decorative, and the platform cannot truthfully say "this
posterior used the verified DESI covariance."

These tests ENCODE the target: the fitted array must BE the checksum-verified
array, and a quotable BAO posterior must certify cov_fidelity == "full". They
are RED until Step 1 binds a sha256-verified loader to the fit and stamps
cov_fidelity. This is the milestone that proves CORRECTNESS, not just honesty.
"""
from __future__ import annotations

import numpy as np
import pytest


def test_fitted_desi_bao_covariance_is_the_checksummed_array(monkeypatch):
    """The chi2 path must consume the current checksum-verified loader record.

    Object identity with the import-time ``_BAO_DATA`` snapshot is intentionally
    not required: the public loader cache can be cleared, after which a fresh
    NumPy array is scientifically equivalent but cannot be the same Python
    object.  Instead, instrument the live loader with a sentinel covariance and
    prove that the fitted chi2 changes to exactly the value from that record.
    """
    from app.services import cosmology_likelihoods as cl
    import app.services.cosmology_likelihoods.bao as bao

    # Target API (Step 1): a sync loader that reads the VENDORED local DESI file,
    # verifies its sha256 against the registry DataProductSpec, and returns the
    # parsed (mean_vector, covariance) arrays + the verified digest.
    load = getattr(cl, "load_verified_bao_data", None)
    assert load is not None, (
        "load_verified_bao_data does not exist: the fit is not provenance-bound. "
        "Step 1 must add a sha256-verified loader and source _BAO_DATA from it."
    )

    verified = load("desi_dr1_bao")
    assert verified["hash_verified"] is True, "loader did not verify the pinned sha256"
    assert verified["cov_fidelity"] == "full"

    # The registry-pinned sha256 must equal the loaded file's digest.
    cov_spec = next(
        p for p in cl.get_cosmology_dataset("desi_dr1_bao").data_products
        if p.role == "covariance"
    )
    assert verified["sha256"] == cov_spec.sha256

    samples = np.asarray(
        [[67.36, 0.3153, 147.09], [72.0, 0.25, 150.0]],
        dtype=float,
    )
    order = ["H0", "omegam", "rd"]
    sentinel_covariance = np.asarray(verified["covariance"], dtype=float) * 2.0
    sentinel_record = {**verified, "covariance": sentinel_covariance}
    calls: list[str] = []

    def _spy(dataset_key: str):
        calls.append(dataset_key)
        return sentinel_record

    monkeypatch.setattr(bao, "load_verified_bao_data", _spy)
    actual = cl._bao_chi2_samples(samples, order, "desi_dr1_bao")

    predictions = bao._bao_predictions(samples, order, verified["mean_vector"])
    observed = np.asarray([row[1] for row in verified["mean_vector"]], dtype=float)
    residual = predictions - observed
    expected = np.einsum(
        "ni,ij,nj->n",
        residual,
        np.linalg.inv(sentinel_covariance),
        residual,
    )
    stale_snapshot_result = np.einsum(
        "ni,ij,nj->n",
        residual,
        np.linalg.inv(np.asarray(cl._BAO_DATA["desi_dr1_bao"][1], dtype=float)),
        residual,
    )

    assert calls == ["desi_dr1_bao"]
    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)
    assert not np.allclose(actual, stale_snapshot_result)


def test_desi_bao_reproduces_omega_m_with_full_cov_fidelity():
    """END-TO-END CORRECTNESS: DESI DR1 BAO LCDM must recover Om = 0.295 +/- 0.015
    (Adame et al. 2024, arXiv:2404.03002) AND the result must certify it fit the
    sha256-verified full covariance (cov_fidelity == 'full').

    RED today: Om already recovers ~0.295 (that is honesty/provenance — the number
    traces to a tool run), but there is no cov_fidelity stamp, so the platform
    cannot prove the number came from the verified covariance (that is the
    missing correctness axis)."""
    from app.services.cosmology_likelihoods import run_likelihood_chain

    r = run_likelihood_chain(
        model="lcdm", dataset_keys=["desi_dr1_bao"], n_samples=4000, random_seed=42
    )
    om = r["parameters"]["omegam"]["median"]
    assert 0.28 <= om <= 0.31, f"Om={om} not within Adame 2024 0.295 +/- 0.015"
    assert r["chain_tier"] == "exploratory"
    assert r["publication_ready"] is False
    assert r["preliminary_ready"] is True

    prov = r["provenance"]["cosmology_likelihood"]
    assert prov.get("cov_fidelity") == "full", (
        "result does not certify it fit the sha256-verified full covariance; "
        "provenance is decorative until Step 1 binds the loader and stamps "
        "cov_fidelity on the fit result."
    )


def test_pantheon_plus_full_path_blocks_any_incomplete_shoes_bundle(monkeypatch):
    """A future incomplete/reverted bundle must fail before likelihood work."""
    import app.services.cosmology_likelihoods.sn as sn

    monkeypatch.setattr(
        sn,
        "load_verified_pantheon_plus_data",
        lambda *_args, **_kwargs: {
            "likelihood_ready": False,
            "scientific_issues": (
                "bundle is missing official fields: m_b_corr, is_calibrator, cepheid_distance",
            ),
        },
    )
    sn._load_pantheon_plus_data.cache_clear()
    try:
        with pytest.raises(ValueError, match="official likelihood requires") as exc_info:
            sn._load_pantheon_plus_data()
        warning = str(exc_info.value)
        assert "IS_CALIBRATOR" in warning
        assert "CEPH_DIST" in warning
        assert "full SH0ES/H0 claim" in warning
    finally:
        sn._load_pantheon_plus_data.cache_clear()
