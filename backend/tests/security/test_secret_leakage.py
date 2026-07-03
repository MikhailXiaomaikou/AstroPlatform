"""Security L10 — secrets (API keys) supplied to the chat endpoint must
not leak back out via responses, server-rendered prompts, or logs.

The contract: BYOK API keys arrive on the chat path inside
``context.api_key`` and must be stripped before the request body is
echoed back, before the system prompt is rendered to the LLM, and
before any log line includes the request payload.

This is a structural test (exercises ``_safe_context`` — the helper
chat.py actually uses to strip key material out of the request context —
plus a request-roundtrip sniff against the chat endpoint to confirm a
key-looking string in ``context.api_key`` does not appear in the
response body verbatim).
"""
from __future__ import annotations

LOOKS_LIKE_AN_API_KEY = "sk-ant-FAKE0123456789-DO-NOT-LEAK-ABCDEFG"


def test_api_key_strip_helper_removes_keylike_values():
    """``_safe_context`` must strip every key-material field out of a
    chat-request context while keeping the rest. This test used to hunt
    for a ``_strip_user_api_key`` helper that never existed and skip —
    it now pins the real guard, so if ``_safe_context`` is renamed or
    deleted this FAILS (ImportError) instead of silently skipping."""
    from app.api.chat import _safe_context

    context = {
        "api_key": LOOKS_LIKE_AN_API_KEY,
        "api_keys": {"anthropic": LOOKS_LIKE_AN_API_KEY},
        "api_provider": "anthropic",
        "other": "keep",
    }
    cleaned = _safe_context(context)
    serialized = repr(cleaned)
    assert LOOKS_LIKE_AN_API_KEY not in serialized, (
        f"_safe_context leaked the key into the cleaned payload: {serialized!r}"
    )
    assert "api_key" not in cleaned
    assert "api_keys" not in cleaned
    assert "api_provider" not in cleaned
    assert cleaned["other"] == "keep"


async def test_chat_response_does_not_echo_api_key(app_client, test_user):
    """End-to-end: send a chat request that carries a fake API key in
    ``context.api_key`` and assert the key text never appears in the
    response body. Uses an intentionally malformed body so the chat
    endpoint either rejects or routes through the validation layer
    without invoking the LLM — we are testing the request-handling
    boundary, not LLM output."""
    _user, token = test_user
    body = {
        "messages": [{"role": "user", "content": "say hi"}],
        "context": {"api_key": LOOKS_LIKE_AN_API_KEY},
        # Deliberately leave out fields the chat endpoint requires so
        # we hit the validation layer rather than the actual LLM path.
    }
    resp = await app_client.post(
        "/api/chat",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    # Whatever status comes back (400 / 422 / 401), the key must not
    # appear in the response text.
    assert LOOKS_LIKE_AN_API_KEY not in resp.text, (
        f"chat endpoint leaked the api_key in its response (status {resp.status_code}). "
        f"body[:300]={resp.text[:300]!r}"
    )
