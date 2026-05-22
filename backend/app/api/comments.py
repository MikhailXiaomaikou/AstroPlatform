"""Public comments section (below the landing page).

No login required — any visitor can submit with a display name and content;
admins use X-Admin-Secret to delete.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.api.auth import _require_admin
from app.models.database import get_db
from app.models.schemas import Comment
from app.rate_limit import get_client_ip, limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/comments", tags=["comments"])

# The admin desktop HTML runs via file:// (Origin: null) and accesses the API
# through the _NULL_ORIGIN_ALLOWED_PREFIXES whitelist. The /api/comments public
# endpoint has been removed from that whitelist — public endpoints only accept
# requests with a real Origin. Admin "list all / delete" routes use the
# /api/admin/comments router below (/api/admin/ remains in the
# _NULL_ORIGIN_ALLOWED_PREFIXES whitelist in main.py).
admin_router = APIRouter(prefix="/api/admin/comments", tags=["admin-comments"])


# Content length limits (keep in sync with the frontend; update both if either changes).
_NAME_MIN = 1
_NAME_MAX = 40
_CONTENT_MIN = 2
_CONTENT_MAX = 500


class CommentCreateRequest(BaseModel):
    author_name: str = Field(..., min_length=_NAME_MIN, max_length=_NAME_MAX)
    content: str = Field(..., min_length=_CONTENT_MIN, max_length=_CONTENT_MAX)

    @field_validator("author_name", "content")
    @classmethod
    def _strip_and_check(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("must not be blank after trimming whitespace")
        return s


class CommentPublicView(BaseModel):
    id: str
    author_name: str
    content: str
    created_at: str  # ISO 8601

    @classmethod
    def from_orm(cls, c: Comment) -> "CommentPublicView":
        return cls(
            id=str(c.id),
            author_name=c.author_name,
            content=c.content,
            created_at=c.created_at.isoformat() if c.created_at else "",
        )


class CommentListResponse(BaseModel):
    comments: list[CommentPublicView]
    total: int
    has_more: bool


# ── public read ──

@router.get("", response_model=CommentListResponse)
async def list_comments(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> CommentListResponse:
    """Return visible comments in reverse chronological order. No auth required."""
    # total count (visible only)
    from sqlalchemy import func as sa_func
    count_stmt = select(sa_func.count(Comment.id)).where(Comment.is_visible == True)  # noqa: E712
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(Comment)
        .where(Comment.is_visible == True)  # noqa: E712
        .order_by(desc(Comment.created_at))
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return CommentListResponse(
        comments=[CommentPublicView.from_orm(c) for c in rows],
        total=int(total),
        has_more=(offset + len(rows)) < int(total),
    )


# ── public write + rate limit ──

@router.post("", response_model=CommentPublicView, status_code=201)
@limiter.limit("3/minute")
async def create_comment(
    request: Request,
    req: CommentCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> CommentPublicView:
    """Any visitor can post a comment. Rate-limited to 3/minute per IP to
    prevent spam.

    Pydantic already enforces length; here we additionally:
    - Re-validate length after trimming (defensive)
    - Record the IP in client_ip (server-side log only, never returned)
    """
    name = req.author_name.strip()
    content = req.content.strip()
    if not (_NAME_MIN <= len(name) <= _NAME_MAX):
        raise HTTPException(status_code=400, detail="author_name length out of range")
    if not (_CONTENT_MIN <= len(content) <= _CONTENT_MAX):
        raise HTTPException(status_code=400, detail="content length out of range")

    # Record the IP (request.client.host may be the proxy IP behind Render;
    # prefer the first entry in X-Forwarded-For).
    client_ip = get_client_ip(request)

    comment = Comment(
        id=uuid.uuid4(),
        author_name=name,
        content=content,
        is_visible=True,
        client_ip=client_ip,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)

    logger.info("comment created: id=%s name=%s ip=%s content_len=%d",
                comment.id, name, client_ip, len(content))

    return CommentPublicView.from_orm(comment)


# ── admin soft-delete ──

@router.delete("/{comment_id}", status_code=204)
async def delete_comment(
    request: Request,
    comment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Admin soft-delete — sets is_visible=False; the row is kept for audit purposes. Requires X-Admin-Secret."""
    await _require_admin(request)

    try:
        uuid_obj = uuid.UUID(comment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid comment id (not a UUID)")

    stmt = select(Comment).where(Comment.id == uuid_obj)
    comment = (await db.execute(stmt)).scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail="comment not found")
    comment.is_visible = False
    await db.commit()
    logger.info("comment soft-deleted: id=%s", comment_id)
    return None


# ── admin: list all comments (including hidden) ──

@admin_router.get("", response_model=CommentListResponse)
async def admin_list_comments(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> CommentListResponse:
    """Admin: list all comments (including hidden ones), authenticated via X-Admin-Secret."""
    await _require_admin(request)
    from sqlalchemy import func as sa_func
    total = (await db.execute(select(sa_func.count(Comment.id)))).scalar_one()
    stmt = (
        select(Comment)
        .order_by(desc(Comment.created_at))
        .offset(offset)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return CommentListResponse(
        comments=[CommentPublicView.from_orm(c) for c in rows],
        total=int(total),
        has_more=(offset + len(rows)) < int(total),
    )


# ── admin: delete comment ──

@admin_router.delete("/{comment_id}", status_code=204)
async def admin_delete_comment(
    request: Request,
    comment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Admin soft-delete — equivalent to DELETE /api/comments/{id}, differing only in URL prefix."""
    await _require_admin(request)
    try:
        uuid_obj = uuid.UUID(comment_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid comment id (not a UUID)")
    stmt = select(Comment).where(Comment.id == uuid_obj)
    comment = (await db.execute(stmt)).scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail="comment not found")
    comment.is_visible = False
    await db.commit()
    logger.info("admin comment soft-deleted: id=%s", comment_id)
    return None
