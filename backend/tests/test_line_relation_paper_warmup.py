"""PART AI Phase 3 — line_relation_paper_warmup registry contracts.

Phase 3 of PART AI follow-up: extend the [CII] LFR pattern (paper-level
metadata + lensing fallback) to four other line families:

  - CO(1-0) — Solomon-style brightness-temperature LFR
  - Hα broad-line — Vestergaard & Peterson 2006 BH mass scaling
  - [OIII] 5007 vel-disp — Nelson+2014 σ-M
  - HCN(1-0) — Gao & Solomon 2004 dense-gas SFR

This file is **data-only** (no logic). The fitter `fit_line_lfr` and
the unit converter `luminosity_units.convert_row_luminosity_inplace`
are already polymorphic in `line_id`, so the metadata is the only
new piece needed.

Locks the contract:

1. All 4 line families are present in DEFAULT_PAPER_ARXIV_IDS.
2. Each family has an entry in WORKFLOW_NOTES with a non-empty note.
3. PAPER_LENSING_NON_CII covers each family with at least 1 paper.
4. lensing helper symmetric with cii_paper_metadata API.
5. ν_rest exists in luminosity_units for every family that needs L'.
"""

from __future__ import annotations



# ── Contract 1: 4 line families registered ────────────────────────────


def test_four_line_families_registered() -> None:
    from app.services.line_relation_paper_warmup import (
        DEFAULT_PAPER_ARXIV_IDS,
        list_line_families,
    )

    expected = {"CO_1-0", "Halpha_broad", "OIII_5007_velocity_dispersion", "HCN_1-0"}
    assert set(list_line_families()) == expected
    assert set(DEFAULT_PAPER_ARXIV_IDS.keys()) == expected


# ── Contract 2: workflow notes exist + non-empty + AI-actionable ──────


def test_workflow_notes_non_empty_for_each_family() -> None:
    from app.services.line_relation_paper_warmup import (
        WORKFLOW_NOTES,
        list_line_families,
        workflow_note_for,
    )

    for family in list_line_families():
        note = workflow_note_for(family)
        assert family in WORKFLOW_NOTES
        assert len(note) >= 100, f"workflow note for {family} too short ({len(note)} chars)"
        # must tell AI which luminosity_kind to use (L_solar or L_prime)
        assert "luminosity_kind" in note
        # must tell AI which line_id string to use when calling fit_line_lfr
        assert "fit_line_lfr" in note


def test_workflow_notes_specify_correct_luminosity_kind_per_family() -> None:
    """CO(1-0) and HCN(1-0) are mm-wave lines; CO LFR literature convention uses L' (brightness temperature).
    Halpha and [OIII] are optical lines; literature uses L_solar."""
    from app.services.line_relation_paper_warmup import workflow_note_for

    assert "L_prime" in workflow_note_for("CO_1-0")
    assert "L_prime" in workflow_note_for("HCN_1-0")
    assert "L_solar" in workflow_note_for("Halpha_broad")
    assert "L_solar" in workflow_note_for("OIII_5007_velocity_dispersion")


# ── Contract 3: PAPER_LENSING_NON_CII covers each family ──────────────


def test_lensing_dict_has_at_least_one_paper_per_family() -> None:
    """Each line family must have at least 1 paper's lensing default registered,
    to prevent AI from falling back to unsafe 'unknown' for unfamiliar bibcodes."""
    from app.services.line_relation_paper_warmup import PAPER_LENSING_NON_CII

    # Solomon CO / Sargent CO MS / Bothwell SMG CO
    assert "1997ApJ...478..144S" in PAPER_LENSING_NON_CII   # Solomon+1997
    assert "2014ApJ...793...19S" in PAPER_LENSING_NON_CII   # Sargent+2014
    assert "2013MNRAS.429.3047B" in PAPER_LENSING_NON_CII   # Bothwell SMG CO

    # Vestergaard & Peterson 2006 (Hα BH mass)
    assert "2006ApJ...641..689V" in PAPER_LENSING_NON_CII

    # Nelson+2014 [OIII] σ-M
    assert "2014ApJ...796..145N" in PAPER_LENSING_NON_CII

    # Gao & Solomon 2004 / Crocker+2012 HCN
    assert "2004ApJ...606..271G" in PAPER_LENSING_NON_CII
    assert "2012MNRAS.421..872C" in PAPER_LENSING_NON_CII


def test_lensing_kind_values_are_valid() -> None:
    """LensingDefault must be one of the enum values, not a free-form string."""
    from app.services.line_relation_paper_warmup import PAPER_LENSING_NON_CII

    valid = {"all_sources_lensed", "no_lensing", "mixed"}
    for bibcode, kind in PAPER_LENSING_NON_CII.items():
        assert kind in valid, f"{bibcode}={kind!r} not in {valid}"


# ── Contract 4: lensing helper API symmetric with [CII] version ───────


def test_is_non_cii_paper_lensed_by_default_returns_true_for_smg() -> None:
    from app.services.line_relation_paper_warmup import is_non_cii_paper_lensed_by_default

    assert is_non_cii_paper_lensed_by_default("2013MNRAS.429.3047B") is True


def test_is_non_cii_paper_lensed_returns_false_for_local_samples() -> None:
    from app.services.line_relation_paper_warmup import is_non_cii_paper_lensed_by_default

    # Local AGN sample not lensed
    assert is_non_cii_paper_lensed_by_default("2006ApJ...641..689V") is False
    # SDSS [OIII] not lensed
    assert is_non_cii_paper_lensed_by_default("2014ApJ...796..145N") is False
    # Solomon local LIRG not lensed
    assert is_non_cii_paper_lensed_by_default("1997ApJ...478..144S") is False


def test_is_non_cii_paper_lensed_returns_false_for_unknown() -> None:
    """Unregistered bibcode must return False; must not assume lensed by default."""
    from app.services.line_relation_paper_warmup import is_non_cii_paper_lensed_by_default

    assert is_non_cii_paper_lensed_by_default("9999XXX...000..000Z") is False
    assert is_non_cii_paper_lensed_by_default(None) is False
    assert is_non_cii_paper_lensed_by_default("") is False


# ── Contract 5: ν_rest exists in luminosity_units for every L' family ──


def test_co_1_0_rest_freq_in_luminosity_units() -> None:
    """CO(1-0) in LINE_REST_FREQ_GHZ must equal 115.27120 GHz (NIST)."""
    from app.services.luminosity_units import LINE_REST_FREQ_GHZ

    assert "CO(1-0)" in LINE_REST_FREQ_GHZ
    assert abs(LINE_REST_FREQ_GHZ["CO(1-0)"] - 115.27120) < 1e-3


def test_hcn_1_0_rest_freq_in_luminosity_units() -> None:
    """HCN(1-0) ν_rest = 88.63185 GHz (Gao & Solomon 2004)."""
    from app.services.luminosity_units import LINE_REST_FREQ_GHZ

    assert "HCN(1-0)" in LINE_REST_FREQ_GHZ
    assert abs(LINE_REST_FREQ_GHZ["HCN(1-0)"] - 88.63185) < 1e-3


def test_halpha_in_luminosity_units_for_optional_lprime_path() -> None:
    """Halpha must be in LINE_REST_FREQ_GHZ even though it normally uses L_solar,
    to avoid crashing if AI mistakenly passes L_prime."""
    from app.services.luminosity_units import LINE_REST_FREQ_GHZ

    assert "Halpha" in LINE_REST_FREQ_GHZ
    # Halpha ~456805 GHz (~656 nm)
    assert LINE_REST_FREQ_GHZ["Halpha"] > 100000


# ── Contract 6: warmup arxiv lists currently empty but helper does not panic ──────────


def test_warmup_arxiv_ids_returns_empty_tuple_for_unverified_families() -> None:
    """The 4 new line families have no locally verified papers yet; helper should return empty tuple
    rather than raise. Add entries once admin has preloaded and verified them."""
    from app.services.line_relation_paper_warmup import (
        list_line_families,
        warmup_arxiv_ids_for,
    )

    for family in list_line_families():
        ids = warmup_arxiv_ids_for(family)
        assert isinstance(ids, tuple)
        # currently all empty; this assertion will need updating once entries are added
        assert ids == ()


def test_warmup_arxiv_ids_for_unknown_family_returns_empty() -> None:
    """Unknown family returns empty, does not raise."""
    from app.services.line_relation_paper_warmup import warmup_arxiv_ids_for

    # type: ignore — intentionally using an unregistered family string
    assert warmup_arxiv_ids_for("X_99-100") == ()  # type: ignore[arg-type]


# ── Contract 7: API public surface unchanged ──────────────────────────


def test_module_exports_complete() -> None:
    """__all__ should export all public helpers for type checker / IDE autocomplete."""
    from app.services import line_relation_paper_warmup as mod

    expected_exports = {
        "DEFAULT_PAPER_ARXIV_IDS",
        "PAPER_LENSING_NON_CII",
        "WORKFLOW_NOTES",
        "LineFamily",
        "LensingDefault",
        "list_line_families",
        "warmup_arxiv_ids_for",
        "lensing_kind_for_non_cii_bibcode",
        "is_non_cii_paper_lensed_by_default",
        "workflow_note_for",
    }
    assert set(mod.__all__) == expected_exports
