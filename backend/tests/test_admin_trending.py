"""Trending section tests: admin endpoints + visibility toggle + public endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


async def test_trending_objects_counts_from_result_click_and_viewed(
    app_client, monkeypatch, db_session,
):
    """object_name is sourced from search.result_click + object.viewed events."""
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    from app.models.schemas import UserEvent
    now = datetime.now(timezone.utc)
    # M31 clicked 2 times, NGC 4258 viewed 1 time, Pleiades clicked 1 + viewed 1
    for et, name in [
        ("search.result_click", "M31"),
        ("search.result_click", "M31"),
        ("object.viewed", "NGC 4258"),
        ("search.result_click", "Pleiades"),
        ("object.viewed", "Pleiades"),
        # non-target events, should not be counted
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
    """search.query.event_data.databases (list) accumulates each source."""
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
    # run_adql also counts as a data source
    db_session.add(UserEvent(
        id=uuid.uuid4(), event_type="ai.tool_called",
        event_data={"tool_name": "run_adql", "service": "vizier"},
        timestamp=now - timedelta(hours=1),
    ))
    await db_session.commit()

    r = await app_client.get("/api/admin/trending/sources?period=7d")
    assert r.status_code == 200
    counts = {it["source"]: it["count"] for it in r.json()["items"]}
    # gaia 2 + sdss 1 + vizier 1 (from ai.tool_called)
    assert counts.get("gaia") == 2
    assert counts.get("sdss") == 1
    assert counts.get("vizier") == 1


async def test_trending_delta_filters_low_count(
    app_client, monkeypatch, db_session,
):
    """Current-period count < min_count (default 10) is excluded from ranking to avoid false spikes from low base counts."""
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    from app.models.schemas import UserEvent
    now = datetime.now(timezone.utc)

    # "Hot" current period 15 times, previous period 5 times → should rank (this>=10)
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
            timestamp=now - timedelta(days=9),  # previous 7d window
        ))
    # "Tiny" current period 3 times vs previous period 0 (fake +inf growth), count < 10 excluded
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
    """Same period + same parameters: computed only once within 5 minutes (cache).

    Tests that the cache is effective: calling /objects twice, the second call returns
    stale results even after new data is inserted."""
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    # clear cache to ensure a clean starting point
    from app.api.admin_trending import _CACHE
    _CACHE.clear()

    r1 = await app_client.get("/api/admin/trending/objects?period=7d&limit=10")
    assert r1.status_code == 200
    total_1 = r1.json()["total_events"]

    # insert a new event
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

    # initially all false
    r = await app_client.get("/api/admin/trending/visibility")
    assert r.status_code == 200
    vis = r.json()["visibility"]
    assert set(vis.keys()) == {"objects", "sources", "delta"}
    assert all(v is False for v in vis.values())

    # enable objects
    r = await app_client.post(
        "/api/admin/trending/visibility",
        json={"key": "objects", "is_public": True},
    )
    assert r.status_code == 200

    # check again
    r = await app_client.get("/api/admin/trending/visibility")
    vis = r.json()["visibility"]
    assert vis["objects"] is True
    assert vis["sources"] is False

    # invalid key rejected
    r = await app_client.post(
        "/api/admin/trending/visibility",
        json={"key": "bogus", "is_public": True},
    )
    assert r.status_code == 400


async def test_public_trending_respects_visibility(app_client, monkeypatch, db_session):
    """/api/trending/public requires no auth, but only returns the sections enabled by admin."""
    monkeypatch.setenv("ENV", "dev")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    # clear cache to avoid pollution from the previous test
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

    # no section enabled by default → public returns empty payload
    r = await app_client.get("/api/trending/public")
    assert r.status_code == 200
    body = r.json()
    assert body["visibility"] == {"objects": False, "sources": False, "delta": False}
    assert "objects" not in body

    # enable objects
    await app_client.post(
        "/api/admin/trending/visibility",
        json={"key": "objects", "is_public": True},
    )
    r = await app_client.get("/api/trending/public")
    body = r.json()
    assert body["visibility"]["objects"] is True
    assert "objects" in body and len(body["objects"]) >= 1
    # sources / delta not enabled → still not returned
    assert "sources" not in body
    assert "delta" not in body


async def test_public_trending_no_auth_required(app_client):
    """/api/trending/public can be GET by any visitor, no X-Admin-Secret required."""
    # neither admin_secret nor ENV=dev is required
    r = await app_client.get("/api/trending/public")
    # should succeed, not 403
    assert r.status_code == 200


async def test_admin_trending_requires_auth(app_client, monkeypatch):
    """admin /api/admin/trending/* must carry X-Admin-Secret or admin JWT."""
    monkeypatch.setenv("ENV", "production")
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "test-xyz")

    for path in ("/api/admin/trending/objects", "/api/admin/trending/sources",
                 "/api/admin/trending/delta", "/api/admin/trending/visibility"):
        r = await app_client.get(path)
        assert r.status_code == 403, f"{path} 无 auth 应 403"
