"""Static and runtime locks for the production deployment topology."""

from __future__ import annotations

import re
import ssl
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def _yaml(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))


def test_external_ci_and_container_inputs_are_immutable_and_updates_are_reviewed():
    workflow_paths = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    action_ref = re.compile(r"uses:\s*(actions/[^@\s]+)@([^\s#]+)")
    refs: list[tuple[str, str, str]] = []
    for path in workflow_paths:
        for action, revision in action_ref.findall(path.read_text(encoding="utf-8")):
            refs.append((path.name, action, revision))

    assert refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for _, _, revision in refs)

    dockerfiles = (
        REPO_ROOT / "backend" / "Dockerfile",
        REPO_ROOT / "frontend" / "Dockerfile",
    )
    image_ref = re.compile(r"^FROM\s+([^\s]+)", re.MULTILINE)
    images = [
        image
        for path in dockerfiles
        for image in image_ref.findall(path.read_text(encoding="utf-8"))
    ]
    assert images
    assert all(
        re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", image) for image in images
    )

    dependabot = _yaml(".github/dependabot.yml")
    update_locations = {
        (update["package-ecosystem"], update["directory"])
        for update in dependabot["updates"]
    }
    assert update_locations == {
        ("github-actions", "/"),
        ("npm", "/frontend"),
        ("docker", "/backend"),
        ("docker", "/frontend"),
    }


def test_ci_exercises_real_postgresql_legacy_uuid_bridge():
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "astro_uuid_drill_ci" in workflow
    assert "run_legacy_uuid_migration_drill.sh" in workflow
    assert "Exercise legacy VARCHAR UUID migration and dirty-data rollback" in workflow
    assert 'RESTORE_EXPECTED_COMMIT="$GITHUB_SHA"' in workflow
    assert "RESTORE_FERNET_KEY_ID=ci-test-key" in workflow
    assert (
        'RESTORE_EVIDENCE_SIGNING_KEY_ID="$EVIDENCE_SIGNING_KEY_ID"'
        in workflow
    )


def test_ci_builds_the_images_used_by_production_and_compose():
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    backend_dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "container-build:" in workflow
    assert "--build-arg TOOL_VERSION=\"$GITHUB_SHA\"" in workflow
    assert "-t standard-astro-backend:ci backend" in workflow
    assert "-t standard-astro-frontend:ci frontend" in workflow
    assert 'ARG TOOL_VERSION=""' in backend_dockerfile
    assert "ENV TOOL_VERSION=${TOOL_VERSION}" in backend_dockerfile


def test_daily_jobs_install_only_the_hash_locked_scientific_environment():
    workflow = (REPO_ROOT / ".github" / "workflows" / "daily.yml").read_text(
        encoding="utf-8"
    )

    assert workflow.count("cache-dependency-path: backend/requirements.lock") == 3
    assert workflow.count("install --require-hashes -r requirements.lock") == 3
    assert "pip install -r requirements.txt" not in workflow


def test_daily_blind_runner_uses_migrated_isolated_database():
    daily = _yaml(".github/workflows/daily.yml")
    blind_job = daily["jobs"]["blind-tests"]

    steps = blind_job["steps"]
    step_index = {step.get("name"): index for index, step in enumerate(steps)}
    migrate_step = steps[step_index["Initialize blind-test database"]]
    migrate_command = migrate_step["run"]

    assert (
        'export DATABASE_URL="sqlite+aiosqlite:///$RUNNER_TEMP/daily-blind.sqlite3"'
        in migrate_command
    )
    assert 'echo "DATABASE_URL=$DATABASE_URL" >> "$GITHUB_ENV"' in migrate_command
    assert "./venv/bin/alembic upgrade head" in migrate_command
    assert "./venv/bin/alembic check" in migrate_command
    assert step_index["Initialize blind-test database"] < step_index[
        "Run blind tests (cosmology)"
    ]


def test_render_workers_wait_for_exact_schema_head():
    blueprint = _yaml("render.yaml")
    services = {service["name"]: service for service in blueprint["services"]}
    databases = {database["name"]: database for database in blueprint["databases"]}

    backend = services["standard-astro-backend"]
    predeploy = backend["preDeployCommand"]
    assert "alembic upgrade head" in predeploy
    assert "alembic check" in predeploy
    assert backend["healthCheckPath"] == "/health/ready"

    database = databases["standard-astro-db"]
    assert database["postgresMajorVersion"] == "16"
    assert database["ipAllowList"] == []

    worker = services["standard-astro-celery-worker"]
    assert worker["maxShutdownDelaySeconds"] == 300
    assert "--concurrency=1" in worker["dockerCommand"]
    assert "--hostname=worker-${RENDER_GIT_COMMIT}@%h" in worker["dockerCommand"]
    for name in ("standard-astro-celery-worker", "standard-astro-celery-beat"):
        command = services[name]["dockerCommand"]
        assert "scripts/wait_for_schema_head.py" in command
        assert "&& exec celery" in command
    beat_env = {
        item["key"]: item for item in services["standard-astro-celery-beat"]["envVars"]
    }
    assert beat_env["ENABLE_TRANSIENT_ALERT_INGEST"]["value"] == "false"

    frontend = services["standard-astro-frontend"]
    backend_env = {item["key"]: item for item in backend["envVars"]}
    frontend_env = {item["key"]: item for item in frontend["envVars"]}
    for key in (
        "ADMIN_SECRET",
        "JWT_SECRET",
        "FERNET_KEY",
        "DELETION_TOMBSTONE_KEY",
        "DELETION_TOMBSTONE_VERIFICATION_KEYS",
        "EVIDENCE_SIGNING_KEY",
        "EVIDENCE_SIGNING_KEY_ID",
    ):
        assert backend_env[key] == {"key": key, "sync": False}
        assert "generateValue" not in backend_env[key]
    assert backend_env["RATE_LIMIT_ENABLED"]["value"] == "true"
    assert backend_env["CONNECTOR_CACHE_BACKEND"]["value"] == "redis"
    assert backend_env["SHARED_DEEPSEEK_API_KEY_ENABLED"]["value"] == "false"
    assert backend_env["SIGNUP_MODE"]["value"] == "invite_only"
    assert backend_env["CLAIM_AUDIT_ENABLED"]["value"] == "false"
    assert backend_env["PRODUCT_ANALYTICS_RETENTION_DAYS"]["value"] == "30"
    assert backend_env["DELETION_TOMBSTONE_KEY_ID"]["value"] == "deletion-prod-v1"
    assert backend_env["TRUSTED_PROXY_MODE"]["value"] == "none"
    assert backend_env["ASTRO_RESEARCH_FOCUS"]["value"] == "cosmology"
    assert backend_env["CORS_ORIGINS"]["fromService"] == {
        "name": "standard-astro-frontend",
        "type": "web",
        "envVarKey": "RENDER_EXTERNAL_URL",
    }
    assert frontend_env["VITE_API_URL"]["fromService"] == {
        "name": "standard-astro-backend",
        "type": "web",
        "envVarKey": "RENDER_EXTERNAL_URL",
    }
    assert frontend_env["NODE_VERSION"]["value"] == "20.19.0"

    # Render injects RENDER_GIT_COMMIT on every deploy. Do not mirror it through
    # a Blueprint self-reference: fromService values refresh on Blueprint sync,
    # not on every commit deploy, and would override the runtime SHA with a stale
    # TOOL_VERSION (the receipt resolver already reads RENDER_GIT_COMMIT).
    assert "TOOL_VERSION" not in backend_env
    for name in ("standard-astro-celery-worker", "standard-astro-celery-beat"):
        service_env = {item["key"]: item for item in services[name]["envVars"]}
        assert "TOOL_VERSION" not in service_env
        assert service_env["CONNECTOR_CACHE_BACKEND"]["value"] == "redis"
        for key in (
            "DELETION_TOMBSTONE_KEY",
            "DELETION_TOMBSTONE_KEY_ID",
            "DELETION_TOMBSTONE_VERIFICATION_KEYS",
            "PRIVACY_OPERATOR_NAME",
            "PRIVACY_CONTACT",
            "PRIVACY_JURISDICTION",
        ):
            assert service_env[key]["fromService"]["name"] == "standard-astro-backend"

    cache_headers = {
        item["path"]: item["value"]
        for item in frontend["headers"]
        if item["name"].lower() == "cache-control"
    }
    assert cache_headers == {
        "/assets/*": "public, max-age=31536000, immutable",
        "/*": "no-cache",
    }


def test_production_frontend_has_no_legacy_render_fallback():
    client = (REPO_ROOT / "frontend" / "src" / "api" / "client.ts").read_text(
        encoding="utf-8"
    )
    vite_config = (REPO_ROOT / "frontend" / "vite.config.ts").read_text(
        encoding="utf-8"
    )

    assert "astro-backend-h4x1.onrender.com" not in client
    assert "VITE_API_URL is required for production builds" in client
    assert "VITE_API_URL is required for a production frontend build" in vite_config


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
    from celery.utils.imports import symbol_by_name

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
    assert "release-heartbeat" not in celery_app.conf.beat_schedule
    assert "system.release_heartbeat" not in celery_app.tasks
    assert celery_app.conf.beat_scheduler == (
        "celery_worker:ReleaseLeaseScheduler"
    )
    assert symbol_by_name(celery_app.conf.beat_scheduler) is (
        celery_worker.ReleaseLeaseScheduler
    )
    assert (
        celery_app.conf.beat_max_loop_interval
        == celery_worker._BEAT_RELEASE_HEARTBEAT_SECONDS
    )
    assert "ingest-ztf-alerts" not in celery_app.conf.beat_schedule


def test_transient_alert_ingest_requires_explicit_opt_in(monkeypatch):
    import celery_worker

    monkeypatch.delenv("ENABLE_TRANSIENT_ALERT_INGEST", raising=False)
    assert "ingest-ztf-alerts" not in celery_worker._build_beat_schedule()

    monkeypatch.setenv("ENABLE_TRANSIENT_ALERT_INGEST", "true")
    schedule = celery_worker._build_beat_schedule()
    assert schedule["ingest-ztf-alerts"] == {
        "task": "alerts.ingest",
        "schedule": 900.0,
    }


def test_beat_release_lease_is_instance_scoped_and_uses_verified_tls(
    monkeypatch,
):
    import redis

    import celery_worker
    from app.config import settings

    commit = "a" * 40
    owner = "1" * 32
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")
    assert celery_worker._runtime_commit() == "unknown"
    with pytest.raises(RuntimeError, match="full Git SHA"):
        celery_worker._write_beat_release_lease(owner)
    with pytest.raises(ValueError, match="owner"):
        celery_worker._write_beat_release_lease("unsafe/owner")

    monkeypatch.setenv("RENDER_GIT_COMMIT", commit)

    writes: list[tuple[str, str, int]] = []

    class FakeRedis:
        def set(self, key, value, *, ex):
            writes.append((key, value, ex))
            return True

        def close(self):
            return None

    def from_url(url, **kwargs):
        assert url == "rediss://redis.example/0"
        assert kwargs["ssl_cert_reqs"] == "required"
        return FakeRedis()

    monkeypatch.setattr(settings, "redis_url", "rediss://redis.example/0")
    monkeypatch.setattr(settings, "redis_tls_insecure", False)
    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(from_url))

    assert celery_worker._write_beat_release_lease(owner) == commit
    assert writes == [
        (
            f"{celery_worker._BEAT_RELEASE_LEASE_PREFIX}{owner}",
            commit,
            celery_worker._BEAT_RELEASE_HEARTBEAT_TTL_SECONDS,
        )
    ]


def test_release_lease_scheduler_renews_only_after_successful_throttled_ticks(
    monkeypatch,
):
    import celery_worker

    scheduler = object.__new__(celery_worker.ReleaseLeaseScheduler)
    scheduler._release_lease_owner = "2" * 32
    scheduler._last_release_lease_attempt = None
    clock = iter((0.0, 10.0, 31.0))
    writes: list[str] = []

    monkeypatch.setattr(
        celery_worker.PersistentScheduler,
        "tick",
        lambda _self, *_args, **_kwargs: 90.0,
    )
    monkeypatch.setattr(celery_worker.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        celery_worker,
        "_write_beat_release_lease",
        lambda owner: writes.append(owner),
    )

    assert scheduler.tick() == celery_worker._BEAT_RELEASE_HEARTBEAT_SECONDS
    assert scheduler.tick() == celery_worker._BEAT_RELEASE_HEARTBEAT_SECONDS
    assert scheduler.tick() == celery_worker._BEAT_RELEASE_HEARTBEAT_SECONDS
    assert writes == ["2" * 32, "2" * 32]


def test_release_lease_scheduler_does_not_renew_when_tick_raises(
    monkeypatch,
):
    import celery_worker

    scheduler = object.__new__(celery_worker.ReleaseLeaseScheduler)
    scheduler._release_lease_owner = "3" * 32
    scheduler._last_release_lease_attempt = None
    writes: list[str] = []

    def failed_tick(_self, *_args, **_kwargs):
        raise RuntimeError("scheduler main loop failed")

    monkeypatch.setattr(celery_worker.PersistentScheduler, "tick", failed_tick)
    monkeypatch.setattr(
        celery_worker,
        "_write_beat_release_lease",
        lambda owner: writes.append(owner),
    )

    with pytest.raises(RuntimeError, match="main loop failed"):
        scheduler.tick()
    assert writes == []


def test_release_lease_scheduler_does_not_renew_while_tick_is_stalled(
    monkeypatch,
):
    import threading

    import celery_worker

    scheduler = object.__new__(celery_worker.ReleaseLeaseScheduler)
    scheduler._release_lease_owner = "5" * 32
    scheduler._last_release_lease_attempt = None
    tick_started = threading.Event()
    release_tick = threading.Event()
    writes: list[str] = []

    def stalled_tick(_self, *_args, **_kwargs):
        tick_started.set()
        assert release_tick.wait(timeout=1.0)
        return 30.0

    monkeypatch.setattr(celery_worker.PersistentScheduler, "tick", stalled_tick)
    monkeypatch.setattr(
        celery_worker,
        "_write_beat_release_lease",
        lambda owner: writes.append(owner),
    )

    tick_thread = threading.Thread(target=scheduler.tick)
    tick_thread.start()
    assert tick_started.wait(timeout=1.0)
    assert writes == []

    release_tick.set()
    tick_thread.join(timeout=1.0)
    assert not tick_thread.is_alive()
    assert writes == ["5" * 32]


def test_release_lease_scheduler_retries_failed_write_on_later_tick(monkeypatch):
    import celery_worker

    scheduler = object.__new__(celery_worker.ReleaseLeaseScheduler)
    scheduler._release_lease_owner = "4" * 32
    scheduler._last_release_lease_attempt = None
    attempts: list[str] = []
    clock = iter((0.0, 31.0))

    def write(owner):
        attempts.append(owner)
        if len(attempts) == 1:
            raise RuntimeError("temporary Redis outage")

    monkeypatch.setattr(
        celery_worker.PersistentScheduler,
        "tick",
        lambda _self, *_args, **_kwargs: 30.0,
    )
    monkeypatch.setattr(celery_worker.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(celery_worker, "_write_beat_release_lease", write)
    logged: list[str] = []
    monkeypatch.setattr(
        celery_worker.logger,
        "exception",
        lambda message: logged.append(message),
    )

    scheduler.tick()
    scheduler.tick()

    assert attempts == ["4" * 32, "4" * 32]
    assert logged == ["Celery Beat release lease renewal failed"]


def test_release_lease_scheduler_assigns_safe_unique_owner(monkeypatch):
    import celery_worker

    monkeypatch.setattr(
        celery_worker.PersistentScheduler,
        "__init__",
        lambda _self, *_args, **_kwargs: None,
    )

    first = celery_worker.ReleaseLeaseScheduler()
    second = celery_worker.ReleaseLeaseScheduler()

    assert celery_worker._BEAT_OWNER.fullmatch(first._release_lease_owner)
    assert celery_worker._BEAT_OWNER.fullmatch(second._release_lease_owner)
    assert first._release_lease_owner != second._release_lease_owner


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
