"""Idempotency keys (Phase 3 / R11).

Endpoints that write shared state (chat save, FITS upload, snapshot,
batch-upload) accept a Stripe-style ``Idempotency-Key`` header.  The
first request with a given key executes normally; subsequent requests
with the same key within a TTL window return the previously-recorded
response without re-executing.

The store is a small SQL table (key + user_id + response_json +
created_at).  Redis would be faster, but the store is tiny and we
already have Postgres in production.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Column, DateTime, String, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Base


IDEMPOTENCY_TTL = timedelta(hours=24)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key_hash = Column(String(64), primary_key=True)
    user_id = Column(String(36), nullable=True, index=True)
    response_json = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


def _hash_key(key: str, user_id: str | None) -> str:
    """Namespaced hash so one user's key can't collide with another's."""
    raw = f"{user_id or 'anon'}::{key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def lookup(
    db: AsyncSession, key: str, user_id: str | None,
) -> Any | None:
    """Return the stored response for (user, key) if present and fresh."""
    key_hash = _hash_key(key, user_id)
    row = (await db.execute(
        select(IdempotencyKey).where(IdempotencyKey.key_hash == key_hash)
    )).scalar_one_or_none()
    if row is None:
        return None
    # Garbage-collect stale entries opportunistically.
    if datetime.now(timezone.utc) - row.created_at > IDEMPOTENCY_TTL:
        await db.delete(row)
        await db.commit()
        return None
    try:
        return json.loads(row.response_json)
    except (TypeError, ValueError):
        return None


async def store(
    db: AsyncSession, key: str, user_id: str | None, response: Any,
) -> None:
    """Persist the response under (user, key)."""
    key_hash = _hash_key(key, user_id)
    payload = json.dumps(response, default=str)[:2_000_000]  # 2 MB cap
    existing = (await db.execute(
        select(IdempotencyKey).where(IdempotencyKey.key_hash == key_hash)
    )).scalar_one_or_none()
    if existing is not None:
        existing.response_json = payload
        existing.created_at = datetime.now(timezone.utc)
    else:
        db.add(IdempotencyKey(
            key_hash=key_hash,
            user_id=str(user_id) if user_id else None,
            response_json=payload,
            created_at=datetime.now(timezone.utc),
        ))
    await db.commit()


def new_key() -> str:
    """Convenience for callers that want a fresh key (mostly tests)."""
    return str(uuid.uuid4())
