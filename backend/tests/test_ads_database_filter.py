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
from unittest.mock import AsyncMock, patch


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
