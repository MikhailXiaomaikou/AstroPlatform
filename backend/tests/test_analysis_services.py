"""Unit tests for scientific analysis services.

Tests cover:
- compare_models (BIC-based model comparison)
- bootstrap_statistic (bootstrap resampling confidence intervals)
- lomb_scargle_period (periodic signal detection)
- _fit_blackbody (Planck spectrum fitting)
- detect_anomalies (ensemble anomaly detection)
- fit_isochrone (isochrone fitting)
"""

import numpy as np
import pandas as pd
import pytest

from app.services.astro_analysis import (
    bootstrap_statistic,
    compare_models,
    lomb_scargle_period,
)
from app.services.cross_wavelength import _fit_blackbody, _planck_flux_ratio
from app.services.anomaly_detection import detect_anomalies


class TestCompareModels:
    """Tests for the BIC/AIC model comparison function."""

    def test_lower_bic_preferred(self):
        from app.services.astro_analysis import compare_models

        result = compare_models(
            100,  # n_data as integer
            [
                {"name": "model_a", "chi2": 10.0, "n_params": 2},
                {"name": "model_b", "chi2": 50.0, "n_params": 3},
            ],
        )
        assert result["best_model"] == "model_a"
        # ranked list should have BIC values
        assert "BIC" in result["ranked"][0]
        assert result["ranked"][0]["name"] == "model_a"

    def test_single_model(self):
        from app.services.astro_analysis import compare_models

        result = compare_models(
            50,
            [
                {"name": "only", "chi2": 5.0, "n_params": 1},
            ],
        )
        assert result["best_model"] == "only"
        assert len(result["ranked"]) == 1
        assert result["ranked"][0]["verdict"] == "best"

    def test_data_dict_input(self):
        from app.services.astro_analysis import compare_models

        result = compare_models(
            {"x": list(range(80)), "y": list(range(80))},
            [
                {"name": "linear", "chi2": 20.0, "n_params": 2},
                {"name": "quad", "chi2": 18.0, "n_params": 3},
            ],
        )
        # Both should have AIC and BIC computed
        for entry in result["ranked"]:
            assert "AIC" in entry
            assert "BIC" in entry
            assert "chi2_reduced" in entry
            assert "delta_BIC" in entry

    def test_penalty_prefers_simpler_model(self):
        """When chi2 values are equal, the model with fewer params wins on BIC."""
        from app.services.astro_analysis import compare_models

        result = compare_models(
            100,
            [
                {"name": "simple", "chi2": 30.0, "n_params": 2},
                {"name": "complex", "chi2": 30.0, "n_params": 5},
            ],
        )
        assert result["best_model"] == "simple"

    def test_summary_present(self):
        from app.services.astro_analysis import compare_models

        result = compare_models(
            50,
            [
                {"name": "a", "chi2": 10.0, "n_params": 1},
                {"name": "b", "chi2": 40.0, "n_params": 2},
            ],
        )
        assert "summary" in result
        assert "Best model" in result["summary"]

    def test_verdict_labels(self):
        """Best model gets 'best' verdict; large delta_BIC gets 'decisive'."""
        result = compare_models(
            100,
            [
                {"name": "winner", "chi2": 50.0, "n_params": 2},
                {"name": "loser", "chi2": 150.0, "n_params": 2},
            ],
        )
        assert result["ranked"][0]["verdict"] == "best"
        # delta_BIC = 100, should be "decisive"
        assert result["ranked"][1]["verdict"] == "decisive"

    def test_delta_bic_is_zero_for_best(self):
        """Best model should have delta_BIC == 0."""
        result = compare_models(
            80,
            [
                {"name": "m1", "chi2": 60.0, "n_params": 3},
                {"name": "m2", "chi2": 70.0, "n_params": 3},
            ],
        )
        assert result["ranked"][0]["delta_BIC"] == 0.0

    def test_three_models_ordering(self):
        """Three models should be sorted by ascending BIC."""
        result = compare_models(
            100,
            [
                {"name": "worst", "chi2": 200.0, "n_params": 5},
                {"name": "best", "chi2": 80.0, "n_params": 2},
                {"name": "middle", "chi2": 95.0, "n_params": 3},
            ],
        )
        bics = [r["BIC"] for r in result["ranked"]]
        assert bics == sorted(bics)
        assert result["ranked"][0]["name"] == "best"

    def test_complex_model_wins_when_much_better_fit(self):
        """Complex model wins when chi2 improvement outweighs parameter penalty."""
        result = compare_models(
            200,
            [
                {"name": "linear", "chi2": 300.0, "n_params": 2},
                {"name": "quadratic", "chi2": 100.0, "n_params": 3},
            ],
        )
        assert result["best_model"] == "quadratic"
        assert result["ranked"][0]["name"] == "quadratic"


class TestBootstrap:
    """Tests for bootstrap resampling uncertainty estimation."""

    def test_ci_contains_true_mean(self):
        from app.services.astro_analysis import bootstrap_statistic

        rng = np.random.default_rng(42)
        data = rng.normal(10.0, 1.0, 200).tolist()
        result = bootstrap_statistic(data, n_bootstrap=500, seed=42)
        assert result["ci_lower"] < 10.0 < result["ci_upper"]

    def test_custom_statistic_func(self):
        from app.services.astro_analysis import bootstrap_statistic

        data = list(range(1, 101))
        result = bootstrap_statistic(
            data, statistic_func=np.median, n_bootstrap=200, seed=42
        )
        assert 40 < result["statistic"] < 60

    def test_output_keys(self):
        from app.services.astro_analysis import bootstrap_statistic

        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = bootstrap_statistic(data, n_bootstrap=100, seed=0)
        assert "statistic" in result
        assert "ci_lower" in result
        assert "ci_upper" in result
        assert "std" in result
        assert "samples" in result
        assert len(result["samples"]) == 100

    def test_narrow_ci_with_tight_data(self):
        """Nearly constant data should produce a very narrow CI."""
        from app.services.astro_analysis import bootstrap_statistic

        data = [5.0] * 100
        result = bootstrap_statistic(data, n_bootstrap=200, seed=1)
        assert result["ci_upper"] - result["ci_lower"] < 0.01

    def test_ci_ordering(self):
        """ci_lower should always be less than ci_upper."""
        data = np.random.default_rng(99).uniform(0, 100, 80)
        result = bootstrap_statistic(data, seed=42)
        assert result["ci_lower"] < result["ci_upper"]

    def test_statistic_equals_sample_mean(self):
        """The returned statistic should exactly equal the sample mean (default func)."""
        rng = np.random.default_rng(7)
        data = rng.normal(20.0, 3.0, 200)
        result = bootstrap_statistic(data, seed=42)
        assert abs(result["statistic"] - float(np.mean(data))) < 1e-10

    def test_n_bootstrap_samples(self):
        """The samples list should have exactly n_bootstrap entries."""
        data = np.random.default_rng(12).normal(0, 1, 50)
        n = 800
        result = bootstrap_statistic(data, n_bootstrap=n, seed=42)
        assert len(result["samples"]) == n

    def test_narrow_ci_with_large_sample(self):
        """Larger sample should produce narrower CI than small sample."""
        rng = np.random.default_rng(77)
        small = rng.normal(10.0, 1.0, 20)
        large = rng.normal(10.0, 1.0, 2000)

        r_small = bootstrap_statistic(small, n_bootstrap=2000, seed=42)
        r_large = bootstrap_statistic(large, n_bootstrap=2000, seed=42)

        width_small = r_small["ci_upper"] - r_small["ci_lower"]
        width_large = r_large["ci_upper"] - r_large["ci_lower"]
        assert width_large < width_small


class TestLombScargle:
    """Tests for Lomb-Scargle periodogram period detection."""

    def test_detects_known_period(self):
        from app.services.astro_analysis import lomb_scargle_period

        np.random.seed(42)
        t = np.sort(np.random.uniform(0, 100, 200))
        period = 10.0
        mag = np.sin(2 * np.pi * t / period) + 0.1 * np.random.randn(200)
        result = lomb_scargle_period(t.tolist(), mag.tolist())
        assert abs(result["best_period"] - period) < 1.0
        assert "fap" in result
        assert "reliable" in result

    def test_output_structure(self):
        from app.services.astro_analysis import lomb_scargle_period

        np.random.seed(0)
        t = np.sort(np.random.uniform(0, 50, 100))
        mag = np.sin(2 * np.pi * t / 5.0) + 0.05 * np.random.randn(100)
        result = lomb_scargle_period(t.tolist(), mag.tolist())
        assert "best_period" in result
        assert "best_power" in result
        assert "power" in result
        assert result["power"] == result["best_power"]
        assert "fap" in result
        assert "fap_level" in result
        assert "reliable" in result
        assert "n_datapoints" in result
        assert "top_periods" in result
        assert result["n_datapoints"] == 100

    def test_empty_input_raises(self):
        from app.services.astro_analysis import lomb_scargle_period

        with pytest.raises(ValueError, match="empty"):
            lomb_scargle_period([], [])

    def test_mismatched_lengths_raises(self):
        from app.services.astro_analysis import lomb_scargle_period

        with pytest.raises(ValueError, match="same length"):
            lomb_scargle_period([1.0, 2.0, 3.0], [1.0, 2.0])

    def test_reliable_flag_with_strong_signal(self):
        """Strong sinusoidal signal with enough points should be reliable."""
        true_period = 7.0
        rng = np.random.default_rng(88)
        time = np.sort(rng.uniform(0, 140, 200))
        mag = 12.0 + 1.0 * np.sin(2 * np.pi * time / true_period)
        mag += rng.normal(0, 0.01, len(time))

        result = lomb_scargle_period(
            time.tolist(), mag.tolist(), min_period=1.0, max_period=50.0
        )
        assert result["reliable"] is True
        assert result["fap"] < 0.01

    def test_fap_is_nonnegative(self):
        """False alarm probability should be non-negative."""
        rng = np.random.default_rng(33)
        time = np.sort(rng.uniform(0, 50, 80))
        mag = rng.normal(15.0, 0.1, len(time))

        result = lomb_scargle_period(
            time.tolist(), mag.tolist(), min_period=0.5, max_period=25.0
        )
        assert result["fap"] >= 0.0

    def test_n_datapoints_matches_input(self):
        """n_datapoints should match the input array length."""
        n = 60
        rng = np.random.default_rng(44)
        time = np.sort(rng.uniform(0, 30, n))
        mag = rng.normal(14.0, 0.05, n)

        result = lomb_scargle_period(time.tolist(), mag.tolist())
        assert result["n_datapoints"] == n


class TestBlackbodyFit:
    """Tests for the blackbody SED fitting helper."""

    def test_recovers_temperature(self):
        from app.services.cross_wavelength import _fit_blackbody, _planck_flux_ratio

        # Generate synthetic blackbody fluxes at 5000 K using the internal
        # Planck function so no external dependency is needed.
        true_T = 5000.0
        wavelengths_um = [0.4, 0.5, 0.6, 0.8, 1.0, 1.5]
        raw_fluxes = [_planck_flux_ratio(w, true_T) for w in wavelengths_um]
        # Normalize so the fit sees a realistic flux scale
        max_f = max(raw_fluxes)
        fluxes = [f / max_f for f in raw_fluxes]

        best_T, chi2, dof, T_err = _fit_blackbody(wavelengths_um, fluxes)
        assert 4000 < best_T < 6000, f"Expected ~5000 K, got {best_T}"
        assert chi2 < 1.0, f"Perfect data should give near-zero chi2, got {chi2}"

    def test_hot_temperature(self):
        from app.services.cross_wavelength import _fit_blackbody, _planck_flux_ratio

        true_T = 20000.0
        wavelengths_um = [0.3, 0.4, 0.5, 0.7, 1.0, 2.0]
        raw_fluxes = [_planck_flux_ratio(w, true_T) for w in wavelengths_um]
        max_f = max(raw_fluxes)
        fluxes = [f / max_f for f in raw_fluxes]

        best_T, chi2, dof, T_err = _fit_blackbody(wavelengths_um, fluxes)
        assert 15000 < best_T < 25000, f"Expected ~20000 K, got {best_T}"

    def test_returns_four_values(self):
        from app.services.cross_wavelength import _fit_blackbody, _planck_flux_ratio

        wavelengths_um = [0.5, 0.8, 1.2, 2.0]
        fluxes = [_planck_flux_ratio(w, 8000.0) for w in wavelengths_um]
        max_f = max(fluxes)
        fluxes = [f / max_f for f in fluxes]

        result = _fit_blackbody(wavelengths_um, fluxes)
        assert len(result) == 4
        best_T, chi2, dof, T_err = result
        assert isinstance(best_T, float)
        assert isinstance(chi2, float)
        assert isinstance(dof, int)
        assert isinstance(T_err, float)

    def test_dof_calculation(self):
        from app.services.cross_wavelength import _fit_blackbody, _planck_flux_ratio

        wavelengths_um = [0.4, 0.5, 0.6, 0.8, 1.0, 1.5, 2.0, 3.0]
        fluxes = [_planck_flux_ratio(w, 6000.0) for w in wavelengths_um]
        max_f = max(fluxes)
        fluxes = [f / max_f for f in fluxes]

        _, _, dof, _ = _fit_blackbody(wavelengths_um, fluxes)
        # dof = max(n_points - 2, 1) = max(8 - 2, 1) = 6
        assert dof == 6

    def test_cool_star_temperature(self):
        """Fit should recover a cool star temperature (3000K) reasonably."""
        true_T = 3000.0
        wavelengths_um = [0.6, 0.8, 1.0, 1.2, 1.6, 2.2, 3.4]
        raw_fluxes = [_planck_flux_ratio(w, true_T) for w in wavelengths_um]
        max_f = max(raw_fluxes)
        fluxes = [f / max_f for f in raw_fluxes]

        best_T, _, _, _ = _fit_blackbody(wavelengths_um, fluxes)
        assert abs(best_T - true_T) < 500.0, f"Expected ~3000 K, got {best_T}"

    def test_chi2_near_zero_for_perfect_data(self):
        """Perfect blackbody data should yield chi2/dof < 1."""
        true_T = 5000.0
        wavelengths_um = [0.4, 0.6, 0.8, 1.0, 1.5, 2.0]
        raw_fluxes = [_planck_flux_ratio(w, true_T) for w in wavelengths_um]
        max_f = max(raw_fluxes)
        fluxes = [f / max_f for f in raw_fluxes]

        best_T, chi2, dof, _ = _fit_blackbody(wavelengths_um, fluxes)
        assert chi2 / dof < 1.0

    def test_with_noisy_data(self):
        """Fit should still be reasonable with 5% noise added."""
        true_T = 5000.0
        rng = np.random.default_rng(42)
        wavelengths_um = [0.4, 0.5, 0.6, 0.8, 1.0, 1.2, 1.6, 2.2]
        raw_fluxes = np.array([_planck_flux_ratio(w, true_T) for w in wavelengths_um])
        max_f = raw_fluxes.max()
        fluxes = raw_fluxes / max_f
        # Add 5% Gaussian noise
        fluxes = fluxes * (1.0 + rng.normal(0, 0.05, len(fluxes)))
        fluxes = fluxes.tolist()

        best_T, _, _, _ = _fit_blackbody(wavelengths_um, fluxes)
        assert abs(best_T - true_T) < 500.0, f"Expected ~5000 K, got {best_T}"

    def test_t_err_is_nonnegative(self):
        """T_err should be a non-negative float."""
        wavelengths_um = [0.5, 0.7, 1.0, 1.5, 2.0]
        raw_fluxes = [_planck_flux_ratio(w, 6000.0) for w in wavelengths_um]
        max_f = max(raw_fluxes)
        fluxes = [f / max_f for f in raw_fluxes]

        _, _, _, T_err = _fit_blackbody(wavelengths_um, fluxes)
        assert isinstance(T_err, float)
        assert T_err >= 0


class TestDetectAnomalies:
    """Tests for detect_anomalies() from anomaly_detection module."""

    @staticmethod
    def _make_dataset_with_outliers(n_normal=200, n_outliers=10, seed=42):
        """Generate a DataFrame of normal points with injected outliers."""
        rng = np.random.default_rng(seed)

        # Normal cluster around (0, 0, 0)
        normal = rng.normal(0, 1, (n_normal, 3))
        # Outliers far from the cluster
        outliers = rng.normal(10, 0.5, (n_outliers, 3))

        all_data = np.vstack([normal, outliers])
        labels = np.array([False] * n_normal + [True] * n_outliers)

        df = pd.DataFrame(all_data, columns=["feat_a", "feat_b", "feat_c"])
        df["_is_true_outlier"] = labels
        return df

    def test_outliers_detected(self):
        """Injected outliers should largely be flagged by the ensemble."""
        df = self._make_dataset_with_outliers(n_normal=200, n_outliers=10)
        features = ["feat_a", "feat_b", "feat_c"]

        result = detect_anomalies(
            df,
            features=features,
            methods=["isolation_forest", "dbscan"],
            voting_threshold=1,
        )

        # At least half the injected outliers should be detected
        true_outlier_mask = result["_is_true_outlier"]
        detected_among_outliers = result.loc[true_outlier_mask, "is_anomaly"]
        assert detected_among_outliers.sum() >= 5

    def test_returned_columns(self):
        """Result DataFrame should contain expected ensemble columns."""
        df = self._make_dataset_with_outliers()
        features = ["feat_a", "feat_b", "feat_c"]

        result = detect_anomalies(
            df,
            features=features,
            methods=["isolation_forest", "dbscan"],
            voting_threshold=1,
        )

        for col in ("is_anomaly", "anomaly_score", "anomaly_methods", "anomaly_features"):
            assert col in result.columns, f"Missing column: {col}"

    def test_anomaly_score_range(self):
        """anomaly_score should be in [0, 1]."""
        df = self._make_dataset_with_outliers()
        features = ["feat_a", "feat_b", "feat_c"]

        result = detect_anomalies(
            df,
            features=features,
            methods=["isolation_forest", "dbscan"],
            voting_threshold=1,
        )

        assert result["anomaly_score"].min() >= 0.0
        assert result["anomaly_score"].max() <= 1.0 + 1e-9

    def test_small_dataset_skips_detection(self):
        """Fewer than 30 rows should skip detection and mark all as normal."""
        rng = np.random.default_rng(0)
        df = pd.DataFrame(rng.normal(0, 1, (20, 2)), columns=["x", "y"])

        result = detect_anomalies(df, features=["x", "y"])

        assert result["is_anomaly"].sum() == 0
        assert (result["anomaly_score"] == 0.0).all()

    def test_anomaly_methods_list_populated(self):
        """Flagged anomalies should have non-empty anomaly_methods list."""
        df = self._make_dataset_with_outliers(n_normal=200, n_outliers=15)
        features = ["feat_a", "feat_b", "feat_c"]

        result = detect_anomalies(
            df,
            features=features,
            methods=["isolation_forest", "dbscan"],
            voting_threshold=1,
        )

        flagged = result[result["is_anomaly"]]
        if len(flagged) > 0:
            for methods_list in flagged["anomaly_methods"]:
                assert len(methods_list) >= 1

    def test_isolation_forest_only(self):
        """Running with only isolation_forest should still produce valid output."""
        df = self._make_dataset_with_outliers()
        features = ["feat_a", "feat_b", "feat_c"]

        result = detect_anomalies(
            df,
            features=features,
            methods=["isolation_forest"],
            voting_threshold=1,
        )

        assert "is_anomaly" in result.columns
        assert "if_score" in result.columns
        assert "if_anomaly" in result.columns

    def test_auto_feature_selection(self):
        """Passing features=None should auto-select numeric columns, excluding ra/dec."""
        rng = np.random.default_rng(99)
        df = pd.DataFrame({
            "flux": rng.normal(0, 1, 100),
            "magnitude": rng.normal(15, 0.5, 100),
            "ra": rng.uniform(0, 360, 100),
            "dec": rng.uniform(-90, 90, 100),
            "label": ["star"] * 100,
        })
        # Inject outliers
        outlier_data = pd.DataFrame({
            "flux": rng.normal(10, 0.5, 10),
            "magnitude": rng.normal(25, 0.5, 10),
            "ra": rng.uniform(0, 360, 10),
            "dec": rng.uniform(-90, 90, 10),
            "label": ["outlier"] * 10,
        })
        df = pd.concat([df, outlier_data], ignore_index=True)

        result = detect_anomalies(
            df,
            features=None,
            methods=["isolation_forest"],
            voting_threshold=1,
        )

        assert "is_anomaly" in result.columns
        assert result["is_anomaly"].sum() > 0


class TestFitIsochrone:
    """Tests for isochrone fitting.

    The fit_isochrone function fetches PARSEC isochrones from a local API
    or the CMD web service, so these tests mock the isochrone data to
    avoid network calls and keep execution fast.
    """

    def test_returns_best_fit_structure(self):
        from unittest.mock import patch
        import pandas as pd
        from app.services.astro_analysis import fit_isochrone

        # Build a synthetic isochrone DataFrame that fit_isochrone expects
        n_iso = 100
        bp_rp_iso = np.linspace(-0.5, 3.5, n_iso)
        g_mag_iso = 2.0 + 3.0 * bp_rp_iso  # linear main-sequence-like relation

        iso_df = pd.DataFrame({
            "G_BPmag": bp_rp_iso + 0.5,  # BP = color + offset
            "G_RPmag": np.full(n_iso, 0.5),  # RP = offset
            "Gmag": g_mag_iso,
        })

        with patch("app.services.astro_analysis.get_isochrone", return_value=iso_df):
            np.random.seed(42)
            bp_rp = np.linspace(-0.3, 3.0, 30).tolist()
            abs_mag = [2.0 + 3.0 * x + 0.1 * np.random.randn() for x in bp_rp]

            result = fit_isochrone(bp_rp, abs_mag, n_grid_age=3, n_grid_met=2)

        assert "best_fit" in result
        assert "log_age" in result["best_fit"]
        assert "metallicity" in result["best_fit"]
        assert "distance_modulus" in result["best_fit"]
        assert "A_V" in result["best_fit"]
        assert "chi2" in result
        assert "chi2_reduced" in result
        assert "method" in result

    def test_returns_uncertainties_when_requested(self):
        from unittest.mock import patch
        import pandas as pd
        from app.services.astro_analysis import fit_isochrone

        n_iso = 100
        bp_rp_iso = np.linspace(-0.5, 3.5, n_iso)
        g_mag_iso = 2.0 + 3.0 * bp_rp_iso

        iso_df = pd.DataFrame({
            "G_BPmag": bp_rp_iso + 0.5,
            "G_RPmag": np.full(n_iso, 0.5),
            "Gmag": g_mag_iso,
        })

        with patch("app.services.astro_analysis.get_isochrone", return_value=iso_df):
            np.random.seed(42)
            bp_rp = np.linspace(-0.3, 3.0, 30).tolist()
            abs_mag = [2.0 + 3.0 * x + 0.1 * np.random.randn() for x in bp_rp]

            result = fit_isochrone(
                bp_rp, abs_mag,
                compute_errors=True,
                n_grid_age=3,
                n_grid_met=2,
            )

        assert "uncertainties" in result
        assert "log_age" in result["uncertainties"]

    def test_too_few_points_raises(self):
        from app.services.astro_analysis import fit_isochrone

        with pytest.raises(ValueError, match="at least 5"):
            fit_isochrone([0.5, 1.0], [10.0, 11.0])
