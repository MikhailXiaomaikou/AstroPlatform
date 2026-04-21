"""Trending section 测试: admin endpoints + visibility 开关 + 公开 endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


async def test_trending_objects_counts_from_result_click_and_viewed(
    app_client, monkeypatch, db_session,
):
    """object_name 来自 search.result_click + object.viewed 两个事件."""
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    from app.models.schemas import UserEvent
    now = datetime.now(timezone.utc)
    # M31 被点 2 次, NGC 4258 被查看 1 次, Pleiades 点 1 + 查 1
    for et, name in [
        ("search.result_click", "M31"),
        ("search.result_click", "M31"),
        ("object.viewed", "NGC 4258"),
        ("search.result_click", "Pleiades"),
        ("object.viewed", "Pleiades"),
        # 非目标事件, 不该被算
        ("search.query", "something"),
        ("ai.message_sent", "chat query"),
    ]:
        db_session.add(UserEvent(
            id=uuid.uuid4(), event_type=et,
            event_data={"object_name": name},
            timestamp=now - timedelta(minutes=5),
        ))
    await db_session.commit()

    r = await app_client.get("/api/admin/trending/objects?period=7d&limit=10")
    assert r.status_code == 200
    counts = {it["object_name"]: it["count"] for it in r.json()["items"]}
    assert counts == {"M31": 2, "Pleiades": 2, "NGC 4258": 1}


async def test_trending_sources_aggregates_databases_list(
    app_client, monkeypatch, db_session,
):
    """search.query.event_data.databases (list) 每个源累加."""
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    from app.models.schemas import UserEvent
    now = datetime.now(timezone.utc)
    db_session.add(UserEvent(
        id=uuid.uuid4(), event_type="search.query",
        event_data={"databases": ["gaia", "sdss"], "object_name": "M31"},
        timestamp=now - timedelta(hours=1),
    ))
    db_session.add(UserEvent(
        id=uuid.uuid4(), event_type="search.query",
        event_data={"databases": ["gaia"], "object_name": "Pleiades"},
        timestamp=now - timedelta(hours=1),
    ))
    # run_adql 也算数据源
    db_session.add(UserEvent(
        id=uuid.uuid4(), event_type="ai.tool_called",
        event_data={"tool_name": "run_adql", "service": "vizier"},
        timestamp=now - timedelta(hours=1),
    ))
    await db_session.commit()

    r = await app_client.get("/api/admin/trending/sources?period=7d")
    assert r.status_code == 200
    counts = {it["source"]: it["count"] for it in r.json()["items"]}
    # gaia 2 + sdss 1 + vizier 1 (来自 ai.tool_called)
    assert counts.get("gaia") == 2
    assert counts.get("sdss") == 1
    assert counts.get("vizier") == 1


async def test_trending_delta_filters_low_count(
    app_client, monkeypatch, db_session,
):
    """本期 count < min_count (默认 10) 不上榜, 避免低基数假上升."""
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    from app.models.schemas import UserEvent
    now = datetime.now(timezone.utc)

    # "Hot" 本期 15 次, 上期 5 次 → 应上榜 (this>=10)
    for _ in range(15):
        db_session.add(UserEvent(
            id=uuid.uuid4(), event_type="search.result_click",
            event_data={"object_name": "Hot"},
            timestamp=now - timedelta(days=1),
        ))
    for _ in range(5):
        db_session.add(UserEvent(
            id=uuid.uuid4(), event_type="search.result_click",
            event_data={"object_name": "Hot"},
            timestamp=now - timedelta(days=9),  # 上一个 7d window
        ))
    # "Tiny" 本期 3 次 vs 上期 0 次 (假的 +inf 增长), count < 10 不上榜
    for _ in range(3):
        db_session.add(UserEvent(
            id=uuid.uuid4(), event_type="search.result_click",
            event_data={"object_name": "Tiny"},
            timestamp=now - timedelta(days=1),
        ))
    await db_session.commit()

    r = await app_client.get("/api/admin/trending/delta?period=7d&min_count=10&dimension=objects")
    assert r.status_code == 200
    items = r.json()["items"]
    keys = [it["key"] for it in items]
    assert "Hot" in keys
    assert "Tiny" not in keys, "低基数对象不该上榜"


async def test_trending_cache_hits(app_client, monkeypatch, db_session):
    """同 period + 同参数 5 分钟内只算一次 (缓存).

    这里测 cache 起效: 调 2 次 /objects, 第 2 次就算我插新数据也返旧结果."""
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    # 清缓存确保干净起点
    from app.api.admin_trending import _CACHE
    _CACHE.clear()

    r1 = await app_client.get("/api/admin/trending/objects?period=7d&limit=10")
    assert r1.status_code == 200
    total_1 = r1.json()["total_events"]

    # 加新 event
    from app.models.schemas import UserEvent
    db_session.add(UserEvent(
        id=uuid.uuid4(), event_type="search.result_click",
        event_data={"object_name": "AfterCache"},
        timestamp=datetime.now(timezone.utc),
    ))
    await db_session.commit()

    r2 = await app_client.get("/api/admin/trending/objects?period=7d&limit=10")
    assert r2.json()["total_events"] == total_1, (
        "5min cache 未命中, 新数据不该在第 2 次出现"
    )


async def test_visibility_toggle(app_client, monkeypatch):
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")

    # 初始所有 false
    r = await app_client.get("/api/admin/trending/visibility")
    assert r.status_code == 200
    vis = r.json()["visibility"]
    assert set(vis.keys()) == {"objects", "sources", "delta"}
    assert all(v is False for v in vis.values())

    # 打开 objects
    r = await app_client.post(
        "/api/admin/trending/visibility",
        json={"key": "objects", "is_public": True},
    )
    assert r.status_code == 200

    # 再查
    r = await app_client.get("/api/admin/trending/visibility")
    vis = r.json()["visibility"]
    assert vis["objects"] is True
    assert vis["sources"] is False

    # 错 key 拒
    r = await app_client.post(
        "/api/admin/trending/visibility",
        json={"key": "bogus", "is_public": True},
    )
    assert r.status_code == 400


async def test_public_trending_respects_visibility(app_client, monkeypatch, db_session):
    """/api/trending/public 不需要 auth, 但只返被 admin 打开的那些."""
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    # 清 cache 避免上一个测试污染
    from app.api.admin_trending import _CACHE
    _CACHE.clear()

    from app.models.schemas import UserEvent
    now = datetime.now(timezone.utc)
    for name in ("M31", "M31", "Pleiades"):
        db_session.add(UserEvent(
            id=uuid.uuid4(), event_type="search.result_click",
            event_data={"object_name": name},
            timestamp=now - timedelta(hours=1),
        ))
    await db_session.commit()

    # 默认没开任何一个 → public 返回空 payload
    r = await app_client.get("/api/trending/public")
    assert r.status_code == 200
    body = r.json()
    assert body["visibility"] == {"objects": False, "sources": False, "delta": False}
    assert "objects" not in body

    # 打开 objects
    await app_client.post(
        "/api/admin/trending/visibility",
        json={"key": "objects", "is_public": True},
    )
    r = await app_client.get("/api/trending/public")
    body = r.json()
    assert body["visibility"]["objects"] is True
    assert "objects" in body and len(body["objects"]) >= 1
    # sources / delta 没开 → 仍不返
    assert "sources" not in body
    assert "delta" not in body


async def test_public_trending_no_auth_required(app_client):
    """/api/trending/public 任何访客都能 GET, 不需要 X-Admin-Secret."""
    # 不设 admin_secret, 不设 ENV=dev 都可以
    r = await app_client.get("/api/trending/public")
    # 应该成功, 不是 403
    assert r.status_code == 200


async def test_admin_trending_requires_auth(app_client, monkeypatch):
    """admin /api/admin/trending/* 必须带 X-Admin-Secret 或 admin JWT."""
    monkeypatch.setenv("ENV", "production")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "test-xyz")

    for path in ("/api/admin/trending/objects", "/api/admin/trending/sources",
                 "/api/admin/trending/delta", "/api/admin/trending/visibility"):
        r = await app_client.get(path)
        assert r.status_code == 403, f"{path} 无 auth 应 403"
