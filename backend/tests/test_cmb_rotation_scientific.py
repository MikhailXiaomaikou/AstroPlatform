"""Calibration and hard-prior regressions for the CMB rotation runner."""

from __future__ import annotations


def _fixture(cr, *, key: str, calibration_sigma: float):
    return cr.CMBRotationDatasetEntry(
        key=key,
        display_name="Unit-test observed EB/TB rotation",
        version="test fixture",
        observables=("EB", "TB", "beta_deg"),
        source_url="https://example.invalid/rotation",
        citations=(cr.CMBRotationCitation(label="Unit Test", year=2026),),
        covariance_provided=True,
        calibration_prior={
            "type": "gaussian",
            "mean_deg": 0.0,
            "sigma_deg": calibration_sigma,
        },
        execution_mode="compressed_gaussian",
        compressed_likelihood=cr.CMBRotationCompressedSpec(
            parameter="beta_deg",
            mean=0.0,
            sigma=0.1,
            source_locator="unit-test pre-calibration observed angle",
            approximation="Gaussian observed-angle likelihood before calibration marginalization",
        ),
    )


def test_calibration_width_changes_beta_uncertainty(monkeypatch):
    from app.services import cmb_rotation_likelihoods as cr

    narrow = _fixture(cr, key="rotation_narrow", calibration_sigma=0.01)
    wide = _fixture(cr, key="rotation_wide", calibration_sigma=1.0)
    monkeypatch.setitem(cr.CMB_ROTATION_DATASETS, narrow.key, narrow)
    monkeypatch.setitem(cr.CMB_ROTATION_DATASETS, wide.key, wide)

    r_narrow = cr.run_cmb_rotation_likelihood(
        dataset_keys=[narrow.key], random_seed=4, n_samples=8000,
    )
    r_wide = cr.run_cmb_rotation_likelihood(
        dataset_keys=[wide.key], random_seed=4, n_samples=8000,
    )

    assert r_narrow["parameters"]["beta_deg"]["std"] < 0.13
    assert r_wide["parameters"]["beta_deg"]["std"] > 0.9
    assert r_narrow["publication_ready"] is False
    assert r_wide["publication_ready"] is False


def test_beta_samples_and_interval_respect_hard_prior(monkeypatch):
    from app.services import cmb_rotation_likelihoods as cr

    entry = _fixture(cr, key="rotation_bounded", calibration_sigma=1.0)
    monkeypatch.setitem(cr.CMB_ROTATION_DATASETS, entry.key, entry)
    result = cr.run_cmb_rotation_likelihood(
        dataset_keys=[entry.key],
        priors={"beta_deg": [-0.1, 0.1]},
        random_seed=9,
        n_samples=4000,
    )
    summary = result["parameters"]["beta_deg"]

    assert -0.1 <= summary["hdi_low_94"] <= summary["hdi_high_94"] <= 0.1
    assert result["priors"]["beta_deg"] == [-0.1, 0.1]
