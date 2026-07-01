"""Research memory/profile endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.models.database import get_db
from app.models.schemas import ChatSession, User, UserEvent
from app.services.memory_service import memory_service

router = APIRouter(prefix="/api/research", tags=["research"])

UPDATE_RECORD_EVENT = "research.update_record"


class ProfileUpdateRequest(BaseModel):
    research_interests: list[str] | None = None
    expertise_level: str | None = None
    preferred_plotting_style: dict | None = None
    memory_enabled: bool | None = None


class UpdateRecordCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(default="", max_length=5000)
    tags: list[str] | None = None
    status: str = Field(default="note", max_length=40)


class UpdateRecordPatchRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    body: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = None
    status: str | None = Field(default=None, max_length=40)


def _clean_update_tags(tags: list[str] | None) -> list[str]:
    if not tags:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = str(raw).strip()[:40]
        key = tag.lower()
        if tag and key not in seen:
            cleaned.append(tag)
            seen.add(key)
        if len(cleaned) >= 12:
            break
    return cleaned


def _serialize_update_record(row: UserEvent, user: User) -> dict:
    data = row.event_data if isinstance(row.event_data, dict) else {}
    return {
        "id": str(row.id),
        "title": str(data.get("title") or "Untitled update"),
        "body": str(data.get("body") or ""),
        "tags": data.get("tags") if isinstance(data.get("tags"), list) else [],
        "status": str(data.get("status") or "note"),
        "created_at": row.timestamp.isoformat() if row.timestamp else None,
        "updated_at": data.get("updated_at") if isinstance(data.get("updated_at"), str) else None,
        "owner_locked": True,
        "locked_user_id": str(user.id),
    }


@router.get("/profile")
async def get_research_profile(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    profile = await memory_service.ensure_profile(user.id, db)
    await db.commit()
    return {
        "id": str(profile.id),
        "user_id": str(profile.user_id),
        "memory_enabled": profile.memory_enabled,
        "frequently_queried_objects": profile.frequently_queried_objects or [],
        "preferred_databases": profile.preferred_databases or [],
        "preferred_analysis_methods": profile.preferred_analysis_methods or [],
        "research_interests": profile.research_interests or [],
        "expertise_level": profile.expertise_level,
        "past_hypotheses": profile.past_hypotheses or [],
        "preferred_plotting_style": profile.preferred_plotting_style or {},
    }


@router.put("/profile")
async def update_research_profile(
    req: ProfileUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    profile = await memory_service.ensure_profile(user.id, db)
    if req.research_interests is not None:
        profile.research_interests = req.research_interests
    if req.expertise_level is not None:
        profile.expertise_level = req.expertise_level
    if req.preferred_plotting_style is not None:
        profile.preferred_plotting_style = req.preferred_plotting_style
    if req.memory_enabled is not None:
        profile.memory_enabled = req.memory_enabled
    await db.commit()
    return {"saved": True}


@router.post("/profile/refresh")
async def refresh_research_profile(
    session_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if session_id:
        try:
            sid = uuid.UUID(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid session ID") from exc
        session = (
            await db.execute(select(ChatSession).where(ChatSession.id == sid, ChatSession.user_id == user.id))
        ).scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        await memory_service.refresh_session_memory(user.id, session.id, db)
        await db.commit()
        return {"refreshed": True, "session_id": str(session.id)}

    sessions = (
        await db.execute(
            select(ChatSession).where(ChatSession.user_id == user.id).order_by(ChatSession.updated_at.desc()).limit(20)
        )
    ).scalars().all()
    for session in sessions:
        await memory_service.refresh_session_memory(user.id, session.id, db)
    await db.commit()
    return {"refreshed": True, "session_count": len(sessions)}


@router.get("/history")
async def list_research_history(
    q: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items = await memory_service.list_history(user.id, db, query=q)
    return items


@router.get("/updates")
async def list_update_records(
    q: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List update records locked to the current account."""
    rows = (
        await db.execute(
            select(UserEvent)
            .where(UserEvent.user_id == user.id, UserEvent.event_type == UPDATE_RECORD_EVENT)
            .order_by(UserEvent.timestamp.desc())
            .limit(100)
        )
    ).scalars().all()
    items = [_serialize_update_record(row, user) for row in rows]
    if q:
        needle = q.lower()
        items = [
            item for item in items
            if needle in item["title"].lower()
            or needle in item["body"].lower()
            or any(needle in str(tag).lower() for tag in item["tags"])
        ]
    return items


@router.post("/updates")
async def create_update_record(
    req: UpdateRecordCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a private update record for the current account only."""
    now = datetime.now(timezone.utc).isoformat()
    record = UserEvent(
        user_id=user.id,
        event_type=UPDATE_RECORD_EVENT,
        event_data={
            "title": req.title.strip(),
            "body": req.body.strip(),
            "tags": _clean_update_tags(req.tags),
            "status": req.status.strip() or "note",
            "updated_at": now,
        },
        page="research-updates",
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return _serialize_update_record(record, user)


async def _require_owned_update_record(record_id: str, user: User, db: AsyncSession) -> UserEvent:
    try:
        rid = uuid.UUID(record_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid update record ID") from exc
    record = (
        await db.execute(
            select(UserEvent).where(
                UserEvent.id == rid,
                UserEvent.user_id == user.id,
                UserEvent.event_type == UPDATE_RECORD_EVENT,
            )
        )
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Update record not found")
    return record


@router.patch("/updates/{record_id}")
async def update_update_record(
    record_id: str,
    req: UpdateRecordPatchRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    record = await _require_owned_update_record(record_id, user, db)
    data = dict(record.event_data) if isinstance(record.event_data, dict) else {}
    if req.title is not None:
        data["title"] = req.title.strip()
    if req.body is not None:
        data["body"] = req.body.strip()
    if req.tags is not None:
        data["tags"] = _clean_update_tags(req.tags)
    if req.status is not None:
        data["status"] = req.status.strip() or "note"
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    record.event_data = data
    await db.commit()
    await db.refresh(record)
    return _serialize_update_record(record, user)


@router.delete("/updates/{record_id}")
async def delete_update_record(
    record_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    record = await _require_owned_update_record(record_id, user, db)
    await db.delete(record)
    await db.commit()
    return {"deleted": True}


@router.delete("/memory")
async def delete_research_memory(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await memory_service.delete_all_memory(user.id, db)
    await db.execute(delete(UserEvent).where(UserEvent.user_id == user.id, UserEvent.event_type == UPDATE_RECORD_EVENT))
    await db.commit()
    return {"deleted": True}
