"""Celery maintenance task for encrypted PostgreSQL backups."""

from __future__ import annotations

import logging
import os


logger = logging.getLogger(__name__)


def _get_celery_app():
    from celery_worker import celery_app

    return celery_app


def _enabled() -> bool:
    raw = str(os.getenv("POSTGRES_BACKUP_ENABLED") or "false").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    raise ValueError("POSTGRES_BACKUP_ENABLED must be a boolean")


@_get_celery_app().task(
    bind=True,
    name="maintenance.postgres_backup",
    max_retries=6,
    ignore_result=False,
)
def postgres_backup_task(self) -> dict[str, object]:
    """Create one encrypted, checksum-verified backup when explicitly enabled."""

    if not _enabled():
        return {"status": "disabled"}

    from scripts.ops.encrypted_postgres_backup import run_backup_from_environment

    try:
        return run_backup_from_environment()
    except Exception as exc:
        # Exception text contains configuration names or integrity classes, but
        # the implementation never includes secret values or DATABASE_URL.
        logger.exception("Encrypted PostgreSQL backup failed")
        raise self.retry(
            exc=exc,
            countdown=min(3600, 60 * (2 ** self.request.retries)),
        )
