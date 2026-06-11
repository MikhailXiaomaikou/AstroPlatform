"""raw_only recovery via user-confirmed column mapping (2026-06-11).

Preflight showed 2/4 representative papers land in raw_only: tables ARE
extracted but the pattern matcher can't recognize the measurement columns —
previously a dead end. Locks:

1. A synthetic table whose headers defeat every pattern yields 0 rows.
2. The same table + a user-confirmed column_mapping yields the rows, with
   values verbatim from the cells and luminosity_inferred_log_from ==
   "user_column_mapping".
3. Mapping by 0-based index and by header name both resolve; a mapping that
   doesn't fit the table is ignored (no crash, no partial application).
4. table_id targets one table only.
5. Exec-level: _exec_extract_literature_tables flips raw_only →
   measurement_ready and stamps column_mapping_source="user_confirmed".
"""
from __future__ import annotations

import asyncio

from app.api.arxiv import (
    _normalize_line_measurements,
    _resolve_mapped_column,
)

# Headers chosen to defeat every synonym pattern in arxiv.py:
# "Obj" (not source/object/name/id), "Spec. velocity ref" (not z*),
# "Brightness (dex)" (not log L*), "Width" (not fwhm/linewidth/dv).
_HOSTILE_TABLE = {
    "table_id": "html_1",
    "name": "Table 1",
    "caption": "",
    "columns": ["Obj", "Spec. velocity ref", "Brightness (dex)", "Width"],
    "rows": [
        ["GAL-1", "4.55", "8.91", "310"],
        ["GAL-2", "5.10", "9.42", "525"],
    ],
    "row_citations": [],
}

_MAPPING = {
    "source_name": "Obj",
    "redshift": 1,                       # by 0-based index
    "log_luminosity": "Brightness (dex)",
    "fwhm_km_s": "Width",
}


def test_hostile_headers_yield_zero_rows_without_mapping():
    assert _normalize_line_measurements([_HOSTILE_TABLE]) == []


def test_mapping_rescues_rows_with_verbatim_values():
    rows = _normalize_line_measurements([_HOSTILE_TABLE], column_mapping=_MAPPING)
    assert len(rows) == 2
    first = rows[0]
    assert first["source_name"] == "GAL-1"
    assert first["redshift"] == 4.55
    assert first["log_luminosity"] == 8.91
    assert first["fwhm_km_s"] == 310.0
    assert first["luminosity_inferred_log_from"] == "user_column_mapping"


def test_mapping_that_does_not_fit_is_ignored():
    bad = dict(_MAPPING)
    bad["fwhm_km_s"] = "No Such Column"
    assert _normalize_line_measurements([_HOSTILE_TABLE], column_mapping=bad) == []


def test_table_id_targets_one_table():
    other = dict(_HOSTILE_TABLE)
    other["table_id"] = "html_2"
    # Mapping pinned to html_2: html_1 stays unrecognized, html_2 rescues.
    rows = _normalize_line_measurements(
        [_HOSTILE_TABLE, other], column_mapping=_MAPPING, table_id="html_2",
    )
    assert len(rows) == 2  # only html_2's two rows
    # Without table_id, both tables resolve → 4 rows.
    rows_all = _normalize_line_measurements(
        [_HOSTILE_TABLE, other], column_mapping=_MAPPING,
    )
    assert len(rows_all) == 4


def test_resolve_mapped_column_forms():
    cols = ["Obj", "Spec. velocity ref", "Brightness (dex)", "Width"]
    assert _resolve_mapped_column(cols, "Obj") == 0
    assert _resolve_mapped_column(cols, "obj") == 0          # case-insensitive
    assert _resolve_mapped_column(cols, 2) == 2              # int index
    assert _resolve_mapped_column(cols, "3") == 3            # digit string
    assert _resolve_mapped_column(cols, "brightness dex") == 2  # key-normalized
    assert _resolve_mapped_column(cols, "nope") is None
    assert _resolve_mapped_column(cols, 9) is None           # out of range
    assert _resolve_mapped_column(cols, True) is None        # bool is not an index


def test_exec_extract_flips_raw_only_to_measurement_ready(monkeypatch):
    import app.services.ai_tools as ai_tools

    payload = {
        "arxiv_id": "9999.00001",
        "title": "Synthetic hostile-header paper",
        "tables": [_HOSTILE_TABLE],
        "line_measurements": [],   # the cached extraction found nothing
        # The real fetch-time payload carries these status fields — the
        # mapping rerun must refresh them, not parrot the stale raw_only.
        "extraction_status": "raw_only",
        "normalization_status": "no_line_measurement_schema",
        "warnings": [],
    }

    async def fake_cached(raw_id: str) -> dict:
        return payload

    monkeypatch.setattr(ai_tools, "_cached_extract_arxiv_tables_payload", fake_cached)

    out_raw = asyncio.run(ai_tools._exec_extract_literature_tables(
        {"arxiv_id": "9999.00001"}, "colmap-test",
    ))
    assert out_raw["extraction_status"] == "raw_only"
    assert out_raw["fit_ready"] is False

    out_mapped = asyncio.run(ai_tools._exec_extract_literature_tables(
        {"arxiv_id": "9999.00001", "column_mapping": _MAPPING}, "colmap-test",
    ))
    assert out_mapped["extraction_status"] == "measurement_ready"
    assert out_mapped["fit_ready"] is True
    assert out_mapped["line_measurement_count"] == 2
    assert out_mapped["column_mapping_source"] == "user_confirmed"
    assert any("user-supplied" in str(w) for w in out_mapped["warnings"])
    # The shared cached payload must NOT have been mutated by the mapping rerun.
    assert payload["line_measurements"] == []
