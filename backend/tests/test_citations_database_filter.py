"""PART AF C1 — citations.py path of search_literature also gets the
ADS database filter.

PART AD C3 added `fq=database:astronomy` to literature_engine.py, but
the AI's `search_literature` TOOL calls _exec_literature → citations.py
which is a different module. The M5 audit caught 3 non-astronomy
papers leaking through (EDS Blois HEP / TTC Flowgraphs ×2 software
workshop) — those came from this unfixed path.

Locks the fix: every ADS request from citations.py carries
fq=database:astronomy.
"""

from __future__ import annotations

from unittest.mock import patch


def test_search_ads_sync_sends_database_astronomy(monkeypatch) -> None:
    monkeypatch.setattr("app.api.citations.ADS_API_KEY", "test-key")
    captured: dict = {}

    class _FakeResponse:
        status_code = 200
        def raise_for_status(self) -> None: ...
        def json(self) -> dict:
            return {"response": {"docs": []}}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _FakeResponse()

    from app.api import citations

    with patch.object(citations.httpx, "get", fake_get):
        citations._search_ads_sync("M31")

    assert captured["params"].get("fq") == "database:astronomy"


def test_search_literature_ads_sends_database_astronomy(monkeypatch) -> None:
    monkeypatch.setattr("app.api.citations.ADS_API_KEY", "test-key")
    captured: dict = {}

    class _FakeResponse:
        status_code = 200
        def raise_for_status(self) -> None: ...
        def json(self) -> dict:
            return {"response": {"docs": []}}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        return _FakeResponse()

    from app.api import citations

    with patch.object(citations.httpx, "get", fake_get):
        citations._search_literature_ads("[CII] luminosity FWHM")

    assert captured["params"].get("fq") == "database:astronomy"
