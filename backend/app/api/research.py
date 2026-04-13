"""Research memory/profile endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.models.database import get_db
from app.models.schemas import ChatSession, User, UserResearchProfile
from app.services.memory_service import memory_service

router = APIRouter(prefix="/api/research", tags=["research"])


class ProfileUpdateRequest(BaseModel):
    research_interests: list[str] | None = None
    expertise_level: str | None = None
    preferred_plotting_style: dict | None = None
    memory_enabled: bool | None = None


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


@router.delete("/memory")
async def delete_research_memory(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await memory_service.delete_all_memory(user.id, db)
    await db.commit()
    return {"deleted": True}
