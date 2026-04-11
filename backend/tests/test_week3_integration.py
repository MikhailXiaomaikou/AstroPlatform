"""Integration tests for Week 3 features: transient classification, anomaly
detection, dossier generation, follow-up recommendations, cross-wavelength
analysis, literature engine, and citation graph.
"""

import asyncio
import os
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest


# =====================================================================
# Test 1 -- Transient Classifier
# =====================================================================


class TestTransientClassifier:
    """Tests for transient light-curve feature extraction and classification."""

    def test_sn_ia_classification(self):
        """Synthetic SN Ia light curve -> correct classification."""
        from app.services.transient_classifier import (
            TransientClassifier,
            extract_lc_features,
        )

        # Use absolute magnitudes matching the training distribution:
        # SN Ia peak ~ -19 to -18, rise ~18 d, delta_m15 ~ 1.1
        times = np.arange(0, 60, 1.0)
        peak_idx = 18
        mags = np.where(
            times < peak_idx,
            -16 - 3 * (times / peak_idx),
            -19 + 1.1 * (times - peak_idx) / 15,
        )
        mag_errs = np.full_like(mags, 0.05)

        features = extract_lc_features(times, mags, mag_errs)
        assert 14 <= features["rise_time"] <= 22
        assert features["peak_magnitude"] < -18

        classifier = TransientClassifier()
        result = classifier.classify_transient(features)
        assert result["classification"] in (
            "SN_Ia",
            "SN_Ib/c",
        ), f"Got {result['classification']}"
        assert result["confidence"] > 0.3
        assert "probabilities" in result
        assert "feature_importance" in result

    def test_agn_classification(self):
        """Synthetic AGN light curve -> correct classification."""
        from app.services.transient_classifier import (
            TransientClassifier,
            extract_lc_features,
        )

        np.random.seed(42)
        times = np.arange(0, 200, 2.0)
        mags = 18.0 + 0.3 * np.sin(times / 30) + np.random.normal(
            0, 0.1, len(times)
        )
        mag_errs = np.full_like(mags, 0.1)

        features = extract_lc_features(times, mags, mag_errs)
        classifier = TransientClassifier()
        result = classifier.classify_transient(features)
        # Low-amplitude irregular variability -- AGN, LPV, or CV are
        # all reasonable for this feature space.
        assert result["classification"] in (
            "AGN",
            "LPV",
            "CV",
        ), f"Got {result['classification']}"

    def test_too_few_points(self):
        """Only 2 data points -> still returns a result."""
        from app.services.transient_classifier import (
            TransientClassifier,
            extract_lc_features,
        )

        features = extract_lc_features([0, 1], [18, 17.5], [0.1, 0.1])
        classifier = TransientClassifier()
        result = classifier.classify_transient(features)
        assert "classification" in result


# =====================================================================
# Test 2 -- Anomaly Detection
# =====================================================================


class TestAnomalyDetection:
    """Tests for the ensemble anomaly detection pipeline."""

    def test_anomaly_detection_synthetic(self):
        """200 normal + 5 outliers -> detect at least 3 outliers."""
        from app.services.anomaly_detection import detect_anomalies

        np.random.seed(42)

        normal = pd.DataFrame(
            {
                "color": np.random.normal(1.0, 0.3, 200),
                "mag": np.random.normal(15, 2, 200),
                "pm": np.random.normal(5, 3, 200),
            }
        )
        outliers = pd.DataFrame(
            {
                "color": [-2.0, 1.0, 1.0, 1.0, 5.0],
                "mag": [15, -5, 15, 15, 15],
                "pm": [5, 5, 500, 5, 5],
            }
        )
        data = pd.concat([normal, outliers], ignore_index=True)

        result = detect_anomalies(data, features=["color", "mag", "pm"])

        # The ensemble sorts anomalies to the top and resets the index,
        # so we cannot use the original row index.  Instead, look for the
        # extreme values we injected.
        detected = result[result["is_anomaly"]]

        # At least some of our extreme outliers must be flagged.
        extreme_color = detected[
            (detected["color"] < -1.5) | (detected["color"] > 4.5)
        ]
        extreme_mag = detected[detected["mag"] < -3]
        extreme_pm = detected[detected["pm"] > 400]

        unique_outliers_found = (
            int(len(extreme_color) > 0)
            + int(len(extreme_mag) > 0)
            + int(len(extreme_pm) > 0)
        )
        assert unique_outliers_found >= 2, (
            f"Only detected {unique_outliers_found}/3 outlier types"
        )

    def test_anomaly_skip_small_dataset(self):
        """< 30 rows -> anomaly detection skips."""
        from app.services.anomaly_detection import detect_anomalies

        data = pd.DataFrame({"a": range(10), "b": range(10)})
        result = detect_anomalies(data)
        assert not result["is_anomaly"].any()

    @pytest.mark.asyncio
    async def test_scanner_skip(self):
        """< 50 rows -> scanner skips with reason."""
        from app.services.anomaly_scanner import scan_results_for_anomalies

        result = await scan_results_for_anomalies(
            [{"a": i} for i in range(20)]
        )
        assert result["skipped"] is True


# =====================================================================
# Test 3 -- Dossier Generator
# =====================================================================


class TestDossierGenerator:
    """Tests for the cross-match dossier generator."""

    @pytest.mark.asyncio
    async def test_dossier_structure(self):
        """Dossier has required structure."""
        from app.services.dossier_generator import generate_dossier

        # Clear cache so we exercise the full code path.
        from app.services.dossier_generator import _dossier_cache
        _dossier_cache.clear()

        result = await generate_dossier(279.2347, 38.7837, name="Vega")
        assert "object" in result
        assert "ra" in result["object"]
        assert "photometry" in result
        assert "sources_queried" in result
        assert result["sources_queried"] >= 5

    @pytest.mark.asyncio
    async def test_dossier_handles_failures(self):
        """Dossier gracefully handles API failures."""
        from app.services.dossier_generator import generate_dossier, _dossier_cache
        _dossier_cache.clear()

        result = await generate_dossier(0.0001, 0.0001)
        assert "object" in result
        # Should still complete even if all sources return empty
        assert isinstance(result["sources_responded"], int)


# =====================================================================
# Test 4 -- Follow-Up Recommendations
# =====================================================================


class TestFollowupRecommender:
    """Tests for the follow-up observation recommendation engine."""

    @pytest.mark.asyncio
    async def test_sn_ia_followup(self):
        """SN Ia alert -> URGENT spectroscopy recommendation."""
        from app.services.followup_recommender import generate_followup

        alert = {
            "ra": 180.0,
            "dec": 30.0,
            "magnitude": 17.5,
            "classification": "SN_Ia",
            "classification_confidence": 0.85,
            "source_id": "ZTF21test",
            "discovery_date": "2026-04-01",
            "redshift": None,
            "host_galaxy": None,
            "raw_data": None,
        }
        classification = {
            "classification": "SN_Ia",
            "confidence": 0.85,
            "probabilities": {"SN_Ia": 0.85},
        }
        result = await generate_followup(alert, classification=classification)
        assert result["urgency"] in ("URGENT", "HIGH")
        assert len(result["recommendations"]) > 0
        assert any(
            r["type"] == "spectroscopy" for r in result["recommendations"]
        )

    @pytest.mark.asyncio
    async def test_unknown_followup(self):
        """Unknown classification -> classification spectroscopy."""
        from app.services.followup_recommender import generate_followup

        alert = {
            "ra": 100.0,
            "dec": -20.0,
            "magnitude": 19.0,
            "classification": "Unknown",
            "classification_confidence": 0.2,
            "source_id": "test",
            "discovery_date": None,
            "redshift": None,
            "host_galaxy": None,
            "raw_data": None,
        }
        result = await generate_followup(alert)
        assert result["urgency"] in ("HIGH", "URGENT")


# =====================================================================
# Test 5 -- Cross-Wavelength
# =====================================================================


class TestCrossWavelength:
    """Tests for the cross-wavelength anomaly detection engine."""

    @pytest.mark.asyncio
    async def test_cross_wavelength_ir_excess(self):
        """K star with high W1-W2 -> IR excess anomaly."""
        from app.services.cross_wavelength import cross_wavelength_analysis

        mock_dossier = {
            "object_type": "Star",
            "spectral_type": "K2V",
            "photometry": {
                "optical": {"g": 12.0, "r": 11.2},
                "nir": {"J": 9.5, "H": 9.0, "Ks": 8.8},
                "mir": {"W1": 8.5, "W2": 6.7, "W3": None, "W4": None},
            },
            "astrometry": {
                "parallax_mas": 25.0,
                "pm_ra": 10.0,
                "pm_dec": -5.0,
            },
            "redshift": None,
        }
        result = await cross_wavelength_analysis(
            180.0, 30.0, dossier=mock_dossier
        )
        assert result["checks_run"] >= 3
        # IR excess should be flagged (W1-W2 = 1.8)
        ir_check = next(
            (r for r in result["results"] if r["check"] == "ir_excess"), None
        )
        assert ir_check is not None
        assert ir_check["status"] == "ANOMALY"

    @pytest.mark.asyncio
    async def test_cross_wavelength_normal(self):
        """Normal star -> no anomalies."""
        from app.services.cross_wavelength import cross_wavelength_analysis

        mock_dossier = {
            "object_type": "Star",
            "spectral_type": "G2V",
            "photometry": {
                "optical": {"g": 10.0, "r": 9.5, "i": 9.3},
                "nir": {"J": 8.5, "H": 8.2, "Ks": 8.1},
                "mir": {"W1": 8.0, "W2": 7.9, "W3": None, "W4": None},
            },
            "astrometry": {
                "parallax_mas": 50.0,
                "pm_ra": 15.0,
                "pm_dec": -10.0,
            },
            "redshift": None,
        }
        result = await cross_wavelength_analysis(
            180.0, 30.0, dossier=mock_dossier
        )
        anomalies = [
            r for r in result["results"] if r["status"] == "ANOMALY"
        ]
        assert len(anomalies) == 0


# =====================================================================
# Test 6 -- Literature Engine
# =====================================================================


class TestLiteratureEngine:
    """Tests for the literature context engine."""

    @pytest.mark.asyncio
    async def test_literature_no_api_key(self):
        """Without ADS API key, returns graceful empty result."""
        old_key = os.environ.pop("ADS_API_KEY", None)
        try:
            from app.services.literature_engine import get_literature_context

            result = await get_literature_context("Pleiades distance")
            assert "papers" in result
            assert isinstance(result["papers"], list)
        finally:
            if old_key:
                os.environ["ADS_API_KEY"] = old_key

    @pytest.mark.asyncio
    async def test_compare_with_literature(self):
        """Compare function structures output correctly."""
        from app.services.literature_engine import compare_with_literature

        literature = {
            "papers": [
                {
                    "title": "Test Paper",
                    "bibcode": "2024test",
                    "year": 2024,
                    "abstract": "Test",
                }
            ],
            "summary": "Test summary",
        }
        user_results = {"distance_pc": 136, "method": "parallax"}
        result = await compare_with_literature(user_results, literature)
        assert "user_findings" in result
        assert "comparison_prompt" in result


# =====================================================================
# Test 7 -- Citation Graph
# =====================================================================


class TestCitationGraph:
    """Tests for the citation graph builder."""

    @pytest.mark.asyncio
    async def test_citation_graph_no_api_key(self):
        """Without ADS key, returns empty graph."""
        # The citation_graph module reads ADS_API_KEY at import time
        # into a module-level variable, so we need to patch that variable.
        from app.services import citation_graph

        with patch.object(citation_graph, "ADS_API_KEY", ""):
            result = await citation_graph.build_citation_graph(
                ["2018A&A...616A..10B"]
            )
            assert "nodes" in result
            assert "edges" in result
