"""M1 acceptance: literature_tables cache schema v2 + backwards compatibility.

M1 three core contracts:
1. Newly written cache payload must carry schema_version=2 + each line_measurement
   must contain at least the 5 v2-required fields (None values allowed).
2. Rows read from old v1 payload (missing fields) are automatically upgraded to v2 schema
   so old sessions do not raise KeyError.
3. arxiv extractor correctly populates mu_lens + infers is_lensed when a mu column
   is found in the table. All new fields are None when no mu column is present (no fabrication).
"""

from app.services.ai_tools import (
    _V2_MEASUREMENT_KEYS,
    _LITERATURE_SCHEMA_VERSION,
    _literature_table_cache_payload,
    _measurement_rows_from_cache_payload,
    _normalize_measurement_to_v2,
)
from app.api.arxiv import _normalize_line_measurements


def test_schema_version_is_v2():
    assert _LITERATURE_SCHEMA_VERSION == 2


def test_v1_row_upgraded_on_cache_write():
    """Old extract results (without new fields) are automatically upgraded when written to cache."""
    v1_row = {
        "source_name": "SRC-1", "redshift": 5.5, "line_id": "[CII]",
        "log_luminosity": 9.1, "fwhm_km_s": 200.0,
        # Note: no fwhm_err_km_s / log_luminosity_err / mu_lens /
        # is_lensed / source_cosmology — these are v2 new fields
    }
    payload = _literature_table_cache_payload(
        {"line_measurements": [v1_row]},
        cache_key="latest_literature_tables",
    )
    assert payload["schema_version"] == 2
    row = payload["line_measurements"][0]
    for key in _V2_MEASUREMENT_KEYS:
        assert key in row, f"v2 schema missing key {key}"
        assert row[key] is None, f"missing v1 field {key} should default to None"


def test_v2_row_preserved():
    """Rows that are already v2 are preserved as-is and not overwritten."""
    v2_row = {
        "source_name": "SRC-2", "redshift": 6.0, "line_id": "[CII]",
        "log_luminosity": 9.5, "log_luminosity_err": 0.15,
        "fwhm_km_s": 350.0, "fwhm_err_km_s": 40.0,
        "mu_lens": 2.3, "is_lensed": True,
        "source_cosmology": {"name": "Planck18", "H0": 67.4, "Om0": 0.3156},
    }
    payload = _literature_table_cache_payload(
        {"line_measurements": [v2_row]},
        cache_key="lit_x",
    )
    row = payload["line_measurements"][0]
    assert row["log_luminosity_err"] == 0.15
    assert row["fwhm_err_km_s"] == 40.0
    assert row["mu_lens"] == 2.3
    assert row["is_lensed"] is True
    assert row["source_cosmology"]["name"] == "Planck18"


def test_old_cache_read_path_auto_upgrades():
    """M1 contract: _measurement_rows_from_cache_payload gives v2 schema even when reading old v1 cache.

    Simulated scenario: user session has a schema_version=1 payload; fit_line_lfr
    should not raise KeyError when reading it.
    """
    old_v1_cache = {
        "schema_version": 1,
        "kind": "literature_tables",
        "line_measurements": [
            {"source_name": "A", "log_luminosity": 9.0, "fwhm_km_s": 180.0},
            {"source_name": "B", "log_luminosity": 9.4, "fwhm_km_s": 250.0},
        ],
    }
    rows = _measurement_rows_from_cache_payload(old_v1_cache)
    assert len(rows) == 2
    for row in rows:
        for key in _V2_MEASUREMENT_KEYS:
            assert key in row
            assert row[key] is None


def test_bare_list_payload_also_upgraded():
    """_measurement_rows_from_cache_payload supports list input (not just dict)."""
    rows = _measurement_rows_from_cache_payload([
        {"source_name": "X", "log_luminosity": 8.5, "fwhm_km_s": 140.0},
    ])
    assert len(rows) == 1
    for key in _V2_MEASUREMENT_KEYS:
        assert key in rows[0]


def test_normalize_measurement_to_v2_idempotent():
    """Running normalize multiple times does not corrupt existing v2 fields."""
    row = {"source_name": "C", "mu_lens": 5.0, "is_lensed": True}
    once = _normalize_measurement_to_v2(row)
    twice = _normalize_measurement_to_v2(once)
    assert once == twice
    assert twice["mu_lens"] == 5.0


def test_arxiv_extractor_picks_up_mu_column():
    """M1 extractor contract: when table has a mu column, mu_lens is populated and is_lensed inferred correctly."""
    tables = [{
        "columns": ["Source", "z", "log L[CII]", "FWHM", "mu"],
        "rows": [
            ["A100", "5.1", "9.2", "200", "1.0"],   # unlensed (μ=1)
            ["A101", "5.5", "9.6", "300", "4.5"],   # lensed (μ=4.5)
            ["A102", "6.0", "8.8", "150", "15.0"],  # strongly lensed
        ],
        "caption": "[CII] sample",
        "label": "Tab1",
    }]
    ms = _normalize_line_measurements(tables)
    assert len(ms) == 3
    assert ms[0]["mu_lens"] == 1.0
    assert ms[0]["is_lensed"] is False
    assert ms[1]["mu_lens"] == 4.5
    assert ms[1]["is_lensed"] is True
    assert ms[2]["mu_lens"] == 15.0
    assert ms[2]["is_lensed"] is True
    # source_cosmology is paper-level; arxiv.py layer keeps it as None
    for m in ms:
        assert m["source_cosmology"] is None


def test_arxiv_extractor_no_mu_column_leaves_fields_none():
    """When table has no mu column, mu_lens / is_lensed are both None (no fabrication)."""
    tables = [{
        "columns": ["Source", "z", "log L[CII]", "FWHM"],
        "rows": [["S1", "5.0", "9.0", "200"]],
        "caption": "[CII] sample",
        "label": "Tab1",
    }]
    ms = _normalize_line_measurements(tables)
    assert len(ms) == 1
    assert ms[0]["mu_lens"] is None
    assert ms[0]["is_lensed"] is None
    assert ms[0]["source_cosmology"] is None
