"""Scientific claim-boundary regressions for cross-wavelength screening."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_all_skipped_checks_return_empty_nonclaimable_result() -> None:
    from app.services.cross_wavelength import cross_wavelength_analysis
    from app.services.result_provenance import normalize_tool_result

    result = await cross_wavelength_analysis(180.0, 30.0, dossier={})

    assert result["checks_run"] == 0
    assert all(check["status"] == "SKIPPED" for check in result["results"])
    assert result["__tool_status__"] == "EMPTY"
    assert result["analysis_status"] == "empty"
    assert result["publication_ready"] is False
    assert result["__do_not_claim__"] is True
    assert result["anomalies_found"] is None
    assert "No multi-wavelength discrepancies" not in result["briefing"]
    assert "No discrepancy or normality conclusion" in result["briefing"]

    normalized = normalize_tool_result(
        "analyze_cross_wavelength",
        result,
        tool_input={"ra": 180.0, "dec": 30.0},
    )
    assert normalized["analysis_status"] == "empty"
    assert normalized["__do_not_claim__"] is True
    assert normalized["provenance"]["reproducibility"]["query_hash"]


@pytest.mark.asyncio
async def test_missing_photometric_errors_quarantine_assumed_sed_fit() -> None:
    from app.services.cross_wavelength import (
        _check_sed_shape,
        cross_wavelength_analysis,
    )

    dossier = {
        "object_type": "Star",
        "photometry": {
            "optical": {"g": 15.0, "r": 14.5, "i": 14.2, "z": 14.0},
        },
    }

    sed = _check_sed_shape(dossier)
    assert sed["statistics_ready"] is False
    assert sed["publication_ready"] is False
    assert sed["preliminary"] is True
    assert sed["__do_not_claim__"] is True
    assert sed["significance"] is None
    assert sed["T_err"] is None
    assert "chi2" not in str(sed).lower()
    assert sed["uncertainty_provenance"]["source"] == (
        "assumed_10_percent_relative_flux_error_for_screening_only"
    )
    assert set(sed["uncertainty_provenance"]["missing_error_bands"]) == {
        "g",
        "r",
        "i",
        "z",
    }

    result = await cross_wavelength_analysis(180.0, 30.0, dossier=dossier)
    assert result["analysis_status"] == "partial"
    assert result["publication_ready"] is False
    assert result["__do_not_claim__"] is True
    assert "sed_shape" in result["preliminary_checks"]


@pytest.mark.asyncio
async def test_real_band_errors_are_propagated_into_weighted_sed_fit() -> None:
    from app.services.cross_wavelength import (
        _check_sed_shape,
        cross_wavelength_analysis,
    )

    dossier = {
        "object_type": "Star",
        "photometry": {
            "optical": {"g": 15.0, "r": 14.5, "i": 14.2, "z": 14.0},
        },
        "photometry_errors": {
            "optical": {"g": 0.021, "r": 0.018, "i": 0.017, "z": 0.025},
        },
    }

    sed = _check_sed_shape(dossier)
    uncertainty = sed["uncertainty_provenance"]
    assert sed["statistics_ready"] is True
    assert sed.get("__do_not_claim__") is not True
    assert sed["chi2"] >= 0
    assert sed["dof"] == 2
    assert sed["significance"].startswith("chi2/dof")
    assert uncertainty["source"] == (
        "catalog_reported_magnitude_errors_propagated_to_flux"
    )
    assert uncertainty["magnitude_errors_by_band"] == {
        "g": 0.021,
        "r": 0.018,
        "i": 0.017,
        "z": 0.025,
    }
    assert all(
        value > 0
        for value in uncertainty["propagated_flux_errors_jy_by_band"].values()
    )

    result = await cross_wavelength_analysis(180.0, 30.0, dossier=dossier)
    assert result["analysis_status"] == "completed"
    assert result.get("__do_not_claim__") is not True
    assert result["publication_ready"] is False
    assert result["claim_scope"] == "cross_wavelength_screening_only"


def test_ir_excess_uses_reported_wise_errors_for_significance() -> None:
    from app.services.cross_wavelength import _check_ir_excess

    result = _check_ir_excess(
        {
            "object_type": "Star",
            "photometry": {"mir": {"W1": 12.0, "W2": 11.0}},
            "photometry_errors": {"mir": {"W1": 0.03, "W2": 0.04}},
        }
    )

    assert result["status"] == "ANOMALY"
    assert result["statistics_ready"] is True
    assert result.get("__do_not_claim__") is not True
    assert result["significance"] == "14.0 sigma above threshold"
    assert result["uncertainty_provenance"]["bands"] == {
        "W1": 0.03,
        "W2": 0.04,
    }


@pytest.mark.asyncio
async def test_dossier_preserves_catalog_errors_for_every_photometric_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import dossier_generator as dossier_module

    async def fake_simbad(*_args, **_kwargs):
        return {"status": "no_match"}

    async def fake_gaia(*_args, **_kwargs):
        return {
            "status": "ok",
            "parallax_mas": 2.0,
            "parallax_error_mas": 0.2,
        }

    async def fake_sdss(*_args, **_kwargs):
        return {
            "status": "ok",
            "u": 16.0,
            "g": 15.0,
            "r": 14.5,
            "i": 14.2,
            "z": 14.0,
            "u_err": 0.05,
            "g_err": 0.04,
            "r_err": 0.03,
            "i_err": 0.03,
            "z_err": 0.04,
        }

    async def fake_twomass(*_args, **_kwargs):
        return {
            "status": "ok",
            "J": 13.5,
            "H": 13.2,
            "Ks": 13.0,
            "J_err": 0.03,
            "H_err": 0.04,
            "Ks_err": 0.05,
        }

    async def fake_allwise(*_args, **_kwargs):
        return {
            "status": "ok",
            "W1": 12.8,
            "W2": 12.7,
            "W3": 12.5,
            "W4": 12.1,
            "W1_err": 0.03,
            "W2_err": 0.03,
            "W3_err": 0.08,
            "W4_err": 0.15,
        }

    async def fake_empty(*_args, **_kwargs):
        return {"status": "no_match"}

    dossier_module._dossier_cache.clear()
    monkeypatch.setattr(dossier_module, "_query_simbad", fake_simbad)
    monkeypatch.setattr(dossier_module, "_query_gaia", fake_gaia)
    monkeypatch.setattr(dossier_module, "_query_sdss", fake_sdss)
    monkeypatch.setattr(dossier_module, "_query_2mass", fake_twomass)
    monkeypatch.setattr(dossier_module, "_query_allwise", fake_allwise)
    monkeypatch.setattr(dossier_module, "_query_ned", fake_empty)
    monkeypatch.setattr(dossier_module, "_query_tns", fake_empty)

    dossier = await dossier_module.generate_dossier(180.0, 30.0)

    assert dossier["photometry_errors"] == {
        "optical": {"u": 0.05, "g": 0.04, "r": 0.03, "i": 0.03, "z": 0.04},
        "nir": {"J": 0.03, "H": 0.04, "Ks": 0.05},
        "mir": {"W1": 0.03, "W2": 0.03, "W3": 0.08, "W4": 0.15},
    }
    assert dossier["photometry_error_unit"].startswith("mag")
    assert dossier["astrometry"]["parallax_error_mas"] == 0.2
    dossier_module._dossier_cache.clear()


@pytest.mark.asyncio
async def test_ai_tool_wrapper_enforces_empty_boundary() -> None:
    from app.services.ai_tools_dossier import _exec_cross_wavelength

    legacy_all_skipped = {
        "checks_run": 0,
        "anomalies_found": 0,
        "results": [
            {"check": "ir_excess", "status": "SKIPPED"},
            {"check": "sed_shape", "status": "SKIPPED"},
        ],
        "briefing": "No multi-wavelength discrepancies detected.",
    }
    with patch(
        "app.services.cross_wavelength.cross_wavelength_analysis",
        new=AsyncMock(return_value=legacy_all_skipped),
    ):
        result = await _exec_cross_wavelength({"ra": 180.0, "dec": 30.0})

    assert result["__tool_status__"] == "EMPTY"
    assert result["analysis_status"] == "empty"
    assert result["__do_not_claim__"] is True
    assert result["publication_ready"] is False
    assert result["anomalies_found"] is None
    assert "No multi-wavelength discrepancies" not in result["briefing"]
