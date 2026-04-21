"""公开评论区 (Landing 页下方).

无需登录, 任何访客填昵称 + 内容就能提交; 管理员用 X-Admin-Secret 删除.
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
from app.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/comments", tags=["comments"])


# 内容边界 (保持跟前端一致, 如果前端也改, 这里也改)
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


# ── 公开读 ──

@router.get("", response_model=CommentListResponse)
async def list_comments(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> CommentListResponse:
    """返回可见评论, 按创建时间倒序.  不做 auth."""
    # 总数 (只算可见的)
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


# ── 公开写 + rate limit ──

@router.post("", response_model=CommentPublicView, status_code=201)
@limiter.limit("3/minute")
async def create_comment(
    request: Request,
    req: CommentCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> CommentPublicView:
    """任何访客都能发评论.  rate limit 按 IP 限 3/minute 防垃圾.

    Pydantic 已经做了长度校验, 这里只追加:
    - trim 后长度二次校验 (防御)
    - 记录 IP 到 client_ip (仅服务端留档, 不返回)
    """
    name = req.author_name.strip()
    content = req.content.strip()
    if not (_NAME_MIN <= len(name) <= _NAME_MAX):
        raise HTTPException(status_code=400, detail="author_name length out of range")
    if not (_CONTENT_MIN <= len(content) <= _CONTENT_MAX):
        raise HTTPException(status_code=400, detail="content length out of range")

    # 记 IP (request.client.host 在 Render 后面的 proxy 可能是 proxy IP;
    # 首选 X-Forwarded-For 里的第一条)
    client_ip: str | None = None
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        client_ip = fwd.split(",")[0].strip()
    elif request.client:
        client_ip = request.client.host
    if client_ip and len(client_ip) > 64:
        client_ip = client_ip[:64]

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


# ── 管理员软删除 ──

@router.delete("/{comment_id}", status_code=204)
async def delete_comment(
    request: Request,
    comment_id: str,
    db: AsyncSession = Depends(get_db),
):
    """管理员软删除 — 置 is_visible=False, 行不动, 便于审计.  需 X-Admin-Secret."""
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
