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

from app.ai.model_profiles import ModelProfile, resolve_model_profile
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
        # PART Z C6 — DeepSeek thinking-mode contract: when the previous
        # assistant turn carried `reasoning_content`, it MUST be passed
        # back on the next request or DeepSeek 400s with
        # "The reasoning_content in the thinking mode must be passed
        # back to the API." We carry it as a dedicated content block
        # so chat.py doesn't need to know which provider produced it.
        reasoning_blocks = [
            block for block in content
            if isinstance(block, dict) and block.get("type") == "reasoning_content"
        ]
        reasoning_text = "\n\n".join(
            str(block.get("text") or block.get("content") or "")
            for block in reasoning_blocks
        ).strip()

        if role == "assistant" and tool_uses:
            msg = {
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
            if reasoning_text:
                msg["reasoning_content"] = reasoning_text
            normalized.append(msg)
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

        msg = {"role": role, "content": "\n\n".join(part for part in text_parts if part)}
        if role == "assistant" and reasoning_text:
            msg["reasoning_content"] = reasoning_text
        normalized.append(msg)
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
        model_profile: ModelProfile | None = None,
    ) -> dict:
        raise NotImplementedError

    @abstractmethod
    def model_name(self) -> str: ...

    @abstractmethod
    def max_context_length(self) -> int: ...


class ClaudeBackend(LLMBackend):
    def __init__(self):
        self._model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

    async def complete(self, messages, *, system=None, tools=None, max_tokens=4096, temperature=0.0, api_key=None, provider_api_keys=None, request_timeout=None, model_profile=None):
        try:
            import anthropic
        except ImportError as exc:
            raise InferenceError("anthropic package not installed") from exc
        key = api_key or (provider_api_keys or {}).get("anthropic") or os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            raise InferenceError("Anthropic API key is not configured")

        client = anthropic.Anthropic(api_key=key)
        system_param: str | list = system or ""
        if system:
            system_param = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        anthropic_kwargs: dict = {
            "model": (model_profile.resolved_model_id if model_profile and model_profile.provider == "anthropic" else self._model),
            "system": system_param,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            cached_tools = [dict(t) for t in tools]
            cached_tools[-1] = {**cached_tools[-1], "cache_control": {"type": "ephemeral"}}
            anthropic_kwargs["tools"] = cached_tools
            anthropic_kwargs["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        response = await asyncio.to_thread(client.messages.create, **anthropic_kwargs)
        text_blocks: list[str] = []
        tool_calls: list[dict] = []
        for block in response.content:
            if block.type == "text":
                text_blocks.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({"id": block.id, "name": block.name, "input": block.input})
        usage_obj = getattr(response, "usage", None)
        usage = {
            "input_tokens": getattr(usage_obj, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage_obj, "output_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(usage_obj, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(usage_obj, "cache_read_input_tokens", 0) or 0,
        }
        return {
            "content": "\n\n".join(text_blocks),
            "tool_calls": tool_calls,
            "usage": usage,
            "stop_reason": getattr(response, "stop_reason", None),
            "backend_name": "claude",
            "model_name": (model_profile.resolved_model_id if model_profile and model_profile.provider == "anthropic" else self._model),
            "model_profile": (model_profile.id if model_profile else "anthropic:default"),
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

    def _profile(self, model_profile: str | ModelProfile | None) -> ModelProfile | None:
        if isinstance(model_profile, str):
            model_profile = resolve_model_profile(self.provider_name, model_profile)
        if model_profile and model_profile.provider == self.provider_name:
            return model_profile
        return None

    def _resolved_model_name(self, model_profile: str | ModelProfile | None) -> str:
        profile = self._profile(model_profile)
        if profile:
            return profile.resolved_model_id
        return os.getenv(self._model_env, self.model_name())

    def _tool_specs(self, tools: list[dict] | None) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for tool in (tools or [])
        ]

    def _responses_tool_specs(self, tools: list[dict] | None) -> list[dict]:
        return [
            {
                "type": "function",
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            }
            for tool in (tools or [])
        ]

    def _decode_tool_arguments(self, raw: object) -> dict:
        if isinstance(raw, dict):
            return raw
        try:
            decoded = json.loads(str(raw or "{}"))
            return decoded if isinstance(decoded, dict) else {}
        except Exception:
            return {}

    def _parse_responses_output(self, data: dict) -> tuple[str, list[dict], str | None]:
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        stop_reason = data.get("status")

        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "message":
                for part in item.get("content") or []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") in {"output_text", "text"}:
                        value = part.get("text") or part.get("content")
                        if isinstance(value, str):
                            text_parts.append(value)
            elif item_type in {"output_text", "text"}:
                value = item.get("text") or item.get("content")
                if isinstance(value, str):
                    text_parts.append(value)
            elif item_type == "function_call":
                tool_calls.append({
                    "id": item.get("call_id") or item.get("id") or str(uuid.uuid4()),
                    "name": item.get("name"),
                    "input": self._decode_tool_arguments(item.get("arguments")),
                })

        # Some Responses API SDK/proxy shims expose the same content under
        # output_text. Keep this tolerant so tests and alternate gateways work.
        if not text_parts and isinstance(data.get("output_text"), str):
            text_parts.append(str(data["output_text"]))

        return "\n\n".join(part for part in text_parts if part).strip(), tool_calls, str(stop_reason) if stop_reason else None

    async def _complete_responses_api(
        self,
        messages,
        *,
        system=None,
        tools=None,
        max_tokens=4096,
        temperature=0.0,
        headers=None,
        request_timeout=None,
        model_profile: ModelProfile,
    ):
        payload = {
            "model": model_profile.resolved_model_id,
            "input": _normalize_openai_messages(messages),
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            payload["instructions"] = system
        if tools:
            payload["tools"] = self._responses_tool_specs(tools)
            payload["tool_choice"] = "auto"
        if model_profile.supports_reasoning:
            payload["reasoning"] = {"effort": model_profile.reasoning_effort or "medium"}

        timeout = request_timeout or 120.0
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{self._base_url}/responses", json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            raise InferenceError(f"{self.backend_label} request timed out after {timeout}s — try a shorter query or fewer tools")
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500] if exc.response else ""
            raise InferenceError(f"{self.backend_label} HTTP {exc.response.status_code}: {body}")
        except httpx.HTTPError as exc:
            raise InferenceError(f"{self.backend_label} connection error: {exc}")

        content_text, tool_calls, stop_reason = self._parse_responses_output(data)
        if not content_text and not tool_calls:
            raise InferenceError(f"{self.backend_label} returned an empty completion")
        usage = data.get("usage") or {}
        return {
            "content": content_text,
            "tool_calls": tool_calls,
            "usage": {
                "input_tokens": int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
                "output_tokens": int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
            },
            "stop_reason": stop_reason,
            "backend_name": self.backend_label,
            "model_name": model_profile.resolved_model_id,
            "model_profile": model_profile.id,
        }

    async def complete(self, messages, *, system=None, tools=None, max_tokens=4096, temperature=0.0, api_key=None, provider_api_keys=None, request_timeout=None, model_profile=None):
        key = (
            api_key
            or (provider_api_keys or {}).get(self.provider_name)
            or os.getenv(self._key_env, "")
        )
        if not key and self._key_env != "LOCAL_MODEL_API_KEY":
            raise InferenceError(f"{self.backend_label} API key is not configured")

        profile = self._profile(model_profile)

        payload_messages = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(_normalize_openai_messages(messages))

        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"

        if profile and profile.endpoint == "responses":
            return await self._complete_responses_api(
                messages,
                system=system,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                headers=headers,
                request_timeout=request_timeout,
                model_profile=profile,
            )

        payload = {
            "model": self._resolved_model_name(profile),
            "messages": payload_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if profile and profile.extra_payload:
            payload.update(profile.extra_payload)
        if tools:
            payload["tools"] = self._tool_specs(tools)
            payload["tool_choice"] = "auto"
            # Disable in-turn parallel tool calls so the per-turn failure
            # circuit breaker can actually react before a model fans out many
            # duplicate calls.
            payload["parallel_tool_calls"] = False

        timeout = request_timeout or 120.0
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{self._base_url}/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException:
            raise InferenceError(f"{self.backend_label} request timed out after {timeout}s — try a shorter query or fewer tools")
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500] if exc.response else ""
            raise InferenceError(f"{self.backend_label} HTTP {exc.response.status_code}: {body}")
        except httpx.HTTPError as exc:
            raise InferenceError(f"{self.backend_label} connection error: {exc}")

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
        # PART Z C6: DeepSeek thinking-mode response carries
        # `reasoning_content` alongside `content`. Surface it so the
        # agent loop can stash it on the assistant turn and send it
        # back next round (DeepSeek 400s otherwise).
        reasoning_content = message.get("reasoning_content")
        if not isinstance(reasoning_content, str):
            reasoning_content = None
        return {
            "content": content_text,
            "tool_calls": tool_calls,
            "reasoning_content": reasoning_content,
            "usage": {
                "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "output_tokens": int(usage.get("completion_tokens", 0) or 0),
            },
            "stop_reason": choice.get("finish_reason"),
            "backend_name": self.backend_label,
            "model_name": str(payload["model"]),
            "model_profile": profile.id if profile else None,
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

    async def complete(self, messages, *, system=None, tools=None, max_tokens=4096, temperature=0.0, api_key=None, provider_api_keys=None, request_timeout=None, model_profile=None):
        if not os.getenv("LOCAL_MODEL_ENABLED", ""):
            raise InferenceError("Local model backend is not enabled. Set LOCAL_MODEL_ENABLED=1 and provide an OpenAI-compatible server.")
        return await super().complete(messages, system=system, tools=tools, max_tokens=max_tokens, temperature=temperature, api_key=api_key, provider_api_keys=provider_api_keys, request_timeout=request_timeout, model_profile=model_profile)

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

    def _provider_for_backend(self, backend_name: str) -> str:
        if backend_name == "claude":
            return "anthropic"
        return backend_name

    async def route(self, agent_name: str, messages: list[dict], *, system: str | None = None, tools: list[dict] | None = None, api_key: str | None = None, provider_api_keys: dict[str, str] | None = None, backend_timeout: float = 60.0, preferred_backend: str | None = None, model_profile: str | ModelProfile | None = None, **kwargs) -> dict:
        primary = preferred_backend or self.agent_routing.get(agent_name, "claude")
        attempted = [primary] + [item for item in self.fallback_chain if item != primary]
        attempted_errors: list[tuple[str, Exception]] = []
        attempted_configured = 0
        primary_provider = self._provider_for_backend(primary)
        primary_profile = resolve_model_profile(primary_provider, model_profile)
        for backend_name in attempted:
            backend = self.backends.get(backend_name)
            if backend is None:
                continue
            if not self._backend_is_available(backend_name, provider_api_keys):
                logger.info("Skipping unavailable inference backend %s for %s", backend_name, agent_name)
                continue
            attempted_configured += 1
            backend_provider = self._provider_for_backend(backend_name)
            backend_profile = (
                resolve_model_profile(backend_provider, model_profile)
                if backend_name == primary
                else resolve_model_profile(backend_provider, None)
            )
            fallback_from = primary_profile.id if backend_name != primary else None
            started = time.perf_counter()
            try:
                result = await asyncio.wait_for(
                    backend.complete(
                        messages,
                        system=system,
                        tools=tools,
                        api_key=api_key,
                        provider_api_keys=provider_api_keys,
                        request_timeout=backend_timeout,
                        model_profile=backend_profile,
                        **kwargs,
                    ),
                    timeout=backend_timeout,
                )
                if fallback_from:
                    result["fallback_from"] = fallback_from
                latency_ms = int((time.perf_counter() - started) * 1000)
                try:
                    await self.log_inference(
                        agent_name,
                        backend_name,
                        result.get("usage", {}),
                        success=True,
                        latency_ms=latency_ms,
                        model_name=result.get("model_name") or backend_profile.resolved_model_id,
                        model_profile=result.get("model_profile") or backend_profile.id,
                        fallback_from=fallback_from,
                    )
                except Exception:
                    logger.debug("Failed to log inference (non-fatal)")
                return result
            except Exception as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                attempted_errors.append((backend_name, exc))
                try:
                    await self.log_inference(
                        agent_name,
                        backend_name,
                        {},
                        success=False,
                        latency_ms=latency_ms,
                        error=str(exc),
                        model_name=backend_profile.resolved_model_id,
                        model_profile=backend_profile.id,
                        fallback_from=fallback_from,
                    )
                except Exception:
                    logger.debug("Failed to log inference error (non-fatal)")
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

    async def log_inference(
        self,
        agent: str,
        backend: str,
        usage: dict,
        success: bool,
        latency_ms: int,
        error: str | None = None,
        *,
        model_name: str | None = None,
        model_profile: str | None = None,
        fallback_from: str | None = None,
    ):
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
                    model_name=model_name,
                    model_profile=model_profile,
                    fallback_from=fallback_from,
                )
            )
            await db.commit()


inference_router = InferenceRouter()
