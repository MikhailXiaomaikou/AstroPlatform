"""PART AD C1 — fit_line_lfr cosmology recompute + variant_label."""

from __future__ import annotations



def _alpine_cache(n: int = 10) -> dict:
    """Build a synthetic ALPINE-shaped cache with n rows."""
    return {
        "kind": "literature_tables",
        "cache_key": "latest_literature_tables",
        "line_measurements": [
            {
                "source_name": f"GAL{i}",
                "redshift": 4.5 + i * 0.05,
                "line_id": "[CII] 158um",
                "log_luminosity": 9.0 + i * 0.05,
                "fwhm_km_s": 200.0 + i * 5,
                "bibcode": "2020A&A...643A...4L",
                "citation": {"bibcode": "2020A&A...643A...4L"},
            }
            for i in range(n)
        ],
        "tables": [],
    }


def _store_alpine(n: int = 10) -> None:
    from app.services.ai_tools import store_search_results

    store_search_results("latest_literature_tables", _alpine_cache(n))


def test_fit_line_lfr_default_cosmology_planck18() -> None:
    from app.services.ai_tools import _exec_fit_line_lfr

    _store_alpine()
    out = _exec_fit_line_lfr({"cache_key": "latest_literature_tables", "line_id": "[CII]"})
    assert out["success"] is True
    assert out["cosmology_used"] == "planck18"
    assert out["cosmology_recomputed"] is False
    assert out["dl_shift_summary"] is None
    assert out["variant_label"] == "main"


def test_fit_line_lfr_riess22_recomputes_dl_and_shifts_log_l() -> None:
    """Riess22 has H0=73.04 vs planck18 H0=67.36 — ~8% lower DL at any z,
    so log_L shifts by 2*log10(DL_riess22/DL_planck18) ~= -0.07 dex.

    The slope is invariant under a uniform multiplicative DL shift
    (additive in log-space) — only the intercept moves.  This locks
    both: dl_shift_summary fires + slope stays close to default."""
    from app.services.ai_tools import _exec_fit_line_lfr

    _store_alpine()
    default_out = _exec_fit_line_lfr({"cache_key": "latest_literature_tables", "line_id": "[CII]"})
    riess_out = _exec_fit_line_lfr({
        "cache_key": "latest_literature_tables",
        "line_id": "[CII]",
        "cosmology": "riess22_shoes",
        "variant_label": "Riess+22 cosmology variant",
    })

    assert riess_out["cosmology_used"] == "riess22_shoes"
    assert riess_out["cosmology_recomputed"] is True
    assert riess_out["variant_label"] == "Riess+22 cosmology variant"

    summary = riess_out["dl_shift_summary"]
    assert summary["target_cosmology"] == "riess22_shoes"
    assert summary["baseline_cosmology"] == "planck18"
    assert summary["n_rows_shifted"] == 10
    # Riess22 → ~-0.07 dex shift (DL_riess22 < DL_planck18 → log_L drops)
    median_shift = summary["median_log_l_shift_dex"]
    assert -0.10 < median_shift < -0.05, (
        f"expected ~-0.07 dex shift, got {median_shift}"
    )
    # Slope invariant under uniform additive log-space shift.
    default_slope = default_out.get("slope") or default_out.get("beta")
    riess_slope = riess_out.get("slope") or riess_out.get("beta")
    assert abs(default_slope - riess_slope) < 1e-3, (
        f"slope should not move under a uniform DL shift "
        f"(planck18={default_slope}, riess22={riess_slope})"
    )


def test_fit_line_lfr_planck18_explicit_does_not_recompute() -> None:
    """Passing cosmology='planck18' explicitly should match the default
    behaviour — no recompute, no DL shift summary, slope unchanged."""
    from app.services.ai_tools import _exec_fit_line_lfr

    _store_alpine()
    default_out = _exec_fit_line_lfr({"cache_key": "latest_literature_tables", "line_id": "[CII]"})
    explicit_out = _exec_fit_line_lfr({
        "cache_key": "latest_literature_tables",
        "line_id": "[CII]",
        "cosmology": "planck18",
    })
    assert explicit_out["cosmology_recomputed"] is False
    assert explicit_out["dl_shift_summary"] is None
    default_slope = default_out.get("slope") or default_out.get("beta")
    explicit_slope = explicit_out.get("slope") or explicit_out.get("beta")
    assert default_slope == explicit_slope


def test_fit_line_lfr_freedman_trgb_smaller_shift_than_riess22() -> None:
    """Freedman21 H0=69.8 sits between Planck18 and Riess22, so the
    DL shift is smaller in absolute value (~3% vs ~8%).  Locks the
    cosmology preset relative ordering; if a future preset edit
    silently moves H0, this trips."""
    from app.services.ai_tools import _exec_fit_line_lfr

    _store_alpine()
    freedman = _exec_fit_line_lfr({
        "cache_key": "latest_literature_tables",
        "line_id": "[CII]",
        "cosmology": "freedman21_trgb",
    })
    riess = _exec_fit_line_lfr({
        "cache_key": "latest_literature_tables",
        "line_id": "[CII]",
        "cosmology": "riess22_shoes",
    })
    f_shift = abs(freedman["dl_shift_summary"]["median_log_l_shift_dex"])
    r_shift = abs(riess["dl_shift_summary"]["median_log_l_shift_dex"])
    assert f_shift < r_shift, (
        f"TRGB shift ({f_shift}) should be smaller than SH0ES shift ({r_shift})"
    )


def test_fit_line_lfr_variant_label_default_main() -> None:
    """Default variant label is 'main' so it always shows up in the UI."""
    from app.services.ai_tools import _exec_fit_line_lfr

    _store_alpine()
    out = _exec_fit_line_lfr({"cache_key": "latest_literature_tables", "line_id": "[CII]"})
    assert out["variant_label"] == "main"


def test_fit_line_lfr_variant_label_user_supplied() -> None:
    from app.services.ai_tools import _exec_fit_line_lfr

    _store_alpine()
    out = _exec_fit_line_lfr({
        "cache_key": "latest_literature_tables",
        "line_id": "[CII]",
        "variant_label": "z>=5 subsample",
    })
    assert out["variant_label"] == "z>=5 subsample"
