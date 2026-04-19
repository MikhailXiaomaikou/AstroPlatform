"""Tests for the zero-fabrication gate (Phase 1 / R2).

Locks the contract that any numeric astronomical claim in an AI reply is
verified against the turn's tool_results, and uncited claims are flagged
so the agent loop can regenerate or block the reply.
"""

from __future__ import annotations

import pytest

from app.services.claim_validator import (
    blocked_reply_text,
    build_regeneration_prompt,
    extract_claims,
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
