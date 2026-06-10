"""Tests for the zero-fabrication gate (Phase 1 / R2).

Locks the contract that any numeric astronomical claim in an AI reply is
verified against the turn's tool_results, and uncited claims are flagged
so the agent loop can regenerate or block the reply.
"""

from __future__ import annotations

import pytest

from app.services.claim_validator import (
    _strip_thousands_separators,
    blocked_reply_text,
    build_regeneration_prompt,
    build_zero_data_qualitative_regeneration_prompt,
    extract_claims,
    literature_prior_violations,
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


def test_b15_unicode_minus_claim_is_extracted():
    """B15: a value written with the Unicode minus U+2212 ('w0 = −0.84') must
    still be extracted as a claim; otherwise it bypasses the whole gate."""
    claims = extract_claims("The chain prefers w0 = −0.84 for this dataset.")
    assert any(
        c.label == "cosmology_w0" and c.value == pytest.approx(-0.84) for c in claims
    )


def test_b15_unicode_minus_fabrication_is_flagged():
    """B15: a fabricated negative value typed with U+2212 must NOT pass the
    gate when the tool returned a different number."""
    tool_results = [{"tool": "run_cosmology_likelihood_chain", "result": {"w0": -0.55}}]
    r = validate_claims("The chain prefers w0 = −0.84.", tool_results)
    assert r.ok is False
    assert any(c.value == pytest.approx(-0.84) for c in r.uncited)


def test_b5_sign_flipped_claim_is_flagged():
    """B5: a sign-flipped value (claim +0.84 vs tool -0.84) must be flagged —
    for w0/wa the sign is the physical conclusion, so abs()-matching is wrong."""
    tool_results = [{"tool": "run_cosmology_likelihood_chain", "result": {"w0": -0.84}}]
    r = validate_claims("The fit gives w0 = 0.84 for this dataset.", tool_results)
    assert r.ok is False


def test_b5_correct_sign_still_matches():
    """B5 guard against over-tightening: the correctly-signed value still
    validates against the tool's value (no false positive)."""
    tool_results = [{"tool": "run_cosmology_likelihood_chain", "result": {"w0": -0.84}}]
    r = validate_claims("The fit gives w0 = -0.84 for this dataset.", tool_results)
    assert r.ok is True


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


def test_cosmology_claims_pass_with_publication_ready_compressed_likelihood_result():
    tool_results = [
        {
            "tool": "run_cosmology_likelihood_chain",
            "result": {
                "success": True,
                "publication_ready": True,
                "claim_scope": "compressed_likelihood_preliminary",
                "parameters": {
                    "H0": {"median": 68.1},
                    "S8": {"median": 0.831},
                },
            },
        }
    ]
    r = validate_claims(
        "The compressed-likelihood preliminary result gives H0 = 68.1 km/s/Mpc and S8 = 0.831.",
        tool_results,
    )
    assert r.ok


def test_cosmology_claims_reject_config_only_likelihood_result():
    tool_results = [
        {
            "tool": "build_cosmology_likelihood",
            "result": {
                "success": True,
                "publication_ready": False,
                "priors": {"H0": [50.0, 90.0]},
            },
        }
    ]
    r = validate_claims("The posterior gives H0 = 68.1 km/s/Mpc and S8 = 0.831.", tool_results)
    assert not r.ok
    assert {claim.label for claim in r.uncited} >= {"cosmology_h0", "cosmology_s8"}


def test_exploratory_mcmc_numbers_flow_into_numeric_universe():
    """EXPLORATORY chain_tier (2026-05-20): numbers from the posterior should
    flow into the numeric universe so chat-level discussion passes the +/-1%
    check, even though publication_ready=False keeps the result out of the
    bibcode / claimable-success pool."""
    tool_results = [
        {
            "tool": "fit_cosmology_mcmc",
            "result": {
                "success": True,
                "__tool_status__": "EXPLORATORY",
                "analysis_status": "EXPLORATORY",
                "chain_tier": "exploratory",
                "publication_ready": False,
                "__exploratory_warning__": "Chain min ESS=200 below publication threshold 400.",
                "parameters": {
                    "H0": {"median": 70.0, "ess_bulk": 200.0, "rhat": 1.07},
                    "w0": {"median": -1.1, "ess_bulk": 200.0, "rhat": 1.07},
                },
            },
        }
    ]
    r = validate_claims(
        "An exploratory chain (ESS=200) suggests H0 around 70 km/s/Mpc and w0 around -1.1.",
        tool_results,
    )
    assert r.ok


def test_exploratory_mcmc_not_counted_as_claimable_success():
    """EXPLORATORY MCMC result publication_ready=False -> excluded from the
    claimable-success pool. It cannot be cited as a published constraint or
    add bibcodes to the citation pool."""
    from app.services.claim_validator import _payload_is_claimable_success

    result = {
        "success": True,
        "__tool_status__": "EXPLORATORY",
        "chain_tier": "exploratory",
        "publication_ready": False,
        "parameters": {"H0": {"median": 70.0}},
    }
    assert not _payload_is_claimable_success("fit_cosmology_mcmc", result)


def test_exploratory_result_not_treated_as_empty_turn():
    """EXPLORATORY status must not flip is_empty_turn to True. The posterior
    is meaningful enough to discuss in chat; only PARTIAL+__do_not_claim__
    and explicit EMPTY/FAILED/UNAVAILABLE qualify as empty."""
    from app.services.claim_validator import is_empty_turn

    tool_results = [
        {
            "tool": "fit_cosmology_mcmc",
            "result": {
                "success": True,
                "__tool_status__": "EXPLORATORY",
                "analysis_status": "EXPLORATORY",
                "chain_tier": "exploratory",
                "publication_ready": False,
                "parameters": {"H0": {"median": 70.0}},
            },
        }
    ]
    assert is_empty_turn(tool_results) is False


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
    assert "cited data lookup" in text.lower()
    assert "redshift_z" not in text


def test_blocked_reply_text_does_not_leak_internal_group_labels():
    r = validate_claims("H0 = 67.0 km/s/Mpc and H0 = 73.0 km/s/Mpc.", [])
    assert not r.ok
    text = blocked_reply_text(r)
    assert "value_bare_unit" not in text
    assert "g1" not in text
    assert "67.0" in text
    assert "73.0" in text


def test_zero_data_qualitative_rewrite_prompt_allows_method_answer_without_numbers():
    r = validate_claims("A DESI+SN comparison may show a 2 sigma deviation at z = 0.4.", [])
    assert not r.ok
    prompt = build_zero_data_qualitative_regeneration_prompt(r)
    assert "qualitative-only answer" in prompt
    assert "Remove every numeric value" in prompt
    assert "method or expected scientific behaviour" in prompt
    assert "Do not call tools" in prompt


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


# -------------------- L1 (audit 2026-04-20): spectral / X-ray / radio units --------------------


def test_wavelength_angstrom_caught():
    """L1: 'Hα emission at 6563 Å' was previously not extracted at all (value_with_error requires
    a ± symbol, label_colon only covers distance/redshift etc., and wavelength unit Å was not
    in the allowlist). After the audit, wavelength claims enter value_bare_unit."""
    tool_results = [{"result": {"foo": 1.0}}]
    r = validate_claims("The Hα emission line is at 6563 Å", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 6563.0 in values, f"6563 Å 没被抽到: {r.uncited}"


def test_xray_luminosity_erg_per_s_caught():
    """L1: X-ray luminosity 'L_X = 1.5e44 erg/s' must be extracted."""
    tool_results = [{"result": {"bar": 2.0}}]
    r = validate_claims("AGN L_X = 1.5e44 erg/s reported", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 1.5e44 in values, f"1.5e44 erg/s 没被抽到: {r.uncited}"


def test_radio_flux_mjy_caught():
    """L1: radio flux '3.2 mJy' must be extracted."""
    tool_results = [{"result": {"qux": 0.5}}]
    r = validate_claims("FIRST flux 3.2 mJy", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 3.2 in values


def test_xray_energy_kev_caught():
    """L1: energy 'E = 5.5 keV' must be extracted."""
    tool_results = [{"result": {"z": 0.1}}]
    r = validate_claims("Peak at 5.5 keV above continuum", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 5.5 in values


def test_frequency_ghz_caught():
    """L1: radio frequency '1.4 GHz' must be extracted."""
    tool_results = [{"result": {"z": 0.1}}]
    r = validate_claims("Observation at 1.4 GHz", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 1.4 in values


def test_gpc_distance_caught():
    """L1: cosmological distance '3.2 Gpc' — distance_pc/kpc/mpc were covered before but Gpc was missing."""
    tool_results = [{"result": {"z": 0.1}}]
    r = validate_claims("The quasar is at distance 3.2 Gpc", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 3.2 in values


def test_wavelength_with_error_caught():
    """L1: wavelength with error '6563.1 ± 0.5 Å' — both numbers must be extracted."""
    tool_results = [{"result": {"z": 0.1}}]
    r = validate_claims("Line center: 6563.1 ± 0.5 Å", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 6563.1 in values
    assert 0.5 in values


def test_existing_parallax_still_dedupped_not_duplicated():
    """L1: the newly added value_bare_unit should not cause 'parallax is 9.00 mas' to produce
    two claims for the same value. Span-overlap deduplication ensures only 1 claim."""
    tool_results = [{"result": {"parallax": 7.5}}]
    r = validate_claims("The Pleiades parallax is 9.00 mas", tool_results)
    assert not r.ok
    # the same value (9.0) should not be counted multiple times
    vals_at_9 = [c for c in r.uncited if abs(c.value - 9.0) < 1e-6]
    assert len(vals_at_9) == 1, f"9.00 mas 被重复抽取: {r.uncited}"


# -------------------- L2 (audit 2026-04-20): numeric pool metadata filtering --------------------


def test_row_count_laundering_blocked_pleiades_776():
    """L2: reproduces the laundering from the first Pleiades review.
    Tool returned row_count=776, AI claimed "776 member stars".
    The original validator ingested row_count as an ordinary number, letting 776 pass.
    After the audit, the row_count field is skipped entirely → 776 not in pool → claim blocked."""
    tool_results = [{
        "tool": "run_adql",
        "result": {
            # no actual 776 data rows, only the row_count metadata field
            "row_count": 776,
            "showing": 100,
            "columns": ["source_id", "ra", "dec", "parallax"],
            "data": {
                "parallax": [7.3, 7.5, 7.4, 7.6, 7.35],  # real data, no 776
            },
        },
    }]
    r = validate_claims("Found 776 member stars in the Pleiades.", tool_results)
    assert not r.ok, "776 不该过 — row_count 是元数据不能 launder 成观测"
    # 776 应该在 uncited 里
    assert any(abs(c.value - 776) < 1e-6 for c in r.uncited), \
        f"776 没被拦下, 说明元数据过滤失效: {r.uncited}"


def test_timestamp_not_laundered_as_data():
    """L2: tool returns a UNIX epoch large integer like timestamp=1745136000;
    the AI must not be able to cite it as if it were real observational data."""
    tool_results = [{"result": {
        "timestamp_utc": 1745136000,
        "elapsed_seconds": 12.5,
        "data": {"parallax": [7.5]},
    }}]
    # AI fabricates a distance of 1745136000 pc — should be blocked
    r = validate_claims("The distance is 1745136000 pc", tool_results)
    assert not r.ok, "timestamp 作为元数据不应 launder"


def test_real_data_still_matches_after_metadata_filter():
    """L2: confirms the filter does not discard all numbers. Real data fields (parallax,
    ra, distance, period, etc.) enter the pool as normal."""
    tool_results = [{"result": {
        "row_count": 1,
        "timestamp": 1745136000,
        "data": {"parallax": 7.353, "period": 5.366},
    }}]
    # citing real data 7.353 and 5.366 → should pass (both in pool)
    r = validate_claims("Parallax 7.353 mas and period 5.366 days.", tool_results)
    assert r.ok, (
        f"真实数据被误拦: uncited={[c.raw for c in r.uncited]}, "
        f"universe_size={r.universe_size}"
    )


def test_nested_data_rows_still_get_harvested():
    """L2: numbers inside data rows must not be incorrectly excluded by keys like 'row_count'.
    Numbers in value positions within nested dict/list are treated as ordinary values."""
    tool_results = [{"result": {
        "row_count": 5,
        "rows": [
            {"pf": 5.366154, "pf_err": 0.000109},
            {"pf": 5.366200, "pf_err": 0.000200},
        ],
    }}]
    # citing 5.366154 should match → pass
    r = validate_claims("Gaia period 5.366154 days", tool_results)
    assert r.ok, f"嵌套数据行里的数字被误拦: {r.uncited}"


# -------------------- L24 (audit 2026-04-20): scientific notation tolerance --------------------


def test_scientific_notation_does_not_cross_orders_of_magnitude():
    """L24: the ±1% relative tolerance correctly rejects cross-order-of-magnitude false matches
    (1.23e-24 vs 1.25e-23 differ by 10x). This test locks: anyone who changes _matches_any
    in the future must not treat an order-of-magnitude mismatch as "close"."""
    tool_results = [{"result": {"mass_kg": 1.23e-24}}]
    # claim is the correct order of magnitude → should pass
    r1 = validate_claims("mass = 1.23e-24 kg", tool_results)
    assert r1.ok, "相同数量级内 1.23e-24 应匹配"

    # claim is off by one order of magnitude → should not pass
    r2 = validate_claims("mass = 1.23e-23 kg", tool_results)
    assert not r2.ok, (
        "1.23e-23 跟 tool 的 1.23e-24 差 10 倍, 不该匹配 (相对容差"
        "的物理意义)"
    )


# ---- W1 (PART W): literature prior age/mass/distance hard block ----


def test_literature_prior_age_without_fit_isochrone_is_violation():
    """W1: run_adql returns many Gaia rows, reply says "age ~100 Myr" but this turn
    did not run fit_isochrone / search_literature / get_object_dossier — treated as a literature prior."""
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
    """W1: after running fit_isochrone the AI may cite the age value."""
    from app.services.claim_validator import literature_prior_violations
    tool_results = [
        {"tool": "fit_isochrone", "input": {}, "result": {"best_log_age": 8.0}},
    ]
    assert literature_prior_violations("The best-fit age is 100 Myr.", tool_results) == []


def test_reply_contains_cjk_detects_chinese_prose():
    """X (PART X scheme D): Chinese prose in reply triggers the CJK guard hardblock."""
    from app.services.claim_validator import reply_contains_cjk
    assert reply_contains_cjk("符合昴星团约 100 Myr 的年龄")
    assert reply_contains_cjk("根据 Gaia DR3 ...")
    assert reply_contains_cjk("年龄: ~100 Myr (年轻疏散星团)")


def test_reply_contains_cjk_english_passes():
    """X: pure English reply does not trigger the CJK guard."""
    from app.services.claim_validator import reply_contains_cjk
    assert not reply_contains_cjk("The Pleiades age is approximately 100 Myr.")
    assert not reply_contains_cjk("")
    assert not reply_contains_cjk("GCVS catalog returns Period = 5.366208 days.")


def test_reply_contains_cjk_scientific_unicode_passes():
    """X: Greek letters / Å / ° / ± / >= / ~ and similar scientific Unicode within DejaVu
    font support are not CJK and are passed through by the guard."""
    from app.services.claim_validator import reply_contains_cjk
    assert not reply_contains_cjk(r"$\alpha$ Cen A, $T_{\rm eff}$ = 5800 K")
    assert not reply_contains_cjk("6563 Å H-alpha, ±0.3 mag, ≈5780 K, RA 180°")
    assert not reply_contains_cjk("naïve façade")


def test_reply_contains_cjk_threshold_tolerates_single_char():
    """X: threshold 2 — a single CJK character (e.g. a proper-noun citation) does not trigger,
    avoiding false positives. But prose lead words of >= 2 CJK characters will always match."""
    from app.services.claim_validator import reply_contains_cjk
    assert not reply_contains_cjk("A 一 B")   # 1 CJK, below threshold
    assert reply_contains_cjk("根据")          # 2 CJK at threshold
    assert reply_contains_cjk("符合约")        # 3 CJK
    assert reply_contains_cjk("一" * 10)       # well above


def test_literature_prior_distance_passes_with_run_adql():
    """W1: distance supported by run_adql (Gaia parallax) is sufficient to pass."""
    from app.services.claim_validator import literature_prior_violations
    tool_results = [
        {"tool": "run_adql", "input": {}, "result": {"data": {"parallax": [7.35]}}},
    ]
    assert literature_prior_violations("The distance is ~136 pc.", tool_results) == []


def test_literature_prior_mass_with_only_search_objects_is_violation():
    """W1: search_objects is not a measurement/citation tool for mass; a mass claim is a violation."""
    from app.services.claim_validator import literature_prior_violations
    tool_results = [
        {"tool": "search_objects", "input": {}, "result": {"results": []}},
    ]
    vios = literature_prior_violations("Typical mass is 2 M_sun.", tool_results)
    assert len(vios) >= 1


def test_literature_prior_no_claim_labels_no_violations():
    """W1: reply contains no age/mass/distance, so naturally no W1 violation (label filtering only)."""
    from app.services.claim_validator import literature_prior_violations
    tool_results = [{"tool": "run_adql", "input": {}, "result": {"data": {"x": [1]}}}]
    assert literature_prior_violations("The period is 5.366 days.", tool_results) == []


# ── Stage 6 P0: blocked_reply_with_narrative — preserve AI narrative ─────


def test_blocked_reply_with_narrative_redacts_uncited_in_place():
    """Stage 6 P0: when AI cites uncited numbers, we should redact them in
    the original reply but keep the surrounding narrative (methodology,
    caveats, qualitative explanations)."""
    from app.services.claim_validator import (
        blocked_reply_with_narrative,
        validate_claims,
    )
    tool_results = [{
        "tool": "fit_line_lfr",
        "result": {"posterior": {"H0_med": 67.36, "slope_med": 0.792}},
    }]
    reply = (
        "Based on the Bayesian linear regression, we find the slope is 0.792 "
        "and the inferred H0 is 73.04 km/s/Mpc which differs from the "
        "Planck baseline. The method used was emcee MCMC with 1500 steps."
    )
    validation = validate_claims(reply, tool_results)
    assert not validation.ok
    assert len(validation.uncited) >= 1
    out = blocked_reply_with_narrative(validation, reply)
    assert "Reply withheld" in out
    assert "---" in out
    # AI's narrative is preserved (methodology + qualitative wording)
    assert "Bayesian linear regression" in out
    assert "emcee MCMC" in out
    assert "differs from the" in out
    # The uncited number 73.04 should be redacted in the narrative section
    assert "[unverified: 73.04]" in out


def test_blocked_reply_with_narrative_falls_back_when_reply_empty():
    """Empty original reply falls back to banner-only (legacy behavior)."""
    from app.services.claim_validator import (
        blocked_reply_text,
        blocked_reply_with_narrative,
        validate_claims,
    )
    tool_results = [{"tool": "run_python", "result": {"value": 1.0}}]
    validation = validate_claims("The mass is 5.5 M_sun.", tool_results)
    out_with_empty_reply = blocked_reply_with_narrative(validation, "")
    out_banner_only = blocked_reply_text(validation)
    assert out_with_empty_reply == out_banner_only


def test_blocked_reply_with_narrative_handles_overlapping_spans():
    """Redaction must handle overlapping/duplicate uncited spans without
    corrupting the reply (dedupe by earliest span)."""
    from app.services.claim_validator import (
        Claim,
        _redact_uncited_phrases,
    )
    reply = "Slope value 0.792 detected here."
    uncited = [
        Claim(label="a", raw="0.792", value=0.792, start=12, end=17),
        Claim(label="b", raw="0.792", value=0.792, start=12, end=17),
    ]
    out = _redact_uncited_phrases(reply, uncited)
    assert out == "Slope value [unverified: 0.792] detected here."


def test_attach_draft_to_banner_with_literature_narrative_banner():
    """Stage 6 P0a follow-up: literature_narrative banner + AI draft assembly;
    the full draft is preserved (banner already lists line numbers for locating, no redaction)."""
    from app.services.claim_validator import (
        CitationViolation,
        attach_draft_to_banner,
        blocked_unsupported_narrative_reply_text,
    )
    violations = [
        CitationViolation(
            kind="literature_fallback",
            match_text="literature values typical for this object",
            line_number=13,
        ),
    ]
    banner = blocked_unsupported_narrative_reply_text(violations)
    draft = (
        "Based on the search results and the Bayesian fit, the slope is "
        "consistent with literature values typical for this object. "
        "The methodology used was emcee MCMC with 1500 steps."
    )
    out = attach_draft_to_banner(banner, draft)
    assert "Reply withheld" in out
    assert "(line 13)" in out
    assert "---" in out
    assert "AI's draft response" in out
    assert "Bayesian fit" in out
    assert "emcee MCMC" in out
    assert "literature values typical for this object" in out


def test_attach_draft_to_banner_with_citation_banner():
    """citation banner + AI draft assembly, the full draft is preserved."""
    from app.services.claim_validator import (
        CitationViolation,
        attach_draft_to_banner,
        blocked_citation_reply_text,
    )
    violations = [
        CitationViolation(
            kind="invalid_bibcode",
            match_text="2024XXX..001A",
            line_number=7,
        ),
    ]
    banner = blocked_citation_reply_text(violations)
    draft = "The fit shows H0 = 67.36 km/s/Mpc per (2024XXX..001A)."
    out = attach_draft_to_banner(banner, draft)
    assert "Reply withheld" in out
    assert "(line 7)" in out
    assert "---" in out
    assert "AI's draft response" in out
    assert "H0 = 67.36 km/s/Mpc" in out


def test_attach_draft_to_banner_falls_back_when_reply_empty():
    """Empty / blank draft falls back to banner-only."""
    from app.services.claim_validator import attach_draft_to_banner

    banner = "⚠ Reply withheld: some reason."
    assert attach_draft_to_banner(banner, "") == banner
    assert attach_draft_to_banner(banner, "   \n\n") == banner


def test_unclassified_literature_violations_blocks_uncited_search_paper():
    """Stage 6 P0c-C (2026-05-19): citing a paper returned by search_literature without
    calling classify_literature_relevance → violation."""
    from app.services.claim_validator import unclassified_literature_violations

    tool_results = [
        {
            "tool": "search_literature",
            "result": {
                "results": [
                    {"bibcode": "2024A&A...678A.123S", "title": "DESI BAO"},
                    {"bibcode": "2024arXiv2401.01001A", "title": "Other"},
                ],
            },
        },
        # no classify_literature_relevance
    ]
    reply = "Based on 2024A&A...678A.123S, the BAO measurement gives H0 = 67."
    violations = unclassified_literature_violations(reply, tool_results)
    assert len(violations) == 1
    assert violations[0].kind == "unclassified_literature"
    assert violations[0].match_text == "2024A&A...678A.123S"


def test_unclassified_literature_violations_passes_after_classify():
    """classify_literature_relevance called and labeled Direct, then cited → no violation."""
    from app.services.claim_validator import unclassified_literature_violations

    tool_results = [
        {
            "tool": "search_literature",
            "result": {
                "results": [{"bibcode": "2024A&A...678A.123S", "title": "DESI BAO"}],
            },
        },
        {
            "tool": "classify_literature_relevance",
            "result": {
                "classifications": [
                    {
                        "bibcode": "2024A&A...678A.123S",
                        "relevance": "Direct",
                        "reason": "DESI DR1 BAO directly answers H0 question",
                    },
                ],
            },
        },
    ]
    reply = "Based on 2024A&A...678A.123S, the BAO measurement gives H0 = 67."
    violations = unclassified_literature_violations(reply, tool_results)
    assert violations == []


def test_unclassified_literature_violations_blocks_cited_off_topic_paper():
    """classify labeled Off-topic, but reply still cites it → violation (kind=cited_off_topic_paper)."""
    from app.services.claim_validator import unclassified_literature_violations

    tool_results = [
        {
            "tool": "search_literature",
            "result": {
                "results": [{"bibcode": "2024A&A...678A.123S", "title": "DESI BAO"}],
            },
        },
        {
            "tool": "classify_literature_relevance",
            "result": {
                "classifications": [
                    {
                        "bibcode": "2024A&A...678A.123S",
                        "relevance": "Off-topic",
                        "reason": "BAO not relevant to this question",
                    },
                ],
            },
        },
    ]
    reply = "Based on 2024A&A...678A.123S, the BAO measurement gives H0 = 67."
    violations = unclassified_literature_violations(reply, tool_results)
    assert len(violations) == 1
    assert violations[0].kind == "cited_off_topic_paper"
    assert violations[0].match_text == "2024A&A...678A.123S"


def test_blocked_unclassified_literature_reply_text_groups_unclassified_and_off_topic():
    """banner text contains 2 groups: unclassified list + Off-topic list."""
    from app.services.claim_validator import (
        CitationViolation,
        blocked_unclassified_literature_reply_text,
    )

    violations = [
        CitationViolation(kind="unclassified_literature", match_text="2024A", line_number=3),
        CitationViolation(kind="cited_off_topic_paper", match_text="2023B", line_number=5),
    ]
    text = blocked_unclassified_literature_reply_text(violations)
    assert "Reply withheld" in text
    assert "classify_literature_relevance" in text
    assert "not classified" in text
    assert "Off-topic" in text
    assert "2024A" in text
    assert "2023B" in text
    assert "(line 3)" in text
    assert "(line 5)" in text


# -------------------- PART AD: thousands separator (B3) --------------------


def test_extract_thousands_separator_in_value():
    # 5,800 must be read as 5800, not split into 5 and 800.
    claims = extract_claims("The star has T_eff = 5,800 K.")
    assert any(c.label == "teff_k" and c.value == pytest.approx(5800) for c in claims)


def test_thousands_normalization_leaves_list_separators_untouched():
    # comma+space (coordinate / list separators) must NOT be merged...
    assert _strip_thousands_separators("ra, dec = 12, 345") == "ra, dec = 12, 345"
    # ...but a genuine thousands grouping is normalized, including 1,234,567.
    assert _strip_thousands_separators("count 1,234,567 rows") == "count 1234567 rows"


# -------------------- PART AD: partial-failure fabrication (B2) --------------------


def test_partial_failure_fabricated_number_still_blocked():
    # search_literature failed (0 hits) but run_adql succeeded. A number the
    # failed tool would have provided is not in the universe → still blocked,
    # without a blanket "any failure blocks the turn" rule that would also
    # reject the legitimate run_adql-backed parts.
    tool_results = [
        {"tool": "search_literature",
         "result": {"success": False, "__tool_status__": "EMPTY", "row_count": 0}},
        {"tool": "run_adql", "result": {"parallax": 7.50}},
    ]
    r = validate_claims("Based on the literature, the redshift is z = 2.45.", tool_results)
    assert not r.ok


# -------------------- P0-b: power-of-ten scientific notation --------------------


def test_normalize_sci_notation_basic_forms():
    from app.services.claim_validator import _normalize_sci_notation
    assert _normalize_sci_notation("3.5 × 10^8") == "3.5e8"
    assert _normalize_sci_notation("3.5 x 10^8") == "3.5e8"
    assert _normalize_sci_notation("3.5·10**8") == "3.5e8"
    assert _normalize_sci_notation("1.2 × 10^-3") == "1.2e-3"
    # superscript exponent forms
    assert _normalize_sci_notation("3.5 × 10⁸") == "3.5e8"
    assert _normalize_sci_notation("1.2 × 10⁻³") == "1.2e-3"
    # bare power of ten → implicit mantissa 1
    assert _normalize_sci_notation("about 10^8 M_sun") == "about 1e8 M_sun"


def test_normalize_sci_notation_leaves_prose_untouched():
    from app.services.claim_validator import _normalize_sci_notation
    # "x" not preceded by a number is not a product sign
    assert _normalize_sci_notation("box 10 stars") == "box 10 stars"
    # "2 x 10 stars" has no ^/** exponent marker after 10 → not sci-notation
    assert _normalize_sci_notation("2 x 10 stars") == "2 x 10 stars"
    # plain e-notation is already understood by _NUM, left as-is
    assert _normalize_sci_notation("1.2e-3 mas") == "1.2e-3 mas"


def test_extract_power_of_ten_mass_keeps_full_value():
    # Regression for the "mass_solar parsed 3.5 × 10^8 as 8.0" bug.
    claims = extract_claims("The galaxy mass is 3.5 × 10^8 M_sun.")
    vals = [c.value for c in claims if c.label == "mass_solar"]
    assert any(v == pytest.approx(3.5e8) for v in vals), vals
    assert 8.0 not in vals  # the old broken extraction must be gone


def test_extract_power_of_ten_xray_luminosity():
    claims = extract_claims("AGN L_X = 1.5 × 10^44 erg/s reported.")
    assert any(c.value == pytest.approx(1.5e44) for c in claims)


def test_power_of_ten_fabrication_is_flagged():
    # universe has 4.2e8; reply claims 3.5 × 10^8 (wrong) → must be flagged
    # now that the full value (not the bare exponent 8) is extracted.
    tool_results = [{"result": {"mass": 4.2e8}}]
    r = validate_claims("The mass is 3.5 × 10^8 M_sun.", tool_results)
    assert not r.ok


def test_power_of_ten_correct_value_passes():
    tool_results = [{"result": {"mass": 3.5e8}}]
    r = validate_claims("The mass is 3.5 × 10^8 M_sun.", tool_results)
    assert r.ok


# -------------------- P0-a: free-text fields don't pollute the universe ------


def test_freetext_banner_numbers_excluded_from_universe():
    from app.services.claim_validator import _iter_numeric_values
    payload = {
        "__message_to_model__": "Try a narrower TOP 5000 query within 365 days.",
        "__suggested_next_step__": "Re-run with radius 0.5 deg.",
        "result": {"parallax": 7.353},
    }
    universe = set(_iter_numeric_values(payload))
    assert 7.353 in universe        # real data value still harvested
    assert 5000 not in universe     # banner prose numbers excluded
    assert 365 not in universe
    assert 0.5 not in universe


def test_fabrication_cannot_launder_via_banner_text():
    # A failed tool's banner mentions "5000"; the reply must not be able to
    # cite 5000 as a result just because it appears in injected prose.
    tool_results = [
        {"tool": "run_adql",
         "result": {"__tool_status__": "EMPTY", "row_count": 0,
                    "__message_to_model__": "0 rows; try TOP 5000."}},
    ]
    r = validate_claims("We found 5000 member stars.", tool_results)
    assert not r.ok


def test_data_rows_still_populate_universe():
    # Regression guard: P0-a must NOT strip real numeric data values that
    # happen to sit in nested rows.
    from app.services.claim_validator import _iter_numeric_values
    payload = {"result": {"rows": [{"mag": 12.3}, {"mag": 13.1}]}}
    universe = set(_iter_numeric_values(payload))
    assert 12.3 in universe and 13.1 in universe
