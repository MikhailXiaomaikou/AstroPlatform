"""User-defined tool macros built from existing Standard Astro tools.

This module intentionally does **not** execute arbitrary user-supplied backend
code.  A user tool is a small, saved macro: validate user arguments, render
literal ``{{param}}`` placeholders into a sequence of existing platform tool
calls, and execute those calls through the normal ``ai_tools.execute_tool``
path.  That keeps provenance, telemetry, sandboxing, connector gates, and claim
validation on the same rails as built-in tools.
"""

from __future__ import annotations

import copy
import re
import time
from typing import Any

from app.services._kv_store import JsonKvStore

_STORE = JsonKvStore("user_tool")
_TTL_SECONDS = 60 * 60 * 24 * 365 * 5
_TOOL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,48}$")
_PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")

# First version: safe non-recursive macros around already-audited platform
# tools.  Keep run_python out of v1; users can already run it explicitly, but
# hiding it inside a macro makes audits much harder.
DISALLOWED_STEP_TOOLS = frozenset({
    "run_python",
    "run_user_tool",
    "list_user_tools",
})


class UserToolError(ValueError):
    """Raised when a user tool definition or invocation is invalid."""


def owner_scope(user_id: str | None, chat_session_id: str | None = None) -> str:
    if user_id:
        return f"user:{user_id}"
    if chat_session_id:
        return f"session:{chat_session_id}"
    return "anonymous"


def _store_key(scope: str, tool_id: str) -> str:
    return f"{scope}:{tool_id}"


def _validate_tool_id(tool_id: str) -> str:
    value = str(tool_id or "").strip().lower()
    if not _TOOL_ID_RE.match(value):
        raise UserToolError("tool_id must match ^[a-z][a-z0-9_]{2,48}$")
    return value


def _validate_input_schema(schema: Any) -> dict[str, Any]:
    if schema is None:
        return {"type": "object", "properties": {}, "additionalProperties": False}
    if not isinstance(schema, dict):
        raise UserToolError("input_schema must be a JSON object")
    if schema.get("type") != "object":
        raise UserToolError("input_schema.type must be 'object'")
    props = schema.get("properties", {})
    if not isinstance(props, dict):
        raise UserToolError("input_schema.properties must be an object")
    required = schema.get("required", [])
    if required is not None and not isinstance(required, list):
        raise UserToolError("input_schema.required must be a list")
    cleaned = copy.deepcopy(schema)
    cleaned.setdefault("additionalProperties", False)
    return cleaned


def _known_platform_tool_names() -> set[str] | None:
    try:
        from app.services.ai_tools import TOOLS
    except Exception:
        return None
    return {str(tool.get("name")) for tool in TOOLS if isinstance(tool, dict) and tool.get("name")}


def _validate_steps(steps: Any) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not steps:
        raise UserToolError("steps must be a non-empty list")
    if len(steps) > 8:
        raise UserToolError("a user tool can contain at most 8 steps")

    known_tools = _known_platform_tool_names()
    cleaned: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise UserToolError(f"step {idx} must be an object")
        tool_name = str(step.get("tool_name") or "").strip()
        if not tool_name:
            raise UserToolError(f"step {idx} is missing tool_name")
        if tool_name in DISALLOWED_STEP_TOOLS:
            raise UserToolError(f"step {idx} cannot call {tool_name}")
        if known_tools is not None and tool_name not in known_tools:
            raise UserToolError(f"step {idx} references unknown platform tool: {tool_name}")
        template = step.get("input", {})
        if not isinstance(template, dict):
            raise UserToolError(f"step {idx}.input must be an object")
        cleaned.append({
            "tool_name": tool_name,
            "input": copy.deepcopy(template),
            "continue_on_error": bool(step.get("continue_on_error", False)),
        })
    return cleaned


def _validate_arguments(schema: dict[str, Any], args: dict[str, Any]) -> None:
    if not isinstance(args, dict):
        raise UserToolError("arguments must be an object")
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    for name in required:
        if name not in args:
            raise UserToolError(f"missing required argument: {name}")
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(args) - set(props))
        if unknown:
            raise UserToolError(f"unknown argument(s): {', '.join(unknown)}")


def _render_template(value: Any, args: dict[str, Any]) -> Any:
    if isinstance(value, str):
        exact = _PLACEHOLDER_RE.fullmatch(value)
        if exact:
            key = exact.group(1)
            if key not in args:
                raise UserToolError(f"template references missing argument: {key}")
            return copy.deepcopy(args[key])

        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in args:
                raise UserToolError(f"template references missing argument: {key}")
            return str(args[key])

        return _PLACEHOLDER_RE.sub(repl, value)
    if isinstance(value, list):
        return [_render_template(item, args) for item in value]
    if isinstance(value, dict):
        return {str(k): _render_template(v, args) for k, v in value.items()}
    return copy.deepcopy(value)


def create_user_tool(
    *,
    scope: str,
    tool_id: str,
    display_name: str,
    description: str,
    input_schema: dict[str, Any] | None,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    tid = _validate_tool_id(tool_id)
    desc = str(description or "").strip()
    if len(desc) < 12:
        raise UserToolError("description must be at least 12 characters")
    payload = {
        "tool_id": tid,
        "display_name": str(display_name or tid).strip()[:80],
        "description": desc[:1200],
        "input_schema": _validate_input_schema(input_schema),
        "steps": _validate_steps(steps),
        "implementation_type": "tool_macro",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "enabled": True,
    }
    _STORE.set(_store_key(scope, tid), payload, ttl=_TTL_SECONDS)
    return payload


def list_user_tools(scope: str) -> list[dict[str, Any]]:
    prefix = f"{scope}:"
    tools: list[dict[str, Any]] = []
    for key in _STORE.scan_keys():
        if not key.startswith(prefix):
            continue
        item = _STORE.get(key)
        if isinstance(item, dict):
            tools.append(item)
    return sorted(tools, key=lambda item: str(item.get("tool_id", "")))


def get_user_tool(scope: str, tool_id: str) -> dict[str, Any] | None:
    tid = _validate_tool_id(tool_id)
    item = _STORE.get(_store_key(scope, tid))
    return item if isinstance(item, dict) else None


def delete_user_tool(scope: str, tool_id: str) -> bool:
    tid = _validate_tool_id(tool_id)
    existed = get_user_tool(scope, tid) is not None
    _STORE.delete(_store_key(scope, tid))
    return existed


async def execute_user_tool(
    *,
    scope: str,
    tool_id: str,
    arguments: dict[str, Any],
    user_id: str | None = None,
    chat_session_id: str | None = None,
    python_session_id: str = "default",
) -> dict[str, Any]:
    definition = get_user_tool(scope, tool_id)
    if not definition or not definition.get("enabled", True):
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "error_class": "user_tool_not_found",
            "error": f"User tool {tool_id!r} is not available in this scope.",
        }

    schema = definition.get("input_schema") if isinstance(definition.get("input_schema"), dict) else {}
    try:
        _validate_arguments(schema, arguments)
    except UserToolError as exc:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "error_class": "invalid_user_tool_arguments",
            "error": str(exc),
        }

    from app.services.ai_tools import execute_tool  # lazy import avoids cycle

    step_results: list[dict[str, Any]] = []
    status = "COMPLETED"
    for idx, step in enumerate(definition.get("steps") or [], start=1):
        tool_name = str(step.get("tool_name") or "")
        rendered_input: dict[str, Any] = {}
        try:
            rendered_input = _render_template(step.get("input") or {}, arguments)
            result = await execute_tool(
                tool_name,
                rendered_input,
                python_session_id=python_session_id,
                user_id=user_id,
                chat_session_id=chat_session_id,
            )
        except Exception as exc:  # defensive: nested tool errors should be shaped
            result = {
                "success": False,
                "__tool_status__": "FAILED",
                "error_class": exc.__class__.__name__,
                "error": str(exc),
            }
        step_results.append({
            "step": idx,
            "tool_name": tool_name,
            "tool_input": rendered_input,
            "tool_result": result,
        })
        failed = isinstance(result, dict) and (
            result.get("success") is False
            or str(result.get("__tool_status__") or "").upper() == "FAILED"
            or bool(result.get("error"))
        )
        if failed:
            status = "PARTIAL" if step_results[:-1] else "FAILED"
            if not step.get("continue_on_error", False):
                break

    return {
        "success": status == "COMPLETED",
        "__tool_status__": status,
        "tool_id": definition.get("tool_id"),
        "display_name": definition.get("display_name"),
        "implementation_type": "tool_macro",
        "steps_run": len(step_results),
        "step_results": step_results,
        "__message_to_model__": (
            "This is a user-defined tool macro built only from existing Standard "
            "Astro tools. Cite only nested tool results that are themselves citable."
        ),
    }
