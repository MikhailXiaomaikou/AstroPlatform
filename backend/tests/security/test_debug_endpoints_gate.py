"""Security L10 — diagnostic / debug endpoints must NOT be reachable
without admin credentials.

The /api/chat/_debug_last_prompt endpoint dumps the latest fully-rendered
LLM prompt for a session. That prompt may contain user-attached API
keys, FITS file paths, account-specific data, or paper drafts. It is
``Depends(require_admin_any)`` at definition time — these tests guard
against an accidental loosening of that gate.
"""
from __future__ import annotations

import pytest


DEBUG_ENDPOINTS = [
    "/api/chat/_debug_last_prompt",
]


@pytest.mark.parametrize("path", DEBUG_ENDPOINTS)
async def test_debug_endpoint_rejects_anonymous(app_client, path):
    resp = await app_client.get(path)
    assert resp.status_code in (401, 403, 404), (
        f"{path} returned {resp.status_code} for anonymous; expected 401/403/404."
    )


@pytest.mark.parametrize("path", DEBUG_ENDPOINTS)
async def test_debug_endpoint_rejects_regular_user(app_client, test_user, path):
    _user, token = test_user
    resp = await app_client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code in (401, 403), (
        f"{path} accepted a non-admin user token (status {resp.status_code})."
    )


@pytest.mark.parametrize("path", DEBUG_ENDPOINTS)
async def test_debug_endpoint_rejects_wrong_admin_secret(app_client, path):
    resp = await app_client.get(path, headers={"X-Admin-Secret": "not-the-real-secret"})
    assert resp.status_code in (401, 403)
