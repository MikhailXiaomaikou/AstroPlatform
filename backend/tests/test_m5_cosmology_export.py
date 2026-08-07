"""M5 acceptance: 1) compare_luminosity_distances tool 2) export_sample_table tool."""

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
    # PART AA: legacy "Planck18" alias is mapped onto the lowercase preset
    # name so the manifest carries the correct bibcode.
    p18 = _cosmology_manifest_for("Planck18")
    assert p18["name"] == "planck18"
    assert abs(p18["H0_km_s_Mpc"] - 67.4) < 0.5
    wmap9 = _cosmology_manifest_for("WMAP9")
    assert wmap9["name"] == "WMAP9"
    # WMAP9 H0 ~ 69.32
    assert 68 < wmap9["H0_km_s_Mpc"] < 71


def test_cosmology_manifest_for_parses_flat_lambdacdm_spec():
    """FlatLambdaCDM_H73p8_Om0p295 → H0=73.8, Om0=0.295 (Riess+11 / Suzuki+12 example)."""
    m = _cosmology_manifest_for("FlatLambdaCDM_H73p8_Om0p295")
    assert abs(m["H0_km_s_Mpc"] - 73.8) < 0.01
    assert abs(m["Om0"] - 0.295) < 0.001


def test_compare_luminosity_distances_returns_per_source_deltas():
    rows = _sample_rows()
    with patch(
        "app.services.ai_tools._resolve_literature_measurement_cache",
        return_value=(rows, "lit_test"),
    ):
        out = _exec_compare_luminosity_distances({
            "cache_key": "lit_test",
            "target_cosmology": "FlatLambdaCDM_H73p8_Om0p295",
        })
    assert out["success"] is True
    # PART AA: current cosmology resolves to the lowercase preset name.
    assert out["current_cosmology"]["name"] == "planck18"
    assert out["target_cosmology"]["name"] == "FlatLambdaCDM_H73p8_Om0p295"
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


def test_compare_h0_anchors_does_not_require_literature_cache():
    with patch(
        "app.services.ai_tools._resolve_literature_measurement_cache",
        side_effect=AssertionError("anchor-only mode must not read the sample cache"),
    ):
        out = _exec_compare_luminosity_distances({
            "target_cosmology": "riess22_shoes",
            "comparison_mode": "h0_anchors",
        }, python_session_id="fresh-session")

    assert out["success"] is True
    assert out["__tool_status__"] == "PARTIAL"
    assert out["analysis_status"] == "partial"
    assert out["data_origin"] == "cached_real"
    assert out["sample_comparison_performed"] is False
    assert out["current_cosmology"]["H0_km_s_Mpc"] == 67.36
    assert out["target_cosmology"]["H0_km_s_Mpc"] == 73.04
    anchor = out["anchor_comparison"]
    assert 8.0 < anchor["target_minus_baseline_pct"] < 9.0
    assert 4.0 < anchor["naive_independent_gaussian_tension_sigma"] < 6.0
    assert "per-source luminosity distance" in out["limitations"][0]


def test_h0_anchor_comparison_honors_explicit_planck_baseline():
    from app.services.cosmology import cosmology_manifest

    freedman = {
        "name": "freedman21_trgb",
        "H0_km_s_Mpc": 69.8,
        "H0_err": 0.8,
        "bibcode": "2021ApJ...919...16F",
    }

    def configured_manifest(name=None):
        return cosmology_manifest(name) if name else freedman

    with patch(
        "app.services.cosmology.cosmology_manifest",
        side_effect=configured_manifest,
    ):
        out = _exec_compare_luminosity_distances({
            "baseline_cosmology": "planck18",
            "target_cosmology": "riess22_shoes",
            "comparison_mode": "h0_anchors",
        })

    assert out["success"] is True
    assert out["current_cosmology"]["name"] == "planck18"
    assert out["current_cosmology"]["H0_km_s_Mpc"] == 67.36


def test_compare_h0_anchors_rejects_uncited_legacy_target():
    out = _exec_compare_luminosity_distances({
        "target_cosmology": "WMAP9",
        "comparison_mode": "h0_anchors",
    })

    assert out["success"] is False
    assert out["error_class"] == "uncited_cosmology_anchor"
    assert out["__tool_status__"] == "FAILED"


def test_h0_anchor_result_has_tool_grounded_fallback_summary():
    from app.services.agent_runtime.summaries import _cosmology_tool_grounded_summary

    out = _exec_compare_luminosity_distances({
        "target_cosmology": "riess22_shoes",
        "comparison_mode": "h0_anchors",
    })
    summary = _cosmology_tool_grounded_summary([{
        "tool": "compare_luminosity_distances",
        "result": out,
    }])

    assert summary is not None
    assert "67.36" in summary
    assert "73.04" in summary
    assert "2020A&A...641A...6P" in summary
    assert "2022ApJ...934L...7R" in summary
    assert "published H0 anchors only" in summary
    assert "per-source luminosity distance" in summary


def test_h0_anchor_uncertainties_and_rounded_sigma_are_claimable():
    from app.services.claim_validator import validate_claims

    out = _exec_compare_luminosity_distances({
        "target_cosmology": "riess22_shoes",
        "comparison_mode": "h0_anchors",
    })
    tool_results = [{
        "tool": "compare_luminosity_distances",
        "input": {
            "target_cosmology": "riess22_shoes",
            "comparison_mode": "h0_anchors",
        },
        "result": out,
    }]
    reply = (
        "Planck H0 = 67.36 ± 0.54 km s⁻¹ Mpc⁻¹ and SH0ES H0 = "
        "73.04 ± 1.04 km s⁻¹ Mpc⁻¹. The offset is 8.43%, with a "
        "naive independent-Gaussian separation of 4.8σ."
    )

    validation = validate_claims(reply, tool_results)
    assert validation.ok, [
        (claim.label, claim.value, claim.raw) for claim in validation.uncited
    ]


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


# ── PART AA C4: compare_luminosity_distances accepts curated presets ──

@pytest.mark.parametrize(
    "preset, expected_bibcode, expected_h0",
    [
        ("planck18", "2020A&A...641A...6P", 67.36),
        ("planck18_bao", "2020A&A...641A...6P", 67.66),
        ("freedman21_trgb", "2021ApJ...919...16F", 69.8),
        ("riess22_shoes", "2022ApJ...934L...7R", 73.04),
    ],
)
def test_compare_luminosity_distances_accepts_part_aa_preset(
    preset, expected_bibcode, expected_h0
):
    """PART AA C4: each curated preset name resolves to the right H0
    AND a non-null bibcode in the tool's target_cosmology manifest.

    Locks the citation contract — if a future cosmology.py refactor
    drops a preset's bibcode, the validator's universe loses the
    citation anchor and chat replies that quote that preset's H0
    can no longer be verified."""
    rows = _sample_rows()
    with patch(
        "app.services.ai_tools._resolve_literature_measurement_cache",
        return_value=(rows, "lit_test"),
    ):
        out = _exec_compare_luminosity_distances({
            "cache_key": "lit_test",
            "target_cosmology": preset,
        })
    assert out["success"] is True
    target = out["target_cosmology"]
    assert target["name"] == preset
    assert target["bibcode"] == expected_bibcode
    assert abs(target["H0_km_s_Mpc"] - expected_h0) < 0.5
    assert target.get("reference"), f"missing reference for {preset}"


def test_compare_luminosity_distances_planck18_to_riess22_matches_hubble_tension():
    """The cross-preset Δ between planck18 and riess22_shoes should
    be ~7-8% at z>0 — the published Hubble tension number. If this
    regresses, either the preset H0 was silently changed or the
    cross-cosmology math broke."""
    rows = _sample_rows()
    with patch(
        "app.services.ai_tools._resolve_literature_measurement_cache",
        return_value=(rows, "lit_test"),
    ):
        out = _exec_compare_luminosity_distances({
            "cache_key": "lit_test",
            "target_cosmology": "riess22_shoes",
        })
    assert out["success"] is True
    assert 5.0 < out["summary"]["max_abs_delta_pct"] < 12.0
