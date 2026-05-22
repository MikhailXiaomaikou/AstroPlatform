"""PART AI #6 — paper-level lensing metadata for [CII] surveys.

The magnification (mu) column is rarely extractable reliably from ar5iv tables,
but **whether a paper's entire sample is lensed is public knowledge**. For example:
  - Bothwell+2013 (SPT [CII]) — entire sample 100% strongly lensed
  - Capak+2015 — z~6 starbursts, lensed
  - ALPINE / REBELS — high-z field galaxies, **not** a lensed sample
  - JADES / CEERS [CII] — field galaxies, not lensed

This dict assigns a paper-level lensing label to each [CII] paper (looked up by
bibcode). When `_normalize_line_measurements` cannot extract a mu column from the
table, it falls back to this dict to set row.is_lensed. When fit_line_lfr sees a
row with is_lensed=True but mu_lens=None, the row is moved to rejected with
kind="lensed_no_mu_correction" — forcing the user to retrieve the mu_map before
the source can enter the fit.

This closes the reviewer-visible bug "0 lensed sources detected" (#6) — that
number was previously reported because the ALPINE table had no mu column and it
was treated as 0, which is not a valid scientific conclusion.
"""

from __future__ import annotations

from typing import Literal

LensingKind = Literal["all_sources_lensed", "no_lensing", "mixed"]


# Bibcode → lensing classification.
#
# Data sourcing principles:
#   - "all_sources_lensed" only when the paper abstract / introduction
#     explicitly states "all members are gravitationally lensed" / "lensed by
#     a foreground cluster" / uses an SPT-SMG / cluster-lensed sample
#   - "no_lensing" only when the paper itself is a field survey and does not
#     specifically select lensed sources
#   - "mixed" when the paper sample is partially lensed (e.g. ALPINE contains
#     some individually known lensed sources from the Capak+2015 sub-sample).
#     In this case row.is_lensed cannot be decided by paper-level metadata
#     fallback; it must be extracted from the table directly.
PAPER_LENSING: dict[str, LensingKind] = {
    # ── ALPINE / REBELS / similar field surveys ────────────────────
    # Béthermin+2020 ALPINE — 124 sources, field-selected, no lensing
    # selection. (Note: Capak+2015 sources reused inside ALPINE compilations
    # ARE lensed, but Béthermin+2020 itself is field-survey).
    "2020A&A...643A...2B": "no_lensing",
    "2020A&A...643A...1L": "no_lensing",   # Le Fèvre+2020 ALPINE
    # REBELS Schouws+2022 + Inami+2022 — z~7 field-selected
    "2022MNRAS.515.5610S": "no_lensing",
    "2022ApJ...940...90I": "no_lensing",

    # ── Cluster-lensed [CII] samples (SPT, Capak) ──────────────────
    # Bothwell+2013 SPT-SMG — all sources gravitationally lensed by
    # foreground cluster
    "2013ApJ...779...67B": "all_sources_lensed",
    # Capak+2015 z~6 LBGs — sample selected behind cluster lenses
    "2015Natur.522..455C": "all_sources_lensed",
    # Spilker+2016 SPT lensed
    "2016ApJ...826..112S": "all_sources_lensed",
    # Marrone+2018 SPT z~6.9 — lensed
    "2018Natur.553...51M": "all_sources_lensed",

    # ── Cluster-lensed individual targets ──────────────────────────
    # Bradac+2017 RXJ1347:1216 — single lensed galaxy (was visible in
    # paper #1 reproducer test as "RXJ1347:1216 ... 5.0 ± 0.3 Lensed galaxy")
    "2017ApJ...836L...2B": "all_sources_lensed",
}


def lensing_kind_for_bibcode(bibcode: str | None) -> LensingKind | None:
    """Return paper-level lensing classification for a given bibcode.

    Returns None if the paper is not in our curated registry.
    Caller (e.g. _normalize_line_measurements) treats None as "unknown,
    don't override what the table said".
    """
    if not bibcode or not isinstance(bibcode, str):
        return None
    return PAPER_LENSING.get(bibcode.strip())


def is_paper_lensed_by_default(bibcode: str | None) -> bool:
    """True iff the paper is `all_sources_lensed` per registry.

    Used by `_normalize_line_measurements` as a fallback when the paper
    table did not expose a μ column.
    """
    return lensing_kind_for_bibcode(bibcode) == "all_sources_lensed"


# Exposed for tests + paper_generator audit panels.
__all__ = [
    "PAPER_LENSING",
    "lensing_kind_for_bibcode",
    "is_paper_lensed_by_default",
    "LensingKind",
]
