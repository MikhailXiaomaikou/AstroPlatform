"""PART AF C2 — fit_line_lfr accepts cache_keys list to union surveys.

R2.4 M5 audit reproducer: AI extracted ALPINE (74 rows) and REBELS
(13 rows) into separate caches, but fit_line_lfr only saw the most
recent one (still 74). The "Loaded 13" log line in the chat was real
but never reached the fit.

Locks the new cache_keys list parameter:
- Multi-cache merge dedupes by (source_name, bibcode).
- contributing_cache_keys + n_surveys_merged surface in the result.
- Single cache_key path is unchanged.
"""

from __future__ import annotations


def _store(key: str, payload: dict) -> None:
    from app.services.ai_tools import store_search_results
    store_search_results(key, payload)


def _make_cache(prefix: str, n: int, bibcode: str, z: float) -> dict:
    return {
        "kind": "literature_tables",
        "cache_key": prefix,
        "line_measurements": [
            {
                "source_name": f"{prefix}_{i}",
                "redshift": z,
                "line_id": "[CII] 158um",
                "log_luminosity": 9.0 + i * 0.05,
                "fwhm_km_s": 200.0 + i * 5,
                "bibcode": bibcode,
                "citation": {"bibcode": bibcode},
            }
            for i in range(n)
        ],
        "tables": [],
    }


def test_fit_line_lfr_merges_two_caches() -> None:
    from app.services.ai_tools import _exec_fit_line_lfr

    _store("alpine_test", _make_cache("ALPINE", 5, "2020A&A...643A...4L", 4.5))
    _store("rebels_test", _make_cache("REBELS", 5, "2022ApJ...931..160B", 7.0))

    out = _exec_fit_line_lfr({
        "cache_keys": ["alpine_test", "rebels_test"],
        "line_id": "[CII]",
    })
    assert out["success"] is True
    assert out["n_used"] == 10, "all 10 rows from both surveys should fit"
    assert out["n_surveys_merged"] == 2
    assert sorted(out["contributing_cache_keys"]) == ["alpine_test", "rebels_test"]


def test_fit_line_lfr_dedupes_same_source_same_bibcode() -> None:
    """Same source name + same bibcode in two cache entries should be
    counted ONCE — not falsely doubled because the same paper happens
    to be referenced from two cache keys."""
    from app.services.ai_tools import _exec_fit_line_lfr

    a = _make_cache("DUPE", 5, "2020TestA", 4.5)
    b = _make_cache("DUPE", 5, "2020TestA", 4.5)  # identical
    _store("dupe_a", a)
    _store("dupe_b", b)

    out = _exec_fit_line_lfr({
        "cache_keys": ["dupe_a", "dupe_b"],
        "line_id": "[CII]",
    })
    assert out["n_used"] == 5  # 5 unique sources, not 10


def test_fit_line_lfr_keeps_same_source_across_different_bibcodes() -> None:
    """Same source name from DIFFERENT papers (independent measurements)
    should NOT be deduped — that's a real cross-paper comparison."""
    from app.services.ai_tools import _exec_fit_line_lfr

    a = _make_cache("HZ7", 1, "2020TestA", 5.69)
    b = _make_cache("HZ7", 1, "2022TestB", 5.69)
    _store("hz7_a", a)
    _store("hz7_b", b)

    out = _exec_fit_line_lfr({
        "cache_keys": ["hz7_a", "hz7_b"],
        # min_rows defaults to 5 → 2 rows would be PARTIAL, lower it
        # so success path runs and we can assert n_used.
        "min_rows": 2,
        "line_id": "[CII]",
    })
    assert out["n_used"] == 2  # both kept


def test_fit_line_lfr_single_cache_key_path_unchanged() -> None:
    """Backwards compat: passing only `cache_key` (not `cache_keys`)
    still resolves a single cache and reports n_surveys_merged=1."""
    from app.services.ai_tools import _exec_fit_line_lfr

    _store("alpine_single", _make_cache("ALPINE", 6, "2020A&A...643A...4L", 4.5))
    out = _exec_fit_line_lfr({
        "cache_key": "alpine_single",
        "line_id": "[CII]",
    })
    assert out["success"] is True
    assert out["n_used"] == 6
    assert out["n_surveys_merged"] == 1
    assert out["contributing_cache_keys"] == ["alpine_single"]


def test_fit_line_lfr_cache_keys_overrides_cache_key_when_both_given() -> None:
    """When both `cache_keys` (list) and `cache_key` (single) are
    supplied, the list wins. Ensures the AI doesn't accidentally
    fall back to single-cache when it meant to merge."""
    from app.services.ai_tools import _exec_fit_line_lfr

    _store("only_alpine", _make_cache("ALPINE", 6, "2020A&A...643A...4L", 4.5))
    _store("only_rebels", _make_cache("REBELS", 6, "2022ApJ...931..160B", 7.0))

    out = _exec_fit_line_lfr({
        "cache_key": "only_alpine",  # would yield n=6
        "cache_keys": ["only_alpine", "only_rebels"],  # should yield n=12
        "line_id": "[CII]",
    })
    assert out["n_used"] == 12
    assert out["n_surveys_merged"] == 2
