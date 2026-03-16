"""Team collaboration API — team management, shared pipelines & datasets, comments."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.models.database import get_db
from app.models.schemas import (
    DataFile,
    Friendship,
    PipelineComment,
    PipelineTemplateDB,
    SearchHistory,
    SharedDataset,
    SharedPipeline,
    TeamMember,
    User,
)

router = APIRouter(prefix="/api/team", tags=["team"])


# ── Request / Response models ──

class InviteRequest(BaseModel):
    email: EmailStr
    role: str = "member"  # "member" or "admin"


class MemberResponse(BaseModel):
    id: str
    user_id: str
    email: str
    role: str
    created_at: str | None


class UpdateRoleRequest(BaseModel):
    role: str


class SharePipelineRequest(BaseModel):
    user_id: str
    permission: str = "view"  # "view" or "edit"


class SharedPipelineResponse(BaseModel):
    id: str
    template_id: str
    template_name: str
    shared_by: str
    shared_by_email: str
    permission: str
    created_at: str | None


class CommentRequest(BaseModel):
    content: str


class CommentResponse(BaseModel):
    id: str
    user_id: str
    email: str
    content: str
    created_at: str | None


class ShareDatasetRequest(BaseModel):
    user_id: str


class SharedDatasetResponse(BaseModel):
    id: str
    data_file_id: str
    source: str
    object_id: str
    shared_by: str
    shared_by_email: str
    created_at: str | None


class FriendRequest(BaseModel):
    email: EmailStr


class FriendResponse(BaseModel):
    id: str
    user_id: str
    email: str
    status: str
    direction: str  # "sent" or "received"
    created_at: str | None


class SearchHistoryResponse(BaseModel):
    id: str
    query: str
    sources: str
    result_count: int
    params: dict | None
    created_at: str | None


# ── Helpers ──

def _require_team_tier(user: User):
    """Raise 403 if user is not on lab or institution tier.
    NOTE: disabled during beta — all users can use team features.
    """
    pass


async def _find_team_owner(user: User, db: AsyncSession) -> uuid.UUID:
    """Return the owner_id for the team this user belongs to.

    If the user owns a team, return their id.
    If the user is a member of a team, return that team's owner_id.
    Otherwise, return the user's own id (they can start a team).
    """
    # Check if user is a member of another team
    result = await db.execute(
        select(TeamMember).where(TeamMember.member_id == user.id)
    )
    membership = result.scalar_one_or_none()
    if membership:
        return membership.owner_id
    return user.id


# ── Team Management ──

@router.post("/invite", response_model=MemberResponse)
async def invite_member(
    req: InviteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Invite a user to the team by email."""
    _require_team_tier(user)

    if req.role not in ("member", "admin"):
        raise HTTPException(status_code=400, detail="Role must be 'member' or 'admin'")

    # Look up the user by email
    result = await db.execute(select(User).where(User.email == req.email))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found with that email")

    if target.id == user.id:
        raise HTTPException(status_code=400, detail="Cannot invite yourself")

    # Check if already a team member
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.owner_id == user.id,
            TeamMember.member_id == target.id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="User is already a team member")

    member = TeamMember(
        owner_id=user.id,
        member_id=target.id,
        role=req.role,
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)

    return MemberResponse(
        id=str(member.id),
        user_id=str(target.id),
        email=target.email,
        role=member.role,
        created_at=member.created_at.isoformat() if member.created_at else None,
    )


@router.get("/members", response_model=list[MemberResponse])
async def list_members(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all team members."""
    owner_id = await _find_team_owner(user, db)

    result = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.member_id == User.id)
        .where(TeamMember.owner_id == owner_id)
        .order_by(TeamMember.created_at)
    )
    rows = result.all()

    return [
        MemberResponse(
            id=str(tm.id),
            user_id=str(u.id),
            email=u.email,
            role=tm.role,
            created_at=tm.created_at.isoformat() if tm.created_at else None,
        )
        for tm, u in rows
    ]


@router.delete("/members/{member_id}")
async def remove_member(
    member_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove a team member (owner only)."""
    mid = uuid.UUID(member_id)
    result = await db.execute(
        select(TeamMember).where(TeamMember.id == mid, TeamMember.owner_id == user.id)
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found")

    await db.execute(
        delete(TeamMember).where(TeamMember.id == mid, TeamMember.owner_id == user.id)
    )
    await db.commit()
    return {"deleted": True}


@router.patch("/members/{member_id}", response_model=MemberResponse)
async def update_member_role(
    member_id: str,
    req: UpdateRoleRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update a team member's role (owner only)."""
    if req.role not in ("member", "admin"):
        raise HTTPException(status_code=400, detail="Role must be 'member' or 'admin'")

    mid = uuid.UUID(member_id)
    result = await db.execute(
        select(TeamMember, User)
        .join(User, TeamMember.member_id == User.id)
        .where(TeamMember.id == mid, TeamMember.owner_id == user.id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Team member not found")

    tm, target_user = row
    tm.role = req.role
    await db.commit()
    await db.refresh(tm)

    return MemberResponse(
        id=str(tm.id),
        user_id=str(target_user.id),
        email=target_user.email,
        role=tm.role,
        created_at=tm.created_at.isoformat() if tm.created_at else None,
    )


# ── Shared Pipelines ──

@router.post("/pipelines/{template_id}/share", response_model=SharedPipelineResponse)
async def share_pipeline(
    template_id: str,
    req: SharePipelineRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Share a pipeline template with another user."""
    if req.permission not in ("view", "edit"):
        raise HTTPException(status_code=400, detail="Permission must be 'view' or 'edit'")

    tid = uuid.UUID(template_id)
    target_uid = uuid.UUID(req.user_id)

    # Verify the template exists
    result = await db.execute(
        select(PipelineTemplateDB).where(PipelineTemplateDB.id == tid)
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Pipeline template not found")

    # Verify target user exists
    result = await db.execute(select(User).where(User.id == target_uid))
    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found")

    shared = SharedPipeline(
        template_id=tid,
        shared_by=user.id,
        shared_with=target_uid,
        permission=req.permission,
    )
    db.add(shared)
    await db.commit()
    await db.refresh(shared)

    return SharedPipelineResponse(
        id=str(shared.id),
        template_id=str(tid),
        template_name=template.name,
        shared_by=str(user.id),
        shared_by_email=user.email,
        permission=shared.permission,
        created_at=shared.created_at.isoformat() if shared.created_at else None,
    )


@router.get("/pipelines/shared", response_model=list[SharedPipelineResponse])
async def list_shared_pipelines(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List pipelines shared with the current user."""
    result = await db.execute(
        select(SharedPipeline, PipelineTemplateDB, User)
        .join(PipelineTemplateDB, SharedPipeline.template_id == PipelineTemplateDB.id)
        .join(User, SharedPipeline.shared_by == User.id)
        .where(SharedPipeline.shared_with == user.id)
        .order_by(SharedPipeline.created_at.desc())
    )
    rows = result.all()

    return [
        SharedPipelineResponse(
            id=str(sp.id),
            template_id=str(sp.template_id),
            template_name=tmpl.name,
            shared_by=str(sp.shared_by),
            shared_by_email=sharer.email,
            permission=sp.permission,
            created_at=sp.created_at.isoformat() if sp.created_at else None,
        )
        for sp, tmpl, sharer in rows
    ]


# ── Pipeline Comments ──

@router.post("/pipelines/{template_id}/comments", response_model=CommentResponse)
async def add_comment(
    template_id: str,
    req: CommentRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Add a comment to a pipeline template."""
    tid = uuid.UUID(template_id)

    # Verify the template exists
    result = await db.execute(
        select(PipelineTemplateDB).where(PipelineTemplateDB.id == tid)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Pipeline template not found")

    comment = PipelineComment(
        template_id=tid,
        user_id=user.id,
        content=req.content,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)

    return CommentResponse(
        id=str(comment.id),
        user_id=str(user.id),
        email=user.email,
        content=comment.content,
        created_at=comment.created_at.isoformat() if comment.created_at else None,
    )


@router.get("/pipelines/{template_id}/comments", response_model=list[CommentResponse])
async def list_comments(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List comments on a pipeline template."""
    tid = uuid.UUID(template_id)

    result = await db.execute(
        select(PipelineComment, User)
        .join(User, PipelineComment.user_id == User.id)
        .where(PipelineComment.template_id == tid)
        .order_by(PipelineComment.created_at)
    )
    rows = result.all()

    return [
        CommentResponse(
            id=str(c.id),
            user_id=str(c.user_id),
            email=u.email,
            content=c.content,
            created_at=c.created_at.isoformat() if c.created_at else None,
        )
        for c, u in rows
    ]


# ── Shared Datasets ──

@router.post("/datasets/{file_id}/share", response_model=SharedDatasetResponse)
async def share_dataset(
    file_id: str,
    req: ShareDatasetRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Share a dataset with another user."""
    fid = uuid.UUID(file_id)
    target_uid = uuid.UUID(req.user_id)

    # Verify the data file exists
    result = await db.execute(
        select(DataFile).where(DataFile.id == fid)
    )
    data_file = result.scalar_one_or_none()
    if not data_file:
        raise HTTPException(status_code=404, detail="Data file not found")

    # Verify target user exists
    result = await db.execute(select(User).where(User.id == target_uid))
    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found")

    shared = SharedDataset(
        data_file_id=fid,
        shared_by=user.id,
        shared_with=target_uid,
    )
    db.add(shared)
    await db.commit()
    await db.refresh(shared)

    return SharedDatasetResponse(
        id=str(shared.id),
        data_file_id=str(fid),
        source=data_file.source,
        object_id=data_file.object_id,
        shared_by=str(user.id),
        shared_by_email=user.email,
        created_at=shared.created_at.isoformat() if shared.created_at else None,
    )


@router.get("/datasets/shared", response_model=list[SharedDatasetResponse])
async def list_shared_datasets(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List datasets shared with the current user."""
    result = await db.execute(
        select(SharedDataset, DataFile, User)
        .join(DataFile, SharedDataset.data_file_id == DataFile.id)
        .join(User, SharedDataset.shared_by == User.id)
        .where(SharedDataset.shared_with == user.id)
        .order_by(SharedDataset.created_at.desc())
    )
    rows = result.all()

    return [
        SharedDatasetResponse(
            id=str(sd.id),
            data_file_id=str(sd.data_file_id),
            source=df.source,
            object_id=df.object_id,
            shared_by=str(sd.shared_by),
            shared_by_email=sharer.email,
            created_at=sd.created_at.isoformat() if sd.created_at else None,
        )
        for sd, df, sharer in rows
    ]


# ══════════════════════════════════════
# Friends
# ══════════════════════════════════════

def _friendship_to_response(
    friendship: Friendship, other_user: User, direction: str
) -> FriendResponse:
    return FriendResponse(
        id=str(friendship.id),
        user_id=str(other_user.id),
        email=other_user.email,
        status=friendship.status,
        direction=direction,
        created_at=friendship.created_at.isoformat() if friendship.created_at else None,
    )


@router.post("/friends/request", response_model=FriendResponse)
async def send_friend_request(
    req: FriendRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Send a friend request to another user by email."""
    result = await db.execute(select(User).where(User.email == req.email))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="User not found with that email")
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="Cannot add yourself as a friend")

    # Check existing friendship in either direction
    result = await db.execute(
        select(Friendship).where(
            or_(
                (Friendship.from_user_id == user.id) & (Friendship.to_user_id == target.id),
                (Friendship.from_user_id == target.id) & (Friendship.to_user_id == user.id),
            )
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        if existing.status == "accepted":
            raise HTTPException(status_code=400, detail="Already friends")
        if existing.status == "pending":
            raise HTTPException(status_code=400, detail="Friend request already pending")
        # If rejected, allow re-sending
        existing.status = "pending"
        existing.from_user_id = user.id
        existing.to_user_id = target.id
        await db.commit()
        await db.refresh(existing)
        return _friendship_to_response(existing, target, "sent")

    friendship = Friendship(from_user_id=user.id, to_user_id=target.id, status="pending")
    db.add(friendship)
    await db.commit()
    await db.refresh(friendship)
    return _friendship_to_response(friendship, target, "sent")


@router.get("/friends", response_model=list[FriendResponse])
async def list_friends(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all friends and pending requests."""
    # Sent requests
    result = await db.execute(
        select(Friendship, User)
        .join(User, Friendship.to_user_id == User.id)
        .where(Friendship.from_user_id == user.id)
        .order_by(Friendship.created_at.desc())
    )
    sent = [_friendship_to_response(f, u, "sent") for f, u in result.all()]

    # Received requests
    result = await db.execute(
        select(Friendship, User)
        .join(User, Friendship.from_user_id == User.id)
        .where(Friendship.to_user_id == user.id)
        .order_by(Friendship.created_at.desc())
    )
    received = [_friendship_to_response(f, u, "received") for f, u in result.all()]

    return sent + received


@router.post("/friends/{friendship_id}/accept", response_model=FriendResponse)
async def accept_friend(
    friendship_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Accept a received friend request."""
    fid = uuid.UUID(friendship_id)
    result = await db.execute(
        select(Friendship, User)
        .join(User, Friendship.from_user_id == User.id)
        .where(Friendship.id == fid, Friendship.to_user_id == user.id, Friendship.status == "pending")
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Pending friend request not found")

    friendship, sender = row
    friendship.status = "accepted"
    await db.commit()
    await db.refresh(friendship)

    # Auto-add to team if user has team tier
    if user.subscription_tier in ("lab", "institution"):
        existing = await db.execute(
            select(TeamMember).where(TeamMember.owner_id == user.id, TeamMember.member_id == sender.id)
        )
        if not existing.scalar_one_or_none():
            db.add(TeamMember(owner_id=user.id, member_id=sender.id, role="member"))
            await db.commit()

    return _friendship_to_response(friendship, sender, "received")


@router.post("/friends/{friendship_id}/reject")
async def reject_friend(
    friendship_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Reject a received friend request."""
    fid = uuid.UUID(friendship_id)
    result = await db.execute(
        select(Friendship).where(
            Friendship.id == fid, Friendship.to_user_id == user.id, Friendship.status == "pending"
        )
    )
    friendship = result.scalar_one_or_none()
    if not friendship:
        raise HTTPException(status_code=404, detail="Pending friend request not found")

    friendship.status = "rejected"
    await db.commit()
    return {"status": "rejected"}


@router.delete("/friends/{friendship_id}")
async def remove_friend(
    friendship_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove a friend."""
    fid = uuid.UUID(friendship_id)
    result = await db.execute(
        select(Friendship).where(
            Friendship.id == fid,
            or_(Friendship.from_user_id == user.id, Friendship.to_user_id == user.id),
        )
    )
    friendship = result.scalar_one_or_none()
    if not friendship:
        raise HTTPException(status_code=404, detail="Friendship not found")

    await db.execute(delete(Friendship).where(Friendship.id == fid))
    await db.commit()
    return {"deleted": True}


# ══════════════════════════════════════
# Search History
# ══════════════════════════════════════

@router.post("/search-history", response_model=SearchHistoryResponse)
async def save_search(
    body: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Save a search to history."""
    entry = SearchHistory(
        user_id=user.id,
        query=body.get("query", ""),
        sources=body.get("sources", ""),
        result_count=body.get("result_count", 0),
        params=body.get("params"),
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return SearchHistoryResponse(
        id=str(entry.id),
        query=entry.query,
        sources=entry.sources,
        result_count=entry.result_count,
        params=entry.params,
        created_at=entry.created_at.isoformat() if entry.created_at else None,
    )


@router.get("/search-history", response_model=list[SearchHistoryResponse])
async def list_search_history(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List user's search history (newest first, max 50)."""
    result = await db.execute(
        select(SearchHistory)
        .where(SearchHistory.user_id == user.id)
        .order_by(SearchHistory.created_at.desc())
        .limit(50)
    )
    rows = result.scalars().all()
    return [
        SearchHistoryResponse(
            id=str(h.id),
            query=h.query,
            sources=h.sources,
            result_count=h.result_count,
            params=h.params,
            created_at=h.created_at.isoformat() if h.created_at else None,
        )
        for h in rows
    ]


@router.delete("/search-history/{entry_id}")
async def delete_search_history(
    entry_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete a search history entry."""
    eid = uuid.UUID(entry_id)
    await db.execute(
        delete(SearchHistory).where(SearchHistory.id == eid, SearchHistory.user_id == user.id)
    )
    await db.commit()
    return {"deleted": True}


@router.delete("/search-history")
async def clear_search_history(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Clear all search history."""
    await db.execute(
        delete(SearchHistory).where(SearchHistory.user_id == user.id)
    )
    await db.commit()
    return {"deleted": True}
