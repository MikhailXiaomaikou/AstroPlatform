"""Deployment-readiness checks must fail closed on missing infrastructure."""

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import health
from app.config import settings


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
