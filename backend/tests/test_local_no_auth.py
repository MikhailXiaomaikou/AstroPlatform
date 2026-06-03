import pytest


@pytest.mark.asyncio
async def test_local_dev_no_auth_exposes_profile_without_token(app_client, monkeypatch):
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("LOCAL_DEV_NO_AUTH", "1")

    resp = await app_client.get("/api/auth/me")

    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "local-dev"
    assert data["email"] == "local-dev@localhost"
    assert data["subscription_tier"] == "institution"


@pytest.mark.asyncio
async def test_local_dev_no_auth_allows_admin_dependency_without_secret(app_client, monkeypatch):
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("LOCAL_DEV_NO_AUTH", "1")
    monkeypatch.setattr("app.api.auth.settings.admin_secret", "", raising=False)

    resp = await app_client.post("/api/auth/generate-setup-keys", json={"count": 1, "label": "local"})

    assert resp.status_code == 200
    keys = resp.json()
    assert len(keys) == 1
    assert keys[0].startswith("ASTRO-LOCAL-")


@pytest.mark.asyncio
async def test_local_dev_no_auth_ignored_outside_dev(app_client, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("LOCAL_DEV_NO_AUTH", "1")

    resp = await app_client.get("/api/auth/me")

    assert resp.status_code == 401
