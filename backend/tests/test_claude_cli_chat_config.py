"""Configuration boundaries for local subscription-backed chat providers."""

from types import SimpleNamespace
import uuid

import pytest


_LOCAL_FLAGS = (
    "LOCAL_MODEL_ENABLED",
    "OPENAI_CLI_ENABLED",
    "CLAUDE_CLI_ENABLED",
)


def _clear_local_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LOCAL_FLAGS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("enabled_value", ["1", "true", "YES", "on"])
def test_claude_cli_is_a_configured_local_backend_in_development(
    monkeypatch: pytest.MonkeyPatch,
    enabled_value: str,
) -> None:
    from app.api.chat import _local_backend_configured

    _clear_local_flags(monkeypatch)
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("CLAUDE_CLI_ENABLED", enabled_value)

    assert _local_backend_configured() is True


@pytest.mark.parametrize("disabled_value", ["", "0", "false", "no", "off"])
def test_claude_cli_flag_uses_strict_truthy_semantics(
    monkeypatch: pytest.MonkeyPatch,
    disabled_value: str,
) -> None:
    from app.api.chat import _local_backend_configured

    _clear_local_flags(monkeypatch)
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("CLAUDE_CLI_ENABLED", disabled_value)

    assert _local_backend_configured() is False


@pytest.mark.parametrize("environment", ["production", "prod"])
def test_subscription_clis_are_never_configured_in_production(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    from app.api.chat import _local_backend_configured

    _clear_local_flags(monkeypatch)
    monkeypatch.setenv("ENV", environment)
    monkeypatch.setenv("CLAUDE_CLI_ENABLED", "1")
    monkeypatch.setenv("OPENAI_CLI_ENABLED", "1")

    assert _local_backend_configured() is False


@pytest.mark.asyncio
async def test_backend_status_reports_claude_cli_as_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.chat import ai_backend_status

    _clear_local_flags(monkeypatch)
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("CLAUDE_CLI_ENABLED", "1")
    monkeypatch.setenv("SHARED_DEEPSEEK_API_KEY_ENABLED", "0")
    monkeypatch.delenv("PLATFORM_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    result = await ai_backend_status(
        request=None,
        api_provider="local",
        model_profile="local:claude-cli",
        user=None,
    )

    assert result["configured_backends"] == ["local"]
    assert result["needs_setup"] is False
    assert result["selected_model_status"]["id"] == "local:claude-cli"


def test_claude_cli_local_call_does_not_consume_platform_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import chat

    _clear_local_flags(monkeypatch)
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("CLAUDE_CLI_ENABLED", "1")
    monkeypatch.setattr(chat, "_server_deepseek_api_key", lambda: "server-key")

    def fail_if_charged(*args, **kwargs):
        raise AssertionError("local subscription CLI must not consume platform quota")

    monkeypatch.setattr(chat.daily_quota, "check_and_increment", fail_if_charged)
    user = SimpleNamespace(id=uuid.uuid4(), subscription_tier="starter")

    chat._enforce_starter_daily_quota(
        user,
        {"api_provider": "local"},
        {"deepseek": "server-key"},
    )


def test_production_cli_flag_does_not_bypass_platform_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import chat

    _clear_local_flags(monkeypatch)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("CLAUDE_CLI_ENABLED", "1")
    monkeypatch.setattr(chat, "_server_deepseek_api_key", lambda: "server-key")
    calls: list[tuple[str, str, str]] = []

    def record_charge(
        user_id: str,
        tier: str,
        metric: str,
        *,
        require_durable: bool = False,
    ) -> dict:
        assert require_durable is True
        calls.append((user_id, tier, metric))
        return {"allowed": True}

    monkeypatch.setattr(chat.daily_quota, "check_and_increment", record_charge)
    user = SimpleNamespace(id=uuid.uuid4(), subscription_tier="starter")

    chat._enforce_starter_daily_quota(
        user,
        {"api_provider": "local"},
        {"deepseek": "server-key"},
    )

    assert calls == [(str(user.id), "starter", "api_calls")]
