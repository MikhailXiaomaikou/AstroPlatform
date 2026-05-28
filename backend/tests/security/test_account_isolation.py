"""Security L10 — one user's resources must not be visible to another.

The smallest concrete contract: GET /api/auth/me with user B's token
must NOT return user A's email, and listing routes that take
``Depends(get_current_user)`` must scope by the bearer-token user, not
by query / path UUIDs alone.

This file is a thin first cut focused on the auth-me round-trip — the
easiest case to exercise without a full chat / paper / FITS workflow.
The list of cross-user GET endpoints (papers, sessions, FITS uploads)
extends here once each one's owner-scoped query is locked.
"""
from __future__ import annotations

import uuid
import pytest

from app.auth import create_access_token, hash_password
from app.models.schemas import User
from app.utils.usernames import username_from_email


@pytest.fixture
async def two_users(db_session):
    user_a = User(
        id=uuid.uuid4(),
        username=username_from_email("alice@astro.example.com"),
        email="alice@astro.example.com",
        password_hash=hash_password("alicepassword123"),
        subscription_tier="solo",
    )
    user_b = User(
        id=uuid.uuid4(),
        username=username_from_email("bob@astro.example.com"),
        email="bob@astro.example.com",
        password_hash=hash_password("bobpassword123"),
        subscription_tier="solo",
    )
    db_session.add(user_a)
    db_session.add(user_b)
    await db_session.commit()
    await db_session.refresh(user_a)
    await db_session.refresh(user_b)
    token_a = create_access_token(user_a.id)
    token_b = create_access_token(user_b.id)
    return (user_a, token_a), (user_b, token_b)


async def test_auth_me_returns_only_bearer_users_identity(app_client, two_users):
    """A's token must surface A's identity. B's token must surface B's.
    A token must never accidentally surface another user's identity."""
    (user_a, token_a), (user_b, token_b) = two_users

    resp_a = await app_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"})
    assert resp_a.status_code == 200
    body_a = resp_a.json()
    assert body_a["email"] == user_a.email
    assert body_a["email"] != user_b.email

    resp_b = await app_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_b}"})
    assert resp_b.status_code == 200
    body_b = resp_b.json()
    assert body_b["email"] == user_b.email
    assert body_b["email"] != user_a.email


async def test_anonymous_cannot_read_auth_me(app_client):
    """No token → /api/auth/me must reject."""
    resp = await app_client.get("/api/auth/me")
    assert resp.status_code in (401, 403)


async def test_invalid_token_rejected(app_client):
    """A clearly-forged JWT must reject."""
    resp = await app_client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer this.is.not.a.real.jwt"},
    )
    assert resp.status_code in (401, 403)
