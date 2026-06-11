"""Paper-level warmup metadata for line-luminosity-FWHM relations
beyond [CII].

Phase 3 of the PART AI follow-up sweep extends the [CII] LFR pattern
(`cii_paper_metadata.py` + `fit_line_lfr` + `luminosity_units`) to
four additional line families. The fitter (`fit_line_lfr`) is already
polymorphic in `line_id`, and `luminosity_units.LINE_REST_FREQ_GHZ`
already carries the rest frequencies. The remaining gap is paper-level
metadata: which arXiv IDs are worth pre-warming, what lensing default
applies per paper, and a short workflow note for the SYSTEM_PROMPT.

This file is **data**, not logic. It is NOT yet wired into any live
path — no module under `app/api` or `app/services` imports it, so its
registry and helpers are currently exercised only by
`tests/test_line_relation_paper_warmup.py`. The intended (not-yet-built)
integration points are:
- `admin_literature.preload_*_caches` admin endpoints (extend with
  generic line-id parameter)
- `arxiv.py _normalize_line_measurements` indirectly via
  `is_paper_lensed_by_default` lookup once that helper is generalized
- `chat.py` SYSTEM_PROMPT line-relation idiom section
Until those land, adding a paper here has no effect on extraction,
normalization, or the prompt.

Adding a new paper to the registry requires running
`POST /api/admin/literature/preload_caches` with the candidate arxiv
id locally first and confirming `len(payload["line_measurements"]) > 0`.
Do NOT add an arxiv id from memory; that mistake produced the false
PAPER_LENSING entries that PART AG C4 had to clean up.
"""

from __future__ import annotations

from typing import Literal

LineFamily = Literal[
    "CO_1-0",
    "Halpha_broad",
    "OIII_5007_velocity_dispersion",
    "HCN_1-0",
]

LensingDefault = Literal["all_sources_lensed", "no_lensing", "mixed"]


# ── Paper warmup arxiv-id lists per line family ────────────────────────
#
# CONVENTION: only include arxiv ids whose ar5iv tables we have verified
# (locally, via the admin preload endpoint) yield > 0 line measurements
# through the full normalizer. Pending entries belong in DOC comments,
# not in the dict.

DEFAULT_PAPER_ARXIV_IDS: dict[LineFamily, tuple[str, ...]] = {
    # CO(1-0) ν_rest = 115.27120 GHz; Solomon+1997 is the canonical CO LFR
    # paper that the [CII] LFR comparison literature follows. Sargent+2014
    # extended to z=0-3 main-sequence galaxies.
    #
    # Pending verification (do NOT add until preload returns > 0 rows):
    #   - 1404.0233  Sargent+2014 (z=0-3 CO MS)
    #   - 1207.5365  Bothwell+2013 SMG CO compilation
    "CO_1-0": (),

    # Hα broad-line (BL AGN reverberation mapping context). Vestergaard
    # & Peterson 2006 is the authoritative single-epoch BH mass scaling
    # relation; the "FWHM" here is broad-Hα FWHM, not host kinematics.
    #
    # Pending verification:
    #   - astro-ph/0605678  Vestergaard & Peterson 2006
    "Halpha_broad": (),

    # [OIII] 5007 narrow-line velocity dispersion as black-hole mass
    # estimator (Nelson+2014; the local σ-M relation for AGN host
    # bulges). The "FWHM" here is the [OIII] line FWHM after subtracting
    # the broad component.
    "OIII_5007_velocity_dispersion": (),

    # HCN(1-0) ν_rest = 88.63185 GHz; Gao & Solomon 2004 established the
    # dense-gas star-formation law via HCN luminosity. Extended in Crocker+
    # 2012 (89 nearby galaxies) and Krumholz & Thompson 2007 to ULIRGs.
    #
    # Pending verification:
    #   - astro-ph/0407001  Gao & Solomon 2004 (HCN-IR relation)
    #   - 1204.6253        Crocker+2012 HCN survey
    "HCN_1-0": (),
}


# ── Per-paper lensing default (extends cii_paper_metadata.PAPER_LENSING
# beyond the [CII] family) ─────────────────────────────────────────────
#
# Reading rule: for any paper bibcode in this dict, the corresponding
# line-relation row's `is_lensed` is set to True by default if the
# paper is `all_sources_lensed`, regardless of whether the ar5iv table
# exposed a μ column. This implements the same fallback as
# `cii_paper_metadata.is_paper_lensed_by_default` for non-[CII] papers.

PAPER_LENSING_NON_CII: dict[str, LensingDefault] = {
    # ── CO(1-0) lensed samples ────────────────────────────────────
    # Bothwell+2013 SPT SMG CO compilation (lensed by foreground
    # cluster, same physical sources as the [CII] entry but the CO
    # paper has its own bibcode).
    "2013MNRAS.429.3047B": "all_sources_lensed",
    # Solomon+1997 — local LIRG/ULIRG sample, not gravitationally lensed
    "1997ApJ...478..144S": "no_lensing",
    # Sargent+2014 — z=0-3 main-sequence galaxies, no lensing selection
    "2014ApJ...793...19S": "no_lensing",

    # ── Hα broad-line AGN samples ─────────────────────────────────
    # Vestergaard & Peterson 2006 — local AGN sample, no lensing
    "2006ApJ...641..689V": "no_lensing",

    # ── [OIII] 5007 σ-M samples ───────────────────────────────────
    # Nelson+2014 SDSS AGN host σ — no lensing
    "2014ApJ...796..145N": "no_lensing",

    # ── HCN(1-0) samples ──────────────────────────────────────────
    # Gao & Solomon 2004 local sample, no lensing
    "2004ApJ...606..271G": "no_lensing",
    # Crocker+2012 nearby HCN survey, no lensing
    "2012MNRAS.421..872C": "no_lensing",
}


# ── Workflow notes per line family ─────────────────────────────────────
#
# These short notes are intended for the SYSTEM_PROMPT's line-relation
# idiom section, telling the AI which `line_id` / `luminosity_kind`
# pairs are appropriate for each line family. The text is plain prose
# (markdown is OK) so it can be inserted verbatim.

WORKFLOW_NOTES: dict[LineFamily, str] = {
    "CO_1-0": (
        "**CO(1-0) LFR (Solomon 1992 brightness-temperature form)**: "
        "use `fit_line_lfr(line_id=\"CO(1-0)\", luminosity_kind=\"L_prime\", "
        "cache_keys=[<warmed CO arxiv ids>])`. The CO LFR literature uses "
        "L' (K km/s pc^2) as the y-axis by convention; do NOT compare "
        "intercepts of an L_solar fit against published CO LFR slopes "
        "without converting first. ν_rest = 115.27120 GHz is auto-applied "
        "by `luminosity_units.convert_row_luminosity_inplace`."
    ),
    "Halpha_broad": (
        "**Hα broad-line AGN BH-mass scaling (Vestergaard & Peterson "
        "2006-style)**: use `fit_line_lfr(line_id=\"Halpha\", "
        "luminosity_kind=\"L_solar\")`. The y-axis is "
        "log L_Hα,broad / L_sun and the x-axis is log FWHM_Hα. The "
        "result `intercept` is the M_BH calibration zero point; quote "
        "it ONLY in tandem with the Hβ-derived virial coefficient if the "
        "user is computing BH masses, not just an empirical relation."
    ),
    "OIII_5007_velocity_dispersion": (
        "**[OIII] 5007 narrow-line σ as σ★ proxy (Nelson+2014)**: use "
        "`fit_line_lfr(line_id=\"[OIII]5007\", luminosity_kind=\"L_solar\")`. "
        "Note: the literature uses σ_[OIII] (velocity dispersion) NOT "
        "FWHM as the kinematic axis; the platform's `fwhm_km_s` field "
        "must be filled with σ × sqrt(8 ln 2) ≈ σ × 2.355 for direct "
        "comparison, AND the prose must explicitly say 'FWHM converted "
        "from σ' so the reader knows."
    ),
    "HCN_1-0": (
        "**HCN(1-0) dense-gas star-formation law (Gao & Solomon 2004)**: "
        "use `fit_line_lfr(line_id=\"HCN(1-0)\", luminosity_kind=\"L_prime\")`. "
        "ν_rest = 88.63185 GHz. The Gao-Solomon relation is L_IR vs "
        "L'_HCN, not FWHM-based; using `fit_line_lfr` requires the user "
        "to have explicit FWHM values per source — many HCN samples only "
        "report integrated L'_HCN. If the cache has no FWHM column, "
        "the fit returns rejected_summary kind='missing_numeric_values'."
    ),
}


# ── Public helpers ─────────────────────────────────────────────────────


def list_line_families() -> tuple[LineFamily, ...]:
    """All registered non-[CII] line-relation families."""
    return tuple(DEFAULT_PAPER_ARXIV_IDS.keys())


def warmup_arxiv_ids_for(family: LineFamily) -> tuple[str, ...]:
    """Verified arxiv ids whose ar5iv tables yield line measurements
    for the given line family. May be empty if no paper is verified
    yet — caller should not block on warmup."""
    return DEFAULT_PAPER_ARXIV_IDS.get(family, ())


def lensing_kind_for_non_cii_bibcode(bibcode: str | None) -> LensingDefault | None:
    """Paper-level lensing classification for non-[CII] papers.

    Returns None when the paper is not in the registry; callers should
    not assume lensing status in that case (do NOT default to 'lensed').
    """
    if not bibcode or not isinstance(bibcode, str):
        return None
    return PAPER_LENSING_NON_CII.get(bibcode.strip())


def is_non_cii_paper_lensed_by_default(bibcode: str | None) -> bool:
    """True iff the paper is in the registry as `all_sources_lensed`.

    Symmetric with `cii_paper_metadata.is_paper_lensed_by_default` so
    callers can call both for unified lensing fallback handling.
    """
    return lensing_kind_for_non_cii_bibcode(bibcode) == "all_sources_lensed"


def workflow_note_for(family: LineFamily) -> str:
    """Plain-text workflow note for SYSTEM_PROMPT injection."""
    return WORKFLOW_NOTES.get(family, "")


__all__ = [
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
]
