"""fit_line_lfr upper-limit censoring (Kelly 2007 delta; 2026-06-12).

Physics discipline locked here:
- OPT-IN (include_upper_limits, default False) — every pre-existing baseline
  is unchanged; the default run instead surfaces a censoring_hint.
- Only '<'-luminosity rows with a GENUINELY tabulated FWHM **and** error
  qualify (a true non-detection has no measured width — x is never
  invented). '>' rows, FWHM-limited rows, direction-unverifiable rows, and
  rows missing FWHM(+err) are rejected with specific reasons.
- Censored rows enter ONLY the Bayesian likelihood; OLS + limits = loud
  refusal, sampler failure with limits admitted = loud failure (no silent
  drop into an OLS that ignores them).
- ysig policy for bare limits = median detected yerr, counted and declared.
"""
from __future__ import annotations

import asyncio

import numpy as np

import app.services.ai_tools as ai_tools
from app.services.ai_tools import store_search_results


def _detection(i: int, fwhm: float, logl: float) -> dict:
    return {
        "source_name": f"DET-{i}",
        "redshift": 4.5 + 0.01 * i,
        "line_id": "[CII]",
        "log_luminosity": logl,
        "log_luminosity_err": 0.05,
        "fwhm_km_s": fwhm,
        "fwhm_err_km_s": 0.06 * fwhm,
        "quality_flags": [],
        "raw_values": {"luminosity": f"{logl}", "fwhm": f"{fwhm}"},
        "citation": {"bibcode": "2020A&A...643A...2B", "arxiv_id": "2002.00962"},
    }


def _limit_row(name: str, raw_lum: str, **overrides) -> dict:
    row = {
        "source_name": name,
        "redshift": 5.0,
        "line_id": "[CII]",
        "log_luminosity": 8.4,
        "log_luminosity_err": None,
        "fwhm_km_s": 260.0,
        "fwhm_err_km_s": 30.0,
        "quality_flags": ["luminosity_limit"],
        "raw_values": {"luminosity": raw_lum, "fwhm": "260"},
        "citation": {"bibcode": "2020A&A...643A...2B", "arxiv_id": "2002.00962"},
    }
    row.update(overrides)
    return row


def _seed_cache(rows: list[dict], key: str) -> str:
    store_search_results(key, {
        "schema_version": 2,
        "kind": "literature_tables",
        "cache_key": key,
        "arxiv_id": "2002.00962",
        "line_measurements": rows,
        "tables": [],
    })
    return key


def _fit(key: str, **extra) -> dict:
    return asyncio.run(ai_tools._exec_fit_line_lfr_async(
        {"cache_key": key, **extra}, "censoring-test", "",
    ))


def _sample_rows() -> list[dict]:
    rng = np.random.default_rng(7)
    detections = [
        _detection(i, fwhm=float(f), logl=float(8.0 + 0.8 * np.log10(f / 100.0) + rng.normal(0, 0.1)))
        for i, f in enumerate((180, 240, 310, 390, 480, 560, 640, 720))
    ]
    limits = [
        _limit_row("LIM-OK-1", "< 8.4"),
        _limit_row("LIM-OK-2", "< 8.2", log_luminosity=8.2),
        _limit_row("LIM-OWNERR", "< 8.6", log_luminosity=8.6, log_luminosity_err=0.1),
        _limit_row("LIM-LOWER", "> 9.0"),                               # '>' unsupported
        _limit_row("LIM-NOFWHMERR", "< 8.3", fwhm_err_km_s=None),       # x err missing
        _limit_row("LIM-NODIR", "8.3 (limit)"),                         # direction unverifiable
    ]
    return detections + limits


def test_default_off_unchanged_with_hint():
    key = _seed_cache(_sample_rows(), "latest_literature_tables:censoring-test")
    out = _fit(key)
    assert out["success"] is True
    assert out["n_used"] == 8
    assert out["n_censored_used"] == 0
    assert out["censoring"]["include_upper_limits"] is False
    # All 6 limit rows rejected under the historical reason.
    assert sum(1 for r in out["rejected_summary"] if r["reason"] == "limit_flag") == 6
    assert "include_upper_limits=true" in out["censoring_hint"]


def test_opt_in_admits_qualified_limits_only():
    key = _seed_cache(_sample_rows(), "latest_literature_tables:censoring-test")
    out = _fit(key, include_upper_limits=True, fit_method_requested="bayesian_xyerr")
    assert out["success"] is True
    assert out["fit_method"] == "bayesian_xyerr_linmix"
    assert out["n_used"] == 8                      # detections-only count
    assert out["n_censored_used"] == 3             # the three '<' rows with FWHM+err
    cens = out["censoring"]
    assert cens["method"] == "kelly2007_linmix_delta"
    assert cens["censored_ysig_policy"] == {"own_err": 1, "median_detected_err": 2}
    assert "never invented" in cens["note"] or "detections-only" in cens["note"]
    reasons = {r["source_name"]: r["reason"] for r in out["rejected_summary"]}
    assert reasons["LIM-LOWER"] == "censored_lower_limit_unsupported"
    assert reasons["LIM-NOFWHMERR"] == "censored_missing_fwhm_err"
    assert reasons["LIM-NODIR"] == "censored_direction_unverifiable"
    # Fit numbers stay sane (true slope 0.8).
    assert 0.3 < out["beta"] < 1.3


def test_ols_with_limits_refuses_loudly():
    key = _seed_cache(_sample_rows(), "latest_literature_tables:censoring-test")
    out = _fit(key, include_upper_limits=True, fit_method_requested="ols")
    assert out["success"] is False
    assert out["error_class"] == "censoring_requires_bayesian"
    assert out["__do_not_claim__"] is True


def test_missing_two_axis_errors_with_limits_refuses_loudly():
    rows = _sample_rows()
    for r in rows[:8]:
        r["fwhm_err_km_s"] = None  # detections lose x errors -> no bayesian
    key = _seed_cache(rows, "latest_literature_tables:censoring-test")
    out = _fit(key, include_upper_limits=True)
    assert out["success"] is False
    assert out["error_class"] == "censoring_needs_two_axis_errors"


def test_censored_rows_follow_unit_conversion(monkeypatch):
    # Adversarial-review blocker: L_prime conversion used to transform
    # detections only, leaving censored y in L_solar — a silently mixed-frame
    # likelihood. Spy on the kelly07 inputs: with luminosity_kind=L_prime the
    # censored y values must carry the same +0.658 dex [CII] offset.
    captured = {}
    from app.services import bayesian_inference as bi
    real = bi.kelly07_linmix_fit

    def spy(**kw):
        captured["y"] = np.array(kw["y"])
        captured["delta"] = kw.get("delta")
        return real(**kw)

    monkeypatch.setattr(bi, "kelly07_linmix_fit", spy)
    key = _seed_cache(_sample_rows(), "latest_literature_tables:censoring-test")
    out = _fit(key, include_upper_limits=True, luminosity_kind="L_prime",
               fit_method_requested="bayesian_xyerr")
    assert out["success"] is True
    assert out["n_censored_used"] == 3
    delta = captured["delta"]
    censored_y = captured["y"][~np.asarray(delta, dtype=bool)]
    # Raw limits were 8.4/8.2/8.6 (L_solar); in L_prime they must sit at
    # +0.658 dex: 9.058/8.858/9.258.
    assert sorted(round(v, 2) for v in censored_y) == [8.86, 9.06, 9.26]


def test_lensed_censored_row_rejected_like_detection():
    rows = _sample_rows()
    rows.append(_limit_row("LIM-LENSED", "< 9.4", log_luminosity=9.4,
                           is_lensed=True, mu_lens=None))
    key = _seed_cache(rows, "latest_literature_tables:censoring-test")
    out = _fit(key, include_upper_limits=True, fit_method_requested="bayesian_xyerr")
    assert out["success"] is True
    assert out["n_censored_used"] == 3  # the lensed limit did NOT slip in
    reasons = {r["source_name"]: r["reason"] for r in out["rejected_summary"]}
    assert reasons["LIM-LENSED"] == "lensed_no_mu_correction"


def test_censored_citations_join_the_pool():
    rows = _sample_rows()
    rows.append(_limit_row(
        "LIM-OTHERPAPER", "< 8.5", log_luminosity=8.5,
        citation={"bibcode": "2019ApJ...881...63N", "arxiv_id": "1906.00001"},
    ))
    key = _seed_cache(rows, "latest_literature_tables:censoring-test")
    out = _fit(key, include_upper_limits=True, fit_method_requested="bayesian_xyerr")
    assert out["success"] is True
    assert out["n_censored_used"] == 4
    assert "2019ApJ...881...63N" in out["citation_summary"]["citations"]


def test_too_few_detections_with_limits_fails_preflight():
    rows = _sample_rows()[:3] + _sample_rows()[8:11]  # 3 detections + 3 '<' limits
    key = _seed_cache(rows, "latest_literature_tables:censoring-test")
    out = _fit(key, include_upper_limits=True)
    assert out["success"] is False
    assert out["error_class"] == "censoring_needs_min_detections"


def test_kelly07_delta_plumbs_through():
    from app.services.bayesian_inference import kelly07_linmix_fit

    rng = np.random.default_rng(3)
    n = 24
    x = rng.uniform(-0.3, 0.9, n)
    y = 8.0 + 0.8 * x + rng.normal(0, 0.08, n)
    delta = np.ones(n, dtype=bool)
    delta[-4:] = False           # last four become upper limits
    y[-4:] = y[-4:] + 0.2        # limits sit above the relation
    out = kelly07_linmix_fit(
        x=x, y=y,
        xerr=np.full(n, 0.05), yerr=np.full(n, 0.08),
        delta=delta, K=2, nchains=2, miniter=600, maxiter=4000, seed=11,
    )
    assert out["n_censored"] == 4
    assert 0.4 < out["beta_median"] < 1.2
