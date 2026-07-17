"""N'-1: /api/admin/stats/* + dual auth + CORS null tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


# ── 1. CORS null-origin policy ──

def test_cors_origins_includes_null_in_development(monkeypatch):
    from app.cors import get_cors_origins

    monkeypatch.setenv("ENV", "dev")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    origins = get_cors_origins()
    assert "null" in origins, (
        "桌面 admin HTML 双击打开时浏览器发 Origin: null, "
        "本地开发默认允许"
    )


def test_cors_origins_requires_explicit_null_opt_in_in_production(monkeypatch):
    from app.cors import get_cors_origins

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://astro.example")
    assert get_cors_origins() == ["https://astro.example"]

    monkeypatch.setenv("CORS_ORIGINS", "https://astro.example,null")
    assert get_cors_origins() == ["https://astro.example", "null"]


# ── 2. require_admin_any dual paths ──────────────────────────────────────

async def test_admin_stats_kpi_with_admin_secret(app_client, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "test-secret-z")

    # no header → 403
    r = await app_client.get("/api/admin/stats/kpi")
    assert r.status_code == 403

    # wrong header → 403
    r = await app_client.get(
        "/api/admin/stats/kpi", headers={"X-Admin-Secret": "wrong"}
    )
    assert r.status_code == 403

    # correct header → 200
    r = await app_client.get(
        "/api/admin/stats/kpi", headers={"X-Admin-Secret": "test-secret-z"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("event_total", "unique_users", "inference_calls",
                "inference_cost_usd", "comments_visible", "period"):
        assert key in body


async def test_deleted_admin_jwt_is_rejected(app_client, db_session, monkeypatch):
    from app.auth import create_access_token, hash_password
    from app.config import settings
    from app.models.schemas import User

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ADMIN_USERNAMES", "deleted-admin")
    monkeypatch.setattr(settings, "admin_secret", "")
    admin = User(
        id=uuid.uuid4(),
        username="deleted-admin",
        email="deleted-admin@example.invalid",
        password_hash=hash_password("securepassword123"),
        subscription_tier="admin",
        account_status="DELETION_PENDING",
    )
    db_session.add(admin)
    await db_session.commit()

    response = await app_client.get(
        "/api/admin/stats/kpi",
        headers={"Authorization": f"Bearer {create_access_token(admin.id)}"},
    )
    assert response.status_code == 403


async def test_admin_stats_dev_bypass(app_client, monkeypatch):
    """ENV=dev + empty admin_secret → bypass allowed (developer experience)"""
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    r = await app_client.get("/api/admin/stats/kpi")
    assert r.status_code == 200


# ── 3. by-tool actually aggregates from event_data.tool_name ──────────────────────

async def test_admin_stats_by_tool_counts_correctly(app_client, monkeypatch, db_session):
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    from app.models.schemas import UserEvent

    # 3 ai.tool_called events: query_gaia_cluster x2, run_adql x1
    now = datetime.now(timezone.utc)
    for tool in ("query_gaia_cluster", "query_gaia_cluster", "run_adql"):
        db_session.add(UserEvent(
            id=uuid.uuid4(),
            event_type="ai.tool_called",
            event_data={"tool_name": tool, "agent_name": "research"},
            timestamp=now - timedelta(hours=1),
        ))
    # one non-ai.tool_called event → should not be counted
    db_session.add(UserEvent(
        id=uuid.uuid4(),
        event_type="ai.message_sent",
        event_data={"tool_name": "should_not_appear"},
        timestamp=now - timedelta(hours=1),
    ))
    await db_session.commit()

    r = await app_client.get("/api/admin/stats/by-tool?period=7d")
    assert r.status_code == 200
    body = r.json()
    items = {it["tool_name"]: it["count"] for it in body["items"]}
    assert items.get("query_gaia_cluster") == 2
    assert items.get("run_adql") == 1
    assert "should_not_appear" not in items


async def test_admin_stats_telemetry_tool_usage_dump(app_client, monkeypatch, db_session):
    """Stage 6 P0c-F (2026-05-19): telemetry/tool_usage returns an enriched distribution,
    including pct_of_total + low_usage flag + low_usage_tools summary (for deciding which tools to cut)."""
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    from app.models.schemas import UserEvent

    now = datetime.now(timezone.utc)
    # 200 popular_tool + 1 rare_tool → rare is 0.5%, low usage
    for _ in range(200):
        db_session.add(UserEvent(
            id=uuid.uuid4(),
            event_type="ai.tool_called",
            event_data={"tool_name": "popular_tool"},
            timestamp=now - timedelta(hours=1),
        ))
    db_session.add(UserEvent(
        id=uuid.uuid4(),
        event_type="ai.tool_called",
        event_data={"tool_name": "rare_tool"},
        timestamp=now - timedelta(hours=1),
    ))
    await db_session.commit()

    r = await app_client.get("/api/admin/stats/telemetry/tool_usage?period=7d")
    assert r.status_code == 200
    body = r.json()
    assert body["total_calls"] == 201
    items = {it["tool_name"]: it for it in body["items"]}
    assert items["popular_tool"]["count"] == 200
    assert items["popular_tool"]["low_usage"] is False
    assert items["rare_tool"]["count"] == 1
    assert items["rare_tool"]["low_usage"] is True
    assert "rare_tool" in body["low_usage_tools"]
    assert "popular_tool" not in body["low_usage_tools"]


# ── 4. by-page aggregation ─────────────────────────────────────────────────

async def test_admin_stats_by_page(app_client, monkeypatch, db_session):
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    from app.models.schemas import UserEvent
    now = datetime.now(timezone.utc)
    for page in ("/chat", "/chat", "/admin", None):
        db_session.add(UserEvent(
            id=uuid.uuid4(),
            event_type="session.page_view",
            event_data={},
            page=page,
            timestamp=now - timedelta(minutes=10),
        ))
    await db_session.commit()

    r = await app_client.get("/api/admin/stats/by-page?period=7d")
    assert r.status_code == 200
    pages = {it["page"]: it["count"] for it in r.json()["items"]}
    # None page should not appear in the results
    assert None not in pages
    assert pages.get("/chat") == 2
    assert pages.get("/admin") == 1


# ── 5. timeline time buckets ─────────────────────────────────────────────

async def test_admin_stats_timeline_buckets_by_day(app_client, monkeypatch, db_session):
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    from app.models.schemas import UserEvent
    now = datetime.now(timezone.utc)
    db_session.add(UserEvent(
        id=uuid.uuid4(), event_type="search.query", event_data={},
        timestamp=now - timedelta(hours=1),
    ))
    db_session.add(UserEvent(
        id=uuid.uuid4(), event_type="ai.message_sent", event_data={},
        timestamp=now - timedelta(hours=1),
    ))
    await db_session.commit()

    r = await app_client.get("/api/admin/stats/timeline?period=7d&bucket=day")
    assert r.status_code == 200
    body = r.json()
    assert body["bucket"] == "day"
    assert len(body["series"]) >= 1
    # must contain at least search.query / ai.message_sent two types
    all_types = set()
    for entry in body["series"]:
        all_types.update(entry["counts"].keys())
    assert "search.query" in all_types
    assert "ai.message_sent" in all_types


async def test_admin_stats_timeline_auto_picks_hour_for_short_period(app_client, monkeypatch):
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    r = await app_client.get("/api/admin/stats/timeline?period=24h&bucket=auto")
    assert r.status_code == 200
    assert r.json()["bucket"] == "hour"

    r = await app_client.get("/api/admin/stats/timeline?period=30d&bucket=auto")
    assert r.status_code == 200
    assert r.json()["bucket"] == "day"


# ── 6. comments stats ──────────────────────────────────────────────

async def test_admin_stats_comments(app_client, monkeypatch, db_session):
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")

    # create 2 visible + 1 hidden
    await app_client.post("/api/comments", json={
        "author_name": "x", "content": "visible comment 1 long enough",
    })
    await app_client.post("/api/comments", json={
        "author_name": "y", "content": "visible comment 2 long enough",
    })
    r3 = await app_client.post("/api/comments", json={
        "author_name": "z", "content": "to be hidden long enough",
    })
    cid_to_hide = r3.json()["id"]
    # soft-delete one comment
    await app_client.delete(f"/api/comments/{cid_to_hide}")

    r = await app_client.get("/api/admin/stats/comments?period=30d")
    assert r.status_code == 200
    body = r.json()
    assert body["total_visible"] == 2
    assert body["total_hidden"] == 1
    assert isinstance(body["per_day"], list)


# ── 7. existing /api/admin/events/stats still accepts X-Admin-Secret (updated to require_admin_any) ──

async def test_existing_events_stats_accepts_admin_secret(app_client, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "test-secret-events")

    r = await app_client.get(
        "/api/admin/events/stats",
        headers={"X-Admin-Secret": "test-secret-events"},
    )
    assert r.status_code == 200
    assert "event_counts" in r.json()


async def test_events_track_endpoint_rejects_anonymous_analytics(app_client, monkeypatch):
    from app.api import events

    captured = []

    async def fake_track(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(events.event_collector, "track", fake_track)

    r = await app_client.post(
        "/api/events/track",
        json={
            "event_type": "session.started",
            "event_data": {"referrer": ""},
            "session_id": "11111111-1111-4111-8111-111111111111",
            "page": "/",
        },
    )

    assert r.status_code == 200, r.text
    assert r.json() == {"tracked": False, "reason": "consent_required"}
    assert captured == []


async def test_events_track_endpoint_rejects_unsupported_type(app_client, monkeypatch):
    from app.api import events

    async def fake_track(**kwargs):
        raise AssertionError("unsupported events should be rejected before tracking")

    monkeypatch.setattr(events.event_collector, "track", fake_track)

    r = await app_client.post(
        "/api/events/track",
        json={"event_type": "frontend.unknown", "event_data": {}},
    )

    assert r.status_code == 400
    assert r.json()["detail"] == "Unsupported event type"


async def test_events_track_endpoint_allows_html_export_event(app_client, monkeypatch):
    from app.api import events

    captured = []

    async def fake_track(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(events.event_collector, "track", fake_track)

    registration = await app_client.post(
        "/api/auth/register",
        json={"username": "analytics-user", "password": "password123"},
    )
    token = registration.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    consent = await app_client.put(
        "/api/privacy/preferences",
        json={"analytics_enabled": True},
        headers=headers,
    )
    assert consent.status_code == 200

    r = await app_client.post(
        "/api/events/track",
        json={"event_type": "export.html", "event_data": {"message_count": 2}},
        headers=headers,
    )

    assert r.status_code == 200, r.text
    assert r.json() == {"tracked": True}
    assert captured[0]["event_type"] == "export.html"
    assert captured[0]["consent_verified"] is True
