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
    for name in ("standard-astro-celery-worker", "standard-astro-celery-beat"):
        command = services[name]["dockerCommand"]
        assert "scripts/wait_for_schema_head.py" in command
        assert "&& exec celery" in command

    frontend = services["standard-astro-frontend"]
    backend_env = {item["key"]: item for item in backend["envVars"]}
    frontend_env = {item["key"]: item for item in frontend["envVars"]}
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
