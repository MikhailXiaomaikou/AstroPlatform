"""Regression coverage for the post-deploy acceptance script."""

from __future__ import annotations

import httpx
import pytest

from scripts import verify_deployment


BASE_URL = "https://astro.example"
LOCAL_BASE_URL = "http://127.0.0.1:8000"
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


def _responses(
    *,
    base_url: str = BASE_URL,
    ready_status: int = 200,
    ready_commit: str = COMMIT_A,
    deep_status: int = 200,
    deep_commit: str = COMMIT_A,
) -> dict[str, httpx.Response]:
    return {
        f"{base_url}/health": httpx.Response(
            200, json={"status": "ok", "version": "0.4.0"}
        ),
        f"{base_url}/openapi.json": httpx.Response(200, json={"openapi": "3.1.0"}),
        f"{base_url}/docs": httpx.Response(200, text="docs"),
        f"{base_url}/redoc": httpx.Response(200, text="redoc"),
        f"{base_url}/health/ready": httpx.Response(
            ready_status,
            json={
                "status": "ready",
                "components": {"db": "ok", "schema": "ok"},
                "version": {"commit": ready_commit},
            },
        ),
        f"{base_url}/health/deep": httpx.Response(
            deep_status,
            json={
                "ok": True,
                "components": {
                    "db": "ok",
                    "schema": "ok",
                    "storage": "ok",
                    "broker": "ok",
                    "celery_worker": "ok",
                },
                "version": {"commit": deep_commit},
            },
        ),
    }


def _install_http_responses(monkeypatch, responses):
    calls: list[str] = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return responses[url]

    monkeypatch.setattr(verify_deployment.httpx, "get", fake_get)
    monkeypatch.delenv("ADMIN_SECRET", raising=False)
    monkeypatch.delenv("EXPECTED_COMMIT", raising=False)
    return calls


def test_verify_requires_ready_and_deep_once_with_same_expected_commit(monkeypatch):
    calls = _install_http_responses(monkeypatch, _responses())

    assert verify_deployment.verify(BASE_URL, expected_commit=COMMIT_A) is True
    assert calls.count(f"{BASE_URL}/health/ready") == 1
    assert calls.count(f"{BASE_URL}/health/deep") == 1


def test_verify_fails_when_ready_endpoint_is_missing(monkeypatch):
    responses = _responses(ready_status=404)
    calls = _install_http_responses(monkeypatch, responses)

    assert verify_deployment.verify(BASE_URL, expected_commit=COMMIT_A) is False
    assert f"{BASE_URL}/health/ready" in calls


@pytest.mark.parametrize(
    ("ready_commit", "deep_commit", "expected_commit"),
    [
        (COMMIT_A, COMMIT_B, COMMIT_A),
        ("unknown", "unknown", COMMIT_A),
        (COMMIT_A, COMMIT_A, COMMIT_B),
    ],
)
def test_verify_fails_closed_on_unreconciled_release_identity(
    monkeypatch, ready_commit, deep_commit, expected_commit
):
    responses = _responses(
        ready_commit=ready_commit,
        deep_commit=deep_commit,
    )
    _install_http_responses(monkeypatch, responses)

    assert (
        verify_deployment.verify(BASE_URL, expected_commit=expected_commit) is False
    )


def test_verify_reads_expected_commit_from_environment(monkeypatch):
    responses = _responses()
    _install_http_responses(monkeypatch, responses)
    monkeypatch.setenv("EXPECTED_COMMIT", COMMIT_B)

    assert verify_deployment.verify(BASE_URL) is False


@pytest.mark.parametrize("expected_commit", [None, "", "abc123", "g" * 40])
def test_verify_rejects_remote_url_without_full_expected_commit(
    monkeypatch, expected_commit
):
    calls = _install_http_responses(monkeypatch, _responses())

    assert (
        verify_deployment.verify(BASE_URL, expected_commit=expected_commit) is False
    )
    assert calls == []


def test_verify_allows_loopback_url_without_expected_commit(monkeypatch):
    responses = _responses(base_url=LOCAL_BASE_URL)
    calls = _install_http_responses(monkeypatch, responses)

    assert verify_deployment.verify(LOCAL_BASE_URL) is True
    assert calls.count(f"{LOCAL_BASE_URL}/health/ready") == 1
    assert calls.count(f"{LOCAL_BASE_URL}/health/deep") == 1
