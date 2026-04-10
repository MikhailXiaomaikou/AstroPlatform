from celery import Celery

from app.config import settings

celery_app = Celery(
    "astro_pipeline",
    broker=settings.celery_broker_url,
    backend=settings.celery_broker_url,
)

_broker_opts: dict = {}
if settings.redis_ssl:
    import ssl
    _broker_opts = {
        "broker_use_ssl": {"ssl_cert_reqs": ssl.CERT_NONE},
        "redis_backend_use_ssl": {"ssl_cert_reqs": ssl.CERT_NONE},
    }

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    **_broker_opts,
)

# Auto-discover pipeline tasks (tasks.py in app.pipeline)
# and explicitly include engine.py which also defines Celery tasks
celery_app.autodiscover_tasks(["app.pipeline"])
celery_app.conf.include = ["app.pipeline.engine", "app.services.isochrone_tasks"]

# Celery Beat schedule: run the scheduler check every 60 seconds
celery_app.conf.beat_schedule = {
    "check-scheduled-pipelines": {
        "task": "scheduler.check_due_schedules",
        "schedule": 60.0,  # every 60 seconds
    },
}


@celery_app.task(name="scheduler.check_due_schedules")
def check_due_schedules_task():
    """Celery Beat task that checks for due scheduled pipeline runs and dispatches them."""
    from app.scheduler_worker import check_and_dispatch_due_schedules
    check_and_dispatch_due_schedules()
