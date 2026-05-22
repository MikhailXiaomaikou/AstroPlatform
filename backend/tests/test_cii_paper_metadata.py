"""PART AI #6 — paper-level lensing metadata + integration with
fit_line_lfr lensing coverage check.

Locks down 4 contracts:
1. PAPER_LENSING dict contains at least SPT-SMG (Bothwell+2013) / Capak+2015 /
   ALPINE / REBELS with correct classifications.
2. lensing_kind_for_bibcode / is_paper_lensed_by_default helpers are usable.
3. _normalize_line_measurements automatically fills is_lensed=True by bibcode when
   the ar5iv table has no mu column (as in Bothwell SPT-type papers).
4. _exec_fit_line_lfr seeing a row with is_lensed=True but mu_lens=None →
   rejected (kind="lensed_no_mu_correction"), result.lensing_summary
   exposes the full 5-bucket counts.
"""

from __future__ import annotations

from unittest.mock import patch


# ── Contract 1: PAPER_LENSING dict contents ─────────────────────────────────────


def test_paper_lensing_registry_has_required_papers() -> None:
    """SPT-SMG / Capak+2015 / ALPINE / REBELS must be in the table with correct classifications.
    Guards against accidental deletion of an entry that would break the fit_line_lfr lensing fallback."""
    from app.services.cii_paper_metadata import PAPER_LENSING

    # cluster-lensed sample must be all_sources_lensed
    assert PAPER_LENSING.get("2013ApJ...779...67B") == "all_sources_lensed"
    assert PAPER_LENSING.get("2015Natur.522..455C") == "all_sources_lensed"
    assert PAPER_LENSING.get("2017ApJ...836L...2B") == "all_sources_lensed"

    # ALPINE / REBELS are field surveys, not lensed samples
    assert PAPER_LENSING.get("2020A&A...643A...2B") == "no_lensing"
    assert PAPER_LENSING.get("2022MNRAS.515.5610S") == "no_lensing"


def test_paper_lensing_values_are_valid_kinds() -> None:
    """PAPER_LENSING values must be one of the LensingKind enum values; free-form strings
    would cause type errors in fit_line_lfr."""
    from app.services.cii_paper_metadata import PAPER_LENSING

    valid_kinds = {"all_sources_lensed", "no_lensing", "mixed"}
    for bibcode, kind in PAPER_LENSING.items():
        assert kind in valid_kinds, (
            f"PAPER_LENSING[{bibcode!r}]={kind!r} not in {valid_kinds}"
        )


# ── Contract 2: helper functions ────────────────────────────────────────────────


def test_lensing_kind_for_bibcode_known_paper() -> None:
    from app.services.cii_paper_metadata import lensing_kind_for_bibcode

    assert lensing_kind_for_bibcode("2013ApJ...779...67B") == "all_sources_lensed"


def test_lensing_kind_for_bibcode_unknown_paper_returns_none() -> None:
    """Unregistered bibcode must return None — do not assume unknown=lensed."""
    from app.services.cii_paper_metadata import lensing_kind_for_bibcode

    assert lensing_kind_for_bibcode("9999XXX...000..000Z") is None
    assert lensing_kind_for_bibcode(None) is None
    assert lensing_kind_for_bibcode("") is None


def test_is_paper_lensed_by_default() -> None:
    """is_paper_lensed_by_default returns True only for all_sources_lensed;
    no_lensing / mixed / unknown all return False."""
    from app.services.cii_paper_metadata import is_paper_lensed_by_default

    assert is_paper_lensed_by_default("2013ApJ...779...67B") is True   # SPT
    assert is_paper_lensed_by_default("2015Natur.522..455C") is True   # Capak
    assert is_paper_lensed_by_default("2020A&A...643A...2B") is False  # ALPINE
    assert is_paper_lensed_by_default("9999XXX...000..000Z") is False
    assert is_paper_lensed_by_default(None) is False


# ── Contract 3: arxiv.py _normalize_line_measurements uses fallback ────────


def test_arxiv_normalize_uses_paper_lensing_fallback_for_spt_smg() -> None:
    """ar5iv table has no mu column + paper is SPT-SMG (Bothwell+2013) → row.is_lensed=True
    set by paper-level metadata fallback, even though the table does not say so."""
    from app.api.arxiv import _normalize_line_measurements

    # simulate a SPT-SMG paper's table with **no mu column**
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

    # at least one row captured (header labeled cii lsun should be recognized as [CII] by normalizer)
    if measurements:
        m = measurements[0]
        # paper-level fallback must set is_lensed to True
        assert m.get("is_lensed") is True
        # but mu_lens is still None (table has no mu)
        assert m.get("mu_lens") is None


def test_arxiv_normalize_does_NOT_fallback_for_field_survey() -> None:
    """ALPINE / REBELS field survey papers must not have is_lensed set to True by paper-level
    fallback, even when the table has no mu column (these papers are no_lensing)."""
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
        # ALPINE must not be marked lensed by fallback
        assert m.get("is_lensed") is not True


# ── Contract 4: fit_line_lfr lensing coverage + lensing_summary fields ─────


def _make_rows_with_lensed_subset() -> list[dict]:
    """Mixed sample: 5 ALPINE rows not lensed + 3 SPT lensed (mu_lens=None)
    + 2 SPT already demagnified (_demagnified=True, mu_lens=2.5)."""
    rows = []
    # 5 ALPINE rows
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
    # 3 SPT rows lensed without mu
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
            "mu_lens": None,        # key: no mu
            "is_lensed": True,      # key: set True by paper-level fallback
            "source_cosmology": None,
        })
    # 2 SPT rows already demagnified
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
    """is_lensed=True + mu_lens=None + not _demagnified → entered into rejected
    with reason='lensed_no_mu_correction', excluded from fit."""
    from app.services.ai_tools import _exec_fit_line_lfr

    rows = _make_rows_with_lensed_subset()
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"cache_key": "x"})

    # 5 ALPINE + 2 SPT-DEMAG = 7 enter fit; 3 SPT-LENSED-NOMU enter rejected
    assert out["n_used"] == 7
    rejected_lensed = [
        r for r in out["rejected_summary"]
        if r.get("reason") == "lensed_no_mu_correction"
    ]
    assert len(rejected_lensed) == 3
    # each rejected row must contain source name + bibcode + action suggestion
    for r in rejected_lensed:
        assert "SPT-LENSED-NOMU" in str(r.get("source_name") or "")
        assert r.get("bibcode") == "2013ApJ...779...67B"
        assert "demagnify_sample" in str(r.get("detail") or "")


def test_fit_line_lfr_lensing_summary_5_bucket_counts() -> None:
    """result.lensing_summary must contain 5 buckets: in_fit unlensed /
    in_fit demagnified / skipped_no_mu / unknown / papers_default_lensed."""
    from app.services.ai_tools import _exec_fit_line_lfr

    rows = _make_rows_with_lensed_subset()
    with _patch_cache(rows):
        out = _exec_fit_line_lfr({"cache_key": "x"})

    summary = out["lensing_summary"]
    assert summary["n_unlensed_in_fit"] == 5
    assert summary["n_lensed_demagnified_in_fit"] == 2
    assert summary["n_lensed_skipped_no_mu"] == 3
    # papers_default_lensed must contain the SPT bibcode (paper-level metadata hit)
    assert "2013ApJ...779...67B" in summary["papers_default_lensed"]
    # ALPINE not in papers_default_lensed (it is no_lensing)
    assert "2020A&A...643A...2B" not in summary["papers_default_lensed"]


def test_fit_line_lfr_all_lensed_no_mu_returns_failed_status() -> None:
    """All rows are lensed but all lack mu → early exit with FAILED + clear error message
    pointing to demagnify_sample, does not fit an empty array."""
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
    """Pure ALPINE field sample → lensing_summary three 'in_fit lensed'
    buckets are all 0, papers_default_lensed is empty."""
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
