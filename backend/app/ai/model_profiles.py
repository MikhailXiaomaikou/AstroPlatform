"""Manual AI model profile registry.

Profiles are intentionally explicit: the user chooses provider + model, and
the router only falls back after the selected backend fails.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, Literal


Provider = Literal["anthropic", "openai", "deepseek", "local"]
EndpointKind = Literal["anthropic_messages", "chat_completions", "responses"]

DEFAULT_AI_PROVIDER: Provider = "deepseek"


@dataclass(frozen=True)
class ModelProfile:
    id: str
    provider: Provider
    model_id: str
    display_name: str
    api_ready: bool
    resolved_model_id: str
    supports_tools: bool = True
    supports_reasoning: bool = False
    endpoint: EndpointKind = "chat_completions"
    reasoning_effort: str | None = None
    note: str | None = None
    extra_payload: dict[str, Any] | None = None

    def to_public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["provider"] = str(self.provider)
        return d


DEFAULT_MODEL_BY_PROVIDER: dict[str, str] = {
    "anthropic": "anthropic:default",
    "openai": "openai:gpt-5.5",
    "deepseek": "deepseek:v4-pro",
    "local": "local:default",
}


def _profiles() -> dict[str, ModelProfile]:
    gpt55_override = os.getenv("OPENAI_GPT55_MODEL", "").strip()
    gpt54_model = (
        os.getenv("OPENAI_GPT54_MODEL", "").strip()
        or os.getenv("OPENAI_MODEL", "").strip()
        or "gpt-5.4"
    )
    deepseek_pro = (
        os.getenv("DEEPSEEK_V4_PRO_MODEL", "").strip()
        or os.getenv("DEEPSEEK_MODEL", "").strip()
        or "deepseek-v4-pro"
    )
    return {
        "anthropic:default": ModelProfile(
            id="anthropic:default",
            provider="anthropic",
            model_id="default",
            display_name="Claude default",
            api_ready=True,
            resolved_model_id=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
            supports_reasoning=True,
            endpoint="anthropic_messages",
        ),
        "openai:gpt-5.5": ModelProfile(
            id="openai:gpt-5.5",
            provider="openai",
            model_id="gpt-5.5",
            display_name="OpenAI GPT-5.5 alias",
            api_ready=bool(gpt55_override),
            resolved_model_id=gpt55_override or "gpt-5.4",
            supports_tools=True,
            supports_reasoning=True,
            endpoint="responses",
            reasoning_effort="medium",
            note=(
                "Uses OPENAI_GPT55_MODEL when configured; otherwise resolves "
                "to gpt-5.4 until the GPT-5.5 API is available."
            ),
        ),
        "openai:gpt-5.4": ModelProfile(
            id="openai:gpt-5.4",
            provider="openai",
            model_id="gpt-5.4",
            display_name="OpenAI GPT-5.4",
            api_ready=True,
            resolved_model_id=gpt54_model,
            supports_tools=True,
            supports_reasoning=True,
            endpoint="responses",
            reasoning_effort="medium",
        ),
        "deepseek:v4-pro": ModelProfile(
            id="deepseek:v4-pro",
            provider="deepseek",
            model_id="deepseek-v4-pro",
            display_name="DeepSeek V4 Pro",
            api_ready=True,
            resolved_model_id=deepseek_pro,
            supports_tools=True,
            supports_reasoning=True,
            endpoint="chat_completions",
            reasoning_effort="high",
            extra_payload={"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
        ),
        "deepseek:v4-flash": ModelProfile(
            id="deepseek:v4-flash",
            provider="deepseek",
            model_id="deepseek-v4-flash",
            display_name="DeepSeek V4 Flash",
            api_ready=True,
            resolved_model_id=os.getenv("DEEPSEEK_V4_FLASH_MODEL", "deepseek-v4-flash"),
            supports_tools=True,
            supports_reasoning=False,
            endpoint="chat_completions",
        ),
        "local:default": ModelProfile(
            id="local:default",
            provider="local",
            model_id="default",
            display_name="Local OpenAI-compatible model",
            api_ready=True,
            resolved_model_id=os.getenv("LOCAL_MODEL_NAME", "local-model"),
            supports_tools=True,
            endpoint="chat_completions",
            note=(
                "Uses LOCAL_MODEL_BASE_URL / LOCAL_MODEL_NAME when "
                "LOCAL_MODEL_ENABLED=1."
            ),
        ),
        "local:openai-cli": ModelProfile(
            id="local:openai-cli",
            provider="local",
            model_id="openai-cli",
            display_name="OpenAI CLI (local subscription)",
            api_ready=True,
            resolved_model_id=os.getenv("OPENAI_CLI_MODEL", "codex-config-default"),
            supports_tools=True,
            endpoint="chat_completions",
            note=(
                "Local-only backend. Uses the installed Codex/OpenAI CLI login "
                "instead of API keys and requests platform tools through a "
                "backend-executed JSON bridge."
            ),
        ),
        "local:claude-cli": ModelProfile(
            id="local:claude-cli",
            provider="local",
            model_id="claude-cli",
            display_name="Claude CLI (local subscription)",
            api_ready=True,
            resolved_model_id=os.getenv("CLAUDE_CLI_MODEL", "claude-config-default"),
            supports_tools=True,
            endpoint="chat_completions",
            note=(
                "Local-only backend. Uses the installed Claude Code CLI login "
                "(subscription auth; Anthropic API-key variables are stripped "
                "from the child process) and requests platform tools through "
                "the same backend-executed JSON bridge as the OpenAI CLI."
            ),
        ),
        "local:kimi-cli": ModelProfile(
            id="local:kimi-cli",
            provider="local",
            model_id="kimi-cli",
            display_name="Kimi CLI (local subscription)",
            api_ready=True,
            resolved_model_id=os.getenv("KIMI_CLI_MODEL", "kimi-code/k3"),
            supports_tools=True,
            supports_reasoning=True,
            endpoint="chat_completions",
            note=(
                "Local-only backend. Uses the installed Kimi Code CLI OAuth "
                "login and requests Standard Astro tools through the same "
                "backend-executed JSON bridge as the other subscription CLIs."
            ),
        ),
    }


def all_model_profiles() -> dict[str, ModelProfile]:
    return _profiles()


def normalize_model_profile_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if not value:
        return None
    aliases = {
        "claude": "anthropic:default",
        "anthropic": "anthropic:default",
        "anthropic:claude": "anthropic:default",
        "openai": "openai:gpt-5.5",
        "gpt-5.5": "openai:gpt-5.5",
        "gpt-5.5-alias": "openai:gpt-5.5",
        "gpt-5.4": "openai:gpt-5.4",
        "deepseek": "deepseek:v4-pro",
        "deepseek-v4-pro": "deepseek:v4-pro",
        "deepseek-v4-flash": "deepseek:v4-flash",
        "local": "local:default",
        "openai-cli": "local:openai-cli",
        "codex-cli": "local:openai-cli",
        "local:codex": "local:openai-cli",
        "claude-cli": "local:claude-cli",
        "local:claude": "local:claude-cli",
        "kimi-cli": "local:kimi-cli",
        "kimi-k3": "local:kimi-cli",
        "local:kimi": "local:kimi-cli",
    }
    return aliases.get(value, value)


def resolve_model_profile(provider: str | None, requested: str | ModelProfile | None = None) -> ModelProfile:
    profiles = all_model_profiles()
    provider_key = str(provider or "").strip().lower()
    if provider_key == "claude":
        provider_key = "anthropic"
    if provider_key not in DEFAULT_MODEL_BY_PROVIDER:
        provider_key = DEFAULT_AI_PROVIDER

    if isinstance(requested, ModelProfile):
        if requested.provider == provider_key:
            return requested
        return profiles[DEFAULT_MODEL_BY_PROVIDER[provider_key]]

    requested_id = normalize_model_profile_id(requested)
    if requested_id:
        profile = profiles.get(requested_id)
        if profile and profile.provider == provider_key:
            return profile

    return profiles[DEFAULT_MODEL_BY_PROVIDER[provider_key]]


def available_model_profiles() -> list[dict[str, Any]]:
    return [profile.to_public_dict() for profile in all_model_profiles().values()]
