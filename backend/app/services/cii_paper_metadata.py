"""PART AI #6 — paper-level lensing metadata for [CII] surveys.

ar5iv 表格里很少能稳定提取 magnification (μ) 列, 但**整篇 paper 的
sample 是不是 lensed 是公开常识**. 例如:
  - Bothwell+2013 (SPT [CII]) — 整个 sample 100% strongly lensed
  - Capak+2015 — z~6 starbursts, lensed
  - ALPINE / REBELS — high-z field galaxies, **不是** lensed sample
  - JADES / CEERS [CII] — field galaxies, 不是 lensed

这个 dict 给每篇 [CII] paper 一个 paper-level lensing 标签 (按 bibcode
查), `_normalize_line_measurements` 在不能从 table 提到 μ 列时按这个
dict fallback 给 row.is_lensed. fit_line_lfr 看到 is_lensed=True 但
mu_lens=None 的 row → 入 rejected, kind="lensed_no_mu_correction" —
强制用户手抓 mu_map 才能进 fit.

这是为了堵审稿人 "0 lensed sources detected" 那条硬伤 (#6) — 该数字
原来是 ALPINE table 没 μ 列就直接当 0 报, 不是真科学结论.
"""

from __future__ import annotations

from typing import Literal

LensingKind = Literal["all_sources_lensed", "no_lensing", "mixed"]


# Bibcode → lensing classification.
#
# 数据来源原则:
#   - "all_sources_lensed" 仅当 paper abstract / introduction 明确说
#     "all members are gravitationally lensed" / "lensed by foreground
#     cluster" / 用 SPT-SMG / cluster-lensed sample
#   - "no_lensing" 仅当 paper 本身是 field survey 不专门挑 lensed
#   - "mixed" 当 paper sample 部分 lensed (e.g. ALPINE 含个别已知
#     lensed 源 - Capak2015 那一支). 这种 row.is_lensed 不能由 paper-level
#     metadata fallback 决定, 必须从 table 抓
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
