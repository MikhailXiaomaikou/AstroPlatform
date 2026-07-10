"""Static and runtime locks for the production deployment topology."""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def _yaml(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))


def test_render_workers_wait_for_exact_schema_head():
    blueprint = _yaml("render.yaml")
    services = {service["name"]: service for service in blueprint["services"]}

    predeploy = services["standard-astro-backend"]["preDeployCommand"]
    assert "alembic upgrade head" in predeploy
    assert "alembic check" in predeploy
    for name in ("standard-astro-celery-worker", "standard-astro-celery-beat"):
        command = services[name]["dockerCommand"]
        assert "scripts/wait_for_schema_head.py" in command
        assert "&& exec celery" in command


def test_compose_frontend_is_built_for_local_backend_not_render():
    compose = _yaml("docker-compose.yml")
    build = compose["services"]["frontend"]["build"]
    assert build["args"]["VITE_API_URL"] == "http://localhost:8000"
    assert "onrender.com" not in build["args"]["VITE_API_URL"]

    dockerfile = (REPO_ROOT / "frontend" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "ARG VITE_API_URL=http://localhost:8000" in dockerfile
    assert compose["services"]["backend"]["environment"][
        "TRUSTED_PROXY_MODE"
    ] == "none"


def test_celery_delivery_semantics_are_fail_safe():
    import celery_worker

    celery_app = celery_worker.celery_app

    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_soft_time_limit > 0
    assert celery_app.conf.task_time_limit > celery_app.conf.task_soft_time_limit
    assert (
        celery_app.conf.broker_transport_options["visibility_timeout"]
        > celery_app.conf.task_time_limit
    )
    assert (
        celery_worker._STALE_JOB_SECONDS
        > celery_app.conf.broker_transport_options["visibility_timeout"]
    )
    assert "reconcile-stale-research-jobs" in celery_app.conf.beat_schedule


def test_rediss_verifies_certificates_unless_insecure_opt_in(monkeypatch):
    import celery_worker
    from app.config import settings

    monkeypatch.setattr(settings, "redis_url", "rediss://redis.example/0")
    monkeypatch.setattr(settings, "redis_tls_insecure", False)
    secure = celery_worker._redis_ssl_options()
    assert secure["broker_use_ssl"]["ssl_cert_reqs"] == ssl.CERT_REQUIRED
    assert secure["redis_backend_use_ssl"]["ssl_cert_reqs"] == ssl.CERT_REQUIRED

    monkeypatch.setattr(settings, "redis_tls_insecure", True)
    insecure = celery_worker._redis_ssl_options()
    assert insecure["broker_use_ssl"]["ssl_cert_reqs"] == ssl.CERT_NONE


@pytest.mark.asyncio
async def test_schema_waiter_retries_then_passes():
    from scripts.wait_for_schema_head import wait_for_schema_head

    replies = iter(((False, "revision_mismatch"), (True, "ok")))
    sleeps: list[float] = []

    async def probe():
        return next(replies)

    async def sleep(delay: float):
        sleeps.append(delay)

    clock_values = iter((0.0, 0.0, 1.0))
    result = await wait_for_schema_head(
        timeout_seconds=10,
        interval_seconds=1,
        probe=probe,
        sleep=sleep,
        clock=lambda: next(clock_values),
    )

    assert result == (True, "ok")
    assert sleeps == [1]


@pytest.mark.asyncio
async def test_schema_waiter_times_out_fail_closed():
    from scripts.wait_for_schema_head import wait_for_schema_head

    async def probe():
        return False, "unversioned_or_unreachable"

    async def sleep(_delay: float):
        return None

    clock_values = iter((0.0, 0.0, 2.0))
    result = await wait_for_schema_head(
        timeout_seconds=1,
        interval_seconds=1,
        probe=probe,
        sleep=sleep,
        clock=lambda: next(clock_values),
    )

    assert result == (False, "unversioned_or_unreachable")
