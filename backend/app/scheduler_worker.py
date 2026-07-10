"""Scheduler background worker — checks for due scheduled pipeline runs and dispatches them.

Can be run in two ways:
1. As a standalone script: python -m app.scheduler_worker
2. Via Celery Beat: the check_due_schedules_task in celery_worker.py calls
   check_and_dispatch_due_schedules() every 60 seconds.
"""

import logging
import time
from datetime import datetime, timezone

import sqlalchemy
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.schemas import DataFile, ScheduledRun
from app.pipeline.storage_auth import collect_pipeline_storage_paths
from app.storage import normalize_storage_key

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60


def _scheduled_storage_keys(schedule: ScheduledRun) -> set[str]:
    """Return every storage key a scheduled DAG can open directly."""
    paths = collect_pipeline_storage_paths(
        dag=schedule.dag or {},
        input_data_id=str(schedule.input_data_id or ""),
    )
    return {normalize_storage_key(path) for path in paths}


def _scheduled_inputs_are_owned(session: Session, schedule: ScheduledRun) -> bool:
    """Re-authorize persisted paths immediately before worker dispatch."""
    try:
        required = _scheduled_storage_keys(schedule)
    except ValueError:
        return False
    if not required:
        return True
    owned = set(
        session.execute(
            select(DataFile.fits_path).where(
                DataFile.user_id == schedule.user_id,
                DataFile.fits_path.in_(required),
            )
        ).scalars().all()
    )
    return owned == required


def _create_sync_engine():
    """Create a synchronous SQLAlchemy engine from the configured database URL."""
    sync_url = settings.database_url
    if "+aiosqlite" in sync_url:
        sync_url = sync_url.replace("+aiosqlite", "")
    elif "+asyncpg" in sync_url:
        sync_url = sync_url.replace("+asyncpg", "+psycopg2")
    return sqlalchemy.create_engine(sync_url)


def _compute_next_run(cron_expr: str) -> datetime | None:
    """Compute the next run time from a cron expression."""
    try:
        from croniter import croniter
        cron = croniter(cron_expr, datetime.now(timezone.utc))
        return cron.get_next(datetime)
    except ImportError:
        # Without croniter, default to 1 hour from now
        from datetime import timedelta
        return datetime.now(timezone.utc) + timedelta(hours=1)


def check_and_dispatch_due_schedules():
    """Query for due schedules and dispatch pipeline execution tasks.

    This function uses synchronous SQLAlchemy and can be called from
    either the standalone loop or from a Celery Beat task.
    """
    from app.pipeline.engine import execute_pipeline_task

    engine = _create_sync_engine()
    now = datetime.now(timezone.utc)

    try:
        with Session(engine) as session:
            # Find all enabled schedules whose next_run_at is in the past
            stmt = (
                select(ScheduledRun)
                .where(
                    ScheduledRun.enabled.is_(True),
                    ScheduledRun.next_run_at <= now,
                )
            )
            result = session.execute(stmt)
            due_schedules = result.scalars().all()

            if not due_schedules:
                logger.debug("No due schedules found")
                return

            logger.info(f"Found {len(due_schedules)} due schedule(s)")

            for schedule in due_schedules:
                schedule_id = str(schedule.id)
                logger.info(
                    f"Dispatching scheduled pipeline '{schedule.name}' "
                    f"(schedule_id={schedule_id})"
                )

                try:
                    if not _scheduled_inputs_are_owned(session, schedule):
                        # A deleted ownership row revokes future scheduled
                        # access. Disable the schedule instead of repeatedly
                        # probing an object that may now belong to another
                        # tenant or be an untracked orphan.
                        schedule.enabled = False
                        schedule.next_run_at = None
                        logger.error(
                            "Disabled schedule %s because its storage input is "
                            "missing or no longer owned by user %s",
                            schedule_id,
                            schedule.user_id,
                        )
                        continue

                    # Create a PipelineRun record for this scheduled execution
                    import uuid
                    from app.models.schemas import PipelineRun

                    run_id = uuid.uuid4()
                    run = PipelineRun(
                        id=run_id,
                        user_id=schedule.user_id,
                        dag=schedule.dag,
                        status="pending",
                    )
                    session.add(run)
                    session.flush()

                    # Dispatch is outside the database transaction boundary. If
                    # the broker rejects it, terminalize the already-flushed run
                    # explicitly instead of committing a permanent `pending`
                    # record that no worker can ever observe.
                    try:
                        execute_pipeline_task.delay(
                            str(run_id),
                            schedule.dag,
                            schedule.input_data_id,
                        )
                    except Exception:
                        run.status = "failed"
                        run.completed_at = datetime.now(timezone.utc)
                        run.results = {
                            "success": False,
                            "error_class": "celery_dispatch_failed",
                            "error": "Scheduled pipeline could not be queued.",
                        }
                        schedule.last_run_at = now
                        schedule.next_run_at = _compute_next_run(
                            schedule.cron_expr
                        )
                        logger.exception(
                            "Celery rejected scheduled run %s for schedule %s; "
                            "recorded a terminal failed attempt",
                            run_id,
                            schedule_id,
                        )
                        continue

                    # Update schedule timestamps
                    schedule.last_run_at = now
                    schedule.next_run_at = _compute_next_run(schedule.cron_expr)

                    logger.info(
                        f"Dispatched run_id={run_id} for schedule '{schedule.name}'. "
                        f"Next run at {schedule.next_run_at}"
                    )

                except Exception as e:
                    logger.error(
                        f"Failed to dispatch schedule '{schedule.name}' "
                        f"(schedule_id={schedule_id}): {e}"
                    )
                    # Continue processing other schedules
                    continue

            session.commit()

    except Exception as e:
        logger.exception(f"Scheduler check failed: {e}")
    finally:
        engine.dispose()


def run_scheduler_loop():
    """Main loop that polls for due schedules every POLL_INTERVAL_SECONDS."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info(
        f"Scheduler worker started. Polling every {POLL_INTERVAL_SECONDS}s."
    )

    while True:
        try:
            check_and_dispatch_due_schedules()
        except KeyboardInterrupt:
            logger.info("Scheduler worker stopped by user")
            break
        except Exception as e:
            logger.exception(f"Unexpected error in scheduler loop: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_scheduler_loop()
