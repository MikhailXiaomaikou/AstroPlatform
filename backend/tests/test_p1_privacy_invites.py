"""P1 invitation, consent, retention, and account-erasure contracts."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.auth import create_access_token, hash_password
from app.models.claim_audit_records import (
    AccountDeletionTombstone,
    Invitation,
    PrivacyPreference,
)
from app.models.schemas import DataFile, SetupKey, User, UserEvent
from app.services.account_deletion import erase_account_data
from app.services.event_collector import purge_expired_product_events, scrub_event_data
from app.storage import download_fits, upload_fits
from app.utils.usernames import internal_email_for_username


async def _register(app_client, username: str, password: str = "password123"):
    response = await app_client.post(
        "/api/auth/register",
        json={"username": username, "password": password},
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


async def test_invite_only_uses_hashed_one_time_invites(
    app_client,
    db_session,
    monkeypatch,
):
    from app.api import auth

    monkeypatch.setattr(auth.settings, "signup_mode", "invite_only")
    monkeypatch.setattr(auth.settings, "admin_secret", "invite-admin-secret")
    blocked = await app_client.post(
        "/api/auth/register",
        json={"username": "open-signup", "password": "password123"},
    )
    assert blocked.status_code == 403

    created = await app_client.post(
        "/api/auth/invitations",
        json={"count": 1, "label": "alpha", "expires_in_days": 7},
        headers={"X-Admin-Secret": "invite-admin-secret"},
    )
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    assert created.json()["link_format"] == "/auth#invite=<invitation-key>"
    raw_key = created.json()["invitations"][0]
    stored = (await db_session.execute(select(Invitation))).scalar_one()
    assert stored.key_hash != raw_key
    assert raw_key not in stored.key_hash

    listed = await app_client.get(
        "/api/auth/invitations",
        headers={"X-Admin-Secret": "invite-admin-secret"},
    )
    assert listed.status_code == 200
    assert raw_key not in listed.text

    redeemed = await app_client.post(
        "/api/auth/invitations/redeem",
        json={
            "invitation_key": raw_key,
            "username": "invited-user",
            "password": "newpassword123",
        },
    )
    assert redeemed.status_code == 200, redeemed.text
    reused = await app_client.post(
        "/api/auth/invitations/redeem",
        json={
            "invitation_key": raw_key,
            "username": "second-user",
            "password": "newpassword123",
        },
    )
    assert reused.status_code == 401
    login = await app_client.post(
        "/api/auth/login",
        json={"username": "invited-user", "password": "newpassword123"},
    )
    assert login.status_code == 200


async def test_redeemed_setup_key_cannot_login_and_can_migrate_once(
    app_client,
    db_session,
    monkeypatch,
):
    from app.api import auth

    monkeypatch.setattr(auth.settings, "signup_mode", "invite_only")
    monkeypatch.setattr(auth.settings, "admin_secret", "migration-admin")
    legacy_user = User(
        id=uuid.uuid4(),
        username="legacy-random-user",
        email=internal_email_for_username("legacy-random-user"),
        password_hash=hash_password(uuid.uuid4().hex),
        subscription_tier="solo",
    )
    setup_key = SetupKey(
        id=uuid.uuid4(),
        key="ASTRO-LEGACY-ONCE",
        label="legacy",
        used_by=legacy_user.id,
        used_at=datetime.now(timezone.utc),
    )
    db_session.add_all([legacy_user, setup_key])
    await db_session.commit()

    blocked = await app_client.post(
        "/api/auth/setup-key-login",
        json={"setup_key": setup_key.key},
    )
    assert blocked.status_code == 410

    listed = await app_client.get(
        "/api/auth/setup-keys",
        headers={"X-Admin-Secret": "migration-admin"},
    )
    assert listed.status_code == 200, listed.text
    listed_setup_key = next(
        row for row in listed.json() if row["key"] == setup_key.key
    )
    assert listed_setup_key == {
        "id": str(setup_key.id),
        "key": setup_key.key,
        "label": "legacy",
        "used": True,
        "used_by_email": legacy_user.email,
    }

    migration = await app_client.post(
        "/api/auth/migration-invitations",
        json={"setup_key_id": listed_setup_key["id"], "expires_in_days": 7},
        headers={"X-Admin-Secret": "migration-admin"},
    )
    assert migration.status_code == 201, migration.text
    assert migration.headers["cache-control"] == "no-store"
    assert migration.json()["link_format"] == "/auth#invite=<invitation-key>"
    raw_key = migration.json()["invitation"]
    duplicate = await app_client.post(
        "/api/auth/migration-invitations",
        json={"setup_key_id": str(setup_key.id), "expires_in_days": 7},
        headers={"X-Admin-Secret": "migration-admin"},
    )
    assert duplicate.status_code == 409
    redeemed = await app_client.post(
        "/api/auth/invitations/redeem",
        json={
            "invitation_key": raw_key,
            "username": "legacy-normal-user",
            "password": "normalpassword123",
        },
    )
    assert redeemed.status_code == 200, redeemed.text
    after_migration = await app_client.post(
        "/api/auth/migration-invitations",
        json={"setup_key_id": str(setup_key.id), "expires_in_days": 7},
        headers={"X-Admin-Secret": "migration-admin"},
    )
    assert after_migration.status_code == 409
    login = await app_client.post(
        "/api/auth/login",
        json={
            "username": "legacy-normal-user",
            "password": "normalpassword123",
        },
    )
    assert login.status_code == 200


async def test_analytics_requires_consent_and_revocation_purges_events(
    app_client,
    db_session,
):
    token = await _register(app_client, "privacy-user")
    headers = {"Authorization": f"Bearer {token}"}
    denied = await app_client.post(
        "/api/events/track",
        json={"event_type": "session.started", "event_data": {}},
        headers=headers,
    )
    assert denied.json() == {"tracked": False, "reason": "consent_required"}

    enabled = await app_client.put(
        "/api/privacy/preferences",
        json={"analytics_enabled": True},
        headers=headers,
    )
    assert enabled.status_code == 200
    assert enabled.json()["analytics_enabled"] is True
    user_id = uuid.UUID((await app_client.get("/api/auth/me", headers=headers)).json()["id"])
    db_session.add(
        UserEvent(
            id=uuid.uuid4(),
            user_id=user_id,
            event_type="session.started",
            event_data={},
        )
    )
    await db_session.commit()

    disabled = await app_client.put(
        "/api/privacy/preferences",
        json={"analytics_enabled": False},
        headers=headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["analytics_enabled"] is False
    assert await db_session.scalar(
        select(UserEvent.id).where(UserEvent.user_id == user_id)
    ) is None
    preference = await db_session.get(PrivacyPreference, user_id)
    assert preference is not None and preference.analytics_enabled is False


def test_claim_analytics_scrubber_drops_research_content_and_numbers():
    cleaned = scrub_event_data(
        "claim_audit.completed",
        {
            "claim": "H0 = 70",
            "doi": "10.0000/private",
            "url": "https://example.invalid/paper",
            "omega_m": 0.3,
            "tool_params": {"seed": 1},
            "tool_count_bucket": "1-3",
            "outcome_bucket": "withheld",
        },
    )
    assert cleaned == {
        "tool_count_bucket": "1-3",
        "outcome_bucket": "withheld",
    }


async def test_product_event_retention_is_bounded(db_session):
    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            UserEvent(
                id=uuid.uuid4(),
                event_type="session.started",
                event_data={},
                timestamp=now - timedelta(days=31),
            ),
            UserEvent(
                id=uuid.uuid4(),
                event_type="session.started",
                event_data={},
                timestamp=now - timedelta(days=1),
            ),
        ]
    )
    await db_session.commit()
    assert await purge_expired_product_events(retention_days=30, db=db_session) == 1
    remaining = (await db_session.execute(select(UserEvent))).scalars().all()
    assert len(remaining) == 1


async def test_account_delete_disables_immediately_erases_data_and_blocks_restore(
    app_client,
    db_session,
    monkeypatch,
    tmp_path,
):
    from app.api import privacy
    from app.config import settings
    from app.pipeline import engine as pipeline_engine

    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "storage_require_integrity", True)
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "objects"))
    monkeypatch.setattr(privacy, "_dispatch_account_erasure", lambda *_: False)
    monkeypatch.setattr(
        pipeline_engine,
        "delete_owner_pipeline_cache_sync",
        lambda *_args, **_kwargs: 0,
    )

    token = await _register(app_client, "delete-me", "deletepassword123")
    headers = {"Authorization": f"Bearer {token}"}
    me = await app_client.get("/api/auth/me", headers=headers)
    user_id = uuid.UUID(me.json()["id"])
    object_key = f"uploads/{user_id}/private.fits"
    upload_fits(object_key, b"private research bytes")
    db_session.add(
        DataFile(
            id=uuid.uuid4(),
            user_id=user_id,
            source="upload",
            object_id="private",
            fits_path=object_key,
        )
    )
    await db_session.commit()

    response = await app_client.request(
        "DELETE",
        "/api/auth/account",
        json={
            "confirmation": "delete-me",
            "password": "deletepassword123",
        },
        headers=headers,
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "DELETION_PENDING"
    assert response.json()["receipt"].startswith("delete_")
    assert (await app_client.get("/api/auth/me", headers=headers)).status_code == 401

    tombstone = (
        await db_session.execute(select(AccountDeletionTombstone))
    ).scalar_one()
    result = await erase_account_data(
        user_id=user_id,
        tombstone_id=tombstone.id,
        db=db_session,
    )
    assert result["rows_deleted"] >= 2
    assert result["objects_deleted"] == 1
    assert await db_session.get(User, user_id) is None
    assert await db_session.scalar(select(DataFile.id)) is None
    tombstone = await db_session.get(AccountDeletionTombstone, tombstone.id)
    assert tombstone is not None and tombstone.status == "COMPLETED"
    try:
        download_fits(object_key)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("Account-owned storage object was not erased")

    # Simulate restoring a pre-deletion users table from an old snapshot. The
    # storage-level tombstone must still reject the otherwise-valid JWT.
    restored = User(
        id=user_id,
        username="delete-me",
        email=internal_email_for_username("delete-me"),
        password_hash=hash_password("deletepassword123"),
        subscription_tier="starter",
    )
    db_session.add(restored)
    await db_session.commit()
    restored_token = create_access_token(user_id)
    restored_response = await app_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {restored_token}"},
    )
    assert restored_response.status_code == 401


async def test_account_delete_reauthentication_is_rate_limited(
    app_client,
    monkeypatch,
):
    from app.rate_limit import limiter

    token = await _register(app_client, "delete-rate-limit", "correctpassword123")
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(limiter, "enabled", True)
    limiter.reset()

    for _ in range(3):
        response = await app_client.request(
            "DELETE",
            "/api/auth/account",
            json={
                "confirmation": "delete-rate-limit",
                "password": "wrongpassword123",
            },
            headers=headers,
        )
        assert response.status_code == 403
        assert response.json()["detail"] == (
            "Account deletion confirmation or reauthentication failed"
        )

    blocked = await app_client.request(
        "DELETE",
        "/api/auth/account",
        json={
            "confirmation": "delete-rate-limit",
            "password": "wrongpassword123",
        },
        headers=headers,
    )
    assert blocked.status_code == 429
