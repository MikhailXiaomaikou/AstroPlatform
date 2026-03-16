"""Tests for built-in pipeline processing nodes."""

import numpy as np
import pytest

from app.pipeline.nodes.denoise import denoise
from app.pipeline.nodes.spectral_fit import spectral_fit, _gaussian
from app.pipeline.nodes.coord_transform import coord_transform
from app.pipeline.nodes.redshift import redshift_estimate
from app.pipeline.nodes.equivalent_width import equivalent_width
from app.pipeline.nodes.image_stack import image_stack


class TestDenoiseNode:
    """Test the sigma-clipping denoise node."""

    def test_removes_outliers(self):
        np.random.seed(42)
        clean = np.sin(np.linspace(0, 4 * np.pi, 200)) * 10.0
        noisy = clean.copy()
        # Inject large outliers
        noisy[10] = 500.0
        noisy[50] = -300.0
        noisy[100] = 400.0

        result = denoise(
            {"data": {"flux": noisy.tolist()}},
            {"sigma": 3.0},
        )
        output_flux = np.array(result["data"]["flux"])

        # Outlier positions should now be interpolated to reasonable values
        assert abs(output_flux[10]) < 100
        assert abs(output_flux[50]) < 100
        assert abs(output_flux[100]) < 100
        assert result["denoise_stats"]["clipped_count"] >= 3

    def test_clean_data_unchanged(self):
        flux = np.sin(np.linspace(0, 2 * np.pi, 100)).tolist()
        result = denoise({"data": {"flux": flux}}, {"sigma": 3.0})
        output = np.array(result["data"]["flux"])
        np.testing.assert_allclose(output, flux, atol=1e-10)
        assert result["denoise_stats"]["clipped_count"] == 0

    def test_missing_flux_key_raises(self):
        with pytest.raises(ValueError, match="no 'flux' column"):
            denoise({"data": {"wavelength": [1, 2, 3]}}, {})

    def test_custom_flux_key(self):
        flux = [1.0, 2.0, 100.0, 3.0, 4.0]
        result = denoise(
            {"data": {"counts": flux}},
            {"flux_key": "counts", "sigma": 2.0},
        )
        assert "counts" in result["data"]


class TestSpectralFitNode:
    """Test Gaussian and Lorentzian spectral fitting."""

    def test_gaussian_fit_recovers_parameters(self):
        x = np.linspace(4800, 5200, 500)
        true_amp, true_mean, true_std = 100.0, 5007.0, 10.0
        y = _gaussian(x, true_amp, true_mean, true_std)
        # Add small noise
        np.random.seed(0)
        y += np.random.normal(0, 0.5, len(y))

        result = spectral_fit(
            {"data": {"index": x.tolist(), "flux": y.tolist()}},
            {"model": "gaussian"},
        )

        assert result["fit_result"]["success"] is True
        params = result["fit_result"]["params"]
        assert abs(params["mean"]["value"] - true_mean) < 1.0
        assert abs(params["stddev"]["value"] - true_std) < 2.0
        assert abs(params["amplitude"]["value"] - true_amp) < 5.0

    def test_lorentzian_fit(self):
        x = np.linspace(4800, 5200, 300)
        y = 50.0 * 5.0**2 / ((x - 5007.0) ** 2 + 5.0**2)

        result = spectral_fit(
            {"data": {"index": x.tolist(), "flux": y.tolist()}},
            {"model": "lorentzian"},
        )
        assert result["fit_result"]["success"] is True
        assert abs(result["fit_result"]["params"]["center"]["value"] - 5007.0) < 2.0

    def test_unknown_model_raises(self):
        with pytest.raises(ValueError, match="Unknown model"):
            spectral_fit(
                {"data": {"index": [1, 2], "flux": [1, 2]}},
                {"model": "quadratic"},
            )

    def test_empty_data_raises(self):
        with pytest.raises(ValueError, match="empty input"):
            spectral_fit({"data": {"index": [], "flux": []}}, {})


class TestCoordTransformNode:
    """Test coordinate frame transformations."""

    def test_icrs_to_galactic(self):
        # Galactic center is roughly RA=266.4, Dec=-28.9
        result = coord_transform(
            {"data": {"ra": [266.4], "dec": [-28.9]}},
            {"from_frame": "icrs", "to_frame": "galactic"},
        )

        assert "l" in result["data"]
        assert "b" in result["data"]
        # Galactic center should be near l=0, b=0
        assert abs(result["data"]["l"][0]) < 2.0
        assert abs(result["data"]["b"][0]) < 2.0

    def test_icrs_to_fk5_identity(self):
        """ICRS and FK5 (J2000) should be nearly identical."""
        ra_in = [180.0, 45.0]
        dec_in = [30.0, -15.0]

        result = coord_transform(
            {"data": {"ra": ra_in, "dec": dec_in}},
            {"from_frame": "icrs", "to_frame": "fk5"},
        )

        ra_out = result["data"]["ra_transformed"]
        dec_out = result["data"]["dec_transformed"]
        for i in range(len(ra_in)):
            assert abs(ra_out[i] - ra_in[i]) < 0.01
            assert abs(dec_out[i] - dec_in[i]) < 0.01

    def test_unsupported_frame_raises(self):
        with pytest.raises(ValueError, match="Unsupported frame"):
            coord_transform(
                {"data": {"ra": [0], "dec": [0]}},
                {"from_frame": "icrs", "to_frame": "supergalactic"},
            )

    def test_empty_coords_raises(self):
        with pytest.raises(ValueError, match="no coordinate data"):
            coord_transform({"data": {"ra": [], "dec": []}}, {})


class TestRedshiftEstimateNode:
    """Test redshift estimation with synthetic spectra."""

    def test_peak_method_known_redshift(self):
        """Create a spectrum with H-alpha shifted by z=0.5 and verify recovery."""
        z_true = 0.5
        ha_rest = 6563.0  # H-alpha
        hb_rest = 4861.0  # H-beta
        oiii_rest = 5007.0  # OIII

        wavelength = np.linspace(3500, 12000, 5000)
        flux = np.ones_like(wavelength) * 10.0  # continuum

        # Add emission lines at shifted wavelengths
        for line_rest in [ha_rest, hb_rest, oiii_rest]:
            obs = line_rest * (1 + z_true)
            flux += 50.0 * np.exp(-0.5 * ((wavelength - obs) / 5.0) ** 2)

        result = redshift_estimate(
            {"data": {"wavelength": wavelength.tolist(), "flux": flux.tolist()}},
            {"method": "peak"},
        )

        z_est = result["redshift_result"]["best_z"]
        assert abs(z_est - z_true) < 0.05, f"Estimated z={z_est}, expected z={z_true}"
        assert len(result["redshift_result"]["matched_lines"]) >= 2

    def test_empty_spectrum_raises(self):
        with pytest.raises(ValueError, match="empty"):
            redshift_estimate(
                {"data": {"wavelength": [], "flux": []}},
                {"method": "peak"},
            )

    def test_unknown_method_raises(self):
        with pytest.raises(ValueError, match="Unknown method"):
            redshift_estimate(
                {"data": {"wavelength": [1, 2, 3], "flux": [1, 2, 3]}},
                {"method": "fourier"},
            )


class TestEquivalentWidthNode:
    """Test equivalent width measurement with synthetic absorption lines."""

    def test_absorption_line_positive_ew(self):
        """Synthetic absorption line should give positive EW."""
        wavelength = np.linspace(5000, 5100, 500)
        continuum = 100.0  # flat continuum
        line_center = 5050.0
        line_depth = 60.0
        line_sigma = 2.0

        flux = continuum - line_depth * np.exp(
            -0.5 * ((wavelength - line_center) / line_sigma) ** 2
        )

        result = equivalent_width(
            {"data": {"wavelength": wavelength.tolist(), "flux": flux.tolist()}},
            {"line_center": line_center, "line_window": [5045, 5055]},
        )

        ew = result["equivalent_width_result"]["ew_value"]
        assert ew > 0, f"Expected positive EW for absorption, got {ew}"
        # For a Gaussian: EW = depth/continuum * sqrt(2*pi) * sigma ~ 0.6 * 2.507 * 2 ~ 3.0 A
        assert 1.0 < ew < 10.0

    def test_emission_line_negative_ew(self):
        """Synthetic emission line should give negative EW."""
        wavelength = np.linspace(6500, 6630, 500)
        continuum = 50.0
        line_center = 6563.0
        flux = continuum + 80.0 * np.exp(
            -0.5 * ((wavelength - line_center) / 3.0) ** 2
        )

        result = equivalent_width(
            {"data": {"wavelength": wavelength.tolist(), "flux": flux.tolist()}},
            {"line_center": line_center, "line_window": [6555, 6571]},
        )

        ew = result["equivalent_width_result"]["ew_value"]
        assert ew < 0, f"Expected negative EW for emission, got {ew}"

    def test_missing_line_center_raises(self):
        with pytest.raises(ValueError, match="line_center"):
            equivalent_width(
                {"data": {"wavelength": [1, 2, 3], "flux": [1, 2, 3]}},
                {},
            )


class TestImageStackNode:
    """Test image stacking with synthetic frames."""

    def test_mean_stack(self):
        np.random.seed(42)
        base_image = np.ones((32, 32)) * 100.0
        images = [(base_image + np.random.normal(0, 5, (32, 32))).tolist() for _ in range(10)]

        result = image_stack(
            {"data": {"images": images}},
            {"method": "mean"},
        )

        stacked = np.array(result["data"]["stacked_image"])
        assert stacked.shape == (32, 32)
        # Mean of 10 frames of ~100 should be close to 100
        assert abs(np.mean(stacked) - 100.0) < 2.0
        assert result["image_stack_result"]["n_images"] == 10

    def test_median_stack(self):
        np.random.seed(0)
        base = np.ones((16, 16)) * 50.0
        images = [(base + np.random.normal(0, 3, (16, 16))).tolist() for _ in range(5)]
        # Add a cosmic ray to one frame
        images[2][8][8] = 9999.0

        result = image_stack(
            {"data": {"images": images}},
            {"method": "median"},
        )

        stacked = np.array(result["data"]["stacked_image"])
        # Median should reject the cosmic ray
        assert stacked[8][8] < 100.0

    def test_sigma_clip_stack(self):
        np.random.seed(1)
        base = np.ones((16, 16)) * 75.0
        images = [(base + np.random.normal(0, 2, (16, 16))).tolist() for _ in range(8)]
        # Cosmic ray
        images[0][4][4] = 5000.0

        result = image_stack(
            {"data": {"images": images}},
            {"method": "sigma_clip", "sigma": 3.0},
        )

        stacked = np.array(result["data"]["stacked_image"])
        assert stacked[4][4] < 100.0

    def test_too_few_images_raises(self):
        with pytest.raises(ValueError, match="at least 2"):
            image_stack(
                {"data": {"images": [[[1, 2], [3, 4]]]}},
                {"method": "mean"},
            )

    def test_no_images_raises(self):
        with pytest.raises(ValueError, match="no 'images'"):
            image_stack({"data": {}}, {"method": "mean"})

    def test_unknown_method_raises(self):
        images = [[[1, 2], [3, 4]], [[5, 6], [7, 8]]]
        with pytest.raises(ValueError, match="Unknown stacking method"):
            image_stack({"data": {"images": images}}, {"method": "bogus"})
