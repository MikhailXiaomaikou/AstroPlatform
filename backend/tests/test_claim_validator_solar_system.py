"""Tests for claim_validator solar_system extensions (M0 Commit 5, 2026-05-18)."""

from __future__ import annotations


# ── numeric regex coverage ─────────────────────────────────────────────────


def test_numeric_pattern_semi_major_axis_au():
    from app.services.claim_validator import _PATTERNS

    pattern = dict(_PATTERNS)["semi_major_axis_au"]
    assert pattern.search("a = 1.271 au")
    assert pattern.search("a = 2.77 AU")
    assert not pattern.search("apparent magnitude = 14.6 mag")


def test_numeric_pattern_eccentricity():
    from app.services.claim_validator import _PATTERNS

    pattern = dict(_PATTERNS)["eccentricity"]
    assert pattern.search("e = 0.8898")
    assert pattern.search("eccentricity = 0.191")
    assert pattern.search("e = 0")
    # eccentricity must be < 1 — 1.5 should not match (avoid false positives like emcee etc.)
    assert not pattern.search("e = 1.5")


def test_numeric_pattern_inclination():
    from app.services.claim_validator import _PATTERNS

    pattern = dict(_PATTERNS)["orbital_inclination_deg"]
    assert pattern.search("i = 22.26°")
    assert pattern.search("inclination = 3.33 deg")
    assert pattern.search("i = 5 degrees")


def test_numeric_pattern_diameter_km():
    from app.services.claim_validator import _PATTERNS

    pattern = dict(_PATTERNS)["diameter_km"]
    assert pattern.search("D = 525 km")
    assert pattern.search("diameter = 16.8 km")


def test_numeric_pattern_albedo():
    from app.services.claim_validator import _PATTERNS

    pattern = dict(_PATTERNS)["albedo_pV"]
    assert pattern.search("p_V = 0.42")
    assert pattern.search("geometric albedo = 0.090")
    assert pattern.search("albedo = 0.20")


def test_numeric_pattern_afrho():
    from app.services.claim_validator import _PATTERNS

    pattern = dict(_PATTERNS)["Afrho_cm"]
    assert pattern.search("Af = 500 cm")
    assert pattern.search("Afρ = 2316 cm")


def test_numeric_pattern_moid():
    from app.services.claim_validator import _PATTERNS

    pattern = dict(_PATTERNS)["MOID_au"]
    assert pattern.search("MOID = 0.00025 au")
    assert pattern.search("MOID = 0.012 AU")


def test_numeric_pattern_phase_angle():
    from app.services.claim_validator import _PATTERNS

    pattern = dict(_PATTERNS)["phase_angle_deg"]
    assert pattern.search("α = 30°")
    assert pattern.search("phase angle = 45 deg")


# ── canonical bibcodes ─────────────────────────────────────────────


def test_solar_system_bibcodes_complete():
    """All 14 keystone references are in the dict, each value contains a tool-name hint."""
    from app.services.claim_validator import SOLAR_SYSTEM_CANONICAL_BIBCODES

    expected = {
        "1989aste.conf..524B", "1996DPS....28.2504G", "2003CeMDA..85..145K",
        "2012A&A...537A.128R", "2019JOSS....4.1426M", "2009Icar..202..160D",
        "2010A&A...513A..46D", "2010A&A...510A..43C", "1984AJ.....89..579A",
        "2011ApJ...743..156M", "1998Icar..131..291H", "2002Icar..158..329M",
        "2009M&PS...44.1853G", "2019AJ....157...98G",
    }
    assert set(SOLAR_SYSTEM_CANONICAL_BIBCODES.keys()) == expected
    # each hint is non-empty + mentions a tool or paper subject
    for bibcode, hint in SOLAR_SYSTEM_CANONICAL_BIBCODES.items():
        assert hint, f"{bibcode} has empty hint"
        assert "—" in hint or "-" in hint


def test_solar_system_manifest_block_note_triggers_on_bowell():
    """Bowell 1989 bibcode not returned by tool, validator cites it → block note shows tool hint."""
    from app.services.claim_validator import (
        CitationViolation, _solar_system_manifest_block_note,
    )

    v = CitationViolation(
        kind="invalid_bibcode", match_text="1989aste.conf..524B", line_number=1,
    )
    note = _solar_system_manifest_block_note([v])
    assert "1989aste.conf..524B" in note
    assert "compute_hg_magnitude" in note  # hints which tool to call


def test_solar_system_manifest_block_note_empty_when_no_match():
    from app.services.claim_validator import (
        CitationViolation, _solar_system_manifest_block_note,
    )

    v = CitationViolation(
        kind="invalid_bibcode", match_text="2024Nature.000..0X", line_number=1,
    )
    assert _solar_system_manifest_block_note([v]) == ""


def test_solar_system_note_appears_in_blocked_citation_text():
    """End-to-end: blocked_citation_reply_text output contains solar_system hint."""
    from app.services.claim_validator import (
        CitationViolation, blocked_citation_reply_text,
    )

    v = CitationViolation(
        kind="invalid_bibcode", match_text="2010A&A...513A..46D",  # DAMIT
        line_number=1,
    )
    text = blocked_citation_reply_text([v])
    assert "2010A&A...513A..46D" in text
    assert "query_damit_shape_model" in text


def test_cosmology_note_still_works_after_solar_system_added():
    """Adding the solar_system note must not break the cosmology note path."""
    from app.services.claim_validator import (
        CitationViolation, blocked_citation_reply_text,
    )

    # a cosmology preset bibcode
    v = CitationViolation(
        kind="invalid_bibcode", match_text="2020A&A...641A...6P",  # Planck18
        line_number=1,
    )
    text = blocked_citation_reply_text([v])
    assert "2020A&A...641A...6P" in text
    # cosmology hint should still appear
    assert "cosmology" in text.lower()
