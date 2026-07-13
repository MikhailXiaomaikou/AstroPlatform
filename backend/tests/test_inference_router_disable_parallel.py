"""Verify provider payloads disable in-turn parallel tool calls.

A repeated-tool-call case exposed that a model can issue many identical tool
calls in one response before the circuit breaker gets a chance to disable that
tool. These tests exercise the actual provider payloads instead of grepping the
router source.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


def _tool_spec() -> dict:
    return {
        "name": "fit_cosmology_mcmc",
        "description": "Run a cosmology likelihood fit.",
        "input_schema": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
        },
    }


@pytest.mark.asyncio
async def test_anthropic_payload_disables_parallel_tool_use(monkeypatch):
    from app.ai.inference_router import ClaudeBackend

    captured: dict = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ok")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                stop_reason="stop",
            )

    class FakeAnthropicClient:
        def __init__(self, api_key):
            self.messages = FakeMessages()

    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        SimpleNamespace(Anthropic=FakeAnthropicClient),
    )

    result = await ClaudeBackend().complete(
        [{"role": "user", "content": "Run a cosmology fit"}],
        tools=[_tool_spec()],
        api_key="sk-ant-test",
    )

    assert result["content"] == "ok"
    expected_tool = {**_tool_spec(), "cache_control": {"type": "ephemeral"}}
    assert captured["tools"] == [expected_tool]
    assert captured["tool_choice"] == {
        "type": "auto",
        "disable_parallel_tool_use": True,
    }


@pytest.mark.asyncio
async def test_anthropic_payload_omits_tool_choice_without_tools(monkeypatch):
    from app.ai.inference_router import ClaudeBackend

    captured: dict = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ok")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                stop_reason="stop",
            )

    class FakeAnthropicClient:
        def __init__(self, api_key):
            self.messages = FakeMessages()

    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        SimpleNamespace(Anthropic=FakeAnthropicClient),
    )

    await ClaudeBackend().complete(
        [{"role": "user", "content": "No tools"}],
        tools=[],
        api_key="sk-ant-test",
    )

    assert "tools" not in captured
    assert "tool_choice" not in captured


@pytest.mark.asyncio
async def test_openai_chat_payload_disables_parallel_tool_calls(monkeypatch):
    from app.ai.inference_router import OpenAIBackend

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "ok"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr("app.ai.inference_router.httpx.AsyncClient", FakeClient)

    result = await OpenAIBackend().complete(
        [{"role": "user", "content": "Run a cosmology fit"}],
        tools=[_tool_spec()],
        provider_api_keys={"openai": "sk-openai-test"},
    )

    payload = captured["json"]
    assert result["content"] == "ok"
    assert payload["tools"][0]["function"]["name"] == "fit_cosmology_mcmc"
    assert payload["tool_choice"] == "auto"
    assert payload["parallel_tool_calls"] is False


@pytest.mark.asyncio
async def test_openai_chat_payload_omits_tool_controls_without_tools(monkeypatch):
    from app.ai.inference_router import OpenAIBackend

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "ok"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr("app.ai.inference_router.httpx.AsyncClient", FakeClient)

    await OpenAIBackend().complete(
        [{"role": "user", "content": "No tools"}],
        tools=[],
        provider_api_keys={"openai": "sk-openai-test"},
    )

    payload = captured["json"]
    assert "tools" not in payload
    assert "tool_choice" not in payload
    assert "parallel_tool_calls" not in payload


@pytest.mark.asyncio
async def test_explicit_provider_map_prevents_generic_key_cross_provider_leak(
    monkeypatch,
):
    from app.ai.inference_router import DeepSeekBackend

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
                "usage": {},
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr("app.ai.inference_router.httpx.AsyncClient", FakeClient)

    await DeepSeekBackend().complete(
        [{"role": "user", "content": "Summarize"}],
        api_key="sk-ant-must-not-cross-provider",
        provider_api_keys={"deepseek": "sk-deepseek-correct"},
    )

    assert captured["headers"]["Authorization"] == "Bearer sk-deepseek-correct"
