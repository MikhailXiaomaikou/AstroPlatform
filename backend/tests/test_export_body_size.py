"""PART AE — chat-export endpoints exempted from the 1 MB body-size middleware.

Bug reproducer:

  Frontend POST /api/export/report/from-chat with a long-session payload
  (transcript + actions + embedded figure metadata) → middleware
  intercepts at 1 MB and returns 413 Payload Too Large → axios in the
  frontend translates this to a generic "Network error" toast.

The 1 MB cap is appropriate for /api/chat / /api/adql / etc. (those
should never carry MB of input). But the chat-export family
(markdown / notebook / latex / bibtex / paper / research / workspace
bulk uploads / FITS uploads) legitimately accept multi-MB bodies.

Locks the contract: export endpoints accept >= 2 MB bodies, chat
endpoints still 413.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client() -> TestClient:
    import app.main as main_mod
    return TestClient(main_mod.app)


def test_export_chat_markdown_accepts_2mb_body() -> None:
    """The user-visible bug: long chat → export 413 → 'network error'."""
    client = _client()
    big = "x" * (2 * 1024 * 1024)  # 2 MB single-message transcript
    resp = client.post(
        "/api/export/report/from-chat",
        json={"messages": [{"role": "user", "content": big}], "title": "test"},
    )
    assert resp.status_code == 200, (
        f"export must accept 2 MB body, got {resp.status_code}: {resp.text[:200]}"
    )
    assert resp.headers.get("content-type", "").startswith("text/markdown")


def test_export_chat_notebook_accepts_2mb_body() -> None:
    client = _client()
    big = "x" * (2 * 1024 * 1024)
    resp = client.post(
        "/api/export/notebook/from-chat",
        json={"messages": [{"role": "user", "content": big}], "title": "test"},
    )
    assert resp.status_code == 200


def test_chat_message_still_413s_above_1mb() -> None:
    """Sanity: the size gate still works for non-export endpoints —
    we don't want this fix to regress the protection on the chat /
    ADQL / etc. payload paths."""
    client = _client()
    big = "x" * (2 * 1024 * 1024)
    resp = client.post(
        "/api/chat/message",
        json={"messages": [{"role": "user", "content": big}]},
    )
    assert resp.status_code == 413


def test_export_workflow_python_accepts_large_body() -> None:
    """The Python workflow export bundles tool_call results which can be
    several MB on a long session; must not 413 either."""
    client = _client()
    big_input = "y" * (1500 * 1024)  # 1.5 MB
    resp = client.post(
        "/api/export/workflow/python",
        json={
            "tool_calls": [
                {"tool": "run_python", "input": {"code": big_input}},
            ],
            "title": "test",
        },
    )
    # Endpoint must reach its handler — could 200 / 422 / etc. but NOT 413
    assert resp.status_code != 413, (
        f"workflow export must not 413 on 1.5 MB, got {resp.status_code}: {resp.text[:200]}"
    )
