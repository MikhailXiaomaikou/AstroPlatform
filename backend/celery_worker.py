import logging
import os
import ssl

from celery import Celery
from celery.signals import worker_ready

from app.config import settings

logger = logging.getLogger(__name__)


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


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
]

# Celery Beat schedule: run the scheduler check every 60 seconds
celery_app.conf.beat_schedule = {
    "check-scheduled-pipelines": {
        "task": "scheduler.check_due_schedules",
        "schedule": 60.0,  # every 60 seconds
    },
    "ingest-ztf-alerts": {
        "task": "alerts.ingest",
        "schedule": 900.0,  # every 15 minutes
    },
    "reconcile-stale-research-jobs": {
        "task": "ai_tools.reconcile_stale_jobs",
        "schedule": 300.0,  # every 5 minutes
    },
}


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
