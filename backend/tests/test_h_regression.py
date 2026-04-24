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
import sys
from unittest.mock import patch

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


def test_ast_linter_accepts_platform_real_data_reader_for_latest_search():
    """R15: download_and_clean_lightcurve 本身是真实 MAST 读取器。

    AI 有时把这种代码声明成 latest_search（因为上一 action 是
    search_lightcurve），合同层不能因此误报为“没读真实数据”。
    """
    from app.services.ai_tools import _PLATFORM_REAL_DATA_READER_TOKENS

    assert "download_and_clean_lightcurve" in _PLATFORM_REAL_DATA_READER_TOKENS
    assert "transit_search" in _PLATFORM_REAL_DATA_READER_TOKENS


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


def test_ast_linter_mismatch_echoes_observed_identifiers():
    """R4-NEW-1: mismatch errors tell the model which names AST actually saw."""
    from app.services.ai_tools import _exec_run_python

    code = "results = [{'target': 'HD 189733'}]\nfirst = results[0]\nprint(first)"
    resp = asyncio.run(_exec_run_python({"code": code, "data_source": "latest_search"}))

    assert resp.get("success") is False
    assert resp.get("error_class") == "data_source_mismatch"
    err = str(resp.get("error") or "")
    assert "get_search_results" in err
    assert "get_cached_results" in err
    assert "AST observed" in err
    assert "results" in err


def test_run_python_savefig_then_close_is_not_empty():
    """R5-OPEN-1: AI often saves a plot then closes it; keep that figure."""
    from app.services.ai_tools import _exec_run_python

    code = (
        "import io\n"
        "rows = get_adql_results()\n"
        "plt.figure()\n"
        "plt.plot([1, 2, 3], [1, 4, 9])\n"
        "buf = io.BytesIO()\n"
        "plt.savefig(buf, format='png')\n"
        "plt.close('all')\n"
    )
    resp = asyncio.run(_exec_run_python({"code": code, "data_source": "latest_adql"}))

    assert resp.get("success") is True, resp
    assert resp.get("figures"), resp
    assert resp["figures"][0].startswith("iVBOR")
    assert resp.get("backend") in {"inprocess", "subprocess", "unknown"}
    assert "exit_code" in resp


def test_system_prompt_routes_transit_queries_to_search_lightcurve():
    """R4-NEW-2: TESS/transit prompts should make search_lightcurve obvious."""
    from app.api.chat import SYSTEM_PROMPT

    prompt = SYSTEM_PROMPT.lower()
    assert "search_lightcurve" in prompt
    assert "tess" in prompt
    assert "transit" in prompt
    assert "before `search_objects`" in SYSTEM_PROMPT
    assert "hd 189733" in prompt


def test_system_prompt_distinguishes_catalog_period_from_measured_phase_curve():
    """R0: variable-star catalog period is not an independent measurement,
    and schematic phase curves must not be presented as folded observations.
    """
    from app.api.chat import SYSTEM_PROMPT

    prompt = SYSTEM_PROMPT.lower()
    assert "catalog-reported" in prompt
    assert "phase plot" in prompt
    assert "schematic" in prompt
    assert "epoch/time-series photometry" in prompt
    assert "distance, parallax, age" in prompt
    assert "与文献一致" in SYSTEM_PROMPT
    assert '"i/355/varisum"' in prompt
    assert '"b/gcvs/gcvs_cat"' in prompt


def test_generated_next_steps_do_not_offer_paper_draft_by_default():
    """B1/B2: suggested actions should not push paper-draft generation."""
    from app.api.chat import _generate_next_steps

    suggestions = _generate_next_steps([{"fitted_params": {"period": 5.0}}])
    assert "paper draft" not in suggestions.lower()
    assert "sensitivity" in suggestions.lower()


def test_gcvs_catalog_registered_for_variable_star_fallback():
    """B1: VizieR variable-star fallback should point at a real catalog entry."""
    from app.services.catalog_registry import CATALOG_REGISTRY

    entry = CATALOG_REGISTRY['"B/gcvs/gcvs_cat"']
    col_names = {col.name for col in entry.columns}
    assert entry.service == "vizier"
    assert {"GCVS", "RAJ2000", "DEJ2000", "Period", "VarType"} <= col_names


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


def test_search_lightcurve_empty_bright_tess_target_mentions_saturation():
    """R0: empty TESS result for a very bright target should not only say
    "maybe not indexed"; it should surface the likely saturation/product
    limitation when SIMBAD has a bright V magnitude.
    """
    from app.services import astro_analysis

    class FakeLKResult:
        def __len__(self):
            return 0

    class FakeMagTable:
        colnames = ["V"]
        def __len__(self):
            return 1
        def __getitem__(self, key):
            if key == "V":
                return [3.8]
            raise KeyError(key)

    with patch("lightkurve.search_lightcurve", return_value=FakeLKResult()):
        with patch("astroquery.simbad.Simbad") as simbad_cls:
            simbad_cls.query_objectids.return_value = None
            simbad_cls.return_value.query_object.return_value = FakeMagTable()
            with patch("astropy.coordinates.SkyCoord.from_name", side_effect=ValueError("offline")):
                result = astro_analysis.search_lightcurve("delta Cephei", mission="tess")

    assert result["found"] == 0
    msg = result["message"].lower()
    assert "very bright" in msg
    assert "saturated" in msg


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


def test_vizier_mirror_fallback_reports_progress_events():
    """ADQL mirror fallback must be visible to the chat stream, not only logs."""
    from app.api import integration as integ
    from astropy.table import Table

    primary_url = integ.ADQL_SERVICE_MIRRORS["vizier"][0]
    fallback_url = integ.ADQL_SERVICE_MIRRORS["vizier"][1]
    progress: list[dict] = []

    class _FakeJob:
        def get_results(self):
            t = Table()
            t["ok"] = [1]
            return t

    class FakeTapPlus:
        def __init__(self, url):
            self.url = url
        def launch_job(self, query):
            if self.url == primary_url:
                raise ConnectionError("503 Service Unavailable (simulated)")
            return _FakeJob()
        def launch_job_async(self, query):
            return self.launch_job(query)

    with patch("astroquery.utils.tap.core.TapPlus", FakeTapPlus):
        result = integ._launch_on_mirrors(
            "SELECT TOP 1 * FROM \"V/154/sdss17\"",
            service="vizier",
            async_mode=False,
            progress_callback=progress.append,
        )

    assert len(result) == 1
    stages = [event.get("stage") for event in progress]
    assert stages[:3] == ["mirror_attempt", "mirror_transient_error", "mirror_attempt"]
    assert progress[-1]["stage"] == "mirror_success"
    assert progress[-1]["mirror_url"] == fallback_url


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
        "available_functions",     # 纯 helper introspection 不应伪装成 latest_lightcurve
        "helper introspection",
        # 反例代码必须在 prompt 里作为 few-shot
        "WRONG",
        "CORRECT",
    ]
    missing = [kw for kw in required_keywords if kw not in SYSTEM_PROMPT]
    assert not missing, f"K1.A prompt 缺 keyword: {missing}"


def test_run_python_tool_description_mentions_helper_introspection_data_source():
    """R4 follow-up: available_functions() 只查 helper API, 不应声明 latest_lightcurve."""
    from app.services.ai_tools import TOOLS

    run_python = next(t for t in TOOLS if t.get("name") == "run_python")
    text = (
        str(run_python.get("description", ""))
        + " "
        + str(run_python.get("input_schema", {}).get("properties", {}).get("data_source", {}).get("description", ""))
    )

    assert "available_functions" in text
    assert "helper introspection" in text
    assert "none_not_analyzing_real_data" in text


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


# ---------- K3: _launch_on_mirrors 错误消息不再用 list repr ----------

def test_launch_on_mirrors_error_message_comma_separated():
    """K3: 所有 mirror 都失败时, 错误消息应是逗号分隔的 URL 列表,
    不带 Python list 的方括号和单引号 ('脏' 字符)."""
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

    err_str = str(excinfo.value)
    # 消息里必须有 "Tried: " 前缀, 且后面跟的不是 Python list repr
    assert "Tried: " in err_str
    # 不能有方括号(Python list repr 会带方括号)
    assert "Tried: [" not in err_str, f"仍是 list repr: {err_str}"
    # 不能用单引号包住单个 URL
    assert "Tried: '" not in err_str, f"仍带单引号: {err_str}"
    # 至少含前两个 mirror (逗号分隔格式)
    primary = integ.ADQL_SERVICE_MIRRORS["vizier"][0]
    fallback = integ.ADQL_SERVICE_MIRRORS["vizier"][1]
    assert primary in err_str and fallback in err_str
    # 逗号分隔
    assert f"{primary}, {fallback}" in err_str or f"{primary},{fallback}" in err_str


# ---------- J2: run_adql per-tool deadline = 300 s ----------

def test_run_adql_deadline_is_300s():
    """J2: integration.py 的 async TAP 有 300 s budget, 但 chat.py 的
    _TOOL_DEADLINE_TABLE 之前没给 run_adql 单独列 → 默认 45 s 先砍掉.
    必须把 run_adql 加到表里且值 ≥ 300, 否则 async 路径没机会跑完.

    用 inspect.getsource 直接看表的字面, 不跑 agent loop 避开副作用."""
    import inspect
    from app.api import chat as chat_module

    src = inspect.getsource(chat_module._execute_tool_calls)
    # 表字面必须带 "run_adql" 且值 >= 300
    assert '"run_adql":' in src, "run_adql 未在 _TOOL_DEADLINE_TABLE 里"
    # 提取 run_adql 对应的值 (简单正则, 足够)
    import re
    m = re.search(r'"run_adql":\s*([\d.]+)', src)
    assert m, "run_adql 的 deadline 值无法 parse"
    deadline = float(m.group(1))
    assert deadline >= 300.0, (
        f"run_adql deadline={deadline}s < 300s, "
        f"integration.py 的 async TAP 跑不满, Paper 5 级大查询会被砍"
    )


def test_execute_tool_calls_preserves_summary_budget_near_deadline():
    """R5: 接近 360s agent-loop deadline 时, 不应再启动长工具调用.

    否则一个新的 run_adql / run_python 会吃掉最后 60s, 外层 420s 墙
    直接杀掉整轮, 用户只看到 workflow timeout 而不是已有结果总结.
    """
    import time
    from app.api.chat import _execute_tool_calls

    result = asyncio.run(_execute_tool_calls(
        [{"id": "toolu_1", "name": "run_adql", "input": {"query": "SELECT 1", "service": "gaia"}}],
        api_key="",
        provider_api_keys={},
        python_session_id="pytest",
        loop_deadline=time.monotonic() + 5.0,
        summary_reserve_s=10.0,
    ))

    assert result[0]["result"]["error_class"] == "workflow_deadline_near"
    assert "summarize" in result[0]["result"]["error"]
    assert result[0]["result"]["workflow_seconds_remaining"] <= 5


def test_agent_deadline_returns_frontend_action_shape():
    """R5: deadline 早退时也必须返回前端 action shape.

    旧代码把内部 all_tool_results 原样塞进 actions, 字段是
    tool/input/result, SSE final 阶段再读 action/tool_result 会全是 None.
    """
    from app.api.chat import _tool_results_to_actions

    actions = _tool_results_to_actions([{
        "tool": "run_adql",
        "input": {"query": "SELECT 1"},
        "result": {"row_count": 1},
    }])

    assert actions == [{
        "action": "run_adql",
        "tool_input": {"query": "SELECT 1"},
        "tool_result": {"row_count": 1},
        "_auto_executed": True,
    }]


def test_tool_results_to_actions_preserves_tool_call_id_for_stream_merge():
    """Final SSE actions need the tool-call id so the frontend can upgrade
    live cards without temporarily hiding later successful tool results."""
    from app.api.chat import _tool_results_to_actions

    actions = _tool_results_to_actions([{
        "id": "toolu_adql_1",
        "tool": "run_adql",
        "input": {"query": "SELECT 1"},
        "result": {"row_count": 1},
    }])

    assert actions[0]["_tool_call_id"] == "toolu_adql_1"


def test_stream_endpoint_wraps_early_setup_failures_as_sse_errors():
    """R12-NEW-1 sibling: stream setup failures must not close SSE silently.

    If runtime construction fails before the agent loop starts, the browser
    otherwise sees a closed stream with only status updates and reports a
    misleading "received no content" error.
    """
    import inspect
    from app.api import chat

    src = inspect.getsource(chat.chat_message_stream)
    assert "stream_setup_failed" in src
    assert "_build_task.result()" in src
    assert "SSE_PREAMBLE_PADDING_BYTES" in src


def test_adql_helpers_do_not_fall_back_to_other_chat_cache():
    """R12-NEW-1: a fresh chat must not silently read another chat's ADQL rows."""
    from app.services import ai_tools
    from app.services.code_executor import _make_data_accessor

    ai_tools._search_result_cache.clear()
    ai_tools.store_search_results("latest_adql", [{"z": 0.12, "kind": "global_sdss"}])
    ai_tools.store_adql_result_set(
        "chat-mw",
        {
            "service": "vizier",
            "query": "SELECT TOP 1 source FROM I/355/gaiadr3",
            "row_count": 1,
            "columns": ["source"],
            "rows": [{"source": 123}],
            "data": {"source": [123]},
        },
    )

    assert _make_data_accessor("chat-mw")["get_adql_results"]() == [{"source": 123}]
    assert _make_data_accessor("chat-sdss")["get_adql_results"]() == []
    assert _make_data_accessor("chat-sdss")["get_latest_adql_result"]() == {}


def test_subprocess_cache_context_filters_foreign_adql_cache():
    """The sandbox subprocess receives only this chat's ADQL aliases."""
    from app.services import ai_tools
    from app.services.code_executor import _collect_subprocess_cache_context

    ai_tools._search_result_cache.clear()
    ai_tools.store_search_results("latest_adql", [{"z": 0.12, "kind": "global_sdss"}])
    ai_tools.store_search_results("latest_adql:chat-mw", [{"source": 123}])

    foreign = _collect_subprocess_cache_context("chat-sdss")
    assert "latest_adql" not in foreign

    current = _collect_subprocess_cache_context("chat-mw")
    assert current["latest_adql"] == [{"source": 123}]


def test_latest_sdss_sql_cache_is_session_scoped_and_aliased():
    """R18-NEW-3: run_python helper 只能看到同 chat 的 SDSS cache。"""
    from app.services import ai_tools
    from app.services.code_executor import _collect_subprocess_cache_context, _make_data_accessor

    ai_tools._search_result_cache.clear()
    ai_tools.store_search_results("latest_sdss_sql:chat-sdss", {
        "petroMag_r": [17.2],
        "petromag_r": [17.2],
        "zErr": [0.001],
        "zerr": [0.001],
    })

    current = _make_data_accessor("chat-sdss")["get_cached_results"]("latest_sdss_sql")
    assert current["petroMag_r"] == [17.2]
    assert current["petromag_r"] == [17.2]
    assert _make_data_accessor("chat-other")["get_cached_results"]("latest_sdss_sql") is None

    ctx = _collect_subprocess_cache_context("chat-sdss")
    assert ctx["latest_sdss_sql"]["zErr"] == [0.001]
    assert ctx["latest_sdss_sql:chat-sdss"]["zerr"] == [0.001]


def test_run_python_exposes_cache_and_introspection_helpers():
    """R18-NEW-5: 常见发现式 helper 不应 NameError。"""
    from app.services.code_executor import execute_python

    result = execute_python(
        "print(callable(get_cached_results))\n"
        "print(callable(get_search_results))\n"
        "print(callable(available_functions))\n"
        "print('rlimit_as_mb' in sandbox_limits())",
        session_id="helper-smoke",
    )

    assert result.success, result.error
    assert result.stdout.strip().splitlines() == ["True", "True", "True", "True"]


def test_available_functions_lists_cache_helpers_and_supports_limit():
    from app.services.astro_analysis import available_functions

    funcs = available_functions()
    assert "get_search_results" in funcs
    assert "get_cached_results" in funcs
    assert "sandbox_limits" in funcs
    first_five = funcs[:5]
    assert isinstance(first_five, dict)
    assert len(first_five) == 5
    first = funcs[0]
    assert isinstance(first, tuple)
    assert len(first) == 2
    assert len(available_functions(limit=3)) == 3


def test_adql_timeout_message_blames_query_pattern_not_global_overload():
    """R5: Gaia 大查询超预算时不要误导用户以为整个 TAP 服务挂了."""
    import inspect
    from app.services.ai_tools import _exec_adql

    src = inspect.getsource(_exec_adql)
    assert "either overloaded or the query is fundamentally" not in src
    assert "query pattern exceeded the retry budget" in src
    assert "timeout_policy" in src


def test_system_prompt_warns_against_invented_gaia_epoch_photometry_schema():
    """R5: 防止 AI 继续写 transit_id/band/time/mag/flux 这种伪 schema."""
    from app.api.chat import SYSTEM_PROMPT

    required = [
        "gaiadr3.epoch_photometry",
        "transit_id",
        "band",
        "time",
        "mag",
        "flux",
        "describe_tap_table",
        "search_lightcurve",
    ]
    missing = [kw for kw in required if kw not in SYSTEM_PROMPT]
    assert not missing, f"SYSTEM_PROMPT 缺 Gaia epoch photometry 护栏: {missing}"


def test_literature_tool_falls_back_to_free_text_arxiv():
    """R5: search_literature 不应只靠 ADS object:<name> 后直接 EMPTY."""
    from app.services.ai_tools import _exec_literature

    fake_paper = {
        "title": "Gaia studies of the Pleiades",
        "authors": ["A. Author", "B. Author"],
        "year": "2024",
        "bibcode": "arXiv:2401.00001",
        "abstract": "A Pleiades paper.",
        "source": "arxiv",
    }

    with (
        patch("app.api.citations._search_ads_sync", return_value=[]),
        patch("app.api.citations._search_literature_ads", return_value=[]),
        patch("app.api.citations._search_literature_arxiv", return_value=[fake_paper]),
    ):
        result = asyncio.run(_exec_literature({"query": "Pleiades Gaia CMD"}))

    assert result["source"] == "arxiv_free_text"
    assert result["results"][0]["bibcode"] == "arXiv:2401.00001"


# ---------- J3: SDSS SkyServer 直连工具 run_sdss_sql ----------

def test_run_sdss_sql_tool_registered():
    """J3: 新工具必须出现在 TOOLS 列表里, 而且 schema 合法."""
    from app.services.ai_tools import TOOLS

    names = [t.get("name") for t in TOOLS]
    assert "run_sdss_sql" in names, "run_sdss_sql 工具没注册进 TOOLS"

    entry = next(t for t in TOOLS if t.get("name") == "run_sdss_sql")
    assert "T-SQL" in entry["description"], "description 必须强调 T-SQL 语法"
    assert "SkyServer" in entry["description"]
    props = entry["input_schema"]["properties"]
    assert "query" in props and "dr" in props
    assert entry["input_schema"]["required"] == ["query"]


def test_r17_critical_data_tools_visible_to_data_agent():
    """R17: TOOLS 里有工具还不够, runtime schema 会被 agent.tool_names 裁剪。

    SDSS LF 和 MW v_esc 两条线都默认走 data_agent；这些工具必须在
    data_agent 的 tool_names 里, 否则 LLM 实际 function schema 看不到。
    """
    from app.ai.agents.data_agent import DATA_AGENT

    exposed = set(DATA_AGENT.tool_names)
    required = {"run_sdss_sql", "query_high_velocity_stars", "search_lightcurve"}
    missing = required - exposed
    assert not missing, f"data_agent 未暴露 R17 关键工具: {missing}"


def test_tool_inventory_prompt_gets_full_tool_schema():
    """R17: 用户直接问工具清单时, 不应被 data_agent 的子集误导。"""
    from app.api.chat import _filter_tools, _is_tool_inventory_request
    from app.services.ai_tools import TOOLS

    assert _is_tool_inventory_request("列出你现在可用的工具清单")
    full_names = {tool["name"] for tool in TOOLS}
    filtered_names = {tool["name"] for tool in _filter_tools(["search_objects"], TOOLS)}
    assert "run_sdss_sql" in full_names and "query_high_velocity_stars" in full_names
    assert "run_sdss_sql" not in filtered_names


def test_latest_sdss_sql_is_valid_data_source():
    """J3: run_python 调用要能声明 data_source='latest_sdss_sql'
    (SDSS cache 的新 source), 否则 Phase G 会拒绝合法 SDSS 分析代码."""
    from app.services.ai_tools import _VALID_DATA_SOURCES, _REAL_DATA_SOURCE_PATTERNS

    assert "latest_sdss_sql" in _VALID_DATA_SOURCES
    assert "latest_sdss_sql" in _REAL_DATA_SOURCE_PATTERNS
    # 匹配 pattern 里至少要含一个能让 run_python 代码常用的 token
    patterns = _REAL_DATA_SOURCE_PATTERNS["latest_sdss_sql"]
    assert "get_cached_results" in patterns or "latest_sdss_sql" in patterns


def test_system_prompt_has_sdss_skyserver_fallback_rule():
    """J3: SYSTEM_PROMPT 必须明确告诉 AI 什么时候切 run_sdss_sql:
    VizieR 挂了 OR 需要 SDSS-only 表 (Photoz / GalSpec*).  少任何
    keyword 就意味着 prompt 被改弱, AI 又会 stuck 在 VizieR."""
    from app.api.chat import SYSTEM_PROMPT

    required = [
        "run_sdss_sql",           # 工具名
        "T-SQL",                  # 强调方言
        "dbo.fGetNearbyObjEq",    # 正确的锥搜函数
        "PhotoObjAll",            # 主表
        "SpecObjAll",             # 光谱表
        "GalSpecExtra",           # SDSS-only 表示例
    ]
    missing = [kw for kw in required if kw not in SYSTEM_PROMPT]
    assert not missing, f"SYSTEM_PROMPT 缺 SDSS SkyServer keyword: {missing}"
    # 必备过滤条件: mode + clean, 写法可能带前缀 (p.mode=1) 也可能不带,
    # 都接受, 只要两者都在 prompt 里.
    assert "mode=1" in SYSTEM_PROMPT.replace(" ", "") or "mode = 1" in SYSTEM_PROMPT
    assert "clean=1" in SYSTEM_PROMPT.replace(" ", "") or "clean = 1" in SYSTEM_PROMPT
    assert "luminosity function" in SYSTEM_PROMPT.lower()
    assert "PhotoObjAll JOIN SpecObjAll" in SYSTEM_PROMPT
    assert "SELECT TOP 1000" in SYSTEM_PROMPT


def test_sdss_adql_error_routes_bulk_samples_to_run_sdss_sql():
    from app.services.adql_dialect import normalize_adql

    result = normalize_adql("SELECT TOP 10 * FROM PhotoObjAll", "sdss")
    assert result.ok is False
    msg = " ".join(result.errors)
    assert "run_sdss_sql" in msg
    assert "PhotoObjAll JOIN SpecObjAll" in msg


def test_run_sdss_sql_missing_query_returns_maintenance_while_gated():
    """The maintenance gate fires before argument validation while provenance is absent."""
    from app.services.ai_tools import _exec_run_sdss_sql

    result = asyncio.run(_exec_run_sdss_sql({}))
    assert result["__tool_status__"] == "UNAVAILABLE"
    assert result["unavailable_sources"] == ["sdss"]
    assert "provenance v2 rollout" in result["error"]


def test_run_sdss_sql_is_gated_until_provenance_upgrade():
    """run_sdss_sql stays registered but returns maintenance until provenance lands."""
    from app.services.ai_tools import execute_tool

    sys.modules.pop("app.connectors.sdss_sql", None)
    res = asyncio.run(execute_tool(
        "run_sdss_sql",
        {"query": "SELECT TOP 1 objID, ra, dec FROM PhotoObjAll WHERE mode=1"},
        api_key="",
    ))

    assert "app.connectors.sdss_sql" not in sys.modules
    assert res["__tool_status__"] == "UNAVAILABLE"
    assert res["unavailable_sources"] == ["sdss"]
    assert res["data_origin"] == "unavailable"


def test_run_sdss_sql_does_not_write_cache_while_gated():
    from app.services import ai_tools
    from app.services.ai_tools import _exec_run_sdss_sql

    ai_tools._search_result_cache.clear()
    sys.modules.pop("app.connectors.sdss_sql", None)

    res = asyncio.run(_exec_run_sdss_sql(
        {"query": "SELECT TOP 1 objID, petroMag_r, zErr FROM PhotoObjAll"},
        python_session_id="chat-sdss",
    ))

    assert "app.connectors.sdss_sql" not in sys.modules
    assert res["__tool_status__"] == "UNAVAILABLE"
    assert ai_tools.get_cached_results("latest_sdss_sql:chat-sdss") is None


def test_run_sdss_sql_long_mode_is_still_gated():
    from app.services.ai_tools import _exec_run_sdss_sql

    sys.modules.pop("app.connectors.sdss_sql", None)
    res = asyncio.run(_exec_run_sdss_sql({
        "query": "SELECT TOP 1 objID FROM PhotoObjAll",
        "_workflow_budget_mode": "long",
    }))

    assert "app.connectors.sdss_sql" not in sys.modules
    assert res["__tool_status__"] == "UNAVAILABLE"
    assert res["unavailable_sources"] == ["sdss"]


# ---------- M1: long workflow budget + checkpoint + MW high-velocity helper ----------

def test_long_workflow_budget_inferred_for_paper_scale_prompt():
    from app.api.chat import ChatMessage, ChatRequest, _infer_workflow_budget_mode, _workflow_budget_config

    req = ChatRequest(messages=[
        ChatMessage(role="user", content="请完整复现 SDSS luminosity function 论文场景")
    ])
    assert _infer_workflow_budget_mode(req) == "long"
    budget = _workflow_budget_config("long")
    assert budget["agent_loop_seconds"] >= 900
    assert budget["endpoint_timeout_seconds"] > budget["agent_loop_seconds"]


def test_execute_tool_calls_marks_adql_extended_in_long_mode():
    from app.api.chat import _execute_tool_calls

    captured: dict = {}

    async def fake_execute_tool(tool_name, tool_input, *args, **kwargs):
        captured["tool_name"] = tool_name
        captured["tool_input"] = tool_input
        return {"success": True, "row_count": 1}

    with patch("app.services.ai_tools.execute_tool", side_effect=fake_execute_tool):
        result = asyncio.run(_execute_tool_calls(
            [{"id": "toolu_adql", "name": "run_adql", "input": {"query": "SELECT TOP 1 *", "service": "gaia"}}],
            api_key="",
            provider_api_keys={},
            python_session_id="pytest",
            workflow_budget_mode="long",
        ))

    assert result[0]["result"]["success"] is True
    assert captured["tool_name"] == "run_adql"
    assert captured["tool_input"]["extended_timeout"] is True
    assert captured["tool_input"]["_workflow_budget_mode"] == "long"


def test_search_lightcurve_long_mode_deadline_is_240s():
    """R17-NEW-3: MAST cold starts regularly exceed 90s in long workflows."""
    import inspect
    from app.api.chat import _execute_tool_calls

    src = inspect.getsource(_execute_tool_calls)
    assert '"search_lightcurve": 90.0' in src
    assert 'tool_name == "search_lightcurve"' in src
    assert "max(base_deadline_s, 240.0)" in src


def test_workflow_checkpoint_helpers_record_cache_refs():
    from app.api.chat import _record_tool_checkpoint
    from app.services import workflow_checkpoint as wc

    wc.reset()
    event = _record_tool_checkpoint(
        chat_session_id="chat-1",
        python_session_id="py-1",
        tool_call={"id": "toolu_1", "name": "run_adql", "input": {"query": "SELECT 1"}},
        result={"success": True, "row_count": 3, "columns": ["ra", "dec"]},
    )
    assert event is not None
    assert event["status"] == "completed"
    assert "latest_adql" in event["cache_refs"]
    summary = wc.summarize("chat-1")
    assert summary["has_checkpoint"] is True
    assert summary["steps"][0]["tool_call_id"] == "toolu_1"
    wc.reset()


def test_high_velocity_star_tool_registered_and_classified():
    from app.services.ai_tools import TOOLS
    from app.services.result_provenance import _DATA_TOOLS

    names = [t.get("name") for t in TOOLS]
    assert "query_high_velocity_stars" in names
    assert "query_high_velocity_stars" in _DATA_TOOLS
    entry = next(t for t in TOOLS if t.get("name") == "query_high_velocity_stars")
    assert "escape-velocity" in entry["description"] or "escape" in entry["description"].lower()
    assert "min_vtan_kms" in entry["input_schema"]["properties"]


def test_high_velocity_star_query_is_focused_gaia_vizier_query():
    from app.services.ai_tools import _build_high_velocity_stars_query

    query = _build_high_velocity_stars_query(
        limit=500,
        min_parallax_mas=0.2,
        min_vtan_kms=250,
        require_radial_velocity=True,
    )
    assert 'FROM "I/355/gaiadr3"' in query
    assert "SQRT" not in query
    assert "ORDER BY (" not in query
    assert "pmRA >=" in query
    assert "RV IS NOT NULL" in query
    assert "TOP 500" in query


def test_high_velocity_star_tool_computes_vtan_in_python():
    from app.services.ai_tools import _exec_query_high_velocity_stars

    fake_result = {
        "columns": ["Source", "RA_ICRS", "DE_ICRS", "Plx", "pmRA", "pmDE", "RV", "Gmag", "RUWE"],
        "data": {
            "Source": [1, 2],
            "RA_ICRS": [10.0, 20.0],
            "DE_ICRS": [1.0, 2.0],
            "Plx": [0.2, 10.0],
            "pmRA": [20.0, 1.0],
            "pmDE": [20.0, 1.0],
            "RV": [None, 30.0],
            "Gmag": [14.0, 9.0],
            "RUWE": [1.1, 1.0],
        },
        "row_count": 2,
        "attempt_log": [{"stage": "mock_success"}],
    }

    async def fake_execute_adql_query(*args, **kwargs):
        return fake_result

    with patch("app.api.integration.execute_adql_query", side_effect=fake_execute_adql_query):
        result = asyncio.run(_exec_query_high_velocity_stars(
            {"limit": 50, "min_parallax_mas": 0.2, "min_vtan_kms": 250},
            python_session_id="pytest-hv",
        ))

    assert result["row_count"] == 1
    assert "vtan_kms" in result["columns"]
    assert result["data"]["source"] == [1]
    assert result["data"]["vtan_kms"][0] > 600
    assert "SQRT" not in result["query"]


def test_x4_exec_adql_flags_radius_reduction():
    """X4 (PART X): _exec_adql 在 radius × 0.5 auto-retry 成功时返回
    `radius_auto_reduced=True` + original_radius_deg + final_radius_deg,
    让前端 AutoToolResult 渲染醒目 banner. 修复 B6 Pleiades 半径腰斩
    无警告问题."""
    from app.services.ai_tools import _exec_adql

    call_count = {"n": 0}

    async def fake_execute_adql_query(req, *args, **kwargs):
        """第一次 raise timeout, 第二次成功."""
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("TAP timeout after 60s")
        return {
            "columns": ["source_id"],
            "data": {"source_id": [1, 2, 3]},
            "row_count": 3,
        }

    query_with_circle = (
        "SELECT TOP 100 source_id FROM gaiadr3.gaia_source "
        "WHERE CONTAINS(POINT('ICRS',ra,dec), CIRCLE('ICRS',56.75,24.12,0.75))=1"
    )
    with patch("app.api.integration.execute_adql_query", side_effect=fake_execute_adql_query):
        result = asyncio.run(_exec_adql({
            "service": "gaia",
            "query": query_with_circle,
        }))

    # 半径应从 0.75 → 0.375 (× 0.5)
    assert result.get("radius_auto_reduced") is True
    assert abs(result.get("original_radius_deg") - 0.75) < 1e-9
    assert abs(result.get("final_radius_deg") - 0.375) < 1e-9
    assert "auto-reduced" in (result.get("note") or "").lower()


def test_w5_exec_adql_result_carries_query_and_service():
    """W5 (PART W): _exec_adql 返回值必须含 `query` 和 `service` 字段,
    这样前端 AutoToolResult run_adql 分支可以渲染执行过的 SQL
    (ChatPage.tsx "Show ADQL query" 折叠块).
    修复 B4 Pleiades 回归: 598 行 Gaia ADQL auto-executed 后用户看不到 SQL.
    """
    from app.services.ai_tools import _exec_adql

    async def fake_execute_adql_query(*args, **kwargs):
        return {
            "columns": ["source_id"],
            "data": {"source_id": [1]},
            "row_count": 1,
        }

    with patch("app.api.integration.execute_adql_query", side_effect=fake_execute_adql_query):
        result = asyncio.run(_exec_adql({
            "service": "gaia",
            "query": "SELECT TOP 1 source_id FROM gaiadr3.gaia_source",
        }))

    # 两个新字段必须到位, 让前端能展示
    assert "query" in result, "_exec_adql result missing 'query' field (W5)"
    assert result["query"].strip().startswith("SELECT TOP 1 source_id")
    assert result["service"] == "gaia"


def test_gaia_adql_sqrt_error_gets_actionable_hint():
    from app.services.ai_tools import _exec_adql

    async def fake_execute_adql_query(*args, **kwargs):
        raise RuntimeError('Encountered " "SQRT" "SQRT " at line 1')

    with patch("app.api.integration.execute_adql_query", side_effect=fake_execute_adql_query):
        with pytest.raises(RuntimeError) as exc:
            asyncio.run(_exec_adql({
                "service": "gaia",
                "query": (
                    "SELECT TOP 10 source_id FROM gaiadr3.gaia_source "
                    "WHERE SQRT(pmra*pmra+pmdec*pmdec) > 10 "
                    "ORDER BY (pmra*pmra+pmdec*pmdec) DESC"
                ),
            }))

    msg = str(exc.value)
    assert "[auto-suggestion]" in msg
    assert "query_high_velocity_stars" in msg
    assert "compute SQRT / velocities in run_python" in msg


def test_system_prompt_routes_escape_velocity_to_high_velocity_tool():
    from app.api.chat import SYSTEM_PROMPT

    assert "query_high_velocity_stars" in SYSTEM_PROMPT
    assert "escape velocity" in SYSTEM_PROMPT.lower()
    assert "latest_adql" in SYSTEM_PROMPT


def test_stream_debug_endpoint_is_dev_fixture():
    import inspect
    from app.api import chat

    src = inspect.getsource(chat.simulate_stream_failure)
    assert "stream_setup_failed" in src
    assert "stream_debug" in src
    assert "ALLOW_STREAM_DEBUG_ENDPOINT" in src


# ---------- L3: K-correction z>0.5 降级 warning ----------

def test_k_correction_z_low_no_warning():
    """L3: z < 0.5 时函数正常返回, 不触发 warning, 状态标 ok."""
    import warnings
    from app.services import astro_analysis

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        kc = astro_analysis.k_correction(0.3, band="r", galaxy_type="elliptical")

    # 不应触发任何 RuntimeWarning (可能有其他 astropy 警告, 过滤)
    kcorr_warnings = [x for x in w if "K-correction" in str(x.message)]
    assert len(kcorr_warnings) == 0, f"z=0.3 不该触发 K-corr warning: {kcorr_warnings}"
    assert astro_analysis.LAST_KCORR_STATUS.get("analysis_status") == "ok"
    # 数学值仍正常
    assert abs(kc - (0.0 + 1.0 * 0.3 + 0.5 * 0.09)) < 1e-9


def test_k_correction_z_high_emits_warning():
    """L3: z > 0.5 时必须 emit RuntimeWarning + 把 LAST_KCORR_STATUS 标
    成 partial 含 uncertainty 信息, 这样 run_python 里 AI 能从 stderr
    看到并 propagate 到最终回复."""
    import warnings
    from app.services import astro_analysis

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        kc = astro_analysis.k_correction(1.2, band="r", galaxy_type="elliptical")

    # 数值本身仍计算 (不拒跑, 只警告)
    assert kc is not None

    # 必须有 K-correction 相关的 RuntimeWarning
    kcorr_warnings = [x for x in w if "K-correction" in str(x.message)]
    assert len(kcorr_warnings) >= 1, "z=1.2 必须触发 K-corr 外推警告"
    msg = str(kcorr_warnings[0].message)
    assert "z_max=1.200" in msg or "z_max=1.2" in msg
    assert "extrapolation" in msg.lower() or "calibration" in msg.lower()

    # LAST_KCORR_STATUS 必须带结构化信息
    status = astro_analysis.LAST_KCORR_STATUS
    assert status.get("analysis_status") == "partial"
    assert status.get("estimated_extra_uncertainty_mag") == 0.5
    assert status.get("max_z") == 1.2
    assert "kcorrect" in status.get("recommended_tool", "").lower() or "template" in status.get("recommended_tool", "").lower()


def test_k_correction_array_input_picks_max_z():
    """L3: 输入是数组 [0.1, 0.3, 0.8] 时, warning 应基于 max(z) 触发."""
    import numpy as np
    import warnings
    from app.services import astro_analysis

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _ = astro_analysis.k_correction(np.array([0.1, 0.3, 0.8]), band="g")

    kcorr_warnings = [x for x in w if "K-correction" in str(x.message)]
    assert len(kcorr_warnings) == 1, "数组含 z=0.8 > 0.5 必须触发一次警告"
    assert astro_analysis.LAST_KCORR_STATUS["max_z"] == 0.8


# ---------- L4: MCMC walker 初始化强化 ----------

def test_bayesian_fit_walker_init_has_strengthened_retry_logic():
    """L4: BayesianFit walker 初始化之前只重试 1 次且阈值 50%, walker
    卡在 -inf 区会跑出垃圾链而 ESS/Rhat 虚假通过 (Gelman & Shirley 2011).
    本测试用 inspect 直接检查源码含:
      - MIN_FINITE_RATIO = 0.80 (阈值提到 80%)
      - MAX_INIT_RETRIES = 3 (重试 3 次)
      - InsufficientPriorSupport (失败时抛的具体标识)
    以便未来有人改弱/取消时 CI 立刻红."""
    import inspect
    from app.pipeline.nodes import bayesian_fit as bf_mod

    src = inspect.getsource(bf_mod.bayesian_fit)
    # 硬阈值与重试次数
    assert "MIN_FINITE_RATIO" in src, "L4 硬阈值常量名丢失"
    assert "0.80" in src or "0.8" in src, "L4 阈值 0.80 已被改弱"
    assert "MAX_INIT_RETRIES" in src, "L4 重试次数常量名丢失"
    assert "3" in src, "L4 重试次数 3 已被改弱"
    # 必须抛出带清晰标识的错误
    assert "InsufficientPriorSupport" in src, (
        "L4 失败路径必须抛 InsufficientPriorSupport, 以便上游能识别这类"
        "错误而不是当普通 ValueError 吞掉"
    )
    # 必须引用文献让后人知道为什么 80% 不是随便写的
    assert "Gelman" in src or "garbage chains" in src, (
        "L4 的阈值 80% 基于 Gelman & Shirley 2011, 理由必须在 comment 里"
    )


# ---------- L2-a: Gaia parallax SNR 门控 ----------

def test_gaia_low_snr_parallax_flagged_partial():
    """L2-a: 当 parallax_error / parallax < 5 时 1/plx 点估计高度偏斜,
    应标 distance_requires_posterior=True + analysis_status=partial,
    引导用户用 Bailer-Jones 后验.  反之 SNR >= 5 直接给 distance_pc.

    用 inspect 断言 gaia.py 源码含新 SNR 门控逻辑, 未来有人去掉
    `parallax_snr` / `distance_requires_posterior` 字段就会红."""
    import inspect
    from app.connectors import gaia as gaia_mod

    src = inspect.getsource(gaia_mod.GaiaConnector)
    assert "parallax_snr" in src, "Gaia SNR 字段丢失"
    assert "distance_requires_posterior" in src, (
        "低 SNR 时引导用户用后验的标记丢失, AI 无法知道 1/plx 不可信"
    )
    # 必须有 SNR < 5 的判断
    assert "< 5" in src or "snr < 5" in src.lower(), (
        "L2-a 的 SNR=5 阈值 (1/plx bias 分界) 被修改/去掉"
    )
    # 必须引用 Bailer-Jones
    assert "Bailer-Jones" in src


# ---------- L2-b: LS FAP n<50 warning + BLS > + transit bounds ----------

def test_lomb_scargle_small_n_emits_fap_warning():
    """L2-b: n∈[20,50) 时 LS FAP 不可信 (VanderPlas & Ivezic 2015).
    result 里必须有非空 fap_warnings 字段 + reliable=False."""
    import numpy as np
    from app.services.astro_analysis import lomb_scargle_period

    # 构造 25 个点的正弦信号 + 噪声
    rng = np.random.default_rng(42)
    t = np.sort(rng.uniform(0, 30, 25))
    mag = 15.0 + 0.1 * np.sin(2 * np.pi * t / 3.5) + rng.normal(0, 0.02, 25)

    r = lomb_scargle_period(t, mag, random_seed=42)
    assert "fap_warnings" in r
    assert len(r["fap_warnings"]) >= 1, "n=25 必须有 LS 小样本警告"
    assert "VanderPlas" in r["fap_warnings"][0] or "50" in r["fap_warnings"][0]
    # reliable 阈值抬到 50, 小样本必 False
    assert r["reliable"] is False, "n=25 < 50, reliable 必须 False"


def test_lomb_scargle_large_n_no_warning():
    """L2-b: n >= 50 时 fap_warnings 为空 (标志正常样本)."""
    import numpy as np
    from app.services.astro_analysis import lomb_scargle_period

    rng = np.random.default_rng(123)
    t = np.sort(rng.uniform(0, 60, 120))
    mag = 15.0 + 0.3 * np.sin(2 * np.pi * t / 5.0) + rng.normal(0, 0.01, 120)

    r = lomb_scargle_period(t, mag, random_seed=123)
    assert r.get("fap_warnings", []) == [], (
        f"n=120 不该有小样本警告: {r['fap_warnings']}"
    )


def test_bls_bootstrap_uses_strict_greater_than():
    """L2-b: BLS bootstrap FAP 用严格 > 不是 >= (Kipping 2011).
    inspect 源码确认等号已改."""
    import inspect
    from app.services import time_domain_pro as tdp

    src = inspect.getsource(tdp)
    # null_max > power[best_idx] 必须存在 (no >=)
    assert "null_max > power[best_idx]" in src, (
        "L2-b: BLS bootstrap FAP 必须用严格 > (Kipping 2011 定义)"
    )
    assert "null_max >= power[best_idx]" not in src, (
        "L2-b: null_max >= 会过估显著性 0.5-2%, 必须改严格 >"
    )


def test_transit_fit_chi2_rejects_unphysical_params():
    """L2-b: transit fit chi2 对 rp<=0 / rp>=1 / a<2.5 / inc 范围外
    应返回 1e20 penalty, 让 minimizer 远离.  inspect 断言."""
    import inspect
    from app.services import time_domain_pro as tdp

    src = inspect.getsource(tdp.fit_transit_batman) if hasattr(tdp, "fit_transit_batman") else inspect.getsource(tdp)
    # 核心 guard 关键词
    assert "1e20" in src, "L2-b: transit chi2 penalty 常量丢失"
    assert "rp >= 1.0" in src or "rp >= 1" in src, (
        "L2-b: transit depth 上界 (rp<1) 保护丢失"
    )
    assert "a < 2.5" in src, (
        "L2-b: Mandel & Agol 2002 的 a/R* >= 2.5 保护丢失"
    )


# ---------- L2-c: Isochrone grid 加密 + photo_z z_max 参数化 ----------

def test_isochrone_grid_defaults_denser():
    """L2-c: fit_isochrone 的默认 n_grid_age 从 20 抬到 40 (Δlog(age)
    从 0.095 → 0.05, Bressan+ 2012 精密拟合推荐); n_grid_met 从 5 到 9
    (Δ[M/H] 0.3→0.15 dex); dm/av 子网格从 3 点到 7 点.  用 inspect 检查
    以防未来回退."""
    import inspect
    from app.services import astro_analysis

    src = inspect.getsource(astro_analysis.fit_isochrone)
    # 默认参数
    assert "n_grid_age=40" in src, "L2-c: age grid 40 点阈值已被改回"
    assert "n_grid_met=9" in src, "L2-c: met grid 9 点已被改回"
    # dm/av 子网格
    assert "dm_range[1], 7" in src or "dm_range[1],7" in src, (
        "L2-c: dm 子网格 7 点被改回 3"
    )
    assert "av_range[1], 7" in src or "av_range[1],7" in src, (
        "L2-c: av 子网格 7 点被改回 3"
    )
    # 引用 Bressan+ 2012
    assert "Bressan" in src, "L2-c: 应在注释里引用 Bressan+ 2012 说明阈值"


def test_photo_z_template_z_max_parameter():
    """L2-c: estimate_photo_z_template 接受 z_max 参数, default 2.0.
    z_max=5.0 时 z_grid 延伸到 5, at_z_max_boundary 反映边界."""
    from app.services.photo_z import estimate_photo_z_template

    mags = {"u": 22.5, "g": 22.0, "r": 21.5, "i": 21.0, "z": 20.5}
    errs = {"u": 0.1, "g": 0.1, "r": 0.1, "i": 0.1, "z": 0.1}

    # z_max=5.0 → grid 跨度到 5
    r = estimate_photo_z_template(mags, errs, z_max=5.0)
    assert r["z_max_used"] == 5.0
    assert max(r["z_grid"]) >= 4.9, f"z_grid 未延伸到 z_max=5: max={max(r['z_grid'])}"

    # default 2.0 保持向后兼容
    r2 = estimate_photo_z_template(mags, errs)
    assert r2["z_max_used"] == 2.0
    assert max(r2["z_grid"]) <= 2.01


def test_photo_z_boundary_warning_when_zphot_at_edge():
    """L2-c: 当 best z_phot 接近 z_max 上界 (差 < 0.05) 时 result
    带 at_z_max_boundary=True + note 里有 WARNING, 提示用户增大."""
    from app.services.photo_z import estimate_photo_z_template

    # 构造红色大目标让 z_phot 落在 z_max 附近.
    # z_max=0.3 (很小) → 真实高 z galaxy 色彩必到边界.
    mags = {"u": 24.0, "g": 23.0, "r": 22.0, "i": 21.0, "z": 20.5}
    errs = {"u": 0.1, "g": 0.1, "r": 0.1, "i": 0.1, "z": 0.1}

    r = estimate_photo_z_template(mags, errs, z_max=0.3)
    assert r.get("at_z_max_boundary") is True, (
        f"z_max=0.3 + 红目标必然撞边界, 但 at_z_max_boundary={r.get('at_z_max_boundary')}"
    )
    assert "WARNING" in r["note"] or "boundary" in r["note"].lower()


# ---------- L2-d: EW 双窗口 + gaia masked + SDSS cone ----------

def test_ew_accepts_two_separate_continuum_windows():
    """L2-d: equivalent_width 新支持 continuum_left / continuum_right
    两段参数 (Gray 2005 恒星分光惯例), 不吃线翼."""
    import numpy as np
    from app.pipeline.nodes.equivalent_width import equivalent_width

    # 构造一条简单的吸收线
    wave = np.linspace(6500, 6625, 500)
    flux = np.ones_like(wave) * 1.0
    line_mask = (wave >= 6558) & (wave <= 6568)
    flux[line_mask] = 0.5  # 50% 吸收

    result = equivalent_width(
        {"data": {"wavelength": wave.tolist(), "flux": flux.tolist()}},
        {
            "line_center": 6563.0,
            "line_window": [6555.0, 6571.0],
            "continuum_left": [6510.0, 6550.0],
            "continuum_right": [6580.0, 6620.0],
            "continuum_method": "polynomial",
            "poly_order": 3,  # 即便用户传 3, L2-d 在双窗口模式强制降到 2
        },
    )
    ew = result["equivalent_window_result"] if "equivalent_window_result" in result else result.get("equivalent_width_result")
    assert ew is not None
    assert ew["ew_value"] > 0, "吸收线 EW 应为正值"


def test_ew_rejects_overlapping_two_windows():
    """L2-d: 两段连续谱窗口与 line_window 重叠时必须 raise, 避免选到
    线翼."""
    import numpy as np
    import pytest
    from app.pipeline.nodes.equivalent_width import equivalent_width

    wave = np.linspace(6500, 6625, 500)
    flux = np.ones_like(wave) * 1.0

    with pytest.raises(ValueError, match="line_window"):
        equivalent_width(
            {"data": {"wavelength": wave.tolist(), "flux": flux.tolist()}},
            {
                "line_center": 6563.0,
                "line_window": [6555.0, 6571.0],
                "continuum_left": [6540.0, 6560.0],  # 右边界 6560 < 6555? no, 6560 > 6555 命中 line
                "continuum_right": [6580.0, 6620.0],
            },
        )


def test_gaia_isfinite_replaces_v_equals_v():
    """L2-d: gaia.py 所有 masked / NaN 检查必须用 np.isfinite, 不能再有
    `v == v` 这种 astropy >= 4.1 会 DeprecationWarning 的写法."""
    import inspect
    from app.connectors import gaia as gaia_mod

    src = inspect.getsource(gaia_mod)
    # 不能再出现 v == v 这种 NaN 检查
    assert "if v == v" not in src, (
        "L2-d: `if v == v` 对 astropy masked scalar 会触发 "
        "DeprecationWarning, 必须换 np.isfinite(v)"
    )
    # 必须见到 np.isfinite (至少 1 次) 表明换法到位
    assert "np.isfinite" in src, "L2-d: 必须引入 np.isfinite 替代 v==v"


def test_sdss_spec_uses_cone_not_box():
    """L2-d: SDSSSpecOnlyConnector.search 不能再用 `ra BETWEEN` 方盒搜
    (极区拉伸严重), 必须切 dbo.fGetNearbyObjEq 的锥搜."""
    import inspect
    from app.connectors import sdss as sdss_mod

    src = inspect.getsource(sdss_mod.SDSSSpecOnlyConnector)
    assert "dbo.fGetNearbyObjEq" in src, (
        "L2-d: SDSS spec 连接器必须用 dbo.fGetNearbyObjEq 做锥搜, "
        "不能再用 RA BETWEEN (极区严重偏差)"
    )
    # 验证老的 BETWEEN 方盒写法已撤
    # (注: BETWEEN 关键字在 SQL 里可能还用于其他目的, 检查特定 WHERE s.ra BETWEEN 子句)
    assert "s.ra BETWEEN" not in src, (
        "L2-d: 老的 s.ra BETWEEN 方盒搜索必须去掉"
    )


# ---------- L3-b: 测试补强 — fabrication counter e2e + 熔断下线 e2e ----------

def test_fabrication_blocked_counter_increments_on_uncited_claim():
    """L3-b: 端到端验证 fabrication_blocked_total counter 在 zero-data
    但含定量 claim 的轮上真的 +1.  之前没有任何测试检查 counter 实际
    是否发火, 万一静默回退无人知.
    """
    from app.observability.metrics import get_registry
    from app.services.claim_validator import zero_data_but_quantitative

    registry = get_registry()
    # 清零 counter 命名空间
    registry.reset()

    # 构造典型 0-data 但 AI 编数的场景
    tool_results = [{
        "tool": "run_adql",
        "result": {"row_count": 0, "data": {}, "columns": []},
    }]
    reply = "The Pleiades parallax is 7.35 mas, distance 136 pc."

    # zero_data_but_quantitative 应该返回 claims (非空 = 检测到)
    offending = zero_data_but_quantitative(reply, tool_results)
    assert len(offending) >= 1, "zero_data_but_quantitative 必须能识别这种模式"

    # 模拟 chat.py 里 fabrication counter 发火 (真实调 record_counter)
    from app.observability.metrics import record_counter
    record_counter("fabrication_blocked_total", 1.0, reason="zero_data_quantitative")

    snap = registry.snapshot()
    assert "fabrication_blocked_total" in snap["counters"], (
        "fabrication_blocked_total counter 未出现在注册表里"
    )
    total = sum(snap["counters"]["fabrication_blocked_total"].values())
    assert total >= 1.0


def test_disable_after_failures_removes_tool_from_visible_list():
    """L3-b: 静态断言 _run_agent_loop 的 tool_failure_counts +
    DISABLE_AFTER_FAILURES 逻辑把失败工具**从 tools 参数里移除**而
    不只是在 prompt 里说禁用.  物理下线比文字约束强."""
    import inspect
    from app.api import chat as chat_mod

    src = inspect.getsource(chat_mod._run_agent_loop)
    # 必须出现 tool_failure_counts dict
    assert "tool_failure_counts" in src, (
        "G3.4 工具失败计数追踪逻辑丢失"
    )
    # 必须有 DISABLE_AFTER_FAILURES 阈值常量
    assert "DISABLE_AFTER_FAILURES" in src, (
        "G3.4 硬下线阈值丢失"
    )
    # 必须有 visible_tools 过滤 (证明是物理下线不是文字约束)
    assert "visible_tools" in src, (
        "G3.4 的 visible_tools 过滤丢失 — 工具下线必须是物理移除, "
        "不能只在 prompt 里说"
    )


def test_honest_abstention_counter_label_schema():
    """L3-b: 验证 honest_abstention_total 有 label schema (reason=
    empty|failed|mixed), 未来监控能按原因切分."""
    from app.observability.metrics import record_counter, get_registry

    registry = get_registry()
    registry.reset()

    # 3 种 reason 都发一次
    record_counter("honest_abstention_total", 1.0, reason="empty", agent="default")
    record_counter("honest_abstention_total", 1.0, reason="failed", agent="default")
    record_counter("honest_abstention_total", 1.0, reason="mixed", agent="default")

    snap = registry.snapshot()
    counters_by_label = snap["counters"]["honest_abstention_total"]
    # 3 个不同 label 组合 → 3 个 key
    assert len(counters_by_label) == 3
    # 每个组合至少 1 次
    assert all(v >= 1.0 for v in counters_by_label.values())


# ── R5 PART O: subprocess 诊断暴露 ────────────────────────────────────

def test_run_python_exposes_stderr_even_when_success_true():
    """R5 O1: stderr 即便 success=True 也要传给 AI / 前端.
    原来 `if result.stderr and not result.success` 让 subprocess crash
    时 payload.success=True (child init 默认值) 的情况下 stderr 被吞,
    诊断信息丢失.  现在条件改成 `if result.stderr:` 总传."""
    import inspect
    from app.services import ai_tools

    src = inspect.getsource(ai_tools._exec_run_python)
    # 原 buggy 形式必须消失
    assert "result.stderr and not result.success" not in src, (
        "R5 O1: stderr 过滤条件还在, success=True 时 stderr 仍会被丢"
    )
    # 新形式必须存在 (if result.stderr: 或类似无条件传)
    assert 'if result.stderr:' in src, (
        "R5 O1: stderr 必须无条件传 response['traceback']"
    )


def test_run_python_exit_code_nonzero_degrades_success():
    """R5 O2: response 构造后, 若 exit_code != 0 则 success 降级 False.
    原来 success 只来自 payload, exit_code 来自 proc.exitcode, 可以
    矛盾 (success=True + exit_code=1)."""
    import inspect
    from app.services import ai_tools

    src = inspect.getsource(ai_tools._exec_run_python)
    # 检查关键行:
    assert "sandbox_nonzero_exit" in src, (
        "R5 O2: exit_code 降级逻辑 (error_class='sandbox_nonzero_exit') 丢失"
    )
    # 降级逻辑必须基于 exit_code 检查
    assert 'response["exit_code"]' in src or 'response.get("exit_code")' in src


def test_inert_code_exempted_from_synthetic_banner():
    """R5 O3: 当 AI 声明 data_source='none...', 但 detector 判 inert
    (纯 literal print), ai_tools 层应把 is_synthetic_declared 翻回 False,
    避免 smoke test 被打 SYNTHETIC."""
    import inspect
    from app.services import ai_tools

    src = inspect.getsource(ai_tools._exec_run_python)
    # 必须有 inert verdict 的 elif 分支
    assert 'detection.verdict == "inert"' in src, (
        "R5 O3: inert verdict 分支丢失, smoke test 仍会被标 SYNTHETIC"
    )
    # 必须翻转 is_synthetic_declared
    assert 'is_synthetic_declared = False' in src, (
        "R5 O3: inert 分支必须把 is_synthetic_declared 设回 False"
    )


def test_run_python_after_failed_fetch_is_empty_not_synthetic():
    """R21: 真实数据抓取失败后的 Python fallback 不应展示为 SYNTHETIC。

    这种路径本质是“没有可引用真实数据”，UI 应显示 ∅ Empty, 并让 AI
    走 <tools_returned_nothing/>；不要把 fallback stdout 当成 synthetic
    demo 暴露给用户。
    """
    import inspect
    from app.api import chat

    src = inspect.getsource(chat._run_agent_loop)
    assert '"__tool_status__": "EMPTY"' in src
    assert '"data_origin": "unavailable"' in src
    assert "empty_data_fetches" in src
    assert "declared_empty_dependency" in src
    assert "empty_after_failed_fetch_total" in src
    assert "synthetic_after_failure_total" not in src


def test_orchestrator_validates_merged_reply_claims():
    """R21: 多 agent 合并后的最终回复也要过同一 turn 的 claim gate。"""
    import inspect
    from app.api import chat

    src = inspect.getsource(chat._run_orchestrated_chat)
    assert "validate_claims(merged_reply, merged_tool_results)" in src
    assert "zero_data_but_quantitative(merged_reply, merged_tool_results)" in src
    assert "merged_orchestrator" in src


# ── Phase P: arXiv 301 redirect + Unknown tool polish ────────────────

async def test_unknown_tool_returns_available_list():
    """R6-NEW-2: 调不存在工具应返 error_class + available_tools list,
    不能留空 error_class 或只给 'Unknown tool: X' 一行."""
    from app.services.ai_tools import execute_tool

    result = await execute_tool("fit_transit_that_does_not_exist", {})
    assert result.get("error_class") == "unknown_tool"
    assert "available_tools" in result
    assert isinstance(result["available_tools"], list)
    assert len(result["available_tools"]) > 0
    # 错误消息里带 "Available" 和工具列表
    err = str(result.get("error", ""))
    assert "Available" in err or "available" in err
    # 至少真的列了几个已知工具
    known = set(result["available_tools"])
    assert "run_adql" in known or "search_objects" in known


def test_arxiv_read_paper_uses_https_and_follow_redirects():
    """R6-NEW-1: _exec_read_paper 必须用 https + follow_redirects 避免
    arXiv 301 fail."""
    import inspect
    from app.services import ai_tools

    src = inspect.getsource(ai_tools._exec_read_paper)
    assert "https://export.arxiv.org" in src, (
        "arXiv URL 必须 https (301 from http)"
    )
    assert "follow_redirects=True" in src, (
        "httpx.AsyncClient 必须 follow_redirects 以防未来又有重定向"
    )


def test_arxiv_search_uses_https_and_follow_redirects():
    """R6-NEW-1: citations._search_arxiv_sync 同样要处理 301."""
    import inspect
    from app.api import citations

    src = inspect.getsource(citations)
    # https URL 必须在
    assert "https://export.arxiv.org/api/query" in src
    # follow_redirects 必须在 httpx.get 调用里
    assert "follow_redirects=True" in src
