"""Deployment-readiness checks must fail closed on missing infrastructure."""

import asyncio
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import health
from app.config import settings


COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


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

    async def no_workers(_expected_commit):
        return 0, False

    async def beat_ok(_expected_commit):
        return True, "ok"

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("RENDER_GIT_COMMIT", COMMIT_A)
    monkeypatch.setattr(settings, "pipeline_mode", "celery")
    monkeypatch.setattr(health, "_probe_database", yes)
    monkeypatch.setattr(health, "_probe_schema_head", schema_head)
    monkeypatch.setattr(health, "_probe_storage", lambda: (True, "ok"))
    monkeypatch.setattr(health, "_probe_broker", yes)
    monkeypatch.setattr(health, "_probe_celery_workers", no_workers)
    monkeypatch.setattr(health, "_probe_celery_beat", beat_ok)

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

    async def one_worker(_expected_commit):
        return 1, True

    async def beat_ok(_expected_commit):
        return True, "ok"

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("RENDER_GIT_COMMIT", COMMIT_A)
    monkeypatch.setattr(settings, "pipeline_mode", "celery")
    monkeypatch.setattr(health, "_probe_database", yes)
    monkeypatch.setattr(health, "_probe_schema_head", schema_head)
    monkeypatch.setattr(health, "_probe_storage", lambda: (True, "ok"))
    monkeypatch.setattr(health, "_probe_broker", yes)
    monkeypatch.setattr(health, "_probe_celery_workers", one_worker)
    monkeypatch.setattr(health, "_probe_celery_beat", beat_ok)

    result = await health.health_deep()

    assert result["ok"] is True
    assert result["components"]["db"] == "ok"
    assert result["components"]["schema"] == "ok"
    assert result["components"]["storage"] == "ok"
    assert result["components"]["broker"] == "ok"
    assert result["components"]["celery_worker"] == "ok"
    assert result["components"]["celery_beat"] == "ok"
    assert result["worker_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("second_commit", [COMMIT_A, COMMIT_B])
async def test_worker_probe_requires_every_live_identity_to_match_release(
    monkeypatch, second_commit
):
    class Control:
        @staticmethod
        def ping(**_kwargs):
            return [
                {f"worker-{COMMIT_A}@new-1": {"ok": "pong"}},
                {f"worker-{second_commit}@second-1": {"ok": "pong"}},
            ]

    monkeypatch.setitem(
        sys.modules,
        "celery_worker",
        SimpleNamespace(celery_app=SimpleNamespace(control=Control())),
    )

    worker_count, identity_ok = await health._probe_celery_workers(COMMIT_A)

    assert worker_count == 2
    assert identity_ok is (second_commit == COMMIT_A)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lease_commits", "expected_result"),
    [
        ([COMMIT_A.encode("ascii")], (True, "ok")),
        ([COMMIT_A.encode("ascii"), COMMIT_A], (True, "ok")),
        (
            [COMMIT_A.encode("ascii"), COMMIT_B.encode("ascii")],
            (False, "revision_mismatch"),
        ),
        ([], (False, "missing")),
        ([None], (False, "missing")),
    ],
)
async def test_beat_probe_requires_fresh_exact_release(
    monkeypatch, lease_commits, expected_result
):
    import redis.asyncio as aioredis

    closed = False
    lease_prefix = "astro:celery:beat:release:v2:"
    legacy_key = "astro:celery:beat:release"
    lease_keys = [
        f"{lease_prefix}{index:032x}".encode("ascii")
        for index in range(len(lease_commits))
    ]
    # The API probe consumes only scheduler-owned Redis leases. It does not need
    # a Celery app or a worker-side heartbeat task to be registered.
    monkeypatch.setitem(
        sys.modules,
        "celery_worker",
        SimpleNamespace(
            _BEAT_RELEASE_LEASE_PREFIX=lease_prefix,
            _BEAT_RELEASE_LEGACY_KEY=legacy_key,
        ),
    )

    class FakeRedis:
        async def get(self, key):
            assert key == legacy_key
            return None

        async def scan_iter(self, *, match, count):
            assert match == f"{lease_prefix}*"
            assert count == 100
            for key in lease_keys:
                yield key

        async def mget(self, keys):
            assert keys == lease_keys
            return lease_commits

        async def aclose(self):
            nonlocal closed
            closed = True

    def from_url(url, **kwargs):
        assert url == "rediss://redis.example/0"
        assert kwargs["ssl_cert_reqs"] == "required"
        return FakeRedis()

    monkeypatch.setattr(settings, "redis_url", "rediss://redis.example/0")
    monkeypatch.setattr(settings, "redis_tls_insecure", False)
    monkeypatch.setattr(aioredis, "from_url", from_url)

    assert await health._probe_celery_beat(COMMIT_A) == expected_result
    assert closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["scan", "read"])
async def test_beat_probe_fails_closed_when_lease_scan_or_read_fails(
    monkeypatch, failure_stage
):
    import redis.asyncio as aioredis

    closed = False
    lease_prefix = "astro:celery:beat:release:v2:"
    legacy_key = "astro:celery:beat:release"
    monkeypatch.setitem(
        sys.modules,
        "celery_worker",
        SimpleNamespace(
            _BEAT_RELEASE_LEASE_PREFIX=lease_prefix,
            _BEAT_RELEASE_LEGACY_KEY=legacy_key,
        ),
    )

    class FakeRedis:
        async def get(self, _key):
            return None

        async def scan_iter(self, **_kwargs):
            if failure_stage == "scan":
                raise RuntimeError("Redis scan failed")
            yield f"{lease_prefix}{'1' * 32}"

        async def mget(self, _keys):
            raise RuntimeError("Redis read failed")

        async def aclose(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr(aioredis, "from_url", lambda *_args, **_kwargs: FakeRedis())

    assert await health._probe_celery_beat(COMMIT_A) == (False, "unavailable")
    assert closed is True


@pytest.mark.asyncio
async def test_beat_probe_rejects_active_legacy_scalar_during_v2_rollout(
    monkeypatch,
):
    import redis.asyncio as aioredis

    closed = False
    lease_prefix = "astro:celery:beat:release:v2:"
    legacy_key = "astro:celery:beat:release"
    monkeypatch.setitem(
        sys.modules,
        "celery_worker",
        SimpleNamespace(
            _BEAT_RELEASE_LEASE_PREFIX=lease_prefix,
            _BEAT_RELEASE_LEGACY_KEY=legacy_key,
        ),
    )

    class FakeRedis:
        async def get(self, key):
            assert key == legacy_key
            return COMMIT_A.encode("ascii")

        async def scan_iter(self, **_kwargs):
            raise AssertionError("v2 leases must not hide an active legacy Beat")
            yield

        async def aclose(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr(aioredis, "from_url", lambda *_args, **_kwargs: FakeRedis())

    assert await health._probe_celery_beat(COMMIT_A) == (
        False,
        "legacy_lease_active",
    )
    assert closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("worker_identity_ok", "beat_result", "failed_component", "failed_status"),
    [
        (False, (True, "ok"), "celery_worker", "revision_mismatch"),
        (True, (False, "revision_mismatch"), "celery_beat", "revision_mismatch"),
        (True, (False, "missing"), "celery_beat", "missing"),
    ],
)
async def test_deep_health_rejects_stale_worker_or_beat_release(
    monkeypatch,
    worker_identity_ok,
    beat_result,
    failed_component,
    failed_status,
):
    async def yes():
        return True

    async def schema_head():
        return True, "ok"

    async def workers(_expected_commit):
        return 2, worker_identity_ok

    async def beat(_expected_commit):
        return beat_result

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("RENDER_GIT_COMMIT", COMMIT_A)
    monkeypatch.setattr(settings, "pipeline_mode", "celery")
    monkeypatch.setattr(health, "_probe_database", yes)
    monkeypatch.setattr(health, "_probe_schema_head", schema_head)
    monkeypatch.setattr(health, "_probe_storage", lambda: (True, "ok"))
    monkeypatch.setattr(health, "_probe_broker", yes)
    monkeypatch.setattr(health, "_probe_celery_workers", workers)
    monkeypatch.setattr(health, "_probe_celery_beat", beat)

    with pytest.raises(HTTPException) as exc_info:
        await health.health_deep()

    assert exc_info.value.status_code == 503
    assert (
        exc_info.value.detail["components"][failed_component] == failed_status
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime_commit", "expected_status"),
    [(None, "release_unknown"), ("abc123", "release_invalid")],
)
async def test_deep_health_rejects_unusable_production_release(
    monkeypatch, runtime_commit, expected_status
):
    async def yes():
        return True

    async def schema_head():
        return True, "ok"

    async def workers(_expected_commit):
        return 1, False

    async def beat(_expected_commit):
        return True, "ok"

    monkeypatch.setenv("ENV", "production")
    if runtime_commit is None:
        monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    else:
        monkeypatch.setenv("RENDER_GIT_COMMIT", runtime_commit)
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.delenv("TOOL_VERSION", raising=False)
    monkeypatch.setattr(settings, "pipeline_mode", "celery")
    monkeypatch.setattr(health, "_probe_database", yes)
    monkeypatch.setattr(health, "_probe_schema_head", schema_head)
    monkeypatch.setattr(health, "_probe_storage", lambda: (True, "ok"))
    monkeypatch.setattr(health, "_probe_broker", yes)
    monkeypatch.setattr(health, "_probe_celery_workers", workers)
    monkeypatch.setattr(health, "_probe_celery_beat", beat)

    with pytest.raises(HTTPException) as exc_info:
        await health.health_deep()

    assert exc_info.value.detail["components"]["celery_worker"] == expected_status


@pytest.mark.asyncio
async def test_deep_health_allows_unversioned_local_celery_with_fresh_beat(
    monkeypatch,
):
    async def yes():
        return True

    async def schema_head():
        return True, "ok"

    async def workers(expected_commit):
        assert expected_commit == "unknown"
        return 1, False

    async def beat(expected_commit):
        assert expected_commit == "unknown"
        return True, "ok"

    monkeypatch.setenv("ENV", "dev")
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.delenv("TOOL_VERSION", raising=False)
    monkeypatch.setattr(settings, "pipeline_mode", "celery")
    monkeypatch.setattr(health, "_probe_database", yes)
    monkeypatch.setattr(health, "_probe_schema_head", schema_head)
    monkeypatch.setattr(health, "_probe_storage", lambda: (True, "ok"))
    monkeypatch.setattr(health, "_probe_broker", yes)
    monkeypatch.setattr(health, "_probe_celery_workers", workers)
    monkeypatch.setattr(health, "_probe_celery_beat", beat)

    result = await health.health_deep()

    assert result["ok"] is True
    assert result["components"]["celery_worker"] == "unmanaged_dev"
    assert result["components"]["celery_beat"] == "ok"


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
