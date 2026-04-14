"""Auth API — registration, login, Google OAuth, user profile, Stripe subscription."""

import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
from app.utils.usernames import (
    internal_email_for_username,
    normalize_username,
    preferred_username,
    username_from_email,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Request / Response models ──

class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class GoogleLoginRequest(BaseModel):
    credential: str  # Google ID token (JWT from Google Identity Services)


class UserProfile(BaseModel):
    id: str
    username: str
    email: str
    subscription_tier: str
    stripe_customer_id: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    google_linked: bool = False


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

_USERNAME_MIN_LENGTH = 3
_USERNAME_MAX_LENGTH = 32


def _clean_username(raw: str) -> str:
    username = normalize_username(raw)
    if not username:
        raise HTTPException(status_code=400, detail="Username cannot be empty")
    if len(username) < _USERNAME_MIN_LENGTH:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters")
    if len(username) > _USERNAME_MAX_LENGTH:
        raise HTTPException(status_code=400, detail="Username must be at most 32 characters")
    return username


async def _username_exists(db: AsyncSession, username: str) -> bool:
    result = await db.execute(select(User.id).where(User.username == username))
    return result.scalar_one_or_none() is not None


async def _unique_username(db: AsyncSession, *candidates: str, fallback: str = "user") -> str:
    base = preferred_username(*candidates, fallback=fallback)
    if len(base) < _USERNAME_MIN_LENGTH:
        base = fallback
    base = base[:_USERNAME_MAX_LENGTH]
    candidate = base
    suffix = 2
    while await _username_exists(db, candidate):
        suffix_str = str(suffix)
        trimmed = base[: max(1, _USERNAME_MAX_LENGTH - len(suffix_str))].rstrip("._-") or fallback
        candidate = f"{trimmed}{suffix_str}"
        suffix += 1
    return candidate

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def register(request: Request, req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    username = _clean_username(req.username)
    existing = await db.execute(select(User).where(User.username == username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already registered")

    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if len(req.password) > 128:
        raise HTTPException(status_code=400, detail="Password must be at most 128 characters")

    user = User(
        username=username,
        email=internal_email_for_username(username),
        password_hash=hash_password(req.password),
        subscription_tier="solo",
        display_name=username,
    )
    db.add(user)
    try:
        await db.commit()
        await db.refresh(user)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Username already registered") from exc

    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, req: LoginRequest, db: AsyncSession = Depends(get_db)):
    username = _clean_username(req.username)
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


@router.post("/google", response_model=TokenResponse)
@limiter.limit("20/minute")
async def google_login(request: Request, req: GoogleLoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate with Google. Verifies the ID token, creates or finds the user."""
    if not settings.google_client_id:
        raise HTTPException(
            status_code=503,
            detail="Google login not configured. Set GOOGLE_CLIENT_ID in environment.",
        )

    # Verify Google ID token via Google's tokeninfo endpoint
    google_payload = await _verify_google_token(req.credential)

    google_id = google_payload["sub"]
    email = google_payload.get("email", "")
    email_verified = google_payload.get("email_verified", False)
    display_name = google_payload.get("name", "")
    avatar_url = google_payload.get("picture", "")

    if not email or not email_verified:
        raise HTTPException(status_code=400, detail="Google account email is not verified")

    # 1. Check if user exists by google_id
    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    if user:
        # Update profile info from Google
        if not user.username:
            user.username = await _unique_username(
                db,
                display_name,
                username_from_email(email),
                fallback="google-user",
            )
        if display_name and user.display_name != display_name:
            user.display_name = display_name
        if avatar_url and user.avatar_url != avatar_url:
            user.avatar_url = avatar_url
        await db.commit()
        return TokenResponse(access_token=create_access_token(user.id))

    # 2. Check if user exists by email (link Google to existing account)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user:
        user.google_id = google_id
        if not user.username:
            user.username = await _unique_username(
                db,
                display_name,
                username_from_email(email),
                fallback="google-user",
            )
        if not user.display_name:
            user.display_name = display_name
        if not user.avatar_url:
            user.avatar_url = avatar_url
        await db.commit()
        return TokenResponse(access_token=create_access_token(user.id))

    # 3. Create new user (handle race condition with concurrent requests)
    random_pw = secrets.token_urlsafe(32)
    username = await _unique_username(
        db,
        display_name,
        username_from_email(email),
        fallback="google-user",
    )
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(random_pw),
        google_id=google_id,
        display_name=display_name,
        avatar_url=avatar_url,
        subscription_tier="solo",
    )
    db.add(user)
    try:
        await db.commit()
        await db.refresh(user)
    except IntegrityError:
        # Concurrent request already created this user — look them up again
        await db.rollback()
        result = await db.execute(select(User).where(User.google_id == google_id))
        user = result.scalar_one_or_none()
        if user is None:
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=500, detail="Account creation failed. Please try again.")

    return TokenResponse(access_token=create_access_token(user.id))


async def _verify_google_token(id_token: str) -> dict:
    """Verify a Google ID token and return the payload.

    Uses Google's tokeninfo endpoint for simplicity and reliability.
    In high-traffic production, switch to local JWT verification with
    Google's public keys from https://www.googleapis.com/oauth2/v3/certs.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Method 1: tokeninfo endpoint (simple, reliable)
        resp = await client.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": id_token},
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    payload = resp.json()

    # Verify audience matches our client ID
    aud = payload.get("aud", "")
    if aud != settings.google_client_id:
        raise HTTPException(
            status_code=401,
            detail="Google token audience mismatch",
        )

    # Verify issuer
    iss = payload.get("iss", "")
    if iss not in ("accounts.google.com", "https://accounts.google.com"):
        raise HTTPException(status_code=401, detail="Invalid Google token issuer")

    return payload


@router.get("/me", response_model=UserProfile)
async def get_profile(user: User = Depends(get_current_user)):
    return UserProfile(
        id=str(user.id),
        username=user.username or user.email.split("@")[0],
        email=user.email,
        subscription_tier=user.subscription_tier,
        stripe_customer_id=user.stripe_customer_id,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        google_linked=bool(user.google_id),
    )


@router.post("/setup-key-login", response_model=TokenResponse)
@limiter.limit("3/minute")
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
    username = await _unique_username(
        db,
        setup_key.label,
        f"beta-{setup_key.label}-{random_suffix}",
        fallback="beta-user",
    )
    random_pw = secrets.token_urlsafe(16)

    user = User(
        username=username,
        email=internal_email_for_username(username),
        password_hash=hash_password(random_pw),
        subscription_tier="solo",
        display_name=username,
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


async def _require_admin(request: Request):
    """Check admin authorization via X-Admin-Secret header."""
    import os
    _env = os.getenv("ENV", "dev")
    if not settings.admin_secret:
        # No admin secret configured — allow in dev, block in production
        if _env != "dev":
            raise HTTPException(status_code=403, detail="Admin endpoints disabled (ADMIN_SECRET not configured)")
        return
    provided = request.headers.get("X-Admin-Secret", "")
    if not provided or provided != settings.admin_secret:
        raise HTTPException(status_code=403, detail="Invalid admin secret")


@router.post("/generate-setup-keys", response_model=list[str])
async def generate_setup_keys(
    request: Request,
    req: GenerateKeysRequest,
    db: AsyncSession = Depends(get_db),
):
    """Generate batch of setup keys for beta distribution."""
    await _require_admin(request)
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
async def list_setup_keys(request: Request, db: AsyncSession = Depends(get_db)):
    """List all setup keys and their usage status."""
    await _require_admin(request)
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
