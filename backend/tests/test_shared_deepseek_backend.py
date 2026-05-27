import pytest


def test_provider_api_keys_include_shared_deepseek_key(monkeypatch):
    from app.api.chat import _provider_api_keys

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("PLATFORM_DEEPSEEK_API_KEY", "sk-shared-deepseek")

    keys = _provider_api_keys({}, None)

    assert keys["deepseek"] == "sk-shared-deepseek"
    assert "openai" not in keys
    assert "anthropic" not in keys


def test_provider_api_keys_user_deepseek_overrides_shared_key(monkeypatch):
    from app.api.chat import _provider_api_keys

    monkeypatch.setenv("PLATFORM_DEEPSEEK_API_KEY", "sk-shared-deepseek")

    keys = _provider_api_keys({"api_keys": {"deepseek": "sk-user-deepseek"}}, None)

    assert keys["deepseek"] == "sk-user-deepseek"


def test_provider_api_keys_can_disable_shared_deepseek_key(monkeypatch):
    from app.api.chat import _provider_api_keys

    monkeypatch.setenv("PLATFORM_DEEPSEEK_API_KEY", "sk-shared-deepseek")
    monkeypatch.setenv("SHARED_DEEPSEEK_API_KEY_ENABLED", "0")

    keys = _provider_api_keys({}, None)

    assert "deepseek" not in keys


def test_provider_api_keys_can_read_shared_deepseek_from_settings(monkeypatch):
    from app.api.chat import _provider_api_keys
    from app.config import settings

    monkeypatch.delenv("PLATFORM_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("SHARED_DEEPSEEK_API_KEY_ENABLED", raising=False)
    monkeypatch.setattr(settings, "platform_deepseek_api_key", "sk-settings-deepseek")
    monkeypatch.setattr(settings, "shared_deepseek_api_key_enabled", True)

    keys = _provider_api_keys({}, None)

    assert keys["deepseek"] == "sk-settings-deepseek"


def test_resolve_model_profile_defaults_to_deepseek():
    from app.ai.model_profiles import resolve_model_profile

    profile = resolve_model_profile(None, None)

    assert profile.id == "deepseek:v4-pro"
    assert profile.provider == "deepseek"


@pytest.mark.asyncio
async def test_ai_backend_status_reports_shared_deepseek_backend(app_client, monkeypatch):
    """Anonymous users can see that server-funded DeepSeek is available."""

    monkeypatch.delenv("OPENAI_CLI_ENABLED", raising=False)
    monkeypatch.delenv("LOCAL_MODEL_ENABLED", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("PLATFORM_DEEPSEEK_API_KEY", "sk-shared-deepseek")

    resp = await app_client.get("/api/chat/ai_backend_status")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["needs_setup"] is False
    assert "deepseek" in payload["configured_backends"]
    assert payload["selected_model_status"]["provider"] == "deepseek"
    assert "sk-shared" not in str(payload)
