"""PART AI #6 — paper-level lensing metadata + integration with
fit_line_lfr lensing coverage check.

锁住 4 个契约:
1. PAPER_LENSING dict 至少含 SPT-SMG (Bothwell+2013) / Capak+2015 /
   ALPINE / REBELS 的正确分类.
2. lensing_kind_for_bibcode / is_paper_lensed_by_default helper 可用.
3. _normalize_line_measurements 在 ar5iv 表无 μ 列时, 按 bibcode 自动
   fill is_lensed=True (Bothwell SPT 那种 paper).
4. _exec_fit_line_lfr 看到 is_lensed=True 但 mu_lens=None 的 row →
   rejected (kind="lensed_no_mu_correction"), result.lensing_summary
   暴露完整 5-bucket 计数.
"""

from __future__ import annotations

from unittest.mock import patch


# ── 契约 1: PAPER_LENSING dict 内容 ─────────────────────────────────────


def test_paper_lensing_registry_has_required_papers() -> None:
    """SPT-SMG / Capak+2015 / ALPINE / REBELS 必须在表里且分类正确.
    防止哪天有人误删某条目导致 fit_line_lfr lensing fallback 失效."""
    from app.services.cii_paper_metadata import PAPER_LENSING

    # cluster-lensed sample 必须 all_sources_lensed
    assert PAPER_LENSING.get("2013ApJ...779...67B") == "all_sources_lensed"
    assert PAPER_LENSING.get("2015Natur.522..455C") == "all_sources_lensed"
    assert PAPER_LENSING.get("2017ApJ...836L...2B") == "all_sources_lensed"

    # ALPINE / REBELS 是 field survey, 不是 lensed sample
    assert PAPER_LENSING.get("2020A&A...643A...2B") == "no_lensing"
    assert PAPER_LENSING.get("2022MNRAS.515.5610S") == "no_lensing"


def test_paper_lensing_values_are_valid_kinds() -> None:
    """PAPER_LENSING value 必须是 LensingKind enum 之一, 不能写成自由
    字符串造成 fit_line_lfr 类型错误."""
    from app.services.cii_paper_metadata import PAPER_LENSING

    valid_kinds = {"all_sources_lensed", "no_lensing", "mixed"}
    for bibcode, kind in PAPER_LENSING.items():
        assert kind in valid_kinds, (
            f"PAPER_LENSING[{bibcode!r}]={kind!r} not in {valid_kinds}"
        )


# ── 契约 2: helper 函数 ────────────────────────────────────────────────


def test_lensing_kind_for_bibcode_known_paper() -> None:
    from app.services.cii_paper_metadata import lensing_kind_for_bibcode

    assert lensing_kind_for_bibcode("2013ApJ...779...67B") == "all_sources_lensed"


def test_lensing_kind_for_bibcode_unknown_paper_returns_none() -> None:
    """未注册 bibcode 必须返回 None — 不要假设 unknown=lensed."""
    from app.services.cii_paper_metadata import lensing_kind_for_bibcode

    assert lensing_kind_for_bibcode("9999XXX...000..000Z") is None
    assert lensing_kind_for_bibcode(None) is None
    assert lensing_kind_for_bibcode("") is None


def test_is_paper_lensed_by_default() -> None:
    """is_paper_lensed_by_default 只对 all_sources_lensed 返 True;
    no_lensing / mixed / unknown 都返 False."""
    from app.services.cii_paper_metadata import is_paper_lensed_by_default

    assert is_paper_lensed_by_default("2013ApJ...779...67B") is True   # SPT
    assert is_paper_lensed_by_default("2015Natur.522..455C") is True   # Capak
    assert is_paper_lensed_by_default("2020A&A...643A...2B") is False  # ALPINE
    assert is_paper_lensed_by_default("9999XXX...000..000Z") is False
    assert is_paper_lensed_by_default(None) is False


# ── 契约 3: arxiv.py _normalize_line_measurements 用 fallback ────────


def test_arxiv_normalize_uses_paper_lensing_fallback_for_spt_smg() -> None:
    """ar5iv 表无 μ 列 + paper 是 SPT-SMG (Bothwell+2013) → row.is_lensed=True
    被 paper-level metadata fallback 设上, 即便 table 没说."""
    from app.api.arxiv import _normalize_line_measurements

    # 模拟一个 SPT-SMG paper 的 table, **没有 μ 列**
    fake_tables = [{
        "table_id": "tbl_a1",
        "label": "Table A1",
        "rows": [
            {
                "source_name": "SPT0103-45",
                "redshift": "3.1",
                "log_l_cii_lsun": "9.2",
                "fwhm_km_s": "350",
            }
        ],
        "header": ["source_name", "redshift", "log_l_cii_lsun", "fwhm_km_s"],
        "citation": {
            "bibcode": "2013ApJ...779...67B",  # Bothwell+2013 SPT
            "arxiv_id": "1304.4256",
        },
    }]
    measurements = _normalize_line_measurements(fake_tables)

    # 至少抓到一条 (header 标 cii lsun 应该被 normalizer 识别成 [CII])
    if measurements:
        m = measurements[0]
        # paper-level fallback 必须把 is_lensed 设 True
        assert m.get("is_lensed") is True
        # 但 mu_lens 仍是 None (table 没 μ)
        assert m.get("mu_lens") is None


def test_arxiv_normalize_does_NOT_fallback_for_field_survey() -> None:
    """ALPINE / REBELS field survey paper 即使 table 没 μ 列, is_lensed
    也不能被 paper-level fallback 设 True (这些 paper 是 no_lensing)."""
    from app.api.arxiv import _normalize_line_measurements

    fake_tables = [{
        "table_id": "tbl1",
        "label": "Table 1",
        "rows": [
            {
                "source_name": "ALPINE-12345",
                "redshift": "5.0",
                "log_l_cii_lsun": "8.5",
                "fwhm_km_s": "200",
            }
        ],
        "header": ["source_name", "redshift", "log_l_cii_lsun", "fwhm_km_s"],
        "citation": {
            "bibcode": "2020A&A...643A...2B",  # Béthermin+2020 ALPINE (no_lensing)
            "arxiv_id": "2002.00962",
        },
    }]
    measurements = _normalize_line_measurements(fake_tables)
    if measurements:
        m = measurements[0]
        # ALPINE 不能被 fallback 标 lensed
        assert m.get("is_lensed") is not True


# ── 契约 4: fit_line_lfr lensing coverage + lensing_summary 字段 ─────


def _make_rows_with_lensed_subset() -> list[dict]:
    """混合样本: 5 行 ALPINE 不 lensed + 3 行 SPT lensed (mu_lens=None)
    + 2 行 SPT 已 demagnify (_demagnified=True, mu_lens=2.5)."""
    rows = []
    # 5 行 ALPINE
    for i in range(5):
        rows.append({
            "source_name": f"ALPINE-{i}",
            "redshift": 5.0 + 0.1 * i,
            "line_id": "[CII]",
            "log_luminosity": 8.5 + 0.1 * i,
            "fwhm_km_s": 200.0 + 10.0 * i,
            "quality_flags": [],
            "citation": {"bibcode": "2020A&A...643A...2B"},
            "bibcode": "2020A&A...643A...2B",
            "arxiv_id": "2002.00962",
            "log_luminosity_err": None,
            "fwhm_err_km_s": None,
            "mu_lens": None,
            "is_lensed": False,
            "source_cosmology": None,
        })
    # 3 行 SPT lensed without mu
    for i in range(3):
        rows.append({
            "source_name": f"SPT-LENSED-NOMU-{i}",
            "redshift": 3.0 + 0.5 * i,
            "line_id": "[CII]",
            "log_luminosity": 9.5 + 0.1 * i,
            "fwhm_km_s": 350.0,
            "quality_flags": [],
            "citation": {"bibcode": "2013ApJ...779...67B"},
            "bibcode": "2013ApJ...779...67B",
            "arxiv_id": "1304.4256",
            "log_luminosity_err": None,
            "fwhm_err_km_s": None,
            "mu_lens": None,        # 关键: 没 mu
            "is_lensed": True,      # 关键: paper-level fallback 设 True
            "source_cosmology": None,
        })
    # 2 行 SPT 已 demagnify
    for i in range(2):
        rows.append({
            "source_name": f"SPT-DEMAG-{i}",
            "redshift": 3.0 + 0.5 * i,
            "line_id": "[CII]",
            "log_luminosity": 9.0,
            "fwhm_km_s": 350.0,
            "quality_flags": [],
            "citation": {"bibcode": "2013ApJ...779...67B"},
            "bibcode": "2013ApJ...779...67B",
            "arxiv_id": "1304.4256",
            "log_luminosity_err": None,
            "fwhm_err_km_s": None,
            "mu_lens": 2.5,
            "is_lensed": True,
            "_demagnified": True,
            "source_cosmology": None,
        })
    return rows


def _patch_cache(rows: list[dict]):
    return patch(
        "app.services.ai_tools._resolve_literature_measurement_cache",
        return_value=(rows, "latest_literature_tables"),
    )


def test_fit_line_lfr_skips_lensed_no_mu_rows_with_clear_reason() -> None:
    """is_lensed=True + mu_lens=None + not _demagnified → 入 rejected
    with reason='lensed_no_mu_correction', 不进 fit."""
    from app.services.ai_tools import _exec_fit_line_lfr

    rows = _make_rows_with_lensed_subset()
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"cache_key": "x"})

    # 5 ALPINE + 2 SPT-DEMAG = 7 进 fit; 3 SPT-LENSED-NOMU 入 rejected
    assert out["n_used"] == 7
    rejected_lensed = [
        r for r in out["rejected_summary"]
        if r.get("reason") == "lensed_no_mu_correction"
    ]
    assert len(rejected_lensed) == 3
    # 每条 rejected 必须含具体源名 + bibcode + 行动建议
    for r in rejected_lensed:
        assert "SPT-LENSED-NOMU" in str(r.get("source_name") or "")
        assert r.get("bibcode") == "2013ApJ...779...67B"
        assert "demagnify_sample" in str(r.get("detail") or "")


def test_fit_line_lfr_lensing_summary_5_bucket_counts() -> None:
    """result.lensing_summary 必须含 5 个 bucket: in_fit unlensed /
    in_fit demagnified / skipped_no_mu / unknown / papers_default_lensed."""
    from app.services.ai_tools import _exec_fit_line_lfr

    rows = _make_rows_with_lensed_subset()
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"cache_key": "x"})

    summary = out["lensing_summary"]
    assert summary["n_unlensed_in_fit"] == 5
    assert summary["n_lensed_demagnified_in_fit"] == 2
    assert summary["n_lensed_skipped_no_mu"] == 3
    # papers_default_lensed 必须含 SPT bibcode (paper-level metadata 命中)
    assert "2013ApJ...779...67B" in summary["papers_default_lensed"]
    # ALPINE 不在 papers_default_lensed (它是 no_lensing)
    assert "2020A&A...643A...2B" not in summary["papers_default_lensed"]


def test_fit_line_lfr_all_lensed_no_mu_returns_failed_status() -> None:
    """所有 row 都 lensed 但都缺 mu → 早退 FAILED + 明确错误消息指向
    demagnify_sample, 不 fit 空 array."""
    from app.services.ai_tools import _exec_fit_line_lfr

    rows = []
    for i in range(5):
        rows.append({
            "source_name": f"SPT-{i}",
            "redshift": 3.0,
            "line_id": "[CII]",
            "log_luminosity": 9.5,
            "fwhm_km_s": 350.0,
            "quality_flags": [],
            "citation": {"bibcode": "2013ApJ...779...67B"},
            "bibcode": "2013ApJ...779...67B",
            "log_luminosity_err": None,
            "fwhm_err_km_s": None,
            "mu_lens": None,
            "is_lensed": True,
            "source_cosmology": None,
        })
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"cache_key": "x"})

    assert out["success"] is False
    assert out["__tool_status__"] == "FAILED"
    assert out["error_class"] == "all_rows_lensed_no_mu"
    assert "demagnify_sample" in out["error"]
    assert out["n_lensed_skipped_no_mu"] == 5


def test_fit_line_lfr_no_lensed_rows_lensing_summary_zeros() -> None:
    """纯 ALPINE field sample → lensing_summary 三个 'in_fit lensed'
    bucket 都是 0, papers_default_lensed 是空."""
    from app.services.ai_tools import _exec_fit_line_lfr

    rows = []
    for i in range(6):
        rows.append({
            "source_name": f"ALPINE-{i}",
            "redshift": 5.0 + 0.1 * i,
            "line_id": "[CII]",
            "log_luminosity": 8.5 + 0.05 * i,
            "fwhm_km_s": 200.0 + 5.0 * i,
            "quality_flags": [],
            "citation": {"bibcode": "2020A&A...643A...2B"},
            "bibcode": "2020A&A...643A...2B",
            "log_luminosity_err": None,
            "fwhm_err_km_s": None,
            "mu_lens": None,
            "is_lensed": False,
            "source_cosmology": None,
        })
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"cache_key": "x"})

    summary = out["lensing_summary"]
    assert summary["n_unlensed_in_fit"] == 6
    assert summary["n_lensed_demagnified_in_fit"] == 0
    assert summary["n_lensed_skipped_no_mu"] == 0
    assert summary["papers_default_lensed"] == []
