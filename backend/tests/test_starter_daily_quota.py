"""Daily quota on platform-funded chat calls (decision 2B + P0 hardening).

New self-service signups land on the "starter" tier: 50 platform-funded
chat calls per UTC day. Anonymous callers receive the same allowance per
trusted client IP. This protects the shared server DeepSeek key (the only
platform-funded provider — Anthropic and OpenAI stay BYOK, see
app/api/chat.py:_provider_api_keys). Calls that run on a user-supplied key
are exempt. Pre-existing accounts (solo/lab/institution) are untouched.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.auth import create_access_token, hash_password
from app.config import settings
from app.models.schemas import User
from app.rate_limit import DailyQuota, daily_quota

SERVER_KEY = "sk-platform-deepseek-test"
STARTER_DAILY_API_CALLS = 50


@pytest.fixture(autouse=True)
def quota_isolation(monkeypatch):
    """Deterministic quota state: no Redis, fresh in-memory counters."""
    import app.rate_limit as rl

    monkeypatch.setattr(rl, "_get_quota_redis", lambda: None)
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.delenv("RENDER", raising=False)
    daily_quota._usage.clear()
    daily_quota._reset_day.clear()
    yield
    daily_quota._usage.clear()
    daily_quota._reset_day.clear()


@pytest.fixture
def platform_key_env(monkeypatch):
    """Simulate an operator explicitly enabling the shared DeepSeek key."""
    monkeypatch.setenv("PLATFORM_DEEPSEEK_API_KEY", SERVER_KEY)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("SHARED_DEEPSEEK_API_KEY_ENABLED", "1")
    monkeypatch.delenv("DEFAULT_AI_BACKEND", raising=False)


async def _make_user(db_session, tier: str):
    """Insert a user directly (bypassing /register) to model a pre-existing account."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        username=f"quota-{suffix}",
        email=f"quota-{suffix}@astro.example.com",
        password_hash=hash_password("securepassword123"),
        subscription_tier=tier,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user, create_access_token(user.id)


def _seed_quota(user_id, n: int) -> None:
    for _ in range(n):
        daily_quota.check_and_increment(str(user_id), "starter", "api_calls")


def _seed_anonymous_quota(client_ip: str, n: int) -> None:
    for _ in range(n):
        daily_quota.check_and_increment(
            f"anonymous-ip:{client_ip}", "anonymous", "api_calls"
        )


def _request(client_ip: str = "203.0.113.7", headers: dict | None = None):
    return SimpleNamespace(
        client=SimpleNamespace(host=client_ip),
        headers=headers or {},
    )


async def _fake_chat(**kwargs):
    return {"reply": "ok", "actions": []}


def _chat_payload(context: dict | None = None) -> dict:
    payload: dict = {"messages": [{"role": "user", "content": "hello"}]}
    if context is not None:
        payload["context"] = context
    return payload


class TestStarterTierAssignment:
    async def test_register_defaults_to_starter_tier(self, app_client):
        resp = await app_client.post(
            "/api/auth/register",
            json={"username": "brand-new-user", "password": "password123"},
        )
        assert resp.status_code == 201
        token = resp.json()["access_token"]
        me = await app_client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert me.status_code == 200
        assert me.json()["subscription_tier"] == "starter"


class TestDailyQuotaStarterLimits:
    def test_starter_allows_50_then_blocks_51st(self):
        quota = DailyQuota()
        user_id = str(uuid.uuid4())
        for _ in range(STARTER_DAILY_API_CALLS):
            verdict = quota.check_and_increment(user_id, "starter", "api_calls")
            assert verdict["allowed"] is True
        verdict = quota.check_and_increment(user_id, "starter", "api_calls")
        assert verdict["allowed"] is False
        assert verdict["limit"] == STARTER_DAILY_API_CALLS

    def test_solo_tier_limits_unchanged(self):
        quota = DailyQuota()
        assert quota.TIER_LIMITS["solo"] == {
            "api_calls": 1000,
            "pipeline_runs": 50,
            "adql_queries": 200,
        }


class TestAnonymousQuotaGate:
    def test_anonymous_allows_50_then_blocks_51st(self, platform_key_env):
        from app.api.chat import _enforce_starter_daily_quota

        request = _request()
        provider_keys = {"deepseek": SERVER_KEY}
        for _ in range(STARTER_DAILY_API_CALLS):
            _enforce_starter_daily_quota(
                None, {}, provider_keys, request=request
            )

        with pytest.raises(HTTPException) as exc_info:
            _enforce_starter_daily_quota(
                None, {}, provider_keys, request=request
            )

        assert exc_info.value.status_code == 429
        assert daily_quota.get_usage("anonymous-ip:203.0.113.7")["api_calls"] == 50

    def test_anonymous_bucket_ignores_spoofed_forwarding_headers(
        self, platform_key_env, monkeypatch
    ):
        from app.api.chat import _enforce_starter_daily_quota

        monkeypatch.setattr(settings, "trusted_proxy_mode", "none", raising=False)
        provider_keys = {"deepseek": SERVER_KEY}
        socket_ip = "203.0.113.7"
        _seed_anonymous_quota(socket_ip, STARTER_DAILY_API_CALLS)

        with pytest.raises(HTTPException) as first:
            _enforce_starter_daily_quota(
                None,
                {},
                provider_keys,
                request=_request(socket_ip, {"X-Forwarded-For": "198.51.100.1"}),
            )
        with pytest.raises(HTTPException) as second:
            _enforce_starter_daily_quota(
                None,
                {},
                provider_keys,
                request=_request(socket_ip, {"X-Forwarded-For": "198.51.100.2"}),
            )

        assert first.value.status_code == second.value.status_code == 429

    def test_anonymous_byok_is_exempt(self, platform_key_env):
        from app.api.chat import _enforce_starter_daily_quota

        client_ip = "203.0.113.7"
        _seed_anonymous_quota(client_ip, STARTER_DAILY_API_CALLS)
        _enforce_starter_daily_quota(
            None,
            {"api_provider": "deepseek"},
            {"deepseek": "sk-user-own-deepseek"},
            request=_request(client_ip),
        )
        assert daily_quota.get_usage(f"anonymous-ip:{client_ip}")["api_calls"] == 50

    def test_hosted_shared_key_fails_closed_without_redis(
        self, platform_key_env, monkeypatch
    ):
        from app.api.chat import _enforce_starter_daily_quota

        monkeypatch.setenv("ENV", "production")
        with pytest.raises(HTTPException) as exc_info:
            _enforce_starter_daily_quota(
                None,
                {},
                {"deepseek": SERVER_KEY},
                request=_request(),
            )

        assert exc_info.value.status_code == 503
        assert "quota service" in str(exc_info.value.detail)

    async def test_anonymous_over_quota_rejected_before_sse_opens(
        self, app_client, platform_key_env
    ):
        _seed_anonymous_quota("127.0.0.1", STARTER_DAILY_API_CALLS)
        with patch("app.api.chat._build_runtime") as build_runtime:
            response = await app_client.post(
                "/api/chat/message/stream",
                json=_chat_payload(),
            )

        assert response.status_code == 429
        assert response.headers["content-type"].startswith("application/json")
        build_runtime.assert_not_called()


class TestStarterQuotaEnforcement:
    async def test_starter_51st_platform_funded_call_rejected_429(
        self, app_client, db_session, platform_key_env
    ):
        user, token = await _make_user(db_session, "starter")
        _seed_quota(user.id, STARTER_DAILY_API_CALLS)
        with patch("app.api.chat._run_orchestrated_chat", new=_fake_chat):
            resp = await app_client.post(
                "/api/chat/message",
                headers={"Authorization": f"Bearer {token}"},
                json=_chat_payload(),
            )
        assert resp.status_code == 429
        detail = resp.json()["detail"]
        # Friendly rejection: states the cap, the reset time, and the BYOK way out.
        assert "50" in detail
        assert "UTC" in detail
        assert "key" in detail.lower()

    async def test_starter_under_quota_allowed_and_counted(
        self, app_client, db_session, platform_key_env
    ):
        user, token = await _make_user(db_session, "starter")
        _seed_quota(user.id, STARTER_DAILY_API_CALLS - 1)
        with patch("app.api.chat._run_orchestrated_chat", new=_fake_chat):
            resp = await app_client.post(
                "/api/chat/message",
                headers={"Authorization": f"Bearer {token}"},
                json=_chat_payload(),
            )
        assert resp.status_code == 200
        usage = daily_quota.get_usage(str(user.id))
        assert usage.get("api_calls") == STARTER_DAILY_API_CALLS

    async def test_stream_endpoint_over_quota_rejected_before_stream(
        self, app_client, db_session, platform_key_env
    ):
        user, token = await _make_user(db_session, "starter")
        _seed_quota(user.id, STARTER_DAILY_API_CALLS)
        resp = await app_client.post(
            "/api/chat/message/stream",
            headers={"Authorization": f"Bearer {token}"},
            json=_chat_payload(),
        )
        # Clean HTTP 429, not an in-stream error frame.
        assert resp.status_code == 429

    async def test_existing_solo_user_unaffected_and_not_counted(
        self, app_client, db_session, platform_key_env
    ):
        user, token = await _make_user(db_session, "solo")
        _seed_quota(user.id, STARTER_DAILY_API_CALLS)
        with patch("app.api.chat._run_orchestrated_chat", new=_fake_chat):
            resp = await app_client.post(
                "/api/chat/message",
                headers={"Authorization": f"Bearer {token}"},
                json=_chat_payload(),
            )
        assert resp.status_code == 200
        # The gate must not even count solo-tier calls.
        usage = daily_quota.get_usage(str(user.id))
        assert usage.get("api_calls") == STARTER_DAILY_API_CALLS

    async def test_no_platform_key_means_no_gate(
        self, app_client, db_session, monkeypatch
    ):
        # Without a platform-funded key there is nothing to protect.
        monkeypatch.delenv("PLATFORM_DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        from app.config import settings

        monkeypatch.setattr(settings, "platform_deepseek_api_key", "", raising=False)
        monkeypatch.setattr(settings, "deepseek_api_key", "", raising=False)
        user, token = await _make_user(db_session, "starter")
        _seed_quota(user.id, STARTER_DAILY_API_CALLS)
        with patch("app.api.chat._run_orchestrated_chat", new=_fake_chat):
            resp = await app_client.post(
                "/api/chat/message",
                headers={"Authorization": f"Bearer {token}"},
                json=_chat_payload(),
            )
        assert resp.status_code == 200


class TestByokExemption:
    async def test_own_deepseek_key_exempt(
        self, app_client, db_session, platform_key_env
    ):
        user, token = await _make_user(db_session, "starter")
        _seed_quota(user.id, STARTER_DAILY_API_CALLS)
        with patch("app.api.chat._run_orchestrated_chat", new=_fake_chat):
            resp = await app_client.post(
                "/api/chat/message",
                headers={"Authorization": f"Bearer {token}"},
                json=_chat_payload({"api_keys": {"deepseek": "sk-user-own-deepseek"}}),
            )
        assert resp.status_code == 200
        # Exempt calls are not counted either.
        usage = daily_quota.get_usage(str(user.id))
        assert usage.get("api_calls") == STARTER_DAILY_API_CALLS

    async def test_own_anthropic_key_with_explicit_provider_exempt(
        self, app_client, db_session, platform_key_env
    ):
        user, token = await _make_user(db_session, "starter")
        _seed_quota(user.id, STARTER_DAILY_API_CALLS)
        with patch("app.api.chat._run_orchestrated_chat", new=_fake_chat):
            resp = await app_client.post(
                "/api/chat/message",
                headers={"Authorization": f"Bearer {token}"},
                json=_chat_payload(
                    {
                        "api_keys": {"anthropic": "sk-ant-user-own"},
                        "api_provider": "anthropic",
                    }
                ),
            )
        assert resp.status_code == 200

    async def test_provider_preference_without_own_key_still_capped(
        self, app_client, db_session, platform_key_env
    ):
        # Anti-bypass: claiming api_provider=anthropic without a key would
        # fall back to the platform DeepSeek key in the inference router,
        # so the gate must fail closed and still count/deny the call.
        user, token = await _make_user(db_session, "starter")
        _seed_quota(user.id, STARTER_DAILY_API_CALLS)
        with patch("app.api.chat._run_orchestrated_chat", new=_fake_chat):
            resp = await app_client.post(
                "/api/chat/message",
                headers={"Authorization": f"Bearer {token}"},
                json=_chat_payload({"api_provider": "anthropic"}),
            )
        assert resp.status_code == 429

    async def test_own_anthropic_key_default_routing_still_capped(
        self, app_client, db_session, platform_key_env
    ):
        # Without an explicit provider the default routing is the platform
        # DeepSeek backend, so the call spends platform money and counts
        # even though the user stored an Anthropic key.
        user, token = await _make_user(db_session, "starter")
        _seed_quota(user.id, STARTER_DAILY_API_CALLS)
        with patch("app.api.chat._run_orchestrated_chat", new=_fake_chat):
            resp = await app_client.post(
                "/api/chat/message",
                headers={"Authorization": f"Bearer {token}"},
                json=_chat_payload({"api_keys": {"anthropic": "sk-ant-user-own"}}),
            )
        assert resp.status_code == 429
