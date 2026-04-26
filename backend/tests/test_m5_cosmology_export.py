"""M5 验收: 1) compare_luminosity_distances 工具 2) export_sample_table 工具."""

import math
from unittest.mock import patch

import pytest

from app.services.ai_tools import (
    _exec_compare_luminosity_distances,
    _exec_export_sample_table,
    _cosmology_manifest_for,
    store_search_results,
)


def _sample_rows():
    return [
        {"source_name": "S1", "redshift": 0.5, "line_id": "[CII]",
         "log_luminosity": 9.2, "fwhm_km_s": 250.0,
         "log_luminosity_err": 0.1, "fwhm_err_km_s": 30.0,
         "mu_lens": None, "is_lensed": None, "source_cosmology": None,
         "bibcode": "2024TestA", "arxiv_id": "2404.0001",
         "table_label": "Tab1",
         "citation": {"bibcode": "2024TestA"}},
        {"source_name": "S2", "redshift": 2.0, "line_id": "[CII]",
         "log_luminosity": 9.8, "fwhm_km_s": 350.0,
         "log_luminosity_err": 0.15, "fwhm_err_km_s": 40.0,
         "mu_lens": None, "is_lensed": None, "source_cosmology": None,
         "bibcode": "2024TestB", "arxiv_id": "2404.0002",
         "table_label": "Tab1",
         "citation": {"bibcode": "2024TestB"}},
        {"source_name": "S3", "redshift": 5.5, "line_id": "[CII]",
         "log_luminosity": 10.3, "fwhm_km_s": 400.0,
         "log_luminosity_err": 0.12, "fwhm_err_km_s": 35.0,
         "mu_lens": 4.0, "is_lensed": True, "source_cosmology": None,
         "bibcode": "2024TestC", "arxiv_id": "2404.0003",
         "table_label": "Tab1",
         "citation": {"bibcode": "2024TestC"}},
    ]


# ── compare_luminosity_distances ──────────────────────────────────────

def test_cosmology_manifest_for_supports_named_cosmologies():
    p18 = _cosmology_manifest_for("Planck18")
    assert p18["name"] == "Planck18"
    assert abs(p18["H0_km_s_Mpc"] - 67.4) < 0.5
    wmap9 = _cosmology_manifest_for("WMAP9")
    assert wmap9["name"] == "WMAP9"
    # WMAP9 H0 ~ 69.32
    assert 68 < wmap9["H0_km_s_Mpc"] < 71


def test_cosmology_manifest_for_parses_flat_lambdacdm_spec():
    """FlatLambdaCDM_H73p8_Om0p27 → H0=73.8, Om0=0.27 (Riess+11 / Suzuki+12 example)."""
    m = _cosmology_manifest_for("FlatLambdaCDM_H73p8_Om0p27")
    assert abs(m["H0_km_s_Mpc"] - 73.8) < 0.01
    assert abs(m["Om0"] - 0.27) < 0.001


def test_compare_luminosity_distances_returns_per_source_deltas():
    rows = _sample_rows()
    with patch(
        "app.services.ai_tools._resolve_literature_measurement_cache",
        return_value=(rows, "lit_test"),
    ):
        out = _exec_compare_luminosity_distances({
            "cache_key": "lit_test",
            "target_cosmology": "FlatLambdaCDM_H73p8_Om0p27",
        })
    assert out["success"] is True
    assert out["current_cosmology"]["name"] == "Planck18"
    assert out["target_cosmology"]["name"] == "FlatLambdaCDM_H73p8_Om0p27"
    assert len(out["per_source"]) == 3
    # H0 73.8 vs 67.4 → DL ~10% smaller (DL ∝ c/H0 at low z;
    # roughly 67.4/73.8 - 1 ≈ -8.7%); sign should be negative
    for r in out["per_source"]:
        assert r["delta_pct"] < 0  # higher H0 → smaller DL
        # log L ∝ 2 log DL → Δlog L should be roughly 2 * log10(0.91) ≈ -0.08
        assert -0.15 < r["delta_log_luminosity"] < 0.0
    assert "max_abs_delta_pct" in out["summary"]
    assert "max_abs_delta_log_luminosity" in out["summary"]
    assert "ΔDL" in out["__message_to_model__"]


def test_compare_requires_target_cosmology():
    out = _exec_compare_luminosity_distances({"cache_key": "x"})
    assert out["success"] is False
    assert out["error_class"] == "missing_target_cosmology"


def test_compare_handles_empty_cache():
    with patch(
        "app.services.ai_tools._resolve_literature_measurement_cache",
        return_value=([], "x"),
    ):
        out = _exec_compare_luminosity_distances({
            "target_cosmology": "WMAP9",
        })
    assert out["success"] is False
    assert out["__tool_status__"] == "EMPTY"


def test_compare_skips_rows_with_no_redshift():
    rows = _sample_rows()
    rows.append({
        "source_name": "Sbad", "redshift": None,
        "log_luminosity": 9.0, "fwhm_km_s": 200.0,
    })
    with patch(
        "app.services.ai_tools._resolve_literature_measurement_cache",
        return_value=(rows, "x"),
    ):
        out = _exec_compare_luminosity_distances({
            "target_cosmology": "WMAP9",
        })
    assert out["success"] is True
    assert out["summary"]["n_used"] == 3  # the 3 valid rows


# ── export_sample_table ───────────────────────────────────────────────

def test_export_csv_default():
    cache_key = "test_export_csv"
    payload = {
        "schema_version": 2, "kind": "literature_tables",
        "cache_key": cache_key, "line_measurements": _sample_rows(), "tables": [],
    }
    store_search_results(cache_key, payload)
    out = _exec_export_sample_table({"cache_key": cache_key})
    assert out["success"] is True
    assert out["format"] == "csv"
    assert out["n_rows"] == 3
    assert out["filename"] == f"sample_{cache_key}.csv"
    content = out["content"]
    # Header row + 3 data rows
    assert content.startswith("source_name,")
    assert "S1," in content
    assert "S2," in content
    assert "S3," in content
    # FWHM err present
    assert "30.0" in content


def test_export_latex_format_emits_deluxetable():
    cache_key = "test_export_latex"
    payload = {
        "schema_version": 2, "kind": "literature_tables",
        "cache_key": cache_key, "line_measurements": _sample_rows(), "tables": [],
    }
    store_search_results(cache_key, payload)
    out = _exec_export_sample_table({"cache_key": cache_key, "format": "latex"})
    assert out["success"] is True
    assert out["format"] == "latex"
    assert out["filename"].endswith(".tex")
    assert r"\begin{deluxetable}" in out["content"]
    assert r"\enddata" in out["content"]
    # Each source appears as a row
    assert "S1" in out["content"]
    assert "S3" in out["content"]


def test_export_votable_format_round_trips_via_astropy():
    cache_key = "test_export_votable"
    payload = {
        "schema_version": 2, "kind": "literature_tables",
        "cache_key": cache_key, "line_measurements": _sample_rows(), "tables": [],
    }
    store_search_results(cache_key, payload)
    out = _exec_export_sample_table({"cache_key": cache_key, "format": "votable"})
    assert out["success"] is True
    assert "<VOTABLE" in out["content"]
    assert "S1" in out["content"]


def test_export_invalid_format_returns_failure():
    out = _exec_export_sample_table({"cache_key": "x", "format": "html"})
    assert out["success"] is False
    assert out["error_class"] == "invalid_format"


def test_export_handles_empty_cache():
    with patch(
        "app.services.ai_tools._resolve_literature_measurement_cache",
        return_value=([], "lit"),
    ):
        out = _exec_export_sample_table({"cache_key": "lit"})
    assert out["success"] is False
    assert out["__tool_status__"] == "EMPTY"


def test_export_respects_columns_subset():
    cache_key = "test_export_subset"
    payload = {
        "schema_version": 2, "kind": "literature_tables",
        "cache_key": cache_key, "line_measurements": _sample_rows(), "tables": [],
    }
    store_search_results(cache_key, payload)
    out = _exec_export_sample_table({
        "cache_key": cache_key,
        "columns": ["source_name", "redshift", "log_luminosity"],
    })
    assert out["success"] is True
    content = out["content"]
    first_line = content.splitlines()[0]
    assert first_line == "source_name,redshift,log_luminosity"
    # FWHM column should be absent
    assert "fwhm" not in first_line.lower()
