"""Control-plane tasks for the registered Union3 reproduction loop.

Redis/Celery is only a delivery mechanism.  PostgreSQL remains authoritative,
and the reconciler rediscovers completed attempts and review-approved audits
after broker or process restarts.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

logger = logging.getLogger(__name__)


def _union3_evidence_lane_enabled() -> bool:
    from app.config import settings

    return all(
        (
            settings.claim_audit_enabled,
            settings.research_workspace_enabled,
            settings.arxiv_reader_enabled,
            settings.union3_reproduction_enabled,
            settings.evidence_pack_v2_enabled,
            settings.local_science_worker_enabled,
        )
    )


def _get_celery_app():
    from celery_worker import celery_app

    return celery_app


def enqueue_union3_verification(attempt_id: uuid.UUID) -> None:
    """Best-effort wake-up; reconciliation is the durable fallback."""

    if not _union3_evidence_lane_enabled():
        return

    _get_celery_app().send_task(
        "union3.verify_primary_attempt",
        args=[str(attempt_id)],
        queue="verification",
    )


def enqueue_union3_finalization(audit_id: uuid.UUID) -> None:
    """Best-effort wake-up for the deterministic finalizer."""

    if not _union3_evidence_lane_enabled():
        return

    _get_celery_app().send_task(
        "union3.finalize_audit",
        args=[str(audit_id)],
        queue="verification",
    )


async def _mark_verification_withheld(attempt_id: uuid.UUID, code: str) -> None:
    from app.models.claim_audit_records import ClaimAudit
    from app.models.database import async_session
    from app.models.worker_records import ScienceExecutionAttempt

    async with async_session() as db:
        attempt = await db.get(ScienceExecutionAttempt, attempt_id)
        if attempt is None or attempt.audit_id is None:
            return
        audit = await db.get(ClaimAudit, attempt.audit_id)
        if (
            audit is None
            or audit.lifecycle_status != "RUNNING"
            or audit.scientific_verdict is not None
        ):
            return
        audit.lifecycle_status = "COMPLETED"
        audit.scientific_verdict = "WITHHELD"
        audit.machine_support_eligible = False
        audit.reproduction_ready = False
        audit.publication_ready = False
        audit.review_status = "NOT_SUBMITTED"
        audit.progress_stage = "machine_verification_withheld"
        audit.error_class = code[:255]
        audit.error = "Independent verification did not satisfy the registered gate"
        await db.commit()


async def _verify(attempt_id: uuid.UUID) -> str:
    from app.models.database import async_session
    from app.services.union3_research_loop import verify_union3_attempt

    async with async_session() as db:
        audit = await verify_union3_attempt(db, attempt_id=attempt_id)
        return str(audit.id)


@_get_celery_app().task(
    bind=True,
    name="union3.verify_primary_attempt",
    max_retries=5,
    ignore_result=True,
)
def verify_union3_primary_attempt_task(self, attempt_id: str) -> None:
    from app.services.union3_research_loop import Union3ResearchLoopError

    if not _union3_evidence_lane_enabled():
        logger.info("Union3 verification skipped because the evidence lane is disabled")
        return
    parsed_id = uuid.UUID(attempt_id)
    try:
        asyncio.run(_verify(parsed_id))
    except Union3ResearchLoopError as exc:
        if exc.status_code >= 500:
            logger.warning(
                "Union3 verifier temporarily unavailable (%s): %s",
                exc.code,
                parsed_id,
            )
            raise self.retry(
                exc=exc,
                countdown=min(900, 15 * (2**self.request.retries)),
            )
        if exc.code in {"claim_audit_cancelled", "claim_audit_not_verifiable"}:
            logger.info("Union3 verification skipped (%s): %s", exc.code, parsed_id)
            return
        logger.warning("Union3 verification withheld (%s): %s", exc.code, parsed_id)
        asyncio.run(_mark_verification_withheld(parsed_id, exc.code))
    except Exception as exc:
        logger.exception("Union3 verification task failed before a durable verdict")
        raise self.retry(exc=exc, countdown=min(900, 15 * (2**self.request.retries)))


async def _finalize(audit_id: uuid.UUID) -> str:
    from app.models.database import async_session
    from app.services.union3_research_loop import finalize_union3_audit

    async with async_session() as db:
        audit, _pack = await finalize_union3_audit(db, audit_id=audit_id)
        return str(audit.id)


@_get_celery_app().task(
    bind=True,
    name="union3.finalize_audit",
    max_retries=5,
    ignore_result=True,
)
def finalize_union3_audit_task(self, audit_id: str) -> None:
    from app.services.union3_research_loop import Union3ResearchLoopError

    if not _union3_evidence_lane_enabled():
        logger.info("Union3 finalization skipped because the evidence lane is disabled")
        return
    try:
        asyncio.run(_finalize(uuid.UUID(audit_id)))
    except Union3ResearchLoopError as exc:
        # A review/machine-gate mismatch is a scientific refusal, not an
        # infrastructure retry.  The audit deliberately remains WITHHELD.
        logger.warning("Union3 finalization refused (%s): %s", exc.code, audit_id)
    except Exception as exc:
        logger.exception("Union3 finalizer failed before an atomic commit")
        raise self.retry(exc=exc, countdown=min(900, 15 * (2**self.request.retries)))


async def _reconcile() -> dict[str, int]:
    if not _union3_evidence_lane_enabled():
        return {"expired": 0, "verified": 0, "finalized": 0}
    from app.models.claim_audit_records import ClaimAudit
    from app.models.database import async_session
    from app.models.research_records import ResearchJob
    from app.models.worker_records import ScienceExecutionAttempt
    from app.services.union3_research_loop import (
        Union3ResearchLoopError,
        finalize_union3_audit,
        verify_union3_attempt,
    )
    from app.services.worker_protocol import reconcile_expired_attempts

    expired = 0
    verified = 0
    finalized = 0
    async with async_session() as db:
        expired = await reconcile_expired_attempts(db)
    async with async_session() as db:
        attempts = list(
            (
                await db.execute(
                    select(ScienceExecutionAttempt)
                    .join(ResearchJob, ResearchJob.job_id == ScienceExecutionAttempt.job_id)
                    .join(ClaimAudit, ClaimAudit.id == ScienceExecutionAttempt.audit_id)
                    .where(
                        ScienceExecutionAttempt.status == "SUCCEEDED",
                        ResearchJob.current_attempt_id == ScienceExecutionAttempt.id,
                        ClaimAudit.lifecycle_status == "RUNNING",
                        ClaimAudit.scientific_verdict.is_not_distinct_from(None),
                    )
                    .order_by(ScienceExecutionAttempt.completed_at.asc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
        attempt_ids = [row.id for row in attempts]

    for attempt_id in attempt_ids:
        try:
            async with async_session() as db:
                await verify_union3_attempt(db, attempt_id=attempt_id)
            verified += 1
        except Union3ResearchLoopError as exc:
            if exc.status_code >= 500:
                logger.warning(
                    "Union3 reconciliation will retry transient verifier error (%s): %s",
                    exc.code,
                    attempt_id,
                )
            elif exc.code not in {
                "claim_audit_cancelled",
                "claim_audit_not_verifiable",
            }:
                await _mark_verification_withheld(attempt_id, exc.code)
        except Exception:
            logger.exception("Union3 reconciliation could not verify %s", attempt_id)

    async with async_session() as db:
        audits = list(
            (
                await db.execute(
                    select(ClaimAudit)
                    .where(
                        ClaimAudit.lifecycle_status == "COMPLETED",
                        ClaimAudit.scientific_verdict == "WITHHELD",
                        ClaimAudit.machine_support_eligible.is_(True),
                        ClaimAudit.review_status == "APPROVED",
                        ClaimAudit.reproduction_ready.is_(False),
                        ClaimAudit.publication_ready.is_(False),
                    )
                    .order_by(ClaimAudit.updated_at.asc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
        audit_ids = [row.id for row in audits]

    for audit_id in audit_ids:
        try:
            async with async_session() as db:
                await finalize_union3_audit(db, audit_id=audit_id)
            finalized += 1
        except Union3ResearchLoopError:
            # Exact bindings are checked again on every run; remaining
            # WITHHELD is the safe terminal behavior.
            continue
        except Exception:
            logger.exception("Union3 reconciliation could not finalize %s", audit_id)
    return {"expired": expired, "verified": verified, "finalized": finalized}


@_get_celery_app().task(name="union3.reconcile", ignore_result=True)
def reconcile_union3_research_loop_task() -> dict[str, int]:
    return asyncio.run(_reconcile())


async def _cleanup_worker_artifacts() -> dict[str, int]:
    """Remove expired staging/superseded objects from the durable URL ledger."""

    from app.models.database import async_session
    from app.models.worker_records import WorkerArtifactIssuance
    from app.storage import delete_fits_all_versions

    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    async with async_session() as db:
        issuance_ids = list(
            (
                await db.execute(
                    select(WorkerArtifactIssuance.id)
                    .where(
                        WorkerArtifactIssuance.expires_at <= cutoff,
                        (
                            WorkerArtifactIssuance.staging_cleaned_at.is_(None)
                            | (
                                WorkerArtifactIssuance.verified_at.is_(None)
                                & WorkerArtifactIssuance.authoritative_cleaned_at.is_(None)
                            )
                        ),
                    )
                    .order_by(WorkerArtifactIssuance.expires_at.asc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
    cleaned = 0
    failed = 0
    for issuance_id in issuance_ids:
        async with async_session() as db:
            row = await db.scalar(
                select(WorkerArtifactIssuance)
                .where(WorkerArtifactIssuance.id == issuance_id)
                .with_for_update()
            )
            if row is None or row.expires_at > cutoff:
                continue
            try:
                if row.staging_cleaned_at is None:
                    await asyncio.to_thread(delete_fits_all_versions, row.artifact_ref)
                    row.staging_cleaned_at = datetime.now(timezone.utc)
                if row.verified_at is None and row.authoritative_cleaned_at is None:
                    await asyncio.to_thread(
                        delete_fits_all_versions,
                        row.authoritative_ref,
                    )
                    row.authoritative_cleaned_at = datetime.now(timezone.utc)
                await db.commit()
                cleaned += 1
            except Exception:
                await db.rollback()
                failed += 1
                logger.exception("Worker artifact cleanup failed for %s", issuance_id)
    return {"cleaned": cleaned, "failed": failed}


@_get_celery_app().task(name="maintenance.cleanup_worker_artifacts", ignore_result=True)
def cleanup_worker_artifacts_task() -> dict[str, int]:
    return asyncio.run(_cleanup_worker_artifacts())


__all__ = [
    "enqueue_union3_finalization",
    "enqueue_union3_verification",
    "cleanup_worker_artifacts_task",
    "finalize_union3_audit_task",
    "reconcile_union3_research_loop_task",
    "verify_union3_primary_attempt_task",
]
