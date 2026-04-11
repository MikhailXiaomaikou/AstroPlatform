"""Celery tasks for periodic ZTF alert ingestion."""

import asyncio
import logging

import sqlalchemy
from celery import shared_task

from app.config import settings

logger = logging.getLogger(__name__)


def _create_sync_engine():
    """Create a synchronous SQLAlchemy engine from the configured database URL."""
    sync_url = settings.database_url
    if "+aiosqlite" in sync_url:
        sync_url = sync_url.replace("+aiosqlite", "")
    elif "+asyncpg" in sync_url:
        sync_url = sync_url.replace("+asyncpg", "+psycopg2")
    return sqlalchemy.create_engine(sync_url)


@shared_task(name="alerts.ingest")
def ingest_alerts_task():
    """Periodic task to ingest new alerts from Lasair.

    Runs the async AlertIngestionService in a sync context suitable for Celery.
    Uses a synchronous SQLAlchemy engine (same pattern as scheduler_worker).
    """
    from app.services.alert_ingestion import AlertIngestionService

    logger.info("Starting scheduled alert ingestion")

    service = AlertIngestionService()

    async def _run():
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        async_engine = create_async_engine(settings.database_url, echo=False)
        async_session = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

        alerts = await service.fetch_recent(hours=24, limit=200)
        if not alerts:
            logger.info("No new alerts fetched from Lasair")
            return 0

        async with async_session() as db:
            count = await service.ingest(alerts, db)
            logger.info("Ingested %d new alerts", count)
            return count

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(_run())
        finally:
            loop.close()
        return {"ingested": result}
    except Exception as exc:
        logger.error("Alert ingestion task failed: %s", exc)
        return {"error": str(exc)}
