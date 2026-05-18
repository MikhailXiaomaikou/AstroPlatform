"""Tests for the template-based photometric redshift estimation module."""

import numpy as np
import pytest

from app.services.photo_z import (
    ALL_BANDS,
    _build_templates,
    _synth_mag,
    estimate_photo_z_template,
)

# NumPy 2.0 renamed trapz -> trapezoid
_trapz = getattr(np, "trapezoid", None) or np.trapz


class TestSyntheticEllipticalZ03:
    """Generate synthetic photometry for an E galaxy at z=0.3 and recover z."""

    @pytest.fixture()
    def elliptical_z03(self):
        """Create synthetic magnitudes for an E template at z=0.3."""
        z_true = 0.3
        wave_rest, template_fluxes = _build_templates(n_wave=80)
        e_flux = template_fluxes[0]  # Elliptical is index 0
        wave_obs = wave_rest * (1.0 + z_true)

        mags = {}
        for band in ALL_BANDS:
            m = _synth_mag(wave_obs, e_flux, band)
            if not np.isnan(m):
                mags[band] = m

        # Assign small photometric errors (0.05 mag)
        mag_errors = {b: 0.05 for b in mags}
        return mags, mag_errors, z_true

    def test_recovers_redshift(self, elliptical_z03):
        mags, errors, z_true = elliptical_z03
        result = estimate_photo_z_template(mags, errors)

        assert "z_phot" in result
        assert "z_err" in result
        assert "pdf_z" in result
        assert "best_template" in result
        assert "chi2_min" in result

        assert abs(result["z_phot"] - z_true) < 0.02, (
            f"z_phot={result['z_phot']:.4f}, expected ~{z_true}"
        )

    def test_best_template_is_elliptical(self, elliptical_z03):
        mags, errors, _ = elliptical_z03
        result = estimate_photo_z_template(mags, errors)
        assert result["best_template"] == "E"

    def test_pdf_is_normalised(self, elliptical_z03):
        mags, errors, _ = elliptical_z03
        result = estimate_photo_z_template(mags, errors)
        z_grid = np.array(result["z_grid"])
        pdf = np.array(result["pdf_z"])
        integral = _trapz(pdf, z_grid)
        assert abs(integral - 1.0) < 0.01, f"PDF integral = {integral:.4f}"


class TestMissingBands:
    """Verify the estimator works with a subset of bands."""

    @pytest.fixture()
    def sparse_photometry(self):
        """Only g, r, i bands available."""
        z_true = 0.3
        wave_rest, template_fluxes = _build_templates(n_wave=80)
        e_flux = template_fluxes[0]
        wave_obs = wave_rest * (1.0 + z_true)

        bands_subset = ["g", "r", "i"]
        mags = {}
        for band in bands_subset:
            m = _synth_mag(wave_obs, e_flux, band)
            if not np.isnan(m):
                mags[band] = m

        mag_errors = {b: 0.05 for b in mags}
        return mags, mag_errors, z_true

    def test_returns_result_with_three_bands(self, sparse_photometry):
        mags, errors, z_true = sparse_photometry
        result = estimate_photo_z_template(mags, errors)

        assert "z_phot" in result
        assert "z_err" in result
        assert "pdf_z" in result
        # With only 3 optical bands the precision is worse but it should
        # still return a plausible answer.
        assert 0.0 <= result["z_phot"] <= 2.0

    def test_none_values_are_skipped(self):
        """Bands set to None should be silently ignored."""
        wave_rest, template_fluxes = _build_templates(n_wave=80)
        e_flux = template_fluxes[0]
        wave_obs = wave_rest * 1.3  # z=0.3

        mags = {"g": None, "r": None, "i": None}
        # Provide valid magnitudes for a few bands
        for band in ["u", "g", "r", "i", "z"]:
            m = _synth_mag(wave_obs, e_flux, band)
            if not np.isnan(m):
                mags[band] = m
        # Set one to None explicitly
        mags["u"] = None

        mag_errors = {b: 0.05 for b in mags}
        mag_errors["u"] = None

        result = estimate_photo_z_template(mags, mag_errors)
        assert "z_phot" in result

    def test_too_few_bands_raises(self):
        """Fewer than 2 bands should raise ValueError."""
        with pytest.raises(ValueError, match="At least 2"):
            estimate_photo_z_template({"g": 20.0}, {"g": 0.1})


class TestEdgeCases:
    """Edge-case handling."""

    def test_nan_magnitudes_skipped(self):
        """NaN magnitudes should be treated as missing."""
        wave_rest, template_fluxes = _build_templates(n_wave=80)
        sbc_flux = template_fluxes[1]
        wave_obs = wave_rest * 1.5  # z=0.5

        mags = {}
        for band in ["g", "r", "i", "z", "J"]:
            mags[band] = _synth_mag(wave_obs, sbc_flux, band)
        mags["g"] = float("nan")

        errors = {b: 0.05 for b in mags}
        result = estimate_photo_z_template(mags, errors)
        assert "z_phot" in result

    def test_output_lists_match_length(self):
        """pdf_z and z_grid must have the same length."""
        wave_rest, template_fluxes = _build_templates(n_wave=80)
        e_flux = template_fluxes[0]
        wave_obs = wave_rest * 1.1  # z=0.1

        mags = {}
        for band in ALL_BANDS:
            m = _synth_mag(wave_obs, e_flux, band)
            if not np.isnan(m):
                mags[band] = m
        errors = {b: 0.05 for b in mags}

        result = estimate_photo_z_template(mags, errors)
        assert len(result["pdf_z"]) == len(result["z_grid"])
        assert len(result["z_grid"]) == 401
