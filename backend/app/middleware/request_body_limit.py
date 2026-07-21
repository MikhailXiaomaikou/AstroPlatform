"""Fail-closed request-body limits, including streamed/chunked bodies.

The ASGI server is not required to provide ``Content-Length``.  Checking that
header alone therefore leaves the limit bypassable with chunked transfer
encoding.  This middleware buffers only bounded request families, counts the
actual ASGI body bytes before dispatch, and replays the exact messages to the
application after the size gate passes.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import JSONResponse

from app.services.evidence_pack_v2 import EVIDENCE_PACK_V2_MAX_ARCHIVE_BYTES


DEFAULT_MAX_REQUEST_BODY = 1_048_576  # 1 MiB
FOUNDRY_DEMO_REPORT_MAX_REQUEST_BODY = 4 * 1_048_576
FOUNDRY_REGISTRY_IMPORT_MAX_REQUEST_BODY = 10 * 1_048_576
RESEARCH_EVIDENCE_PACK_VERIFY_MAX_REQUEST_BODY = 32 * 1_048_576

_FOUNDRY_DEMO_REPORT_PATH_RE = re.compile(
    r"^/api/internal/foundry/validation-runs/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/demo-report$"
)
_FOUNDRY_REGISTRY_IMPORT_PATH = "/api/internal/foundry/registry-releases/import"
_RESEARCH_EVIDENCE_PACK_VERIFY_PATH = "/api/research/evidence-packs/verify"
_PUBLIC_EVIDENCE_PACK_VERIFY_PATH = "/api/public/evidence-packs/verify"

# These existing upload/export routes deliberately retain their own endpoint
# limits.  Do not add ``/api/internal/foundry`` here: the two Foundry callbacks
# above have separate, exact caps and every other internal callback stays at
# the conservative 1 MiB default.
_LARGE_BODY_PREFIXES = (
    "/api/data/fits/upload",
    "/api/data/upload",
    "/api/export/",
    "/api/integration/jupyter/export",
    "/api/workspace/batch-upload",
    "/api/paper/",
)


def request_body_limit_for_path(path: str) -> int | None:
    """Return the exact byte limit, or ``None`` for endpoint-owned limits."""

    if _FOUNDRY_DEMO_REPORT_PATH_RE.fullmatch(path):
        return FOUNDRY_DEMO_REPORT_MAX_REQUEST_BODY
    if path == _FOUNDRY_REGISTRY_IMPORT_PATH:
        return FOUNDRY_REGISTRY_IMPORT_MAX_REQUEST_BODY
    if path == _RESEARCH_EVIDENCE_PACK_VERIFY_PATH:
        return RESEARCH_EVIDENCE_PACK_VERIFY_MAX_REQUEST_BODY
    if path == _PUBLIC_EVIDENCE_PACK_VERIFY_PATH:
        return EVIDENCE_PACK_V2_MAX_ARCHIVE_BYTES
    if any(path.startswith(prefix) for prefix in _LARGE_BODY_PREFIXES):
        return None
    return DEFAULT_MAX_REQUEST_BODY


class RequestBodyLimitMiddleware:
    """Enforce actual request bytes before a bounded request reaches a route."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    @staticmethod
    async def _reject(scope: dict[str, Any], receive: Any, send: Any, *, detail: str) -> None:
        response = JSONResponse(status_code=413, content={"detail": detail})
        await response(scope, receive, send)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        limit = request_body_limit_for_path(str(scope.get("path") or ""))
        if limit is None:
            await self.app(scope, receive, send)
            return

        content_length_values = [
            value
            for name, value in scope.get("headers", [])
            if bytes(name).lower() == b"content-length"
        ]
        if len(content_length_values) > 1:
            await self._reject(
                scope,
                receive,
                send,
                detail="Ambiguous Content-Length",
            )
            return
        if content_length_values:
            try:
                declared_length = int(bytes(content_length_values[0]).decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                await self._reject(
                    scope,
                    receive,
                    send,
                    detail="Invalid Content-Length",
                )
                return
            if declared_length < 0 or declared_length > limit:
                await self._reject(
                    scope,
                    receive,
                    send,
                    detail="Request body too large",
                )
                return

        buffered: list[dict[str, Any]] = []
        observed_bytes = 0
        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "http.disconnect":
                buffered.append(message)
                break
            if message_type != "http.request":
                buffered.append(message)
                continue
            body = bytes(message.get("body") or b"")
            observed_bytes += len(body)
            if observed_bytes > limit:
                await self._reject(
                    scope,
                    receive,
                    send,
                    detail="Request body too large",
                )
                return
            buffered.append({**message, "body": body})
            if not message.get("more_body", False):
                break

        position = 0

        async def replay_receive() -> dict[str, Any]:
            nonlocal position
            if position < len(buffered):
                message = buffered[position]
                position += 1
                return message
            # Once the buffered request body has been replayed, hand control
            # back to the real ASGI receive channel.  Streaming responses use
            # it to wait for ``http.disconnect``.  Returning another synthetic
            # ``http.request`` here violates Starlette's consumed-body state
            # machine and breaks otherwise valid SSE/chat responses.
            return await receive()

        await self.app(scope, replay_receive, send)


__all__ = [
    "DEFAULT_MAX_REQUEST_BODY",
    "FOUNDRY_DEMO_REPORT_MAX_REQUEST_BODY",
    "FOUNDRY_REGISTRY_IMPORT_MAX_REQUEST_BODY",
    "RESEARCH_EVIDENCE_PACK_VERIFY_MAX_REQUEST_BODY",
    "RequestBodyLimitMiddleware",
    "request_body_limit_for_path",
]
