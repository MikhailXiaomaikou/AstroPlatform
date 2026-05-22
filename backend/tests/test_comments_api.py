"""Public comments API tests.

Coverage:
- POST /api/comments: valid / missing fields / too long / blank
- GET /api/comments: reverse order / pagination / has_more / soft-deleted not returned
- DELETE /api/comments/{id}: no secret rejected / non-UUID rejected / normal delete

Uses the project's existing conftest.app_client fixture to reuse in-memory SQLite + dep override.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.auth import create_access_token
from app.rate_limit import get_rate_limit_key


async def test_post_and_get_comment_roundtrip(app_client):
    """Basic happy path: POST → immediately visible in GET."""
    r = await app_client.post("/api/comments", json={
        "author_name": "Alice",
        "content": "Great platform, very useful for research!",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["author_name"] == "Alice"
    assert body["content"] == "Great platform, very useful for research!"
    assert "id" in body
    comment_id = body["id"]

    r2 = await app_client.get("/api/comments?limit=50")
    assert r2.status_code == 200
    listing = r2.json()
    assert any(c["id"] == comment_id for c in listing["comments"])


async def test_post_rejects_missing_fields(app_client):
    r = await app_client.post("/api/comments", json={"author_name": "Bob"})
    assert r.status_code == 422

    r = await app_client.post("/api/comments", json={"content": "x" * 50})
    assert r.status_code == 422


async def test_post_rejects_overlong_content(app_client):
    r = await app_client.post("/api/comments", json={
        "author_name": "Bob",
        "content": "x" * 501,  # max 500
    })
    assert r.status_code == 422


async def test_post_rejects_blank_after_trim(app_client):
    r = await app_client.post("/api/comments", json={
        "author_name": "   ",
        "content": "this is fine",
    })
    assert r.status_code == 422


async def test_delete_without_admin_secret_rejected(app_client, monkeypatch):
    # simulate production environment (ENV=production + empty ADMIN_SECRET → fail closed)
    monkeypatch.setenv("ENV", "production")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")

    r = await app_client.post("/api/comments", json={
        "author_name": "Carol",
        "content": "test comment for deletion auth",
    })
    assert r.status_code == 201
    cid = r.json()["id"]

    # no X-Admin-Secret → 403
    r2 = await app_client.delete(f"/api/comments/{cid}")
    assert r2.status_code == 403


async def test_delete_invalid_uuid_rejected(app_client, monkeypatch):
    # _require_admin fail-closed: ENV=dev is required to bypass the check.
    monkeypatch.setenv("ENV", "dev")
    r = await app_client.delete("/api/comments/not-a-uuid")
    assert r.status_code == 400


async def test_delete_then_comment_disappears_from_list(app_client, monkeypatch):
    monkeypatch.setenv("ENV", "dev")
    r = await app_client.post("/api/comments", json={
        "author_name": "Dave",
        "content": "to be soft-deleted in test",
    })
    assert r.status_code == 201
    cid = r.json()["id"]

    r2 = await app_client.delete(f"/api/comments/{cid}")
    assert r2.status_code == 204, r2.text

    r3 = await app_client.get("/api/comments?limit=100")
    ids = [c["id"] for c in r3.json()["comments"]]
    assert cid not in ids


async def test_delete_with_matching_admin_secret(app_client, monkeypatch):
    # Production-like: ENV=production + admin_secret set + correct header provided
    monkeypatch.setenv("ENV", "production")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "test-secret-xyz")

    r = await app_client.post("/api/comments", json={
        "author_name": "Fay",
        "content": "comment to delete with proper admin header",
    })
    assert r.status_code == 201
    cid = r.json()["id"]

    # wrong header → 403
    r_bad = await app_client.delete(
        f"/api/comments/{cid}", headers={"X-Admin-Secret": "wrong"}
    )
    assert r_bad.status_code == 403

    # correct header → 204
    r_ok = await app_client.delete(
        f"/api/comments/{cid}", headers={"X-Admin-Secret": "test-secret-xyz"}
    )
    assert r_ok.status_code == 204


async def test_list_pagination_and_has_more(app_client):
    for i in range(3):
        r = await app_client.post("/api/comments", json={
            "author_name": f"Pagination-{i}",
            "content": f"pagination test comment {i} with enough characters",
        })
        assert r.status_code == 201

    r = await app_client.get("/api/comments?limit=1&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert len(body["comments"]) == 1
    assert body["total"] >= 3
    assert body["has_more"] is True


async def test_list_default_order_newest_first(app_client):
    r1 = await app_client.post("/api/comments", json={
        "author_name": "Order-Early",
        "content": "early comment for order check",
    })
    assert r1.status_code == 201
    early_id = r1.json()["id"]

    await asyncio.sleep(0.05)

    r2 = await app_client.post("/api/comments", json={
        "author_name": "Order-Late",
        "content": "late comment for order check",
    })
    assert r2.status_code == 201
    late_id = r2.json()["id"]

    r = await app_client.get("/api/comments?limit=50")
    ids = [c["id"] for c in r.json()["comments"]]
    assert ids.index(late_id) < ids.index(early_id)


async def test_ip_not_exposed_in_public_view(app_client):
    r = await app_client.post("/api/comments", json={
        "author_name": "Edward",
        "content": "check that IP is not leaked in public response",
    })
    assert r.status_code == 201
    body = r.json()
    assert "client_ip" not in body
    assert "ip" not in body

    r2 = await app_client.get("/api/comments")
    for c in r2.json()["comments"]:
        assert "client_ip" not in c


def test_guest_rate_limit_key_prefers_forwarded_ip():
    request = SimpleNamespace(
        headers={
            "X-Forwarded-For": "203.0.113.10, 10.0.0.1",
        },
        client=SimpleNamespace(host="10.0.0.99"),
    )

    assert get_rate_limit_key(request) == "ip:203.0.113.10"


def test_authenticated_rate_limit_key_still_uses_user_id():
    token = create_access_token("user-123")
    request = SimpleNamespace(
        headers={
            "Authorization": f"Bearer {token}",
            "X-Forwarded-For": "203.0.113.10",
        },
        client=SimpleNamespace(host="10.0.0.99"),
    )

    assert get_rate_limit_key(request) == "user:user-123"
