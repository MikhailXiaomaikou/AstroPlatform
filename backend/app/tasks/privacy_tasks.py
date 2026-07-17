"""Celery jobs for account erasure and bounded analytics retention."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

logger = logging.getLogger(__name__)


def _get_celery_app():
    from celery_worker import celery_app

    return celery_app


@_get_celery_app().task(
    bind=True,
    name="privacy.erase_account",
    max_retries=8,
    ignore_result=True,
)
def erase_account_task(
    self,
    user_id: str,
    tombstone_id: str,
    lease_id: str | None = None,
) -> None:
    from app.services.account_deletion import erase_account_data

    try:
        asyncio.run(
            erase_account_data(
                user_id=uuid.UUID(user_id),
                tombstone_id=uuid.UUID(tombstone_id),
                lease_id=lease_id,
            )
        )
    except Exception as exc:
        logger.exception("Asynchronous account erasure failed")
        raise self.retry(exc=exc, countdown=min(3600, 30 * (2 ** self.request.retries)))


@_get_celery_app().task(
    name="privacy.purge_expired_product_events",
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=3600,
    max_retries=8,
)
def purge_expired_product_events_task() -> dict[str, int]:
    from app.config import settings
    from app.services.event_collector import (
        purge_expired_inference_logs,
        purge_expired_product_events,
    )

    async def _purge() -> tuple[int, int]:
        events_deleted = await purge_expired_product_events(
            retention_days=settings.product_analytics_retention_days
        )
        inference_logs_deleted = await purge_expired_inference_logs(
            retention_days=settings.product_analytics_retention_days
        )
        return events_deleted, inference_logs_deleted

    events_deleted, inference_logs_deleted = asyncio.run(_purge())
    return {
        "events_deleted": events_deleted,
        "inference_logs_deleted": inference_logs_deleted,
    }


@_get_celery_app().task(
    name="privacy.purge_artifact_cleanup_queue",
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=3600,
    max_retries=8,
)
def purge_artifact_cleanup_queue_task() -> dict[str, int]:
    from app.services.artifact_cleanup import purge_artifact_cleanup_queue

    result = asyncio.run(purge_artifact_cleanup_queue())
    if result["objects_failed"]:
        raise RuntimeError("One or more queued artifacts could not be deleted")
    return result


async def _reconcile_deletions() -> list[tuple[str, str, str]]:
    from app.auth import hash_password
    from app.models.claim_audit_records import AccountDeletionTombstone
    from app.models.database import async_session
    from app.models.schemas import User
    from app.services.account_deletion import (
        claim_account_deletion_lease,
        deletion_user_fingerprint,
        external_deletion_tombstone_exists,
    )

    now = datetime.now(timezone.utc)
    async with async_session() as db:
        users = (await db.execute(select(User))).scalars().all()
        tombstones = (
            await db.execute(select(AccountDeletionTombstone))
        ).scalars().all()
        by_fingerprint = {row.user_fingerprint: row for row in tombstones}
        candidates: set[tuple[uuid.UUID, uuid.UUID]] = set()

        for user in users:
            fingerprint = deletion_user_fingerprint(user.id)
            tombstone = by_fingerprint.get(fingerprint)
            externally_deleted = await asyncio.to_thread(
                external_deletion_tombstone_exists,
                user.id,
            )
            if externally_deleted and tombstone is None:
                tombstone = AccountDeletionTombstone(
                    user_fingerprint=fingerprint,
                    status="RETRYABLE",
                    receipt_hash=hashlib.sha256(
                        f"recovered:{fingerprint}".encode("utf-8")
                    ).hexdigest(),
                    requested_at=now,
                    backup_expires_at=now + timedelta(days=30),
                    last_error_class="restored_database_reconciliation",
                )
                db.add(tombstone)
                await db.flush()
                by_fingerprint[fingerprint] = tombstone
            elif externally_deleted and tombstone.status == "COMPLETED":
                # A completed tombstone plus a live user means an older DB
                # snapshot was restored. Re-run erasure before serving it.
                tombstone.status = "RETRYABLE"
                tombstone.completed_at = None
                tombstone.last_error_class = "restored_database_reconciliation"

            if tombstone is None:
                continue
            if externally_deleted:
                was_active = str(user.account_status or "").upper() == "ACTIVE"
                user.account_status = "DELETION_PENDING"
                user.deletion_requested_at = user.deletion_requested_at or now
                if was_active:
                    user.api_keys = None
                    user.anthropic_api_key = None
                    user.google_id = None
                    user.password_hash = hash_password(
                        uuid.uuid4().hex + uuid.uuid4().hex
                    )
            if tombstone.status != "COMPLETED":
                candidates.add((user.id, tombstone.id))

        # The user row may already be gone when an in-flight worker reports a
        # late output. pending_user_id exists only until that object is erased.
        for tombstone in tombstones:
            if (
                tombstone.pending_user_id is not None
                and tombstone.status != "COMPLETED"
            ):
                candidates.add((tombstone.pending_user_id, tombstone.id))

        await db.flush()
        work: list[tuple[str, str, str]] = []
        for user_id, tombstone_id in candidates:
            lease_id = await claim_account_deletion_lease(
                db,
                user_id=user_id,
                tombstone_id=tombstone_id,
                commit=False,
            )
            if lease_id is not None:
                work.append((str(user_id), str(tombstone_id), lease_id))
        await db.commit()
        return work


@_get_celery_app().task(
    name="privacy.reconcile_deletions",
    ignore_result=True,
)
def reconcile_deletions_task() -> dict[str, int]:
    work = asyncio.run(_reconcile_deletions())
    for user_id, tombstone_id, lease_id in work:
        erase_account_task.delay(user_id, tombstone_id, lease_id)
    return {"scheduled": len(work)}
