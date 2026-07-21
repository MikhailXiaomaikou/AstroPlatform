"""Static and runtime locks for the production deployment topology."""

from __future__ import annotations

import os
import re
import ssl
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def _yaml(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))


def _run_signed_worker_preflight(
    tmp_path: Path,
    *,
    image_commit: str,
    expected_commit: str | None,
) -> tuple[subprocess.CompletedProcess[str], str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    log_path = tmp_path / "docker.log"
    cosign = fake_bin / "cosign"
    cosign.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cosign.chmod(0o755)
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/bin/sh
printf '%s\n' "$*" >>"$FAKE_DOCKER_LOG"
if [ "${1:-}" = "image" ] && [ "${2:-}" = "inspect" ]; then
  printf 'PATH=/usr/local/bin\nTOOL_VERSION=%s\n' "$FAKE_IMAGE_COMMIT"
fi
exit 0
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "ASTRO_WORKER_IMAGE": "example.invalid/worker@sha256:" + "a" * 64,
            "ASTRO_WORKER_COMPOSE_FILE": str(
                REPO_ROOT / "deploy" / "compose.worker.yml"
            ),
            "FAKE_DOCKER_LOG": str(log_path),
            "FAKE_IMAGE_COMMIT": image_commit,
        }
    )
    if expected_commit is None:
        env.pop("GIT_COMMIT", None)
    else:
        env["GIT_COMMIT"] = expected_commit
    result = subprocess.run(
        ["sh", str(REPO_ROOT / "deploy" / "start-signed-worker.sh")],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, log_path.read_text(encoding="utf-8")


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
        REPO_ROOT / "backend" / "Dockerfile.worker",
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


def test_ci_migration_role_does_not_receive_runtime_secrets():
    workflow = _yaml(".github/workflows/ci.yml")
    migration_env = workflow["jobs"]["migration-and-recovery"]["env"]

    assert migration_env["APP_ROLE"] == "migration"
    assert migration_env["TOOL_VERSION"] == "${{ github.sha }}"
    for key in (
        "ADMIN_SECRET",
        "DELETION_TOMBSTONE_KEY",
        "EVIDENCE_SIGNING_KEY",
        "FERNET_KEY",
        "JWT_SECRET",
        "WORKER_TASK_SIGNING_PRIVATE_KEY",
    ):
        assert key not in migration_env


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
    assert predeploy.count("APP_ROLE=migration") == 2
    assert backend["healthCheckPath"] == "/health/ready"

    database = databases["standard-astro-db"]
    assert database["postgresMajorVersion"] == "16"
    assert database["ipAllowList"] == []

    worker = services["standard-astro-celery-worker"]
    assert worker["maxShutdownDelaySeconds"] == 300
    assert "--concurrency=1" in worker["dockerCommand"]
    assert "--queues=control,maintenance,verification" in worker["dockerCommand"]
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
    worker_env = {item["key"]: item for item in worker["envVars"]}
    frontend_env = {item["key"]: item for item in frontend["envVars"]}
    assert backend_env["APP_ROLE"]["value"] == "api"
    assert worker_env["APP_ROLE"]["value"] == "control_worker"
    assert beat_env["APP_ROLE"]["value"] == "beat"
    assert backend_env["SCIENCE_EXECUTION_BACKEND"]["value"] == "https_worker"
    assert worker_env["SCIENCE_EXECUTION_BACKEND"]["value"] == "https_worker"
    assert beat_env["SCIENCE_EXECUTION_BACKEND"]["value"] == "https_worker"
    assert backend_env["STANDARD_ASTRO_RELEASE"] == {
        "key": "STANDARD_ASTRO_RELEASE",
        "sync": False,
    }
    assert worker_env["STANDARD_ASTRO_RELEASE"]["fromService"] == {
        "name": "standard-astro-backend",
        "type": "web",
        "envVarKey": "STANDARD_ASTRO_RELEASE",
    }
    for key in (
        "ADMIN_SECRET",
        "JWT_SECRET",
        "FERNET_KEY",
        "DELETION_TOMBSTONE_KEY",
        "DELETION_TOMBSTONE_VERIFICATION_KEYS",
        "EVIDENCE_SIGNING_KEY",
        "EVIDENCE_SIGNING_KEY_ID",
        "WORKER_TASK_SIGNING_PRIVATE_KEY",
        "WORKER_TASK_SIGNING_KEY_ID",
        "WORKER_TASK_SIGNING_PUBLIC_KEY",
        "WORKER_TASK_VERIFICATION_KEYS",
        "EVIDENCE_V2_SIGNING_PRIVATE_KEY",
        "EVIDENCE_V2_SIGNING_KEY_ID",
        "EVIDENCE_V2_SIGNING_PUBLIC_KEY",
        "EVIDENCE_V2_VERIFICATION_KEYS",
        "SCIENTIFIC_REVIEWER_USERNAMES",
        "FOUNDRY_FORMAL_BUILD_RESULT_SECRET",
        "FOUNDRY_FORMAL_BUILD_FAILURE_RESULT_SECRET",
    ):
        assert backend_env[key] == {"key": key, "sync": False}
        assert "generateValue" not in backend_env[key]
    assert backend_env["RATE_LIMIT_ENABLED"]["value"] == "true"
    assert backend_env["CONNECTOR_CACHE_BACKEND"]["value"] == "redis"
    assert backend_env["SHARED_DEEPSEEK_API_KEY_ENABLED"]["value"] == "false"
    assert backend_env["SIGNUP_MODE"]["value"] == "invite_only"
    assert backend_env["CLAIM_AUDIT_ENABLED"]["value"] == "false"
    for key in (
        "RESEARCH_WORKSPACE_ENABLED",
        "ARXIV_READER_ENABLED",
        "UNION3_REPRODUCTION_ENABLED",
        "EVIDENCE_PACK_V2_ENABLED",
        "LOCAL_SCIENCE_WORKER_ENABLED",
    ):
        assert backend_env[key]["value"] == "false"
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

    for key in (
        "JWT_SECRET",
        "FERNET_KEY",
        "ADMIN_SECRET",
        "PRIVACY_OPERATOR_NAME",
        "PRIVACY_CONTACT",
        "PRIVACY_JURISDICTION",
        "WORKER_TASK_SIGNING_PRIVATE_KEY",
    ):
        assert key not in worker_env
    assert worker_env["EVIDENCE_V2_SIGNING_PRIVATE_KEY"]["fromService"] == {
        "name": "standard-astro-backend",
        "type": "web",
        "envVarKey": "EVIDENCE_V2_SIGNING_PRIVATE_KEY",
    }
    for key in (
        "JWT_SECRET",
        "FERNET_KEY",
        "ADMIN_SECRET",
        "DELETION_TOMBSTONE_KEY",
        "EVIDENCE_SIGNING_KEY",
        "PRIVACY_OPERATOR_NAME",
        "WORKER_TASK_SIGNING_PRIVATE_KEY",
        "EVIDENCE_V2_SIGNING_PRIVATE_KEY",
    ):
        assert key not in beat_env
    for key in (
        "DELETION_TOMBSTONE_KEY",
        "DELETION_TOMBSTONE_KEY_ID",
        "DELETION_TOMBSTONE_VERIFICATION_KEYS",
        "EVIDENCE_SIGNING_KEY",
        "EVIDENCE_SIGNING_KEY_ID",
        "EVIDENCE_VERIFICATION_KEYS",
        "WORKER_TASK_SIGNING_KEY_ID",
        "WORKER_TASK_SIGNING_PUBLIC_KEY",
        "WORKER_TASK_VERIFICATION_KEYS",
    ):
        assert worker_env[key]["fromService"]["name"] == "standard-astro-backend"

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


def test_compose_declares_role_scoped_secrets_and_one_release_identity():
    services = _yaml("docker-compose.yml")["services"]
    expected_roles = {
        "migrate": "migration",
        "backend": "api",
        "celery-worker": "control_worker",
        "celery-beat": "beat",
    }
    release_arg = (
        "${GIT_COMMIT:?GIT_COMMIT must be one full 40-character Git SHA}"
    )

    for service_name, role in expected_roles.items():
        service = services[service_name]
        assert service["environment"]["APP_ROLE"] == role
        assert service["build"]["args"]["TOOL_VERSION"] == release_arg

    migration_env = services["migrate"]["environment"]
    assert set(migration_env) == {
        "ENV",
        "APP_ROLE",
        "SCIENCE_EXECUTION_BACKEND",
        "DATABASE_URL",
    }
    worker = services["celery-worker"]
    for key in (
        "RESEARCH_WORKSPACE_ENABLED",
        "ARXIV_READER_ENABLED",
        "UNION3_REPRODUCTION_ENABLED",
        "EVIDENCE_PACK_V2_ENABLED",
        "LOCAL_SCIENCE_WORKER_ENABLED",
    ):
        assert services["backend"]["environment"][key] == "false"
    assert services["backend"]["environment"][
        "SCIENTIFIC_REVIEWER_USERNAMES"
    ] == (
        "${SCIENTIFIC_REVIEWER_USERNAMES:?SCIENTIFIC_REVIEWER_USERNAMES must "
        "list an independent reviewer}"
    )
    assert "--queues=control,maintenance,verification" in worker["command"]
    assert "worker-$${TOOL_VERSION}@%h" in worker["command"]
    for key in ("JWT_SECRET", "FERNET_KEY", "ADMIN_SECRET", "PRIVACY_OPERATOR_NAME"):
        assert key not in worker["environment"]
    assert services["backend"]["environment"]["SCIENCE_EXECUTION_BACKEND"] == (
        "https_worker"
    )
    assert services["backend"]["environment"][
        "FOUNDRY_FORMAL_BUILD_RESULT_SECRET"
    ] == "${FOUNDRY_FORMAL_BUILD_RESULT_SECRET:-}"
    assert services["backend"]["environment"][
        "FOUNDRY_FORMAL_BUILD_FAILURE_RESULT_SECRET"
    ] == "${FOUNDRY_FORMAL_BUILD_FAILURE_RESULT_SECRET:-}"
    assert worker["environment"]["SCIENCE_EXECUTION_BACKEND"] == "https_worker"
    assert worker["environment"]["WORKER_TASK_SIGNING_KEY_ID"] == (
        "${WORKER_TASK_SIGNING_KEY_ID:?WORKER_TASK_SIGNING_KEY_ID must be set "
        "explicitly}"
    )
    assert worker["environment"]["WORKER_TASK_SIGNING_PUBLIC_KEY"] == (
        "${WORKER_TASK_SIGNING_PUBLIC_KEY:?WORKER_TASK_SIGNING_PUBLIC_KEY must be "
        "set explicitly}"
    )
    assert worker["environment"]["WORKER_TASK_VERIFICATION_KEYS"] == (
        "${WORKER_TASK_VERIFICATION_KEYS:-}"
    )
    assert "WORKER_TASK_SIGNING_PRIVATE_KEY" not in worker["environment"]
    release_identity = (
        "${STANDARD_ASTRO_RELEASE:?STANDARD_ASTRO_RELEASE must identify this release}"
    )
    assert services["backend"]["environment"]["STANDARD_ASTRO_RELEASE"] == (
        release_identity
    )
    assert worker["environment"]["STANDARD_ASTRO_RELEASE"] == release_identity
    assert services["celery-beat"]["environment"][
        "SCIENCE_EXECUTION_BACKEND"
    ] == "https_worker"
    assert set(services["celery-beat"]["environment"]) == {
        "ENV",
        "APP_ROLE",
        "SCIENCE_EXECUTION_BACKEND",
        "DATABASE_URL",
        "REDIS_URL",
        "POSTGRES_BACKUP_ENABLED",
    }


def test_local_worker_requires_digest_pin_cosign_and_hardened_container():
    compose = _yaml("deploy/compose.worker.yml")
    worker = compose["services"]["science-worker"]
    assert worker["image"] == (
        "${ASTRO_WORKER_IMAGE:?set ASTRO_WORKER_IMAGE to a signed digest}"
    )
    assert worker["read_only"] is True
    assert worker["user"] == "10001:10001"
    assert worker["environment"]["ENV"] == "production"
    assert worker["environment"]["APP_ROLE"] == "science_worker"
    assert worker["environment"]["SCIENCE_EXECUTION_BACKEND"] == "https_worker"
    assert worker["cap_drop"] == ["ALL"]
    assert worker["security_opt"] == ["no-new-privileges:true"]
    assert worker["environment"]["WORKER_IMAGE_DIGEST"] == (
        "${WORKER_IMAGE_DIGEST:?set the pinned image digest}"
    )
    assert "GIT_COMMIT" not in worker["environment"]
    assert not any("/var/run/docker.sock" in mount for mount in worker["volumes"])

    preflight = (REPO_ROOT / "deploy" / "start-signed-worker.sh").read_text(
        encoding="utf-8"
    )
    assert "cosign verify" in preflight
    assert "MikhailXiaomaikou/Standard-Astro" in preflight
    assert "refs/tags/v" in preflight
    assert (
        "foundry-formal-worker.yml@refs/heads/main$" in preflight
    )
    assert "worker-image.yml@refs/heads/main" not in preflight
    assert ".github/workflows/.+@refs/heads/main" not in preflight
    assert "docker image inspect" in preflight
    assert "Worker image TOOL_VERSION" in preflight
    assert "docker compose" in preflight
    assert preflight.index("cosign verify") < preflight.index("docker compose")

    workflow = _yaml(".github/workflows/worker-image.yml")
    assert workflow["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
    }
    steps = workflow["jobs"]["build-sign"]["steps"]
    build = next(step for step in steps if step.get("id") == "build")["with"]
    assert build["platforms"] == "linux/amd64,linux/arm64"
    assert build["push"] is True
    assert build["provenance"] == "mode=max"
    assert build["sbom"] is True

    worker_cli = (REPO_ROOT / "backend" / "app" / "worker_agent" / "cli.py").read_text(
        encoding="utf-8"
    )
    pure_plot = (
        REPO_ROOT / "backend" / "app" / "services" / "union3_profile_plot.py"
    ).read_text(encoding="utf-8")
    assert "app.services.union3_profile_plot" in worker_cli
    assert "app.services.union3_research_loop" not in worker_cli
    for forbidden_import in ("app.config", "app.models", "app.storage", "sqlalchemy"):
        assert forbidden_import not in pure_plot


def test_signed_worker_rejects_host_commit_that_differs_from_baked_image(
    tmp_path,
):
    expected_commit = "a" * 40
    result, docker_log = _run_signed_worker_preflight(
        tmp_path,
        image_commit="b" * 40,
        expected_commit=expected_commit,
    )

    assert result.returncode == 2
    assert "does not match the signed Worker image TOOL_VERSION" in result.stderr
    assert "pull science-worker" in docker_log
    assert "image inspect" in docker_log
    assert "up -d science-worker" not in docker_log


def test_signed_worker_uses_baked_commit_without_host_override(tmp_path):
    result, docker_log = _run_signed_worker_preflight(
        tmp_path,
        image_commit="c" * 40,
        expected_commit=None,
    )

    assert result.returncode == 0, result.stderr
    assert "image inspect" in docker_log
    assert "up -d science-worker" in docker_log


def test_celery_delivery_semantics_are_fail_safe(monkeypatch):
    import celery_worker
    from app.config import settings
    from celery.utils.imports import symbol_by_name

    celery_app = celery_worker.celery_app

    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_default_queue == "science.short"
    guard, task_routes = celery_app.conf.task_routes
    assert isinstance(guard, celery_worker.ControlPlaneTaskRouter)
    assert task_routes["scheduler.check_due_schedules"] == {
        "queue": "control"
    }
    assert task_routes["privacy.*"] == {"queue": "maintenance"}
    assert task_routes["research_source.*"] == {"queue": "control"}
    assert task_routes["claim_audit.process"] == {
        "queue": "science.heavy"
    }
    monkeypatch.setattr(settings, "science_execution_backend", "https_worker")
    router = celery_app.amqp.Router()
    assert router.route({}, "maintenance.cleanup")["queue"].name == "maintenance"
    assert router.route({}, "union3.verify")["queue"].name == "verification"
    for task_name in (
        "ai_tools.run_long_tool",
        "claim_audit.process",
        "isochrone.prefetch_grid",
        "pipeline.execute_pipeline",
        "unknown.unrouted_task",
    ):
        with pytest.raises(celery_worker.ScienceTaskPublishRejected):
            router.route({}, task_name)
    with pytest.raises(celery_worker.ScienceTaskPublishRejected):
        router.route({"queue": "science.heavy"}, "maintenance.cleanup")

    monkeypatch.setattr(settings, "science_execution_backend", "celery")
    assert router.route({}, "pipeline.execute_pipeline")["queue"].name == (
        "science.heavy"
    )
    assert router.route({}, "unknown.unrouted_task")["queue"].name == (
        "science.short"
    )
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
    assert "reconcile-queued-union3-sources" in celery_app.conf.beat_schedule
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
