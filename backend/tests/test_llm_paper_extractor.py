"""Stage 6.3 spike unit tests (2026-05-20).

不 mock 真 LLM call (会要 API key), 只测 module 内部:
  - parse_llm_json: 解析 LLM 输出的 JSON array
  - verify_value_against_text: ±1% 反查逻辑
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
    # cell 含 "235.4 ± 12.1" — 235.0 应匹配 235.4
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
    # LLM 偶尔会在 array 前后加废话, 我们提第一个 [...]
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
    """measurement-like 表会被保留 (score > 0)."""
    tables = [
        [["source", "z", "FWHM"], ["A", "1", "100"], ["B", "2", "200"]],
        [["x", "y"], ["1", "2"]],  # 没 measurement keyword, score=0 跳
    ]
    excerpt = build_html_excerpt(tables)
    assert "Table 0" in excerpt
    assert "A | 1 | 100" in excerpt
    # Table 1 score=0 没 measurement keyword → 跳过
    assert "Table 1" not in excerpt


# ── score_table_relevance ──────────────────────────────────────────


def test_score_table_relevance_high_for_measurement_header():
    """ALPINE 类 header 应该高分."""
    from app.services.llm_paper_extractor import score_table_relevance

    table = [
        ["Name", "RA", "Dec", "z[CII]", "I[CII]", "FWHM", "L[CII]"],
        ["DEIMOS_873756", "...", "...", "4.5457", "...", "526", "9.56"],
    ]
    score = score_table_relevance(table)
    assert score >= 10  # 多个 strong keyword 命中


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
    """第 1 行只 1 cell → caption / metadata."""
    from app.services.llm_paper_extractor import is_low_value_table
    table = [["Table caption text here"], ["data row 1"], ["data row 2"]]
    assert is_low_value_table(table)


def test_is_low_value_table_filters_equation_table():
    """大量 LaTeX equation markers → formula table."""
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
    """ALPINE 复现: 真 measurement 表在后面, 但 build_excerpt 应该把它
    排到最前送给 LLM, 而不是按 idx 顺序送 budget."""
    from app.services.llm_paper_extractor import build_html_excerpt

    # 24 个 equation/metadata 表 + 第 25 个真 measurement 表
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

    # 真表 idx 是 24 (0-indexed). excerpt 应该含 Table 24
    assert "Table 24" in excerpt
    assert "GalA" in excerpt
    # formula 表应该全被 filter 掉
    assert "Table 0" not in excerpt
    assert "Table 23" not in excerpt


def test_build_html_excerpt_returns_no_tables_message_when_all_filtered():
    """全是 low-value 表 → 不返 raw garbage, 返友好提示."""
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
        "row_idx": 0,  # LLM 视角, 不含 header → 实际 HTML row 1
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
    """LLM 编了一个 FWHM=999 但 cell 里实际是 235.4 → fail."""
    record = {
        "source_name": "GN20",
        "fwhm_km_s": 999.0,  # 编的
        "log_luminosity": 9.84,
        "z": 4.055,
        "table_idx": 0,
        "row_idx": 0,
        "cell_provenance": {
            "fwhm_km_s": "235.4 ± 12.1",  # provenance 没编, 但数字跟它对不上
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
        "row_idx": 99,  # 不存在
        "cell_provenance": {},
    }
    result = verify_record(record, sample_tables)
    assert result.validation_status == "failed_no_cell"


def test_verify_record_falls_back_when_llm_uses_dump_row_n_convention(sample_tables):
    """Stage 6.3 spike v3 (2026-05-20): codex 报 ALPINE row=76 越界 — LLM
    在 last row 切换到 dump Row N convention (含 header). verify_record
    应该 fallback to row_idx 直接当 HTML idx (without +1)."""
    # sample_tables[0] 3 行: header + 2 数据. LLM 给 row=2 用 dump convention
    # (Row 2 in dump = HZ7 第 2 数据行). +1 = 3 越界, fallback 2 = HZ7.
    record = {
        "source_name": "HZ7",
        "fwhm_km_s": 110.0,
        "log_luminosity": 8.95,
        "z": 5.255,
        "table_idx": 0,
        "row_idx": 2,  # dump Row N convention (含 header)
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
    """两个 candidate (row_idx+1 和 +0) 都 valid 时, 用 cell_provenance
    匹配更多的那行作 truth (排除 ambiguity)."""
    # sample_tables[0]: [header, GN20, HZ7]
    # LLM 给 row_idx=0 with HZ7 provenance:
    #   +1 → row 1 = GN20 (provenance 不匹配)
    #   +0 → row 0 = header (provenance 不匹配 — header 无数据)
    # 但 LLM 输出 HZ7 数字 + HZ7 provenance, 应该选 +1 还是 +0?
    # 实际两个都 fail provenance 检查 → fallback to first (+1 = GN20).
    # 然后 verify_value 阶段会 fail (GN20 FWHM ≠ HZ7 FWHM).
    record = {
        "source_name": "HZ7",
        "fwhm_km_s": 110.0,
        "log_luminosity": 8.95,
        "z": 5.255,
        "table_idx": 0,
        "row_idx": 0,  # 错误 row_idx
        "cell_provenance": {
            "fwhm_km_s": "110.2 ± 8.3",
            "log_luminosity": "8.95 ± 0.07",
        },
    }
    result = verify_record(record, sample_tables)
    # +1 落在 row 1 = GN20 (fwhm=235.4). LLM 给 110 → fail
    assert result.validation_status == "failed_mismatch"


def test_verify_record_null_fields_pass_through(sample_tables):
    """LLM 标 null 是 honest behavior, 不该 fail."""
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
    """LLM 没给 cell_provenance, 我们退回到整行文本反查."""
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
