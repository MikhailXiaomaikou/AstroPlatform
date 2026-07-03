"""Regression: BYOK keys must never be persisted in cleartext.

The legacy ``users.anthropic_api_key`` column is plain TEXT (no Fernet).
Historically ``save_api_key`` mirrored the raw key into it on every save,
silently defeating the encryption-at-rest of the ``api_keys`` blob.
These tests pin the fixed behavior: saves write ONLY the encrypted blob,
the legacy column is nulled on the next save, and legacy cleartext rows
stay readable until then.
"""

from sqlalchemy import text


async def _raw_user_columns(db_session, user_id):
    """Read the raw column values, bypassing SQLAlchemy type decorators."""
    row = (
        await db_session.execute(
            text("SELECT anthropic_api_key, api_keys FROM users WHERE id = :id"),
            {"id": str(user_id)},
        )
    ).one()
    return row.anthropic_api_key, row.api_keys


class TestNoCleartextKeyAtRest:
    async def test_save_api_key_does_not_store_cleartext(
        self, app_client, test_user, db_session
    ):
        user, token = test_user
        secret = "sk-ant-regression-cleartext-check-000111222333"
        resp = await app_client.put(
            "/api/settings/api-keys",
            json={"provider": "anthropic", "key": secret},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        legacy_raw, api_keys_raw = await _raw_user_columns(db_session, user.id)
        # Legacy plain-TEXT column must not hold the key.
        assert legacy_raw is None
        # Encrypted blob must not contain the key in cleartext.
        assert api_keys_raw is not None
        assert secret not in api_keys_raw

        # Read path still resolves the key (decrypted from the blob).
        resp = await app_client.get(
            "/api/settings/api-keys",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        providers = [k["provider"] for k in resp.json()["keys"]]
        assert "anthropic" in providers

    async def test_legacy_endpoint_save_does_not_store_cleartext(
        self, app_client, test_user, db_session
    ):
        user, token = test_user
        secret = "sk-ant-legacy-endpoint-check-444555666777"
        resp = await app_client.put(
            "/api/settings/api-key",
            json={"provider": "anthropic", "key": secret},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        legacy_raw, api_keys_raw = await _raw_user_columns(db_session, user.id)
        assert legacy_raw is None
        assert api_keys_raw is not None
        assert secret not in api_keys_raw

    async def test_legacy_cleartext_row_readable_then_scrubbed_on_next_save(
        self, app_client, test_user, db_session
    ):
        """Backward compat: an old cleartext row keeps working, and the next
        save migrates it into the encrypted blob and nulls the legacy column."""
        user, token = test_user
        old_secret = "sk-ant-old-legacy-row-888999000111"
        # Simulate a pre-migration row: cleartext column set, no encrypted blob.
        await db_session.execute(
            text("UPDATE users SET anthropic_api_key = :k, api_keys = NULL WHERE id = :id"),
            {"k": old_secret, "id": str(user.id)},
        )
        await db_session.commit()
        db_session.expire_all()

        # Read path falls back to the legacy column.
        resp = await app_client.get(
            "/api/settings/api-key",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["has_key"] is True

        # Saving any key (here a different provider) migrates + scrubs.
        resp = await app_client.put(
            "/api/settings/api-keys",
            json={"provider": "openai", "key": "sk-openai-new-key-abcdef"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        legacy_raw, api_keys_raw = await _raw_user_columns(db_session, user.id)
        assert legacy_raw is None
        assert api_keys_raw is not None
        assert old_secret not in api_keys_raw  # encrypted, not cleartext

        # The migrated anthropic key is still resolvable.
        resp = await app_client.get(
            "/api/settings/api-keys",
            headers={"Authorization": f"Bearer {token}"},
        )
        providers = [k["provider"] for k in resp.json()["keys"]]
        assert "anthropic" in providers and "openai" in providers
