"""PART AI #5/#6 — paper_generator fit_line_lfr 渲染段.

锁住 4 个契约:
1. 没跑 fit_line_lfr → helper 返 None, paper_json 不含 fit_line_lfr_diagnostics 字段.
2. 跑了 fit (含 readiness/claimability/Bayesian/lensing 全字段) → 渲染段
   含每个字段的关键值, 防 LaTeX 段悄悄漏掉某条诊断.
3. PARTIAL fit → 渲染段必须含 'exploratory_only' / blocking_reasons 关键字.
4. 跨 cosmology recompute → 渲染段含 cosmology_used / baseline / shift_summary.
"""

from __future__ import annotations

from app.services.paper_generator import (
    SessionArtifacts,
    build_fit_line_lfr_diagnostics_section,
    _build_default_paper_json,
)


def _make_artifacts(tool_results: list[dict]) -> SessionArtifacts:
    return SessionArtifacts(
        session=None,
        user_prompts=["Test fit_line_lfr render"],
        assistant_text=["assistant said something"],
        search_calls=[],
        adql_calls=[],
        python_calls=[],
        pipeline_calls=[],
        figure_refs=[],
        tool_results=tool_results,
        provenance_datasets=[],
        provenance_runs=[],
        cosmology_runs=[],
        source_urls=[],
        bibcodes=[],
    )


# ── 契约 1: 没 fit_line_lfr → 返 None + paper_json 不带字段 ─────────


def test_no_fit_line_lfr_returns_none() -> None:
    artifacts = _make_artifacts([
        {"tool": "search_objects", "results": []},
        {"tool": "run_python", "success": True},
    ])
    assert build_fit_line_lfr_diagnostics_section(artifacts) is None


def test_paper_json_omits_fit_lfr_key_when_no_fit() -> None:
    artifacts = _make_artifacts([])
    paper = _build_default_paper_json(artifacts, "aastex")
    assert "fit_line_lfr_diagnostics" not in paper


def test_failed_fit_line_lfr_returns_none() -> None:
    """success=False fit 也算没跑成功 → 不渲染."""
    artifacts = _make_artifacts([
        {"tool": "fit_line_lfr", "success": False, "error": "no rows"},
    ])
    assert build_fit_line_lfr_diagnostics_section(artifacts) is None


# ── 契约 2: 完整 fit envelope 渲染所有关键字段 ──────────────────────


def _full_fit_envelope() -> dict:
    """模拟 codex commit 6be015c 之后的完整 fit_line_lfr 返回结构."""
    return {
        "tool": "fit_line_lfr",
        "success": True,
        "line_id": "[CII]",
        "n_used": 74,
        "n_surveys_merged": 3,
        "contributing_cache_keys": ["alpine", "rebels", "spt"],
        "model": "log10(L_prime/(K km/s pc^2)) = alpha + beta * log10(FWHM_km_s / 100)",
        "intercept_unit": "log10(L_prime/(K km/s pc^2))",
        "slope_unit": "dex per dex (log_L per log10(FWHM/100 km/s))",
        "luminosity_kind": "L_prime",
        "n_unit_converted": 74,
        "fit_method": "bayesian_xyerr_linmix",
        "fit_method_requested": "bayesian_xyerr",
        "fit_method_downgrade_reason": None,
        "alpha": 8.36,
        "alpha_stderr": 0.09,
        "beta": 0.79,
        "beta_stderr": 0.20,
        "residual_rms_dex": 0.32,
        "intrinsic_scatter_dex": 0.32,
        "intrinsic_scatter_dex_hdi": [0.26, 0.39],
        "pearson_r": 0.55,
        "spearman_r": 0.51,
        "bayesian_summary": {
            "method": "bayesian_xyerr_linmix",
            "package": "linmix (vendored, jmeyers314, BSD-2)",
            "reference": "Kelly 2007, ApJ 665, 1489 (arXiv:0705.2774)",
            "n_chains": 4,
            "n_draws_total": 16000,
            "K": 2,
            "converged": True,
            "publication_ready": True,
            "parameters": {
                "alpha": {"median": 8.36, "std": 0.09, "hdi_low_94": 8.20,
                          "hdi_high_94": 8.52, "ess": 1200, "rhat": 1.001},
                "beta":  {"median": 0.79, "std": 0.20, "hdi_low_94": 0.40,
                          "hdi_high_94": 1.18, "ess": 950, "rhat": 1.003},
                "sigma_int": {"median": 0.32, "std": 0.04, "hdi_low_94": 0.26,
                              "hdi_high_94": 0.39, "ess": 1100, "rhat": 1.002},
            },
        },
        "publication_readiness": {
            "status": "publication_ready_relation",
            "data_checks_publication_ready": True,
            "checks": {
                "minimum_rows":           {"passed": True, "n_used": 74, "required": 5},
                "citations":              {"passed": True, "rows_with_citations": 74, "n_used": 74},
                "confirmed_luminosity_units": {"passed": True, "value_range_inferred_rows": 0, "requires_header_or_caption_log_units": True},
                "method_not_downgraded":  {"passed": True, "fit_method_requested": "bayesian_xyerr", "fit_method": "bayesian_xyerr_linmix", "reason": None},
                "bayesian_sampler":       {"passed": True, "converged": True, "publication_ready": True, "error": None},
            },
        },
        "relation_claimability": {
            "can_claim_relation": True,
            "claim_scope": "publication_ready_relation",
            "blocking_reasons": [],
        },
        "lensing_summary": {
            "n_unlensed_in_fit": 65,
            "n_lensed_demagnified_in_fit": 9,
            "n_lensed_skipped_no_mu": 0,
            "n_lensed_unknown": 0,
            "papers_default_lensed": ["2013ApJ...779...67B", "2015Natur.522..455C"],
        },
        "cosmology_recomputed": True,
        "cosmology_used": {"name": "riess22_shoes", "H0_km_s_Mpc": 73.04, "Om0": 0.3},
        "cosmology_baseline": {"name": "planck18", "H0_km_s_Mpc": 67.4, "Om0": 0.315},
        "dl_shift_summary": {
            "median_log_l_shift_dex": -0.014,
            "max_abs_log_l_shift_dex": 0.018,
            "n_rows_shifted": 74,
            "baseline_cosmology": "planck18",
            "target_cosmology": "riess22_shoes",
        },
    }


def test_full_envelope_renders_all_critical_fields() -> None:
    artifacts = _make_artifacts([_full_fit_envelope()])
    section = build_fit_line_lfr_diagnostics_section(artifacts)
    assert section is not None

    # 标题
    assert "Fit Diagnostics: line luminosity-FWHM relation" in section

    # Sample composition
    assert "74 rows" in section
    assert "[CII]" in section
    assert "3 surveys merged" in section

    # Model / units
    assert "L_prime/(K km/s pc^2)" in section
    assert "luminosity_kind" in section
    assert "n_rows_unit_converted: 74" in section

    # Best-fit values
    assert "fit_method:" in section
    assert "bayesian_xyerr_linmix" in section
    assert "8.36" in section  # alpha
    assert "0.79" in section  # beta
    assert "0.32" in section  # scatter / residual_rms

    # Bayesian
    assert "Kelly 2007" in section
    assert "linmix" in section
    assert "n_chains: 4" in section
    assert "n_draws_total: 16000" in section
    assert "rhat" in section.lower()
    assert "1.001" in section or "1.002" in section or "1.003" in section
    assert "ess" in section.lower()
    assert "1200" in section or "1100" in section or "950" in section

    # Readiness gates (5 gates)
    assert "minimum_rows" in section
    assert "citations" in section
    assert "confirmed_luminosity_units" in section
    assert "method_not_downgraded" in section
    assert "bayesian_sampler" in section
    # 全过 5 gates 应该都打 ✓
    assert section.count("✓") >= 5

    # Claim scope
    assert "can_claim_relation: True" in section
    assert "publication_ready_relation" in section

    # Lensing summary (5 buckets)
    assert "n_unlensed_in_fit: 65" in section
    assert "n_lensed_demagnified_in_fit: 9" in section
    assert "n_lensed_skipped_no_mu: 0" in section
    assert "papers_default_lensed:" in section
    assert "2013ApJ...779...67B" in section  # SPT bibcode
    assert "2015Natur.522..455C" in section  # Capak bibcode

    # Cosmology recompute
    assert "Cosmology recomputation" in section
    assert "riess22_shoes" in section
    assert "73.04" in section
    assert "planck18" in section


def test_paper_json_includes_fit_lfr_diagnostics_when_fit_succeeded() -> None:
    artifacts = _make_artifacts([_full_fit_envelope()])
    paper = _build_default_paper_json(artifacts, "aastex")
    assert "fit_line_lfr_diagnostics" in paper
    assert "Fit Diagnostics" in paper["fit_line_lfr_diagnostics"]


# ── 契约 3: PARTIAL fit 渲染段必须含 exploratory_only + blocking_reasons ──


def test_partial_fit_renders_exploratory_scope_and_blockers() -> None:
    """fit_line_lfr=PARTIAL → relation_claimability.claim_scope=exploratory_only,
    blocking_reasons 非空; helper 必须把这些原文渲染."""
    envelope = _full_fit_envelope()
    envelope["publication_readiness"] = {
        "status": "exploratory_only",
        "data_checks_publication_ready": False,
        "checks": {
            "minimum_rows":           {"passed": True, "n_used": 74, "required": 5},
            "citations":              {"passed": False, "rows_with_citations": 30, "n_used": 74},
            "confirmed_luminosity_units": {"passed": False, "value_range_inferred_rows": 30, "requires_header_or_caption_log_units": True},
            "method_not_downgraded":  {"passed": True, "fit_method_requested": "bayesian_xyerr", "fit_method": "bayesian_xyerr_linmix", "reason": None},
            "bayesian_sampler":       {"passed": True, "converged": True, "publication_ready": True, "error": None},
        },
    }
    envelope["relation_claimability"] = {
        "can_claim_relation": False,
        "claim_scope": "exploratory_only",
        "blocking_reasons": ["incomplete_citations", "unconfirmed_luminosity_units"],
    }

    artifacts = _make_artifacts([envelope])
    section = build_fit_line_lfr_diagnostics_section(artifacts)
    assert section is not None

    assert "exploratory_only" in section
    assert "can_claim_relation: False" in section
    assert "incomplete_citations" in section
    assert "unconfirmed_luminosity_units" in section
    # 失败 gate 必须打 ✗
    assert section.count("✗") >= 2


# ── 契约 4: 多 fit 时只取最新 ────────────────────────────────────────


def test_only_latest_fit_is_rendered() -> None:
    """同 session 多次调 fit_line_lfr 时, helper 取最新一次成功结果."""
    older = _full_fit_envelope()
    older["alpha"] = 7.123  # 旧 fit intercept (用 unique 值避开其它字段干扰)
    older["beta"] = 0.456

    newer = _full_fit_envelope()
    newer["alpha"] = 9.876
    newer["beta"] = 0.987

    artifacts = _make_artifacts([older, newer])
    section = build_fit_line_lfr_diagnostics_section(artifacts)
    assert section is not None
    # 新 fit 的 alpha/beta 必须出现
    assert "9.876" in section
    assert "0.987" in section
    # 旧 fit 的 alpha/beta 不应该出现 (helper 只渲染最新一次)
    assert "7.123" not in section
    assert "0.456" not in section
