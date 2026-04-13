"""Shared chat session collaboration endpoints."""

from __future__ import annotations

import secrets
import uuid
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_optional_user
from app.models.database import get_db
from app.models.schemas import (
    ChatSession,
    SessionComment,
    SessionFork,
    SessionSnapshot,
    SharedSession,
    User,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
shared_router = APIRouter(prefix="/api/shared", tags=["shared-sessions"])


class ShareSessionRequest(BaseModel):
    access_level: str = "view"
    expires_hours: int | None = None


class SessionCommentRequest(BaseModel):
    target_type: str = "general"
    target_id: str | None = None
    content: str
    parent_id: str | None = None


class SnapshotRequest(BaseModel):
    name: str


def _serialize_session(session: ChatSession) -> dict:
    return {
        "id": str(session.id),
        "title": session.title,
        "messages": deepcopy(session.messages or []),
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


def _coerce_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _require_owned_session(session_id: str, user: User, db: AsyncSession) -> ChatSession:
    try:
        sid = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid session ID") from exc
    session = (
        await db.execute(
            select(ChatSession).where(ChatSession.id == sid, ChatSession.user_id == user.id)
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


async def _load_shared_session(token: str, db: AsyncSession) -> tuple[SharedSession, ChatSession]:
    shared = (
        await db.execute(select(SharedSession).where(SharedSession.share_token == token))
    ).scalar_one_or_none()
    if shared is None:
        raise HTTPException(status_code=404, detail="Shared session not found")
    expires_at = _coerce_utc(shared.expires_at)
    if expires_at and expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="This share link has expired")
    session = (
        await db.execute(select(ChatSession).where(ChatSession.id == shared.session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Source session not found")
    return shared, session


@router.post("/{session_id}/share")
async def create_session_share(
    session_id: str,
    req: ShareSessionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if req.access_level not in {"view", "fork", "comment"}:
        raise HTTPException(status_code=400, detail="access_level must be view, fork, or comment")
    session = await _require_owned_session(session_id, user, db)
    token = secrets.token_urlsafe(24)
    expires_at = None
    if req.expires_hours:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=req.expires_hours)

    shared = SharedSession(
        session_id=session.id,
        owner_id=user.id,
        share_token=token,
        access_level=req.access_level,
        expires_at=expires_at,
    )
    db.add(shared)
    await db.commit()
    await db.refresh(shared)

    base_url = os.getenv("PUBLIC_APP_URL", "https://app.standardastro.com").rstrip("/")
    return {
        "id": str(shared.id),
        "share_url": f"{base_url}/shared/{token}",
        "share_token": token,
        "access_level": shared.access_level,
        "expires_at": shared.expires_at.isoformat() if shared.expires_at else None,
    }


@router.get("/{session_id}/shares")
async def list_session_shares(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _require_owned_session(session_id, user, db)
    shares = (
        await db.execute(
            select(SharedSession)
            .where(SharedSession.session_id == session.id)
            .order_by(SharedSession.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": str(share.id),
            "share_token": share.share_token,
            "access_level": share.access_level,
            "expires_at": share.expires_at.isoformat() if share.expires_at else None,
            "created_at": share.created_at.isoformat() if share.created_at else None,
        }
        for share in shares
    ]


@router.delete("/{session_id}/share/{share_id}")
async def revoke_session_share(
    session_id: str,
    share_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _require_owned_session(session_id, user, db)
    try:
        share_uuid = uuid.UUID(share_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid share ID") from exc
    share = (
        await db.execute(
            select(SharedSession).where(
                SharedSession.id == share_uuid,
                SharedSession.session_id == session.id,
                SharedSession.owner_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found")
    await db.delete(share)
    await db.commit()
    return {"deleted": True}


@shared_router.get("/{token}")
async def get_shared_session(
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    shared, session = await _load_shared_session(token, db)
    comments = (
        await db.execute(
            select(SessionComment).where(SessionComment.session_id == session.id).order_by(SessionComment.created_at.asc())
        )
    ).scalars().all()
    return {
        "share": {
            "access_level": shared.access_level,
            "expires_at": shared.expires_at.isoformat() if shared.expires_at else None,
        },
        "session": _serialize_session(session),
        "comments": [
            {
                "id": str(comment.id),
                "user_id": str(comment.user_id),
                "target_type": comment.target_type,
                "target_id": comment.target_id,
                "content": comment.content,
                "parent_id": str(comment.parent_id) if comment.parent_id else None,
                "created_at": comment.created_at.isoformat() if comment.created_at else None,
            }
            for comment in comments
        ],
        "can_fork": bool(user and shared.access_level in {"fork", "comment"}),
        "can_comment": bool(user and shared.access_level == "comment"),
    }


@shared_router.post("/{token}/fork")
async def fork_shared_session(
    token: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    shared, session = await _load_shared_session(token, db)
    if shared.access_level not in {"fork", "comment"}:
        raise HTTPException(status_code=403, detail="This shared session is view-only")

    forked = ChatSession(
        user_id=user.id,
        title=f"Fork of {session.title}",
        messages=deepcopy(session.messages or []),
    )
    db.add(forked)
    await db.flush()
    db.add(SessionFork(session_id=forked.id, forked_from=session.id, user_id=user.id))
    await db.commit()
    await db.refresh(forked)
    return {"id": str(forked.id), "forked_from": str(session.id)}


@router.get("/{session_id}/comments")
async def list_session_comments(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _require_owned_session(session_id, user, db)
    comments = (
        await db.execute(
            select(SessionComment).where(SessionComment.session_id == session.id).order_by(SessionComment.created_at.asc())
        )
    ).scalars().all()
    return [
        {
            "id": str(comment.id),
            "user_id": str(comment.user_id),
            "target_type": comment.target_type,
            "target_id": comment.target_id,
            "content": comment.content,
            "parent_id": str(comment.parent_id) if comment.parent_id else None,
            "created_at": comment.created_at.isoformat() if comment.created_at else None,
        }
        for comment in comments
    ]


@router.post("/{session_id}/comments")
async def add_session_comment(
    session_id: str,
    req: SessionCommentRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _require_owned_session(session_id, user, db)
    parent_uuid = None
    if req.parent_id:
        try:
            parent_uuid = uuid.UUID(req.parent_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid parent ID") from exc
    comment = SessionComment(
        session_id=session.id,
        user_id=user.id,
        target_type=req.target_type,
        target_id=req.target_id,
        content=req.content,
        parent_id=parent_uuid,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return {
        "id": str(comment.id),
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
    }


@shared_router.post("/{token}/comments")
async def add_shared_session_comment(
    token: str,
    req: SessionCommentRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    shared, session = await _load_shared_session(token, db)
    if shared.access_level != "comment":
        raise HTTPException(status_code=403, detail="Comment access is not enabled for this share link")
    parent_uuid = None
    if req.parent_id:
        try:
            parent_uuid = uuid.UUID(req.parent_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid parent ID") from exc
    comment = SessionComment(
        session_id=session.id,
        user_id=user.id,
        target_type=req.target_type,
        target_id=req.target_id,
        content=req.content,
        parent_id=parent_uuid,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return {"id": str(comment.id)}


@router.delete("/{session_id}/comments/{comment_id}")
async def delete_session_comment(
    session_id: str,
    comment_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _require_owned_session(session_id, user, db)
    try:
        comment_uuid = uuid.UUID(comment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid comment ID") from exc
    comment = (
        await db.execute(
            select(SessionComment).where(
                SessionComment.id == comment_uuid,
                SessionComment.session_id == session.id,
                SessionComment.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail="Comment not found")
    await db.delete(comment)
    await db.commit()
    return {"deleted": True}


@router.post("/{session_id}/snapshots")
async def create_snapshot(
    session_id: str,
    req: SnapshotRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _require_owned_session(session_id, user, db)
    snapshot = SessionSnapshot(
        session_id=session.id,
        name=req.name,
        snapshot_data=_serialize_session(session),
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    return {"id": str(snapshot.id), "name": snapshot.name}


@router.get("/{session_id}/snapshots")
async def list_snapshots(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _require_owned_session(session_id, user, db)
    snapshots = (
        await db.execute(
            select(SessionSnapshot).where(SessionSnapshot.session_id == session.id).order_by(SessionSnapshot.created_at.desc())
        )
    ).scalars().all()
    return [
        {
            "id": str(snapshot.id),
            "name": snapshot.name,
            "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
        }
        for snapshot in snapshots
    ]


@router.post("/{session_id}/snapshots/{snapshot_id}/restore")
async def restore_snapshot(
    session_id: str,
    snapshot_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _require_owned_session(session_id, user, db)
    try:
        snapshot_uuid = uuid.UUID(snapshot_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid snapshot ID") from exc
    snapshot = (
        await db.execute(
            select(SessionSnapshot).where(SessionSnapshot.id == snapshot_uuid, SessionSnapshot.session_id == session.id)
        )
    ).scalar_one_or_none()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    snapshot_data = snapshot.snapshot_data or {}
    session.title = snapshot_data.get("title", session.title)
    session.messages = deepcopy(snapshot_data.get("messages", session.messages or []))
    await db.commit()
    await db.refresh(session)
    return {"restored": True, "session": _serialize_session(session)}


@router.get("/{session_id}/snapshots/diff")
async def diff_snapshots(
    session_id: str,
    a: str = Query(...),
    b: str = Query(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = await _require_owned_session(session_id, user, db)
    try:
        snap_a = uuid.UUID(a)
        snap_b = uuid.UUID(b)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid snapshot ID") from exc
    snapshots = (
        await db.execute(
            select(SessionSnapshot).where(SessionSnapshot.id.in_([snap_a, snap_b]), SessionSnapshot.session_id == session.id)
        )
    ).scalars().all()
    if len(snapshots) != 2:
        raise HTTPException(status_code=404, detail="One or both snapshots were not found")
    mapping = {snapshot.id: snapshot for snapshot in snapshots}
    left = mapping[snap_a].snapshot_data or {}
    right = mapping[snap_b].snapshot_data or {}

    left_messages = left.get("messages", [])
    right_messages = right.get("messages", [])
    changes = []
    if left.get("title") != right.get("title"):
        changes.append({"type": "modified", "field": "title", "from": left.get("title"), "to": right.get("title")})
    if len(left_messages) != len(right_messages):
        delta = len(right_messages) - len(left_messages)
        changes.append({"type": "modified", "field": "message_count", "from": len(left_messages), "to": len(right_messages), "delta": delta})
    min_len = min(len(left_messages), len(right_messages))
    for idx in range(min_len):
        if left_messages[idx] != right_messages[idx]:
            changes.append({"type": "modified", "field": f"message_{idx}", "from": left_messages[idx], "to": right_messages[idx]})
            if len(changes) >= 25:
                break
    added_messages = max(0, len(right_messages) - len(left_messages))
    removed_messages = max(0, len(left_messages) - len(right_messages))

    if len(right_messages) > min_len:
        for msg in right_messages[min_len:min_len + 10]:
            changes.append({"type": "added", "field": "message", "to": msg})
    if len(left_messages) > min_len:
        for msg in left_messages[min_len:min_len + 10]:
            changes.append({"type": "removed", "field": "message", "from": msg})
    return {
        "changes": changes,
        "added_messages": added_messages,
        "removed_messages": removed_messages,
        "updated_title": left.get("title") != right.get("title"),
        "titles": {"a": left.get("title"), "b": right.get("title")},
    }
