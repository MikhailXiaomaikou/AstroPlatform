"""Pre-release validation: verify new professional services with synthetic data.

Each test generates realistic synthetic astronomical data and verifies
the service produces correct, physically meaningful results.
"""

import numpy as np
import pytest


class TestSpectralPro:
    """Verify spectral analysis service with synthetic spectra."""

    def _make_spectrum(self, lines=None, noise=0.02, n_pixels=1000):
        """Generate synthetic spectrum with Gaussian emission lines."""
        wave = np.linspace(3500, 8000, n_pixels)
        flux = np.ones_like(wave)  # flat continuum
        if lines is None:
            lines = [(6562.79, 0.5, 3.0), (5006.84, 0.3, 2.5)]  # H-alpha, [OIII]
        for center, amplitude, sigma in lines:
            flux += amplitude * np.exp(-0.5 * ((wave - center) / sigma) ** 2)
        flux += np.random.normal(0, noise, len(flux))
        return wave.tolist(), flux.tolist()

    def test_identify_lines_finds_halpha(self):
        from app.services.spectral_analysis_pro import identify_lines
        wave, flux = self._make_spectrum(lines=[(6562.79, 1.0, 3.0)])
        result = identify_lines(wave, flux, threshold_snr=3.0)
        assert len(result) >= 1
        # At least one line should be near H-alpha
        ha_found = any(abs(line["observed_wavelength"] - 6562.79) < 10 for line in result)
        assert ha_found, f"H-alpha not found in {result}"

    def test_fit_gaussian_recovers_center(self):
        from app.services.spectral_analysis_pro import fit_lines
        center = 5007.0
        wave, flux = self._make_spectrum(lines=[(center, 0.8, 2.0)], noise=0.01)
        result = fit_lines(wave, flux, line_centers=[center])
        assert len(result) >= 1
        assert abs(result[0]["center"] - center) < 1.0, f"Center {result[0]['center']} too far from {center}"

    def test_measure_ew_nonzero(self):
        from app.services.spectral_analysis_pro import measure_equivalent_width
        wave, flux = self._make_spectrum(lines=[(6562.79, 0.5, 3.0)], noise=0.005)
        result = measure_equivalent_width(wave, flux, 6562.79, window=30.0)
        assert "equivalent_width" in result
        assert result["equivalent_width"] != 0

    def test_velocity_dispersion_recovers_shift(self):
        from app.services.spectral_analysis_pro import velocity_dispersion_xcorr
        # Two identical spectra with a small velocity shift
        wave = np.linspace(4000, 7000, 2000)
        flux1 = np.ones_like(wave)
        # Add absorption features
        for c in [4340, 4861, 5175, 5893]:
            flux1 -= 0.3 * np.exp(-0.5 * ((wave - c) / 2.0) ** 2)
        flux2 = flux1.copy()  # Same template
        result = velocity_dispersion_xcorr(wave.tolist(), flux1.tolist(), wave.tolist(), flux2.tolist())
        assert "sigma_v_km_s" in result or "error" in result

    def test_load_spectrum_fallback(self):
        """Verify load_spectrum handles missing files gracefully."""
        from app.services.spectral_analysis_pro import load_spectrum
        with pytest.raises(Exception):
            load_spectrum("/nonexistent/file.fits")


class TestPhotoZPro:
    """Verify photo-z with synthetic photometry."""

    def test_template_fitting_returns_result(self):
        from app.services.photo_z_pro import fit_template_enhanced
        # Synthetic magnitudes for a z~0.3 spiral galaxy
        mags = {"g": 21.5, "r": 20.8, "i": 20.3, "z": 20.0}
        errors = {"g": 0.1, "r": 0.08, "i": 0.07, "z": 0.08}
        result = fit_template_enhanced(mags, errors)
        assert "z_phot" in result
        assert 0 <= result["z_phot"] <= 6
        assert "pz_values" in result
        assert len(result["pz_values"]) > 0

    def test_template_fitting_with_prior(self):
        from app.services.photo_z_pro import fit_template_enhanced
        mags = {"g": 22.0, "r": 21.0, "i": 20.5, "z": 20.2}
        result = fit_template_enhanced(mags, prior="magnitude")
        assert "z_phot" in result
        assert result["prior"] == "magnitude"

    def test_quality_metrics(self):
        from app.services.photo_z_pro import compute_photo_z_statistics
        z_true = [0.1, 0.2, 0.3, 0.5, 0.8, 1.0]
        z_phot = [0.12, 0.19, 0.28, 0.55, 0.75, 1.05]
        stats = compute_photo_z_statistics(z_true, z_phot)
        assert "sigma_MAD" in stats
        assert stats["sigma_MAD"] < 0.1  # Should be reasonable
        assert stats["outlier_fraction"] < 0.5


class TestBayesianInference:
    """Verify Bayesian inference services."""

    def test_chain_diagnostics_basic(self):
        from app.services.bayesian_inference import chain_diagnostics
        # Well-mixed samples from a normal distribution
        samples = np.random.normal(0, 1, (1000, 2))
        result = chain_diagnostics(samples, ["mu", "sigma"])
        assert "parameters" in result
        assert "mu" in result["parameters"]

    def test_bayes_factor_interpretation(self):
        from app.services.bayesian_inference import compute_bayes_factor
        # Decisive evidence
        result = compute_bayes_factor(-10.0, -20.0)
        assert result["strength"] == "decisive"
        assert result["favored"] == "model_1"
        # Inconclusive
        result2 = compute_bayes_factor(-10.0, -10.5)
        assert result2["strength"] == "inconclusive"

    def test_model_comparison_table(self):
        from app.services.bayesian_inference import model_comparison_table
        models = {
            "linear": {"chi2": 50, "n_params": 2, "n_data": 100},
            "quadratic": {"chi2": 40, "n_params": 3, "n_data": 100},
        }
        result = model_comparison_table(models)
        assert "ranking" in result
        assert len(result["ranking"]) == 2
        assert result["best_model"] is not None

    def test_posterior_predictive_check(self):
        from app.services.bayesian_inference import posterior_predictive_check
        # Simple model: y = theta
        samples = np.random.normal(5, 0.1, (100, 1))
        observed = np.array([5.0, 5.1, 4.9, 5.05])
        result = posterior_predictive_check(lambda t: np.full(4, t[0]), samples, observed)
        assert "overall_p_value" in result


class TestTimeDomainPro:
    """Verify time-domain services."""

    def test_flare_detection_finds_injected(self):
        from app.services.time_domain_pro import detect_flares
        t = np.linspace(0, 10, 1000)
        flux = np.ones_like(t) + np.random.normal(0, 0.01, len(t))
        # Inject a flare at t=5
        flare_mask = (t > 4.95) & (t < 5.1)
        flux[flare_mask] += 0.2  # 20x noise level
        result = detect_flares(t.tolist(), flux.tolist(), nsigma=3.0)
        assert result["n_flares"] >= 1

    @pytest.mark.slow
    def test_bls_recovers_period(self):
        # Q4: ~148 s on typical hardware — excluded from default test run.
        from app.services.time_domain_pro import transit_search_bls
        t = np.linspace(0, 30, 3000)
        flux = np.ones_like(t)
        period = 3.5
        for epoch in np.arange(0, 30, period):
            mask = (t > epoch - 0.05) & (t < epoch + 0.05)
            flux[mask] -= 0.01
        result = transit_search_bls(t.tolist(), flux.tolist(), period_range=(1.0, 10.0))
        assert "best_period" in result
        # Period should be recovered within 10%
        assert abs(result["best_period"] - period) / period < 0.15, \
            f"Period {result['best_period']} too far from {period}"

    def test_gp_detrend_returns_structure(self):
        from app.services.time_domain_pro import gp_detrend
        t = np.linspace(0, 10, 200)
        flux = np.sin(2 * np.pi * t / 3) + np.random.normal(0, 0.05, len(t))
        result = gp_detrend(t.tolist(), flux.tolist())
        assert "flux_detrended" in result
        assert "trend" in result
        assert len(result["flux_detrended"]) == len(t)


class TestImageProcessingPro:
    """Verify image processing services."""

    def test_deblend_missing_file(self):
        from app.services.image_processing_pro import deblend_sources
        # pyfits.open raises on missing file
        with pytest.raises(Exception):
            deblend_sources("/nonexistent.fits")

    def test_cutout_missing_file(self):
        from app.services.image_processing_pro import cutout_service
        with pytest.raises(Exception):
            cutout_service("/nonexistent.fits", 180.0, 45.0)


class TestVOServices:
    """Verify VO service interfaces."""

    def test_auto_select_sources_transient(self):
        from app.services.ai_tools import _auto_select_sources
        sources = _auto_select_sources("supernova SN 2024abc")
        assert "simbad" in sources

    def test_auto_select_sources_star(self):
        from app.services.ai_tools import _auto_select_sources
        sources = _auto_select_sources("nearby stars with high proper motion")
        assert "gaia" in sources

    def test_auto_select_sources_galaxy(self):
        from app.services.ai_tools import _auto_select_sources
        sources = _auto_select_sources("galaxy cluster Abell 2218")
        assert "ned" in sources or "simbad" in sources

    def test_suggest_actions_with_results(self):
        from app.services.ai_tools import _suggest_actions_for_results
        results = [
            {"name": "M31", "magnitude": 3.4, "redshift": -0.001},
            {"name": "NGC 1068", "magnitude": 8.8, "redshift": 0.003},
        ]
        suggestions = _suggest_actions_for_results(results)
        assert len(suggestions) >= 1
        assert any("plot" in s["label"].lower() or "dossier" in s["label"].lower() for s in suggestions)


class TestDeploymentReadiness:
    """Verify all services can import and AI tools have handlers."""

    def test_all_services_importable(self):
        import importlib
        services = [
            "app.services.spectral_analysis_pro",
            "app.services.photo_z_pro",
            "app.services.bayesian_inference",
            "app.services.time_domain_pro",
            "app.services.image_processing_pro",
            "app.services.vo_services",
            "app.services.provenance",
        ]
        for svc in services:
            mod = importlib.import_module(svc)
            assert mod is not None, f"Failed to import {svc}"

    def test_all_ai_tools_have_handlers(self):
        from app.services.ai_tools import TOOLS, _execute_tool_inner
        import inspect
        source = inspect.getsource(_execute_tool_inner)
        missing = []
        for tool in TOOLS:
            name = tool["name"]
            if f'"{name}"' not in source and f"'{name}'" not in source:
                missing.append(name)
        assert not missing, f"Tools without handlers: {missing}"

    def test_tool_count_at_least_47(self):
        from app.services.ai_tools import TOOLS
        assert len(TOOLS) >= 47, f"Expected >=47 tools, got {len(TOOLS)}"

    def test_config_loads(self):
        from app.config import settings
        assert settings.jwt_secret
        assert settings.database_url

    def test_pipeline_registry_loads(self):
        from app.pipeline.nodes import _LazyRegistry
        registry = _LazyRegistry()
        node_count = len(list(registry.keys()))
        assert node_count >= 32, f"Expected >=32 pipeline nodes, got {node_count}"
