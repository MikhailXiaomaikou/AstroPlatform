"""Stage 6.3 spike unit tests (2026-05-20).

Does not mock real LLM calls (would require an API key); only tests module internals:
  - parse_llm_json: parse LLM JSON array output
  - verify_value_against_text: ±1% reverse-verification logic
  - verify_record: LLM record + parsed tables → ExtractedMeasurement
  - parse_html_tables: HTML → list of tables
"""

from __future__ import annotations

import pytest

from app.services.llm_paper_extractor import (
    ExtractedMeasurement,
    build_html_excerpt,
    parse_html_tables,
    parse_llm_json,
    verify_record,
    verify_value_against_text,
)


# ── verify_value_against_text ──────────────────────────────────────


def test_verify_value_matches_within_tolerance():
    ok, note = verify_value_against_text(235.0, "235.4 km/s")
    assert ok
    assert "matched" in note


def test_verify_value_matches_exact():
    ok, _ = verify_value_against_text(8.52, "8.52")
    assert ok


def test_verify_value_off_more_than_1pct():
    ok, note = verify_value_against_text(100.0, "200")
    assert not ok
    assert "no cell number within" in note


def test_verify_null_value_is_passed():
    ok, _ = verify_value_against_text(None, "anything")
    assert ok


def test_verify_no_numbers_in_cell():
    ok, note = verify_value_against_text(5.0, "n/a")
    assert not ok
    assert "no numbers" in note


def test_verify_picks_closest_when_multiple_numbers():
    # cell contains "235.4 ± 12.1" — 235.0 should match 235.4
    ok, note = verify_value_against_text(235.0, "235.4 ± 12.1")
    assert ok


# ── parse_llm_json ──────────────────────────────────────────────────


def test_parse_llm_json_bare_array():
    raw = '[{"source_name": "GN20"}]'
    out = parse_llm_json(raw)
    assert out == [{"source_name": "GN20"}]


def test_parse_llm_json_with_markdown_fence():
    raw = "```json\n[{\"a\": 1}]\n```"
    out = parse_llm_json(raw)
    assert out == [{"a": 1}]


def test_parse_llm_json_with_prose_around():
    # LLM occasionally adds prose before/after the array; we extract the first [...]
    raw = "Sure, here you go:\n[{\"x\": 2}]\nHope this helps."
    out = parse_llm_json(raw)
    assert out == [{"x": 2}]


def test_parse_llm_json_invalid_raises():
    import json
    with pytest.raises(json.JSONDecodeError):
        parse_llm_json("not even close")


# ── parse_html_tables ───────────────────────────────────────────────


def test_parse_html_tables_extracts_basic_table():
    html = """
    <table>
      <tr><th>name</th><th>z</th></tr>
      <tr><td>SourceA</td><td>5.5</td></tr>
      <tr><td>SourceB</td><td>6.2</td></tr>
    </table>
    """
    tables = parse_html_tables(html)
    assert len(tables) == 1
    assert tables[0][0] == ["name", "z"]
    assert tables[0][1] == ["SourceA", "5.5"]


def test_parse_html_tables_handles_multiple():
    html = "<table><tr><td>A</td></tr></table><table><tr><td>B</td></tr></table>"
    tables = parse_html_tables(html)
    assert len(tables) == 2


def test_parse_html_tables_empty_html():
    assert parse_html_tables("<html></html>") == []


# ── build_html_excerpt ──────────────────────────────────────────────


def test_build_html_excerpt_marks_tables():
    """Measurement-like tables are kept (score > 0)."""
    tables = [
        [["source", "z", "FWHM"], ["A", "1", "100"], ["B", "2", "200"]],
        [["x", "y"], ["1", "2"]],  # no measurement keyword, score=0, skipped
    ]
    excerpt = build_html_excerpt(tables)
    assert "Table 0" in excerpt
    assert "A | 1 | 100" in excerpt
    # Table 1 score=0, no measurement keyword → skipped
    assert "Table 1" not in excerpt


# ── score_table_relevance ──────────────────────────────────────────


def test_score_table_relevance_high_for_measurement_header():
    """ALPINE-style headers should score high."""
    from app.services.llm_paper_extractor import score_table_relevance

    table = [
        ["Name", "RA", "Dec", "z[CII]", "I[CII]", "FWHM", "L[CII]"],
        ["DEIMOS_873756", "...", "...", "4.5457", "...", "526", "9.56"],
    ]
    score = score_table_relevance(table)
    assert score >= 10  # multiple strong keyword hits


def test_score_table_relevance_zero_for_random_header():
    from app.services.llm_paper_extractor import score_table_relevance

    table = [["foo", "bar", "baz"], ["1", "2", "3"]]
    assert score_table_relevance(table) == 0


def test_score_table_relevance_empty():
    from app.services.llm_paper_extractor import score_table_relevance
    assert score_table_relevance([]) == 0


# ── is_low_value_table ─────────────────────────────────────────────


def test_is_low_value_table_filters_single_row():
    from app.services.llm_paper_extractor import is_low_value_table
    assert is_low_value_table([["header_only"]])


def test_is_low_value_table_filters_caption_only():
    """First row has only 1 cell → caption / metadata."""
    from app.services.llm_paper_extractor import is_low_value_table
    table = [["Table caption text here"], ["data row 1"], ["data row 2"]]
    assert is_low_value_table(table)


def test_is_low_value_table_filters_equation_table():
    """Many LaTeX equation markers → formula table."""
    from app.services.llm_paper_extractor import is_low_value_table
    table = [
        ["formula", "result"],
        ["\\frac{a}{b}", "\\sum_{i=0}^N"],
        ["\\int_0^1", "\\partial_x"],
    ]
    assert is_low_value_table(table)


def test_is_low_value_table_passes_real_data_table():
    from app.services.llm_paper_extractor import is_low_value_table
    table = [
        ["Name", "z[CII]", "FWHM", "L[CII]"],
        ["A", "4.5", "200", "9.5"],
        ["B", "5.2", "300", "9.8"],
    ]
    assert not is_low_value_table(table)


# ── build_html_excerpt v2 — score-based ordering ──────────────────


def test_build_html_excerpt_prefers_high_score_table():
    """ALPINE reproduction: real measurement table appears last, but build_excerpt should
    move it to the front for the LLM, not use index order."""
    from app.services.llm_paper_extractor import build_html_excerpt

    # 24 equation/metadata tables + 1 real measurement table at position 25
    formula_tables = [
        [["formula"], ["\\frac{a}{b}=1"], ["\\sum_i x_i"]]
        for _ in range(24)
    ]
    real_table = [
        ["Name", "z[CII]", "FWHM", "L[CII]"],
        ["GalA", "4.5", "200", "9.5"],
        ["GalB", "5.2", "300", "9.8"],
    ]
    tables = formula_tables + [real_table]
    excerpt = build_html_excerpt(tables, max_chars=2000)

    # real table idx is 24 (0-indexed); excerpt should contain Table 24
    assert "Table 24" in excerpt
    assert "GalA" in excerpt
    # formula tables should all be filtered out
    assert "Table 0" not in excerpt
    assert "Table 23" not in excerpt


def test_build_html_excerpt_returns_no_tables_message_when_all_filtered():
    """All low-value tables → do not return raw garbage; return a friendly message instead."""
    from app.services.llm_paper_extractor import build_html_excerpt
    tables = [
        [["caption"]],
        [["formula"], ["\\frac{a}{b}"], ["\\sum"]],
    ]
    excerpt = build_html_excerpt(tables)
    assert "no measurement-like tables found" in excerpt


# ── verify_record (integration: LLM record → ExtractedMeasurement) ──


@pytest.fixture
def sample_tables():
    """Mimics a paper table with header + 2 data rows.
    Table 0: source / FWHM / logL / z
    Row 0 (LLM view, header=0): source=GN20, FWHM=235.4, logL=9.84, z=4.055
    Row 1 (LLM view): source=HZ7, FWHM=110.2, logL=8.95, z=5.255
    """
    return [
        [
            ["source", "FWHM (km/s)", "log L[CII]", "z"],
            ["GN20", "235.4 ± 12.1", "9.84 ± 0.05", "4.055"],
            ["HZ7", "110.2 ± 8.3", "8.95 ± 0.07", "5.255"],
        ],
    ]


def test_verify_record_passes_when_numbers_match(sample_tables):
    record = {
        "source_name": "GN20",
        "fwhm_km_s": 235.0,  # within 1% of 235.4
        "log_luminosity": 9.84,
        "z": 4.055,
        "table_idx": 0,
        "row_idx": 0,  # LLM perspective, header excluded → actual HTML row 1
        "cell_provenance": {
            "fwhm_km_s": "235.4 ± 12.1",
            "log_luminosity": "9.84 ± 0.05",
            "z": "4.055",
        },
    }
    result = verify_record(record, sample_tables)
    assert result.validation_status == "passed"
    assert result.source_name == "GN20"
    assert result.z == 4.055


def test_verify_record_fails_when_fabricated_value(sample_tables):
    """LLM fabricated FWHM=999 but cell actually contains 235.4 → fail."""
    record = {
        "source_name": "GN20",
        "fwhm_km_s": 999.0,  # fabricated
        "log_luminosity": 9.84,
        "z": 4.055,
        "table_idx": 0,
        "row_idx": 0,
        "cell_provenance": {
            "fwhm_km_s": "235.4 ± 12.1",  # provenance not fabricated, but number doesn't match
            "log_luminosity": "9.84 ± 0.05",
            "z": "4.055",
        },
    }
    result = verify_record(record, sample_tables)
    assert result.validation_status == "failed_mismatch"
    assert any("fwhm_km_s" in note for note in result.validation_notes)


def test_verify_record_fails_when_row_out_of_range(sample_tables):
    record = {
        "source_name": "Phantom",
        "fwhm_km_s": 100.0,
        "log_luminosity": None,
        "z": None,
        "table_idx": 0,
        "row_idx": 99,  # does not exist
        "cell_provenance": {},
    }
    result = verify_record(record, sample_tables)
    assert result.validation_status == "failed_no_cell"


def test_verify_record_falls_back_when_llm_uses_dump_row_n_convention(sample_tables):
    """Stage 6.3 spike v3 (2026-05-20): codex reported ALPINE row=76 out-of-range — LLM
    switches to dump Row N convention (includes header) at the last row. verify_record
    should fallback to using row_idx directly as HTML idx (without +1)."""
    # sample_tables[0] has 3 rows: header + 2 data. LLM gives row=2 using dump convention
    # (Row 2 in dump = HZ7, the 2nd data row). +1 = 3 out-of-range, fallback 2 = HZ7.
    record = {
        "source_name": "HZ7",
        "fwhm_km_s": 110.0,
        "log_luminosity": 8.95,
        "z": 5.255,
        "table_idx": 0,
        "row_idx": 2,  # dump Row N convention (includes header)
        "cell_provenance": {
            "fwhm_km_s": "110.2 ± 8.3",
            "log_luminosity": "8.95 ± 0.07",
            "z": "5.255",
        },
    }
    result = verify_record(record, sample_tables)
    assert result.validation_status == "passed"
    assert result.source_name == "HZ7"


def test_verify_record_picks_higher_provenance_match_when_two_candidates_valid(sample_tables):
    """When both candidates (row_idx+1 and +0) are valid, use the row where cell_provenance
    matches more fields as the truth (eliminates ambiguity)."""
    # sample_tables[0]: [header, GN20, HZ7]
    # LLM gives row_idx=0 with HZ7 provenance:
    #   +1 → row 1 = GN20 (provenance mismatch)
    #   +0 → row 0 = header (provenance mismatch — header has no data)
    # But LLM output HZ7 numbers + HZ7 provenance; should we choose +1 or +0?
    # Both actually fail provenance check → fallback to first (+1 = GN20).
    # Then verify_value stage will fail (GN20 FWHM ≠ HZ7 FWHM).
    record = {
        "source_name": "HZ7",
        "fwhm_km_s": 110.0,
        "log_luminosity": 8.95,
        "z": 5.255,
        "table_idx": 0,
        "row_idx": 0,  # wrong row_idx
        "cell_provenance": {
            "fwhm_km_s": "110.2 ± 8.3",
            "log_luminosity": "8.95 ± 0.07",
        },
    }
    result = verify_record(record, sample_tables)
    # +1 lands on row 1 = GN20 (fwhm=235.4). LLM gave 110 → fail
    assert result.validation_status == "failed_mismatch"


def test_verify_record_null_fields_pass_through(sample_tables):
    """LLM marking null is honest behavior and should not fail."""
    record = {
        "source_name": "GN20",
        "fwhm_km_s": None,
        "log_luminosity": None,
        "z": 4.055,
        "table_idx": 0,
        "row_idx": 0,
        "cell_provenance": {"z": "4.055"},
    }
    result = verify_record(record, sample_tables)
    assert result.validation_status == "passed"
    assert result.fwhm_km_s is None
    assert result.log_luminosity is None
    assert result.z == 4.055


def test_verify_record_uses_row_text_when_provenance_missing(sample_tables):
    """When LLM provides no cell_provenance, fall back to full-row text reverse-verification."""
    record = {
        "source_name": "HZ7",
        "fwhm_km_s": 110.0,  # within 1% of 110.2 anywhere in row
        "log_luminosity": None,
        "z": None,
        "table_idx": 0,
        "row_idx": 1,
        "cell_provenance": {},
    }
    result = verify_record(record, sample_tables)
    assert result.validation_status == "passed"


def test_extracted_measurement_to_dict():
    m = ExtractedMeasurement(
        source_name="X",
        fwhm_km_s=100.0,
        log_luminosity=None,
        z=5.0,
        table_idx=0,
        row_idx=2,
        cell_provenance={"z": "5.0"},
        validation_status="passed",
        validation_notes=["z: matched"],
    )
    d = m.to_dict()
    assert d["source_name"] == "X"
    assert d["validation_status"] == "passed"
    assert d["cell_provenance"] == {"z": "5.0"}


# ── helper integration: _extract_and_cache_paper_measurements ──
# (2026-05-20 moved down) The original _exec_extract_paper_measurements_with_llm top-level tool
# has been merged into fit_line_lfr; this tests the internal helper form — same LLM extraction
# + ±1% reverse-verification + cache write semantics, but the signature is
# (arxiv_id, api_key, python_session_id, fields), no longer accepts an inp dict,
# and return value no longer carries __message_to_model__ (fit_line_lfr wrapper handles that).


@pytest.mark.asyncio
async def test_helper_writes_passed_rows_to_session_cache(monkeypatch):
    """Passed records converted to fit_line_lfr-compatible row schema + written to session-scoped cache."""
    from app.services import ai_tools

    fake_records = [
        ExtractedMeasurement(
            source_name="DEIMOS_873756",
            fwhm_km_s=526.0,
            log_luminosity=9.56,
            z=4.5457,
            table_idx=25,
            row_idx=2,
            cell_provenance={"fwhm_km_s": "526±13", "z": "4.5457"},
            validation_status="passed",
            validation_notes=["all matched"],
        ),
        ExtractedMeasurement(
            source_name="FabricatedSource",
            fwhm_km_s=999.0,
            log_luminosity=None,
            z=None,
            table_idx=25,
            row_idx=99,
            cell_provenance={},
            validation_status="failed_mismatch",
            validation_notes=["fwhm: no cell number within 1%"],
        ),
    ]

    stored: dict[str, Any] = {}

    def fake_store(key, session_id, value):
        stored[f"{key}:{session_id}"] = value

    monkeypatch.setattr(
        "app.services.llm_paper_extractor.extract_with_llm_and_verify",
        lambda arxiv_id, fields, api_key: fake_records,
    )
    monkeypatch.setattr(
        "app.services.ai_tools.literature.store_session_results",
        fake_store,
    )

    out = await ai_tools._extract_and_cache_paper_measurements(
        "2002.00962",
        api_key="sk-ant-fake",
        python_session_id="sid_abc",
    )
    assert out["success"] is True
    assert out["passed_count"] == 1
    assert out["failed_mismatch_count"] == 1
    assert out["failed_no_cell_count"] == 0
    # only passed rows go into the cache
    assert len(out["line_measurements"]) == 1
    assert out["line_measurements"][0]["source_name"] == "DEIMOS_873756"
    # session-scoped key written
    assert "latest_literature_tables:sid_abc" in stored
    cached = stored["latest_literature_tables:sid_abc"]
    assert len(cached["line_measurements"]) == 1
    # extraction_method 标记可追踪
    assert cached["line_measurements"][0]["extraction_method"] == "llm_with_cell_reverify"
    # rejected 仍 surface 在 result 给 LLM (warn) 但不进 cache
    assert any(r["source_name"] == "FabricatedSource" for r in out["rejected_rows"])


@pytest.mark.asyncio
async def test_helper_returns_error_on_missing_api_key():
    from app.services import ai_tools

    out = await ai_tools._extract_and_cache_paper_measurements(
        "2002.00962",
        api_key="",
        python_session_id="sid",
    )
    assert out["success"] is False
    assert out["error_class"] == "missing_api_key"


@pytest.mark.asyncio
async def test_helper_returns_error_on_missing_arxiv_id():
    from app.services import ai_tools

    out = await ai_tools._extract_and_cache_paper_measurements(
        "",
        api_key="sk-ant-fake",
        python_session_id="sid",
    )
    assert out["success"] is False
    assert out["error_class"] == "missing_argument"


@pytest.mark.asyncio
async def test_helper_handles_zero_passed(monkeypatch):
    """LLM 全编 → 0 passed, cache 不写, 返回 passed_count=0 + line_measurements=[]."""
    from app.services import ai_tools

    fake_records = [
        ExtractedMeasurement(
            source_name="Phantom",
            fwhm_km_s=999.0,
            log_luminosity=None,
            z=None,
            table_idx=0,
            row_idx=99,
            cell_provenance={},
            validation_status="failed_no_cell",
            validation_notes=["out of range"],
        ),
    ]
    stored: dict[str, Any] = {}
    monkeypatch.setattr(
        "app.services.llm_paper_extractor.extract_with_llm_and_verify",
        lambda arxiv_id, fields, api_key: fake_records,
    )
    monkeypatch.setattr(
        "app.services.ai_tools.literature.store_session_results",
        lambda key, session_id, value: stored.setdefault(
            f"{key}:{session_id}", value
        ),
    )

    out = await ai_tools._extract_and_cache_paper_measurements(
        "0000.0000",
        api_key="sk-ant-fake",
        python_session_id="sid",
    )
    assert out["success"] is True
    assert out["passed_count"] == 0
    assert out["line_measurements"] == []
    # 0 passed 不写 cache
    assert stored == {}
    # rejected_rows 仍 surface 给上层 fit_line_lfr / LLM
    assert len(out["rejected_rows"]) == 1
    assert out["rejected_rows"][0]["validation_status"] == "failed_no_cell"


@pytest.mark.asyncio
async def test_helper_propagates_extraction_exception(monkeypatch):
    """spike module 抛异常 (e.g. HTTP timeout) → helper 返 error_class."""
    from app.services import ai_tools

    def boom(arxiv_id, fields, api_key):
        raise RuntimeError("network timeout to ar5iv")

    monkeypatch.setattr(
        "app.services.llm_paper_extractor.extract_with_llm_and_verify",
        boom,
    )

    out = await ai_tools._extract_and_cache_paper_measurements(
        "2002.00962",
        api_key="sk-ant-fake",
        python_session_id="sid",
    )
    assert out["success"] is False
    assert out["error_class"] == "llm_extraction_failed"
    assert "network timeout" in out["error"]


# ── fit_line_lfr integration: arxiv_id 早退路径 ──


@pytest.mark.asyncio
async def test_fit_line_lfr_arxiv_id_no_api_key_early_exit():
    """fit_line_lfr 收到 arxiv_id 但 BYOK key 为空 → FAILED 早退."""
    from app.services import ai_tools

    out = await ai_tools._exec_fit_line_lfr(
        {"arxiv_id": "2002.00962"},
        python_session_id="sid",
        api_key="",
    )
    assert out["success"] is False
    assert out["__tool_status__"] == "FAILED"
    assert out["error_class"] == "missing_api_key"
    assert out["arxiv_id"] == "2002.00962"


@pytest.mark.asyncio
async def test_fit_line_lfr_arxiv_id_zero_passed_early_exit(monkeypatch):
    """fit_line_lfr 收到 arxiv_id, LLM 抽出全部 fail → EMPTY 早退 + no_passed_measurements."""
    from app.services import ai_tools

    fake_records = [
        ExtractedMeasurement(
            source_name="Phantom",
            fwhm_km_s=999.0,
            log_luminosity=None,
            z=None,
            table_idx=0,
            row_idx=99,
            cell_provenance={},
            validation_status="failed_mismatch",
            validation_notes=["no cell match"],
        ),
    ]
    monkeypatch.setattr(
        "app.services.llm_paper_extractor.extract_with_llm_and_verify",
        lambda arxiv_id, fields, api_key: fake_records,
    )
    monkeypatch.setattr(
        "app.services.ai_tools.literature.store_session_results",
        lambda _key, _session_id, _value: None,
    )

    out = await ai_tools._exec_fit_line_lfr(
        {"arxiv_id": "2002.00962"},
        python_session_id="sid",
        api_key="sk-ant-fake",
    )
    assert out["success"] is False
    assert out["__tool_status__"] == "EMPTY"
    assert out["error_class"] == "no_passed_measurements"
    assert "extraction_summary" in out


# 让顶部 Any 可用 (tool integration tests above 用 dict[str, Any])
from typing import Any  # noqa: E402
