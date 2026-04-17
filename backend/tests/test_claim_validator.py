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
