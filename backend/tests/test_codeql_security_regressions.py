"""Behavioral regressions for confirmed CodeQL backend findings."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest


@pytest.mark.timeout(2)
def test_publication_math_stripping_is_linear_for_unclosed_delimiters():
    from app.services.analysis_validator import _natural_language_words

    assert _natural_language_words(r"\(" * 50_000) == []


def test_publication_math_stripping_keeps_prose_and_removes_complete_math():
    from app.services.analysis_validator import _strip_math_segments

    text = r"The fit \(f(x) = x^2\) remains robust and $p < 0.01$ today."
    stripped = _strip_math_segments(text)

    assert stripped == "The fit   remains robust and   today."
    assert _strip_math_segments(r"unclosed \(formula") == r"unclosed \(formula"


@pytest.mark.timeout(2)
def test_scientific_notation_normalizer_is_linear_for_long_digit_runs():
    from app.services.claim_validator import (
        _normalize_sci_notation,
        _transform_for_claims,
    )

    hostile = "+" + "0" * 100_000
    assert _normalize_sci_notation(hostile) == hostile
    assert _normalize_sci_notation("3.5 × 10^8 M_sun") == "3.5e8 M_sun"

    original = "prefix 3.5 × 10^8 M_sun suffix"
    transformed, boundary_map = _transform_for_claims(original)
    normalized_start = transformed.index("3.5e8")
    normalized_end = normalized_start + len("3.5e8")
    assert original[boundary_map[normalized_start]:boundary_map[normalized_end]] == (
        "3.5 × 10^8"
    )


@pytest.mark.timeout(2)
def test_lfr_context_matcher_is_linear_for_long_whitespace_runs():
    from app.services.claim_validator import _LFR_CONTEXT_RE

    assert _LFR_CONTEXT_RE.search("L" + " " * 50_000 + "X") is None
    assert _LFR_CONTEXT_RE.search("L' [CII] relation") is not None
    assert _LFR_CONTEXT_RE.search("L' - FWHM") is not None


@pytest.mark.timeout(2)
def test_redshift_range_parser_is_linear_for_long_whitespace_runs():
    from app.search.query_parser import _RE_Z_RANGE, parse_natural_query

    assert _RE_Z_RANGE.search("z" + " " * 50_000 + "X") is None
    parsed = parse_natural_query("redshift = 2.5 to 3.5")
    assert parsed["redshift_min"] == 2.5
    assert parsed["redshift_max"] == 3.5


@pytest.mark.asyncio
async def test_spectrum_ai_failure_does_not_expose_exception_text(
    app_client, test_user, monkeypatch
):
    from app.api import data as data_api
    from app.services import spectrum_analyzer

    sentinel = "postgresql://internal-user:secret@private-host/db"

    async def _owned(_db, _user, path):
        return path

    async def _failed_ai(*_args, **_kwargs):
        raise RuntimeError(sentinel)

    summary = SimpleNamespace(
        peaks=[],
        continuum_shape="flat",
        wavelength_min=4_000.0,
        wavelength_max=5_000.0,
        n_points=2,
    )
    monkeypatch.setattr(data_api, "_require_owned_file_by_path", _owned)
    monkeypatch.setattr(
        spectrum_analyzer,
        "extract_spectrum_from_fits",
        lambda _path: {"wavelength": [4_000.0, 5_000.0], "flux": [1.0, 1.0]},
    )
    monkeypatch.setattr(spectrum_analyzer, "analyze_spectrum", lambda *_args: summary)
    monkeypatch.setattr(spectrum_analyzer, "ai_interpret", _failed_ai)

    _user, token = test_user
    response = await app_client.post(
        "/api/data/fits/analyze",
        json={"fits_path": "uploads/test/spectrum.fits", "api_key": "test-key"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["ai_error"] == "AI interpretation is temporarily unavailable."
    assert sentinel not in response.text


@pytest.mark.asyncio
async def test_detailed_health_does_not_expose_dependency_exceptions(
    app_client, test_user, monkeypatch
):
    import redis.asyncio as aioredis

    from app.api import health
    from app.models import database

    sentinel = "redis://default:secret@private-host:6379/0"

    class _BrokenSession:
        async def __aenter__(self):
            raise RuntimeError(sentinel)

        async def __aexit__(self, *_args):
            return False

    class _BrokenRedis:
        async def ping(self):
            raise RuntimeError(sentinel)

        async def aclose(self):
            return None

    def _broken_storage():
        raise RuntimeError(sentinel)

    async def _healthy_external(_url, timeout=2.0):
        return "ok", 1

    monkeypatch.setattr(database, "async_session", lambda: _BrokenSession())
    monkeypatch.setattr(aioredis, "from_url", lambda *_args, **_kwargs: _BrokenRedis())
    monkeypatch.setattr(health, "_round_trip_storage", _broken_storage)
    monkeypatch.setattr(health, "_probe_url", _healthy_external)

    _user, token = test_user
    response = await app_client.get(
        "/health/detailed",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["checks"]["database"]["status"] == "error"
    assert body["checks"]["redis"]["status"] == "error"
    assert body["checks"]["storage"]["status"] == "error"
    assert sentinel not in response.text


@pytest.mark.asyncio
async def test_external_health_probe_does_not_expose_exception_text(monkeypatch):
    import httpx

    from app.api import health

    sentinel = "https://private-service.invalid/token/secret"

    class _BrokenClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            raise RuntimeError(sentinel)

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", _BrokenClient)

    status, _duration_ms = await health._probe_url("https://example.invalid")
    assert status == "error"
    assert sentinel not in status


def test_celery_dispatch_failure_does_not_expose_exception_text():
    from app.services import async_tool_runtime as runtime

    sentinel = "redis://default:secret@private-host:6379/0"

    def _failed_dispatch(*_args, **_kwargs):
        raise RuntimeError(sentinel)

    runtime.set_dispatcher(_failed_dispatch)
    banner = runtime.submit_async_job(
        "fit_transit",
        {"target": "TOI-700"},
        dedup=False,
        user_id=str(uuid.uuid4()),
    )
    stored = runtime._JOBS_STORE.get(banner["job_id"])

    assert banner["error"] == "Background worker is temporarily unavailable."
    assert stored["error"] == banner["error"]
    assert sentinel not in str(banner)
    assert sentinel not in str(stored)
