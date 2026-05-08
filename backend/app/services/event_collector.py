"""Buffered user event tracking for analytics and future training signals."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from collections.abc import Callable
from functools import wraps

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import async_session
from app.models.schemas import UserEvent

logger = logging.getLogger(__name__)

ALLOWED_EVENT_TYPES = {
    "search.query",
    "search.adql",
    "search.filter_applied",
    "search.result_click",
    "ai.message_sent",
    "ai.tool_called",
    "ai.research_mode_started",
    "ai.research_mode_completed",
    "ai.agent_routed",
    "analysis.function_called",
    "analysis.pipeline_run",
    "analysis.anomaly_detected",
    "object.viewed",
    "object.dossier_generated",
    "alert.viewed",
    "alert.watchlist_created",
    "alert.followup_generated",
    "export.latex",
    "export.notebook",
    "export.paper_draft",
    "export.csv",
    "export.html",
    "error.query_failed",
    "error.analysis_failed",
    "error.ai_failed",
    "session.started",
    "session.page_view",
    "session.ended",
    "security.auth_failure",
    "security.forbidden",
    "security.rate_limited",
}


def _coerce_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


class EventCollector:
    def __init__(self, flush_interval: int = 5, flush_size: int = 100):
        self.buffer: list[dict] = []
        self.buffer_lock = asyncio.Lock()
        self.FLUSH_INTERVAL = flush_interval
        self.FLUSH_SIZE = flush_size

    async def track(
        self,
        event_type: str,
        event_data: dict,
        user_id: str | uuid.UUID | None = None,
        session_id: str | uuid.UUID | None = None,
        duration_ms: int | None = None,
        page: str | None = None,
    ) -> None:
        """Add an event to the buffer without blocking the caller on DB IO."""
        if event_type not in ALLOWED_EVENT_TYPES:
            logger.debug("Ignoring unknown event type %s", event_type)
            return

        record = {
            "id": uuid.uuid4(),
            "user_id": _coerce_uuid(user_id),
            "session_id": _coerce_uuid(session_id),
            "event_type": event_type,
            "event_data": event_data,
            "duration_ms": duration_ms,
            "page": page,
        }

        should_flush = False
        async with self.buffer_lock:
            self.buffer.append(record)
            should_flush = len(self.buffer) >= self.FLUSH_SIZE

        if should_flush:
            await self.flush()

    async def flush(self) -> None:
        async with self.buffer_lock:
            if not self.buffer:
                return
            events = self.buffer[:]
            self.buffer.clear()

        async with async_session() as db:
            await db.execute(insert(UserEvent), events)
            await db.commit()


async def periodic_flush(collector: EventCollector, interval: int = 5) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await collector.flush()
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Failed to flush user event buffer: %s", exc)


def track_event(event_type: str) -> Callable:
    """Decorator for async/sync functions that should emit tracking events."""

    def decorator(func: Callable):
        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                started = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                    await event_collector.track(
                        event_type,
                        {
                            "function_name": func.__name__,
                            "success": True,
                            "duration_ms": int((time.perf_counter() - started) * 1000),
                        },
                    )
                    return result
                except Exception as exc:
                    await event_collector.track(
                        event_type,
                        {
                            "function_name": func.__name__,
                            "success": False,
                            "error_msg": str(exc)[:200],
                            "duration_ms": int((time.perf_counter() - started) * 1000),
                        },
                    )
                    raise

            return async_wrapper

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(event_collector.track(
                        event_type,
                        {
                            "function_name": func.__name__,
                            "success": True,
                            "duration_ms": int((time.perf_counter() - started) * 1000),
                        },
                    ))
                except RuntimeError:
                    pass
                return result
            except Exception as exc:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(event_collector.track(
                        event_type,
                        {
                            "function_name": func.__name__,
                            "success": False,
                            "error_msg": str(exc)[:200],
                            "duration_ms": int((time.perf_counter() - started) * 1000),
                        },
                    ))
                except RuntimeError:
                    pass
                raise

        return sync_wrapper

    return decorator


async def get_event_stats(start_time=None, end_time=None, db: AsyncSession | None = None) -> dict:
    async def _query(session: AsyncSession) -> dict:
        query = select(
            UserEvent.event_type,
            func.count(UserEvent.id).label("count"),
            func.avg(UserEvent.duration_ms).label("avg_duration"),
        )
        if start_time is not None:
            query = query.where(UserEvent.timestamp >= start_time)
        if end_time is not None:
            query = query.where(UserEvent.timestamp <= end_time)
        query = query.group_by(UserEvent.event_type).order_by(func.count(UserEvent.id).desc())
        rows = (await db.execute(query)).all()
        return {
            "event_counts": [
                {
                    "event_type": row.event_type,
                    "count": int(row.count),
                    "avg_duration_ms": float(row.avg_duration) if row.avg_duration is not None else None,
                }
                for row in rows
            ]
        }

    if db is not None:
        return await _query(db)

    async with async_session() as session:
        return await _query(session)


event_collector = EventCollector()
