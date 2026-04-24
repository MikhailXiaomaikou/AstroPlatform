"""M1 验收: literature_tables cache schema v2 + 向后兼容.

M1 的三条核心契约:
1. 新写入的 cache payload 必须带 schema_version=2 + 每条 line_measurement
   至少包含 v2 要求的 5 个字段(允许值为 None).
2. 从老 v1 payload(字段缺失)读出来的 rows 自动补齐 v2 字段,老 session
   不会 KeyError.
3. arxiv extractor 在表格里识别 μ 列时,能正确填 mu_lens + 推断
   is_lensed.  没有 μ 列时所有新字段都是 None(不伪造).
"""

from app.services.ai_tools import (
    _V2_MEASUREMENT_KEYS,
    _LITERATURE_SCHEMA_VERSION,
    _literature_table_cache_payload,
    _measurement_rows_from_cache_payload,
    _normalize_measurement_to_v2,
)
from app.api.arxiv import _normalize_line_measurements


def test_schema_version_is_v2():
    assert _LITERATURE_SCHEMA_VERSION == 2


def test_v1_row_upgraded_on_cache_write():
    """老的 extract 结果(不带新字段)进 cache 时被自动升级."""
    v1_row = {
        "source_name": "SRC-1", "redshift": 5.5, "line_id": "[CII]",
        "log_luminosity": 9.1, "fwhm_km_s": 200.0,
        # 注意:没有 fwhm_err_km_s / log_luminosity_err / mu_lens /
        # is_lensed / source_cosmology — 这些是 v2 新增字段
    }
    payload = _literature_table_cache_payload(
        {"line_measurements": [v1_row]},
        cache_key="latest_literature_tables",
    )
    assert payload["schema_version"] == 2
    row = payload["line_measurements"][0]
    for key in _V2_MEASUREMENT_KEYS:
        assert key in row, f"v2 schema missing key {key}"
        assert row[key] is None, f"missing v1 field {key} should default to None"


def test_v2_row_preserved():
    """已经是 v2 的 row 原样保留,不被覆盖."""
    v2_row = {
        "source_name": "SRC-2", "redshift": 6.0, "line_id": "[CII]",
        "log_luminosity": 9.5, "log_luminosity_err": 0.15,
        "fwhm_km_s": 350.0, "fwhm_err_km_s": 40.0,
        "mu_lens": 2.3, "is_lensed": True,
        "source_cosmology": {"name": "Planck18", "H0": 67.4, "Om0": 0.3156},
    }
    payload = _literature_table_cache_payload(
        {"line_measurements": [v2_row]},
        cache_key="lit_x",
    )
    row = payload["line_measurements"][0]
    assert row["log_luminosity_err"] == 0.15
    assert row["fwhm_err_km_s"] == 40.0
    assert row["mu_lens"] == 2.3
    assert row["is_lensed"] is True
    assert row["source_cosmology"]["name"] == "Planck18"


def test_old_cache_read_path_auto_upgrades():
    """M1 契约:_measurement_rows_from_cache_payload 读老 v1 cache 也给 v2 schema.

    模拟场景:用户 session 里有一个 schema_version=1 的 payload,fit_line_lfr
    读它时不该 KeyError.
    """
    old_v1_cache = {
        "schema_version": 1,
        "kind": "literature_tables",
        "line_measurements": [
            {"source_name": "A", "log_luminosity": 9.0, "fwhm_km_s": 180.0},
            {"source_name": "B", "log_luminosity": 9.4, "fwhm_km_s": 250.0},
        ],
    }
    rows = _measurement_rows_from_cache_payload(old_v1_cache)
    assert len(rows) == 2
    for row in rows:
        for key in _V2_MEASUREMENT_KEYS:
            assert key in row
            assert row[key] is None


def test_bare_list_payload_also_upgraded():
    """_measurement_rows_from_cache_payload 支持 list 输入(不是 dict)."""
    rows = _measurement_rows_from_cache_payload([
        {"source_name": "X", "log_luminosity": 8.5, "fwhm_km_s": 140.0},
    ])
    assert len(rows) == 1
    for key in _V2_MEASUREMENT_KEYS:
        assert key in rows[0]


def test_normalize_measurement_to_v2_idempotent():
    """多次跑 normalize 不会破坏已有的 v2 字段."""
    row = {"source_name": "C", "mu_lens": 5.0, "is_lensed": True}
    once = _normalize_measurement_to_v2(row)
    twice = _normalize_measurement_to_v2(once)
    assert once == twice
    assert twice["mu_lens"] == 5.0


def test_arxiv_extractor_picks_up_mu_column():
    """M1 extractor 契约:表格里有 mu 列时 mu_lens 被填,is_lensed 推断正确."""
    tables = [{
        "columns": ["Source", "z", "log L[CII]", "FWHM", "mu"],
        "rows": [
            ["A100", "5.1", "9.2", "200", "1.0"],   # unlensed (μ=1)
            ["A101", "5.5", "9.6", "300", "4.5"],   # lensed (μ=4.5)
            ["A102", "6.0", "8.8", "150", "15.0"],  # strongly lensed
        ],
        "caption": "[CII] sample",
        "label": "Tab1",
    }]
    ms = _normalize_line_measurements(tables)
    assert len(ms) == 3
    assert ms[0]["mu_lens"] == 1.0
    assert ms[0]["is_lensed"] is False
    assert ms[1]["mu_lens"] == 4.5
    assert ms[1]["is_lensed"] is True
    assert ms[2]["mu_lens"] == 15.0
    assert ms[2]["is_lensed"] is True
    # source_cosmology 是 paper-level,arxiv.py 层保持 None
    for m in ms:
        assert m["source_cosmology"] is None


def test_arxiv_extractor_no_mu_column_leaves_fields_none():
    """表格没有 μ 列时,mu_lens / is_lensed 都是 None(不伪造)."""
    tables = [{
        "columns": ["Source", "z", "log L[CII]", "FWHM"],
        "rows": [["S1", "5.0", "9.0", "200"]],
        "caption": "[CII] sample",
        "label": "Tab1",
    }]
    ms = _normalize_line_measurements(tables)
    assert len(ms) == 1
    assert ms[0]["mu_lens"] is None
    assert ms[0]["is_lensed"] is None
    assert ms[0]["source_cosmology"] is None
