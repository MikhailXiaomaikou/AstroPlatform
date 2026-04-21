"""N'-1: /api/admin/stats/* + 双 auth + CORS null 测试."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


# ── 1. CORS null origin 允许 (桌面 HTML 双击打开浏览器发 Origin: null) ──

def test_cors_origins_includes_null():
    from app.cors import get_cors_origins
    origins = get_cors_origins()
    assert "null" in origins, (
        "桌面 admin HTML 双击打开时浏览器发 Origin: null, "
        "CORS 必须显式允许"
    )


# ── 2. require_admin_any 双路径 ──────────────────────────────────────

async def test_admin_stats_kpi_with_admin_secret(app_client, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "test-secret-z")

    # 没 header → 403
    r = await app_client.get("/api/admin/stats/kpi")
    assert r.status_code == 403

    # 错 header → 403
    r = await app_client.get(
        "/api/admin/stats/kpi", headers={"X-Admin-Secret": "wrong"}
    )
    assert r.status_code == 403

    # 正确 header → 200
    r = await app_client.get(
        "/api/admin/stats/kpi", headers={"X-Admin-Secret": "test-secret-z"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("event_total", "unique_users", "inference_calls",
                "inference_cost_usd", "comments_visible", "period"):
        assert key in body


async def test_admin_stats_dev_bypass(app_client, monkeypatch):
    """ENV=dev + admin_secret 空 → 旁路放行 (开发体验)"""
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    r = await app_client.get("/api/admin/stats/kpi")
    assert r.status_code == 200


# ── 3. by-tool 真的从 event_data.tool_name 聚合 ──────────────────────

async def test_admin_stats_by_tool_counts_correctly(app_client, monkeypatch, db_session):
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    from app.models.schemas import UserEvent

    # 注 3 条 ai.tool_called: query_gaia_cluster x2, run_adql x1
    now = datetime.now(timezone.utc)
    for tool in ("query_gaia_cluster", "query_gaia_cluster", "run_adql"):
        db_session.add(UserEvent(
            id=uuid.uuid4(),
            event_type="ai.tool_called",
            event_data={"tool_name": tool, "agent_name": "research"},
            timestamp=now - timedelta(hours=1),
        ))
    # 一条非 ai.tool_called → 不该被算
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


# ── 4. by-page 聚合 ─────────────────────────────────────────────────

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
    # None page 不该在结果里
    assert None not in pages
    assert pages.get("/chat") == 2
    assert pages.get("/admin") == 1


# ── 5. timeline 时间桶 ─────────────────────────────────────────────

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
    # 至少含 search.query / ai.message_sent 两个 type
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

    # 创建 2 visible + 1 hidden
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
    # 软删一条
    await app_client.delete(f"/api/comments/{cid_to_hide}")

    r = await app_client.get("/api/admin/stats/comments?period=30d")
    assert r.status_code == 200
    body = r.json()
    assert body["total_visible"] == 2
    assert body["total_hidden"] == 1
    assert isinstance(body["per_day"], list)


# ── 7. 现有 /api/admin/events/stats 仍接受 X-Admin-Secret (改 require_admin_any) ──

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
