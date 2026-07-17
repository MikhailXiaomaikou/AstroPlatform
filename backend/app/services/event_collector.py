"""Buffered user event tracking for analytics and future training signals."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from functools import wraps

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import async_session
from app.models.claim_audit_records import PrivacyPreference
from app.models.schemas import InferenceLog, UserEvent

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
    "claim_audit.started",
    "claim_audit.completed",
    "claim_audit.evidence_viewed",
    "claim_audit.failed",
    "claim_audit.feedback",
    "evidence_pack.exported",
}

_CLAIM_AUDIT_EVENT_FIELDS = {
    "outcome_bucket",
    "duration_bucket",
    "tool_count_bucket",
    "source_kind",
    "execution_mode",
    "error_class",
    "retryable",
    "usefulness_rating",
    "strictness_rating",
}
_GENERIC_EVENT_FIELDS = {
    "agent_name",
    "backend",
    "count_bucket",
    "duration_bucket",
    "error_class",
    "execution_mode",
    "function_name",
    "model",
    "outcome_bucket",
    "provider",
    "result_count_bucket",
    "retryable",
    "source_kind",
    "status_code",
    "success",
    "tool_count_bucket",
    "tool_name",
    "usefulness_rating",
    "strictness_rating",
}
_SENSITIVE_FIELD_FRAGMENTS = (
    "arg",
    "claim",
    "content",
    "doi",
    "error_msg",
    "exception",
    "input",
    "keyword",
    "message",
    "output",
    "paper",
    "param",
    "prompt",
    "query",
    "result",
    "text",
    "title",
    "tool_result",
    "url",
)
_BUCKET_VALUES = {
    "count_bucket": frozenset({"0", "1-3", "4+", "unknown"}),
    "duration_bucket": frozenset(
        {"under_1m", "1m_to_10m", "over_10m", "unknown"}
    ),
    "result_count_bucket": frozenset({"0", "1-3", "4+", "unknown"}),
    "tool_count_bucket": frozenset({"0", "1-3", "4+", "unknown"}),
}
_CATEGORICAL_VALUES = {
    "execution_mode": frozenset(
        {
            "audit_only",
            "execute_registered",
            "external_cobaya",
            "full_likelihood",
            "compressed_gaussian",
            "config_only",
            "unknown",
        }
    ),
    "outcome_bucket": frozenset(
        {
            "supported",
            "withheld",
            "capability_gap",
            "failed_retryable",
            "failed_final",
            "cancelled",
            "completed",
            "failed",
            "success",
            "error",
            "unknown",
        }
    ),
    "source_kind": frozenset({"doi", "arxiv", "bibcode", "url", "unknown"}),
}
_SAFE_HTTP_STATUS_CODES = frozenset(
    {200, 201, 202, 204, 400, 401, 403, 404, 409, 410, 422, 429, 500, 502, 503, 504}
)
_SAFE_PRODUCT_PAGES = frozenset(
    {
        "/",
        "/account",
        "/alerts",
        "/anomalies",
        "/auth",
        "/bot",
        "/chat",
        "/claim-audit",
        "/help",
        "/observations",
        "/papers",
        "/research",
        "/settings",
        "/shared",
        "/team",
    }
)


def _coarse_error_class(value: object) -> str | None:
    """Map an error label to a finite product bucket, never retain its text."""

    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().lower()
    if "timeout" in normalized:
        return "timeout"
    if any(token in normalized for token in ("network", "connection", "oserror")):
        return "network"
    if any(token in normalized for token in ("missing", "not_found", "unavailable")):
        return "unavailable"
    if any(token in normalized for token in ("input", "argument", "validation", "value")):
        return "invalid_input"
    if any(
        token in normalized
        for token in ("integrity", "checksum", "signature", "scientific")
    ):
        return "integrity"
    return "internal"


def _safe_event_value(key: str, value: object) -> object | None:
    """Return only finite product categories that cannot carry research text."""

    if key in {"retryable", "success"}:
        return value if isinstance(value, bool) else None
    if key in _BUCKET_VALUES and isinstance(value, str):
        normalized = value.strip().lower()
        return normalized if normalized in _BUCKET_VALUES[key] else None
    if key in _CATEGORICAL_VALUES and isinstance(value, str):
        normalized = value.strip().lower()
        return normalized if normalized in _CATEGORICAL_VALUES[key] else None
    if key == "error_class":
        return _coarse_error_class(value)
    if key == "status_code" and isinstance(value, int) and not isinstance(value, bool):
        return value if value in _SAFE_HTTP_STATUS_CODES else None
    if key in {"usefulness_rating", "strictness_rating"}:
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5:
            return value
    # Free-form identifiers (tool/model/provider/function/agent/backend) are
    # intentionally discarded.  The public tracking endpoint must not be a
    # covert channel for claims, paper titles, DOI values, or tool arguments.
    return None


def scrub_event_data(event_type: str, event_data: dict | None) -> dict:
    """Return bounded metadata and remove research content at one choke point."""

    if not isinstance(event_data, dict):
        return {}
    strict_claim_event = event_type.startswith("claim_audit.") or event_type == "evidence_pack.exported"
    cleaned: dict = {}
    for raw_key, value in event_data.items():
        key = str(raw_key)[:64]
        lowered = key.lower()
        allowed_fields = (
            _CLAIM_AUDIT_EVENT_FIELDS if strict_claim_event else _GENERIC_EVENT_FIELDS
        )
        if key not in allowed_fields:
            continue
        if any(fragment in lowered for fragment in _SENSITIVE_FIELD_FRAGMENTS):
            continue
        safe_value = _safe_event_value(key, value)
        if safe_value is not None:
            cleaned[key] = safe_value
    return cleaned


def scrub_page(page: str | None) -> str | None:
    """Keep only static product-route labels, never URLs or record ids."""

    if not isinstance(page, str):
        return None
    normalized = page.strip().lower()
    if normalized not in _SAFE_PRODUCT_PAGES:
        return None
    return normalized


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
        self._consent_cache: dict[uuid.UUID, tuple[float, bool]] = {}
        self._consent_cache_seconds = 60.0

    async def _has_consent(
        self,
        user_id: uuid.UUID | None,
        consent_verified: bool | None,
    ) -> bool:
        if consent_verified is not None:
            return consent_verified
        if user_id is None:
            return False
        cached = self._consent_cache.get(user_id)
        now = time.monotonic()
        if cached is not None and now - cached[0] < self._consent_cache_seconds:
            return cached[1]
        try:
            async with async_session() as db:
                allowed = bool(
                    await db.scalar(
                        select(PrivacyPreference.analytics_enabled).where(
                            PrivacyPreference.user_id == user_id
                        )
                    )
                )
        except Exception as exc:
            logger.debug("Analytics consent lookup failed closed: %s", exc)
            allowed = False
        self._consent_cache[user_id] = (now, allowed)
        return allowed

    async def invalidate_consent(self, user_id: str | uuid.UUID) -> None:
        parsed = _coerce_uuid(user_id)
        if parsed is None:
            return
        self._consent_cache.pop(parsed, None)
        async with self.buffer_lock:
            self.buffer = [row for row in self.buffer if row.get("user_id") != parsed]

    async def track(
        self,
        event_type: str,
        event_data: dict,
        user_id: str | uuid.UUID | None = None,
        session_id: str | uuid.UUID | None = None,
        duration_ms: int | None = None,
        page: str | None = None,
        consent_verified: bool | None = None,
    ) -> None:
        """Add an event to the buffer without blocking the caller on DB IO."""
        if event_type not in ALLOWED_EVENT_TYPES:
            logger.debug("Ignoring unknown event type %s", event_type)
            return

        parsed_user_id = _coerce_uuid(user_id)
        if not await self._has_consent(parsed_user_id, consent_verified):
            return

        record = {
            "id": uuid.uuid4(),
            "user_id": parsed_user_id,
            # A browser-supplied UUID is not an authenticated product category
            # and can encode arbitrary research content. Keep the argument for
            # API compatibility but never persist it.
            "session_id": None,
            "event_type": event_type,
            "event_data": scrub_event_data(event_type, event_data),
            "duration_ms": (
                max(0, min(int(duration_ms), 86_400_000))
                if isinstance(duration_ms, int)
                else None
            ),
            "page": scrub_page(page),
        }

        should_flush = False
        async with self.buffer_lock:
            self.buffer.append(record)
            should_flush = len(self.buffer) >= self.FLUSH_SIZE

        if should_flush:
            # Analytics is best-effort: an inline flush failure (schema
            # missing, DB down) must never take down the user request that
            # happened to be the FLUSH_SIZE-th event — same defensive
            # contract periodic_flush already has.
            try:
                await self.flush()
            except Exception as exc:
                logger.warning("Failed to flush user event buffer inline: %s", exc)

    async def flush(self) -> None:
        async with self.buffer_lock:
            if not self.buffer:
                return
            events = self.buffer[:]
            self.buffer.clear()

        async with async_session() as db:
            # Consent is rechecked in the same transaction that writes the
            # rows.  Locking the preference rows serializes this insert with a
            # concurrent opt-out: either this commit happens first and the
            # opt-out deletes it, or the opt-out happens first and this batch
            # is dropped.  Cached/remote-process consent can never be the final
            # authority for persistence.
            user_ids = {
                row["user_id"] for row in events if row.get("user_id") is not None
            }
            if not user_ids:
                return
            allowed_user_ids = set(
                (
                    await db.execute(
                        select(PrivacyPreference.user_id)
                        .where(
                            PrivacyPreference.user_id.in_(user_ids),
                            PrivacyPreference.analytics_enabled.is_(True),
                        )
                        .with_for_update()
                    )
                ).scalars()
            )
            permitted = [
                row for row in events if row.get("user_id") in allowed_user_ids
            ]
            if permitted:
                await db.execute(insert(UserEvent), permitted)
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


async def purge_expired_product_events(
    *,
    retention_days: int,
    db: AsyncSession | None = None,
) -> int:
    """Delete product analytics past the configured retention window."""

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    async def _purge(session: AsyncSession) -> int:
        result = await session.execute(delete(UserEvent).where(UserEvent.timestamp < cutoff))
        await session.commit()
        return int(result.rowcount or 0)

    if db is not None:
        return await _purge(db)
    async with async_session() as session:
        return await _purge(session)


async def purge_expired_inference_logs(
    *,
    retention_days: int,
    db: AsyncSession | None = None,
) -> int:
    """Delete coarse inference operations metrics past the same retention cap."""

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    async def _purge(session: AsyncSession) -> int:
        result = await session.execute(
            delete(InferenceLog).where(InferenceLog.timestamp < cutoff)
        )
        await session.commit()
        return int(result.rowcount or 0)

    if db is not None:
        return await _purge(db)
    async with async_session() as session:
        return await _purge(session)
