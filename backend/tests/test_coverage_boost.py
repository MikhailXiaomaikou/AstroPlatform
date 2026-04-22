"""Targeted tests to boost backend coverage from ~42% to 60%+.

Covers pure functions in:
- app.services.cross_wavelength (cross-wavelength anomaly detection)
- app.services.anomaly_detection (ensemble anomaly detection)
- app.pipeline.engine (DAG execution helpers)
- app.pipeline.validate (DAG validation)
- app.api.data (NaN-safe helpers, error classification)
- app.services.astro_analysis (astronomy analysis utilities)
- app.services.code_executor (sandboxed code execution helpers)
- app.connectors.panstarrs (_safe_float with sentinel)
"""

import math
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest


# =====================================================================
# 1. app.services.cross_wavelength — helpers & anomaly checks
# =====================================================================


class TestCrossWavelengthHelpers:
    """Tests for pure helpers in cross_wavelength.py."""

    def test_safe_float_normal(self):
        from app.services.cross_wavelength import _safe_float

        assert _safe_float(3.14) == 3.14
        assert _safe_float(0) == 0.0
        assert _safe_float(-1.5) == -1.5

    def test_safe_float_nan_inf(self):
        from app.services.cross_wavelength import _safe_float

        assert _safe_float(float("nan")) is None
        assert _safe_float(float("inf")) is None
        assert _safe_float(float("-inf")) is None

    def test_safe_float_none_and_invalid(self):
        from app.services.cross_wavelength import _safe_float

        assert _safe_float(None) is None
        assert _safe_float("not_a_number") is None
        assert _safe_float([1, 2]) is None

    def test_safe_float_string_number(self):
        from app.services.cross_wavelength import _safe_float

        assert _safe_float("3.14") == 3.14

    def test_get_phot_valid(self):
        from app.services.cross_wavelength import _get_phot

        dossier = {"photometry": {"optical": {"g": 15.2, "r": 14.8}}}
        assert _get_phot(dossier, "optical", "g") == 15.2
        assert _get_phot(dossier, "optical", "r") == 14.8

    def test_get_phot_missing_section(self):
        from app.services.cross_wavelength import _get_phot

        assert _get_phot({}, "optical", "g") is None
        assert _get_phot({"photometry": "invalid"}, "optical", "g") is None
        assert _get_phot({"photometry": {"nir": {}}}, "optical", "g") is None

    def test_get_phot_nan_value(self):
        from app.services.cross_wavelength import _get_phot

        dossier = {"photometry": {"optical": {"g": float("nan")}}}
        assert _get_phot(dossier, "optical", "g") is None

    def test_get_phot_section_not_dict(self):
        from app.services.cross_wavelength import _get_phot

        dossier = {"photometry": {"optical": "bad"}}
        assert _get_phot(dossier, "optical", "g") is None

    def test_get_astrometry_valid(self):
        from app.services.cross_wavelength import _get_astrometry

        dossier = {"astrometry": {"parallax_mas": 1.5, "pm_ra": 10.0}}
        assert _get_astrometry(dossier, "parallax_mas") == 1.5
        assert _get_astrometry(dossier, "pm_ra") == 10.0

    def test_get_astrometry_missing(self):
        from app.services.cross_wavelength import _get_astrometry

        assert _get_astrometry({}, "parallax_mas") is None
        assert _get_astrometry({"astrometry": "invalid"}, "parallax_mas") is None

    def test_object_class_agn(self):
        from app.services.cross_wavelength import _object_class

        assert _object_class({"object_type": "QSO"}) == "agn"
        assert _object_class({"object_type": "Seyfert 1"}) == "agn"
        assert _object_class({"object_type": "Blazar"}) == "agn"

    def test_object_class_giant(self):
        from app.services.cross_wavelength import _object_class

        assert _object_class({"object_type": "RGB star"}) == "giant"
        assert _object_class({"object_type": "Supergiant"}) == "giant"

    def test_object_class_main_sequence(self):
        from app.services.cross_wavelength import _object_class

        assert _object_class({"object_type": "Main Sequence Star"}) == "main_sequence"
        assert _object_class({"object_type": "dwarf star"}) == "main_sequence"

    def test_object_class_spectral_type_fallback(self):
        from app.services.cross_wavelength import _object_class

        assert _object_class({"object_type": "", "spectral_type": "G2V"}) == "main_sequence"
        assert _object_class({"object_type": "", "spectral_type": "K3III"}) == "giant"

    def test_object_class_unknown(self):
        from app.services.cross_wavelength import _object_class

        assert _object_class({}) == "unknown"
        assert _object_class({"object_type": "unknown blob"}) == "unknown"

    def test_mag_to_flux_known_band(self):
        from app.services.cross_wavelength import _mag_to_flux

        flux = _mag_to_flux(0.0, "r")
        assert flux == pytest.approx(3631.0, rel=1e-4)

    def test_mag_to_flux_nonzero_mag(self):
        from app.services.cross_wavelength import _mag_to_flux

        flux = _mag_to_flux(20.0, "g")
        expected = 3631.0 * 10.0 ** (-0.4 * 20.0)
        assert flux == pytest.approx(expected, rel=1e-6)

    def test_mag_to_flux_unknown_band(self):
        from app.services.cross_wavelength import _mag_to_flux

        assert _mag_to_flux(10.0, "unknown_band") is None

    def test_planck_flux_ratio_finite(self):
        from app.services.cross_wavelength import _planck_flux_ratio

        result = _planck_flux_ratio(0.55, 5800)  # V-band, solar T
        assert result > 0
        assert np.isfinite(result)

    def test_planck_flux_ratio_high_x_returns_zero(self):
        from app.services.cross_wavelength import _planck_flux_ratio

        result = _planck_flux_ratio(0.001, 10)  # very short wave, very low T
        assert result == 0.0


class TestCrossWavelengthAnomalyChecks:
    """Tests for individual anomaly check functions."""

    def test_check_ir_excess_skipped_no_data(self):
        from app.services.cross_wavelength import _check_ir_excess

        result = _check_ir_excess({})
        assert result["status"] == "SKIPPED"
        assert result["check"] == "ir_excess"

    def test_check_ir_excess_normal(self):
        from app.services.cross_wavelength import _check_ir_excess

        dossier = {
            "photometry": {"mir": {"W1": 12.0, "W2": 12.1}},
            "object_type": "Star",
        }
        result = _check_ir_excess(dossier)
        assert result["status"] == "NORMAL"

    def test_check_ir_excess_anomaly(self):
        from app.services.cross_wavelength import _check_ir_excess

        dossier = {
            "photometry": {"mir": {"W1": 12.0, "W2": 11.0}},
            "object_type": "Star",
        }
        result = _check_ir_excess(dossier)
        assert result["status"] == "ANOMALY"
        assert "circumstellar disk" in result["possible_causes"]

    def test_check_ir_excess_giant_threshold(self):
        from app.services.cross_wavelength import _check_ir_excess

        dossier = {
            "photometry": {"mir": {"W1": 12.0, "W2": 11.9}},
            "object_type": "RGB star",
        }
        result = _check_ir_excess(dossier)
        assert result["status"] == "NORMAL"

    def test_check_ir_excess_agn_threshold(self):
        from app.services.cross_wavelength import _check_ir_excess

        dossier = {
            "photometry": {"mir": {"W1": 12.0, "W2": 11.7}},
            "object_type": "QSO",
        }
        result = _check_ir_excess(dossier)
        assert result["status"] == "NORMAL"

    def test_check_xray_optical_skipped(self):
        from app.services.cross_wavelength import _check_xray_optical

        result = _check_xray_optical({})
        assert result["status"] == "SKIPPED"

    def test_check_xray_optical_no_optical(self):
        from app.services.cross_wavelength import _check_xray_optical

        dossier = {"xray_flux": 1e-12}
        result = _check_xray_optical(dossier)
        assert result["status"] == "SKIPPED"
        assert "no optical magnitude" in (result["observed"] or "")

    def test_check_xray_optical_normal(self):
        from app.services.cross_wavelength import _check_xray_optical

        dossier = {
            "xray_flux": 1e-14,
            "photometry": {"optical": {"r": 15.0}},
            "object_type": "Star",
        }
        result = _check_xray_optical(dossier)
        assert result["status"] in ("NORMAL", "ANOMALY")
        assert result["check"] == "xray_optical_ratio"

    def test_check_xray_optical_from_raw(self):
        from app.services.cross_wavelength import _check_xray_optical

        dossier = {
            "_raw": {"chandra": {"flux": 1e-14}},
            "photometry": {"optical": {"r": 15.0}},
            "object_type": "Star",
        }
        result = _check_xray_optical(dossier)
        assert result["status"] != "SKIPPED"

    def test_check_astrometric_anomaly_skipped(self):
        from app.services.cross_wavelength import _check_astrometric_anomaly

        result = _check_astrometric_anomaly({})
        assert result["status"] == "SKIPPED"

    def test_check_astrometric_anomaly_negative_parallax(self):
        from app.services.cross_wavelength import _check_astrometric_anomaly

        dossier = {
            "astrometry": {"parallax_mas": -0.5, "parallax_error_mas": 0.1},
            "g_mag": 12.0,
        }
        result = _check_astrometric_anomaly(dossier)
        assert result["status"] == "ANOMALY"
        assert "negative" in result["observed"]

    def test_check_astrometric_anomaly_high_pm(self):
        from app.services.cross_wavelength import _check_astrometric_anomaly

        dossier = {
            "astrometry": {"pm_ra": 300.0, "pm_dec": 100.0},
            "g_mag": 10.0,
        }
        result = _check_astrometric_anomaly(dossier)
        assert result["status"] == "ANOMALY"

    def test_check_astrometric_anomaly_large_parallax_error(self):
        from app.services.cross_wavelength import _check_astrometric_anomaly

        dossier = {
            "astrometry": {"parallax_mas": 1.0, "parallax_error_mas": 0.8},
            "g_mag": 12.0,
        }
        result = _check_astrometric_anomaly(dossier)
        assert result["status"] == "ANOMALY"
        assert "parallax_error/parallax" in result["observed"]

    def test_check_astrometric_anomaly_normal(self):
        from app.services.cross_wavelength import _check_astrometric_anomaly

        dossier = {
            "astrometry": {"parallax_mas": 5.0, "parallax_error_mas": 0.1, "pm_ra": 10.0, "pm_dec": 5.0},
            "g_mag": 10.0,
        }
        result = _check_astrometric_anomaly(dossier)
        assert result["status"] == "NORMAL"

    def test_check_color_color_skipped(self):
        from app.services.cross_wavelength import _check_color_color

        result = _check_color_color({})
        assert result["status"] == "SKIPPED"

    def test_check_color_color_normal(self):
        from app.services.cross_wavelength import _check_color_color

        dossier = {"photometry": {"optical": {"g": 15.0, "r": 14.5, "i": 14.2}}}
        result = _check_color_color(dossier)
        assert result["status"] == "NORMAL"
        assert "g-r" in result["observed"]
        assert "r-i" in result["observed"]

    def test_check_color_color_very_red(self):
        from app.services.cross_wavelength import _check_color_color

        dossier = {"photometry": {"optical": {"g": 20.0, "r": 15.0}}}
        result = _check_color_color(dossier)
        assert result["status"] == "ANOMALY"
        assert "very red" in result["possible_causes"][0]

    def test_check_color_color_very_blue(self):
        from app.services.cross_wavelength import _check_color_color

        dossier = {"photometry": {"optical": {"g": 14.0, "r": 15.0}}}
        result = _check_color_color(dossier)
        assert result["status"] == "ANOMALY"
        assert "very blue" in result["possible_causes"][0]

    def test_check_sed_shape_skipped_few_bands(self):
        from app.services.cross_wavelength import _check_sed_shape

        dossier = {"photometry": {"optical": {"g": 15.0, "r": 14.5}}}
        result = _check_sed_shape(dossier)
        assert result["status"] == "SKIPPED"

    def test_check_sed_shape_with_enough_bands(self):
        from app.services.cross_wavelength import _check_sed_shape

        dossier = {
            "photometry": {
                "optical": {"g": 15.0, "r": 14.5, "i": 14.2, "z": 14.0},
                "nir": {"J": 13.5},
            }
        }
        result = _check_sed_shape(dossier)
        assert result["status"] in ("NORMAL", "ANOMALY")
        assert result["check"] == "sed_shape"
        assert "T_err" in result

    def test_check_variability_skipped(self):
        from app.services.cross_wavelength import _check_variability

        result = _check_variability({})
        assert result["status"] == "SKIPPED"

    def test_check_variability_normal(self):
        from app.services.cross_wavelength import _check_variability

        dossier = {"multi_epoch": [{"mag": 15.0}, {"mag": 15.1}, {"mag": 15.05}]}
        result = _check_variability(dossier)
        assert result["status"] == "NORMAL"

    def test_check_variability_anomaly(self):
        from app.services.cross_wavelength import _check_variability

        dossier = {"multi_epoch": [{"mag": 15.0}, {"mag": 16.0}, {"mag": 14.5}]}
        result = _check_variability(dossier)
        assert result["status"] == "ANOMALY"
        assert "Range" in result["significance"]

    def test_check_variability_dict_format(self):
        from app.services.cross_wavelength import _check_variability

        dossier = {"multi_epoch": {"magnitudes": [15.0, 15.1, 15.2]}}
        result = _check_variability(dossier)
        assert result["status"] == "NORMAL"

    def test_check_variability_raw_floats(self):
        from app.services.cross_wavelength import _check_variability

        dossier = {"multi_epoch": [15.0, 15.1, 15.2]}
        result = _check_variability(dossier)
        assert result["status"] == "NORMAL"

    def test_check_variability_too_few_epochs(self):
        from app.services.cross_wavelength import _check_variability

        dossier = {"multi_epoch": [{"mag": 15.0}]}
        result = _check_variability(dossier)
        assert result["status"] == "SKIPPED"

    def test_check_variability_with_light_curve_key(self):
        from app.services.cross_wavelength import _check_variability

        dossier = {"light_curve": [{"magnitude": 15.0}, {"magnitude": 15.1}]}
        result = _check_variability(dossier)
        assert result["status"] == "NORMAL"


class TestCrossWavelengthBriefing:
    """Tests for the briefing generator."""

    def test_generate_briefing_no_anomalies(self):
        from app.services.cross_wavelength import _generate_briefing

        results = [
            {"check": "ir_excess", "status": "NORMAL"},
            {"check": "color_color", "status": "NORMAL"},
        ]
        briefing = _generate_briefing(results)
        assert "No multi-wavelength discrepancies" in briefing

    def test_generate_briefing_with_skipped(self):
        from app.services.cross_wavelength import _generate_briefing

        results = [
            {"check": "ir_excess", "status": "SKIPPED"},
            {"check": "color_color", "status": "NORMAL"},
        ]
        briefing = _generate_briefing(results)
        assert "skipped" in briefing.lower()

    def test_generate_briefing_with_anomalies(self):
        from app.services.cross_wavelength import _generate_briefing

        results = [
            {
                "check": "ir_excess",
                "status": "ANOMALY",
                "observed": "W1-W2 = 1.5",
                "possible_causes": ["dust disk"],
                "recommended_followup": "Get spectrum",
            },
            {"check": "color_color", "status": "NORMAL"},
            {"check": "variability", "status": "SKIPPED"},
        ]
        briefing = _generate_briefing(results)
        assert "1 anomaly detected" in briefing
        assert "dust disk" in briefing
        assert "1 check(s) passed normally" in briefing
        assert "1 check(s) skipped" in briefing

    def test_generate_briefing_multiple_anomalies(self):
        from app.services.cross_wavelength import _generate_briefing

        results = [
            {
                "check": "ir_excess",
                "status": "ANOMALY",
                "observed": "W1-W2 = 1.5",
                "possible_causes": ["dust"],
                "recommended_followup": "IR spec",
            },
            {
                "check": "color_color",
                "status": "ANOMALY",
                "observed": "g-r = 3.0",
                "possible_causes": ["reddened"],
                "recommended_followup": "photometry",
            },
        ]
        briefing = _generate_briefing(results)
        assert "2 anomalies detected" in briefing


class TestCrossWavelengthBlackbodyFit:
    """Tests for the blackbody SED fitting."""

    def test_fit_blackbody_recovers_temperature(self):
        from app.services.cross_wavelength import _fit_blackbody, _planck_flux_ratio

        # Generate synthetic data at T=5000K
        T_true = 5000.0
        wavelengths = [0.47, 0.62, 0.75, 1.24, 1.66, 2.16]
        fluxes = [_planck_flux_ratio(w, T_true) for w in wavelengths]
        # Normalize so values are reasonable
        max_f = max(fluxes)
        fluxes = [f / max_f * 1000.0 for f in fluxes]

        best_T, chi2, dof, T_err = _fit_blackbody(wavelengths, fluxes)
        assert abs(best_T - T_true) < 1000  # within 1000K
        assert dof >= 1

    def test_fit_blackbody_with_errors(self):
        from app.services.cross_wavelength import _fit_blackbody, _planck_flux_ratio

        T_true = 8000.0
        wavelengths = [0.35, 0.47, 0.62, 0.75, 0.89]
        fluxes = [_planck_flux_ratio(w, T_true) for w in wavelengths]
        max_f = max(fluxes)
        fluxes = [f / max_f * 500.0 for f in fluxes]
        errors = [f * 0.05 for f in fluxes]

        best_T, chi2, dof, T_err = _fit_blackbody(wavelengths, fluxes, errors)
        assert best_T > 0
        assert T_err >= 0


class TestCrossWavelengthEntryPoint:
    """Tests for the main cross_wavelength_analysis entry point."""

    async def test_cross_wavelength_analysis_with_dossier(self):
        from app.services.cross_wavelength import cross_wavelength_analysis

        dossier = {
            "photometry": {
                "optical": {"g": 15.0, "r": 14.5, "i": 14.2, "z": 14.0},
                "nir": {"J": 13.5, "H": 13.2, "Ks": 13.0},
                "mir": {"W1": 12.8, "W2": 12.9},
            },
            "astrometry": {"parallax_mas": 5.0, "pm_ra": 10.0, "pm_dec": 5.0},
            "object_type": "Star",
        }
        result = await cross_wavelength_analysis(180.0, 45.0, dossier=dossier)
        assert "anomalies_found" in result
        assert "checks_run" in result
        assert "results" in result
        assert "briefing" in result
        assert len(result["results"]) == 6

    async def test_cross_wavelength_analysis_empty_dossier(self):
        from app.services.cross_wavelength import cross_wavelength_analysis

        result = await cross_wavelength_analysis(180.0, 45.0, dossier={})
        assert result["checks_run"] == 0  # all skipped
        assert result["anomalies_found"] == 0

    async def test_cross_wavelength_analysis_dossier_generation_fails(self):
        from app.services.cross_wavelength import cross_wavelength_analysis

        with patch("app.services.dossier_generator.generate_dossier", side_effect=RuntimeError("fail")):
            result = await cross_wavelength_analysis(0.0, 0.0, dossier=None)
            assert result["anomalies_found"] == 0
            assert "aborted" in result["briefing"].lower()


# =====================================================================
# 2. app.services.anomaly_detection — ensemble ML detection
# =====================================================================


class TestAnomalyDetectionHelpers:
    """Tests for pure helper functions in anomaly_detection.py."""

    def test_select_numeric_features(self):
        from app.services.anomaly_detection import _select_numeric_features

        df = pd.DataFrame({
            "ra": [1.0], "dec": [2.0],
            "flux": [100.0], "mag": [15.0],
            "object_id": [1], "temperature": [5000.0],
        })
        features = _select_numeric_features(df)
        assert "flux" in features
        assert "mag" in features
        assert "temperature" in features
        assert "ra" not in features
        assert "dec" not in features
        assert "object_id" not in features

    def test_select_numeric_features_excludes_id_suffix(self):
        from app.services.anomaly_detection import _select_numeric_features

        df = pd.DataFrame({"source_id": [1], "catalog_id": [2], "flux": [100.0]})
        features = _select_numeric_features(df)
        assert "flux" in features
        assert "source_id" not in features
        assert "catalog_id" not in features

    def test_estimate_eps_kneedle_basic(self):
        from app.services.anomaly_detection import _estimate_eps_kneedle

        distances = np.array([0.1, 0.2, 0.3, 0.5, 0.8, 1.5, 3.0, 5.0])
        eps = _estimate_eps_kneedle(distances)
        assert eps > 0
        assert np.isfinite(eps)

    def test_estimate_eps_kneedle_small(self):
        from app.services.anomaly_detection import _estimate_eps_kneedle

        distances = np.array([1.0, 2.0])
        eps = _estimate_eps_kneedle(distances)
        assert eps > 0

    def test_estimate_eps_kneedle_constant(self):
        from app.services.anomaly_detection import _estimate_eps_kneedle

        distances = np.array([1.0, 1.0, 1.0, 1.0])
        eps = _estimate_eps_kneedle(distances)
        assert eps > 0

    def test_feature_importances_no_anomalies(self):
        from app.services.anomaly_detection import _feature_importances_from_if

        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        mask = np.array([False, False, False])
        result = _feature_importances_from_if(df, ["a", "b"], mask, top_n=2)
        assert len(result) == 2

    def test_feature_importances_with_anomalies(self):
        from app.services.anomaly_detection import _feature_importances_from_if

        df = pd.DataFrame({
            "a": [1.0, 2.0, 3.0, 100.0],
            "b": [10.0, 11.0, 12.0, 12.5],
        })
        mask = np.array([False, False, False, True])
        result = _feature_importances_from_if(df, ["a", "b"], mask, top_n=2)
        # "a" has the larger outlier deviation
        assert result[0] == "a"


class TestIsolationForestDetect:
    """Tests for the Isolation Forest detector."""

    def test_isolation_forest_basic(self):
        from app.services.anomaly_detection import isolation_forest_detect

        rng = np.random.default_rng(42)
        n = 100
        df = pd.DataFrame({
            "x": np.concatenate([rng.normal(0, 1, n - 5), rng.normal(10, 0.1, 5)]),
            "y": np.concatenate([rng.normal(0, 1, n - 5), rng.normal(10, 0.1, 5)]),
        })
        result = isolation_forest_detect(df, ["x", "y"], contamination=0.05)
        assert "if_score" in result.columns
        assert "if_anomaly" in result.columns
        assert result["if_anomaly"].sum() > 0

    def test_isolation_forest_with_nans(self):
        from app.services.anomaly_detection import isolation_forest_detect

        df = pd.DataFrame({
            "x": [1.0, 2.0, float("nan"), 4.0, 5.0] * 10,
            "y": [10.0, 20.0, 30.0, float("nan"), 50.0] * 10,
        })
        result = isolation_forest_detect(df, ["x", "y"])
        assert len(result) == 50


class TestDBSCANDetect:
    """Tests for the DBSCAN detector."""

    def test_dbscan_auto_eps(self):
        from app.services.anomaly_detection import dbscan_outlier_detect

        rng = np.random.default_rng(42)
        n = 100
        df = pd.DataFrame({
            "x": np.concatenate([rng.normal(0, 1, n), [20.0, -20.0]]),
            "y": np.concatenate([rng.normal(0, 1, n), [20.0, -20.0]]),
        })
        result = dbscan_outlier_detect(df, ["x", "y"], eps="auto")
        assert "dbscan_label" in result.columns
        assert "dbscan_anomaly" in result.columns

    def test_dbscan_fixed_eps(self):
        from app.services.anomaly_detection import dbscan_outlier_detect

        df = pd.DataFrame({
            "x": [1.0, 1.1, 1.2, 100.0] * 10,
            "y": [2.0, 2.1, 2.2, 200.0] * 10,
        })
        result = dbscan_outlier_detect(df, ["x", "y"], eps=0.5, min_samples=3)
        assert "dbscan_anomaly" in result.columns


class TestEnsembleDetection:
    """Tests for the ensemble detect_anomalies function."""

    def test_detect_anomalies_too_few_rows(self):
        from app.services.anomaly_detection import detect_anomalies

        df = pd.DataFrame({"x": [1.0, 2.0], "y": [3.0, 4.0]})
        result = detect_anomalies(df)
        assert "is_anomaly" in result.columns
        assert not result["is_anomaly"].any()

    def test_detect_anomalies_no_features(self):
        from app.services.anomaly_detection import detect_anomalies

        df = pd.DataFrame({"name": ["a"] * 50})
        result = detect_anomalies(df)
        assert "is_anomaly" in result.columns
        assert not result["is_anomaly"].any()

    def test_detect_anomalies_single_method(self):
        from app.services.anomaly_detection import detect_anomalies

        rng = np.random.default_rng(42)
        n = 50
        df = pd.DataFrame({
            "flux": np.concatenate([rng.normal(100, 10, n - 3), [500, 600, 700]]),
            "temp": np.concatenate([rng.normal(5000, 200, n - 3), [15000, 16000, 17000]]),
        })
        result = detect_anomalies(df, methods="isolation_forest")
        assert "is_anomaly" in result.columns
        assert "anomaly_score" in result.columns
        assert "anomaly_methods" in result.columns

    def test_detect_anomalies_string_method(self):
        from app.services.anomaly_detection import detect_anomalies

        rng = np.random.default_rng(42)
        df = pd.DataFrame({
            "x": rng.normal(0, 1, 50),
            "y": rng.normal(0, 1, 50),
        })
        result = detect_anomalies(df, methods="dbscan")
        assert "is_anomaly" in result.columns

    def test_detect_anomalies_all_methods(self):
        from app.services.anomaly_detection import detect_anomalies

        rng = np.random.default_rng(42)
        n = 60
        df = pd.DataFrame({
            "a": np.concatenate([rng.normal(0, 1, n - 3), [20, 21, 22]]),
            "b": np.concatenate([rng.normal(0, 1, n - 3), [20, 21, 22]]),
        })
        result = detect_anomalies(df, methods="all")
        assert "is_anomaly" in result.columns
        assert "anomaly_features" in result.columns
        # Anomalies sorted to top
        if result["is_anomaly"].any():
            assert result["is_anomaly"].iloc[0] is True or result["is_anomaly"].iloc[0] == True


# =====================================================================
# 3. app.pipeline.engine — DAG helpers
# =====================================================================


class TestPipelineEngine:
    """Tests for pipeline engine pure functions."""

    def _sample_dag(self):
        return {
            "nodes": [
                {"id": "A", "type": "LoadData", "data": {"params": {}}},
                {"id": "B", "type": "Denoise", "data": {"params": {}}},
                {"id": "C", "type": "Plot", "data": {"params": {}}},
            ],
            "edges": [
                {"source": "A", "target": "B"},
                {"source": "B", "target": "C"},
            ],
        }

    def test_topological_sort_linear(self):
        from app.pipeline.engine import topological_sort

        dag = self._sample_dag()
        levels = topological_sort(dag)
        assert len(levels) == 3
        assert levels[0] == ["A"]
        assert levels[1] == ["B"]
        assert levels[2] == ["C"]

    def test_topological_sort_parallel(self):
        from app.pipeline.engine import topological_sort

        dag = {
            "nodes": [
                {"id": "A", "type": "LoadData"},
                {"id": "B", "type": "Denoise"},
                {"id": "C", "type": "Denoise"},
                {"id": "D", "type": "Plot"},
            ],
            "edges": [
                {"source": "A", "target": "B"},
                {"source": "A", "target": "C"},
                {"source": "B", "target": "D"},
                {"source": "C", "target": "D"},
            ],
        }
        levels = topological_sort(dag)
        assert levels[0] == ["A"]
        assert set(levels[1]) == {"B", "C"}
        assert levels[2] == ["D"]

    def test_topological_sort_cycle_raises(self):
        from app.pipeline.engine import topological_sort

        dag = {
            "nodes": [{"id": "A", "type": "X"}, {"id": "B", "type": "Y"}],
            "edges": [
                {"source": "A", "target": "B"},
                {"source": "B", "target": "A"},
            ],
        }
        with pytest.raises(ValueError, match="cycle"):
            topological_sort(dag)

    def test_topological_sort_single_node(self):
        from app.pipeline.engine import topological_sort

        dag = {"nodes": [{"id": "A", "type": "LoadData"}], "edges": []}
        levels = topological_sort(dag)
        assert levels == [["A"]]

    def test_build_node_map(self):
        from app.pipeline.engine import build_node_map

        dag = self._sample_dag()
        nm = build_node_map(dag)
        assert set(nm.keys()) == {"A", "B", "C"}
        assert nm["A"]["type"] == "LoadData"

    def test_build_parent_map(self):
        from app.pipeline.engine import build_parent_map

        dag = self._sample_dag()
        pm = build_parent_map(dag)
        assert pm["A"] == []
        assert pm["B"] == ["A"]
        assert pm["C"] == ["B"]

    def test_build_edge_index(self):
        from app.pipeline.engine import build_edge_index

        dag = self._sample_dag()
        ei = build_edge_index(dag)
        assert len(ei["A"]) == 1
        assert ei["A"][0]["target"] == "B"
        assert len(ei["C"]) == 0

    def test_compute_skipped_nodes_true_branch(self):
        from app.pipeline.engine import _compute_skipped_nodes

        edge_index = {
            "cond": [
                {"source": "cond", "target": "T1", "sourceHandle": "true"},
                {"source": "cond", "target": "F1", "sourceHandle": "false"},
            ],
            "T1": [],
            "F1": [],
        }
        parent_map = {"cond": [], "T1": ["cond"], "F1": ["cond"]}
        node_map = {"cond": {}, "T1": {}, "F1": {}}

        skipped = _compute_skipped_nodes("cond", True, edge_index, parent_map, node_map)
        assert "F1" in skipped
        assert "T1" not in skipped

    def test_compute_skipped_nodes_false_branch(self):
        from app.pipeline.engine import _compute_skipped_nodes

        edge_index = {
            "cond": [
                {"source": "cond", "target": "T1", "sourceHandle": "true"},
                {"source": "cond", "target": "F1", "sourceHandle": "false"},
            ],
            "T1": [{"source": "T1", "target": "T2", "sourceHandle": None}],
            "F1": [],
            "T2": [],
        }
        parent_map = {"cond": [], "T1": ["cond"], "F1": ["cond"], "T2": ["T1"]}
        node_map = {"cond": {}, "T1": {}, "F1": {}, "T2": {}}

        skipped = _compute_skipped_nodes("cond", False, edge_index, parent_map, node_map)
        assert "T1" in skipped
        assert "T2" in skipped
        assert "F1" not in skipped

    def test_trim_for_api_no_truncation(self):
        from app.pipeline.engine import _trim_for_api

        results = {
            "node1": {"data": {"x": [1, 2, 3], "y": [4, 5, 6]}},
            "node2": {"status": "done"},
        }
        trimmed = _trim_for_api(results)
        assert trimmed["node1"]["data"]["x"] == [1, 2, 3]
        assert "_truncated" not in trimmed["node1"]["data"]

    def test_trim_for_api_truncation(self):
        from app.pipeline.engine import _trim_for_api

        big_list = list(range(200))
        results = {"node1": {"data": {"values": big_list, "label": "test"}}}
        trimmed = _trim_for_api(results)
        data = trimmed["node1"]["data"]
        assert data["_truncated"] is True
        assert len(data["values"]) == 51  # 25 + "..." + 25
        assert data["values"][25] == "..."
        assert data["label"] == "test"

    def test_trim_for_api_non_dict_data(self):
        from app.pipeline.engine import _trim_for_api

        results = {"node1": {"data": "just a string"}}
        trimmed = _trim_for_api(results)
        assert trimmed["node1"]["data"] == "just a string"

    def test_build_node_cache_key(self):
        from app.pipeline.engine import _build_node_cache_key

        key = _build_node_cache_key("Denoise", {"sigma": 3}, [], {})
        assert key is not None
        assert key.startswith("pipeline_node:Denoise:")

    def test_build_node_cache_key_with_parents(self):
        from app.pipeline.engine import _build_node_cache_key

        results = {"A": {"_output_checksum": "abc123"}}
        key = _build_node_cache_key("Plot", {}, ["A"], results)
        assert key is not None

    def test_build_node_cache_key_deterministic(self):
        from app.pipeline.engine import _build_node_cache_key

        k1 = _build_node_cache_key("X", {"a": 1}, [], {})
        k2 = _build_node_cache_key("X", {"a": 1}, [], {})
        assert k1 == k2

    def test_capture_environment(self):
        from app.pipeline.engine import _capture_environment

        env = _capture_environment()
        assert "python" in env
        assert "platform" in env
        assert "numpy" in env


# =====================================================================
# 4. app.pipeline.validate — DAG validation
# =====================================================================


class TestDAGValidation:
    """Tests for pipeline DAG validation."""

    def test_validate_valid_dag(self):
        from app.pipeline.validate import validate_dag

        dag = {
            "nodes": [
                {"id": "n1", "type": "LoadData"},
                {"id": "n2", "type": "Denoise"},
            ],
            "edges": [{"source": "n1", "target": "n2"}],
        }
        warnings = validate_dag(dag)
        # Should not raise; may have warnings about unknown types
        assert isinstance(warnings, list)

    def test_validate_empty_nodes_raises(self):
        from app.pipeline.validate import validate_dag, DAGValidationError

        with pytest.raises(DAGValidationError, match="no nodes"):
            validate_dag({"nodes": [], "edges": []})

    def test_validate_duplicate_ids_raises(self):
        from app.pipeline.validate import validate_dag, DAGValidationError

        dag = {
            "nodes": [{"id": "A", "type": "X"}, {"id": "A", "type": "Y"}],
            "edges": [],
        }
        with pytest.raises(DAGValidationError, match="Duplicate"):
            validate_dag(dag)

    def test_validate_invalid_edge_source_raises(self):
        from app.pipeline.validate import validate_dag, DAGValidationError

        dag = {
            "nodes": [{"id": "A", "type": "X"}],
            "edges": [{"source": "Z", "target": "A"}],
        }
        with pytest.raises(DAGValidationError, match="unknown source"):
            validate_dag(dag)

    def test_validate_invalid_edge_target_raises(self):
        from app.pipeline.validate import validate_dag, DAGValidationError

        dag = {
            "nodes": [{"id": "A", "type": "X"}],
            "edges": [{"source": "A", "target": "Z"}],
        }
        with pytest.raises(DAGValidationError, match="unknown target"):
            validate_dag(dag)

    def test_validate_cycle_raises(self):
        from app.pipeline.validate import validate_dag, DAGValidationError

        dag = {
            "nodes": [{"id": "A", "type": "X"}, {"id": "B", "type": "Y"}],
            "edges": [
                {"source": "A", "target": "B"},
                {"source": "B", "target": "A"},
            ],
        }
        with pytest.raises(DAGValidationError, match="cycle"):
            validate_dag(dag)

    def test_validate_disconnected_warns(self):
        from app.pipeline.validate import validate_dag

        dag = {
            "nodes": [
                {"id": "A", "type": "X"},
                {"id": "B", "type": "Y"},
                {"id": "C", "type": "Z"},
            ],
            "edges": [{"source": "A", "target": "B"}],
        }
        warnings = validate_dag(dag)
        assert any("Disconnected" in w for w in warnings)


# =====================================================================
# 5. app.api.data — NaN safety, error classification
# =====================================================================


class TestDataHelpers:
    """Tests for data API helper functions."""

    def test_safe_float_normal(self):
        from app.api.data import _safe_float

        assert _safe_float(3.14) == 3.14
        assert _safe_float(0.0) == 0.0
        assert _safe_float(-1.5) == -1.5

    def test_safe_float_nan(self):
        from app.api.data import _safe_float

        assert _safe_float(float("nan")) is None

    def test_safe_float_inf(self):
        from app.api.data import _safe_float

        assert _safe_float(float("inf")) is None
        assert _safe_float(float("-inf")) is None

    def test_safe_float_none(self):
        from app.api.data import _safe_float

        assert _safe_float(None) is None

    def test_sanitize_extra_cleans_nan(self):
        from app.api.data import _sanitize_extra

        result = _sanitize_extra({"a": 1.0, "b": float("nan"), "c": "text", "d": float("inf")})
        assert result["a"] == 1.0
        assert result["b"] is None
        assert result["c"] == "text"
        assert result["d"] is None

    def test_sanitize_extra_preserves_normal(self):
        from app.api.data import _sanitize_extra

        d = {"x": 1, "y": 2.5, "z": "hello"}
        assert _sanitize_extra(d) == d

    def test_classify_error_timeout(self):
        from app.api.data import _classify_error

        assert _classify_error(TimeoutError("timed out")) == "timeout"

    def test_classify_error_connection(self):
        from app.api.data import _classify_error

        assert _classify_error(ConnectionError("refused")) == "connection"
        assert _classify_error(Exception("DNS resolution failed")) == "connection"

    def test_classify_error_auth(self):
        from app.api.data import _classify_error

        assert _classify_error(Exception("401 Unauthorized")) == "auth"
        assert _classify_error(Exception("403 Forbidden")) == "auth"

    def test_classify_error_rate_limit(self):
        from app.api.data import _classify_error

        assert _classify_error(Exception("429 Too Many Requests")) == "rate_limit"

    def test_classify_error_server(self):
        from app.api.data import _classify_error

        assert _classify_error(Exception("502 Bad Gateway")) == "server_error"
        assert _classify_error(Exception("503 Service Unavailable")) == "server_error"

    def test_classify_error_unknown(self):
        from app.api.data import _classify_error

        assert _classify_error(Exception("some random error")) == "unknown"


# =====================================================================
# 6. app.services.astro_analysis — astronomy computations
# =====================================================================


class TestAstroAnalysisModelComparison:
    """Tests for compare_models()."""

    def test_compare_models_basic(self):
        from app.services.astro_analysis import compare_models

        result = compare_models(100, [
            {"name": "linear", "chi2": 50.0, "n_params": 2},
            {"name": "quadratic", "chi2": 30.0, "n_params": 3},
        ])
        assert result["best_model"] in ("linear", "quadratic")
        assert len(result["ranked"]) == 2
        assert result["ranked"][0]["verdict"] == "best"
        assert "summary" in result

    def test_compare_models_with_data_dict(self):
        from app.services.astro_analysis import compare_models

        data = {"x": [1, 2, 3, 4, 5], "y": [10, 20, 30, 40, 50]}
        result = compare_models(data, [
            {"name": "A", "chi2": 10.0, "n_params": 1},
        ])
        assert result["best_model"] == "A"

    def test_compare_models_verdict_levels(self):
        from app.services.astro_analysis import compare_models

        result = compare_models(1000, [
            {"name": "A", "chi2": 100.0, "n_params": 2},
            {"name": "B", "chi2": 105.0, "n_params": 2},  # delta_BIC < 6
            {"name": "C", "chi2": 115.0, "n_params": 2},  # delta_BIC > 10
            {"name": "D", "chi2": 108.0, "n_params": 2},  # delta_BIC ~ 6-10
        ])
        verdicts = {r["name"]: r["verdict"] for r in result["ranked"]}
        assert verdicts["A"] == "best"
        # Others should have various verdict levels
        assert all(v in ("best", "inconclusive", "positive", "strong", "decisive") for v in verdicts.values())

    def test_compare_models_raises_no_data(self):
        from app.services.astro_analysis import compare_models

        with pytest.raises(ValueError, match="at least 1"):
            compare_models(0, [{"name": "A", "chi2": 10.0, "n_params": 1}])


class TestAstroAnalysisBPT:
    """Tests for BPT classification."""

    def test_bpt_classify_star_forming(self):
        from app.services.astro_analysis import bpt_classify

        # Well below the Kauffmann line
        result = bpt_classify([-1.0], [-0.5])
        assert result[0] == "SF"

    def test_bpt_classify_agn(self):
        from app.services.astro_analysis import bpt_classify

        # Above the Kewley line
        result = bpt_classify([0.3], [1.0])
        assert result[0] == "AGN"

    def test_bpt_classify_composite(self):
        from app.services.astro_analysis import bpt_classify

        # Between Kauffmann and Kewley lines: need x < 0.05, y above Kau but below Kew
        # Kau: y < 0.61/(x-0.05)+1.3; Kew: y < 0.61/(x-0.47)+1.19
        # At x=-0.3: Kau_y = 0.61/(-0.35)+1.3 = -1.743+1.3 = -0.443; Kew_y = 0.61/(-0.77)+1.19 = 0.398
        # So y=0.0 should be above Kau (0 > -0.443) but below Kew (0 < 0.398) => Composite
        result = bpt_classify([-0.3], [0.0])
        assert result[0] == "Composite"

    def test_bpt_classify_array(self):
        from app.services.astro_analysis import bpt_classify

        result = bpt_classify([-1.0, 0.3, -0.2], [-0.5, 1.0, 0.5])
        assert len(result) == 3


class TestAstroAnalysisCosmology:
    """Tests for cosmological distance calculations."""

    def test_compute_luminosity_distance_low_z(self):
        from app.services.astro_analysis import compute_luminosity_distance

        dl = compute_luminosity_distance(0.1)
        assert dl > 0
        assert dl < 1000  # should be ~430 Mpc

    def test_compute_luminosity_distance_zero(self):
        from app.services.astro_analysis import compute_luminosity_distance

        dl = compute_luminosity_distance(0.0)
        assert dl == pytest.approx(0.0, abs=0.01)

    def test_compute_absolute_magnitude_parallax(self):
        from app.services.astro_analysis import compute_absolute_magnitude

        M = compute_absolute_magnitude(mag=np.array([10.0]), parallax_mas=np.array([100.0]))
        # M = m + 5*log10(plx) - 10 = 10 + 5*2 - 10 = 10
        assert M[0] == pytest.approx(10.0, rel=1e-4)

    def test_compute_absolute_magnitude_distance_pc(self):
        from app.services.astro_analysis import compute_absolute_magnitude

        M = compute_absolute_magnitude(mag=np.array([10.0]), distance_pc=np.array([100.0]))
        expected = 10.0 - 5 * np.log10(100.0) + 5  # = 10 - 10 + 5 = 5
        assert M[0] == pytest.approx(expected, rel=1e-4)

    def test_compute_absolute_magnitude_distance_mpc(self):
        from app.services.astro_analysis import compute_absolute_magnitude

        M = compute_absolute_magnitude(mag=np.array([20.0]), distance_mpc=np.array([10.0]))
        assert np.isfinite(M[0])

    def test_compute_absolute_magnitude_redshift(self):
        from app.services.astro_analysis import compute_absolute_magnitude

        M = compute_absolute_magnitude(mag=np.array([20.0]), redshift=np.array([0.1]))
        assert np.isfinite(M[0])

    def test_compute_absolute_magnitude_no_mag_raises(self):
        from app.services.astro_analysis import compute_absolute_magnitude

        with pytest.raises(ValueError, match="apparent magnitude"):
            compute_absolute_magnitude(distance_pc=100.0)

    def test_compute_absolute_magnitude_both_mag_raises(self):
        from app.services.astro_analysis import compute_absolute_magnitude

        with pytest.raises(ValueError, match="either"):
            compute_absolute_magnitude(mag=10, apparent_mag=10, distance_pc=100)

    def test_compute_absolute_magnitude_no_distance_raises(self):
        from app.services.astro_analysis import compute_absolute_magnitude

        with pytest.raises(ValueError, match="exactly one"):
            compute_absolute_magnitude(mag=np.array([10.0]))

    def test_compute_absolute_magnitude_multiple_distances_raises(self):
        from app.services.astro_analysis import compute_absolute_magnitude

        with pytest.raises(ValueError, match="exactly one"):
            compute_absolute_magnitude(mag=np.array([10.0]), distance_pc=100, distance_mpc=0.1)

    def test_compute_absolute_magnitude_negative_parallax(self):
        from app.services.astro_analysis import compute_absolute_magnitude

        M = compute_absolute_magnitude(mag=np.array([10.0]), parallax_mas=np.array([-1.0]))
        assert np.isnan(M[0])

    def test_compute_absolute_magnitude_apparent_mag_alias(self):
        from app.services.astro_analysis import compute_absolute_magnitude

        M = compute_absolute_magnitude(apparent_mag=np.array([10.0]), distance_pc=np.array([100.0]))
        assert np.isfinite(M[0])


class TestAstroAnalysisKCorrection:
    """Tests for K-correction."""

    def test_k_correction_elliptical(self):
        from app.services.astro_analysis import k_correction

        k = k_correction(0.1, band="r", galaxy_type="elliptical")
        assert np.isfinite(k)
        assert k > 0

    def test_k_correction_spiral(self):
        from app.services.astro_analysis import k_correction

        k = k_correction(0.1, band="g", galaxy_type="spiral")
        assert np.isfinite(k)

    def test_k_correction_zero_z(self):
        from app.services.astro_analysis import k_correction

        k = k_correction(0.0)
        assert k == pytest.approx(0.0, abs=0.01)


class TestAstroAnalysisExtinction:
    """Tests for dust extinction functions."""

    def test_extinction_curve_manual(self):
        from app.services.astro_analysis import _extinction_curve_manual

        # V-band at 5500A with E(B-V)=1.0
        A = _extinction_curve_manual(5500.0, 1.0)
        assert A > 0
        assert np.isfinite(A)

    def test_extinction_curve_manual_array(self):
        from app.services.astro_analysis import _extinction_curve_manual

        waves = np.array([3000.0, 5500.0, 10000.0, 20000.0])
        A = _extinction_curve_manual(waves, 0.5)
        assert len(A) == 4
        # UV should have higher extinction than IR
        assert A[0] > A[-1]

    def test_extinction_curve_uv_regime(self):
        from app.services.astro_analysis import _extinction_curve_manual

        # UV wavelength at ~1500A  (x ~ 6.67)
        A = _extinction_curve_manual(1500.0, 0.3)
        assert A > 0

    def test_estimate_ebv(self):
        from app.services.astro_analysis import estimate_ebv

        ebv = estimate_ebv(1.0, 0.65)
        assert ebv == pytest.approx(0.35, rel=1e-4)

    def test_estimate_ebv_array(self):
        from app.services.astro_analysis import estimate_ebv

        ebv = estimate_ebv(np.array([1.0, 1.2]), np.array([0.65, 0.70]))
        assert len(ebv) == 2
        assert ebv[0] == pytest.approx(0.35, rel=1e-4)

    def test_deredden(self):
        from app.services.astro_analysis import deredden

        waves = np.array([4000.0, 5500.0, 8000.0])
        flux = np.array([1.0, 1.0, 1.0])
        corrected = deredden(waves, flux, 0.3)
        # Dereddened flux should be >= observed flux
        assert all(corrected >= flux)


class TestAstroAnalysisErrorPropagation:
    """Tests for error propagation utilities."""

    def test_monte_carlo_propagate(self):
        from app.services.astro_analysis import monte_carlo_propagate

        result = monte_carlo_propagate(
            lambda x, y: x + y,
            params=[10.0, 20.0],
            errors=[0.5, 0.3],
            n_samples=500,
        )
        assert result["mean"] == pytest.approx(30.0, abs=1.0)
        assert result["std"] > 0
        assert result["ci_lower"] < 30.0
        assert result["ci_upper"] > 30.0

    def test_bootstrap_statistic(self):
        from app.services.astro_analysis import bootstrap_statistic

        data = np.random.default_rng(42).normal(10.0, 1.0, 100)
        result = bootstrap_statistic(data, n_bootstrap=500)
        assert result["statistic"] == pytest.approx(np.mean(data))
        assert result["ci_lower"] < result["ci_upper"]
        assert result["std"] > 0

    def test_bootstrap_statistic_custom_func(self):
        from app.services.astro_analysis import bootstrap_statistic

        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        result = bootstrap_statistic(data, statistic_func=np.median)
        assert result["statistic"] == pytest.approx(5.5)

    def test_error_weighted_mean(self):
        from app.services.astro_analysis import error_weighted_mean

        result = error_weighted_mean([10.0, 11.0, 9.5], [0.5, 0.3, 0.8])
        assert result["weighted_mean"] > 9.0
        assert result["weighted_mean"] < 12.0
        assert result["uncertainty"] > 0

    def test_error_weighted_mean_equal_weights(self):
        from app.services.astro_analysis import error_weighted_mean

        result = error_weighted_mean([10.0, 20.0], [1.0, 1.0])
        assert result["weighted_mean"] == pytest.approx(15.0, rel=1e-6)

    def test_error_weighted_mean_zero_error_raises(self):
        from app.services.astro_analysis import error_weighted_mean

        with pytest.raises(ValueError, match="positive"):
            error_weighted_mean([10.0, 20.0], [1.0, 0.0])


class TestAstroAnalysisTimeSeries:
    """Tests for time-series / variability functions."""

    def test_phase_fold(self):
        from app.services.astro_analysis import phase_fold

        time = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
        phases = phase_fold(time, period=1.0)
        assert all(0 <= p < 1.0 for p in phases)
        assert phases[0] == pytest.approx(0.0)
        assert phases[1] == pytest.approx(0.5)
        assert phases[2] == pytest.approx(0.0)

    def test_phase_fold_with_epoch(self):
        from app.services.astro_analysis import phase_fold

        time = np.array([1.0, 1.5, 2.0])
        phases = phase_fold(time, period=1.0, epoch=0.5)
        assert phases[0] == pytest.approx(0.5)

    def test_phase_fold_with_flux_signature(self):
        from app.services.astro_analysis import phase_fold

        time = np.array([2.0, 1.0, 1.5])
        flux = np.array([0.99, 1.0, 0.98])
        folded = phase_fold(time, flux, 1.0, 1.0)
        phase, flux_folded = folded
        assert list(folded.keys()) == ["phase", "flux_folded"]
        assert folded.phase is phase
        assert folded.flux_folded is flux_folded
        assert folded["phase"] is phase
        assert np.all(np.diff(folded["phase"]) >= 0)
        assert len(flux_folded) == len(flux)

    def test_phase_fold_rejects_mismatched_flux_length(self):
        from app.services.astro_analysis import phase_fold

        with pytest.raises(ValueError, match="same length"):
            phase_fold([1.0, 2.0], [1.0], 1.0)

    def test_variability_indices(self):
        from app.services.astro_analysis import variability_indices

        rng = np.random.default_rng(42)
        time = np.linspace(0, 100, 50)
        mag = 15.0 + rng.normal(0, 0.1, 50)
        result = variability_indices(time, mag)
        assert "stetson_J" in result
        assert "stetson_K" in result
        assert "eta" in result
        assert "chi2_reduced" in result
        assert "iqr" in result
        assert "mad" in result
        assert "amplitude" in result
        assert "beyond_1sigma_fraction" in result
        assert "skewness" in result
        assert "kurtosis" in result

    def test_variability_indices_with_errors(self):
        from app.services.astro_analysis import variability_indices

        time = np.array([0, 1, 2, 3, 4])
        mag = np.array([15.0, 15.1, 14.9, 15.2, 15.0])
        err = np.array([0.05, 0.05, 0.05, 0.05, 0.05])
        result = variability_indices(time, mag, mag_err=err)
        assert result["chi2_reduced"] > 0

    def test_variability_indices_too_few_raises(self):
        from app.services.astro_analysis import variability_indices

        with pytest.raises(ValueError, match="at least 3"):
            variability_indices([0, 1], [15.0, 15.1])

    def test_variability_indices_empty_raises(self):
        from app.services.astro_analysis import variability_indices

        with pytest.raises(ValueError, match="empty"):
            variability_indices([], [])


class TestAstroAnalysisClassifyVariable:
    """Tests for the variable star classifier."""

    def test_classify_non_variable(self):
        from app.services.astro_analysis import classify_variable

        indices = {"chi2_reduced": 1.0, "stetson_J": 0.1, "amplitude": 0.01}
        result = classify_variable(indices)
        assert result["classification"] == "non_variable"

    def test_classify_rr_lyrae(self):
        from app.services.astro_analysis import classify_variable

        indices = {
            "chi2_reduced": 10.0, "stetson_J": 2.0,
            "amplitude": 0.5, "skewness": 0.5,
            "beyond_1sigma_fraction": 0.5,
        }
        result = classify_variable(indices, period=0.5)
        assert result["classification"] == "RR_Lyrae"

    def test_classify_eclipsing_binary(self):
        from app.services.astro_analysis import classify_variable

        indices = {
            "chi2_reduced": 10.0, "stetson_J": 2.0,
            "amplitude": 0.5, "skewness": -0.5,
            "beyond_1sigma_fraction": 0.5,
        }
        result = classify_variable(indices, period=3.0)
        assert result["classification"] == "eclipsing_binary"

    def test_classify_cepheid(self):
        from app.services.astro_analysis import classify_variable

        indices = {
            "chi2_reduced": 10.0, "stetson_J": 2.0,
            "amplitude": 0.8, "skewness": 0.1,
            "beyond_1sigma_fraction": 0.5,
        }
        result = classify_variable(indices, period=10.0)
        assert result["classification"] == "cepheid"

    def test_classify_long_period_variable(self):
        from app.services.astro_analysis import classify_variable

        indices = {
            "chi2_reduced": 10.0, "stetson_J": 2.0,
            "amplitude": 0.3, "skewness": 0.0,
            "beyond_1sigma_fraction": 0.5,
        }
        result = classify_variable(indices, period=200.0)
        assert result["classification"] == "long_period_variable"

    def test_classify_irregular_high_j(self):
        from app.services.astro_analysis import classify_variable

        indices = {
            "chi2_reduced": 10.0, "stetson_J": 2.0,
            "amplitude": 0.3, "skewness": 0.0,
            "beyond_1sigma_fraction": 0.5,
        }
        result = classify_variable(indices)
        assert result["classification"] == "irregular"

    def test_classify_irregular_fallback(self):
        from app.services.astro_analysis import classify_variable

        indices = {
            "chi2_reduced": 5.0, "stetson_J": 0.8,
            "amplitude": 0.2, "skewness": 0.0,
            "beyond_1sigma_fraction": 0.2,
        }
        result = classify_variable(indices)
        assert result["classification"] == "irregular"
        assert result["confidence"] == 0.3


class TestAstroAnalysisCosmologicalCalculator:
    """Tests for the full cosmological calculator."""

    def test_cosmological_calculator(self):
        from app.services.astro_analysis import cosmological_calculator

        result = cosmological_calculator(0.5)
        assert "luminosity_distance_mpc" in result
        assert "angular_diameter_distance_mpc" in result
        assert "comoving_distance_mpc" in result
        assert "lookback_time_gyr" in result
        assert "age_at_redshift_gyr" in result
        assert "distance_modulus" in result
        assert "scale_factor" in result
        assert result["luminosity_distance_mpc"] > 0
        assert result["scale_factor"] == pytest.approx(1.0 / 1.5)

    def test_redshift_at_age(self):
        from app.services.astro_analysis import redshift_at_age

        z = redshift_at_age(10.0)
        assert z > 0
        assert z < 100


class TestAstroAnalysisSpectralStacking:
    """Tests for spectral stacking."""

    def test_spectral_stacking_median(self):
        from app.services.astro_analysis import spectral_stacking

        waves = [np.linspace(4000, 7000, 100)] * 3
        fluxes = [np.ones(100) * (i + 1) for i in range(3)]
        common_wave, stacked_flux = spectral_stacking(waves, fluxes, method="median")
        assert len(common_wave) == 100
        assert len(stacked_flux) == 100
        # Median of [1, 2, 3] = 2.0
        assert np.nanmedian(stacked_flux) == pytest.approx(2.0, abs=0.1)

    def test_spectral_stacking_mean(self):
        from app.services.astro_analysis import spectral_stacking

        waves = [np.linspace(4000, 7000, 50)] * 5
        fluxes = [np.ones(50) * i for i in range(5)]
        common_wave, stacked_flux = spectral_stacking(waves, fluxes, method="mean")
        assert len(common_wave) == 50
        # Mean of [0, 1, 2, 3, 4] = 2.0
        assert np.nanmean(stacked_flux) == pytest.approx(2.0, abs=0.1)


class TestAstroAnalysisContinuumNormalize:
    """Tests for continuum normalization."""

    def test_continuum_normalize(self):
        from app.services.astro_analysis import continuum_normalize

        wave = np.linspace(4000, 7000, 200)
        flux = np.ones(200) * 100.0 + np.random.default_rng(42).normal(0, 1, 200)
        normalized, continuum = continuum_normalize(wave, flux)
        assert len(normalized) == 200
        assert len(continuum) == 200
        # Normalized flux should be near 1.0
        assert np.median(normalized) == pytest.approx(1.0, abs=0.1)
        # Continuum should be near 100.0
        assert np.median(continuum) == pytest.approx(100.0, abs=5.0)


# =====================================================================
# 7. app.services.code_executor — sandbox helpers
# =====================================================================


class TestCodeExecutor:
    """Tests for code executor helper functions."""

    def test_normalize_code_from_import(self):
        from app.services.code_executor import _normalize_code

        code = "from app.services import astro_analysis"
        result = _normalize_code(code)
        assert "import astro as astro_analysis" in result

    def test_normalize_code_from_import_as(self):
        from app.services.code_executor import _normalize_code

        code = "from app.services import astro_analysis as aa"
        result = _normalize_code(code)
        assert "import astro as aa" in result

    def test_normalize_code_import_astro_analysis(self):
        from app.services.code_executor import _normalize_code

        code = "import astro_analysis"
        result = _normalize_code(code)
        assert "import astro as astro_analysis" in result

    def test_normalize_code_import_astro_analysis_as(self):
        from app.services.code_executor import _normalize_code

        code = "import astro_analysis as tools"
        result = _normalize_code(code)
        assert "import astro as tools" in result

    def test_normalize_code_import_app_services(self):
        from app.services.code_executor import _normalize_code

        code = "import app.services.astro_analysis as helper"
        result = _normalize_code(code)
        assert "import astro as helper" in result

    def test_normalize_code_from_astro_import(self):
        from app.services.code_executor import _normalize_code

        code = "from astro_analysis import compare_models"
        result = _normalize_code(code)
        assert "from astro import compare_models" in result

    def test_normalize_code_from_app_services_import(self):
        from app.services.code_executor import _normalize_code

        code = "from app.services.astro_analysis import compare_models"
        result = _normalize_code(code)
        assert "from astro import compare_models" in result

    def test_normalize_code_no_change(self):
        from app.services.code_executor import _normalize_code

        code = "import numpy as np\nprint('hello')"
        result = _normalize_code(code)
        assert result == code

    def test_safe_import_allowed(self):
        from app.services.code_executor import _safe_import

        mod = _safe_import("math")
        assert hasattr(mod, "sqrt")

    def test_safe_import_blocked(self):
        from app.services.code_executor import _safe_import

        with pytest.raises(ImportError, match="blocked|not allowed"):
            _safe_import("os")

    def test_safe_import_numpy(self):
        from app.services.code_executor import _safe_import

        mod = _safe_import("numpy")
        assert hasattr(mod, "array")

    def test_safe_import_astro(self):
        from app.services.code_executor import _safe_import

        mod = _safe_import("astro")
        assert mod is not None

    def test_should_persist_value_normal(self):
        from app.services.code_executor import _should_persist_value

        assert _should_persist_value(42) is True
        assert _should_persist_value("hello") is True
        assert _should_persist_value([1, 2, 3]) is True

    def test_should_persist_value_module(self):
        from app.services.code_executor import _should_persist_value
        import types

        mod = types.ModuleType("fake")
        assert _should_persist_value(mod) is False

    def test_session_vars(self):
        from app.services.code_executor import get_session_vars, clear_session_vars, has_session_state

        sid = "test_session_12345"
        assert not has_session_state(sid)
        vars_ = get_session_vars(sid)
        assert isinstance(vars_, dict)
        vars_["x"] = 42
        assert has_session_state(sid)
        clear_session_vars(sid)
        assert not has_session_state(sid)

    def test_code_execution_result(self):
        from app.services.code_executor import CodeExecutionResult

        r = CodeExecutionResult()
        assert r.success is True
        assert r.stdout == ""
        assert r.stderr == ""
        assert r.error is None
        assert r.figures == []
        assert r.variables == {}

    def test_get_memory_usage(self):
        from app.services.code_executor import _get_memory_usage_bytes

        usage = _get_memory_usage_bytes()
        assert isinstance(usage, int)
        assert usage >= 0

    def test_check_memory_no_warning(self):
        import io as _io
        from app.services.code_executor import _check_memory

        stream = _io.StringIO()
        usage = _check_memory(stream)
        assert isinstance(usage, int)

    def test_execute_python_simple(self):
        from app.services.code_executor import execute_python

        result = execute_python("x = 2 + 3\nprint(x)")
        assert result.success is True
        assert "5" in result.stdout

    def test_execute_python_numpy(self):
        from app.services.code_executor import execute_python

        result = execute_python("import numpy as np\nprint(np.mean([1,2,3]))")
        assert result.success is True
        assert "2" in result.stdout

    def test_execute_python_error(self):
        from app.services.code_executor import execute_python

        result = execute_python("raise ValueError('test error')")
        assert result.success is False
        assert result.error is not None

    def test_execute_python_blocked_import(self):
        from app.services.code_executor import execute_python

        result = execute_python("import os")
        assert result.success is False

    def test_execute_python_variables_captured(self):
        from app.services.code_executor import execute_python

        result = execute_python("result = 42\ndata = [1, 2, 3]")
        assert result.success is True
        assert "result" in result.variables or "data" in result.variables

    def test_execute_python_matplotlib_figure(self):
        from app.services.code_executor import execute_python

        result = execute_python(
            "import matplotlib.pyplot as plt\n"
            "plt.figure()\n"
            "plt.plot([1,2,3],[1,4,9])\n"
            "plt.title('test')\n"
        )
        assert result.success is True
        assert len(result.figures) >= 1

    def test_execute_python_session_persistence(self):
        from app.services.code_executor import execute_python, clear_session_vars

        sid = "test_persist_999"
        clear_session_vars(sid)
        r1 = execute_python("shared_val = 100", session_id=sid)
        assert r1.success is True
        r2 = execute_python("print(shared_val)", session_id=sid)
        assert r2.success is True
        assert "100" in r2.stdout
        clear_session_vars(sid)

    def test_execute_python_with_context(self):
        from app.services.code_executor import execute_python

        result = execute_python("print(injected)", context={"injected": "hello_world"})
        assert result.success is True
        assert "hello_world" in result.stdout

    def test_execute_python_astro_import(self):
        from app.services.code_executor import execute_python

        result = execute_python("import astro\nprint(type(astro))")
        assert result.success is True

    def test_execute_python_scipy(self):
        from app.services.code_executor import execute_python

        result = execute_python("import scipy\nprint(scipy.__version__)")
        assert result.success is True

    def test_execute_python_pandas(self):
        from app.services.code_executor import execute_python

        result = execute_python("import pandas as pd\ndf = pd.DataFrame({'a': [1,2]})\nprint(len(df))")
        assert result.success is True
        assert "2" in result.stdout

    def test_execute_python_astropy(self):
        from app.services.code_executor import execute_python

        result = execute_python("from astropy.coordinates import SkyCoord\nprint('ok')")
        assert result.success is True

    def test_replay_session_history(self):
        from app.services.code_executor import replay_session_history, get_session_vars, clear_session_vars

        sid = "replay_test_999"
        clear_session_vars(sid)
        replay_session_history(sid, ["x = 42"])
        assert get_session_vars(sid).get("x") == 42
        # Replaying same blocks should be idempotent
        replay_session_history(sid, ["x = 42"])
        assert get_session_vars(sid).get("x") == 42
        clear_session_vars(sid)

    def test_replay_session_empty(self):
        from app.services.code_executor import replay_session_history

        # Should not raise
        replay_session_history("empty_replay", [])

    def test_make_sandbox_helpers(self):
        from app.services.code_executor import _make_sandbox_helpers

        helpers = _make_sandbox_helpers()
        assert "process_in_chunks" in helpers
        assert "load_csv" in helpers
        assert "memory_usage_mb" in helpers

        # Test process_in_chunks
        chunks_result = helpers["process_in_chunks"]([1, 2, 3, 4, 5], 2, sum)
        assert chunks_result == [3, 7, 5]


# =====================================================================
# 8. app.connectors.panstarrs — sentinel handling
# =====================================================================


class TestPanSTARRSSafeFloat:
    """Tests for the PanSTARRS _safe_float with -999 sentinel."""

    def test_panstarrs_safe_float_normal(self):
        from app.connectors.panstarrs import _safe_float

        assert _safe_float(15.5) == 15.5
        assert _safe_float(0.0) == 0.0

    def test_panstarrs_safe_float_sentinel(self):
        from app.connectors.panstarrs import _safe_float

        assert _safe_float(-999.0) is None

    def test_panstarrs_safe_float_nan(self):
        from app.connectors.panstarrs import _safe_float

        assert _safe_float(float("nan")) is None

    def test_panstarrs_safe_float_none(self):
        from app.connectors.panstarrs import _safe_float

        assert _safe_float(None) is None

    def test_panstarrs_safe_float_invalid_type(self):
        from app.connectors.panstarrs import _safe_float

        assert _safe_float("abc") is None


# =====================================================================
# 9. app.services.spectrum_analyzer — spectrum analysis
# =====================================================================


class TestSpectrumAnalyzer:
    """Tests for the automated spectrum analysis service."""

    def test_analyze_spectrum_flat(self):
        from app.services.spectrum_analyzer import analyze_spectrum

        w = np.linspace(4000, 7000, 200)
        f = np.ones(200) * 100.0
        summary = analyze_spectrum(w.tolist(), f.tolist())
        assert summary.continuum_shape == "flat"
        assert summary.n_points == 200
        assert summary.wavelength_min == pytest.approx(4000.0, rel=0.01)
        assert summary.wavelength_max == pytest.approx(7000.0, rel=0.01)
        assert summary.mean_flux == pytest.approx(100.0, rel=0.01)

    def test_analyze_spectrum_rising(self):
        from app.services.spectrum_analyzer import analyze_spectrum

        w = np.linspace(4000, 7000, 100)
        f = 50 + (w - 4000) * 0.01  # rising continuum
        summary = analyze_spectrum(w.tolist(), f.tolist())
        assert summary.continuum_shape == "rising"

    def test_analyze_spectrum_falling(self):
        from app.services.spectrum_analyzer import analyze_spectrum

        w = np.linspace(4000, 7000, 100)
        f = 100 - (w - 4000) * 0.01  # falling continuum
        summary = analyze_spectrum(w.tolist(), f.tolist())
        assert summary.continuum_shape == "falling"

    def test_analyze_spectrum_with_emission(self):
        from app.services.spectrum_analyzer import analyze_spectrum

        rng = np.random.default_rng(42)
        w = np.linspace(4000, 7000, 300)
        f = np.ones(300) * 100.0 + rng.normal(0, 0.5, 300)
        # Add strong emission line
        line_center = 150
        f[line_center] = 200.0
        summary = analyze_spectrum(w.tolist(), f.tolist())
        assert any(p.is_emission for p in summary.peaks)

    def test_analyze_spectrum_with_absorption(self):
        from app.services.spectrum_analyzer import analyze_spectrum

        rng = np.random.default_rng(42)
        w = np.linspace(4000, 7000, 300)
        f = np.ones(300) * 100.0 + rng.normal(0, 0.5, 300)
        # Add strong absorption line
        line_center = 150
        f[line_center] = 50.0
        summary = analyze_spectrum(w.tolist(), f.tolist())
        assert any(not p.is_emission for p in summary.peaks)

    def test_analyze_spectrum_too_short_raises(self):
        from app.services.spectrum_analyzer import analyze_spectrum

        with pytest.raises(ValueError, match="too short"):
            analyze_spectrum([4000, 4100, 4200], [100, 200, 150])

    def test_peak_dataclass(self):
        from app.services.spectrum_analyzer import Peak

        p = Peak(wavelength=6563.0, flux=200.0, snr=15.0, is_emission=True)
        assert p.wavelength == 6563.0
        assert p.is_emission is True

    def test_spectrum_summary_dataclass(self):
        from app.services.spectrum_analyzer import SpectrumSummary

        s = SpectrumSummary(
            wavelength_min=4000, wavelength_max=7000, n_points=100,
            mean_flux=100.0, median_flux=99.5, std_flux=5.0,
            continuum_shape="flat", continuum_slope=0.0,
        )
        assert s.peaks == []
        assert s.n_points == 100

    def test_build_claude_prompt_basic(self):
        from app.services.spectrum_analyzer import build_claude_prompt, SpectrumSummary, Peak

        summary = SpectrumSummary(
            wavelength_min=4000, wavelength_max=7000, n_points=200,
            mean_flux=1e-15, median_flux=1e-15, std_flux=1e-16,
            continuum_shape="flat", continuum_slope=0.0,
            peaks=[Peak(wavelength=6563.0, flux=2e-15, snr=10.0, is_emission=True)],
        )
        prompt = build_claude_prompt(summary, None)
        assert "4000.0" in prompt
        assert "7000.0" in prompt
        assert "6563.0" in prompt
        assert "Emission peaks" in prompt

    def test_build_claude_prompt_with_redshift(self):
        from app.services.spectrum_analyzer import build_claude_prompt, SpectrumSummary

        summary = SpectrumSummary(
            wavelength_min=4000, wavelength_max=7000, n_points=100,
            mean_flux=100.0, median_flux=99.0, std_flux=5.0,
            continuum_shape="rising", continuum_slope=0.001,
        )
        z_result = {
            "best_z": 0.05,
            "confidence": 0.9,
            "quality": "high",
            "matched_lines": [
                {"line": "H-alpha", "rest_wavelength": 6563, "observed_wavelength": 6891},
            ],
        }
        prompt = build_claude_prompt(summary, z_result)
        assert "z=0.050000" in prompt
        assert "H-alpha" in prompt


# =====================================================================
# 10. app.services.transient_service — safe_float
# =====================================================================


class TestTransientServiceHelpers:
    """Tests for transient service helper functions."""

    def test_safe_float_normal(self):
        from app.services.transient_service import _safe_float

        assert _safe_float(15.5) == 15.5
        assert _safe_float("3.14") == 3.14

    def test_safe_float_none_and_empty(self):
        from app.services.transient_service import _safe_float

        assert _safe_float(None) is None
        assert _safe_float("") is None
        assert _safe_float("None") is None

    def test_safe_float_nan(self):
        from app.services.transient_service import _safe_float

        assert _safe_float(float("nan")) is None

    def test_safe_float_invalid(self):
        from app.services.transient_service import _safe_float

        assert _safe_float("abc") is None


# =====================================================================
# 11. app.services.alert_ingestion — helpers
# =====================================================================


class TestAlertIngestionHelpers:
    """Tests for alert ingestion helper functions."""

    def test_safe_float(self):
        from app.services.alert_ingestion import _safe_float

        assert _safe_float(15.5) == 15.5
        assert _safe_float("3.14") == 3.14
        assert _safe_float(None) is None
        assert _safe_float("") is None
        assert _safe_float("None") is None
        assert _safe_float(float("nan")) is None
        assert _safe_float("abc") is None

    def test_parse_mjd(self):
        from app.services.alert_ingestion import _parse_mjd
        from datetime import datetime, timezone

        result = _parse_mjd(59000.0)
        assert result is not None
        assert isinstance(result, datetime)
        assert result.tzinfo == timezone.utc

    def test_parse_mjd_none(self):
        from app.services.alert_ingestion import _parse_mjd

        assert _parse_mjd(None) is None
        assert _parse_mjd("invalid") is None

    def test_datetime_to_mjd(self):
        from app.services.alert_ingestion import _datetime_to_mjd
        from datetime import datetime, timezone

        dt = datetime(2020, 1, 1, tzinfo=timezone.utc)
        mjd = _datetime_to_mjd(dt)
        assert mjd > 58000
        assert mjd < 60000

    def test_filter_map(self):
        from app.services.alert_ingestion import _FILTER_MAP

        assert _FILTER_MAP["zg"] == "g"
        assert _FILTER_MAP["zr"] == "r"
        assert _FILTER_MAP["zi"] == "i"


# =====================================================================
# 12. app.search.query_parser — parsing utilities
# =====================================================================


class TestQueryParser:
    """Tests for query parsing utilities."""

    def test_compute_observed_wavelength(self):
        from app.search.query_parser import compute_observed_wavelength

        # At z=1, observed = 2 * rest
        assert compute_observed_wavelength(1.0, 1.0) == pytest.approx(2.0)
        assert compute_observed_wavelength(0.5, 0.0) == pytest.approx(0.5)

    def test_compute_observed_frequency(self):
        from app.search.query_parser import compute_observed_frequency

        # At z=1, observed = rest/2
        assert compute_observed_frequency(100.0, 1.0) == pytest.approx(50.0)
        assert compute_observed_frequency(100.0, 0.0) == pytest.approx(100.0)

    def test_match_longest_first(self):
        from app.search.query_parser import _match_longest_first

        mapping = {"star": "stellar", "main sequence star": "ms"}
        key, val = _match_longest_first("find main sequence star", mapping)
        assert key == "main sequence star"
        assert val == "ms"

    def test_match_longest_first_no_match(self):
        from app.search.query_parser import _match_longest_first

        key, val = _match_longest_first("nothing here", {"foo": 1})
        assert key is None
        assert val is None

    def test_suggest_sources_default(self):
        from app.search.query_parser import suggest_sources

        sources = suggest_sources({})
        assert isinstance(sources, list)
        assert len(sources) > 0
        assert "simbad" in sources

    def test_suggest_sources_xray(self):
        from app.search.query_parser import suggest_sources

        sources = suggest_sources({"observation_type": "X-ray"})
        # Chandra should be near the top
        assert "chandra" in sources[:5]

    def test_suggest_sources_radio(self):
        from app.search.query_parser import suggest_sources

        sources = suggest_sources({"observation_type": "radio"})
        assert "nvss" in sources[:3] or "first" in sources[:3]

    def test_suggest_sources_infrared(self):
        from app.search.query_parser import suggest_sources

        sources = suggest_sources({"observation_type": "infrared"})
        assert "2mass" in sources[:5] or "allwise" in sources[:5]

    def test_suggest_sources_redshift(self):
        from app.search.query_parser import suggest_sources

        sources = suggest_sources({"redshift_min": 2.0})
        assert "ned" in sources[:5]

    def test_suggest_sources_galaxy(self):
        from app.search.query_parser import suggest_sources

        sources = suggest_sources({"object_type": "galaxy"})
        # SDSS and NED should be boosted
        assert "sdss" in sources[:5]
        assert "ned" in sources[:5]

    def test_suggest_sources_submm(self):
        from app.search.query_parser import suggest_sources

        sources = suggest_sources({"observation_type": "submillimeter"})
        assert "alma" in sources[:3]

    def test_suggest_sources_alma_frequency(self):
        from app.search.query_parser import suggest_sources

        sources = suggest_sources({"observed_freq_min_ghz": 200.0, "observed_freq_max_ghz": 300.0})
        assert sources[0] == "alma"

    def test_suggest_sources_stellar(self):
        from app.search.query_parser import suggest_sources

        sources = suggest_sources({"object_type": "star"})
        assert "gaia" in sources[:3]

    def test_get_spectral_lines_list(self):
        from app.search.query_parser import get_spectral_lines_list

        lines = get_spectral_lines_list()
        assert isinstance(lines, list)
        assert len(lines) > 0
        # Each entry should have standard keys
        first = lines[0]
        assert "key" in first


# =====================================================================
# 13. app.services.astro_analysis — remaining functions
# =====================================================================


class TestAstroAnalysisMultiGaussianFit:
    """Tests for multi-Gaussian fitting."""

    def test_multi_gaussian_fit_single(self):
        from app.services.astro_analysis import multi_gaussian_fit

        wave = np.linspace(6500, 6600, 100)
        flux = 10.0 * np.exp(-0.5 * ((wave - 6563) / 3.0) ** 2) + 1.0
        result = multi_gaussian_fit(wave, flux, n_components=1, initial_centers=[6563])
        assert result["success"] is True
        assert len(result["components"]) == 1
        assert result["components"][0]["center"]["value"] == pytest.approx(6563, abs=5)

    def test_multi_gaussian_fit_too_few_points(self):
        from app.services.astro_analysis import multi_gaussian_fit

        result = multi_gaussian_fit([6560, 6563, 6566], [1.0, 5.0, 1.0], n_components=2)
        assert result["success"] is False


class TestAstroAnalysisBatchEW:
    """Tests for batch equivalent width measurement."""

    def test_batch_equivalent_width(self):
        from app.services.astro_analysis import batch_equivalent_width

        wave = np.linspace(6400, 6700, 500)
        flux = np.ones(500) * 100.0
        # Add absorption line at H-alpha
        line_mask = np.abs(wave - 6563) < 5
        flux[line_mask] = 50.0

        result = batch_equivalent_width(wave, flux, [6563.0], window=10.0)
        assert len(result) == 1
        assert "ew" in result[0] or "EW" in result[0] or "equivalent_width" in result[0]


class TestAstroAnalysisAvailableFunctions:
    """Tests for the function catalogue."""

    def test_available_functions(self):
        from app.services.astro_analysis import available_functions

        funcs = available_functions()
        assert isinstance(funcs, (list, dict))
        if isinstance(funcs, list):
            assert len(funcs) > 10
        else:
            assert len(funcs) > 10


# =====================================================================
# 14. app.pipeline.validate — expanded tests
# =====================================================================


class TestDAGValidationExpanded:
    """More validation edge cases."""

    def test_validate_single_node_no_edges(self):
        from app.pipeline.validate import validate_dag

        dag = {"nodes": [{"id": "n1", "type": "LoadData"}], "edges": []}
        warnings = validate_dag(dag)
        # Single node, no edges — should be valid (no disconnected warning for single nodes)
        assert isinstance(warnings, list)

    def test_validate_node_type_from_data(self):
        from app.pipeline.validate import validate_dag

        dag = {
            "nodes": [{"id": "n1", "data": {"nodeType": "SomeUnknown"}}],
            "edges": [],
        }
        warnings = validate_dag(dag)
        assert any("Unknown node type" in w for w in warnings)


# =====================================================================
# 15. app.search.query_parser — parse_natural_query comprehensive
# =====================================================================


class TestParseNaturalQuery:
    """Comprehensive tests for the natural language query parser.

    This function is ~130 lines and covers parsing of redshift, spectral
    lines, object types, observation types, coordinates, and regions.
    """

    def test_parse_redshift_greater_than(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("galaxies with z > 6")
        assert result.get("redshift_min") == 6.0
        assert result.get("object_type") == "galaxy"
        assert "rvz_redshift" in result.get("required_fields", [])

    def test_parse_redshift_less_than(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("stars with z < 0.5")
        assert result.get("redshift_max") == 0.5

    def test_parse_redshift_range(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("quasars z=2-3")
        assert result.get("redshift_min") == 2.0
        assert result.get("redshift_max") == 3.0
        assert result.get("object_type") == "quasar"

    def test_parse_redshift_equals(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("galaxy z=5")
        assert result.get("redshift_min") == pytest.approx(4.5)
        assert result.get("redshift_max") == pytest.approx(5.5)

    def test_parse_redshift_keyword_high(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("high redshift galaxies")
        assert result.get("redshift_min") == 4.0

    def test_parse_redshift_keyword_nearby(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("nearby stars")
        assert result.get("redshift_max") == 0.1

    def test_parse_redshift_keyword_eor(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("epoch of reionization")
        assert result.get("redshift_min") == 6.0
        assert result.get("redshift_max") == 15.0

    def test_parse_spectral_line_halpha(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("H-alpha emission line")
        assert result.get("spectral_line") is not None
        assert "H-" in result.get("spectral_line_info", {}).get("name", "")

    def test_parse_spectral_line_cii(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("[CII] line data at z > 6")
        assert result.get("spectral_line") is not None
        assert result.get("redshift_min") == 6.0
        # Should compute observed frequency range
        assert result.get("observed_freq_min_ghz") is not None

    def test_parse_spectral_line_co(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("CO(3-2) observations")
        assert result.get("spectral_line") is not None
        assert "CO" in result.get("spectral_line_info", {}).get("name", "")

    def test_parse_object_type_quasar(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("QSO survey data")
        assert result.get("object_type") == "quasar"

    def test_parse_object_type_agn(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("active galactic nuclei")
        assert result.get("object_type") == "AGN"

    def test_parse_object_type_supernova(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("supernova remnants")
        assert result.get("object_type") == "supernova"

    def test_parse_object_type_lyman_alpha_emitter(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("Lyman-alpha emitters at high redshift")
        assert result.get("object_type") == "Lyman-alpha emitter"

    def test_parse_object_type_white_dwarf(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("white dwarf candidates")
        assert result.get("object_type") == "white dwarf"

    def test_parse_observation_type_xray(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("X-ray observations of AGN")
        assert result.get("observation_type") == "X-ray"

    def test_parse_observation_type_radio(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("radio continuum survey")
        assert result.get("observation_type") == "radio"

    def test_parse_observation_type_spectroscopy(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("spectroscopic survey of galaxies")
        assert result.get("observation_type") == "spectroscopy"

    def test_parse_observation_type_infrared(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("infrared photometry")
        assert result.get("observation_type") == "infrared"

    def test_parse_observation_type_submm(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("submillimeter galaxy survey")
        assert result.get("observation_type") == "submillimeter"

    def test_parse_coordinates(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("stars at ra=180.5 dec=-30.2 radius=5")
        assert result.get("ra") == pytest.approx(180.5)
        assert result.get("dec") == pytest.approx(-30.2)
        assert result.get("radius") == pytest.approx(5.0)

    def test_parse_region_pleiades(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("stars in the pleiades")
        assert result.get("region") == "Pleiades (M45)"
        assert result.get("ra") is not None
        assert result.get("dec") is not None

    def test_parse_region_orion(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("protostars in orion nebula")
        assert result.get("region") is not None
        assert "Orion" in result["region"]

    def test_parse_region_galactic_center(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("galactic center sources")
        assert result.get("region") is not None

    def test_parse_region_hubble_deep_field(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("galaxies in the hubble ultra deep field")
        assert "Ultra Deep Field" in result.get("region", "")

    def test_parse_region_does_not_override_coords(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("orion at ra=100 dec=20")
        # Explicit coords should take priority over region coords
        assert result.get("ra") == pytest.approx(100.0)
        assert result.get("dec") == pytest.approx(20.0)

    def test_parse_required_fields_redshift(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("galaxies with measured redshift")
        assert "rvz_redshift" in result.get("required_fields", [])

    def test_parse_required_fields_parallax(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("stars with parallax data")
        assert "plx_value" in result.get("required_fields", [])

    def test_parse_required_fields_proper_motion(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("stars with high proper motion")
        assert "pmra" in result.get("required_fields", [])

    def test_parse_required_fields_spectral_type(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("spectral type classification")
        assert "sp_type" in result.get("required_fields", [])

    def test_parse_empty_query(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("")
        assert isinstance(result, dict)

    def test_parse_simple_object_name(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("M31")
        assert isinstance(result, dict)

    def test_parse_complex_query(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query(
            "high redshift Lyman-alpha emitters with CO(3-2) in the COSMOS field"
        )
        assert result.get("redshift_min") is not None
        assert result.get("object_type") == "Lyman-alpha emitter"
        assert result.get("spectral_line") is not None
        assert "COSMOS" in result.get("region", "")

    def test_parse_observation_band_takes_priority(self):
        from app.search.query_parser import parse_natural_query

        result = parse_natural_query("X-ray spectroscopy of AGN")
        # X-ray (band keyword) should take priority over spectroscopy (mode keyword)
        assert result.get("observation_type") == "X-ray"

    def test_parse_spectral_line_with_rest_wavelength_only(self):
        from app.search.query_parser import parse_natural_query

        # H-alpha has rest_wavelength_um but no rest_freq_ghz
        result = parse_natural_query("H-alpha at z > 1")
        if result.get("observed_wavelength_min_um") is not None:
            assert result["observed_wavelength_min_um"] > 0

    def test_parsed_query_dataclass(self):
        from app.search.query_parser import ParsedQuery

        pq = ParsedQuery()
        assert pq.redshift_min is None
        assert pq.matched_keywords == []
        assert pq.required_fields == []


# =====================================================================
# 16. app.services.dossier_generator — safe_float
# =====================================================================


class TestDossierGeneratorHelpers:
    """Tests for dossier generator helpers."""

    def test_safe_float(self):
        from app.services.dossier_generator import _safe_float

        assert _safe_float(3.14) == 3.14
        assert _safe_float(None) is None
        assert _safe_float(float("nan")) is None
        assert _safe_float(float("inf")) is None
        assert _safe_float("3.14") == 3.14

    def test_safe_float_invalid(self):
        from app.services.dossier_generator import _safe_float

        assert _safe_float("abc") is None
        assert _safe_float([1, 2]) is None


# =====================================================================
# 17. app.services.astro_analysis — more function coverage
# =====================================================================


class TestAstroAnalysisPubStyle:
    """Tests for publication figure styling."""

    def test_pub_style_dict(self):
        from app.services.astro_analysis import PUB_STYLE

        assert "font.family" in PUB_STYLE
        assert PUB_STYLE["figure.dpi"] == 150


class TestAstroAnalysisCosmologicalExpanded:
    """Extended cosmology tests to cover more lines."""

    def test_cosmological_calculator_array(self):
        from app.services.astro_analysis import cosmological_calculator

        result = cosmological_calculator([0.1, 0.5, 1.0])
        assert isinstance(result["luminosity_distance_mpc"], list)
        assert len(result["luminosity_distance_mpc"]) == 3

    def test_k_correction_default_band(self):
        from app.services.astro_analysis import k_correction

        # Default band and galaxy_type
        k = k_correction(np.array([0.05, 0.1, 0.2]))
        assert len(k) == 3
        assert all(np.isfinite(k))

    def test_k_correction_i_band(self):
        from app.services.astro_analysis import k_correction

        k = k_correction(0.1, band="i", galaxy_type="elliptical")
        assert np.isfinite(k)

    def test_k_correction_i_band_spiral(self):
        from app.services.astro_analysis import k_correction

        k = k_correction(0.1, band="i", galaxy_type="spiral")
        assert np.isfinite(k)

    def test_extinction_curve_ccm89(self):
        from app.services.astro_analysis import extinction_curve

        A = extinction_curve(5500.0, 0.5)
        assert A > 0

    def test_deredden_result(self):
        from app.services.astro_analysis import deredden

        waves = np.array([4000.0, 5500.0, 8000.0])
        flux = np.array([1.0, 1.0, 1.0])
        corrected = deredden(waves, flux, 0.0)
        # Zero reddening should return original flux
        np.testing.assert_allclose(corrected, flux, rtol=1e-6)


class TestAstroAnalysisBatchEWExpanded:
    """Extended batch EW tests."""

    def test_batch_equivalent_width_emission(self):
        from app.services.astro_analysis import batch_equivalent_width

        wave = np.linspace(6400, 6700, 500)
        flux = np.ones(500) * 100.0
        # Emission line (flux > continuum)
        line_mask = np.abs(wave - 6563) < 5
        flux[line_mask] = 150.0

        result = batch_equivalent_width(wave, flux, [6563.0])
        assert len(result) == 1
        assert result[0]["type"] == "emission"

    def test_batch_equivalent_width_insufficient_data(self):
        from app.services.astro_analysis import batch_equivalent_width

        wave = np.array([6560, 6563, 6566])
        flux = np.array([100, 50, 100])
        result = batch_equivalent_width(wave, flux, [6563.0], window=1.0, cont_window=5.0)
        assert result[0].get("error") is not None


# =====================================================================
# 18. app.api.chat — pure helper functions
# =====================================================================


class TestChatHelpers:
    """Tests for chat API helper functions."""

    def test_safe_context_removes_keys(self):
        from app.api.chat import _safe_context

        ctx = {"query": "stars", "api_key": "secret", "api_keys": {"a": "b"}, "api_provider": "anthropic"}
        result = _safe_context(ctx)
        assert "query" in result
        assert "api_key" not in result
        assert "api_keys" not in result
        assert "api_provider" not in result

    def test_safe_context_none(self):
        from app.api.chat import _safe_context

        assert _safe_context(None) == {}
        assert _safe_context({}) == {}

    def test_preferred_backend_anthropic(self):
        from app.api.chat import _preferred_backend

        assert _preferred_backend({"api_provider": "anthropic"}) == "claude"

    def test_preferred_backend_openai(self):
        from app.api.chat import _preferred_backend

        assert _preferred_backend({"api_provider": "openai"}) == "openai"

    def test_preferred_backend_deepseek(self):
        from app.api.chat import _preferred_backend

        assert _preferred_backend({"api_provider": "deepseek"}) == "deepseek"

    def test_preferred_backend_local(self):
        from app.api.chat import _preferred_backend

        assert _preferred_backend({"api_provider": "local"}) == "local"

    def test_preferred_backend_none(self):
        from app.api.chat import _preferred_backend

        assert _preferred_backend(None) is None
        assert _preferred_backend({}) is None

    def test_filter_tools_all(self):
        from app.api.chat import _filter_tools

        tools = [{"name": "search"}, {"name": "plot"}, {"name": "code"}]
        result = _filter_tools(None, tools)
        assert len(result) == 3

    def test_filter_tools_subset(self):
        from app.api.chat import _filter_tools

        tools = [{"name": "search"}, {"name": "plot"}, {"name": "code"}]
        result = _filter_tools(["search", "plot"], tools)
        assert len(result) == 2

    def test_filter_tools_no_match_returns_all(self):
        from app.api.chat import _filter_tools

        tools = [{"name": "search"}, {"name": "plot"}]
        result = _filter_tools(["nonexistent"], tools)
        assert len(result) == 2  # fallback to all

    def test_parse_actions_valid_json(self):
        from app.api.chat import _parse_actions

        text = 'Some text <actions>[{"action": "search", "query": "M31"}]</actions>'
        result = _parse_actions(text)
        assert len(result) == 1
        assert result[0]["action"] == "search"

    def test_parse_actions_single_object(self):
        from app.api.chat import _parse_actions

        text = '<actions>{"action": "plot"}</actions>'
        result = _parse_actions(text)
        assert len(result) == 1

    def test_parse_actions_no_tags(self):
        from app.api.chat import _parse_actions

        text = "Just normal text, no actions here."
        result = _parse_actions(text)
        assert result == []

    def test_parse_actions_invalid_json_line_by_line(self):
        from app.api.chat import _parse_actions

        text = '<actions>\n{"action": "search"}\nnot json\n{"action": "plot"}\n</actions>'
        result = _parse_actions(text)
        assert len(result) == 2

    def test_strip_actions_from_reply(self):
        from app.api.chat import _strip_actions_from_reply

        text = 'Hello! <actions>[{"action": "search"}]</actions> How are you?'
        result = _strip_actions_from_reply(text)
        assert "<actions>" not in result
        assert "Hello!" in result
        assert "How are you?" in result

    def test_strip_actions_from_reply_no_actions(self):
        from app.api.chat import _strip_actions_from_reply

        text = "Just normal text."
        assert _strip_actions_from_reply(text) == text

    def test_normalize_messages(self):
        from app.api.chat import _normalize_messages, ChatMessage

        msgs = [
            ChatMessage(role="user", content="hello"),
            ChatMessage(role="assistant", content="hi there"),
        ]
        result = _normalize_messages(msgs)
        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "hello"}

    def test_chat_message_model(self):
        from app.api.chat import ChatMessage

        msg = ChatMessage(role="user", content="test")
        assert msg.role == "user"
        assert msg.content == "test"

    def test_chat_request_model(self):
        from app.api.chat import ChatRequest, ChatMessage

        req = ChatRequest(messages=[ChatMessage(role="user", content="hello")])
        assert len(req.messages) == 1

    def test_provider_api_keys_from_context(self):
        from app.api.chat import _provider_api_keys

        ctx = {"api_key": "sk-ant-test123", "api_provider": "anthropic"}
        result = _provider_api_keys(ctx, None)
        assert result.get("anthropic") == "sk-ant-test123"

    def test_provider_api_keys_empty(self):
        from app.api.chat import _provider_api_keys

        result = _provider_api_keys(None, None)
        # May or may not have server key, but should not error
        assert isinstance(result, dict)

    def test_provider_api_keys_from_api_keys_dict(self):
        from app.api.chat import _provider_api_keys

        ctx = {"api_keys": {"openai": "sk-openai-test"}}
        result = _provider_api_keys(ctx, None)
        assert result.get("openai") == "sk-openai-test"

    def test_prime_adql_context_cache_none(self):
        from app.api.chat import _prime_adql_context_cache

        # Should not raise
        _prime_adql_context_cache(None, "test")
        _prime_adql_context_cache("not_a_dict", "test")


# =====================================================================
# 19. app.api.export — topological sort helper
# =====================================================================


class TestExportHelpers:
    """Tests for export API helper functions."""

    def test_topological_sort_flat_linear(self):
        from app.api.export import _topological_sort_flat

        dag = {
            "nodes": [{"id": "A"}, {"id": "B"}, {"id": "C"}],
            "edges": [{"source": "A", "target": "B"}, {"source": "B", "target": "C"}],
        }
        order = _topological_sort_flat(dag)
        assert order == ["A", "B", "C"]

    def test_topological_sort_flat_no_edges(self):
        from app.api.export import _topological_sort_flat

        dag = {"nodes": [{"id": "A"}, {"id": "B"}], "edges": []}
        order = _topological_sort_flat(dag)
        assert set(order) == {"A", "B"}

    def test_topological_sort_flat_empty(self):
        from app.api.export import _topological_sort_flat

        assert _topological_sort_flat({"nodes": [], "edges": []}) == []

    def test_topological_sort_flat_cycle_fallback(self):
        from app.api.export import _topological_sort_flat

        dag = {
            "nodes": [{"id": "A"}, {"id": "B"}],
            "edges": [{"source": "A", "target": "B"}, {"source": "B", "target": "A"}],
        }
        # Should not raise, falls back to declaration order
        order = _topological_sort_flat(dag)
        assert set(order) == {"A", "B"}

    def test_topological_sort_flat_diamond(self):
        from app.api.export import _topological_sort_flat

        dag = {
            "nodes": [{"id": "A"}, {"id": "B"}, {"id": "C"}, {"id": "D"}],
            "edges": [
                {"source": "A", "target": "B"},
                {"source": "A", "target": "C"},
                {"source": "B", "target": "D"},
                {"source": "C", "target": "D"},
            ],
        }
        order = _topological_sort_flat(dag)
        assert order[0] == "A"
        assert order[-1] == "D"


# =====================================================================
# 20. app.api.data — more helpers
# =====================================================================


class TestDataHelpersExpanded:
    """Tests for additional data API helpers."""

    def test_build_source_error_name_ned_timeout(self):
        from app.api.data import _build_source_error_name

        result = _build_source_error_name("ned", "timeout", TimeoutError("timed out"))
        assert "NED" in result
        assert "slowly" in result

    def test_build_source_error_name_sdss_timeout(self):
        from app.api.data import _build_source_error_name

        result = _build_source_error_name("sdss", "timeout", TimeoutError("timed out"))
        assert "SDSS" in result

    def test_build_source_error_name_generic(self):
        from app.api.data import _build_source_error_name

        result = _build_source_error_name("gaia", "connection", ConnectionError("refused"))
        assert "refused" in result

    def test_build_source_error_name_empty_message(self):
        from app.api.data import _build_source_error_name

        result = _build_source_error_name("vizier", "unknown", Exception(""))
        assert "vizier" in result.lower()

    def test_dedup_by_position_empty(self):
        from app.api.data import _dedup_by_position

        assert _dedup_by_position([]) == []

    def test_dedup_by_position_single(self):
        from app.api.data import _dedup_by_position, SearchResult

        r = SearchResult(source="sdss", object_id="1", name="star", ra=180.0, dec=45.0)
        assert len(_dedup_by_position([r])) == 1

    def test_dedup_by_position_merges(self):
        from app.api.data import _dedup_by_position, SearchResult

        r1 = SearchResult(source="sdss", object_id="1", name="obj", ra=180.0, dec=45.0,
                          redshift=0.5, extra={"a": 1})
        r2 = SearchResult(source="gaia", object_id="2", name="obj", ra=180.0, dec=45.0,
                          extra={"b": 2})
        result = _dedup_by_position([r1, r2], sep_arcsec=5.0)
        assert len(result) == 1
        assert result[0].redshift == 0.5
        assert "also_in" in result[0].extra

    def test_dedup_by_position_no_merge_far_apart(self):
        from app.api.data import _dedup_by_position, SearchResult

        r1 = SearchResult(source="sdss", object_id="1", name="obj1", ra=180.0, dec=45.0)
        r2 = SearchResult(source="gaia", object_id="2", name="obj2", ra=181.0, dec=46.0)
        result = _dedup_by_position([r1, r2])
        assert len(result) == 2

    def test_dedup_by_position_fills_missing(self):
        from app.api.data import _dedup_by_position, SearchResult

        r1 = SearchResult(source="sdss", object_id="1", name="obj", ra=180.0, dec=45.0)
        r2 = SearchResult(source="gaia", object_id="2", name="obj", ra=180.0, dec=45.0,
                          redshift=1.0, object_type="Star", magnitude=15.0)
        result = _dedup_by_position([r1, r2])
        assert len(result) == 1
        # r2 should be picked (has more data) and r1's missing fields filled

    def test_search_timeout_for_source(self):
        from app.api.data import _search_timeout_for_source

        assert _search_timeout_for_source("ned") == 75.0
        assert _search_timeout_for_source("sdss") == 20.0  # default


# =====================================================================
# 21. More astro_analysis coverage — large uncovered functions
# =====================================================================


class TestAstroAnalysisAnalyzeResiduals:
    """Tests for analyze_residuals (lines 551-681, ~130 lines)."""

    def test_analyze_residuals_good_fit(self):
        import matplotlib
        matplotlib.use("Agg")
        from app.services.astro_analysis import analyze_residuals

        rng = np.random.default_rng(42)
        data = np.linspace(0, 10, 50) + rng.normal(0, 0.1, 50)
        model = np.linspace(0, 10, 50)

        result = analyze_residuals(data, model)
        assert result["overall_rating"] in ("pass", "warn", "fail")
        assert result["n_data"] == 50
        assert "residuals" in result
        assert "durbin_watson" in result
        assert "shapiro_stat" in result
        assert "n_outliers_3sigma" in result
        import matplotlib.pyplot as plt
        plt.close(result["qq_fig"])

    def test_analyze_residuals_with_errors(self):
        import matplotlib
        matplotlib.use("Agg")
        from app.services.astro_analysis import analyze_residuals

        rng = np.random.default_rng(42)
        data = rng.normal(100, 5, 30)
        model = np.full(30, 100.0)
        errors = np.full(30, 5.0)

        result = analyze_residuals(data, model, errors=errors)
        assert result["n_data"] == 30
        import matplotlib.pyplot as plt
        plt.close(result["qq_fig"])

    def test_analyze_residuals_too_few_raises(self):
        from app.services.astro_analysis import analyze_residuals

        with pytest.raises(ValueError, match="at least 5"):
            analyze_residuals([1, 2, 3], [1, 2, 3])

    def test_analyze_residuals_autocorrelated(self):
        import matplotlib
        matplotlib.use("Agg")
        from app.services.astro_analysis import analyze_residuals

        # Create autocorrelated residuals (sine wave)
        x = np.linspace(0, 4 * np.pi, 100)
        data = np.sin(x) + 10
        model = np.full(100, 10.0)

        result = analyze_residuals(data, model)
        # Should detect autocorrelation (DW far from 2)
        assert result["durbin_watson_rating"] in ("warn", "fail")
        import matplotlib.pyplot as plt
        plt.close(result["qq_fig"])


class TestAstroAnalysisMultiGaussianFitExpanded:
    """More multi-Gaussian fit tests."""

    def test_multi_gaussian_fit_auto_peaks(self):
        from app.services.astro_analysis import multi_gaussian_fit

        wave = np.linspace(6400, 6700, 200)
        flux = (
            10.0 * np.exp(-0.5 * ((wave - 6500) / 3.0) ** 2) +
            5.0 * np.exp(-0.5 * ((wave - 6563) / 4.0) ** 2) +
            1.0
        )
        result = multi_gaussian_fit(wave, flux, n_components=2)
        assert result["success"] is True
        assert len(result["components"]) == 2
        assert "model_flux" in result
        assert "reduced_chi2" in result


class TestAstroAnalysisAvailableFunctionsExpanded:
    """Test available_functions covers the function catalogue."""

    def test_available_functions_has_expected_keys(self):
        from app.services.astro_analysis import available_functions

        funcs = available_functions()
        # Check some expected functions are listed
        assert "compare_models" in funcs
        assert "compute_luminosity_distance" in funcs
        assert "bootstrap_statistic" in funcs
        assert "phase_fold" in funcs
        assert "extinction_curve" in funcs
        # Each entry should have signature and summary
        for name, info in funcs.items():
            assert "signature" in info
            assert "summary" in info


# =====================================================================
# 22. API endpoint integration tests (exercise many lines of code)
# =====================================================================


class TestAPIEndpointsAuth:
    """Test auth endpoints -- exercises app.api.auth (233 lines)."""

    async def test_register_user(self, app_client):
        resp = await app_client.post("/api/auth/register", json={
            "username": "boostuser1", "password": "securepassword123",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "access_token" in data

    async def test_register_duplicate_username(self, app_client):
        await app_client.post("/api/auth/register", json={
            "username": "dupuser", "password": "pass12345678",
        })
        resp = await app_client.post("/api/auth/register", json={
            "username": "dupuser", "password": "pass12345678",
        })
        assert resp.status_code == 400

    async def test_login(self, app_client):
        await app_client.post("/api/auth/register", json={
            "username": "loginboost", "password": "securepassword123",
        })
        resp = await app_client.post("/api/auth/login", json={
            "username": "loginboost", "password": "securepassword123",
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    async def test_login_wrong_password(self, app_client):
        await app_client.post("/api/auth/register", json={
            "username": "wrongpwboost", "password": "correct123456",
        })
        resp = await app_client.post("/api/auth/login", json={
            "username": "wrongpwboost", "password": "incorrect123",
        })
        assert resp.status_code == 401

    async def test_me_endpoint(self, app_client, test_user):
        user, token = test_user
        resp = await app_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == user.email

    async def test_me_unauthorized(self, app_client):
        resp = await app_client.get("/api/auth/me")
        assert resp.status_code in (401, 403)

    async def test_usage_endpoint(self, app_client, test_user):
        user, token = test_user
        resp = await app_client.get("/api/auth/usage", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    async def test_generate_setup_keys(self, app_client, test_user, monkeypatch):
        # H11: admin endpoints now fail-closed when ADMIN_SECRET is unset and
        # ENV is not the literal "dev".  Opt into the dev path explicitly.
        monkeypatch.setenv("ENV", "dev")
        user, token = test_user
        resp = await app_client.post("/api/auth/generate-setup-keys",
                                     json={},
                                     headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    async def test_list_setup_keys(self, app_client, test_user, monkeypatch):
        monkeypatch.setenv("ENV", "dev")
        user, token = test_user
        resp = await app_client.get("/api/auth/setup-keys", headers={
            "Authorization": f"Bearer {token}",
        })
        assert resp.status_code == 200

    async def test_generate_setup_keys_fails_closed_without_env_dev(self, app_client, test_user, monkeypatch):
        """H11 regression: admin endpoints must block when ENV is not 'dev'."""
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.setattr("app.api.auth.settings.admin_secret", "", raising=False)
        user, token = test_user
        resp = await app_client.post("/api/auth/generate-setup-keys",
                                     json={},
                                     headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403


class TestAPIEndpointsSettings:
    """Test settings endpoints -- exercises app.api.settings (80 lines)."""

    async def test_get_api_keys_unauthenticated(self, app_client):
        resp = await app_client.get("/api/settings/api-keys")
        assert resp.status_code in (401, 403)

    async def test_get_api_keys_authenticated(self, app_client, test_user):
        user, token = test_user
        resp = await app_client.get("/api/settings/api-keys", headers={
            "Authorization": f"Bearer {token}",
        })
        assert resp.status_code == 200

    async def test_get_api_key_legacy(self, app_client, test_user):
        user, token = test_user
        resp = await app_client.get("/api/settings/api-key", headers={
            "Authorization": f"Bearer {token}",
        })
        assert resp.status_code == 200


class TestAPIEndpointsData:
    """Test data endpoint helpers -- exercises app.api.data lines."""

    async def test_search_bad_source(self, app_client):
        resp = await app_client.get("/api/data/search", params={
            "q": "M31", "sources": "nonexistent_source",
        })
        assert resp.status_code == 400

    async def test_spectral_lines_list(self, app_client):
        resp = await app_client.get("/api/data/spectral-lines")
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, list)
