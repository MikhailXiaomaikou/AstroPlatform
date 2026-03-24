"""Auth API — registration, login, user profile, Stripe subscription."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.config import settings
from app.models.database import get_db
from app.models.schemas import SetupKey, User
from app.rate_limit import limiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Request / Response models ──

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserProfile(BaseModel):
    id: str
    email: str
    subscription_tier: str
    stripe_customer_id: str | None = None


class SetupKeyRequest(BaseModel):
    setup_key: str


class GenerateKeysRequest(BaseModel):
    count: int = 5
    label: str = "beta"


class SetupKeyInfo(BaseModel):
    key: str
    label: str
    used: bool
    used_by_email: str | None = None


class SubscribeRequest(BaseModel):
    tier: str  # "solo", "lab", "institution"


# ── Endpoints ──

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def register(request: Request, req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == req.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        subscription_tier="solo",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserProfile)
async def get_profile(user: User = Depends(get_current_user)):
    return UserProfile(
        id=str(user.id),
        email=user.email,
        subscription_tier=user.subscription_tier,
        stripe_customer_id=user.stripe_customer_id,
    )


@router.post("/setup-key-login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def setup_key_login(request: Request, req: SetupKeyRequest, db: AsyncSession = Depends(get_db)):
    """Log in (or auto-register) using a setup key."""
    import secrets

    key_str = req.setup_key.strip()
    if not key_str:
        raise HTTPException(status_code=400, detail="Setup key cannot be empty")

    result = await db.execute(select(SetupKey).where(SetupKey.key == key_str))
    setup_key = result.scalar_one_or_none()
    if not setup_key:
        raise HTTPException(status_code=401, detail="Invalid setup key")

    # If already used, log in as the bound user
    if setup_key.used_by:
        result = await db.execute(select(User).where(User.id == setup_key.used_by))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401, detail="User associated with this key no longer exists")
        token = create_access_token(user.id)
        return TokenResponse(access_token=token)

    # First use: create a new account bound to this key
    random_suffix = secrets.token_hex(4)
    email = f"beta-{setup_key.label}-{random_suffix}@astro.local"
    random_pw = secrets.token_urlsafe(16)

    user = User(
        email=email,
        password_hash=hash_password(random_pw),
        subscription_tier="solo",
    )
    db.add(user)
    await db.flush()

    from datetime import datetime, timezone
    setup_key.used_by = user.id
    setup_key.used_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@router.post("/generate-setup-keys", response_model=list[str])
async def generate_setup_keys(
    req: GenerateKeysRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generate batch of setup keys for beta distribution.
    NOTE: In production, protect this with admin auth.
    """
    import secrets

    if req.count < 1 or req.count > 100:
        raise HTTPException(status_code=400, detail="Count must be 1-100")

    keys = []
    for i in range(req.count):
        key_str = f"ASTRO-{req.label.upper()}-{secrets.token_hex(6).upper()}"
        sk = SetupKey(key=key_str, label=req.label)
        db.add(sk)
        keys.append(key_str)

    await db.commit()
    return keys


@router.get("/setup-keys", response_model=list[SetupKeyInfo])
async def list_setup_keys(db: AsyncSession = Depends(get_db)):
    """List all setup keys and their usage status.
    NOTE: In production, protect this with admin auth.
    """
    from sqlalchemy.orm import aliased
    UserAlias = aliased(User)

    result = await db.execute(
        select(SetupKey, UserAlias)
        .outerjoin(UserAlias, SetupKey.used_by == UserAlias.id)
        .order_by(SetupKey.created_at.desc())
    )
    rows = result.all()
    return [
        SetupKeyInfo(
            key=sk.key,
            label=sk.label,
            used=sk.used_by is not None,
            used_by_email=u.email if u else None,
        )
        for sk, u in rows
    ]


@router.post("/subscribe")
async def subscribe(
    req: SubscribeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update subscription tier (billing not yet integrated)."""
    valid_tiers = {"solo", "lab", "institution"}
    if req.tier not in valid_tiers:
        raise HTTPException(status_code=400, detail=f"Invalid tier. Choose from: {valid_tiers}")
    user.subscription_tier = req.tier
    await db.commit()
    return {"status": "updated", "tier": req.tier}


@router.get("/usage")
async def get_usage(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current user's usage stats (pipeline runs this month, storage)."""
    from datetime import datetime, timezone
    from sqlalchemy import func as sqlfunc
    from app.models.schemas import PipelineRun, DataFile

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Count pipeline runs this month
    runs_result = await db.execute(
        select(sqlfunc.count())
        .select_from(PipelineRun)
        .where(PipelineRun.user_id == user.id, PipelineRun.created_at >= month_start)
    )
    runs_this_month = runs_result.scalar() or 0

    # Count total data files as proxy for storage
    files_result = await db.execute(
        select(sqlfunc.count())
        .select_from(DataFile)
        .where(DataFile.user_id == user.id)
    )
    total_files = files_result.scalar() or 0
    # Estimate ~50MB per file as rough proxy
    storage_used_gb = round(total_files * 0.05, 2)

    tier = user.subscription_tier or "solo"
    runs_limit = 300 if tier == "solo" else None
    storage_limit = 5 if tier == "solo" else 50 if tier == "lab" else None

    return {
        "runs_this_month": runs_this_month,
        "runs_limit": runs_limit,
        "storage_used_gb": storage_used_gb,
        "storage_limit": storage_limit,
    }
