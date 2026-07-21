"""Exact and streamed request-body limits for Foundry internal callbacks."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

import pytest

from app.middleware.request_body_limit import (
    DEFAULT_MAX_REQUEST_BODY,
    FOUNDRY_DEMO_REPORT_MAX_REQUEST_BODY,
    FOUNDRY_REGISTRY_IMPORT_MAX_REQUEST_BODY,
    RESEARCH_EVIDENCE_PACK_VERIFY_MAX_REQUEST_BODY,
    RequestBodyLimitMiddleware,
    request_body_limit_for_path,
)
from app.services.evidence_pack_v2 import EVIDENCE_PACK_V2_MAX_ARCHIVE_BYTES


DEMO_PATH = f"/api/internal/foundry/validation-runs/{uuid.uuid4()}/demo-report"
REGISTRY_IMPORT_PATH = "/api/internal/foundry/registry-releases/import"
RESEARCH_EVIDENCE_VERIFY_PATH = "/api/research/evidence-packs/verify"
PUBLIC_EVIDENCE_VERIFY_PATH = "/api/public/evidence-packs/verify"


async def _invoke_streamed(
    path: str,
    chunks: Iterable[bytes],
    *,
    content_length: int | None = None,
) -> tuple[int, bool, int]:
    downstream_called = False
    downstream_bytes = 0

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        nonlocal downstream_called, downstream_bytes
        downstream_called = True
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            downstream_bytes += len(message.get("body") or b"")
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(downstream)
    body_chunks = list(chunks)
    incoming = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(body_chunks) - 1,
        }
        for index, chunk in enumerate(body_chunks)
    ]
    if not incoming:
        incoming.append({"type": "http.request", "body": b"", "more_body": False})

    async def receive() -> dict[str, Any]:
        if incoming:
            return incoming.pop(0)
        return {"type": "http.disconnect"}

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = [(b"content-type", b"application/json")]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("test", 443),
    }
    await middleware(scope, receive, send)
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    return status, downstream_called, downstream_bytes


def test_only_exact_foundry_callback_paths_receive_larger_caps() -> None:
    assert request_body_limit_for_path(DEMO_PATH) == FOUNDRY_DEMO_REPORT_MAX_REQUEST_BODY
    assert (
        request_body_limit_for_path(REGISTRY_IMPORT_PATH)
        == FOUNDRY_REGISTRY_IMPORT_MAX_REQUEST_BODY
    )
    assert (
        request_body_limit_for_path("/api/internal/foundry/other-callback")
        == DEFAULT_MAX_REQUEST_BODY
    )
    assert (
        request_body_limit_for_path(DEMO_PATH + "/extra")
        == DEFAULT_MAX_REQUEST_BODY
    )


def test_research_json_routes_keep_default_limit_except_exact_pack_verifier() -> None:
    assert (
        request_body_limit_for_path("/api/research/capability-requests")
        == DEFAULT_MAX_REQUEST_BODY
    )
    assert (
        request_body_limit_for_path("/api/research/workspaces")
        == DEFAULT_MAX_REQUEST_BODY
    )
    assert (
        request_body_limit_for_path(RESEARCH_EVIDENCE_VERIFY_PATH)
        == RESEARCH_EVIDENCE_PACK_VERIFY_MAX_REQUEST_BODY
    )
    assert (
        request_body_limit_for_path(PUBLIC_EVIDENCE_VERIFY_PATH)
        == EVIDENCE_PACK_V2_MAX_ARCHIVE_BYTES
    )


async def test_chunked_foundry_user_json_is_rejected_at_default_limit() -> None:
    status, called, observed = await _invoke_streamed(
        "/api/research/claim-audits/00000000-0000-0000-0000-000000000000/capability-requests",
        [b"x" * DEFAULT_MAX_REQUEST_BODY, b"x"],
    )
    assert status == 413
    assert called is False
    assert observed == 0


@pytest.mark.parametrize(
    ("path", "limit"),
    [
        (DEMO_PATH, FOUNDRY_DEMO_REPORT_MAX_REQUEST_BODY),
        (REGISTRY_IMPORT_PATH, FOUNDRY_REGISTRY_IMPORT_MAX_REQUEST_BODY),
    ],
)
async def test_chunked_body_without_content_length_is_counted_and_replayed(
    path: str,
    limit: int,
) -> None:
    chunks = [b"a" * (limit // 2), b"b" * (limit - limit // 2)]
    status, called, observed = await _invoke_streamed(path, chunks)
    assert status == 204
    assert called is True
    assert observed == limit


@pytest.mark.parametrize(
    ("path", "limit"),
    [
        (DEMO_PATH, FOUNDRY_DEMO_REPORT_MAX_REQUEST_BODY),
        (REGISTRY_IMPORT_PATH, FOUNDRY_REGISTRY_IMPORT_MAX_REQUEST_BODY),
    ],
)
async def test_chunked_body_without_content_length_fails_closed_above_exact_cap(
    path: str,
    limit: int,
) -> None:
    chunks = [b"a" * limit, b"x"]
    status, called, observed = await _invoke_streamed(path, chunks)
    assert status == 413
    assert called is False
    assert observed == 0


async def test_declared_oversize_is_rejected_before_body_is_dispatched() -> None:
    status, called, observed = await _invoke_streamed(
        DEMO_PATH,
        [b"{}"],
        content_length=FOUNDRY_DEMO_REPORT_MAX_REQUEST_BODY + 1,
    )
    assert status == 413
    assert called is False
    assert observed == 0


async def test_other_internal_callbacks_keep_default_streaming_limit() -> None:
    status, called, observed = await _invoke_streamed(
        "/api/internal/foundry/draft-runs/00000000-0000-0000-0000-000000000000/result",
        [b"x" * DEFAULT_MAX_REQUEST_BODY, b"x"],
    )
    assert status == 413
    assert called is False
    assert observed == 0


async def test_replayed_body_then_uses_real_receive_channel_for_disconnect() -> None:
    """Streaming apps may await disconnect after consuming the request body."""

    observed_types: list[str] = []

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope
        observed_types.append((await receive())["type"])
        observed_types.append((await receive())["type"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    incoming = [
        {"type": "http.request", "body": b"{}", "more_body": False},
        {"type": "http.disconnect"},
    ]

    async def receive() -> dict[str, Any]:
        return incoming.pop(0)

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/chat/stream",
        "headers": [(b"content-type", b"application/json")],
    }
    await RequestBodyLimitMiddleware(downstream)(scope, receive, send)

    assert observed_types == ["http.request", "http.disconnect"]
    assert sent[0]["status"] == 204
