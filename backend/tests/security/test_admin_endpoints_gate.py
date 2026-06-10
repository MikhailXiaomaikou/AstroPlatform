"""Security L10 — admin endpoints must reject unauthenticated and
non-admin callers.

Every endpoint mounted under ``/api/admin/*`` declares
``Depends(require_admin_any)``. If a refactor accidentally drops that
dependency, the endpoint would expose telemetry / sandbox diagnostics /
trending data to any logged-in user. These tests gate against that
specific class of regression.
"""
from __future__ import annotations

import pytest


# A representative endpoint from each admin router. Adding a new
# admin router? add a path here too.
ADMIN_GET_ENDPOINTS = [
    "/api/admin/stats/telemetry/tool_usage",
    "/api/admin/sandbox/health",
    "/api/admin/trending/objects",
    "/api/admin/trending/visibility",
]


@pytest.mark.parametrize("path", ADMIN_GET_ENDPOINTS)
async def test_admin_endpoint_rejects_anonymous(app_client, path):
    """No auth header at all -> 401 / 403."""
    resp = await app_client.get(path)
    assert resp.status_code in (401, 403), (
        f"{path} returned {resp.status_code} for anonymous; expected 401/403. "
        f"body[:200]={resp.text[:200]!r}"
    )


@pytest.mark.parametrize("path", ADMIN_GET_ENDPOINTS)
async def test_admin_endpoint_rejects_regular_user_token(app_client, test_user, path):
    """A normal user JWT is NOT an admin token. require_admin_any must
    reject it the same way it rejects anonymous traffic."""
    _user, token = test_user
    resp = await app_client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code in (401, 403), (
        f"{path} accepted a non-admin JWT (status {resp.status_code}). "
        "require_admin_any must reject non-admin user tokens."
    )


async def test_admin_endpoint_with_wrong_secret_rejected(app_client):
    """X-Admin-Secret with a clearly-wrong value -> 401 / 403."""
    resp = await app_client.get(
        ADMIN_GET_ENDPOINTS[0],
        headers={"X-Admin-Secret": "not-the-real-admin-secret"},
    )
    assert resp.status_code in (401, 403)


async def test_subscribe_to_institution_does_not_grant_admin(app_client, test_user):
    """B14: self-serving POST /api/auth/subscribe to the 'institution' billing
    tier must NOT confer admin. A normal user can pick any billing tier, but
    the admin surface stays gated (admin tier / ADMIN_USERNAMES / X-Admin-Secret
    only). Before the fix, require_admin_any treated subscription_tier in
    {admin, institution} as admin, so a normal user could self-escalate."""
    _user, token = test_user
    auth = {"Authorization": f"Bearer {token}"}

    sub = await app_client.post(
        "/api/auth/subscribe", headers=auth, json={"tier": "institution"}
    )
    assert sub.status_code == 200, f"subscribe failed: {sub.status_code} {sub.text[:200]}"

    resp = await app_client.get(ADMIN_GET_ENDPOINTS[0], headers=auth)
    assert resp.status_code in (401, 403), (
        "subscribing to the 'institution' billing tier escalated to admin "
        f"(status {resp.status_code}) — vertical privilege escalation."
    )
