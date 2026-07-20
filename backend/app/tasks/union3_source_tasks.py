"""Durable control-plane acquisition for the registered Union3 source.

Celery is a wake-up mechanism only.  The queued SourceDocument in PostgreSQL
is authoritative, and the periodic reconciler redelivers messages lost during
a Redis restart.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

logger = logging.getLogger(__name__)


def _source_reader_enabled() -> bool:
    from app.config import settings

    return bool(settings.research_workspace_enabled and settings.arxiv_reader_enabled)


def _get_celery_app():
    from celery_worker import celery_app

    return celery_app


def enqueue_union3_source(source_document_id: uuid.UUID) -> None:
    """Best-effort wake-up for one already persisted source request."""

    if not _source_reader_enabled():
        return

    _get_celery_app().send_task(
        "research_source.union3.ingest",
        args=[str(source_document_id)],
        queue="control",
    )


async def _process(source_document_id: uuid.UUID) -> str:
    from app.models.database import async_session
    from app.models.schemas import User
    from app.models.workspace_records import SourceDocument
    from app.services.research_workspace_service import (
        WorkspaceConflictError,
        process_queued_union3_source,
    )

    async with async_session() as db:
        document = await db.get(SourceDocument, source_document_id)
        if document is None:
            return "missing"
        owner = await db.get(User, document.user_id)
        if owner is None or str(owner.account_status or "ACTIVE").upper() != "ACTIVE":
            return "owner_inactive"
        if document.lifecycle_status == "COMPLETED":
            return "already_completed"
        if document.lifecycle_status not in {"QUEUED", "FAILED_RETRYABLE"}:
            return "not_processable"
        try:
            completed, _ = await process_queued_union3_source(
                db,
                source_document_id=source_document_id,
            )
        except WorkspaceConflictError:
            # Duplicate delivery may lose the finalization race.  Re-read the
            # durable row before treating that as an error.
            await db.rollback()
            current = await db.get(SourceDocument, source_document_id)
            if current is not None and current.lifecycle_status == "COMPLETED":
                return "already_completed"
            raise
        return completed.lifecycle_status


@_get_celery_app().task(
    bind=True,
    name="research_source.union3.ingest",
    max_retries=3,
    ignore_result=True,
)
def ingest_union3_source_task(self, source_document_id: str) -> None:
    from app.services.research_workspace_service import WorkspaceServiceError

    if not _source_reader_enabled():
        logger.info("Union3 source ingestion skipped because the reader is disabled")
        return
    parsed_id = uuid.UUID(source_document_id)
    try:
        asyncio.run(_process(parsed_id))
    except WorkspaceServiceError as exc:
        if exc.retryable:
            logger.warning(
                "Union3 source acquisition will retry (%s): %s",
                exc.error_class,
                parsed_id,
            )
            raise self.retry(
                exc=exc,
                countdown=min(900, 15 * (2**self.request.retries)),
            )
        logger.warning(
            "Union3 source acquisition refused (%s): %s",
            exc.error_class,
            parsed_id,
        )
    except Exception as exc:
        logger.exception(
            "Union3 source control task failed before a durable outcome: %s",
            parsed_id,
        )
        raise self.retry(
            exc=exc,
            countdown=min(900, 15 * (2**self.request.retries)),
        )


async def _queued_source_ids() -> list[uuid.UUID]:
    from app.models.database import async_session
    from app.models.schemas import User
    from app.models.workspace_records import SourceDocument

    async with async_session() as db:
        documents = list(
            (
                await db.execute(
                    select(SourceDocument)
                    .join(User, User.id == SourceDocument.user_id)
                    .where(
                        SourceDocument.lifecycle_status.in_(
                            {"QUEUED", "FAILED_RETRYABLE"}
                        ),
                        User.account_status == "ACTIVE",
                    )
                    .order_by(SourceDocument.created_at.asc())
                    .limit(1000)
                )
            )
            .scalars()
            .all()
        )
        now = datetime.now(timezone.utc)
        ready: list[uuid.UUID] = []
        for document in documents:
            if document.lifecycle_status == "QUEUED":
                ready.append(document.id)
                continue
            retry_state = (
                document.source_metadata.get("retry_state")
                if isinstance(document.source_metadata, dict)
                else None
            )
            if not isinstance(retry_state, dict) or retry_state.get(
                "auto_retry_exhausted"
            ):
                continue
            raw_next_retry = retry_state.get("next_retry_at")
            try:
                next_retry = datetime.fromisoformat(
                    str(raw_next_retry).replace("Z", "+00:00")
                )
            except (TypeError, ValueError):
                continue
            if next_retry.tzinfo is None:
                next_retry = next_retry.replace(tzinfo=timezone.utc)
            if next_retry <= now:
                ready.append(document.id)
            if len(ready) >= 100:
                break
        return ready


@_get_celery_app().task(name="research_source.union3.reconcile", ignore_result=True)
def reconcile_union3_sources_task() -> dict[str, int]:
    if not _source_reader_enabled():
        return {"redispatched": 0}
    source_ids = asyncio.run(_queued_source_ids())
    for source_id in source_ids:
        enqueue_union3_source(source_id)
    return {"redispatched": len(source_ids)}


__all__ = [
    "enqueue_union3_source",
    "ingest_union3_source_task",
    "reconcile_union3_sources_task",
]
