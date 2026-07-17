"""Worker entry point for the top-level Claim Audit lifecycle."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

logger = logging.getLogger(__name__)


def _get_celery_app():
    from celery_worker import celery_app

    return celery_app


async def _heartbeat(
    audit_id: uuid.UUID,
    worker_lease_id: str,
    stop: asyncio.Event,
) -> None:
    from app.config import settings
    from app.models.database import async_session
    from app.services.claim_audit_service import renew_claim_audit_lease

    interval = float(settings.claim_audit_heartbeat_seconds)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        async with async_session() as heartbeat_db:
            if not await renew_claim_audit_lease(
                heartbeat_db,
                audit_id=audit_id,
                worker_lease_id=worker_lease_id,
            ):
                return


async def _process(audit_id: uuid.UUID) -> str:
    from app.models.claim_audit_records import ClaimAudit
    from app.models.database import async_session
    from app.models.schemas import User
    from app.services.claim_audit_service import process_claim_audit

    async with async_session() as db:
        audit = (
            await db.execute(
                select(ClaimAudit)
                .join(User, User.id == ClaimAudit.user_id)
                .where(
                    ClaimAudit.id == audit_id,
                    User.account_status == "ACTIVE",
                )
            )
        ).scalar_one_or_none()
        if audit is None or audit.lifecycle_status not in {"QUEUED", "RUNNING"}:
            return "terminal"
        worker_lease_id = uuid.uuid4().hex
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(
            _heartbeat(audit_id, worker_lease_id, stop)
        )
        try:
            result = await process_claim_audit(
                db,
                audit,
                worker_lease_id=worker_lease_id,
            )
        finally:
            stop.set()
            await heartbeat
        if (
            result.lifecycle_status == "RUNNING"
            and result.worker_lease_id != worker_lease_id
        ):
            return "active_lease"
        return "processed"


@_get_celery_app().task(
    bind=True,
    name="claim_audit.process",
    max_retries=None,
    ignore_result=True,
)
def process_claim_audit_task(self, audit_id: str) -> None:
    try:
        outcome = asyncio.run(_process(uuid.UUID(audit_id)))
    except Exception as exc:
        logger.exception("Claim Audit worker failed before lifecycle finalization")
        raise self.retry(exc=exc, countdown=min(1800, 30 * (2 ** self.request.retries)))
    if outcome == "active_lease":
        from app.config import settings

        raise self.retry(
            exc=RuntimeError("Claim Audit is held by an active worker lease"),
            countdown=int(settings.claim_audit_worker_lease_seconds),
        )


async def _reconcile_stale() -> int:
    from app.config import settings
    from app.models.claim_audit_records import ClaimAudit, EvidencePack
    from app.models.database import async_session
    from app.models.research_records import ResearchJob
    from app.models.schemas import User

    now = datetime.now(timezone.utc)
    upload_cutoff = now - timedelta(
        seconds=2 * int(settings.claim_audit_worker_lease_seconds)
    )
    reconciled = 0
    async with async_session() as db:
        def lease_expired(value: datetime | None) -> bool:
            if value is None:
                return True
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value < now

        async def stop_generated_children(
            audit: ClaimAudit,
            *,
            status: str,
            error_class: str | None,
        ) -> int:
            if not audit.child_job_ids:
                return 0
            children = list((await db.execute(
                select(ResearchJob).where(
                    ResearchJob.user_id == audit.user_id,
                    ResearchJob.job_id.in_(audit.child_job_ids),
                    ResearchJob.status.in_({"queued", "running"}),
                )
            )).scalars().all())
            for child in children:
                if isinstance(child.result, dict) and child.result.get("_artifact_ref"):
                    try:
                        from app.storage import delete_fits_all_versions

                        await asyncio.to_thread(
                            delete_fits_all_versions,
                            str(child.result["_artifact_ref"]),
                        )
                        child.result = None
                    except FileNotFoundError:
                        child.result = None
                    except Exception:
                        logger.exception(
                            "Could not remove stale child artifact for %s",
                            child.job_id,
                        )
                child.status = status
                child.error_class = error_class
                child.error = (
                    "Parent Claim Audit worker lease expired"
                    if error_class
                    else None
                )
                child.progress_message = f"{status.capitalize()} during Claim Audit recovery"
                child.completed_at = datetime.now(timezone.utc)
                child.attestation = None
            return len(children)

        staged = list((await db.execute(
            select(EvidencePack)
            .where(
                EvidencePack.status == "UPLOADING",
                EvidencePack.upload_started_at < upload_cutoff,
                EvidencePack.user_id.in_(
                    select(User.id).where(User.account_status == "ACTIVE")
                ),
            )
            .order_by(EvidencePack.upload_started_at.asc())
            .limit(100)
            .with_for_update(skip_locked=True)
        )).scalars().all())
        for pack in staged:
            audit = await db.get(ClaimAudit, pack.audit_id)
            if (
                audit is not None
                and audit.lifecycle_status == "RUNNING"
                and not lease_expired(audit.lease_expires_at)
            ):
                # A long object-store upload may predate upload_cutoff while
                # a worker is healthy. Even a different upload token may be a
                # takeover between row load and restaging; do not race it.
                continue
            cleaned = False
            try:
                from app.storage import delete_fits_all_versions

                await asyncio.to_thread(
                    delete_fits_all_versions,
                    pack.artifact_ref,
                )
                cleaned = True
            except FileNotFoundError:
                cleaned = True
            except Exception:
                logger.exception(
                    "Could not reconcile staged Evidence Pack %s", pack.id
                )
            parent_lease_expired = (
                audit is not None
                and audit.lifecycle_status == "RUNNING"
                and audit.worker_lease_id == pack.upload_lease_id
                and lease_expired(audit.lease_expires_at)
            )
            if parent_lease_expired:
                audit.lifecycle_status = "FAILED_RETRYABLE"
                audit.scientific_verdict = None
                audit.error_class = "stale_evidence_pack_upload"
                audit.error = "Evidence Pack upload did not finalize before its lease expired"
                await stop_generated_children(
                    audit,
                    status="failed",
                    error_class="stale_claim_audit_worker",
                )
                audit.worker_lease_id = None
                audit.lease_expires_at = None
            if cleaned:
                await db.delete(pack)
            reconciled += 1

        running = list((await db.execute(
            select(ClaimAudit)
            .where(
                ClaimAudit.lifecycle_status == "RUNNING",
                ClaimAudit.user_id.in_(
                    select(User.id).where(User.account_status == "ACTIVE")
                ),
                or_(
                    ClaimAudit.lease_expires_at.is_(None),
                    ClaimAudit.lease_expires_at < now,
                ),
            )
            .order_by(ClaimAudit.lease_expires_at.asc())
            .limit(100)
            .with_for_update(skip_locked=True)
        )).scalars().all())
        for audit in running:
            audit.lifecycle_status = "FAILED_RETRYABLE"
            audit.scientific_verdict = None
            audit.error_class = "stale_claim_audit_worker"
            audit.error = "Claim Audit worker lease expired before finalization"
            await stop_generated_children(
                audit,
                status="failed",
                error_class="stale_claim_audit_worker",
            )
            audit.worker_lease_id = None
            audit.lease_expires_at = None
            reconciled += 1

        cancelled = list((await db.execute(
            select(ClaimAudit)
            .where(
                ClaimAudit.lifecycle_status == "CANCELLED",
                ClaimAudit.updated_at < upload_cutoff,
                ClaimAudit.user_id.in_(
                    select(User.id).where(User.account_status == "ACTIVE")
                ),
            )
            .order_by(ClaimAudit.updated_at.asc())
            .limit(100)
            .with_for_update(skip_locked=True)
        )).scalars().all())
        for audit in cancelled:
            reconciled += await stop_generated_children(
                audit,
                status="cancelled",
                error_class=None,
            )
            # Rotate terminal rows through the bounded batch even when there
            # are no active children, preventing the oldest 100 rows from
            # starving later cancellation cleanup forever.
            audit.updated_at = now
        await db.commit()
    return reconciled


@_get_celery_app().task(
    name="claim_audit.reconcile_stale",
    ignore_result=True,
)
def reconcile_stale_claim_audits_task() -> None:
    asyncio.run(_reconcile_stale())
