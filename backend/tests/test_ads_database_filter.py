"""PART AD C3 — ADS query scoped to database:astronomy.

R2.9 + M4 audit reproducer: search_literature for [CII] / line-relation
work returned a Noah Rhodes 2022 paper "Co-optimization of power line
shutoff and restoration under high wildfire ignition risk" because the
ADS query had no database scope and "shutoff" / "wildfire" / "ignition"
matched power-engineering papers in ADS' general index.

The fix is a single `fq=database:astronomy` filter query on every ADS
request. This test locks both the async and sync paths.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch


def test_get_literature_context_uses_astronomy_database_fq(monkeypatch) -> None:
    """The async ADS path must include `fq=database:astronomy`."""
    monkeypatch.setenv("ADS_API_KEY", "test-key")

    captured_params: dict = {}

    class _FakeResponse:
        status_code = 200
        def raise_for_status(self) -> None: ...
        def json(self) -> dict:
            return {"response": {"docs": [], "numFound": 0}}

    class _FakeClient:
        def __init__(self, *args, **kwargs): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *args): ...
        async def get(self, url, params=None, headers=None):
            captured_params["params"] = params
            return _FakeResponse()

    from app.services import literature_engine

    with patch.object(literature_engine.httpx, "AsyncClient", _FakeClient):
        asyncio.run(literature_engine.get_literature_context(
            topic="[CII] luminosity FWHM relation",
        ))

    assert captured_params, "ADS request never made"
    fq = captured_params["params"].get("fq")
    assert fq == "database:astronomy", (
        f"expected fq='database:astronomy', got {fq!r}"
    )


def test_search_literature_sync_uses_astronomy_database_fq(monkeypatch) -> None:
    """The sync ADS path must also include the database filter."""
    monkeypatch.setenv("ADS_API_KEY", "test-key")

    captured: dict = {}

    class _FakeResponse:
        status_code = 200
        def raise_for_status(self) -> None: ...
        def json(self) -> dict:
            return {"response": {"docs": []}}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _FakeResponse()

    from app.services import literature_engine

    with patch.object(literature_engine.httpx, "get", fake_get):
        literature_engine.search_literature("AGN variability")

    assert captured["params"].get("fq") == "database:astronomy"


def test_exec_literature_surfaces_retracted_flag_from_ads_property(monkeypatch) -> None:
    """Stage 6 P0c-B (2026-05-19): ADS doc 的 property=['RETRACTED'] 应该映射
    到 result paper 的 retracted=True, 透传给前端 chip + LLM message_to_model."""
    import asyncio
    from app.api import citations
    from app.services.ai_tools import _exec_literature

    # 模拟 ADS 返回 2 篇, 第 1 篇 RETRACTED
    fake_hits = [
        {
            "bibcode": "2020arXiv2007.10785S",
            "title": "Schlafly+2020 retracted paper",
            "authors": ["Schlafly"],
            "year": "2020",
            "doi": None,
            "abstract": "Galactic extinction map.",
            "source": "ads",
            "retracted": True,  # 模拟 mapper 输出 (property=['RETRACTED'] → True)
        },
        {
            "bibcode": "2024arXiv2404.03002D",
            "title": "DESI 2024 BAO good paper",
            "authors": ["DESI"],
            "year": "2024",
            "doi": None,
            "abstract": "DESI DR1 BAO measurements and dark energy constraints.",
            "source": "ads",
            "retracted": False,
        },
    ]
    monkeypatch.setattr(citations, "_search_ads_sync", lambda q: fake_hits)
    monkeypatch.setattr(citations, "_search_literature_ads", lambda q, n: [])
    monkeypatch.setattr(citations, "_search_literature_arxiv", lambda q, n: [])

    out = asyncio.run(_exec_literature({"query": "Galactic extinction"}))
    assert "results" in out
    results = out["results"]
    assert len(results) == 2
    retracted_paper = next(p for p in results if p["bibcode"] == "2020arXiv2007.10785S")
    good_paper = next(p for p in results if p["bibcode"] == "2024arXiv2404.03002D")
    assert retracted_paper["retracted"] is True
    assert good_paper["retracted"] is False
    assert out["retracted_count"] == 1
    # message_to_model 应警告 LLM 严禁引用 retracted
    assert "RETRACTED" in out["__message_to_model__"]
    assert "MUST NOT cite" in out["__message_to_model__"]


def test_search_ads_query_includes_property_field() -> None:
    """Stage 6 P0c-B (2026-05-19): _search_ads_sync + _search_literature_ads
    的 ADS query 必须含 `fl=...,property` 才能拿 RETRACTED tag."""
    import inspect
    from app.api import citations

    src_sync = inspect.getsource(citations._search_ads_sync)
    src_lit = inspect.getsource(citations._search_literature_ads)
    assert "property" in src_sync, "_search_ads_sync 必须 fl 含 property"
    assert "property" in src_lit, "_search_literature_ads 必须 fl 含 property"


def test_ads_get_with_retry_retries_5xx_then_succeeds(monkeypatch) -> None:
    """Stage 6 P0c-E (2026-05-19): 5xx → backoff retry → 第 2 次 200 应返回成功."""
    from app.api import citations

    call_log: list[int] = []

    class _FakeResp:
        def __init__(self, status_code: int, data: dict):
            self.status_code = status_code
            self._data = data
        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"status {self.status_code}")
        def json(self) -> dict:
            return self._data

    def fake_get(url, params=None, headers=None, timeout=None):
        call_log.append(len(call_log))
        if len(call_log) == 1:
            return _FakeResp(503, {})
        return _FakeResp(200, {"response": {"docs": [{"bibcode": "test_bib"}]}})

    # 让 time.sleep 不真睡 (test 快)
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda _s: None)
    monkeypatch.setattr(citations.httpx, "get", fake_get)

    resp = citations._ads_get_with_retry(
        "https://example.test", params={}, headers={}, timeout=5, max_retries=2,
    )
    assert resp is not None
    assert resp.status_code == 200
    assert len(call_log) == 2  # 第 1 次 503, 第 2 次 200


def test_ads_get_with_retry_does_not_retry_429(monkeypatch) -> None:
    """Stage 6 P0c-E: 429 quota exhausted 不 retry, 直接返回 response 让 caller 处理."""
    from app.api import citations

    call_log: list[int] = []

    class _FakeResp:
        status_code = 429
        def raise_for_status(self) -> None: pass
        def json(self) -> dict: return {}

    def fake_get(url, params=None, headers=None, timeout=None):
        call_log.append(len(call_log))
        return _FakeResp()

    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda _s: None)
    monkeypatch.setattr(citations.httpx, "get", fake_get)

    resp = citations._ads_get_with_retry(
        "https://example.test", params={}, headers={}, timeout=5, max_retries=2,
    )
    assert resp is not None
    assert resp.status_code == 429
    assert len(call_log) == 1  # 没 retry, 只调 1 次


