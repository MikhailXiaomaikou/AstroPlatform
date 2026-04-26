"""PART Z C3 — end-to-end coverage for the extract_literature_tables tool.

Audit of the tool surfaced 4 P0/P1 gaps + 2 polish items; C1+C2 fixed
them; this file locks the regressions:

- _infer_line_id covers 5+ emission lines, returns None for unknown
  (replaces the silent "[CII] 158um" fallback that mislabelled Hα/Lyα/...
  papers).
- _normalize_line_measurements correctly identifies a non-[CII]
  table (Hα FWHM/luminosity), which the pre-PART-Z code dropped.
- _parse_html_tables (BS4-based) strips KaTeX wrappers and skips
  nested tables, where the old regex parser leaked them.
- _cached_extract_arxiv_tables_payload caches via connector_cache so
  a second call for the same arxiv_id does not re-fetch ar5iv.
- _exec_extract_literature_tables rejects unauthenticated chat calls
  (closes the DoS-amplifier path that bypassed the M19 endpoint gate).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


class _InMemoryCacheBackend:
    """Test-only backend so cache assertions don't touch the on-disk SQLite
    cache (which persists across test runs and would mask cache misses)."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def get(self, key: str) -> Any:
        return self._store.get(key)

    def set(self, key: str, value: Any, ttl: int) -> None:  # noqa: ARG002
        self._store[key] = value


def _install_isolated_cache(monkeypatch) -> _InMemoryCacheBackend:
    from app.services import connector_cache

    backend = _InMemoryCacheBackend()
    # Replace module-level _backend so connector_cache.get_backend() returns
    # ours without going through the SQLite/Redis selection logic.
    monkeypatch.setattr(connector_cache, "_backend", backend, raising=False)
    # Also clear inflight singleflight registry so prior test futures don't bleed.
    monkeypatch.setattr(connector_cache, "_inflight", {}, raising=False)
    return backend


# ---------------------------------------------------------------------------
# _infer_line_id (PART Z C1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "header_text,expected",
    [
        ("log L[CII]", "[CII] 158um"),
        ("L_CII 158um", "[CII] 158um"),
        ("log L_Halpha", "Halpha 6563A"),
        ("Hα luminosity", "Halpha 6563A"),
        ("log L(Lyα)", "Lyalpha 1216A"),
        ("Lyalpha at 1216", "Lyalpha 1216A"),
        ("log L([OIII] 5007)", "[OIII] 5007A"),
        ("[OII] 3727", "[OII] 3727A"),
        # Hα appears before [NII] in _LINE_ID_PATTERNS (Hα is the brighter,
        # more commonly cited line), so a mixed "[NII]/Halpha" header
        # matches Hα first. Documented here so future re-orderings are
        # deliberate.
        ("[NII]/Halpha", "Halpha 6563A"),
        ("CO(1-0) line", "CO rotational"),
        ("Just a regular column header", None),
        ("", None),
    ],
)
def test_infer_line_id_pattern_matches(header_text: str, expected: str | None) -> None:
    from app.api.arxiv import _infer_line_id

    assert _infer_line_id(header_text) == expected


# ---------------------------------------------------------------------------
# _parse_html_tables — BS4 (PART Z C2)
# ---------------------------------------------------------------------------

def test_parse_html_tables_strips_katex_wrappers() -> None:
    """KaTeX / ar5iv <span class="ltx_text"> wrappers must NOT leak into cells."""
    from app.api.arxiv import _parse_html_tables

    html = (
        '<table>'
        '<thead><tr>'
        '<th><span class="ltx_text">Source</span></th>'
        '<th><span class="ltx_text">z</span></th>'
        '<th><span class="ltx_text">log L[CII]</span></th>'
        '</tr></thead>'
        '<tbody>'
        '<tr><td>HZ1</td><td>5.69</td><td>8.40</td></tr>'
        '<tr><td>HZ2</td><td>5.67</td><td>8.62</td></tr>'
        '</tbody>'
        '</table>'
    )
    tables = _parse_html_tables(html)
    assert len(tables) == 1
    assert tables[0]["columns"] == ["Source", "z", "log L[CII]"]
    # KaTeX text class name must NOT show up in any cell
    for row in tables[0]["rows"]:
        for cell in row:
            assert "ltx_text" not in cell


def test_parse_html_tables_skips_nested_tables() -> None:
    """A <table> inside another <table> is part of the outer table's data,
    not a separate top-level table — only the outer table is returned."""
    from app.api.arxiv import _parse_html_tables

    html = (
        '<table>'
        '<thead><tr><th>A</th><th>B</th></tr></thead>'
        '<tbody><tr><td>x</td>'
        '<td><table><tr><td>nested</td><td>inner</td></tr></table></td>'
        '</tr></tbody>'
        '</table>'
    )
    tables = _parse_html_tables(html)
    # Only one top-level table
    assert len(tables) == 1
    assert tables[0]["columns"] == ["A", "B"]


def test_parse_html_tables_handles_no_thead() -> None:
    """When there's no <thead>, the first <tr> is the header."""
    from app.api.arxiv import _parse_html_tables

    html = (
        '<table>'
        '<tr><th>Source</th><th>FWHM</th></tr>'
        '<tr><td>HZ1</td><td>180</td></tr>'
        '<tr><td>HZ2</td><td>205</td></tr>'
        '</table>'
    )
    tables = _parse_html_tables(html)
    assert len(tables) == 1
    assert tables[0]["columns"] == ["Source", "FWHM"]
    assert len(tables[0]["rows"]) == 2  # header NOT counted as a data row


# ---------------------------------------------------------------------------
# _normalize_line_measurements — non-[CII] line support (PART Z C1)
# ---------------------------------------------------------------------------

def test_normalize_line_measurements_extracts_halpha_table() -> None:
    """Pre-PART-Z this returned 0 measurements for an Hα table because:
    (a) luminosity_idx regex set was [CII]-only, and
    (b) inferred_line silently fell back to "[CII] 158um".
    Now it should extract the rows with line_id="Halpha 6563A"."""
    from app.api.arxiv import _normalize_line_measurements

    table = {
        "table_id": "test-1",
        "columns": ["Source", "z", "log L_Halpha", "FWHM", "FWHM_err"],
        "rows": [
            ["GAL-1", "0.50", "42.3", "180", "20"],
            ["GAL-2", "0.45", "42.1", "165", "15"],
        ],
        "row_citations": [{"bibcode": "2023ExGL...1A...1"}, {"bibcode": "2023ExGL...1A...1"}],
        "caption": "Hα measurements for star-forming galaxies",
    }
    measurements = _normalize_line_measurements([table])
    assert len(measurements) == 2
    assert all(m["line_id"] == "Halpha 6563A" for m in measurements)
    assert measurements[0]["log_luminosity"] == 42.3
    assert measurements[0]["fwhm_km_s"] == 180.0
    assert measurements[0]["fwhm_err_km_s"] == 20.0


def test_normalize_line_measurements_still_extracts_cii() -> None:
    """Backwards-compat: original [CII] table still works."""
    from app.api.arxiv import _normalize_line_measurements

    table = {
        "table_id": "test-cii",
        "columns": ["Source", "z", "log L[CII]", "FWHM"],
        "rows": [
            ["HZ1", "5.69", "8.40", "180"],
        ],
        "row_citations": [{"bibcode": "2020ALPINE..1A...1"}],
        "caption": "[CII] 158um line measurements",
    }
    measurements = _normalize_line_measurements([table])
    assert len(measurements) == 1
    assert measurements[0]["line_id"] == "[CII] 158um"


def test_normalize_line_measurements_skips_table_without_required_columns() -> None:
    """Tables missing a source name / luminosity / FWHM column must yield
    zero measurements, not a partially-filled best-guess row."""
    from app.api.arxiv import _normalize_line_measurements

    table = {
        "table_id": "test-bad",
        "columns": ["Index", "Notes"],
        "rows": [["1", "no real measurement here"]],
        "row_citations": [{}],
    }
    assert _normalize_line_measurements([table]) == []


# ---------------------------------------------------------------------------
# _exec_extract_literature_tables — auth gate (PART Z C1, P0)
# ---------------------------------------------------------------------------

def test_exec_extract_literature_tables_rate_limit_for_anonymous(monkeypatch) -> None:
    """PART Z C5: anonymous chat sessions are allowed up to 5 calls per
    rolling hour (the 24h cache + circuit-breaker carry the main DoS
    defence; the limit is the thin extra layer). Calls 1-5 must
    succeed (or fail for unrelated reasons), call 6 must hit
    rate_limit_exceeded."""
    from app.services import ai_tools

    # Reset the rate-limit registry for a clean per-session bucket.
    ai_tools._arxiv_tool_calls.clear()  # type: ignore[attr-defined]

    # Stub the upstream fetch so we don't hit ar5iv during the test.
    async def fake_fetch(arxiv_id: str) -> dict:
        return {
            "arxiv_id": arxiv_id,
            "title": "stub",
            "tables": [],
            "line_measurements": [],
            "bibcode": f"arXiv:{arxiv_id}",
            "authors": [],
            "year": 2024,
            "doi": None,
            "source_url": "",
        }
    monkeypatch.setattr(ai_tools, "_extract_arxiv_tables_payload_with_retry", fake_fetch)
    _install_isolated_cache(monkeypatch)

    async def call_once() -> dict:
        return await ai_tools._exec_extract_literature_tables(
            {"arxiv_id": "2310.12345"},
            user_id=None,
            chat_session_id="test-anon-session",
        )

    # 5 calls within quota.
    for i in range(5):
        result = asyncio.run(call_once())
        assert result.get("error_class") != "rate_limit_exceeded", (
            f"call {i+1} unexpectedly rate-limited: {result}"
        )

    # 6th call must trip the limiter.
    result = asyncio.run(call_once())
    assert result["success"] is False
    assert result["error_class"] == "rate_limit_exceeded"
    assert result["rate_limit_used"] == 5
    assert result["rate_limit_cap"] == 5
    assert result["rate_limit_kind"] == "anonymous chat session"


def test_exec_extract_literature_tables_authenticated_quota_higher(monkeypatch) -> None:
    """Authenticated users get 50/hr (10× the anonymous cap)."""
    from app.services import ai_tools

    ai_tools._arxiv_tool_calls.clear()  # type: ignore[attr-defined]

    async def fake_fetch(arxiv_id: str) -> dict:
        return {"arxiv_id": arxiv_id, "title": "stub", "tables": [],
                "line_measurements": [], "bibcode": f"arXiv:{arxiv_id}",
                "authors": [], "year": 2024, "doi": None, "source_url": ""}
    monkeypatch.setattr(ai_tools, "_extract_arxiv_tables_payload_with_retry", fake_fetch)
    _install_isolated_cache(monkeypatch)

    # 6 calls — would already trip the anonymous cap, but authenticated
    # users have headroom up to 50.
    for i in range(6):
        result = asyncio.run(
            ai_tools._exec_extract_literature_tables(
                {"arxiv_id": "2310.12345"},
                user_id="user-authenticated",
                chat_session_id="any-session",
            )
        )
        assert result.get("error_class") != "rate_limit_exceeded", (
            f"authenticated call {i+1} unexpectedly rate-limited: {result}"
        )


def test_exec_extract_literature_tables_rejects_missing_arxiv_id() -> None:
    """Authenticated but no arxiv_id payload → missing_arxiv_id."""
    from app.services.ai_tools import _exec_extract_literature_tables

    # Use a fresh user_id so we don't bleed budget from other tests.
    result = asyncio.run(
        _exec_extract_literature_tables({}, user_id="user-test-no-arxiv-id")
    )
    assert result["success"] is False
    assert result["error_class"] == "missing_arxiv_id"


# ---------------------------------------------------------------------------
# _cached_extract_arxiv_tables_payload — singleflight + 24h cache
# ---------------------------------------------------------------------------

def test_cached_extract_arxiv_tables_payload_caches_second_call(monkeypatch) -> None:
    """Second call for the same arxiv_id MUST hit the cache (no second
    upstream fetch). Locks the 24h-cache contract — a chat user asking
    the AI about the same paper twice should not amplify load on ar5iv."""
    from app.services import ai_tools

    _install_isolated_cache(monkeypatch)

    call_count = {"n": 0}
    async def fake_fetch(arxiv_id: str) -> dict:
        call_count["n"] += 1
        return {
            "arxiv_id": arxiv_id.replace("arXiv:", ""),
            "title": "Mocked paper",
            "tables": [],
            "line_measurements": [],
            "bibcode": f"arXiv:{arxiv_id}",
        }

    monkeypatch.setattr(
        ai_tools,
        "_extract_arxiv_tables_payload_with_retry",
        fake_fetch,
    )

    async def run() -> tuple[dict, dict]:
        first = await ai_tools._cached_extract_arxiv_tables_payload("2310.12345")
        # Same id, different alias spellings should hit the same cache entry.
        second = await ai_tools._cached_extract_arxiv_tables_payload("arXiv:2310.12345")
        return first, second

    first, second = asyncio.run(run())
    assert first["title"] == "Mocked paper"
    assert second["title"] == "Mocked paper"
    # Critical: only one upstream fetch, even with two callers + alias.
    assert call_count["n"] == 1, (
        f"expected 1 upstream fetch, got {call_count['n']} — cache is leaking"
    )


def test_arxiv_retryable_exceptions_includes_httpx() -> None:
    """The retry decorator must include httpx network exceptions; otherwise
    a flaky ar5iv would surface as a hard failure to the AI on the very
    first transient timeout."""
    from app.services.ai_tools import _arxiv_retryable_exceptions
    import httpx

    excs = _arxiv_retryable_exceptions()
    assert httpx.TimeoutException in excs
    assert httpx.ConnectError in excs
    # Stdlib network exceptions still in.
    assert ConnectionError in excs
    assert TimeoutError in excs


# ---------------------------------------------------------------------------
# Top-level integration: clean 4-col Hα paper → cached payload (auth happy path)
# ---------------------------------------------------------------------------

def test_exec_extract_literature_tables_happy_path_with_mocked_fetch(monkeypatch) -> None:
    """Full happy path: authenticated user + valid arxiv_id + mocked
    upstream returns 2 normalised line_measurements → result has
    line_measurement_count=2, fit_ready=True, supports_measurement_claims=True,
    correct provenance shape, and an Hα-style cache_key wired through."""
    from app.services import ai_tools

    _install_isolated_cache(monkeypatch)

    async def fake_fetch(arxiv_id: str) -> dict:
        return {
            "arxiv_id": "2310.99999",
            "title": "Hα survey at z~0.5",
            "authors": ["Test Author"],
            "year": 2023,
            "bibcode": "arXiv:2310.99999",
            "doi": None,
            "source_url": "https://arxiv.org/abs/2310.99999",
            "tables": [{"table_id": "t1", "columns": ["Source"], "rows": []}],
            "line_measurements": [
                {
                    "source_name": "GAL-1",
                    "redshift": 0.50,
                    "line_id": "Halpha 6563A",
                    "log_luminosity": 42.3,
                    "fwhm_km_s": 180.0,
                    "bibcode": "2023ExGL...1A...1",
                    "arxiv_id": "2310.99999",
                    "table_id": "t1",
                    "row_index": 0,
                },
                {
                    "source_name": "GAL-2",
                    "redshift": 0.45,
                    "line_id": "Halpha 6563A",
                    "log_luminosity": 42.1,
                    "fwhm_km_s": 165.0,
                    "bibcode": "2023ExGL...1A...1",
                    "arxiv_id": "2310.99999",
                    "table_id": "t1",
                    "row_index": 1,
                },
            ],
            "result_granularity": "paper_table",
            "supports_measurement_claims": True,
        }

    monkeypatch.setattr(ai_tools, "_extract_arxiv_tables_payload_with_retry", fake_fetch)

    result = asyncio.run(
        ai_tools._exec_extract_literature_tables(
            {"arxiv_id": "2310.99999"},
            user_id="user-test",
        )
    )

    assert result["success"] is True
    assert result["line_measurement_count"] == 2
    assert result["fit_ready"] is True
    assert result["supports_measurement_claims"] is True
    assert result["bibcode"] == "arXiv:2310.99999"
    assert result["cache_key"]  # non-empty session-scoped key
    assert "provenance" in result
    assert result["provenance"]["coverage"]["primary_citation_source"] == "field_level"


def _silence_unused_import_warning() -> Any:
    return pytest, asyncio


# ---------------------------------------------------------------------------
# C-X4: redshift column flexibility (ALPINE-style z_CII / z_phot / z_sys)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "z_col_name", ["z_CII", "z_[CII]", "z_line", "zCII", "z_phot", "z_sys", "z_CO", "zspec"]
)
def test_normalize_line_measurements_recognises_z_variant(z_col_name: str) -> None:
    """ALPINE / REBELS / similar high-z surveys put the redshift in a
    line-specific column. Pre-C-X4 the strict `^(z|redshift)$|zspec`
    pattern dropped these rows with a silent "missing redshift" — which
    today's audit identified as the root cause of cached samples that
    "have luminosity and FWHM but no redshift"."""
    from app.api.arxiv import _normalize_line_measurements

    table = {
        "table_id": "alpine-style",
        "columns": ["Source", z_col_name, "log L_[CII]", "FWHM"],
        "rows": [["HZ1", "5.69", "8.40", "180"]],
        "row_citations": [{"bibcode": "2020A&A...643A...4L"}],
        "caption": f"[CII] sample with {z_col_name} column",
    }
    ms = _normalize_line_measurements([table])
    assert len(ms) == 1, (
        f"redshift column {z_col_name!r} was not recognised; row dropped"
    )
    assert ms[0]["redshift"] == 5.69
    assert ms[0]["log_luminosity"] == 8.40
    assert ms[0]["fwhm_km_s"] == 180.0


# ---------------------------------------------------------------------------
# C-X4: cached 0-measurements message points to companion-paper search
# ---------------------------------------------------------------------------

def test_exec_extract_literature_tables_zero_measurements_message_is_actionable(
    monkeypatch,
) -> None:
    """When the paper's tables exist but yield zero line_measurements,
    the message_to_model must point the AI at the two valid recoveries
    (search_literature for the companion paper, or
    <tools_returned_nothing/>) — NOT just "do not quote L[CII]"."""
    from app.services import ai_tools

    _install_isolated_cache(monkeypatch)

    async def fake_fetch(arxiv_id: str) -> dict:
        return {
            "arxiv_id": "2310.99999",
            "title": "REBELS size measurements",
            "authors": ["Test"],
            "year": 2023,
            "bibcode": "arXiv:2310.99999",
            "doi": None,
            "source_url": "https://arxiv.org/abs/2310.99999",
            "tables": [{"table_id": "t1", "columns": ["Source", "size_kpc"], "rows": [["G1", "1.2"]]}],
            "line_measurements": [],
            "result_granularity": "paper_table",
            "supports_measurement_claims": False,
        }

    monkeypatch.setattr(ai_tools, "_extract_arxiv_tables_payload_with_retry", fake_fetch)

    result = asyncio.run(
        ai_tools._exec_extract_literature_tables(
            {"arxiv_id": "2310.99999"},
            user_id="user-test",
        )
    )
    assert result["line_measurement_count"] == 0
    msg = result["__message_to_model__"]
    # Two valid recoveries spelled out
    assert "search_literature" in msg
    assert "<tools_returned_nothing" in msg
    # Forbidden behaviours spelled out
    assert "hardcode" in msg
    assert "REBELS" in msg


# ---------------------------------------------------------------------------
# PART AC C1: log/linear schema fixes — caption + value-range detection
# ---------------------------------------------------------------------------

def test_normalize_alpine_style_caption_log_detection() -> None:
    """ALPINE-style: column header is 'L [CII]' (no 'log') but the
    caption says log10. Pre-AC the value would land in `luminosity`
    instead of `log_luminosity`, breaking fit_line_lfr.

    R2.4 M3 audit reproducer — 74-row cache fitted 0 rows because of
    this exact mis-routing.
    """
    from app.api.arxiv import _normalize_line_measurements

    table = {
        "table_id": "alpine",
        "columns": ["Source", "z", "L [CII]", "FWHM"],
        "rows": [["DEIMOS_COSMOS_873756", "4.5457", "9.56", "526.0"]],
        "row_citations": [{"bibcode": "2020A&A...643A...4L"}],
        "caption": "ALPINE [CII] sample. log10 L_[CII] in solar luminosities.",
    }
    ms = _normalize_line_measurements([table])
    assert len(ms) == 1
    assert ms[0]["log_luminosity"] == 9.56
    assert ms[0]["luminosity"] is None
    assert ms[0]["luminosity_inferred_log_from"] == "caption"


def test_normalize_value_range_log_detection_per_row() -> None:
    """Tier 3: header has no 'log', caption has no 'log', but a known
    line + value in [3, 13] is unambiguously log. Falls through to the
    per-row value-range heuristic and still gets it right."""
    from app.api.arxiv import _normalize_line_measurements

    table = {
        "table_id": "hidden",
        "columns": ["Source", "z", "L [CII]", "FWHM"],
        "rows": [["G1", "5.5", "8.42", "200"]],
        "row_citations": [{"bibcode": "test"}],
        "caption": "[CII] measurements (table with no explicit log marker)",
    }
    ms = _normalize_line_measurements([table])
    assert ms[0]["log_luminosity"] == 8.42
    assert ms[0]["luminosity_inferred_log_from"] == "value_range"


def test_normalize_linear_luminosity_not_promoted_when_value_too_large() -> None:
    """Sanity: a real linear luminosity (1e42 erg/s style) must NOT be
    promoted to log just because the column header is ambiguous."""
    from app.api.arxiv import _normalize_line_measurements

    table = {
        "table_id": "linear",
        "columns": ["Source", "z", "L [CII]", "FWHM"],
        "rows": [["G1", "0.5", "3.2e42", "150"]],
        "row_citations": [{"bibcode": "test"}],
        "caption": "Line luminosities in erg/s (no log)",
    }
    ms = _normalize_line_measurements([table])
    # 3.2e42 is well outside [3, 13] → stays in `luminosity`.
    assert ms[0]["log_luminosity"] is None
    assert ms[0]["luminosity"] == 3.2e42
    assert ms[0]["luminosity_inferred_log_from"] is None


def test_fit_line_lfr_legacy_cache_schema_recovery() -> None:
    """The R2.4 M3 audit: a 74-row literature cache written by the OLD
    arxiv normalizer has log_luminosity=None and luminosity=9.56 (real
    log value mis-routed). PART AC C1's fit_line_lfr fallback recovers
    those rows in-place so users don't have to re-extract."""
    from app.services.ai_tools import _exec_fit_line_lfr, store_search_results

    legacy_cache = {
        "kind": "literature_tables",
        "cache_key": "latest_literature_tables",
        "line_measurements": [
            {
                "source_name": f"GAL{i}",
                "redshift": 4.5,
                "line_id": "[CII] 158um",
                "log_luminosity": None,           # the bug
                "log_luminosity_err": None,
                "luminosity": 9.0 + i * 0.05,     # real log10 value
                "luminosity_err": 0.05,
                "fwhm_km_s": 200 + i * 5,
                "fwhm_err_km_s": 15,
                "bibcode": "2020A&A...643A...4L",
                "citation": {"bibcode": "2020A&A...643A...4L"},
            }
            for i in range(10)
        ],
        "tables": [],
    }
    store_search_results("latest_literature_tables", legacy_cache)

    out = _exec_fit_line_lfr({
        "cache_key": "latest_literature_tables",
        "line_id": "[CII]",
        "fit_method_requested": "ols",
    })
    assert out["success"] is True
    assert out["n_used"] == 10
    assert out.get("slope") is not None or out.get("beta") is not None
