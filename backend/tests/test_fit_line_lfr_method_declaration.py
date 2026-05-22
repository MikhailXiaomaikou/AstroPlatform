"""M2 acceptance: fit_line_lfr methodology declaration + cosmology mismatch warning.

Core M2 contracts:
1. fit_method / fit_method_requested / fit_method_downgrade_reason must always
   be present in the returned dict (even when only OLS runs), never omitted.
2. Explicit bayesian_xyerr request + missing err -> __tool_status__=METHOD_DOWNGRADED
   + reason naming which axis is missing.
3. Explicit bayesian_xyerr request + err fully populated -> still downgraded in M2,
   reason says "bayesian backend not yet wired" (this test will change when M3 is wired).
4. Sample contains source_cosmology inconsistent with current manifest -> warnings
   include cosmology_mismatch, cosmology_mismatch=True.
5. Old v1 cache (rows without v2 fields) can still run OLS without KeyError.
6. residual_rms_dex field exists; scatter_dex alias is still present (backward compat).
7. provenance.method_provenance node carries all three declaration fields.
"""

from unittest.mock import patch

from app.services.ai_tools import _exec_fit_line_lfr
from app.services import result_provenance as _rp


def _make_rows(n: int, *, with_err: bool = False, with_cosmo: str | None = None,
               with_mu: bool = False) -> list[dict]:
    """Create n synthetic line_measurements, optionally with err / cosmology / mu fields."""
    rows = []
    for i in range(n):
        row = {
            "source_name": f"SRC-{i}",
            "redshift": 5.0 + 0.1 * i,
            "line_id": "[CII]",
            "log_luminosity": 9.0 + 0.05 * i,
            "fwhm_km_s": 200.0 + 10.0 * i,
            "quality_flags": [],
            "citation": {"bibcode": f"2024Paper.X{i:02d}"},
            "bibcode": f"2024Paper.X{i:02d}",
            "arxiv_id": f"2404.{i:05d}",
            # v2 fields default to None
            "log_luminosity_err": None,
            "fwhm_err_km_s": None,
            "mu_lens": None,
            "is_lensed": None,
            "source_cosmology": None,
        }
        if with_err:
            row["log_luminosity_err"] = 0.1
            row["fwhm_err_km_s"] = 15.0
        if with_cosmo:
            row["source_cosmology"] = {"name": with_cosmo, "H0": 73.8, "Om0": 0.295}
        if with_mu:
            row["mu_lens"] = 2.0
            row["is_lensed"] = True
        rows.append(row)
    return rows


def _patch_cache(rows: list[dict]):
    """Patch the cache resolution point inside fit_line_lfr to return fixed rows."""
    return patch(
        "app.services.ai_tools._resolve_literature_measurement_cache",
        return_value=(rows, "latest_literature_tables"),
    )


# ── Test 1: METHOD_DOWNGRADED status is added to _VALID_STATUS ──────────────

def test_method_downgraded_is_valid_status():
    assert "method_downgraded" in _rp._VALID_STATUS
    assert _rp.METHOD_DOWNGRADED == "method_downgraded"


def test_fit_line_lfr_is_in_stochastic_tools():
    assert "fit_line_lfr" in _rp._STOCHASTIC_TOOLS


# ── Test 2: fit_method field is always present (even on the default auto path) ───────────────

def test_default_auto_returns_ols_with_method_fields():
    rows = _make_rows(6)
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"cache_key": "latest_literature_tables"})
    assert out["success"] is True
    assert out["fit_method"] == "ols"
    assert out["fit_method_requested"] == "auto"
    assert out["fit_method_downgrade_reason"] is None
    # auto path is not considered a downgrade
    assert out.get("__tool_status__") != "METHOD_DOWNGRADED"
    # PART AI #2: model + fit_orientation strings now include luminosity_kind unit
    # labels (log L/L_sun default vs log L_prime/(K km/s pc^2) explicit opt-in).
    assert out["model"] == "log10(L/L_sun) = alpha + beta * log10(FWHM_km_s / 100)"
    assert out["fit_orientation"]["dependent_variable"] == "log10(L/L_sun)"
    assert out["fit_orientation"]["independent_variable"] == "log10(FWHM_km_s / 100)"
    assert "L_solar" in out["fit_orientation"]["literature_comparison_note"]
    # PART AI #2: unit fields are required
    assert out["luminosity_kind"] == "L_solar"
    assert out["intercept_unit"] == "log10(L/L_sun)"
    assert "log_L per log10(FWHM/100" in out["slope_unit"]
    assert out["n_unit_converted"] == 0  # L_solar 默认路径无转换
    assert out["unit_conversion_failures"] == []


def test_explicit_ols_never_downgrades():
    rows = _make_rows(6)
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "ols"})
    assert out["fit_method"] == "ols"
    assert out["fit_method_requested"] == "ols"
    assert out["fit_method_downgrade_reason"] is None


# ── Test 3: bayesian_xyerr requested + err missing -> downgrade + reason names the missing axis ──

def test_bayesian_requested_but_errs_missing_triggers_downgrade():
    rows = _make_rows(6, with_err=False)
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "bayesian_xyerr"})
    assert out["__tool_status__"] == "METHOD_DOWNGRADED"
    assert out["fit_method"] == "ols"
    assert out["fit_method_requested"] == "bayesian_xyerr"
    reason = out["fit_method_downgrade_reason"]
    assert reason is not None
    assert "fwhm_err_km_s" in reason and "log_luminosity_err" in reason
    # On downgrade, supports_measurement_claims must be False
    assert out["supports_measurement_claims"] is False
    assert out["__do_not_claim__"] is True
    # error_axes diagnostic field
    assert out["error_axes_available"]["x_err_rows"] == 0
    assert out["error_axes_available"]["y_err_rows"] == 0
    assert out["error_axes_available"]["both_axes_available"] is False


def test_bayesian_requested_errs_available_runs_bayesian_in_m3(monkeypatch):
    """M3 contract: bayesian_xyerr + err columns populated → real Bayesian
    fit (linmix), no downgrade.  We monkeypatch kelly07_linmix_fit so the
    test does not actually pay the MCMC cost; the integration test that
    runs the real sampler lives in test_bayesian_linmix_kelly07.py.
    """
    rows = _make_rows(6, with_err=True)

    fake_bayes = {
        "method": "bayesian_xyerr_linmix",
        "alpha_median": 8.5, "alpha_hdi_94": [8.3, 8.7],
        "beta_median": 1.2,  "beta_hdi_94": [1.0, 1.4],
        "intrinsic_scatter_dex": 0.18,
        "intrinsic_scatter_dex_hdi": [0.12, 0.25],
        "parameters": {
            "alpha": {"mean": 8.5, "median": 8.5, "std": 0.1, "hdi_low_94": 8.3, "hdi_high_94": 8.7, "ess": 800},
            "beta":  {"mean": 1.2, "median": 1.2, "std": 0.1, "hdi_low_94": 1.0, "hdi_high_94": 1.4, "ess": 800},
            "sigma_int": {"mean": 0.18, "median": 0.18, "std": 0.03, "hdi_low_94": 0.12, "hdi_high_94": 0.25, "ess": 800},
        },
        "n_draws_total": 16000, "n_chains": 4, "miniter": 4000, "maxiter": 20000, "K": 2,
        "converged": True, "publication_ready": True,
        "package": "linmix (vendored)", "reference": "Kelly 2007",
    }
    import app.services.bayesian_inference as bi
    monkeypatch.setattr(bi, "kelly07_linmix_fit", lambda **kw: fake_bayes)

    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "bayesian_xyerr"})
    # Bayesian path actually ran — no METHOD_DOWNGRADED status.
    assert out.get("__tool_status__") != "METHOD_DOWNGRADED"
    assert out["fit_method"] == "bayesian_xyerr_linmix"
    assert out["fit_method_downgrade_reason"] is None
    assert out["error_axes_available"]["both_axes_available"] is True
    # alpha / beta now come from Bayesian medians.
    assert out["alpha"] == 8.5
    assert out["beta"] == 1.2
    # M3 fields exposed.
    assert out["intrinsic_scatter_dex"] == 0.18
    assert out["intrinsic_scatter_dex_hdi"] == [0.12, 0.25]
    assert out["bayesian_summary"] is fake_bayes
    # method_provenance carries sampler bookkeeping.
    mp = out["provenance"]["method_provenance"]
    assert mp["intrinsic_scatter_dex"] == 0.18
    assert mp["bayesian_n_draws"] == 16000
    assert mp["bayesian_converged"] is True
    assert mp["bayesian_publication_ready"] is True


def test_bayesian_linmix_path_attaches_kelly07_method_bibcode(monkeypatch):
    """PART AH C6 — After bayesian_xyerr_linmix runs successfully,
    provenance.datasets must carry the bibcode `2007ApJ...665.1489K` for
    Kelly 2007 (the linmix methodology citation), so that claim_validator's
    _build_valid_bibcode_pool auto-recognizes it and replies citing "Kelly 2007"
    / "Kelly 07" are no longer flagged as author-year fabrication.

    M7 retest #2 reproducer: 6 citation guard violations all say
    `Kelly 2007 (citation context)` is an unsourced author-year — but it is
    actually the method paper hardcoded inside the fit_line_lfr tool, not a
    model fabrication.
    """
    rows = _make_rows(6, with_err=True)

    fake_bayes = {
        "method": "bayesian_xyerr_linmix",
        "alpha_median": 8.5, "alpha_hdi_94": [8.3, 8.7],
        "beta_median": 1.2,  "beta_hdi_94": [1.0, 1.4],
        "intrinsic_scatter_dex": 0.18,
        "intrinsic_scatter_dex_hdi": [0.12, 0.25],
        "parameters": {
            "alpha": {"mean": 8.5, "median": 8.5, "std": 0.1, "hdi_low_94": 8.3, "hdi_high_94": 8.7, "ess": 800},
            "beta":  {"mean": 1.2, "median": 1.2, "std": 0.1, "hdi_low_94": 1.0, "hdi_high_94": 1.4, "ess": 800},
            "sigma_int": {"mean": 0.18, "median": 0.18, "std": 0.03, "hdi_low_94": 0.12, "hdi_high_94": 0.25, "ess": 800},
        },
        "n_draws_total": 16000, "n_chains": 4, "miniter": 4000, "maxiter": 20000, "K": 2,
        "converged": True, "publication_ready": True,
        "package": "linmix (vendored)", "reference": "Kelly 2007",
    }
    import app.services.bayesian_inference as bi
    monkeypatch.setattr(bi, "kelly07_linmix_fit", lambda **kw: fake_bayes)

    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "bayesian_xyerr"})

    assert out["fit_method"] == "bayesian_xyerr_linmix"
    datasets = out["provenance"]["datasets"]
    method_entries = [d for d in datasets if d.get("service_key") == "method_citation"]
    assert len(method_entries) == 1, (
        f"linmix path must attach exactly one method_citation dataset; "
        f"got datasets={[d.get('service_key') for d in datasets]}"
    )
    assert method_entries[0]["article"] == "2007ApJ...665.1489K"
    assert method_entries[0]["source_authority"] == "method_paper"
    # OLS-only fits MUST NOT carry the Kelly bibcode (different fit method).
    # Sanity: the literature_table_fit dataset is still there.
    lit_entries = [d for d in datasets if d.get("service_key") == "literature_table_fit"]
    assert len(lit_entries) == 1


def test_ols_only_path_does_not_attach_kelly07_bibcode(monkeypatch):
    """The Kelly 2007 method bibcode is ONLY appended on the linmix
    path. OLS / no-error-bars paths must not pollute the bibcode pool
    with a method ref they did not actually use.
    """
    rows = _make_rows(6, with_err=False)
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "ols"})
    datasets = out["provenance"]["datasets"]
    method_entries = [d for d in datasets if d.get("service_key") == "method_citation"]
    assert len(method_entries) == 0


def test_kelly07_bibcode_makes_validator_accept_kelly_2007_citation(monkeypatch):
    """End-to-end: claim_validator's bibcode pool must accept
    `2007ApJ...665.1489K` when the linmix path ran, so a reply citing
    'Kelly 2007' (the canonical linmix reference) does not trip
    suspicious_author_year. This was the M7 retest #2 false positive.
    """
    from app.services.claim_validator import _build_valid_bibcode_pool

    rows = _make_rows(6, with_err=True)
    fake_bayes = {
        "method": "bayesian_xyerr_linmix",
        "alpha_median": 8.5, "alpha_hdi_94": [8.3, 8.7],
        "beta_median": 1.2,  "beta_hdi_94": [1.0, 1.4],
        "intrinsic_scatter_dex": 0.18,
        "intrinsic_scatter_dex_hdi": [0.12, 0.25],
        "parameters": {
            "alpha": {"mean": 8.5, "median": 8.5, "std": 0.1, "hdi_low_94": 8.3, "hdi_high_94": 8.7, "ess": 800},
            "beta":  {"mean": 1.2, "median": 1.2, "std": 0.1, "hdi_low_94": 1.0, "hdi_high_94": 1.4, "ess": 800},
            "sigma_int": {"mean": 0.18, "median": 0.18, "std": 0.03, "hdi_low_94": 0.12, "hdi_high_94": 0.25, "ess": 800},
        },
        "n_draws_total": 16000, "n_chains": 4, "miniter": 4000, "maxiter": 20000, "K": 2,
        "converged": True, "publication_ready": True,
        "package": "linmix (vendored)", "reference": "Kelly 2007",
    }
    import app.services.bayesian_inference as bi
    monkeypatch.setattr(bi, "kelly07_linmix_fit", lambda **kw: fake_bayes)

    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "bayesian_xyerr"})

    # Build a tool_results list as the agent loop would, run it through
    # the validator's bibcode pool harvester.
    tool_results = [{"tool": "fit_line_lfr", "result": out}]
    pool = _build_valid_bibcode_pool(tool_results)
    assert "2007ApJ...665.1489K" in pool, (
        f"Kelly 2007 method bibcode missing from validator pool — "
        f"reply citations like 'Kelly 2007' would still be flagged as "
        f"suspicious_author_year. Pool: {sorted(pool)}"
    )


def test_bayesian_sampler_failure_falls_back_to_ols(monkeypatch):
    """M3 contract: when err columns are present but the sampler raises,
    we still get a sane result — OLS with METHOD_DOWNGRADED + concrete
    reason that mentions the exception class.
    """
    rows = _make_rows(6, with_err=True)
    import app.services.bayesian_inference as bi

    def _boom(**kw):
        raise RuntimeError("synthetic sampler failure")

    monkeypatch.setattr(bi, "kelly07_linmix_fit", _boom)

    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "bayesian_xyerr"})
    assert out["__tool_status__"] == "METHOD_DOWNGRADED"
    assert out["fit_method"] == "ols"
    assert out["bayesian_error"] is not None
    assert "RuntimeError" in out["bayesian_error"]
    assert "synthetic sampler failure" in out["fit_method_downgrade_reason"]


def test_auto_with_errs_picks_bayesian(monkeypatch):
    """auto + err columns populated → Bayesian (not a downgrade)."""
    rows = _make_rows(6, with_err=True)
    fake_bayes = {
        "method": "bayesian_xyerr_linmix",
        "alpha_median": 9.0, "alpha_hdi_94": [8.8, 9.2],
        "beta_median": 1.1,  "beta_hdi_94": [0.9, 1.3],
        "intrinsic_scatter_dex": 0.20,
        "intrinsic_scatter_dex_hdi": [0.15, 0.27],
        "parameters": {
            "alpha": {"mean": 9.0, "median": 9.0, "std": 0.1, "hdi_low_94": 8.8, "hdi_high_94": 9.2, "ess": 800},
            "beta":  {"mean": 1.1, "median": 1.1, "std": 0.1, "hdi_low_94": 0.9, "hdi_high_94": 1.3, "ess": 800},
            "sigma_int": {"mean": 0.2, "median": 0.2, "std": 0.03, "hdi_low_94": 0.15, "hdi_high_94": 0.27, "ess": 800},
        },
        "n_draws_total": 12000, "n_chains": 4, "miniter": 4000, "maxiter": 20000, "K": 2,
        "converged": True, "publication_ready": True,
        "package": "linmix (vendored)", "reference": "Kelly 2007",
    }
    import app.services.bayesian_inference as bi
    monkeypatch.setattr(bi, "kelly07_linmix_fit", lambda **kw: fake_bayes)

    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "auto"})
    assert out["fit_method"] == "bayesian_xyerr_linmix"
    assert out.get("__tool_status__") != "METHOD_DOWNGRADED"
    # auto carries no methodology promise → no downgrade label
    assert out["fit_method_downgrade_reason"] is None


def test_auto_without_errs_stays_ols_no_downgrade():
    """auto + err columns missing → silently stays OLS, NOT a downgrade."""
    rows = _make_rows(6, with_err=False)
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "auto"})
    assert out["fit_method"] == "ols"
    assert out["fit_method_downgrade_reason"] is None
    assert out.get("__tool_status__") != "METHOD_DOWNGRADED"


# ── Test 4: cosmology mismatch warning ────────────────────────────────

def test_source_cosmology_mismatch_triggers_warning():
    # Sample declares Riess2011-style non-Planck cosmology
    rows = _make_rows(6, with_cosmo="Riess2011")
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({})
    assert out["cosmology_mismatch"] is True
    assert "Riess2011" in out["sample_source_cosmologies"]
    warnings = out.get("warnings") or []
    assert any(w.get("code") == "cosmology_mismatch" for w in warnings)
    assert out["provenance"]["method_provenance"]["cosmology_mismatch"] is True


def test_matching_cosmology_no_warning():
    # Force current manifest to Planck18, sample also Planck18 → no warning
    rows = _make_rows(6, with_cosmo="Planck18")
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({})
    assert out["cosmology_mismatch"] is False
    warnings = out.get("warnings") or []
    assert not any(w.get("code") == "cosmology_mismatch" for w in warnings)


# ── Test 5: backward compatibility with v1 rows (no v2 fields) ─────────────────

def test_v1_rows_still_fit_without_keyerror():
    """Rows that never went through v2 normalization must still fit OK."""
    v1_rows = [
        {
            "source_name": f"S{i}", "redshift": 5.0, "line_id": "[CII]",
            "log_luminosity": 9.0 + 0.1 * i, "fwhm_km_s": 250.0 + 5.0 * i,
            "quality_flags": [], "citation": {"bibcode": f"2024X{i:02d}"},
            "bibcode": f"2024X{i:02d}",
            # Key: no v2 fields present
        }
        for i in range(6)
    ]
    with _patch_cache(v1_rows):
        out = _exec_fit_line_lfr({})
    assert out["success"] is True
    assert out["fit_method"] == "ols"
    # No err fields -> error_axes diagnostic shows 0/6
    assert out["error_axes_available"]["x_err_rows"] == 0
    assert out["error_axes_available"]["y_err_rows"] == 0
    # No mu_lens / is_lensed -> all unknown
    assert out["n_lensed"] == 0
    assert out["n_unlensed"] == 0
    assert out["n_lensed_unknown"] == 6


# ── Test 6: residual_rms_dex + scatter_dex alias ─────────────────────

def test_residual_rms_dex_field_and_alias():
    rows = _make_rows(6)
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({})
    assert "residual_rms_dex" in out
    assert "scatter_dex" in out  # deprecated alias
    assert out["residual_rms_dex"] == out["scatter_dex"]


# ── Test 7: lensing statistics counters ──────────────────────────────


def test_lensing_counters_when_some_rows_are_lensed():
    rows = _make_rows(3, with_mu=False) + _make_rows(3, with_mu=True)
    # tweak source_names to keep them unique
    for i, r in enumerate(rows):
        r["source_name"] = f"S{i}"
    # First 3 rows have is_lensed=None (unknown), last 3 have is_lensed=True
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({})
    assert out["n_lensed"] == 3
    assert out["n_lensed_unknown"] == 3
    assert out["n_unlensed"] == 0
    # M2 does not yet demagnify, so demagnified must be 0
    assert out["lensed_sources_demagnified"] == 0


# ── Test 8: method_provenance node is fully populated ───────────────────────────────

def test_method_provenance_node_populated():
    """When err columns are missing, the downgrade path is taken and
    method_provenance carries every diagnostic field."""
    rows = _make_rows(6, with_err=False, with_cosmo="Planck18")
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"fit_method_requested": "bayesian_xyerr"})
    mp = out["provenance"]["method_provenance"]
    assert mp["fit_method"] == "ols"
    assert mp["fit_method_requested"] == "bayesian_xyerr"
    assert mp["fit_method_downgrade_reason"] is not None
    assert "cosmology_used" in mp
    assert "cosmology_mismatch" in mp
    assert mp["lensed_sources_demagnified"] == 0
    # M3: Bayesian-specific keys are present even when the path didn't
    # run (all None in that case) so consumers can rely on shape.
    assert "intrinsic_scatter_dex" in mp
    assert "bayesian_n_draws" in mp
    assert mp["intrinsic_scatter_dex"] is None
    assert mp["bayesian_n_draws"] is None


# ── PART AB: methodology_consistency_violations gate fixes ──────────

def test_methodology_violation_not_triggered_without_fit_line_lfr_success():
    """PART AB trigger fix: when fit_line_lfr never ran (or only failed),
    a prose mention of "Bayesian" in the reply must NOT raise a
    method_mismatch violation. Otherwise R2.4 M2 reproduces — 0 tool
    calls + the user's prompt mentioning "Bayesian linear regression"
    would block the reply pre-flight.
    """
    from app.services.claim_validator import methodology_consistency_violations

    # Case 1: empty tool_results
    out1 = methodology_consistency_violations(
        "We could use a Bayesian linmix fit here.",
        [],
    )
    assert out1 == [], "empty tool_results should produce zero violations"

    # Case 2: fit_line_lfr ran but FAILED (banner-stamped)
    out2 = methodology_consistency_violations(
        "We could use a Bayesian linmix fit here.",
        [{
            "tool": "fit_line_lfr",
            "result": {
                "success": False,
                "__tool_status__": "FAILED",
                "error": "no rows",
            },
        }],
    )
    assert out2 == [], "FAILED fit_line_lfr should not enable method_mismatch"

    # Case 3: fit_line_lfr ran but EMPTY
    out3 = methodology_consistency_violations(
        "We could use a Bayesian fit here.",
        [{
            "tool": "fit_line_lfr",
            "result": {
                "success": True,
                "__tool_status__": "EMPTY",
                "row_count": 0,
            },
        }],
    )
    assert out3 == [], "EMPTY fit_line_lfr should not enable method_mismatch"


def test_methodology_violation_triggers_only_on_real_success_with_wrong_method():
    """The actual bug: fit_line_lfr ran successfully with OLS, but the
    prose claims it was Bayesian. Now the gate fires correctly."""
    from app.services.claim_validator import methodology_consistency_violations

    out = methodology_consistency_violations(
        "We ran a Bayesian xyerr fit and the slope is 1.05.",
        [{
            "tool": "fit_line_lfr",
            "result": {
                "success": True,
                "fit_method": "ols",  # actual method that ran — NOT bayesian
                "publication_ready": True,
                "slope": 1.05,
            },
        }],
    )
    assert len(out) == 1
    assert out[0].kind == "method_mismatch"


def test_blocked_methodology_reply_text_uses_method_specific_advice():
    """PART AB route fix: method_mismatch violations rendered through
    `blocked_methodology_reply_text` get the right fix instruction
    (call fit_line_lfr with bayesian_xyerr) — NOT the citation-flavour
    "re-run the archive query" message."""
    from app.services.claim_validator import (
        blocked_methodology_reply_text,
        CitationViolation,
    )

    text = blocked_methodology_reply_text([
        CitationViolation(
            kind="method_mismatch",
            match_text="Bayesian",
            line_number=21,
        ),
    ])
    # Right phrasing for the method violation
    assert "fit_method_requested=\"bayesian_xyerr\"" in text
    assert "Bayesian" in text
    # NOT the citation-flavour misleading hint
    assert "re-run the archive" not in text
    assert "re-run the relevant archive or literature" not in text


def test_blocked_methodology_reply_text_separates_demag_from_method():
    """When both bayesian and demagnify violations exist, they get
    separate explanations and separate fix instructions."""
    from app.services.claim_validator import (
        blocked_methodology_reply_text,
        CitationViolation,
    )

    text = blocked_methodology_reply_text([
        CitationViolation(kind="method_mismatch", match_text="Bayesian", line_number=4),
        CitationViolation(kind="demagnify_count_mismatch", match_text="demagnified 12 sources", line_number=8),
    ])
    assert "Bayesian" in text
    assert "demagnified 12 sources" in text
    assert "demagnify_sample" in text
    assert "fit_method_requested=\"bayesian_xyerr\"" in text


# ── PART AI #2: luminosity_kind unit parameter (L_solar / L_prime) ───────────


def test_default_luminosity_kind_is_l_solar_no_conversion():
    """With the default luminosity_kind="L_solar", log_luminosity is unchanged and conversion count is 0."""
    rows = _make_rows(6)
    original_log_l = [r["log_luminosity"] for r in rows]
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"cache_key": "latest_literature_tables"})
    assert out["luminosity_kind"] == "L_solar"
    assert out["intercept_unit"] == "log10(L/L_sun)"
    assert out["n_unit_converted"] == 0
    assert out["unit_conversion_failures"] == []
    # row.log_luminosity must not have been mutated
    assert [r["log_luminosity"] for r in rows] == original_log_l


def test_explicit_l_prime_converts_all_rows_and_relabels_units():
    """Explicitly passing luminosity_kind="L_prime" converts all ALPINE z=5 rows,
    and the alpha unit becomes log L_prime, numerically offset from L_solar by ~+2.2 dex."""
    rows = _make_rows(6)  # z=5.0..5.5, [CII], log_L=9.0..9.25
    with _patch_cache(rows):
        out_solar = _exec_fit_line_lfr({"cache_key": "x"})
        out_prime = _exec_fit_line_lfr({"cache_key": "x", "luminosity_kind": "L_prime"})
    assert out_solar["luminosity_kind"] == "L_solar"
    assert out_prime["luminosity_kind"] == "L_prime"
    assert out_prime["intercept_unit"] == "log10(L_prime/(K km/s pc^2))"
    assert "L_prime" in out_prime["model"]
    assert "K km/s pc^2" in out_prime["model"]
    assert out_prime["n_unit_converted"] == 6
    assert out_prime["unit_conversion_failures"] == []
    # alpha under L_prime should be ~+2 dex larger than L_solar (z=5 [CII] single-point offset
    # is +2.215, but the OLS fit across z=5.0..5.5 means the intercept is not a strict rigid shift)
    delta_alpha = out_prime["alpha"] - out_solar["alpha"]
    assert 1.9 < delta_alpha < 2.4, f"expected alpha shift ~+2.0 dex, got {delta_alpha:.3f}"
    # beta (slope) WILL change across L_solar/L_prime, because the 2*log(1+z) term is
    # a per-row z-dependent shift, not a rigid translation. For the z=5.0..5.5 sample,
    # each row shifts by a different amount, so the OLS slope naturally changes. This is
    # physical, not a bug. Sign must stay positive (LFR remains a positive correlation)
    # and magnitude should be in the ~0-10 range.
    assert out_prime["beta"] > 0
    assert 0 < out_prime["beta"] < 10
    assert 0 < out_solar["beta"] < 10


def test_l_prime_rejects_rows_missing_redshift():
    """Rows that fail conversion must not be silently fit; they must be added to
    rejected + unit_conversion_failures.

    Note: a bad line_id (e.g. "BogusLine") is rejected earlier at the _line_matches_filter
    stage (reason="line_filter") and never reaches the unit conversion path, so only the
    missing-redshift scenario is tested here."""
    rows = _make_rows(6)
    # Strip redshift from rows 2 and 4
    rows[2]["redshift"] = None
    rows[4]["redshift"] = None
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"cache_key": "x", "luminosity_kind": "L_prime"})
    assert out["luminosity_kind"] == "L_prime"
    assert out["n_used"] == 4  # 6 - 2 reject
    assert out["n_unit_converted"] == 4
    assert len(out["unit_conversion_failures"]) == 2
    # Failure reason must explicitly name the missing redshift
    failure_reasons = " ".join(f["reason"] for f in out["unit_conversion_failures"])
    assert "redshift" in failure_reasons.lower()


def test_l_prime_all_rows_failing_returns_failed_status_not_panic():
    """When all row conversions fail, must not numpy-panic; instead exit early with a FAILED status and clear error."""
    rows = _make_rows(6)
    for r in rows:
        r["redshift"] = None  # strip all redshifts
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"cache_key": "x", "luminosity_kind": "L_prime"})
    assert out["success"] is False
    assert out["__tool_status__"] == "FAILED"
    assert out["__do_not_claim__"] is True
    assert out["error_class"] == "unit_conversion_all_failed"
    assert out["luminosity_kind"] == "L_prime"
    assert len(out["unit_conversion_failures"]) == 6


def test_invalid_luminosity_kind_falls_back_to_l_solar():
    """An invalid string (typo / wrong case) must silently fall back to L_solar, not raise."""
    rows = _make_rows(6)
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"cache_key": "x", "luminosity_kind": "L_brightness"})
    assert out["luminosity_kind"] == "L_solar"
    assert out["n_unit_converted"] == 0

