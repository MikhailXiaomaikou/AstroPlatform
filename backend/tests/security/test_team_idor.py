"""Security B13 — team-scoped endpoints must verify membership.

Every ``/api/team/{team_id}/*`` endpoint identifies a team by the owner's user
UUID, which leaks throughout the app (member lists, comments, shared_by fields).
Without a membership check, any authenticated user could read or write another
team's shared notebooks / results / activity by passing the victim owner's
UUID. These tests lock the ``_require_team_access`` guard mirrored from
``ws.py:_authorize_team_member``.
"""
from __future__ import annotations

import uuid

import pytest

from app.auth import create_access_token, hash_password
from app.models.schemas import User
from app.utils.usernames import username_from_email


@pytest.fixture
async def owner_and_outsider(db_session):
    owner = User(
        id=uuid.uuid4(),
        username=username_from_email("teamowner@astro.example.com"),
        email="teamowner@astro.example.com",
        password_hash=hash_password("ownerpassword123"),
        subscription_tier="institution",
    )
    outsider = User(
        id=uuid.uuid4(),
        username=username_from_email("outsider@astro.example.com"),
        email="outsider@astro.example.com",
        password_hash=hash_password("outsiderpassword123"),
        subscription_tier="solo",
    )
    db_session.add(owner)
    db_session.add(outsider)
    await db_session.commit()
    await db_session.refresh(owner)
    await db_session.refresh(outsider)
    return (
        (owner, create_access_token(owner.id)),
        (outsider, create_access_token(outsider.id)),
    )


TEAM_GET_SUBPATHS = ["shared-notebooks", "shared-results", "activity"]


@pytest.mark.parametrize("sub", TEAM_GET_SUBPATHS)
async def test_outsider_cannot_read_another_teams_resources(app_client, owner_and_outsider, sub):
    (owner, _owner_token), (_outsider, outsider_token) = owner_and_outsider
    resp = await app_client.get(
        f"/api/team/{owner.id}/{sub}",
        headers={"Authorization": f"Bearer {outsider_token}"},
    )
    assert resp.status_code == 403, (
        f"/api/team/{{owner}}/{sub} leaked to a non-member (status {resp.status_code}); "
        "membership check missing."
    )


async def test_outsider_cannot_write_to_another_teams_notebooks(app_client, owner_and_outsider):
    (owner, _owner_token), (_outsider, outsider_token) = owner_and_outsider
    resp = await app_client.post(
        f"/api/team/{owner.id}/shared-notebooks",
        headers={"Authorization": f"Bearer {outsider_token}"},
        json={"title": "poison", "content": "injected by an outsider"},
    )
    assert resp.status_code == 403


async def test_owner_can_read_their_own_team(app_client, owner_and_outsider):
    """Guard against over-tightening: the owner (team_id == own UUID) must
    still reach their own team resources."""
    (owner, owner_token), _ = owner_and_outsider
    resp = await app_client.get(
        f"/api/team/{owner.id}/shared-notebooks",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp.status_code == 200
