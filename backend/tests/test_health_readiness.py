"""Deployment-readiness checks must fail closed on missing infrastructure."""

import asyncio
from pathlib import Path
import time

import pytest
from fastapi import HTTPException

from app.api import health
from app.config import settings


@pytest.mark.asyncio
async def test_fast_readiness_runs_database_and_schema_concurrently(
    monkeypatch,
):
    both_started = asyncio.Event()
    started: set[str] = set()

    async def mark_started(name: str):
        started.add(name)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.5)

    async def database_ok():
        await mark_started("db")
        return True

    async def schema_ok():
        await mark_started("schema")
        return True, "ok"

    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")
    monkeypatch.setenv("RENDER_GIT_BRANCH", "main")
    monkeypatch.setenv("RENDER_SERVICE_NAME", "astro-api")
    monkeypatch.setattr(health, "_probe_database", database_ok)
    monkeypatch.setattr(health, "_probe_schema_head", schema_ok)

    result = await health.health_ready()

    assert result == {
        "status": "ready",
        "components": {"db": "ok", "schema": "ok"},
        "version": {
            "commit": "abc123",
            "branch": "main",
            "service": "astro-api",
        },
    }


@pytest.mark.asyncio
async def test_fast_readiness_fails_closed_on_probe_error(monkeypatch):
    async def database_error():
        raise RuntimeError("secret connection details")

    async def schema_error():
        return False, "secret schema diagnostics"

    monkeypatch.setattr(health, "_probe_database", database_error)
    monkeypatch.setattr(health, "_probe_schema_head", schema_error)

    with pytest.raises(HTTPException) as exc_info:
        await health.health_ready()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["status"] == "not_ready"
    assert exc_info.value.detail["components"] == {"db": "fail", "schema": "fail"}
    assert "secret" not in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_fast_readiness_enforces_deadline(monkeypatch):
    cancelled = asyncio.Event()

    async def slow_probe():
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    monkeypatch.setattr(health, "_READINESS_DEADLINE_SECONDS", 0.02)
    monkeypatch.setattr(health, "_probe_database", slow_probe)
    monkeypatch.setattr(health, "_probe_schema_head", slow_probe)

    started_at = time.monotonic()
    with pytest.raises(HTTPException) as exc_info:
        await health.health_ready()
    elapsed = time.monotonic() - started_at
    await asyncio.wait_for(cancelled.wait(), timeout=0.5)

    assert elapsed < 0.5
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["components"] == {
        "db": "timeout",
        "schema": "timeout",
    }


@pytest.mark.asyncio
async def test_fast_readiness_cancels_child_probes_when_request_is_cancelled(
    monkeypatch,
):
    both_started = asyncio.Event()
    both_cancelled = asyncio.Event()
    started = 0
    cancelled = 0

    async def cancellable_probe():
        nonlocal started, cancelled
        started += 1
        if started == 2:
            both_started.set()
        try:
            await asyncio.sleep(60)
        finally:
            cancelled += 1
            if cancelled == 2:
                both_cancelled.set()

    monkeypatch.setattr(health, "_probe_database", cancellable_probe)
    monkeypatch.setattr(health, "_probe_schema_head", cancellable_probe)

    request = asyncio.create_task(health.health_ready())
    await asyncio.wait_for(both_started.wait(), timeout=0.5)
    request.cancel()

    with pytest.raises(asyncio.CancelledError):
        await request
    await asyncio.wait_for(both_cancelled.wait(), timeout=0.5)


def test_migration_history_has_one_declared_head():
    heads = health._expected_alembic_heads()
    assert len(heads) == 1
    assert next(iter(heads))


def test_storage_probe_requires_configured_mount_in_production(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "fits"))
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("PERSISTENT_STORAGE_MOUNT", raising=False)

    assert health._probe_storage() == (False, "persistent_mount_not_configured")


def test_storage_probe_fsyncs_configured_mount(monkeypatch, tmp_path):
    mount = tmp_path / "data"
    mount.mkdir()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(mount / "fits"))
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("PERSISTENT_STORAGE_MOUNT", str(mount))
    monkeypatch.setattr(Path, "is_mount", lambda _path: True)

    assert health._probe_storage() == (True, "ok")
    assert not list((mount / "fits").glob(".health_probe_*"))


def test_storage_probe_round_trips_s3_without_local_mount(monkeypatch):
    from app import storage

    monkeypatch.setattr(settings, "storage_backend", "s3")
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("PERSISTENT_STORAGE_MOUNT", raising=False)
    monkeypatch.setattr(
        storage,
        "storage_healthcheck",
        lambda: {"ok": True, "backend": "s3"},
    )

    assert health._probe_storage() == (True, "ok")


@pytest.mark.asyncio
async def test_deep_health_requires_live_worker_in_celery_mode(monkeypatch):
    async def yes():
        return True

    async def schema_head():
        return True, "ok"

    async def no_workers():
        return 0

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setattr(settings, "pipeline_mode", "celery")
    monkeypatch.setattr(health, "_probe_database", yes)
    monkeypatch.setattr(health, "_probe_schema_head", schema_head)
    monkeypatch.setattr(health, "_probe_storage", lambda: (True, "ok"))
    monkeypatch.setattr(health, "_probe_broker", yes)
    monkeypatch.setattr(health, "_probe_celery_workers", no_workers)

    with pytest.raises(HTTPException) as exc_info:
        await health.health_deep()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["components"]["celery_worker"] == "none"


@pytest.mark.asyncio
async def test_deep_health_passes_only_with_schema_broker_worker_and_storage(
    monkeypatch,
):
    async def yes():
        return True

    async def schema_head():
        return True, "ok"

    async def one_worker():
        return 1

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setattr(settings, "pipeline_mode", "celery")
    monkeypatch.setattr(health, "_probe_database", yes)
    monkeypatch.setattr(health, "_probe_schema_head", schema_head)
    monkeypatch.setattr(health, "_probe_storage", lambda: (True, "ok"))
    monkeypatch.setattr(health, "_probe_broker", yes)
    monkeypatch.setattr(health, "_probe_celery_workers", one_worker)

    result = await health.health_deep()

    assert result["ok"] is True
    assert result["components"]["db"] == "ok"
    assert result["components"]["schema"] == "ok"
    assert result["components"]["storage"] == "ok"
    assert result["components"]["broker"] == "ok"
    assert result["components"]["celery_worker"] == "ok"
    assert result["worker_count"] == 1


@pytest.mark.asyncio
async def test_deep_health_blocks_schema_drift_in_production(monkeypatch):
    async def yes():
        return True

    async def stale_schema():
        return False, "revision_mismatch"

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setattr(settings, "pipeline_mode", "sync")
    monkeypatch.setattr(health, "_probe_database", yes)
    monkeypatch.setattr(health, "_probe_schema_head", stale_schema)
    monkeypatch.setattr(health, "_probe_storage", lambda: (True, "ok"))

    with pytest.raises(HTTPException) as exc_info:
        await health.health_deep()

    assert exc_info.value.detail["components"]["schema"] == "revision_mismatch"
