"""Durable cleanup discovery for objects uploaded before DB ledger commit."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.claim_audit_records import ArtifactCleanupQueue
from app.models.database import async_session
from app.config import settings
from app.storage import delete_fits_all_versions, normalize_storage_key

logger = logging.getLogger(__name__)


def _owner_fingerprint(user_id: str | uuid.UUID | None) -> str | None:
    if user_id in (None, ""):
        return None
    from app.services.account_deletion import deletion_user_fingerprint

    owner_id = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    return deletion_user_fingerprint(owner_id)


def stage_artifact_cleanup_sync(
    artifact_ref: str,
    *,
    user_id: str | uuid.UUID | None,
    reason_class: str,
) -> str:
    """Commit cleanup discovery before any object bytes are uploaded."""

    from app.services.durable_research_records import _engine

    normalized = normalize_storage_key(artifact_ref)
    deadline = datetime.now(timezone.utc) + timedelta(
        seconds=int(settings.artifact_cleanup_grace_seconds)
    )
    with Session(_engine()) as db:
        row = db.scalar(
            select(ArtifactCleanupQueue)
            .where(ArtifactCleanupQueue.artifact_ref == normalized)
            .with_for_update()
        )
        if row is None:
            row = ArtifactCleanupQueue(
                user_fingerprint=_owner_fingerprint(user_id),
                artifact_ref=normalized,
                reason_class=str(reason_class)[:64],
                not_before=deadline,
            )
            db.add(row)
        else:
            row.user_fingerprint = _owner_fingerprint(user_id)
            row.reason_class = str(reason_class)[:64]
            row.last_error_class = None
            row.not_before = deadline
        db.commit()
    return normalized


def renew_artifact_cleanup_grace_sync(artifact_ref: str) -> None:
    """Restart the safety window after a potentially long object upload."""

    from app.services.durable_research_records import _engine

    normalized = normalize_storage_key(artifact_ref)
    deadline = datetime.now(timezone.utc) + timedelta(
        seconds=int(settings.artifact_cleanup_grace_seconds)
    )
    with Session(_engine()) as db:
        row = db.scalar(
            select(ArtifactCleanupQueue)
            .where(ArtifactCleanupQueue.artifact_ref == normalized)
            .with_for_update()
        )
        if row is None:
            raise LookupError("Artifact cleanup discovery disappeared during upload")
        row.not_before = deadline
        db.commit()


def clear_artifact_cleanup_sync(artifact_ref: str) -> None:
    """Remove a queue row after successful immediate object deletion."""

    from app.services.durable_research_records import _engine

    normalized = normalize_storage_key(artifact_ref)
    with Session(_engine()) as db:
        db.execute(
            delete(ArtifactCleanupQueue).where(
                ArtifactCleanupQueue.artifact_ref == normalized
            )
        )
        db.commit()


async def _artifact_is_referenced(session: AsyncSession, artifact_ref: str) -> bool:
    """Check every trusted durable object ledger before destructive cleanup."""

    from app.models.claim_audit_records import EvidencePack
    from app.models.research_records import ResearchJob
    from app.models.schemas import DataFile, PipelineRun, RunResult

    for model, column in (
        (EvidencePack, EvidencePack.artifact_ref),
        (DataFile, DataFile.fits_path),
        (RunResult, RunResult.output_path),
    ):
        if await session.scalar(select(model).where(column == artifact_ref).limit(1)):
            return True

    def _contains(value: object) -> bool:
        if isinstance(value, dict):
            if value.get("_artifact_ref") == artifact_ref:
                return True
            if value.get("output_path") == artifact_ref:
                return True
            return any(_contains(nested) for nested in value.values())
        if isinstance(value, (list, tuple)):
            return any(_contains(nested) for nested in value)
        return False

    for value in (
        await session.scalars(select(ResearchJob.result).where(ResearchJob.result.is_not(None)))
    ).all():
        if _contains(value):
            return True
    for value in (
        await session.scalars(select(PipelineRun.results).where(PipelineRun.results.is_not(None)))
    ).all():
        if _contains(value):
            return True
    return False


async def purge_artifact_cleanup_queue(
    *,
    limit: int = 100,
    db: AsyncSession | None = None,
) -> dict[str, int]:
    """Permanently delete queued objects; failures remain discoverable."""

    async def _purge(session: AsyncSession) -> dict[str, int]:
        rows = list(
            (
                await session.execute(
                    select(ArtifactCleanupQueue)
                    .where(
                        ArtifactCleanupQueue.not_before
                        <= datetime.now(timezone.utc)
                    )
                    .order_by(ArtifactCleanupQueue.created_at.asc())
                    .limit(limit)
                )
            ).scalars().all()
        )
        deleted = 0
        failed = 0
        referenced = 0
        for row in rows:
            try:
                if await _artifact_is_referenced(session, row.artifact_ref):
                    await session.delete(row)
                    await session.commit()
                    referenced += 1
                    continue
                await asyncio.to_thread(delete_fits_all_versions, row.artifact_ref)
                await session.delete(row)
                await session.commit()
                deleted += 1
            except Exception as exc:
                await session.rollback()
                failed += 1
                current = await session.get(ArtifactCleanupQueue, row.id)
                if current is not None:
                    current.attempt_count += 1
                    current.last_error_class = exc.__class__.__name__[:255]
                    await session.commit()
                logger.exception("Artifact cleanup remains queued for %s", row.artifact_ref)
        return {
            "objects_deleted": deleted,
            "objects_failed": failed,
            "references_reconciled": referenced,
        }

    if db is not None:
        return await _purge(db)
    async with async_session() as session:
        return await _purge(session)
