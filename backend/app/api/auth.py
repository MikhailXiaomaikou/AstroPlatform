"""Auth API — registration, login, user profile, Stripe subscription."""

import logging

import stripe
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

stripe.api_key = settings.stripe_secret_key


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
    """Create or update Stripe subscription."""
    valid_tiers = {"solo", "lab", "institution"}
    if req.tier not in valid_tiers:
        raise HTTPException(status_code=400, detail=f"Invalid tier. Choose from: {valid_tiers}")

    if not settings.stripe_secret_key:
        # Dev mode: just update tier directly
        user.subscription_tier = req.tier
        await db.commit()
        return {"status": "updated", "tier": req.tier, "mode": "dev"}

    # Create Stripe customer if needed
    if not user.stripe_customer_id:
        customer = stripe.Customer.create(email=user.email)
        user.stripe_customer_id = customer.id
        await db.commit()

    # In production, you'd create a Checkout Session or Subscription here
    # For now, update the tier directly
    user.subscription_tier = req.tier
    await db.commit()

    return {"status": "updated", "tier": req.tier}


@router.post("/stripe-webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Handle Stripe webhook events."""
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=400, detail="Webhook not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    if event["type"] == "customer.subscription.updated":
        subscription = event["data"]["object"]
        customer_id = subscription["customer"]

        result = await db.execute(
            select(User).where(User.stripe_customer_id == customer_id)
        )
        user = result.scalar_one_or_none()
        if user:
            # Map Stripe price to tier
            status_val = subscription.get("status")
            if status_val == "active":
                logger.info(f"Subscription updated for user {user.email}")
            elif status_val in ("canceled", "unpaid"):
                user.subscription_tier = "solo"
                await db.commit()

    return {"received": True}
