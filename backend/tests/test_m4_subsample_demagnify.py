"""M4 验收: 1) subsample 显著性检验 2) demagnify_sample 工具."""

import math
from unittest.mock import patch

import numpy as np
import pytest

from app.services.ai_tools import (
    _bootstrap_ols_betas,
    _subsample_significance_from_betas,
    _split_rows_by_redshift,
    _exec_fit_line_lfr,
    _exec_demagnify_sample,
    _resolve_literature_measurement_cache,
    store_search_results,
)


def _make_rows(n: int, beta: float = 1.5, alpha: float = 8.0,
               z_min: float = 4.0, z_max: float = 7.0,
               *, seed: int = 0, source_prefix: str = "SRC"):
    """Synthetic rows on the LFR (logFWHM, log L) plane."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        z = z_min + (z_max - z_min) * i / max(n - 1, 1)
        fwhm = 200.0 + 30.0 * rng.normal()
        x = math.log10(fwhm / 100.0)
        y = alpha + beta * x + 0.1 * rng.normal()
        rows.append({
            "source_name": f"{source_prefix}-{i:03d}",
            "redshift": z, "line_id": "[CII]",
            "log_luminosity": float(y),
            "fwhm_km_s": float(max(20.0, fwhm)),
            "fwhm_err_km_s": None, "log_luminosity_err": None,
            "mu_lens": None, "is_lensed": None,
            "source_cosmology": None,
            "quality_flags": [],
            "citation": {"bibcode": f"2024Test.X{i:03d}"},
            "bibcode": f"2024Test.X{i:03d}",
        })
    return rows


# ── 1. helpers ────────────────────────────────────────────────────────

def test_bootstrap_returns_n_betas():
    x = np.linspace(-1, 1, 30)
    y = 1.0 + 1.5 * x + 0.1 * np.random.default_rng(0).normal(size=30)
    rng = np.random.default_rng(7)
    betas = _bootstrap_ols_betas(x, y, n_boot=500, rng=rng)
    assert betas.size > 400  # NaN should be rare; allow some loss
    # Mean of bootstrap betas should be near the true 1.5
    assert abs(float(np.mean(betas)) - 1.5) < 0.1


def test_subsample_significance_detects_clear_difference():
    """Two manifestly-different bootstrap arrays must produce p<0.01."""
    rng = np.random.default_rng(42)
    beta1 = rng.normal(1.0, 0.05, 2000)  # tight around 1.0
    beta2 = rng.normal(2.0, 0.05, 2000)  # tight around 2.0
    sig = _subsample_significance_from_betas(beta1, beta2)
    assert sig["interpretation"] == "significantly_different"
    assert sig["p_value"] < 0.01
    assert sig["delta_beta"] is not None and sig["delta_beta"] < -0.5


def test_subsample_significance_detects_consistent_samples():
    """Two identically-distributed arrays must produce p>0.32."""
    rng = np.random.default_rng(42)
    beta1 = rng.normal(1.5, 0.1, 2000)
    beta2 = rng.normal(1.5, 0.1, 2000)
    sig = _subsample_significance_from_betas(beta1, beta2)
    assert sig["interpretation"] in {"consistent", "weak_evidence"}


def test_split_rows_by_redshift_inclusive_lower_exclusive_upper():
    rows = [
        {"source_name": "a", "redshift": 0.5},
        {"source_name": "b", "redshift": 1.0},  # boundary
        {"source_name": "c", "redshift": 1.5},
        {"source_name": "d", "redshift": None},  # filtered
    ]
    splits = [
        {"name": "low", "z_max": 1.0},
        {"name": "high", "z_min": 1.0},
    ]
    out = _split_rows_by_redshift(rows, splits)
    assert out[0][0] == "low" and len(out[0][1]) == 1  # only a
    assert out[1][0] == "high" and len(out[1][1]) == 2  # b and c


# ── 2. fit_line_lfr integration ───────────────────────────────────────

def test_fit_line_lfr_subsample_test_marks_clear_split():
    """Two subsamples with manifestly different true β must produce p<0.05.

    N=40 each + tight scatter + wide x range ensures the bootstrap-OLS
    β stderr is small enough to detect a Δβ=1.0 split, mirroring what
    a real LFR sample would look like once it actually carries the
    methodological signal we want this test to assert.
    """
    rows_low = _make_rows(40, beta=1.0, alpha=8.0, z_min=4.0, z_max=4.9,
                          seed=1, source_prefix="LO")
    rows_hi = _make_rows(40, beta=2.0, alpha=8.0, z_min=5.1, z_max=6.5,
                         seed=2, source_prefix="HI")
    # spread x more by spiking some FWHM extremes so both subsamples
    # have a reasonable lever arm for β estimation
    for i, r in enumerate(rows_low + rows_hi):
        r["fwhm_km_s"] = float(80.0 + 8.0 * i)  # 80..720 → wide log range
        # recompute y with the wider x to keep the synthetic relation
        x = math.log10(r["fwhm_km_s"] / 100.0)
        # original beta lives on row's source_prefix; reconstruct it
        beta = 1.0 if r["source_name"].startswith("LO") else 2.0
        rng = np.random.default_rng(hash(r["source_name"]) & 0xFFFFFFFF)
        r["log_luminosity"] = float(8.0 + beta * x + 0.05 * rng.normal())
    rows = rows_low + rows_hi
    with patch(
        "app.services.ai_tools._resolve_literature_measurement_cache",
        return_value=(rows, "latest_literature_tables"),
    ):
        out = _exec_fit_line_lfr({
            "cache_key": "latest_literature_tables",
            "subsample_splits": [
                {"name": "z<5", "z_max": 5.0},
                {"name": "z>=5", "z_min": 5.0},
            ],
            "subsample_n_boot": 1500,
            "seed": 12345,
        })
    test = out["subsample_significance_test"]
    assert test is not None
    assert test["method"] == "bootstrap_ols"
    assert len(test["subsamples"]) == 2
    assert test["subsamples"][0]["n"] == 40
    assert test["subsamples"][1]["n"] == 40
    cmp_pair = test["comparisons"][0]
    assert cmp_pair["interpretation"] in {"significantly_different", "marginal_significance"}
    assert cmp_pair["p_value"] is not None and cmp_pair["p_value"] < 0.05


def test_fit_line_lfr_no_subsample_splits_test_is_none():
    rows = _make_rows(10)
    with patch(
        "app.services.ai_tools._resolve_literature_measurement_cache",
        return_value=(rows, "lit"),
    ):
        out = _exec_fit_line_lfr({})
    assert out["subsample_significance_test"] is None


# ── 3. demagnify_sample tool ──────────────────────────────────────────

def test_demagnify_sample_corrects_log_luminosity_and_writes_new_cache():
    """μ=10 must shift log L' by exactly -1 dex; original cache untouched."""
    rows = _make_rows(5, beta=1.5, source_prefix="DM")
    # Stash directly into cache so _exec_demagnify_sample can read it.
    cache_key = "test_demag_input"
    payload = {
        "schema_version": 2,
        "kind": "literature_tables",
        "cache_key": cache_key,
        "line_measurements": rows,
        "tables": [],
    }
    store_search_results(cache_key, payload)
    mu_map = {
        rows[0]["source_name"]: 10.0,
        rows[2]["source_name"]: {"mu": 100.0, "reference": "Test+24"},
    }
    out = _exec_demagnify_sample({"cache_key": cache_key, "mu_map": mu_map})
    assert out["success"] is True
    assert out["n_demagnified"] == 2
    assert out["n_skipped"] == 3
    assert out["output_cache_key"] == "test_demag_input__demag"
    # Verify shifted log L
    applied = {a["source_name"]: a for a in out["applied"]}
    assert math.isclose(applied[rows[0]["source_name"]]["delta_log_l"], -1.0, abs_tol=1e-9)
    assert math.isclose(applied[rows[2]["source_name"]]["delta_log_l"], -2.0, abs_tol=1e-9)
    assert applied[rows[2]["source_name"]]["reference"] == "Test+24"
    # Original cache untouched
    new_rows, _ = _resolve_literature_measurement_cache(cache_key, None)
    assert new_rows[0]["mu_lens"] is None  # original
    # New cache present
    demag_rows, _ = _resolve_literature_measurement_cache(
        "test_demag_input__demag", None,
    )
    assert demag_rows[0]["is_lensed"] is True
    assert demag_rows[0]["mu_lens"] == 10.0


def test_demagnify_sample_rejects_empty_mu_map():
    out = _exec_demagnify_sample({"cache_key": "anything", "mu_map": {}})
    assert out["success"] is False
    assert out["error_class"] == "missing_mu_map"


def test_demagnify_sample_rejects_invalid_mu():
    cache_key = "test_demag_invalid"
    rows = _make_rows(3)
    payload = {
        "schema_version": 2, "kind": "literature_tables",
        "cache_key": cache_key, "line_measurements": rows, "tables": [],
    }
    store_search_results(cache_key, payload)
    # μ <= 0 must be rejected
    out = _exec_demagnify_sample({
        "cache_key": cache_key,
        "mu_map": {"SRC-001": -1.0, "SRC-002": 0.0},
    })
    assert out["success"] is False
    assert out["error_class"] == "invalid_mu_map"


def test_demagnified_count_propagates_to_fit():
    """fit_line_lfr should count how many rows in the cache are flagged."""
    rows = _make_rows(6)
    rows[0]["_demagnified"] = True
    rows[2]["_demagnified"] = True
    with patch(
        "app.services.ai_tools._resolve_literature_measurement_cache",
        return_value=(rows, "lit"),
    ):
        out = _exec_fit_line_lfr({})
    assert out["lensed_sources_demagnified"] == 2
