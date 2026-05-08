"""User event tracking and analytics endpoints."""

from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin_any
from app.auth import get_optional_user
from app.models.database import get_db
from app.models.schemas import User, UserEvent
from app.rate_limit import limiter
from app.services.event_collector import ALLOWED_EVENT_TYPES, event_collector, get_event_stats

router = APIRouter(prefix="/api/events", tags=["events"])
admin_router = APIRouter(prefix="/api/admin/events", tags=["admin-events"])


class TrackEventRequest(BaseModel):
    event_type: str
    event_data: dict = Field(default_factory=dict)
    session_id: str | None = None
    page: str | None = None
    duration_ms: int | None = None


def _require_admin(user: User) -> User:
    admin_usernames = {
        username.strip()
        for username in os.getenv("ADMIN_USERNAMES", "").split(",")
        if username.strip()
    }
    if user.username in admin_usernames or user.subscription_tier in {"admin", "institution"}:
        return user
    raise HTTPException(status_code=403, detail="Admin access required")


def _parse_period(period: str) -> timedelta:
    normalized = period.strip().lower()
    if normalized.endswith("d"):
        return timedelta(days=int(normalized[:-1]))
    if normalized.endswith("h"):
        return timedelta(hours=int(normalized[:-1]))
    raise HTTPException(status_code=400, detail="Unsupported period format")


@router.post("/track")
@limiter.limit("100/minute")
async def track_event_endpoint(
    request: Request,
    payload: TrackEventRequest,
    user: User | None = Depends(get_optional_user),
):
    if payload.event_type not in ALLOWED_EVENT_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported event type")

    await event_collector.track(
        event_type=payload.event_type,
        event_data=payload.event_data,
        user_id=user.id if user else None,
        session_id=payload.session_id,
        duration_ms=payload.duration_ms,
        page=payload.page,
    )
    return {"tracked": True}


@admin_router.get("/recent")
async def recent_events(
    hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin_any),
):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        await db.execute(
            select(UserEvent)
            .where(UserEvent.timestamp >= cutoff)
            .order_by(desc(UserEvent.timestamp))
            .limit(500)
        )
    ).scalars().all()
    return [
        {
            "id": str(row.id),
            "user_id": str(row.user_id) if row.user_id else None,
            "session_id": str(row.session_id) if row.session_id else None,
            "event_type": row.event_type,
            "event_data": row.event_data or {},
            "page": row.page,
            "duration_ms": row.duration_ms,
            "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        }
        for row in rows
    ]


@admin_router.get("/stats")
async def event_stats(
    period: str = Query("7d"),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin_any),
):
    delta = _parse_period(period)
    cutoff = datetime.now(timezone.utc) - delta
    stats = await get_event_stats(start_time=cutoff, db=db)
    return {
        "period": period,
        "start_time": cutoff.isoformat(),
        **stats,
    }


@admin_router.get("/export")
async def export_events(
    start: datetime = Query(...),
    end: datetime = Query(...),
    format: str = Query("csv"),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin_any),
):
    if format != "csv":
        raise HTTPException(status_code=400, detail="Only csv export is supported")

    rows = (
        await db.execute(
            select(UserEvent)
            .where(UserEvent.timestamp >= start, UserEvent.timestamp <= end)
            .order_by(UserEvent.timestamp.asc())
        )
    ).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "event_type", "user_id", "session_id", "page", "duration_ms", "event_data"])
    for row in rows:
        writer.writerow([
            row.timestamp.isoformat() if row.timestamp else "",
            row.event_type,
            str(row.user_id) if row.user_id else "",
            str(row.session_id) if row.session_id else "",
            row.page or "",
            row.duration_ms or "",
            row.event_data or {},
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="standard_astro_events.csv"'},
    )
