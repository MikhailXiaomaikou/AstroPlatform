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


def test_fitted_desi_bao_covariance_is_the_checksummed_array():
    """OBJECT IDENTITY: the covariance _bao_chi2_samples fits MUST be the exact
    array parsed from the sha256-pinned DESI data product, byte-for-byte — not a
    hand-typed tuple. RED until the loader is bound to the fit."""
    from app.services import cosmology_likelihoods as cl

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

    # The array the chi2 fits IS the verified array — object identity, not merely
    # equal values. The hand-typed constant is byte-identical to the file, so a
    # value-equality check would pass even if the fit still read a hand-typed
    # copy; only `is` proves _BAO_DATA is sourced FROM the verified loader.
    fitted_cov = cl._BAO_DATA["desi_dr1_bao"][1]
    assert fitted_cov is verified["covariance"], (
        "fitted covariance is a different object from the checksum-verified array; "
        "provenance is decorative until _BAO_DATA is sourced from the loader."
    )
    np.testing.assert_array_equal(
        np.asarray(fitted_cov, dtype=float),
        np.asarray(verified["covariance"], dtype=float),
    )


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
    assert r["chain_tier"] == "publication"

    prov = r["provenance"]["cosmology_likelihood"]
    assert prov.get("cov_fidelity") == "full", (
        "result does not certify it fit the sha256-verified full covariance; "
        "provenance is decorative until Step 1 binds the loader and stamps "
        "cov_fidelity on the fit result."
    )


@pytest.mark.slow
def test_pantheon_plus_full_cov_reproduces_omega_m(monkeypatch):
    """FLAG-FLIP reproduction target: with the Pantheon+ full 1701x1701 covariance
    path enabled, LCDM recovers Om ~ 0.334 (Brout et al. 2022, arXiv:2202.04077)
    from the real covariance — not the compressed H0=73 Gaussian. ~200s (slow).

    This demonstrates that flipping the full-cov flag yields a genuinely
    re-derived constraint; the gate (_is_executable_sn_entry) and the full-cov
    chi2 (_pantheon_plus_chi2_samples) already exist behind
    PANTHEON_PLUS_FULL_CHI2_ENABLED."""
    import app.services.cosmology_likelihoods as cl

    monkeypatch.setattr(cl, "PANTHEON_PLUS_EXECUTABLE_KEYS", {"pantheon_plus"})
    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["pantheon_plus"],
        n_samples=2000, random_seed=42, allow_emcee_fallback=True,
    )
    assert r["sampler"] != "compressed_gaussian_analytic", (
        "Pantheon+ fell through to the compressed Gaussian instead of the full-cov fit"
    )
    om = r["parameters"]["omegam"]["median"]
    assert 0.30 <= om <= 0.37, f"Om={om} not near Brout 2022 0.334 +/- 0.018"
