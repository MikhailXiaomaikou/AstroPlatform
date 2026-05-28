"""Security L10 — secrets (API keys) supplied to the chat endpoint must
not leak back out via responses, server-rendered prompts, or logs.

The contract: BYOK API keys arrive on the chat path inside
``context.api_key`` and must be stripped before the request body is
echoed back, before the system prompt is rendered to the LLM, and
before any log line includes the request payload.

This is a structural test (uses the auth_strip_api_key helper directly
where one exists, plus a request-roundtrip sniff against the chat
endpoint to confirm a key-looking string in ``context.api_key`` does
not appear in the response body verbatim).
"""
from __future__ import annotations

import pytest

LOOKS_LIKE_AN_API_KEY = "sk-ant-FAKE0123456789-DO-NOT-LEAK-ABCDEFG"


def _strip_api_key_helper():
    """Locate the centralized 'strip api_key' helper. The codebase has
    historically kept this in chat.py / auth.py; the test resolves it
    dynamically so it doesn't break if the helper moves."""
    try:
        from app.api.chat import _strip_user_api_key  # type: ignore
        return _strip_user_api_key
    except Exception:
        pass
    try:
        from app.auth import strip_api_key  # type: ignore
        return strip_api_key
    except Exception:
        pass
    return None


def test_api_key_strip_helper_removes_keylike_values():
    """If the helper exists, it must strip a key-looking value out of a
    chat-request-shaped dict. If the helper is missing, the test SKIPS
    so we don't pin its name, but does so loudly so reviewers notice."""
    helper = _strip_api_key_helper()
    if helper is None:
        pytest.skip(
            "No _strip_user_api_key / strip_api_key helper exposed; "
            "the leak guard relies on inline stripping inside chat.py."
        )
    payload = {"context": {"api_key": LOOKS_LIKE_AN_API_KEY, "other": "keep"}}
    cleaned = helper(payload)
    serialized = repr(cleaned)
    assert LOOKS_LIKE_AN_API_KEY not in serialized, (
        f"strip helper leaked the key into the cleaned payload: {serialized!r}"
    )


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
