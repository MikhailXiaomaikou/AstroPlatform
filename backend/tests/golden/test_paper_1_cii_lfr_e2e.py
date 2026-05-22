"""Golden e2e regression for Paper #1: Wu, Gao & Wang 2022 [C II] LFR.

Spec source: `.local/observational_cosmology_loop.md` paper 1.
Reference: arXiv:2211.04968.

Phase 4 of PART AI follow-up: locks the full PART AI tool chain
end-to-end through `_exec_fit_line_lfr` using a synthetic ALPINE +
Bothwell SPT mixed sample. This is a **unit-level e2e** (synthetic
rows + mocked cache resolver) — no HTTP mocks, no LLM call. The
"end-to-end" refers to the full guardrail chain through fit_line_lfr:

  - luminosity_kind="L_prime" conversion (PART AI #2)
  - cosmology recompute via riess22_shoes preset (existing path)
  - lensing coverage gate: SPT rows with mu_lens=None get rejected
    (PART AI #6)
  - readiness_checks 5 gates + relation_claimability (PART AI #4)
  - bayesian_xyerr_linmix Kelly 2007 method (existing) — mocked
    sampler so test stays fast
  - paper_generator.build_fit_line_lfr_diagnostics_section render
    over the result envelope (PART AI #5)

This test does NOT exercise:
  - chat.py agent loop (no LLM call)
  - extract_literature_tables (no ar5iv fetch — we feed synthetic rows
    into the cache resolver)
  - claim_validator on a final reply (no prose to validate)

Future work — a true HTTP-mocked agent-loop e2e is left for a separate
fixture under tests/golden/cii_lfr_paper1_full/ when respx is added
to requirements.

Locked invariants:

1. Mixed ALPINE (no_lensing) + SPT (all_sources_lensed) sample with
   SPT rows missing mu_lens → SPT rows rejected with
   reason='lensed_no_mu_correction'; fit runs on ALPINE only.
2. result.lensing_summary 5 buckets correct (n_unlensed_in_fit,
   n_lensed_skipped_no_mu, papers_default_lensed contains
   "2013ApJ...779...67B").
3. luminosity_kind="L_prime" → result.intercept_unit =
   "log10(L_prime/(K km/s pc^2))"; ALPINE rows converted with positive
   shift ~+2 dex (z~5).
4. cosmology="riess22_shoes" → result.cosmology_recomputed=True with
   non-zero dl_shift_summary.
5. fit_method="bayesian_xyerr_linmix" path: result.bayesian_summary
   contains Kelly 2007 reference + per-param ess/rhat/hdi_94.
6. paper_generator.build_fit_line_lfr_diagnostics_section produces a
   markdown section containing all 6 PART AI checkpoints.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# ── Synthetic literature-table fixtures ─────────────────────────────


def _alpine_rows_synthetic() -> list[dict]:
    """10 synthetic ALPINE [C II] rows at z=4.4-5.8.

    Mimics the schema produced by arxiv.py `_normalize_line_measurements`
    for Béthermin+2020. is_lensed=False because Béthermin ALPINE is a
    field survey (cii_paper_metadata.PAPER_LENSING entry).
    """
    rows = []
    for i in range(10):
        z = 4.4 + i * 0.15
        rows.append({
            "source_name": f"ALPINE-CII-{i:03d}",
            "redshift": z,
            "line_id": "[CII]",
            "log_luminosity": 8.4 + 0.05 * i,         # log L/L_sun
            "log_luminosity_err": 0.10,
            "fwhm_km_s": 200.0 + 15.0 * i,
            "fwhm_err_km_s": 25.0,
            "luminosity_unit": "log L_CII / L_sun",
            "mu_lens": None,
            "is_lensed": False,
            "source_cosmology": None,
            "quality_flags": [],
            "citation": {"bibcode": "2020A&A...643A...2B"},
            "bibcode": "2020A&A...643A...2B",
            "arxiv_id": "2002.00962",
            "table_id": "T_alpine",
            "table_label": "ALPINE Béthermin+2020 main",
            "row_index": i,
        })
    return rows


def _bothwell_spt_rows_synthetic() -> list[dict]:
    """4 synthetic Bothwell SPT-SMG [CII] rows. is_lensed=True via
    cii_paper_metadata.PAPER_LENSING fallback (the bibcode
    2013ApJ...779...67B is registered as all_sources_lensed). mu_lens
    intentionally None: the table did not expose μ, so fit_line_lfr
    must reject these rows with reason='lensed_no_mu_correction'."""
    rows = []
    for i in range(4):
        z = 2.5 + i * 0.4
        rows.append({
            "source_name": f"SPT-CII-{i:03d}",
            "redshift": z,
            "line_id": "[CII]",
            "log_luminosity": 9.5 + 0.1 * i,
            "log_luminosity_err": 0.15,
            "fwhm_km_s": 350.0 + 20.0 * i,
            "fwhm_err_km_s": 30.0,
            "luminosity_unit": "log L_CII / L_sun",
            "mu_lens": None,            # μ unknown → must reject
            "is_lensed": True,          # paper-level fallback set this
            "source_cosmology": None,
            "quality_flags": [],
            "citation": {"bibcode": "2013ApJ...779...67B"},
            "bibcode": "2013ApJ...779...67B",
            "arxiv_id": "1304.4256",
            "table_id": "T_bothwell",
            "table_label": "Bothwell+2013 SPT [CII]",
            "row_index": i,
        })
    return rows


@pytest.fixture
def mixed_paper1_sample():
    """ALPINE + Bothwell SPT mixed sample: 10 + 4 = 14 rows total."""
    return _alpine_rows_synthetic() + _bothwell_spt_rows_synthetic()


def _patch_cache(rows: list[dict]):
    """Patch the literature measurement cache resolver to return our
    synthetic rows under a predictable cache_key."""
    return patch(
        "app.services.ai_tools._resolve_literature_measurement_cache",
        return_value=(rows, "latest_literature_tables"),
    )


# ── PART AI #6: lensing coverage gate fires on SPT rows ───────────────


def test_paper1_spt_rows_rejected_for_missing_mu_lens(mixed_paper1_sample):
    """Bothwell SPT 4 rows with is_lensed=True + mu_lens=None must be
    rejected from fit; only ALPINE 10 rows enter the fit."""
    from app.services.ai_tools import _exec_fit_line_lfr

    with _patch_cache(mixed_paper1_sample):
        out = _exec_fit_line_lfr({
            "cache_key": "x",
            "luminosity_kind": "L_prime",
        })

    assert out["n_used"] == 10  # ALPINE only
    rejected_lensed = [
        r for r in out["rejected_summary"]
        if r.get("reason") == "lensed_no_mu_correction"
    ]
    assert len(rejected_lensed) == 4
    for r in rejected_lensed:
        assert "SPT" in str(r.get("source_name") or "")
        assert r.get("bibcode") == "2013ApJ...779...67B"
        assert "demagnify_sample" in str(r.get("detail") or "")


def test_paper1_lensing_summary_5_buckets(mixed_paper1_sample):
    """result.lensing_summary has 5 fields — 3 enter fit / 1 reject for clear
    user-actionable bucket counts."""
    from app.services.ai_tools import _exec_fit_line_lfr

    with _patch_cache(mixed_paper1_sample):
        out = _exec_fit_line_lfr({
            "cache_key": "x",
            "luminosity_kind": "L_prime",
        })

    summary = out["lensing_summary"]
    assert summary["n_unlensed_in_fit"] == 10
    assert summary["n_lensed_demagnified_in_fit"] == 0
    assert summary["n_lensed_skipped_no_mu"] == 4
    # papers_default_lensed must contain the SPT bibcode (paper-level fallback triggered)
    assert "2013ApJ...779...67B" in summary["papers_default_lensed"]
    # ALPINE is absent (it is no_lensing)
    assert "2020A&A...643A...2B" not in summary["papers_default_lensed"]


# ── PART AI #2: luminosity_kind L_prime conversion ───────────────────


def test_paper1_l_prime_unit_label_in_envelope(mixed_paper1_sample):
    """luminosity_kind="L_prime" → intercept_unit/slope_unit strings
    must explicitly state the brightness-temperature unit to prevent silent
    inconsistency when prose cites alpha."""
    from app.services.ai_tools import _exec_fit_line_lfr

    with _patch_cache(mixed_paper1_sample):
        out = _exec_fit_line_lfr({
            "cache_key": "x",
            "luminosity_kind": "L_prime",
        })

    assert out["luminosity_kind"] == "L_prime"
    assert out["intercept_unit"] == "log10(L_prime/(K km/s pc^2))"
    assert "log_L per log10(FWHM/100" in out["slope_unit"]
    # Note: unit conversion happens before lensing-reject — 14 rows (10 ALPINE + 4 SPT)
    # all attempt conversion, then the lensing gate removes the 4 SPT rows from fit.
    # Therefore n_unit_converted=14, n_used=10.
    assert out["n_unit_converted"] == 14
    assert out["n_used"] == 10
    assert out["unit_conversion_failures"] == []
    assert "L_prime" in out["model"]
    assert "K km/s pc^2" in out["model"]


def test_paper1_l_prime_alpha_shift_above_2_dex(mixed_paper1_sample):
    """ALPINE z=4.4-5.8 sample: L_prime fit alpha should exceed L_solar fit
    by approximately +2 dex (single-source +2.215 dex at z=5, OLS intercept spread
    1.9-2.3 dex range)."""
    from app.services.ai_tools import _exec_fit_line_lfr

    with _patch_cache(mixed_paper1_sample):
        out_solar = _exec_fit_line_lfr({"cache_key": "x"})  # default L_solar
        out_prime = _exec_fit_line_lfr({
            "cache_key": "x",
            "luminosity_kind": "L_prime",
        })

    delta_alpha = out_prime["alpha"] - out_solar["alpha"]
    # Note: ALPINE z=4.4-5.8 sample mean (1+z)^2 term is not exactly the z=5 single-point +2.215;
    # OLS intercept spread 1.7-2.3 dex is normal — what matters is sign + order of magnitude.
    assert 1.7 < delta_alpha < 2.4, (
        f"expected +1.7~2.4 dex shift, got {delta_alpha:.3f}"
    )
    # beta also changes (unit conversion is not a rigid translation), but the sign stays positive
    assert out_prime["beta"] > 0
    assert out_solar["beta"] > 0


# ── Existing path: cosmology recompute via Riess22 ───────────────────


def test_paper1_cosmology_recompute_with_riess22(mixed_paper1_sample):
    """Passing cosmology="riess22_shoes" → result.cosmology_recomputed=True
    + dl_shift_summary contains a non-zero shift."""
    from app.services.ai_tools import _exec_fit_line_lfr

    with _patch_cache(mixed_paper1_sample):
        out = _exec_fit_line_lfr({
            "cache_key": "x",
            "luminosity_kind": "L_prime",
            "cosmology": "riess22_shoes",
        })

    assert out["cosmology_recomputed"] is True
    # Note: result.cosmology_used is a string (name); the full manifest is in
    # result.cosmology_manifest dict
    assert out["cosmology_used"] == "riess22_shoes"
    manifest = out["cosmology_manifest"]
    assert isinstance(manifest, dict)
    assert abs(manifest.get("H0_km_s_Mpc", 0) - 73.04) < 0.1

    shift = out["dl_shift_summary"]
    assert shift is not None
    # H0=73.04 vs Planck18 baseline 67.36, dl shrinks ~7.8%, log shift ~−0.07
    assert abs(shift["median_log_l_shift_dex"]) > 0.001
    assert shift["target_cosmology"] == "riess22_shoes"


# ── PART AI #4: readiness checks 5 gates ─────────────────────────────


def test_paper1_readiness_checks_5_gates(mixed_paper1_sample):
    """result must contain readiness_checks dict + 5 gates (minimum_rows /
    citations / confirmed_luminosity_units / method_not_downgraded /
    bayesian_sampler when bayesian path is triggered)."""
    from app.services.ai_tools import _exec_fit_line_lfr

    with _patch_cache(mixed_paper1_sample):
        out = _exec_fit_line_lfr({
            "cache_key": "x",
            "luminosity_kind": "L_prime",
        })

    readiness = out["publication_readiness"]
    assert "checks" in readiness
    checks = readiness["checks"]
    for gate in ("minimum_rows", "citations", "confirmed_luminosity_units",
                 "method_not_downgraded"):
        assert gate in checks
        assert "passed" in checks[gate]


def test_paper1_relation_claimability_present(mixed_paper1_sample):
    """relation_claimability dict must contain can_claim_relation +
    claim_scope + blocking_reasons."""
    from app.services.ai_tools import _exec_fit_line_lfr

    with _patch_cache(mixed_paper1_sample):
        out = _exec_fit_line_lfr({
            "cache_key": "x",
            "luminosity_kind": "L_prime",
        })

    claim = out["relation_claimability"]
    assert "can_claim_relation" in claim
    assert "claim_scope" in claim
    assert "blocking_reasons" in claim
    assert claim["claim_scope"] in {
        "publication_ready_relation",
        "method_mismatch",
        "exploratory_only",
    }


# ── Bayesian path: Kelly 2007 linmix ─────────────────────────────────


def test_paper1_bayesian_summary_with_kelly07_reference(
    mixed_paper1_sample, monkeypatch,
):
    """fit_method_requested="bayesian_xyerr" + all errors provided → bayesian_summary
    contains Kelly 2007 reference + linmix package + per-param ess/rhat/hdi.

    Sampler is mocked so test runs in ms, not minutes."""
    fake_bayes = {
        "method": "bayesian_xyerr_linmix",
        "alpha_median": 10.6,
        "alpha_hdi_94": [10.4, 10.8],
        "beta_median": 0.85,
        "beta_hdi_94": [0.55, 1.15],
        "intrinsic_scatter_dex": 0.32,
        "intrinsic_scatter_dex_hdi": [0.26, 0.40],
        "parameters": {
            "alpha": {"mean": 10.6, "median": 10.6, "std": 0.10,
                      "hdi_low_94": 10.4, "hdi_high_94": 10.8, "ess": 1200,
                      "rhat": 1.001},
            "beta": {"mean": 0.85, "median": 0.85, "std": 0.15,
                     "hdi_low_94": 0.55, "hdi_high_94": 1.15, "ess": 950,
                     "rhat": 1.003},
            "sigma_int": {"mean": 0.32, "median": 0.32, "std": 0.04,
                          "hdi_low_94": 0.26, "hdi_high_94": 0.40,
                          "ess": 1100, "rhat": 1.002},
        },
        "n_draws_total": 16000,
        "n_chains": 4,
        "miniter": 4000,
        "maxiter": 20000,
        "K": 2,
        "converged": True,
        "publication_ready": True,
        "package": "linmix (vendored, jmeyers314, BSD-2)",
        "reference": "Kelly 2007, ApJ 665, 1489 (arXiv:0705.2774)",
    }
    import app.services.bayesian_inference as bi
    monkeypatch.setattr(bi, "kelly07_linmix_fit", lambda **kw: fake_bayes)

    from app.services.ai_tools import _exec_fit_line_lfr

    with _patch_cache(mixed_paper1_sample):
        out = _exec_fit_line_lfr({
            "cache_key": "x",
            "luminosity_kind": "L_prime",
            "fit_method_requested": "bayesian_xyerr",
        })

    assert out["fit_method"] == "bayesian_xyerr_linmix"
    bayes = out["bayesian_summary"]
    assert "Kelly 2007" in bayes["reference"]
    assert "linmix" in bayes["package"]
    # Per-param ess + rhat must be exposed
    for pname in ("alpha", "beta", "sigma_int"):
        p = bayes["parameters"][pname]
        assert p["ess"] >= 400
        assert 0.99 < p["rhat"] < 1.05


# ── PART AI #5: paper_generator render ───────────────────────────────


def test_paper1_paper_generator_renders_full_diagnostics_section(
    mixed_paper1_sample, monkeypatch,
):
    """build_fit_line_lfr_diagnostics_section must extract ALL PART AI fields
    from the fit_line_lfr result envelope and render them as a markdown section."""
    fake_bayes = {
        "method": "bayesian_xyerr_linmix",
        "alpha_median": 10.6, "alpha_hdi_94": [10.4, 10.8],
        "beta_median": 0.85, "beta_hdi_94": [0.55, 1.15],
        "intrinsic_scatter_dex": 0.32,
        "intrinsic_scatter_dex_hdi": [0.26, 0.40],
        "parameters": {
            "alpha": {"median": 10.6, "std": 0.10, "hdi_low_94": 10.4,
                      "hdi_high_94": 10.8, "ess": 1200, "rhat": 1.001},
            "beta": {"median": 0.85, "std": 0.15, "hdi_low_94": 0.55,
                     "hdi_high_94": 1.15, "ess": 950, "rhat": 1.003},
            "sigma_int": {"median": 0.32, "std": 0.04, "hdi_low_94": 0.26,
                          "hdi_high_94": 0.40, "ess": 1100, "rhat": 1.002},
        },
        "n_draws_total": 16000, "n_chains": 4, "K": 2,
        "converged": True, "publication_ready": True,
        "package": "linmix (vendored, jmeyers314, BSD-2)",
        "reference": "Kelly 2007, ApJ 665, 1489 (arXiv:0705.2774)",
    }
    import app.services.bayesian_inference as bi
    monkeypatch.setattr(bi, "kelly07_linmix_fit", lambda **kw: fake_bayes)

    from app.services.ai_tools import _exec_fit_line_lfr
    from app.services.paper_generator import (
        SessionArtifacts,
        build_fit_line_lfr_diagnostics_section,
    )

    with _patch_cache(mixed_paper1_sample):
        out = _exec_fit_line_lfr({
            "cache_key": "x",
            "luminosity_kind": "L_prime",
            "cosmology": "riess22_shoes",
            "fit_method_requested": "bayesian_xyerr",
        })
    out["tool"] = "fit_line_lfr"

    artifacts = SessionArtifacts(
        session=None,
        user_prompts=["Paper #1 [CII] LFR audit"],
        assistant_text=["fit complete"],
        search_calls=[],
        adql_calls=[],
        python_calls=[],
        pipeline_calls=[],
        figure_refs=[],
        tool_results=[out],
        provenance_datasets=[],
        provenance_runs=[],
        cosmology_runs=[],
        source_urls=[],
        bibcodes=[],
    )

    section = build_fit_line_lfr_diagnostics_section(artifacts)
    assert section is not None

    # PART AI #2 unit labels
    assert "L_prime/(K km/s pc^2)" in section
    # PART AI #5 Bayesian diagnostics
    assert "Kelly 2007" in section
    assert "linmix" in section
    # Per-param rhat / ess (Bayesian table)
    assert "rhat" in section.lower()
    assert "ess" in section.lower()
    # PART AI #4 readiness gates
    assert "minimum_rows" in section
    assert "method_not_downgraded" in section
    # PART AI #6 lensing 5-bucket
    assert "n_unlensed_in_fit: 10" in section
    assert "n_lensed_skipped_no_mu: 4" in section
    assert "2013ApJ...779...67B" in section  # SPT bibcode in
                                              # papers_default_lensed
    # Cosmology recompute audit
    assert "riess22_shoes" in section


# ── End-to-end summary: all PART AI checkpoints fire on a single call ──


def test_paper1_e2e_full_part_ai_invariants_in_one_call(mixed_paper1_sample, monkeypatch):
    """Single fit_line_lfr call produces a result envelope that
    satisfies every PART AI #2/#4/#5/#6 invariant simultaneously.
    This is the most compact "did the platform really do its job"
    regression."""
    fake_bayes = {
        "method": "bayesian_xyerr_linmix",
        "alpha_median": 10.6, "alpha_hdi_94": [10.4, 10.8],
        "beta_median": 0.85, "beta_hdi_94": [0.55, 1.15],
        "intrinsic_scatter_dex": 0.32,
        "intrinsic_scatter_dex_hdi": [0.26, 0.40],
        "parameters": {
            "alpha": {"median": 10.6, "ess": 1200, "rhat": 1.001,
                      "hdi_low_94": 10.4, "hdi_high_94": 10.8, "std": 0.1,
                      "mean": 10.6},
            "beta": {"median": 0.85, "ess": 950, "rhat": 1.003,
                     "hdi_low_94": 0.55, "hdi_high_94": 1.15, "std": 0.15,
                     "mean": 0.85},
            "sigma_int": {"median": 0.32, "ess": 1100, "rhat": 1.002,
                          "hdi_low_94": 0.26, "hdi_high_94": 0.40,
                          "std": 0.04, "mean": 0.32},
        },
        "n_draws_total": 16000, "n_chains": 4, "K": 2,
        "converged": True, "publication_ready": True,
        "package": "linmix (vendored, jmeyers314, BSD-2)",
        "reference": "Kelly 2007, ApJ 665, 1489 (arXiv:0705.2774)",
    }
    import app.services.bayesian_inference as bi
    monkeypatch.setattr(bi, "kelly07_linmix_fit", lambda **kw: fake_bayes)

    from app.services.ai_tools import _exec_fit_line_lfr

    with _patch_cache(mixed_paper1_sample):
        out = _exec_fit_line_lfr({
            "cache_key": "paper1_audit",
            "luminosity_kind": "L_prime",
            "cosmology": "riess22_shoes",
            "fit_method_requested": "bayesian_xyerr",
        })

    # PART AI #2 — units
    assert out["luminosity_kind"] == "L_prime"
    assert out["intercept_unit"] == "log10(L_prime/(K km/s pc^2))"

    # PART AI #4 — readiness + claimability dicts present
    # publication_readiness.checks is a 5-gate dict (minimum_rows / citations
    # / confirmed_luminosity_units / method_not_downgraded / bayesian_sampler)
    assert "checks" in out["publication_readiness"]
    assert isinstance(out["publication_readiness"]["checks"], dict)
    assert out["relation_claimability"]["claim_scope"] in {
        "publication_ready_relation", "method_mismatch", "exploratory_only",
    }

    # PART AI #5 — Bayesian diagnostics
    assert out["fit_method"] == "bayesian_xyerr_linmix"
    bayes = out["bayesian_summary"]
    assert "Kelly 2007" in bayes["reference"]
    assert all(
        p["ess"] >= 400 and p["rhat"] < 1.05
        for p in bayes["parameters"].values()
    )

    # PART AI #6 — lensing coverage gate
    summary = out["lensing_summary"]
    assert summary["n_unlensed_in_fit"] == 10
    assert summary["n_lensed_skipped_no_mu"] == 4
    assert "2013ApJ...779...67B" in summary["papers_default_lensed"]

    # Existing path — cosmology recompute applied
    assert out["cosmology_recomputed"] is True
    assert out["cosmology_used"] == "riess22_shoes"
    assert out["cosmology_manifest"]["name"] == "riess22_shoes"
