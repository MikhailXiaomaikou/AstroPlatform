"""Tests for the zero-fabrication gate (Phase 1 / R2).

Locks the contract that any numeric astronomical claim in an AI reply is
verified against the turn's tool_results, and uncited claims are flagged
so the agent loop can regenerate or block the reply.
"""

from __future__ import annotations

import pytest

from app.services.claim_validator import (
    blocked_reply_text,
    blocked_line_relation_reply_text,
    build_regeneration_prompt,
    extract_claims,
    line_relation_stat_claims,
    measurement_data_available,
    unsupported_line_relation_stat_claims,
    validate_claims,
)


# -------------------- extract_claims --------------------


def test_extract_redshift_claim():
    claims = extract_claims("The galaxy has z = 0.032 and some nice features.")
    assert any(c.label == "redshift_z" and c.value == pytest.approx(0.032) for c in claims)


def test_extract_multiple_patterns():
    txt = "We find T_eff = 5800 K, log g = 4.2, and age 4.5 Gyr."
    claims = extract_claims(txt)
    labels = {c.label for c in claims}
    assert {"teff_k", "log_g", "age_gyr"} <= labels


def test_extract_scientific_notation():
    claims = extract_claims("parallax of 1.2e-3 mas")
    assert any(c.value == pytest.approx(1.2e-3) for c in claims)


def test_extract_skips_text_without_numbers():
    assert extract_claims("This galaxy is beautiful.") == []


def test_extract_exoplanet_radius_ratio_claims():
    claims = extract_claims("综合估计 Rp/Rs 约为 0.157。")
    assert any(c.label == "radius_ratio" and c.value == pytest.approx(0.157) for c in claims)


def test_extract_cosmology_parameter_claims():
    claims = extract_claims("We find H0 = 70 km/s/Mpc, Om0 = 0.31, w0 = -1.1, wa = 0.2.")
    labels = {c.label for c in claims}
    assert {"cosmology_h0", "cosmology_om0", "cosmology_w0", "cosmology_wa"} <= labels


# -------------------- validate_claims --------------------


def test_validate_ok_when_value_matches_tool_result():
    tool_results = [
        {"tool": "run_adql", "input": {}, "result": {"parallax": 7.50}},
    ]
    r = validate_claims("The parallax is 7.50 mas.", tool_results)
    assert r.ok
    assert r.uncited == []


def test_validate_flags_fabricated_number():
    tool_results = [
        {"tool": "run_adql", "input": {}, "result": {"parallax": 7.50}},
    ]
    r = validate_claims("The parallax is 9.00 mas.", tool_results)
    assert not r.ok
    assert len(r.uncited) == 1
    assert r.uncited[0].label == "parallax_mas"


def test_cosmology_claims_require_publication_ready_mcmc_result():
    tool_results = [
        {
            "tool": "fit_cosmology_mcmc",
            "result": {
                "success": True,
                "__tool_status__": "PARTIAL",
                "__do_not_claim__": True,
                "publication_ready": False,
                "parameters": {
                    "H0": {"median": 70.0},
                    "w0": {"median": -1.1},
                },
            },
        }
    ]
    r = validate_claims("The posterior gives H0 = 70 km/s/Mpc and w0 = -1.1.", tool_results)
    assert not r.ok
    assert {claim.label for claim in r.uncited} >= {"cosmology_h0", "cosmology_w0"}


def test_cosmology_claims_pass_with_publication_ready_mcmc_result():
    tool_results = [
        {
            "tool": "fit_cosmology_mcmc",
            "result": {
                "success": True,
                "publication_ready": True,
                "parameters": {
                    "H0": {"median": 70.0},
                    "w0": {"median": -1.1},
                },
            },
        }
    ]
    r = validate_claims("The posterior gives H0 = 70 km/s/Mpc and w0 = -1.1.", tool_results)
    assert r.ok


def test_line_relation_stats_blocked_when_measurement_workflow_has_zero_rows():
    tool_results = [
        {
            "tool": "search_literature",
            "result": {
                "success": True,
                "results": [{"title": "ALPINE survey", "bibcode": "2020A&A...643A...2B"}],
            },
        },
        {
            "tool": "extract_literature_tables",
            "result": {
                "success": True,
                "tables": [],
                "line_measurements": [],
                "line_measurement_count": 0,
                "__tool_status__": "EMPTY",
            },
        },
        {
            "tool": "fit_line_lfr",
            "result": {
                "success": True,
                "__tool_status__": "PARTIAL",
                "__do_not_claim__": True,
                "publication_ready": False,
                "n_used": 0,
            },
        },
        {
            "tool": "run_python",
            "result": {
                "success": False,
                "error_class": "SubprocessCrash",
                "error": "run_python returned an empty response (tool response malformed; check backend logs)",
                "stdout": "",
                "stderr": "",
            },
        },
    ]
    reply = (
        "The relation is FWHM = (128 ± 39) × log L'[CII] + (-831 ± 339) km/s.\n"
        "Intrinsic scatter = 60 ± 20 km/s. Pearson r = 0.338, p = 3.2e-3."
    )

    claims = unsupported_line_relation_stat_claims(reply, tool_results)

    assert claims
    assert {claim.label for claim in claims} >= {
        "line_relation_equation",
        "line_relation_scatter",
        "line_relation_correlation",
        "line_relation_p_value",
    }
    blocked = blocked_line_relation_reply_text(claims)
    assert "publication-ready measurement-table fit" in blocked
    assert "search_literature" not in blocked
    assert "fit_line_lfr" not in blocked


def test_search_literature_abstracts_do_not_support_line_relation_stats():
    tool_results = [{
        "tool": "search_literature",
        "result": {
            "success": True,
            "results": [{"title": "A [CII] survey", "bibcode": "2022MNRAS.517.2508W"}],
        },
    }]

    claims = unsupported_line_relation_stat_claims(
        "Slope beta = 0.21, intercept alpha = 0.0, Spearman rho = 0.52.",
        tool_results,
    )

    assert claims
    assert not measurement_data_available(tool_results, domain="line_lfr", require_fit=True)


def test_publication_ready_line_fit_supports_relation_stats():
    tool_results = [{
        "tool": "fit_line_lfr",
        "result": {
            "success": True,
            "publication_ready": True,
            "n_used": 74,
            "alpha": 8.0,
            "beta": 0.5,
            "pearson_r": 0.92,
            "pearson_p": 0.001,
            "scatter_dex": 0.12,
        },
    }]

    assert measurement_data_available(tool_results, domain="line_lfr", require_fit=True)
    assert unsupported_line_relation_stat_claims(
        "Slope beta = 0.50, intercept alpha = 8.0, Pearson r = 0.92, p = 0.001.",
        tool_results,
    ) == []


def test_cited_run_python_mcmc_supports_relation_stats():
    tool_results = [{
        "tool": "run_python",
        "input": {"data_source": "cached:latest_literature_tables:82fdb062"},
        "result": {
            "success": True,
            "data_origin": "cached_real",
            "stdout": (
                "Bayesian Linear Regression for the [CII] line relation.\n"
                "alpha/intercept = 9.823 +/- 0.467\n"
                "beta/slope = 0.766 +/- 0.188\n"
                "σ_int = 0.315 dex\n"
                "Citation: Bethermin et al. (2020; arXiv:2002.00962)"
            ),
            "variables": {
                "method": "emcee MCMC",
                "slope": 0.766,
                "intercept": 9.823,
                "citation": "arXiv:2002.00962",
            },
        },
    }]

    assert measurement_data_available(tool_results, domain="line_lfr", require_fit=True)
    assert unsupported_line_relation_stat_claims(
        "Slope beta = 0.766, intercept alpha = 9.823, intrinsic scatter = 0.315 dex.",
        tool_results,
    ) == []


def test_run_python_line_fit_without_citation_does_not_support_stats():
    tool_results = [{
        "tool": "run_python",
        "input": {"data_source": "cached:latest_literature_tables:82fdb062"},
        "result": {
            "success": True,
            "data_origin": "cached_real",
            "stdout": (
                "Bayesian [CII] MCMC fit: slope = 0.766, "
                "intercept = 9.823, intrinsic scatter = 0.315 dex."
            ),
        },
    }]

    claims = unsupported_line_relation_stat_claims("Slope beta = 0.766.", tool_results)

    assert claims
    assert not measurement_data_available(tool_results, domain="line_lfr", require_fit=True)


def test_synthetic_run_python_line_fit_does_not_support_stats():
    tool_results = [{
        "tool": "run_python",
        "input": {"data_source": "none_not_analyzing_real_data"},
        "result": {
            "success": True,
            "__tool_status__": "SYNTHETIC",
            "__do_not_claim__": True,
            "data_origin": "synthetic",
            "stdout": (
                "Fake [CII] relation: slope = 0.766, intercept = 9.823, "
                "intrinsic scatter = 0.315 dex. Citation: arXiv:2002.00962"
            ),
        },
    }]

    claims = unsupported_line_relation_stat_claims("Slope beta = 0.766.", tool_results)

    assert claims
    assert not measurement_data_available(tool_results, domain="line_lfr", require_fit=True)


def test_non_publication_ready_line_fit_does_not_support_headline_stats():
    tool_results = [{
        "tool": "fit_line_lfr",
        "result": {
            "success": True,
            "__tool_status__": "PARTIAL",
            "__do_not_claim__": True,
            "publication_ready": False,
            "n_used": 3,
            "beta": 0.5,
        },
    }]

    claims = unsupported_line_relation_stat_claims("Slope beta = 0.50.", tool_results)

    assert claims


def test_line_relation_stat_extractor_catches_r2_5_shapes():
    claims = line_relation_stat_claims(
        "FWHM = (128 ± 39) × log L'[CII] + (-831 ± 339) km/s; "
        "intrinsic scatter = 60 ± 20 km/s; Pearson r = 0.338; "
        "Spearman ρ = 0.356; p-value = 3.2e-3."
    )
    labels = {claim.label for claim in claims}

    assert {
        "line_relation_equation",
        "line_relation_scatter",
        "line_relation_correlation",
        "line_relation_p_value",
    } <= labels


def test_lowercase_relation_r_is_not_parsed_as_band_magnitude():
    claims = extract_claims("The relation has Pearson r ≈ 0.45.")

    assert all(claim.label != "magnitude" for claim in claims)


def test_cosmology_prior_bounds_do_not_support_posterior_claims():
    tool_results = [
        {
            "tool": "fit_cosmology_mcmc",
            "result": {
                "success": True,
                "publication_ready": True,
                "parameters": {
                    "H0": {"median": 70.0},
                    "Om0": {"median": 0.31},
                },
                "priors": {
                    "H0": [50.0, 90.0],
                    "Om0": [0.05, 0.6],
                },
                "chain_diagnostics": {
                    "thresholds": {"ess_min": 400.0, "rhat_max": 1.05},
                },
            },
        }
    ]
    r = validate_claims("The posterior gives H0 = 50 km/s/Mpc and Om0 = 0.05.", tool_results)
    assert not r.ok
    assert {claim.label for claim in r.uncited} >= {"cosmology_h0", "cosmology_om0"}


def test_validate_flags_uncited_radius_ratio():
    r = validate_claims("The planet-to-star radius ratio is 0.157.", [])
    assert not r.ok
    assert any(c.label == "radius_ratio" and c.value == pytest.approx(0.157) for c in r.uncited)


def test_extract_chinese_period_and_percent_claims():
    claims = extract_claims("周期值为 5.366154 天，距离估算约260 pc，误差 5.2%。")
    assert any(c.label == "period_days_zh" and c.value == pytest.approx(5.366154) for c in claims)
    assert any(c.value == pytest.approx(260.0) for c in claims)
    assert any(c.label == "percent_claim" and c.value == pytest.approx(5.2) for c in claims)


def test_validate_tolerance_accepts_rounded_value():
    """7.504 formatted as 7.50 must count as a match under the 1 % tolerance."""
    tool_results = [{"result": {"parallax": 7.504}}]
    r = validate_claims("The parallax is 7.50 mas.", tool_results)
    assert r.ok


def test_validate_universe_walks_nested_payload():
    tool_results = {
        "tool": "get_object_dossier",
        "result": {
            "photometry": {"g_mag": 12.34, "bp_rp": 0.88},
            "motion": [{"pmra": -2.1, "pmdec": 4.9}],
        },
    }
    r = validate_claims("pmra is -2.1 mas and g = 12.34 mag.", tool_results)
    assert r.ok


def test_validate_text_inside_string_values_counts():
    """Numbers embedded in stringified CSV rows still satisfy citation."""
    tool_results = [{"result": {"csv_preview": "name,z\nM31,-0.001\n"}}]
    r = validate_claims("z = -0.001", tool_results)
    assert r.ok


def test_validate_returns_multiple_uncited():
    tool_results = [{"result": {"parallax": 5.0}}]
    r = validate_claims(
        "The log g = 4.4 and [Fe/H] = -0.2 and age 5 Gyr.",
        tool_results,
    )
    assert not r.ok
    assert len(r.uncited) >= 2


def test_schema_numbers_inside_markdown_code_are_not_claims():
    text = (
        "Available tool schema:\n"
        "```json\n"
        '{"limit": 24.0, "radius": 4.0, "max_rows": 70.0}\n'
        "```\n"
        "Use `SELECT TOP 1000 objID FROM PhotoObjAll` for examples."
    )
    claims = extract_claims(text)
    values = {c.value for c in claims}
    assert 24.0 not in values
    assert 4.0 not in values
    assert 70.0 not in values
    assert 1000.0 not in values


def test_spelled_out_summary_numbers_are_claims():
    """R20: 'three point zero' 这种英文拼写数字不能绕过 gate."""
    claims = extract_claims("The synthetic run said the mean was three point zero.")
    assert any(c.label == "spelled_number" and c.value == pytest.approx(3.0) for c in claims)


def test_spelled_out_uncited_number_is_blocked():
    r = validate_claims(
        "The mean was three point zero.",
        [{"result": {"__tool_status__": "SYNTHETIC", "__do_not_claim__": True, "stdout": "mean=3.0"}}],
    )
    assert not r.ok
    assert any(c.value == pytest.approx(3.0) for c in r.uncited)


# -------------------- Prompts & blocked text --------------------


def test_regeneration_prompt_lists_uncited_values():
    r = validate_claims("z = 3.14", [{"result": {"unrelated": 0.02}}])
    assert not r.ok
    prompt = build_regeneration_prompt(r)
    assert "3.14" in prompt
    assert "not determined by my tools" in prompt.lower()


def test_blocked_reply_text_is_user_friendly():
    r = validate_claims("z = 9.99", [{"result": {}}])
    assert not r.ok
    text = blocked_reply_text(r)
    assert "withheld" in text.lower()
    assert "rephrase" in text.lower()


# -------------------- F1.1: Pleiades fabrication regression --------------------
#
# The Pleiades reviewer saw the AI invent these numbers despite every tool
# call that turn returning 0 rows or an error:
#   "Member Star Count: 776 stars"
#   "Mean Parallax: 7.353 ± 0.001 mas (weighted mean)"
#   "Distance: 136.0 ± 0.0 pc"
# Every one of these must now be extracted AND flagged as uncited when the
# tool-results universe is empty.

PLEIADES_REPLY = (
    "Member Star Count: 776 stars\n"
    "Mean Parallax: 7.353 ± 0.001 mas (weighted mean)\n"
    "Distance: 136.0 ± 0.0 pc\n"
    "Literature comparison: Excellent agreement."
)


def test_pleiades_regex_extracts_labelled_colon_form():
    claims = extract_claims(PLEIADES_REPLY)
    values = {c.value for c in claims}
    assert 776.0 in values, f"776 (member count) should be extracted; got {values}"
    assert 7.353 in values, f"7.353 (mean parallax) should be extracted; got {values}"
    assert 0.001 in values, f"0.001 (parallax err) should be extracted; got {values}"
    assert 136.0 in values, f"136.0 (distance) should be extracted; got {values}"


def test_pleiades_count_with_noun_captures_776():
    claims = extract_claims("We found 776 member stars in the cluster.")
    assert any(c.value == 776.0 and c.label == "count_with_noun" for c in claims)


def test_pleiades_uncertainty_pair_extracts_both_value_and_err():
    claims = extract_claims("π = 7.353 ± 0.001 mas")
    values = {c.value for c in claims}
    assert 7.353 in values
    assert 0.001 in values


def test_pleiades_empty_tool_results_flags_all_claims():
    """The exact bug: all tool calls this turn failed/returned 0 rows, AI
    still wrote the full Pleiades paragraph.  Every fabricated number
    must now be flagged."""
    tool_results = [
        {"tool": "run_adql", "input": {}, "result": {"row_count": 0, "rows": []}},
        {"tool": "run_python", "input": {}, "result": {"success": False, "error": "crashed"}},
    ]
    r = validate_claims(PLEIADES_REPLY, tool_results)
    assert not r.ok
    uncited_values = {c.value for c in r.uncited}
    assert 776.0 in uncited_values
    assert 7.353 in uncited_values
    assert 136.0 in uncited_values


def test_strict_mode_rejects_accidental_index_match():
    """F1.3: with a thin tool universe (only row_count=0 and a few
    indices), a value of 776 should NOT match 775 even though 775 is
    within 1% — under strict mode tolerance is 0.1%."""
    tool_results = [{"result": {"row_count": 775}}]  # thin universe
    r = validate_claims("We found 776 stars.", tool_results)
    # Under normal 1% tolerance, 776 would match 775 (diff = 0.13%).
    # Under strict mode (<10 universe entries → 0.1% tol), it must fail.
    assert not r.ok, "strict mode should reject 776 vs 775 (0.13% diff > 0.1%)"
    assert r.strict_mode is True


def test_strict_mode_off_with_rich_universe():
    """When tool_results have >=10 distinct values, normal 1% tolerance
    applies (not strict)."""
    tool_results = [{"result": {f"col_{i}": float(i) + 0.5 for i in range(20)}}]
    r = validate_claims("z = 0.5", tool_results)
    assert r.strict_mode is False


# -------------------- F1.4: zero-data hard block --------------------


def test_is_empty_turn_with_0_rows_adql():
    from app.services.claim_validator import is_empty_turn

    assert is_empty_turn([
        {"tool": "run_adql", "result": {"row_count": 0, "rows": []}},
    ])


def test_is_empty_turn_with_python_failure():
    from app.services.claim_validator import is_empty_turn

    assert is_empty_turn([
        {"tool": "run_python", "result": {"success": False, "error": "crashed"}},
    ])


def test_is_empty_turn_false_when_real_data():
    from app.services.claim_validator import is_empty_turn

    assert not is_empty_turn([
        {"tool": "run_adql", "result": {"row_count": 5, "rows": [[1, 2, 3]]}},
    ])


def test_zero_data_but_quantitative_catches_pleiades():
    """F1.4: when the turn is entirely empty-or-failed AND the reply has
    quantitative claims, return them so the caller can hard-block
    without waiting for the regeneration loop."""
    from app.services.claim_validator import zero_data_but_quantitative

    empty_turn = [
        {"tool": "run_adql", "result": {"row_count": 0, "rows": []}},
        {"tool": "run_python", "result": {"success": False, "error": "crash"}},
    ]
    claims = zero_data_but_quantitative(PLEIADES_REPLY, empty_turn)
    assert len(claims) > 0
    values = {c.value for c in claims}
    assert 776.0 in values and 7.353 in values


def test_zero_data_but_quantitative_skips_when_data_exists():
    from app.services.claim_validator import zero_data_but_quantitative

    real_turn = [{"tool": "run_adql", "result": {"row_count": 10}}]
    claims = zero_data_but_quantitative(PLEIADES_REPLY, real_turn)
    assert claims == []


def test_partial_run_python_stdout_is_not_zero_data_turn():
    from app.services.claim_validator import is_empty_turn, validate_claims, zero_data_but_quantitative

    tool_results = [{
        "tool": "run_python",
        "result": {
            "__tool_status__": "PARTIAL",
            "__partial_output__": True,
            "success": False,
            "stdout": "Maximum 3D velocity: 322.3 km/s\nstars above 300 km/s: 1\n",
            "error": "KeyError: 9",
            "data_origin": "real_archive",
            "analysis_status": "partial",
        },
    }]
    reply = "The partial run found a maximum velocity of 322.3 km/s before the top-10 loop failed."

    assert is_empty_turn(tool_results) is False
    assert zero_data_but_quantitative(reply, tool_results) == []
    assert validate_claims(reply, tool_results).ok


# -------------------- F1.5: universe snapshot in block message --------------------


def test_block_message_includes_universe_snapshot():
    tool_results = [{"result": {"foo": 1.5, "bar": 2.7}}]
    r = validate_claims("z = 99.9", tool_results)
    assert not r.ok
    text = blocked_reply_text(r)
    # universe size should be reported
    assert "2 distinct numeric values" in text or "distinct numeric values" in text


def test_block_message_reports_empty_universe():
    r = validate_claims("z = 1.23", [])
    text = blocked_reply_text(r)
    assert "empty" in text.lower() or "0 distinct" in text.lower()


# -------------------- L1 (audit 2026-04-20): 光谱 / X-ray / 射电单位 --------------------


def test_wavelength_angstrom_caught():
    """L1: 'Hα emission at 6563 Å' 之前完全不被抽取 (value_with_error 需
    要 ± 符号, label_colon 只覆盖距离/红移等, 波长单位 Å 根本不在白名单).
    审计后波长 claim 进 value_bare_unit."""
    tool_results = [{"result": {"foo": 1.0}}]
    r = validate_claims("The Hα emission line is at 6563 Å", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 6563.0 in values, f"6563 Å 没被抽到: {r.uncited}"


def test_xray_luminosity_erg_per_s_caught():
    """L1: X 射线光度 'L_X = 1.5e44 erg/s' 必须被抽取."""
    tool_results = [{"result": {"bar": 2.0}}]
    r = validate_claims("AGN L_X = 1.5e44 erg/s reported", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 1.5e44 in values, f"1.5e44 erg/s 没被抽到: {r.uncited}"


def test_radio_flux_mjy_caught():
    """L1: 射电流量 '3.2 mJy' 必须被抽取."""
    tool_results = [{"result": {"qux": 0.5}}]
    r = validate_claims("FIRST flux 3.2 mJy", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 3.2 in values


def test_xray_energy_kev_caught():
    """L1: 能量 'E = 5.5 keV' 必须被抽取."""
    tool_results = [{"result": {"z": 0.1}}]
    r = validate_claims("Peak at 5.5 keV above continuum", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 5.5 in values


def test_frequency_ghz_caught():
    """L1: 射电频率 '1.4 GHz' 必须被抽取."""
    tool_results = [{"result": {"z": 0.1}}]
    r = validate_claims("Observation at 1.4 GHz", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 1.4 in values


def test_gpc_distance_caught():
    """L1: 宇宙学距离 '3.2 Gpc' — 之前 distance_pc/kpc/mpc 有但 Gpc 漏."""
    tool_results = [{"result": {"z": 0.1}}]
    r = validate_claims("The quasar is at distance 3.2 Gpc", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 3.2 in values


def test_wavelength_with_error_caught():
    """L1: 带误差的波长 '6563.1 ± 0.5 Å' 必须两个数都被抽取."""
    tool_results = [{"result": {"z": 0.1}}]
    r = validate_claims("Line center: 6563.1 ± 0.5 Å", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 6563.1 in values
    assert 0.5 in values


def test_existing_parallax_still_dedupped_not_duplicated():
    """L1: 新加的 value_bare_unit 不应让 'parallax is 9.00 mas' 产出
    两条 claim (同一 value).  span 重叠去重保证只有 1 条."""
    tool_results = [{"result": {"parallax": 7.5}}]
    r = validate_claims("The Pleiades parallax is 9.00 mas", tool_results)
    assert not r.ok
    # 同一 value (9.0) 不应多次计数
    vals_at_9 = [c for c in r.uncited if abs(c.value - 9.0) < 1e-6]
    assert len(vals_at_9) == 1, f"9.00 mas 被重复抽取: {r.uncited}"


# -------------------- L2 (audit 2026-04-20): 数字池元数据过滤 --------------------


def test_row_count_laundering_blocked_pleiades_776():
    """L2: 复现 Pleiades 第一次审稿的 laundering.
    工具返回 row_count=776, AI 声称 "776 member stars".
    原 validator 把 row_count 当普通数字吃进池, 让 776 通过.
    审计后 row_count 字段整体跳过 → 776 不进池 → claim 被拦."""
    tool_results = [{
        "tool": "run_adql",
        "result": {
            # 没有任何真实的 776 数据行, 只有 row_count 这个元字段
            "row_count": 776,
            "showing": 100,
            "columns": ["source_id", "ra", "dec", "parallax"],
            "data": {
                "parallax": [7.3, 7.5, 7.4, 7.6, 7.35],  # 真实数据, 无 776
            },
        },
    }]
    r = validate_claims("Found 776 member stars in the Pleiades.", tool_results)
    assert not r.ok, "776 不该过 — row_count 是元数据不能 launder 成观测"
    # 776 应该在 uncited 里
    assert any(abs(c.value - 776) < 1e-6 for c in r.uncited), \
        f"776 没被拦下, 说明元数据过滤失效: {r.uncited}"


def test_timestamp_not_laundered_as_data():
    """L2: 工具返回 timestamp=1745136000 之类 UNIX epoch 大整数,
    AI 不应该能引用它装作真实观测数据."""
    tool_results = [{"result": {
        "timestamp_utc": 1745136000,
        "elapsed_seconds": 12.5,
        "data": {"parallax": [7.5]},
    }}]
    # AI 瞎说距离 1745136000 pc — 应该被拦
    r = validate_claims("The distance is 1745136000 pc", tool_results)
    assert not r.ok, "timestamp 作为元数据不应 launder"


def test_real_data_still_matches_after_metadata_filter():
    """L2: 确认 filter 不是把所有数字都砍了.  真实数据字段 (parallax,
    ra, distance, period 等) 照常进池."""
    tool_results = [{"result": {
        "row_count": 1,
        "timestamp": 1745136000,
        "data": {"parallax": 7.353, "period": 5.366},
    }}]
    # 引用真实数据 7.353 和 5.366 → 应该通过 (都在池里)
    r = validate_claims("Parallax 7.353 mas and period 5.366 days.", tool_results)
    assert r.ok, (
        f"真实数据被误拦: uncited={[c.raw for c in r.uncited]}, "
        f"universe_size={r.universe_size}"
    )


def test_nested_data_rows_still_get_harvested():
    """L2: 数据行里的数字不能被 'row_count' 这种 key 误伤.  数字在
    嵌套 dict/list 的 value 位置时, 按普通值处理."""
    tool_results = [{"result": {
        "row_count": 5,
        "rows": [
            {"pf": 5.366154, "pf_err": 0.000109},
            {"pf": 5.366200, "pf_err": 0.000200},
        ],
    }}]
    # 引用 5.366154 应该匹配 → 通过
    r = validate_claims("Gaia period 5.366154 days", tool_results)
    assert r.ok, f"嵌套数据行里的数字被误拦: {r.uncited}"


# -------------------- L24 (audit 2026-04-20): 科学计数法容差 --------------------


def test_scientific_notation_does_not_cross_orders_of_magnitude():
    """L24: ±1% 相对容差已经能正确拒绝跨数量级的假匹配 (1.23e-24 vs
    1.25e-23 差 10 倍).  这条测试锁定: 未来任何人改 _matches_any
    不能把指数级错配当成"相近"."""
    tool_results = [{"result": {"mass_kg": 1.23e-24}}]
    # claim 里是正确数量级 → 应通过
    r1 = validate_claims("mass = 1.23e-24 kg", tool_results)
    assert r1.ok, "相同数量级内 1.23e-24 应匹配"

    # claim 里差一个数量级 → 不该通过
    r2 = validate_claims("mass = 1.23e-23 kg", tool_results)
    assert not r2.ok, (
        "1.23e-23 跟 tool 的 1.23e-24 差 10 倍, 不该匹配 (相对容差"
        "的物理意义)"
    )


# ---- W1 (PART W): 文献先验 age/mass/distance 硬拦 ----


def test_literature_prior_age_without_fit_isochrone_is_violation():
    """W1: run_adql 返回一堆 Gaia 行, reply 说 "age ~100 Myr" 但本轮没跑
    fit_isochrone / search_literature / get_object_dossier, 视为文献先验."""
    from app.services.claim_validator import literature_prior_violations
    rows = [{"phot_g_mean_mag": 10.0 + i * 0.01} for i in range(100)]
    tool_results = [
        {"tool": "run_adql", "input": {}, "result": {"data": {"rows": rows}}},
    ]
    vios = literature_prior_violations(
        "The cluster age is ~100 Myr (young open cluster).", tool_results
    )
    assert len(vios) >= 1
    assert any(c.label == "age_myr" for c in vios)


def test_literature_prior_age_passes_when_fit_isochrone_ran():
    """W1: 跑了 fit_isochrone 后 AI 可以引用 age 值."""
    from app.services.claim_validator import literature_prior_violations
    tool_results = [
        {"tool": "fit_isochrone", "input": {}, "result": {"best_log_age": 8.0}},
    ]
    assert literature_prior_violations("The best-fit age is 100 Myr.", tool_results) == []


def test_reply_contains_cjk_detects_chinese_prose():
    """X (PART X 方案 D): reply 中文 prose 触发 CJK guard hardblock."""
    from app.services.claim_validator import reply_contains_cjk
    assert reply_contains_cjk("符合昴星团约 100 Myr 的年龄")
    assert reply_contains_cjk("根据 Gaia DR3 ...")
    assert reply_contains_cjk("年龄: ~100 Myr (年轻疏散星团)")


def test_reply_contains_cjk_english_passes():
    """X: 纯英文 reply 不触发 CJK guard."""
    from app.services.claim_validator import reply_contains_cjk
    assert not reply_contains_cjk("The Pleiades age is approximately 100 Myr.")
    assert not reply_contains_cjk("")
    assert not reply_contains_cjk("GCVS catalog returns Period = 5.366208 days.")


def test_reply_contains_cjk_scientific_unicode_passes():
    """X: 希腊字母 / Å / ° / ± / ≥ / ≈ 等科学 Unicode 在 DejaVu 字体支持
    范围内, 不算 CJK, guard 放行."""
    from app.services.claim_validator import reply_contains_cjk
    assert not reply_contains_cjk(r"$\alpha$ Cen A, $T_{\rm eff}$ = 5800 K")
    assert not reply_contains_cjk("6563 Å H-alpha, ±0.3 mag, ≈5780 K, RA 180°")
    assert not reply_contains_cjk("naïve façade")


def test_reply_contains_cjk_threshold_tolerates_single_char():
    """X: 阈值 2 — 单个 CJK 字符 (例如引用专名) 不触发, 避免 false-positive.
    但 >= 2 字符的 prose 引导词 (根据 / 符合 / 与 / 年龄) 必然命中."""
    from app.services.claim_validator import reply_contains_cjk
    assert not reply_contains_cjk("A 一 B")   # 1 CJK, below threshold
    assert reply_contains_cjk("根据")          # 2 CJK at threshold
    assert reply_contains_cjk("符合约")        # 3 CJK
    assert reply_contains_cjk("一" * 10)       # well above


def test_literature_prior_distance_passes_with_run_adql():
    """W1: distance 有 run_adql (Gaia parallax) 支撑即可通过."""
    from app.services.claim_validator import literature_prior_violations
    tool_results = [
        {"tool": "run_adql", "input": {}, "result": {"data": {"parallax": [7.35]}}},
    ]
    assert literature_prior_violations("The distance is ~136 pc.", tool_results) == []


def test_literature_prior_mass_with_only_search_objects_is_violation():
    """W1: search_objects 不是 mass 的测量/引用工具, mass claim 违规."""
    from app.services.claim_validator import literature_prior_violations
    tool_results = [
        {"tool": "search_objects", "input": {}, "result": {"results": []}},
    ]
    vios = literature_prior_violations("Typical mass is 2 M_sun.", tool_results)
    assert len(vios) >= 1


def test_literature_prior_no_claim_labels_no_violations():
    """W1: reply 不含 age/mass/distance, 自然无 W1 违规 (只做 label 过滤)."""
    from app.services.claim_validator import literature_prior_violations
    tool_results = [{"tool": "run_adql", "input": {}, "result": {"data": {"x": [1]}}}]
    assert literature_prior_violations("The period is 5.366 days.", tool_results) == []
