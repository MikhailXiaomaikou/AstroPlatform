import logging
import os
import re
import ssl
import time
import uuid
from fnmatch import fnmatchcase

from celery import Celery
from celery.beat import PersistentScheduler
from celery.schedules import crontab
from celery.signals import worker_ready

from app.config import settings
# Worker and Beat must fail boot on the same invalid Registry release as API.
from app.services import workflow_registry_v2 as _workflow_registry_v2  # noqa: F401
from app.services.foundry_registry_activation import (
    assert_configured_registry_activation_bundle,
)

# A configured formal release must be the public bundle baked into this exact
# image.  This executes before the worker can subscribe to verification work.
assert_configured_registry_activation_bundle()

logger = logging.getLogger(__name__)

_BEAT_RELEASE_LEASE_PREFIX = "astro:celery:beat:release:v2:"
_BEAT_RELEASE_LEGACY_KEY = "astro:celery:beat:release"
_BEAT_RELEASE_HEARTBEAT_SECONDS = 30.0
_BEAT_RELEASE_HEARTBEAT_TTL_SECONDS = 120
_GIT_COMMIT = re.compile(r"[0-9a-f]{40}", re.IGNORECASE)
_BEAT_OWNER = re.compile(r"[0-9a-f]{32}")

# Hosted workers are control-plane processes. Any task without an explicit
# control/maintenance route lands on a science queue that Render never
# consumes; a future no-secrets HTTPS worker owns those computations.
_CONTROL_TASK_ROUTES = {
    "scheduler.check_due_schedules": {"queue": "control"},
    "alerts.ingest": {"queue": "control"},
    "ai_tools.reconcile_stale_jobs": {"queue": "maintenance"},
    "claim_audit.reconcile_stale": {"queue": "maintenance"},
    "research_source.*": {"queue": "control"},
    "union3.*": {"queue": "verification"},
    "privacy.*": {"queue": "maintenance"},
    "maintenance.*": {"queue": "maintenance"},
}
_SCIENCE_TASK_ROUTES = {
    "ai_tools.run_long_tool": {"queue": "science.short"},
    "claim_audit.process": {"queue": "science.heavy"},
    "isochrone.prefetch_grid": {"queue": "science.heavy"},
    "pipeline.*": {"queue": "science.heavy"},
}


class ScienceTaskPublishRejected(RuntimeError):
    """Raised before Redis publish when hosted science Celery is disabled."""


class ControlPlaneTaskRouter:
    """Fail closed on science or unknown tasks in HTTPS Worker topology.

    Render and the production Compose stack intentionally run only the
    control-plane Celery queues.  This router is first in ``task_routes`` so a
    forgotten producer cannot silently fall through to the ``science.short``
    default queue and remain there forever.
    """

    _ALLOWED_QUEUES = frozenset({"control", "maintenance", "verification"})

    def __call__(self, name, args, kwargs, options, task=None):
        del args, kwargs, task
        if settings.science_execution_backend == "celery":
            return None
        requested_queue = options.get("queue")
        requested_queue_name = getattr(requested_queue, "name", requested_queue)
        if (
            requested_queue_name is not None
            and str(requested_queue_name) not in self._ALLOWED_QUEUES
        ):
            raise ScienceTaskPublishRejected(
                f"Celery queue {requested_queue_name!r} is disabled when "
                "SCIENCE_EXECUTION_BACKEND is not 'celery'"
            )
        if any(fnmatchcase(str(name), pattern) for pattern in _CONTROL_TASK_ROUTES):
            return None
        raise ScienceTaskPublishRejected(
            f"Celery science task {name!r} is disabled when "
            "SCIENCE_EXECUTION_BACKEND is not 'celery'"
        )


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def _boolean_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _strict_boolean_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _runtime_commit() -> str:
    """Return a bounded release identifier safe to put in task arguments."""
    raw = (
        os.getenv("RENDER_GIT_COMMIT")
        or os.getenv("GIT_COMMIT")
        or os.getenv("TOOL_VERSION")
        or "unknown"
    )
    commit = str(raw).strip().lower()
    return commit if _GIT_COMMIT.fullmatch(commit) else "unknown"


_TASK_SOFT_TIME_LIMIT = _positive_int_env(
    "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS", 12 * 60 * 60
)
_TASK_TIME_LIMIT = _positive_int_env(
    "CELERY_TASK_TIME_LIMIT_SECONDS", _TASK_SOFT_TIME_LIMIT + 30 * 60
)
if _TASK_TIME_LIMIT <= _TASK_SOFT_TIME_LIMIT:
    raise ValueError(
        "CELERY_TASK_TIME_LIMIT_SECONDS must exceed "
        "CELERY_TASK_SOFT_TIME_LIMIT_SECONDS"
    )
_VISIBILITY_TIMEOUT = _positive_int_env(
    "CELERY_VISIBILITY_TIMEOUT_SECONDS", _TASK_TIME_LIMIT + 30 * 60
)
if _VISIBILITY_TIMEOUT <= _TASK_TIME_LIMIT:
    raise ValueError(
        "CELERY_VISIBILITY_TIMEOUT_SECONDS must exceed "
        "CELERY_TASK_TIME_LIMIT_SECONDS"
    )
_STALE_JOB_SECONDS = _positive_int_env(
    "RESEARCH_JOB_STALE_SECONDS", 14 * 60 * 60
)
if _STALE_JOB_SECONDS <= _VISIBILITY_TIMEOUT:
    raise ValueError(
        "RESEARCH_JOB_STALE_SECONDS must exceed "
        "CELERY_VISIBILITY_TIMEOUT_SECONDS"
    )

celery_app = Celery(
    "astro_pipeline",
    broker=settings.celery_broker_url,
    backend=settings.celery_broker_url,
)


def _redis_ssl_options() -> dict:
    if not settings.redis_ssl:
        return {}
    cert_requirement = (
        ssl.CERT_NONE if settings.redis_tls_insecure else ssl.CERT_REQUIRED
    )
    return {
        "broker_use_ssl": {"ssl_cert_reqs": cert_requirement},
        "redis_backend_use_ssl": {"ssl_cert_reqs": cert_requirement},
    }


_broker_opts = _redis_ssl_options()

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_default_queue="science.short",
    task_routes=(
        ControlPlaneTaskRouter(),
        {**_CONTROL_TASK_ROUTES, **_SCIENCE_TASK_ROUTES},
    ),
    # Long scientific tasks must be returned to Redis when a worker process is
    # lost.  Prefetch=1 prevents one worker from reserving a backlog it cannot
    # finish, and the visibility timeout exceeds the hard task limit so a live
    # task is not delivered twice merely because it is slow.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=_TASK_SOFT_TIME_LIMIT,
    task_time_limit=_TASK_TIME_LIMIT,
    broker_transport_options={"visibility_timeout": _VISIBILITY_TIMEOUT},
    result_backend_transport_options={"visibility_timeout": _VISIBILITY_TIMEOUT},
    visibility_timeout=_VISIBILITY_TIMEOUT,
    # The custom scheduler renews a per-instance release lease only after a
    # successful scheduler tick. Bound the sleep so a healthy idle Beat ticks
    # often enough to renew the lease.
    beat_scheduler="celery_worker:ReleaseLeaseScheduler",
    beat_max_loop_interval=_BEAT_RELEASE_HEARTBEAT_SECONDS,
    **_broker_opts,
)

# Auto-discover pipeline tasks (tasks.py in app.pipeline)
# and explicitly include engine.py which also defines Celery tasks
celery_app.autodiscover_tasks(["app.pipeline"])
celery_app.conf.include = [
    "app.pipeline.engine",
    "app.services.isochrone_tasks",
    "app.services.alert_tasks",
    "app.tasks.ai_tools_tasks",
    "app.tasks.claim_audit_tasks",
    "app.tasks.privacy_tasks",
    "app.tasks.postgres_backup_tasks",
    "app.tasks.union3_source_tasks",
    "app.tasks.union3_research_tasks",
]


def _build_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the production schedule with non-cosmology ingestion opt-in."""

    schedule: dict[str, dict[str, object]] = {
        "check-scheduled-pipelines": {
            "task": "scheduler.check_due_schedules",
            "schedule": 60.0,  # every 60 seconds
        },
        "reconcile-stale-research-jobs": {
            "task": "ai_tools.reconcile_stale_jobs",
            "schedule": 300.0,  # every 5 minutes
        },
        "reconcile-account-deletions": {
            "task": "privacy.reconcile_deletions",
            "schedule": 300.0,
        },
        "reconcile-stale-claim-audits": {
            "task": "claim_audit.reconcile_stale",
            "schedule": 300.0,
        },
        "reconcile-union3-research-loop": {
            "task": "union3.reconcile",
            "schedule": 300.0,
        },
        "reconcile-queued-union3-sources": {
            "task": "research_source.union3.reconcile",
            "schedule": 300.0,
            "options": {"queue": "control"},
        },
        "purge-expired-product-events": {
            "task": "privacy.purge_expired_product_events",
            "schedule": 3600.0,
        },
        "purge-uncommitted-artifacts": {
            "task": "privacy.purge_artifact_cleanup_queue",
            "schedule": 300.0,
        },
        "cleanup-expired-worker-artifacts": {
            "task": "maintenance.cleanup_worker_artifacts",
            "schedule": 900.0,
            "options": {"queue": "maintenance"},
        },
    }
    if _boolean_env("ENABLE_TRANSIENT_ALERT_INGEST"):
        schedule["ingest-ztf-alerts"] = {
            "task": "alerts.ingest",
            "schedule": 900.0,  # every 15 minutes
        }
    if _strict_boolean_env("POSTGRES_BACKUP_ENABLED"):
        schedule["daily-encrypted-postgresql-backup"] = {
            "task": "maintenance.postgres_backup",
            "schedule": crontab(hour=3, minute=15),
            "options": {"queue": "maintenance"},
        }
    return schedule


# Celery Beat schedule: cosmology-safe tasks are enabled by default. The
# retained ZTF/Lasair vertical is dormant unless an operator explicitly opts
# in on a separate deployment.
celery_app.conf.beat_schedule = _build_beat_schedule()


def _write_beat_release_lease(owner: str) -> str:
    """Renew this Beat instance's release lease directly in Redis."""
    import redis

    normalized_owner = str(owner or "").strip().lower()
    if _BEAT_OWNER.fullmatch(normalized_owner) is None:
        raise ValueError("Beat release lease owner must be 32 lowercase hex chars")
    commit = _runtime_commit()
    if commit == "unknown" and os.getenv("ENV", "dev").lower() == "production":
        raise RuntimeError("Beat release lease requires a full Git SHA")

    client = redis.Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
        **settings.redis_tls_kwargs(),
    )
    try:
        stored = client.set(
            f"{_BEAT_RELEASE_LEASE_PREFIX}{normalized_owner}",
            commit,
            ex=_BEAT_RELEASE_HEARTBEAT_TTL_SECONDS,
        )
        if not stored:
            raise RuntimeError("Redis did not acknowledge Beat release lease")
    finally:
        client.close()
    return commit


class ReleaseLeaseScheduler(PersistentScheduler):
    """Persistent Beat scheduler with tick-coupled per-instance release leases."""

    def __init__(self, *args, **kwargs) -> None:
        self._release_lease_owner = uuid.uuid4().hex
        self._last_release_lease_attempt: float | None = None
        super().__init__(*args, **kwargs)

    def _maybe_renew_release_lease(self) -> None:
        now = time.monotonic()
        last_attempt = self._last_release_lease_attempt
        if (
            last_attempt is not None
            and now - last_attempt < _BEAT_RELEASE_HEARTBEAT_SECONDS
        ):
            return
        # Throttle both successful writes and failures. A persistent Redis
        # outage expires the lease fail-closed without log-spamming every tick.
        self._last_release_lease_attempt = now
        try:
            _write_beat_release_lease(self._release_lease_owner)
        except Exception:
            logger.exception("Celery Beat release lease renewal failed")

    def tick(self, *args, **kwargs):
        # Never renew before/surrounding the scheduler operation: if the main
        # scheduling loop blocks or raises, its lease must age out.
        next_interval = super().tick(*args, **kwargs)
        self._maybe_renew_release_lease()
        if next_interval is None:
            return _BEAT_RELEASE_HEARTBEAT_SECONDS
        return min(next_interval, _BEAT_RELEASE_HEARTBEAT_SECONDS)


@celery_app.task(name="scheduler.check_due_schedules")
def check_due_schedules_task():
    """Celery Beat task that checks for due scheduled pipeline runs and dispatches them."""
    from app.scheduler_worker import check_and_dispatch_due_schedules
    check_and_dispatch_due_schedules()


@worker_ready.connect
def reconcile_stale_jobs_on_worker_start(**_kwargs) -> None:
    """Fail visibly stale jobs after a worker restart before new work arrives."""
    try:
        from app.services.durable_research_records import reconcile_stale_jobs

        reconciled = reconcile_stale_jobs()
        if reconciled:
            logger.warning(
                "reconciled %d stale research job(s) at worker startup",
                reconciled,
            )
    except Exception:
        # Do not make a transient reconciliation query take down the worker.
        # Beat retries the same operation every five minutes and this failure is
        # deliberately ERROR-visible rather than silently best-effort.
        logger.exception("startup stale-job reconciliation failed")
