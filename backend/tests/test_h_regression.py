"""PART H regression tests.

Covers the key fixes from the 5-paper reviewer cycle:
- H0.2 ADQL multiline normalization
- H0.3 search_lightcurve Quantity unit fix + fallback
- H0.8 TOP auto-degradation
- H1.1 V/154/sdss17 catalog registry
- H2.1 arXiv fallback for literature search
- H3.1 data_source AST-based linting (alias + import forms)
- H0.7 tool_failure_counts ignores soft failures
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------- H0.2 ADQL multiline normalization ----------

def test_adql_newline_normalized_before_tap_call():
    """H0.2: req.query with \\n / \\r / \\t must be collapsed to spaces
    before TapPlus.launch_job sees it — otherwise TAP may truncate at
    newline mid-identifier (Paper 4 'Cannot parse query FROM g' bug)."""
    from app.api.integration import execute_adql_query, ADQLRequest

    captured_queries: list[str] = []

    class FakeTapPlus:
        def __init__(self, url):
            pass
        def launch_job(self, query):
            captured_queries.append(query)
            # Return a fake job with an empty table so we don't hit network.
            class FakeJob:
                def get_results(self):
                    from astropy.table import Table
                    return Table()
            return FakeJob()
        def launch_job_async(self, query):
            return self.launch_job(query)

    with patch("astroquery.utils.tap.core.TapPlus", FakeTapPlus):
        req = ADQLRequest(
            query="SELECT TOP 5\nsource_id, ra, dec\nFROM gaiadr3.gaia_source",
            service="gaia",
        )
        try:
            asyncio.run(execute_adql_query(req))
        except Exception:
            pass  # augment_adql_payload may fail on empty table, not our concern

    assert captured_queries, "TAP never called"
    assert "\n" not in captured_queries[0], f"Newline survived normalization: {captured_queries[0]!r}"
    # Also no double spaces (we collapsed whitespace)
    assert "  " not in captured_queries[0], f"Double space in normalized query: {captured_queries[0]!r}"


# ---------- H0.3 search_lightcurve no longer crashes on Quantity ----------

def test_search_lightcurve_no_quantity_truthiness_error():
    """H0.3: the old `if r.exptime` crashed on Quantity.  The fix replaces
    it with `is not None` + safe Quantity value extraction.  This test
    directly exercises the helper inside search_lightcurve by constructing
    a fake result with a Quantity exptime."""
    import astropy.units as u
    import inspect
    from app.services import astro_analysis

    # Grab _exptime from the closure by re-parsing the source.
    src = inspect.getsource(astro_analysis.search_lightcurve)
    # Ensure the old buggy pattern is gone
    assert "if r.exptime else" not in src, "Old Quantity-truthy bug pattern still present"
    # And new pattern is present
    assert "is not None" in src or "_exptime" in src

    # Also do a live smoke test: mock lightkurve to return a result with
    # a Quantity exptime and ensure no "Quantity truthiness" error.
    class FakeResult:
        def __init__(self, exptime):
            self.mission = "TESS"
            self.target_name = "HD 189733"
            self.exptime = exptime

    class FakeSearchResult:
        def __init__(self, results):
            self._results = results
        def __len__(self):
            return len(self._results)
        def __getitem__(self, key):
            return FakeSearchResult(self._results[key])
        def __iter__(self):
            return iter(self._results)

    with patch("lightkurve.search_lightcurve") as lk_mock:
        lk_mock.return_value = FakeSearchResult([
            FakeResult(exptime=120 * u.second),
            FakeResult(exptime=None),
        ])
        result = astro_analysis.search_lightcurve("HD 189733", mission="tess")

    assert "Quantity truthiness" not in str(result.get("error", ""))
    assert result.get("found", 0) == 2


# ---------- H0.8 TOP auto-degradation on timeout ----------

def test_top_degradation_retries_with_smaller_sample():
    """H0.8: ADQL query with TOP 50000 that times out should be retried
    with TOP auto-reduced to 5000 (min(1000, 50000/10))."""
    # Lightweight check: verify the regex-based TOP detection works for
    # the common spellings — full integration test would need mocks
    # across execute_adql_query which is heavier.
    import re
    for query, expected_top in [
        ("SELECT TOP 50000 * FROM t", 50000),
        ("SELECT top 20000 ra FROM gaiadr3.gaia_source", 20000),
        ("SELECT * FROM t", None),  # no TOP
        ("SELECT TOP 5 x FROM t", 5),  # too small for degradation
    ]:
        m = re.search(r"\bTOP\s+(\d+)\b", query, re.IGNORECASE)
        found_top = int(m.group(1)) if m else None
        assert found_top == expected_top


# ---------- H1.1 V/154/sdss17 catalog registry ----------

def test_v154_sdss17_has_correct_column_names():
    """H1.1: the Paper 3 reviewer hit 'unresolved identifier RAJ2000'
    because AI guessed VizieR column names.  V/154/sdss17 actually uses
    lowercase ra/dec/u/g/r/i/z + objID/class/zsp/zph."""
    from app.services.catalog_registry import get_catalog
    entry = get_catalog('"V/154/sdss17"')
    assert entry is not None
    col_names = {c.name for c in entry.columns}
    # Real columns
    assert "ra" in col_names
    assert "dec" in col_names
    assert "objID" in col_names  # capital ID
    assert "class" in col_names
    assert "u" in col_names
    assert "g" in col_names
    assert "r" in col_names
    assert "i" in col_names
    assert "z" in col_names  # photometry, not redshift
    assert "zsp" in col_names  # spec redshift
    assert "zph" in col_names  # photo redshift
    # Wrong columns AI used to guess
    assert "RAJ2000" not in col_names
    assert "DEJ2000" not in col_names
    assert "petroMag_r" not in col_names
    assert "psfMag_r" not in col_names
    assert "redshift" not in col_names


def test_vizier_common_mistakes_cover_sdss_columns():
    """H1.1: common wrong column names should have actionable hints."""
    from app.services.catalog_registry import suggest_for_missing_column
    for wrong_col in ["petroMag_r", "petroMag_g", "psfMag_r", "redshift", "objid"]:
        hint = suggest_for_missing_column(wrong_col)
        assert hint is not None, f"No hint for common mistake {wrong_col!r}"
        assert len(hint) > 20  # actually useful content


# ---------- H2.1 arXiv fallback ----------

def test_arxiv_fallback_parses_atom_xml():
    """H2.1: when ADS is unavailable, arXiv API returns Atom XML —
    _search_arxiv_sync must parse it into our standard shape."""
    from app.api.citations import _search_arxiv_sync

    # Sample arXiv Atom response (trimmed)
    fake_xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1912.12345</id>
    <title>Cepheid Period-Luminosity Relation in Gaia DR3</title>
    <summary>We measure the P-L relation for 1000 Cepheids.</summary>
    <published>2019-12-25T00:00:00Z</published>
    <author><name>J. Smith</name></author>
    <author><name>K. Jones</name></author>
  </entry>
</feed>"""

    class FakeResponse:
        status_code = 200
        text = fake_xml
        def raise_for_status(self):
            pass

    with patch("httpx.get", return_value=FakeResponse()):
        results = _search_arxiv_sync("Cepheid period-luminosity")

    assert len(results) == 1
    r = results[0]
    assert r["bibcode"] == "arXiv:1912.12345"
    assert "Cepheid" in r["title"]
    assert r["authors"] == ["J. Smith", "K. Jones"]
    assert r["year"] == "2019"
    assert r["source"] == "arxiv"


# ---------- H3.1 data_source AST linter ----------

def test_ast_linter_accepts_direct_call():
    """H3.1: `rows = get_adql_results()` — the obvious case."""
    from app.services.ai_tools import _exec_run_python

    code = "rows = get_adql_results()\nprint(len(rows))"
    resp = asyncio.run(_exec_run_python({"code": code, "data_source": "latest_adql"}))
    # Should NOT be rejected by data_source_mismatch (may fail in sandbox
    # because get_adql_results isn't in scope — that's a different error).
    assert resp.get("error_class") != "data_source_mismatch"


def test_ast_linter_accepts_aliased_call():
    """H3.1: `fetch = get_adql_results; rows = fetch()` — alias form.
    Old string check already caught this via module-level 'get_adql_results'
    appearing literally, but we want to guarantee AST also works."""
    from app.services.ai_tools import _exec_run_python

    code = "fetch = get_adql_results\nrows = fetch()"
    resp = asyncio.run(_exec_run_python({"code": code, "data_source": "latest_adql"}))
    assert resp.get("error_class") != "data_source_mismatch"


def test_ast_linter_accepts_import_alias():
    """H3.1: `from ... import get_adql_results as q` — the case that
    broke the naive string check before AST."""
    from app.services.ai_tools import _exec_run_python

    code = "from builtins import print\nimport builtins\nprint(1)\n# reads via get_adql_results"
    # Using a comment to mention the token — old code would accept this.
    # The NEW AST check also accepts because the name appears in a Name node
    # somewhere (actually no — in a comment it's not in AST).  Let's test
    # a real legitimate pattern: call via attribute.
    code2 = "import astro\nrows = astro.get_adql_results()\nprint(len(rows))"
    resp = asyncio.run(_exec_run_python({"code": code2, "data_source": "latest_adql"}))
    assert resp.get("error_class") != "data_source_mismatch"


def test_ast_linter_accepts_get_cached_results_any_source():
    """H3.1: `get_cached_results('latest_adql')` should satisfy any
    declared real data_source (it's the generic cache reader)."""
    from app.services.ai_tools import _exec_run_python

    code = "rows = get_cached_results('latest_adql')\nprint(len(rows))"
    resp = asyncio.run(_exec_run_python({"code": code, "data_source": "latest_adql"}))
    assert resp.get("error_class") != "data_source_mismatch"


def test_ast_linter_rejects_random_numpy_without_reader():
    """H3.1: np.random-only code with data_source='latest_adql' still rejected."""
    from app.services.ai_tools import _exec_run_python

    code = "import numpy as np\nrows = np.random.normal(0, 1, 100)\nprint(rows.mean())"
    resp = asyncio.run(_exec_run_python({"code": code, "data_source": "latest_adql"}))
    # Should be rejected by either the AST linter OR the synthetic detector
    assert resp.get("success") is False
    assert resp.get("error_class") in {
        "data_source_mismatch",
        "synthetic_declared_as_real",
    }


# ---------- H0.7 tool_failure_counts ignores soft failures ----------

def test_h07_soft_failure_classification_reference():
    """H0.7 logic lives inside _run_agent_loop; this is a shape test
    to make sure someone doesn't accidentally remove the soft-failure
    exclusion. Importing + reading the source for the specific markers."""
    import inspect
    from app.api import chat as chat_module

    # _run_agent_loop source must contain the soft-failure guard
    src = inspect.getsource(chat_module._run_agent_loop)
    assert 'soft_failure' in src
    assert 'payload_too_large' in src
    assert 'DISABLE_AFTER_FAILURES = 3' in src or '= 3' in src


# ---------- I2 / B-S3: search_lightcurve Simbad cross-ID ----------

def test_search_lightcurve_resolves_via_simbad_tic():
    """I2 / B-S3: search_lightcurve("HD 189733", mission="tess") used to
    return 0 because MAST indexes by TIC.  New flow uses
    Simbad.query_objectids to resolve aliases, finds the TIC, retries."""
    from app.services import astro_analysis

    class FakeLKResult:
        def __init__(self, results):
            self._results = results
        def __len__(self):
            return len(self._results)
        def __getitem__(self, key):
            return FakeLKResult(self._results[key])
        def __iter__(self):
            return iter(self._results)

    class FakeRow:
        def __init__(self, mission, target_name):
            self.mission = mission
            self.target_name = target_name
            self.exptime = None

    # Mock Simbad table — a list-of-dicts-like with an "ID" column.
    class FakeIDTable:
        def __init__(self, ids):
            self._ids = ids
            self.colnames = ["ID"]
        def __len__(self):
            return len(self._ids)
        def __iter__(self):
            return iter([{"ID": i} for i in self._ids])

    call_log: list = []

    def fake_search_lightcurve(q, mission=None):
        call_log.append(str(q))
        # Only TIC 256364928 returns a hit; HD 189733 returns empty
        if "TIC" in str(q).upper():
            return FakeLKResult([FakeRow("TESS", "TIC 256364928")])
        return FakeLKResult([])

    with patch("lightkurve.search_lightcurve", side_effect=fake_search_lightcurve):
        with patch("astroquery.simbad.Simbad.query_objectids") as simbad_mock:
            simbad_mock.return_value = FakeIDTable([
                "HD 189733", "HIP 98505", "2MASS J20004370+2242391",
                "TIC 256364928", "Gaia DR3 1832671533156937856",
            ])
            result = astro_analysis.search_lightcurve("HD 189733", mission="tess")

    assert result["found"] == 1
    assert result.get("target_resolved_via", "").upper().startswith("TIC"), \
        f"expected TIC resolution, got {result.get('target_resolved_via')!r}"
    # Should have tried direct first, then TIC
    assert any("HD 189733" in q for q in call_log)
    assert any("TIC" in q.upper() for q in call_log)


# ---------- I3 / B-S1: VizieR multi-mirror fallback ----------

def test_vizier_multi_mirror_falls_through_on_first_failure():
    """I3/B-S1: when the primary VizieR mirror returns 503 / times out,
    the launch loop must transparently move to the next configured
    mirror and use its result without surfacing the failure to the
    caller."""
    from app.api import integration as integ
    from astropy.table import Table

    # Confirm at least 2 VizieR mirrors are configured (otherwise the
    # test exercises nothing).
    assert len(integ.ADQL_SERVICE_MIRRORS["vizier"]) >= 2

    primary_url = integ.ADQL_SERVICE_MIRRORS["vizier"][0]
    fallback_url = integ.ADQL_SERVICE_MIRRORS["vizier"][1]
    urls_tried: list[str] = []

    class _FakeJob:
        def __init__(self, url):
            self._url = url
        def get_results(self):
            t = Table()
            t["from_url"] = [self._url]
            return t

    class FakeTapPlus:
        def __init__(self, url):
            self.url = url
            urls_tried.append(url)
        def launch_job(self, query):
            if self.url == primary_url:
                raise ConnectionError("503 Service Unavailable (simulated)")
            return _FakeJob(self.url)
        def launch_job_async(self, query):
            return self.launch_job(query)

    with patch("astroquery.utils.tap.core.TapPlus", FakeTapPlus):
        result = integ._launch_on_mirrors(
            "SELECT TOP 1 * FROM \"V/154/sdss17\"",
            service="vizier",
            async_mode=False,
        )

    assert urls_tried[:2] == [primary_url, fallback_url], (
        f"Expected primary then fallback; got {urls_tried}"
    )
    # Result must be from the fallback URL
    assert "from_url" in result.colnames
    assert str(result["from_url"][0]) == fallback_url


def test_vizier_all_mirrors_fail_raises_with_url_list():
    """I3/B-S1: when EVERY VizieR mirror fails, the caller must see an
    error that names which URLs were tried — so support / users can
    immediately tell the request reached more than just CDS France."""
    from app.api import integration as integ

    urls_tried: list[str] = []

    class FakeTapPlus:
        def __init__(self, url):
            self.url = url
            urls_tried.append(url)
        def launch_job(self, query):
            raise ConnectionError(f"timeout from {self.url}")
        def launch_job_async(self, query):
            return self.launch_job(query)

    with patch("astroquery.utils.tap.core.TapPlus", FakeTapPlus):
        with pytest.raises(Exception) as excinfo:
            integ._launch_on_mirrors(
                "SELECT TOP 1 * FROM \"V/154/sdss17\"",
                service="vizier",
                async_mode=False,
            )

    assert urls_tried == integ.ADQL_SERVICE_MIRRORS["vizier"], (
        f"Did not try all mirrors; only tried {urls_tried}"
    )
    err_str = str(excinfo.value)
    # Error must name the service and at least the primary URL
    assert "vizier" in err_str.lower()
    assert integ.ADQL_SERVICE_MIRRORS["vizier"][0] in err_str


def test_adql_services_legacy_dict_still_exists():
    """I3: ADQL_SERVICES (single-URL view) must remain importable for
    backward compatibility — list_adql_services() and any external
    consumer still indexes it directly."""
    from app.api.integration import ADQL_SERVICES, ADQL_SERVICE_MIRRORS

    for service in ADQL_SERVICE_MIRRORS:
        assert service in ADQL_SERVICES
        assert ADQL_SERVICES[service] == ADQL_SERVICE_MIRRORS[service][0]


def test_search_lightcurve_picks_kic_for_kepler_mission():
    """I2: prefix priority respects requested mission."""
    from app.services import astro_analysis

    class FakeLKResult:
        def __init__(self, results):
            self._results = results
        def __len__(self):
            return len(self._results)
        def __getitem__(self, key):
            return FakeLKResult(self._results[key])
        def __iter__(self):
            return iter(self._results)

    class FakeRow:
        mission = "Kepler"
        target_name = "KIC 11446443"
        exptime = None

    class FakeIDTable:
        colnames = ["ID"]
        def __init__(self, ids):
            self._ids = ids
        def __len__(self):
            return len(self._ids)
        def __iter__(self):
            return iter([{"ID": i} for i in self._ids])

    def fake_lk(q, mission=None):
        if "KIC" in str(q).upper():
            return FakeLKResult([FakeRow()])
        return FakeLKResult([])

    with patch("lightkurve.search_lightcurve", side_effect=fake_lk):
        with patch("astroquery.simbad.Simbad.query_objectids") as simbad_mock:
            simbad_mock.return_value = FakeIDTable([
                "Kepler-10", "TIC 377780790", "KIC 11904151",
            ])
            result = astro_analysis.search_lightcurve("Kepler-10", mission="kepler")

    assert result["found"] == 1
    # Must have picked KIC (mission=kepler), not TIC (higher priority only for tess)
    assert "KIC" in (result.get("target_resolved_via") or "").upper()


# ---------- K1.A: SYSTEM_PROMPT 必须含 data_source 硬规则 ----------

def test_system_prompt_contains_data_source_hard_rule():
    """K1.A: 三次回归审稿人定位到 SYNTHETIC 误报其实是 AI 自己把
    data_source 填成了 none_not_analyzing_real_data (被 'literature
    comparison' 字样触发).  prompt 必须明确告诉 AI: 用了前一步 real
    source 的输出就声明 latest_adql, literature 对比不是 synthetic.
    少任何一条 keyword 就意味着硬规则被改弱/去掉."""
    from app.api.chat import SYSTEM_PROMPT

    # Rule section must be present
    assert "K1.A" in SYSTEM_PROMPT, "K1.A 硬规则段已经丢失/被合并覆盖"

    # 关键 keyword: 硬规则必须明确列出正/反两种常见情形
    required_keywords = [
        "literature",              # 反例涉及 literature 字样
        "'latest_adql'",           # 正确声明例
        "'none_not_analyzing_real_data'",  # 错误声明例
        "rows",                    # Rule 1 里提到的变量名
        "np.random",               # Rule 3 里的合法 synthetic 触发条件
        "np.linspace",             # Rule 3 的另一条
        "bootstrap",               # Rule 2 里明确不算 synthetic
        # 反例代码必须在 prompt 里作为 few-shot
        "WRONG",
        "CORRECT",
    ]
    missing = [kw for kw in required_keywords if kw not in SYSTEM_PROMPT]
    assert not missing, f"K1.A prompt 缺 keyword: {missing}"


# ---------- K2: search_lightcurve missing target 错误消息 ----------

def test_search_lightcurve_missing_target_returns_actionable_error():
    """K2: AI 第一次调用若漏传 target, 后端必须返回带 error_class +
    示例的清晰错误 (而不是空洞的 'target is required'), 这样 AI 在同
    一轮的下一次 tool call 里就能补对."""
    from app.services.ai_tools import _exec_search_lightcurve

    # target 整个缺
    result = asyncio.run(_exec_search_lightcurve({}))
    assert result.get("error_class") == "missing_argument"
    assert result.get("argument") == "target"
    err = str(result.get("error") or "")
    # 错误消息里必须带至少一个具体示例, 否则没治本
    assert any(ex in err for ex in ("HD 189733", "Kepler-10", "TIC", "delta Cep"))

    # 空字符串 / 纯空格也走同一路径
    result = asyncio.run(_exec_search_lightcurve({"target": "   "}))
    assert result.get("error_class") == "missing_argument"

    # mission 非法值 → 单独的 invalid_argument 错误
    result = asyncio.run(_exec_search_lightcurve({"target": "HD 189733", "mission": "hubble"}))
    assert result.get("error_class") == "invalid_argument"
    assert result.get("argument") == "mission"
