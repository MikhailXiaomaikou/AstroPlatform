"""Inference routing across multiple LLM backends."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from abc import ABC, abstractmethod

import httpx

from app.models.database import async_session
from app.models.schemas import InferenceLog

logger = logging.getLogger(__name__)


class InferenceError(RuntimeError):
    pass


def _normalize_openai_messages(messages: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if not isinstance(content, list):
            normalized.append({"role": role, "content": content})
            continue

        text_parts = [str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text"]
        tool_uses = [block for block in content if isinstance(block, dict) and block.get("type") == "tool_use"]
        tool_results = [block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"]

        if role == "assistant" and tool_uses:
            normalized.append(
                {
                    "role": "assistant",
                    "content": "\n\n".join(part for part in text_parts if part).strip() or None,
                    "tool_calls": [
                        {
                            "id": block.get("id") or str(uuid.uuid4()),
                            "type": "function",
                            "function": {
                                "name": block.get("name"),
                                "arguments": json.dumps(block.get("input") or {}),
                            },
                        }
                        for block in tool_uses
                    ],
                }
            )
            continue

        if role == "user" and tool_results:
            for block in tool_results:
                normalized.append(
                    {
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id") or str(uuid.uuid4()),
                        "content": block.get("content", ""),
                    }
                )
            continue

        normalized.append({"role": role, "content": "\n\n".join(part for part in text_parts if part)})
    return normalized


def _extract_openai_content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    text_parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            text_parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        text_value = item.get("text")
        if isinstance(text_value, str):
            text_parts.append(text_value)
            continue
        if isinstance(text_value, dict):
            nested = text_value.get("value")
            if isinstance(nested, str):
                text_parts.append(nested)
                continue
        if item.get("type") in {"output_text", "text"} and isinstance(item.get("content"), str):
            text_parts.append(str(item["content"]))
    return "\n\n".join(part for part in text_parts if part).strip()


class LLMBackend(ABC):
    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        *,
        system: str | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        api_key: str | None = None,
        provider_api_keys: dict[str, str] | None = None,
        request_timeout: float | None = None,
    ) -> dict:
        raise NotImplementedError

    @abstractmethod
    def model_name(self) -> str: ...

    @abstractmethod
    def max_context_length(self) -> int: ...


class ClaudeBackend(LLMBackend):
    def __init__(self):
        self._model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

    async def complete(self, messages, *, system=None, tools=None, max_tokens=4096, temperature=0.0, api_key=None, provider_api_keys=None, request_timeout=None):
        try:
            import anthropic
        except ImportError as exc:
            raise InferenceError("anthropic package not installed") from exc
        key = api_key or (provider_api_keys or {}).get("anthropic") or os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            raise InferenceError("Anthropic API key is not configured")

        client = anthropic.Anthropic(api_key=key)
        response = await asyncio.to_thread(
            client.messages.create,
            model=self._model,
            system=system or "",
            messages=messages,
            tools=tools or [],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text_blocks: list[str] = []
        tool_calls: list[dict] = []
        for block in response.content:
            if block.type == "text":
                text_blocks.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "input": block.input})
        usage = {
            "input_tokens": getattr(getattr(response, "usage", None), "input_tokens", 0) or 0,
            "output_tokens": getattr(getattr(response, "usage", None), "output_tokens", 0) or 0,
        }
        return {
            "content": "\n\n".join(text_blocks),
            "tool_calls": tool_calls,
            "usage": usage,
            "stop_reason": getattr(response, "stop_reason", None),
            "backend_name": "claude",
        }

    def model_name(self) -> str:
        return self._model

    def max_context_length(self) -> int:
        return 200_000


class OpenAICompatibleBackend(LLMBackend):
    backend_label = "openai-compatible"

    def __init__(self, *, model_env: str, key_env: str, base_url: str):
        self._model_env = model_env
        self._key_env = key_env
        self._base_url = base_url.rstrip("/")

    provider_name = ""

    async def complete(self, messages, *, system=None, tools=None, max_tokens=4096, temperature=0.0, api_key=None, provider_api_keys=None, request_timeout=None):
        key = (
            api_key
            or (provider_api_keys or {}).get(self.provider_name)
            or os.getenv(self._key_env, "")
        )
        if not key and self._key_env != "LOCAL_MODEL_API_KEY":
            raise InferenceError(f"{self.backend_label} API key is not configured")

        payload_messages = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(_normalize_openai_messages(messages))

        payload = {
            "model": os.getenv(self._model_env, self.model_name()),
            "messages": payload_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                    },
                }
                for tool in tools
            ]
            payload["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"

        async with httpx.AsyncClient(timeout=request_timeout or 60.0) as client:
            response = await client.post(f"{self._base_url}/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        tool_calls = []
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function", {})
            tool_calls.append({
                "id": tool_call.get("id") or str(uuid.uuid4()),
                "name": function.get("name"),
                "input": json.loads(function.get("arguments") or "{}"),
            })
        legacy_function_call = message.get("function_call")
        if isinstance(legacy_function_call, dict):
            tool_calls.append({
                "id": str(uuid.uuid4()),
                "name": legacy_function_call.get("name"),
                "input": json.loads(legacy_function_call.get("arguments") or "{}"),
            })
        content_text = _extract_openai_content_text(message.get("content"))
        refusal = message.get("refusal")
        if not content_text and isinstance(refusal, str):
            content_text = refusal
        if not content_text and not tool_calls:
            raise InferenceError(f"{self.backend_label} returned an empty completion")
        usage = data.get("usage") or {}
        return {
            "content": content_text,
            "tool_calls": tool_calls,
            "usage": {
                "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "output_tokens": int(usage.get("completion_tokens", 0) or 0),
            },
            "stop_reason": choice.get("finish_reason"),
            "backend_name": self.backend_label,
        }

    def model_name(self) -> str:
        return "gpt-4o-mini"

    def max_context_length(self) -> int:
        return 128_000


class OpenAIBackend(OpenAICompatibleBackend):
    backend_label = "openai"
    provider_name = "openai"

    def __init__(self):
        super().__init__(model_env="OPENAI_MODEL", key_env="OPENAI_API_KEY", base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))


class LocalBackend(OpenAICompatibleBackend):
    backend_label = "local"
    provider_name = "local"

    def __init__(self):
        super().__init__(model_env="LOCAL_MODEL_NAME", key_env="LOCAL_MODEL_API_KEY", base_url=os.getenv("LOCAL_MODEL_BASE_URL", "http://localhost:8000/v1"))

    async def complete(self, messages, *, system=None, tools=None, max_tokens=4096, temperature=0.0, api_key=None, provider_api_keys=None, request_timeout=None):
        if not os.getenv("LOCAL_MODEL_ENABLED", ""):
            raise InferenceError("Local model backend is not enabled. Set LOCAL_MODEL_ENABLED=1 and provide an OpenAI-compatible server.")
        return await super().complete(messages, system=system, tools=tools, max_tokens=max_tokens, temperature=temperature, api_key=api_key, provider_api_keys=provider_api_keys, request_timeout=request_timeout)

    def model_name(self) -> str:
        return os.getenv("LOCAL_MODEL_NAME", "local-model")


class DeepSeekBackend(OpenAICompatibleBackend):
    backend_label = "deepseek"
    provider_name = "deepseek"

    def __init__(self):
        super().__init__(model_env="DEEPSEEK_MODEL", key_env="DEEPSEEK_API_KEY", base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"))

    def model_name(self) -> str:
        return os.getenv("DEEPSEEK_MODEL", "deepseek-chat")


class InferenceRouter:
    def __init__(self):
        self.backends: dict[str, LLMBackend] = {
            "claude": ClaudeBackend(),
            "openai": OpenAIBackend(),
            "local": LocalBackend(),
            "deepseek": DeepSeekBackend(),
        }
        default_backend = os.getenv("DEFAULT_AI_BACKEND", "claude")
        self.agent_routing = {
            "orchestrator": os.getenv("ORCHESTRATOR_AGENT_BACKEND", default_backend),
            "data_agent": os.getenv("DATA_AGENT_BACKEND", default_backend),
            "analysis_agent": os.getenv("ANALYSIS_AGENT_BACKEND", default_backend),
            "literature_agent": os.getenv("LITERATURE_AGENT_BACKEND", default_backend),
            "observation_agent": os.getenv("OBSERVATION_AGENT_BACKEND", default_backend),
            "visualization_agent": os.getenv("VISUALIZATION_AGENT_BACKEND", default_backend),
            "spectrum_agent": os.getenv("SPECTRUM_AGENT_BACKEND", default_backend),
        }
        self.fallback_chain = [name for name in ["claude", "openai", "deepseek", "local"] if name in self.backends]

    def _backend_is_available(self, backend_name: str, provider_api_keys: dict[str, str] | None) -> bool:
        keys = provider_api_keys or {}
        if backend_name == "claude":
            return bool(keys.get("anthropic") or os.getenv("ANTHROPIC_API_KEY", ""))
        if backend_name == "openai":
            return bool(keys.get("openai") or os.getenv("OPENAI_API_KEY", ""))
        if backend_name == "deepseek":
            return bool(keys.get("deepseek") or os.getenv("DEEPSEEK_API_KEY", ""))
        if backend_name == "local":
            return bool(os.getenv("LOCAL_MODEL_ENABLED", ""))
        return backend_name in self.backends

    async def route(self, agent_name: str, messages: list[dict], *, system: str | None = None, tools: list[dict] | None = None, api_key: str | None = None, provider_api_keys: dict[str, str] | None = None, backend_timeout: float = 60.0, preferred_backend: str | None = None, **kwargs) -> dict:
        primary = preferred_backend or self.agent_routing.get(agent_name, "claude")
        attempted = [primary] + [item for item in self.fallback_chain if item != primary]
        attempted_errors: list[tuple[str, Exception]] = []
        attempted_configured = 0
        for backend_name in attempted:
            backend = self.backends.get(backend_name)
            if backend is None:
                continue
            if not self._backend_is_available(backend_name, provider_api_keys):
                logger.info("Skipping unavailable inference backend %s for %s", backend_name, agent_name)
                continue
            attempted_configured += 1
            started = time.perf_counter()
            try:
                result = await asyncio.wait_for(
                    backend.complete(messages, system=system, tools=tools, api_key=api_key, provider_api_keys=provider_api_keys, request_timeout=backend_timeout, **kwargs),
                    timeout=backend_timeout,
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                await self.log_inference(agent_name, backend_name, result.get("usage", {}), success=True, latency_ms=latency_ms)
                return result
            except Exception as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                attempted_errors.append((backend_name, exc))
                await self.log_inference(agent_name, backend_name, {}, success=False, latency_ms=latency_ms, error=str(exc))
                logger.warning("Inference backend %s failed for %s: %s", backend_name, agent_name, exc)
                continue
        if attempted_configured == 0:
            raise InferenceError(
                "No configured AI backends are available. Add an Anthropic, OpenAI, or DeepSeek API key in Settings, "
                "or enable LOCAL_MODEL_ENABLED for a local model backend."
            )
        if attempted_errors:
            details = "; ".join(f"{backend}: {exc}" for backend, exc in attempted_errors[:3])
            raise InferenceError(f"All configured AI backends failed: {details}")
        raise InferenceError("No inference backends could be used for this request.")

    async def log_inference(self, agent: str, backend: str, usage: dict, success: bool, latency_ms: int, error: str | None = None):
        prices = {
            "claude": (0.000003, 0.000015),
            "openai": (0.000001, 0.000004),
            "deepseek": (0.0000008, 0.000002),
            "local": (0.0, 0.0),
        }
        in_price, out_price = prices.get(backend, (0.0, 0.0))
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        cost = input_tokens * in_price + output_tokens * out_price
        async with async_session() as db:
            db.add(
                InferenceLog(
                    agent_name=agent,
                    backend_name=backend,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    success=success,
                    error=error,
                    cost_usd=cost,
                )
            )
            await db.commit()


inference_router = InferenceRouter()
