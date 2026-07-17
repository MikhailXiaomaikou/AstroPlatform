"""JWT authentication utilities."""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import get_db
from app.models.schemas import User

security = HTTPBearer(auto_error=False)

_LOCAL_DEV_USERNAME = "local-dev"
_LOCAL_DEV_EMAIL = "local-dev@localhost"
_ACTIVE_ACCOUNT_STATUS = "ACTIVE"


def _env_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def local_dev_no_auth_enabled() -> bool:
    """Return True when this process explicitly opts into local no-auth mode.

    The flag is intentionally ignored in production.  The desktop launcher sets
    both LOCAL_DEV_NO_AUTH=1 and ENV=dev, while Render should set neither.
    """
    if not _env_truthy(os.getenv("LOCAL_DEV_NO_AUTH")):
        return False
    env = os.getenv("ENV", "").strip().lower()
    return env in {"dev", "development", "local", "test"}


async def get_or_create_local_dev_user(db: AsyncSession) -> User:
    """Persist and return the deterministic local desktop user."""
    result = await db.execute(select(User).where(User.username == _LOCAL_DEV_USERNAME))
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    user = User(
        username=_LOCAL_DEV_USERNAME,
        email=_LOCAL_DEV_EMAIL,
        password_hash=hash_password(uuid.uuid4().hex),
        subscription_tier="institution",
        display_name="Local Dev",
    )
    db.add(user)
    try:
        await db.commit()
        await db.refresh(user)
        return user
    except Exception:
        await db.rollback()
        result = await db.execute(select(User).where(User.username == _LOCAL_DEV_USERNAME))
        user = result.scalar_one_or_none()
        if user is None:
            raise
        return user


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: uuid.UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> uuid.UUID:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return uuid.UUID(payload["sub"])
    except (JWTError, KeyError, ValueError) as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e


def require_active_account(user: User | None) -> User:
    """Reject disabled/deleting accounts even when their JWT is still valid."""

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    if str(user.account_status or "").upper() != _ACTIVE_ACCOUNT_STATUS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is disabled",
        )
    return user


async def require_not_tombstoned(user: User, db: AsyncSession) -> User:
    """Prevent an old database restore from resurrecting a deleted account."""

    from app.services.account_deletion import external_deletion_tombstone_exists

    if await asyncio.to_thread(external_deletion_tombstone_exists, user.id):
        user.account_status = "DELETION_PENDING"
        user.deletion_requested_at = user.deletion_requested_at or datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is disabled",
        )
    return user


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    if local_dev_no_auth_enabled():
        user = require_active_account(await get_or_create_local_dev_user(db))
        return await require_not_tombstoned(user, db)
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user_id = decode_token(creds.credentials)
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    return await require_not_tombstoned(require_active_account(user), db)


async def get_optional_user(
    creds: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Returns user if authenticated, None otherwise. For endpoints that work with or without auth."""
    if local_dev_no_auth_enabled():
        try:
            user = require_active_account(await get_or_create_local_dev_user(db))
            return await require_not_tombstoned(user, db)
        except HTTPException:
            return None
    if creds is None:
        return None
    try:
        user_id = decode_token(creds.credentials)
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None or str(user.account_status or "").upper() != _ACTIVE_ACCOUNT_STATUS:
            return None
        try:
            return await require_not_tombstoned(user, db)
        except HTTPException:
            return None
    except HTTPException:
        return None
